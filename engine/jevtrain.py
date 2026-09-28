"""判官训练材料流水线（T13）：真机运行 → 收集 → 人审 → 导出可训练版本草案。

为什么需要它（"把攻击记录存档成训练材料"）：
  判官（JEV，Qwen3.5-4B 微调）要变强得吃**真实交换**；但直接拿判官自己的判读当标签，
  等于让它教自己 —— 越训越自信。本模块的立场：

  1. **标签只来自硬信号**：marker 命中 / 业务门禁 / 复测结果（`intent_label`），
     判官读数列在 `judge` 字段里，**只用于分歧分析**（FP/FN 才是最有价值的训练样本）；
  2. **B 留存不可破**：入队与导出两道都过 `is_b_sealed`；已封家族的桶归属用 `lookup_split`
     查表，**绝不重切**（同族样本漂到另一个桶 = 跨切分泄漏）；
  3. **队列 ≠ 已训练**：导出物是"可训练版本草案"（train/holdout + manifest + TRAINING.md），
     训练与评估走既有 bench 流程，成绩零样本/微调后分栏。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
from typing import Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
_DATASET = ROOT / "dataset"
if str(_DATASET) not in sys.path:
    sys.path.insert(0, str(_DATASET))

from engine.learning_queue import (  # noqa: E402
    LearningQueue,
    is_b_sealed,
    lookup_split,
    scrub_v2,
)

DEFAULT_QUEUE = ROOT / "dataset" / "review_queue" / "jevtrain_candidates.jsonl"


class JevTrainError(RuntimeError):
    pass


def _utcnow() -> str:
    from engine.demo_contracts import utcnow_iso
    return utcnow_iso()


def family_of(method: str, path: str) -> str:
    """家族键：方法 + 路径模板（数字/长十六进制段折叠成 *，查询串去掉）。"""
    p = re.sub(r"/\d+", "/*", path or "")
    p = re.sub(r"[0-9a-fA-F]{8,}", "*", p)
    return f"{(method or 'GET').upper()} {p.split('?')[0]}"


def _read_jsonl(p: pathlib.Path) -> List[dict]:
    if not p.is_file():
        return []
    out = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def truth_fields(flow: dict) -> dict:
    """把**三件事**分开，别再混为一谈（2026-09-28 独立审阅 P1-1）：

      * `traffic_intent`  —— 这段流量**是不是攻击**。这是判官（JEV）的本职任务。
      * `exploit_outcome` —— 这发**有没有打穿**。marker 只提供"成功"的一种证据。
      * `label_review_state` —— 能不能直接进训练集（auto_ok / needs_review）。

    要点：**缺 marker 不能证明正常**，只说明"没拿到成功的证据"。攻击探针即使被挡住，
    它仍然是攻击流量 ⇒ 不再自动判 benign。
    """
    intent = flow.get("intent_label")
    outcome = flow.get("outcome")
    hits = flow.get("markers_hit") or []
    if intent == "attack":
        if hits:
            return {"traffic_intent": "attack", "exploit_outcome": "confirmed",
                    "label_review_state": "auto_ok"}
        if outcome == "blocked":
            return {"traffic_intent": "attack", "exploit_outcome": "blocked",
                    "label_review_state": "needs_review"}
        if flow.get("error") or outcome == "error":
            return {"traffic_intent": "attack", "exploit_outcome": "error",
                    "label_review_state": "needs_review"}
        return {"traffic_intent": "attack", "exploit_outcome": "no_marker",
                "label_review_state": "needs_review"}
    if intent == "benign":
        if hits:
            return {"traffic_intent": "benign", "exploit_outcome": "marker_on_benign",
                    "label_review_state": "needs_review"}
        return {"traffic_intent": "benign", "exploit_outcome": "none",
                "label_review_state": "auto_ok"}
    return {"traffic_intent": "unknown", "exploit_outcome": "unknown",
            "label_review_state": "needs_review"}


def classify_truth(flow: dict) -> Tuple[Optional[str], str]:
    """训练标签 = **流量意图**（不是"有没有打穿"）；拿不准返回 (None, 原因)。

    与 truth_fields 一致的规则：攻击探针恒为 attack（无论是否被打断），良性流量仅在
    无 marker 时为 benign；其余交人审（返回 None 或标注 needs_review）。
    """
    f = truth_fields(flow)
    ti, eo, rs = f["traffic_intent"], f["exploit_outcome"], f["label_review_state"]
    if flow.get("error") or str(flow.get("outcome") or "") == "error":
        # 交换本身不可用 ⇒ 无证据，按"无法确定"处理：不入训练集（fields 里仍记 outcome=error）
        return None, "exchange_errored(样本不可用，不作标签)"
    if ti == "unknown":
        return None, "unknown_intent"
    if ti == "benign" and rs == "needs_review":
        return None, f"benign_with_marker(异常，需人看):{eo}"
    reason = f"{ti}/{eo}" + ("" if rs == "auto_ok" else "|needs_review")
    return ti, reason


def collect_from_run(run_dir: pathlib.Path, queue: LearningQueue,
                     with_response: bool = True, origin: str = "") -> dict:
    """把一轮 run 的 flow.jsonl + judge.jsonl 合成候选样本（脱敏 + 去重 + B 集拒入）。"""
    run_dir = pathlib.Path(run_dir)
    flows = _read_jsonl(run_dir / "flow.jsonl")
    judges = {j.get("flow_id"): j for j in _read_jsonl(run_dir / "judge.jsonl")}
    if not flows:
        raise JevTrainError(f"run 目录没有 flow.jsonl 或为空: {run_dir}")
    added, dup, refused, unlabelled = [], [], [], []
    for fl in flows:
        fid = fl.get("flow_id")
        method = (fl.get("method") or "GET").upper()
        path = fl.get("path") or ""
        body = fl.get("body") or ""
        status = fl.get("status")
        response = (fl.get("response") or "") if with_response else ""
        truth, why = classify_truth(fl)
        if truth is None:
            unlabelled.append({"flow_id": fid, "reason": why})
            continue
        fam = family_of(method, path)
        sealed = is_b_sealed(fam, path)
        if sealed:
            refused.append({"flow_id": fid, "reason": sealed, "family": fam})
            continue
        j = judges.get(fid) or {}
        # 交换级身份：判官看的是"请求+响应"，所以去重键必须含 status 与响应摘要，
        # 否则"同一请求、判官该看到不同响应"的样本会被当重复丢掉（实测踩过）。
        _key = f"{method} {path} {body} | {status} | {scrub_v2(response)[:200]}"
        sample = {
            "input_hash": hashlib.sha256(_key.encode("utf-8")).hexdigest()[:32],
            "run": run_dir.name, "flow_id": fid, "method": method, "path": path,
            "body": scrub_v2(body)[:1000], "status": status,
            "response": scrub_v2(response)[:4000],
            "intent_label": truth,                      # ← 硬信号给的标签
            "input_mode": "exchange" if with_response else "request_only",
            "truth_reason": why, "family": fam,
            "judge": {k: j.get(k) for k in ("verdict", "p_attack", "band", "protocol_id",
                                            "model_version", "latency_ms")},
            "agreement": (j.get("verdict") == truth) if j.get("verdict") else None,
            "pair_role": truth,                       # 配对角色：同一 family 的攻/良互为配对
            "source": "run_collect", "collected_at": _utcnow(),
            # learning_queue._set_state 要求显式 pending（无键会被判"状态单向"）
            "review_state": "pending",
        }
        res = queue.append(sample)
        if res.get("duplicate"):
            dup.append(fid)
        else:
            added.append(res["sample"]["sample_id"])
    return {"run": run_dir.name, "flows": len(flows), "added": len(added),
            "duplicates": len(dup), "refused_b_sealed": refused,
            "unlabelled": unlabelled, "sample_ids": added, "origin": origin}


def review_label(queue: LearningQueue, sample_id: str, traffic_intent: str,
                 reviewer: str, note: str) -> dict:
    """Operator confirms/corrects a label separately from sample approval.

    This is provenance, not OS-level identity verification. A shell owner can
    bypass the environmental operator gate; don't claim a security boundary.
    """
    if traffic_intent not in ("attack", "benign") or not reviewer.strip() or not note.strip():
        raise JevTrainError("explicit label, reviewer and evidence note required")
    rows = queue.load()
    row = next((r for r in rows if r.get("sample_id") == sample_id), None)
    if row is None or row.get("review_state") == "rejected":
        raise JevTrainError("sample missing or rejected")
    before = row.get("intent_label")
    row.setdefault("label_audit", []).append({"before": before, "after": traffic_intent,
        "reviewer": reviewer, "evidence_note": scrub_v2(note), "reviewed_at": _utcnow()})
    row.update(intent_label=traffic_intent, traffic_intent=traffic_intent,
               pair_role=traffic_intent, label_review_state="human_reviewed")
    verdict = (row.get("judge") or {}).get("verdict")
    row["agreement"] = (verdict == traffic_intent) if verdict else None
    if before != traffic_intent:
        # A corrected label invalidates any old sample approval.
        row["review_state"] = "pending"
    tmp = queue.path.with_suffix(".label.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    from engine.demo_contracts import atomic_replace
    atomic_replace(tmp, queue.path)
    return row


def stamp_reviewer(queue: LearningQueue, sample_id: str, reviewer: str,
                   note: str = "") -> dict:
    """在审核前把 reviewer/note/时间盖到样本上（learning_queue.approve 不接收审核人）。

    为什么必须盖：导出产物的 provenance 要能回答"谁批的、何时批的"。
    """
    rows = queue.load()
    hit = next((r for r in rows if r.get("sample_id") == sample_id), None)
    if hit is None:
        raise JevTrainError(f"unknown sample_id: {sample_id}")
    hit["reviewer"] = reviewer
    hit["review_note"] = note
    hit["reviewed_at"] = _utcnow()
    tmp = queue.path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    from engine.demo_contracts import atomic_replace
    atomic_replace(tmp, queue.path)
    return hit


def collect_from_flows_jsonl(path: pathlib.Path, queue: LearningQueue,
                             origin: str = "") -> dict:
    """收集"外部整理好的流语料"（例如盲评考生落盘的 A1-flows.jsonl）。

    这批语料的价值在于：truth 由**独立考生**用硬证据标注（不是判官自述），
    因此里面判官误报/漏报的条目正好是判官最需要的 hard negative / hard positive。
    期望的每行结构（宽松）：
      {"id","class","request":{"method","path","body","status"},
       "response_excerpt":{"value"} | "response","judge":{"verdict","p_attack","band"},
       "truth": "attack"|"benign", "truth_rationale": "..."}
    """
    src = pathlib.Path(path)
    rows = _read_jsonl(src)
    if not rows:
        raise JevTrainError(f"没有可读行: {path}")
    added, dup, refused, unlabelled = [], [], [], []
    resp_root = src.parent / "A1-evidence"           # 响应体常与语料同目录并排存放
    for r in rows:
        # request 既可能是 {"method","path","body","status"}，也可能是原始请求行字符串
        req = r.get("request")
        body = ""
        status = r.get("status")
        if isinstance(req, dict):
            method = (req.get("method") or "GET").upper()
            pth = req.get("path") or ""
            body = req.get("body") or ""
            status = req.get("status", status)
        elif isinstance(req, str):
            parts = req.split()
            method = (parts[0] if parts else "GET").upper()
            pth = parts[1] if len(parts) > 1 else ""
        else:
            method, pth = "GET", ""
        rex = r.get("response_excerpt")
        response = (rex.get("value") if isinstance(rex, dict) else rex) or r.get("response") or ""
        # 响应体走文件时读文件（判官看的是真实响应，这一项不能缺）
        rfile = r.get("response_file")
        if not response and rfile:
            for cand in (resp_root / pathlib.Path(rfile).name, src.parent / rfile):
                if cand.is_file():
                    response = cand.read_text(encoding="utf-8", errors="replace")
                    break
        truth = r.get("truth")
        if truth not in ("attack", "benign"):
            unlabelled.append({"id": r.get("id"), "reason": "truth 缺失或不是 attack/benign"})
            continue
        fam = family_of(method, pth)
        sealed = is_b_sealed(fam, pth)
        if sealed:
            refused.append({"id": r.get("id"), "reason": sealed, "family": fam})
            continue
        j = r.get("judge") or {}
        key = f"{method} {pth} {body} | {status} | {scrub_v2(response)[:200]}"
        sample = {
            "input_hash": hashlib.sha256(key.encode("utf-8")).hexdigest()[:32],
            "run": r.get("run") or pathlib.Path(path).parent.name,
            "flow_id": r.get("id"),
            "class": r.get("class") or r.get("vclass"),
            "method": method, "path": pth, "body": scrub_v2(body)[:1000], "status": status,
            "response": scrub_v2(response)[:4000],
            "intent_label": truth,
            "input_mode": "exchange",
            "truth_reason": (r.get("truth_rationale") or "external_truth")[:200],
            "family": fam,
            "judge": {k: j.get(k) for k in ("verdict", "p_attack", "band", "protocol_id",
                                            "model_version", "latency_ms")},
            "agreement": (j.get("verdict") == truth) if j.get("verdict") else None,
            "matrix_cell": r.get("matrix_cell"),
            "pair_role": truth,                       # 配对角色：同一 family 的攻/良互为配对
            "source": "flows_jsonl", "collected_at": _utcnow(),
            "review_state": "pending",
        }
        res = queue.append(sample)
        if res.get("duplicate"):
            dup.append(r.get("id"))
        else:
            added.append(res["sample"]["sample_id"])
    return {"source_file": str(path), "rows": len(rows), "added": len(added),
            "duplicates": len(dup), "refused_b_sealed": refused,
            "unlabelled": unlabelled, "sample_ids": added, "origin": origin}


def _default_judge(exchange: dict) -> Optional[dict]:
    """调判官服务取读数（advisory；失败返回 None，不冒充有读数）。"""
    import os
    import urllib.request
    url = os.environ.get("AEGIS_JUDGE_URL", "http://127.0.0.1:30002") + "/judge"
    body = json.dumps({k: exchange.get(k, "") for k in
                       ("method", "path", "body", "status", "response")},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 —— 判官不可达如实记 None
        return None


def collect_from_hunt(result_path: pathlib.Path, queue: LearningQueue,
                      judge_fn=None, only_anomalies: bool = False) -> dict:
    """Collect versioned observed exchanges; historical summaries stay quarantined.

    Attack intent and successful exploitation are separate. A model verdict
    never establishes truth. Both judge input and queue use the SAME scrubbed
    exchange, including the original method and request body.
    """
    src = pathlib.Path(result_path)
    doc = json.loads(src.read_text(encoding="utf-8"))
    attempts = doc.get("attempts") or []
    if not attempts:
        raise JevTrainError("hunt result has no attempts")
    judge_fn = judge_fn or _default_judge
    added, dup, refused, skipped = [], [], [], []
    for at in attempts:
        step = str(at.get("step") or "")
        observed = at.get("exchange")
        required = {"method", "path", "body", "status", "response", "error"}
        if (not isinstance(observed, dict) or observed.get("schema_version") != 1
                or not required.issubset(observed)):
            skipped.append({"step": step, "reason": "incomplete_exchange_quarantined"})
            continue
        status = observed["status"]
        if type(status) is not int or status <= 0 or observed["error"]:
            skipped.append({"step": step, "reason": "failed_exchange(no usable evidence)"})
            continue
        if not all(isinstance(observed[k], str) for k in ("method", "path", "body", "response", "error")):
            skipped.append({"step": step, "reason": "invalid_exchange_quarantined"})
            continue
        exch = {"method": observed["method"].upper(),
                "path": scrub_v2(observed["path"]),
                "body": scrub_v2(observed["body"])[:1000], "status": status,
                "response": scrub_v2(observed["response"])[:4000]}
        hits = list(at.get("markers_hit") or [])
        if only_anomalies and not hits and status == 200:
            skipped.append({"step": step, "reason": "anomaly_filter"})
            continue
        control = step.startswith("baseline") or step.startswith("uid_own_control")
        truth = "benign" if control else "attack"
        why = ("legitimate_control:" + step if control else
               "marker_hit:" + ",".join(map(str, hits))[:60] if hits else "attack/no_marker|needs_review")
        fam = family_of(exch["method"], exch["path"])
        sealed = is_b_sealed(fam, exch["path"])
        if sealed:
            refused.append({"step": step, "reason": sealed})
            continue
        j = judge_fn(dict(exch)) or {}
        sample = {**exch,
            "input_hash": hashlib.sha256(json.dumps(exch, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32],
            "run": src.parent.name, "flow_id": step,
            "intent_label": truth, "traffic_intent": truth, "input_mode": "exchange",
            "exchange_schema_version": 1,
            "exploit_outcome": "confirmed" if hits else "none" if control else "no_marker",
            "label_review_state": "auto_ok" if (hits and not control) or (control and not hits) else "needs_review",
            "probe_note": scrub_v2(str(at.get("note") or ""))[:200],
            "truth_reason": why, "family": fam, "pair_role": truth,
            "judge": {k: j.get(k) for k in ("verdict", "p_attack", "band", "protocol_id", "model_version", "latency_ms")},
            "agreement": j.get("verdict") == truth if j.get("verdict") else None,
            "source": "hunt_ladder", "collected_at": _utcnow(), "review_state": "pending"}
        res = queue.append(sample)
        (dup if res.get("duplicate") else added).append(step if res.get("duplicate") else res["sample"]["sample_id"])
    return {"source_file": str(src), "attempts": len(attempts), "added": len(added),
            "duplicates": len(dup), "refused_b_sealed": refused, "skipped": skipped, "sample_ids": added}


def report(queue: LearningQueue) -> dict:
    rows = queue.load()
    by_truth: Dict[str, int] = {}
    agree = disagree = nojudge = 0
    fp, fn = [], []
    for r in rows:
        by_truth[r.get("intent_label")] = by_truth.get(r.get("intent_label"), 0) + 1
        j = (r.get("judge") or {}).get("verdict")
        if j is None:
            nojudge += 1
        elif r.get("agreement"):
            agree += 1
        else:
            disagree += 1
            if j == "attack" and r.get("intent_label") == "benign":
                fp.append(r.get("sample_id"))       # 判官误报
            elif j == "benign" and r.get("intent_label") == "attack":
                fn.append(r.get("sample_id"))       # 判官漏报
    return {
        "total": len(rows),
        "review": {"pending": sum(1 for r in rows if r.get("review_state") == "pending"),
                   "approved": sum(1 for r in rows if r.get("review_state") == "approved"),
                   "rejected": sum(1 for r in rows if r.get("review_state") == "rejected")},
        "by_truth": by_truth,
        "judge_agreement": {"agree": agree, "disagree": disagree, "no_judge": nojudge},
        "false_positives": fp[:20], "false_negatives": fn[:20],
        "note": "队列 ≠ 已训练；只有 approved 才会进入导出",
    }


def pairing_report(queue: LearningQueue) -> dict:
    """按 family 看配对：**只有攻击侧或只有良性侧**的家族会训出偏标签的判官。

    只统计已入队的样本（不自动补样本，也不绕过人审）；它回答的是"还缺什么料"。
    """
    fams: Dict[str, Dict[str, int]] = {}
    for r in queue.load():
        fam = r.get("family") or family_of(r.get("method", "GET"), r.get("path", ""))
        role = r.get("pair_role") or r.get("intent_label") or "unknown"
        fams.setdefault(fam, {"attack": 0, "benign": 0})[role] = \
            fams.setdefault(fam, {"attack": 0, "benign": 0}).get(role, 0) + 1
    both = [f for f, c in fams.items() if c.get("attack") and c.get("benign")]
    only_attack = [f for f, c in fams.items() if c.get("attack") and not c.get("benign")]
    only_benign = [f for f, c in fams.items() if c.get("benign") and not c.get("attack")]
    return {
        "families": len(fams),
        "paired_families": len(both),
        "unpaired_only_attack": only_attack[:15],
        "unpaired_only_benign": only_benign[:15],
        "note": "只有单侧的家族会训出偏标签的判官；补齐配对（同端点、同 family 的另一侧）再导出",
    }


def _split_for(family: str, sample_id: str) -> str:
    """已封家族查表（不得重切）；新家族按 hash 稳定切。"""
    seen = lookup_split(family)
    if seen:
        return seen
    n = int(hashlib.sha256(family.encode()).hexdigest(), 16)
    return "train" if n % 10 < 8 else "holdout"


def export(queue: LearningQueue, version: str, out_root: Optional[pathlib.Path] = None,
           with_response: bool = True) -> dict:
    """把 **approved** 样本导出为可训练版本草案。"""
    import build_judge as BJ
    root = pathlib.Path(out_root or (ROOT / "dataset" / f"judge_{version}"))
    rows = [r for r in queue.load() if r.get("review_state") == "approved"]
    if not rows:
        raise JevTrainError("没有 approved 样本 ⇒ 拒绝导出（未审核不得进训练集）")
    train, holdout, refused = [], [], []
    for r in rows:
        fam = r.get("family") or family_of(r.get("method", "GET"), r.get("path", ""))
        if is_b_sealed(fam, r.get("path", "")):
            refused.append({"sample_id": r.get("sample_id"), "reason": "b_sealed"})
            continue
        split = _split_for(fam, r.get("sample_id"))
        rec = BJ.make_record(
            r.get("method", "GET"), r.get("path", ""), r.get("body", ""), r.get("status"),
            r.get("response", ""), r.get("intent_label"),
            {"source": "jevtrain", "queue_sample_id": r.get("sample_id"),
             "run": r.get("run"), "flow_id": r.get("flow_id"),
             "family": fam, "truth_reason": r.get("truth_reason"),
             "judge_verdict": (r.get("judge") or {}).get("verdict"),
             "collected_at": r.get("collected_at"), "reviewed_at": r.get("reviewed_at"),
             "reviewer": r.get("reviewer"), "review_state": r.get("review_state"),
             "label_review_state": r.get("label_review_state"), "label_audit": r.get("label_audit", []),
             "traffic_intent": r.get("traffic_intent"), "exploit_outcome": r.get("exploit_outcome"),
             "input_mode": r.get("input_mode")},
            with_response=with_response)
        (train if split == "train" else holdout).append(rec)
    root.mkdir(parents=True, exist_ok=True)

    def _write(name: str, recs: List[dict]) -> dict:
        p = root / name
        p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in recs),
                     encoding="utf-8")
        return {"file": name, "rows": len(recs),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}

    files = [_write("train.jsonl", train), _write("holdout.jsonl", holdout)]
    labels = {"attack": 0, "benign": 0}
    for x in train + holdout:
        last = (x.get("messages") or [{}])[-1].get("content", "").strip()
        if last in labels:
            labels[last] += 1
    manifest = {
        "schema": "jevtrain-manifest-v1", "version": version, "created_at": _utcnow(),
        "source_queue": str(queue.path.relative_to(ROOT))
        if str(queue.path).startswith(str(ROOT)) else str(queue.path),
        "queue_sha256": hashlib.sha256(queue.path.read_bytes()).hexdigest()
        if queue.path.is_file() else "",
        "rows": {"train": len(train), "holdout": len(holdout)},
        "refused": refused, "files": files, "labels": labels,
        "note": "可训练版本草案：未经训练与评估，不得对外引用任何指标",
    }
    (root / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    # 与训练侧约定对齐：数据集目录同时写 stats.json；并给出"新样本与既有卷的家族级重合度"
    # （训练侧门禁一的简化版：只看家族；E2/E3 近似重复需用他们的 hackskills_overlap.py，
    #   本模块不做，避免给出半个口径的数字）
    manifest["overlap_vs_existing"] = _overlap_vs_existing([r for r in rows])
    _paired = {}
    for r in rows:
        fam = r.get("family") or family_of(r.get("method", "GET"), r.get("path", ""))
        _paired.setdefault(fam, set()).add(r.get("intent_label"))
    _both = sum(1 for v in _paired.values() if len(v) > 1)
    _single = [f for f, v in _paired.items() if len(v) == 1]
    manifest["pairing"] = {"families": len(_paired), "paired_families": _both,
                           "unpaired_families": len(_single),
                           "unpaired_examples": _single[:10],
                           "note": "未配对家族（只有攻击侧或只有良性侧）会训出偏标签的判官"}
    (root / "stats.json").write_text(json.dumps({
        "version": version, "created_at": manifest["created_at"],
        "rows": manifest["rows"], "labels": manifest["labels"],
        "families": {"train": None, "holdout": None},
        "note": "本 stats 只描述导出草案；口径与 dataset/judge_v3_mix/stats.json 同级但字段更少，"
                "不得与既有卷的 stats 混引",
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    total = manifest["rows"]["train"] + manifest["rows"]["holdout"]
    a, b = manifest["labels"]["attack"], manifest["labels"]["benign"]
    warn = []
    if total and (a == 0 or b == 0):
        warn.append("单一标签（全部 %s）—— 直接训练会把判官教成只判这一类" % ("attack" if b == 0 else "benign"))
    if total and min(a, b) / max(total, 1) < 0.2:
        warn.append("标签严重不平衡（attack %d / benign %d）—— 需补配对样本或加权" % (a, b))
    if manifest.get("pairing", {}).get("unpaired_families"):
        warn.append("有 %d 个家族只有单侧（见 MANIFEST.pairing）—— 它们会把判官教偏"
                    % manifest["pairing"]["unpaired_families"])
    # --- 导出物自检（陌生验收 2026-09-27 第二轮抓到的两处「假绿灯」）---
    all_recs = list(train) + list(holdout)
    qids = {r.get("sample_id") for r in rows}
    qrec = [((rec.get("meta") or {}).get("queue_sample_id")) for rec in all_recs]
    unresolved = [x for x in qrec if x not in qids]
    manifest["traceability"] = {
        "exported_rows": len(qrec), "resolved_to_queue": len(qrec) - len(unresolved),
        "unresolved": len(unresolved),
        "unresolved_examples": [str(x)[:28] for x in unresolved[:5]],
        "note": "逐条以 meta.queue_sample_id 回指队列原样本；未解析条数为 0 才谈得上「每条可追责」",
    }
    if unresolved:
        warn.append("可追溯性不足：%d/%d 条导出行回不到队列（见 MANIFEST.traceability）——"
                    "不要把这份卷当作「每条都能追责」的证据"
                    % (len(unresolved), len(qrec)))

    def _fam(rec):
        return ((rec.get("meta") or {}).get("family") or "")

    def _lbl(rec):
        """导出的标签在 messages[-1].content（与 export 里的原有计数口径一致）。"""
        msgs = rec.get("messages") or []
        if msgs:
            c = str((msgs[-1] or {}).get("content") or "").strip().lower()
            if c in ("attack", "benign"):
                return c
        return rec.get("label") or (rec.get("meta") or {}).get("label")

    fam_t = {_fam(rec) for rec in train} - {""}
    fam_h = {_fam(rec) for rec in holdout} - {""}
    shared = sorted(fam_t & fam_h)
    hl = {k: sum(1 for rec in holdout if _lbl(rec) == k) for k in ("attack", "benign")}
    manifest["split_independence"] = {
        "train_families": len(fam_t), "holdout_families": len(fam_h),
        "shared_families": len(shared), "shared_examples": shared[:5],
        "holdout_labels": hl,
        "note": "家族级切分只在 train/holdout 不共族时才算独立；共族时 holdout 会泄漏，"
                "其成绩不得当作「未见过的家族」的泛化证据",
    }
    if shared:
        warn.append("切分不独立：train 与 holdout 共用 %d 个家族（见 MANIFEST.split_independence）——"
                    "此时 holdout 不是独立集，其成绩不得当作泛化证据" % len(shared))
    if (hl.get("attack") or 0) == 0 or (hl.get("benign") or 0) == 0:
        warn.append("holdout 单侧缺标签（attack %d / benign %d）—— 这份 holdout 只能测一侧，"
                    "另一侧的成绩无从谈起" % (hl.get("attack", 0), hl.get("benign", 0)))

    # --- B 留存登记表状态：为空时必须大声（否则"已排除 B 家族"是假绿灯）---
    try:
        from engine.learning_queue import b_sealed_family_ids as _bfi
        _b = _bfi()
        missing = [s.get("path") for s in (_b.get("sources") or [])
                   if s.get("status") != "ok"]
        manifest["b_sealed_registry"] = {
            "loaded_families": _b.get("loaded_families", 0),
            "sources_missing": missing,
            "sources": _b.get("sources"),
            "note": "登记表为空时，B 留存拒入只剩 `:8082` 路径标记这一道；"
                    "不要把这份卷当作「已排除 B 家族」的证据",
        }
        if missing:
            warn.append("B 留存登记来源不完整；训练门禁必须拒绝")
        if not _b.get("loaded_families"):
            warn.append("B 留存家族登记表为空（%d 个卷不在本机）—— 当前拒入只剩 `:8082` 路径标记这一道，"
                        "本卷不得声称已排除 B 家族（见 MANIFEST.b_sealed_registry）" % len(missing))
    except Exception as exc:  # noqa: BLE001 —— 状态读不到也是"未验证"，不许静默当通过
        manifest["b_sealed_registry"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
        warn.append("B 留存登记表状态读取失败（%s）—— 本卷的 B 排除性未验证"
                    % type(exc).__name__)

    # 标签语义与可训练性（审阅 P2：警告只说明问题、不阻断训练是不可接受的）
    needs_review = sum(1 for r in rows if r.get("label_review_state") not in ("auto_ok", "human_reviewed"))   # 缺字段=待审，不得宽松默认
    manifest["label_semantics"] = {
        "traffic_intent": "这段流量是不是攻击（判官 JEV 的任务）",
        "exploit_outcome": "这发有没有打穿；marker 只是成功证据之一，缺 marker 不等于正常",
        "label_review_state": "auto_ok 或 human_reviewed，仍需样本批准和全部质量门禁；其他状态禁止训练",
        "rule": "攻击探针恒为 attack；良性仅在无 marker 时为 benign；其余交人审",
    }

    manifest["rows_needing_review"] = needs_review
    if needs_review:
        warn.append("有 %d 条样本的标签待人审（label_review_state=needs_review）——"
                    "training_ready=false，**不得**交给训练侧自动消费" % needs_review)

    manifest["balance_warnings"] = warn
    manifest["training_ready"] = (needs_review == 0 and not warn)
    manifest["training_blockers"] = list(warn)   # 全部告警（含 needs_review）之后的最终阻塞清单
    (root / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    (root / "TRAINING.md").write_text(_training_doc(version, manifest), encoding="utf-8")
    return manifest


def _overlap_vs_existing(rows: List[dict]) -> dict:
    """新样本 vs 既有训练卷的**家族级**重合（训练侧门禁一的简化版）。

    只比家族，不做 E2/E3 近似重复（那需要训练侧的 overlap 工具与全量卷）。
    B 留存卷一律不读（一次性读数、不用于选型）。
    """
    base = ROOT / "dataset" / "judge_v3_mix"
    out = {"compared_against": str(base.relative_to(ROOT)),
           "existing_families": 0, "overlapping_families": 0,
           "checked": "family-level only; E2/E3 near-duplicate NOT done here",
           "b_sealed_read": False}
    fams = set()
    for split in ("train.jsonl", "holdout.jsonl"):
        f = base / split
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            fam = (rec.get("meta") or {}).get("family")
            if fam:
                fams.add(fam)
    out["existing_families"] = len(fams)
    if fams:
        out["overlapping_families"] = sum(
            1 for r in rows
            if (r.get("family") or family_of(r.get("method", "GET"), r.get("path", ""))) in fams)
    else:
        # 对比卷不在本机（v3 的两个卷未入库，需 build_judge_v3_mix.py 重生成）：
        # 必须写明"未算"，否则 0 会被误读成"无重合"。
        out["status"] = "not_computed"
        out["reason"] = ("对比卷缺失（dataset/judge_v3_mix 下无 train/holdout.jsonl；"
                         "需先 python dataset/build_judge_v3_mix.py --out dataset/judge_v3_mix）"
                         "—— 本次家族级重合度未算，不得把 0 当作结论")
    return out


def _training_doc(version: str, m: dict) -> str:
    warn = m.get("balance_warnings") or []
    warn_block = ("\n".join(f"- ⚠️ {w}" for w in warn) if warn
                  else "- 未发现明显不平衡（仍建议人工看一眼分布）")
    return f"""# judge_{version} — 可训练版本草案

生成时间：{m['created_at']}　来源队列：`{m['source_queue']}`（sha256 前 16 位 {m['queue_sha256'][:16]}）

| | |
| --- | --- |
| train | {m['rows']['train']} 行 |
| holdout | {m['rows']['holdout']} 行 |
| 标签分布 | attack {m['labels']['attack']} / benign {m['labels']['benign']} |
| B 留存拒入 | {len(m['refused'])} 条（硬拒，不允许绕过） |

## 标签口径（必须一起读）

- 标签来自**硬信号**（marker 命中 / 业务门禁 / 复测结果），**不来自判官自己的判读**；
  判官读数在 `meta.judge_verdict`，只用于分歧分析。
- 攻击形态但缺成功证据的请求**不再按 benign 计入**（那是把攻击意图与攻击成功混为一谈）：
  它们记 `traffic_intent=attack` + `exploit_outcome=blocked/no_marker` + `label_review_state=needs_review`，待人审；它们是
  判官误报的典型素材。
- 已封家族的桶归属**查表决定、不重切**；B 集（`:8082` 路径与留存家族）**一律不进**。

## 标签平衡提醒

{warn_block}

## 后接流程（不在本产物内）

0. 目录形状与既有训练卷一致（`train.jsonl` / `holdout.jsonl` / `stats.json`），
   可直接：`python bench/train/train_lora.py --data dataset/{version} --batch-size 4 --epochs 3`；
   与既有卷的家族级重合见 `MANIFEST.json: overlap_vs_existing`（E2/E3 近似重复需用训练侧的
   overlap 工具另做，本产物不做也不冒充）；
1. 训练走既有 `bench/train` 流程（本产物只提供数据 + 账本）；
2. 评估 **零样本 vs 微调后分栏**；线上带 0.40–0.60 与离线带 0.20–0.80 **不得互引**；
   不得声称"未见攻击泛化"；
3. 对外数字必须先入 `claims.yaml` 才能引用。
"""

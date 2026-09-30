"""T5 候选学习队列 —— 对抗样本的证据归档(契约 §2.6 / 施工书 §6.3)。

分工边界:本模块只负责**候选样本的生成、去重、家族归组与审核状态**,
不训练、不改现有数据集、不覆盖 v1/v2/v3 任何产物。样本进入队列 ≠ 已训练;
只有 review_state=approved 的样本才可能进入之后新建的数据集版本。

复用面(数据面勘察冻结):fold/h/scrub/family_of/make_record/dedup_key/split
直接 import 自 dataset/build_judge.py(先例 build_judge_v2_dvwa.py:39)。
**绝不调用 build_judge.main()** —— HEAD 版 main() 有 b_dropped 未初始化缺陷
(见 T0-contract-freeze F12),任何重建 v0/v1 卷的尝试都会在收尾崩溃。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Dict, List, Optional
from engine.storage import JsonlStore

ROOT = pathlib.Path(__file__).resolve().parent.parent
_DATASET = ROOT / "dataset"
if str(_DATASET) not in sys.path:
    sys.path.insert(0, str(_DATASET))

import build_judge as BJ  # noqa: E402  只取纯函数,不触发任何 main()

# ---- 脱敏(契约 §2.6):在 build_judge.SCRUB 基础上补三类泄漏面 ----
# F13 勘察结论:现有 SCRUB 只覆盖 /home/ 路径,不覆盖 Windows 路径/Cookie/令牌。
SCRUB_V2 = list(BJ.SCRUB) + [
    (re.compile(r"[A-Za-z]:\\Users\\[^\s\"']+"), "[PATH]"),
    (re.compile(r"PHPSESSID=[A-Za-z0-9]+"), "PHPSESSID=[SCRUB]"),
    (re.compile(r"(?i)Cookie:\s*[^\n\r]+"), "Cookie: [SCRUB]"),
    (re.compile(r"(?i)Bearer\s+\S+"), "Bearer [SCRUB]"),
]
# 版本 = 规则内容 hash:改规则自动换版本,旧样本的 scrub_version 可追溯
SCRUB_VERSION = "v2:" + BJ.h("|".join(f"{p.pattern}->{r}" for p, r in SCRUB_V2))[:8]

# B 集硬拒(契约 §2.6,采 v2/v3 硬拒模式而非 v0 的剔除计数)
B_PATH_MARKER = ":8082"


def scrub_v2(text: str) -> str:
    out = text or ""
    for pat, repl in SCRUB_V2:
        out = pat.sub(repl, out)
    return out


def input_hash(method: str, path: str, body: str) -> str:
    """规范化输入内容 hash(label 无关,跨环境稳定)。

    不用 BJ.make_record 的 sample_id:它把 label/status 混进哈希
    (勘察 §1.4),同一请求双形态会得出不同 id。
    """
    return BJ.h(scrub_v2(f"{method} {path} {body or ''}"))


def _family_b_cores() -> set:
    """B 家族核集合(只读留存卷);读不到返回空集,不阻断。"""
    try:
        from dataset import judge_v2_dvwa as _v2  # type: ignore  # noqa
    except Exception:  # noqa: BLE001
        try:
            sys.path.insert(0, str(_DATASET))
            import judge_v2_dvwa as _v2  # type: ignore  # noqa
        except Exception:  # noqa: BLE001
            return set()
    try:
        return set(_v2.b_family_cores(ROOT / "bench" / "train" / "results" / "d7_b"))
    except Exception:  # noqa: BLE001
        return set()


# ---- B 留存家族台账(只读;候选 → 数据集入口据此硬拒 B 族)----
# 契约 §2.6:B 集永封,新样本不得与 B 族同族进池。可核对来源 = 仓内
# d7_b 留存卷的行级 family 字段。既有 _family_b_cores() 依赖
# dataset.judge_v2_dvwa 模块(仓内不存在),实测恒为空集 —— 故此处直接
# 从卷文件读家族键,并保留 _family_b_cores() 作为额外来源。
B_SEALED_VOLUMES = (
    "bench/train/results/d7_b/samples_b_clean.jsonl",
    "bench/train/results/d7_b/samples_b_all.jsonl",
    "bench/train/results/d7_b/holdout_b_clean.jsonl",
    "bench/train/results/d7_b/holdout_b.jsonl",
)
# 折叠成家族键后 ":8082" 退化为裸数字(实测 A 侧 6805 族零命中)。
# 命中即拒:宁可错拒一条候选,不可放 B 实例同族样本进池。
B_FAMILY_HINT = "8082"

_b_sealed_cache: Optional[Dict[str, object]] = None


def b_sealed_family_ids() -> Dict[str, object]:
    """B 留存家族键集合 + 来源账(带缓存;只读,不写盘)。

    返回 {"families": set, "sources": [{"path","status","n_families"}],
          "loaded_families": int}。status 为 "ok"/"missing"/"unreadable":
    卷不在本机时如实记为 missing,不假定"本机没有 B 族"。
    """
    global _b_sealed_cache
    if _b_sealed_cache is not None:
        return _b_sealed_cache
    fams = set(_family_b_cores())
    sources: List[dict] = []
    for rel in B_SEALED_VOLUMES:
        p = ROOT / rel
        if not p.is_file():
            sources.append({"path": rel, "status": "missing", "n_families": 0})
            continue
        before = len(fams)
        try:
            with p.open(encoding="utf-8-sig") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    fam = rec.get("family") or (rec.get("meta") or {}).get("family")
                    if fam:
                        fams.add(fam)
        except OSError:
            sources.append({"path": rel, "status": "unreadable", "n_families": 0})
            continue
        sources.append({"path": rel, "status": "ok",
                        "n_families": len(fams) - before})
    _b_sealed_cache = {"families": fams, "sources": sources,
                       "loaded_families": len(fams)}
    return _b_sealed_cache


def is_b_sealed(family_id: Optional[str], path: str = "") -> Optional[str]:
    """命中 B 留存面返回拒入理由字符串,否则 None(供候选 → 数据集入口硬拒)。"""
    if path and B_PATH_MARKER in path:
        return "b_path_marker"
    if family_id and B_FAMILY_HINT in family_id:
        return "b_family_hint"
    if family_id and family_id in b_sealed_family_ids()["families"]:
        return "b_sealed_family"
    return None


_split_cache: Optional[Dict[str, str]] = None


def lookup_split(family: str) -> Optional[str]:
    """已封卷家族 → 其所在桶("train"/"holdout");未封卷返回 None。

    契约 §2.6:B 集及冻结评估桶不因新样本到来重新切分。已封卷家族
    必须**查表**(现有 train/holdout.jsonl 的 family 集合),不能用
    split() 重算 —— 否则同族样本可能漂到另一个桶,造成跨切分泄漏。
    """
    global _split_cache
    if _split_cache is None:
        _split_cache = {}
        for vol in (ROOT / "dataset" / "judge_v0",
                    ROOT / "dataset" / "judge_v2_merged"):
            for bucket in ("train", "holdout"):
                f = vol / f"{bucket}.jsonl"
                if not f.is_file():
                    continue
                try:
                    with f.open(encoding="utf-8-sig") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            fam = (rec.get("meta") or {}).get("family")
                            if fam:
                                _split_cache.setdefault(fam, bucket)
                except OSError:
                    continue
    return _split_cache.get(family)


class LearningQueue:
    """跨进程事务 JSONL 队列；标签修订使旧审批失效。"""

    def __init__(self, queue_path: pathlib.Path):
        self.path = pathlib.Path(queue_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------- 读 ----------
    def load(self) -> List[dict]:
        return JsonlStore(self.path).load()

    def mutate(self, sample_id, callback):
        def change(rows):
            hit = next((r for r in rows if r.get("sample_id") == sample_id), None)
            if hit is None:
                raise KeyError(f"unknown sample_id: {sample_id}")
            callback(hit)
            return hit
        return JsonlStore(self.path).update(change)

    def stats(self) -> dict:
        rows = self.load()
        out = {"total": len(rows), "pending": 0, "approved": 0, "rejected": 0,
               "duplicates": 0, "by_split": {}}
        for r in rows:
            st = r.get("review_state", "pending")
            out[st] = out.get(st, 0) + 1
            if r.get("exclusion_reason") == "duplicate":
                out["duplicates"] += 1
            sp = r.get("split_assignment")
            if sp:
                out["by_split"][sp] = out["by_split"].get(sp, 0) + 1
        return out

    # ---------- 写 ----------
    def append(self, sample: dict) -> dict:
        """入队;同 (input_hash, intent_label, input_mode) 已存在 → 返回原行并标 duplicate。"""
        sample = dict(sample)
        ih = sample.get("input_hash") or input_hash(
            sample.get("method", ""), sample.get("path", ""), sample.get("body", ""))
        sample["input_hash"] = ih
        sample.setdefault("captured_at", _utcnow())
        if not sample.get("sample_id"):
            sample["sample_id"] = ih[:12] + "-" + BJ.h(
                f"{sample.get('intent_label', '')}|{sample.get('input_mode', '')}")[:6]
        def change(rows):
            for existing in rows:
                if (existing.get("input_hash") == ih
                        and existing.get("intent_label") == sample.get("intent_label")
                        and existing.get("input_mode") == sample.get("input_mode")):
                    return {"duplicate": True, "existing": existing}
            sample.setdefault("label_revision", 0)
            rows.append(sample)
            return {"duplicate": False, "sample": sample}
        return JsonlStore(self.path).update(change)

    def _set_state(self, sample_id, state, *, expected_label_revision=None,
                   reviewer=None, note=""):
        def change(hit):
            revision = int(hit.get("label_revision", 0))
            expected = 0 if expected_label_revision is None else expected_label_revision
            if state == "approved" and revision != expected:
                raise ValueError("label revision changed; review the current label before approval")
            if hit.get("review_state") != "pending":
                raise ValueError(f"sample {sample_id} already {hit.get('review_state')}")
            hit["review_state"] = state
            if reviewer is not None:
                if not reviewer.strip():
                    raise ValueError("reviewer must not be empty")
                hit.update(reviewer=reviewer, review_note=note, reviewed_at=_utcnow())
            hit.setdefault("admission_audit", []).append(
                {"state": state, "label_revision": revision,
                 "reviewer": reviewer or hit.get("reviewer"), "at": _utcnow()})
        return self.mutate(sample_id, change)

    def approve(self, sample_id, *, expected_label_revision=None, reviewer=None, note=""):
        return self._set_state(sample_id, "approved",
                               expected_label_revision=expected_label_revision,
                               reviewer=reviewer, note=note)

    def reject(self, sample_id):
        return self._set_state(sample_id, "rejected")


def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def from_run_dir(run_dir, queue_path=None) -> dict:
    """从一轮 run 目录生成候选样本并直接入队(demo_case 调用契约)。

    真值旁证来自 flow.jsonl 自身记录的 intent_label/outcome(记录于
    过程证证,与观测面分离);观测面仅用于 input_hash/family_id。
    返回 {"queued": n, "duplicates": n, "samples": [...], "queue": 路径}。
    """
    run_dir = pathlib.Path(run_dir)
    run_id = run_dir.name
    flows: List[dict] = []
    f = run_dir / "flow.jsonl"
    if f.is_file():
        with f.open(encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                obs = rec.get("obs") or {}
                flows.append({
                    "flow_id": rec.get("flow_id"),
                    "method": obs.get("method", ""),
                    "path": obs.get("path", ""),
                    "body": obs.get("body", "") or "",
                    "status": obs.get("status", 0),
                    "response": obs.get("response", "") or "",
                    "replay_of": rec.get("replay_of"),
                    "finding_id": None,
                })
    intent_map = {}
    try:
        with f.open(encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                intent_map[rec.get("flow_id")] = {
                    "intent_label": rec.get("intent_label", "unknown"),
                    "outcome": rec.get("outcome", "unknown"),
                    "label_source": "case_intent",
                    "evidence_refs": [f"flow:{rec.get('flow_id')}"]
                    + (["markers_hit"] if rec.get("markers_hit") else []),
                }
    except OSError:
        pass
    rows = from_flows(run_id, flows, intent_map)
    q = LearningQueue(queue_path or (run_dir / "learning-candidates.jsonl"))
    queued = dups = 0
    for row in rows:
        res = q.append(row)
        if res.get("duplicate"):
            dups += 1
        else:
            queued += 1
    return {"queued": queued, "duplicates": dups, "samples": rows,
            "queue": str(q.path)}


def from_flows(run_id: str, flows: List[dict],
               intent_map: Dict[str, dict]) -> List[dict]:
    """本轮 flow 行 + 真值旁证 → 候选样本列表(未入队,由调用方 append)。

    flows:flow.jsonl 行(含 flow_id/method/path/body/status/response,
           可选 replay_of)。
    intent_map:flow_id → {intent_label, outcome, label_source, evidence_refs},
           真值旁证只进 meta 记录,**不进模型输入**(契约 §2.5 真值隔离)。
    """
    out: List[dict] = []
    for fl in flows:
        fid = fl.get("flow_id") or ""
        meta = intent_map.get(fid, {})
        method = fl.get("method", "")
        path = fl.get("path", "")
        body = fl.get("body", "") or ""
        fam = BJ.family_of(method, path, body)
        row = {
            "sample_id": None,
            "run_id": run_id,
            "flow_id": fid,
            "finding_id": fl.get("finding_id"),
            "replay_of": fl.get("replay_of"),
            "captured_at": _utcnow(),
            "input_hash": input_hash(method, path, body),
            "family_id": fam,
            "input_mode": "exchange",
            "intent_label": meta.get("intent_label", "unknown"),
            "outcome": meta.get("outcome", "unknown"),
            "label_source": meta.get("label_source", "unknown"),
            "evidence_refs": meta.get("evidence_refs") or [],
            "scrub_version": SCRUB_VERSION,
            "review_state": "pending",
            "split_assignment": lookup_split(fam),
            "dataset_version": None,
            "exclusion_reason": None,
        }
        # B 集硬拒(内容级):坐标 :8082 是 B 实例标识,命中即拒入
        if B_PATH_MARKER in path:
            row["exclusion_reason"] = "b_set_contamination"
            row["review_state"] = "rejected"
            row["split_assignment"] = None
        out.append(row)
    return out

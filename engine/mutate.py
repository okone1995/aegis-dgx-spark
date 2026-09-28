"""变异刷量（T13 续）：把 hunt 阶梯的每个探测点当母弹，用预制变换引擎生成不同请求。

为什么这样做：hunt 阶梯是确定性的（重复跑同一轮全被去重），要量就得**造多样性**；
引擎本来就有 20+ 变换（sqli 当前启用 5 招：dbl_encode / kw_case / inline_comment /
space_alt / param_pollute），对 N 个母弹做一遍就是 N×5 条互不相同的真实交换。

纪律（与 hunt / jevtrain 完全一致）：
  * 每条变体先过 `payload_policy` 与 scope，再发射；
  * 真值标签由**硬信号**给（marker 命中 ⇒ attack；未命中 ⇒ benign 的"攻击形态"）；
  * 逐条**真调判官**取读数（advisory，只作分歧分析）；
  * B 留存家族拒入；预算硬上限；队列写入失败可见不静默。
"""
from __future__ import annotations

import json
import pathlib
import sys
from typing import List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import attacker, replay  # noqa: E402
from engine.jevtrain import (  # noqa: E402
    DEFAULT_QUEUE,
    _default_judge,
    family_of,
    _utcnow,
)
from engine.learning_queue import LearningQueue, is_b_sealed, scrub_v2  # noqa: E402


class MutateError(RuntimeError):
    pass


def parents_from_hunt(result_path: pathlib.Path) -> List[Tuple[str, str]]:
    """把一次 hunt 的 attempts 还原成母弹请求文本：[(step, 请求行)]。"""
    doc = json.loads(pathlib.Path(result_path).read_text(encoding="utf-8"))
    out = []
    for at in doc.get("attempts") or []:
        url = at.get("url") or ""
        if "?" not in url:
            continue
        path_q = url.split("://", 1)[-1]
        path_q = "/" + path_q.split("/", 1)[1] if "/" in path_q else "/"
        out.append((str(at.get("step") or "probe"), f"GET {{{{TARGET}}}}{path_q} HTTP/1.1\n"
                                                   "Cookie: {{SESSION}}\n"))
    for pf in (doc.get("library_hit"),):
        pass
    return out


def parents_from_library(profile, cls: str) -> List[Tuple[str, str]]:
    d = profile.plugin_dir(cls, ROOT / "plugins") / "payloads" / "attack"
    return [(pf.name, pf.read_text(encoding="utf-8")) for pf in sorted(d.glob("*.txt"))] \
        if d.is_dir() else []


def mutate_round(base: str, cls: str, parents: List[Tuple[str, str]], instance: str = "a",
                 profile=None, budget_requests: int = 150, queue: Optional[LearningQueue] = None,
                 judge_fn=None, scope=None) -> dict:
    import yaml
    from engine.target_profile import active as active_profile
    profile = profile or active_profile()
    queue = queue or LearningQueue(DEFAULT_QUEUE)
    judge_fn = judge_fn or _default_judge

    meta = yaml.safe_load((profile.plugin_dir(cls, ROOT / "plugins") / "plugin.yaml")
                          .read_text(encoding="utf-8"))
    markers = list(meta.get("markers") or (meta.get("detector") or {}).get("markers") or [])
    if not markers:
        raise MutateError("plugin.yaml 未声明 markers ⇒ 无判定依据，拒绝盲跑")
    menu = attacker.menu_for(cls)
    if not menu:
        raise MutateError(f"{cls} 没有可用的变换菜单")

    try:
        cookie = replay.admin_session(base, instance)
    except Exception:  # noqa: BLE001
        cookie = ""
    ctx = replay.make_ctx(base, instance, cookie)

    fired = added = dup = 0
    hits = refused = notapplic = 0
    rows = []
    for pname, ptext in parents:
        for tid in menu:
            if fired >= budget_requests:
                break
            try:
                variant = attacker.apply_transform(tid, cls, ptext, base)
            except Exception:  # noqa: BLE001
                variant = None
            if not variant:
                notapplic += 1
                continue
            req = variant if isinstance(variant, str) else variant.get("text", "")
            if not req:
                notapplic += 1
                continue
            try:
                replay.check_payload_policy(req)     # 与主链同一道闸
            except Exception as exc:  # noqa: BLE001
                refused += 1
                rows.append({"parent": pname, "transform": tid, "fired": False,
                             "policy": f"rejected: {str(exc)[:60]}"})
                continue
            if scope is not None:
                try:
                    scope.check_url(req.split()[1].replace("{{TARGET}}", base))
                except Exception as exc:  # noqa: BLE001
                    refused += 1
                    rows.append({"parent": pname, "transform": tid, "fired": False,
                                 "policy": f"scope: {str(exc)[:60]}"})
                    continue
            res = replay.replay_payload(base, req, f"{pname}:{tid}", ctx, markers)
            fired += 1
            hit = bool(res.markers_hit)
            hits += 1 if hit else 0
            label = "attack" if hit else "benign"
            reason = "marker_hit:" + ",".join(res.markers_hit)[:50] if hit \
                else "attack_shaped_but_no_marker"
            pth = req.split("\n", 1)[0].split()[1].replace("{{TARGET}}", "")
            fam = family_of("GET", pth)
            if is_b_sealed(fam, pth):
                refused += 1
                rows.append({"parent": pname, "transform": tid, "fired": True,
                             "policy": "b_sealed_refused"})
                continue
            exch = {"method": "GET", "path": pth, "body": "",
                    "status": res.status, "response": scrub_v2(res.response_text)[:1200]}
            j = judge_fn(exch) or {}
            sample = {
                "run": "mutate_round", "flow_id": f"{pname}:{tid}",
                "method": "GET", "path": pth, "body": "",
                "status": res.status, "response": scrub_v2(res.response_text)[:4000],
                "intent_label": label, "input_mode": "exchange",
                "truth_reason": reason, "family": fam, "pair_role": label,
                "judge": {k: j.get(k) for k in ("verdict", "p_attack", "band", "protocol_id",
                                                "model_version", "latency_ms")},
                "agreement": (j.get("verdict") == label) if j.get("verdict") else None,
                "source": "mutate_engine", "collected_at": _utcnow(),
                "review_state": "pending",
            }
            r2 = queue.append(sample)
            if r2.get("duplicate"):
                dup += 1
            else:
                added += 1
            rows.append({"parent": pname, "transform": tid, "fired": True,
                         "status": res.status, "markers_hit": list(res.markers_hit),
                         "truth": label, "judge": j.get("verdict"),
                         "duplicate": bool(r2.get("duplicate"))})
        if fired >= budget_requests:
            break
    return {"status": "ok", "base": base, "class": cls, "menu": menu,
            "parents": len(parents), "fired": fired, "added": added, "duplicates": dup,
            "hits": hits, "policy_refused": refused, "transform_not_applicable": notapplic,
            "budget_requests": budget_requests, "rows": rows[:200],
            "note": "标签来自硬信号；判官读数仅作分歧分析；队列 ≠ 已训练"}

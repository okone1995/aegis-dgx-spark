#!/usr/bin/env python3
"""Fail-closed agent bridge for judge-training material collection.

Read-only: selftest / collect / collect-flows / report.
Gated by the operator (AEGIS_JEVTRAIN_OPERATOR): approve / export.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

DEFAULT_QUEUE_REL = "dataset/review_queue/jevtrain_candidates.jsonl"


class BridgeError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _engine():
    cands = []
    env_root = os.environ.get("AEGIS_ENGINE_ROOT")
    if env_root:
        cands.append(pathlib.Path(env_root))
    cands += [p for p in pathlib.Path(__file__).resolve().parents
              if (p / "engine" / "jevtrain.py").is_file()]
    for c in cands:
        if (c / "engine" / "jevtrain.py").is_file():
            if str(c) not in sys.path:
                sys.path.insert(0, str(c))
            from engine import jevtrain as JT  # noqa: PLC0415
            from engine.learning_queue import LearningQueue  # noqa: PLC0415
            return c, JT, LearningQueue
    raise BridgeError("engine_not_found")


def _queue(root, LearningQueue):
    rel = os.environ.get("AEGIS_JEVCAND_QUEUE", DEFAULT_QUEUE_REL)
    p = pathlib.Path(rel)
    return LearningQueue(p if p.is_absolute() else root / rel)


def _operator(reviewer: str) -> None:
    """操作员闸门：环境变量必须设，且 --reviewer 必填并与之逐字相同。

    为什么必须逐字相同且不许留空：provenance 要落到"谁批的"。原先允许在没有
    --reviewer 的情况下放行，等于允许无署名批准（陌生验收 2026-09-27 #3 指出）。
    """
    op = os.environ.get("AEGIS_JEVTRAIN_OPERATOR", "").strip()
    if not op:
        raise BridgeError("operator_gate_not_satisfied")
    rv = (reviewer or "").strip()
    if not rv:
        raise BridgeError("operator_reviewer_required")
    if rv != op:
        raise BridgeError("operator_gate_not_satisfied")


def cmd_selftest(a) -> int:
    root, JT, LearningQueue = _engine()
    q = _queue(root, LearningQueue)
    print(json.dumps({
        "status": "ok", "engine_root": str(root), "queue": str(q.path),
        "queue_stats": q.stats(),
        "label_policy": "hard_signals_only",
        "sealed_guard_probe": {"sealed_path_refused": bool(
            JT.is_b_sealed("GET /x", "/x?u=http://127.0.0.1:8082/y"))},
        "operator_gate_set": bool(os.environ.get("AEGIS_JEVTRAIN_OPERATOR")),
    }, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_collect(a) -> int:
    root, JT, LearningQueue = _engine()
    q = _queue(root, LearningQueue)
    try:
        res = JT.collect_from_run(pathlib.Path(a.run), q, with_response=not a.no_response)
    except Exception as exc:  # noqa: BLE001
        raise BridgeError("collect_failed") from exc
    print(json.dumps({"status": "ok", **res}, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_collect_flows(a) -> int:
    root, JT, LearningQueue = _engine()
    q = _queue(root, LearningQueue)
    try:
        res = JT.collect_from_flows_jsonl(pathlib.Path(a.file), q)
    except Exception as exc:  # noqa: BLE001
        raise BridgeError("collect_failed") from exc
    print(json.dumps({"status": "ok", **res}, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_report(a) -> int:
    root, JT, LearningQueue = _engine()
    q = _queue(root, LearningQueue)
    rep = JT.report(q)
    if rep["total"] == 0:
        raise BridgeError("queue_empty")
    print(json.dumps({"status": "ok", **rep}, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_review_label(a) -> int:
    _operator(a.reviewer)
    root, JT, LearningQueue = _engine()
    try:
        row = JT.review_label(_queue(root, LearningQueue), a.id, a.traffic_intent, a.reviewer, a.note)
    except JT.JevTrainError as exc:
        raise BridgeError("label_review_refused") from exc
    print(json.dumps({"status": "ok", "sample_id": a.id,
        "traffic_intent": row["traffic_intent"], "label_review_state": row["label_review_state"],
        "review_state": row["review_state"], "label_audit": row["label_audit"]}, ensure_ascii=False))
    return 0


def cmd_approve(a) -> int:
    _operator(a.reviewer)
    root, JT, LearningQueue = _engine()
    q = _queue(root, LearningQueue)
    rows = {r.get("sample_id"): r for r in q.load()}
    row = rows.get(a.id)
    if row is None:
        raise BridgeError("approve_refused")
    # #4：批的样本必须有硬信号标签（attack/benign），否则拒批并给 reason
    label = str(row.get("intent_label") or "").strip()
    if label not in ("attack", "benign"):
        print(json.dumps({"status": "error", "code": "approve_refused",
                          "reason": "no_hard_signal_label",
                          "sample_id": a.id, "intent_label": label or None},
                         ensure_ascii=False, sort_keys=True))
        return 2
    # #6：--only-disagreements 真正生效（不是判官分歧的不许批）
    if a.only_disagreements and row.get("agreement") is not False:
        print(json.dumps({"status": "error", "code": "approve_refused",
                          "reason": "not_a_disagreement", "sample_id": a.id,
                          "agreement": row.get("agreement")},
                         ensure_ascii=False, sort_keys=True))
        return 2
    try:
        JT.stamp_reviewer(q, a.id, a.reviewer, note=a.note or "")
        q.approve(a.id)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "code": "approve_refused",
                          "detail": str(exc)[:200]}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "ok", "sample_id": a.id, "reviewer": a.reviewer,
                      "intent_label": row.get("intent_label"),
                      "judge_verdict": (row.get("judge") or {}).get("verdict"),
                      "agreement": row.get("agreement"),
                      "matrix_cell": row.get("matrix_cell")}, ensure_ascii=False,
                     sort_keys=True))
    return 0


def cmd_pairs(a) -> int:
    """攻良配对报告（#2）：引擎有此能力就必须在桥接层可调用，不能只在 engine CLI 有。"""
    root, JT, LearningQueue = _engine()
    fn = getattr(JT, "pairing_report", None)
    if fn is None:
        raise BridgeError("pairs_unavailable")
    q = _queue(root, LearningQueue)
    try:
        out = fn(q)
    except TypeError:  # 兼容只接受行列表的实现
        out = fn(q.load())
    print(json.dumps({"status": "ok", **out}, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_export(a) -> int:
    _operator(a.reviewer)
    root, JT, LearningQueue = _engine()
    q = _queue(root, LearningQueue)
    rows = [r for r in q.load() if r.get("review_state") == "approved"]
    if not rows:
        raise BridgeError("export_refused")
    out = pathlib.Path(a.out) if a.out else None
    m = JT.export(q, a.version, out_root=out)
    print(json.dumps({"status": "ok", **m}, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    c = sub.add_parser("collect")
    c.add_argument("--run", required=True)
    c.add_argument("--no-response", action="store_true")
    f = sub.add_parser("collect-flows")
    f.add_argument("--file", required=True)
    sub.add_parser("report")
    sub.add_parser("pairs")
    label = sub.add_parser("review-label")
    label.add_argument("--id", required=True)
    label.add_argument("--traffic-intent", choices=("attack", "benign"), required=True)
    label.add_argument("--reviewer", required=True)
    label.add_argument("--note", required=True, help="独立证据依据；不得用判官自述作真值")
    ap_ = sub.add_parser("approve")
    ap_.add_argument("--id", required=True)
    ap_.add_argument("--reviewer", required=True,
                     help="操作员署名；必须与 AEGIS_JEVTRAIN_OPERATOR 逐字相同")
    ap_.add_argument("--note", default="")
    ap_.add_argument("--only-disagreements", action="store_true")
    e = sub.add_parser("export")
    e.add_argument("--version", required=True)
    e.add_argument("--reviewer", required=True,
                   help="操作员署名；必须与 AEGIS_JEVTRAIN_OPERATOR 逐字相同")
    e.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    try:
        return {"selftest": cmd_selftest, "collect": cmd_collect,
                "collect-flows": cmd_collect_flows, "report": cmd_report,
                "pairs": cmd_pairs, "review-label": cmd_review_label, "approve": cmd_approve, "export": cmd_export}[a.cmd](a)
    except BridgeError as exc:
        print(json.dumps({"status": "error", "code": exc.code}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())

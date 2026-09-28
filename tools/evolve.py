#!/usr/bin/env python3
"""自主进化的操作员工具（T12/④）。

把"跑完 → 沉淀 → 人审 → 入库 → 下轮用上 → 改前/改后分栏"串成一次可执行、可审计的动作。

子命令：
  report          看队列现状（pending / confirmed / approved）与可入库项
  review          人审：--id <candidate_id> --reviewer <人名> (--approve|--reject) [--note]
  apply           写回载荷到 plugins（**默认 dry-run**；需 --apply 真写）
  verify-written  只重放"由本工具写回"的载荷（带 .provenance.json 的那些）并断言 marker 命中
  compare         把两轮搜索的结果做**分栏**对比（不许合并成一条曲线）

纪律（与 T12 施工书一致）：
  * 未命中（无 markers_hit）不得 approve；
  * 未 approved 不得写回；
  * 写回必须有人名（reviewer）；
  * 对比输出分栏，标注是哪一轮（改前/改后），并写明口径。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.poc_queue import PocQueue, PocQueueError  # noqa: E402


def _load_json(path: str) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def cmd_report(a) -> int:
    q = PocQueue(pathlib.Path(a.queue))
    if getattr(a, "emit_cid", False):
        rows = q.load()
        cands = [r["candidate_id"] for r in rows
                 if r.get("review_state") == "pending"
                 and r.get("evidence_state") == "confirmed"]
        print(cands[-1] if cands else "")
        return 0
    rows = q.load()
    st = q.stats()
    eligible = [r for r in rows if r.get("review_state") == "approved"
                and r.get("evidence_state") == "confirmed"]
    out = {
        "queue": str(a.queue),
        "stats": st,
        "eligible_for_writeback": [r["candidate_id"] for r in eligible],
        "pending_with_evidence": [r["candidate_id"] for r in rows
                                  if r.get("review_state") == "pending"
                                  and r.get("evidence_state") == "confirmed"],
        "pending_without_evidence": [r["candidate_id"] for r in rows
                                     if r.get("review_state") == "pending"
                                     and r.get("evidence_state") != "confirmed"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def cmd_review(a) -> int:
    q = PocQueue(pathlib.Path(a.queue))
    try:
        if a.approve:
            row = q.approve(a.id, a.reviewer, a.note or "")
        else:
            row = q.reject(a.id, a.reviewer, a.note or "")
    except PocQueueError as exc:
        print(json.dumps({"status": "error", "code": "review_refused",
                          "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "ok", "candidate_id": a.id,
                      "review_state": row.get("review_state"),
                      "reviewer": row.get("reviewer"),
                      "evidence_state": row.get("evidence_state"),
                      "markers_hit": (row.get("evidence") or {}).get("markers_hit")},
                     ensure_ascii=False))
    return 0


def cmd_apply(a) -> int:
    # 复用写回工具（它自己默认 dry-run）
    sys.argv = ["poc_apply.py", "--queue", str(a.queue), "--plugins-dir", str(a.plugins_dir)]
    if a.apply:
        sys.argv.append("--apply")
    import runpy
    return runpy.run_path(str(ROOT / "tools" / "poc_apply.py"), run_name="__main__") or 0


def cmd_verify_written(a) -> int:
    """只重放由本工具写回的载荷（旁边有 .provenance.json），断言 marker 命中。"""
    import requests
    from engine import replay
    from engine.target_profile import active as active_profile

    prof = active_profile()
    cls = a.cls or "sqli"
    pdir = prof.plugin_dir(cls, ROOT / "plugins")
    d = pdir / "payloads" / "attack"
    written = sorted(p for p in d.glob("*.txt") if p.with_suffix(".provenance.json").is_file())
    if not written:
        print(json.dumps({"status": "ok", "written": 0, "results": [],
                          "note": "没有带 provenance 的载荷（还没写回过）"}, ensure_ascii=False))
        return 0
    import yaml
    meta = yaml.safe_load((pdir / "plugin.yaml").read_text(encoding="utf-8"))
    markers = list(meta.get("markers") or (meta.get("detector") or {}).get("markers") or [])
    base = prof.instance(a.instance).base
    try:
        cookie = replay.admin_session(base, a.instance)
    except Exception:  # noqa: BLE001
        cookie = ""
    results = []
    for pf in written:
        raw = pf.read_text(encoding="utf-8")
        first = raw.split("\n", 1)[0].strip()
        path_q = first.split(" ")[1].replace("{{TARGET}}", "")
        r = requests.get(base + path_q, headers={"Cookie": cookie} if cookie else {},
                         timeout=10, allow_redirects=False)
        hits = [m for m in markers if m in r.text]
        results.append({"payload": pf.name, "status": r.status_code,
                        "markers_hit": hits, "hit": bool(hits),
                        "provenance": json.loads(
                            pf.with_suffix(".provenance.json").read_text(encoding="utf-8"))})
    out = {"status": "ok", "written": len(written),
           "hits": sum(1 for x in results if x["hit"]), "results": results}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["hits"] == out["written"] else 1


def cmd_compare(a) -> int:
    """改前/改后**分栏**对比。禁止把两轮合并。"""
    before, after = _load_json(a.before), _load_json(a.after)
    def col(d, label):
        return {
            "label": label,
            "claim": d.get("claim"),
            "requests_used": (d.get("budget") or {}).get("requests_used"),
            "seconds_used": (d.get("budget") or {}).get("seconds_used"),
            "first_hit_at": (d.get("hit") or {}).get("n"),
            "hit_step": (d.get("hit") or {}).get("step"),
            "library_hit": d.get("library_hit"),
            "column_count": d.get("column_count"),
        }
    out = {"schema": "evolve-compare-v1",
           "note": "两轮分栏，各自口径独立；不得合并成一条曲线",
           "before": col(before, a.label_before),
           "after": col(after, a.label_after),
           "delta": {
               "requests_saved": (before.get("budget", {}).get("requests_used", 0)
                                  - after.get("budget", {}).get("requests_used", 0)),
               "hit_earlier_by": ((before.get("hit") or {}).get("n", 0)
                                  - (after.get("hit") or {}).get("n", 0)),
           }}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", default=str(ROOT / "dataset" / "review_queue" / "poc_candidates.jsonl"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("report")
    rp.add_argument("--emit-cid", action="store_true",
                    help="只打印最后一个有硬证据且待审的候选 id（供脚本取用）")
    rp.set_defaults(func=cmd_report)

    r = sub.add_parser("review")
    r.add_argument("--id", required=True)
    r.add_argument("--reviewer", required=True)
    r.add_argument("--approve", action="store_true")
    r.add_argument("--reject", action="store_true")
    r.add_argument("--note", default="")
    r.set_defaults(func=cmd_review)

    ap_ = sub.add_parser("apply")
    ap_.add_argument("--plugins-dir", default=str(ROOT / "plugins"))
    ap_.add_argument("--apply", action="store_true")
    ap_.set_defaults(func=cmd_apply)

    v = sub.add_parser("verify-written")
    v.add_argument("--cls", default=None)
    v.add_argument("--instance", default="a")
    v.set_defaults(func=cmd_verify_written)

    c = sub.add_parser("compare")
    c.add_argument("--before", required=True)
    c.add_argument("--after", required=True)
    c.add_argument("--label-before", default="改前（零样本）")
    c.add_argument("--label-after", default="改后（用上 POC 库）")
    c.set_defaults(func=cmd_compare)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())

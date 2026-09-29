#!/usr/bin/env python3
"""jevtrain CLI（操作员侧驱动，供脚本调用）。

子命令：
  collect --run <run_dir> [--no-response]
  report
  approve-all --reviewer <人名> [--only-disagreements]
  export --version <v> [--out <dir>]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import jevtrain as JT  # noqa: E402
from engine.learning_queue import LearningQueue  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--run", required=True)
    c.add_argument("--no-response", action="store_true")
    f = sub.add_parser("collect-flows")
    f.add_argument("--file", required=True)
    sub.add_parser("report")
    sub.add_parser("pairs")
    h = sub.add_parser("collect-hunt")
    h.add_argument("--result", required=True)
    a = sub.add_parser("approve-all")
    a.add_argument("--reviewer", required=True)
    a.add_argument("--only-disagreements", action="store_true")
    e = sub.add_parser("export")
    e.add_argument("--version", required=True)
    e.add_argument("--out", default=None)
    args = ap.parse_args()

    q = LearningQueue(JT.DEFAULT_QUEUE)
    if args.cmd == "collect":
        res = JT.collect_from_run(pathlib.Path(args.run), q,
                                  with_response=not args.no_response)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "collect-flows":
        res = JT.collect_from_flows_jsonl(pathlib.Path(args.file), q)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "report":
        print(json.dumps(JT.report(q), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "collect-hunt":
        res = JT.collect_from_hunt(pathlib.Path(args.result), q)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "pairs":
        print(json.dumps(JT.pairing_report(q), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "approve-all":
        rows = q.load()
        targets = []
        for r in rows:
            if r.get("review_state") != "pending":
                continue
            if args.only_disagreements and not (r.get("agreement") is False):
                continue
            targets.append((r["sample_id"], int(r.get("label_revision", 0))))
        for sid, revision in targets:
            q.approve(sid, reviewer=args.reviewer, note="jevtrain approve-all",
                      expected_label_revision=revision)
        print(json.dumps({"approved": len(targets), "reviewer": args.reviewer,
                          "sample_ids": [sid for sid, _ in targets]}, ensure_ascii=False))
        return 0
    out = pathlib.Path(args.out) if args.out else None
    m = JT.export(q, args.version, out_root=out)
    print(json.dumps(m, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

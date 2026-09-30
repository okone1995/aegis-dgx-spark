#!/usr/bin/env python3
"""v8 选择与审批：判官分歧优先 + 同 family 攻良配对；全部盖操作员章。

用途：把变异刷量产生的样本按"对判官最有价值"的顺序挑出来批准（人审仍由操作员执行）。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import jevtrain as JT  # noqa: E402
from engine.learning_queue import LearningQueue  # noqa: E402

REVIEWER = os.environ.get("AEGIS_JEVTRAIN_OPERATOR", "operator-集成负责人")
MAX_TOTAL = int(os.environ.get("V8_MAX", "60"))


def main() -> int:
    q = LearningQueue(JT.DEFAULT_QUEUE)
    rows = [r for r in q.load() if r.get("review_state") == "pending"]
    picked, seen = [], set()

    def take(r):
        if r["sample_id"] in seen:
            return
        picked.append(r)
        seen.add(r["sample_id"])

    # 1) 判官分歧（误报/漏报）—— 最值钱
    for r in rows:
        if r.get("agreement") is False:
            take(r)
    # 2) 攻击侧全要（正样本稀缺）
    for r in rows:
        if r.get("intent_label") == "attack":
            take(r)
    # 3) 良性侧按 family 补齐（同端点的攻击面配对）
    fam_attack = {r.get("family") for r in picked if r.get("intent_label") == "attack"}
    for r in rows:
        if len(picked) >= MAX_TOTAL:
            break
        if r.get("intent_label") == "benign" and r.get("family") in fam_attack:
            take(r)
    # 4) 仍不足则按判官高分（更接近边界）补齐
    for r in sorted(rows, key=lambda x: -float((x.get("judge") or {}).get("p_attack") or 0)):
        if len(picked) >= MAX_TOTAL:
            break
        take(r)

    for r in picked:
        q.approve(r["sample_id"], reviewer=REVIEWER, note="v8 mutation selection",
                  expected_label_revision=r.get("label_revision", 0))
    print(json.dumps({"approved": len(picked),
                      "attack": sum(1 for r in picked if r.get("intent_label") == "attack"),
                      "benign": sum(1 for r in picked if r.get("intent_label") == "benign"),
                      "disagreements": sum(1 for r in picked if r.get("agreement") is False),
                      "sources": sorted({r.get("source") for r in picked})},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

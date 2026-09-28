"""诊断：plan_free 在真机环境下 3 发全被"语法闸"拒，模型到底吐了什么形态。"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from engine import attacker  # noqa: E402

plan = attacker.plan_free(
    "sqli", [],
    "确定性阶梯跑完未命中；端点 /reports/exception-queue 参数 unconfigured_filter，请给菜单外的新形态",
    n=3)
print("accepted =", len(plan.get("accepted") or []), "| rejected =", len(plan.get("rejected") or []))
for i, r in enumerate(plan.get("rejected") or []):
    raw = str(r.get("req") or "")
    print(f"--- rejected[{i}] why={str(r.get('why_rejected'))[:80]}")
    print("    前 200 字:", raw[:200].replace("\n", "\\n"))
for i, a in enumerate(plan.get("accepted") or []):
    print(f"--- accepted[{i}] 前 160 字:", str(a.get("text"))[:160].replace("\n", "\\n"))

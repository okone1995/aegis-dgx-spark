#!/usr/bin/env python3
"""变异刷量 CLI：把 hunt 阶梯的母弹 × 变换菜单跑一遍并入库（逐条真调判官）。

用法:
  python tools/mutate_collect.py --hunt-result workspace/runs/_hunts/hunt_1.json \
      [--library] [--class sqli] [--budget 150] [--scope <scope.json>]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import mutate as MT  # noqa: E402
from engine.target_profile import active as active_profile  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hunt-result", default=None)
    ap.add_argument("--library", action="store_true", help="把目标库内载荷也当母弹")
    ap.add_argument("--class", dest="cls", default="sqli")
    ap.add_argument("--instance", default="a")
    ap.add_argument("--budget", type=int, default=150)
    ap.add_argument("--scope", default=None)
    a = ap.parse_args()

    prof = active_profile()
    base = prof.instance(a.instance).base
    parents = []
    if a.hunt_result:
        parents += MT.parents_from_hunt(pathlib.Path(a.hunt_result))
    if a.library or not parents:
        parents += MT.parents_from_library(prof, a.cls)
    if not parents:
        print(json.dumps({"status": "error", "code": "no_parents"}, ensure_ascii=False))
        return 2
    scope = None
    if a.scope:
        from engine.scope import Scope
        scope = Scope(**json.loads(pathlib.Path(a.scope).read_text(encoding="utf-8")))
    res = MT.mutate_round(base=base, cls=a.cls, parents=parents, instance=a.instance,
                          profile=prof, budget_requests=a.budget, scope=scope)
    brief = {k: res[k] for k in ("status", "class", "menu", "parents", "fired", "added",
                                 "duplicates", "hits", "policy_refused",
                                 "transform_not_applicable", "budget_requests")}
    print(json.dumps(brief, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

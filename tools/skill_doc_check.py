#!/usr/bin/env python3
"""技能文档 → 引擎符号一致性检查（把文档债变成可执行验收）。

扫三类引用是否真实存在：
  1) `engine.<mod>` / `engine.<mod>.<attr>`：模块可导入且属性存在；
  2) `profile.<attr>`：出现在 engine/target_profile.py 里；
  3) `AEGIS_*`：引擎或 tools 里确实有地方读它。

为什么需要：`php -l`、`judge v2` 这种陈旧引用不是语法错，py_compile 抓不到；
只有真跑或人眼能发现。本工具把它变成一条可跑、可回归的检查。
用法: python tools/skill_doc_check.py [--skills-dir skills] [--engine-root .]
"""
from __future__ import annotations

import argparse
import importlib
import pathlib
import re
import sys

REF_ENGINE = re.compile(r"`engine\.([a-z_][a-z0-9_]*)(?:\.([a-z_][a-z0-9_]*))?")
REF_PROFILE = re.compile(r"`profile\.([a-z_][a-z0-9_]*)")
REF_ENV = re.compile(r"`?(AEGIS_[A-Z0-9_]+)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skills-dir", default="skills")
    ap.add_argument("--engine-root", default=".")
    ap.add_argument("--also-read", default=None, action="append",
                    help="额外的读取面目录（例如另一 checkout 的 skills/），可重复；"
                         "用于消除 变量由别的 checkout 的技能读 这类假阳性")
    a = ap.parse_args()
    root = pathlib.Path(a.engine_root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    src = ""
    if (root / "engine").is_dir():
        src += "\n".join(p.read_text(encoding="utf-8", errors="replace")
                         for p in (root / "engine").glob("*.py"))
    if (root / "tools").is_dir():
        src += "\n".join(p.read_text(encoding="utf-8", errors="replace")
                         for p in (root / "tools").glob("*.py"))
    # 技能的桥接脚本也是"读环境变量的合法地方"（例如 AEGIS_CONSOLE_ORIGIN 由
    # aegis-self-repair 的桥接读，而不是引擎读）——不把它们算进来会全是假阳性。
    roots = [pathlib.Path.cwd()] + [pathlib.Path(x) for x in (a.also_read or []) if x]
    for r in roots:
        for sd in r.glob("skills/*/scripts/*.py"):
            src += sd.read_text(encoding="utf-8", errors="replace")
    prof = root / "engine" / "target_profile.py"
    prof_src = prof.read_text(encoding="utf-8") if prof.is_file() else ""

    skills = sorted(p for p in pathlib.Path(a.skills_dir).iterdir()
                    if (p / "SKILL.md").is_file())
    problems = []
    for sk in skills:
        text = (sk / "SKILL.md").read_text(encoding="utf-8")
        for mod, attr in sorted(set(REF_ENGINE.findall(text))):
            try:
                m = importlib.import_module(f"engine.{mod}")
            except Exception as exc:  # noqa: BLE001
                problems.append((sk.name, f"engine.{mod}", f"模块不可导入 {type(exc).__name__}"))
                continue
            if attr and not hasattr(m, attr):
                problems.append((sk.name, f"engine.{mod}.{attr}", "属性不存在"))
        for attr in sorted(set(REF_PROFILE.findall(text))):
            if attr not in prof_src:
                problems.append((sk.name, f"profile.{attr}", "profile 加载器里没有该字段"))
        for env in sorted(set(REF_ENV.findall(text))):
            if env not in src:
                problems.append((sk.name, env, "引擎/tools 里没有任何地方读它"))

    print(f"扫描 {len(skills)} 个技能文档 | 引擎根 {root} | 读取面包含 engine+tools+skills/scripts")
    if not problems:
        print("OK: 无未解析引用")
        return 0
    print(f"未解析引用 {len(problems)} 处：")
    for sk, ref, why in problems:
        print(f"  [{sk}] {ref} — {why}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

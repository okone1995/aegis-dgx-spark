"""禁用措辞 lint（评审 P0 的制度化）：降级后的口径不许被写回成过度宣称。

用法: python bench/train/lint_language.py [文件...]   # 缺省扫对外材料全集
命中即退出码 1。词表与 EVIDENCE.md《我们不说什么》一节同源，改一处必须同步。
"""
from __future__ import annotations
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BANNED = [
    r"未见过的攻击", r"新载荷泛化", r"字面全未见", r"成绩不来自背题",
    r"零交集", r"内存无漂移", r"无内存泄漏", r"无漂移", r"未见载荷泛化",
    r"\bTBD\b", r"\bTODO\b", r"100%\s*泛化", r"永不", r"绝对安全",
]
# 只扫**对外材料**：内部工程记录允许出现"初版误写/作废/恒等式"这类自曝纠错句，
# 那是审计痕迹；对外材料一个字都不许多说。--all 可临时扩扫内部文档作参考。
DEFAULT_TARGETS = ["README.md", "EVIDENCE.md", "JUDGES.md"]
EXTRA_OUTWARD = ["deck", "video", "十日谈", "essay"]   # 目录/文件名含这些词也纳入


def _outward_all():
    ts = list(DEFAULT_TARGETS)
    for p in ROOT.rglob("*.md"):
        rel = str(p.relative_to(ROOT))
        if any(k in rel.lower() for k in EXTRA_OUTWARD) and rel not in ts:
            ts.append(rel)
    return ts


def main() -> int:
    if "--all" in sys.argv[1:]:
        args = [x for x in sys.argv[1:] if x != "--all"] or _outward_all() + DEFAULT_TARGETS[2:]
    else:
        args = sys.argv[1:] or _outward_all()
    paths = [pathlib.Path(x) for x in args]
    hits = []
    for p in paths:
        p = p if p.is_absolute() else ROOT / p
        if not p.is_file():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for pat in BANNED:
                # 引用式提及（禁用清单本身、"初版误写…"这类自曝）放行
                # 否定语境放行：《我们不说什么》一节必须引用禁语才能禁止它，
                # 这是制度化的自曝，不是过度宣称。
                if re.search(pat, line) and not re.search(
                        r"禁用|不许|严禁|禁止|不得|不主张|不说|不含|不带|不作|作废|误写|已归档|反面教材|永不同句|不为|非宣称", line):
                    hits.append(f"{p.relative_to(ROOT)}:{i}: [{pat}] {line.strip()[:70]}")
    if hits:
        print("禁用措辞命中：")
        print("\n".join(hits))
        return 1
    print(f"禁用措辞零命中（扫 {len(paths)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

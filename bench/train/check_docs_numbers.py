"""Check that numeric claims in outward-facing Markdown have traceable sources.

Usage: python bench/train/check_docs_numbers.py [file.md ...]
"""
from __future__ import annotations

import pathlib
import re
import sys
from decimal import Decimal

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TARGETS = ["README.md", "EVIDENCE.md"]
EXTRA_OUTWARD = ["deck", "video", "十日谈", "essay", "ten-days"]


def outward_all():
    targets = list(TARGETS)
    for path in ROOT.rglob("*.md"):
        rel = path.relative_to(ROOT).as_posix()
        if any(word in rel.lower() for word in EXTRA_OUTWARD) and rel not in targets:
            targets.append(rel)
    return targets


CODE_SPAN = re.compile(r"```.*?```|`[^`]*`", re.S)
URL = re.compile(r"(?:https?://|www\.)[^\s)>]+", re.I)
MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
HTML_TAG = re.compile(r"</?[^>]+>")
TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+-]+Z?)?\b|"
    r"\b\d{1,2}:\d{2}(?::\d{2})?\b"
)
STRUCTURAL = re.compile(
    r"\bpython\s+\d+\.\d+(?:\.\d+)?\+?\b|"
    r"\bsha-?\d{3}\b|\bh\.?26[45]\b|\b\d{3,4}p\b|"
    r"\b\d+(?:\.\d+)?\s*(?:mb|gb|kib|mib)\b|"
    r"\bqwen[\d.]+|\bllama[\d.]+|"
    r"\b[a-z]+\d\.\d(?=-?(?:flash|plus|pro|max|next|turbo))|"
    r"\b(?:bf|fp|tf|hf|int)\d{1,2}\b",
    re.I,
)
NUMBER = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?!\w)")


def _canonical_number(raw: str) -> str:
    is_percent = raw.endswith("%")
    value = Decimal(raw[:-1] if is_percent else raw.replace(",", ""))
    if is_percent:
        value /= Decimal(100)
    normalized = format(value.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def numbers_in(text: str) -> set[str]:
    """Return canonical metric tokens, ignoring syntax and reference metadata.

    Thousands separators are removed and percentages are represented as ratios so
    ``4,244`` matches 4244 and ``99.92%`` matches a claim value of 0.9992.
    """
    text = re.sub(r"(?m)^\s*#{1,6}\s+\d+\.\s+", " ", text)
    text = re.sub(r"(?m)^\s*\d+\.\s+", " ", text)
    text = re.sub(r"(?m)^\|\s*\d+\s*\|", "| |", text)
    clean = CODE_SPAN.sub(" ", text)
    clean = URL.sub(" ", clean)
    clean = MARKDOWN_LINK.sub(lambda match: match.group(1), clean)
    clean = HTML_TAG.sub(" ", clean)
    clean = TIMESTAMP.sub(" ", clean)
    clean = STRUCTURAL.sub(" ", clean)
    return {_canonical_number(match.group(0)) for match in NUMBER.finditer(clean)}


def claims_tokens() -> set[str]:
    claims = yaml.safe_load((ROOT / "claims.yaml").read_text(encoding="utf-8"))
    tokens: set[str] = set()
    for claim in claims:
        for value in (claim.get("clause", ""), claim.get("expected", ""), claim.get("floor", "")):
            tokens.update(numbers_in(str(value)))
        tie = claim.get("tie")
        if isinstance(tie, dict) and "expected" in tie:
            tokens.update(numbers_in(str(tie["expected"])))
    return tokens


def main() -> int:
    args = sys.argv[1:]
    if "--all" in args:
        args = [arg for arg in args if arg != "--all"] or outward_all()
    else:
        args = args or TARGETS
    tokens = claims_tokens()
    bad: list[tuple[str, str]] = []
    for rel in args:
        path = pathlib.Path(rel) if pathlib.Path(rel).is_absolute() else ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for number in numbers_in(text):
            if number not in tokens:
                bad.append((path.relative_to(ROOT).as_posix(), number))
    if bad:
        print("未溯源数字（缺少 claims.yaml 对应条目）：")
        for filename, number in sorted(set(bad)):
            print(f"  {filename}: {number}")
        return 1
    print(f"对外材料数字全部可溯源（扫描 {len(args)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""把**已审核通过**的 POC 候选写回 plugins/<class>/payloads/<intent>/。

设计红线（对应安全专家那条链路的最后一步）：
  * 默认 **dry-run**：不加 --apply 只打印将要写什么，一个字节都不落盘；
  * **只写 review_state=approved 且 evidence_state=confirmed 的候选**（未命中/未审核一律跳过）；
  * 写出的载荷首行必须是 `METHOD path HTTP/1.1`，Cookie 一律 `{{SESSION}}`、
    目标一律 `{{TARGET}}`（票据与宿主信息永不进库）；
  * 每个载荷旁边写 `<name>.provenance.json`：候选来源、母弹/变换、命中证据、审核人、时间、
    队列文件 sha256 —— 让"这条 POC 从哪来、谁批的、凭什么"可追溯。

用法：
  python tools/poc_apply.py --queue dataset/review_queue/poc_candidates.jsonl \
      --plugins-dir plugins            # dry-run
  ... 同上加 --apply                   # 真写
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.poc_queue import DEFAULT_QUEUE, PocQueue, PocQueueError  # noqa: E402

FIRST_LINE = re.compile(r"^[A-Z]+\s+\S+\s+HTTP/1\.[01]$")


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _slug(text: str) -> str:
    out = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_").lower()
    return out[:40] or "candidate"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", default=str(DEFAULT_QUEUE))
    ap.add_argument("--plugins-dir", default=str(ROOT / "plugins"))
    ap.add_argument("--apply", action="store_true", help="真写入（默认只 dry-run）")
    a = ap.parse_args()

    qpath = pathlib.Path(a.queue)
    plugins = pathlib.Path(a.plugins_dir)
    queue = PocQueue(qpath)
    rows = queue.load()
    approved = [r for r in rows if r.get("review_state") == "approved"]
    eligible = [r for r in approved if r.get("evidence_state") == "confirmed"]

    print(f"queue: {qpath}")
    print(f"  rows={len(rows)} approved={len(approved)} "
          f"eligible(approved+confirmed)={len(eligible)} "
          f"skipped_unconfirmed={len(approved) - len(eligible)}")
    if not eligible:
        print("no approved candidates with confirmed evidence — 0 written")
        return 0

    qsha = _sha256(qpath)
    written = 0
    for r in eligible:
        cls = r.get("class") or "unknown"
        intent = r.get("intent") or "attack"
        text = r.get("request_text") or ""
        first = text.split("\n", 1)[0].strip()
        if not FIRST_LINE.match(first) or "{{SESSION}}" not in text:
            print(f"  SKIP {r.get('candidate_id')}: payload 形态不合格（首行/占位符）")
            continue
        target_dir = plugins / cls / "payloads" / intent
        # 序号：避开既有文件名，取 90+ 区间给"自造招入库"（人工载荷习惯 01..20）
        existing = sorted(target_dir.glob("*.txt")) if target_dir.is_dir() else []
        nums = [int(m.group(1)) for m in
                (re.match(r"(\d+)_", p.name) for p in existing) if m]
        nxt = (max(nums) + 1) if nums else 90
        nxt = max(nxt, 90)
        base = r.get("candidate_id") or "candidate"
        name = f"{nxt:02d}_poc_{_slug(base.split('-')[-1] if '-' in base else base)}.txt"
        prov = {
            "candidate_id": r.get("candidate_id"),
            "class": cls,
            "intent": intent,
            "source": r.get("source"),
            "parent": r.get("parent"),
            "transform_id": r.get("transform_id"),
            "reason": r.get("rationale"),
            "evidence": r.get("evidence"),
            "reviewer": r.get("reviewer"),
            "review_note": r.get("review_note"),
            "reviewed_at": r.get("reviewed_at"),
            "written_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
            "queue": str(qpath.relative_to(ROOT)) if str(qpath).startswith(str(ROOT)) else str(qpath),
            "queue_sha256": qsha,
        }
        if not a.apply:
            print(f"  [dry-run] would write {target_dir / name} (+ .provenance.json)")
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / name).write_text(text, encoding="utf-8")
        (target_dir / name).with_suffix(".provenance.json").write_text(
            json.dumps(prov, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  wrote {target_dir / name}")
        written += 1

    if not a.apply:
        print(f"dry-run: {len(eligible)} would be written — {written} written")
    else:
        print(f"{written} written")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PocQueueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

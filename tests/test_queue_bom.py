"""BOM 队列可读性单测（Windows 操作员用 Set-Content -Encoding UTF8 写出来的队列带 BOM）。"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.learning_queue import LearningQueue  # noqa: E402


def _row(i: int) -> dict:
    return {"sample_id": f"bomtest{i:04d}-{i:04d}", "path": "/p", "method": "GET",
            "status": 200, "response": "ok", "intent_label": "benign",
            "family": "GET /p", "review_state": "pending", "source": "bomtest"}


def test_queue_written_with_bom_is_readable(tmp_path):
    qf = tmp_path / "bom.jsonl"
    payload = "".join(json.dumps(_row(i), ensure_ascii=False) + "\n" for i in range(3))
    qf.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))     # 模拟 PowerShell 的 BOM
    q = LearningQueue(qf)
    rows = q.load()
    assert len(rows) == 3, rows
    assert rows[0]["sample_id"] == "bomtest0000-0000"


def test_queue_written_without_bom_still_works(tmp_path):
    qf = tmp_path / "plain.jsonl"
    qf.write_text("".join(json.dumps(_row(i), ensure_ascii=False) + "\n" for i in range(2)),
                  encoding="utf-8")
    assert len(LearningQueue(qf).load()) == 2

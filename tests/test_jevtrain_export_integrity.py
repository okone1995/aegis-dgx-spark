"""导出物自检的单测：可追溯 + 切分独立性 + holdout 单侧缺标签。

不联网：直接写一份 approved 队列 JSONL（同一家族的攻良对），跑 export，检查 MANIFEST 的自检块与告警。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import jevtrain as JT  # noqa: E402
from engine.learning_queue import LearningQueue  # noqa: E402

FAM_PATH = "/reports/exception-queue"


def _row(i: int, label: str) -> dict:
    sid = f"accepttest{i:04d}-{i:04d}"          # 19 位风格
    return {
        "sample_id": sid, "run": "accepttest", "flow_id": f"f{i}",
        "method": "GET", "path": FAM_PATH, "body": "",
        "status": 200,
        "response": "SQL syntax error near 'UNION'" if label == "attack" else "ok",
        "intent_label": label, "input_mode": "exchange",
        "truth_reason": "marker_hit" if label == "attack" else "attack_shaped_but_no_marker",
        "family": f"GET {FAM_PATH}",
        "judge": {"verdict": "benign", "p_attack": 0.1},
        "agreement": (label == "benign"),
        "source": "accepttest", "collected_at": "2026-09-27T00:00:00Z",
        "review_state": "approved",
        "reviewed_at": "2026-09-27T01:00:00Z", "reviewer": "unit-test",
    }


def test_export_reports_traceability_and_split_independence(tmp_path):
    qf = tmp_path / "q.jsonl"
    rows = [_row(i, "attack" if i % 2 == 0 else "benign") for i in range(40)]
    qf.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    q = LearningQueue(qf)
    m = JT.export(q, "accepttest", out_root=tmp_path / "out")

    # 1) 可追溯：每条导出行都能用 meta.queue_sample_id 回到队列（0 未解析）
    tr = m["traceability"]
    assert tr["exported_rows"] == m["rows"]["train"] + m["rows"]["holdout"]
    assert tr["unresolved"] == 0, tr
    assert tr["resolved_to_queue"] == tr["exported_rows"]

    # 2) 切分独立性：本样本全在同一家族 ⇒ 若 holdout 非空则必然共族（必须报），
    #    若 holdout 为空（哈希切分在样本少时会这样）也必须告警——两种情况都不许静默
    si = m["split_independence"]
    hl = si["holdout_labels"]
    if m["rows"]["holdout"] == 0:
        assert any("holdout 单侧缺标签" in w for w in m["balance_warnings"]), m["balance_warnings"]
    else:
        assert si["shared_families"] >= 1, si
        assert any("切分不独立" in w for w in m["balance_warnings"]), m["balance_warnings"]
        if hl.get("attack", 0) == 0 or hl.get("benign", 0) == 0:
            assert any("holdout 单侧缺标签" in w for w in m["balance_warnings"]), m["balance_warnings"]

    # 4) 单侧标签与不平衡告警仍然在（原有能力不许被这次改动吞掉）
    assert isinstance(m["balance_warnings"], list) and m["balance_warnings"]
    print("warnings:", json.dumps(m["balance_warnings"], ensure_ascii=False))
    print("holdout_labels:", hl)

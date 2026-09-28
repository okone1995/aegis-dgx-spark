# -*- coding: utf-8 -*-
"""T5 测试:候选学习队列(去重/脱敏/家族查表/B 拒/审核状态机)。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import learning_queue as LQ


def _flows():
    return [
        {"flow_id": "FL-0001", "method": "GET", "path": "/news?q=%E5%9B%BE%E4%B9%A6%E9%A6%86",
         "body": "", "status": 200, "response": "期中考试通知"},
        {"flow_id": "FL-0002", "method": "GET",
         "path": "/news?q=1%27%20UNION%20SELECT%20username%20FROM%20users--",
         "body": "", "status": 200, "response": "admin123"},
    ]


def _intent():
    return {
        "FL-0001": {"intent_label": "benign", "outcome": "legitimate_success",
                    "label_source": "case_intent", "evidence_refs": ["business.checked"]},
        "FL-0002": {"intent_label": "attack", "outcome": "exploited",
                    "label_source": "case_intent", "evidence_refs": ["flow:FL-0002"]},
    }


def test_from_flows_schema_complete():
    rows = LQ.from_flows("run-x", _flows(), _intent())
    assert len(rows) == 2
    need = {"sample_id", "run_id", "flow_id", "finding_id", "replay_of",
            "captured_at", "input_hash", "family_id", "input_mode",
            "intent_label", "outcome", "label_source", "evidence_refs",
            "scrub_version", "review_state", "split_assignment",
            "dataset_version", "exclusion_reason"}
    assert need <= set(rows[0].keys())
    assert rows[1]["intent_label"] == "attack"
    assert rows[0]["family_id"].startswith("REQ:")
    assert rows[0]["scrub_version"].startswith("v2:")


def test_input_hash_label_insensitive():
    a = LQ.input_hash("GET", "/news?q=1", "")
    b = LQ.input_hash("GET", "/news?q=1", "")
    assert a == b
    # 同一请求不同 intent_label → 同 input_hash(label 无关)
    r1 = LQ.from_flows("run-1", _flows()[:1], {"FL-0001": {"intent_label": "benign"}})
    r2 = LQ.from_flows("run-2", _flows()[:1], {"FL-0001": {"intent_label": "attack"}})
    assert r1[0]["input_hash"] == r2[0]["input_hash"]


def test_append_dedupes(tmp_path):
    q = LQ.LearningQueue(tmp_path / "candidates.jsonl")
    row = LQ.from_flows("run-1", _flows(), _intent())[0]
    assert q.append(row)["duplicate"] is False
    again = q.append(dict(row))
    assert again["duplicate"] is True
    assert q.stats()["total"] == 1


def test_scrub_v2_covers_cookie_and_windows_path():
    body = ("Cookie: PHPSESSID=abc123; path=[path] "
            "Bearer sk-abcdef123456")
    out = LQ.scrub_v2(body)
    # 安全性质:类敏感值一律不再出现(具体标记形态取决于规则命中顺序——
    # Cookie 是全行级规则,会连同其后的路径一起吃掉)
    assert "abc123" not in out
    assert "sk-abcdef123456" not in out
    assert "[SCRUB]" in out
    # 独立验证 Windows 路径规则(单独输入,不被 Cookie 规则遮蔽)
    p = LQ.scrub_v2("open C:\\Users\\<user>\\secret\\app.php failed")
    assert "<user>" not in p and "[PATH]" in p


def test_b_set_hard_rejected():
    flows = [{"flow_id": "FL-0009", "method": "GET",
              "path": "http://127.0.0.1:8082/news?q=x", "body": "",
              "status": 200, "response": ""}]
    row = LQ.from_flows("run-b", flows, {})[0]
    assert row["review_state"] == "rejected"
    assert row["exclusion_reason"] == "b_set_contamination"
    assert row["split_assignment"] is None


def test_lookup_split_from_sealed_volume():
    """已封卷家族查表:取 judge_v0 train.jsonl 里的真实家族,须回同一桶。"""
    f = ROOT / "dataset" / "judge_v0" / "train.jsonl"
    if not f.is_file():
        pytest.skip("judge_v0 卷不可用")
    fam = None
    with f.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            fam = (json.loads(line).get("meta") or {}).get("family")
            if fam:
                break
    assert fam, "卷内应有 family 字段"
    assert LQ.lookup_split(fam) == "train"
    assert LQ.lookup_split("REQ:neverseenfamily000") is None


def test_review_state_machine(tmp_path):
    q = LQ.LearningQueue(tmp_path / "candidates.jsonl")
    row = q.append(LQ.from_flows("run-1", _flows(), _intent())[0])["sample"]
    q.approve(row["sample_id"])
    with pytest.raises(ValueError):
        q.reject(row["sample_id"])  # 单向
    with pytest.raises(KeyError):
        q.approve("nonexistent")
    st = q.stats()
    assert st["approved"] == 1 and st["pending"] == 0


def test_b_sealed_family_ids_reads_sealed_volumes():
    """S5:B 留存家族台账(真实来源 = d7_b 卷;读不到如实记 missing)。"""
    seal = LQ.b_sealed_family_ids()
    assert isinstance(seal["families"], set)
    assert seal["sources"], "必须留下逐文件来源账"
    assert seal["loaded_families"] == len(seal["families"])
    assert {s["status"] for s in seal["sources"]} <= {"ok", "missing", "unreadable"}
    f = ROOT / "bench" / "train" / "results" / "d7_b" / "samples_b_clean.jsonl"
    if not f.is_file():
        pytest.skip("B 留存卷不可用(只验形状与来源账)")
    by_path = {s["path"]: s for s in seal["sources"]}
    assert by_path["bench/train/results/d7_b/samples_b_clean.jsonl"]["status"] == "ok"
    assert seal["loaded_families"] > 2000
    assert any("8082" in fam for fam in seal["families"])


def test_is_b_sealed_gate_matrix(monkeypatch):
    """B 闸三态:坐标命中 / 家族键命中 / 留存集命中;A 侧家族放行。"""
    a_fam = "DVWA:REQ:getaboutphp"          # A 侧真实家族
    assert LQ.is_b_sealed(a_fam, "/news?q=1") is None
    assert LQ.is_b_sealed(a_fam, "http://127.0.0.1:8082/news") == "b_path_marker"
    # 实测:全部 2327 个 B 家族键都含 "8082" ⇒ 走 hint 分支
    assert LQ.is_b_sealed("REQ:gethttp1270018082downloadpathx", "") == "b_family_hint"
    # 留存集分支:注入一个不含 8082 的留存家族键(隔离真实数据,验分支本身)
    monkeypatch.setattr(LQ, "_b_sealed_cache",
                        {"families": {"REQ:n0_marker_family"}, "sources": [],
                         "loaded_families": 1})
    assert LQ.is_b_sealed("REQ:n0_marker_family", "") == "b_sealed_family"
    assert LQ.is_b_sealed(a_fam, "") is None


def test_stats_counts_splits(tmp_path):
    q = LQ.LearningQueue(tmp_path / "candidates.jsonl")
    for row in LQ.from_flows("run-1", _flows(), _intent()):
        q.append(row)
    st = q.stats()
    assert st["total"] == 2
    assert st["pending"] == 2
    assert sum(st["by_split"].values()) <= 2

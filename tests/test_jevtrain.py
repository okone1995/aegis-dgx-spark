"""jevtrain 单测（T13）：守四条 —— 标签来自硬信号、B 集拒入、未审核不导出、已封家族不重切。"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import jevtrain as JT  # noqa: E402
from engine.learning_queue import LearningQueue  # noqa: E402


@pytest.fixture()
def q(tmp_path):
    return LearningQueue(tmp_path / "jevtrain.jsonl")


def _run_dir(tmp_path, flows, judges=()):
    d = tmp_path / "run-x"
    d.mkdir(parents=True, exist_ok=True)
    (d / "flow.jsonl").write_text(
        "".join(json.dumps(f, ensure_ascii=False) + "\n" for f in flows), encoding="utf-8")
    (d / "judge.jsonl").write_text(
        "".join(json.dumps(j, ensure_ascii=False) + "\n" for j in judges), encoding="utf-8")
    return d


FLOWS = [
    {"flow_id": "FL-001", "intent_label": "benign", "outcome": "legitimate_success",
     "method": "GET", "path": "/reports/exception-queue", "status": 200,
     "response": "ok Cookie: dtk=SECRET123", "markers_hit": []},
    {"flow_id": "FL-002", "intent_label": "attack", "outcome": "exploited",
     "method": "GET", "path": "/reports/exception-queue", "status": 200,
     "response": "CREATE TABLE api_registrations", "markers_hit": ["CREATE TABLE"]},
    {"flow_id": "FL-003", "intent_label": "attack", "outcome": "blocked",
     "method": "GET", "path": "/reports/exception-queue", "status": 200,
     "response": "empty", "markers_hit": []},
    {"flow_id": "FL-004", "intent_label": "attack", "outcome": "error",
     "method": "GET", "path": "/reports/exception-queue", "status": 0,
     "response": "", "error": "TimeoutError"},
]
JUDGES = [
    {"flow_id": "FL-001", "verdict": "benign", "p_attack": 0.001},
    {"flow_id": "FL-002", "verdict": "attack", "p_attack": 0.999},
    {"flow_id": "FL-003", "verdict": "attack", "p_attack": 0.98},   # 误报（真值 benign）
]


def test_truth_labels_come_from_hard_signals(q, tmp_path):
    res = JT.collect_from_run(_run_dir(tmp_path, FLOWS, JUDGES), q)
    assert res["added"] == 3                      # FL-004 打不出标签 ⇒ 不入队
    assert any(u["flow_id"] == "FL-004" for u in res["unlabelled"])
    rows = {r["flow_id"]: r for r in q.load()}
    assert rows["FL-002"]["intent_label"] == "attack"        # marker 命中 ⇒ attack
    assert rows["FL-003"]["intent_label"] == "attack"   # 攻击探针恒为 attack（无论是否打穿）        # 打不中 ⇒ benign（hard negative）
    assert rows["FL-003"]["truth_reason"].startswith("attack/")  # 记 outcome + needs_review
    # 判官读数只记录，不参与标签
    assert rows["FL-003"]["judge"]["verdict"] == "attack"
    assert rows["FL-003"]["intent_label"] == rows["FL-003"]["judge"]["verdict"]  # 撤销"误报"口径：判官判 attack 与真值一致


def test_cookies_are_scrubbed_before_queueing(q, tmp_path):
    JT.collect_from_run(_run_dir(tmp_path, FLOWS, JUDGES), q)
    blob = (tmp_path / "jevtrain.jsonl").read_text(encoding="utf-8")
    assert "dtk=SECRET123" not in blob
    assert "[SCRUB]" in blob


def test_report_splits_fp_and_fn(q, tmp_path):
    JT.collect_from_run(_run_dir(tmp_path, FLOWS, JUDGES), q)
    rep = JT.report(q)
    assert rep["by_truth"]["attack"] == 2 and rep["by_truth"]["benign"] == 1
    assert rep["judge_agreement"]["disagree"] == 0
    assert len(rep["false_positives"]) == 0 and rep["false_negatives"] == []  # 旧口径下的"误报"其实是真阳性


def test_b_sealed_flows_are_refused(q, tmp_path):
    flows = [dict(FLOWS[1], flow_id="FL-901", path="/x?target=http://127.0.0.1:8082/y")]
    res = JT.collect_from_run(_run_dir(tmp_path, flows), q)
    assert res["added"] == 0 and res["refused_b_sealed"][0]["reason"]


def test_export_requires_approval(q, tmp_path):
    JT.collect_from_run(_run_dir(tmp_path, FLOWS, JUDGES), q)
    with pytest.raises(JT.JevTrainError, match="没有 approved"):
        JT.export(q, "v9-draft", out_root=tmp_path / "out")


def test_export_writes_trainable_draft_with_provenance(q, tmp_path):
    JT.collect_from_run(_run_dir(tmp_path, FLOWS, JUDGES), q)
    for sid in [r["sample_id"] for r in q.load()]:
        q.approve(sid)
    out = tmp_path / "judge_v9"
    m = JT.export(q, "v9-draft", out_root=out)
    assert m["schema"] == "jevtrain-manifest-v1"
    assert m["rows"]["train"] + m["rows"]["holdout"] == 3
    assert "未经训练与评估" in m["note"]
    # 产物格式必须是训练流程吃的 messages/meta 形态
    first = json.loads((out / "train.jsonl").read_text(encoding="utf-8").splitlines()[0]
                       if (out / "train.jsonl").read_text(encoding="utf-8").strip() else "{}")
    for f in ("train.jsonl", "holdout.jsonl"):
        for line in (out / f).read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            assert "messages" in rec and "meta" in rec
            assert rec["meta"]["source"] == "jevtrain"
    assert (out / "MANIFEST.json").is_file() and (out / "TRAINING.md").is_file()
    assert "标签来自**硬信号**" in (out / "TRAINING.md").read_text(encoding="utf-8")


def test_split_is_stable_and_sealed_families_are_not_resplit(q, tmp_path, monkeypatch):
    """已封家族必须查表（不得重切）：这里把某家族伪装成已封，验证导出走查表值。"""
    JT.collect_from_run(_run_dir(tmp_path, FLOWS, JUDGES), q)
    for sid in [r["sample_id"] for r in q.load()]:
        q.approve(sid)
    monkeypatch.setattr(JT, "lookup_split", lambda fam: "holdout")
    m = JT.export(q, "v9b", out_root=tmp_path / "judge_v9b")
    assert m["rows"]["train"] == 0 and m["rows"]["holdout"] == 3

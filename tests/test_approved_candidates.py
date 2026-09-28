# -*- coding: utf-8 -*-
"""S5 测试:候选 → 人工审核 → 已批准数据集版本(硬拒/幂等 hash/切分账)。

纪律:不为了让测试变绿而放宽"只收 approved"——每条拒入断言都要求
`CandidateRejected` 真的抛出,而不是被静默过滤。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dataset"))

import approved_candidates as AC  # noqa: E402
from engine import learning_queue as LQ  # noqa: E402

KW = dict(reviewer="qa-reviewer", reviewed_at="2026-09-27T02:00:00Z",
          dataset_version="s5_demo_v1", train_device="NVIDIA GeForce RTX 5090 D",
          train_device_source="本机 nvidia-smi 实测 + .gitignore:13")


def _sealed_family(bucket):
    """取一份已封卷卷里的真实家族(顺带验证查表口径与卷一致)。"""
    f = ROOT / "dataset" / "judge_v0" / f"{bucket}.jsonl"
    if not f.is_file():
        pytest.skip(f"judge_v0/{bucket}.jsonl 不可用")
    with f.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            fam = (json.loads(line).get("meta") or {}).get("family")
            if fam:
                assert LQ.lookup_split(fam) == bucket, "已封卷家族须查表回同一桶"
                return fam
    pytest.skip(f"judge_v0/{bucket}.jsonl 无 family 字段")


def _b_family():
    f = ROOT / "bench" / "train" / "results" / "d7_b" / "samples_b_clean.jsonl"
    if not f.is_file():
        pytest.skip("B 留存卷不可用")
    with f.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                fam = (json.loads(line).get("family")
                       or (json.loads(line).get("meta") or {}).get("family"))
                if fam:
                    return fam
    pytest.skip("B 卷无 family 字段")


def _row(sid, family, split, **kw):
    row = {
        "sample_id": sid, "run_id": "run-s5-0001", "flow_id": "FL-0001",
        "finding_id": None, "replay_of": None,
        "captured_at": "2026-09-27T00:00:00.000Z",
        "input_hash": "ih-" + sid, "family_id": family, "input_mode": "exchange",
        "intent_label": "attack", "outcome": "exploited",
        "label_source": "case_intent", "evidence_refs": ["flow:FL-0001"],
        "scrub_version": LQ.SCRUB_VERSION, "review_state": "approved",
        "split_assignment": split, "dataset_version": None, "exclusion_reason": None,
    }
    row.update(kw)
    return row


def _reasons(exc):
    return dict(exc.value.defects)


# ---------- ① 只收 approved + 字段完整 ----------

def test_admits_only_approved_and_records_split():
    fam_t = _sealed_family("train")
    fam_h = _sealed_family("holdout")
    rows = [_row("s-0001", fam_t, LQ.lookup_split(fam_t)),
            _row("s-0002", fam_t, LQ.lookup_split(fam_t)),
            _row("s-0003", fam_h, LQ.lookup_split(fam_h))]
    m = AC.build_manifest(rows, **KW)
    assert m["counts"]["samples"] == 3
    assert m["counts"]["families"] == 2
    assert m["counts"]["by_split_samples"] == {"train": 2, "holdout": 1}
    assert m["counts"]["by_split_families"] == {"train": 1, "holdout": 1}
    assert m["family_split"][fam_t] == "train"
    assert m["family_split"][fam_h] == "holdout"
    assert set(m["split_source"].values()) == {"sealed_volume_lookup"}
    assert m["sample_ids"] == ["s-0001", "s-0002", "s-0003"]
    assert m["run_ids"] == ["run-s5-0001"]
    assert m["scrub_versions"] == [LQ.SCRUB_VERSION]


def test_pending_rejected_and_b_contaminated_hard_rejected():
    fam = _sealed_family("train")
    sp = LQ.lookup_split(fam)
    rows = [_row("ok-1", fam, sp),
            _row("pend-1", fam, sp, review_state="pending"),
            _row("rej-1", fam, sp, review_state="rejected"),
            _row("contam-1", fam, sp, exclusion_reason="b_set_contamination")]
    with pytest.raises(AC.CandidateRejected) as e:
        AC.build_manifest(rows, **KW)
    rs = _reasons(e)
    assert rs["pend-1"] == "review_state='pending'"
    assert rs["rej-1"] == "review_state='rejected'"
    assert rs["contam-1"] == "exclusion_reason=b_set_contamination"
    assert "ok-1" not in rs


def test_duplicate_hard_rejected():
    fam = _sealed_family("train")
    sp = LQ.lookup_split(fam)
    rows = [_row("d-1", fam, sp),
            _row("d-2", fam, sp, exclusion_reason="duplicate"),
            _row("d-3", fam, sp, input_hash="ih-d-1")]
    with pytest.raises(AC.CandidateRejected) as e:
        AC.build_manifest(rows, **KW)
    rs = _reasons(e)
    assert rs["d-2"] == "exclusion_reason=duplicate"
    assert rs["d-3"].startswith("duplicate_within_input")


def test_b_sealed_family_and_path_marker_hard_rejected():
    fam = _sealed_family("train")
    rows = [_row("b-1", _b_family(), "holdout"),
            _row("b-2", fam, "train",
                 path="http://127.0.0.1:8082/news?q=x")]
    with pytest.raises(AC.CandidateRejected) as e:
        AC.build_manifest(rows, **KW)
    rs = _reasons(e)
    assert rs["b-1"].startswith("b_")          # 留存卷同族 / 家族键命中 8082
    assert rs["b-2"] == "b_path_marker"
    # 记账模式也在账上留痕,不是静默丢弃
    m = AC.build_manifest([dict(r) for r in rows], strict=False, **KW)
    assert m["counts"]["samples"] == 0
    assert {r["sample_id"] for r in m["b_seal"]["rejected_rows"]} == {"b-1", "b-2"}


def test_missing_or_stale_fields_hard_rejected():
    fam = _sealed_family("train")
    sp = LQ.lookup_split(fam)
    rows = [_row("m-1", fam, sp, run_id=None),
            _row("m-2", fam, sp, family_id=None),
            _row("m-3", fam, sp, input_hash=None),
            _row("m-4", fam, None),
            _row("m-5", fam, sp, scrub_version=None),
            _row("m-6", fam, sp, scrub_version="v2:deadbeef")]
    with pytest.raises(AC.CandidateRejected) as e:
        AC.build_manifest(rows, **KW)
    rs = _reasons(e)
    assert rs["m-1"] == "missing_run_id"
    assert rs["m-2"] == "missing_family_id"
    assert rs["m-3"] == "missing_input_hash"
    assert rs["m-4"] == "missing_split_assignment"
    assert rs["m-5"] == "missing_scrub_version"
    assert rs["m-6"] == "scrub_version_stale=v2:deadbeef"


def test_family_split_conflict_rejected():
    """同族不得两桶(跨切分泄漏)。"""
    fam = _sealed_family("train")
    rows = [_row("c-1", fam, "train"), _row("c-2", fam, "holdout")]
    with pytest.raises(AC.CandidateRejected) as e:
        AC.build_manifest(rows, **KW)
    assert _reasons(e)["c-2"].startswith("family_split_conflict")


def test_unsealed_family_needs_human_override():
    fam = "REQ:neverapprovedfamily0001"
    assert LQ.lookup_split(fam) is None
    with pytest.raises(AC.CandidateRejected) as e:
        AC.build_manifest([_row("o-1", fam, None)], **KW)
    assert _reasons(e)["o-1"] == "missing_split_assignment"
    m = AC.build_manifest([_row("o-1", fam, None)],
                          split_overrides={fam: "holdout"}, **KW)
    assert m["family_split"][fam] == "holdout"
    assert m["split_source"][fam] == "human_override"
    with pytest.raises(AC.CandidateRejected) as e2:
        AC.build_manifest([_row("o-2", fam, None)],
                          split_overrides={fam: "test"}, **KW)
    assert _reasons(e2)["o-2"] == "invalid_split_assignment='test'"


# ---------- ② 不可变 manifest + 同输入同 hash ----------

def test_manifest_hash_stable_and_content_sensitive(tmp_path):
    fam = _sealed_family("train")
    sp = LQ.lookup_split(fam)
    rows = [_row("h-1", fam, sp), _row("h-2", fam, sp)]
    m1 = AC.build_manifest([dict(r) for r in rows],
                           built_at="2026-09-27T02:01:00Z", **KW)
    m2 = AC.build_manifest([dict(r) for r in rows],
                           built_at="2026-09-27T23:59:59Z", **KW)
    assert m1["manifest_hash"] == m2["manifest_hash"], "时钟字段不得进 hash"
    assert m1["dataset_hash"] == m2["dataset_hash"]
    assert m1["manifest_hash"].startswith("sha256:")
    # hash 由内容决定:少一条样本即换 hash(不是常量)
    m3 = AC.build_manifest([dict(rows[0])], **KW)
    assert m3["manifest_hash"] != m1["manifest_hash"]
    assert m3["dataset_hash"] != m1["dataset_hash"]
    # 反复落盘同内容 → 幂等,不留第二个版本
    p = tmp_path / "manifest.json"
    AC.write_manifest(m1, p)
    first = p.read_text(encoding="utf-8")
    AC.write_manifest(json.loads(first), p)
    assert p.read_text(encoding="utf-8") == first


def test_write_manifest_refuses_conflicting_overwrite(tmp_path):
    fam = _sealed_family("train")
    sp = LQ.lookup_split(fam)
    m1 = AC.build_manifest([_row("w-1", fam, sp)], **KW)
    m2 = AC.build_manifest([_row("w-1", fam, sp), _row("w-2", fam, sp)], **KW)
    p = tmp_path / "manifest.json"
    AC.write_manifest(m1, p)
    with pytest.raises(AC.ManifestConflict):
        AC.write_manifest(m2, p)
    assert json.loads(p.read_text(encoding="utf-8"))["manifest_hash"] == m1["manifest_hash"]


def test_manifest_fields_complete_and_ready_only():
    fam = _sealed_family("train")
    sp = LQ.lookup_split(fam)
    m = AC.build_manifest(
        [_row("f-1", fam, sp)], **KW,
        adapter_paths=["bench/train/out/judge_v3_mix_lora"],
        eval_paths=["bench/results/three_arm/v3_mixed.json"])
    for k in ("schema_version", "kind", "status", "status_note", "release",
              "dataset_version", "built_at", "reviewer", "reviewed_at",
              "sample_ids", "run_ids", "flow_ids", "scrub_versions",
              "family_split", "split_source", "counts", "dataset_hash",
              "dataset_hash_basis", "train_device", "train_device_source",
              "adapter_paths", "eval_paths", "input_queue_paths", "b_seal",
              "rejected", "hash_excludes", "manifest_hash"):
        assert k in m, f"manifest 缺字段 {k}"
    assert m["status"] == AC.STATUS_READY == "candidates_ready"
    assert m["release"] == {"trained": False, "published": False}
    assert "built_at" in m["hash_excludes"]
    assert m["adapter_paths"] == ["bench/train/out/judge_v3_mix_lora"]
    text = json.dumps(m, ensure_ascii=False)
    for tok in ("已训练", "已发布"):
        assert tok not in text, f"本轮不得出现口径 {tok}"


def test_release_claim_guard_rejects_trained_or_published():
    fam = _sealed_family("train")
    m = AC.build_manifest([_row("g-1", fam, LQ.lookup_split(fam))], **KW)
    AC.assert_no_release_claim(m)                      # 自身合法
    bad_status = dict(m, status="已训练")
    with pytest.raises(ValueError):
        AC.assert_no_release_claim(bad_status)
    bad_note = dict(m, status_note="本轮已发布数据集 v1")
    with pytest.raises(ValueError):
        AC.assert_no_release_claim(bad_note)
    bad_release = dict(m, release={"trained": True, "published": False})
    with pytest.raises(ValueError):
        AC.assert_no_release_claim(bad_release)


# ---------- ③ 端到端:候选 → 审核 → manifest ----------

def test_end_to_end_queue_approve_then_manifest(tmp_path):
    q = LQ.LearningQueue(tmp_path / "learning-candidates.jsonl")
    flows = [
        {"flow_id": "FL-0001", "method": "GET", "path": "/news?q=1", "body": "",
         "status": 200, "response": "ok"},
        {"flow_id": "FL-0002", "method": "GET", "path": "/news?q=1%27%20OR%201=1--",
         "body": "", "status": 200, "response": "leak"},
    ]
    intent = {
        "FL-0001": {"intent_label": "benign", "outcome": "legitimate_success",
                    "label_source": "case_intent", "evidence_refs": ["business.checked"]},
        "FL-0002": {"intent_label": "attack", "outcome": "exploited",
                    "label_source": "case_intent", "evidence_refs": ["flow:FL-0002"]},
    }
    rows = LQ.from_flows("run-s5-e2e", flows, intent)
    ids = []
    for r in rows:
        res = q.append(r)
        assert res["duplicate"] is False
        ids.append(res["sample"]["sample_id"])
    q.approve(ids[0])
    q.reject(ids[1])
    fam0 = rows[0]["family_id"]
    assert fam0 != rows[1]["family_id"], "两条请求须属不同家族,便于分桶"
    # 队列里还留着被拒的那条 → 硬拒(不得静默只挑好的)
    with pytest.raises(AC.CandidateRejected) as e:
        AC.build_from_queues([q.path], split_overrides={fam0: "train"}, **KW)
    assert _reasons(e)[ids[1]] == "review_state='rejected'"
    # 记账模式:只登记已批准的那条
    m = AC.build_from_queues([q.path], split_overrides={fam0: "train"},
                             strict=False, **KW)
    assert m["counts"]["samples"] == 1
    assert m["sample_ids"] == [ids[0]]
    assert m["family_split"] == {fam0: "train"}
    assert {r["sample_id"] for r in m["rejected"]} == {ids[1]}
    assert m["input_queue_paths"] == [str(q.path)]
    # 同输入重建 → 同 hash(端到端幂等)
    m2 = AC.build_from_queues([q.path], split_overrides={fam0: "train"},
                              strict=False, **KW)
    assert m2["manifest_hash"] == m["manifest_hash"]


def test_unparseable_queue_line_is_hard_rejected(tmp_path):
    p = tmp_path / "learning-candidates.jsonl"
    fam = _sealed_family("train")
    good = _row("p-1", fam, LQ.lookup_split(fam))
    p.write_text(json.dumps(good, ensure_ascii=False) + "\n{not json\n",
                 encoding="utf-8")
    with pytest.raises(AC.CandidateRejected) as e:
        AC.build_from_queues([p], **KW)
    assert any(why == "unparseable_line" for _sid, why in e.value.defects)


def test_missing_queue_file_is_hard_rejected(tmp_path):
    with pytest.raises(AC.CandidateRejected) as e:
        AC.build_from_queues([tmp_path / "nope.jsonl"], **KW)
    assert e.value.defects[0][1] == "queue_missing"


# ---------- S5 §4:demo_models.json 设备字段必须有来源 ----------

def test_demo_models_device_fields_are_sourced():
    """守住设备回填:train_device 不得退回 'none'/null,且必须给来源。"""
    p = ROOT / "bench" / "results" / "demo_models.json"
    if not p.is_file():
        pytest.skip("manifest 不在仓内")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d.get("schema_version") == 2
    assert {"train", "inference"} <= set(d.get("device_registry") or {})
    for m in d["models"]:
        mid = m["model_id"]
        assert m.get("train_device") not in (None, "none"), f"{mid} train_device 未补"
        assert m.get("train_device_source"), f"{mid} 缺 train_device_source"
        # 取不到就得写 unknown,不得留空/不得编造设备名
        assert m.get("inference_device") in ("NVIDIA GB10", "unknown"), mid
        assert m.get("inference_device_source"), f"{mid} 缺 inference_device_source"
        assert m.get("train_device_ref"), f"{mid} 缺 train_device_ref"
        # 历史数字/引用字段不得被动过
        assert m["protocol"]["protocol_id"] == "judge-protocol-v1"
        assert m["paired_comparisons"], f"{mid} 配对材料被清空"

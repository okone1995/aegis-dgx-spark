"""Real processes/files and HTTP contract regressions; no target or model calls."""
import concurrent.futures
import importlib
import json
import multiprocessing
from pathlib import Path
import threading

import pytest
from engine.learning_queue import LearningQueue
from engine import jevtrain as JT
from engine.storage import CorruptQueueError


def _row(sid):
    return {"sample_id": sid, "input_hash": sid, "input_mode": "exchange",
            "intent_label": "attack", "review_state": "pending"}


def _queue_worker(args):
    path, kind, count = args
    queue = LearningQueue(path)
    for i in range(count):
        if kind == "review":
            queue.mutate("seed", lambda r: r.update(updates=r.get("updates", 0) + 1))
        else:
            queue.append(_row(f"{kind}-{i}"))
    return True


def test_cross_process_append_review_and_dedup(tmp_path):
    path = tmp_path / "q.jsonl"
    queue = LearningQueue(path)
    queue.append(_row("seed"))
    ctx = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(max_workers=4, mp_context=ctx) as pool:
        # Two writers submit identical samples; the third submits distinct ones.
        jobs = [(str(path), "same", 16), (str(path), "same", 16),
                (str(path), "different", 16), (str(path), "review", 24)]
        assert all(pool.map(_queue_worker, jobs))
    rows = queue.load()
    assert len(rows) == 33
    assert len({r["sample_id"] for r in rows}) == 33
    assert next(r for r in rows if r["sample_id"] == "seed")["updates"] == 24


def test_correction_invalidates_stale_admission(tmp_path):
    q = LearningQueue(tmp_path / "q.jsonl")
    q.append(_row("sample"))
    corrected = JT.review_label(q, "sample", "benign", "operator", "independent evidence")
    assert corrected["label_revision"] == 1
    with pytest.raises(ValueError, match="revision"):
        q.approve("sample", expected_label_revision=0)
    q.approve("sample", reviewer="operator", expected_label_revision=1)
    JT.review_label(q, "sample", "attack", "operator", "additional evidence")
    assert q.load()[0]["review_state"] == "pending"
    with pytest.raises(ValueError, match="revision"):
        q.approve("sample", expected_label_revision=1)
    assert len(q.load()[0]["label_audit"]) == 2


def test_corruption_refuses_update_without_erasing_evidence(tmp_path):
    path = tmp_path / "q.jsonl"
    original = b'{"sample_id":"valid"}\n{"broken":\n'
    path.write_bytes(original)
    with pytest.raises(CorruptQueueError, match="row 2"):
        LearningQueue(path).append(_row("new"))
    assert path.read_bytes() == original


def test_report_excludes_unreviewed_labels_and_abstentions(tmp_path):
    q = LearningQueue(tmp_path / "q.jsonl")
    for sid, verdict in [("pending", "attack"), ("abstaining", "abstain")]:
        q.append(dict(_row(sid), intent_label="benign", label_review_state="needs_review",
                      judge={"verdict": verdict}, agreement=False))
    result = JT.report(q)
    assert result["false_positives"] == []
    assert result["suspected_disagreements"] == ["pending"]
    assert result["judge_agreement"]["abstain"] == 1
    assert result["judge_agreement"]["disagree"] == 0
    JT.review_label(q, "pending", "benign", "operator", "normal request confirmed")
    assert JT.report(q)["false_positives"] == ["pending"]


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("AEGIS_JUDGE_MODE", "off")
    monkeypatch.setenv("AEGIS_RECONCILE", "0")
    import console.backend.main as module
    module = importlib.reload(module)
    calls = []
    monkeypatch.setattr(module, "_spawn_demo_case", lambda run_id, **kw: calls.append(run_id))
    from fastapi.testclient import TestClient
    return TestClient(module.app), module, calls


def test_concurrent_http_requests_create_exactly_one_run(api):
    client, module, calls = api
    barrier = threading.Barrier(8)
    def create(_):
        barrier.wait(timeout=10)
        return client.post("/api/demo/runs", json={"client_request_id": "same-request-0001"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(create, range(8)))
    assert [r.status_code for r in responses] == [202] * 8
    assert len({r.json()["run_id"] for r in responses}) == 1
    assert len(calls) == 1
    assert sum(r.json().get("created", False) for r in responses) == 1


def test_idempotent_parameters_cannot_change_and_survive_restart(api):
    client, module, calls = api
    body = {"client_request_id": "same-request-0002"}
    first = client.post("/api/demo/runs", json=body)
    assert first.status_code == 202
    changed = client.post("/api/demo/runs", json={**body, "cleanup_policy": "retain"})
    assert changed.status_code == 409
    from engine.run_store import RunStore
    module._run_store = RunStore(module.RUNS_ROOT)  # new instance, same durable records
    again = client.post("/api/demo/runs", json=body)
    assert again.status_code == 202
    assert first.json()["run_id"] == again.json()["run_id"]
    assert len(calls) == 1


def test_different_keys_cannot_create_two_active_runs(api):
    client, module, calls = api
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda key: client.post("/api/demo/runs",
            json={"client_request_id": key}), ["different-1", "different-2"]))
    assert sorted(r.status_code for r in responses) == [202, 409]
    assert len(calls) == 1

def _hold_transaction(path, ready):
    from engine.storage import JsonlStore
    import time
    def change(rows):
        rows.append({"sample_id": "must-not-commit"})
        ready.set()
        time.sleep(60)
    JsonlStore(path).update(change)


def test_process_death_releases_lock_without_partial_commit(tmp_path):
    from engine.storage import JsonlStore
    path = tmp_path / "q.jsonl"
    store = JsonlStore(path)
    store.update(lambda rows: rows.append({"sample_id": "committed"}))
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    child = ctx.Process(target=_hold_transaction, args=(str(path), ready))
    child.start()
    try:
        assert ready.wait(timeout=15)
        child.terminate()
        child.join(timeout=10)
        assert not child.is_alive()
        assert store.load() == [{"sample_id": "committed"}]
        store.update(lambda rows: rows.append({"sample_id": "after-restart"}))
        assert [r["sample_id"] for r in store.load()] == ["committed", "after-restart"]
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=5)


def test_export_snapshot_blocks_concurrent_label_review(tmp_path, monkeypatch):
    import hashlib
    import build_judge as BJ
    q = LearningQueue(tmp_path / "q.jsonl")
    q.append(dict(_row("exported"), method="GET", path="/unit", status=200,
                  response="test", family="unit-family", label_review_state="auto_ok"))
    q.approve("exported", reviewer="operator")
    snapshot_hash = hashlib.sha256(q.path.read_bytes()).hexdigest()
    entered, release, review_started = threading.Event(), threading.Event(), threading.Event()
    original = BJ.make_record
    def paused_record(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return original(*args, **kwargs)
    monkeypatch.setattr(BJ, "make_record", paused_record)
    def review():
        review_started.set()
        return JT.review_label(q, "exported", "benign", "operator", "revised evidence")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        exporting = pool.submit(JT.export, q, "unit", tmp_path / "export")
        assert entered.wait(timeout=10)
        reviewing = pool.submit(review)
        try:
            assert review_started.wait(timeout=10)
            with pytest.raises(concurrent.futures.TimeoutError):
                reviewing.result(timeout=0.1)
        finally:
            release.set()
        manifest = exporting.result(timeout=10)
        reviewing.result(timeout=10)
    assert manifest["queue_sha256"] == snapshot_hash
    assert q.load()[0]["intent_label"] == "benign"
    records = []
    for name in ("train.jsonl", "holdout.jsonl"):
        records.extend(json.loads(line) for line in (tmp_path/"export"/name).read_text(
            encoding="utf-8").splitlines())
    assert len(records) == 1
    assert records[0]["messages"][-1]["content"] == "attack"


@pytest.mark.parametrize("field,value", [
    ("case_id", []), ("patch_provider", {}), ("review_provider", []),
    ("cleanup_policy", {}), ("client_request_id", {"arbitrary": "object"}),
    ("client_request_id", "x" * 201),
])
def test_invalid_run_parameters_never_spawn(api, field, value):
    client, module, calls = api
    response = client.post("/api/demo/runs", json={field: value})
    assert response.status_code == 400
    assert calls == []
    assert module._run_store.list_runs() == []

def _poc_worker(args):
    from engine.poc_queue import PocQueue
    path, mode = args
    q = PocQueue(path)
    for i in range(12):
        if mode == "review":
            sid = f"seed-{i}"
            q.confirm(sid, {"status": 200, "response": "test marker"}, ["test marker"])
            q.approve(sid, "unit-operator", "synthetic evidence")
        else:
            q.append({"class": "sqli", "intent": "attack", "source": "menu",
                      "request_text": f"GET /unit/new/{i} HTTP/1.1"})
    return True


def test_poc_append_and_evidence_approval_are_one_transaction_each(tmp_path):
    from engine.poc_queue import PocQueue
    path = tmp_path / "poc.jsonl"
    q = PocQueue(path)
    for i in range(12):
        q.append({"candidate_id": f"seed-{i}", "class": "sqli", "intent": "attack",
                  "request_text": f"GET /unit/seed/{i} HTTP/1.1"})
    ctx = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(max_workers=3, mp_context=ctx) as pool:
        assert all(pool.map(_poc_worker,
                            [(str(path), "append"), (str(path), "append"), (str(path), "review")]))
    rows = q.load()
    assert len(rows) == 24
    assert len({row["candidate_id"] for row in rows}) == 24
    for i in range(12):
        row = q.get(f"seed-{i}")
        assert row["review_state"] == "approved"
        assert row["evidence_state"] == "confirmed"
        assert row["reviewer"] == "unit-operator"

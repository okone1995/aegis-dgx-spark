# -*- coding: utf-8 -*-
"""T10-S6/S7:受控恢复入口 + 故障注入登记。

来历(2026-09-27 真机):
  * 在 patch 阶段 SIGKILL 子进程 → 子进程成僵尸 → 旧存活探测把僵尸当活 →
    run 永远 running、锁不释放、新建任务 409,且**没有任何接口能收尾**。
  * 因此补一个 POST /api/demo/runs/{id}/recover:进程仍在则 409 拒绝;持锁后才恢复并写终态。
  * 故障注入必须可审计:被注入的轮次要在 preflight 载荷与事件流里留痕。
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from console.backend import main as M  # noqa: E402
from engine import demo_contracts as DC  # noqa: E402


def test_recover_refuses_when_terminal(tmp_path, monkeypatch):
    """已终态的 run 不需要恢复:返回 recovered=False,不得改状态。"""
    runs = tmp_path / "workspace" / "runs" / "run-20260101-000000-aaaa"
    runs.mkdir(parents=True)
    (runs / "run.json").write_text(json.dumps({"schema_version": 1, "run_id": runs.name,
                                               "state": "succeeded", "pid": 0}), encoding="utf-8")
    monkeypatch.setattr(M, "WS", tmp_path / "workspace")
    c = TestClient(M.app)
    r = c.post("/api/demo/runs/run-20260101-000000-aaaa/recover")
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body["recovered"] is False and body["state"] == "succeeded"


def test_recover_refuses_when_process_still_alive(tmp_path, monkeypatch):
    """进程仍存活 → 409 拒绝(绝不允许两个写者)。这里用当前进程自己的 pid 当'活着'的证据。"""
    import os
    rid = "run-20260101-000000-bbbb"
    runs = tmp_path / "workspace" / "runs" / rid
    runs.mkdir(parents=True)
    (runs / "run.json").write_text(json.dumps({"schema_version": 1, "run_id": rid,
                                               "state": "running", "pid": os.getpid()}),
                                  encoding="utf-8")
    monkeypatch.setattr(M, "WS", tmp_path / "workspace")
    c = TestClient(M.app)
    r = c.post(f"/api/demo/runs/{rid}/recover")
    assert r.status_code == 409, r.text[:200]
    assert "still alive" in r.json()["detail"]


def test_recover_unknown_run_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "WS", tmp_path / "workspace")
    c = TestClient(M.app)
    r = c.post("/api/demo/runs/run-20260101-000000-cccc/recover")
    assert r.status_code == 404


def test_fault_injected_event_type_is_registered():
    """注入登记事件必须在契约白名单里,否则会被契约校验拒掉。"""
    assert "fault.injected" in DC.VALID_EVENTS


def test_startup_reconcile_recovers_orphan_and_skips_live(tmp_path, monkeypatch):
    """D7:启动对账 —— 进程已消失的孤儿 run 自动收尾;进程仍活着的必须跳过。"""
    import os
    ws = tmp_path / "workspace"
    orphan = ws / "runs" / "run-20260101-000000-dddd"
    alive = ws / "runs" / "run-20260101-000000-eeee"
    for rd, pid in ((orphan, 999999998), (alive, os.getpid())):
        rd.mkdir(parents=True)
        (rd / "run.json").write_text(
            json.dumps({"schema_version": 1, "run_id": rd.name, "state": "running", "pid": pid}),
            encoding="utf-8")
    called = {}

    def _fake_restore(reason):
        called["restore_reason"] = reason
        return {"ok": True, "detail": "stub-restored"}

    def _fake_record(run_id, cleanup, stop_reason, detail, state):
        called.setdefault("records", []).append((run_id, cleanup, stop_reason, state))

    monkeypatch.setattr(M, "_restore_canonical_target", _fake_restore)
    monkeypatch.setattr(M, "_record_cancel_outcome", _fake_record)
    res = M._reconcile_orphan_runs(ws)
    by_id = {r["run_id"]: r for r in res}
    assert by_id[orphan.name]["action"] == "recovered", res
    assert by_id[alive.name]["action"] == "skip", res
    assert called["restore_reason"].startswith("startup-reconcile"), called
    assert called["records"][0][0] == orphan.name
    assert called["records"][0][3] == "interrupted"
    # 对账完锁必须已释放
    from engine import run_lock as _rl
    assert not _rl.default_lock_path().exists(), "对账后不得留锁"

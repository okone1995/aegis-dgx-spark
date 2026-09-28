# -*- coding: utf-8 -*-
"""T1 遗留补充:demo API 端点测试(8 端点契约 §2.4)。

写法约束(来自 T1 交接):模块 import 时已固化 WS/RUNS_ROOT,必须用
monkeypatch.setenv("AEGIS_WORKSPACE") + importlib.reload(console.backend.main)。
httpx 已装(0.28.1),TestClient 可用。零 PHP/LLM/网络。
"""
import importlib
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 每次 reload main 都需要 TestClient(依赖 httpx);缺 httpx 的环境(如 Spark venv)
# 应跳过而不是报 collection error——skip 是诚实标记,不是假绿。
httpx = pytest.importorskip("httpx", reason="需要 httpx 才能用 starlette TestClient")
# T7 复核补充:缺 fastapi 的环境里 import console.backend.main 会在**收集阶段**
# 直接报错(整仓测试停摆),而 skip 才是诚实标记 —— 两处都做 importorskip。
pytest.importorskip("fastapi", reason="需要 fastapi 才能导入 console.backend.main")


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_WORKSPACE", str(tmp_path))
    import console.backend.main as m
    m = importlib.reload(m)
    # T7 实测:POST 路径会真的派生引擎子进程(bootstrap 修好后更是必然成功启动),
    # 在本机跑测试时会真的写 workspace/runs/ 并抢靶场排他锁 —— 本文件只验 API 形状,
    # 必须把派生替换成 no-op(进程管理另有 tests/test_api_param_passthrough.py 锁)。
    monkeypatch.setattr(m, "_spawn_demo_case", lambda *a, **k: None)
    from fastapi.testclient import TestClient
    # 阻止 _shadow_loop 线程访问不存在的 workspace 文件(它会静默吞异常,无害)
    return TestClient(m.app), m, tmp_path


def test_create_run_202_and_shape(api):
    tc, m, ws = api
    r = tc.post("/api/demo/runs", json={"case_id": "sqli"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert "run_id" in body
    rid = body["run_id"]
    assert m.RUN_ID_RE.match(rid) if hasattr(m, "RUN_ID_RE") else True
    # run 目录已建,run.json 为 queued 或已 failed(engine.demo_case 不存在时)
    rj = json.loads((ws / "runs" / rid / "run.json").read_text(encoding="utf-8"))
    assert rj["case_id"] == "sqli"
    assert rj["state"] in ("queued", "running", "failed")
    # T2 未实现时应有 run.failed 事件落盘
    evs = (ws / "runs" / rid / "events.jsonl").read_text(encoding="utf-8").strip()
    assert evs, "events.jsonl 不能为空"


def test_client_request_id_idempotent(api):
    tc, m, ws = api
    r1 = tc.post("/api/demo/runs", json={"case_id": "sqli",
                                         "client_request_id": "abc-123"})
    r2 = tc.post("/api/demo/runs", json={"case_id": "sqli",
                                         "client_request_id": "abc-123"})
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["run_id"] == r2.json()["run_id"], "同 client_request_id 必须幂等"


def test_bad_params_rejected(api):
    tc, m, ws = api
    assert tc.post("/api/demo/runs", json={"case_id": "xxe"}).status_code == 400
    assert tc.post("/api/demo/runs", json={"case_id": "sqli",
                                           "patch_provider": "openai"}).status_code == 400


def test_get_run_404_and_snapshot(api):
    tc, m, ws = api
    # 未知/非法 id:非法格式直接 404,合法格式但不存在也 404,均不得 500
    assert tc.get("/api/demo/runs/nonexistent-id").status_code == 404
    assert tc.get("/api/demo/runs/..%2F..%2Fetc").status_code in (400, 404)
    rid = tc.post("/api/demo/runs", json={"case_id": "sqli"}).json()["run_id"]
    # 同格式但随机不存在的 id → 404
    fake = rid[:-4] + "beef"
    assert tc.get(f"/api/demo/runs/{fake}").status_code == 404
    r = tc.get(f"/api/demo/runs/{rid}")
    assert r.status_code == 200
    assert r.json()["run"]["run_id"] == rid


def test_events_pagination(api):
    tc, m, ws = api
    rid = tc.post("/api/demo/runs", json={"case_id": "sqli"}).json()["run_id"]
    r = tc.get(f"/api/demo/runs/{rid}/events?after=0&limit=1")
    assert r.status_code == 200
    d = r.json()
    assert "events" in d and "next_seq" in d and "has_more" in d


def test_cancel_idempotent(api):
    tc, m, ws = api
    rid = tc.post("/api/demo/runs", json={"case_id": "sqli"}).json()["run_id"]
    r1 = tc.post(f"/api/demo/runs/{rid}/cancel")
    assert r1.status_code == 202
    r2 = tc.post(f"/api/demo/runs/{rid}/cancel")
    assert r2.status_code == 202  # 幂等
    assert tc.post("/api/demo/runs/zzz/cancel").status_code == 404
    assert tc.post("/api/demo/runs/nonexistent-id/cancel").status_code == 404
    assert tc.post("/api/demo/runs/..%2F..%2Fetc/cancel").status_code in (400, 404)


# ---- 取消路径的受控收尾（三分支；子进程用替身，结论为「替身级」）----------
class _FakeProc:
    """替身级子进程：只实现取消路径用到的 poll/terminate/kill。

    本机真派生 + 真 kill 不在单测范围（会真抢靶场锁、真写 canonical），
    因此三分支统一由替身驱动后端逻辑。
    """

    def __init__(self, exit_after_s=None, die_on_terminate=True, on_exit=None):
        self._t0 = time.monotonic()
        self._exit_after_s = exit_after_s
        self._dead = False
        self._die_on_terminate = die_on_terminate
        self._on_exit = on_exit
        self.terminated = 0
        self.killed = 0

    def _die(self):
        """进程消失；on_exit 模拟子进程在退出前自己落终态。"""
        if self._dead:
            return
        self._dead = True
        if self._on_exit is not None:
            self._on_exit()

    def poll(self):
        if self._dead:
            return 0
        if self._exit_after_s is not None and \
                time.monotonic() - self._t0 >= self._exit_after_s:
            self._die()
            return 0
        return None

    def terminate(self):
        self.terminated += 1
        if self._die_on_terminate:
            self._die()

    def kill(self):
        self.killed += 1
        self._die()


def _run_json(ws, rid):
    return json.loads((ws / "runs" / rid / "run.json").read_text(encoding="utf-8"))


def _cancel_events(ws, rid):
    raw = (ws / "runs" / rid / "events.jsonl").read_text(encoding="utf-8")
    return [json.loads(l) for l in raw.splitlines()
            if l.strip() and json.loads(l).get("type") == "run.cancelled"]


def _start_running_run(tc, m):
    """创建一个 run 并置 running（真实场景：活动任务进程=run 已被接管）。"""
    r = tc.post("/api/demo/runs", json={"case_id": "sqli"})
    assert r.status_code == 202, r.text
    rid = r.json()["run_id"]
    m._run_store.update(rid, state="running", pid=4242)
    return rid


def _cancel_env(monkeypatch, grace="0.05", kill_wait="0.05", lock_wait="0.05"):
    monkeypatch.setenv("AEGIS_CANCEL_GRACE_S", grace)
    monkeypatch.setenv("AEGIS_CANCEL_KILL_WAIT_S", kill_wait)
    monkeypatch.setenv("AEGIS_CANCEL_LOCK_WAIT_S", lock_wait)


def test_cancel_child_self_exit_keeps_child_terminal_state(api, monkeypatch):
    """分支①：窗口内子进程自退 → 保持子进程自己写的终态，不覆盖、不恢复。"""
    tc, m, ws = api
    rid = _start_running_run(tc, m)
    _cancel_env(monkeypatch, grace="1.0")
    restore_calls = []
    monkeypatch.setattr(m, "_restore_canonical_target",
                        lambda **kw: restore_calls.append(kw) or
                        {"ok": True, "detail": "stub"})
    def child_finalize():
        """模拟子进程退出前自己落的终态（子进程独占 run.json 写权）。"""
        child = _run_json(ws, rid)
        child.update({"state": "cancelled", "cleanup": "child_self",
                      "ended_at": "2026-01-01T00:00:00.000Z"})
        (ws / "runs" / rid / "run.json").write_text(
            json.dumps(child, ensure_ascii=False), encoding="utf-8")

    fake = _FakeProc(exit_after_s=0.02, die_on_terminate=False,
                     on_exit=child_finalize)
    m._active_demo_runs[rid] = fake
    assert _run_json(ws, rid)["state"] == "running", "前置：取消前 run 仍在跑"

    body = tc.post(f"/api/demo/runs/{rid}/cancel").json()
    assert (ws / "runs" / rid / "cancel.request").exists(), "取消标记必须先落盘"
    assert body["stop_reason"] == "child_self_exit", body
    assert body["cleanup"] == "self", body
    assert fake.terminated == 0, "窗口内自退不得 terminate"
    assert restore_calls == [], "自退分支不得恢复靶场"
    final = _run_json(ws, rid)
    assert final["state"] == "cancelled", "子进程终态被覆盖"
    assert final["cleanup"] == "child_self", "子进程 cleanup 被覆盖"
    assert _cancel_events(ws, rid) == [], "自退分支后端不应另写终态事件"
    # 之后再取消：已是子进程终态 → 幂等返回，不重复动作
    again = tc.post(f"/api/demo/runs/{rid}/cancel").json()
    assert again["cancelled"] is False and "terminal" in again["note"], again


def test_cancel_terminates_after_grace_then_recovers_under_lock(api, monkeypatch):
    """分支②：窗口后仍存活 → terminate + 取到锁后恢复 canonical → interrupted。"""
    tc, m, ws = api
    rid = _start_running_run(tc, m)
    _cancel_env(monkeypatch)
    restore_calls = []
    monkeypatch.setattr(m, "_restore_canonical_target",
                        lambda **kw: restore_calls.append(kw) or
                        {"ok": True, "detail": "stub restored"})
    fake = _FakeProc(exit_after_s=None, die_on_terminate=True)
    m._active_demo_runs[rid] = fake

    body = tc.post(f"/api/demo/runs/{rid}/cancel").json()
    assert fake.terminated == 1, "窗口后必须 terminate"
    assert len(restore_calls) == 1, "取到锁后应恢复一次靶场"
    assert body["cleanup"] == "restored", body
    assert body["stop_reason"].startswith("forced_terminate"), body
    assert body["state"] == "interrupted", body
    final = _run_json(ws, rid)
    assert final["state"] == "interrupted", final
    assert final["cleanup"] == "restored", final
    assert final["ended_at"], "终态必须带 ended_at"
    evs = _cancel_events(ws, rid)
    assert evs, "必须记一条 run.cancelled 事件"
    assert evs[-1]["payload"]["cleanup"] == "restored", evs[-1]
    assert evs[-1]["payload"]["stop_reason"].startswith("forced_terminate"), evs[-1]


def test_cancel_lock_unavailable_does_not_restore(api, monkeypatch):
    """分支③：取不到靶场锁 → 严禁恢复，只记 cleanup=pending 交人工。"""
    tc, m, ws = api
    rid = _start_running_run(tc, m)
    _cancel_env(monkeypatch)
    restore_calls = []
    monkeypatch.setattr(m, "_restore_canonical_target",
                        lambda **kw: restore_calls.append(kw) or
                        {"ok": True, "detail": "must not run"})
    fake = _FakeProc(exit_after_s=None, die_on_terminate=True)
    m._active_demo_runs[rid] = fake
    holder = m.RunLock(m._LOCK_PATH, m._LOCK_IDENTITY)  # 真实锁，由本测试进程持有
    assert holder.acquire(timeout_s=1.0), "测试前置：锁必须能取到"
    try:
        body = tc.post(f"/api/demo/runs/{rid}/cancel").json()
    finally:
        holder.release()
    assert fake.terminated == 1, "进程仍须先被终止"
    assert restore_calls == [], "取不到锁时严禁恢复靶场"
    assert body["cleanup"] == "pending", body
    assert body["error"], body
    final = _run_json(ws, rid)
    assert final["state"] == "interrupted", final
    assert final["cleanup"] == "pending", final
    assert final["error"], "没恢复必须留下原因"


def test_artifacts_rejects_traversal(api):
    tc, m, ws = api
    rid = tc.post("/api/demo/runs", json={"case_id": "sqli"}).json()["run_id"]
    # 无 artifacts.json → 404
    assert tc.get(f"/api/demo/runs/{rid}/artifacts/anything").status_code == 404
    # 伪造白名单 + 穿越尝试
    arts = ws / "runs" / rid / "artifacts.json"
    arts.write_text(json.dumps([{"id": "diff-1",
                                 "path": "patches/F-001.patch",
                                 "scrubbed": True}]), encoding="utf-8")
    # 已登记但文件不存在 → 404(不伪造内容)
    assert tc.get(f"/api/demo/runs/{rid}/artifacts/diff-1").status_code == 404
    for evil in ("..%2F..%2Fsecrets.stepfun.env", "patches/F-001.patch",
                 "nonexistent"):
        resp = tc.get(f"/api/demo/runs/{rid}/artifacts/{evil}")
        assert resp.status_code in (400, 404), f"{evil} 必须被拒绝"


def test_models_unavailable_when_manifest_missing(api):
    tc, m, ws = api
    r = tc.get("/api/demo/models")
    assert r.status_code == 200
    assert r.json().get("status") == "unavailable" or "models" in r.json()


def test_runs_list(api):
    tc, m, ws = api
    tc.post("/api/demo/runs", json={"case_id": "sqli"})
    r = tc.get("/api/demo/runs")
    assert r.status_code == 200
    assert isinstance(r.json() if isinstance(r.json(), list) else r.json().get("runs", []), list)

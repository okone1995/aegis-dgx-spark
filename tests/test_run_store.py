"""T1 工包测试:run 生命周期存储 + 跨进程排他锁(契约冻结 §2.2/§2.3/§3)。

不依赖 PHP/LLM/网络;全部用 tmp_path 真实文件验证。
"""
import json
import os
import pathlib

import pytest

from engine.demo_contracts import (
    ContractError,
    DemoEvent,
    make_run_id,
    utcnow_iso,
    validate_event,
    validate_state_transition,
)
from engine.run_lock import RunLock, StaleLockError
from engine.run_store import RunStore, RunStoreError, StaleWriterError


# ---------- demo_contracts ----------

def test_utcnow_iso_format():
    ts = utcnow_iso()
    assert ts.endswith("Z")
    assert len(ts) == len("2026-09-26T03:00:00.123Z")
    # 毫秒精度:小数秒三位
    date_part, _, ms = ts.partition(".")
    assert len(ms) == 4 and ms.endswith("Z")  # .123Z


def test_make_run_id_shape():
    rid = make_run_id()
    assert rid.startswith("run-")
    # run-yyyymmdd-hhmmss-4hex
    parts = rid.split("-")
    assert len(parts) == 4
    assert len(parts[1]) == 8 and parts[1].isdigit()
    assert len(parts[2]) == 6 and parts[2].isdigit()
    assert len(parts[3]) == 4
    int(parts[3], 16)  # 4 位 hex


def test_validate_state_transition_legal_paths():
    validate_state_transition("queued", "running")
    validate_state_transition("queued", "cancelled")
    validate_state_transition("queued", "failed")
    for term in ("succeeded", "partial", "failed", "cancelled", "interrupted"):
        validate_state_transition("running", term)
        validate_state_transition(term, term)  # 终态自我等值(幂等)


@pytest.mark.parametrize("old,new", [
    ("queued", "succeeded"),      # 跳过 running
    ("queued", "partial"),
    ("queued", "interrupted"),
    ("running", "queued"),        # 不可回退
    ("succeeded", "running"),     # 终态不可复活
    ("failed", "cancelled"),
    ("cancelled", "failed"),
    ("interrupted", "running"),
])
def test_validate_state_transition_illegal(old, new):
    with pytest.raises(ContractError):
        validate_state_transition(old, new)


def test_validate_state_transition_unknown_state():
    with pytest.raises(ContractError):
        validate_state_transition("queued", "bogus")
    with pytest.raises(ContractError):
        validate_state_transition("bogus", "running")


def _mk_event(**over):
    base = dict(
        schema_version=1, run_id="run-20260926-010203-0a0b",
        seq=1, event_id="run-20260926-010203-0a0b:1",
        ts="2026-09-26T01:02:03.456Z", type="run.created",
        stage="preflight", actor="backend",
    )
    base.update(over)
    return DemoEvent(**base)


def test_validate_event_accepts_skeleton():
    validate_event(_mk_event())


def test_validate_event_rejects_bad():
    with pytest.raises(ContractError):
        validate_event(_mk_event(seq=0))                     # seq < 1
    with pytest.raises(ContractError):
        validate_event(_mk_event(type="made.up.event"))      # type 不在表内
    with pytest.raises(ContractError):
        validate_event(_mk_event(ts="2026-09-26T01:02:03Z"))  # 缺毫秒
    with pytest.raises(ContractError):
        validate_event(_mk_event(ts="2026-09-26 01:02:03.456Z"))  # 非 ISO T
    with pytest.raises(ContractError):
        validate_event(_mk_event(event_id="wrong:999"))      # event_id 不匹配
    with pytest.raises(ContractError):
        validate_event(_mk_event(stage="not-a-stage"))       # stage 枚举


def test_demo_event_roundtrip():
    e = _mk_event(payload={"k": [1, 2]}, artifact_refs=["judge:FL-0001"])
    d = e.to_dict()
    e2 = DemoEvent.from_row(d)
    assert e2.to_dict() == d
    with pytest.raises(ContractError):
        DemoEvent.from_row({"seq": "x"})  # 缺必备字段


# ---------- RunStore ----------

def test_create_load_list_roundtrip(tmp_path):
    st = RunStore(tmp_path / "runs")
    m1 = st.create("sqli", {"patch": "qwen", "review": "step"})
    m2 = st.create("sqli", {"patch": "step", "review": "step"})
    assert m1.state == "queued" and m2.state == "queued"
    assert m1.run_id != m2.run_id

    loaded = st.load(m1.run_id)
    assert loaded is not None
    assert loaded.run_id == m1.run_id
    assert loaded.case_id == "sqli"
    assert loaded.providers == {"patch": "qwen", "review": "step"}
    assert loaded.last_seq == 0

    # 目录契约:全套子目录/文件就位
    d = st.run_dir(m1.run_id)
    assert (d / "run.json").is_file()
    assert (d / "events.jsonl").is_file()
    assert (d / "patches").is_dir()
    assert (d / "flow.jsonl").is_file()

    runs = st.list_runs()
    assert [r.run_id for r in runs] == [m2.run_id, m1.run_id]  # created_at 倒序

    # run.json 内容:indent=2、ensure_ascii=False 可回读
    raw = (d / "run.json").read_text(encoding="utf-8")
    assert json.loads(raw)["run_id"] == m1.run_id
    assert "\n  \"state\"" in raw  # indent=2 的形态


def test_load_unknown_and_corrupt(tmp_path):
    st = RunStore(tmp_path / "runs")
    assert st.load("run-20260926-000000-dead") is None
    # 坏 run.json → None
    d = tmp_path / "runs" / "run-20260926-000000-bad0"
    d.mkdir(parents=True)
    (d / "run.json").write_text("not json", encoding="utf-8")
    assert st.load("run-20260926-000000-bad0") is None
    # list_runs 跳过坏行
    assert st.list_runs() == []


def test_run_id_path_traversal_rejected(tmp_path):
    st = RunStore(tmp_path / "runs")
    st.create("sqli")
    for bad in ("../etc", "..\\etc", "run-../escape", "../../secrets.stepfun.env",
                "run-20260926-000000-..%2f", "", "run-2026-9-6-0-0-ab"):
        with pytest.raises(RunStoreError):
            st.load(bad)
        with pytest.raises(RunStoreError):
            st.read_events(bad)
        with pytest.raises(RunStoreError):
            st.update(bad, state="running")


def test_append_event_seq_strictly_increasing_and_snapshot_after(tmp_path):
    st = RunStore(tmp_path / "runs")
    m = st.create("sqli")
    seqs = []
    for i in range(5):
        e = st.append_event(m.run_id, "stage.started", "attack", "task",
                            summary=f"step {i}")
        seqs.append(e.seq)
        # 快照 last_seq 立即可见(事件先落盘后快照)
        assert st.load(m.run_id).last_seq == e.seq
    assert seqs == [1, 2, 3, 4, 5]

    # events.jsonl 逐行可回读且 seq 严格递增
    lines = (st.run_dir(m.run_id) / "events.jsonl") \
        .read_text(encoding="utf-8").strip().splitlines()
    disk_seq = [json.loads(l)["seq"] for l in lines]
    assert disk_seq == [1, 2, 3, 4, 5]
    # 事件骨架完整字段
    row = json.loads(lines[0])
    for k in ("schema_version", "run_id", "seq", "event_id", "ts", "type",
              "stage", "actor", "summary"):
        assert k in row
    assert row["event_id"] == f"{m.run_id}:1"


def test_append_event_rejects_unknown_type(tmp_path):
    st = RunStore(tmp_path / "runs")
    m = st.create("sqli")
    with pytest.raises(ContractError):
        st.append_event(m.run_id, "made.up", "preflight", "backend")
    # 失败事件不落盘、不动快照
    assert st.load(m.run_id).last_seq == 0
    assert (st.run_dir(m.run_id) / "events.jsonl").read_text() == ""


def test_read_events_pagination(tmp_path):
    st = RunStore(tmp_path / "runs")
    m = st.create("sqli")
    for i in range(7):
        st.append_event(m.run_id, "stage.started", "attack", "task",
                        summary=f"e{i}")

    # 全量
    page = st.read_events(m.run_id)
    assert len(page["events"]) == 7
    assert page["next_seq"] == 7 and page["has_more"] is False
    assert page["state"] == "queued"

    # after=3 → seq 4..7
    page = st.read_events(m.run_id, after=3)
    assert [e["seq"] for e in page["events"]] == [4, 5, 6, 7]

    # limit=3, after=0 → 前 3 条 + has_more
    page = st.read_events(m.run_id, after=0, limit=3)
    assert [e["seq"] for e in page["events"]] == [1, 2, 3]
    assert page["has_more"] is True
    assert page["next_seq"] == 3

    # limit=3, after=3 → 4..6 + has_more
    page = st.read_events(m.run_id, after=3, limit=3)
    assert [e["seq"] for e in page["events"]] == [4, 5, 6]
    assert page["has_more"] is True

    # after 超过最大 seq → 空,next_seq=after
    page = st.read_events(m.run_id, after=99)
    assert page["events"] == [] and page["next_seq"] == 99
    assert page["has_more"] is False


def test_read_events_skips_bad_lines_keeps_order(tmp_path):
    st = RunStore(tmp_path / "runs")
    m = st.create("sqli")
    st.append_event(m.run_id, "run.created", "preflight", "backend")
    st.append_event(m.run_id, "stage.started", "attack", "task")
    # 手动插入坏行:半行 JSON + 全垃圾
    p = st.run_dir(m.run_id) / "events.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    lines.insert(1, '{"seq": 2, "broken": ')      # 半行(进程中断形态)
    lines.append("total garbage")                  # 全垃圾
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # 再追加一条合法事件(seq 会是 3,因为快照 last_seq=2)
    st.append_event(m.run_id, "flow.captured", "attack", "task")

    page = st.read_events(m.run_id)
    seqs = [e["seq"] for e in page["events"]]
    assert seqs == [1, 2, 3]        # 坏行跳过,合法 seq 顺序保持
    assert page["next_seq"] == 3


def test_update_whitelist_and_validations(tmp_path):
    st = RunStore(tmp_path / "runs")
    m = st.create("sqli")

    # queued → running 合法
    m2 = st.update(m.run_id, state="running", started_at=utcnow_iso(),
                   pid=os.getpid(), process_identity="demo-case-proc")
    assert m2.state == "running" and m2.pid == os.getpid()
    assert m2.heartbeat_at >= m.heartbeat_at  # 心跳刷新

    # 非法 state 迁移拒绝
    with pytest.raises(ContractError):
        st.update(m.run_id, state="queued")    # running 不可回 queued
    # 白名单外字段拒绝
    with pytest.raises(RunStoreError):
        st.update(m.run_id, case_id="lfi")     # case_id 只读
    with pytest.raises(RunStoreError):
        st.update(m.run_id, schema_version=99)  # 只读字段
    # stage 枚举校验
    with pytest.raises(ContractError):
        st.update(m.run_id, stage="not-a-stage")
    m3 = st.update(m.run_id, stage="verify")
    assert m3.stage == "verify"

    # ended_at 只在终态可设
    with pytest.raises(RunStoreError):
        st.update(m.run_id, ended_at=utcnow_iso())   # running 态拒绝
    m4 = st.update(m.run_id, state="succeeded", ended_at=utcnow_iso(),
                   exit_code=0, repair_outcome="verified",
                   judge_status="ok", learning_status="queued",
                   cleanup="restored")
    assert m4.state == "succeeded" and m4.ended_at is not None
    # 终态不可再改 state(除自我等值)
    with pytest.raises(ContractError):
        st.update(m.run_id, state="failed")


def test_cancel_marker_does_not_touch_run_json(tmp_path):
    st = RunStore(tmp_path / "runs")
    m = st.create("sqli")
    before = (st.run_dir(m.run_id) / "run.json").read_text(encoding="utf-8")

    assert st.cancel_requested(m.run_id) is False
    st.request_cancel(m.run_id)
    assert st.cancel_requested(m.run_id) is True

    after = (st.run_dir(m.run_id) / "run.json").read_text(encoding="utf-8")
    assert after == before            # 契约 §2.3 规则 4:cancel 不动 run.json
    assert (st.run_dir(m.run_id) / "cancel.request").is_file()
    # run 状态仍 queued:取消的执行归任务进程/恢复器
    assert st.load(m.run_id).state == "queued"

    # 未知 run → RunStoreError
    with pytest.raises(RunStoreError):
        st.request_cancel("run-20260926-000000-none")


def test_owner_generation_stale_writer_rejected(tmp_path):
    st = RunStore(tmp_path / "runs")
    m = st.create("sqli")

    # 正确 generation 可写
    st.append_event(m.run_id, "run.created", "preflight", "backend",
                    expected_generation=1)
    st.update(m.run_id, state="running", expected_generation=1)

    # 过期写者(旧 backend generation=0)被拒
    with pytest.raises(StaleWriterError):
        st.append_event(m.run_id, "stage.started", "attack", "old-writer",
                        expected_generation=0)
    with pytest.raises(StaleWriterError):
        st.update(m.run_id, stage="attack", expected_generation=0)

    # 不带 expected_generation 的写不拦(后端自身/恢复器直写路径)
    st.update(m.run_id, stage="classify")


def test_heartbeat_refreshed_by_writes(tmp_path):
    st = RunStore(tmp_path / "runs")
    m = st.create("sqli")
    h0 = st.load(m.run_id).heartbeat_at
    st.append_event(m.run_id, "run.created", "preflight", "backend")
    h1 = st.load(m.run_id).heartbeat_at
    st.update(m.run_id, pid=os.getpid())
    h2 = st.load(m.run_id).heartbeat_at
    assert h0 <= h1 <= h2


def test_find_by_client_request_id(tmp_path):
    st = RunStore(tmp_path / "runs")
    m = st.create("sqli", options={"client_request_id": "req-abc"})
    st.create("sqli", options={"client_request_id": "req-def"})
    found = st.find_by_client_request_id("req-abc")
    assert found is not None and found.run_id == m.run_id
    assert st.find_by_client_request_id("req-nope") is None
    assert st.find_by_client_request_id("") is None


# ---------- RunLock ----------

def test_lock_acquire_release_basic(tmp_path):
    p = tmp_path / ".aegis.lock"
    lk = RunLock(p, "backend")
    assert lk.acquire(timeout_s=1) is True
    assert lk.held is True
    assert json.loads(p.read_text(encoding="utf-8"))["pid"] == os.getpid()
    # 第二把锁(不同对象)抢不到:本进程存活
    lk2 = RunLock(p, "backend")
    assert lk2.acquire(timeout_s=0.4) is False
    lk.release()
    assert lk.held is False
    assert not p.exists()
    # 释放后可再抢
    assert lk2.acquire(timeout_s=1) is True
    lk2.release()


def test_lock_context_manager(tmp_path):
    p = tmp_path / ".aegis.lock"
    with RunLock(p, "backend") as lk:
        assert lk.held
        assert p.exists()
    assert not p.exists()


def test_lock_alive_holder_detected_via_pid(tmp_path):
    """伪造存活锁:pid=当前进程 → 等待超时,不删锁。"""
    p = tmp_path / ".aegis.lock"
    p.write_text(json.dumps({"pid": os.getpid(),
                             "identity": "someone-else",
                             "acquired_at": 0}), encoding="utf-8")
    lk = RunLock(p, "backend")
    assert lk.acquire(timeout_s=0.4) is False     # 存活 → 等待 → 超时
    assert p.exists()                              # 锁文件不被删


def test_lock_dead_pid_wrong_identity_raises_stale(tmp_path):
    """伪造死亡锁:大 pid(不存在)且 identity 不匹配 → StaleLockError。"""
    p = tmp_path / ".aegis.lock"
    p.write_text(json.dumps({"pid": 999999998,
                             "identity": "old-service",
                             "acquired_at": 0}), encoding="utf-8")
    lk = RunLock(p, "backend")
    with pytest.raises(StaleLockError):
        lk.acquire(timeout_s=0.5)
    assert p.exists()   # 绝不静默删除


def test_lock_dead_pid_same_identity_takeover(tmp_path):
    """同 identity 死锁接手:崩溃遗留可安全重置。"""
    p = tmp_path / ".aegis.lock"
    p.write_text(json.dumps({"pid": 999999998,
                             "identity": "backend",
                             "acquired_at": 0}), encoding="utf-8")
    lk = RunLock(p, "backend")
    assert lk.acquire(timeout_s=1) is True
    assert json.loads(p.read_text(encoding="utf-8"))["pid"] == os.getpid()
    lk.release()
    assert not p.exists()


def test_lock_corrupt_file_raises_stale(tmp_path):
    p = tmp_path / ".aegis.lock"
    p.write_text("garbage not json", encoding="utf-8")
    with pytest.raises(StaleLockError):
        RunLock(p, "backend").acquire(timeout_s=0.5)
    assert p.read_text(encoding="utf-8") == "garbage not json"  # 不代删


def test_lock_release_only_removes_own_lock(tmp_path):
    """release 核对 pid:不删别人的锁。"""
    p = tmp_path / ".aegis.lock"
    # 别的活进程持锁
    import subprocess, sys
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        p.write_text(json.dumps({"pid": child.pid, "identity": "backend",
                                 "acquired_at": 0}), encoding="utf-8")
        lk = RunLock(p, "backend")
        # 未 acquire 成功就 release:不动锁文件
        lk.release()
        assert p.exists()
        # 强行标 held 再 release:pid 不是自己 → 不删
        lk._held = True
        lk.release()
        assert p.exists()
    finally:
        child.terminate()
        child.wait()

# -*- coding: utf-8 -*-
"""共享靶场锁回归锁(T7 复核 P0-3)。

三个要求:
1. 旧入口(runloop / marathon / demo.sh)必须与新入口共用同一把锁;
2. 遗留锁接管必须只有一个进程能成功(原子 CAS);
3. 抢不到锁时旧入口必须**非零退出并说明原因**,不能静默继续改靶场。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.run_lock import DEFAULT_IDENTITY, RunLock, locked_run  # noqa: E402


def test_locked_run_refuses_when_lock_held(tmp_path, capsys):
    """锁被占用时,旧入口的任务体不得执行,返回 3 且打印原因。"""
    lp = tmp_path / ".aegis.lock"
    holder = RunLock(lp, DEFAULT_IDENTITY)
    assert holder.acquire(timeout_s=1.0)
    ran = {"called": False}

    def _body():
        ran["called"] = True
        pytest.fail("锁被占用时不应执行任务体")

    code = locked_run(_body, lock_path=lp, timeout_s=0.5, label="marathon")
    holder.release()
    assert code == 3
    assert ran["called"] is False
    err = capsys.readouterr().err
    assert "占用" in err and "契约 §3" in err


def test_locked_run_executes_and_releases(tmp_path):
    lp = tmp_path / ".aegis.lock"
    code = locked_run(lambda: 0, lock_path=lp, timeout_s=1.0, label="x")
    assert code == 0
    assert not lp.exists(), "任务结束必须释放锁"


def test_takeover_has_single_winner(tmp_path):
    """遗留锁(死属主 + 同 identity)只能被一个进程接手——原子 CAS。"""
    lp = tmp_path / ".aegis.lock"
    lp.write_text(json.dumps({"pid": 999999998, "identity": DEFAULT_IDENTITY,
                              "acquired_at": 0}), encoding="utf-8")
    a = RunLock(lp, DEFAULT_IDENTITY)
    b = RunLock(lp, DEFAULT_IDENTITY)
    assert a._try_takeover() is True, "第一个请求者应接手成功"
    assert b._try_takeover() is False, "第二个请求者不得同时认为持锁"
    assert b.held is False
    info = json.loads(lp.read_text(encoding="utf-8"))
    assert info["pid"] == os.getpid()
    leftovers = [p.name for p in tmp_path.iterdir() if ".stale." in p.name]
    assert leftovers == [], f"坟场文件未清理: {leftovers}"
    a.release()


def test_legacy_entries_reference_shared_lock():
    """静态:旧入口源码必须引用共享锁(防回归)。"""
    for rel in ("engine/runloop.py", "engine/marathon.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "run_lock" in src, f"{rel} 未接入共享锁"
    assert "locked_run" in (ROOT / "engine/marathon.py").read_text(encoding="utf-8")
    assert "RunLock" in (ROOT / "engine/runloop.py").read_text(encoding="utf-8")


def test_marathon_exits_3_when_lock_held(tmp_path, monkeypatch):
    """行为:锁被占时真跑 marathon 的 __main__ 入口 → SystemExit(3)。

    用 runpy 在进程内跑真入口(与 `python -m engine.marathon` 同一代码路径),
    避免派生孙子进程;断言 main() 未被执行(不会碰靶场)。
    """
    import runpy
    lp = tmp_path / ".aegis.lock"
    holder = RunLock(lp, DEFAULT_IDENTITY)
    assert holder.acquire(timeout_s=1.0)
    monkeypatch.setenv("AEGIS_LOCK_PATH", str(lp))
    monkeypatch.setenv("AEGIS_LOCK_TIMEOUT_S", "1")
    monkeypatch.setattr(sys, "argv",
                        ["engine.marathon", "--base", "http://127.0.0.1:8081",
                         "--instance", "a"])
    called = {"n": 0}
    import engine.marathon as M
    monkeypatch.setattr(M, "main", lambda: called.__setitem__("n", called["n"] + 1) or 0)
    try:
        with pytest.raises(SystemExit) as ei:
            runpy.run_module("engine.marathon", run_name="__main__")
        assert int(ei.value.code) == 3, ei.value.code
        assert called["n"] == 0, "锁被占时不得执行 marathon 主体"
    finally:
        holder.release()

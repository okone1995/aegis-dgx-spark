# -*- coding: utf-8 -*-
"""P0-1 回归锁:POST 白名单参数必须真到引擎进程(契约 §4.3)。

真机/代码事实双证据:后端曾只把 --run-id/--runs-root 传给子进程,
patch_provider/review_provider/strict_llm/cleanup_policy 只被写进 run.json
与 run.created 事件 —— 请求 retain 却实际 restore、请求换复核 provider 却仍走云端,
账本与执行不一致(撞 §0.7 诚实性硬线)。
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 与兄弟 console 面测试同一写法（test_demo_metrics / test_recover_and_fault_hooks）：
# 缺 fastapi/httpx 的环境整文件跳过。原先在收集期就 ImportError，
# 会把 `pytest tests/` 整轮打断（本机 conda env aegis 实测 0 条未跑）。
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
import console.backend.main as M  # noqa: E402


def test_spawn_passes_whitelist_params(monkeypatch, tmp_path):
    seen = {}

    class _P:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd
            seen["env"] = kw.get("env")
            self.pid = 1234

    monkeypatch.setattr(M.subprocess, "Popen", _P)
    monkeypatch.setattr(M, "_ensure_engine_importable", lambda: None)
    monkeypatch.setattr(M, "_active_demo_runs", {})
    M._spawn_demo_case("run-20260926-000000-abcd", patch_provider="qwen",
                       review_provider="local", strict_llm=False,
                       cleanup_policy="retain")
    cmd = seen["cmd"]
    joined = " ".join(cmd)
    assert "--review-provider local" in joined, cmd
    assert "--strict-llm false" in joined, cmd
    assert "--cleanup-policy retain" in joined, cmd
    assert "--patch-provider qwen" in joined, cmd
    # T7 实测:本机解释器忽略 PYTHONPATH 且 cwd 不进 sys.path →
    # 必须用显式 bootstrap(-c + sys.path.insert),否则引擎 import 不进去
    assert "-c" in cmd and "sys.path.insert" in " ".join(cmd), cmd
    assert str(ROOT) in " ".join(cmd)


def test_spawn_defaults_are_strict_and_restore(monkeypatch):
    seen = {}

    class _P:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd
            self.pid = 1

    monkeypatch.setattr(M.subprocess, "Popen", _P)
    monkeypatch.setattr(M, "_ensure_engine_importable", lambda: None)
    monkeypatch.setattr(M, "_active_demo_runs", {})
    M._spawn_demo_case("run-20260926-000000-abcd")
    joined = " ".join(seen["cmd"])
    assert "--strict-llm true" in joined
    assert "--cleanup-policy restore" in joined


def test_engine_cli_accepts_these_flags():
    """引擎侧必须有对应开关,否则透传也是空转。

    本机解释器 safe_path=True 且 PYTHONPATH 被忽略 → 用与后端相同的 bootstrap
    (`-c` + sys.path.insert)实跑一次,证明子进程能真的 import 到引擎。"""
    import subprocess as sp
    boot = ("import sys; sys.path.insert(0, {root!r}); "
            "from engine.demo_case import main; sys.exit(main(sys.argv[1:]))"
            ).format(root=str(ROOT))
    r = sp.run([sys.executable, "-c", boot, "--help"],
               cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-400:]
    for flag in ("--patch-provider", "--review-provider", "--strict-llm",
                 "--cleanup-policy"):
        assert flag in r.stdout, f"引擎 CLI 缺 {flag}"

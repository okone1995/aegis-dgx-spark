# -*- coding: utf-8 -*-
"""S6 真机注入发现的回归:僵尸进程必须判死。

来历(2026-09-27 真机):在 patch 阶段对 demo_case 发 SIGKILL,子进程变成僵尸(state=Z,
父进程未 wait)。原 `_pid_alive()` 用 os.kill(pid,0) 探测 —— 对僵尸仍返回成功,于是:
  * run 永远停在 running(200s 未转 interrupted);
  * 排他锁不释放;
  * 新建任务被 409 拦住,必须人工收尾。
本测试真造一个僵尸(仅 POSIX),断言 _pid_alive 判死。
"""
import os
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import run_lock as RL  # noqa: E402


@pytest.mark.skipif(not pathlib.Path("/proc").is_dir(),
                    reason="仅 POSIX(依赖 /proc/<pid>/stat)可造僵尸;Windows 上该分支不适用")
def test_pid_alive_treats_zombie_as_dead():
    pid = os.fork()
    if pid == 0:                      # 子进程:立刻退出,且父进程暂不 wait ⇒ 短暂僵尸
        os._exit(0)
    try:
        time.sleep(0.4)
        # 确认它真的是僵尸(否则本测试没测到东西,要报出来而不是假绿)
        with open("/proc/%d/stat" % pid) as fh:
            state = fh.read().rsplit(")", 1)[-1].split()[0]
        assert state == "Z", "前置条件不成立:子进程未处于僵尸态(实际 state=%s)" % state
        assert RL._pid_alive(pid) is False, "僵尸进程必须判死,否则强杀后锁与状态永不释放"
    finally:
        try:
            os.waitpid(pid, 0)        # 回收,避免给测试机留僵尸
        except OSError:
            pass


def test_pid_alive_false_for_nonexistent_pid():
    """不存在的大 pid 必须判死(两平台一致;方向安全)。"""
    assert RL._pid_alive(999999998) is False


def test_pid_alive_true_for_self():
    assert RL._pid_alive(os.getpid()) is True

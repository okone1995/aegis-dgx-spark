"""Aegis 决赛演示 —— T1 跨进程排他锁(契约冻结 §3、施工书 §3)。

锁文件位置:workspace/.aegis.lock。新入口、旧 CLI、旧 Console 共享
canonical 源码和靶场端口时必须持同一把锁;旧 threading.Lock 不足以
阻止第二个进程。

遗留锁纪律(契约 §3):遇到已存在的锁,读出 pid 核对进程是否存活——
存活则等待重试;死亡且 identity 核对失败则报 StaleLockError,
**不能直接删除锁抢跑**。锁的移除只允许持锁者本人 release()。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from typing import Optional


class LockBusyError(Exception):
    """锁被存活进程持有(acquire 超时)。"""


# 默认锁身份:后端服务与任务进程(demo_case)共用同一 identity——
# 这样崩溃遗留的锁可被另一方原子接手,不必人工删锁(跨 identity 的遗留
# 锁会报 StaleLockError 等人工处理,那是防两套服务互踩的硬线)。
DEFAULT_IDENTITY = "aegis-demo"


class StaleLockError(Exception):
    """锁文件属主进程已死但 identity 与当前申请者不匹配 —— 不允许静默删除抢锁。

    需人工核对后处理(删除或换 identity),本模块绝不代删。
    """


def _pid_alive(pid: int) -> bool:
    """进程存活探测,Windows/Linux 双平台。

    Windows:os.kill(pid, 0) 对不存在的 pid 抛 OSError(errno 22);
    对任意存在的 pid(含已退出但 pid 未复用/句柄未释放)返回成功。
    大 pid(如 999999998)在 Windows 上必然 OSError → 判死,方向安全。
    Linux:ESRCH=不存在,EPERM=存在但无权限。

    **2026-09-27 真机注入实测补充**:被 SIGKILL 但父进程尚未 wait 的子进程会变成
    僵尸(state=Z),此时 os.kill(pid,0) 仍成功,但该进程**已不再执行任何代码**。
    不把它判死的后果(已实测):任务被强杀 → run 永远停在 running、锁不释放、
    新建任务被 409 拦住,必须由人工收尾。故此处加 /proc 状态判定。
    """
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.path.isdir("/proc"):          # POSIX:先看进程状态,僵尸/已死一律判死
        try:
            with open("/proc/%d/stat" % pid, "r") as fh:
                raw = fh.read()
            # 形如 "1234 (comm) S 5678 ..." —— comm 可能含空格与括号,取最后一个 ')' 之后
            state = raw.rsplit(")", 1)[-1].split()[0]
            if state in ("Z", "X"):
                return False
        except OSError:
            return False                # /proc 无此 pid ⇒ 不存在
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:  # EPERM(Linux):进程存在但属别的用户
        return True
    except OSError:
        return False


def default_lock_path() -> pathlib.Path:
    """靶场锁位置:workspace/.aegis.lock(契约 §3)。

    新入口(engine.demo_case)、后端预检、旧 CLI(runloop/marathon/demo.sh)
    必须共用同一路径,否则锁形同虚设。
    环境变量 AEGIS_LOCK_PATH 可覆盖(测试与运维改址用)。
    """
    override = os.environ.get("AEGIS_LOCK_PATH")
    if override:
        return pathlib.Path(override)
    root = pathlib.Path(__file__).resolve().parent.parent
    return root / "workspace" / ".aegis.lock"


def locked_run(main_fn, *, lock_path=None, identity: str = DEFAULT_IDENTITY,
               timeout_s: float = None, label: str = "legacy") -> int:
    """在共享靶场锁下执行 main_fn —— 旧 CLI 的统一接入点。

    T7 复核 P0:旧 runloop/marathon/demo.sh 原先无锁,会在新入口
    attack/verify 期间重部署靶场,污染证据。抢不到锁时返回 3 并打印原因
    (明确非零,不冒认为业务失败)。

    等锁时长默认 120s,可用 AEGIS_LOCK_TIMEOUT_S 覆盖(测试/运维)。
    """
    if timeout_s is None:
        timeout_s = float(os.environ.get("AEGIS_LOCK_TIMEOUT_S") or 120.0)
    lk = RunLock(lock_path or default_lock_path(), identity)
    try:
        got = lk.acquire(timeout_s=timeout_s)
    except StaleLockError as e:
        print(f"[lock] {label}: 遗留锁需人工处理,拒绝启动: {e}", file=sys.stderr)
        return 3
    if not got:
        print(f"[lock] {label}: 靶场被其他任务占用({lk.lock_path}),拒绝启动——"
              f"不同时跑两个会重部署靶场的任务(契约 §3)", file=sys.stderr)
        return 3
    try:
        return int(main_fn() or 0)
    finally:
        lk.release()


class RunLock:
    """文件排他锁:os.open(O_CREAT|O_EXCL) 抢建 + {pid, identity, acquired_at}。

    支持 context manager;release() 只释放自己创建的锁(核对 pid)。
    """

    def __init__(self, lock_path: pathlib.Path, identity: str):
        self.lock_path = pathlib.Path(lock_path)
        self.identity = str(identity)
        self._held = False
        self._info: Optional[dict] = None

    # ---------- 抢锁 ----------
    def acquire(self, timeout_s: float = 30.0, poll_s: float = 0.2) -> bool:
        """尝试在 timeout_s 内取锁;成功 True,超时(锁被存活进程占着)False。

        锁文件已存在时读出属主探活:
        - 属主存活 → 等待重试(Windows 注意:对已退出但句柄未释放的 pid
          os.kill(pid,0) 仍成功——该情形表现为"等待→超时",方向安全,
          不会误删锁;Linux ESRCH/EPERM 语义准确)。
        - 属主死亡且 identity 匹配(本 identity 上次崩溃遗留)→ 原子接手,成功。
        - 属主死亡且 identity 不匹配 → StaleLockError,绝不静默删除。
        """
        deadline = time.monotonic() + max(0.0, timeout_s)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            fd = None
            try:
                # O_CREAT|O_EXCL:原子抢建;同刻只有一个进程成功
                fd = os.open(str(self.lock_path),
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                self._info = {
                    "pid": os.getpid(),
                    "identity": self.identity,
                    "acquired_at": time.time(),
                }
                os.write(fd, json.dumps(self._info).encode("utf-8"))
                self._held = True
                return True
            except FileExistsError:
                if self._try_takeover():
                    return True  # 遗留锁已接手,本进程即持锁者
                if time.monotonic() >= deadline:
                    return False
                time.sleep(poll_s)
            finally:
                if fd is not None:
                    os.close(fd)

    def _try_takeover(self) -> bool:
        """检查已存在的锁文件,返回 True=本进程已接手持锁。

        - 属主存活 → False(调用方继续等待)
        - 属主死亡且 identity 匹配(本 identity 上次崩溃遗留)→ 原子接手,True
        - 属主死亡且 identity 不匹配 → StaleLockError(人工处理,不代删)
        - 文件损坏读不出 pid → StaleLockError(宁可拒绝也不猜)
        """
        try:
            raw = self.lock_path.read_text(encoding="utf-8").strip()
            info = json.loads(raw)
            pid = int(info["pid"])
            holder_identity = str(info.get("identity", ""))
        except (OSError, ValueError, KeyError, TypeError):
            raise StaleLockError(
                f"lock file {self.lock_path} unreadable/corrupt — "
                f"manual inspection required, refusing to auto-remove") from None
        if _pid_alive(pid):
            return False
        # 属主已死:identity 核对
        if holder_identity != self.identity:
            raise StaleLockError(
                f"lock held by dead pid {pid} (identity={holder_identity!r}) "
                f"does not match requester identity {self.identity!r} — "
                f"manual resolution required, refusing to auto-remove")
        # 同 identity 遗留(本服务上次崩溃):安全接手。
        # T7 复核修正:原实现是“重写属主信息为本进程”(tmp + atomic_replace),
        # 两个同 identity 进程会先后覆盖同一锁文件、都认为自己持锁——存在真竞争窗口。
        # 现改为**原子 CAS**:先把旧锁文件 rename 到私有坟场名——
        # rename 是原子的,同一源路径只有一个进程能成功,失败者得到 FileNotFoundError;
        # 赢家再用 O_CREAT|O_EXCL 独占创建新锁,确保同一时刻只有一个持锁者。
        graveyard = self.lock_path.with_name(
            f"{self.lock_path.name}.stale.{os.getpid()}.{int(time.time() * 1000)}")
        try:
            os.rename(self.lock_path, graveyard)   # 原子:败者 FileNotFoundError
        except FileNotFoundError:
            return False                            # 别人先接手了 → 回等待循环
        except OSError:
            return False
        try:
            graveyard.unlink()                      # 清理坟场(失败不影响持锁)
        except OSError:
            pass
        try:
            fd2 = os.open(self.lock_path,
                          os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False                            # 已有活进程建了新锁 → 等待
        except OSError:
            return False
        try:
            self._info = {"pid": os.getpid(), "identity": self.identity,
                          "acquired_at": time.time()}
            os.write(fd2, json.dumps(self._info).encode("utf-8"))
        finally:
            os.close(fd2)
        self._held = True
        return True

    # ---------- 放锁 ----------
    def release(self) -> None:
        """释放锁:只删自己 pid 持有的锁文件。"""
        if not self._held:
            return
        try:
            raw = self.lock_path.read_text(encoding="utf-8").strip()
            info = json.loads(raw)
            if int(info.get("pid", -1)) != os.getpid():
                # 不是我们的锁(被人接管)——不动它
                return
        except (OSError, ValueError, TypeError):
            pass  # 读不出就不删,宁漏勿错
        try:
            self.lock_path.unlink()
        except OSError:
            pass
        finally:
            self._held = False

    # ---------- context manager ----------
    def __enter__(self) -> "RunLock":
        if not self.acquire():
            raise LockBusyError(
                f"failed to acquire {self.lock_path} — held by a live process")
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    @property
    def held(self) -> bool:
        return self._held

"""Cross-process transactions for small local JSONL stores.

OS-owned advisory locks survive neither process termination nor host restart.
All cooperating readers/writers use the same persistent sidecar lock file.
Malformed rows fail closed: an update never silently erases corrupt evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import time

_registry_guard = threading.Lock()
_registry = {}
_local = threading.local()


class StorageBusyError(TimeoutError):
    pass


class CorruptQueueError(ValueError):
    pass


@contextmanager
def file_mutex(path, timeout=30.0):
    path = Path(path).resolve()
    key = os.path.normcase(str(path))
    with _registry_guard:
        lock = _registry.setdefault(key, threading.RLock())
    deadline = time.monotonic() + timeout
    if not lock.acquire(timeout=max(0, timeout)):
        raise StorageBusyError(f"storage busy: {path.name}")
    held = getattr(_local, "held", None)
    if held is None:
        held = _local.held = set()
    stream = None
    acquired = False
    nested = key in held
    try:
        if nested:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a+b", buffering=0)
        if path.stat().st_size == 0:
            stream.write(b"\0")
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                held.add(key)
                break
            except (BlockingIOError, PermissionError, OSError):
                if time.monotonic() >= deadline:
                    raise StorageBusyError(f"storage busy: {path.name}") from None
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        yield
    finally:
        if acquired:
            held.discard(key)
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        if stream is not None:
            stream.close()
        lock.release()


def atomic_text(path, text):
    """Unique temp + fsync + atomic replace; caller owns the transaction lock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        from engine.demo_contracts import atomic_replace
        atomic_replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class JsonlStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def _read(self):
        if not self.path.exists():
            return []
        rows = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("row is not an object")
            except (ValueError, TypeError) as exc:
                raise CorruptQueueError(f"{self.path.name}: invalid row {number}") from exc
            rows.append(row)
        return rows

    def load(self):
        with file_mutex(self.lock_path):
            return self._read()

    def update(self, mutate):
        """Read/validate/mutate/replace is one cross-process transaction."""
        with file_mutex(self.lock_path):
            rows = self._read()
            result = mutate(rows)
            atomic_text(self.path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
            return result

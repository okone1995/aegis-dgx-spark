"""Aegis 决赛演示 —— T1 单写者 run 生命周期存储(契约冻结 §2.2/§2.3)。

目录契约:workspace/runs/<run_id>/{run.json, events.jsonl, flow.jsonl,
findings.json, alerts.json, judge.jsonl, patches/, verification.json,
learning-candidates.jsonl, summary.json, artifacts.json}。

写入纪律(§2.3):
  1. 事件先持久化(flush)再更新 run.json 快照;快照 last_seq 是 API 游标。
  2. cancel 只写独立标记文件 cancel.request,不动 run.json —— 所有权纪律规则 4。
  3. owner_generation 拒绝过期写者:任务进程接管时 generation+1,
     旧写者带过期 expected_generation 写入抛 StaleWriterError。

并发模型:单写者假设下,进程内 threading.Lock 保证事件 seq 与快照更新
的原子性;跨进程靠 demo_case 进程独占写 + RunLock 排他锁(见 run_lock.py)。
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
from typing import Any, Dict, List, Optional

from engine.demo_contracts import (
    SCHEMA_VERSION,
    DemoEvent,
    ContractError,
    TERMINAL_STATES,
    VALID_STAGES,
    atomic_replace,
    validate_event,
    validate_stage,
    validate_state_transition,
    valid_run_id,
    utcnow_iso,
    make_run_id,
)

# run.json 可由 update() 修改的白名单字段(契约 §2.2 全字段除只读项)
# 契约 §4.1 要求 run.json “至少包含”的字段:即使为 null 也要落盘。
CONTRACT_KEYS = ("schema_version", "run_id", "case_id", "state", "stage",
                 "created_at", "started_at", "ended_at", "heartbeat_at",
                 "last_seq", "pid", "process_identity", "exit_code",
                 "providers", "model_versions", "environment", "patch_mode",
                 "repair_outcome", "judge_status", "learning_status", "summary",
                 "error", "cleanup", "degraded_reasons")

_UPDATABLE_FIELDS = frozenset({
    "state", "stage", "started_at", "ended_at", "heartbeat_at",
    "pid", "process_identity", "exit_code", "providers", "model_versions",
    "environment", "patch_mode", "repair_outcome", "judge_status",
    "learning_status", "summary", "error", "cleanup",
    # 契约 §4.1:降级原因单独落盘(T7 真机实测:partial 轮次把它写进 update 时
    # 被白名单拒绝——字段名必须先在这里登记)
    "degraded_reasons",
    # 契约 §2.7/T10:终局原因(cancelled/interrupted/...)与 state 分开落盘 ——
    # cleanup 失败把 state 降 failed 时,原始中止原因必须仍可读(前端读 run.stop_reason)。
    "stop_reason",
})


class RunStoreError(Exception):
    """run 存储层错误(未知 run/非法 run_id/非法参数)。"""


class StaleWriterError(RunStoreError):
    """owner_generation 不匹配 —— 过期写者试图写入。"""


class _Meta:
    """run.json 的内存表示。RunMeta 字段见契约 §2.2 全字段。"""

    __slots__ = tuple(_UPDATABLE_FIELDS | {
        "schema_version", "run_id", "case_id", "created_at", "last_seq",
        "owner_generation", "client_request_id"})

    def __init__(self, **kw: Any) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    # -- 序列化 --
    def to_dict(self) -> Dict[str, Any]:
        # 契约 §4.1:run.json “至少包含”下列字段——**含空值也要落盘为 null**,
        # 否则前端/审阅只能看到 undefined(T7 真机实测:成功轮缺 error 键)。
        d = {k: getattr(self, k)
             for k in self.__slots__ if getattr(self, k) is not None}
        for k in CONTRACT_KEYS:
            d.setdefault(k, getattr(self, k, None))
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "_Meta":
        return cls(**{k: v for k, v in d.items() if k in cls.__slots__})


# RunMeta 别名:契约命名(§2.2 run.json 字段表);实现上与 _Meta 同体,
# 供 update()/create() 返回。保持 dataclass 语义由 to_dict/from_dict 承担。
RunMeta = _Meta


def _atomic_write_json(path: pathlib.Path, data: Dict[str, Any]) -> None:
    """run.json 原子替换:.tmp + 重试替换(blackboard.py 同款纪律)。

    Windows 实测:os.replace 偶发 WinError 5(Defender/句柄瞬时占用),
    单次替换会把一次正常 run 打成 internal_error —— 小退避重试三次。
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    atomic_replace(tmp, path)


class RunStore:
    """单写者 run 生命周期存储。所有路径都限制在 runs_root 之下。"""

    def __init__(self, runs_root: pathlib.Path):
        self.runs_root = pathlib.Path(runs_root)
        self._lock = threading.Lock()

    # ---------- 路径 ----------
    def _dir(self, run_id: str) -> pathlib.Path:
        if not valid_run_id(run_id):
            raise RunStoreError(f"invalid run_id: {run_id!r}")
        return self.runs_root / run_id

    def run_dir(self, run_id: str) -> pathlib.Path:
        return self._dir(run_id)

    # ---------- 生命周期 ----------
    def create(
        self,
        case_id: str,
        providers: Optional[Dict[str, str]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> RunMeta:
        """创建 queued 态 run,写 run.json(原子替换)+ 全套子目录。"""
        options = dict(options or {})
        client_request_id = options.pop("client_request_id", "") or ""
        with self._lock:
            self.runs_root.mkdir(parents=True, exist_ok=True)
            run_id = make_run_id()
            # 同毫秒冲突极小概率;碰撞则重生成,不依赖时间唯一性
            while (self.runs_root / run_id).exists():
                run_id = make_run_id()
            d = self.runs_root / run_id
            d.mkdir()
            for sub in ("patches",):
                (d / sub).mkdir()
            for f in ("flow.jsonl", "judge.jsonl", "learning-candidates.jsonl",
                      "events.jsonl"):
                (d / f).write_text("", encoding="utf-8")
            meta = _Meta(
                schema_version=SCHEMA_VERSION,
                run_id=run_id,
                case_id=case_id,
                state="queued",
                stage="preflight",
                created_at=utcnow_iso(),
                started_at=None,
                ended_at=None,
                heartbeat_at=utcnow_iso(),
                last_seq=0,
                pid=None,
                process_identity=None,
                exit_code=None,
                providers=dict(providers or {}),
                model_versions={},
                environment={},
                patch_mode=None,
                repair_outcome=None,
                judge_status="not_invoked",
                learning_status="not_run",
                summary={},
                error=None,
                cleanup="pending",
                owner_generation=1,
                client_request_id=client_request_id,
            )
            _atomic_write_json(d / "run.json", meta.to_dict())
            return meta

    def load(self, run_id: str) -> Optional[RunMeta]:
        """读取 run.json;未知/损坏返回 None。run_id 先过格式校验防穿越。"""
        d = self._dir(run_id)
        try:
            data = json.loads(
                (d / "run.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or "run_id" not in data:
            return None
        return _Meta.from_dict(data)

    def list_runs(self, limit: int = 50) -> List[RunMeta]:
        """按 created_at 倒序;坏行(run.json 损坏/缺字段)跳过。"""
        out: List[RunMeta] = []
        if not self.runs_root.exists():
            return out
        for p in sorted(self.runs_root.iterdir(), key=lambda x: x.name, reverse=True):
            if not p.is_dir():
                continue
            try:
                data = json.loads(
                    (p / "run.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            try:
                out.append(_Meta.from_dict(data))
            except Exception:  # noqa: BLE001 —— 坏行跳过契约
                continue
            if len(out) >= limit:
                break
        # 排序:created_at 倒序。**同毫秒创建的 tie 必须确定性**——否则
        # 同秒内两次 create 的顺序在快文件系统(Spark)上不稳定(T7 实测:
        # 验收用例断言 [m2,m1] 偶发翻转)。tiebreaker 用目录 mtime_ns。
        def _key(m):
            try:
                mt = (self.runs_root / m.run_id).stat().st_mtime_ns
            except OSError:
                mt = 0
            return (m.created_at or "", mt)
        out.sort(key=_key, reverse=True)
        return out

    def find_by_client_request_id(self, client_request_id: str) -> Optional[RunMeta]:
        """幂等键查重(POST /api/demo/runs client_request_id)。"""
        if not client_request_id:
            return None
        for m in self.list_runs(limit=1000):
            if m.client_request_id == client_request_id:
                return m
        return None

    # ---------- 事件 ----------
    def append_event(
        self,
        run_id: str,
        type: str,  # noqa: A002 —— 契约字段名
        stage: str,
        actor: str,
        expected_generation: Optional[int] = None,
        **fields: Any,
    ) -> DemoEvent:
        """追加事件:seq 在锁内自增;先写 events.jsonl(flush)再更新 run.json。

        事件 ts 用 utcnow_iso;last_seq 与 heartbeat_at 随快照刷新。
        """
        with self._lock:
            meta = self._load_or_raise(run_id)
            if expected_generation is not None and \
                    meta.owner_generation != expected_generation:
                raise StaleWriterError(
                    f"owner_generation mismatch: have {meta.owner_generation}, "
                    f"writer expected {expected_generation}")
            seq = int(meta.last_seq or 0) + 1
            e = DemoEvent(
                schema_version=SCHEMA_VERSION,
                run_id=run_id,
                seq=seq,
                event_id=f"{run_id}:{seq}",
                ts=utcnow_iso(),
                type=type,
                stage=stage,
                actor=actor,
                flow_id=str(fields.pop("flow_id", "") or ""),
                finding_id=str(fields.pop("finding_id", "") or ""),
                caused_by=str(fields.pop("caused_by", "") or ""),
                status=str(fields.pop("status", "") or ""),
                summary=str(fields.pop("summary", "") or ""),
                payload=dict(fields.pop("payload", {}) or {}),
                artifact_refs=list(fields.pop("artifact_refs", []) or []),
            )
            validate_event(e)
            line = json.dumps(e.to_dict(), ensure_ascii=False)
            with (self._dir(run_id) / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            # 快照:事件已持久化,现在才更新 last_seq + heartbeat(§2.3 规则 1)
            meta.last_seq = seq
            meta.heartbeat_at = utcnow_iso()
            _atomic_write_json(self._dir(run_id) / "run.json", meta.to_dict())
            return e

    def read_events(
        self,
        run_id: str,
        after: int = 0,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """读事件流:after 是"已见最大 seq"(返回 seq>after 的前 limit 条)。

        坏行跳过但保持 seq 顺序;返回 {events, next_seq, has_more, state}。
        """
        d = self._dir(run_id)
        if not (d / "run.json").exists():
            raise RunStoreError(f"unknown run: {run_id}")
        events: List[DemoEvent] = []
        has_more = False
        with (d / "events.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    e = DemoEvent.from_row(row)
                except (json.JSONDecodeError, ContractError):
                    continue  # 坏行跳过契约:半行/中断不当作成功事件
                if e.seq <= after:
                    continue
                if len(events) >= limit:
                    has_more = True
                    continue
                events.append(e)
        meta = self._load_or_raise(run_id)
        return {
            "events": [e.to_dict() for e in events],
            "next_seq": events[-1].seq if events else after,
            "has_more": has_more,
            "state": meta.state,
        }

    # ---------- 快照更新 ----------
    def update(
        self,
        run_id: str,
        expected_generation: Optional[int] = None,
        **fields: Any,
    ) -> RunMeta:
        """白名单字段更新。set_state 过 validate_state_transition;
        ended_at 只在终态可设。心跳自动刷新。"""
        with self._lock:
            meta = self._load_or_raise(run_id)
            if expected_generation is not None and \
                    meta.owner_generation != expected_generation:
                raise StaleWriterError(
                    f"owner_generation mismatch: have {meta.owner_generation}, "
                    f"writer expected {expected_generation}")
            bad = set(fields) - _UPDATABLE_FIELDS
            if bad:
                raise RunStoreError(f"non-updatable fields: {sorted(bad)}")

            if "state" in fields:
                new_state = fields["state"]
                validate_state_transition(meta.state, new_state)
                meta.state = new_state
            if "stage" in fields:
                validate_stage(fields["stage"])
                meta.stage = fields["stage"]
            if "ended_at" in fields:
                if meta.state not in TERMINAL_STATES:
                    raise RunStoreError(
                        f"ended_at only settable in terminal states, state={meta.state}")
                meta.ended_at = fields["ended_at"]
            for k, v in fields.items():
                if k in ("state", "stage", "ended_at"):
                    continue
                setattr(meta, k, v)
            meta.heartbeat_at = utcnow_iso()
            _atomic_write_json(self._dir(run_id) / "run.json", meta.to_dict())
            return meta

    # ---------- 取消(所有权纪律 §2.3 规则 4)----------
    def request_cancel(self, run_id: str) -> None:
        """写独立取消标记文件 cancel.request;不动 run.json。

        取消的执行(终止进程树/恢复/落终态)归任务进程或恢复器。
        """
        d = self._dir(run_id)
        if not (d / "run.json").exists():
            raise RunStoreError(f"unknown run: {run_id}")
        (d / "cancel.request").write_text(
            json.dumps({"requested_at": utcnow_iso()}, ensure_ascii=False),
            encoding="utf-8")

    def cancel_requested(self, run_id: str) -> bool:
        return (self._dir(run_id) / "cancel.request").exists()

    # ---------- 内部 ----------
    def _load_or_raise(self, run_id: str) -> RunMeta:
        meta = self.load(run_id)
        if meta is None:
            raise RunStoreError(f"unknown run: {run_id}")
        return meta

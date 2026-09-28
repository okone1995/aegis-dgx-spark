"""Aegis 决赛演示 —— T1 纯数据契约层(施工书 §4.1/§4.2、契约冻结 §2.1/§2.3)。

零第三方依赖:本模块只做常量表、dataclass、状态机校验、时间与 run_id 工具。
事件先落 events.jsonl 再更新 run.json 快照的所有权纪律由 run_store 落实;
本文件只定义"什么是合法的"。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

# ---- 任务状态机(§2.1)----
VALID_STATES = (
    "queued",      # 后端已创建,任务进程尚未接管
    "running",     # 任务进程已接管(从 run.started 起)
    "succeeded",   # 全部必需阶段通过
    "partial",     # 修复证据成立但主演示要求未全完成
    "failed",      # 预检/修补/审查/部署/核心验证/收尾失败
    "cancelled",   # 用户取消
    "interrupted",  # 任务进程意外消失
)
TERMINAL_STATES = ("succeeded", "partial", "failed", "cancelled", "interrupted")

# 非法状态迁移表(白名单之外全拒绝;终态只能自我等值)
_LEGAL_TRANSITIONS: Dict[str, frozenset] = {
    "queued": frozenset({"running", "cancelled", "failed"}),
    "running": frozenset({"succeeded", "partial", "failed", "cancelled", "interrupted"}),
    # 终态:succeeded/partial/failed/cancelled/interrupted → 仅自身(幂等重放)
    "succeeded": frozenset({"succeeded"}),
    "partial": frozenset({"partial"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
    "interrupted": frozenset({"interrupted"}),
}

# ---- 阶段枚举(§2.1)----
VALID_STAGES = (
    "preflight", "baseline", "attack", "classify", "patch",
    "review", "deploy", "verify", "learn", "cleanup",
)

# ---- 固定事件全表(§2.3,施工书 §4.2"至少")----
VALID_EVENTS = (
    "run.created", "run.started", "stage.started",
    "business.checked", "flow.captured", "detection.completed",
    "judge.completed", "patch.proposed", "gate.completed",
    "patch.applied", "verification.completed", "learning.queued",
    "cleanup.completed", "run.finished", "run.partial",
    "run.failed", "run.cancelled",
    # T10-S6/S7:故障注入登记事件 —— 被注入的那一轮必须在事件流里可见,
    # 否则注入跑出来的失败会被当成自然失败。
    "fault.injected",
    # T11/A:造招产物入 POC 候选队列的登记事件（旁路归档）。
    # 纪律:入队失败必须同样可见(poc.candidate_failed),不得静默吞掉;
    # 且入队 ≠ 入库 —— 写回 plugins/ 只能由操作员跑 tools/poc_apply.py。
    "poc.candidate_queued", "poc.candidate_failed",
    # T13:判官训练材料收集（旁路归档）。失败同样必须可见(jevtrain.collect_failed)。
    "jevtrain.collected", "jevtrain.collect_failed",
)
# 集成负责人扩展登记(2026-09-26,T6 偏离审计 B7):施工书 §4.2 列了 run.finished,
# 而 §4.1 的终态含 partial——旧表两者都无出口,demo_case 被迫用 run.failed 兼作
# partial 兜底,屏幕无法区分“部分完成”与“失败”。现补全为与 VALID_STATES 一一对应。
# run.failed / run.cancelled 是 T1 骨架落地失败/取消的可见出口(契约"失败事件保留错误类别"),
# T2 接入后亦可用 run.finished + status 表达同一事实。

# ---- 独立子状态(§2.1)----
REPAIR_OUTCOMES = ("verified", "rejected", "regressed", "inconclusive")
JUDGE_STATUSES = ("ok", "partial", "unavailable", "not_invoked")
LEARNING_STATUSES = ("queued", "partial", "failed", "not_run")
CLEANUP_STATES = ("pending", "running", "restored", "retained", "failed")

# ---- run_id 契约:服务端生成,读取时防路径穿越 ----
RUN_ID_RE = re.compile(r"^run-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
EVENT_ID_FMT = "{run_id}:{seq}"

# ts 契约:毫秒精度 ISO8601 UTC,Z 结尾(事件骨架示例即此格式)
_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class ContractError(ValueError):
    """契约校验失败(非法迁移/非法事件/非法枚举)。"""


class ContractEventTypeError(ContractError):
    """事件 type 不在固定事件表内。"""


def utcnow_iso() -> str:
    """当前 UTC 时间,毫秒精度,Z 结尾。例:2026-09-26T03:00:00.123Z"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def atomic_replace(src, dst, attempts: int = 5, backoff_s: float = 0.05) -> None:
    """原子替换 + 小退避重试。

    Windows 实测:os.replace 偶发 WinError 5(Defender 实时扫描/句柄瞬时占用),
    单次替换会把一次正常 run 打成 internal_error(PermissionError)。
    POSIX 无此问题,重试路径不影响语义。重试仍失败则原样抛出,
    不静默吞错(失败必须可见)。
    """
    import os
    import time
    last = None
    for i in range(max(1, attempts)):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:  # WinError 5 瞬时占用
            last = e
            time.sleep(backoff_s * (i + 1))
    raise last  # type: ignore[misc]


def make_run_id() -> str:
    """服务端生成 run_id:run-yyyymmdd-hhmmss-4 位随机 hex。

    随机后缀保证同秒并发创建不碰撞;读取侧只接受 RUN_ID_RE 形态。
    """
    import secrets
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"run-{stamp}-{secrets.token_hex(2)}"


def valid_run_id(run_id: str) -> bool:
    return isinstance(run_id, str) and bool(RUN_ID_RE.match(run_id))


def validate_state_transition(old: str, new: str) -> None:
    """校验 state 迁移;非法抛 ContractError。终态只能自我等值。"""
    if old not in VALID_STATES:
        raise ContractError(f"unknown state: {old!r}")
    if new not in VALID_STATES:
        raise ContractError(f"unknown state: {new!r}")
    legal = _LEGAL_TRANSITIONS[old]
    if new not in legal:
        raise ContractError(
            f"illegal state transition: {old} -> {new} "
            f"(legal: {sorted(legal)})")


def validate_stage(stage: str) -> None:
    if stage not in VALID_STAGES:
        raise ContractError(f"unknown stage: {stage!r} (legal: {list(VALID_STAGES)})")


@dataclass
class DemoEvent:
    """结构化事件骨架(施工书 §4.2 示例全字段)。payload 任意 JSON。"""
    schema_version: int
    run_id: str
    seq: int
    event_id: str
    ts: str
    type: str
    stage: str = ""
    actor: str = ""
    flow_id: str = ""
    finding_id: str = ""
    caused_by: str = ""
    status: str = ""
    summary: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    artifact_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "DemoEvent":
        """从 events.jsonl 的一行构造;字段缺失/类型不符抛 ContractError。"""
        try:
            return cls(
                schema_version=int(row["schema_version"]),
                run_id=str(row["run_id"]),
                seq=int(row["seq"]),
                event_id=str(row["event_id"]),
                ts=str(row["ts"]),
                type=str(row["type"]),
                stage=str(row.get("stage", "") or ""),
                actor=str(row.get("actor", "") or ""),
                flow_id=str(row.get("flow_id", "") or ""),
                finding_id=str(row.get("finding_id", "") or ""),
                caused_by=str(row.get("caused_by", "") or ""),
                status=str(row.get("status", "") or ""),
                summary=str(row.get("summary", "") or ""),
                payload=dict(row.get("payload") or {}),
                artifact_refs=list(row.get("artifact_refs") or []),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ContractError(f"bad event row: {e}") from e


def validate_event(e: DemoEvent) -> None:
    """事件校验:seq≥1、type 在表内、ts 是 ISO8601 UTC Z 毫秒、event_id==run_id:seq。"""
    if not isinstance(e.seq, int) or isinstance(e.seq, bool) or e.seq < 1:
        raise ContractError(f"seq must be int >= 1, got {e.seq!r}")
    if e.type not in VALID_EVENTS:
        raise ContractEventTypeError(
            f"unknown event type: {e.type!r} (legal: {list(VALID_EVENTS)})")
    if not _TS_RE.match(e.ts or ""):
        raise ContractError(
            f"ts must be ISO8601 UTC ms-precision '...T..:..:...mmmZ', got {e.ts!r}")
    if e.event_id != EVENT_ID_FMT.format(run_id=e.run_id, seq=e.seq):
        raise ContractError(
            f"event_id must be '{e.run_id}:{e.seq}', got {e.event_id!r}")
    if e.stage and e.stage not in VALID_STAGES:
        raise ContractError(f"unknown stage in event: {e.stage!r}")


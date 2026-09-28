"""Aegis 黑板数据契约 — 对应 SPEC §7 schema v1。

所有 agent 之间只通过黑板上的这些结构交换状态，不直接对话。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class FindingStatus(str, Enum):
    open = "open"            # 红方已确认
    patched = "patched"      # 补丁已应用到分支，待验证
    verified = "verified"    # 重放失败 + 功能测试绿（本周期终态）
    regressed = "regressed"  # 验证失败或后续轮次被绕过
    accepted = "accepted"    # 书面接受，转人工（最终态）


class HistoryEvent(BaseModel):
    round: int
    event: str  # found / open->patched / patched->verified / ...
    detail: str = ""
    ts: str = Field(default_factory=utcnow)


class Evidence(BaseModel):
    replay_ref: str = ""       # 回放请求文件引用
    marker: str = ""           # 成功判定命中的响应特征
    request_summary: str = ""  # 一行请求摘要（方法+路径+参数名）
    marker_hit: Optional[bool] = None  # 飞轮埋点：确定性 marker 是否命中（judge 训练标签源①）


class DetectionInfo(BaseModel):
    red: bool = True
    blue_alert_ref: str = ""   # 蓝方告警 id，双视角关联键


class Finding(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(pattern=r"^F-\d{3,}$")
    class_: str = Field(alias="class")  # 插件类别，如 upload_bypass
    plugin_version: str = "0.1.0"
    endpoint: str
    evidence: Evidence = Field(default_factory=Evidence)
    severity: Severity
    detected_by: DetectionInfo = Field(default_factory=DetectionInfo)
    status: FindingStatus = FindingStatus.open
    retries: int = 0
    history: List[HistoryEvent] = Field(default_factory=list)


class Alert(BaseModel):
    """蓝方告警 — secaudit-detect 产出。"""
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(pattern=r"^A-\d{3,}$")
    class_: str = Field(alias="class")
    endpoint: str
    flow_ref: str = ""         # flow.jsonl 行号/偏移
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""        # LLM 判定理由（规则命中则为规则 id）
    label: Optional[str] = None        # 飞轮埋点：attack/benign（红方 finding 关联或良性发生器，标签源②）
    finding_link: str = ""             # 与 finding 的关联键（双视角同源）
    ts: str = Field(default_factory=utcnow)


class PatchRecord(BaseModel):
    """secaudit-patch 产出。"""
    finding_id: str
    patch_file: str            # patches/F-xxx.patch
    branch: str                # patch/F-xxx
    template_id: str           # 插件包模板引用
    model: str                 # 生成者：qwen38-flash / step-3.5-flash / specialist
    verify_outcome: Optional[str] = None  # 飞轮埋点：verified/regressed（judge 补丁预检训练标签源③）
    ts: str = Field(default_factory=utcnow)


class VerifyResult(BaseModel):
    """secaudit-verify 门禁结果。"""
    finding_id: str
    round: int
    replay_blocked: bool       # 攻击重放必须失败
    tests_passed: bool         # 功能测试必须全绿
    detail: str = ""
    ts: str = Field(default_factory=utcnow)

    @property
    def passed(self) -> bool:
        return self.replay_blocked and self.tests_passed

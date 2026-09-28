"""黑板 — agent 间唯一契约，findings.json 状态机驱动（SPEC §5.3/§7）。

状态机：
    open ──patch──▶ patched ──verify 通过──▶ verified
      │                │ verify 失败
      │ accept         ▼
      ▼            regressed ──重试(≤max_retries)──▶ patched
    accepted ◀────────┘ 超限/接受
    verified ──后续轮次被变异绕过──▶ open（re-found）
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional

from .models import Finding, FindingStatus, utcnow

TRANSITIONS: Dict[str, set] = {
    "open": {"patched", "accepted"},
    "patched": {"verified", "regressed"},
    "regressed": {"patched", "accepted"},
    "verified": {"open"},
    "accepted": set(),
}

DEFAULT_MAX_RETRIES = 2


class BlackboardError(Exception):
    pass


class Blackboard:
    def __init__(self, workspace: Path, max_retries: int = DEFAULT_MAX_RETRIES):
        self.workspace = Path(workspace)
        self.findings_path = self.workspace / "findings.json"
        self.audit_path = self.workspace / "audit-log.jsonl"
        self.rounds_dir = self.workspace / "rounds"
        self.patches_dir = self.workspace / "patches"
        self.max_retries = max_retries
        self._lock = threading.Lock()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.rounds_dir.mkdir(exist_ok=True)
        self.patches_dir.mkdir(exist_ok=True)
        self._findings: Dict[str, Finding] = self._load()

    # ---------- 持久化 ----------
    def _load(self) -> Dict[str, Finding]:
        if not self.findings_path.exists():
            return {}
        data = json.loads(self.findings_path.read_text(encoding="utf-8"))
        return {f["id"]: Finding.model_validate(f) for f in data.get("findings", [])}

    def _save(self) -> None:
        payload = {"findings": [f.model_dump(by_alias=True) for f in self._findings.values()]}
        tmp = self.findings_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # T7:Windows 下 os.replace 偶发 WinError 5(Defender/句柄瞬时占用),
        # 单次替换会把一次正常 run 打成 PermissionError——统一走带退避重试的
        # atomic_replace(该 flake 已在 demo_case/run_store 实测到)。
        from .demo_contracts import atomic_replace
        atomic_replace(tmp, self.findings_path)  # 原子替换(带重试)

    def audit(self, actor: str, event: str, **detail) -> None:
        rec = {"ts": utcnow(), "actor": actor, "event": event, **detail}
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------- 查询 ----------
    def get(self, finding_id: str) -> Finding:
        if finding_id not in self._findings:
            raise BlackboardError(f"unknown finding {finding_id}")
        return self._findings[finding_id]

    def all(self) -> List[Finding]:
        return list(self._findings.values())

    def by_status(self, status: FindingStatus) -> List[Finding]:
        return [f for f in self._findings.values() if f.status == status]

    def counts(self) -> Dict[str, int]:
        c: Dict[str, int] = {}
        for f in self._findings.values():
            c[f.status.value] = c.get(f.status.value, 0) + 1
        return c

    # ---------- 写操作 ----------
    def add_finding(self, finding: Finding, round_: int = 1, actor: str = "secaudit-attack") -> Finding:
        with self._lock:
            if finding.id in self._findings:
                raise BlackboardError(f"duplicate finding {finding.id}")
            finding.history.append(_hist(round_, "found", actor))
            self._findings[finding.id] = finding
            self._save()
            self.audit(actor, "finding.added", finding_id=finding.id, cls=finding.class_)
            return finding

    def transition(
        self,
        finding_id: str,
        new_status: FindingStatus,
        round_: int,
        detail: str = "",
        actor: str = "secaudit-run",
    ) -> Finding:
        """执行状态机流转；regressed→patched 超过重试上限时自动转 accepted。"""
        with self._lock:
            f = self.get(finding_id)
            old = f.status.value
            if new_status.value not in TRANSITIONS[old]:
                raise BlackboardError(f"illegal transition {old} -> {new_status.value} ({finding_id})")

            forced = ""
            if old == "regressed" and new_status == FindingStatus.patched:
                if f.retries >= self.max_retries:
                    new_status = FindingStatus.accepted
                    forced = f"retries {f.retries} >= max {self.max_retries}, auto-accepted"
                else:
                    f.retries += 1

            f.status = new_status
            f.history.append(_hist(round_, f"{old}->{new_status.value}", detail or forced or actor))
            self._save()
            self.audit(
                actor, "finding.transition",
                finding_id=finding_id, **{"from": old, "to": new_status.value},
                detail=detail or forced,
            )
            return f

    def snapshot_round(self, round_: int, counts_override: dict = None) -> Path:
        """轮次结束快照 — 报告与收敛曲线的数据源。

        counts_override：基线快照（round 0，攻击前）用——黑板此时还没有 findings，
        但"8 类在战前可被利用"是插件清单给出的事实，收敛曲线需要这个起点。"""
        path = self.rounds_dir / f"round_{round_:03d}.json"
        counts = self.counts() if counts_override is None else counts_override
        path.write_text(
            json.dumps(
                {"round": round_, "ts": utcnow(), "counts": counts,
                 "findings": [{"id": f.id, "class": f.class_, "status": f.status.value}
                              for f in self._findings.values()]},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        return path


def _hist(round_: int, event: str, detail: str = ""):
    from .models import HistoryEvent
    return HistoryEvent(round=round_, event=event, detail=detail)

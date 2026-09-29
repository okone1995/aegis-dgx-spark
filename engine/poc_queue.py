"""POC 候选队列 —— 造招产物的**证据化归档 + 人工审核写回**（T11/A）。

为什么需要它（安全专家提的问题）：
  现在的 POC 全在 `plugins/<class>/payloads/` 里，是人工资产；`attacker.plan` /
  `plan_free` 能造新形态，但产物**用完即弃**，既不落库也无从审核 ——
  于是系统的攻击面只会随人工维护而变化，不会"自己长"。

本模块只做三件事，且**绝不自动改 plugins/**：
  1. 入队：造招产物（含来源：母弹/变换/freeform）写入候选队列，**脱敏后**落盘；
  2. 取证：候选真被打过之后，把"是否命中 marker + 真实交换摘要"挂上去；
  3. 审核：只有 `evidence_state=confirmed` 的候选才允许 approve（**未命中不得入库**）。

写回由操作员显式执行 `tools/poc_apply.py`（默认 dry-run），本模块不写 plugins。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Dict, List, Optional
from engine.storage import JsonlStore

ROOT = pathlib.Path(__file__).resolve().parent.parent
_DATASET = ROOT / "dataset"
if str(_DATASET) not in sys.path:
    sys.path.insert(0, str(_DATASET))

from engine.learning_queue import (  # noqa: E402  复用脱敏与规范化哈希（同一套口径）
    input_hash,
    scrub_v2,
)

DEFAULT_QUEUE = ROOT / "dataset" / "review_queue" / "poc_candidates.jsonl"

SOURCES = ("menu", "freeform")
EVIDENCE_STATES = ("untried", "confirmed", "miss")


class PocQueueError(RuntimeError):
    pass


def _utcnow() -> str:
    from engine.demo_contracts import utcnow_iso
    return utcnow_iso()


def normalize_payload_text(request_text: str, base: str = "") -> str:
    """把一条**真实交换的请求文本**规范成可入库的 payload：

    - `Cookie: ...` → `Cookie: {{SESSION}}`（票据永不进库）
    - 目标 base → `{{TARGET}}`
    - 保证首行是 `METHOD path HTTP/1.1`
    """
    text = scrub_v2(request_text or "")
    text = re.sub(r"(?im)^Cookie:\s*\[SCRUB\]\s*$", "Cookie: {{SESSION}}", text)
    text = re.sub(r"(?im)^Cookie:\s*.*$", "Cookie: {{SESSION}}", text)
    if base:
        text = text.replace(base.rstrip("/"), "{{TARGET}}")
    # 兜底：请求行里若仍是绝对 URL，把 scheme+host 换成 {{TARGET}}（宿主地址绝不进库）
    text = re.sub(r"(?m)^([A-Z]+)\s+https?://[^/\s]+(\S*)\s+(HTTP/1\.[01])$",
                  r"\1 {{TARGET}}\2 \3", text)
    lines = [ln for ln in text.split("\n")]
    if lines and not re.match(r"^[A-Z]+\s+\S+\s+HTTP/1\.[01]$", lines[0].strip()):
        raise PocQueueError("payload 首行不是 METHOD path HTTP/1.1")
    return "\n".join(lines).rstrip("\n") + "\n"


def split_request(request_text: str) -> tuple[str, str, str]:
    """(method, path, body) —— 与 learning_queue 同一口径，用于去重哈希。"""
    from engine import attacker
    method, pathq, head, body = attacker._split(request_text)
    return method, pathq, body or ""


class PocQueue:
    """跨进程事务 JSONL 候选队列。"""

    def __init__(self, queue_path: pathlib.Path = DEFAULT_QUEUE):
        self.path = pathlib.Path(queue_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------- 读 ----------
    def load(self) -> List[dict]:
        return JsonlStore(self.path).load()

    def get(self, candidate_id: str) -> Optional[dict]:
        for r in self.load():
            if r.get("candidate_id") == candidate_id:
                return r
        return None

    def stats(self) -> dict:
        out = {"total": 0, "pending": 0, "approved": 0, "rejected": 0,
               "duplicates": 0, "by_evidence": {}, "by_source": {}}
        for r in self.load():
            out["total"] += 1
            st = r.get("review_state", "pending")
            out[st] = out.get(st, 0) + 1
            if r.get("exclusion_reason") == "duplicate":
                out["duplicates"] += 1
            ev = r.get("evidence_state", "untried")
            out["by_evidence"][ev] = out["by_evidence"].get(ev, 0) + 1
            src = r.get("source", "menu")
            out["by_source"][src] = out["by_source"].get(src, 0) + 1
        return out

    # ---------- 写 ----------
    def append(self, candidate: dict) -> dict:
        """入队。同 poc_hash + class + intent 已存在 → 返回原行并标 duplicate。"""
        c = dict(candidate)
        cls = c.get("class") or ""
        if not cls:
            raise PocQueueError("缺少 class")
        src = c.get("source") or "menu"
        if src not in SOURCES:
            raise PocQueueError(f"未知 source: {src!r}（只接受 {SOURCES}）")
        text = normalize_payload_text(c.get("request_text") or "", c.get("base") or "")
        c["request_text"] = text
        c["base"] = ""  # base 已替换为 {{TARGET}}，不再留宿主信息
        method, path, body = split_request(text)
        c["method"], c["path"], c["body"] = method, path, body
        ph = input_hash(method, path, body)
        c["poc_hash"] = ph
        c.setdefault("captured_at", _utcnow())
        c.setdefault("evidence_state", "untried")
        c.setdefault("review_state", "pending")
        if not c.get("candidate_id"):
            c["candidate_id"] = f"{cls}-{ph[:12]}"
        if c["evidence_state"] not in EVIDENCE_STATES:
            raise PocQueueError(f"非法 evidence_state: {c['evidence_state']!r}")
        def change(rows):
            for existing in rows:
                if (existing.get("poc_hash") == ph and existing.get("class") == cls
                        and existing.get("intent") == c.get("intent")):
                    return {"duplicate": True, "existing": existing}
            rows.append(c)
            return {"duplicate": False, "candidate": c}
        return JsonlStore(self.path).update(change)

    def _mutate(self, candidate_id: str, fn) -> dict:
        def change(rows):
            hit = next((r for r in rows if r.get("candidate_id") == candidate_id), None)
            if hit is None:
                raise KeyError(f"unknown candidate_id: {candidate_id}")
            fn(hit)
            return hit
        return JsonlStore(self.path).update(change)

    # ---------- 取证与审核 ----------
    def confirm(self, candidate_id: str, exchange: dict, markers_hit: List[str],
                origin_run: str = "", origin_flow: str = "") -> dict:
        """挂上"真打过且命中"的证据。**命中为空必须拒绝**（不得把没打中的说成有效）。"""
        if not markers_hit:
            raise PocQueueError(
                "confirm 需要非空 markers_hit —— 未命中的候选只能留 pending/untried")
        def _apply(hit: dict) -> None:
            if hit.get("evidence_state") == "confirmed":
                raise PocQueueError(f"{candidate_id} 已 confirmed（取证单向）")
            hit["evidence_state"] = "confirmed"
            hit["evidence"] = {
                "origin_run": origin_run,
                "origin_flow": origin_flow,
                "markers_hit": list(markers_hit),
                "status": (exchange or {}).get("status"),
                "method": (exchange or {}).get("method"),
                "path": (exchange or {}).get("path"),
                "response_excerpt": scrub_v2(str((exchange or {}).get("response") or ""))[:600],
                "captured_at": _utcnow(),
            }
        return self._mutate(candidate_id, _apply)

    def approve(self, candidate_id: str, reviewer: str, note: str = "") -> dict:
        """审核通过。**只有 confirmed 才可 approve** —— 未命中的候选永远进不了库。"""
        if not reviewer or not reviewer.strip():
            raise PocQueueError("approve 需要 reviewer")
        def _apply(hit: dict) -> None:
            if hit.get("review_state") != "pending":
                raise PocQueueError(
                    f"{candidate_id} already {hit.get('review_state')} —— 审核状态单向")
            if hit.get("evidence_state") != "confirmed":
                raise PocQueueError(
                    f"{candidate_id} evidence_state={hit.get('evidence_state')!r} ⇒ "
                    "无命中证据不得入库（fail-closed）")
            hit["review_state"] = "approved"
            hit["reviewer"] = reviewer
            hit["review_note"] = note
            hit["reviewed_at"] = _utcnow()
        return self._mutate(candidate_id, _apply)

    def reject(self, candidate_id: str, reviewer: str, note: str = "") -> dict:
        if not reviewer or not reviewer.strip():
            raise PocQueueError("reject 需要 reviewer")
        def _apply(hit: dict) -> None:
            if hit.get("review_state") != "pending":
                raise PocQueueError(
                    f"{candidate_id} already {hit.get('review_state')} —— 审核状态单向")
            hit["review_state"] = "rejected"
            hit["reviewer"] = reviewer
            hit["review_note"] = note
            hit["reviewed_at"] = _utcnow()
        return self._mutate(candidate_id, _apply)

    def approved(self) -> List[dict]:
        return [r for r in self.load() if r.get("review_state") == "approved"]


def record_variant(queue: PocQueue, cls: str, variant: dict, base: str = "",
                   origin_run: str = "", origin_flow: str = "") -> dict:
    """把一条造招产物写进候队列（menu 或 freeform 都走这里）。"""
    text = variant.get("text") or variant.get("request_text") or ""
    return queue.append({
        "class": cls,
        "source": variant.get("source") or ("freeform" if variant.get("transform_id") == "free"
                                            else "menu"),
        "parent": variant.get("parent"),
        "transform_id": variant.get("transform_id"),
        "intent": variant.get("intent") or "attack",
        "request_text": text,
        "base": base,
        "origin_run": origin_run,
        "origin_flow": origin_flow,
        "rationale": variant.get("rationale"),
    })

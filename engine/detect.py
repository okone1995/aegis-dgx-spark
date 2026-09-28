"""蓝方确定性检测 —— 签名规则扫描 flow.jsonl（SPEC §10 四层检测栈第一层）。

规则来源：plugins/*/signatures/rules.yaml；命中即告警；
低置信规则命中同样出告警（confidence 透传，由上层决定是否升级 judge）。
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List

import yaml

PLUGINS_DIR = pathlib.Path(__file__).resolve().parent.parent / "plugins"


# ---- T3 纯观测接口（契约 §2.5：真值隔离）----
# Observation 只携带观测面（method/path/body/status/response）。
# label / marker_hits / finding_id / phase / expected_verdict 等真值字段
# 一律不得进入本结构 —— 它们是案例意图记录，不是检测输入。
@dataclass
class Observation:
    method: str
    path: str
    body: str = ""
    status: int = 0
    response: str = ""


_OBS_KEYS = ("method", "path", "body", "status", "response")


def coerce_observation(src) -> "Observation":
    """把 dict / Observation 统一成 Observation。

    A02 真值隔离：dict 输入只按 _OBS_KEYS 白名单取观测字段，
    label/marker_hits/finding_id/phase/expected_verdict 一律丢弃——
    改变外部真值不可能改变送判输入。
    """
    if isinstance(src, Observation):
        return src
    if isinstance(src, dict):
        return Observation(method=str(src.get("method", "") or ""),
                           path=str(src.get("path", "") or ""),
                           body=str(src.get("body", "") or ""),
                           status=int(src.get("status", 0) or 0),
                           response=str(src.get("response", "") or ""))
    raise TypeError(f"unsupported observation source: {type(src)!r}")


def scan_observation(obs, rules: List[dict] = None,
                     plugins_root: pathlib.Path = PLUGINS_DIR) -> dict:
    """对单条纯观测流扫描签名规则（A02/A03：正常流也扫，不预过滤）。

    与 scan_flow 的区别：输入是 Observation（或仅含观测键的 dict），
    无 label 可跳过、无 flow 行号可引用；输出不含 label/finding_link 真值字段。
    matched=False 只说明无规则命中，不等于正常。规则匹配逻辑与 scan_flow
    完全同源（同一 _flow_text 与 pattern）。
    """
    obs = coerce_observation(obs)
    rules = rules or load_rules(plugins_root)
    entry = {"method": obs.method, "path": obs.path, "body": obs.body,
             "status": obs.status, "response": obs.response}
    alerts: List[dict] = []
    n = 0
    for rule in rules:
        text = _flow_text(entry, rule.get("where", "request_path"))
        if text and re.search(rule["pattern"], text):
            n += 1
            alerts.append({
                "id": f"O-{n:03d}",
                "class": rule["class"],
                "endpoint": obs.path.split("?")[0],
                "confidence": float(rule.get("confidence", 0.5)),
                "reasoning": f"rule:{rule['id']}",
            })
    return {
        "matched": bool(alerts),
        "alerts": alerts,
        "scanned_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
    }


def load_rules(plugins_root: pathlib.Path = PLUGINS_DIR) -> List[dict]:
    rules = []
    for rf in sorted(plugins_root.glob("*/signatures/rules.yaml")):
        data = yaml.safe_load(rf.read_text(encoding="utf-8")) or {}
        for r in data.get("rules", []):
            r["_plugin"] = rf.parent.name
            rules.append(r)
    return rules


def _flow_text(entry: dict, where: str) -> str:
    if where == "request_query":
        path = entry.get("path", "")
        return path.split("?", 1)[1] if "?" in path else ""
    if where == "request_path":
        return entry.get("path", "").split("?", 1)[0]
    if where == "request_body":
        return entry.get("body", "")
    if where == "response_body":
        return entry.get("response", "")
    return ""


def scan_flow(flow_path: pathlib.Path, rules: List[dict] = None,
              plugins_root: pathlib.Path = PLUGINS_DIR) -> List[dict]:
    """扫描 flow.jsonl → 告警列表（A-xxx 编号，含 finding 关联占位）。"""
    rules = rules or load_rules(plugins_root)
    alerts: List[dict] = []
    n = 0
    with open(flow_path, encoding="utf-8") as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("label") == "benign":
                continue  # 良性流量不产生攻击告警（误报率统计另走评测）
            for rule in rules:
                text = _flow_text(entry, rule.get("where", "request_path"))
                if text and re.search(rule["pattern"], text):
                    n += 1
                    alerts.append({
                        "id": f"A-{n:03d}",
                        "class": rule["class"],
                        "endpoint": entry.get("path", "").split("?")[0],
                        "flow_ref": str(idx),
                        "confidence": float(rule.get("confidence", 0.5)),
                        "reasoning": f"rule:{rule['id']}",
                        "label": "attack",
                        "finding_link": "",
                    })
    return alerts


def link_findings(alerts: List[dict], findings_by_class: Dict[str, str]) -> List[dict]:
    """按漏洞类别把告警关联到 finding（双视角同源键）。"""
    for a in alerts:
        fid = findings_by_class.get(a["class"])
        if fid:
            a["finding_link"] = fid
    return alerts

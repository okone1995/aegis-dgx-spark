# -*- coding: utf-8 -*-
"""契约锁测试:事件 payload ↔ 前端读取字段 必须成对存在。

来历:2026-09-26 偏离审计(MF1)发现前端读了 8 个后端从不 emit 的字段,
后果是真实成功轮在屏幕上显示 0/N VERIFIED、门禁 OPEN、p_attack 0% BENIGN、
业务回归恒 PASS —— 屏幕在撒谎。

T7 对照计划书补强(审阅员 M1):原锁只断言「CONTRACT 表里的键在事件中存在」,
从不解析 demo/index.html 推导前端**实际读取**的键 —— 单向锁,测不出漂移
(business.checked 就是活例:表里列对了一组,前端读的是另一组)。
现增 `test_frontend_source_reads_match_emitted`:从 demo/index.html 各 case 块
反推 p.<key> 读取,与实发 payload 对拍;新读完但引擎不发的键必须进
FRONTEND_OPTIONAL 白名单并写明理由。

按前端实际读取的表逐项断言事件里真有这些字段。

维护方式:前端每新增一处 `p.<字段>` 读取,必须在本表加一行;改字段名时两处同改。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_demo_case import build_env  # noqa: E402  共用替身环境

# 事件类型 → 前端读取的键(可在 payload 或事件顶层)
CONTRACT = {
    "run.created": ["case_id", "providers", "cleanup_policy"],
    "run.started": ["case_id", "base"],
    "stage.started": ["stage"],
    "business.checked": ["intent_label", "keyword", "ok", "reason"],
    # 注意:引擎发 markers_hit(与 flow.jsonl 同拼写),前端兼容读 markers_hit/marker_hits
    "flow.captured": ["flow_id", "intent_label", "outcome", "markers_hit"],
    "detection.completed": ["flow_id", "matched", "alerts", "scanner"],
    "judge.completed": ["flow_id", "path", "p_attack", "verdict", "judge_status",
                        "input_hash", "protocol_id", "model_version"],
    "patch.proposed": ["finding_id", "sha256", "provider", "call_id", "diff"],
    "gate.completed": ["finding_id", "gates", "pass", "reasons", "provider",
                       "call_id"],
    "patch.applied": ["finding_id", "sha256", "backup"],
    "verification.completed": ["blocked", "auth_valid", "marker_hit",
                               "network_error", "exchanges", "business",
                               "business_pass", "replay_hit", "replay_total",
                               "repair_outcome", "finding_id"],
    "learning.queued": ["queued", "duplicates", "count"],
    "cleanup.completed": ["policy", "status"],
}
ENVELOPE = ["run_id", "seq", "event_id", "ts", "type", "stage", "actor"]


def _events(inst):
    rows = []
    with (inst.run_dir / "events.jsonl").open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def test_event_envelope_complete(tmp_path, monkeypatch):
    inst, _state, _canon = build_env(tmp_path, monkeypatch)
    inst.run()
    for ev in _events(inst):
        missing = [k for k in ENVELOPE if ev.get(k) in (None, "")]
        assert not missing, f"事件 {ev.get('type')} 缺信封字段 {missing}"


def test_payload_fields_frontend_reads_are_emitted(tmp_path, monkeypatch):
    """前端读取表 ↔ 实发事件字段 逐项对齐(MF1 回归锁)。"""
    inst, _state, _canon = build_env(tmp_path, monkeypatch)
    inst.run()
    events = _events(inst)
    by_type = {}
    for ev in events:
        by_type.setdefault(ev["type"], []).append(ev)

    problems = []
    for ev_type, keys in CONTRACT.items():
        rows = by_type.get(ev_type)
        if not rows:
            problems.append(f"{ev_type}: 本轮未发出")
            continue
        # 该类型的**每一条**事件都必须能供前端渲染(逐条查,不只看第一条)
        for ev in rows:
            payload = ev.get("payload") or {}
            missing = [k for k in keys
                       if k not in payload and k not in ev]
            if missing:
                problems.append(f"{ev_type}(seq={ev['seq']}): 缺 {missing}")
    assert not problems, "前端读取与事件字段不一致:\n  " + "\n  ".join(problems)


def test_detection_honesty_benign_flow_matched_false(tmp_path, monkeypatch):
    """A03 诚实性:正常业务流也进检测,但必须 matched=false,前端据此显示
    “规则沉默”而不是“检出未知类”。"""
    inst, _state, _canon = build_env(tmp_path, monkeypatch)
    inst.run()
    det = [e for e in _events(inst) if e["type"] == "detection.completed"]
    assert det, "正常流也必须进检测(A03)"
    benign = [e for e in det if (e.get("payload") or {}).get("intent_label") == "benign"]
    assert benign, "应有正常流的判读记录"
    for e in benign:
        assert (e["payload"] or {}).get("matched") is False, \
            f"正常搜索不应有签名命中: {e['payload']}"


def test_judge_payload_carries_verdict_not_just_status(tmp_path, monkeypatch):
    """M1 回归锁:判官事件必须带逐流裁决,顺序错位/字段缺失都会让表盘显示 BENIGN。"""
    inst, _state, _canon = build_env(tmp_path, monkeypatch)
    inst.run()
    judged = [e for e in _events(inst) if e["type"] == "judge.completed"]
    assert judged, "应有逐流判官裁决事件"
    for e in judged:
        p = e["payload"]
        assert p.get("p_attack") is not None, f"缺 p_attack: {p}"
        assert p.get("verdict") in ("attack", "benign", "abstain"), p.get("verdict")
        assert "/" in str(p.get("path") or "") or p.get("path"), "缺 path"


def test_verification_payload_has_three_state_business(tmp_path, monkeypatch):
    """M4 回归锁:业务回归必须是布尔三态,不能让前端把缺字段渲染成 PASS。"""
    inst, _state, _canon = build_env(tmp_path, monkeypatch)
    inst.run()
    ver = [e for e in _events(inst) if e["type"] == "verification.completed"][0]
    p = ver["payload"]
    assert p["business_pass"] is True
    assert isinstance(p["replay_total"], int) and p["replay_total"] >= 1
    assert isinstance(p.get("business"), list) and p["business"], "逐条业务断言应保留"


# 前端确实读、但引擎**故意不发**的键：必须逐条写明理由（缺证据显示未知,
# 不得折算为通过/良性）。新增条目需同时确认前端对该缺失有诚实处理。
FRONTEND_OPTIONAL = {
    "reasoning": "LLM 思考流走 llm-live.json/SSE,不重复进事件;前端 if(p.reasoning) 守护",
    "candidate_id": "前端 `p.candidate_id||fid` 回退到 finding_id",
    "diff_stats": "可选摘要;不回退到任何数字",
    "decision": "gate 事件另有 pass 布尔;前端 `p.decision===...||p.pass===true` 多路径",
    "kind": "可选语义提示;前端有 intent_label/outcome 主路径",
    "marker_hits": "旧名兼容(p.markers_hit 为主),不依赖",
    "endpoint": "见 method/path 主路径;business.checked 已实发 endpoint",
    "total": "snapshot 计数键;前端有 findings 累积回退",
    "candidates": "learning.queued 的第三重回退(count/queued 为主且有 ||0)",
    "category": "detection 的 class 兼容名;前端 `p.class||p.category` 主路径走 class",
    "error": "cleanup 失败时的可选诊断;前端已回退到 p.note/run.json.error",
    "note": "cleanup 的可选备注(无备份分支才发);有 p.status 与 restored_sha256 主路径",
    "id": "finding 兼容名;前端 `p.id||fid` 主路径走 envelope 的 finding_id",
    "path": "观测路径兼容名;引擎发 endpoint(主路径),path 仅兼底",
}


def _frontend_reads_by_type():
    """从 demo/index.html 的 switch-case 块反推 `p.<key>` 读取表。

    先剔注释:注释里出现的 `p.pass` 之类不属读取(而且会误导审计)。"""
    import re
    html = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")
    html = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    html = re.sub(r"(?m)^\s*//.*$", "", html)
    out = {}
    for m in re.finditer(r'case\s+"([a-z_.]+)"\s*:\s*\{(.*?)\n    \}', html, re.S):
        ev_type, block = m.group(1), m.group(2)
        keys = set(re.findall(r"\bp\.([A-Za-z_][A-Za-z0-9_]*)", block))
        if keys:
            out.setdefault(ev_type, set()).update(keys)
    return out


def test_frontend_source_reads_match_emitted(tmp_path, monkeypatch):
    """双向锁:demo/index.html 实际读的 p.<key> 必须能被引擎实发 payload 满足。"""
    reads = _frontend_reads_by_type()
    assert reads, "未能从 demo/index.html 反推读取表(选择器失配?)"
    inst, _state, _canon = build_env(tmp_path, monkeypatch)
    inst.run()
    emitted = {}
    for ev in _events(inst):
        emitted.setdefault(ev["type"], set()).update((ev.get("payload") or {}).keys())
        emitted[ev["type"]].update(k for k in ev.keys() if k != "payload")

    problems = []
    for ev_type, keys in sorted(reads.items()):
        if ev_type not in emitted:
            continue          # 本轮未触发的阶段(如 attack_not_exploited 分支)不在此锁
        unknown = sorted(k for k in keys
                         if k not in emitted[ev_type] and k not in FRONTEND_OPTIONAL)
        if unknown:
            problems.append(f"{ev_type}: 前端读了引擎不发的键 {unknown}")
    assert not problems, (
        "前端读取与事件实发漂移(屏幕会说谎或恒显历史值):\n  " + "\n  ".join(problems)
        + "\n  修法:引擎补发该键,或把前端改为缺证据显示未知,或(仅当确实可选且"
          "前端有诚实回退)加入 FRONTEND_OPTIONAL 并写明理由。")

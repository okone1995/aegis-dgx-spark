"""learn.py 单元测试 —— 差集信号、低置信升格、双门禁。"""
import json

from engine.learn import _alerted_refs, _hits, blue, red, verify


def _write_flow(p, entries):
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def test_blue_miss_and_lowconf(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_flow(ws / "flow.jsonl", [
        {"label": "attack", "method": "GET", "path": "/news/search?q=1%27%20UNION%20SELECT%201--%20",
         "status": 200, "marker_hits": ["admin123"], "ts": 1.0},          # HIT + 低置信告警 → 升格
        {"label": "attack", "method": "GET", "path": "/profile/avatar",
         "status": 200, "marker_hits": ["VULN_CONFIRMED"], "ts": 1.5},    # HIT 无告警 → 漏检
        {"label": "attack", "method": "GET", "path": "/news/search?q=x",
         "status": 200, "marker_hits": [], "ts": 2.0},                     # 未命中 → 不进
    ])
    (ws / "alerts.json").write_text(json.dumps([
        {"id": "A-001", "class": "sqli", "endpoint": "/news/search", "flow_ref": "0",
         "confidence": 0.3, "reasoning": "rule:SQ-003", "finding_link": "F-001"},
    ]), encoding="utf-8")
    (ws / "findings.json").write_text(json.dumps(
        {"findings": [{"id": "F-001", "class": "sqli", "endpoint": "/news/search",
                       "status": "verified"}]}), encoding="utf-8")

    cands = blue(ws, tmp_path / "cand")
    origins = {c["origin"] for c in cands}
    assert origins == {"missed-hit", "lowconf-upgrade"}, cands
    miss = next(c for c in cands if c["origin"] == "missed-hit")
    assert "VULN_CONFIRMED" in miss["evidence"]
    assert all(c["confidence"] >= 0.8 for c in cands)
    out = list((tmp_path / "cand").glob("blue-*.yaml"))
    assert len(out) == 1 and "人工过闸" in out[0].read_text(encoding="utf-8")


def test_verify_gate_rejects_benign_hit(tmp_path):
    flow = tmp_path / "flow.jsonl"
    _write_flow(flow, [
        {"label": "attack", "method": "GET", "path": "/news/search?q=%27%20UNION%20SELECT%201--%20",
         "status": 200, "marker_hits": ["admin123"]},
    ])
    benign = tmp_path / "benign.jsonl"
    _write_flow(benign, [
        {"label": "benign", "method": "GET", "path": "/news/search?q=%27%20UNION%20SELECT%201--%20",
         "status": 200},
    ])
    cand = tmp_path / "cand.yaml"
    cand.write_text(
        "- id: BLUE-C-001\n  class: sqli\n  origin: missed-hit\n  evidence: x\n"
        "  where: request_query\n  pattern: \"UNION(%20|\\\\s)+(SELECT|select)\"\n"
        "  confidence: 0.85\n", encoding="utf-8")
    rep = verify(cand, flow)
    assert rep["results"][0]["gate"] == "PASS"          # 攻击流命中

    cand2 = tmp_path / "cand2.yaml"
    cand2.write_text(
        "- id: BLUE-C-002\n  class: sqli\n  origin: missed-hit\n  evidence: x\n"
        "  where: request_query\n  pattern: \"UNION\"\n  confidence: 0.85\n", encoding="utf-8")
    rep2 = verify(cand2, flow, benign_path=benign)
    assert rep2["results"][0]["gate"] == "REJECT"       # 良性也命中 → 打回


def test_red_family_folding(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_flow(ws / "flow.jsonl", [
        {"label": "attack", "method": "POST", "path": "/login", "status": 401,
         "marker_hits": ["bad credentials"], "body": "username=lockme&password=x1"},
        {"label": "attack", "method": "POST", "path": "/login", "status": 401,
         "marker_hits": ["bad credentials"], "body": "username=lockme&password=x2"},
    ])
    fams = red(ws, tmp_path / "cand")
    assert len(fams) == 1 and fams[0]["count"] == 2     # 同族折叠
    out = list((tmp_path / "cand").glob("red-*.md"))
    assert out and "人工过闸" in out[0].read_text(encoding="utf-8")

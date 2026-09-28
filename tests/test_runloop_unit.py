"""runloop 单元测试 —— D3 评审 P0 修正的回归锁：re-attack 必须重新认证。"""
import pathlib

from engine import runloop
from engine.blackboard import Blackboard
from engine.models import Finding, Severity


class _Res:
    def __init__(self, hit):
        self.markers_hit = ["m"] if hit else []
        self.payload_file = "stub"


def test_re_attack_reauthenticates_and_checks_policy(monkeypatch, tmp_path):
    """空证据回归锁：re-attack 用新会话（旧会话被补丁重部署作废）且逐条过 policy。"""
    calls = {"auth": 0, "policy": 0, "replay": 0, "ctx_cookie": None}

    monkeypatch.setattr(runloop.replay, "admin_session",
                        lambda base, inst: calls.__setitem__("auth", calls["auth"] + 1) or "FRESH=1")
    monkeypatch.setattr(runloop.replay, "make_ctx",
                        lambda base, inst, cookie: calls.__setitem__("ctx_cookie", cookie) or {})
    monkeypatch.setattr(runloop.replay, "check_payload_policy", lambda req: calls.__setitem__("policy", calls["policy"] + 1))
    monkeypatch.setattr(runloop.replay, "replay_payload",
                        lambda base, req, name, ctx, markers, on_exchange=None:
                        calls.__setitem__("replay", calls["replay"] + 1) or _Res(False))

    pd = tmp_path / "pl"
    (pd / "payloads" / "attack").mkdir(parents=True)
    (pd / "payloads" / "attack" / "01.txt").write_text("x")
    (pd / "payloads" / "attack" / "02.txt").write_text("y")

    hits = runloop.re_attack("http://x", "a", pd, ["m"])

    assert hits == []
    assert calls["auth"] == 1, "re-attack 必须重新认证"
    assert calls["ctx_cookie"] == "FRESH=1", "ctx 必须由新会话构造"
    assert calls["policy"] == 2, "每条 payload 发射前过 policy"
    assert calls["replay"] == 2


def test_re_attack_reports_hits(monkeypatch, tmp_path):
    monkeypatch.setattr(runloop.replay, "admin_session", lambda b, i: "S=1")
    monkeypatch.setattr(runloop.replay, "make_ctx", lambda b, i, c: {})
    monkeypatch.setattr(runloop.replay, "check_payload_policy", lambda r: None)
    monkeypatch.setattr(runloop.replay, "replay_payload",
                        lambda *a, **k: _Res(True))
    pd = tmp_path / "pl"
    (pd / "payloads" / "attack").mkdir(parents=True)
    (pd / "payloads" / "attack" / "01.txt").write_text("x")
    assert runloop.re_attack("http://x", "a", pd, ["m"]) == ["01.txt"]


def test_next_finding_id_increments(tmp_path):
    bb = Blackboard(tmp_path)
    assert runloop.next_finding_id(bb) == "F-001"
    bb.add_finding(Finding(id="F-001", class_="sqli", endpoint="/x",
                           severity=Severity.high))
    assert runloop.next_finding_id(bb) == "F-002"

"""LLM 兜底（hunt）的单测：只看"提议→取回本端点载荷→走同一发射路径→硬信号定命"。

不联网、不打靶：plan_free 与 _fire 都被替身接管，验的是**接线与纪律**：
  * 本端点本参数的载荷要发射，形态外的一律 skip 且入账；
  * 命中与否由 markers_hit 决定（模型说了不算）；
  * 模型不可用 / 发射被拒 / 预算耗尽，都要如实进 notes，不假装成功。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from engine import hunt as H  # noqa: E402

PATH = "/reports/exception-queue"
PARAM = "warehouse"


def _mk(base="http://127.0.0.1:8093", shots=4, markers_on=()):
    """造一个只带兜底所需字段的 Hunt（不碰 profile / 网络）。"""
    h = H.Hunt.__new__(H.Hunt)
    h.cls, h.llm_shots, h.path, h.param, h.base = "sqli", shots, PATH, PARAM, base
    h.fired = []

    def fake_fire(value, step_id="", extra=""):
        h.fired.append((value, step_id))
        hit = ["SQL_SYNTAX_LEAK"] if step_id in markers_on else []
        return {"step": step_id, "status": 200, "length": 100, "markers_hit": hit}

    h._fire = fake_fire
    return h


def _req(value_enc: str, path: str = PATH, param: str = PARAM) -> str:
    return f"GET {{{{TARGET}}}}{path}?{param}={value_enc} HTTP/1.1\nCookie: {{{{SESSION}}}}\n\n"


def test_on_endpoint_shot_is_fired_with_decoded_value(monkeypatch):
    from engine import attacker

    monkeypatch.setattr(attacker, "plan_free", lambda *a, **k: {
        "accepted": [{"text": _req("1%27%20UNION%20SELECT%201--%20"), "rationale": "r0"}], "rejected": []})
    h = _mk()
    notes = []
    assert h._llm_round(notes) is None                     # 未命中 ⇒ 返回 None（不粉饰）
    assert h.fired == [("1' UNION SELECT 1-- ", "llm:0")]  # 值已解码，交回 _fire 再编码
    assert any(n.startswith("llm_fallback:accepted=1") for n in notes)


def test_off_endpoint_or_off_param_is_skipped_not_fired(monkeypatch):
    from engine import attacker

    monkeypatch.setattr(attacker, "plan_free", lambda *a, **k: {"accepted": [
        {"text": _req("1", path="/admin/users")},           # 别的端点
        {"text": _req("1", param="other")},                 # 别的参数
        {"text": "GET / HTTP/1.1\n\n"},                     # 连占位符都没有
        {"text": _req("2%20OR%202%3D2--")},                 # 合法：本端点本参数
    ], "rejected": []})
    h = _mk()
    notes = []
    h._llm_round(notes)
    assert [v for v, _ in h.fired] == ["2 OR 2=2--"], "只有本端点本参数那一发允许发射"
    assert sum(1 for n in notes if n.startswith("llm_skipped[")) == 3


def test_hit_is_decided_by_markers_not_by_model(monkeypatch):
    from engine import attacker

    monkeypatch.setattr(attacker, "plan_free", lambda *a, **k: {"accepted": [
        {"text": _req("a%27")}, {"text": _req("b%27")}], "rejected": []})
    h = _mk(markers_on=("llm:1",))
    notes = []
    obs = h._llm_round(notes)
    assert obs and obs["step"] == "llm:1" and obs["markers_hit"]
    assert [s for _, s in h.fired] == ["llm:0", "llm:1"], "第一发未命中应继续，命中即停"
    assert any(n.startswith("llm_fallback_hit@llm:1") for n in notes)


def test_model_unavailable_and_refused_shots_are_recorded(monkeypatch):
    from engine import attacker

    def boom(*a, **k):
        raise RuntimeError("no key")

    monkeypatch.setattr(attacker, "plan_free", boom)
    h = _mk()
    notes = []
    assert h._llm_round(notes) is None
    assert any(n.startswith("llm_fallback_unavailable:RuntimeError") for n in notes)
    assert h.fired == []

    monkeypatch.setattr(attacker, "plan_free", lambda *a, **k: {
        "accepted": [{"text": _req("x%27")}, {"text": _req("y%27")}],
        "rejected": [{"req": "…", "why_rejected": "policy闸：[destructive]"}],
        "jail": "strict"})

    h2 = _mk()

    def refuse_first(value, step_id="", extra=""):
        if step_id == "llm:0":
            raise ValueError("policy: destructive")
        h2.fired.append((value, step_id))
        return {"step": step_id, "status": 200, "length": 10, "markers_hit": []}

    h2._fire = refuse_first
    notes2 = []
    h2._llm_round(notes2)
    assert any("llm_refused[0]:ValueError" in n for n in notes2), "被 policy 拒的发要入账"
    assert any(n.startswith("llm_rejected[0]:") for n in notes2)
    assert [s for _, s in h2.fired] == ["llm:1"], "被拒后继续下一发，不静默中断"
    assert any("jail=strict" in n for n in notes2)


def test_budget_exhaustion_stops_fallback_and_is_visible(monkeypatch):
    from engine import attacker

    monkeypatch.setattr(attacker, "plan_free", lambda *a, **k: {
        "accepted": [{"text": _req("a%27")}, {"text": _req("b%27")}], "rejected": []})
    h = _mk()

    def budget_fire(value, step_id="", extra=""):
        raise H.HuntError("budget:requests")

    h._fire = budget_fire
    notes = []
    assert h._llm_round(notes) is None
    assert any(n.startswith("llm_stop:budget:requests") for n in notes)

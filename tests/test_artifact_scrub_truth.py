# -*- coding: utf-8 -*-
"""T7 复核两条 P1 的回归锁。

P1-a:规则命中只产生检测结果 ⇒ 页面措辞不得写"拦截";补丁门禁名称必须是实跑的三项。
P1-b:工件清单不得无条件声称已脱敏 —— scrubbed 必须反映是否真跑过脱敏。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import demo_case as DC  # noqa: E402
from tests.test_demo_case import build_env  # noqa: E402


def test_frontend_says_detection_not_interception():
    """规则命中≠拦截:措辞与计数标签必须按真实动作。"""
    html = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")
    assert "蓝方拦截" not in html, "顶栏计数不得写「拦截」"
    assert "蓝方规则检出" in html
    # 检测事件的叙述行不得出现"拦截 <b>…</b>"式措辞
    assert 'sideSay("blue","拦截' not in html
    assert 'sideSay("blue","检出' in html


def test_frontend_gate_names_match_real_gates():
    """补丁画面必须写实跑的门禁名,不得写未实现的 正则→语法→AST。"""
    html = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")
    assert "正则→语法→AST" not in html, "门禁名与实现不符"
    assert "三重审查" not in html, "三关=改动范围/危险模式/模型复核,不是「三重审查」这套说法"
    for name in ("改动范围", "危险模式", "模型复核"):
        assert name in html, f"缺真实门禁名 {name}"


def test_artifacts_scrubbed_flag_is_truthful(tmp_path, monkeypatch):
    """工件清单:scrubbed 必须来自真实脱敏运行,并附替换处数。"""
    inst, _state, _canon = build_env(tmp_path, monkeypatch)
    inst.run()
    arts = json.loads((inst.run_dir / "artifacts.json").read_text(encoding="utf-8"))
    assert arts
    for a in arts:
        assert "scrubbed" in a and "scrub_substitutions" in a
        assert isinstance(a["scrubbed"], bool)
    # 仓库脱敏器在本机可用 ⇒ 应真跑过
    assert any(a["scrubbed"] for a in arts)


def test_artifacts_marked_unscrubbed_when_scrubber_missing(tmp_path, monkeypatch):
    """脱敏器不可用时必须写 scrubbed=False,不得默认声称已脱敏。"""
    inst, _state, _canon = build_env(tmp_path, monkeypatch)
    monkeypatch.setattr(DC, "_scrub_artifact",
                        lambda text: (text, False, 0), raising=False)
    inst.run()
    arts = json.loads((inst.run_dir / "artifacts.json").read_text(encoding="utf-8"))
    assert arts and all(a["scrubbed"] is False for a in arts), arts

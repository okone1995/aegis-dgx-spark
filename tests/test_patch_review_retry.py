# -*- coding: utf-8 -*-
"""异构复核的重试行为测试(T7 真机间歇空内容/裁决不稳事故的回归锁)。

背景:云端 step 对同一份良性候选 diff 会间歇性返回空内容,且 full 形态裁决不稳
(实测同一 diff 三次:FAIL / 空内容 / PASS)。现实现为「min → full → min 多数决」:
有效回答不足 2 次 → fail-closed;多数决结果与每次原始回答都写进备注。
本文件用注入替身锁死这几条路径,并断言 fail-closed 仍在。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import patch_review  # noqa: E402


def _mk_llm(script, calls):
    """script: 依次返回的字符串或异常实例。"""
    class _LLM:
        @staticmethod
        def chat(prompt, **kw):
            calls.append(prompt)
            item = script[min(len(calls) - 1, len(script) - 1)]
            if isinstance(item, Exception):
                raise item
            return item
    return _LLM


def test_majority_pass_when_full_form_disagrees(monkeypatch):
    """真实事故形态:min 稳定 PASS,full 抽风答 FAIL → 多数决 PASS。"""
    calls = []
    script = ["PASS", "FAIL", "PASS"]      # min, full, min
    import engine.llm as real_llm
    monkeypatch.setattr(real_llm, "chat", _mk_llm(script, calls).chat, raising=False)
    verdict, note = patch_review.llm_review("+安全修复行", provider="step", timeout_s=5)
    assert verdict == "PASS"
    assert len(calls) == 3
    assert "多数决 2PASS/1FAIL" in note
    assert "原始回答: min=PASS full=FAIL min=PASS" in note


def test_majority_fail_when_two_forms_say_fail(monkeypatch):
    calls = []
    script = ["FAIL", "PASS", "FAIL"]
    import engine.llm as real_llm
    monkeypatch.setattr(real_llm, "chat", _mk_llm(script, calls).chat, raising=False)
    verdict, note = patch_review.llm_review("+危险行", provider="step", timeout_s=5)
    assert verdict == "FAIL"
    assert "多数决 1PASS/2FAIL" in note


def test_fail_closed_when_fewer_than_two_valid_answers(monkeypatch):
    """三次里两次空内容/异常 → 有效回答不足 2 → fail-closed。"""
    calls = []
    script = [RuntimeError("step: empty content (stream)"), "", "PASS"]
    import engine.llm as real_llm
    monkeypatch.setattr(real_llm, "chat", _mk_llm(script, calls).chat, raising=False)
    verdict, note = patch_review.llm_review("+x", provider="step", timeout_s=5)
    assert verdict == "FAIL"
    assert "fail-closed" in note and "有效回答 1/3" in note
    assert "error(RuntimeError)" in note and "full=empty" in note


def test_min_form_is_first_and_questions_are_unambiguous(monkeypatch):
    """首问必须是实测最稳的 min 形态;且措辞不得有否定歧义。"""
    calls = []
    import engine.llm as real_llm
    monkeypatch.setattr(real_llm, "chat", _mk_llm(["PASS", "PASS", "PASS"], calls).chat, raising=False)
    long_diff = "\n".join(f"+line{i}" for i in range(500))
    verdict, note = patch_review.llm_review(long_diff, provider="step", timeout_s=5)
    assert verdict == "PASS"
    assert "只回答一个词" in calls[0], "首问应为极简形态"
    assert "若否回答 PASS" not in calls[0], "不得出现否定映射歧义(历史 bug)"
    assert len(calls[0]) < len(calls[1]), "首问应比 full 形态短"


def test_review_still_fail_closed_when_llm_unavailable(monkeypatch):
    """端到端:复核每次抛错 → review() 必须拒(理由含 fail-closed)。"""
    def _boom(*a, **k):
        raise RuntimeError("cloud down")
    monkeypatch.setattr(patch_review, "llm_review", _boom)
    orig = "<?php\n$a=1;\n"
    patched = "<?php\n$a=2;\n"
    ok, reasons, diff = patch_review.review(orig, patched, [orig.strip()])
    assert ok is False
    assert any("fail-closed" in r or "复核" in r for r in reasons)

# -*- coding: utf-8 -*-
"""T10-S4:受约束自适应探针的四条分支断言。

整改单要求四条分支都有测试:
  ① 模型选招(source=llm)      ② 确定性 fallback(source=fallback)
  ③ 策略拒绝(不发射)          ④ 变体重击 ⇒ 修复结果转 regressed

做法:复用 tests/test_demo_case.py 的替身夹具(build_env),只替换三处可注入点:
  attacker.plan / payload_policy.assert_allowed / DemoCase._replay。
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from engine import demo_case as DC  # noqa: E402
from tests.test_demo_case import build_env  # noqa: E402


class _Res:
    """replay 返回值桩(与 replay.ReplayResult 的字段对齐)。"""

    def __init__(self, status=200, response_text="", markers_hit=None, payload_file="p.txt", error=None):
        self.status = status
        self.response_text = response_text
        self.markers_hit = markers_hit or []
        self.payload_file = payload_file
        self.error = error


def _prep(tmp_path, monkeypatch, *, plan_ret, hits=(), policy_raises=False):
    monkeypatch.setenv("AEGIS_PROBE", "on")
    env = build_env(tmp_path, monkeypatch)
    # 夹具里 build_env 会把探针关掉,这里再打开并注入
    monkeypatch.setenv("AEGIS_PROBE", "on")

    from engine import attacker as ATK

    def _fake_plan(cls, parents, defense_note, provider="qwen", max_variants=6, budget_s=20, base="", chat=None):
        return list(plan_ret)

    monkeypatch.setattr(ATK, "plan", _fake_plan)

    from governance import payload_policy as POL

    def _fake_allowed(text, collector_port=30010, **kw):
        if policy_raises:
            raise POL.PolicyViolation("stub: off-cage external host")
        return True

    monkeypatch.setattr(POL, "assert_allowed", _fake_allowed)

    # 只对**探针载荷**(run_dir/probe/probe-*.txt)返回受控结果;
    # 其余(补前攻击/既定复测)走夹具自带的真桩 —— 否则会把攻击阶段也改成“不命中”。
    _orig_replay = DC.DemoCase._replay

    def _fake_replay(self, pf, ctx, markers, rec):
        name = str(getattr(pf, "name", pf)).replace("\\", "/")
        if "/probe/" in name or name.startswith("probe-"):
            return _Res(markers_hit=list(hits))
        return _orig_replay(self, pf, ctx, markers, rec)

    monkeypatch.setattr(DC.DemoCase, "_replay", _fake_replay)
    return env


def _probe(tmp_path):
    p = tmp_path / "run.json"          # 占位,真正的 run 目录由 env 提供
    return p


def test_probe_disabled_is_inert(tmp_path, monkeypatch):
    """探针关闭时必须完全惰性:不写 probe-lineage、不发射变体。"""
    env = _prep(tmp_path, monkeypatch, plan_ret=[], hits=())
    monkeypatch.setenv("AEGIS_PROBE", "off")
    inst, state, canon = env
    assert inst.run() == 0
    ver = json.loads((inst.run_dir / "verification.json").read_text(encoding="utf-8"))
    assert ver["probe"]["enabled"] is False
    assert not (inst.run_dir / "probe-lineage.json").exists()


def test_probe_records_lineage_and_fires_allowed_variant(tmp_path, monkeypatch):
    """① 模型选招:lineage 必须记 parent/transform/source/replay_of/flow_id,并落盘可审计。"""
    env = _prep(tmp_path, monkeypatch, hits=(), plan_ret=[
        {"parent": "01.txt", "transform_id": "kw_case", "text": "GET /news/search?q=UNION",
         "rationale": "大小写混淆绕过", "source": "llm"},
    ])
    inst, state, canon = env
    assert inst.run() == 0
    lin = json.loads((inst.run_dir / "probe-lineage.json").read_text(encoding="utf-8"))
    assert lin["enabled"] is True and lin["model_calls"] == 1
    row = lin["variants"][0]
    assert row["source"] == "llm" and row["transform_id"] == "kw_case"
    assert row["policy"] == "allowed" and row["fired"] is True
    assert row["flow_id"] and row["replay_of"], "必须记 flow_id 与 replay_of"
    assert len(row["payload_sha256"]) == 64
    assert lin["any_hit"] is False
    ver = json.loads((inst.run_dir / "verification.json").read_text(encoding="utf-8"))
    assert ver["repair_outcome"] == "verified", "未命中时不应改动既定结论"


def test_probe_records_fallback_source_verbatim(tmp_path, monkeypatch):
    """② 确定性 fallback:source 必须如实写 fallback,不得冒称模型选招。"""
    env = _prep(tmp_path, monkeypatch, hits=(), plan_ret=[
        {"parent": "01.txt", "transform_id": "dbl_encode", "text": "GET /news/search?q=%2527",
         "rationale": "（确定性轮换）", "source": "fallback"},
    ])
    inst, state, canon = env
    assert inst.run() == 0
    lin = json.loads((inst.run_dir / "probe-lineage.json").read_text(encoding="utf-8"))
    assert lin["variants"][0]["source"] == "fallback"


def test_probe_policy_rejection_does_not_fire(tmp_path, monkeypatch):
    """③ 策略拒绝:不得发射,且如实记 rejected。"""
    env = _prep(tmp_path, monkeypatch, plan_ret=[
        {"parent": "01.txt", "transform_id": "x", "text": "GET http://evil.example/",
         "rationale": "外联", "source": "llm"},
    ], policy_raises=True)
    inst, state, canon = env
    assert inst.run() == 0
    lin = json.loads((inst.run_dir / "probe-lineage.json").read_text(encoding="utf-8"))
    row = lin["variants"][0]
    assert row["fired"] is False and row["flow_id"] is None
    assert str(row["policy"]).startswith("rejected")


def test_probe_hit_turns_result_regressed(tmp_path, monkeypatch):
    """④ 变体重击 ⇒ 修复结果转 regressed:不得因为既定重放 0/2 就维持 succeeded。"""
    env = _prep(tmp_path, monkeypatch, hits=("admin123",), plan_ret=[
        {"parent": "01.txt", "transform_id": "kw_case", "text": "GET /news/search?q=UNION",
         "rationale": "变体命中", "source": "llm"},
    ])
    inst, state, canon = env
    code = inst.run()
    assert code != 0
    meta = json.loads((inst.run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["repair_outcome"] == "regressed", meta.get("repair_outcome")
    assert meta["state"] != "succeeded"
    lin = json.loads((inst.run_dir / "probe-lineage.json").read_text(encoding="utf-8"))
    assert lin["any_hit"] is True and lin["variants"][0]["markers_hit"] == ["admin123"]

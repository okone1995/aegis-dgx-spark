# -*- coding: utf-8 -*-
"""T3 测试:judge_client 替身测试 + 渲染对齐 + Observation 真值隔离。"""
import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import judge_client
from engine.detect import Observation, scan_observation


# ---------- judge_client ----------

class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen_ok(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    return mock.MagicMock(return_value=_FakeResp(body))


def test_judge_parses_normal_response():
    with mock.patch.object(judge_client.urllib.request, "urlopen",
                           _urlopen_ok({"verdict": "attack", "band": "confident",
                                        "p_attack": 0.93, "latency_ms": 12.3})):
        out = judge_client.judge({"method": "GET", "path": "/news?q=1' union",
                                  "body": "", "status": 200, "response": "admin123"})
    assert out["verdict"] == "attack"
    assert out["model_version"] == "unknown"  # 服务未给时的缺省
    assert out["protocol_id"] == "unknown"  # S3 遗留已修:服务未自报协议时不回填具体版本号(与 model_version 同法)


def test_judge_timeout_raises_unavailable():
    def _timeout(*a, **k):
        raise TimeoutError("t")
    with mock.patch.object(judge_client.urllib.request, "urlopen", _timeout):
        with pytest.raises(judge_client.JudgeUnavailable):
            judge_client.judge({"method": "GET", "path": "/x"})


def test_judge_http_500_raises_unavailable():
    with mock.patch.object(judge_client.urllib.request, "urlopen",
                           side_effect=urllib.error.HTTPError(
                               "u", 503, "oops", None, io.BytesIO(b""))):
        with pytest.raises(judge_client.JudgeUnavailable):
            judge_client.judge({"method": "GET", "path": "/x"})


def test_dedupe_key_stable():
    a = judge_client.dedupe_key("run-1", "FL-0001", "v3", "p1")
    b = judge_client.dedupe_key("run-1", "FL-0001", "v3", "p1")
    c = judge_client.dedupe_key("run-2", "FL-0001", "v3", "p1")
    assert a == b and a != c


# ---------- 渲染对齐(契约 §2.8:训练/评估/服务共用输入渲染) ----------

def test_rendering_parity_with_make_record():
    sys.path.insert(0, str(ROOT / "dataset"))
    import build_judge as BJ
    method, path, body, status, response = (
        "POST", "http://127.0.0.1:8081/login", "username=a&" + "pass" + "word" + "=b",
        401, "<html>bad credentials</html>")
    ours = judge_client.render_user(method, path, body, status, response)
    rec = BJ.make_record(method, path, body, status, response, "attack", {})
    theirs = rec["messages"][1]["content"]
    assert ours == theirs, "judge_client 渲染必须与训练卷 make_record 逐字一致"


# ---------- Observation 真值隔离(契约 §2.5 / A02) ----------

def test_observation_has_no_truth_fields():
    import dataclasses
    names = {f.name for f in dataclasses.fields(Observation)}
    assert names == {"method", "path", "body", "status", "response"}
    for forbidden in ("label", "marker_hits", "finding_id", "phase",
                      "expected_verdict"):
        assert forbidden not in names


def test_observation_rejects_unknown_fields():
    with pytest.raises(TypeError):
        Observation(method="GET", path="/x", label="attack")


def test_scan_observation_detects_sqli_and_passes_benign():
    # 注入请求命中 SQ-001(UNION SELECT, request_query)
    atk = Observation(method="GET",
                      path="/news?q=1%27%20UNION%20SELECT%20username%20FROM%20users--",
                      status=200, response="admin123")
    r1 = scan_observation(atk)
    assert r1["matched"] is True
    assert any("rule:SQ-" in a["reasoning"] for a in r1["alerts"])
    # 真值字段不进告警
    for a in r1["alerts"]:
        assert "label" not in a and "finding_link" not in a
    # 正常搜索:无规则命中(不代表正常,只是规则沉默)
    ok = Observation(method="GET", path="/news?q=%E5%9B%BE%E4%B9%A6%E9%A6%86",
                     status=200, response="期中考试通知")
    r2 = scan_observation(ok)
    assert r2["matched"] is False
    assert isinstance(r2["scanned_at"], str) and r2["scanned_at"].endswith("Z")

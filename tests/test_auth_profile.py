"""认证配置的单测（T11/S1h）：凭据只走环境变量，缺失必须可见失败。

补这组用例的原因：edu-lite 不声明 credential_env，本地回归走不到该分支，
C 场首次触发时暴露了 replay.py 缺 import os 的 NameError（真机才发现）。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import replay  # noqa: E402


def test_auth_cfg_edu_lite_default():
    cfg = replay._auth_cfg()
    assert cfg.get("mode") == "form_login"
    assert cfg.get("user_field") == "username"
    assert cfg.get("cookie") == "edu_session"


def test_credential_env_missing_fails_visibly(monkeypatch):
    monkeypatch.setattr(replay, "_auth_cfg",
                        lambda: {"mode": "form_login",
                                 "credential_env": {"user": "AEGIS_X_USER",
                                                    "pass": "AEGIS_X_PASS"}})
    monkeypatch.delenv("AEGIS_X_USER", raising=False)
    monkeypatch.delenv("AEGIS_X_PASS", raising=False)
    with pytest.raises(RuntimeError, match="AEGIS_X_USER"):
        replay._auth_credential()


def test_credential_env_present_is_used(monkeypatch):
    monkeypatch.setattr(replay, "_auth_cfg",
                        lambda: {"mode": "form_login",
                                 "credential_env": {"user": "AEGIS_X_USER",
                                                    "pass": "AEGIS_X_PASS"}})
    monkeypatch.setenv("AEGIS_X_USER", "STAFF-A")
    monkeypatch.setenv("AEGIS_X_PASS", "PLACEHOLDER")
    user, secret = replay._auth_credential()
    assert user == "STAFF-A" and secret == "PLACEHOLDER"


def test_no_credential_env_returns_empty(monkeypatch):
    monkeypatch.setattr(replay, "_auth_cfg", lambda: {"mode": "form_login"})
    assert replay._auth_credential() == ("", "")


def test_auth_none_skips_session(monkeypatch):
    monkeypatch.setattr(replay, "_auth_mode", lambda: "none")
    assert replay.admin_session("http://127.0.0.1:9", "a") == ""

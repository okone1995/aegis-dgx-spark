import pytest

from engine.scope import Scope, ScopeError

SCOPE_YAML = """
targets:
  - name: edu-lite-a
    base_url: http://127.0.0.1:8080
    bind: local-only
  - name: lab-net
    base_url: http://192.168.1.50:8080
    bind: local-only
allowed_classes: [sqli, upload_bypass]
forbidden_actions: [destructive_write, dos]
max_rounds: 3
"""


@pytest.fixture()
def scope(tmp_path):
    p = tmp_path / "scope.yaml"
    p.write_text(SCOPE_YAML, encoding="utf-8")
    return Scope.from_yaml(p)


def test_local_targets_pass(scope):
    scope.check_url("http://127.0.0.1:8080/news.php?q=1")
    scope.check_url("http://192.168.1.50:8080/login.php")
    assert scope.target("edu-lite-a").bind == "local-only"


def test_public_ip_rejected(scope):
    with pytest.raises(ScopeError):
        scope.check_url("http://8.8.8.8:8080/x")
    with pytest.raises(ScopeError):
        scope.check_url("http://example.com/payday.php")


def test_unknown_host_rejected(scope):
    with pytest.raises(ScopeError):
        scope.check_url("http://10.99.99.99:8080/x")  # 内网但不在 targets


def test_class_and_action(scope):
    scope.check_class("sqli")
    with pytest.raises(ScopeError):
        scope.check_class("worm_spread")
    with pytest.raises(ScopeError):
        scope.check_action("dos")

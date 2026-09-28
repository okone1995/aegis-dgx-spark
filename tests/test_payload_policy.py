import pytest

from governance.payload_policy import (
    PolicyViolation, assert_allowed, check_payload,
)


def test_benign_sqli_payload_passes():
    assert_allowed("1' OR '1'='1' -- ")
    assert_allowed("1 UNION SELECT username,password FROM users--")


def test_destructive_rejected():
    v = check_payload("x; rm -rf /var/www")
    assert any(x.category == "destructive_write" for x in v)
    v = check_payload("1; DROP TABLE users--")
    assert any(x.category == "destructive_write" for x in v)
    with pytest.raises(PolicyViolation):
        assert_allowed("1; TRUNCATE TABLE news--")
    # 无分号结尾的整表删同样拦截（STRATEGY §5.3 指出的绕过）
    v = check_payload("1; DELETE FROM users")
    assert any(x.category == "destructive_write" for x in v)


def test_persistence_rejected():
    v = check_payload("<?php eval($_POST['cmd']); ?>")
    assert any(x.category == "persistence" for x in v)
    v = check_payload("system($_GET['c'])")
    assert any(x.category == "persistence" for x in v)


def test_dos_rejected():
    v = check_payload("1 AND BENCHMARK(10000000, MD5('x'))--")
    assert any(x.category == "dos" for x in v)


def test_outbound_egress_control():
    v = check_payload("http://evil.example.com/shell.txt")
    assert any(x.category == "outbound_exploit" for x in v)
    # 本地采集器放行
    assert check_payload("http://127.0.0.1:30010/collect?d=abc") == []
    # 采集端口之外的本机端口拒绝
    v = check_payload("http://127.0.0.1:9999/exfil?d=abc")
    assert any(x.category == "outbound_exploit" for x in v)

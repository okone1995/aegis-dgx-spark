"""发射器跳转钉桩测试（评审 P0：宿主 HTTP 客户端跟随重定向 = 两档通用出笼通道）。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
from engine import replay  # noqa: E402

BASE = "http://172.31.99.3:8081"


class FakeResp:
    def __init__(self, status, loc=None, text="ok"):
        self.status_code = status
        self.headers = {"Location": loc} if loc else {}
        self.text = text


def _drive(monkeypatch, script):
    """script: 依次返回的 FakeResp；记录每次被请求的 url/method/data。"""
    seen = []

    def fake_request(method, url, **kw):
        seen.append((method, url, kw.get("data"), kw.get("allow_redirects")))
        return script.pop(0)

    monkeypatch.setattr(replay.requests, "request", fake_request)
    return seen


def test_302_to_host_loopback_raises(monkeypatch):
    seen = _drive(monkeypatch, [FakeResp(302, "//127.0.0.1:30000/v1/models")])
    with pytest.raises(RuntimeError, match="重定向出界"):
        replay._pinned_request(BASE, "GET", BASE + "/uploads/shell.php")
    assert seen[0][3] is False          # 绝不交给 requests 自动跟


def test_protocol_relative_external_raises(monkeypatch):
    _drive(monkeypatch, [FakeResp(302, "//evil.example/x")])
    with pytest.raises(RuntimeError, match="重定向出界"):
        replay._pinned_request(BASE, "GET", BASE + "/a")


def test_307_same_site_keeps_method_and_body(monkeypatch):
    seen = _drive(monkeypatch, [FakeResp(307, "/news"), FakeResp(200)])
    r = replay._pinned_request(BASE, "POST", BASE + "/login", data=b"u=1")
    assert r.status_code == 200 and seen[1][0] == "POST" and seen[1][2] == b"u=1"
    assert replay.redirect_hops(r) == ["/news"]


def test_303_switches_to_get_and_drops_body(monkeypatch):
    seen = _drive(monkeypatch, [FakeResp(303, "/news"), FakeResp(200)])
    replay._pinned_request(BASE, "POST", BASE + "/login", data=b"u=1")
    assert seen[1][0] == "GET" and seen[1][2] is None


def test_same_site_302_chain_still_followed(monkeypatch):
    seen = _drive(monkeypatch, [FakeResp(302, "/login"), FakeResp(200, text="form")])
    r = replay._pinned_request(BASE, "GET", BASE + "/profile")
    assert r.text == "form" and seen[1][1] == BASE + "/login"


def test_hop_limit_guard(monkeypatch):
    _drive(monkeypatch, [FakeResp(302, "/a")] * 9)
    with pytest.raises(RuntimeError, match="跳数"):
        replay._pinned_request(BASE, "GET", BASE + "/loop")


def test_fire_records_hops_into_exchange(monkeypatch):
    _drive(monkeypatch, [FakeResp(302, "/news"), FakeResp(200, text="hi")])
    ex = []
    replay.fire(BASE, "GET {{TARGET}}/profile HTTP/1.1", {"TARGET": BASE, "SESSION": "s=1"},
                on_exchange=ex.append)
    assert ex[0]["redirect_hops"] == ["/news"]

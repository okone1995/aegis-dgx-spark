"""回放引擎单元测试（不触网：解析/替换/抓取目标/判定）。"""
from engine.replay import fetch_paths, parse_req, replay_payload, substitute

REQ = (
    "GET {{TARGET}}/news/search?{{SEARCH_PARAM}}=%27%20UNION HTTP/1.1\r\n"
    "Cookie: {{SESSION}}\r\n"
    "\r\n"
)


def test_parse_req():
    method, path, headers, body = parse_req(REQ)
    assert method == "GET"
    assert path.startswith("{{TARGET}}/news/search")
    assert headers["Cookie"] == "{{SESSION}}"
    assert body == ""


def test_substitute():
    out = substitute(REQ, {"TARGET": "http://x", "SEARCH_PARAM": "q", "SESSION": "c=1"})
    assert "{{" not in out and "http://x/news/search?q=" in out


def test_fetch_paths_upload_and_deser():
    body_upload = 'filename="pwn_a1.phtml"\r\nVULN_CONFIRMED_A1'
    assert fetch_paths(body_upload) == ["uploads/pwn_a1.phtml"]
    body_deser = '{"backup":"O:15:\\"EduBackupLogger\\":2:{s:7:\\"logFile\\";s:18:\\"uploads/pwn_v1.txt\\"'
    assert fetch_paths(body_deser) == ["uploads/pwn_v1.txt"]


def test_replay_result_error_on_unreachable(monkeypatch):
    import requests
    calls = []
    def unreachable(method, url, **kwargs):
        calls.append((method, url))
        raise requests.ConnectionError("isolated connection failure")
    monkeypatch.setattr("engine.replay.requests.request", unreachable)
    res = replay_payload("http://127.0.0.1:59999", REQ, "x.txt", {"TARGET": "http://127.0.0.1:59999"}, ["m"])
    assert calls and res.error and not res.markers_hit

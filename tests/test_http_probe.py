# -*- coding: utf-8 -*-
"""http_probe 真实回归测试(不桩网络) —— 补上 T7 真机事故的覆盖缺口。

事故回顾:Spark 上 preflight 恒失败,根因是函数体内 `import urllib.error` 把
`urllib` 变成函数局部名 → 首次尝试 UnboundLocalError、重试 AttributeError,
均被 `except Exception` 吞掉 → `http_probe` 恒返回 False。
当时所有测试都把 http_probe 桩掉了,所以本地全绿而真机全挂。
本文件用本机临时 HTTP 服务直接打真函数,锁死行为。
"""
import http.server
import socket
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.demo_case import http_probe  # noqa: E402


class _Handler(http.server.BaseHTTPRequestHandler):
    STATUS = 200

    def do_GET(self):  # noqa: N802
        body = b"<html>ok</html>"
        self.send_response(self.STATUS)
        if self.STATUS in (301, 302, 303):
            self.send_header("Location", "/login")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静音
        pass


def _serve(status=200):
    handler = type("H", (_Handler,), {"STATUS": status})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_probe_true_on_200():
    srv, base = _serve(200)
    try:
        assert http_probe(base) is True
    finally:
        srv.shutdown()


def test_probe_true_on_302_and_404():
    """3xx/4xx 是真实 HTTP 响应 → 服务活着(旧实现把 HTTPError 当存活处理)。"""
    for st in (302, 404):
        srv, base = _serve(st)
        try:
            assert http_probe(base) is True, f"status {st} 应判存活"
        finally:
            srv.shutdown()


def test_probe_false_when_nothing_listening():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert http_probe(f"http://127.0.0.1:{port}", timeout=1.0) is False


def test_probe_repeated_calls_stable():
    """事故形态:同一进程内连续调用不该出现“第一次假后续真”或反向漂移。"""
    srv, base = _serve(200)
    try:
        results = [http_probe(base) for _ in range(5)]
        assert results == [True] * 5, results
    finally:
        srv.shutdown()

import concurrent.futures
from contextlib import contextmanager
import json
import socket
import threading
import time
import urllib.error
import urllib.request

from engine.judge_http import JudgeRuntime, make_server


@contextmanager
def service(predict=lambda _: {"verdict": "benign", "latency_ms": 1}, **kwargs):
    runtime = JudgeRuntime(predict, {"protocol_id": "test"}, capacity=2, queue_timeout=.4)
    server = make_server(("127.0.0.1", 0), runtime, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", runtime
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(url, data=None):
    try:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


BODY = json.dumps({"method": "GET", "path": "/ordinary"}).encode()


def test_bad_requests_and_model_exceptions_release_capacity():
    def predict(p):
        if p["path"] == "/fail":
            raise RuntimeError("private traceback")
        return {"verdict": "benign"}
    with service(predict, max_body_bytes=100) as (url, rt):
        assert request(url+"/judge", b"{")[0] == 400
        assert request(url+"/judge", b"[]")[0] == 422
        assert request(url+"/judge", b"x"*101)[0] == 413
        failed = request(url+"/judge", json.dumps({"method":"GET","path":"/fail"}).encode())
        assert failed == (503, {"error": "inference_failed"})
        status, result = request(url+"/judge", BODY)
        assert status == 200 and result["queue_ms"] >= 0 and result["request_ms"] >= 0
        assert rt.health()["in_flight"] == 0


def test_capacity_is_bounded_and_health_does_not_wait_for_gpu():
    entered, release = threading.Event(), threading.Event()
    def predict(_):
        entered.set()
        release.wait(2)
        return {"verdict": "attack"}
    with service(predict) as (url, rt), concurrent.futures.ThreadPoolExecutor(2) as pool:
        first = pool.submit(request, url+"/judge", BODY)
        assert entered.wait(1)
        second = pool.submit(request, url+"/judge", BODY)
        deadline = time.monotonic()+1
        while rt.health()["in_flight"] < 2 and time.monotonic() < deadline:
            time.sleep(.005)
        assert rt.health()["in_flight"] == 2
        assert request(url+"/judge", BODY) == (503, {"error":"inference_capacity_exceeded"})
        assert request(url+"/health")[0] == 200
        release.set()
        assert first.result()[0] == second.result()[0] == 200


def test_slow_body_timeout_does_not_reserve_gpu():
    with service(read_timeout=.15) as (url, rt):
        port = int(url.rsplit(":",1)[1])
        with socket.create_connection(("127.0.0.1",port),timeout=2) as client:
            client.sendall(b"POST /judge HTTP/1.0\r\nContent-Length: 50\r\n\r\n{}")
            data = client.recv(4096)
        assert b"408" in data
        assert rt.health()["in_flight"] == 0
        assert request(url+"/judge", BODY)[0] == 200


def test_queue_timeout_releases_admission_slot():
    release = threading.Event()
    with service(lambda _: (release.wait(2) or True) and {"verdict":"benign"}) as (url, rt):
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            first = pool.submit(request, url+"/judge", BODY)
            deadline=time.monotonic()+1
            while not rt.health()["in_flight"] and time.monotonic()<deadline:
                time.sleep(.005)
            second = pool.submit(request, url+"/judge", BODY)
            assert second.result()[1]["error"] == "inference_queue_timeout"
            release.set()
            assert first.result()[0] == 200
        assert rt.health()["in_flight"] == 0

def test_dripping_body_cannot_extend_absolute_read_deadline():
    with service(read_timeout=.2) as (url, rt):
        port = int(url.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(b"POST /judge HTTP/1.0\r\nContent-Length: 500\r\n\r\n")
            stopped = threading.Event()
            def drip():
                while not stopped.wait(.03):
                    try:
                        client.sendall(b" ")
                    except OSError:
                        return
            writer = threading.Thread(target=drip, daemon=True)
            writer.start()
            started = time.monotonic()
            try:
                data = client.recv(4096)
            finally:
                stopped.set()
                writer.join(timeout=1)
        assert b"408" in data
        assert time.monotonic() - started < 1.5
        assert rt.health()["in_flight"] == 0
        assert request(url + "/judge", BODY)[0] == 200

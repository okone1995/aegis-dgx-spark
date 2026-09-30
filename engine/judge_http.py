"""Bounded HTTP transport and serialized inference, independent of GPU libraries."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time


class JudgeError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


class JudgeRuntime:
    def __init__(self, predict, metadata=None, capacity=8, queue_timeout=10.0):
        if capacity < 1 or queue_timeout <= 0:
            raise ValueError("capacity and timeout must be positive")
        self.predict = predict
        self.metadata = dict(metadata or {})
        self.capacity, self.queue_timeout = capacity, queue_timeout
        self._slots = threading.BoundedSemaphore(capacity)
        self._gpu = threading.Lock()
        self._stats_lock = threading.Lock()
        self._stats = dict(in_flight=0, completed=0, rejected=0, failed=0)
        self.started = time.monotonic()

    def _count(self, **values):
        with self._stats_lock:
            for key, delta in values.items():
                self._stats[key] += delta

    def health(self):
        with self._stats_lock:
            stats = dict(self._stats)
        return {"status": "ok", "model_loaded": True, "capacity": self.capacity,
                "uptime_s": round(time.monotonic() - self.started, 1),
                "protocol_id": self.metadata.get("protocol_id"), **stats}

    def judge(self, payload):
        if not self._slots.acquire(blocking=False):
            self._count(rejected=1)
            raise JudgeError(503, "inference_capacity_exceeded")
        self._count(in_flight=1)
        started = time.monotonic()
        acquired = False
        try:
            acquired = self._gpu.acquire(timeout=self.queue_timeout)
            if not acquired:
                self._count(rejected=1)
                raise JudgeError(503, "inference_queue_timeout")
            queued = time.monotonic()
            result = self.predict(payload)
            self._count(completed=1)
            return {**result, "queue_ms": round((queued - started) * 1000, 2),
                    "service_ms": round((time.monotonic() - started) * 1000, 2)}
        except JudgeError:
            raise
        except ValueError as exc:
            self._count(failed=1)
            raise JudgeError(422, "invalid_model_input") from exc
        except Exception as exc:
            self._count(failed=1)
            raise JudgeError(503, "inference_failed") from exc
        finally:
            if acquired:
                self._gpu.release()
            self._count(in_flight=-1)
            self._slots.release()


def make_server(address, runtime, *, max_body_bytes=262144, read_timeout=5.0):
    if max_body_bytes < 2 or read_timeout <= 0:
        raise ValueError("body limit and read timeout must be positive")

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(read_timeout)
            super().setup()
            self._read_deadline = time.monotonic() + read_timeout
            self._read_expired = threading.Event()
            def expire_read():
                self._read_expired.set()
                try:
                    self.request.shutdown(socket.SHUT_RD)
                except OSError:
                    pass
            # An absolute header/body deadline also stops clients sending one
            # byte just before each socket idle timeout.
            self._read_timer = threading.Timer(read_timeout, expire_read)
            self._read_timer.daemon = True
            self._read_timer.start()

        def finish(self):
            self._read_timer.cancel()
            super().finish()

        def send_json(self, status, data):
            raw = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            if status == 503:
                self.send_header("Retry-After", "1")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(raw)

        def do_GET(self):
            self._read_timer.cancel()
            if self._read_expired.is_set():
                self.send_json(408, {"error": "request_read_timeout"})
                return
            if self.path == "/health":
                self.send_json(200, runtime.health())
            elif self.path == "/metadata":
                self.send_json(200, {**runtime.metadata, "capacity": runtime.capacity,
                                    "queue_timeout_s": runtime.queue_timeout,
                                    "max_body_bytes": max_body_bytes})
            else:
                self.send_json(404, {"error": "not_found"})

        def do_POST(self):
            started = time.monotonic()
            try:
                if self.path != "/judge":
                    raise JudgeError(404, "not_found")
                if self.headers.get("Transfer-Encoding"):
                    raise JudgeError(400, "unsupported_transfer_encoding")
                lengths = self.headers.get_all("Content-Length", [])
                if not lengths:
                    raise JudgeError(411, "content_length_required")
                if len(lengths) != 1:
                    raise JudgeError(400, "ambiguous_content_length")
                try:
                    length = int(lengths[0])
                except ValueError:
                    raise JudgeError(400, "invalid_content_length") from None
                if length < 1:
                    raise JudgeError(400, "invalid_content_length")
                if length > max_body_bytes:
                    raise JudgeError(413, "request_too_large")
                # Header parsing is finished. Read chunks with the remaining
                # absolute budget, rather than restarting an idle timeout.
                self._read_timer.cancel()
                chunks, received = [], 0
                try:
                    while received < length:
                        remaining = self._read_deadline - time.monotonic()
                        if remaining <= 0:
                            raise JudgeError(408, "request_read_timeout")
                        self.request.settimeout(remaining)
                        chunk = self.rfile.read1(min(65536, length - received))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        received += len(chunk)
                    raw = b"".join(chunks)
                except (TimeoutError, socket.timeout):
                    raise JudgeError(408, "request_read_timeout") from None
                self.request.settimeout(read_timeout)
                if self._read_expired.is_set():
                    raise JudgeError(408, "request_read_timeout")
                if len(raw) != length:
                    raise JudgeError(400, "incomplete_body")
                try:
                    payload = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    raise JudgeError(400, "invalid_json") from None
                if (not isinstance(payload, dict)
                        or not isinstance(payload.get("method"), str)
                        or not payload["method"].strip()
                        or not isinstance(payload.get("path"), str)
                        or not payload["path"].strip()):
                    raise JudgeError(422, "invalid_exchange")
                for key in ("body", "response"):
                    if payload.get(key) is not None and not isinstance(payload[key], str):
                        raise JudgeError(422, "invalid_exchange")
                result = runtime.judge(payload)
                result["request_ms"] = round((time.monotonic() - started) * 1000, 2)
                self.send_json(200, result)
            except JudgeError as exc:
                self.send_json(exc.status, {"error": exc.code})
            except (BrokenPipeError, ConnectionError, OSError):
                self.close_connection = True

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True
        # Parser/management headroom, bounded separately from GPU admission.
        _connections = threading.BoundedSemaphore(runtime.capacity + 4)

        def process_request(self, request, client_address):
            if not self._connections.acquire(blocking=False):
                runtime._count(rejected=1)
                try:
                    request.settimeout(read_timeout)
                    body = b'{"error":"http_capacity_exceeded"}'
                    request.sendall(b"HTTP/1.0 503 Service Unavailable\r\n"
                                    b"Content-Type: application/json\r\n"
                                    b"Connection: close\r\nRetry-After: 1\r\n"
                                    + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
                except OSError:
                    pass
                finally:
                    self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self._connections.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self._connections.release()

    return Server(address, Handler)

"""Step Code ↔ SGLang 兼容垫片（D2 spike 产物）。

作用：
1. role 重写：Step Code 发 OpenAI 新式 `developer` 角色，SGLang fork 仅认 `system`
2. 字段兼容：max_completion_tokens → max_tokens
3. token 审计：SSE usage 帧落 /tmp/step_shim.log（spike#④ 计量 & Console 素材）

部署: nohup python3 compat_shim.py > /tmp/step_shim_serve.log 2>&1 &
用法: step -e local-qwen.mjs（baseUrl 用 AEGIS_SGLANG_URL=http://127.0.0.1:30005/v1）
"""
import json
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "http://127.0.0.1:30000"
LOG = "/tmp/step_shim.log"


class Shim(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = self.rfile.read(n)
        try:
            j = json.loads(body)
        except Exception:
            j = None
        if j:
            for m in j.get("messages", []):
                if m.get("role") == "developer":
                    m["role"] = "system"
            if "max_completion_tokens" in j:
                j.setdefault("max_tokens", j.pop("max_completion_tokens"))
            body = json.dumps(j).encode()

        req = urllib.request.Request(UPSTREAM + self.path, data=body, method="POST")
        for k in ("content-type", "authorization"):
            if self.headers.get(k):
                req.add_header(k, self.headers[k])
        try:
            r = urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            r = e

        self.send_response(r.status)
        self.send_header("content-type", r.headers.get("content-type", "application/json"))
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()

        in_tok = out_tok = 0
        buf = b""
        try:
            while True:
                chunk = r.read(1024)
                if not chunk:
                    break
                buf += chunk
                # SSE 行可能跨 chunk，用残留 buf 一起解析 usage
                for line in buf.split(b"\n"):
                    if line.startswith(b"data: ") and b'"usage"' in line:
                        try:
                            u = (json.loads(line[6:]) or {}).get("usage") or {}
                            in_tok = u.get("prompt_tokens", in_tok)
                            out_tok = u.get("completion_tokens", out_tok)
                        except Exception:
                            pass
                buf = buf[-1024:]
                self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
        except Exception:
            pass
        try:
            self.wfile.write(b"0\r\n\r\n")
        except Exception:
            pass
        with open(LOG, "a") as f:
            f.write(json.dumps({"ts": time.time(), "path": self.path,
                                "in": in_tok, "out": out_tok,
                                "usage_miss": 1 if (in_tok == 0 and out_tok == 0) else 0}) + "\n")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 30005), Shim).serve_forever()

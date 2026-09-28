"""诊断：把 hunt 实际发出的每个 HTTP 请求（方法/URL/头/状态/长度）落盘，找出
"同一 URL：hunt 404 / curl 200" 的差异所在。

做法：在本进程里给 requests.Session.request 打探针（cookie 只记长度与前 8 字符，不记全值），
然后直接调 Hunt 跑最小预算。日志写 agent_dbg.jsonl。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

LOG = pathlib.Path(__file__).resolve().parent / "agent_dbg.jsonl"
LOG.write_text("", encoding="utf-8")

_orig = requests.Session.request


def _mask(v: str) -> str:
    v = str(v)
    return f"<len={len(v)} head={v[:8]}>" if len(v) > 12 else v


def spy(self, method, url, **kw):  # noqa: ANN001
    r = _orig(self, method, url, **kw)
    hdrs = {k: (_mask(v) if k.lower() == "cookie" else str(v)[:60])
            for k, v in dict(kw.get("headers") or {}).items()}
    rec = {"method": method, "url": str(url)[:120], "headers": hdrs,
           "data": (str(kw.get("data"))[:120] if kw.get("data") else None),
           "status": r.status_code, "len": len(r.text or ""),
           "resp_set_cookie": _mask((r.headers.get("Set-Cookie") or "")[:80])}
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return r


requests.Session.request = spy

from engine import hunt as H  # noqa: E402
from engine.poc_queue import PocQueue  # noqa: E402

h = H.Hunt(base="http://127.0.0.1:8081", path="/download", param="file", cls="lfi",
           budget_requests=2, budget_seconds=30, queue=PocQueue(ROOT / "dataset" / "review_queue"
                                                                 / "poc_candidates.jsonl"))
try:
    out = h.run()
    print(json.dumps({"claim": out.get("claim"),
                      "attempts": [{"step": a.get("step"), "status": a.get("status"),
                                    "len": a.get("length")} for a in out.get("attempts") or []]},
                     ensure_ascii=False))
except Exception as exc:  # noqa: BLE001
    print("hunt 异常:", type(exc).__name__, str(exc)[:200])
print("--- 探针日志 ---")
for ln in LOG.read_text(encoding="utf-8").splitlines():
    d = json.loads(ln)
    print(f"  {d['method']} {d['url']} -> {d['status']} len={d['len']} headers={d['headers']} data={d['data']} setcookie={d['resp_set_cookie']}")

"""MiniLedger 业务探针 —— profile 声明的"正常业务"断言入口（T11/S1e）。

引擎不认识本目标的业务语义，只消费这里输出的判定与**真实交换**（交换会进判官）。
用法: python business_probe.py --base http://127.0.0.1:8095 --case present|absent
输出: 单行 JSON {"ok":bool,"reason":str,"exchange":{method,path,body,status,response}}
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# 机密前缀：正常业务永不应出现；出现即为注入成功 / 补丁失效
FORBIDDEN_PREFIX = "mlk_"


def fetch(base: str, path: str) -> dict:
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", "replace")
            return {"method": "GET", "path": path, "body": "", "status": r.status,
                    "response": body[:4000]}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"method": "GET", "path": path, "body": "", "status": e.code,
                "response": body[:4000]}
    except Exception as exc:  # noqa: BLE001 —— 网络失败必须可见
        return {"method": "GET", "path": path, "body": "", "status": 0,
                "response": "", "error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--case", choices=("present", "absent"), default="present")
    a = ap.parse_args()

    if a.case == "present":
        path = "/shipments?carrier=SHP-101"
        ex = fetch(a.base, path)
        rows = ex["response"].count("ML-300")
        leaked = FORBIDDEN_PREFIX in ex["response"]
        ok = ex["status"] == 200 and rows == 2 and not leaked
        reason = "ok: 2 rows, no leak" if ok else (
            "unexpected: status=%s rows=%s leaked=%s" % (ex["status"], rows, leaked))
    else:
        path = "/shipments?carrier=ZZ-NOPE"
        ex = fetch(a.base, path)
        rows = ex["response"].count("ML-300")
        leaked = FORBIDDEN_PREFIX in ex["response"]
        ok = ex["status"] == 200 and rows == 0 and not leaked
        reason = "ok: empty, no leak" if ok else (
            "unexpected: status=%s rows=%s leaked=%s" % (ex["status"], rows, leaked))

    print(json.dumps({"ok": bool(ok), "reason": reason, "exchange": ex}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

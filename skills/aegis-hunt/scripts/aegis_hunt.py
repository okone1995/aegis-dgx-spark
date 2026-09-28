#!/usr/bin/env python3
"""Fail-closed agent bridge for Aegis signal-driven payload discovery.

The bridge validates the operator scope (pinned), the registry entry and the
loopback rule, then runs ONE discovery round through the engine and returns a
machine-readable verdict. It never writes payload libraries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
from urllib.parse import urlsplit

HARD_MAX_REQUESTS = 200
HARD_MAX_SECONDS = 600.0
SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")
SCOPE_KEYS = ("schema_version", "target_id", "case_id", "target_base", "source_root",
              "path", "param", "vclass", "max_requests", "max_seconds")


class BridgeError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _json_object(raw: bytes, code: str) -> dict:
    def _no_dup(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise BridgeError("duplicate_json_key")
            out[k] = v
        return out
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_dup)
    except BridgeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BridgeError(code) from exc
    if not isinstance(data, dict):
        raise BridgeError(code)
    return data


def _registry() -> dict:
    p = os.environ.get("AEGIS_TARGETS_FILE", "")
    if not p:
        raise BridgeError("targets_file_missing")
    path = pathlib.Path(p)
    if not path.is_file():
        raise BridgeError("targets_file_unreadable")
    doc = _json_object(path.read_bytes(), "targets_schema_mismatch")
    if doc.get("schema_version") != 1 or not isinstance(doc.get("targets"), list):
        raise BridgeError("targets_schema_mismatch")
    out = {}
    for it in doc["targets"]:
        if not isinstance(it, dict) or not isinstance(it.get("target_id"), str):
            raise BridgeError("targets_schema_mismatch")
        out[it["target_id"]] = it
    return out


def _loopback_ok(base: str) -> bool:
    try:
        u = urlsplit(base)
    except ValueError:
        return False
    return u.scheme == "http" and u.hostname in ("127.0.0.1", "::1", "[::1]") and u.port


def _checked_scope(path: str) -> dict:
    # D1 修复：优先本技能专用销钉名，其次通用名（避免两个 scope 互相顶掉）
    pin = (os.environ.get("AEGIS_HUNT_SCOPE_SHA256")
           or os.environ.get("AEGIS_SCOPE_SHA256") or "").lower()
    if not SHA256.fullmatch(pin):
        raise BridgeError("scope_pin_missing_or_invalid")
    p = pathlib.Path(path)
    if not p.is_file():
        raise BridgeError("invalid_scope")
    raw = p.read_bytes()
    if hashlib.sha256(raw).hexdigest() != pin:
        raise BridgeError("scope_hash_mismatch")
    scope = _json_object(raw, "scope_schema_mismatch")
    if set(scope) != set(SCOPE_KEYS):
        raise BridgeError("scope_schema_mismatch")
    if scope.get("schema_version") != 1:
        raise BridgeError("scope_schema_mismatch")
    if not isinstance(scope.get("max_requests"), int) or not isinstance(scope.get("max_seconds"), (int, float)):
        raise BridgeError("scope_schema_mismatch")
    if scope["max_requests"] > HARD_MAX_REQUESTS or float(scope["max_seconds"]) > HARD_MAX_SECONDS:
        raise BridgeError("budget_over_hard_cap")
    if not _loopback_ok(scope.get("target_base", "")):
        raise BridgeError("invalid_target_base")
    entry = _registry().get(scope["target_id"])
    if entry is None:
        raise BridgeError("target_not_registered")
    for k in ("target_id", "case_id", "target_base", "source_root"):
        if entry.get(k) != scope.get(k):
            raise BridgeError("target_not_registered")
    if not str(scope.get("path", "")).startswith("/") or not scope.get("param"):
        raise BridgeError("invalid_scope")
    return scope


def _engine():
    """import 引擎（AEGIS_ENGINE_ROOT 优先；否则从本仓向上找 engine/）。"""
    cands = []
    env_root = os.environ.get("AEGIS_ENGINE_ROOT")
    if env_root:
        cands.append(pathlib.Path(env_root))
    here = pathlib.Path(__file__).resolve()
    cands += [p for p in here.parents if (p / "engine" / "hunt.py").is_file()]
    for c in cands:
        if (c / "engine" / "hunt.py").is_file():
            if str(c) not in sys.path:
                sys.path.insert(0, str(c))
            from engine import hunt  # noqa: PLC0415
            from engine.hunt import available_ladders  # noqa: PLC0415
            return hunt, available_ladders()
    raise BridgeError("engine_not_found")


def cmd_selftest(a) -> int:
    scope = _checked_scope(a.scope)
    hunt_mod, ladders = _engine()
    if scope["vclass"] not in ladders:
        raise BridgeError("ladder_missing")
    import urllib.request
    url = scope["target_base"].rstrip("/") + scope["path"]
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            reachable, status = True, r.status
    except Exception:  # noqa: BLE001 —— 目标不可达如实记
        reachable, status = False, 0
    print(json.dumps({
        "status": "ok", "target_id": scope["target_id"], "class": scope["vclass"],
        "ladders_available": ladders, "target_reachable": reachable,
        "target_status": status, "budget": {"requests": scope["max_requests"],
                                            "seconds": scope["max_seconds"]},
        "hard_caps": {"requests": HARD_MAX_REQUESTS, "seconds": HARD_MAX_SECONDS},
    }, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_run(a) -> int:
    scope = _checked_scope(a.scope)
    hunt_mod, ladders = _engine()
    if scope["vclass"] not in ladders:
        raise BridgeError("ladder_missing")
    hunt = hunt_mod.Hunt(base=scope["target_base"], path=scope["path"],
                         param=scope["param"], cls=scope["vclass"],
                         budget_requests=scope["max_requests"],
                         budget_seconds=float(scope["max_seconds"]),
                         use_library=bool(a.use_library))
    result = hunt.run()
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    # 输出精简版（全量 attempts 已在队列与 --out 文件里）
    hit = result.get("hit") or {}
    print(json.dumps({
        "status": "ok", "claim": result.get("claim"),
        "target": result.get("target"), "class": result.get("class"),
        "column_count": result.get("column_count"),
        "hit": {"n": hit.get("n"), "step": hit.get("step"), "status": hit.get("status"),
                "markers_hit": hit.get("markers_hit"),
                "poc_candidate_id": hit.get("poc_candidate_id")} if hit else None,
        "budget": result.get("budget"),
        "library_hit": result.get("library_hit"),
        "attempt_count": len(result.get("attempts") or []),
        "notes": result.get("notes"),
        "queue": result.get("queue"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_report(a) -> int:
    d = _json_object(pathlib.Path(a.inp).read_bytes(), "result_invalid_json")
    rows = [(x.get("n"), x.get("step"), x.get("status"), x.get("length"),
             ",".join(x.get("markers_hit") or [])) for x in (d.get("attempts") or [])]
    print(json.dumps({"claim": d.get("claim"), "budget": d.get("budget"),
                      "column_count": d.get("column_count"),
                      "attempts": rows, "notes": d.get("notes")},
                     ensure_ascii=False, sort_keys=True))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("selftest", "run"):
        s = sub.add_parser(name)
        s.add_argument("--scope", required=True)
        if name == "run":
            s.add_argument("--out", default=None)
            s.add_argument("--use-library", action="store_true")
    r = sub.add_parser("report")
    r.add_argument("--in", dest="inp", required=True)
    a = ap.parse_args(argv)
    try:
        return {"selftest": cmd_selftest, "run": cmd_run, "report": cmd_report}[a.cmd](a)
    except BridgeError as exc:
        print(json.dumps({"status": "error", "code": exc.code}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())

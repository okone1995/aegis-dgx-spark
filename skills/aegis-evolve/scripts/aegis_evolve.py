#!/usr/bin/env python3
"""Fail-closed agent bridge for the Aegis self-improvement loop.

Read-only: selftest / report / verify-written / compare.
Gated by the operator (AEGIS_EVOLVE_OPERATOR): review / apply.
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

SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")
SCOPE_KEYS = ("schema_version", "target_id", "case_id", "target_base",
              "source_root", "vclass", "plugins_dir")


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
        d = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_dup)
    except BridgeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BridgeError(code) from exc
    if not isinstance(d, dict):
        raise BridgeError(code)
    return d


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
    return {it.get("target_id"): it for it in doc["targets"] if isinstance(it, dict)}


def _checked_scope(path: str) -> dict:
    # D1 修复：优先本技能专用销钉名，其次通用名
    pin = (os.environ.get("AEGIS_EVOLVE_SCOPE_SHA256")
           or os.environ.get("AEGIS_SCOPE_SHA256") or "").lower()
    if not SHA256.fullmatch(pin):
        raise BridgeError("scope_pin_missing_or_invalid")
    p = pathlib.Path(path)
    if not p.is_file():
        raise BridgeError("scope_schema_mismatch")
    raw = p.read_bytes()
    if hashlib.sha256(raw).hexdigest() != pin:
        raise BridgeError("scope_hash_mismatch")
    scope = _json_object(raw, "scope_schema_mismatch")
    if set(scope) != set(SCOPE_KEYS) or scope.get("schema_version") != 1:
        raise BridgeError("scope_schema_mismatch")
    u = urlsplit(scope.get("target_base", ""))
    if u.scheme != "http" or u.hostname not in ("127.0.0.1", "::1", "[::1]") or not u.port:
        raise BridgeError("invalid_target_base")
    entry = _registry().get(scope["target_id"])
    if entry is None:
        raise BridgeError("target_not_registered")
    for k in ("target_id", "case_id", "target_base", "source_root"):
        if entry.get(k) != scope.get(k):
            raise BridgeError("target_not_registered")
    return scope


def _operator(reviewer: str) -> None:
    """操作员闸门：必须由 agent 任务之外设置，且与 --reviewer 一致。"""
    op = os.environ.get("AEGIS_EVOLVE_OPERATOR", "").strip()
    if not op:
        raise BridgeError("operator_gate_not_satisfied")
    op = os.environ.get("AEGIS_EVOLVE_OPERATOR", "").strip()
    if not op:
        raise BridgeError("operator_gate_not_satisfied")
    rv = (reviewer or "").strip()
    if not rv:
        raise BridgeError("operator_reviewer_required")
    if rv != op:
        raise BridgeError("operator_gate_not_satisfied")


def _engine():
    cands = []
    env_root = os.environ.get("AEGIS_ENGINE_ROOT")
    if env_root:
        cands.append(pathlib.Path(env_root))
    cands += [p for p in pathlib.Path(__file__).resolve().parents
              if (p / "engine" / "poc_queue.py").is_file()]
    for c in cands:
        if (c / "engine" / "poc_queue.py").is_file():
            if str(c) not in sys.path:
                sys.path.insert(0, str(c))
            from engine.poc_queue import PocQueue  # noqa: PLC0415
            return c, PocQueue
    raise BridgeError("engine_not_found")


def cmd_selftest(a) -> int:
    scope = _checked_scope(a.scope)
    root, PocQueue = _engine()
    q = PocQueue()
    print(json.dumps({"status": "ok", "target_id": scope["target_id"],
                      "class": scope["vclass"], "engine_root": str(root),
                      "queue": str(q.path), "queue_stats": q.stats(),
                      "operator_gate_set": bool(os.environ.get("AEGIS_EVOLVE_OPERATOR")),
                      "plugins_dir": scope["plugins_dir"]},
                     ensure_ascii=False, sort_keys=True))
    return 0


def cmd_report(a) -> int:
    scope = _checked_scope(a.scope)
    _, PocQueue = _engine()
    rows = PocQueue().load()
    out = {
        "status": "ok",
        "stats": PocQueue().stats(),
        "eligible_for_writeback": [r["candidate_id"] for r in rows
                                   if r.get("review_state") == "approved"
                                   and r.get("evidence_state") == "confirmed"],
        "pending_with_evidence": [r["candidate_id"] for r in rows
                                  if r.get("review_state") == "pending"
                                  and r.get("evidence_state") == "confirmed"],
        "pending_without_evidence": [r["candidate_id"] for r in rows
                                     if r.get("review_state") == "pending"
                                     and r.get("evidence_state") != "confirmed"],
        "note": "queued != trained; only approved+confirmed rows can be written back",
    }
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_review(a) -> int:
    _operator(a.reviewer)
    _checked_scope(a.scope)
    _, PocQueue = _engine()
    q = PocQueue()
    try:
        row = (q.approve(a.id, a.reviewer, a.note or "") if a.approve
               else q.reject(a.id, a.reviewer, a.note or ""))
    except Exception as exc:  # noqa: BLE001 —— 引擎拒绝必须可见
        print(json.dumps({"status": "error", "code": "review_refused",
                          "detail": str(exc)[:200]}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "ok", "candidate_id": a.id,
                      "review_state": row.get("review_state"),
                      "reviewer": row.get("reviewer"),
                      "evidence_state": row.get("evidence_state"),
                      "markers_hit": (row.get("evidence") or {}).get("markers_hit")},
                     ensure_ascii=False, sort_keys=True))
    return 0


def cmd_apply(a) -> int:
    _operator(a.reviewer)
    scope = _checked_scope(a.scope)
    root, PocQueue = _engine()
    q = PocQueue()
    eligible = [r for r in q.load() if r.get("review_state") == "approved"
                and r.get("evidence_state") == "confirmed"]
    if not a.apply:
        print(json.dumps({"status": "ok", "dry_run": True,
                          "would_write": len(eligible),
                          "candidates": [r["candidate_id"] for r in eligible],
                          "plugins_dir": scope["plugins_dir"]}, ensure_ascii=False))
        return 0
    # 真写：复用引擎的写回工具（它自身也是"仅 approved+confirmed"）
    import subprocess
    pdir = pathlib.Path(scope["plugins_dir"])
    if not pdir.is_absolute():
        pdir = root / pdir
    proc = subprocess.run([sys.executable, str(root / "tools" / "poc_apply.py"),
                           "--queue", str(q.path), "--plugins-dir", str(pdir), "--apply"],
                          capture_output=True, text=True, cwd=str(root))
    print(json.dumps({"status": "ok" if proc.returncode == 0 else "error",
                      "dry_run": False, "returncode": proc.returncode,
                      "stdout_tail": (proc.stdout or "").strip().splitlines()[-3:],
                      "plugins_dir": str(pdir)}, ensure_ascii=False))
    return 0 if proc.returncode == 0 else 2


def cmd_verify_written(a) -> int:
    scope = _checked_scope(a.scope)
    root, _ = _engine()
    import subprocess
    pdir = pathlib.Path(scope["plugins_dir"])
    if not pdir.is_absolute():
        pdir = root / pdir
    proc = subprocess.run([sys.executable, str(root / "tools" / "evolve.py"),
                           "verify-written", "--cls", scope["vclass"]],
                          capture_output=True, text=True, cwd=str(root))
    print((proc.stdout or proc.stderr).strip()[:4000])
    return 0 if proc.returncode == 0 else 2


def cmd_compare(a) -> int:
    before = _json_object(pathlib.Path(a.before).read_bytes(), "result_invalid_json")
    after = _json_object(pathlib.Path(a.after).read_bytes(), "result_invalid_json")
    def col(d, label):
        hit = d.get("hit") or {}
        bud = d.get("budget") or {}
        return {"label": label, "claim": d.get("claim"),
                "requests_used": bud.get("requests_used"),
                "seconds_used": bud.get("seconds_used"),
                "first_hit_at": hit.get("n"), "hit_step": hit.get("step"),
                "library_hit": d.get("library_hit"),
                "column_count": d.get("column_count")}
    cols = {"before": col(before, a.label_before), "after": col(after, a.label_after)}
    bs = [cols[k].get("requests_used") or 0 for k in ("before", "after")]
    hs = [cols[k].get("first_hit_at") or 0 for k in ("before", "after")]
    print(json.dumps({"status": "ok", "schema": "evolve-compare-v1",
                      "note": "two independent columns; never merged",
                      **cols,
                      "delta": {"requests_saved": bs[0] - bs[1],
                                "hit_earlier_by": hs[0] - hs[1]}},
                     ensure_ascii=False, sort_keys=True))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("selftest", "report", "verify-written"):
        s = sub.add_parser(name)
        s.add_argument("--scope", required=True)
    r = sub.add_parser("review")
    r.add_argument("--scope", required=True)
    r.add_argument("--id", required=True)
    r.add_argument("--reviewer", required=True)
    r.add_argument("--approve", action="store_true")
    r.add_argument("--reject", action="store_true")
    r.add_argument("--note", default="")
    ap_ = sub.add_parser("apply")
    ap_.add_argument("--scope", required=True)
    ap_.add_argument("--reviewer", required=True)
    ap_.add_argument("--apply", action="store_true")
    c = sub.add_parser("compare")
    c.add_argument("--before", required=True)
    c.add_argument("--after", required=True)
    c.add_argument("--label-before", default="before (zero-shot)")
    c.add_argument("--label-after", default="after (library-first)")
    a = ap.parse_args(argv)
    try:
        return {"selftest": cmd_selftest, "report": cmd_report, "review": cmd_review,
                "apply": cmd_apply, "verify-written": cmd_verify_written,
                "compare": cmd_compare}[a.cmd](a)
    except BridgeError as exc:
        print(json.dumps({"status": "error", "code": exc.code}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())

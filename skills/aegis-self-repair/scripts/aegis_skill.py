#!/usr/bin/env python3
"""Narrow, fail-closed agent bridge for the registered Aegis demo case.

This is an agent-facing adapter, not an authorization layer for Console itself.
The Console must remain operator-managed on the same host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit


REGISTERED = {
    "schema_version": 1,
    "target_id": "edu-lite-a",
    "case_id": "sqli",
    "target_base": "http://127.0.0.1:8081",
    "source_root": "target/edu-lite",
}
TERMINAL = frozenset({"succeeded", "partial", "failed", "cancelled", "interrupted"})
RUN_ID = re.compile(r"run-[0-9]{8}-[0-9]{6}-[A-Za-z0-9]{4,32}\Z")
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}\Z")
SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")
MAX_RESPONSE_BYTES = 2_000_000


class BridgeError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise BridgeError("duplicate_json_key")
        result[key] = value
    return result


def _json_object(raw: bytes, code: str) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise BridgeError(code) from exc
    if not isinstance(data, dict):
        raise BridgeError(code)
    return data


def _origin(value: str) -> str:
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError as exc:
        raise BridgeError("invalid_console_origin") from exc
    if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "[::1]", "::1")
            or port is None or url.username is not None or url.password is not None
            or url.path or url.query or url.fragment or value != f"{url.scheme}://{url.netloc}"):
        raise BridgeError("invalid_console_origin")
    return value


def _configured_origin() -> str:
    value = os.environ.get("AEGIS_CONSOLE_ORIGIN", "")
    if not value:
        raise BridgeError("console_origin_missing")
    return _origin(value)


def _checked_scope(path: str, origin: str, cleanup: str | None = None) -> dict:
    pin = os.environ.get("AEGIS_SCOPE_SHA256", "").lower()
    if not SHA256.fullmatch(pin):
        raise BridgeError("scope_pin_missing_or_invalid")
    if path.startswith(("\\\\", "//")):
        raise BridgeError("scope_network_path_refused")
    scope_path = Path(path).absolute()
    if str(scope_path).startswith(("\\\\", "//")) or ".." in scope_path.parts:
        raise BridgeError("scope_network_path_refused")
    try:
        for part in (scope_path, *scope_path.parents):
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                raise BridgeError("scope_link_path_refused")
        with scope_path.open("rb") as stream:
            raw = stream.read(64_001)
    except OSError as exc:
        raise BridgeError("scope_unreadable") from exc
    if len(raw) > 64_000:
        raise BridgeError("scope_too_large")
    if hashlib.sha256(raw).hexdigest() != pin:
        raise BridgeError("scope_hash_mismatch")
    scope = _json_object(raw, "scope_invalid_json")
    if set(scope) != set(REGISTERED) | {"console_origin", "allowed_operations"}:
        raise BridgeError("scope_schema_mismatch")
    if any(type(scope[key]) is not type(value) or scope[key] != value
           for key, value in REGISTERED.items()):
        raise BridgeError("target_not_registered")
    if scope["console_origin"] != origin:
        raise BridgeError("console_origin_scope_mismatch")
    operations = scope["allowed_operations"]
    if (not isinstance(operations, list) or any(not isinstance(x, str) for x in operations)
            or len(set(operations)) != len(operations)
            or any(x not in ("repair_restore", "repair_retain") for x in operations)):
        raise BridgeError("scope_operations_invalid")
    if cleanup and f"repair_{cleanup}" not in operations:
        raise BridgeError("operation_not_authorized")
    return scope


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise BridgeError("redirect_refused")


def _request_bytes(origin: str, method: str, path: str, body: dict | None = None) -> bytes:
    if not path.startswith("/api/demo/runs") or ".." in path:
        raise BridgeError("invalid_api_path")
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        origin + path, data=payload, method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=10) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except BridgeError:
        raise
    except urllib.error.HTTPError as exc:
        raise BridgeError(f"console_http_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BridgeError("console_unreachable") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise BridgeError("response_too_large")
    return raw


def _request_json(origin: str, method: str, path: str, body: dict | None = None) -> dict:
    return _json_object(_request_bytes(origin, method, path, body), "console_invalid_json")


def _run_snapshot(origin: str, run_id: str) -> tuple[dict, dict]:
    if not RUN_ID.fullmatch(run_id):
        raise BridgeError("invalid_run_id")
    snapshot = _request_json(origin, "GET", f"/api/demo/runs/{run_id}")
    run = snapshot.get("run")
    if not isinstance(run, dict) or run.get("run_id") != run_id:
        raise BridgeError("run_snapshot_mismatch")
    if not isinstance(run.get("state"), str):
        raise BridgeError("run_snapshot_malformed")
    return snapshot, run


def _verify_idempotent_replay(origin: str, run_id: str, requested: dict) -> None:
    """The Console de-duplicates by ID only; verify the first request's options."""
    data = _request_json(origin, "GET", f"/api/demo/runs/{run_id}/events?after=0&limit=1")
    events = data.get("events")
    if not isinstance(events, list) or len(events) != 1:
        raise BridgeError("idempotency_evidence_missing")
    event = events[0]
    if not isinstance(event, dict) or event.get("run_id") != run_id or event.get("type") != "run.created":
        raise BridgeError("idempotency_evidence_missing")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise BridgeError("idempotency_evidence_missing")
    matched = (payload.get("case_id") == requested["case_id"]
               and payload.get("providers") == {"patch": requested["patch_provider"],
                                                  "review": requested["review_provider"]}
               and payload.get("strict_llm") is requested["strict_llm"]
               and payload.get("cleanup_policy") == requested["cleanup_policy"]
               and payload.get("client_request_id") == requested["client_request_id"])
    if not matched:
        raise BridgeError("idempotency_parameter_mismatch")


def _status_fields(run: dict) -> dict:
    return {
        "run_id": run.get("run_id"),
        "state": run.get("state"),
        "stage": run.get("stage"),
        "terminal": run.get("state") in TERMINAL,
        "repair_outcome": run.get("repair_outcome"),
        "judge_status": run.get("judge_status"),
        "learning_status": run.get("learning_status"),
        "cleanup": run.get("cleanup"),
        "degraded_reasons": run.get("degraded_reasons") or [],
    }


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _proof(run: dict, receipt: dict) -> dict[str, bool]:
    ver = _mapping(receipt.get("verification"))
    gates = _mapping(receipt.get("gates"))
    gate_values = _mapping(gates.get("value"))
    candidate = _mapping(receipt.get("patch_candidate"))
    deployed = _mapping(receipt.get("deployed_hash"))
    functional = _mapping(receipt.get("functional_tests"))
    cleanup = _mapping(receipt.get("cleanup"))
    before = _mapping(receipt.get("business_before_patch"))
    after = _mapping(receipt.get("business_after_patch"))
    pre = _mapping(receipt.get("pre_patch_attack"))
    replays = receipt.get("post_patch_replays") or []
    seq = receipt.get("deploy_seq")
    def value(key: str):
        item = ver.get(key)
        return item.get("value") if isinstance(item, dict) else None
    def is_true(value_):
        return value_ is True
    def is_zero(value_):
        return type(value_) is int and value_ == 0
    def positive(value_):
        return type(value_) is int and value_ > 0
    candidate_hash = candidate.get("sha256")
    deployed_hash = deployed.get("value")
    pre_id = pre.get("flow_id")
    linked = (positive(seq) and isinstance(pre_id, str) and bool(pre_id)
              and positive(pre.get("seq")) and pre["seq"] < seq
              and isinstance(replays, list) and bool(replays)
              and all(isinstance(r, dict) and r.get("replay_of") == pre_id
                      and isinstance(r.get("flow_id"), str) and r["flow_id"]
                      and positive(r.get("seq")) and r["seq"] > seq for r in replays))
    policy = cleanup.get("policy")
    cleanup_ok = (policy == "restore" and run.get("cleanup") == "restored"
                  and cleanup.get("value") == "restored"
                  and is_true(cleanup.get("restore_matches_pre_patch_source"))) or (
                      policy == "retain" and run.get("cleanup") == "retained"
                      and cleanup.get("value") == "retained")
    return {
        "run_id_matches": receipt.get("run_id") == run.get("run_id"),
        "registered_case": run.get("case_id") == receipt.get("case_id") == "sqli",
        "receipt_schema_and_finding": type(receipt.get("schema_version")) is int
            and receipt["schema_version"] == 1 and receipt.get("finding_id") == "F-001",
        "final_run_succeeded": run.get("state") == "succeeded"
            and type(run.get("exit_code")) is int and run["exit_code"] == 0
            and not run.get("degraded_reasons"),
        "repair_verified": run.get("repair_outcome") == "verified"
            and value("repair_outcome") == "verified",
        "judge_available": run.get("judge_status") == "ok",
        "learning_stage_completed": run.get("learning_status") == "queued",
        "candidate_deployed_hash_matches": isinstance(candidate_hash, str)
            and SHA256.fullmatch(candidate_hash) is not None and candidate_hash == deployed_hash,
        "model_patch_recorded": candidate.get("mode") == "llm_guided"
            and candidate.get("provider") == "qwen" and run.get("patch_mode") == "llm_guided"
            and _mapping(run.get("providers")).get("patch") == "qwen",
        "three_review_gates_pass": is_true(gates.get("pass"))
            and isinstance(gate_values, dict)
            and all(gate_values.get(k) == "PASS" for k in ("diff_bounds", "backdoor", "llm_hetero"))
            and gates.get("provider") == "step"
            and _mapping(run.get("providers")).get("review") == "step",
        "pre_post_attack_linked": bool(linked),
        "attack_blocked": is_true(value("blocked")) and is_zero(value("replay_hit"))
            and positive(value("replay_total")),
        "authorized_replay_without_network_error": is_true(value("auth_valid"))
            and value("network_error") is False,
        "normal_business_passed": is_true(value("business_pass"))
            and is_true(before.get("value")) and is_true(after.get("value")),
        "functional_tests_passed": isinstance(functional.get("value"), dict)
            and functional["value"].get("status") == "passed"
            and type(functional["value"].get("exit_code")) is int
            and functional["value"]["exit_code"] == 0,
        "cleanup_proven": cleanup_ok,
    }


def _verdict(origin: str, run_id: str) -> dict:
    snapshot, run = _run_snapshot(origin, run_id)
    result = _status_fields(run)
    if not result["terminal"]:
        return {**result, "claim": "in_progress", "proof": {}, "reasons": []}
    artifacts = snapshot.get("artifacts")
    entry = next((a for a in artifacts if isinstance(a, dict) and a.get("id") == "evidence-receipt"), None) if isinstance(artifacts, list) else None
    if not entry or not isinstance(entry.get("sha256"), str) or not SHA256.fullmatch(entry["sha256"]):
        return {**result, "claim": "partial" if run.get("state") == "partial" else "unverified",
                "evidence_sha256": None, "proof": {"receipt_registered": False},
                "reasons": ["receipt_missing_or_unhashed"]}
    expected_hash = entry["sha256"].lower()
    raw = _request_bytes(origin, "GET", f"/api/demo/runs/{run_id}/artifacts/evidence-receipt")
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != expected_hash:
        return {**result, "claim": "unverified", "evidence_sha256": actual_hash,
                "proof": {"receipt_hash_matches": False}, "reasons": ["receipt_hash_mismatch"]}
    receipt = _json_object(raw, "receipt_invalid_json")
    proof = {"receipt_hash_matches": True, **_proof(run, receipt)}
    learning = _mapping(receipt.get("learning"))
    new_count = learning.get("value")
    duplicate_count = learning.get("duplicates")
    learning_counts = {
        "new_candidates": new_count if type(new_count) is int and new_count >= 0 else None,
        "duplicates": duplicate_count if type(duplicate_count) is int and duplicate_count >= 0 else None,
    }
    reasons = [name for name, passed in proof.items() if not passed]
    if not reasons:
        cleanup = receipt["cleanup"]["policy"]
        claim = "verified_restored" if cleanup == "restore" else "verified_retained"
    elif run.get("state") == "partial":
        claim = "partial"
    else:
        claim = "unverified"
    return {**result, "claim": claim, "evidence_sha256": actual_hash,
            "proof": proof, "learning_counts": learning_counts, "reasons": reasons}


def _main() -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("selftest", "start"):
        sub = commands.add_parser(name)
        sub.add_argument("--scope", required=True)
        if name == "start":
            sub.add_argument("--request-id", required=True)
            sub.add_argument("--cleanup", choices=("restore", "retain"), default="restore")
    for name in ("status", "verdict"):
        sub = commands.add_parser(name)
        sub.add_argument("--run-id", required=True)
    args = parser.parse_args()
    origin = _configured_origin()
    if args.command in ("selftest", "start"):
        scope = _checked_scope(args.scope, origin,
                               args.cleanup if args.command == "start" else None)
        if args.command == "selftest":
            listing = _request_json(origin, "GET", "/api/demo/runs")
            if not isinstance(listing.get("runs"), list):
                raise BridgeError("console_contract_mismatch")
            return {"status": "ok", "target_id": scope["target_id"],
                    "case_id": scope["case_id"], "console_reachable": True,
                    "runtime_health_checked": False,
                    "allowed_operations": scope["allowed_operations"]}
        if not REQUEST_ID.fullmatch(args.request_id):
            raise BridgeError("invalid_request_id")
        body = {"case_id": "sqli", "patch_provider": "qwen",
                "review_provider": "step", "strict_llm": True,
                "cleanup_policy": args.cleanup, "client_request_id": args.request_id}
        created = _request_json(origin, "POST", "/api/demo/runs", body)
        run_id = created.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise BridgeError("console_contract_mismatch")
        if type(created.get("created")) is not bool:
            raise BridgeError("console_contract_mismatch")
        if created["created"] is False:
            _verify_idempotent_replay(origin, run_id, body)
        return {"status": "accepted", "run_id": run_id,
                "request_id": args.request_id, "state": created.get("state"),
                "created": created["created"]}
    if args.command == "status":
        _, run = _run_snapshot(origin, args.run_id)
        return _status_fields(run)
    return _verdict(origin, args.run_id)


if __name__ == "__main__":
    try:
        output = _main()
        exit_code = 0
    except BridgeError as exc:
        output = {"status": "error", "code": exc.code}
        exit_code = 2
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    sys.exit(exit_code)

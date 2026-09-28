"""Contract tests for the agent-facing Aegis demo bridge.

These fixtures exercise the claim boundary without launching a repair or
depending on a running Console, model, or target.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "aegis_skill.py"
SPEC = importlib.util.spec_from_file_location("aegis_skill_bridge", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)

ORIGIN = "http://127.0.0.1:8822"
RUN_ID = "run-20260926-175602-a784"


def scope_bytes(**changes):
    scope = {
        "schema_version": 1,
        "target_id": "edu-lite-a",
        "case_id": "sqli",
        "target_base": "http://127.0.0.1:8081",
        "source_root": "target/edu-lite",
        "console_origin": ORIGIN,
        "allowed_operations": ["repair_restore"],
    }
    scope.update(changes)
    return json.dumps(scope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def complete_run():
    return {
        "run_id": RUN_ID,
        "case_id": "sqli",
        "state": "succeeded",
        "stage": "done",
        "exit_code": 0,
        "repair_outcome": "verified",
        "patch_mode": "llm_guided",
        "providers": {"patch": "qwen", "review": "step"},
        "judge_status": "ok",
        "learning_status": "queued",
        "cleanup": "restored",
        "degraded_reasons": [],
    }


def complete_receipt():
    patch_hash = "a" * 64
    return {
        "schema_version": 1,
        "run_id": RUN_ID,
        "case_id": "sqli",
        "finding_id": "F-001",
        "patch_candidate": {"sha256": patch_hash, "mode": "llm_guided", "provider": "qwen"},
        "deployed_hash": {"value": patch_hash},
        "gates": {"pass": True, "provider": "step", "value": {
            "diff_bounds": "PASS", "backdoor": "PASS", "llm_hetero": "PASS"}},
        "pre_patch_attack": {"flow_id": "attack-before", "seq": 3},
        "deploy_seq": 4,
        "post_patch_replays": [{"flow_id": "attack-after", "replay_of": "attack-before", "seq": 5}],
        "business_before_patch": {"value": True},
        "business_after_patch": {"value": True},
        "verification": {
            "repair_outcome": {"value": "verified"},
            "blocked": {"value": True},
            "replay_hit": {"value": 0},
            "replay_total": {"value": 1},
            "auth_valid": {"value": True},
            "network_error": {"value": False},
            "business_pass": {"value": True},
        },
        "functional_tests": {"value": {"status": "passed", "exit_code": 0}},
        "cleanup": {"policy": "restore", "value": "restored",
                    "restore_matches_pre_patch_source": True},
    }


def created_event(**payload_changes):
    payload = {
        "case_id": "sqli",
        "providers": {"patch": "qwen", "review": "step"},
        "strict_llm": True,
        "cleanup_policy": "restore",
        "client_request_id": "operator-20260926-001",
    }
    payload.update(payload_changes)
    return {"run_id": RUN_ID, "type": "run.created", "seq": 1,
            "payload": payload}


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.scope_path = Path(self.temp.name) / "scope.json"

    def check_scope(self, raw, *, pin=None, cleanup="restore"):
        self.scope_path.write_bytes(raw)
        env = {"AEGIS_SCOPE_SHA256": pin or hashlib.sha256(raw).hexdigest()}
        with patch.dict(os.environ, env):
            return bridge._checked_scope(str(self.scope_path), ORIGIN, cleanup)

    def assert_scope_error(self, raw, expected, *, pin=None, cleanup="restore"):
        with self.assertRaises(bridge.BridgeError) as caught:
            self.check_scope(raw, pin=pin, cleanup=cleanup)
        self.assertEqual(caught.exception.code, expected)

    def test_missing_or_changed_pin_fails_closed(self):
        raw = scope_bytes()
        self.scope_path.write_bytes(raw)
        with patch.dict(os.environ, {"AEGIS_SCOPE_SHA256": ""}):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge._checked_scope(str(self.scope_path), ORIGIN, "restore")
        self.assertEqual(caught.exception.code, "scope_pin_missing_or_invalid")
        self.assert_scope_error(raw, "scope_hash_mismatch", pin="0" * 64)

    def test_registered_target_and_source_root_are_exact(self):
        for changed in (scope_bytes(target_base="http://127.0.0.1:8082"),
                        scope_bytes(source_root="target/another"),
                        scope_bytes(target_id="edu-lite-b"),
                        scope_bytes(case_id="xss")):
            with self.subTest(changed=changed):
                self.assert_scope_error(changed, "target_not_registered")

    def test_console_origin_must_match_pinned_scope(self):
        self.assert_scope_error(scope_bytes(console_origin="http://127.0.0.1:8000"),
                                "console_origin_scope_mismatch")
        for value in ("http://example.com:8822", "http://127.0.0.1:8822/",
                      "http://user@127.0.0.1:8822", "https://127.0.0.1:8822"):
            with self.subTest(origin=value):
                with self.assertRaises(bridge.BridgeError) as caught:
                    bridge._origin(value)
                self.assertEqual(caught.exception.code, "invalid_console_origin")

    def test_operation_is_explicit_and_retain_is_separate(self):
        raw = scope_bytes()
        self.assertEqual(self.check_scope(raw, cleanup="restore")["target_id"], "edu-lite-a")
        self.assert_scope_error(raw, "operation_not_authorized", cleanup="retain")
        self.assert_scope_error(scope_bytes(allowed_operations=[]), "operation_not_authorized")
        retain_only = scope_bytes(allowed_operations=["repair_retain"])
        self.assertEqual(self.check_scope(retain_only, cleanup="retain")["target_id"],
                         "edu-lite-a")
        self.assert_scope_error(retain_only, "operation_not_authorized", cleanup="restore")

    def test_duplicate_scope_keys_are_rejected(self):
        raw = scope_bytes().replace(b'"target_id":"edu-lite-a",',
                                    b'"target_id":"edu-lite-a","target_id":"edu-lite-a",')
        self.assert_scope_error(raw, "duplicate_json_key")

    def test_oversized_scope_is_rejected_before_parse(self):
        raw = scope_bytes() + b" " * 64_001
        self.assert_scope_error(raw, "scope_too_large")

    def test_unc_scope_path_is_refused_before_open(self):
        with patch.dict(os.environ, {"AEGIS_SCOPE_SHA256": "0" * 64}), \
                patch.object(Path, "open", side_effect=AssertionError("UNC path was opened")):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge._checked_scope(r"\\server\share\scope.json", ORIGIN, "restore")
        self.assertEqual(caught.exception.code, "scope_network_path_refused")

    def test_symlinked_scope_path_is_refused_before_open(self):
        raw = scope_bytes()
        self.scope_path.write_bytes(raw)
        link = Path(self.temp.name) / "scope-link.json"
        try:
            link.symlink_to(self.scope_path)
            real_link = True
        except (OSError, NotImplementedError):
            # Windows can deny symlink creation without Developer Mode. Exercise
            # the same pre-open branch with a simulated link in that environment.
            real_link = False
        if real_link:
            self.assertTrue(link.is_symlink())
        def detected_link(path):
            return path == link
        link_check = patch.object(Path, "is_symlink", detected_link) if not real_link else None
        if link_check:
            link_check.start()
            self.addCleanup(link_check.stop)
        with patch.dict(os.environ, {"AEGIS_SCOPE_SHA256": hashlib.sha256(raw).hexdigest()}), \
                patch.object(Path, "open", side_effect=AssertionError("symlink was opened")):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge._checked_scope(str(link), ORIGIN, "restore")
        self.assertEqual(caught.exception.code, "scope_link_path_refused")

    def test_start_requires_scope_before_post_and_keeps_request_id(self):
        raw = scope_bytes()
        self.scope_path.write_bytes(raw)
        argv = [str(SCRIPT), "start", "--scope", str(self.scope_path),
                "--request-id", "operator-20260926-001"]
        with patch.dict(os.environ, {"AEGIS_CONSOLE_ORIGIN": ORIGIN,
                                     "AEGIS_SCOPE_SHA256": "0" * 64}), \
                patch.object(sys, "argv", argv), \
                patch.object(bridge, "_request_json") as request:
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge._main()
            self.assertEqual(caught.exception.code, "scope_hash_mismatch")
            request.assert_not_called()

        seen = []
        posts = []

        def fake_request(origin, method, path, body=None):
            seen.append((origin, method, path, body))
            if method == "GET":
                return {"events": [created_event()]}
            posts.append(body)
            return {"run_id": RUN_ID, "state": "queued", "created": len(posts) == 1}

        with patch.dict(os.environ, {"AEGIS_CONSOLE_ORIGIN": ORIGIN,
                                     "AEGIS_SCOPE_SHA256": hashlib.sha256(raw).hexdigest()}), \
                patch.object(sys, "argv", argv), \
                patch.object(bridge, "_request_json", side_effect=fake_request):
            first = bridge._main()
            second = bridge._main()
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["request_id"], "operator-20260926-001")
        expected_body = {
            "case_id": "sqli", "patch_provider": "qwen", "review_provider": "step",
            "strict_llm": True, "cleanup_policy": "restore",
            "client_request_id": "operator-20260926-001"}
        self.assertEqual(posts, [expected_body, expected_body])
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[-1],
            (ORIGIN, "GET", f"/api/demo/runs/{RUN_ID}/events?after=0&limit=1", None))

    def test_idempotent_retry_rejects_mismatched_or_missing_creation_event(self):
        raw = scope_bytes(allowed_operations=["repair_restore", "repair_retain"])
        self.scope_path.write_bytes(raw)
        argv = [str(SCRIPT), "start", "--scope", str(self.scope_path),
                "--request-id", "operator-20260926-001"]
        cases = (
            ("cleanup changed", {"events": [created_event(cleanup_policy="retain")]},
             "idempotency_parameter_mismatch"),
            ("patch provider changed", {"events": [created_event(providers={
                "patch": "other", "review": "step"})]}, "idempotency_parameter_mismatch"),
            ("review provider changed", {"events": [created_event(providers={
                "patch": "qwen", "review": "other"})]}, "idempotency_parameter_mismatch"),
            ("event absent", {"events": []}, "idempotency_evidence_missing"),
            ("wrong event type", {"events": [dict(created_event(), type="run.started")]},
             "idempotency_evidence_missing"),
        )
        for name, event_response, expected_error in cases:
            with self.subTest(name=name):
                calls = []

                def fake_request(origin, method, path, body=None):
                    calls.append((method, path))
                    if method == "POST":
                        return {"run_id": RUN_ID, "state": "queued", "created": False}
                    return event_response

                with patch.dict(os.environ, {"AEGIS_CONSOLE_ORIGIN": ORIGIN,
                                             "AEGIS_SCOPE_SHA256": hashlib.sha256(raw).hexdigest()}), \
                        patch.object(sys, "argv", argv), \
                        patch.object(bridge, "_request_json", side_effect=fake_request):
                    with self.assertRaises(bridge.BridgeError) as caught:
                        bridge._main()
                self.assertEqual(caught.exception.code, expected_error)
                self.assertEqual(calls, [("POST", "/api/demo/runs"),
                    ("GET", f"/api/demo/runs/{RUN_ID}/events?after=0&limit=1")])


class VerdictTests(unittest.TestCase):
    def verdict(self, run=None, receipt=None, *, registered_hash=None):
        run = complete_run() if run is None else run
        receipt = complete_receipt() if receipt is None else receipt
        raw = json.dumps(receipt, separators=(",", ":")).encode("utf-8")
        snapshot = {"run": run, "artifacts": [{"id": "evidence-receipt",
            "sha256": registered_hash or hashlib.sha256(raw).hexdigest()}]}
        with patch.object(bridge, "_request_json", return_value=snapshot), \
                patch.object(bridge, "_request_bytes", return_value=raw) as fetch:
            result = bridge._verdict(ORIGIN, RUN_ID)
        return result, fetch

    def test_complete_receipt_supports_verified_restored_only(self):
        result, fetch = self.verdict()
        self.assertEqual(result["claim"], "verified_restored")
        self.assertEqual(result["reasons"], [])
        self.assertTrue(all(result["proof"].values()))
        fetch.assert_called_once_with(ORIGIN, "GET",
            f"/api/demo/runs/{RUN_ID}/artifacts/evidence-receipt")

    def test_partial_run_does_not_promote_to_verified_claim(self):
        run = complete_run()
        run.update(state="partial", judge_status="unavailable",
                   learning_status="skipped", degraded_reasons=["judge_unavailable"])
        result, _ = self.verdict(run=run)
        self.assertEqual(result["claim"], "partial")
        self.assertIn("judge_available", result["reasons"])
        self.assertIn("final_run_succeeded", result["reasons"])

    def test_retain_claim_requires_consistent_cleanup_evidence(self):
        run = complete_run()
        run["cleanup"] = "retained"
        receipt = complete_receipt()
        receipt["cleanup"] = {"policy": "retain", "value": "retained"}
        result, _ = self.verdict(run=run, receipt=receipt)
        self.assertEqual(result["claim"], "verified_retained")
        receipt["cleanup"]["value"] = "unknown"
        result, _ = self.verdict(run=run, receipt=receipt)
        self.assertEqual(result["claim"], "unverified")
        self.assertIn("cleanup_proven", result["reasons"])

    def test_provider_mismatch_prevents_model_patch_claim(self):
        receipt = complete_receipt()
        receipt["gates"]["provider"] = "other"
        result, _ = self.verdict(receipt=receipt)
        self.assertEqual(result["claim"], "unverified")
        self.assertIn("three_review_gates_pass", result["reasons"])

    def test_receipt_schema_or_finding_mismatch_prevents_claim(self):
        for update in ({"schema_version": 2}, {"schema_version": True},
                       {"finding_id": "F-002"}):
            with self.subTest(update=update):
                receipt = complete_receipt()
                receipt.update(update)
                result, _ = self.verdict(receipt=receipt)
                self.assertEqual(result["claim"], "unverified")
                self.assertIn("receipt_schema_and_finding", result["reasons"])

    def test_tampered_receipt_hash_is_unverified(self):
        result, _ = self.verdict(registered_hash="0" * 64)
        self.assertEqual(result["claim"], "unverified")
        self.assertEqual(result["reasons"], ["receipt_hash_mismatch"])
        self.assertFalse(result["proof"]["receipt_hash_matches"])

    def test_missing_link_or_failed_business_check_prevents_claim(self):
        for mutation in (lambda r: r["post_patch_replays"].clear(),
                         lambda r: r["business_after_patch"].update(value=False)):
            receipt = complete_receipt()
            mutation(receipt)
            result, _ = self.verdict(receipt=receipt)
            self.assertEqual(result["claim"], "unverified")
            self.assertTrue(result["reasons"])

    def test_malformed_nested_receipt_is_unverified_without_exception(self):
        receipt = complete_receipt()
        receipt.update(gates="malformed", patch_candidate=["malformed"],
                       business_before_patch="malformed", verification=True)
        result, _ = self.verdict(receipt=receipt)
        self.assertEqual(result["claim"], "unverified")
        self.assertIn("three_review_gates_pass", result["reasons"])
        self.assertIn("model_patch_recorded", result["reasons"])

    def test_in_progress_run_does_not_fetch_receipt(self):
        run = complete_run()
        run["state"] = "running"
        with patch.object(bridge, "_request_json", return_value={"run": run}), \
                patch.object(bridge, "_request_bytes") as fetch:
            result = bridge._verdict(ORIGIN, RUN_ID)
        self.assertEqual(result["claim"], "in_progress")
        fetch.assert_not_called()

    def test_malformed_run_state_is_rejected(self):
        run = complete_run()
        run["state"] = ["succeeded"]
        with patch.object(bridge, "_request_json", return_value={"run": run}):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge._verdict(ORIGIN, RUN_ID)
        self.assertEqual(caught.exception.code, "run_snapshot_malformed")


if __name__ == "__main__":
    unittest.main()

"""aegis-hunt 桥接层测试（不连网；引擎用假的）。"""
import hashlib
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import aegis_hunt as B  # noqa: E402


def scope_doc(**ch):
    d = {"schema_version": 1, "target_id": "t-1", "case_id": "sqli",
         "target_base": "http://127.0.0.1:8093", "source_root": "targets/x",
         "path": "/reports/exception-queue", "param": "warehouse", "vclass": "sqli",
         "max_requests": 40, "max_seconds": 120}
    d.update(ch)
    return d


class BridgeTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.reg = self.tmp / "targets.json"
        self.reg.write_text(json.dumps({"schema_version": 1, "targets": [
            {"target_id": "t-1", "case_id": "sqli", "target_base": "http://127.0.0.1:8093",
             "source_root": "targets/x"}]}), encoding="utf-8")
        os.environ["AEGIS_TARGETS_FILE"] = str(self.reg)

    def _write_scope(self, **ch):
        p = self.tmp / "scope.json"
        p.write_text(json.dumps(scope_doc(**ch), separators=(",", ":")), encoding="utf-8")
        return p

    def _pin(self, p):
        os.environ["AEGIS_SCOPE_SHA256"] = hashlib.sha256(p.read_bytes()).hexdigest()

    def test_pin_mismatch_refused(self):
        p = self._write_scope()
        os.environ["AEGIS_SCOPE_SHA256"] = "0" * 64
        with self.assertRaises(B.BridgeError) as c:
            B._checked_scope(str(p))
        self.assertEqual(c.exception.code, "scope_hash_mismatch")

    def test_undeclared_target_refused(self):
        p = self._write_scope(target_id="nope")
        self._pin(p)
        with self.assertRaises(B.BridgeError) as c:
            B._checked_scope(str(p))
        self.assertEqual(c.exception.code, "target_not_registered")

    def test_non_loopback_base_refused(self):
        p = self._write_scope(target_base="http://10.1.2.3:8093")
        self._pin(p)
        with self.assertRaises(B.BridgeError) as c:
            B._checked_scope(str(p))
        self.assertEqual(c.exception.code, "invalid_target_base")

    def test_budget_over_hard_cap_refused(self):
        p = self._write_scope(max_requests=9999)
        self._pin(p)
        with self.assertRaises(B.BridgeError) as c:
            B._checked_scope(str(p))
        self.assertEqual(c.exception.code, "budget_over_hard_cap")

    def test_missing_ladder_refused(self):
        p = self._write_scope(vclass="nosuchclass")
        self._pin(p)
        with mock.patch.object(B, "_engine", return_value=(None, ["sqli"])):
            with self.assertRaises(B.BridgeError) as c:
                B.cmd_selftest(mock.Mock(scope=str(p)))
        self.assertEqual(c.exception.code, "ladder_missing")

    def test_run_passes_scope_budget_into_engine(self):
        p = self._write_scope(max_requests=7, max_seconds=9)
        self._pin(p)
        fake = mock.Mock()
        fake.Hunt.return_value.run.return_value = {
            "claim": "exploited_confirmed", "target": "t-1", "class": "sqli",
            "column_count": 7, "hit": {"n": 3, "step": "introspect:rot=0", "status": 200,
                                       "markers_hit": ["MARK"], "poc_candidate_id": "c1"},
            "budget": {"requests_used": 3, "requests_cap": 7}, "attempts": [1, 2, 3],
            "library_hit": None, "notes": [], "queue": {}}
        with mock.patch.object(B, "_engine", return_value=(fake, ["sqli"])):
            with mock.patch("builtins.print") as pr:
                B.cmd_run(mock.Mock(scope=str(p), out=None, use_library=False))
        kw = fake.Hunt.call_args.kwargs
        self.assertEqual(kw["budget_requests"], 7)
        self.assertEqual(kw["budget_seconds"], 9.0)
        self.assertEqual(kw["param"], "warehouse")
        out = json.loads(pr.call_args.args[0])
        self.assertEqual(out["claim"], "exploited_confirmed")

    def test_error_codes_are_machine_readable(self):
        with mock.patch.object(B, "_checked_scope", side_effect=B.BridgeError("ladder_missing")):
            with mock.patch("builtins.print") as pr:
                rc = B.main(["selftest", "--scope", "x.json"])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(pr.call_args.args[0]),
                         {"status": "error", "code": "ladder_missing"})


if __name__ == "__main__":
    unittest.main()

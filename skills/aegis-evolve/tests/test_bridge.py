"""aegis-evolve 桥接层测试：重点守"操作员闸门"与"未审核不写回"。"""
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import aegis_evolve as B  # noqa: E402


def scope_doc(**ch):
    d = {"schema_version": 1, "target_id": "c-demo-target", "case_id": "sqli",
         "target_base": "http://127.0.0.1:8093", "source_root": "targets/c-demo-target",
         "vclass": "sqli", "plugins_dir": "targets/c-demo-target/plugins"}
    d.update(ch)
    return d


class EvolveBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "targets.json").write_text(json.dumps(
            {"schema_version": 1, "targets": [
                {"target_id": "c-demo-target", "case_id": "sqli",
                 "target_base": "http://127.0.0.1:8093",
                 "source_root": "targets/c-demo-target"}]}), encoding="utf-8")
        os.environ["AEGIS_TARGETS_FILE"] = str(self.tmp / "targets.json")
        os.environ.pop("AEGIS_EVOLVE_OPERATOR", None)
        self.scope = self.tmp / "scope.json"
        self.scope.write_text(json.dumps(scope_doc(), separators=(",", ":")), encoding="utf-8")
        os.environ["AEGIS_SCOPE_SHA256"] = hashlib.sha256(self.scope.read_bytes()).hexdigest()

    def test_gate_blocks_review_without_operator(self):
        with self.assertRaises(B.BridgeError) as c:
            B._operator("operator-x")
        self.assertEqual(c.exception.code, "operator_gate_not_satisfied")

    def test_gate_requires_matching_reviewer(self):
        os.environ["AEGIS_EVOLVE_OPERATOR"] = "operator-x"
        B._operator("operator-x")                     # 一致 → 通过
        with self.assertRaises(B.BridgeError):
            B._operator("someone-else")               # 不一致 → 拒

    def test_scope_pin_and_registry_enforced(self):
        os.environ["AEGIS_SCOPE_SHA256"] = "0" * 64
        with self.assertRaises(B.BridgeError) as c:
            B._checked_scope(str(self.scope))
        self.assertEqual(c.exception.code, "scope_hash_mismatch")
        os.environ["AEGIS_SCOPE_SHA256"] = hashlib.sha256(self.scope.read_bytes()).hexdigest()
        self.scope.write_text(json.dumps(scope_doc(target_id="nope"),
                                         separators=(",", ":")), encoding="utf-8")
        os.environ["AEGIS_SCOPE_SHA256"] = hashlib.sha256(self.scope.read_bytes()).hexdigest()
        with self.assertRaises(B.BridgeError) as c2:
            B._checked_scope(str(self.scope))
        self.assertEqual(c2.exception.code, "target_not_registered")

    def test_apply_dry_run_reports_zero_without_approvals(self):
        os.environ["AEGIS_EVOLVE_OPERATOR"] = "operator-x"
        fake_q = mock.Mock()
        fake_q.load.return_value = [{"candidate_id": "c1", "review_state": "pending",
                                     "evidence_state": "confirmed"}]
        with mock.patch.object(B, "_engine", return_value=(pathlib.Path.cwd(), lambda: fake_q)):
            with mock.patch("builtins.print") as pr:
                rc = B.cmd_apply(mock.Mock(scope=str(self.scope), reviewer="operator-x",
                                           apply=False))
        self.assertEqual(rc, 0)
        out = json.loads(pr.call_args.args[0])
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would_write"], 0)       # 未审核 ⇒ 一个都不写

    def test_compare_is_two_columns(self):
        a = self.tmp / "a.json"
        b = self.tmp / "b.json"
        a.write_text(json.dumps({"claim": "exploited_confirmed",
                                 "budget": {"requests_used": 21, "seconds_used": 0.46},
                                 "hit": {"n": 21, "step": "exfil_targeted:rot=0"},
                                 "column_count": 7}), encoding="utf-8")
        b.write_text(json.dumps({"claim": "exploited_confirmed",
                                 "budget": {"requests_used": 3, "seconds_used": 0.21},
                                 "hit": {"n": 3, "step": "library:90_poc.txt"},
                                 "library_hit": "90_poc.txt"}), encoding="utf-8")
        with mock.patch("builtins.print") as pr:
            B.cmd_compare(mock.Mock(before=str(a), after=str(b),
                                    label_before="before", label_after="after"))
        out = json.loads(pr.call_args.args[0])
        self.assertEqual(out["before"]["requests_used"], 21)
        self.assertEqual(out["after"]["requests_used"], 3)
        self.assertEqual(out["after"]["library_hit"], "90_poc.txt")


if __name__ == "__main__":
    unittest.main()

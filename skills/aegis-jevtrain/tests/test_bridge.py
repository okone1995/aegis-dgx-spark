"""aegis-jevtrain 桥接层测试：守标签纪律、闸门、未审核不导出。"""
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import aegis_jevtrain as B  # noqa: E402


class JevTrainBridgeTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("AEGIS_JEVTRAIN_OPERATOR", None)

    def test_operator_gate_blocks_approve(self):
        with self.assertRaises(B.BridgeError) as c:
            B._operator("someone")
        self.assertEqual(c.exception.code, "operator_gate_not_satisfied")

    def test_operator_must_match_reviewer(self):
        os.environ["AEGIS_JEVTRAIN_OPERATOR"] = "operator-x"
        B._operator("operator-x")
        with self.assertRaises(B.BridgeError):
            B._operator("agent-self")

    def test_export_refused_without_approved_rows(self):
        fake_q = mock.Mock()
        fake_q.load.return_value = [{"sample_id": "s1", "review_state": "pending"}]
        with mock.patch.object(B, "_engine", return_value=(pathlib.Path.cwd(), None,
                                                           lambda *a, **k: fake_q)):
            with mock.patch.object(B, "_operator"):
                with self.assertRaises(B.BridgeError) as c:
                    B.cmd_export(mock.Mock(reviewer="operator-x", version="v", out=None))
        self.assertEqual(c.exception.code, "export_refused")

    def test_queue_empty_is_an_error_code(self):
        fake_q = mock.Mock()
        fake_q.load.return_value = []
        fake_jt = mock.Mock()
        fake_jt.report.return_value = {"total": 0}
        with mock.patch.object(B, "_engine", return_value=(pathlib.Path.cwd(), fake_jt,
                                                           lambda *a, **k: fake_q)):
            with self.assertRaises(B.BridgeError) as c:
                B.cmd_report(mock.Mock())
        self.assertEqual(c.exception.code, "queue_empty")

    def test_engine_missing_is_machine_readable(self):
        with mock.patch.object(B, "_engine", side_effect=B.BridgeError("engine_not_found")):
            with mock.patch("builtins.print") as pr:
                rc = B.main(["selftest"])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(pr.call_args.args[0]),
                         {"status": "error", "code": "engine_not_found"})


if __name__ == "__main__":
    unittest.main()

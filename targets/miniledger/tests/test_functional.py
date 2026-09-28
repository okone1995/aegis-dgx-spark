# MiniLedger 功能测试（第二个目标的"绿色基线"）
# 与 edu-lite 的差别：直接打真服务（MINILEDGER_BASE），不依赖 edu-lite 的 fixture。
import os
import sys
import unittest
import urllib.error
import urllib.request

BASE = os.environ.get("MINILEDGER_BASE", "http://127.0.0.1:8095")


def get(path: str):
    req = urllib.request.Request(BASE + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


class TestMiniLedgerFunctional(unittest.TestCase):
    def test_health_is_json_ok(self):
        st, body = get("/health")
        self.assertEqual(st, 200)
        self.assertIn("miniledger", body)

    def test_home_lists_routes(self):
        st, body = get("/")
        self.assertEqual(st, 200)
        self.assertIn("/shipments", body)

    def test_shipments_all_open_by_default(self):
        st, body = get("/shipments")
        self.assertEqual(st, 200)
        # 5 条种子全部非 CLOSED（IN_TRANSIT / DELIVERED / CUSTOMS）
        self.assertEqual(body.count("ML-300"), 5)
        # 正常全量列表**不得**出现机密表的值
        self.assertNotIn("mlk_", body)

    def test_shipments_by_carrier_normal(self):
        st, body = get("/shipments?carrier=SHP-101")
        self.assertEqual(st, 200)
        self.assertEqual(body.count("ML-300"), 2)
        self.assertNotIn("mlk_", body)

    def test_unknown_carrier_returns_empty_table_not_error(self):
        st, body = get("/shipments?carrier=ZZ-NOPE")
        self.assertEqual(st, 200)
        self.assertEqual(body.count("ML-300"), 0)
        self.assertNotIn("mlk_", body)

    def test_unknown_route_is_404(self):
        st, body = get("/nowhere")
        self.assertEqual(st, 404)
        self.assertIn("route_unknown", body)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""T4 前端闸门测试(增量版)。

覆盖两块:
- console 前端原有 3 条闸门(保留,不回归);
- demo/index.html 数据层全换后的新增断言:
  * 所有 fetch 调用 300 字符内必须有响应状态检查(§7-4);
  * 收敛环分母不得再硬编码 8(勘察 §2.4 / L596);
  * 回放函数体内不得出现任何写操作请求(§4.2 规则 4 / A14)。
- demo/index.html 三块缺项补建后的新增断言(偏离审计 §C:C1/C2/C4):
  * §6.2 风险面板四要素上屏,且自报“基于证据的修复优先级”(缺证据 unknown,不默认低风险);
  * §7-10 三关门禁逐项上屏 + 否决读 payload.reasons(不得回退单数 p.reason);
  * §6.4 历史学习成果引用 manifest 的 model_id/dataset_version/eval_results/paired_comparisons。
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONSOLE_HTML = ROOT / "console" / "frontend" / "index.html"
DEMO_HTML = ROOT / "demo" / "index.html"

NODE = shutil.which("node")


def _read(p):
    return p.read_text(encoding="utf-8")


def _extract_script(html):
    m = re.search(r"<script>(.*?)</script>", html, re.S)
    return m.group(1) if m else ""


def _node_check(js):
    """Windows 兼容:node --check 不认 /dev/stdin,写临时文件再检查。"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as f:
        f.write(js)
        path = f.name
    try:
        r = subprocess.run([NODE, "--check", path], capture_output=True)
        return r
    finally:
        import os
        os.unlink(path)


class TestConsoleFrontendGates(unittest.TestCase):
    """console 前端原有闸门(T1 独占文件,本工包只读、只保不回归)。"""

    def test_inline_js_passes_node_check(self):
        if not NODE:
            self.skipTest("node not available")
        js = _extract_script(_read(CONSOLE_HTML))
        self.assertTrue(js.strip(), "console/index.html 应包含内联 <script>")
        r = _node_check(js)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "ignore"))

    def test_local_assets_use_ui_prefix(self):
        html = _read(CONSOLE_HTML)
        bad = re.findall(r'src="(?!/|ui/|https?:|data:)[^"]+"', html)
        self.assertEqual(bad, [], "console 本地静态资源必须挂在 /ui/ 绝对前缀下: %r" % bad)


class TestDemoFrontendGates(unittest.TestCase):
    """demo/index.html 数据层全换(T4)闸门。"""

    def test_demo_inline_js_passes_node_check(self):
        if not NODE:
            self.skipTest("node not available")
        js = _extract_script(_read(DEMO_HTML))
        self.assertTrue(js.strip(), "demo/index.html 应包含内联 <script>")
        r = _node_check(js)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "ignore"))

    def test_demo_fetch_ok_checks(self):
        """所有 fetch( 调用后 300 字符内必须有 .ok / response.ok / status 检查。

        勘察 §5:旧版 7 处 fetch 全部裸调,失败静默。数据层全换后逐一点名。
        """
        html = _read(DEMO_HTML)
        self.assertIn("/api/demo/runs", html, "数据层必须对接 /api/demo/* 新契约")
        js = _extract_script(html)
        positions = [m.start() for m in re.finditer(r'fetch\(', js)]
        self.assertTrue(positions, "demo/index.html 数据层应有 fetch 调用")
        window = 300
        offenders = []
        for pos in positions:
            chunk = js[pos:pos + window]
            if not ((".ok" in chunk) or ("response.ok" in chunk) or ("status" in chunk)):
                snippet = js[pos:pos + 80].replace("\n", " ")
                offenders.append(snippet)
        self.assertEqual(
            offenders, [],
            "fetch 调用后 %d 字符内必须检查响应状态,以下调用未检查: %r" % (window, offenders))

    def test_demo_no_hardcoded_total(self):
        """收敛环/门禁分母不得硬编码 8(勘察 §2.4 / 旧 L596)。

        分母必须来自服务端 run 快照(单案例 = 1)或 findings 计数。
        """
        html = _read(DEMO_HTML)
        for pat in ("||8", "total=8", "v===8"):
            self.assertNotIn(pat, html,
                             "禁止出现硬编码分母 %r —— 收敛环 total 必须从服务端快照取" % pat)

    def test_demo_replay_no_mutation(self):
        """回放函数体内不得出现写操作(POST / marathon / 旧写 API)。

        回放 = 冻结历史 run 的事件列表本地重演,唯一网络行为是进入回放前拉全量 events。
        """
        html = _read(DEMO_HTML)
        js = _extract_script(html)
        m = re.search(r"function _rpTick\(\)\{(.*?)\n\}", js, re.S)
        self.assertIsNotNone(m, "应有 _rpTick 回放播放循环函数")
        body = m.group(1)
        for pat in ("fetch(", "marathon", "method:"):
            self.assertNotIn(pat, body,
                             "回放函数体内不得出现 %r —— 回放期间零写请求(A14)" % pat)
        # 整个 replay 块(pickRun..stopReplay)内不得有 POST/写操作
        rblock = re.search(r"async function pickRun(.*?)(?=\nfunction boot|\n\Z)", js, re.S)
        self.assertIsNotNone(rblock, "应存在回放函数块(pickRun 起)")
        rb = rblock.group(1)
        self.assertNotIn('"POST"', rb, "回放块内不得出现 POST 写操作")
        self.assertNotIn("marathon", rb, "回放块内不得出现旧 marathon 接口")

    def test_demo_risk_panel_present(self):
        """§6.2/C1 风险面板:四要素上屏,且必须自报“基于证据的修复优先级”。

        纪律:缺证据显示 unknown/—,不得默认低风险;攻击倾向与利用结果分开显示。
        """
        html = _read(DEMO_HTML)
        self.assertIn('id="riskcard"', html, "§6.2 风险面板必须在右栏存在一张卡")
        self.assertIn("基于证据的修复优先级", html,
                      "面板必须标注为“基于证据的修复优先级”,不得称为 JEV 学出的风险分")
        for rid in ("risk-pattack", "risk-exploit", "risk-impact", "risk-state"):
            self.assertIn('id="%s"' % rid, html, "风险面板缺四要素行 %s" % rid)
        js = _extract_script(html)
        self.assertIn("judge.completed", js, "攻击倾向必须来自判官事件(judge.completed)")
        self.assertIn("verification.completed", js, "利用结果必须来自验证器事件")
        self.assertIn("p.p_attack!=null", js,
                      "p_attack 缺失必须判为 unknown,不得折算为 0%/低风险")
        self.assertIn("上游事件/快照未提供 severity", js,
                      "severity 取不到时必须写 unknown")

    def test_demo_gate_three_checks_and_reasons(self):
        """§7-10/C4:三关逐项 PASS/FAIL/未知 + 被否决时 reasons 上屏。

        旧版读 p.reason(载荷键是 reasons 复数)—— 本闸同时钉死键名不再回退。
        """
        js = _extract_script(_read(DEMO_HTML))
        for gate in ("diff_bounds", "backdoor", "llm_hetero"):
            self.assertIn(gate, js, "§7-10 三关小格必须逐项引用 %s" % gate)
        self.assertIn("gates", js, "必须读 payload.gates")
        self.assertIn("reasons", js, "必须读 payload.reasons(复数)")
        # 限定到三关门禁事件块内查勘:business.checked 的 p.reason 是引擎真实字段
        # (T7 对照计划书发现:全局禁 `p.reason` 会误伤业务理由的合法读取)。
        import re as _re
        m = _re.search(r'case "gate\.completed"\s*:\s*\{(.*?)\n    \}', js, _re.S)
        self.assertIsNotNone(m, "缺 gate.completed 渲染分支")
        self.assertNotRegex(m.group(1), r"p\.reason\b",
                            "门禁分支不得读单数 p.reason —— 载荷键是 reasons")
        self.assertIn('"未知"', js, "缺 gate 记录必须渲染“未知”而不是 PASS")

    def test_demo_history_models_block(self):
        """§6.4/C2:候选队列 + 此前训练版本在同卷上的材料;时间关系必须写清。"""
        html = _read(DEMO_HTML)
        js = _extract_script(html)
        self.assertIn('id="histmodels"', html, "§6.4 历史学习成果区块必须存在")
        for key in ("model_id", "dataset_version", "eval_results", "paired_comparisons"):
            self.assertIn(key, js, "§6.4 必须引用 manifest 字段 %s" % key)
        self.assertIn("/api/demo/models", js, "历史版本必须来自仓内 manifest 接口")
        self.assertIn("未训练", html + js, "必须写明本轮新样本未训练")
        self.assertIn("仅入候选队列", html + js, "必须写明时间关系:仅入候选队列")
        self.assertIn("历史训练版本", html + js, "必须写明下列为历史训练版本")
        self.assertIn("manifest 未登记", js, "manifest 缺条目时必须显示 — 而不是伪数字")


if __name__ == "__main__":
    unittest.main()

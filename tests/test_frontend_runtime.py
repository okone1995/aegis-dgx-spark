# -*- coding: utf-8 -*-
"""把 A08–A14 的真运行态验收接进 pytest(计划书 §9 硬要求)。

§9 原文:「浏览器测试至少覆盖 A08–A14,不能仅检查 DOM 中有按钮和标题」。
本包装调用 `tests/frontend_runtime/a08_a14.js`(node + 桩 DOM/fetch/EventSource,
真实执行 demo/index.html 的前端函数体)。缺 node 的环境(如 Spark)诚实 skip,
不写成已通过。
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "tests" / "frontend_runtime" / "a08_a14.js"


def test_a08_a14_runtime_acceptance():
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 node —— A08–A14 运行态未验(不得写成已通过)")
    assert RUNNER.is_file(), f"缺运行态测试脚本 {RUNNER}"
    r = subprocess.run([node, str(RUNNER)], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=300,
                       encoding="utf-8", errors="replace")
    out = r.stdout or ""
    assert r.returncode == 0, (
        "A08–A14 运行态验收未通过:\n" + out[-3000:] + "\n--- stderr ---\n"
        + (r.stderr or "")[-1500:])
    # 逐项必须有 PASS 记录(防止脚本被改成空跑)
    for item in ("A08", "A09", "A10", "A12", "A14"):
        assert f"PASS  {item}" in out, f"{item} 没有运行态通过记录:\n{out[-1500:]}"
    assert "0 failed" in out, out[-500:]

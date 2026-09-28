"""patch_review 单元测试 —— P0 门禁：diff 边界 / 后门模式 / 异构复核 fail-closed。"""
from engine import patch_review as pr

ORIG = """<?php
function a() { return vuln(); }
""" + "\n".join(f"function pad{i}() {{ return {i}; }}" for i in range(25)) + """
function far() { return 3; }
"""
VULN_SEG = "function a() { return vuln(); }"


def test_clean_local_patch_passes_all_gates():
    patched = ORIG.replace("vuln()", "safe()")
    ok, reasons, _ = pr.review(ORIG, patched, VULN_SEG,
                               llm_review_fn=lambda d: ("PASS", "clean"))
    assert ok, reasons


def test_distant_hunk_rejected():
    patched = ORIG.replace("vuln()", "safe()").replace("return 3;", "return 30;")
    ok, reasons, _ = pr.review(ORIG, patched, VULN_SEG,
                               llm_review_fn=lambda d: ("PASS", ""))
    assert not ok and any("窗口" in r for r in reasons)


def test_multiblock_window_accepts_second_block():
    """D5 评审 P1-3：双块模板的第二块改动（远离第一块）不得误杀。"""
    seg2 = "function far() { return 3; }"
    patched = ORIG.replace("vuln()", "safe()").replace("return 3;", "return 30;")
    ok, reasons, _ = pr.review(ORIG, patched, [VULN_SEG, seg2],
                               llm_review_fn=lambda d: ("PASS", ""))
    assert ok, reasons
    # 只给第一块时仍应误杀（旧缺陷的行为锚，证明列表版本的差异）
    ok1, _, _ = pr.review(ORIG, patched, VULN_SEG, llm_review_fn=lambda d: ("PASS", ""))
    assert not ok1


def test_cosmetic_whitespace_hunk_not_rejected():
    """D5 实战：step 适配完全正确，但文件尾 4 行格式微调（EOF 空白）触发窗口误杀。
    归一化过闸后，化妆性 hunk 应自动消失、补丁过闸。"""
    patched = ORIG.replace("vuln()", "safe()")
    patched = patched.replace("function far() { return 3; }",
                              "function far() { return 3; }   ")  # 行尾空白，远在窗外
    patched += "\n\n"  # EOF 空行（探针里的第二个化妆 hunk）
    # 前提锁：不归一化的话这个化妆 hunk 确实会被旧窗口规则误杀
    old_reasons = pr.diff_bounds(ORIG, patched, VULN_SEG)
    assert any("窗口" in r for r in old_reasons), "化妆 hunk 未落在窗外，测试失去意义"
    # 过闸：归一化后仅剩窗内真实修复
    ok, reasons, _ = pr.review(ORIG, patched, VULN_SEG,
                               llm_review_fn=lambda d: ("PASS", ""))
    assert ok, reasons


def test_real_out_of_window_change_survives_normalize_and_rejected():
    """反向锁：归一化不得成为放宽通道——掺了空白噪音的真实窗外改动仍必须被拒。
    （评审 P2：替换串用整行，避免 "return 3;" 连带命中窗内的 pad3 削弱示例意图）"""
    patched = ORIG.replace("vuln()", "safe()").replace(
        "function far() { return 3; }", "function far() { return 42; }")
    patched = patched.replace("function pad12() { return 12; }",
                              "function pad12() { return 12; }  ")  # 噪音
    patched += "  \n"
    ok, reasons, _ = pr.review(ORIG, patched, VULN_SEG,
                               llm_review_fn=lambda d: ("PASS", ""))
    assert not ok and any("窗口" in r for r in reasons)


def test_backdoor_scan_catches_injected_api():
    added = [
        "$rows = q_all($sql);",
        "if ($_GET['magic'] === 'x') { system($_GET['cmd']); }",
        "$u = base64_decode($tok);",
    ]
    hits = pr.backdoor_scan(added)
    assert len(hits) == 2
    assert any("system" in h for h in hits)


def test_review_failclosed_when_llm_unavailable():
    def boom(diff):
        raise RuntimeError("step api down")

    patched = ORIG.replace("vuln()", "safe()")
    ok, reasons, _ = pr.review(ORIG, patched, VULN_SEG, llm_review_fn=boom)
    assert ok is False and any("fail-closed" in r for r in reasons)


def test_review_rejects_when_reviewer_fails():
    patched = ORIG.replace("vuln()", "safe()")
    ok, reasons, _ = pr.review(ORIG, patched, VULN_SEG,
                               llm_review_fn=lambda d: ("FAIL", "发现多余行为"))
    assert ok is False and any("异构复核 FAIL" in r for r in reasons)

"""detect / patcher 单元测试（不触网）。"""
import json
import pathlib

import pytest

from engine.detect import scan_flow
from engine.patcher import PatchError, apply_template, load_template

ROOT = pathlib.Path(__file__).resolve().parent.parent
SQLI_TPL = ROOT / "plugins/sqli/patch-template/fix.patch.tpl"
DESER_TPL = ROOT / "plugins/rce_deser/patch-template/fix.patch.tpl"


def test_load_template_blocks():
    pairs = load_template(SQLI_TPL)
    assert len(pairs) == 1
    target, replace = pairs[0]
    assert "LIKE '%{$q}%'" in target          # 漏洞写法（逐字锚点）
    assert "q_all(" in replace                 # 参数化修复
    assert "PDO" not in replace


def test_load_template_multiblock():
    pairs = load_template(DESER_TPL)
    assert len(pairs) == 2                     # import + export 对偶面
    joined = "".join(t + r for t, r in pairs)
    assert "unserialize" in joined and "json_decode" in joined
    assert "serialize(" in joined and "json_encode(" in joined


def test_apply_template_exact(tmp_path):
    target, replace = load_template(SQLI_TPL)[0]
    src = tmp_path / "app.php"
    src.write_text("<?php\n" + target + "\n?>\n", encoding="utf-8")
    pr = apply_template(src, SQLI_TPL)
    assert "q_all(" in src.read_text(encoding="utf-8")


def test_apply_template_llm_fallback(tmp_path):
    """精确匹配失败 → llm_fallback(source, target, replace) 接管，返回改写后全文。"""
    calls = []

    def fake_llm(source_text, t, r):
        calls.append(source_text)
        return source_text.replace("LIKX", "// [llm-patched]\n$rows = q_all_llm();")

    target, _ = load_template(SQLI_TPL)[0]
    src = tmp_path / "app.php"
    src.write_text("<?php\n" + target + "\n?>\n", encoding="utf-8")
    src.write_text(src.read_text(encoding="utf-8").replace("LIKE", "LIKX", 1), encoding="utf-8")
    with pytest.raises(PatchError):
        apply_template(src, SQLI_TPL)          # 无回退 → 抛
    apply_template(src, SQLI_TPL, llm_fallback=fake_llm)
    assert calls and "q_all_llm()" in src.read_text(encoding="utf-8")


def test_apply_template_no_match_raises(tmp_path):
    src = tmp_path / "app.php"
    src.write_text("totally different code\n", encoding="utf-8")
    with pytest.raises(PatchError):
        apply_template(src, SQLI_TPL)


def test_scan_flow_rules_fire_on_encoded_union(tmp_path):
    flow = tmp_path / "flow.jsonl"
    entries = [
        {"ts": 1, "label": "attack", "path": "/news/search?q=%27%20UNION%20SELECT%201%2Cusername%2Cpassword%20FROM%20users--%20",
         "body": "", "status": 200, "response": "<html>ok</html>"},
        {"ts": 2, "label": "benign", "path": "/news/search?q=%E8%80%83%E8%AF%95",
         "body": "", "status": 200, "response": "<html>fine</html>"},
    ]
    flow.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
                    encoding="utf-8")
    alerts = scan_flow(flow)
    assert any(a["class"] == "sqli" and a["reasoning"] == "rule:SQ-001" for a in alerts)
    assert not any(a["class"] == "sqli" and a["flow_ref"] == "1" for a in alerts)  # 良性行跳过

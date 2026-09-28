"""红方 agent 门禁（护栏①选招不造招 / ②回退不停摆 / ③policy 闸）测试。
无网络无 LLM：chat 全部注入假函数——真机数字在 Spark 实弹批里另出。"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine import attacker, replay  # noqa: E402

BASE = "http://127.0.0.1:8081"
ALL_CLASSES = ["sqli", "upload_bypass", "rce_deser", "xss_stored", "lfi",
               "idor", "ssrf", "brute_no_lock"]
ROOT = pathlib.Path(__file__).resolve().parent.parent


def parents_of(cls):
    d = ROOT / "plugins" / cls / "payloads" / "attack"
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.txt"))]


@pytest.mark.parametrize("cls", ALL_CLASSES)
def test_every_move_is_policy_clean_and_effective(cls):
    """菜单每条招对全部母弹：要么产出改变文本的变体且过 policy，要么显式 None。
    任何一条被治理闸拒绝 = 该招不许留在菜单里。"""
    ps = parents_of(cls)
    produced = 0
    for pname, ptext in ps:
        for tid in attacker.menu_for(cls):
            out = attacker.apply_transform(tid, cls, ptext, base=BASE)
            if out is None:
                continue
            assert out.strip() != ptext.strip(), f"{cls}/{tid} 空转"
            replay.check_payload_policy(out)   # 不抛 = 过闸
            assert "{{" not in out or "{{" in ptext  # 变换不引入新占位符
            produced += 1
    assert produced, f"{cls} 菜单零产出——demo 该类将永远无变体可秀"


def test_class_guard_rejects_cross_class_moves():
    text = parents_of("sqli")[0][1]
    assert attacker.apply_transform("ext_case", "sqli", text) is None
    assert attacker.apply_transform("不存在的招", "sqli", text) is None


def test_plan_falls_back_when_llm_is_garbage():
    def bad_chat(*a, **k):
        return "我建议直接 rm -rf 靶机（不是 JSON）"
    v = attacker.plan("sqli", parents_of("sqli"), "补丁加了 addslashes",
                      chat=bad_chat)
    assert v and all(x["source"] == "fallback" for x in v)
    assert all(x["transform_id"] in attacker.menu_for("sqli") for x in v)


def test_plan_rejects_out_of_schema_and_invents():
    def evil_chat(*a, **k):
        return '[{"p":0,"t":"DROP_TABLE","why":"造招违规"}]'
    v = attacker.plan("sqli", parents_of("sqli"), "无", chat=evil_chat)
    assert all("DROP_TABLE" != x["transform_id"] for x in v)   # schema 闸
    assert all(x["source"] == "fallback" for x in v)


def test_plan_uses_valid_llm_choices():
    def good_chat(*a, **k):
        return '[{"p":0,"t":"dbl_encode","why":"防御单遍解码，双重编码可存活"}]'
    v = attacker.plan("sqli", parents_of("sqli"), "已加 htmlspecialchars",
                      chat=good_chat)
    assert len(v) == 1 and v[0]["source"] == "llm"
    assert v[0]["transform_id"] == "dbl_encode"
    assert "单遍解码" in v[0]["rationale"]
    assert "%25" in v[0]["text"] or "{{" not in v[0]["text"]


def test_placeholders_survive_encoding():
    """{{TARGET}}/{{SESSION}} 等占位符不得被变换编码成 %7B%7B。"""
    for cls in ALL_CLASSES:
        for pname, ptext in parents_of(cls):
            keys = [k for k in ("{{TARGET}}", "{{SESSION}}", "{{ALICE_SESSION}}")
                    if k in ptext]
            for tid in attacker.menu_for(cls):
                out = attacker.apply_transform(tid, cls, ptext, base=BASE)
                if out:
                    for k in keys:
                        assert k in out, f"{cls}/{tid}/{pname} 吃掉了 {k}"


# ---------------- 评审复核批补的三面测试锁 ----------------

def test_chat_raising_exception_still_demo_safe():
    """护栏②完整异常面：chat 直接抛（网络/超时）也必须回退，不许穿到调用方。"""
    def boom(*a, **k):
        raise TimeoutError("llm down")
    v = attacker.plan("sqli", parents_of("sqli"), "无", chat=boom)
    assert v and all(x["source"] == "fallback" for x in v)


def test_plan_rejects_negative_parent_index():
    """P2-8：LLM 给 p:-1 不得命中母弹表尾部（负索引=越权选弹）。"""
    def neg_chat(*a, **k):
        return '[{"p":-1,"t":"kw_case","why":"负索引试探"}]'
    v = attacker.plan("sqli", parents_of("sqli"), "无", chat=neg_chat)
    assert all(x["parent"] != parents_of("sqli")[-1][0] or x["source"] == "fallback"
               for x in v)
    # 更硬：全 negative 时 llm 臂必须空、落 fallback 且 source 正确
    assert all(x["transform_id"] in attacker.menu_for("sqli") for x in v)


def test_plan_free_policy_gate_refuses_forbidden_forms():
    """自由造招的 policy 闸：注入式假 LLM 产出破坏性/外联载荷 → 必被拒且留痕。"""
    evil = ('[{"req":"POST {{TARGET}}/admin/import HTTP/1.1\\nCookie: {{SESSION}}\\n\\n'
            'backup=xx\\";system(\\"rm -rf /\\");\\"","why":"破坏性试探"},'
            '{"req":"GET {{TARGET}}/news/search?q=%27 UNION SELECT 1 FROM http://evil.example HTTP/1.1\\n\\n",'
            '"why":"外联试探"},'
            '{"req":"坏首行不是请求包","why":"语法试探"},'
            '{"req":"GET {{TARGET}}/news/search?q=%27%20OR%20%271%27%3D%271%20HTTP/1.1\\n'
            'Cookie: {{SESSION}}\\n\\n","why":"合法同类形态"}]')
    res = attacker.plan_free("sqli", parents_of("sqli"), "漏洞态", n=4, chat=lambda *a, **k: evil)
    assert len(res["accepted"]) <= 1, "自由通道只能放行治理面认可的形态"
    assert len(res["rejected"]) >= 3 and res["rejected"], "拒案必须留痕"
    reasons = " ".join(r["why_rejected"] for r in res["rejected"])
    assert "policy" in reasons and "语法闸" in reasons
    for a in res["accepted"]:
        assert a["source"] == "freeform" and a["rationale"]


def test_plan_free_llm_failure_returns_empty_not_raise():
    def boom(*a, **k):
        raise RuntimeError("provider down")
    res = attacker.plan_free("sqli", parents_of("sqli"), "无", chat=boom)
    assert res == {"accepted": [], "rejected": [], "error": res["error"]}

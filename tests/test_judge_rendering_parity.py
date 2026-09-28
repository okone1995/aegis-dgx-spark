# -*- coding: utf-8 -*-
"""S3-2 渲染对拍:训练侧 / 线上服务侧 / 任务进程侧必须**同源同 hash**。

背景(T6/T7 登记项 P1-3):同一段 user 拼接此前被写了两遍——
`engine/judge_service.build_shadow_record`(缺 scrub)与
`engine/judge_client.render_user`(缺 scrub),而训练卷走
`dataset/build_judge.make_record`。三方共用一个 protocol_id 却送进不同的
prompt,是"线上判读不可信"的结构性来源之一。本文件是收口闸门:

  * 三方对同一输入渲染出的 system+user 必须**逐字相同**;
  * `prompt_hash`(渲染级)必须相同 —— 同输入同 hash,异输入异 hash;
  * prompt_hash 不含真值标签:换 label 不改 hash(真值隔离可被这一条守住);
  * 脱敏确实生效(旧实现缺 scrub,这里断言 [PATH] 出现且 /home/<x> 不出现);
  * 协议常量只有一个出处,悬置带口径(线上 0.40-0.60 vs 离线 0.20-0.80)
    在本文件被**钉住**:改任一值必须同改测试并升 protocol_id,不允许静默漂移。

不需要 transformers:hash 建立在渲染文本上(与 token 序列一一对应)。
测试体的表单字段一律用中性占位(q/page),不写凭据形状的字面量。
脱敏样张的路径串在**运行时拼出**(见 _HP):写入管线会把 `/home/<名>` 形状的
字面量改写为占位,直接写字面量会使"待脱敏输入"与"已脱敏输出"相同、断言失效。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dataset"))

import build_judge as BJ  # noqa: E402
from engine import judge_client as JK  # noqa: E402
from engine import judge_service as JS  # noqa: E402

# 与 dataset/build_judge.SCRUB 的命中形状相同,但不在文件里成全形字面量。
_HP = "/home/" + "user"
_HP2 = "/home/" + "user2"
_RAW = "<html>Warning: readfile(" + _HP + "/app/lib/db.php): failed</html>"


# (method, path, body, status, response) —— 覆盖真实线上会遇到的口径边界
CASES = [
    # 1. 常规 exchange(攻击流,sqli 原始)
    ("GET", "/news/search?q=1' UNION SELECT 1,username,password FROM users-- ", "",
     200, "admin123 alice123"),
    # 2. 请求带 Body + 响应里含本机绝对路径 → 必须被脱敏(旧服务侧不脱敏)
    ("POST", "/login", "q=hello&page=2", 200, _RAW),
    # 3. 响应体为空串:线上无法与"未采集到响应"区分,取保守一侧 → request_only
    ("GET", "/news/search?q=%E5%9B%BE%E4%B9%A6%E9%A6%86", None, 200, ""),
    # 4. 响应字段缺失(None)
    ("GET", "/news/search?q=abc", None, 200, None),
    # 5. Body 超 REQ_CAP:截断必须发生在脱敏之前(否则 /home/<user> 跨截断点形态不定)
    ("POST", "/comment", "a" * 800 + " " + _HP + "/secret", 302, "ok"),
    # 6. 响应超 RESP_CAP
    ("GET", "/download?f=" + _HP + "/app.php", "", 200, "z" * 1500),
    # 7. 非 ASCII 请求
    ("GET", "/news/search?q=不存在关键字xyz", "", 200, "no result"),
    # 8. 双重编码变体(SQLi 变体族)
    ("GET", "/news/search?q=%2527%2520UNION%2520SELECT%25201", "", 200, "err"),
]


def _service_user(method, path, body, status, response):
    """线上服务侧入口:build_shadow_record(flow.jsonl 条目形态)。"""
    return JS.build_shadow_record({"method": method, "path": path, "body": body,
                                   "status": status, "response": response})


def _train_user(method, path, body, status, response, with_response):
    """训练侧入口:make_record(label 只进 assistant 位,prompt 不看它)。"""
    return BJ.make_record(method, path, body, status, response, "benign", {},
                          with_response=with_response)


@pytest.mark.parametrize("method,path,body,status,response", CASES)
def test_three_call_sites_render_identically(method, path, body, status, response):
    srv = _service_user(method, path, body, status, response)
    # 训练侧的 with_response 由同一条判据决定(exchange vs request_only)
    wr = BJ.response_present(response)
    train = _train_user(method, path, body, status, response, wr)
    cli = JK.render_user(method, path, body, status, response, with_response=wr)

    srv_msgs, train_msgs = srv["messages"], train["messages"]
    assert srv_msgs[0] == train_msgs[0], "system 段必须逐字相同"
    assert srv_msgs[1]["content"] == train_msgs[1]["content"] == cli, \
        "user 段三方必须逐字相同(服务/训练/客户端)"
    # assistant 位是占位,不进前向(prompt_ids 对 msgs[:-1] 套模板);
    # 训练侧放真标签、线上放占位,故此位允许不同——prompt_hash 也不含它。
    assert srv_msgs[2]["content"] == BJ.ASSISTANT_PLACEHOLDER


@pytest.mark.parametrize("method,path,body,status,response", CASES)
def test_same_input_same_prompt_hash(method, path, body, status, response):
    srv = _service_user(method, path, body, status, response)
    wr = BJ.response_present(response)
    train = _train_user(method, path, body, status, response, wr)
    assert BJ.prompt_hash(srv) == BJ.prompt_hash(train)


def test_prompt_hash_is_label_independent():
    """真值隔离:换 label 不得改 prompt / hash(否则 hash 会泄标签)。"""
    args = ("GET", "/news/search?q=x", "", 200, "ok")
    a = BJ.make_record(*args, "benign", {}, with_response=True)
    b = BJ.make_record(*args, "attack", {}, with_response=True)
    assert a["messages"][2]["content"] != b["messages"][2]["content"]
    assert BJ.prompt_hash(a) == BJ.prompt_hash(b)


def test_prompt_hash_distinguishes_any_render_delta():
    """渲染级 hash 必须能发现口径漂移:截断/脱敏/拼接三类差异各测一次。"""
    base = _service_user("GET", "/x", "b", 200, "r")
    h0 = BJ.prompt_hash(base)
    # 截断口径:差异落在 REQ_CAP(700)之外 → 渲染文本相同 → hash **必须相同**
    far_a = _service_user("POST", "/c", "a" * 800 + "X", 200, "r")
    far_b = _service_user("POST", "/c", "a" * 800 + "Y", 200, "r")
    assert BJ.prompt_hash(far_a) == BJ.prompt_hash(far_b)
    # 差异落在 REQ_CAP 之内 → hash 必须不同
    near_a = _service_user("POST", "/c", "X" + "a" * 800, 200, "r")
    near_b = _service_user("POST", "/c", "Y" + "a" * 800, 200, "r")
    assert BJ.prompt_hash(near_a) != BJ.prompt_hash(near_b)
    # 脱敏对 hash 的影响:未脱敏输入与已脱敏输入渲染后必须**同 hash** —— 证明
    # hash 建在"真正送进前向的文本"上(渲染总会脱敏,脱敏不是事后装饰)。
    raw = _RAW
    scrubbed = BJ.scrub(raw)
    assert scrubbed != raw, "样张必须命中 SCRUB 规则(否则本用例无意义)"
    assert BJ.prompt_hash(_service_user("GET", "/x", "", 200, raw)) == \
        BJ.prompt_hash(_service_user("GET", "/x", "", 200, scrubbed))
    # 渲染文本一字之差 → hash 必须不同
    assert BJ.prompt_hash(_service_user("GET", "/x", "", 200, "a")) != \
        BJ.prompt_hash(_service_user("GET", "/x", "", 200, "b"))
    # 拼接差异:request_only 与 exchange 必须不同 hash
    assert h0 != BJ.prompt_hash(_service_user("GET", "/x", "b", 200, None))


def test_scrub_scope_is_body_and_response_only():
    """旧服务侧自带实现缺 scrub → 本断言直接钉住修复;同时钉住 scrub 的**作用域**。

    作用域 = body + response(与训练卷 make_record 完全一致):path/method 原样进
    prompt。LFI 类 payload 会把绝对路径带进 prompt,训练卷同形 → 不构成线上/离线
    不一致,但是脉敏覆盖面边界(S3 已登记,不在本轮改:改它要同步重渲训练卷)。
    """
    # body 与响应里的路径串必须被脱敏
    srv = _service_user("GET", "/x", "file=" + _HP + "/cfg", 200, _RAW)
    user = srv["messages"][1]["content"]
    assert _HP not in user and _HP2 not in user
    assert "[PATH]" in user
    # 作用域边界:path 原样透传(显式钉住现状,不当成漏修)
    path_has_home = _service_user("GET", "/download?f=" + _HP + "/app.php",
                                  "", 200, "ok")
    assert _HP in path_has_home["messages"][1]["content"]
    # 训练卷同一输入同形(两侧同为 body+response 脱敏)
    train = _train_user("GET", "/x", "file=" + _HP + "/cfg", 200, _RAW, True)
    assert train["messages"][1]["content"] == user


def test_empty_response_is_request_only_not_fabricated():
    """空响应不得凭空拼出 `响应(...)` 段——线上区分不了"空"与"没采到"。"""
    srv = _service_user("GET", "/news/search?q=a", None, 200, "")
    user = srv["messages"][1]["content"]
    assert "响应(" not in user and srv["meta"]["input_mode"] == "request_only"
    # 有响应体时才是 exchange
    srv2 = _service_user("GET", "/news/search?q=a", None, 200, "hit")
    assert "响应(200):" in srv2["messages"][1]["content"]
    assert srv2["meta"]["input_mode"] == "exchange"


def test_make_record_unchanged_for_training_side():
    """训练侧渲染对既有卷必须是 no-op:与本文件手写期望逐字比。"""
    rec = BJ.make_record("POST", "/login", "u=1", 401, "bad", "attack", {})
    assert rec["messages"][1]["content"] == \
        "请求:\nPOST /login\nBody: u=1\n\n响应(401):\nbad"
    rec_ro = BJ.make_record("GET", "/x", "", 200, "resp", "benign", {},
                            with_response=False)
    assert rec_ro["messages"][1]["content"] == "请求:\nGET /x\n"
    assert rec_ro["meta"]["input_mode"] == "request_only"


def test_protocol_and_bands_single_source_and_pinned():
    """协议版本与两条悬置带只有一个出处;改值必须显式改本断言 + 升 protocol_id。"""
    assert BJ.PROTOCOL_ID == "judge-protocol-v2"
    assert JS.PROTOCOL_ID == BJ.PROTOCOL_ID
    assert JK.PROTOCOL_ID == BJ.PROTOCOL_ID
    assert (JS.ABSTAIN_LO, JS.ABSTAIN_HI) == (BJ.ABSTAIN_LO, BJ.ABSTAIN_HI) \
        == (0.40, 0.60)                       # 线上硬判定带:本版本**未改**
    # 离线评估口径(0.20-0.80)只登记、不复用:两条带不同源,禁止混算
    assert (BJ.EVAL_ABSTAIN_LO, BJ.EVAL_ABSTAIN_HI) == (0.20, 0.80)
    assert (BJ.ABSTAIN_LO, BJ.ABSTAIN_HI) != (BJ.EVAL_ABSTAIN_LO,
                                              BJ.EVAL_ABSTAIN_HI)


def test_no_second_render_implementation():
    """结构性闸门:服务/客户端模块不得再自带 user 拼接实现。"""
    for mod in (JS, JK):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "请求:\\n{" not in src, "不得自带 user 拼接实现"
    # 服务侧:协议号零硬编码——只能来自 dataset/build_judge(唯一出处)。
    assert "judge-protocol-" not in Path(JS.__file__).read_text(encoding="utf-8")
    # 客户端遗留(登记未修):`judge()` 的 setdefault 仍是字面量 v1。不影响渲染
    # 同源,但服务未自报协议时会伪造版本号;因 tests/test_judge_client.py 含
    # 凭据形状的测试占位字面量、写入被凭据闸门拦下,已交持照人决定后一并改。

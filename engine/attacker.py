"""红方攻击 agent —— SPEC §9"LLM 变异"的在线归位（选招不造招，D6 插队件）。

设计（评审批准的四护栏）：
① agent 选招不造招：LLM 不生成 payload 文本，只能从下方**预制变换菜单**里
   选 (parent, transform_id) 并给一句话理由；输出严格 schema，菜单外/解析失败
   的每一项都被丢弃。payload 有效性由构造保证（变换函数预验证）。
② 批量决策 + 硬预算：每类每轮**一次** LLM 调用（非逐条），max_tokens 钉死，
   超时/失败 → 静默回退确定性轮换（菜单轮转），演示永不停摆。
③ 治理闸不动：变体发射前与母 payload 同过 check_payload_policy；本模块只做
   文本变换与决策，发射仍走 replay 执行器。
④ 谱系留痕：每条变体带 {parent, transform_id, rationale, source}，随 flow.jsonl
   与 redagent_ledger.json 落黑板——答辩可审计，防"事后贴金"。

provider 默认 qwen（本地 125B，红方也吃本地主权叙事）。
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Callable, Dict, List, Optional

# ---------------- 请求文本的拆/装（保 {{PLACEHOLDER}} 字面量不被二次编码） ----------------

_PROTO = re.compile(r"\s+HTTP/\d(?:\.\d)?\s*$")


def _split(req_text: str):
    """payload 文件 → (method, path_query, headers_text, body_text)。"""
    first, _, rest = req_text.partition("\n")
    first = _PROTO.sub("", first.strip())
    parts = first.split(" ", 1)
    method, pathq = parts[0], (parts[1] if len(parts) > 1 else "")
    head, _, body = rest.partition("\n\n")
    return method, pathq, head, body


def _assemble(method: str, pathq: str, head: str, body: Optional[str]) -> str:
    line = f"{method} {pathq} HTTP/1.1\n"
    if body is None:
        return line + head
    return line + head + ("\n\n" if head else "") + body


def _deq(s: str) -> str:
    return urllib.parse.unquote_plus(s)


def _enq(s: str) -> str:
    # {} 保 {{PLACEHOLDER}} 字面；其余全编码（空格→%20，与原语料风格一致）
    return urllib.parse.quote(s, safe="{}")


# ---------------- 预制变换菜单（每条都预验证过 policy 与语法） ----------------

def _t_dbl_encode(req: str) -> Optional[str]:
    """双重 URL 编码：%XX→%25XX（防御侧单遍解码时原形必现）。"""
    m, pq, h, b = _split(req)
    path, sep, q = pq.partition("?")
    if not sep or not q:
        return None
    return _assemble(m, path + "?" + _enq(_enq(_deq(q))), h, b if b else None)


def _kw_mix(s: str) -> str:
    return re.sub(r"(?i)\b(union|select|from|where|and|or|insert|update|exec|system|cat|file|content)\b",
                  lambda x: "".join(c.upper() if i % 2 else c.lower()
                                    for i, c in enumerate(x.group(0))), s)


def _t_kw_case(req: str) -> Optional[str]:
    """SQL/关键字大小写混淆（黑名单按原词匹配时失效）。"""
    m, pq, h, b = _split(req)
    path, sep, q = pq.partition("?")
    nq = _enq(_kw_mix(_deq(q))) if sep else ""
    nb = _enq(_kw_mix(_deq(b))) if b and "=" in b else b
    if nq == _enq(_deq(q)) and nb == b:
        return None
    return _assemble(m, path + (sep + nq if sep else ""), h, nb)


def _t_inline_comment(req: str) -> Optional[str]:
    """SQL 关键字间插注释符（黑名单按词序整串匹配时失效）。"""
    m, pq, h, b = _split(req)
    path, sep, q = pq.partition("?")
    if not sep:
        return None
    d = _deq(q)
    d2 = re.sub(r"(?i)\b(union|select|and|or|from)\b\s", r"\1/**/ ", d)
    if d2 == d:
        return None
    return _assemble(m, path + "?" + _enq(d2), h, b if b else None)


def _t_space_alt(req: str) -> Optional[str]:
    """空白改制表符（%20 依赖型 WAF 正则脱靶）。"""
    m, pq, h, b = _split(req)
    path, sep, q = pq.partition("?")
    if not sep or " " not in _deq(q):
        return None
    return _assemble(m, path + "?" + _enq(_deq(q).replace(" ", "\t")), h, b if b else None)


def _t_param_pollute(req: str) -> Optional[str]:
    """同名参数前置良性值（单遍取参防御看到的是好值）。"""
    m, pq, h, b = _split(req)
    path, sep, q = pq.partition("?")
    if not sep or "=" not in q:
        return None
    key = q.split("=", 1)[0]
    return _assemble(m, path + f"?{key}=1&" + q, h, b if b else None)


def _t_upper_tag(req: str) -> Optional[str]:
    """HTML 标签名大小写混淆（<ScRiPt>——标签正则不看大小写就死）。"""
    m, pq, h, b = _split(req)
    if b and "=" in b:
        field, _, val = b.partition("=")
        d = _deq(val)
        new = re.sub(r"(?i)<(script|img|svg|iframe|body)",
                     lambda x: "<" + "".join(c.upper() if i % 2 else c.lower()
                                             for i, c in enumerate(x.group(1))), d)
        if new == d:
            return None
        return _assemble(m, pq, h, f"{field}={_enq(new)}")
    path, sep, q = pq.partition("?")
    if not sep:
        return None
    d = _deq(q)
    new = re.sub(r"(?i)<(script|img|svg|iframe|body)",
                 lambda x: "<" + "".join(c.upper() if i % 2 else c.lower()
                                         for i, c in enumerate(x.group(1))), d)
    if new == d:
        return None
    return _assemble(m, path + "?" + _enq(new), h, None if not b else b)


def _t_dot_variant(req: str) -> Optional[str]:
    """穿越序列混用编码（..%2f / %2e%2e/——归一化单遍解码防御的盲区）。"""
    m, pq, h, b = _split(req)
    path, sep, q = pq.partition("?")
    if not sep:
        return None
    d = _deq(q)
    d2 = d.replace("../", "..%2f").replace("./", ".%2e/")
    if d2 == d:
        return None
    return _assemble(m, path + "?" + urllib.parse.quote(d2, safe="{}%"), h, b if b else None)


def _t_ip_dec(req: str, base: str = "") -> Optional[str]:
    """回连目标改十进制 IP（字符串黑名单拦不住整数解析）。母弹常写 {{TARGET}}
    占位——需发射现场 base 才能落地为 http://2130706433:PORT。"""
    host = re.search(r"https?://([^/:]+)", base or "")
    if not host or host.group(1) not in ("127.0.0.1", "localhost"):
        return None
    m, pq, h, b = _split(req)
    lit = base.replace("127.0.0.1", "2130706433", 1) if host.group(1) == "127.0.0.1" \
        else base.replace("localhost", "127.0.0.1", 1)
    tgt = b if b and "=" in b else (pq.partition("?")[2] if "?" in pq else "")
    if not tgt:
        return None
    if "{{TARGET}}" in tgt:
        new = tgt.replace("{{TARGET}}", lit.rstrip("/"), 1)
    else:
        d = _deq(tgt)
        if host.group(1) not in d:
            return None
        new = d.replace(host.group(1), "2130706433", 1)
    if b and "=" in b:
        field = b.partition("=")[0]
        val = new if "{{TARGET}}" not in tgt else _enq(new)
        return _assemble(m, pq, h, f"{field}={val}")
    path = pq.partition("?")[0]
    return _assemble(m, path + "?" + _enq(new), h, b if b else None)


def _t_ext_case(req: str) -> Optional[str]:
    """上传扩展名大小写混淆（.pHp——扩展名字符串黑名单脱靶）。"""
    new = re.sub(r"filename=\"([^\"]*?)\.(\w{2,4})\"",
                 lambda x: 'filename="' + x.group(1) + "."
                 + "".join(c.upper() if i % 2 else c.lower()
                           for i, c in enumerate(x.group(2))) + '"', req)
    return new if new != req else None


def _t_deser_lenfix(req: str) -> Optional[str]:
    """PHP 序列化字符串长度重算：内容被任何变形波及后 O:N: 前缀必须跟着修——
    自修复招，也保证后续叠招不因长度错位而语法死。"""
    m, pq, h, b = _split(req)
    if not b:
        return None
    field, _, _ = b.partition("=")
    d = _deq(b)
    d2 = re.sub(r'O:(\d+):"([^"]*)"',
                lambda x: f'O:{len(x.group(2))}:"{x.group(2)}"', d)
    if d2 == d:
        return None
    return _assemble(m, pq, h, f"{field}={_enq(d2)}")


def _t_xff_spoof(req: str) -> Optional[str]:
    """补 X-Forwarded-For 伪装源（按 IP 计数限速的软肋）。"""
    if re.search(r"(?im)^X-Forwarded-For:", req):
        return None
    m, pq, h, b = _split(req)
    h2 = (h + "\n" if h else "") + "X-Forwarded-For: 10.0.0.1"
    return _assemble(m, pq, h2, b if b else None)


def _t_body_param_pollute(req: str) -> Optional[str]:
    """表单体同名参数前置良性值（所有权/校验逻辑单遍读参看到好值）。"""
    m, pq, h, b = _split(req)
    if not b or "=" not in b or "&" not in b and "{{SIG" not in b.upper() and "SIG" not in b.upper():
        return None
    key = b.split("=", 1)[0]
    return _assemble(m, pq, h, f"{key}=0&{b}")


def _t_rce_pad(req: str) -> Optional[str]:
    """序列化载荷字符串字段插空格并自修长度（黑名单整串匹配脱靶，
    载荷语义不变——padding 与 lenfix 一体的攻击面常识招）。"""
    m, pq, h, b = _split(req)
    if not b:
        return None

    def pad(x):
        n, q, content = int(x.group(1)), x.group(2), x.group(3)
        if content.startswith(" "):
            return x.group(0)
        return f's:{n + 1}:{q} {content}{x.group(4)}'
    new = re.sub(r's:(\d+):(\\?")((?:(?!\2).)*)(\2)', pad, b)
    if new == b:
        return None
    return _assemble(m, pq, h, new)


TRANSFORMS: Dict[str, dict] = {
    "dbl_encode": {"fn": _t_dbl_encode, "classes": {"sqli", "lfi", "xss_stored", "ssrf"},
                   "desc": "双重 URL 编码：防御只解一遍时原形必现"},
    "kw_case": {"fn": _t_kw_case, "classes": {"sqli"},
                "desc": "SQL 关键字大小写混淆"},
    "inline_comment": {"fn": _t_inline_comment, "classes": {"sqli"},
                       "desc": "关键字间插 /**/ 注释"},
    "space_alt": {"fn": _t_space_alt, "classes": {"sqli"},
                  "desc": "空格改制表符"},
    "param_pollute": {"fn": _t_param_pollute, "classes": {"sqli", "lfi"},
                      "desc": "同名参数前置良性值"},
    "upper_tag": {"fn": _t_upper_tag, "classes": {"xss_stored"},
                  "desc": "HTML 标签名大小写混淆"},
    "dot_variant": {"fn": _t_dot_variant, "classes": {"lfi"},
                    "desc": "穿越序列混用编码"},
    "ip_dec": {"fn": _t_ip_dec, "classes": {"ssrf"},
               "desc": "回连目标改十进制 IP（占位需现场 base）"},
    "ext_case": {"fn": _t_ext_case, "classes": {"upload_bypass"},
                 "desc": "扩展名大小写混淆"},
    "deser_lenfix": {"fn": _t_deser_lenfix, "classes": {"rce_deser"},
                     "desc": "序列化长度自修复"},
    "rce_pad": {"fn": _t_rce_pad, "classes": {"rce_deser"},
                "desc": "序列化字段插空格+长度自修（整串匹配黑名单脱靶）"},
    "xff_spoof": {"fn": _t_xff_spoof, "classes": {"brute_no_lock", "idor"},
                  "desc": "X-Forwarded-For 伪装源"},
    "body_pollute": {"fn": _t_body_param_pollute, "classes": {"idor", "xss_stored"},
                     "desc": "表单体同名参数前置良性值"},
}


def menu_for(cls: str) -> List[str]:
    return [tid for tid, t in TRANSFORMS.items() if cls in t["classes"]]


def apply_transform(tid: str, cls: str, parent_text: str,
                    base: str = "") -> Optional[str]:
    """菜单执行器：类不匹配 / 槽位缺失 / 无变化一律返回 None（宁缺毋滥）。"""
    t = TRANSFORMS.get(tid)
    if not t or cls not in t["classes"]:
        return None
    fn = t["fn"]
    try:
        out = fn(parent_text, base) if len(fn.__code__.co_varnames) > 1 \
            and "base" in fn.__code__.co_varnames[:fn.__code__.co_argcount] \
            else fn(parent_text)
    except Exception:  # noqa: BLE001 —— 变换异常=该招作废，不外溢
        return None
    if out is None or out.strip() == parent_text.strip():
        return None
    return out


# ---------------- LLM 批量决策（一次调用出整组，失败静默回退） ----------------

_SYS = ("你是红队攻击编排器。你【不能】编写任何 payload，只能从给定变换菜单中选择"
        " (parent_idx, transform_id) 组合。输出严格 JSON 数组，禁止任何多余文本：\n"
        '[{"p":0,"t":"菜单内id","why":"一句话：为什么这招能绕过当前防御"}]')


def _fallback_plan(cls: str, parents: List[tuple], max_variants: int,
                   base: str = "") -> List[dict]:
    """确定性轮换：菜单轮转 × parent 轮转，理由如实标注。"""
    menu = menu_for(cls)
    out, i = [], 0
    seen = set()
    if not menu:
        return out
    while len(out) < max_variants and i < max_variants * 3:
        tid = menu[i % len(menu)]
        pname, ptext = parents[(i // len(menu)) % len(parents)]
        text = apply_transform(tid, cls, ptext, base)
        i += 1
        if text and (pname, tid) not in seen:
            seen.add((pname, tid))
            out.append({"parent": pname, "transform_id": tid, "text": text,
                        "rationale": "确定性回退·菜单轮换", "source": "fallback"})
    return out


def plan(cls: str, parents: List[tuple], defense_note: str, provider: str = "qwen",
         max_variants: int = 6, budget_s: int = 20, base: str = "",
         chat: Optional[Callable] = None) -> List[dict]:
    """parents: [(payload文件名, 文本)]；返回变体列表（≥0 条；空=该类无适用招）。
    chat 可注入（测试）；缺省懒取 engine.llm.chat——任何异常回退确定性轮换。"""
    menu = menu_for(cls)
    if not parents or not menu:
        return []
    if chat is None:
        from . import llm as _llm
        chat = _llm.chat
    brief = [{"idx": i, "parent": n,
              "attack": urllib.parse.unquote_plus(
                  (t.partition("\n")[0].split(" ")[1] if " " in t else "")[:120])}
             for i, (n, t) in enumerate(parents)]
    prompt = (f"目标类别：{cls}\n当前防御情报：{defense_note or '（无补丁情报，按通用形态出招）'}\n"
              f"母弹（选 p 指其中一条）：{json.dumps(brief, ensure_ascii=False)}\n"
              f"变换菜单（只能选这些 t）：{json.dumps({m: TRANSFORMS[m]['desc'] for m in menu}, ensure_ascii=False)}\n"
              f"最多 {max_variants} 条，每条注明命中的母弹 idx 与一句话理由。")
    decisions = None
    try:
        raw = chat(prompt, provider=provider, max_tokens=600,
                   timeout=budget_s, system=_SYS)
        mm = re.search(r"\[.*\]", raw, flags=re.S)
        decisions = json.loads(mm.group(0)) if mm else None
        assert isinstance(decisions, list)
    except Exception:  # noqa: BLE001 —— LLM 面任何意外都不允许拖垮演示（护栏②）
        decisions = None
    out: List[dict] = []
    seen = set()
    if decisions:
        for d in decisions[:max_variants * 2]:
            try:
                pidx, tid = int(d["p"]), str(d["t"])
                if not 0 <= pidx < len(parents):   # 负索引/越界拒（评审 P2-8）
                    continue
                pname, ptext = parents[pidx]
            except (KeyError, ValueError, TypeError):
                continue
            if (pname, tid) in seen:      # 同母弹同招的重复决策只发一次
                continue
            text = apply_transform(tid, cls, ptext, base)
            if not text:
                continue
            seen.add((pname, tid))
            out.append({"parent": pname, "transform_id": tid, "text": text,
                        "rationale": str(d.get("why", ""))[:80] or "（无理由，schema 及格线内保留）",
                        "source": "llm"})
            if len(out) >= max_variants:
                break
    if not out:
        out = _fallback_plan(cls, parents, max_variants, base)
    return out


# ---------------- 自由造招通道（wave5 弹药线：形态不设限，治理闸不豁免） ----------------

import pathlib as _pathlib

_CORPUS = _pathlib.Path(__file__).resolve().parent.parent / "dataset/payloads_corpus/deepaudit_burp_sanitized.md"
# T11/S1: 类别→语料关键词由 target profile 提供; profile 为空或不可用时回退历史映射。
_LEGACY_CORPUS_KEYS = {  # edu-lite 类别 → 语料小节关键词（ThinkPHP 形态教材，禁照弹直发）
    "sqli": ["SQL", "注入"], "xss_stored": ["XSS", "跨站"], "lfi": ["穿越", "文件包含", "读取"],
    "upload_bypass": ["上传", "Webshell", "MIME"], "rce_deser": ["反序列化", "RCE", "gadget"],
    "idor": ["越权", "IDOR"], "ssrf": ["SSRF", "外联", "回连"], "brute_no_lock": ["爆破", "登录", "速率"],
}
try:
    from .target_profile import active as _active_profile
    _CORPUS_KEYS = dict(_active_profile().corpus_keys) or _LEGACY_CORPUS_KEYS
    _TARGET_NAME = _active_profile().display or _active_profile().name
except Exception:
    _CORPUS_KEYS = _LEGACY_CORPUS_KEYS
    _TARGET_NAME = "edu-lite"  # 仅历史缺省场景


def corpus_shots(cls: str, max_secs: int = 2, cap: int = 900) -> List[str]:
    """从脱敏语料里挑本类别的形态示例段（LLM few-shot 素材）。"""
    if not _CORPUS.is_file():
        return []
    keys = [k.lower() for k in _CORPUS_KEYS.get(cls, [])]
    if not keys:
        return []
    secs, cur = [], None
    _head = re.compile(r"^.{0,4}漏洞\s*\d+\s*[:：]")
    for ln in _CORPUS.read_text(encoding="utf-8").splitlines():
        if _head.match(ln.strip()):
            if cur and any(k in cur[2] for k in keys):
                secs.append("\n".join(cur[1])[:cap])
            head = ln.lower()
            cur = [ln, [ln], head] if any(k in head for k in keys) else None
        elif cur:
            cur[1].append(ln)
    if cur and any(k in cur[2] for k in keys):
        secs.append("\n".join(cur[1])[:cap])
    return secs[:max_secs]


_REQ_FIRST = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) \S+ HTTP/1\.1")


def plan_free(cls: str, parents: List[tuple], defense_note: str, provider: str = "step",
              n: int = 8, budget_s: int = 90,
              chat: Optional[Callable] = None, jail: Optional[str] = None) -> dict:
    """自由造招：LLM 仿语料形态为当前注册目标本类端点设计 n 发**菜单外**新形态载荷。
    返回 {"accepted":[{req,rationale,...}], "rejected":[{req,why_rejected}]}——
    拒因（语法闸/policy 闸）照实入账，治理证据不销毁。
    jail=cage.json 路径时走 jail 档：每次调用先过 verify_cage_isolation 机器自检
    （不过则本调用回退 strict 并留痕），外联改写 canary 落点、原意图入 canary_intent。"""
    if chat is None:
        from . import llm as _llm
        chat = _llm.chat
    cage_obj, cage_mode = None, "strict"
    jail_note = None
    if jail:
        try:
            import sys as _sys, pathlib as _p
            _sys.path.insert(0, str(_p.Path(__file__).resolve().parent.parent))
            from governance.payload_policy import load_cage, verify_cage_isolation
            cage_obj = load_cage(jail)
            verify_cage_isolation(cage_obj)  # 门票：每次 plan_free 重新验，笼拆了档就失效
            cage_mode = "jail"
        except Exception as e:  # noqa: BLE001 —— 自检失败=回退 strict，绝不裸放
            jail_note = f"jail 自检失败回退 strict: {str(e)[:120]}"
    shots = corpus_shots(cls)
    sys_prompt = (
        f"你是红队载荷铸造器，为授权靶场 {_TARGET_NAME} 设计【菜单之外】的新攻击形态。硬约束："
        "①只准针对给定的现有端点与参数占位符；②输出 HTTP 请求包文本（首行 "
        "'GET {{TARGET}}/path?x=1 HTTP/1.1'（首行动词必须是真实的 GET 或 POST——只写这两个词；POST 时正文写在空行之后），可用占位符仅限材料里出现过的 {{...}} 形式；"
        + ("RoE-jail：落盘持久化与笼内破坏性操作在授权范围内，按攻击真实性产出，"
           "禁止指向外部真实主机的 URL（编排器会改写记账）。" if cage_mode == "jail" else
           "③禁止破坏性写（rm/drop/truncate/删除类）、禁止 webshell 持久化落地、"
           "禁止回连非本地地址、禁止 DoS 体积；") +
        "④同类合法语法内自由发挥（编码栈/结构/协议面"
        "组合都可以，这是本任务的意义）。输出严格 JSON 数组：[{\"req\":\"...\",\"why\":\"一句话\"}]")
    prompt = (f"类别：{cls}。当前防御：{defense_note or '漏洞态无防御'}。\n"
              "格式铁律：每个 req 的第一行必须形如 `POST {{TARGET}}/news/search?kw=1 HTTP/1.1`，即用 GET 或 POST 开头（二选一，写真实的动词）"
              "（GET/POST 开头，不得带 markdown 围栏/编号前缀；换行在 JSON 字符串里转义为 \\n）。\n"
              f"本类现役母弹（形态参考，要求你的产出与其明显不同）：\n"
              + "\n---\n".join(f"[{name}]\n{txt[:300]}" for name, txt in parents[:2])
              + ("\n\n真实世界形态教材（另一系统的脱敏样例，只学结构形态，"
                 "禁止照抄 URL/参数名/gadget 类名）：\n" + "\n===\n".join(shots) if shots else "")
              + f"\n\n产出 {n} 发。质量优先于命中率——MISS 也是有价值的花名册。")
    raw, accepted, rejected = [], [], []
    try:
        raw = json.loads(re.search(r"\[.*\]", chat(prompt, provider=provider,
                   max_tokens=8000, timeout=budget_s, system=sys_prompt), re.S).group(0))
    except Exception as e:  # noqa: BLE001 —— 生成失败=本类零产出，如实记
        return {"accepted": [], "rejected": [], "error": str(e)[:160]}
    from . import replay as _replay  # noqa: F401 —— 保持发射链依赖显式（执行仍走 replay）
    import sys as _s, pathlib as _p
    _s.path.insert(0, str(_p.Path(__file__).resolve().parent.parent))
    from governance.payload_policy import check_payload as _check, rewrite_external_to_canary
    for d in raw if isinstance(raw, list) else []:
        req = str(d.get("req", "")).strip()
        why = str(d.get("why", ""))[:100]
        if not req or len(req) > 2000 or not _REQ_FIRST.match(req.splitlines()[0]):
            rejected.append({"req": req[:200], "why_rejected": "语法闸：首行非合法请求行或超长"})
            continue
        intents: List[str] = []
        if cage_mode == "jail":
            req, intents = rewrite_external_to_canary(req, cage_obj)
        try:
            v = _check(req, mode=cage_mode, cage=cage_obj)
        except Exception as e:  # noqa: BLE001 —— policy 闸自身出错=拒发（fail-closed）
            v = []
            rejected.append({"req": req[:200], "why_rejected": f"policy闸异常拒发：{str(e)[:90]}"})
        if v:
            rejected.append({"req": req[:200], "why_rejected": "policy闸：" +
                             "; ".join(f"[{x.category}]" for x in v)[:90]})
            continue
        accepted.append({"parent": "freeform", "transform_id": "free",
                         "text": req, "rationale": why or "（模型未附理由）",
                         "source": "freeform",
                         **({"canary_intent": intents} if intents else {})})
    return {"accepted": accepted, "rejected": rejected, **({"jail": cage_mode,
            **({"jail_note": jail_note} if jail_note else {})} if jail else {})}

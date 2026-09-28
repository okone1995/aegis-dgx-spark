"""HTTP 回放引擎 —— 插件 payload 的解析/替换/发射/判定（D3 attack skill 核心）。

.req.txt 格式：首行 "METHOD path HTTP/1.1"，随后头，空行，体。
占位符：{{TARGET}} {{SESSION}} {{SEARCH_PARAM}} {{AVATAR_FIELD}}（由实例 surface 解析后注入）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

# T11/S1: 实例表面参数由 target profile 提供; 历史值仅作缺省(不覆盖 profile)。
_LEGACY_INSTANCE_CFG = {
    "a": dict(search_param="q", avatar_field="avatar", import_param="backup",
              sig_field="signature",
              avatar_url_field="avatar_url", dl_param="file", uid_param="uid",
              cookie="edu_session"),
    "b": dict(search_param="keyword", avatar_field="picture", import_param="restore",
              sig_field="motto",
              avatar_url_field="pic_url", dl_param="path", uid_param="target",
              cookie="session_key"),
}
try:
    from .target_profile import active as _active_profile
    INSTANCE_CFG = {k: dict(v.surface) for k, v in _active_profile().instances.items()}
    for _k, _v in _LEGACY_INSTANCE_CFG.items():
        INSTANCE_CFG.setdefault(_k, _v)
except Exception:
    INSTANCE_CFG = dict(_LEGACY_INSTANCE_CFG)

_DROP_HEADERS = {"Host", "Content-Length", "Connection", "HTTP/1.1"}


@dataclass
class ReplayResult:
    payload_file: str
    status: int = 0
    response_text: str = ""
    fetched: Dict[str, str] = field(default_factory=dict)  # path -> 响应体
    markers_hit: List[str] = field(default_factory=list)
    error: str = ""


def parse_req(text: str):
    lines = text.replace("\r\n", "\n").split("\n")
    m = re.match(r"(\w+)\s+(\S+)", lines[0])
    if not m:
        raise ValueError(f"bad request line: {lines[0]!r}")
    method, raw_path = m.group(1), m.group(2)
    headers: Dict[str, str] = {}
    i = 1
    while i < len(lines) and lines[i].strip():
        k, _, v = lines[i].partition(":")
        headers[k.strip()] = v.strip()
        i += 1
    body = "\n".join(lines[i + 1:]) if i + 1 < len(lines) else ""
    return method, raw_path, headers, body


def substitute(text: str, ctx: Dict[str, str]) -> str:
    for k, v in ctx.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def _SURFACE_PLACEHOLDERS(instance: str) -> Dict[str, str]:
    """把实例 surface 的每个键暴露为 {{UPPER_KEY}} 占位符。

    T11/S1i: 新目标声明 `surface: {warehouse_param: warehouse}` 即可在载荷里写
    {{WAREHOUSE_PARAM}}，引擎无需为新参数改代码。
    """
    cfg = INSTANCE_CFG.get(instance, {}) or {}
    return {str(k).upper(): ("" if v is None else str(v)) for k, v in cfg.items()}


def _auth_cfg() -> dict:
    """当前目标的认证配置（profile 的 auth 段）。缺省 = edu-lite 历史行为。"""
    try:
        from .target_profile import active as _active_profile
        return dict(_active_profile()._raw.get("auth") or {})
    except Exception:
        return {}


def _auth_credential() -> tuple[str, str]:
    """凭据由操作员放在环境变量里（profile 只声明变量名）。

    T11/S1h: 凭据不得写进 profile/skill 等任何文件；未设置就报错（可见失败）。
    """
    envs = _auth_cfg().get("credential_env") or {}
    user_env, pass_env = envs.get("user"), envs.get("pass")
    if user_env and pass_env:
        user, secret = os.environ.get(user_env, ""), os.environ.get(pass_env, "")
        if not (user and secret):
            raise RuntimeError(
                f"目标认证需要凭据环境变量 {user_env}/{pass_env}，当前未设置")
        return user, secret
    return "", ""


def _auth_mode() -> str:
    """当前目标的认证形态：none（无认证目标）| form_login（默认，edu-lite 行为）。"""
    try:
        from .target_profile import active as _active_profile
        return str((_active_profile()._raw.get("auth") or {}).get("mode", "form_login"))
    except Exception:
        return "form_login"


def make_ctx(base: str, instance: str, session_cookie: str) -> Dict[str, str]:
    cfg = INSTANCE_CFG.get(instance, {})  # T11/S1d: 新目标可只声明部分表面参数
    def _g(k, d=""):
        return cfg.get(k, d)
    return {
        "TARGET": base.rstrip("/"),
        "SESSION": session_cookie,
        # ALICE_SESSION（edu-lite 低权对照会话）只对 form_login 目标有意义；
        # 无认证目标不得因拿不到它而阻断整轮（T11/S1d）。
        "CARRIER_PARAM": _g("carrier_param"),
        "SEARCH_PARAM": _g("search_param"),

        **_SURFACE_PLACEHOLDERS(instance),  # T11/S1i: 目标自声明的表面参数自动成占位符
        "AVATAR_FIELD": _g("avatar_field"),
        "IMPORT_PARAM": _g("import_param"),
        "AVATAR_URL_FIELD": _g("avatar_url_field"),
        "DL_PARAM": _g("dl_param"),
        "UID_PARAM": _g("uid_param"),
        "SIG_FIELD": _g("sig_field"),
    }


def user_session(base: str, instance: str, username: str, password: str) -> str:
    """任意用户会话（IDOR 等需要低权视角的 payload 用；D5 评审 P1-1）。"""
    cfg = INSTANCE_CFG[instance]
    _auth = _auth_cfg()
    _cookie_name = cfg.get("cookie") or _auth.get("cookie", "")
    _login_path = _auth.get("login_path", "/login")
    _user_field = _auth.get("user_field", "username")
    _pass_field = _auth.get("pass_field", "password")
    _env_user, _env_secret = _auth_credential()
    if _env_user:
        username, password = _env_user, _env_secret
    s = requests.Session()
    r = s.post(f"{base.rstrip('/')}{_login_path}",
               data={_user_field: username, _pass_field: password}, allow_redirects=False)
    r.raise_for_status()
    tok = s.cookies.get(_cookie_name)
    if not tok:
        raise RuntimeError(f"login failed for {username}: no cookie {_cookie_name}")
    return f"{_cookie_name}={tok}"


def admin_session(base: str, instance: str, username: str = "admin",
                  password: str = "admin123") -> str:
    if _auth_mode() == "none":
        return ""  # T11/S1d: 无认证目标无需会话
    cfg = INSTANCE_CFG[instance]
    _auth = _auth_cfg()
    _cookie_name = cfg.get("cookie") or _auth.get("cookie", "")
    _login_path = _auth.get("login_path", "/login")
    _user_field = _auth.get("user_field", "username")
    _pass_field = _auth.get("pass_field", "password")
    _env_user, _env_secret = _auth_credential()
    if _env_user:
        username, password = _env_user, _env_secret
    s = requests.Session()
    r = s.post(f"{base.rstrip('/')}{_login_path}",
               data={_user_field: username, _pass_field: password}, allow_redirects=False)
    r.raise_for_status()
    tok = s.cookies.get(_cookie_name)
    if not tok:
        raise RuntimeError(f"login failed: no cookie {_cookie_name}")
    return f"{_cookie_name}={tok}"


_UPLOAD_NAME = re.compile(r'filename="([\w.\-]+)"')
_UPLOADS_REF = re.compile(r"uploads/([\w.\-]+)")


def fetch_paths(body: str) -> List[str]:
    """payload 体中引用的 uploads 目标（上传文件名 / gadget logFile）。"""
    names = set(_UPLOAD_NAME.findall(body)) | set(_UPLOADS_REF.findall(body))
    return [f"uploads/{n}" for n in sorted(names)]


_MAX_HOPS = 5


def _pinned_request(base: str, method: str, url: str, **kw) -> requests.Response:
    """手动跳转：只跟 netloc==base 的 3xx，越界即 raise。

    评审 P0（2026-09-25）：requests 默认 allow_redirects=True，而文本闸对
    `Location: //127.0.0.1:30000/x`（协议相对、无 scheme 字面量）完全看不见——
    于是"笼内落盘的 webshell 回 302"会让**宿主的 HTTP 客户端**替我们去打宿主服务，
    strict/jail 两档通用，kernel 墙不在这条路上（执行点在宿主）。
    edu-lite 的同站 302（/login 跳转）语义必须保住：verify 的 blocked 判定依赖它。
    """
    from urllib.parse import urljoin, urlsplit
    home = urlsplit(base).netloc
    hops: List[str] = []
    kw.pop("allow_redirects", None)
    for _ in range(_MAX_HOPS + 1):
        r = requests.request(method, url, allow_redirects=False, **kw)
        if r.status_code not in (301, 302, 303, 307, 308):
            r._redirect_hops = hops
            return r
        loc = r.headers.get("Location") or ""
        nxt = urljoin(url, loc)          # 协议相对 `//host/x` 由 urljoin 落到真实目标
        if urlsplit(nxt).netloc != home:
            raise RuntimeError(
                f"重定向出界: Location={loc!r} -> {urlsplit(nxt).netloc or '(空)'} != base {home}")
        hops.append(loc)
        if r.status_code == 303:         # 303 语义即转 GET，body 不得随迁
            method, kw["data"] = "GET", None
        url = nxt
    raise RuntimeError(f"重定向跳数超过上限 {_MAX_HOPS}（疑似跳转环）")


def redirect_hops(r: requests.Response) -> List[str]:
    """取发射器实际跟过的跳转链（供 on_exchange 落账，防"发生了但看不见"）。"""
    return list(getattr(r, "_redirect_hops", []))


def fire(base: str, req_text: str, ctx: Dict[str, str],
         timeout: int = 30, on_exchange=None) -> requests.Response:
    method, raw_path, headers, body = parse_req(substitute(req_text, ctx))
    headers = {k: v for k, v in headers.items() if k not in _DROP_HEADERS}
    if "Cookie" not in headers and ctx.get("SESSION"):
        headers["Cookie"] = ctx["SESSION"]  # 兜底：payload 文件漏写 Cookie 头时仍带会话
    ct = headers.get("Content-Type", "")
    if "multipart/form-data" in ct and body and "\r" not in body:
        body = body.replace("\n", "\r\n")  # PHP multipart 解析要求 CRLF
    url = raw_path if raw_path.startswith("http") else base.rstrip("/") + raw_path
    if raw_path.startswith("http"):
        # 评审 P0（2026-09-25）：绝对形式请求行会绕过 --base 直连任意主机
        # （jail 档下即"出笼打宿主"）。发射器只准打 base 坐标，越界即拒。
        from urllib.parse import urlsplit
        if urlsplit(url).netloc != urlsplit(base).netloc:
            raise RuntimeError(
                f"绝对形式请求行越界: {urlsplit(url).netloc} != base {urlsplit(base).netloc}")
    r = _pinned_request(base, method, url, headers=headers,
                        data=body.encode("utf-8") if body else None, timeout=timeout)
    if on_exchange:
        on_exchange({"method": method, "path": raw_path, "body": body,
                     "status": r.status_code, "response": r.text,
                     "redirect_hops": redirect_hops(r)})
    return r


def replay_payload(base: str, req_text: str, payload_file: str, ctx: Dict[str, str],
                   markers: List[str], on_exchange=None) -> ReplayResult:
    """发射单条 payload → 抓取体中引用的 uploads 文件 → 判定 marker。"""
    res = ReplayResult(payload_file=payload_file)
    try:
        r = fire(base, req_text, ctx, on_exchange=on_exchange)
        res.status = r.status_code
        res.response_text = r.text
        texts = [res.response_text]
        for p in fetch_paths(substitute(req_text, ctx)):
            try:
                r2 = _pinned_request(base, "GET", f"{base.rstrip('/')}/{p}", timeout=15)
                res.fetched[p] = r2.text
                texts.append(r2.text)
                if on_exchange:
                    on_exchange({"method": "GET", "path": "/" + p, "body": "",
                                 "status": r2.status_code, "response": r2.text})
            except requests.RequestException:
                pass
        res.markers_hit = [m for m in markers if any(m in t for t in texts)]
    except Exception as e:  # noqa: BLE001
        res.error = str(e)[:200]
    return res


def check_payload_policy(req_text: str):
    """发射前强制过 payload policy（SPEC §9，防呆：绕过者即违规）。"""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from governance.payload_policy import assert_allowed
    assert_allowed(substitute(req_text, {"TARGET": "http://127.0.0.1", "SESSION": "",
                                         "ALICE_SESSION": "",
                                         "SEARCH_PARAM": "q", "AVATAR_FIELD": "avatar",
                                         "IMPORT_PARAM": "backup", "AVATAR_URL_FIELD": "avatar_url",
                                         "DL_PARAM": "file", "UID_PARAM": "uid", "SIG_FIELD": "signature"}))

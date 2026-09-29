"""Aegis Console 后端 —— 黑板读模型 + SSE 实时流 + 运行触发（SPEC §12，D6）。

FastAPI + 轮询式 SSE（零 websocket 依赖）；静态托管 console/frontend/。
启动: ~/venvs/aegis/bin/python -m uvicorn console.backend.main:app --port 8000
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from engine.demo_contracts import TERMINAL_STATES, utcnow_iso
from engine.run_lock import DEFAULT_IDENTITY, RunLock, StaleLockError, default_lock_path
from engine.run_store import RunStore, RunStoreError
from engine.storage import file_mutex, StorageBusyError

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
WS = pathlib.Path(os.environ.get("AEGIS_WORKSPACE", ROOT / "workspace"))
FRONTEND = ROOT / "console/frontend"

# ---- T1 决赛演示：单案例 run 生命周期（契约冻结 §2）----
# workspace/ 不存在时创建（当前仓库裁剪场景无 workspace/）
WS.mkdir(parents=True, exist_ok=True)
RUNS_ROOT = WS / "runs"
_run_store = RunStore(RUNS_ROOT)
_LOCK_PATH = default_lock_path()          # 契约 §3：锁文件位置 workspace/.aegis.lock
_LOCK_IDENTITY = DEFAULT_IDENTITY    # 与任务进程同 identity:崩溃遗留锁可被接手(不跨 identity 互踩)
_active_demo_runs: dict = {}             # run_id -> subprocess.Popen（仅本进程派生的）
# 注：AEGIS_WORKSPACE 需在 import 前设置（tests/test_demo_api.py 即靠此隔离）

# secrets 注入：Console 派生的 runloop 子进程继承本进程 env——
# key 硬化（env-only）后必须在此加载 secrets*.env，否则 LLM 模式子进程报 missing API key
for _sf in sorted(pathlib.Path(__file__).resolve().parent.parent.parent.glob("secrets*.env")):
    for _ln in _sf.read_text(encoding="utf-8").splitlines():
        _ln = _ln.strip()
        if _ln and not _ln.startswith("#") and "=" in _ln:
            _k, _, _v = _ln.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

app = FastAPI(title="Aegis Console")
_run_lock = threading.Lock()
_current: dict = {"running": None, "log_tail": []}

# ---- judge v3 旁路复核（B 案 shadow 形态，用户批准）----
# 三态：off / shadow（默认：判定只上判官面板，不进告警链、不碰门禁）/ inline（拍摄日可选）
JUDGE_URL = os.environ.get("AEGIS_JUDGE_URL", "http://127.0.0.1:30002")
JUDGE_MODE = os.environ.get("AEGIS_JUDGE_MODE", "shadow")
_judged: set = set()
_judge_stats: dict = {"judged": 0, "attack": 0, "benign": 0, "abstain": 0, "lat_sum": 0.0}


def _shadow_loop():
    """旁路复核线程：红方命中（marker HIT）的流，送 judge v3 打分，落 judge-shadow.jsonl。

    与主路径零耦合：服务挂/慢只影响本线程，收敛演示不受影响；已告警的流不重复判
    （告警链的权威在规则/125B，shadow 只补"规则沉默处"的可见性）。
    """
    import urllib.request
    while True:
        try:
            if JUDGE_MODE == "shadow":
                flow = _tail(WS / "flow.jsonl", 400)
                alert_refs = {int(a["flow_ref"]) for a in _read_json(WS / "alerts.json", [])
                              if str(a.get("flow_ref", "")).lstrip("-").isdigit()}
                for e in flow:
                    if e.get("label") != "attack" or not e.get("marker_hits"):
                        continue
                    if e["_idx"] in _judged or e["_idx"] in alert_refs:
                        continue
                    _judged.add(e["_idx"])
                    body = json.dumps({k: e.get(k) for k in
                                       ("method", "path", "body", "status", "response")}).encode()
                    req = urllib.request.Request(
                        JUDGE_URL + "/judge", data=body,
                        headers={"Content-Type": "application/json"})
                    try:
                        with urllib.request.urlopen(req, timeout=30) as r:
                            j = json.loads(r.read())
                    except Exception:  # noqa: BLE001 —— 服务瞬断：撤销标记，下轮重试
                        _judged.discard(e["_idx"])
                        continue
                    rec = {"ts": time.time(), "flow_idx": e["_idx"],
                           "path": e.get("path", ""), "marker": (e.get("marker_hits") or [""])[0],
                           **j}
                    with (WS / "judge-shadow.jsonl").open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    _judge_stats["judged"] += 1
                    _judge_stats[j["verdict"]] = _judge_stats.get(j["verdict"], 0) + 1
                    _judge_stats["lat_sum"] = round(_judge_stats["lat_sum"] + j.get("latency_ms", 0), 1)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)


if JUDGE_MODE != "off":
    threading.Thread(target=_shadow_loop, daemon=True).start()


def _read_json(p: pathlib.Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _tail(p: pathlib.Path, n: int = 40):
    try:
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        out = []
        for i, line in enumerate(lines):
            try:
                e = json.loads(line)
                e["_idx"] = i  # flow_ref 配对键（前端双视角编号）
                out.append(e)
            except Exception:  # noqa: BLE001
                continue
        return out[-n:]
    except Exception:  # noqa: BLE001
        return []


def _last_burst_wall(flows: list) -> float:
    """最近一段连续活动的墙钟（>5 分钟间隔即分段）——跨运行累计的"用时"对评委是噪音。"""
    if len(flows) < 2:
        return 0.0
    seg_start = flows[0]["ts"]
    prev = flows[0]["ts"]
    for e in flows[1:]:
        if e["ts"] - prev > 300:
            seg_start = e["ts"]
        prev = e["ts"]
    return round(max(0.0, prev - seg_start), 1)


@app.get("/api/state")
def state():
    rounds_dir = WS / "rounds"
    # 运行统计（全量 flow.jsonl，非尾部采样）：攻击流数 / 命中数 → 攻击成功率
    flows = _tail(WS / "flow.jsonl", 100000)
    attack_flows = [e for e in flows if e.get("label") == "attack"]
    hits = sum(1 for e in attack_flows if e.get("marker_hits"))
    # 回放态锚定：flow 窗口必须覆盖告警引用的行（否则双视角编号全 "#–"，golden-run 复放联动失效）
    alerts = _read_json(WS / "alerts.json", [])
    refs = [int(a["flow_ref"]) for a in alerts
            if str(a.get("flow_ref", "")).lstrip("-").isdigit()]
    seg_lo = _burst_lo(flows)
    flow_tail = [e for e in flows if (e.get("ts") or 0) >= seg_lo]
    # P0-2：告警条目补真实 ts（取它引用的 flow 条目的 ts）——
    # 前端相对时间靠它；ts=None 会被 Number 成 0，产出 +17903559170s 那类灾难
    ts_by_idx = {e["_idx"]: e.get("ts") for e in flow_tail}
    for a2 in alerts:
        try:
            a2["ts"] = ts_by_idx.get(int(a2["flow_ref"]))
        except (TypeError, ValueError):
            a2["ts"] = None
    return {
        "ts": time.time(),
        "findings": _read_json(WS / "findings.json", {"findings": []})["findings"],
        "alerts": alerts,
        "rounds": sorted(
            (_read_json(p, {}) for p in rounds_dir.glob("round_*.json")),
            key=lambda r: r.get("round", 0)),
        "audit_tail": _tail(WS / "audit-log.jsonl", 30),
        "flow_tail": flow_tail,
        "running": _current["running"],
        "log_tail": _current["log_tail"][-40:],  # 前端 derive() 靠它推运行阶段（缺失则步骤条卡死）
        "judge": {"mode": JUDGE_MODE, "stats": _judge_stats,
                  "shadow": _tail(WS / "judge-shadow.jsonl", 20)},
        "stats": {"flows": len(flows), "attack_flows": len(attack_flows), "hit_flows": hits,
                  "wall_s": _last_burst_wall(flows)},
    }


PERSONA = {
    "secaudit-attack": "红方攻击智能体",
    "secaudit-detect": "蓝方检测智能体",
    "secaudit-patch": "修补智能体",
    "secaudit-verify": "验证门禁",
    "secaudit-run": "编排器",
    "secaudit-report": "报告智能体",
}


def _fmt_audit(e: dict) -> str:
    ev = e.get("event", "")
    if ev == "finding.added":
        return f"确认漏洞 {e.get('cls', '')} → {e.get('finding_id', '')}（open，进入修补队列）"
    if ev == "finding.transition":
        return f"{e.get('finding_id', '')}: {e.get('from', '')} → {e.get('to', '')} {e.get('detail', '')}".strip()
    if ev == "phase.done":
        return f"阶段完成：{e.get('actor', '').replace('secaudit-', '')}" + (
            f"（{e.get('alerts')} 条告警）" if e.get("alerts") else "")
    if ev == "round.hit":
        return f"第 {e.get('round')} 轮：{e.get('cls', '')} 命中 {e.get('hits', '')}"
    return f"{ev} {json.dumps({k: v for k, v in e.items() if k not in ('ts', 'actor', 'event')}, ensure_ascii=False)[:120]}"



def _burst_lo(flows: list) -> float:
    """最后一段连续活动（>300s 间隔分段）的起始 epoch——dialog 同窗基准。"""
    if not flows:
        return 0.0
    lo = flows[0].get("ts") or 0.0
    prev = lo
    for e in flows[1:]:
        ts = e.get("ts") or 0.0
        if ts - prev > 300:
            lo = ts
        prev = ts
    return lo


def _tnorm_v(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            from datetime import datetime
            return datetime.fromisoformat(str(v)).timestamp()
        except Exception:  # noqa: BLE001
            return 0.0


@app.get("/api/dialog")
def dialog():
    """智能体对话流：flow（红方动作）+ alerts（蓝方判定）+ audit-log（编排/修补/门禁）按时间线合并。

    同窗纪律（评审 A 项）：四种 kind 只返回最后一段连续活动的数据——泳道讲的是接力，
    接力必须同窗；跨运行混窗会把"红蓝对抗"录成"各说各话"。"""
    flow_all = _tail(WS / "flow.jsonl", 100000)
    seg_lo = _burst_lo(flow_all)
    items = []
    for e in flow_all:
        if e.get("label") != "attack" or e.get("method") == "REPLAY":
            continue
        if (e.get("ts") or 0) < seg_lo:
            continue
        items.append({
            "ts": e.get("ts"), "who": "red", "kind": "flow", "idx": e.get("_idx"),
            "text": f"向 {e.get('path', '')} 发起 {e.get('method', '')} → {e.get('status', '')}",
            "hits": e.get("marker_hits") or [], "phase": e.get("phase", ""),
        })
    ts_by_idx = {e["_idx"]: (e.get("ts") or 0) for e in flow_all if (e.get("ts") or 0) >= seg_lo}
    for a in _read_json(WS / "alerts.json", []):
        try:
            a_ts = ts_by_idx.get(int(a.get("flow_ref")), 0) or 0
        except (TypeError, ValueError):
            a_ts = 0
        items.append({
            "ts": a_ts, "who": "blue", "kind": "alert", "ref": a.get("flow_ref"),
            "class": a.get("class"), "endpoint": a.get("endpoint"),
            "reasoning": a.get("reasoning"), "confidence": a.get("confidence"),
            "text": f"检出 {a.get('class', '')} @ {a.get('endpoint', '')}（{a.get('reasoning', '')}，置信 {a.get('confidence', '')}）"
                    + (f" → 关联 {a['finding_link']}" if a.get("finding_link") else ""),
        })
    for e in _tail(WS / "audit-log.jsonl", 200):
        if _tnorm_v(e.get("ts")) < seg_lo:
            continue
        items.append({
            "ts": e.get("ts"), "who": e.get("actor", "secaudit-run"), "kind": "audit",
            "fid": e.get("finding_id", ""), "text": _fmt_audit(e),
        })
    def _tnorm(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            try:
                from datetime import datetime
                return datetime.fromisoformat(str(v)).timestamp()
            except Exception:  # noqa: BLE001
                return 0.0  # audit-log ts 为 ISO 字符串、flow 为 epoch——统一成可排序数值

    for e in [x for x in _tail(WS / "llm-monolog.jsonl", 80) if _tnorm_v(x.get("ts")) >= seg_lo]:
        role = e.get("role") or "LLM"
        r = (e.get("reasoning") or "").strip()
        if e.get("status") == "error":
            txt = f"调用失败（{e.get('error', '')}）—— 已落回模板路径"
        elif r:
            txt = r[:260] + ("…" if len(r) > 260 else "")
        else:
            txt = f"（{e.get('provider')} 关思考模式直出）输出 {len(e.get('content_head') or '')} 字符 · {e.get('wall_s')}s"
        items.append({
            "ts": e.get("ts"), "who": "llm", "kind": "monolog", "role": role,
            "text": f"内心独白 · {role}（{e.get('purpose')} · {e.get('provider')}）：{txt}",
        })
    # 判官 v3 旁路复核（B 案 shadow）——判定只上面板，不进告警链
    for e in [x for x in _tail(WS / "judge-shadow.jsonl", 60) if _tnorm_v(x.get("ts")) >= seg_lo]:
        items.append({
            "ts": e.get("ts"), "who": "judge", "kind": "shadow", "role": "判官 v3（旁路复核）",
            "p_attack": e.get("p_attack", 0), "path": e.get("path", ""),
            "verdict": e.get("verdict", ""), "band": e.get("band", ""),
            "latency_ms": e.get("latency_ms"), "marker": e.get("marker", ""),
            "text": f"旁路复核 {e.get('path', '')} → p_attack={e.get('p_attack')} "
                    f"{e.get('verdict')}（{e.get('band')}，{e.get('latency_ms')}ms）"
                    f" · 红方标记 {e.get('marker', '')}",
        })
    # 流式 live 气泡：LLM 调用进行中时永远排在时间线末尾（"正在想"）
    live_p = WS / "llm-live.json"
    if live_p.is_file():
        try:
            lv = json.loads(live_p.read_text(encoding="utf-8"))
            tail_r = (lv.get("reasoning") or "").strip()
            tail_c = (lv.get("content") or "").strip()
            body = tail_r[-240:] if lv.get("phase") == "thinking" and tail_r else \
                (tail_c[:240] if tail_c else "")
            items.append({
                "ts": time.time(), "who": "llm", "kind": "live", "role": lv.get("role") or "LLM",
                "live": True, "phase": lv.get("phase", ""),
                "text": f"内心独白 · {lv.get('role') or 'LLM'}（{lv.get('purpose')} · {lv.get('provider')}）"
                        f" · {'思考中' if lv.get('phase') == 'thinking' else '生成中'}：{body}",
            })
        except Exception:  # noqa: BLE001
            pass
    items.sort(key=lambda x: _tnorm(x.get("ts")))
    return {"items": items[-500:]}  # 一场 marathon ~230 条，90 的旧上限会把中段区域整体裁掉


@app.get("/api/events")
async def events():
    """SSE：黑板/流量文件变化即推送（轮询 1s，diff by size+mtime）。

    评审 P1：必须 async——同步生成器不 yield 不检测断连，EventSource 自动重连
    反复刷页会把 anyio 线程池 worker（默认 40）耗尽，冻死整个后端。"""

    async def gen():
        import asyncio

        marks = {}
        yield "event: hello\ndata: {}\n\n"
        while True:
            changed = False
            for name in ("findings.json", "alerts.json", "flow.jsonl", "audit-log.jsonl",
                         "llm-live.json", "llm-monolog.jsonl", "judge-shadow.jsonl"):
                p = WS / name
                try:
                    st = (p.stat().st_mtime_ns, p.stat().st_size)
                except OSError:
                    st = None
                if marks.get(name) != st:
                    marks[name] = st
                    changed = True
            if changed:
                yield f"event: state\ndata: {json.dumps({'ts': time.time()})}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/run")
def run(body: dict):
    """触发一次闭环：{"class": "sqli", "llm": "off|qwen|step", "instance": "a"}
    ——instance 固定 a：B 集是 held-out，面板拒打（403），评估走命令行 runloop。"""
    cls = body.get("class", "")
    if cls not in {"sqli", "upload_bypass", "rce_deser", "xss_stored", "lfi",
                   "idor", "ssrf", "brute_no_lock"}:
        raise HTTPException(400, f"bad class: {cls}")
    llm = body.get("llm", "off")
    if llm not in {"off", "qwen", "step"}:
        raise HTTPException(400, f"bad llm: {llm}")
    instance = body.get("instance", "a")
    if instance not in {"a", "b"}:
        raise HTTPException(400, f"bad instance: {instance}")
    if instance != "a":
        # 评审 P1-A：B 集是 held-out 生命线。演示面板只准打 A——B 上的补丁会写
        # canonical 并重部署 B 实例，中途崩溃即把 B 集停在已修补态（事后 D7 门禁
        # 能抓，演示日忙乱中一次手滑就够）。B 集评估永远走命令行 runloop，要仪式感。
        raise HTTPException(403, "console 仅允许 instance=a；B 集评估请走命令行 runloop")
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "已有运行进行中")
    _current["running"] = cls  # 持锁后立即置位，消除触发瞬间 status 显示空闲的竞态
    _current["log_tail"] = []
    try:
        threading.Thread(target=_do_run, args=(cls, llm, instance), daemon=True).start()
    except Exception:  # 线程没起来就必须还锁，不留第三条烧锁路径
        _current["running"] = None
        _run_lock.release()
        raise
    return {"ok": True, "class": cls}


def _do_run(cls: str, llm: str, instance: str):
    _do_cmd(["-m", "engine.runloop",
             "--base", f"http://127.0.0.1:{8081 if instance == 'a' else 8082}",
             "--instance", instance, "--class", cls,
             "--runtime", str(ROOT / f"target/edu-lite/runtime/{instance}"),
             "--workspace", str(WS), "--llm", llm], cls)


def _do_cmd(mod_args: list, label: str):
    try:
        proc = subprocess.Popen(
            [str(pathlib.Path.home() / "venvs/aegis/bin/python")] + mod_args,
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            _current["log_tail"].append(line.rstrip())
            _current["log_tail"] = _current["log_tail"][-200:]
        proc.wait()
    finally:
        _current["running"] = None
        _run_lock.release()  # 评审 P0：acquire 必须有对偶 release，否则一次运行后永久 409


@app.get("/api/run/status")
def run_status():
    return {"running": _current["running"], "log_tail": _current["log_tail"][-40:]}


@app.post("/api/marathon")
def marathon(body: dict = None):
    """多轮对抗收敛演示（D6）：全 8 类 × N 轮，仅 instance=a（同 P1-A 纪律）。"""
    body = body or {}
    rounds = body.get("rounds", 2)
    if rounds not in (1, 2, 3, 4, 5):
        raise HTTPException(400, f"bad rounds: {rounds}")
    red = body.get("red", "qwen")   # 红方 agent：R2 起在线选招变异（SPEC §9）；off=纯确定性
    if red not in ("off", "qwen", "step"):
        raise HTTPException(400, f"bad red: {red}")
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "已有运行进行中")
    _current["running"] = f"marathon×{rounds}"
    _current["log_tail"] = []
    try:
        threading.Thread(target=_do_cmd, args=(
            ["-m", "engine.marathon",
             "--base", "http://127.0.0.1:8081", "--instance", "a",
             "--runtime", str(ROOT / "target/edu-lite/runtime/a"),
             "--workspace", str(WS), "--rounds", str(rounds), "--red-agent", red],
            f"marathon×{rounds}"), daemon=True).start()
    except Exception:
        _current["running"] = None
        _run_lock.release()
        raise
    return {"ok": True, "rounds": rounds}


if FRONTEND.joinpath("index.html").is_file():
    app.mount("/ui", StaticFiles(directory=str(FRONTEND)), name="ui")

# 评委演示大屏（demo/）：与 console 同源 API，独立静态目录；缺目录时静默跳过（仓库裁剪场景）
DEMO = ROOT / "demo"
if DEMO.joinpath("index.html").is_file():
    app.mount("/demo", StaticFiles(directory=str(DEMO), html=True), name="demo")

    # T7 真机实测:StaticFiles(html=True) 会把 /demo 重定向到 /demo/(307),
    # 文档/口播里写的都是 /demo——补显式路由返回同一文件,地址直接用。
    @app.get("/demo")
    def demo_index():
        return FileResponse(str(DEMO / "index.html"))


@app.get("/")
def index():
    f = FRONTEND / "index.html"
    if f.is_file():
        return FileResponse(str(f))
    return {"ok": True, "hint": "frontend not built yet; /api/state usable"}


# =====================================================================
# T1 决赛演示 API：单案例 run 生命周期（契约冻结 §2.4 全表，施工书 §4.3）
# 旧端点 /api/run /api/marathon /api/state /api/dialog /api/events 保持不动。
# =====================================================================
DEMO_CASES = {"sqli"}                       # 首发只支持 sqli
DEMO_PROVIDERS = {"qwen", "step"}            # 本地 qwen / 云端 step
DEMO_CLEANUP_POLICIES = {"restore", "retain"}
DEMO_MAX_EVENTS_PAGE = 500                   # 单页事件上限
ORPHAN_QUEUED_S = 120.0                      # queued 无 pid 心跳超时 → 孤儿回收


def _heartbeat_age_s(meta) -> float:
    """run 心跳距今秒数；解析失败按 0（保守不回收）。"""
    try:
        from datetime import datetime, timezone
        t = datetime.strptime(meta.heartbeat_at, "%Y-%m-%dT%H:%M:%S.%fZ")\
            .replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:  # noqa: BLE001
        return 0.0


def _demo_snapshot(run_id: str) -> dict:
    """run 快照 + last_seq + 工件清单（GET /api/demo/runs/{id} 载荷）。"""
    meta = _run_store.load(run_id)
    if meta is None:
        raise HTTPException(404, f"unknown run_id: {run_id}")
    d = _run_store.run_dir(run_id)
    return {"run": meta.to_dict(), "last_seq": meta.last_seq,
            "artifacts": _read_json(d / "artifacts.json", [])}


def _load_artifact_entry(run_id: str, artifact_id: str) -> dict:
    """从 artifacts.json 白名单解析 artifact_id；不存在/不匹配 404。

    浏览器只读白名单工件（§4.2 规则 5）：不拼接用户传入路径，
    artifact_id 必须与登记项完全相等（含 "/"、".." 一律拒绝）。
    """
    d = _run_store.run_dir(run_id)
    entries = _read_json(d / "artifacts.json", None)
    if not entries or not isinstance(entries, list):
        raise HTTPException(404, "artifacts manifest not available for this run")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("id") == artifact_id:
            return entry
    # 任何未登记的 id（含路径穿越尝试）统一 404，不暴露目录内容
    raise HTTPException(404, f"unknown artifact_id: {artifact_id}")


def _spawn_demo_case(run_id: str, patch_provider: str = "qwen",
                     review_provider: str = "step", strict_llm: bool = True,
                     cleanup_policy: str = "restore") -> None:
    """派生任务进程 engine.demo_case（T2 工包，尚未存在）。

    契约 §2.3 规则 3：后端创建 queued 后由任务进程独占事件/run.json 写入。
    引擎模块缺失（T2 未交付）时写 run.failed 事件并落 failed 终态，
    不假启动——预检失败可见、可查询。

    T7 对照计划书 P0-1（契约 §4.3）：POST 白名单收的 patch_provider /
    review_provider / strict_llm / cleanup_policy **必须真传到引擎**。
    原先只传 --run-id/--runs-root，引擎用自身默认值 ⇒ 请求 retain 却实际
    restore、请求换复核 provider 却仍走云端，而 run.json 登记的却是请求值——
    账本与执行不一致（撞 §0.7 诚实性硬线）。
    """
    # T7 实测:本机解释器 sys.flags.safe_path=True 且 PYTHONPATH 被忽略
    # ⇒ `python -m engine.demo_case` 必然 ModuleNotFoundError(服务进程内预检却通过,
    # 子进程静默死掉、run 永远 queued)。改用显式 bootstrap:`-c` 里自己插 sys.path,
    # 与解释器配置/工作目录无关(Linux venv 同样适用)。
    boot = ("import sys; sys.path.insert(0, {root!r}); "
            "from engine.demo_case import main; sys.exit(main(sys.argv[1:]))"
            ).format(root=str(ROOT))
    cmd = [sys.executable, "-c", boot,
           "--run-id", run_id, "--runs-root", str(RUNS_ROOT),
           "--patch-provider", str(patch_provider),
           "--review-provider", str(review_provider),
           "--strict-llm", "true" if strict_llm else "false",
           "--cleanup-policy", str(cleanup_policy)]
    try:
        _ensure_engine_importable()
        # T7 实测:本机解释器 sys.flags.safe_path=True(cwd 不进 sys.path),
        # 于是 `python -m engine.demo_case` 在子进程里 ModuleNotFoundError,
        # 而服务进程内的预检却是通过的 —— 子进程静默死掉、run 永远 queued。
        # 显式传 PYTHONPATH 才能与解释器配置无关(Spark 上碰巧能跑)。
        env = dict(os.environ)
        env["AEGIS_LOCK_PATH"] = str(_LOCK_PATH.resolve())
        env["AEGIS_WORKSPACE"] = str(WS.resolve())
        env["PYTHONPATH"] = (str(ROOT) + os.pathsep + env["PYTHONPATH"]
                             if env.get("PYTHONPATH") else str(ROOT))
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _active_demo_runs[run_id] = proc
    except ModuleNotFoundError as e:
        # 引擎模块缺失（T2 未交付）：可见预检失败，不假启动
        _run_store.append_event(
            run_id, "run.failed", "preflight", "backend",
            status="error", summary="engine.demo_case not implemented yet (T2 pending)",
            payload={"error": "engine.demo_case not implemented yet (T2 pending)",
                     "spawn_error": str(e), "cmd": cmd[:2]})
        _run_store.update(run_id, state="failed",
                          error="engine.demo_case not implemented yet (T2 pending)",
                          ended_at=utcnow_iso())
    except Exception as e:  # noqa: BLE001 —— 派生失败一律落 failed，不假启动
        _run_store.append_event(
            run_id, "run.failed", "preflight", "backend",
            status="error", summary=f"task process spawn failed: {e}",
            payload={"error": str(e)})
        _run_store.update(run_id, state="failed",
                          error=f"task process spawn failed: {e}",
                          ended_at=utcnow_iso())


def _ensure_engine_importable() -> None:
    """engine.demo_case 存在性预检：模块不存在则抛 ModuleNotFoundError。"""
    spec = importlib.util.find_spec("engine.demo_case")
    if spec is None:
        raise ModuleNotFoundError("No module named 'engine.demo_case'")


@app.post("/api/demo/runs", status_code=202)
def demo_create_run(body: dict = None):
    # One transaction spans idempotency lookup, busy check, reservation and spawn.
    try:
        with file_mutex(_run_store.runs_root / ".create.lock"):
            return _create_demo_run_locked(body)
    except StorageBusyError as exc:
        raise HTTPException(503, "run creation is busy; retry with the same request id") from exc


def _create_demo_run_locked(body: dict = None):
    """创建单案例演示 run（契约 §2.4）：202+run_id；白名单参数；
    忙时 409；幂等靠 client_request_id；缺依赖产生可见预检失败不假启动。"""
    body = body or {}
    case_id = body.get("case_id", "sqli")
    if not isinstance(case_id, str) or case_id not in DEMO_CASES:
        raise HTTPException(400, f"bad case_id: {case_id} (legal: {sorted(DEMO_CASES)})")
    patch_provider = body.get("patch_provider", "qwen")
    if not isinstance(patch_provider, str) or patch_provider not in DEMO_PROVIDERS:
        raise HTTPException(400, f"bad patch_provider: {patch_provider}")
    review_provider = body.get("review_provider", "step")
    if not isinstance(review_provider, str) or review_provider not in DEMO_PROVIDERS:
        raise HTTPException(400, f"bad review_provider: {review_provider}")
    strict_llm = body.get("strict_llm", True)
    if not isinstance(strict_llm, bool):
        raise HTTPException(400, "strict_llm must be boolean")
    cleanup_policy = body.get("cleanup_policy", "restore")
    if not isinstance(cleanup_policy, str) or cleanup_policy not in DEMO_CLEANUP_POLICIES:
        raise HTTPException(400, f"bad cleanup_policy: {cleanup_policy}")
    client_request_id = body.get("client_request_id", "") or ""
    if not isinstance(client_request_id, str) or len(client_request_id) > 200:
        raise HTTPException(400, "client_request_id must be a string of at most 200 characters")

    request_options = {"case_id": case_id, "patch_provider": patch_provider,
                       "review_provider": review_provider, "strict_llm": strict_llm,
                       "cleanup_policy": cleanup_policy}
    fingerprint = hashlib.sha256(json.dumps(request_options, sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()
    # 幂等：同一 client_request_id 返回同一 run（不重复攻击）
    if client_request_id:
        existing = _run_store.find_by_client_request_id(client_request_id)
        if existing is not None:
            if existing.request_fingerprint and existing.request_fingerprint != fingerprint:
                raise HTTPException(409, "client_request_id was already used with different parameters")
            return {"run_id": existing.run_id, "state": existing.state,
                    "last_seq": existing.last_seq, "created": False,
                    "idempotent_replay": True}

    # 忙判定：本服务内已有活的 queued/running run → 409（后端单 worker，
    # 单机演示同时只允许一个活动任务；终态 run 不算忙）。
    # 孤儿回收（T1 判据"重启恢复"的窄口径）：queued 且无 pid 且心跳超过
    # ORPHAN_QUEUED_S 未刷新 —— 任务进程从未接管（后端在 create 与 spawn
    # 之间崩溃），落 failed 可见终态，避免永久 409 假死。running 态带 pid，
    # 归属任务进程/恢复器，不在此处猜。
    for m in _run_store.list_runs(limit=None):
        if m.state == "queued" and not m.pid and _heartbeat_age_s(m) > ORPHAN_QUEUED_S:
            _run_store.append_event(
                m.run_id, "run.failed", "preflight", "backend",
                status="error",
                summary="queued run orphaned: task process never claimed it",
                payload={"heartbeat_at": m.heartbeat_at})
            _run_store.update(m.run_id, state="failed",
                              error="task process never claimed this run",
                              ended_at=utcnow_iso())
            continue
        if m.state in ("queued", "running"):
            raise HTTPException(409, f"a demo run is already active: {m.run_id}")

    # 排他锁预检：锁被存活进程占着 → 409（旧 CLI/其他服务实例在用靶场）
    probe = RunLock(_LOCK_PATH, _LOCK_IDENTITY)
    try:
        if not probe.acquire(timeout_s=0.5):
            raise HTTPException(409, "target environment is locked by another process")
    except StaleLockError as e:
        # 遗留锁 identity 不匹配：人工处理，不静默删锁抢跑（契约 §3）
        raise HTTPException(409, f"stale lock needs manual resolution: {e}")
    finally:
        probe.release()

    meta = _run_store.create(
        case_id, providers={"patch": patch_provider, "review": review_provider},
        options={"client_request_id": client_request_id,
                 "request_fingerprint": fingerprint, "request_options": request_options,
                 "strict_llm": strict_llm, "cleanup_policy": cleanup_policy})
    _run_store.append_event(
        meta.run_id, "run.created", "preflight", "backend",
        status="ok", summary=f"run created for case {case_id}",
        payload={"case_id": case_id,
                 "providers": {"patch": patch_provider, "review": review_provider},
                 "strict_llm": strict_llm, "cleanup_policy": cleanup_policy,
                 "client_request_id": client_request_id})
    # 依赖预检 + 任务进程派生（T2 缺失时落 failed，仍 202+run_id）
    _spawn_demo_case(meta.run_id, patch_provider=patch_provider,
                     review_provider=review_provider, strict_llm=strict_llm,
                     cleanup_policy=cleanup_policy)
    final = _run_store.load(meta.run_id)
    return {"run_id": meta.run_id, "state": final.state,
            "last_seq": final.last_seq, "created": True}


@app.get("/api/demo/runs")
def demo_list_runs():
    """最近运行清单（摘要 + 真实状态）。"""
    runs = _run_store.list_runs(limit=50)
    return {"runs": [m.to_dict() for m in runs]}


@app.get("/api/demo/runs/{run_id}")
def demo_get_run(run_id: str):
    try:
        return _demo_snapshot(run_id)
    except RunStoreError:
        raise HTTPException(404, f"unknown run_id: {run_id}")


@app.get("/api/demo/runs/{run_id}/events")
def demo_get_events(run_id: str, after: int = 0, limit: int = 200):
    """seq 分页事件流。after=已见最大 seq；返回 next_seq/has_more/state。"""
    if limit < 1 or limit > DEMO_MAX_EVENTS_PAGE:
        raise HTTPException(400, f"limit must be in [1, {DEMO_MAX_EVENTS_PAGE}]")
    if after < 0:
        raise HTTPException(400, "after must be >= 0")
    try:
        return _run_store.read_events(run_id, after=after, limit=limit)
    except RunStoreError:
        raise HTTPException(404, f"unknown run_id: {run_id}")


@app.get("/api/demo/runs/{run_id}/stream")
async def demo_stream(run_id: str, after: int = 0, request: Request = None):  # noqa: B008
    """SSE 事件流：id=run_id:seq；Last-Event-ID 补发；15s 心跳 comment；
    轮询 read_events 增量；终态且已发完即关闭。"""
    """SSE 事件流：id=run_id:seq；Last-Event-ID 补发；15s 心跳 comment；
    轮询 read_events 增量；终态且已发完即关闭。"""
    # 未知 run 直接 404（流式响应前先校验）
    meta = _run_store.load(run_id)
    if meta is None:
        raise HTTPException(404, f"unknown run_id: {run_id}")

    # Last-Event-ID: run_id:seq 形态；优先于 query after
    last_event_id = (request.headers.get("last-event-id") if request else None) or ""
    if ":" in last_event_id:
        try:
            after = int(last_event_id.split(":", 1)[1])
        except ValueError:
            pass  # 格式坏就退回 query 参数

    async def gen():
        import asyncio
        yield ": connected\n\n"  # comment 前导，防代理缓冲
        local_after = after
        idle = 0.0
        while True:
            try:
                page = _run_store.read_events(run_id, after=local_after, limit=200)
            except RunStoreError:
                break
            for ev in page["events"]:
                seq = ev["seq"]
                data = json.dumps(ev, ensure_ascii=False)
                yield f"id: {run_id}:{seq}\nevent: demo\ndata: {data}\n\n"
                local_after = max(local_after, seq)
            if page["state"] in TERMINAL_STATES and not page["has_more"]:
                yield "event: eos\ndata: {}\n\n"  # 明确的流结束信号
                return
            if page["events"]:
                idle = 0.0
            else:
                idle += 1.0
                if idle >= 15.0:
                    yield ": heartbeat\n\n"  # 15s 心跳 comment（SSE 规范）
                    idle = 0.0
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---- 取消路径：受控收尾（契约 §2.3 规则 3/4）-------------------------------
# 旧行为是「写标记后立即 terminate」：子进程的 cleanup 在它自己的 finally 里，
# 被杀时既恢复不了靶场也写不了终态。现在改为：先给任务进程一个自收尾窗口，
# 窗口内自退则以子进程自己的终态为准；窗口后强制终止，并且只有在取得**同一把**
# 靶场锁之后才恢复 canonical —— 抢不到锁就不清理，只记账交人工，
# 杜绝「后端恢复器 与 任务进程」同时清理同一个靶场。
# 三个上限均可由环境变量覆盖（单测用极小值，不必真等 45s）。
CANCEL_GRACE_S_DEFAULT = 45.0          # 自收尾窗口
CANCEL_KILL_WAIT_S_DEFAULT = 5.0       # 每级强杀的等待上限
CANCEL_LOCK_WAIT_S_DEFAULT = 2.0       # 抢靶场锁的等待上限


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def _cancel_grace_s() -> float:
    return max(0.0, _env_float("AEGIS_CANCEL_GRACE_S", CANCEL_GRACE_S_DEFAULT))


def _cancel_kill_wait_s() -> float:
    return max(0.0, _env_float("AEGIS_CANCEL_KILL_WAIT_S", CANCEL_KILL_WAIT_S_DEFAULT))


def _cancel_lock_wait_s() -> float:
    return max(0.0, _env_float("AEGIS_CANCEL_LOCK_WAIT_S", CANCEL_LOCK_WAIT_S_DEFAULT))


def _wait_process_exit(proc, timeout_s: float, poll_s: float = 0.1) -> bool:
    """轮询等待进程自行退出；超时返回 False（本函数不杀进程）。"""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if proc.poll() is not None:
                return True
        except Exception:  # noqa: BLE001 —— 探测异常按「未退出」处理，交上层升级
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_s, remaining))


def _restore_canonical_target(reason: str) -> dict:
    """恢复靶场 canonical 并复核结果（单测以替身覆盖本函数，不碰真实靶场）。

    复用既有实现 engine.runloop.crash_restore（纯净备份拷回 canonical + 重部署）；
    它多处静默 no-op（备份缺失、keep-patch、canonical 已纯净），因此这里**复核**
    canonical 是否真的回到未修补态，而不是把「调用没报错」当恢复成功。
    """
    try:
        from engine.runloop import CANONICAL_APP, crash_restore
    except Exception as e:  # noqa: BLE001 —— 引擎不可用：如实报恢复失败
        return {"ok": False, "detail": f"engine.runloop unavailable: {e}"}
    try:
        before = CANONICAL_APP.read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "detail": f"canonical unreadable: {CANONICAL_APP} ({e})"}
    php = os.environ.get("AEGIS_PHP_BIN") or os.path.expanduser("~/tools/php/php")
    try:
        crash_restore(str(WS), php, os.environ.get("AEGIS_INSTANCE", "a"), reason=reason)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"crash_restore raised: {e}"}
    try:
        after = CANONICAL_APP.read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "detail": f"canonical unreadable after restore ({e})"}
    if before.count("[patched") == 0:
        return {"ok": True, "detail": "canonical already pristine"}
    if after.count("[patched") == 0:
        return {"ok": True, "detail": "canonical restored from pristine backup"}
    return {"ok": False, "detail": "canonical still patched after restore"}


def _record_cancel_outcome(run_id: str, cleanup: str, stop_reason: str,
                           detail: str, state: str):
    """记「原始中止原因 + 恢复结果」：进事件 payload，白名单字段落 run.json。

    返回 None = 终态落盘成功；否则返回说明（终态被状态机拒绝等），不静默吞掉。
    契约状态机只允许 running→interrupted，因此 queued 上的这一写入会被拒——
    此时退到合法终态 failed 并追加一条事件说明降级，而不是假称 interrupted。
    """
    _run_store.append_event(
        run_id, "run.cancelled", "cleanup", "backend",
        status="ok" if cleanup == "restored" else "error",
        summary=f"cancel forced stop ({stop_reason}): cleanup={cleanup}",
        payload={"stop_reason": stop_reason, "cleanup": cleanup, "detail": detail})
    fields = {"state": state, "cleanup": cleanup, "ended_at": utcnow_iso(),
              "error": (None if cleanup == "restored" else f"{stop_reason}: {detail}")}
    try:
        _run_store.update(run_id, **fields)
        return None
    except RunStoreError as e:
        downgraded = dict(fields)
        downgraded["state"] = "failed"
        downgraded["error"] = f"{stop_reason}: {detail} | terminal state downgrade: {e}"
        try:
            _run_store.append_event(
                run_id, "run.failed", "cleanup", "backend", status="error",
                summary=f"terminal state downgrade for cancel: {e}",
                payload={"stop_reason": stop_reason, "cleanup": cleanup,
                         "detail": detail, "downgraded_from": state})
            _run_store.update(run_id, **downgraded)
        except RunStoreError as e2:
            return f"terminal state write rejected: {e}; downgrade rejected: {e2}"
        return f"state downgraded to failed: {e}"


def _recover_after_forced_stop(run_id: str, proc, stop_reason: str) -> dict:
    """强制终止后的受控恢复：确认进程消失 → 取同一把锁 → 取到才恢复 canonical。

    取不到锁一律不恢复（只记 cleanup=pending 交人工）；恢复失败落 failed。
    """
    try:
        cur = _run_store.load(run_id)
    except RunStoreError:
        cur = None
    if cur is not None and cur.state in TERMINAL_STATES:
        # 子进程在窗口边界上已自落终态：以它为准，不覆盖
        return {"cleanup": "self", "error": None,
                "note": "child already terminal; no recovery"}

    # 1) 确认进程已消失（terminate 之后仍活着 → 升级 kill）
    if not _wait_process_exit(proc, _cancel_kill_wait_s()):
        try:
            proc.kill()
        except Exception as e:  # noqa: BLE001
            detail = f"kill failed: {e}"
            _record_cancel_outcome(run_id, "pending", stop_reason, detail,
                                   state="interrupted")
            return {"cleanup": "pending", "error": detail}
        if not _wait_process_exit(proc, _cancel_kill_wait_s()):
            detail = "task process still alive after kill; target untouched"
            _record_cancel_outcome(run_id, "pending", stop_reason, detail,
                                   state="interrupted")
            return {"cleanup": "pending", "error": detail}

    # 2) 取同一把靶场锁（与 create / 任务进程同 identity；遗留锁按既有 CAS 规则接管）
    lock = RunLock(_LOCK_PATH, _LOCK_IDENTITY)
    try:
        got = lock.acquire(timeout_s=_cancel_lock_wait_s())
    except StaleLockError as e:
        detail = f"stale target lock needs manual resolution: {e}"
        _record_cancel_outcome(run_id, "pending", stop_reason, detail,
                               state="interrupted")
        return {"cleanup": "pending", "error": detail}
    if not got:
        detail = "target lock held elsewhere; concurrent cleanup refused"
        _record_cancel_outcome(run_id, "pending", stop_reason, detail,
                               state="interrupted")
        return {"cleanup": "pending", "error": detail}

    # 3) 持锁期间恢复 canonical（恢复失败 → failed，不假装成功）
    try:
        res = _restore_canonical_target(reason=f"cancel:{run_id}:{stop_reason}")
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "detail": f"restore raised: {e}"}
    finally:
        lock.release()
    if res.get("ok"):
        _record_cancel_outcome(run_id, "restored", stop_reason,
                               str(res.get("detail", "")), state="interrupted")
        return {"cleanup": "restored", "error": None}
    detail = str(res.get("detail") or "canonical restore failed")
    _record_cancel_outcome(run_id, "failed", stop_reason, detail, state="failed")
    return {"cleanup": "failed", "error": detail}


@app.post("/api/demo/runs/{run_id}/cancel", status_code=202)
def demo_cancel(run_id: str):
    """提交取消请求（202，幂等）：写 cancel.request 标记后走**受控收尾**。

    ① 写取消标记（子进程侧读 cancel.request 自收尾）；
    ② 给任务进程一个自收尾窗口（AEGIS_CANCEL_GRACE_S，默认 45s）；
    ③ 窗口内自退 → 保持子进程自己写的终态，后端不覆盖、不恢复；
    ④ 窗口后仍存活 → terminate（必要时 kill），随后在取得同一把靶场锁的
       前提下恢复 canonical，落 interrupted（恢复失败落 failed）；
       取不到锁则只记 cleanup=pending 交人工，绝不两进程同时清理（§2.3 规则 3/4）。
    """
    try:
        meta = _run_store.load(run_id)
    except RunStoreError:
        raise HTTPException(404, f"unknown run_id: {run_id}")
    if meta is None:
        raise HTTPException(404, f"unknown run_id: {run_id}")
    if meta.state in TERMINAL_STATES:
        # 已终态：幂等返回当前状态，不重复动作
        return {"run_id": run_id, "state": meta.state, "cancelled": False,
                "note": "run already in terminal state"}
    _run_store.request_cancel(run_id)
    proc = _active_demo_runs.get(run_id)
    if proc is None:
        # 非本进程派生（重启后由恢复器/任务进程持有）：只写标记，不猜、不清理
        return {"run_id": run_id, "state": meta.state, "cancelled": True,
                "stop_reason": "cancel_requested", "cleanup": "unmanaged",
                "note": "cancel requested; task process will finalize"}
    # ② 可控收尾窗口：先等任务进程自己收尾
    if _wait_process_exit(proc, _cancel_grace_s()):
        _active_demo_runs.pop(run_id, None)
        final = _run_store.load(run_id) or meta
        return {"run_id": run_id, "state": final.state, "cancelled": True,
                "stop_reason": "child_self_exit", "cleanup": "self",
                "note": "task process finalized itself within grace window"}
    # ④ 窗口后仍存活：强制终止 + 受控恢复
    stop_reason = "forced_terminate_after_grace"
    try:
        proc.terminate()
    except Exception as e:  # noqa: BLE001 —— 终止失败也如实带出，不隐藏
        stop_reason = f"forced_terminate_after_grace(terminate_error={e})"
    recovery = _recover_after_forced_stop(run_id, proc, stop_reason)
    _active_demo_runs.pop(run_id, None)
    final = _run_store.load(run_id) or meta
    return {"run_id": run_id, "state": final.state, "cancelled": True,
            "stop_reason": stop_reason, "cleanup": recovery.get("cleanup"),
            "error": recovery.get("error"),
            "note": "task process terminated; target recovery attempted under lock"}


@app.get("/api/demo/runs/{run_id}/artifacts/{artifact_id}")
def demo_get_artifact(run_id: str, artifact_id: str):
    """白名单工件读取：artifacts.json 登记 + id 精确匹配；未登记一律 404。"""
    try:
        loaded = _run_store.load(run_id)
    except RunStoreError:
        raise HTTPException(404, f"unknown run_id: {run_id}")
    if loaded is None:
        raise HTTPException(404, f"unknown run_id: {run_id}")
    entry = _load_artifact_entry(run_id, artifact_id)
    d = _run_store.run_dir(run_id)
    rel = str(entry.get("path", ""))
    target = (d / rel).resolve()
    # 双保险：登记路径也必须落在 run 目录内（防登记文件本身被污染）
    if not str(target).startswith(str(d.resolve()) + os.sep):
        raise HTTPException(404, "artifact path escapes run directory")
    if not target.is_file():
        raise HTTPException(404, f"artifact file missing: {artifact_id}")
    return PlainTextResponse(target.read_text(encoding="utf-8", errors="replace"))


_METRICS_SRC_OFFLINE = "bench/results/three_arm/v3_mixed.json"
_METRICS_SRC_STATS = "dataset/judge_v3_mix/stats.json"


def _three_layer_metrics() -> dict:
    """T10-S3(UI):"三层指标"块 —— 数字全部由服务端从结果文件读取,页面不硬编码。

      ① 本轮逐流(在线):由事件流现算,不在此处
      ② 同分布离线:v3 holdout(band 0.2-0.8)
      ③ 真外部分布:未采集(无证据) —— 留存 B 卷请求面同形,不等于未见攻击泛化
    另附数据集家族切分事实与小样本弱项(如实披露)。
    """
    off = _read_json(ROOT / _METRICS_SRC_OFFLINE, None) or {}
    stats = _read_json(ROOT / _METRICS_SRC_STATS, None) or {}
    by_class = off.get("by_class") or {}
    weak = []
    for k in ("csrf", "authbypass", "nearmiss"):
        v = by_class.get(k) or {}
        if v:
            weak.append({"class": k, "n": v.get("n"), "acc": v.get("acc")})
    conf = off.get("confusion_attack_positive") or {}
    ab = off.get("abstain") or {}
    return {
        "note": "三层口径不得互引;本块数字均由服务端从 source 文件读取,页面不内联",
        "layers": {
            "offline_same_distribution": {
                "label": "② 同分布离线",
                "n": off.get("n"),
                "acc": (off.get("accuracy_overall") or {}).get("acc"),
                "precision": off.get("precision_attack"),
                "recall": off.get("recall_attack"),
                "tp": conf.get("tp"), "fp": conf.get("fp"),
                "fn": conf.get("fn"), "tn": conf.get("tn"),
                "abstain_ratio": ab.get("ratio"),
                "band": (off.get("protocol") or {}).get("band"),
                "adapter": (off.get("model") or {}).get("adapter"),
                "source": _METRICS_SRC_OFFLINE,
                "unavailable": not bool(off),
            },
            "external_distribution": {
                "label": "③ 真外部分布",
                "status": "未采集",
                "why": "尚无真外部同分布卷读数;留存 B 卷请求面按改名表归一后与训练面同形",
                "source": None,
            },
        },
        "small_sample_weak": {"items": weak, "source": _METRICS_SRC_OFFLINE,
                              "note": "样本量极小,仅为如实披露"},
        "dataset": {"train_n": stats.get("train_n"), "holdout_n": stats.get("holdout_n"),
                    "families": stats.get("families"),
                    "b_family_overlap": stats.get("b_family_overlap"),
                    "b_cores_known": stats.get("b_cores_known"),
                    "source": _METRICS_SRC_STATS},
    }


def _reconcile_orphan_runs(ws: pathlib.Path) -> list:
    """启动对账（T10-S6/D7）:进程已消失但 run 仍 queued/running 的，自动收尾。

    真机实测背景:强杀子进程后后端不会自己发现 ⇒ run 卡在 running、排他锁不释放、
    新建任务 409（整场演示会被卡住）。这里在服务启动时做一次对账:
      * 只有**确认进程已消失**（僵尸也算死）且**能拿到锁**的 run 才动手;
      * 语义与 POST /recover 完全一致（恢复 canonical → 写 interrupted，失败写 failed）;
      * 拿不到锁则跳过并如实记一行，不抢跑。
    """
    out = []
    runs_dir = ws / "runs"
    if not runs_dir.is_dir():
        return out
    from engine import run_lock as _rl
    for rd in sorted(runs_dir.glob("run-*")):
        snap = _read_json(rd / "run.json", None)
        if not isinstance(snap, dict):
            continue
        if str(snap.get("state") or "") not in ("queued", "running"):
            continue
        try:
            pid_int = int(snap.get("pid") or 0)
        except (TypeError, ValueError):
            pid_int = 0
        if pid_int and _rl._pid_alive(pid_int):
            out.append({"run_id": rd.name, "action": "skip",
                        "detail": f"pid {pid_int} still alive"})
            continue
        lk = _rl.RunLock(_rl.default_lock_path(), identity=_rl.DEFAULT_IDENTITY)
        if not lk.acquire(timeout_s=1.0):
            out.append({"run_id": rd.name, "action": "skip",
                        "detail": "target lock held by another writer"})
            continue
        try:
            res = _restore_canonical_target("startup-reconcile:orphan-run")
            cleanup = "restored" if res.get("ok") else "failed"
            _record_cancel_outcome(rd.name, cleanup, "startup_reconcile_orphan",
                                   f"pid={pid_int or 'unknown'} gone; restore={res.get('detail')}",
                                   "interrupted" if res.get("ok") else "failed")
            out.append({"run_id": rd.name, "action": "recovered", "cleanup": cleanup})
        finally:
            lk.release()
    return out


@app.on_event("startup")
def _on_startup_reconcile():
    """服务启动做一次孤儿 run 对账；AEGIS_RECONCILE=0 可关闭（测试/离线环境）。"""
    if os.environ.get("AEGIS_RECONCILE", "1") == "0":
        return
    try:
        for row in _reconcile_orphan_runs(pathlib.Path(WS)):
            print(f"[startup-reconcile] {row}", flush=True)
    except Exception as e:  # noqa: BLE001 —— 对账失败不得阻断服务启动
        print(f"[startup-reconcile] failed: {type(e).__name__}: {e}", flush=True)


@app.post("/api/demo/runs/{run_id}/recover")
def demo_recover(run_id: str):
    """受控恢复（T10-S6/D8）:任务进程已消失但 run 仍 queued/running 时的**唯一人工入口**。

    来历(真机实测):在 patch 阶段 SIGKILL 子进程后,子进程成僵尸,
    旧存活探测把僵尸当活 ⇒ run 永远 running、锁不释放、新建任务 409,
    且没有任何接口可以收尾（只能人工改 JSON）。

    纪律:
      * 进程仍存活 → 409 拒绝（不允许两个写者）;
      * 拿不到排他锁 → 409 拒绝（不抢跑）;
      * 确认进程已消失**且**持锁后,才恢复 canonical 并写终态 interrupted;
      * 恢复失败一律写 failed,并保留双重原因（原始原因 + 恢复结果）。
    """
    snap = _read_json(WS / "runs" / run_id / "run.json", None)
    if not isinstance(snap, dict):
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")
    state = str(snap.get("state") or "")
    if state not in ("queued", "running"):
        return {"run_id": run_id, "state": state, "recovered": False,
                "detail": "already terminal; nothing to recover"}
    try:
        pid_int = int(snap.get("pid") or 0)
    except (TypeError, ValueError):
        pid_int = 0
    from engine import run_lock as _rl          # 惰性导入:main.py 其余处不依赖该模块
    if pid_int and _rl._pid_alive(pid_int):
        raise HTTPException(status_code=409,
                            detail=f"task process still alive (pid={pid_int}); refuse to recover")
    lk = _rl.RunLock(_rl.default_lock_path(), identity=_rl.DEFAULT_IDENTITY)
    if not lk.acquire(timeout_s=_cancel_lock_wait_s()):
        raise HTTPException(status_code=409,
                            detail="target lock held by another writer; refuse to recover")
    try:
        res = _restore_canonical_target("recover:task-process-gone")
        cleanup = "restored" if res.get("ok") else "failed"
        detail = ("recover: pid=%s gone; restore=%s; 目标未因此轮改动则 canonical 保持纯净"
                  % (pid_int or "unknown", res.get("detail")))
        _record_cancel_outcome(run_id, cleanup, "recovered_after_process_gone", detail,
                               "interrupted" if res.get("ok") else "failed")
    finally:
        lk.release()
    snap2 = _read_json(WS / "runs" / run_id / "run.json", None) or {}
    return {"run_id": run_id, "recovered": True, "state": snap2.get("state"),
            "cleanup": snap2.get("cleanup"), "restore": res, "detail": detail}


@app.get("/api/demo/models")
def demo_models():
    """模型 manifest（T5 工包产出 bench/results/demo_models.json）。
    缺记录返回 {"models": [], "status": "unavailable"}，不返回虚构性能。
    T10-S3(UI):附带 metrics_three_layers,供页面"三层指标"展示(服务端读文件)。"""
    p = ROOT / "bench" / "results" / "demo_models.json"
    data = _read_json(p, None)
    if not isinstance(data, dict) or "models" not in data:
        return {"models": [], "status": "unavailable"}
    data["metrics_three_layers"] = _three_layer_metrics()
    return data

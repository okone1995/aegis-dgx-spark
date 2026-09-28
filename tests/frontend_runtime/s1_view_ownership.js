/* Aegis 演示大屏 · S1(页面状态所有权)回归断言
 *
 * 来历(GPT-6 整改单 S1/P0):浏览器里真回放**失败轮** run-20260926-151849-943c 并等到
 * 播放结束后,事件流显示 failed,但顶栏/摘要被「最近成功轮」run-20260926-152228-24de 覆盖。
 * 根因:rpDone() 回到裸 idle 后,scanRuns() 又 loadStatic() 最新 run 重画了顶栏。
 * 现有 46 条 node vm 断言没覆盖这个**异步时序**(见 a08_a14.js)。
 *
 * 本文件把 S1 的五条要求钉成可复现的断言:
 *   ① 四种页面视角(live follow / 回放进行中 / 回放已结束冻结 / 仅看最近 run 背景)互相排斥
 *   ② rpDone 后画面冻结:身份/相位/summary/门禁/尾帧不被 scanRuns 改写,且「退出回放」仍可达
 *   ③ 所有异步回调(loadStatic/loadModels/pollSnap/scanRuns)落 DOM 前校验「当前视角 + 目标 run_id」
 *   ④ 首屏必须明确「待命;最近运行仅作背景信息」,或给一张完整收据 —— 不得让「历史成功」与
 *      本轮红蓝/判官全 0 同屏无解释
 *   ⑤ 终态色/标题/原因从选中 run 的服务端 state 与事件得出;failed/partial/cancelled/
 *      interrupted/cleanup failed 分开呈现;回放中不得复用其他 run 的 summary/风险/模型身份
 *
 * 定性:与 a08_a14.js 同口径 —— 桩 DOM + 桩 fetch 里**真实执行 demo/index.html 的前端函数体**,
 * 不是浏览器渲染测试(不验布局/像素/真事件循环)。
 *
 * 用法:
 *   node tests/frontend_runtime/s1_view_ownership.js           # 桩数据场景(离线可跑)
 *   node tests/frontend_runtime/s1_view_ownership.js --live    # 追加真数据场景(GET 127.0.0.1:8822)
 */
"use strict";
const { createEnv } = require("./dom_env");

let pass = 0, fail = 0;
function chk(name, cond, extra) {
  if (cond) { pass++; console.log("PASS  " + name); }
  else { fail++; console.log("FAIL  " + name + (extra == null ? "" : "  ::  " + extra)); }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
const txtOf = (env, id) => String((env.byId[id] && env.byId[id].textContent) || "").replace(/\s+/g, " ").trim();
const clsOf = (env, id) => String((env.byId[id] && env.byId[id].className) || "");
/* 新增内部量在前端修复前不存在 —— 一律用 typeof 守卫,保证「修复前」也能跑出 RED 而不是崩在 ReferenceError */
const modeOf = env => env.state().mode;
const viewOf = env => env.evalIn("typeof _view==='undefined'?'(未实现)':String(_view)");
const hintOf = env => env.evalIn("typeof _frozenHint==='undefined'?'(未实现)':String(_frozenHint)");
/* 事件流里的终局横幅(与 _live_render.mjs 同读法:桩 DOM 下 appendChild 不写 innerHTML) */
const banners = env => ((env.byId["feed"] && env.byId["feed"].children_) || [])
  .filter(c => String(c.className || "").indexOf("finbanner") >= 0)
  .map(c => "." + String(c.className).replace("finbanner", "").trim() + " " +
            String(c.textContent || "").replace(/\s+/g, " ").trim());
function stageOf(env) {
  const h = String((env.byId["stagebar"] && env.byId["stagebar"].innerHTML) || "");
  const all = Array.from(h.matchAll(/<div class="sstage([^"]*)">([^<]+)<\/div>/g));
  return {
    cur: all.filter(m => m[1].indexOf("cur") >= 0).map(m => m[2]).join(",") || "—",
    done: all.filter(m => m[1].indexOf("done") >= 0).length,
  };
}
/* 当前「画面帧」的可观测身份 —— 顶栏 + 身份 + 摘要 + 门禁 + 相位归属 */
function frame(env) {
  return {
    phase: txtOf(env, "phase"), run: txtOf(env, "rm-run"), case: txtOf(env, "rm-case"),
    models: txtOf(env, "rm-models"), clean: txtOf(env, "rm-clean"), wall: txtOf(env, "t-wall"),
    gates: txtOf(env, "gates"), rv: txtOf(env, "rv"), convWall: txtOf(env, "cv-wall"),
    viewbar: txtOf(env, "viewbar"),
  };
}
const frameBrief = f => JSON.stringify({ phase: f.phase, run: f.run, models: f.models, wall: f.wall, gates: f.gates.slice(0, 46) });

/* ══════════ 夹具 ══════════ */
function ev(runId, seq, type, stage, payload, summary, status) {
  return {
    schema_version: 1, run_id: runId, seq, event_id: runId + ":" + seq,
    ts: "2026-09-26T12:00:0" + Math.min(9, seq) + ".000Z", type, stage: stage || "patch",
    actor: "engine", finding_id: "F-001", flow_id: null, caused_by: null,
    status: status || "ok", summary: summary || (type + " summary"), payload: payload || {},
    artifact_refs: [],
  };
}
function mkSnap(runId, state, o) {
  o = o || {};
  return Object.assign({
    run_id: runId, state, case_id: "sqli", stage: "cleanup",
    judge_status: state === "succeeded" ? "ok" : "not_invoked",
    repair_outcome: state === "succeeded" ? "verified" : null,
    model_versions: { patch: "qwen", review: "step", judge: state === "succeeded" ? "judge_v3_adapter" : "not_invoked" },
    providers: { patch: "qwen", review: "step" },
    cleanup: o.cleanup || "restored",
    summary: state + ": repair=" + (state === "succeeded" ? "verified" : "None") +
             " judge=" + (state === "succeeded" ? "ok" : "not_invoked") + " learn=not_run cleanup=restored flows=0",
    started_at: "2026-09-26T11:55:32.000Z", ended_at: "2026-09-26T11:56:50.000Z",
    created_at: o.created_at || "2026-09-26T11:55:32.000Z",
  }, o.extra || {});
}
/* 真实 943c 的形状:4 条事件,终局是 run.failed + payload.error */
const FAIL_EVENTS = [
  ev("run-FAIL", 1, "run.created", "preflight", {}, "run created for case sqli"),
  ev("run-FAIL", 2, "stage.started", "preflight", {}, "预检 开始"),
  ev("run-FAIL", 3, "cleanup.completed", "cleanup", { status: "restored" }, "cleanup restored", "ok"),
  ev("run-FAIL", 4, "run.failed", "cleanup",
     { error: "preflight_php_missing: PHP_BIN unset & php not on PATH", stage: "preflight" },
     "failed: repair=None judge=not_invoked learn=not_run cleanup=restored flows=0", "failed"),
];
const NEW_RUN = { run_id: "run-NEW", state: "succeeded", created_at: "2026-09-26T15:22:28.972Z" };

/* 与 a08_a14.js 同风格的桩 router;多一个按 URL 延迟的钩子(用于造「晚到响应」) */
function mkEnv(cfg) {
  const seen = [];
  const router = (rawUrl, method) => {
    const url = String(rawUrl);
    method = method || "GET";
    seen.push(method + " " + url);
    const d = (cfg.delay && cfg.delay(url, method)) || 0;
    const R = o => (d > 0 ? Object.assign({}, o, { delayMs: d }) : o);
    if (method === "POST" && url.indexOf("/api/demo/runs") === 0) {
      return cfg.post || { status: 202, body: { run_id: cfg.postRunId, state: "queued", last_seq: 0, created: true } };
    }
    let m = url.match(/\/api\/demo\/runs\/([^/?]+)\/events/);
    if (m) {
      const rid = decodeURIComponent(m[1]);
      const after = parseInt((url.match(/after=(\d+)/) || [])[1] || "0", 10);
      const all = (cfg.events && cfg.events[rid]) || [];
      const page = all.filter(e => Number(e.seq) > after);
      return R({ status: 200, body: { events: page, next_seq: page.length ? page[page.length - 1].seq : after,
                                     has_more: false, state: (cfg.state && cfg.state[rid]) || "running" } });
    }
    m = url.match(/\/api\/demo\/runs\/([^/?]+)$/);
    if (m) {
      const rid = decodeURIComponent(m[1]);
      const s = cfg.snaps && cfg.snaps[rid];
      if (!s) return { status: 404, body: { detail: "no such run" } };
      return R({ status: 200, body: { run: s, last_seq: 0, artifacts: [] } });
    }
    if (url.indexOf("/api/demo/runs") === 0) {
      const runs = typeof cfg.runs === "function" ? cfg.runs() : (cfg.runs || []);
      return R({ status: 200, body: { runs: runs } });
    }
    if (url.indexOf("/api/demo/models") === 0) return R({ status: 200, body: cfg.models || { models: [], status: "ok" } });
    return R({ status: 200, body: {} });
  };
  const env = createEnv({ router });
  env.seen_ = seen;
  return env;
}
/* 基准夹具:boot 背景 = run-NEW(成功轮,更晚),被回放的历史轮 = run-FAIL(失败轮)。
   这正是缺陷现场:屏幕先被最新成功轮当背景,再切进失败轮回放。 */
function baseCfg(extra) {
  return Object.assign({
    runs: [NEW_RUN],
    snaps: { "run-NEW": mkSnap("run-NEW", "succeeded"), "run-FAIL": mkSnap("run-FAIL", "failed") },
    events: { "run-FAIL": FAIL_EVENTS, "run-NEW": [] },
    state: { "run-FAIL": "failed", "run-NEW": "succeeded" },
    models: { models: [{ model_id: "judge_v3_adapter", role: "judge", version: "judge_v3_adapter" }], status: "ok" },
  }, extra || {});
}
async function driveReplayToDone(env, guard) {
  for (let i = 0; i < (guard || 40) && env.state().mode === "replay"; i++) {
    await env.evalIn("_rpTick()");
    await env.settle();
  }
}

/* ══════════ A. 回放结束即冻结:两次 idle 扫描不得改写顶栏/相位/summary(缺陷复现主场景) ══════════ */
async function scenarioA_freezeAfterReplay() {
  const env = mkEnv(baseCfg());
  await env.settle();
  chk("S1-A 前置:boot 把最新 run 当静态背景(缺陷现场)",
      txtOf(env, "rm-run") === "run-NEW", txtOf(env, "rm-run"));

  await env.evalIn("pickRun('run-FAIL')");
  await env.settle();
  await driveReplayToDone(env);
  const tail = frame(env);
  console.log("      · 回放尾帧(冻结前) " + frameBrief(tail));

  chk("S1-A 尾帧相位属被回放的失败轮", /FAILED/.test(tail.phase) && /回放/.test(tail.phase), tail.phase);
  chk("S1-A 尾帧身份属被回放的失败轮", tail.run === "run-FAIL", tail.run);
  chk("S1-A 尾帧摘要属被回放的失败轮(门禁面板原样登该轮 summary)",
      tail.gates.indexOf("failed:") >= 0 && tail.gates.indexOf("succeeded:") < 0, tail.gates.slice(0, 90));
  chk("S1-A 回放已结束:回放时钟停,回 idle", modeOf(env) === "idle", modeOf(env));
  chk("S1-A 回放结束后「退出回放」仍可达(回放条保持可见,否则冻结成死路)",
      clsOf(env, "replaybar").indexOf("show") >= 0, "replaybar=\"" + clsOf(env, "replaybar") + "\"");
  chk("S1-A 回放结束进入冻结视角(frozen),而不是可被扫描覆盖的裸 idle",
      viewOf(env) === "frozen", "视角=" + viewOf(env));

  const h = hintOf(env);
  for (let k = 1; k <= 2; k++) {
    await env.evalIn("scanRuns()");
    await env.settle();
    const now = frame(env);
    chk("S1-A idle 扫描 #" + k + ":顶栏/相位/身份/summary/门禁 全部保持冻结",
        now.phase === tail.phase && now.run === tail.run && now.models === tail.models &&
        now.wall === tail.wall && now.gates === tail.gates && now.rv === tail.rv,
        "冻结前 " + frameBrief(tail) + " → 扫描后 " + frameBrief(now));
  }
  chk("S1-A 扫描期间零网络写请求", env.writes().length === 0, JSON.stringify(env.writes()));
  chk("S1-A 提示「有新 LIVE 可查看」而不改写画面(hint 提到更新的 run)",
      String(hintOf(env)).indexOf("run-NEW") >= 0, JSON.stringify(hintOf(env)));
  chk("S1-A 视角条如实声明「回放已结束(冻结)」", txtOf(env, "viewbar").indexOf("冻结") >= 0,
      txtOf(env, "viewbar").slice(0, 120));

  /* C. 退出回放 = 唯一放行口:释放冻结后背景才允许被重画 */
  await env.evalIn("stopReplay()");
  await env.settle();
  await env.evalIn("scanRuns()");
  await env.settle();
  const after = frame(env);
  chk("S1-C 退出回放后释放冻结,背景回到最新 run(run-NEW)",
      after.run === "run-NEW" && after.phase.indexOf("背景") >= 0, frameBrief(after));
  chk("S1-C 退出回放后视角声明回到「仅看最近 run(背景)」",
      viewOf(env) === "ambient", "视角=" + viewOf(env));
  await env.settle();
  console.log("      · 退出回放后 " + frameBrief(after) + "  viewbar=" + after.viewbar.slice(0, 90));
}

/* ══════════ B. 异步乱序:先发出的 loadStatic 晚于 pickRun 返回,不得污染当前画面 ══════════ */
async function scenarioB_lateStaticVsReplay() {
  const order = {};
  const cfg = baseCfg();
  cfg.delay = url => (/\/api\/demo\/runs\/run-NEW$/.test(url) ? 600 : 0);
  const env = mkEnv(cfg);
  /* 不 settle:让 boot 的 loadStatic(run-NEW) 卡在延迟里,模拟「背景响应还在路上」 */
  await sleep(30);
  chk("S1-B 前置:背景响应仍在途中(_snap 未落)", env.evalIn("_snap===null") === true,
      String(env.evalIn("_snap && _snap.run_id")));
  await env.evalIn("pickRun('run-FAIL')");
  await env.settle();
  chk("S1-B 前置:已进入回放(晚到响应将落在回放进行中)", modeOf(env) === "replay", modeOf(env));
  const during = frame(env);
  /* 让那份晚到的背景响应落地;落地时点必须仍是 replay,否则这条测试没测到东西 */
  order.at = "未观测";
  await sleep(900);
  order.at = (() => { try { return String(env.evalIn("_mode")); } catch (e) { return "(err)"; } })();
  await env.settle();
  const now = frame(env);
  chk("S1-B 时序成立:晚到的背景响应落地时页面正处在回放中",
      order.at === "replay", "落地时 mode=" + order.at);
  chk("S1-B 晚到的 loadStatic 不得把 A 轮(run-NEW 背景)覆成 B 轮(run-FAIL 回放)",
      now.run === during.run && now.phase === during.phase && now.models === during.models,
      "回放中 " + frameBrief(during) + " → 晚到后 " + frameBrief(now));
  chk("S1-B 晚到响应后身份仍是 run-FAIL", now.run === "run-FAIL", now.run);
  chk("S1-B 晚到响应后相位仍是回放中", now.phase.indexOf("回放中") >= 0, now.phase);
}

/* ══════════ D. LIVE 跨轮晚到:pollSnap/followRun 的旧响应不得覆写新一轮 ══════════ */
async function scenarioD_lateLiveSnapshotVsNewRun() {
  const cfg = {
    postRunId: "run-A",
    runs: [],
    snaps: { "run-A": mkSnap("run-A", "running"), "run-B": mkSnap("run-B", "running") },
    events: { "run-A": [], "run-B": [] },
    state: { "run-A": "running", "run-B": "running" },
    delay: url => (/\/api\/demo\/runs\/run-A$/.test(url) ? 600 : 0),
  };
  const env = mkEnv(cfg);
  await env.settle();
  env.evalIn("followRun('run-A')");            // 不 await:快照拉取被延迟
  await sleep(30);
  env.evalIn("followRun('run-B')");            // 立刻切到另一轮
  await env.settle();
  await sleep(900);                            // run-A 的晚到快照落地
  await env.settle();
  const f = frame(env);
  chk("S1-D 切轮后身份为 run-B", f.run === "run-B", f.run);
  chk("S1-D 上一轮的晚到快照不得覆写新一轮身份", env.evalIn("String(_snap&&_snap.run_id)") === "run-B",
      String(env.evalIn("String(_snap&&_snap.run_id)")));
  chk("S1-D 上一轮的晚到续体不得重复开流(SSE 计数 1)",
      env.sources.length === 1, "EventSource 实例数=" + env.sources.length);
  chk("S1-D 上一轮的晚到续体不得重复注册轮询定时器(2s+2.5s+1s 各一)",
      env.timers.filter(t => t.kind === "interval" && [1000, 2000, 2500].indexOf(t.ms) >= 0).length === 3,
      JSON.stringify(env.timers.map(t => t.kind + ":" + t.ms)));
}

/* ══════════ E. 首屏:不得让「历史成功」与本轮红蓝/判官全 0 同屏无解释 ══════════ */
async function scenarioE_firstScreenAmbiguity() {
  const env = mkEnv(baseCfg());
  await env.settle();
  const f = frame(env);
  const bar = f.viewbar;
  console.log("      · 首屏 " + frameBrief(f));
  console.log("      · 首屏视角条 " + JSON.stringify(bar));
  chk("S1-E 首屏必须有一张「视角声明」(交代这块画面是什么视角)",
      bar.length > 0, "viewbar=\"" + bar + "\"");
  chk("S1-E 首屏声明该 run 是「背景」且点名 run_id", bar.indexOf("背景") >= 0 && bar.indexOf("run-NEW") >= 0, bar);
  chk("S1-E 首屏解释「本轮红蓝/判官/门禁为 0」的原因(未播放该轮事件)",
      bar.indexOf("未播放") >= 0, bar);
  chk("S1-E 相位芯片不得把背景轮读成「本轮成功」(须带背景/历史标注)",
      f.phase.indexOf("背景") >= 0 || f.phase.indexOf("历史") >= 0, f.phase);
  chk("S1-E 背景轮的红蓝计数确为 0(所以上面那句解释是必要的,不是装饰)",
      txtOf(env, "t-shots") === "0" && txtOf(env, "t-block") === "0", txtOf(env, "t-shots") + "/" + txtOf(env, "t-block"));
  chk("S1-E 判官面板状态不得让背景轮的 0 条裁决看起来像本轮结论",
      txtOf(env, "j-n") === "0", txtOf(env, "j-n"));
}

/* ══════════ F. 终态分开呈现:failed / partial / cancelled / interrupted / cleanup failed ══════════ */
const TERM_EV = { failed: "run.failed", partial: "run.partial", cancelled: "run.cancelled", succeeded: "run.finished" };
function termRunCfg(state, opts) {
  opts = opts || {};
  const rid = "run-" + state.toUpperCase();
  const evs = [ev(rid, 1, "stage.started", "preflight", {}, "S1MARK")];
  let n = 1;
  if (opts.cleanupFailed) evs.push(ev(rid, ++n, "cleanup.completed", "cleanup",
    { status: "failed", error: "restore timeout" }, "cleanup failed", "failed"));
  if (!opts.noTermEvent) {
    const p = { state: state };
    if (state === "failed") p.error = "preflight_php_missing: PHP_BIN unset";
    if (state === "partial") p.degraded_reasons = ["judge_status=unavailable"];
    evs.push(ev(rid, ++n, TERM_EV[state] || "run.finished", "cleanup", p,
      state === "partial" ? "succeeded: repair=verified judge=unavailable learn=queued cleanup=restored flows=9 | degraded: judge_status=unavailable"
                          : state + ": repair=None judge=not_invoked learn=not_run cleanup=restored flows=0",
      state));
  }
  return {
    runId: rid,
    cfg: {
      runs: [NEW_RUN],
      snaps: { "run-NEW": mkSnap("run-NEW", "succeeded"),
               [rid]: mkSnap(rid, state, { cleanup: opts.cleanupFailed ? "failed" : "restored",
                                           extra: { error: state === "failed" ? "preflight_php_missing: PHP_BIN unset" : null } }) },
      events: { [rid]: evs, "run-NEW": [] },
      state: { [rid]: state, "run-NEW": "succeeded" },
    },
  };
}
async function playOne(rid, cfg) {
  const env = mkEnv(cfg);
  await env.settle();
  await env.evalIn("pickRun('" + rid + "')");
  await env.settle();
  await driveReplayToDone(env);
  return env;
}
async function scenarioF_terminalStatesSeparated() {
  const seen = {};
  for (const st of ["failed", "partial", "cancelled"]) {
    const { runId, cfg } = termRunCfg(st);
    const env = await playOne(runId, cfg);
    const f = frame(env);
    const bn = banners(env);
    seen[st] = { phase: f.phase, cls: clsOf(env, "phase"), banner: bn.join(" | ") };
    chk("S1-F " + st + " 相位由该轮终态得出(含 " + (st === "cancelled" ? "CANCELLED" : st.toUpperCase()) + ")",
        f.phase.indexOf(st === "cancelled" ? "CANCELLED" : st.toUpperCase()) >= 0, f.phase);
    chk("S1-F " + st + " 终局横幅存在且不宣称收敛成功", bn.length > 0 && !/收敛成功/.test(bn.join(" ")), JSON.stringify(bn));
    if (st === "failed") {
      chk("S1-F failed 相位为红(bad)", clsOf(env, "phase").indexOf("bad") >= 0, clsOf(env, "phase"));
      chk("S1-F failed 横幅带 payload.error 根因", bn.join(" ").indexOf("preflight_php_missing") >= 0, JSON.stringify(bn));
    }
    if (st === "partial") {
      chk("S1-F partial 相位为黄(warn),与 failed 分开", clsOf(env, "phase").indexOf("warn") >= 0, clsOf(env, "phase"));
      chk("S1-F partial 横幅低于成功档并如实带 degraded 原因",
          bn.join(" ").indexOf("部分完成") >= 0, JSON.stringify(bn));
    }
    if (st === "cancelled") {
      chk("S1-F cancelled 文案与 failed 明确不同(不得混为一色一句)",
          seen.cancelled.phase !== seen.failed.phase && seen.cancelled.banner !== seen.failed.banner,
          "cancelled=" + JSON.stringify(seen.cancelled) + " failed=" + JSON.stringify(seen.failed));
      chk("S1-F cancelled 如实说「已取消」", seen.cancelled.banner.indexOf("取消") >= 0 || seen.cancelled.phase.indexOf("取消") >= 0,
          JSON.stringify(seen.cancelled));
    }
  }
  /* interrupted:引擎不发明局事件(见 demo_contracts.VALID_EVENTS),只能由服务端 state 得出 */
  {
    const { runId, cfg } = termRunCfg("interrupted", { noTermEvent: true });
    const env = await playOne(runId, cfg);
    const f = frame(env);
    chk("S1-F interrupted 无终局事件时,终态由服务端 state 得出(相位含 INTERRUPTED)",
        f.phase.indexOf("INTERRUPTED") >= 0, f.phase);
    chk("S1-F interrupted 相位为红(bad),不与成功/部分完成同色",
        clsOf(env, "phase").indexOf("bad") >= 0 && clsOf(env, "phase").indexOf("done") < 0, clsOf(env, "phase"));
    chk("S1-F interrupted 有终局横幅", banners(env).length > 0, JSON.stringify(banners(env)));
  }
  /* cleanup failed:与中止原因分开呈现 */
  {
    const { runId, cfg } = termRunCfg("failed", { cleanupFailed: true });
    const env = await playOne(runId, cfg);
    const bn = banners(env);
    chk("S1-F cleanup failed 与中止原因分开呈现(两条横幅)",
        bn.length >= 2, JSON.stringify(bn));
    chk("S1-F cleanup failed 有独立横幅且带恢复失败原因",
        bn.join(" | ").indexOf("恢复失败") >= 0, JSON.stringify(bn));
    chk("S1-F cleanup failed 恢复状态芯片如实为「恢复失败」",
        txtOf(env, "rm-clean") === "恢复失败", txtOf(env, "rm-clean"));
  }
}

/* ══════════ G. 回放中不得复用其他 run 的模型身份/阶段/renderState ══════════ */
async function scenarioG_lateModelsDuringReplay() {
  const cfg = baseCfg();
  cfg.delay = url => (url.indexOf("/api/demo/models") === 0 ? 600 : 0);
  const env = mkEnv(cfg);
  await sleep(30);                       // boot 的 loadModels 卡在延迟里
  await env.evalIn("pickRun('run-FAIL')");
  await env.settle();
  await env.evalIn("_rpTick()");         // 推进到 #2(预检阶段)
  await env.settle();
  const during = { phase: txtOf(env, "phase"), run: txtOf(env, "rm-run"), stage: stageOf(env).cur };
  await sleep(900);                      // 晚到的 models 响应落地
  await env.settle();
  chk("S1-G 晚到 loadModels 后相位仍是回放中", txtOf(env, "phase") === during.phase, during.phase + " → " + txtOf(env, "phase"));
  chk("S1-G 晚到 loadModels 后身份仍是被回放 run(不得套上最新 run 的模型身份)",
      txtOf(env, "rm-run") === during.run && txtOf(env, "rm-run") === "run-FAIL", txtOf(env, "rm-run"));
  chk("S1-G 晚到 loadModels 不得把阶段条按终态快照整屏改写",
      stageOf(env).cur === during.stage, "回放中 cur=" + during.stage + " → 晚到后 cur=" + stageOf(env).cur +
      " done=" + stageOf(env).done);
  chk("S1-G 晚到 loadModels 后回放仍在推进(未被切走)", modeOf(env) === "replay", modeOf(env));
}

/* ══════════ L. 真数据场景(GET 127.0.0.1:8822,验证 ②) ══════════ */
async function scenarioLiveRealData() {
  const BASE = process.env.AEGIS_BASE || "http://127.0.0.1:8822";
  const argv = process.argv.slice(2);
  const want = (argv[argv.indexOf("--run") + 1] && argv.indexOf("--run") >= 0) ? argv[argv.indexOf("--run") + 1] : null;
  const GET = async p => {
    const r = await fetch(BASE + p);
    if (!r.ok) throw new Error("GET " + p + " → HTTP " + r.status);
    return r.json();
  };
  let runs, models;
  try {
    runs = ((await GET("/api/demo/runs")).runs || []);
    models = await GET("/api/demo/models");
  } catch (e) {
    console.log("SKIP  真数据场景:本机 " + BASE + " 不可达(" + e.message + ")");
    return;
  }
  runs.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  const newest = runs[0] && runs[0].run_id;
  const target = want || ((runs.find(r => r.state === "failed") || runs.find(r => r.state === "partial") || runs[0] || {}).run_id);
  if (!target) { console.log("SKIP  真数据场景:后端没有 run"); return; }
  const snaps = {};
  const events = {};
  for (const id of Array.from(new Set([target, newest]))) snaps[id] = (await GET("/api/demo/runs/" + encodeURIComponent(id))).run;
  events[target] = [];
  for (let after = 0, guard = 0; guard < 500; guard++) {
    const page = ((await GET("/api/demo/runs/" + encodeURIComponent(target) + "/events?after=" + after + "&limit=200")).events) || [];
    if (!page.length) break;
    events[target] = events[target].concat(page);
    after = Number(page[page.length - 1].seq);
    if (page.length < 200) break;
  }
  const misses = [];
  const env = createEnv({
    router: (u, m) => {
      const url = new URL(String(u), BASE);
      if (m !== "GET") { misses.push("NONGET " + m + " " + u); return { status: 405, body: {} }; }
      if (url.pathname === "/api/demo/runs") return { status: 200, body: { runs: runs } };
      if (url.pathname === "/api/demo/models") return { status: 200, body: models };
      let mm = url.pathname.match(/^\/api\/demo\/runs\/([^/]+)\/events$/);
      if (mm) {
        const id = decodeURIComponent(mm[1]);
        if (!events[id]) { misses.push("MISS " + u); throw new Error("router miss " + u); }
        const after = Number(url.searchParams.get("after") || 0);
        const page = events[id].filter(e => Number(e.seq) > after);
        return { status: 200, body: { events: page, next_seq: page.length ? Number(page[page.length - 1].seq) : after, has_more: false, state: String((snaps[id] || {}).state || "") } };
      }
      mm = url.pathname.match(/^\/api\/demo\/runs\/([^/]+)$/);
      if (mm) {
        const id = decodeURIComponent(mm[1]);
        if (!snaps[id]) { misses.push("MISS " + u); throw new Error("router miss " + u); }
        return { status: 200, body: { run: snaps[id], last_seq: 0, artifacts: [] } };
      }
      misses.push("MISS " + u);
      throw new Error("router miss " + u);
    },
  });
  await env.settle();
  const snapOf = id => {
    const s = snaps[id] || {};
    return { state: String(s.state || ""), summary: String(s.summary || "") };
  };
  await env.evalIn("pickRun('" + target + "')");
  await env.settle();
  await driveReplayToDone(env);
  const tail = frame(env);
  console.log("      · 真数据尾帧(" + target + ") " + frameBrief(tail));
  console.log("      · 该轮服务端快照 state=" + snapOf(target).state + " summary=" + JSON.stringify(snapOf(target).summary));
  console.log("      · 最新 run(静态背景来源)=" + newest + " state=" + snapOf(newest).state);

  chk("S1-L 真数据:回放尾帧身份 = 所选 run", tail.run === target, tail.run);
  chk("S1-L 真数据:尾帧门禁面板登的是该 run 自己的 summary",
      tail.gates.indexOf(snapOf(target).summary.slice(0, 24)) >= 0, tail.gates.slice(0, 110));
  for (let k = 1; k <= 2; k++) {
    await env.tickAll("interval");       // = 页面的 3s idle 周期扫描
    await env.settle();
    const now = frame(env);
    chk("S1-L 真数据:第 " + k + " 次 idle 扫描后顶栏/相位/summary 仍指向 " + target,
        now.run === target && now.phase === tail.phase &&
        now.models === tail.models && now.wall === tail.wall &&
        now.gates === tail.gates && now.gates.indexOf("succeeded:") < 0,
        frameBrief(now));
  }
  chk("S1-L 真数据:最新 run 已被提示为「有新 LIVE 可查看」而非改写画面",
      txtOf(env, "viewbar").indexOf(newest) >= 0 || String(hintOf(env)).indexOf(newest) >= 0,
      JSON.stringify(txtOf(env, "viewbar") + " | " + hintOf(env)));
  chk("S1-L 真数据:全程零写请求、router 未命中 0 笔",
      env.writes().length === 0 && misses.length === 0, JSON.stringify(env.writes()) + " " + JSON.stringify(misses));
}

(async () => {
  await scenarioA_freezeAfterReplay();
  await scenarioB_lateStaticVsReplay();
  await scenarioD_lateLiveSnapshotVsNewRun();
  await scenarioE_firstScreenAmbiguity();
  await scenarioF_terminalStatesSeparated();
  await scenarioG_lateModelsDuringReplay();
  if (process.argv.indexOf("--live") >= 0) await scenarioLiveRealData();
  console.log("\n结果: " + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error("HARNESS ERROR:", (e && e.stack) || e); process.exit(2); });

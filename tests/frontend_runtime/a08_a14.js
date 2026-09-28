/* Aegis 演示大屏 · A08–A14 真运行态验收(计划书 §9)
 *
 * 每项对应计划书验收矩阵:
 *   A08 同页连续启动两轮   : 各有 run_id、无旧事件残留、游标重置、按钮恢复
 *   A09 外部启动后打开页面 : 无需点击 LIVE 也能跟随
 *   A10 断线/乱序/超 500   : 按 seq 补齐、不重复不倒退;断线显示重连中
 *   A12 双击/409/服务 500  : 不卡 RUNNING、错误明确
 *   A14 回放冻结/零写请求  : 回放不产生攻击或模型请求;新 LIVE 不污染回放
 *
 * 说明:这不是浏览器渲染测试(不验布局/像素),而是用桩 DOM+桩 fetch+桩 EventSource
 * **真实执行 demo/index.html 的前端函数体**,验数据层与状态机行为。
 */
"use strict";
const { createEnv } = require("./dom_env");

let pass = 0, fail = 0;
function chk(name, cond, extra) {
  if (cond) { pass++; console.log("PASS  " + name); }
  else { fail++; console.log("FAIL  " + name + (extra == null ? "" : "  :: " + extra)); }
}

function ev(runId, seq, type, stage, payload, summary) {
  return {
    schema_version: 1, run_id: runId, seq, event_id: runId + ":" + seq,
    ts: "2026-09-26T12:00:00.000Z", type, stage: stage || "patch",
    actor: "engine", finding_id: "F-001", flow_id: null, caused_by: null,
    status: "ok", summary: summary || (type + " summary"), payload: payload || {},
    artifact_refs: [],
  };
}

function routerFor(o) {
  o = o || {};
  return (url, method) => {
    if (method === "POST" && url.indexOf("/api/demo/runs") === 0) {
      if (o.postStatus && o.postStatus !== 202) {
        return { status: o.postStatus, body: { detail: o.postDetail || "busy" } };
      }
      return { status: 202, body: { run_id: o.runId, state: "queued", last_seq: 0, created: true } };
    }
    const evMatch = url.match(/\/api\/demo\/runs\/([^/?]+)\/events/);
    if (evMatch) {
      const rid = decodeURIComponent(evMatch[1]);
      const after = parseInt((url.match(/after=(\d+)/) || [])[1] || "0", 10);
      const all = (o.eventsByRun && o.eventsByRun[rid]) || [];
      const page = all.filter(e => e.seq > after).slice(0, o.pageSize || 500);
      const last = page.length ? page[page.length - 1].seq : after;
      return { status: 200, body: { events: page, next_seq: last,
                                   has_more: all.some(e => e.seq > last),
                                   state: (o.snapshotState && o.snapshotState[rid]) || "running" } };
    }
    const one = url.match(/\/api\/demo\/runs\/([^/?]+)$/);
    if (one) {
      const rid = decodeURIComponent(one[1]);
      const state = (o.snapshotState && o.snapshotState[rid]) || "running";
      const snap = Object.assign({ run_id: rid, state, last_seq: 0, cleanup: "pending" },
                                 (o.snapshots && o.snapshots[rid]) || {});
      return { status: 200, body: { run: snap, last_seq: snap.last_seq || 0, artifacts: [] } };
    }
    if (url.indexOf("/api/demo/runs") === 0) {
      return { status: 200, body: { runs: o.runs || [] } };
    }
    if (url.indexOf("/api/demo/models") === 0) return { status: 200, body: { models: [], status: "ok" } };
    if (url.indexOf("/api/state") === 0) return { status: 200, body: {} };
    return { status: 200, body: {} };
  };
}

async function scenarioA08() {
  const t1a = ev("run-T1", 1, "stage.started", "patch", {}, "T1ONLY-MARKER");
  const t1b = ev("run-T1", 2, "run.finished", "cleanup", { state: "succeeded" }, "T1END");
  const t2a = ev("run-T2", 1, "stage.started", "patch", {}, "T2ONLY-MARKER");
  const t2b = ev("run-T2", 2, "run.finished", "cleanup", { state: "succeeded" }, "T2END");
  const env = createEnv({
    router: routerFor({ runId: "run-T1", eventsByRun: { "run-T1": [t1a] } }),
  });
  await env.settle();

  await env.evalIn("startLive()");
  await env.settle();
  let st = env.state();
  chk("A08 第一轮进入 follow 且 run_id 正确",
      st.mode === "follow" && st.run === "run-T1", JSON.stringify(st));
  chk("A08 第一轮注册了 2s 轮询兑底",
      env.timers.some(t => t.kind === "interval" && t.ms === 2000));
  chk("A08 第一轮开了 SSE 流", env.sources.some(s => !s.closed),
      String(env.sources.length));

  const es1 = env.sources[env.sources.length - 1];
  es1.fire("demo", t1a);
  es1.fire("demo", t1b);
  await env.settle();
  st = env.state();
  chk("A08 终态由服务端事件落定并回 idle", st.mode === "idle", JSON.stringify(st));
  chk("A08 结束后按钮恢复可用", env.byId["btn-live"].disabled === false);

  // 第二轮:同一页再启动(换路由 = 另一个 run)
  env.setRouter(routerFor({ runId: "run-T2", eventsByRun: { "run-T2": [t2a] } }));
  await env.evalIn("startLive()");
  await env.settle();
  st = env.state();
  chk("A08 第二轮换了 run_id", st.run === "run-T2", JSON.stringify(st));
  // 真实时序:followRun 会先拉齐本轮历史。T1 共有 2 条事件而 T2 只有 1 条,
  // 因此 lastSeq=1 正好证明游标没沿用上一轮(T1)的 2。
  chk("A08 第二轮游标不沿用上一轮(T1 为 2,T2 为 1)", st.lastSeq === 1, String(st.lastSeq));

  const es2 = env.sources[env.sources.length - 1];
  es2.fire("demo", t1b);                  // 旧 run 的迟到事件(seq=2 > 当前游标 1)
  await env.settle();
  chk("A08 旧 run 的迟到事件被忽略(带 run_id 守卫)", env.state().lastSeq === 1,
      String(env.state().lastSeq));
  es2.fire("demo", t2b);                  // 新 run 的终态事件
  await env.settle();
  chk("A08 新 run 事件被接收并落终态",
      env.state().lastSeq === 2 && env.state().mode === "idle", JSON.stringify(env.state()));
  const feedText = String(env.byId["feed"] ? env.byId["feed"].textContent : "");
  chk("A08 事件流无旧 run 残留",
      feedText.indexOf("T1ONLY-MARKER") < 0 && feedText.indexOf("T1END") < 0
      && feedText.length > 0,
      JSON.stringify(feedText.slice(0, 160)));
}

async function scenarioA09() {
  const env = createEnv({
    router: routerFor({ runId: "run-EXT", eventsByRun: { "run-EXT": [] },
                        runs: [{ run_id: "run-EXT", state: "running" }] }),
  });
  await env.settle();
  await env.evalIn("scanRuns()");
  await env.settle();
  const st = env.state();
  chk("A09 外部启动的 run 被自动跟随(无需点 LIVE)",
      st.mode === "follow" && st.run === "run-EXT", JSON.stringify(st));
  const err = env.byId["errbar"] ? String(env.byId["errbar"].textContent) : "";
  chk("A09 无多余错误提示", err.indexOf("失败") < 0, JSON.stringify(err.slice(0, 60)));
}

async function scenarioA10() {
  // 第一阶段:流已开、历史为空 → 测乱序/重复/断线
  const many = [];
  for (let i = 1; i <= 620; i++) many.push(ev("run-L", i, "stage.started", "patch", {}, "SEQ" + i));
  many.push(ev("run-L", 621, "run.finished", "cleanup", { state: "succeeded" }, "SEQ621"));
  const env = createEnv({
    router: routerFor({ runId: "run-L", eventsByRun: { "run-L": [] }, pageSize: 500 }),
  });
  await env.settle();
  await env.evalIn("startLive()");
  await env.settle();
  const es = env.sources[env.sources.length - 1];
  chk("A10 流已建立", !!es && !es.closed);

  es.fire("demo", many[4]);   // seq 5
  es.fire("demo", many[2]);   // seq 3(迟到)
  es.fire("demo", many[3]);   // seq 4(迟到)
  es.fire("demo", many[4]);   // 重复 seq 5
  await env.settle();
  chk("A10 乱序→游标取最大且不倒退", env.state().lastSeq === 5, String(env.state().lastSeq));
  const feedHtml = env.byId["feed"] ? String(env.byId["feed"].innerHTML) : "";
  const dup = (feedHtml.match(/SEQ5[^0-9]/g) || []).length;
  chk("A10 重复 seq 不重复渲染", dup <= 1, "dup=" + dup);

  es.error();
  await env.settle();
  const conn = env.byId["conn"] ? String(env.byId["conn"].textContent) : "";
  chk("A10 SSE 断线显示重连状态", conn.indexOf("重连") >= 0, conn);

  // 第二阶段:分页拉齐(>500 条)
  env.setRouter(routerFor({ runId: "run-L", eventsByRun: { "run-L": many }, pageSize: 500 }));
  const before = env.state().lastSeq;
  await env.evalIn("pollEvents()");
  await env.settle();
  let after = env.state().lastSeq;
  chk("A10 单页上限内推进游标", after > before, before + "→" + after);
  for (let i = 0; i < 6 && after < 621; i++) {
    await env.evalIn("pollEvents()");
    await env.settle();
    after = env.state().lastSeq;
  }
  chk("A10 分页补齐至终态(>=621)", after >= 621, String(after));
  chk("A10 补齐后不卡 RUNNING(回 idle)", env.state().mode === "idle", env.state().mode);
}

async function scenarioA12() {
  for (const code of [409, 500]) {
    const env = createEnv({ router: routerFor({ runId: "run-X", postStatus: code }) });
    await env.settle();
    await env.evalIn("startLive()");
    await env.settle();
    const st = env.state();
    chk(`A12 POST ${code} 后不卡 RUNNING`, st.mode === "idle", JSON.stringify(st));
    chk(`A12 POST ${code} 后按钮可用`, env.byId["btn-live"].disabled === false);
    const err = env.byId["errbar"] ? String(env.byId["errbar"].textContent) : "";
    chk(`A12 POST ${code} 有可理解的错误提示`, err.length > 0, JSON.stringify(err.slice(0, 60)));
  }
}

async function scenarioA14() {
  // 回放要用足够长的事件序列,否则几拍就播完了(播完自然退 idle——那是正确行为)。
  // 注意:_mode=follow 时 pickRun 会**拒绝**回放(设计如此),所以这一场景
  // 先让页面处于 idle(运行清单为空),再进回放。
  const evs = [];
  for (let i = 1; i <= 8; i++) {
    evs.push(ev("run-R", i, "stage.started", "patch", {}, "RMARK" + i));
  }
  evs.push(ev("run-R", 9, "verification.completed", "verify",
              { blocked: true, business_pass: true, replay_hit: 0, replay_total: 2 },
              "RMARK9"));
  const env = createEnv({
    router: routerFor({ runId: "run-R", eventsByRun: { "run-R": evs },
                        snapshotState: { "run-R": "succeeded" }, runs: [] }),
  });
  await env.settle();
  await env.evalIn("pickRun('run-R')");
  await env.settle();
  chk("A14 进入回放模式", env.state().mode === "replay", env.state().mode);

  env.resetCalls();
  for (let i = 0; i < 2; i++) {
    await env.evalIn("_rpTick()");
    await env.settle();
  }
  chk("A14 回放期间零网络写请求", env.writes().length === 0,
      JSON.stringify(env.writes()));
  chk("A14 回放期间不发起攻击/模型请求",
      env.calls.every(c => c.method === "GET" && c.url.indexOf("/stream") < 0),
      JSON.stringify(env.calls.map(c => c.method + " " + c.url).slice(0, 4)));
  chk("A14 未播完不回 idle、也不先显终态", env.state().mode === "replay",
      env.state().mode);

  // 回放期间另有一轮 LIVE 开始:不得把回放切走
  env.setRouter(routerFor({ runId: "run-R", eventsByRun: { "run-R": evs },
                            snapshotState: { "run-R": "running" },
                            runs: [{ run_id: "run-OTHER", state: "running" }] }));
  await env.evalIn("scanRuns()");
  await env.settle();
  chk("A14 新 LIVE 不污染回放(仍停在 replay)", env.state().mode === "replay",
      env.state().mode);
  chk("A14 回放仍不急写请求", env.writes().length === 0, JSON.stringify(env.writes()));
}

async function scenarioA14b() {
  // LIVE 进行中不允许回放(否则会两个数据源同时画同一块屏)
  const env = createEnv({
    router: routerFor({ runId: "run-EXT", eventsByRun: { "run-EXT": [] },
                        runs: [{ run_id: "run-EXT", state: "running" }] }),
  });
  await env.settle();
  await env.evalIn("scanRuns()");
  await env.settle();
  chk("A14b LIVE 进行中处于 follow", env.state().mode === "follow", env.state().mode);
  await env.evalIn("pickRun('run-EXT')");
  await env.settle();
  chk("A14b LIVE 中点回放被拒绝(不切走数据源)", env.state().mode === "follow",
      env.state().mode);
  const err = env.byId["errbar"] ? String(env.byId["errbar"].textContent) : "";
  chk("A14b 拒绝时有可理解提示", err.indexOf("LIVE") >= 0 || err.indexOf("回放") >= 0,
      JSON.stringify(err.slice(0, 60)));
}

/* ══════════ D1–D7 回归断言(真前端 + 桩 DOM,与 docs/worklog/_live_render.mjs 同口径) ══════════
   来历:真数据回放勘察发现 D1(门禁证据被 verify 阶段重放检测覆盖)与 D4(回放结束文案
   硬编码「三门禁全过」,与同屏 FAIL 门禁矛盾)。这两条直接决定屏幕是否在说假话,必须有回归
   断言钉住;同场景顺带钉住 D3/D5/D6/D7 的可观测结果。 */
function gateCellsFromPanel(env) {
  const raw = String((env.byId["gates"] && env.byId["gates"].innerHTML) || "");
  const out = [], re = /data-gate="([^"]+)"[^>]*>([^<]*)<b>([^<]*)<\/b>/g;
  let m; while ((m = re.exec(raw))) out.push(m[1] + "=" + m[3].trim());
  return out.join(" ");
}
const txtOf = (env, id) => String((env.byId[id] && env.byId[id].textContent) || "").replace(/\s+/g, " ").trim();
async function driveReplay(env, n) {
  for (let i = 0; i < n; i++) { await env.evalIn("_rpTick()"); await env.settle(); }
}
const ALL_PASS_CLAIM = /每个补丁都过了|都过了|全部通过|全过/;

/* D1 + D4 正例 + D6:同一 finding 在门禁通过后,被 verify 阶段重放检测再次检出 */
async function scenarioD1GatesSurviveRedetection() {
  const det = { matched: true, class: "sqli", severity: "high", endpoint: "/news/search?q=x",
                alerts: 1, reasoning: "rule:SQ-001" };
  const evs = [
    ev("run-D1", 1, "detection.completed", "classify", det, "检出"),
    ev("run-D1", 2, "patch.proposed", "patch",
       { finding_id: "F-001", provider: "qwen", model: "qwen",
         diff: "--- \n+++ \n@@ -1,2 +1,2 @@\n-a1\n-a2\n-a3\n+b1\n",
         diff_sha256: "abcdef0123456789ffff", template: "fix.patch.tpl",
         artifact: "patches/F-001.candidate.php" }, "候选"),
    ev("run-D1", 3, "gate.completed", "review",
       { finding_id: "F-001", pass: true, reasons: [],
         gates: [{ gate: "diff_bounds", result: "PASS" }, { gate: "backdoor", result: "PASS" },
                 { gate: "llm_hetero", result: "PASS" }] }, "门禁通过"),
    ev("run-D1", 4, "detection.completed", "verify", det, "复检再次检出同一 fid"),
    ev("run-D1", 5, "verification.completed", "verify",
       { repair_outcome: "verified", blocked: true, business_pass: true,
         replay_hit: 0, replay_total: 2 }, "复验通过"),
    ev("run-D1", 6, "run.finished", "cleanup", { state: "succeeded" }, "D1END"),
  ];
  const env = createEnv({
    router: routerFor({ runId: "run-D1", eventsByRun: { "run-D1": evs },
                        snapshotState: { "run-D1": "succeeded" }, runs: [],
                        snapshots: { "run-D1": { state: "succeeded", cleanup: "restored",
                          started_at: "2026-09-26T12:00:00.000Z",
                          ended_at: "2026-09-26T12:01:32.000Z",
                          summary: "succeeded: repair=verified judge=ok learn=queued cleanup=restored flows=9" } } }),
  });
  await env.settle();
  await env.evalIn("pickRun('run-D1')");
  await env.settle();

  await driveReplay(env, 3);            // 播到 #4(重放检测):enterReplay 已呈现 #1,每拍推进一条
  const g = env.evalIn("JSON.stringify((_findings['F-001']||{}).gates||null)");
  chk("D1 门禁三项 == 最后一个 gate.completed 载荷(未被后续重放检测覆盖/退化为未知)",
      gateCellsFromPanel(env) === "diff_bounds=PASS backdoor=PASS llm_hetero=PASS",
      "面板=[" + gateCellsFromPanel(env) + "] _findings.gates=" + g);
  const f = env.evalIn("JSON.stringify({pass:(_findings['F-001']||{}).pass,status:(_findings['F-001']||{}).status})");
  chk("D1 gate.completed 写入的 pass/status 未被重放检测覆盖(pass=true, 仍 patched)",
      f === '{"pass":true,"status":"patched"}', f);
  chk("D1 收敛环未因重放检测倒退(open=0, patched=1)",
      txtOf(env, "cv-open") === "0" && txtOf(env, "cv-patched") === "1",
      "open=" + txtOf(env, "cv-open") + " patched=" + txtOf(env, "cv-patched"));
  chk("D6 补丁 diff 摘要上屏(真载荷 diff/diff_sha256/artifact/template)",
      txtOf(env, "feed").indexOf("diff +1/-3 行") >= 0 &&
      txtOf(env, "feed").indexOf("sha abcdef012345") >= 0 &&
      txtOf(env, "feed").indexOf("patches/F-001.candidate.php") >= 0,
      JSON.stringify(txtOf(env, "feed").slice(-200)));

  await driveReplay(env, 3);            // #5 复验 → #6 终局 → rpDone
  const verdict = txtOf(env, "spot-act");
  chk("D4 正例 结束文案由真实门禁数据推导(门禁全过才说全过,且有复验才说已复测)",
      verdict.indexOf("回放结束") >= 0 && verdict.indexOf("全部通过") >= 0 &&
      verdict.indexOf("重放复测") >= 0, JSON.stringify(verdict));
  chk("D1b 尾帧收敛环自洽(1/1,verified)",
      txtOf(env, "rv") === "1/1" && env.evalIn("(_findings['F-001']||{}).status") === "verified",
      "rv=" + txtOf(env, "rv") + " status=" + env.evalIn("(_findings['F-001']||{}).status"));
}

/* D4 反例 + D3 + D5 + D7:回放一个门禁 FAIL 的 failed run(静态背景是「最新成功 run」) */
async function scenarioD4VerdictRespectsFailedGate() {
  const evs = [
    ev("run-FAIL", 1, "detection.completed", "classify",
       { matched: true, class: "sqli", severity: "high", endpoint: "/news/search?q=x", alerts: 1 }, "检出"),
    ev("run-FAIL", 2, "patch.proposed", "patch",
       { finding_id: "F-001", provider: "qwen", diff: "--- \n+++ \n@@ -1 +1 @@\n-a\n+b\n",
         diff_sha256: "0011223344556677", artifact: "patches/F-001.candidate.php" }, "候选"),
    ev("run-FAIL", 3, "gate.completed", "review",
       { finding_id: "F-001", pass: false, reasons: ["异构复核 FAIL: FAIL"],
         gates: [{ gate: "diff_bounds", result: "PASS" }, { gate: "backdoor", result: "PASS" },
                 { gate: "llm_hetero", result: "FAIL" }] }, "门禁否决"),
    ev("run-FAIL", 4, "run.failed", "review",
       { error: "review_rejected: 异构复核 FAIL: FAIL", stage: "review" },
       "failed: repair=rejected judge=ok learn=not_run cleanup=restored flows=3"),
  ];
  const env = createEnv({
    router: routerFor({ runId: "run-FAIL", eventsByRun: { "run-FAIL": evs },
                        snapshotState: { "run-FAIL": "failed" },
                        runs: [{ run_id: "run-NEW", state: "succeeded", created_at: "2026-09-26T13:00:00.000Z" }],
                        snapshots: {
                          "run-NEW": { state: "succeeded", cleanup: "restored", started_at: "2026-09-26T12:00:00.000Z",
                                       ended_at: "2026-09-26T12:01:32.000Z" },
                          "run-FAIL": { state: "failed", cleanup: "restored", started_at: "2026-09-26T11:55:32.000Z",
                                        ended_at: "2026-09-26T11:56:50.000Z",
                                        summary: "failed: repair=rejected judge=ok learn=not_run cleanup=restored flows=3" } } }),
  });
  await env.settle();
  const stalePhase = txtOf(env, "phase");   // boot 静态背景留下的相位(D3 的触发条件)
  await env.evalIn("pickRun('run-FAIL')");
  await env.settle();
  const ph = txtOf(env, "phase");
  chk("D3 切入回放复位相位芯片(不沿用 boot 静态背景的历史相位)",
      env.state().mode === "replay" && ph === "● REPLAY · 回放中" && ph.indexOf("历史") < 0,
      "切入前=\"" + stalePhase + "\" 切入后=\"" + ph + "\"");
  chk("D5 顶栏「耗时」与收敛环「用时」同源一致(不再恒为 —)",
      txtOf(env, "t-wall") === txtOf(env, "cv-wall") && txtOf(env, "t-wall") === "1m18s",
      "t-wall=" + txtOf(env, "t-wall") + " cv-wall=" + txtOf(env, "cv-wall"));

  await driveReplay(env, evs.length + 1);
  chk("D1' 失败轮次门禁三项仍 == gate.completed 载荷(PASS PASS FAIL 不被吞成未知)",
      gateCellsFromPanel(env) === "diff_bounds=PASS backdoor=PASS llm_hetero=FAIL",
      "面板=[" + gateCellsFromPanel(env) + "]");
  const verdict = txtOf(env, "spot-act");
  chk("D4 反例 结束文案不得宣称门禁全过(与同屏 FAIL 相反)",
      verdict.indexOf("回放结束") >= 0 && !ALL_PASS_CLAIM.test(verdict),
      JSON.stringify(verdict));
  chk("D4 反例 文案如实点出被否决项",
      verdict.indexOf("否决") >= 0 && verdict.indexOf("异构复核") >= 0, JSON.stringify(verdict));
  const feed = txtOf(env, "feed");
  chk("D7 终局横幅显示 payload.error 根因(不再被 summary 顶掉)",
      feed.indexOf("review_rejected: 异构复核 FAIL: FAIL") >= 0 &&
      feed.indexOf("回放:failed · failed:") < 0, JSON.stringify(feed.slice(-160)));
}

(async () => {
  await scenarioA08();
  await scenarioA09();
  await scenarioA10();
  await scenarioA12();
  await scenarioA14();
  await scenarioA14b();
  await scenarioD1GatesSurviveRedetection();
  await scenarioD4VerdictRespectsFailedGate();
  console.log(`\n结果: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error("HARNESS ERROR:", (e && e.stack) || e); process.exit(2); });

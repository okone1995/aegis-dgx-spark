#!/usr/bin/env node
/**
 * tests/browser/replay_switch.mjs —— 「回放失败轮后顶栏是否被最新成功轮覆盖」验收驱动器(真 Chrome + CDP)。
 *
 * 断言方向(先写明期望,再比对实测):
 *   A. 首屏 idle:顶栏 run_id 应等于最新终态 run(=24de,静态背景),相位 CONVERGED · 成功(历史);
 *      LIVE 按钮可见可点但本轮**绝不点击**;回放条隐藏。
 *   B. 回放失败轮 943c(事件流尾帧 = run.failed)结束后再等 ≥2 次 3s 扫描:
 *      顶栏 run_id 与相位**应与事件流尾帧同源**(仍为 943c / FAILED),
 *      若变成 24de / CONVERGED · 成功(历史) ⇒ 判定 FAIL(缺陷复现)。
 *   C. 回放成功轮 24de 亦然:顶栏应仍为 24de 且相位为回放口径(后缀「· 回放」而非「(历史)」)。
 *   D. 1366×768 重载后首屏关键控件(LIVE 按钮 / run 选择器 / 回放条)可见性与可点性。
 *
 * 只读红线:全程仅 GET(Network 域守卫记录所有请求方法,出现非 GET 立即中止并报告)。
 * 不编辑被测前端;不点 LIVE。
 *
 * 用法:
 *   node tests/browser/replay_switch.mjs
 *   node tests/browser/replay_switch.mjs --url=http://127.0.0.1:8822/demo --out=.zwork/browser
 */
import { createHash } from 'node:crypto';
import { execSync } from 'node:child_process';
import { mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { apiGet, launchBrowser, sleep, waitFor } from './cdp.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');

function parseArgs(argv) {
  const out = { url: 'http://127.0.0.1:8822/demo', port: 9333, out: path.join('.zwork', 'browser') };
  for (const a of argv.slice(2)) {
    const m = /^--([^=]+)=(.*)$/.exec(a);
    if (m) out[m[1]] = m[2];
  }
  return out;
}

const ARGS = parseArgs(process.argv);
const DEMO_URL = ARGS.url;
const ORIGIN = new URL(DEMO_URL).origin;
const PORT = Number(ARGS.port) || 9333;
const OUT_DIR = path.isAbsolute(ARGS.out) ? ARGS.out : path.join(REPO, ARGS.out);
const DEMO_FILE = path.join(REPO, 'demo', 'index.html');

const RUN_OK = 'run-20260926-175602-a784';   // 成功轮(2026-09-26 真机连续第三轮;此前为 -152228-24de)
const RUN_FAIL = 'run-20260926-151849-943c'; // 预检失败轮
const SCAN_INTERVAL_MS = 3000;
const POST_SCANS_WAIT_MS = 8000;             // ≥2 次 3s 扫描(题面要求)
const REPLAY_SPEED = '4';                    // 用页面自己的速度滑条(真实控件)

/* ---------------------------------------------------------------- 页面探针 */
/** 一次性读出首屏/顶栏/阶段条/判官卡/收敛环/风险面板/事件流尾部/关键控件几何。 */
const PROBE = `(() => {
  const T = (id) => { const e = document.getElementById(id); return e ? e.textContent.replace(/\\s+/g, ' ').trim() : null; };
  const R = (id) => {
    const e = document.getElementById(id);
    if (!e) return null;
    const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
    return {
      x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
      visible: cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity) > 0 && r.width > 0 && r.height > 0,
      fullyInViewport: r.top >= 0 && r.left >= 0 && r.bottom <= window.innerHeight && r.right <= window.innerWidth,
      disabled: !!e.disabled
    };
  };
  const feed = document.getElementById('feed');
  const rows = feed ? Array.from(feed.children) : [];
  const tail = rows.slice(-3).map((el) => el.textContent.replace(/\\s+/g, ' ').trim().slice(0, 200));
  const pk = document.getElementById('pk-list');
  const lex = (fn) => { try { return fn(); } catch (e) { return 'unavailable'; } };
  return {
    readyState: document.readyState,
    viewport: { w: window.innerWidth, h: window.innerHeight },
    overflow: { scrollW: document.documentElement.scrollWidth, scrollH: document.documentElement.scrollHeight, hOverflow: document.documentElement.scrollWidth > window.innerWidth },
    topbar: { phase: T('phase'), run: T('rm-run'), case_id: T('rm-case'), models: T('rm-models'), clean: T('rm-clean'), conn: T('conn'), wall: T('t-wall'), shots: T('t-shots'), block: T('t-block'), veri: T('t-veri') },
    stagebar: T('stagebar'),
    judge: { p_attack: T('jv-p'), mode: T('j-mode'), n: T('j-n'), attack: T('j-a'), benign: T('j-b'), abstain: T('j-s'), lat: T('j-lat'), state: T('j-state'), sel: T('j-sel'), diff: T('j-diff'), note: T('jv-note'), verdict: T('jv-verdict') },
    receipt: { r1: T('ev-1'), r2: T('ev-2'), r3: T('ev-3'), r4: T('ev-4'), src: T('ev-src'), table: T('j-table') },
    conv: { rv: T('rv'), open: T('cv-open'), patched: T('cv-patched'), hits: T('cv-hits'), atk: T('cv-atk'), wall: T('cv-wall'), finVisible: R('cv-fin') ? R('cv-fin').visible : null },
    risk: { pattack: T('risk-pattack'), exploit: T('risk-exploit'), impact: T('risk-impact'), state: T('risk-state') },
    feedCount: rows.length,
    feedTail: tail,
    spotlight: { role: T('spot-role'), name: T('spot-name'), act: T('spot-act') },
    errbar: { text: T('errbar'), cls: (document.getElementById('errbar') || {}).className || '' },
    controls: {
      btnLive: R('btn-live'), btnReplay: R('btn-replay'), btnCancel: R('btn-cancel'),
      replaybar: R('replaybar'), modebadge: R('modebadge'), runpicker: R('runpicker'), pkList: R('pk-list'),
      replaybarShown: !!(document.getElementById('replaybar') || {}).classList && document.getElementById('replaybar').classList.contains('show'),
      modebadgeShown: !!(document.getElementById('modebadge') || {}).classList && document.getElementById('modebadge').classList.contains('show'),
      pickerShown: !!(document.getElementById('runpicker') || {}).classList && document.getElementById('runpicker').classList.contains('show'),
      pkRows: pk ? pk.children.length : 0
    },
    rpPos: T('rp-pos'),
    viewbar: { text: T('viewbar'), cls: ((document.getElementById('viewbar') || {}).className || '') },
    lexical: {
      mode: lex(() => _mode),
      view: lex(() => _view),
      viewRunId: lex(() => _viewRunId),
      staticRunId: lex(() => _staticRunId),
      replayRunId: lex(() => (_rp && _rp.runId) || null),
      replayIndex: lex(() => (_rp && _rp.i)),
      replayLength: lex(() => (_rp && _rp.items ? _rp.items.length : null)),
      replaySpeed: lex(() => (_rp && _rp.speed))
    }
  };
})()`;

/* ------------------------------------------------------------------ 小工具 */
const iso = () => new Date().toISOString();
const sha256 = (file) => createHash('sha256').update(readFileSync(file)).digest('hex');
const pick = (probe, keys) => {
  const o = {};
  for (const k of keys) o[k] = probe[k];
  return o;
};

const steps = [];
const retries = [];
const screenshots = [];
const consoleErrors = [];

function record(step) {
  // 每一步都盖上「当时被测前端是哪一份」的章 —— 前端正被并发修改,
  // 没有这个戳就无法把判定归到具体修订上。
  try {
    const st = statSync(DEMO_FILE);
    step.subjectRevisionAtRecord = { sha256_16: sha256(DEMO_FILE).slice(0, 16), mtime: st.mtime.toISOString(), bytes: st.size };
  } catch (e) {
    step.subjectRevisionAtRecord = { error: String(e.message || e) };
  }
  steps.push(step);
  const tag = step.verdict.padEnd(10);
  console.log(`[${step.id}] ${tag} ${step.name}${step.note ? ' — ' + step.note : ''}`);
  return step;
}

/** 每步包一层:失败自动重试一次(前端正被并发修改,瞬时 DOM 变动可能导致驱动报错)。 */
async function withRetry(label, fn) {
  try {
    return await fn();
  } catch (e) {
    retries.push({ label, attempt: 1, error: String(e.message || e), at: iso() });
    await sleep(1500);
    try {
      return await fn();
    } catch (e2) {
      retries.push({ label, attempt: 2, error: String(e2.message || e2), at: iso(), final: true });
      throw e2;
    }
  }
}

const readProbe = (page) => page.evaluate(PROBE);

async function waitForScanCycle(page, { timeoutMs = 12000 } = {}) {
  // boot() 里 scanRuns() 一次后 _staticRunId 非空 —— 用运行期探测确认「一轮扫描已完成」。
  try {
    await waitFor(async () => {
      const v = await page.evaluate("(() => { try { return _staticRunId || null; } catch (e) { return 'unavailable'; } })()");
      return v && v !== 'unavailable' ? v : null;
    }, { timeoutMs, intervalMs: 250, label: '_staticRunId 被赋值(一轮 scanRuns 完成)' });
    return 'detected';
  } catch {
    return 'timeout-fallback'; // 探针不可得时退化为固定等待
  }
}

/** 打开 run 选择器(真点 #btn-replay 或运行期探测到的等价函数),再点中目标 run 行。 */
async function replayRun(page, runId) {
  await page.clickSelector('#btn-replay');
  const rows = await waitFor(async () => {
    const n = await page.evaluate("(() => { const l = document.getElementById('pk-list'); return l ? l.children.length : 0; })()");
    return n > 0 ? n : null;
  }, { timeoutMs: 20000, intervalMs: 250, label: 'run 选择器行渲染(打开选择器)' });

  const row = await page.evaluate(`(() => {
    const rows = Array.from(document.querySelectorAll('#pk-list .pk-row'));
    const i = rows.findIndex((r) => r.textContent.includes(${JSON.stringify(runId)}));
    if (i < 0) return null;
    const el = rows[i];
    el.scrollIntoView({ block: 'center' });
    const r = el.getBoundingClientRect();
    return { i, count: rows.length, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), text: el.textContent.replace(/\\s+/g, ' ').trim() };
  })()`);
  if (!row) throw new Error(`选择器里找不到 run 行: ${runId}(共 ${rows} 行)`);
  const click = await page.clickAt(row.x, row.y);

  // 进入回放态(真控件点击 → pickRun → enterReplay)
  // 2026-09-27 评审:不能用“回放条可见”当判据 —— 冻结态会**故意保留**回放条,
  // 上一轮的残留会让本步立即通过。改为按视角与目标 run id 精确等待。
  await waitFor(async () => {
    const s = await page.evaluate(`(() => {
      let m = 'unavailable', v = 'unavailable', vrid = 'unavailable', rp = 'unavailable';
      try { m = _mode; } catch (e) {}
      try { v = _view; } catch (e) {}
      try { vrid = _viewRunId; } catch (e) {}
      try { rp = (_rp && _rp.runId) || ''; } catch (e) {}
      return { mode: m, view: v, viewRunId: vrid, rpRunId: rp };
    })()`);
    const ok = s.view === 'replay' && String(s.viewRunId) === String(runId) && String(s.rpRunId) === String(runId);
    return ok ? s : null;
  }, { timeoutMs: 25000, intervalMs: 250, label: `进入回放态(view=replay 且 viewRunId=${runId})` });
  return { pickerRows: rows, row, click };
}

/** 等指定 run 的回放**真的播完**:view=frozen 且 _rp.i 到达 _rp.items.length,再等扫描周期。
    2026-09-27 评审:原先按固定时长等待,会在 24/41 这种中途状态取样。 */
async function waitReplayFinished(page, runId, extraScansMs = POST_SCANS_WAIT_MS) {
  await waitFor(async () => {
    const s = await page.evaluate(`(() => {
      let v = 'unavailable', vrid = 'unavailable', i = -1, n = -1;
      try { v = _view; } catch (e) {}
      try { vrid = _viewRunId; } catch (e) {}
      try { i = (_rp && _rp.i) || 0; } catch (e) {}
      try { n = (_rp && _rp.items && _rp.items.length) || 0; } catch (e) {}
      return { view: v, viewRunId: vrid, i: i, n: n };
    })()`);
    const ok = s.view === 'frozen' && String(s.viewRunId) === String(runId) && s.n > 0 && s.i >= s.n;
    return ok ? s : null;
  }, { timeoutMs: 240000, intervalMs: 250, label: `回放播完(view=frozen 且 _rp.i 到顶: ${runId})` });
  await sleep(extraScansMs);
  return true;
}

/** 用页面自己的速度滑条加速(真实控件 + input 事件,等价于用户拖动)。 */async function setReplaySpeed(page, speed) {
  return page.evaluate(`(() => {
    const el = document.getElementById('rp-speed');
    if (!el) return { ok: false, reason: '找不到 #rp-speed' };
    el.value = ${JSON.stringify(speed)};
    el.dispatchEvent(new Event('input', { bubbles: true }));
    let s = 'unavailable'; try { s = _rp.speed; } catch (e) {}
    return { ok: true, sliderValue: el.value, internalSpeed: s };
  })()`);
}

/** 等回放自然结束(rpDone:回放条收起 / _mode 回 idle)。 */
async function waitReplayEnd(page, runId) {
  const t0 = Date.now();
  const state = await waitFor(async () => {
    const s = await page.evaluate(`(() => {
      let mode = 'unavailable', i = null, len = null;
      try { mode = _mode; } catch (e) {}
      try { i = _rp.i; len = _rp.items.length; } catch (e) {}
      const b = document.getElementById('replaybar');
      return { mode, i, len, showing: !!b && b.classList.contains('show') };
    })()`);
    if (s.mode === 'idle' || s.showing === false) return s;
    return null;
  }, { timeoutMs: 240000, intervalMs: 300, label: `回放 ${runId} 自然结束` });
  return { ...state, elapsedMs: Date.now() - t0 };
}

/**
 * 采样顶栏 run_id / 相位的时间线。—— 关键:页面每 3s 的 scanRuns tick 是异步的,
 * 「回放结束瞬间」的单点快照会因 tick 是否落在采样窗口内而不同。用 250ms 轮询的时间线,
 * 才能给出与采样时机无关的判定依据。
 */
async function watchTopbar(page, durationMs, intervalMs = 250) {
  const t0 = Date.now();
  const samples = [];
  while (Date.now() - t0 < durationMs) {
    const s = await page.evaluate(`(() => {
      const r = document.getElementById('rm-run'), p = document.getElementById('phase');
      let m = 'unavailable', sid = null;
      try { m = _mode; } catch (e) {}
      try { sid = _staticRunId; } catch (e) {}
      return { run: r ? r.textContent.trim() : null, phase: p ? p.textContent.trim() : null, mode: m, staticRunId: sid };
    })()`);
    samples.push({ tMs: Date.now() - t0, ...s });
    await sleep(intervalMs);
  }
  // 压缩为「变化点」序列
  const changes = [];
  for (const s of samples) {
    const last = changes[changes.length - 1];
    if (!last || last.run !== s.run || last.phase !== s.phase || last.staticRunId !== s.staticRunId) {
      changes.push({ tMs: s.tMs, run: s.run, phase: s.phase, mode: s.mode, staticRunId: s.staticRunId });
    }
  }
  return { durationMs, intervalMs, sampleCount: samples.length, first: samples[0], last: samples[samples.length - 1], changes };
}

/* -------------------------------------------------------------------- 主流程 */
async function main() {
  const startedAt = iso();
  const demoStat0 = statSync(DEMO_FILE);
  const demoHash0 = sha256(DEMO_FILE);
  mkdirSync(OUT_DIR, { recursive: true });

  /* 记录被测修订(只读 git 查询,不进工作区) */
  const git = (cmd) => { try { return execSync(cmd, { cwd: REPO, stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim(); } catch (e) { return 'unavailable: ' + String(e.message || e).split('\n')[0]; } };

  const result = {
    schema: 'aegis.browser.replay_switch/1',
    startedAt, finishedAt: null,
    target: { url: DEMO_URL, repoRoot: REPO, outDir: OUT_DIR },
    constants: { RUN_OK, RUN_FAIL, SCAN_INTERVAL_MS, POST_SCANS_WAIT_MS, REPLAY_SPEED },
    subjectUnderTest: {
      file: 'demo/index.html', sha256AtStart: demoHash0, mtimeAtStart: demoStat0.mtime.toISOString(), bytesAtStart: demoStat0.size,
      gitHead: null, demoIndexDirtyVsHead: null,
      note: '前端正被另一子代理并发修改,故记录改前/改后哈希;两次不同即视为本轮可能观察到修复前后两种行为。',
    },
    environment: {},
    steps: [],
    retries: [],
    screenshots: [],
    pageConsoleErrors: [],
    frontendConcurrentEdit: null,
    onlyGetRequests: null,
    fatalError: null,
  };

  let browser = null;
  let page = null;

  result.subjectUnderTest.gitHead = git('git rev-parse HEAD');
  result.subjectUnderTest.demoIndexDirtyVsHead = git('git status --porcelain -- demo/index.html') || '(clean vs HEAD)';

  try {
    browser = await launchBrowser({ port: PORT, windowSize: '1920,1080' });
    result.environment.browser = browser.info();
    page = await browser.openPage();

    /* 地面真值(GET,只读):两个 run 的终态与事件流尾帧 */
    const gtOk = await apiGet(ORIGIN, `/api/demo/runs/${RUN_OK}/events`);
    const gtFail = await apiGet(ORIGIN, `/api/demo/runs/${RUN_FAIL}/events`);
    const lastOf = (r) => (r.json?.events || []).slice(-1)[0] || null;
    result.groundTruth = {
      runOk: { status: gtOk.status, eventCount: (gtOk.json?.events || []).length, lastEvent: lastOf(gtOk) && { seq: lastOf(gtOk).seq, type: lastOf(gtOk).type, status: lastOf(gtOk).status, summary: lastOf(gtOk).summary } },
      runFail: { status: gtFail.status, eventCount: (gtFail.json?.events || []).length, lastEvent: lastOf(gtFail) && { seq: lastOf(gtFail).seq, type: lastOf(gtFail).type, status: lastOf(gtFail).status, summary: lastOf(gtFail).summary, error: lastOf(gtFail).payload?.error } },
    };

    /* ---------------------------------------------------- A. 首屏 idle(1920×1080) */
    await withRetry('step A', async () => {
      await page.setViewport(1920, 1080);
      await page.navigate(DEMO_URL);

      /* 运行期探测:页面到底暴露了哪些函数(不硬编码函数名;在 /demo 源上探测,
         且只读属性描述符,避免触碰会抛 SecurityError 的访问器属性)。 */
      const fnProbe = await page.evaluate(`(() => {
        const fnNames = [];
        for (const n of Object.getOwnPropertyNames(window)) {
          try {
            const d = Object.getOwnPropertyDescriptor(window, n);
            if (d && typeof d.value === 'function') fnNames.push(n);
          } catch (e) { /* 访问器属性跳过,不触发读 */ }
        }
        const pick = (re) => fnNames.filter((n) => re.test(n)).sort();
        const kind = (n) => { try { return typeof window[n]; } catch (e) { return 'accessor-denied'; } };
        return {
          allFunctionCount: fnNames.length,
          replayRelated: pick(/rp|replay|pick|picker/i),
          runRelated: pick(/run|scan|static/i),
          probeExpectations: {
            openRunPicker: kind('openRunPicker'), pickRun: kind('pickRun'),
            rpDone: kind('rpDone'), rpToggle: kind('rpToggle'), rpStep: kind('rpStep'), rpSetSpeed: kind('rpSetSpeed'),
            scanRuns: kind('scanRuns'), loadStatic: kind('loadStatic'), startLive: kind('startLive')
          }
        };
      })()`);
      result.runtimeFunctionProbe = fnProbe;

      const scanCycle = await waitForScanCycle(page);
      await sleep(1200);
      const probe = await readProbe(page);
      const shot = await page.screenshot(path.join(OUT_DIR, '01_idle_1920.png'));
      result.screenshots.push({ step: 'A', ...shot, absolutePath: path.resolve(shot.path) });
      screenshots.push({ step: 'A', ...shot });

      const c = probe.controls;
      const checks = {
        '顶栏 run_id = 最新终态 run(24de)': probe.topbar.run === RUN_OK,
        '相位 = CONVERGED · 成功(历史)': /CONVERGED/.test(probe.topbar.phase || '') && /历史/.test(probe.topbar.phase || ''),
        'LIVE 按钮可见且未禁用': !!c.btnLive && c.btnLive.visible && c.btnLive.fullyInViewport && !c.btnLive.disabled,
        'run 选择器入口(#btn-replay)可见可点': !!c.btnReplay && c.btnReplay.visible && !c.btnReplay.disabled,
        '回放条在 idle 隐藏': c.replaybarShown === false,
        '选择器面板在 idle 关闭': c.pickerShown === false,
        '事件流区域已挂载(idle 下可能是占位行)': probe.feedCount > 0,
      /* T10-S2:四步收据必须真渲染(该轮有 evidence-receipt 工件);缺失时页面应显“未提供” */
      '四步收据①补前命中已渲染(非未提供)': !String(probe.receipt.r1 || '').includes('未提供'),
      '四步收据③审查门禁已渲染(非未提供)': !String(probe.receipt.r3 || '').includes('未提供'),
      };
      const failed = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
      record({
        id: 'A', name: '首屏 idle 基线(1920×1080)', at: iso(), viewport: '1920x1080', scanCycle, renderWaitMs: 1200,
        expectation: 'idle 静态背景:顶栏 run_id=24de、相位 ● CONVERGED · 成功(历史);LIVE 按钮可见可点(本轮不点);回放条与选择器隐藏',
        observedText: {
          viewbar: probe.viewbar,
          phase: probe.topbar.phase, run_id: probe.topbar.run, case_id: probe.topbar.case_id, clean: probe.topbar.clean,
          wall: probe.topbar.wall, shots: probe.topbar.shots, block: probe.topbar.block, veri: probe.topbar.veri,
          stagebar: probe.stagebar, judge: probe.judge, conv: probe.conv, risk: probe.risk,
          feedTail: probe.feedTail, spotlight: probe.spotlight,
          controls: { btnLive: c.btnLive, btnReplay: c.btnReplay, replaybarShown: c.replaybarShown, pickerShown: c.pickerShown },
          lexical: probe.lexical,
        },
        checks, screenshot: shot.path, screenshotBytes: shot.bytes,
        verdict: failed.length === 0 ? 'PASS' : 'FAIL', failedChecks: failed,
        note: failed.length ? '未达期望:' + failed.join(' / ') : '与期望一致',
      });
    });

    /* --------------------------------- B. 回放失败轮 943c + 等 2 次扫描(1920×1080) */
    await withRetry('step B', async () => {
      const enter = await replayRun(page, RUN_FAIL);
      const speed = await setReplaySpeed(page, REPLAY_SPEED);
      const during = await readProbe(page);
      const end = await waitReplayEnd(page, RUN_FAIL);
      await sleep(400);
      const atEnd = await readProbe(page);

      // ≥2 次 3s 扫描;250ms 采样顶栏时间线(判定依据与扫描 tick 落点无关)
      const timeline = await watchTopbar(page, POST_SCANS_WAIT_MS);
      const afterScans = await readProbe(page);
      const shot = await page.screenshot(path.join(OUT_DIR, '02_replay_943c_after_scans.png'));
      result.screenshots.push({ step: 'B', ...shot, absolutePath: path.resolve(shot.path) });
      screenshots.push({ step: 'B', ...shot });

      const tailText = (afterScans.feedTail || []).join(' | ');
      const replayedId = during.lexical.replayRunId && during.lexical.replayRunId !== 'unavailable' ? during.lexical.replayRunId : RUN_FAIL;
      // 事件流尾部是否仍承载「被回放轮」的终局证据:用该轮自己的错误串匹配。
      // 不用 /failed/i —— 前端文案正被并发修改(实测已从“回放:failed”改为“回放:该轮 RUN 失败”),
      // 判定不得依赖会被改稿的措辞。
      const failKey = String(result.groundTruth?.runFail?.lastEvent?.error || '').split(':')[0];
      const feedTailCarriesReplayedRunError = !!failKey && tailText.includes(failKey);
      const runIdSame = afterScans.topbar.run === replayedId;
      const flippedAtWindowStart = timeline.first?.run !== replayedId;
      const flipEntry = (timeline.changes || []).find((c) => c.run !== replayedId) || null;
      const overwriteSeenAtMs = runIdSame ? null : (flippedAtWindowStart ? 0 : (flipEntry ? flipEntry.tMs : null));
      let verdict = 'UNVERIFIED';
      let note = '';
      if (runIdSame) {
        verdict = 'PASS';
        note = `回放结束后顶栏仍指向被回放 run(${replayedId}),未被最近成功轮覆盖`;
      } else {
        verdict = 'FAIL';
        note = `顶栏与事件流不同源:被回放轮 ${replayedId} 结束后,顶栏 run_id=${afterScans.topbar.run} / 相位=${afterScans.topbar.phase}`
          + (afterScans.viewbar?.text ? ` / 页面视角条=「${afterScans.viewbar.text}」` : '')
          + `,而被回放轮的终局证据仍在事件流尾部(${feedTailCarriesReplayedRunError ? '已匹配到该轮错误串 ' + failKey : '尾部归属需人工确认'})。`
          + (flippedAtWindowStart
            ? '翻转在 rpDone 后 ≤400ms 内已完成(采样窗口起点即已是新值)。'
            : `翻转发生在 rpDone 后约 ${overwriteSeenAtMs}ms 的扫描周期。`);
      }
      record({
        id: 'B', name: `回放失败轮 943c 后等 ${POST_SCANS_WAIT_MS}ms(≥2 次扫描)`, at: iso(), viewport: '1920x1080',
        expectation: `回放 943c 结束后顶栏 run_id 与相位应与事件流尾帧同源(run_id=943c / ● FAILED · 回放 或等价失败文案),不得被 24de 覆盖`,
        replaySpeed: { slider: REPLAY_SPEED, applied: speed }, replayEnd: end, postScansWaitMs: POST_SCANS_WAIT_MS,
        replayedRunId: replayedId,
        topbarTimeline: { note: 'rpDone 后 250ms 采样;changes = 顶栏 run/相位/_staticRunId 变化点', durationMs: timeline.durationMs, intervalMs: timeline.intervalMs, sampleCount: timeline.sampleCount, first: timeline.first, last: timeline.last, changes: timeline.changes },
        pickerEnter: { pickerRows: enter.pickerRows, rowClicked: enter.row.text, clickTarget: enter.click.elementAtPoint },
        observedText: {
          duringReplay: { phase: during.topbar.phase, run_id: during.topbar.run, rpPos: during.rpPos, lexical: during.lexical, replaybarShown: during.controls.replaybarShown },
          atReplayEnd: { phase: atEnd.topbar.phase, run_id: atEnd.topbar.run, rpPos: atEnd.rpPos, spotlight: atEnd.spotlight, lexical: atEnd.lexical, feedTail: atEnd.feedTail, viewbar: atEnd.viewbar },
          afterScans: {
            phase: afterScans.topbar.phase, run_id: afterScans.topbar.run, case_id: afterScans.topbar.case_id, wall: afterScans.topbar.wall,
            viewbar: afterScans.viewbar,
            veri: afterScans.topbar.veri, stagebar: afterScans.stagebar, judge: afterScans.judge, conv: afterScans.conv, risk: afterScans.risk,
            feedTail: afterScans.feedTail, feedCount: afterScans.feedCount, spotlight: afterScans.spotlight, errbar: afterScans.errbar,
            lexical: afterScans.lexical, replaybarShown: afterScans.controls.replaybarShown,
          },
        },
        eventStreamTail: result.groundTruth.runFail.lastEvent,
        sameSourceAfterScans: runIdSame,
        sameSourceAtReplayEnd_400ms: atEnd.topbar.run === replayedId,
        topbarOverwrittenBy: runIdSame ? null : afterScans.topbar.run,
        overwriteSeenAtMs,
        feedTailCarriesReplayedRunError,
        feedTailWordingAsRendered: afterScans.feedTail,
        screenshot: shot.path, screenshotBytes: shot.bytes,
        verdict, note,
      });
    });

    /* --------------------------------- C. 回放成功轮 24de + 等 2 次扫描(1920×1080) */
    await withRetry('step C', async () => {
      const enter = await replayRun(page, RUN_OK);
      const speed = await setReplaySpeed(page, REPLAY_SPEED);
      const during = await readProbe(page);
      const end = await waitReplayEnd(page, RUN_OK);
      await sleep(400);
      const atEnd = await readProbe(page);
      // 2026-09-27 评审:先确保**本轮真的播完**(view=frozen 且 _rp.i 到顶),再等扫描周期取样;
      // 否则会在 24/41 这种中途状态断言,得出假失败。
      await waitReplayFinished(page, RUN_OK);
      const timelineC = await watchTopbar(page, POST_SCANS_WAIT_MS);
      const afterScans = await readProbe(page);
      const shot = await page.screenshot(path.join(OUT_DIR, '03_replay_24de_after_scans.png'));
      result.screenshots.push({ step: 'C', ...shot, absolutePath: path.resolve(shot.path) });
      screenshots.push({ step: 'C', ...shot });

      const replayedId = RUN_OK;   /* 步 C 就是点 24de 那一行触发的回放,目标由构造决定;
                                     不再从 during 词法采样取 —— 该采样会落在上一步的冻结态上(时序伪影)。 */
      const runIdSame = afterScans.topbar.run === replayedId;
      // 24de 既是被回放轮也是最新成功轮 ⇒ run_id 无法区分「同源」与「被覆盖」;
      // 判别器是 _staticRunId 被重新填入 + 相位后缀变成「(历史)」(= loadStatic 重渲染发生了)。
      const staticRerenderObserved = (timelineC.changes || []).some((c) => c.staticRunId === replayedId)
        || afterScans.lexical.staticRunId === replayedId;
      const phaseFlipEntry = (timelineC.changes || []).find((c) => /历史/.test(c.phase || '')) || null;
      /* T10-S2 相关:四步收据、选中流、分歧数、三列表(真数据) */
      const receiptOk = !String((afterScans.receipt && afterScans.receipt.r1) || '').includes('未提供')
        && !String((afterScans.receipt && afterScans.receipt.r4) || '').includes('未提供');
      const selOk = String((afterScans.judge && afterScans.judge.sel) || '') === 'FL-003';
      const diffOk = String((afterScans.judge && afterScans.judge.diff) || '') === '3';
      const tableOk = String((afterScans.receipt && afterScans.receipt.table) || '').includes('FL-001')
        && String((afterScans.receipt && afterScans.receipt.table) || '').includes('不一致');
      const s2Ok = receiptOk && selOk && diffOk && tableOk;
      const verdict = (runIdSame && s2Ok) ? 'PASS' : 'FAIL';
      const s2Note = `四步收据=${receiptOk ? 'OK' : 'FAIL'} · 选中流=${String(afterScans.judge && afterScans.judge.sel)}`
        + `(${selOk ? 'OK' : 'FAIL'},期望 FL-003=finding 对应攻击流) · 与真值不一致=${String(afterScans.judge && afterScans.judge.diff)}`
        + `(${diffOk ? 'OK' : 'FAIL'},期望 3=三条 /login 误报) · 三列表含分歧行=${tableOk ? 'OK' : 'FAIL'}`;
      record({
        id: 'C', name: `回放成功轮 24de 后等 ${POST_SCANS_WAIT_MS}ms(≥2 次扫描)`, at: iso(), viewport: '1920x1080',
        expectation: '回放 24de 结束后顶栏 run_id 应仍为 24de;相位应为回放口径(含「· 回放」),不应被 idle 静态背景重渲染成「(历史)」',
        replaySpeed: { slider: REPLAY_SPEED, applied: speed }, replayEnd: end, postScansWaitMs: POST_SCANS_WAIT_MS,
        replayedRunId: replayedId,
        topbarTimeline: { note: 'rpDone 后 250ms 采样;changes = 顶栏 run/相位/_staticRunId 变化点', durationMs: timelineC.durationMs, intervalMs: timelineC.intervalMs, sampleCount: timelineC.sampleCount, first: timelineC.first, last: timelineC.last, changes: timelineC.changes },
        pickerEnter: { pickerRows: enter.pickerRows, rowClicked: enter.row.text, clickTarget: enter.click.elementAtPoint },
        observedText: {
          duringReplay: { phase: during.topbar.phase, run_id: during.topbar.run, rpPos: during.rpPos, lexical: during.lexical },
          atReplayEnd: { phase: atEnd.topbar.phase, run_id: atEnd.topbar.run, spotlight: atEnd.spotlight, lexical: atEnd.lexical, viewbar: atEnd.viewbar },
          afterScans: {
            phase: afterScans.topbar.phase, run_id: afterScans.topbar.run, wall: afterScans.topbar.wall, veri: afterScans.topbar.veri,
            receipt: afterScans.receipt, rpPos: afterScans.rpPos,
            viewbar: afterScans.viewbar,
            stagebar: afterScans.stagebar, judge: afterScans.judge, conv: afterScans.conv, risk: afterScans.risk,
            feedTail: afterScans.feedTail, feedCount: afterScans.feedCount, lexical: afterScans.lexical,
          },
        },
        eventStreamTail: result.groundTruth.runOk.lastEvent,
        runIdSameAsReplayed: runIdSame,
        staticRerenderObserved_lexical: staticRerenderObserved,
        phaseFlipEntry,
        s2Checks: { receiptOk: (typeof receiptOk !== 'undefined' ? receiptOk : null),
                    sel: (afterScans.judge && afterScans.judge.sel) || null,
                    diff: (afterScans.judge && afterScans.judge.diff) || null,
                    tableHasFL001: String((afterScans.receipt && afterScans.receipt.table) || '').includes('FL-001') },
        s2Note: (typeof s2Note !== 'undefined' ? s2Note : ''),
        screenshot: shot.path, screenshotBytes: shot.bytes,
        verdict,
        note: runIdSame
          ? (staticRerenderObserved
            ? `run_id 同源(${replayedId}),但 _staticRunId 已被重新填入为 ${afterScans.lexical.staticRunId}`
              + (phaseFlipEntry ? ` 、相位在 rpDone 后约 ${phaseFlipEntry.tMs}ms 变为「${phaseFlipEntry.phase}」` : '')
              + ' ⇒ loadStatic 静态背景重渲染已发生。24de 与被覆盖目标为同一 run,步 B 才是唯一能区分「同源 / 被覆盖」的场景。'
            : 'run_id 与相位均为回放口径,本次采样未观察到静态背景重渲染')
          : `run_id 与回放轮不一致(atEnd=${atEnd.topbar.run}, afterScans=${afterScans.topbar.run})`,
      });
    });

    /* ------------------------------------------- D. 1366×768 首屏关键控件 */
    await withRetry('step D', async () => {
      await page.setViewport(1366, 768);
      await page.reload();
      const scanCycle = await waitForScanCycle(page);
      await sleep(1200);
      const probe = await readProbe(page);
      const shot = await page.screenshot(path.join(OUT_DIR, '04_idle_1366.png'));
      result.screenshots.push({ step: 'D', ...shot, absolutePath: path.resolve(shot.path) });
      screenshots.push({ step: 'D', ...shot });

      const c = probe.controls;
      const checks = {
        'LIVE 按钮可见且在视口内': !!c.btnLive && c.btnLive.visible && c.btnLive.fullyInViewport,
        'LIVE 按钮可用(未禁用)——本轮未点击': !!c.btnLive && !c.btnLive.disabled,
        'run 选择器入口(#btn-replay)可见且在视口内': !!c.btnReplay && c.btnReplay.visible && c.btnReplay.fullyInViewport,
        'run 选择器入口可用(未禁用)': !!c.btnReplay && !c.btnReplay.disabled,
        '回放条在 idle 隐藏(与 1920 一致)': c.replaybarShown === false,
        '无横向溢出(1366 宽)': probe.overflow.hOverflow === false,
      };
      const failed = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);

      // 真点一次 run 选择器,确认 1366 下确实可点开(只读 GET;不点 LIVE)
      let openAttempt = null;
      try {
        const clicked = await page.clickSelector('#btn-replay');
        openAttempt = { ok: true, click: clicked, after: await waitFor(async () => {
          const s = await page.evaluate("(() => { const l = document.getElementById('pk-list'); const pk = document.getElementById('runpicker'); return { shown: !!pk && pk.classList.contains('show'), rows: l ? l.children.length : 0 }; })()");
          return s.shown ? s : null;
        }, { timeoutMs: 15000, intervalMs: 250, label: '1366 下选择器打开' }) };
      } catch (e) {
        openAttempt = { ok: false, error: String(e.message || e) };
      }

      record({
        id: 'D', name: '1366×768 重载后首屏关键控件', at: iso(), viewport: '1366x768', scanCycle, renderWaitMs: 1200,
        expectation: '1366×768 下 LIVE 按钮、run 选择器入口(#btn-replay)仍可见且在视口内、未禁用;回放条在 idle 应隐藏;无横向溢出',
        observedText: {
          topbar: probe.topbar, stagebar: probe.stagebar, overflow: probe.overflow, viewbar: probe.viewbar,
          controls: { btnLive: c.btnLive, btnReplay: c.btnReplay, replaybar: c.replaybar, replaybarShown: c.replaybarShown, pickerShown: c.pickerShown },
          lexical: probe.lexical,
        },
        checks, pickerOpenAttempt: openAttempt,
        screenshot: shot.path, screenshotBytes: shot.bytes,
        verdict: failed.length === 0 ? 'PASS' : 'FAIL', failedChecks: failed,
        note: (failed.length ? '未达期望:' + failed.join(' / ') : '与期望一致') + (openAttempt?.ok ? ';选择器真点测试通过' : ';选择器真点测试失败'),
      });
    });

    result.frontendConcurrentEdit = {
      sha256AtStart: demoHash0,
      sha256AtEnd: sha256(DEMO_FILE),
      mtimeAtEnd: statSync(DEMO_FILE).mtime.toISOString(),
      changedDuringRun: sha256(DEMO_FILE) !== demoHash0,
    };
  } catch (e) {
    result.fatalError = { message: String(e.message || e), stack: String(e.stack || '').split('\n').slice(0, 6).join('\n'), at: iso(), chromeStderr: browser?.logBuf?.value?.slice(-3000) || null };
    console.error('[FATAL] ' + result.fatalError.message);
  } finally {
    if (page) {
      result.onlyGetRequests = { noNonGetObserved: page.writeRequests().length === 0, nonGet: page.writeRequests(), summary: page.requestSummary() };
      result.pageConsoleErrors = page.consoleErrors.slice(0, 20);
    }
    if (browser) {
      try { result.browserClose = await browser.close(); } catch (e) { result.browserClose = { error: String(e.message || e) }; }
    }
    result.finishedAt = iso();
    result.steps = steps;
    result.retries = retries;
    result.screenshots = screenshots.map((s) => ({ step: s.step, path: s.path, absolutePath: s.absolutePath, bytes: s.bytes }));
    result.summary = {
      verdicts: Object.fromEntries(steps.map((s) => [s.id, s.verdict])),
      defectReproduced_stepB: steps.find((s) => s.id === 'B')?.verdict === 'FAIL',
      screenshotCount: screenshots.length,
      screenshotBytes: screenshots.map((s) => s.bytes),
      note: 'PASS/FAIL 是对「应有正确行为」的判定,不是对演示效果的背书;UNVERIFIED = 未取得可用证据。',
    };
    const outFile = path.join(HERE, 'last_run.json');
    writeFileSync(outFile, JSON.stringify(result, null, 2));
    // 每次运行另存一份归档(同一场景可能因前端并发修改/时序而呈现不同行为,保留可对照的历史)
    const histDir = path.join(OUT_DIR, 'history');
    mkdirSync(histDir, { recursive: true });
    const histFile = path.join(histDir, 'last_run_' + result.startedAt.replace(/[:.]/g, '-') + '.json');
    writeFileSync(histFile, JSON.stringify(result, null, 2));
    console.log('归档副本: ' + histFile);
    console.log('被测前端: ' + result.subjectUnderTest.file + ' sha256=' + result.subjectUnderTest.sha256AtStart.slice(0, 16)
      + ' mtime=' + result.subjectUnderTest.mtimeAtStart + ' head=' + String(result.subjectUnderTest.gitHead).slice(0, 12)
      + ' dirty=' + result.subjectUnderTest.demoIndexDirtyVsHead);
    console.log('前端本轮内被改写: ' + (result.frontendConcurrentEdit?.changedDuringRun ? '是' : '否'));
    console.log('\n机器可读结果: ' + outFile);
    console.log('截图: ' + screenshots.map((s) => `${path.basename(s.path)}(${s.bytes}B)`).join(', '));
    console.log('判定: ' + JSON.stringify(result.summary.verdicts));
    if (result.fatalError) console.log('FATAL: ' + result.fatalError.message);
    if (result.onlyGetRequests) console.log('只读守卫: 非 GET 请求数 = ' + result.onlyGetRequests.nonGet.length + ' (总请求 ' + result.onlyGetRequests.summary.total + ')');
    process.exitCode = result.fatalError ? 1 : 0;
  }
}

main();

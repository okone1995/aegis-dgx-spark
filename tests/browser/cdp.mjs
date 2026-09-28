/**
 * tests/browser/cdp.mjs —— 最小 CDP 客户端(Chrome --headless=new + 原生 WebSocket)。
 *
 * 用途:驱动 http://127.0.0.1:8822/demo 的真页面取证。只读用途。
 * 硬约束(见 Run 简报):
 *   - 只允许 GET;本模块在 Network 域上挂一个 GET-only 守卫,任何非 GET 请求都会被记录,
 *     并由调用方在关键节点 assert 后立即中止。
 *   - 不编辑被测前端,只驱动。
 * 依赖:Node 24 自带的全局 fetch / WebSocket(无第三方包)。
 */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export const DEFAULT_PORT = 9333;
export const DEFAULT_USER_DATA_DIR = path.join(os.tmpdir(), 'aegis-cdp');

/** 按优先级挑一个可用的 Chromium 内核浏览器。 */
export function findChrome() {
  const candidates = [
    process.env.AEGIS_CHROME_PATH,
    process.env.CHROME_PATH,
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    path.join(process.env.LOCALAPPDATA || '', 'Google\\Chrome\\Application\\chrome.exe'),
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
  ].filter(Boolean);
  for (const c of candidates) if (existsSync(c)) return c;
  return null;
}

async function getJson(url, timeoutMs = 1500) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const r = await fetch(url, { signal: ac.signal });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}

/** 轮询直到 fn() 返回真值,否则抛错(错误里带 label,便于报告归因)。 */
export async function waitFor(fn, { timeoutMs = 15000, intervalMs = 200, label = 'condition' } = {}) {
  const t0 = Date.now();
  let last;
  while (Date.now() - t0 < timeoutMs) {
    last = await fn();
    if (last) return last;
    await sleep(intervalMs);
  }
  throw new Error(`waitFor 超时(${timeoutMs}ms): ${label}`);
}

/** 每个测试会话用独立的日志缓冲,便于把 Chrome 的 stderr 原文带进报告。 */
function makeLogBuf(limit = 20000) {
  let s = '';
  return {
    push(chunk) {
      s += chunk.toString('utf8');
      if (s.length > limit) s = s.slice(-limit);
    },
    get value() {
      return s;
    },
  };
}

class CdpSession {
  constructor(wsUrl, target = {}) {
    this.wsUrl = wsUrl;
    this.target = target;
    this.nextId = 0;
    this.pending = new Map();
    this.listeners = new Map();
    this.requests = []; // Network.requestWillBeSent 记录(仅元数据)
    this.docResponse = null; // 主文档响应(revision 归因用)
    this.consoleErrors = [];
    this.closed = false;
  }

  async connect({ enableNetwork = true } = {}) {
    this.ws = new WebSocket(this.wsUrl);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('WebSocket 连接超时(10s): ' + this.wsUrl)), 10000);
      this.ws.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
      this.ws.addEventListener('error', () => { clearTimeout(timer); reject(new Error('WebSocket 连接失败: ' + this.wsUrl)); }, { once: true });
    });
    this.ws.addEventListener('message', (ev) => this._onMessage(ev.data));
    this.ws.addEventListener('close', () => {
      this.closed = true;
      for (const p of this.pending.values()) p.reject(new Error('WebSocket 已关闭,未收到响应: ' + p.method));
      this.pending.clear();
    });
    await this.send('Page.enable');
    await this.send('Runtime.enable');
    if (enableNetwork) await this.send('Network.enable');
    this.on('Network.requestWillBeSent', (p) => {
      this.requests.push({ method: p.request?.method, url: p.request?.url, type: p.type, ts: Date.now() });
    });
    this.on('Network.responseReceived', (p) => {
      if (p.type === 'Document' && p.response) {
        this.docResponse = { requestId: p.requestId, url: p.response.url, status: p.response.status, mimeType: p.response.mimeType };
      }
    });
    this.on('Runtime.consoleAPICalled', (p) => {
      if (p.type === 'error') {
        this.consoleErrors.push((p.args || []).map((a) => a.value ?? a.description ?? '').join(' ').slice(0, 300));
      }
    });
    return this;
  }

  _onMessage(data) {
    let msg;
    try {
      msg = JSON.parse(typeof data === 'string' ? data : data.toString());
    } catch {
      return;
    }
    if (msg.id != null) {
      const p = this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      clearTimeout(p.timer);
      if (msg.error) p.reject(new Error(`${p.method} 失败: ${msg.error.message}`));
      else p.resolve(msg.result);
      return;
    }
    const hs = this.listeners.get(msg.method);
    if (hs) for (const h of hs) { try { h(msg.params || {}); } catch { /* 监听器异常不影响主流程 */ } }
  }

  on(method, handler) {
    if (!this.listeners.has(method)) this.listeners.set(method, []);
    this.listeners.get(method).push(handler);
    return this;
  }

  send(method, params = {}, { timeoutMs = 30000 } = {}) {
    if (this.closed) return Promise.reject(new Error('会话已关闭,无法发送: ' + method));
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP ${method} 超时(${timeoutMs}ms)`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, method, timer });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  /** 在页面里求值;抛异常时把页面错误原文带出来。 */
  async evaluate(expression, { awaitPromise = true, returnByValue = true, timeoutMs = 30000 } = {}) {
    const res = await this.send('Runtime.evaluate', { expression, awaitPromise, returnByValue, userGesture: false }, { timeoutMs });
    if (res.exceptionDetails) {
      const d = res.exceptionDetails;
      const detail = d.exception?.description || d.exception?.value || d.text || 'unknown';
      const err = new Error('页面求值抛异常: ' + String(detail).split('\n')[0]);
      err.pageException = d;
      throw err;
    }
    return res.result?.value;
  }

  async setViewport(width, height, deviceScaleFactor = 1) {
    await this.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor, mobile: false });
  }

  async navigate(url, { timeoutMs = 30000 } = {}) {
    await this.send('Page.navigate', { url });
    await waitFor(async () => (await this.evaluate('document.readyState')) === 'complete', {
      timeoutMs, intervalMs: 200, label: `document.readyState==='complete' @ ${url}`,
    });
  }

  async reload({ timeoutMs = 30000 } = {}) {
    await this.send('Page.reload', { ignoreCache: true });
    await waitFor(async () => (await this.evaluate('document.readyState')) === 'complete', {
      timeoutMs, intervalMs: 200, label: 'reload 后 readyState complete',
    });
  }

  async screenshot(filePath) {
    mkdirSync(path.dirname(filePath), { recursive: true });
    const r = await this.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    const buf = Buffer.from(r.data, 'base64');
    writeFileSync(filePath, buf);
    return { path: filePath, bytes: buf.length };
  }

  /** 点坐标处元素是谁 —— 用于记录「我到底点了哪个控件」,并拦住任何指向 LIVE 的点击。 */
  async elementAtPoint(x, y) {
    return this.evaluate(`(() => {
      const el = document.elementFromPoint(${Math.round(x)}, ${Math.round(y)});
      if (!el) return null;
      const live = el.closest ? el.closest('#btn-live') : null;
      return {
        tag: el.tagName, id: el.id || '', cls: el.className ? String(el.className) : '',
        text: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
        insideLiveButton: !!live
      };
    })()`);
  }

  /** 真鼠标点击(CSS 像素、视口坐标)。默认拒绝点到 LIVE 按钮上。 */
  async clickAt(x, y, { expectNotLive = true, settleMs = 120 } = {}) {
    const cx = Math.round(x), cy = Math.round(y);
    const who = await this.elementAtPoint(cx, cy);
    if (expectNotLive && who?.insideLiveButton) {
      throw new Error(`拒绝对 LIVE 按钮发起点击(x=${cx}, y=${cy})——本轮禁止触发真实对抗`);
    }
    await this.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: cx, y: cy, buttons: 0 });
    await this.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: cx, y: cy, button: 'left', buttons: 1, clickCount: 1 });
    await sleep(40);
    await this.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: cx, y: cy, button: 'left', buttons: 0, clickCount: 1 });
    await sleep(settleMs);
    return { x: cx, y: cy, elementAtPoint: who };
  }

  /** 取元素中心点后真点击;返回落点与元素身份。 */
  async clickSelector(selector, opts = {}) {
    const box = await this.evaluate(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return null;
      el.scrollIntoView({ block: 'center' });
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return {
        x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
        w: Math.round(r.width), h: Math.round(r.height),
        visible: cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0,
        disabled: !!el.disabled,
        text: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 60)
      };
    })()`);
    if (!box) throw new Error('找不到元素: ' + selector);
    if (!box.visible) throw new Error('元素不可见,拒绝点击: ' + selector);
    if (box.disabled) throw new Error('元素被禁用,拒绝点击: ' + selector);
    const click = await this.clickAt(box.x, box.y, opts);
    return { selector, ...box, ...click };
  }

  /**
   * 取本次页面加载**实际收到的主文档字节**的 sha256。
   * 为什么必须这样做:被测前端正在被并发修改,文件级哈希只能告诉你「现在磁盘上是什么」,
   * 不能告诉你「浏览器这一帧跑的是哪一份」。这里用 Network.getResponseBody 拿加载时的响应体,
   * 是精确的归因依据。
   */
  async servedDocumentSha256() {
    if (!this.docResponse) return { error: 'unavailable: 未捕获到 Document 响应' };
    try {
      const r = await this.send('Network.getResponseBody', { requestId: this.docResponse.requestId }, { timeoutMs: 10000 });
      const buf = r.base64Encoded ? Buffer.from(r.body, 'base64') : Buffer.from(r.body, 'utf8');
      return {
        sha256: createHash('sha256').update(buf).digest('hex'),
        bytes: buf.length,
        url: this.docResponse.url,
        httpStatus: this.docResponse.status,
        capturedAt: new Date().toISOString(),
      };
    } catch (e) {
      return { error: 'unavailable: ' + String(e.message || e), url: this.docResponse.url };
    }
  }

  /** 非 GET 请求清单 —— 只读红线守卫。 */
  writeRequests() {
    return this.requests.filter((r) => r.method && !['GET', 'HEAD', 'OPTIONS'].includes(r.method));
  }

  requestSummary() {
    const byMethod = {};
    for (const r of this.requests) byMethod[r.method] = (byMethod[r.method] || 0) + 1;
    const urls = [...new Set(this.requests.map((r) => r.url))].slice(0, 40);
    return { total: this.requests.length, byMethod, sampleUrls: urls };
  }
}

export class Browser {
  constructor({ exe, port, child, version, logBuf, userDataDir, reused }) {
    this.exe = exe;
    this.port = port;
    this.child = child;
    this.version = version;
    this.logBuf = logBuf;
    this.userDataDir = userDataDir;
    this.reused = reused;
    this.session = null;
  }

  info() {
    return {
      exe: this.exe,
      port: this.port,
      reusedExistingInstance: this.reused,
      userDataDir: this.userDataDir,
      browser: this.version?.Browser || null,
      protocolVersion: this.version?.['Protocol-Version'] || null,
      userAgent: this.version?.['User-Agent'] || null,
      launchFlags: LAUNCH_FLAGS_TEMPLATE(this.port, this.userDataDir),
    };
  }

  /** 连上（或新建）一个 page target 的会话。 */
  async openPage({ enableNetwork = true } = {}) {
    const targets = await waitFor(async () => {
      const list = await getJson(`http://127.0.0.1:${this.port}/json/list`);
      const pages = (list || []).filter((t) => t.type === 'page' && t.webSocketDebuggerUrl);
      return pages.length ? pages : null;
    }, { timeoutMs: 15000, intervalMs: 200, label: 'page target ( /json/list )' });
    const target = targets.find((t) => t.url && t.url.startsWith('about:')) || targets[0];
    const session = new CdpSession(target.webSocketDebuggerUrl, { id: target.id, url: target.url, type: target.type });
    await session.connect({ enableNetwork });
    this.session = session;
    return session;
  }

  async close() {
    const out = { browserClosed: false, closeMethod: null, error: null };
    try {
      if (this.session && !this.session.closed) {
        await this.session.send('Browser.close', {}, { timeoutMs: 5000 });
        out.browserClosed = true;
        out.closeMethod = 'CDP Browser.close';
      }
    } catch (e) {
      out.error = String(e.message || e);
    }
    await sleep(600);
    if (this.child && this.child.exitCode == null) {
      try {
        this.child.kill();
        out.closeMethod = out.closeMethod || 'child.kill()';
        await sleep(400);
        if (this.child.exitCode == null) {
          this.child.kill('SIGKILL');
          out.closeMethod += ' + SIGKILL';
        }
        out.browserClosed = true;
      } catch (e) {
        out.error = (out.error ? out.error + ' | ' : '') + String(e.message || e);
      }
    }
    if (this.child && this.child.exitCode != null) out.exitCode = this.child.exitCode;
    return out;
  }
}

const LAUNCH_FLAGS_TEMPLATE = (port, userDataDir) => ([
  '--headless=new',
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${userDataDir}`,
  '--window-size=1920,1080',
  '--disable-gpu',
  '--no-first-run',
  '--no-default-browser-check',
  'about:blank',
]);

/**
 * 启动 Chrome(若 9333 上已有实例则复用并在报告里标注)。
 * 抛错时错误对象带 chromeStderr 原文,便于如实报告启动失败。
 */
export async function launchBrowser({
  port = DEFAULT_PORT,
  userDataDir = DEFAULT_USER_DATA_DIR,
  windowSize = '1920,1080',
  extraArgs = [],
  exePath = null,
} = {}) {
  const probe = await getJson(`http://127.0.0.1:${port}/json/version`, 1200);
  if (probe) {
    return new Browser({ exe: exePath || findChrome(), port, child: null, version: probe, logBuf: makeLogBuf(), userDataDir, reused: true });
  }

  const exe = exePath || findChrome();
  if (!exe) {
    const err = new Error('未找到 Chrome/Edge 可执行文件(已检查 CHROME_PATH/AEGIS_CHROME_PATH 与标准安装路径)');
    err.allCandidates = 'see findChrome()';
    throw err;
  }
  mkdirSync(userDataDir, { recursive: true });

  const args = [
    '--headless=new',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${userDataDir}`,
    `--window-size=${windowSize}`,
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    '--disable-sync',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-features=Translate,MediaRouter',
    ...extraArgs,
    'about:blank',
  ];
  const logBuf = makeLogBuf();
  const child = spawn(exe, args, { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  child.stdout?.on('data', (c) => logBuf.push(c));
  child.stderr?.on('data', (c) => logBuf.push(c));
  let exited = null;
  child.on('exit', (code, signal) => { exited = { code, signal }; });

  let version = null;
  try {
    version = await waitFor(() => getJson(`http://127.0.0.1:${port}/json/version`, 1200), {
      timeoutMs: 30000, intervalMs: 300, label: `/json/version @ port ${port}`,
    });
  } catch (e) {
    const err = new Error(`Chrome 启动失败: /json/version 在 30s 内不可达(${e.message})`);
    err.chromeStderr = logBuf.value;
    err.exited = exited;
    err.command = `"${exe}" ${args.map((a) => (a.includes(' ') ? `"${a}"` : a)).join(' ')}`;
    throw err;
  }
  if (exited && exited.code != null) {
    const err = new Error(`Chrome 进程已退出(code=${exited.code})但调试端口一度可达,实例不可信`);
    err.chromeStderr = logBuf.value;
    err.exited = exited;
    throw err;
  }
  return new Browser({ exe, port, child, version, logBuf, userDataDir, reused: false });
}

/** 只读 GET(驱动自身取地面真值用)。 */
export async function apiGet(baseUrl, route) {
  const url = baseUrl.replace(/\/$/, '') + route;
  const r = await fetch(url, { method: 'GET' });
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* 非 JSON 时保留原文 */ }
  return { url, status: r.status, ok: r.ok, json, text: text.slice(0, 4000) };
}

/* Aegis 演示大屏 · 真运行态测试环境(node vm,零 npm 依赖)
 *
 * 来历:计划书 §9 要求「浏览器测试至少覆盖 A08–A14,不能仅检查 DOM 中有按钮和标题」。
 * 之前只有 HTML 字符串/正则断言,测不出字段漂移与状态机行为(见 T7 偏离审计)。
 *
 * 做法:从 demo/index.html 抽出内联脚本,在 vm 上下文里用桩 DOM + 桩 fetch +
 * 桩 EventSource **真实执行前端函数体**,再驱动状态机、断言可观测结果。
 * 注意:这不是浏览器渲染,不验布局/像素;只验数据层与状态机行为。
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function makeEl(tag) {
  const el = {
    tagName: tag || "div",
    children_: [],
    style: {},
    dataset: {},
    _text: "",
    _html: "",
    _cls: new Set(),
    value: "",
    title: "",
    disabled: false,
    scrollHeight: 0,
    scrollTop: 0,
    clientHeight: 0,
    onclick: null,
  };
  Object.defineProperty(el, "textContent", {
    get() {
      const kids = el.children_.map(c => c.textContent || "").join("");
      if (kids) return (el._text || "") + kids;
      // 无子节点但用了 innerHTML 渲染时,返回去标签文本(断言需要)
      return (el._text || "") + String(el._html || "").replace(/<[^>]*>/g, " ");
    },
    set(v) { el.children_ = []; el._html = ""; el._text = String(v == null ? "" : v); },
  });
  Object.defineProperty(el, "innerHTML", {
    get() { return el._html; },
    set(v) { el._html = String(v == null ? "" : v); el._text = ""; el.children_ = []; },
  });
  Object.defineProperty(el, "className", {
    get() { return Array.from(el._cls).join(" "); },
    set(v) { el._cls = new Set(String(v || "").split(/\s+/).filter(Boolean)); },
  });
  Object.defineProperty(el, "firstChild", { get() { return el.children_[0] || null; } });
  Object.defineProperty(el, "lastChild", { get() { return el.children_[el.children_.length - 1] || null; } });
  Object.defineProperty(el, "children", { get() { return el.children_; } });
  el.classList = {
    add: (...c) => c.forEach(x => el._cls.add(x)),
    remove: (...c) => c.forEach(x => el._cls.delete(x)),
    contains: c => el._cls.has(c),
    toggle: c => (el._cls.has(c) ? el._cls.delete(c) : el._cls.add(c)),
  };
  el.appendChild = c => { el.children_.push(c); return c; };
  el.insertBefore = c => { el.children_.unshift(c); return c; };
  el.removeChild = c => {
    const i = el.children_.indexOf(c);
    if (i >= 0) el.children_.splice(i, 1);
    return c;
  };
  el.remove = () => {};
  el.addEventListener = () => {};
  el.removeEventListener = () => {};
  el.querySelector = () => null;
  el.querySelectorAll = () => [];
  el.setAttribute = (k, v) => { if (k === "class") el.className = v; };
  el.getAttribute = () => null;
  el.focus = () => {};
  el.scrollIntoView = () => {};
  return el;
}

function createEnv(opts) {
  opts = opts || {};
  const root = opts.root || path.resolve(__dirname, "..", "..");
  const html = fs.readFileSync(path.join(root, "demo", "index.html"), "utf8");
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const ids = Array.from(html.matchAll(/id="([^"]+)"/g)).map(m => m[1]);

  const byId = {};
  ids.forEach(id => { const e = makeEl("div"); e.id = id; byId[id] = e; });
  const body = makeEl("body");
  const document = {
    body,
    querySelector(sel) {
      if (typeof sel === "string" && sel.charAt(0) === "#") return byId[sel.slice(1)] || null;
      return makeEl("div");
    },
    getElementById(id) { return byId[id] || null; },
    createElement(t) { return makeEl(t); },
    createTextNode(t) { const e = makeEl("#text"); e._text = String(t); return e; },
    addEventListener() {},
    querySelectorAll() { return []; },
    documentElement: makeEl("html"),
  };

  // ---- fetch 桩:按 URL 路由,记录全部调用 ----
  const calls = [];
  let router = opts.router || (() => ({ status: 200, body: {} }));  function mkResponse(url, r) {
    const body = r && "body" in r ? r.body : {};
    return {
      ok: (r.status || 200) >= 200 && (r.status || 200) < 300,
      status: r.status || 200,
      url,
      headers: { get: () => null },
      json: async () => body,
      text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
      clone() { return mkResponse(url, r); },
    };
  }
  async function fetchStub(url, init) {
    const method = (init && init.method) || "GET";
    const entry = { url: String(url), method };
    calls.push(entry);
    const r = router(String(url), method, init) || { status: 200, body: {} };
    if (r instanceof Error) throw r;
    if (r.throw) throw new Error(r.throw);
    if (r.delayMs) await new Promise(res => setTimeoutReal(res, r.delayMs));
    if (r.onCall) r.onCall(entry);
    return mkResponse(url, r);
  }

  // ---- 定时器桩:登记 + 手动触发 ----
  const timers = [];
  let timerSeq = 1;
  const setTimeoutReal = opts.realSetTimeout || global.setTimeout;
  const setIntervalStub = (fn, ms) => { const id = timerSeq++; timers.push({ id, fn, ms, kind: "interval" }); return id; };
  const setTimeoutStub = (fn, ms) => { const id = timerSeq++; timers.push({ id, fn, ms, kind: "timeout" }); return id; };
  const clearStub = id => { const i = timers.findIndex(t => t.id === id); if (i >= 0) timers.splice(i, 1); };

  // ---- EventSource 桩 ----
  const sources = [];
  class EventSourceStub {
    constructor(url) { this.url = String(url); this._h = {}; sources.push(this); }
    addEventListener(t, fn) { (this._h[t] = this._h[t] || []).push(fn); }
    removeEventListener() {}
    close() { this.closed = true; }
    fire(type, data) { (this._h[type] || []).forEach(fn => fn({ data: typeof data === "string" ? data : JSON.stringify(data) })); }
    error() { if (this.onerror) this.onerror({}); }
  }

  const nav = { clipboard: { writeText: async () => {} } };
  const ctx = {
    console,
    document,
    fetch: fetchStub,
    EventSource: EventSourceStub,
    navigator: nav,
    location: { href: "http://127.0.0.1:8000/demo", hash: "" },
    setInterval: setIntervalStub,
    clearInterval: clearStub,
    setTimeout: setTimeoutStub,
    clearTimeout: clearStub,
    requestAnimationFrame: fn => setTimeoutStub(fn, 16),
    addEventListener: () => {},
    Date, JSON, Math, Promise, Object, Array, String, Number, Boolean, Error,
    isFinite, parseInt, parseFloat, encodeURIComponent, decodeURIComponent, RegExp, Set, Map,
  };
  ctx.window = ctx;
  ctx.globalThis = ctx;

  const context = vm.createContext(ctx);
  vm.runInContext(script, context, { filename: "demo/index.html<script>" });

  return {
    ctx: context,
    byId,
    document,
    calls,
    timers,
    sources,
    setRouter: fn => { router = fn; },
    resetCalls: () => { calls.length = 0; },
    evalIn: expr => vm.runInContext(expr, context),
    state: () => JSON.parse(vm.runInContext(
      'JSON.stringify({mode:_mode,run:_run,lastSeq:_lastSeq,findings:Object.keys(_findings).length})',
      context)),
    tickAll: async (kind) => {
      const snapshot = timers.filter(t => !kind || t.kind === kind);
      for (const t of snapshot) await t.fn();
    },
    writes: () => calls.filter(c => c.method !== "GET"),
    settle: async () => { for (let i = 0; i < 8; i++) await new Promise(r => setTimeoutReal(r, 0)); },
  };
}

module.exports = { createEnv, makeEl };

/*
 * _ui_harness.js — self-contained DOM + fetch shim to execute the REAL
 * frontend/index.html <script> in Node (no jsdom/playwright/network needed)
 * and drive the custom-questions UI contract scenarios CU-05, CU-08..CU-18.
 *
 * It builds a minimal-but-sufficient DOM, a controllable fetch that mimics the
 * FastAPI web layer's contract (bare-array list, raw-record detail, status
 * codes, status-driven create/confirm/precheck), loads the page script, and
 * returns a JSON object of raw observations for the Python unittest to assert.
 *
 * Usage:  node _ui_harness.js <path-to-frontend/index.html>
 * Output: a single JSON object printed to stdout.
 */
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const INDEX = process.argv[2] || path.resolve(__dirname, '..', '..', 'frontend', 'index.html');
const RAW = fs.readFileSync(INDEX, 'utf8');
const M = RAW.match(/<script>([\s\S]*?)<\/script>/);
if (!M) {
  process.stdout.write(JSON.stringify({ __fatal: 'NO_SCRIPT_BLOCK_FOUND' }));
  process.exit(2);
}
const SCRIPT = M[1];

/* -------------------------------------------------------------------------- */
/* Minimal DOM                                                               */
/* -------------------------------------------------------------------------- */
class Node {
  constructor(tag) {
    this.tagName = (tag || 'div').toUpperCase();
    this._classes = new Set();
    this._children = [];
    this._listeners = {};
    this._innerHTML = '';
    this._text = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.id = '';
    this.dataset = {};
    this.style = {};
    this._classList = null;
  }
  get className() { return [...this._classes].join(' '); }
  set className(v) { this._classes = new Set(String(v || '').split(/\s+/).filter(Boolean)); }
  get classList() {
    if (!this._classList) {
      const self = this;
      this._classList = {
        add: (...c) => c.forEach((x) => self._classes.add(x)),
        remove: (...c) => c.forEach((x) => self._classes.delete(x)),
        toggle: (c, force) => {
          const has = self._classes.has(c);
          const want = force === undefined ? !has : !!force;
          if (want) self._classes.add(c); else self._classes.delete(c);
          return want;
        },
        contains: (c) => self._classes.has(c),
      };
    }
    return this._classList;
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) { this._innerHTML = String(v == null ? '' : v); }
  get textContent() { return this._text || this._innerHTML; }
  set textContent(v) { this._text = String(v == null ? '' : v); }
  appendChild(child) { if (child) { child._parent = this; this._children.push(child); } return child; }
  append(...kids) { kids.forEach((k) => this.appendChild(k)); }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  click() {
    const ls = this._listeners['click'] || [];
    const ev = { preventDefault() {}, key: undefined, ctrlKey: false, metaKey: false };
    return Promise.all(ls.map((fn) => fn(ev)));
  }
  _walk(out, cls) {
    for (const c of this._children) {
      if (cls && c._classes.has(cls)) out.push(c);
      c._walk(out, cls);
    }
  }
  querySelectorAll(sel) {
    const cls = sel.startsWith('.') ? sel.slice(1) : null;
    const out = [];
    this._walk(out, cls);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

class Document {
  constructor() {
    this._byId = {};
    this._tabs = [];
    ['problems', 'gocode', 'custom', 'detail'].forEach((name, i) => {
      const t = new Node('button');
      t.className = 'tab' + (i === 0 ? ' active' : '');
      t.dataset.tab = name;
      this._tabs.push(t);
    });
  }
  getElementById(id) {
    if (!this._byId[id]) { const e = new Node('div'); e.id = id; this._byId[id] = e; }
    return this._byId[id];
  }
  createElement(tag) { return new Node(tag); }
  querySelectorAll(sel) { if (sel === '.tab') return this._tabs.slice(); return []; }
  querySelector(sel) {
    if (sel === '.tab.active') return this._tabs.find((t) => t._classes.has('active')) || this._tabs[0];
    if (sel === '.tab') return this._tabs[0];
    return null;
  }
}

/* -------------------------------------------------------------------------- */
/* Controllable fetch (mimics the FastAPI web contract)                      */
/* -------------------------------------------------------------------------- */
function route(config, method, path, body) {
  if (method === 'GET' && path === '/api/custom-questions') {
    return { ok: true, status: 200, json: config.list !== undefined ? config.list : [] };
  }
  if (method === 'GET' && path.startsWith('/api/custom-questions/')) {
    const num = decodeURIComponent(path.slice('/api/custom-questions/'.length));
    if (num === 'C-1') return { ok: false, status: 404, json: { detail: 'not found' } };
    return {
      ok: true, status: 200,
      json: config.detail !== undefined ? config.detail : {
        number: num, source: 'custom', created_at: '2026-01-01T00:00:00',
        input_question: 'default', category: 'coding', task_dir: 'x',
        code_path: '/out/go-code/x/x.go', build_result: 'DEFAULT_BUILD', final_output: null,
      },
    };
  }
  if (method === 'POST' && path === '/api/custom-questions') {
    // Mirror real behavior: no_confirm short-circuits to created.
    if (body && body.no_confirm) return { ok: true, status: 200, json: { status: 'created', number: 'C-9999' } };
    return { ok: true, status: 200, json: config.create !== undefined ? config.create : { status: 'created', number: 'C-1234' } };
  }
  if (method === 'POST' && path === '/api/custom-questions/confirm') {
    return { ok: true, status: 200, json: config.confirm !== undefined ? config.confirm : { status: 'reused', number: null, matched_slug: body ? body.matched_slug : null } };
  }
  if (method === 'POST' && path === '/api/custom-questions/precheck') {
    return { ok: true, status: 200, json: config.precheck !== undefined ? config.precheck : { status: 'no_match', matched_slug: null, reason: '' } };
  }
  if (method === 'GET' && path === '/api/problems') return { ok: true, status: 200, json: { items: [], total: 0 } };
  if (method === 'GET' && path === '/api/stats') return { ok: true, status: 200, json: { tags: [] } };
  if (method === 'GET' && path === '/api/go-code') return { ok: true, status: 200, json: { items: [], total: 0 } };
  if (method === 'GET' && path.startsWith('/api/go-code/')) return { ok: true, status: 200, json: { task_name: 'x', rel_path: '', size_bytes: 0, line_count: 0, content: 'package main', related_problem: null } };
  return { ok: true, status: 200, json: {} };
}

function makeFetch(config, CALLS) {
  return async (url, opts) => {
    const u = new URL(url, 'http://localhost/');
    const p = u.pathname;
    const method = (opts && opts.method) || 'GET';
    let body = null;
    if (opts && opts.body) { try { body = JSON.parse(opts.body); } catch (e) { body = opts.body; } }
    CALLS.push({ method, path: p, body });
    const res = route(config, method, p, body);
    return {
      ok: res.ok !== false,
      status: res.status,
      json: async () => res.json,
      text: async () => JSON.stringify(res.json),
    };
  };
}

/* -------------------------------------------------------------------------- */
/* Driver helpers                                                            */
/* -------------------------------------------------------------------------- */
const delay = (ms) => new Promise((r) => setTimeout(r, ms));
const $ = (sb, id) => sb.document.getElementById(id);
const setVal = (sb, id, v) => { const e = $(sb, id); if (e) e.value = v; };
const setChecked = (sb, id, v) => { const e = $(sb, id); if (e) e.checked = v; };
const html = (sb, id) => { const e = $(sb, id); return e ? e.innerHTML : null; };
const click = async (sb, id) => { const e = $(sb, id); if (e && e.click) await e.click(); };
const cardCount = (sb, id) => { const e = $(sb, id); return e ? e.querySelectorAll('.card').length : -1; };
const findCall = (CALLS, method, path) => CALLS.find((c) => c.method === method && c.path === path);

/* -------------------------------------------------------------------------- */
/* Scenarios                                                                 */
/* -------------------------------------------------------------------------- */
const SCENARIOS = {
  'CU-05': async (sb, cfg, CALLS) => {
    setVal(sb, 'c-text', '用 Go 实现 LRU Cache');
    CALLS.length = 0;
    await click(sb, 'c-submit');
    const c = findCall(CALLS, 'POST', '/api/custom-questions');
    return { body: c ? c.body : null };
  },
  'CU-08': async (sb, cfg, CALLS) => {
    cfg.create = { status: 'needs_confirm', matched_slug: 'two-sum', reason: '与已有题目相似' };
    setVal(sb, 'c-text', '用 Go 实现 LRU');
    CALLS.length = 0;
    await click(sb, 'c-submit');
    return { confirmHTML: html(sb, 'custom-confirm') };
  },
  'CU-09': async (sb, cfg, CALLS) => {
    cfg.create = { status: 'needs_confirm', matched_slug: 'two-sum', reason: '相似' };
    setVal(sb, 'c-text', '用 Go 实现 LRU');
    await click(sb, 'c-submit');
    CALLS.length = 0;
    await click(sb, 'c-reuse');
    const c = findCall(CALLS, 'POST', '/api/custom-questions/confirm');
    return { body: c ? c.body : null };
  },
  'CU-10': async (sb, cfg, CALLS) => {
    cfg.create = { status: 'needs_confirm', matched_slug: 'two-sum', reason: '相似' };
    setVal(sb, 'c-text', '用 Go 实现 LRU');
    await click(sb, 'c-submit');
    CALLS.length = 0;
    await click(sb, 'c-new');
    const c = findCall(CALLS, 'POST', '/api/custom-questions/confirm');
    return { body: c ? c.body : null };
  },
  'CU-11': async (sb, cfg, CALLS) => {
    setChecked(sb, 'c-no-confirm', true);
    setVal(sb, 'c-text', '用 Go 实现 LRU');
    CALLS.length = 0;
    await click(sb, 'c-submit');
    return { confirmHTML: html(sb, 'custom-confirm') };
  },
  'CU-13': async (sb, cfg, CALLS) => {
    cfg.list = [];
    await sb.__exports.loadCustom();
    const emptyHTML = html(sb, 'custom-list');
    const emptyCards = cardCount(sb, 'custom-list');
    cfg.list = [
      { number: 'C-0001', source: 'custom', created_at: '2026-01-01T00:00:00', category: 'coding', task_dir: 't1', has_code: true, title: '题一' },
      { number: 'C-0002', source: 'custom', created_at: '2026-01-02T00:00:00', category: 'general', task_dir: null, has_code: false, title: '题二' },
    ];
    await sb.__exports.loadCustom();
    return { emptyHTML, emptyCards, listCards: cardCount(sb, 'custom-list'), listHTML: html(sb, 'custom-list') };
  },
  'CU-14': async (sb, cfg, CALLS) => {
    cfg.list = [];
    await sb.__exports.loadCustom();
    return { emptyHTML: html(sb, 'custom-list') };
  },
  'CU-15': async (sb, cfg, CALLS) => {
    cfg.detail = {
      number: 'C-1234', source: 'custom', created_at: '2026-01-01T00:00:00',
      input_question: '用 Go 实现 LRU', category: 'coding', task_dir: 'lru',
      code_path: '/out/go-code/lru/lru.go', build_result: 'CODING_BUILD_MARKER', final_output: null,
    };
    await sb.__exports.openCustom('C-1234');
    return { detailHTML: html(sb, 'detail-content') };
  },
  'CU-16': async (sb, cfg, CALLS) => {
    cfg.detail = {
      number: 'C-1234', source: 'custom', created_at: '2026-01-01T00:00:00',
      input_question: '解释 TCP 三次握手', category: 'general', task_dir: null,
      code_path: null, build_result: null, final_output: 'NONCODING_MARKER_最终输出',
    };
    await sb.__exports.openCustom('C-1234');
    return { detailHTML: html(sb, 'detail-content') };
  },
  'CU-17': async (sb, cfg, CALLS) => {
    let threw = false, errMsg = null;
    try { await sb.__exports.openCustom('C-1'); }
    catch (e) { threw = true; errMsg = String((e && e.stack) || e); }
    return { detailHTML: html(sb, 'detail-content'), threw, errMsg };
  },
  'CU-18': async (sb, cfg, CALLS) => {
    cfg.detail = {
      number: 'C-1234', source: 'custom', created_at: '2026-01-01T00:00:00',
      input_question: 'xss', category: 'general', task_dir: null,
      code_path: null, final_output: '<script>alert(1)</script>',
    };
    await sb.__exports.openCustom('C-1234');
    return { detailHTML: html(sb, 'detail-content') };
  },
  'CU-19': async (sb, cfg, CALLS) => {
    // Regression for the `goTask is not defined` scope bug: rendering a CODING
    // detail must attach a working `#c-to-gocode` click handler that calls
    // openGoCode(<task_name>) — i.e. goTask must be visible outside the
    // `if (rec.code_path)` block. Click the button and assert no ReferenceError
    // and that fetch hits /api/go-code/<task_dir>.
    cfg.detail = {
      number: 'C-1234', source: 'custom', created_at: '2026-01-01T00:00:00',
      input_question: '用 Go 实现 LRU', category: 'coding', task_dir: 'custom_task_x',
      code_path: '/out/go-code/custom_task_x/custom_task_x.go', build_result: 'CU19_BUILD', final_output: null,
    };
    CALLS.length = 0;
    await sb.__exports.openCustom('C-1234');
    const detailHTML = html(sb, 'detail-content');
    const btn = sb.document.getElementById('c-to-gocode');
    const btnExists = !!(btn && btn._listeners && btn._listeners['click']);
    let threw = false, errMsg = null, calledGoCode = null;
    try {
      await click(sb, 'c-to-gocode');
      const c = CALLS.find((x) => x.method === 'GET' && x.path.startsWith('/api/go-code/'));
      calledGoCode = c ? c.path : null;
    } catch (e) {
      threw = true;
      errMsg = String((e && e.stack) || e);
    }
    return { detailHTML, btnExists, threw, errMsg, calledGoCode };
  },
};

function baseConfig() {
  return {
    list: [],
    create: { status: 'created', number: 'C-1234' },
    detail: null,
    confirm: null,
    precheck: null,
  };
}

async function runScenario(name) {
  const config = baseConfig();
  const document = new Document();
  const CALLS = [];
  const fetchFn = makeFetch(config, CALLS);
  const sandbox = {
    document, fetch: fetchFn,
    location: { protocol: 'http:', href: 'http://localhost/' },
    alert: () => {},
    console,
    URL, encodeURIComponent, JSON, Math, Object, Array, String, Number, Boolean,
    Promise, setTimeout, clearTimeout, RegExp, Error, Date, parseInt, parseFloat, isNaN,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  const code = SCRIPT +
    '\n;globalThis.__exports={loadCustom:loadCustom,openCustom:openCustom,submitCustom:submitCustom,' +
    'precheckCustom:precheckCustom,renderConfirmPanel:renderConfirmPanel,resolveConfirm:resolveConfirm};';
  vm.runInContext(code, sandbox, { filename: 'frontend/index.html#script' });
  await delay(60); // let the init() IIFE settle
  const obs = await SCENARIOS[name](sandbox, config, CALLS);
  obs.__calls = CALLS.slice();
  return obs;
}

(async () => {
  const names = ['CU-05', 'CU-08', 'CU-09', 'CU-10', 'CU-11', 'CU-13', 'CU-14', 'CU-15', 'CU-16', 'CU-17', 'CU-18', 'CU-19'];
  const results = {};
  for (const name of names) {
    try {
      results[name] = await runScenario(name);
    } catch (e) {
      results[name] = { __error: String((e && e.stack) || e) };
    }
  }
  process.stdout.write(JSON.stringify(results));
})();

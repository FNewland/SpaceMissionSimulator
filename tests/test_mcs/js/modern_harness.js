// modern_harness.js — run the MODERN MCS page module (static/modern/mcs.js)
// under Node.
//
// Unlike the classic page (one inline <script>, driven by mcs_harness.js —
// which this file deliberately does NOT touch), the modern page is a real ES
// module importing the frozen shared vocabulary from /static/shared/. This
// harness:
//   1. copies components.js + format.js (smo-common) and mcs.js into a temp
//      dir, rewriting the absolute /static/shared/ import specifiers to
//      relative ones;
//   2. installs a small DOM stub + fetch/localStorage/WebSocket stubs as
//      globals and sets globalThis.__MCS_TEST__ so the module does not boot;
//   3. dynamically imports the module and runs the caller's snippet with the
//      module's `__test` export in scope as `T` (plus `document`,
//      `__registry`, `__fetchLog`);
//   4. prints the snippet's (possibly awaited) return value as JSON.
//
//   node modern_harness.js <repo-root> <path-to-snippet.js>
//
// It is NOT a browser: layout, CSS, and real event dispatch are out of scope.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { pathToFileURL } = require('url');

const repo = process.argv[2];
const snippetPath = process.argv[3];

const SHARED = path.join(repo, 'packages', 'smo-common', 'src', 'smo_common', 'ui');
const MODERN = path.join(repo, 'packages', 'smo-mcs', 'src', 'smo_mcs', 'static', 'modern');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mcs-modern-'));
fs.copyFileSync(path.join(SHARED, 'components.js'), path.join(tmp, 'components.js'));
fs.copyFileSync(path.join(SHARED, 'format.js'), path.join(tmp, 'format.js'));
let src = fs.readFileSync(path.join(MODERN, 'mcs.js'), 'utf8');
src = src.replace(/["']\/static\/shared\/components\.js["']/g, '"./components.js"')
         .replace(/["']\/static\/shared\/format\.js["']/g, '"./format.js"');
fs.writeFileSync(path.join(tmp, 'mcs.js'), src);

// ---- minimal DOM ----------------------------------------------------------
function mkEl(id) {
  const el = {
    id: id || '', tagName: 'DIV', _html: '', _text: '', value: '', checked: false,
    hidden: false, disabled: false, files: null, style: {}, dataset: {},
    children: [], parentElement: null, selectedOptions: [],
    className: '',
    classList: {
      _s: new Set(),
      add(...c) { c.forEach((x) => this._s.add(x)); },
      remove(...c) { c.forEach((x) => this._s.delete(x)); },
      toggle(c, on) { const w = on === undefined ? !this._s.has(c) : !!on;
        w ? this._s.add(c) : this._s.delete(c); return w; },
      contains(c) { return this._s.has(c); },
    },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); this._text = String(v).replace(/<[^>]*>/g, ''); },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v);
      this._html = String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); },
    setAttribute(k, v) { this['_attr_' + k] = String(v); },
    getAttribute(k) { const v = this['_attr_' + k]; return v === undefined ? null : v; },
    removeAttribute(k) { delete this['_attr_' + k]; },
    appendChild(c) { this.children.push(c); c.parentElement = this; return c; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {}, removeEventListener() {},
    focus() {}, click() {}, contains() { return false; },
    getContext() { return null; },
    closest() { return null; },
  };
  return el;
}
const registry = Object.create(null);
const documentStub = {
  readyState: 'complete',
  body: mkEl('body'),
  documentElement: mkEl('html'),
  getElementById(id) { return registry[id] || (registry[id] = mkEl(id)); },
  createElement(tag) { const e = mkEl(''); e.tagName = String(tag).toUpperCase(); return e; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {}, removeEventListener() {},
};

const fetchLog = [];
const fetchMap = {};   // url-prefix -> response object; snippet may extend
globalThis.__MCS_TEST__ = true;
globalThis.document = documentStub;
globalThis.__registry = registry;
globalThis.__fetchLog = fetchLog;
globalThis.__fetchMap = fetchMap;
globalThis.window = { addEventListener() {}, location: { host: 'localhost', protocol: 'http:' } };
globalThis.location = { host: 'localhost', protocol: 'http:', hostname: 'localhost' };
globalThis.localStorage = {
  _m: {}, getItem(k) { return this._m[k] ?? null; },
  setItem(k, v) { this._m[k] = String(v); }, removeItem(k) { delete this._m[k]; },
};
globalThis.fetch = async (url, opts) => {
  fetchLog.push({ url: String(url), method: (opts && opts.method) || 'GET' });
  for (const [prefix, resp] of Object.entries(fetchMap)) {
    if (String(url).startsWith(prefix)) {
      return { ok: true, status: 200,
        json: async () => resp, text: async () => JSON.stringify(resp) };
    }
  }
  return { ok: true, status: 200, json: async () => ({}), text: async () => '' };
};
globalThis.WebSocket = function () { this.send = () => {}; this.close = () => {}; this.readyState = 0; };
globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = (init || {}).detail; } };

(async () => {
  let mod;
  try {
    mod = await import(pathToFileURL(path.join(tmp, 'mcs.js')).href);
  } catch (e) {
    console.error('LOAD ERROR: ' + (e.stack || e.message));
    process.exit(3);
  }
  const T = mod.__test;
  const snippet = fs.readFileSync(snippetPath, 'utf8');
  let out;
  try {
    const byId = (id) => documentStub.getElementById(id);
    const fn = new Function('T', 'mod', 'document', '__registry', '__fetchLog', '__fetchMap', 'byId',
      `return (async () => { ${snippet} })();`);
    out = await fn(T, mod, documentStub, registry, fetchLog, fetchMap, byId);
  } catch (e) {
    console.error('SNIPPET ERROR: ' + (e.stack || e.message));
    process.exit(4);
  }
  process.stdout.write(JSON.stringify(out === undefined ? null : out));
})();

// mcs_harness.js — run the MCS operator page's inline script under Node.
//
// index.html is a single self-contained page with ~6 kLOC of inline JS and no
// module boundary, so the behavioural properties we care about (the chart
// staleness gate, the log scroll lock, SID/RID row construction, big-endian
// SID packing) cannot be imported. This harness evaluates the whole inline
// script inside a `vm` context backed by a deliberately small DOM stub, then
// runs a caller-supplied snippet against the resulting globals and prints its
// return value as JSON.
//
//   node mcs_harness.js <path-to-index.html> <path-to-snippet.js>
//
// It is NOT a browser: layout, CSS and real event dispatch are out of scope
// (see the module docstring of test_mcs_page_behaviour.py for what still needs
// manual verification).
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const indexPath = process.argv[2];
const html = fs.readFileSync(indexPath, 'utf8');
const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
if (!blocks.length) { console.error('no inline <script> block'); process.exit(2); }
const src = blocks.reduce((a, b) => (a.length >= b.length ? a : b));

// ---- minimal DOM ----------------------------------------------------
function mkEl(id) {
  const el = {
    id: id || '', tagName: 'DIV', className: '', _html: '', style: {}, dataset: {},
    children: [], childNodes: [], parentElement: null, firstChild: null, lastChild: null,
    value: '', _text: '', scrollTop: 0, scrollHeight: 0, offsetHeight: 12,
    checked: false, disabled: false, hidden: false,
    classList: {
      _s: new Set(),
      add(...c) { c.forEach(x => this._s.add(x)); },
      remove(...c) { c.forEach(x => this._s.delete(x)); },
      toggle(c, on) { const want = on === undefined ? !this._s.has(c) : !!on;
                      want ? this._s.add(c) : this._s.delete(c); return want; },
      contains(c) { return this._s.has(c); },
    },
    get innerHTML() { return this._html; },
    set innerHTML(v) {
      this._html = String(v); this._text = String(v).replace(/<[^>]*>/g, '');
      // Assigning innerHTML replaces the element's children (we only model the
      // empty case, which is what the page uses to clear a list).
      if (this._html === '') { this.children = []; this.childNodes = [];
                               this.firstChild = null; this.lastChild = null; }
    },
    get textContent() { return this._text; },
    // Mirrors the browser: assigning textContent HTML-escapes into innerHTML.
    // This is exactly what the page's esc() helper relies on.
    set textContent(v) {
      this._text = String(v);
      this._html = String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                            .replace(/>/g, '&gt;');
    },
    appendChild(c) {
                     if (c && c.isFragment) {            // DocumentFragment semantics
                       const kids = c.children.slice(); c.children = []; c.childNodes = [];
                       c.firstChild = null; c.lastChild = null;
                       kids.forEach(k => this.appendChild(k));
                       return c;
                     }
                     this.children.push(c); this.childNodes.push(c); c.parentElement = this;
                     this.firstChild = this.children[0]; this.lastChild = this.children[this.children.length-1]; return c; },
    insertBefore(c, ref) { const i = ref ? this.children.indexOf(ref) : this.children.length;
                           this.children.splice(i < 0 ? this.children.length : i, 0, c);
                           c.parentElement = this; this.childNodes = this.children;
                           this.firstChild = this.children[0]; this.lastChild = this.children[this.children.length-1]; return c; },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1);
                     this.childNodes = this.children;
                     this.firstChild = this.children[0] || null;
                     this.lastChild = this.children[this.children.length-1] || null; return c; },
    remove() { if (this.parentElement) this.parentElement.removeChild(this); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener() {}, removeEventListener() {}, setAttribute(k, v) { this['_attr_' + k] = v; },
    getAttribute(k) { return this['_attr_' + k]; }, removeAttribute() {},
    getContext() { return {}; }, focus() {}, click() {}, scrollIntoView() {},
    getBoundingClientRect() { return { top: 0, left: 0, width: 100, height: 100 }; },
    insertAdjacentHTML() {},
  };
  return el;
}
const registry = Object.create(null);
const document = {
  body: mkEl('body'),
  documentElement: mkEl('html'),
  getElementById(id) { return registry[id] || (registry[id] = mkEl(id)); },
  createElement() { return mkEl(''); },
  createDocumentFragment() { const f = mkEl(''); f.isFragment = true; return f; },
  createTextNode(t) { const n = mkEl(''); n.textContent = t; return n; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {}, removeEventListener() {},
};
const sandbox = {
  console, document,
  window: { addEventListener() {}, location: { host: 'localhost', protocol: 'http:', href: '' },
            matchMedia: () => ({ matches: false, addListener() {} }) },
  navigator: { userAgent: 'node' },
  location: { host: 'localhost', protocol: 'http:', hostname: 'localhost', href: '' },
  fetch: async () => ({ ok: true, json: async () => ({}), text: async () => '' }),
  WebSocket: function () { this.send = () => {}; this.close = () => {}; },
  Chart: class { constructor(ctx, cfg) { this.cfg = cfg; this.data = cfg.data; } update() {} destroy() {} },
  setInterval: () => 0, clearInterval: () => {}, setTimeout: () => 0, clearTimeout: () => {},
  requestAnimationFrame: () => 0,
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  alert: () => {}, confirm: () => true, prompt: () => null,
  Math, JSON, Date, Number, String, Object, Array, Promise, Error,
  encodeURIComponent, decodeURIComponent, isFinite, isNaN, parseInt, parseFloat,
};
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);
try {
  vm.runInContext(src, sandbox, { filename: 'index.html:<script>' });
} catch (e) {
  console.error('LOAD ERROR: ' + e.message);
  process.exit(3);
}
sandbox.__mkEl = mkEl;
sandbox.__registry = registry;

// ---- run the caller's test snippet ----------------------------------
const snippet = fs.readFileSync(process.argv[3], 'utf8');
(async () => {
  let out;
  try {
    out = vm.runInContext('(function(){' + snippet + '})()', sandbox, { filename: 'snippet' });
    // A snippet may return a Promise (e.g. to await loadTcCatalog()).
    if (out && typeof out.then === 'function') out = await out;
  } catch (e) {
    console.error('SNIPPET ERROR: ' + e.stack);
    process.exit(4);
  }
  process.stdout.write(JSON.stringify(out === undefined ? null : out));
})();

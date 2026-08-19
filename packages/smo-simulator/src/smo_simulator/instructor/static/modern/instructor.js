/* ============================================================================
 * SMO INSTRUCTOR — modern console page logic (design_2026-08 plan §2.4)
 * ============================================================================
 * ES module, no build step. Uses the frozen Agent-1 shared design system:
 * pure renderers + poller/wsClient from /static/shared/components.js, time
 * formatters from /static/shared/format.js.
 *
 * TRANSPORT RULES (deliberate deviations from the classic page):
 *  - Every instructor command goes ONCE via POST /api/command — the
 *    synchronous error surface; a 400/403 body carries the engine's REASON.
 *    The classic page sent each command twice (WS + POST), enqueuing it
 *    twice. Do not reintroduce that.
 *  - The WebSocket is INBOUND-ONLY: `event`, `state_update`,
 *    `failure_injected`, `failure_cleared`. This module never calls the WS
 *    client's send().
 *  - Event-log timestamps are SIM time (from the last /api/state poll), not
 *    the browser wall clock the classic page used.
 *
 * POLLING (classic cadences preserved): /api/state 1 s ·
 * /api/instructor/snapshot 2 s · /api/failures 5 s (debrief piggybacks the
 * 5 s cycle so a scenario auto-stop surfaces without a click).
 * ========================================================================== */

import {
  esc, renderChip, renderEventRow, poller, wsClient,
} from "/static/shared/components.js?v=1";
import { fmtUTC, fmtHex } from "/static/shared/format.js?v=1";

/* ── constants ─────────────────────────────────────────────────────────── */

const MAX_LOG_ENTRIES = 200;

const AOCS_MODES = { 0: "OFF", 1: "DETUMBLE", 2: "SAFE_POINT", 3: "NADIR_POINT", 4: "TARGET_TRACK" };
const OBDH_MODES = { 0: "NOMINAL", 1: "SAFE", 2: "BOOTLOADER" };
const TTC_MODES  = { 0: "NOMINAL", 1: "REDUNDANT", 2: "OFF" };
const PL_MODES   = { 0: "OFF", 1: "STANDBY", 2: "IMAGING", 3: "PLAYBACK" };
const LINK_STATUS = { 0: "DOWN", 1: "UP" };
// Authoritative sc_mode convention (engine SC_* constants):
const SC_MODES = { 0: "SAFE", 1: "NOMINAL", 2: "SCIENCE", 3: "EMERGENCY" };
const PHASE_NAMES = ["PRE_SEPARATION", "SEPARATION_TIMER", "INITIAL_POWER_ON",
                     "BOOTLOADER_OPS", "LEOP", "COMMISSIONING", "NOMINAL"];

/* ── state ─────────────────────────────────────────────────────────────── */

let state = null;           // last /api/state
let snapshot = null;        // last /api/instructor/snapshot
let scenarios = [];
let selectedScenario = null;
let activeScenarioName = null;
let scenarioStartSimMs = null;   // scenario progress runs on SIM time
let debriefWasActive = false;
let frozen = false;
let failureCatalog = null;
let currentFailureSpec = null;
let breakpoints = [];

const $ = (id) => document.getElementById(id);
const $$ = (sel) => document.querySelectorAll(sel);

/* ── helpers ───────────────────────────────────────────────────────────── */

function fmt(v, d) { return v != null && isFinite(v) ? Number(v).toFixed(d) : "—"; }
function fmtInt(v) { return v != null && isFinite(v) ? String(Math.round(v)) : "—"; }

function formatDuration(totalSec) {
  const m = Math.floor(totalSec / 60);
  const s = Math.floor(totalSec % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function simNowMs() {
  if (!state || !state.sim_time) return null;
  const t = Date.parse(state.sim_time);
  return isNaN(t) ? null : t;
}

/** Current SIM-time-of-day string for log rows ("—" before the first poll). */
function simClock() {
  return state && state.sim_time ? fmtUTC(state.sim_time) : "—";
}

function showToast(msg, type) {
  const t = $("toast");
  t.textContent = msg;
  t.className = (type || "") + " show";
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => t.classList.remove("show"), 2500);
}

/* ── API (single command channel: POST /api/command) ───────────────────── */

async function apiGet(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`HTTP ${r.status} on ${path}`);
  return r.json();
}

async function apiPost(path, body) {
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    // A 400/403 carries the REASON the server refused — return it either way.
    let payload = null;
    try { payload = await r.json(); } catch (e) { payload = null; }
    if (!r.ok) return payload || { status: "error", message: `HTTP ${r.status}` };
    return payload;
  } catch (e) {
    return { status: "error", message: String(e) };
  }
}

/** Send one instructor command, ONCE, over the documented HTTP path.
 *  Returns the response object; callers surface `message` on non-ok. */
function sendCommand(payload) {
  return apiPost("/api/command", payload);
}

/* ── event log (SIM-time stamps, 200-entry cap, severity-coloured) ─────── */

function addLog(severity, source, message) {
  const log = $("event-log");
  const holder = document.createElement("div");
  holder.innerHTML = renderEventRow({
    severity, source, text: message, utc: simClock(),
  });
  log.appendChild(holder.firstElementChild);
  while (log.children.length > MAX_LOG_ENTRIES) log.removeChild(log.firstChild);
  log.scrollTop = log.scrollHeight;
}

/* ── WebSocket — INBOUND ONLY ──────────────────────────────────────────── */

function setConn(up) {
  $("conn-chip").innerHTML = renderChip(
    up ? { kind: "ok", label: "WS CONNECTED" }
       : { kind: "stale", label: "WS DISCONNECTED" });
}

function startWs() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  // Return value deliberately unused: no instructor command is ever sent on
  // the WS — POST /api/command is the single outbound channel.
  wsClient(`${proto}//${location.host}/ws`, {
    _open: () => { setConn(true); addLog("info", "SYSTEM", "WebSocket connected"); },
    _close: () => setConn(false),
    event: (msg) => {
      addLog(msg.severity || "info", msg.source || msg.subsystem || "SIM",
             msg.message || JSON.stringify(msg));
    },
    state_update: (msg) => { if (msg.state) state = msg.state; },
    failure_injected: (msg) =>
      addLog("high", "FAIL", `Injected: ${msg.subsystem}/${msg.failure}`),
    failure_cleared: (msg) =>
      addLog("info", "FAIL", `Cleared: ${msg.failure_id}`),
  });
}

/* ── snapshot browser (full god-mode state; ?sub= deep-dive) ───────────── */

let snapshotTabs = [];
let snapshotActiveTab = null;
let snapshotTabsBuilt = false;

/** Normalise a snapshot.parameters key to "0xHHHH". The endpoint serialises
 *  integer param ids as decimal strings ("257"); accept both forms. */
function paramHexId(key) {
  if (/^0x/i.test(key)) return key;
  const n = parseInt(key, 10);
  return Number.isFinite(n) ? fmtHex(n, 4) : key;
}

const SUB_REGIONS = {
  "01": "EPS·raw", "02": "AOCS·raw", "03": "TCS·raw", "04": "OBDH·raw",
  "05": "TTC·raw", "06": "PAYLOAD·raw", "07": "FDIR·raw", "08": "ORBIT·raw",
  "0B": "PROP·raw",
};

function formatParamValue(v) {
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? "TRUE" : "FALSE";
  if (typeof v === "number") {
    if (!isFinite(v)) return String(v);
    if (Number.isInteger(v)) return String(v);
    const a = Math.abs(v);
    if (a !== 0 && (a < 1e-3 || a >= 1e6)) return v.toExponential(3);
    return v.toFixed(3);
  }
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function buildSnapshotTabs() {
  const tabsRoot = $("full-snapshot-tabs");
  const body = $("full-snapshot-body");
  if (!tabsRoot || !body || !snapshot) return;
  tabsRoot.innerHTML = "";
  body.innerHTML = "";
  snapshotTabs = [];

  // 1. One tab per subsystem model from snapshot.subsystems
  if (snapshot.subsystems && typeof snapshot.subsystems === "object") {
    for (const sub of Object.keys(snapshot.subsystems).sort()) {
      const st = snapshot.subsystems[sub];
      if (st == null || typeof st !== "object") continue;
      const fields = Object.keys(st).sort().map((k) => ({
        key: k,
        getter: () => (snapshot.subsystems[sub] ? snapshot.subsystems[sub][k] : undefined),
      }));
      snapshotTabs.push({ id: "sub:" + sub, label: sub.toUpperCase(), fields, cells: new Map() });
    }
  }

  // 2. Raw parameter dict grouped by hex high byte (subsystem region);
  //    unknown regions fall back to a "0xNNxx" group so EVERY parameter in
  //    the snapshot has a rendering home (completeness guarantee).
  if (snapshot.parameters && typeof snapshot.parameters === "object") {
    const groups = {};
    for (const rawId of Object.keys(snapshot.parameters)) {
      const hex = paramHexId(rawId);
      const m = hex.match(/^0x([0-9A-Fa-f]{2})/);
      const region = m ? (SUB_REGIONS[m[1].toUpperCase()] || ("0x" + m[1].toUpperCase() + "xx"))
                       : "OTHER·raw";
      (groups[region] = groups[region] || []).push(rawId);
    }
    for (const region of Object.keys(groups).sort()) {
      const fields = groups[region]
        .sort((a, b) => paramHexId(a).localeCompare(paramHexId(b)))
        .map((rawId) => ({
          key: paramHexId(rawId),
          getter: () => (snapshot.parameters ? snapshot.parameters[rawId] : undefined),
        }));
      snapshotTabs.push({ id: "raw:" + region, label: region, fields, cells: new Map() });
    }
  }

  for (const tab of snapshotTabs) {
    const btn = document.createElement("button");
    btn.className = "snap-tab";
    btn.dataset.tabId = tab.id;
    btn.innerHTML = `${esc(tab.label)}<span class="count">${tab.fields.length}</span>`;
    btn.addEventListener("click", () => {
      snapshotActiveTab = tab.id;
      renderActiveSnapshotTab();
    });
    tabsRoot.appendChild(btn);
  }

  // Honour ?sub=<id> in subpage mode (accepts "sub:eps", "eps" or "raw:…").
  const requested = new URLSearchParams(location.search).get("sub");
  if (requested) {
    const want = snapshotTabs.find((t) => t.id === requested
      || t.id === "sub:" + requested || t.id === "raw:" + requested);
    snapshotActiveTab = want ? want.id
      : (snapshotTabs.length ? snapshotTabs[0].id : null);
  } else if (snapshotTabs.length) {
    snapshotActiveTab = snapshotTabs[0].id;
  }
  populateSubsystemLauncher();
  renderActiveSnapshotTab();
}

/** Deep-dive launcher: one link per snapshot tab. ?sub= COMPOSES with ?ui= —
 *  the current ui override (if any) is propagated so a deep-dive opened from
 *  /?ui=modern stays modern. */
function populateSubsystemLauncher() {
  const root = $("subsystem-launcher-buttons");
  if (!root) return;
  const ui = new URLSearchParams(location.search).get("ui");
  root.innerHTML = "";
  for (const tab of snapshotTabs) {
    const a = document.createElement("a");
    const q = new URLSearchParams({ sub: tab.id });
    if (ui) q.set("ui", ui);
    a.href = "?" + q.toString();
    a.target = "_blank";
    a.rel = "noopener";
    a.innerHTML = `<span>${esc(tab.label)}</span><span class="ct">${tab.fields.length}</span>`;
    root.appendChild(a);
  }
}

function renderActiveSnapshotTab() {
  const body = $("full-snapshot-body");
  if (!body) return;
  body.innerHTML = "";
  $$("#full-snapshot-tabs .snap-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tabId === snapshotActiveTab));

  const tab = snapshotTabs.find((t) => t.id === snapshotActiveTab);
  if (!tab) return;
  tab.cells = new Map();
  const frag = document.createDocumentFragment();
  for (const f of tab.fields) {
    const row = document.createElement("div");
    row.className = "param-cell";
    const k = document.createElement("span");
    k.className = "pkey";
    k.textContent = f.key;
    const v = document.createElement("span");
    v.className = "pval";
    v.textContent = formatParamValue(f.getter());
    row.appendChild(k);
    row.appendChild(v);
    frag.appendChild(row);
    tab.cells.set(f.key, v);
  }
  body.appendChild(frag);
}

function updateActiveSnapshotTab() {
  // In-place text update of the visible tab only — no innerHTML rebuild.
  const tab = snapshotTabs.find((t) => t.id === snapshotActiveTab);
  if (!tab) return;
  for (const f of tab.fields) {
    const cell = tab.cells.get(f.key);
    if (!cell) continue;
    const formatted = formatParamValue(f.getter());
    if (cell.textContent !== formatted) cell.textContent = formatted;
  }
}

function updateCompletenessCounter() {
  if (!snapshot) return;
  let total = 0, populated = 0;
  for (const t of snapshotTabs) {
    total += t.fields.length;
    for (const f of t.fields) {
      const v = f.getter();
      if (v !== undefined && v !== null) populated++;
    }
  }
  const el = $("param-completeness");
  el.textContent = `${populated}/${total}`;
  el.title = `${populated} of ${total} snapshot fields populated`;
  el.classList.toggle("warn", total > 0 && populated / total < 0.95);
}

function updateRawSnapshot() {
  const container = $("raw-snapshot-container");
  if (snapshot && container && container.style.display === "block") {
    $("raw-snapshot-json").textContent = JSON.stringify(snapshot, null, 2);
  }
}

/* ── parameter search (name or hex id, across params + subsystem state) ── */

function performParameterSearch() {
  const query = $("param-search").value.trim().toLowerCase();
  const results = $("param-search-results");
  if (!query) { results.style.display = "none"; return; }
  if (!snapshot) return;

  const all = [];
  if (snapshot.parameters) {
    for (const [rawId, value] of Object.entries(snapshot.parameters)) {
      const hex = paramHexId(rawId);
      all.push({ id: hex, name: hex, value });
    }
  }
  if (snapshot.subsystems) {
    for (const [subName, subState] of Object.entries(snapshot.subsystems)) {
      if (subState && typeof subState === "object") {
        for (const [key, val] of Object.entries(subState)) {
          all.push({ id: `${subName}.${key}`, name: `${subName}.${key}`, value: val });
        }
      }
    }
  }

  const isHexQuery = query.startsWith("0x");
  const matches = all.filter((p) => {
    if (isHexQuery) return p.id.toLowerCase().includes(query);
    return p.name.toLowerCase().includes(query)
        || String(p.value).toLowerCase().includes(query);
  }).slice(0, 10);

  results.innerHTML = matches.length
    ? matches.map((m) =>
        `<div class="hit"><span class="k">${esc(m.id)}</span>` +
        `<span>${esc(formatParamValue(m.value))}</span></div>`).join("")
    : `<div class="no-data">No parameters found</div>`;
  results.style.display = "block";
}

/* ── scenarios & session ───────────────────────────────────────────────── */

async function loadScenarios() {
  let data = null;
  try { data = await apiGet("/api/scenarios"); } catch (e) { data = null; }
  scenarios = data ? (Array.isArray(data) ? data : (data.scenarios || [])) : [];
  renderScenarios();
}

function renderScenarios() {
  const list = $("scenario-list");
  if (!scenarios.length) {
    list.innerHTML = `<div class="no-data">No scenarios available</div>`;
    return;
  }
  list.innerHTML = scenarios.map((sc, i) => `
    <div class="scenario-item ${selectedScenario === i ? "selected" : ""}
         ${activeScenarioName === sc.name ? "active" : ""}" data-index="${i}">
      <div>${esc(sc.name)}</div>
      <div class="sd"><span>${esc(sc.difficulty || "?")}</span>
        <span class="num">${formatDuration(sc.duration_s || 0)}</span></div>
    </div>`).join("");
  list.querySelectorAll(".scenario-item").forEach((el) =>
    el.addEventListener("click", () => selectScenario(parseInt(el.dataset.index, 10))));
}

function selectScenario(idx) {
  selectedScenario = idx;
  const sc = scenarios[idx];
  if (!sc) return;
  $("scenario-detail").hidden = false;
  $("scenario-briefing").textContent = sc.briefing || "No briefing available.";
  $("btn-start-scenario").disabled = (activeScenarioName === sc.name);
  $("btn-stop-scenario").disabled = (activeScenarioName !== sc.name);
  renderScenarios();
}

async function startScenario() {
  if (selectedScenario == null) return;
  const sc = scenarios[selectedScenario];
  if (!sc) return;
  const res = await sendCommand({ type: "start_scenario", scenario: sc.name });
  if (res && res.status !== "ok") {
    showToast(`Scenario start refused: ${res.message || "unknown"}`, "error");
    return;
  }
  activeScenarioName = sc.name;
  scenarioStartSimMs = simNowMs();
  $("btn-start-scenario").disabled = true;
  $("btn-stop-scenario").disabled = false;
  $("scenario-progress").hidden = false;
  addLog("info", "SCENARIO", `Started: ${sc.name}`);
  showToast(`Scenario "${sc.name}" started`, "success");
  renderScenarios();
}

async function stopScenario() {
  if (!activeScenarioName) return;
  const name = activeScenarioName;
  const res = await sendCommand({ type: "stop_scenario" });
  if (res && res.status !== "ok") {
    showToast(`Stop refused: ${res.message || "unknown"}`, "error");
    return;
  }
  addLog("info", "SCENARIO", `Stopped: ${name}`);
  activeScenarioName = null;
  scenarioStartSimMs = null;
  $("btn-start-scenario").disabled = false;
  $("btn-stop-scenario").disabled = true;
  $("scenario-progress").hidden = true;
  renderScenarios();
  // The engine builds the debrief on stop; give it a tick then show it.
  setTimeout(() => fetchDebrief(true), 1200);
}

/* ── DEBRIEF (new — wires the previously-unreferenced endpoint) ────────── */

function renderDebrief(d) {
  const body = $("debrief-body");
  if (!d) {
    body.innerHTML = `<div class="no-data">No debrief available yet — appears
      when a scenario ends (STOP or its duration elapsing).</div>`;
    return;
  }
  const t = (v) => (v == null ? "—" : `${Number(v).toFixed(1)} s`);
  const responses = (d.responses || []).map((r) =>
    `<div class="prow"><span class="pl">${esc(r.category || "?")}` +
    `${r.description ? " — " + esc(r.description) : ""}</span>` +
    `<span class="pv">${r.time_s != null ? Number(r.time_s).toFixed(1) + " s" : "—"}</span></div>`
  ).join("");
  body.innerHTML = `
    <table>
      <tr><td>Scenario</td><td>${esc(d.name || "—")}</td></tr>
      <tr><td>Duration</td><td>${t(d.duration_s)}</td></tr>
      <tr><td>Score</td><td>${d.score_pct != null ? Number(d.score_pct).toFixed(0) + " %" : "—"}</td></tr>
      <tr><td>MTTD (detect)</td><td>${t(d.mttd_s)}</td></tr>
      <tr><td>MTTI (isolate)</td><td>${t(d.mtti_s)}</td></tr>
      <tr><td>MTTR (recover)</td><td>${t(d.mttr_s)}</td></tr>
    </table>
    ${responses ? `<div class="note" style="margin-top:6px;">Operator responses</div>${responses}`
                : `<div class="note" style="margin-top:6px;">No operator responses recorded.</div>`}`;
}

/** Poll GET /api/scenario/debrief. Also the authority for the header
 *  scenario chip; announces a debrief when a scenario ends (incl. auto-stop
 *  on duration_s, which the page cannot see any other way). */
async function fetchDebrief(announce) {
  let d = null;
  try { d = await apiGet("/api/scenario/debrief"); } catch (e) { return; }
  const chip = $("scenario-chip");
  if (d.active && d.current) {
    chip.innerHTML = renderChip({ kind: "warn", label: `SCENARIO: ${d.current}`,
                                  title: "A training scenario is running" });
    if (activeScenarioName !== d.current) {
      activeScenarioName = d.current;   // page reload during a run
      if (scenarioStartSimMs == null) $("scenario-progress").hidden = false;
      renderScenarios();
    }
  } else {
    chip.innerHTML = renderChip({ kind: "neutral", label: "NO SCENARIO" });
    if (debriefWasActive && !d.active) {
      // active -> finished transition (auto-stop or STOP elsewhere)
      announce = true;
      if (activeScenarioName) {
        addLog("info", "SCENARIO", `Ended: ${activeScenarioName}`);
        activeScenarioName = null;
        scenarioStartSimMs = null;
        $("btn-start-scenario").disabled = false;
        $("btn-stop-scenario").disabled = true;
        $("scenario-progress").hidden = true;
        renderScenarios();
      }
    }
  }
  debriefWasActive = !!d.active;
  if (d.debrief) {
    renderDebrief(d.debrief);
    if (announce) showToast("Scenario debrief available", "success");
  } else if (announce) {
    renderDebrief(null);
  }
}

/* ── failure injection (form generated from /api/failure-catalog) ──────── */

async function loadFailureCatalog() {
  let data = null;
  try { data = await apiGet("/api/failure-catalog"); } catch (e) { data = null; }
  if (!data || !Array.isArray(data.subsystems)) {
    addLog("high", "FAIL", "Failure catalogue unavailable — injection form disabled");
    $("fail-subsystem").innerHTML = `<option value="">(catalogue unavailable)</option>`;
    return;
  }
  failureCatalog = data;
  $("fail-subsystem").innerHTML = data.subsystems
    .map((s) => `<option value="${esc(s.name)}">${esc(s.label || s.name.toUpperCase())}</option>`)
    .join("");
  updateFailureTypes();
}

function currentSubsystemEntry() {
  if (!failureCatalog) return null;
  const name = $("fail-subsystem").value;
  return failureCatalog.subsystems.find((s) => s.name === name) || null;
}

function updateFailureTypes() {
  const entry = currentSubsystemEntry();
  const failures = entry ? entry.failures : [];
  $("fail-type").innerHTML = failures
    .map((f) => `<option value="${esc(f.name)}">${esc(f.label || f.name)}</option>`)
    .join("");
  updateFailureParams();
}

/** One typed control per ParamSpec: bool→checkbox, enum→select (multi →
 *  multi-select), int/float→number with min/max/step and the engineering
 *  unit; the permitted range is always shown. */
function renderParamControl(p, idPrefix) {
  const id = `${idPrefix}-${p.name}`;
  const unit = p.unit ? ` <span class="form-unit">[${esc(p.unit)}]</span>` : "";
  const bits = [];
  if (p.min !== undefined && p.min !== null) bits.push(`min ${p.min}`);
  if (p.max !== undefined && p.max !== null) bits.push(`max ${p.max}`);
  if (p.description) bits.push(esc(p.description));
  const hint = bits.length ? `<span class="form-hint">${bits.join(" · ")}</span>` : "";
  const label = `<label class="form-label" for="${id}">${esc(p.label || p.name)}${unit}${p.required ? " *" : ""}</label>`;

  if (p.type === "bool") {
    const checked = p.default ? " checked" : "";
    return `<div class="form-group">
      <label class="checkbox-label"><input type="checkbox" id="${id}"
        data-param="${esc(p.name)}" data-ptype="bool"${checked}> ${esc(p.label || p.name)}</label>${hint}
    </div>`;
  }
  if (p.type === "enum") {
    const values = p.values || [];
    const labels = p.value_labels || values.map(String);
    const defaults = Array.isArray(p.default) ? p.default.map(String)
      : (p.default === null || p.default === undefined ? [] : [String(p.default)]);
    const opts = values.map((v, i) => {
      const sel = defaults.includes(String(v)) ? " selected" : "";
      return `<option value="${esc(String(v))}"${sel}>${esc(String(labels[i]))}</option>`;
    }).join("");
    const multi = p.multi ? ` multiple size="4" class="form-select form-multi"` : ` class="form-select"`;
    return `<div class="form-group">${label}
      <select id="${id}" data-param="${esc(p.name)}" data-ptype="enum"
        data-multi="${p.multi ? "1" : ""}"${multi}>${opts}</select>${hint}
    </div>`;
  }
  const isNum = (p.type === "int" || p.type === "float");
  const step = p.type === "int" ? 1 : (p.step || "any");
  const attrs = [
    isNum ? `type="number"` : `type="text"`,
    isNum ? `step="${step}"` : "",
    (isNum && p.min !== undefined && p.min !== null) ? `min="${p.min}"` : "",
    (isNum && p.max !== undefined && p.max !== null) ? `max="${p.max}"` : "",
    (p.default !== null && p.default !== undefined) ? `value="${esc(String(p.default))}"` : "",
  ].filter(Boolean).join(" ");
  return `<div class="form-group">${label}
    <input class="form-input" id="${id}" data-param="${esc(p.name)}"
      data-ptype="${esc(p.type)}" ${attrs}>${hint}
  </div>`;
}

function updateFailureParams() {
  const entry = currentSubsystemEntry();
  const name = $("fail-type").value;
  const spec = entry ? entry.failures.find((f) => f.name === name) : null;
  currentFailureSpec = spec;
  setInjectError("");
  $("fail-effect").textContent = spec ? (spec.effect || "") : "";
  if (!spec) { $("fail-params").innerHTML = ""; return; }

  const html = [];
  // Magnitude only when the failure uses one — with ITS unit and range.
  if (spec.magnitude) html.push(renderParamControl(spec.magnitude, "fail-mag"));
  (spec.params || []).forEach((p) => html.push(renderParamControl(p, "fail-p")));
  if (!spec.reversible && spec.notes) {
    html.push(`<div class="fail-effect">NOT FULLY REVERSIBLE — ${esc(spec.notes)}</div>`);
  }
  $("fail-params").innerHTML = html.join("");
}

/** The server-rejection error box: shows the engine's refusal REASON. */
function setInjectError(msg) {
  const el = $("fail-error");
  el.textContent = msg || "";
  el.style.display = msg ? "block" : "none";
}

function readParamControl(p, idPrefix) {
  const el = $(`${idPrefix}-${p.name}`);
  if (!el) return { ok: true, skip: true };
  const where = `${p.label || p.name}`;
  if (p.type === "bool") return { ok: true, value: el.checked };
  if (p.type === "enum") {
    if (p.multi) {
      const vals = Array.from(el.selectedOptions).map((o) => o.value);
      if (!vals.length) return { ok: false, reason: `${where}: select at least one` };
      return { ok: true, value: vals };
    }
    if (el.value === "") return { ok: true, skip: true };
    return { ok: true, value: el.value };
  }
  const raw = (el.value || "").trim();
  if (raw === "") {
    if (p.required) return { ok: false, reason: `${where} is required` };
    return { ok: true, skip: true };
  }
  if (p.type === "int" || p.type === "float") {
    const num = Number(raw);
    if (!Number.isFinite(num)) return { ok: false, reason: `${where} must be a number (got "${raw}")` };
    if (p.type === "int" && !Number.isInteger(num)) {
      return { ok: false, reason: `${where} must be a whole number (got ${num})` };
    }
    const u = p.unit ? ` ${p.unit}` : "";
    if (p.min !== undefined && p.min !== null && num < p.min) {
      return { ok: false, reason: `${where} must be >= ${p.min}${u} (got ${num}${u})` };
    }
    if (p.max !== undefined && p.max !== null && num > p.max) {
      return { ok: false, reason: `${where} must be <= ${p.max}${u} (got ${num}${u})` };
    }
    return { ok: true, value: num };
  }
  return { ok: true, value: raw };
}

function updateOnsetDurationVisibility() {
  const onset = document.querySelector("input[name=\"fail-onset\"]:checked").value;
  // Duration applies to every non-step onset (gradual ramp / intermittent).
  $("onset-duration-group").style.display = (onset === "step") ? "none" : "block";
}

async function injectFailure() {
  const spec = currentFailureSpec;
  if (!spec) { setInjectError("No failure selected (catalogue not loaded)."); return; }
  let subsystem = $("fail-subsystem").value;
  if (subsystem === "adcs") subsystem = "aocs";   // legacy alias
  const onset = document.querySelector("input[name=\"fail-onset\"]:checked").value;
  const onset_duration_s = parseInt($("fail-onset-dur").value, 10) || 30;

  const payload = { type: "failure_inject", subsystem, failure: spec.name,
                    onset, onset_duration_s };
  if (spec.magnitude) {
    const r = readParamControl(spec.magnitude, "fail-mag");
    if (!r.ok) { setInjectError(r.reason); return; }
    if (!r.skip) payload.magnitude = r.value;
  }
  for (const p of (spec.params || [])) {
    const r = readParamControl(p, "fail-p");
    if (!r.ok) { setInjectError(r.reason); return; }
    if (!r.skip) payload[p.name] = r.value;
  }
  setInjectError("");

  const res = await sendCommand(payload);
  if (res && res.status !== "ok") {
    setInjectError(`REJECTED: ${res.message || "unknown reason"}`);
    showToast("Injection rejected — see the reason above", "error");
    return;
  }
  const describe = Object.keys(payload)
    .filter((k) => !["type", "subsystem", "failure", "onset", "onset_duration_s"].includes(k))
    .map((k) => `${k}=${JSON.stringify(payload[k])}`).join(" ");
  addLog("high", "INJECT", `${subsystem}/${spec.name} ${describe} onset=${onset}`);
  showToast(`Failure injected: ${subsystem}/${spec.name}`, "error");
  failuresPoll.kick();   // don't wait up to 5 s for the next cycle
}

/* ── active failures ───────────────────────────────────────────────────── */

function renderFailures(failures) {
  const list = $("failures-list");
  if (!failures || !failures.length) {
    list.innerHTML = `<div class="no-data">No active failures</div>`;
    return;
  }
  list.innerHTML = failures.map((f) => {
    const id = f.failure_id || f.id || `${f.subsystem}_${f.failure}`;
    const magPct = ((f.magnitude || 0) * 100).toFixed(0);
    const elapsed = f.elapsed_s ? formatDuration(f.elapsed_s) : "--:--";
    return `
      <div class="failure-item" data-id="${esc(id)}">
        <div class="fh">
          <span class="fname">${esc((f.subsystem || "").toUpperCase())} / ${esc(f.failure || f.type || "")}</span>
          <button class="ds-btn" data-act="clear-failure" data-id="${esc(id)}">CLEAR</button>
        </div>
        <div class="fmeta"><span>mag ${magPct}%</span><span class="num">${elapsed}</span></div>
      </div>`;
  }).join("");
}

async function clearFailure(id) {
  const res = await sendCommand({ type: "failure_clear", failure_id: id });
  if (res && res.status !== "ok") {
    showToast(`Clear refused: ${res.message || "unknown"}`, "error");
    return;
  }
  addLog("info", "FAIL", `Clearing failure: ${id}`);
  failuresPoll.kick();
}

async function clearAllFailures() {
  const res = await sendCommand({ type: "failure_clear_all" });
  if (res && res.status !== "ok") {
    showToast(`Clear-all refused: ${res.message || "unknown"}`, "error");
    return;
  }
  addLog("info", "FAIL", "Clearing all failures");
  showToast("All failures cleared", "success");
  failuresPoll.kick();
}

/* ── mission phase & separation ────────────────────────────────────────── */

async function setSpacecraftPhase() {
  const phase = parseInt($("phase-select").value, 10);
  if (!Number.isInteger(phase) || phase < 0 || phase > 6) {
    showToast("Phase must be 0-6", "error");
    return;
  }
  const res = await sendCommand({ type: "set_phase", phase });
  if (res && res.status !== "ok") {
    showToast(`Refused: ${res.message || "unknown"}`, "error");
    return;
  }
  addLog("high", "PHASE", `set_phase ${phase} (${PHASE_NAMES[phase]})`);
  showToast(`Phase set: ${phase} ${PHASE_NAMES[phase]}`, "success");
}

async function startSeparation() {
  const res = await sendCommand({ type: "start_separation" });
  if (res && res.status !== "ok") {
    showToast(`Refused: ${res.message || "unknown"}`, "error");
    return;
  }
  addLog("high", "PHASE", "start_separation");
  showToast("Separation sequence started", "success");
}

/* ── overrides ─────────────────────────────────────────────────────────── */

async function toggleOverride() {
  // Desired state from the TRUE engine state, not the button class.
  const newState = !(state && state.override_passes);
  const res = await sendCommand({ type: "override_passes", enabled: newState });
  if (res && res.status !== "ok") {
    showToast(`Refused: ${res.message || "unknown"}`, "error");
    return;
  }
  addLog(newState ? "low" : "info", "OVERRIDE",
         newState ? "Pass override ENABLED" : "Pass override DISABLED");
}

async function setBatterySoc() {
  const soc = parseFloat($("soc-override-input").value);
  if (!isFinite(soc) || soc < 0 || soc > 100) {
    showToast("Battery SoC override must be 0-100 %", "error");
    return;
  }
  const res = await sendCommand({ type: "set_battery_soc", soc_pct: soc });
  if (res && res.status !== "ok") {
    showToast(`Refused: ${res.message || "unknown"}`, "error");
    return;
  }
  addLog("low", "OVERRIDE", `Battery SoC override -> ${soc} %`);
  showToast(`Battery SoC set to ${soc} %`, "success");
}

/* ── speed group (one stepper: 0.5/1/2/5/10x + FREEZE) ─────────────────── */

async function setSpeed(speed) {
  const res = await sendCommand({ type: "set_speed", value: speed });
  if (res && res.status !== "ok") {
    showToast(`Refused: ${res.message || "unknown"}`, "error");
    return;
  }
  $$("#speed-group button[data-speed]").forEach((b) =>
    b.classList.toggle("on", parseFloat(b.dataset.speed) === speed));
}

async function toggleFreeze() {
  const next = !frozen;
  const res = await sendCommand({ type: next ? "freeze" : "resume" });
  if (res && res.status !== "ok") {
    showToast(`Refused: ${res.message || "unknown"}`, "error");
    return;
  }
  frozen = next;
  const btn = $("freeze-btn");
  btn.textContent = frozen ? "RESUME" : "FREEZE";
  btn.classList.toggle("on", frozen);
  addLog(frozen ? "low" : "info", "SIM",
         frozen ? "Simulation FROZEN" : "Simulation RESUMED");
}

/* ── breakpoints ───────────────────────────────────────────────────────── */

async function saveBreakpoint() {
  const name = $("breakpoint-name").value.trim();
  if (!name) return;
  $("breakpoint-name").value = "";
  const res = await apiPost("/api/breakpoint/save", { name });
  if (res && res.status !== "saved") {
    showToast(`Breakpoint save failed: ${res.error || res.message || "unknown"}`, "error");
    return;
  }
  addLog("info", "BRKPT", `Saved: "${name}"`);
  showToast(`Breakpoint "${name}" saved`, "success");
  setTimeout(refreshBreakpoints, 1200);
}

async function refreshBreakpoints() {
  let data = null;
  try { data = await apiGet("/api/breakpoints"); } catch (e) { return; }
  if (!Array.isArray(data)) return;
  breakpoints = data.map((b) => ({
    name: b.name,
    tick: b.tick != null ? b.tick : (b.tick_count != null ? b.tick_count : 0),
    time: b.sim_time || null,
  }));
  renderBreakpoints();
}

function renderBreakpoints() {
  const list = $("breakpoints-list");
  if (!breakpoints.length) {
    list.innerHTML = `<div class="no-data">No breakpoints saved</div>`;
    return;
  }
  list.innerHTML = breakpoints.map((bp) => `
    <div class="failure-item bp">
      <div class="fh">
        <span class="fname">${esc(bp.name)}</span>
        <button class="ds-btn" data-act="load-breakpoint" data-name="${esc(bp.name)}">LOAD</button>
      </div>
      <div class="fmeta"><span>tick ${bp.tick}</span>
        <span class="num">${bp.time ? fmtUTC(bp.time, { date: true }) : ""}</span></div>
    </div>`).join("");
}

async function loadBreakpoint(name) {
  const res = await apiPost("/api/breakpoint/load", { name });
  if (res && res.status !== "loaded") {
    showToast(`Breakpoint load failed: ${res.error || res.message || "unknown"}`, "error");
    return;
  }
  addLog("info", "BRKPT", `Loaded: "${name}"`);
  showToast(`Breakpoint "${name}" loaded`, "success");
}

/* ── truth dashboard render (1 Hz, from /api/state) ────────────────────── */

function setBar(id, pct, warnAt, alarmAt) {
  const bar = $(id);
  if (!bar) return;
  const fill = bar.firstElementChild;
  if (pct == null || !isFinite(pct)) { fill.style.width = "0%"; return; }
  fill.style.width = Math.min(100, Math.max(0, pct)) + "%";
  bar.classList.toggle("alarm", alarmAt !== undefined && pct >= alarmAt);
  bar.classList.toggle("warn",
    warnAt !== undefined && pct >= warnAt && !(alarmAt !== undefined && pct >= alarmAt));
}

function setBarLow(id, pct, warnBelow, alarmBelow) {
  const bar = $(id);
  if (!bar) return;
  const fill = bar.firstElementChild;
  if (pct == null || !isFinite(pct)) { fill.style.width = "0%"; return; }
  fill.style.width = Math.min(100, Math.max(0, pct)) + "%";
  bar.classList.toggle("alarm", pct < alarmBelow);
  bar.classList.toggle("warn", pct >= alarmBelow && pct < warnBelow);
}

function tempClass(val, low, high) {
  if (val == null) return "";
  if (val > high || val < low) return val > high ? "alarm" : "warn";
  return "";
}

function setPv(id, text, cls) {
  const el = $(id);
  if (!el) return;
  el.textContent = text;
  el.className = "pv" + (cls ? " " + cls : "");
}

function onOff(id, on) {
  $(id).innerHTML = renderChip(on ? { kind: "ok", label: "ON" }
                                  : { kind: "off", label: "OFF" });
}

function renderDashboard() {
  if (!state) return;

  // header: SIM TIME primary clock + speed group + SC mode badge
  $("sim-time").textContent = state.sim_time
    ? fmtUTC(state.sim_time, { date: true }) + " UTC" : "—";
  if (state.speed != null) {
    $$("#speed-group button[data-speed]").forEach((b) =>
      b.classList.toggle("on", parseFloat(b.dataset.speed) === state.speed));
  }
  const badge = $("sc-mode-badge");
  const mode = SC_MODES[state.sc_mode] || "UNKNOWN";
  badge.textContent = "SC " + mode;
  badge.className = "ds-badge" + (mode === "EMERGENCY" ? " alarm"
    : mode === "SAFE" ? " warn" : "");

  // EPS
  const eps = state.eps || {};
  setPv("eps-soc", fmt(eps.soc_pct, 1));
  setBarLow("eps-soc-bar", eps.soc_pct, 70, 30);
  setPv("eps-bus-v", fmt(eps.bus_voltage_V, 1) + " V");
  setPv("eps-bat-v", fmt(eps.bat_voltage_V, 1) + " V");
  setPv("eps-bat-temp", fmt(eps.bat_temp_C, 1) + " °C", tempClass(eps.bat_temp_C, 5, 40));
  setPv("eps-pgen", fmt(eps.power_gen_W, 1) + " W");
  setPv("eps-pcons", fmt(eps.power_cons_W, 1) + " W");
  setPv("eps-sa", fmt(eps.sa_a_A, 2) + " / " + fmt(eps.sa_b_A, 2) + " A");

  // AOCS
  const aocs = state.aocs || {};
  $("aocs-mode").textContent = AOCS_MODES[aocs.mode] || "—";
  setPv("aocs-atterr", fmt(aocs.att_error_deg, 3) + " °",
    aocs.att_error_deg > 1 ? "alarm" : aocs.att_error_deg > 0.5 ? "warn" : "");
  setPv("aocs-rr", fmt(aocs.rate_roll, 4) + " °/s");
  setPv("aocs-rp", fmt(aocs.rate_pitch, 4) + " °/s");
  setPv("aocs-ry", fmt(aocs.rate_yaw, 4) + " °/s");
  setPv("aocs-rw12", fmtInt(aocs.rw1_rpm) + " / " + fmtInt(aocs.rw2_rpm) + " rpm");
  setPv("aocs-rw34", fmtInt(aocs.rw3_rpm) + " / " + fmtInt(aocs.rw4_rpm) + " rpm");

  // TCS
  const tcs = state.tcs || {};
  setPv("tcs-obc", fmt(tcs.temp_obc_C, 1) + " °C", tempClass(tcs.temp_obc_C, -10, 50));
  setPv("tcs-bat", fmt(tcs.temp_bat_C, 1) + " °C", tempClass(tcs.temp_bat_C, 5, 40));
  setPv("tcs-fpa", fmt(tcs.temp_fpa_C, 1) + " °C", tempClass(tcs.temp_fpa_C, -50, -10));
  onOff("tcs-htr-bat", !!tcs.htr_bat);
  onOff("tcs-htr-obc", !!tcs.htr_obc);
  onOff("tcs-cooler", !!tcs.cooler_fpa);

  // OBDH
  const obdh = state.obdh || {};
  $("obdh-mode").textContent = OBDH_MODES[obdh.mode] || "—";
  setPv("obdh-cpu", fmt(obdh.cpu_load_pct, 1));
  setBar("obdh-cpu-bar", obdh.cpu_load_pct, 60, 80);
  setPv("obdh-mem", fmt(obdh.mem_used_pct, 1));
  setBar("obdh-mem-bar", obdh.mem_used_pct, 60, 80);
  setPv("obdh-reboots", obdh.reboot_count != null ? String(obdh.reboot_count) : "—",
    obdh.reboot_count > 0 ? "warn" : "");

  // TT&C
  const ttc = state.ttc || {};
  $("ttc-mode").textContent = TTC_MODES[ttc.mode] || "—";
  $("ttc-link").innerHTML = renderChip(ttc.link_status === 1
    ? { kind: "ok", label: LINK_STATUS[1] }
    : { kind: "off", label: LINK_STATUS[ttc.link_status] || "—" });
  setPv("ttc-rssi", fmt(ttc.rssi_dBm, 1) + " dBm");
  setPv("ttc-margin", fmt(ttc.link_margin_dB, 1) + " dB",
    ttc.link_margin_dB != null && ttc.link_margin_dB < 3 ? "alarm"
      : ttc.link_margin_dB < 6 ? "warn" : "");
  setPv("ttc-range", fmt(ttc.range_km, 1) + " km");
  setPv("ttc-elev", fmt(ttc.elevation_deg, 1) + " °");

  // Payload
  const pl = state.payload || {};
  $("payload-mode").textContent = PL_MODES[pl.mode] || "—";
  setPv("pl-fpa", fmt(pl.fpa_temp_C, 1) + " °C", tempClass(pl.fpa_temp_C, -50, -10));
  setPv("pl-store", fmt(pl.store_used_pct, 1));
  setBar("pl-store-bar", pl.store_used_pct, 60, 80);
  setPv("pl-images", pl.image_count != null ? String(pl.image_count) : "—");

  // Orbit bar
  $("orb-lat").textContent = fmt(state.lat, 4) + "°";
  $("orb-lon").textContent = fmt(state.lon, 4) + "°";
  $("orb-alt").textContent = fmt(state.alt_km, 2) + " km";
  $("orb-eclipse").innerHTML = renderChip(state.in_eclipse
    ? { kind: "off", label: "ECLIPSE" } : { kind: "ok", label: "SUNLIGHT" });
  $("orb-contact").innerHTML = renderChip(state.in_contact
    ? { kind: "ok", label: state.ground_station_name
        ? "CONTACT: " + state.ground_station_name.toUpperCase() : "CONTACT" }
    : { kind: "off", label: "NO CONTACT" });

  // OVERRIDE PASSES — the single copy; label reflects engine truth.
  const ob = $("btn-override");
  ob.textContent = state.override_passes ? "OVERRIDE ON" : "OVERRIDE PASSES";
  ob.classList.toggle("danger", !!state.override_passes);

  // phase select mirrors engine truth when the user is not mid-change
  if (state.spacecraft_phase != null && document.activeElement !== $("phase-select")) {
    $("phase-select").value = String(state.spacecraft_phase);
  }

  // Active failures + card highlight
  if (Array.isArray(state.active_failures)) {
    renderFailures(state.active_failures);
    const failed = new Set(state.active_failures.map((f) => f.subsystem));
    ["eps", "aocs", "tcs", "obdh", "ttc", "payload"].forEach((sub) => {
      const card = $("card-" + sub);
      if (card) card.style.borderColor = failed.has(sub) ? "var(--alarm)" : "";
    });
  }

  // Scenario progress in SIM time (freezes with the sim; speed changes do
  // not rescale already-elapsed time).
  if (activeScenarioName && selectedScenario != null) {
    const sc = scenarios[selectedScenario];
    const nowSimMs = simNowMs();
    if (sc && scenarioStartSimMs != null && nowSimMs != null) {
      const elapsedS = Math.max(0, (nowSimMs - scenarioStartSimMs) / 1000);
      const totalS = sc.duration_s || 1;
      const pct = Math.min(100, (elapsedS / totalS) * 100);
      $("progress-elapsed").textContent = formatDuration(elapsedS);
      $("progress-remaining").textContent = "-" + formatDuration(Math.max(0, totalS - elapsedS));
      $("progress-fill").style.width = pct + "%";
    }
  }
}

/* ── clock truth card (instructor only — never downlinked) ─────────────── */

const CUC_EPOCH_MS = Date.UTC(2000, 0, 1, 12, 0, 0);   // mission time_epoch

function formatClockAge(sec) {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${String(m).padStart(2, "0")}m`
               : `${m}m ${String(s % 60).padStart(2, "0")}s`;
}

function updateClockTruth() {
  const c = (snapshot && snapshot.clock_truth) || null;
  if (!c) {
    ["clock-sim-utc", "clock-obc-utc", "clock-since-sync"].forEach((id) => setPv(id, "—"));
    setPv("clock-drift", "— ppm");
    setPv("clock-offset", "— s");
    $("clock-ref").textContent = "—";
    return;
  }
  $("clock-ref").textContent = c.time_ref_name || "—";
  setPv("clock-sim-utc", c.sim_time ? fmtUTC(c.sim_time, { date: true }) : "—");
  setPv("clock-obc-utc", c.obc_time_s != null
    ? fmtUTC(new Date(CUC_EPOCH_MS + c.obc_time_s * 1000), { date: true }) : "—");
  setPv("clock-since-sync", c.time_since_sync_s == null
    ? "never synced" : formatClockAge(c.time_since_sync_s));
  setPv("clock-drift", c.drift_ppm == null ? "— ppm" : fmt(c.drift_ppm, 2) + " ppm");
  if (c.offset_s == null) {
    setPv("clock-offset", "— s");
  } else {
    const a = Math.abs(c.offset_s);
    // 1 s: an operator starts to care. 5 s: the FDIR jump guard.
    setPv("clock-offset", (c.offset_s >= 0 ? "+" : "") + c.offset_s.toFixed(3) + " s",
          a >= 5 ? "alarm" : a >= 1 ? "warn" : "good");
  }
}

/* ── polling (shared poller helper; classic cadences) ──────────────────── */

const statePoll = poller(async () => {
  state = await apiGet("/api/state");
  renderDashboard();
}, 1000);

const snapshotPoll = poller(async () => {
  snapshot = await apiGet("/api/instructor/snapshot");
  updateRawSnapshot();
  updateClockTruth();
  if (!snapshotTabsBuilt) { buildSnapshotTabs(); snapshotTabsBuilt = true; }
  updateActiveSnapshotTab();
  updateCompletenessCounter();
  performParameterSearch();
}, 2000);

const failuresPoll = poller(async () => {
  const data = await apiGet("/api/failures");
  renderFailures(Array.isArray(data) ? data : (data.failures || []));
  await fetchDebrief(false);
}, 5000);

/* ── init ──────────────────────────────────────────────────────────────── */

function init() {
  // ?sub= subpage deep-dive mode (preserved exactly, composing with ?ui=):
  // hide the dashboard chrome, render only the requested snapshot tab
  // full-width with a back link.
  const q = new URLSearchParams(location.search);
  const requestedSub = q.get("sub");
  if (requestedSub) {
    document.body.classList.add("subpage");
    const cont = $("full-snapshot-container");
    const back = document.createElement("a");
    back.className = "subpage-back";
    const backQ = new URLSearchParams();
    if (q.get("ui")) backQ.set("ui", q.get("ui"));
    back.href = "/" + (backQ.toString() ? "?" + backQ.toString() : "");
    back.textContent = "← Back to dashboard";
    cont.parentNode.insertBefore(back, cont);
    $("full-snapshot-title").textContent =
      "SUBSYSTEM DEEP-DIVE — " + requestedSub.toUpperCase();
    document.title = "SMO — " + requestedSub.toUpperCase();
  }

  // header controls
  $$("#speed-group button[data-speed]").forEach((btn) =>
    btn.addEventListener("click", () => setSpeed(parseFloat(btn.dataset.speed))));
  $("freeze-btn").addEventListener("click", toggleFreeze);

  // scenarios + debrief
  $("refresh-scenarios").addEventListener("click", loadScenarios);
  $("btn-start-scenario").addEventListener("click", startScenario);
  $("btn-stop-scenario").addEventListener("click", stopScenario);
  $("btn-fetch-debrief").addEventListener("click", () => fetchDebrief(true));

  // interventions
  $("fail-subsystem").addEventListener("change", updateFailureTypes);
  $("fail-type").addEventListener("change", updateFailureParams);
  $$("input[name=\"fail-onset\"]").forEach((r) =>
    r.addEventListener("change", updateOnsetDurationVisibility));
  updateOnsetDurationVisibility();
  $("btn-inject").addEventListener("click", injectFailure);
  $("btn-clear-all").addEventListener("click", clearAllFailures);
  $("btn-set-phase").addEventListener("click", setSpacecraftPhase);
  $("btn-start-separation").addEventListener("click", startSeparation);
  $("btn-override").addEventListener("click", toggleOverride);
  $("btn-set-soc").addEventListener("click", setBatterySoc);
  $("soc-override-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") setBatterySoc();
  });

  // per-row actions (event delegation — no inline handlers, no globals)
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    if (btn.dataset.act === "clear-failure") clearFailure(btn.dataset.id);
    else if (btn.dataset.act === "load-breakpoint") loadBreakpoint(btn.dataset.name);
  });

  // breakpoints
  $("btn-save-bp").addEventListener("click", saveBreakpoint);
  $("breakpoint-name").addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveBreakpoint();
  });
  refreshBreakpoints();

  // log panel: collapse + clear
  $("clear-log").addEventListener("click", () => { $("event-log").innerHTML = ""; });
  $("btn-log-toggle").addEventListener("click", () => {
    const panel = $("log-panel");
    const collapsed = panel.classList.toggle("collapsed");
    $("btn-log-toggle").setAttribute("aria-expanded", String(!collapsed));
    $("btn-log-toggle").textContent = (collapsed ? "▸" : "▾") + " EVENT LOG";
  });

  // parameter search + raw snapshot
  $("param-search").addEventListener("input", performParameterSearch);
  $("btn-clear-search").addEventListener("click", () => {
    $("param-search").value = "";
    $("param-search-results").style.display = "none";
  });
  $("btn-toggle-raw").addEventListener("click", () => {
    const container = $("raw-snapshot-container");
    const showing = container.style.display === "block";
    container.style.display = showing ? "none" : "block";
    $("btn-toggle-raw").textContent = showing ? "SHOW RAW SNAPSHOT" : "HIDE RAW SNAPSHOT";
    if (!showing) updateRawSnapshot();
  });

  setConn(false);
  $("scenario-chip").innerHTML = renderChip({ kind: "neutral", label: "NO SCENARIO" });
  loadScenarios();
  loadFailureCatalog();
  startWs();
  statePoll.start();
  snapshotPoll.start();
  failuresPoll.start();
  addLog("info", "SYSTEM", "Instructor console (modern) initialised");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}

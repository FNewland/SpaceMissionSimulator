/* ============================================================================
 * SMO MCS MODERN CONSOLE — mcs.js (execution plan §2.1)
 * ============================================================================
 * One ES module, no build step. Shared vocabulary from /static/shared/
 * (frozen Agent-1 contract): components.js + format.js. All rendering is
 * string-composition + innerHTML; this module owns DOM insertion.
 *
 * SECTIONS (search for "═══"):
 *   1. TIME            — simNow(), the single time primitive (M3)
 *   2. STATE           — module globals, catalogs, ring buffers
 *   3. UTIL            — helpers
 *   4. TRANSPORT       — WS /ws?position= + 3 s reconnect, REST fallback
 *   5. SA HEADER       — clocks, four cards, TRAINING chip, contingency strip
 *   6. NAV             — 62 px rail, pages, position filtering, keyboard
 *   7. SCHEMATICS      — schematics.svg mounting + bindSchematic
 *   8. OVERVIEW (L1)   — schematic-as-navigation, timeline, map, board
 *   9. SUBSYSTEM (L2)  — one skeleton instantiated 7×
 *  10. FRESHNESS       — display-wide staleness gate (P4)
 *  11. CMD             — builder + codec + quick cmds + S15 + HK + verification
 *  12. DIAG (L4)       — S3/S11/S6/S15/S12/S19/S9 + PARAMS panel
 *  13. FDIR            — strip + shared S12/S19 mounts + autonomy panel (M4)
 *  14. PROC            — runner + doc viewer + override + builder
 *  15. TRND            — archive trends (Chart.js, vendor-guarded)
 *  16. MAN             — operations manual
 *  17. DRAWER          — bottom log drawer (5 tabs, scroll lock)
 *  18. ALARMS          — docked alarm drawer + causal chain (P7/P9)
 *  19. GUIDED          — teaching layer from /api/annotations
 *  20. BOOT
 *
 * House rules honoured here:
 *  - Date.now() appears ONLY inside section 1 (TIME). Everything else asks
 *    simNow()/simNowMs(). (Report 2 M3.)
 *  - The page polls /api/state — never the sim-named route (M12) and never
 *    reads the sim-clock-jump field (M10).
 *  - TRAINING chip only when the ground clock is simulator time (M1/M11).
 *  - sc_mode renderings are TM-derived (0x0700 / obdh.mode) and carry a
 *    provenance sub-label (M5-M8).
 * ============================================================================ */

import {
  esc, glyph, freshness, limitClass, renderParam, renderDeviationBar,
  renderSparkline, renderAndTable, renderChip, renderStepper, renderAlarmRow,
  renderEventRow, bindSchematic, poller, wsClient,
} from "/static/shared/components.js";
import { fmtUTC, fmtMET, fmtCUC, fmtAge, fmtHex } from "/static/shared/format.js";

/* ═══ 1. TIME — the single time primitive ═════════════════════════════════ */
/* simNow() = server-anchored mission clock, advancing at the published rate.
 * The ONLY place Date.now() may be called (checked by
 * tests/test_mcs/test_modern_page_behaviour.py). In real mode speed is 1.0
 * and this degenerates to wall-clock UTC. */

let _anchorMs = null;       // mission epoch ms at anchor
let _anchorWallMs = null;   // wall ms at anchor            (Date.now domain)
let _speed = 1.0;

function _wallNowMs() { return Date.now(); }   // sole Date.now() call site

export function adoptServerClock(data) {
  if (!data) return;
  const iso = data.sim_time || data.ground_utc;
  if (iso) {
    const t = Date.parse(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
    if (Number.isFinite(t)) { _anchorMs = t; _anchorWallMs = _wallNowMs(); }
  }
  if (typeof data.speed === "number" && Number.isFinite(data.speed)) _speed = data.speed;
}

export function simNowMs() {
  if (_anchorMs === null) return _wallNowMs();
  return _anchorMs + (_wallNowMs() - _anchorWallMs) * _speed;
}
export function simNow() { return new Date(simNowMs()); }
export function simNowSec() { return simNowMs() / 1000; }

/* ═══ 2. STATE ════════════════════════════════════════════════════════════ */

const S = {
  state: {},               // latest WS/REST state document
  positions: {},           // /api/positions
  position: "flight_director",
  paramCatalog: [],        // /api/param-catalog parameters[]
  paramByHex: {},          // hex -> catalog def
  limits: {},              // /api/limits limits{}
  tcCatalog: [],           // /api/tc-catalog commands[] (position-filtered flags)
  tcCatalogAll: [],        // unfiltered (flight_director view) for authority display
  quickCommands: [],       // /api/tc-catalog quick_commands[]
  clientConfig: {},        // /api/client-config
  procCards: null,         // /api/procedure/cards (per position)
  procCardOpen: "",        // expanded card id
  annotations: {},         // /api/annotations
  timeMode: "unknown",     // "sim" | "real" | "unknown"  (see resolveTimeMode)
  wsConnected: false,
  page: "ovr",
  alarms: [],              // /api/alarms journal (client copy)
  contacts: [],            // /api/contacts (normalised)
  contactSchedule: [],     // /api/displays/contact-schedule passes
  groundStations: [],
  hkStructures: null,
  memoryMap: null,
  failureCodes: {},
  fdir: null,              // /api/fdir
  verifLog: [],
  cmdHistory: [],          // local ring
  events: [],              // event log ring
  tmLog: [],               // packet log ring
  subsysEvents: { eps: [], tcs: [], aocs: [], ttc: [], payload: [], prop: [], obdh: [], orbital: [] },
  handover: [],
  goNoGo: null,
  series: {},              // hex -> ring buffer for sparklines
  manual: null,
  manualOpen: "",        // path of the open section (active-item highlight)
  manualBodies: {},      // path -> markdown, cached so search covers body text

  procStatus: null,
  s11Schedule: null,
  armed: {},               // cmd name -> armed flag (arm→execute)
  chartStale: false,       // display-wide staleness gate verdict
  // ── POSITION_NEEDS gap surfaces (G1–G9, G12, G15) ──
  faultGraph: null,        // /api/fault-graph (G6 first-out grouping)
  gonogoMatrix: null,      // /api/gonogo-matrix (G7 call-matrix context)
  consoleLog: [],          // /api/console-log merged stream (G8)
  conlogKind: "all",       // LOG tab kind filter
  burnPlan: null,          // planner orbit_manoeuvre join (G9)
  linkPred: null,          // /api/displays/link-prediction (G5)
  linkMeasured: [],        // [{t, eb, rssi}] measured ring for the overlay
  blindTc: { enabled: false, armPending: false },   // G12
  alarmGroupsOpen: {},     // rootAlarmId -> expanded (G6)
};

const RING = 300;
const EVENT_SUBSYSTEM_RANGES = {
  orbital: [0x0001, 0x00FF], eps: [0x0100, 0x01FF], aocs: [0x0200, 0x02FF],
  obdh: [0x0300, 0x03FF], tcs: [0x0400, 0x04FF], ttc: [0x0500, 0x05FF],
  payload: [0x0600, 0x06FF], prop: [0x0800, 0x08FF],
};
const AOCS_MODES = ["OFF", "SAFE_BOOT", "DETUMBLE", "COARSE_SUN", "NOMINAL",
  "FINE_POINT", "SLEW", "DESAT", "ECLIPSE", "INERTIAL"];
const SC_MODES = ["NOMINAL", "SAFE", "EMERGENCY", "SCIENCE"];

/* Modern page id ↔ classic tab id (positions.yaml speaks classic ids). */
const PAGE_TO_CLASSIC = {
  ovr: "overview", eps: "eps", tcs: "tcs", aocs: "aocs", ttc: "ttc",
  pld: "payload", prp: "prop", obd: "obdh", cmd: "commanding", fdir: "fdir",
  proc: "procedures", trnd: "trends", diag: "pus", man: "manual",
};
const SUB_PAGES = ["eps", "tcs", "aocs", "ttc", "pld", "prp", "obd"];
const PAGE_SUBSYSTEM = { eps: "eps", tcs: "tcs", aocs: "aocs", ttc: "ttc",
  pld: "payload", prp: "prop", obd: "obdh" };
const RAIL = [
  ["ovr", "OVR"], ["eps", "EPS"], ["tcs", "TCS"], ["aocs", "AOC"], ["ttc", "TTC"],
  ["pld", "PLD"], ["prp", "PRP"], ["obd", "OBD"], ["|", ""],
  ["cmd", "CMD"], ["fdir", "FDR"], ["proc", "PRC"], ["trnd", "TRD"],
  // "PUS", not "DIA": the classic console's top-level tab was "PUS SERVICES"
  // and operators look for that name. The page id stays `diag`.
  ["diag", "PUS"], ["man", "MAN"],
];

const POSITION_INFO = {
  flight_director: { title: "FLIGHT DIRECTOR" },
  eps_tcs: { title: "POWER & THERMAL" },
  aocs: { title: "FLIGHT DYNAMICS / AOCS" },
  ttc: { title: "TT&C" },
  payload_ops: { title: "PAYLOAD OPERATIONS" },
  fdir_systems: { title: "FDIR / SYSTEMS" },
  propulsion: { title: "PROPULSION" },
};

/* ═══ 3. UTIL ═════════════════════════════════════════════════════════════ */

const $ = (id) => document.getElementById(id);
function setText(id, v) { const el = $(id); if (el) el.textContent = v; }
function setHTML(id, v) { const el = $(id); if (el) el.innerHTML = v; }
function show(id, on) { const el = $(id); if (el) el.classList.toggle("hide", !on); }
function num(v, dp = 1) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
  return Number(v).toFixed(dp);
}
function hex4(v) { return fmtHex(v, 4); }
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}
async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}) });
  let j = null;
  try { j = await r.json(); } catch (e) { /* non-JSON */ }
  return { ok: r.ok, status: r.status, json: j };
}
function announce(msg) { setText("alert-live", msg); }
function tsShort() { return fmtUTC(simNow(), { seconds: true }); }
function paramVal(hexId) {
  const p = S.state.params;
  return p ? p[hexId] : undefined;
}
function paramAge(hexId) {
  const ages = S.state.params_age;
  if (!ages) return undefined;
  return ages[String(parseInt(hexId, 16))];
}
/* Composite values object for renderAndTable / renderParam callers. */
function liveValues() {
  return {
    params: S.state.params || {},
    ages: S.state.params_age || {},
    limitState: S.state.param_limit_state || {},
    limits: S.limits,
  };
}
/* Page-level param triad: catalog + live + limits joined (P3). */
/* ── Engineering values ───────────────────────────────────────────────────
 *
 * Field report 2026-08-20: "check in the new version that parameters are
 * displayed in engineering values, e.g. for AOCS mode - but this may be a
 * generic issue - at the minute only the raw values seem to display".
 *
 * It was generic. Every catalogue-driven surface here formatted values with a
 * plain numeric formatter, so aocs.mode read `4` instead of `NOMINAL`. Two
 * widgets (the AOCS mode chain and the SA-header mode chip) had their own
 * hardcoded arrays -- a third copy of codes that already live in config.
 *
 * /api/param-catalog now serves a `states` map for every parameter whose codes
 * are documented in configuration (see smo_common/telemetry/eng_values.py).
 * This is the one place that turns a raw value into what an operator reads.
 *
 * The raw code is kept in parentheses on purpose: procedures, packet dumps and
 * S12 limit definitions all quote the number, so hiding it would break the
 * cross-check an operator actually performs.
 */
export function paramStates(hexId) {
  const st = (S.paramByHex[hexId] || {}).states;
  return st && Object.keys(st).length ? st : null;
}
export function engValue(hexId, raw) {
  if (raw === undefined || raw === null) return "—";
  const st = paramStates(hexId);
  if (!st) return null;                 // not enumerated: caller formats numerically
  const code = Math.round(Number(raw));
  if (!Number.isFinite(code)) return String(raw);
  const name = st[String(code)];
  return name ? `${name} (${code})` : `UNKNOWN (${code})`;
}
export function paramTriad(hexId) {
  const def = S.paramByHex[hexId] || { hex: hexId, name: hexId };
  const band = S.limits[hexId] ? { ...S.limits[hexId] } : null;
  const st = (S.state.param_limit_state || {})[hexId];
  const age = paramAge(hexId);
  const lim = band || (st || age !== undefined ? {} : null);
  if (lim) { if (st) lim.state = st; if (age !== undefined) lim.age_s = age; }
  const eng = engValue(hexId, paramVal(hexId));
  // renderParam() formats numerically and appends `units`; for a discrete
  // parameter the state NAME is the value, and a unit suffix is meaningless.
  if (eng !== null) return renderParam({ ...def, units: "" }, eng, lim);
  return renderParam(def, paramVal(hexId), lim);
}
function pushSeries(hexId, v) {
  if (!Number.isFinite(Number(v))) return;
  const s = S.series[hexId] || (S.series[hexId] = []);
  s.push(Number(v));
  if (s.length > 120) s.shift();
}
/* ── Cadence-aware staleness ──────────────────────────────────────────────
 *
 * Field report 2026-08-20: "check the stale telemetry mechanism ... both in
 * the old and new versions of the UI".
 *
 * The modern console was judging EVERY parameter against a flat 5 s / 15 s
 * threshold (`freshness()` in the shared component library). That is exactly
 * the defect configs/eosat1/mcs/staleness.yaml was written to kill: HK SID 3
 * (TCS) has interval_s 60, so the thermal displays read STALE for ~45 s out of
 * every 60, and SID 11 (beacon, 30 s) read STALE for 22 s out of every 30 --
 * during the one mission phase where the beacon is the ONLY telemetry there
 * is. Classic was fixed; modern was never wired up, and still fetched
 * /api/client-config without reading the cadence join out of it.
 *
 * This is the classic algorithm, deliberately identical so the two consoles
 * cannot disagree about whether a value is current:
 *
 *     fresh    while age <= max(floor_fresh_s, interval_s * fresh_factor)
 *     degraded while age <= max(floor_stale_s, interval_s * stale_factor)
 *     stale    beyond that
 *
 * plus the fourth state the policy file defines: NOT ENABLED, when every HK
 * structure carrying the parameter is disabled. A value with no enabled source
 * is not stale telemetry -- it is telemetry nobody has asked for, and the
 * remedy is TC(3,5), not chasing a link fault. Rendering that as "stale" is
 * the silent lie the four-state model exists to prevent.
 *
 * Ages are SIM seconds as served (params_age / subsystem_staleness /
 * hk_sid_age_s). Never re-age them against the browser wall clock.
 */
const STALE_POLICY_FALLBACK = {
  stale_factor: 1.5, fresh_factor: 0.9,
  floor_fresh_s: 10.0, floor_stale_s: 20.0,
  aperiodic_fresh_s: 300.0, aperiodic_stale_s: 900.0,
};
function stalePolicy() {
  return Object.assign({}, STALE_POLICY_FALLBACK, (S.clientConfig || {}).staleness || {});
}
export function stalenessThresholds(intervalS) {
  const pol = stalePolicy();
  if (intervalS === null || intervalS === undefined || !(Number(intervalS) > 0)) {
    return { fresh: pol.aperiodic_fresh_s, stale: pol.aperiodic_stale_s,
             aperiodic: true, interval_s: null };
  }
  const iv = Number(intervalS);
  const fresh = Math.max(pol.floor_fresh_s, iv * pol.fresh_factor);
  const stale = Math.max(pol.floor_stale_s, iv * pol.stale_factor, fresh);
  return { fresh, stale, aperiodic: false, interval_s: iv };
}
export function ageVerdict(ageS, intervalS) {
  const th = stalenessThresholds(intervalS);
  const age = Number(ageS);
  if (!Number.isFinite(age)) return "stale";
  if (age <= th.fresh) return "fresh";
  if (age <= th.stale) return "degraded";
  return "stale";
}
// Cadence for one parameter, from the /api/client-config join. null => aperiodic.
function paramInterval(hexId) {
  const cad = ((S.clientConfig || {}).param_cadence || {})[hexId] ||
              ((S.clientConfig || {}).param_cadence || {})[String(parseInt(hexId, 16))];
  if (!cad) return null;
  return Array.isArray(cad) ? cad[0] : cad.interval_s;
}
function paramSids(hexId) {
  const cad = ((S.clientConfig || {}).param_cadence || {})[hexId] ||
              ((S.clientConfig || {}).param_cadence || {})[String(parseInt(hexId, 16))];
  if (!cad) return [];
  return (Array.isArray(cad) ? cad.slice(1) : cad.sids) || [];
}
/** 'fresh' | 'degraded' | 'stale' | 'disabled' for one parameter. */
export function paramFreshness(hexId) {
  const sids = paramSids(hexId);
  if (sids.length) {
    const en = S.state.hk_enabled || {};
    // only claim "disabled" when we actually know the enable state
    const known = sids.filter((sid) => en[String(sid)] !== undefined);
    if (known.length && known.every((sid) => !en[String(sid)])) return "disabled";
  }
  return ageVerdict(paramAge(hexId), paramInterval(hexId));
}
export function paramIsStale(hexId) {
  const v = paramFreshness(hexId);
  return v === "stale" || v === "disabled";
}
function spark(hexId, cls = "") {
  const stale = paramIsStale(hexId);
  return renderSparkline(S.series[hexId] || [], { stale, cls, w: 110, h: 24 });
}
/* Spacecraft mode, TM-derived with provenance (M5-M8). */
export function scModeInfo() {
  const p = S.state.params || {};
  const tm = p["0x0700"];
  if (tm !== undefined && tm !== null) {
    return { label: SC_MODES[Number(tm)] ?? `MODE_${tm}`, src: "TM 0x0700" };
  }
  const obdh = (S.state.obdh || {}).mode;
  if (obdh !== undefined && obdh !== null) {
    return { label: SC_MODES[Number(obdh)] ?? String(obdh), src: "TM obdh.mode" };
  }
  if (S.timeMode === "sim" && S.state.sc_mode) {
    return { label: String(S.state.sc_mode).toUpperCase(), src: "SIM (training)" };
  }
  return { label: "—", src: "not downlinked" };
}

/* ═══ 4. TRANSPORT ════════════════════════════════════════════════════════ */

let ws = null;
let restFallback = null;

function handleState(data) {
  S.state = data || {};
  adoptServerClock(S.state);
  if (S.state.schedule) S.s11Schedule = S.state.schedule;
  // feed sparkline rings for every schematic/triad param we track
  const p = S.state.params || {};
  for (const hexId of TRACKED_SERIES) pushSeries(hexId, p[hexId]);
  // measured Eb/N0 + RSSI ring for the predicted-vs-measured overlay (G5).
  // Timestamped (sim time), unlike the sparkline rings, because it must be
  // drawn against the prediction's absolute time axis.
  const eb = Number(p["0x0519"]), rssi = Number(p["0x0502"]);
  if (Number.isFinite(eb)) {
    S.linkMeasured.push({ t: simNowSec(), eb, rssi: Number.isFinite(rssi) ? rssi : null });
    if (S.linkMeasured.length > 300) S.linkMeasured.shift();
  }
  updateChartGate();
  updateSaHeader();
  updateRailHealth();
  updateContingency();
  updateTrainingChip();
  renderCurrentPage(false);
  // The drawer used to be driven ONLY by handleTMPacket, so on a quiet link --
  // or in bootloader mode, where only SID 11 flows -- CMD HISTORY and the
  // event/TM logs froze (field report 2026-08-20). State packets arrive on a
  // fixed cadence; renderBottomDrawer() no-ops when the drawer is shut.
  renderBottomDrawer();
}

function handleTMPacket(pkt) {
  if (!pkt) return;
  const ts = tsShort();
  S.tmLog.unshift(packetRowText(pkt, ts));
  if (S.tmLog.length > RING) S.tmLog.length = RING;
  if (pkt.event) {
    const sevMap = { 1: "info", 2: "warn", 3: "warn", 4: "alarm" };
    const msg = eventText(pkt.event);
    pushEvent({ ts, sev: pkt.event.rejection ? "warn" : (sevMap[pkt.event.severity] || "info"),
      label: pkt.event.rejection ? "REJECT" : `EVT`, msg,
      instructor: /^INSTRUCTOR OVERRIDE/.test(pkt.event.description || "") });
    routeEvent(msg, pkt.event.event_id);
    if (pkt.event.severity >= 3) announce(`Alarm: event ${hex4(pkt.event.event_id)} — ${pkt.event.description || ""}`);
  }
  if (pkt.monitoring) {
    const m = pkt.monitoring;
    pushEvent({ ts, sev: m.transition === "out_of_limits" ? "warn" : "nom",
      label: m.transition === "out_of_limits" ? "OOL" : "NOM",
      msg: `S12 ${hex4(m.param_id)} = ${num(m.value, 2)} [${m.transition}]` });
    if (S.page === "fdir" || S.page === "diag") loadFdir();
  }
  if (pkt.schedule) { S.s11Schedule = pkt.schedule; renderS11Table(); }
  if (pkt.monitor_defs || pkt.ea_defs || pkt.fdir_journal) loadFdir();
  if (pkt.memory_dump) renderMemoryDump(pkt.memory_dump);
  if (pkt.memory_check) renderMemoryCheck(pkt.memory_check);
  if (pkt.verification) {
    const v = pkt.verification;
    const okSub = v.subtype === 1 || v.subtype === 7;
    pushEvent({ ts, sev: okSub ? "nom" : "alarm", label: `S1.${v.subtype}`,
      msg: `TC(${v.tc_service},${v.tc_subtype}) ${okSub ? "OK" : "FAILED"}` +
           (v.code !== undefined ? ` code ${hex4(v.code)}` : "") });
    noteVerification(v);
  }
  renderBottomDrawer();
}

/* Audible cue on a NEW alarm (classic playAlarmBeep capability; Agent 5
 * inventory fix). WebAudio, one short tone, swallowed silently when the
 * browser blocks audio before a user gesture. */
function playAlarmBeep() {
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    if (!playAlarmBeep._ctx) playAlarmBeep._ctx = new AC();
    const ctx = playAlarmBeep._ctx;
    const osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.frequency.value = 880; gain.gain.value = 0.06;
    osc.connect(gain); gain.connect(ctx.destination);
    osc.start(); osc.stop(ctx.currentTime + 0.15);
  } catch (e) { /* audio unavailable — visual + live-region cues remain */ }
}

function handleAlarmMsg(alarm) {
  if (!alarm) return;
  const i = S.alarms.findIndex((a) => a.id === alarm.id);
  const isNew = i < 0;
  if (i >= 0) S.alarms[i] = alarm; else S.alarms.unshift(alarm);
  renderAlarmDrawer();
  updateSaHeader();
  if (isNew) playAlarmBeep();
  announce(`Alarm: ${alarm.subsystem || ""} ${alarm.message || ""}`);
}
function handleAlarmCleared(msg) {
  const ids = msg.ids || (msg.id !== undefined ? [msg.id] : []);
  for (const id of ids) {
    const a = S.alarms.find((x) => x.id === id);
    if (a) a.rtn = true;
  }
  renderAlarmDrawer();
}

export function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  if (ws) ws.close();
  ws = wsClient(`${proto}//${location.host}/ws?position=${encodeURIComponent(S.position)}`, {
    state: (m) => handleState(m.data),
    tm: (m) => handleTMPacket(m.packet),
    alarm: (m) => handleAlarmMsg(m.alarm),
    alarm_cleared: (m) => handleAlarmCleared(m),
    go_nogo_poll: (m) => handleGoNoGo(m),
    go_nogo_status: (m) => handleGoNoGo(m),
    go_nogo_result: (m) => handleGoNoGo(m),
    _open() { S.wsConnected = true; wsDot(true); },
    _close() { S.wsConnected = false; wsDot(false); },
    _unknown() { /* forward-compatible: ignore */ },
  });
}
function wsDot(on) { const el = $("ws-dot"); if (el) el.classList.toggle("on", on); }
function setPosition(pos) {
  S.position = pos;
  try { localStorage.setItem("mcs.position", pos); } catch (e) { /* private mode */ }
  if (ws && ws.readyState() === 1) ws.send({ type: "set_position", position: pos });
  else connectWS();
  loadTcCatalog();     // server filters `allowed` per position
  applyPositionFilter();
  renderCurrentPage(true);
}

/* REST fallback: /api/state every 2 s ONLY while the WS is down (classic
 * cadence preserved; route per M12 — never the sim-named one). */
function startRestFallback() {
  restFallback = poller(async () => {
    if (S.wsConnected) return;
    handleState(await getJSON("/api/state"));
  }, 2000);
  restFallback.start();
}

/* Time-source resolution (M1). Preferred: the MCS's own /api/client-config
 * `time_source` field (additive server change, Agent 5) — no cross-origin
 * call needed. Fallbacks kept: the planner's /api/time-source via the
 * configured planner base (M13), then the published speed (a rate ≠ 1.0 can
 * only be simulation). Unknown -> chip hidden. */
async function resolveTimeMode() {
  const cc = S.clientConfig || {};
  if (cc.time_source === "real" || cc.time_source === "sim") {
    S.timeMode = cc.time_source;
    updateTrainingChip();
    return;
  }
  const base = S.clientConfig.planner_url;
  if (base) {
    try {
      const t = await getJSON(`${base}/api/time-source`);
      if (t && (t.time_source === "real" || t.time_source === "sim")) {
        S.timeMode = t.time_source;
        updateTrainingChip();
        return;
      }
    } catch (e) { /* planner unreachable — fall through */ }
  }
  S.timeMode = "unknown";
  updateTrainingChip();
}

export function trainingChipState() {
  const speed = Number(S.state.speed);
  const simEvidence = S.timeMode === "sim" ||
    (S.timeMode === "unknown" && Number.isFinite(speed) && speed !== 1.0);
  if (S.timeMode === "real" || !simEvidence) return { visible: false, text: "" };
  const paused = Number.isFinite(speed) && speed === 0;
  const text = paused ? "TRAINING · PAUSED"
    : `TRAINING · SIM x${Number.isFinite(speed) ? speed : 1}`;
  return { visible: true, text };
}
function updateTrainingChip() {
  const c = trainingChipState();
  const el = $("training-chip");
  if (!el) return;
  el.classList.toggle("hide", !c.visible);
  el.textContent = c.text;
}

/* ═══ 5. SA HEADER ════════════════════════════════════════════════════════ */

function startClocks() {
  setInterval(() => {
    setText("utc-clock", fmtUTC(simNow(), { seconds: true }));
    // strip the shared formatter's "MET " prefix — this clock already has a
    // MET label above it (Agent 5 screenshot fix: read "MET MET 000/…")
    setText("met-clock", fmtMET(S.state.met_s).replace(/^MET /, ""));
    const tc = S.state.time_correlation || {};
    const cuc = fmtCUC(tc.sc_cuc_coarse, tc.sc_cuc_fine, tc.epoch);
    setText("sc-time-clock", cuc.utc === "—" ? "—" : cuc.utc.slice(11));
    updateNextCard();     // countdowns re-derive every second on simNow()
  }, 1000);
  // The PUS panels' fetched inputs -- S12 monitor definitions, S19 rules, the
  // HK structure table -- used to refresh only on an S12 transition event or a
  // tab click, so a monitor that changed quietly never showed. Poll them while
  // a page that renders them is actually on screen.
  setInterval(() => {
    if (S.page === "fdir" || S.page === "diag") loadFdir();
    if (S.page === "diag" && diagTab === "s3") loadHkStructures();
  }, 5000);
}

function saDot(id, cls) {
  const el = $(id);
  if (el) el.className = "sadot" + (cls ? " " + cls : "");
}

export function updateSaHeader() {
  const st = S.state;
  const sa = st.sa || {};
  // SEE — link freshness, TM tier active vs commanded, store fill
  const age = sa.last_frame_age_s;
  const fr = freshness(age);
  const link = (sa.link || fr).toUpperCase();
  setText("sa-see-v", age === undefined || age === null ? "NO TM" : `${link} · ${fmtAge(age)}`);
  const tierBits = [];
  if (sa.tm_tier_name || sa.tm_tier !== undefined) {
    tierBits.push(`tier ${sa.tm_tier_name || sa.tm_tier}`);
    if (sa.tm_tier_demoted) tierBits.push(`cmd ${sa.tm_tier_cmd_name || sa.tm_tier_cmd} — ${sa.tm_tier_demotion_reason || "demoted"}`);
  }
  if (sa.store_fill_pct !== undefined && sa.store_fill_pct !== null)
    tierBits.push(`store ${num(sa.store_fill_pct, 0)} % (${sa.store_fill_source || ""})`);
  setText("sa-see-s", tierBits.join(" · ") || "awaiting HK");
  saDot("sa-see-dot", fr === "fresh" ? "" : fr === "degraded" ? "w" : "stale");
  const seeCard = $("sa-see");
  if (seeCard) seeCard.className = "sacard" + (fr === "stale" ? " a-st" : fr === "degraded" ? " w" : "");

  // TALK — lock chain, last TC verdict, GS in contact
  const p = st.params || {};
  const locks = [p["0x0510"], p["0x0511"], p["0x0512"]].map((v) => Number(v) >= 1);
  const inContact = !!st.in_contact || locks[2];
  const gs = st.active_gs_name || (st.ttc || {}).active_gs_name || "";
  setText("sa-talk-v", inContact ? `LINK · ${gs || "IN CONTACT"}` : "NO CONTACT");
  const lockTxt = `carrier ${locks[0] ? "✓" : "—"} · bit ${locks[1] ? "✓" : "—"} · frame ${locks[2] ? "✓" : "—"}`;
  const lastV = S.verifLog[0];
  setText("sa-talk-s", lockTxt + (lastV ? ` · last TC ${lastV.verdict}` : ""));
  saDot("sa-talk-dot", inContact ? "" : "stale");

  // WELL — sc mode (TM-derived + provenance), alarm counts, load shed
  const mode = scModeInfo();
  const unack = st.unacked_alarms ?? sa.unacked_alarms ?? S.alarms.filter((a) => !a.acknowledged).length;
  const counts = alarmCounts();
  setText("sa-well-v", `${mode.label} · ${counts.alarm} ALM ${counts.warn} WRN`);
  const shed = p["0x0134"];
  // G1: SoC-at-eclipse-exit is one of the two header-grade projections.
  // Valid -> the number (ground-derived); invalid -> nothing here (the EPS
  // page carries the full INVALID row with its reason — header space is
  // rationed).
  const socExit = derivedEntry("eps.soc_at_eclipse_exit_pred");
  setText("sa-well-s", `mode src: ${mode.src} · ${unack} unacked` +
    (Number(shed) > 0 ? ` · LOAD SHED L${shed}` : "") +
    (socExit && socExit.valid ? ` · SoC@ecl-exit ${num(socExit.value, 0)} % (gnd)` : ""));
  saDot("sa-well-dot", counts.alarm ? "a" : counts.warn ? "w" : "");
  const wellCard = $("sa-well");
  if (wellCard) wellCard.className = "sacard" + (counts.alarm ? " a-st" : counts.warn ? " w" : "");
  setText("alarm-unack-n", String(unack));
  setText("alarm-drawer-unack", `${unack} UNACK`);

  updateNextCard();
}

function updateNextCard() {
  // NEXT — next AOS/LOS countdown, next eclipse, next scheduled activity
  const nowS = simNowSec();
  let cur = null, next = null;
  for (const c of S.contactSchedule) {
    const aos = c.aos_time ?? c.aos_utc, los = c.los_time ?? c.los_utc;
    if (aos === undefined || los === undefined) continue;
    if (aos <= nowS && nowS < los) cur = c;
    else if (aos > nowS && (!next || aos < (next.aos_time ?? next.aos_utc))) next = c;
  }
  const cd = (t) => {
    const d = Math.max(0, t - nowS);
    const m = Math.floor(d / 60), s2 = Math.floor(d % 60);
    return `${String(m).padStart(2, "0")}:${String(s2).padStart(2, "0")}`;
  };
  let v = "—", sub = "no pass schedule";
  if (cur) { v = `LOS IN ${cd(cur.los_time ?? cur.los_utc)}`; sub = `${cur.ground_station || ""} pass in progress`; }
  else if (next) { v = `AOS IN ${cd(next.aos_time ?? next.aos_utc)}`; sub = `${next.ground_station || ""}`; }
  const ecl = S.state.next_eclipse_s ?? (S.state.orbit || {}).next_eclipse_s;
  if (ecl !== undefined && ecl !== null) sub += ` · eclipse in ${fmtAge(ecl)}`;
  // G1/G3: pass-fit ratio (backlog ÷ capacity) is the second header-grade
  // projection — it answers "will this pass be enough?" next to the AOS/LOS
  // countdown it depends on.
  const fit = derivedEntry("ttc.pass_fit_ratio");
  if (fit && fit.valid) {
    sub += ` · fit ${num(fit.value, 2)} ${Number(fit.value) > 1.0 ? "WON'T FIT" : "fits"} (gnd)`;
  }
  setText("sa-next-v", v);
  setText("sa-next-s", sub);
  saDot("sa-next-dot", cur || next ? "" : "stale");
}

function alarmCounts() {
  let warn = 0, alarm = 0;
  const ls = S.state.param_limit_state || {};
  for (const k in ls) {
    if (String(ls[k]).startsWith("red")) alarm++;
    else if (String(ls[k]).startsWith("yellow")) warn++;
  }
  for (const a of S.alarms) {
    if (a.acknowledged || a.rtn) continue;
    if (Number(a.severity) >= 3) alarm++; else warn++;
  }
  return { warn, alarm };
}

function updateContingency() {
  // M8: obdh.mode primary, 0x0700 secondary — never raw sim sc_mode.
  const mode = scModeInfo();
  const bad = mode.label === "SAFE" || mode.label === "EMERGENCY";
  const el = $("contingency-banner");
  if (!el) return;
  el.classList.toggle("hide", !bad);
  if (bad) el.textContent =
    `CONTINGENCY — spacecraft mode ${mode.label} (source: ${mode.src}). ` +
    `Open FDIR for recovery state; alarms drawer for the journal.`;
}

/* ═══ 6. NAV — rail, pages, position filtering, keyboard ══════════════════ */

function buildRail() {
  const host = $("nav-rail");
  if (!host) return;
  host.innerHTML = RAIL.map(([id, label]) => {
    if (id === "|") return `<div class="sep" role="separator"></div>`;
    return `<button id="rail-${id}" data-page="${id}" role="tab" aria-selected="false"
      title="${esc(pageTitle(id))}">${esc(label)}<span class="hd" id="rail-hd-${id}"></span></button>`;
  }).join("");
  host.setAttribute("role", "tablist");
  host.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-page]");
    if (b) showPage(b.dataset.page);
  });
  host.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight" &&
        e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
    e.preventDefault();
    const dir = (e.key === "ArrowRight" || e.key === "ArrowDown") ? 1 : -1;
    navRelative(dir);
  });
}
function pageTitle(id) {
  return { ovr: "Overview (L1)", eps: "Electrical power", tcs: "Thermal control",
    aocs: "Attitude & orbit control", ttc: "Telemetry, tracking & command",
    pld: "Payload", prp: "Propulsion", obd: "On-board data handling",
    cmd: "Commanding", fdir: "FDIR", proc: "Procedures", trnd: "Trends",
    diag: "PUS services — S3 HK · S11 schedule · S6 memory · S15 stores · " +
          "S12 monitoring · S19 event-action · S9 time · parameters",
    man: "Operations manual" }[id] || id;
}
function visiblePages() {
  return RAIL.filter(([id]) => id !== "|").map(([id]) => id)
    .filter((id) => railVisibleForPosition(id, S.positions[S.position], S.position === "flight_director"));
}
function navRelative(dir) {
  const vis = visiblePages();
  const i = vis.indexOf(S.page);
  const nxt = vis[(i + dir + vis.length) % vis.length];
  showPage(nxt);
  const b = $(`rail-${nxt}`);
  if (b) b.focus();
}

/* Position filtering of the rail (classic tabVisibleForPosition, mapped). */
export function railVisibleForPosition(pageId, pos, seeAll) {
  if (seeAll) return true;
  if (!pos) return true;
  const classic = PAGE_TO_CLASSIC[pageId];
  const visibleTabs = pos.visible_tabs || [];
  const subsystems = pos.subsystems || [];
  if (pageId === "fdir") return subsystems.includes("fdir");
  return visibleTabs.includes(classic);
}

export function applyPositionFilter() {
  const pos = S.positions[S.position];
  const seeAll = S.position === "flight_director";
  for (const [id] of RAIL) {
    if (id === "|") continue;
    const b = $(`rail-${id}`);
    if (b) b.hidden = !railVisibleForPosition(id, pos, seeAll);
  }
  if (!railVisibleForPosition(S.page, pos, seeAll)) showPage("ovr");
  // overview schematic node filtering
  const overview = pos ? (pos.overview_subsystems || pos.subsystems || []) : [];
  for (const [pageId, sub] of Object.entries(PAGE_SUBSYSTEM)) {
    const node = $(`ovr-node-${pageId === "pld" ? "pld" : pageId}`);
    if (node) node.style.display = (seeAll || overview.includes(sub)) ? "" : "none";
  }
  renderQuickCommands();     // in-place quick commands re-filter
  renderManual();            // manual section filter
  if (S.page === "man") loadProcedureCards();   // cards are per position
  const pi = POSITION_INFO[S.position] || {};
  setText("pos-detail", pi.title || S.position);
}

function updateRailHealth() {
  const ls = S.state.param_limit_state || {};
  const counts = {};
  for (const [hexId, st2] of Object.entries(ls)) {
    const def = S.paramByHex[hexId];
    if (!def) continue;
    const sub = def.subsystem;
    if (!counts[sub]) counts[sub] = { w: 0, a: 0 };
    if (String(st2).startsWith("red")) counts[sub].a++;
    else if (String(st2).startsWith("yellow")) counts[sub].w++;
  }
  for (const [pageId, sub] of Object.entries(PAGE_SUBSYSTEM)) {
    const el = $(`rail-hd-${pageId}`);
    if (!el) continue;
    const c = counts[sub] || { w: 0, a: 0 };
    // health dot + NUMERAL — never colour alone (P6)
    if (c.a) { el.className = "hd a"; el.textContent = String(c.a); }
    else if (c.w) { el.className = "hd w"; el.textContent = String(c.w); }
    else { el.className = "hd"; el.textContent = ""; }
  }
  const alarmsHd = $("rail-hd-fdir");
  if (alarmsHd) {
    const unack = S.alarms.filter((a) => !a.acknowledged).length;
    alarmsHd.className = unack ? "hd a" : "hd";
    alarmsHd.textContent = unack ? String(unack) : "";
  }
}

export function showPage(id) {
  if (!id) return;
  S.page = id;
  for (const [pid] of RAIL) {
    if (pid === "|") continue;
    const sec = $(`page-${pid}`);
    if (sec) sec.classList.toggle("hide", pid !== id);
    const b = $(`rail-${pid}`);
    if (b) { b.classList.toggle("on", pid === id); b.setAttribute("aria-selected", pid === id ? "true" : "false"); }
  }
  const work = $("main");
  if (work) work.scrollTop = 0;
  renderCurrentPage(true);
}

function initKeyboard() {
  const REGIONS = ["top-bar", "nav-rail", "main", "bottom-drawer"];
  document.addEventListener("keydown", (e) => {
    const tag = (e.target && e.target.tagName || "").toLowerCase();
    const typing = tag === "input" || tag === "select" || tag === "textarea";
    if (e.key === "F6") {
      e.preventDefault();
      const cur = REGIONS.findIndex((r) => $(r) && $(r).contains(document.activeElement));
      const nxt = $(REGIONS[(cur + 1 + REGIONS.length) % REGIONS.length]);
      if (nxt) { nxt.setAttribute("tabindex", "-1"); nxt.focus(); }
      return;
    }
    if (e.key === "Escape") {
      if ($("help-overlay") && $("help-overlay").classList.contains("open")) { toggleHelp(false); return; }
      if ($("chain-overlay") && $("chain-overlay").classList.contains("open")) { toggleChain(false); return; }
      if ($("alarm-drawer") && $("alarm-drawer").classList.contains("open")) { toggleAlarmDrawer(false); return; }
      if (S.page !== "ovr") showPage("ovr");
      return;
    }
    if (typing) return;
    if (e.key === "?") { toggleHelp(); return; }
    if (e.key.toLowerCase() === "a") { toggleAlarmDrawer(true); return; }
    if (e.key.toLowerCase() === "k") { ackFirstUnacked(); return; }
  });
}

/* ═══ 7. SCHEMATICS ═══════════════════════════════════════════════════════ */

let schematicsDoc = null;   // raw svg text -> symbol innerHTML by id
const TRACKED_SERIES = ["0x0101", "0x0105", "0x0135", "0x0217", "0x025B", "0x0502",
  "0x0503", "0x0406", "0x0407", "0x0408", "0x0601", "0x0604", "0x0B01", "0x0302", "0x0312"];

async function loadSchematics() {
  try {
    const txt = await (await fetch("/static/modern/schematics.svg")).text();
    schematicsDoc = {};
    const re = /<symbol id="([^"]+)" viewBox="([^"]+)">([\s\S]*?)<\/symbol>/g;
    let m;
    while ((m = re.exec(txt))) schematicsDoc[m[1]] = { viewBox: m[2], body: m[3] };
  } catch (e) { schematicsDoc = {}; }
}
function schematicMarkup(symbolId) {
  const sym = schematicsDoc && schematicsDoc[symbolId];
  if (!sym) return `<div class="ds-banner stale">schematic unavailable</div>`;
  return `<div class="stage"><svg class="sc" viewBox="${esc(sym.viewBox)}" ` +
         `data-schematic="${esc(symbolId)}">${sym.body}</svg></div>`;
}
function bindPageSchematic(pageId) {
  const sec = $(`page-${pageId}`);
  if (!sec || !sec.querySelectorAll) return;
  sec.querySelectorAll("svg[data-schematic]").forEach((svg) => bindSchematic(svg, S.state));
}

/* schematic-as-navigation: one document-level listener for ds-nav */
function initSchematicNav() {
  document.addEventListener("ds-nav", (e) => {
    const page = e.detail && e.detail.page;
    if (page) showPage(page);
  });
}

/* ═══ 8. OVERVIEW (L1) ════════════════════════════════════════════════════ */

function buildOverviewPage() {
  return `
  <div class="pad" id="page-ovr" role="tabpanel" aria-label="Overview">
    <div class="row" style="justify-content:space-between">
      <div><h1 class="pt">Spacecraft overview</h1>
        <p class="sub">The schematic is the navigation — click the thing you are worried
        about. Position: <span id="pos-detail">—</span></p></div>
      <div class="row mono" id="ovr-orbit-bar" style="gap:16px;font-size:13px"></div>
    </div>
    <div class="guided-note" data-ann="ovr.schematic"></div>
    <!-- ovr-* classes (Agent 5 screenshot fix): L1 is viewport-fit — the
         schematic absorbs the leftover height, so the work area never
         scrolls at 1366x768 (plan §2.1 "L1/L2 fit ... without scroll"). -->
    <div class="g ovr-grid" style="grid-template-columns:minmax(0,1fr) 400px">
      <div class="g ovr-left">
        <div class="ds-card ovr-schem-card"><h2>EOSAT-1 · functional schematic
          <span class="r">grey is nominal — colour marks deviation (P1)</span></h2>
          <div class="bd" id="ovr-schematic"></div></div>
        <div class="ds-card"><h2>Contact windows (24 h)
          <span class="r" id="ovr-timeline-src">/api/contacts</span></h2>
          <div class="bd">
            <canvas id="ovr-timeline" height="64" style="width:100%"></canvas>
            <div id="ovr-timeline-legend" class="row" style="font-size:12px;margin-top:4px"></div>
            <div class="guided-note" data-ann="ovr.timeline"></div>
            <!-- capped + scrollable (Agent 5): keeps L1 inside 1366x768 -->
            <div class="scroll-y" style="max-height:150px">
              <table class="plain" id="ovr-pass-table"></table></div>
          </div></div>
      </div>
      <div class="g ovr-right" style="align-content:start">
        <div class="ds-card"><h2>Ground track</h2>
          <div class="bd" id="ovr-map" style="min-height:180px"></div></div>
        <div class="ds-card"><h2>System board <span class="r">/api/displays/system-overview</span></h2>
          <div class="bd" id="ovr-board"></div></div>
      </div>
    </div>
  </div>`;
}

function updateOverview() {
  // orbit bar
  const st = S.state;
  const orbitBits = [
    `LAT ${num(st.lat_deg ?? (st.orbit || {}).lat_deg, 2)}°`,
    `LON ${num(st.lon_deg ?? (st.orbit || {}).lon_deg, 2)}°`,
    `ALT ${num(st.alt_km ?? (st.orbit || {}).alt_km, 0)} km`,
    (st.eclipse ?? paramVal("0x0108")) ? "ECLIPSE" : "SUNLIT",
    st.in_contact ? "CONTACT" : "NO CONTACT",
  ];
  setHTML("ovr-orbit-bar", orbitBits.map((b) => `<span>${esc(b)}</span>`).join(""));
  // schematic
  const host = $("ovr-schematic");
  if (host && !host.dataset.mounted && schematicsDoc) {
    host.innerHTML = schematicMarkup("sch-ovr");
    host.dataset.mounted = "1";
  }
  const mode = scModeInfo();
  const modeEl = document.getElementById("sch-ovr-mode");
  if (modeEl) modeEl.textContent = `${mode.label} (${mode.src})`;
  bindPageSchematic("ovr");
  renderContactTimeline();
  renderPassTable();
  renderGroundTrack();
}

function renderContactTimeline() {
  const cv = $("ovr-timeline");
  if (!cv || !cv.getContext) return;
  const ctx = cv.getContext("2d");
  if (!ctx || !ctx.fillRect) return;
  const W = cv.width = cv.clientWidth || 800, H = cv.height = 64;
  ctx.clearRect(0, 0, W, H);
  const nowS = simNowSec();
  const t0 = nowS - 2 * 3600, t1 = nowS + 22 * 3600;
  const x = (t) => ((t - t0) / (t1 - t0)) * W;
  ctx.fillStyle = "#22272F"; ctx.fillRect(0, 24, W, 16);
  const stations = {};
  let si = 0;
  for (const c of S.contacts) {
    const aos = c.aos_utc ?? c.aos_time, los = c.los_utc ?? c.los_time;
    if (aos === undefined || los > t1 || los < t0) continue;
    const gs = c.ground_station || "GS";
    if (!(gs in stations)) stations[gs] = si++;
    ctx.fillStyle = "#8A93A0";
    ctx.globalAlpha = 0.55 + 0.15 * (stations[gs] % 3);
    ctx.fillRect(x(aos), 24, Math.max(2, x(los) - x(aos)), 16);
    ctx.globalAlpha = 1;
  }
  ctx.fillStyle = "#E6E9ED"; ctx.fillRect(x(nowS) - 1, 8, 2, 48);
  ctx.fillStyle = "#8A93A0"; ctx.font = "10px monospace";
  ctx.fillText("NOW", Math.min(W - 26, x(nowS) + 4), 16);
  setHTML("ovr-timeline-legend", Object.keys(stations)
    .map((gs) => renderChip({ kind: "neutral", label: gs })).join(" "));
}

function renderPassTable() {
  const rows = S.contactSchedule.slice(0, 8).map((c, i) => {
    const aos = c.aos_time ?? c.aos_utc, los = c.los_time ?? c.los_utc;
    const dur = aos !== undefined && los !== undefined ? Math.round((los - aos) / 60) : null;
    const nowS = simNowSec();
    const status = los < nowS ? "PAST" : aos <= nowS ? "IN PROGRESS" : "UPCOMING";
    return `<tr><td>${i + 1}</td><td>${esc(c.ground_station || "")}</td>
      <td class="mono">${fmtUTC(aos, { date: false })}</td>
      <td class="mono">${fmtUTC(los, { date: false })}</td>
      <td class="mono">${dur === null ? "—" : dur + " m"}</td>
      <td class="mono">${num(c.max_elevation, 0)}°</td><td>${status}</td></tr>`;
  }).join("");
  setHTML("ovr-pass-table",
    `<thead><tr><th>#</th><th>Station</th><th>AOS</th><th>LOS</th><th>Dur</th><th>Max el</th><th>Status</th></tr></thead>` +
    `<tbody>${rows || `<tr><td colspan="7">no pass schedule</td></tr>`}</tbody>`);
}

let worldMap = null;
function renderGroundTrack() {
  const host = $("ovr-map");
  if (!host) return;
  const st = S.state;
  const lat = st.lat_deg ?? (st.orbit || {}).lat_deg;
  const lon = st.lon_deg ?? (st.orbit || {}).lon_deg;
  if (typeof SvgWorldMap === "undefined") {
    // Vendor asset guard (plan R3): designed absence, never an exception.
    host.innerHTML = `<div class="ds-banner stale">map library not installed
      (/static/vendor/js/svg-world-map.js)</div>
      <div class="mono" style="margin-top:8px;font-size:13px">
      subsatellite point: ${num(lat, 2)}°, ${num(lon, 2)}°</div>`;
    return;
  }
  try {
    if (!worldMap) { host.innerHTML = ""; worldMap = new SvgWorldMap(host); }
    worldMap.removeLayer && worldMap.removeLayer("sc");
    if (lat !== undefined && lon !== undefined)
      worldMap.addMarker && worldMap.addMarker(lat, lon, { layer: "sc" });
    for (const gs of S.groundStations)
      worldMap.addMarker && worldMap.addMarker(gs.lat ?? gs.lat_deg, gs.lon ?? gs.lon_deg, { layer: "gs" });
  } catch (e) {
    host.innerHTML = `<div class="ds-banner stale">map render failed — ${esc(String(e.message || e))}</div>`;
  }
}

async function refreshSystemBoard() {
  try {
    const d = await getJSON("/api/displays/system-overview");
    const subs = d.subsystems || d.rows || [];
    const chips = (Array.isArray(subs) ? subs : Object.entries(subs).map(([k, v]) => ({ name: k, ...v })))
      .map((s2) => renderChip({
        kind: /alarm|red/i.test(s2.state || s2.status || "") ? "alarm"
          : /warn|yellow/i.test(s2.state || s2.status || "") ? "warn" : "ok",
        label: (s2.name || s2.subsystem || "?").toUpperCase() })).join(" ");
    const params = d.key_parameters || d.parameters || [];
    const rows = params.slice(0, 12).map((r) => `<tr>
      <td>${esc(r.name || "")}</td><td class="mono">${esc(r.id || r.hex || "")}</td>
      <td class="mono">${esc(String(r.value ?? "—"))}</td><td>${esc(r.state || "")}</td></tr>`).join("");
    setHTML("ovr-board", `<div class="row">${chips}</div>` +
      (rows ? `<table class="plain" style="margin-top:8px"><tbody>${rows}</tbody></table>` : "") +
      `<div style="font-size:12px;color:var(--tx3);margin-top:6px">source: /api/displays/system-overview</div>`);
  } catch (e) {
    setHTML("ovr-board", `<div class="ds-banner stale">system overview unavailable</div>`);
  }
}

/* ═══ 9. SUBSYSTEM PAGES (L2) — one skeleton × 7 ══════════════════════════ */

/* Per-subsystem composition: schematic symbol, key-param triads, stepper
 * definition, equipment cards. AND table + quick commands + events + dump
 * playback are identical machinery for all seven. */
const L2 = {
  eps: { title: "Electrical power", sym: "sch-eps",
    key: ["0x0101", "0x0105", "0x0100", "0x0109", "0x0102", "0x0135", "0x0120"],
    stepper: () => {
      const stage = Number(paramVal("0x0134"));
      return { label: "Load shed ladder", src: "0x0134",
        states: ["L0 NOMINAL", "L1 PAYLOAD", "L2 NON-ESS", "L3 SURVIVAL"],
        active: Number.isFinite(stage) ? stage : -1 };
    },
    equipment: [["SA-A", "0x0103", "A"], ["SA-B", "0x0104", "A"], ["BATTERY", "0x0101", "%"],
      ["BUS", "0x0105", "V"], ["MPPT", "0x0122", ""], ["LOAD", "0x0148", "A"]] },
  tcs: { title: "Thermal control", sym: "sch-tcs",
    key: ["0x0406", "0x0407", "0x0408", "0x0409", "0x0411", "0x041A"],
    stepper: () => ({ label: "FPA cooldown", src: "0x040C", states: ["OFF", "COOLING", "REGULATING"],
      active: Number(paramVal("0x040C")) > 0 ? (Number(paramVal("0x0425")) > 0 ? 2 : 1) : 0 }),
    equipment: [["HTR BAT", "0x040A", ""], ["HTR OBC", "0x040B", ""], ["COOLER FPA", "0x040C", ""],
      ["HTR THR", "0x041B", ""], ["DUTY BAT", "0x040E", "%"], ["DUTY OBC", "0x040F", "%"]] },
  aocs: { title: "Attitude & orbit control", sym: "sch-aocs",
    key: ["0x0217", "0x0204", "0x0205", "0x0206", "0x025B", "0x0282", "0x02C5"],
    stepper: () => {
      const m = Number(paramVal("0x020F"));
      // states from the catalogue (ultimately aocs/aocs_mode_codes.yaml, which
      // calls itself canonical); AOCS_MODES stays as the pre-fetch fallback.
      const cat = paramStates("0x020F");
      const names = cat ? Object.keys(cat).map(Number).sort((x, y) => x - y).map((k) => cat[String(k)])
                        : AOCS_MODES;
      return { label: "AOCS mode chain", src: "0x020F", states: names,
        active: Number.isFinite(m) ? m : -1 };
    },
    equipment: [["RW1", "0x0207", "RPM"], ["RW2", "0x0208", "RPM"], ["RW3", "0x0209", "RPM"],
      ["RW4", "0x020A", "RPM"], ["ST1", "0x0241", "stars"], ["ST2", "0x0242", "stars"],
      ["GYRO", "0x0273", "°C"], ["GPS", "0x0276", "sats"]] },
  ttc: { title: "Telemetry, tracking & command", sym: "sch-ttc",
    key: ["0x0502", "0x0503", "0x050C", "0x050F", "0x0506", "0x0519"],
    stepper: () => {
      const locks = [paramVal("0x0510"), paramVal("0x0511"), paramVal("0x0512")]
        .map((v) => Number(v) >= 1);
      const active = locks[2] ? 3 : locks[1] ? 2 : locks[0] ? 1 : 0;
      return { label: "Link lock chain", src: "0x0510",
        states: ["NO SIGNAL", "CARRIER", "BIT", "FRAME"], active };
    },
    equipment: [["XPDR", "0x0507", "°C"], ["PA", "0x050F", "°C"], ["PA ON", "0x0516", ""],
      ["BEACON", "0x0526", ""], ["TIER", "0x0539", ""], ["TIER CMD", "0x053A", ""]] },
  pld: { title: "Payload", sym: "sch-pld",
    key: ["0x0601", "0x0604", "0x0605", "0x0616", "0x0602", "0x060D"],
    stepper: () => ({ label: "Instrument state", src: "0x0600",
      states: ["OFF", "STANDBY", "IMAGING", "CAL", "TRANSFER"],
      active: (() => { const m = Number(paramVal("0x0600")); return Number.isFinite(m) ? Math.min(m, 4) : -1; })() }),
    equipment: [["FPA", "0x0601", "°C"], ["COOLER", "0x0602", "W"], ["STORE", "0x0604", "%"],
      ["IMAGES", "0x0605", ""], ["SNR", "0x0616", "dB"], ["FPA READY", "0x0610", ""]] },
  prp: { title: "Propulsion", sym: "sch-prp",
    key: ["0x0B01", "0x0B03", "0x0B04", "0x0B05", "0x0B0C", "0x0B16"],
    stepper: () => {
      const st2 = Number(paramVal("0x0B0D"));
      return { label: "Burn sequence", src: "0x0B0D",
        states: ["IDLE", "ARMED", "BURNING", "COMPLETE"],
        active: Number.isFinite(st2) ? Math.min(st2, 3) : 0 };
    },
    equipment: [["TANK", "0x0B01", "bar"], ["LATCH A", "0x0B06", ""], ["LATCH B", "0x0B07", ""],
      ["FCV", "0x0B08", ""], ["CATBED", "0x0B05", "°C"], ["INHIBIT", "0x0B15", ""]] },
  obd: { title: "On-board data handling", sym: "sch-obd",
    key: ["0x0302", "0x0303", "0x0312", "0x0313", "0x0319", "0x0308"],
    stepper: () => ({ label: "Active OBC", src: "0x030C",
      states: ["OBC-A", "OBC-B"],
      active: Number(paramVal("0x030C")) === 1 ? 1 : 0 }),
    equipment: [["CPU", "0x0302", "%"], ["MEM", "0x0303", "%"], ["HKTM BUF", "0x0312", "%"],
      ["EVT BUF", "0x0313", "%"], ["SEU", "0x0319", ""], ["CAN", "0x0A11", ""]] },
};

function buildSubsystemPage(pageId) {
  const cfg = L2[pageId];
  const sub = PAGE_SUBSYSTEM[pageId];
  return `
  <!-- l2-* classes (Agent 5 screenshot fix): L2 pages are viewport-fit —
       the two columns scroll internally, the work area itself never
       scrolls (plan §2.1 "L1/L2 fit 1366×768 without scroll"). -->
  <div class="pad hide l2-page" id="page-${pageId}" role="tabpanel" aria-label="${esc(cfg.title)}">
    <div class="row" style="justify-content:space-between">
      <div><h1 class="pt">${esc(cfg.title)}</h1>
        <p class="sub">Level 2 · subsystem <span class="mono">${esc(sub)}</span> ·
          <a href="#" data-alarm-filter="${esc(sub)}">alarms for this subsystem</a></p></div>
      <button class="ibtn" data-page-nav="ovr">← Overview</button>
    </div>
    <div class="guided-note" data-ann="${pageId}.schematic"></div>
    <div class="g l2-grid" style="grid-template-columns:minmax(0,1fr) 420px">
      <div class="g l2-col">
        <div class="ds-card"><h2>Schematic <span class="r">click a block to navigate; values live</span></h2>
          <div class="bd" id="${pageId}-schematic"></div></div>
        <div class="ds-card"><h2>Parameters <span class="r">AND — generated from /api/param-catalog (P10)</span></h2>
          <div class="bd scroll-y" style="max-height:300px" id="${pageId}-and"></div></div>
        <div class="ds-card"><h2>Stored TM playback <span class="r">S15 dump → /api/tm-dump-data</span></h2>
          <div class="bd" id="${pageId}-dump">
            <div class="row">
              <button class="ds-btn" data-dump-request="${esc(sub)}">REQUEST DUMP (S15)</button>
              <span id="${pageId}-dump-status" style="font-size:12px;color:var(--tx3)">no dump loaded</span>
            </div>
            <canvas id="${pageId}-dump-chart" height="120" class="hide"></canvas>
            <input type="range" id="${pageId}-dump-scrub" class="hide" style="width:100%" min="0" max="100" value="100"
              aria-label="Playback position">
          </div></div>
      </div>
      <div class="g l2-col" style="align-content:start">
        <div class="ds-card"><h2>Key parameters</h2>
          <div class="bd" id="${pageId}-params"></div></div>
        ${(DERIVED_BY_PAGE[pageId] || []).length || FIT_PAGES.includes(pageId) ? `
        <div class="ds-card"><h2>Projections <span class="r">ground-derived · not downlinked</span></h2>
          <div class="bd">
            <div class="guided-note" data-ann="l2.derived"></div>
            ${FIT_PAGES.includes(pageId) ? `<div class="guided-note" data-ann="${pageId}.pass-fit"></div>` : ""}
            <div id="${pageId}-derived"></div>
          </div></div>` : ""}
        ${pageId === "pld" ? `<div class="ds-card"><h2>Capture readiness (NOM-002)
          <span class="r">gates from annotations.yaml</span></h2>
          <div class="bd">
            <div class="guided-note" data-ann="pld.capture-gates"></div>
            <div id="pld-capture"></div>
          </div></div>` : ""}
        ${pageId === "aocs" ? `<div class="ds-card"><h2>FINE-entry readiness
          <span class="r">aocs.fine_ready 0x02C5 decoded</span></h2>
          <div class="bd">
            <div class="guided-note" data-ann="aocs.fine-ready"></div>
            <div id="aocs-fineready"></div>
          </div></div>` : ""}
        ${pageId === "prp" ? `<div class="ds-card"><h2>Arm interlocks
          <span class="r">prop.arm_inhibit_mask 0x0B15 decoded</span></h2>
          <div class="bd">
            <div class="guided-note" data-ann="prp.arm-inhibit"></div>
            <div id="prp-inhibit"></div>
          </div></div>
        <div class="ds-card"><h2>Loaded vs planned burn
          <span class="r">TM 0x0B0E/0x0B10 vs planner orbit_manoeuvre</span></h2>
          <div class="bd">
            <div class="guided-note" data-ann="prp.burn-plan"></div>
            <div id="prp-burnplan">awaiting planner…</div>
          </div></div>` : ""}
        ${pageId === "ttc" ? `<div class="ds-card"><h2>Predicted vs measured link
          <span class="r">/api/displays/link-prediction</span></h2>
          <div class="bd">
            <div class="guided-note" data-ann="ttc.link-overlay"></div>
            <div id="ttc-linkpred-now"></div>
            <canvas id="ttc-linkpred-chart" height="140" class="hide"></canvas>
          </div></div>
        <div class="ds-card"><h2>Blind commanding (CON-004)
          <span class="r">requires FD GO — armed toggle</span></h2>
          <div class="bd">
            <div class="guided-note" data-ann="ttc.blind-tc"></div>
            <div class="row">
              <button class="ds-btn danger" data-blind-toggle="1" id="ttc-blind-btn">ENABLE BLIND-TC</button>
              <span id="ttc-blind-status" style="font-size:12.5px;color:var(--tx3)"></span>
            </div>
          </div></div>` : ""}
        <div class="ds-card"><h2 id="${pageId}-stepper-title">Mode</h2>
          <div class="bd" id="${pageId}-stepper"></div></div>
        <div class="ds-card"><h2>Equipment</h2>
          <div class="bd g" style="grid-template-columns:1fr 1fr" id="${pageId}-equip"></div></div>
        <div class="ds-card"><h2>Command <span class="r">in place · position-gated (P9)</span></h2>
          <div class="bd" id="${pageId}-quick">
            <div class="guided-note" data-ann="l2.quick-commands"></div>
            <div id="${pageId}-quick-list"></div></div></div>
        ${pageId === "ttc" ? `<div class="ds-card"><h2>Ground station network
          <span class="r">/api/ground-stations</span></h2>
          <div class="bd" id="ttc-gs-panel">awaiting station list…</div></div>` : ""}
        ${pageId === "eps" ? `<div class="ds-card"><h2>Power budget
          <span class="r">/api/displays/power-budget</span></h2>
          <div class="bd" id="eps-power-budget">awaiting data…</div></div>` : ""}
        <div class="ds-card"><h2>Events</h2>
          <div class="bd scroll-y" style="max-height:180px" id="${pageId}-events"></div></div>
      </div>
    </div>
  </div>`;
}

/* Ground-station network board (classic TTC "GROUND STATION STATUS" panel;
 * Agent 5 inventory fix). Station list from /api/ground-stations; the
 * station currently in contact (state.sa.gs_in_contact / state.in_contact)
 * is marked and carries the live elevation from the geometry params. */
function renderGsNetwork() {
  const host = $("ttc-gs-panel");
  if (!host) return;
  if (!S.groundStations.length) {
    host.innerHTML = `<div class="ds-banner stale">station list unavailable (/api/ground-stations)</div>`;
    return;
  }
  const activeGs = (S.state.sa || {}).gs_in_contact ||
    (S.state.in_contact ? (S.state.ttc || {}).ground_station : null);
  const el = paramVal("0x050A"), rng = paramVal("0x0509"); // ttc.contact_elevation / ttc.range_km
  host.innerHTML = `<table class="plain"><tbody>` + S.groundStations.map((g) => {
    const nm = g.name || g.id || "GS";
    const live = activeGs && nm === activeGs;
    return `<tr><td>${live ? "●" : "○"} ${esc(nm)}${live ? " · IN CONTACT" : ""}</td>
      <td class="mono">${num(g.lat_deg ?? g.lat, 1)}° / ${num(g.lon_deg ?? g.lon, 1)}°</td>
      <td class="mono">${live ? `EL ${num(el, 0)}° · ${num(rng, 0)} km` : "—"}</td></tr>`;
  }).join("") + `</tbody></table>`;
}

/* Power budget board (classic EPS tab capability; Agent 5 inventory fix —
 * checklist row "POWER BUDGET board <- /api/displays/power-budget"). */
async function refreshPowerBudget() {
  const host = $("eps-power-budget");
  if (!host || S.page !== "eps") return;
  try {
    const d = await getJSON("/api/displays/power-budget");
    if (!d.has_data) { host.innerHTML = `<div class="ds-banner stale">no power telemetry yet</div>`; return; }
    const row = (k2, v) => `<tr><td>${esc(k2)}</td><td class="mono">${esc(String(v ?? "—"))}</td></tr>`;
    const per = Object.entries(d.per_subsystem_power || {})
      .map(([k2, v]) => row(k2.toUpperCase(), `${num(v, 1)} W`)).join("");
    host.innerHTML = `<table class="plain"><tbody>` +
      row("GENERATION", `${num(d.power_gen_w, 1)} W`) +
      row("CONSUMPTION", `${num(d.power_cons_w, 1)} W`) +
      row("MARGIN", `${num(d.power_margin_w, 1)} W`) +
      row("SOC", `${num(d.battery_soc_percent, 1)} % (${esc(d.soc_trend || "")})`) +
      row("LOAD SHED", esc(d.load_shedding_label || "—")) +
      per + `</tbody></table>` +
      (d.breakdown_note ? `<div style="font-size:12px;color:var(--tx3);margin-top:6px">${esc(d.breakdown_note)}</div>` : "");
  } catch (e) {
    host.innerHTML = `<div class="ds-banner stale">power budget unavailable</div>`;
  }
}

function updateSubsystemPage(pageId, full) {
  const cfg = L2[pageId];
  const sub = PAGE_SUBSYSTEM[pageId];
  const host = $(`${pageId}-schematic`);
  if (host && !host.dataset.mounted && schematicsDoc) {
    host.innerHTML = schematicMarkup(cfg.sym);
    host.dataset.mounted = "1";
  }
  bindPageSchematic(pageId);
  // key param triads + embedded sparklines
  setHTML(`${pageId}-params`, cfg.key.map((h) =>
    `<div class="row" style="gap:6px;align-items:center">
       <div style="flex:1">${paramTriad(h)}</div>${spark(h)}</div>`).join(""));
  // stepper (ONE component for all chains — P5)
  const stp = cfg.stepper();
  // A chain widget reads as "this IS the state right now". Without a staleness
  // check it kept asserting the last known step forever after its driving
  // parameter went silent (field report 2026-08-20) -- the most confident
  // possible way to show nothing.
  const stpFr = stp.src ? paramFreshness(stp.src) : "fresh";
  const stpDead = stpFr === "stale" || stpFr === "disabled";
  setText(`${pageId}-stepper-title`,
    stp.label + (stpDead ? (stpFr === "disabled" ? "  — HK DISABLED (TC 3,5)" : "  — STALE") : ""));
  setHTML(`${pageId}-stepper`,
    renderStepper(stp.states, stpDead ? -1 : stp.active));
  // equipment cards
  setHTML(`${pageId}-equip`, cfg.equipment.map(([nm, h, u]) => {
    const v = paramVal(h);
    const eng = engValue(h, v);
    const fr = paramFreshness(h);
    const lc = (fr === "stale" || fr === "disabled") ? "stale" : limitClass((S.state.param_limit_state || {})[h]);
    return `<div class="ds-card" style="border-radius:6px"><div class="bd" style="padding:8px 10px">
      <div style="font-size:12px;color:var(--tx3)">${esc(nm)} <span class="mono">${esc(h)}</span></div>
      <div class="mono" style="font-size:15px;font-weight:600;
        color:${lc === "a" ? "var(--alarm)" : lc === "w" ? "var(--warn)" : "var(--tx)"}">
        ${fr === "stale" ? "—" : (eng !== null ? esc(eng) : num(v, 1) + (u ? " " + esc(u) : ""))}</div></div></div>`;
  }).join(""));
  if (full) {
    // AND table from the catalog (never drifts from the dictionary)
    const params = S.paramCatalog.filter((p) => p.subsystem === sub);
    setHTML(`${pageId}-and`, renderAndTable(params, liveValues()));
    renderQuickCommands();
  }
  // POSITION_NEEDS gap surfaces on their owning pages
  renderDerivedCard(pageId);                                     // G1 + G3
  if (pageId === "aocs") setHTML("aocs-fineready",
    renderBitmaskChips("0x02C5", { clearLabel: "READY FOR FINE" }));   // G2
  if (pageId === "prp") {
    setHTML("prp-inhibit",
      renderBitmaskChips("0x0B15", { clearLabel: "ARM PERMITTED" }));  // G2
    renderBurnCard();                                            // G9
  }
  if (pageId === "pld") renderCaptureCard();                     // G4 (+G2)
  if (pageId === "ttc") { renderBlindTc(); renderLinkOverlay(); } // G12 + G5
  if (pageId === "ttc") renderGsNetwork();
  setHTML(`${pageId}-events`, (S.subsysEvents[sub] || []).slice(-30).reverse()
    .map((e) => `<div class="logrow">${esc(e)}</div>`).join("") ||
    `<div class="logrow nom">No events</div>`);
}

/* ═══ 9b. POSITION-NEEDS GAP SURFACES (G1–G9, G12, G15) ═══════════════════ */
/* The L3 tier POSITION_NEEDS §6 found missing: ground-derived projections
 * (G1), bitmask decoders (G2), fit verdict (G3), capture-readiness composite
 * (G4), predicted-vs-measured link (G5), first-out alarm grouping (G6),
 * GO/NO-GO call-matrix context (G7), console log (G8), loaded-vs-planned
 * burn (G9), blind-TC tracking (G12), baseline-vs-now health (G15).
 * Everything here renders honest absence: a missing endpoint, an invalid
 * projection or an unreachable planner is a designed state with a reason,
 * never a fabricated number. */

/* — G1: derived-parameter rows (server block `state.derived`, 0x0803-0x080A) — */
const DERIVED_BY_PAGE = {
  eps: ["eps.soc_at_eclipse_exit_pred", "eps.time_to_soc_yellow"],
  aocs: ["aocs.time_to_mom_70pct"],
  obd: ["obdh.time_to_buf_full", "obdh.time_offset_ms"],
  ttc: ["ttc.pass_data_capacity_bytes"],
  prp: ["prop.catbed_ready_eta"],
};
const FIT_PAGES = ["ttc", "pld"];           // G3 verdict line lives here
const DERIVED_LABELS = {
  "eps.soc_at_eclipse_exit_pred": "SoC at eclipse exit (predicted)",
  "eps.time_to_soc_yellow": "Time to SoC yellow limit",
  "aocs.time_to_mom_70pct": "Time to 70 % momentum",
  "obdh.time_to_buf_full": "Time to buffer full",
  "obdh.time_offset_ms": "OBC − ground time offset",
  "ttc.pass_data_capacity_bytes": "Pass data capacity",
  "prop.catbed_ready_eta": "Catbed ready ETA",
  "ttc.pass_fit_ratio": "Pass fit ratio",
};
export function derivedEntry(name) {
  const d = S.state.derived;
  return (d && d[name]) ? d[name] : null;
}
export function fmtDerivedValue(ent) {
  if (!ent || !ent.valid || ent.value === null || ent.value === undefined) return "—";
  const v = Number(ent.value);
  const u = String(ent.units || "");
  if (u === "s") return fmtAge(v);
  if (u === "bytes") return v >= 1e6 ? `${(v / 1e6).toFixed(1)} MB` : `${Math.round(v / 1e3)} kB`;
  if (u === "ms") return `${num(v, 0)} ms`;
  if (u === "ratio") return num(v, 2);
  return `${num(v, 1)}${u ? " " + u : ""}`;
}
const GND_MARK = (id) =>
  `<span class="ds-badge" title="ground-derived ${esc(id || "0x08xx")} — computed by the MCS ` +
  `from archive trend + orbit state; NOT downlinked from the spacecraft">GND</span>`;
export function renderDerivedRow(name) {
  const ent = derivedEntry(name);
  const label = DERIVED_LABELS[name] || name;
  if (!ent) {
    return `<div class="logrow nom">${esc(label)} — no derived block in /api/state ` +
           `(server predates POSITION_NEEDS G1?)</div>`;
  }
  const head = `<span style="font-size:12px;color:var(--tx3)">${esc(label)} ` +
               `<span class="mono">${esc(ent.id)}</span></span>`;
  if (!ent.valid) {
    // The server NEVER sends a number when invalid; say why, prominently.
    return `<div class="row derived-row" style="justify-content:space-between;gap:6px">
      ${head}
      <span style="font-size:12px;color:var(--stale)">INVALID — ${esc(ent.reason || "no reason given")}
        ${GND_MARK(ent.id)}</span></div>`;
  }
  return `<div class="row derived-row" style="justify-content:space-between;gap:6px">
    ${head}
    <span class="mono" style="font-size:14px;font-weight:600">${fmtDerivedValue(ent)}
      ${GND_MARK(ent.id)}</span></div>`;
}
/* — G3: the "will it fit" verdict line — */
export function fitVerdictLine() {
  const ent = derivedEntry("ttc.pass_fit_ratio");
  if (!ent) return `<div class="logrow nom">PASS FIT — no derived block</div>`;
  if (!ent.valid) {
    return `<div class="logrow" style="color:var(--stale)">PASS FIT — INVALID: ` +
           `${esc(ent.reason || "")} ${GND_MARK(ent.id)}</div>`;
  }
  const r = Number(ent.value);
  const wont = r > 1.0;
  const tight = !wont && r > 0.9;
  const chip = renderChip({
    kind: wont ? "alarm" : tight ? "warn" : "ok",
    label: wont ? "will NOT fit" : tight ? "fits (tight)" : "fits",
    title: "backlog ÷ (tier rate × time to LOS); > 1.0 means the queued data will not move this pass",
  });
  return `<div class="row" style="gap:8px;align-items:center">
    <span class="mono" style="font-size:14px;font-weight:600">PASS FIT ${num(r, 2)}</span>
    ${chip} ${GND_MARK(ent.id)}</div>`;
}
function renderDerivedCard(pageId) {
  const host = $(`${pageId}-derived`);
  if (!host) return;
  const rows = (DERIVED_BY_PAGE[pageId] || []).map(renderDerivedRow).join("");
  const fit = FIT_PAGES.includes(pageId) ? fitVerdictLine() : "";
  host.innerHTML = rows + fit ||
    `<div class="logrow nom">no projections for this subsystem</div>`;
}

/* — G2: bitmask decoders (0x02C5 fine_ready, 0x0B15 arm_inhibit_mask) —
 * The bit legends ARE parsed from the served /api/param-catalog descriptions
 * (decision per POSITION_NEEDS G2: both descriptions follow the "bitN label,
 * bitN label" / "bN label" convention and are the single source of truth —
 * a table in annotations.yaml would drift from the dictionary). */
export function parseBitLegend(description) {
  const s = String(description || "");
  const re = /\b(?:bit|b)(\d+)\b[ :=-]*/g;
  const marks = [];
  let m;
  while ((m = re.exec(s))) marks.push({ bit: Number(m[1]), end: re.lastIndex, start: m.index });
  const out = [];
  for (let i = 0; i < marks.length; i++) {
    let label = s.slice(marks[i].end, i + 1 < marks.length ? marks[i + 1].start : s.length);
    // strip teaching parentheticals (kept in the catalogue, too long for a chip)
    label = label.replace(/\s*\(([^)]{25,})\)/g, "");
    label = label.replace(/[,;.\s]+$/, "").replace(/^[,;\s]+/, "").trim();
    if (label) out.push({ bit: marks[i].bit, label });
  }
  return out;
}
export function renderBitmaskChips(hexId, opts = {}) {
  const def = S.paramByHex[hexId] || {};
  const legend = parseBitLegend(def.description);
  if (!legend.length) {
    return `<div class="ds-banner stale">no bit legend in the parameter catalogue for ` +
           `${esc(hexId)} — cannot decode</div>`;
  }
  const raw = paramVal(hexId);
  const stale = paramIsStale(hexId);
  if (raw === undefined || raw === null || stale) {
    return `<div class="ds-banner stale">${esc(def.name || hexId)} not in current TM ` +
           `(${stale ? "stale" : "never seen"}) — chips withheld rather than guessed</div>`;
  }
  const mask = Number(raw) >>> 0;
  const legendMask = legend.reduce((a, b) => a | (1 << b.bit), 0);
  const allClear = (mask & legendMask) === 0;
  const chips = legend.map((b) => {
    const set = (mask >> b.bit) & 1;
    return renderChip({ kind: set ? "alarm" : "ok",
      label: `b${b.bit} ${b.label}`,
      title: set ? `bit ${b.bit} SET — ${b.label} is blocking/failing`
                 : `bit ${b.bit} clear — ${b.label} OK` });
  }).join(" ");
  const verdict = renderChip({ kind: allClear ? "ok" : "alarm",
    label: allClear ? (opts.clearLabel || "ALL CLEAR") : `${countBits(mask & legendMask)} BLOCKING` });
  return `<div class="row" style="gap:6px;margin-bottom:6px">
      <span class="mono" style="font-size:13px">${esc(def.name || hexId)} = 0x${mask.toString(16).toUpperCase().padStart(4, "0")}</span>
      ${verdict}</div>
    <div class="row" style="gap:4px">${chips}</div>`;
}
function countBits(v) { let n = 0; for (let x = v >>> 0; x; x >>= 1) n += x & 1; return n; }

/* — G4: capture-readiness composite (NOM-002 gates, table in annotations.yaml
 *      pld.capture-gates.gates so instructors can tune it) — */
export function captureGateTable() {
  const cfg = (S.annotations.pld || {})["capture-gates"];
  return (cfg && Array.isArray(cfg.gates)) ? cfg.gates : [];
}
export function evalCaptureGate(g) {
  const v = paramVal(g.param);
  const stale = paramIsStale(g.param);
  if (v === undefined || v === null || stale) {
    return { state: "unknown", actual: null,
             why: stale ? "telemetry stale" : "parameter not in TM" };
  }
  const n = Number(v);
  let pass = false;
  const val = Number(g.value);
  switch (String(g.op)) {
    case "eq": pass = n === val; break;
    case "ne": pass = n !== val; break;
    case "gt": pass = n > val; break;
    case "ge": pass = n >= val; break;
    case "lt": pass = n < val; break;
    case "le": pass = n <= val; break;
    case "range": pass = n >= Number(g.min) && n <= Number(g.max); break;
    default: return { state: "unknown", actual: n, why: `unknown op ${g.op}` };
  }
  return { state: pass ? "pass" : "fail", actual: n };
}
export function computeCaptureReadiness() {
  const gates = captureGateTable();
  if (!gates.length) return { available: false };
  const rows = gates.map((g) => ({ ...g, ...evalCaptureGate(g) }));
  const composite = rows.some((r) => r.state === "fail") ? "NO-GO"
    : rows.some((r) => r.state === "unknown") ? "UNKNOWN" : "GO";
  return { available: true, rows, composite };
}
function renderCaptureCard() {
  const host = $("pld-capture");
  if (!host) return;
  const c = computeCaptureReadiness();
  if (!c.available) {
    host.innerHTML = `<div class="ds-banner stale">no gate table — add ` +
      `pld.capture-gates.gates to configs/&lt;mission&gt;/mcs/annotations.yaml</div>`;
    return;
  }
  const chips = c.rows.map((r) => renderChip({
    kind: r.state === "pass" ? "ok" : r.state === "fail" ? "alarm" : "stale",
    label: `${r.label || r.param}${r.actual === null ? "" : ` = ${num(r.actual, 1)}`}`,
    title: r.why || `${r.param} ${r.op} ${r.value ?? `${r.min}…${r.max}`}` })).join(" ");
  const cls = c.composite === "GO" ? "var(--ok)"
    : c.composite === "NO-GO" ? "var(--alarm)" : "var(--stale)";
  host.innerHTML = `
    <div class="row" style="gap:10px;align-items:center;margin-bottom:6px">
      <span class="mono" style="font-size:18px;font-weight:700;color:${cls}">${c.composite}</span>
      <span style="font-size:12px;color:var(--tx3)">capture readiness — all gates from three subsystems, one verdict</span>
    </div>
    <div class="row" style="gap:4px">${chips}</div>
    <div style="margin-top:8px;font-size:12px;color:var(--tx3)">FINE-entry preconditions (aocs.fine_ready — PL4: bit4 set means a desat is coming):</div>
    ${renderBitmaskChips("0x02C5", { clearLabel: "READY FOR FINE" })}`;
}

/* — G5: predicted-vs-measured link overlay (TTC page) — */
async function loadLinkPrediction() {
  if (S.page !== "ttc") return;
  try { S.linkPred = await getJSON("/api/displays/link-prediction"); }
  catch (e) { S.linkPred = { available: false, reason: "link-prediction endpoint unreachable" }; }
  renderLinkOverlay();
}
export function linkNowReadout() {
  const lp = S.linkPred;
  if (!lp || !lp.available) {
    return { available: false, reason: (lp && lp.reason) || "no prediction loaded" };
  }
  const tierIdx = Number(paramVal("0x0539"));
  const tierName = ["SAFE", "LOW", "HIGH"][tierIdx] ||
    ((lp.tiers && lp.tiers[0]) ? lp.tiers[0].name : "LOW");
  const nowP = lp.now;
  const predicted = nowP && nowP.tiers && nowP.tiers[tierName]
    ? Number(nowP.tiers[tierName].eb_n0_db) : null;
  const measured = Number(paramVal("0x0519"));
  const haveMeasured = Number.isFinite(measured) &&
    !paramIsStale("0x0519");
  return {
    available: true, tierName,
    predicted: Number.isFinite(predicted) ? predicted : null,
    measured: haveMeasured ? measured : null,
    delta: Number.isFinite(predicted) && haveMeasured ? measured - predicted : null,
    pass: lp.pass || null,
  };
}
function renderLinkOverlay() {
  const host = $("ttc-linkpred-now");
  if (!host) return;
  const r = linkNowReadout();
  if (!r.available) {
    host.innerHTML = `<div class="ds-banner stale">prediction unavailable — ${esc(r.reason)}</div>`;
    const cv0 = $("ttc-linkpred-chart");
    if (cv0) cv0.classList.add("hide");
    return;
  }
  const dChip = r.delta === null ? "" : renderChip({
    kind: r.delta < -6 ? "alarm" : r.delta < -3 ? "warn" : "ok",
    label: `Δ ${r.delta >= 0 ? "+" : ""}${num(r.delta, 1)} dB`,
    title: "measured minus predicted Eb/N0 at the current geometry — a large " +
           "negative delta is a real loss (pointing, weather, hardware), not physics" });
  host.innerHTML = `
    <div class="row" style="gap:8px;font-size:13px">
      <span>${esc((r.pass || {}).gs_name || "")} ${esc((r.pass || {}).which || "")} pass · tier ${esc(r.tierName)}</span>
      <span class="mono">predicted-now ${r.predicted === null ? "— (not in pass)" : num(r.predicted, 1) + " dB"}</span>
      <span class="mono">measured-now ${r.measured === null ? "—" : num(r.measured, 1) + " dB"}</span>
      ${dChip}
    </div>`;
  drawLinkOverlayChart();
}
let linkChart = null;
function drawLinkOverlayChart() {
  const cv = $("ttc-linkpred-chart");
  if (!cv) return;
  const lp = S.linkPred;
  if (typeof Chart === "undefined" || !lp || !lp.available) {
    cv.classList.add("hide");
    return;   // the now-readout above is the vendor-guard fallback
  }
  cv.classList.remove("hide");
  const series = (lp.series && lp.series.eb_n0_db) || {};
  const r = linkNowReadout();
  const pred = series[r.tierName] || [];
  const datasets = [
    { label: `predicted Eb/N0 (${r.tierName})`, pointRadius: 0, borderWidth: 1.2,
      borderDash: [5, 4], borderColor: "#8A93A0",
      data: pred.map((p) => ({ x: p[0] * 1000, y: p[1] })) },
    { label: "measured Eb/N0 (0x0519)", pointRadius: 1.5, borderWidth: 1.5,
      borderColor: "#E6E9ED", showLine: true,
      data: S.linkMeasured.map((p) => ({ x: p.t * 1000, y: p.eb })) },
  ];
  if (linkChart) linkChart.destroy();
  linkChart = new Chart(cv.getContext("2d"), {
    type: "line", data: { datasets },
    options: { animation: false,
      scales: { x: { type: "linear", ticks: { callback: (v) => fmtUTC(v / 1000) } } },
      plugins: { legend: { labels: { boxWidth: 10 } } } },
  });
}

/* — G6: first-out alarm grouping from /api/fault-graph — */
async function loadFaultGraph() {
  try { S.faultGraph = await getJSON("/api/fault-graph"); }
  catch (e) { S.faultGraph = null; }
}
export function groupAlarmsByAncestry(alarms, graph, minSize = 4) {
  const rest = alarms.slice();
  if (!graph || !graph.available || !Array.isArray(graph.edges) || !graph.edges.length) {
    return { groups: [], rest };
  }
  const byParam = {};
  for (const n of graph.nodes || []) if (n.param_id) byParam[n.param_id] = n;
  // transitive descendant sets (graph is small — tens of nodes)
  const children = {};
  for (const e of graph.edges) (children[e.parent] = children[e.parent] || []).push(e.child);
  const desc = {};
  for (const id of Object.keys(children)) {
    const seen = new Set();
    const stack = [...children[id]];
    while (stack.length) {
      const c = stack.pop();
      if (seen.has(c)) continue;
      seen.add(c);
      for (const g of children[c] || []) stack.push(g);
    }
    desc[id] = seen;
  }
  const active = alarms.filter((a) => !a.rtn);
  const entries = active
    .map((a) => ({ a, n: a.param_id !== undefined ? byParam[hex4(a.param_id)] : null }))
    .filter((e) => e.n);
  // roots with the MOST active descendants claim their cascade first
  const activeDescCount = (e) => {
    const d = desc[e.n.id];
    return d ? entries.filter((o) => o !== e && d.has(o.n.id)).length : 0;
  };
  entries.sort((x, y) => activeDescCount(y) - activeDescCount(x));
  const used = new Set();
  const groups = [];
  for (const e of entries) {
    if (used.has(e.a.id)) continue;
    const d = desc[e.n.id];
    if (!d) continue;
    const cons = entries.filter((o) => o !== e && !used.has(o.a.id) && d.has(o.n.id));
    if (1 + cons.length >= minSize) {
      groups.push({ root: e.a, rootNode: e.n, consequents: cons.map((c) => c.a) });
      used.add(e.a.id);
      for (const c of cons) used.add(c.a.id);
    }
  }
  return { groups, rest: alarms.filter((a) => !used.has(a.id)) };
}

/* — G7: GO/NO-GO call-matrix context — */
async function loadGonogoMatrix() {
  try { S.gonogoMatrix = await getJSON("/api/gonogo-matrix"); }
  catch (e) { S.gonogoMatrix = null; }
}
export function matchGonogoRow(label) {
  const rows = (S.gonogoMatrix && S.gonogoMatrix.matrix) || [];
  const l = String(label || "").toLowerCase();
  if (!l || !rows.length) return null;
  let best = null, bestScore = 0;
  for (const r of rows) {
    const toks = String(r.situation || "").toLowerCase()
      .split(/[^a-z0-9]+/).filter((t) => t.length > 2);
    const score = toks.filter((t) => l.includes(t)).length;
    if (score > bestScore) { bestScore = score; best = r; }
  }
  return bestScore >= 1 ? best : null;
}
export function renderGonogoMatrixRow(label) {
  if (S.gonogoMatrix && S.gonogoMatrix.available === false) {
    return `<div class="logrow nom">call matrix unavailable — ${esc(S.gonogoMatrix.reason || "")}</div>`;
  }
  const row = matchGonogoRow(label);
  if (!row) {
    return `<div class="logrow nom">no matching call-matrix row for this poll — ` +
           `free-form poll; FD arbitrates per manual/10_desk_procedures.md</div>`;
  }
  const who = (p) => (POSITION_INFO[p] || {}).title || String(p).toUpperCase();
  const responders = (Array.isArray(row.who_responds) ? row.who_responds : [row.who_responds])
    .map(who).join(", ");
  return `<div class="ds-card" style="margin-top:6px"><div class="bd" style="font-size:12.5px">
    <b>${esc(row.situation)}</b>${row.critical ? " " + renderChip({ kind: "alarm", label: "CRITICAL" }) : ""}<br>
    calls: <b>${esc(who(row.who_calls))}</b> · must respond: <b>${esc(responders)}</b><br>
    FD: ${esc(row.fd_decision || "")}
    <div style="color:var(--tx3);margin-top:2px">source: /api/gonogo-matrix (mcs/gonogo_matrix.yaml)</div>
  </div></div>`;
}

/* — G8: console / mission log (LOG drawer tab) — */
async function loadConsoleLog() {
  try {
    const d = await getJSON("/api/console-log?limit=300");
    S.consoleLog = d.entries || [];
  } catch (e) { /* keep previous */ }
  if (bdTab === "conlog") renderConsoleLog();
}
export function renderConsoleLog() {
  const host = $("conlog-list");
  if (!host) return;
  const k = S.conlogKind;
  const rows = S.consoleLog
    .filter((e) => k === "all" || e.kind === k)
    .slice(-200).reverse()
    .map((e) => {
      const cls = e.kind === "gonogo" ? "warn" : e.kind === "mark" ? "nom" : "";
      return `<div class="logrow ${cls}">${fmtUTC(e.utc_epoch, { seconds: true })} ` +
        `[${esc((e.kind || "").toUpperCase())}]` +
        `${e.position ? ` [${esc(e.position)}]` : ""}` +
        `${e.source === "server" ? ` <span class="ds-badge">AUTO</span>` : ""} ` +
        `${esc(e.text || "")}${e.ref ? ` <span class="mono">(${esc(e.ref)})</span>` : ""}</div>`;
    }).join("");
  host.innerHTML = rows || `<div class="logrow nom">console log empty — ` +
    `TC sends, verdicts, acks and GO/NO-GO results are appended by the server; ` +
    `MARK and notes by you</div>`;
}
async function postConsoleEntry(kind) {
  const el = $("conlog-input");
  const typed = el ? el.value.trim() : "";
  const text = kind === "mark" ? (typed || "MARK") : typed;
  if (!text) { announce("console log: type a note first"); return; }
  const r = await postJSON("/api/console-log",
    { position: S.position, kind, text });
  if (r.ok && el) el.value = "";
  announce(r.ok ? `logged (${kind})` : `console log rejected (${r.status})`);
  await loadConsoleLog();
  renderConsoleLog();
}

/* — G9: loaded-vs-planned burn card (PROP page) — */
export function plannedBurnDv(activity) {
  if (!activity) return NaN;
  return Number(activity.dv_mps ?? activity.dv ?? activity.delta_v_mps ?? activity.delta_v);
}
export function burnMismatch(tmDv, plannedDv) {
  if (!Number.isFinite(tmDv) || !Number.isFinite(plannedDv)) return null;
  return Math.abs(tmDv - plannedDv) > Math.max(0.05, 0.05 * Math.abs(plannedDv));
}
async function loadBurnPlan() {
  if (S.page !== "prp") return;
  const base = S.clientConfig.planner_url;
  if (!base) {
    S.burnPlan = { available: false,
      reason: "planner_url not configured — confirm the burn plan by voice with FDS" };
    renderBurnCard();
    return;
  }
  try {
    const d = await getJSON(`${base}/api/schedule`);
    const acts = (d.schedule || []).filter((a) => a.name === "orbit_manoeuvre");
    // prefer the next PLANNED manoeuvre; else the most recent one
    const next = acts.find((a) => a.state_name === "PLANNED") || acts[acts.length - 1] || null;
    S.burnPlan = { available: true, activity: next, count: acts.length };
  } catch (e) {
    // cross-origin / planner-down is a DESIGNED absent state (G9)
    S.burnPlan = { available: false,
      reason: "planner unreachable (offline or cross-origin) — confirm the burn plan by voice with FDS" };
  }
  renderBurnCard();
}
function renderBurnCard() {
  const host = $("prp-burnplan");
  if (!host) return;
  const dvTm = Number(paramVal("0x0B0E"));
  const tTm = Number(paramVal("0x0B10"));
  const tmStale = paramIsStale("0x0B0E");
  const tmRow = `<tr><td>LOADED (TM)</td>
    <td class="mono">${tmStale || !Number.isFinite(dvTm) ? "—" : num(dvTm, 2) + " m/s"}</td>
    <td class="mono">${tmStale || !Number.isFinite(tTm) ? "—" : num(tTm, 0) + " s burn"}</td>
    <td class="mono">0x0B0E / 0x0B10</td></tr>`;
  const bp = S.burnPlan;
  if (!bp) { host.innerHTML = `<table class="plain"><tbody>${tmRow}</tbody></table>` +
    `<div class="logrow nom">planner not queried yet…</div>`; return; }
  if (!bp.available) {
    host.innerHTML = `<table class="plain"><tbody>${tmRow}</tbody></table>` +
      `<div class="ds-banner stale">${esc(bp.reason)}</div>`;
    return;
  }
  if (!bp.activity) {
    host.innerHTML = `<table class="plain"><tbody>${tmRow}</tbody></table>` +
      `<div class="logrow nom">no orbit_manoeuvre activity in the plan` +
      `${Number.isFinite(dvTm) && dvTm > 0 && !tmStale
        ? ` — a burn is LOADED on board with nothing planned: query FDS`
        : ""}</div>`;
    return;
  }
  const a = bp.activity;
  const dvPlan = plannedBurnDv(a);
  const planRow = `<tr><td>PLANNED (FDS)</td>
    <td class="mono">${Number.isFinite(dvPlan) ? num(dvPlan, 2) + " m/s" : "— (no ΔV in activity)"}</td>
    <td class="mono">${a.start_time ? esc(String(a.start_time)) : "—"}</td>
    <td class="mono">${esc(a.procedure_ref || "")} #${a.id ?? "—"} ${esc(a.state_name || "")}</td></tr>`;
  const mism = burnMismatch(dvTm, dvPlan);
  const verdict = mism === null
    ? `<div class="logrow nom">not comparable — ` +
      `${tmStale || !Number.isFinite(dvTm) ? "no burn loaded in TM" : "activity carries no ΔV value"}; ` +
      `cross-check with FDS on the loop</div>`
    : mism
      ? `<div class="ds-banner alarm">MISMATCH — loaded ΔV ${num(dvTm, 2)} m/s ≠ planned ` +
        `${num(dvPlan, 2)} m/s. Do NOT arm until FDS confirms which is right (PR4).</div>`
      : `<div class="row" style="margin-top:4px">${renderChip({ kind: "ok", label: "LOADED = PLANNED" })}</div>`;
  host.innerHTML = `<table class="plain"><tbody>${tmRow}${planRow}</tbody></table>${verdict}`;
}

/* — G12: blind-TC mode (TT&C toggle, armed confirm, history tagging) — */
let blindArmTimer = null;
export function blindTcState() { return { ...S.blindTc }; }
export function toggleBlindTc() {
  if (S.blindTc.enabled) {
    S.blindTc.enabled = false;
    postJSON("/api/console-log", { position: S.position, kind: "note",
      text: "BLIND-TC mode DISABLED — TM confirmation restored" });
    announce("Blind-TC mode disabled");
  } else if (!S.blindTc.armPending) {
    S.blindTc.armPending = true;
    announce("Blind-TC ARM — press again within 8 s to confirm. FD GO required (CON-004).");
    blindArmTimer = setTimeout(() => { S.blindTc.armPending = false; renderBlindTc(); }, 8000);
  } else {
    if (blindArmTimer) { clearTimeout(blindArmTimer); blindArmTimer = null; }
    S.blindTc.armPending = false;
    S.blindTc.enabled = true;
    postJSON("/api/console-log", { position: S.position, kind: "note",
      text: "BLIND-TC mode ENABLED (FD GO per CON-004) — subsequent commands are " +
            "sent WITHOUT TM confirmation and tagged BLIND until a readback confirms" });
    announce("BLIND-TC MODE ENABLED — commands are now tagged BLIND");
  }
  renderBlindTc();
}
function renderBlindTc() {
  const btn = $("ttc-blind-btn");
  const st = $("ttc-blind-status");
  if (btn) {
    btn.textContent = S.blindTc.enabled ? "DISABLE BLIND-TC"
      : S.blindTc.armPending ? "CONFIRM BLIND-TC (armed)" : "ENABLE BLIND-TC";
    btn.classList.toggle("armed", S.blindTc.armPending);
  }
  if (st) {
    st.innerHTML = S.blindTc.enabled
      ? `<span class="blind-chip">BLIND-TC ACTIVE</span> commands go up without TM readback — log every send`
      : S.blindTc.armPending ? "armed — second press enables (voice FD GO first)"
      : "off — commands verify normally via S1 reports";
  }
}
export function blindHistoryChip(h) {
  if (!h || !h.blind) return "";
  return h.blind === "confirmed"
    ? `<span class="blind-chip confirmed" title="sent blind; a later TM readback (S1) confirmed it">BLIND ✓ TM</span>`
    : `<span class="blind-chip" title="sent in blind-TC mode — NO telemetry confirmation yet (CON-004)">BLIND</span>`;
}

/* — G15: baseline-vs-now health card (TRND page; baselines from
 *        annotations.yaml trnd.baseline-health.baselines) — */
export function baselineTable() {
  const cfg = (S.annotations.trnd || {})["baseline-health"];
  return (cfg && cfg.baselines && typeof cfg.baselines === "object") ? cfg.baselines : null;
}
export function computeBaselineDeltas() {
  const t = baselineTable();
  if (!t) return { available: false };
  const rows = Object.entries(t).map(([hexId, b]) => {
    const base = Number(b.value);
    const label = b.label || (S.paramByHex[hexId] || {}).name || hexId;
    const units = b.units || (S.paramByHex[hexId] || {}).units || "";
    const raw = paramVal(hexId);
    const stale = paramIsStale(hexId);
    if (raw === undefined || raw === null || stale) {
      return { hexId, label, units, base, now: null, delta: null, drift: null,
               flag: false, state: "no-tm" };
    }
    const now = Number(raw);
    const delta = now - base;
    const drift = base !== 0 ? (delta / Math.abs(base)) * 100 : null;
    const flag = drift !== null
      ? Math.abs(drift) > 5
      : Math.abs(delta) > (Number(b.abs_flag) || 0) && delta !== 0;
    return { hexId, label, units, base, now, delta, drift, flag, state: "ok" };
  });
  return { available: true, rows,
           flagged: rows.filter((r) => r.flag).length };
}
export function renderBaselineCard() {
  const host = $("trnd-baseline");
  if (!host) return;
  const c = computeBaselineDeltas();
  if (!c.available) {
    host.innerHTML = `<div class="ds-banner stale">no commissioning baseline — the instructor ` +
      `fills trnd.baseline-health.baselines in configs/&lt;mission&gt;/mcs/annotations.yaml</div>`;
    return;
  }
  const rows = c.rows.map((r) => {
    if (r.state === "no-tm") {
      return `<tr class="stale"><td>${esc(r.label)}</td><td class="mono">${esc(r.hexId)}</td>
        <td class="mono">${num(r.base, 2)}</td><td class="mono">—</td><td class="mono">—</td>
        <td>${renderChip({ kind: "stale", label: "NO TM" })}</td></tr>`;
    }
    const driftTxt = r.drift === null ? `Δ ${num(r.delta, 3)} ${esc(r.units)}`
      : `${r.drift >= 0 ? "+" : ""}${num(r.drift, 1)} %`;
    return `<tr><td>${esc(r.label)}</td><td class="mono">${esc(r.hexId)}</td>
      <td class="mono">${num(r.base, 2)}</td><td class="mono">${num(r.now, 2)} ${esc(r.units)}</td>
      <td class="mono">${driftTxt}</td>
      <td>${r.flag ? renderChip({ kind: "warn", label: ">5 % — NOM-011", dir: "down",
        title: "degraded more than 5 % against the commissioning baseline — long-term trend review (NOM-011)" })
        : renderChip({ kind: "ok", label: "in family" })}</td></tr>`;
  }).join("");
  host.innerHTML = `<table class="plain">
    <thead><tr><th>Metric</th><th>ID</th><th>Baseline</th><th>Now</th><th>Drift</th><th></th></tr></thead>
    <tbody>${rows}</tbody></table>
    <div style="font-size:12px;color:var(--tx3);margin-top:6px">baseline: commissioning snapshot ` +
    `(annotations.yaml) · drift computed on the console, flagged &gt;5 % per NOM-011</div>`;
}

/* ═══ 10. FRESHNESS — display-wide staleness gate (P4) ════════════════════ */

/* Promoted from the classic per-chart rule: when telemetry is stale the whole
 * display stops pretending. Sparklines get {stale:true} (line ends + cut
 * mark), dump/trend charts stop appending, values render em-dash via the
 * component layer. */
export function tmFreshness(st, subsystem) {
  const s2 = st || S.state;
  if (subsystem && s2.subsystem_staleness &&
      s2.subsystem_staleness[subsystem] !== undefined) {
    const sc = ((S.clientConfig || {}).subsystem_cadence || {})[subsystem];
    return ageVerdict(s2.subsystem_staleness[subsystem], sc ? sc.interval_s : null);
  }
  if (s2.stale) return "stale";
  return freshness(s2.last_frame_age_s);
}
function updateChartGate() { S.chartStale = tmFreshness() === "stale"; }

/* ═══ 11. CMD — commanding page ═══════════════════════════════════════════ */
/* Catalogue codec adapted from the classic console (same wire behaviour):
 * big-endian by default, S8.1 func_id octet first, linear calibration. */

function catFieldWidth(t) {
  return { uint8: 1, int8: 1, uint16: 2, int16: 2, uint32: 4, int32: 4,
    float32: 4, float64: 8 }[t] ?? null;   // null => variable hex tail
}
function catCal(f) { return f.calibration || {}; }
function catIsFloat(f) { return String(f.type || "").toLowerCase().indexOf("float") === 0; }
/* Engineering value -> raw wire value. The ENCODER owns the unit conversion
 * (classic parity): the operator types -15 °C, the wire carries centi-kelvin.
 * Integer types are rounded like the classic encoder; a variable hex tail
 * (`bytes` fields) passes through untouched. */
function catEngToRaw(f, eng) {
  if (catFieldWidth(String(f.type || "uint8").toLowerCase()) == null) return eng;
  if (catIsFloat(f)) return Number(eng);
  const cal = catCal(f);
  const ct = String(cal.type || "").toLowerCase();
  if (ct === "linear") {
    const scale = cal.scale != null ? cal.scale : 1;
    const offset = cal.offset != null ? cal.offset : 0;
    return Math.round((Number(eng) - offset) / scale);
  }
  if (ct === "polynomial") {
    const c = cal.coefficients || [];
    return Math.round((Number(eng) - (c[0] || 0)) / (c.length > 1 ? c[1] : 1));
  }
  return Math.round(Number(eng));
}
function catRawToEng(f, raw) {
  if (catIsFloat(f)) return Number(raw);
  const cal = catCal(f);
  const ct = String(cal.type || "").toLowerCase();
  if (ct === "linear") {
    const scale = cal.scale != null ? cal.scale : 1;
    const offset = cal.offset != null ? cal.offset : 0;
    return Number(raw) * scale + offset;
  }
  if (ct === "polynomial") {
    const c = cal.coefficients || [];
    let v = 0;
    for (let i = 0; i < c.length; i++) v += c[i] * Math.pow(Number(raw), i);
    return v;
  }
  return raw;
}
function catFieldDefault(f) {
  if (f.default !== undefined && f.default !== null) return f.default;
  if (f.range && f.range.min !== undefined) return f.range.min;
  if (f.min !== undefined) return f.min;
  if ((f.enum || []).length) return f.enum[0].value;
  return 0;
}
/* Engineering-units validation (classic catFieldError parity). Returns the
 * reason the spacecraft would reject the value, or null when valid. */
export function catFieldError(f, eng) {
  if (catFieldWidth(String(f.type || "uint8").toLowerCase()) == null) return null;
  if (eng === "" || eng === null || eng === undefined) return `${f.name}: no value`;
  const val = Number(eng);
  if (!Number.isFinite(val)) return `${f.name}: not a number`;
  if (f.range && (val < f.range.min || val > f.range.max)) {
    const u = f.unit ? ` ${f.unit}` : "";
    return `${f.name} = ${val}${u} is outside [${f.range.min} … ${f.range.max}]${u}` +
      ` — the spacecraft will REJECT this (S1.8)`;
  }
  const ct = String(catCal(f).type || "").toLowerCase();
  if (ct === "enum" && (f.enum || []).length) {
    if (!(f.enum || []).some((e) => Number(e.value) === Math.round(val)))
      return `${f.name} = ${val} is not an enumerated value`;
  }
  if (ct === "bitfield" && (f.bits || []).length) {
    let allowed = 0;
    for (const b of f.bits) if (!b.reserved) allowed |= (1 << b.bit);
    if (Math.round(val) & ~allowed)
      return `${f.name} = 0x${Math.round(val).toString(16).toUpperCase()} sets reserved bits`;
  }
  return null;
}
/* Format an engineering value for an input box (respects the field scale). */
export function catFormatEng(f, v) {
  if (catIsFloat(f)) return String(Math.round(Number(v) * 1e4) / 1e4);
  const cal = catCal(f);
  if (String(cal.type || "").toLowerCase() === "linear") {
    const scale = cal.scale != null ? cal.scale : 1;
    const decimals = Math.max(0, Math.ceil(-Math.log10(Math.abs(scale) || 1)));
    return Number(v).toFixed(decimals);
  }
  const n = Number(v);
  return Number.isFinite(n) ? String(Math.round(n)) : String(v);
}
/* The server speaks `criticality: "critical"|"caution"`; older fixtures used
 * a boolean `critical`. Arm→execute is required for CRITICAL only. */
export function isCritical(cmd) {
  if (!cmd) return false;
  return cmd.critical === true ||
    String(cmd.criticality || "").toLowerCase() === "critical";
}
export function catPackRaw(f, raw) {
  const t = String(f.type || "uint8").toLowerCase();
  const w = catFieldWidth(t);
  if (w == null) {
    const s2 = String(raw || "").replace(/[\s,]+/g, "");
    const out = [];
    for (let i = 0; i + 1 < s2.length + 1 && i < s2.length; i += 2)
      out.push(parseInt(s2.substr(i, 2), 16) || 0);
    return out;
  }
  const buf = new ArrayBuffer(w);
  const dv = new DataView(buf);
  const le = String(f.endian || "big").toLowerCase() === "little";
  const v = Number(raw);
  if (t === "float32") dv.setFloat32(0, v, le);
  else if (t === "float64") dv.setFloat64(0, v, le);
  else if (t === "int8") dv.setInt8(0, v);
  else if (t === "uint8") dv.setUint8(0, v & 0xFF);
  else if (t === "int16") dv.setInt16(0, v, le);
  else if (t === "uint16") dv.setUint16(0, v & 0xFFFF, le);
  else if (t === "int32") dv.setInt32(0, v, le);
  else dv.setUint32(0, v >>> 0, le);
  return Array.from(new Uint8Array(buf));
}
export function encodeCatalogCommand(cmd, args) {
  let bytes = [];
  if (Number(cmd.service) === 8 && Number(cmd.subtype) === 1 && cmd.func_id != null)
    bytes.push(Number(cmd.func_id) & 0xFF);
  for (const f of (cmd.fields || [])) {
    const eng = (args && args[f.name] !== undefined && args[f.name] !== "")
      ? args[f.name] : catFieldDefault(f);
    bytes = bytes.concat(catPackRaw(f, catEngToRaw(f, eng)));
  }
  return bytes.map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ");
}
export function decodeCatalogArgs(cmd, hexStr) {
  const s2 = String(hexStr || "").replace(/[\s,]+/g, "");
  if (s2.length % 2) return { ok: false, error: "odd number of hex digits" };
  const bytes = [];
  for (let i = 0; i < s2.length; i += 2) {
    const b = parseInt(s2.substr(i, 2), 16);
    if (isNaN(b)) return { ok: false, error: `bad hex at octet ${i / 2}` };
    bytes.push(b);
  }
  const values = {};
  let pos = 0;
  for (const f of (cmd.fields || [])) {
    const t = String(f.type || "uint8").toLowerCase();
    const w = catFieldWidth(t);
    if (w == null) {
      values[f.name] = bytes.slice(pos)
        .map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ");
      pos = bytes.length;
      continue;
    }
    if (pos + w > bytes.length)
      return { ok: false, error: `${bytes.length} byte(s) is too short for ${f.name}` };
    const dv = new DataView(new Uint8Array(bytes.slice(pos, pos + w)).buffer);
    const le = String(f.endian || "big").toLowerCase() === "little";
    let raw;
    if (t === "float32") raw = dv.getFloat32(0, le);
    else if (t === "float64") raw = dv.getFloat64(0, le);
    else if (t === "int8") raw = dv.getInt8(0);
    else if (t === "uint8") raw = dv.getUint8(0);
    else if (t === "int16") raw = dv.getInt16(0, le);
    else if (t === "uint16") raw = dv.getUint16(0, le);
    else if (t === "int32") raw = dv.getInt32(0, le);
    else raw = dv.getUint32(0, le);
    values[f.name] = catRawToEng(f, raw);
    pos += w;
  }
  if (pos !== bytes.length)
    return { ok: false, error: `${bytes.length - pos} trailing byte(s)` };
  return { ok: true, values };
}
export function sidToHex(sid) {
  const v = Number(sid) & 0xFFFF;
  return [(v >> 8) & 0xFF, v & 0xFF]
    .map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ");
}
export function provenanceText(cmd) {
  if (!cmd) return "";
  let head = `S${cmd.service}.${cmd.subtype}`;
  if (cmd.func_id != null) head += ` func ${cmd.func_id} (0x${Number(cmd.func_id).toString(16).toUpperCase().padStart(2, "0")})`;
  const bits = [head];
  const exp = cmd.expected_data_bytes != null ? cmd.expected_data_bytes : cmd.expected_bytes;
  if (exp != null) bits.push(`${exp} data bytes`);
  else if (cmd.variable_length) bits.push(`variable length${cmd.minimum != null ? ` (≥${cmd.minimum} B)` : ""}`);
  if (isCritical(cmd)) bits.push("CRITICAL — arm→execute");
  else if (cmd.criticality) bits.push(String(cmd.criticality).toUpperCase());
  if (cmd.readback) {
    const rb = cmd.readback;
    if (rb && typeof rb === "object")
      bits.push(`readback ${rb.description || ""}${rb.param_id != null ? ` (${fmtHex(rb.param_id, 4)})` : ""}`.trim());
    else bits.push(`readback ${rb}`);
  }
  if (cmd.description) bits.push(cmd.description);
  return bits.join(" · ");
}

function buildCmdPage() {
  return `
  <div class="pad hide" id="page-cmd" role="tabpanel" aria-label="Commanding">
    <h1 class="pt">Commanding</h1>
    <p class="sub">PUS telecommand builder · verification is a stepper attached to the
      command, not a separate log (P9)</p>
    <div class="guided-note" data-ann="cmd.builder"></div>
    <div class="g" style="grid-template-columns:minmax(0,1fr) 420px;align-items:start">
      <div class="g">
        <div class="ds-card"><h2>Command builder <span class="r" id="cmd-authority"></span></h2>
          <div class="bd">
            <div class="row">
              <div class="fld"><label for="cmd-catalog">Catalogue command</label>
                <select id="cmd-catalog" class="in" style="min-width:280px"></select></div>
              <div class="fld"><label for="cmd-service">Service</label>
                <select id="cmd-service" class="in">
                  ${[3, 5, 6, 8, 9, 11, 12, 15, 17, 19, 20].map((s2) => `<option>${s2}</option>`).join("")}
                </select></div>
              <div class="fld"><label for="cmd-subtype">Subtype</label>
                <input id="cmd-subtype" class="in" type="number" min="1" max="255" value="1" style="width:70px"></div>
              <div class="fld"><label for="cmd-name">Name</label>
                <input id="cmd-name" class="in" placeholder="optional"></div>
            </div>
            <div id="cmd-fields" class="g" style="grid-template-columns:repeat(auto-fill,minmax(200px,1fr));margin-top:8px"></div>
            <div id="cmd-raw-preview" class="mono" style="font-size:12px;color:var(--info);margin-top:6px"></div>
            <div id="cmd-field-errors" style="font-size:12px;color:var(--alarm)"></div>
            <div class="row" style="margin-top:8px">
              <div class="fld" style="flex:1"><label for="cmd-data">Data (hex) — live from the fields; paste hex to decode; Enter sends</label>
                <input id="cmd-data" class="in mono" placeholder="AA BB …"></div>
              <span id="cmd-bytes-chip" class="ds-badge"></span>
            </div>
            <div id="cmd-provenance" style="font-size:12px;color:var(--tx3);margin:6px 0"></div>
            <div class="row">
              <button class="ds-btn" id="cmd-pack">PACK FROM FIELDS</button>
              <button class="ds-btn" id="cmd-decode">DECODE HEX → FIELDS</button>
              <span style="flex:1"></span>
              <button class="ds-btn arm" id="cmd-arm">ARM</button>
              <button class="ds-btn primary" id="cmd-send" disabled>SEND</button>
            </div>
            <div id="cmd-verif-stepper" style="margin-top:10px"></div>
            <div id="cmd-result" style="font-size:12.5px;margin-top:6px"></div>
          </div></div>
        <div class="ds-card"><h2>Quick commands <span class="r">position-gated; denied entries say who owns them</span></h2>
          <div class="bd" id="cmd-quick"></div></div>
        <div class="ds-card"><h2>Verification log <span class="r">/api/verification-log · 5 s</span></h2>
          <div class="bd scroll-y" style="max-height:200px" id="cmd-verif-log"></div></div>
      </div>
      <div class="g" style="align-content:start">
        <div class="ds-card"><h2>Stored TM (S15) dump</h2><div class="bd">
          <div class="row">
            <div class="fld"><label for="s15-target">Subsystem</label>
              <select id="s15-target" class="in">
                ${["eps", "tcs", "aocs", "ttc", "payload", "prop", "obdh", "orbit", "all"]
                  .map((s2) => `<option>${s2}</option>`).join("")}</select></div>
            <div class="fld"><label for="s15-store">Store id</label>
              <select id="s15-store" class="in">
                <option value="">auto</option><option>1</option><option>2</option><option>3</option><option>4</option>
              </select></div>
            <button class="ds-btn" id="s15-dump">DUMP</button>
          </div>
          <div id="s15-result" style="font-size:12px;color:var(--tx3);margin-top:6px"></div>
        </div></div>
        <div class="ds-card"><h2>HK SID control (S3)</h2><div class="bd">
          <div class="row">
            <div class="fld"><label for="hk-sid">SID</label>
              <input id="hk-sid" class="in" type="number" min="1" value="1" style="width:70px"></div>
            <button class="ds-btn" data-hk="enable">ENABLE (3,5)</button>
            <button class="ds-btn" data-hk="disable">DISABLE (3,6)</button>
            <button class="ds-btn" data-hk="oneshot">ONE-SHOT (3,27)</button>
          </div>
          <div id="hk-quick-result" style="font-size:12px;color:var(--tx3);margin-top:6px"></div>
        </div></div>
        <div class="ds-card"><h2>Command history</h2>
          <div class="bd scroll-y" style="max-height:280px" id="cmd-history"></div></div>
      </div>
    </div>
  </div>`;
}

function currentCatalogCmd() {
  const sel = $("cmd-catalog");
  if (!sel || !sel.value) return null;
  return S.tcCatalog.find((c) => c.name === sel.value) || null;
}

function renderCmdCatalogSelect() {
  const sel = $("cmd-catalog");
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = `<option value="">— raw service/subtype —</option>` +
    S.tcCatalog.map((c) => `<option value="${esc(c.name)}"${c.allowed ? "" : " data-denied=\"1\""}>` +
      `${esc(c.name)} (S${c.service},${c.subtype})${c.allowed ? "" : " 🔒"}</option>`).join("");
  if (cur) sel.value = cur;
}

/* "NOT PERMITTED — belongs to <position>" (plan §2.1): authority is shown,
 * never hidden. Which position owns a denied command is derived from
 * positions.yaml allowed_commands lists. */
export function commandAuthority(cmdName) {
  const cmd = S.tcCatalog.find((c) => c.name === cmdName);
  if (!cmd) return { allowed: false, text: "unknown command" };
  if (cmd.allowed) return { allowed: true, text: "" };
  const owners = [];
  for (const [pos, p] of Object.entries(S.positions)) {
    const ac = p.allowed_commands;
    if (ac === "all" || (Array.isArray(ac) && ac.includes(cmdName))) owners.push(p.label || pos);
  }
  const owner = owners.filter((o) => o !== (S.positions[S.position] || {}).label);
  return { allowed: false,
    text: `NOT PERMITTED — belongs to ${owner.length ? owner.join(", ") : "another position"}` };
}

/* Names that read far better as hex on an ops console (classic parity). */
const HEX_FIELD_NAMES = new Set([
  "param_id", "mon_id", "sid", "address", "trigger_id", "device_id", "event_id",
]);

/* One typed widget per catalogue field — the widget kind is schema-driven
 * (classic Phase E WP-4 parity): enum -> dropdown with the value NAMES,
 * bitfield -> one checkbox per bit, linear-calibrated / ranged numbers ->
 * engineering-unit number inputs with min/max/step, bytes -> hex tail. */
export function cmdFieldWidget(f) {
  const cal = catCal(f);
  const ct = String(cal.type || "").toLowerCase();
  const unit = f.unit || f.units || "";
  const label = `${esc(f.name)}${unit ? ` (${esc(unit)})` : ""}`;
  const title = esc(f.description || f.type || "");
  const dflt = catFieldDefault(f);
  if ((f.enum || []).length) {
    const opts = f.enum.map((e) =>
      `<option value="${esc(String(e.value))}"${String(e.value) === String(dflt) ? " selected" : ""}>` +
      `${e.value} — ${esc(e.label)}${e.description ? ` (${esc(e.description)})` : ""}</option>`).join("");
    return `<div class="fld"><label>${label}</label>
      <select class="in" data-cmd-field="${esc(f.name)}" title="${title}">${opts}</select></div>`;
  }
  if (ct === "bitfield" && (f.bits || []).length) {
    const mask = Number(dflt) || 0;
    const rows = f.bits.map((b) => b.reserved
      ? `<div style="color:var(--tx3)">bit${b.bit} reserved (0)</div>`
      : `<label style="display:block"><input type="checkbox" data-cmd-bit="${esc(f.name)}"
           data-bit="${b.bit}"${(mask >> b.bit) & 1 ? " checked" : ""}>
           bit${b.bit} ${esc(b.name)}${b.description ? " — " + esc(b.description) : ""}</label>`).join("");
    return `<div class="fld" data-cmd-bitfield="${esc(f.name)}"><label>${label} — flags</label>
      <div style="font-size:12px">${rows}
        <span class="mono" data-cmd-mask="${esc(f.name)}" style="color:var(--tx3)"></span></div></div>`;
  }
  const t = String(f.type || "uint8").toLowerCase();
  if (catFieldWidth(t) == null) {
    return `<div class="fld"><label>${label} — hex bytes</label>
      <input class="in mono" data-cmd-field="${esc(f.name)}" placeholder="00 FF …"
        value="${f.default != null ? esc(String(f.default)) : ""}" title="${title}"></div>`;
  }
  const rng = f.range || {};
  const hasRange = rng.min !== undefined || rng.max !== undefined;
  const hint = hasRange
    ? ` <span style="color:var(--tx3);font-weight:400">[${rng.min} … ${rng.max}]${unit ? " " + esc(unit) : ""}</span>` : "";
  if (HEX_FIELD_NAMES.has(f.name)) {
    return `<div class="fld"><label>${label}${hint}</label>
      <input class="in mono" data-cmd-field="${esc(f.name)}" value="${esc(String(dflt))}"
        placeholder="0xHHHH" title="${title}"></div>`;
  }
  const step = t.indexOf("float") === 0 ? "0.01"
    : ct === "linear" ? String(Math.abs(cal.scale != null ? cal.scale : 1)) : "1";
  return `<div class="fld"><label>${label}${hint}</label>
    <input class="in" type="number" step="${step}" data-cmd-field="${esc(f.name)}"
      value="${esc(catFormatEng(f, dflt))}"
      ${rng.min !== undefined ? `min="${rng.min}"` : ""} ${rng.max !== undefined ? `max="${rng.max}"` : ""}
      title="${title}"></div>`;
}

function updateCmdBuilder() {
  const cmd = currentCatalogCmd();
  const authEl = $("cmd-authority");
  const sendBtn = $("cmd-send");
  const armBtn = $("cmd-arm");
  if (cmd) {
    $("cmd-service").value = String(cmd.service);
    $("cmd-subtype").value = String(cmd.subtype);
    $("cmd-name").value = cmd.name;
    setText("cmd-provenance", provenanceText(cmd));
    const auth = commandAuthority(cmd.name);
    if (authEl) { authEl.textContent = auth.text; authEl.style.color = auth.allowed ? "" : "var(--alarm)"; }
    // structured field editor (engineering units, enum names, validation)
    setHTML("cmd-fields", (cmd.fields || []).map(cmdFieldWidget).join(""));
    const critical = isCritical(cmd);
    if (armBtn) armBtn.classList.toggle("hide", !critical);
    if (sendBtn) sendBtn.disabled = !auth.allowed || (critical && !S.armed[cmd.name]);
    cmdLivePack();     // hex + length visible immediately, from the defaults
  } else {
    if (authEl) authEl.textContent = "";
    setHTML("cmd-fields", "");
    setText("cmd-provenance", "raw command — no catalogue entry; length not validated");
    setText("cmd-raw-preview", "");
    setText("cmd-field-errors", "");
    if (armBtn) armBtn.classList.add("hide");
    if (sendBtn) sendBtn.disabled = false;
    updateBytesChip();
  }
}
function collectFieldArgs() {
  const args = {};
  const sec = $("page-cmd");
  if (!sec || !sec.querySelectorAll) return args;
  sec.querySelectorAll("#cmd-fields [data-cmd-field]").forEach((el) => {
    args[el.dataset.cmdField] = el.value;
  });
  // bitfields: assemble the mask from the checkboxes
  const masks = {};
  sec.querySelectorAll("#cmd-fields [data-cmd-bit]").forEach((el) => {
    const nm = el.dataset.cmdBit;
    if (masks[nm] === undefined) masks[nm] = 0;
    if (el.checked) masks[nm] |= (1 << Number(el.dataset.bit));
  });
  for (const [nm, mask] of Object.entries(masks)) {
    args[nm] = mask;
    sec.querySelectorAll(`[data-cmd-mask="${nm}"]`).forEach((el) => {
      el.textContent = `mask = 0x${mask.toString(16).toUpperCase().padStart(2, "0")}`;
    });
  }
  return args;
}

/* Live pack (classic refreshSchemaPreview parity): every keystroke in the
 * typed fields re-encodes engineering -> raw hex, mirrors it into the data
 * box, shows RAW ▸ preview + byte count and flags invalid fields WITH the
 * rejection reason. Non-blocking: fault-injection training may still send. */
let cmdPackGuard = false;
function cmdLivePack() {
  const cmd = currentCatalogCmd();
  if (!cmd) return;
  const args = collectFieldArgs();
  const errs = [];
  const sec = $("page-cmd");
  if (sec && sec.querySelectorAll) {
    sec.querySelectorAll("#cmd-fields [data-cmd-field]").forEach((el) => {
      const f = (cmd.fields || []).find((x) => x.name === el.dataset.cmdField);
      if (!f) return;
      const msg = catFieldError(f, args[f.name]);
      if (el.style) el.style.borderColor = msg ? "var(--alarm)" : "";
      if (msg) errs.push(msg);
    });
  }
  const hex = encodeCatalogCommand(cmd, args);
  cmdPackGuard = true;
  const dataEl = $("cmd-data");
  if (dataEl) dataEl.value = hex;
  cmdPackGuard = false;
  setText("cmd-raw-preview", hex ? `RAW ▸ ${hex}` : "");
  setText("cmd-field-errors", errs.join("  ·  "));
  updateBytesChip();
}

/* Reverse decode (classic reverseDecodeIntoFields parity): hex typed or
 * pasted into the data box is decoded against the schema back into the
 * typed fields in ENGINEERING units. For S8.1, octet 0 identifies the
 * command; the builder auto-switches to it. */
function cmdReverseDecode() {
  if (cmdPackGuard) return;
  updateBytesChip();
  const raw = String($("cmd-data") ? $("cmd-data").value : "").replace(/[\s,]+/g, "");
  if (!raw || raw.length % 2) return;
  const svc = Number($("cmd-service") ? $("cmd-service").value : 0);
  const sub = Number($("cmd-subtype") ? $("cmd-subtype").value : 0);
  let cmd = currentCatalogCmd();
  let body = raw;
  if (svc === 8 && sub === 1) {
    const fid = parseInt(raw.slice(0, 2), 16);
    if (!Number.isFinite(fid)) return;
    const match = S.tcCatalog.find((c) => Number(c.service) === 8 &&
      Number(c.subtype) === 1 && Number(c.func_id) === fid);
    if (!match) {
      setText("cmd-raw-preview", `✗ func 0x${fid.toString(16).toUpperCase().padStart(2, "0")} is not in the catalogue`);
      return;
    }
    if (!cmd || cmd.name !== match.name) {
      const sel = $("cmd-catalog");
      if (sel) sel.value = match.name;
      const keep = $("cmd-data") ? $("cmd-data").value : "";
      updateCmdBuilder();                 // re-render typed fields for it
      const dataEl = $("cmd-data");
      if (dataEl) dataEl.value = keep;    // defaults overwrote the paste
    }
    cmd = match;
    body = raw.slice(2);
  }
  if (!cmd || !Array.isArray(cmd.fields)) return;
  if (!cmd.fields.length) {
    setText("cmd-raw-preview", body.length ? `✗ ${cmd.name} takes no arguments` : `DECODED ✓ ${cmd.name}`);
    return;
  }
  const r = decodeCatalogArgs(cmd, body);
  if (!r.ok) { setText("cmd-raw-preview", `✗ decode: ${r.error}`); return; }
  applyDecodedValues(cmd, r.values);
  setText("cmd-raw-preview", `DECODED ✓ ${cmd.name}`);
  setText("cmd-field-errors", "");
  updateBytesChip();
}
function applyDecodedValues(cmd, values) {
  const sec = $("page-cmd");
  if (!sec || !sec.querySelectorAll) return;
  sec.querySelectorAll("#cmd-fields [data-cmd-field]").forEach((el) => {
    const f = (cmd.fields || []).find((x) => x.name === el.dataset.cmdField);
    if (!f || values[f.name] === undefined) return;
    if (HEX_FIELD_NAMES.has(f.name))
      el.value = "0x" + Math.round(Number(values[f.name])).toString(16).toUpperCase();
    else if ((f.enum || []).length) el.value = String(Math.round(Number(values[f.name])));
    else if (catFieldWidth(String(f.type || "uint8").toLowerCase()) == null)
      el.value = String(values[f.name]);
    else el.value = catFormatEng(f, values[f.name]);
  });
  // bitfields back into checkboxes
  sec.querySelectorAll("#cmd-fields [data-cmd-bit]").forEach((el) => {
    const nm = el.dataset.cmdBit;
    if (values[nm] === undefined) return;
    el.checked = !!((Number(values[nm]) >> Number(el.dataset.bit)) & 1);
  });
}
function updateBytesChip() {
  const cmd = currentCatalogCmd();
  const chip = $("cmd-bytes-chip");
  if (!chip) return;
  const entered = String($("cmd-data") ? $("cmd-data").value : "").replace(/[\s,]+/g, "").length / 2;
  const exp = cmd ? (cmd.expected_data_bytes != null ? cmd.expected_data_bytes : cmd.expected_bytes) : null;
  if (!cmd || exp == null) {
    if (cmd && cmd.variable_length) {
      chip.textContent = `${entered} B (variable${cmd.minimum != null ? `, ≥${cmd.minimum}` : ""})`;
      chip.className = "ds-badge" + (cmd.minimum != null && entered < cmd.minimum ? " warn" : "");
    } else { chip.textContent = `${entered} B`; chip.className = "ds-badge"; }
    return;
  }
  chip.textContent = `${entered} / ${exp} B`;
  chip.className = "ds-badge" + (entered === exp ? "" : " warn");
}

async function sendCommand() {
  const cmd = currentCatalogCmd();
  const service = Number($("cmd-service").value);
  const subtype = Number($("cmd-subtype").value);
  const data = $("cmd-data").value.trim();
  const name = $("cmd-name").value.trim() || (cmd ? cmd.name : "");
  if (cmd) {
    const auth = commandAuthority(cmd.name);
    if (!auth.allowed) { setHTML("cmd-result", `<span style="color:var(--alarm)">${esc(auth.text)}</span>`); return; }
    if (isCritical(cmd) && !S.armed[cmd.name]) {
      setHTML("cmd-result", `<span style="color:var(--warn)">critical command — ARM first</span>`);
      return;
    }
  }
  setHTML("cmd-verif-stepper", renderStepper(["BUILD", "SENT", "ACCEPT (S1.1)", "EXEC (S1.7)"], 1));
  const r = await sendPus({
    service, subtype, data_hex: data, name, position: S.position });
  const ok = r.ok && (!r.json || r.json.status !== "rejected");
  // (history is recorded by sendPus() for every send, builder included)
  setHTML("cmd-result", ok
    ? `sent TC(${service},${subtype})${name ? " — " + esc(name) : ""}`
    : `<span style="color:var(--alarm)">rejected — ${esc((r.json && (r.json.reason || r.json.error)) || `HTTP ${r.status}`)}</span>`);
  if (cmd && isCritical(cmd)) { S.armed[cmd.name] = false; updateCmdBuilder(); const a = $("cmd-arm"); if (a) a.classList.remove("armed"); }
  renderBottomDrawer();
}

function noteVerification(v) {
  const okSub = v.subtype === 1 || v.subtype === 7;
  // G12: any TM readback (an S1 verification report IS telemetry from the
  // spacecraft) confirms the oldest still-pending blind command.
  if (okSub) {
    const pending = S.cmdHistory.filter((h) => h.blind === "pending");
    if (pending.length) {
      pending[pending.length - 1].blind = "confirmed";
      renderCmdHistory();
    }
  }
  S.verifLog.unshift({ ts: tsShort(),
    verdict: `S1.${v.subtype} ${okSub ? "OK" : "FAIL"}`,
    text: `TC(${v.tc_service},${v.tc_subtype})` +
          (v.code !== undefined ? ` code ${hex4(v.code)}` : "") });
  if (S.verifLog.length > 80) S.verifLog.length = 80;
  const stepIdx = v.subtype === 1 ? 2 : v.subtype === 7 ? 3 : 2;
  const fail = !okSub;
  setHTML("cmd-verif-stepper", renderStepper(
    ["BUILD", "SENT", fail && v.subtype === 2 ? { label: "ACCEPT (S1.1)", fail: true } : "ACCEPT (S1.1)",
     fail && v.subtype === 8 ? { label: "EXEC (S1.7)", fail: true } : "EXEC (S1.7)"], stepIdx));
  renderVerifLog();
}
function renderVerifLog() {
  setHTML("cmd-verif-log", S.verifLog.map((v) =>
    `<div class="logrow ${v.verdict.includes("FAIL") ? "alarm" : ""}">` +
    `${esc(v.ts)} ${esc(v.verdict)} ${esc(v.text)}</div>`).join("") ||
    `<div class="logrow nom">no verification reports yet</div>`);
}
// EVERY telecommand this console sends goes out through here.
//
// Field report 2026-08-20: the PUS service panels commanded the spacecraft
// but left no trace in the CMD HISTORY window. They posted to
// /api/pus-command directly, and only two of the twenty call sites in this
// module also called noteHistory() -- the command builder and the quick
// commands. So an S3 ENABLE ALL, an S9 SET SC TIME or an S12 monitor change
// went to the spacecraft and showed up nowhere on the ground: both a
// usability failure and a hole in the decision record.
//
// The recording now happens at the transport instead of at each call site,
// so a new panel cannot forget to do it.
async function sendPus(body) {
  const b = Object.assign({ position: S.position }, body);
  const r = await postJSON("/api/pus-command", b);
  const ok = r.ok && (!r.json || r.json.status !== "rejected");
  const reason = (r.json && (r.json.reason || r.json.error)) || `HTTP ${r.status}`;
  noteHistory({ ts: tsShort(), name: b.name || `S${b.service},${b.subtype}`,
    service: b.service, subtype: b.subtype, data: b.data_hex || "",
    verdict: ok ? "sent" : `rejected: ${reason}` });
  return r;
}
function noteHistory(h) {
  // G12: while blind-TC mode is active, every send is tagged BLIND and stays
  // "pending" (visually distinct) until a TM readback confirms it.
  if (S.blindTc.enabled && !h.blind) h.blind = "pending";
  S.cmdHistory.unshift(h);
  if (S.cmdHistory.length > RING) S.cmdHistory.length = RING;
  renderCmdHistory();
}
function cmdHistoryRow(c) {
  return `<div class="logrow">${esc(c.ts)} ${esc(c.name)} ` +
    `<span class="mono">${esc(c.data || "")}</span> — ${esc(c.verdict)}` +
    (c.blind ? " " + blindHistoryChip(c) : "") + `</div>`;
}
function renderCmdHistory() {
  setHTML("cmd-history", S.cmdHistory.slice(0, 60).map(cmdHistoryRow).join(""));
}

/* Quick commands: grouped grid, critical styling, position filtering; shown
 * on CMD and mirrored per-subsystem in place on every L2 page. */
function renderQuickCommands() {
  const groups = {};
  for (const q of S.quickCommands) {
    const g = q.group || q.subsystem || "GENERAL";
    (groups[g] = groups[g] || []).push(q);
  }
  // Stable identity per entry, independent of whether config gave it a
  // `name:`. Stamped here so the CMD grid and the per-subsystem mirrors agree.
  S.quickCommands.forEach((q, i) => { q._qi = i; });
  const btn = (q) => {
    const cmd = S.tcCatalog.find((c) => c.name === q.command);
    const auth = commandAuthority(q.command);
    const critical = isCritical(cmd);
    if (!auth.allowed) {
      return `<span class="ds-chip off" title="${esc(auth.text)}">${esc(q.label || q.command)} — ${esc(auth.text)}</span>`;
    }
    // data-quick carries the quick ENTRY name (unique), not the catalogue
    // command name: several quick entries share a command with different
    // preset args (AOCS_SET_MODE mode=2/3/4/5) and the args must survive.
    // FIELD DEFECT 2026-08-20: this carried `q.name || q.command`, and
    // sendQuick resolved it with .find() -- which returns the FIRST match.
    // Shipped entries without a `name:` collided on their bare command name,
    // so BOTH buttons of a pair fired the first entry's args: "DL Mode OFF"
    // sent TTC_SET_DOWNLINK_MODE(on=1) and "GPS Rx Off" sent
    // S2_AOCS_GPS_RECEIVER(state=1). Neither could be turned off from the
    // console, silently, while the button reported success. An index cannot
    // collide -- fixed by construction, not by config hygiene.
    return `<button class="ds-btn ${critical ? "danger" : ""}" data-quick="${q._qi}"
      title="${esc(provenanceText(cmd))}">${esc(q.label || q.command)}${critical ? " ⚠" : ""}</button>`;
  };
  setHTML("cmd-quick", Object.entries(groups).map(([g, qs]) =>
    `<div style="margin-bottom:8px"><div style="font-size:12px;letter-spacing:1px;color:var(--tx3);
      text-transform:uppercase;margin-bottom:4px">${esc(g)}</div>
      <div class="row">${qs.map(btn).join("")}</div></div>`).join("") ||
    `<div class="logrow nom">no quick commands for this position</div>`);
  // per-subsystem in-place mirrors
  for (const pageId of SUB_PAGES) {
    const sub = PAGE_SUBSYSTEM[pageId];
    const mine = S.quickCommands.filter((q) => {
      const cmd = S.tcCatalog.find((c) => c.name === q.command) || {};
      const g = String(q.group || q.subsystem || cmd.subsystem || "").toLowerCase();
      return g.includes(sub) || (sub === "payload" && g.includes("pld")) ||
             (sub === "prop" && (g.includes("prop") || g.includes("prp")));
    });
    setHTML(`${pageId}-quick-list`, mine.length
      ? `<div class="row">${mine.map(btn).join("")}</div>`
      : `<div class="logrow nom">no quick commands for ${esc(sub)} — full builder on CMD</div>`);
  }
}

async function sendQuick(key) {
  // `key` is the quick ENTRY name; fall back to a bare catalogue name so
  // older callers keep working.
  // `key` is the entry's index (see renderQuickCommands). Numeric first; the
  // name lookup survives only for older callers, and is NOT what buttons use.
  const _i = Number(key);
  const q = (Number.isInteger(_i) && S.quickCommands[_i])
    || S.quickCommands.find((x) => (x.name || x.command) === key)
    || { command: key };
  const cmd = S.tcCatalog.find((c) => c.name === q.command);
  if (!cmd) return;
  const auth = commandAuthority(q.command);
  if (!auth.allowed) { announce(auth.text); return; }
  if (isCritical(cmd)) {
    // arm→execute in place: first press arms, second within 8 s executes
    if (!S.armed[key]) {
      S.armed[key] = true;
      announce(`${q.label || cmd.name} ARMED — press again to execute`);
      setTimeout(() => { S.armed[key] = false; }, 8000);
      return;
    }
    S.armed[key] = false;
  }
  // The quick entry's preset args ride through the SAME encoder as the
  // builder (AOCS_DETUMBLE is AOCS_SET_MODE with mode=2, not the default).
  const data = encodeCatalogCommand(cmd, q.args || {});
  const r = await sendPus({
    service: cmd.service, subtype: cmd.subtype, data_hex: data, name: cmd.name, position: S.position });
  announce(`${cmd.name} ${r.ok ? "sent" : "rejected"}`);
}

/* ═══ 12. DIAG (L4) — PUS service panels + PARAMS ═════════════════════════ */

const DIAG_TABS = [["s3", "S3 HK"], ["s11", "S11 SCHED"], ["s6", "S6 MEMORY"],
  ["s15", "S15 STORES"], ["s12", "S12 MONITOR"], ["s19", "S19 EVT-ACT"],
  ["s9", "S9 TIME"], ["params", "PARAMS"]];

function buildDiagPage() {
  return `
  <div class="pad hide" id="page-diag" role="tabpanel" aria-label="Diagnostics">
    <div class="row" style="justify-content:space-between">
      <div><h1 class="pt">PUS services</h1>
        <p class="sub">All seven service panels from the classic PUS SERVICES tab —
          S3 housekeeping · S11 scheduler · S6 memory · S15 stores · S12 monitoring ·
          S19 event-action · S9 time — plus the parameter catalog. S12/S19 tables are
          the same renderer FDIR mounts (one implementation, two mounts).</p></div>
      <div class="fld"><label for="hdr-param-search">Parameter search (global)</label>
        <input id="hdr-param-search" class="in" placeholder="name or 0xHHHH" style="min-width:240px"></div>
    </div>
    <div class="guided-note" data-ann="diag.pus"></div>
    <div class="row" role="tablist" id="diag-tabs">
      ${DIAG_TABS.map(([id, l], i) =>
        `<button class="ds-btn" role="tab" data-diag="${id}" aria-selected="${i === 0}">${l}</button>`).join("")}
    </div>
    <div style="margin-top:10px">
      <div id="diag-s3" class="diag-pane"></div>
      <div id="diag-s11" class="diag-pane hide"></div>
      <div id="diag-s6" class="diag-pane hide"></div>
      <div id="diag-s15" class="diag-pane hide"></div>
      <div id="diag-s12" class="diag-pane hide"></div>
      <div id="diag-s19" class="diag-pane hide"></div>
      <div id="diag-s9" class="diag-pane hide"></div>
      <div id="diag-params" class="diag-pane hide"></div>
    </div>
  </div>`;
}

let diagTab = "s3";
function selectDiagTab(id) {
  diagTab = id;
  for (const [tid] of DIAG_TABS) {
    show(`diag-${tid}`, tid === id);
    const b = document.querySelector ? document.querySelector(`[data-diag="${tid}"]`) : null;
    if (b) b.setAttribute("aria-selected", tid === id ? "true" : "false");
  }
  renderDiag();
}

/* ── Live re-render of panels that used to update only on a click ─────────
 *
 * Field report 2026-08-20: "the PUS windows and the bottom windows do not
 * refresh properly - they need a user click to refresh".
 *
 * True, and by construction: renderCurrentPage(full) rebuilt the DIAG page
 * only when `full` was set, and `full` is set on navigation, not on the state
 * cadence. So HK ages, the S15 store fill, the S11 schedule, S12 monitor
 * values and every PARAMS value sat frozen at whatever they were when the tab
 * was opened. A console that shows a stale number as though it were live is
 * worse than one that shows nothing.
 *
 * These panels were left click-only for a real reason: they carry form inputs
 * and scrollable tables, and a naive rebuild throws away what the operator is
 * typing and where they have scrolled. So the rebuild is wrapped -- focus,
 * caret and scroll offsets are captured and restored around it, and a refresh
 * is SKIPPED outright while a dropdown inside the panel has focus, because
 * re-rendering under an open <select> yanks the rug mid-selection.
 */
function preservingUi(hostId, fn) {
  const host = $(hostId);
  if (!host) { fn(); return true; }
  const act = document.activeElement;
  if (act && host.contains(act) && (act.tagName === "SELECT" || act.tagName === "OPTION")) {
    return false;                       // operator is mid-interaction: leave it alone
  }
  const actId = act && host.contains(act) ? act.id : null;
  let selS = null, selE = null;
  if (actId) { try { selS = act.selectionStart; selE = act.selectionEnd; } catch (e) { /* not a text field */ } }
  // Scroll offsets are captured POSITIONALLY, not by id: the scrolling element
  // in these panels is usually an anonymous <div class="scroll-y"> with no id,
  // so an id-keyed capture silently restored nothing. A re-render of the same
  // panel reproduces the same element order, so index alignment holds; if the
  // shape changed (different tab, different row count) the lengths differ and
  // restoration is skipped rather than applied to the wrong element.
  const hostTop = host.scrollTop;
  const before = Array.prototype.map.call(host.querySelectorAll("*"), (el) => el.scrollTop);
  fn();
  host.scrollTop = hostTop;
  const after = host.querySelectorAll("*");
  if (after.length === before.length) {
    for (let i = 0; i < after.length; i++) {
      if (before[i]) after[i].scrollTop = before[i];
    }
  }
  if (actId) {
    const el2 = $(actId);
    if (el2) {
      el2.focus();
      if (selS !== null && el2.setSelectionRange) {
        try { el2.setSelectionRange(selS, selE); } catch (e) { /* not selectable */ }
      }
    }
  }
  return true;
}

// The PARAMS table can be ~400 rows, so the re-render is throttled. This is a
// PAINT budget, not a telemetry age: performance.now() is the right clock (and
// keeps simNow() the single wall-clock reader, disposition M3). Telemetry
// ages are still judged in sim seconds, by ageVerdict() above.
let _diagLastRender = 0;
function refreshDiagLive() {
  const now = (typeof performance !== "undefined" && performance.now)
    ? performance.now() : 0;
  if (now - _diagLastRender < 900) return;
  _diagLastRender = now;
  preservingUi(`diag-${diagTab}`, renderDiag);
}

function renderDiag() {
  if (diagTab === "s3") renderS3Panel();
  else if (diagTab === "s11") renderS11Panel();
  else if (diagTab === "s6") renderS6Panel();
  else if (diagTab === "s15") renderS15Panel();
  else if (diagTab === "s12") setHTML("diag-s12", renderS12Block("diag"));
  else if (diagTab === "s19") setHTML("diag-s19", renderS19Block("diag"));
  else if (diagTab === "s9") renderS9Panel();
  else if (diagTab === "params") renderParamsPanel();
}

/* — S3 HK manager — */
async function loadHkStructures() {
  try { S.hkStructures = await getJSON("/api/hk-structures"); }
  catch (e) { S.hkStructures = null; }
}
function hkObserved(sid) {
  const meta = ((S.hkStructures || {}).structures || []).find((s2) => Number(s2.sid) === Number(sid)) || {};
  const ages = S.state.hk_sid_age_s || {};
  // live state age first, then the /api/hk-structures observed snapshot
  const age = ages[String(sid)] !== undefined ? ages[String(sid)]
    : (meta.last_seen_age_s === null ? undefined : meta.last_seen_age_s);
  const interval = meta.interval_s || 60;
  if (age === undefined || age === null) return { label: "NEVER SEEN", cls: "stale" };
  if (age <= interval * 2.5 + 1) return { label: `PERIODIC · ${fmtAge(age)}`, cls: "ok" };
  return { label: `SILENT · ${fmtAge(age)}`, cls: "warn" };
}
function renderS3Panel() {
  const structures = ((S.hkStructures || {}).structures) || [];
  const rows = structures.map((s2) => {
    const obs = hkObserved(s2.sid);
    // /api/hk-structures serves `param_count`; older fixtures a list
    const nParams = s2.param_count ?? (s2.parameters || []).length;
    return `<tr><td class="mono">${s2.sid}</td><td>${esc(s2.name || "")}</td>
      <td class="mono">${s2.apid ?? "—"}</td>
      <td class="mono">${s2.interval_s ?? "—"} s</td>
      <td class="mono">${nParams}</td>
      <td>${renderChip({ kind: obs.cls === "ok" ? "ok" : obs.cls === "warn" ? "warn" : "stale", label: obs.label })}</td>
      <td><button class="ds-btn" data-s3="enable" data-sid="${s2.sid}">EN</button>
          <button class="ds-btn" data-s3="disable" data-sid="${s2.sid}">DIS</button>
          <button class="ds-btn" data-s3="oneshot" data-sid="${s2.sid}">REQ</button></td></tr>`;
  }).join("");
  setHTML("diag-s3", `
    <div class="ds-card"><h2>Housekeeping manager (S3)</h2><div class="bd">
      <div class="row" style="margin-bottom:8px">
        <button class="ds-btn" id="s3-req-all">REQUEST ALL (ONE-SHOT)</button>
        <button class="ds-btn" id="s3-en-all">ENABLE ALL</button>
        <button class="ds-btn" id="s3-dis-all">DISABLE ALL</button>
        <button class="ds-btn" id="s3-reload">RELOAD DEFINITIONS</button>
      </div>
      <table class="plain"><thead><tr><th>SID</th><th>Name</th><th>APID</th><th>Interval</th>
        <th>Params</th><th>Observed</th><th></th></tr></thead>
        <tbody>${rows || `<tr><td colspan="7">no HK structures</td></tr>`}</tbody></table>
    </div></div>`);
}
async function s3Command(action, sid) {
  const map = { enable: [3, 5], disable: [3, 6], oneshot: [3, 27] };
  const [svc, sub] = map[action];
  await sendPus({ service: svc, subtype: sub,
    data_hex: sidToHex(sid), name: `HK_${action.toUpperCase()}_SID${sid}`, position: S.position });
  announce(`S3 ${action} SID ${sid} sent`);
}

/* — S11 scheduler — */
function renderS11Panel() {
  setHTML("diag-s11", `
    <div class="ds-card"><h2>On-board schedule (S11)</h2><div class="bd">
      <div class="row" style="margin-bottom:8px">
        <button class="ds-btn" data-s11="refresh">REFRESH LIST (11,17)</button>
        <button class="ds-btn" data-s11="enable">ENABLE (11,13)</button>
        <button class="ds-btn" data-s11="disable">DISABLE (11,9)</button>
        <button class="ds-btn danger" data-s11="clear">CLEAR ALL</button>
      </div>
      <div class="row">
        <div class="fld"><label for="s11-mode">Time-tag mode</label>
          <select id="s11-mode" class="in">
            <option value="abs">Absolute UTC</option>
            <option value="rel">Relative now+N min</option>
            <option value="cuc">Raw CUC seconds</option></select></div>
        <div class="fld"><label for="s11-time">Time value</label>
          <input id="s11-time" class="in mono" placeholder="YYYY-MM-DDTHH:MM:SS / min / s"></div>
        <button class="ds-btn" id="s11-now">NOW</button>
        <div class="fld"><label for="s11-cmd">Catalogue command</label>
          <select id="s11-cmd" class="in" style="min-width:220px">
            <option value="">— raw svc,sub —</option>
            ${S.tcCatalog.map((c) => `<option value="${esc(c.name)}">${esc(c.name)} (S${c.service},${c.subtype})</option>`).join("")}
          </select></div>
        <div class="fld"><label for="s11-svc">Svc,Sub</label>
          <input id="s11-svc" class="in mono" style="width:80px" placeholder="8,1"></div>
        <div class="fld"><label for="s11-data">Data hex</label>
          <input id="s11-data" class="in mono" placeholder="24 …"></div>
        <button class="ds-btn primary" id="s11-add">ADD (11,4)</button>
      </div>
      <div style="font-size:12px;color:var(--tx3);margin-top:4px">
        Pick a catalogue command to pre-fill Svc,Sub and its default-argument hex
        (build non-default arguments on the CMD page and paste the hex here).</div>
      <div id="s11-preview" class="mono" style="font-size:12px;color:var(--tx3);margin:6px 0"></div>
      <table class="plain" id="s11-table"></table>
    </div></div>`);
  renderS11Table();
  loadS11Schedule();      // last TM(11,18) report from /api/schedule
  s11UpdatePreview();
}
export function s11CucSeconds(mode, value) {
  // live CUC preview: mission epoch J2000 unless the server tells us otherwise
  const epochIso = (S.state.time_correlation || {}).epoch || "2000-01-01T12:00:00Z";
  const epochMs = Date.parse(epochIso);
  if (mode === "cuc") return Number(value) || 0;
  if (mode === "rel") return Math.round((simNowMs() + (Number(value) || 0) * 60000 - epochMs) / 1000);
  const t = Date.parse(String(value).endsWith("Z") ? value : value + "Z");
  if (!Number.isFinite(t)) return null;
  return Math.round((t - epochMs) / 1000);
}
// Write a result or a refusal where the operator is actually looking.
//
// Field report 2026-08-20: ADD (11,4) "did nothing". It was in fact refusing —
// with an empty Svc,Sub you cannot build a valid time-tagged command — but it
// said so only through announce(), which writes to the screen-reader-only live
// region. A refusal nobody can see is indistinguishable from a dead button, so
// the reason now lands in the panel's own preview line, in the alarm colour.
function panelResult(id, msg, bad) {
  const el = $(id);
  if (el) {
    el.textContent = msg;
    el.style.color = bad ? "var(--alarm)" : "var(--tx3)";
  }
  announce(msg);
}
function s11UpdatePreview() {
  const mode = $("s11-mode") ? $("s11-mode").value : "abs";
  const v = $("s11-time") ? $("s11-time").value : "";
  const cuc = s11CucSeconds(mode, v);
  const el = $("s11-preview");
  if (el) el.style.color = "var(--tx3)";      // clear any previous refusal
  setText("s11-preview", cuc === null ? "CUC —" : `CUC ${cuc} (0x${(cuc >>> 0).toString(16).toUpperCase()})`);
}
async function loadS11Schedule() {
  try {
    const d = await getJSON("/api/schedule");
    if (d && !d.never_reported) S.s11Schedule = d;
  } catch (e) { /* offline */ }
  renderS11Table();
}
function s11EntryUtc(e) {
  // TM(11,18) rows carry exec_time_cuc (mission-epoch seconds)
  if (e.time_utc) return String(e.time_utc);
  const cuc = e.exec_time_cuc ?? e.cuc;
  if (cuc === undefined || cuc === null) return "";
  const epochIso = (S.state.time_correlation || {}).epoch || "2000-01-01T12:00:00Z";
  return fmtUTC(new Date(Date.parse(epochIso) + Number(cuc) * 1000), { date: true });
}
function renderS11Table() {
  const sch = S.s11Schedule;
  const rows = ((sch && sch.entries) || []).map((e) => {
    const cuc = e.exec_time_cuc ?? e.cuc;
    const cmd = S.tcCatalog.find((c) => Number(c.service) === Number(e.service) &&
      Number(c.subtype) === Number(e.subtype) &&
      (Number(c.service) !== 8 || Number(c.subtype) !== 1 ||
       (e.data_hex || "").slice(0, 2) === (c.func_id != null
         ? Number(c.func_id).toString(16).toUpperCase().padStart(2, "0") : "")));
    return `<tr>
    <td class="mono">${e.id ?? "—"}</td>
    <td class="mono">${esc(s11EntryUtc(e))}</td>
    <td class="mono">${cuc ?? "—"}</td>
    <td class="mono">S${e.service},${e.subtype}${cmd ? " " + esc(cmd.name) : ""}</td>
    <td class="mono">${esc(e.data || e.data_hex || "")}</td>
    <td><button class="ds-btn" data-s11-del="${e.id}">✕</button></td></tr>`;
  }).join("");
  setHTML("s11-table", `<thead><tr><th>ID</th><th>Exec (UTC)</th><th>CUC</th><th>TC</th><th>Data</th><th></th></tr></thead>
    <tbody>${rows || `<tr><td colspan="6">${sch ? "schedule empty" : "no schedule report — REFRESH (11,17)"}</td></tr>`}</tbody>` +
    (sch ? `<caption style="caption-side:bottom;text-align:left;font-size:12px;color:var(--tx3)">
      schedule ${sch.enabled ? "ENABLED" : "DISABLED"} · ${sch.count ?? ((sch.entries || []).length)} entries` +
      `${sch.next_id != null ? ` · next id ${sch.next_id}` : ""}</caption>` : ""));
}
async function s11Command(action) {
  const map = { refresh: [11, 17, ""], enable: [11, 13, ""], disable: [11, 9, ""], clear: [11, 3, ""] };
  const [svc, sub, data] = map[action];
  await sendPus({ service: svc, subtype: sub, data_hex: data,
    name: `S11_${action.toUpperCase()}`, position: S.position });
  announce(`S11 ${action} sent`);
  if (action === "refresh") setTimeout(loadS11Schedule, 800);
}
async function s11Add() {
  const mode = $("s11-mode").value;
  const cuc = s11CucSeconds(mode, $("s11-time").value);
  const svcSub = String($("s11-svc").value || "").split(",").map((x) => Number(x.trim()));
  const badTime = cuc === null;
  const badSvc = svcSub.length !== 2 || svcSub.some((x) => !Number.isFinite(x));
  if (badTime || badSvc) {
    panelResult("s11-preview", "ADD refused — " + (
      badTime && badSvc
        ? "no time value and no Svc,Sub. Pick a catalogue command (it fills both), or press NOW and type Svc,Sub."
        : badTime ? "the time value is empty or unparseable for this time-tag mode. Press NOW for a starting point."
        : "Svc,Sub is empty. Pick a catalogue command to fill it, or type it (e.g. 8,1)."), true);
    return;
  }
  const dataHex = String($("s11-data").value || "").replace(/[\s,]+/g, "");
  const cucBytes = [(cuc >>> 24) & 255, (cuc >>> 16) & 255, (cuc >>> 8) & 255, cuc & 255];
  const payload = cucBytes.concat([svcSub[0], svcSub[1]])
    .map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ") +
    (dataHex ? " " + dataHex.replace(/(..)/g, "$1 ").trim() : "");
  await sendPus({ service: 11, subtype: 4, data_hex: payload,
    name: "S11_ADD", position: S.position });
  panelResult("s11-preview",
    `ADD sent — CUC ${cuc} (0x${(cuc >>> 0).toString(16).toUpperCase()}), S${svcSub[0]},${svcSub[1]}` +
    (dataHex ? ` + ${dataHex.length / 2} arg byte(s)` : ""), false);
}
async function s11Delete(id) {
  const v = Number(id) >>> 0;
  const data = [(v >>> 8) & 255, v & 255].map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ");
  await sendPus({ service: 11, subtype: 5, data_hex: data,
    name: `S11_DELETE_${id}`, position: S.position });
}

/* — S6 memory — */
async function loadMemoryMap() {
  try { S.memoryMap = await getJSON("/api/memory-map"); }
  catch (e) { S.memoryMap = null; }
}
export function crc16Ccitt(hexStr) {
  const s2 = String(hexStr || "").replace(/[\s,]+/g, "");
  let crc = 0xFFFF;
  for (let i = 0; i < s2.length; i += 2) {
    const b = parseInt(s2.substr(i, 2), 16) || 0;
    crc ^= b << 8;
    for (let k = 0; k < 8; k++)
      crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xFFFF : (crc << 1) & 0xFFFF;
  }
  return crc;
}
function renderS6Panel() {
  const regions = ((S.memoryMap || {}).regions) || [];
  setHTML("diag-s6", `
    <div class="ds-card"><h2>Memory (S6)</h2><div class="bd">
      <div class="row">
        <div class="fld"><label for="s6-region">Region</label>
          <select id="s6-region" class="in">${regions.map((r) =>
            `<option value="${esc(String(r.start ?? r.address ?? 0))}">${esc(r.name || "region")}</option>`).join("")}
          </select></div>
        <div class="fld"><label for="s6-addr">Address (hex)</label>
          <input id="s6-addr" class="in mono" value="0" style="width:110px"></div>
        <div class="fld"><label for="s6-len">Length</label>
          <input id="s6-len" class="in" type="number" value="64" style="width:80px"></div>
        <div class="fld"><label for="s6-memid">Memory id</label>
          <input id="s6-memid" class="in" type="number" value="1" style="width:60px"></div>
        <button class="ds-btn" id="s6-dump">DUMP (6,5)</button>
        <button class="ds-btn" id="s6-crc">CRC CHECK (6,9)</button>
      </div>
      <div class="row" style="margin-top:8px">
        <div class="fld" style="flex:1"><label for="s6-patch">Patch data (hex)</label>
          <input id="s6-patch" class="in mono" placeholder="DE AD …"></div>
        <input type="file" id="s6-file" aria-label="Patch file">
        <button class="ds-btn" id="s6-load">1. LOAD (6,2)</button>
        <button class="ds-btn" id="s6-verify">2. VERIFY (6,9)</button>
        <button class="ds-btn" id="s6-readback">READ BACK (6,5)</button>
        <span id="s6-localcrc" class="mono" style="font-size:12px;color:var(--tx3)"></span>
      </div>
      <table class="plain" style="margin-top:8px"><thead><tr><th>Region</th><th>Start</th><th>Size</th><th>Writable</th></tr></thead>
        <tbody>${regions.map((r) => `<tr><td>${esc(r.name || "")}</td>
          <td class="mono">0x${Number(r.start ?? r.address ?? 0).toString(16).toUpperCase().padStart(8, "0")}</td>
          <td class="mono">${r.size ?? r.length ?? "—"}</td><td>${r.writable ? "yes" : "no"}</td></tr>`).join("") ||
          `<tr><td colspan="4">memory map unavailable</td></tr>`}</tbody></table>
      <div id="s6-dump-view" class="mono" style="font-size:12px;margin-top:8px;white-space:pre-wrap"></div>
      <div id="s6-check-view" class="mono" style="font-size:12px;margin-top:4px"></div>
    </div></div>`);
}
function s6AddrBytes() {
  const memId = Number($("s6-memid").value) & 0xFF;
  const addr = parseInt($("s6-addr").value, 16) >>> 0;
  const len = Number($("s6-len").value) >>> 0;
  const b = [memId,
    (addr >>> 24) & 255, (addr >>> 16) & 255, (addr >>> 8) & 255, addr & 255,
    (len >>> 24) & 255, (len >>> 16) & 255, (len >>> 8) & 255, len & 255];
  return b.map((x) => x.toString(16).toUpperCase().padStart(2, "0")).join(" ");
}
async function s6Command(subtype, extraHex) {
  const data = s6AddrBytes() + (extraHex ? " " + extraHex : "");
  await sendPus({ service: 6, subtype, data_hex: data,
    name: `S6_${subtype}`, position: S.position });
  announce(`S6.${subtype} sent`);
}
function renderMemoryDump(d) {
  const bytes = d.data || d.bytes || [];
  const hexRows = [];
  for (let i = 0; i < bytes.length; i += 16) {
    const chunk = bytes.slice(i, i + 16);
    hexRows.push(`0x${(Number(d.address) + i).toString(16).toUpperCase().padStart(8, "0")}  ` +
      chunk.map((b) => Number(b).toString(16).toUpperCase().padStart(2, "0")).join(" "));
  }
  setHTML("s6-dump-view", esc(hexRows.join("\n")) || "empty dump");
}
function renderMemoryCheck(c) {
  setHTML("s6-check-view", `CRC 0x${Number(c.address).toString(16).toUpperCase().padStart(8, "0")} ` +
    `(${c.length} B) = <b>0x${Number(c.crc).toString(16).toUpperCase().padStart(4, "0")}</b>`);
}

/* — S15 storage — */
function renderS15Panel() {
  const stores = [[1, "HK_Store"], [2, "Event_Store"], [3, "Science_Store"], [4, "Alarm_Store"]];
  const status = S.state.storage_status || [];
  setHTML("diag-s15", `
    <div class="row" style="margin-bottom:8px">
      <button class="ds-btn" data-s15st="refresh">REFRESH STATUS (15,13)</button></div>
    <div class="g" style="grid-template-columns:repeat(auto-fill,minmax(240px,1fr))">
    ${stores.map(([id, nm]) => {
      const st2 = status.find((s3) => Number(s3.id) === id) || {};
      const detail = st2.count !== undefined
        ? `${st2.count}${st2.capacity ? " / " + st2.capacity : ""} packets · ${st2.enabled ? "ENABLED" : "disabled"}${st2.circular ? " · circular" : ""}${st2.wrap_count ? ` · ${st2.wrap_count} wraps` : ""}`
        : "";
      return `<div class="ds-card"><h2>${id} · ${esc(st2.name || nm)}</h2><div class="bd">
        <div class="mono" style="font-size:14px">${st2.fill_pct !== undefined ? num(st2.fill_pct, 1) + " % full" : "no report — REFRESH STATUS (15,13)"}
          ${st2.overflow ? renderChip({ kind: "alarm", label: "OVERFLOW" }) : ""}</div>
        ${detail ? `<div style="font-size:12px;color:var(--tx3)">${esc(detail)}</div>` : ""}
        <div class="row" style="margin-top:6px">
          <button class="ds-btn" data-s15st="enable" data-store="${id}">EN (15,1)</button>
          <button class="ds-btn" data-s15st="disable" data-store="${id}">DIS (15,2)</button>
          <button class="ds-btn" data-s15st="dump" data-store="${id}">DUMP (15,9)</button>
          <button class="ds-btn danger" data-s15st="clear" data-store="${id}">CLEAR</button>
        </div></div></div>`;
    }).join("")}
    </div>`);
}
async function s15StoreCommand(action, storeId) {
  const map = { enable: 1, disable: 2, dump: 9, clear: 11, refresh: 13 };
  const subtype = map[action];
  const data = storeId ? (Number(storeId) & 0xFF).toString(16).toUpperCase().padStart(2, "0") : "";
  await sendPus({ service: 15, subtype, data_hex: data,
    name: `S15_${action.toUpperCase()}`, position: S.position });
  announce(`S15 ${action} sent`);
}

/* — S9 time — */
function renderS9Panel() {
  const tc = S.state.time_correlation || {};
  const cuc = fmtCUC(tc.sc_cuc_coarse, tc.sc_cuc_fine, tc.epoch);
  const stateCls = tc.offset_state === "alarm" ? "alarm" : tc.offset_state === "warning" ? "warn" : "ok";
  setHTML("diag-s9", `
    <div class="ds-card"><h2>Time correlation (S9)</h2><div class="bd">
      <table class="plain"><tbody>
        <tr><td>Ground (mission clock)</td><td class="mono">${fmtUTC(simNow(), { date: true })}</td></tr>
        <tr><td>OBC UTC (TM 0x0309${tc.cuc_fine_available ? " + fine" : ""})</td><td class="mono">${esc(cuc.utc)}</td></tr>
        <tr><td>OBC CUC raw</td><td class="mono">${esc(cuc.raw)}</td></tr>
        <tr><td>Offset (sc − ground)</td><td class="mono">${tc.offset_s === null || tc.offset_s === undefined ? "—" : num(tc.offset_s, 3) + " s"}
          ${renderChip({ kind: stateCls, label: (tc.offset_state || "unknown").toUpperCase() })}</td></tr>
        <tr><td>Time reference</td><td class="mono">${esc(tc.time_ref_name || "—")}</td></tr>
        <tr><td>Since last sync</td><td class="mono">${tc.never_synced ? "NEVER SYNCED" : fmtAge(tc.time_since_sync_s)}</td></tr>
      </tbody></table>
      <div class="row" style="margin-top:8px">
        <button class="ds-btn" id="s9-report">REQUEST TIME REPORT (9,2)</button>
        <button class="ds-btn" id="s9-sync">SYNC TIME TO GROUND</button>
        <div class="fld"><label for="s9-set">Set SC time (UTC)</label>
          <input id="s9-set" class="in mono" placeholder="YYYY-MM-DDTHH:MM:SS"></div>
        <button class="ds-btn" id="s9-set-now">NOW</button>
        <button class="ds-btn" id="s9-set-send">SET SC TIME</button>
      </div>
      <div id="s9-preview" class="mono" style="font-size:12px;color:var(--tx3);margin-top:6px"></div>
    </div></div>`);
  s9UpdatePreview();
}
function s9CucFor(iso) {
  const epochIso = (S.state.time_correlation || {}).epoch || "2000-01-01T12:00:00Z";
  const t = iso ? Date.parse(String(iso).endsWith("Z") ? iso : iso + "Z") : simNowMs();
  if (!Number.isFinite(t)) return null;
  return Math.round((t - Date.parse(epochIso)) / 1000);
}
function s9UpdatePreview() {
  const cuc = s9CucFor($("s9-set") ? $("s9-set").value : "");
  const el = $("s9-preview");
  if (el) el.style.color = "var(--tx3)";      // clear any previous refusal
  setText("s9-preview", cuc === null ? "CUC —" : `CUC ${cuc} (0x${(cuc >>> 0).toString(16).toUpperCase().padStart(8, "0")})`);
}
async function s9SetTime(iso) {
  const cuc = s9CucFor(iso);
  if (cuc === null) {
    panelResult("s9-preview",
      "SET SC TIME refused — the time value is empty or unparseable. Press NOW to fill it.", true);
    return;
  }
  const v = cuc >>> 0;
  const data = [(v >>> 24) & 255, (v >>> 16) & 255, (v >>> 8) & 255, v & 255]
    .map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ");
  await sendPus({ service: 9, subtype: 1, data_hex: data,
    name: "S9_SET_TIME", position: S.position });
  panelResult("s9-preview",
    `SET SC TIME sent — CUC ${v} (0x${v.toString(16).toUpperCase().padStart(8, "0")})`, false);
}

/* — PARAMS panel (classic ON-DEMAND TM tab relocated here) — */
let paramsFilter = "";
let paramsOnDemandOnly = false;
function renderParamsPanel() {
  const f = paramsFilter.toLowerCase();
  const list = S.paramCatalog.filter((p) =>
    (!paramsOnDemandOnly || p.on_demand) &&
    (!f || (p.name || "").toLowerCase().includes(f) || (p.hex || "").toLowerCase().includes(f)));
  const rows = list.slice(0, 400).map((p) => {
    const v = paramVal(p.hex);
    const eng = engValue(p.hex, v);
    const fr = paramFreshness(p.hex);
    return `<tr class="${(fr === "stale" || fr === "disabled") ? "stale" : ""}" title="${esc(fr)}">
      <td class="mono">${esc(p.hex)}</td><td>${esc(p.name)}</td><td>${esc(p.subsystem)}</td>
      <td>${esc(p.units || "")}</td><td>${p.in_hk ? "HK" : ""}</td><td>${p.on_demand ? "OD" : ""}</td>
      <td class="mono">${fr === "stale" ? "—" : (eng !== null ? esc(eng) : num(v, 3))}</td>
      <td>${p.on_demand || !p.in_hk
        ? `<button class="ds-btn" data-od-get="${esc(p.hex)}">GET (20,3)</button>` : ""}</td></tr>`;
  }).join("");
  setHTML("diag-params", `
    <div class="ds-card"><h2>Parameter catalog · TC(20,3) on-demand fetch</h2><div class="bd">
      <div class="row" style="margin-bottom:6px">
        <input id="params-filter" class="in" placeholder="filter…" value="${esc(paramsFilter)}">
        <label style="font-size:12px"><input type="checkbox" id="params-od" ${paramsOnDemandOnly ? "checked" : ""}> on-demand only</label>
        <button class="ds-btn" id="params-refresh">REFRESH</button>
        <span style="font-size:12px;color:var(--tx3)">${list.length} parameters</span>
      </div>
      <div class="scroll-y" style="max-height:420px">
      <table class="plain"><thead><tr><th>ID</th><th>Name</th><th>Subsys</th><th>Unit</th>
        <th>HK</th><th>OD</th><th>Value</th><th></th></tr></thead><tbody>${rows}</tbody></table>
      </div></div></div>`);
}
async function onDemandGet(hexId) {
  const pid = parseInt(hexId, 16) >>> 0;
  const data = [(pid >>> 8) & 255, pid & 255]
    .map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ");
  await sendPus({ service: 20, subtype: 3, data_hex: data,
    name: `OD_GET_${hexId}`, position: S.position });
  setTimeout(async () => {
    try { handleState(await getJSON("/api/state")); renderDiag(); } catch (e) { /* offline */ }
  }, 250);
}

/* ═══ 13. FDIR — shared S12/S19 renderers, autonomy per M4 ════════════════ */

async function loadFdir() {
  try { S.fdir = await getJSON("/api/fdir"); }
  catch (e) { S.fdir = null; }
  if (S.page === "fdir") renderFdirPage();
  if (S.page === "diag" && (diagTab === "s12" || diagTab === "s19")) renderDiag();
}

/* ONE implementation, two mounts (FDIR + DIAG). */
let s12Filter = "";
let s12OolOnly = false;
export function renderS12Block(mount) {
  const monitors = ((S.fdir || {}).monitors) || ((S.fdir || {}).s12) || [];
  // /api/fdir marks a violated monitor with status "VIOLATION"; older
  // fixtures use out_of_limits. Accept both (this was one of the "content is
  // not available" data-path breaks — OOL state never rendered live).
  const isOol = (m) => !!m.out_of_limits || m.state === "out_of_limits" ||
    /viol|out/i.test(String(m.status || ""));
  const f = s12Filter.toLowerCase();
  const list = monitors.filter((m) =>
    (!s12OolOnly || isOol(m)) &&
    (!f || JSON.stringify(m).toLowerCase().includes(f)));
  const rows = list.map((m) => `<tr class="${isOol(m) ? "a" : ""}">
    <td class="mono">${m.mon_id ?? m.id ?? "—"}</td>
    <td class="mono">${hex4(m.param_id)}</td>
    <td>${esc(m.name || m.param_name ||
      (S.paramByHex[hex4(m.param_id)] ? S.paramByHex[hex4(m.param_id)].name : ""))}</td>
    <td>${esc(String(m.check_type_name ?? m.check_type ?? m.type ?? ""))}</td>
    <td class="mono">${num(m.low_limit ?? m.low, 2)}</td>
    <td class="mono">${num(m.high_limit ?? m.high, 2)}</td>
    <td class="mono">${m.current_value === null || m.current_value === undefined
      ? "—" : num(m.current_value, 2)}</td>
    <td>${m.enabled ? "EN" : "dis"}</td>
    <td>${isOol(m) ? renderChip({ kind: "alarm", label: "OOL" })
      : esc(String(m.status || ""))}</td>
    <td><button class="ds-btn" data-s12row="enable" data-mid="${m.mon_id ?? m.id}">EN</button>
        <button class="ds-btn" data-s12row="disable" data-mid="${m.mon_id ?? m.id}">DIS</button>
        <button class="ds-btn" data-s12row="delete" data-mid="${m.mon_id ?? m.id}">✕</button></td></tr>`).join("");
  return `
    <div class="ds-card"><h2>On-board monitoring (S12) <span class="r">${mount} mount — one renderer</span></h2>
      <div class="bd">
      <div class="row" style="margin-bottom:6px">
        <button class="ds-btn" data-s12="refresh">REFRESH (12,12)</button>
        <button class="ds-btn" data-s12="enable">ENABLE MON (12,1)</button>
        <button class="ds-btn" data-s12="disable">DISABLE MON (12,2)</button>
        <input class="in" data-s12filter placeholder="filter…" value="${esc(s12Filter)}">
        <label style="font-size:12px"><input type="checkbox" data-s12ool ${s12OolOnly ? "checked" : ""}> out-of-limits only</label>
      </div>
      <div class="row" style="margin-bottom:6px">
        <input class="in mono" id="s12-add-mid-${mount}" placeholder="mon id" style="width:70px">
        <input class="in mono" id="s12-add-pid-${mount}" placeholder="param 0xHHHH" style="width:110px">
        <select class="in" id="s12-add-type-${mount}"><option value="0">absolute</option><option value="1">delta</option></select>
        <input class="in mono" id="s12-add-low-${mount}" placeholder="low" style="width:80px">
        <input class="in mono" id="s12-add-high-${mount}" placeholder="high" style="width:80px">
        <button class="ds-btn" data-s12add="${mount}">ADD (12,5)</button>
      </div>
      <table class="plain"><thead><tr><th>Mon</th><th>Param</th><th>Name</th><th>Check</th>
        <th>Low</th><th>High</th><th>Value</th><th>En</th><th>State</th><th></th></tr></thead>
        <tbody>${rows || `<tr><td colspan="10">no monitor table — REFRESH (12,12)</td></tr>`}</tbody></table>
      </div></div>`;
}
export function renderS19Block(mount) {
  // /api/fdir serves the rule table under `rules` (row shape: ea_id, name,
  // trigger_kind_name, trigger_label, action_label, fire_count). The old
  // code only read `ea_rules`/`s19`, so the LIVE rule table always rendered
  // "no rule table" — the core of defect "PUS content is not available".
  const rules = ((S.fdir || {}).ea_rules) || ((S.fdir || {}).s19) ||
                ((S.fdir || {}).rules) || [];
  const actions = S.tcCatalog.map((c) => c.name);
  const rows = rules.map((r) => `<tr>
    <td class="mono">${r.ea_id ?? r.id ?? "—"}</td>
    <td>${esc(String(r.name || ""))}</td>
    <td>${esc(String(r.trigger_kind_name ||
      (Number(r.trigger_kind) === 1 ? "S12 param" : "S5 event")))}</td>
    <td class="mono">${esc(String(r.trigger_label || hex4(r.trigger_value ?? r.trigger_id)))}</td>
    <td>${esc(String(r.action || r.action_name || r.action_label ||
      (r.action_func_id != null ? `func ${r.action_func_id}` : "")))}</td>
    <td>${r.enabled ? "EN" : "dis"}</td>
    <td class="mono">${r.fire_count ?? "—"}</td>
    <td><button class="ds-btn" data-s19row="enable" data-eid="${r.ea_id ?? r.id}">EN</button>
        <button class="ds-btn" data-s19row="disable" data-eid="${r.ea_id ?? r.id}">DIS</button>
        <button class="ds-btn" data-s19row="delete" data-eid="${r.ea_id ?? r.id}">✕</button></td></tr>`).join("");
  return `
    <div class="ds-card"><h2>Event-action rules (S19) <span class="r">${mount} mount — one renderer</span></h2>
      <div class="bd">
      <div class="row" style="margin-bottom:6px">
        <button class="ds-btn" data-s19="refresh">REFRESH (19,8)</button>
        <input class="in mono" id="s19-add-eid-${mount}" placeholder="EA id" style="width:70px">
        <select class="in" id="s19-add-kind-${mount}">
          <option value="0">0 — S5 event id</option><option value="1">1 — S12 parameter id</option></select>
        <input class="in mono" id="s19-add-trig-${mount}" placeholder="trigger 0xHHHH" style="width:110px">
        <select class="in" id="s19-add-action-${mount}">${actions.map((a) => `<option>${esc(a)}</option>`).join("")}</select>
        <button class="ds-btn" data-s19add="${mount}">ADD RULE (19,1)</button>
      </div>
      <table class="plain"><thead><tr><th>EA</th><th>Name</th><th>Kind</th><th>Trigger</th>
        <th>Action</th><th>En</th><th>Fires</th><th></th></tr></thead>
        <tbody>${rows || `<tr><td colspan="8">no rule table — REFRESH (19,8)</td></tr>`}</tbody></table>
      </div></div>`;
}

function buildFdirPage() {
  return `
  <div class="pad hide" id="page-fdir" role="tabpanel" aria-label="FDIR">
    <h1 class="pt">Fault detection, isolation &amp; recovery</h1>
    <p class="sub">What the spacecraft <em>told</em> us (S5 / S12 / S19 / mode), plus recovery controls.</p>
    <div class="guided-note" data-ann="fdir.strip"></div>
    <div class="ds-card"><h2>FDIR state</h2><div class="bd row" id="fdir-strip" style="gap:18px"></div></div>
    <div class="row" style="margin:10px 0">
      <button class="ds-btn" id="fdir-refresh-all">REFRESH ALL</button>
      <button class="ds-btn" data-s12="refresh">REPORT MONITORS (12,12)</button>
      <button class="ds-btn" data-s19="refresh">REPORT RULES (19,8)</button>
      <button class="ds-btn" data-s12="enable">ENABLE MONITORING (12,1)</button>
      <button class="ds-btn" data-s12="disable">DISABLE MONITORING (12,2)</button>
      <button class="ds-btn" id="fdir-ack-all">ACK ALL ALARMS</button>
    </div>
    <div id="fdir-autonomy-host"></div>
    <div class="g" style="grid-template-columns:1fr;margin-top:10px">
      <div id="fdir-s12"></div>
      <div id="fdir-s19"></div>
      <div class="ds-card"><h2>Alarm / anomaly journal</h2><div class="bd">
        <div class="row" style="font-size:12px;margin-bottom:6px">
          ${["s5:S5 events", "s12:S12 transitions", "s19:S19 rule fires", "tcrej:TC rejections", "mode:mode changes"]
            .map((x) => { const [k, l] = x.split(":");
              return `<label><input type="checkbox" data-fdir-j="${k}" checked> ${l}</label>`; }).join(" ")}
        </div>
        <div class="scroll-y" style="max-height:240px" id="fdir-journal"></div>
      </div></div>
    </div>
  </div>`;
}

function renderFdirPage() {
  const d = S.fdir || {};
  // /api/fdir nests the summary under `state` (fdir_level, safe_latched,
  // load_shed_stage, monitors_out_of_limits); the old flat keys never
  // existed, so the whole strip rendered "—". Accept both shapes.
  const fst = d.state || {};
  const mode = scModeInfo();
  const oolCount = fst.monitors_out_of_limits ??
    (d.monitors || []).filter((m) => m.out_of_limits ||
      /viol|out/i.test(String(m.status || ""))).length;
  const strip = [
    ["FDIR LEVEL", fst.fdir_level ?? d.fdir_level ?? paramVal("0x0700") ?? "—", ""],
    ["SC MODE", mode.label, `src: ${mode.src}`],
    ["LATCHED", (fst.safe_latched ?? d.latched_safe) ? "SAFE"
      : (fst.emergency_latched ?? d.latched_emergency) ? "EMERG" : "—",
      fst.latch_source || ""],
    ["LOAD SHED", fst.load_shed_stage ?? paramVal("0x0134") ?? "—", ""],
    ["MONITORS OOL", oolCount, ""],
    ["ACTIVE ALARMS", paramVal("0x0701") ?? (d.active_alarms || []).length, "TM 0x0701"],
  ];
  setHTML("fdir-strip", strip.map(([k, v, s2]) => `
    <div><div style="font-size:12px;letter-spacing:1px;color:var(--tx3)">${esc(k)}</div>
    <div class="mono" style="font-size:16px;font-weight:600">${esc(String(v))}</div>
    ${s2 ? `<div style="font-size:12px;color:var(--tx3)">${esc(s2)}</div>` : ""}</div>`).join(""));

  // M4: SIMULATED ON-BOARD AUTONOMY — sim mode only, retitled; real mode
  // shows the TM-derived story instead (designed empty state, not accident).
  const host = $("fdir-autonomy-host");
  if (host) {
    if (S.timeMode === "sim" && (S.state.sim_health || {}).fdir) {
      const a = S.state.sim_health.fdir;
      host.innerHTML = `<div class="ds-card"><h2>SIMULATED ON-BOARD AUTONOMY
        <span class="r">instructor truth — not downlinked</span></h2><div class="bd">
        <div class="guided-note" data-ann="fdir.autonomy"></div>
        <div class="row" style="gap:18px">
          <div>active faults: <b class="mono">${(a.active_faults || []).length}</b></div>
          <div>pending cascades: <b class="mono">${(a.pending_cascades || []).length}</b></div>
          <div>recovery attempts: <b class="mono">${(a.recovery_attempts || []).length || (a.recoveries || []).length || 0}</b></div>
        </div>
        ${(a.procedures || []).map((p) => `<div style="margin-top:6px;font-size:12.5px">
          ${esc(p.name || "on-board procedure")} — ${num(p.progress, 0)} %</div>`).join("")}
        </div></div>`;
      applyAnnotations();
    } else {
      host.innerHTML = `<div class="ds-card"><h2>On-board autonomy — as telemetered</h2>
        <div class="bd" style="font-size:13px;color:var(--tx2)">
        A real spacecraft does not downlink its fault-cascade queue. The honest ground view is
        what it <em>told</em> us: S5 events (drawer below), S12 monitor transitions (table),
        S19 rule fires, and the mode itself (${esc(mode.label)}, ${esc(mode.src)}).</div></div>`;
    }
  }
  setHTML("fdir-s12", renderS12Block("fdir"));
  setHTML("fdir-s19", renderS19Block("fdir"));
  renderFdirJournal();
}
const fdirJournalFilter = { s5: true, s12: true, s19: true, tcrej: true, mode: true };
function renderFdirJournal() {
  const j = ((S.fdir || {}).journal) || [];
  const rows = j.filter((e) => {
    const kind = String(e.kind || e.type || "").toLowerCase();
    if (kind.includes("rej")) return fdirJournalFilter.tcrej;
    if (kind.includes("s12") || kind.includes("monitor")) return fdirJournalFilter.s12;
    if (kind.includes("s19") || kind.includes("rule")) return fdirJournalFilter.s19;
    if (kind.includes("mode")) return fdirJournalFilter.mode;
    return fdirJournalFilter.s5;
  }).slice(-80).reverse().map((e) => renderEventRow({
    severity: e.severity, subsystem: e.subsystem, utc: e.utc || fmtUTC(e.timestamp),
    description: e.text || e.message || e.description })).join("");
  setHTML("fdir-journal", rows || `<div class="logrow nom">journal empty</div>`);
}

/* ═══ 14. PROC — procedures ═══════════════════════════════════════════════ */

function buildProcPage() {
  return `
  <div class="pad hide" id="page-proc" role="tabpanel" aria-label="Procedures">
    <h1 class="pt">Procedures</h1>
    <p class="sub">Loader · step-by-step runner · document viewer · manual override · builder.
      Reachable from any alarm row (P7.6).</p>
    <div class="guided-note" data-ann="proc.runner"></div>
    <div class="g" style="grid-template-columns:minmax(0,1fr) 420px;align-items:start">
      <div class="g">
        <div class="ds-card"><h2>Load &amp; run</h2><div class="bd">
          <div class="row">
            <div class="fld"><label for="proc-source">Source</label>
              <select id="proc-source" class="in">
                <option value="activity">Activity type</option>
                <option value="ops">Ops procedure</option>
                <option value="saved">Saved procedure</option></select></div>
            <div class="fld"><label for="proc-type">Procedure</label>
              <select id="proc-type" class="in" style="min-width:260px"></select></div>
            <label style="font-size:12px"><input type="checkbox" id="proc-stepwise" checked> step-by-step</label>
          </div>
          <div class="row" style="margin-top:8px">
            <button class="ds-btn primary" data-proc="load">LOAD</button>
            <button class="ds-btn" data-proc="start">START</button>
            <button class="ds-btn" data-proc="pause">PAUSE</button>
            <button class="ds-btn" data-proc="resume">RESUME</button>
            <button class="ds-btn" data-proc="step">STEP</button>
            <button class="ds-btn" data-proc="skip">SKIP</button>
            <button class="ds-btn danger" data-proc="abort">ABORT</button>
          </div>
          <div id="proc-exec-status" style="margin-top:10px"></div>
          <div class="scroll-y" style="max-height:220px;margin-top:6px" id="proc-exec-log"></div>
        </div></div>
        <div class="ds-card"><h2>Procedure document</h2>
          <div class="bd scroll-y" style="max-height:320px" id="proc-doc">select a procedure…</div></div>
      </div>
      <div class="g" style="align-content:start">
        <div class="ds-card"><h2>Manual override <span class="r">arbitrary TC during a run</span></h2>
          <div class="bd"><div class="row">
            <input id="proc-ov-svc" class="in mono" placeholder="svc" style="width:60px">
            <input id="proc-ov-sub" class="in mono" placeholder="sub" style="width:60px">
            <input id="proc-ov-data" class="in mono" placeholder="data hex" style="flex:1">
            <button class="ds-btn" id="proc-ov-send">SEND OVERRIDE</button>
          </div><div id="proc-ov-result" style="font-size:12px;color:var(--tx3);margin-top:6px"></div></div></div>
        <div class="ds-card"><h2>Procedure builder</h2><div class="bd">
          <div class="row">
            <input id="pb-name" class="in" placeholder="procedure name" style="flex:1">
            <select id="pb-step-type" class="in">
              <option value="command">Command</option><option value="tlm_check">Telemetry check</option>
              <option value="wait">Wait</option><option value="gonogo">GO-NO-GO</option>
              <option value="note">Note</option></select>
            <button class="ds-btn" id="pb-add">+ ADD</button>
          </div>
          <div id="pb-step-fields" class="row" style="margin-top:6px"></div>
          <ol id="pb-steps" style="margin:8px 0 0 18px;font-size:13px"></ol>
          <div class="row" style="margin-top:8px">
            <button class="ds-btn" id="pb-save">SAVE PROCEDURE</button>
            <button class="ds-btn" id="pb-run">RUN THESE STEPS</button>
            <button class="ds-btn" id="pb-clear">CLEAR</button>
            <select id="pb-saved" class="in"><option value="">— load saved —</option></select>
          </div>
          <div id="pb-result" style="font-size:12px;color:var(--tx3);margin-top:6px"></div>
        </div></div>
      </div>
    </div>
  </div>`;
}

const pb = { steps: [] };
let procPoll = null;
async function loadProcSources() {
  const src = $("proc-source") ? $("proc-source").value : "activity";
  const sel = $("proc-type");
  if (!sel) return;
  try {
    let opts = [];
    if (src === "activity") {
      const d = await getJSON("/api/procedure/activity-types");
      opts = (d.activity_types || d.types || []).map((t) => [t.id || t.name || t, t.label || t.name || t]);
    } else if (src === "ops") {
      const d = await getJSON("/api/procedures");
      opts = (d.procedures || []).map((p) => [p.path || p.id || p, p.name || p.path || p]);
    } else {
      const d = await getJSON("/api/procedure/custom");
      opts = (d.procedures || []).map((p) => [p.id || p.name, p.name || p.id]);
      const saved = $("pb-saved");
      if (saved) saved.innerHTML = `<option value="">— load saved —</option>` +
        opts.map(([v, l]) => `<option value="${esc(String(v))}">${esc(String(l))}</option>`).join("");
    }
    sel.innerHTML = opts.map(([v, l]) => `<option value="${esc(String(v))}">${esc(String(l))}</option>`).join("");
  } catch (e) { sel.innerHTML = `<option value="">unavailable</option>`; }
  loadProcDoc();
}
async function loadProcDoc() {
  const src = $("proc-source") ? $("proc-source").value : "activity";
  const v = $("proc-type") ? $("proc-type").value : "";
  if (src !== "ops" || !v) { return; }
  try {
    const txt = await (await fetch(`/api/procedures/${encodeURIComponent(v)}`)).text();
    // Same JSON-as-markdown defect as the manual viewer (2026-08-20).
    setHTML("proc-doc", renderMarkdown(
      (txt && typeof txt === "object") ? (txt.content_md || "") : txt));
  } catch (e) { setHTML("proc-doc", "document unavailable"); }
}
export function renderMarkdown(md) {
  // Small, safe markdown subset (headings, bold, code, lists) — classic parity.
  let h = esc(md);
  h = h.replace(/^### (.*)$/gm, "<h4>$1</h4>")
       .replace(/^## (.*)$/gm, "<h3>$1</h3>")
       .replace(/^# (.*)$/gm, "<h2>$1</h2>")
       .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
       .replace(/`([^`]+)`/g, "<code>$1</code>")
       .replace(/^---+$/gm, "<hr>")
       .replace(/^[-*] (.*)$/gm, "<li>$1</li>");
  // GFM tables. The shipped manual is table-heavy (the command reference alone
  // is >1000 table lines), so without this the page renders as a wall of pipes.
  h = h.replace(
    /^\|(.+)\|[ \t]*\n\|[ \t]*:?-{2,}[-:| \t]*\|[ \t]*\n((?:\|.*\|[ \t]*\n?)+)/gm,
    (_m, head, body) => {
      const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|");
      const th = cells(head).map((c) => `<th>${c.trim()}</th>`).join("");
      const tr = body.trim().split("\n").map((r) =>
        `<tr>${cells(r).map((c) => `<td>${c.trim()}</td>`).join("")}</tr>`).join("");
      return `<table class="man-tbl"><thead><tr>${th}</tr></thead>` +
             `<tbody>${tr}</tbody></table>\n`;
    });
  h = h.replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, "<ul>$1</ul>")
       .replace(/\n{2,}/g, "<br><br>");
  return `<div style="font-size:13px;line-height:1.55">${h}</div>`;
}
async function procAction(action) {
  if (action === "load") {
    const source = $("proc-source").value, id = $("proc-type").value;
    const body = { source, id, step_by_step: $("proc-stepwise").checked };
    if (source === "activity") body.activity_type = id;
    if (source === "ops") body.path = id;
    const r = await postJSON("/api/procedure/load", body);
    setHTML("proc-exec-status", r.ok ? "loaded" :
      `<span style="color:var(--alarm)">load failed (${r.status})</span>`);
    startProcPolling();
    return;
  }
  const r = await postJSON(`/api/procedure/${action}`, {});
  if (!r.ok) setHTML("proc-exec-status", `<span style="color:var(--alarm)">${action} failed (${r.status})</span>`);
  if (action === "start") startProcPolling();
}
function startProcPolling() {
  if (procPoll) return;
  procPoll = poller(async () => {
    const st = await getJSON("/api/procedure/status");
    S.procStatus = st;
    renderProcStatus();
    if (!st || !st.loaded && !st.running) { procPoll.stop(); procPoll = null; }
  }, 2000);
  procPoll.start();
}
function renderProcStatus() {
  const st = S.procStatus || {};
  const steps = st.steps || [];
  const idx = st.current_step ?? st.step_index ?? -1;
  setHTML("proc-exec-status",
    `<div style="font-size:13px">${esc(st.name || st.procedure || "no procedure loaded")} — ` +
    `<b>${esc(String(st.state || st.status || "idle").toUpperCase())}</b></div>` +
    renderStepper(steps.map((s2) => (s2.name || s2.description || "step")), Number(idx)));
  setHTML("proc-exec-log", (st.log || []).slice(-50).reverse()
    .map((l) => `<div class="logrow">${esc(typeof l === "string" ? l : (l.text || JSON.stringify(l)))}</div>`).join(""));
}
function pbFieldMarkup() {
  const t = $("pb-step-type") ? $("pb-step-type").value : "command";
  const f = {
    command: `<input class="in mono" id="pb-f-svc" placeholder="svc,sub" style="width:80px">
      <input class="in mono" id="pb-f-data" placeholder="data hex" style="flex:1">`,
    tlm_check: `<input class="in mono" id="pb-f-param" placeholder="param (name or 0xHHHH)" style="flex:1">
      <select class="in" id="pb-f-op"><option>==</option><option>&lt;</option><option>&gt;</option></select>
      <input class="in mono" id="pb-f-val" placeholder="value" style="width:90px">`,
    wait: `<input class="in" id="pb-f-wait" type="number" placeholder="seconds" style="width:110px">`,
    gonogo: `<input class="in" id="pb-f-label" placeholder="poll label" style="flex:1">`,
    note: `<input class="in" id="pb-f-note" placeholder="note text" style="flex:1">`,
  }[t];
  return f || "";
}
function pbAddStep() {
  const t = $("pb-step-type").value;
  const step = { type: t };
  if (t === "command") {
    const [svc, sub] = String($("pb-f-svc").value || "").split(",").map((x) => Number(x));
    step.service = svc; step.subtype = sub; step.data = $("pb-f-data").value;
  } else if (t === "tlm_check") {
    step.param = $("pb-f-param").value; step.op = $("pb-f-op").value; step.value = $("pb-f-val").value;
  } else if (t === "wait") step.seconds = Number($("pb-f-wait").value);
  else if (t === "gonogo") step.label = $("pb-f-label").value;
  else step.text = $("pb-f-note").value;
  pb.steps.push(step);
  renderPbSteps();
}
function renderPbSteps() {
  setHTML("pb-steps", pb.steps.map((s2, i) =>
    `<li>${esc(s2.type)} — ${esc(JSON.stringify(s2))}
      <button class="ds-btn" data-pb-del="${i}">✕</button></li>`).join(""));
}
async function pbSave() {
  const r = await postJSON("/api/procedure/save",
    { name: $("pb-name").value || "unnamed", steps: pb.steps });
  setText("pb-result", r.ok ? "saved" : `save failed (${r.status})`);
  loadProcSources();
}
async function pbRun() {
  const r = await postJSON("/api/procedure/load",
    { source: "inline", name: $("pb-name").value || "builder steps",
      steps: pb.steps, step_by_step: true });
  setText("pb-result", r.ok ? "loaded — use runner controls" : `run failed (${r.status})`);
  startProcPolling();
}

/* ═══ 15. TRND — archive trends (Chart.js, vendor-guarded) ════════════════ */

function buildTrndPage() {
  return `
  <div class="pad hide" id="page-trnd" role="tabpanel" aria-label="Trends">
    <h1 class="pt">Trends</h1>
    <p class="sub">Ground archive · eclipse/contact context · alarm &amp; event markers (P8)</p>
    <div id="trnd-provenance"></div>
    <div class="guided-note" data-ann="trnd.archive"></div>
    <div class="g" style="grid-template-columns:280px minmax(0,1fr);align-items:start">
      <div class="g" style="align-content:start">
        <div class="ds-card"><h2>Query</h2><div class="bd">
          <div class="fld"><label for="trnd-window">Window</label>
            <select id="trnd-window" class="in">
              <option value="900">15 m</option><option value="3600" selected>1 h</option>
              <option value="10800">3 h</option><option value="21600">6 h</option>
              <option value="43200">12 h</option><option value="86400">24 h</option>
              <option value="604800">7 d</option></select></div>
          <div class="fld" style="margin-top:6px"><label for="trnd-filter">Parameter filter</label>
            <input id="trnd-filter" class="in" placeholder="filter…"></div>
          <div style="font-size:12px;margin-top:6px">
            <label><input type="checkbox" id="trnd-bands" checked> limit bands</label><br>
            <label><input type="checkbox" id="trnd-shade" checked> eclipse/contact shading</label><br>
            <label><input type="checkbox" id="trnd-markers" checked> event + alarm markers</label><br>
            <label><input type="checkbox" id="trnd-norm"> normalise 0–100 %</label>
          </div>
          <div class="row" style="margin-top:8px">
            <button class="ds-btn primary" id="trnd-refresh">REFRESH</button>
            <button class="ds-btn" id="trnd-csv">EXPORT CSV</button>
          </div>
          <select id="trnd-params" class="in" multiple size="12" style="width:100%;margin-top:8px"
            aria-label="Parameters to trend"></select>
        </div></div>
        <div class="ds-card"><h2>Subsystem playback</h2><div class="bd row">
          <select id="trnd-playback-sub" class="in">
            ${["eps", "tcs", "aocs", "ttc", "payload", "prop", "obdh"].map((s2) => `<option>${s2}</option>`).join("")}</select>
          <button class="ds-btn" id="trnd-playback">LOAD SUBSYSTEM</button>
        </div></div>
      </div>
      <div class="g">
        <div class="ds-card"><h2>Chart</h2><div class="bd">
          <div id="trnd-chart-host"><canvas id="trnd-chart" height="240"></canvas></div></div></div>
        <div class="ds-card"><h2>Alarms in window</h2>
          <div class="bd scroll-y" style="max-height:150px" id="trnd-alarms"></div></div>
        <div class="ds-card"><h2>Events in window</h2>
          <div class="bd scroll-y" style="max-height:150px" id="trnd-events"></div></div>
        <div class="ds-card"><h2>Command history (archived)</h2>
          <div class="bd scroll-y" style="max-height:150px" id="trnd-cmds"></div></div>
        <div class="ds-card"><h2>Baseline vs now — health (NOM-011)
          <span class="r">baseline: annotations.yaml commissioning snapshot</span></h2>
          <div class="bd">
            <div class="guided-note" data-ann="trnd.baseline"></div>
            <div id="trnd-baseline"></div>
          </div></div>
      </div>
    </div>
  </div>`;
}

let trndChart = null;
let trndLastSeries = [];
function archiveBanner(status, detail) {
  // four-state provenance: ok / unavailable / failed / legacy
  const map = {
    ok: ["", ""],
    unavailable: ["stale", "ARCHIVE UNAVAILABLE — server has no archive database"],
    failed: ["alarm", `ARCHIVE QUERY FAILED — ${detail || ""}`],
    legacy: ["warn", "LEGACY ARCHIVE — schema predates provenance fields"],
  };
  const [cls, text] = map[status] || map.failed;
  setHTML("trnd-provenance", text ? `<div class="ds-banner ${cls}">${esc(text)}</div>` : "");
}
async function loadTrndParamNames() {
  try {
    const d = await getJSON("/api/archive/param-names");
    const names = d.names || d.parameters || [];
    const f = ($("trnd-filter") ? $("trnd-filter").value : "").toLowerCase();
    const sel = $("trnd-params");
    if (sel) sel.innerHTML = names.filter((n) => !f || String(n).toLowerCase().includes(f))
      .map((n) => `<option>${esc(String(n))}</option>`).join("");
    archiveBanner(d.status || "ok", d.detail);
  } catch (e) { archiveBanner("unavailable"); }
}
function trndSelected() {
  const sel = $("trnd-params");
  if (!sel || !sel.selectedOptions) return [];
  return Array.from(sel.selectedOptions).map((o) => o.value);
}
async function refreshTrend() {
  const names = trndSelected();
  const win = Number($("trnd-window").value);
  const end = simNowSec(), start = end - win;
  const series = [];
  try {
    for (const name of names.slice(0, 6)) {
      const d = await getJSON(`/api/archive/parameters?name=${encodeURIComponent(name)}` +
        `&start=${start}&end=${end}&limit=5000`);
      series.push({ name, rows: d.rows || d.data || [] });
      if (d.status && d.status !== "ok") { archiveBanner(d.status, d.detail); }
    }
    if ($("trnd-shade") && $("trnd-shade").checked) {
      for (const ctxName of ["orbit.eclipse", "orbit.contact"]) {
        try {
          const d = await getJSON(`/api/archive/parameters?name=${encodeURIComponent(ctxName)}` +
            `&start=${start}&end=${end}&limit=5000`);
          series.push({ name: ctxName, rows: d.rows || d.data || [], context: true });
        } catch (e) { /* context series optional */ }
      }
    }
    trndLastSeries = series;
    drawTrendChart(series);
    archiveBanner("ok");
  } catch (e) { archiveBanner("failed", String(e.message || e)); }
  loadArchiveTables(start, end);
}
function drawTrendChart(series) {
  const host = $("trnd-chart-host");
  if (typeof Chart === "undefined") {
    // vendor guard (R3): designed absence + textual latest values
    if (host) host.innerHTML = `<div class="ds-banner stale">Chart.js not installed
      (/static/vendor/js/chart.umd.min.js) — latest archive values:</div>` +
      `<table class="plain">${series.filter((s2) => !s2.context).map((s2) => {
        const last = s2.rows[s2.rows.length - 1];
        return `<tr><td>${esc(s2.name)}</td><td class="mono">${last ? num(last.value ?? last[1], 3) : "—"}</td></tr>`;
      }).join("")}</table>`;
    return;
  }
  const cv = $("trnd-chart");
  if (!cv) return;
  const norm = $("trnd-norm") && $("trnd-norm").checked;
  const datasets = series.filter((s2) => !s2.context).map((s2, i) => {
    let pts = s2.rows.map((r) => ({ x: (r.ts ?? r.t ?? r[0]) * 1000, y: r.value ?? r.v ?? r[1] }));
    if (norm && pts.length) {
      const ys = pts.map((p) => p.y), mn = Math.min(...ys), mx = Math.max(...ys), sp = (mx - mn) || 1;
      pts = pts.map((p) => ({ x: p.x, y: ((p.y - mn) / sp) * 100 }));
    }
    return { label: s2.name, data: pts, borderColor: `hsl(${i * 60},15%,${60 + i * 5}%)`,
      pointRadius: 0, borderWidth: 1.5 };
  });
  if (trndChart) trndChart.destroy();
  trndChart = new Chart(cv.getContext("2d"), {
    type: "line", data: { datasets },
    options: { animation: false, responsive: true,
      scales: { x: { type: "linear", ticks: { callback: (v) => fmtUTC(v / 1000) } } },
      plugins: { legend: { labels: { boxWidth: 10 } } } },
  });
}
async function loadArchiveTables(start, end) {
  const q = `?start=${start}&end=${end}`;
  try {
    const a = await getJSON(`/api/archive/alarms${q}`);
    setHTML("trnd-alarms", (a.alarms || a.rows || []).slice(-40).reverse().map((r) =>
      renderAlarmRow({ ...r, utc: fmtUTC(r.timestamp) })).join("") || `<div class="logrow nom">none</div>`);
  } catch (e) { setHTML("trnd-alarms", `<div class="logrow nom">archive alarms unavailable</div>`); }
  try {
    const ev = await getJSON(`/api/archive/events${q}`);
    setHTML("trnd-events", (ev.events || ev.rows || []).slice(-40).reverse().map((r) =>
      renderEventRow({ ...r, utc: fmtUTC(r.timestamp) })).join("") || `<div class="logrow nom">none</div>`);
  } catch (e) { setHTML("trnd-events", `<div class="logrow nom">archive events unavailable</div>`); }
  try {
    const c = await getJSON(`/api/archive/commands${q}`);
    setHTML("trnd-cmds", (c.commands || c.rows || []).slice(-40).reverse().map((r) =>
      `<div class="logrow">${fmtUTC(r.timestamp)} ${esc(r.name || `S${r.service},${r.subtype}`)} — ${esc(r.verdict || r.status || "")}</div>`).join("") ||
      `<div class="logrow nom">none</div>`);
  } catch (e) { setHTML("trnd-cmds", `<div class="logrow nom">archive commands unavailable</div>`); }
}
function exportTrendCsv() {
  const lines = ["series,ts,value"];
  for (const s2 of trndLastSeries) {
    if (s2.context) continue;
    for (const r of s2.rows) lines.push(`${s2.name},${r.ts ?? r[0]},${r.value ?? r[1]}`);
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "trend_export.csv";
  a.click();
}
async function loadSubsystemPlayback() {
  const sub = $("trnd-playback-sub").value;
  try {
    const d = await getJSON(`/api/archive/playback?subsystem=${encodeURIComponent(sub)}`);
    const series = (d.parameters || d.series || []).map((p) => ({
      name: p.name, rows: p.rows || p.samples || [] }));
    trndLastSeries = series;
    drawTrendChart(series);
    archiveBanner(d.status || "ok", d.detail);
  } catch (e) { archiveBanner("failed", String(e.message || e)); }
}

/* ═══ 16. MAN — operations manual ═════════════════════════════════════════ */

function buildManPage() {
  return `
  <div class="pad hide" id="page-man" role="tabpanel" aria-label="Manual">
    <h1 class="pt">Operations manual</h1>
    <p class="sub">Sections filtered to your position; search covers titles and body text.</p>
    <div class="g" style="grid-template-columns:300px minmax(0,1fr);align-items:start">
      <div class="ds-card"><h2>Contents</h2><div class="bd">
        <input id="man-search" class="in" placeholder="search…" style="width:100%;margin-bottom:8px">
        <div id="man-toc" class="scroll-y" style="max-height:480px"></div>
        <div class="sub" style="margin-top:6px">sections <span id="man-count">0/0</span></div></div></div>
      <div class="ds-card"><h2 id="man-title">—</h2>
        <div class="bd scroll-y" style="max-height:560px" id="man-body">select a section…</div></div>
    </div>
    <div class="ds-card" style="margin-top:12px"><h2>Procedure cards — your position</h2>
      <div class="bd">
        <div class="row" style="gap:10px;align-items:center;margin-bottom:8px">
          <label class="sub"><input type="checkbox" id="pcard-mine-only" checked>
            only cards my position is required for</label>
          <input id="pcard-search" class="in" placeholder="filter cards…" style="max-width:260px">
          <span class="sub" id="pcard-count"></span>
        </div>
        <div id="pcard-list" class="scroll-y" style="max-height:520px"></div>
      </div></div>
  </div>`;
}

/* ── Procedure cards, per position ────────────────────────────────────────
 *
 * Field report 2026-08-20: "could the MAN page display the procedure cards per
 * position with the specific steps to be executed by that position highlighted
 * somehow (so this changes by position)?"
 *
 * Two grades of attribution, shown differently because they are not equally
 * certain:
 *
 *   * the ROLE line and which cards are relevant come from
 *     procedure_index.yaml's `required_positions` / `position_roles` -- a
 *     human wrote those, they are exact;
 *   * the per-step highlight is DERIVED, by joining each command step's
 *     func_id against the position's commanding authority in positions.yaml.
 *
 * Only the func_id grain is used. Service-level authority looked tempting and
 * is useless: every position may send S17 pings and S3 HK requests, so a
 * service match attributed CONNECTION_TEST to six positions at once and
 * highlighted nearly every step of every card. A highlight that is always on
 * is not a highlight.
 *
 * Steps with no owner at this grain -- GO/NO-GO calls, telemetry checks, and
 * commands anyone may send -- are shown plainly and never highlighted. The
 * card says so, so an operator does not read "not highlighted" as "not mine".
 */
async function loadProcedureCards() {
  try {
    S.procCards = await getJSON(
      `/api/procedure/cards?position=${encodeURIComponent(S.position)}`);
  } catch (e) { S.procCards = null; }
  renderProcedureCards();
}
function renderProcedureCards() {
  const host = $("pcard-list");
  if (!host) return;
  const data = S.procCards || {};
  const all = data.cards || [];
  const mineOnly = $("pcard-mine-only") ? $("pcard-mine-only").checked : true;
  const f = ($("pcard-search") ? $("pcard-search").value : "").toLowerCase();
  const cards = all.filter((c) => (!mineOnly || c.relevant) &&
    (!f || `${c.id} ${c.name} ${c.category}`.toLowerCase().includes(f)));
  setText("pcard-count", `${cards.length}/${all.length} cards`);
  if (!cards.length) {
    host.innerHTML = `<div class="logrow nom">no procedure cards for this position</div>`;
    return;
  }
  host.innerHTML = cards.map((c) => {
    const open = S.procCardOpen === c.id;
    const roleLine = c.my_role
      ? `<div class="sub" style="margin:2px 0 6px">Your role: ${esc(c.my_role)}</div>`
      : (c.relevant
          ? `<div class="sub" style="margin:2px 0 6px">Your position is required; no per-position role recorded for this card.</div>`
          : "");
    const mineChip = c.my_step_count
      ? `<span class="ds-badge">${c.my_step_count} step${c.my_step_count === 1 ? "" : "s"} yours</span>`
      : "";
    const body = open ? `
      ${roleLine}
      <div class="sub" style="margin-bottom:6px">Required: ${
        esc((c.required_positions || []).join(", ") || "—")}</div>
      <table class="plain man-tbl"><tr><th>#</th><th>Step</th><th>Owner</th></tr>
      ${(c.steps || []).map((st) => {
        const own = (st.owners || []).join(", ");
        return `<tr class="${st.mine ? "pcard-mine" : ""}">
          <td class="mono">${st.index + 1}</td>
          <td>${st.mine ? "<b>" : ""}${esc(st.description || st.mnemonic || st.type)}${st.mine ? "</b>" : ""}</td>
          <td class="sub">${esc(own || "—")}</td></tr>`;
      }).join("")}</table>
      <div class="sub" style="margin-top:6px">Highlighted steps are attributed by
        commanding authority (func id). Unhighlighted steps are calls, checks, or
        commands any position may send — not necessarily someone else's.</div>` : "";
    return `<div class="ds-card" style="border-radius:6px;margin-bottom:6px"><div class="bd" style="padding:8px 10px">
      <a href="#" data-pcard="${esc(c.id)}" style="font-weight:600">${esc(c.id)} — ${esc(c.name)}</a>
      ${mineChip}
      <span class="sub"> ${esc(c.category || "")} · ${(c.steps || []).length} steps</span>
      ${body}</div></div>`;
  }).join("");
}
async function loadManual() {
  try { S.manual = await getJSON("/api/manual"); }
  catch (e) { S.manual = null; }
  renderManual();
}
function manualSectionsForPosition() {
  // FIELD FIX 2026-08-20: the server serves {"pages":[{name,path,...}]} --
  // this read `.sections` and `.title`, neither of which exists, so the TOC
  // was ALWAYS empty ("no sections") and the manual looked absent. Accept the
  // served shape; keep `.sections` tolerated so an old fixture cannot silently
  // resurrect the bug (a test asserts a .sections payload yields NO entries).
  const secs = ((S.manual || {}).pages) || [];
  const pos = S.positions[S.position];
  if (S.position === "flight_director" || !pos ||
      !pos.manual_sections || !pos.manual_sections.length ||
      pos.manual_sections === "all" || pos.manual_sections[0] === "all")
    return secs;
  // Classic's rule: the position's stems, PLUS the two everyone needs.
  const always = ["00_mission_overview", "09_command_reference"];
  return secs.filter((s2) => {
    const stem = String(s2.path || s2.filename || "").replace(/\.md$/, "");
    return always.some((a) => stem.includes(a)) ||
           pos.manual_sections.some((m) => stem.includes(m));
  });
}
function manualTitle(s2) { return s2.name || s2.title || s2.path || "(untitled)"; }
function renderManual() {
  const host = $("man-toc");
  if (!host) return;
  const f = ($("man-search") ? $("man-search").value : "").toLowerCase();
  const all = manualSectionsForPosition();
  const secs = all.filter((s2) => {
    if (!f) return true;
    if (manualTitle(s2).toLowerCase().includes(f)) return true;
    const body = S.manualBodies[String(s2.path || "")];   // body-text search
    return body ? body.toLowerCase().includes(f) : false;
  });
  const cur = S.manualOpen || "";
  host.innerHTML = secs.map((s2) => {
    const path = String(s2.path || s2.filename || "");
    const on = path === cur ? ' style="font-weight:700"' : "";
    return `<div class="man-toc-item"><a href="#" data-man="${esc(path)}"${on}>` +
           `${esc(manualTitle(s2))}</a></div>`;
  }).join("") || `<div class="logrow nom">no sections</div>`;
  setText("man-count", `${secs.length}/${all.length}`);
}
async function openManualSection(path) {
  // FIELD FIX 2026-08-20: this took .text() of a JSON response and rendered the
  // envelope as markdown, and encodeURIComponent() destroyed the slashes in a
  // nested path. Decode properly, encode per segment.
  try {
    const url = "/api/manual/" +
      String(path).split("/").map(encodeURIComponent).join("/");
    const data = await (await fetch(url)).json();
    const md = data.content_md || "";
    S.manualOpen = path;
    S.manualBodies[path] = md;          // cache for body-text search
    setText("man-title", data.title || path);
    setHTML("man-body", renderMarkdown(md));
    renderManual();                      // refresh active-item highlight
  } catch (e) { setHTML("man-body", "section unavailable"); }
}

/* ═══ 17. DRAWER — bottom log drawer ══════════════════════════════════════ */

let bdTab = "events";
let bdOpen = false;
const logLock = { events: false, tm: false };   // scroll lock per pane

function packetRowText(pkt, ts) {
  const sid = pkt.hk_decoded ? pkt.hk_decoded.sid : "";
  const rid = pkt.event ? pkt.event.event_id : "";
  return `${ts}  TM(${pkt.service},${pkt.subtype})  ` +
         `${sid !== "" ? "SID " + sid : rid !== "" ? "RID " + hex4(rid) : ""}  ` +
         `${pkt.length !== undefined ? pkt.length + " B" : ""}`;
}
function eventText(evt) {
  return `${hex4(evt.event_id)} ${evt.name || ""} — ${evt.description || ""}`.trim();
}
function pushEvent(e) {
  S.events.unshift(e);
  if (S.events.length > RING) S.events.length = RING;
}
function routeEvent(msg, eventId) {
  const id = Number(eventId);
  if (!Number.isFinite(id)) return;
  for (const [sub, [lo, hi]] of Object.entries(EVENT_SUBSYSTEM_RANGES)) {
    if (id >= lo && id <= hi) {
      const buf = S.subsysEvents[sub];
      buf.push(msg);
      if (buf.length > 100) buf.shift();
      return;
    }
  }
}
function eventPassesFilter(e) {
  const f = $("evt-sev-filter") ? $("evt-sev-filter").value : "all";
  if (f === "all") return true;
  if (f === "warnplus") return e.sev === "warn" || e.sev === "alarm";
  return e.sev === f;
}
export function renderBottomDrawer() {
  if (!bdOpen) return;
  if (bdTab === "events") {
    const rows = S.events.filter(eventPassesFilter).slice(0, 120);
    setHTML("bdp-events",
      (logLock.events ? `<span class="jump-chip" id="events-jump">${S.events.length} rows · jump to latest</span>` : "") +
      rows.map((e) => `<div class="logrow ${e.sev}">${esc(e.ts)} [${esc(e.label)}] ${esc(e.msg)}` +
        `${e.instructor && S.timeMode !== "real" ? ` <span class="ds-badge">INSTRUCTOR</span>` : ""}</div>`).join(""));
  } else if (bdTab === "tm") {
    setHTML("bdp-tm", S.tmLog.slice(0, 120).map((r) => `<div class="logrow">${esc(r)}</div>`).join(""));
  } else if (bdTab === "cmdhist") {
    setHTML("bdp-cmdhist", S.cmdHistory.slice(0, 120).map(cmdHistoryRow).join("") ||
      `<div class="logrow nom">no commands this session</div>`);
  } else if (bdTab === "conlog") {
    renderConsoleLog();
  } else if (bdTab === "handover") {
    setHTML("handover-list", S.handover.map((h) =>
      `<div class="logrow">${esc(h.ts || "")} [${esc(h.position || "")}] ${esc(h.text || h.note || "")}</div>`).join("") ||
      `<div class="logrow nom">no handover notes</div>`);
  } else if (bdTab === "gonogo") {
    renderGoNoGo();
  }
}
function selectBdTab(id, open = true) {
  // `open=false` selects the tab without expanding — used at init so the
  // drawer boots COLLAPSED to its 24 px strip (chrome budget, plan §2.1;
  // Agent 5 screenshot fix: it used to boot expanded and cost the work
  // area ~170 px at 1366×768).
  bdTab = id;
  if (open) {
    bdOpen = true;
    $("bottom-drawer").classList.add("open");
    $("bd-toggle").setAttribute("aria-expanded", "true");
  }
  for (const t of ["events", "tm", "cmdhist", "conlog", "handover", "gonogo"]) {
    show(`bdp-${t}`, t === id);
    const b = document.querySelector ? document.querySelector(`[data-bd="${t}"]`) : null;
    if (b) b.setAttribute("aria-selected", t === id ? "true" : "false");
  }
  renderBottomDrawer();
}
async function loadHandover() {
  try { S.handover = (await getJSON("/api/handover")).notes || []; }
  catch (e) { /* keep */ }
  renderBottomDrawer();
}
async function addHandoverNote() {
  const el = $("handover-input");
  const text = el ? el.value.trim() : "";
  if (!text) return;
  await postJSON("/api/handover", { text, position: S.position });
  if (el) el.value = "";
  loadHandover();
}
function handleGoNoGo(msg) {
  S.goNoGo = msg;
  if (msg.type === "go_nogo_poll") {
    selectBdTab("gonogo");
    announce(`GO/NO-GO poll: ${msg.label || ""} — respond from the drawer`);
  }
  renderGoNoGo();
}
export function renderGoNoGo() {
  const g = S.goNoGo;
  if (!g) { setHTML("gonogo-status", `<div class="logrow nom">no active poll</div>`); return; }
  const resp = g.responses || {};
  // G7: an open poll carries its call-matrix row — who calls, who must
  // respond, and the FD criticality note — beside the response buttons.
  const matrixHtml = g.active ? renderGonogoMatrixRow(g.label) : "";
  setHTML("gonogo-status",
    `<div style="font-size:13px;margin:4px 0"><b>${esc(g.label || "GO/NO-GO")}</b>` +
    ` — ${g.active ? "OPEN" : (g.result ? `RESULT: ${esc(g.result)}` : "closed")}</div>` +
    Object.entries(resp).map(([pos, r]) => renderChip({
      kind: r === "GO" ? "ok" : r === "NOGO" ? "alarm" : "warn",
      label: `${(POSITION_INFO[pos] || {}).title || pos}: ${r}` })).join(" ") +
    matrixHtml);
}
async function goNoGoRespond(response) {
  await postJSON("/api/go-nogo/respond", { position: S.position, response });
}
async function goNoGoStart() {
  await postJSON("/api/go-nogo/poll", { label: ($("gonogo-label") || {}).value || "GO/NO-GO" });
}

/* ═══ 18. ALARMS — docked drawer + causal chain ═══════════════════════════ */

const ALARM_FILTER_KEY = "mcs.alarmFilters";
function restoreAlarmFilters() {
  try {
    const f = JSON.parse(localStorage.getItem(ALARM_FILTER_KEY) || "{}");
    if ($("alarm-f-sub") && f.sub !== undefined) $("alarm-f-sub").value = f.sub;
    if ($("alarm-f-sev") && f.sev !== undefined) $("alarm-f-sev").value = f.sev;
  } catch (e) { /* corrupted storage */ }
}
function persistAlarmFilters() {
  try {
    localStorage.setItem(ALARM_FILTER_KEY, JSON.stringify({
      sub: $("alarm-f-sub") ? $("alarm-f-sub").value : "",
      sev: $("alarm-f-sev") ? $("alarm-f-sev").value : "0" }));
  } catch (e) { /* private mode */ }
}
export function filteredAlarms() {
  const sub = $("alarm-f-sub") ? $("alarm-f-sub").value : "";
  const sevMin = Number($("alarm-f-sev") ? $("alarm-f-sev").value : 0);
  return S.alarms.filter((a) =>
    (!sub || a.subsystem === sub) && (!sevMin || Number(a.severity) >= sevMin));
}
function alarmRowHtml(a) {
  const html = renderAlarmRow({ ...a, utc: a.utc || fmtUTC(a.timestamp) });
  // append the causal-chain link inside the action cell
  return html.replace(`</span></div>`,
    `<button class="ds-btn" data-chain="${esc(String(a.id))}" title="causal chain (P9)">⛓</button></span></div>`);
}
export function renderAlarmDrawer() {
  // G6: when ≥4 active alarms share fault-graph ancestry, collapse the
  // cascade to its first-out root; the consequents expand on demand.
  const { groups, rest } = groupAlarmsByAncestry(filteredAlarms(), S.faultGraph);
  const semantics = (S.faultGraph && S.faultGraph.edge_semantics) ||
    "edges are inferred fault-to-fault ancestry from fdir/fault_propagation.yaml";
  const groupHtml = groups.map((g) => {
    const gid = String(g.root.id);
    const open = !!S.alarmGroupsOpen[gid];
    return `<div class="alarm-group">
      <div class="alarm-group-hd">
        <b>FIRST-OUT ROOT</b> ${esc(g.rootNode.id)}
        <span> + ${g.consequents.length} consequent</span>
        <button class="ds-btn" data-group="${esc(gid)}" aria-expanded="${open}">
          ${open ? "collapse" : "expand"}</button>
        <div class="alarm-group-note" title="${esc(semantics)}">grouping is INFERRED from the
          ground fault-propagation model, not telemetered — verify with the S5 event order</div>
      </div>
      ${alarmRowHtml(g.root)}
      ${open ? `<div class="alarm-group-kids">${g.consequents.map(alarmRowHtml).join("")}</div>` : ""}
    </div>`;
  }).join("");
  const rows = rest.map(alarmRowHtml).join("");
  setHTML("alarm-list", groupHtml + rows ||
    `<div class="logrow nom" style="padding:14px">no alarms match the filters</div>`);
  updateRailHealth();
}
export function toggleAlarmDrawer(open) {
  const d = $("alarm-drawer");
  if (!d) return;
  const on = open === undefined ? !d.classList.contains("open") : open;
  d.classList.toggle("open", on);
  if (on) {
    renderAlarmDrawer();
    // focus trap entry: focus the first control
    const first = d.querySelector ? d.querySelector("select,button") : null;
    if (first) first.focus();
  }
}
export async function ackAlarm(id) {
  const a = S.alarms.find((x) => String(x.id) === String(id));
  if (a) a.acknowledged = true;
  renderAlarmDrawer();
  updateSaHeader();
  await postJSON(`/api/alarms/${encodeURIComponent(id)}/ack`, { position: S.position });
}
export async function ackAllAlarms() {
  for (const a of filteredAlarms()) {
    if (!a.acknowledged) await ackAlarm(a.id);
  }
}
function ackFirstUnacked() {
  const a = filteredAlarms().find((x) => !x.acknowledged);
  if (a) { ackAlarm(a.id); announce(`acknowledged ${a.subsystem || ""} ${a.message || a.id}`); }
}
async function loadAlarms() {
  try { S.alarms = (await getJSON("/api/alarms")).alarms || []; }
  catch (e) { /* keep client copy */ }
  renderAlarmDrawer();
  updateSaHeader();
}
function openProcedureFromAlarm(procPath) {
  showPage("proc");
  const sel = $("proc-source");
  if (sel) { sel.value = "ops"; loadProcSources().then(() => {
    const t = $("proc-type");
    if (t) { t.value = procPath; loadProcDoc(); }
  }); }
}

/* Causal chain (P9, Mockup C spine): alarm → parameter → limit → recent
 * commands → S5 events, assembled client-side. */
export async function openCausalChain(alarmId) {
  const a = S.alarms.find((x) => String(x.id) === String(alarmId));
  if (!a) return;
  toggleChain(true);
  const hexId = a.param_id !== undefined ? hex4(a.param_id) : null;
  const def = hexId ? S.paramByHex[hexId] : null;
  let limitsHtml = `<i>no ground limit involved</i>`;
  try {
    const lim = await getJSON("/api/limits");
    const band = hexId ? lim.limits[hexId] : null;
    if (band) limitsHtml = renderDeviationBar(Number(a.value), band) +
      `<div class="mono" style="font-size:12px;margin-top:14px">yellow ${num(band.yellow_low, 2)}…${num(band.yellow_high, 2)}` +
      ` · red ${num(band.red_low, 2)}…${num(band.red_high, 2)} ${esc(band.units || "")}</div>`;
  } catch (e) { limitsHtml = `<i>limits unavailable</i>`; }
  const end = simNowSec(), start = end - 1800;
  let cmdsHtml = "", evtsHtml = "";
  try {
    const c = await getJSON(`/api/archive/commands?start=${start}&end=${end}`);
    cmdsHtml = (c.commands || c.rows || []).slice(-8).reverse().map((r) =>
      `<div class="logrow">${fmtUTC(r.timestamp)} ${esc(r.name || `S${r.service},${r.subtype}`)} — ${esc(r.verdict || r.status || "")}</div>`).join("") ||
      `<div class="logrow nom">no commands in the last 30 min</div>`;
  } catch (e) { cmdsHtml = `<div class="logrow nom">command archive unavailable</div>`; }
  try {
    const ev = await getJSON(`/api/archive/events?start=${start}&end=${end}`);
    evtsHtml = (ev.events || ev.rows || []).slice(-8).reverse().map((r) =>
      renderEventRow({ ...r, utc: fmtUTC(r.timestamp) })).join("") ||
      `<div class="logrow nom">no S5 events in the last 30 min</div>`;
  } catch (e) { evtsHtml = `<div class="logrow nom">event archive unavailable</div>`; }
  setHTML("chain-body", `
    ${renderStepper(["COMMANDS (30 min)", "S5 EVENTS", "PARAMETER", "GROUND LIMIT", "ALARM"], 4)}
    <div class="g" style="grid-template-columns:1fr 1fr;margin-top:12px">
      <div class="ds-card"><h2>1 · Recent commands</h2><div class="bd">${cmdsHtml}</div></div>
      <div class="ds-card"><h2>2 · S5 events</h2><div class="bd">${evtsHtml}</div></div>
      <div class="ds-card"><h2>3 · Parameter</h2><div class="bd">
        ${def ? paramTriad(hexId) : `<i>no parameter attached to this alarm</i>`}</div></div>
      <div class="ds-card"><h2>4 · Ground limit</h2><div class="bd">${limitsHtml}</div></div>
    </div>
    <div class="ds-card" style="margin-top:10px"><h2>5 · The alarm</h2><div class="bd">
      ${renderAlarmRow({ ...a, utc: a.utc || fmtUTC(a.timestamp) })}
      <p style="font-size:12.5px;color:var(--tx2);margin-top:8px">Read the chain backwards:
      did a command (1) or an on-board event (2) move the parameter (3) across the ground
      limit (4) to raise this alarm (5)? This is the mental model the console teaches (P9).</p>
    </div></div>`);
}
function toggleChain(open) {
  const o = $("chain-overlay");
  if (o) o.classList.toggle("open", open === undefined ? !o.classList.contains("open") : open);
}
function toggleHelp(open) {
  const o = $("help-overlay");
  if (o) o.classList.toggle("open", open === undefined ? !o.classList.contains("open") : open);
}

/* Focus trap for the alarm drawer (B3): Tab cycles inside while open. */
function initFocusTrap() {
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const d = $("alarm-drawer");
    if (!d || !d.classList.contains("open") || !d.querySelectorAll) return;
    if (!d.contains(document.activeElement)) return;
    const els = Array.from(d.querySelectorAll("button,select,a,[tabindex]"))
      .filter((x) => !x.hidden && !x.disabled);
    if (!els.length) return;
    const first = els[0], last = els[els.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
}

/* ═══ 19. GUIDED — teaching layer ═════════════════════════════════════════ */

async function loadAnnotations() {
  try { S.annotations = await getJSON("/api/annotations"); }
  catch (e) { S.annotations = {}; }
  applyAnnotations();
}
export function annotationText(key) {
  // key "page.element" -> annotations[page][element] (string or {n, text})
  const [page, ...rest] = String(key).split(".");
  const el = rest.join(".");
  const pageMap = S.annotations[page];
  if (!pageMap) return null;
  const v = pageMap[el];
  if (v === undefined || v === null) return null;
  if (typeof v === "string") return { n: null, text: v };
  return { n: v.n ?? null, text: v.text || "" };
}
function applyAnnotations() {
  if (!document.querySelectorAll) return;
  document.querySelectorAll("[data-ann]").forEach((el) => {
    const a = annotationText(el.dataset.ann);
    if (!a) { el.innerHTML = ""; return; }
    el.innerHTML = (a.n !== null ? `<b class="n">${esc(String(a.n))}</b>` : "") + esc(a.text);
  });
}
function setGuided(on) {
  document.documentElement.setAttribute("data-guided", on ? "1" : "0");
  const b = $("guided-btn");
  if (b) b.setAttribute("aria-pressed", on ? "true" : "false");
  try { localStorage.setItem("mcs.guided", on ? "1" : "0"); } catch (e) { /* */ }
  if (on) applyAnnotations();
}
function setGreyscale(on) {
  document.documentElement.setAttribute("data-greyscale", on ? "1" : "0");
  const b = $("grey-btn");
  if (b) b.setAttribute("aria-pressed", on ? "true" : "false");
}
function setTheme(light) {
  document.documentElement.setAttribute("data-theme", light ? "light" : "dark");
  const b = $("theme-btn");
  if (b) b.setAttribute("aria-pressed", light ? "true" : "false");
  try { localStorage.setItem("mcs.theme", light ? "light" : "dark"); } catch (e) { /* */ }
}

/* ═══ 20. BOOT ════════════════════════════════════════════════════════════ */

/* S15 dump playback shared by the CMD panel and all seven L2 containers. */
const dumpState = {};   // sub -> {rows, chart}
async function requestTmDump(sub, storeId) {
  const r = await postJSON("/api/tm-dump", { subsystem: sub, store_id: storeId || undefined });
  if (!r.ok) { announce(`dump request failed (${r.status})`); return; }
  setTimeout(() => loadDumpData(sub), 1200);
}
async function loadDumpData(sub) {
  try {
    const d = await getJSON(`/api/tm-dump-data?subsystem=${encodeURIComponent(sub)}`);
    const rows = d.rows || d.samples || d.data || [];
    dumpState[sub] = { rows };
    renderDumpChart(sub);
  } catch (e) {
    const pageId = Object.keys(PAGE_SUBSYSTEM).find((k) => PAGE_SUBSYSTEM[k] === sub);
    if (pageId) setText(`${pageId}-dump-status`, "dump data unavailable");
  }
}
function renderDumpChart(sub) {
  const pageId = Object.keys(PAGE_SUBSYSTEM).find((k) => PAGE_SUBSYSTEM[k] === sub);
  if (!pageId) return;
  const st = dumpState[sub];
  const statusEl = `${pageId}-dump-status`;
  if (!st || !st.rows.length) { setText(statusEl, "dump empty"); return; }
  setText(statusEl, `${st.rows.length} samples`);
  const cv = $(`${pageId}-dump-chart`);
  const scrub = $(`${pageId}-dump-scrub`);
  if (typeof Chart === "undefined") {
    setText(statusEl, `${st.rows.length} samples — Chart.js not installed; latest: ` +
      JSON.stringify(st.rows[st.rows.length - 1]).slice(0, 120));
    return;
  }
  if (cv) cv.classList.remove("hide");
  if (scrub) scrub.classList.remove("hide");
  const frac = scrub ? Number(scrub.value) / 100 : 1;
  const upto = Math.max(2, Math.floor(st.rows.length * frac));
  const keys = Object.keys(st.rows[0]).filter((k) => k !== "ts" && k !== "t" &&
    Number.isFinite(Number(st.rows[0][k]))).slice(0, 4);
  const datasets = keys.map((k, i) => ({
    label: k, pointRadius: 0, borderWidth: 1.5,
    borderColor: `hsl(${i * 70},15%,${60 + i * 5}%)`,
    data: st.rows.slice(0, upto).map((r) => ({ x: (r.ts ?? r.t) * 1000, y: Number(r[k]) })) }));
  if (st.chart) st.chart.destroy();
  st.chart = new Chart(cv.getContext("2d"), {
    type: "line", data: { datasets },
    options: { animation: false, scales: { x: { type: "linear",
      ticks: { callback: (v) => fmtUTC(v / 1000) } } } } });
}

async function loadTcCatalog() {
  try {
    const d = await getJSON(`/api/tc-catalog?position=${encodeURIComponent(S.position)}`);
    S.tcCatalog = d.commands || [];
    S.quickCommands = d.quick_commands || [];
  } catch (e) { /* keep old */ }
  renderCmdCatalogSelect();
  renderQuickCommands();
  updateCmdBuilder();
}

function renderCurrentPage(full) {
  if (S.page === "ovr") updateOverview();
  else if (SUB_PAGES.includes(S.page)) {
    updateSubsystemPage(S.page, full);
    if (full && S.page === "ttc") loadLinkPrediction();   // G5 warm fetch
    if (full && S.page === "prp") loadBurnPlan();         // G9 warm fetch
  }
  else if (S.page === "cmd") { if (full) { renderCmdCatalogSelect(); updateCmdBuilder(); renderQuickCommands(); renderVerifLog(); } }
  else if (S.page === "diag") { if (full) { renderDiag(); loadFdir(); } else refreshDiagLive(); }
  else if (S.page === "fdir") { if (full) loadFdir(); else renderFdirPage(); }
  else if (S.page === "proc") { if (full) { loadProcSources(); renderProcStatus(); } }
  else if (S.page === "trnd") { if (full) loadTrndParamNames(); renderBaselineCard(); }
  else if (S.page === "man") { if (full) { loadManual(); loadProcedureCards(); } }
  if (full) applyAnnotations();
}

function buildPages() {
  const host = $("pages");
  if (!host) return;
  host.innerHTML =
    buildOverviewPage() +
    SUB_PAGES.map(buildSubsystemPage).join("") +
    buildCmdPage() + buildFdirPage() + buildProcPage() +
    buildTrndPage() + buildDiagPage() + buildManPage();
}

function initDelegatedEvents() {
  document.addEventListener("click", (e) => {
    const t = e.target.closest ? e.target.closest("[data-page-nav],[data-quick],[data-alarm-filter],[data-dump-request],[data-chain],[data-act],[data-proc],[data-s3],[data-s11],[data-s11-del],[data-s12],[data-s12row],[data-s12add],[data-s19],[data-s19row],[data-s19add],[data-s15st],[data-od-get],[data-man],[data-gonogo],[data-hk],[data-pb-del],[data-diag],[data-bd],[data-blind-toggle],[data-group],[data-conlog]") : null;
    if (!t) return;
    const d = t.dataset;
    if (d.pageNav) return showPage(d.pageNav);
    if (d.blindToggle) return toggleBlindTc();
    if (d.group !== undefined) {
      S.alarmGroupsOpen[d.group] = !S.alarmGroupsOpen[d.group];
      return renderAlarmDrawer();
    }
    if (d.conlog) return void postConsoleEntry(d.conlog);
    if (d.quick) return void sendQuick(d.quick);
    if (d.diag) return selectDiagTab(d.diag);
    if (d.bd) return selectBdTab(d.bd);
    if (d.alarmFilter !== undefined) {
      e.preventDefault();
      const sel = $("alarm-f-sub");
      if (sel) { sel.value = d.alarmFilter; persistAlarmFilters(); }
      return toggleAlarmDrawer(true);
    }
    if (d.dumpRequest) return void requestTmDump(d.dumpRequest);
    if (d.chain) return void openCausalChain(d.chain);
    if (d.act === "ack" && d.id !== undefined) return void ackAlarm(d.id);
    if (d.act === "procedure") return openProcedureFromAlarm(d.proc);
    if (d.proc) return void procAction(d.proc);
    if (d.s3) return void s3Command(d.s3, Number(d.sid));
    if (d.s11) return void s11Command(d.s11);
    if (d.s11Del !== undefined) return void s11Delete(d.s11Del);
    if (d.s12) return void s12Command(d.s12);
    if (d.s12row) return void s12RowCommand(d.s12row, d.mid);
    if (d.s12add) return void s12Add(d.s12add);
    if (d.s19) return void sendPus({ service: 19, subtype: 8, data_hex: "",
      name: "S19_REPORT", position: S.position });
    if (d.s19row) return void s19RowCommand(d.s19row, d.eid);
    if (d.s19add) return void s19Add(d.s19add);
    if (d.s15st) return void s15StoreCommand(d.s15st, d.store);
    if (d.odGet) return void onDemandGet(d.odGet);
    if (d.man !== undefined) { e.preventDefault(); return void openManualSection(d.man); }
    if (d.gonogo) return void goNoGoRespond(d.gonogo);
    if (d.hk) {
      const sid = Number($("hk-sid").value) || 1;
      const map = { enable: [3, 5], disable: [3, 6], oneshot: [3, 27] };
      const [svc, sub] = map[d.hk];
      return void sendPus({ service: svc, subtype: sub,
        data_hex: sidToHex(sid), name: `HK_${d.hk.toUpperCase()}_SID${sid}`, position: S.position })
        .then(() => setText("hk-quick-result", `S3 ${d.hk} SID ${sid} sent`));
    }
    if (d.pbDel !== undefined) { pb.steps.splice(Number(d.pbDel), 1); return renderPbSteps(); }
  });
  document.addEventListener("input", (e) => {
    const t = e.target;
    if (!t || !t.id) {
      if (t && t.dataset && t.dataset.s12filter !== undefined) { s12Filter = t.value; renderDiag(); if (S.page === "fdir") renderFdirPage(); }
      // typed command-builder fields: live re-pack on every keystroke
      if (t && t.dataset && (t.dataset.cmdField !== undefined || t.dataset.cmdBit !== undefined)) cmdLivePack();
      return;
    }
    if (t.id === "cmd-data") cmdReverseDecode();
    else if (t.id === "s11-time" || t.id === "s11-mode") s11UpdatePreview();
    else if (t.id === "s9-set") s9UpdatePreview();
    else if (t.id === "trnd-filter") loadTrndParamNames();
    else if (t.id === "man-search") renderManual();
    else if (t.id === "pcard-search") preservingUi("pcard-list", renderProcedureCards);
    // renderParamsPanel() replaces #diag-params, which CONTAINS this input --
    // without preservingUi the element is destroyed on the first keystroke and
    // every later character goes nowhere.
    else if (t.id === "params-filter") { paramsFilter = t.value; preservingUi("diag-params", renderParamsPanel); }
    else if (t.id === "hdr-param-search") { paramsFilter = t.value; selectDiagTab("params"); }
    else if (t.id === "s6-patch") setText("s6-localcrc", `local CRC16 0x${crc16Ccitt(t.value).toString(16).toUpperCase().padStart(4, "0")}`);
  });
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (!t || !t.id) {
      if (t && t.dataset && t.dataset.s12ool !== undefined) { s12OolOnly = t.checked; renderDiag(); if (S.page === "fdir") renderFdirPage(); }
      if (t && t.dataset && t.dataset.fdirJ) { fdirJournalFilter[t.dataset.fdirJ] = t.checked; renderFdirJournal(); }
      // enum dropdowns / bitfield checkboxes in the command builder
      if (t && t.dataset && (t.dataset.cmdField !== undefined || t.dataset.cmdBit !== undefined)) cmdLivePack();
      return;
    }
    if (t.id === "pos-select") setPosition(t.value);
    else if (t.id === "pcard-mine-only") preservingUi("pcard-list", renderProcedureCards);
    else if (t.id === "cmd-catalog") { S.armed = {}; updateCmdBuilder(); }
    else if (t.id === "alarm-f-sub" || t.id === "alarm-f-sev") { persistAlarmFilters(); renderAlarmDrawer(); }
    else if (t.id === "evt-sev-filter") renderBottomDrawer();
    else if (t.id === "conlog-kind") { S.conlogKind = t.value; renderConsoleLog(); }
    else if (t.id === "s11-cmd") {
      const cmd = S.tcCatalog.find((c) => c.name === t.value);
      if (cmd) {
        if ($("s11-svc")) $("s11-svc").value = `${cmd.service},${cmd.subtype}`;
        if ($("s11-data")) $("s11-data").value = encodeCatalogCommand(cmd, {});
      }
    }
    else if (t.id === "proc-source") loadProcSources();
    else if (t.id === "proc-type") loadProcDoc();
    else if (t.id === "pb-step-type") setHTML("pb-step-fields", pbFieldMarkup());
    else if (t.id === "pb-saved" && t.value) {
      getJSON("/api/procedure/custom").then((d) => {
        const p = (d.procedures || []).find((x) => String(x.id || x.name) === t.value);
        if (p) { pb.steps = p.steps || []; if ($("pb-name")) $("pb-name").value = p.name || ""; renderPbSteps(); }
      });
    }
    else if (t.id && t.id.endsWith("-dump-scrub")) {
      const pageId = t.id.replace("-dump-scrub", "");
      renderDumpChart(PAGE_SUBSYSTEM[pageId]);
    }
    else if (t.id === "s6-file" && t.files && t.files[0]) {
      t.files[0].arrayBuffer().then((buf) => {
        const bytes = Array.from(new Uint8Array(buf));
        const hexStr = bytes.map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ");
        if ($("s6-patch")) $("s6-patch").value = hexStr;
        setText("s6-localcrc", `local CRC16 0x${crc16Ccitt(hexStr).toString(16).toUpperCase().padStart(4, "0")}`);
      });
    }
  });
  document.addEventListener("click", (e) => {
    const a = e.target && e.target.closest ? e.target.closest("[data-pcard]") : null;
    if (!a) return;
    e.preventDefault();
    const id = a.dataset.pcard;
    S.procCardOpen = (S.procCardOpen === id) ? "" : id;
    preservingUi("pcard-list", renderProcedureCards);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    if (e.target && e.target.id === "cmd-data") { e.preventDefault(); sendCommand(); }
    // keyboard flow: Tab through the typed fields, Enter sends (the hex is
    // already live-packed); critical commands still require ARM first.
    if (e.target && e.target.dataset && e.target.dataset.cmdField !== undefined &&
        e.target.tagName !== "SELECT") { e.preventDefault(); sendCommand(); }
    if (e.target && e.target.id === "handover-input") { e.preventDefault(); addHandoverNote(); }
    if (e.target && e.target.id === "conlog-input") { e.preventDefault(); postConsoleEntry("note"); }
  });
  // Button bindings by id, resolved through DELEGATION rather than by binding a
  // listener to the element at init.
  //
  // Field report 2026-08-20: sixteen PUS-panel controls (s3-en-all, s6-dump,
  // s9-report, s11-add, params-refresh, ...) posted nothing at all. The reason
  // was that this helper used to be
  //     const on = (id, fn) => { const el = $(id); if (el) el.addEventListener(...) };
  // and it runs once at init — but the S3/S6/S9/S11/params panels are rendered
  // LAZILY when their diag tab is first clicked. At init those elements do not
  // exist, `if (el)` silently skipped them, and no listener was ever attached.
  // Every control that did work used data-* delegation. So: same call sites,
  // same ids, but the handler is looked up on the click instead.
  const _clickById = new Map();
  const on = (id, fn) => { _clickById.set(id, fn); };
  document.addEventListener("click", (e) => {
    let el = e.target;
    while (el && el !== document) {
      if (el.id && _clickById.has(el.id)) { _clickById.get(el.id)(e); return; }
      el = el.parentElement;
    }
  });
  on("alarms-btn", () => toggleAlarmDrawer(true));
  on("alarm-close", () => toggleAlarmDrawer(false));
  on("alarm-ack-all", () => ackAllAlarms());
  on("guided-btn", () => setGuided(document.documentElement.getAttribute("data-guided") !== "1"));
  on("grey-btn", () => setGreyscale(document.documentElement.getAttribute("data-greyscale") !== "1"));
  on("theme-btn", () => setTheme(document.documentElement.getAttribute("data-theme") !== "light"));
  on("help-btn", () => toggleHelp());
  on("help-close", () => toggleHelp(false));
  on("chain-close", () => toggleChain(false));
  on("bd-toggle", () => {
    bdOpen = !bdOpen;
    $("bottom-drawer").classList.toggle("open", bdOpen);
    $("bd-toggle").setAttribute("aria-expanded", bdOpen ? "true" : "false");
    renderBottomDrawer();
  });
  on("handover-add", addHandoverNote);
  on("gonogo-start", goNoGoStart);
  on("cmd-send", sendCommand);
  on("cmd-arm", () => {
    const cmd = currentCatalogCmd();
    if (!cmd) return;
    S.armed[cmd.name] = !S.armed[cmd.name];
    $("cmd-arm").classList.toggle("armed", !!S.armed[cmd.name]);
    updateCmdBuilder();
    if (S.armed[cmd.name]) announce(`${cmd.name} ARMED — SEND now executes`);
  });
  on("cmd-pack", () => cmdLivePack());
  on("cmd-decode", () => cmdReverseDecode());
  on("s15-dump", () => requestTmDump($("s15-target").value, $("s15-store").value)
    .then(() => setText("s15-result", `dump requested for ${$("s15-target").value}`)));
  on("s3-req-all", () => s3All("oneshot"));
  on("s3-en-all", () => s3All("enable"));
  on("s3-dis-all", () => s3All("disable"));
  on("s3-reload", () => loadHkStructures().then(renderS3Panel));
  on("s11-now", () => { if ($("s11-mode").value === "rel") $("s11-time").value = "0";
    else $("s11-time").value = fmtUTC(simNow(), { date: true }).replace(" ", "T"); s11UpdatePreview(); });
  on("s11-add", s11Add);
  on("s6-dump", () => s6Command(5));
  on("s6-crc", () => s6Command(9));
  on("s6-load", () => {
    const patch = String($("s6-patch").value || "").replace(/[\s,]+/g, "").replace(/(..)/g, "$1 ").trim();
    s6Command(2, patch);
  });
  on("s6-verify", () => s6Command(9));
  on("s6-readback", () => s6Command(5));
  on("s9-report", () => sendPus({ service: 9, subtype: 2, data_hex: "",
    name: "S9_REPORT", position: S.position }));
  on("s9-sync", () => s9SetTime(""));
  on("s9-set-now", () => { $("s9-set").value = fmtUTC(simNow(), { date: true }).replace(" ", "T"); s9UpdatePreview(); });
  on("s9-set-send", () => s9SetTime($("s9-set").value));
  on("fdir-refresh-all", () => {
    sendPus({ service: 12, subtype: 12, data_hex: "", name: "S12_REPORT", position: S.position });
    sendPus({ service: 19, subtype: 8, data_hex: "", name: "S19_REPORT", position: S.position });
    loadFdir();
  });
  on("fdir-ack-all", ackAllAlarms);
  on("proc-ov-send", async () => {
    const r = await postJSON("/api/procedure/override-command", {
      service: Number($("proc-ov-svc").value), subtype: Number($("proc-ov-sub").value),
      data: $("proc-ov-data").value, position: S.position });
    setText("proc-ov-result", r.ok ? "override sent" : `override rejected (${r.status})`);
  });
  on("pb-add", pbAddStep);
  on("pb-save", pbSave);
  on("pb-run", pbRun);
  on("pb-clear", () => { pb.steps = []; renderPbSteps(); });
  on("trnd-refresh", refreshTrend);
  on("trnd-csv", exportTrendCsv);
  on("trnd-playback", loadSubsystemPlayback);
  on("params-refresh", () => renderParamsPanel());
}
async function s3All(action) {
  const structures = ((S.hkStructures || {}).structures) || [];
  for (const s2 of structures) await s3Command(action, s2.sid);
}
async function s12Command(action) {
  const map = { refresh: [12, 12], enable: [12, 1], disable: [12, 2] };
  const [svc, sub] = map[action];
  await sendPus({ service: svc, subtype: sub, data_hex: "",
    name: `S12_${action.toUpperCase()}`, position: S.position });
  if (action === "refresh") setTimeout(loadFdir, 800);
}
async function s12RowCommand(action, monId) {
  const map = { enable: 3, disable: 4, delete: 6 };
  const data = (Number(monId) & 0xFF).toString(16).toUpperCase().padStart(2, "0");
  await sendPus({ service: 12, subtype: map[action], data_hex: data,
    name: `S12_${action.toUpperCase()}_${monId}`, position: S.position });
  setTimeout(loadFdir, 800);
}
async function s12Add(mount) {
  const mid = Number($(`s12-add-mid-${mount}`).value) & 0xFF;
  const pid = parseInt($(`s12-add-pid-${mount}`).value, 16) >>> 0;
  const type = Number($(`s12-add-type-${mount}`).value) & 0xFF;
  const lo = parseFloat($(`s12-add-low-${mount}`).value);
  const hi = parseFloat($(`s12-add-high-${mount}`).value);
  const f32 = (v) => {
    const b = new ArrayBuffer(4); new DataView(b).setFloat32(0, v, false);
    return Array.from(new Uint8Array(b));
  };
  const bytes = [mid, (pid >> 8) & 255, pid & 255, type, ...f32(lo), ...f32(hi)];
  await sendPus({ service: 12, subtype: 5,
    data_hex: bytes.map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" "),
    name: `S12_ADD_${mid}`, position: S.position });
  setTimeout(loadFdir, 800);
}
async function s19RowCommand(action, eaId) {
  const map = { enable: 4, disable: 5, delete: 2 };
  const data = (Number(eaId) & 0xFF).toString(16).toUpperCase().padStart(2, "0");
  await sendPus({ service: 19, subtype: map[action], data_hex: data,
    name: `S19_${action.toUpperCase()}_${eaId}`, position: S.position });
  setTimeout(loadFdir, 800);
}
async function s19Add(mount) {
  const eid = Number($(`s19-add-eid-${mount}`).value) & 0xFF;
  const kind = Number($(`s19-add-kind-${mount}`).value) & 0xFF;
  const trig = parseInt($(`s19-add-trig-${mount}`).value, 16) >>> 0;
  const action = $(`s19-add-action-${mount}`).value;
  const cmd = S.tcCatalog.find((c) => c.name === action) || {};
  const argHex = encodeCatalogCommand(cmd, {});
  const head = [eid, kind, (trig >> 8) & 255, trig & 255, cmd.service & 255, cmd.subtype & 255]
    .map((b) => b.toString(16).toUpperCase().padStart(2, "0")).join(" ");
  await sendPus({ service: 19, subtype: 1,
    data_hex: head + (argHex ? " " + argHex : ""), name: `S19_ADD_${eid}`, position: S.position });
  setTimeout(loadFdir, 800);
}

async function loadPositions() {
  try {
    S.positions = (await getJSON("/api/positions")).positions || {};
    const sel = $("pos-select");
    if (sel) {
      sel.innerHTML = Object.entries(S.positions).map(([id, p]) =>
        `<option value="${esc(id)}">${esc(p.label || id)}</option>`).join("");
      sel.value = S.position;
    }
  } catch (e) { /* offline */ }
}
async function loadParamCatalog() {
  try {
    const d = await getJSON("/api/param-catalog");
    S.paramCatalog = (d.parameters || []).map((p) => ({
      ...p, hex: p.hex || fmtHex(p.id, 4) }));
    S.paramByHex = {};
    for (const p of S.paramCatalog) S.paramByHex[p.hex] = p;
  } catch (e) { /* offline */ }
}
async function loadLimits() {
  try { S.limits = (await getJSON("/api/limits")).limits || {}; }
  catch (e) { /* offline */ }
}
async function loadContacts() {
  try {
    const d = await getJSON("/api/contacts");
    S.contacts = d.contacts || d.windows || [];
  } catch (e) { /* planner offline */ }
}
async function loadContactSchedule() {
  try {
    const d = await getJSON("/api/displays/contact-schedule");
    S.contactSchedule = d.passes || d.contacts || d.schedule || [];
  } catch (e) { /* offline */ }
}
async function loadGroundStations() {
  try {
    const d = await getJSON("/api/ground-stations");
    S.groundStations = d.stations || d.ground_stations || [];
  } catch (e) { /* offline */ }
}
async function loadFailureCodes() {
  try { S.failureCodes = (await getJSON("/api/failure-codes")).codes || {}; }
  catch (e) { /* offline */ }
}
async function loadVerifLogRest() {
  try {
    const d = await getJSON("/api/verification-log");
    const rows = d.log || d.entries || [];
    if (rows.length) {
      // Row shape (server _VERIF_ROW_DEFAULTS): seq, name ("S17.1"),
      // service, subtype, state (SENT/ACCEPTED/COMPLETED/FAILED/...),
      // error_code, error_text, reason, timestamp, position. (Agent 5 fix:
      // the previous mapping read tc_service/ok/verdict, which this API
      // never emits, rendering "TC(undefined,undefined)".)
      S.verifLog = rows.slice(0, 80).map((v) => ({
        ts: fmtUTC(v.timestamp),
        verdict: `${v.state || "?"}`,
        text: `TC(${v.service},${v.subtype})` +
              (v.name ? ` ${v.name}` : "") +
              (v.error_text ? ` — ${v.error_text}` : "") }));
      renderVerifLog();
    }
  } catch (e) { /* offline */ }
}

async function boot() {
  buildRail();
  buildPages();
  initDelegatedEvents();
  initKeyboard();
  initSchematicNav();
  initFocusTrap();
  setHTML("pb-step-fields", pbFieldMarkup());
  // restore persisted prefs
  try {
    setGuided(localStorage.getItem("mcs.guided") === "1");
    if (localStorage.getItem("mcs.theme") === "light") setTheme(true);
    const pos = localStorage.getItem("mcs.position");
    if (pos) S.position = pos;
  } catch (e) { /* private mode */ }
  restoreAlarmFilters();
  await Promise.all([loadParamCatalog(), loadLimits(), loadPositions()]);
  try { S.clientConfig = await getJSON("/api/client-config"); } catch (e) { S.clientConfig = {}; }
  await Promise.all([loadTcCatalog(), loadSchematics(), loadAnnotations(),
    loadHkStructures(), loadMemoryMap(), loadAlarms(), loadFailureCodes(),
    loadFaultGraph(), loadGonogoMatrix(), loadConsoleLog()]);
  const sel = $("pos-select");
  if (sel) sel.value = S.position;
  connectWS();
  startRestFallback();
  startClocks();
  resolveTimeMode();
  applyPositionFilter();
  showPage("ovr");
  selectBdTab("events", false);   // collapsed 24 px strip on boot
  // polling cadences preserved from the classic console
  poller(loadContactSchedule, 30000).start();       // 30 s pass schedule
  poller(loadContacts, 60000).start();              // 60 s contact windows
  poller(loadVerifLogRest, 5000).start();           // 5 s verification log
  poller(refreshSystemBoard, 5000).start();         // 5 s system overview
  poller(refreshPowerBudget, 5000).start();         // 5 s power budget (EPS page)
  poller(loadAlarms, 10000).start();                // journal reconciliation
  poller(loadHandover, 15000).start();
  poller(async () => { if (S.page === "fdir" || (S.page === "diag" && (diagTab === "s12" || diagTab === "s19"))) await loadFdir(); }, 2000).start();
  poller(resolveTimeMode, 60000).start();
  // POSITION_NEEDS gap surfaces (page-gated inside the loaders)
  poller(loadLinkPrediction, 20000).start();        // G5 — TTC page only
  poller(loadBurnPlan, 30000).start();              // G9 — PROP page only
  poller(loadConsoleLog, 15000).start();            // G8 merged stream
  poller(loadFaultGraph, 120000).start();           // G6 — config changes rarely
}

/* Test hook: under the node harness (globalThis.__MCS_TEST__) nothing runs
 * automatically; the harness drives exported functions directly. */
export const __test = {
  S, adoptServerClock, simNowMs, updateSaHeader, trainingChipState,
  railVisibleForPosition, applyPositionFilter, showPage, buildRail, buildPages,
  renderAlarmDrawer, filteredAlarms, ackAlarm, ackAllAlarms, paramTriad,
  scModeInfo, commandAuthority, encodeCatalogCommand, decodeCatalogArgs,
  sidToHex, crc16Ccitt, s11CucSeconds, tmFreshness, handleState, handleTMPacket,
  handleAlarmMsg, renderBottomDrawer, annotationText, renderS12Block,
  renderS19Block, renderMarkdown, provenanceText, updateTrainingChip,
  loadSchematics, updateRailHealth,
  catFieldError, catFormatEng, isCritical, cmdFieldWidget,
  renderS3Panel, renderS11Table, renderS15Panel, renderS6Panel, renderS9Panel,
  renderParamsPanel, renderFdirPage, selectDiagTab, renderDiag,
  // POSITION_NEEDS gap surfaces (G1-G9, G12, G15)
  derivedEntry, fmtDerivedValue, renderDerivedRow, fitVerdictLine,
  parseBitLegend, renderBitmaskChips,
  captureGateTable, evalCaptureGate, computeCaptureReadiness, renderCaptureCard,
  linkNowReadout, renderLinkOverlay,
  groupAlarmsByAncestry,
  matchGonogoRow, renderGonogoMatrixRow, renderGoNoGo,
  renderConsoleLog,
  plannedBurnDv, burnMismatch, renderBurnCard,
  blindTcState, toggleBlindTc, blindHistoryChip, noteHistory, noteVerification,
  baselineTable, computeBaselineDeltas, renderBaselineCard,
  updateNextCard, renderDerivedCard,
};

if (!globalThis.__MCS_TEST__) {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
}

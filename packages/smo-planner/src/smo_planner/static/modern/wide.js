/* ============================================================================
 * SMO MISSION PLANNER — /wide wall display logic (plan §2.3)
 * ============================================================================
 * NON-INTERACTIVE by construction: this module registers NO user event
 * handlers of any kind — no click, key, hover or focus behaviour. Everything
 * is timer-driven rendering.
 *
 * Cadences:  1 s clock + timeline now-marker redraw
 *           10 s contacts + link (spacecraft-state) + time-source anchor
 *           30 s schedule + power/data budgets
 *          120 s ground track (24 h horizon for eclipse arcs + map)
 *
 * FRESHNESS WATCHDOG (risk R7 — "the wall must never lie"):
 *   - per-block: each data family records the wall time of its last
 *     successful fetch; when that age exceeds 3x the family's cadence the
 *     block gets .stale (hatch, STALE tag) and its numerals render as
 *     em-dashes instead of the last-known values.
 *   - full page: if NO fetch of any kind has succeeded for 120 s, a page-wide
 *     red "DISPLAY STALE" banner appears with the outage age.
 * ============================================================================ */

import { fmtUTC, fmtMET } from "/static/shared/format.js";

const $ = (id) => document.getElementById(id);

/* ── Clock anchor (single time primitive, P2) ─────────────────────────── */
let timeSource = null, simAnchorMs = null, simAnchorWallMs = null;
let simSpeed = 1.0, metAnchorS = null, clockDegraded = false;

function simNow() {
  if (simAnchorMs === null) return Date.now();
  return simAnchorMs + (Date.now() - simAnchorWallMs) * simSpeed;
}

/* ── Data + freshness state ───────────────────────────────────────────── */
let contacts = [];        // {station, aos, los, maxEl, durMin} (ms epochs)
let schedule = [];        // {name, startMs, durMs}
let track = [];           // 24 h ground track points
let scState = null;
let powerBudget = null, dataBudget = null, plannerConfig = null;
let services = null;          // planner.yaml services block (G1/G14) — may be null
let mcsDerived = null;        // MCS /api/state "derived" block, when configured
let lastRollup = null;        // newest completed rfsim pass rollup, when configured
let rollupActive = null;
let apiOk = false;

const CADENCE = { time: 10000, contacts: 10000, link: 10000,
  schedule: 30000, budgets: 30000, track: 120000, mission: 30000 };
const lastOk = { time: 0, contacts: 0, link: 0, schedule: 0, budgets: 0,
  track: 0, mission: 0 };
let lastAnyOk = 0;

/* Cross-origin service URL join (G14): the path literal has NO leading
 * slash — it is relative to the CONFIGURED base (planner.yaml services.*),
 * never a planner route. */
function serviceUrl(base, path) {
  return String(base).replace(/\/+$/, "") + "/" + path;
}

function mark(family) { lastOk[family] = Date.now(); lastAnyOk = Date.now(); }
function isStale(family) {
  return Date.now() - lastOk[family] > 3 * CADENCE[family];
}

/* ── Fetch loops ──────────────────────────────────────────────────────── */
async function fetchTimeSource() {
  try {
    const res = await fetch("/api/time-source");
    if (!res.ok) throw new Error();
    const d = await res.json();
    if (d.time_source === "sim" || d.time_source === "real") timeSource = d.time_source;
    const now = d.now || d.sim_time;
    if (now) { simAnchorMs = new Date(now).getTime(); simAnchorWallMs = Date.now(); }
    if (typeof d.speed === "number") simSpeed = d.speed;
    metAnchorS = (typeof d.met_s === "number") ? d.met_s : null;
    clockDegraded = !!(d.degraded || d.wall_clock_fallback);
    mark("time");
  } catch (e) { /* watchdog handles it */ }
}

async function fetchContacts() {
  try {
    const res = await fetch("/api/contacts");
    if (!res.ok) throw new Error();
    const d = await res.json();
    contacts = (d.contacts || []).map((c) => ({
      station: c.gs_name,
      aos: new Date(c.aos).getTime(), los: new Date(c.los).getTime(),
      maxEl: c.max_elevation_deg,
      durMin: Math.round((c.duration_s || 0) / 60) }));
    apiOk = true; mark("contacts");
  } catch (e) { apiOk = false; }
}

async function fetchLink() {
  try {
    const res = await fetch("/api/spacecraft-state");
    if (!res.ok) throw new Error();
    scState = await res.json();
    mark("link");
  } catch (e) { /* watchdog */ }
}

async function fetchSchedule() {
  try {
    const res = await fetch("/api/schedule");
    if (!res.ok) throw new Error();
    const d = await res.json();
    schedule = (d.schedule || []).map((s) => ({
      name: s.name,
      startMs: new Date(s.start_time).getTime(),
      durMs: (s.duration_s || 300) * 1000,
      dataVolumeMb: Number(s.data_volume_mb) || 0,
      stateName: s.state_name || "PLANNED" }));
    mark("schedule");
  } catch (e) { /* watchdog */ }
}

async function fetchBudgets() {
  let ok = false;
  try {
    const res = await fetch("/api/budget/power");
    if (res.ok) { powerBudget = await res.json(); ok = true; }
  } catch (e) { /* fall through */ }
  try {
    const res = await fetch("/api/budget/data");
    if (res.ok) { dataBudget = await res.json(); ok = true; }
  } catch (e) { /* fall through */ }
  if (ok) mark("budgets");
}

async function fetchTrack() {
  try {
    const hours = (plannerConfig && plannerConfig.horizons
      && plannerConfig.horizons.timeline_hours) || 24;
    const res = await fetch("/api/ground-track?duration_hours=" + hours + "&step_s=120");
    if (!res.ok) throw new Error();
    track = (await res.json()).ground_track || [];
    mark("track");
  } catch (e) { /* watchdog */ }
}

async function fetchConfig() {
  try {
    const res = await fetch("/api/config/planner");
    if (res.ok) {
      const d = await res.json();
      plannerConfig = d.planner_config || null;
      services = (plannerConfig && plannerConfig.services) || null;
    }
  } catch (e) { /* thresholds default */ }
}

/* ── Mission-level derived data (G1 + G14) ────────────────────────────── */
/* Read-only cross-origin fetches to the CONFIGURED MCS / rfsim bases.
 * Unset URLs are the designed absent state: the readouts stay hatched
 * em-dashes and the family is marked fresh (absence is not staleness). */
async function fetchMission() {
  const mcsBase = services && services.mcs_url;
  const rfsimBase = services && services.rfsim_url;
  if (!mcsBase && !rfsimBase) { mcsDerived = null; lastRollup = null;
    rollupActive = null; mark("mission"); return; }
  let ok = false;
  if (mcsBase) {
    try {
      const res = await fetch(serviceUrl(mcsBase, "api/state"));
      if (!res.ok) throw new Error();
      mcsDerived = (await res.json()).derived || null;
      ok = true;
    } catch (e) { mcsDerived = null; }
  }
  if (rfsimBase) {
    try {
      const res = await fetch(serviceUrl(rfsimBase, "api/pass-rollups?limit=5"));
      if (!res.ok) throw new Error();
      const d = await res.json();
      const rollups = d.rollups || [];
      lastRollup = rollups.length ? rollups[rollups.length - 1] : null;
      rollupActive = d.active || null;
      ok = true;
    } catch (e) { lastRollup = null; rollupActive = null; }
  }
  if (ok) mark("mission");
}

/* Planner-side pass-fit fallback: next upcoming dump-scheduled pass,
 * achievable capacity vs backlog, from the planner's own budget machinery
 * (same join as planner.js computeFitVerdicts, reduced to one verdict). */
function plannerPassFit() {
  const d = dataBudget;
  if (!d || !Array.isArray(d.pass_downlink)) return null;
  const now = simNow();
  const passes = d.pass_downlink.map((p) => ({
    ...p, aosMs: new Date(p.aos).getTime(), losMs: new Date(p.los).getTime() }));
  const isDump = (a) => /dump|downlink/i.test(a.name || "");
  const acts = schedule.filter((a) =>
    a.stateName !== "CANCELLED" && a.stateName !== "FAILED");
  const next = passes.filter((p) => p.dump_scheduled && p.losMs >= now)
    .sort((a, b) => a.aosMs - b.aosMs)[0];
  if (!next) return null;
  const gen = acts.filter((a) => !isDump(a) && a.startMs < next.aosMs)
    .reduce((s, a) => s + a.dataVolumeMb, 0);
  const dumped = passes.filter((p) => p.dump_scheduled && p.losMs <= next.aosMs)
    .reduce((s, p) => s + (Number(p.planned_dump_mb) || 0), 0);
  const backlog = Math.max(
    0, (Number(d.onboard_data_mb) || 0) + gen - dumped);
  const cap = Number(next.capacity_mb) || 0;
  return { gs: next.gs_name, backlogMb: backlog, capacityMb: cap,
    ratio: backlog > 0 ? cap / backlog : null,
    fits: backlog <= cap + 1e-6 };
}

function renderMission() {
  const stale = isStale("mission");
  const fitEl = $("wPassFit"), socEl = $("wSocExit"), sub = $("wMissionSub");
  const setVal = (el, text, cls) => {
    el.textContent = text;
    el.className = "v2 num" + (text === "—" ? " absent" : "") + (cls ? " " + cls : "");
  };
  const notes = [];

  // PASS FIT: MCS derived when valid, else the planner's own budget join.
  let fitDone = false;
  const dFit = !stale && mcsDerived && mcsDerived["ttc.pass_fit_ratio"];
  if (dFit && dFit.valid && typeof dFit.value === "number") {
    setVal(fitEl, dFit.value.toFixed(2) + "×",
      dFit.value < 1 ? "alarm" : "");
    notes.push("fit: MCS derived");
    fitDone = true;
  } else if (dFit && !dFit.valid) {
    notes.push("fit invalid: " + (dFit.reason || "?"));
  }
  if (!fitDone) {
    const pf = plannerPassFit();
    if (pf && !isStale("budgets") && !isStale("schedule")) {
      if (pf.backlogMb <= 1e-6) {
        setVal(fitEl, "FIT");
        notes.push("no backlog (" + pf.gs + ")");
      } else if (pf.ratio !== null) {
        setVal(fitEl, pf.ratio.toFixed(2) + "×", pf.fits ? "" : "alarm");
        notes.push("fit: planner budget, next dump pass " + pf.gs);
      }
      fitDone = true;
    }
  }
  if (!fitDone) setVal(fitEl, "—");

  // SoC AT ECLIPSE EXIT: MCS derived only; invalid/unconfigured = em-dash.
  const dSoc = !stale && mcsDerived
    && mcsDerived["eps.soc_at_eclipse_exit_pred"];
  if (dSoc && dSoc.valid && typeof dSoc.value === "number") {
    const minSoc = (plannerConfig && plannerConfig.budget
      && plannerConfig.budget.min_soc_percent) ?? null;
    setVal(socEl, dSoc.value.toFixed(1) + "%",
      minSoc !== null && dSoc.value < minSoc ? "alarm" : "");
    notes.push("SoC: MCS derived");
  } else {
    setVal(socEl, "—");
    if (dSoc && !dSoc.valid) notes.push("SoC invalid: " + (dSoc.reason || "?"));
    else if (!(services && services.mcs_url)) notes.push("no MCS URL configured");
  }
  sub.textContent = notes.join(" · ") || "—";

  // LAST PASS achieved rollup (link block, G14).
  const lp = $("wLastPass");
  if (stale || !lastRollup) {
    lp.textContent = (services && services.rfsim_url)
      ? "—" : "not configured";
  } else {
    const r = lastRollup;
    const lock = r.locked
      ? ((r.lock_delay_s === null || r.lock_delay_s === undefined)
        ? "locked" : "lock +" + Number(r.lock_delay_s).toFixed(1) + " s")
      : "NO LOCK";
    const eb = (r.mean_eb_n0_db === null || r.mean_eb_n0_db === undefined)
      ? "—" : Number(r.mean_eb_n0_db).toFixed(1) + " dB";
    const vol = (r.data_volume_bytes === null
      || r.data_volume_bytes === undefined)
      ? "—" : (Number(r.data_volume_bytes) / 1024).toFixed(0) + " kB";
    lp.textContent = (r.gs_name || "?") + " · " + lock + " · " + eb
      + " · " + vol;
  }
}

/* ── Watchdog (risk R7) ───────────────────────────────────────────────── */
const BLOCK_FAMILY = {
  "block-clock": "time", "block-passes": "contacts",
  "block-timeline": "schedule", "block-track": "track",
  "block-budgets": "budgets", "block-link": "link",
};
const PAGE_STALE_MS = 120000;   // full-page banner after 120 s of silence

function watchdog() {
  for (const [blockId, family] of Object.entries(BLOCK_FAMILY)) {
    $(blockId).classList.toggle("stale", isStale(family));
  }
  const banner = $("staleBanner");
  const silent = Date.now() - lastAnyOk;
  if (lastAnyOk !== 0 && silent > PAGE_STALE_MS) {
    banner.classList.add("on");
    $("staleFor").textContent = Math.floor(silent / 1000) + " s";
  } else if (lastAnyOk === 0 && performance.now() > PAGE_STALE_MS) {
    banner.classList.add("on");
    $("staleFor").textContent = Math.floor(performance.now() / 1000) + " s";
  } else {
    banner.classList.remove("on");
  }
}

/* ── 1. Clock block ───────────────────────────────────────────────────── */
function renderClock() {
  const stale = isStale("time");
  if (stale && lastOk.time === 0) {
    $("wUtc").textContent = "--:--:--"; $("wDate").textContent = "—";
    $("wMet").textContent = "MET —"; $("wClockBadge").textContent = "—";
    return;
  }
  const now = new Date(simNow());
  const months = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
  $("wDate").textContent = String(now.getUTCDate()).padStart(2, "0") + " "
    + months[now.getUTCMonth()] + " " + now.getUTCFullYear() + " UTC";
  $("wUtc").textContent = fmtUTC(now);
  const met = (metAnchorS !== null && simAnchorMs !== null)
    ? metAnchorS + (simNow() - simAnchorMs) / 1000 : null;
  $("wMet").textContent = met === null ? "MET —" : fmtMET(met);
  const badge = $("wClockBadge");
  badge.classList.toggle("wall", clockDegraded);
  badge.textContent = clockDegraded ? "WALL CLOCK"
    : timeSource === "sim" ? "SIM TIME"
    : timeSource === "real" ? "UTC (REAL)" : "—";
  // TRAINING chip: rendered ONLY when the time source is the simulator.
  const chip = $("trainingChip");
  if (timeSource === "sim" && !clockDegraded) {
    chip.classList.add("on");
    chip.textContent = "TRAINING — SIM TIME x" + (simSpeed || 1);
  } else {
    chip.classList.remove("on");
  }
}

/* ── 2. Passes block ──────────────────────────────────────────────────── */
function renderPasses() {
  const stale = isStale("contacts");
  const now = simNow();
  const mmss = (ms) => {
    const t = Math.max(0, ms);
    const h = Math.floor(t / 3600000);
    const m = Math.floor((t % 3600000) / 60000);
    const s = Math.floor((t % 60000) / 1000);
    return (h ? h + ":" : "") + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  };
  if (stale) {
    $("wNextStation").textContent = "—"; $("wCountdown").textContent = "—";
    $("wNextLabel").textContent = "NEXT AOS";
    $("wPassList").innerHTML = "";
    return;
  }
  const active = contacts.find((c) => c.aos <= now && c.los >= now);
  const next = contacts.filter((c) => c.aos > now).sort((a, b) => a.aos - b.aos)[0];
  const cd = $("wCountdown");
  if (active) {
    $("wNextLabel").textContent = "IN CONTACT — LOS";
    $("wNextStation").textContent = active.station;
    cd.textContent = mmss(active.los - now);
    cd.classList.add("los");
  } else if (next) {
    $("wNextLabel").textContent = "NEXT AOS";
    $("wNextStation").textContent = next.station;
    cd.textContent = mmss(next.aos - now);
    cd.classList.remove("los");
  } else {
    $("wNextLabel").textContent = "NEXT AOS";
    $("wNextStation").textContent = "NO CONTACTS";
    cd.textContent = "—";
    cd.classList.remove("los");
  }
  const upcoming = contacts.filter((c) => c.los > now)
    .sort((a, b) => a.aos - b.aos).slice(0, 5);
  $("wPassList").innerHTML =
    "<div class=\"pass head\"><span>STATION</span><span>AOS</span><span>LOS</span><span>MAX EL</span></div>"
    + upcoming.map((c) =>
      "<div class=\"pass\"><span class=\"st\">" + c.station + "</span>"
      + "<span>" + fmtUTC(c.aos, { seconds: false }) + "</span>"
      + "<span>" + fmtUTC(c.los, { seconds: false }) + "</span>"
      + "<span>" + (typeof c.maxEl === "number" ? c.maxEl.toFixed(0) + "°" : "—")
      + "</span></div>").join("");
}

/* ── 3. Plan timeline (24 h canvas) ───────────────────────────────────── */
function eclipseIntervals() {
  const out = [];
  let start = null;
  for (const pt of track) {
    const t = new Date(pt.utc).getTime();
    if (pt.in_eclipse && start === null) start = t;
    if (!pt.in_eclipse && start !== null) { out.push([start, t]); start = null; }
  }
  if (start !== null && track.length) {
    out.push([start, new Date(track[track.length - 1].utc).getTime()]);
  }
  return out;
}

function renderTimeline() {
  const canvas = $("wTimeline");
  const box = canvas.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(80, box.width - 4), h = Math.max(60, box.height - 4);
  canvas.style.width = w + "px"; canvas.style.height = h + "px";
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const C = (n) => css.getPropertyValue(n).trim();
  ctx.fillStyle = C("--bg"); ctx.fillRect(0, 0, w, h);

  const stale = isStale("schedule") && isStale("contacts");
  const mL = 8, mR = 8, mT = 8, mB = 30;
  const pW = w - mL - mR, pH = h - mT - mB;
  const now = simNow();
  const windowMs = 24 * 3600 * 1000;
  const toX = (t) => mL + ((t - now) / windowMs) * pW;

  // Hour grid + labels (>= 18 px wall floor).
  ctx.strokeStyle = C("--line"); ctx.lineWidth = 1;
  ctx.fillStyle = C("--tx3"); ctx.font = "18px " + C("--mono");
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (let hr = 0; hr <= 24; hr += 4) {
    const x = mL + (hr / 24) * pW;
    ctx.beginPath(); ctx.moveTo(x, mT); ctx.lineTo(x, mT + pH); ctx.stroke();
    ctx.fillText("+" + hr + "h", Math.min(x, w - 24), mT + pH + 6);
  }

  if (stale) {
    ctx.fillStyle = C("--tx2"); ctx.font = "24px " + C("--mono");
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("—", w / 2, h / 2);
    return;
  }

  // Eclipse arcs (top band) from the served track.
  const bandEcl = { y: mT, h: pH * 0.16 };
  ctx.fillStyle = "rgba(90,169,255,0.28)";
  for (const [a, b] of eclipseIntervals()) {
    const x1 = Math.max(mL, toX(a)), x2 = Math.min(mL + pW, toX(b));
    if (x2 > x1) ctx.fillRect(x1, bandEcl.y, x2 - x1, bandEcl.h);
  }

  // Contacts band (middle).
  const bandC = { y: mT + pH * 0.22, h: pH * 0.30 };
  for (const c of contacts) {
    if (c.los < now || c.aos > now + windowMs) continue;
    const x1 = Math.max(mL, toX(c.aos)), x2 = Math.min(mL + pW, toX(c.los));
    const active = c.aos <= now && c.los >= now;
    ctx.fillStyle = active ? "rgba(90,169,255,0.55)" : "rgba(138,147,160,0.40)";
    ctx.fillRect(x1, bandC.y, Math.max(x2 - x1, 3), bandC.h);
  }

  // Activities band (bottom).
  const bandA = { y: mT + pH * 0.60, h: pH * 0.30 };
  ctx.fillStyle = "rgba(232,163,23,0.45)";
  for (const a of schedule) {
    const end = a.startMs + a.durMs;
    if (end < now || a.startMs > now + windowMs) continue;
    const x1 = Math.max(mL, toX(a.startMs)), x2 = Math.min(mL + pW, toX(end));
    ctx.fillRect(x1, bandA.y, Math.max(x2 - x1, 3), bandA.h);
  }

  // Band labels.
  ctx.fillStyle = C("--tx3"); ctx.font = "18px " + C("--sans");
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  ctx.fillText("ECLIPSE", mL + 6, bandEcl.y + bandEcl.h / 2);
  ctx.fillText("CONTACTS", mL + 6, bandC.y + bandC.h / 2);
  ctx.fillText("ACTIVITIES", mL + 6, bandA.y + bandA.h / 2);

  // Now-marker (redrawn every second with the whole canvas).
  ctx.strokeStyle = C("--alarm"); ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(mL, mT); ctx.lineTo(mL, mT + pH); ctx.stroke();
}

/* ── 4. Ground-track map (vendor when present, canvas fallback) ───────── */
let map = null, mapTried = false;
let mapTrackLayers = [], mapScMarker = null;

function loadVendorMap() {
  return new Promise((resolve) => {
    const s = document.createElement("script");
    s.src = "/static/vendor/js/svg-world-map.js";
    s.onload = () => resolve(typeof window.SvgWorldMap !== "undefined");
    s.onerror = () => resolve(false);
    document.head.appendChild(s);
  });
}

async function initTrackMap() {
  mapTried = true;
  const ok = await loadVendorMap();
  if (ok) {
    $("wideMap").style.display = "block";
    $("wTrackCanvas").style.display = "none";
    map = new SvgWorldMap("wideMap");
  } else {
    // Graceful vendor-missing state (risk R3): plain-canvas track plot with
    // an explicit note — never a silent empty box.
    $("wideMapFallbackNote").classList.add("on");
  }
}

function renderTrackVendor() {
  if (!map) return;
  for (const l of mapTrackLayers) map.removeLayer(l);
  mapTrackLayers = [];
  if (track.length > 1) {
    let seg = [], segEcl = track[0].in_eclipse;
    const flush = () => {
      if (seg.length > 1) {
        mapTrackLayers.push(map.addPolyline(seg, {
          color: segEcl ? "#5AA9FF" : "#E8A317", weight: 3, opacity: 0.85 }));
      }
    };
    for (let i = 0; i < track.length; i++) {
      const pt = track[i];
      const prev = i > 0 ? track[i - 1] : null;
      if (prev && (Math.abs(pt.lon - prev.lon) > 180 || pt.in_eclipse !== segEcl)
          && seg.length) { flush(); seg = []; }
      segEcl = pt.in_eclipse;
      seg.push([pt.lat, pt.lon]);
    }
    flush();
  }
  if (scState) {
    if (mapScMarker) map.removeLayer(mapScMarker);
    mapScMarker = map.addMarker(scState.lat, scState.lon, {
      color: "#E6E9ED", strokeColor: "#E6E9ED", radius: 6 });
  }
}

function renderTrackCanvas() {
  const canvas = $("wTrackCanvas");
  if (canvas.style.display === "none") { renderTrackVendor(); return; }
  const box = canvas.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(80, box.width), h = Math.max(60, box.height);
  canvas.style.width = w + "px"; canvas.style.height = h + "px";
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const C = (n) => css.getPropertyValue(n).trim();
  ctx.fillStyle = C("--bg"); ctx.fillRect(0, 0, w, h);
  const toXY = (lat, lon) => [((lon + 180) / 360) * w, ((90 - lat) / 180) * h];

  // Graticule every 30 degrees.
  ctx.strokeStyle = C("--line"); ctx.lineWidth = 1;
  for (let lon = -180; lon <= 180; lon += 30) {
    const [x] = toXY(0, lon);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    const [, y] = toXY(lat, 0);
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }

  const stale = isStale("track");
  if (stale) {
    ctx.fillStyle = C("--tx2"); ctx.font = "24px " + C("--mono");
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("—", w / 2, h / 2);
    return;
  }

  // Track segments (colour by eclipse), split at the antimeridian.
  ctx.lineWidth = 3;
  let prev = null;
  for (const pt of track) {
    if (prev && Math.abs(pt.lon - prev.lon) <= 180) {
      ctx.strokeStyle = pt.in_eclipse ? C("--info") : C("--warn");
      const [x1, y1] = toXY(prev.lat, prev.lon);
      const [x2, y2] = toXY(pt.lat, pt.lon);
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    }
    prev = pt;
  }
  // Spacecraft position.
  if (scState && !isStale("link")) {
    const [x, y] = toXY(scState.lat, scState.lon);
    ctx.fillStyle = C("--tx");
    ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.fill();
  }
}

/* ── 5. Budgets block ─────────────────────────────────────────────────── */
function renderBudgets() {
  const stale = isStale("budgets");
  const minSoc = (plannerConfig && plannerConfig.budget
    && plannerConfig.budget.min_soc_percent) ?? null;
  const warnFill = (plannerConfig && plannerConfig.budget
    && plannerConfig.budget.warn_utilization_percent) ?? 90;

  const pv = $("wPowerVal"), pb = $("wPowerBar"), ps = $("wPowerSub");
  if (stale || !powerBudget) {
    pv.textContent = "—"; pv.className = "v num";
    pb.querySelector(".fl").style.width = "0%";
    ps.textContent = stale ? "—" : "waiting for /api/budget/power";
  } else if (powerBudget.computable === false || powerBudget.final_soc == null) {
    // The designed cannot-compute state — never a 0 % / 100 % bar.
    pv.textContent = "N/C"; pv.className = "v num warn";
    pb.querySelector(".fl").style.width = "0%";
    ps.textContent = (powerBudget.reason
      || (powerBudget.warnings || [])[0] || "cannot compute").substring(0, 90);
    ps.className = "reason";
  } else {
    const soc = powerBudget.final_soc;
    const cls = minSoc !== null && soc < minSoc ? "alarm"
      : minSoc !== null && soc < minSoc + 10 ? "warn" : "";
    pv.textContent = soc.toFixed(1) + "%";
    pv.className = "v num " + cls;
    pb.className = "wbar " + (cls === "alarm" ? "a" : cls === "warn" ? "w" : "");
    pb.querySelector(".fl").style.width = Math.max(0, Math.min(100, soc)) + "%";
    pb.innerHTML = "<div class=\"fl\" style=\"width:" + Math.max(0, Math.min(100, soc))
      + "%\"></div>" + (minSoc !== null
        ? "<div class=\"tick\" style=\"left:" + minSoc + "%\"></div>" : "");
    ps.className = "sub";
    ps.textContent = "initial " + (powerBudget.initial_soc ?? 0).toFixed(0) + "%"
      + (minSoc !== null ? " · min " + minSoc + "%" : "")
      + (powerBudget.soc_saturated ? " · SATURATED (limit, not prediction)" : "");
  }

  const dv = $("wDataVal"), db = $("wDataBar"), dsub = $("wDataSub");
  if (stale || !dataBudget || dataBudget.utilization_percent === undefined) {
    dv.textContent = "—"; dv.className = "v num";
    db.querySelector(".fl").style.width = "0%";
    dsub.textContent = stale ? "—" : "waiting for /api/budget/data";
  } else {
    const peak = dataBudget.peak_utilization_percent ?? dataBudget.utilization_percent;
    const cls = peak >= 100 ? "alarm" : peak >= warnFill ? "warn" : "";
    dv.textContent = peak.toFixed(1) + "%";
    dv.className = "v num " + cls;
    db.className = "wbar " + (cls === "alarm" ? "a" : cls === "warn" ? "w" : "");
    db.querySelector(".fl").style.width = Math.max(0, Math.min(100, peak)) + "%";
    dsub.textContent = (dataBudget.peak_onboard_mb ?? 0).toFixed(0) + " / "
      + (dataBudget.storage_capacity_mb ?? 0).toFixed(0) + " MB · "
      + (dataBudget.plan_closes === false ? "PLAN DOES NOT CLOSE" : "plan closes");
    dsub.style.color = dataBudget.plan_closes === false ? "var(--alarm)" : "";
  }
}

/* ── 6. Link block ────────────────────────────────────────────────────── */
function renderLink() {
  const stale = isStale("link");
  const st = $("wLinkState");
  if (stale || !scState) {
    st.textContent = "—"; st.className = "state";
    $("wLinkStation").textContent = "—";
    $("wLinkElev").textContent = "—";
    $("wLinkRange").textContent = "—";
  } else {
    const inC = !!scState.in_contact;
    st.textContent = inC ? "IN CONTACT" : "NO CONTACT";
    st.className = "state" + (inC ? " yes" : "");
    $("wLinkStation").textContent = scState.active_gs || "—";
    $("wLinkElev").textContent = scState.gs_elevation_deg != null
      ? scState.gs_elevation_deg.toFixed(1) + "°" : "—";
    $("wLinkRange").textContent = scState.gs_range_km != null
      ? scState.gs_range_km.toFixed(0) + " km" : "—";
  }
  const dot = $("wApiDot"), txt = $("wApiText");
  const silent = lastAnyOk === 0 || Date.now() - lastAnyOk > 30000;
  dot.className = "api-dot " + (silent ? "error" : (apiOk ? "connected" : "error"));
  txt.textContent = silent ? "unavailable" : (apiOk ? "connected" : "degraded");
}

/* ── Timers (the ONLY drivers on this page) ───────────────────────────── */
function tick1s() {
  renderClock();
  renderPasses();     // countdown must move every second
  renderTimeline();   // now-marker redraw (1 s per plan §2.3)
  watchdog();
}

async function init() {
  await fetchConfig();
  await Promise.all([fetchTimeSource(), fetchContacts(), fetchLink()]);
  await Promise.all([fetchSchedule(), fetchBudgets(), fetchTrack(),
    fetchMission()]);
  await initTrackMap();
  renderTrackCanvas(); renderBudgets(); renderLink(); renderMission();
  tick1s();

  setInterval(tick1s, 1000);                                     //   1 s
  setInterval(async () => {                                      //  10 s
    await Promise.all([fetchTimeSource(), fetchContacts(), fetchLink()]);
    renderLink();
  }, 10000);
  setInterval(async () => {                                      //  30 s
    await Promise.all([fetchSchedule(), fetchBudgets(), fetchMission()]);
    renderBudgets(); renderMission();
  }, 30000);
  setInterval(async () => {                                      // 120 s
    await fetchTrack();
    renderTrackCanvas();
  }, 120000);
  setInterval(renderTrackCanvas, 10000);  // marker follows the spacecraft
}

document.addEventListener("DOMContentLoaded", init);

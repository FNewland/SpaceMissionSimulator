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

import { LAND } from "./wide-basemap.js";

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
let stations = [];        // canonical ground stations (/api/ground-stations)
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
/* Freshness for a family that resolved WITHOUT a network read (the designed
 * "not configured" state). It must not feed the page-wide watchdog: marking
 * lastAnyOk here made the 120 s DISPLAY STALE banner unreachable whenever
 * services.* was unset — i.e. in the default deployment — because the
 * mission poller "succeeded" every 30 s while the planner was unreachable. */
function markLocal(family) { lastOk[family] = Date.now(); }
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

/* Canonical station list (orbit.yaml ground_stations, served verbatim by
 * /api/ground-stations — defect #42 names that file as the ONE geometry
 * source). Static configuration, so it rides the slow track cadence and
 * shares the track family's freshness: if the map is stale the stations go
 * with it, because they are drawn on the same plot. */
async function fetchStations() {
  try {
    const res = await fetch("/api/ground-stations");
    if (!res.ok) throw new Error();
    const d = await res.json();
    stations = (d.ground_stations || []).filter(
      (g) => typeof g.lat_deg === "number" && typeof g.lon_deg === "number");
  } catch (e) { /* keep the last-known list; the track family gates staleness */ }
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
    rollupActive = null; markLocal("mission"); return; }
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

/* ── Orbital period — DERIVED, never a literal ────────────────────────── */
/* This repo has a history of hard-coded period literals — a decimal minutes
 * constant that outlived the elements it was measured from (the very reason
 * tests/test_planner/test_modern_page.py greps these files for it) — and the
 * elements DO change: today's epoch is 2026-08-20T12:00:00Z at ~513 km. The
 * orbit boundary that both the PLAN block and the GROUND TRACK block draw is
 * measured from the data the page already polls:
 *
 *   primary  — equator crossings of the served ground track. Successive
 *              crossings (ascending or descending) are half a NODAL period
 *              apart; the crossing time is linearly interpolated between the
 *              bracketing samples and the median of the gaps is doubled, so
 *              one mis-sampled crossing cannot move the answer. A 24 h track
 *              gives ~30 crossings; two (~47 min of track) already suffice.
 *   fallback — two-body Kepler from the mean geodetic altitude reported by
 *              the track / spacecraft state. mu and the mean Earth radius
 *              below are geodesy constants, NOT mission numbers: no altitude,
 *              period or SoC value is baked in here.
 *
 * Returns {ms, source} or null when neither route has data (the blocks then
 * render their em-dash / no-boundary state rather than guessing).
 */
const MU_EARTH_KM3_S2 = 398600.4418;     // WGS-84 geocentric gravitation
const EARTH_MEAN_R_KM = 6371.0;          // IUGG mean Earth radius

function derivedOrbitPeriod() {
  // -- primary: equator crossings of the served track ------------------
  const cross = [];
  for (let i = 1; i < track.length; i++) {
    const a = track[i - 1], b = track[i];
    if (typeof a.lat !== "number" || typeof b.lat !== "number") continue;
    if ((a.lat < 0) === (b.lat < 0)) continue;
    const ta = new Date(a.utc).getTime(), tb = new Date(b.utc).getTime();
    if (!isFinite(ta) || !isFinite(tb) || tb <= ta) continue;
    const f = Math.abs(a.lat) / (Math.abs(a.lat) + Math.abs(b.lat));
    cross.push(ta + f * (tb - ta));
  }
  if (cross.length >= 2) {
    const halves = [];
    for (let i = 1; i < cross.length; i++) halves.push(cross[i] - cross[i - 1]);
    halves.sort((x, y) => x - y);
    const mid = halves.length >> 1;
    const med = halves.length % 2 ? halves[mid]
      : (halves[mid - 1] + halves[mid]) / 2;
    const ms = 2 * med;
    // Sanity band: anything outside 60..240 min is not a LEO nodal period
    // (a sampling artefact), so fall through to Kepler rather than draw it.
    if (ms > 60 * 60000 && ms < 240 * 60000) {
      return { ms, source: "node crossings" };
    }
  }
  // -- fallback: Kepler from the reported mean altitude -----------------
  let alt = null;
  if (track.length) {
    const alts = track.map((p) => Number(p.alt_km)).filter((v) => isFinite(v));
    if (alts.length) alt = alts.reduce((s, v) => s + v, 0) / alts.length;
  }
  if (alt === null && scState && isFinite(Number(scState.alt_km))) {
    alt = Number(scState.alt_km);
  }
  if (alt === null || alt <= 0) return null;
  const a = EARTH_MEAN_R_KM + alt;
  const ms = 2 * Math.PI * Math.sqrt((a * a * a) / MU_EARTH_KM3_S2) * 1000;
  return { ms, source: "Kepler from mean altitude" };
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

/* Activity type -> wall label: "imaging_pass" reads as "IMAGING PASS". */
function activityLabel(name) {
  return String(name || "ACTIVITY").replace(/[_-]+/g, " ").trim().toUpperCase();
}

/* Draw text with a background-coloured outline so it stays legible where it
 * crosses a bar, a coastline or the track. */
function haloText(ctx, text, x, y, fill, bg) {
  ctx.lineJoin = "round";
  ctx.lineWidth = 4;
  ctx.strokeStyle = bg;
  ctx.strokeText(text, x, y);
  ctx.fillStyle = fill;
  ctx.fillText(text, x, y);
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
  const mL = 8, mR = 8, mT = 6, mB = 26;
  const pW = w - mL - mR, pH = h - mT - mB;
  const now = simNow();
  const windowMs = 24 * 3600 * 1000;

  if (stale) {
    ctx.fillStyle = C("--tx2"); ctx.font = "24px " + C("--mono");
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("—", w / 2, h / 2);
    return;
  }

  /* ── Two lanes (owner request 2026-08-19: "label the imminent ones") ──
     The 24 h overview is ~15 revolutions wide: an orbit is 6 % of the
     canvas, so a label drawn in place would be a smear. Instead the block
     splits into
        NEXT ORBIT  — now .. now + ONE DERIVED ORBITAL PERIOD, across the
                      full width, every contact and activity spelled out;
        NEXT 24 H   — the familiar three-band overview, with that same one
                      orbit shaded so the eye can place the detail lane.
     The boundary is derivedOrbitPeriod() — the SAME call the ground-track
     block uses for its solid/dotted split, so the two blocks cannot
     disagree about where "the next orbit" ends. */
  const per = derivedOrbitPeriod();
  const detailH = Math.max(56, pH * 0.66);
  const gap = 10;
  const ovY = mT + detailH + gap, ovH = Math.max(30, pH - detailH - gap);

  // ── Lane 1: NEXT ORBIT, labelled ──────────────────────────────────
  const hdrH = 22;
  ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.font = "700 18px " + C("--sans");
  ctx.fillStyle = C("--tx3");
  ctx.fillText("NEXT ORBIT", mL, mT);
  const hdrW = ctx.measureText("NEXT ORBIT").width;
  if (per) {
    ctx.font = "18px " + C("--mono");
    ctx.fillStyle = C("--tx2");
    ctx.fillText((per.ms / 60000).toFixed(1) + " MIN — DERIVED FROM "
      + per.source.toUpperCase(), mL + hdrW + 18, mT);
  }
  // Row names live in a left gutter, not on top of the bars: on the old
  // single-lane layout "CONTACTS" sat over the first hour of data.
  const gut = pW >= 700 ? 104 : 0;
  const plotL = mL + gut, plotW = pW - gut;
  const orbMs = per ? per.ms : null;
  const dEnd = orbMs ? now + orbMs : now + windowMs / 15;
  const dX = (t) => plotL + ((t - now) / (dEnd - now)) * plotW;
  const dTop = mT + hdrH;
  const dH = detailH - hdrH;
  const rowH = dH / 2;

  // 15-minute ticks so a labelled bar can still be placed in time.
  ctx.strokeStyle = C("--line"); ctx.lineWidth = 1;
  for (let m = 15; m < (dEnd - now) / 60000; m += 15) {
    const x = dX(now + m * 60000);
    ctx.beginPath(); ctx.moveTo(x, dTop); ctx.lineTo(x, dTop + dH); ctx.stroke();
  }
  ctx.fillStyle = C("--s2");
  ctx.fillRect(plotL, dTop, plotW, 1);
  ctx.fillRect(plotL, dTop + rowH, plotW, 1);
  if (gut) {
    ctx.font = "18px " + C("--sans");
    ctx.fillStyle = C("--tx3");
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText("CONTACTS", mL, dTop + rowH / 2);
    ctx.fillText("ACTIVITIES", mL, dTop + rowH * 1.5);
  }

  // Bar + label writer, shared by both detail rows. Labels are 21 px and
  // sit inside the bar when it is wide enough, immediately after it
  // otherwise; a label that would collide with the previous one is pushed
  // right, and dropped only if it would leave the lane.
  // Type and bars scale with the lane: the ultrawide tier gives this block
  // three times the height of the 16:9 tier, and 21 px labels floating in it
  // read as a whisper from the back of the room.
  const barH = Math.max(14, Math.min(48, rowH * 0.38));
  // The label sits ABOVE the bar, so its size is bounded by what is left of
  // the row — never below the 21 px this block needs at wall distance.
  const labelPx = Math.round(Math.max(21,
    Math.min(36, rowH - barH - 8)));
  function detailRow(items, rowTop, fill, edge, textColor) {
    const barY = rowTop + rowH - barH - 6;
    // Bars first, then the labels ABOVE them: a label sitting on top of a
    // bar (or painted over by the next item's bar) is exactly the mush the
    // owner could not read from across the room.
    for (const it of items) {
      const x1 = Math.max(plotL, dX(it.start));
      const x2 = Math.min(plotL + plotW, dX(it.end));
      const bw = Math.max(4, x2 - x1);
      ctx.fillStyle = fill;
      ctx.fillRect(x1, barY, bw, barH);
      ctx.strokeStyle = edge; ctx.lineWidth = 2;
      ctx.strokeRect(x1 + 1, barY + 1, Math.max(2, bw - 2), barH - 2);
    }
    ctx.font = "700 " + labelPx + "px " + C("--sans");
    ctx.textBaseline = "bottom"; ctx.textAlign = "left";
    let cursor = -1e9;
    for (const it of items) {
      const x1 = Math.max(plotL, dX(it.start));
      const tw = ctx.measureText(it.label).width;
      let tx = x1;
      if (tx < cursor) tx = cursor;               // never overwrite a label
      if (tx + tw > plotL + plotW) tx = plotL + plotW - tw;
      if (tx < cursor || tx < plotL) continue;    // no room left: drop it
      haloText(ctx, it.label, tx, barY - 4, textColor, C("--bg"));
      // A leader tick ties the label back to the bar it names.
      ctx.strokeStyle = edge; ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(tx + 1, barY - 2); ctx.lineTo(x1, barY + 1); ctx.stroke();
      cursor = tx + tw + 20;
    }
  }

  const hhmm = (t) => fmtUTC(t, { seconds: false });
  const nearContacts = contacts
    .filter((c) => c.los > now && c.aos < dEnd)
    .sort((a, b) => a.aos - b.aos)
    .map((c) => ({ start: c.aos, end: c.los,
      label: (c.aos <= now && c.los >= now ? "IN CONTACT " : "")
        + c.station.toUpperCase() + "  " + hhmm(c.aos)
        + (typeof c.maxEl === "number" ? "  " + c.maxEl.toFixed(0) + "°" : "") }));
  const nearActs = schedule
    .filter((a) => a.stateName !== "CANCELLED"
      && a.startMs + a.durMs > now && a.startMs < dEnd)
    .sort((a, b) => a.startMs - b.startMs)
    .map((a) => ({ start: a.startMs, end: a.startMs + a.durMs,
      label: activityLabel(a.name) + "  " + hhmm(a.startMs) }));

  detailRow(nearContacts, dTop, "rgba(107,179,255,0.30)", C("--info"),
    C("--tx"));
  detailRow(nearActs, dTop + rowH, "rgba(240,180,41,0.28)", C("--warn"),
    C("--tx"));

  ctx.font = "18px " + C("--sans"); ctx.fillStyle = C("--tx3");
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  if (!nearContacts.length) {
    haloText(ctx, "NO CONTACTS THIS ORBIT", plotL + plotW - 4,
      dTop + rowH / 2, C("--tx3"), C("--bg"));
  }
  if (!nearActs.length) {
    haloText(ctx, "NO ACTIVITIES THIS ORBIT", plotL + plotW - 4,
      dTop + rowH * 1.5, C("--tx3"), C("--bg"));
  }

  // ── Lane 2: the 24 h overview ─────────────────────────
  const toX = (t) => plotL + ((t - now) / windowMs) * plotW;

  // The one orbit blown up above, shaded here.
  if (orbMs) {
    ctx.fillStyle = "rgba(255,255,255,0.07)";
    ctx.fillRect(plotL, ovY, Math.max(2, toX(now + orbMs) - plotL), ovH);
    ctx.strokeStyle = C("--tx3"); ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(toX(now + orbMs), ovY);
    ctx.lineTo(toX(now + orbMs), ovY + ovH);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.strokeStyle = C("--line"); ctx.lineWidth = 1;
  ctx.fillStyle = C("--tx3"); ctx.font = "18px " + C("--mono");
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (let hr = 0; hr <= 24; hr += 4) {
    const x = plotL + (hr / 24) * plotW;
    ctx.beginPath(); ctx.moveTo(x, ovY); ctx.lineTo(x, ovY + ovH); ctx.stroke();
    ctx.fillText("+" + hr + "h", Math.min(x, w - 24), ovY + ovH + 4);
  }

  const bandEcl = { y: ovY, h: ovH * 0.26 };
  ctx.fillStyle = "rgba(90,169,255,0.28)";
  for (const [a, b] of eclipseIntervals()) {
    const x1 = Math.max(plotL, toX(a)), x2 = Math.min(plotL + plotW, toX(b));
    if (x2 > x1) ctx.fillRect(x1, bandEcl.y, x2 - x1, bandEcl.h);
  }
  const bandC = { y: ovY + ovH * 0.34, h: ovH * 0.28 };
  for (const c of contacts) {
    if (c.los < now || c.aos > now + windowMs) continue;
    const x1 = Math.max(plotL, toX(c.aos));
    const x2 = Math.min(plotL + plotW, toX(c.los));
    const active = c.aos <= now && c.los >= now;
    ctx.fillStyle = active ? "rgba(107,179,255,0.75)" : "rgba(107,179,255,0.45)";
    ctx.fillRect(x1, bandC.y, Math.max(x2 - x1, 3), bandC.h);
  }
  const bandA = { y: ovY + ovH * 0.70, h: ovH * 0.28 };
  ctx.fillStyle = "rgba(240,180,41,0.50)";
  for (const a of schedule) {
    const end = a.startMs + a.durMs;
    if (end < now || a.startMs > now + windowMs) continue;
    const x1 = Math.max(plotL, toX(a.startMs));
    const x2 = Math.min(plotL + plotW, toX(end));
    ctx.fillRect(x1, bandA.y, Math.max(x2 - x1, 3), bandA.h);
  }

  ctx.font = "18px " + C("--sans"); ctx.fillStyle = C("--tx3");
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  const ovLabelX = gut ? mL : plotL + 4;
  haloText(ctx, "ECLIPSE", ovLabelX, bandEcl.y + bandEcl.h / 2,
    C("--tx3"), C("--bg"));
  haloText(ctx, "CONTACTS", ovLabelX, bandC.y + bandC.h / 2,
    C("--tx3"), C("--bg"));
  haloText(ctx, "ACTIVITIES", ovLabelX, bandA.y + bandA.h / 2,
    C("--tx3"), C("--bg"));

  // Now-markers (redrawn every second with the whole canvas).
  ctx.strokeStyle = C("--alarm"); ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(plotL, dTop); ctx.lineTo(plotL, dTop + dH);
  ctx.stroke();
  ctx.beginPath(); ctx.moveTo(plotL, ovY); ctx.lineTo(plotL, ovY + ovH);
  ctx.stroke();
}

/* ── 4. Ground-track map — ONE renderer, the canvas ────────────────────

   Decision 2026-08-19 (see the RENDERER DECISION note below): this page no
   longer probes for the vendor SVG world map. The canvas below is the only
   path, in every deployment, online or off. It carries a real basemap, not
   a graticule:

     LAND (./wide-basemap.js) — Natural Earth 110 m land polygons,
     Douglas-Peucker simplified to 0.6 deg (45 rings / ~1.2 k points).
     Flat [lon,lat,lon,lat,...] rings.

   PROJECTION — plate carree with a SINGLE scale for both axes, so the map
   is never stretched to the block's aspect ratio (the old code mapped
   lon->[0,w] and lat->[0,h] independently, which squashed the world flat in
   a wide block). The scale fills the block's width with the full 360 deg
   unless that would push the track's polar reach (|lat| <= 82 deg for a
   sun-synchronous orbit) off the top and bottom, in which case the
   height-limited scale wins and the map is centred with thin side margins.
   Either way the map fills essentially the whole block. */
const TRACK_LAT_VIS = 84;        // deg of latitude guaranteed visible


function trackProjection(w, h) {
  const scale = Math.min(w / 360, h / (2 * TRACK_LAT_VIS));
  const cx = w / 2, cy = h / 2;
  return {
    scale, cx, cy,
    x: (lon) => cx + lon * scale,
    y: (lat) => cy - lat * scale,
    left: cx - 180 * scale, right: cx + 180 * scale,
    top: cy - 90 * scale, bottom: cy + 90 * scale,
  };
}

/* RENDERER DECISION (2026-08-19, this page only) — ONE renderer: canvas.
 *
 * This page previously tried /static/vendor/js/svg-world-map.js first and
 * fell back to the canvas basemap, i.e. two renderers with different
 * capabilities, only one of which was ever exercised. The wall now needs
 * things the vendor SVG map does not offer as primitives: a per-segment
 * dashed/solid split at a derived orbit boundary, haloed text labels, AOS
 * and LOS glyphs interpolated ONTO the track, leader lines and a legend.
 * Maintaining that twice — and shipping a wall whose appearance depends on
 * whether a vendor file happens to exist in a given deployment — is the
 * worse outcome, so the vendor path is gone from /wide (it is untouched on
 * the interactive planner page and in the MCS). The canvas renderer is
 * self-contained (Natural Earth land in wide-basemap.js), offline, identical
 * in every deployment, and is the path that is tested and screenshotted. */

/* Track position at an arbitrary time, by linear interpolation between the
 * bracketing served samples (the same points that are drawn), so AOS/LOS
 * marks land exactly ON the plotted track. Returns null outside the track. */
function trackPosAt(tMs) {
  if (track.length < 2) return null;
  let prevT = new Date(track[0].utc).getTime();
  if (tMs <= prevT) return { lat: track[0].lat, lon: track[0].lon };
  for (let i = 1; i < track.length; i++) {
    const t = new Date(track[i].utc).getTime();
    if (tMs <= t) {
      const a = track[i - 1], b = track[i];
      const f = (t === prevT) ? 0 : (tMs - prevT) / (t - prevT);
      let dLon = b.lon - a.lon;
      if (dLon > 180) dLon -= 360; else if (dLon < -180) dLon += 360;
      let lon = a.lon + f * dLon;
      if (lon > 180) lon -= 360; else if (lon < -180) lon += 360;
      return { lat: a.lat + f * (b.lat - a.lat), lon };
    }
    prevT = t;
  }
  return null;
}

function renderTrackCanvas() {
  const canvas = $("wTrackCanvas");
  const box = canvas.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(80, box.width), h = Math.max(60, box.height);
  canvas.style.width = w + "px"; canvas.style.height = h + "px";
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const css = getComputedStyle(document.documentElement);
  const C = (n) => css.getPropertyValue(n).trim();
  const P = trackProjection(w, h);
  ctx.fillStyle = C("--bg"); ctx.fillRect(0, 0, w, h);

  // Ocean panel: the projected map rectangle, clipped to the block.
  const mx = Math.max(0, P.left), mw = Math.min(w, P.right) - mx;
  const my = Math.max(0, P.top), mh = Math.min(h, P.bottom) - my;
  ctx.fillStyle = C("--bg") || "#0B0D10";
  ctx.fillRect(mx, my, mw, mh);

  // Landmasses.
  ctx.beginPath();
  for (const ring of LAND) {
    for (let i = 0; i < ring.length; i += 2) {
      const x = P.x(ring[i]), y = P.y(ring[i + 1]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.closePath();
  }
  ctx.fillStyle = C("--s3") || "#262C36";
  ctx.fill("evenodd");
  ctx.strokeStyle = C("--line-2") || "#3B4450";
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // Graticule every 30 deg, equator and prime meridian emphasised.
  ctx.strokeStyle = C("--line"); ctx.lineWidth = 1;
  for (let lon = -180; lon <= 180; lon += 30) {
    if (lon === 0) continue;
    const x = P.x(lon);
    ctx.beginPath(); ctx.moveTo(x, my); ctx.lineTo(x, my + mh); ctx.stroke();
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    if (lat === 0) continue;
    const y = P.y(lat);
    ctx.beginPath(); ctx.moveTo(mx, y); ctx.lineTo(mx + mw, y); ctx.stroke();
  }
  ctx.strokeStyle = C("--line-2");
  ctx.beginPath(); ctx.moveTo(mx, P.y(0)); ctx.lineTo(mx + mw, P.y(0)); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(P.x(0), my); ctx.lineTo(P.x(0), my + mh); ctx.stroke();

  const stale = isStale("track");
  if (stale) {
    // Stale treatment: the numerals of this block are its plotted track, so
    // the whole plot is withheld and replaced with the em-dash.
    ctx.fillStyle = C("--tx2");
    ctx.font = "700 " + Math.round(Math.min(120, h * 0.28)) + "px " + C("--mono");
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("\u2014", w / 2, h / 2);
    $("wTrackLegend").textContent = "\u2014";
    return;
  }

  const now = simNow();
  const per = derivedOrbitPeriod();
  // Map key, in the block header (the map itself keeps all of its pixels).
  $("wTrackLegend").textContent =
    "\u2014 SOLID = NOW\u2192+1 ORBIT"
    + (per ? " (" + (per.ms / 60000).toFixed(1) + " MIN)" : "")
    + " \u00b7 DOTTED = LATER \u00b7 \u25b2 STATION"
    + " \u00b7 \u25cf AOS \u00b7 \u25cb LOS";
  // ONE orbit boundary for the whole page: the same derivedOrbitPeriod()
  // the PLAN block labels against. Everything up to it (this revolution and
  // the next one it runs into) is drawn solid; everything after it is
  // dotted, so "solid = what the plan block just spelled out".
  const boundary = per ? now + per.ms : null;

  // Track segments (colour by eclipse), split at the antimeridian and at the
  // solid/dotted boundary, drawn as chunked polylines so the dash pattern is
  // continuous instead of restarting at every 120 s sample.
  const wide = Math.max(3, Math.min(5.5, P.scale * 0.7));
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  const dashLen = Math.max(2, wide * 0.55), dashGap = Math.max(6, wide * 2.2);
  let chunk = [], key = null, chunkAge = 0;
  const flushChunk = () => {
    if (chunk.length > 1 && key !== null) {
      const [ecl, solid] = key.split("|");
      ctx.strokeStyle = ecl === "1" ? C("--info") : C("--warn");
      if (solid === "1") {
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
        ctx.lineWidth = wide;
      } else {
        ctx.setLineDash([dashLen, dashGap]);
        ctx.globalAlpha = 0.55 - 0.28 * Math.min(1, chunkAge);
        ctx.lineWidth = wide * 0.62;
      }
      ctx.beginPath();
      ctx.moveTo(P.x(chunk[0].lon), P.y(chunk[0].lat));
      for (let j = 1; j < chunk.length; j++) {
        ctx.lineTo(P.x(chunk[j].lon), P.y(chunk[j].lat));
      }
      ctx.stroke();
    }
    chunk = [];
  };
  for (let i = 0; i < track.length; i++) {
    const pt = track[i];
    const t = new Date(pt.utc).getTime();
    const solid = boundary === null || t <= boundary;
    const k = (pt.in_eclipse ? "1" : "0") + "|" + (solid ? "1" : "0");
    const prevPt = i > 0 ? track[i - 1] : null;
    const wrapped = prevPt && Math.abs(pt.lon - prevPt.lon) > 180;
    if (chunk.length && (wrapped || k !== key)) {
      flushChunk();
      // Carry the previous point across a state change (but never across the
      // antimeridian) so the line has no gap at the boundary.
      if (!wrapped && prevPt) chunk.push(prevPt);
    }
    key = k;
    chunkAge = track.length > 1 ? i / (track.length - 1) : 0;
    chunk.push(pt);
  }
  flushChunk();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;

  /* Label placer: a wall has no hover and no tooltip, so every glyph that
     matters carries text — which means collisions have to be resolved here.
     Each label tries a ring of candidate offsets around its anchor and takes
     the first that clears everything already placed (and the map edges); a
     label with nowhere to go is dropped rather than drawn as mush. Labels go
     down in priority order, so the important ones win the good positions. */
  const placed = [];
  const overlaps = (r) => placed.some((q) =>
    r.x < q.x + q.w && q.x < r.x + r.w && r.y < q.y + q.h && q.y < r.y + r.h);
  function placeLabel(text, ax, ay, r, fill, font, px) {
    ctx.font = font;
    const tw = ctx.measureText(text).width, th = (px || 20) + 4;
    const cands = [
      [ax + r + 8, ay - r - 4, "left", "bottom"],
      [ax + r + 8, ay + r + 4, "left", "top"],
      [ax - r - 8, ay - r - 4, "right", "bottom"],
      [ax - r - 8, ay + r + 4, "right", "top"],
      [ax + r + 8, ay, "left", "middle"],
      [ax - r - 8, ay, "right", "middle"],
      [ax, ay - r - 10, "center", "bottom"],
      [ax, ay + r + 10, "center", "top"],
    ];
    for (const [x, y, al, bl] of cands) {
      const bx = al === "left" ? x : al === "right" ? x - tw : x - tw / 2;
      const by = bl === "top" ? y : bl === "bottom" ? y - th : y - th / 2;
      const rect = { x: bx - 3, y: by - 2, w: tw + 6, h: th + 4 };
      if (rect.x < 2 || rect.x + rect.w > w - 2
        || rect.y < 2 || rect.y + rect.h > h - 2) continue;
      if (overlaps(rect)) continue;
      placed.push(rect);
      ctx.textAlign = al; ctx.textBaseline = bl;
      haloText(ctx, text, x, y, fill, C("--bg"));
      return true;
    }
    return false;
  }
  // Chrome the labels must not collide with: the basemap-provenance chip
  // sitting in the DOM at the map's top right.
  placed.push({ x: w - 460, y: 0, w: 460, h: 34 });

  /* ── Upcoming passes on the map (owner request 3) ─────────────────────
     Driven by the SAME `contacts` array, filtered the SAME way, as the NEXT
     PASSES rail block (los > now, soonest first, five deep) — the list and
     the map are two views of one dataset and cannot disagree. Each pass is
     drawn as a highlighted arc of the track from AOS to LOS with a marked
     and labelled end at each: filled disc = AOS, hollow ring = LOS. */
  const upcoming = contacts.filter((c) => c.los > now)
    .sort((a, b) => a.aos - b.aos).slice(0, 5);
  const passR = Math.max(8, Math.min(20, P.scale * 2.0));
  const passLabels = [];
  if (!isStale("contacts")) {
    for (const c of upcoming) {
      const imminent = boundary === null || c.aos <= boundary;
      const a = trackPosAt(Math.max(c.aos, now)), b = trackPosAt(c.los);
      if (!a || !b) continue;
      // Highlighted contact arc.
      const arc = [a].concat(track.filter((p) => {
        const t = new Date(p.utc).getTime();
        return t > c.aos && t < c.los;
      })).concat([b]);
      ctx.strokeStyle = C("--ok");
      ctx.lineWidth = wide * 1.5;
      ctx.globalAlpha = imminent ? 1 : 0.55;
      ctx.beginPath();
      let started = false, lastLon = null;
      for (const p of arc) {
        if (lastLon !== null && Math.abs(p.lon - lastLon) > 180) started = false;
        const x = P.x(p.lon), y = P.y(p.lat);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
        lastLon = p.lon;
      }
      ctx.stroke();

      const ax = P.x(a.lon), ay = P.y(a.lat);
      const bx = P.x(b.lon), by = P.y(b.lat);
      // Leader from AOS to the station it is a pass over — only for the
      // imminent ones, so the map does not turn into a cat's cradle.
      const gs = stations.find((s) => s.name === c.station);
      if (imminent && gs) {
        ctx.setLineDash([3, 5]); ctx.lineWidth = 2;
        ctx.strokeStyle = C("--ok"); ctx.globalAlpha = 0.7;
        ctx.beginPath();
        ctx.moveTo(ax, ay); ctx.lineTo(P.x(gs.lon_deg), P.y(gs.lat_deg));
        ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.globalAlpha = 1;

      // AOS: filled disc. LOS: hollow ring. Both ringed in --bg so they
      // read over the track and the coastlines.
      ctx.lineWidth = 3; ctx.strokeStyle = C("--bg");
      ctx.beginPath(); ctx.arc(ax, ay, passR, 0, Math.PI * 2); ctx.stroke();
      ctx.fillStyle = C("--ok");
      ctx.beginPath(); ctx.arc(ax, ay, passR, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.arc(bx, by, passR, 0, Math.PI * 2); ctx.stroke();
      ctx.fillStyle = C("--bg");
      ctx.beginPath(); ctx.arc(bx, by, passR, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = C("--ok"); ctx.lineWidth = 4;
      ctx.beginPath(); ctx.arc(bx, by, passR - 1, 0, Math.PI * 2); ctx.stroke();

      // Labels are queued, not drawn here: they are placed after the
      // stations, once every arc and marker is on the canvas.
      passLabels.push({ text: "AOS " + c.station.toUpperCase() + " "
        + fmtUTC(c.aos, { seconds: false }), x: ax, y: ay, imminent });
      passLabels.push({ text: "LOS " + fmtUTC(c.los, { seconds: false }),
        x: bx, y: by, imminent });
    }
  }

  /* ── Ground stations (owner request 3) ────────────────────────────────
     The canonical five from /api/ground-stations (orbit.yaml
     ground_stations). Triangle glyph + name, always drawn: the wall should
     say where the network is even between passes. */
  const gsR = Math.max(9, Math.min(22, P.scale * 2.4));
  const gsLabels = [];
  for (const s of stations) {
    const x = P.x(s.lon_deg), y = P.y(s.lat_deg);
    // The "in contact" highlight is contact data, so it dies with the
    // contact family's freshness — a stale wall must not claim a live pass.
    const inContact = !isStale("contacts") && contacts.some(
      (c) => c.station === s.name && c.aos <= now && c.los >= now);
    ctx.beginPath();
    ctx.moveTo(x, y - gsR);
    ctx.lineTo(x + gsR * 0.92, y + gsR * 0.72);
    ctx.lineTo(x - gsR * 0.92, y + gsR * 0.72);
    ctx.closePath();
    ctx.fillStyle = inContact ? C("--ok") : C("--tx");
    ctx.strokeStyle = C("--bg"); ctx.lineWidth = 3;
    ctx.stroke(); ctx.fill();
    gsLabels.push({ text: s.name.toUpperCase(), x, y,
      fill: inContact ? C("--ok") : C("--tx") });
  }

  // Label pass, in priority order: stations (fixed geography the room
  // learns), then the imminent AOS/LOS tags, then the later ones. Type
  // scales with the map: on a 5760 px wall the map is nearly twice the
  // linear size it has at 1920, and 20 px labels on it read as a whisper.
  const LBL_PX = Math.round(Math.max(20, Math.min(32, P.scale * 4.6)));
  const LBL_FONT = "700 " + LBL_PX + "px " + C("--sans");
  for (const g of gsLabels) {
    placeLabel(g.text, g.x, g.y, gsR, g.fill, LBL_FONT, LBL_PX);
  }
  for (const l of passLabels.filter((l) => l.imminent)) {
    placeLabel(l.text, l.x, l.y, passR, C("--ok"), LBL_FONT, LBL_PX);
  }
  for (const l of passLabels.filter((l) => !l.imminent)) {
    placeLabel(l.text.split(" ")[0], l.x, l.y, passR, C("--ok"), LBL_FONT,
      LBL_PX);
  }

  // Spacecraft position — sized for the wall, with a halo so it reads over
  // the track it sits on.
  if (scState && !isStale("link")) {
    const x = P.x(scState.lon), y = P.y(scState.lat);
    const r = Math.max(8, Math.min(18, P.scale * 1.6));
    ctx.strokeStyle = C("--bg"); ctx.lineWidth = 4;
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = C("--tx");
    ctx.beginPath(); ctx.arc(x, y, r * 0.55, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = C("--tx"); ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
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
    fetchStations(), fetchMission()]);
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
    await Promise.all([fetchTrack(), fetchStations()]);
    renderTrackCanvas();
  }, 120000);
  setInterval(renderTrackCanvas, 10000);  // marker follows the spacecraft
}

document.addEventListener("DOMContentLoaded", init);

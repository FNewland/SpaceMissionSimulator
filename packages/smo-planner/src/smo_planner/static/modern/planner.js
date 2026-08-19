/* ============================================================================
 * SMO MISSION PLANNER — modern interactive page logic (plan §2.2)
 * ============================================================================
 * ES module, no build step. Companion to modern/index.html; classic
 * reference is ../index.html (frozen).
 *
 * Endpoints used (all pre-existing planner routes — additive-only API rule):
 *   GET  /api/time-source                 clock anchor + mode (5 s)
 *   GET  /api/spacecraft-state            position / eclipse / contact (5 s)
 *   GET  /api/contacts                    contact windows (30 s poll)
 *   GET  /api/schedule                    activity schedule (30 s poll)
 *   POST /api/schedule                    add activity (modal)
 *   PUT  /api/schedule/{id}               EDIT activity (per-row EDIT — new UI
 *                                         client for a previously headless route)
 *   DELETE /api/schedule/{id}             delete activity (per-row ×)
 *   DELETE /api/schedule                  clear schedule
 *   GET  /api/schedule/templates          template list
 *   POST /api/schedule/load-template      instantiate template
 *   GET  /api/schedule/validate           schedule validation
 *   POST /api/schedule/upload             upload to MCS
 *   GET  /api/activity-types              modal type list
 *   GET  /api/ground-stations             station list (canonical, orbit.yaml)
 *   GET  /api/ground-track                map track (120 s) + 24 h eclipse arcs
 *   GET  /api/config/planner              orbit/budget configuration (P4)
 *   GET  /api/budget/power                power budget (30 s)
 *   GET  /api/budget/data                 data budget (30 s)
 *   GET  /api/imaging/targets             target list (new UI client)
 *   GET  /api/imaging/opportunities       imaging windows (30 s)
 *   POST /api/imaging/schedule            schedule a capture
 *   GET  /api/constraints/validate        aggregate check (explicit SoC — P5)
 *   GET  /api/constraints/power           per-domain breakdown accordion
 *   GET  /api/constraints/aocs              "
 *   GET  /api/constraints/thermal           "
 *   GET  /api/constraints/data-volume       "
 *   GET  /api/constraints/conflicts         "
 *
 * Polling cadences preserved from the classic page (Report 1 §1B):
 *   1 s clock · 5 s spacecraft-state · 5 s time-source · 10 s contacts
 *   table + timeline redraw · 30 s main poll · 60 s terminator · 120 s track.
 * ============================================================================ */

import { esc, poller } from "/static/shared/components.js";
import { fmtUTC, fmtMET } from "/static/shared/format.js";

const $ = (id) => document.getElementById(id);

/* ── Server clock (P2 / defect #58): the ONE time primitive ───────────── */
let timeSource = null;          // null until /api/time-source resolves (P3)
let simAnchorMs = null, simAnchorWallMs = null, simSpeed = 1.0;
let metAnchorS = null;          // MET at the anchor instant; null = not served
let clockDegraded = false, clockNote = "";

function simNow() {
  if (simAnchorMs === null) return Date.now();
  return simAnchorMs + (Date.now() - simAnchorWallMs) * simSpeed;
}
const simNowDate = () => new Date(simNow());
function metNowS() {
  if (metAnchorS === null || simAnchorMs === null) return null;
  return metAnchorS + (simNow() - simAnchorMs) / 1000;
}

/* ── Page state ───────────────────────────────────────────────────────── */
let contacts = [];              // {station, aos:Date, los:Date, maxEl, durationMin}
let schedule = [];              // raw rows + start:Date, durationMin
let groundStations = [], groundStationsError = false;
let mapTrack = [];              // 3 h map window (past 90 min + future)
let eclipseTrack = [];          // 24 h track for timeline arcs / next eclipse
let scState = null;
let plannerConfig = null, budgetParams = null, services = null;
let activityTypes = [], planTemplates = [], imagingOpps = [];

/* Cross-origin service URL join (G14): the path literal deliberately has no
 * leading slash — it is relative to the CONFIGURED base
 * (planning/planner.yaml services.*), never a planner route. */
function serviceUrl(base, path) {
  return String(base).replace(/\/+$/, "") + "/" + path;
}

/* ── Derived orbit values (P4: from served data, never literals) ──────── */
const MU_KM3_S2 = 398600.4418, EARTH_R_KM = 6378.137;
function orbitPeriodMin() {
  const alt = budgetParams && Number(budgetParams.orbit_altitude_km);
  if (!alt || !Number.isFinite(alt)) return null;
  const a = EARTH_R_KM + alt;
  return (2 * Math.PI * Math.sqrt((a * a * a) / MU_KM3_S2)) / 60;
}
function inclinationEstimateDeg() {
  // Max |lat| of the served track = i (prograde) or 180-i (retrograde);
  // the longitude drift at the northernmost point tells the two apart.
  const t = eclipseTrack.length ? eclipseTrack : mapTrack;
  if (t.length < 3) return null;
  let iMax = 0;
  for (let i = 1; i < t.length; i++) {
    if (Math.abs(t[i].lat) > Math.abs(t[iMax].lat)) iMax = i;
  }
  const maxLat = Math.abs(t[iMax].lat);
  const j = Math.min(iMax + 1, t.length - 1), k = Math.max(iMax - 1, 0);
  let dLon = t[j].lon - t[k].lon;
  if (dLon > 180) dLon -= 360;
  if (dLon < -180) dLon += 360;
  return dLon >= 0 ? maxLat : 180 - maxLat;
}

/* ── Clock + badges ───────────────────────────────────────────────────── */
function updateClock() {
  const now = simNowDate();
  $("utcClock").textContent = fmtUTC(now);
  const months = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
  $("utcDate").textContent =
    `${String(now.getUTCDate()).padStart(2, "0")} ${months[now.getUTCMonth()]} ${now.getUTCFullYear()}`;
  const met = metNowS();
  const metEl = $("metReadout");
  if (met === null) {
    metEl.textContent = "MET —";
    metEl.title = "MET is not published by the planner in this mode "
      + "(real mode has no simulator MET — disposition L4)";
  } else {
    metEl.textContent = fmtMET(met);
    metEl.title = "Mission elapsed time as published by the planner";
  }
  updateClockBadge();
}

// P1: the badge names the clock; the speed tooltip is deliberately gone.
function updateClockBadge() {
  const el = $("clockSourceBadge");
  if (timeSource === null && !clockDegraded) { el.textContent = "—"; return; }
  el.classList.remove("wall", "warn");
  if (clockDegraded) {
    el.classList.add("wall");
    el.textContent = "WALL CLOCK";
    el.title = clockNote || "Planner sim clock unavailable — showing browser wall clock";
  } else {
    el.textContent = timeSource === "sim" ? "SIM TIME" : "UTC (REAL)";
    el.title = "Clock source: " + timeSource;
  }
}

// P3: badges default "—" in markup; text appears only once the mode is known.
function applyDataSourceBadges() {
  if (timeSource === null && !clockDegraded) return;
  const sim = timeSource === "sim";
  const shortText = clockDegraded ? "WALL" : (sim ? "SIM" : "UTC");
  const longText = clockDegraded ? "WALL CLOCK" : (sim ? "SIM CLOCK" : "REAL UTC");
  const title = clockNote || ("Clock source: " + (timeSource || "unknown"));
  for (const id of ["mapDataBadge", "powerDataBadge", "dataDataBadge", "imagingDataBadge"]) {
    const el = $(id); el.textContent = shortText; el.title = title;
    el.classList.toggle("warn", clockDegraded);
  }
  for (const id of ["contactDataBadge", "activityDataBadge"]) {
    const el = $(id); el.textContent = longText; el.title = title;
    el.classList.toggle("warn", clockDegraded);
  }
}

async function loadTimeSource() {
  try {
    const res = await fetch("/api/time-source");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    if (data.time_source === "sim" || data.time_source === "real") {
      timeSource = data.time_source;
    }
    const serverNow = data.now || data.sim_time;
    if (serverNow) {
      simAnchorMs = new Date(serverNow).getTime();
      simAnchorWallMs = Date.now();
    }
    if (typeof data.speed === "number") simSpeed = data.speed;
    metAnchorS = (typeof data.met_s === "number") ? data.met_s : null;
    clockDegraded = !!(data.degraded || data.wall_clock_fallback);
    clockNote = "";
    if (data.wall_clock_fallback) {
      clockNote = "Simulator at " + (data.sim_state_url || "the configured URL")
        + " is unreachable — planner is running on wall-clock UTC";
    } else if (data.degraded) {
      clockNote = "Sim clock anchor is stale (" + data.anchor_age_s + " s old)";
    }
  } catch (e) {
    clockDegraded = true;
    clockNote = "Planner API unreachable — page is on browser wall clock";
  }
  applyDataSourceBadges();
  updateClockBadge();
}

/* ── Planner configuration (P4) ───────────────────────────────────────── */
async function loadPlannerConfig() {
  const res = await fetch("/api/config/planner");
  if (!res.ok) throw new Error("HTTP " + res.status);
  const data = await res.json();
  plannerConfig = data.planner_config || null;
  budgetParams = data.budget_params || null;
  services = (plannerConfig && plannerConfig.services) || null;
  const p = orbitPeriodMin();
  $("scPeriod").textContent = p ? "~" + p.toFixed(1) + " min" : "—";
  $("bbPeriod").textContent = p ? p.toFixed(1) + " min" : "—";
  $("bbRevDay").textContent = p ? (1440 / p).toFixed(2) : "—";
  const soc = plannerConfig && plannerConfig.budget
    && plannerConfig.budget.initial_soc_percent;
  const socEl = $("socInput");
  if (typeof soc === "number" && !socEl.value) {
    socEl.placeholder = "default " + soc + "%";
    socEl.title = "State of charge the constraint check assumes. Leave blank "
      + "to use the server's configured default (" + soc + "% from planner.yaml).";
  }
}

/* ── Ground track map (svg-world-map from /static/vendor/) ────────────── */
let map = null, vendorMissing = false;
let trackLayers = [], gsMarkers = [], gsCircles = [], gsLabels = [];
let scMarker = null, scFootprint = null, nightOverlay = null;

function loadVendorMap() {
  return new Promise((resolve) => {
    const s = document.createElement("script");
    s.src = "/static/vendor/js/svg-world-map.js";
    s.onload = () => resolve(typeof window.SvgWorldMap !== "undefined");
    s.onerror = () => resolve(false);
    document.head.appendChild(s);
  });
}

async function initMap() {
  const ok = await loadVendorMap();
  if (!ok) {
    // Designed vendor-missing state (risk R3) — never a silent black box.
    vendorMissing = true;
    $("groundTrackMap").style.display = "none";
    $("mapFallback").style.display = "block";
    return;
  }
  map = new SvgWorldMap("groundTrackMap");
  updateTerminator();
  setInterval(updateTerminator, 60000);           // 60 s (classic cadence)
  drawGroundTrack();
  updateGroundStationMarkers();
  updateSpacecraftMarker();
}

function computeSubsolarPoint(date) {
  const JD = date.getTime() / 86400000 + 2440587.5;
  const n = JD - 2451545.0;
  const L0 = (280.460 + 0.9856474 * n) % 360;
  const g = ((357.528 + 0.9856003 * n) % 360) * Math.PI / 180;
  const lambda = (L0 + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * Math.PI / 180;
  const epsilon = (23.439 - 0.0000004 * n) * Math.PI / 180;
  const decl = Math.asin(Math.sin(epsilon) * Math.sin(lambda));
  const RA = Math.atan2(Math.cos(epsilon) * Math.sin(lambda), Math.cos(lambda));
  const eqTime = (L0 * Math.PI / 180 - RA) * 720 / Math.PI;
  const utcHours = date.getUTCHours() + date.getUTCMinutes() / 60 + date.getUTCSeconds() / 3600;
  const subLon = (12 - utcHours - eqTime / 60) * 15;
  return { lat: decl * 180 / Math.PI, lon: ((subLon + 180) % 360 + 360) % 360 - 180 };
}

function terminatorLatAtLon(lonDeg, subsolar) {
  const declR = subsolar.lat * Math.PI / 180;
  if (Math.abs(declR) < 1e-6) return 0;
  const H = (lonDeg - subsolar.lon) * Math.PI / 180;
  return Math.atan(-Math.cos(H) / Math.tan(declR)) * 180 / Math.PI;
}

function updateTerminator() {
  if (!map) return;
  const subsolar = computeSubsolarPoint(simNowDate());
  const poly = [];
  for (let lon = -180; lon <= 180; lon += 2) {
    poly.push([terminatorLatAtLon(lon, subsolar), lon]);
  }
  const nightPoleLat = subsolar.lat >= 0 ? -90 : 90;
  poly.push([nightPoleLat, 180]); poly.push([nightPoleLat, -180]);
  if (nightOverlay) map.removeLayer(nightOverlay);
  nightOverlay = map.addPolygon(poly, {
    fillColor: "#000011", fillOpacity: 0.45, color: "none" });
}

function updateGroundStationMarkers() {
  if (!map) return;
  for (const l of [...gsMarkers, ...gsCircles, ...gsLabels]) map.removeLayer(l);
  gsMarkers = []; gsCircles = []; gsLabels = [];
  for (const gs of groundStations) {
    gsMarkers.push(map.addMarker(gs.lat_deg, gs.lon_deg, {
      color: "#5AA9FF", strokeColor: "#5AA9FF", radius: 4 }));
    gsLabels.push(map.addLabel(gs.lat_deg, gs.lon_deg, gs.name, {
      color: "#A3ACB9", fontSize: "5.5" }));
    gsCircles.push(map.addCircle(gs.lat_deg, gs.lon_deg, 2200, {
      color: "#5AA9FF", weight: 0.5, opacity: 0.25,
      fillColor: "#5AA9FF", fillOpacity: 0.04, dashArray: "2 3" }));
  }
}

function drawGroundTrack() {
  if (!map || mapTrack.length < 2) return;
  for (const l of trackLayers) map.removeLayer(l);
  trackLayers = [];
  const nowMs = simNow();
  let seg = [], segEclipse = mapTrack[0].in_eclipse;
  let segPast = new Date(mapTrack[0].utc).getTime() < nowMs;
  const flush = () => {
    if (seg.length > 1) {
      trackLayers.push(map.addPolyline(seg, {
        color: segEclipse ? "#5AA9FF" : "#E8A317",
        weight: segPast ? 1.5 : 2.5,
        opacity: segPast ? 0.35 : (segEclipse ? 0.7 : 0.85),
        dashArray: segPast ? "4 6" : (segEclipse ? "6 4" : null) }));
    }
  };
  for (let i = 0; i < mapTrack.length; i++) {
    const pt = mapTrack[i];
    const isPast = new Date(pt.utc).getTime() < nowMs;
    const prev = i > 0 ? mapTrack[i - 1] : null;
    const lonJump = prev ? Math.abs(pt.lon - prev.lon) : 0;
    if ((lonJump > 180 || pt.in_eclipse !== segEclipse || isPast !== segPast)
        && seg.length) {
      flush(); seg = [];
    }
    segEclipse = pt.in_eclipse; segPast = isPast;
    seg.push([pt.lat, pt.lon]);
  }
  flush();
}

function scGlyph(headingDeg) {
  return '<svg width="20" height="20" viewBox="0 0 24 24" style="transform:rotate('
    + (headingDeg || 0) + 'deg)"><polygon points="12,2 18,20 12,16 6,20" '
    + 'fill="#E6E9ED" stroke="#A3ACB9" stroke-width="1" opacity="0.9"/></svg>';
}

function updateSpacecraftMarker() {
  if (!scState) return;
  const { lat, lon } = scState;
  const latDir = lat >= 0 ? "N" : "S", lonDir = lon >= 0 ? "E" : "W";
  $("mapLat").textContent = Math.abs(lat).toFixed(2) + "°" + latDir;
  $("mapLon").textContent = Math.abs(lon).toFixed(2) + "°" + lonDir;
  $("mapAlt").textContent = scState.alt_km + " km";
  $("mapBeta").textContent = scState.solar_beta_deg + "°";
  if (!map) return;
  if (!scMarker) {
    scMarker = map.addMarker(lat, lon, { html: scGlyph(scState.heading_deg), size: 20 });
  } else {
    scMarker.setLatLng(lat, lon);
    scMarker.setIcon({ html: scGlyph(scState.heading_deg) });
  }
  // Sensor footprint from the CONFIGURED payload FOV (P4), not a literal.
  const fov = plannerConfig && plannerConfig.imaging && plannerConfig.imaging.fov_deg;
  if (fov && scState.alt_km) {
    const rKm = scState.alt_km * Math.tan((fov / 2) * Math.PI / 180);
    if (!scFootprint) {
      scFootprint = map.addCircle(lat, lon, rKm, {
        color: "#E6E9ED", weight: 0.8, opacity: 0.3,
        fillColor: "#E6E9ED", fillOpacity: 0.06, dashArray: "2 3" });
    } else {
      scFootprint.setLatLng(lat, lon);
    }
  }
}

/* ── Spacecraft state (header + map overlay) ──────────────────────────── */
async function loadSpacecraftState() {
  const res = await fetch("/api/spacecraft-state");
  if (!res.ok) throw new Error("HTTP " + res.status);
  scState = await res.json();
  const { lat, lon } = scState;
  $("scLatLon").textContent = Math.abs(lat).toFixed(2) + "°" + (lat >= 0 ? "N" : "S")
    + " / " + Math.abs(lon).toFixed(2) + "°" + (lon >= 0 ? "E" : "W");
  $("scAlt").textContent = scState.alt_km + " km";
  const badge = $("scStateBadge");
  badge.innerHTML = "<span>" + (scState.in_eclipse ? "ECLIPSE" : "SUNLIT") + "</span>";
  badge.classList.toggle("warn", !!scState.in_eclipse);
  updateSpacecraftMarker();
}

/* ── Ground track fetches (map 3 h window + 24 h eclipse horizon) ─────── */
async function loadGroundTrack() {
  // Map window: 90 min of past + the rest future (classic behaviour).
  try {
    let res = await fetch("/api/ground-track?offset_minutes=-90&duration_hours=3.0&step_s=30");
    if (!res.ok) res = await fetch("/api/ground-track");
    if (res.ok) {
      mapTrack = (await res.json()).ground_track || [];
      drawGroundTrack();
    }
  } catch (e) { /* map keeps last track */ }
  // Timeline horizon: the timeline is 24 h, so its eclipse arcs and the
  // NEXT ECLIPSE readout come from a 24 h track — real propagation, not the
  // classic page's fixed orbit-period pattern (P4).
  try {
    const hours = (plannerConfig && plannerConfig.horizons
      && plannerConfig.horizons.timeline_hours) || 24;
    const res = await fetch("/api/ground-track?duration_hours=" + hours + "&step_s=120");
    if (res.ok) eclipseTrack = (await res.json()).ground_track || [];
  } catch (e) { /* keep last horizon */ }
  const incl = inclinationEstimateDeg();
  $("bbIncl").textContent = incl === null ? "—" : "~" + incl.toFixed(1) + "°";
}

function eclipseIntervals() {
  const out = [];
  let start = null;
  for (const pt of eclipseTrack) {
    const t = new Date(pt.utc).getTime();
    if (pt.in_eclipse && start === null) start = t;
    if (!pt.in_eclipse && start !== null) { out.push([start, t]); start = null; }
  }
  if (start !== null && eclipseTrack.length) {
    out.push([start, new Date(eclipseTrack[eclipseTrack.length - 1].utc).getTime()]);
  }
  return out;
}

/* ── Contacts + schedule polling ──────────────────────────────────────── */
async function pollAPIs() {
  let contactsOk = false, scheduleOk = false;
  try {
    const res = await fetch("/api/contacts");
    if (res.ok) {
      const data = await res.json();
      contacts = (data.contacts || []).map((c) => ({
        station: c.gs_name, aos: new Date(c.aos), los: new Date(c.los),
        maxEl: c.max_elevation_deg,
        durationMin: Math.round((c.duration_s || 0) / 60) }));
      contactsOk = true;
    }
  } catch (e) { contacts = []; }
  try {
    const res = await fetch("/api/schedule");
    if (res.ok) {
      const data = await res.json();
      schedule = (data.schedule || []).map((s) => ({
        ...s, start: new Date(s.start_time),
        durationMin: Math.round((s.duration_s || 300) / 60),
        state_name: s.state_name || "PLANNED" }));
      scheduleOk = true;
    }
  } catch (e) { schedule = []; }

  const dot = $("apiDot"), txt = $("apiStatusText");
  if (contactsOk && scheduleOk) {
    dot.className = "api-dot connected";
    txt.textContent = clockDegraded ? "API: connected (wall clock)" : "API: connected";
  } else {
    dot.className = "api-dot error";
    txt.textContent = "API: unavailable";
  }
  applyDataSourceBadges();
  drawTimeline();
  updateContactsTable();
  updateActivityList();
  loadPowerBudget(); loadDataBudget(); loadImagingOpportunities(); loadImagingTargets();
  loadPassRollups();
}

/* ── Timeline canvas ──────────────────────────────────────────────────── */
function drawTimeline() {
  const canvas = $("timelineCanvas");
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.parentElement.clientWidth, h = 180;
  canvas.style.width = w + "px"; canvas.style.height = h + "px";
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const C = (n) => css.getPropertyValue(n).trim();
  ctx.fillStyle = C("--bg"); ctx.fillRect(0, 0, w, h);

  const mL = 80, mR = 12, mT = 22, mB = 18;
  const pW = w - mL - mR, pH = h - mT - mB;
  const gsCount = Math.max(groundStations.length, 1);
  const rowH = pH / gsCount;
  const now = simNow();
  const windowMs = 24 * 3600 * 1000;
  const toX = (t) => mL + ((t - now) / windowMs) * pW;

  ctx.strokeStyle = C("--line"); ctx.lineWidth = 0.5;
  for (let hr = 0; hr <= 24; hr += 2) {
    const x = mL + (hr / 24) * pW;
    ctx.beginPath(); ctx.moveTo(x, mT); ctx.lineTo(x, mT + pH); ctx.stroke();
  }
  for (let i = 0; i <= gsCount; i++) {
    const y = mT + i * rowH;
    ctx.beginPath(); ctx.moveTo(mL, y); ctx.lineTo(mL + pW, y); ctx.stroke();
  }

  // Eclipse arcs from the SERVED 24 h track (P4) — none served, none drawn.
  ctx.fillStyle = "rgba(90,169,255,0.10)";
  for (const [a, b] of eclipseIntervals()) {
    const x1 = Math.max(mL, toX(a)), x2 = Math.min(mL + pW, toX(b));
    if (x2 > mL && x1 < mL + pW) ctx.fillRect(x1, mT, x2 - x1, pH);
  }

  for (const c of contacts) {
    const idx = groundStations.findIndex((gs) => gs.name === c.station);
    if (idx === -1) continue;
    const aosMs = c.aos.getTime(), losMs = c.los.getTime();
    if (losMs < now || aosMs > now + windowMs) continue;
    const x1 = Math.max(mL, toX(aosMs)), x2 = Math.min(mL + pW, toX(losMs));
    const y = mT + idx * rowH + 4, bh = rowH - 8;
    const active = aosMs <= now && losMs >= now;
    ctx.fillStyle = active ? "rgba(90,169,255,0.45)" : "rgba(138,147,160,0.30)";
    ctx.strokeStyle = active ? C("--info") : C("--tx3");
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.roundRect(x1, y, Math.max(x2 - x1, 2), bh, 2); ctx.fill();
    ctx.beginPath(); ctx.roundRect(x1, y, Math.max(x2 - x1, 2), bh, 2); ctx.stroke();
    if (x2 - x1 > 30) {
      ctx.fillStyle = C("--tx"); ctx.font = "12px " + C("--mono");
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(c.durationMin + "m", (x1 + x2) / 2, y + bh / 2);
    }
  }

  ctx.fillStyle = C("--tx2"); ctx.font = "12px " + C("--mono");
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  groundStations.forEach((gs, i) => {
    ctx.fillText(gs.name, mL - 8, mT + i * rowH + rowH / 2);
  });
  ctx.fillStyle = C("--tx3"); ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (let hr = 0; hr <= 24; hr += 4) {
    ctx.fillText("+" + hr + "h", mL + (hr / 24) * pW, mT + pH + 4);
  }

  ctx.strokeStyle = C("--alarm"); ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
  ctx.beginPath(); ctx.moveTo(mL, mT); ctx.lineTo(mL, mT + pH); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = C("--alarm"); ctx.font = "bold 12px " + C("--mono");
  ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  ctx.fillText("NOW" + (clockDegraded ? " (wall clock)" : ""), mL + 3, mT - 6);

  if (groundStations.length === 0 || contacts.length === 0) {
    ctx.fillStyle = C("--tx2"); ctx.font = "13px " + C("--mono");
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(
      groundStationsError
        ? "Ground-station list unavailable — check /api/ground-stations"
        : (groundStations.length === 0
          ? "No ground stations configured — check orbit.yaml"
          : "No contact windows — check configuration"),
      mL + pW / 2, mT + pH / 2);
  }
}

/* ── Contacts table + bottom-bar countdowns ───────────────────────────── */
function updateContactsTable() {
  const now = simNow();
  const upcoming = contacts.filter((c) => c.los.getTime() > now)
    .sort((a, b) => a.aos - b.aos).slice(0, 5);
  const tbody = $("contactsBody");
  tbody.innerHTML = upcoming.map((c) => {
    const active = c.aos.getTime() <= now && c.los.getTime() >= now;
    const status = active ? "IN PROGRESS" : "UPCOMING";
    return "<tr><td>" + esc(c.station) + "</td><td>" + fmtUTC(c.aos)
      + "</td><td>" + fmtUTC(c.los) + "</td><td>" + c.durationMin
      + " min</td><td>" + (typeof c.maxEl === "number" ? c.maxEl.toFixed(1) : esc(String(c.maxEl)))
      + "°</td><td style=\"" + (active ? "color:var(--info);font-weight:700" : "color:var(--tx2)")
      + "\">" + status + "</td></tr>";
  }).join("");

  const mmss = (ms) => String(Math.floor(ms / 60000)).padStart(2, "0")
    + ":" + String(Math.floor((ms % 60000) / 1000)).padStart(2, "0");
  const next = contacts.find((c) => c.aos.getTime() > now);
  $("nextAos").textContent = next
    ? next.station + " in " + mmss(next.aos.getTime() - now) : "—";
  const ecl = eclipseIntervals().find(([a]) => a > now);
  const inEcl = eclipseIntervals().find(([a, b]) => a <= now && b > now);
  $("nextEclipse").textContent = inEcl ? "in eclipse now"
    : (ecl ? "in " + mmss(ecl[0] - now) : "—");
}

/* ── Activity schedule list (per-row EDIT + DELETE) ───────────────────── */
function updateActivityList() {
  const el = $("activityList");
  if (!schedule.length) {
    el.innerHTML = "<div style=\"color:var(--tx3);font-size:12px;padding:8px 2px\">"
      + "No activities planned. Use ADD ACTIVITY, or load a standard activity set below.</div>";
    return;
  }
  el.innerHTML = schedule.map((a) => {
    const h = String(a.start.getUTCHours()).padStart(2, "0");
    const m = String(a.start.getUTCMinutes()).padStart(2, "0");
    const state = a.state_name || "PLANNED";
    const stateCol = state === "FAILED" ? "var(--alarm)"
      : state === "EXECUTING" || state === "UPLOADED" ? "var(--info)" : "var(--tx2)";
    return "<div class=\"act-row\" data-id=\"" + esc(a.id) + "\">"
      + "<div class=\"when num\">" + h + ":" + m + "<small>UTC</small></div>"
      + "<div><div class=\"nm\">" + esc(a.name)
      + " <span class=\"st\" style=\"color:" + stateCol + "\">" + esc(state) + "</span></div>"
      + "<div class=\"meta\">" + a.durationMin + " min · "
      + esc(a.station || "no dependency") + " · " + esc(a.priority || "medium") + "</div></div>"
      + "<div class=\"ops\">"
      + (a.id !== undefined && a.id !== null
        ? "<button class=\"ds-btn\" data-act=\"edit\" data-id=\"" + esc(a.id)
          + "\" title=\"Edit start time / duration / priority\">EDIT</button>"
          + "<button class=\"ds-btn danger\" data-act=\"delete\" data-id=\"" + esc(a.id)
          + "\" title=\"Delete activity\">&times;</button>"
        : "")
      + "</div></div>";
  }).join("");
}

async function deleteActivity(id) {
  if (!confirm("Delete activity #" + id + "?")) return;
  try {
    await fetch("/api/schedule/" + id, { method: "DELETE" });
    mainPoller.kick();
  } catch (e) { /* next poll refreshes */ }
}

/* ── Add / Edit modal — classic page's proven UTC handling, verbatim ──── */
function dateToUtcInputValue(d) {
  const p = (n) => String(n).padStart(2, "0");
  return d.getUTCFullYear() + "-" + p(d.getUTCMonth() + 1) + "-" + p(d.getUTCDate())
    + "T" + p(d.getUTCHours()) + ":" + p(d.getUTCMinutes());
}
function utcInputValueToDate(v) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(v || "");
  if (!m) return null;
  return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)));
}
function updateStartTimeHint() {
  const hintEl = $("actStartTimeHint");
  const d = utcInputValueToDate($("actStartTime").value);
  if (!d || isNaN(d.getTime())) { hintEl.textContent = "Enter a UTC start time."; return; }
  let zone = "";
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { /* ok */ }
  const offMin = -d.getTimezoneOffset();
  const sign = offMin < 0 ? "-" : "+";
  const abs = Math.abs(offMin);
  const offset = "UTC" + sign + String(Math.floor(abs / 60)).padStart(2, "0")
    + ":" + String(abs % 60).padStart(2, "0");
  hintEl.textContent = d.toISOString().slice(0, 16).replace("T", " ")
    + " UTC = " + d.toLocaleString() + " local (" + (zone ? zone + ", " : "") + offset + ")";
}

let editingId = null;   // null = add mode; number = PUT /api/schedule/{id}

function openModal(activity) {
  editingId = activity ? activity.id : null;
  $("modalTitle").textContent = activity
    ? "EDIT ACTIVITY #" + activity.id : "ADD ACTIVITY";
  $("editNote").style.display = activity ? "block" : "none";
  $("actType").disabled = !!activity;
  $("actStation").disabled = !!activity;
  if (activity) {
    // The catalogue name may not be in the select; show it as-is.
    const sel = $("actType");
    if (![...sel.options].some((o) => o.value === activity.name)) {
      const opt = document.createElement("option");
      opt.value = activity.name; opt.textContent = activity.name;
      sel.appendChild(opt);
    }
    sel.value = activity.name;
    $("actStartTime").value = dateToUtcInputValue(activity.start);
    $("actDuration").value = activity.durationMin;
    $("actPriority").value = activity.priority || "medium";
    $("actStation").value = activity.station || "";
  } else {
    // Prefill with the PLANNER's current time (sim time in sim mode).
    $("actStartTime").value = dateToUtcInputValue(simNowDate());
  }
  updateStartTimeHint();
  $("modalOverlay").classList.add("active");
}
function closeModal() { $("modalOverlay").classList.remove("active"); editingId = null; }

async function submitActivity() {
  const startDate = utcInputValueToDate($("actStartTime").value);
  if (!startDate || isNaN(startDate.getTime())) {
    alert("Could not read the start time — expected UTC YYYY-MM-DD HH:MM."); return;
  }
  const duration = parseInt($("actDuration").value, 10) || 15;
  const priority = $("actPriority").value || "medium";
  try {
    let res;
    if (editingId !== null) {
      res = await fetch("/api/schedule/" + editingId, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start_time: startDate.toISOString(),
          duration_s: duration * 60, priority }) });
    } else {
      const name = $("actType").value;
      if (!name) { alert("Select an activity type."); return; }
      res = await fetch("/api/schedule", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name, start_time: startDate.toISOString(),
          duration_s: duration * 60, priority,
          station: $("actStation").value || null }) });
    }
    if (res.ok) { closeModal(); mainPoller.kick(); }
    else {
      const err = await res.json().catch(() => ({}));
      alert("Could not save activity: " + (err.error || ("HTTP " + res.status)));
    }
  } catch (e) {
    alert("Could not save activity — planner API unreachable: " + e);
  }
}

/* ── Activity types / templates / stations ────────────────────────────── */
async function loadActivityTypes() {
  try {
    const res = await fetch("/api/activity-types");
    if (!res.ok) return;
    activityTypes = (await res.json()).activity_types || [];
    if (activityTypes.length) {
      const sel = $("actType");
      sel.innerHTML = "<option value=\"\">Select activity type…</option>";
      for (const at of activityTypes) {
        const opt = document.createElement("option");
        opt.value = at.name;
        opt.textContent = at.name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
        sel.appendChild(opt);
      }
    }
  } catch (e) { /* keep static options */ }
}

function onActivityTypeChange() {
  const name = $("actType").value;
  const infoEl = $("actTypeInfo");
  const at = activityTypes.find((a) => a.name === name);
  if (!at) { infoEl.style.display = "none"; return; }
  infoEl.style.display = "block";
  const dur = Math.round((at.duration_s || 0) / 60);
  infoEl.innerHTML = esc(at.description || "") + "<br>Duration: " + dur
    + " min | Power: " + (at.power_w || 0) + " W | Procedure: "
    + esc(at.procedure_ref || "N/A")
    + (at.pre_conditions && at.pre_conditions.length
      ? "<br>Pre-conditions: " + esc(at.pre_conditions.join(", ")) : "");
  $("actDuration").value = dur;
  $("actPriority").value = at.priority || "medium";
}

async function loadPlanTemplates() {
  try {
    const res = await fetch("/api/schedule/templates");
    if (!res.ok) return;
    const data = await res.json();
    planTemplates = data.templates || [];
    const sel = $("templateSelect");
    sel.innerHTML = "<option value=\"\">Standard activities…</option>";
    for (const t of planTemplates) {
      const opt = document.createElement("option");
      opt.value = t.id; opt.textContent = t.name + " (" + t.activity_count + ")";
      opt.title = t.description || "";
      if (t.id === data.default_template) opt.selected = true;
      sel.appendChild(opt);
    }
  } catch (e) { /* dropdown stays empty */ }
}

async function loadStandardActivities() {
  const id = $("templateSelect").value;
  if (!id) { alert("Select a standard activity set first."); return; }
  try {
    const res = await fetch("/api/schedule/load-template", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template: id }) });
    const data = await res.json();
    if (!res.ok) { alert("Could not load \"" + id + "\": " + (data.error || res.status)); return; }
    let msg = "Loaded " + data.added + " activities from \"" + data.template + "\" at " + data.t0;
    if (data.warnings && data.warnings.length) msg += "\n\n" + data.warnings.join("\n");
    alert(msg);
    mainPoller.kick();
  } catch (e) { alert("Could not load standard activities: " + e); }
}

async function clearSchedule() {
  if (!confirm("Remove every planned activity?")) return;
  try {
    const res = await fetch("/api/schedule", { method: "DELETE" });
    const data = await res.json();
    if (data.removed !== undefined) mainPoller.kick();
  } catch (e) { alert("Could not clear the schedule: " + e); }
}

async function loadGroundStations() {
  try {
    const res = await fetch("/api/ground-stations");
    if (!res.ok) throw new Error("HTTP " + res.status);
    groundStations = (await res.json()).ground_stations || [];
    groundStationsError = groundStations.length === 0;
  } catch (e) {
    groundStations = []; groundStationsError = true;
  }
  const sel = $("actStation");
  while (sel.options.length > 1) sel.remove(1);
  for (const gs of groundStations) {
    const opt = document.createElement("option");
    opt.value = gs.name; opt.textContent = gs.name;
    sel.appendChild(opt);
  }
  updateGroundStationMarkers();
}

/* ── Validate / upload ────────────────────────────────────────────────── */
async function validateSchedule() {
  const el = $("scheduleValidation");
  el.style.display = "block";
  try {
    const res = await fetch("/api/schedule/validate");
    const data = await res.json();
    if (data.valid) {
      el.className = "ok";
      el.textContent = "Schedule valid (" + data.activity_count + " activities, no conflicts)";
    } else {
      el.className = "bad";
      el.innerHTML = data.issues.map((i) =>
        "<div>" + esc(i.type) + ": " + esc(i.message)
        + " (activity #" + esc(i.activity_id) + ")</div>").join("");
    }
    el.style.display = "block";
    setTimeout(() => { el.style.display = "none"; }, 8000);
  } catch (e) {
    el.className = "err"; el.textContent = "Validation failed: " + e;
  }
}

async function uploadToMCS() {
  const planned = schedule.filter((a) => a.state_name === "PLANNED" || !a.state_name);
  if (!planned.length) { alert("No planned activities to upload"); return; }
  const act = planned[0];
  if (!act.id) { alert("Activity has no ID"); return; }
  const useS11 = confirm(
    "Schedule on-board (S11) at planned start time " + act.start_time
    + "?\nOK = time-tagged on-board schedule.\nCancel = load as immediate MCS procedure.");
  let stepMode = false;
  if (!useS11) stepMode = confirm("Use step-by-step mode?");
  try {
    const res = await fetch("/api/schedule/upload", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        activity_id: act.id, mode: useS11 ? "s11" : "immediate",
        step_by_step: stepMode }) });
    const data = await res.json();
    if (data.error) { alert("Upload failed: " + data.error); return; }
    if (useS11) {
      alert("Activity \"" + act.name + "\" scheduled on board: "
        + (data.count !== undefined ? data.count : "?")
        + " time-tagged command(s) at " + act.start_time
        + (data.skipped && data.skipped.length
          ? "\n(" + data.skipped.length + " telemetry-check step(s) skipped — ground-only)" : ""));
    } else {
      alert("Activity \"" + act.name + "\" uploaded to MCS. "
        + "Switch to the MCS Procedures page to execute.");
    }
    mainPoller.kick();
  } catch (e) { alert("Upload failed: " + e); }
}

/* ── Constraint validation (P5: explicit SoC, per-domain accordion) ───── */
function constraintUrl() {
  const v = $("socInput").value.trim();
  // Blank = server default: the request carries NO battery_soc parameter.
  if (v === "") return "/api/constraints/validate";
  return "/api/constraints/validate?battery_soc=" + encodeURIComponent(v);
}

async function validateConstraints() {
  const el = $("constraintsResult");
  el.innerHTML = "<span style=\"color:var(--tx3)\">Checking…</span>";
  try {
    const res = await fetch(constraintUrl());
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || ("HTTP " + res.status));
    const head = data.valid
      ? "<div class=\"v\" style=\"color:var(--tx2);font-weight:700\">VALID — no constraint violations</div>"
      : "<div class=\"v error\" style=\"font-weight:700\">NOT VALID — "
        + (data.error_count ?? "?") + " error(s), " + (data.warning_count ?? "?") + " warning(s)</div>";
    const rows = (data.violations || []).slice(0, 12).map((v) =>
      "<div class=\"v " + esc(v.severity || "") + "\">[" + esc((v.severity || "?").toUpperCase())
      + "] " + esc(v.activity_name || "System") + ": " + esc(v.message || "") + "</div>").join("");
    el.innerHTML = head + rows;
  } catch (e) {
    el.innerHTML = "<div class=\"v error\">Constraint validation failed: " + esc(String(e)) + "</div>";
  }
}

function renderDomainResult(data) {
  const violations = data.violations || data.issues || [];
  const rows = violations.slice(0, 10).map((v) =>
    "<div style=\"color:var(--warn)\">" + esc(v.severity ? "[" + v.severity.toUpperCase() + "] " : "")
    + esc(v.activity_name || v.type || "") + (v.activity_name || v.type ? ": " : "")
    + esc(v.message || JSON.stringify(v)) + "</div>").join("");
  const scalars = Object.entries(data)
    .filter(([k, v]) => (typeof v === "number" || typeof v === "string" || typeof v === "boolean")
      && k !== "error")
    .map(([k, v]) => "<div><span style=\"color:var(--tx3)\">" + esc(k) + "</span> "
      + esc(String(v)) + "</div>").join("");
  return (rows || "<div style=\"color:var(--tx2)\">No violations reported.</div>") + scalars;
}

async function loadDomainConstraint(details) {
  const domain = details.dataset.domain;
  const bd = details.querySelector(".bd");
  bd.textContent = "Checking…";
  try {
    const res = await fetch("/api/constraints/" + domain);
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || ("HTTP " + res.status));
    bd.innerHTML = renderDomainResult(data);
  } catch (e) {
    bd.innerHTML = "<span style=\"color:var(--alarm)\">Check failed: " + esc(String(e)) + "</span>";
  }
}

/* ── Power budget ─────────────────────────────────────────────────────── */
async function loadPowerBudget() {
  try {
    const res = await fetch("/api/budget/power");
    if (!res.ok) return;
    renderPowerBudget(await res.json());
  } catch (e) { /* keep last render */ }
}

function renderPowerBudget(b) {
  const panel = $("powerBudgetPanel");
  const warnings = b.warnings || [];
  // "Cannot compute" is a RESULT, not a zero — render it as a designed
  // state, never a confident 0 % / 100 % (server contract, plan §2.2).
  if (b.computable === false || b.final_soc === null || b.final_soc === undefined) {
    const reason = b.reason || warnings[0]
      || "the planner returned no state-of-charge prediction";
    panel.innerHTML = "<div class=\"nocompute\">"
      + "<div class=\"t\">POWER BUDGET CANNOT BE COMPUTED</div>"
      + "<div class=\"r\">" + esc(reason) + "</div></div>";
    return;
  }
  const minSoc = (plannerConfig && plannerConfig.budget
    && plannerConfig.budget.min_soc_percent) ?? null;
  const cls = (soc) => minSoc === null ? ""
    : soc < minSoc ? "alarm" : soc < minSoc + 10 ? "warn" : "";
  let html = "";
  if (b.soc_saturated) {
    html += "<div class=\"ds-banner warn\" style=\"margin-bottom:8px\">SoC saturated against a battery rail ("
      + (b.soc_floor_hits || 0) + " at 0 %, " + (b.soc_ceiling_hits || 0)
      + " at 100 %) — the figure below is a limit, not a prediction.</div>";
  }
  html += "<div class=\"tiles\">"
    + "<div class=\"tile\"><div class=\"k\">INITIAL SoC</div><div class=\"v num\">"
    + (b.initial_soc ?? 0).toFixed(1) + "%</div></div>"
    + "<div class=\"tile\"><div class=\"k\">FINAL SoC (horizon)</div><div class=\"v num "
    + cls(b.final_soc) + "\">" + b.final_soc.toFixed(1) + "%</div></div></div>";
  if (minSoc !== null) {
    const w = Math.max(0, Math.min(100, b.final_soc));
    html += "<div class=\"fillbar " + (cls(b.final_soc) === "alarm" ? "a" : cls(b.final_soc) === "warn" ? "w" : "")
      + "\" title=\"final SoC vs configured minimum " + minSoc + "%\">"
      + "<div class=\"fl\" style=\"width:" + w + "%\"></div></div>"
    + "<div class=\"kv-grid\"><b>min allowed</b><span>" + minSoc + "%</span>"
    + "<b>charge / drain</b><span>" + (b.total_charge_wh ?? "—") + " / "
    + (b.total_drain_wh ?? "—") + " Wh</span></div>";
  }
  const passes = b.pass_predictions || [];
  if (passes.length) {
    html += "<div class=\"sect\">PASS SoC TIMELINE</div>";
    for (const p of passes.slice(0, 5)) {
      html += "<div class=\"kv-grid\" style=\"grid-template-columns:1.2fr 1fr 1fr;"
        + "border-bottom:1px solid var(--line);padding:3px 0\">"
        + "<span style=\"font-family:var(--sans)\">" + esc(p.gs_name) + "</span>"
        + "<span>" + p.soc_at_aos.toFixed(1) + "%</span>"
        + "<span class=\"" + (cls(p.soc_at_los) ? cls(p.soc_at_los) : "") + "\" style=\"font-weight:600;"
        + (cls(p.soc_at_los) === "alarm" ? "color:var(--alarm)" : cls(p.soc_at_los) === "warn" ? "color:var(--warn)" : "")
        + "\">" + p.soc_at_los.toFixed(1) + "%</span></div>";
    }
  }
  if (warnings.length) {
    html += "<div class=\"warns\">" + warnings.slice(0, 3).map((w) =>
      "<div>⚠ " + esc(String(w).substring(0, 110)) + "</div>").join("") + "</div>";
  }
  panel.innerHTML = html;
}

/* ── Data budget + fit verdicts (G3 planner half) ─────────────────────── */
/* Joins the budget tracker's per-pass achievable capacity (served on
 * /api/budget/data pass_downlink) with the schedule's dump activities:
 * backlog to move vs what the pass can carry. Computed entirely from the
 * planner's own budget machinery — the MCS is never called.
 *
 * backlog at time T = onboard now
 *                   + generation planned before T (non-dump activities)
 *                   - downlink the tracker already credits to passes that
 *                     END before T (planned_dump_mb), clamped at 0.
 * fit ratio = achievable / backlog (the planner-side analogue of the MCS
 * derived ttc.pass_fit_ratio). >= 1 fits; < 1 will not fit. */
function computeFitVerdicts(d) {
  if (!d || !Array.isArray(d.pass_downlink)) return null;
  const now = simNow();
  const passes = d.pass_downlink.map((p) => ({
    ...p,
    aosMs: new Date(p.aos).getTime(),
    losMs: new Date(p.los).getTime() }));
  const acts = schedule.filter((a) => {
    const st = a.state_name || "PLANNED";
    return st !== "CANCELLED" && st !== "FAILED";
  });
  const isDump = (a) => /dump|downlink/i.test(a.name || "");
  const genBefore = (tMs) => acts
    .filter((a) => !isDump(a) && a.start.getTime() < tMs)
    .reduce((s, a) => s + (Number(a.data_volume_mb) || 0), 0);
  const dumpedBefore = (tMs) => passes
    .filter((p) => p.dump_scheduled && p.losMs <= tMs)
    .reduce((s, p) => s + (Number(p.planned_dump_mb) || 0), 0);
  const backlogAt = (tMs) => Math.max(
    0, (Number(d.onboard_data_mb) || 0) + genBefore(tMs) - dumpedBefore(tMs));

  const perPass = new Map();
  for (const p of passes) {
    if (!p.dump_scheduled || p.losMs < now) continue;
    const backlogMb = backlogAt(p.aosMs);
    const capacityMb = Number(p.capacity_mb) || 0;
    perPass.set(p.pass_id, {
      backlogMb, capacityMb,
      ratio: backlogMb > 0 ? capacityMb / backlogMb : null,
      fits: backlogMb <= capacityMb + 1e-6 });
  }

  const dumps = [];
  for (const a of acts) {
    if (!isDump(a)) continue;
    const startMs = a.start.getTime();
    const endMs = startMs + (a.durationMin || 0) * 60000;
    if (endMs < now) continue;
    const over = passes.filter((p) => p.aosMs < endMs && p.losMs > startMs);
    const achievableMb = over.reduce(
      (s, p) => s + (Number(p.capacity_mb) || 0), 0);
    const backlogMb = backlogAt(startMs);
    dumps.push({
      name: a.name, startMs,
      station: over.map((p) => p.gs_name).join(" + ") || null,
      inPass: over.length > 0,
      achievableMb, backlogMb,
      ratio: backlogMb > 0 ? achievableMb / backlogMb : null,
      fits: over.length > 0 && backlogMb <= achievableMb + 1e-6 });
  }
  return { perPass, dumps };
}

function fitChip(v) {
  if (!v) return "<span style=\"color:var(--tx3)\">—</span>";
  if (v.backlogMb <= 1e-6) {
    return "<span style=\"color:var(--tx2)\">FIT (no backlog)</span>";
  }
  const r = v.ratio === null ? null : v.ratio.toFixed(2);
  return v.fits
    ? "<span style=\"color:var(--tx2)\">FIT " + r + "&times;</span>"
    : "<span style=\"color:var(--alarm);font-weight:700\">WILL NOT FIT"
      + (r !== null ? " " + r + "&times;" : "") + "</span>";
}

async function loadDataBudget() {
  try {
    const res = await fetch("/api/budget/data");
    if (!res.ok) return;
    renderDataBudget(await res.json());
  } catch (e) { /* keep last render */ }
}

function renderDataBudget(d) {
  const panel = $("dataBudgetPanel");
  if (d.utilization_percent === undefined) {
    panel.innerHTML = "<div class=\"nocompute\"><div class=\"t\">DATA BUDGET UNAVAILABLE</div>"
      + "<div class=\"r\">" + esc(d.reason || d.error || "no utilisation figure returned") + "</div></div>";
    return;
  }
  const util = d.utilization_percent || 0;
  const warnAt = (plannerConfig && plannerConfig.budget
    && plannerConfig.budget.warn_utilization_percent) ?? 90;
  const cls = util >= 100 ? "a" : util >= warnAt ? "w" : "";
  let html = "<div class=\"tile\" style=\"margin-bottom:8px\">"
    + "<div class=\"k\">STORAGE UTILISATION</div>"
    + "<div class=\"fillbar " + cls + "\"><div class=\"fl\" style=\"width:"
    + Math.min(100, util) + "%\"></div></div>"
    + "<div class=\"kv-grid\"><b>onboard</b><span>" + (d.onboard_data_mb || 0).toFixed(0)
    + " / " + (d.storage_capacity_mb || 0).toFixed(0) + " MB</span>"
    + "<b>fill</b><span>" + util.toFixed(1) + "%</span></div></div>";
  html += "<div class=\"kv-grid\">"
    + "<b>generate</b><span>" + (d.planned_generation_mb ?? 0).toFixed(0) + " MB</span>"
    + "<b>downlink</b><span>" + (d.planned_downlink_mb ?? 0).toFixed(0) + " MB</span>"
    + "<b>peak fill</b><span>" + (d.peak_onboard_mb ?? 0).toFixed(0) + " MB"
    + (d.peak_utilization_percent !== undefined ? " (" + d.peak_utilization_percent.toFixed(1) + "%)" : "")
    + "</span><b>backlog</b><span>" + (d.backlog_growth_mb ?? 0).toFixed(0) + " MB</span>"
    + "<b>pass capacity</b><span>" + (d.daily_downlink_capacity_mb ?? 0).toFixed(0) + " MB/day</span>"
    + "<b>rate (eff.)</b><span>" + ((d.effective_downlink_rate_bps || 0) / 1000).toFixed(1) + " kbps</span>"
    + "</div>";
  html += "<div style=\"margin-top:6px;font-size:12px;font-weight:700;color:"
    + (d.plan_closes === false ? "var(--alarm)" : "var(--tx2)") + "\">"
    + (d.plan_closes === false
      ? "PLAN DOES NOT CLOSE" + ((d.days_to_drain || 0) > 0
        ? " — " + d.days_to_drain.toFixed(1) + " days of contact needed to drain" : "")
      : "Plan closes") + "</div>";
  if (d.first_overflow_utc) {
    html += "<div style=\"margin-top:4px;font-size:12px;color:var(--alarm)\">First overflow: "
      + esc(d.first_overflow_utc) + " (" + (d.overflow_mb || 0).toFixed(0) + " MB lost)</div>";
  }
  // G3: fit verdicts from the planner's own budget machinery.
  const fit = computeFitVerdicts(d);
  if (fit && fit.dumps.length) {
    html += "<div class=\"sect\">DUMP FIT — ACHIEVABLE vs BACKLOG</div>";
    for (const v of fit.dumps.slice(0, 5)) {
      const t = new Date(v.startMs);
      const hm = String(t.getUTCHours()).padStart(2, "0") + ":"
        + String(t.getUTCMinutes()).padStart(2, "0");
      html += "<div class=\"kv-grid\" style=\"grid-template-columns:1.3fr 1fr 1fr;"
        + "border-bottom:1px solid var(--line);padding:3px 0\">"
        + "<span style=\"font-family:var(--sans)\">" + esc(v.name) + " " + hm + "Z"
        + (v.station ? " · " + esc(v.station) : "") + "</span>"
        + "<span>" + v.achievableMb.toFixed(1) + " / "
        + v.backlogMb.toFixed(1) + " MB</span>"
        + "<span>" + (v.inPass ? fitChip(v)
          : "<span style=\"color:var(--alarm);font-weight:700\">WILL NOT FIT"
            + " — outside every pass</span>") + "</span></div>";
    }
  }
  const passes = d.pass_downlink || [];
  if (passes.length) {
    html += "<div class=\"sect\">PER-PASS DOWNLINK (NEXT 5)</div>";
    for (const p of passes.slice(0, 5)) {
      const v = fit ? fit.perPass.get(p.pass_id) : null;
      html += "<div class=\"kv-grid\" style=\"grid-template-columns:1.2fr .9fr .9fr .8fr .9fr;"
        + "border-bottom:1px solid var(--line);padding:3px 0\">"
        + "<span style=\"font-family:var(--sans)\">" + esc(p.gs_name) + "</span>"
        + "<span>" + (p.duration_s / 60).toFixed(1) + " min</span>"
        + "<span>" + p.capacity_mb.toFixed(1) + " MB cap</span>"
        + "<span>" + (p.dump_scheduled ? p.planned_dump_mb.toFixed(1) + " MB" : "—") + "</span>"
        + "<span>" + (p.dump_scheduled ? fitChip(v)
          : "<span style=\"color:var(--tx3)\">no dump</span>") + "</span></div>";
    }
  }
  const warnings = d.warnings || [];
  if (warnings.length) {
    html += "<div class=\"warns\">" + warnings.map((w) =>
      "<div>⚠ " + esc(String(w).substring(0, 110)) + "</div>").join("") + "</div>";
  }
  panel.innerHTML = html;
}

/* ── Last-pass RF rollup (G14 display half) ───────────────────────────── */
/* Reads the rfsim pass-rollup store cross-origin at the CONFIGURED
 * services.rfsim_url (planning/planner.yaml — additive, default unset).
 * No URL configured is a DESIGNED absent state; unreachable renders an
 * explicit notice — never stale numbers, never a guessed host. */
async function loadPassRollups() {
  const el = $("passRollupPanel");
  if (!el) return;
  const base = services && services.rfsim_url;
  if (!base) {
    el.innerHTML = "<span style=\"font-size:12px;color:var(--tx3)\">"
      + "No rfsim URL configured (planning/planner.yaml "
      + "services.rfsim_url) — achieved pass rollups are not shown.</span>";
    return;
  }
  try {
    const res = await fetch(serviceUrl(base, "api/pass-rollups?limit=5"));
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    const rollups = data.rollups || [];
    const last = rollups.length ? rollups[rollups.length - 1] : null;
    let html = "";
    if (!last) {
      html += "<span style=\"font-size:12px;color:var(--tx3)\">"
        + "No completed passes recorded yet.</span>";
    } else {
      const lock = last.locked
        ? ((last.lock_delay_s === null || last.lock_delay_s === undefined)
          ? "locked" : "+" + Number(last.lock_delay_s).toFixed(1) + " s")
        : "<span style=\"color:var(--alarm)\">no lock</span>";
      const eb = (last.mean_eb_n0_db === null || last.mean_eb_n0_db === undefined)
        ? "—" : Number(last.mean_eb_n0_db).toFixed(1) + " dB";
      const fer = (last.fer === null || last.fer === undefined)
        ? "—" : Number(last.fer).toFixed(4);
      const vol = (last.data_volume_bytes === null
        || last.data_volume_bytes === undefined)
        ? "—" : (Number(last.data_volume_bytes) / 1024).toFixed(1) + " kB";
      html += "<div class=\"kv-grid\">"
        + "<b>station</b><span>" + esc(last.gs_name || "?") + "</span>"
        + "<b>lock delay</b><span>" + lock + "</span>"
        + "<b>mean Eb/N0</b><span>" + eb + "</span>"
        + "<b>FER</b><span>" + fer + "</span>"
        + "<b>volume</b><span>" + vol + "</span>"
        + "<b>duration</b><span>"
        + (last.duration_s !== null && last.duration_s !== undefined
          ? Number(last.duration_s).toFixed(0) + " s" : "—") + "</span>"
        + "</div>";
    }
    if (data.active) {
      html += "<div style=\"margin-top:4px;font-size:12px;color:var(--info)\">"
        + "Pass in progress: " + esc(data.active.gs_name || "?")
        + (data.active.first_lock_ts === null ? " (not locked yet)" : " (locked)")
        + "</div>";
    }
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = "<span style=\"font-size:12px;color:var(--warn)\">"
      + "rfsim at " + esc(String(base))
      + " unreachable — no achieved rollup.</span>";
  }
}

/* ── Imaging: targets list (new UI client) + opportunities ────────────── */
async function loadImagingTargets() {
  try {
    const res = await fetch("/api/imaging/targets");
    if (!res.ok) return;
    const targets = (await res.json()).targets || [];
    const el = $("imagingTargets");
    if (!targets.length) {
      el.innerHTML = "<span style=\"font-size:12px;color:var(--tx3)\">No imaging targets configured.</span>";
      return;
    }
    el.innerHTML = targets.map((t) => {
      const nm = t.name || t.target_id || t.id || "?";
      const where = (t.lat !== undefined && t.lon !== undefined)
        ? t.lat.toFixed(2) + "°, " + t.lon.toFixed(2) + "°"
        : (t.lat_min !== undefined
          ? t.lat_min.toFixed(1) + "…" + (t.lat_max ?? 0).toFixed(1) + "°" : "");
      return "<div class=\"tgt\"><span class=\"n\">" + esc(nm) + "</span>"
        + "<span>" + esc(where) + "</span>"
        + "<span>" + esc(t.priority || "") + "</span></div>";
    }).join("");
  } catch (e) { /* keep last */ }
}

async function loadImagingOpportunities() {
  try {
    const res = await fetch("/api/imaging/opportunities");
    if (!res.ok) return;
    imagingOpps = (await res.json()).opportunities || [];
    renderImagingOpps();
  } catch (e) { /* keep last */ }
}

function renderImagingOpps() {
  const panel = $("imagingOpportunitiesPanel");
  if (!imagingOpps.length) {
    panel.innerHTML = "<span style=\"font-size:12px;color:var(--tx3)\">No opportunities in the horizon.</span>";
    return;
  }
  panel.innerHTML = imagingOpps.slice(0, 8).map((opp) => {
    const captureUtc = opp.capture_utc || opp.start_utc;
    const dur = Math.round((opp.duration_s || 0) / 60);
    const aim = (opp.access_lat !== undefined)
      ? opp.access_lat.toFixed(2) + "°, " + opp.access_lon.toFixed(2) + "°" : "";
    return "<div class=\"opp " + (opp.priority === "high" ? "high" : "") + "\">"
      + "<div class=\"nm\">" + esc(opp.target_name) + "</div>"
      + "<div class=\"meta\">" + fmtUTC(captureUtc) + "Z · " + dur + " min access</div>"
      + "<div class=\"meta\">aim " + esc(aim) + " · off-nadir "
      + (opp.off_nadir_deg !== undefined ? opp.off_nadir_deg.toFixed(1) : "?")
      + "° · sun " + (opp.solar_elevation_deg !== undefined
        ? opp.solar_elevation_deg.toFixed(0) : "?") + "°</div>"
      + "<button class=\"ds-btn\" style=\"margin-top:4px\" data-act=\"schedule-imaging\""
      + " data-target=\"" + esc(opp.target_id) + "\" data-start=\"" + esc(captureUtc)
      + "\">SCHEDULE</button></div>";
  }).join("");
}

async function scheduleImagingActivity(targetId, startTime) {
  try {
    const res = await fetch("/api/imaging/schedule", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_id: targetId, start_time: startTime }) });
    if (res.ok) { alert("Imaging activity scheduled."); mainPoller.kick(); }
    else {
      const err = await res.json().catch(() => ({}));
      alert("Failed to schedule imaging activity: " + (err.error || res.status));
    }
  } catch (e) { alert("Error scheduling imaging activity: " + e); }
}

/* ── Wiring + init ────────────────────────────────────────────────────── */
const mainPoller = poller(pollAPIs, 30000);            // 30 s main poll
const scPoller = poller(loadSpacecraftState, 5000);    // 5 s position
const tsPoller = poller(loadTimeSource, 5000);         // 5 s clock anchor
const trackPoller = poller(loadGroundTrack, 120000);   // 120 s track

function wire() {
  $("btnAddActivity").addEventListener("click", () => openModal(null));
  $("btnModalClose").addEventListener("click", closeModal);
  $("btnModalSubmit").addEventListener("click", submitActivity);
  $("modalOverlay").addEventListener("click", (e) => {
    if (e.target === $("modalOverlay")) closeModal();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  $("actType").addEventListener("change", onActivityTypeChange);
  $("actStartTime").addEventListener("input", updateStartTimeHint);
  $("actStartTime").addEventListener("change", updateStartTimeHint);
  $("btnLoadTemplate").addEventListener("click", loadStandardActivities);
  $("btnClearSchedule").addEventListener("click", clearSchedule);
  $("btnValidate").addEventListener("click", validateSchedule);
  $("btnUpload").addEventListener("click", uploadToMCS);
  $("btnValidateConstraints").addEventListener("click", validateConstraints);
  for (const details of document.querySelectorAll("details.cx")) {
    details.addEventListener("toggle", () => {
      if (details.open) loadDomainConstraint(details);
    });
  }
  // Delegated row actions (rows are re-rendered every poll).
  $("activityList").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (btn.dataset.act === "delete") deleteActivity(id);
    if (btn.dataset.act === "edit") {
      const a = schedule.find((x) => Number(x.id) === id);
      if (a) openModal(a);
    }
  });
  $("imagingOpportunitiesPanel").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act=\"schedule-imaging\"]");
    if (btn) scheduleImagingActivity(btn.dataset.target, btn.dataset.start);
  });
  window.addEventListener("resize", drawTimeline);
}

async function init() {
  wire();
  updateClock();
  setInterval(updateClock, 1000);                       // 1 s clock
  loadActivityTypes();
  loadPlanTemplates();
  try { await loadPlannerConfig(); } catch (e) { /* readouts stay em-dash */ }
  // Clock + stations before first paint (hard deps of the timeline).
  await loadTimeSource();
  await loadGroundStations();
  await initMap();
  await loadGroundTrack();
  drawTimeline(); updateContactsTable(); updateActivityList();
  mainPoller.start(); scPoller.start(); tsPoller.start(); trackPoller.start();
  setInterval(() => { updateContactsTable(); drawTimeline(); }, 10000);  // 10 s
}

// roundRect polyfill (older classroom browsers — preserved from classic).
if (!CanvasRenderingContext2D.prototype.roundRect) {
  CanvasRenderingContext2D.prototype.roundRect = function (x, y, w, h, r) {
    if (typeof r === "number") r = [r, r, r, r];
    else if (typeof r === "undefined") r = [0, 0, 0, 0];
    this.moveTo(x + r[0], y);
    this.lineTo(x + w - r[1], y);
    this.quadraticCurveTo(x + w, y, x + w, y + r[1]);
    this.lineTo(x + w, y + h - r[2]);
    this.quadraticCurveTo(x + w, y + h, x + w - r[2], y + h);
    this.lineTo(x + r[3], y + h);
    this.quadraticCurveTo(x, y + h, x, y + h - r[3]);
    this.lineTo(x, y + r[0]);
    this.quadraticCurveTo(x, y, x + r[0], y);
    this.closePath();
    return this;
  };
}

document.addEventListener("DOMContentLoaded", init);

/* ============================================================================
 * SMO DESIGN SYSTEM — components.js
 * ============================================================================
 * The shared component vocabulary for the three modern consoles (MCS,
 * Planner, Instructor). Served by every server at
 * /static/shared/components.js; styled exclusively by design-system.css;
 * glyphs referenced from glyphs.svg.
 *
 * THIS IS A FROZEN CONTRACT (execution plan §3). Agents 2-4 build against
 * these exact names and signatures. Do not rename, remove, reorder or retype
 * an argument. Additive-only evolution (new optional args / new exports).
 *
 * DESIGN RULES (PRINCIPLES.md):
 *  - Pure render functions: every render*() takes plain data and RETURNS AN
 *    HTML STRING (or SVG string). No DOM reads, no fetch, no globals, no
 *    Date.now(). Callers own insertion and diffing.
 *  - One idiom per concept (P5): there is exactly one stepper, one chip, one
 *    param triad, one AND table. Pages must not fork these.
 *  - Colour is never the only cue (P6): every state emitted here carries a
 *    glyph and/or text label alongside its status class.
 *  - Stale must never look like steady (P4): renderers take ages/staleness
 *    explicitly and emit the hatch + em-dash affordance.
 *
 * DATA CONTRACT SUMMARY (which endpoint feeds which argument):
 *  - `def`    — one entry of MCS GET /api/param-catalog `parameters[]`:
 *               {id, hex, name, subsystem, units, description, in_hk,
 *                on_demand, hk_sids, hk_interval_s}.
 *  - `limits` — one band of MCS GET /api/limits `limits["0xHHHH"]`:
 *               {yellow_low, yellow_high, red_low, red_high, name,
 *                subsystem, units}; absent/null band = no ground limit
 *               (renders the explicit "no limit defined" affordance).
 *  - `value` / `values` — live numbers from WS `state` message `data.params`
 *               (keys "0xHHHH") with per-parameter ages in `data.params_age`
 *               (keys are DECIMAL id strings — quirk preserved from classic)
 *               and limit states in `data.param_limit_state` ("0xHHHH" ->
 *               "nominal"|"yellow_low"|"yellow_high"|"red_low"|"red_high";
 *               "unlimited" appears only from /api/limits `state`).
 *  - `alarm`  — one WS `alarm` message payload / one row of GET /api/alarms
 *               `alarms[]` (see renderAlarmRow).
 *  - `evt`    — one MCS event entry (WS `event` messages / event log rows).
 * ============================================================================ */

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** esc(s) — HTML-escape a string. Exported because pages composing their own
 *  markup around these components need the same escaping discipline. */
export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
}

/** Path to the shared glyph sprite; change here only. */
export const GLYPHS = "/static/shared/glyphs.svg";

/** glyph(name, cls) — inline <svg><use> for a glyphs.svg symbol id
 *  (without the "g-" prefix): glyph("warn-up") -> #g-warn-up. */
export function glyph(name, cls = "") {
  return `<svg class="${esc(cls)}" aria-hidden="true" width="11" height="11">` +
         `<use href="${GLYPHS}#g-${esc(name)}"></use></svg>`;
}

/* ── FRESHNESS ─────────────────────────────────────────────────────────── */

/**
 * freshness(ageS) -> "fresh" | "degraded" | "stale"
 *
 * The display-wide freshness policy (P4), promoted from the classic charts:
 * fresh < 5 s, degraded 5-15 s, stale > 15 s. `null`/`undefined` (never
 * seen) is "stale".
 *
 * @param {number|null|undefined} ageS — SIM-seconds age as served
 *        (`data.params_age[id]`, `data.last_frame_age_s`,
 *        `data.subsystem_staleness[sub]`, `data.hk_sid_age_s[sid]`).
 *        Do not re-age client-side with Date.now().
 */
export function freshness(ageS) {
  if (ageS === null || ageS === undefined || !Number.isFinite(Number(ageS))) {
    return "stale";
  }
  const a = Number(ageS);
  if (a < 5) return "fresh";
  if (a <= 15) return "degraded";
  return "stale";
}

/* Map a limit-state string ("nominal"/"yellow_*"/"red_*"/"unlimited") to the
   design-system modifier class ("", "w", "a"). Internal + exported. */
export function limitClass(state) {
  if (!state) return "";
  if (state.startsWith("red")) return "a";
  if (state.startsWith("yellow")) return "w";
  return "";
}

/* ── 1. PARAMETER TRIAD (P3) ───────────────────────────────────────────── */

/**
 * renderParam(def, value, limits) -> html string
 *
 * The one way a scalar with limits is displayed: name + numeral + unit +
 * deviation bar. Parameters WITHOUT limits render a visible "no limit
 * defined" line instead of a bar (P3).
 *
 * @param {object} def    — /api/param-catalog entry (see header). Only
 *        `name`, `hex`, `units` are required; `description` becomes the
 *        title attribute.
 * @param {number|null|undefined} value — live value from WS state
 *        `data.params[def.hex]`. null/undefined => stale em-dash rendering.
 * @param {object|null} [limits] — /api/limits band for def.hex, optionally
 *        extended by the caller with:
 *          state {string}  — data.param_limit_state[def.hex] (else derived
 *                            from the band client-side),
 *          age_s {number}  — data.params_age[String(def.id)],
 *          lo/hi {number}  — display range override for the bar.
 * @returns {string} `<div class="ds-param ...">...`
 */
export function renderParam(def, value, limits) {
  const d = def || {};
  const hasVal = value !== null && value !== undefined && Number.isFinite(Number(value));
  const band = _band(limits);
  const state = (limits && limits.state) || (band && hasVal ? _classify(Number(value), band) : "");
  const fr = limits && "age_s" in limits ? freshness(limits.age_s) : (hasVal ? "fresh" : "stale");
  const stale = fr === "stale" || !hasVal;
  const lc = stale ? "" : limitClass(state);
  const cls = ["ds-param", lc, stale ? "stale" : "", fr === "degraded" ? "degraded" : ""]
    .filter(Boolean).join(" ");
  // `def.eng` — the engineering-value string for a DISCRETE parameter
  // ("COARSE_SUN (2)"). It is carried separately from `value` on purpose:
  // `value` stays numeric so limits, banding and the deviation bar keep
  // working, and only the rendered text changes. Passing the string as
  // `value` instead makes Number() NaN, which trips `hasVal` and renders the
  // parameter as "—" with a stale class — i.e. a mode chip that shows nothing.
  const vTxt = stale ? "—" : (d.eng !== undefined && d.eng !== null
                              ? esc(String(d.eng)) : _num(Number(value)));
  const unit = d.units ? `<u>${esc(d.units)}</u>` : "";
  const ageChip = fr === "degraded" && limits && "age_s" in limits
    ? ` <span class="ds-age degraded">${esc(_age(limits.age_s))}</span>` : "";
  const bar = band
    ? renderDeviationBar(hasVal && !stale ? Number(value) : null, limits)
    : `<span class="nolimit">no limit defined</span>`;
  return `<div class="${cls}" data-param="${esc(d.hex || "")}"` +
         (d.description ? ` title="${esc(d.description)}"` : "") + `>` +
         `<span class="nm">${esc(d.name || d.hex || "?")}` +
         `<small>${esc(d.hex || "")}</small></span>` +
         `<span class="vl">${vTxt}${unit}${ageChip}</span>` +
         bar +
         `</div>`;
}

/* ── 2. DEVIATION BAR (P3) ─────────────────────────────────────────────── */

/**
 * renderDeviationBar(value, limits) -> html string
 *
 * Analogue position-in-range bar with the yellow/red boundaries drawn as
 * ticks. Tick labels are revealed on hover/focus — handled by CSS, nothing
 * to do here.
 *
 * @param {number|null} value — live value; null => needle omitted (stale).
 * @param {object|null} limits — /api/limits band {yellow_low, yellow_high,
 *        red_low, red_high} (any subset non-null), plus optional lo/hi
 *        display-range override and units. With no band at all, returns the
 *        "no limit defined" affordance so callers can use this standalone.
 * @returns {string} `<div class="ds-devbar ...">...`
 */
export function renderDeviationBar(value, limits) {
  const band = _band(limits);
  if (!band) return `<span class="nolimit">no limit defined</span>`;
  const { yellow_low: yl, yellow_high: yh, red_low: rl, red_high: rh } = band;
  // Display range: explicit lo/hi, else pad the outermost defined bound.
  const bounds = [yl, yh, rl, rh].filter((v) => v !== null && v !== undefined);
  let lo = limits.lo, hi = limits.hi;
  if (lo === undefined || hi === undefined) {
    const mn = Math.min(...bounds, value ?? Infinity);
    const mx = Math.max(...bounds, value ?? -Infinity);
    const pad = (mx - mn || Math.abs(mx) || 1) * 0.15;
    if (lo === undefined) lo = mn - pad;
    if (hi === undefined) hi = mx + pad;
  }
  const pct = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
  let segs = "";
  const seg = (a, b, c) =>
    `<span class="seg ${c}" style="left:${pct(a).toFixed(1)}%;width:${(pct(b) - pct(a)).toFixed(1)}%"></span>`;
  if (rl !== null && rl !== undefined) segs += seg(lo, rl, "r");
  if (yl !== null && yl !== undefined) segs += seg(rl ?? lo, yl, "y");
  if (yh !== null && yh !== undefined) segs += seg(yh, rh ?? hi, "y");
  if (rh !== null && rh !== undefined) segs += seg(rh, hi, "r");
  let ticks = "";
  const tick = (v, c) =>
    `<span class="tick ${c}" style="left:${pct(v).toFixed(1)}%"></span>` +
    `<span class="lab" style="left:${pct(v).toFixed(1)}%">${_num(v)}</span>`;
  for (const [v, c] of [[yl, "y"], [yh, "y"], [rl, "r"], [rh, "r"]]) {
    if (v !== null && v !== undefined) ticks += tick(v, c);
  }
  const hasVal = value !== null && value !== undefined && Number.isFinite(Number(value));
  const state = hasVal ? _classify(Number(value), band) : "";
  const needle = hasVal
    ? `<span class="nd" style="left:${pct(Number(value)).toFixed(1)}%"></span>` : "";
  return `<div class="ds-devbar ${limitClass(state)}" tabindex="0" role="img" ` +
         `aria-label="value ${hasVal ? _num(Number(value)) : "unknown"} of range ${_num(lo)} to ${_num(hi)}">` +
         segs + ticks + needle + `</div>`;
}

/* ── 3. SPARKLINE ──────────────────────────────────────────────────────── */

/**
 * renderSparkline(series, opts) -> svg string
 *
 * Chrome-free inline-SVG trend (P5: "sparkline or trend panel, never a
 * third thing"). STOPS APPENDING WHEN STALE: the caller passes the series
 * it holds; when opts.stale is true the line simply ends and a short dashed
 * cut mark is drawn at the last sample — the line is never flat-extended
 * to "now" (P4).
 *
 * @param {number[]} series — newest-last numeric samples (caller-maintained
 *        ring buffer fed from WS state values; recommended cap ~120).
 *        Non-finite entries break the line (gap), which is how HK dropouts
 *        should look.
 * @param {{w?:number,h?:number,stale?:boolean,cls?:string,min?:number,max?:number}} [opts]
 *        w/h — pixel size (default 90x22); stale — freshness(age)==="stale";
 *        cls — extra class ("w"/"a" to colour by limit state);
 *        min/max — fixed y-range (else auto from data).
 * @returns {string} `<svg class="ds-spark ...">...`
 */
export function renderSparkline(series, opts = {}) {
  const { w = 90, h = 22, stale = false, cls = "", min, max } = opts;
  const data = Array.isArray(series) ? series : [];
  const fin = data.filter((v) => Number.isFinite(v));
  if (!fin.length) {
    return `<svg class="ds-spark ${esc(cls)}" width="${w}" height="${h}" aria-hidden="true"></svg>`;
  }
  const lo = min !== undefined ? min : Math.min(...fin);
  const hi = max !== undefined ? max : Math.max(...fin);
  const span = hi - lo || 1;
  const n = data.length;
  const x = (i) => (n === 1 ? w / 2 : (i / (n - 1)) * (w - 2) + 1);
  const y = (v) => h - 2 - ((v - lo) / span) * (h - 4);
  let d = "", pen = false, lastX = null, lastY = null;
  data.forEach((v, i) => {
    if (!Number.isFinite(v)) { pen = false; return; }
    lastX = x(i); lastY = y(v);
    d += `${pen ? "L" : "M"}${lastX.toFixed(1)} ${lastY.toFixed(1)}`;
    pen = true;
  });
  const cut = stale && lastX !== null
    ? `<line class="stale-cut" x1="${lastX.toFixed(1)}" y1="2" ` +
      `x2="${lastX.toFixed(1)}" y2="${h - 2}"></line>` : "";
  return `<svg class="ds-spark ${stale ? "stale " : ""}${esc(cls)}" width="${w}" ` +
         `height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">` +
         `<path d="${d}"></path>${cut}</svg>`;
}

/* ── 4. AND TABLE (SCOS-2000) ──────────────────────────────────────────── */

/**
 * renderAndTable(params, values) -> html string
 *
 * SCOS-2000-style alphanumeric display: one row per parameter with name,
 * description, raw, engineering value, unit, and OOL flag column. Generated
 * from the catalog so it can never drift from the telemetry dictionary (P10).
 *
 * @param {object[]} params — /api/param-catalog entries (typically filtered
 *        to one subsystem: p.subsystem === "eps").
 * @param {object} values — the composite live view the page maintains:
 *        {
 *          params:      WS state data.params            ("0xHHHH" -> number),
 *          ages:        WS state data.params_age        (decimal-id-string -> s),
 *          limitState:  WS state data.param_limit_state ("0xHHHH" -> state),
 *          limits:      GET /api/limits `limits`        ("0xHHHH" -> band),
 *          raw:         optional ("0xHHHH" -> raw count) when the page has
 *                       decommutated raw values; omitted -> raw column shows
 *                       an em-dash.
 *        }
 * @returns {string} `<table class="ds-and">...`
 */
export function renderAndTable(params, values) {
  const v = values || {};
  const rows = (params || []).map((p) => {
    const hex = p.hex || (Number.isFinite(p.id) ? "0x" + p.id.toString(16).toUpperCase().padStart(4, "0") : "");
    const val = v.params ? v.params[hex] : undefined;
    const age = v.ages ? v.ages[String(p.id)] : undefined;
    // `v.verdicts` — the caller's cadence-aware four-state verdict, which also
    // knows about DISABLED HK structures. Without it we fall back to the flat
    // threshold, which cannot distinguish "the link is down" from "nobody
    // enabled this SID", and so shows the last known value as though it were
    // arriving. See configs/eosat1/mcs/staleness.yaml.
    const fr = (v.verdicts && v.verdicts[hex])
      || freshness(age !== undefined ? age : (val !== undefined ? 0 : null));
    const disabled = fr === "disabled";
    const stale = fr === "stale" || disabled;
    const state = (v.limitState && v.limitState[hex]) || "";
    const lc = stale ? "" : limitClass(state);
    const band = v.limits ? v.limits[hex] : null;
    const hasVal = val !== null && val !== undefined && Number.isFinite(Number(val));
    const st = v.states ? v.states[hex] : null;
    const engTxt = disabled ? "NOT ENABLED"
      : stale || !hasVal ? "—"
      : (st && st[String(Math.round(Number(val)))] !== undefined
          ? `${st[String(Math.round(Number(val)))]} (${Math.round(Number(val))})`
          : _num(Number(val)));
    // RAW follows the same gate as ENG. Showing the last received count beside
    // an ENG cell reading "NOT ENABLED" is a mixed message: the operator sees a
    // number in a value column and reads it as current. The value is not lost —
    // it is still in the ground cache, and the Age column says how old it is —
    // but a display column is not the place to assert it.
    const raw = stale || !hasVal ? "—"
      : (v.raw && v.raw[hex] !== undefined ? String(v.raw[hex]) : _num(Number(val)));
    const ool = !band ? "NO LIM"
      : stale || !hasVal ? "—"
      : lc === "a" ? (state.endsWith("high") ? "RH" : "RL")
      : lc === "w" ? (state.endsWith("high") ? "YH" : "YL")
      : "";
    return `<tr class="${[lc, stale ? "stale" : "", disabled ? "disabled" : ""].filter(Boolean).join(" ")}" ` +
           `data-param="${esc(hex)}">` +
           `<td class="id">${esc(hex)}</td>` +
           `<td>${esc(p.name || "")}` +
           (p.description ? `<br><small>${esc(p.description)}</small>` : "") + `</td>` +
           `<td class="raw">${esc(raw)}</td>` +
           `<td class="eng">${esc(engTxt)}</td>` +
           `<td>${esc(p.units || "")}</td>` +
           `<td class="ool">${ool}</td>` +
           `<td class="ds-age${fr === "degraded" ? " degraded" : ""}">${_age(age)}</td>` +
           `</tr>`;
  }).join("");
  return `<table class="ds-and"><thead><tr>` +
         `<th>ID</th><th>Parameter</th><th>Raw</th><th>Eng</th>` +
         `<th>Unit</th><th>OOL</th><th>Age</th>` +
         `</tr></thead><tbody>${rows}</tbody></table>`;
}

/* ── 5. STATE CHIP (P6 glyph vocabulary) ───────────────────────────────── */

/**
 * renderChip(state) -> html string
 *
 * The one binary/enumerated-state idiom: glyph + text label + colour class.
 * Glyph vocabulary (PRINCIPLES P6): warning = hollow amber up/down triangle;
 * alarm = filled red triangle; stale = hatched em-dash; commanded = blue
 * dashed diamond; off = hollow ring; nominal/ok = plain grey (no glyph
 * unless provided).
 *
 * @param {{kind:string, label:string, dir?:"up"|"down", title?:string}} state
 *        kind — "ok"|"warn"|"alarm"|"stale"|"cmd"|"off"|"neutral".
 *        label — the text (ALWAYS present: colour is never the only cue).
 *        dir — for warn/alarm: which side of the band ("up" high, "down"
 *              low; derived from limit state suffix _high/_low).
 *        title — tooltip.
 * @returns {string} `<span class="ds-chip ...">...`
 */
export function renderChip(state) {
  const s = state || {};
  const kind = s.kind || "neutral";
  const dir = s.dir === "down" ? "down" : "up";
  const g = {
    warn: `warn-${dir}`, alarm: `alarm-${dir}`,
    stale: "stale", cmd: "commanded", off: "off",
  }[kind];
  const cls = { neutral: "", ok: "ok" }[kind] ?? kind;
  return `<span class="ds-chip ${cls}"` +
         (s.title ? ` title="${esc(s.title)}"` : "") + `>` +
         (g ? glyph(g) : "") +
         `<span>${esc(s.label ?? "—")}</span></span>`;
}

/* ── 6. STEPPER (P5: one component for all chains) ─────────────────────── */

/**
 * renderStepper(states, activeIdx) -> html string
 *
 * Progression through discrete stages: AOCS mode chain, TTC lock chain
 * (carrier -> bit -> frame), load-shed ladder, TC verification
 * (accept -> start -> complete), procedure steps. ONE component for all.
 *
 * @param {(string|{label:string, fail?:boolean, detail?:string})[]} states
 *        Ordered stage labels. An object form marks a failed stage
 *        (fail:true) and can carry a small detail line.
 * @param {number} activeIdx — index of the CURRENT stage; stages before it
 *        render as done, after it as pending. Pass -1 for "none reached".
 * @returns {string} `<div class="ds-stepper" role="list">...`
 */
export function renderStepper(states, activeIdx) {
  const items = (states || []).map((st, i) => {
    const o = typeof st === "string" ? { label: st } : (st || { label: "?" });
    const cls = o.fail ? "fail" : i < activeIdx ? "done" : i === activeIdx ? "active" : "";
    return `<span class="ds-step ${cls}" role="listitem"` +
           (i === activeIdx ? ` aria-current="step"` : "") + `>` +
           `<span class="pip"></span><span>${esc(o.label)}` +
           (o.detail ? `<br><small>${esc(o.detail)}</small>` : "") +
           `</span></span>`;
  }).join("");
  return `<div class="ds-stepper" role="list">${items}</div>`;
}

/* ── 7. ALARM ROW (P7) ─────────────────────────────────────────────────── */

/**
 * renderAlarmRow(alarm) -> html string
 *
 * One row of the single alarm authority. Blank-tolerant: rationalisation /
 * procedure / priority may be absent and the row still renders.
 *
 * @param {object} alarm — WS `alarm` message payload / GET /api/alarms row:
 *        {id, timestamp (sim epoch s), time_base, subsystem, severity,
 *         message?, param_id?, value?, status?, limit?, acknowledged,
 *         procedure? {id?, name?, path?}, db_id?}.
 *        Optional extensions the renderer honours when present:
 *        priority (1-4, ISA-18.2; derived by the page when absent),
 *        rationalisation {cause?, consequence?, response?}, shelved (bool),
 *        rtn (bool, returned-to-normal), utc (pre-formatted time string —
 *        pages format timestamps with fmtUTC + the sim clock; this renderer
 *        does NOT read clocks).
 * @returns {string} `<div class="ds-alarm-row ...">...`
 */
export function renderAlarmRow(alarm) {
  const a = alarm || {};
  const pri = [1, 2, 3, 4].includes(a.priority) ? a.priority
    : (Number(a.severity) >= 3 ? 2 : Number(a.severity) === 2 ? 3 : 4);
  const cls = [
    "ds-alarm-row", `p${pri}`,
    a.acknowledged ? "acked" : "unacked",
    a.shelved ? "shelved" : "", a.rtn ? "rtn" : "",
  ].filter(Boolean).join(" ");
  const rat = a.rationalisation || {};
  const ratHtml = (rat.cause || rat.consequence || rat.response)
    ? `<small>${[rat.cause, rat.consequence, rat.response]
        .filter(Boolean).map(esc).join(" · ")}</small>` : "";
  const proc = a.procedure
    ? `<button class="ds-btn" data-act="procedure" ` +
      `data-proc="${esc(a.procedure.path || a.procedure.id || "")}">` +
      `${esc(a.procedure.name || a.procedure.id || "PROCEDURE")}</button>` : "";
  const ack = a.acknowledged ? "" :
    `<button class="ds-btn" data-act="ack" data-id="${esc(a.id)}">ACK</button>`;
  const msg = a.message ||
    (a.param_id !== undefined
      ? `${a.status || "limit"} on 0x${Number(a.param_id).toString(16).toUpperCase().padStart(4, "0")}`
      : "");
  return `<div class="${cls}" data-id="${esc(a.id)}" role="row">` +
         `<span class="t num">${esc(a.utc || "")}</span>` +
         `<span class="pri">P${pri}</span>` +
         `<span class="msg">${esc(a.subsystem ? a.subsystem.toUpperCase() + " — " : "")}` +
         `${esc(msg)}${a.value !== undefined ? ` <span class="num">(${_num(Number(a.value))})</span>` : ""}` +
         `${ratHtml}</span>` +
         `<span class="act">${ack}${proc}</span>` +
         `</div>`;
}

/* ── 8. EVENT ROW (P5) ─────────────────────────────────────────────────── */

/**
 * renderEventRow(evt) -> html string
 *
 * List-of-things-that-happened idiom: time · severity glyph · source · text.
 *
 * @param {object} evt — event entry (MCS WS `event` payloads / S5 decodes /
 *        instructor log lines): {severity (1-4 numeric or
 *        "info"|"low"|"medium"|"high"|"critical"), name?, subsystem?,
 *        description?|text?, utc? (pre-formatted by the page — this renderer
 *        reads no clock), instructor? (bool: M9 INSTRUCTOR badge, sim only)}.
 * @returns {string} `<div class="ds-event-row ...">...`
 */
export function renderEventRow(evt) {
  const e = evt || {};
  const sevNum = typeof e.severity === "number" ? e.severity
    : { critical: 4, high: 3, medium: 2, low: 1, info: 1 }[String(e.severity).toLowerCase()] ?? 1;
  const cls = sevNum >= 3 ? "alarm" : sevNum === 2 ? "warn" : "info";
  const sevTxt = sevNum >= 4 ? "CRIT" : sevNum === 3 ? "HIGH" : sevNum === 2 ? "MED" : "INFO";
  const badge = e.instructor
    ? ` <span class="ds-badge" title="injected by instructor (training)">INSTRUCTOR</span>` : "";
  return `<div class="ds-event-row ${cls}" role="row">` +
         `<span class="t num">${esc(e.utc || "")}</span>` +
         `<span class="sev">${sevTxt}</span>` +
         `<span class="src">${esc((e.subsystem || e.source || "").toUpperCase())}</span>` +
         `<span>${esc(e.name || "")}${e.name && (e.description || e.text) ? " — " : ""}` +
         `${esc(e.description || e.text || "")}${badge}</span>` +
         `</div>`;
}

/* ── 9. SCHEMATIC BINDING ──────────────────────────────────────────────── */

/**
 * bindSchematic(svgRoot, state) -> void
 *
 * The ONE deliberately impure export (it must walk a live SVG DOM).
 * Idempotent: call it on every state update.
 *
 * Walks the schematic for:
 *  - [data-param="0xHHHH"] nodes: sets the node's status class from
 *    state.param_limit_state / staleness, and writes the value into a child
 *    <text class="vl"> (formatted, with units from data-units attr if set).
 *  - [data-nav="pageid"] nodes: adds click + Enter/Space handlers that
 *    dispatch a bubbling CustomEvent "ds-nav" with detail {page} on the
 *    node — pages listen once at the document level and route
 *    (schematic-as-navigation, plan §2.1). Adds tabindex/role/cursor.
 *
 * @param {SVGElement|Element} svgRoot — the inlined schematic root
 *        (e.g. the <svg> from modern/schematics.svg for the shown page).
 * @param {object} state — the WS `state` message `data` object (needs
 *        .params, .params_age, .param_limit_state; tolerates absence).
 */
function hasFiniteNumber(v) {
  return v !== null && v !== undefined && Number.isFinite(Number(v));
}
export function bindSchematic(svgRoot, state) {
  if (!svgRoot) return;
  const st = state || {};
  const params = st.params || {};
  const limitState = st.param_limit_state || {};
  const ages = st.params_age || {};
  svgRoot.querySelectorAll("[data-param]").forEach((node) => {
    const hex = node.getAttribute("data-param");
    const val = params[hex];
    const id = parseInt(hex, 16);
    const age = ages[String(id)];
    // Same four-state verdict as the tables when the caller supplies one
    // (state.param_verdicts): a schematic that prints the last known number
    // for a SID nobody enabled is the most confident possible way to be wrong.
    const fr = (st.param_verdicts && st.param_verdicts[hex])
      || freshness(age !== undefined ? age : (val !== undefined ? 0 : null));
    const disabled = fr === "disabled";
    const stale = fr === "stale" || disabled || val === undefined || val === null;
    const lc = stale ? "" : limitClass(limitState[hex] || "");
    node.classList.remove("warn", "alarm", "stale");
    if (stale) node.classList.add("stale");
    else if (lc === "a") node.classList.add("alarm");
    else if (lc === "w") node.classList.add("warn");
    const vl = node.querySelector(".vl");
    if (vl) {
      const units = node.getAttribute("data-units") || "";
      // Discrete parameters read as their state name. The AOCS CORE box on the
      // L2 schematic is `data-param="0x020F"` — it showed a bare "3" where the
      // operator needs "COARSE_SUN". States come from the parameter catalogue
      // via state.param_states; a unit suffix on a state name is meaningless.
      const stt = st.param_states && st.param_states[hex];
      const code = hasFiniteNumber(val) ? Math.round(Number(val)) : null;
      if (disabled) vl.textContent = "NOT ENABLED";
      else if (stale) vl.textContent = "—";
      else if (stt && code !== null && stt[String(code)] !== undefined) {
        vl.textContent = `${stt[String(code)]} (${code})`;
      } else vl.textContent = _num(Number(val)) + (units ? " " + units : "");
    }
  });
  svgRoot.querySelectorAll("[data-nav]").forEach((node) => {
    if (node.__dsNavBound) return;
    node.__dsNavBound = true;
    node.setAttribute("tabindex", "0");
    node.setAttribute("role", "link");
    const go = () => node.dispatchEvent(new CustomEvent("ds-nav", {
      bubbles: true, detail: { page: node.getAttribute("data-nav") },
    }));
    node.addEventListener("click", go);
    node.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); }
    });
  });
}

/* ── 10. TRANSPORT HELPERS ─────────────────────────────────────────────── */

/**
 * poller(fn, ms) -> {start(), stop(), kick()}
 *
 * Shared REST polling loop with backoff, so the three pages do not each
 * reinvent it. Calls `fn()` (async) every `ms`; on rejection, backs off
 * exponentially (x2 per consecutive failure, capped at 8x ms) and recovers
 * to the base cadence on the next success. Never overlaps calls.
 *
 * @param {() => Promise<any>} fn — the poll body (fetch + render).
 * @param {number} ms — base cadence in milliseconds.
 * @returns {{start:Function, stop:Function, kick:Function}} kick() runs one
 *          immediate cycle (e.g. after a user action) without disturbing
 *          the schedule.
 */
export function poller(fn, ms) {
  let timer = null, running = false, fails = 0, inFlight = false;
  async function cycle() {
    if (inFlight) return;
    inFlight = true;
    try { await fn(); fails = 0; }
    catch (e) { fails = Math.min(fails + 1, 3); }
    finally { inFlight = false; }
    if (running) {
      timer = setTimeout(cycle, ms * Math.pow(2, fails));  // 1x,2x,4x,8x
    }
  }
  return {
    start() { if (!running) { running = true; cycle(); } },
    stop() { running = false; if (timer) { clearTimeout(timer); timer = null; } },
    kick() { if (running) { if (timer) clearTimeout(timer); cycle(); } },
  };
}

/**
 * wsClient(url, handlers) -> {close(), send(obj), readyState()}
 *
 * Shared WebSocket wrapper with the house 3-second reconnect (preserved 1:1
 * from the classic consoles). Messages are JSON; each parsed message `m` is
 * dispatched to `handlers[m.type](m)` — every message type is a first-class
 * handler, no default swallowing (unknown types go to `handlers._unknown`
 * if provided).
 *
 * @param {string} url — e.g. `ws://host/ws?position=eps_tcs` (MCS; the
 *        `position` query param is part of the server contract) or the
 *        instructor `/ws`.
 * @param {object} handlers — {state, alarm, event, ... : (msg) => void}
 *        plus optional lifecycle callbacks:
 *        _open(), _close() (fired on close AND on failed connect — drive
 *        the WS dot from these), _unknown(msg).
 * @returns {{close:Function, send:Function, readyState:Function}}
 *        close() disables reconnection and closes; send(obj) JSON-serialises
 *        (silently dropped when not open — callers check readyState() when
 *        it matters); readyState() -> WebSocket.readyState or -1.
 */
export function wsClient(url, handlers) {
  const h = handlers || {};
  let ws = null, closed = false, timer = null;
  function connect() {
    if (closed) return;
    try { ws = new WebSocket(url); }
    catch (e) { retry(); return; }
    ws.onopen = () => { if (h._open) h._open(); };
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch (e) { return; }
      const fn = h[m.type];
      if (fn) fn(m);
      else if (h._unknown) h._unknown(m);
    };
    ws.onclose = () => { if (h._close) h._close(); retry(); };
    ws.onerror = () => { try { ws.close(); } catch (e) { /* noop */ } };
  }
  function retry() {
    if (closed || timer) return;
    timer = setTimeout(() => { timer = null; connect(); }, 3000);  // house rule
  }
  connect();
  return {
    close() { closed = true; if (timer) clearTimeout(timer); if (ws) try { ws.close(); } catch (e) { /* noop */ } },
    send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); },
    readyState() { return ws ? ws.readyState : -1; },
  };
}

/* ── internals ─────────────────────────────────────────────────────────── */

function _band(limits) {
  if (!limits) return null;
  const { yellow_low, yellow_high, red_low, red_high } = limits;
  if ([yellow_low, yellow_high, red_low, red_high]
      .every((v) => v === null || v === undefined)) return null;
  return { yellow_low, yellow_high, red_low, red_high };
}

function _classify(value, band) {
  if (band.red_low !== null && band.red_low !== undefined && value < band.red_low) return "red_low";
  if (band.red_high !== null && band.red_high !== undefined && value > band.red_high) return "red_high";
  if (band.yellow_low !== null && band.yellow_low !== undefined && value < band.yellow_low) return "yellow_low";
  if (band.yellow_high !== null && band.yellow_high !== undefined && value > band.yellow_high) return "yellow_high";
  return "nominal";
}

function _num(v) {
  if (!Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a !== 0 && (a < 0.01 || a >= 1e6)) return v.toExponential(2);
  if (Number.isInteger(v) && a < 1e6) return String(v);
  return v.toFixed(a < 10 ? 2 : a < 100 ? 1 : 0);
}

function _age(ageS) {
  if (ageS === null || ageS === undefined || !Number.isFinite(Number(ageS))) return "—";
  const a = Math.max(0, Number(ageS));
  if (a < 10) return `${a.toFixed(1)} s`;
  if (a < 100) return `${Math.round(a)} s`;
  if (a < 3600) return `${Math.floor(a / 60)} m ${String(Math.round(a % 60)).padStart(2, "0")} s`;
  return `${Math.floor(a / 3600)} h`;
}

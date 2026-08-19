/* ============================================================================
 * SMO DESIGN SYSTEM — format.js
 * ============================================================================
 * Shared time/units/CUC/MET formatters for the three modern consoles.
 * ES module of PURE functions: no DOM, no globals, no fetch, no Date.now()
 * except where explicitly documented. Served at /static/shared/format.js.
 *
 * THIS IS A FROZEN CONTRACT (execution plan §3). Pages (Agents 2-4) build
 * against these exact names and signatures; do not rename, remove, or change
 * an argument's meaning. Additive changes only.
 *
 * House time rules encoded here:
 *  - All wall/ground/sim instants are ISO-8601 UTC strings or epoch seconds
 *    as served by the servers; formatters never apply a local timezone.
 *  - CUC epoch is mission-configured (mission.yaml `time_epoch`, J2000 by
 *    default). Callers pass it explicitly; there is no baked-in literal
 *    beyond the J2000 default the protocol layer also defaults to.
 * ============================================================================ */

/** Default CUC epoch: J2000.0 (2000-01-01T12:00:00 UTC) — mirrors
 *  smo_common.protocol TIME_EPOCH_DEFAULT. Override per-mission via the
 *  `epochIso` argument of fmtCUC (source: MCS /api/state
 *  `time_correlation.epoch`). */
export const CUC_EPOCH_DEFAULT = "2000-01-01T12:00:00Z";

/**
 * fmtUTC(input, opts) -> string
 *
 * Render an instant as a UTC clock string.
 *
 * @param {string|number|Date|null|undefined} input
 *        ISO-8601 string (e.g. `/api/state` `sim_time` / `ground_utc` /
 *        `sc_time`; planner `/api/time-source` `sim_time`), OR epoch seconds
 *        (numbers < 1e12 are treated as seconds, >= 1e12 as milliseconds),
 *        OR a Date.
 * @param {{seconds?:boolean, date?:boolean, ms?:boolean}} [opts]
 *        seconds (default true) — include :SS. date (default false) —
 *        prefix YYYY-MM-DD. ms (default false) — append .mmm.
 * @returns {string} e.g. "14:03:22", "2026-08-17 14:03:22", or "—"
 *        (em-dash) when input is null/undefined/unparseable — the caller
 *        renders that as the stale affordance, never "Invalid Date".
 */
export function fmtUTC(input, opts = {}) {
  const d = _toDate(input);
  if (!d) return "—";
  const { seconds = true, date = false, ms = false } = opts;
  const p = (n, w = 2) => String(n).padStart(w, "0");
  let out = `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  if (seconds) out += `:${p(d.getUTCSeconds())}`;
  if (ms) out += `.${p(d.getUTCMilliseconds(), 3)}`;
  if (date) {
    out = `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${out}`;
  }
  return out;
}

/**
 * fmtMET(metS) -> string
 *
 * Mission elapsed time as "MET DDD/HH:MM:SS" (sign-aware).
 *
 * @param {number|null|undefined} metS — seconds since separation. Source:
 *        MCS /api/state `met_s` (null in real mode / pre-separation —
 *        the plan's absent-MET affordance) or the planner/instructor
 *        equivalents.
 * @returns {string} "MET 012/03:44:09", "MET -000/00:10:00" before
 *        separation, or "—" when metS is null/undefined/not finite.
 */
export function fmtMET(metS) {
  if (metS === null || metS === undefined || !Number.isFinite(Number(metS))) {
    return "—";
  }
  let s = Number(metS);
  const neg = s < 0;
  s = Math.abs(Math.floor(s));
  const p = (n, w = 2) => String(n).padStart(w, "0");
  const days = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `MET ${neg ? "-" : ""}${p(days, 3)}/${p(h)}:${p(m)}:${p(sec)}`;
}

/**
 * fmtCUC(coarse, fine, epochIso) -> {utc:string, seconds:number|null, raw:string}
 *
 * Decode a CCSDS CUC time (coarse seconds + optional 1/65536-s fine field)
 * against the mission epoch and render all three useful forms.
 *
 * @param {number|null|undefined} coarse — CUC coarse seconds. Source: TM
 *        param 0x0309 (MCS /api/state `sc_obc_time_cuc`).
 * @param {number|null|undefined} [fine] — CUC fine, 1/65536 s units.
 *        Source: TM param 0x0A12 (/api/state `time_correlation.sc_cuc_fine`).
 *        Pass null/undefined when not downlinked; the raw form then omits it.
 * @param {string} [epochIso=CUC_EPOCH_DEFAULT] — mission CUC epoch, ISO UTC.
 *        Source: /api/state `time_correlation.epoch`.
 * @returns {{utc:string, seconds:number|null, raw:string}}
 *        utc — "YYYY-MM-DD HH:MM:SS" (em-dash when coarse is absent);
 *        seconds — total CUC seconds incl. fine fraction (null when absent);
 *        raw — "0xXXXXXXXX.0xXXXX" hex rendering for the packet inspector.
 */
export function fmtCUC(coarse, fine, epochIso = CUC_EPOCH_DEFAULT) {
  if (coarse === null || coarse === undefined || !Number.isFinite(Number(coarse))) {
    return { utc: "—", seconds: null, raw: "—" };
  }
  const c = Math.floor(Number(coarse));
  const hasFine = fine !== null && fine !== undefined && Number.isFinite(Number(fine));
  const f = hasFine ? Number(fine) / 65536.0 : 0.0;
  const seconds = c + f;
  const epoch = _toDate(epochIso);
  const utc = epoch
    ? fmtUTC(new Date(epoch.getTime() + seconds * 1000), { date: true })
    : "—";
  const raw = fmtHex(c, 8) + (hasFine ? "." + fmtHex(Math.floor(Number(fine)), 4) : "");
  return { utc, seconds, raw };
}

/**
 * fmtAge(ageS) -> string
 *
 * Human-scale age for freshness chips: "0.4 s", "12 s", "3 m 05 s",
 * "2 h 14 m", "> 1 d".
 *
 * @param {number|null|undefined} ageS — age in SIM seconds as served
 *        (`last_frame_age_s`, `params_age`, `hk_sid_age_s`,
 *        `subsystem_staleness`). Never re-age with Date.now(): the server's
 *        `age_time_base` is sim time (spec Part D.2).
 * @returns {string} em-dash when null/undefined (no data ever received).
 */
export function fmtAge(ageS) {
  if (ageS === null || ageS === undefined || !Number.isFinite(Number(ageS))) {
    return "—";
  }
  const a = Math.max(0, Number(ageS));
  if (a < 10) return `${a.toFixed(1)} s`;
  if (a < 100) return `${Math.round(a)} s`;
  if (a < 3600) {
    const m = Math.floor(a / 60), s = Math.round(a % 60);
    return `${m} m ${String(s).padStart(2, "0")} s`;
  }
  if (a < 86400) {
    const h = Math.floor(a / 3600), m = Math.round((a % 3600) / 60);
    return `${h} h ${String(m).padStart(2, "0")} m`;
  }
  return "> 1 d";
}

/**
 * fmtHex(value, width) -> string
 *
 * Uppercase 0x-prefixed hex, zero-padded. The house parameter-id rendering:
 * fmtHex(0x0539, 4) === "0x0539", matching the `0xHHHH` keys of
 * /api/state `params` / `param_limit_state` and /api/limits `limits`.
 *
 * @param {number|null|undefined} value — non-negative integer.
 * @param {number} [width=4] — minimum hex digits.
 * @returns {string} em-dash when value is null/undefined/not finite.
 */
export function fmtHex(value, width = 4) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return "—";
  }
  const v = Math.floor(Math.abs(Number(value)));
  return "0x" + v.toString(16).toUpperCase().padStart(width, "0");
}

/* ── internal ── */
function _toDate(input) {
  if (input === null || input === undefined) return null;
  if (input instanceof Date) return isNaN(input.getTime()) ? null : input;
  if (typeof input === "number") {
    if (!Number.isFinite(input)) return null;
    const ms = Math.abs(input) >= 1e12 ? input : input * 1000;
    const d = new Date(ms);
    return isNaN(d.getTime()) ? null : d;
  }
  if (typeof input === "string") {
    const s = input.trim();
    if (!s) return null;
    // Treat a bare "YYYY-MM-DDTHH:MM:SS" (no zone) as UTC, per house rule.
    const needsZ = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s);
    const d = new Date(needsZ ? s.replace(" ", "T") + "Z" : s);
    return isNaN(d.getTime()) ? null : d;
  }
  return null;
}

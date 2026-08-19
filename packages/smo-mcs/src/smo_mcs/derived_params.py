"""Ground-derived parameter block v2 (POSITION_NEEDS G1 + G3).

The ground segment computes exactly three derived parameters before this
module existed (``contact.current_gs`` 0x0800, ``contact.time_to_aos``
0x0801, ``contact.in_contact`` 0x0802), so every operator "how long until…"
question — SoC at eclipse exit, momentum to the desat threshold, buffer to
full, data to fit, catbed to ready — was mental arithmetic over data the
MCS already holds. This module is the one projection helper that closes
that tier.

Inputs, all of which already exist server-side:

* the latest RECEIVED telemetry (the ``/api/state`` subsystem groups) —
  never simulator truth, so real mode behaves identically;
* the recent TM **archive** history (slope estimation);
* ``mcs/limits.yaml`` thresholds;
* the MCS's own orbit propagator (eclipse and contact geometry — ground
  orbit knowledge, legitimate in real mode).

HONESTY CONTRACT (the planner's hard-coded-0.35 lesson): every value
carries an explicit ``valid`` flag and a ``reason`` when invalid. No entry
is ever a fabricated number — no archive history ⇒ ``valid: false``,
``value: null``, never a guess. Displays must render the invalid state,
not hide it.

These are GROUND pseudo-parameters in the 0x08xx block. They are declared
in ``configs/<mission>/telemetry/derived.yaml`` (NOT in
``telemetry/parameters.yaml`` — the spacecraft has no such parameters and
the parameter-chain contract test would rightly flag them).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: id allocation in the ground-derived 0x08xx block. 0x0800-0x0802 are the
#: original contact trio (published in the state payload as
#: ``in_contact`` / ``ground_station_name`` / the contact schedule).
DERIVED_V2_IDS = {
    "eps.soc_at_eclipse_exit_pred": 0x0803,
    "eps.time_to_soc_yellow": 0x0804,
    "aocs.time_to_mom_70pct": 0x0805,
    "obdh.time_to_buf_full": 0x0806,
    "ttc.pass_data_capacity_bytes": 0x0807,
    "prop.catbed_ready_eta": 0x0808,
    "ttc.pass_fit_ratio": 0x0809,
    "obdh.time_offset_ms": 0x080A,
}

#: Momentum threshold that clears/sets ``aocs.fine_ready`` bit 4 and drives
#: desat planning (NOM-002): 70 % of saturation.
MOMENTUM_THRESHOLD_PCT = 70.0

#: Archive window the slope is fitted over (sim seconds) and the minimum
#: evidence to call a slope real rather than noise.
SLOPE_WINDOW_S = 900.0
SLOPE_MIN_POINTS = 3
SLOPE_MIN_SPAN_S = 30.0

#: A projection further out than this is not operationally meaningful and
#: is reported invalid rather than as a huge number.
MAX_HORIZON_S = 7 * 86400.0


def _entry(name: str, units: str, value: Optional[float] = None,
           valid: bool = False, reason: str = "") -> dict:
    """One derived-parameter record, honest by construction."""
    return {
        "id": f"0x{DERIVED_V2_IDS[name]:04X}",
        "name": name,
        "units": units,
        "value": None if value is None else round(float(value), 3),
        "valid": bool(valid),
        "reason": str(reason),
        "source": "ground",
    }


class DerivedParamEngine:
    """Computes the v2 ground-derived block each state poll.

    ``archive`` needs ``query_parameters(name, start, end, limit)`` and an
    ``available`` attribute; ``orbit_prop`` is the MCS's
    :class:`smo_common.orbit.propagator.OrbitPropagator` (may be ``None``);
    ``limits`` is the parsed ``mcs/limits.yaml`` list; ``now_fn`` returns
    sim time as a Unix epoch float.
    """

    def __init__(self, archive, limits, orbit_prop, now_fn: Callable[[], float]):
        self._archive = archive
        self._orbit_prop = orbit_prop
        self._now_fn = now_fn
        self._limits_by_id = {int(l.param_id): l for l in (limits or [])}
        # Contact-window cache: contact_windows() sweeps SGP4 and is too
        # expensive for the 1 Hz poll; refresh every 60 sim-seconds.
        self._contact_cache: list[dict] = []
        self._contact_cache_at: Optional[float] = None
        self._contact_cache_interval_s = 60.0

    # ── slope helper ─────────────────────────────────────────────────

    def _slope_per_s(self, param_name: str) -> tuple[Optional[float], str]:
        """Least-squares slope of the recent archive history, in units/s.

        Returns ``(slope, "")`` or ``(None, why-not)``. The archive rows are
        sim-time stamped, same clock as ``now_fn`` — the projection stays
        correct under sim acceleration.
        """
        if not getattr(self._archive, "available", False):
            return None, "TM archive unavailable — no history to project from"
        now = self._now_fn()
        try:
            rows = self._archive.query_parameters(
                param_name, start_epoch=now - SLOPE_WINDOW_S,
                end_epoch=now, limit=500)
        except Exception as e:  # noqa: BLE001
            return None, f"archive query failed: {e}"
        if len(rows) < SLOPE_MIN_POINTS:
            return None, (f"insufficient history ({len(rows)} samples in "
                          f"{SLOPE_WINDOW_S:.0f} s window, need "
                          f">={SLOPE_MIN_POINTS})")
        ts = [float(r["utc_epoch"]) for r in rows]
        vs = [float(r["value"]) for r in rows]
        span = max(ts) - min(ts)
        if span < SLOPE_MIN_SPAN_S:
            return None, (f"history span {span:.0f} s too short "
                          f"(need >={SLOPE_MIN_SPAN_S:.0f} s)")
        t0 = ts[0]
        n = float(len(ts))
        sx = sum(t - t0 for t in ts)
        sy = sum(vs)
        sxx = sum((t - t0) ** 2 for t in ts)
        sxy = sum((t - t0) * v for t, v in zip(ts, vs))
        denom = n * sxx - sx * sx
        if denom <= 0:
            return None, "degenerate history (no time spread)"
        return (n * sxy - sx * sy) / denom, ""

    def _time_to_threshold(self, current: float, threshold: float,
                           slope: float, direction: str) -> tuple[Optional[float], str]:
        """Seconds until ``current`` crosses ``threshold`` at ``slope``.

        ``direction`` is "down" (falling toward a low threshold) or "up".
        Already past ⇒ 0. Moving away or flat ⇒ ``(None, reason)``.
        """
        if direction == "down":
            if current <= threshold:
                return 0.0, ""
            if slope >= 0.0:
                return None, "not trending toward threshold (value not falling)"
            eta = (current - threshold) / -slope
        else:
            if current >= threshold:
                return 0.0, ""
            if slope <= 0.0:
                return None, "not trending toward threshold (value not rising)"
            eta = (threshold - current) / slope
        if eta > MAX_HORIZON_S:
            return None, (f"projected crossing beyond the {MAX_HORIZON_S/86400:.0f}"
                          " day horizon")
        return eta, ""

    # ── contact geometry ─────────────────────────────────────────────

    def _contact_windows(self) -> list[dict]:
        """Cached forward contact windows from the MCS's own propagator.

        The caller (state poll) has already reset the propagator to "now",
        so ``contact_windows`` sweeps forward from the current sim epoch.
        """
        if self._orbit_prop is None:
            return []
        now = self._now_fn()
        if (self._contact_cache_at is not None
                and abs(now - self._contact_cache_at)
                < self._contact_cache_interval_s):
            return self._contact_cache
        try:
            self._contact_cache = self._orbit_prop.contact_windows(
                duration_s=7200.0, step_s=30.0)
        except Exception as e:  # noqa: BLE001
            logger.debug("contact_windows failed: %s", e)
            self._contact_cache = []
        self._contact_cache_at = now
        return self._contact_cache

    @staticmethod
    def _iso_to_epoch(v: Any) -> Optional[float]:
        from datetime import datetime, timezone
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            return None

    def _pass_seconds_and_window(self, in_contact: bool) -> tuple[Optional[float], str]:
        """Usable pass seconds: remaining (in contact) or next full window."""
        wins = self._contact_windows()
        if not wins:
            return None, ("no contact window predictable (orbit propagator "
                          "or ground-station catalogue unavailable)")
        now = self._now_fn()
        first = wins[0]
        los = self._iso_to_epoch(first.get("los"))
        aos = self._iso_to_epoch(first.get("aos"))
        if los is None or aos is None:
            return None, "contact window timestamps unreadable"
        if in_contact and aos <= now + 60.0:
            # Remainder of the current pass.
            return max(0.0, los - now), ""
        return max(0.0, los - aos), ""

    # ── the block ────────────────────────────────────────────────────

    def compute(self, state: dict) -> dict[str, dict]:
        """Build the derived block from the state snapshot being published.

        ``state`` is the poll-loop data dict: subsystem groups hold ONLY
        received TM; ``in_eclipse`` / ``time_to_eclipse_exit_s`` /
        ``in_contact`` come from the ground propagator.
        """
        out: dict[str, dict] = {}
        eps = state.get("eps") or {}
        aocs = state.get("aocs") or {}
        obdh = state.get("obdh") or {}
        ttc = state.get("ttc") or {}
        prop = state.get("prop") or {}

        out.update(self._eps_soc_at_eclipse_exit(state, eps))
        out.update(self._eps_time_to_soc_yellow(eps))
        out.update(self._aocs_time_to_mom(aocs))
        out.update(self._obdh_time_to_buf_full(obdh))
        cap = self._ttc_pass_capacity(state, ttc)
        out.update(cap)
        out.update(self._ttc_pass_fit(cap["ttc.pass_data_capacity_bytes"],
                                      obdh, state))
        out.update(self._prop_catbed_eta(prop, state))
        out.update(self._obdh_time_offset(state))
        return out

    def _eps_soc_at_eclipse_exit(self, state: dict, eps: dict) -> dict:
        name = "eps.soc_at_eclipse_exit_pred"
        soc = eps.get("bat_soc", eps.get("soc_pct"))
        if soc is None:
            return {name: _entry(name, "%",
                                 reason="no eps.bat_soc telemetry received")}
        if not state.get("in_eclipse"):
            return {name: _entry(name, "%",
                                 reason="not in eclipse — nothing to project")}
        t_exit = state.get("time_to_eclipse_exit_s")
        if t_exit is None:
            return {name: _entry(
                name, "%", reason="eclipse exit time not predictable "
                "(orbit propagator unavailable)")}
        slope, why = self._slope_per_s("eps.bat_soc")
        if slope is None:
            return {name: _entry(name, "%", reason=why)}
        pred = max(0.0, min(100.0, float(soc) + slope * float(t_exit)))
        return {name: _entry(name, "%", value=pred, valid=True)}

    def _eps_time_to_soc_yellow(self, eps: dict) -> dict:
        name = "eps.time_to_soc_yellow"
        soc = eps.get("bat_soc", eps.get("soc_pct"))
        if soc is None:
            return {name: _entry(name, "s",
                                 reason="no eps.bat_soc telemetry received")}
        band = self._limits_by_id.get(0x0101)
        if band is None or band.yellow_low is None:
            return {name: _entry(
                name, "s", reason="no yellow-low limit for eps.bat_soc "
                "in mcs/limits.yaml")}
        slope, why = self._slope_per_s("eps.bat_soc")
        if slope is None:
            return {name: _entry(name, "s", reason=why)}
        eta, why = self._time_to_threshold(float(soc),
                                           float(band.yellow_low),
                                           slope, "down")
        if eta is None:
            return {name: _entry(name, "s", reason=why)}
        return {name: _entry(name, "s", value=eta, valid=True)}

    def _aocs_time_to_mom(self, aocs: dict) -> dict:
        name = "aocs.time_to_mom_70pct"
        mom = aocs.get("momentum_saturation_pct")
        if mom is None:
            return {name: _entry(
                name, "s",
                reason="no aocs.momentum_saturation_pct telemetry received")}
        slope, why = self._slope_per_s("aocs.momentum_saturation_pct")
        if slope is None:
            return {name: _entry(name, "s", reason=why)}
        eta, why = self._time_to_threshold(float(mom),
                                           MOMENTUM_THRESHOLD_PCT,
                                           slope, "up")
        if eta is None:
            return {name: _entry(name, "s", reason=why)}
        return {name: _entry(name, "s", value=eta, valid=True)}

    #: The three OBDH stores, worst-case wins.
    _BUF_PARAMS = ("obdh.hktm_buf_fill", "obdh.event_buf_fill",
                   "obdh.alarm_buf_fill")

    def _obdh_time_to_buf_full(self, obdh: dict) -> dict:
        name = "obdh.time_to_buf_full"
        best: Optional[float] = None
        reasons: list[str] = []
        seen_any = False
        for pname in self._BUF_PARAMS:
            short = pname.split(".", 1)[1]
            fill = obdh.get(short)
            if fill is None:
                continue
            seen_any = True
            slope, why = self._slope_per_s(pname)
            if slope is None:
                reasons.append(f"{short}: {why}")
                continue
            eta, why = self._time_to_threshold(float(fill), 100.0, slope, "up")
            if eta is None:
                reasons.append(f"{short}: {why}")
                continue
            best = eta if best is None else min(best, eta)
        if best is not None:
            return {name: _entry(name, "s", value=best, valid=True)}
        if not seen_any:
            return {name: _entry(
                name, "s", reason="no OBDH buffer-fill telemetry received")}
        return {name: _entry(name, "s",
                             reason="; ".join(reasons) or "no buffer filling")}

    def _ttc_pass_capacity(self, state: dict, ttc: dict) -> dict:
        name = "ttc.pass_data_capacity_bytes"
        # Active tier rate from the ladder view the state poll already built
        # (TM 0x0539 preferred, sim state fallback) — data_rate is bps.
        ladder = state.get("tm_rate_ladder") or []
        tier_idx = ttc.get("tm_tier")
        bps: Optional[float] = None
        if tier_idx is not None:
            try:
                bps = float(ladder[int(tier_idx)]["bps"])
            except (IndexError, KeyError, TypeError, ValueError):
                bps = None
        if bps is None or bps <= 0:
            return {name: _entry(
                name, "bytes",
                reason="active TM tier unknown (no ttc.tm_tier telemetry / "
                "rate ladder)")}
        secs, why = self._pass_seconds_and_window(bool(state.get("in_contact")))
        if secs is None:
            return {name: _entry(name, "bytes", reason=why)}
        return {name: _entry(name, "bytes", value=bps * secs / 8.0,
                             valid=True)}

    def _ttc_pass_fit(self, capacity_entry: dict, obdh: dict,
                      state: dict) -> dict:
        """G3 server-side verdict, one number: backlog ÷ pass capacity.

        > 1.0 means the queued data does NOT fit in the pass at the active
        tier. Backlog = payload store bytes + OBDH HK/event buffer bytes
        (buffer capacities from the S15 store report when present).
        """
        name = "ttc.pass_fit_ratio"
        if not capacity_entry.get("valid"):
            return {name: _entry(
                name, "ratio",
                reason="pass capacity invalid: "
                + capacity_entry.get("reason", ""))}
        payload = state.get("payload") or {}
        backlog_bytes = 0.0
        seen = False
        mem_mb = payload.get("mem_used_mb")
        if mem_mb is not None:
            backlog_bytes += float(mem_mb) * 1e6
            seen = True
        # S15 store report: authoritative per-store fill with byte sizes.
        for s in state.get("storage_status") or []:
            try:
                used = s.get("used_bytes")
                if used is None and s.get("size_bytes") is not None:
                    used = (float(s.get("fill_pct", 0.0)) / 100.0
                            * float(s["size_bytes"]))
                if used is not None:
                    backlog_bytes += float(used)
                    seen = True
            except (TypeError, ValueError):
                continue
        if not seen:
            return {name: _entry(
                name, "ratio",
                reason="no downlink backlog telemetry received "
                "(payload.mem_used_mb / S15 store report)")}
        cap = float(capacity_entry["value"])
        if cap <= 0:
            return {name: _entry(name, "ratio", reason="pass capacity is zero")}
        return {name: _entry(name, "ratio", value=backlog_bytes / cap,
                             valid=True)}

    def _prop_catbed_eta(self, prop: dict, state: dict) -> dict:
        name = "prop.catbed_ready_eta"
        # prop.catbed_temp (0x0B05) mirrors tcs.temp_thruster (0x0409).
        temp = prop.get("catbed_temp")
        if temp is None:
            temp = (state.get("tcs") or {}).get("temp_thruster")
        if temp is None:
            return {name: _entry(
                name, "s", reason="no catbed temperature telemetry received")}
        threshold = self._catbed_min_c
        if threshold is None:
            return {name: _entry(
                name, "s",
                reason="catbed ready threshold not configured "
                "(subsystems/prop.yaml interlocks.catbed_min_c)")}
        slope, why = self._slope_per_s("prop.catbed_temp")
        if slope is None:
            slope, why = self._slope_per_s("tcs.temp_thruster")
        if slope is None:
            return {name: _entry(name, "s", reason=why)}
        eta, why = self._time_to_threshold(float(temp), float(threshold),
                                           slope, "up")
        if eta is None:
            return {name: _entry(name, "s", reason=why)}
        return {name: _entry(name, "s", value=eta, valid=True)}

    #: Set by the server from subsystems/prop.yaml at startup.
    _catbed_min_c: Optional[float] = None

    def set_catbed_threshold(self, value: Optional[float]) -> None:
        self._catbed_min_c = None if value is None else float(value)

    def _obdh_time_offset(self, state: dict) -> dict:
        name = "obdh.time_offset_ms"
        corr = (state.get("time_correlation")
                or (state.get("sa") or {}).get("time_correlation") or {})
        offset_s = corr.get("offset_s")
        if offset_s is None:
            return {name: _entry(
                name, "ms",
                reason="no on-board clock telemetry received (obdh.obc_time)")}
        return {name: _entry(name, "ms", value=float(offset_s) * 1000.0,
                             valid=True)}

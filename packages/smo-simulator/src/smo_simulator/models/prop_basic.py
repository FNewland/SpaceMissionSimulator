"""SMO Simulator — Propulsion Model (Phase D, brief 03).

1U green-monopropellant (LMP-103S/ADN-class) blowdown module, single
thruster on -X firing along +X body. Deliberately minimal: the system exists
for DEBRIS-AVOIDANCE manoeuvres and limited orbit maintenance (drag make-up),
NOT end-of-life deorbit — disposal is passive decay (1.7 yr mean from 450 km
at B = 77.9 kg/m2, inside the FCC 5-year rule).

Physics (isothermal blowdown):
    P    = P0 * V_ull0 / (V_tank - m_p/rho)
    F    = F0 * P/P0 * perf_factor
    Isp  = Isp_EOL + (Isp_BOL - Isp_EOL) * (P - P_EOL)/(P0 - P_EOL)
    mdot = F / (Isp * g0)
    dv  += (F/m) * dt          (sub-stepped at <= 1 s while FIRING)

Attitude coupling: effective along-track dv is dv*cos(theta) with
theta = aocs.att_error (0x0217); the lateral dv*sin(theta) goes into the
orbit-normal direction. Thrust offset (5 mm) publishes a disturbance torque
(0x0B17) that aocs_basic feeds into its real disturbance-torque path.

State machine:
    SAFED(0) -PROP_ARM-> ARMED(1) -PROP_EXECUTE-> FIRING(2) -nominal-> ARMED
    any abort -> SAFED; a second abort latches INHIBIT(3), cleared only by
    PROP_INHIBIT_CLR from the ground.

Out of scope (see configs/eosat1/subsystems/prop.yaml and
docs/PROPULSION_GUIDE.md): multi-thruster TVC, propulsive deorbit, plane
changes, closed-loop orbit control, conjunction screening, combustion/feed
transients, non-isothermal blowdown, slosh/CG migration, inertia coupling.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from smo_common.models.subsystem import SubsystemModel
from smo_simulator.failure_spec import (
    FailureSpec, ParamSpec, T_FLOAT, enum, number,
)

G0 = 9.80665

# States
ST_SAFED, ST_ARMED, ST_FIRING, ST_INHIBIT = 0, 1, 2, 3

# Abort reasons (0x0B14)
AB_NONE, AB_ATT_ERR, AB_LOW_P, AB_OVERTEMP, AB_LOW_SOC, AB_ECLIPSE, \
    AB_GROUND, AB_TIMEOUT, AB_VALVE = range(9)

# Arm-inhibit bitmask bits (0x0B15)
INH_CATBED = 1 << 0
INH_TANK_P = 1 << 1
INH_PROPELLANT = 1 << 2
INH_AOCS_MODE = 1 << 3
INH_ATT_ERR = 1 << 4
INH_SOC = 1 << 5
INH_PHASE = 1 << 6
INH_ECLIPSE = 1 << 7
INH_VALVES = 1 << 8
INH_LATCHED = 1 << 9
INH_NO_BURN = 1 << 10

# Distinct S1(8) execution-failure codes, one per interlock (brief 03 WP-2a).
_INTERLOCK_CODES = {
    INH_CATBED: (0x0B01, "catalyst bed below minimum temperature"),
    INH_TANK_P: (0x0B02, "tank pressure outside the operating band"),
    INH_PROPELLANT: (0x0B03, "insufficient propellant remaining"),
    INH_AOCS_MODE: (0x0B04, "AOCS not in a burn-capable mode (NOMINAL/FINE/INERTIAL)"),
    INH_ATT_ERR: (0x0B05, "attitude error above the arm limit"),
    INH_SOC: (0x0B06, "battery SoC below the arm limit"),
    INH_PHASE: (0x0B07, "spacecraft not in NOMINAL phase"),
    INH_ECLIPSE: (0x0B08, "burn not permitted in eclipse"),
    INH_VALVES: (0x0B09, "no latch-valve branch open (PROP_ISO_OPEN first)"),
    INH_LATCHED: (0x0B0A, "propulsion inhibit latched (PROP_INHIBIT_CLR required)"),
    INH_NO_BURN: (0x0B0B, "no burn loaded (PROP_SET_BURN within the arm window)"),
}

# Shared parameters read from other subsystems
_P_CATBED = 0x0409       # tcs.temp_thruster (catalyst bed)
_P_AOCS_MODE = 0x020F
_P_ATT_ERROR = 0x0217
_P_BAT_SOC = 0x0101
_P_SC_PHASE = 0x0129
_P_PROP_LINE = 0x014A    # eps.pl_prop (EPS propulsion power line)

# Valve state enum (0x0B06/07/08)
VLV_CLOSED, VLV_OPEN, VLV_FAULT = 0, 1, 2


@dataclass
class PropState:
    state: int = ST_SAFED
    m_prop_kg: float = 0.400
    tank_p_bar: float = 22.0
    # Injected overpressure floor [bar], 0 = no injection. The blowdown
    # relation recomputes `tank_p_bar` from the propellant load on EVERY tick,
    # so an injection that only wrote `tank_p_bar` was erased within one tick
    # and `tank_overpressure` had no observable effect at all. This field is
    # part of the dataclass, so it also survives a breakpoint round trip.
    tank_overpressure_bar: float = 0.0
    tank_temp_c: float = 15.0
    catbed_temp_c: float = 5.0     # mirror of 0x0409 for status/telemetry
    latch_a: int = VLV_OPEN        # branch A normally open
    latch_b: int = VLV_CLOSED      # branch B normally closed (redundant)
    fcv_open: bool = False
    inhibit_latched: bool = False
    ground_inhibit: bool = False
    dv_accum_m_s: float = 0.0      # lifetime accumulated delta-V
    burn_count: int = 0
    total_on_time_s: float = 0.0
    abort_reason: int = AB_NONE
    abort_count: int = 0
    arm_inhibit_mask: int = 0
    leak_rate_mg_s: float = 0.0
    perf_factor: float = 1.0       # thrust degradation knob (1.0 nominal)
    # Loaded burn (PROP_SET_BURN)
    burn_dv_cmd_m_s: float = 0.0
    burn_direction: int = 0        # 0=prograde, 1=retrograde
    burn_mode: int = 1             # 0=timed, 1=closed-loop dv cut-off
    burn_t_start_cuc: int = 0
    burn_time_cmd_s: float = 0.0   # predicted duration at load time
    burn_loaded: bool = False
    burn_loaded_met_s: float = -1.0e9
    # Active burn integration
    burn_elapsed_s: float = 0.0
    burn_dv_at_m_s: float = 0.0    # along-track component (cos theta)
    burn_dv_lat_m_s: float = 0.0   # lateral component (sin theta)
    burn_dv_mag_m_s: float = 0.0   # |dv| this burn
    burn_status: int = 0           # 0 idle 1 armed 2 firing 3 complete 4 aborted
    burn_dv_achieved_m_s: float = 0.0
    burn_start_utc: str = ""
    # Onboard abort thresholds (PROP_SET_LIMITS)
    lim_att_err_max_deg: float = 2.0
    lim_p_min_bar: float = 4.0
    lim_catbed_max_c: float = 350.0
    lim_t_burn_max_s: float = 300.0
    # Cached cross-subsystem reads (round-trips so a restored mid-burn
    # breakpoint aborts/continues identically)
    met_s: float = 0.0
    in_eclipse: bool = False
    last_att_error_deg: float = 0.0
    # Edge/latch trackers
    _prev_low_prop: bool = False
    _prev_depleted: bool = False
    _prev_p_low: bool = False
    _prev_p_high: bool = False
    _prev_catbed_under: bool = False
    _prev_catbed_over: bool = False
    _prev_leak: bool = False
    # Pending orbit impulse (consumed by the engine after the tick)
    pending_impulse: dict | None = None


class PropBasicModel(SubsystemModel):
    """Minimal blowdown monoprop model (debris avoidance + drag make-up)."""

    def __init__(self):
        self._state = PropState()
        import random as _random
        self.rng = _random.Random(20260731)  # engine overrides via derive_rng
        self._engine = None
        self._param_ids: dict[str, int] = {}
        # Hardware sheet defaults (configs/eosat1/subsystems/prop.yaml wins)
        self._sc_dry_mass_kg = 12.0
        self._m_prop0_kg = 0.400
        self._rho_kg_m3 = 1240.0
        self._tank_v_cm3 = 560.0
        self._p0_bar = 22.0
        self._p_eol_bar = 9.33
        self._f0_n = 0.33
        self._isp_bol_s = 220.0
        self._isp_eol_s = 196.0
        self._thrust_offset_m = 0.005
        self._valve_power_w = 2.0
        self._catbed_heater_w = 12.0
        self._arm_code = 0xA5C3
        self._arm_window_s = 300.0
        self.max_speed_during_burn = 10.0
        # Interlock thresholds
        self._ilk = {
            "catbed_min_c": 180.0,
            "tank_p_min_bar": 6.0,
            "tank_p_max_bar": 24.0,
            "prop_min_g": 5.0,
            "aocs_modes": [4, 5, 9],
            "att_err_max_deg": 2.0,
            "soc_min_pct": 45.0,
            "min_phase": 6,
            "allow_eclipse_burn": False,
        }

    # ── SubsystemModel interface ─────────────────────────────────────

    @property
    def name(self) -> str:
        return "prop"

    def configure(self, config: dict[str, Any]) -> None:
        g = config.get
        self._sc_dry_mass_kg = float(g("sc_dry_mass_kg", self._sc_dry_mass_kg))
        self._m_prop0_kg = float(g("propellant_mass_kg", self._m_prop0_kg))
        self._rho_kg_m3 = float(g("propellant_density_kg_m3", self._rho_kg_m3))
        self._tank_v_cm3 = float(g("tank_volume_cm3", self._tank_v_cm3))
        self._p0_bar = float(g("tank_pressure_bol_bar", self._p0_bar))
        self._p_eol_bar = float(g("tank_pressure_eol_bar", self._p_eol_bar))
        self._f0_n = float(g("thrust_bol_n", self._f0_n))
        self._isp_bol_s = float(g("isp_bol_s", self._isp_bol_s))
        self._isp_eol_s = float(g("isp_eol_s", self._isp_eol_s))
        self._thrust_offset_m = float(g("thrust_offset_mm", 5.0)) * 1e-3
        self._valve_power_w = float(g("valve_power_w", self._valve_power_w))
        self._catbed_heater_w = float(g("catbed_heater_w", self._catbed_heater_w))
        self._arm_code = int(g("arm_code", self._arm_code))
        self._arm_window_s = float(g("arm_window_s", self._arm_window_s))
        ilk = g("interlocks", {}) or {}
        for k, v in ilk.items():
            if k in self._ilk and v is not None:
                self._ilk[k] = v
        ab = g("abort", {}) or {}
        s = self._state
        s.lim_att_err_max_deg = float(ab.get("att_err_max_deg", s.lim_att_err_max_deg))
        s.lim_p_min_bar = float(ab.get("p_min_bar", s.lim_p_min_bar))
        s.lim_catbed_max_c = float(ab.get("catbed_max_c", s.lim_catbed_max_c))
        s.lim_t_burn_max_s = float(ab.get("t_burn_max_s", s.lim_t_burn_max_s))
        s.m_prop_kg = self._m_prop0_kg
        s.tank_p_bar = self._tank_pressure(s.m_prop_kg)
        self._param_ids = g("param_ids", {}) or {}
        # simulation.yaml prop: block (merged by the engine under "simulation")
        sim = g("simulation", {}) or {}
        self.max_speed_during_burn = float(
            sim.get("max_speed_during_burn",
                    g("max_speed_during_burn", self.max_speed_during_burn)))
        if sim.get("allow_eclipse_burn") is not None:
            self._ilk["allow_eclipse_burn"] = bool(sim["allow_eclipse_burn"])
        if sim.get("initial_propellant_kg") is not None:
            s.m_prop_kg = float(sim["initial_propellant_kg"])
            s.tank_p_bar = self._tank_pressure(s.m_prop_kg)

    # ── Blowdown physics helpers ─────────────────────────────────────

    def _tank_pressure(self, m_prop_kg: float) -> float:
        """Isothermal blowdown: P = P0 * V_ull0 / (V_tank - m_p/rho)."""
        v_tank_m3 = self._tank_v_cm3 * 1e-6
        v_ull0_m3 = v_tank_m3 - self._m_prop0_kg / self._rho_kg_m3
        v_ull_m3 = max(v_tank_m3 - max(m_prop_kg, 0.0) / self._rho_kg_m3, 1e-9)
        return self._p0_bar * v_ull0_m3 / v_ull_m3

    def _thrust_n(self, p_bar: float) -> float:
        return max(self._f0_n * p_bar / self._p0_bar, 0.0) * self._state.perf_factor

    def _isp_s(self, p_bar: float) -> float:
        frac = (p_bar - self._p_eol_bar) / max(self._p0_bar - self._p_eol_bar, 1e-9)
        return self._isp_eol_s + (self._isp_bol_s - self._isp_eol_s) * frac

    def _sc_mass_kg(self) -> float:
        return self._sc_dry_mass_kg + max(self._state.m_prop_kg, 0.0)

    def dv_remaining_m_s(self) -> float:
        """Tsiolkovsky on the live propellant load (CTG step: usable envelope)."""
        m0 = self._sc_mass_kg()
        mf = self._sc_dry_mass_kg
        isp_avg = 0.5 * (self._isp_s(self._state.tank_p_bar) + self._isp_eol_s)
        return isp_avg * G0 * math.log(max(m0 / mf, 1.0))

    def predict_burn_time_s(self, dv_m_s: float) -> float:
        """Predicted duration of a dv burn at the CURRENT tank state."""
        m = self._sc_mass_kg()
        f = self._thrust_n(self._state.tank_p_bar)
        if f <= 1e-6:
            return 0.0
        # First-order (thrust and mass ~constant over one burn)
        return dv_m_s * m / f

    # ── Interlocks ───────────────────────────────────────────────────

    def _interlock_mask(self, require_burn: bool = True) -> int:
        s = self._state
        mask = 0
        if s.catbed_temp_c < float(self._ilk["catbed_min_c"]):
            mask |= INH_CATBED
        if not (float(self._ilk["tank_p_min_bar"]) <= s.tank_p_bar
                <= float(self._ilk["tank_p_max_bar"])):
            mask |= INH_TANK_P
        if s.m_prop_kg * 1e3 <= float(self._ilk["prop_min_g"]):
            mask |= INH_PROPELLANT
        if int(self._aocs_mode) not in [int(m) for m in self._ilk["aocs_modes"]]:
            mask |= INH_AOCS_MODE
        if s.last_att_error_deg >= float(self._ilk["att_err_max_deg"]):
            mask |= INH_ATT_ERR
        if self._bat_soc <= float(self._ilk["soc_min_pct"]):
            mask |= INH_SOC
        if self._sc_phase < int(self._ilk["min_phase"]):
            mask |= INH_PHASE
        if s.in_eclipse and not bool(self._ilk["allow_eclipse_burn"]):
            mask |= INH_ECLIPSE
        if not (s.latch_a in (VLV_OPEN, VLV_FAULT)
                or s.latch_b in (VLV_OPEN, VLV_FAULT)):
            mask |= INH_VALVES
        if s.inhibit_latched or s.ground_inhibit:
            mask |= INH_LATCHED
        if require_burn and not (
                s.burn_loaded
                and (s.met_s - s.burn_loaded_met_s) <= self._arm_window_s):
            mask |= INH_NO_BURN
        return mask

    # ── Tick ─────────────────────────────────────────────────────────

    def tick(self, dt: float, orbit_state: Any,
             shared_params: dict[int, float]) -> None:
        s = self._state
        s.met_s += dt
        # Cache the orbit state so command-path aborts (PROP_ABORT/DISARM
        # between ticks) can still build the partial-burn impulse.
        self._last_orbit_state = orbit_state

        # Cross-subsystem reads (cached on the state so a breakpoint restore
        # sees the same world the save did)
        s.in_eclipse = bool(getattr(orbit_state, "in_eclipse", False))
        s.catbed_temp_c = float(shared_params.get(_P_CATBED, s.catbed_temp_c))
        s.last_att_error_deg = float(
            shared_params.get(_P_ATT_ERROR, s.last_att_error_deg))
        self._aocs_mode = int(shared_params.get(_P_AOCS_MODE, -1))
        self._bat_soc = float(shared_params.get(_P_BAT_SOC, 100.0))
        self._sc_phase = int(shared_params.get(_P_SC_PHASE, 6))
        line_present = _P_PROP_LINE in shared_params
        line_on = bool(shared_params.get(_P_PROP_LINE, 0)) if line_present else True

        # EPS line dropped (load shed / overcurrent / ground): a burn aborts,
        # an armed system safes — valves close on loss of power.
        if not line_on:
            if s.state == ST_FIRING:
                self._abort(AB_LOW_SOC, "EPS prop line shed mid-burn",
                            orbit_state)
            elif s.state == ST_ARMED:
                s.state = ST_SAFED
                s.burn_status = 0
                self._emit(0x0801, "INFO",
                           "PROP_DISARMED: EPS prop line unpowered")

        # Leak paths (failure injection): tank leak and/or stuck-open valve
        # seep. Visible only through the 0x0B01 delta monitor + 0x0B16.
        leak_kg_s = s.leak_rate_mg_s * 1e-6
        if leak_kg_s > 0.0 and s.m_prop_kg > 0.0:
            s.m_prop_kg = max(s.m_prop_kg - leak_kg_s * dt, 0.0)

        # Firing integration, sub-stepped at <= 1 s (bounded)
        if s.state == ST_FIRING:
            self._integrate_burn(dt, orbit_state)

        # Blowdown pressure follows the propellant load, unless an injected
        # overpressure (pressurant thermal runaway) holds it above that.
        s.tank_p_bar = max(self._tank_pressure(s.m_prop_kg),
                           s.tank_overpressure_bar)

        # Threshold events (edge-triggered)
        self._threshold_events()

        # Live arm-inhibit mask — the single most useful trainee parameter
        s.arm_inhibit_mask = self._interlock_mask(
            require_burn=(s.state == ST_SAFED))

        self._publish(shared_params)

    def _integrate_burn(self, dt: float, orbit_state: Any) -> None:
        s = self._state
        # Abort checks at tick cadence
        if s.last_att_error_deg > s.lim_att_err_max_deg:
            self._abort(AB_ATT_ERR,
                        f"attitude error {s.last_att_error_deg:.2f} deg "
                        f"> {s.lim_att_err_max_deg:.2f} deg", orbit_state)
            return
        if s.tank_p_bar < s.lim_p_min_bar:
            self._abort(AB_LOW_P, f"tank pressure {s.tank_p_bar:.2f} bar low",
                        orbit_state)
            return
        if s.catbed_temp_c > s.lim_catbed_max_c:
            self._abort(AB_OVERTEMP,
                        f"catbed {s.catbed_temp_c:.0f} C over limit",
                        orbit_state)
            return
        if s.burn_elapsed_s >= s.lim_t_burn_max_s:
            self._abort(AB_TIMEOUT, "burn watchdog timeout", orbit_state)
            return
        if not (s.latch_a in (VLV_OPEN, VLV_FAULT)
                or s.latch_b in (VLV_OPEN, VLV_FAULT)):
            self._abort(AB_VALVE, "latch valves closed mid-burn", orbit_state)
            return
        if s.m_prop_kg <= 0.0:
            self._abort(AB_LOW_P, "propellant depleted", orbit_state)
            return

        n_sub = max(1, min(int(math.ceil(dt / 1.0)), 600))
        h = dt / n_sub
        theta = math.radians(min(abs(s.last_att_error_deg), 90.0))
        done = False
        for _ in range(n_sub):
            p_bar = self._tank_pressure(s.m_prop_kg)
            f_n = self._thrust_n(p_bar)
            isp = self._isp_s(p_bar)
            mdot = f_n / (isp * G0) if isp > 0 else 0.0
            m = self._sc_mass_kg()
            a = f_n / m
            # Fractional final sub-step so the cut-off lands on the command
            # (closed-loop dv cut-off, mode 1) or the timer (mode 0).
            h_use = h
            if s.burn_mode == 1 and a > 1e-9:
                t_need = (s.burn_dv_cmd_m_s - s.burn_dv_mag_m_s) / a
                if t_need <= h:
                    h_use = max(t_need, 0.0)
                    done = True
            elif s.burn_mode == 0:
                t_left = s.burn_time_cmd_s - s.burn_elapsed_s
                if t_left <= h:
                    h_use = max(t_left, 0.0)
                    done = True
            s.burn_dv_mag_m_s += a * h_use
            s.burn_dv_at_m_s += a * h_use * math.cos(theta)
            s.burn_dv_lat_m_s += a * h_use * math.sin(theta)
            s.m_prop_kg = max(s.m_prop_kg - mdot * h_use, 0.0)
            s.burn_elapsed_s += h_use
            s.total_on_time_s += h_use
            if done:
                break
            if s.m_prop_kg <= 0.0:
                break
        if done:
            self._complete_burn(orbit_state)
        elif s.m_prop_kg <= 0.0:
            self._abort(AB_LOW_P, "propellant depleted mid-burn", orbit_state)

    def _burn_impulse(self, orbit_state: Any) -> dict | None:
        """Build the ECI impulse for the accumulated burn dv (m/s -> km/s).

        Along-track along +/- v-hat by direction; lateral along the orbit
        normal (attitude-error cross-coupling). Timestamped at the burn
        mid-point (finite-burn gravity loss for these burn arcs is < 0.01 %).
        """
        s = self._state
        if s.burn_dv_mag_m_s <= 0.0:
            return None
        r = np.asarray(getattr(orbit_state, "pos_eci", np.zeros(3)), float)
        v = np.asarray(getattr(orbit_state, "vel_eci", np.zeros(3)), float)
        if np.linalg.norm(v) < 1.0:
            return None
        v_hat = v / np.linalg.norm(v)
        h = np.cross(r, v)
        h_hat = h / (np.linalg.norm(h) + 1e-12)
        sign = -1.0 if s.burn_direction == 1 else 1.0
        dv_m_s = sign * s.burn_dv_at_m_s * v_hat + s.burn_dv_lat_m_s * h_hat
        from datetime import timedelta
        utc_end = getattr(orbit_state, "utc", None)
        utc_mid = (utc_end - timedelta(seconds=s.burn_elapsed_s / 2.0)
                   if utc_end is not None else None)
        return {
            "dv_eci_km_s": (dv_m_s / 1e3).tolist(),
            "dv_mag_m_s": float(s.burn_dv_mag_m_s),
            "utc_mid": utc_mid.isoformat() if utc_mid else None,
        }

    def _complete_burn(self, orbit_state: Any) -> None:
        s = self._state
        s.pending_impulse = self._burn_impulse(orbit_state)
        s.burn_dv_achieved_m_s = s.burn_dv_mag_m_s
        s.burn_count += 1
        s.state = ST_ARMED
        s.burn_status = 3
        s.fcv_open = False
        s.burn_loaded = False
        self._emit(0x0804, "INFO",
                   f"PROP_BURN_COMPLETE: dv {s.burn_dv_achieved_m_s:.3f} m/s "
                   f"({s.burn_elapsed_s:.1f} s), commanded "
                   f"{s.burn_dv_cmd_m_s:.3f} m/s")

    def _abort(self, reason: int, text: str, orbit_state: Any = None) -> None:
        s = self._state
        if orbit_state is None:
            orbit_state = getattr(self, "_last_orbit_state", None)
        was_firing = s.state == ST_FIRING
        if was_firing:
            # The propellant already expended still changed the orbit
            s.pending_impulse = self._burn_impulse(orbit_state)
            s.burn_dv_achieved_m_s = s.burn_dv_mag_m_s
            s.burn_count += 1
        s.state = ST_SAFED
        s.burn_status = 4
        s.fcv_open = False
        s.abort_reason = reason
        s.burn_loaded = False
        s.abort_count += 1
        self._emit(0x0805, "HIGH", f"PROP_BURN_ABORTED: {text} "
                                   f"(reason={reason})")
        # Two aborts => latched inhibit, ground-cleared only (the deployer
        # "independent inhibits" lesson; PROP_INHIBIT_CLR clears it).
        if s.abort_count >= 2 and not s.inhibit_latched:
            s.inhibit_latched = True
            s.state = ST_INHIBIT
            self._emit(0x0810, "HIGH",
                       "PROP_INHIBIT_LATCHED: second abort — propulsion "
                       "inhibited until PROP_INHIBIT_CLR")

    def _threshold_events(self) -> None:
        s = self._state
        checks = [
            ("_prev_low_prop", s.m_prop_kg * 1e3 < 20.0, 0x080D, "MEDIUM",
             f"PROP_PROPELLANT_LOW: {s.m_prop_kg * 1e3:.1f} g remaining"),
            ("_prev_depleted", s.m_prop_kg * 1e3 <= 1.0, 0x080E, "HIGH",
             "PROP_PROPELLANT_DEPLETED"),
            ("_prev_p_low", s.tank_p_bar < 6.0, 0x0806, "MEDIUM",
             f"PROP_TANK_PRESSURE_LOW: {s.tank_p_bar:.2f} bar"),
            ("_prev_p_high", s.tank_p_bar > 24.0, 0x0807, "HIGH",
             f"PROP_TANK_PRESSURE_HIGH: {s.tank_p_bar:.2f} bar"),
            ("_prev_catbed_under",
             s.state in (ST_ARMED, ST_FIRING)
             and s.catbed_temp_c < float(self._ilk["catbed_min_c"]),
             0x0808, "MEDIUM",
             f"PROP_CATBED_UNDERTEMP: {s.catbed_temp_c:.0f} C"),
            ("_prev_catbed_over", s.catbed_temp_c > s.lim_catbed_max_c,
             0x0809, "HIGH", f"PROP_CATBED_OVERTEMP: {s.catbed_temp_c:.0f} C"),
            ("_prev_leak", s.leak_rate_mg_s > 0.0, 0x080B, "HIGH",
             f"PROP_LEAK_DETECTED: {s.leak_rate_mg_s:.2f} mg/s"),
        ]
        for attr, cond, eid, sev, text in checks:
            prev = getattr(s, attr)
            if cond and not prev:
                self._emit(eid, sev, text)
            setattr(s, attr, bool(cond))

    def _publish(self, sp: dict[int, float]) -> None:
        s = self._state
        firing = s.state == ST_FIRING
        p_noise = self.rng.gauss(0.0, 0.005)
        # 0x0B09 is the thrust ESTIMATE at the current feed pressure (incl.
        # any degradation), published continuously so the S12 "low thrust"
        # monitor is meaningful outside a burn too; the physical thrust is
        # only non-zero while FIRING (0x0B08 fcv_state = 1).
        f_n = self._thrust_n(s.tank_p_bar)
        sp[0x0B00] = float(s.state)
        sp[0x0B01] = s.tank_p_bar + p_noise
        sp[0x0B02] = s.m_prop_kg * 1e3                       # g
        sp[0x0B03] = 100.0 * s.m_prop_kg / max(self._m_prop0_kg, 1e-9)
        sp[0x0B04] = s.tank_temp_c
        sp[0x0B05] = s.catbed_temp_c
        sp[0x0B06] = float(s.latch_a)
        sp[0x0B07] = float(s.latch_b)
        sp[0x0B08] = 1.0 if s.fcv_open else 0.0
        sp[0x0B09] = f_n * 1e3                               # mN
        sp[0x0B0A] = self._isp_s(s.tank_p_bar)
        sp[0x0B0B] = s.dv_accum_m_s + s.burn_dv_mag_m_s
        sp[0x0B0C] = self.dv_remaining_m_s()
        sp[0x0B0D] = float(s.burn_status)
        sp[0x0B0E] = s.burn_dv_cmd_m_s
        sp[0x0B0F] = s.burn_dv_achieved_m_s
        sp[0x0B10] = s.burn_time_cmd_s
        sp[0x0B11] = s.burn_elapsed_s
        sp[0x0B12] = float(s.burn_count)
        sp[0x0B13] = s.total_on_time_s
        sp[0x0B14] = float(s.abort_reason)
        sp[0x0B15] = float(s.arm_inhibit_mask)
        sp[0x0B16] = s.leak_rate_mg_s
        # Thrust misalignment torque (uN*m) — aocs_basic reads this into its
        # disturbance-torque path (about body Y: thrust +X, offset in Z).
        # Non-zero only while the thruster actually fires.
        sp[0x0B17] = (f_n * self._thrust_offset_m * 1e6) if firing else 0.0
        sp[0x0B18] = self._sc_mass_kg()
        eng = getattr(self, "_engine", None)
        orbit = getattr(eng, "orbit", None) if eng else None
        sp[0x0B19] = float(getattr(orbit, "epoch_version", 0)) if orbit else 0.0
        try:
            sp[0x0B1A] = float(orbit.semi_major_axis_km()) if orbit else 0.0
        except Exception:
            sp[0x0B1A] = 0.0
        # Live power draw for EPS actuator-state billing (Phase B convention:
        # bill the actuator state, not the line switch): valve/electronics
        # load while ARMED/FIRING + the catbed heater when the TCS thruster
        # circuit (0x041B) is energised.
        draw = self._valve_power_w if s.state in (ST_ARMED, ST_FIRING) else 0.0
        if bool(sp.get(0x041B, 0)):
            draw += self._catbed_heater_w
        sp[0x0B1B] = draw

    # ── Commands ─────────────────────────────────────────────────────

    def handle_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        s = self._state
        command = cmd.get("command", "")

        if command == "set_burn":
            dv = float(cmd.get("dv_m_s", 0.0))
            if not (0.0 < dv <= 20.0):
                return {"success": False, "error_code": 0x0B0C,
                        "message": f"dv {dv:.3f} m/s outside (0, 20] m/s"}
            if dv > self.dv_remaining_m_s():
                return {"success": False, "error_code": 0x0B0D,
                        "message": "dv exceeds remaining delta-V envelope"}
            if s.state == ST_FIRING:
                return {"success": False, "error_code": 0x0B0E,
                        "message": "burn in progress"}
            s.burn_dv_cmd_m_s = dv
            s.burn_direction = int(cmd.get("direction", 0)) & 1
            s.burn_mode = int(cmd.get("mode", 1)) & 1
            s.burn_t_start_cuc = int(cmd.get("t_start_cuc", 0))
            s.burn_time_cmd_s = self.predict_burn_time_s(dv)
            s.burn_loaded = True
            s.burn_loaded_met_s = s.met_s
            return {"success": True,
                    "message": f"burn loaded: {dv:.3f} m/s "
                               f"{'retrograde' if s.burn_direction else 'prograde'}, "
                               f"predicted {s.burn_time_cmd_s:.1f} s"}

        if command == "arm":
            code = int(cmd.get("arm_code", 0))
            if code != self._arm_code:
                return {"success": False, "error_code": 0x0B0F,
                        "message": "bad arm code"}
            if s.state == ST_INHIBIT or s.inhibit_latched or s.ground_inhibit:
                c, m = _INTERLOCK_CODES[INH_LATCHED]
                return {"success": False, "error_code": c, "message": m}
            if s.state != ST_SAFED:
                return {"success": False, "error_code": 0x0B10,
                        "message": f"arm refused in state {s.state}"}
            mask = self._interlock_mask(require_burn=True)
            s.arm_inhibit_mask = mask
            if mask:
                # Report the LOWEST failing interlock's distinct code
                for bit in sorted(_INTERLOCK_CODES):
                    if mask & bit:
                        c, m = _INTERLOCK_CODES[bit]
                        self._emit(0x0802, "MEDIUM",
                                   f"PROP_ARM_REJECTED: {m} "
                                   f"(mask=0x{mask:03X})")
                        return {"success": False, "error_code": c,
                                "message": f"{m} (inhibit mask 0x{mask:03X})"}
            s.state = ST_ARMED
            s.burn_status = 1
            s.abort_reason = AB_NONE
            self._emit(0x0800, "MEDIUM",
                       f"PROP_ARMED: {s.burn_dv_cmd_m_s:.3f} m/s "
                       f"{'retrograde' if s.burn_direction else 'prograde'}")
            return {"success": True, "message": "armed"}

        if command == "disarm":
            if s.state == ST_FIRING:
                self._abort(AB_GROUND, "disarm during burn")
            elif s.state == ST_ARMED:
                s.state = ST_SAFED
                s.burn_status = 0
                s.fcv_open = False
                self._emit(0x0801, "INFO", "PROP_DISARMED")
            return {"success": True}

        if command == "execute":
            if s.state != ST_ARMED:
                return {"success": False, "error_code": 0x0B11,
                        "message": "PROP_EXECUTE refused: not ARMED "
                                   "(PROP_SET_BURN + PROP_ARM first)"}
            s.state = ST_FIRING
            s.burn_status = 2
            s.fcv_open = True
            s.burn_elapsed_s = 0.0
            s.burn_dv_at_m_s = 0.0
            s.burn_dv_lat_m_s = 0.0
            s.burn_dv_mag_m_s = 0.0
            eng = getattr(self, "_engine", None)
            utc = getattr(getattr(eng, "orbit", None), "utc", None) if eng else None
            s.burn_start_utc = utc.isoformat() if utc else ""
            self._emit(0x0803, "MEDIUM",
                       f"PROP_BURN_START: {s.burn_dv_cmd_m_s:.3f} m/s, "
                       f"predicted {s.burn_time_cmd_s:.1f} s")
            return {"success": True}

        if command == "abort":
            if s.state == ST_FIRING:
                self._abort(AB_GROUND, "ground abort")
            elif s.state == ST_ARMED:
                s.state = ST_SAFED
                s.burn_status = 4
                s.abort_reason = AB_GROUND
                s.burn_loaded = False
                self._emit(0x0805, "HIGH", "PROP_BURN_ABORTED: ground abort "
                                           "while armed (reason=6)")
            return {"success": True}

        if command == "iso_open" or command == "iso_close":
            branch = int(cmd.get("branch", 2))
            want = VLV_OPEN if command == "iso_open" else VLV_CLOSED
            moved, faults = [], []
            for name in (["latch_a"] if branch == 0 else
                         ["latch_b"] if branch == 1 else
                         ["latch_a", "latch_b"]):
                if getattr(s, name) == VLV_FAULT:
                    faults.append(name)
                    self._emit(0x080A, "HIGH",
                               f"PROP_VALVE_FAULT: {name} stuck, "
                               f"{command} had no effect")
                else:
                    setattr(s, name, want)
                    moved.append(name)
            if faults and not moved:
                return {"success": False, "error_code": 0x0B12,
                        "message": f"valve(s) {'/'.join(faults)} stuck"}
            return {"success": True,
                    "message": f"{command} {'/'.join(moved) or 'none'}"
                               + (f" ({'/'.join(faults)} stuck)" if faults else "")}

        if command == "set_limits":
            s.lim_att_err_max_deg = float(cmd.get("att_err_max_deg",
                                                  s.lim_att_err_max_deg))
            s.lim_p_min_bar = float(cmd.get("p_min_bar", s.lim_p_min_bar))
            s.lim_catbed_max_c = float(cmd.get("catbed_max_c",
                                               s.lim_catbed_max_c))
            s.lim_t_burn_max_s = float(cmd.get("t_burn_max_s",
                                               s.lim_t_burn_max_s))
            return {"success": True}

        if command == "inhibit_set":
            on = bool(cmd.get("on", True))
            s.ground_inhibit = on
            if on and s.state == ST_FIRING:
                self._abort(AB_GROUND, "ground inhibit set during burn")
            elif on and s.state == ST_ARMED:
                s.state = ST_SAFED
                s.burn_status = 0
            return {"success": True}

        if command == "inhibit_clr":
            key = int(cmd.get("key", 0))
            if key != self._arm_code:
                return {"success": False, "error_code": 0x0B13,
                        "message": "bad inhibit-clear key"}
            s.ground_inhibit = False
            s.inhibit_latched = False
            s.abort_count = 0
            if s.state == ST_INHIBIT:
                s.state = ST_SAFED
                s.burn_status = 0
            return {"success": True, "message": "inhibit cleared"}

        if command == "get_status":
            return {"success": True, "status": {
                "state": s.state, "tank_p_bar": round(s.tank_p_bar, 3),
                "propellant_g": round(s.m_prop_kg * 1e3, 2),
                "catbed_c": round(s.catbed_temp_c, 1),
                "dv_accum_m_s": round(s.dv_accum_m_s, 3),
                "dv_remaining_m_s": round(self.dv_remaining_m_s(), 2),
                "arm_inhibit_mask": s.arm_inhibit_mask,
                "abort_reason": s.abort_reason,
            }}

        return {"success": False, "message": f"Unknown: {command}"}

    # ── Engine coupling ──────────────────────────────────────────────

    def consume_pending_impulse(self) -> dict | None:
        """Pop the burn impulse for the engine to apply to the orbit."""
        s = self._state
        imp = s.pending_impulse
        if imp is not None:
            s.pending_impulse = None
            s.dv_accum_m_s += imp.get("dv_mag_m_s", 0.0)
            # Reset the per-burn integrators now that the dv is banked
            s.burn_dv_mag_m_s = 0.0
            s.burn_dv_at_m_s = 0.0
            s.burn_dv_lat_m_s = 0.0
        return imp

    # ── Failures ─────────────────────────────────────────────────────

    # ─── Failure injection ───────────────────────────────────────────
    # Declarative parameter specs — single source for the instructor form,
    # FailureManager validation and the scenario content gate.
    FAILURE_SPECS = {
        "leak": FailureSpec(
            name="leak", label="Propellant leak",
            effect="Continuous propellant loss: tank mass (0x0B02) falls and "
                   "blowdown pressure (0x0B01) decays faster than the burn "
                   "plan predicts.",
            magnitude=None,
            params=(number("rate_mg_s", "Leak rate", unit="mg/s", lo=0.0,
                           hi=1000.0, default=2.0, step=0.1),),
        ),
        "latch_stuck_open": FailureSpec(
            name="latch_stuck_open", label="Latch valve stuck open",
            effect="Selected latch valve reads FAULT and cannot be closed; "
                   "with a seeping FCV the module loses propellant. Emits "
                   "PROP_VALVE_FAULT (0x080A).",
            magnitude=None,
            params=(
                enum("branch", "Latch valve", [0, 1],
                     labels=["Latch A (branch 0)", "Latch B (branch 1)"],
                     default=0),
                number("rate_mg_s", "Resulting leak rate", unit="mg/s", lo=0.0,
                       hi=1000.0, default=20.0, step=0.1),
            ),
        ),
        "thrust_degraded": FailureSpec(
            name="thrust_degraded", label="Thruster underperformance",
            effect="Scales delivered thrust (catalyst bed degradation): the "
                   "burn under-delivers dv. Emits PROP_UNDERPERFORMANCE "
                   "(0x080C).",
            magnitude=ParamSpec(
                name="magnitude", type=T_FLOAT, label="Performance factor",
                unit="fraction of nominal thrust", min=0.0, max=1.0,
                default=0.6, step=0.01,
                description="1.0 = nominal, 0.0 = no thrust at all."),
        ),
        "tank_overpressure": FailureSpec(
            name="tank_overpressure", label="Tank overpressure",
            effect="Pressurant thermal runaway: tank pressure (0x0B01) is "
                   "forced to 24 bar + 4 bar x magnitude, over the red limit.",
            magnitude=ParamSpec(
                name="magnitude", type=T_FLOAT, label="Overpressure severity",
                unit="fraction (1.0 = +4 bar over 24 bar)", min=0.0, max=4.0,
                default=1.0, step=0.05),
        ),
    }

    def inject_failure(self, failure: str, magnitude: float = 1.0, **kw) -> None:
        s = self._state
        if failure == "leak":
            s.leak_rate_mg_s = float(kw.get("rate_mg_s", magnitude * 2.0))
        elif failure == "latch_stuck_open":
            branch = int(kw.get("branch", 0))
            name = "latch_a" if branch == 0 else "latch_b"
            setattr(s, name, VLV_FAULT)
            # A stuck-open latch with a seeping FCV loses propellant slowly
            s.leak_rate_mg_s = max(s.leak_rate_mg_s,
                                   float(kw.get("rate_mg_s", 20.0)))
            self._emit(0x080A, "HIGH",
                       f"PROP_VALVE_FAULT: {name} stuck open")
        elif failure == "thrust_degraded":
            s.perf_factor = max(min(float(magnitude), 1.0), 0.0)
            self._emit(0x080C, "MEDIUM",
                       f"PROP_UNDERPERFORMANCE: thrust factor "
                       f"{s.perf_factor:.2f}")
        elif failure == "tank_overpressure":
            # Thermal-runaway teaching case: pressurant heats, P climbs and
            # STAYS up (0x0807 PROP_TANK_PRESSURE_HIGH, arm interlock refuses
            # the burn) until the injection is cleared.
            s.tank_overpressure_bar = 24.0 + magnitude * 4.0
            s.tank_p_bar = max(s.tank_p_bar, s.tank_overpressure_bar)

    def clear_failure(self, failure: str, **kw) -> None:
        s = self._state
        if failure == "leak":
            s.leak_rate_mg_s = 0.0
        elif failure == "latch_stuck_open":
            branch = int(kw.get("branch", 0))
            name = "latch_a" if branch == 0 else "latch_b"
            if getattr(s, name) == VLV_FAULT:
                setattr(s, name, VLV_OPEN)
            s.leak_rate_mg_s = 0.0
        elif failure == "thrust_degraded":
            s.perf_factor = 1.0
        elif failure == "tank_overpressure":
            s.tank_overpressure_bar = 0.0
            s.tank_p_bar = self._tank_pressure(s.m_prop_kg)

    # ── Telemetry / state ────────────────────────────────────────────

    def get_telemetry(self) -> dict[int, float]:
        s = self._state
        return {0x0B00: float(s.state), 0x0B01: s.tank_p_bar,
                0x0B02: s.m_prop_kg * 1e3}

    def get_state(self) -> dict[str, Any]:
        import dataclasses
        from smo_common.state import rng_state_to_json
        d = dataclasses.asdict(self._state)
        d["_rng_state"] = rng_state_to_json(self.rng.getstate())
        return d

    def set_state(self, state: dict[str, Any]) -> None:
        # Container fields are deep-copied by restore_dataclass_state: the
        # plain setattr this used to do aliased the snapshot's own dicts and
        # lists into live model state (G2-f).
        from smo_common.state import restore_dataclass_state, restore_rng
        restore_rng(self.rng, state.get("_rng_state"))
        restore_dataclass_state(self._state, state, skip=("_rng_state",))

    # ── Internals ────────────────────────────────────────────────────

    # Defaults for cross-subsystem caches before the first tick
    _aocs_mode: int = -1
    _bat_soc: float = 100.0
    _sc_phase: int = 6

    def _emit(self, event_id: int, severity: str, description: str) -> None:
        eng = getattr(self, "_engine", None)
        if not eng:
            return
        try:
            eng._model_event_queue.put_nowait({
                "event_id": event_id, "severity": severity,
                "subsystem": "prop", "description": description,
            })
        except Exception:
            pass

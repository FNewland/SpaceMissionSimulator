"""SMO Simulator — Basic TCS Model.

Thermal zones, heater thermostat control, FPA cooler.
Enhanced with heater setpoint commands, auto mode, sensor drift, heater stuck-on.

Active thermal control (EOSAT-1):
  - Battery heater: thermostat-controlled
  - OBC heater: manual control only (via EPS power line state)
  - Thruster (catalyst-bed) heater: REAL as of Phase D — 12 W catbed preheat
    for the green-monoprop module, commanded via HEATER_THRUSTER (S8 func 42,
    circuit 2), powered from the EPS `prop` line (0x014A), state published at
    0x041B and the bed temperature at 0x0409 (the propulsion arm interlock).
    Manual by default; HEATER_AUTO_MODE circuit=2 enables the thermostat hold.
  - Panel temperatures coupled to solar illumination via EPS per-panel currents
"""
import math
import random
from dataclasses import dataclass, field
from typing import Any
from smo_common.config.fpa_envelope import FPAEnvelope
from smo_common.config.schemas import TCSConfig
from smo_common.models.subsystem import SubsystemModel
from smo_simulator.failure_spec import (
    FailureSpec, ParamSpec, T_FLOAT, T_BOOL, enum, number,
)
# The FPA setpoint arrives on the same wire field (S8 func 36, uint16
# centi-kelvin) whether it is routed to the payload or to TCS, so both sides
# must apply the same decode tolerance or the two will disagree about which
# setpoints are legal. One helper, imported.
from smo_simulator.models.payload_basic import snap_to_envelope

#: Schema defaults, so the model's fallbacks and `subsystems/tcs.yaml`'s
#: accepted keys cannot drift apart (DISPLAY_REQUESTS_PAYLOAD.md §8.2).
_TCS_DEFAULTS = TCSConfig()

@dataclass
class TCSState:
    temp_panel_px: float = 15.0; temp_panel_mx: float = 12.0
    temp_panel_py: float = 20.0; temp_panel_my: float = 18.0
    temp_panel_pz: float = 10.0; temp_panel_mz: float = 8.0
    temp_obc: float = 25.0; temp_battery: float = 15.0
    temp_fpa: float = -5.0; temp_thruster: float = 5.0
    htr_battery: bool = False; htr_obc: bool = False
    htr_thruster: bool = False
    # cooler_fpa: the COMMANDED cooler state — one flag, written by every
    # command path (TCS func 43, EPS func 17, PL_SET_MODE, S2 device — brief
    # 05 WP-2, last command wins). cooler_fpa_active is the EFFECTIVE state
    # (commanded AND EPS line 4 powered AND not failed) — published at 0x040C
    # and the one the thermal physics and EPS billing use.
    cooler_fpa: bool = False
    cooler_fpa_active: bool = False
    # 2026-08b: coldest the cooler can hold at the present sink and load, and
    # how much of that is left below the commanded setpoint. Negative margin =
    # the setpoint is out of reach and FPA READY will not come up.
    fpa_cooler_floor_c: float = 0.0
    fpa_cooler_margin_k: float = 0.0
    htr_battery_failed: bool = False; htr_obc_failed: bool = False
    htr_thruster_failed: bool = False; cooler_failed: bool = False
    obc_internal_heat_w: float = 0.0
    # Manual override flags (False = auto thermostat, True = manual control)
    htr_battery_manual: bool = False
    htr_obc_manual: bool = False
    # Thruster (catbed) heater defaults to MANUAL: preheat is an explicit
    # NOM-013 step via HEATER_THRUSTER func 42, not an automatic thermostat.
    # HEATER_AUTO_MODE circuit=2 switches it to the thermostat hold.
    htr_thruster_manual: bool = True
    # Sensor drift offsets (failure injection)
    sensor_drift: dict = field(default_factory=dict)
    # Heater stuck-on flags
    htr_battery_stuck_on: bool = False
    htr_obc_stuck_on: bool = False
    htr_thruster_stuck_on: bool = False
    # Heater open-circuit flags (heater appears ON but provides no heat)
    htr_battery_open_circuit: bool = False
    htr_obc_open_circuit: bool = False
    htr_thruster_open_circuit: bool = False
    # ── Phase 4: Flight hardware realism ──
    htr_duty_battery: float = 0.0    # Battery heater duty cycle (0-100%)
    htr_duty_obc: float = 0.0       # OBC heater duty cycle (0-100%)
    htr_duty_thruster: float = 0.0  # Thruster heater duty cycle (0-100%)
    htr_total_power_w: float = 0.0  # Total heater power consumption
    # Duty cycle tracking (cumulative on-time in sliding window)
    _htr_on_accum: dict = field(default_factory=lambda: {
        "battery": 0.0, "obc": 0.0, "thruster": 0.0
    })
    _duty_window_s: float = 600.0  # 10-minute sliding window
    # ── Heater setpoints and limits ──
    # Defaults match configs/eosat1/subsystems/tcs.yaml; configure() re-syncs
    # these from the heaters: block (they are the LIVE thermostat band —
    # previously the config band was loaded into _THERMOSTAT while these
    # state fields kept their old defaults and silently won).
    htr_battery_setpoint_on_c: float = 5.0
    htr_battery_setpoint_off_c: float = 10.0
    htr_obc_setpoint_on_c: float = 5.0
    htr_obc_setpoint_off_c: float = 10.0
    # Phase D: catbed hold band (auto mode) — thermostat on below 190 C,
    # off at 230 C; the propulsion arm interlock needs >= 180 C (prop.yaml).
    htr_thruster_setpoint_on_c: float = 190.0
    htr_thruster_setpoint_off_c: float = 230.0
    htr_battery_duty_limit_pct: float = 100.0
    htr_obc_duty_limit_pct: float = 100.0
    htr_thruster_duty_limit_pct: float = 100.0
    # ── Decontamination heating ──
    decontamination_active: bool = False
    decontam_start_time: float = 0.0  # Relative to tick
    decontam_fpa_target_c: float = 50.0
    # ── Event tracking (to avoid repeated events) ──
    _prev_htr_battery_on: bool = False
    _prev_htr_obc_on: bool = False
    _prev_htr_thruster_on: bool = False
    _prev_temp_battery: float = 15.0
    _prev_temp_obc: float = 25.0
    _prev_temp_fpa: float = -5.0
    _prev_htr_battery_failed: bool = False
    _prev_htr_obc_failed: bool = False
    _prev_htr_thruster_failed: bool = False
    _prev_htr_battery_stuck_on: bool = False
    _prev_htr_obc_stuck_on: bool = False
    _prev_htr_thruster_stuck_on: bool = False
    _prev_fpa_ready: bool = False
    _prev_thermal_runaway: bool = False
    # ── Thermal zone coupling ──
    _temp_history: list = field(default_factory=lambda: [])  # For rate calculation

    # S2 Device Access — device on/off states (device_id -> on/off)
    # NOTE (DEFECT 5): S2 device commands are NOT used for EOSAT-1 thermal control.
    # All heater control is via S8 functional commands (HEATER_*, func_id 40-49).
    # This dictionary is kept for future mission variants with multi-zone control.
    device_states: dict = field(default_factory=lambda: {
        0x0300: True,   # Heater zone 1 (unused for EOSAT-1)
        0x0301: True,   # Heater zone 2 (unused for EOSAT-1)
        0x0302: True,   # Heater zone 3 (unused for EOSAT-1)
        0x0303: True,   # Heater zone 4 (unused for EOSAT-1)
        0x0304: True,   # Heater zone 5 (unused for EOSAT-1)
        0x0305: True,   # Heater zone 6 (unused for EOSAT-1)
        0x0306: True,   # Heater zone 7 (unused for EOSAT-1)
        0x0307: True,   # Heater zone 8 (unused for EOSAT-1)
        0x0308: True,   # Heater zone 9 (unused for EOSAT-1)
        0x0309: True,   # Heater zone 10 (unused for EOSAT-1)
        0x030A: True,   # Thermistor array (unused for EOSAT-1)
        0x030B: True,   # Decontamination heater (unused for EOSAT-1)
    })


class TCSBasicModel(SubsystemModel):
    _HEATER_POWER = {"battery": 6.0, "obc": 4.0, "thruster": 12.0}
    _THERMOSTAT = {"battery": (5.0, 1.0), "obc": (10.0, 5.0),
                   "thruster": (230.0, 190.0)}
    # Phase D retires the last leg of defect #31: the thruster circuit now
    # drives the REAL green-monoprop catalyst-bed heater (12 W, EPS `prop`
    # line), so every heater circuit accepts setpoint/auto-mode commands.
    _NO_HEATER_CIRCUITS = frozenset()
    # Thruster node (Phase D): small catalyst bed + heater blanket,
    # C = 50 J/K, tau = 1200 s => UA = 0.042 W/K, so 12 W has ~288 K of
    # authority and reaches the 180 C arm interlock in ~18 min, 200 C in
    # ~22 min — the "12 W for 20 min = 4.0 Wh" preheat of the design sheet.
    _TAU = {"panel_px":600,"panel_mx":600,"panel_py":500,"panel_my":500,
            "panel_pz":800,"panel_mz":800,"obc":900,"battery":1200,"fpa":120,"thruster":1200}
    _CAP = {"panel_px":5000,"panel_mx":5000,"panel_py":4000,"panel_my":4000,
            "panel_pz":3000,"panel_mz":3000,"obc":800,"battery":2000,"fpa":100,"thruster":50}

    def __init__(self):
        self._state = TCSState()
        # Per-model RNG (plan A-2). Standalone construction gets a fresh
        # unseeded stream so existing unit tests keep working; the engine
        # replaces this with a Random derived from simulation.yaml `seed`.
        import random as _random
        self.rng = _random.Random(20260731)  # deterministic standalone fallback; engine overrides via derive_rng
        self._param_ids: dict[str, int] = {}
        self._fpa_cooler_target: float = -5.0
        # Envelope defaults, replaced by configure() from tcs.yaml. Set here
        # too so a directly instantiated model still has the readiness rule.
        self._fpa_env = FPAEnvelope()
        self._engine = None
        # Instance copies so configure() doesn't mutate class-level dicts
        self._TAU = dict(TCSBasicModel._TAU)
        self._CAP = dict(TCSBasicModel._CAP)
        self._HEATER_POWER = dict(TCSBasicModel._HEATER_POWER)
        self._THERMOSTAT = dict(TCSBasicModel._THERMOSTAT)
        # Thermal zone conductance matrix (conduction between adjacent zones) [W/K]
        self._zone_conductance = {
            ("obc", "battery"): 0.5,
            ("battery", "obc"): 0.5,
            ("structure", "electronics"): 0.8,
            ("electronics", "structure"): 0.8,
            ("payload", "structure"): 0.4,
            ("structure", "payload"): 0.4,
        }
        # Radiation to space (effective conductance)
        self._space_radiation_g = 0.2  # W/K (radiative coupling to 3K sink)
        # Event thresholds (can be overridden by config)
        self._temp_warning_limits = {
            "battery": (0.0, 40.0),  # (low_warn, high_warn)
            "obc": (5.0, 50.0),
            "fpa": (-50.0, -5.0),
        }
        self._temp_alarm_limits = {
            "battery": (-10.0, 50.0),
            "obc": (-5.0, 60.0),
            "fpa": (-60.0, 0.0),
        }
        self._thermal_runaway_rate = 2.0  # deg/min
        # Thermal environment (simulation.yaml tcs.environment overrides these)
        self._env = {
            "eclipse_external_c": -30.0,
            "eclipse_internal_c": 10.0,
            "sunlit_external_base_c": -10.0,
            "sunlit_external_beta_gain_c": 50.0,
            "sunlit_internal_c": 12.0,
            # Battery-zone sink (brief 05 WP-3). None = inherit the internal
            # sink (legacy behaviour, thermostat unreachable).
            "eclipse_battery_c": None,
            "sunlit_battery_c": None,
            "panel_solar_heat_fraction": 0.3,
            # Trim multiplier on the physical 1/UA panel solar gain (WP-8).
            "panel_solar_heat_gain": 1.0,
        }
        # Bounded cooler heat lift (brief 05 WP-1): the FPA cannot be driven
        # more than this many kelvin below its ambient sink. Retained as the
        # ABSOLUTE ceiling on the machine; the achievable lift at any instant
        # is now computed from the cooler's efficiency (see
        # `_fpa_achievable_lift_k`) and is smaller than this almost everywhere.
        self._fpa_cooler_max_lift_k = 40.0
        # ── Cooler efficiency model (2026-08b) — see `_fpa_achievable_lift_k`
        # The numbers live in TCSConfig (and therefore in subsystems/tcs.yaml),
        # not here: they used to be literals in this constructor AND in
        # configure(), while TCSConfig — a StrictModel — did not declare them,
        # so a YAML override was REJECTED by the loader and these were the only
        # live values. One source now.
        self._fpa_cooler_power_w = _TCS_DEFAULTS.fpa_cooler_power_w
        self._fpa_cooler_carnot_fraction = _TCS_DEFAULTS.fpa_cooler_carnot_fraction
        self._fpa_imaging_load_w = _TCS_DEFAULTS.fpa_imaging_load_w

    # ── FPA cooler authority (2026-08b) ─────────────────────────────────
    #
    # WHAT WAS WRONG. `fpa_cooler_max_lift_k` was a flat 40 K with no coupling
    # whatever to `fpa_cooler_power_w`, so the cooler had exactly as much
    # authority at a 39 K lift as at a 1 K lift and "the cooler is struggling"
    # was not a state the model could enter. The number was also not
    # achievable by the machine the envelope docstring named. Holding -15 C
    # against the +12 C internal sink is a 27 K lift; the FPA node's UA is
    # C/tau = 100/120 = 0.833 W/K, so the parasitic heat to be pumped is
    # 0.833 x 27 = 22.5 W, from 15 W of input — a COP of 1.50. A single-stage
    # Bi2Te3 thermoelectric cannot do that. At its optimum-COP current,
    #
    #     COP_max = (Tc/dT) . (g - Th/Tc)/(g + 1),  g = sqrt(1 + Z.Tm)
    #
    # (Goldsmid's standard result, reproduced in every TEC selection guide —
    # e.g. Ferrotec, "Mathematical Modeling of Modules"), with Tc = 258.15 K,
    # Th = 285.15 K and the ZT ~ 0.73 of commercial bismuth telluride at this
    # mean temperature, gives COP_max = 0.88. The required 1.50 is 1.7x what
    # the material allows, and at the 40 K flat lift the model claimed it is
    # worse still: a single-stage module's dTmax is only ~70 K at zero heat
    # load, where the COP is zero by definition.
    #
    # WHAT IT IS INSTEAD. 1.50 is 15.7 % of the Carnot COP at this lift
    # (Tc/dT = 258.15/27 = 9.56). That is squarely inside the 10-25 % of
    # Carnot that miniature Stirling / free-piston space coolers achieve at
    # near-ambient lifts, and cooled small-satellite EO detectors use exactly
    # that class of machine (the CubeSat cryocooler literature — Lockheed
    # Martin Microcryocooler, AIM SX030/SF-100 — is all Stirling/pulse-tube,
    # not thermoelectric). So the hardware is reclassified: this is a
    # miniature Stirling-class cooler, `fpa_cooler_carnot_fraction` of Carnot,
    # and the "Peltier envelope" wording in the refusal messages and in
    # smo_common.config.fpa_envelope is what was wrong, not the mission.
    #
    # WHAT THE MODEL NOW DOES. Cooling authority is the steady state where the
    # cooler's heat lift equals the heat arriving at the node:
    #
    #     Qc(dT) = eta . P . Tc/dT              (fraction of Carnot)
    #     Qc(dT) = UA.dT + Q_load               (steady state, Tc = Th - dT)
    #  => UA.dT^2 + (Q_load + eta.P).dT - eta.P.Th = 0
    #
    # solved for the positive root. Authority therefore FALLS as the sink
    # warms and as the detector's own dissipation rises, which is what makes
    # "the cooler is struggling under imaging load" — the thing COM-011's
    # off-nominal section asks the trainee to diagnose — a real, reachable,
    # measurable state instead of a story.
    #
    # Sized (fpa_cooler_carnot_fraction 0.23, 15 W, UA 0.833 W/K):
    #   sink +12 C, idle       32.3 K of lift -> floor -20.3 C  (-20 C just holds)
    #   sink +12 C, imaging    31.2 K         -> floor -19.2 C  (-20 C does NOT)
    #   sink +20 C, idle       32.8 K         -> floor -12.8 C  (-15 C does NOT)
    # The nominal -15 C setpoint keeps ~5 K of margin, so the shipped mission
    # is unchanged; the margin is now finite, visible (0x0424/0x0425) and
    # spendable, which is the teaching point.
    def _fpa_achievable_lift_k(self, sink_c: float, q_load_w: float) -> float:
        """Steady-state lift (K) the cooler can hold below `sink_c`."""
        ua = self._CAP.get("fpa", 100.0) / max(self._TAU.get("fpa", 120.0), 1e-6)
        eta_p = max(0.0, self._fpa_cooler_carnot_fraction
                    * self._fpa_cooler_power_w)
        if eta_p <= 0.0:
            return 0.0
        th = sink_c + 273.15
        b = q_load_w + eta_p
        disc = b * b + 4.0 * ua * eta_p * th
        lift = (math.sqrt(max(0.0, disc)) - b) / (2.0 * ua)
        # The absolute machine ceiling still applies: no working fluid, no
        # regenerator and no number of stages here goes past it.
        return max(0.0, min(lift, self._fpa_cooler_max_lift_k))

    @property
    def name(self) -> str: return "tcs"

    def configure(self, config: dict[str, Any]) -> None:
        self._param_ids = config.get("param_ids", {
            "temp_panel_px":0x0400,"temp_panel_mx":0x0401,"temp_panel_py":0x0402,
            "temp_panel_my":0x0403,"temp_panel_pz":0x0404,"temp_panel_mz":0x0405,
            "temp_obc":0x0406,"temp_battery":0x0407,"temp_fpa":0x0408,
            "temp_thruster":0x0409,"htr_battery":0x040A,"htr_obc":0x040B,
            "cooler_fpa":0x040C,"htr_thruster":0x041B,
        })
        # Override thermal zone constants from config
        for zone in config.get("zones", []):
            name = zone.get("name") if isinstance(zone, dict) else getattr(zone, "name", None)
            if not name:
                continue
            tau = zone.get("time_constant_s") if isinstance(zone, dict) else getattr(zone, "time_constant_s", None)
            cap = zone.get("capacitance_j_per_c") if isinstance(zone, dict) else getattr(zone, "capacitance_j_per_c", None)
            init_temp = zone.get("initial_temp_c") if isinstance(zone, dict) else getattr(zone, "initial_temp_c", None)
            if tau is not None:
                self._TAU[name] = tau
            if cap is not None:
                self._CAP[name] = cap
            if init_temp is not None:
                attr = f"temp_{name}"
                if hasattr(self._state, attr):
                    setattr(self._state, attr, init_temp)
        # Override heater constants from config
        for heater in config.get("heaters", []):
            name = heater.get("name") if isinstance(heater, dict) else getattr(heater, "name", None)
            if not name:
                continue
            power = heater.get("power_w") if isinstance(heater, dict) else getattr(heater, "power_w", None)
            on_t = heater.get("on_temp_c") if isinstance(heater, dict) else getattr(heater, "on_temp_c", None)
            off_t = heater.get("off_temp_c") if isinstance(heater, dict) else getattr(heater, "off_temp_c", None)
            if power is not None:
                self._HEATER_POWER[name] = power
            if on_t is not None and off_t is not None:
                self._THERMOSTAT[name] = (off_t, on_t)
                # Sync the live per-circuit state setpoints too — the
                # thermostat reads these, and they used to keep stale
                # dataclass defaults that silently overrode the config.
                if hasattr(self._state, f"htr_{name}_setpoint_on_c"):
                    setattr(self._state, f"htr_{name}_setpoint_on_c", float(on_t))
                    setattr(self._state, f"htr_{name}_setpoint_off_c", float(off_t))
        # FPA cooler target
        self._fpa_cooler_target = config.get("fpa_cooler_target_c", -5.0)
        self._fpa_cooler_max_lift_k = float(
            config.get("fpa_cooler_max_lift_k", 40.0))
        # Cooler efficiency model — now REAL configuration: TCSConfig declares
        # all three, so subsystems/tcs.yaml can set them and the loader accepts
        # the file. The fallbacks are the schema defaults, never fresh literals.
        self._fpa_cooler_power_w = float(
            config.get("fpa_cooler_power_w", self._fpa_cooler_power_w))
        self._fpa_cooler_carnot_fraction = float(
            config.get("fpa_cooler_carnot_fraction",
                       self._fpa_cooler_carnot_fraction))
        self._fpa_imaging_load_w = float(
            config.get("fpa_imaging_load_w", self._fpa_imaging_load_w))
        # FPA envelope — the single source for readiness and for the derived
        # limit bands (DECISIONS §3, ruled in G3-d).
        self._fpa_env = FPAEnvelope.from_config(config)

        # ── tcs.yaml thermal_coupling ────────────────────────────────────
        # Previously this section was never read (the TCSConfig schema dropped
        # it before the model saw it), so the YAML values were dead config and
        # the code literals below were the real model — defect #51.
        coupling = config.get("thermal_coupling", {}) or {}
        for pair_key, g in (coupling.get("zone_conductance", {}) or {}).items():
            if "_" not in str(pair_key):
                continue
            a, b = str(pair_key).split("_", 1)
            self._zone_conductance[(a, b)] = float(g)
            self._zone_conductance[(b, a)] = float(g)
        if coupling.get("space_radiation_g") is not None:
            self._space_radiation_g = float(coupling["space_radiation_g"])

        # ── tcs.yaml event_thresholds (same dead-config story) ───────────
        evt = config.get("event_thresholds", {}) or {}
        for zone, limits in (evt.get("temperature_warning", {}) or {}).items():
            if isinstance(limits, (list, tuple)) and len(limits) == 2:
                self._temp_warning_limits[zone] = (float(limits[0]), float(limits[1]))
        for zone, limits in (evt.get("temperature_alarm", {}) or {}).items():
            if isinstance(limits, (list, tuple)) and len(limits) == 2:
                self._temp_alarm_limits[zone] = (float(limits[0]), float(limits[1]))
        if evt.get("thermal_runaway_rate_deg_per_min") is not None:
            self._thermal_runaway_rate = float(evt["thermal_runaway_rate_deg_per_min"])

        # ── simulation.yaml `tcs.environment:` block (defect #51) ────────
        env = (config.get("simulation", {}) or {}).get("environment", {}) or {}
        self._env = dict(self._env)
        self._env.update({k: float(v) for k, v in env.items() if v is not None})
        # Effective battery sink: dedicated keys, else the internal sink.
        if self._env.get("eclipse_battery_c") is None:
            self._env["eclipse_battery_c"] = self._env["eclipse_internal_c"]
        if self._env.get("sunlit_battery_c") is None:
            self._env["sunlit_battery_c"] = self._env["sunlit_internal_c"]

    def _thermostat_control(self, circuit: str, temp: float) -> None:
        """Apply thermostat control for a heater circuit (auto mode only)."""
        s = self._state
        manual_flag = getattr(s, f"htr_{circuit}_manual", False)
        stuck_on = getattr(s, f"htr_{circuit}_stuck_on", False)
        failed = getattr(s, f"htr_{circuit}_failed", False)

        if stuck_on:
            # Heater stays on regardless of commands
            setattr(s, f"htr_{circuit}", True)
            return
        if manual_flag:
            # Manual mode — don't apply thermostat
            return
        if failed:
            setattr(s, f"htr_{circuit}", False)
            return

        # Get dynamic setpoints (allow override via commands)
        on_attr = f"htr_{circuit}_setpoint_on_c"
        off_attr = f"htr_{circuit}_setpoint_off_c"
        lo = getattr(s, on_attr, self._THERMOSTAT[circuit][1])
        hi = getattr(s, off_attr, self._THERMOSTAT[circuit][0])

        if temp <= lo:
            setattr(s, f"htr_{circuit}", True)
        elif temp >= hi:
            setattr(s, f"htr_{circuit}", False)

    def _apply_thermal_coupling(self, dt: float) -> None:
        """Apply conductive and radiative heat transfer between zones."""
        s = self._state
        # Coupling between battery and OBC (shared electronics box)
        g_bat_obc = self._zone_conductance.get(("battery", "obc"), 0.5)
        heat_flow = g_bat_obc * (s.temp_obc - s.temp_battery)
        s.temp_battery += (heat_flow / self._CAP["battery"]) * dt
        s.temp_obc -= (heat_flow / self._CAP["obc"]) * dt

        # Radiation cooling to space (all external zones)
        for zone in ["panel_px", "panel_mx", "panel_py", "panel_my", "panel_pz", "panel_mz"]:
            T = getattr(s, f"temp_{zone}", 0.0)
            # Stefan-Boltzmann: rad cooling proportional to T difference from 3K space
            rad_loss = self._space_radiation_g * (T - (-270.15))  # 3K in Celsius
            setattr(s, f"temp_{zone}", T - (rad_loss / self._CAP.get(zone, 1000)) * dt)

    def _generate_events(self, dt: float) -> None:
        """Generate TCS events with edge detection."""
        if not hasattr(self, '_engine') or not self._engine:
            return

        s = self._state
        events = []

        # Helper: emit event with edge detection
        def emit_if_changed(event_dict: dict, condition_now: bool, prev_attr: str) -> None:
            prev = getattr(s, prev_attr, False)
            if condition_now and not prev:
                events.append(event_dict)
                setattr(s, prev_attr, True)
            elif not condition_now and prev:
                setattr(s, prev_attr, False)

        # Temperature warning/alarm events
        bat_temp = s.temp_battery
        obc_temp = s.temp_obc
        fpa_temp = s.temp_fpa

        # Battery overtemp warning
        bat_warn_low, bat_warn_high = self._temp_warning_limits["battery"]
        emit_if_changed(
            {"event_id": 0x0400, "severity": "MEDIUM", "subsystem": "tcs",
             "description": f"TCS_OVERTEMP_WARNING: Battery {bat_temp:.1f}C"},
            (bat_temp > bat_warn_high),
            "_prev_bat_overtemp_warn"
        )

        # Battery overtemp alarm
        bat_alarm_low, bat_alarm_high = self._temp_alarm_limits["battery"]
        emit_if_changed(
            {"event_id": 0x0401, "severity": "HIGH", "subsystem": "tcs",
             "description": f"TCS_OVERTEMP_ALARM: Battery {bat_temp:.1f}C"},
            (bat_temp > bat_alarm_high),
            "_prev_bat_overtemp_alarm"
        )

        # Battery undertemp warning
        emit_if_changed(
            {"event_id": 0x0402, "severity": "MEDIUM", "subsystem": "tcs",
             "description": f"TCS_UNDERTEMP_WARNING: Battery {bat_temp:.1f}C"},
            (bat_temp < bat_warn_low),
            "_prev_bat_undertemp_warn"
        )

        # Battery undertemp alarm
        emit_if_changed(
            {"event_id": 0x0403, "severity": "HIGH", "subsystem": "tcs",
             "description": f"TCS_UNDERTEMP_ALARM: Battery {bat_temp:.1f}C"},
            (bat_temp < bat_alarm_low),
            "_prev_bat_undertemp_alarm"
        )

        # Heater on/off events
        if s.htr_battery != s._prev_htr_battery_on:
            if s.htr_battery:
                events.append({"event_id": 0x0404, "severity": "INFO", "subsystem": "tcs",
                              "description": "HEATER_ON: Battery heater activated"})
            else:
                events.append({"event_id": 0x0405, "severity": "INFO", "subsystem": "tcs",
                              "description": "HEATER_OFF: Battery heater deactivated"})
            s._prev_htr_battery_on = s.htr_battery

        # Heater failure events
        if s.htr_battery_stuck_on != s._prev_htr_battery_stuck_on:
            if s.htr_battery_stuck_on:
                events.append({"event_id": 0x0406, "severity": "HIGH", "subsystem": "tcs",
                              "description": "HEATER_STUCK_ON: Battery heater cannot turn off"})
            s._prev_htr_battery_stuck_on = s.htr_battery_stuck_on

        if s.htr_battery_failed != s._prev_htr_battery_failed:
            if s.htr_battery_failed:
                events.append({"event_id": 0x0407, "severity": "HIGH", "subsystem": "tcs",
                              "description": "HEATER_STUCK_OFF: Battery heater circuit failure"})
            s._prev_htr_battery_failed = s.htr_battery_failed

        # Mode change event
        decontam_now = s.decontamination_active
        prev_decontam = getattr(s, "_prev_decontam_active", False)
        if decontam_now != prev_decontam:
            events.append({"event_id": 0x0408, "severity": "INFO", "subsystem": "tcs",
                          "description": f"TCS_MODE_CHANGE: Decontamination {'START' if decontam_now else 'STOP'}"})
            s._prev_decontam_active = decontam_now

        # Thermal runaway detection (temperature rate > 2 deg/min).
        # Use a SMOOTHED rate over the temperature-history window rather than the
        # tick-to-tick delta: consecutive-sample deltas are dominated by the
        # per-tick gaussian noise on bat_temp, so abs(rate) chattered across the
        # 2 deg/min threshold every tick and re-fired the event continuously.
        # Combined with hysteresis (trigger at the limit, only clear well below),
        # this yields one event per genuine runaway onset.
        if len(s._temp_history) >= 3:
            oldest_temp = s._temp_history[0][1]
            span_min = max(len(s._temp_history) * dt / 60.0, 1e-6)
            rate_deg_per_min = (bat_temp - oldest_temp) / span_min
            high = self._thermal_runaway_rate          # 2.0 deg/min — trigger
            low = self._thermal_runaway_rate * 0.5      # 1.0 deg/min — clear
            if abs(rate_deg_per_min) > high:
                runaway_now = True
            elif abs(rate_deg_per_min) < low:
                runaway_now = False
            else:
                runaway_now = s._prev_thermal_runaway   # hold state inside deadband
            # Rising-edge: fire once when the rate first exceeds the limit.
            if runaway_now and not s._prev_thermal_runaway:
                events.append({"event_id": 0x0409, "severity": "HIGH", "subsystem": "tcs",
                              "description": f"THERMAL_RUNAWAY: Battery rate {rate_deg_per_min:.1f} deg/min"})
            s._prev_thermal_runaway = runaway_now

        # Track temperature for rate calculation
        s._temp_history.append((0, bat_temp))
        if len(s._temp_history) > 10:
            s._temp_history.pop(0)

        # FPA thermal readiness (G3-d).
        #
        # This used to be `warn_low <= T <= warn_high` off the event
        # thresholds, which forced those thresholds to be an imaging-readiness
        # band ([-50, -5]) rather than a health band — the root of the FPA's
        # five-way disagreement. Worse, it was absolute: after Phase E made
        # the setpoint commandable, a perfectly regulated node at a commanded
        # -2 C reported NOT READY while the payload's own flag reported READY.
        #
        # Readiness is now the same rule in both subsystems: the loop has
        # settled about the ACTIVE setpoint, AND the detector is cold enough
        # for the dark current to be tolerable.
        fpa_ready = self._fpa_env.is_ready(fpa_temp, self._fpa_cooler_target)
        if fpa_ready and not s._prev_fpa_ready:
            events.append({"event_id": 0x040A, "severity": "INFO", "subsystem": "tcs",
                          "description": f"FPA_THERMAL_READY: FPA {fpa_temp:.1f}C in operational range"})
            s._prev_fpa_ready = True
        elif not fpa_ready and s._prev_fpa_ready:
            events.append({"event_id": 0x040B, "severity": "MEDIUM", "subsystem": "tcs",
                          "description": f"FPA_THERMAL_NOT_READY: FPA {fpa_temp:.1f}C out of range"})
            s._prev_fpa_ready = False

        # Dispatch all events to the engine (defect #23)
        for event in events:
            try:
                self._engine._model_event_queue.put_nowait(event)
            except Exception:
                pass  # Queue full, skip event

    def tick(self, dt: float, orbit_state: Any, shared_params: dict[int, float]) -> None:
        s = self._state
        e = self._env
        if orbit_state.in_eclipse:
            env_ext = e["eclipse_external_c"]
            env_int = e["eclipse_internal_c"]
            env_bat = e["eclipse_battery_c"]
        else:
            beta = orbit_state.solar_beta_deg
            # Beta dependence un-inverted (brief 05 finding 8 / WP-8): the
            # orbit-average external sink RISES with |beta| (less eclipse,
            # more continuous sun). The old |cos(beta)| peaked at beta = 0 —
            # the maximum-eclipse geometry.
            sun_f = abs(math.sin(math.radians(beta)))
            env_ext = e["sunlit_external_base_c"] + e["sunlit_external_beta_gain_c"] * sun_f
            env_int = e["sunlit_internal_c"]
            env_bat = e["sunlit_battery_c"]

        # Apply thermal coupling between zones
        self._apply_thermal_coupling(dt)

        # --- Panel temperatures with solar illumination coupling ---
        # Read per-panel solar currents from EPS (proxy for illumination)
        panel_faces = ['px', 'mx', 'py', 'my', 'pz', 'mz']
        panel_solar_param_ids = [0x012B, 0x012C, 0x012D, 0x012E, 0x012F, 0x0130]
        panel_base_envs = [env_ext+5, env_ext, env_ext+8, env_ext, env_ext-5, env_ext-5]

        for face, pid, base_env in zip(panel_faces, panel_solar_param_ids, panel_base_envs):
            attr = f"temp_panel_{face}"
            key = f"panel_{face}"
            T = getattr(s, attr)
            solar_current = shared_params.get(pid, 0.0)
            # Solar heating proportional to current (fraction of solar power
            # that becomes heat). The equivalent environment rise for heat Q
            # into a first-order node is Q/UA with UA = C/tau — the previous
            # code multiplied by a flat 10 K/W ON TOP of that, ~80x too much:
            # +362 C panels at full wing current (brief 05 finding 1 / WP-8).
            # panel_solar_heat_gain is now only a trim multiplier (default 1).
            solar_heat_w = solar_current * 28.0 * e["panel_solar_heat_fraction"]
            ua_node = self._CAP.get(key, 1000.0) / max(self._TAU.get(key, 600.0), 1e-6)
            heat_input = solar_heat_w / max(ua_node, 1e-6) * e["panel_solar_heat_gain"]
            env = base_env + heat_input
            # Integrator process noise: sqrt(dt) scaling (plan A-3)
            T += (env - T) / self._TAU[key] * dt + self.rng.gauss(0, 0.05) * math.sqrt(dt)
            setattr(s, attr, T)

        # --- Battery temp: thermostat-controlled, gated on EPS power line ---
        # EPS power line 5 (htr_bat) must be ON for battery heater to operate
        bat_eps_on = bool(shared_params.get(0x0115, 0))
        if bat_eps_on:
            self._thermostat_control("battery", s.temp_battery)
        else:
            s.htr_battery = False  # No power → heater off
        # Apply heater stuck-on / open-circuit failure overrides
        if s.htr_battery_stuck_on:
            s.htr_battery = True  # Cannot turn off
        bat_pwr = self._HEATER_POWER["battery"] if s.htr_battery else 0.0
        if s.htr_battery_open_circuit:
            bat_pwr = 0.0  # Heater appears ON but provides no heat
        s.temp_battery += ((env_bat-s.temp_battery)/self._TAU["battery"]+bat_pwr/self._CAP["battery"])*dt+self.rng.gauss(0,0.02)*math.sqrt(dt)

        # --- OBC temp (defect #11) ---
        # By default the OBC heater follows its EPS power line (line 6, 0x0116).
        # If the operator has issued a direct heater command (S8 func 41, which
        # sets htr_obc_manual), honour that commanded state instead of blindly
        # overwriting it every tick — but the heater can only actually energise
        # if its EPS line has power. This mirrors the battery-circuit logic and
        # stops func 41 from reporting success while having no lasting effect.
        obc_eps_on = bool(shared_params.get(0x0116, 0))
        if getattr(s, "htr_obc_manual", False):
            s.htr_obc = bool(s.htr_obc) and obc_eps_on
        else:
            s.htr_obc = obc_eps_on
        pwr = self._HEATER_POWER["obc"] if s.htr_obc else 0.0
        if s.htr_obc_open_circuit:
            pwr = 0.0
        s.temp_obc += ((env_int-s.temp_obc)/self._TAU["obc"]+(pwr+s.obc_internal_heat_w)/self._CAP["obc"])*dt+self.rng.gauss(0,0.03)*math.sqrt(dt)

        # --- FPA temp (single thermal node — brief 05 defect 1 / WP-1) ---
        # The cooler is a CONTROLLED device driving the node to its setpoint,
        # bounded by a maximum heat lift below the ambient sink. The previous
        # code applied the -15 C setpoint as an additive -35 K environment
        # offset (`target - 20`), so TCS asymptoted to env-35, never to the
        # setpoint, and diverged from the payload's own integrator by up to
        # 26 C on the command path the shipped procedures use.
        # Effective cooler state = commanded (any path, s.cooler_fpa) AND the
        # EPS fpa_cooler line (0x0114) powered AND not failed (WP-2).
        fpa_line_on = (bool(shared_params.get(0x0114, 0))
                       if 0x0114 in shared_params else True)
        s.cooler_fpa_active = bool(s.cooler_fpa and fpa_line_on
                                   and not s.cooler_failed)
        if s.decontamination_active:
            # Defect #31: decontamination bake-out actively heats the FPA toward
            # its target temperature, overriding the cooler. Previously the
            # command set a flag the thermal model never read, so the FPA never
            # warmed and the procedure was untrainable.
            s.temp_fpa += (s.decontam_fpa_target_c - s.temp_fpa) / self._TAU["fpa"] * dt \
                + self.rng.gauss(0, 0.02) * math.sqrt(dt)
        else:
            if s.cooler_fpa_active:
                # 2026-08b: authority now falls with the lift the cooler is
                # being asked for, and with the detector's own dissipation
                # while it is reading out (payload.mode 0x0600 == 2 IMAGING).
                q_load = (self._fpa_imaging_load_w
                          if int(shared_params.get(0x0600, 0)) == 2 else 0.0)
                s.fpa_cooler_floor_c = env_int - self._fpa_achievable_lift_k(
                    env_int, q_load)
                fpa_target = max(self._fpa_cooler_target, s.fpa_cooler_floor_c)
            else:
                s.fpa_cooler_floor_c = env_int
                fpa_target = env_int
            s.fpa_cooler_margin_k = (self._fpa_cooler_target
                                     - s.fpa_cooler_floor_c)
            s.temp_fpa += (fpa_target-s.temp_fpa)/self._TAU["fpa"]*dt+self.rng.gauss(0,0.02)*math.sqrt(dt)

        # --- Thruster (catalyst-bed) temp — Phase D, real heater ---
        # Gated on the EPS `prop` power line (0x014A). Manual by default
        # (HEATER_THRUSTER func 42 commands it; the "fail loudly" gate in
        # handle_command refuses ON while the line is dark); auto mode runs
        # the 190/230 C thermostat hold. During a burn (prop.state 0x0B00 ==
        # FIRING) decomposition heat soaks the bed.
        prop_line_on = bool(shared_params.get(0x014A, 0))
        if not prop_line_on:
            s.htr_thruster = False   # no power -> heater off
        elif not s.htr_thruster_manual:
            self._thermostat_control("thruster", s.temp_thruster)
        if s.htr_thruster_stuck_on:
            s.htr_thruster = True
        thr_pwr = self._HEATER_POWER["thruster"] if s.htr_thruster else 0.0
        if s.htr_thruster_open_circuit:
            thr_pwr = 0.0
        q_comb_w = 30.0 if int(shared_params.get(0x0B00, 0)) == 2 else 0.0
        s.temp_thruster += ((env_int-5-s.temp_thruster)/self._TAU["thruster"]
                            + (thr_pwr + q_comb_w)/self._CAP["thruster"])*dt \
            + self.rng.gauss(0, 0.03)*math.sqrt(dt)

        # Defect #31: enforce heater duty-cycle limits. If the measured duty for
        # a circuit has reached its configured limit (<100%), force the heater
        # off this tick so the duty cannot climb further. Only active when an
        # operator has actually set a limit below 100%, so nominal behaviour is
        # unchanged.
        for circuit in ("battery", "obc", "thruster"):
            limit = getattr(s, f"htr_{circuit}_duty_limit_pct", 100.0)
            if limit < 100.0 and getattr(s, f"htr_duty_{circuit}", 0.0) >= limit:
                setattr(s, f"htr_{circuit}", False)

        # Phase 4: Heater duty cycle tracking
        decay = dt / s._duty_window_s
        for circuit in ("battery", "obc", "thruster"):
            on = getattr(s, f"htr_{circuit}")
            if on:
                s._htr_on_accum[circuit] += dt
            # Exponential decay to approximate sliding window
            s._htr_on_accum[circuit] *= (1.0 - decay)
            duty = min(100.0, s._htr_on_accum[circuit] / s._duty_window_s * 100.0)
            setattr(s, f"htr_duty_{circuit}", duty)

        # Total heater power
        s.htr_total_power_w = 0.0
        if s.htr_battery:
            s.htr_total_power_w += self._HEATER_POWER["battery"]
        if s.htr_obc:
            s.htr_total_power_w += self._HEATER_POWER["obc"]
        if s.htr_thruster:
            s.htr_total_power_w += self._HEATER_POWER["thruster"]

        # Write shared params — apply sensor drift offsets for failure simulation
        p = self._param_ids
        drift = s.sensor_drift

        shared_params[p.get("temp_panel_px",0x0400)] = s.temp_panel_px + drift.get("panel_px", 0.0)
        shared_params[p.get("temp_panel_mx",0x0401)] = s.temp_panel_mx + drift.get("panel_mx", 0.0)
        shared_params[p.get("temp_panel_py",0x0402)] = s.temp_panel_py + drift.get("panel_py", 0.0)
        shared_params[p.get("temp_panel_my",0x0403)] = s.temp_panel_my + drift.get("panel_my", 0.0)
        shared_params[p.get("temp_panel_pz",0x0404)] = s.temp_panel_pz + drift.get("panel_pz", 0.0)
        shared_params[p.get("temp_panel_mz",0x0405)] = s.temp_panel_mz + drift.get("panel_mz", 0.0)
        shared_params[p.get("temp_obc",0x0406)] = s.temp_obc + drift.get("obc", 0.0)
        shared_params[p.get("temp_battery",0x0407)] = s.temp_battery + drift.get("battery", 0.0)
        shared_params[p.get("temp_fpa",0x0408)] = s.temp_fpa + drift.get("fpa", 0.0)
        shared_params[p.get("temp_thruster",0x0409)] = s.temp_thruster + drift.get("thruster", 0.0)
        shared_params[p.get("htr_battery",0x040A)] = 1 if s.htr_battery else 0
        shared_params[p.get("htr_obc",0x040B)] = 1 if s.htr_obc else 0
        # 0x040C is the EFFECTIVE cooler state (commanded AND powered AND not
        # failed) — the flag EPS bills from and the displays show (WP-2).
        shared_params[p.get("cooler_fpa",0x040C)] = 1 if s.cooler_fpa_active else 0
        # Phase D: htr_thruster publishes at 0x041B (0x040D belongs to
        # ttc.pa_backup_temp — the Phase B collision is NOT reintroduced).
        # EPS bills the 12 W from this parameter via the prop line (0x0B1B).
        shared_params[p.get("htr_thruster", 0x041B)] = 1 if s.htr_thruster else 0
        # 2026-08b cooler authority. Published in the TCS-owned 0x04xx range;
        # the payload mirrors them at 0x064A/0x064B for its own HK structure.
        shared_params[0x0424] = s.fpa_cooler_floor_c
        shared_params[0x0425] = s.fpa_cooler_margin_k
        # Phase 4: Flight hardware params
        shared_params[0x040E] = s.htr_duty_battery
        shared_params[0x040F] = s.htr_duty_obc
        shared_params[0x0410] = s.htr_duty_thruster
        shared_params[0x0411] = s.htr_total_power_w
        # Panel temperatures (illumination-coupled, secondary param IDs)
        shared_params[0x0412] = s.temp_panel_px + drift.get("panel_px", 0.0)
        shared_params[0x0413] = s.temp_panel_mx + drift.get("panel_mx", 0.0)
        shared_params[0x0414] = s.temp_panel_py + drift.get("panel_py", 0.0)
        shared_params[0x0415] = s.temp_panel_my + drift.get("panel_my", 0.0)
        shared_params[0x0416] = s.temp_panel_pz + drift.get("panel_pz", 0.0)
        shared_params[0x0417] = s.temp_panel_mz + drift.get("panel_mz", 0.0)

        # New telemetry parameters (Phase 5: Event generation support)
        shared_params[0x0418] = 1.0 if s.decontamination_active else 0.0  # DECONTAMINATION_ACTIVE
        # Thermal gradient (max temp difference between zones)
        all_temps = [s.temp_panel_px, s.temp_panel_mx, s.temp_panel_py, s.temp_panel_my,
                     s.temp_panel_pz, s.temp_panel_mz, s.temp_obc, s.temp_battery, s.temp_fpa]
        thermal_gradient = max(all_temps) - min(all_temps)
        shared_params[0x0419] = thermal_gradient

        # Thermal regulation margin (0x041A) — the minimum distance of any
        # actively-regulated zone to its ALARM band. Declared by the MCS gauge
        # and the planner imaging pre-condition but previously written by
        # nothing (brief 05 finding 12 / WP-7). Negative = a zone is outside
        # its alarm band (e.g. FPA not yet cooled for imaging).
        zones = [("battery", s.temp_battery), ("obc", s.temp_obc)]
        if s.cooler_fpa_active or s.decontamination_active:
            # The FPA only counts while actively regulated (cooler running or
            # bake-out) — unpowered it legitimately floats to ambient.
            zones.append(("fpa", s.temp_fpa))
        margin = 999.0
        for zone, temp in zones:
            lo, hi = self._temp_alarm_limits.get(zone, (-999.0, 999.0))
            margin = min(margin, hi - temp, temp - lo)
        shared_params[0x041A] = margin

        # Radiator panel temperatures (0x0420/0x0421). Simple derived nodes:
        # the north/south radiator edges ride the -Z (space-facing) panel a
        # few kelvin colder. Declared in parameters.yaml since Phase 5 but
        # never written; full radiator physics is deferred (WP-8 out-of-scope
        # note) — these are honest first-order proxies, not stubs.
        shared_params[0x0420] = s.temp_panel_mz - 4.0
        shared_params[0x0421] = s.temp_panel_mz - 5.0

        # Setpoint readback (0x0330, 0x0331) — DEFECT 3 fix
        # Allow operators to verify heater setpoints after command via S20 parameter read
        shared_params[0x0330] = s.htr_battery_setpoint_on_c
        shared_params[0x0331] = s.htr_obc_setpoint_on_c

        # Generate events
        self._generate_events(dt)

    def get_telemetry(self) -> dict[int, float]:
        return {0x0408: self._state.temp_fpa, 0x0407: self._state.temp_battery}

    def handle_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        command = cmd.get("command", "")
        if command == "heater":
            circuit = cmd.get("circuit", "battery")
            on = bool(cmd.get("on", True))
            failed = getattr(self._state, f"htr_{circuit}_failed", False)
            stuck = getattr(self._state, f"htr_{circuit}_stuck_on", False)
            if stuck:
                return {"success": False, "message": "Heater stuck on, cannot control"}
            if failed and on:
                return {"success": False, "message": "Heater failed"}
            # Fail loudly when the command cannot take effect (brief 05 WP-4):
            # switching a heater ON whose EPS power line is dark used to
            # return success and be silently reverted on the next tick — the
            # trainee saw TC accepted while 0x040A stayed 0 forever.
            if on:
                eng = getattr(self, "_engine", None)
                line_param = {"battery": 0x0115, "obc": 0x0116,
                              "thruster": 0x014A}.get(circuit)
                if eng is not None and line_param is not None \
                        and not bool(getattr(eng, "params", {}).get(line_param, 0)):
                    line_no = {"battery": 5, "obc": 6, "thruster": 8}[circuit]
                    # 0x0004 = "Subsystem unpowered or unreachable"
                    return {"success": False, "error_code": 0x0004,
                            "message": (f"{circuit} heater EPS power line "
                                        f"{line_no} is OFF — command cannot "
                                        f"take effect (EPS_POWER_ON first)")}
            setattr(self._state, f"htr_{circuit}", on)
            # Manual override — disable thermostat auto control
            setattr(self._state, f"htr_{circuit}_manual", True)
            return {"success": True}
        elif command == "set_setpoint":
            circuit_idx = cmd.get("circuit", 0)
            circuits = ["battery", "obc", "thruster"]
            if isinstance(circuit_idx, int) and 0 <= circuit_idx < len(circuits):
                circuit = circuits[circuit_idx]
            elif isinstance(circuit_idx, str) and circuit_idx in circuits:
                circuit = circuit_idx
            else:
                # Unknown circuit index: reject honestly instead of a false
                # success that would pollute state with phantom htr_* fields.
                return {"success": False,
                        "message": f"Unknown heater circuit {circuit_idx!r}"}
            # Defect #31 (historic): a circuit without a heater can never apply a
            # setpoint — reject honestly instead of a false success.
            if circuit in self._NO_HEATER_CIRCUITS:
                return {"success": False,
                        "message": f"No heater on '{circuit}' circuit — setpoint not applicable"}
            on_temp = cmd.get("on_temp")
            off_temp = cmd.get("off_temp")
            if on_temp is not None and off_temp is not None:
                # Update both instance dict and state object
                self._THERMOSTAT[circuit] = (float(off_temp), float(on_temp))
                setattr(self._state, f"htr_{circuit}_setpoint_on_c", float(on_temp))
                setattr(self._state, f"htr_{circuit}_setpoint_off_c", float(off_temp))
                return {"success": True}
            return {"success": False, "message": "Missing on_temp or off_temp"}
        elif command == "auto_mode":
            circuit_idx = cmd.get("circuit", 0)
            circuits = ["battery", "obc", "thruster"]
            if isinstance(circuit_idx, int) and 0 <= circuit_idx < len(circuits):
                circuit = circuits[circuit_idx]
            elif isinstance(circuit_idx, str) and circuit_idx in circuits:
                circuit = circuit_idx
            else:
                return {"success": False,
                        "message": f"Unknown heater circuit {circuit_idx!r}"}
            # Defect #31 (historic). Battery (thermostat) and
            # OBC (manual-override flag, see #11) both honour auto-mode.
            if circuit in self._NO_HEATER_CIRCUITS:
                return {"success": False,
                        "message": f"No heater on '{circuit}' circuit — auto-mode not applicable"}
            setattr(self._state, f"htr_{circuit}_manual", False)
            return {"success": True}
        elif command == "fpa_cooler":
            on = bool(cmd.get("on", True))
            if self._state.cooler_failed and on:
                return {"success": False, "error_code": 0x0006,
                        "message": "FPA cooler has failed — cannot be "
                                   "commanded ON"}
            # ── 2026-08b PL-a: fail loudly when the EPS line is dark ──
            # This handler used to `return {"success": True}` unconditionally,
            # so an operator commanding the cooler on TCS func 43 — the ONLY
            # path COM-009, the mission's only cooler-activation procedure,
            # uses — got a clean S1.7 "execution complete" while 0x040C stayed
            # 0, the node stayed at +11.94 C and FPA READY stayed NO forever.
            # The heater circuit right next door has implemented this exact
            # "the command cannot take effect" refusal since brief 05 WP-4;
            # the cooler is now consistent with it. Service dispatch also
            # energises the line on every request path
            # (`ServiceDispatcher._request_fpa_cooler`), so on the nominal
            # ground path this refusal never fires — it catches the FDIR,
            # procedure and OBCP paths that reach the model directly.
            if on:
                eng = getattr(self, "_engine", None)
                if eng is not None and not bool(
                        getattr(eng, "params", {}).get(0x0114, 0)):
                    # 0x0004 = "Subsystem unpowered or unreachable"
                    return {"success": False, "error_code": 0x0004,
                            "message": "FPA cooler EPS power line 4 is OFF — "
                                       "command cannot take effect "
                                       "(EPS_FPA_COOLER / EPS_POWER_ON first)"}
            self._state.cooler_fpa = on
            return {"success": True}
        elif command == "set_fpa_setpoint":
            # Phase E (brief 05 deferred item): PAYLOAD_COOLER_SETPOINT
            # (S8 func 36) drives the TCS-OWNED FPA node end-to-end — TCS is
            # the single integrator since Phase B (WP-1); the payload's
            # fpa_temp is the detector-side lag onto this node. Same Peltier
            # envelope as the payload model so both sides agree on rejection.
            env = self._fpa_env
            setpoint = float(cmd.get("setpoint_c", env.setpoint_default_c))
            # Same wire-decode epsilon as the payload side: raw 27315 cK
            # decodes to +5.68e-14 degC, which is > 0.0 and was refused.
            setpoint = snap_to_envelope(setpoint, env.setpoint_min_c,
                                        env.setpoint_max_c)
            if not (env.setpoint_min_c <= setpoint <= env.setpoint_max_c):
                return {"success": False, "error_code": 0x0003,
                        "message": f"FPA setpoint {setpoint:.2f} C outside "
                                   f"the Peltier envelope "
                                   f"({env.setpoint_min_c:.0f} C to "
                                   f"{env.setpoint_max_c:.0f} C)"}
            self._fpa_cooler_target = setpoint
            return {"success": True}
        elif command == "set_heater_duty_limit":
            circuit_idx = cmd.get("circuit", 0)
            circuits = ["battery", "obc", "thruster"]
            if isinstance(circuit_idx, int) and 0 <= circuit_idx < len(circuits):
                circuit = circuits[circuit_idx]
            else:
                circuit = str(circuit_idx)
            duty_pct = cmd.get("duty_limit_pct", 100.0)
            duty_pct = max(0.0, min(100.0, float(duty_pct)))
            setattr(self._state, f"htr_{circuit}_duty_limit_pct", duty_pct)
            return {"success": True, "message": f"Heater {circuit} duty limit set to {duty_pct}%"}
        elif command == "decontamination_start":
            self._state.decontamination_active = True
            self._state.decontam_start_time = 0.0
            self._state.decontam_fpa_target_c = cmd.get("target_temp_c", 50.0)
            return {"success": True, "message": "Decontamination heating started"}
        elif command == "decontamination_stop":
            self._state.decontamination_active = False
            return {"success": True, "message": "Decontamination heating stopped"}
        elif command == "get_thermal_map":
            thermal_map = {
                "temp_panel_px": self._state.temp_panel_px,
                "temp_panel_mx": self._state.temp_panel_mx,
                "temp_panel_py": self._state.temp_panel_py,
                "temp_panel_my": self._state.temp_panel_my,
                "temp_panel_pz": self._state.temp_panel_pz,
                "temp_panel_mz": self._state.temp_panel_mz,
                "temp_obc": self._state.temp_obc,
                "temp_battery": self._state.temp_battery,
                "temp_fpa": self._state.temp_fpa,
                "temp_thruster": self._state.temp_thruster,
            }
            return {"success": True, "thermal_map": thermal_map}
        return {"success": False, "message": f"Unknown: {command}"}

    # ─── Failure injection ───────────────────────────────────────────
    # Declarative parameter specs — single source for the instructor form,
    # FailureManager validation and the scenario content gate.
    _HEATER_CIRCUITS = ["battery", "obc", "thruster"]
    _HEATER_LABELS = ["Battery heater", "OBC heater", "Thruster catbed heater"]
    # Thermal zones = the `temp_<zone>` fields the model publishes.
    _ZONES = ["battery", "obc", "fpa", "thruster",
              "panel_px", "panel_mx", "panel_py", "panel_my",
              "panel_pz", "panel_mz"]

    FAILURE_SPECS = {
        "heater_failure": FailureSpec(
            name="heater_failure", label="Heater circuit failure",
            effect="Selected heater cannot energise — its zone cools to the "
                   "passive equilibrium and the low-temperature limit trips.",
            magnitude=ParamSpec(name="magnitude", type=T_BOOL,
                                label="Failed", default=True),
            params=(enum("circuit", "Heater circuit", _HEATER_CIRCUITS,
                         labels=_HEATER_LABELS, default="battery"),),
        ),
        "heater_stuck_on": FailureSpec(
            name="heater_stuck_on", label="Heater stuck ON",
            effect="Selected heater cannot be commanded off — the zone "
                   "overheats and heater power stays on the EPS budget.",
            magnitude=None,
            params=(enum("circuit", "Heater circuit", _HEATER_CIRCUITS,
                         labels=_HEATER_LABELS, default="battery"),),
        ),
        "heater_open_circuit": FailureSpec(
            name="heater_open_circuit", label="Heater open circuit",
            effect="Heater commands are accepted and the status reads ON, but "
                   "no heat is delivered and no current is drawn — the "
                   "'commanded on but still cooling' teaching case.",
            magnitude=None,
            params=(enum("circuit", "Heater circuit", _HEATER_CIRCUITS,
                         labels=_HEATER_LABELS, default="battery"),),
        ),
        "cooler_failure": FailureSpec(
            name="cooler_failure", label="FPA cooler failure",
            effect="The Stirling-class FPA cooler stops: the focal plane warms "
                   "off its centi-kelvin setpoint and imaging quality degrades.",
            magnitude=ParamSpec(name="magnitude", type=T_BOOL,
                                label="Failed", default=True),
        ),
        "obc_thermal": FailureSpec(
            name="obc_thermal", label="OBC internal heat rise",
            effect="Extra dissipation inside the OBC box drives temp_obc "
                   "(0x0406) up toward its red limit.",
            magnitude=None,
            params=(number("heat_w", "Extra dissipation", unit="W", lo=0.0,
                           hi=200.0, default=30.0, step=0.5),),
        ),
        "sensor_drift": FailureSpec(
            name="sensor_drift", label="Temperature sensor drift",
            effect="Adds a fixed offset to ONE reported zone temperature while "
                   "the true temperature is unchanged — the classic "
                   "'is it the sensor or the hardware?' exercise.",
            magnitude=ParamSpec(
                name="magnitude", type=T_FLOAT, label="Sensor offset",
                unit="degC", min=-100.0, max=100.0, default=10.0, step=0.1),
            params=(enum("zone", "Thermal zone", _ZONES,
                         labels=[z.replace("_", " ").upper() for z in _ZONES],
                         default="battery"),),
        ),
        "temp_anomaly": FailureSpec(
            name="temp_anomaly", label="Zone over-temperature",
            effect="Pushes a zone over its red limit and holds it there, "
                   "driving the thermal-exceedance contingency.",
            magnitude=None,
            params=(
                enum("zone", "Thermal zone", _ZONES,
                     labels=[z.replace("_", " ").upper() for z in _ZONES],
                     default="battery"),
                number("offset_c", "Temperature rise", unit="degC", lo=-100.0,
                       hi=200.0, default=30.0, step=0.5),
            ),
        ),
    }

    def inject_failure(self, failure: str, magnitude: float = 1.0, **kw) -> None:
        if failure == "heater_failure":
            c = kw.get("circuit", "battery")
            setattr(self._state, f"htr_{c}_failed", bool(magnitude))
            if magnitude: setattr(self._state, f"htr_{c}", False)
        elif failure == "cooler_failure":
            self._state.cooler_failed = bool(magnitude)
            if magnitude: self._state.cooler_fpa = False
        elif failure == "obc_thermal":
            self._state.obc_internal_heat_w = float(kw.get("heat_w", 30.0))
        elif failure == "sensor_drift":
            zone = kw.get("zone", "battery")
            self._state.sensor_drift[zone] = magnitude
        elif failure == "heater_stuck_on":
            c = kw.get("circuit", "battery")
            setattr(self._state, f"htr_{c}_stuck_on", True)
            setattr(self._state, f"htr_{c}", True)
        elif failure == "heater_open_circuit":
            c = kw.get("circuit", "battery")
            setattr(self._state, f"htr_{c}_open_circuit", True)

        elif failure == "temp_anomaly":
            # Force a sensor over its red line — drives thermal_exceedance.md.
            zone = kw.get("zone", "battery")
            offset_c = float(kw.get("offset_c", magnitude * 30.0))
            attr = f"temp_{zone}"
            if hasattr(self._state, attr):
                setattr(self._state, attr, getattr(self._state, attr) + offset_c)
            # Persist via sensor_drift so subsequent ticks keep it elevated.
            self._state.sensor_drift[zone] = self._state.sensor_drift.get(zone, 0.0) + offset_c

    def clear_failure(self, failure: str, **kw) -> None:
        if failure == "heater_failure":
            c = kw.get("circuit", "battery")
            setattr(self._state, f"htr_{c}_failed", False)
        elif failure == "cooler_failure":
            self._state.cooler_failed = False
        elif failure == "obc_thermal":
            self._state.obc_internal_heat_w = 0.0
        elif failure == "sensor_drift":
            zone = kw.get("zone", "battery")
            self._state.sensor_drift.pop(zone, None)
        elif failure == "heater_stuck_on":
            c = kw.get("circuit", "battery")
            setattr(self._state, f"htr_{c}_stuck_on", False)
        elif failure == "heater_open_circuit":
            c = kw.get("circuit", "battery")
            setattr(self._state, f"htr_{c}_open_circuit", False)

        elif failure == "temp_anomaly":
            zone = kw.get("zone", "battery")
            self._state.sensor_drift.pop(zone, None)

    def get_state(self) -> dict[str, Any]:
        import dataclasses
        from smo_common.state import rng_state_to_json
        d = dataclasses.asdict(self._state)
        d["_rng_state"] = rng_state_to_json(self.rng.getstate())
        # The commanded FPA setpoint lives on the model, not on _state, so
        # `dataclasses.asdict` missed it and a breakpoint load silently reset
        # the cooler to the configured default (B-2 class, found in G3-d).
        d["_fpa_cooler_target"] = self._fpa_cooler_target
        return d

    def set_state(self, state: dict[str, Any]) -> None:
        # Container fields are deep-copied by restore_dataclass_state: the
        # plain setattr this used to do aliased the snapshot's own dicts and
        # lists into live model state (G2-f).
        from smo_common.state import restore_dataclass_state, restore_rng
        restore_rng(self.rng, state.get("_rng_state"))
        if "_fpa_cooler_target" in state:
            self._fpa_cooler_target = float(state["_fpa_cooler_target"])
        restore_dataclass_state(self._state, state,
                                skip=("_rng_state", "_fpa_cooler_target"))

    def get_battery_temp(self) -> float: return self._state.temp_battery

    # S2 Device Access — device-level on/off control (reserved for future multi-zone variants)
    # NOTE (DEFECT 5): Not used for EOSAT-1. All control via S8 functional commands.
    def set_device_state(self, device_id: int, on_off: bool) -> bool:
        """Set device on/off state. Returns True if successful.
        Reserved for future missions with zone-level heater control."""
        if device_id not in self._state.device_states:
            return False
        self._state.device_states[device_id] = on_off
        return True

    def get_device_state(self, device_id: int) -> bool:
        """Get device on/off state. Returns True if on, False if off or invalid.
        Reserved for future missions with zone-level heater control."""
        return self._state.device_states.get(device_id, False)

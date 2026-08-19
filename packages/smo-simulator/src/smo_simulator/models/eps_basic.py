"""SMO Simulator — Basic EPS Model.

Battery SoC/voltage model, solar array power coupled to eclipse state,
power budget management with switchable power lines.
Per-line current draw, overcurrent detection, overvoltage/undervoltage flags,
and load shedding sequence.

6 body-panel solar array model: body-mounted panels on all 6 faces
(+X, -X, +Y, -Y, +Z, -Z) with attitude-dependent power generation
via AOCS CSS sun vector.
"""
import math
import random
from dataclasses import dataclass, field
from typing import Any

from smo_common.models.subsystem import SubsystemModel
from smo_simulator.failure_spec import (
    FailureSpec, ParamSpec, T_FLOAT, T_BOOL, enum, number,
)

# Panel face normals in body frame (6U cubesat body-mounted panels)
PANEL_NORMALS = {
    'px': (1, 0, 0), 'mx': (-1, 0, 0),
    'py': (0, 1, 0), 'my': (0, -1, 0),
    'pz': (0, 0, 1), 'mz': (0, 0, -1),
}

# Body faces carrying solar cells — single source for the panel-loss injection
# parameter and for `sa_panel_degradation`'s keys.
SOLAR_PANEL_FACES = tuple(PANEL_NORMALS)

# Power line definitions
# (name, switchable, default_on, description)
# Convention: only the non-switchable infrastructure lines (OBC, TTC RX) are
# energised at power-on. Every switchable line defaults OFF — operators must
# explicitly command equipment on after separation, matching real LEOP flow.
# NOTE (Phase B, brief 01 F14 / WP-13): the per-line wattage column is GONE.
# It advertised ratings (ttc_tx 20 W, aocs_wheels 12 W) that contradicted the
# live values and was consumed by nothing — configuration (eps.yaml +
# simulation.yaml eps.line_power_w) is the only source of wattages.
POWER_LINE_DEFS = [
    ("obc", False, True, "OBC computer"),
    ("ttc_rx", False, True, "TTC receiver"),
    ("ttc_tx", True, False, "TTC transmitter"),
    ("payload", True, False, "Payload imager"),
    ("fpa_cooler", True, False, "FPA cooler"),
    ("htr_bat", True, False, "Battery heater"),
    ("htr_obc", True, False, "OBC heater"),
    ("aocs_wheels", True, False, "Reaction wheels"),
    # Phase D: propulsion line (catbed heater 12 W via the TCS thruster
    # circuit + 2 W valve drivers while armed/firing). Index 8: line status
    # publishes at 0x014A and current at 0x014B — NOT 0x0110+8/0x0118+8,
    # which would collide with line_current_0 / the 0x0120 block.
    ("prop", True, False, "Propulsion (catbed htr + valves)"),
]

POWER_LINE_NAMES = [d[0] for d in POWER_LINE_DEFS]

# Deferred-completion keys (verification-semantics ruling, 2026-08). Solar
# wing deployment is a burn-wire release plus a ~30 s hinge sweep; the array
# area, and therefore the whole point of the command, only changes when the
# timer expires. The S1.7 waits for these.
COMPLETION_WING_PY = "eps.wing_deploy.py"
COMPLETION_WING_MY = "eps.wing_deploy.my"
COMPLETION_WING_BOTH = "eps.wing_deploy.both"
POWER_LINE_SWITCHABLE = {d[0]: d[1] for d in POWER_LINE_DEFS}
POWER_LINE_DEFAULTS = {d[0]: d[2] for d in POWER_LINE_DEFS}

# Legacy contiguous param blocks cover the first 8 lines only; later lines
# carry explicit (status, current) param ids here.
_EXTRA_LINE_PARAM_IDS = {"prop": (0x014A, 0x014B)}

# Overcurrent thresholds (amps) per line — 150% of nominal at 28V
OC_THRESHOLDS = {
    "obc": 2.0, "ttc_rx": 0.3, "ttc_tx": 1.0, "payload": 2.5,
    "fpa_cooler": 1.0, "htr_bat": 0.5, "htr_obc": 0.3, "aocs_wheels": 0.8,
    "prop": 0.8,
}
OC_TRIP_TIME_S = 0.05  # 50ms trip time (simulated as immediate at 1Hz tick)

# Load shedding priority (first shed = lowest priority, index 0 = first off).
# prop is LAST (Phase D): shedding propulsion is the last resort, and losing
# the line mid-burn triggers the on-board burn abort (reason 4).
LOAD_SHED_ORDER = ["payload", "fpa_cooler", "ttc_tx", "aocs_wheels", "prop"]
LOAD_SHED_VOLTAGE = 26.5  # Bus voltage below which load shedding begins


@dataclass
class EPSState:
    bat_soc_pct: float = 75.0
    bat_voltage: float = 26.4
    bat_current: float = 0.0
    bat_temp: float = 15.0
    sa_a_current: float = 4.5
    sa_b_current: float = 4.5
    sa_a_voltage: float = 28.2
    sa_b_voltage: float = 28.2
    sa_a_enabled: bool = True
    sa_b_enabled: bool = True
    bus_voltage: float = 28.2
    power_gen_w: float = 252.0
    power_cons_w: float = 120.0
    in_eclipse: bool = False
    #: Illuminated fraction of the solar disc, copied verbatim from the orbit
    #: propagator (the single eclipse authority). Every generation path is
    #: multiplied by it; 0.0 in umbra.
    sun_fraction: float = 1.0
    payload_mode: int = 0
    fpa_cooler_on: bool = False
    transponder_tx: bool = True
    sa_a_degradation: float = 1.0
    sa_b_degradation: float = 1.0
    bat_cell_failure: bool = False
    bus_short: bool = False
    # Power lines
    power_lines: dict = field(default_factory=lambda: dict(POWER_LINE_DEFAULTS))
    # Per-line currents (indexed by line name)
    line_currents: dict = field(default_factory=lambda: {n: 0.0 for n in POWER_LINE_NAMES})
    # Overcurrent
    oc_trip_flags: int = 0  # bitmask: bit i = line i tripped
    uv_flag: bool = False
    ov_flag: bool = False
    # Overcurrent injection multiplier per line
    oc_inject: dict = field(default_factory=dict)
    # ── Separation / LEOP ──
    sep_timer_active: bool = False
    sep_timer_remaining: float = 0.0
    pdm_unsw_status: int = 0x03  # bit 0 = RX, bit 1 = OBC (both on by default)
    # ── Per-panel solar currents (6 body-mounted faces; ±Y include wings) ──
    sa_panel_currents: dict = field(default_factory=lambda: {
        'px': 0.0, 'mx': 0.0, 'py': 0.0, 'my': 0.0, 'pz': 0.0, 'mz': 0.0
    })
    # Wing-only currents (brief 01 WP-11: per-wing telemetry, separate from
    # the body faces). Also included in sa_panel_currents['py'/'my'].
    sa_wing_currents: dict = field(default_factory=lambda: {'py': 0.0, 'my': 0.0})
    sa_panel_degradation: dict = field(default_factory=lambda: {
        'px': 1.0, 'mx': 1.0, 'py': 1.0, 'my': 1.0, 'pz': 1.0, 'mz': 1.0
    })
    # ── Deployable solar wings (±Y faces) ──
    wing_py_deployed: bool = False     # +Y wing deployed
    wing_my_deployed: bool = False     # -Y wing deployed
    wing_py_deploying: bool = False    # +Y deployment in progress
    wing_my_deploying: bool = False    # -Y deployment in progress
    wing_deploy_timer: float = 0.0    # Deployment timer (seconds remaining)
    wing_deploy_target: str = ""      # Which wing is deploying ("py" or "my")
    # ── Phase 4: Flight hardware realism ──
    bat_dod_pct: float = 25.0       # Depth of discharge (100 - SoC)
    bat_cycles: int = 0              # Charge/discharge cycle count
    bat_max_dod_pct: float = 80.0    # Maximum allowed DoD
    mppt_efficiency: float = 0.97    # MPPT tracker efficiency
    sa_lifetime_hours: float = 0.0   # Cumulative sunlit hours
    sa_age_factor: float = 1.0       # Degradation factor from aging (1.0=new)
    _was_charging: bool = False      # For cycle count tracking
    # ── Event tracking (previous state for edge detection) ──
    _prev_mode: int = 0              # Previous spacecraft mode
    _prev_soc: float = 75.0          # Previous SoC
    _prev_bus_voltage: float = 28.2  # Previous bus voltage
    _prev_in_eclipse: bool = False   # Previous eclipse state
    _prev_oc_trip_flags: int = 0     # Previous OC trip flags
    _prev_power_line_mask: int = -1  # Previous switchable power-line ON bitmask (-1 = uninitialised)
    _prev_load_shed_stage: int = 0   # Previous load shedding stage
    _was_charging_prev: bool = False # For charge complete detection
    _prev_bat_overtemp: bool = False # Battery overtemp edge state
    _prev_bat_undertemp: bool = False # Battery undertemp edge state
    _prev_sa_degraded: bool = False  # Solar-array-degraded edge state
    # ── Load shedding and control ──
    load_shed_stage: int = 0         # 0=none, 1/2/3=progressive shed
    # Stage latched by EPS_EMERGENCY_LOAD_SHED (or EPS_SET_MODE safing) —
    # 2026-08c item 8. The tick used to recompute load_shed_stage purely
    # from bus voltage, so a COMMANDED shed was overwritten to 0 on the
    # very next tick (0x0134 never moved, no stage event ever fired) and
    # the command looked like a no-op from the console. Dataclass field so
    # a breakpoint restore cannot heal the latch.
    commanded_shed_stage: int = 0
    # Lines shed by the bus-voltage shedder, in shed order — consumed by the
    # hysteresis auto-restore (brief 01 WP-7). Operator/safe-mode switch-offs
    # are NOT recorded here and are never auto-restored.
    shed_lines: list = field(default_factory=list)
    charge_rate_override_a: float = 0.0  # 0.0 = auto, else manual A
    actual_charge_current_a: float = 0.0  # Actual charge current applied (Defect #4)
    solar_array_drive_angle: float = 0.0 # Solar array orientation angle
    battery_heater_on: bool = True   # Battery heater enable
    battery_heater_setpoint_c: float = 10.0  # Setpoint temperature
    eps_mode: int = 0                # 0=nominal, 1=safe, 2=emergency
    # S2 Device Access — device on/off states (device_id -> on/off)
    device_states: dict = field(default_factory=lambda: {
        0x0100: True,   # Battery heater
        0x0101: True,   # Solar array drive
        0x0102: True,   # PDU switch bus 1
        0x0103: True,   # PDU switch bus 2
        0x0104: True,   # PDU switch bus 3
        0x010F: True,   # Battery charge regulator
    })


class EPSBasicModel(SubsystemModel):
    """Basic Electrical Power Subsystem simulation with power lines."""

    # Mean projected-area fraction of a convex body over random attitudes
    # (Cauchy's theorem: <A_proj> = A_total / 4). Used when the AOCS sun
    # vector is unavailable — i.e. the spacecraft is uncontrolled/tumbling —
    # instead of the old fallback that assumed a fully sun-facing array pair
    # and over-generated by roughly 3x (roadmap N-9).
    TUMBLING_PROJECTION_FACTOR = 0.25

    #: Sunlit fraction of a ~450 km SSO orbit, used ONLY to convert
    #: `array_losses.annual_degradation` (per calendar year) into the per
    #: SUNLIT-hour rate the live `sa_age_factor` integrates (2026-08b).
    SUNLIT_FRACTION = 0.65
    #: Fallback annual array degradation when `array_losses` is absent —
    #: 2.75 %/yr, the TJ GaAs LEO figure this model has always documented.
    ANNUAL_DEGRADATION_DEFAULT = 0.0275
    #: Reporting-only split of the non-switchable `obc` line (eps.yaml
    #: obc_power_w = OBC 3 W + PDU/EPS housekeeping 3 W). Used ONLY to
    #: attribute the per-subsystem breakdown 0x013C (EPS) vs 0x0141 (OBDH);
    #: billing is unchanged. A model constant rather than a config key because
    #: the smo-common EPSConfig schema forbids extra keys in eps.yaml.
    EPS_HOUSEKEEPING_SHARE_W = 3.0

    def __init__(self):
        self._state = EPSState()
        # Per-model RNG (plan A-2). Standalone construction gets a fresh
        # unseeded stream so existing unit tests keep working; the engine
        # replaces this with a Random derived from simulation.yaml `seed`.
        import random as _random
        self.rng = _random.Random(20260731)  # deterministic standalone fallback; engine overrides via derive_rng
        self._cfg: dict[str, Any] = {}
        # Deferred-completion reports drained by the engine (2026-08 ruling).
        self._completions: list[dict] = []
        # Defaults from original config
        self._battery_capacity_wh = 120.0
        self._payload_power_w = 45.0
        self._payload_standby_w = 8.0
        self._fpa_cooler_w = 15.0
        self._transponder_w = 20.0
        self._transponder_hi_w = 20.0
        self._transponder_rx_w = 5.0
        self._sa_degradation_per_hour = (
            self.ANNUAL_DEGRADATION_DEFAULT / (8760.0 * self.SUNLIT_FRACTION))
        self._soc_100_v = 29.2
        self._soc_0_v = 21.5
        self._internal_r = 0.05
        self._charge_eff = 1.0
        self._discharge_eff = 1.0
        self._cv_taper_start_pct: float | None = None
        self._cell_eff = 0.295
        self._solar_irrad = 1361.0
        # Array loss stack (brief 01 WP-1). None => legacy BOL model.
        self._array_losses: dict[str, float] | None = None
        # Solar array drive (brief 01 WP-2)
        self._sada_present = False
        self._sada_tracking = False
        self._sada_tracking_nominal = False
        self._sada_stuck = False
        self._sada_axis = 'z'
        self._sada_max_rate = 1.0
        self._sada_target_angle = 0.0
        # Bill heaters from the TCS thermostat state (brief 01 WP-5)
        self._heater_duty_from_tcs = False
        self._shed_auto_restore = False
        self._bat_temp_tau = 600.0
        self._bat_temp_env = 15.0
        # UV/OV thresholds
        self._uv_threshold = 26.5
        self._ov_threshold = 29.5
        # Pre-injection OV threshold, so clearing an `overvoltage` injection
        # restores the CONFIGURED value rather than the module default.
        self._ov_threshold_nominal: float | None = None
        # Protection / event tuning — module-level defaults, overridden by the
        # simulation.yaml `eps:` block in configure() (defect #51).
        self._line_power_w = {"htr_bat": 6.0, "htr_obc": 4.0, "aocs_wheels": 6.0}
        self._shed_order = list(LOAD_SHED_ORDER)
        self._shed_voltage = LOAD_SHED_VOLTAGE
        self._shed_hysteresis_v = 0.5
        self._oc_thresholds = dict(OC_THRESHOLDS)
        self._oc_trip_time_s = OC_TRIP_TIME_S
        self._evt = {
            "soc_low_pct": 20.0, "soc_critical_pct": 10.0,
            "soc_charge_complete_pct": 99.0,
            "bus_warning_v": 27.0, "bus_critical_v": 25.0,
            "bat_overtemp_c": 45.0, "bat_undertemp_c": -5.0,
            "sa_degraded_fraction": 0.7,
        }
        # Parameter IDs
        self._param_ids: dict[str, int] = {}

    @property
    def name(self) -> str:
        return "eps"

    def configure(self, config: dict[str, Any]) -> None:
        self._cfg = config
        bat = config.get("battery", {})
        self._battery_capacity_wh = bat.get("capacity_wh", 120.0)
        self._soc_100_v = bat.get("soc_100_v", 29.2)
        self._soc_0_v = bat.get("soc_0_v", 21.5)
        self._internal_r = bat.get("internal_r_ohm", 0.05)
        self._charge_eff = float(bat.get("charge_efficiency", 1.0))
        self._discharge_eff = float(bat.get("discharge_efficiency", 1.0))
        cv = bat.get("cv_taper_start_soc_pct")
        self._cv_taper_start_pct = float(cv) if cv is not None else None
        self._payload_power_w = config.get("payload_power_w", 45.0)
        self._payload_standby_w = config.get("payload_standby_power_w", 8.0)
        self._fpa_cooler_w = config.get("fpa_cooler_power_w", 15.0)
        self._transponder_w = config.get("transponder_power_w", 20.0)
        self._transponder_hi_w = config.get(
            "transponder_hi_power_w", self._transponder_w)
        self._transponder_rx_w = config.get("transponder_rx_power_w", 5.0)
        # Cell efficiency: cell_efficiency key, with the legacy arrays[0]
        # efficiency as a back-compat fallback for older mission configs.
        arrays = config.get("arrays", [])
        self._cell_eff = config.get(
            "cell_efficiency",
            arrays[0].get("efficiency", 0.295) if arrays else 0.295)
        # Array performance loss stack (brief 01 WP-1). Absent/empty => legacy
        # BOL behaviour (loss factor exactly 1.0, K = 389.5 W/m2).
        al = config.get("array_losses") or None
        self._array_losses = dict(al) if al else None
        # Live aging rate, DERIVED from array_losses.annual_degradation so the
        # config value and the model can no longer disagree (2026-08b; see the
        # aging block in tick()). Linear in cumulative SUNLIT hours, hence the
        # sunlit-fraction divisor.
        self._sa_degradation_per_hour = (
            float((self._array_losses or {}).get("annual_degradation",
                                                 self.ANNUAL_DEGRADATION_DEFAULT))
            / (8760.0 * self.SUNLIT_FRACTION))
        self._param_ids = config.get("param_ids", {
            "bat_voltage": 0x0100, "bat_soc": 0x0101, "bat_temp": 0x0102,
            "sa_a_current": 0x0103, "sa_b_current": 0x0104,
            "bus_voltage": 0x0105, "power_cons": 0x0106, "power_gen": 0x0107,
            "eclipse_flag": 0x0108, "bat_current": 0x0109,
            "bat_capacity_wh": 0x010A,
        })
        # OBC power (configurable, default matches POWER_LINE_DEFS)
        self._obc_power_w = config.get("obc_power_w", 15.0)
        # Per-panel solar areas (6U cubesat body-mounted panels)
        panel_cfg = config.get("solar_panels", {})
        self._panel_areas_body = {
            'px': panel_cfg.get('px_area_m2', 0.06),
            'mx': panel_cfg.get('mx_area_m2', 0.06),
            'py': panel_cfg.get('py_area_m2', 0.03),
            'my': panel_cfg.get('my_area_m2', 0.03),
            'pz': panel_cfg.get('pz_area_m2', 0.06),
            'mz': panel_cfg.get('mz_area_m2', 0.06),
        }
        # Deployable wing areas (added to ±Y body areas when deployed)
        self._wing_py_area = panel_cfg.get('wing_py_area_m2', 0.24)
        self._wing_my_area = panel_cfg.get('wing_my_area_m2', 0.24)
        self._wing_deploy_time_s = panel_cfg.get('wing_deploy_time_s', 30.0)
        # Single-axis solar array drive (brief 01 WP-2). When present and
        # tracking, the wing pair follows the sun's projection perpendicular
        # to the drive axis; when present but not tracking, the wings are
        # frozen at solar_array_drive_angle about the drive axis.
        self._sada_present = bool(panel_cfg.get('sada_present', False))
        self._sada_tracking = bool(panel_cfg.get('sada_tracking', False))
        self._sada_axis = str(panel_cfg.get('sada_axis', 'z')).lower()
        # Maximum shaft rate (2026-08b: previously dead config). Applies to
        # both sun-tracking and a commanded repositioning.
        self._sada_max_rate = float(panel_cfg.get('sada_max_rate_deg_s', 1.0))
        # `sada_tracking` as CONFIGURED, so `clear_failure('sada_stuck')`
        # restores the designed behaviour rather than guessing True.
        self._sada_tracking_nominal = self._sada_tracking
        self._sada_stuck = False
        self._sada_target_angle = self._state.solar_array_drive_angle
        # Effective panel areas (updated when wings deploy)
        self._panel_areas = dict(self._panel_areas_body)
        # Load power line config overrides if present
        pl_cfg = config.get("power_lines", {})
        for line_name in POWER_LINE_DEFAULTS:
            if line_name in pl_cfg:
                self._state.power_lines[line_name] = pl_cfg[line_name]

        # ── simulation.yaml `eps:` tuning block (defect #51) ──────────────
        # Every value falls back to the module-level constant that used to be
        # hard-coded here, so a config dir without simulation.yaml is unchanged.
        sim = config.get("simulation", {}) or {}

        initial = sim.get("initial", {}) or {}
        soc0 = initial.get("soc_pct")
        if soc0 is not None:
            self._state.bat_soc_pct = float(soc0)
            self._state._prev_soc = float(soc0)
            self._state.bat_dod_pct = 100.0 - float(soc0)
        temp0 = initial.get("bat_temp_c")
        if temp0 is not None:
            self._state.bat_temp = float(temp0)
            self._bat_temp_env = float(temp0)

        # Per-line wattages billed directly by tick() (lines whose rating has no
        # home in eps.yaml). Others come from eps.yaml hardware ratings.
        self._line_power_w = {"htr_bat": 6.0, "htr_obc": 4.0, "aocs_wheels": 6.0}
        self._line_power_w.update(
            {k: float(v) for k, v in (sim.get("line_power_w", {}) or {}).items()})

        self._uv_threshold = float(sim.get("uv_threshold_v", self._uv_threshold))
        self._ov_threshold = float(sim.get("ov_threshold_v", self._ov_threshold))

        shed = sim.get("load_shed", {}) or {}
        self._shed_order = list(shed.get("order") or LOAD_SHED_ORDER)
        self._shed_voltage = float(shed.get("bus_voltage_v", LOAD_SHED_VOLTAGE))
        self._shed_hysteresis_v = float(shed.get("restore_hysteresis_v", 0.5))
        self._shed_auto_restore = bool(shed.get("auto_restore", False))

        self._heater_duty_from_tcs = bool(sim.get("heater_duty_from_tcs", False))

        oc = sim.get("overcurrent", {}) or {}
        self._oc_thresholds = dict(OC_THRESHOLDS)
        self._oc_thresholds.update(
            {k: float(v) for k, v in (oc.get("thresholds_a", {}) or {}).items()})
        self._oc_trip_time_s = float(oc.get("trip_time_s", OC_TRIP_TIME_S))

        evt = sim.get("event_thresholds", {}) or {}
        self._evt = {
            "soc_low_pct": 20.0, "soc_critical_pct": 10.0,
            "soc_charge_complete_pct": 99.0,
            "bus_warning_v": 27.0, "bus_critical_v": 25.0,
            "bat_overtemp_c": 45.0, "bat_undertemp_c": -5.0,
            "sa_degraded_fraction": 0.7,
        }
        self._evt.update({k: float(v) for k, v in evt.items() if v is not None})

    # ── Solar array drive geometry (2026-08b) ─────────────────────────────
    #
    # These three helpers make `sada_max_rate_deg_s` and the "SADA stuck"
    # failure real. Before, the drive angle was assigned the tracked bearing
    # instantly and the illumination was computed from the bearing rather than
    # from the angle, so: the rate config was read by nothing, nothing at
    # runtime could set `_sada_tracking = False`, the failure the comments
    # described could not be exercised, and `set_solar_array_drive` was inert
    # whenever tracking was on.

    def _wing_normal(self, theta: float) -> tuple[float, float, float]:
        """Wing cell normal for a shaft angle `theta` (rad) about the drive
        axis. Chosen so that at the tracked bearing the dot product with the
        sun equals sqrt(1 - (s.axis)^2) — i.e. the cos-beta law is recovered
        exactly at steady state and the existing acceptance tests hold."""
        if self._sada_axis == 'y':
            return (math.cos(theta), 0.0, math.sin(theta))
        if self._sada_axis == 'x':
            return (0.0, math.cos(theta), math.sin(theta))
        return (-math.sin(theta), math.cos(theta), 0.0)     # 'z'

    def _tracked_bearing_deg(self, sx: float, sy: float, sz: float) -> float:
        """Shaft angle that points the wing normal at the sun's projection."""
        if self._sada_axis == 'y':
            return math.degrees(math.atan2(sz, sx))
        if self._sada_axis == 'x':
            return math.degrees(math.atan2(sz, sy))
        return math.degrees(math.atan2(-sx, sy))            # 'z'

    def _slew_drive(self, dt: float) -> None:
        """Move the shaft toward `_sada_target_angle` by at most
        `sada_max_rate_deg_s` * dt, taking the shorter way round. A STUCK
        drive does not move at all — which is the whole point of the failure:
        0x0133 freezes while the sun walks away from it."""
        if self._sada_stuck:
            return
        s = self._state
        err = (self._sada_target_angle - s.solar_array_drive_angle + 180.0) \
            % 360.0 - 180.0
        step = self._sada_max_rate * max(dt, 0.0)
        if abs(err) <= step:
            s.solar_array_drive_angle = self._sada_target_angle
        else:
            s.solar_array_drive_angle += math.copysign(step, err)
        # Keep the reported angle in (-180, 180] for the h/100 TM encoding.
        s.solar_array_drive_angle = (
            (s.solar_array_drive_angle + 180.0) % 360.0) - 180.0

    def tick(self, dt: float, orbit_state: Any, shared_params: dict[int, float]) -> None:
        s = self._state
        events_to_generate = []  # Accumulate events
        # ── Illumination comes from THE authority, never from EPS ──────────
        # `sun_fraction` (0.0 umbra … 1.0 full sun) is computed once per step
        # by the orbit propagator from smo_common.orbit.eclipse.shadow_state
        # and published on the OrbitState. EPS multiplies every generation
        # path by it, so "generation in eclipse" is impossible BY
        # CONSTRUCTION rather than by a branch that a future edit can dodge.
        # A reduced harness whose orbit stub predates the field falls back to
        # the boolean the same authority publishes.
        s.in_eclipse = bool(orbit_state.in_eclipse)
        illum = getattr(orbit_state, "sun_fraction", None)
        try:
            illum = float(illum)
        except (TypeError, ValueError):
            illum = None
        if illum is None or not (0.0 <= illum <= 1.0):
            illum = 0.0 if s.in_eclipse else 1.0
        if s.in_eclipse:
            # The authority's two spellings cannot disagree: umbra is zero sun.
            illum = 0.0
        s.sun_fraction = illum
        beta_deg = orbit_state.solar_beta_deg

        # ── Event: Eclipse entry/exit ──
        if s.in_eclipse and not s._prev_in_eclipse:
            events_to_generate.append((0x010C, f"Eclipse entry: SoC={s.bat_soc_pct:.1f}%"))
        elif not s.in_eclipse and s._prev_in_eclipse:
            events_to_generate.append((0x010D, f"Eclipse exit: resuming solar generation"))

        # ── Wing deployment tick ──
        if s.wing_py_deploying or s.wing_my_deploying:
            s.wing_deploy_timer -= dt
            if s.wing_deploy_timer <= 0:
                s.wing_deploy_timer = 0.0
                if s.wing_py_deploying:
                    s.wing_py_deployed = True
                    s.wing_py_deploying = False
                    self._panel_areas['py'] = self._panel_areas_body['py'] + self._wing_py_area
                    # 0x0112 WING_PY_DEPLOYED. This used to raise 0x010E, which
                    # is CHARGE_COMPLETE — so a wing deployment logged "battery
                    # reached 100% SoC" and 0x0112 appeared nowhere in the tree.
                    events_to_generate.append((0x0112, f"+Y solar wing deployed: area now {self._panel_areas['py']:.3f} m2"))
                    self._complete(COMPLETION_WING_PY, True)
                if s.wing_my_deploying:
                    s.wing_my_deployed = True
                    s.wing_my_deploying = False
                    self._panel_areas['my'] = self._panel_areas_body['my'] + self._wing_my_area
                    # 0x0113 WING_MY_DEPLOYED (was 0x010F PDU_OVERCURRENT).
                    events_to_generate.append((0x0113, f"-Y solar wing deployed: area now {self._panel_areas['my']:.3f} m2"))
                    self._complete(COMPLETION_WING_MY, True)
                # The "both" telecommand is one telecommand: its S1.7 lands
                # when the shared burn-wire timer expires, which is the same
                # tick both panels latch deployed.
                self._complete(COMPLETION_WING_BOTH, True)
        # Keep panel areas in sync with deployment state (e.g. after state restore)
        if s.wing_py_deployed and not s.wing_py_deploying:
            self._panel_areas['py'] = self._panel_areas_body['py'] + self._wing_py_area
        if s.wing_my_deployed and not s.wing_my_deploying:
            self._panel_areas['my'] = self._panel_areas_body['my'] + self._wing_my_area

        # ── Solar array aging (2026-08b: config and code reconciled) ──
        # Three numbers claimed to be the same one and none of them were:
        #   * this line aged at a hard-coded 3.14e-6 per sunlit hour, which at
        #     the SUNLIT_FRACTION below is 1.79 %/yr, not the 2.75 %/yr its own
        #     comment claimed (the comment's arithmetic used 0.6, but 3.14e-6
        #     x 8760 x 0.6 = 1.65 %/yr — it did not work out either);
        #   * eps.yaml `annual_degradation` said 0.00275 = 0.275 %/yr, a factor
        #     of ten below the rate its neighbouring comment documented; and
        #   * eps.yaml's K comment and the generation tests both assume
        #     sa_age_factor = 0.92 at 3-year EOL, i.e. 2.75 %/yr linear.
        # The 0.92-at-3-years intent wins (it is the one the loss stack, the
        # manual and tools/power_budget.py are all built on). The per-hour rate
        # is now DERIVED from `annual_degradation`, so the two cannot drift
        # apart again: 0.0275 / (8760 x 0.65) = 4.83e-6 per sunlit hour, and
        # 3 years x 0.0275 = 0.0825 => sa_age_factor 0.9175 at EOL.
        # Aging accrues with ILLUMINATION, not with a boolean: a penumbra pass
        # is not a full sunlit hour. Same authority, same number.
        if illum > 0.0:
            s.sa_lifetime_hours += illum * dt / 3600.0
        s.sa_age_factor = max(
            0.5, 1.0 - s.sa_lifetime_hours * self._sa_degradation_per_hour)

        # Sun vector in the body frame. Phase C: prefer the TRUE sun-body
        # vector the AOCS publishes at 0x02B3-B5 (a function of the true
        # attitude quaternion) — illumination is physics, not a sensor
        # reading, so a failed CSS must not black out the arrays. Fall back
        # to the CSS composite (0x0245-47) for reduced harnesses.
        sun_bx = shared_params.get(0x02B3)
        sun_by = shared_params.get(0x02B4)
        sun_bz = shared_params.get(0x02B5)
        if sun_bx is None or (sun_bx == 0.0 and sun_by == 0.0 and sun_bz == 0.0):
            sun_bx = shared_params.get(0x0245, 0.0)  # CSS sun vector X (body)
            sun_by = shared_params.get(0x0246, 0.0)  # CSS sun vector Y
            sun_bz = shared_params.get(0x0247, 0.0)  # CSS sun vector Z
        sun_mag = (sun_bx**2 + sun_by**2 + sun_bz**2) ** 0.5

        # ── Effective plane-of-array performance constant (brief 01 WP-1) ──
        # W per m2 of projected area: irradiance x cell efficiency x the
        # honest loss stack (inherent degradation/packing, array temperature,
        # harness) x MPPT x live aging. With `array_losses` absent the loss
        # factor is exactly 1.0 (legacy BOL behaviour, K = 389.5 W/m2); with
        # the recommended stack K = 292 W/m2 BOL, 267.9 W/m2 at 3-year EOL.
        loss_f = 1.0
        if self._array_losses:
            al = self._array_losses
            temp_f = 1.0 + al.get("temp_coeff_per_c", -0.0020) * (
                al.get("array_temp_sunlit_c", 65.0) - al.get("temp_ref_c", 28.0))
            loss_f = (al.get("inherent_degradation", 0.85) * temp_f
                      * al.get("harness_efficiency", 0.95))
        #
        # ILLUMINATION IS A FACTOR OF K, NOT A BRANCH (single-authority
        # refactor): `illum` is the propagator's sun_fraction — 0.0 in umbra,
        # 1.0 in full sun, the penumbra value in between. Because every
        # generation path below (6-panel, tumbling fallback, wings) is
        # `area x k_eff x <geometry>`, generation in eclipse is arithmetically
        # zero and cannot be reintroduced by editing any one branch.
        k_eff = (self._cell_eff * self._solar_irrad * loss_f
                 * s.mppt_efficiency * s.sa_age_factor * illum)

        # Expected (undegraded) generation, accumulated alongside the actual
        # generation with the SAME model + geometry. Reference for the
        # "solar array degraded" event (brief 01 F11 / WP-10) — previously it
        # used the dead legacy `arrays:` area and fired on every run.
        expected_w = 0.0
        s.sa_wing_currents['py'] = 0.0
        s.sa_wing_currents['my'] = 0.0
        # Currents are referenced to the live bus voltage (brief 01 F18/C10).
        # Hoisted out of the branches below so the post-branch device gates can
        # convert currents back to watts in the eclipse case too (2026-08b).
        bus_v_gen = max(s.bus_voltage, 20.0)

        # NOTE (single-authority refactor): there is deliberately NO
        # `if s.in_eclipse: gen_w = 0` branch here any more. That branch was a
        # check bolted on beside the physics — it zeroed the 6-panel path
        # while leaving every other generation path (tumbling fallback, SADA
        # wings, and anything added later) free to produce watts from a stale
        # sun vector. Illumination is now a multiplicative factor inside
        # k_eff, so all three paths below yield exactly 0 W in umbra.
        if sun_mag < 0.01:
            # No valid AOCS sun vector — which is the case for the whole of
            # phases 0-3, i.e. all of bootloader and early LEOP.
            #
            # Roadmap N-9: the legacy fallback here used `self._panel_area`
            # (0.628 m2, the sum of a two-array model that no longer exists)
            # and produced ~245 W sunlit, against ~82 W for the deployed
            # 6-panel model and ~22 W for the real body-only configuration.
            # LEOP was therefore survivable only because of a bug, and every
            # LEOP power exercise was physically meaningless.
            #
            # Replacement: a cosine-of-beta model over the *actual* configured
            # panel areas. An uncontrolled spacecraft is not sun-pointing, so
            # only the projected area of a tumbling body is illuminated; the
            # geometric mean over random attitudes of a convex body is 1/4 of
            # its total area (Cauchy), which is the factor used here. Wing
            # deployment therefore matters, and "deploy before you switch
            # things on" becomes a real ordering constraint.
            cos_beta = max(0.0, abs(math.cos(math.radians(beta_deg))))
            total_area = sum(self._panel_areas.get(f, 0.0) for f in PANEL_NORMALS)
            effective_area = total_area * self.TUMBLING_PROJECTION_FACTOR
            noise = 1.0 + self.rng.gauss(0, 0.0025)

            gen_w = effective_area * k_eff * cos_beta * noise
            expected_w = effective_area * k_eff * cos_beta

            # Split across the legacy A/B halves using their enable/degradation
            # state, so array failures still bite in this regime.
            a_share = 0.5 * s.sa_a_degradation if s.sa_a_enabled else 0.0
            b_share = 0.5 * s.sa_b_degradation if s.sa_b_enabled else 0.0
            share = a_share + b_share
            # 2026-08b: this was `share / 0.5`, which DOUBLES the healthy case
            # (a_share = b_share = 0.5 => share = 1.0 => x2). The tumbling
            # fallback therefore over-generated by exactly 2x, and reported
            # ~60 W for a spacecraft with no attitude control. It mattered
            # little while the branch was almost unreachable; it matters now,
            # because clearing the stale sun vector (engine.py P-c) makes this
            # the branch every uncontrolled spacecraft actually uses.
            # `share` is already the surviving fraction of the array (1.0 when
            # both halves are healthy), so it scales directly.
            gen_w *= share
            sa_a_pwr = gen_w * (a_share / share) if share else 0.0
            sa_b_pwr = gen_w - sa_a_pwr

            for face in PANEL_NORMALS:
                area = self._panel_areas.get(face, 0.0)
                s.sa_panel_currents[face] = (
                    (gen_w * area / total_area) / bus_v_gen if total_area > 0 else 0.0)
            s.sa_a_current = sa_a_pwr / bus_v_gen
            s.sa_b_current = sa_b_pwr / bus_v_gen
        else:
            # 6 body-panel model: per-panel cosine projection against sun
            # vector, plus the ±Y deployable wings on the solar array drive.
            sun_nx = sun_bx / sun_mag
            sun_ny = sun_by / sun_mag
            sun_nz = sun_bz / sun_mag
            gen_w = 0.0
            # ── String A/B health (2026-08b) ──
            # `sa_a_degradation` / `sa_b_degradation` and the two enable flags
            # are the *string-level* health the instructor's
            # `solar_array_partial` failure injection and the S2 array
            # enable/disable commands drive. Until now they were applied ONLY
            # in the tumbling fallback: the 6-panel sunlit branch read
            # `sa_panel_degradation` alone, so injecting a 50 % array-A loss on
            # a healthy, attitude-holding spacecraft moved measured generation
            # by 0.4 W (86.20 -> 85.78 W) — a first-class failure that was a
            # measured no-op. Same px/py/pz vs mx/my/mz mapping the A/B current
            # split below already uses.
            def _half(face: str) -> float:
                if face in ('px', 'py', 'pz'):
                    return s.sa_a_degradation if s.sa_a_enabled else 0.0
                return s.sa_b_degradation if s.sa_b_enabled else 0.0

            for face, normal in PANEL_NORMALS.items():
                # Cosine projection: dot product of sun vector with panel normal
                cos_angle = normal[0] * sun_nx + normal[1] * sun_ny + normal[2] * sun_nz
                illumination = max(0.0, cos_angle)  # Only sunlit when facing sun

                area = self._panel_areas_body.get(face, 0.0)
                degradation = s.sa_panel_degradation.get(face, 1.0) * _half(face)
                noise = 1.0 + self.rng.gauss(0, 0.005)

                panel_pwr = area * k_eff * illumination * degradation * noise
                expected_w += area * k_eff * illumination

                s.sa_panel_currents[face] = panel_pwr / bus_v_gen
                gen_w += panel_pwr

            # ── Deployable wings on the single-axis SADA (brief 01 WP-2) ──
            # sada_tracking: the wing pair follows the sun's projection
            #   perpendicular to the drive axis — illumination is the sun's
            #   out-of-axis component (= cos beta for axis = orbit normal),
            #   independent of orbit phase.
            # not tracking (or no SADA): the wings are frozen at
            #   solar_array_drive_angle about the drive axis; angle 0 = the
            #   legacy fixed ±Y orientation. Back-to-back cells: one wing lit
            #   at a time, |sin(phase)| duty — the "SADA stuck" failure case.
            axis = {'x': (1.0, 0.0, 0.0), 'y': (0.0, 1.0, 0.0),
                    'z': (0.0, 0.0, 1.0)}.get(self._sada_axis, (0.0, 0.0, 1.0))
            if self._sada_present and self._sada_tracking:
                # ── Rate-limited tracking (2026-08b) ──
                # `sada_max_rate_deg_s` used to be read by nothing: the drive
                # angle was assigned the tracked bearing instantly and the
                # illumination was computed from the BEARING, not the angle,
                # so the shaft was infinitely fast and the telemetry at 0x0133
                # was decoration. Now the angle SLEWS toward the bearing at the
                # configured rate and the illumination comes from where the
                # shaft actually is, which makes both the rate limit and the
                # stuck-drive failure observable.
                #
                # In nominal ops this changes nothing measurable: the required
                # tracking rate is one revolution per orbit = 0.064 deg/s
                # against a 1.0 deg/s drive, ~15x margin.
                s_dot_a = (sun_nx * axis[0] + sun_ny * axis[1]
                           + sun_nz * axis[2])
                illum_track = math.sqrt(max(0.0, 1.0 - s_dot_a * s_dot_a))
                if illum_track > 1e-6:
                    self._sada_target_angle = self._tracked_bearing_deg(
                        sun_nx, sun_ny, sun_nz)
                self._slew_drive(dt)
                n = self._wing_normal(math.radians(s.solar_array_drive_angle))
                s_dot_n = sun_nx * n[0] + sun_ny * n[1] + sun_nz * n[2]
                # Cells on both wings face the same way on a tracking shaft.
                illum_py = illum_wing_my = max(0.0, s_dot_n)
            else:
                # Not tracking (no SADA, or a STUCK drive): the wings sit at
                # solar_array_drive_angle; a commanded angle still slews there
                # at the drive rate while the drive is healthy.
                if self._sada_present:
                    self._slew_drive(dt)
                theta = math.radians(s.solar_array_drive_angle
                                     if self._sada_present else 0.0)
                # Wing normal rotated about the drive axis; home (theta=0) is
                # the +Y face normal. Back-to-back cells: one wing lit at a
                # time, |sin(phase)| duty — the "SADA stuck" failure case.
                n = (-math.sin(theta), math.cos(theta), 0.0)
                s_dot_n = sun_nx * n[0] + sun_ny * n[1] + sun_nz * n[2]
                illum_py = max(0.0, s_dot_n)
                illum_wing_my = max(0.0, -s_dot_n)

            for face, area, deployed, illum in (
                    ('py', self._wing_py_area, s.wing_py_deployed, illum_py),
                    ('my', self._wing_my_area, s.wing_my_deployed, illum_wing_my)):
                if not deployed:
                    continue
                # The +Y wing is on string A, the -Y wing on string B — same
                # mapping as the body faces (2026-08b).
                degradation = s.sa_panel_degradation.get(face, 1.0) * _half(face)
                noise = 1.0 + self.rng.gauss(0, 0.005)
                wing_pwr = area * k_eff * illum * degradation * noise
                expected_w += area * k_eff * illum
                s.sa_wing_currents[face] = wing_pwr / bus_v_gen
                s.sa_panel_currents[face] += wing_pwr / bus_v_gen
                gen_w += wing_pwr

            # Map panels to legacy SA A/B: A = +X,+Y,+Z; B = -X,-Y,-Z
            s.sa_a_current = sum(s.sa_panel_currents[f] for f in ['px', 'py', 'pz'])
            s.sa_b_current = sum(s.sa_panel_currents[f] for f in ['mx', 'my', 'mz'])

        # ── S2 device-access power gate: solar array drive (0x0101) ──
        # The solar-array drive orients the DEPLOYABLE WINGS. Switching it off
        # via S2 device access removes the wing contribution — the wings sit
        # wherever the shaft stopped and, with the drive unpowered, are not
        # producing usable bus power.
        #
        # 2026-08b: it used to zero gen_w and EVERY sa_panel_current, including
        # the five BODY-MOUNTED faces. Body cells are bonded to the structure
        # and are wired straight to the MPPT; they need no drive, so an
        # operator who switched the SADA off saw generation fall to exactly
        # 0 W in full sunlight — which is not a lesson, it is a bug that made
        # the failure look like a total array loss. Now only the wing
        # contribution is removed, so the residual is the honest body-panel
        # generation (~18 W sunlit), and the diagnosis "wings are not
        # contributing" is actually visible in the per-face currents.
        if not s.device_states.get(0x0101, True):
            for face in ('py', 'my'):
                wing_pwr = s.sa_wing_currents.get(face, 0.0) * bus_v_gen
                gen_w -= wing_pwr
                expected_w -= wing_pwr   # commanded OFF is not degraded (WP-10)
                s.sa_panel_currents[face] = max(
                    0.0, s.sa_panel_currents.get(face, 0.0)
                    - s.sa_wing_currents.get(face, 0.0))
                s.sa_wing_currents[face] = 0.0
            gen_w = max(0.0, gen_w)
            expected_w = max(0.0, expected_w)
            # Re-derive the legacy A/B halves from the surviving body faces.
            s.sa_a_current = sum(s.sa_panel_currents[f] for f in ('px', 'py', 'pz'))
            s.sa_b_current = sum(s.sa_panel_currents[f] for f in ('mx', 'my', 'mz'))

        # Array-side MPPT input voltage (brief 01 F17/C10): the old code
        # divided current*28 by current — exactly 28.0 V, a tautology. Publish
        # the string MPP voltage when the array is generating, 0 when dark.
        _SA_MPP_V = 32.4
        s.sa_a_voltage = _SA_MPP_V if s.sa_a_current > 0.01 else 0.0
        s.sa_b_voltage = _SA_MPP_V if s.sa_b_current > 0.01 else 0.0
        s.power_gen_w = gen_w

        # Power consumption — driven by power line states with per-line current
        cons_w = 0.0
        lines = s.power_lines
        bus_v = max(s.bus_voltage, 20.0)

        # Compute per-line power and current
        line_powers = {}
        line_powers["obc"] = self._obc_power_w  # non-switchable
        # Non-switchable RX line: use the configured rating (eps.yaml
        # transponder_rx_power_w) instead of the old hard-coded 5.0 W, which
        # made that config key dead.
        line_powers["ttc_rx"] = self._transponder_rx_w

        if lines.get("ttc_tx", True):
            # Tier-dependent TX power (brief 01 F20 / WP-12): bill the
            # HIGH-tier wattage when the active TM tier's information rate
            # (ttc.tm_data_rate, 0x0506) exceeds the 16 kbps LOW tier. In
            # standalone/test harnesses without a TTC model the parameter is
            # absent and the LOW rating applies (legacy behaviour).
            tm_bps = shared_params.get(0x0506, 0.0) or 0.0
            line_powers["ttc_tx"] = (
                self._transponder_hi_w if tm_bps > 32000.0 else self._transponder_w)
        else:
            line_powers["ttc_tx"] = 0.0

        # Payload billed from the ACTUAL payload state (0x0600, published by
        # the payload model) regardless of which command path set it — brief
        # 05 "Heater power vs EPS" / WP-5. Previously up to 45 W ran off the
        # books when the operator used PL_SET_MODE instead of the EPS command.
        # Fallback to the EPS-internal mode for standalone harnesses.
        pl_mode = (int(shared_params[0x0600])
                   if 0x0600 in shared_params else s.payload_mode)
        if lines.get("payload", False):
            if pl_mode == 1:
                line_powers["payload"] = self._payload_standby_w
            elif pl_mode == 2:
                line_powers["payload"] = self._payload_power_w
            else:
                line_powers["payload"] = 0.0
        else:
            line_powers["payload"] = 0.0

        # FPA cooler billed from the ACTUAL cooler state (0x040C, the unified
        # TCS cooler flag driven by all four command paths — brief 05 WP-2).
        # Fallback to the EPS-internal flag for standalone harnesses.
        cooler_actual = (bool(shared_params[0x040C])
                         if 0x040C in shared_params else s.fpa_cooler_on)
        if lines.get("fpa_cooler", False) and cooler_actual:
            line_powers["fpa_cooler"] = self._fpa_cooler_w
        else:
            line_powers["fpa_cooler"] = 0.0

        # Heater + reaction-wheel line loads come from simulation.yaml
        # eps.line_power_w. With heater_duty_from_tcs the heater lines are
        # billed from the ACTUAL thermostat/heater state TCS publishes
        # (0x040A/0x040B) — the thermostat, not the line switch, is the load
        # (brief 01 F10 / WP-5). Previously heaters billed 100 % duty, a
        # ~8 W orbit-average over-bill.
        htr_bat_on = htr_obc_on = True
        if self._heater_duty_from_tcs:
            if 0x040A in shared_params:
                htr_bat_on = bool(shared_params[0x040A])
            if 0x040B in shared_params:
                htr_obc_on = bool(shared_params[0x040B])
        line_powers["htr_bat"] = (
            self._line_power_w.get("htr_bat", 6.0)
            if (lines.get("htr_bat", True) and htr_bat_on) else 0.0)
        line_powers["htr_obc"] = (
            self._line_power_w.get("htr_obc", 4.0)
            if (lines.get("htr_obc", True) and htr_obc_on) else 0.0)
        # AOCS suite billed from the ACTUAL mode-dependent draw the AOCS
        # model publishes at 0x02C8 (Phase C: COARSE 4 / NOMINAL 9 / FINE
        # 15 W — brief 02 table), falling back to the 12 W line rating for
        # standalone harnesses. Same Phase B convention as the heaters:
        # bill actuator state, not line-switch position.
        aocs_draw = shared_params.get(0x02C8)
        if aocs_draw is None:
            aocs_draw = self._line_power_w.get("aocs_wheels", 6.0)
        line_powers["aocs_wheels"] = (
            float(aocs_draw) if lines.get("aocs_wheels", True) else 0.0)

        # Propulsion billed from the ACTUAL live draw the prop model publishes
        # at 0x0B1B (valve drivers while ARMED/FIRING + the 12 W catbed heater
        # when the TCS thruster circuit 0x041B is energised) — Phase B
        # convention: the actuator state, not the line switch, is the load.
        # Quiescent propulsion (line on, nothing commanded) draws 0 W.
        prop_draw = shared_params.get(0x0B1B)
        if prop_draw is None:
            prop_draw = self._line_power_w.get("prop", 0.0)
        line_powers["prop"] = (
            float(prop_draw) if lines.get("prop", False) else 0.0)

        # Apply overcurrent injection multiplier
        for line_name, mult in s.oc_inject.items():
            if line_name in line_powers:
                line_powers[line_name] *= mult

        # Compute currents and check overcurrent
        for i, line_name in enumerate(POWER_LINE_NAMES):
            pwr = line_powers.get(line_name, 0.0)
            current = pwr / bus_v
            s.line_currents[line_name] = current

            # Overcurrent detection
            threshold = self._oc_thresholds.get(line_name, 999.0)
            if current > threshold and POWER_LINE_SWITCHABLE.get(line_name, False):
                # Trip the line
                s.oc_trip_flags |= (1 << i)
                s.power_lines[line_name] = False
                line_powers[line_name] = 0.0
                s.line_currents[line_name] = 0.0
                # Side effects
                if line_name == "payload":
                    s.payload_mode = 0
                elif line_name == "fpa_cooler":
                    s.fpa_cooler_on = False
                elif line_name == "ttc_tx":
                    s.transponder_tx = False

            cons_w += line_powers.get(line_name, 0.0)

        if s.bus_short:
            cons_w += 80.0
        s.power_cons_w = cons_w + self.rng.gauss(0, 1.0)

        # Battery SoC
        net_power_w = gen_w - s.power_cons_w

        # ── S2 device-access power gate: battery charge regulator (0x010F) ──
        # The charge regulator is the only path by which array power can charge
        # the battery. With it switched OFF via S2 device access the battery
        # cannot take charge, so any surplus array power is dumped rather than
        # raising SoC: clamp the net power to <= 0 while the regulator is off.
        # (Loads still discharge the battery normally.) Default ON → unchanged.
        if not s.device_states.get(0x010F, True):
            net_power_w = min(net_power_w, 0.0)

        # Charge rate limiting: if charge_rate_override_a > 0, clamp charge current
        # (Defect #4 fix: enforce set_charge_rate command in model)
        if net_power_w > 0 and s.charge_rate_override_a > 0:
            # Charging case: limit charge power to charge_rate_override_a * nominal bus voltage
            max_charge_power_w = s.charge_rate_override_a * 28.0  # 28V nominal bus
            net_power_w = min(net_power_w, max_charge_power_w)

        # CV taper (brief 01 WP-4): above cv_taper_start_soc_pct the battery
        # accepts progressively less charge power (constant-voltage phase);
        # the un-accepted array surplus is shunted, not stored.
        if (net_power_w > 0 and self._cv_taper_start_pct is not None
                and s.bat_soc_pct > self._cv_taper_start_pct):
            frac = max(0.0, (100.0 - s.bat_soc_pct)
                       / max(1e-6, 100.0 - self._cv_taper_start_pct))
            net_power_w *= frac

        # Coulombic efficiency (brief 01 F19 / WP-4): only charge_efficiency
        # of the charge energy is stored; discharge draws 1/discharge_eff.
        stored_power_w = (net_power_w * self._charge_eff if net_power_w > 0
                          else net_power_w / max(self._discharge_eff, 1e-6))
        d_soc = (stored_power_w / (self._battery_capacity_wh * 3600.0)) * 100.0 * dt

        # DoD limiting: reduce charge acceptance above max DoD recovery
        old_soc = s.bat_soc_pct
        s.bat_soc_pct = max(0.0, min(100.0, s.bat_soc_pct + d_soc))
        s.bat_dod_pct = 100.0 - s.bat_soc_pct

        # Cycle count: one full charge-discharge transition
        is_charging = net_power_w > 0
        if s._was_charging and not is_charging:
            s.bat_cycles += 1
        s._was_charging = is_charging

        # Voltage
        ocv = self._soc_0_v + (self._soc_100_v - self._soc_0_v) * (s.bat_soc_pct / 100.0)
        bat_i = net_power_w / (ocv + 1e-6)
        s.bat_current = bat_i + self.rng.gauss(0, 0.1)
        # Track actual charge current (positive = charging)
        # (Defect #4: provide feedback of actual charge rate)
        if net_power_w > 0:
            s.actual_charge_current_a = max(0.0, bat_i)
        else:
            s.actual_charge_current_a = 0.0
        # Terminal voltage: v = ocv + i*R with i > 0 on CHARGE (the bus rides
        # above OCV while charging, sags below on discharge). The previous
        # sign was inverted (brief 01 F8 / WP-4), so every voltage-based
        # protection fired ~1.3 SoC points late on discharge.
        v_loaded = ocv + bat_i * self._internal_r
        if s.bat_cell_failure:
            v_loaded -= 3.7
        s.bat_voltage = max(0.0, v_loaded) + self.rng.gauss(0, 0.02)
        # Bus voltage tracks the loaded battery (single-bus, direct-energy
        # transfer architecture). Previously this was a SoC-only polynomial
        # which ignored load current and internal resistance, hiding bus
        # sag during high-power phases (payload imaging, slew, eclipse).
        s.bus_voltage = min(29.0, max(0.0, s.bat_voltage)) + self.rng.gauss(0, 0.01)

        # Undervoltage / overvoltage detection
        s.uv_flag = s.bus_voltage < self._uv_threshold
        s.ov_flag = s.bus_voltage > self._ov_threshold

        # Load shedding: progressively shed switchable loads when bus voltage
        # drops below the load-shedding threshold.  Loads are shed in priority
        # order (lowest priority first).  Lines are restored only when bus
        # voltage recovers above threshold + 0.5 V hysteresis.
        if s.bus_voltage < self._shed_voltage:
            for shed_line in self._shed_order:
                if s.power_lines.get(shed_line, False):
                    s.power_lines[shed_line] = False
                    s.shed_lines.append(shed_line)  # for auto-restore (WP-7)
                    # Apply side effects (same as overcurrent trip)
                    if shed_line == "payload":
                        s.payload_mode = 0
                    elif shed_line == "fpa_cooler":
                        s.fpa_cooler_on = False
                    elif shed_line == "ttc_tx":
                        s.transponder_tx = False
                    break  # Shed one line per tick to allow recovery
        elif (self._shed_auto_restore and s.shed_lines
              and s.bus_voltage > self._shed_voltage + self._shed_hysteresis_v):
            # Hysteresis restore (brief 01 F15 / WP-7): the code comment above
            # promised this for a year while no restore code existed. One line
            # per tick, REVERSE shed order, only lines this shedder shed.
            restore_line = s.shed_lines.pop()
            if not s.power_lines.get(restore_line, False):
                s.power_lines[restore_line] = True
                if restore_line == "ttc_tx":
                    s.transponder_tx = True

        # Battery temperature
        bat_heat_w = abs(s.bat_current) ** 2 * self._internal_r
        delta_temp = ((self._bat_temp_env - s.bat_temp) / self._bat_temp_tau + bat_heat_w / 30.0) * dt
        # Integrator process noise: sqrt(dt) scaling (plan A-3)
        s.bat_temp += delta_temp + self.rng.gauss(0, 0.05) * math.sqrt(dt)

        # ── Event detection: Battery temperature extremes (rising-edge only) ──
        bat_overtemp = s.bat_temp > self._evt["bat_overtemp_c"]
        if bat_overtemp and not s._prev_bat_overtemp:
            events_to_generate.append((0x0109, f"Battery overtemp: {s.bat_temp:.1f}C"))
        s._prev_bat_overtemp = bat_overtemp
        bat_undertemp = s.bat_temp < self._evt["bat_undertemp_c"]
        if bat_undertemp and not s._prev_bat_undertemp:
            events_to_generate.append((0x010A, f"Battery undertemp: {s.bat_temp:.1f}C"))
        s._prev_bat_undertemp = bat_undertemp

        # ── Event detection: SoC thresholds ──
        soc_crit = self._evt["soc_critical_pct"]
        soc_low = self._evt["soc_low_pct"]
        if s.bat_soc_pct < soc_crit and s._prev_soc >= soc_crit:
            events_to_generate.append((0x0102, f"Battery critical SoC: {s.bat_soc_pct:.1f}%"))
        elif s.bat_soc_pct < soc_low and s._prev_soc >= soc_low:
            events_to_generate.append((0x0101, f"Battery low SoC warning: {s.bat_soc_pct:.1f}%"))

        # ── Event detection: Charge complete ──
        soc_full = self._evt["soc_charge_complete_pct"]
        if s.bat_soc_pct >= soc_full and s._prev_soc < soc_full and s._was_charging:
            events_to_generate.append((0x010E, f"Battery charge complete: {s.bat_soc_pct:.1f}%"))

        # ── Event detection: Bus voltage issues ──
        bus_crit = self._evt["bus_critical_v"]
        bus_warn = self._evt["bus_warning_v"]
        if s.bus_voltage < bus_crit and s._prev_bus_voltage >= bus_crit:
            events_to_generate.append((0x0104, f"Bus undervoltage critical: {s.bus_voltage:.2f}V"))
        elif s.bus_voltage < bus_warn and s._prev_bus_voltage >= bus_warn:
            events_to_generate.append((0x0103, f"Bus undervoltage warning: {s.bus_voltage:.2f}V"))

        # ── Event detection: Overcurrent trips ──
        new_trips = s.oc_trip_flags & ~s._prev_oc_trip_flags
        for i in range(len(POWER_LINE_NAMES)):
            if new_trips & (1 << i):
                line_name = POWER_LINE_NAMES[i]
                events_to_generate.append((0x010F, f"PDU overcurrent trip: {line_name}"))

        # ── Event detection: Load shedding stages ──
        # The published stage (0x0134) is the max of the COMMANDED latch
        # (EPS_EMERGENCY_LOAD_SHED / EPS_SET_MODE safing) and the autonomous
        # bus-voltage shedder's progress (2026-08c item 8 — the commanded
        # stage used to be recomputed to 0 here every tick).
        cur_load_shed_stage = int(getattr(s, "commanded_shed_stage", 0))
        if s.bus_voltage < self._shed_voltage:
            shed_count = sum(1 for ln in self._shed_order if not s.power_lines.get(ln, False))
            cur_load_shed_stage = max(cur_load_shed_stage, min(3, shed_count))

        # One event PER STAGE crossed, so a direct 0 -> 3 shed still logs
        # stages 1 and 2 (each stage is a distinct S5 event id the FDIR /
        # alarm journal keys on).
        for st in range(s._prev_load_shed_stage + 1, cur_load_shed_stage + 1):
            events_to_generate.append((0x0106 + st - 1, f"Load shed stage {st}"))

        s.load_shed_stage = cur_load_shed_stage

        # ── Event detection: Solar array degradation ──
        # Expected power comes from the LIVE 6-panel + wing model with the
        # loss stack and the current sun geometry, degradation factors held at
        # 1.0 (brief 01 F11 / WP-10). The old reference used the dead legacy
        # `arrays:` area (0.628 m2 -> 244 W "expected"), which was above the
        # real peak, so the alarm fired on the first sunlit tick of every run
        # and a genuine failure afterwards produced no event.
        expected_sa_power = expected_w
        actual_sa_power = s.power_gen_w
        sa_degraded = (
            expected_sa_power > 50.0
            and actual_sa_power < expected_sa_power * self._evt["sa_degraded_fraction"]
            and not s.in_eclipse
        )
        # Rising-edge: emit once when the array first drops below the
        # degradation threshold. The edge state only updates while the check
        # is MEANINGFUL (sunlit, expected power above the floor) so the latch
        # holds through eclipse instead of re-firing every sunrise.
        if sa_degraded and not s._prev_sa_degraded:
            events_to_generate.append((0x010B, f"Solar array degraded: {actual_sa_power:.1f}W vs {expected_sa_power:.1f}W"))
        if not s.in_eclipse and expected_sa_power > 50.0:
            s._prev_sa_degraded = sa_degraded

        # ── Event detection: Power line state changes ──
        # Edge-detect against a DEDICATED previous power-line bitmask. Previously
        # this compared against `_prev_oc_trip_flags` (a different thing that was
        # never the prior line state), so any switchable line that was ON re-fired
        # "switched ON" every tick (e.g. ttc_tx, aocs_wheels). First tick (mask ==
        # -1) only initialises the baseline without emitting.
        cur_mask = 0
        for i, line_name in enumerate(POWER_LINE_NAMES):
            if s.power_lines.get(line_name, False):
                cur_mask |= (1 << i)
        if s._prev_power_line_mask != -1:
            for i, line_name in enumerate(POWER_LINE_NAMES):
                bit = 1 << i
                rose = (cur_mask & bit) and not (s._prev_power_line_mask & bit)
                if rose and POWER_LINE_SWITCHABLE.get(line_name, False):
                    events_to_generate.append((0x0110, f"Power line {line_name} switched ON"))
        s._prev_power_line_mask = cur_mask

        # ── Event detection: EPS mode changes ──
        if s.eps_mode != s._prev_mode:
            events_to_generate.append((0x0100, f"EPS mode change: {s._prev_mode} -> {s.eps_mode}"))
            # Defect #27: entering safe/emergency mode must take a real protective
            # action — shed non-essential loads. Previously eps_mode drove no
            # physics, so FDIR EPS-safing (and the set_eps_mode command) had no
            # effect. Turn OFF the least-essential loads (monotonic — never powers
            # anything on): 2 loads on safe (mode 1), all on emergency (mode 2).
            # Loads are restored by the operator, not automatically.
            if s.eps_mode >= 1:
                n_shed = len(self._shed_order) if s.eps_mode >= 2 else 2
                for load_name in self._shed_order[:n_shed]:
                    s.power_lines[load_name] = False
                # Latch the commanded stage so the tick's stage derivation
                # (above) keeps 0x0134 truthful next tick instead of
                # zeroing it (2026-08c item 8).
                s.commanded_shed_stage = max(
                    int(getattr(s, "commanded_shed_stage", 0)),
                    min(3, n_shed))
                s.load_shed_stage = max(s.load_shed_stage, min(3, n_shed))

        # Push events to the engine for S5 generation (defect #23)
        if events_to_generate and getattr(self, '_engine', None):
            for event_id, event_desc in events_to_generate:
                self._engine._model_event_queue.put((event_id, event_desc, self._get_time()))

        # ── Update previous state tracking ──
        s._prev_soc = s.bat_soc_pct
        s._prev_bus_voltage = s.bus_voltage
        s._prev_in_eclipse = s.in_eclipse
        s._prev_oc_trip_flags = s.oc_trip_flags
        s._prev_load_shed_stage = s.load_shed_stage
        s._prev_mode = s.eps_mode

        # Write shared params
        p = self._param_ids
        shared_params[p.get("bat_voltage", 0x0100)] = s.bat_voltage
        shared_params[p.get("bat_soc", 0x0101)] = s.bat_soc_pct
        shared_params[p.get("bat_temp", 0x0102)] = s.bat_temp
        shared_params[p.get("bat_current", 0x0109)] = s.bat_current
        shared_params[p.get("bat_capacity_wh", 0x010A)] = self._battery_capacity_wh * (s.bat_soc_pct / 100.0)
        shared_params[p.get("sa_a_current", 0x0103)] = s.sa_a_current
        shared_params[p.get("sa_b_current", 0x0104)] = s.sa_b_current
        shared_params[p.get("bus_voltage", 0x0105)] = s.bus_voltage
        shared_params[p.get("power_cons", 0x0106)] = s.power_cons_w
        shared_params[p.get("power_gen", 0x0107)] = s.power_gen_w
        shared_params[p.get("eclipse_flag", 0x0108)] = 1 if s.in_eclipse else 0

        # New EPS params (Phase 3)
        shared_params[0x010B] = s.sa_a_voltage
        shared_params[0x010C] = s.sa_b_voltage
        shared_params[0x010D] = s.oc_trip_flags
        shared_params[0x010E] = 1 if s.uv_flag else 0
        shared_params[0x010F] = 1 if s.ov_flag else 0

        # Power line status (0x0110-0x0117 for lines 0-7; later lines have
        # explicit ids — prop publishes at 0x014A, Phase D)
        for i, line_name in enumerate(POWER_LINE_NAMES[:8]):
            shared_params[0x0110 + i] = 1 if s.power_lines.get(line_name, False) else 0

        # Per-line currents (0x0118-0x011F for lines 0-7; prop at 0x014B)
        for i, line_name in enumerate(POWER_LINE_NAMES[:8]):
            shared_params[0x0118 + i] = s.line_currents.get(line_name, 0.0)

        for line_name, (pid_state, pid_cur) in _EXTRA_LINE_PARAM_IDS.items():
            shared_params[pid_state] = 1 if s.power_lines.get(line_name, False) else 0
            shared_params[pid_cur] = s.line_currents.get(line_name, 0.0)

        # Phase 4: Flight hardware realism
        shared_params[0x0120] = s.bat_dod_pct
        shared_params[0x0121] = float(s.bat_cycles)
        shared_params[0x0122] = s.mppt_efficiency
        shared_params[0x0123] = s.sa_age_factor
        shared_params[0x0124] = s.sa_a_degradation
        shared_params[0x0125] = s.sa_b_degradation
        shared_params[0x0126] = s.sa_lifetime_hours

        # PDM and separation
        shared_params[0x0127] = 1 if s.sep_timer_active else 0
        shared_params[0x0128] = s.sep_timer_remaining
        # ID collision resolution (brief 01 F16 / WP-11):
        #   0x0129 = spacecraft_phase (engine-owned, _tick_spacecraft_phase)
        #   0x012A = pdm_unsw_status  (was parked on 0x013A, which
        #            parameters.yaml declares as eps.eclipse_active)
        #   0x013A = eclipse_active   (now actually written)
        shared_params[0x012A] = float(s.pdm_unsw_status)
        shared_params[0x013A] = 1.0 if s.in_eclipse else 0.0
        # 0x0142 = the illumination AUTHORITY on the wire (0.0 umbra … 1.0
        # full sun), copied verbatim from the orbit propagator. 0x0108 /
        # 0x013A are its boolean spelling; all three come from one number so
        # they cannot disagree, and the ground can now see WHY generation is
        # what it is instead of inferring it from a flag.
        shared_params[0x0142] = float(s.sun_fraction)

        # Per-panel solar array currents
        panel_names = ['px', 'mx', 'py', 'my', 'pz', 'mz']
        for i, face in enumerate(panel_names):
            shared_params[0x012B + i] = s.sa_panel_currents.get(face, 0.0)

        # Enhanced telemetry: load switching and power management
        shared_params[0x0131] = float(sum(1 << i for i, ln in enumerate(POWER_LINE_NAMES) if s.power_lines.get(ln, False)))
        shared_params[0x0132] = s.charge_rate_override_a
        shared_params[0x0143] = s.actual_charge_current_a  # Defect #4: actual charge current feedback
        shared_params[0x0133] = s.solar_array_drive_angle
        shared_params[0x0134] = float(s.load_shed_stage)
        shared_params[0x0135] = s.power_gen_w - s.power_cons_w  # Power margin
        shared_params[0x0136] = max(0.0, 100.0 - s.bat_dod_pct)  # Battery health percentage
        shared_params[0x0137] = float(s.eps_mode)
        shared_params[0x0138] = 1 if s.battery_heater_on else 0
        shared_params[0x0139] = s.battery_heater_setpoint_c
        shared_params[0x010E] = 1 if s.uv_flag else 0
        shared_params[0x010F] = 1 if s.ov_flag else 0
        # Deployable wing status
        wing_status = 0
        if s.wing_py_deployed: wing_status |= 0x01
        if s.wing_my_deployed: wing_status |= 0x02
        if s.wing_py_deploying: wing_status |= 0x10
        if s.wing_my_deploying: wing_status |= 0x20
        shared_params[0x0144] = float(wing_status)
        shared_params[0x0145] = s.wing_deploy_timer

        # ── Power-management telemetry (brief 01 F16 / WP-11) ──
        # power_margin + the six per-subsystem power draws were declared in
        # parameters.yaml and displayed twice in the MCS, but written by
        # nothing. Billed line powers are grouped by the subsystem that owns
        # the equipment on the line.
        shared_params[0x013B] = s.power_gen_w - s.power_cons_w
        _lp = line_powers
        # ── Breakdown must RECONCILE with the bus load (2026-08b) ──
        # The six buckets did not add up to 0x0106 and could not: 0x013C (EPS
        # itself) was hard-wired 0.0, and the propulsion line, an injected
        # 80 W bus short and the measurement noise belonged to no bucket at
        # all. An operator comparing the breakdown panel against total
        # consumption found several watts — or, with a bus short, eighty —
        # missing with nothing to attribute them to.
        #
        # Fix, in two parts:
        #  (a) the `obc` line is OBC 3 W + PDU/EPS housekeeping 3 W (see
        #      eps.yaml obc_power_w); split it so the EPS bucket carries its
        #      own housekeeping instead of billing it to OBDH;
        #  (b) the EPS bucket then absorbs the RESIDUAL — propulsion, bus
        #      short, noise — so sum(0x013C..0x0141) == 0x0106 identically.
        #      A large unexplained EPS bucket is itself the symptom (that is
        #      what an 80 W bus short looks like from the breakdown panel).
        _hk = min(self.EPS_HOUSEKEEPING_SHARE_W, _lp.get("obc", 0.0))
        shared_params[0x013D] = _lp.get("aocs_wheels", 0.0)           # AOCS suite
        shared_params[0x013E] = (_lp.get("htr_bat", 0.0)
                                 + _lp.get("htr_obc", 0.0))           # TCS heaters
        shared_params[0x013F] = (_lp.get("ttc_rx", 0.0)
                                 + _lp.get("ttc_tx", 0.0))            # TTC
        shared_params[0x0140] = (_lp.get("payload", 0.0)
                                 + _lp.get("fpa_cooler", 0.0))        # Payload chain
        shared_params[0x0141] = _lp.get("obc", 0.0) - _hk             # OBDH
        shared_params[0x013C] = max(0.0, s.power_cons_w - (
            shared_params[0x013D] + shared_params[0x013E]
            + shared_params[0x013F] + shared_params[0x0140]
            + shared_params[0x0141]))                                 # EPS + residual
        # Per-wing array currents (separate from the ±Y body faces) and the
        # total load bus current.
        shared_params[0x0146] = s.sa_wing_currents.get('py', 0.0)
        shared_params[0x0147] = s.sa_wing_currents.get('my', 0.0)
        shared_params[0x0148] = sum(s.line_currents.values())
        # EPS-side cooler command flag (func 17) — one input to the unified
        # cooler state assembled by TCS (brief 05 WP-2).
        shared_params[0x0149] = 1.0 if s.fpa_cooler_on else 0.0

        # NOTE: An earlier duplicate telemetry-write block that referenced
        # undefined locals `lines`, `gen_w`, `cons_w` and re-wrote all of the
        # params already set above has been removed. It would have crashed on
        # the first tick. All power-line / per-line current / Phase 4 / panel /
        # 0x0131–0x0139 params are written exactly once, earlier in this
        # method, using the canonical ``s.power_lines`` / ``s.line_currents``
        # state. See defects/reviews/power.md for the full defect entry.

    def get_telemetry(self) -> dict[int, float]:
        s = self._state
        p = self._param_ids
        return {
            p.get("bat_voltage", 0x0100): s.bat_voltage,
            p.get("bat_soc", 0x0101): s.bat_soc_pct,
            p.get("bat_temp", 0x0102): s.bat_temp,
            p.get("bus_voltage", 0x0105): s.bus_voltage,
            p.get("power_gen", 0x0107): s.power_gen_w,
            p.get("power_cons", 0x0106): s.power_cons_w,
        }

    # ── Deferred completion (verification-semantics ruling, 2026-08) ──

    def _wing_deploy_timeout_s(self) -> float:
        """Timeout for a held wing-deploy S1.7: 4x the nominal sweep, floor 120 s."""
        return max(120.0, 4.0 * float(self._wing_deploy_time_s))

    def _complete(self, key: str, success: bool = True,
                  error_code: int = 0x0001, reason: str = "") -> None:
        """Queue a deferred-completion report for the engine to drain."""
        self._completions.append({"key": str(key), "success": bool(success),
                                  "error_code": int(error_code),
                                  "reason": str(reason or "")})

    def pop_completions(self) -> list[dict]:
        out, self._completions = self._completions, []
        return out

    def handle_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        command = cmd.get("command", "")
        if command == "set_payload_mode":
            mode = int(cmd.get("mode", 0))
            if mode in (0, 1, 2):
                self._state.payload_mode = mode
                return {"success": True}
            return {"success": False, "message": "Invalid mode"}
        elif command == "set_fpa_cooler":
            on = bool(cmd.get("on", False))
            self._state.fpa_cooler_on = on
            # EPS owns the PDU: the EPS cooler command implies its power line
            # (brief 05 WP-2 — one cryocooler, one effective state). ON
            # energises line 4; OFF de-energises it so the cooler stops even
            # if the payload is still requesting cooling.
            self._state.power_lines["fpa_cooler"] = on
            return {"success": True}
        elif command == "set_transponder_tx":
            self._state.transponder_tx = bool(cmd.get("on", True))
            return {"success": True}
        elif command == "disable_array":
            arr = cmd.get("array", "A")
            if arr == "A": self._state.sa_a_enabled = False
            elif arr == "B": self._state.sa_b_enabled = False
            return {"success": True}
        elif command == "enable_array":
            arr = cmd.get("array", "A")
            if arr == "A": self._state.sa_a_enabled = True
            elif arr == "B": self._state.sa_b_enabled = True
            return {"success": True}
        elif command == "power_line_on":
            return self._set_power_line(cmd, True)
        elif command == "power_line_off":
            return self._set_power_line(cmd, False)
        elif command == "reset_oc_flag":
            return self._reset_oc_flag(cmd)
        elif command == "switch_load":
            load_id = cmd.get("load_id", 0)
            state = bool(cmd.get("state", False))
            return self._switch_load(load_id, state)
        elif command == "set_charge_rate":
            rate_a = float(cmd.get("rate_a", 0.0))
            self._state.charge_rate_override_a = rate_a
            return {"success": True, "message": f"Charge rate set to {rate_a}A"}
        elif command == "set_solar_array_drive":
            # 2026-08b: this used to slam the angle instantly AND be inert
            # while tracking (the tracking branch overwrote it every tick).
            # It now sets the drive's TARGET; the shaft slews there at
            # sada_max_rate_deg_s, and commanding a manual angle drops the
            # drive out of sun-tracking — which is what a real
            # position-commanded SADA does.
            angle = max(-90.0, min(90.0, float(cmd.get("angle_deg", 0.0))))
            self._sada_target_angle = angle
            if self._sada_present and not self._sada_stuck:
                self._sada_tracking = False
            if self._sada_stuck:
                return {"success": False,
                        "message": "Solar array drive is stuck — angle command "
                                   f"accepted as target {angle} deg but the "
                                   "shaft cannot move"}
            return {"success": True,
                    "message": f"Solar array drive slewing to {angle} deg "
                               f"at {self._sada_max_rate} deg/s (sun-tracking "
                               f"disabled)"}
        elif command == "set_solar_array_tracking":
            # Re-arm sun-tracking after a manual reposition (or after the
            # stuck-drive failure is cleared).
            on = bool(cmd.get("enable", True))
            self._sada_tracking = on and self._sada_present and not self._sada_stuck
            return {"success": True,
                    "message": f"Solar array sun-tracking "
                               f"{'enabled' if self._sada_tracking else 'disabled'}"}
        elif command == "emergency_load_shed":
            stage = int(cmd.get("stage", 1))
            return self._emergency_load_shed(stage)
        elif command == "bus_isolate":
            bus_id = int(cmd.get("bus_id", 0))
            return {"success": True, "message": f"Bus {bus_id} isolation command acknowledged"}
        elif command == "set_battery_heater":
            on = bool(cmd.get("on", True))
            setpoint = float(cmd.get("setpoint_c", 10.0))
            self._state.battery_heater_on = on
            self._state.battery_heater_setpoint_c = setpoint
            return {"success": True, "message": f"Battery heater: {on}, setpoint {setpoint}C"}
        elif command == "reset_trip":
            line_idx = cmd.get("line_index", -1)
            if isinstance(line_idx, int) and 0 <= line_idx < len(POWER_LINE_NAMES):
                line_name = POWER_LINE_NAMES[line_idx]
                self._state.oc_trip_flags &= ~(1 << line_idx)
                self._state.power_lines[line_name] = True
                return {"success": True, "message": f"Trip reset: {line_name}"}
            return {"success": False, "message": "Invalid line index"}
        elif command == "get_power_budget":
            return self._get_power_budget_summary()
        elif command == "set_eps_mode":
            mode = int(cmd.get("mode", 0))
            if mode in (0, 1, 2):
                self._state.eps_mode = mode
                return {"success": True, "message": f"EPS mode set to {mode}"}
            return {"success": False, "message": "Invalid EPS mode"}
        elif command == "deploy_wing":
            return self._deploy_wing(cmd)
        elif command == "get_wing_status":
            s = self._state
            return {"success": True, "py_deployed": s.wing_py_deployed,
                    "my_deployed": s.wing_my_deployed,
                    "py_deploying": s.wing_py_deploying,
                    "my_deploying": s.wing_my_deploying,
                    "deploy_timer": s.wing_deploy_timer}
        return {"success": False, "message": f"Unknown command: {command}"}

    def _deploy_wing(self, cmd: dict) -> dict[str, Any]:
        """Deploy a solar wing (+Y or -Y)."""
        s = self._state
        wing = cmd.get("wing", "").lower()
        if wing == "py" or wing == "+y":
            if s.wing_py_deployed:
                return {"success": False, "message": "+Y wing already deployed"}
            if s.wing_py_deploying:
                return {"success": False, "message": "+Y wing deployment in progress"}
            s.wing_py_deploying = True
            s.wing_deploy_timer = self._wing_deploy_time_s
            s.wing_deploy_target = "py"
            # Execution STARTED: the panel is still stowed and the array area
            # has not changed until the burn-wire timer expires, so the S1.7
            # waits for COMPLETION_WING_PY (verification-semantics ruling).
            return {"success": True,
                    "deferred": COMPLETION_WING_PY,
                    "deferred_timeout_s": self._wing_deploy_timeout_s(),
                    "deferred_label": "EPS_DEPLOY_WING(+Y)",
                    "message": f"+Y wing deployment initiated ({self._wing_deploy_time_s:.0f}s)"}
        elif wing == "my" or wing == "-y":
            if s.wing_my_deployed:
                return {"success": False, "message": "-Y wing already deployed"}
            if s.wing_my_deploying:
                return {"success": False, "message": "-Y wing deployment in progress"}
            s.wing_my_deploying = True
            s.wing_deploy_timer = self._wing_deploy_time_s
            s.wing_deploy_target = "my"
            return {"success": True,
                    "deferred": COMPLETION_WING_MY,
                    "deferred_timeout_s": self._wing_deploy_timeout_s(),
                    "deferred_label": "EPS_DEPLOY_WING(-Y)",
                    "message": f"-Y wing deployment initiated ({self._wing_deploy_time_s:.0f}s)"}
        elif wing == "both":
            msgs = []
            if not s.wing_py_deployed and not s.wing_py_deploying:
                s.wing_py_deploying = True
                msgs.append("+Y")
            if not s.wing_my_deployed and not s.wing_my_deploying:
                s.wing_my_deploying = True
                msgs.append("-Y")
            if msgs:
                s.wing_deploy_timer = self._wing_deploy_time_s
                return {"success": True,
                        "deferred": COMPLETION_WING_BOTH,
                        "deferred_timeout_s": self._wing_deploy_timeout_s(),
                        "deferred_label": "EPS_DEPLOY_WING(both)",
                        "message": f"Wing deployment initiated: {', '.join(msgs)} ({self._wing_deploy_time_s:.0f}s)"}
            return {"success": False, "message": "Both wings already deployed"}
        return {"success": False, "message": f"Invalid wing: {wing} (use 'py', 'my', or 'both')"}

    def _set_power_line(self, cmd: dict, on: bool) -> dict[str, Any]:
        """Switch a power line on or off."""
        line_idx = cmd.get("line_index", -1)
        if isinstance(line_idx, int) and 0 <= line_idx < len(POWER_LINE_NAMES):
            line_name = POWER_LINE_NAMES[line_idx]
        else:
            line_name = cmd.get("line_name", "")

        if line_name not in POWER_LINE_NAMES:
            return {"success": False, "message": f"Unknown power line: {line_name}",
                    "error_code": 0x0005}

        if not POWER_LINE_SWITCHABLE[line_name]:
            return {"success": False,
                    "message": f"Power line {line_name} is not switchable",
                    "error_code": 0x0006}

        self._state.power_lines[line_name] = on

        # Side effects
        if not on:
            if line_name == "payload":
                self._state.payload_mode = 0
            elif line_name == "fpa_cooler":
                self._state.fpa_cooler_on = False
            elif line_name == "ttc_tx":
                self._state.transponder_tx = False
        else:
            if line_name == "ttc_tx":
                self._state.transponder_tx = True

        return {"success": True}

    def _reset_oc_flag(self, cmd: dict) -> dict[str, Any]:
        """Reset overcurrent trip flag and re-enable the line."""
        line_idx = cmd.get("line_index", -1)
        if not isinstance(line_idx, int) or line_idx < 0 or line_idx >= len(POWER_LINE_NAMES):
            return {"success": False, "message": "Invalid line index"}
        line_name = POWER_LINE_NAMES[line_idx]
        if not (self._state.oc_trip_flags & (1 << line_idx)):
            return {"success": False, "message": "Line not tripped"}
        # Clear trip flag
        self._state.oc_trip_flags &= ~(1 << line_idx)
        # Re-enable line
        if POWER_LINE_SWITCHABLE.get(line_name, False):
            self._state.power_lines[line_name] = True
        return {"success": True}

    # ─── Failure injection ───────────────────────────────────────────
    #
    # FAILURE_SPECS is the single source of truth for what each injection
    # below accepts. The instructor UI generates its form from it (served by
    # GET /api/failure-catalog), FailureManager.inject() validates against it,
    # and the scenario content gate checks every shipped scenario file with
    # it. Keep it adjacent to inject_failure() — if you add a `kwargs.get(...)`
    # there, it needs a ParamSpec here or callers cannot reach it.
    FAILURE_SPECS = {
        "solar_array_partial": FailureSpec(
            name="solar_array_partial",
            label="Solar array partial degradation",
            effect="Scales one wing's output by (1 - magnitude); array current "
                   "0x0146/0x0147 and bus power fall, SoC follows.",
            magnitude=ParamSpec(
                name="magnitude", type=T_FLOAT, label="Output loss",
                unit="fraction", min=0.0, max=1.0, default=0.5, step=0.01,
                description="0 = healthy, 1 = wing dead"),
            params=(
                enum("array", "Wing", ["A", "B"], labels=["Wing A (+Y)", "Wing B (-Y)"],
                     default="A"),
            ),
        ),
        "solar_panel_loss": FailureSpec(
            name="solar_panel_loss", label="Solar panel (body face) loss",
            effect="Zeroes/derates one body face's contribution to array current.",
            magnitude=ParamSpec(
                name="magnitude", type=T_FLOAT, label="Output loss",
                unit="fraction", min=0.0, max=1.0, default=1.0, step=0.01),
            params=(
                enum("face", "Panel face", SOLAR_PANEL_FACES,
                     labels=[f.upper() for f in SOLAR_PANEL_FACES], default="px"),
            ),
        ),
        "solar_array_total_loss": FailureSpec(
            name="solar_array_total_loss", label="Total solar array loss",
            effect="Both wings and all body faces dead — worst-case power "
                   "contingency, drives the progressive load-shed ladder.",
            magnitude=None,
        ),
        "bat_cell": FailureSpec(
            name="bat_cell", label="Battery cell failure",
            effect="Marks one cell string failed: capacity and bus voltage drop.",
            magnitude=ParamSpec(name="magnitude", type=T_BOOL,
                                label="Failed", default=True),
        ),
        "bus_short": FailureSpec(
            name="bus_short", label="Main bus short",
            effect="Adds a large parasitic load to the 28 V bus; SoC collapses.",
            magnitude=ParamSpec(name="magnitude", type=T_BOOL,
                                label="Shorted", default=True),
        ),
        "overcurrent": FailureSpec(
            name="overcurrent", label="Power line overcurrent",
            effect="Multiplies the selected line's draw; above its OC threshold "
                   "the LCL trips and the line drops out.",
            magnitude=ParamSpec(
                name="magnitude", type=T_FLOAT, label="Current multiplier",
                unit="x nominal", min=0.0, max=20.0, default=3.0, step=0.1),
            params=(
                enum("line_index", "Power line", list(range(len(POWER_LINE_NAMES))),
                     labels=[f"{i} — {d[3]}" for i, d in enumerate(POWER_LINE_DEFS)],
                     default=3),
            ),
        ),
        "undervoltage": FailureSpec(
            name="undervoltage", label="Battery undervoltage (SoC drain)",
            effect="One-shot removal of state of charge, to drive the "
                   "undervoltage protection ladder.",
            magnitude=ParamSpec(
                name="magnitude", type=T_FLOAT,
                label="Severity (legacy: x10 % SoC)", unit="fraction",
                min=0.0, max=10.0, default=2.0, step=0.1,
                description="Ignored when an explicit SoC drop is given."),
            params=(
                number("soc_pct", "SoC to remove", unit="% SoC", lo=0.0,
                       hi=100.0, step=0.5,
                       description="Absolute value; overrides magnitude."),
            ),
            reversible=False,
            notes="Not reversible by clear: SoC recovers physically by charging.",
        ),
        "overvoltage": FailureSpec(
            name="overvoltage", label="Overvoltage detection threshold drop",
            effect="Lowers the OV trip threshold so the OV flag sets at the "
                   "current bus voltage.",
            magnitude=ParamSpec(
                name="magnitude", type=T_FLOAT, label="Threshold reduction",
                unit="V", min=0.0, max=4.5, default=2.0, step=0.1),
        ),
        "sada_stuck": FailureSpec(
            name="sada_stuck", label="Solar array drive stuck",
            effect="Freezes the SADA shaft: 0x0133 goes flat, wing currents "
                   "develop a |sin| ripple, orbit-average yield falls ~36 %.",
            magnitude=None,
            params=(
                number("angle_deg", "Seize at angle", unit="deg", lo=-180.0,
                       hi=360.0, step=1.0,
                       description="Omit to seize at the current shaft angle."),
            ),
        ),
    }

    def inject_failure(self, failure: str, magnitude: float = 1.0, **kwargs) -> None:
        if failure == "solar_array_partial":
            arr = kwargs.get("array", "A")
            if arr == "A":
                self._state.sa_a_degradation = max(0.0, min(1.0, 1.0 - magnitude))
            else:
                self._state.sa_b_degradation = max(0.0, min(1.0, 1.0 - magnitude))
        elif failure == "bat_cell":
            self._state.bat_cell_failure = bool(magnitude)
        elif failure == "bus_short":
            self._state.bus_short = bool(magnitude)
        elif failure == "overcurrent":
            # Inject overcurrent on a specific line via multiplier
            line_idx = kwargs.get("line_index", 3)
            if isinstance(line_idx, int) and 0 <= line_idx < len(POWER_LINE_NAMES):
                line_name = POWER_LINE_NAMES[line_idx]
                self._state.oc_inject[line_name] = magnitude
        elif failure == "undervoltage":
            # Remove state of charge so the bus voltage falls and the
            # undervoltage protection ladder runs. `soc_pct` is the real
            # engineering value (percentage points of SoC to remove); the
            # legacy `magnitude * 10` mapping is kept for scenario files and
            # the old 0..1 severity slider that predate the typed parameter.
            drop = kwargs.get("soc_pct")
            if drop is None:
                drop = magnitude * 10.0
            self._state.bat_soc_pct = max(0.0,
                                          self._state.bat_soc_pct - float(drop))
        elif failure == "overvoltage":
            # Will be detected by ov_flag threshold check. Remember the
            # CONFIGURED threshold so clear_failure restores simulation.yaml's
            # `eps.ov_threshold_v` instead of a hard-coded 29.5 V.
            if self._ov_threshold_nominal is None:
                self._ov_threshold_nominal = self._ov_threshold
            self._ov_threshold = max(25.0, self._ov_threshold - magnitude)
        elif failure == "solar_panel_loss":
            face = kwargs.get("face", "px")
            if face in self._state.sa_panel_degradation:
                self._state.sa_panel_degradation[face] = max(0.0, 1.0 - magnitude)

        elif failure == "sada_stuck":
            # ── Stuck solar array drive (2026-08b) ──
            # A good teaching failure that the model described in comments but
            # could not produce: nothing at runtime could clear
            # `_sada_tracking`. The shaft seizes where it is; the sun walks
            # away from the frozen normal, so 0x0133 goes flat while wing
            # currents (0x0146/0x0147) develop a |sin(phase)| ripple and the
            # orbit-average wing yield falls ~36 %. Diagnosable from telemetry
            # that already exists, with no new parameter IDs.
            self._sada_stuck = True
            self._sada_tracking = False
            angle = kwargs.get("angle_deg")
            if angle is not None:
                self._state.solar_array_drive_angle = float(angle)
            self._sada_target_angle = self._state.solar_array_drive_angle

        elif failure == "solar_array_total_loss":
            # Both wings dead — drives the worst-case
            # solar_panel_loss_response / progressive_load_shed contingency.
            self._state.sa_a_degradation = 0.0
            self._state.sa_b_degradation = 0.0
            for face in self._state.sa_panel_degradation:
                self._state.sa_panel_degradation[face] = 0.0

    def clear_failure(self, failure: str, **kwargs) -> None:
        if failure == "solar_array_partial":
            arr = kwargs.get("array", "A")
            if arr == "A": self._state.sa_a_degradation = 1.0
            else: self._state.sa_b_degradation = 1.0
        elif failure == "bat_cell":
            self._state.bat_cell_failure = False
        elif failure == "bus_short":
            self._state.bus_short = False
        elif failure == "overcurrent":
            line_idx = kwargs.get("line_index", 3)
            if isinstance(line_idx, int) and 0 <= line_idx < len(POWER_LINE_NAMES):
                line_name = POWER_LINE_NAMES[line_idx]
                self._state.oc_inject.pop(line_name, None)
        elif failure == "undervoltage":
            # SoC recovers naturally during charging; mark the inject "off" by
            # zeroing any residual injection bookkeeping. Recovery is physical.
            pass
        elif failure == "overvoltage":
            if self._ov_threshold_nominal is not None:
                self._ov_threshold = self._ov_threshold_nominal
                self._ov_threshold_nominal = None
        elif failure == "solar_panel_loss":
            face = kwargs.get("face", "px")
            if face in self._state.sa_panel_degradation:
                self._state.sa_panel_degradation[face] = 1.0

        elif failure == "sada_stuck":
            self._sada_stuck = False
            self._sada_tracking = self._sada_tracking_nominal

        elif failure == "solar_array_total_loss":
            self._state.sa_a_degradation = 1.0
            self._state.sa_b_degradation = 1.0
            for face in self._state.sa_panel_degradation:
                self._state.sa_panel_degradation[face] = 1.0

    # Model-level (non-dataclass) entries carried in the state dict. These are
    # FAILURE state: `sada_stuck` lives entirely in `_sada_stuck` /
    # `_sada_tracking` and `overvoltage` entirely in `_ov_threshold`, neither
    # of which is a dataclass field — so before this both injections silently
    # HEALED across a breakpoint save/load (the gyro_bias bug again).
    _EXTRA_STATE_KEYS = ("_rng_state", "_sada_stuck", "_sada_tracking",
                         "_sada_target_angle", "_ov_threshold",
                         "_ov_threshold_nominal")

    def get_state(self) -> dict[str, Any]:
        import dataclasses
        from smo_common.state import rng_state_to_json
        d = dataclasses.asdict(self._state)
        d["_rng_state"] = rng_state_to_json(self.rng.getstate())
        d["_sada_stuck"] = bool(self._sada_stuck)
        d["_sada_tracking"] = bool(self._sada_tracking)
        d["_sada_target_angle"] = float(self._sada_target_angle)
        d["_ov_threshold"] = float(self._ov_threshold)
        d["_ov_threshold_nominal"] = (None if self._ov_threshold_nominal is None
                                      else float(self._ov_threshold_nominal))
        return d

    def set_state(self, state: dict[str, Any]) -> None:
        # Container fields are deep-copied by restore_dataclass_state: the
        # plain setattr this used to do aliased the snapshot's own dicts and
        # lists into live model state (G2-f).
        from smo_common.state import restore_dataclass_state, restore_rng
        restore_rng(self.rng, state.get("_rng_state"))
        if "_sada_stuck" in state:
            self._sada_stuck = bool(state["_sada_stuck"])
            self._sada_tracking = bool(state.get("_sada_tracking",
                                                 self._sada_tracking))
            self._sada_target_angle = float(state.get("_sada_target_angle",
                                                      self._sada_target_angle))
        if "_ov_threshold" in state:
            self._ov_threshold = float(state["_ov_threshold"])
            nom = state.get("_ov_threshold_nominal")
            self._ov_threshold_nominal = None if nom is None else float(nom)
        restore_dataclass_state(self._state, state,
                                skip=self._EXTRA_STATE_KEYS)

    def set_bat_ambient_temp(self, temp_c: float) -> None:
        self._bat_temp_env = temp_c

    def _get_time(self) -> float:
        """MET in seconds — model-INTERNAL time only.

        It used to return ``sim_time.timestamp()``, a Unix epoch value. This
        is not a packet timestamp and never reaches the wire: its only use is
        the third element of the ``_model_event_queue`` tuple, which the
        engine discards (the S5 packet is stamped with onboard CUC by the TM
        builder). So it is expressed as MET — seconds since t0_epoch, taken
        from the engine's single definition (spec Part C.7). Standalone (no
        engine) it is 0.0; a wall clock here would break determinism.
        """
        eng = getattr(self, "_engine", None)
        met = getattr(eng, "met_s", None)
        return float(met) if met is not None else 0.0

    def _switch_load(self, load_id: int, state: bool) -> dict[str, Any]:
        """Switch individual load/power line on/off."""
        if load_id < 0 or load_id >= len(POWER_LINE_NAMES):
            return {"success": False, "message": f"Invalid load ID: {load_id}", "error_code": 0x0005}
        line_name = POWER_LINE_NAMES[load_id]
        if not POWER_LINE_SWITCHABLE.get(line_name, False):
            return {"success": False, "message": f"Load {line_name} is not switchable", "error_code": 0x0006}
        self._state.power_lines[line_name] = state
        return {"success": True, "message": f"Load {line_name} switched {'ON' if state else 'OFF'}"}

    def _emergency_load_shed(self, stage: int) -> dict[str, Any]:
        """Immediately shed loads to the commanded stage (0-3).

        2026-08c item 8, two defects fixed here:

        * it used to write ``power_lines[load] = not should_shed`` — i.e. a
          SHED command POWERED ON every load beyond the commanded stage, so
          "shed stage 1" could silently energise the FPA cooler, the wheels
          and the prop line. Shedding is now monotonic: it only ever turns
          lines OFF. Restoration is the operator's job, line by line (same
          contract as EPS_SET_MODE safing).
        * the commanded stage was not latched, so the next tick recomputed
          ``load_shed_stage`` from bus voltage alone and published 0 — the
          command produced no stage telemetry and no stage event.

        Stage N sheds the first N lines of the shed order
        (payload, fpa_cooler, ttc_tx); stage 0 clears the commanded latch
        without re-powering anything. Side effects match the autonomous
        bus-voltage shedder so a shed payload really stops imaging and a
        shed ttc_tx really silences the transmitter.
        """
        if stage < 0 or stage > 3:
            return {"success": False, "message": "Invalid load shed stage (0-3)"}
        s = self._state
        newly_shed: list[str] = []
        already_off: list[str] = []
        for load_name in self._shed_order[:stage]:
            if s.power_lines.get(load_name, False):
                s.power_lines[load_name] = False
                newly_shed.append(load_name)
                if load_name == "payload":
                    s.payload_mode = 0
                elif load_name == "fpa_cooler":
                    s.fpa_cooler_on = False
                elif load_name == "ttc_tx":
                    s.transponder_tx = False
            else:
                already_off.append(load_name)
        s.commanded_shed_stage = stage
        s.load_shed_stage = max(s.load_shed_stage, stage)
        if stage == 0:
            return {"success": True,
                    "message": ("Load shed stage 0: commanded-shed latch "
                                "cleared — no lines re-powered (restore "
                                "lines individually with EPS_POWER_ON)")}
        detail = f"shed {', '.join(newly_shed) if newly_shed else 'nothing new'}"
        if already_off:
            detail += f"; already off: {', '.join(already_off)}"
        return {"success": True,
                "message": f"Emergency load shed stage {stage} ({detail})"}

    def _get_power_budget_summary(self) -> dict[str, Any]:
        """Return current power generation vs consumption summary."""
        gen_w = self._state.power_gen_w
        cons_w = self._state.power_cons_w
        margin_w = gen_w - cons_w
        return {
            "success": True,
            "power_gen_w": gen_w,
            "power_cons_w": cons_w,
            "power_margin_w": margin_w,
            "power_margin_pct": (margin_w / max(gen_w, 1.0)) * 100.0,
            "bus_voltage_v": self._state.bus_voltage,
            "bat_soc_pct": self._state.bat_soc_pct,
        }

    # S2 Device Access — device-level on/off control
    def set_device_state(self, device_id: int, on_off: bool) -> bool:
        """Set device on/off state. Returns True if successful."""
        if device_id not in self._state.device_states:
            return False
        self._state.device_states[device_id] = on_off
        return True

    def get_device_state(self, device_id: int) -> bool:
        """Get device on/off state. Returns True if on, False if off or invalid."""
        return self._state.device_states.get(device_id, False)

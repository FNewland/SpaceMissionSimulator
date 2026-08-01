"""SMO Simulator — AOCS with real attitude dynamics (Phase C rebuild).

Rigid-body truth dynamics (RK4, fixed h = 0.5 s, sub-stepped inside the
model tick), pyramid reaction wheels with torque limits / motor lag /
friction, magnetorquer physics, attitude-dependent sensor models, a 6-state
MEKF, quaternion-feedback PD control with orbit-rate feed-forward, LVLH /
inertial / sun / target reference generation (new mode 9 = INERTIAL),
gravity-gradient + aero + SRP + residual-dipole disturbance torques, and a
declared SECULAR fast-forward mode above 60x time acceleration.

Retains the original 9-mode state machine + guards, dual star-tracker /
CSS / magnetometer health & redundancy FSMs, GPS TTFF model, S2 device
gates, failure injection and the 0x0217 ``att_error`` semantics (total
attitude pointing error, degrees — see brief 02 §Risks).
"""
import logging
import math
import random
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from smo_common.models.subsystem import SubsystemModel
from smo_common import attitude as att
from smo_simulator.models.aocs_estimator import MEKF6

logger = logging.getLogger(__name__)

_DEG = math.pi / 180.0
_RAD = 180.0 / math.pi
_RPM2RAD = 2.0 * math.pi / 60.0


@dataclass
class AOCSState:
    # Attitude quaternion [x, y, z, w]
    q: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    rate_roll: float = 0.0
    rate_pitch: float = 0.0
    rate_yaw: float = 0.0
    att_error: float = 0.0

    # Mode state machine
    mode: int = 0  # start OFF — AOCS unpowered at construction. See defects/reviews/aocs.md
    submode: int = 0
    time_in_mode: float = 0.0
    # Guard accumulators (seconds that guard condition has been met)
    _guard_timer: float = 0.0

    # Reaction wheels (4 wheels in tetrahedron config)
    rw_speed: list[float] = field(default_factory=lambda: [1200.0] * 4)
    rw_temp: list[float] = field(default_factory=lambda: [28.0] * 4)
    rw_enabled: list[bool] = field(default_factory=lambda: [True] * 4)
    rw_current: list[float] = field(default_factory=lambda: [0.1] * 4)
    active_wheels: list[bool] = field(default_factory=lambda: [True] * 4)

    # Magnetometer (composite — fed by active mag source)
    mag_x: float = 25000.0
    mag_y: float = 10000.0
    mag_z: float = -40000.0
    mag_valid: bool = True

    # Dual magnetometer
    mag_a_x: float = 0.0
    mag_a_y: float = 0.0
    mag_a_z: float = 0.0
    mag_b_x: float = 0.0
    mag_b_y: float = 0.0
    mag_b_z: float = 0.0
    mag_select: str = 'A'  # Which mag is active source: 'A' or 'B'
    mag_a_bias: tuple = (0.0, 0.0, 0.0)
    mag_b_bias: tuple = (0.0, 0.0, 0.0)
    mag_a_noise: float = 50.0   # σ in nT (primary)
    mag_b_noise: float = 75.0   # σ in nT (backup, slightly noisier)
    mag_a_failed: bool = False
    mag_b_failed: bool = False

    # Star trackers (2 units, cold redundant)
    # Star trackers start OFF at power-up. They're powered on by the
    # commissioning procedure (S8 func_id 4/ST1_POWER) — there should be no
    # ST alarms in bootloader/initial-power phases because the cameras are
    # simply not energised. Operators power them on explicitly once AOCS
    # checkout begins.
    st1_status: int = 0  # 0=OFF, 1=BOOTING, 2=TRACKING, 3=BLIND, 4=FAILED
    st1_num_stars: int = 12
    st2_status: int = 0  # backup starts OFF
    st2_num_stars: int = 0
    st_selected: int = 1  # which ST is primary (1 or 2)
    st1_boot_timer: float = 0.0
    st2_boot_timer: float = 0.0
    st_valid: bool = True  # composite: selected tracker is TRACKING

    # Coarse Sun Sensor (CSS) — composite sun vector
    css_sun_x: float = 0.0
    css_sun_y: float = 0.0
    css_sun_z: float = 1.0
    css_valid: bool = True

    # Individual CSS heads (one per body face)
    css_heads: dict = field(default_factory=lambda: {
        'px': 0.0, 'mx': 0.0, 'py': 0.0, 'my': 0.0, 'pz': 0.0, 'mz': 0.0
    })
    css_head_failed: dict = field(default_factory=lambda: {
        'px': False, 'mx': False, 'py': False, 'my': False, 'pz': False, 'mz': False
    })

    # Magnetorquers (3-axis)
    mtq_x_duty: float = 0.0
    mtq_y_duty: float = 0.0
    mtq_z_duty: float = 0.0
    mtq_enabled: bool = True

    # Total system angular momentum (Nms)
    total_momentum: float = 0.0

    # Hardware failure flags
    st1_failed: bool = False
    st2_failed: bool = False
    css_failed: bool = False
    mag_failed: bool = False
    mtq_x_failed: bool = False
    mtq_y_failed: bool = False
    mtq_z_failed: bool = False

    # ── Phase 4: Flight hardware realism ──
    # Gyroscope bias telemetry (measured bias estimate, deg/s)
    gyro_bias_x: float = 0.0
    gyro_bias_y: float = 0.0
    gyro_bias_z: float = 0.0
    gyro_temp: float = 22.0

    # GPS receiver model
    gps_fix: int = 3       # 0=no fix, 1=2D, 2=3D, 3=3D+velocity
    gps_pdop: float = 1.8  # Position dilution of precision
    gps_num_sats: int = 8  # Tracked satellites
    gps_start_mode: int = 0  # 0=COLD, 1=WARM, 2=HOT. See defects/reviews/aocs.md Defect #3.
    gps_ttff_timer: float = 0.0  # Time-to-first-fix tracker (seconds)

    # TLE (Two-Line Element) state — defects/reviews/aocs.md Defect #2
    tle_valid: bool = False
    tle_last_upload_time: float = 0.0  # Epoch time of last valid TLE upload
    tle_validity_timer: float = 0.0  # Seconds since upload (ECSS limit: 30 days typical)
    tle_line1: str = ""
    tle_line2: str = ""

    # Magnetometer field magnitude (nT)
    mag_field_total: float = 50000.0

    # Slew state (0=IDLE, 1=SLEWING, 2=SETTLING, 3=COMPLETE)
    slew_state: int = 0

    # ── Per-axis control-torque gains (X, Y, Z body axes) ──
    # Operator-tunable multipliers applied to the commanded control torque in
    # each body axis. Unity (1.0) reproduces the legacy ungained behaviour; a
    # higher value makes that axis respond more strongly, a lower value weaker.
    # Loaded/dumped via PUS S6 (memory management) at the AOCS gain register
    # block (base 0x20100000; X/Y/Z at +0/+4/+8 as IEEE-754 big-endian
    # float32). Persisted across breakpoints via get_state/set_state.
    torque_gain_x: float = 1.0
    torque_gain_y: float = 1.0
    torque_gain_z: float = 1.0

    # ── Phase C: truth dynamics, estimator, reference, actuators ──
    # TRUE attitude quaternion [x,y,z,w] (ECI->body) and body rates [rad/s].
    q_true: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    omega_true: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    # Per-wheel angular momentum [N·m·s] and actual motor torque state [N·m]
    h_wheel: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    tau_wheel: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    # Reference attitude / body-frame reference rate [rad/s]
    q_ref: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    omega_ref: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    # Pointing frame: 0=INERTIAL, 1=LVLH_NADIR, 2=TARGET_TRACK, 3=SUN
    pointing_frame: int = 1
    target_q_cmd: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    target_geo: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    target_geo_valid: bool = False
    # Slew machinery (moved off the model object — WP-0: breakpoint-complete)
    slew_target_q: list[float] | None = None
    slew_start_q: list[float] | None = None
    slew_elapsed: float | None = None
    slew_rate_dps: float = 0.5
    slew_idle_timer: float = 1.0e6   # seconds since last slew ended
    # Legacy sun-sweep phase (kept for reduced test harnesses without ECI)
    orbit_phase: float = 0.0
    # True gyro bias [deg/s] (telemetry gyro_bias_* mirrors the estimate)
    # Star-tracker slowly-varying (thermoelastic) bias, rad, per unit
    st1_lf_bias: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    st2_lf_bias: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    # Control configuration (operator-tunable)
    ctrl_omega_n: float = 0.0        # 0 -> per-mode default
    ctrl_zeta: float = 0.0           # 0 -> per-mode default
    deadband_deg: float = 0.005      # FINE control deadband
    att_error_threshold_deg: float = 5.0   # S5 event threshold (func 15 also writes)
    emergency_rate_threshold: float = 2.0
    # Momentum management (AOCS_SET_MOM_MGMT)
    mom_mgmt_enable: bool = False
    mom_mgmt_entry_pct: float = 70.0
    mom_mgmt_exit_pct: float = 20.0
    # Estimator products mirrored for telemetry
    knowledge_err_3sig_deg: float = 180.0
    control_err_deg: float = 0.0
    est_source: int = 0              # bit0 ST, bit1 CSS, bit2 MAG, bit3 gyro-only
    # FINE-entry precondition bitmask (0x02C5); 0 = ready
    fine_ready_mask: int = 0x7F
    fine_conv_timer: float = 0.0     # MEKF-converged sustain timer (30 s)
    # Fidelity: 0 = full physics, 1 = secular (accel bypass)
    fidelity_mode: int = 0
    # Rate-error history for the 60 s rate-stability metric [deg/s]
    rate_err_hist: list[float] = field(default_factory=list)
    rate_stability_3sig: float = 0.0
    settle_time_s: float = 0.0
    settled: bool = False
    # Disturbance torque diagnostics [N·m]
    tau_dist: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    # FINE-mode integral state [rad·s] — rejects the static disturbance the
    # PD alone would carry as a τ_dist/Kp ≈ 0.02-0.05° offset
    ctrl_integ: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    # Edge-detector flags (WP-0: moved into the dataclass so breakpoints
    # round-trip them; previously module-level `_prev_*`)
    prev_in_eclipse: bool = False
    prev_st1_status: int = 0
    prev_st2_status: int = 0
    prev_css_valid: bool = False
    prev_gps_fix: int = 0
    prev_momentum_saturation: bool = False
    prev_rw_overspeed: bool = False
    prev_rw_bearing_degraded: list[bool] = field(
        default_factory=lambda: [False, False, False, False])
    prev_att_error_high: bool = False
    prev_gyro_bias_high: bool = False
    prev_css_degraded: bool = False
    prev_secular: bool = False

    # S2 Device Access — device on/off states (device_id -> on/off)
    device_states: dict = field(default_factory=lambda: {
        0x0200: True,   # Reaction wheel 0
        0x0201: True,   # Reaction wheel 1
        0x0202: True,   # Reaction wheel 2
        0x0203: True,   # Reaction wheel 3
        0x0204: True,   # Star tracker 1
        0x0205: True,   # Star tracker 2
        0x0206: True,   # Gyroscope 0
        0x0207: True,   # Gyroscope 1
        0x0208: True,   # Gyroscope 2
        0x0209: True,   # Magnetometer A
        0x020A: True,   # Magnetometer B
        0x020B: True,   # Magnetorquer X
        0x020C: True,   # Magnetorquer Y
        0x020D: True,   # Magnetorquer Z
        0x020E: True,   # Sun sensor array
        0x020F: True,   # GPS receiver
    })


# Mode constants
MODE_OFF = 0
MODE_SAFE_BOOT = 1
MODE_DETUMBLE = 2
MODE_COARSE_SUN = 3
MODE_NOMINAL = 4
MODE_FINE_POINT = 5
MODE_SLEW = 6
MODE_DESAT = 7
MODE_ECLIPSE = 8
MODE_INERTIAL = 9   # Phase C WP-5 — appended, never renumbered (brief 02 §5)

_MODE_NAMES = {
    0: "OFF", 1: "SAFE_BOOT", 2: "DETUMBLE", 3: "COARSE_SUN",
    4: "NOMINAL", 5: "FINE_POINT", 6: "SLEW", 7: "DESAT", 8: "ECLIPSE",
    9: "INERTIAL",
}

# Minimum dwell times per mode (seconds) before auto-transition allowed
_MIN_DWELL = {
    MODE_OFF: 0, MODE_SAFE_BOOT: 5.0, MODE_DETUMBLE: 10.0,
    MODE_COARSE_SUN: 20.0, MODE_NOMINAL: 10.0, MODE_FINE_POINT: 10.0,
    MODE_SLEW: 0, MODE_DESAT: 10.0, MODE_ECLIPSE: 5.0, MODE_INERTIAL: 10.0,
}

# Wheel-based control modes (PD + wheel allocation active)
_WHEEL_CTRL_MODES = (MODE_NOMINAL, MODE_FINE_POINT, MODE_SLEW, MODE_DESAT,
                     MODE_ECLIPSE, MODE_INERTIAL)

# Per-mode control bandwidth (rad/s) and damping. Gains follow
# K_p = 2·I·ωn², K_d = 2·ζ·I·ωn (Wie quaternion-feedback PD).
# NOMINAL ωn re-derived for the 6U inertia so the PD steady-state error
# under the ~3e-7 N·m 6U disturbance floor stays inside the 0x0217
# imaging band (τ/Kp ≈ 0.05°) — see aocs.yaml comments.
_MODE_CTRL = {
    MODE_NOMINAL:    (0.040, 0.70),
    MODE_FINE_POINT: (0.080, 0.75),
    MODE_SLEW:       (0.060, 0.90),
    MODE_DESAT:      (0.020, 0.70),
    MODE_ECLIPSE:    (0.040, 0.70),
    MODE_INERTIAL:   (0.040, 0.70),
}

# AOCS suite electrical power by mode [W] (brief 02 COARSE/FINE table;
# billed by EPS from the published 0x02C8 aocs.power_w — Phase B convention:
# bill actuator state, not line-switch position).
_MODE_POWER_W = {
    MODE_OFF: 1.0, MODE_SAFE_BOOT: 2.5, MODE_DETUMBLE: 4.0,
    MODE_COARSE_SUN: 4.0, MODE_NOMINAL: 9.0, MODE_FINE_POINT: 15.0,
    MODE_SLEW: 12.0, MODE_DESAT: 6.0, MODE_ECLIPSE: 7.0, MODE_INERTIAL: 9.0,
}

# Secular-mode closed-loop total-pointing-error 1σ per mode [deg] — drawn
# deterministically from self.rng when the fast loop is bypassed (>60x).
_MODE_ERR_SIGMA_DEG = {
    MODE_COARSE_SUN: 1.5, MODE_NOMINAL: 0.020, MODE_FINE_POINT: 0.007,
    MODE_SLEW: 0.10, MODE_DESAT: 0.05, MODE_ECLIPSE: 0.05,
    MODE_INERTIAL: 0.020,
}

# Star tracker boot time (seconds)
_ST_BOOT_TIME = 60.0

# CSS face normals (one per body face)
CSS_NORMALS = {
    'px': (1, 0, 0), 'mx': (-1, 0, 0),
    'py': (0, 1, 0), 'my': (0, -1, 0),
    'pz': (0, 0, 1), 'mz': (0, 0, -1),
}

# Star camera exclusion cone half-angle (degrees)
_ST_EXCLUSION_DEG = 15.0


class AOCSBasicModel(SubsystemModel):
    """Enhanced AOCS with 9-mode state machine, dual star trackers,
    CSS, magnetorquers, and comprehensive failure modes."""

    def __init__(self):
        self._state = AOCSState()
        # Per-model RNG (plan A-2). Standalone construction gets a fixed
        # deterministic stream; the engine replaces this with a Random
        # derived from simulation.yaml `seed` (derive_rng).
        import random as _random
        self.rng = _random.Random(20260731)
        # TRUE gyro bias [deg/s] — the failure-injectable ground truth the
        # MEKF estimates. (Kept model-level, serialised in get_state.)
        self._gyro_bias = [0.0, 0.0, 0.0]
        self._bearing_degradation = [0.0, 0.0, 0.0, 0.0]
        # ── Spacecraft definition defaults (overridden by aocs.yaml) ──
        # 6U-class spacecraft; see configs/eosat1/subsystems/aocs.yaml for
        # the datasheet anchors (DECISIONS.md §4 resolution).
        self._mass_kg = 12.0
        self._inertia = np.diag([0.180, 0.150, 0.060])   # kg·m², +Z boresight
        self._inertia_inv = np.linalg.inv(self._inertia)
        self._rw_max_speed = 5500
        self._rw_desat_speed = 200
        self._rw_nominal_speed = 1200
        self._rw_inertia = 5.2e-5     # kg·m² rotor (RW-0.03 class: 30 mN·m·s @ 5500 rpm)
        self._rw_max_torque = 0.002   # N·m (2 mN·m class)
        self._rw_motor_tau = 0.05     # s
        self._rw_coulomb = 2.0e-6     # N·m
        self._rw_viscous = 1.0e-8     # N·m/(rad/s)
        self._mtq_max_dipole = 1.6    # A·m² per rod (NCTR-M016 class)
        self._residual_dipole = np.array([0.008, -0.005, 0.006])  # A·m²
        self._drag_area_m2 = 0.06
        self._cp_cg_offset_m = 0.03
        self._srp_area_m2 = 0.34
        self._cd = 2.2
        self._cr = 1.5
        self._rho_kgm3 = 2.0e-12
        # Pyramid wheel geometry: az 45/135/225/315°, el 30° above XY
        self._wheel_axes = self._pyramid_axes()
        self._alloc_active = (True, True, True, True)
        self._A = np.array(self._wheel_axes).T          # 3x4
        self._A_pinv = np.linalg.pinv(self._A)          # 4x3
        self._null_vec = np.array([0.5, -0.5, 0.5, -0.5])  # A·v = 0
        # Integration
        self._h_step = 0.5
        self._max_substeps = 120
        self._secular_speed_threshold = 60.0
        # Sensor spec (aocs.yaml `sensors:` overrides)
        self._gyro_arw_dps_rts = 0.001      # deg/√s (0.06 deg/√h)
        self._gyro_bias_tau_s = 1800.0
        self._gyro_bias_ss_dps = 2.8e-5     # 0.1 deg/h
        self._gyro_noise_dps = 0.0005       # per-sample white noise floor (post-filter)
        self._st_nea_cross_arcsec = 6.0
        self._st_nea_roll_arcsec = 40.0
        self._st_lf_bias_arcsec = 3.0       # 1σ, per axis, 1/e ≈ 1 orbit
        self._st_misalign_arcsec = 15.0
        self._css_head_noise = 0.02
        self._ekf = MEKF6(
            arw_dps_rthz=self._gyro_arw_dps_rts,
            bias_tau_s=self._gyro_bias_tau_s,
            bias_ss_dps=self._gyro_bias_ss_dps,
            st_nea_cross_arcsec=self._st_nea_cross_arcsec,
            st_nea_roll_arcsec=self._st_nea_roll_arcsec,
            st_bias_arcsec=math.sqrt(self._st_lf_bias_arcsec ** 2
                                     + self._st_misalign_arcsec ** 2))
        # Static ST mounting misalignment (deterministic small rotations)
        self._st_misalign_vec = [
            np.array([1.0, -0.7, 0.4]) * (self._st_misalign_arcsec / 3600.0 * _DEG),
            np.array([-0.5, 0.9, -0.3]) * (self._st_misalign_arcsec / 3600.0 * _DEG),
        ]
        self._param_ids: dict[str, int] = {}
        # Bearing health tracking (0-100%)
        self._rw_bearing_health = [100.0, 100.0, 100.0, 100.0]
        # Per-axis torque-gain values saved before a ``gain_corruption`` inject
        self._gain_corruption_saved: dict[str, float] = {}
        # GPS start mode timing (defects/reviews/aocs.md Defect #3)
        self._gps_cold_ttff_s = 60.0
        self._gps_warm_ttff_s = 30.0
        self._gps_hot_ttff_s = 5.0

    @staticmethod
    def _pyramid_axes():
        el = math.radians(30.0)
        axes = []
        for az_deg in (45.0, 135.0, 225.0, 315.0):
            az = math.radians(az_deg)
            axes.append((math.cos(el) * math.cos(az),
                         math.cos(el) * math.sin(az),
                         math.sin(el)))
        return axes

    # ── Legacy attribute shims (WP-0) ────────────────────────────────
    # Slew/target state moved into AOCSState so breakpoints round-trip it
    # wholesale; these properties keep older call sites working.

    @property
    def _slew_target_q(self):
        return self._state.slew_target_q

    @_slew_target_q.setter
    def _slew_target_q(self, v):
        self._state.slew_target_q = list(v) if v is not None else None

    @property
    def _slew_rate_dps(self):
        return self._state.slew_rate_dps

    @_slew_rate_dps.setter
    def _slew_rate_dps(self, v):
        self._state.slew_rate_dps = float(v)

    @property
    def _orbit_phase(self):
        return self._state.orbit_phase

    @_orbit_phase.setter
    def _orbit_phase(self, v):
        self._state.orbit_phase = float(v)

    @property
    def _att_error_threshold_deg(self):
        return self._state.att_error_threshold_deg

    @_att_error_threshold_deg.setter
    def _att_error_threshold_deg(self, v):
        self._state.att_error_threshold_deg = float(v)

    @property
    def _emergency_rate_threshold(self):
        return self._state.emergency_rate_threshold

    @_emergency_rate_threshold.setter
    def _emergency_rate_threshold(self, v):
        self._state.emergency_rate_threshold = float(v)

    # Edge-detector flags now live in AOCSState (WP-0); property shims keep
    # the event-generation code and older tests working unchanged.
    def _mk_prev(name):  # noqa: N805 — descriptor factory
        def _get(self):
            return getattr(self._state, name)

        def _set(self, v):
            setattr(self._state, name, v)
        return property(_get, _set)

    _prev_in_eclipse = _mk_prev("prev_in_eclipse")
    _prev_st1_status = _mk_prev("prev_st1_status")
    _prev_st2_status = _mk_prev("prev_st2_status")
    _prev_css_valid = _mk_prev("prev_css_valid")
    _prev_gps_fix = _mk_prev("prev_gps_fix")
    _prev_momentum_saturation = _mk_prev("prev_momentum_saturation")
    _prev_rw_overspeed = _mk_prev("prev_rw_overspeed")
    _prev_rw_bearing_degraded = _mk_prev("prev_rw_bearing_degraded")
    _prev_att_error_high = _mk_prev("prev_att_error_high")
    _prev_gyro_bias_high = _mk_prev("prev_gyro_bias_high")
    _prev_css_degraded = _mk_prev("prev_css_degraded")
    del _mk_prev

    @property
    def name(self) -> str:
        return "aocs"

    def configure(self, config: dict[str, Any]) -> None:
        mp = config.get("mass_properties", {})
        self._mass_kg = float(mp.get("mass_kg", self._mass_kg))
        inertia = mp.get("inertia_kgm2")
        if inertia is not None:
            self._inertia = np.array(inertia, dtype=float)
            if self._inertia.ndim == 1:
                self._inertia = np.diag(self._inertia)
            self._inertia_inv = np.linalg.inv(self._inertia)

        rw = config.get("reaction_wheels", {})
        self._rw_max_speed = rw.get("max_speed_rpm", 5500)
        self._rw_desat_speed = rw.get("desaturation_speed_rpm", 200)
        self._rw_nominal_speed = rw.get("nominal_speed_rpm", 1200)
        self._rw_inertia = rw.get("rotor_inertia_kgm2",
                                  rw.get("inertia_kgm2", 5.2e-5))
        self._rw_max_torque = rw.get("max_torque_nm", 0.002)
        self._rw_motor_tau = rw.get("motor_tau_s", 0.05)
        self._rw_coulomb = rw.get("coulomb_friction_nm", 2.0e-6)
        self._rw_viscous = rw.get("viscous_friction_nm_per_rads", 1.0e-8)
        # Null-space nominal bias: alternate wheel spin signs so four equal
        # speeds carry ZERO net body momentum (pyramid null vector [+,-,+,-]).
        nom = float(self._rw_nominal_speed)
        self._state.rw_speed = [nom, -nom, nom, -nom]
        self._state.h_wheel = [
            self._rw_inertia * v * _RPM2RAD for v in self._state.rw_speed]

        mtq = config.get("magnetorquers", {})
        self._mtq_max_dipole = float(mtq.get("max_dipole_am2", 1.6))

        dist = config.get("disturbances", {})
        rd = dist.get("residual_dipole_am2")
        if rd is not None:
            self._residual_dipole = np.array(rd, dtype=float)
        self._drag_area_m2 = float(dist.get("drag_area_m2", self._drag_area_m2))
        self._cp_cg_offset_m = float(dist.get("cp_cg_offset_m", self._cp_cg_offset_m))
        self._srp_area_m2 = float(dist.get("srp_area_m2", self._srp_area_m2))
        self._cd = float(dist.get("cd", self._cd))
        self._cr = float(dist.get("cr", self._cr))
        self._rho_kgm3 = float(dist.get("rho_kgm3", self._rho_kgm3))

        integ = config.get("integration", {})
        self._h_step = float(integ.get("h_s", 0.5))
        self._max_substeps = int(integ.get("max_substeps", 120))
        self._secular_speed_threshold = float(
            integ.get("secular_above_speed", 60.0))

        sens = config.get("sensors", {})
        gy = sens.get("gyro", {})
        self._gyro_arw_dps_rts = float(gy.get("arw_dps_rtsec", 0.001))
        self._gyro_bias_tau_s = float(gy.get("bias_tau_s", 1800.0))
        self._gyro_bias_ss_dps = float(gy.get("bias_ss_dps", 2.8e-5))
        self._gyro_noise_dps = float(gy.get("noise_dps", 0.0005))
        st = sens.get("star_tracker", {})
        self._st_nea_cross_arcsec = float(st.get("nea_cross_arcsec", 6.0))
        self._st_nea_roll_arcsec = float(st.get("nea_roll_arcsec", 40.0))
        self._st_lf_bias_arcsec = float(st.get("lf_bias_arcsec", 3.0))
        self._st_misalign_arcsec = float(st.get("misalign_arcsec", 15.0))
        self._ekf = MEKF6(
            arw_dps_rthz=self._gyro_arw_dps_rts,
            bias_tau_s=self._gyro_bias_tau_s,
            bias_ss_dps=self._gyro_bias_ss_dps,
            st_nea_cross_arcsec=self._st_nea_cross_arcsec,
            st_nea_roll_arcsec=self._st_nea_roll_arcsec,
            st_bias_arcsec=math.sqrt(self._st_lf_bias_arcsec ** 2
                                     + self._st_misalign_arcsec ** 2))

        self._emergency_rate_threshold = config.get(
            "emergency_rate_threshold_dps", 2.0
        )

        # Allow setting of slew rate and attitude thresholds from config
        self._slew_rate_dps = config.get("slew_rate_dps", 0.5)
        self._att_error_threshold_deg = config.get("attitude_error_threshold_deg", 5.0)
        self._state.deadband_deg = float(
            config.get("attitude_error_deadband_deg", 0.005))

        self._param_ids = config.get("param_ids", {
            "att_q1": 0x0200, "att_q2": 0x0201, "att_q3": 0x0202, "att_q4": 0x0203,
            "rate_roll": 0x0204, "rate_pitch": 0x0205, "rate_yaw": 0x0206,
            "rw1_speed": 0x0207, "rw2_speed": 0x0208,
            "rw3_speed": 0x0209, "rw4_speed": 0x020A,
            "mag_x": 0x020B, "mag_y": 0x020C, "mag_z": 0x020D,
            "aocs_mode": 0x020F, "gps_lat": 0x0210, "gps_lon": 0x0211,
            "gps_alt": 0x0212, "gps_vx": 0x0213, "gps_vy": 0x0214,
            "gps_vz": 0x0215, "solar_beta": 0x0216, "att_error": 0x0217,
            "rw1_temp": 0x0218, "rw2_temp": 0x0219,
            "rw3_temp": 0x021A, "rw4_temp": 0x021B,
            # New params
            "st1_status": 0x0240, "st1_num_stars": 0x0241,
            "st2_status": 0x0243,
            "css_sun_x": 0x0245, "css_sun_y": 0x0246, "css_sun_z": 0x0247,
            "css_valid": 0x0248,
            "rw1_current": 0x0250, "rw2_current": 0x0251,
            "rw3_current": 0x0252, "rw4_current": 0x0253,
            "rw1_enabled": 0x0254, "rw2_enabled": 0x0255,
            "rw3_enabled": 0x0256, "rw4_enabled": 0x0257,
            "mtq_x_duty": 0x0258, "mtq_y_duty": 0x0259, "mtq_z_duty": 0x025A,
            "total_momentum": 0x025B,
            "aocs_submode": 0x0262, "time_in_mode": 0x0264,
            # slew_state moved 0x0285 -> 0x0292: 0x0285 is declared
            # aocs.rw2_bearing_health in parameters.yaml and is downlinked in
            # HK SID 2 — the old assignment made the ground display show the
            # slew state as RW2 bearing health (brief 02 root cause 10).
            "slew_state": 0x0292,
        })

    # ─── Mode transitions ────────────────────────────────────────────

    def _set_mode(self, new_mode: int) -> None:
        """Transition to a new AOCS mode, resetting timers."""
        s = self._state
        if s.mode == new_mode:
            return
        s.mode = new_mode
        s.time_in_mode = 0.0
        s._guard_timer = 0.0
        s.submode = 0
        s.ctrl_integ = [0.0, 0.0, 0.0]

    def _check_emergency(self, s: AOCSState) -> bool:
        """Check for emergency conditions that force mode transitions."""
        rate_mag = math.sqrt(
            s.rate_roll ** 2 + s.rate_pitch ** 2 + s.rate_yaw ** 2
        )
        if rate_mag > self._emergency_rate_threshold and s.mode not in (
            MODE_OFF, MODE_DETUMBLE, MODE_SAFE_BOOT
        ):
            self._set_mode(MODE_DETUMBLE)
            return True
        return False

    def _check_auto_transitions(self, s: AOCSState, dt: float,
                                orbit_state: Any) -> None:
        """Check guard conditions for automatic mode transitions."""
        dwell = _MIN_DWELL.get(s.mode, 0)
        if s.time_in_mode < dwell:
            return

        if s.mode == MODE_SAFE_BOOT:
            # After 30s hardware init, transition to DETUMBLE
            if s.time_in_mode >= 30.0:
                self._set_mode(MODE_DETUMBLE)

        elif s.mode == MODE_DETUMBLE:
            # Guard: rates < 0.5 deg/s for 30 consecutive seconds
            rate_mag = math.sqrt(
                s.rate_roll ** 2 + s.rate_pitch ** 2 + s.rate_yaw ** 2
            )
            if rate_mag < 0.5:
                s._guard_timer += dt
            else:
                s._guard_timer = 0.0
            if s._guard_timer >= 30.0:
                self._set_mode(MODE_COARSE_SUN)

        elif s.mode == MODE_COARSE_SUN:
            # Guard: CSS valid AND att_error < 10 deg for 60s
            if s.css_valid and s.att_error < 10.0:
                s._guard_timer += dt
            else:
                s._guard_timer = 0.0
            if s._guard_timer >= 60.0 and s.st_valid:
                self._set_mode(MODE_NOMINAL)

        elif s.mode in (MODE_NOMINAL, MODE_FINE_POINT):
            # Eclipse entry -> ECLIPSE mode. Defect #3: auto-trigger on
            # in_eclipse. Phase C: FINE_POINT also yields to eclipse (root
            # cause 14 — it previously sailed through claiming ST knowledge).
            if orbit_state.in_eclipse and not self._prev_in_eclipse:
                s._guard_timer = 0.0  # Reset guard timer for eclipse debouncing
            if orbit_state.in_eclipse:
                s._guard_timer += dt
                # Debounce 5s before entering eclipse (hysteresis to avoid thrashing)
                if s._guard_timer >= 5.0:
                    self._set_mode(MODE_ECLIPSE)

        elif s.mode == MODE_ECLIPSE:
            # Eclipse exit -> return to NOMINAL if ST valid
            if not orbit_state.in_eclipse:
                if s.st_valid:
                    self._set_mode(MODE_NOMINAL)
                else:
                    self._set_mode(MODE_COARSE_SUN)

        elif s.mode == MODE_DESAT:
            # All active wheels below threshold -> NOMINAL
            if all(
                abs(s.rw_speed[i]) <= self._rw_desat_speed + 100
                for i in range(4) if s.active_wheels[i]
            ):
                s._guard_timer += dt
            else:
                s._guard_timer = 0.0
            if s._guard_timer >= 10.0:
                self._set_mode(MODE_NOMINAL)

    # ─── Sensor models ───────────────────────────────────────────────

    def _tick_star_trackers(self, s: AOCSState, dt: float, orbit_state: Any) -> None:
        """Model dual star trackers with boot time and blinding."""
        for unit in (1, 2):
            status_attr = f"st{unit}_status"
            stars_attr = f"st{unit}_num_stars"
            boot_attr = f"st{unit}_boot_timer"
            failed_attr = f"st{unit}_failed"

            status = getattr(s, status_attr)
            failed = getattr(s, failed_attr)

            if failed:
                setattr(s, status_attr, 4)  # FAILED
                setattr(s, stars_attr, 0)
                continue

            if status == 0:  # OFF
                setattr(s, stars_attr, 0)
                continue

            if status == 1:  # BOOTING
                timer = getattr(s, boot_attr) + dt
                setattr(s, boot_attr, timer)
                setattr(s, stars_attr, 0)
                if timer >= _ST_BOOT_TIME:
                    setattr(s, status_attr, 2)  # -> TRACKING
                    setattr(s, boot_attr, 0.0)
                continue

            # status 2 (TRACKING) or 3 (BLIND) — check blinding
            # FOV geometry (Phase C, consistent with the LVLH airframe where
            # +Z is the nadir payload boresight — manual §2.1.3): ST1 zenith
            # on -Z, ST2 nadir on +Z. The old assignment was inverted.
            blinded = False
            if not orbit_state.in_eclipse:
                sun_body = self._get_sun_body_vector(orbit_state)
                if unit == 1:
                    # ST1 zenith (-Z boresight)
                    blinded = self._check_st_blinding(sun_body, (0, 0, -1))
                else:
                    # ST2 nadir (+Z boresight) — also susceptible to earth limb
                    blinded = self._check_st_blinding(sun_body, (0, 0, 1))

            if blinded:
                setattr(s, status_attr, 3)  # BLIND
                setattr(s, stars_attr, 0)
            else:
                setattr(s, status_attr, 2)  # TRACKING
                num = self.rng.randint(8, 20)
                setattr(s, stars_attr, num)

        # Composite validity: selected tracker is TRACKING
        sel = s.st_selected
        sel_status = getattr(s, f"st{sel}_status")
        s.st_valid = (sel_status == 2)

    def _check_st_blinding(self, sun_body, st_boresight):
        """Check if sun is within exclusion cone of star camera boresight."""
        cos_angle = sum(b * sb for b, sb in zip(st_boresight, sun_body))
        cos_angle = max(-1.0, min(1.0, cos_angle))
        sun_angle_deg = math.degrees(math.acos(cos_angle))
        return sun_angle_deg < _ST_EXCLUSION_DEG

    # ─── Environment (Phase C WP-3) ──────────────────────────────────

    def _environment(self, orbit_state) -> dict:
        """True environment vectors for this tick.

        Uses the propagator's ECI state when available; otherwise
        synthesises a deterministic circular orbit from the legacy
        orbit-phase accumulator so reduced test harnesses (MagicMock orbit
        stubs) still exercise the full physics.
        """
        r = getattr(orbit_state, 'pos_eci', None)
        v = getattr(orbit_state, 'vel_eci', None)
        sun = getattr(orbit_state, 'sun_eci', None)
        r_eci = v_eci = sun_hat = None
        try:
            if r is not None and v is not None and sun is not None:
                r_eci = np.array([float(r[0]), float(r[1]), float(r[2])])
                v_eci = np.array([float(v[0]), float(v[1]), float(v[2])])
                sun_hat = np.array([float(sun[0]), float(sun[1]), float(sun[2])])
                # Plausibility guard: reject stub values (e.g. MagicMock
                # coerces to 1.0) — need an orbit-sized radius and a
                # non-degenerate angular momentum vector.
                if (np.linalg.norm(r_eci) < 6000.0
                        or np.linalg.norm(np.cross(r_eci, v_eci)) < 1.0):
                    r_eci = None
        except (TypeError, IndexError, ValueError):
            r_eci = None
        if r_eci is None:
            # Synthetic circular orbit in the ECI XY plane; sun elevated by
            # the solar beta angle out of the orbit plane.
            try:
                alt_km = float(getattr(orbit_state, 'alt_km', 450.0))
            except (TypeError, ValueError):
                alt_km = 450.0
            try:
                beta = math.radians(float(
                    getattr(orbit_state, 'solar_beta_deg', 20.0)))
            except (TypeError, ValueError):
                beta = math.radians(20.0)
            rmag_km = 6378.137 + alt_km
            phi = self._state.orbit_phase * _DEG
            vmag = math.sqrt(att.MU_EARTH / (rmag_km * 1000.0)) / 1000.0
            # SSO-like inclination (98°) so the geomagnetic field rotates
            # through the orbit plane — an equatorial synthetic orbit would
            # be a degenerate geometry for magnetorquer control.
            inc = math.radians(98.0)
            j_hat = np.array([0.0, math.cos(inc), math.sin(inc)])
            n_hat = np.array([0.0, -math.sin(inc), math.cos(inc)])
            x_hat = np.array([1.0, 0.0, 0.0])
            r_eci = rmag_km * (math.cos(phi) * x_hat + math.sin(phi) * j_hat)
            v_eci = vmag * (-math.sin(phi) * x_hat + math.cos(phi) * j_hat)
            sun_hat = math.cos(beta) * x_hat + math.sin(beta) * n_hat
        sun_hat = sun_hat / (np.linalg.norm(sun_hat) + 1e-12)
        r_m = r_eci * 1000.0
        try:
            lon_deg = float(getattr(orbit_state, 'lon_deg', 0.0))
        except (TypeError, ValueError):
            lon_deg = 0.0
        gmst = att.gmst_from_position(r_m, lon_deg)
        B_eci = att.igrf_dipole_eci(r_m, gmst)     # Tesla
        return {
            'r_m': r_m, 'v_ms': v_eci * 1000.0,
            'sun_hat': sun_hat, 'B_eci': B_eci, 'gmst': gmst,
            'eclipse': bool(getattr(orbit_state, 'in_eclipse', False)),
        }

    def _true_sun_body(self):
        """TRUE sun unit vector in the body frame (from q_true)."""
        env = self._env
        C = att.quat_to_dcm(np.asarray(self._state.q_true, float))
        return C @ env['sun_hat']

    def _get_sun_body_vector(self, orbit_state):
        """Sun vector in body frame — TRUE geometry (used for ST blinding
        and SRP); falls back to the CSS composite if no environment yet."""
        if getattr(self, '_env', None) is not None:
            sb = self._true_sun_body()
            return (float(sb[0]), float(sb[1]), float(sb[2]))
        s = self._state
        mag = (s.css_sun_x ** 2 + s.css_sun_y ** 2 + s.css_sun_z ** 2) ** 0.5
        if mag > 0.1:
            return (s.css_sun_x / mag, s.css_sun_y / mag, s.css_sun_z / mag)
        beta_rad = math.radians(orbit_state.solar_beta_deg)
        phase_rad = self._orbit_phase * _DEG
        return (
            math.cos(beta_rad) * math.cos(phase_rad),
            math.cos(beta_rad) * math.sin(phase_rad),
            math.sin(beta_rad),
        )

    def _tick_css(self, s: AOCSState, orbit_state: Any) -> None:
        """Model Coarse Sun Sensor — 6 individual heads + composite sun vector."""
        if s.css_failed:
            s.css_valid = False
            s.css_sun_x = 0.0
            s.css_sun_y = 0.0
            s.css_sun_z = 0.0
            for face in CSS_NORMALS:
                s.css_heads[face] = 0.0
            return

        if orbit_state.in_eclipse:
            s.css_valid = False
            s.css_sun_x = 0.0
            s.css_sun_y = 0.0
            s.css_sun_z = 0.0
            for face in CSS_NORMALS:
                s.css_heads[face] = 0.0
            return

        # TRUE sun direction in the body frame — a function of the true
        # attitude quaternion (WP-3: rotating the spacecraft now rotates the
        # sun across the CSS heads; previously this was orbit-phase only).
        sun_body = self._true_sun_body()

        # Compute per-head illumination from sun vector and face normals
        for face, normal in CSS_NORMALS.items():
            if s.css_head_failed.get(face, False):
                s.css_heads[face] = 0.0
                continue
            cos_angle = sum(n * sb for n, sb in zip(normal, sun_body))
            illumination = max(0.0, cos_angle)
            # Add noise
            illumination += self.rng.gauss(0, 0.02)
            s.css_heads[face] = max(0.0, illumination)

        # Reconstruct composite sun vector from CSS heads
        s.css_sun_x = s.css_heads['px'] - s.css_heads['mx']
        s.css_sun_y = s.css_heads['py'] - s.css_heads['my']
        s.css_sun_z = s.css_heads['pz'] - s.css_heads['mz']

        # Normalise
        mag = math.sqrt(s.css_sun_x ** 2 + s.css_sun_y ** 2 + s.css_sun_z ** 2)
        if mag > 0.01:
            s.css_sun_x /= mag
            s.css_sun_y /= mag
            s.css_sun_z /= mag
            s.css_valid = True
        else:
            s.css_valid = False

    def _true_B_body_nT(self):
        """TRUE geomagnetic field in the body frame, nanoTesla."""
        env = self._env
        C = att.quat_to_dcm(np.asarray(self._state.q_true, float))
        return (C @ env['B_eci']) * 1e9

    def _tick_magnetometer(self, s: AOCSState, orbit_state: Any = None) -> None:
        """Dual magnetometer readings: B_body = C(q_true)·B_IGRF + bias + noise.

        WP-3: the field is the tilted-dipole IGRF evaluated at the true ECI
        position and rotated through the TRUE attitude — rotating the
        spacecraft now rotates the measured field.
        """
        if s.mag_failed:
            s.mag_valid = False
            return
        # Power gate: magnetometers are part of the AOCS sensor suite, only
        # energised when AOCS is on. While OFF/SAFE_BOOT, they produce no data.
        if s.mode == MODE_OFF or s.mode == MODE_SAFE_BOOT:
            s.mag_valid = False
            s.mag_x = s.mag_y = s.mag_z = 0.0
            s.mag_a_x = s.mag_a_y = s.mag_a_z = 0.0
            s.mag_b_x = s.mag_b_y = s.mag_b_z = 0.0
            return

        true_x, true_y, true_z = (float(x) for x in self._true_B_body_nT())

        # Mag A reading
        if not s.mag_a_failed:
            s.mag_a_x = true_x + s.mag_a_bias[0] + self.rng.gauss(0, s.mag_a_noise)
            s.mag_a_y = true_y + s.mag_a_bias[1] + self.rng.gauss(0, s.mag_a_noise)
            s.mag_a_z = true_z + s.mag_a_bias[2] + self.rng.gauss(0, s.mag_a_noise)

        # Mag B reading
        if not s.mag_b_failed:
            s.mag_b_x = true_x + s.mag_b_bias[0] + self.rng.gauss(0, s.mag_b_noise)
            s.mag_b_y = true_y + s.mag_b_bias[1] + self.rng.gauss(0, s.mag_b_noise)
            s.mag_b_z = true_z + s.mag_b_bias[2] + self.rng.gauss(0, s.mag_b_noise)

        # Active mag feeds into control loop (keep existing mag_x/y/z for compat)
        if s.mag_select == 'A' and not s.mag_a_failed:
            s.mag_x, s.mag_y, s.mag_z = s.mag_a_x, s.mag_a_y, s.mag_a_z
            s.mag_valid = True
        elif s.mag_select == 'B' and not s.mag_b_failed:
            s.mag_x, s.mag_y, s.mag_z = s.mag_b_x, s.mag_b_y, s.mag_b_z
            s.mag_valid = True
        else:
            # Both failed or selected mag failed — try fallback
            if not s.mag_a_failed:
                s.mag_x, s.mag_y, s.mag_z = s.mag_a_x, s.mag_a_y, s.mag_a_z
                s.mag_valid = True
            elif not s.mag_b_failed:
                s.mag_x, s.mag_y, s.mag_z = s.mag_b_x, s.mag_b_y, s.mag_b_z
                s.mag_valid = True
            else:
                s.mag_valid = False

    def _tick_magnetorquers(self, s: AOCSState, dt: float) -> None:
        """Housekeeping for MTQ telemetry: zero duty when disabled. The
        actual dipole→torque physics runs inside the control step."""
        if not s.mtq_enabled:
            s.mtq_x_duty = 0.0
            s.mtq_y_duty = 0.0
            s.mtq_z_duty = 0.0

    # ─── Phase C physics core ────────────────────────────────────────

    def _alloc_pinv(self, active: tuple) -> np.ndarray:
        """Pseudo-inverse of the wheel distribution matrix over the active
        wheel set (cached)."""
        if active != self._alloc_active or not hasattr(self, '_A_pinv_active'):
            idx = [i for i in range(4) if active[i]]
            self._alloc_active = active
            self._alloc_idx = idx
            if idx:
                self._A_pinv_active = np.linalg.pinv(self._A[:, idx])
            else:
                self._A_pinv_active = np.zeros((0, 3))
        return self._A_pinv_active

    def _apply_wheel_torque(self, s: AOCSState, h: float,
                            tau_cmd_body: np.ndarray,
                            bias_target_rpm: list | None,
                            bias_k: float) -> np.ndarray:
        """Allocate a commanded body torque to the wheels, advance the wheel
        momentum states, and return the ACTUAL reaction torque on the body.

        Pseudo-inverse allocation over active wheels, null-space bias-speed
        regulation (4-wheel set only), per-wheel torque clamp that preserves
        the torque direction, first-order motor lag integrated exactly, and
        coulomb + viscous bearing friction (with reaction — momentum is
        exchanged with the body, never destroyed).
        """
        active = tuple(bool(a) for a in s.active_wheels)
        idx = [i for i in range(4) if active[i]]
        pinv = self._alloc_pinv(active)
        hd_cmd = np.zeros(4)
        if idx:
            hd = pinv @ (-np.asarray(tau_cmd_body, float))
            for k, i in enumerate(idx):
                hd_cmd[i] = hd[k]
        # Null-space bias regulation (keeps wheels off zero-crossing
        # stiction in NOMINAL; drives speeds to the desat target in DESAT)
        if len(idx) == 4 and bias_target_rpm is not None:
            h_t = np.array([self._rw_inertia * _RPM2RAD * bias_target_rpm[i]
                            for i in range(4)])
            hw = np.array(s.h_wheel)
            hd_cmd += self._null_vec * (
                float(self._null_vec @ (h_t - hw)) * bias_k)
        # Torque clamp preserving direction
        mx = max(abs(x) for x in hd_cmd) if len(idx) else 0.0
        if mx > self._rw_max_torque:
            hd_cmd *= self._rw_max_torque / mx
        # Motor lag (exact exponential — removes the stiff pole from RK4)
        alpha = 1.0 - math.exp(-h / max(1e-6, self._rw_motor_tau))
        h_max = self._rw_inertia * self._rw_max_speed * _RPM2RAD
        eff_hdot = np.zeros(4)
        for i in range(4):
            w_i = s.h_wheel[i] / self._rw_inertia
            degrade = 1.0 + 10.0 * self._bearing_degradation[i]
            fric = (self._rw_coulomb * degrade
                    + self._rw_viscous * abs(w_i)) * math.copysign(1.0, w_i)
            if abs(w_i) < 1e-3:
                fric = 0.0
            if active[i]:
                # The wheel driver closes a torque loop: it compensates the
                # NOMINAL bearing friction so the flywheel sees the commanded
                # ḣ. Only the nominal (healthy) friction is compensated — the
                # excess drag of a DEGRADED bearing is unmodelled by the
                # driver and torques the body (the CERISE-class signature).
                fric_nom = (self._rw_coulomb
                            + self._rw_viscous * abs(w_i)) \
                    * math.copysign(1.0, w_i)
                if abs(w_i) < 1e-3:
                    fric_nom = 0.0
                cmd = float(hd_cmd[i]) + fric_nom
                s.tau_wheel[i] += (cmd - s.tau_wheel[i]) * alpha
                motor = s.tau_wheel[i]
            else:
                s.tau_wheel[i] = 0.0
                # Disabled wheel: dynamic braking, ~5 %/s momentum decay
                motor = -s.h_wheel[i] * 0.05
            hdot = motor - fric
            new_h = s.h_wheel[i] + hdot * h
            new_h = max(-h_max, min(h_max, new_h))
            eff_hdot[i] = (new_h - s.h_wheel[i]) / h if h > 0 else 0.0
            s.h_wheel[i] = new_h
            s.rw_speed[i] = new_h / self._rw_inertia / _RPM2RAD
        # Reaction on the body (Newton's third law on every wheel)
        return -(self._A @ eff_hdot)

    def _mtq_dipole_torque(self, s: AOCSState, m_cmd: np.ndarray,
                           B_body_T: np.ndarray) -> np.ndarray:
        """Clamp/duty-account a commanded dipole and return τ = m × B."""
        if not s.mtq_enabled:
            m_cmd = np.zeros(3)
        m = np.clip(np.asarray(m_cmd, float),
                    -self._mtq_max_dipole, self._mtq_max_dipole)
        ds = s.device_states
        if s.mtq_x_failed or not ds.get(0x020B, True):
            m[0] = 0.0
        if s.mtq_y_failed or not ds.get(0x020C, True):
            m[1] = 0.0
        if s.mtq_z_failed or not ds.get(0x020D, True):
            m[2] = 0.0
        s.mtq_x_duty = float(m[0] / self._mtq_max_dipole)
        s.mtq_y_duty = float(m[1] / self._mtq_max_dipole)
        s.mtq_z_duty = float(m[2] / self._mtq_max_dipole)
        self._mtq_dipole_cmd = m
        return np.cross(m, B_body_T)

    def _disturbance_torques(self, s: AOCSState) -> np.ndarray:
        """Gravity gradient + aero + SRP + residual dipole, body frame [N·m]
        (WP-7). Also stores the diagnostic vector for 0x02BE-C0."""
        env = self._env
        C = att.quat_to_dcm(np.asarray(s.q_true, float))
        r = env['r_m']
        Rmag = float(np.linalg.norm(r))
        nadir_b = C @ (-r / Rmag)
        tau_gg = (3.0 * att.MU_EARTH / Rmag ** 3) * np.cross(
            nadir_b, self._inertia @ nadir_b)
        # Aerodynamic: cannonball with fixed cp-cg offset along +X body
        v_b = C @ env['v_ms']
        vmag = float(np.linalg.norm(v_b))
        tau_aero = np.zeros(3)
        if vmag > 1.0:
            f_aero = -0.5 * self._rho_kgm3 * vmag * self._cd \
                * self._drag_area_m2 * v_b
            tau_aero = np.cross(np.array([self._cp_cg_offset_m, 0.0, 0.0]),
                                f_aero)
        # SRP: flat plate, zero in eclipse (visible as a wheel-momentum
        # slope change at the terminator)
        tau_srp = np.zeros(3)
        if not env['eclipse']:
            s_b = C @ env['sun_hat']
            f_srp = -4.5e-6 * self._cr * self._srp_area_m2 * s_b
            tau_srp = np.cross(np.array([0.0, 0.05, 0.0]), f_srp)
        # Residual magnetic dipole
        B_b = C @ env['B_eci']
        tau_dip = np.cross(self._residual_dipole, B_b)
        # External torque (Phase D): thrust-misalignment torque published by
        # the propulsion model at 0x0B17 (set in tick(); zero when not firing)
        tau_ext = getattr(self, '_ext_torque_body', None)
        if tau_ext is None:
            tau_ext = np.zeros(3)
        tau = tau_gg + tau_aero + tau_srp + tau_dip + tau_ext
        s.tau_dist = [float(x) for x in tau]
        return tau

    def _rk4_step(self, s: AOCSState, h: float,
                  tau_body: np.ndarray) -> None:
        """RK4 on the 7-state (q, ω) with wheel-momentum gyroscopic
        coupling; quaternion renormalised each step (WP-2)."""
        Ib = self._inertia
        Iinv = self._inertia_inv
        h_b = self._A @ np.array(s.h_wheel)
        q = np.asarray(s.q_true, float)
        w = np.asarray(s.omega_true, float)

        def wdot(wv):
            return Iinv @ (tau_body - np.cross(wv, Ib @ wv + h_b))

        def qdot(qv, wv):
            return 0.5 * att.quat_mult(qv, np.array([wv[0], wv[1], wv[2], 0.0]))

        k1w = wdot(w)
        k1q = qdot(q, w)
        k2w = wdot(w + 0.5 * h * k1w)
        k2q = qdot(att.quat_normalize(q + 0.5 * h * k1q), w + 0.5 * h * k1w)
        k3w = wdot(w + 0.5 * h * k2w)
        k3q = qdot(att.quat_normalize(q + 0.5 * h * k2q), w + 0.5 * h * k2w)
        k4w = wdot(w + h * k3w)
        k4q = qdot(att.quat_normalize(q + h * k3q), w + h * k3w)
        w = w + (h / 6.0) * (k1w + 2 * k2w + 2 * k3w + k4w)
        q = att.quat_normalize(q + (h / 6.0) * (k1q + 2 * k2q + 2 * k3q + k4q))
        s.omega_true = [float(x) for x in w]
        s.q_true = [float(x) for x in q]

    def _mode_gains(self, mode: int) -> tuple[float, float]:
        wn, z = _MODE_CTRL.get(mode, (0.030, 0.70))
        s = self._state
        if s.ctrl_omega_n > 0.0:
            wn = s.ctrl_omega_n
        if s.ctrl_zeta > 0.0:
            z = s.ctrl_zeta
        return wn, z

    def _pd_torque(self, s: AOCSState, q_ref, w_ref,
                   omega_n: float, zeta: float,
                   deadband_deg: float = 0.0,
                   integral_h: float = 0.0) -> np.ndarray:
        """Quaternion-feedback PD with gyroscopic feed-forward (Wie form):
        τ = K_p·q_e,vec − K_d·(ω̂ − ω_ref) + ω_ref × (Iω̂ + h_w)
        (+ a slow anti-windup integral term in FINE, which rejects the
        quasi-static disturbance torque a pure PD carries as offset)."""
        q_est = self._est_quat()
        w_est = self._omega_est
        err = att.quat_error_vec(q_est, q_ref)
        w_rel = w_est - np.asarray(w_ref, float)
        if deadband_deg > 0.0:
            if (np.linalg.norm(err) < deadband_deg * _DEG
                    and np.linalg.norm(w_rel) < 0.002 * _DEG):
                return np.zeros(3)
        # Large-angle acquisition rate limit: saturate the proportional error
        # so the PD's terminal rate (ωn/ζ)·|err| never exceeds the slew-rate
        # cap — an unbounded PD would run the body to ~5 °/s on a 120° entry
        # and trip the 2 °/s emergency detumble.
        rate_cap = max(0.1, self._state.slew_rate_dps) * _DEG
        err_max = rate_cap * zeta / max(1e-6, omega_n)
        err_mag = float(np.linalg.norm(err))
        saturated = err_mag > err_max
        if saturated:
            err = err * (err_max / err_mag)
        Ib = self._inertia
        Kp = 2.0 * Ib * omega_n ** 2
        Kd = 2.0 * zeta * Ib * omega_n
        h_b = self._A @ np.array(s.h_wheel)
        tau = (Kp @ err - Kd @ w_rel
               + np.cross(np.asarray(w_ref, float), Ib @ w_est + h_b))
        if integral_h > 0.0:
            if saturated:
                s.ctrl_integ = [0.0, 0.0, 0.0]     # anti-windup: hold-off
            else:
                lim = 0.05 * _DEG * 8.0 / omega_n   # |Ki·integ| ≤ Kp·0.05°·...
                for i in range(3):
                    s.ctrl_integ[i] = max(-lim, min(
                        lim, s.ctrl_integ[i] + float(err[i]) * integral_h))
            Ki = Kp * (omega_n / 8.0)
            tau = tau + Ki @ np.asarray(s.ctrl_integ, float)
        gains = np.array([s.torque_gain_x, s.torque_gain_y, s.torque_gain_z])
        return tau * gains

    def _est_quat(self) -> np.ndarray:
        """Best available attitude estimate (MEKF; truth as last-resort
        bootstrap before the estimator has initialised)."""
        if self._ekf.initialised:
            return self._ekf.q
        return np.asarray(self._state.q_true, float)

    def _run_control_dynamics(self, s: AOCSState, dt: float,
                              control_fn) -> None:
        """Sub-stepped truth integration: N = ceil(dt/h) capped, control and
        disturbances recomputed each sub-step (WP-1)."""
        n = max(1, min(int(math.ceil(dt / self._h_step)), self._max_substeps))
        h = dt / n
        for _ in range(n):
            tau_body = control_fn(h)
            tau_body = tau_body + self._disturbance_torques(s)
            self._rk4_step(s, h, tau_body)

    # ── Reference generation (WP-5) ──────────────────────────────────

    def _compute_reference(self, s: AOCSState, mode: int):
        """(q_ref, ω_ref[body, rad/s]) for the active mode/pointing frame."""
        env = self._env
        frame = s.pointing_frame
        if mode == MODE_INERTIAL:
            frame = 0
        if frame == 0:
            return (np.asarray(s.target_q_cmd, float), np.zeros(3))
        if frame == 3 or mode == MODE_COARSE_SUN:
            C = att.sun_frame_dcm(env['sun_hat'], env['r_m'])
            return (att.dcm_to_quat(C), np.zeros(3))
        if frame == 2 and s.target_geo_valid:
            lat, lon = math.radians(s.target_geo[0]), math.radians(s.target_geo[1])
            r_t_ecef = (att.R_EARTH + s.target_geo[2]) * np.array([
                math.cos(lat) * math.cos(lon),
                math.cos(lat) * math.sin(lon),
                math.sin(lat)])
            gmst = env.get('gmst', 0.0)
            cg, sg = math.cos(gmst), math.sin(gmst)
            Rz = np.array([[cg, -sg, 0.0], [sg, cg, 0.0], [0.0, 0.0, 1.0]])
            r_t = Rz @ r_t_ecef
            b = r_t - env['r_m']
            bmag = np.linalg.norm(b)
            if bmag < 1.0:
                b = -env['r_m'] / np.linalg.norm(env['r_m'])
            else:
                b = b / bmag
            # z = boresight, x toward velocity projection
            x = env['v_ms'] - b * float(np.dot(env['v_ms'], b))
            xm = np.linalg.norm(x)
            x = x / xm if xm > 1e-6 else np.array([1.0, 0.0, 0.0])
            y = np.cross(b, x)
            C = np.vstack([x, y, b])
            return (att.dcm_to_quat(C), np.zeros(3))
        # Default: LVLH nadir with orbit-rate feed-forward
        C_li = att.lvlh_dcm(env['r_m'], env['v_ms'])
        q_ref = att.dcm_to_quat(C_li)
        r = env['r_m']
        w_orb_eci = np.cross(r, env['v_ms']) / float(np.dot(r, r))
        w_ref = att.quat_to_dcm(self._est_quat()) @ w_orb_eci
        return (q_ref, w_ref)

    # ── Sensors → estimator (WP-3 / WP-4) ────────────────────────────

    def _gyro_measurement(self, s: AOCSState, dt: float) -> np.ndarray:
        """Measured body rate [rad/s]: truth + true bias + ARW noise."""
        sigma_dps = (self._gyro_arw_dps_rts / math.sqrt(max(dt, 1e-3))
                     + self._gyro_noise_dps)
        return np.array([
            s.omega_true[i] + self._gyro_bias[i] * _DEG
            + self.rng.gauss(0.0, sigma_dps) * _DEG
            for i in range(3)])

    def _st_measurement(self, s: AOCSState, unit: int,
                        n_avg: int = 1) -> np.ndarray:
        """Star-tracker quaternion measurement: truth ⊗ misalignment ⊗
        LF thermoelastic bias ⊗ NEA noise (roll axis = boresight z).
        ``n_avg`` models a faster-than-tick fix rate as an averaged fix:
        NEA scales by 1/√n (the LF bias / misalignment floor does not)."""
        # LF bias Markov update (1/e time ≈ one orbit)
        tau = 5670.0
        lf = s.st1_lf_bias if unit == 1 else s.st2_lf_bias
        sig = self._st_lf_bias_arcsec / 3600.0 * _DEG
        for i in range(3):
            lf[i] += (-lf[i] / tau + self.rng.gauss(0.0, sig)
                      * math.sqrt(2.0 / tau)) * 1.0
        rt_n = math.sqrt(max(1, int(n_avg)))
        nea_c = self._st_nea_cross_arcsec / 3600.0 * _DEG / rt_n
        nea_r = self._st_nea_roll_arcsec / 3600.0 * _DEG / rt_n
        noise = np.array([self.rng.gauss(0.0, nea_c),
                          self.rng.gauss(0.0, nea_c),
                          self.rng.gauss(0.0, nea_r)])
        err = noise + np.asarray(lf, float) + self._st_misalign_vec[unit - 1]
        ang = float(np.linalg.norm(err))
        if ang > 1e-15:
            axis = err / ang
            dq = np.concatenate([axis * math.sin(ang / 2.0),
                                 [math.cos(ang / 2.0)]])
        else:
            dq = np.array([0.0, 0.0, 0.0, 1.0])
        return att.quat_normalize(
            att.quat_mult(np.asarray(s.q_true, float), dq))

    def _tick_estimator(self, s: AOCSState, dt: float, secular: bool) -> None:
        """Run the MEKF: propagate on the gyro, update from ST (NOMINAL/FINE
        family) or CSS+mag vectors (DETUMBLE/COARSE), propagate-only in
        eclipse mode. Produces knowledge error, est source and q_est."""
        omega_meas = self._gyro_measurement(s, dt)
        self._omega_meas = omega_meas
        if s.mode in (MODE_OFF, MODE_SAFE_BOOT):
            s.knowledge_err_3sig_deg = 180.0
            s.est_source = 0
            self._omega_est = np.zeros(3)
            return
        source = 0x08  # gyro
        use_st = (s.st_valid and s.mode not in (MODE_ECLIPSE, MODE_DETUMBLE,
                                                MODE_COARSE_SUN))
        # Update-then-propagate: the sensor measurements correspond to the
        # CURRENT truth state; the mode tick integrates the truth over dt
        # afterwards, so propagating after the update keeps the estimate
        # time-aligned with the end-of-tick truth (no one-tick lag offset).
        if secular:
            pass  # covariance handled by _tick_secular
        elif use_st:
            n_avg = 4 if s.mode == MODE_FINE_POINT else 1
            q_meas = self._st_measurement(s, s.st_selected, n_avg=n_avg)
            if self._ekf.update_st(q_meas, n_avg=n_avg):
                source |= 0x01
        elif s.mode in (MODE_DETUMBLE, MODE_COARSE_SUN):
            env = self._env
            q_triad = None
            if s.css_valid and s.mag_valid:
                # TRIAD from CSS + mag: the deterministic two-vector fix.
                v = np.array([s.css_sun_x, s.css_sun_y, s.css_sun_z])
                b = np.array([s.mag_x, s.mag_y, s.mag_z])
                geom = np.linalg.norm(np.cross(
                    env['sun_hat'],
                    env['B_eci'] / (np.linalg.norm(env['B_eci']) + 1e-18)))
                if geom > 0.17:      # TRIAD ill-conditioned near-collinear
                    try:
                        C = att.triad(b, v, env['B_eci'], env['sun_hat'])
                        q_triad = att.dcm_to_quat(C)
                    except (ZeroDivisionError, ValueError,
                            FloatingPointError):
                        q_triad = None
            far = (not self._ekf.initialised
                   or self._ekf.knowledge_3sigma_deg() > 5.0
                   or (q_triad is not None and att.quat_angle_deg(
                       self._ekf.q, q_triad) > 10.0))
            if q_triad is not None and far:
                # COARSE determination IS TRIAD @1 Hz (brief 02 table): snap
                # to the algebraic two-vector solution — the EKF vector
                # linearisation is invalid at large errors anyway.
                self._ekf.init_from_quat(q_triad, math.radians(1.5))
                source |= 0x06
            else:
                if s.css_valid:
                    v = np.array([s.css_sun_x, s.css_sun_y, s.css_sun_z])
                    if self._ekf.update_vector(v, env['sun_hat'],
                                               math.radians(2.0)):
                        source |= 0x02
                if s.mag_valid:
                    b = np.array([s.mag_x, s.mag_y, s.mag_z])
                    if self._ekf.update_vector(b, env['B_eci'],
                                               math.radians(2.0)):
                        source |= 0x04
        if not secular:
            self._ekf.propagate(omega_meas, dt)
        kn = self._ekf.knowledge_3sigma_deg()
        s.knowledge_err_3sig_deg = min(180.0, kn)
        s.est_source = source
        # MEKF convergence sustain timer (FINE precondition bit 2)
        if kn < 0.03:
            s.fine_conv_timer += dt
        else:
            s.fine_conv_timer = 0.0
        self._ekf.converged = s.fine_conv_timer >= 30.0
        # Bias-corrected rate estimate
        self._omega_est = omega_meas - self._ekf.b
        # Published attitude/rates are the ESTIMATE (what the FSW believes)
        s.q = [float(x) for x in self._ekf.q] if self._ekf.initialised \
            else list(s.q_true)
        s.rate_roll = float(self._omega_est[0] * _RAD)
        s.rate_pitch = float(self._omega_est[1] * _RAD)
        s.rate_yaw = float(self._omega_est[2] * _RAD)

    def _update_fine_ready(self, s: AOCSState) -> None:
        """0x02C5 FINE-entry precondition bitmask; 0 = ready (WP-6)."""
        mask = 0
        sel_stars = getattr(s, f"st{s.st_selected}_num_stars", 0)
        if not (s.st_valid and sel_stars >= 5):
            mask |= 0x01          # bit0: ST not tracking >= 5 stars
        if sum(1 for w in s.active_wheels if w) < 4:
            mask |= 0x02          # bit1: fewer than 4 wheels
        if not self._ekf.converged:
            mask |= 0x04          # bit2: MEKF not converged/sustained 30 s
        w_rel = getattr(self, '_omega_est', np.zeros(3)) \
            - np.asarray(s.omega_ref, float)
        if float(np.linalg.norm(w_rel)) * _RAD > 0.01:
            mask |= 0x08          # bit3: |ω| >= 0.01 deg/s
        if self._h_env_frac(s) >= 70.0:
            mask |= 0x10          # bit4: momentum >= 70 % of envelope
        if s.mode == MODE_ECLIPSE or (s.est_source & 0x07) == 0:
            mask |= 0x20          # bit5: propagate-only knowledge
        if s.mode == MODE_SLEW or s.slew_idle_timer < 60.0:
            mask |= 0x40          # bit6: slew not idle >= 60 s
        s.fine_ready_mask = mask

    def _h_env_frac(self, s: AOCSState) -> float:
        """Momentum envelope usage, % — fraction of the most-loaded wheel
        (per-axis-aware: any single wheel saturating ends control authority)."""
        h_max = self._rw_inertia * self._rw_max_speed * _RPM2RAD
        if h_max <= 0:
            return 0.0
        return max(abs(h) for h in s.h_wheel) / h_max * 100.0

    # ─── Per-axis control-torque gains ───────────────────────────────

    def _commanded_control_torque(
        self, axis: list[float], magnitude: float
    ) -> list[float]:
        """Return the commanded control-torque vector for the active control
        law, with the operator-set per-axis gains applied.

        The attitude control laws (NOMINAL / FINE_POINT) command a correction
        of size ``magnitude`` along the body-frame error direction ``axis``
        (a unit vector). The commanded torque on each body axis is scaled by
        that axis's gain (``torque_gain_x/y/z``) so a higher gain produces a
        stronger response and a lower gain a weaker one. With the unity
        defaults this returns ``[axis[i] * magnitude]`` — identical to the
        pre-gain behaviour.

        Args:
            axis: 3-element body-frame error-axis unit vector [x, y, z].
            magnitude: scalar control effort (e.g. min(kp*err, cap)).

        Returns:
            3-element commanded torque vector [Tx, Ty, Tz].
        """
        s = self._state
        gains = (s.torque_gain_x, s.torque_gain_y, s.torque_gain_z)
        return [axis[i] * magnitude * gains[i] for i in range(3)]

    def set_torque_gain(self, axis: str, value: float) -> None:
        """Set the control-torque gain for body axis 'x', 'y' or 'z'."""
        attr = f"torque_gain_{axis.lower()}"
        if not hasattr(self._state, attr):
            raise ValueError(f"Invalid torque-gain axis: {axis!r}")
        setattr(self._state, attr, float(value))

    def get_torque_gain(self, axis: str) -> float:
        """Get the control-torque gain for body axis 'x', 'y' or 'z'."""
        attr = f"torque_gain_{axis.lower()}"
        if not hasattr(self._state, attr):
            raise ValueError(f"Invalid torque-gain axis: {axis!r}")
        return float(getattr(self._state, attr))

    # ─── Mode tick functions ─────────────────────────────────────────

    def _free_dynamics(self, s: AOCSState, dt: float) -> None:
        """Uncontrolled truth propagation (OFF / SAFE_BOOT)."""
        def control_fn(h):
            return self._apply_wheel_torque(s, h, np.zeros(3), None, 0.0)
        self._run_control_dynamics(s, dt, control_fn)

    def _tick_off(self, s: AOCSState, dt: float) -> None:
        """OFF/IDLE — no control; the body tumbles freely under
        disturbances, wheels coast down on friction."""
        self._free_dynamics(s, dt)
        s.att_error = 180.0  # Unknown attitude (no sensors powered)

    def _tick_safe_boot(self, s: AOCSState, dt: float) -> None:
        """SAFE_BOOT — hardware initialisation, no actuation yet."""
        self._free_dynamics(s, dt)
        s.att_error = 90.0 + self.rng.gauss(0, 2.0)
        s.submode = min(int(s.time_in_mode / 10.0), 2)  # 0=init, 1=mag_cal, 2=ready

    def _tick_detumble(self, s: AOCSState, dt: float) -> None:
        """DETUMBLE — B-dot rate damping through the magnetorquer dipoles.

        m = −k·(ω̂ × B̂), τ = m × B. Physically actuated: the MTQ torque
        enters the rigid-body integration, so damping capability follows the
        actual rod dipole and local field (and fails honestly when the rods
        or the magnetometer are lost).
        """
        def control_fn(h):
            tau_w = self._apply_wheel_torque(s, h, np.zeros(3), None, 0.0)
            B_b = att.quat_to_dcm(np.asarray(s.q_true, float)) @ self._env['B_eci']
            Bmag = float(np.linalg.norm(B_b))
            m = np.zeros(3)
            if s.mag_valid and s.mtq_enabled and Bmag > 1e-9:
                # B-dot from the magnetometer DERIVATIVE (self-contained, no
                # gyro in the loop — the classic property of B-dot):
                # Ḃ_body ≈ −ω×B ⇒ m = −k·Ḃ = +k·(ω×B̂); τ = −k|B|·ω_perp.
                # ω enters through the measured field derivative, i.e. the
                # true rate plus differentiation noise.
                w = (np.asarray(s.omega_true, float)
                     + np.array([self.rng.gauss(0.0, 0.003 * _DEG)
                                 for _ in range(3)]))
                m = 4000.0 * np.cross(w, B_b / Bmag)
                mmax = max(abs(float(x)) for x in m)
                if mmax > self._mtq_max_dipole:
                    m *= self._mtq_max_dipole / mmax
            return tau_w + self._mtq_dipole_torque(s, m, B_b)
        self._run_control_dynamics(s, dt, control_fn)
        # 0x0217 semantics preserved: in DETUMBLE the attitude is not yet
        # determined; att_error remains the historical rate-magnitude proxy.
        s.att_error = math.sqrt(
            s.rate_roll ** 2 + s.rate_pitch ** 2 + s.rate_yaw ** 2
        ) * 10.0

    def _tick_coarse_sun(self, s: AOCSState, dt: float) -> None:
        """COARSE_SUN — CSS + magnetometer knowledge (TRIAD path into the
        MEKF), sun pointing.

        With >= 3 healthy wheels the wheels do the pointing at a low
        bandwidth (the flight-typical 6U sun-safe configuration — a 1.6 A·m²
        rod set cannot precess a 0.15 kg·m² body to a sun hold in
        operational time); the magnetorquers damp rates and manage momentum.
        With fewer wheels it degrades to the slow magnetic-only crawl.
        """
        q_ref, w_ref = self._compute_reference(s, MODE_COARSE_SUN)
        s.q_ref = [float(x) for x in q_ref]
        s.omega_ref = [float(x) for x in w_ref]
        wheels_ok = sum(1 for w in s.active_wheels if w) >= 3
        nom = float(self._rw_nominal_speed)
        bias = [nom, -nom, nom, -nom]

        def control_fn(h):
            B_b = att.quat_to_dcm(np.asarray(s.q_true, float)) @ self._env['B_eci']
            Bmag = float(np.linalg.norm(B_b))
            if wheels_ok:
                tau_cmd = self._pd_torque(s, q_ref, w_ref, 0.010, 0.9)
                tau_w = self._apply_wheel_torque(s, h, tau_cmd, bias,
                                                 1.0 / 300.0)
                m = np.zeros(3)
                if s.mtq_enabled and Bmag > 1e-9:
                    # rate damping + momentum dump assist
                    m = 800.0 * np.cross(self._omega_est, B_b / Bmag)
                    h_b = self._A @ np.array(s.h_wheel)
                    m += np.cross(h_b, B_b / (Bmag * Bmag))
                    mmax = max(abs(float(x)) for x in m)
                    if mmax > self._mtq_max_dipole:
                        m *= self._mtq_max_dipole / mmax
                return tau_w + self._mtq_dipole_torque(s, m, B_b)
            tau_w = self._apply_wheel_torque(s, h, np.zeros(3), None, 0.0)
            m = np.zeros(3)
            if s.mtq_enabled and Bmag > 1e-9:
                # Magnetic-only: realise the achievable component ⊥ B
                tau_des = self._pd_torque(s, q_ref, w_ref, 0.005, 1.0)
                m = np.cross(B_b, tau_des) / (Bmag * Bmag) * 25.0
                m += 800.0 * np.cross(self._omega_est, B_b / Bmag)
                mmax = max(abs(float(x)) for x in m)
                if mmax > self._mtq_max_dipole:
                    m *= self._mtq_max_dipole / mmax
            return tau_w + self._mtq_dipole_torque(s, m, B_b)
        self._run_control_dynamics(s, dt, control_fn)
        # att_error = true sun-pointing error (angle sun-to-+X axis)
        sb = self._true_sun_body()
        if self._env['eclipse']:
            s.att_error = max(5.0, s.att_error)
        else:
            s.att_error = math.degrees(
                math.acos(max(-1.0, min(1.0, float(sb[0])))))

    def _tick_wheel_pointing(self, s: AOCSState, dt: float, mode: int,
                             bias_rpm: float | None = None,
                             bias_k: float = 1.0 / 300.0,
                             mtq_fn=None,
                             deadband_deg: float = 0.0,
                             integral: bool = False) -> None:
        """Shared wheel-based pointing tick: reference, PD, allocation,
        integration, att_error = TRUE total pointing error (0x0217)."""
        q_ref, w_ref = self._compute_reference(s, mode)
        s.q_ref = [float(x) for x in q_ref]
        s.omega_ref = [float(x) for x in w_ref]
        wn, zeta = self._mode_gains(mode)
        nom = float(self._rw_nominal_speed if bias_rpm is None else bias_rpm)
        bias = [nom, -nom, nom, -nom]

        def control_fn(h):
            tau_cmd = self._pd_torque(s, q_ref, w_ref, wn, zeta,
                                      deadband_deg=deadband_deg,
                                      integral_h=h if integral else 0.0)
            tau_w = self._apply_wheel_torque(s, h, tau_cmd, bias, bias_k)
            B_b = att.quat_to_dcm(np.asarray(s.q_true, float)) @ self._env['B_eci']
            m = mtq_fn(B_b) if mtq_fn is not None else np.zeros(3)
            return tau_w + self._mtq_dipole_torque(s, m, B_b)
        self._run_control_dynamics(s, dt, control_fn)
        s.att_error = att.quat_angle_deg(s.q_true, q_ref)

    def _tick_nominal(self, s: AOCSState, dt: float) -> None:
        """NOMINAL — LVLH nadir tracking (ST + gyro MEKF, 3+ wheels)."""
        self._tick_wheel_pointing(s, dt, MODE_NOMINAL)

    def _tick_inertial(self, s: AOCSState, dt: float) -> None:
        """INERTIAL (mode 9) — hold the commanded ECI attitude. LVLH-fixed
        disturbances rotate through the body at orbit rate, so wheel momentum
        accumulates secularly and desats come 2-3x more often than NADIR."""
        self._tick_wheel_pointing(s, dt, MODE_INERTIAL)

    def _tick_fine_point(self, s: AOCSState, dt: float) -> None:
        """FINE_POINT — ST @4 Hz, both heads, all 4 wheels, tight PD with a
        control deadband."""
        if not s.st_valid:
            self._set_mode(MODE_NOMINAL)
            return
        n_active = sum(1 for w in s.active_wheels if w)
        if n_active < 4:
            self._set_mode(MODE_NOMINAL)
            return
        self._tick_wheel_pointing(s, dt, MODE_FINE_POINT,
                                  deadband_deg=s.deadband_deg,
                                  integral=True)

    def _tick_slew(self, s: AOCSState, dt: float) -> None:
        """SLEW — track a rate-limited SLERP reference trajectory from the
        start attitude to the commanded target with the FINE-bandwidth PD."""
        if s.slew_target_q is None:
            self._set_mode(MODE_NOMINAL)
            return
        if s.slew_start_q is None:
            s.slew_start_q = list(s.q_true)
        if s.slew_elapsed is None:
            s.slew_elapsed = 0.0
        s.slew_elapsed += dt

        q0 = np.asarray(s.slew_start_q, float)
        qt = np.asarray(s.slew_target_q, float)
        total_angle = att.quat_angle_deg(q0, qt)
        rate = max(0.01, s.slew_rate_dps)
        duration = total_angle / rate
        progress = 1.0 if duration <= 0 else min(1.0, s.slew_elapsed / duration)
        q_ref = att.quat_slerp(q0, qt, progress)
        # Reference rate: slew axis at the commanded rate while moving
        if progress < 1.0:
            axis = att.quat_error_vec(q0, qt)
            am = np.linalg.norm(axis)
            axis = axis / am if am > 1e-9 else np.zeros(3)
            w_ref = axis * rate * _DEG
        else:
            w_ref = np.zeros(3)
        s.q_ref = [float(x) for x in q_ref]
        s.omega_ref = [float(x) for x in w_ref]
        wn, zeta = self._mode_gains(MODE_SLEW)
        nom = float(self._rw_nominal_speed)
        bias = [nom, -nom, nom, -nom]

        def control_fn(h):
            tau_cmd = self._pd_torque(s, q_ref, w_ref, wn, zeta)
            tau_w = self._apply_wheel_torque(s, h, tau_cmd, bias, 0.0)
            B_b = att.quat_to_dcm(np.asarray(s.q_true, float)) @ self._env['B_eci']
            return tau_w + self._mtq_dipole_torque(s, np.zeros(3), B_b)
        self._run_control_dynamics(s, dt, control_fn)

        err = att.quat_angle_deg(s.q_true, qt)
        s.att_error = err
        # Complete when the reference has arrived and the body has settled
        # (or on a generous timeout — 2x duration + settling allowance).
        if (progress >= 1.0 and err < 0.2) or (
                s.slew_elapsed > duration * 2.0 + 120.0):
            self._set_mode(MODE_NOMINAL)
            s.slew_target_q = None
            s.slew_start_q = None
            s.slew_elapsed = None
            s.slew_idle_timer = 0.0

    def _tick_desat(self, s: AOCSState, dt: float) -> None:
        """DESAT — wheels HOLD the attitude at mode entry while the
        magnetorquers dump system momentum (cross-product law
        m = k·(h×B)/|B|²) and the wheel bias speeds relax to the desat
        target through the null space. Holding the entry attitude (not the
        moving LVLH frame) keeps the dump from fighting a tracking
        manoeuvre."""
        if s.time_in_mode <= dt or getattr(self, '_desat_hold_q', None) is None:
            self._desat_hold_q = [float(x) for x in self._est_quat()]
        hold_q = np.asarray(self._desat_hold_q, float)
        s.q_ref = [float(x) for x in hold_q]
        s.omega_ref = [0.0, 0.0, 0.0]
        wn, zeta = self._mode_gains(MODE_DESAT)
        tgt = float(self._rw_desat_speed)
        bias = [tgt, -tgt, tgt, -tgt]

        def control_fn(h):
            B_b = att.quat_to_dcm(np.asarray(s.q_true, float)) @ self._env['B_eci']
            Bsq = float(np.dot(B_b, B_b))
            m = np.zeros(3)
            if Bsq > 1e-18 and s.mtq_enabled:
                h_b = self._A @ np.array(s.h_wheel)
                m = 1.0 * np.cross(h_b, B_b / Bsq)
                # Uniform (direction-preserving) scaling into the rod
                # envelope: per-axis clipping would distort the dump
                # direction and churn momentum between wheels instead of
                # removing it.
                mmax = max(abs(float(x)) for x in m)
                if mmax > self._mtq_max_dipole:
                    m *= self._mtq_max_dipole / mmax
            tau_mtq = self._mtq_dipole_torque(s, m, B_b)
            # PD hold + feed-forward of the (known) commanded MTQ torque so
            # the dump does not drag the attitude off the hold point.
            tau_cmd = self._pd_torque(s, hold_q, np.zeros(3), wn, zeta) \
                - tau_mtq
            tau_w = self._apply_wheel_torque(s, h, tau_cmd, bias, 1.0 / 100.0)
            return tau_w + tau_mtq
        self._run_control_dynamics(s, dt, control_fn)
        s.att_error = att.quat_angle_deg(s.q_true, hold_q)

    def _tick_eclipse(self, s: AOCSState, dt: float) -> None:
        """ECLIPSE — nadir hold on gyro-only knowledge (no ST updates), so
        the knowledge error — and with it the true pointing error — grows
        with the gyro ARW/bias random walk."""
        self._tick_wheel_pointing(s, dt, MODE_ECLIPSE)

    # ── Secular fast-forward (>60x — WP-1) ───────────────────────────

    def _tick_secular(self, s: AOCSState, dt: float, mode: int) -> None:
        """Fast loop bypassed: the truth attitude is placed on the reference
        plus a deterministic draw from the mode's closed-loop error
        distribution; the SLOW states (wheel momentum from ∫τ_dist, gyro
        bias random walk, MEKF covariance steady state) integrate exactly
        over the full dt. Honest and visible: 0x02C6 reads 1 and
        AOCS_FAST_LOOP_BYPASSED (0x020D) fires at entry/exit."""
        if mode in (MODE_OFF, MODE_SAFE_BOOT):
            # Uncontrolled: slow tumble persists; nothing else to do fast.
            if mode == MODE_OFF:
                s.att_error = 180.0
            else:
                s.att_error = 90.0 + self.rng.gauss(0, 2.0)
            return
        if mode == MODE_DETUMBLE:
            # Exponential closed-form B-dot decay (τ ≈ I/(m_max·B) ≈ 150 s)
            decay = math.exp(-dt / 150.0)
            s.omega_true = [w * decay for w in s.omega_true]
            s.rate_roll *= decay
            s.rate_pitch *= decay
            s.rate_yaw *= decay
            s.att_error = math.sqrt(
                s.rate_roll ** 2 + s.rate_pitch ** 2 + s.rate_yaw ** 2) * 10.0
            return
        # Advance any active slew reference by dt
        if mode == MODE_SLEW and s.slew_target_q is not None:
            if s.slew_elapsed is None:
                s.slew_elapsed = 0.0
            if s.slew_start_q is None:
                s.slew_start_q = list(s.q_true)
            s.slew_elapsed += dt
            q0 = np.asarray(s.slew_start_q, float)
            qt = np.asarray(s.slew_target_q, float)
            total = att.quat_angle_deg(q0, qt)
            duration = total / max(0.01, s.slew_rate_dps)
            progress = 1.0 if duration <= 0 else min(1.0, s.slew_elapsed / duration)
            q_ref = att.quat_slerp(q0, qt, progress)
            if progress >= 1.0:
                self._set_mode(MODE_NOMINAL)
                s.slew_target_q = None
                s.slew_start_q = None
                s.slew_elapsed = None
                s.slew_idle_timer = 0.0
        else:
            q_ref, _ = self._compute_reference(s, mode)
        s.q_ref = [float(x) for x in q_ref]
        # Deterministic error draw from the mode's closed-loop distribution
        sigma = _MODE_ERR_SIGMA_DEG.get(mode, 0.05)
        ev = np.array([self.rng.gauss(0.0, sigma) for _ in range(3)]) * _DEG
        ang = float(np.linalg.norm(ev))
        if ang > 1e-12:
            axisv = ev / ang
            dq = np.concatenate([axisv * math.sin(ang / 2.0),
                                 [math.cos(ang / 2.0)]])
        else:
            dq = np.array([0.0, 0.0, 0.0, 1.0])
        q_true = att.quat_normalize(att.quat_mult(np.asarray(q_ref, float), dq))
        s.q_true = [float(x) for x in q_true]
        # Body rate = reference rate (LVLH) — nadir tracking continues
        env = self._env
        r = env['r_m']
        w_orb_eci = np.cross(r, env['v_ms']) / float(np.dot(r, r))
        w_body = att.quat_to_dcm(q_true) @ w_orb_eci
        if mode in (MODE_INERTIAL,):
            w_body = np.zeros(3)
        s.omega_true = [float(x) for x in w_body]
        # SLOW state 1: wheel momentum from exact ∫τ_disturbance dt
        # (the wheels absorb what the disturbances pour in)
        tau_d = self._disturbance_torques(s)
        if mode == MODE_DESAT:
            # MTQ dump wins: relax wheel speeds toward the desat target
            decay = math.exp(-dt / 200.0)
            tgt = float(self._rw_desat_speed)
            bias = [tgt, -tgt, tgt, -tgt]
            for i in range(4):
                if s.active_wheels[i]:
                    h_t = self._rw_inertia * _RPM2RAD * bias[i]
                    s.h_wheel[i] = h_t + (s.h_wheel[i] - h_t) * decay
                    s.rw_speed[i] = s.h_wheel[i] / self._rw_inertia / _RPM2RAD
        else:
            dh = self._A_pinv @ (tau_d * dt)
            h_max = self._rw_inertia * self._rw_max_speed * _RPM2RAD
            for i in range(4):
                if s.active_wheels[i]:
                    s.h_wheel[i] = max(-h_max, min(h_max, s.h_wheel[i] + float(dh[i])))
                    s.rw_speed[i] = s.h_wheel[i] / self._rw_inertia / _RPM2RAD
        # SLOW state 3: MEKF covariance to its steady state
        kn = 0.008 if mode == MODE_FINE_POINT else 0.02
        if mode == MODE_ECLIPSE:
            kn = min(0.5, 0.02 + 3.0 * self._gyro_arw_dps_rts * math.sqrt(dt))
        self._ekf.set_covariance_steady_state(kn)
        self._ekf.q = q_true.copy()
        self._ekf.initialised = True
        s.att_error = att.quat_angle_deg(s.q_true, s.q_ref)
        if mode == MODE_COARSE_SUN:
            sb = self._true_sun_body()
            s.att_error = math.degrees(
                math.acos(max(-1.0, min(1.0, float(sb[0])))))

    # ─── Gyro and GPS models ─────────────────────────────────────────

    def _tick_gyro_and_gps(self, s: AOCSState, dt: float,
                            orbit_state: Any) -> None:
        """Model gyroscope bias estimation and GPS receiver."""
        # Gyro bias drift (random walk, ~0.003 deg/s/sqrt(hr) typical MEMS)
        drift_sigma = 0.00005 * math.sqrt(dt)
        for i, attr in enumerate(("gyro_bias_x", "gyro_bias_y", "gyro_bias_z")):
            bias = self._gyro_bias[i]
            bias += self.rng.gauss(0, drift_sigma)
            self._gyro_bias[i] = max(-0.1, min(0.1, bias))
            setattr(s, attr, self._gyro_bias[i])

        # Gyro temperature (correlated with RW proximity heat)
        avg_rw_temp = sum(s.rw_temp) / 4.0
        s.gyro_temp += (avg_rw_temp - 5.0 - s.gyro_temp) / 300.0 * dt
        s.gyro_temp += self.rng.gauss(0, 0.01) * math.sqrt(dt)

        # GPS receiver model with cold/warm/hot start tracking (Defect #3)
        # See defects/reviews/aocs.md for start mode semantics.
        #
        # GPS power is gated by BOTH the AOCS mode (no GPS in OFF/SAFE_BOOT) AND
        # the S2 device-access on/off state for the GPS receiver (device 0x020F).
        # Previously only the mode was checked, so the S2_AOCS_GPS_RECEIVER
        # command (the operator's GPS ON/OFF control) toggled device_states but
        # had no effect on the receiver — an unwired command. Switching the
        # device OFF now powers the GPS down; switching it back ON forces a COLD
        # re-acquisition (TTFF restart).
        gps_device_on = s.device_states.get(0x020F, True)
        if s.mode in (MODE_OFF, MODE_SAFE_BOOT) or not gps_device_on:
            s.gps_fix = 0
            s.gps_num_sats = 0
            s.gps_pdop = 99.9
            # Reset TTFF timer when powered down (mode-off or receiver switched off)
            if s.mode == MODE_OFF or not gps_device_on:
                s.gps_ttff_timer = 0.0
                s.gps_start_mode = 0  # COLD
        else:
            # GPS is powered and can acquire fix
            # Increment TTFF timer based on start mode
            s.gps_ttff_timer += dt

            # Determine TTFF target based on start mode
            ttff_target = {
                0: self._gps_cold_ttff_s,   # COLD: 60s
                1: self._gps_warm_ttff_s,   # WARM: 30s
                2: self._gps_hot_ttff_s,    # HOT: 5s
            }.get(s.gps_start_mode, self._gps_cold_ttff_s)

            # Award satellite count and fix quality based on acquisition progress
            if s.gps_ttff_timer < ttff_target * 0.3:
                # Early phase: searching for satellites
                s.gps_num_sats = self.rng.randint(0, 3)
                s.gps_fix = 0 if s.gps_num_sats < 3 else 1
                s.gps_pdop = 99.9
            elif s.gps_ttff_timer < ttff_target * 0.8:
                # Middle phase: acquiring position fix
                s.gps_num_sats = self.rng.randint(4, 8)
                s.gps_fix = 2 if s.gps_num_sats >= 4 else 1
                s.gps_pdop = 3.0 + self.rng.gauss(0, 0.5)
                s.gps_pdop = max(1.5, min(8.0, s.gps_pdop))
            elif s.gps_ttff_timer >= ttff_target:
                # Post-TTFF: full lock with velocity
                s.gps_num_sats = self.rng.randint(6, 12)
                s.gps_fix = 3  # 3D + velocity
                s.gps_pdop = 1.2 + self.rng.gauss(0, 0.3)
                s.gps_pdop = max(0.8, min(6.0, s.gps_pdop))
            else:
                # Intermediate: progressing toward full fix
                s.gps_num_sats = self.rng.randint(4, 8)
                s.gps_fix = 2
                s.gps_pdop = 2.0 + self.rng.gauss(0, 0.5)
                s.gps_pdop = max(1.5, min(6.0, s.gps_pdop))

        # Magnetometer total field magnitude
        s.mag_field_total = math.sqrt(
            s.mag_x ** 2 + s.mag_y ** 2 + s.mag_z ** 2
        )

    # ─── Wheels and momentum ─────────────────────────────────────────

    def _tick_wheels(self, s: AOCSState, dt: float) -> None:
        """Wheel housekeeping: temperatures, currents, momentum telemetry.

        Wheel SPEEDS are now owned by the momentum states advanced in the
        control step (WP-2) — no random walk. The thermal update uses an
        exact exponential relaxation so it stays stable at secular-mode dt.
        """
        for i in range(4):
            # Keep rw_speed consistent with the momentum state
            s.rw_speed[i] = s.h_wheel[i] / self._rw_inertia / _RPM2RAD
            deg = self._bearing_degradation[i]
            if not s.active_wheels[i]:
                s.rw_current[i] = 0.0
                s.rw_enabled[i] = False
                # Defect #24: a *seized* wheel keeps generating bearing-
                # friction heat even though it is stopped and disabled.
                heat = 0.2 * deg if deg > 0.0 else 0.0
            else:
                s.rw_enabled[i] = True
                # Current: baseline + speed-dependent + torque-dependent +
                # degradation
                base_current = 0.05
                speed_current = abs(s.rw_speed[i]) / self._rw_max_speed * 0.15
                torque_current = min(1.0, abs(s.tau_wheel[i])
                                     / max(1e-9, self._rw_max_torque)) * 0.2
                degrade_current = deg * 0.3
                s.rw_current[i] = max(0.0, base_current + speed_current
                                      + torque_current + degrade_current
                                      + self.rng.gauss(0, 0.005))
                heat = 0.00002 * abs(s.rw_speed[i]) * (1.0 + 5.0 * deg)
            # Exact exponential relaxation toward the equilibrium temperature
            # T_eq = 20 + 300*heat  (tau = 300 s); stable at any dt.
            t_eq = 20.0 + 300.0 * heat
            decay = math.exp(-dt / 300.0)
            s.rw_temp[i] = t_eq + (s.rw_temp[i] - t_eq) * decay \
                + self.rng.gauss(0, 0.02) * math.sqrt(min(dt, 60.0))

        # System momentum: VECTOR wheel momentum in the body frame (replaces
        # the old scalar sum-of-magnitudes — brief 02 root cause: four wheels
        # at equal bias speed in the pyramid now correctly report ~0).
        h_b = self._A @ np.array(s.h_wheel)
        self._h_body = h_b
        s.total_momentum = float(np.linalg.norm(h_b))

    # ─── S2 device-access power gates ────────────────────────────────

    def _apply_device_power_gates(self, s: AOCSState) -> None:
        """Force AOCS sensor/actuator telemetry to a powered-down state for
        any device switched OFF via S2 device access (device_states).

        Mirrors the GPS receiver gate (0x020F, handled in _tick_gyro_and_gps):
        before this, every other entry in ``device_states`` was a dead flag —
        the S2_AOCS_* on/off commands were accepted and toggled the dict but
        nothing in the model ever read it, so they had no effect. Each device
        now collapses its corresponding telemetry/validity when powered off and
        the normal sensor/actuator tick restores it when powered back on.

        Called at the end of ``tick`` so it overrides the freshly-computed
        sensor values. All devices default ON, so nominal behaviour and the
        existing test suite are unchanged.
        """
        ds = s.device_states

        # Star trackers (0x0204 ST1, 0x0205 ST2). Forcing status 0 (OFF) makes
        # the next _tick_star_trackers treat them as un-powered; here we also
        # collapse the composite validity for the selected unit immediately.
        if not ds.get(0x0204, True):
            s.st1_status = 0
            s.st1_num_stars = 0
        if not ds.get(0x0205, True):
            s.st2_status = 0
            s.st2_num_stars = 0
        sel_status = getattr(s, f"st{s.st_selected}_status", 0)
        if sel_status != 2:
            s.st_valid = False

        # Gyroscopes (0x0206-0x0208). With no gyro powered the rate solution is
        # invalid; zero the reported body rates and bias telemetry.
        if not any(ds.get(d, True) for d in (0x0206, 0x0207, 0x0208)):
            s.rate_roll = s.rate_pitch = s.rate_yaw = 0.0
            s.gyro_bias_x = s.gyro_bias_y = s.gyro_bias_z = 0.0

        # Magnetometers A/B (0x0209/0x020A). If the selected unit (or both) is
        # powered off, the magnetometer reading is invalid.
        mag_a_on = ds.get(0x0209, True)
        mag_b_on = ds.get(0x020A, True)
        if not mag_a_on:
            s.mag_a_x = s.mag_a_y = s.mag_a_z = 0.0
        if not mag_b_on:
            s.mag_b_x = s.mag_b_y = s.mag_b_z = 0.0
        if not mag_a_on and not mag_b_on:
            s.mag_valid = False
            s.mag_x = s.mag_y = s.mag_z = 0.0
        elif s.mag_select == 'A' and not mag_a_on:
            s.mag_valid = False
        elif s.mag_select == 'B' and not mag_b_on:
            s.mag_valid = False

        # Sun sensor array (0x020E) — collapse the coarse sun vector.
        if not ds.get(0x020E, True):
            s.css_valid = False
            s.css_sun_x = s.css_sun_y = s.css_sun_z = 0.0
            for face in list(s.css_heads):
                s.css_heads[face] = 0.0

        # Magnetorquers X/Y/Z (0x020B-0x020D) — a powered-off rod produces no
        # dipole, so zero its duty cycle.
        if not ds.get(0x020B, True):
            s.mtq_x_duty = 0.0
        if not ds.get(0x020C, True):
            s.mtq_y_duty = 0.0
        if not ds.get(0x020D, True):
            s.mtq_z_duty = 0.0

        # Reaction wheels 0-3 (0x0200-0x0203) — a powered-off wheel is no longer
        # spun up or current-drawing; mark it inactive and zero its telemetry.
        for idx, dev in enumerate((0x0200, 0x0201, 0x0202, 0x0203)):
            if not ds.get(dev, True):
                if idx < len(s.active_wheels):
                    s.active_wheels[idx] = False
                if idx < len(s.rw_speed):
                    s.rw_speed[idx] = 0.0
                if idx < len(s.rw_current):
                    s.rw_current[idx] = 0.0

    @staticmethod
    def _orbit_phase_from_geometry(orbit_state) -> float | None:
        """Sun-sweep phase angle (deg) from the true orbit geometry.

        Defines the rotating LVLH-like frame used by the CSS model
        (sun_body = (cos b cos phi, cos b sin phi, sin b)): body Z on the
        orbit normal, X/Y in the orbit plane. phi is the in-plane bearing of
        the sun's projection measured from the spacecraft position vector, so
        it advances exactly once per orbit and is phase-locked to eclipse.
        Returns None when the orbit state has no ECI vectors (test stubs).
        """
        r = getattr(orbit_state, 'pos_eci', None)
        v = getattr(orbit_state, 'vel_eci', None)
        sun = getattr(orbit_state, 'sun_eci', None)
        if r is None or v is None or sun is None:
            return None
        try:
            rx, ry, rz = float(r[0]), float(r[1]), float(r[2])
            vx, vy, vz = float(v[0]), float(v[1]), float(v[2])
            sx, sy, sz = float(sun[0]), float(sun[1]), float(sun[2])
        except (TypeError, IndexError):
            return None
        rmag = math.sqrt(rx * rx + ry * ry + rz * rz)
        smag = math.sqrt(sx * sx + sy * sy + sz * sz)
        if rmag < 1.0 or smag < 1.0:
            return None
        # Orbit normal h = r x v
        hx = ry * vz - rz * vy
        hy = rz * vx - rx * vz
        hz = rx * vy - ry * vx
        hmag = math.sqrt(hx * hx + hy * hy + hz * hz)
        if hmag < 1e-6:
            return None
        hx, hy, hz = hx / hmag, hy / hmag, hz / hmag
        ux, uy, uz = rx / rmag, ry / rmag, rz / rmag        # in-plane X axis
        # in-plane Y axis = h x u (completes the right-handed triad)
        wx = hy * uz - hz * uy
        wy = hz * ux - hx * uz
        wz = hx * uy - hy * ux
        sxn, syn, szn = sx / smag, sy / smag, sz / smag
        cos_phi = sxn * ux + syn * uy + szn * uz
        sin_phi = sxn * wx + syn * wy + szn * wz
        if abs(cos_phi) < 1e-12 and abs(sin_phi) < 1e-12:
            return None
        return math.degrees(math.atan2(sin_phi, cos_phi)) % 360.0

    # ─── Main tick ───────────────────────────────────────────────────

    def tick(self, dt: float, orbit_state: Any,
             shared_params: dict[int, float]) -> None:
        s = self._state
        p = self._param_ids

        # Full-fidelity oversized steps (standalone harnesses hand the model
        # dt up to 60 s directly; the engine itself sub-steps at 1 s): run
        # the WHOLE chain — sensors, estimator, reference, control — in
        # inner passes of <= 2 s, otherwise the estimate and reference would
        # freeze while the truth integrates (LVLH rotates 3.8°/min). Above
        # the secular threshold the fast loop is bypassed instead (WP-1).
        _speed = shared_params.get(0x05FE)
        _secular_early = (dt > self._secular_speed_threshold
                          or (_speed is not None
                              and _speed > self._secular_speed_threshold))
        if not _secular_early and dt > 2.0:
            n_inner = int(math.ceil(dt / 2.0))
            dt_inner = dt / n_inner
            for _ in range(n_inner):
                self.tick(dt_inner, orbit_state, shared_params)
            return

        # ── EPS power-line gate ──
        # AOCS sensors and wheels are powered from the `aocs_wheels` line
        # (param 0x0117). If EPS has dropped that line (operator command,
        # overcurrent trip, load shed) the AOCS subsystem must collapse to
        # MODE_OFF — sensors stop, wheels coast, no fresh telemetry.
        # Without this, AOCS continues to publish a valid attitude, valid
        # rates and current draws even though the EPS line is dark.
        _line_aocs_on = bool(shared_params.get(0x0117, 1))
        if not _line_aocs_on and s.mode != MODE_OFF:
            s.mode = MODE_OFF
            s.submode = 0
            s.time_in_mode = 0.0
            s.st_valid = False
            s.css_valid = False
            s.mag_valid = False

        # External (propulsion) torque hook — Phase D. The prop model
        # publishes its thrust-misalignment torque at 0x0B17 (uN*m, thrust
        # along +X with the offset in Z => torque about body Y). It joins the
        # environmental disturbances in _disturbance_torques, so a firing
        # thruster genuinely pushes the attitude and loads the wheels — the
        # mechanical reason NOM-013 ends with a momentum check.
        _ext_uNm = float(shared_params.get(0x0B17, 0.0))
        self._ext_torque_body = np.array([0.0, _ext_uNm * 1e-6, 0.0])

        # Sun-sweep phase (brief 01 F7 / WP-8). Previously accumulated at a
        # hard-coded 360/5700 deg/s against a 5669.3 s orbit — a 30.7 s/orbit
        # beat that walked the sun through 360 deg of alignment every ~12 days
        # and moved orbit-average generation by ±8 W between runs. Now derived
        # from the propagator's true geometry: the angle between the
        # spacecraft position and the sun's projection onto the orbit plane,
        # a deterministic function of orbit_state. Falls back to the legacy
        # accumulator only for reduced test harnesses without ECI state.
        phase = self._orbit_phase_from_geometry(orbit_state)
        if phase is not None:
            self._orbit_phase = phase
        else:
            self._orbit_phase = (
                self._orbit_phase + dt * (360.0 / 5700.0)
            ) % 360.0

        # GPS from orbit state
        shared_params[p.get("gps_lat", 0x0210)] = orbit_state.lat_deg
        shared_params[p.get("gps_lon", 0x0211)] = orbit_state.lon_deg
        shared_params[p.get("gps_alt", 0x0212)] = orbit_state.alt_km
        shared_params[p.get("gps_vx", 0x0213)] = orbit_state.vel_x
        shared_params[p.get("gps_vy", 0x0214)] = orbit_state.vel_y
        shared_params[p.get("gps_vz", 0x0215)] = orbit_state.vel_z
        shared_params[p.get("solar_beta", 0x0216)] = orbit_state.solar_beta_deg

        # ── External-poke sync (tests / scenarios / separation tumble) ──
        # Injection sites (engine separation tumble, scenario scripts, unit
        # tests) write s.rate_* and s.rw_speed directly. Those are now
        # DERIVED telemetry (from omega_true / h_wheel); detect an external
        # write since the last publish and fold it back into the owning
        # truth state so injections still mean what they always meant.
        cur_rates = (s.rate_roll, s.rate_pitch, s.rate_yaw)
        lp = getattr(self, '_pub_rates', None)
        poked = (lp is not None and any(
            abs(c - l) > 1e-9 for c, l in zip(cur_rates, lp)))
        if lp is None:
            poked = (any(abs(c) > 1e-9 for c in cur_rates)
                     and all(abs(w) < 1e-12 for w in s.omega_true))
        if poked:
            s.omega_true = [c * _DEG for c in cur_rates]
        lw = getattr(self, '_pub_rw', None)
        for i in range(4):
            if lw is None or abs(s.rw_speed[i] - lw[i]) > 1e-6:
                derived = s.h_wheel[i] / self._rw_inertia / _RPM2RAD
                if abs(s.rw_speed[i] - derived) > 1e-3:
                    s.h_wheel[i] = self._rw_inertia * s.rw_speed[i] * _RPM2RAD

        # True environment for this tick (ECI position/velocity/sun/field)
        self._env = self._environment(orbit_state)

        # Secular-mode detection (WP-1): the engine publishes its speed at
        # 0x05FE; a single oversized dt (standalone harness) also qualifies.
        speed = shared_params.get(0x05FE)
        secular = (dt > self._secular_speed_threshold
                   or (speed is not None
                       and speed > self._secular_speed_threshold))

        # Update sensors
        self._tick_star_trackers(s, dt, orbit_state)
        self._tick_css(s, orbit_state)
        self._tick_magnetometer(s, orbit_state)
        self._tick_gyro_and_gps(s, dt, orbit_state)

        # Attitude determination (MEKF / TRIAD path)
        self._tick_estimator(s, dt, secular)

        # Emergency checks (before mode tick)
        if not self._check_emergency(s):
            # Auto transitions
            self._check_auto_transitions(s, dt, orbit_state)

        # Autonomous momentum management (AOCS_SET_MOM_MGMT)
        if (s.mom_mgmt_enable and s.mode in (MODE_NOMINAL, MODE_FINE_POINT,
                                             MODE_INERTIAL, MODE_ECLIPSE)
                and self._h_env_frac(s) >= s.mom_mgmt_entry_pct):
            self._set_mode(MODE_DESAT)

        # Mode tick
        prev_mode = s.mode
        s.time_in_mode += dt
        s.fidelity_mode = 1 if secular else 0
        if secular:
            self._tick_secular(s, dt, s.mode)
        elif s.mode == MODE_OFF:
            self._tick_off(s, dt)
        elif s.mode == MODE_SAFE_BOOT:
            self._tick_safe_boot(s, dt)
        elif s.mode == MODE_DETUMBLE:
            self._tick_detumble(s, dt)
        elif s.mode == MODE_COARSE_SUN:
            self._tick_coarse_sun(s, dt)
        elif s.mode == MODE_NOMINAL:
            self._tick_nominal(s, dt)
        elif s.mode == MODE_FINE_POINT:
            self._tick_fine_point(s, dt)
        elif s.mode == MODE_SLEW:
            self._tick_slew(s, dt)
        elif s.mode == MODE_DESAT:
            self._tick_desat(s, dt)
        elif s.mode == MODE_ECLIPSE:
            self._tick_eclipse(s, dt)
        elif s.mode == MODE_INERTIAL:
            self._tick_inertial(s, dt)

        if secular:
            # Published rates follow the reference in secular mode
            s.rate_roll = float(s.omega_true[0] * _RAD)
            s.rate_pitch = float(s.omega_true[1] * _RAD)
            s.rate_yaw = float(s.omega_true[2] * _RAD)

        # Slew-idle timer (FINE precondition bit 6)
        if s.mode != MODE_SLEW:
            s.slew_idle_timer = min(s.slew_idle_timer + dt, 1.0e6)

        # Control error / settle-time / rate-stability metrics
        q_est_now = self._est_quat()
        s.control_err_deg = att.quat_angle_deg(q_est_now, s.q_ref)
        if prev_mode != s.mode:
            s.settle_time_s = 0.0
            s.settled = False
        gate = 0.05 if s.mode == MODE_FINE_POINT else 1.0
        if not s.settled:
            s.settle_time_s += dt
            if s.mode in _WHEEL_CTRL_MODES and s.control_err_deg < gate:
                s.settled = True
        w_rel = getattr(self, '_omega_est', np.zeros(3)) \
            - np.asarray(s.omega_ref, float)
        s.rate_err_hist.append(float(np.linalg.norm(w_rel)) * _RAD)
        if len(s.rate_err_hist) > 60:
            del s.rate_err_hist[:len(s.rate_err_hist) - 60]
        if len(s.rate_err_hist) >= 2:
            # Jitter metric: 3x the standard deviation of the rate error
            # magnitude over the last 60 s (stability, not absolute error —
            # the mean is dominated by the gyro noise floor).
            m = sum(s.rate_err_hist) / len(s.rate_err_hist)
            var = sum((x - m) ** 2 for x in s.rate_err_hist) / len(s.rate_err_hist)
            s.rate_stability_3sig = 3.0 * math.sqrt(var)
        else:
            s.rate_stability_3sig = 0.0

        # FINE-entry precondition bitmask (0x02C5)
        self._update_fine_ready(s)

        # Wheels housekeeping — temperatures, currents, momentum telemetry
        self._tick_wheels(s, dt)
        self._tick_magnetorquers(s, dt)

        # ── S2 device-access power gates ──
        # Override sensor/actuator telemetry for any device the operator has
        # switched OFF via S2 device access (e.g. S2_AOCS_STAR_TRACKER_1). Runs
        # after the sensor/wheel ticks so a powered-off device reads as down.
        self._apply_device_power_gates(s)

        self._prev_in_eclipse = orbit_state.in_eclipse

        # Event generation on tick
        self._generate_tick_events(s, prev_mode)

        # ── Write all shared params ──
        # Gate published attitude on a valid attitude source. During cold boot
        # the star trackers spend ~30 s in lost-in-space search; until any
        # source (ST or CSS) is valid, operators must not see a quaternion.
        _att_known = (s.mode != MODE_OFF) and (s.mode != MODE_SAFE_BOOT) and (s.st_valid or s.css_valid)
        if _att_known:
            shared_params[p.get("att_q1", 0x0200)] = s.q[0]
            shared_params[p.get("att_q2", 0x0201)] = s.q[1]
            shared_params[p.get("att_q3", 0x0202)] = s.q[2]
            shared_params[p.get("att_q4", 0x0203)] = s.q[3]
        else:
            # No valid attitude source — publish identity quat as a clearly
            # invalid placeholder (operators must check att_source/att_error
            # rather than trust the quat at face value).
            shared_params[p.get("att_q1", 0x0200)] = 0.0
            shared_params[p.get("att_q2", 0x0201)] = 0.0
            shared_params[p.get("att_q3", 0x0202)] = 0.0
            shared_params[p.get("att_q4", 0x0203)] = 0.0
        # Rates and attitude error: only publish when AOCS is active (not OFF)
        if s.mode != MODE_OFF:
            shared_params[p.get("rate_roll", 0x0204)] = s.rate_roll
            shared_params[p.get("rate_pitch", 0x0205)] = s.rate_pitch
            shared_params[p.get("rate_yaw", 0x0206)] = s.rate_yaw
            shared_params[p.get("att_error", 0x0217)] = s.att_error
        else:
            shared_params[p.get("rate_roll", 0x0204)] = 0.0
            shared_params[p.get("rate_pitch", 0x0205)] = 0.0
            shared_params[p.get("rate_yaw", 0x0206)] = 0.0
            shared_params[p.get("att_error", 0x0217)] = 0.0
        shared_params[p.get("aocs_mode", 0x020F)] = s.mode
        shared_params[p.get("aocs_submode", 0x0262)] = s.submode
        shared_params[p.get("time_in_mode", 0x0264)] = s.time_in_mode

        for i in range(4):
            shared_params[p.get(f"rw{i+1}_speed", 0x0207 + i)] = s.rw_speed[i]
            shared_params[p.get(f"rw{i+1}_temp", 0x0218 + i)] = s.rw_temp[i]
            shared_params[p.get(f"rw{i+1}_current", 0x0250 + i)] = s.rw_current[i]
            shared_params[p.get(f"rw{i+1}_enabled", 0x0254 + i)] = (
                1 if s.rw_enabled[i] else 0
            )

        shared_params[p.get("mag_x", 0x020B)] = s.mag_x
        shared_params[p.get("mag_y", 0x020C)] = s.mag_y
        shared_params[p.get("mag_z", 0x020D)] = s.mag_z

        # Body-frame sun vector (from CSS composite)
        shared_params[0x0220] = s.css_sun_x
        shared_params[0x0221] = s.css_sun_y
        shared_params[0x0222] = s.css_sun_z

        # Dual magnetometer params
        shared_params[0x0223] = s.mag_a_x
        shared_params[0x0224] = s.mag_a_y
        shared_params[0x0225] = s.mag_a_z
        shared_params[0x0226] = s.mag_b_x
        shared_params[0x0227] = s.mag_b_y
        shared_params[0x0228] = s.mag_b_z
        shared_params[0x0229] = 0.0 if s.mag_select == 'A' else 1.0

        # CSS per-head illumination params
        _css_head_pids = {
            'px': 0x027A, 'mx': 0x027B, 'py': 0x027C,
            'my': 0x027D, 'pz': 0x027E, 'mz': 0x027F,
        }
        for face, pid in _css_head_pids.items():
            shared_params[pid] = s.css_heads.get(face, 0.0)

        # Star tracker
        shared_params[p.get("st1_status", 0x0240)] = s.st1_status
        shared_params[p.get("st1_num_stars", 0x0241)] = s.st1_num_stars
        shared_params[0x0242] = float(s.st2_num_stars)
        shared_params[p.get("st2_status", 0x0243)] = s.st2_status
        shared_params[0x0244] = float(s.st_selected)

        # CSS
        shared_params[p.get("css_sun_x", 0x0245)] = s.css_sun_x
        shared_params[p.get("css_sun_y", 0x0246)] = s.css_sun_y
        shared_params[p.get("css_sun_z", 0x0247)] = s.css_sun_z
        shared_params[p.get("css_valid", 0x0248)] = 1 if s.css_valid else 0

        # Magnetorquers
        shared_params[p.get("mtq_x_duty", 0x0258)] = s.mtq_x_duty
        shared_params[p.get("mtq_y_duty", 0x0259)] = s.mtq_y_duty
        shared_params[p.get("mtq_z_duty", 0x025A)] = s.mtq_z_duty

        # Momentum
        shared_params[p.get("total_momentum", 0x025B)] = s.total_momentum

        # Equipment enabled flags
        shared_params[0x025C] = 1.0 if s.mag_valid else 0.0
        shared_params[0x025D] = 1.0 if (s.mode != MODE_OFF) else 0.0  # Gyro on when AOCS active
        shared_params[0x025E] = 1.0 if s.mtq_enabled else 0.0

        # Phase 4: Flight hardware params
        shared_params[0x0270] = s.gyro_bias_x
        shared_params[0x0271] = s.gyro_bias_y
        shared_params[0x0272] = s.gyro_bias_z
        shared_params[0x0273] = s.gyro_temp
        shared_params[0x0274] = float(s.gps_fix)
        shared_params[0x0275] = s.gps_pdop
        shared_params[0x0276] = float(s.gps_num_sats)
        shared_params[0x0277] = s.mag_field_total

        # Wave 5-C: Slew and momentum management
        # Slew time remaining
        if s.mode == MODE_SLEW and s.slew_elapsed is not None:
            total_angle = self._quat_angle_error(
                s.slew_start_q or s.q, s.slew_target_q or s.q
            )
            expected_duration = total_angle / max(s.slew_rate_dps, 0.1)
            time_remaining = max(0.0, expected_duration - s.slew_elapsed)
            shared_params[0x0280] = time_remaining
            # Slew progress
            progress = (expected_duration - time_remaining) / max(expected_duration, 0.1) * 100.0
            shared_params[0x0281] = min(100.0, max(0.0, progress))
        else:
            shared_params[0x0280] = 0.0
            shared_params[0x0281] = 0.0

        # Momentum envelope usage (per-axis-aware, % of most-loaded wheel)
        momentum_pct = self._h_env_frac(s)
        shared_params[0x0282] = min(100.0, momentum_pct)

        # Attitude source (0=ST, 1=CSS, 2=Gyro)
        if s.st_valid:
            attitude_source = 0
        elif s.css_valid:
            attitude_source = 1
        else:
            attitude_source = 2
        shared_params[0x0283] = float(attitude_source)

        # RW bearing health at the DECLARED ids 0x0284-0x0287 (brief 02 root
        # cause 10: the model previously published these at 0x028E-0x0291,
        # which are undeclared, while the ground read the slew state as RW2
        # bearing health).
        for i in range(4):
            shared_params[0x0284 + i] = self._rw_bearing_health[i]
        # 0x028E aocs.rw_cmd_sum diagnostic (declared; previously a stub)
        shared_params[0x028E] = float(sum(abs(t) for t in s.tau_wheel))

        # Slew state (0=IDLE, 1=SLEWING, 2=SETTLING, 3=COMPLETE)
        if s.mode == MODE_SLEW:
            if s.slew_target_q is None:
                s.slew_state = 0  # IDLE
            else:
                err = self._quat_angle_error(s.q, s.slew_target_q)
                if err > 1.0:
                    s.slew_state = 1  # SLEWING
                else:
                    s.slew_state = 2  # SETTLING
        else:
            s.slew_state = 0  # IDLE (not in slew mode)
        shared_params[p.get("slew_state", 0x0292)] = float(s.slew_state)

        # Number of enabled wheels — writes the previously-dead 0x0260 that
        # fdir.yaml monitors (`aocs.wheels_enabled < 2` latched SAFE at t=0).
        shared_params[0x0260] = float(sum(1 for w in s.active_wheels if w))

        # ── Phase C determination/control telemetry block 0x02A0-0x02C8 ──
        q_est = self._est_quat()
        shared_params[0x02A0] = float(q_est[0])
        shared_params[0x02A1] = float(q_est[1])
        shared_params[0x02A2] = float(q_est[2])
        shared_params[0x02A3] = float(q_est[3])
        w_est = getattr(self, '_omega_est', np.zeros(3))
        shared_params[0x02A4] = float(w_est[0] * _RAD)
        shared_params[0x02A5] = float(w_est[1] * _RAD)
        shared_params[0x02A6] = float(w_est[2] * _RAD)
        shared_params[0x02A7] = s.knowledge_err_3sig_deg * 3600.0  # arcsec
        shared_params[0x02A8] = s.control_err_deg
        shared_params[0x02A9] = s.control_err_deg * 3600.0
        total_err = math.sqrt(s.control_err_deg ** 2
                              + s.knowledge_err_3sig_deg ** 2)
        shared_params[0x02AA] = total_err
        shared_params[0x02AB] = float(s.q_ref[0])
        shared_params[0x02AC] = float(s.q_ref[1])
        shared_params[0x02AD] = float(s.q_ref[2])
        shared_params[0x02AE] = float(s.q_ref[3])
        shared_params[0x02AF] = float(
            0 if s.mode == MODE_INERTIAL else s.pointing_frame)
        env = self._env
        C_true = att.quat_to_dcm(np.asarray(s.q_true, float))
        nadir_b = C_true @ (-env['r_m'] / np.linalg.norm(env['r_m']))
        shared_params[0x02B0] = float(nadir_b[0])
        shared_params[0x02B1] = float(nadir_b[1])
        shared_params[0x02B2] = float(nadir_b[2])
        sun_b = C_true @ env['sun_hat']
        if env['eclipse']:
            shared_params[0x02B3] = 0.0
            shared_params[0x02B4] = 0.0
            shared_params[0x02B5] = 0.0
        else:
            shared_params[0x02B3] = float(sun_b[0])
            shared_params[0x02B4] = float(sun_b[1])
            shared_params[0x02B5] = float(sun_b[2])
        h_b = getattr(self, '_h_body', np.zeros(3))
        shared_params[0x02B6] = float(h_b[0])
        shared_params[0x02B7] = float(h_b[1])
        shared_params[0x02B8] = float(h_b[2])
        shared_params[0x02B9] = float(np.linalg.norm(h_b))
        shared_params[0x02BA] = momentum_pct
        m_cmd = getattr(self, '_mtq_dipole_cmd', np.zeros(3))
        shared_params[0x02BB] = float(m_cmd[0])
        shared_params[0x02BC] = float(m_cmd[1])
        shared_params[0x02BD] = float(m_cmd[2])
        shared_params[0x02BE] = s.tau_dist[0] * 1e6   # µN·m
        shared_params[0x02BF] = s.tau_dist[1] * 1e6
        shared_params[0x02C0] = s.tau_dist[2] * 1e6
        shared_params[0x02C1] = float(s.est_source)
        shared_params[0x02C2] = float(self._ekf.innov_reject_count)
        shared_params[0x02C3] = s.rate_stability_3sig
        shared_params[0x02C4] = s.settle_time_s
        shared_params[0x02C5] = float(s.fine_ready_mask)
        shared_params[0x02C6] = float(s.fidelity_mode)
        try:
            alt_m = float(orbit_state.alt_km) * 1000.0
        except (TypeError, ValueError):
            alt_m = 450000.0
        shared_params[0x02C7] = alt_m * total_err * _DEG
        # AOCS suite power draw — EPS bills the aocs_wheels line from this
        # (COARSE 4 / NOMINAL 9 / FINE 15 W, brief 02 table; Phase B billing
        # convention: actuator state, not line-switch position).
        shared_params[0x02C8] = _MODE_POWER_W.get(s.mode, 9.0)

        # CSS head health
        css_faces = ['px', 'mx', 'py', 'my', 'pz', 'mz']
        for j, face in enumerate(css_faces):
            health = 0.0 if s.css_head_failed.get(face, False) else 100.0
            shared_params[0x0288 + j] = health

        # Record published derived telemetry for the external-poke detector
        self._pub_rates = (s.rate_roll, s.rate_pitch, s.rate_yaw)
        self._pub_rw = tuple(s.rw_speed)

    def get_telemetry(self) -> dict[int, float]:
        """Return all AOCS telemetry parameters for HK packet SID 2.

        This includes attitude, rates, wheel speeds/currents, sensor status,
        magnetorquer commands, and flight hardware parameters.
        """
        s = self._state
        p = self._param_ids

        _att_known = (s.mode != MODE_OFF) and (s.mode != MODE_SAFE_BOOT) and (s.st_valid or s.css_valid)
        tm = {
            # Attitude quaternion (zeroed if no valid attitude source)
            p.get("att_q1", 0x0200): s.q[0] if _att_known else 0.0,
            p.get("att_q2", 0x0201): s.q[1] if _att_known else 0.0,
            p.get("att_q3", 0x0202): s.q[2] if _att_known else 0.0,
            p.get("att_q4", 0x0203): s.q[3] if _att_known else 0.0,
            # Body rates
            p.get("rate_roll", 0x0204): s.rate_roll,
            p.get("rate_pitch", 0x0205): s.rate_pitch,
            p.get("rate_yaw", 0x0206): s.rate_yaw,
            # Attitude error
            p.get("att_error", 0x0217): s.att_error,
            # AOCS mode
            p.get("aocs_mode", 0x020F): float(s.mode),
            p.get("aocs_submode", 0x0262): float(s.submode),
            p.get("time_in_mode", 0x0264): s.time_in_mode,
        }

        # Reaction wheels
        for i in range(4):
            tm[p.get(f"rw{i+1}_speed", 0x0207 + i)] = s.rw_speed[i]
            tm[p.get(f"rw{i+1}_current", 0x0250 + i)] = s.rw_current[i]
            tm[p.get(f"rw{i+1}_enabled", 0x0254 + i)] = float(1 if s.rw_enabled[i] else 0)

        # Magnetometer (composite)
        tm[p.get("mag_x", 0x020B)] = s.mag_x
        tm[p.get("mag_y", 0x020C)] = s.mag_y
        tm[p.get("mag_z", 0x020D)] = s.mag_z

        # Star trackers
        tm[p.get("st1_status", 0x0240)] = float(s.st1_status)
        tm[p.get("st1_num_stars", 0x0241)] = float(s.st1_num_stars)
        tm[p.get("st2_status", 0x0243)] = float(s.st2_status)

        # CSS (Coarse Sun Sensor)
        tm[p.get("css_sun_x", 0x0245)] = s.css_sun_x
        tm[p.get("css_sun_y", 0x0246)] = s.css_sun_y
        tm[p.get("css_sun_z", 0x0247)] = s.css_sun_z
        tm[p.get("css_valid", 0x0248)] = float(1 if s.css_valid else 0)

        # Magnetorquers
        tm[p.get("mtq_x_duty", 0x0258)] = s.mtq_x_duty
        tm[p.get("mtq_y_duty", 0x0259)] = s.mtq_y_duty
        tm[p.get("mtq_z_duty", 0x025A)] = s.mtq_z_duty

        # Total momentum
        tm[p.get("total_momentum", 0x025B)] = s.total_momentum

        # Phase 4: Flight hardware parameters
        tm[0x0270] = s.gyro_bias_x
        tm[0x0271] = s.gyro_bias_y
        tm[0x0272] = s.gyro_bias_z
        tm[0x0273] = s.gyro_temp
        tm[0x0274] = float(s.gps_fix)
        tm[0x0275] = s.gps_pdop
        tm[0x0276] = float(s.gps_num_sats)
        tm[0x0277] = s.mag_field_total

        return tm

    # ─── Event Generation ───────────────────────────────────────────

    def _generate_tick_events(self, s: AOCSState, prev_mode: int) -> None:
        """Generate events based on state transitions and threshold crossings.

        Events are added to the engine's event queue via the subsystem.
        """
        events = []

        # AOCS_MODE_CHANGE (0x0200) — on any mode transition
        if prev_mode != s.mode:
            events.append({
                'event_id': 0x0200,
                'severity': 'LOW',
                'description': f'AOCS mode change from {_MODE_NAMES.get(prev_mode, "UNKNOWN")} to {_MODE_NAMES.get(s.mode, "UNKNOWN")}'
            })

        # RW_OVERSPEED (0x0201) — rising-edge: fire once when any wheel first
        # exceeds 5000 RPM, not every tick the overspeed persists.
        overspeed_idx = next((i for i in range(4) if abs(s.rw_speed[i]) > 5000.0), None)
        rw_overspeed = overspeed_idx is not None
        if rw_overspeed and not self._prev_rw_overspeed:
            events.append({
                'event_id': 0x0201,
                'severity': 'MEDIUM',
                'description': f'Reaction wheel {overspeed_idx+1} overspeed: {s.rw_speed[overspeed_idx]:.1f} RPM'
            })
        self._prev_rw_overspeed = rw_overspeed

        # RW_BEARING_DEGRADED (0x0202) — per-wheel rising-edge: fire once when a
        # wheel's bearing health first drops below 50%, not every tick after.
        for i in range(4):
            health_pct = (1.0 - self._bearing_degradation[i]) * 100.0
            self._rw_bearing_health[i] = health_pct
            degraded = (health_pct < 50.0 and self._bearing_degradation[i] > 0.0)
            if degraded and not self._prev_rw_bearing_degraded[i]:
                events.append({
                    'event_id': 0x0202,
                    'severity': 'MEDIUM',
                    'description': f'Reaction wheel {i+1} bearing degradation: {health_pct:.1f}%'
                })
            self._prev_rw_bearing_degraded[i] = degraded

        # ST_BLIND (0x0203) — when star tracker loses lock (sun in FOV, etc.)
        for unit in (1, 2):
            status_attr = f"st{unit}_status"
            prev_attr = f"_prev_st{unit}_status"
            curr_status = getattr(s, status_attr)
            prev_status = getattr(self, prev_attr, 0)
            if prev_status == 2 and curr_status == 3:  # TRACKING -> BLIND
                events.append({
                    'event_id': 0x0203,
                    'severity': 'MEDIUM',
                    'description': f'Star tracker {unit} lost lock (BLIND)'
                })
            # NOTE: _prev_stN_status is updated in the ST_RECOVERY loop below
            # (after both edge checks) so that the recovery check still sees the
            # pre-tick status. Updating it here would mask BLIND->TRACKING.

        # ST_RECOVERY (0x0204) — when star tracker reacquires
        for unit in (1, 2):
            status_attr = f"st{unit}_status"
            prev_attr = f"_prev_st{unit}_status"
            curr_status = getattr(s, status_attr)
            prev_status = getattr(self, prev_attr, 0)
            if prev_status == 3 and curr_status == 2:  # BLIND -> TRACKING
                events.append({
                    'event_id': 0x0204,
                    'severity': 'INFO',
                    'description': f'Star tracker {unit} reacquired lock'
                })
            setattr(self, prev_attr, curr_status)

        # MOMENTUM_SATURATION (0x0205) — most-loaded wheel > 90 % of its
        # momentum envelope (per-axis-aware, matches 0x02BA)
        momentum_pct = self._h_env_frac(s)
        is_saturating = momentum_pct > 90.0
        if is_saturating and not self._prev_momentum_saturation:
            events.append({
                'event_id': 0x0205,
                'severity': 'MEDIUM',
                'description': f'Momentum saturation: {momentum_pct:.1f}%'
            })
        self._prev_momentum_saturation = is_saturating

        # AOCS_FAST_LOOP_BYPASSED (0x020D) — secular-mode entry/exit edges
        secular_now = (s.fidelity_mode == 1)
        if secular_now != s.prev_secular:
            events.append({
                'event_id': 0x020D,
                'severity': 'LOW',
                'description': (
                    'AOCS fast loop BYPASSED (secular mode, >60x accel)'
                    if secular_now else
                    'AOCS fast loop restored (full-fidelity dynamics)')
            })
        s.prev_secular = secular_now

        # ATTITUDE_ERROR_HIGH (0x0206) — rising-edge: fire once when pointing
        # error first exceeds the configured threshold, not every tick after.
        att_error_high = s.att_error > self._att_error_threshold_deg
        if att_error_high and not self._prev_att_error_high:
            events.append({
                'event_id': 0x0206,
                'severity': 'MEDIUM',
                'description': f'Attitude error exceeds threshold: {s.att_error:.2f} deg'
            })
        self._prev_att_error_high = att_error_high

        # DESATURATION_START (0x0207) — when entering DESAT mode
        if s.mode == MODE_DESAT and prev_mode != MODE_DESAT:
            events.append({
                'event_id': 0x0207,
                'severity': 'INFO',
                'description': 'Wheel desaturation sequence started'
            })

        # DESATURATION_COMPLETE (0x0208) — when exiting DESAT mode successfully
        if prev_mode == MODE_DESAT and s.mode != MODE_DESAT:
            events.append({
                'event_id': 0x0208,
                'severity': 'INFO',
                'description': 'Wheel desaturation sequence completed'
            })

        # GPS_LOCK_LOST (0x0209) — when GPS fix transitions from 3D to lower
        if self._prev_gps_fix >= 2 and s.gps_fix < 2:
            events.append({
                'event_id': 0x0209,
                'severity': 'MEDIUM',
                'description': 'GPS lock lost'
            })
        self._prev_gps_fix = s.gps_fix

        # GPS_LOCK_ACQUIRED (0x020A) — when GPS fix transitions to 3D or better
        if self._prev_gps_fix < 2 and s.gps_fix >= 2:
            events.append({
                'event_id': 0x020A,
                'severity': 'INFO',
                'description': f'GPS lock acquired (fix={s.gps_fix})'
            })

        # GYRO_BIAS_HIGH (0x020B) — rising-edge: fire once when bias drift first
        # exceeds the threshold, not every tick after.
        bias_mag = math.sqrt(sum(b*b for b in self._gyro_bias))
        gyro_bias_high = bias_mag > 0.05  # deg/s threshold
        if gyro_bias_high and not self._prev_gyro_bias_high:
            events.append({
                'event_id': 0x020B,
                'severity': 'LOW',
                'description': f'Gyro bias drift high: {bias_mag:.5f} deg/s'
            })
        self._prev_gyro_bias_high = gyro_bias_high

        # CSS_DEGRADED (0x020C) — rising-edge: fire once when CSS first goes
        # degraded (2+ heads failed and composite invalid), not every tick.
        failed_count = sum(1 for failed in s.css_head_failed.values() if failed)
        css_degraded = failed_count >= 2 and not s.css_valid
        if css_degraded and not self._prev_css_degraded:
            events.append({
                'event_id': 0x020C,
                'severity': 'MEDIUM',
                'description': f'CSS degraded: {failed_count} heads failed'
            })
        self._prev_css_degraded = css_degraded

        # Dispatch all events to the engine (defect #23: drained via _emit_event)
        if hasattr(self, '_engine') and self._engine:
            for event in events:
                self._engine._model_event_queue.put(event)

    # ─── Command handling ────────────────────────────────────────────

    def handle_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        command = cmd.get("command", "")

        if command == "set_mode":
            mode = int(cmd.get("mode", 0))
            if not (0 <= mode <= 9):
                return {"success": False, "message": f"Invalid mode: {mode}"}
            if mode == MODE_FINE_POINT and self._state.mode != MODE_FINE_POINT:
                # WP-6: FINE entry is gated on the 0x02C5 precondition
                # bitmask — refuse loudly instead of accept-then-drop-back.
                mask = self._state.fine_ready_mask
                if mask != 0:
                    return {"success": False,
                            "code": "FINE_NOT_READY",
                            "fine_ready_mask": mask,
                            "message": (
                                f"FINE_POINT refused: preconditions unmet "
                                f"(0x02C5 bitmask=0x{mask:02X})")}
            self._set_mode(mode)
            return {"success": True}

        elif command == "desaturate":
            self._set_mode(MODE_DESAT)
            return {"success": True}

        elif command == "slew_to":
            q = cmd.get("quaternion", [0, 0, 0, 1])
            s = self._state
            s.slew_target_q = list(q)
            s.slew_start_q = list(s.q_true)
            s.slew_elapsed = 0.0
            self._set_mode(MODE_SLEW)
            return {"success": True}

        elif command == "set_pointing_frame":
            frame = int(cmd.get("frame", 1))
            if frame not in (0, 1, 2, 3):
                return {"success": False,
                        "message": "Frame must be 0=INERTIAL, 1=LVLH_NADIR, "
                                   "2=TARGET_TRACK, 3=SUN"}
            self._state.pointing_frame = frame
            return {"success": True}

        elif command == "set_target_quat":
            q = cmd.get("quaternion", [0, 0, 0, 1])
            if len(q) != 4:
                return {"success": False, "message": "Quaternion must be [x, y, z, w]"}
            mag = math.sqrt(sum(float(x) ** 2 for x in q))
            if mag < 1e-6:
                return {"success": False, "message": "Zero-norm quaternion"}
            self._state.target_q_cmd = [float(x) / mag for x in q]
            return {"success": True}

        elif command == "set_target_geo":
            lat = float(cmd.get("lat", 0.0))
            lon = float(cmd.get("lon", 0.0))
            alt_m = float(cmd.get("alt_m", 0.0))
            if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 360.0):
                return {"success": False, "message": "Invalid lat/lon"}
            self._state.target_geo = [lat, lon, alt_m]
            self._state.target_geo_valid = True
            return {"success": True}

        elif command == "set_control_bw":
            omega_n = float(cmd.get("omega_n", 0.0))
            zeta = float(cmd.get("zeta", 0.0))
            if omega_n and not (0.001 <= omega_n <= 0.2):
                return {"success": False,
                        "message": "omega_n must be 0.001-0.2 rad/s"}
            if zeta and not (0.1 <= zeta <= 2.0):
                return {"success": False, "message": "zeta must be 0.1-2.0"}
            self._state.ctrl_omega_n = omega_n
            self._state.ctrl_zeta = zeta
            return {"success": True}

        elif command == "fine_ready_check":
            return {"success": True,
                    "fine_ready_mask": self._state.fine_ready_mask}

        elif command == "set_mom_mgmt":
            s = self._state
            s.mom_mgmt_enable = bool(cmd.get("enable", False))
            entry = float(cmd.get("entry_pct", s.mom_mgmt_entry_pct))
            exit_ = float(cmd.get("exit_pct", s.mom_mgmt_exit_pct))
            if not (0.0 < exit_ < entry <= 100.0):
                return {"success": False,
                        "message": "Require 0 < exit < entry <= 100"}
            s.mom_mgmt_entry_pct = entry
            s.mom_mgmt_exit_pct = exit_
            return {"success": True}

        elif command == "est_reset":
            warm = int(cmd.get("mode", 0)) == 1
            self._ekf.reset(q0=list(self._state.q) if warm else None,
                            warm=warm)
            self._state.fine_conv_timer = 0.0
            return {"success": True,
                    "message": f"Estimator {'warm' if warm else 'cold'} reset"}

        elif command == "disable_wheel":
            idx = int(cmd.get("wheel", 0))
            if 0 <= idx < 4:
                self._state.active_wheels[idx] = False
                self._state.rw_enabled[idx] = False
                return {"success": True}
            return {"success": False, "message": "Invalid wheel index"}

        elif command == "enable_wheel":
            idx = int(cmd.get("wheel", 0))
            if 0 <= idx < 4:
                self._state.active_wheels[idx] = True
                self._state.rw_enabled[idx] = True
                return {"success": True}
            return {"success": False, "message": "Invalid wheel index"}

        elif command == "st_power":
            unit = int(cmd.get("unit", 1))
            on = bool(cmd.get("on", True))
            if unit not in (1, 2):
                return {"success": False, "message": "Invalid ST unit"}
            status_attr = f"st{unit}_status"
            failed_attr = f"st{unit}_failed"
            boot_attr = f"st{unit}_boot_timer"
            if getattr(self._state, failed_attr):
                return {"success": False, "message": f"ST{unit} failed"}
            if on:
                setattr(self._state, status_attr, 1)  # BOOTING
                setattr(self._state, boot_attr, 0.0)
            else:
                setattr(self._state, status_attr, 0)  # OFF
                setattr(self._state, boot_attr, 0.0)
            return {"success": True}

        elif command == "st_select":
            unit = int(cmd.get("unit", 1))
            if unit not in (1, 2):
                return {"success": False, "message": "Invalid ST unit"}
            st_status = getattr(self._state, f"st{unit}_status")
            if st_status == 0:
                return {"success": False, "message": f"ST{unit} is OFF"}
            self._state.st_selected = unit
            return {"success": True}

        elif command == "mag_select":
            source = cmd.get("source", cmd.get("on", "A"))
            # Support legacy boolean interface (on=True/False)
            if isinstance(source, bool):
                if self._state.mag_failed and source:
                    return {"success": False, "message": "Magnetometer failed"}
                self._state.mag_valid = source
                return {"success": True}
            # New dual-mag select interface (source='A' or 'B')
            source = str(source).upper()
            if source in ('A', 'B'):
                s = self._state
                if source == 'A' and s.mag_a_failed:
                    return {"success": False, "message": "Mag A failed"}
                if source == 'B' and s.mag_b_failed:
                    return {"success": False, "message": "Mag B failed"}
                s.mag_select = source
                return {"success": True}
            return {"success": False, "message": "Invalid mag source, use A or B"}

        elif command == "rw_set_speed_bias":
            wheel = int(cmd.get("wheel", 0))
            bias = float(cmd.get("bias", 0.0))
            if 0 <= wheel < 4 and self._state.active_wheels[wheel]:
                self._state.rw_speed[wheel] += bias
                self._state.h_wheel[wheel] = (
                    self._rw_inertia * self._state.rw_speed[wheel] * _RPM2RAD)
                return {"success": True}
            return {"success": False, "message": "Wheel inactive or invalid"}

        elif command == "mtq_enable":
            self._state.mtq_enabled = True
            return {"success": True}

        elif command == "mtq_disable":
            self._state.mtq_enabled = False
            self._state.mtq_x_duty = 0.0
            self._state.mtq_y_duty = 0.0
            self._state.mtq_z_duty = 0.0
            return {"success": True}

        elif command == "slew_to_quaternion":
            q = cmd.get("quaternion", [0, 0, 0, 1])
            rate_dps = float(cmd.get("rate_dps", 0.5))
            if len(q) != 4:
                return {"success": False, "message": "Quaternion must be [x, y, z, w]"}
            if rate_dps <= 0 or rate_dps > 10.0:
                return {"success": False, "message": "Slew rate must be 0-10 deg/s"}
            s = self._state
            s.slew_target_q = list(q)
            s.slew_rate_dps = rate_dps
            s.slew_start_q = list(s.q_true)
            s.slew_elapsed = 0.0
            self._set_mode(MODE_SLEW)
            return {"success": True, "message": "Slew maneuver started"}

        elif command == "check_momentum":
            return {
                "success": True,
                "total_momentum_nms": self._state.total_momentum,
                "saturation_percent": self._h_env_frac(self._state),
            }

        elif command == "begin_acquisition":
            # Automated acquisition sequence: DETUMBLE -> COARSE_SUN -> NOMINAL
            if self._state.mode == MODE_OFF or self._state.mode == MODE_SAFE_BOOT:
                self._set_mode(MODE_DETUMBLE)
                return {"success": True, "message": "Acquisition sequence started (DETUMBLE)"}
            return {"success": False, "message": "Cannot start acquisition from current mode"}

        elif command == "gyro_calibration":
            # Reset gyro bias estimate
            self._gyro_bias = [0.0, 0.0, 0.0]
            return {"success": True, "message": "Gyro calibration complete"}

        elif command == "rw_ramp_down":
            wheel = int(cmd.get("wheel", -1))
            target_rpm = float(cmd.get("target_rpm", 0.0))
            s = self._state
            if wheel == -1 or wheel == 255:
                # Ramp down all active wheels
                for i in range(4):
                    if s.active_wheels[i]:
                        s.rw_speed[i] = target_rpm
                        s.h_wheel[i] = self._rw_inertia * target_rpm * _RPM2RAD
                return {"success": True, "message": "All wheels ramping down"}
            elif 0 <= wheel < 4:
                s.rw_speed[wheel] = target_rpm
                s.h_wheel[wheel] = self._rw_inertia * target_rpm * _RPM2RAD
                return {"success": True, "message": f"Wheel {wheel} ramping down"}
            return {"success": False, "message": "Invalid wheel index"}

        elif command == "set_deadband":
            deadband_deg = float(cmd.get("deadband_deg", 0.01))
            if deadband_deg < 0 or deadband_deg > 1.0:
                return {"success": False, "message": "Deadband must be 0-1 degree"}
            # Phase C: a REAL control deadband (FINE limit-cycle width) — and,
            # preserving the historical wiring, it still writes the S5
            # ATTITUDE_ERROR_HIGH event threshold.
            self._state.deadband_deg = deadband_deg
            self._att_error_threshold_deg = deadband_deg
            return {"success": True, "message": f"Attitude error deadband set to {deadband_deg} deg"}

        elif command == "gps_set_start_mode":
            # Set GPS start mode: 0=COLD, 1=WARM, 2=HOT (defects/reviews/aocs.md)
            start_mode = int(cmd.get("start_mode", 0))
            if start_mode not in (0, 1, 2):
                return {"success": False, "message": "Start mode must be 0(COLD), 1(WARM), or 2(HOT)"}
            self._state.gps_start_mode = start_mode
            self._state.gps_ttff_timer = 0.0  # Reset timer when changing start mode
            return {"success": True, "message": f"GPS start mode set to {['COLD', 'WARM', 'HOT'][start_mode]}"}

        elif command == "tle_upload":
            # TLE upload command (defects/reviews/aocs.md Defect #2)
            line1 = cmd.get("line1", "")
            line2 = cmd.get("line2", "")
            if not line1 or not line2 or len(line1) < 60 or len(line2) < 60:
                return {"success": False, "message": "TLE lines must be >= 60 characters each"}
            self._state.tle_line1 = line1
            self._state.tle_line2 = line2
            self._state.tle_valid = True
            self._state.tle_last_upload_time = 0.0  # Would be current mission time in real system
            self._state.tle_validity_timer = 0.0
            return {"success": True, "message": "TLE uploaded and validated"}

        return {"success": False, "message": f"Unknown: {command}"}

    # ─── Failure injection ───────────────────────────────────────────

    def inject_failure(self, failure: str, magnitude: float = 1.0,
                       **kw) -> None:
        s = self._state

        if failure == "rw_bearing":
            w = int(kw.get("wheel", 0))
            self._bearing_degradation[w] = max(0.0, min(1.0, magnitude))
            if magnitude >= 0.95:
                s.active_wheels[w] = False

        elif failure == "rw_seizure":
            w = int(kw.get("wheel", 0))
            self._bearing_degradation[w] = 1.0
            s.active_wheels[w] = False
            s.rw_speed[w] = 0.0

        elif failure == "gyro_bias":
            axis = int(kw.get("axis", 0))
            self._gyro_bias[axis] = float(kw.get("bias", 0.05))

        elif failure == "st_blind":
            # Temporary blinding of selected tracker
            sel = s.st_selected
            if magnitude:
                setattr(s, f"st{sel}_status", 3)  # BLIND
                self._gyro_bias = [self.rng.gauss(0, 0.005) for _ in range(3)]
            else:
                setattr(s, f"st{sel}_status", 2)  # TRACKING

        elif failure == "st_failure":
            # Permanent hardware failure of a specific unit
            unit = int(kw.get("unit", 1))
            setattr(s, f"st{unit}_failed", True)
            setattr(s, f"st{unit}_status", 4)  # FAILED
            setattr(s, f"st{unit}_num_stars", 0)

        elif failure == "css_failure":
            s.css_failed = True
            s.css_valid = False

        elif failure == "mag_failure":
            s.mag_failed = True
            s.mag_valid = False

        elif failure == "mtq_failure":
            axis = kw.get("axis", "x")
            setattr(s, f"mtq_{axis}_failed", True)

        elif failure == "mag_a_fail":
            s.mag_a_failed = True

        elif failure == "mag_b_fail":
            s.mag_b_failed = True

        elif failure == "css_head_fail":
            face = kw.get("face", "px")
            if face in s.css_head_failed:
                s.css_head_failed[face] = True

        elif failure == "multi_wheel_failure":
            # Fail 2+ wheels — forces safe mode
            wheels = kw.get("wheels", [0, 1])
            for w in wheels:
                if 0 <= w < 4:
                    self._bearing_degradation[w] = 1.0
                    s.active_wheels[w] = False
                    s.rw_speed[w] = 0.0
            n_active = sum(1 for a in s.active_wheels if a)
            if n_active < 3:
                self._set_mode(MODE_COARSE_SUN)

        elif failure == "gain_corruption":
            # Corrupt a per-axis control-torque gain register — models faulted
            # RW driver electronics writing a scale factor into the AOCS gain
            # block (base 0x20100000; X/Y/Z at +0/+4/+8) that PUS S6 MEM_DUMP
            # reads and MEM_LOAD patches. A high factor makes the control law
            # over-drive that body axis, so once a control mode is active the
            # attitude error diverges and FDIR trips safe_mode_aocs. The operator
            # diagnoses it by dumping the gains (they read ~factor x nominal on
            # the faulted axis) and recovers by loading the nominal gain back.
            axis = str(kw.get("axis", "y")).lower()
            factor = float(kw.get("factor", 10.0))
            if axis in ("x", "y", "z"):
                self._gain_corruption_saved.setdefault(axis, self.get_torque_gain(axis))
                self.set_torque_gain(axis, factor)
            else:
                logger.warning("AOCS gain_corruption: invalid axis %r (no-op)", axis)

        else:
            # Defect #25: previously an unrecognised failure name fell through
            # silently (a no-op injection), which is how aocs_wheel_failure.yaml
            # shipped a dead scenario. Log it so naming drift is caught.
            logger.warning("AOCS inject_failure: unknown failure type %r (no-op)", failure)

    def clear_failure(self, failure: str, **kw) -> None:
        s = self._state

        if failure == "rw_bearing":
            w = int(kw.get("wheel", 0))
            self._bearing_degradation[w] = 0.0

        elif failure == "rw_seizure":
            w = int(kw.get("wheel", 0))
            self._bearing_degradation[w] = 0.0
            # Wheel stays disabled — must be re-enabled via command

        elif failure == "gyro_bias":
            self._gyro_bias = [0.0, 0.0, 0.0]

        elif failure in ("st_blind", "st_failure"):
            unit = int(kw.get("unit", s.st_selected))
            setattr(s, f"st{unit}_failed", False)
            setattr(s, f"st{unit}_status", 2)  # TRACKING
            if failure == "st_blind":
                self._gyro_bias = [0.0, 0.0, 0.0]

        elif failure == "css_failure":
            s.css_failed = False

        elif failure == "mag_failure":
            s.mag_failed = False

        elif failure == "mtq_failure":
            axis = kw.get("axis", "x")
            setattr(s, f"mtq_{axis}_failed", False)

        elif failure == "mag_a_fail":
            s.mag_a_failed = False

        elif failure == "mag_b_fail":
            s.mag_b_failed = False

        elif failure == "css_head_fail":
            face = kw.get("face", "px")
            if face in s.css_head_failed:
                s.css_head_failed[face] = False

        elif failure == "multi_wheel_failure":
            wheels = kw.get("wheels", [0, 1])
            for w in wheels:
                if 0 <= w < 4:
                    self._bearing_degradation[w] = 0.0

        elif failure == "gain_corruption":
            # Restore the pre-corruption gain on the affected axis (unity unless
            # the operator had deliberately set a non-unity gain beforehand).
            axis = str(kw.get("axis", "y")).lower()
            if axis in ("x", "y", "z"):
                restore = self._gain_corruption_saved.pop(axis, 1.0)
                self.set_torque_gain(axis, restore)

    # ─── State save/restore ──────────────────────────────────────────

    # Model-level (non-dataclass) entries carried in the state dict.
    _EXTRA_STATE_KEYS = ("_gyro_bias", "_bearing_degradation",
                         "_gain_corruption_saved", "_rng_state", "_ekf")

    def get_state(self) -> dict[str, Any]:
        import dataclasses
        from smo_common.state import rng_state_to_json
        d = dataclasses.asdict(self._state)
        d["_gyro_bias"] = list(self._gyro_bias)
        d["_bearing_degradation"] = list(self._bearing_degradation)
        d["_gain_corruption_saved"] = dict(self._gain_corruption_saved)
        d["_rng_state"] = rng_state_to_json(self.rng.getstate())
        d["_ekf"] = self._ekf.get_state()
        return d

    def set_state(self, state: dict[str, Any]) -> None:
        # NOTE: must not mutate `state` — it may be a stored breakpoint dict
        # that will be loaded again (the old .pop() here corrupted snapshots
        # on their first load).
        from smo_common.state import restore_dataclass_state, restore_rng
        restore_rng(self.rng, state.get("_rng_state"))
        self._gyro_bias = list(state.get("_gyro_bias", [0.0] * 3))
        self._bearing_degradation = list(state.get("_bearing_degradation", [0.0] * 4))
        self._gain_corruption_saved = dict(state.get("_gain_corruption_saved", {}))
        ekf_state = state.get("_ekf")
        if ekf_state is not None:
            self._ekf.set_state(ekf_state)
        # Deep-copies mutable values: assigning the stored list itself would
        # alias the live model state onto the breakpoint dict — the model
        # would then silently corrupt the snapshot on every subsequent tick
        # (and two models restored from one snapshot would share state).
        restore_dataclass_state(self._state, state,
                                skip=self._EXTRA_STATE_KEYS)

    # ─── Quaternion math utilities ───────────────────────────────────

    @staticmethod
    def _normalise(q):
        mag = math.sqrt(sum(x * x for x in q))
        return [x / (mag + 1e-9) for x in q]

    @staticmethod
    def _quat_angle_error(q, q_tgt):
        dot = sum(a * b for a, b in zip(q, q_tgt))
        dot = max(-1.0, min(1.0, abs(dot)))
        return 2.0 * math.acos(dot) * _RAD

    @staticmethod
    def _quat_error_axis(q, q_tgt):
        ax = q_tgt[0] - q[0]
        ay = q_tgt[1] - q[1]
        az = q_tgt[2] - q[2]
        mag = math.sqrt(ax * ax + ay * ay + az * az) + 1e-9
        return [ax / mag, ay / mag, az / mag]

    @staticmethod
    def _rotate_quat(q, axis, angle_rad):
        s_half = math.sin(angle_rad / 2)
        c_half = math.cos(angle_rad / 2)
        dq = [axis[0] * s_half, axis[1] * s_half, axis[2] * s_half, c_half]
        q1, q2, q3, q4 = q
        d1, d2, d3, d4 = dq
        return [
            d4 * q1 + d3 * q2 - d2 * q3 + d1 * q4,
            -d3 * q1 + d4 * q2 + d1 * q3 + d2 * q4,
            d2 * q1 - d1 * q2 + d4 * q3 + d3 * q4,
            -d1 * q1 - d2 * q2 - d3 * q3 + d4 * q4,
        ]

    @staticmethod
    def _slerp_quat(q0, q1, t):
        """Spherical Linear Interpolation (SLERP) between two quaternions.

        Args:
            q0: Start quaternion [x, y, z, w]
            q1: End quaternion [x, y, z, w]
            t: Interpolation parameter [0, 1] where 0=q0 and 1=q1

        Returns:
            Interpolated quaternion
        """
        # Clamp t to [0, 1]
        t = max(0.0, min(1.0, t))

        # Compute dot product
        dot = sum(a * b for a, b in zip(q0, q1))

        # If dot product is negative, negate one quaternion to take shorter path
        if dot < 0.0:
            q1 = [-x for x in q1]
            dot = -dot

        # Clamp dot to avoid numerical issues with acos
        dot = max(-1.0, min(1.0, dot))

        # If quaternions are very close, use linear interpolation
        if dot > 0.9995:
            result = [q0[i] + t * (q1[i] - q0[i]) for i in range(4)]
            return AOCSBasicModel._normalise(result)

        # Calculate angle between quaternions
        theta = math.acos(dot)
        sin_theta = math.sin(theta)

        # SLERP formula
        w0 = math.sin((1.0 - t) * theta) / sin_theta
        w1 = math.sin(t * theta) / sin_theta

        result = [w0 * q0[i] + w1 * q1[i] for i in range(4)]
        return AOCSBasicModel._normalise(result)

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

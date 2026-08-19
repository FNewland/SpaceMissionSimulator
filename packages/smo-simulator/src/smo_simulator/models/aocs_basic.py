"""SMO Simulator — AOCS with real attitude dynamics (Phase C rebuild).

Rigid-body truth dynamics (RK4, fixed h = 0.5 s, sub-stepped inside the
model tick), pyramid reaction wheels with torque limits / motor lag /
friction, magnetorquer physics, attitude-dependent sensor models, a 6-state
MEKF, quaternion-feedback PD control with orbit-rate feed-forward, LVLH /
inertial / sun / target reference generation (new mode 9 = INERTIAL),
gravity-gradient + aero + SRP + residual-dipole disturbance torques, and a
declared SECULAR fast-forward mode above 60x time acceleration.

Retains the original 9-mode state machine + guards, dual star-tracker /
CSS health FSM, the single magnetometer, GPS TTFF model, S2 device
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
from smo_common.protocol.pus_services import failure_code_text
from smo_simulator.failure_spec import (
    FailureSpec, ParamSpec, T_FLOAT, T_BOOL, enum, number,
)
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

    # Magnetometer — ONE 3-axis unit (owner ruling 2026-08c: no redundancy,
    # like the gyros — no spare, failure teaching via injection only).
    # mag_x/y/z are the MEASURED body-frame field (IGRF truth rotated into
    # the body frame + bias + noise), zeroed when unpowered/failed.
    mag_x: float = 25000.0
    mag_y: float = 10000.0
    mag_z: float = -40000.0
    mag_valid: bool = True
    mag_bias: tuple = (0.0, 0.0, 0.0)
    mag_noise: float = 50.0   # σ in nT

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

    # ── Gyro bias calibration (S8.1 func 13 AOCS_GYRO_CALIBRATION) ──
    # Every field here lives in the dataclass on purpose: an in-flight
    # calibration, its accumulator AND the compensation it produced all have to
    # survive a breakpoint save/load. A compensation that vanished on restore
    # would silently un-calibrate the gyros, and an accumulator that vanished
    # would make a restored mid-run calibration finish on a partial average —
    # the "persistent state that heals on restore" bug this codebase keeps
    # re-growing.
    # 0=IDLE, 1=RUNNING, 2=COMPLETE, 3=ABORTED
    gyro_cal_state: int = 0
    gyro_cal_elapsed: float = 0.0        # s into the current arc
    gyro_cal_duration_s: float = 300.0   # commanded arc length
    gyro_cal_settle_s: float = 30.0      # discard the first N s of the arc
    gyro_cal_progress: float = 0.0       # %
    # Running sum / count of the observed bias [deg/s] over the averaging arc.
    gyro_cal_acc: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    gyro_cal_n: int = 0
    # Result of the LAST completed calibration [deg/s per body axis].
    gyro_cal_bias: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    # Compensation currently applied on the measurement path [deg/s]. This is
    # the calibration's only lasting effect: it cancels the hardware bias at
    # the gyro output. It does NOT touch `_gyro_bias_inject` — an injected
    # gyro_bias fault is a HARDWARE defect and stays exactly where the
    # instructor put it (visible in 0x0296), which is the teaching point.
    gyro_cal_comp: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    gyro_cal_result: int = 0             # 0=never run, 1=SUCCESS, 2=FAILED
    gyro_cal_reject: int = 0             # last refusal/abort failure code

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

    # ── Commanded-mode memory (finding A-a) ──
    # `mode` is where the spacecraft IS; `commanded_mode` is the operational
    # mode the ground last asked for. Every transient (ECLIPSE, DESAT, SLEW)
    # and every automatic demotion (FDIR safing, sensor loss) must have a
    # documented way back to `commanded_mode`, or the spacecraft silently
    # abandons its mission mode and never returns — which is exactly what the
    # instrumented run showed (NOMINAL -> ECLIPSE -> COARSE_SUN, six orbits).
    commanded_mode: int = 0
    # Why the last transition happened, for the 0x0200 event description.
    mode_change_reason: str = "init"
    # 1 while the spacecraft is somewhere other than its commanded mode.
    mode_demoted: bool = False
    # Mode to restore when the transient completes (DESAT / SLEW).
    return_mode: int = 0
    # Sustain timer for the automatic NOMINAL -> FINE_POINT re-promotion.
    fine_promote_timer: float = 0.0
    # FINE running on a degraded (3-wheel) actuator set — the mode is
    # permitted (R3) but the jitter/deadband spec is relaxed.
    fine_degraded: bool = False
    # Desaturation inhibit: set by the operator, and asserted automatically
    # while the payload is imaging so a dump cannot pre-empt a strip.
    desat_inhibit: bool = False
    desat_inhibit_auto: bool = False
    # DESAT diagnostics
    desat_h0_nms: float = 0.0        # body momentum at DESAT entry
    desat_ineffective: bool = False

    # Latched once the reaction-wheel assembly has ever been commissioned
    # (>= 3 wheels enabled at the same time). FDIR needs this to tell "two
    # wheels have failed" apart from "the wheels have not been switched on
    # yet" — see fault_propagation.yaml aocs_wheel_failure_dual. Cleared only
    # by an AOCS power cycle (MODE_OFF), which genuinely restarts commissioning.
    wheels_commissioned: bool = False
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
    # Mode seen by the LAST event pass. The 0x0200 edge cannot be detected
    # from the mode-tick's local `prev_mode`: autonomous transitions
    # (_check_auto_transitions — eclipse entry, FDIR demotions) happen EARLIER
    # in the same tick, and commanded ones (handle_command) happen between
    # ticks, so both were already applied by the time that local was taken and
    # neither produced an event. Persistent state sees all three.
    prev_event_mode: int = 0

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
        0x0209: True,   # Magnetometer (the ONE unit — no redundant B unit;
                        # 0x020A retired with the fabricated Magnetometer B)
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
        # Deferred-completion reports drained by the engine (2026-08 ruling).
        self._completions: list[dict] = []
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
        self._gyro_max_rate_dps = 400.0     # measurement range (tactical IMU class)
        self._gyro_tempco_dps_per_k = 2.0e-6  # bias tempco about 22 °C
        # Density used for an axis with NO rate measurement (unit off /
        # saturated): the attitude error there grows at the unknown body rate.
        self._gyro_dead_arw_dps_rts = 0.1
        # Gyro bias calibration spec (aocs.yaml sensors.gyro.calibration)
        self._gyro_cal_duration_s = 300.0
        self._gyro_cal_min_duration_s = 30.0
        self._gyro_cal_max_duration_s = 1800.0
        self._gyro_cal_max_rate_dps = 0.10
        self._gyro_cal_settle_s = 30.0
        # Injected constant bias per axis [deg/s] — the failure surface the
        # instructor book advertises as "IMU drifts" (scenario `gyro_bias`).
        self._gyro_bias_inject = [0.0, 0.0, 0.0]
        self._gyro_temp_bias = 0.0
        self._gyro_saturated = False
        # Star trackers deliberately blinded by an injection (see the
        # `st_blind` failure). Persistent failure state — carried through
        # breakpoint save/load in `_EXTRA_STATE_KEYS`.
        self._st_blind_units: set[int] = set()
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
        # ── Desaturation law (aocs.yaml `desaturation:`) ──
        # k [s^-1] in m = k*(h x B)/|B|^2. MUST match aocs.yaml
        # magnetorquers.desaturation.gain_per_s — this is the value a
        # standalone `configure({})` model gets, and a mismatch here means the
        # unit tests measure a different spacecraft from the simulator.
        self._desat_gain_per_s = 0.002
        self._desat_deadband_nms = 5.0e-4     # 0.5 mN.m.s: stop, don't chatter
        self._desat_timeout_s = 3600.0        # see aocs.yaml `desaturation:`
        # Bang-bang (rods flat out) is OFF by default: measured, it is WORSE
        # than the linear law — see the desaturation block in aocs.yaml.
        self._desat_saturate = False
        # B-dot gain [A.m2 / (T/s)]. Sized against a realistic separation
        # tumble: at |B| = 3e-5 T the damping torque is k|B|^2*w, so the rate
        # time constant is tau = I/(k|B|^2). With I = 0.18 kg.m2 and the
        # 6U rod set, k = 5e5 gives tau = 0.18/(5e5*9e-10) = 400 s, i.e. a
        # 10 deg/s separation tumble is inside the 0.5 deg/s DETUMBLE exit
        # guard in ~3*tau = 20 min, and the commanded dipole at 10 deg/s
        # (|Bdot| ~ 5e-6 T/s) is 2.6 A.m2 -> saturated, as it should be.
        self._bdot_gain = 5.0e5
        self._bdot_prev_B = None
        self._bdot_prev_dt = 0.0
        self._bdot_filt = np.zeros(3)
        # Previous reference quaternion per frame, for the numerical
        # reference-rate feed-forward (see _ref_rate_from_history).
        self._ref_hist: dict = {}
        # FINE deadband multiplier when running on a degraded (3-wheel) set
        self._fine_degraded_db_factor = 4.0
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
    _prev_event_mode = _mk_prev("prev_event_mode")
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
        self._gyro_max_rate_dps = float(gy.get("max_rate_dps", 400.0))
        self._gyro_tempco_dps_per_k = float(
            gy.get("tempco_dps_per_k", 2.0e-6))
        self._gyro_dead_arw_dps_rts = float(
            gy.get("dead_axis_arw_dps_rtsec", 0.1))
        gcal = gy.get("calibration", {}) or {}
        self._gyro_cal_duration_s = float(gcal.get("duration_s", 300.0))
        self._gyro_cal_min_duration_s = float(
            gcal.get("min_duration_s", 30.0))
        self._gyro_cal_max_duration_s = float(
            gcal.get("max_duration_s", 1800.0))
        self._gyro_cal_max_rate_dps = float(gcal.get("max_rate_dps", 0.10))
        self._gyro_cal_settle_s = float(gcal.get("settle_s", 30.0))
        self._state.gyro_cal_duration_s = self._gyro_cal_duration_s
        self._state.gyro_cal_settle_s = self._gyro_cal_settle_s
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

        # The dump law lives under `magnetorquers:` — it IS the magnetorquer
        # control law, and AOCSConfig is a StrictModel whose free-form blocks
        # are the only place model-owned sub-config can go.
        des = mtq.get("desaturation", config.get("desaturation", {}))
        self._desat_gain_per_s = float(des.get("gain_per_s", 0.002))
        self._desat_deadband_nms = float(des.get("deadband_nms", 5.0e-4))
        self._desat_timeout_s = float(des.get("timeout_s", 3600.0))
        self._desat_saturate = bool(des.get("saturate_rods", False))
        self._bdot_gain = float(mtq.get("bdot_gain_am2_per_tps", 5.0e5))


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

    # Modes the ground can hold as an OPERATIONAL mode. Everything else is a
    # transient (SLEW, DESAT, ECLIPSE) or a safing state (DETUMBLE, SAFE_BOOT)
    # that must eventually hand control back to one of these.
    #
    # COARSE_SUN is deliberately NOT in this set. aocs_mode_codes.yaml defines
    # mode 3 as "Sun-pointing via coarse sun sensors. PREREQUISITE FOR NOMINAL"
    # — a hold on the way somewhere, not a destination. It also has to stay out
    # because FDIR safing reaches the model through the same
    # `set_mode` command an operator uses (engine `safe_mode_aocs`): if
    # commanding mode 3 latched it as the commanded mode, every FDIR safing
    # would erase the mission mode it was supposed to protect, which is exactly
    # the failure the live run showed.
    _OPERATIONAL_MODES = (MODE_NOMINAL, MODE_FINE_POINT, MODE_INERTIAL)

    def _set_mode(self, new_mode: int, reason: str = "") -> None:
        """Transition to a new AOCS mode, resetting timers.

        ``reason`` is carried into the 0x0200 AOCS_MODE_CHANGE event so every
        transition — commanded, automatic or FDIR-driven — says on the ground
        WHY it happened and, for a demotion, what the way back is."""
        s = self._state
        if s.mode == new_mode:
            return
        s.mode = new_mode
        s.time_in_mode = 0.0
        s._guard_timer = 0.0
        s.submode = 0
        s.ctrl_integ = [0.0, 0.0, 0.0]
        s.mode_change_reason = reason or "unspecified"
        s.mode_demoted = (s.commanded_mode in self._OPERATIONAL_MODES
                          and new_mode != s.commanded_mode)
        if new_mode != MODE_FINE_POINT:
            s.fine_degraded = False
        s.fine_promote_timer = 0.0

    def _resume_target(self, s: AOCSState,
                       fallback: int = MODE_NOMINAL) -> int:
        """The mode to return to when a transient or demotion clears.

        FINE_POINT is never re-entered directly — its 0x02C5 preconditions
        need settled wheels and a sustained MEKF convergence that cannot be
        true at the instant a transient ends. The spacecraft returns to
        NOMINAL and `_check_fine_promotion` promotes it back to FINE once the
        preconditions have actually been met for 30 s. That is the documented
        return path for the only automatic demotion that is not instant."""
        target = s.commanded_mode
        if target not in self._OPERATIONAL_MODES:
            return fallback
        if target == MODE_FINE_POINT:
            return MODE_NOMINAL
        if target in (MODE_NOMINAL, MODE_INERTIAL) and not s.st_valid:
            # No inertial attitude reference — sun-safe is the honest fallback.
            return MODE_COARSE_SUN
        return target

    def _check_fine_promotion(self, s: AOCSState, dt: float) -> None:
        """NOMINAL -> FINE_POINT re-promotion when FINE is the commanded mode.

        Without this, every eclipse, desat or safing episode permanently costs
        the mission its fine-pointing mode: the ground command that selected
        FINE is consumed once and never reasserted."""
        if s.commanded_mode != MODE_FINE_POINT or s.mode != MODE_NOMINAL:
            s.fine_promote_timer = 0.0
            return
        if s.fine_ready_mask != 0:
            s.fine_promote_timer = 0.0
            return
        s.fine_promote_timer += dt
        if s.fine_promote_timer >= 30.0:
            self._set_mode(MODE_FINE_POINT,
                           reason="preconditions met — resuming commanded FINE")

    def _check_emergency(self, s: AOCSState) -> bool:
        """Check for emergency conditions that force mode transitions."""
        rate_mag = math.sqrt(
            s.rate_roll ** 2 + s.rate_pitch ** 2 + s.rate_yaw ** 2
        )
        if rate_mag > self._emergency_rate_threshold and s.mode not in (
            MODE_OFF, MODE_DETUMBLE, MODE_SAFE_BOOT
        ):
            self._set_mode(
                MODE_DETUMBLE,
                reason=(f"EMERGENCY body rate {rate_mag:.2f} deg/s > "
                        f"{self._emergency_rate_threshold:.2f}; return path "
                        f"DETUMBLE -> COARSE_SUN -> "
                        f"{_MODE_NAMES.get(s.commanded_mode, 'NOMINAL')}"))
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
                self._set_mode(MODE_DETUMBLE,
                               reason="SAFE_BOOT hardware init complete")

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
                self._set_mode(
                    MODE_COARSE_SUN,
                    reason=(f"rates damped below 0.5 deg/s for 30 s; next "
                            f"COARSE_SUN -> "
                            f"{_MODE_NAMES.get(s.commanded_mode, 'NOMINAL')}"))

        elif s.mode == MODE_COARSE_SUN:
            # Guard: CSS valid AND sun-pointing error inside the COARSE band
            # for 60 s. Threshold raised 10 -> 15 deg: measured COARSE_SUN
            # holds 3.8 deg mean with 10.4 deg peaks, because the mode is
            # KNOWLEDGE-limited by the coarse sun sensor (six heads, 0.02
            # illumination noise, TRIAD against the magnetometer) and not by
            # the control. A 10 deg gate is barely 3 sigma on that
            # distribution, so a single excursion reset the 60 s timer and
            # could stall the promotion out of the safe hold indefinitely.
            # COARSE_SUN is a safe hold, never a destination: as soon as the
            # sun lock is good and the tracker is back, hand control to the
            # mode the ground actually asked for. Before this, a COARSE_SUN
            # demotion could only ever return to NOMINAL — so a FINE or
            # INERTIAL commanded mode was lost for the rest of the pass.
            if s.css_valid and s.att_error < 15.0:
                s._guard_timer += dt
            else:
                s._guard_timer = 0.0
            if s._guard_timer >= 60.0 and s.st_valid:
                # commanded_mode 0 (never commanded, e.g. LEOP acquisition)
                # keeps the historical NOMINAL destination.
                tgt = self._resume_target(s, fallback=MODE_NOMINAL)
                self._set_mode(
                    tgt, reason=("sun lock + star tracker recovered; resuming "
                                 f"commanded "
                                 f"{_MODE_NAMES.get(s.commanded_mode, 'NOMINAL')}"))

        elif s.mode in (MODE_NOMINAL, MODE_FINE_POINT):
            # INERTIAL is deliberately absent from this list: an ECI attitude
            # hold has no sun dependence at all, and ECLIPSE exists to say
            # "no sun reference is available". Demoting it would be a mode
            # change with nothing to gain and a return path to get wrong.
            # Eclipse entry -> ECLIPSE mode. Defect #3: auto-trigger on
            # in_eclipse. Phase C: FINE_POINT also yields to eclipse (root
            # cause 14 — it previously sailed through claiming ST knowledge).
            if orbit_state.in_eclipse and not self._prev_in_eclipse:
                s._guard_timer = 0.0  # Reset guard timer for eclipse debouncing
            if orbit_state.in_eclipse:
                s._guard_timer += dt
                # Debounce 5s before entering eclipse (hysteresis to avoid thrashing)
                if s._guard_timer >= 5.0:
                    self._set_mode(
                        MODE_ECLIPSE,
                        reason=("entered eclipse; will resume "
                                f"{_MODE_NAMES.get(s.commanded_mode, 'NOMINAL')}"
                                " at eclipse exit"))

        elif s.mode == MODE_ECLIPSE:
            # Eclipse exit -> resume the COMMANDED mode, not a hard-coded
            # NOMINAL. This is the transition that lost nadir pointing for six
            # orbits in the instrumented run.
            if not orbit_state.in_eclipse:
                tgt = self._resume_target(s, fallback=MODE_NOMINAL)
                self._set_mode(
                    tgt, reason=("eclipse exit; resuming commanded "
                                 f"{_MODE_NAMES.get(s.commanded_mode, 'NOMINAL')}"
                                 if s.st_valid else
                                 "eclipse exit with no star tracker — sun-safe hold"))

        elif s.mode == MODE_DESAT:
            self._check_desat_exit(s, dt)

    def _check_desat_exit(self, s: AOCSState, dt: float) -> None:
        """DESAT completion, timeout and return path.

        Three defects fixed here at once:
        * the old exit test looked only at WHEEL SPEED, which the null-space
          bias regulator can satisfy without removing a single N·m·s of BODY
          momentum — the thing a magnetic dump actually controls;
        * `mom_mgmt_exit_pct` was accepted from the operator (func 90) and read
          nowhere. It is the operational exit target, so it is now one of the
          completion tests;
        * there was NO timeout. A dump that cannot converge (weak field
          geometry, failed rod, inhibited MTQ) parked the spacecraft in DESAT
          for ever — off-pointing, out of contact, unrecoverable without an
          operator who happens to be watching.
        """
        h_body = float(np.linalg.norm(self._A @ np.array(s.h_wheel)))
        env_pct = self._h_env_frac(s)
        # Physical completion: body momentum inside the dump deadband, or the
        # envelope usage back under the operator's exit target.
        done = (h_body <= self._desat_deadband_nms
                or env_pct <= s.mom_mgmt_exit_pct)
        # Legacy wheel-speed completion retained: a wheel-speed-driven desat
        # (LEOP wheel run-down) is still a legitimate way to finish.
        done = done or all(
            abs(s.rw_speed[i]) <= self._rw_desat_speed + 100
            for i in range(4) if s.active_wheels[i])
        if done:
            s._guard_timer += dt
        else:
            s._guard_timer = 0.0
        if s._guard_timer >= 10.0:
            tgt = s.return_mode if s.return_mode in self._OPERATIONAL_MODES \
                else self._resume_target(s)
            if tgt == MODE_FINE_POINT:
                tgt = MODE_NOMINAL      # FINE re-entry goes via the promotion
            s.desat_ineffective = False
            self._set_mode(tgt, reason=(
                f"desaturation complete: |h_body| {s.desat_h0_nms * 1000:.2f} "
                f"-> {h_body * 1000:.2f} mN·m·s ({env_pct:.0f} % envelope); "
                f"resuming {_MODE_NAMES.get(tgt, '?')}"))
            return
        # Timeout: give up loudly rather than silently living in DESAT.
        if s.time_in_mode >= self._desat_timeout_s:
            s.desat_ineffective = True
            tgt = s.return_mode if s.return_mode in self._OPERATIONAL_MODES \
                else self._resume_target(s)
            if tgt == MODE_FINE_POINT:
                tgt = MODE_NOMINAL
            self._emit_model_event(
                0x020E, 'MEDIUM',
                f'Desaturation INEFFECTIVE after {s.time_in_mode:.0f} s: '
                f'|h_body| {s.desat_h0_nms * 1000:.2f} -> {h_body * 1000:.2f} '
                f'mN·m·s, {env_pct:.0f} % of envelope still loaded; '
                f'returning to {_MODE_NAMES.get(tgt, "?")}')
            self._set_mode(tgt, reason="desat timeout — dump ineffective")

    def _slew_return_target(self, s: AOCSState) -> int:
        """Where a completed SLEW hands control back to.

        A slew is a transient between two pointing states; ending it always in
        NOMINAL threw away an INERTIAL or FINE commanded mode every time the
        spacecraft manoeuvred."""
        tgt = s.return_mode if s.return_mode in self._OPERATIONAL_MODES \
            else self._resume_target(s)
        return MODE_NOMINAL if tgt == MODE_FINE_POINT else tgt

    def _desat_inhibited(self, s: AOCSState) -> bool:
        """True while a momentum dump must not be started.

        Either the operator has latched the inhibit (AOCS_SET_MOM_MGMT /
        AOCS_SET_DESAT_INHIBIT) or the payload is imaging."""
        return bool(s.desat_inhibit or s.desat_inhibit_auto)

    def _enter_desat(self, s: AOCSState, reason: str) -> None:
        """Enter DESAT, recording where to come back to and the entry loading."""
        if s.mode != MODE_DESAT:
            if s.return_mode not in self._OPERATIONAL_MODES:
                s.return_mode = s.mode if s.mode in self._OPERATIONAL_MODES \
                    else s.commanded_mode
            s.desat_h0_nms = float(
                np.linalg.norm(self._A @ np.array(s.h_wheel)))
            s.desat_ineffective = False
        self._set_mode(MODE_DESAT, reason=reason)

    def _emit_model_event(self, event_id: int, severity: str,
                          description: str) -> None:
        """Push a single event to the engine outside the tick-event sweep."""
        if getattr(self, '_engine', None):
            try:
                self._engine._model_event_queue.put({
                    'event_id': event_id, 'severity': severity,
                    'description': description})
            except Exception:      # queue full — telemetry, never fatal
                pass

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
            # An INJECTED blinding is sticky: `_tick_star_trackers` recomputes
            # blinding from sun geometry every tick, so the injection's
            # `st{n}_status = 3` used to be overwritten on the very next tick
            # and st_blind had no observable effect at all (it "healed" itself
            # instantly). The injected set is honoured here and cleared only by
            # clear_failure.
            blinded = unit in self._st_blind_units
            if not blinded and not orbit_state.in_eclipse:
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
        # Sun-pointing confidence (0x0215, on-demand): the pre-normalisation
        # composite magnitude, clamped to 0..1 — near 1 with one face square
        # to the sun, degrading toward 0 in eclipse, at grazing geometry or
        # with failed heads. Declared in the catalogue but never written
        # before (S20.3 returned a plausible 0.0).
        self._css_sun_confidence = max(0.0, min(1.0, mag))
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
        """The ONE magnetometer: B_body = C(q_true)·B_IGRF + bias + noise.

        WP-3: the field is the tilted-dipole IGRF evaluated at the true ECI
        position and rotated through the TRUE attitude — rotating the
        spacecraft now rotates the measured field.

        2026-08c (owner ruling, item 8): EO-Sat-1 flies a SINGLE 3-axis
        magnetometer — no redundant B unit, no prime/backup switch, exactly
        like the gyro fit. The old dual-mag A/B model (cross-strap, mag_select,
        S2 device 0x020A) fabricated a spare that does not exist; magnetometer
        failure teaching is by instructor injection (`mag_failure`) only.
        mag_x/y/z are the MEASURED values (noisy, failure-affected) and are
        zeroed — never frozen — when the unit is failed, unpowered (S2 device
        0x0209 off) or the AOCS suite is dark.
        """
        # Power/failure gate applied HERE rather than only in the post-tick
        # `_apply_device_power_gates` sweep: the sweep runs AFTER the mode
        # tick, so a powered-off magnetometer would still feed the control law
        # with a fresh reading for the whole tick. B-dot is a magnetometer-only
        # law, so this is the sensor whose loss it has to show.
        unit_on = s.device_states.get(0x0209, True)
        if (s.mag_failed or not unit_on
                or s.mode == MODE_OFF or s.mode == MODE_SAFE_BOOT):
            s.mag_valid = False
            s.mag_x = s.mag_y = s.mag_z = 0.0
            return

        true_x, true_y, true_z = (float(x) for x in self._true_B_body_nT())
        s.mag_x = true_x + s.mag_bias[0] + self.rng.gauss(0, s.mag_noise)
        s.mag_y = true_y + s.mag_bias[1] + self.rng.gauss(0, s.mag_noise)
        s.mag_z = true_z + s.mag_bias[2] + self.rng.gauss(0, s.mag_noise)
        s.mag_valid = True

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

    def _ref_rate_from_history(self, key: str, q_ref: np.ndarray,
                               dt: float) -> np.ndarray:
        """Body-frame reference rate by differencing successive q_ref.

        A tracking mode whose reference MOVES needs a rate feed-forward or the
        PD carries the whole tracking rate as a lag error (a type-1 loop has
        finite velocity error: e_ss = ω_ref·ζ·2/ω_n). The LVLH frame gets this
        analytically below, but the SUN and TARGET frames both returned a zero
        reference rate even though both rotate — the sun frame is built on the
        nadir direction and so turns at orbit rate, and a ground target sweeps
        several times faster than that near closest approach. Measured before
        this feed-forward existed: TARGET_TRACK held 0.63 deg mean error and
        COARSE_SUN 3.16 deg, against 0.055 deg for LVLH nadir which had it.

        Differencing is what flight software actually does for a reference it
        cannot differentiate in closed form: ω ≈ 2·vec(q_prev* ⊗ q_ref)/dt."""
        prev = self._ref_hist.get(key)
        self._ref_hist[key] = (np.array(q_ref, dtype=float), dt)
        if prev is None or dt <= 0.0:
            return np.zeros(3)
        q_prev, dt_prev = prev
        if dt_prev <= 0.0:
            return np.zeros(3)
        dq = att.quat_mult(att.quat_conj(q_prev), np.asarray(q_ref, float))
        if dq[3] < 0:
            dq = -dq
        w = 2.0 * dq[:3] / dt_prev
        # Reject the transient at a reference discontinuity (mode/target
        # change) — a step in q_ref would otherwise be differentiated into a
        # huge spurious rate command.
        if float(np.linalg.norm(w)) > 0.02:      # > 1.15 deg/s
            return np.zeros(3)
        return w

    def _compute_reference(self, s: AOCSState, mode: int, dt: float = 0.0):
        """(q_ref, ω_ref[body, rad/s]) for the active mode/pointing frame."""
        env = self._env
        frame = s.pointing_frame
        if mode == MODE_INERTIAL:
            frame = 0
        if frame == 0:
            # A commanded ECI quaternion genuinely does not move: zero is the
            # correct feed-forward, and the wheels absorb the LVLH-fixed
            # disturbances as secular momentum (measured 0.32 uN.m mean).
            return (np.asarray(s.target_q_cmd, float), np.zeros(3))
        if frame == 3 or mode == MODE_COARSE_SUN:
            C = att.sun_frame_dcm(env['sun_hat'], env['r_m'])
            q_ref = att.dcm_to_quat(C)
            return (q_ref, self._ref_rate_from_history('sun', q_ref, dt))
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
            q_ref = att.dcm_to_quat(C)
            return (q_ref, self._ref_rate_from_history('target', q_ref, dt))
        # Default: LVLH nadir with orbit-rate feed-forward
        C_li = att.lvlh_dcm(env['r_m'], env['v_ms'])
        q_ref = att.dcm_to_quat(C_li)
        r = env['r_m']
        w_orb_eci = np.cross(r, env['v_ms']) / float(np.dot(r, r))
        w_ref = att.quat_to_dcm(self._est_quat()) @ w_orb_eci
        return (q_ref, w_ref)

    # ── Sensors → estimator (WP-3 / WP-4) ────────────────────────────

    _GYRO_DEVICES = (0x0206, 0x0207, 0x0208)   # one single-axis unit per body axis

    def _gyro_axes_powered(self, s: AOCSState) -> list[bool]:
        """Per-axis gyro availability from the S2 device states (0x0206-0x0208).

        The three units are the X, Y and Z channels: switching one off must
        cost the estimator that axis. Previously nothing read these, so the
        operator's GYRO OFF commands were inert (coherence review §2.6)."""
        ds = s.device_states
        return [bool(ds.get(d, True)) for d in self._GYRO_DEVICES]

    def _gyro_units_mask(self, s: AOCSState) -> int:
        """Bitmask of the three gyro units (bit0 X / bit1 Y / bit2 Z).

        0x0294 only counts how many units are on. WHICH one is off matters —
        losing the pitch channel of a nadir-tracking spacecraft is a different
        problem from losing roll — so 0x02D1 publishes the identity too."""
        return sum(1 << i for i, on in enumerate(self._gyro_axes_powered(s))
                   if on)

    def _gyro_bias_total(self, s: AOCSState) -> list[float]:
        """TOTAL bias present in the hardware, per axis [deg/s].

        Gauss-Markov drift + thermal term + any injected hardware fault. This
        is the quantity a calibration has to estimate; it is NOT what the
        gyro output shows once a calibration has been applied (see
        `_gyro_bias_residual`)."""
        t_bias = getattr(self, '_gyro_temp_bias', 0.0)
        return [self._gyro_bias[i] + t_bias + self._gyro_bias_inject[i]
                for i in range(3)]

    def _gyro_bias_residual(self, s: AOCSState) -> list[float]:
        """Bias still visible at the gyro OUTPUT after on-board compensation.

        A successful calibration drives this to ~0 while the hardware bias
        (0x0296) is untouched: calibration compensates a fault, it does not
        repair it."""
        total = self._gyro_bias_total(s)
        return [total[i] - s.gyro_cal_comp[i] for i in range(3)]

    def _gyro_measurement(self, s: AOCSState, dt: float) -> np.ndarray:
        """Measured body rate [rad/s]: truth + true bias + ARW noise, minus the
        on-board calibration compensation.

        A powered-off unit is a DEAD CHANNEL: it reports zero rate, and
        `_gyro_axis_sigma` inflates that axis's process noise so the MEKF
        stops trusting it instead of silently propagating on a perfect gyro.
        Rate saturation is modelled too — a tactical-grade unit clips at
        `max_rate_dps`, so a violent separation tumble genuinely loses rate
        knowledge on the saturated axis (the reason DETUMBLE is specified on
        the magnetometer and not on the gyro).

        `gyro_cal_comp` is the bias table AOCS_GYRO_CALIBRATION writes. It is
        subtracted BEFORE saturation, exactly where a flight unit applies its
        compensation coefficients — downstream of the sensing element and
        upstream of the range limit."""
        sigma_dps = (self._gyro_arw_dps_rts / math.sqrt(max(dt, 1e-3))
                     + self._gyro_noise_dps)
        powered = self._gyro_axes_powered(s)
        resid = self._gyro_bias_residual(s)
        w_max = self._gyro_max_rate_dps * _DEG
        out = np.zeros(3)
        sat = False
        for i in range(3):
            if not powered[i]:
                out[i] = 0.0
                continue
            w = (s.omega_true[i]
                 + resid[i] * _DEG
                 + self.rng.gauss(0.0, sigma_dps) * _DEG)
            if abs(w) > w_max:
                w = math.copysign(w_max, w)
                sat = True
            out[i] = w
        self._gyro_saturated = sat
        return out

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
        powered = self._gyro_axes_powered(s)
        n_gyro = sum(1 for p in powered if p)
        source = 0x08 if n_gyro else 0x00     # bit3 = gyro contributing
        # A star tracker's enemy is stray light, not darkness: ECLIPSE is the
        # BEST sky it ever sees. The old rule excluded ST updates in ECLIPSE
        # and left the filter propagating open-loop for the whole 35-minute
        # umbra — the direct cause of the eclipse pointing decay that tripped
        # FDIR. Eclipse now uses the tracker whenever it is tracking; the
        # gyro-only coast is retained only as the FALLBACK when it is not
        # (which is what the mode-table line "gyro-only propagation" is really
        # describing — no CSS/sun reference is available in umbra).
        use_st = (s.st_valid and s.mode not in (MODE_DETUMBLE,
                                                MODE_COARSE_SUN))
        # 2026-08c item 11: once the vehicle is quiescent, a TRACKING star
        # tracker aids the filter in COARSE_SUN as well. Physically nothing
        # about the ST update needs wheel control — it needs a tracking head
        # and low rates — and gating it on NOMINAL forced the commissioning
        # order backwards (gyro calibration at COM-003 needed an ST-in-the-
        # loop estimate, but NOMINAL needs the wheels COM-004 enables).
        # Early COARSE acquisition (high rates / no track) still runs the
        # CSS+MAG TRIAD path below.
        if (not use_st and s.mode == MODE_COARSE_SUN and s.st_valid
                and getattr(s, f"st{s.st_selected}_num_stars", 0) >= 5
                and float(np.linalg.norm(
                    getattr(self, '_omega_est', np.zeros(3)))) * _RAD < 0.25):
            use_st = True
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
        elif s.mode in (MODE_DETUMBLE, MODE_COARSE_SUN) or n_gyro == 0:
            # With every gyro unit off there is no rate reference at all, so
            # the coarse two-vector (CSS + magnetometer TRIAD) path is the ONLY
            # thing keeping the estimate bounded — run it in any mode, and let
            # the covariance grow honestly when the vectors are unavailable too
            # (eclipse: no sun ⇒ pure coast).
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
            # Per-axis process noise: a healthy channel contributes its ARW; a
            # powered-off (or saturated) channel contributes the unknown-rate
            # density instead, so P grows at ~σ_dead·√t on that axis.
            dead = self._gyro_dead_arw_dps_rts * _DEG
            arw = self._gyro_arw_dps_rts * _DEG
            if all(powered) and not getattr(self, '_gyro_saturated', False):
                self._ekf.propagate(omega_meas, dt)
            else:
                self._ekf.propagate(omega_meas, dt, arw_axis=np.array([
                    arw if powered[i] else dead for i in range(3)]))
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
        # bit1: fewer than THREE wheels. R3 — the pyramid's whole point is that
        # any three axes still span R³, so 3-of-4 is a degraded-but-legal FINE
        # configuration (see _tick_fine_point for the measured numbers). Two
        # wheels cannot span R³ and remain a hard refusal.
        if sum(1 for w in s.active_wheels if w) < 3:
            mask |= 0x02          # bit1: fewer than 3 wheels (FINE impossible)
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
                # TRUE B-dot: a first-difference of the MEASURED magnetometer
                # vector. The previous implementation claimed to be B-dot in
                # its comment but actually fed back `s.omega_true` — the truth
                # body rate, a quantity no sensor produces. That made DETUMBLE
                # work perfectly with every gyro switched off AND with the
                # gyros saturated, which is precisely the case B-dot exists to
                # cover, and it hid the mode's real failure signature (a
                # magnetometer fault, not a gyro fault).
                #
                # Ḃ_body = −ω×B in the body frame, so m = −k·Ḃ gives
                # τ = m×B = −k·|B|²·ω_perp: a pure rate damper that needs no
                # attitude knowledge and no rate sensor.
                B_meas = np.array([s.mag_x, s.mag_y, s.mag_z]) * 1e-9  # nT->T
                prev = getattr(self, '_bdot_prev_B', None)
                prev_t = getattr(self, '_bdot_prev_dt', 0.0)
                if prev is not None and prev_t > 1e-6:
                    Bdot = (B_meas - prev) / prev_t
                    # Low-pass the difference lightly (first-order IIR,
                    # alpha = 0.3, tau ~ 1.2 s at h = 0.5 s): the magnetometer
                    # noise floor (50 nT) differentiated at h = 0.5 s is
                    # 1e-7 T/s, the same order as Ḃ at 1 deg/s, so a raw
                    # difference is all noise at low rate. The filter also
                    # absorbs the sample-rate mismatch — mag_x/y/z are
                    # refreshed once per model tick while control runs per
                    # sub-step, so the raw difference alternates between a
                    # 2x-scaled value and zero. Measured against truth over a
                    # 400 s tumble the filtered estimate tracks −ω×B with
                    # mean cos = 0.95 and |est|/|true| = 0.99, and the control
                    # power ω·τ is negative on every sub-step.
                    self._bdot_filt = (0.7 * getattr(self, '_bdot_filt',
                                                     np.zeros(3))
                                       + 0.3 * Bdot)
                    m = -self._bdot_gain * self._bdot_filt
                    mmax = max(abs(float(x)) for x in m)
                    if mmax > self._mtq_max_dipole:
                        m *= self._mtq_max_dipole / mmax
                self._bdot_prev_B = B_meas
                self._bdot_prev_dt = h
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
        q_ref, w_ref = self._compute_reference(s, MODE_COARSE_SUN, dt)
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
        q_ref, w_ref = self._compute_reference(s, mode, dt)
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
        """FINE_POINT — ST @4 Hz, both heads, tight PD with a control deadband.

        R3 (Franz): FINE runs on THREE wheels. A pyramid is flown four-of-four
        precisely so that any three still span R³ — refusing the mode on a
        single wheel loss threw away the redundancy the geometry was bought
        for. Measured on the shipped allocation: every 3-of-4 subset has
        condition number 2.175, mean pointing error on three wheels is
        0.0188 deg against 0.0208 deg on four, and peak per-wheel torque is
        4.4 µN·m against the 2 mN·m wheel limit — a ~450x margin. What a
        3-wheel set genuinely loses is the null space: bias-speed regulation
        and zero-crossing avoidance are gone, so wheels sit wherever the
        allocation puts them and jitter is worse. The mode is therefore
        PERMITTED but flagged `fine_degraded`, with a relaxed deadband, rather
        than refused. Two wheels do NOT span R³ and are still refused."""
        if not s.st_valid:
            self._set_mode(MODE_NOMINAL,
                           reason="FINE lost star tracker — return path is "
                                  "automatic re-promotion once ST recovers")
            return
        n_active = sum(1 for w in s.active_wheels if w)
        if n_active < 3:
            self._set_mode(MODE_NOMINAL,
                           reason=f"FINE needs >= 3 wheels, {n_active} active")
            return
        s.fine_degraded = (n_active == 3)
        # Degraded set: relax the control deadband to the NOMINAL-class band
        # rather than chattering the wheels against a spec they cannot hold
        # without null-space authority.
        db = s.deadband_deg * (self._fine_degraded_db_factor
                               if s.fine_degraded else 1.0)
        self._tick_wheel_pointing(s, dt, MODE_FINE_POINT,
                                  deadband_deg=db,
                                  integral=True)

    def _tick_slew(self, s: AOCSState, dt: float) -> None:
        """SLEW — track a rate-limited SLERP reference trajectory from the
        start attitude to the commanded target with the FINE-bandwidth PD."""
        if s.slew_target_q is None:
            self._set_mode(self._slew_return_target(s),
                           reason="SLEW entered with no target quaternion")
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
            timed_out = not (progress >= 1.0 and err < 0.2)
            self._set_mode(
                self._slew_return_target(s),
                reason=("slew TIMED OUT" if timed_out else
                        f"slew complete ({total_angle:.1f} deg, "
                        f"residual {err:.3f} deg)"))
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
            h_b = self._A @ np.array(s.h_wheel)
            if (Bsq > 1e-18 and s.mtq_enabled
                    and float(np.linalg.norm(h_b)) > self._desat_deadband_nms):
                # Cross-product dump: m = k·(h×B)/|B|² ⇒ τ = m×B = −k·h_perp,
                # so k is a RATE in s⁻¹ and the dump time constant is 1/k.
                # The old k = 1.0 s⁻¹ is not a dump gain at all — it is ~1000x
                # the orbital rate Ω = 1.1e-3 s⁻¹. When k ≫ Ω the commanded
                # dipole is clipped to the rod envelope on the very first tick,
                # the momentum vector locks onto B̂ and simply rides with the
                # rotating field, and the net secular decay collapses to
                # ~Ω²/k. Measured: rods loafing at 0.11-0.41 A·m² (7-25 % of
                # 1.6) and DESAT never reaching its exit condition.
                # k is now sized so the rods SATURATE over the whole useful
                # momentum range and the dump runs at the rod's actual torque
                # authority τ = m_max·B ≈ 1.6 · 30 µT = 48 µN·m, i.e. a full
                # 0.02 N·m·s wheel loading in 0.02/4.8e-5 ≈ 7 min — the
                # 5-15 min the manual and aocs.yaml §magnetorquers claim.
                m = self._desat_gain_per_s * np.cross(h_b, B_b / Bsq)
                # Uniform (direction-preserving) scaling into the rod
                # envelope: per-axis clipping would distort the dump
                # direction and churn momentum between wheels instead of
                # removing it.
                mmax = max(abs(float(x)) for x in m)
                if mmax > self._mtq_max_dipole:
                    m *= self._mtq_max_dipole / mmax
                elif self._desat_saturate and mmax > 1e-9:
                    # Bang-bang: below saturation the linear law leaves the
                    # rods loafing (measured 0.11-0.46 A.m2 against a 1.6 A.m2
                    # capability) and the dump takes most of an orbit. Scaling
                    # the commanded dipole UP to the rod envelope keeps the
                    # DIRECTION the cross-product law chose while running the
                    # actuator at the authority it actually has.
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
        """ECLIPSE — nadir hold with no SUN reference (the CSS is dark).

        The star tracker is still used: umbra is the best sky it ever gets.
        Only when the tracker is unavailable does the mode coast on the gyro,
        and the MEKF covariance then grows honestly (see _tick_estimator)."""
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
                self._set_mode(self._slew_return_target(s),
                               reason="slew complete (secular)")
                s.slew_target_q = None
                s.slew_start_q = None
                s.slew_elapsed = None
                s.slew_idle_timer = 0.0
        else:
            q_ref, _ = self._compute_reference(s, mode, dt)
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
            # Allocate over the ACTIVE wheel set, exactly as _apply_wheel_torque
            # does. The fixed 4-wheel `_A_pinv` silently discarded the share of
            # the disturbance momentum belonging to a failed wheel, so with a
            # wheel down the momentum books diverged between the real-time and
            # the >60x paths — a fast-forward could not be trusted to predict a
            # desat.
            active = tuple(bool(a) for a in s.active_wheels)
            pinv = self._alloc_pinv(active)
            idx = self._alloc_idx
            h_max = self._rw_inertia * self._rw_max_speed * _RPM2RAD
            if idx:
                dh = pinv @ (tau_d * dt)
                for k, i in enumerate(idx):
                    s.h_wheel[i] = max(-h_max, min(
                        h_max, s.h_wheel[i] + float(dh[k])))
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

    def _publish_gps_pvt(self, s: AOCSState, orbit_state: Any,
                         shared_params: dict[int, float]) -> None:
        """Publish the GPS receiver's MEASURED PVT solution (2026-08c item 2).

        Gating: gps_fix (0x0274) < 2 => all nine PVT parameters are zero —
        honest-invalid, with 0x0274 as the validity flag. Fix 2 gives
        position only; fix 3 adds velocity. Noise is per-axis Gaussian,
        1σ = 5 m x PDOP (position) and 0.05 m/s x PDOP (velocity).

        ECEF comes from the propagator's true ECI state rotated through GMST
        (the same transform the contact geometry uses); reduced test
        harnesses whose orbit_state has no usable ECI state fall back to the
        geodetic truth via _lla_to_ecef, and anything unresolvable publishes
        zeros rather than a fabricated value.
        """
        p = self._param_ids
        pids = (p.get("gps_lat", 0x0210), p.get("gps_lon", 0x0211),
                p.get("gps_alt", 0x0212),
                p.get("gps_ecef_x", 0x0231), p.get("gps_ecef_y", 0x0232),
                p.get("gps_ecef_z", 0x0233),
                p.get("gps_vel_x", 0x0234), p.get("gps_vel_y", 0x0235),
                p.get("gps_vel_z", 0x0236))
        if s.gps_fix < 2:
            for pid in pids:
                shared_params[pid] = 0.0
            return
        pdop = max(0.8, float(s.gps_pdop))
        sig_pos_m = 5.0 * pdop
        sig_vel_ms = 0.05 * pdop
        # Geodetic solution (measured): truth + noise mapped to angle.
        try:
            lat = float(orbit_state.lat_deg)
            lon = float(orbit_state.lon_deg)
            alt = float(orbit_state.alt_km)
        except (TypeError, ValueError):
            lat = lon = alt = 0.0
        m_per_deg = 111320.0
        shared_params[pids[0]] = lat + self.rng.gauss(0, sig_pos_m) / m_per_deg
        shared_params[pids[1]] = lon + self.rng.gauss(0, sig_pos_m) / (
            m_per_deg * max(0.1, math.cos(math.radians(lat))))
        shared_params[pids[2]] = alt + self.rng.gauss(0, sig_pos_m) / 1000.0
        # ECEF position/velocity from the true ECI state.
        r_ecef_m = v_ecef_ms = None
        try:
            from smo_common.orbit.propagator import (
                _eci_to_ecef, _gmst, _lla_to_ecef)
            r_eci = np.asarray(getattr(orbit_state, "pos_eci", None), float)
            utc = getattr(orbit_state, "utc", None)
            if (r_eci is not None and r_eci.shape == (3,)
                    and float(np.linalg.norm(r_eci)) > 6000.0
                    and utc is not None):
                # Julian date from the orbit-state epoch (UT1 ~= UTC here).
                jd = (utc.timestamp() / 86400.0) + 2440587.5
                gmst = _gmst(jd)
                r_ecef_m = _eci_to_ecef(r_eci, gmst) * 1000.0
                v_eci = np.asarray(
                    getattr(orbit_state, "vel_eci", None), float)
                if v_eci is not None and v_eci.shape == (3,)                         and float(np.linalg.norm(v_eci)) > 0.1:
                    omega_e = 7.2921150e-5  # rad/s
                    v_rot = _eci_to_ecef(v_eci, gmst) * 1000.0
                    v_ecef_ms = v_rot - np.cross(
                        np.array([0.0, 0.0, omega_e]), r_ecef_m)
            if r_ecef_m is None:
                r_ecef_m = _lla_to_ecef(lat, lon, alt) * 1000.0
        except Exception:   # noqa: BLE001 — mock harness, publish zeros
            r_ecef_m = None
        if r_ecef_m is not None:
            for i in range(3):
                shared_params[pids[3 + i]] = (
                    float(r_ecef_m[i]) + self.rng.gauss(0, sig_pos_m))
        else:
            for i in range(3):
                shared_params[pids[3 + i]] = 0.0
        if s.gps_fix >= 3 and v_ecef_ms is not None:
            for i in range(3):
                shared_params[pids[6 + i]] = (
                    float(v_ecef_ms[i]) + self.rng.gauss(0, sig_vel_ms))
        else:
            for i in range(3):
                shared_params[pids[6 + i]] = 0.0

    def _tick_gyro_and_gps(self, s: AOCSState, dt: float,
                            orbit_state: Any) -> None:
        """Model gyroscope bias truth state and GPS receiver."""
        # ── TRUE gyro bias: first-order Gauss-Markov, NOT a free random walk ──
        # The old model was `b += N(0, 5e-5·√dt)` with no mean reversion. That
        # is an unbounded random walk whose 1σ reaches 5e-5·√3600 = 3e-3 °/s
        # (≈ 11 °/h) after one hour — 30x the 0.1 °/h bias instability the
        # datasheet anchor in aocs.yaml declares, and ~100x the σ the MEKF is
        # built from (MEKF6 takes bias_ss_dps / bias_tau_s straight out of the
        # same config). Filter and truth therefore disagreed about the process
        # noise by two orders of magnitude: in ECLIPSE, where the filter has no
        # attitude update, the MEKF reported 0.29° 3σ knowledge while the
        # estimate had actually walked 6.8° off truth. That drift is what tripped
        # the `aocs.att_error > 5` FDIR rule every eclipse and demoted the
        # spacecraft out of NOMINAL (finding A-a).
        # Implemented now as the Gauss-Markov the config actually describes:
        #   db = -b/τ·dt + σ_ss·√(2·dt/τ)·N(0,1)   ⇒  steady-state σ = σ_ss.
        tau = max(1.0, self._gyro_bias_tau_s)
        sigma_ss = self._gyro_bias_ss_dps
        q_step = sigma_ss * math.sqrt(2.0 * min(dt, tau) / tau)
        # Temperature-dependent bias: a real gyro's bias is specified over a
        # temperature range, and 0x0273 gyro_temp was decorative until now.
        # A linear tempco about the 22 °C calibration point (deg/s per K).
        t_bias = self._gyro_tempco_dps_per_k * (s.gyro_temp - 22.0)
        for i, attr in enumerate(("gyro_bias_x", "gyro_bias_y", "gyro_bias_z")):
            bias = self._gyro_bias[i]
            bias += -bias / tau * dt + self.rng.gauss(0.0, q_step)
            self._gyro_bias[i] = max(-0.1, min(0.1, bias))
        self._gyro_temp_bias = t_bias
        # 0x0270-0x0272 report the bias an operator can actually SEE at the
        # gyro output: random component + thermal term + any injected fault,
        # MINUS whatever the last calibration compensated. Before the
        # calibration was implemented `gyro_cal_comp` was always zero and this
        # was the raw total, so the change is invisible on an uncalibrated
        # spacecraft — but it is the difference between a card that says
        # "confirm the bias fell" meaning something and meaning nothing.
        for i, attr in enumerate(("gyro_bias_x", "gyro_bias_y",
                                  "gyro_bias_z")):
            setattr(s, attr, self._gyro_bias[i] + t_bias
                    + self._gyro_bias_inject[i] - s.gyro_cal_comp[i])

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

    # ─── Gyro bias calibration (S8.1 func 13) ────────────────────────
    #
    # What this replaces: `gyro_calibration` used to be one line —
    # `self._gyro_bias = [0, 0, 0]` — which zeroed the TRUTH state of the
    # Gauss-Markov drift (a ~1e-6 deg/s quantity that mean-reverts on its own
    # in half an hour anyway), completed instantly, ignored the injected
    # hardware bias entirely, reported nothing, raised no event and had no
    # preconditions. It was a command that reached into the physics and
    # deleted a number the spacecraft cannot observe. On the shipped build an
    # instructor could inject a 0.05 deg/s gyro_bias, fly COM-003 to the
    # letter, get "Gyro calibration complete", and the bias would be exactly
    # where it started.
    #
    # What a bias calibration really is: with the vehicle quiescent and a
    # stable attitude reference in the loop, the difference between what the
    # gyro says and what the reference says the vehicle is doing IS the bias.
    # On board that observation is exactly the MEKF's bias state, so the
    # calibration averages `MEKF6.b` over a quiet arc and writes the result
    # into a compensation table applied on the measurement path
    # (`gyro_cal_comp`, subtracted in `_gyro_measurement`). Estimate the bias,
    # compensate it in the sensor chain, and reset the filter's bias state
    # because the measurement it now sees is bias-free.
    #
    # Crucially the compensation does NOT touch `_gyro_bias_inject`. An
    # injected gyro_bias is a HARDWARE fault: calibration cancels its effect,
    # it does not repair the unit. 0x0296 keeps showing the instructor the
    # fault is still there while 0x02D2 shows the operator the residual has
    # gone — which is the whole teaching point of the scenario.

    #: Deferred-completion key for AOCS_GYRO_CALIBRATION (S8.1 func 13).
    #: The arc is 300 s by default. The command already publishes progress
    #: (0x02CA) and raises its own 0x0210/0x0211/0x0212 events, so the
    #: operator was never blind here — but the S1.7 still claimed the
    #: calibration was COMPLETE the moment the arc STARTED, which is the
    #: semantics the ruling fixes everywhere, not just where it also blinds
    #: the operator. Progress telemetry is the right *situational* contract
    #: and stays; it is not a substitute for the verification service saying
    #: what actually happened to this telecommand.
    COMPLETION_GYRO_CAL = "aocs.gyro_calibration"

    GYRO_CAL_IDLE = 0
    GYRO_CAL_RUNNING = 1
    GYRO_CAL_COMPLETE = 2
    GYRO_CAL_ABORTED = 3

    # Refusal / abort codes — the shared vocabulary in
    # smo_common.protocol.pus_services.FAILURE_CODES.
    CAL_ERR_UNPOWERED = 0x0A01
    CAL_ERR_UNIT_OFF = 0x0A02
    CAL_ERR_NO_REFERENCE = 0x0A03
    CAL_ERR_RATE_HIGH = 0x0A04
    CAL_ERR_BUSY = 0x0A05
    CAL_ERR_LOST = 0x0A06
    CAL_ERR_NOT_RUNNING = 0x0A07
    CAL_ERR_OPERATOR = 0x0A08

    def _gyro_cal_blocker(self, s: AOCSState) -> int:
        """First unmet gyro-calibration precondition, or 0 when all hold.

        Checked at command time AND every tick of the run: a calibration that
        lost its attitude reference half way through has averaged nonsense,
        and must abort loudly rather than publish a bias estimate nobody can
        tell apart from a good one."""
        if s.mode in (MODE_OFF, MODE_SAFE_BOOT):
            return self.CAL_ERR_UNPOWERED
        if not all(self._gyro_axes_powered(s)):
            return self.CAL_ERR_UNIT_OFF
        # A stable attitude reference means the tracker is not merely powered
        # but actually IN the determination loop. 2026-08c item 11: this is
        # no longer a MODE gate — bias calibration does not physically need
        # wheel control, only a tracking head feeding the filter and low
        # rates, so the check is on the ESTIMATOR CONFIGURATION itself:
        # est_source bit 0 says the last tick's MEKF update really came from
        # the star tracker. In COARSE_SUN that bit sets once the vehicle is
        # quiescent and the tracker holds >=5 stars (see _tick_estimator),
        # which is exactly the state COM-003 reaches before COM-004 enables
        # the wheels. DETUMBLE never sets it (CSS+MAG TRIAD only), so the
        # refusal there is unchanged in effect.
        sel_stars = getattr(s, f"st{s.st_selected}_num_stars", 0)
        if (not s.st_valid or sel_stars < 5
                or not (int(getattr(s, "est_source", 0)) & 0x01)):
            return self.CAL_ERR_NO_REFERENCE
        # Quiescent: the bias is only separable from the true rate when the
        # vehicle is not manoeuvring. Measured against the ESTIMATE relative
        # to the reference, so LVLH nadir tracking (a genuine ~0.06 deg/s
        # orbital rate) is not mistaken for a slew.
        w_rel = (getattr(self, '_omega_est', np.zeros(3))
                 - np.asarray(s.omega_ref, float))
        if float(np.linalg.norm(w_rel)) * _RAD > self._gyro_cal_max_rate_dps:
            return self.CAL_ERR_RATE_HIGH
        return 0

    def _emit_event(self, event_id: int, severity: str,
                    description: str) -> None:
        """Queue one S5 event (same path `_generate_events` uses)."""
        engine = getattr(self, '_engine', None)
        if engine is not None:
            engine._model_event_queue.put({
                'event_id': event_id,
                'severity': severity,
                'description': description,
            })

    # ── Deferred completion (verification-semantics ruling, 2026-08) ──

    def _complete(self, key: str, success: bool = True,
                  error_code: int = 0x0001, reason: str = "") -> None:
        """Queue a deferred-completion report for the engine to drain."""
        if not hasattr(self, "_completions"):
            self._completions = []
        self._completions.append({"key": str(key), "success": bool(success),
                                  "error_code": int(error_code),
                                  "reason": str(reason or "")})

    def pop_completions(self) -> list[dict]:
        out = getattr(self, "_completions", [])
        self._completions = []
        return out

    def _start_gyro_calibration(self, duration_s: float = 0.0) -> dict:
        """AOCS_GYRO_CALIBRATION start. Refuses with a specific code."""
        s = self._state
        if s.gyro_cal_state == self.GYRO_CAL_RUNNING:
            s.gyro_cal_reject = self.CAL_ERR_BUSY
            return {"success": False, "error_code": self.CAL_ERR_BUSY,
                    "message": ("Gyro calibration already running "
                                f"({s.gyro_cal_progress:.0f} % complete)")}
        blocker = self._gyro_cal_blocker(s)
        if blocker:
            s.gyro_cal_reject = blocker
            return {"success": False, "error_code": blocker,
                    "message": failure_code_text(blocker)}
        dur = float(duration_s) or self._gyro_cal_duration_s
        dur = max(self._gyro_cal_min_duration_s,
                  min(self._gyro_cal_max_duration_s, dur))
        s.gyro_cal_duration_s = dur
        s.gyro_cal_settle_s = min(self._gyro_cal_settle_s, 0.5 * dur)
        s.gyro_cal_state = self.GYRO_CAL_RUNNING
        s.gyro_cal_elapsed = 0.0
        s.gyro_cal_progress = 0.0
        s.gyro_cal_acc = [0.0, 0.0, 0.0]
        s.gyro_cal_n = 0
        s.gyro_cal_reject = 0
        self._emit_event(0x0210, 'INFO',
                         f'Gyro bias calibration started: {dur:.0f} s arc '
                         f'({s.gyro_cal_settle_s:.0f} s settle)')
        return {"success": True,
                "deferred": self.COMPLETION_GYRO_CAL,
                # Generous: the arc is `dur`, and the abort paths report
                # their own failure, so the timeout only catches a lost
                # completion, never a slow one.
                "deferred_timeout_s": 2.0 * dur + 120.0,
                "deferred_label": (f"AOCS_GYRO_CALIBRATION "
                                   f"({dur:.0f} s arc)"),
                "message": (f"Gyro bias calibration started ({dur:.0f} s); "
                            "progress in aocs.gyro_cal_progress (0x02CA)"),
                "duration_s": dur}

    def _abort_gyro_calibration(self, code: int, why: str) -> None:
        s = self._state
        s.gyro_cal_state = self.GYRO_CAL_ABORTED
        s.gyro_cal_result = 2
        s.gyro_cal_reject = code
        s.gyro_cal_progress = 0.0
        s.gyro_cal_acc = [0.0, 0.0, 0.0]
        s.gyro_cal_n = 0
        self._emit_event(0x0212, 'MEDIUM',
                         f'Gyro bias calibration aborted: {why} '
                         f'(code 0x{code:04X})')
        self._complete(self.COMPLETION_GYRO_CAL, False, int(code),
                       f"AOCS_GYRO_CALIBRATION aborted: {why}")

    def _tick_gyro_calibration(self, s: AOCSState, dt: float) -> None:
        """Advance a running calibration by dt."""
        if s.gyro_cal_state != self.GYRO_CAL_RUNNING:
            return
        blocker = self._gyro_cal_blocker(s)
        if blocker:
            # The shared text is phrased as a REFUSAL ("Gyro calibration
            # refused: ..."); mid-run it is an abort, so strip the prefix and
            # let `_abort_gyro_calibration` supply the verb.
            why = failure_code_text(blocker).split(": ", 1)[-1]
            self._abort_gyro_calibration(blocker, why)
            return
        s.gyro_cal_elapsed += dt
        s.gyro_cal_progress = min(100.0, 100.0 * s.gyro_cal_elapsed
                                  / max(1e-6, s.gyro_cal_duration_s))
        # Accumulate only after the settle window: the filter's bias state
        # needs a few star-tracker fixes to respond, and averaging its
        # transient in would bias the calibration toward zero.
        if s.gyro_cal_elapsed >= s.gyro_cal_settle_s:
            b_dps = np.asarray(self._ekf.b, float) * _RAD
            for i in range(3):
                s.gyro_cal_acc[i] += float(b_dps[i])
            s.gyro_cal_n += 1
        if s.gyro_cal_elapsed < s.gyro_cal_duration_s:
            return
        # ── Complete ──
        if s.gyro_cal_n <= 0:
            self._abort_gyro_calibration(
                self.CAL_ERR_LOST, "no samples accumulated over the arc")
            return
        n = float(s.gyro_cal_n)
        # The filter observes the bias of the ALREADY-COMPENSATED measurement,
        # so the new table is the old table plus what is left over. Running
        # the calibration twice therefore converges rather than double-
        # counting, which a naive `comp = mean(b)` would not.
        new_comp = [s.gyro_cal_comp[i] + s.gyro_cal_acc[i] / n
                    for i in range(3)]
        s.gyro_cal_comp = new_comp
        s.gyro_cal_bias = list(new_comp)
        s.gyro_cal_state = self.GYRO_CAL_COMPLETE
        s.gyro_cal_result = 1
        s.gyro_cal_reject = 0
        s.gyro_cal_progress = 100.0
        s.gyro_cal_acc = [0.0, 0.0, 0.0]
        s.gyro_cal_n = 0
        # The measurement the filter sees from the next tick is bias-free, so
        # carrying the old bias state forward would double-correct it.
        self._ekf.b = np.zeros(3)
        self._emit_event(
            0x0211, 'INFO',
            'Gyro bias calibration complete: '
            f'X={new_comp[0]:+.5f} Y={new_comp[1]:+.5f} '
            f'Z={new_comp[2]:+.5f} deg/s applied as compensation')
        self._complete(self.COMPLETION_GYRO_CAL, True)

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

        # The magnetometer (0x0209 — the single unit). Powered off => the
        # reading is invalid and zeroed, never frozen.
        if not ds.get(0x0209, True):
            s.mag_valid = False
            s.mag_x = s.mag_y = s.mag_z = 0.0

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

        phi is the in-plane bearing of the sun's projection measured from the
        spacecraft position vector: with u = r_hat, h_hat = orbit normal and
        w = h_hat x u (~velocity), phi = atan2(s.w, s.u). It advances exactly
        once per orbit and is phase-locked to eclipse.

        FRAME NOTE (2026-08b P-d). This docstring used to claim phi defined
        "the rotating LVLH-like frame used by the CSS model, sun_body =
        (cos b cos phi, cos b sin phi, sin b), body Z on the orbit normal".
        That has been false since Phase C: the CSS model reads
        `_true_sun_body()` off the attitude quaternion, and the body frame it
        lands in when nadir-pointing is real LVLH (smo_common.attitude:
        z = nadir, y = -orbit normal, x ~ velocity), in which

            sun_body = (cos b sin phi, -sin b, -cos b cos phi).

        The stale claim was copied into tools/power_budget.py, where it made
        the tool over-read generation by ~28 % and invert the beta trend.
        Anything reconstructing a synthetic sun vector from (beta, phi) must
        use the LVLH form above — see tools/power_budget.sun_body_lvlh.

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
        # Remembered for command-time precondition checks (handle_command has
        # no shared_params): DETUMBLE entry refuses when the suite is dark.
        self._line_aocs_on = _line_aocs_on
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

        # ── GPS PVT (2026-08c item 2) ──────────────────────────────────
        # MEASURED, not truth. The old code wrote orbit-state TRUTH straight
        # into 0x0210-0x0212 unconditionally (no fix gate, no noise) and
        # wrote truth velocity to 0x0213-0x0215 — ids that collide with
        # aocs.css_sun_confidence (0x0215) and are not GPS parameters at all.
        # The receiver now publishes a noisy PVT solution gated on its own
        # fix state: position (lat/lon/alt + ECEF 0x0231-0x0233) only while
        # gps_fix >= 2, velocity (ECEF 0x0234-0x0236) only at fix 3, honest
        # zeros otherwise. Noise scales with PDOP — 5 m·PDOP 1σ per axis in
        # position, 0.05 m/s·PDOP in velocity (commodity LEO receiver class).
        self._publish_gps_pvt(s, orbit_state, shared_params)
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

        # Gyro bias calibration — runs off the filter's bias observation, so it
        # has to come AFTER the estimator has consumed this tick's star-tracker
        # fix, not before.
        self._tick_gyro_calibration(s, dt)

        # Desat inhibit follows the payload: a dump drops fine pointing, so it
        # must never pre-empt a strip. 0x0600 payload.mode == 2 is IMAGING.
        # The payload capture gate demands EXACTLY AOCS mode 5, so this and
        # that gate have to agree — see docs/plan_2026-08b/DISPLAY_REQUESTS_AOCS.md.
        s.desat_inhibit_auto = int(shared_params.get(0x0600, 0)) == 2

        # Emergency checks (before mode tick)
        if not self._check_emergency(s):
            # Auto transitions
            self._check_auto_transitions(s, dt, orbit_state)

        # Autonomous momentum management (AOCS_SET_MOM_MGMT).
        # FINE_POINT is deliberately EXCLUDED from automatic entry: auto-desat
        # used to fire mid-strip, the payload flagged the strip PARTIAL, and the
        # healthy DESAT exit returned to NOMINAL — so a single dump silently and
        # permanently cost the mission its imaging mode. In FINE the momentum is
        # allowed to build to the 0x02C5 bit-4 (70 % envelope) refusal instead,
        # which is visible to the operator before the strip is planned.
        if (s.mom_mgmt_enable
                and s.mode in (MODE_NOMINAL, MODE_INERTIAL, MODE_ECLIPSE)
                and not self._desat_inhibited(s)
                and self._h_env_frac(s) >= s.mom_mgmt_entry_pct):
            s.return_mode = s.mode
            self._enter_desat(s, "autonomous momentum management: "
                              f"{self._h_env_frac(s):.0f} % >= "
                              f"{s.mom_mgmt_entry_pct:.0f} % entry threshold")

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

        # Automatic NOMINAL -> FINE re-promotion when FINE is the commanded
        # mode (the return path for the one demotion that cannot be instant).
        self._check_fine_promotion(s, dt)

        # Wheel-assembly commissioning latch (0x029D). Set the moment three
        # or more wheels are simultaneously enabled; an AOCS power-down clears
        # it because that is a real re-commissioning.
        if s.mode == MODE_OFF:
            s.wheels_commissioned = False
        elif sum(1 for w in s.active_wheels if w) >= 3:
            s.wheels_commissioned = True

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

        # 0x0223-0x0229 (dual-magnetometer block) retired — one magnetometer
        # only (2026-08c item 8). The measured field is 0x020B-0x020D above.

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
        # Sun-pointing confidence 0x0215 (declared on-demand; never written
        # before — S20.3 returned a plausible 0.0).
        shared_params[p.get("css_sun_confidence", 0x0215)] = float(
            getattr(self, "_css_sun_confidence", 0.0))

        # Magnetorquers
        shared_params[p.get("mtq_x_duty", 0x0258)] = s.mtq_x_duty
        shared_params[p.get("mtq_y_duty", 0x0259)] = s.mtq_y_duty
        shared_params[p.get("mtq_z_duty", 0x025A)] = s.mtq_z_duty

        # Momentum
        shared_params[p.get("total_momentum", 0x025B)] = s.total_momentum

        # Equipment enabled flags
        shared_params[0x025C] = 1.0 if s.mag_valid else 0.0
        # 0x025D aocs.gyro_enabled — was hard-wired to "mode != OFF", which made
        # the three S2 gyro device commands (0x0206-0x0208) look inert on the
        # ground. It now reports the real hardware state: gyros are enabled when
        # the AOCS is powered AND at least one unit is switched on.
        _gyro_powered = self._gyro_axes_powered(s)
        shared_params[0x025D] = 1.0 if (s.mode != MODE_OFF
                                        and any(_gyro_powered)) else 0.0
        # 0x0294-0x0296 (free AOCS block): how many of the three gyro units are
        # on, whether any channel is rate-saturated, and the injected-bias
        # magnitude — so a dead/saturated IMU is diagnosable from telemetry.
        shared_params[0x0294] = float(sum(1 for g in _gyro_powered if g))
        shared_params[0x0295] = 1.0 if getattr(self, '_gyro_saturated',
                                               False) else 0.0
        shared_params[0x0296] = float(math.sqrt(
            sum(b * b for b in self._gyro_bias_inject)))
        # 0x02D1: WHICH unit is off, not only how many. 0x02D2: the bias still
        # present at the gyro output after on-board compensation — the number
        # that says whether a calibration took.
        shared_params[0x02D1] = float(self._gyro_units_mask(s))
        _resid = self._gyro_bias_residual(s)
        shared_params[0x02D2] = float(math.sqrt(sum(b * b for b in _resid)))

        # ── Gyro bias calibration (S8.1 func 13) ──
        shared_params[0x02C9] = float(s.gyro_cal_state)
        shared_params[0x02CA] = float(s.gyro_cal_progress)
        shared_params[0x02CB] = float(s.gyro_cal_bias[0])
        shared_params[0x02CC] = float(s.gyro_cal_bias[1])
        shared_params[0x02CD] = float(s.gyro_cal_bias[2])
        shared_params[0x02CE] = float(math.sqrt(
            sum(c * c for c in s.gyro_cal_comp)))
        shared_params[0x02CF] = float(s.gyro_cal_result)
        shared_params[0x02D0] = float(s.gyro_cal_reject)

        # ── Mode-management / degraded-FINE / desat telemetry ──
        # These make the mode machine observable from the ground: what mode was
        # ASKED for, whether the spacecraft is currently somewhere else, and
        # whether FINE is running on a degraded actuator set.
        shared_params[0x0297] = float(s.commanded_mode)
        shared_params[0x0298] = 1.0 if s.mode_demoted else 0.0
        shared_params[0x0299] = 1.0 if s.fine_degraded else 0.0
        shared_params[0x029A] = 1.0 if self._desat_inhibited(s) else 0.0
        shared_params[0x029B] = float(
            np.linalg.norm(self._A @ np.array(s.h_wheel)))   # |h_body| N.m.s
        shared_params[0x029C] = 1.0 if s.desat_ineffective else 0.0
        shared_params[0x029D] = 1.0 if s.wheels_commissioned else 0.0
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
        tm[p.get("css_sun_confidence", 0x0215)] = float(
            getattr(self, "_css_sun_confidence", 0.0))

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

        # `prev_mode` is the mode at the start of the MODE TICK and therefore
        # misses every transition made before it: eclipse entry and the FDIR
        # demotions in `_check_auto_transitions` run earlier in the same tick,
        # and a commanded change lands in `handle_command` between ticks. Edge-
        # detect against the mode seen by the last event pass, which sees all
        # three — a NOMINAL -> ECLIPSE transition used to produce NO event from
        # this model at all, which is why the engine's (wrongly labelled)
        # duplicate looked load-bearing.
        prev_mode = self._prev_event_mode
        self._prev_event_mode = s.mode

        # AOCS_MODE_CHANGE (0x0200) — on any mode transition. The reason string
        # is part of the event: an unexplained mode change is the single most
        # expensive thing to debug from a telemetry archive, and a demotion
        # whose return path is not stated is indistinguishable from a mode the
        # spacecraft has decided to live in.
        if prev_mode != s.mode:
            events.append({
                'event_id': 0x0200,
                'severity': 'MEDIUM' if s.mode_demoted else 'LOW',
                'description': (
                    f'AOCS mode change from {_MODE_NAMES.get(prev_mode, "UNKNOWN")} '
                    f'to {_MODE_NAMES.get(s.mode, "UNKNOWN")} '
                    f'({s.mode_change_reason})'
                    + (f' [DEMOTED from commanded '
                       f'{_MODE_NAMES.get(s.commanded_mode, "?")}]'
                       if s.mode_demoted else '')),
            })
            # AOCS_MODE_RESTORED (0x020F) — the paired positive edge, so an
            # operator can see the loop close instead of inferring it.
            if not s.mode_demoted and s.mode == s.commanded_mode \
                    and prev_mode != s.commanded_mode:
                events.append({
                    'event_id': 0x020F,
                    'severity': 'INFO',
                    'description': (
                        'AOCS returned to commanded mode '
                        f'{_MODE_NAMES.get(s.commanded_mode, "?")} '
                        f'({s.mode_change_reason})'),
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
        # Measured on the RESIDUAL (post-compensation) bias: what the flight
        # software can see at the gyro output. A successful calibration
        # therefore clears the alarm, which is the correct operational story —
        # the hardware fault is still there (0x0296) but the vehicle is no
        # longer suffering from it.
        bias_mag = math.sqrt(sum(b * b for b in self._gyro_bias_residual(s)))
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
            if mode == MODE_DETUMBLE and self._state.mode != MODE_DETUMBLE:
                # 2026-08c item 9: DETUMBLE is the B-dot law — magnetometer
                # in, magnetorquers out. It used to be enterable with nothing
                # enabled (accepted, then sat there doing no physics). The
                # command now refuses honestly, one failure code per missing
                # leg, mirroring the FINE_POINT precondition gate.
                sd = self._state
                if not getattr(self, "_line_aocs_on", True):
                    return {"success": False, "error_code": 0x0004,
                            "code": "AOCS_UNPOWERED",
                            "message": ("DETUMBLE refused: EPS aocs_wheels "
                                        "line (0x0117) is off — the AOCS "
                                        "suite is unpowered")}
                mag_ok = (not sd.mag_failed
                          and sd.device_states.get(0x0209, True))
                if not mag_ok:
                    return {"success": False, "error_code": 0x0A20,
                            "code": "DETUMBLE_NO_MAG",
                            "message": ("DETUMBLE refused: the magnetometer "
                                        "(S2 device 0x0209) is off or failed "
                                        "— B-dot has no field reference")}
                mtq_ok = sd.mtq_enabled and any(
                    (not getattr(sd, f"mtq_{ax}_failed"))
                    and sd.device_states.get(dev, True)
                    for ax, dev in (("x", 0x020B), ("y", 0x020C),
                                    ("z", 0x020D)))
                if not mtq_ok:
                    return {"success": False, "error_code": 0x0A21,
                            "code": "DETUMBLE_NO_MTQ",
                            "message": ("DETUMBLE refused: magnetorquers "
                                        "disabled (MTQ_ENABLE) or all rods "
                                        "off/failed — B-dot has no "
                                        "actuator")}
            if mode == MODE_FINE_POINT and self._state.mode != MODE_FINE_POINT:
                # WP-6: FINE entry is gated on the 0x02C5 precondition
                # bitmask — refuse loudly instead of accept-then-drop-back.
                mask = self._state.fine_ready_mask
                if mask != 0:
                    return {"success": False,
                            "code": "FINE_NOT_READY",
                            "error_code": 0x0A22,
                            "fine_ready_mask": mask,
                            "message": (
                                f"FINE_POINT refused: preconditions unmet "
                                f"(0x02C5 bitmask=0x{mask:02X})")}
            s = self._state
            # Latch the ground's INTENT. Every automatic transition after this
            # (eclipse, desat, safing) resolves its return path against
            # `commanded_mode`, which is what makes a demotion recoverable.
            # Transients are not intents: commanding SLEW/DESAT/ECLIPSE does
            # not change where the spacecraft is supposed to end up.
            if mode in self._OPERATIONAL_MODES:
                s.commanded_mode = mode
            elif mode == MODE_OFF:
                s.commanded_mode = MODE_OFF
            self._set_mode(mode, reason="commanded by ground (AOCS_SET_MODE)")
            return {"success": True}

        elif command == "desaturate":
            s = self._state
            # Guard the dispatch path itself (S8 func 1 and the S19 ea_id 2001
            # autonomy rule both land here). A dump mid-strip truncates the
            # strip; a dump commanded while OFF/DETUMBLE is meaningless.
            if s.mode in (MODE_OFF, MODE_SAFE_BOOT, MODE_DETUMBLE):
                return {"success": False, "code": "MODE_INVALID",
                        "message": (f"Desaturation refused in "
                                    f"{_MODE_NAMES.get(s.mode, '?')}")}
            if self._desat_inhibited(s):
                why = "payload imaging" if s.desat_inhibit_auto \
                    else "operator inhibit"
                return {"success": False, "code": "DESAT_INHIBITED",
                        "message": f"Desaturation refused: {why}"}
            if s.mode == MODE_FINE_POINT:
                return {"success": False, "code": "DESAT_REFUSED_FINE",
                        "message": ("Desaturation refused in FINE_POINT — "
                                    "leave FINE first (a dump drops fine "
                                    "pointing and the strip is lost)")}
            s.return_mode = s.mode if s.mode in self._OPERATIONAL_MODES \
                else s.commanded_mode
            self._enter_desat(s, "commanded by ground (AOCS_DESATURATE)")
            return {"success": True}

        elif command == "set_desat_inhibit":
            self._state.desat_inhibit = bool(cmd.get("inhibit", True))
            return {"success": True,
                    "message": f"Desat inhibit {'SET' if self._state.desat_inhibit else 'CLEARED'}"}

        elif command == "slew_to":
            q = cmd.get("quaternion", [0, 0, 0, 1])
            s = self._state
            s.slew_target_q = list(q)
            s.slew_start_q = list(s.q_true)
            s.slew_elapsed = 0.0
            # A slew is a transient: remember where to hand control back.
            if s.mode in self._OPERATIONAL_MODES:
                s.return_mode = s.mode
            self._set_mode(MODE_SLEW, reason="commanded slew manoeuvre")
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
            # 2026-08c (owner ruling, item 8): EO-Sat-1 flies ONE 3-axis
            # magnetometer — there is no redundant unit to select, exactly
            # like the gyro fit. Refused honestly rather than pretending to
            # switch to hardware that does not exist. Power switching of the
            # one unit remains legitimate via S2 device 0x0209.
            return {"success": False, "error_code": 0x0007,
                    "message": ("MAG_SELECT refused: a single magnetometer "
                                "is fitted — no redundant unit exists "
                                "(power the one unit via S2 device 0x0209)")}

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
            if s.mode in self._OPERATIONAL_MODES:
                s.return_mode = s.mode
            self._set_mode(MODE_SLEW,
                           reason=f"commanded slew at {rate_dps:.2f} deg/s")
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
            # Was: `self._gyro_bias = [0, 0, 0]; return success`. That reached
            # into the truth state of a process the spacecraft cannot observe,
            # left the injected hardware bias untouched, and completed in zero
            # time with no preconditions and no telemetry. See the
            # "Gyro bias calibration" block above for what it does now.
            action = int(cmd.get("action", 0))
            if action == 1:            # operator abort
                s = self._state
                if s.gyro_cal_state != self.GYRO_CAL_RUNNING:
                    s.gyro_cal_reject = self.CAL_ERR_NOT_RUNNING
                    return {"success": False,
                            "error_code": self.CAL_ERR_NOT_RUNNING,
                            "message": failure_code_text(
                                self.CAL_ERR_NOT_RUNNING)}
                self._abort_gyro_calibration(self.CAL_ERR_OPERATOR,
                                             "abandoned by the operator")
                return {"success": True,
                        "message": "Gyro bias calibration aborted"}
            return self._start_gyro_calibration(
                float(cmd.get("duration_s", 0.0)))

        elif command == "gyro_cal_status":
            s = self._state
            return {
                "success": True,
                "state": int(s.gyro_cal_state),
                "progress_pct": float(s.gyro_cal_progress),
                "result": int(s.gyro_cal_result),
                "reject_code": int(s.gyro_cal_reject),
                "bias_dps": list(s.gyro_cal_bias),
                "comp_dps": list(s.gyro_cal_comp),
            }

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

    # ─── Failure injection ───────────────────────────────────────────
    #
    # Single source of truth for the injection parameters (see
    # smo_simulator/failure_spec.py). The instructor form, the
    # FailureManager's validation and the scenario content gate all read this.
    _WHEEL_LABELS = ["RW1 (pyramid +X)", "RW2 (pyramid +Y)",
                     "RW3 (pyramid -X)", "RW4 (pyramid -Y)"]
    _BODY_FACES = ["px", "mx", "py", "my", "pz", "mz"]

    FAILURE_SPECS = {
        "rw_bearing": FailureSpec(
            name="rw_bearing", label="Reaction wheel bearing degradation",
            effect="Adds bearing friction on the selected wheel: current and "
                   "temperature rise, torque authority falls; >= 0.95 takes "
                   "the wheel offline.",
            magnitude=ParamSpec(
                name="magnitude", type=T_FLOAT, label="Bearing degradation",
                unit="fraction", min=0.0, max=1.0, default=0.5, step=0.01),
            params=(enum("wheel", "Reaction wheel", [0, 1, 2, 3],
                         labels=_WHEEL_LABELS, default=0),),
        ),
        "rw_seizure": FailureSpec(
            name="rw_seizure", label="Reaction wheel seizure",
            effect="Selected wheel seizes: speed to zero, wheel disabled, "
                   "momentum dumped to the body — the 4-wheel pyramid drops "
                   "to 3-wheel control.",
            magnitude=None,
            params=(enum("wheel", "Reaction wheel", [0, 1, 2, 3],
                         labels=_WHEEL_LABELS, default=0),),
            reversible=False,
            notes="Clear removes the friction but the wheel stays disabled "
                  "until re-enabled by command.",
        ),
        "multi_wheel_failure": FailureSpec(
            name="multi_wheel_failure", label="Multiple wheel failure",
            effect="Fails two or more wheels at once; with fewer than 3 active "
                   "the AOCS drops to COARSE_SUN.",
            magnitude=None,
            params=(enum("wheels", "Wheels to fail", [0, 1, 2, 3],
                         labels=_WHEEL_LABELS, default=(0, 1), multi=True),),
        ),
        "gyro_bias": FailureSpec(
            name="gyro_bias", label="Gyro rate bias",
            effect="Adds a PERSISTENT rate bias to the selected gyro channel "
                   "(0x0210-0x0212 and the MEKF input). Not the mean-reverting "
                   "Gauss-Markov state — this one does not heal itself.",
            magnitude=None,
            params=(
                enum("axis", "Gyro axis", [0, 1, 2, "all"],
                     labels=["X (roll)", "Y (pitch)", "Z (yaw)",
                             "All three axes"], default=0),
                number("bias", "Rate bias", unit="deg/s", lo=-5.0, hi=5.0,
                       default=0.05, step=0.001),
            ),
            notes="Three gyros, no spare: a biased channel is not switchable "
                  "away from.",
        ),
        "st_blind": FailureSpec(
            name="st_blind", label="Star tracker blinded",
            effect="Selected tracker reports BLIND (status 3) and stops "
                   "supplying attitude; the MEKF coasts on gyros.",
            magnitude=ParamSpec(name="magnitude", type=T_BOOL,
                                label="Blinded", default=True),
            params=(enum("unit", "Star tracker", [1, 2],
                         labels=["STR-1 (primary)", "STR-2 (redundant)"],
                         default=1),),
        ),
        "st_failure": FailureSpec(
            name="st_failure", label="Star tracker hardware failure",
            effect="Selected tracker permanently FAILED (status 4, 0 stars); "
                   "redundancy logic must switch to the other unit.",
            magnitude=None,
            params=(enum("unit", "Star tracker", [1, 2],
                         labels=["STR-1 (primary)", "STR-2 (redundant)"],
                         default=1),),
        ),
        "css_failure": FailureSpec(
            name="css_failure", label="Coarse sun sensor assembly failure",
            effect="Whole CSS assembly invalid — sun vector unavailable, "
                   "coarse sun pointing degraded.",
            magnitude=None,
        ),
        "css_head_fail": FailureSpec(
            name="css_head_fail", label="Coarse sun sensor head failure",
            effect="One CSS head on the selected body face reads zero; the "
                   "sun vector skews toward the remaining heads.",
            magnitude=None,
            params=(enum("face", "CSS head (body face)", _BODY_FACES,
                         labels=[f.upper() for f in _BODY_FACES],
                         default="px"),),
        ),
        "mag_failure": FailureSpec(
            name="mag_failure", label="Magnetometer assembly failure",
            effect="Both magnetometers invalid — B-dot detumble and MTQ "
                   "momentum dumping lose their field reference.",
            magnitude=None,
        ),
        # mag_a_fail / mag_b_fail retired (2026-08c item 8): there is ONE
        # magnetometer and no fail-over — use `mag_failure`.
        "mtq_failure": FailureSpec(
            name="mtq_failure", label="Magnetorquer failure",
            effect="Selected magnetorquer produces no dipole: that axis loses "
                   "detumble/momentum-dump authority.",
            magnitude=None,
            params=(enum("axis", "Magnetorquer axis", ["x", "y", "z"],
                         labels=["MTQ-X", "MTQ-Y", "MTQ-Z"], default="x"),),
        ),
        "gain_corruption": FailureSpec(
            name="gain_corruption", label="Control gain register corruption",
            effect="Writes a wrong scale factor into the AOCS torque-gain "
                   "register for one body axis (0x20100000 block). The control "
                   "law over/under-drives that axis and attitude error diverges; "
                   "diagnose with S6 MEM_DUMP, recover with MEM_LOAD.",
            magnitude=None,
            params=(
                enum("axis", "Body axis", ["x", "y", "z"],
                     labels=["X (roll)", "Y (pitch)", "Z (yaw)"], default="y"),
                number("factor", "Corrupted gain", unit="x nominal", lo=0.0,
                       hi=100.0, default=10.0, step=0.1),
            ),
        ),
    }

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
            # Injected into the PERSISTENT offset, not into `_gyro_bias`: the
            # latter is now a Gauss-Markov state that mean-reverts with
            # τ = 1800 s, so a fault written there would quietly heal itself in
            # half an hour. `axis=3` (or "all") biases all three channels.
            bias = float(kw.get("bias", kw.get("magnitude", 0.05)))
            axis = kw.get("axis", 0)
            if axis in ("all", 3, -1):
                self._gyro_bias_inject = [bias, bias, bias]
            else:
                self._gyro_bias_inject[int(axis) % 3] = bias

        elif failure == "st_blind":
            # Temporary blinding of a SPECIFIC tracker. This used to blind
            # whichever unit happened to be `st_selected`, so an instructor
            # could not blind STR-2 while STR-1 was primary — and the clear
            # path (which does take `unit`) could then un-blind a different
            # box than the inject blinded.
            sel = int(kw.get("unit", s.st_selected))
            if sel not in (1, 2):
                logger.warning("AOCS st_blind: invalid unit %r (no-op)", sel)
            elif magnitude:
                setattr(s, f"st{sel}_status", 3)  # BLIND
                self._st_blind_units.add(sel)
                self._gyro_bias = [self.rng.gauss(0, 0.005) for _ in range(3)]
            else:
                setattr(s, f"st{sel}_status", 2)  # TRACKING
                self._st_blind_units.discard(sel)

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
            self._gyro_bias_inject = [0.0, 0.0, 0.0]
            # `gyro_cal_comp` is deliberately NOT cleared. The compensation
            # table is flight-software state the OPERATOR loaded with
            # AOCS_GYRO_CALIBRATION, not failure state the instructor injected,
            # and clearing it here would reach into the spacecraft on the
            # operator's behalf. If a crew calibrated against this bias and the
            # instructor then removes the hardware fault, the stale table
            # over-corrects and the gyro reads the compensation back with the
            # opposite sign — which is exactly what would happen in flight, is
            # fully diagnosable (aocs.gyro_bias_resid 0x02D2 non-zero with
            # aocs.gyro_cal_comp_mag 0x02CE non-zero), and is fixed by
            # re-running the calibration.

        elif failure in ("st_blind", "st_failure"):
            unit = int(kw.get("unit", s.st_selected))
            setattr(s, f"st{unit}_failed", False)
            setattr(s, f"st{unit}_status", 2)  # TRACKING
            if failure == "st_blind":
                self._st_blind_units.discard(unit)
                self._gyro_bias = [0.0, 0.0, 0.0]

        elif failure == "css_failure":
            s.css_failed = False

        elif failure == "mag_failure":
            s.mag_failed = False

        elif failure == "mtq_failure":
            axis = kw.get("axis", "x")
            setattr(s, f"mtq_{axis}_failed", False)

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
    _EXTRA_STATE_KEYS = ("_gyro_bias", "_gyro_bias_inject",
                         "_bearing_degradation", "_st_blind_units",
                         "_gain_corruption_saved", "_rng_state", "_ekf")

    def get_state(self) -> dict[str, Any]:
        import dataclasses
        from smo_common.state import rng_state_to_json
        d = dataclasses.asdict(self._state)
        d["_gyro_bias"] = list(self._gyro_bias)
        # The injected (persistent) bias offset is failure state, not a
        # mean-reverting process — a snapshot that dropped it would silently
        # heal an active gyro_bias failure on restore.
        d["_gyro_bias_inject"] = list(self._gyro_bias_inject)
        d["_bearing_degradation"] = list(self._bearing_degradation)
        # Injected star-tracker blinding is persistent failure state; a
        # snapshot that dropped it healed the fault on restore.
        d["_st_blind_units"] = sorted(self._st_blind_units)
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
        self._gyro_bias_inject = list(state.get("_gyro_bias_inject", [0.0] * 3))
        self._bearing_degradation = list(state.get("_bearing_degradation", [0.0] * 4))
        self._st_blind_units = {int(u) for u in state.get("_st_blind_units", ())}
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

"""SMO Simulator — Enhanced AOCS Model.

Quaternion attitude simulation, 9-mode state machine with transition guards,
dual star trackers, CSS sun sensor, magnetorquers, reaction wheel management,
gyro bias estimation, and comprehensive failure injection.
"""
import math
import random
from dataclasses import dataclass, field
from typing import Any

from smo_common.models.subsystem import SubsystemModel

_DEG = math.pi / 180.0
_RAD = 180.0 / math.pi


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

_MODE_NAMES = {
    0: "OFF", 1: "SAFE_BOOT", 2: "DETUMBLE", 3: "COARSE_SUN",
    4: "NOMINAL", 5: "FINE_POINT", 6: "SLEW", 7: "DESAT", 8: "ECLIPSE",
}

# Minimum dwell times per mode (seconds) before auto-transition allowed
_MIN_DWELL = {
    MODE_OFF: 0, MODE_SAFE_BOOT: 5.0, MODE_DETUMBLE: 10.0,
    MODE_COARSE_SUN: 20.0, MODE_NOMINAL: 10.0, MODE_FINE_POINT: 10.0,
    MODE_SLEW: 0, MODE_DESAT: 10.0, MODE_ECLIPSE: 5.0,
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
        self._gyro_bias = [0.0, 0.0, 0.0]
        self._bearing_degradation = [0.0, 0.0, 0.0, 0.0]
        self._target_q = [0.0, 0.0, 0.0, 1.0]
        self._kp = 0.02
        self._slew_target_q = None
        self._orbit_phase = 0.0
        self._rw_max_speed = 5500
        self._rw_desat_speed = 200
        self._rw_inertia = 0.005  # kg*m^2 per wheel
        self._param_ids: dict[str, int] = {}
        # Emergency rate threshold (deg/s) — triggers DETUMBLE
        self._emergency_rate_threshold = 2.0
        # Previous eclipse state for eclipse entry/exit detection
        self._prev_in_eclipse = False
        # Slew maneuver state
        self._slew_rate_dps = 0.5  # deg/s, configurable
        self._slew_start_q = None
        self._slew_start_time = None
        # Event tracking (to avoid repeated events)
        self._prev_st1_status = 0
        self._prev_st2_status = 0
        self._prev_css_valid = False
        self._prev_gps_fix = 0
        self._prev_momentum_saturation = False
        # Attitude error threshold for event (degrees)
        self._att_error_threshold_deg = 5.0
        # Bearing health tracking (0-100%)
        self._rw_bearing_health = [100.0, 100.0, 100.0, 100.0]
        # GPS start mode timing (defects/reviews/aocs.md Defect #3)
        self._gps_cold_ttff_s = 60.0   # Cold start TTFF: 60s
        self._gps_warm_ttff_s = 30.0   # Warm start TTFF: 30s
        self._gps_hot_ttff_s = 5.0     # Hot start TTFF: 5s
        # IGRF/WMM field model placeholder (simple altitude/latitude bins)
        self._igrf_model_initialized = False

    @property
    def name(self) -> str:
        return "aocs"

    def configure(self, config: dict[str, Any]) -> None:
        rw = config.get("reaction_wheels", {})
        self._rw_max_speed = rw.get("max_speed_rpm", 5500)
        self._rw_desat_speed = rw.get("desaturation_speed_rpm", 200)
        nom_speed = rw.get("nominal_speed_rpm", 1200)
        self._rw_inertia = rw.get("inertia_kgm2", 0.005)
        self._state.rw_speed = [float(nom_speed)] * 4

        self._emergency_rate_threshold = config.get(
            "emergency_rate_threshold_dps", 2.0
        )

        # Allow setting of slew rate and attitude thresholds from config
        self._slew_rate_dps = config.get("slew_rate_dps", 0.5)
        self._att_error_threshold_deg = config.get("attitude_error_threshold_deg", 5.0)

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
            "slew_state": 0x0285,
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

        elif s.mode == MODE_NOMINAL:
            # Eclipse entry -> ECLIPSE mode. Defect #3: auto-trigger on in_eclipse.
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
            # Deterministic FOV geometry: ST1 zenith (+Z), ST2 nadir (-Z)
            blinded = False
            if not orbit_state.in_eclipse:
                sun_body = self._get_sun_body_vector(orbit_state)
                if unit == 1:
                    # ST1 zenith (+Z boresight)
                    blinded = self._check_st_blinding(sun_body, (0, 0, 1))
                else:
                    # ST2 nadir (-Z boresight) — also susceptible to earth limb
                    blinded = self._check_st_blinding(sun_body, (0, 0, -1))

            if blinded:
                setattr(s, status_attr, 3)  # BLIND
                setattr(s, stars_attr, 0)
            else:
                setattr(s, status_attr, 2)  # TRACKING
                num = random.randint(8, 20)
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

    def _get_sun_body_vector(self, orbit_state):
        """Get sun vector in body frame (approximation from CSS or orbit state)."""
        s = self._state
        mag = (s.css_sun_x ** 2 + s.css_sun_y ** 2 + s.css_sun_z ** 2) ** 0.5
        if mag > 0.1:
            return (s.css_sun_x / mag, s.css_sun_y / mag, s.css_sun_z / mag)
        # Fallback: approximate from orbit beta angle and orbit phase
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

        # Compute true sun direction in body frame from orbit geometry
        beta = orbit_state.solar_beta_deg * _DEG
        phase = self._orbit_phase * _DEG
        sun_body = (
            math.cos(beta) * math.cos(phase),
            math.cos(beta) * math.sin(phase),
            math.sin(beta),
        )

        # Compute per-head illumination from sun vector and face normals
        for face, normal in CSS_NORMALS.items():
            if s.css_head_failed.get(face, False):
                s.css_heads[face] = 0.0
                continue
            cos_angle = sum(n * sb for n, sb in zip(normal, sun_body))
            illumination = max(0.0, cos_angle)
            # Add noise
            illumination += random.gauss(0, 0.02)
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

    def _compute_igrf_field(self, lat_deg: float, lon_deg: float,
                           alt_km: float) -> tuple[float, float, float]:
        """Simple IGRF-13 field model lookup (position-dependent).

        Returns (Bx, By, Bz) in nanoTesla for given latitude/longitude/altitude.
        This is a simplified analytic model; a real implementation would use
        IGRF coefficients or a lookup table.

        See defects/reviews/aocs.md Defect #2.
        """
        # Scaled field magnitude by latitude (dipole-like, varies ±30% by latitude)
        lat_rad = lat_deg * _DEG
        field_scale = 50000.0 * (0.7 + 0.3 * abs(math.sin(lat_rad)))

        # Altitude variation: -0.03 nT/km typical for dipole
        alt_correction = 1.0 - (alt_km - 500.0) * 0.00003
        field_scale *= alt_correction

        # Position-dependent field direction (simplified to orbit phase + lat/lon)
        lon_rad = lon_deg * _DEG
        phi = self._orbit_phase * _DEG

        # Combine orbit phase and geographic position
        bx = field_scale * 0.8 * math.cos(phi + lon_rad * 0.1)
        by = field_scale * 0.5 * math.sin(phi)
        bz = -field_scale * (0.3 + 0.2 * math.sin(2 * phi)) * math.cos(lat_rad)

        return (bx, by, bz)

    def _tick_magnetometer(self, s: AOCSState, orbit_state: Any = None) -> None:
        """Model dual magnetometer readings from IGRF field model.

        If orbit_state is provided, use position-dependent field (Defect #2).
        Otherwise fall back to phase-only model.
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

        # Compute true magnetic field using IGRF model (if orbit_state available)
        if orbit_state is not None:
            true_x, true_y, true_z = self._compute_igrf_field(
                orbit_state.lat_deg, orbit_state.lon_deg, orbit_state.alt_km
            )
        else:
            # Fallback: phase-only model (backward compatible)
            phi = self._orbit_phase * _DEG
            true_x = 35000.0 * 0.8 * math.cos(phi)
            true_y = 35000.0 * 0.5 * math.sin(phi)
            true_z = -35000.0 * (0.3 + 0.2 * math.sin(2 * phi))

        # Mag A reading
        if not s.mag_a_failed:
            s.mag_a_x = true_x + s.mag_a_bias[0] + random.gauss(0, s.mag_a_noise)
            s.mag_a_y = true_y + s.mag_a_bias[1] + random.gauss(0, s.mag_a_noise)
            s.mag_a_z = true_z + s.mag_a_bias[2] + random.gauss(0, s.mag_a_noise)

        # Mag B reading
        if not s.mag_b_failed:
            s.mag_b_x = true_x + s.mag_b_bias[0] + random.gauss(0, s.mag_b_noise)
            s.mag_b_y = true_y + s.mag_b_bias[1] + random.gauss(0, s.mag_b_noise)
            s.mag_b_z = true_z + s.mag_b_bias[2] + random.gauss(0, s.mag_b_noise)

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
        """Apply magnetorquer duty cycles (used in DETUMBLE and DESAT)."""
        if not s.mtq_enabled:
            s.mtq_x_duty = 0.0
            s.mtq_y_duty = 0.0
            s.mtq_z_duty = 0.0
            return
        # Failed axes produce no torque but duty is still commanded
        # (the model shows commanded duty; actual torque is zero on failed axis)

    # ─── Mode tick functions ─────────────────────────────────────────

    def _tick_off(self, s: AOCSState, dt: float) -> None:
        """OFF/IDLE — no control, rates drift slowly."""
        s.rate_roll += random.gauss(0, 0.001) * dt
        s.rate_pitch += random.gauss(0, 0.001) * dt
        s.rate_yaw += random.gauss(0, 0.001) * dt
        s.att_error = 180.0  # Unknown attitude

    def _tick_safe_boot(self, s: AOCSState, dt: float) -> None:
        """SAFE_BOOT — hardware initialisation, magnetometer only."""
        s.rate_roll += random.gauss(0, 0.002) * dt
        s.rate_pitch += random.gauss(0, 0.002) * dt
        s.rate_yaw += random.gauss(0, 0.002) * dt
        s.att_error = 90.0 + random.gauss(0, 2.0)
        s.submode = min(int(s.time_in_mode / 10.0), 2)  # 0=init, 1=mag_cal, 2=ready

    def _tick_detumble(self, s: AOCSState, dt: float) -> None:
        """DETUMBLE — B-dot control using magnetorquers."""
        # B-dot: duty proportional to rate of change of B-field
        damp = 0.1 * dt
        if s.mag_valid and s.mtq_enabled:
            # Magnetorquer duty proportional to body rates (B-dot proxy)
            s.mtq_x_duty = max(-1.0, min(1.0, -s.rate_roll * 0.5))
            s.mtq_y_duty = max(-1.0, min(1.0, -s.rate_pitch * 0.5))
            s.mtq_z_duty = max(-1.0, min(1.0, -s.rate_yaw * 0.5))
            # Apply failed axis
            if s.mtq_x_failed:
                effective_x = 0.0
            else:
                effective_x = s.mtq_x_duty
            if s.mtq_y_failed:
                effective_y = 0.0
            else:
                effective_y = s.mtq_y_duty
            if s.mtq_z_failed:
                effective_z = 0.0
            else:
                effective_z = s.mtq_z_duty
            # Damping from magnetorquers
            s.rate_roll *= (1.0 - damp * (0.5 + 0.5 * abs(effective_x)))
            s.rate_pitch *= (1.0 - damp * (0.5 + 0.5 * abs(effective_y)))
            s.rate_yaw *= (1.0 - damp * (0.5 + 0.5 * abs(effective_z)))
        else:
            # No mag or MTQ — rates drift
            s.rate_roll *= (1.0 - 0.01 * dt)  # minimal aero drag
            s.rate_pitch *= (1.0 - 0.01 * dt)
            s.rate_yaw *= (1.0 - 0.01 * dt)

        for attr in ("rate_roll", "rate_pitch", "rate_yaw"):
            setattr(s, attr, getattr(s, attr) + random.gauss(0, 0.005))
        s.att_error = math.sqrt(
            s.rate_roll ** 2 + s.rate_pitch ** 2 + s.rate_yaw ** 2
        ) * 10.0

    def _tick_coarse_sun(self, s: AOCSState, dt: float) -> None:
        """COARSE_SUN_POINT — CSS + magnetometer, coarse sun pointing."""
        # Slowly reduce attitude error toward ~5 deg
        target_err = 5.0
        s.att_error += (target_err - s.att_error) * 0.02 * dt + random.gauss(0, 0.3)
        s.att_error = max(0.1, s.att_error)
        # Rates are small but not zero
        s.rate_roll = random.gauss(0, 0.02)
        s.rate_pitch = random.gauss(0, 0.02)
        s.rate_yaw = random.gauss(0, 0.02)

    def _tick_nominal(self, s: AOCSState, dt: float) -> None:
        """NOMINAL_NADIR — star tracker + gyros + RW, nadir pointing."""
        err = self._quat_angle_error(s.q, self._target_q)
        axis = self._quat_error_axis(s.q, self._target_q)
        delta = min(self._kp * err, 0.5) * dt
        s.q = self._rotate_quat(s.q, axis, delta * _DEG)
        s.q = self._normalise(s.q)
        for i in range(3):
            s.q[i] += random.gauss(0, 0.00002)
        s.q = self._normalise(s.q)
        for i, attr in enumerate(("rate_roll", "rate_pitch", "rate_yaw")):
            setattr(s, attr, self._gyro_bias[i] + random.gauss(0, 0.0005))
        s.att_error = self._quat_angle_error(
            s.q, self._target_q
        ) + random.gauss(0, 0.002)
        s.att_error = max(0.0, s.att_error)

    def _tick_fine_point(self, s: AOCSState, dt: float) -> None:
        """FINE_POINT — tight control bandwidth, highest accuracy."""
        if not s.st_valid:
            # Lost ST validity, fall back to NOMINAL
            self._set_mode(MODE_NOMINAL)
            return
        n_active = sum(1 for w in s.active_wheels if w)
        if n_active < 4:
            # Need all 4 wheels for fine point
            self._set_mode(MODE_NOMINAL)
            return

        err = self._quat_angle_error(s.q, self._target_q)
        axis = self._quat_error_axis(s.q, self._target_q)
        # Tighter gain
        delta = min(self._kp * 1.5 * err, 0.3) * dt
        s.q = self._rotate_quat(s.q, axis, delta * _DEG)
        s.q = self._normalise(s.q)
        for i in range(3):
            s.q[i] += random.gauss(0, 0.000005)
        s.q = self._normalise(s.q)
        for i, attr in enumerate(("rate_roll", "rate_pitch", "rate_yaw")):
            setattr(s, attr, self._gyro_bias[i] + random.gauss(0, 0.0001))
        s.att_error = self._quat_angle_error(
            s.q, self._target_q
        ) + random.gauss(0, 0.0005)
        s.att_error = max(0.0, s.att_error)

    def _tick_slew(self, s: AOCSState, dt: float) -> None:
        """SLEW — rotate toward commanded target quaternion using SLERP."""
        if self._slew_target_q is None:
            self._set_mode(MODE_NOMINAL)
            return

        # Track elapsed time in slew
        if self._slew_start_time is None:
            self._slew_start_time = 0.0
        self._slew_start_time += dt

        # Compute total angle and expected duration
        total_angle = self._quat_angle_error(self._slew_start_q or s.q, self._slew_target_q)
        max_rate = min(self._slew_rate_dps, total_angle / max(0.01, total_angle / 10.0))
        expected_duration = total_angle / max(self._slew_rate_dps, 0.1)

        # Current error and interpolation progress
        err = self._quat_angle_error(s.q, self._slew_target_q)

        # SLERP interpolation: smooth rotation from start to target
        if total_angle > 0.01:
            # Progress as fraction of total angle
            progress = (total_angle - err) / total_angle
            progress = max(0.0, min(1.0, progress))
        else:
            progress = 1.0

        # SLERP: smooth interpolation between quaternions
        s.q = self._slerp_quat(self._slew_start_q or s.q, self._slew_target_q, progress)
        s.q = self._normalise(s.q)

        # Body rates proportional to slew rate
        axis = self._quat_error_axis(s.q, self._slew_target_q)
        rate = min(self._slew_rate_dps, err * 0.5)
        s.rate_roll = rate * axis[0]
        s.rate_pitch = rate * axis[1]
        s.rate_yaw = rate * axis[2]

        s.att_error = err

        # Slew complete when angle < tolerance or time exceeded
        if err < 0.1 or self._slew_start_time > expected_duration * 2.0:
            s.q = list(self._slew_target_q)
            s.q = self._normalise(s.q)
            s.att_error = 0.0
            self._set_mode(MODE_NOMINAL)
            self._slew_target_q = None
            self._slew_start_time = None
            self._slew_start_q = None

    def _tick_desat(self, s: AOCSState, dt: float) -> None:
        """DESATURATION — magnetorquers dump wheel momentum."""
        for i, active in enumerate(s.active_wheels):
            if active:
                target = (
                    self._rw_desat_speed
                    if s.rw_speed[i] > 0
                    else -self._rw_desat_speed
                )
                s.rw_speed[i] += (target - s.rw_speed[i]) * 0.05 * dt
        s.att_error = 0.5 + random.gauss(0, 0.05)
        # MTQ duty reflects desaturation effort
        if s.mtq_enabled:
            s.mtq_x_duty = max(-1.0, min(1.0, random.gauss(0, 0.3)))
            s.mtq_y_duty = max(-1.0, min(1.0, random.gauss(0, 0.3)))
            s.mtq_z_duty = max(-1.0, min(1.0, random.gauss(0, 0.3)))

    def _tick_eclipse(self, s: AOCSState, dt: float) -> None:
        """ECLIPSE_PROPAGATE — gyro-only propagation when ST blinded."""
        # Gyro drift causes slow attitude error growth
        drift_rate = 0.001  # deg/s
        s.att_error += drift_rate * dt + random.gauss(0, 0.01)
        s.att_error = max(0.0, min(30.0, s.att_error))
        for i, attr in enumerate(("rate_roll", "rate_pitch", "rate_yaw")):
            setattr(s, attr, self._gyro_bias[i] + random.gauss(0, 0.001))

    # ─── Gyro and GPS models ─────────────────────────────────────────

    def _tick_gyro_and_gps(self, s: AOCSState, dt: float,
                            orbit_state: Any) -> None:
        """Model gyroscope bias estimation and GPS receiver."""
        # Gyro bias drift (random walk, ~0.003 deg/s/sqrt(hr) typical MEMS)
        drift_sigma = 0.00005 * math.sqrt(dt)
        for i, attr in enumerate(("gyro_bias_x", "gyro_bias_y", "gyro_bias_z")):
            bias = self._gyro_bias[i]
            bias += random.gauss(0, drift_sigma)
            self._gyro_bias[i] = max(-0.1, min(0.1, bias))
            setattr(s, attr, self._gyro_bias[i])

        # Gyro temperature (correlated with RW proximity heat)
        avg_rw_temp = sum(s.rw_temp) / 4.0
        s.gyro_temp += (avg_rw_temp - 5.0 - s.gyro_temp) / 300.0 * dt
        s.gyro_temp += random.gauss(0, 0.01)

        # GPS receiver model with cold/warm/hot start tracking (Defect #3)
        # See defects/reviews/aocs.md for start mode semantics.
        if s.mode in (MODE_OFF, MODE_SAFE_BOOT):
            s.gps_fix = 0
            s.gps_num_sats = 0
            s.gps_pdop = 99.9
            # Reset TTFF timer when powered down
            if s.mode == MODE_OFF:
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
                s.gps_num_sats = random.randint(0, 3)
                s.gps_fix = 0 if s.gps_num_sats < 3 else 1
                s.gps_pdop = 99.9
            elif s.gps_ttff_timer < ttff_target * 0.8:
                # Middle phase: acquiring position fix
                s.gps_num_sats = random.randint(4, 8)
                s.gps_fix = 2 if s.gps_num_sats >= 4 else 1
                s.gps_pdop = 3.0 + random.gauss(0, 0.5)
                s.gps_pdop = max(1.5, min(8.0, s.gps_pdop))
            elif s.gps_ttff_timer >= ttff_target:
                # Post-TTFF: full lock with velocity
                s.gps_num_sats = random.randint(6, 12)
                s.gps_fix = 3  # 3D + velocity
                s.gps_pdop = 1.2 + random.gauss(0, 0.3)
                s.gps_pdop = max(0.8, min(6.0, s.gps_pdop))
            else:
                # Intermediate: progressing toward full fix
                s.gps_num_sats = random.randint(4, 8)
                s.gps_fix = 2
                s.gps_pdop = 2.0 + random.gauss(0, 0.5)
                s.gps_pdop = max(1.5, min(6.0, s.gps_pdop))

        # Magnetometer total field magnitude
        s.mag_field_total = math.sqrt(
            s.mag_x ** 2 + s.mag_y ** 2 + s.mag_z ** 2
        )

    # ─── Wheels and momentum ─────────────────────────────────────────

    def _tick_wheels(self, s: AOCSState, dt: float) -> None:
        """Update reaction wheel speeds, temperatures, and currents."""
        for i in range(4):
            if not s.active_wheels[i]:
                s.rw_speed[i] *= (1.0 - 0.05 * dt)
                s.rw_current[i] = 0.0
                s.rw_enabled[i] = False
                continue

            s.rw_enabled[i] = True
            dist = random.gauss(0, 0.5)
            degrade = 1.0 + self._bearing_degradation[i] * 10.0
            friction = 0.00005 * degrade * abs(s.rw_speed[i])
            s.rw_speed[i] += (
                dist - friction * math.copysign(1, s.rw_speed[i])
            ) * dt
            s.rw_speed[i] = max(
                -self._rw_max_speed,
                min(self._rw_max_speed, s.rw_speed[i]),
            )
            s.rw_speed[i] += random.gauss(0, 1.0)

            # Current draw: baseline + speed-dependent + degradation
            base_current = 0.05  # amps
            speed_current = abs(s.rw_speed[i]) / self._rw_max_speed * 0.15
            degrade_current = self._bearing_degradation[i] * 0.3
            s.rw_current[i] = base_current + speed_current + degrade_current
            s.rw_current[i] += random.gauss(0, 0.005)
            s.rw_current[i] = max(0.0, s.rw_current[i])

            # Thermal model
            heat = 0.00002 * abs(s.rw_speed[i]) * (
                1.0 + 5.0 * self._bearing_degradation[i]
            )
            cool = (s.rw_temp[i] - 20.0) / 300.0
            s.rw_temp[i] += (heat - cool) * dt + random.gauss(0, 0.02)

        # Total system momentum (Nms)
        # H = sum(I * omega) for each wheel, omega in rad/s
        rpm_to_rads = 2.0 * math.pi / 60.0
        s.total_momentum = sum(
            self._rw_inertia * abs(s.rw_speed[i]) * rpm_to_rads
            for i in range(4) if s.active_wheels[i]
        )

    # ─── Main tick ───────────────────────────────────────────────────

    def tick(self, dt: float, orbit_state: Any,
             shared_params: dict[int, float]) -> None:
        s = self._state
        p = self._param_ids

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

        # Update sensors
        self._tick_star_trackers(s, dt, orbit_state)
        self._tick_css(s, orbit_state)
        self._tick_magnetometer(s, orbit_state)  # Pass orbit_state for IGRF model
        self._tick_gyro_and_gps(s, dt, orbit_state)

        # Emergency checks (before mode tick)
        if not self._check_emergency(s):
            # Auto transitions
            self._check_auto_transitions(s, dt, orbit_state)

        # Mode tick
        prev_mode = s.mode
        s.time_in_mode += dt
        if s.mode == MODE_OFF:
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

        # Wheels and momentum — always tick (wheels can be enabled individually
        # even when AOCS mode is OFF; they just aren't actively controlled)
        self._tick_wheels(s, dt)
        self._tick_magnetorquers(s, dt)

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
        if s.mode == MODE_SLEW and self._slew_start_time is not None:
            total_angle = self._quat_angle_error(
                self._slew_start_q or s.q, self._slew_target_q or s.q
            )
            expected_duration = total_angle / max(self._slew_rate_dps, 0.1)
            time_remaining = max(0.0, expected_duration - self._slew_start_time)
            shared_params[0x0280] = time_remaining
            # Slew progress
            progress = (expected_duration - time_remaining) / max(expected_duration, 0.1) * 100.0
            shared_params[0x0281] = min(100.0, max(0.0, progress))
        else:
            shared_params[0x0280] = 0.0
            shared_params[0x0281] = 0.0

        # Momentum saturation percentage
        max_momentum = self._rw_inertia * self._rw_max_speed * (2.0 * math.pi / 60.0) * 4
        if max_momentum > 0:
            momentum_pct = (s.total_momentum / max_momentum) * 100.0
        else:
            momentum_pct = 0.0
        shared_params[0x0282] = min(100.0, momentum_pct)

        # Attitude source (0=ST, 1=CSS, 2=Gyro)
        if s.st_valid:
            attitude_source = 0
        elif s.css_valid:
            attitude_source = 1
        else:
            attitude_source = 2
        shared_params[0x0283] = float(attitude_source)

        # RW bearing health (per wheel) - moved to 0x028E-0x0291 to avoid collision with slew_state
        for i in range(4):
            shared_params[0x028E + i] = self._rw_bearing_health[i]

        # Slew state (0=IDLE, 1=SLEWING, 2=SETTLING, 3=COMPLETE)
        if s.mode == MODE_SLEW:
            if self._slew_target_q is None:
                s.slew_state = 0  # IDLE
            else:
                err = self._quat_angle_error(s.q, self._slew_target_q)
                if err > 1.0:
                    s.slew_state = 1  # SLEWING
                else:
                    s.slew_state = 2  # SETTLING
        else:
            s.slew_state = 0  # IDLE (not in slew mode)
        shared_params[p.get("slew_state", 0x0285)] = float(s.slew_state)

        # CSS head health
        css_faces = ['px', 'mx', 'py', 'my', 'pz', 'mz']
        for j, face in enumerate(css_faces):
            health = 0.0 if s.css_head_failed.get(face, False) else 100.0
            shared_params[0x0288 + j] = health

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

        # RW_OVERSPEED (0x0201) — when any wheel > 5000 RPM
        for i in range(4):
            if abs(s.rw_speed[i]) > 5000.0:
                events.append({
                    'event_id': 0x0201,
                    'severity': 'MEDIUM',
                    'description': f'Reaction wheel {i+1} overspeed: {s.rw_speed[i]:.1f} RPM'
                })
                break  # Only report once per tick

        # RW_BEARING_DEGRADED (0x0202) — when bearing_health < 50%
        for i in range(4):
            health_pct = (1.0 - self._bearing_degradation[i]) * 100.0
            self._rw_bearing_health[i] = health_pct
            if health_pct < 50.0 and self._rw_bearing_health[i] < 50.0:
                if self._bearing_degradation[i] > 0.0:
                    events.append({
                        'event_id': 0x0202,
                        'severity': 'MEDIUM',
                        'description': f'Reaction wheel {i+1} bearing degradation: {health_pct:.1f}%'
                    })
                    break

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
            setattr(self, prev_attr, curr_status)

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

        # MOMENTUM_SATURATION (0x0205) — when total angular momentum > 90% of max
        max_momentum = self._rw_inertia * self._rw_max_speed * (2.0 * math.pi / 60.0) * 4
        if max_momentum > 0:
            momentum_pct = (s.total_momentum / max_momentum) * 100.0
            is_saturating = momentum_pct > 90.0
            if is_saturating and not self._prev_momentum_saturation:
                events.append({
                    'event_id': 0x0205,
                    'severity': 'MEDIUM',
                    'description': f'Momentum saturation: {momentum_pct:.1f}%'
                })
            self._prev_momentum_saturation = is_saturating

        # ATTITUDE_ERROR_HIGH (0x0206) — when pointing error > configured threshold
        if s.att_error > self._att_error_threshold_deg:
            events.append({
                'event_id': 0x0206,
                'severity': 'MEDIUM',
                'description': f'Attitude error exceeds threshold: {s.att_error:.2f} deg'
            })

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

        # GYRO_BIAS_HIGH (0x020B) — when gyro bias drift exceeds threshold
        bias_mag = math.sqrt(sum(b*b for b in self._gyro_bias))
        if bias_mag > 0.05:  # deg/s threshold
            events.append({
                'event_id': 0x020B,
                'severity': 'LOW',
                'description': f'Gyro bias drift high: {bias_mag:.5f} deg/s'
            })

        # CSS_DEGRADED (0x020C) — when multiple CSS heads fail
        failed_count = sum(1 for failed in s.css_head_failed.values() if failed)
        if failed_count >= 2 and not s.css_valid:
            events.append({
                'event_id': 0x020C,
                'severity': 'MEDIUM',
                'description': f'CSS degraded: {failed_count} heads failed'
            })

        # Dispatch all events to the engine
        if hasattr(self, '_engine') and self._engine:
            for event in events:
                self._engine.event_queue.put(event)

    # ─── Command handling ────────────────────────────────────────────

    def handle_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        command = cmd.get("command", "")

        if command == "set_mode":
            mode = int(cmd.get("mode", 0))
            if 0 <= mode <= 8:
                self._set_mode(mode)
                return {"success": True}
            return {"success": False, "message": f"Invalid mode: {mode}"}

        elif command == "desaturate":
            self._set_mode(MODE_DESAT)
            return {"success": True}

        elif command == "slew_to":
            q = cmd.get("quaternion", [0, 0, 0, 1])
            self._slew_target_q = list(q)
            self._set_mode(MODE_SLEW)
            return {"success": True}

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
            self._slew_target_q = list(q)
            self._slew_rate_dps = rate_dps
            self._slew_start_q = list(self._state.q)
            self._slew_start_time = 0.0
            self._set_mode(MODE_SLEW)
            return {"success": True, "message": "Slew maneuver started"}

        elif command == "check_momentum":
            max_momentum = self._rw_inertia * self._rw_max_speed * (2.0 * math.pi / 60.0) * 4
            momentum_pct = (self._state.total_momentum / max_momentum * 100.0) if max_momentum > 0 else 0.0
            return {
                "success": True,
                "total_momentum_nms": self._state.total_momentum,
                "saturation_percent": momentum_pct,
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
            if wheel == -1:
                # Ramp down all active wheels
                for i in range(4):
                    if self._state.active_wheels[i]:
                        self._state.rw_speed[i] = target_rpm
                return {"success": True, "message": "All wheels ramping down"}
            elif 0 <= wheel < 4:
                self._state.rw_speed[wheel] = target_rpm
                return {"success": True, "message": f"Wheel {wheel} ramping down"}
            return {"success": False, "message": "Invalid wheel index"}

        elif command == "set_deadband":
            deadband_deg = float(cmd.get("deadband_deg", 0.01))
            if deadband_deg < 0 or deadband_deg > 1.0:
                return {"success": False, "message": "Deadband must be 0-1 degree"}
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
                self._gyro_bias = [random.gauss(0, 0.005) for _ in range(3)]
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

    # ─── State save/restore ──────────────────────────────────────────

    def get_state(self) -> dict[str, Any]:
        import dataclasses
        d = dataclasses.asdict(self._state)
        d["_gyro_bias"] = list(self._gyro_bias)
        d["_bearing_degradation"] = list(self._bearing_degradation)
        return d

    def set_state(self, state: dict[str, Any]) -> None:
        self._gyro_bias = state.pop("_gyro_bias", [0.0] * 3)
        self._bearing_degradation = state.pop("_bearing_degradation", [0.0] * 4)
        for k, v in state.items():
            if hasattr(self._state, k):
                setattr(self._state, k, v)

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

"""
EO Mission Simulator — Attitude & Orbit Control Subsystem (AOCS)
Quaternion attitude simulation, reaction wheel speeds, magnetometer,
gyro bias model, AOCS mode state machine, and failure injection.
"""
import math
import random
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import (
    P_ATT_Q1, P_ATT_Q2, P_ATT_Q3, P_ATT_Q4,
    P_RATE_ROLL, P_RATE_PITCH, P_RATE_YAW,
    P_RW1_SPEED, P_RW2_SPEED, P_RW3_SPEED, P_RW4_SPEED,
    P_RW1_TEMP, P_RW2_TEMP, P_RW3_TEMP, P_RW4_TEMP,
    P_MAG_X, P_MAG_Y, P_MAG_Z,
    P_AOCS_MODE, P_ATT_ERROR,
    P_GPS_LAT, P_GPS_LON, P_GPS_ALT,
    P_GPS_VX, P_GPS_VY, P_GPS_VZ, P_SOLAR_BETA,
    RW_MAX_SPEED_RPM, RW_NOMINAL_SPEED_RPM, RW_DESATURATION_SPD,
    AOCS_MODE_NOMINAL, AOCS_MODE_DETUMBLE, AOCS_MODE_SAFE,
    AOCS_MODE_WHEEL_DESAT, AOCS_MODE_SLEW,
)

_DEG = math.pi / 180.0
_RAD = 180.0 / math.pi


@dataclass
class AOCSState:
    # Attitude quaternion [q1 q2 q3 q4] where q4 is scalar
    q: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    # Body rates (deg/s)
    rate_roll:  float = 0.0
    rate_pitch: float = 0.0
    rate_yaw:   float = 0.0
    # Attitude error (deg)
    att_error: float = 0.0
    # AOCS mode
    mode: int = AOCS_MODE_NOMINAL
    # Reaction wheel speeds (RPM), wheels 1-4
    rw_speed: List[float]  = field(default_factory=lambda: [
        RW_NOMINAL_SPEED_RPM, RW_NOMINAL_SPEED_RPM,
        RW_NOMINAL_SPEED_RPM, RW_NOMINAL_SPEED_RPM
    ])
    # Reaction wheel temperatures (°C)
    rw_temp: List[float] = field(default_factory=lambda: [28.0, 28.0, 28.0, 28.0])
    # Magnetometer (nT)
    mag_x: float = 25000.0
    mag_y: float = 10000.0
    mag_z: float = -40000.0
    # Star tracker valid
    st_valid: bool = True
    # Active wheel set (which wheels are in use)
    active_wheels: List[bool] = field(default_factory=lambda: [True, True, True, True])


class AOCSSubsystem:
    """
    AOCS simulation.

    Nominal mode: nadir-pointing quaternion with small Gaussian noise.
    Wheel speeds drift at a configurable rate and can be saturated.
    Failure modes: wheel seizure, gyro bias, star tracker blinding.
    """

    # Magnetometer field model constants (approximate mid-latitude)
    _MAG_B0      = 35000.0     # nT reference magnitude
    _MAG_NOISE   = 50.0        # nT  1-sigma noise
    # Gyro drift parameters
    _GYRO_DRIFT_NOMINAL   = 0.0001  # deg/s drift rate per axis (nominal)
    # Wheel desaturation threshold
    _SAT_THRESHOLD_RPM    = 4800.0
    # Wheel friction (speed decay per second, fraction of speed)
    _RW_FRICTION          = 0.00005
    # Wheel temperature rise from bearing friction (°C/s per RPM at full speed)
    _RW_TEMP_RISE_RATE    = 0.00002
    _RW_AMBIENT_TEMP      = 20.0
    _RW_THERMAL_TAU       = 300.0    # s

    def __init__(self, dt_s: float = 1.0):
        self.dt    = dt_s
        self.state = AOCSState()
        # Gyro bias on each axis (deg/s), injected by failure model
        self._gyro_bias = [0.0, 0.0, 0.0]
        # Bearing degradation per wheel (0=healthy, 1=seized)
        self._bearing_degradation = [0.0, 0.0, 0.0, 0.0]
        # Attitude target (nadir-pointing quaternion, updated from orbit)
        self._target_q = [0.0, 0.0, 0.0, 1.0]
        # Control law gain (fraction of error corrected per tick)
        self._kp = 0.02
        # Slew target
        self._slew_target_q = None
        # Orbit tick counter for magnetometer variation
        self._orbit_phase = 0.0

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self, orbit_state, shared_params: Dict) -> None:
        s    = self.state
        dt   = self.dt

        # 1. Update orbit-derived quantities
        self._orbit_phase = (self._orbit_phase + dt * (360.0 / 5700.0)) % 360.0
        shared_params[P_GPS_LAT]    = orbit_state.lat_deg
        shared_params[P_GPS_LON]    = orbit_state.lon_deg
        shared_params[P_GPS_ALT]    = orbit_state.alt_km
        shared_params[P_GPS_VX]     = orbit_state.vel_x
        shared_params[P_GPS_VY]     = orbit_state.vel_y
        shared_params[P_GPS_VZ]     = orbit_state.vel_z
        shared_params[P_SOLAR_BETA] = orbit_state.solar_beta_deg

        # 2. Mode-dependent attitude evolution
        if s.mode == AOCS_MODE_NOMINAL:
            self._tick_nominal(s, dt)
        elif s.mode == AOCS_MODE_DETUMBLE:
            self._tick_detumble(s, dt)
        elif s.mode == AOCS_MODE_SAFE:
            self._tick_safe(s, dt)
        elif s.mode == AOCS_MODE_WHEEL_DESAT:
            self._tick_desat(s, dt)
        elif s.mode == AOCS_MODE_SLEW:
            self._tick_slew(s, dt)

        # 3. Reaction wheel dynamics
        self._tick_wheels(s, dt)

        # 4. Magnetometer (orbit-varying sinusoidal)
        phi = self._orbit_phase * _DEG
        s.mag_x = self._MAG_B0 * 0.8 * math.cos(phi) + random.gauss(0, self._MAG_NOISE)
        s.mag_y = self._MAG_B0 * 0.5 * math.sin(phi) + random.gauss(0, self._MAG_NOISE)
        s.mag_z = -self._MAG_B0 * (0.3 + 0.2 * math.sin(2*phi)) + random.gauss(0, self._MAG_NOISE)

        # 5. Write shared parameters
        shared_params[P_ATT_Q1]     = s.q[0]
        shared_params[P_ATT_Q2]     = s.q[1]
        shared_params[P_ATT_Q3]     = s.q[2]
        shared_params[P_ATT_Q4]     = s.q[3]
        shared_params[P_RATE_ROLL]  = s.rate_roll
        shared_params[P_RATE_PITCH] = s.rate_pitch
        shared_params[P_RATE_YAW]   = s.rate_yaw
        shared_params[P_ATT_ERROR]  = s.att_error
        shared_params[P_AOCS_MODE]  = s.mode
        shared_params[P_RW1_SPEED]  = s.rw_speed[0]
        shared_params[P_RW2_SPEED]  = s.rw_speed[1]
        shared_params[P_RW3_SPEED]  = s.rw_speed[2]
        shared_params[P_RW4_SPEED]  = s.rw_speed[3]
        shared_params[P_RW1_TEMP]   = s.rw_temp[0]
        shared_params[P_RW2_TEMP]   = s.rw_temp[1]
        shared_params[P_RW3_TEMP]   = s.rw_temp[2]
        shared_params[P_RW4_TEMP]   = s.rw_temp[3]
        shared_params[P_MAG_X]      = s.mag_x
        shared_params[P_MAG_Y]      = s.mag_y
        shared_params[P_MAG_Z]      = s.mag_z

    # ------------------------------------------------------------------
    # Mode-specific attitude updates
    # ------------------------------------------------------------------

    def _tick_nominal(self, s: AOCSState, dt: float) -> None:
        """Nadir-pointing with reaction wheel control and small noise."""
        # Apply small attitude error correction toward target
        err = self._quat_angle_error(s.q, self._target_q)
        correction = self._kp * err
        # Perturb quaternion slightly toward target
        axis = self._quat_error_axis(s.q, self._target_q)
        delta_angle = min(correction, 0.5) * dt  # cap at 0.5 deg/s
        s.q = self._rotate_quat(s.q, axis, delta_angle * _DEG)
        s.q = self._normalise(s.q)

        # Residual noise (gyro quantisation + thermal noise)
        noise_sd = 0.002   # deg
        s.q[0] += random.gauss(0, noise_sd * 0.01)
        s.q[1] += random.gauss(0, noise_sd * 0.01)
        s.q[2] += random.gauss(0, noise_sd * 0.01)
        s.q = self._normalise(s.q)

        # Body rates: mostly zero + gyro drift + noise
        for i, attr in enumerate(('rate_roll', 'rate_pitch', 'rate_yaw')):
            bias = self._gyro_bias[i]
            noise = random.gauss(0, 0.0005)
            setattr(s, attr, bias + noise)

        # Attitude error
        s.att_error = self._quat_angle_error(s.q, self._target_q) + random.gauss(0, 0.002)

        # Star tracker validity (may be blinded by sun proximity)
        s.st_valid = True

    def _tick_detumble(self, s: AOCSState, dt: float) -> None:
        """B-dot detumbling: rates damp toward zero."""
        damp = 0.1 * dt
        s.rate_roll  *= (1.0 - damp)
        s.rate_pitch *= (1.0 - damp)
        s.rate_yaw   *= (1.0 - damp)
        for i in range(3):
            setattr(s, ('rate_roll', 'rate_pitch', 'rate_yaw')[i],
                    getattr(s, ('rate_roll', 'rate_pitch', 'rate_yaw')[i])
                    + random.gauss(0, 0.01))
        # Attitude drifts while rates non-zero
        s.att_error = math.sqrt(s.rate_roll**2 + s.rate_pitch**2 + s.rate_yaw**2) * 10.0

    def _tick_safe(self, s: AOCSState, dt: float) -> None:
        """Sun-pointing safe mode: slow precession, wide attitude error."""
        s.rate_roll  = random.gauss(0, 0.01)
        s.rate_pitch = random.gauss(0, 0.01)
        s.rate_yaw   = random.gauss(0, 0.01)
        s.att_error  = 5.0 + random.gauss(0, 0.5)
        s.st_valid   = False

    def _tick_desat(self, s: AOCSState, dt: float) -> None:
        """Wheel desaturation via magnetorquers: wheels decelerate."""
        for i, active in enumerate(s.active_wheels):
            if active:
                target = RW_DESATURATION_SPD if s.rw_speed[i] > 0 else -RW_DESATURATION_SPD
                s.rw_speed[i] += (target - s.rw_speed[i]) * 0.05 * dt
        # Small attitude disturbance during desaturation
        s.att_error = 0.5 + random.gauss(0, 0.05)
        # Transition back to nominal when all wheels are below threshold
        all_desat = all(
            abs(s.rw_speed[i]) <= RW_DESATURATION_SPD + 100
            for i in range(4) if s.active_wheels[i]
        )
        if all_desat:
            s.mode = AOCS_MODE_NOMINAL

    def _tick_slew(self, s: AOCSState, dt: float) -> None:
        """Slew manoeuvre toward target quaternion."""
        if self._slew_target_q is None:
            s.mode = AOCS_MODE_NOMINAL
            return
        err = self._quat_angle_error(s.q, self._slew_target_q)
        axis = self._quat_error_axis(s.q, self._slew_target_q)
        rate = min(0.5, err * 0.1)   # deg/s, max 0.5 deg/s
        s.q = self._rotate_quat(s.q, axis, rate * dt * _DEG)
        s.q = self._normalise(s.q)
        s.rate_roll = rate * axis[0]
        s.rate_pitch = rate * axis[1]
        s.rate_yaw = rate * axis[2]
        s.att_error = self._quat_angle_error(s.q, self._slew_target_q)
        if err < 0.05:
            s.mode = AOCS_MODE_NOMINAL
            self._slew_target_q = None

    # ------------------------------------------------------------------
    # Reaction wheel dynamics
    # ------------------------------------------------------------------

    def _tick_wheels(self, s: AOCSState, dt: float) -> None:
        for i in range(4):
            if not s.active_wheels[i]:
                # Seized wheel: coasts to zero with high friction
                s.rw_speed[i] *= (1.0 - 0.05 * dt)
                continue

            # Torque disturbance from environment → slow wheel saturation
            disturbance_torque = random.gauss(0, 0.5)   # small random torque
            omega_dot = disturbance_torque  # simplified: directly affects speed
            degrade = 1.0 + self._bearing_degradation[i] * 10.0  # bearing issue → friction
            friction = self._RW_FRICTION * degrade * abs(s.rw_speed[i])
            s.rw_speed[i] += (omega_dot - friction * math.copysign(1, s.rw_speed[i])) * dt
            s.rw_speed[i] = max(-RW_MAX_SPEED_RPM, min(RW_MAX_SPEED_RPM, s.rw_speed[i]))
            s.rw_speed[i] += random.gauss(0, 1.0)

            # Temperature: rises with bearing friction, cools toward ambient
            heat_rate = self._RW_TEMP_RISE_RATE * abs(s.rw_speed[i]) * (1.0 + 5.0 * self._bearing_degradation[i])
            cool_rate = (s.rw_temp[i] - self._RW_AMBIENT_TEMP) / self._RW_THERMAL_TAU
            s.rw_temp[i] += (heat_rate - cool_rate) * dt + random.gauss(0, 0.02)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def cmd_set_mode(self, mode: int) -> bool:
        if mode not in (AOCS_MODE_NOMINAL, AOCS_MODE_DETUMBLE,
                        AOCS_MODE_SAFE, AOCS_MODE_WHEEL_DESAT, AOCS_MODE_SLEW):
            return False
        self.state.mode = mode
        return True

    def cmd_desaturate(self) -> bool:
        self.state.mode = AOCS_MODE_WHEEL_DESAT
        return True

    def cmd_slew_to(self, q_target: List[float]) -> bool:
        if len(q_target) != 4:
            return False
        self._slew_target_q = list(q_target)
        self.state.mode = AOCS_MODE_SLEW
        return True

    def cmd_wheel_speed_set(self, wheel_idx: int, speed_rpm: float) -> bool:
        if wheel_idx not in range(4):
            return False
        self.state.rw_speed[wheel_idx] = speed_rpm
        return True

    def cmd_disable_wheel(self, wheel_idx: int) -> bool:
        if wheel_idx not in range(4):
            return False
        self.state.active_wheels[wheel_idx] = False
        return True

    def cmd_enable_wheel(self, wheel_idx: int) -> bool:
        if wheel_idx not in range(4):
            return False
        self.state.active_wheels[wheel_idx] = True
        return True

    # ------------------------------------------------------------------
    # Failure injection
    # ------------------------------------------------------------------

    def inject_gyro_bias(self, axis: int, bias_deg_s: float) -> None:
        """0=roll, 1=pitch, 2=yaw"""
        self._gyro_bias[axis] = bias_deg_s

    def inject_bearing_degradation(self, wheel_idx: int, magnitude: float) -> None:
        """0.0 = nominal, 1.0 = seizure imminent"""
        self._bearing_degradation[wheel_idx] = max(0.0, min(1.0, magnitude))
        if magnitude >= 0.95:
            self.state.active_wheels[wheel_idx] = False

    def inject_star_tracker_blind(self, blind: bool) -> None:
        """Force star tracker invalid (sun blinding)."""
        self.state.st_valid = not blind
        if blind and self.state.mode == AOCS_MODE_NOMINAL:
            # Attitude error grows at gyro drift rate
            self._gyro_bias = [random.gauss(0, 0.005) for _ in range(3)]

    # ------------------------------------------------------------------
    # Quaternion utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise(q: List[float]) -> List[float]:
        mag = math.sqrt(sum(x*x for x in q))
        return [x / (mag + 1e-9) for x in q]

    @staticmethod
    def _quat_angle_error(q: List[float], q_tgt: List[float]) -> float:
        """Angle (deg) between two quaternions."""
        dot = sum(a*b for a, b in zip(q, q_tgt))
        dot = max(-1.0, min(1.0, abs(dot)))
        return 2.0 * math.acos(dot) * _RAD

    @staticmethod
    def _quat_error_axis(q: List[float], q_tgt: List[float]) -> List[float]:
        """Approximate rotation axis from q toward q_tgt."""
        # Cross product of vector parts (simplified)
        ax = q_tgt[0] - q[0]
        ay = q_tgt[1] - q[1]
        az = q_tgt[2] - q[2]
        mag = math.sqrt(ax*ax + ay*ay + az*az) + 1e-9
        return [ax/mag, ay/mag, az/mag]

    @staticmethod
    def _rotate_quat(q: List[float], axis: List[float], angle_rad: float) -> List[float]:
        """Apply a small rotation (axis-angle) to quaternion q."""
        s_half = math.sin(angle_rad / 2)
        c_half = math.cos(angle_rad / 2)
        dq = [axis[0]*s_half, axis[1]*s_half, axis[2]*s_half, c_half]
        # Quaternion multiply: q_new = dq * q
        q1, q2, q3, q4 = q
        d1, d2, d3, d4 = dq
        return [
            d4*q1 + d3*q2 - d2*q3 + d1*q4,
           -d3*q1 + d4*q2 + d1*q3 + d2*q4,
            d2*q1 - d1*q2 + d4*q3 + d3*q4,
           -d1*q1 - d2*q2 - d3*q3 + d4*q4,
        ]

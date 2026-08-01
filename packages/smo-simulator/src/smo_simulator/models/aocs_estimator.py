"""SMO Simulator — 6-state multiplicative extended Kalman filter (MEKF).

Phase C WP-4 (brief 02 §Proposed model 2). Error state is
``[δθ (3, rotation vector), δb (3, gyro bias)]``; the reference quaternion
``q_ref`` (scalar-last) carries the full attitude. Propagation runs at the
physics step (h ≤ 1 s); star-tracker updates run at the sensor rate — a
faster-than-tick rate is folded in as R/N (N averaged fixes per tick),
which keeps the recursion fixed-step and deterministic.

Design points from the brief:
* discrete Φ = I + F·h + F²h²/2 with F = [[−[ω×], −I],[0, −I/τ_b]]
* process noise from ARW / RRW (Farrenkopf)
* Joseph-form covariance update (PSD-stable)
* error-state reset after each update
* 3σ innovation gate on the ST quaternion; rejected-fix counter
* state is 28 plain floats (q 4 + b 3 + P upper triangle 21) → JSON-safe.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from smo_common.attitude import (
    quat_conj, quat_mult, quat_normalize, skew,
)

_ARCSEC = math.pi / (180.0 * 3600.0)


class MEKF6:
    """6-state MEKF (3 attitude error + 3 gyro bias)."""

    def __init__(self,
                 arw_dps_rthz: float = 0.001,      # deg/√s  (ARW converted)
                 bias_tau_s: float = 1800.0,
                 bias_ss_dps: float = 2.8e-5,      # steady-state bias σ, deg/s
                 st_nea_cross_arcsec: float = 6.0,
                 st_nea_roll_arcsec: float = 40.0,
                 st_bias_arcsec: float = 9.0):
        self.q = np.array([0.0, 0.0, 0.0, 1.0])
        self.b = np.zeros(3)                       # gyro bias estimate [rad/s]
        self._arw = math.radians(arw_dps_rthz)     # rad/√s
        self._tau_b = bias_tau_s
        sb = math.radians(bias_ss_dps)
        self._q_b = 2.0 * sb * sb / bias_tau_s     # RRW PSD [rad²/s³]
        # ST measurement noise (per-axis 1σ, incl. LF bias + misalignment
        # inflation so the covariance stays consistent with the actual error)
        cx = st_nea_cross_arcsec * _ARCSEC
        rz = st_nea_roll_arcsec * _ARCSEC
        bi = st_bias_arcsec * _ARCSEC
        # NEA averages down with fix rate (R_nea/n); the LF-bias/misalignment
        # floor does NOT — keep them separate so a 4 Hz FINE cadence cannot
        # talk the gate into rejecting a healthy tracker.
        self._R_nea = np.diag([cx * cx, cx * cx, rz * rz])
        self._R_bias = np.eye(3) * (bi * bi)
        self._R_st = self._R_nea + self._R_bias
        self.P = self._initial_P()
        self.innov_reject_count = 0
        self._consec_rejects = 0
        self.converged = False
        self.initialised = False

    # ── lifecycle ────────────────────────────────────────────────────

    @staticmethod
    def _initial_P() -> np.ndarray:
        P = np.zeros((6, 6))
        P[:3, :3] = np.eye(3) * math.radians(10.0) ** 2
        P[3:, 3:] = np.eye(3) * math.radians(0.05) ** 2
        return P

    def reset(self, q0=None, warm: bool = False) -> None:
        """Cold (q from scratch, big P) or warm (keep q/b, re-inflate P)."""
        if q0 is not None:
            self.q = quat_normalize(np.asarray(q0, float))
        if not warm:
            self.b = np.zeros(3)
        self.P = self._initial_P()
        self.converged = False
        self.initialised = q0 is not None or warm

    def init_from_quat(self, q_meas, att_sigma_rad: float) -> None:
        self.q = quat_normalize(np.asarray(q_meas, float))
        self.P = self._initial_P()
        self.P[:3, :3] = np.eye(3) * att_sigma_rad ** 2
        self.initialised = True

    # ── propagate ────────────────────────────────────────────────────

    def propagate(self, omega_meas: np.ndarray, h: float) -> None:
        """Propagate with a bias-corrected gyro measurement over step h."""
        if h <= 0:
            return
        w = np.asarray(omega_meas, float) - self.b
        # attitude propagation (first-order quaternion integration is fine at
        # h ≤ 1 s and LEO rates; renormalised)
        wn = np.linalg.norm(w)
        if wn > 1e-12:
            half = 0.5 * wn * h
            dq = np.concatenate([w / wn * math.sin(half), [math.cos(half)]])
            self.q = quat_normalize(quat_mult(self.q, dq))
        # covariance: Φ = I + F h + F² h²/2
        F = np.zeros((6, 6))
        F[:3, :3] = -skew(w)
        F[:3, 3:] = -np.eye(3)
        F[3:, 3:] = -np.eye(3) / self._tau_b
        Phi = np.eye(6) + F * h + (F @ F) * (h * h / 2.0)
        Q = np.zeros((6, 6))
        Q[:3, :3] = np.eye(3) * (self._arw ** 2 * h)
        Q[3:, 3:] = np.eye(3) * (self._q_b * h)
        self.P = Phi @ self.P @ Phi.T + Q
        self.P = 0.5 * (self.P + self.P.T)

    # ── star-tracker quaternion update ───────────────────────────────

    def update_st(self, q_meas, n_avg: int = 1) -> bool:
        """Update with an ST quaternion; n_avg folds a faster sensor rate in
        as R/n_avg. Returns True if the fix was accepted."""
        q_meas = quat_normalize(np.asarray(q_meas, float))
        if not self.initialised:
            self.init_from_quat(q_meas, math.sqrt(float(self._R_st[0, 0])))
            return True
        dq = quat_mult(quat_conj(self.q), q_meas)
        if dq[3] < 0:
            dq = -dq
        innov = 2.0 * dq[:3]                        # rotation-vector residual
        R = self._R_nea / max(1, int(n_avg)) + self._R_bias
        S = self.P[:3, :3] + R
        # 3σ innovation gate (Mahalanobis on the 3-vector)
        try:
            md2 = float(innov @ np.linalg.solve(S, innov))
        except np.linalg.LinAlgError:
            md2 = float('inf')
        if md2 > 3.0 ** 2 * 3.0:                    # ~3σ per axis, 3 dof
            self.innov_reject_count += 1
            self._consec_rejects += 1
            # Watchdog: a healthy tracker persistently outside the gate means
            # the FILTER is wrong (divergence trap) — reinitialise from the
            # measurement rather than rejecting good fixes forever.
            if self._consec_rejects >= 10:
                self.init_from_quat(q_meas,
                                    math.sqrt(float(self._R_st[0, 0])) * 3.0)
                self._consec_rejects = 0
            return False
        self._consec_rejects = 0
        H = np.zeros((3, 6))
        H[:, :3] = np.eye(3)
        K = self.P @ H.T @ np.linalg.inv(S)
        dx = K @ innov
        # Joseph form
        IKH = np.eye(6) - K @ H
        self.P = IKH @ self.P @ IKH.T + K @ R @ K.T
        self.P = 0.5 * (self.P + self.P.T)
        self._apply_correction(dx)
        return True

    def update_vector(self, v_body_meas, v_ref_eci, sigma_rad: float) -> bool:
        """Vector measurement update (CSS sun vector / magnetometer),
        used on the coarse path. v_ref_eci is the reference unit vector in
        ECI; the predicted body vector comes from the current estimate."""
        from smo_common.attitude import quat_to_dcm
        v_meas = np.asarray(v_body_meas, float)
        nm = np.linalg.norm(v_meas)
        if nm < 1e-9 or not self.initialised:
            return False
        v_meas = v_meas / nm
        v_ref = np.asarray(v_ref_eci, float)
        v_ref = v_ref / (np.linalg.norm(v_ref) + 1e-12)
        v_pred = quat_to_dcm(self.q) @ v_ref
        innov = np.cross(v_pred, v_meas)            # small-angle residual
        H = np.zeros((3, 6))
        H[:, :3] = skew(v_pred)
        R = np.eye(3) * sigma_rad ** 2
        S = H @ self.P @ H.T + R
        try:
            md2 = float(innov @ np.linalg.solve(S, innov))
        except np.linalg.LinAlgError:
            return False
        if md2 > 3.0 ** 2 * 3.0:
            self.innov_reject_count += 1
            return False
        K = self.P @ H.T @ np.linalg.inv(S)
        dx = K @ innov
        IKH = np.eye(6) - K @ H
        self.P = IKH @ self.P @ IKH.T + K @ R @ K.T
        self.P = 0.5 * (self.P + self.P.T)
        self._apply_correction(dx)
        return True

    def _apply_correction(self, dx: np.ndarray) -> None:
        dth = dx[:3]
        ang = np.linalg.norm(dth)
        if ang > 1e-15:
            axis = dth / ang
            dq = np.concatenate([axis * math.sin(ang / 2.0),
                                 [math.cos(ang / 2.0)]])
            self.q = quat_normalize(quat_mult(self.q, dq))
        self.b = self.b + dx[3:]
        # error-state reset is implicit (δθ folded into q_ref)

    # ── outputs ──────────────────────────────────────────────────────

    def knowledge_3sigma_deg(self) -> float:
        tr = max(0.0, float(np.trace(self.P[:3, :3])))
        return 3.0 * math.degrees(math.sqrt(tr))

    def set_covariance_steady_state(self, knowledge_3sig_deg: float) -> None:
        """Secular-mode shortcut: collapse P to a stated steady state."""
        s = math.radians(max(1e-6, knowledge_3sig_deg) / 3.0)
        self.P[:3, :3] = np.eye(3) * (s * s / 3.0)
        self.P[3:, 3:] = np.minimum(self.P[3:, 3:],
                                    np.eye(3) * math.radians(0.01) ** 2)

    # ── breakpoint round-trip (28 floats + counters) ─────────────────

    _IU = np.triu_indices(6)

    def get_state(self) -> dict[str, Any]:
        return {
            "q": [float(x) for x in self.q],
            "b": [float(x) for x in self.b],
            "P_upper": [float(x) for x in self.P[self._IU]],
            "innov_reject_count": int(self.innov_reject_count),
            "converged": bool(self.converged),
            "initialised": bool(self.initialised),
        }

    def set_state(self, st: dict[str, Any]) -> None:
        self.q = np.asarray(st.get("q", [0, 0, 0, 1]), float)
        self.b = np.asarray(st.get("b", [0, 0, 0]), float)
        pu = st.get("P_upper")
        if pu is not None and len(pu) == 21:
            P = np.zeros((6, 6))
            P[self._IU] = pu
            self.P = P + np.triu(P, 1).T
        self.innov_reject_count = int(st.get("innov_reject_count", 0))
        self.converged = bool(st.get("converged", False))
        self.initialised = bool(st.get("initialised", False))

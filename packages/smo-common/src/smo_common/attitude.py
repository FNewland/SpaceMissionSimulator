"""SMO Common — attitude/frame math for the AOCS rebuild (Phase C).

Pure functions ported from ``ADCS_failure_sim/cubesat_adcs_sim.py`` (the
validated numpy reference implementation) so the main simulator and the
standalone viewer share one set of quaternion / frame / environment
primitives without importing the viewer's module-global state.

Conventions (identical to the reference):
* Quaternions are scalar-last ``[x, y, z, w]``.
* ``quat_to_dcm(q)`` returns the DCM mapping ECI -> body: ``v_b = C @ v_eci``.
* LVLH: z = nadir, y = -orbit normal, x completes (≈ velocity direction).

Everything is deterministic and side-effect free.
"""
from __future__ import annotations

import math

import numpy as np

MU_EARTH = 3.986004418e14     # m^3/s^2
R_EARTH = 6378137.0           # m
B0_SURF = 3.12e-5             # Earth surface equatorial dipole field [T]

_DEG = math.pi / 180.0


# ── Quaternion utilities (port of cubesat_adcs_sim.py:223-278) ──────────

def quat_normalize(q):
    q = np.asarray(q, dtype=float)
    n = np.linalg.norm(q)
    return q / n if n > 0 else np.array([0.0, 0.0, 0.0, 1.0])


def quat_mult(a, b):
    """Hamilton product a ⊗ b, scalar-last convention."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ])


def quat_conj(q):
    return np.array([-q[0], -q[1], -q[2], q[3]])


def quat_to_dcm(q):
    """DCM mapping ECI -> body: v_body = C @ v_eci."""
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)],
        [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)],
        [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)],
    ])


def dcm_to_quat(C):
    """Quaternion (scalar-last) from a body<-ECI DCM."""
    C = np.asarray(C, dtype=float)
    tr = np.trace(C)
    if tr > 0:
        S = math.sqrt(tr + 1.0) * 2
        w = 0.25 * S
        x = (C[1, 2] - C[2, 1]) / S
        y = (C[2, 0] - C[0, 2]) / S
        z = (C[0, 1] - C[1, 0]) / S
    elif C[0, 0] > C[1, 1] and C[0, 0] > C[2, 2]:
        S = math.sqrt(1.0 + C[0, 0] - C[1, 1] - C[2, 2]) * 2
        w = (C[1, 2] - C[2, 1]) / S
        x = 0.25 * S
        y = (C[1, 0] + C[0, 1]) / S
        z = (C[2, 0] + C[0, 2]) / S
    elif C[1, 1] > C[2, 2]:
        S = math.sqrt(1.0 + C[1, 1] - C[0, 0] - C[2, 2]) * 2
        w = (C[2, 0] - C[0, 2]) / S
        x = (C[1, 0] + C[0, 1]) / S
        y = 0.25 * S
        z = (C[2, 1] + C[1, 2]) / S
    else:
        S = math.sqrt(1.0 + C[2, 2] - C[0, 0] - C[1, 1]) * 2
        w = (C[0, 1] - C[1, 0]) / S
        x = (C[2, 0] + C[0, 2]) / S
        y = (C[2, 1] + C[1, 2]) / S
        z = 0.25 * S
    return quat_normalize(np.array([x, y, z, w]))


def skew(v):
    return np.array([[0.0, -v[2], v[1]],
                     [v[2], 0.0, -v[0]],
                     [-v[1], v[0], 0.0]])


def quat_angle_deg(qa, qb) -> float:
    """Rotation angle between two attitudes, degrees (frame-correct)."""
    qe = quat_mult(quat_conj(np.asarray(qa, float)), np.asarray(qb, float))
    return 2.0 * math.degrees(math.acos(min(1.0, abs(float(qe[3])))))


def quat_error_vec(q_est, q_ref):
    """Small-angle body-frame error rotation vector [rad] taking q_est to
    q_ref (vector part of q_err = q_est⁻¹ ⊗ q_ref, shortest path, ×2)."""
    qe = quat_mult(quat_conj(np.asarray(q_est, float)), np.asarray(q_ref, float))
    if qe[3] < 0:
        qe = -qe
    return 2.0 * qe[:3]


def quat_slerp(q0, q1, t):
    """SLERP between scalar-last quaternions (shortest path)."""
    q0 = quat_normalize(q0)
    q1 = quat_normalize(q1)
    dot = float(np.dot(q0, q1))
    if dot < 0.0:
        q1 = -q1
        dot = -dot
    dot = min(1.0, dot)
    if dot > 0.9995:
        return quat_normalize(q0 + t * (q1 - q0))
    theta = math.acos(dot)
    st = math.sin(theta)
    return quat_normalize(
        (math.sin((1.0 - t) * theta) / st) * q0
        + (math.sin(t * theta) / st) * q1)


# ── Reference frames / environment (port of cubesat_adcs_sim.py:304-397) ─

def lvlh_dcm(r_eci, v_eci):
    """DCM mapping ECI -> LVLH (nadir-pointing target frame).

    z_LVLH = nadir (-r_hat), y_LVLH = -orbit normal, x completes (~velocity).
    """
    r_eci = np.asarray(r_eci, float)
    v_eci = np.asarray(v_eci, float)
    z = -r_eci / np.linalg.norm(r_eci)
    h = np.cross(r_eci, v_eci)
    y = -h / np.linalg.norm(h)
    x = np.cross(y, z)
    x /= np.linalg.norm(x)
    return np.vstack([x, y, z])


def sun_frame_dcm(sun_hat_eci, r_eci):
    """DCM mapping ECI -> a sun-pointing frame.

    +X_body -> sun (maximises SADA wing illumination for a drive about the
    body Y axis), Y_body chosen along sun × nadir so the wings' drive axis
    stays near the orbit normal, Z completes the right-handed triad.
    """
    s = np.asarray(sun_hat_eci, float)
    s = s / (np.linalg.norm(s) + 1e-12)
    nadir = -np.asarray(r_eci, float)
    nmag = np.linalg.norm(nadir)
    nadir = nadir / nmag if nmag > 0 else np.array([0.0, 0.0, -1.0])
    y = np.cross(s, nadir)
    ymag = np.linalg.norm(y)
    if ymag < 1e-6:          # sun ∥ nadir — pick any perpendicular
        y = np.cross(s, np.array([0.0, 0.0, 1.0]))
        ymag = np.linalg.norm(y)
        if ymag < 1e-6:
            y = np.array([0.0, 1.0, 0.0])
            ymag = 1.0
    y = y / ymag
    z = np.cross(s, y)
    return np.vstack([s, y, z])


def igrf_dipole_eci(r_eci, gmst):
    """Tilted-dipole geomagnetic field in ECI [Tesla] (simplified IGRF)."""
    colat = math.radians(9.41)
    elong = math.radians(-72.7)
    m_ecef = np.array([math.sin(colat) * math.cos(elong),
                       math.sin(colat) * math.sin(elong),
                       math.cos(colat)])
    cg, sg = math.cos(gmst), math.sin(gmst)
    Rz = np.array([[cg, -sg, 0.0], [sg, cg, 0.0], [0.0, 0.0, 1.0]])
    m_hat = Rz @ m_ecef
    r_eci = np.asarray(r_eci, float)
    r = np.linalg.norm(r_eci)
    r_hat = r_eci / r
    coef = B0_SURF * (R_EARTH / r) ** 3
    return coef * (3.0 * float(np.dot(m_hat, r_hat)) * r_hat - m_hat)


def gmst_from_position(r_eci, lon_deg) -> float:
    """GMST [rad] recovered from an ECI position and its geodetic longitude.

    alpha = atan2(y, x) is the right ascension of the sub-satellite point;
    GMST = alpha - east longitude. Avoids threading absolute time through
    every model that needs the ECEF <-> ECI rotation.
    """
    alpha = math.atan2(float(r_eci[1]), float(r_eci[0]))
    return (alpha - lon_deg * _DEG) % (2.0 * math.pi)


def triad(v1_body, v2_body, v1_ref, v2_ref):
    """TRIAD attitude DCM (ECI->body) from two vector pairs (port :406-419)."""
    t1b = v1_body / np.linalg.norm(v1_body)
    t2b = np.cross(v1_body, v2_body)
    t2b /= np.linalg.norm(t2b)
    t3b = np.cross(t1b, t2b)
    t1r = v1_ref / np.linalg.norm(v1_ref)
    t2r = np.cross(v1_ref, v2_ref)
    t2r /= np.linalg.norm(t2r)
    t3r = np.cross(t1r, t2r)
    Mb = np.vstack([t1b, t2b, t3b]).T
    Mr = np.vstack([t1r, t2r, t3r]).T
    return Mb @ Mr.T

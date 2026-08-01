#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 CubeSat Nadir-Pointing ADCS Telemetry Simulator
================================================================================
Simulates the attitude determination & control system (ADCS) telemetry of a
3U CubeSat in a Sun-synchronous low-Earth orbit, reporting realistic values of:

    * SENSOR INPUTS      - 3-axis magnetometer, coarse Sun sensor (+ eclipse flag)
    * ALGORITHM STATES   - estimated attitude quaternion, estimated body rates,
                           attitude error, control mode, reaction-wheel momentum
    * ACTUATOR OUTPUTS   - reaction-wheel torque & speed, magnetorquer dipole

The run is divided into three phases driven by user-set event times:

    1. NORMAL      - nadir-pointing PD control on the reaction wheels, with
                     magnetorquer momentum dumping. Telemetry looks "healthy".
    2. IMPACT      - a debris strike imparts an angular-momentum kick. The ADCS
                     trips to SAFE (control off) and the spacecraft enters a
                     slow tumble/roll. A latent control-gain corruption is set
                     on ONE axis (does not manifest while control is off).
    3. RECOVERY    - at a user-set time the ground re-enables control. The
                     corrupted axis now commands ~10x the intended torque, the
                     reaction wheel saturates almost immediately, and the ADCS
                     auto-trips back to SAFE. The slow roll resumes.

Reference theory: Markley & Crassidis, "Fundamentals of Spacecraft Attitude
Determination and Control"; Wertz, "Spacecraft Attitude Determination and
Control". Quaternion convention: q = [qx, qy, qz, qw], scalar-last, body<-ECI.

Outputs (written next to this script):
    cubesat_adcs_telemetry.csv   - full time series of every channel
    plot_1_sensors.png           - magnetometer + Sun-sensor telemetry
    plot_2_estimator.png         - attitude error, body rates, control mode
    plot_3_actuators.png         - wheel torque/speed, magnetorquer dipole

Dependencies: numpy, matplotlib (pandas optional). No internet required.
================================================================================
"""

import numpy as np
import csv
import os
from datetime import datetime, timezone, timedelta

# matplotlib is only needed for the PNG plot output. The physics (``run``) works
# without it, so importing this module stays possible on a plotting-free install
# (e.g. a headless service that only wants the telemetry log).
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_MATPLOTLIB = True
except Exception:                      # pragma: no cover - environment dependent
    plt = None
    HAVE_MATPLOTLIB = False

# =============================================================================
#  USER CONFIGURATION BLOCK  --  edit these values to define your scenario
# =============================================================================

# --- Epoch (UTC). The Sun direction & geomagnetic field depend on this. ------
EPOCH_UTC = datetime(2026, 6, 23, 10, 30, 0, tzinfo=timezone.utc)

# --- Orbit (classical Keplerian elements at EPOCH). Default = Sun-sync 500 km -
ORBIT = dict(
    altitude_km   = 550.0,      # mean altitude above Earth surface
    ecc           = 0.011,     # eccentricity (near-circular)
    inc_deg       = 97.4,       # inclination (97.4 deg ~ Sun-synchronous @500 km)
    raan_deg      = 95.0,       # right ascension of ascending node
    argp_deg      = 0.0,        # argument of perigee
    true_anom_deg = 0.0,        # true anomaly at epoch
)

# --- Spacecraft mass properties (3U CubeSat, principal axes, kg*m^2) ----------
INERTIA = np.diag([0.0419, 0.0419, 0.0067])   # Ix, Iy, Iz

# --- Simulation timing --------------------------------------------------------
DT          = 1.0       # integration / telemetry step [s]
T_TOTAL     = 16000.0   # total simulated duration [s]  (~3 hrs, ~1.9 orbits)

# --- Event times (seconds from EPOCH) ----------------------------------------
T_IMPACT    = 3000.0    # debris impact -> loss of attitude control + slow roll
T_RECOVERY  = 5180.0    # ground attempts to re-enable attitude control

# --- Debris impact model ------------------------------------------------------
# Instantaneous body angular-rate change [rad/s] imparted by the strike.
IMPACT_DELTA_OMEGA = np.array([0.018, -0.011, 0.025])   # ~1-1.5 deg/s per axis
CORRUPTED_AXIS     = 0        # 0=X, 1=Y, 2=Z : axis whose control gain is corrupted
CORRUPTION_FACTOR  = 10.0     # corrupted axis commands this multiple of torque

# --- Reaction wheels ----------------------------------------------------------
RW_MAX_TORQUE  = 1.0e-3       # max wheel torque per axis [N*m]
RW_MAX_MOMENTUM= 3.0e-2       # wheel angular-momentum saturation [N*m*s] (30 mNms)
RW_INERTIA     = 5.0e-6       # wheel rotor inertia [kg*m^2] (for RPM telemetry)

# --- Magnetorquers ------------------------------------------------------------
MTQ_MAX_DIPOLE = 0.2          # max magnetic dipole per axis [A*m^2]
MTQ_DUMP_GAIN  = 4.0e-3       # momentum-dump cross-product law gain [1/s]

# --- PD attitude controller (nominal gains) -----------------------------------
KP = np.array([0.012, 0.012, 0.004])    # proportional gain (on quaternion vector)
KD = np.array([0.045, 0.045, 0.015])    # derivative gain (on body rate)

# --- Safing thresholds (auto-trip to SAFE mode) -------------------------------
SAFE_RATE_LIMIT = np.deg2rad(8.0)       # body rate that trips ADCS off [rad/s]
# (wheel momentum saturation also trips SAFE during the corrupted recovery)

# --- Sensor noise / bias (1-sigma) --------------------------------------------
MAG_NOISE_T     = 250e-9      # magnetometer white noise [T] (250 nT)
MAG_BIAS_T      = np.array([400e-9, -300e-9, 200e-9])   # fixed bias [T]
SUN_NOISE_DEG   = 0.5         # coarse Sun-sensor angular noise [deg]
EST_ATT_NOISE_DEG = 0.4       # residual attitude-determination noise [deg]

RANDOM_SEED = 42              # reproducibility

# =============================================================================
#  SCENARIO PARAMETERS  --  programmatic form of the block above
# =============================================================================
# ``run()`` accepts an optional dict of overrides so callers (e.g.
# tools/adcs_sim_viewer.py) can drive the scenario without editing this file.
# Every default below is exactly the constant defined above, so the CLI entry
# point and anything that calls ``run()`` with no arguments is unchanged.

PARAM_LIMITS = {
    #  name                (min,    max)
    "alt_km":              (150.0,  3000.0),
    "ecc":                 (0.0,    0.9),
    "inc_deg":             (0.0,    180.0),
    "raan_deg":            (0.0,    360.0),
    "argp_deg":            (0.0,    360.0),
    "nu_deg":              (0.0,    360.0),
    "dt":                  (0.05,   10.0),
    "t_total":             (10.0,   86400.0),
    "t_impact":            (0.0,    86400.0),
    "t_recovery":          (0.0,    86400.0),
    "corruption_factor":   (1.0,    100.0),
    "dw_x":                (-2.0,   2.0),
    "dw_y":                (-2.0,   2.0),
    "dw_z":                (-2.0,   2.0),
}


def default_params():
    """Return the scenario parameters implied by the USER CONFIGURATION BLOCK."""
    return dict(
        epoch=EPOCH_UTC,
        alt_km=float(ORBIT["altitude_km"]),
        ecc=float(ORBIT["ecc"]),
        inc_deg=float(ORBIT["inc_deg"]),
        raan_deg=float(ORBIT["raan_deg"]),
        argp_deg=float(ORBIT["argp_deg"]),
        nu_deg=float(ORBIT["true_anom_deg"]),
        dt=float(DT),
        t_total=float(T_TOTAL),
        t_impact=float(T_IMPACT),
        t_recovery=float(T_RECOVERY),
        dw_x=float(IMPACT_DELTA_OMEGA[0]),
        dw_y=float(IMPACT_DELTA_OMEGA[1]),
        dw_z=float(IMPACT_DELTA_OMEGA[2]),
        corrupted_axis=int(CORRUPTED_AXIS),
        corruption_factor=float(CORRUPTION_FACTOR),
        seed=int(RANDOM_SEED),
        verbose=True,
    )


def _parse_epoch(value):
    """Accept a datetime, or an ISO-8601 string (incl. the HTML datetime-local
    form ``2026-06-23T10:30``). Always returns a UTC-aware datetime."""
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_params(overrides=None):
    """Merge ``overrides`` onto the defaults, coerce types and clamp to
    PARAM_LIMITS. Unknown keys raise ValueError so typos are not silently lost."""
    cfg = default_params()
    if overrides:
        unknown = set(overrides) - set(cfg)
        if unknown:
            raise ValueError(f"unknown ADCS scenario parameter(s): {sorted(unknown)}")
        for k, v in overrides.items():
            if v is None or v == "":
                continue
            cfg[k] = v

    cfg["epoch"] = _parse_epoch(cfg["epoch"])
    cfg["corrupted_axis"] = int(cfg["corrupted_axis"])
    if cfg["corrupted_axis"] not in (0, 1, 2):
        raise ValueError("corrupted_axis must be 0 (X), 1 (Y) or 2 (Z)")
    cfg["seed"] = int(cfg["seed"])
    cfg["verbose"] = bool(cfg["verbose"])
    for k, (lo, hi) in PARAM_LIMITS.items():
        cfg[k] = float(np.clip(float(cfg[k]), lo, hi))
    # events must lie inside the run, and recovery must follow the impact
    cfg["t_impact"] = min(cfg["t_impact"], cfg["t_total"])
    cfg["t_recovery"] = min(max(cfg["t_recovery"], cfg["t_impact"]), cfg["t_total"])
    return cfg


# =============================================================================
#  PHYSICAL CONSTANTS
# =============================================================================
MU_EARTH = 3.986004418e14     # m^3/s^2
R_EARTH  = 6378137.0          # m (WGS84 equatorial)
J2       = 1.08263e-3
B0_SURF  = 3.12e-5            # Earth surface equatorial dipole field [T] (~31200 nT)
DEG = np.pi / 180.0

rng = np.random.default_rng(RANDOM_SEED)

# =============================================================================
#  QUATERNION & ROTATION UTILITIES  (scalar-last: q=[x,y,z,w], C maps ECI->body)
# =============================================================================
def quat_normalize(q):
    n = np.linalg.norm(q)
    return q / n if n > 0 else np.array([0, 0, 0, 1.0])

def quat_mult(a, b):
    """Hamilton product a (x) b, scalar-last convention."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([
        aw*bx + ax*bw + ay*bz - az*by,
        aw*by - ax*bz + ay*bw + az*bx,
        aw*bz + ax*by - ay*bx + az*bw,
        aw*bw - ax*bx - ay*by - az*bz,
    ])

def quat_conj(q):
    return np.array([-q[0], -q[1], -q[2], q[3]])

def quat_to_dcm(q):
    """DCM that maps a vector from ECI to body frame: v_body = C @ v_eci."""
    x, y, z, w = q
    return np.array([
        [1-2*(y*y+z*z),   2*(x*y+z*w),   2*(x*z-y*w)],
        [2*(x*y-z*w),   1-2*(x*x+z*z),   2*(y*z+x*w)],
        [2*(x*z+y*w),     2*(y*z-x*w), 1-2*(x*x+y*y)],
    ])

def dcm_to_quat(C):
    """Inverse: build quaternion (scalar-last) from a body<-ECI DCM."""
    tr = np.trace(C)
    if tr > 0:
        S = np.sqrt(tr + 1.0) * 2
        w = 0.25 * S
        x = (C[1, 2] - C[2, 1]) / S
        y = (C[2, 0] - C[0, 2]) / S
        z = (C[0, 1] - C[1, 0]) / S
    elif C[0, 0] > C[1, 1] and C[0, 0] > C[2, 2]:
        S = np.sqrt(1.0 + C[0, 0] - C[1, 1] - C[2, 2]) * 2
        w = (C[1, 2] - C[2, 1]) / S
        x = 0.25 * S
        y = (C[1, 0] + C[0, 1]) / S
        z = (C[2, 0] + C[0, 2]) / S
    elif C[1, 1] > C[2, 2]:
        S = np.sqrt(1.0 + C[1, 1] - C[0, 0] - C[2, 2]) * 2
        w = (C[2, 0] - C[0, 2]) / S
        x = (C[1, 0] + C[0, 1]) / S
        y = 0.25 * S
        z = (C[2, 1] + C[1, 2]) / S
    else:
        S = np.sqrt(1.0 + C[2, 2] - C[0, 0] - C[1, 1]) * 2
        w = (C[0, 1] - C[1, 0]) / S
        x = (C[2, 0] + C[0, 2]) / S
        y = (C[2, 1] + C[1, 2]) / S
        z = 0.25 * S
    return quat_normalize(np.array([x, y, z, w]))

def skew(v):
    return np.array([[0, -v[2], v[1]],
                     [v[2], 0, -v[0]],
                     [-v[1], v[0], 0]])

# =============================================================================
#  TIME / EPHEMERIS  (low-precision analytic models, adequate for telemetry)
# =============================================================================
def julian_date(dt):
    dt = dt.astimezone(timezone.utc)
    y, m = dt.year, dt.month
    d = (dt.day + (dt.hour + (dt.minute + dt.second/60)/60)/24)
    if m <= 2:
        y -= 1; m += 12
    A = y // 100
    B = 2 - A + A // 4
    return int(365.25*(y+4716)) + int(30.6001*(m+1)) + d + B - 1524.5

def gmst_rad(jd):
    """Greenwich mean sidereal time [rad]."""
    T = (jd - 2451545.0) / 36525.0
    gmst = 280.46061837 + 360.98564736629*(jd-2451545.0) \
           + 0.000387933*T*T - T*T*T/38710000.0
    return np.deg2rad(gmst % 360.0)

def sun_vector_eci(jd):
    """Unit Sun direction in ECI (low-precision, ~0.01 deg). Vallado algo."""
    T = (jd - 2451545.0) / 36525.0
    L = (280.460 + 36000.771*T) % 360.0
    M = np.deg2rad((357.5291092 + 35999.05034*T) % 360.0)
    lam = np.deg2rad(L + 1.914666471*np.sin(M) + 0.019994643*np.sin(2*M))
    eps = np.deg2rad(23.439291 - 0.0130042*T)
    return np.array([np.cos(lam),
                     np.cos(eps)*np.sin(lam),
                     np.sin(eps)*np.sin(lam)])

def igrf_dipole_eci(r_eci, gmst):
    """Tilted-dipole geomagnetic field in ECI [Tesla].
    Simplified IGRF: centred dipole tilted ~9.6 deg, realistic LEO magnitude."""
    # Dipole axis in ECEF (toward geomagnetic south pole), 2020-ish orientation
    colat = np.deg2rad(9.41)      # tilt from spin axis
    elong = np.deg2rad(-72.7)     # east longitude of dipole
    m_ecef = np.array([np.sin(colat)*np.cos(elong),
                       np.sin(colat)*np.sin(elong),
                       np.cos(colat)])
    # ECEF -> ECI rotation about z by GMST
    Rz = np.array([[np.cos(gmst), -np.sin(gmst), 0],
                   [np.sin(gmst),  np.cos(gmst), 0],
                   [0, 0, 1.0]])
    m_hat = Rz @ m_ecef
    r = np.linalg.norm(r_eci)
    r_hat = r_eci / r
    # Dipole field: B = B0*(R_E/r)^3 * (3(m_hat.r_hat) r_hat - m_hat)  [Tesla]
    # |B| ranges ~ B0(R/r)^3 (mag equator) to 2x that (mag pole): ~25-50 uT @500 km
    coef = B0_SURF * (R_EARTH / r)**3
    B = coef * (3.0*np.dot(m_hat, r_hat)*r_hat - m_hat)
    return B

# =============================================================================
#  ORBIT
# =============================================================================
def kepler_to_rv(a, e, inc, raan, argp, nu):
    """Classical elements -> ECI position/velocity (angles in rad)."""
    p = a*(1-e*e)
    r = p/(1+e*np.cos(nu))
    r_pqw = np.array([r*np.cos(nu), r*np.sin(nu), 0.0])
    v_pqw = np.sqrt(MU_EARTH/p)*np.array([-np.sin(nu), e+np.cos(nu), 0.0])
    cO, sO = np.cos(raan), np.sin(raan)
    ci, si = np.cos(inc),  np.sin(inc)
    cw, sw = np.cos(argp), np.sin(argp)
    R = np.array([
        [cO*cw - sO*sw*ci, -cO*sw - sO*cw*ci,  sO*si],
        [sO*cw + cO*sw*ci, -sO*sw + cO*cw*ci, -cO*si],
        [sw*si,             cw*si,             ci]])
    return R @ r_pqw, R @ v_pqw

class Orbit:
    """Keplerian propagation with J2 secular RAAN/argp drift (good for SSO)."""
    def __init__(self, cfg):
        self.a = R_EARTH + cfg["altitude_km"]*1000.0
        self.e = cfg["ecc"]
        self.inc = cfg["inc_deg"]*DEG
        self.raan0 = cfg["raan_deg"]*DEG
        self.argp0 = cfg["argp_deg"]*DEG
        self.nu0 = cfg["true_anom_deg"]*DEG
        self.n = np.sqrt(MU_EARTH/self.a**3)         # mean motion
        self.period = 2*np.pi/self.n
        p = self.a*(1-self.e**2)
        f = -1.5*J2*(R_EARTH/p)**2*self.n
        self.raan_dot = f*np.cos(self.inc)
        self.argp_dot = f*(2.5*np.sin(self.inc)**2 - 2.0)
        E0 = 2*np.arctan2(np.sqrt(1-self.e)*np.sin(self.nu0/2),
                          np.sqrt(1+self.e)*np.cos(self.nu0/2))
        self.M0 = E0 - self.e*np.sin(E0)

    def state(self, t):
        M = self.M0 + self.n*t
        E = M
        for _ in range(8):                            # Newton solve Kepler
            E = E - (E - self.e*np.sin(E) - M)/(1 - self.e*np.cos(E))
        nu = 2*np.arctan2(np.sqrt(1+self.e)*np.sin(E/2),
                          np.sqrt(1-self.e)*np.cos(E/2))
        raan = self.raan0 + self.raan_dot*t
        argp = self.argp0 + self.argp_dot*t
        return kepler_to_rv(self.a, self.e, self.inc, raan, argp, nu)

def lvlh_dcm(r_eci, v_eci):
    """DCM mapping ECI -> LVLH (nadir-pointing target frame).
    z_LVLH = nadir (-r_hat), y_LVLH = -orbit normal, x_LVLH completes (~velocity)."""
    z = -r_eci/np.linalg.norm(r_eci)
    h = np.cross(r_eci, v_eci)
    y = -h/np.linalg.norm(h)
    x = np.cross(y, z)
    x /= np.linalg.norm(x)
    return np.vstack([x, y, z])     # rows are LVLH axes in ECI -> maps ECI->LVLH

def in_eclipse(r_eci, sun_hat):
    """Cylindrical shadow test."""
    proj = np.dot(r_eci, sun_hat)
    if proj > 0:
        return False                              # sunward side
    perp = np.linalg.norm(r_eci - proj*sun_hat)
    return perp < R_EARTH

# =============================================================================
#  ATTITUDE DETERMINATION  -- TRIAD (Sun + mag) with eclipse dead-reckoning
# =============================================================================
def triad(b_body, s_body, b_ref, s_ref):
    """TRIAD attitude: returns DCM (ECI->body) from two vector pairs."""
    t1b = b_body/np.linalg.norm(b_body)
    t2b = np.cross(b_body, s_body); t2b /= np.linalg.norm(t2b)
    t3b = np.cross(t1b, t2b)
    t1r = b_ref/np.linalg.norm(b_ref)
    t2r = np.cross(b_ref, s_ref); t2r /= np.linalg.norm(t2r)
    t3r = np.cross(t1r, t2r)
    Mb = np.vstack([t1b, t2b, t3b]).T
    Mr = np.vstack([t1r, t2r, t3r]).T
    return Mb @ Mr.T

# =============================================================================
#  MAIN SIMULATION
# =============================================================================
def run(params=None, events=None):
    """Run the scenario.

    params : dict | None
        Overrides for the USER CONFIGURATION BLOCK (see ``default_params()``).
        ``None`` reproduces the module constants exactly — this is what the CLI
        entry point below uses, so its behaviour is unchanged.
    events : list | None
        If given, the three storyline events (debris impact, ground recovery,
        automatic safe-mode re-trip) are appended to it as
        ``{'t': float, 'type': 'impact'|'recovery'|'autosafe', 'msg': str}``
        dicts, in addition to being printed.

    Returns ``(log, orbit)`` — unchanged from the original signature.
    """
    cfg = resolve_params(params)
    EPOCH_UTC = cfg["epoch"]
    DT = cfg["dt"]
    T_TOTAL = cfg["t_total"]
    T_IMPACT = cfg["t_impact"]
    T_RECOVERY = cfg["t_recovery"]
    CORRUPTED_AXIS = cfg["corrupted_axis"]
    CORRUPTION_FACTOR = cfg["corruption_factor"]
    IMPACT_DELTA_OMEGA = np.array([cfg["dw_x"], cfg["dw_y"], cfg["dw_z"]])
    verbose = cfg["verbose"]
    rng = np.random.default_rng(cfg["seed"])

    def _emit(t, kind, msg):
        if events is not None:
            events.append({"t": float(t), "type": kind, "msg": msg})
        if verbose:
            print(msg)

    jd0 = julian_date(EPOCH_UTC)
    orbit = Orbit(dict(altitude_km=cfg["alt_km"], ecc=cfg["ecc"],
                       inc_deg=cfg["inc_deg"], raan_deg=cfg["raan_deg"],
                       argp_deg=cfg["argp_deg"], true_anom_deg=cfg["nu_deg"]))
    if verbose:
        print(f"Orbital period      : {orbit.period:8.1f} s ({orbit.period/60:.1f} min)")
        print(f"RAAN drift (J2)     : {np.rad2deg(orbit.raan_dot)*86400:+.3f} deg/day "
              f"(SSO target ~ +0.986)")

    n_steps = int(T_TOTAL/DT) + 1
    I = INERTIA
    Iinv = np.linalg.inv(I)

    # --- initial true attitude = perfectly nadir-pointing, body rate = LVLH rate
    r0, v0 = orbit.state(0.0)
    C_li = lvlh_dcm(r0, v0)
    q_true = dcm_to_quat(C_li)
    # orbital angular velocity expressed in body (nadir track requires this rate)
    h0 = np.cross(r0, v0)
    w_orb_eci = h0/np.dot(r0, r0)                 # orbit rate vector in ECI
    omega = C_li @ w_orb_eci                       # body rate to hold nadir lock

    # --- estimator / actuator initial states
    q_est = q_true.copy()
    omega_est = omega.copy()
    h_wheel = np.zeros(3)                          # reaction-wheel momentum [Nms]
    mode = "NORMAL"
    gain_corrupt = np.ones(3)                      # multiplies KP/KD per axis

    log = []

    for k in range(n_steps):
        t = k*DT
        jd = jd0 + t/86400.0
        gmst = gmst_rad(jd)

        # ---- environment ----------------------------------------------------
        r_eci, v_eci = orbit.state(t)
        sun_hat = sun_vector_eci(jd)
        B_eci = igrf_dipole_eci(r_eci, gmst)
        eclipse = in_eclipse(r_eci, sun_hat)
        C_true = quat_to_dcm(q_true)               # ECI->body (truth)

        # ---- target (nadir / LVLH) ------------------------------------------
        C_li = lvlh_dcm(r_eci, v_eci)
        q_ref = dcm_to_quat(C_li)
        # orbital (LVLH) rate vector, expressed in body via the ESTIMATED attitude
        w_orb_eci_now = np.cross(r_eci, v_eci)/np.dot(r_eci, r_eci)
        w_orb = quat_to_dcm(q_est) @ w_orb_eci_now

        # ---- event triggers -------------------------------------------------
        if abs(t - T_IMPACT) < DT/2:
            omega = omega + IMPACT_DELTA_OMEGA      # debris angular-momentum kick
            mode = "SAFE"                           # loss of attitude control
            gain_corrupt[CORRUPTED_AXIS] = CORRUPTION_FACTOR   # latent fault
            _emit(t, "impact",
                  f"t={t:6.0f}s  DEBRIS IMPACT -> SAFE mode, slow roll begins, "
                  f"axis {'XYZ'[CORRUPTED_AXIS]} gain corrupted x{CORRUPTION_FACTOR:.0f}")
        if abs(t - T_RECOVERY) < DT/2:
            mode = "RECOVERY"                       # ground re-enables control
            _emit(t, "recovery",
                  f"t={t:6.0f}s  GROUND RECOVERY -> control re-enabled "
                  f"(corrupted axis will over-torque)")

        # =====================================================================
        #  SENSORS
        # =====================================================================
        # Magnetometer: true body field + bias + white noise
        B_body_true = C_true @ B_eci
        mag_meas = B_body_true + MAG_BIAS_T + rng.normal(0, MAG_NOISE_T, 3)
        # Coarse Sun sensor: unit Sun vector in body + angular noise (sunlit only)
        if not eclipse:
            s_body_true = C_true @ sun_hat
            s_meas = s_body_true + rng.normal(0, np.deg2rad(SUN_NOISE_DEG), 3)
            s_meas /= np.linalg.norm(s_meas)
            sun_valid = 1
        else:
            s_meas = np.array([np.nan, np.nan, np.nan])
            sun_valid = 0

        # =====================================================================
        #  ATTITUDE DETERMINATION (algorithm internal state)
        # =====================================================================
        # --- (a) rate estimate: light low-pass of the measured body rate. Stands
        #     in for a MEMS rate sensor / attitude-rate filter (noise ~0.05 deg/s).
        omega_meas = omega + rng.normal(0, np.deg2rad(0.05), 3)
        omega_est = 0.6*omega_est + 0.4*omega_meas

        # --- (b) PREDICT: propagate the attitude estimate forward with the rate
        q_pred = quat_normalize(q_est + 0.5*quat_mult(q_est,
                                 np.concatenate([omega_est, [0.0]]))*DT)

        # --- (c) CORRECT with TRIAD, but only when the Sun is visible AND the
        #     Sun/field reference vectors are not near-collinear (TRIAD is
        #     ill-conditioned when sin(angle) is small), with innovation gating.
        triad_geom = np.linalg.norm(np.cross(sun_hat, B_eci/np.linalg.norm(B_eci)))
        triad_used = 0
        if sun_valid and triad_geom > 0.17:
            b_corr = mag_meas - MAG_BIAS_T
            C_det = triad(b_corr, s_meas, B_eci, sun_hat)
            q_meas = dcm_to_quat(C_det)
            dn = rng.normal(0, np.deg2rad(EST_ATT_NOISE_DEG), 3)/2.0
            q_meas = quat_normalize(quat_mult(q_meas, np.array([dn[0], dn[1], dn[2], 1.0])))
            if np.dot(q_meas, q_pred) < 0:
                q_meas = -q_meas
            innov = quat_mult(q_meas, quat_conj(q_pred))
            innov_deg = 2*np.rad2deg(np.arccos(min(1.0, abs(innov[3]))))
            if innov_deg < 25.0:                       # gate out bad fixes
                q_est = quat_normalize(0.7*q_pred + 0.3*q_meas)
                triad_used = 1
            else:
                q_est = q_pred                         # reject, rely on prediction
        else:
            q_est = q_pred                             # propagate (eclipse / bad geom)
        if q_est[3] < 0:
            q_est = -q_est                             # keep estimate in +w hemisphere

        # =====================================================================
        #  CONTROL LAW  (PD on quaternion error -> wheels;  MTQ momentum dump)
        # =====================================================================
        # body-frame attitude error: q_err = q_est^-1 (x) q_ref  (vector part in body)
        q_err = quat_mult(quat_conj(q_est), q_ref)
        if q_err[3] < 0:
            q_err = -q_err                            # shortest rotation
        att_err_vec = q_err[:3]
        att_err_deg = 2*np.rad2deg(np.arcsin(np.clip(np.linalg.norm(att_err_vec), 0, 1)))
        _qe_truth = quat_mult(q_est, quat_conj(q_true))
        est_err_deg = 2*np.rad2deg(np.arccos(min(1.0, abs(_qe_truth[3]))))
        omega_rel = omega_est - w_orb                 # rate error vs nadir track

        tau_cmd = np.zeros(3)
        mtq_dipole = np.zeros(3)
        if mode in ("NORMAL", "RECOVERY"):
            kp = KP*gain_corrupt
            kd = KD*gain_corrupt
            # corrupted axis has 10x gain -> commands ~10x the normal torque,
            # which rails the wheel at its hardware ceiling (= ~10x nominal effort)
            tau_cmd = kp*att_err_vec - kd*omega_rel   # desired body torque (raw)
            # magnetorquer momentum-dump law: m = k (h_wheel x B)/|B|^2
            Bn = mag_meas/ (np.linalg.norm(mag_meas)**2 + 1e-30)
            mtq_dipole = MTQ_DUMP_GAIN*np.cross(h_wheel, Bn)
            mtq_dipole = np.clip(mtq_dipole, -MTQ_MAX_DIPOLE, MTQ_MAX_DIPOLE)

        # ---- actuator realisation -------------------------------------------
        # Wheels absorb momentum: body torque = -d(h_wheel)/dt = +tau_cmd (clamped).
        # Healthy axes are limited to the rated wheel torque; the corrupted axis,
        # during the recovery attempt, has a faulted driver that delivers up to
        # CORRUPTION_FACTOR x the rated torque (this is the "10x torque" fault).
        tau_wheel_body = np.clip(tau_cmd, -RW_MAX_TORQUE, RW_MAX_TORQUE)
        if mode == "RECOVERY":
            ceil = RW_MAX_TORQUE*CORRUPTION_FACTOR
            tau_wheel_body[CORRUPTED_AXIS] = np.clip(tau_cmd[CORRUPTED_AXIS], -ceil, ceil)
        h_wheel_new = h_wheel - tau_wheel_body*DT
        # saturate wheel momentum; if saturated, body torque is reduced accordingly
        for ax in range(3):
            if abs(h_wheel_new[ax]) > RW_MAX_MOMENTUM:
                h_wheel_new[ax] = np.sign(h_wheel_new[ax])*RW_MAX_MOMENTUM
                tau_wheel_body[ax] = -(h_wheel_new[ax]-h_wheel[ax])/DT
        h_wheel = h_wheel_new
        mtq_torque = np.cross(mtq_dipole, B_body_true)

        # =====================================================================
        #  ENVIRONMENTAL DISTURBANCE TORQUES
        # =====================================================================
        # Gravity gradient: 3 mu/R^3 * (n_b x (I n_b))
        Rmag = np.linalg.norm(r_eci)
        nadir_b = C_true @ (-r_eci/Rmag)
        M_gg = 3*MU_EARTH/Rmag**3 * np.cross(nadir_b, I @ nadir_b)
        # Residual dipole + aero (small constant-ish)
        M_res = np.cross(np.array([0.002, -0.001, 0.0015]), B_body_true)
        M_aero = np.array([3e-7, -2e-7, 1e-7])
        M_dist = M_gg + M_res + M_aero

        # =====================================================================
        #  ATTITUDE DYNAMICS  (Euler eqn with wheel momentum coupling)  RK4
        # =====================================================================
        M_ext = M_dist + mtq_torque
        def omega_dot(w):
            return Iinv @ (-np.cross(w, I @ w + h_wheel) + M_ext + tau_wheel_body)
        def qdot(q, w):
            return 0.5*quat_mult(q, np.concatenate([w, [0.0]]))

        k1w = omega_dot(omega);              k1q = qdot(q_true, omega)
        k2w = omega_dot(omega+0.5*DT*k1w);   k2q = qdot(quat_normalize(q_true+0.5*DT*k1q), omega+0.5*DT*k1w)
        k3w = omega_dot(omega+0.5*DT*k2w);   k3q = qdot(quat_normalize(q_true+0.5*DT*k2q), omega+0.5*DT*k2w)
        k4w = omega_dot(omega+DT*k3w);       k4q = qdot(quat_normalize(q_true+DT*k3q),     omega+DT*k3w)
        omega = omega + DT/6*(k1w+2*k2w+2*k3w+k4w)
        q_true = quat_normalize(q_true + DT/6*(k1q+2*k2q+2*k3q+k4q))

        # =====================================================================
        #  SAFING LOGIC  (auto-trip back to SAFE)
        # =====================================================================
        wheel_saturated = np.any(np.abs(h_wheel) >= RW_MAX_MOMENTUM*0.999)
        rate_exceeded = np.linalg.norm(omega - w_orb) > SAFE_RATE_LIMIT
        # During recovery the corrupted axis over-torques and ramps its wheel to
        # saturation; that saturation is what trips the ADCS back to SAFE.
        if mode == "RECOVERY" and wheel_saturated:
            sat_ax = 'XYZ'[int(np.argmax(np.abs(h_wheel)))]
            mode = "SAFE"
            _emit(t, "autosafe",
                  f"t={t:7.1f}s ADCS AUTO-SAFE: wheel '{sat_ax}' momentum saturated, "
                  f"driven by corrupted axis '{'XYZ'[CORRUPTED_AXIS]}' "
                  f"{CORRUPTION_FACTOR:.0f}x over-torque "
                  f"-> control off, spacecraft left spinning")
        if mode == "NORMAL" and rate_exceeded:
            mode = "SAFE"

        # ---- wheel speed telemetry (RPM) ------------------------------------
        wheel_rpm = h_wheel/RW_INERTIA*60/(2*np.pi)
        body_rate_deg = np.rad2deg(omega)
        tumble_rate = np.rad2deg(np.linalg.norm(omega - w_orb))

        log.append(dict(
            t=t,
            utc=(EPOCH_UTC+timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%S"),
            mode=mode, eclipse=int(eclipse), sun_valid=sun_valid, triad_used=triad_used,
            mag_x_uT=mag_meas[0]*1e6, mag_y_uT=mag_meas[1]*1e6, mag_z_uT=mag_meas[2]*1e6,
            mag_mag_uT=np.linalg.norm(mag_meas)*1e6,
            sun_x=s_meas[0], sun_y=s_meas[1], sun_z=s_meas[2],
            q_est_x=q_est[0], q_est_y=q_est[1], q_est_z=q_est[2], q_est_w=q_est[3],
            att_err_deg=att_err_deg, est_err_deg=est_err_deg,
            rate_x_dps=body_rate_deg[0], rate_y_dps=body_rate_deg[1], rate_z_dps=body_rate_deg[2],
            tumble_rate_dps=tumble_rate,
            tau_cmd_x=tau_cmd[0], tau_cmd_y=tau_cmd[1], tau_cmd_z=tau_cmd[2],
            tau_wheel_x=tau_wheel_body[0], tau_wheel_y=tau_wheel_body[1], tau_wheel_z=tau_wheel_body[2],
            h_wheel_x=h_wheel[0], h_wheel_y=h_wheel[1], h_wheel_z=h_wheel[2],
            wheel_rpm_x=wheel_rpm[0], wheel_rpm_y=wheel_rpm[1], wheel_rpm_z=wheel_rpm[2],
            mtq_x=mtq_dipole[0], mtq_y=mtq_dipole[1], mtq_z=mtq_dipole[2],
        ))

    return log, orbit

# =============================================================================
#  OUTPUT: CSV + PLOTS
# =============================================================================
def write_csv(log, path):
    keys = list(log[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(log)
    print(f"Wrote telemetry CSV : {path}  ({len(log)} rows, {len(keys)} channels)")

def arr(log, key):
    return np.array([row[key] for row in log], dtype=float)

def phase_shading(ax, ti=None, tr=None, tt=None):
    ti = T_IMPACT if ti is None else ti
    tr = T_RECOVERY if tr is None else tr
    tt = T_TOTAL if tt is None else tt
    ax.axvspan(0, ti/60, color="#e8f5e9", alpha=0.5, lw=0)
    ax.axvspan(ti/60, tr/60, color="#fff3e0", alpha=0.5, lw=0)
    ax.axvspan(tr/60, tt/60, color="#ffebee", alpha=0.5, lw=0)
    ax.axvline(ti/60, color="k", ls="--", lw=1)
    ax.axvline(tr/60, color="k", ls="--", lw=1)

def make_plots(log, outdir, params=None):
    if not HAVE_MATPLOTLIB:
        raise RuntimeError("matplotlib is required for plot output "
                           "(pip install matplotlib)")
    cfg = resolve_params(params)
    T_IMPACT, T_RECOVERY = cfg["t_impact"], cfg["t_recovery"]
    t = arr(log, "t")/60.0   # minutes

    # ---- Plot 1: SENSORS ----
    fig, ax = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    for a in ax: phase_shading(a, T_IMPACT, T_RECOVERY, cfg["t_total"])
    ax[0].plot(t, arr(log,"mag_x_uT"), label="Bx", lw=0.8)
    ax[0].plot(t, arr(log,"mag_y_uT"), label="By", lw=0.8)
    ax[0].plot(t, arr(log,"mag_z_uT"), label="Bz", lw=0.8)
    ax[0].plot(t, arr(log,"mag_mag_uT"), "k", label="|B|", lw=1.3)
    ax[0].set_ylabel("Magnetometer [uT]"); ax[0].legend(ncol=4, fontsize=8); ax[0].grid(alpha=0.3)
    ax[0].set_title("SENSOR TELEMETRY  (green=NORMAL  orange=POST-IMPACT roll  red=RECOVERY/after)")
    ax[1].plot(t, arr(log,"sun_x"), label="Sun_x (body)", lw=0.8)
    ax[1].plot(t, arr(log,"sun_y"), label="Sun_y (body)", lw=0.8)
    ax[1].plot(t, arr(log,"sun_z"), label="Sun_z (body)", lw=0.8)
    ax[1].set_ylabel("Sun sensor [unit]"); ax[1].legend(ncol=3, fontsize=8); ax[1].grid(alpha=0.3)
    ax[2].plot(t, arr(log,"eclipse"), label="eclipse flag", lw=1.0, color="navy")
    ax[2].plot(t, arr(log,"sun_valid"), label="sun valid", lw=1.0, color="orange", ls=":")
    ax[2].set_ylabel("Flags"); ax[2].set_xlabel("Time since epoch [min]")
    ax[2].legend(fontsize=8); ax[2].grid(alpha=0.3); ax[2].set_ylim(-0.1, 1.1)
    fig.tight_layout(); p=os.path.join(outdir,"plot_1_sensors.png"); fig.savefig(p, dpi=110); plt.close(fig)
    print(f"Wrote plot          : {p}")

    # ---- Plot 2: ESTIMATOR / CONTROL STATE ----
    fig, ax = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    for a in ax: phase_shading(a, T_IMPACT, T_RECOVERY, cfg["t_total"])
    ax[0].plot(t, arr(log,"att_err_deg"), color="crimson", lw=1.0)
    ax[0].set_ylabel("Attitude error [deg]"); ax[0].grid(alpha=0.3)
    ax[0].set_title("ADCS INTERNAL STATE: pointing error, body rates, control mode")
    ax[1].plot(t, arr(log,"rate_x_dps"), label="wx", lw=0.8)
    ax[1].plot(t, arr(log,"rate_y_dps"), label="wy", lw=0.8)
    ax[1].plot(t, arr(log,"rate_z_dps"), label="wz", lw=0.8)
    ax[1].plot(t, arr(log,"tumble_rate_dps"), "k", label="|rate vs nadir|", lw=1.2)
    ax[1].set_ylabel("Body rate [deg/s]"); ax[1].legend(ncol=4, fontsize=8); ax[1].grid(alpha=0.3)
    modemap = {"NORMAL":2, "RECOVERY":1, "SAFE":0}
    mode_num = np.array([modemap[r["mode"]] for r in log])
    ax[2].plot(t, mode_num, drawstyle="steps-post", color="purple", lw=1.4)
    ax[2].set_yticks([0,1,2]); ax[2].set_yticklabels(["SAFE","RECOVERY","NORMAL"])
    ax[2].set_ylabel("Control mode"); ax[2].set_xlabel("Time since epoch [min]"); ax[2].grid(alpha=0.3)
    fig.tight_layout(); p=os.path.join(outdir,"plot_2_estimator.png"); fig.savefig(p, dpi=110); plt.close(fig)
    print(f"Wrote plot          : {p}")

    # ---- Plot 3: ACTUATORS ----
    fig, ax = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    for a in ax: phase_shading(a, T_IMPACT, T_RECOVERY, cfg["t_total"])
    ax[0].plot(t, arr(log,"tau_wheel_x")*1e3, label="tau_x", lw=0.8)
    ax[0].plot(t, arr(log,"tau_wheel_y")*1e3, label="tau_y", lw=0.8)
    ax[0].plot(t, arr(log,"tau_wheel_z")*1e3, label="tau_z", lw=0.8)
    ax[0].set_ylabel("Wheel torque [mN m]"); ax[0].legend(ncol=3, fontsize=8); ax[0].grid(alpha=0.3)
    ax[0].set_title("ACTUATOR OUTPUTS: reaction-wheel torque & momentum, magnetorquer dipole")
    ax[1].plot(t, arr(log,"h_wheel_x")*1e3, label="h_x", lw=0.9)
    ax[1].plot(t, arr(log,"h_wheel_y")*1e3, label="h_y", lw=0.9)
    ax[1].plot(t, arr(log,"h_wheel_z")*1e3, label="h_z", lw=0.9)
    ax[1].axhline(RW_MAX_MOMENTUM*1e3, color="r", ls=":", lw=1, label="sat limit")
    ax[1].axhline(-RW_MAX_MOMENTUM*1e3, color="r", ls=":", lw=1)
    ax[1].set_ylabel("Wheel momentum [mN m s]"); ax[1].legend(ncol=4, fontsize=8); ax[1].grid(alpha=0.3)
    ax[2].plot(t, arr(log,"mtq_x"), label="m_x", lw=0.8)
    ax[2].plot(t, arr(log,"mtq_y"), label="m_y", lw=0.8)
    ax[2].plot(t, arr(log,"mtq_z"), label="m_z", lw=0.8)
    ax[2].set_ylabel("MTQ dipole [A m^2]"); ax[2].set_xlabel("Time since epoch [min]")
    ax[2].legend(ncol=3, fontsize=8); ax[2].grid(alpha=0.3)
    fig.tight_layout(); p=os.path.join(outdir,"plot_3_actuators.png"); fig.savefig(p, dpi=110); plt.close(fig)
    print(f"Wrote plot          : {p}")

    # ---- Plot 4: ZOOM on the two events (seconds axis) ----
    ts = arr(log, "t")
    def zoom_panel(axcol, t0, t1, title):
        m = (ts >= t0) & (ts <= t1)
        tt = ts[m]
        mk = dict(marker=".", ms=4)
        axcol[0].set_title(title, fontsize=10)
        axcol[0].plot(tt, arr(log,"rate_x_dps")[m], label="wx", **mk)
        axcol[0].plot(tt, arr(log,"rate_y_dps")[m], label="wy", **mk)
        axcol[0].plot(tt, arr(log,"rate_z_dps")[m], label="wz", **mk)
        axcol[0].plot(tt, arr(log,"tumble_rate_dps")[m], "k", lw=1.3, label="|rate vs nadir|")
        axcol[0].axvline(T_IMPACT if t0<T_IMPACT<t1 else T_RECOVERY, color="purple", ls="--", lw=1)
        axcol[0].set_ylabel("Body rate [deg/s]"); axcol[0].grid(alpha=0.3); axcol[0].legend(fontsize=7, ncol=2)
        axcol[1].plot(tt, arr(log,"tau_cmd_x")[m]*1e3, label="cmd_x (corrupted)", **mk)
        axcol[1].plot(tt, arr(log,"tau_wheel_x")[m]*1e3, label="delivered_x", **mk)
        axcol[1].plot(tt, arr(log,"tau_wheel_y")[m]*1e3, label="delivered_y", alpha=0.7, **mk)
        axcol[1].axhline(RW_MAX_TORQUE*1e3, color="grey", ls=":", lw=1, label="rated torque")
        axcol[1].set_ylabel("Torque [mN m]"); axcol[1].grid(alpha=0.3); axcol[1].legend(fontsize=7)
        axcol[2].plot(tt, arr(log,"h_wheel_x")[m]*1e3, label="h_x (corrupted)", **mk)
        axcol[2].plot(tt, arr(log,"h_wheel_y")[m]*1e3, label="h_y", **mk)
        axcol[2].plot(tt, arr(log,"h_wheel_z")[m]*1e3, label="h_z", **mk)
        axcol[2].axhline(RW_MAX_MOMENTUM*1e3, color="r", ls=":", lw=1, label="sat limit")
        axcol[2].axhline(-RW_MAX_MOMENTUM*1e3, color="r", ls=":", lw=1)
        axcol[2].set_ylabel("Wheel mom. [mN m s]"); axcol[2].set_xlabel("Time since epoch [s]")
        axcol[2].grid(alpha=0.3); axcol[2].legend(fontsize=7, ncol=2)
    fig, ax = plt.subplots(3, 2, figsize=(14, 10), sharex="col")
    zoom_panel(ax[:,0], T_IMPACT-30, T_IMPACT+90, "ZOOM: DEBRIS IMPACT (kick -> SAFE -> slow roll)")
    zoom_panel(ax[:,1], T_RECOVERY-20, T_RECOVERY+220, "ZOOM: RECOVERY ATTEMPT (corrupted axis 10x over-torque -> wheel saturation -> auto-SAFE)")
    fig.tight_layout(); p=os.path.join(outdir,"plot_4_event_zoom.png"); fig.savefig(p, dpi=110); plt.close(fig)
    print(f"Wrote plot          : {p}")


if __name__ == "__main__":
    outdir = os.path.dirname(os.path.abspath(__file__))
    print("="*70)
    print(" CubeSat Nadir-Pointing ADCS Telemetry Simulation")
    print("="*70)
    log, orbit = run()
    write_csv(log, os.path.join(outdir, "cubesat_adcs_telemetry.csv"))
    make_plots(log, outdir)
    print("Done.")

"""
EO Mission Simulator — Orbit Propagator
SGP4 propagation with eclipse detection, ground station contact windows,
solar beta angle, and sub-satellite point computation.
"""
import math
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from sgp4.api import Satrec, jday

from config import (
    TLE_LINE1, TLE_LINE2,
    EARTH_RADIUS_KM, GS_MIN_ELEVATION,
    GS_LAT_DEG, GS_LON_DEG, GS_ALT_KM,
    ORBIT_ALTITUDE_KM,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEG  = math.pi / 180.0
_RAD  = 180.0 / math.pi
_AU   = 1.495978707e8      # km per AU
_EARTH_FLATTENING = 1.0 / 298.257223563


class OrbitState:
    """Snapshot of spacecraft orbital state at a given epoch."""
    __slots__ = (
        "utc", "pos_eci", "vel_eci",
        "lat_deg", "lon_deg", "alt_km",
        "vel_x", "vel_y", "vel_z",
        "in_eclipse", "solar_beta_deg",
        "sun_eci",
        "gs_elevation_deg", "gs_azimuth_deg", "gs_range_km",
        "in_contact",
    )

    def __init__(self):
        self.utc             = datetime.now(timezone.utc)
        self.pos_eci         = np.zeros(3)
        self.vel_eci         = np.zeros(3)
        self.lat_deg         = 0.0
        self.lon_deg         = 0.0
        self.alt_km          = ORBIT_ALTITUDE_KM
        self.vel_x           = 0.0
        self.vel_y           = 0.0
        self.vel_z           = 0.0
        self.in_eclipse      = False
        self.solar_beta_deg  = 0.0
        self.sun_eci         = np.zeros(3)
        self.gs_elevation_deg = -90.0
        self.gs_azimuth_deg   = 0.0
        self.gs_range_km      = 0.0
        self.in_contact       = False


class OrbitPropagator:
    """
    SGP4-based orbit propagator.

    Usage:
        prop = OrbitPropagator()
        prop.reset(epoch_utc)        # set simulation clock
        state = prop.propagate()     # returns OrbitState for current time
        prop.advance(dt_seconds)     # step forward by dt
    """

    def __init__(
        self,
        tle_line1: str = TLE_LINE1,
        tle_line2: str = TLE_LINE2,
        gs_lat: float  = GS_LAT_DEG,
        gs_lon: float  = GS_LON_DEG,
        gs_alt_km: float = GS_ALT_KM,
    ):
        self._sat     = Satrec.twoline2rv(tle_line1, tle_line2)
        self._gs_lat  = gs_lat * _DEG
        self._gs_lon  = gs_lon * _DEG
        self._gs_alt  = gs_alt_km
        self._gs_ecef = self._lla_to_ecef(gs_lat, gs_lon, gs_alt_km)

        # Simulation wall-clock (UTC), advances with each call to advance()
        self._sim_utc: datetime = datetime.now(timezone.utc)

        # Last computed state (cached)
        self.state = OrbitState()
        self._propagate_internal()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def reset(self, epoch_utc: datetime) -> None:
        """Reset simulation clock to the given UTC epoch."""
        self._sim_utc = epoch_utc.replace(tzinfo=timezone.utc) if epoch_utc.tzinfo is None else epoch_utc
        self._propagate_internal()

    def advance(self, dt_seconds: float) -> "OrbitState":
        """Step the simulation clock forward by dt_seconds and return new state."""
        self._sim_utc += timedelta(seconds=dt_seconds)
        self._propagate_internal()
        return self.state

    @property
    def utc(self) -> datetime:
        return self._sim_utc

    def contact_windows(
        self, duration_s: float = 7200.0, step_s: float = 30.0
    ) -> list:
        """
        Predict ground station contact windows over the next *duration_s* seconds.
        Returns a list of dicts: {aos, los, max_elevation_deg, duration_s}
        """
        windows = []
        t_start = self._sim_utc
        in_pass  = False
        aos_time = None
        max_el   = 0.0

        t = t_start
        while (t - t_start).total_seconds() < duration_s:
            el = self._elevation_at(t)
            if not in_pass and el >= GS_MIN_ELEVATION:
                in_pass  = True
                aos_time = t
                max_el   = el
            elif in_pass:
                if el > max_el:
                    max_el = el
                if el < GS_MIN_ELEVATION:
                    in_pass = False
                    pass_dur = (t - aos_time).total_seconds()
                    windows.append({
                        "aos": aos_time,
                        "los": t,
                        "max_elevation_deg": round(max_el, 2),
                        "duration_s": round(pass_dur, 1),
                    })
            t += timedelta(seconds=step_s)

        return windows

    # ------------------------------------------------------------------
    # Internal propagation
    # ------------------------------------------------------------------

    def _propagate_internal(self) -> None:
        t = self._sim_utc
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond * 1e-6)

        e, r, v = self._sat.sgp4(jd, fr)
        if e != 0:
            # SGP4 error — keep last state
            return

        r = np.array(r)   # ECI km
        v = np.array(v)   # ECI km/s

        # Sub-satellite point
        gmst = self._gmst(jd + fr)
        lat, lon, alt = self._eci_to_lla(r, gmst)

        # Sun vector in ECI
        sun_eci = self._sun_eci(jd + fr)

        # Eclipse: cylindrical Earth shadow model
        in_eclipse = self._is_eclipse(r, sun_eci)

        # Solar beta angle (deg between orbit plane normal and sun)
        h = np.cross(r, v)                      # orbit angular momentum
        h_hat = h / (np.linalg.norm(h) + 1e-9)
        sun_hat = sun_eci / (np.linalg.norm(sun_eci) + 1e-9)
        beta_rad = math.asin(np.dot(h_hat, sun_hat))
        beta_deg = beta_rad * _RAD

        # Ground station geometry
        gs_ecef = self._gs_ecef
        r_ecef  = self._eci_to_ecef(r, gmst)
        el_deg, az_deg, rng_km = self._look_angles(r_ecef, gs_ecef, self._gs_lat, self._gs_lon)
        in_contact = el_deg >= GS_MIN_ELEVATION

        s = self.state
        s.utc             = t
        s.pos_eci         = r
        s.vel_eci         = v
        s.lat_deg         = lat
        s.lon_deg         = lon
        s.alt_km          = alt
        s.vel_x, s.vel_y, s.vel_z = float(v[0]), float(v[1]), float(v[2])
        s.in_eclipse      = in_eclipse
        s.solar_beta_deg  = beta_deg
        s.sun_eci         = sun_eci
        s.gs_elevation_deg = el_deg
        s.gs_azimuth_deg   = az_deg
        s.gs_range_km      = rng_km
        s.in_contact       = in_contact

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _gmst(jd_ut1: float) -> float:
        """Greenwich Mean Sidereal Time (radians) from Julian Date."""
        T = (jd_ut1 - 2451545.0) / 36525.0
        gmst_deg = (280.46061837
                    + 360.98564736629 * (jd_ut1 - 2451545.0)
                    + 0.000387933 * T * T
                    - T * T * T / 38710000.0) % 360.0
        return gmst_deg * _DEG

    @staticmethod
    def _eci_to_ecef(r_eci: np.ndarray, gmst: float) -> np.ndarray:
        """Rotate ECI → ECEF using GMST."""
        c, s = math.cos(gmst), math.sin(gmst)
        return np.array([
            c * r_eci[0] + s * r_eci[1],
           -s * r_eci[0] + c * r_eci[1],
            r_eci[2],
        ])

    @staticmethod
    def _eci_to_lla(r_eci: np.ndarray, gmst: float) -> Tuple[float, float, float]:
        """ECI → geodetic latitude (deg), longitude (deg), altitude (km)."""
        c, s = math.cos(gmst), math.sin(gmst)
        # ECI → ECEF (km)
        x_km =  c * r_eci[0] + s * r_eci[1]
        y_km = -s * r_eci[0] + c * r_eci[1]
        z_km = r_eci[2]

        # Convert to metres for geodetic calculation
        x, y, z = x_km * 1000.0, y_km * 1000.0, z_km * 1000.0

        lon = math.atan2(y, x) * _RAD
        a   = EARTH_RADIUS_KM * 1000.0          # metres
        e2  = 2 * _EARTH_FLATTENING - _EARTH_FLATTENING ** 2
        p   = math.sqrt(x * x + y * y)          # metres

        # Iterative Bowring method
        lat = math.atan2(z, p * (1.0 - e2))
        for _ in range(5):
            sin_lat = math.sin(lat)
            N  = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
            lat = math.atan2(z + e2 * N * sin_lat, p)

        sin_lat, cos_lat = math.sin(lat), math.cos(lat)
        N  = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
        if abs(cos_lat) > 0.01:
            alt_m = p / cos_lat - N
        else:
            alt_m = z / sin_lat - N * (1.0 - e2)

        return lat * _RAD, lon, alt_m / 1000.0  # deg, deg, km

    @staticmethod
    def _lla_to_ecef(lat_deg: float, lon_deg: float, alt_km: float) -> np.ndarray:
        """Geodetic LLA → ECEF (km)."""
        lat = lat_deg * _DEG
        lon = lon_deg * _DEG
        a   = EARTH_RADIUS_KM
        e2  = 2 * _EARTH_FLATTENING - _EARTH_FLATTENING ** 2
        N   = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        r   = (N + alt_km) * math.cos(lat)
        return np.array([r * math.cos(lon), r * math.sin(lon), (N * (1 - e2) + alt_km) * math.sin(lat)])

    @staticmethod
    def _is_eclipse(r_eci: np.ndarray, sun_eci: np.ndarray) -> bool:
        """Cylindrical Earth shadow model."""
        r_mag   = np.linalg.norm(r_eci)
        sun_hat = sun_eci / (np.linalg.norm(sun_eci) + 1e-9)
        # Component of position along sun direction
        dot     = np.dot(r_eci, sun_hat)
        # Perpendicular distance from shadow cylinder axis
        perp    = math.sqrt(max(r_mag * r_mag - dot * dot, 0.0))
        return (dot < 0.0) and (perp < EARTH_RADIUS_KM)

    @staticmethod
    def _sun_eci(jd: float) -> np.ndarray:
        """
        Low-precision Sun unit vector in ECI, scaled to 1 AU (km).
        Accuracy ~1° sufficient for eclipse and beta angle.
        """
        n  = jd - 2451545.0          # days since J2000
        L  = (280.460 + 0.9856474 * n) % 360.0
        g  = (357.528 + 0.9856003 * n) % 360.0
        g_rad = g * _DEG
        lam   = (L + 1.915 * math.sin(g_rad) + 0.020 * math.sin(2 * g_rad)) * _DEG
        eps   = (23.439 - 0.0000004 * n) * _DEG
        return _AU * np.array([math.cos(lam), math.sin(lam) * math.cos(eps), math.sin(lam) * math.sin(eps)])

    @staticmethod
    def _look_angles(
        sc_ecef: np.ndarray,
        gs_ecef: np.ndarray,
        gs_lat: float,   # radians
        gs_lon: float,   # radians
    ) -> Tuple[float, float, float]:
        """
        Compute elevation (deg), azimuth (deg), range (km) from ground station
        to spacecraft in ECEF.
        """
        rho     = sc_ecef - gs_ecef
        rng_km  = float(np.linalg.norm(rho))

        # SEZ (South-East-Zenith) transform
        sin_lat, cos_lat = math.sin(gs_lat), math.cos(gs_lat)
        sin_lon, cos_lon = math.sin(gs_lon), math.cos(gs_lon)

        S = ( sin_lat * cos_lon * rho[0]
            + sin_lat * sin_lon * rho[1]
            - cos_lat           * rho[2])
        E = (-sin_lon           * rho[0]
            + cos_lon           * rho[1])
        Z = ( cos_lat * cos_lon * rho[0]
            + cos_lat * sin_lon * rho[1]
            + sin_lat           * rho[2])

        el_rad = math.asin(Z / (rng_km + 1e-9))
        az_rad = math.atan2(E, -S)

        return el_rad * _RAD, (az_rad * _RAD) % 360.0, rng_km

    def _elevation_at(self, utc: datetime) -> float:
        """Quick elevation calculation at a given UTC without updating state."""
        t = utc
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond * 1e-6)
        e, r, _ = self._sat.sgp4(jd, fr)
        if e != 0:
            return -90.0
        r = np.array(r)
        gmst = self._gmst(jd + fr)
        r_ecef = self._eci_to_ecef(r, gmst)
        el, _, _ = self._look_angles(r_ecef, self._gs_ecef, self._gs_lat, self._gs_lon)
        return el

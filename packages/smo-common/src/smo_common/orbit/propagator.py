"""SMO Common — SGP4 Orbit Propagator.

Config-driven orbit propagation with eclipse detection, ground station
contact windows, solar beta angle, and sub-satellite point computation.
"""
import math
import numpy as np
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

from sgp4.api import Satrec, jday

_DEG = math.pi / 180.0
_RAD = 180.0 / math.pi
_AU = 1.495978707e8  # km per AU
_EARTH_FLATTENING = 1.0 / 298.257223563


@dataclass
class OrbitState:
    """Snapshot of spacecraft orbital state at a given epoch."""
    utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pos_eci: np.ndarray = field(default_factory=lambda: np.zeros(3))
    vel_eci: np.ndarray = field(default_factory=lambda: np.zeros(3))
    lat_deg: float = 0.0
    lon_deg: float = 0.0
    alt_km: float = 500.0
    vel_x: float = 0.0
    vel_y: float = 0.0
    vel_z: float = 0.0
    in_eclipse: bool = False
    solar_beta_deg: float = 0.0
    sun_eci: np.ndarray = field(default_factory=lambda: np.zeros(3))
    gs_elevation_deg: float = -90.0
    gs_azimuth_deg: float = 0.0
    gs_range_km: float = 0.0
    in_contact: bool = False


@dataclass
class GroundStation:
    """Ground station definition for contact window computation."""
    name: str
    lat_deg: float
    lon_deg: float
    alt_km: float = 0.0
    min_elevation_deg: float = 5.0

    def __post_init__(self):
        self.lat_rad = self.lat_deg * _DEG
        self.lon_rad = self.lon_deg * _DEG
        self.ecef = _lla_to_ecef(self.lat_deg, self.lon_deg, self.alt_km)


class OrbitPropagator:
    """SGP4-based orbit propagator with ground station contact geometry."""

    def __init__(
        self,
        tle_line1: str,
        tle_line2: str,
        ground_stations: list[GroundStation] | None = None,
        earth_radius_km: float = 6371.0,
    ):
        self._sat = Satrec.twoline2rv(tle_line1, tle_line2)
        self._earth_r = earth_radius_km
        self._ground_stations = ground_stations or []
        self._primary_gs = self._ground_stations[0] if self._ground_stations else None
        self._sim_utc: datetime = datetime.now(timezone.utc)
        self.state = OrbitState()
        self._propagate_internal()

    def reset(self, epoch_utc: datetime) -> None:
        """Reset simulation clock to the given UTC epoch."""
        if epoch_utc.tzinfo is None:
            epoch_utc = epoch_utc.replace(tzinfo=timezone.utc)
        self._sim_utc = epoch_utc
        self._propagate_internal()

    def advance(self, dt_seconds: float) -> OrbitState:
        """Step the simulation clock forward and return new state."""
        self._sim_utc += timedelta(seconds=dt_seconds)
        self._propagate_internal()
        return self.state

    @property
    def utc(self) -> datetime:
        return self._sim_utc

    def contact_windows(
        self,
        duration_s: float = 7200.0,
        step_s: float = 30.0,
        gs: GroundStation | None = None,
    ) -> list[dict]:
        """Predict contact windows over the next duration_s seconds."""
        gs = gs or self._primary_gs
        if gs is None:
            return []
        windows = []
        t_start = self._sim_utc
        in_pass = False
        aos_time = None
        max_el = 0.0

        t = t_start
        while (t - t_start).total_seconds() < duration_s:
            el = self._elevation_at(t, gs)
            if not in_pass and el >= gs.min_elevation_deg:
                in_pass = True
                aos_time = t
                max_el = el
            elif in_pass:
                if el > max_el:
                    max_el = el
                if el < gs.min_elevation_deg:
                    in_pass = False
                    pass_dur = (t - aos_time).total_seconds()
                    windows.append({
                        "aos": aos_time.isoformat(),
                        "los": t.isoformat(),
                        "gs_name": gs.name,
                        "max_elevation_deg": round(max_el, 2),
                        "duration_s": round(pass_dur, 1),
                    })
            t += timedelta(seconds=step_s)
        return windows

    def _propagate_internal(self) -> None:
        t = self._sim_utc
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                       t.second + t.microsecond * 1e-6)

        e, r, v = self._sat.sgp4(jd, fr)
        if e != 0:
            return

        r = np.array(r)
        v = np.array(v)
        gmst = _gmst(jd + fr)
        lat, lon, alt = _eci_to_lla(r, gmst, self._earth_r)
        sun_eci = _sun_eci(jd + fr)
        in_eclipse = _is_eclipse(r, sun_eci, self._earth_r)

        h = np.cross(r, v)
        h_hat = h / (np.linalg.norm(h) + 1e-9)
        sun_hat = sun_eci / (np.linalg.norm(sun_eci) + 1e-9)
        beta_rad = math.asin(np.clip(np.dot(h_hat, sun_hat), -1.0, 1.0))

        # Primary ground station geometry
        el_deg, az_deg, rng_km = -90.0, 0.0, 0.0
        in_contact = False
        if self._primary_gs:
            gs = self._primary_gs
            r_ecef = _eci_to_ecef(r, gmst)
            el_deg, az_deg, rng_km = _look_angles(r_ecef, gs.ecef, gs.lat_rad, gs.lon_rad)
            in_contact = el_deg >= gs.min_elevation_deg

        s = self.state
        s.utc = t
        s.pos_eci = r
        s.vel_eci = v
        s.lat_deg = lat
        s.lon_deg = lon
        s.alt_km = alt
        s.vel_x, s.vel_y, s.vel_z = float(v[0]), float(v[1]), float(v[2])
        s.in_eclipse = in_eclipse
        s.solar_beta_deg = beta_rad * _RAD
        s.sun_eci = sun_eci
        s.gs_elevation_deg = el_deg
        s.gs_azimuth_deg = az_deg
        s.gs_range_km = rng_km
        s.in_contact = in_contact

    def _elevation_at(self, utc: datetime, gs: GroundStation) -> float:
        t = utc
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                       t.second + t.microsecond * 1e-6)
        e, r, _ = self._sat.sgp4(jd, fr)
        if e != 0:
            return -90.0
        r = np.array(r)
        gmst_val = _gmst(jd + fr)
        r_ecef = _eci_to_ecef(r, gmst_val)
        el, _, _ = _look_angles(r_ecef, gs.ecef, gs.lat_rad, gs.lon_rad)
        return el


# Module-level geometry helpers (pure functions)

def _gmst(jd_ut1: float) -> float:
    T = (jd_ut1 - 2451545.0) / 36525.0
    gmst_deg = (280.46061837 + 360.98564736629 * (jd_ut1 - 2451545.0)
                + 0.000387933 * T * T - T * T * T / 38710000.0) % 360.0
    return gmst_deg * _DEG


def _eci_to_ecef(r_eci: np.ndarray, gmst: float) -> np.ndarray:
    c, s = math.cos(gmst), math.sin(gmst)
    return np.array([c * r_eci[0] + s * r_eci[1],
                     -s * r_eci[0] + c * r_eci[1],
                     r_eci[2]])


def _eci_to_lla(r_eci: np.ndarray, gmst: float, earth_r_km: float = 6371.0):
    c, s = math.cos(gmst), math.sin(gmst)
    x_km = c * r_eci[0] + s * r_eci[1]
    y_km = -s * r_eci[0] + c * r_eci[1]
    z_km = r_eci[2]
    x, y, z = x_km * 1000.0, y_km * 1000.0, z_km * 1000.0
    lon = math.atan2(y, x) * _RAD
    a = earth_r_km * 1000.0
    e2 = 2 * _EARTH_FLATTENING - _EARTH_FLATTENING ** 2
    p = math.sqrt(x * x + y * y)
    lat = math.atan2(z, p * (1.0 - e2))
    for _ in range(5):
        sin_lat = math.sin(lat)
        N = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
        lat = math.atan2(z + e2 * N * sin_lat, p)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    N = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
    if abs(cos_lat) > 0.01:
        alt_m = p / cos_lat - N
    else:
        alt_m = z / sin_lat - N * (1.0 - e2)
    return lat * _RAD, lon, alt_m / 1000.0


def _lla_to_ecef(lat_deg: float, lon_deg: float, alt_km: float,
                 earth_r_km: float = 6371.0) -> np.ndarray:
    lat = lat_deg * _DEG
    lon = lon_deg * _DEG
    e2 = 2 * _EARTH_FLATTENING - _EARTH_FLATTENING ** 2
    N = earth_r_km / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    r = (N + alt_km) * math.cos(lat)
    return np.array([r * math.cos(lon), r * math.sin(lon),
                     (N * (1 - e2) + alt_km) * math.sin(lat)])


def _is_eclipse(r_eci: np.ndarray, sun_eci: np.ndarray, earth_r_km: float = 6371.0) -> bool:
    r_mag = np.linalg.norm(r_eci)
    sun_hat = sun_eci / (np.linalg.norm(sun_eci) + 1e-9)
    dot = np.dot(r_eci, sun_hat)
    perp = math.sqrt(max(r_mag * r_mag - dot * dot, 0.0))
    return (dot < 0.0) and (perp < earth_r_km)


def _sun_eci(jd: float) -> np.ndarray:
    n = jd - 2451545.0
    L = (280.460 + 0.9856474 * n) % 360.0
    g = (357.528 + 0.9856003 * n) % 360.0
    g_rad = g * _DEG
    lam = (L + 1.915 * math.sin(g_rad) + 0.020 * math.sin(2 * g_rad)) * _DEG
    eps = (23.439 - 0.0000004 * n) * _DEG
    return _AU * np.array([math.cos(lam), math.sin(lam) * math.cos(eps),
                           math.sin(lam) * math.sin(eps)])


def _look_angles(sc_ecef: np.ndarray, gs_ecef: np.ndarray,
                 gs_lat_rad: float, gs_lon_rad: float):
    rho = sc_ecef - gs_ecef
    rng_km = float(np.linalg.norm(rho))
    sin_lat, cos_lat = math.sin(gs_lat_rad), math.cos(gs_lat_rad)
    sin_lon, cos_lon = math.sin(gs_lon_rad), math.cos(gs_lon_rad)
    S = (sin_lat * cos_lon * rho[0] + sin_lat * sin_lon * rho[1] - cos_lat * rho[2])
    E = (-sin_lon * rho[0] + cos_lon * rho[1])
    Z = (cos_lat * cos_lon * rho[0] + cos_lat * sin_lon * rho[1] + sin_lat * rho[2])
    el_rad = math.asin(Z / (rng_km + 1e-9))
    az_rad = math.atan2(E, -S)
    return el_rad * _RAD, (az_rad * _RAD) % 360.0, rng_km

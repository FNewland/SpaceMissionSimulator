"""SMO Common — SGP4 Orbit Propagator.

Config-driven orbit propagation with eclipse detection, ground station
contact windows, solar beta angle, and sub-satellite point computation.
"""
import math
import numpy as np
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

from sgp4.api import Satrec, jday, WGS72
from sgp4.exporter import export_tle

from .eclipse import is_in_eclipse

_DEG = math.pi / 180.0
_RAD = 180.0 / math.pi
_AU = 1.495978707e8  # km per AU
_EARTH_FLATTENING = 1.0 / 298.257223563
_MU_KM3_S2 = 398600.8  # WGS-72 gravitational parameter, km^3/s^2


class DeltaVFitError(RuntimeError):
    """The post-burn SGP4 element fit failed; the elset was left unchanged."""


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
    # Multi-station contact support: name of the ground station currently in
    # contact (highest elevation wins if several are simultaneously visible),
    # or None when no station is above its elevation mask. The single-valued
    # gs_* fields above always describe the "best" station (the active one,
    # or the closest-to-visible one when out of contact) so existing
    # single-station consumers keep working unchanged.
    active_gs: Optional[str] = None
    # Per-station elevation (deg) keyed by station name, refreshed each step.
    gs_elevations_deg: dict = field(default_factory=dict)


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
        self._tle0_line1 = tle_line1
        self._tle0_line2 = tle_line2
        # Manoeuvre bookkeeping (Phase D). SGP4 is stateless given a UTC time
        # ONLY as long as the element set never changes; a propulsive burn
        # breaks that. The propagator therefore owns a mutable, versioned
        # element set: _epoch_version increments on every applied delta-V and
        # get_elset()/set_elset() serialise the active TLE for breakpoints and
        # for the wire (/api/state -> planner + MCS).
        self._epoch_version: int = 0
        self._active_tle: tuple[str, str] = (tle_line1, tle_line2)
        self._earth_r = earth_radius_km
        self._ground_stations = ground_stations or []
        self._primary_gs = self._ground_stations[0] if self._ground_stations else None
        self._sim_utc: datetime = datetime.now(timezone.utc)
        self.state = OrbitState()
        self._propagate_internal()

    # ── Manoeuvre support (Phase D, brief 03 WP-1) ────────────────────

    @property
    def epoch_version(self) -> int:
        """Increments once per applied delta-V (planner/MCS cache key)."""
        return self._epoch_version

    def get_elset(self) -> Optional[dict]:
        """Active element set for breakpoint/wire serialisation.

        Returns ``None`` while the spacecraft has never manoeuvred (the
        orbit.yaml TLE is authoritative), else a dict with the fitted TLE
        lines and the epoch version.
        """
        if self._epoch_version == 0:
            return None
        s = self._sat
        return {
            # Human/wire-friendly TLE (planner + MCS build propagators from
            # these two strings — no new code path).
            "tle_line1": self._active_tle[0],
            "tle_line2": self._active_tle[1],
            "epoch_version": self._epoch_version,
            # Full-precision mean elements. The TLE text quantises angles to
            # 1e-4 deg (~1.3 m of position); breakpoint restore uses these so
            # a save/load round-trip is sub-millimetre.
            "elements": {
                "epoch_1949": float(s.jdsatepoch - 2433281.5 + s.jdsatepochF),
                "no_kozai": float(s.no_kozai),
                "ecco": float(s.ecco),
                "inclo": float(s.inclo),
                "nodeo": float(s.nodeo),
                "argpo": float(s.argpo),
                "mo": float(s.mo),
                "bstar": float(s.bstar),
                "ndot": float(s.ndot),
                "nddot": float(s.nddot),
            },
        }

    def set_elset(self, elset: Optional[dict]) -> None:
        """Restore an element set produced by :meth:`get_elset`.

        ``None`` (or a missing key in an old snapshot) restores the original
        config TLE — old snapshots load as un-manoeuvred.
        """
        if not elset:
            self._sat = Satrec.twoline2rv(self._tle0_line1, self._tle0_line2)
            self._active_tle = (self._tle0_line1, self._tle0_line2)
            self._epoch_version = 0
        else:
            el = elset.get("elements")
            l1 = elset.get("tle_line1", self._tle0_line1)
            l2 = elset.get("tle_line2", self._tle0_line2)
            if el:
                # Full-precision restore (breakpoints).
                template = Satrec.twoline2rv(l1, l2)
                sat = Satrec()
                sat.sgp4init(
                    WGS72, 'i', template.satnum, float(el["epoch_1949"]),
                    float(el.get("bstar", 0.0)), float(el.get("ndot", 0.0)),
                    float(el.get("nddot", 0.0)),
                    float(el["ecco"]), float(el["argpo"]), float(el["inclo"]),
                    float(el["mo"]), float(el["no_kozai"]), float(el["nodeo"]),
                )
                self._sat = sat
            else:
                self._sat = Satrec.twoline2rv(l1, l2)
            self._active_tle = (l1, l2)
            self._epoch_version = int(elset.get("epoch_version", 1))
        self._propagate_internal()

    def semi_major_axis_km(self) -> float:
        """Semi-major axis from the active mean elements (Kozai mean motion)."""
        n_rad_s = self._sat.no_kozai / 60.0
        return (_MU_KM3_S2 / (n_rad_s * n_rad_s)) ** (1.0 / 3.0)

    def apply_delta_v(self, dv_eci_km_s, utc: datetime) -> dict:
        """Apply an impulsive delta-V at ``utc`` by re-fitting the TLE.

        Takes the osculating state at ``utc`` from the active elset, adds
        ``dv_eci_km_s`` to the velocity, and runs a 6-parameter SGP4
        differential correction so the new element set reproduces the
        post-burn state vector. Everything downstream (contact windows,
        eclipse scans, live propagation) already goes through ``self._sat``
        and follows the new orbit with no further change.

        Raises :class:`DeltaVFitError` (leaving the elset UNCHANGED) when the
        fit does not converge or the result fails the sanity guards.
        """
        if utc.tzinfo is None:
            utc = utc.replace(tzinfo=timezone.utc)
        jd, fr = jday(utc.year, utc.month, utc.day, utc.hour, utc.minute,
                      utc.second + utc.microsecond * 1e-6)
        e, r, v = self._sat.sgp4(jd, fr)
        if e != 0:
            raise DeltaVFitError(f"SGP4 error {e} at burn epoch")
        a_before = self.semi_major_axis_km()
        r = np.array(r, dtype=float)
        v_new = np.array(v, dtype=float) + np.asarray(dv_eci_km_s, dtype=float)

        sat_new = _fit_satrec(self._sat, jd, fr, r, v_new)

        # Sanity guards — reject the fit, keep the old elset.
        e2, r2, v2 = sat_new.sgp4(jd, fr)
        if e2 != 0:
            raise DeltaVFitError(f"fitted elset does not propagate (e={e2})")
        res_pos_m = float(np.linalg.norm(np.array(r2) - r)) * 1e3
        res_vel_mm_s = float(np.linalg.norm(np.array(v2) - v_new)) * 1e6
        n_rad_s = sat_new.no_kozai / 60.0
        a_after = (_MU_KM3_S2 / (n_rad_s * n_rad_s)) ** (1.0 / 3.0)
        if res_pos_m > 1.0:
            raise DeltaVFitError(
                f"fit residual {res_pos_m:.3f} m exceeds 1 m")
        if abs(a_after - a_before) > 50.0:
            raise DeltaVFitError(
                f"|da| = {abs(a_after - a_before):.1f} km exceeds 50 km guard")
        if sat_new.ecco > 0.02:
            raise DeltaVFitError(
                f"fitted eccentricity {sat_new.ecco:.4f} exceeds 0.02 guard")

        line1, line2 = export_tle(sat_new)
        self._sat = sat_new
        self._active_tle = (line1, line2)
        self._epoch_version += 1
        self._propagate_internal()
        return {
            "a_before_km": a_before,
            "a_after_km": a_after,
            "da_km": a_after - a_before,
            "dv_applied_m_s": float(np.linalg.norm(dv_eci_km_s)) * 1e3,
            "fit_residual_m": res_pos_m,
            "fit_residual_mm_s": res_vel_mm_s,
            "epoch_version": self._epoch_version,
        }

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
        """Predict contact windows over the next duration_s seconds.

        With ``gs`` given, predicts windows for that single station (legacy
        behaviour). With ``gs=None``, predicts windows for ALL configured
        stations, merged and sorted by AOS; each window dict is tagged with
        ``gs_name`` so callers can tell the stations apart. For a
        single-station propagator both forms are identical.
        """
        if gs is not None:
            stations = [gs]
        else:
            stations = self._ground_stations
        if not stations:
            return []

        # One time sweep for all stations: propagate once per step and
        # evaluate every station's elevation against that single ECEF fix.
        windows: list[dict] = []
        t_start = self._sim_utc
        track = {s.name: {"in_pass": False, "aos": None, "max_el": 0.0}
                 for s in stations}

        t = t_start
        while (t - t_start).total_seconds() < duration_s:
            jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                          t.second + t.microsecond * 1e-6)
            e, r, _ = self._sat.sgp4(jd, fr)
            if e == 0:
                r_ecef = _eci_to_ecef(np.array(r), _gmst(jd + fr))
                for s in stations:
                    el, _, _ = _look_angles(r_ecef, s.ecef, s.lat_rad, s.lon_rad)
                    st = track[s.name]
                    if not st["in_pass"] and el >= s.min_elevation_deg:
                        st["in_pass"] = True
                        st["aos"] = t
                        st["max_el"] = el
                    elif st["in_pass"]:
                        if el > st["max_el"]:
                            st["max_el"] = el
                        if el < s.min_elevation_deg:
                            st["in_pass"] = False
                            pass_dur = (t - st["aos"]).total_seconds()
                            windows.append({
                                "aos": st["aos"].isoformat(),
                                "los": t.isoformat(),
                                "gs_name": s.name,
                                "max_elevation_deg": round(st["max_el"], 2),
                                "duration_s": round(pass_dur, 1),
                            })
            t += timedelta(seconds=step_s)
        windows.sort(key=lambda w: w["aos"])
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
        in_eclipse = is_in_eclipse(r, sun_eci, self._earth_r)

        h = np.cross(r, v)
        h_hat = h / (np.linalg.norm(h) + 1e-9)
        sun_hat = sun_eci / (np.linalg.norm(sun_eci) + 1e-9)
        beta_rad = math.asin(np.clip(np.dot(h_hat, sun_hat), -1.0, 1.0))

        # Ground station geometry — evaluate ALL configured stations each
        # step. in_contact is True when ANY station sees the spacecraft above
        # its elevation mask; the single-valued gs_* fields carry the "best"
        # station's geometry (the in-contact station with the highest
        # elevation, or, out of contact, the station closest to visibility).
        el_deg, az_deg, rng_km = -90.0, 0.0, 0.0
        in_contact = False
        active_gs: str | None = None
        gs_elevations: dict[str, float] = {}
        if self._ground_stations:
            r_ecef = _eci_to_ecef(r, gmst)
            best = None  # (in_contact, el, az, rng, name)
            for gs in self._ground_stations:
                g_el, g_az, g_rng = _look_angles(
                    r_ecef, gs.ecef, gs.lat_rad, gs.lon_rad)
                gs_elevations[gs.name] = g_el
                g_contact = g_el >= gs.min_elevation_deg
                if best is None or (g_contact, g_el) > (best[0], best[1]):
                    best = (g_contact, g_el, g_az, g_rng, gs.name)
            in_contact = best[0]
            el_deg, az_deg, rng_km = best[1], best[2], best[3]
            active_gs = best[4] if in_contact else None

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
        s.active_gs = active_gs
        s.gs_elevations_deg = gs_elevations

    def next_eclipse_transition(
        self, duration_s: float = 7200.0, step_s: float = 60.0
    ) -> dict:
        """Forward-scan the orbit for the next eclipse entry/exit.

        Returns time-from-now (seconds) to the next eclipse entry and exit
        relative to the current sim epoch, using the SAME math as the
        propagator (``_sun_eci`` / ``is_in_eclipse`` and ``self._sat.sgp4``).

        Does NOT mutate ``self.state`` or ``self._sim_utc`` — all work is done
        on locals. Coarse/bounded by ``duration_s`` / ``step_s``.
        """
        current = bool(self.state.in_eclipse)
        entry_s: float | None = None
        exit_s: float | None = None
        prev = current
        t0 = self._sim_utc
        n_steps = int(duration_s / step_s) + 1
        for i in range(1, n_steps + 1):
            offset = i * step_s
            t = t0 + timedelta(seconds=offset)
            jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                          t.second + t.microsecond * 1e-6)
            e, r, _ = self._sat.sgp4(jd, fr)
            if e != 0:
                continue
            r = np.array(r)
            sun_eci = _sun_eci(jd + fr)
            ecl = is_in_eclipse(r, sun_eci, self._earth_r)
            if ecl and not prev and entry_s is None:
                entry_s = float(offset)
            if (not ecl) and prev and exit_s is None:
                exit_s = float(offset)
            prev = ecl
            if entry_s is not None and exit_s is not None:
                break
        return {
            "in_eclipse": current,
            "time_to_eclipse_entry_s": entry_s,
            "time_to_eclipse_exit_s": exit_s,
        }

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


# ── SGP4 element fit (Phase D, brief 03) ──────────────────────────────
#
# Writing osculating elements straight into a TLE fails (SGP4 mean elements
# differ from osculating by the short-period J2 terms: 12.5 km at epoch,
# ~335 km after 24 h on this mission's TLE). Instead a 6-parameter
# differential correction fits (n, e, i, RAAN, argp, M) so that
# sgp4(elset, epoch) reproduces the target state vector. Measured on the
# shipped TLE: 4-6 iterations, sub-millimetre residual.

def _rv_to_kepler(r_km: np.ndarray, v_km_s: np.ndarray):
    """Osculating (n[rad/min], e, i, raan, argp, M) from an ECI state."""
    mu = _MU_KM3_S2
    r = np.asarray(r_km, float)
    v = np.asarray(v_km_s, float)
    rmag = float(np.linalg.norm(r))
    vmag = float(np.linalg.norm(v))
    h = np.cross(r, v)
    hmag = float(np.linalg.norm(h))
    n_vec = np.cross([0.0, 0.0, 1.0], h)
    nmag = float(np.linalg.norm(n_vec))
    e_vec = (np.cross(v, h) / mu) - r / rmag
    ecc = float(np.linalg.norm(e_vec))
    energy = vmag * vmag / 2.0 - mu / rmag
    a = -mu / (2.0 * energy)
    incl = math.acos(np.clip(h[2] / hmag, -1.0, 1.0))
    raan = math.atan2(n_vec[1], n_vec[0]) % (2.0 * math.pi)
    if nmag > 1e-12 and ecc > 1e-12:
        argp = math.acos(np.clip(np.dot(n_vec, e_vec) / (nmag * ecc), -1, 1))
        if e_vec[2] < 0.0:
            argp = 2.0 * math.pi - argp
    else:
        argp = 0.0
    if ecc > 1e-12:
        nu = math.acos(np.clip(np.dot(e_vec, r) / (ecc * rmag), -1, 1))
        if np.dot(r, v) < 0.0:
            nu = 2.0 * math.pi - nu
    else:
        nu = math.atan2(np.dot(np.cross(n_vec, r), h) / hmag,
                        np.dot(n_vec, r)) % (2.0 * math.pi)
    # True -> eccentric -> mean anomaly
    E = math.atan2(math.sqrt(max(1.0 - ecc * ecc, 0.0)) * math.sin(nu),
                   ecc + math.cos(nu))
    M = (E - ecc * math.sin(E)) % (2.0 * math.pi)
    n_rad_min = math.sqrt(mu / a ** 3) * 60.0
    return np.array([n_rad_min, ecc, incl, raan, argp, M])


def _satrec_from_params(template: Satrec, epoch_1949: float,
                        p: np.ndarray) -> Satrec:
    """Build a Satrec from fit parameters, inheriting identity/drag from
    ``template`` so the exported TLE stays attributable."""
    two_pi = 2.0 * math.pi
    sat = Satrec()
    sat.sgp4init(
        WGS72, 'i', template.satnum, epoch_1949,
        template.bstar, template.ndot, template.nddot,
        min(max(float(p[1]), 1e-7), 0.999),      # ecco
        float(p[4]) % two_pi,                     # argpo
        min(max(float(p[2]), 0.0), math.pi),      # inclo
        float(p[5]) % two_pi,                     # mo
        max(float(p[0]), 1e-6),                   # no_kozai [rad/min]
        float(p[3]) % two_pi,                     # nodeo
    )
    sat.classification = getattr(template, 'classification', 'U') or 'U'
    sat.intldesg = getattr(template, 'intldesg', '') or ''
    sat.ephtype = getattr(template, 'ephtype', 0)
    sat.elnum = getattr(template, 'elnum', 0)
    sat.revnum = getattr(template, 'revnum', 0)
    return sat


def _fit_satrec(template: Satrec, jd: float, fr: float,
                r_target: np.ndarray, v_target: np.ndarray,
                max_iter: int = 10) -> Satrec:
    """SGP4 differential correction: fit a TLE to an epoch state vector.

    Seeds with the osculating elements and iterates
    ``p <- p + J^+ . (target - sgp4(p))`` with a numerically differenced
    Jacobian. Raises :class:`DeltaVFitError` on non-convergence.
    """
    epoch_1949 = (jd - 2433281.5) + fr
    target = np.concatenate([np.asarray(r_target, float),
                             np.asarray(v_target, float)])
    k0 = _rv_to_kepler(r_target, v_target)
    # Equinoctial-style fit parameters — for a near-circular orbit the
    # classic (e, argp, M) triplet is degenerate (the first Newton step
    # drives e negative and the fit stalls on the e >= 0 boundary):
    #   q = [n, e cos(argp), e sin(argp), i, raan, L = argp + M]
    p = np.array([k0[0], k0[1] * math.cos(k0[4]), k0[1] * math.sin(k0[4]),
                  k0[2], k0[3], (k0[4] + k0[5]) % (2.0 * math.pi)])

    def to_kepler(q: np.ndarray) -> np.ndarray:
        ecc = math.hypot(q[1], q[2])
        argp = math.atan2(q[2], q[1]) if ecc > 1e-9 else 0.0
        return np.array([q[0], ecc, q[3], q[4], argp, q[5] - argp])

    def residual(params: np.ndarray) -> np.ndarray:
        sat = _satrec_from_params(template, epoch_1949, to_kepler(params))
        e, r, v = sat.sgp4(jd, fr)
        if e != 0:
            raise DeltaVFitError(f"SGP4 error {e} during fit")
        return target - np.concatenate([r, v])

    steps = np.array([1e-7, 1e-7, 1e-7, 1e-7, 1e-7, 1e-6])
    converged = False
    for _ in range(max_iter):
        res = residual(p)
        # Convergence: position residual < 1e-6 km (1 mm) and velocity
        # residual < 1e-9 km/s.
        if (np.linalg.norm(res[:3]) < 1e-6
                and np.linalg.norm(res[3:]) < 1e-9):
            converged = True
            break
        # J[:, k] = d(sgp4 state)/d(p_k), differenced through the residual:
        # residual = target - sgp4(p), so d(state)/dp = -d(residual)/dp.
        J = np.zeros((6, 6))
        for k in range(6):
            dp = np.array(p)
            dp[k] += steps[k]
            J[:, k] = (res - residual(dp)) / steps[k]
        # Want sgp4(p + delta) ~= target  =>  J . delta = residual(p).
        try:
            delta, *_ = np.linalg.lstsq(J, res, rcond=None)
        except np.linalg.LinAlgError as exc:
            raise DeltaVFitError(f"fit Jacobian singular: {exc}") from exc
        p = p + delta
    if not converged:
        res = residual(p)
        if not (np.linalg.norm(res[:3]) < 1e-6
                and np.linalg.norm(res[3:]) < 1e-9):
            raise DeltaVFitError(
                f"element fit did not converge in {max_iter} iterations "
                f"(pos residual {np.linalg.norm(res[:3]) * 1e3:.3g} m)")
    return _satrec_from_params(template, epoch_1949, to_kepler(p))


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


# NOTE (Phase B, brief 01 F24 / WP-14): the private duplicate `_is_eclipse`
# is gone — smo_common.orbit.eclipse.is_in_eclipse is the single eclipse
# model for the whole suite (its eclipse_fraction adds penumbra when needed).


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

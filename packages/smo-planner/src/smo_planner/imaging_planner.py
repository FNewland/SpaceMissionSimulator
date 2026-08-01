"""SMO Planner — Imaging Target Planner.

Computes imaging opportunities for ocean-current monitoring targets, decides
where inside a target region the payload can actually be aimed, and generates
capture command sequences.

Geometry and constraints (defect #61)
-------------------------------------
Previously an opportunity was "the sub-satellite point fell inside the target
box expanded by half a swath", the capture was always commanded at the box
CENTRE (up to >1000 km off-swath for a 2200x2800 km region), and the only check
applied was a binary eclipse flag — ``min_solar_elevation_deg`` and
``revisit_days`` were loaded from the config and never used.

Now:

* the **access point** is the point of the target region closest to the ground
  track (the swath-crossing point), recomputed at every track sample; the
  capture is aimed there and centred on **closest approach**;
* **off-nadir feasibility** is enforced: the roll needed to reach the access
  point must be within ``max_off_nadir_deg`` (equivalently the access point
  must lie within half a swath cross-track). Unreachable opportunities are
  reported as infeasible and are not offered;
* **solar elevation** is computed properly (sun elevation at the access point,
  from the subsolar point) and the per-target ``min_solar_elevation_deg`` is
  enforced — eclipse alone is a far weaker condition;
* **revisit_days** is enforced per target, both within the computed horizon and
  against captures already scheduled.

Camera/orbit parameters come from ``planning/planner.yaml`` (``imaging:``) and
``orbit.yaml`` rather than module constants.

Out of scope / forward work: a map-drawing UI for operator-defined regions
(the API already accepts explicit ``lat``/``lon`` on POST /api/imaging/schedule,
so a "capture here" map click is a front-end-only addition), a cloud
climatology layer, and a slew-time/momentum model feeding the AOCS checker.
"""
from __future__ import annotations

import math
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from smo_planner.utils import parse_iso as _parse_iso

logger = logging.getLogger(__name__)

# Camera/orbit fallbacks — overridden from planner.yaml / orbit.yaml.
ALTITUDE_KM = 450.0
FOV_DEG = 15.0
SWATH_KM = 2.0 * ALTITUDE_KM * math.tan(math.radians(FOV_DEG / 2.0))  # ~118 km
MIN_SOLAR_ELEVATION_DEG = 10.0
IMAGING_DURATION_S = 120  # seconds per imaging pass
IMAGING_POWER_W = 60
IMAGING_DATA_MB = 4    # fallback only; payload.yaml image_size_mb (defect #82)

# Approximate Earth radius for distance calculations
EARTH_RADIUS_KM = 6371.0


# ── Solar geometry ──────────────────────────────────────────────────

def subsolar_point(when: datetime) -> tuple[float, float]:
    """Return (lat_deg, lon_deg) of the subsolar point at *when* (UTC).

    Low-precision almanac formulation (accurate to ~0.01 deg in declination),
    the same model the planner UI uses to draw the terminator.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    jd = when.timestamp() / 86400.0 + 2440587.5
    n = jd - 2451545.0
    L0 = (280.460 + 0.9856474 * n) % 360.0
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)
    lam = math.radians(L0 + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    decl = math.asin(math.sin(eps) * math.sin(lam))
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    eq_time_min = (math.radians(L0) - ra) * 720.0 / math.pi
    utc_hours = when.hour + when.minute / 60.0 + when.second / 3600.0
    sub_lon = (12.0 - utc_hours - eq_time_min / 60.0) * 15.0
    sub_lon = ((sub_lon + 180.0) % 360.0 + 360.0) % 360.0 - 180.0
    return math.degrees(decl), sub_lon


def solar_elevation_deg(lat_deg: float, lon_deg: float, when: datetime) -> float:
    """Solar elevation at a ground point, in degrees (negative = below horizon)."""
    s_lat, s_lon = subsolar_point(when)
    phi = math.radians(lat_deg)
    s_phi = math.radians(s_lat)
    dlon = math.radians(lon_deg - s_lon)
    cos_zenith = (math.sin(phi) * math.sin(s_phi)
                  + math.cos(phi) * math.cos(s_phi) * math.cos(dlon))
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    return 90.0 - math.degrees(math.acos(cos_zenith))


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(_wrap_lon(lon2 - lon1))
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _wrap_lon(dlon: float) -> float:
    """Wrap a longitude difference into [-180, 180]."""
    return (dlon + 180.0) % 360.0 - 180.0


class ImagingTarget:
    """An imaging target defined by a lat/lon bounding box."""

    def __init__(self, target_cfg: dict):
        self.id = target_cfg.get("id", "unknown")
        self.name = target_cfg.get("name", "unnamed")
        self.description = target_cfg.get("description", "")
        self.priority = target_cfg.get("priority", "medium")
        self.revisit_days = target_cfg.get("revisit_days", 7)
        self.min_solar_elevation_deg = target_cfg.get(
            "min_solar_elevation_deg", MIN_SOLAR_ELEVATION_DEG
        )
        # Optional per-target override of the platform roll limit (config
        # extension; absent = use the planner-wide imaging.max_off_nadir_deg).
        self.max_off_nadir_deg = target_cfg.get("max_off_nadir_deg")

        region = target_cfg.get("region", {})
        self.min_lat = region.get("min_lat", -90.0)
        self.max_lat = region.get("max_lat", 90.0)
        self.min_lon = region.get("min_lon", -180.0)
        self.max_lon = region.get("max_lon", 180.0)

        # Center for reference
        self.center_lat = (self.min_lat + self.max_lat) / 2.0
        self.center_lon = (self.min_lon + self.max_lon) / 2.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "priority": self.priority,
            "revisit_days": self.revisit_days,
            "min_solar_elevation_deg": self.min_solar_elevation_deg,
            "max_off_nadir_deg": self.max_off_nadir_deg,
            "region": {
                "min_lat": self.min_lat,
                "max_lat": self.max_lat,
                "min_lon": self.min_lon,
                "max_lon": self.max_lon,
            },
            "center_lat": self.center_lat,
            "center_lon": self.center_lon,
        }

    def contains_point(self, lat: float, lon: float) -> bool:
        """Check if a point falls within the target bounding box."""
        return (
            self.min_lat <= lat <= self.max_lat
            and self.min_lon <= lon <= self.max_lon
        )

    def access_point(self, lat: float, lon: float) -> tuple[float, float]:
        """Point of the region closest to a given sub-satellite point.

        This is the point the payload should actually be aimed at: the
        swath-crossing point of the region, i.e. the sub-satellite position
        clamped into the bounding box. When the ground track passes through the
        region the access point IS the sub-satellite point (nadir capture);
        when it grazes the edge, it is the nearest edge/corner point.
        """
        acc_lat = min(max(lat, self.min_lat), self.max_lat)
        # Longitude is clamped on the wrapped difference so an antimeridian
        # -spanning box (min_lon > max_lon) behaves sensibly.
        if self.min_lon <= self.max_lon:
            acc_lon = min(max(lon, self.min_lon), self.max_lon)
        else:  # region wraps the antimeridian
            if lon >= self.min_lon or lon <= self.max_lon:
                acc_lon = lon
            else:
                d_min = abs(_wrap_lon(lon - self.min_lon))
                d_max = abs(_wrap_lon(lon - self.max_lon))
                acc_lon = self.min_lon if d_min <= d_max else self.max_lon
        return acc_lat, acc_lon

    def cross_track_km(self, lat: float, lon: float) -> float:
        """Ground distance from a sub-satellite point to the region."""
        acc_lat, acc_lon = self.access_point(lat, lon)
        return great_circle_km(lat, lon, acc_lat, acc_lon)

    def within_swath(self, lat: float, lon: float, swath_km: float = SWATH_KM) -> bool:
        """True if the region is reachable within half a swath of nadir.

        Retained (and now geometric rather than a degrees-box expansion): the
        real question is whether the closest point of the region lies within
        half the swath width cross-track.
        """
        return self.cross_track_km(lat, lon) <= (swath_km / 2.0)


class ImagingPlanner:
    """Plans imaging activities for ocean current monitoring targets."""

    def __init__(
        self,
        targets: list[dict] | None = None,
        altitude_km: float = ALTITUDE_KM,
        fov_deg: float = FOV_DEG,
        swath_km: float | None = None,
        max_off_nadir_deg: float | None = None,
        capture_duration_s: float = IMAGING_DURATION_S,
        capture_power_w: float = IMAGING_POWER_W,
        image_size_mb: float = IMAGING_DATA_MB,
        enforce_solar_elevation: bool = True,
        enforce_revisit: bool = True,
        default_min_solar_elevation_deg: float = MIN_SOLAR_ELEVATION_DEG,
    ):
        self._targets: list[ImagingTarget] = []
        if targets:
            for t in targets:
                self._targets.append(ImagingTarget(t))
        self.altitude_km = altitude_km
        self.fov_deg = fov_deg
        self.swath_km = (
            swath_km if swath_km is not None
            else 2.0 * altitude_km * math.tan(math.radians(fov_deg / 2.0))
        )
        # Half the FOV is the roll needed to reach the swath edge; a larger
        # limit means the platform may roll off nadir to reach the region.
        self.max_off_nadir_deg = (
            max_off_nadir_deg if max_off_nadir_deg is not None else fov_deg / 2.0
        )
        self.capture_duration_s = capture_duration_s
        self.capture_power_w = capture_power_w
        self.image_size_mb = image_size_mb
        self.enforce_solar_elevation = enforce_solar_elevation
        self.enforce_revisit = enforce_revisit
        self.default_min_solar_elevation_deg = default_min_solar_elevation_deg
        # target_id -> last capture time already committed to the plan
        self._last_scheduled: dict[str, datetime] = {}
        # (target_id, start_utc) -> opportunity, so a scheduling request can
        # recover the computed access point when the caller sends none.
        self._opportunity_index: dict[tuple[str, str], dict] = {}
        # ── Phase E WP-1: ground-assigned product ids ──
        # The planner chooses the scene_id at plan time (real missions
        # pre-assign product ids so the downlink can be booked in the same
        # plan). Ids start above the spacecraft's auto-assign counter seed
        # (payload_basic._next_scene_id = 100) to keep the two id spaces
        # visibly distinct. Deterministic per planner instance.
        self._next_plan_scene_id = 1000
        self._scene_id_index: dict[tuple[str, str], int] = {}
        # Along-track lines per capture: the 2048-line reference frame
        # (4.0 MB at 4 bands, subsystems/payload.yaml image_size_mb).
        self.capture_lines = 2048

    def _assign_scene_id(self, target_id: str, start_time: str) -> int:
        """Ground-assigned scene id, stable for a (target, time) request."""
        key = (target_id, str(start_time))
        if key not in self._scene_id_index:
            self._next_plan_scene_id += 1
            self._scene_id_index[key] = self._next_plan_scene_id
        return self._scene_id_index[key]

    @classmethod
    def from_config(
        cls,
        targets_path: Path | None = None,
        imaging_cfg: Any | None = None,
        altitude_km: float = ALTITUDE_KM,
        image_size_mb: float | None = None,
    ) -> "ImagingPlanner":
        """Build from ``planner.yaml`` imaging settings + orbit altitude."""
        kwargs: dict[str, Any] = {"altitude_km": altitude_km}
        if imaging_cfg is not None:
            kwargs.update(
                fov_deg=imaging_cfg.fov_deg,
                swath_km=imaging_cfg.swath_km,
                max_off_nadir_deg=imaging_cfg.max_off_nadir_deg,
                capture_duration_s=imaging_cfg.capture_duration_s,
                capture_power_w=imaging_cfg.capture_power_w,
                enforce_solar_elevation=imaging_cfg.enforce_solar_elevation,
                enforce_revisit=imaging_cfg.enforce_revisit,
                default_min_solar_elevation_deg=(
                    imaging_cfg.default_min_solar_elevation_deg),
            )
        if image_size_mb is not None:
            kwargs["image_size_mb"] = image_size_mb
        planner = cls(**kwargs)
        if targets_path is not None and Path(targets_path).exists():
            planner.load_targets_from_config(Path(targets_path))
        return planner

    def load_targets_from_config(self, config_path: Path) -> None:
        """Load imaging targets from a YAML config file."""
        try:
            import yaml
            with open(config_path) as f:
                data = yaml.safe_load(f)
            target_list = data.get("imaging_targets", [])
            self._targets = [ImagingTarget(t) for t in target_list]
            logger.info("Loaded %d imaging targets from %s",
                        len(self._targets), config_path)
        except Exception as e:
            logger.error("Failed to load imaging targets: %s", e)

    def get_targets(self) -> list[dict]:
        """Return all configured imaging targets as dicts."""
        return [t.to_dict() for t in self._targets]

    def note_scheduled(self, target_id: str, when: datetime | str) -> None:
        """Record a committed capture so ``revisit_days`` can be enforced."""
        t = _parse_iso(when) if isinstance(when, str) else when
        if t is None:
            return
        prev = self._last_scheduled.get(target_id)
        if prev is None or t > prev:
            self._last_scheduled[target_id] = t

    def _max_off_nadir_for(self, target: ImagingTarget) -> float:
        return (target.max_off_nadir_deg
                if target.max_off_nadir_deg is not None
                else self.max_off_nadir_deg)

    def _off_nadir_deg(self, cross_track_km: float, alt_km: float) -> float:
        """Roll angle needed to see a point *cross_track_km* off the track.

        Flat-Earth small-angle form (atan(d/h)); at 450 km altitude and the
        ~60 km half-swath this is within 0.1 deg of the spherical solution.
        """
        if alt_km <= 0:
            return 90.0
        return math.degrees(math.atan2(cross_track_km, alt_km))

    # ── Opportunity computation ─────────────────────────────────

    def compute_opportunities(
        self,
        ground_track: list[dict],
        duration_hours: float = 24.0,
        include_infeasible: bool = False,
    ) -> list[dict]:
        """Compute imaging opportunities from a ground track.

        An opportunity is a contiguous run of track samples during which the
        target region is reachable (within the off-nadir limit) AND the sun is
        high enough over the access point. Each opportunity reports its closest
        approach — the access point, time, cross-track distance, off-nadir angle
        and solar elevation at that instant — which is what the capture is
        aimed at and centred on.

        Args:
            ground_track: dicts with utc, lat, lon, alt_km, in_eclipse
            duration_hours: planning horizon (for reference)
            include_infeasible: also return runs rejected by the constraints,
                flagged ``feasible: False`` with a ``reject_reason``

        Returns:
            list of opportunity dicts, sorted by start time
        """
        if not self._targets or not ground_track:
            return []

        opportunities: list[dict] = []
        rejected: list[dict] = []
        active: dict[str, dict | None] = {t.id: None for t in self._targets}

        def _close(target_id: str) -> None:
            opp = active.get(target_id)
            if opp is None:
                return
            active[target_id] = None
            self._finalise(opp)
            (opportunities if opp["feasible"] else rejected).append(opp)

        for point in ground_track:
            lat = point.get("lat", 0.0)
            lon = point.get("lon", 0.0)
            utc_str = point.get("utc", "")
            in_eclipse = point.get("in_eclipse", False)
            alt_km = point.get("alt_km", self.altitude_km)
            when = _parse_iso(utc_str)

            for target in self._targets:
                acc_lat, acc_lon = target.access_point(lat, lon)
                cross_km = great_circle_km(lat, lon, acc_lat, acc_lon)
                off_nadir = self._off_nadir_deg(cross_km, alt_km)
                reachable = off_nadir <= self._max_off_nadir_for(target)

                sun_el = (solar_elevation_deg(acc_lat, acc_lon, when)
                          if when is not None else -90.0)
                min_sun = (target.min_solar_elevation_deg
                           if target.min_solar_elevation_deg is not None
                           else self.default_min_solar_elevation_deg)
                if self.enforce_solar_elevation:
                    lit = (not in_eclipse) and sun_el >= min_sun
                    reason = ("eclipse" if in_eclipse
                              else f"solar elevation {sun_el:.1f} deg < required {min_sun:.1f} deg")
                else:
                    lit = not in_eclipse
                    reason = "eclipse"

                if not reachable:
                    if active[target.id] is not None:
                        _close(target.id)
                    continue
                if not lit:
                    if active[target.id] is not None:
                        _close(target.id)
                    elif include_infeasible:
                        rejected.append(self._make_opp(
                            target, utc_str, lat, lon, acc_lat, acc_lon,
                            cross_km, off_nadir, sun_el, alt_km,
                            feasible=False, reject_reason=reason))
                    continue

                opp = active[target.id]
                if opp is None:
                    opp = self._make_opp(
                        target, utc_str, lat, lon, acc_lat, acc_lon,
                        cross_km, off_nadir, sun_el, alt_km)
                    active[target.id] = opp
                else:
                    opp["end_utc"] = utc_str
                    if cross_km < opp["cross_track_km"]:
                        opp.update({
                            "cross_track_km": round(cross_km, 1),
                            "off_nadir_deg": round(off_nadir, 2),
                            "closest_approach_utc": utc_str,
                            "access_lat": round(acc_lat, 4),
                            "access_lon": round(acc_lon, 4),
                            "sub_sat_lat": round(lat, 4),
                            "sub_sat_lon": round(lon, 4),
                            "solar_elevation_deg": round(sun_el, 1),
                        })

        for tid in list(active):
            _close(tid)

        opportunities.sort(key=lambda o: o.get("start_utc", ""))
        rejected.sort(key=lambda o: o.get("start_utc", ""))

        if self.enforce_revisit:
            opportunities, revisit_rejects = self._apply_revisit(opportunities)
            rejected.extend(revisit_rejects)

        self._opportunity_index = {
            (o["target_id"], o["start_utc"]): o for o in opportunities
        }
        # Also index by closest-approach time: the UI schedules at the
        # capture time, not necessarily the window start.
        for o in opportunities:
            self._opportunity_index[(o["target_id"], o["capture_utc"])] = o

        if include_infeasible:
            merged = opportunities + rejected
            merged.sort(key=lambda o: o.get("start_utc", ""))
            return merged
        return opportunities

    def _make_opp(self, target, utc_str, lat, lon, acc_lat, acc_lon,
                  cross_km, off_nadir, sun_el, alt_km,
                  feasible: bool = True, reject_reason: str | None = None) -> dict:
        return {
            "target_id": target.id,
            "target_name": target.name,
            "priority": target.priority,
            "start_utc": utc_str,
            "end_utc": utc_str,
            "closest_approach_utc": utc_str,
            "sub_sat_lat": round(lat, 4),
            "sub_sat_lon": round(lon, 4),
            "access_lat": round(acc_lat, 4),
            "access_lon": round(acc_lon, 4),
            "cross_track_km": round(cross_km, 1),
            "off_nadir_deg": round(off_nadir, 2),
            "max_off_nadir_deg": self._max_off_nadir_for(target),
            "solar_elevation_deg": round(sun_el, 1),
            "min_solar_elevation_deg": target.min_solar_elevation_deg,
            "revisit_days": target.revisit_days,
            "alt_km": alt_km,
            "center_lat": target.center_lat,
            "center_lon": target.center_lon,
            "swath_km": round(self.swath_km, 1),
            "feasible": feasible,
            "reject_reason": reject_reason,
        }

    def _finalise(self, opp: dict) -> None:
        """Fill duration and the capture window centred on closest approach."""
        start = _parse_iso(opp.get("start_utc", ""))
        end = _parse_iso(opp.get("end_utc", ""))
        opp["duration_s"] = round((end - start).total_seconds(), 1) if start and end else 0.0

        closest = _parse_iso(opp.get("closest_approach_utc", "")) or start
        if closest is not None:
            half = timedelta(seconds=self.capture_duration_s / 2.0)
            capture_start = closest - half
            # Keep the capture inside the access window when it is long enough;
            # otherwise start at closest approach and flag the shortfall.
            if start is not None and capture_start < start:
                capture_start = start
            if end is not None and start is not None:
                if (end - start).total_seconds() < self.capture_duration_s:
                    opp["capture_exceeds_window"] = True
            opp["capture_utc"] = capture_start.isoformat()
        else:
            opp["capture_utc"] = opp.get("start_utc", "")
        opp.setdefault("capture_exceeds_window", False)

    def _apply_revisit(self, opportunities: list[dict]) -> tuple[list[dict], list[dict]]:
        """Drop opportunities that violate a target's revisit interval."""
        kept: list[dict] = []
        dropped: list[dict] = []
        last: dict[str, datetime] = dict(self._last_scheduled)
        for opp in opportunities:
            tid = opp["target_id"]
            when = _parse_iso(opp.get("capture_utc") or opp.get("start_utc", ""))
            revisit_days = opp.get("revisit_days") or 0
            if when is None or not revisit_days:
                kept.append(opp)
                continue
            prev = last.get(tid)
            if prev is not None and (when - prev) < timedelta(days=revisit_days):
                gap_h = (when - prev).total_seconds() / 3600.0
                opp["feasible"] = False
                opp["reject_reason"] = (
                    f"revisit: only {gap_h:.1f} h since the previous "
                    f"{tid} capture ({revisit_days} day interval required)")
                dropped.append(opp)
                continue
            last[tid] = when
            kept.append(opp)
        return kept, dropped

    # ── Capture generation ──────────────────────────────────────

    def resolve_capture_point(
        self, target_id: str, start_time: str
    ) -> tuple[float, float] | None:
        """Access point computed for a previously offered opportunity."""
        opp = self._opportunity_index.get((target_id, start_time))
        if opp is None:
            return None
        return opp.get("access_lat"), opp.get("access_lon")

    def generate_capture_sequence(
        self,
        target_id: str,
        start_time: str,
        lat: float | None = None,
        lon: float | None = None,
    ) -> dict:
        """Generate a capture command sequence for a target.

        The aim point is, in order of precedence: an explicit ``lat``/``lon``
        from the caller; the access point computed for the matching
        opportunity; the region centre as a last resort (flagged in the
        returned activity so the operator can see the capture is not tied to a
        computed access).

        Returns an activity dict suitable for the ActivityScheduler.
        """
        target = None
        for t in self._targets:
            if t.id == target_id:
                target = t
                break

        if target is None:
            raise ValueError(f"Unknown target ID: {target_id}")

        aim_source = "explicit"
        if lat is None or lon is None:
            resolved = self.resolve_capture_point(target_id, start_time)
            if resolved and resolved[0] is not None:
                lat, lon = resolved
                aim_source = "access_point"
            else:
                lat, lon = target.center_lat, target.center_lon
                aim_source = "region_centre"
                logger.warning(
                    "Imaging capture for %s at %s has no computed access point "
                    "— falling back to the region centre (%.3f, %.3f). The "
                    "capture may be off-swath.",
                    target_id, start_time, lat, lon)

        capture_lat = float(lat)
        capture_lon = float(lon)

        if not target.contains_point(capture_lat, capture_lon):
            logger.warning(
                "Capture point (%.3f, %.3f) is outside target %s region",
                capture_lat, capture_lon, target_id)

        # Phase E WP-1: PAYLOAD_CAPTURE takes `scene_id uint16 + lines
        # uint16` (DECISIONS.md section 9 — the catalogue/textbook form won).
        # The planner assigns the ground-side product id at PLAN time, which
        # is what lets the same plan pre-book the downlink
        # (PAYLOAD_DOWNLOAD_IMAGE addresses the id it chose here). The WHERE
        # is the WHEN: this sequence is scheduled via S11 at the computed
        # overpass, and the spacecraft stamps the actual sub-satellite
        # lat/lon into the catalogue entry at shutter open. The aim point
        # below is retained as plan METADATA (display, access checking) —
        # it is no longer a command argument.
        scene_id = self._assign_scene_id(target_id, start_time)
        command_sequence = [
            {
                "service": 8,
                "subtype": 1,
                "func_id": 0x1A,
                "args": {"mode": 2},  # mode 2 = IMAGING
                "description": "PAYLOAD_SET_MODE_IMAGING",
            },
            {"wait_s": 5},
            {
                "wait_for": {
                    "parameter": "payload.mode",
                    "value": 2,
                    "timeout_s": 30,
                },
            },
            {
                "service": 8,
                "subtype": 1,
                "func_id": 0x1C,
                "args": {"scene_id": scene_id, "lines": self.capture_lines},
                "description": (f"PAYLOAD_CAPTURE scene={scene_id} "
                                f"lines={self.capture_lines} "
                                f"(aim {capture_lat:.2f}, {capture_lon:.2f})"),
            },
        ]

        opp = self._opportunity_index.get((target_id, start_time))
        return {
            "name": f"imaging_{target_id}",
            "scene_id": scene_id,
            "lines": self.capture_lines,
            "start_time": start_time,
            "duration_s": self.capture_duration_s,
            "power_w": self.capture_power_w,
            "data_volume_mb": self.image_size_mb,
            "priority": target.priority,
            "procedure_ref": "NOM-001",
            "command_sequence": command_sequence,
            "target_id": target_id,
            "target_name": target.name,
            "capture_lat": capture_lat,
            "capture_lon": capture_lon,
            "aim_source": aim_source,
            "off_nadir_deg": opp.get("off_nadir_deg") if opp else None,
            "solar_elevation_deg": opp.get("solar_elevation_deg") if opp else None,
        }


# _parse_iso imported from smo_planner.utils

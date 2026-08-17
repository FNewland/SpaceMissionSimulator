"""Tests for imaging opportunity geometry and constraints (defect #61).

Covers:
  - the capture is aimed at the ACCESS POINT (closest point of the region to
    the ground track), not the bounding-box centre which can be >1000 km
    off-swath for a large region
  - off-nadir / swath feasibility: an unreachable region is not offered
  - min_solar_elevation_deg is ENFORCED (it used to be loaded and ignored, with
    a binary eclipse flag standing in for it)
  - revisit_days is ENFORCED, within the horizon and against scheduled captures
  - the capture is centred on closest approach
  - solar elevation helper agrees with known geometry
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smo_planner.imaging_planner import (
    ImagingPlanner, ImagingTarget, solar_elevation_deg, subsolar_point,
    great_circle_km,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"

# Gulf Stream: 25-45 N, 80-50 W — ~2200 x 2800 km, i.e. the case where the
# box centre is nowhere near the ground track.
GULF_STREAM = {
    "id": "OCM-001", "name": "Gulf Stream", "priority": "high",
    "revisit_days": 3, "min_solar_elevation_deg": 10.0,
    "region": {"min_lat": 25.0, "max_lat": 45.0,
               "min_lon": -80.0, "max_lon": -50.0},
}
NOON_UTC = datetime(2026, 6, 21, 16, 0, 0, tzinfo=timezone.utc)  # ~11:40 local at 65W


def _track(points, start=NOON_UTC, step_s=60):
    return [
        {"utc": (start + timedelta(seconds=i * step_s)).isoformat(),
         "lat": lat, "lon": lon, "alt_km": 450.0, "in_eclipse": False}
        for i, (lat, lon) in enumerate(points)
    ]


# ── Solar geometry ─────────────────────────────────────────────────

class TestSolarGeometry:

    def test_subsolar_point_at_june_solstice(self):
        lat, lon = subsolar_point(datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc))
        assert lat == pytest.approx(23.4, abs=0.5)      # tropic of Cancer
        assert abs(lon) < 5.0                            # near the prime meridian

    def test_sun_is_overhead_at_the_subsolar_point(self):
        when = datetime(2026, 3, 20, 9, 0, 0, tzinfo=timezone.utc)
        lat, lon = subsolar_point(when)
        assert solar_elevation_deg(lat, lon, when) == pytest.approx(90.0, abs=0.5)

    def test_sun_is_below_the_horizon_on_the_night_side(self):
        when = datetime(2026, 3, 20, 9, 0, 0, tzinfo=timezone.utc)
        lat, lon = subsolar_point(when)
        anti_lon = lon + 180.0
        if anti_lon > 180:
            anti_lon -= 360.0
        assert solar_elevation_deg(-lat, anti_lon, when) < -80.0

    def test_the_old_sample_epoch_was_actually_before_local_sunrise(self):
        """10:00Z over the Gulf Stream is ~05:40 local solar time — the case
        the eclipse-only check used to accept."""
        when = datetime(2026, 3, 11, 10, 0, 0, tzinfo=timezone.utc)
        assert solar_elevation_deg(35.0, -65.0, when) < 10.0


# ── Access point ───────────────────────────────────────────────────

class TestAccessPoint:

    def test_access_point_is_the_sub_satellite_point_inside_the_region(self):
        t = ImagingTarget(GULF_STREAM)
        assert t.access_point(30.0, -70.0) == (30.0, -70.0)

    def test_access_point_is_clamped_to_the_region_edge_outside_it(self):
        t = ImagingTarget(GULF_STREAM)
        lat, lon = t.access_point(50.0, -70.0)
        assert lat == 45.0 and lon == -70.0

    def test_capture_targets_the_access_point_not_the_box_centre(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        # Track crosses the region near its south-west corner.
        track = _track([(26.0, -78.0), (26.5, -77.0), (27.0, -76.0), (60.0, -20.0)])
        opps = planner.compute_opportunities(track)
        assert opps, "expected an opportunity"
        opp = opps[0]

        capture = planner.generate_capture_sequence(
            opp["target_id"], opp["capture_utc"])
        centre_dist = great_circle_km(
            capture["capture_lat"], capture["capture_lon"],
            opp["center_lat"], opp["center_lon"])
        access_dist = great_circle_km(
            capture["capture_lat"], capture["capture_lon"],
            opp["access_lat"], opp["access_lon"])
        assert capture["aim_source"] == "access_point"
        assert access_dist < 1.0
        # The box centre really is far away — this is the bug being fixed.
        assert centre_dist > 500.0

    def test_capture_point_is_inside_the_region(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        track = _track([(30.0, -70.0), (31.0, -69.0), (60.0, -20.0)])
        opp = planner.compute_opportunities(track)[0]
        capture = planner.generate_capture_sequence(
            opp["target_id"], opp["capture_utc"])
        target = ImagingTarget(GULF_STREAM)
        assert target.contains_point(
            capture["capture_lat"], capture["capture_lon"])

    def test_explicit_lat_lon_still_wins(self):
        planner = ImagingPlanner([GULF_STREAM])
        capture = planner.generate_capture_sequence(
            "OCM-001", NOON_UTC.isoformat(), lat=33.0, lon=-60.0)
        assert (capture["capture_lat"], capture["capture_lon"]) == (33.0, -60.0)
        assert capture["aim_source"] == "explicit"

    def test_capture_is_centred_on_closest_approach(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0,
                                 capture_duration_s=120.0)
        # Closest approach at the middle sample.
        track = _track([(45.4, -70.0), (44.0, -70.0), (45.4, -70.0), (60.0, -20.0)])
        opp = planner.compute_opportunities(track)[0]
        closest = datetime.fromisoformat(opp["closest_approach_utc"])
        capture = datetime.fromisoformat(opp["capture_utc"])
        assert capture <= closest
        assert (closest - capture) <= timedelta(seconds=60)


# ── Off-nadir feasibility ──────────────────────────────────────────

class TestOffNadirFeasibility:

    def test_unreachable_region_is_not_offered(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        # 5 degrees of latitude north of the region = ~555 km cross-track.
        track = _track([(50.0, -65.0), (50.5, -64.0), (51.0, -63.0)])
        assert planner.compute_opportunities(track) == []

    def test_grazing_pass_within_half_a_swath_is_offered(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        # ~0.4 deg north of max_lat = ~44 km, inside the 120 km half swath
        # (240 km swath, owner ruling 2026-08).
        track = _track([(45.4, -65.0), (45.4, -64.0), (45.4, -63.0)])
        opps = planner.compute_opportunities(track)
        assert opps
        assert opps[0]["off_nadir_deg"] <= opps[0]["max_off_nadir_deg"]
        assert opps[0]["cross_track_km"] <= planner.swath_km / 2.0 + 1.0

    def test_off_nadir_is_reported_and_bounded(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        track = _track([(30.0, -70.0), (31.0, -69.0)])
        opp = planner.compute_opportunities(track)[0]
        assert opp["off_nadir_deg"] == pytest.approx(0.0, abs=0.01)
        assert opp["swath_km"] == pytest.approx(planner.swath_km, abs=0.1)

    def test_infeasible_windows_can_be_inspected_with_a_reason(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        night = [
            {"utc": (datetime(2026, 3, 11, 4, 0, 0, tzinfo=timezone.utc)
                     + timedelta(minutes=i)).isoformat(),
             "lat": 30.0, "lon": -70.0, "alt_km": 450.0, "in_eclipse": False}
            for i in range(3)
        ]
        offered = planner.compute_opportunities(night)
        assert offered == []
        with_rejects = planner.compute_opportunities(night, include_infeasible=True)
        assert with_rejects
        assert all(not o["feasible"] for o in with_rejects)
        assert any("solar elevation" in (o["reject_reason"] or "")
                   for o in with_rejects)


# ── Solar elevation constraint ─────────────────────────────────────

class TestSolarElevationConstraint:

    def test_low_sun_blocks_an_otherwise_reachable_target(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        # 09:00Z at 65W = ~04:40 local solar time: reachable, but dark.
        dark = datetime(2026, 3, 11, 9, 0, 0, tzinfo=timezone.utc)
        track = _track([(30.0, -65.0), (31.0, -64.0)], start=dark)
        assert planner.compute_opportunities(track) == []

    def test_daylight_pass_over_the_same_geometry_is_offered(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        track = _track([(30.0, -65.0), (31.0, -64.0)],
                       start=datetime(2026, 3, 11, 16, 0, 0, tzinfo=timezone.utc))
        opps = planner.compute_opportunities(track)
        assert opps
        assert opps[0]["solar_elevation_deg"] >= 10.0

    def test_per_target_threshold_is_used(self):
        strict = dict(GULF_STREAM)
        strict["min_solar_elevation_deg"] = 60.0
        planner = ImagingPlanner([strict], altitude_km=450.0)
        track = _track([(30.0, -65.0), (31.0, -64.0)],
                       start=datetime(2026, 3, 11, 16, 0, 0, tzinfo=timezone.utc))
        rejects = planner.compute_opportunities(track, include_infeasible=True)
        assert planner.compute_opportunities(track) == []
        assert any("60.0" in (o["reject_reason"] or "") for o in rejects)

    def test_enforcement_can_be_disabled_by_config(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0,
                                 enforce_solar_elevation=False)
        dark = datetime(2026, 3, 11, 9, 0, 0, tzinfo=timezone.utc)
        track = _track([(30.0, -65.0), (31.0, -64.0)], start=dark)
        assert planner.compute_opportunities(track)   # eclipse-only behaviour

    def test_eclipse_still_blocks(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        track = _track([(30.0, -65.0), (31.0, -64.0)])
        for pt in track:
            pt["in_eclipse"] = True
        assert planner.compute_opportunities(track) == []


# ── Revisit constraint ─────────────────────────────────────────────

class TestRevisitConstraint:

    def _two_passes(self, gap_hours):
        first = _track([(30.0, -65.0), (31.0, -64.0), (60.0, -20.0)])
        second = _track([(30.0, -65.0), (31.0, -64.0), (60.0, -20.0)],
                        start=NOON_UTC + timedelta(hours=gap_hours))
        return first + second

    def test_second_pass_inside_the_revisit_interval_is_dropped(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        opps = planner.compute_opportunities(self._two_passes(24))
        assert len(opps) == 1, [o["capture_utc"] for o in opps]

    def test_second_pass_after_the_revisit_interval_is_kept(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        opps = planner.compute_opportunities(self._two_passes(24 * 4))
        assert len(opps) == 2

    def test_revisit_can_be_disabled_by_config(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0,
                                 enforce_revisit=False)
        assert len(planner.compute_opportunities(self._two_passes(24))) == 2

    def test_already_scheduled_capture_suppresses_a_new_opportunity(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        planner.note_scheduled("OCM-001", NOON_UTC - timedelta(hours=6))
        track = _track([(30.0, -65.0), (31.0, -64.0), (60.0, -20.0)])
        assert planner.compute_opportunities(track) == []

    def test_rejected_revisit_reports_the_reason(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        rejects = planner.compute_opportunities(
            self._two_passes(24), include_infeasible=True)
        assert any("revisit" in (o["reject_reason"] or "") for o in rejects)


# ── Config wiring ──────────────────────────────────────────────────

class TestConfigWiring:

    def test_from_config_reads_planner_yaml_and_orbit_altitude(self):
        from smo_planner.planner_config import load_planner_config
        cfg = load_planner_config(CONFIG_DIR)
        planner = ImagingPlanner.from_config(
            targets_path=CONFIG_DIR / "planning" / "imaging_targets.yaml",
            imaging_cfg=cfg.imaging,
            altitude_km=450.0,
            image_size_mb=800.0,
        )
        assert len(planner.get_targets()) == 7
        assert planner.max_off_nadir_deg == cfg.imaging.max_off_nadir_deg
        assert planner.swath_km == pytest.approx(240.0, abs=2.0)
        assert planner.image_size_mb == 800.0
        assert planner.enforce_revisit is True


# ── The swath is 240 km (owner ruling, 2026-08) ────────────────────

class TestSwathIs240Km:
    """The planner's swath must be the instrument's 240 km swath.

    DERIVATION — the planner works from the full across-track FOV, which is
    the one the payload optics give (configs/eosat1/subsystems/payload.yaml:
    8192 px x 5.5 um on a focal length of 84.5 mm):

        FOV   = 2 * atan((8192 * 5.5 um / 2) / 84.5 mm) = 29.856 deg
        swath = 2 * h * tan(FOV/2)
              = 2 * 450 km * tan(14.928 deg)            = 239.94 km

    TOLERANCE 240 +/- 2 km (0.8%): covers the rounded focal length and the
    flat-Earth swath formula (spherical ground arc 240.56 km, +0.3%).

    Before the ruling the planner used FOV = 15 deg -> 118 km, while the
    simulator produced 45 km and the MCS map drew a 55 km circle: three
    different instruments. All three are now this one.
    """

    def test_default_swath_is_240_km(self):
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        assert planner.swath_km == pytest.approx(240.0, abs=2.0)

    def test_module_constant_is_240_km(self):
        from smo_planner.imaging_planner import SWATH_KM, FOV_DEG
        assert SWATH_KM == pytest.approx(240.0, abs=2.0)
        assert FOV_DEG == pytest.approx(29.856, abs=0.01)

    def test_mission_config_swath_is_240_km(self):
        from smo_planner.planner_config import load_planner_config
        cfg = load_planner_config(CONFIG_DIR)
        planner = ImagingPlanner.from_config(
            imaging_cfg=cfg.imaging, altitude_km=450.0)
        assert planner.swath_km == pytest.approx(240.0, abs=2.0)
        # The roll limit is half the FOV, i.e. the 120 km half swath.
        assert planner.max_off_nadir_deg == pytest.approx(
            cfg.imaging.fov_deg / 2.0, abs=0.01)

    def test_planner_swath_matches_the_payload_optics(self):
        """Single source of truth: recompute the swath straight from
        subsystems/payload.yaml and require the planner to agree."""
        import math
        import yaml
        cfg = yaml.safe_load(
            (CONFIG_DIR / "subsystems" / "payload.yaml").read_text())
        half_fov = math.atan(
            (cfg["pixels_across_track"] * cfg["pixel_pitch_um"] * 1e-3)
            / 2.0 / cfg["focal_length_mm"])
        optics_swath_km = 2.0 * 450.0 * math.tan(half_fov)
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        assert planner.swath_km == pytest.approx(optics_swath_km, abs=0.5)

    def test_a_120_km_cross_track_target_is_now_reachable(self):
        """Consequence of the wider swath: a target ~1 deg (111 km) off the
        ground track is inside the swath and IS offered — it was infeasible
        at the old 118 km swath (59 km half swath)."""
        planner = ImagingPlanner([GULF_STREAM], altitude_km=450.0)
        track = _track([(46.0, -65.0), (46.0, -64.0), (46.0, -63.0)])
        opps = planner.compute_opportunities(track)
        assert opps
        assert 100.0 <= opps[0]["cross_track_km"] <= 120.0
        assert opps[0]["off_nadir_deg"] <= opps[0]["max_off_nadir_deg"]

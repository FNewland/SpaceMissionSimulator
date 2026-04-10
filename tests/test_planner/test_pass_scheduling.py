"""Tests for Wave 7 — Pass-Based Scheduling (smo-planner).

Covers:
  - schedule_pass_activity: valid scheduling at AOS+offset
  - schedule_pass_activity: reject activity past LOS
  - schedule_pass_activity: reject invalid pass_id
  - schedule_pass_activity: reject negative offset
  - schedule_pass_activity: reject empty contacts
  - validate_pass_plan: name conflicts
  - validate_pass_plan: time overlaps
  - validate_pass_plan: pass boundary violations
  - validate_pass_plan: power constraint warnings
  - validate_pass_plan: data constraint warnings
  - check_time_overlap: overlapping activities
  - check_time_overlap: non-overlapping activities
  - POST /api/schedule/pass-activity endpoint
"""
import pytest
from datetime import datetime, timezone

from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient
from pathlib import Path
from unittest.mock import MagicMock

from smo_planner.activity_scheduler import ActivityScheduler, ActivityState
from smo_planner.imaging_planner import ImagingPlanner


SAMPLE_CONTACTS = [
    {
        "gs_name": "Iqaluit",
        "aos": "2026-03-11T10:00:00+00:00",
        "los": "2026-03-11T10:10:00+00:00",
        "max_elevation_deg": 45.0,
        "duration_s": 600,
    },
    {
        "gs_name": "Troll",
        "aos": "2026-03-11T14:00:00+00:00",
        "los": "2026-03-11T14:08:00+00:00",
        "max_elevation_deg": 30.0,
        "duration_s": 480,
    },
    {
        "gs_name": "Iqaluit",
        "aos": "2026-03-11T21:30:00+00:00",
        "los": "2026-03-11T21:42:00+00:00",
        "max_elevation_deg": 60.0,
        "duration_s": 720,
    },
]

SAMPLE_TYPES = [
    {
        "name": "data_dump",
        "duration_s": 300,
        "power_w": 25,
        "data_volume_mb": 0,
        "priority": "high",
        "conflicts_with": [],
    },
    {
        "name": "imaging",
        "duration_s": 120,
        "power_w": 60,
        "data_volume_mb": 800,
        "priority": "high",
        "conflicts_with": ["software_upload"],
    },
    {
        "name": "software_upload",
        "duration_s": 1200,
        "power_w": 15,
        "data_volume_mb": 0,
        "priority": "high",
        "conflicts_with": ["imaging"],
    },
]


def _make_scheduler():
    return ActivityScheduler(SAMPLE_TYPES)


# ── schedule_pass_activity ─────────────────────────────────────────

class TestSchedulePassActivity:
    """Test scheduling activities relative to contact passes."""

    def test_schedule_at_aos(self):
        """Schedule an activity at AOS+0 min (start of pass)."""
        sched = _make_scheduler()
        act = sched.schedule_pass_activity(
            pass_id=1, offset_min=0, activity_name="data_dump",
            contacts=SAMPLE_CONTACTS,
        )
        assert act["name"] == "data_dump"
        assert act["start_time"] == "2026-03-11T10:00:00+00:00"
        assert act["pass_id"] == 1
        assert act["pass_gs"] == "Iqaluit"
        assert act["offset_min"] == 0

    def test_schedule_at_aos_plus_offset(self):
        """Schedule at AOS+2min."""
        sched = _make_scheduler()
        act = sched.schedule_pass_activity(
            pass_id=1, offset_min=2, activity_name="data_dump",
            contacts=SAMPLE_CONTACTS,
        )
        assert act["start_time"] == "2026-03-11T10:02:00+00:00"
        assert act["pass_id"] == 1

    def test_schedule_at_second_pass(self):
        """Schedule at pass 2."""
        sched = _make_scheduler()
        act = sched.schedule_pass_activity(
            pass_id=2, offset_min=1, activity_name="data_dump",
            contacts=SAMPLE_CONTACTS,
        )
        assert act["start_time"] == "2026-03-11T14:01:00+00:00"
        assert act["pass_gs"] == "Troll"

    def test_reject_activity_past_los(self):
        """Activity that extends past LOS should be rejected."""
        sched = _make_scheduler()
        # Pass 1 is 600s (10min). data_dump is 300s.
        # Offset 6 min = 360s start, end at 360+300=660s > 600s
        with pytest.raises(ValueError, match="past LOS"):
            sched.schedule_pass_activity(
                pass_id=1, offset_min=6, activity_name="data_dump",
                contacts=SAMPLE_CONTACTS,
            )

    def test_reject_invalid_pass_id_zero(self):
        """Pass ID 0 is invalid (1-based)."""
        sched = _make_scheduler()
        with pytest.raises(ValueError, match="Invalid pass_id"):
            sched.schedule_pass_activity(
                pass_id=0, offset_min=0, activity_name="data_dump",
                contacts=SAMPLE_CONTACTS,
            )

    def test_reject_invalid_pass_id_too_large(self):
        """Pass ID beyond contacts length should be rejected."""
        sched = _make_scheduler()
        with pytest.raises(ValueError, match="Invalid pass_id"):
            sched.schedule_pass_activity(
                pass_id=99, offset_min=0, activity_name="data_dump",
                contacts=SAMPLE_CONTACTS,
            )

    def test_reject_negative_offset(self):
        """Negative offset should be rejected."""
        sched = _make_scheduler()
        with pytest.raises(ValueError, match="offset_min must be >= 0"):
            sched.schedule_pass_activity(
                pass_id=1, offset_min=-5, activity_name="data_dump",
                contacts=SAMPLE_CONTACTS,
            )

    def test_reject_empty_contacts(self):
        """Empty contacts list should be rejected."""
        sched = _make_scheduler()
        with pytest.raises(ValueError, match="No contact windows"):
            sched.schedule_pass_activity(
                pass_id=1, offset_min=0, activity_name="data_dump",
                contacts=[],
            )

    def test_activity_added_to_schedule(self):
        """Scheduled pass activity should appear in the schedule."""
        sched = _make_scheduler()
        sched.schedule_pass_activity(
            pass_id=1, offset_min=0, activity_name="data_dump",
            contacts=SAMPLE_CONTACTS,
        )
        schedule = sched.get_schedule()
        assert len(schedule) == 1
        assert schedule[0]["pass_id"] == 1

    def test_custom_duration_override(self):
        """Custom duration_s via kwargs should be used for LOS validation."""
        sched = _make_scheduler()
        # Pass 2 is 480s. Schedule with 120s custom duration at offset 5min=300s.
        # End at 300+120=420s < 480s, should succeed
        act = sched.schedule_pass_activity(
            pass_id=2, offset_min=5, activity_name="data_dump",
            contacts=SAMPLE_CONTACTS, duration_s=120,
        )
        assert act["duration_s"] == 120


# ── check_time_overlap ─────────────────────────────────────────────

class TestCheckTimeOverlap:
    """Test time-based overlap detection."""

    def test_overlapping_activities(self):
        sched = _make_scheduler()
        sched.add_activity("data_dump", "2026-03-11T10:00:00+00:00")
        activity = {
            "id": 99,
            "name": "imaging",
            "start_time": "2026-03-11T10:02:00+00:00",
            "duration_s": 120,
        }
        overlaps = sched.check_time_overlap(activity)
        assert len(overlaps) == 1
        assert "data_dump" in overlaps[0]

    def test_non_overlapping_activities(self):
        sched = _make_scheduler()
        sched.add_activity("data_dump", "2026-03-11T10:00:00+00:00")
        activity = {
            "id": 99,
            "name": "imaging",
            "start_time": "2026-03-11T12:00:00+00:00",
            "duration_s": 120,
        }
        overlaps = sched.check_time_overlap(activity)
        assert len(overlaps) == 0

    def test_adjacent_activities_no_overlap(self):
        """Activity starting exactly when another ends should not overlap."""
        sched = _make_scheduler()
        # data_dump: 300s starting at 10:00 -> ends 10:05
        sched.add_activity("data_dump", "2026-03-11T10:00:00+00:00")
        activity = {
            "id": 99,
            "name": "imaging",
            "start_time": "2026-03-11T10:05:00+00:00",
            "duration_s": 120,
        }
        overlaps = sched.check_time_overlap(activity)
        assert len(overlaps) == 0

    def test_skip_cancelled_in_overlap_check(self):
        sched = _make_scheduler()
        sched.add_activity("data_dump", "2026-03-11T10:00:00+00:00")
        sched.update_state(1, ActivityState.CANCELLED)
        activity = {
            "id": 99,
            "name": "imaging",
            "start_time": "2026-03-11T10:02:00+00:00",
            "duration_s": 120,
        }
        overlaps = sched.check_time_overlap(activity)
        assert len(overlaps) == 0


# ── validate_pass_plan ─────────────────────────────────────────────

class TestValidatePassPlan:
    """Test full pass plan validation."""

    def test_clean_plan_no_issues(self):
        sched = _make_scheduler()
        sched.schedule_pass_activity(
            pass_id=1, offset_min=0, activity_name="data_dump",
            contacts=SAMPLE_CONTACTS,
        )
        issues = sched.validate_pass_plan(SAMPLE_CONTACTS)
        # data_dump has no name conflicts, no overlaps
        assert len(issues) == 0

    def test_detect_name_conflicts(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00+00:00")
        sched.add_activity("software_upload", "2026-03-11T10:01:00+00:00")
        issues = sched.validate_pass_plan(SAMPLE_CONTACTS)
        conflict_issues = [i for i in issues if i["type"] == "conflict"]
        assert len(conflict_issues) > 0

    def test_detect_time_overlaps(self):
        sched = _make_scheduler()
        sched.add_activity("data_dump", "2026-03-11T10:00:00+00:00")
        sched.add_activity("data_dump", "2026-03-11T10:02:00+00:00")
        issues = sched.validate_pass_plan(SAMPLE_CONTACTS)
        overlap_issues = [i for i in issues if i["type"] == "time_overlap"]
        assert len(overlap_issues) > 0

    def test_power_constraint_warnings(self):
        sched = _make_scheduler()
        sched.add_activity("data_dump", "2026-03-11T10:00:00+00:00")
        power_budget = {"warnings": ["SoC below 25% at pass 3"]}
        issues = sched.validate_pass_plan(
            SAMPLE_CONTACTS, power_budget=power_budget
        )
        power_issues = [i for i in issues if i["type"] == "power_constraint"]
        assert len(power_issues) == 1
        assert "SoC below 25%" in power_issues[0]["message"]

    def test_data_constraint_warnings(self):
        sched = _make_scheduler()
        data_budget = {"warnings": ["Onboard data volume exceeds storage"]}
        issues = sched.validate_pass_plan(
            SAMPLE_CONTACTS, data_budget=data_budget
        )
        data_issues = [i for i in issues if i["type"] == "data_constraint"]
        assert len(data_issues) == 1

    def test_ignores_cancelled_activities(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00+00:00")
        sched.add_activity("software_upload", "2026-03-11T10:01:00+00:00")
        sched.update_state(1, ActivityState.CANCELLED)
        issues = sched.validate_pass_plan(SAMPLE_CONTACTS)
        conflict_issues = [i for i in issues if i["type"] == "conflict"]
        assert len(conflict_issues) == 0


# ── POST /api/schedule/pass-activity endpoint ─────────────────────

class _SimpleGS:
    def __init__(self, name, lat_deg, lon_deg, alt_km, min_elevation_deg):
        self.name = name
        self.lat_deg = lat_deg
        self.lon_deg = lon_deg
        self.alt_km = alt_km
        self.min_elevation_deg = min_elevation_deg


def _make_planner_server():
    from smo_planner.server import PlannerServer

    mock_state = MagicMock()
    mock_state.utc = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    mock_state.lat_deg = 45.0
    mock_state.lon_deg = -75.0
    mock_state.alt_km = 550.0
    mock_state.in_eclipse = False
    mock_state.in_contact = True
    mock_state.vel_x = 7.0
    mock_state.vel_y = 0.5
    mock_state.solar_beta_deg = 30.0
    mock_state.gs_elevation_deg = 25.0
    mock_state.gs_range_km = 800.0

    gs = _SimpleGS("Iqaluit", 63.747, -68.518, 0.034, 5.0)
    mock_prop = MagicMock()
    mock_prop.advance.return_value = mock_state
    mock_planner = MagicMock()
    mock_planner.predict_ground_track.return_value = []

    server = PlannerServer.__new__(PlannerServer)
    server.config_dir = Path("/tmp/fake_config")
    server.http_port = 0
    server._gs_list = [gs]
    server._tle1 = "1 99999U 26001A   26070.50000000  .00000100  00000-0  10000-3 0  9991"
    server._tle2 = "2 99999  97.5000   0.0000 0010000   0.0000   0.0000 15.00000000    10"
    server._earth_r = 6371.0
    server._orbit_planner = mock_planner
    server._live_prop = mock_prop
    server._activity_types = SAMPLE_TYPES
    server._scheduler = ActivityScheduler(SAMPLE_TYPES)
    server._mcs_url = "http://localhost:9090"
    server._contacts_cache = SAMPLE_CONTACTS
    server._contacts_computed_at = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    server._ground_track_cache = []
    server._ground_track_computed_at = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    server._imaging_planner = ImagingPlanner()
    return server


def _build_app(server):
    app = web.Application()
    app.router.add_post("/api/schedule/pass-activity", server._handle_pass_activity)
    app.router.add_get("/api/budget/power", server._handle_power_budget)
    app.router.add_get("/api/budget/data", server._handle_data_budget)
    return app


async def _make_client(server):
    app = _build_app(server)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class TestPassActivityEndpoint:
    """Test POST /api/schedule/pass-activity HTTP endpoint."""

    @pytest.mark.asyncio
    async def test_schedule_pass_activity_success(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/schedule/pass-activity", json={
                "pass_id": 1,
                "offset_min": 1,
                "name": "data_dump",
            })
            assert resp.status == 201
            data = await resp.json()
            assert "activity" in data
            assert data["activity"]["pass_id"] == 1
            assert data["activity"]["pass_gs"] == "Iqaluit"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_schedule_pass_activity_missing_pass_id(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/schedule/pass-activity", json={
                "offset_min": 0,
                "name": "data_dump",
            })
            assert resp.status == 400
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_schedule_pass_activity_missing_name(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/schedule/pass-activity", json={
                "pass_id": 1,
                "offset_min": 0,
            })
            assert resp.status == 400
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_schedule_past_los_returns_400(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/schedule/pass-activity", json={
                "pass_id": 1,
                "offset_min": 8,
                "name": "data_dump",
            })
            assert resp.status == 400
            data = await resp.json()
            assert "past LOS" in data["error"]
        finally:
            await client.close()

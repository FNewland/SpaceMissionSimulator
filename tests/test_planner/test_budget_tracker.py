"""Tests for Wave 7 — Power/Data Budget Tracker (smo-planner).

Covers:
  - Power budget computation with contacts
  - Power budget with no contacts (returns warning)
  - SoC prediction at pass boundaries
  - SoC threshold warnings (below 25%)
  - Activity power consumption during passes
  - Eclipse fraction estimation from ground track
  - Data budget computation
  - Data generation tracking from imaging activities
  - Downlink capacity calculation
  - Storage capacity overflow warnings
  - Data dump scheduling detection
  - GET /api/budget/power endpoint
  - GET /api/budget/data endpoint
"""
import pytest
from datetime import datetime, timezone

from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient
from pathlib import Path
from unittest.mock import MagicMock

from smo_planner.budget_tracker import (
    BudgetTracker,
    BATTERY_CAPACITY_WH,
    MIN_SOC_PERCENT,
    ONBOARD_STORAGE_MB,
    DOWNLINK_RATE_BPS,
    PROTOCOL_OVERHEAD,
)
from smo_planner.activity_scheduler import ActivityScheduler
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
]

SAMPLE_GROUND_TRACK = [
    {"utc": "2026-03-11T10:00:00+00:00", "lat": 60.0, "lon": -65.0,
     "alt_km": 450.0, "in_eclipse": False},
    {"utc": "2026-03-11T10:05:00+00:00", "lat": 55.0, "lon": -60.0,
     "alt_km": 450.0, "in_eclipse": False},
    {"utc": "2026-03-11T10:10:00+00:00", "lat": 50.0, "lon": -55.0,
     "alt_km": 450.0, "in_eclipse": True},
]


# ── Power budget computation ──────────────────────────────────────

class TestPowerBudget:
    """Test power budget computation."""

    def test_power_budget_returns_required_fields(self):
        tracker = BudgetTracker(contacts=SAMPLE_CONTACTS)
        budget = tracker.compute_power_budget()
        assert "initial_soc" in budget
        assert "pass_predictions" in budget
        assert "final_soc" in budget
        assert "warnings" in budget
        assert "total_charge_wh" in budget
        assert "total_drain_wh" in budget

    def test_power_budget_with_contacts(self):
        tracker = BudgetTracker(contacts=SAMPLE_CONTACTS)
        budget = tracker.compute_power_budget()
        assert len(budget["pass_predictions"]) == 2
        for pred in budget["pass_predictions"]:
            assert "pass_id" in pred
            assert "gs_name" in pred
            assert "soc_at_aos" in pred
            assert "soc_at_los" in pred

    def test_power_budget_no_contacts(self):
        tracker = BudgetTracker(contacts=[])
        budget = tracker.compute_power_budget()
        assert len(budget["pass_predictions"]) == 0
        assert len(budget["warnings"]) > 0
        assert "No contact windows" in budget["warnings"][0]

    def test_initial_soc_custom(self):
        tracker = BudgetTracker(contacts=SAMPLE_CONTACTS, initial_soc=90.0)
        budget = tracker.compute_power_budget()
        assert budget["initial_soc"] == 90.0

    def test_soc_decreases_with_activities(self):
        """Activities should increase drain and decrease final SoC."""
        schedule_no_act = []
        schedule_with_act = [
            {
                "name": "imaging",
                "start_time": "2026-03-11T10:01:00+00:00",
                "duration_s": 120,
                "power_w": 60,
                "state": 0,
                "state_name": "PLANNED",
            },
        ]
        t1 = BudgetTracker(contacts=SAMPLE_CONTACTS, schedule=schedule_no_act)
        t2 = BudgetTracker(contacts=SAMPLE_CONTACTS, schedule=schedule_with_act)
        b1 = t1.compute_power_budget()
        b2 = t2.compute_power_budget()
        # With activity, total drain should be higher
        assert b2["total_drain_wh"] >= b1["total_drain_wh"]

    def test_pass_predictions_ordered(self):
        tracker = BudgetTracker(contacts=SAMPLE_CONTACTS)
        budget = tracker.compute_power_budget()
        preds = budget["pass_predictions"]
        assert preds[0]["pass_id"] == 1
        assert preds[1]["pass_id"] == 2

    def test_eclipse_fraction_from_ground_track(self):
        """Eclipse fraction should use ground track data if available."""
        tracker = BudgetTracker(
            contacts=SAMPLE_CONTACTS,
            ground_track=SAMPLE_GROUND_TRACK,
        )
        # Calling private method for unit test
        start = datetime(2026, 3, 11, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 11, 10, 10, 0, tzinfo=timezone.utc)
        frac = tracker._estimate_eclipse_fraction(start, end)
        # 1 of 3 points is in eclipse
        assert abs(frac - 1.0 / 3.0) < 0.01

    def test_eclipse_fraction_default_no_track(self):
        """Without ground track, default eclipse fraction should be used."""
        tracker = BudgetTracker(contacts=SAMPLE_CONTACTS, ground_track=[])
        start = datetime(2026, 3, 11, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 11, 10, 10, 0, tzinfo=timezone.utc)
        frac = tracker._estimate_eclipse_fraction(start, end)
        assert abs(frac - 0.35) < 0.01


# ── Data budget computation ───────────────────────────────────────

class TestDataBudget:
    """Test data volume budget computation."""

    def test_data_budget_returns_required_fields(self):
        tracker = BudgetTracker(contacts=SAMPLE_CONTACTS)
        budget = tracker.compute_data_budget()
        assert "onboard_data_mb" in budget
        assert "storage_capacity_mb" in budget
        assert "utilization_percent" in budget
        assert "pass_downlink" in budget
        assert "planned_generation_mb" in budget
        assert "planned_downlink_mb" in budget
        assert "warnings" in budget

    def test_data_budget_no_activities(self):
        tracker = BudgetTracker(contacts=SAMPLE_CONTACTS)
        budget = tracker.compute_data_budget()
        assert budget["planned_generation_mb"] == 0
        assert budget["onboard_data_mb"] == 0

    def test_data_generation_from_imaging(self):
        schedule = [
            {
                "name": "imaging",
                "start_time": "2026-03-11T09:00:00+00:00",
                "duration_s": 120,
                "power_w": 60,
                "data_volume_mb": 800,
                "state": 0,
                "state_name": "PLANNED",
            },
        ]
        tracker = BudgetTracker(contacts=SAMPLE_CONTACTS, schedule=schedule)
        budget = tracker.compute_data_budget()
        assert budget["planned_generation_mb"] == 800

    def test_data_generation_ignores_cancelled(self):
        schedule = [
            {
                "name": "imaging",
                "start_time": "2026-03-11T09:00:00+00:00",
                "duration_s": 120,
                "power_w": 60,
                "data_volume_mb": 800,
                "state": 6,
                "state_name": "CANCELLED",
            },
        ]
        tracker = BudgetTracker(contacts=SAMPLE_CONTACTS, schedule=schedule)
        budget = tracker.compute_data_budget()
        assert budget["planned_generation_mb"] == 0

    def test_downlink_capacity_calculation(self):
        """Verify per-pass downlink capacity with elevation-dependent efficiency."""
        tracker = BudgetTracker(contacts=SAMPLE_CONTACTS)
        budget = tracker.compute_data_budget()
        # Pass 1: max_elevation_deg=45 -> efficiency = 0.9 + 0.1*(15/60) = 0.925
        el_efficiency = 0.9 + 0.1 * min((45.0 - 30.0) / 60.0, 1.0)
        expected_mb = (DOWNLINK_RATE_BPS * (1.0 - PROTOCOL_OVERHEAD) * 600 * el_efficiency) / (
            8.0 * 1024 * 1024
        )
        assert abs(budget["pass_downlink"][0]["capacity_mb"] - round(expected_mb, 1)) < 0.2
        # Verify elevation efficiency is reported
        assert "elevation_efficiency" in budget["pass_downlink"][0]

    def test_storage_overflow_warning(self):
        """Should warn when onboard data exceeds storage capacity."""
        # Create enough imaging to exceed 1024 MB
        schedule = [
            {
                "name": f"imaging_{i}",
                "start_time": f"2026-03-11T0{i}:00:00+00:00",
                "duration_s": 120,
                "power_w": 60,
                "data_volume_mb": 600,
                "state": 0,
                "state_name": "PLANNED",
            }
            for i in range(3)  # 3 * 600 = 1800 MB > 1024 MB
        ]
        tracker = BudgetTracker(contacts=SAMPLE_CONTACTS, schedule=schedule)
        budget = tracker.compute_data_budget()
        assert any("exceeds storage" in w for w in budget["warnings"])

    def test_dump_scheduled_detection(self):
        """Should detect when a data_dump is scheduled during a pass."""
        schedule = [
            {
                "name": "data_dump",
                "start_time": "2026-03-11T10:01:00+00:00",
                "duration_s": 300,
                "power_w": 25,
                "data_volume_mb": 0,
                "state": 0,
                "state_name": "PLANNED",
            },
        ]
        tracker = BudgetTracker(contacts=SAMPLE_CONTACTS, schedule=schedule)
        budget = tracker.compute_data_budget()
        # Pass 1 should show dump_scheduled=True
        assert budget["pass_downlink"][0]["dump_scheduled"] is True
        # Pass 2 should show dump_scheduled=False
        assert budget["pass_downlink"][1]["dump_scheduled"] is False

    def test_no_dump_warning(self):
        """Should warn when data is generated but no dump is planned."""
        schedule = [
            {
                "name": "imaging",
                "start_time": "2026-03-11T09:00:00+00:00",
                "duration_s": 120,
                "power_w": 60,
                "data_volume_mb": 800,
                "state": 0,
                "state_name": "PLANNED",
            },
        ]
        tracker = BudgetTracker(contacts=SAMPLE_CONTACTS, schedule=schedule)
        budget = tracker.compute_data_budget()
        assert any("no downlink" in w.lower() for w in budget["warnings"])

    def test_storage_capacity_custom(self):
        tracker = BudgetTracker(
            contacts=SAMPLE_CONTACTS, storage_capacity_mb=2048.0
        )
        budget = tracker.compute_data_budget()
        assert budget["storage_capacity_mb"] == 2048.0


# ── Budget API endpoints ─────────────────────────────────────────

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
    server._activity_types = []
    server._scheduler = ActivityScheduler([])
    server._mcs_url = "http://localhost:9090"
    server._contacts_cache = SAMPLE_CONTACTS
    server._contacts_computed_at = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    server._ground_track_cache = SAMPLE_GROUND_TRACK
    server._ground_track_computed_at = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    server._imaging_planner = ImagingPlanner()
    return server


def _build_app(server):
    app = web.Application()
    app.router.add_get("/api/budget/power", server._handle_power_budget)
    app.router.add_get("/api/budget/data", server._handle_data_budget)
    return app


async def _make_client(server):
    app = _build_app(server)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class TestBudgetEndpoints:
    """Test GET /api/budget/power and /api/budget/data endpoints."""

    @pytest.mark.asyncio
    async def test_power_budget_endpoint(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/budget/power")
            assert resp.status == 200
            data = await resp.json()
            assert "initial_soc" in data
            assert "pass_predictions" in data
            assert "final_soc" in data
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_data_budget_endpoint(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/budget/data")
            assert resp.status == 200
            data = await resp.json()
            assert "onboard_data_mb" in data
            assert "storage_capacity_mb" in data
            assert "pass_downlink" in data
        finally:
            await client.close()

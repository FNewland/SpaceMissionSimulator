"""Tests for Wave 7 — Imaging Target Planner (smo-planner).

Covers:
  - ImagingTarget bounding box containment
  - ImagingTarget swath proximity check
  - ImagingTarget serialization to dict
  - ImagingPlanner target loading from list
  - ImagingPlanner target loading from YAML config
  - Imaging opportunity computation from ground track
  - Opportunity filtering by eclipse
  - Opportunity duration calculation
  - Capture command sequence generation
  - Capture with custom lat/lon
  - Unknown target_id raises ValueError
  - GET /api/imaging/targets endpoint
  - GET /api/imaging/opportunities endpoint
  - POST /api/imaging/schedule endpoint
"""
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from smo_planner.imaging_planner import (
    ImagingPlanner,
    ImagingTarget,
    SWATH_KM,
    IMAGING_DURATION_S,
    IMAGING_POWER_W,
    IMAGING_DATA_MB,
)
from smo_planner.activity_scheduler import ActivityScheduler
from smo_planner.budget_tracker import BudgetTracker


SAMPLE_TARGETS = [
    {
        "id": "OCM-001",
        "name": "Gulf Stream",
        "description": "Western boundary current",
        "priority": "high",
        "revisit_days": 3,
        "min_solar_elevation_deg": 10.0,
        "region": {
            "min_lat": 25.0,
            "max_lat": 45.0,
            "min_lon": -80.0,
            "max_lon": -50.0,
        },
    },
    {
        "id": "OCM-002",
        "name": "Kuroshio Current",
        "description": "Western boundary current of North Pacific",
        "priority": "high",
        "revisit_days": 3,
        "min_solar_elevation_deg": 10.0,
        "region": {
            "min_lat": 25.0,
            "max_lat": 40.0,
            "min_lon": 125.0,
            "max_lon": 155.0,
        },
    },
    {
        "id": "OCM-003",
        "name": "Agulhas Current",
        "description": "Western boundary current of South Indian Ocean",
        "priority": "high",
        "revisit_days": 5,
        "min_solar_elevation_deg": 10.0,
        "region": {
            "min_lat": -40.0,
            "max_lat": -25.0,
            "min_lon": 20.0,
            "max_lon": 40.0,
        },
    },
]


# ── ImagingTarget ──────────────────────────────────────────────────

class TestImagingTarget:
    """Test ImagingTarget bounding box and swath checks."""

    def test_contains_point_inside(self):
        target = ImagingTarget(SAMPLE_TARGETS[0])
        assert target.contains_point(35.0, -65.0) is True

    def test_contains_point_outside(self):
        target = ImagingTarget(SAMPLE_TARGETS[0])
        assert target.contains_point(50.0, -65.0) is False

    def test_contains_point_on_boundary(self):
        target = ImagingTarget(SAMPLE_TARGETS[0])
        assert target.contains_point(25.0, -80.0) is True

    def test_within_swath_inside_region(self):
        target = ImagingTarget(SAMPLE_TARGETS[0])
        assert target.within_swath(35.0, -65.0) is True

    def test_within_swath_near_boundary(self):
        """Point just outside the region but within swath margin."""
        target = ImagingTarget(SAMPLE_TARGETS[0])
        # Just above max_lat (45.0) but within ~60km (half swath)
        assert target.within_swath(45.3, -65.0) is True

    def test_within_swath_far_outside(self):
        target = ImagingTarget(SAMPLE_TARGETS[0])
        assert target.within_swath(60.0, -65.0) is False

    def test_to_dict(self):
        target = ImagingTarget(SAMPLE_TARGETS[0])
        d = target.to_dict()
        assert d["id"] == "OCM-001"
        assert d["name"] == "Gulf Stream"
        assert d["region"]["min_lat"] == 25.0
        assert d["center_lat"] == 35.0
        assert d["center_lon"] == -65.0

    def test_center_computation(self):
        target = ImagingTarget(SAMPLE_TARGETS[0])
        assert target.center_lat == (25.0 + 45.0) / 2.0
        assert target.center_lon == (-80.0 + -50.0) / 2.0


# ── ImagingPlanner ─────────────────────────────────────────────────

class TestImagingPlanner:
    """Test ImagingPlanner target management and opportunity computation."""

    def test_get_targets(self):
        planner = ImagingPlanner(SAMPLE_TARGETS)
        targets = planner.get_targets()
        assert len(targets) == 3
        assert targets[0]["id"] == "OCM-001"

    def test_get_targets_empty(self):
        planner = ImagingPlanner()
        assert planner.get_targets() == []

    def test_compute_opportunities_empty_track(self):
        planner = ImagingPlanner(SAMPLE_TARGETS)
        opps = planner.compute_opportunities([])
        assert opps == []

    def test_compute_opportunities_no_targets(self):
        planner = ImagingPlanner([])
        track = [{"utc": "2026-03-11T10:00:00Z", "lat": 35.0, "lon": -65.0,
                  "alt_km": 450.0, "in_eclipse": False}]
        opps = planner.compute_opportunities(track)
        assert opps == []

    def test_compute_opportunities_detects_target(self):
        planner = ImagingPlanner(SAMPLE_TARGETS)
        # Ground track passes through Gulf Stream region
        track = [
            {"utc": "2026-03-11T10:00:00Z", "lat": 30.0, "lon": -65.0,
             "alt_km": 450.0, "in_eclipse": False},
            {"utc": "2026-03-11T10:01:00Z", "lat": 32.0, "lon": -63.0,
             "alt_km": 450.0, "in_eclipse": False},
            {"utc": "2026-03-11T10:02:00Z", "lat": 34.0, "lon": -61.0,
             "alt_km": 450.0, "in_eclipse": False},
            {"utc": "2026-03-11T10:03:00Z", "lat": 50.0, "lon": -40.0,
             "alt_km": 450.0, "in_eclipse": False},
        ]
        opps = planner.compute_opportunities(track)
        assert len(opps) >= 1
        assert opps[0]["target_id"] == "OCM-001"
        assert opps[0]["target_name"] == "Gulf Stream"

    def test_opportunity_has_duration(self):
        planner = ImagingPlanner(SAMPLE_TARGETS)
        track = [
            {"utc": "2026-03-11T10:00:00+00:00", "lat": 30.0, "lon": -65.0,
             "alt_km": 450.0, "in_eclipse": False},
            {"utc": "2026-03-11T10:01:00+00:00", "lat": 32.0, "lon": -63.0,
             "alt_km": 450.0, "in_eclipse": False},
            {"utc": "2026-03-11T10:02:00+00:00", "lat": 50.0, "lon": -40.0,
             "alt_km": 450.0, "in_eclipse": False},
        ]
        opps = planner.compute_opportunities(track)
        assert len(opps) >= 1
        assert opps[0]["duration_s"] > 0

    def test_eclipse_blocks_opportunities(self):
        planner = ImagingPlanner(SAMPLE_TARGETS)
        # All points in eclipse
        track = [
            {"utc": "2026-03-11T10:00:00Z", "lat": 30.0, "lon": -65.0,
             "alt_km": 450.0, "in_eclipse": True},
            {"utc": "2026-03-11T10:01:00Z", "lat": 32.0, "lon": -63.0,
             "alt_km": 450.0, "in_eclipse": True},
        ]
        opps = planner.compute_opportunities(track)
        assert len(opps) == 0

    def test_multiple_targets_in_same_track(self):
        """Track that passes through two target regions."""
        planner = ImagingPlanner(SAMPLE_TARGETS)
        track = [
            # Gulf Stream
            {"utc": "2026-03-11T10:00:00Z", "lat": 35.0, "lon": -65.0,
             "alt_km": 450.0, "in_eclipse": False},
            {"utc": "2026-03-11T10:01:00Z", "lat": 50.0, "lon": -40.0,
             "alt_km": 450.0, "in_eclipse": False},
            # Agulhas Current
            {"utc": "2026-03-11T10:30:00Z", "lat": -30.0, "lon": 30.0,
             "alt_km": 450.0, "in_eclipse": False},
            {"utc": "2026-03-11T10:31:00Z", "lat": 0.0, "lon": 50.0,
             "alt_km": 450.0, "in_eclipse": False},
        ]
        opps = planner.compute_opportunities(track)
        target_ids = {o["target_id"] for o in opps}
        assert "OCM-001" in target_ids
        assert "OCM-003" in target_ids


# ── Capture command sequence ───────────────────────────────────────

class TestCaptureSequence:
    """Test capture command sequence generation."""

    def test_generate_capture_sequence(self):
        planner = ImagingPlanner(SAMPLE_TARGETS)
        capture = planner.generate_capture_sequence(
            "OCM-001", "2026-03-11T10:00:00Z"
        )
        assert capture["name"] == "imaging_OCM-001"
        assert capture["start_time"] == "2026-03-11T10:00:00Z"
        assert capture["duration_s"] == IMAGING_DURATION_S
        assert capture["power_w"] == IMAGING_POWER_W
        assert capture["data_volume_mb"] == IMAGING_DATA_MB
        assert capture["target_id"] == "OCM-001"
        assert capture["target_name"] == "Gulf Stream"
        assert len(capture["command_sequence"]) == 4

    def test_capture_command_has_s8_func_id(self):
        planner = ImagingPlanner(SAMPLE_TARGETS)
        capture = planner.generate_capture_sequence(
            "OCM-001", "2026-03-11T10:00:00Z"
        )
        # Find the capture command (third step)
        cmd = capture["command_sequence"][2]
        assert cmd["service"] == 8
        assert cmd["subtype"] == 1
        assert cmd["func_id"] == 0x16
        assert "parameters" in cmd
        assert cmd["parameters"]["target_id"] == "OCM-001"

    def test_capture_custom_lat_lon(self):
        planner = ImagingPlanner(SAMPLE_TARGETS)
        capture = planner.generate_capture_sequence(
            "OCM-001", "2026-03-11T10:00:00Z", lat=30.5, lon=-62.3
        )
        assert capture["capture_lat"] == 30.5
        assert capture["capture_lon"] == -62.3

    def test_capture_default_lat_lon_uses_center(self):
        planner = ImagingPlanner(SAMPLE_TARGETS)
        capture = planner.generate_capture_sequence(
            "OCM-001", "2026-03-11T10:00:00Z"
        )
        assert capture["capture_lat"] == 35.0  # center of region
        assert capture["capture_lon"] == -65.0

    def test_unknown_target_raises(self):
        planner = ImagingPlanner(SAMPLE_TARGETS)
        with pytest.raises(ValueError, match="Unknown target ID"):
            planner.generate_capture_sequence("BOGUS", "2026-03-11T10:00:00Z")


# ── Imaging config file ──────────────────────────────────────────

class TestImagingConfig:
    """Test loading imaging targets from YAML config."""

    def test_load_targets_from_real_config(self):
        config_path = Path(
            "/Users/FNewland/SpaceMissionSimulation/configs/eosat1/planning"
            "/imaging_targets.yaml"
        )
        if not config_path.exists():
            pytest.skip("Config file not found")
        planner = ImagingPlanner()
        planner.load_targets_from_config(config_path)
        targets = planner.get_targets()
        assert len(targets) >= 5
        names = {t["name"] for t in targets}
        assert "Gulf Stream" in names
        assert "Kuroshio Current" in names
        assert "Agulhas Current" in names


# ── Imaging API endpoints ─────────────────────────────────────────

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
    mock_planner.predict_ground_track.return_value = [
        {"utc": "2026-03-11T12:00:00", "lat": 35.0, "lon": -65.0,
         "alt_km": 450.0, "in_eclipse": False},
    ]

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
    server._contacts_cache = []
    server._contacts_computed_at = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    server._ground_track_cache = []
    server._ground_track_computed_at = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    server._imaging_planner = ImagingPlanner(SAMPLE_TARGETS)
    return server


def _build_app(server):
    app = web.Application()
    app.router.add_get("/api/imaging/targets", server._handle_imaging_targets)
    app.router.add_get("/api/imaging/opportunities",
                       server._handle_imaging_opportunities)
    app.router.add_post("/api/imaging/schedule",
                        server._handle_imaging_schedule)
    return app


async def _make_client(server):
    app = _build_app(server)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class TestImagingEndpoints:
    """Test imaging API endpoints."""

    @pytest.mark.asyncio
    async def test_get_targets(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/imaging/targets")
            assert resp.status == 200
            data = await resp.json()
            assert "targets" in data
            assert len(data["targets"]) == 3
            assert data["targets"][0]["id"] == "OCM-001"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_get_opportunities(self):
        server = _make_planner_server()
        # Mock OrbitPropagator and OrbitPlanner for the 24h track computation
        mock_prop_cls = MagicMock()
        mock_planner_cls = MagicMock()
        mock_planner_cls.return_value.predict_ground_track.return_value = [
            {"utc": "2026-03-11T12:00:00", "lat": 35.0, "lon": -65.0,
             "alt_km": 450.0, "in_eclipse": False},
            {"utc": "2026-03-11T12:01:00", "lat": 37.0, "lon": -63.0,
             "alt_km": 450.0, "in_eclipse": False},
            {"utc": "2026-03-11T12:02:00", "lat": 50.0, "lon": -40.0,
             "alt_km": 450.0, "in_eclipse": False},
        ]

        with patch("smo_planner.server.OrbitPropagator", mock_prop_cls), \
             patch("smo_planner.server.OrbitPlanner", mock_planner_cls):
            client = await _make_client(server)
            try:
                resp = await client.get("/api/imaging/opportunities")
                assert resp.status == 200
                data = await resp.json()
                assert "opportunities" in data
                assert "computed_at" in data
                assert "target_count" in data
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_schedule_imaging(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/imaging/schedule", json={
                "target_id": "OCM-001",
                "start_time": "2026-03-11T12:00:00Z",
            })
            assert resp.status == 201
            data = await resp.json()
            assert "activity" in data
            assert data["activity"]["target_id"] == "OCM-001"
            assert data["activity"]["target_name"] == "Gulf Stream"
            assert data["activity"]["data_volume_mb"] == IMAGING_DATA_MB
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_schedule_imaging_unknown_target(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/imaging/schedule", json={
                "target_id": "BOGUS",
                "start_time": "2026-03-11T12:00:00Z",
            })
            assert resp.status == 400
            data = await resp.json()
            assert "Unknown target" in data["error"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_schedule_imaging_missing_fields(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/imaging/schedule", json={
                "target_id": "OCM-001",
            })
            assert resp.status == 400
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_schedule_imaging_with_custom_coords(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/imaging/schedule", json={
                "target_id": "OCM-001",
                "start_time": "2026-03-11T12:00:00Z",
                "lat": 30.5,
                "lon": -62.3,
            })
            assert resp.status == 201
            data = await resp.json()
            assert data["activity"]["capture_lat"] == 30.5
            assert data["activity"]["capture_lon"] == -62.3
        finally:
            await client.close()

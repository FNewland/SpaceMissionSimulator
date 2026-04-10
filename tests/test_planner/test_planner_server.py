"""Tests for smo-planner — PlannerServer HTTP API.

Covers:
  - GET / returns HTML content
  - GET /api/contacts returns contact windows
  - GET /api/ground-stations returns station list
  - GET /api/ground-track returns predicted track
  - GET /api/spacecraft-state returns current position
  - GET /api/schedule returns activity list
  - POST /api/schedule adds an activity
  - POST /api/schedule missing fields returns 400
  - PUT /api/schedule/{id} updates an activity
  - DELETE /api/schedule/{id} removes an activity
  - DELETE /api/schedule/{id} nonexistent returns 404
  - GET /api/schedule/validate returns validation result
  - GET /api/activity-types returns activity type list

Uses aiohttp.test_utils.TestServer/TestClient for HTTP endpoint testing
with mocked orbit propagation (no real TLE required).
"""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from smo_planner.server import PlannerServer
from smo_planner.activity_scheduler import ActivityScheduler, ActivityState
from smo_planner.imaging_planner import ImagingPlanner


# ── Helpers ─────────────────────────────────────────────────────────

def _make_mock_state():
    """Create a mock orbit state."""
    state = MagicMock()
    state.utc = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    state.lat_deg = 45.0
    state.lon_deg = -75.0
    state.alt_km = 550.0
    state.in_eclipse = False
    state.in_contact = True
    state.vel_x = 7.0
    state.vel_y = 0.5
    state.solar_beta_deg = 30.0
    state.gs_elevation_deg = 25.0
    state.gs_range_km = 800.0
    return state


class _SimpleGS:
    """Simple ground station stand-in with serializable attributes."""
    def __init__(self, name, lat_deg, lon_deg, alt_km, min_elevation_deg):
        self.name = name
        self.lat_deg = lat_deg
        self.lon_deg = lon_deg
        self.alt_km = alt_km
        self.min_elevation_deg = min_elevation_deg


def _make_planner_server():
    """Create a PlannerServer with all external dependencies mocked."""
    mock_state = _make_mock_state()
    gs_ottawa = _SimpleGS(
        name="Ottawa", lat_deg=45.4215, lon_deg=-75.6972,
        alt_km=0.07, min_elevation_deg=5.0,
    )

    mock_prop_instance = MagicMock()
    mock_prop_instance.advance.return_value = mock_state
    mock_prop_instance.contact_windows.return_value = [
        {
            "gs": "Ottawa",
            "aos": "2026-03-11T12:00:00+00:00",
            "los": "2026-03-11T12:10:00+00:00",
            "max_elevation_deg": 45.0,
            "duration_s": 600,
        },
    ]

    mock_planner_instance = MagicMock()
    mock_planner_instance.predict_ground_track.return_value = [
        {"utc": "2026-03-11T12:00:00", "lat": 45.0, "lon": -75.0,
         "alt_km": 550.0, "in_eclipse": False},
        {"utc": "2026-03-11T12:01:00", "lat": 44.5, "lon": -74.0,
         "alt_km": 550.1, "in_eclipse": False},
    ]

    server = PlannerServer.__new__(PlannerServer)
    server.config_dir = Path("/tmp/fake_config")
    server.http_port = 0
    server._gs_list = [gs_ottawa]
    server._tle1 = "1 99999U 26001A   26070.50000000  .00000100  00000-0  10000-3 0  9991"
    server._tle2 = "2 99999  97.5000   0.0000 0010000   0.0000   0.0000 15.00000000    10"
    server._earth_r = 6371.0
    server._orbit_planner = mock_planner_instance
    server._live_prop = mock_prop_instance
    server._activity_types = [
        {
            "name": "imaging",
            "duration_s": 120,
            "power_w": 45,
            "command_sequence": [
                {"service": 8, "subtype": 1, "func_id": "0x10"},
            ],
        },
    ]
    server._scheduler = ActivityScheduler(server._activity_types)
    server._mcs_url = "http://localhost:9090"
    server._contacts_cache = mock_prop_instance.contact_windows.return_value
    server._contacts_computed_at = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    server._ground_track_cache = mock_planner_instance.predict_ground_track.return_value
    server._ground_track_computed_at = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    server._imaging_planner = ImagingPlanner()

    return server


def _build_app(server: PlannerServer) -> web.Application:
    """Build the aiohttp Application with all routes registered."""
    app = web.Application()
    app.router.add_get("/", server._handle_index)
    app.router.add_get("/api/contacts", server._handle_contacts)
    app.router.add_get("/api/ground-stations", server._handle_ground_stations)
    app.router.add_get("/api/ground-track", server._handle_ground_track)
    app.router.add_get("/api/spacecraft-state", server._handle_spacecraft_state)
    app.router.add_get("/api/schedule", server._handle_schedule)
    app.router.add_post("/api/schedule", server._handle_add_activity)
    app.router.add_put("/api/schedule/{id}", server._handle_update_activity)
    app.router.add_delete("/api/schedule/{id}", server._handle_delete_activity)
    app.router.add_get("/api/schedule/validate", server._handle_validate_schedule)
    app.router.add_get("/api/activity-types", server._handle_activity_types)
    app.router.add_post("/api/schedule/pass-activity", server._handle_pass_activity)
    app.router.add_get("/api/budget/power", server._handle_power_budget)
    app.router.add_get("/api/budget/data", server._handle_data_budget)
    app.router.add_get("/api/imaging/targets", server._handle_imaging_targets)
    app.router.add_get("/api/imaging/opportunities", server._handle_imaging_opportunities)
    app.router.add_post("/api/imaging/schedule", server._handle_imaging_schedule)
    return app


async def _make_client(server: PlannerServer) -> TestClient:
    """Create a TestClient for the given PlannerServer."""
    app = _build_app(server)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# ── GET / ───────────────────────────────────────────────────────────

class TestIndexEndpoint:
    """Test GET / returns HTML."""

    @pytest.mark.asyncio
    async def test_index_returns_html(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/")
            assert resp.status == 200
            assert resp.content_type == "text/html"
        finally:
            await client.close()


# ── GET /api/contacts ──────────────────────────────────────────────

class TestContactsEndpoint:
    """Test GET /api/contacts."""

    @pytest.mark.asyncio
    async def test_api_contacts(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/contacts")
            assert resp.status == 200
            data = await resp.json()
            assert "contacts" in data
            assert isinstance(data["contacts"], list)
            assert "computed_at" in data
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_api_contacts_has_ground_stations(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/contacts")
            data = await resp.json()
            assert "ground_stations" in data
        finally:
            await client.close()


# ── GET /api/ground-stations ───────────────────────────────────────

class TestGroundStationsEndpoint:
    """Test GET /api/ground-stations."""

    @pytest.mark.asyncio
    async def test_api_ground_stations(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/ground-stations")
            assert resp.status == 200
            data = await resp.json()
            assert "ground_stations" in data
            assert isinstance(data["ground_stations"], list)
            for gs in data["ground_stations"]:
                assert "name" in gs
                assert "lat_deg" in gs
                assert "lon_deg" in gs
        finally:
            await client.close()


# ── GET /api/ground-track ──────────────────────────────────────────

class TestGroundTrackEndpoint:
    """Test GET /api/ground-track."""

    @pytest.mark.asyncio
    async def test_api_ground_track(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/ground-track")
            assert resp.status == 200
            data = await resp.json()
            assert "ground_track" in data
            assert isinstance(data["ground_track"], list)
            assert len(data["ground_track"]) > 0
            point = data["ground_track"][0]
            assert "lat" in point
            assert "lon" in point
            assert "utc" in point
        finally:
            await client.close()


# ── GET /api/spacecraft-state ──────────────────────────────────────

class TestSpacecraftStateEndpoint:
    """Test GET /api/spacecraft-state."""

    @pytest.mark.asyncio
    async def test_api_spacecraft_state(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/spacecraft-state")
            assert resp.status == 200
            data = await resp.json()
            assert "lat" in data
            assert "lon" in data
            assert "alt_km" in data
            assert "in_eclipse" in data
            assert "in_contact" in data
        finally:
            await client.close()


# ── GET /api/schedule ──────────────────────────────────────────────

class TestScheduleGetEndpoint:
    """Test GET /api/schedule."""

    @pytest.mark.asyncio
    async def test_api_schedule_empty(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/schedule")
            assert resp.status == 200
            data = await resp.json()
            assert "schedule" in data
            assert data["schedule"] == []
        finally:
            await client.close()


# ── POST /api/schedule ─────────────────────────────────────────────

class TestSchedulePostEndpoint:
    """Test POST /api/schedule to add activities."""

    @pytest.mark.asyncio
    async def test_add_activity(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/schedule", json={
                "name": "imaging",
                "start_time": "2026-03-11T14:00:00Z",
            })
            assert resp.status == 201
            data = await resp.json()
            assert "activity" in data
            assert data["activity"]["name"] == "imaging"
            assert data["activity"]["id"] == 1
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_add_activity_missing_name(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/schedule", json={
                "start_time": "2026-03-11T14:00:00Z",
            })
            assert resp.status == 400
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_add_activity_missing_start_time(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/schedule", json={
                "name": "imaging",
            })
            assert resp.status == 400
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_add_multiple_activities(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            await client.post("/api/schedule", json={
                "name": "imaging", "start_time": "2026-03-11T14:00:00Z",
            })
            await client.post("/api/schedule", json={
                "name": "imaging", "start_time": "2026-03-11T15:00:00Z",
            })
            resp = await client.get("/api/schedule")
            data = await resp.json()
            assert len(data["schedule"]) == 2
        finally:
            await client.close()


# ── PUT /api/schedule/{id} ─────────────────────────────────────────

class TestSchedulePutEndpoint:
    """Test PUT /api/schedule/{id} to update activities."""

    @pytest.mark.asyncio
    async def test_update_activity_state(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            await client.post("/api/schedule", json={
                "name": "imaging", "start_time": "2026-03-11T14:00:00Z",
            })
            resp = await client.put("/api/schedule/1", json={
                "state": "VALIDATED",
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["activity"]["state_name"] == "VALIDATED"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_404(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.put("/api/schedule/999", json={"state": "VALIDATED"})
            assert resp.status == 404
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_update_invalid_state_returns_400(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            await client.post("/api/schedule", json={
                "name": "imaging", "start_time": "2026-03-11T14:00:00Z",
            })
            resp = await client.put("/api/schedule/1", json={
                "state": "BOGUS_STATE",
            })
            assert resp.status == 400
        finally:
            await client.close()


# ── DELETE /api/schedule/{id} ──────────────────────────────────────

class TestScheduleDeleteEndpoint:
    """Test DELETE /api/schedule/{id}."""

    @pytest.mark.asyncio
    async def test_delete_activity(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            await client.post("/api/schedule", json={
                "name": "imaging", "start_time": "2026-03-11T14:00:00Z",
            })
            resp = await client.delete("/api/schedule/1")
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "deleted"
            resp = await client.get("/api/schedule")
            data = await resp.json()
            assert len(data["schedule"]) == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.delete("/api/schedule/999")
            assert resp.status == 404
        finally:
            await client.close()


# ── GET /api/schedule/validate ─────────────────────────────────────

class TestScheduleValidateEndpoint:
    """Test GET /api/schedule/validate."""

    @pytest.mark.asyncio
    async def test_validate_empty_schedule(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/schedule/validate")
            assert resp.status == 200
            data = await resp.json()
            assert data["valid"] is True
            assert data["issues"] == []
            assert data["activity_count"] == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_validate_with_activities(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            await client.post("/api/schedule", json={
                "name": "imaging", "start_time": "2026-03-11T14:00:00Z",
            })
            resp = await client.get("/api/schedule/validate")
            data = await resp.json()
            assert "valid" in data
            assert "activity_count" in data
            assert data["activity_count"] == 1
        finally:
            await client.close()


# ── GET /api/activity-types ───────────────────────────────────────

class TestActivityTypesEndpoint:
    """Test GET /api/activity-types."""

    @pytest.mark.asyncio
    async def test_api_activity_types(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/activity-types")
            assert resp.status == 200
            data = await resp.json()
            assert "activity_types" in data
            assert isinstance(data["activity_types"], list)
            assert len(data["activity_types"]) == 1
            assert data["activity_types"][0]["name"] == "imaging"
        finally:
            await client.close()

"""Tests for ground track endpoint with offset support.

Covers:
  - GET /api/ground-track with default parameters (uses cache)
  - GET /api/ground-track with offset_minutes=-50 (different start time)
  - GET /api/ground-track with positive offset
  - GET /api/ground-track with custom duration_hours
  - Mock-based testing to avoid real TLE propagation

Follows the existing test pattern from test_planner_server.py using
aiohttp.test_utils.TestServer/TestClient with mocked orbit propagation.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from smo_planner.server import PlannerServer
from smo_planner.activity_scheduler import ActivityScheduler
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
    """Simple ground station stand-in."""
    def __init__(self, name, lat_deg, lon_deg, alt_km, min_elevation_deg):
        self.name = name
        self.lat_deg = lat_deg
        self.lon_deg = lon_deg
        self.alt_km = alt_km
        self.min_elevation_deg = min_elevation_deg


def _make_track_data(start_time=None):
    """Generate synthetic ground track data, optionally starting from a time."""
    base = start_time or datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    return [
        {
            "utc": (base + timedelta(minutes=i)).isoformat(),
            "lat": 45.0 - i * 0.5,
            "lon": -75.0 + i * 1.0,
            "alt_km": 550.0 + i * 0.1,
            "in_eclipse": False,
        }
        for i in range(10)
    ]


def _make_planner_server(track_data=None):
    """Create a PlannerServer with mocked dependencies."""
    mock_state = _make_mock_state()
    gs_ottawa = _SimpleGS(
        name="Ottawa", lat_deg=45.4215, lon_deg=-75.6972,
        alt_km=0.07, min_elevation_deg=5.0,
    )

    mock_prop_instance = MagicMock()
    mock_prop_instance.advance.return_value = mock_state
    mock_prop_instance.contact_windows.return_value = []

    default_track = track_data or _make_track_data()

    mock_planner_instance = MagicMock()
    mock_planner_instance.predict_ground_track.return_value = default_track

    server = PlannerServer.__new__(PlannerServer)
    server.config_dir = Path("/tmp/fake_config")
    server.http_port = 0
    server._gs_list = [gs_ottawa]
    server._tle1 = "1 99999U 26001A   26070.50000000  .00000100  00000-0  10000-3 0  9991"
    server._tle2 = "2 99999  97.5000   0.0000 0010000   0.0000   0.0000 15.00000000    10"
    server._earth_r = 6371.0
    server._orbit_planner = mock_planner_instance
    server._live_prop = mock_prop_instance
    server._activity_types = []
    server._scheduler = ActivityScheduler(server._activity_types)
    server._mcs_url = "http://localhost:9090"
    server._contacts_cache = []
    server._contacts_computed_at = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    server._ground_track_cache = default_track
    server._ground_track_computed_at = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    server._imaging_planner = ImagingPlanner()

    return server


def _build_app(server: PlannerServer) -> web.Application:
    """Build aiohttp Application with ground-track route."""
    app = web.Application()
    app.router.add_get("/api/ground-track", server._handle_ground_track)
    return app


async def _make_client(server: PlannerServer) -> TestClient:
    """Create a TestClient for the given PlannerServer."""
    app = _build_app(server)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# ── Default ground track (no offset, uses cache) ─────────────────

class TestDefaultGroundTrack:
    """GET /api/ground-track with no parameters should return cached data."""

    @pytest.mark.asyncio
    async def test_default_returns_cached_track(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/ground-track")
            assert resp.status == 200
            data = await resp.json()
            assert "ground_track" in data
            assert isinstance(data["ground_track"], list)
            assert len(data["ground_track"]) == 10
            assert "computed_at" in data
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_default_does_not_create_new_propagator(self):
        """Default params (3.0h, 30s, 0 offset) should use cache, not recompute."""
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/ground-track")
            assert resp.status == 200
            # The orbit_planner.predict_ground_track should NOT be called
            # because the handler serves from cache for default params
            data = await resp.json()
            assert "offset_minutes" not in data  # Cache response has no offset field
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_cached_track_has_required_fields(self):
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/ground-track")
            data = await resp.json()
            point = data["ground_track"][0]
            assert "lat" in point
            assert "lon" in point
            assert "utc" in point
            assert "alt_km" in point
        finally:
            await client.close()


# ── Negative offset (look back in time) ──────────────────────────

class TestNegativeOffsetGroundTrack:
    """GET /api/ground-track?offset_minutes=-50 should recompute track."""

    @pytest.mark.asyncio
    async def test_negative_offset_returns_track(self):
        """Request with offset_minutes=-50 should return a valid track."""
        offset_track = _make_track_data(
            datetime(2026, 3, 11, 11, 10, 0, tzinfo=timezone.utc)
        )
        server = _make_planner_server()
        # Mock OrbitPropagator and OrbitPlanner construction for non-cache path
        mock_planner_cls = MagicMock()
        mock_planner_cls.return_value.predict_ground_track.return_value = offset_track

        mock_prop_cls = MagicMock()

        with patch("smo_planner.server.OrbitPropagator", mock_prop_cls), \
             patch("smo_planner.server.OrbitPlanner", mock_planner_cls):
            client = await _make_client(server)
            try:
                resp = await client.get(
                    "/api/ground-track?offset_minutes=-50"
                )
                assert resp.status == 200
                data = await resp.json()
                assert "ground_track" in data
                assert data["offset_minutes"] == -50.0
                assert len(data["ground_track"]) == 10
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_negative_offset_creates_new_propagator(self):
        """Offset != 0 should instantiate a new OrbitPropagator."""
        server = _make_planner_server()
        mock_planner_cls = MagicMock()
        mock_planner_cls.return_value.predict_ground_track.return_value = _make_track_data()
        mock_prop_cls = MagicMock()

        with patch("smo_planner.server.OrbitPropagator", mock_prop_cls), \
             patch("smo_planner.server.OrbitPlanner", mock_planner_cls):
            client = await _make_client(server)
            try:
                await client.get("/api/ground-track?offset_minutes=-50")
                # OrbitPropagator should be constructed
                mock_prop_cls.assert_called_once()
                # OrbitPlanner should be constructed with the propagator
                mock_planner_cls.assert_called_once()
            finally:
                await client.close()


# ── Positive offset (look ahead) ────────────────────────────────

class TestPositiveOffsetGroundTrack:
    """GET /api/ground-track?offset_minutes=30 should compute future track."""

    @pytest.mark.asyncio
    async def test_positive_offset_returns_track(self):
        future_track = _make_track_data(
            datetime(2026, 3, 11, 12, 30, 0, tzinfo=timezone.utc)
        )
        server = _make_planner_server()
        mock_planner_cls = MagicMock()
        mock_planner_cls.return_value.predict_ground_track.return_value = future_track
        mock_prop_cls = MagicMock()

        with patch("smo_planner.server.OrbitPropagator", mock_prop_cls), \
             patch("smo_planner.server.OrbitPlanner", mock_planner_cls):
            client = await _make_client(server)
            try:
                resp = await client.get(
                    "/api/ground-track?offset_minutes=30"
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["offset_minutes"] == 30.0
                assert len(data["ground_track"]) == 10
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_positive_offset_passes_correct_start_time(self):
        """The start_time passed to predict_ground_track should be now + offset."""
        server = _make_planner_server()
        mock_planner_cls = MagicMock()
        mock_planner_cls.return_value.predict_ground_track.return_value = _make_track_data()
        mock_prop_cls = MagicMock()

        with patch("smo_planner.server.OrbitPropagator", mock_prop_cls), \
             patch("smo_planner.server.OrbitPlanner", mock_planner_cls):
            client = await _make_client(server)
            try:
                before = datetime.now(timezone.utc)
                await client.get("/api/ground-track?offset_minutes=60")
                after = datetime.now(timezone.utc)

                # Verify predict_ground_track was called
                planner_inst = mock_planner_cls.return_value
                planner_inst.predict_ground_track.assert_called_once()
                call_args = planner_inst.predict_ground_track.call_args
                start_time = call_args[0][0]  # First positional arg

                # start_time should be approximately now + 60 minutes
                expected_low = before + timedelta(minutes=60)
                expected_high = after + timedelta(minutes=60)
                assert expected_low <= start_time <= expected_high, (
                    f"start_time {start_time} not in expected range "
                    f"[{expected_low}, {expected_high}]"
                )
            finally:
                await client.close()


# ── Custom duration_hours parameter ──────────────────────────────

class TestDurationHoursParameter:
    """GET /api/ground-track?duration_hours=6 should pass custom duration."""

    @pytest.mark.asyncio
    async def test_custom_duration_triggers_recompute(self):
        """Non-default duration_hours should bypass cache."""
        server = _make_planner_server()
        mock_planner_cls = MagicMock()
        mock_planner_cls.return_value.predict_ground_track.return_value = _make_track_data()
        mock_prop_cls = MagicMock()

        with patch("smo_planner.server.OrbitPropagator", mock_prop_cls), \
             patch("smo_planner.server.OrbitPlanner", mock_planner_cls):
            client = await _make_client(server)
            try:
                resp = await client.get(
                    "/api/ground-track?duration_hours=6"
                )
                assert resp.status == 200
                # Verify predict_ground_track was called with duration_hours=6
                planner_inst = mock_planner_cls.return_value
                planner_inst.predict_ground_track.assert_called_once()
                call_kwargs = planner_inst.predict_ground_track.call_args
                assert call_kwargs[1]["duration_hours"] == 6.0
            finally:
                await client.close()

    @pytest.mark.asyncio
    async def test_default_duration_uses_cache(self):
        """duration_hours=3.0 with step_s=30 and offset=0 should use cache."""
        server = _make_planner_server()
        client = await _make_client(server)
        try:
            resp = await client.get(
                "/api/ground-track?duration_hours=3.0&step_s=30&offset_minutes=0"
            )
            assert resp.status == 200
            data = await resp.json()
            # Cache response does not include offset_minutes key
            assert "offset_minutes" not in data
            assert len(data["ground_track"]) == 10
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_custom_step_triggers_recompute(self):
        """Non-default step_s should bypass cache."""
        server = _make_planner_server()
        mock_planner_cls = MagicMock()
        mock_planner_cls.return_value.predict_ground_track.return_value = _make_track_data()
        mock_prop_cls = MagicMock()

        with patch("smo_planner.server.OrbitPropagator", mock_prop_cls), \
             patch("smo_planner.server.OrbitPlanner", mock_planner_cls):
            client = await _make_client(server)
            try:
                resp = await client.get(
                    "/api/ground-track?step_s=10"
                )
                assert resp.status == 200
                planner_inst = mock_planner_cls.return_value
                planner_inst.predict_ground_track.assert_called_once()
                call_kwargs = planner_inst.predict_ground_track.call_args
                assert call_kwargs[1]["step_s"] == 10.0
            finally:
                await client.close()

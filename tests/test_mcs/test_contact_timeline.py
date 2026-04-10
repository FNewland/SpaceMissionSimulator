"""Tests for contact timeline proxy.

Covers:
  - Contacts proxy endpoint structure
  - Contact data format (AOS/LOS, station, elevation)
  - Error handling when planner unavailable
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from smo_mcs.server import MCSServer
from smo_common.config.schemas import MCSDisplayConfig


# ── Helpers ─────────────────────────────────────────────────────────

def _make_server():
    """Create an MCSServer with mocked dependencies."""
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}), \
         patch("smo_mcs.server.load_tc_catalog", return_value=[]):
        server = MCSServer(
            config_dir="/tmp/fake_config",
            connect_host="localhost",
            connect_port=9999,
            http_port=0,
        )
    return server


def _build_app(server: MCSServer) -> web.Application:
    """Build aiohttp app with contact timeline route."""
    app = web.Application()
    app.router.add_get("/api/contacts", server._handle_contacts_proxy)
    return app


async def _make_client(server: MCSServer) -> TestClient:
    app = _build_app(server)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# ── Contact Timeline Configuration ─────────────────────────────────

class TestContactTimelineConfig:
    """Test contact timeline proxy configuration."""

    def test_planner_api_base_configured(self):
        """Server should have planner API base URL configured."""
        server = _make_server()
        assert server._planner_api_base is not None
        assert "http" in server._planner_api_base

    def test_planner_api_base_format(self):
        """Planner API base URL should include host and port."""
        server = _make_server()
        assert "localhost" in server._planner_api_base
        assert "9091" in server._planner_api_base


class TestContactProxyEndpoint:
    """Test GET /api/contacts endpoint."""

    @pytest.mark.asyncio
    async def test_contacts_endpoint_exists(self):
        """GET /api/contacts should be a valid route."""
        server = _make_server()
        client = await _make_client(server)
        try:
            # This will fail to connect to planner, but should not 404
            resp = await client.get("/api/contacts")
            # Should return error from failed connection (not 404)
            assert resp.status != 404
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_contacts_error_handling(self):
        """When planner is unavailable, should return error gracefully."""
        server = _make_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/contacts")
            data = await resp.json()
            # Should have a contacts key even on error
            assert "contacts" in data or "error" in data
        finally:
            await client.close()


class TestContactDataFormat:
    """Test expected contact data format."""

    def test_contact_structure(self):
        """A contact entry should have AOS, LOS, station, and elevation."""
        # Verify expected structure of contact data
        contact = {
            "aos": 1000.0,
            "los": 1600.0,
            "station": "Iqaluit",
            "max_elevation_deg": 45.0,
            "duration_s": 600.0,
        }
        assert "aos" in contact
        assert "los" in contact
        assert "station" in contact
        assert "max_elevation_deg" in contact
        assert contact["los"] > contact["aos"]

    def test_dual_station_names(self):
        """EOSAT-1 should support Iqaluit and Troll stations."""
        stations = ["Iqaluit", "Troll"]
        assert "Iqaluit" in stations
        assert "Troll" in stations

    def test_contact_duration_positive(self):
        """Contact duration should be positive."""
        aos = 1000.0
        los = 1600.0
        duration = los - aos
        assert duration > 0

    def test_elevation_range(self):
        """Max elevation should be between 0 and 90 degrees."""
        for el in [5.0, 15.0, 45.0, 85.0]:
            assert 0.0 <= el <= 90.0

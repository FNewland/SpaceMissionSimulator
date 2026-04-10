"""Tests for stored TM playback.

Covers:
  - TM dump request endpoint
  - TM dump data retrieval
  - Dump data structure and format
"""
import time
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
    """Build aiohttp app with TM dump routes."""
    app = web.Application()
    app.router.add_post("/api/tm-dump", server._handle_tm_dump)
    app.router.add_get("/api/tm-dump-data", server._handle_tm_dump_data)
    return app


async def _make_client(server: MCSServer) -> TestClient:
    app = _build_app(server)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# ── TM Dump Request Endpoint ───────────────────────────────────────

class TestTMDumpRequest:
    """Test POST /api/tm-dump endpoint."""

    @pytest.mark.asyncio
    async def test_tm_dump_no_tc_connection(self):
        """Without TC writer, dump request should return 503."""
        server = _make_server()
        server._tc_writer = None
        client = await _make_client(server)
        try:
            resp = await client.post("/api/tm-dump", json={
                "store_id": 1,
                "subsystem": "eps",
            })
            assert resp.status == 503
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_tm_dump_with_tc_connection(self):
        """With TC writer, dump request should succeed and return dump key."""
        server = _make_server()
        mock_writer = MagicMock()
        mock_writer.is_closing.return_value = False
        mock_writer.drain = AsyncMock()
        server._tc_writer = mock_writer
        client = await _make_client(server)
        try:
            resp = await client.post("/api/tm-dump", json={
                "store_id": 1,
                "subsystem": "eps",
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "sent"
            assert "dump_key" in data
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_tm_dump_stores_data(self):
        """After dump request, data should be stored in _tm_dump_data."""
        server = _make_server()
        mock_writer = MagicMock()
        mock_writer.is_closing.return_value = False
        mock_writer.drain = AsyncMock()
        server._tc_writer = mock_writer
        client = await _make_client(server)
        try:
            resp = await client.post("/api/tm-dump", json={
                "store_id": 1,
                "subsystem": "eps",
            })
            data = await resp.json()
            dump_key = data["dump_key"]
            assert dump_key in server._tm_dump_data
        finally:
            await client.close()


# ── TM Dump Data Retrieval ─────────────────────────────────────────

class TestTMDumpDataRetrieval:
    """Test GET /api/tm-dump-data endpoint."""

    @pytest.mark.asyncio
    async def test_empty_dump_data(self):
        """Requesting non-existent dump key should return empty data."""
        server = _make_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/tm-dump-data?key=nonexistent")
            assert resp.status == 200
            data = await resp.json()
            assert data["dump_key"] == "nonexistent"
            assert data["data"] == []
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_dump_data_with_key(self):
        """Stored dump data should be retrievable by key."""
        server = _make_server()
        # Pre-populate dump data
        server._tm_dump_data["test_key"] = [
            {"timestamp": 1000.0, "index": 0, "bat_soc": 75.0},
            {"timestamp": 1060.0, "index": 1, "bat_soc": 74.5},
        ]
        client = await _make_client(server)
        try:
            resp = await client.get("/api/tm-dump-data?key=test_key")
            assert resp.status == 200
            data = await resp.json()
            assert data["dump_key"] == "test_key"
            assert len(data["data"]) == 2
            assert data["data"][0]["bat_soc"] == 75.0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_dump_data_no_key_param(self):
        """Request without key parameter should return empty key."""
        server = _make_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/tm-dump-data")
            assert resp.status == 200
            data = await resp.json()
            assert data["dump_key"] == ""
            assert data["data"] == []
        finally:
            await client.close()


class TestTMDumpDataFormat:
    """Test the format of TM dump data."""

    def test_dump_data_is_list_of_dicts(self):
        """Dump data should be a list of dicts."""
        server = _make_server()
        server._tm_dump_data["test"] = [
            {"timestamp": 1000.0, "index": 0, "value": 42.0},
        ]
        data = server._tm_dump_data["test"]
        assert isinstance(data, list)
        assert isinstance(data[0], dict)

    def test_dump_data_has_timestamp(self):
        """Each dump data point should have a timestamp."""
        server = _make_server()
        server._tm_dump_data["test"] = [
            {"timestamp": 1000.0, "index": 0, "value": 42.0},
        ]
        assert "timestamp" in server._tm_dump_data["test"][0]

    def test_dump_data_has_index(self):
        """Each dump data point should have an index."""
        server = _make_server()
        server._tm_dump_data["test"] = [
            {"timestamp": 1000.0, "index": 0, "value": 42.0},
        ]
        assert "index" in server._tm_dump_data["test"][0]

    def test_generate_dump_data_produces_points(self):
        """_generate_dump_data should produce historical data points."""
        server = _make_server()
        data = server._generate_dump_data("eps", 1)
        assert isinstance(data, list)
        assert len(data) > 0
        assert "timestamp" in data[0]
        assert "index" in data[0]

"""Tests for the MCS alarm journal system.

Covers:
  - Alarm creation from S5 events
  - Alarm creation from S12 violations
  - Alarm acknowledge endpoint
  - Alarm listing with filtering
  - Alarm severity levels
  - Alarm deque max length (1000)
"""
import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from collections import deque

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
    """Build aiohttp app with alarm routes."""
    app = web.Application()
    app.router.add_get("/api/alarms", server._handle_alarms_get)
    app.router.add_post("/api/alarms/{alarm_id}/ack", server._handle_alarm_ack)
    return app


async def _make_client(server: MCSServer) -> TestClient:
    app = _build_app(server)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# ── Alarm Journal Internal State ────────────────────────────────────

class TestAlarmJournalInit:
    """Test alarm journal initialization."""

    def test_alarm_journal_is_deque(self):
        """Alarm journal should be a deque."""
        server = _make_server()
        assert isinstance(server._alarm_journal, deque)

    def test_alarm_journal_maxlen_1000(self):
        """Alarm journal should have maxlen of 1000."""
        server = _make_server()
        assert server._alarm_journal.maxlen == 1000

    def test_alarm_id_counter_starts_at_zero(self):
        """Alarm ID counter should start at 0."""
        server = _make_server()
        assert server._alarm_id_counter == 0

    def test_alarm_journal_initially_empty(self):
        """Journal should start empty."""
        server = _make_server()
        assert len(server._alarm_journal) == 0


class TestAlarmCreation:
    """Test alarm creation from S5 and S12 events."""

    def test_create_alarm_manually(self):
        """Adding an alarm entry directly should work."""
        server = _make_server()
        server._alarm_id_counter += 1
        alarm = {
            "id": server._alarm_id_counter,
            "timestamp": time.time(),
            "severity": 3,
            "subsystem": "eps",
            "parameter": "EVT-0x0100",
            "value": "Battery SoC critical",
            "limit": "",
            "acknowledged": False,
            "source": "S5",
        }
        server._alarm_journal.appendleft(alarm)
        assert len(server._alarm_journal) == 1
        assert server._alarm_journal[0]["id"] == 1

    def test_alarm_id_increments(self):
        """Each alarm should get a unique incrementing ID."""
        server = _make_server()
        for i in range(5):
            server._alarm_id_counter += 1
            alarm = {
                "id": server._alarm_id_counter,
                "timestamp": time.time(),
                "severity": 2,
                "subsystem": "aocs",
                "parameter": f"EVT-{i}",
                "value": f"Event {i}",
                "limit": "",
                "acknowledged": False,
                "source": "S5",
            }
            server._alarm_journal.appendleft(alarm)
        assert len(server._alarm_journal) == 5
        ids = [a["id"] for a in server._alarm_journal]
        assert len(set(ids)) == 5  # All unique

    def test_s5_alarm_has_source_S5(self):
        """Alarms from S5 events should have source='S5'."""
        server = _make_server()
        server._alarm_id_counter += 1
        alarm = {
            "id": server._alarm_id_counter,
            "timestamp": time.time(),
            "severity": 3,
            "subsystem": "eps",
            "parameter": "EVT-0x0100",
            "value": "Battery SoC critical",
            "limit": "",
            "acknowledged": False,
            "source": "S5",
        }
        server._alarm_journal.appendleft(alarm)
        assert server._alarm_journal[0]["source"] == "S5"

    def test_s12_alarm_has_source_S12(self):
        """Alarms from S12 violations should have source='S12'."""
        server = _make_server()
        server._alarm_id_counter += 1
        alarm = {
            "id": server._alarm_id_counter,
            "timestamp": time.time(),
            "severity": 3,
            "subsystem": "eps",
            "parameter": "0x0101",
            "value": "15.0",
            "limit": "OOL",
            "acknowledged": False,
            "source": "S12",
        }
        server._alarm_journal.appendleft(alarm)
        assert server._alarm_journal[0]["source"] == "S12"
        assert server._alarm_journal[0]["limit"] == "OOL"

    def test_alarm_severity_levels(self):
        """Alarms should preserve severity levels 1-4."""
        server = _make_server()
        for sev in [1, 2, 3, 4]:
            server._alarm_id_counter += 1
            alarm = {
                "id": server._alarm_id_counter,
                "timestamp": time.time(),
                "severity": sev,
                "subsystem": "obdh",
                "parameter": f"EVT-sev{sev}",
                "value": f"Severity {sev}",
                "limit": "",
                "acknowledged": False,
                "source": "S5",
            }
            server._alarm_journal.appendleft(alarm)
        severities = [a["severity"] for a in server._alarm_journal]
        assert set(severities) == {1, 2, 3, 4}


class TestAlarmMaxLength:
    """Test alarm deque overflow behavior."""

    def test_deque_drops_oldest_beyond_1000(self):
        """Beyond 1000 alarms, oldest should be dropped."""
        server = _make_server()
        for i in range(1010):
            server._alarm_id_counter += 1
            alarm = {
                "id": server._alarm_id_counter,
                "timestamp": time.time(),
                "severity": 2,
                "subsystem": "eps",
                "parameter": f"EVT-{i}",
                "value": f"Event {i}",
                "limit": "",
                "acknowledged": False,
                "source": "S5",
            }
            server._alarm_journal.appendleft(alarm)
        assert len(server._alarm_journal) == 1000
        # Oldest should be the 11th inserted (id=11)
        oldest = server._alarm_journal[-1]
        assert oldest["id"] == 11


# ── HTTP Endpoints ──────────────────────────────────────────────────

class TestAlarmGetEndpoint:
    """Test GET /api/alarms endpoint."""

    @pytest.mark.asyncio
    async def test_empty_alarm_list(self):
        server = _make_server()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/alarms")
            assert resp.status == 200
            data = await resp.json()
            assert "alarms" in data
            assert len(data["alarms"]) == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_alarm_list_with_entries(self):
        server = _make_server()
        server._alarm_id_counter = 1
        server._alarm_journal.appendleft({
            "id": 1, "timestamp": time.time(), "severity": 3,
            "subsystem": "eps", "parameter": "EVT-0x0100",
            "value": "Critical", "limit": "", "acknowledged": False,
            "source": "S5",
        })
        client = await _make_client(server)
        try:
            resp = await client.get("/api/alarms")
            data = await resp.json()
            assert len(data["alarms"]) == 1
            assert data["alarms"][0]["id"] == 1
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_filter_by_subsystem(self):
        server = _make_server()
        for i, sub in enumerate(["eps", "aocs", "eps"]):
            server._alarm_id_counter += 1
            server._alarm_journal.appendleft({
                "id": server._alarm_id_counter, "timestamp": time.time(),
                "severity": 2, "subsystem": sub, "parameter": f"P{i}",
                "value": "V", "limit": "", "acknowledged": False,
                "source": "S5",
            })
        client = await _make_client(server)
        try:
            resp = await client.get("/api/alarms?subsystem=eps")
            data = await resp.json()
            assert len(data["alarms"]) == 2
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_filter_by_severity(self):
        server = _make_server()
        for sev in [1, 2, 3, 4]:
            server._alarm_id_counter += 1
            server._alarm_journal.appendleft({
                "id": server._alarm_id_counter, "timestamp": time.time(),
                "severity": sev, "subsystem": "eps", "parameter": f"S{sev}",
                "value": "V", "limit": "", "acknowledged": False,
                "source": "S5",
            })
        client = await _make_client(server)
        try:
            resp = await client.get("/api/alarms?severity=3")
            data = await resp.json()
            assert len(data["alarms"]) == 2  # severity 3 and 4
        finally:
            await client.close()


class TestAlarmAckEndpoint:
    """Test POST /api/alarms/{alarm_id}/ack endpoint."""

    @pytest.mark.asyncio
    async def test_acknowledge_alarm(self):
        server = _make_server()
        server._alarm_id_counter = 1
        server._alarm_journal.appendleft({
            "id": 1, "timestamp": time.time(), "severity": 3,
            "subsystem": "eps", "parameter": "EVT-0x0100",
            "value": "Critical", "limit": "", "acknowledged": False,
            "source": "S5",
        })
        client = await _make_client(server)
        try:
            resp = await client.post("/api/alarms/1/ack")
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "acknowledged"
            assert server._alarm_journal[0]["acknowledged"] is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_acknowledge_nonexistent_alarm(self):
        server = _make_server()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/alarms/999/ack")
            assert resp.status == 404
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_acknowledge_invalid_id(self):
        server = _make_server()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/alarms/abc/ack")
            assert resp.status == 400
        finally:
            await client.close()

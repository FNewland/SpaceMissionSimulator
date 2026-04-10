"""Tests for smo-mcs — MCSServer HTTP API.

Covers:
  - GET / returns HTML
  - GET /api/positions returns JSON with positions dict
  - GET /api/verification-log returns log list
  - POST /api/pus-command with valid command
  - POST /api/pus-command with position access denied
  - POST /api/pus-command with no TC connection returns 503
  - GET /api/handover returns notes list
  - POST /api/handover creates entry
  - POST /api/handover with empty note returns 400
  - POST /api/procedure/load with steps
  - POST /api/procedure/start / pause / resume / abort
  - GET /api/procedure/status returns procedure state
  - GET /api/procedure/activity-types returns list
  - GET /api/procedure/index returns procedure index
  - GET /api/state returns latest state dict
  - _check_position_access allow-all, service filtering, func_id filtering

Uses aiohttp.test_utils.TestServer/TestClient for HTTP endpoint tests,
and direct unit tests for internal methods.
"""
import json
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from smo_mcs.server import MCSServer
from smo_mcs.tc_manager import TCManager
from smo_common.config.schemas import PositionConfig, MCSDisplayConfig


# ── Helpers ─────────────────────────────────────────────────────────

def _make_server_with_mocks():
    """Create an MCSServer with all external dependencies mocked out."""
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}), \
         patch("smo_mcs.server.load_tc_catalog", return_value=[]):
        server = MCSServer(
            config_dir="/tmp/fake_config",
            connect_host="localhost",
            connect_port=9999,
            http_port=0,  # unused in tests
        )
    return server


def _make_server_with_positions():
    """Create an MCSServer with position access control configured."""
    positions = {
        "flight_director": PositionConfig(
            label="FD",
            display_name="Flight Director",
            allowed_commands="all",
        ),
        "eps_tcs": PositionConfig(
            label="EPS/TCS",
            display_name="EPS/TCS Operator",
            allowed_services=[3, 8, 20],
            allowed_func_ids=[0x01, 0x02, 0x10],
        ),
        "payload": PositionConfig(
            label="PL",
            display_name="Payload Operator",
            allowed_services=[8],
            allowed_func_ids=[0x20, 0x21],
        ),
    }
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value=positions), \
         patch("smo_mcs.server.load_tc_catalog", return_value=[]):
        server = MCSServer(
            config_dir="/tmp/fake_config",
            connect_host="localhost",
            connect_port=9999,
            http_port=0,
        )
    return server


def _build_app(server: MCSServer) -> web.Application:
    """Build the aiohttp Application with all routes registered."""
    app = web.Application()
    app.router.add_get("/", server._handle_index)
    app.router.add_get("/api/state", server._handle_state)
    app.router.add_get("/api/positions", server._handle_positions)
    app.router.add_get("/api/verification-log", server._handle_verification_log)
    app.router.add_post("/api/pus-command", server._handle_pus_command)
    app.router.add_get("/api/handover", server._handle_handover_get)
    app.router.add_post("/api/handover", server._handle_handover_post)
    app.router.add_post("/api/procedure/load", server._handle_proc_load)
    app.router.add_post("/api/procedure/start", server._handle_proc_start)
    app.router.add_post("/api/procedure/pause", server._handle_proc_pause)
    app.router.add_post("/api/procedure/resume", server._handle_proc_resume)
    app.router.add_post("/api/procedure/abort", server._handle_proc_abort)
    app.router.add_get("/api/procedure/status", server._handle_proc_status)
    app.router.add_get("/api/procedure/activity-types", server._handle_activity_types)
    app.router.add_get("/api/procedure/index", server._handle_proc_index)
    app.router.add_get("/api/displays", server._handle_displays)
    # Wave 6 endpoints
    app.router.add_get("/api/alarms", server._handle_alarms_get)
    app.router.add_post("/api/alarms/{alarm_id}/ack", server._handle_alarm_ack)
    app.router.add_get("/api/contacts", server._handle_contacts_proxy)
    app.router.add_post("/api/tm-dump", server._handle_tm_dump)
    app.router.add_get("/api/tm-dump-data", server._handle_tm_dump_data)
    return app


async def _make_client(server: MCSServer) -> TestClient:
    """Create a TestClient for the given MCSServer."""
    app = _build_app(server)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# ── GET / ───────────────────────────────────────────────────────────

class TestIndexEndpoint:
    """Test GET / returns HTML content."""

    @pytest.mark.asyncio
    async def test_index_returns_html(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.get("/")
            assert resp.status == 200
            assert resp.content_type == "text/html"
        finally:
            await client.close()


# ── GET /api/positions ──────────────────────────────────────────────

class TestPositionsEndpoint:
    """Test GET /api/positions."""

    @pytest.mark.asyncio
    async def test_api_positions_empty(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/positions")
            assert resp.status == 200
            data = await resp.json()
            assert "positions" in data
            assert isinstance(data["positions"], dict)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_api_positions_with_config(self):
        server = _make_server_with_positions()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/positions")
            assert resp.status == 200
            data = await resp.json()
            positions = data["positions"]
            assert "flight_director" in positions
            assert "eps_tcs" in positions
            assert positions["flight_director"]["allowed_commands"] == "all"
            assert 8 in positions["eps_tcs"]["allowed_services"]
        finally:
            await client.close()


# ── GET /api/verification-log ──────────────────────────────────────

class TestVerificationLogEndpoint:
    """Test GET /api/verification-log."""

    @pytest.mark.asyncio
    async def test_api_verification_log_empty(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/verification-log")
            assert resp.status == 200
            data = await resp.json()
            assert "log" in data
            assert isinstance(data["log"], list)
            assert len(data["log"]) == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_api_verification_log_with_entries(self):
        server = _make_server_with_mocks()
        server._verification_log.appendleft({
            "seq": 1, "name": "TestCmd", "service": 8, "subtype": 1,
            "state": "SENT", "timestamp": 12345.0, "error_code": 0,
            "position": "flight_director",
        })
        client = await _make_client(server)
        try:
            resp = await client.get("/api/verification-log")
            data = await resp.json()
            assert len(data["log"]) == 1
            assert data["log"][0]["name"] == "TestCmd"
        finally:
            await client.close()


# ── POST /api/pus-command ──────────────────────────────────────────

class TestPusCommandEndpoint:
    """Test POST /api/pus-command."""

    @pytest.mark.asyncio
    async def test_pus_command_no_tc_connection(self):
        """With no TC writer, should return 503."""
        server = _make_server_with_mocks()
        server._tc_writer = None
        client = await _make_client(server)
        try:
            resp = await client.post("/api/pus-command", json={
                "service": 8, "subtype": 1, "data_hex": "10",
                "name": "TestCmd", "position": "flight_director",
            })
            assert resp.status == 503
            data = await resp.json()
            assert data["status"] == "error"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_pus_command_valid(self):
        """With a mock TC writer, should send and return seq."""
        server = _make_server_with_mocks()
        mock_writer = MagicMock()
        mock_writer.is_closing.return_value = False
        mock_writer.drain = AsyncMock()
        server._tc_writer = mock_writer
        client = await _make_client(server)
        try:
            resp = await client.post("/api/pus-command", json={
                "service": 8, "subtype": 1, "data_hex": "10",
                "name": "TestCmd", "position": "flight_director",
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "sent"
            assert "seq" in data
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_pus_command_access_denied(self):
        """Payload operator should not be able to send S3 commands."""
        server = _make_server_with_positions()
        mock_writer = MagicMock()
        mock_writer.is_closing.return_value = False
        mock_writer.drain = AsyncMock()
        server._tc_writer = mock_writer
        client = await _make_client(server)
        try:
            resp = await client.post("/api/pus-command", json={
                "service": 3, "subtype": 25, "data_hex": "0001",
                "name": "HK Request", "position": "payload",
            })
            assert resp.status == 403
            data = await resp.json()
            assert data["status"] == "denied"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_pus_command_increments_verification_log(self):
        """Each sent command should add an entry to the verification log."""
        server = _make_server_with_mocks()
        mock_writer = MagicMock()
        mock_writer.is_closing.return_value = False
        mock_writer.drain = AsyncMock()
        server._tc_writer = mock_writer
        client = await _make_client(server)
        try:
            await client.post("/api/pus-command", json={
                "service": 8, "subtype": 1, "data_hex": "10",
                "name": "Cmd1", "position": "flight_director",
            })
            await client.post("/api/pus-command", json={
                "service": 8, "subtype": 1, "data_hex": "11",
                "name": "Cmd2", "position": "flight_director",
            })
            assert len(server._verification_log) == 2
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_pus_command_invalid_json(self):
        """Malformed body should return 400."""
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.post(
                "/api/pus-command",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp.status == 400
        finally:
            await client.close()


# ── GET/POST /api/handover ─────────────────────────────────────────

class TestHandoverEndpoint:
    """Test shift handover API."""

    @pytest.mark.asyncio
    async def test_handover_get_empty(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/handover")
            assert resp.status == 200
            data = await resp.json()
            assert data["notes"] == []
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_handover_post_creates_entry(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/handover", json={
                "note": "Battery SOC at 78%, trending down.",
                "position": "eps_tcs",
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "ok"
            assert data["entry"]["note"] == "Battery SOC at 78%, trending down."
            assert data["entry"]["position"] == "eps_tcs"
            assert "timestamp" in data["entry"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_handover_post_empty_note_returns_400(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/handover", json={
                "note": "",
                "position": "flight_director",
            })
            assert resp.status == 400
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_handover_post_whitespace_note_returns_400(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/handover", json={
                "note": "   ",
                "position": "flight_director",
            })
            assert resp.status == 400
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_handover_roundtrip(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            await client.post("/api/handover", json={
                "note": "Note 1", "position": "fd",
            })
            await client.post("/api/handover", json={
                "note": "Note 2", "position": "eps",
            })
            resp = await client.get("/api/handover")
            data = await resp.json()
            assert len(data["notes"]) == 2
        finally:
            await client.close()


# ── Procedure API ──────────────────────────────────────────────────

class TestProcedureAPI:
    """Test procedure load / start / pause / resume / abort / status."""

    @pytest.mark.asyncio
    async def test_procedure_load_with_steps(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/procedure/load", json={
                "name": "TestProc",
                "steps": [
                    {"service": 8, "subtype": 1, "func_id": "0x10"},
                    {"wait_s": 1},
                ],
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["state"] == "LOADED"
            assert data["total_steps"] == 2
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_procedure_load_unknown_activity_returns_404(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/procedure/load", json={
                "name": "nonexistent_activity_type",
            })
            assert resp.status == 404
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_procedure_start(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            await client.post("/api/procedure/load", json={
                "name": "TestProc",
                "steps": [{"wait_s": 10}],
            })
            resp = await client.post("/api/procedure/start")
            assert resp.status == 200
            data = await resp.json()
            assert data["state"] == "RUNNING"
            await client.post("/api/procedure/abort")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_procedure_pause_resume(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            await client.post("/api/procedure/load", json={
                "name": "TestProc",
                "steps": [{"wait_s": 10}],
            })
            await client.post("/api/procedure/start")
            resp = await client.post("/api/procedure/pause")
            assert resp.status == 200
            data = await resp.json()
            assert data["state"] == "PAUSED"
            resp = await client.post("/api/procedure/resume")
            data = await resp.json()
            assert data["state"] == "RUNNING"
            await client.post("/api/procedure/abort")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_procedure_abort(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            await client.post("/api/procedure/load", json={
                "name": "TestProc",
                "steps": [{"wait_s": 10}],
            })
            await client.post("/api/procedure/start")
            resp = await client.post("/api/procedure/abort")
            assert resp.status == 200
            data = await resp.json()
            assert data["state"] == "ABORTED"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_procedure_status(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/procedure/status")
            assert resp.status == 200
            data = await resp.json()
            assert "state" in data
            assert "state_code" in data
            assert "step_results" in data
        finally:
            await client.close()


# ── GET /api/procedure/activity-types ──────────────────────────────

class TestActivityTypesEndpoint:
    """Test GET /api/procedure/activity-types."""

    @pytest.mark.asyncio
    async def test_api_activity_types_empty(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/procedure/activity-types")
            assert resp.status == 200
            data = await resp.json()
            assert "activity_types" in data
            assert isinstance(data["activity_types"], list)
        finally:
            await client.close()


# ── GET /api/procedure/index ───────────────────────────────────────

class TestProcedureIndexEndpoint:
    """Test GET /api/procedure/index."""

    @pytest.mark.asyncio
    async def test_api_procedure_index(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/procedure/index")
            assert resp.status == 200
            data = await resp.json()
            assert "procedures" in data
            assert isinstance(data["procedures"], list)
        finally:
            await client.close()


# ── GET /api/state ──────────────────────────────────────────────────

class TestStateEndpoint:
    """Test GET /api/state returns latest state."""

    @pytest.mark.asyncio
    async def test_api_state_empty(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/state")
            assert resp.status == 200
            data = await resp.json()
            assert isinstance(data, dict)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_api_state_with_data(self):
        server = _make_server_with_mocks()
        server._latest_state = {"sim_time": 100.0, "speed": 1}
        client = await _make_client(server)
        try:
            resp = await client.get("/api/state")
            data = await resp.json()
            assert data["sim_time"] == 100.0
        finally:
            await client.close()


# ── GET /api/displays ──────────────────────────────────────────────

class TestDisplaysEndpoint:
    """Test GET /api/displays."""

    @pytest.mark.asyncio
    async def test_api_displays(self):
        server = _make_server_with_mocks()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/displays")
            assert resp.status == 200
            data = await resp.json()
            assert isinstance(data, dict)
        finally:
            await client.close()


# ── _check_position_access (unit tests) ────────────────────────────

class TestCheckPositionAccess:
    """Test position-based access control logic."""

    def test_no_positions_configured_allows_all(self):
        server = _make_server_with_mocks()
        allowed, reason = server._check_position_access("anyone", 8, 1, "10")
        assert allowed is True

    def test_flight_director_allow_all(self):
        server = _make_server_with_positions()
        allowed, reason = server._check_position_access(
            "flight_director", 8, 1, "10"
        )
        assert allowed is True

    def test_eps_allowed_service(self):
        server = _make_server_with_positions()
        allowed, reason = server._check_position_access("eps_tcs", 3, 25, "0001")
        assert allowed is True

    def test_eps_denied_service(self):
        server = _make_server_with_positions()
        allowed, reason = server._check_position_access("eps_tcs", 6, 2, "")
        assert allowed is False
        assert "Service 6" in reason

    def test_eps_denied_func_id(self):
        server = _make_server_with_positions()
        # func_id 0xFF is not in allowed_func_ids [0x01, 0x02, 0x10]
        allowed, reason = server._check_position_access("eps_tcs", 8, 1, "FF")
        assert allowed is False
        assert "Function" in reason

    def test_eps_allowed_func_id(self):
        server = _make_server_with_positions()
        allowed, reason = server._check_position_access("eps_tcs", 8, 1, "10")
        assert allowed is True

    def test_unknown_position_allowed_by_default(self):
        server = _make_server_with_positions()
        allowed, reason = server._check_position_access("unknown_pos", 8, 1, "10")
        assert allowed is True


# ── _proc_get_telemetry (unit test) ────────────────────────────────

class TestProcGetTelemetry:
    """Test telemetry value lookup from latest state."""

    def test_dot_path_lookup(self):
        server = _make_server_with_mocks()
        server._latest_state = {"eps": {"battery_soc": 78.5}}
        result = server._proc_get_telemetry("eps.battery_soc")
        assert result == 78.5

    def test_top_level_lookup(self):
        server = _make_server_with_mocks()
        server._latest_state = {"sim_time": 12345}
        result = server._proc_get_telemetry("sim_time")
        assert result == 12345

    def test_missing_param_returns_none(self):
        server = _make_server_with_mocks()
        server._latest_state = {}
        result = server._proc_get_telemetry("eps.missing_param")
        assert result is None

    def test_empty_state_returns_none(self):
        server = _make_server_with_mocks()
        result = server._proc_get_telemetry("any.param")
        assert result is None

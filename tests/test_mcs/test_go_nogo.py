"""Tests for GO/NO-GO coordination API.

Covers:
  - POST /api/go-nogo/poll — poll initiation by flight_director
  - POST /api/go-nogo/poll — non-FD position returns 403
  - POST /api/go-nogo/respond — position response recording
  - POST /api/go-nogo/respond — invalid response rejected
  - POST /api/go-nogo/respond — result aggregation (all GO vs any NO-GO)
  - GET /api/go-nogo/status — returns current state

Uses aiohttp.test_utils.TestServer/TestClient following the pattern from
test_mcs_server.py.
"""
import json
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from smo_mcs.server import MCSServer
from smo_common.config.schemas import PositionConfig, MCSDisplayConfig


# ── Helpers ─────────────────────────────────────────────────────────

def _make_server_with_positions():
    """Create an MCSServer with position access control configured.

    Includes all 6 positions so GO/NO-GO aggregation can be fully tested.
    """
    positions = {
        "flight_director": PositionConfig(
            label="FD",
            display_name="Flight Director",
            allowed_commands="all",
        ),
        "eps_tcs": PositionConfig(
            label="EPS/TCS",
            display_name="Power & Thermal",
            allowed_services=[3, 8, 20],
            allowed_func_ids=[0x01, 0x02],
        ),
        "aocs": PositionConfig(
            label="AOCS",
            display_name="Flight Dynamics",
            allowed_services=[3, 8, 11],
            allowed_func_ids=[0x00, 0x01],
        ),
        "ttc": PositionConfig(
            label="TTC",
            display_name="TT&C",
            allowed_services=[3, 8, 9],
            allowed_func_ids=[0x50, 0x51],
        ),
        "payload_ops": PositionConfig(
            label="PL",
            display_name="Payload Operations",
            allowed_services=[8],
            allowed_func_ids=[0x20, 0x21],
        ),
        "fdir_systems": PositionConfig(
            label="FDIR",
            display_name="FDIR / Systems",
            allowed_services=[3, 6, 8, 12],
            allowed_func_ids=[0x40, 0x41],
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


def _make_server_minimal():
    """Create an MCSServer with minimal positions (for simpler tests)."""
    positions = {
        "flight_director": PositionConfig(
            label="FD",
            display_name="Flight Director",
            allowed_commands="all",
        ),
        "eps_tcs": PositionConfig(
            label="EPS/TCS",
            display_name="Power & Thermal",
            allowed_services=[3, 8],
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
    """Build aiohttp Application with GO/NO-GO routes."""
    app = web.Application()
    app.router.add_get("/api/go-nogo/status", server._handle_go_nogo_status)
    app.router.add_post("/api/go-nogo/poll", server._handle_go_nogo_poll)
    app.router.add_post("/api/go-nogo/respond", server._handle_go_nogo_respond)
    return app


async def _make_client(server: MCSServer) -> TestClient:
    """Create a TestClient for the given MCSServer."""
    app = _build_app(server)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# ── POST /api/go-nogo/poll — initiation ──────────────────────────

class TestGoNogoPollInitiation:
    """Test GO/NO-GO poll initiation."""

    @pytest.mark.asyncio
    async def test_flight_director_can_initiate_poll(self):
        """Flight Director should be able to start a GO/NO-GO poll."""
        server = _make_server_with_positions()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Pre-pass GO/NO-GO",
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "poll_started"
            assert data["label"] == "Pre-pass GO/NO-GO"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_poll_sets_active_state(self):
        """After poll initiation, internal state should be active."""
        server = _make_server_with_positions()
        client = await _make_client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Test Poll",
            })
            assert server._go_nogo_active is True
            assert server._go_nogo_label == "Test Poll"
            assert server._go_nogo_initiator == "flight_director"
            # FD auto-responds GO
            assert server._go_nogo_responses.get("flight_director") == "GO"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_non_fd_cannot_initiate_poll(self):
        """Non-FD positions should get 403 when trying to start a poll."""
        server = _make_server_with_positions()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/go-nogo/poll", json={
                "position": "eps_tcs",
                "label": "Unauthorized Poll",
            })
            assert resp.status == 403
            data = await resp.json()
            assert "error" in data
            assert "Flight Director" in data["error"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_aocs_cannot_initiate_poll(self):
        """AOCS operator should get 403 when trying to start a poll."""
        server = _make_server_with_positions()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/go-nogo/poll", json={
                "position": "aocs",
                "label": "AOCS Poll",
            })
            assert resp.status == 403
        finally:
            await client.close()


# ── POST /api/go-nogo/respond — response recording ──────────────

class TestGoNogoRespond:
    """Test GO/NO-GO response submission."""

    @pytest.mark.asyncio
    async def test_position_can_respond_go(self):
        """A position should be able to respond GO to an active poll."""
        server = _make_server_minimal()
        client = await _make_client(server)
        try:
            # Start a poll
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Test",
            })
            # Respond
            resp = await client.post("/api/go-nogo/respond", json={
                "position": "eps_tcs",
                "response": "GO",
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "recorded"
            assert data["position"] == "eps_tcs"
            assert data["response"] == "GO"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_position_can_respond_nogo(self):
        """A position should be able to respond NOGO."""
        server = _make_server_minimal()
        client = await _make_client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Test",
            })
            resp = await client.post("/api/go-nogo/respond", json={
                "position": "eps_tcs",
                "response": "NOGO",
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["response"] == "NOGO"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_position_can_respond_standby(self):
        """A position should be able to respond STANDBY."""
        server = _make_server_minimal()
        client = await _make_client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Test",
            })
            resp = await client.post("/api/go-nogo/respond", json={
                "position": "eps_tcs",
                "response": "STANDBY",
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["response"] == "STANDBY"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_response_case_insensitive(self):
        """Responses should be accepted case-insensitively."""
        server = _make_server_minimal()
        client = await _make_client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Test",
            })
            resp = await client.post("/api/go-nogo/respond", json={
                "position": "eps_tcs",
                "response": "go",  # lowercase
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["response"] == "GO"
        finally:
            await client.close()


# ── Invalid response rejected ─────────────────────────────────────

class TestGoNogoInvalidResponse:
    """Test that invalid responses are rejected."""

    @pytest.mark.asyncio
    async def test_invalid_response_text_rejected(self):
        """A response that is not GO/NOGO/STANDBY should be rejected."""
        server = _make_server_minimal()
        client = await _make_client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Test",
            })
            resp = await client.post("/api/go-nogo/respond", json={
                "position": "eps_tcs",
                "response": "MAYBE",
            })
            assert resp.status == 400
            data = await resp.json()
            assert "error" in data
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_empty_response_rejected(self):
        """An empty response string should be rejected."""
        server = _make_server_minimal()
        client = await _make_client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Test",
            })
            resp = await client.post("/api/go-nogo/respond", json={
                "position": "eps_tcs",
                "response": "",
            })
            assert resp.status == 400
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_respond_with_no_active_poll(self):
        """Responding when no poll is active should return 400."""
        server = _make_server_minimal()
        client = await _make_client(server)
        try:
            resp = await client.post("/api/go-nogo/respond", json={
                "position": "eps_tcs",
                "response": "GO",
            })
            assert resp.status == 400
            data = await resp.json()
            assert "No active poll" in data["error"]
        finally:
            await client.close()


# ── Result aggregation ────────────────────────────────────────────

class TestGoNogoResultAggregation:
    """Test that results are correctly aggregated when all positions respond."""

    @pytest.mark.asyncio
    async def test_all_go_produces_all_go_result(self):
        """When all positions respond GO, the result should be ALL_GO."""
        server = _make_server_minimal()
        client = await _make_client(server)
        try:
            # Start poll (FD auto-responds GO)
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "All GO Test",
            })
            # EPS/TCS responds GO (completing all positions)
            resp = await client.post("/api/go-nogo/respond", json={
                "position": "eps_tcs",
                "response": "GO",
            })
            data = await resp.json()
            assert data["all_responses"]["flight_director"] == "GO"
            assert data["all_responses"]["eps_tcs"] == "GO"
            # Poll should become inactive after all responded
            assert server._go_nogo_active is False
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_any_nogo_produces_no_go_result(self):
        """When any position responds NOGO, the result should be NO_GO."""
        server = _make_server_minimal()
        client = await _make_client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "NOGO Test",
            })
            resp = await client.post("/api/go-nogo/respond", json={
                "position": "eps_tcs",
                "response": "NOGO",
            })
            data = await resp.json()
            assert data["all_responses"]["eps_tcs"] == "NOGO"
            # Poll should become inactive
            assert server._go_nogo_active is False
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_partial_responses_keep_poll_active(self):
        """Poll should remain active until all positions have responded."""
        server = _make_server_with_positions()
        client = await _make_client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Partial Test",
            })
            # Only one non-FD position responds
            await client.post("/api/go-nogo/respond", json={
                "position": "eps_tcs",
                "response": "GO",
            })
            # Poll should still be active (not all 6 positions responded)
            assert server._go_nogo_active is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_all_six_positions_go(self):
        """Test complete poll with all 6 positions responding GO."""
        server = _make_server_with_positions()
        client = await _make_client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Full GO Test",
            })
            for pos in ["eps_tcs", "aocs", "ttc", "payload_ops", "fdir_systems"]:
                await client.post("/api/go-nogo/respond", json={
                    "position": pos,
                    "response": "GO",
                })
            # All 6 responded -> poll should be complete
            assert server._go_nogo_active is False
            # All responses should be GO
            for pos in server._go_nogo_responses:
                assert server._go_nogo_responses[pos] == "GO"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_one_nogo_among_six_produces_no_go(self):
        """If one of six positions says NOGO, the result is NO_GO."""
        server = _make_server_with_positions()
        client = await _make_client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Mixed Test",
            })
            for pos in ["eps_tcs", "aocs", "ttc", "payload_ops"]:
                await client.post("/api/go-nogo/respond", json={
                    "position": pos,
                    "response": "GO",
                })
            # FDIR says NOGO
            await client.post("/api/go-nogo/respond", json={
                "position": "fdir_systems",
                "response": "NOGO",
            })
            assert server._go_nogo_active is False
            assert server._go_nogo_responses["fdir_systems"] == "NOGO"
        finally:
            await client.close()


# ── GET /api/go-nogo/status ───────────────────────────────────────

class TestGoNogoStatus:
    """Test GET /api/go-nogo/status endpoint."""

    @pytest.mark.asyncio
    async def test_status_before_poll(self):
        """Before any poll, status should show inactive."""
        server = _make_server_with_positions()
        client = await _make_client(server)
        try:
            resp = await client.get("/api/go-nogo/status")
            assert resp.status == 200
            data = await resp.json()
            assert data["active"] is False
            assert data["label"] == ""
            assert data["responses"] == {}
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_status_during_poll(self):
        """During an active poll, status should show active with current responses."""
        server = _make_server_with_positions()
        client = await _make_client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Active Poll",
            })
            resp = await client.get("/api/go-nogo/status")
            data = await resp.json()
            assert data["active"] is True
            assert data["label"] == "Active Poll"
            assert data["initiator"] == "flight_director"
            assert "flight_director" in data["responses"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_status_after_all_responded(self):
        """After all positions respond, poll becomes inactive."""
        server = _make_server_minimal()
        client = await _make_client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Complete Poll",
            })
            await client.post("/api/go-nogo/respond", json={
                "position": "eps_tcs",
                "response": "GO",
            })
            resp = await client.get("/api/go-nogo/status")
            data = await resp.json()
            assert data["active"] is False
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_status_includes_all_responses(self):
        """Status should include responses from all positions that have responded."""
        server = _make_server_with_positions()
        client = await _make_client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director",
                "label": "Check Responses",
            })
            await client.post("/api/go-nogo/respond", json={
                "position": "eps_tcs",
                "response": "GO",
            })
            await client.post("/api/go-nogo/respond", json={
                "position": "aocs",
                "response": "NOGO",
            })
            resp = await client.get("/api/go-nogo/status")
            data = await resp.json()
            assert data["responses"]["flight_director"] == "GO"
            assert data["responses"]["eps_tcs"] == "GO"
            assert data["responses"]["aocs"] == "NOGO"
        finally:
            await client.close()

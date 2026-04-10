"""Tests for procedure builder save/load API.

Covers:
  - POST /api/procedure/save — saves a custom procedure
  - GET /api/procedure/custom — lists custom procedures
  - Name sanitization (special chars become underscores)
  - Validation: empty name returns 400
  - Validation: whitespace-only name returns 400

Uses aiohttp.test_utils.TestServer/TestClient with a mocked MCSServer,
following the pattern established in test_mcs_server.py. The config_dir
is pointed at a temporary directory to avoid polluting real configs.
"""
import json
import asyncio
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from smo_mcs.server import MCSServer
from smo_common.config.schemas import PositionConfig, MCSDisplayConfig


# ── Helpers ─────────────────────────────────────────────────────────

def _make_server_with_tmpdir(tmp_path):
    """Create an MCSServer with config_dir pointing to a temp directory.

    This allows procedure save to write files without polluting the
    real config tree.
    """
    # Create the required directory structure
    proc_dir = tmp_path / "procedures"
    proc_dir.mkdir(parents=True, exist_ok=True)

    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}), \
         patch("smo_mcs.server.load_tc_catalog", return_value=[]):
        server = MCSServer(
            config_dir=str(tmp_path),
            connect_host="localhost",
            connect_port=9999,
            http_port=0,
        )
    return server


def _build_app(server: MCSServer) -> web.Application:
    """Build the aiohttp Application with procedure builder routes."""
    app = web.Application()
    app.router.add_post("/api/procedure/save", server._handle_proc_save)
    app.router.add_get("/api/procedure/custom", server._handle_proc_custom_list)
    return app


async def _make_client(server: MCSServer) -> TestClient:
    """Create a TestClient for the given MCSServer."""
    app = _build_app(server)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# ── POST /api/procedure/save ──────────────────────────────────────

class TestProcedureSave:
    """Test saving custom procedures via the procedure builder."""

    @pytest.mark.asyncio
    async def test_save_valid_procedure(self, tmp_path):
        """A valid procedure with name and steps should save successfully."""
        server = _make_server_with_tmpdir(tmp_path)
        client = await _make_client(server)
        try:
            resp = await client.post("/api/procedure/save", json={
                "name": "My Test Procedure",
                "description": "A test procedure",
                "position": "flight_director",
                "steps": [
                    {"service": 8, "subtype": 1, "func_id": "0x10"},
                    {"wait_s": 5},
                    {"service": 3, "subtype": 25, "func_id": "0x01"},
                ],
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "saved"
            assert "path" in data
            assert data["path"].startswith("custom/")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_save_creates_file_on_disk(self, tmp_path):
        """Saving a procedure should create a YAML file in the custom dir."""
        server = _make_server_with_tmpdir(tmp_path)
        client = await _make_client(server)
        try:
            await client.post("/api/procedure/save", json={
                "name": "Battery Check",
                "steps": [{"service": 3, "subtype": 25}],
                "position": "eps_tcs",
            })
            custom_dir = tmp_path / "procedures" / "custom"
            assert custom_dir.exists()
            yaml_files = list(custom_dir.glob("*.yaml"))
            assert len(yaml_files) == 1
            assert "battery_check" in yaml_files[0].name
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_save_preserves_steps_in_file(self, tmp_path):
        """The saved YAML file should contain the procedure steps."""
        import yaml
        server = _make_server_with_tmpdir(tmp_path)
        client = await _make_client(server)
        try:
            steps = [
                {"service": 8, "subtype": 1, "func_id": "0x10"},
                {"wait_s": 2},
            ]
            await client.post("/api/procedure/save", json={
                "name": "Step Test",
                "steps": steps,
                "position": "flight_director",
            })
            custom_dir = tmp_path / "procedures" / "custom"
            yaml_file = list(custom_dir.glob("*.yaml"))[0]
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            assert data["name"] == "Step Test"
            assert len(data["steps"]) == 2
            assert data["created_by"] == "flight_director"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_save_empty_name_returns_400(self, tmp_path):
        """An empty name should return 400."""
        server = _make_server_with_tmpdir(tmp_path)
        client = await _make_client(server)
        try:
            resp = await client.post("/api/procedure/save", json={
                "name": "",
                "steps": [{"wait_s": 1}],
            })
            assert resp.status == 400
            data = await resp.json()
            assert "error" in data
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_save_whitespace_name_returns_400(self, tmp_path):
        """A whitespace-only name should return 400."""
        server = _make_server_with_tmpdir(tmp_path)
        client = await _make_client(server)
        try:
            resp = await client.post("/api/procedure/save", json={
                "name": "   ",
                "steps": [{"wait_s": 1}],
            })
            assert resp.status == 400
            data = await resp.json()
            assert "error" in data
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_save_empty_steps_still_succeeds(self, tmp_path):
        """An empty steps list should still save (no validation on steps count)."""
        server = _make_server_with_tmpdir(tmp_path)
        client = await _make_client(server)
        try:
            resp = await client.post("/api/procedure/save", json={
                "name": "Empty Steps Proc",
                "steps": [],
                "position": "flight_director",
            })
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "saved"
        finally:
            await client.close()


# ── Name sanitization ─────────────────────────────────────────────

class TestNameSanitization:
    """Test that procedure names are sanitized for filesystem safety."""

    @pytest.mark.asyncio
    async def test_spaces_become_underscores(self, tmp_path):
        server = _make_server_with_tmpdir(tmp_path)
        client = await _make_client(server)
        try:
            resp = await client.post("/api/procedure/save", json={
                "name": "My Great Procedure",
                "steps": [{"wait_s": 1}],
            })
            data = await resp.json()
            assert "my_great_procedure" in data["path"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_special_chars_sanitized(self, tmp_path):
        server = _make_server_with_tmpdir(tmp_path)
        client = await _make_client(server)
        try:
            resp = await client.post("/api/procedure/save", json={
                "name": "Test@Proc#123!",
                "steps": [{"wait_s": 1}],
            })
            data = await resp.json()
            path = data["path"]
            # Only alphanumeric, hyphens, and underscores should remain
            filename = path.split("/")[-1].replace(".yaml", "")
            assert all(c.isalnum() or c in "-_" for c in filename), (
                f"Sanitized filename contains illegal chars: {filename}"
            )
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_uppercase_lowered(self, tmp_path):
        server = _make_server_with_tmpdir(tmp_path)
        client = await _make_client(server)
        try:
            resp = await client.post("/api/procedure/save", json={
                "name": "UPPERCASE Test",
                "steps": [],
            })
            data = await resp.json()
            filename = data["path"].split("/")[-1]
            assert filename == filename.lower()
        finally:
            await client.close()


# ── GET /api/procedure/custom ─────────────────────────────────────

class TestProcedureCustomList:
    """Test listing custom procedures."""

    @pytest.mark.asyncio
    async def test_list_empty_when_no_custom(self, tmp_path):
        """When no custom procedures exist, the list should be empty."""
        server = _make_server_with_tmpdir(tmp_path)
        client = await _make_client(server)
        try:
            resp = await client.get("/api/procedure/custom")
            assert resp.status == 200
            data = await resp.json()
            assert "procedures" in data
            assert data["procedures"] == []
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_list_includes_saved_procedure(self, tmp_path):
        """After saving a procedure, it should appear in the custom list."""
        server = _make_server_with_tmpdir(tmp_path)
        client = await _make_client(server)
        try:
            await client.post("/api/procedure/save", json={
                "name": "Listed Procedure",
                "description": "Should appear in list",
                "steps": [{"wait_s": 1}],
                "position": "eps_tcs",
            })
            resp = await client.get("/api/procedure/custom")
            data = await resp.json()
            assert len(data["procedures"]) == 1
            proc = data["procedures"][0]
            assert proc["name"] == "Listed Procedure"
            assert proc["description"] == "Should appear in list"
            assert proc["created_by"] == "eps_tcs"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_list_multiple_procedures(self, tmp_path):
        """Multiple saved procedures should all appear in the list."""
        server = _make_server_with_tmpdir(tmp_path)
        client = await _make_client(server)
        try:
            for i in range(3):
                await client.post("/api/procedure/save", json={
                    "name": f"Proc {i}",
                    "steps": [{"wait_s": i}],
                    "position": "flight_director",
                })
            resp = await client.get("/api/procedure/custom")
            data = await resp.json()
            assert len(data["procedures"]) == 3
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_list_entries_have_required_fields(self, tmp_path):
        """Each custom procedure entry should have name, description, path, steps."""
        server = _make_server_with_tmpdir(tmp_path)
        client = await _make_client(server)
        try:
            await client.post("/api/procedure/save", json={
                "name": "Field Check",
                "description": "Check fields",
                "steps": [{"service": 8, "subtype": 1}],
                "position": "aocs",
            })
            resp = await client.get("/api/procedure/custom")
            data = await resp.json()
            proc = data["procedures"][0]
            assert "name" in proc
            assert "description" in proc
            assert "path" in proc
            assert "steps" in proc
            assert "created_by" in proc
        finally:
            await client.close()

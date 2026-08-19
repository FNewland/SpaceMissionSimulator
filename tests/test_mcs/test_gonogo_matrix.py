"""GO/NO-GO call matrix endpoint (POSITION_NEEDS G7)."""
from pathlib import Path

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_mcs.server import MCSServer

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("SMO_TM_ARCHIVE", str(tmp_path / "a.db"))
    return MCSServer(config_dir=str(CONFIG_DIR), http_port=0)


async def _client(server):
    app = web.Application()
    app.router.add_get("/api/gonogo-matrix", server._handle_gonogo_matrix)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_endpoint_serves_config_verbatim(server):
    cfg = yaml.safe_load(
        (CONFIG_DIR / "mcs" / "gonogo_matrix.yaml").read_text())
    client = await _client(server)
    try:
        resp = await client.get("/api/gonogo-matrix")
        assert resp.status == 200
        data = await resp.json()
        assert data["available"] is True
        assert data["matrix"] == cfg["gonogo_matrix"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rows_have_the_call_contract_fields(server):
    client = await _client(server)
    try:
        resp = await client.get("/api/gonogo-matrix")
        data = await resp.json()
        assert len(data["matrix"]) >= 11  # the manual's full matrix
        for row in data["matrix"]:
            assert row["situation"]
            assert row["who_calls"]
            assert isinstance(row["who_responds"], list) and row["who_responds"]
            assert row["fd_decision"]
            assert isinstance(row["critical"], bool)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_missing_config_is_honest(tmp_path, monkeypatch):
    """A mission without the file gets available:false, not invented rows."""
    monkeypatch.setenv("SMO_TM_ARCHIVE", str(tmp_path / "a.db"))
    # Point a server at a config dir without mcs/gonogo_matrix.yaml.
    server = MCSServer(config_dir=str(tmp_path), http_port=0)
    client = await _client(server)
    try:
        resp = await client.get("/api/gonogo-matrix")
        assert resp.status == 200
        data = await resp.json()
        assert data["available"] is False
        assert data["matrix"] == []
    finally:
        await client.close()

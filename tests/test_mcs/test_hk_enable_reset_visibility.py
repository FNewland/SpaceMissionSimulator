"""The HK Manager must be able to say WHY the enable set changed.

A boot or a bootloader entry legitimately replaces the on-board HK enable set
with `simulation.yaml housekeeping.default_enabled_sids`. Nothing on the
ground said so, so the operator's first evidence that their commissioned SIDs
had gone quiet was a hole in the next S15 dump.

The simulator now raises S5 0x0056 and publishes the same record on its state
endpoint as `hk_enable_reset`. These tests pin the MCS half: the record is
absorbed from the sim-state poll, offered on `/api/hk-structures`, and the
console renders it as an explanation next to the enable column.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_common.config.schemas import MCSDisplayConfig
from smo_mcs.server import MCSServer

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
MODERN_JS = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / \
    "modern" / "mcs.js"

RESET = {
    "reason": "OBC application software booted — the OBSW starts from its "
              "configured power-up HK set",
    "sids_disabled": [2, 3, 4, 5, 6, 9, 12],
    "sids_enabled": [],
    "enabled_now": [1, 11],
    "obc_time_s": 1234.5,
}


def _make_server() -> MCSServer:
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}):
        return MCSServer(config_dir=str(CONFIG_DIR), connect_host="localhost",
                         connect_port=9999, http_port=0)


@pytest.fixture
async def mcs():
    server = _make_server()
    server._running = False
    app = web.Application()
    app.router.add_get("/api/hk-structures", server._handle_hk_structures)
    cli = TestClient(TestServer(app))
    await cli.start_server()
    yield server, cli
    await cli.close()


def test_reset_record_starts_empty():
    assert _make_server()._hk_enable_reset is None


def test_reset_record_is_absorbed_from_the_sim_state_poll():
    server = _make_server()
    server._ingest_hk_enable_reset(RESET)

    rec = server._hk_enable_reset
    assert rec["reason"] == RESET["reason"]
    assert rec["sids_disabled"] == [2, 3, 4, 5, 6, 9, 12]
    assert rec["enabled_now"] == [1, 11]
    assert rec["source"] == "sim_state", (
        "the record does not come from telemetry and must say so")


@pytest.mark.parametrize("junk", [None, {}, [], "boom", {"sids_disabled": [1]}])
def test_malformed_reset_records_are_ignored(junk):
    """A bad record must never degrade the display."""
    server = _make_server()
    server._ingest_hk_enable_reset(junk)
    assert server._hk_enable_reset is None


async def test_hk_structures_endpoint_carries_the_reason(mcs):
    server, cli = mcs
    body = await (await cli.get("/api/hk-structures")).json()
    assert body["enable_reset"] is None, "nothing has reset the set yet"

    server._ingest_hk_enable_reset(RESET)
    body = await (await cli.get("/api/hk-structures")).json()
    assert body["enable_reset"]["sids_disabled"] == [2, 3, 4, 5, 6, 9, 12]
    assert RESET["reason"] in body["enable_reset"]["reason"]


def test_console_renders_the_enable_set_and_the_reason():
    """The modern HK Manager must show both, not just observed arrival."""
    js = MODERN_JS.read_text()
    assert "hkEnableSummaryHtml" in js
    assert "hk_enable_reset" in js, (
        "the console never reads the spacecraft's explanation")
    assert "hkEnabledBit" in js, (
        "the HK Manager shows no reported enable bit — an operator cannot "
        "tell a commanded-off SID from one that is merely not arriving")
    # The banner has to name the SIDs that were silenced and point at the fix.
    assert "sids_disabled" in js
    assert "HK_ENABLE" in js

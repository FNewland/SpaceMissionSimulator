"""Tests for the MCS unified time-source configuration.

Covers:
  - SIM mode: closed-loop re-anchor of get_ground_utc() to a stubbed
    simulator sim_time, and that it scales with `speed`.
  - REAL mode: get_ground_utc() tracks wall clock and no sim-state polling.
  - Spacecraft time (sc_obc_time_cuc, TM 0x0309) is independent of the
    ground clock / time-source switch.

The sim-state fetch is exercised by driving the same re-anchor logic the
background loop uses (parsing sim_time/speed into the open-loop clock),
so no live HTTP/event loop is required.
"""
import time as _time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from smo_mcs.server import MCSServer
from smo_common.config.schemas import MCSDisplayConfig, MissionConfig


# ── Helpers ─────────────────────────────────────────────────────────

def _make_server(time_source, sim_state_url=None, mission_time_source="sim"):
    """Construct an MCSServer with config loaders mocked out."""
    mc = MissionConfig(time_source=mission_time_source)
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}), \
         patch("smo_mcs.server.load_tc_catalog", return_value=[]), \
         patch("smo_common.config.loader.load_mission_config", return_value=mc):
        return MCSServer(
            config_dir="/tmp/fake_config",
            connect_host="localhost",
            connect_port=9999,
            http_port=0,
            time_source=time_source,
            sim_state_url=sim_state_url,
        )


def _reanchor(server, sim_time_iso, speed):
    """Apply the same re-anchor the sim-state poll loop performs."""
    parsed = datetime.fromisoformat(sim_time_iso.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    server._ground_epoch = parsed
    server._ground_start_wall = _time.time()
    server._sim_speed = float(speed)


# ── SIM mode ────────────────────────────────────────────────────────

def test_sim_mode_anchors_to_sim_time():
    server = _make_server(time_source="sim")
    assert server._time_source == "sim"
    sim_time = "2030-01-01T00:00:00+00:00"
    _reanchor(server, sim_time, speed=1.0)
    ground = server.get_ground_utc()
    expected = datetime(2030, 1, 1, tzinfo=timezone.utc)
    # Within a couple of seconds of the anchored sim_time.
    assert abs((ground - expected).total_seconds()) < 2.0


def test_sim_mode_scales_with_speed():
    server = _make_server(time_source="sim")
    sim_time = "2030-01-01T00:00:00+00:00"
    _reanchor(server, sim_time, speed=10.0)
    # Pretend 3 wall seconds elapsed since the anchor.
    server._ground_start_wall = _time.time() - 3.0
    ground = server.get_ground_utc()
    expected = datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=30.0)
    # 3 wall s × speed 10 ≈ 30 sim s (allow a little slack).
    assert abs((ground - expected).total_seconds()) < 2.0


def test_sim_state_url_default_uses_connect_host():
    server = _make_server(time_source="sim")
    assert server._sim_state_url == "http://localhost:8080/api/state"


def test_cli_sim_state_url_override():
    server = _make_server(time_source="sim",
                          sim_state_url="http://elsewhere:9999/state")
    assert server._sim_state_url == "http://elsewhere:9999/state"


# ── REAL mode ───────────────────────────────────────────────────────

def test_real_mode_tracks_wall_clock_no_anchor():
    server = _make_server(time_source="real")
    assert server._time_source == "real"
    # Real mode must NOT set a sim epoch anchor.
    assert server._ground_epoch is None
    ground = server.get_ground_utc()
    now = datetime.now(timezone.utc)
    assert abs((ground - now).total_seconds()) < 2.0


def test_real_mode_does_not_poll(monkeypatch):
    """In real mode the sim-state poll loop is a no-op (returns immediately)."""
    server = _make_server(time_source="real")
    called = {"n": 0}

    # If the loop were to poll, it would construct a ClientSession; assert it
    # never does because the loop returns early for non-sim time sources.
    import asyncio

    async def _run():
        # Loop should return immediately without entering the while body.
        await asyncio.wait_for(server._sim_state_poll_loop(), timeout=1.0)

    asyncio.run(_run())  # would hang/raise if it entered the polling loop


# ── Precedence ──────────────────────────────────────────────────────

def test_env_overrides_mission_config(monkeypatch):
    monkeypatch.setenv("SMO_TIME_SOURCE", "real")
    # mission config says sim, but env says real → real wins (CLI None).
    server = _make_server(time_source=None, mission_time_source="sim")
    assert server._time_source == "real"


def test_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("SMO_TIME_SOURCE", "real")
    server = _make_server(time_source="sim", mission_time_source="real")
    assert server._time_source == "sim"


def test_mission_config_default(monkeypatch):
    monkeypatch.delenv("SMO_TIME_SOURCE", raising=False)
    server = _make_server(time_source=None, mission_time_source="real")
    assert server._time_source == "real"


# ── Spacecraft time independence ────────────────────────────────────

def test_spacecraft_time_independent_of_ground_clock():
    """sc_obc_time_cuc comes from TM 0x0309, not the ground clock."""
    server = _make_server(time_source="sim")
    # Seed the spacecraft OBC time param (as the TM decoder would).
    server._param_cache[0x0309] = {"value": 123456789, "last_update_ts": _time.time(), "sid": 1}
    server._param_cache[0x0308] = {"value": 4242, "last_update_ts": _time.time(), "sid": 1}

    # Move the ground clock far away by re-anchoring.
    _reanchor(server, "2099-12-31T23:59:59+00:00", speed=100.0)

    ts = server._compute_time_state()
    # Spacecraft time reflects the TM value, untouched by the ground anchor.
    assert ts["sc_obc_time_cuc"] == 123456789
    assert ts["sc_uptime_s"] == 4242

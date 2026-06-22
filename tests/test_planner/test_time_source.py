"""Tests for the Planner unified time-source configuration.

Covers:
  - SIM mode: _get_now() returns the simulator's sim_time (from a stubbed
    sim-state fetch) and contacts compute against it.
  - REAL mode: _get_now() returns wall-clock UTC.
  - The explicit ?epoch= override still wins over _get_now().
  - Precedence (CLI > env > mission-config > default).
"""
import time as _time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from smo_planner.server import PlannerServer
from smo_common.config.schemas import MissionConfig, OrbitConfig


# ── Helpers ─────────────────────────────────────────────────────────

def _make_planner(time_source=None, sim_state_url=None, mission_time_source="sim",
                  sim_state_payload=None):
    """Construct a PlannerServer with orbit + mission config mocked.

    sim_state_payload, if given, is returned by the stubbed _fetch_sim_state.
    """
    orbit_cfg = OrbitConfig(
        tle_line1="1 25544U 98067A   24001.00000000  .00000000  00000-0  00000-0 0  9999",
        tle_line2="2 25544  51.6400 000.0000 0001000   0.0000   0.0000 15.50000000000000",
        ground_stations=[],
    )
    mc = MissionConfig(time_source=mission_time_source)
    with patch("smo_planner.server.load_orbit_config", return_value=orbit_cfg), \
         patch("smo_planner.server.OrbitPropagator", return_value=MagicMock()), \
         patch("smo_planner.server.OrbitPlanner", return_value=MagicMock()), \
         patch("smo_common.config.loader.load_mission_config", return_value=mc):
        server = PlannerServer(
            config_dir="/tmp/fake_config",
            http_port=0,
            time_source=time_source,
            sim_state_url=sim_state_url,
        )
    if sim_state_payload is not None:
        server._fetch_sim_state = lambda: sim_state_payload
    return server


# ── SIM mode ────────────────────────────────────────────────────────

def test_sim_mode_get_now_returns_sim_time():
    payload = {"sim_time": "2030-06-15T12:00:00+00:00", "speed": 1.0}
    server = _make_planner(time_source="sim", sim_state_payload=payload)
    server._refresh_sim_anchor(force=True)  # install stubbed anchor
    now = server._get_now()
    expected = datetime(2030, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    assert abs((now - expected).total_seconds()) < 2.0


def test_sim_mode_scales_with_speed():
    payload = {"sim_time": "2030-06-15T12:00:00+00:00", "speed": 60.0}
    server = _make_planner(time_source="sim", sim_state_payload=payload)
    server._refresh_sim_anchor(force=True)
    # Pretend 2 wall seconds passed since the anchor.
    server._sim_anchor_wall = _time.time() - 2.0
    now = server._get_now()
    expected = datetime(2030, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    delta = (now - expected).total_seconds()
    # 2 wall s × 60 ≈ 120 sim s.
    assert 110 < delta < 130


def test_sim_mode_contacts_compute_from_sim_time():
    payload = {"sim_time": "2030-06-15T12:00:00+00:00", "speed": 1.0}
    server = _make_planner(time_source="sim", sim_state_payload=payload)
    captured = {}

    def _fake_compute(epoch=None):
        captured["now"] = epoch or server._get_now()

    server._compute_contacts = _fake_compute
    server._refresh_sim_anchor(force=True)  # install stubbed anchor
    server._compute_contacts()
    assert captured["now"].year == 2030
    assert captured["now"].month == 6


def test_sim_mode_falls_back_to_wall_clock_when_unreachable():
    server = _make_planner(time_source="sim")
    server._fetch_sim_state = lambda: None  # sim never reachable
    now = server._get_now()
    assert abs((now - datetime.now(timezone.utc)).total_seconds()) < 2.0


def test_sim_state_url_default():
    server = _make_planner(time_source="sim")
    assert server._sim_state_url == "http://localhost:8080/api/state"


# ── REAL mode ───────────────────────────────────────────────────────

def test_real_mode_returns_wall_clock():
    server = _make_planner(time_source="real")
    assert server._time_source == "real"
    now = server._get_now()
    assert abs((now - datetime.now(timezone.utc)).total_seconds()) < 2.0


def test_real_mode_ignores_sim_state():
    # Even with a sim payload available, real mode must use wall clock.
    payload = {"sim_time": "2030-06-15T12:00:00+00:00", "speed": 1.0}
    server = _make_planner(time_source="real", sim_state_payload=payload)
    now = server._get_now()
    assert now.year == datetime.now(timezone.utc).year


# ── ?epoch= override precedence ─────────────────────────────────────

def test_epoch_override_wins_over_get_now():
    payload = {"sim_time": "2030-06-15T12:00:00+00:00", "speed": 1.0}
    server = _make_planner(time_source="sim", sim_state_payload=payload)
    captured = {}

    def _fake_compute(epoch=None):
        captured["now"] = epoch or server._get_now()

    server._compute_contacts = _fake_compute
    explicit = datetime(2040, 1, 1, tzinfo=timezone.utc)
    # Simulate the ?epoch= path: caller passes an explicit epoch.
    server._compute_contacts(explicit)
    assert captured["now"] == explicit  # override, NOT the sim_time


# ── Precedence ──────────────────────────────────────────────────────

def test_env_overrides_mission_config(monkeypatch):
    monkeypatch.setenv("SMO_TIME_SOURCE", "real")
    server = _make_planner(time_source=None, mission_time_source="sim")
    assert server._time_source == "real"


def test_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("SMO_TIME_SOURCE", "real")
    server = _make_planner(time_source="sim", mission_time_source="real")
    assert server._time_source == "sim"


def test_mission_config_default(monkeypatch):
    monkeypatch.delenv("SMO_TIME_SOURCE", raising=False)
    server = _make_planner(time_source=None, mission_time_source="real")
    assert server._time_source == "real"

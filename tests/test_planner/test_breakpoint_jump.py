"""Tests for sim-clock discontinuity detection (defect #59).

The planner's clock follows a breakpoint load within ~1.5 s because the anchor
is simply replaced, but the contact-window and ground-track caches used to be
refreshed only by a fixed 600 s wall-clock loop, so passes, budgets and
schedule validation stayed wrong for up to ten minutes after a jump.

Covers:
  - a backward jump (breakpoint load) is detected and marks the plan dirty
  - a large forward jump is detected too
  - normal drift within tolerance is NOT a jump
  - a speed change alone is not mistaken for a jump
  - the live propagator is reset on the jump rather than by luck
  - recompute_plan_products() rebuilds both caches and clears the flag
  - /api/time-source exposes the jump and the wall-clock fallback
"""
import time as _time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from smo_planner.server import PlannerServer
from smo_common.config.schemas import MissionConfig, OrbitConfig


T0 = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)


_UNREACHABLE_SIM_URL = "http://127.0.0.1:59999/api/state"


def _make_planner(payload=None):
    orbit_cfg = OrbitConfig(
        tle_line1="1 25544U 98067A   24001.00000000  .00000000  00000-0  00000-0 0  9999",
        tle_line2="2 25544  51.6400 000.0000 0001000   0.0000   0.0000 15.50000000000000",
        ground_stations=[],
    )
    mc = MissionConfig(time_source="sim")
    with patch("smo_planner.server.load_orbit_config", return_value=orbit_cfg), \
         patch("smo_planner.server.OrbitPropagator", return_value=MagicMock()), \
         patch("smo_planner.server.OrbitPlanner", return_value=MagicMock()), \
         patch("smo_common.config.loader.load_mission_config", return_value=mc):
        # Point the constructor at a port nothing can be listening on. The
        # PlannerServer takes its first sim anchor during __init__, BEFORE the
        # test can swap _fetch_sim_state -- so with the default
        # localhost:8080 these tests passed only when no simulator happened to
        # be running on the developer's machine, and failed silently-differently
        # when one was. The unreachable URL makes the "sim was never reachable"
        # premise true by construction.
        server = PlannerServer("/tmp/fake_config", http_port=0, time_source="sim",
                               sim_state_url=_UNREACHABLE_SIM_URL)
    server._fetch_sim_state = lambda: payload
    return server


def _anchor_at(server, sim_time: datetime, speed: float = 1.0):
    """Install an anchor as if the sim reported sim_time just now."""
    server._sim_anchor_time = sim_time
    server._sim_anchor_wall = _time.time()
    server._sim_anchor_speed = speed
    server._plan_dirty = False


# ── Detection ──────────────────────────────────────────────────────

class TestJumpDetection:

    def test_backward_jump_marks_plan_dirty(self):
        server = _make_planner()
        _anchor_at(server, T0)
        # Breakpoint load: sim time moves back 6 hours.
        server._fetch_sim_state = lambda: {
            "sim_time": (T0 - timedelta(hours=6)).isoformat(), "speed": 1.0}
        server._refresh_sim_anchor(force=True)
        assert server._plan_dirty is True
        assert server._jump_count == 1
        assert server._last_jump_delta_s == pytest.approx(-6 * 3600, abs=5)
        assert server._sim_anchor_time == T0 - timedelta(hours=6)

    def test_large_forward_jump_marks_plan_dirty(self):
        server = _make_planner()
        _anchor_at(server, T0)
        server._fetch_sim_state = lambda: {
            "sim_time": (T0 + timedelta(hours=3)).isoformat(), "speed": 1.0}
        server._refresh_sim_anchor(force=True)
        assert server._plan_dirty is True
        assert server._last_jump_delta_s == pytest.approx(3 * 3600, abs=5)

    def test_normal_drift_is_not_a_jump(self):
        server = _make_planner()
        _anchor_at(server, T0)
        # 1.5 s later the sim reports 1.5 s of sim time: no discontinuity.
        server._fetch_sim_state = lambda: {
            "sim_time": (T0 + timedelta(seconds=1.5)).isoformat(), "speed": 1.0}
        server._refresh_sim_anchor(force=True)
        assert server._plan_dirty is False
        assert server._jump_count == 0

    def test_speed_change_alone_is_not_a_jump(self):
        server = _make_planner()
        _anchor_at(server, T0, speed=1.0)
        server._fetch_sim_state = lambda: {
            "sim_time": T0.isoformat(), "speed": 50.0}
        server._refresh_sim_anchor(force=True)
        assert server._plan_dirty is False
        assert server._sim_anchor_speed == 50.0

    def test_tolerance_scales_with_speed(self):
        """At x100 the sim advances 150 s of sim time between refreshes; that
        must not be reported as a discontinuity."""
        server = _make_planner()
        _anchor_at(server, T0, speed=100.0)
        server.planner_cfg.time.anchor_refresh_s = 1.5
        server._fetch_sim_state = lambda: {
            "sim_time": (T0 + timedelta(seconds=150)).isoformat(), "speed": 100.0}
        server._refresh_sim_anchor(force=True)
        assert server._plan_dirty is False

    def test_first_anchor_is_not_a_jump(self):
        server = _make_planner()
        server._fetch_sim_state = lambda: {
            "sim_time": T0.isoformat(), "speed": 1.0}
        server._refresh_sim_anchor(force=True)
        assert server._plan_dirty is False
        assert server._sim_anchor_time == T0

    def test_live_propagator_is_reset_on_the_jump(self):
        server = _make_planner()
        _anchor_at(server, T0)
        server._live_prop = MagicMock()
        new_time = T0 - timedelta(hours=6)
        server._fetch_sim_state = lambda: {
            "sim_time": new_time.isoformat(), "speed": 1.0}
        server._refresh_sim_anchor(force=True)
        server._live_prop.reset.assert_called_with(new_time)


# ── Invalidation ───────────────────────────────────────────────────

class TestCacheInvalidation:

    def test_recompute_rebuilds_both_caches_and_clears_the_flag(self):
        server = _make_planner()
        _anchor_at(server, T0)
        server._contacts_cache = [{"gs_name": "Stale", "aos": T0.isoformat()}]
        server._ground_track_cache = [{"utc": T0.isoformat()}]
        server._contacts_computed_at = T0
        server._plan_dirty = True

        new_time = T0 - timedelta(hours=6)
        server._get_now = lambda: new_time
        calls = []
        server._compute_contacts = lambda epoch=None: calls.append(("c", epoch))
        server._compute_ground_track = lambda epoch=None: calls.append(("g", epoch))
        server._live_prop = MagicMock()

        server.recompute_plan_products()

        assert calls == [("c", new_time), ("g", new_time)]
        server._live_prop.reset.assert_called_with(new_time)
        assert server._plan_dirty is False

    def test_stale_windows_are_actually_replaced(self):
        """End-to-end: after a backward jump the cached windows for the OLD
        epoch must not still be served."""
        server = _make_planner()
        _anchor_at(server, T0)
        server._contacts_cache = [
            {"gs_name": "Svalbard", "aos": (T0 + timedelta(minutes=30)).isoformat(),
             "los": (T0 + timedelta(minutes=38)).isoformat()}]
        new_time = T0 - timedelta(hours=6)
        server._fetch_sim_state = lambda: {
            "sim_time": new_time.isoformat(), "speed": 1.0}
        server._refresh_sim_anchor(force=True)
        assert server._plan_dirty is True

        # The refresh loop's action:
        def _recompute(epoch=None):
            server._contacts_cache = [
                {"gs_name": "Svalbard",
                 "aos": (new_time + timedelta(minutes=12)).isoformat(),
                 "los": (new_time + timedelta(minutes=20)).isoformat()}]
            server._contacts_computed_at = new_time
        server._compute_contacts = _recompute
        server._compute_ground_track = lambda epoch=None: None
        server._get_now = lambda: new_time
        server._live_prop = MagicMock()
        server.recompute_plan_products()

        aos = datetime.fromisoformat(server._contacts_cache[0]["aos"])
        assert aos > new_time
        assert (aos - new_time) < timedelta(hours=1)
        assert server._plan_dirty is False


# ── Reporting ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_time_source_endpoint_reports_jump_and_fallback():
    server = _make_planner()
    _anchor_at(server, T0)
    server._sim_reachable = True
    server._fetch_sim_state = lambda: {
        "sim_time": (T0 - timedelta(hours=6)).isoformat(), "speed": 1.0}
    server._refresh_sim_anchor(force=True)

    app = web.Application()
    app.router.add_get("/api/time-source", server._handle_time_source)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        data = await (await client.get("/api/time-source")).json()
        assert data["time_source"] == "sim"
        assert data["jump_count"] == 1
        assert data["last_jump_delta_s"] == pytest.approx(-21600, abs=5)
        assert data["sim_reachable"] is True
        assert data["wall_clock_fallback"] is False
        # the page clock anchors off this
        assert data["now"].startswith("2026-03-10T06:")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_time_source_endpoint_flags_silent_wall_clock_fallback():
    """time_source=sim but the sim was never reachable: the UI must be told."""
    server = _make_planner(payload=None)
    app = web.Application()
    app.router.add_get("/api/time-source", server._handle_time_source)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        data = await (await client.get("/api/time-source")).json()
        assert data["time_source"] == "sim"
        assert data["wall_clock_fallback"] is True
        assert data["degraded"] is True
        assert data["sim_reachable"] is False
    finally:
        await client.close()

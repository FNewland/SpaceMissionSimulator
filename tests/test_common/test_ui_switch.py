"""Tests for the SMO_UI switch (design_2026-08 execution plan §1.2).

One switch, three servers, house precedence mirroring time_source:

    CLI --ui  >  SMO_UI env  >  mission-config `ui:`  >  default ("modern")

plus per-request overrides that win over everything for one response:

    ?ui=classic / ?ui=modern    on any index route
    GET /classic  /  GET /modern  explicit generation routes (live A/B)

Covers, per server:
  - MCS: resolution precedence, ?ui= override, /classic + /modern routes,
    Cache-Control: no-cache on every index response.
  - Planner: resolution precedence; the /wide branch — classic mode keeps
    the documented /?layout=wide redirect (with ?ui=classic carried
    through), modern mode serves the distinct wall page.
  - Instructor: create_instructor_app(engine, ui_mode=) arg > env > default;
    ?ui= composing with the existing ?sub= deep-dive param.

The Agent-5 verification gate has passed: the shipped default is "modern"
(the 2026-08-18 final flip); the default tests below pin that. The escape
hatches (/classic, SMO_UI=classic, --classic, ?ui=classic) stay covered.
The tests are written to pass both BEFORE the modern pages exist (the
servers then serve an explicit "modern UI assets not installed" page, still
200 + no-cache) and AFTER Agents 2-4 land them (the real file is served).
"""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from smo_common.config.schemas import (MCSDisplayConfig, MissionConfig,
                                       OrbitConfig)
from smo_mcs.server import MCSServer
from smo_planner.server import PlannerServer
from smo_simulator.instructor.app import create_instructor_app

REPO = Path(__file__).resolve().parents[2]
MCS_STATIC = REPO / "packages/smo-mcs/src/smo_mcs/static"
PLANNER_STATIC = REPO / "packages/smo-planner/src/smo_planner/static"
INSTRUCTOR_STATIC = (REPO / "packages/smo-simulator/src/smo_simulator"
                     / "instructor/static")


def _expected_body(static_dir: Path, mode: str, rel_modern="modern/index.html"):
    """What an index response should contain for `mode`.

    classic -> the exact classic file text. modern -> the modern file text
    if shipped, else the explicit not-installed page (tests stay green
    before and after Agents 2-4 land).
    """
    if mode == "classic":
        return (static_dir / "index.html").read_text(), True
    modern = static_dir / rel_modern
    if modern.exists():
        return modern.read_text(), True
    return "modern UI assets not installed", False


async def _assert_index(client, url, static_dir, mode, rel_modern="modern/index.html"):
    resp = await client.get(url, allow_redirects=False)
    assert resp.status == 200
    assert resp.headers.get("Cache-Control") == "no-cache", (
        f"{url}: index responses must carry no-cache (plan §1.2 / risk R9)")
    text = await resp.text()
    expected, exact = _expected_body(static_dir, mode, rel_modern)
    if exact:
        assert text == expected, f"{url}: expected the {mode} page"
    else:
        assert expected in text, f"{url}: expected the {mode} placeholder"


# ═══════════════════════════════ MCS ═══════════════════════════════

def _make_mcs(ui_mode=None, mission_ui=None):
    mc = MissionConfig(ui=mission_ui)
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}), \
         patch("smo_mcs.server.load_tc_catalog", return_value=[]), \
         patch("smo_common.config.loader.load_mission_config", return_value=mc):
        return MCSServer(config_dir="/tmp/fake_config", connect_host="localhost",
                         connect_port=9999, http_port=0, ui_mode=ui_mode)


async def _mcs_client(server) -> TestClient:
    app = web.Application()
    app.router.add_get("/", server._handle_index)
    app.router.add_get("/classic", server._handle_index_classic)
    app.router.add_get("/modern", server._handle_index_modern)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class TestMCSResolution:

    def test_default_is_modern(self, monkeypatch):
        # flipped from classic on 2026-08-18 (Agent-5 gate passed)
        monkeypatch.delenv("SMO_UI", raising=False)
        assert _make_mcs()._ui_mode == "modern"

    def test_env_beats_default(self, monkeypatch):
        monkeypatch.setenv("SMO_UI", "classic")
        assert _make_mcs()._ui_mode == "classic"

    def test_cli_beats_env(self, monkeypatch):
        monkeypatch.setenv("SMO_UI", "modern")
        assert _make_mcs(ui_mode="classic")._ui_mode == "classic"

    def test_mission_config_beats_default_loses_to_env(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        assert _make_mcs(mission_ui="classic")._ui_mode == "classic"
        monkeypatch.setenv("SMO_UI", "modern")
        assert _make_mcs(mission_ui="classic")._ui_mode == "modern"

    def test_unknown_mode_falls_back_to_classic(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        assert _make_mcs(ui_mode="neon")._ui_mode == "classic"


class TestMCSRoutes:

    @pytest.mark.asyncio
    async def test_index_serves_resolved_mode_and_query_overrides(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        client = await _mcs_client(_make_mcs())
        try:
            await _assert_index(client, "/", MCS_STATIC, "modern")
            # ?ui= wins over the resolved server mode, per request.
            await _assert_index(client, "/?ui=classic", MCS_STATIC, "classic")
            await _assert_index(client, "/?ui=modern", MCS_STATIC, "modern")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_query_override_on_modern_server(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        client = await _mcs_client(_make_mcs(ui_mode="modern"))
        try:
            await _assert_index(client, "/", MCS_STATIC, "modern")
            await _assert_index(client, "/?ui=classic", MCS_STATIC, "classic")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_explicit_generation_routes(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        for mode in ("classic", "modern"):
            client = await _mcs_client(_make_mcs(ui_mode=mode))
            try:
                # both routes exist whatever the server default is
                await _assert_index(client, "/classic", MCS_STATIC, "classic")
                await _assert_index(client, "/modern", MCS_STATIC, "modern")
            finally:
                await client.close()


# ═════════════════════════════ Planner ═════════════════════════════

def _make_planner(ui_mode=None, mission_ui=None):
    orbit_cfg = OrbitConfig(
        tle_line1="1 25544U 98067A   24001.00000000  .00000000  00000-0  00000-0 0  9999",
        tle_line2="2 25544  51.6400 000.0000 0001000   0.0000   0.0000 15.50000000000000",
        ground_stations=[],
    )
    mc = MissionConfig(ui=mission_ui)
    with patch("smo_planner.server.load_orbit_config", return_value=orbit_cfg), \
         patch("smo_planner.server.OrbitPropagator", return_value=MagicMock()), \
         patch("smo_planner.server.OrbitPlanner", return_value=MagicMock()), \
         patch("smo_common.config.loader.load_mission_config", return_value=mc):
        return PlannerServer(config_dir="/tmp/fake_config", http_port=0,
                             ui_mode=ui_mode)


async def _planner_client(server) -> TestClient:
    app = web.Application()
    app.router.add_get("/", server._handle_index)
    app.router.add_get("/classic", server._handle_index_classic)
    app.router.add_get("/modern", server._handle_index_modern)
    app.router.add_get("/wide", server._handle_index_wide)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class TestPlannerResolution:

    def test_default_is_modern(self, monkeypatch):
        # flipped from classic on 2026-08-18 (Agent-5 gate passed)
        monkeypatch.delenv("SMO_UI", raising=False)
        assert _make_planner()._ui_mode == "modern"

    def test_precedence_cli_env_config(self, monkeypatch):
        monkeypatch.setenv("SMO_UI", "classic")
        assert _make_planner()._ui_mode == "classic"
        assert _make_planner(ui_mode="classic")._ui_mode == "classic"
        monkeypatch.delenv("SMO_UI", raising=False)
        assert _make_planner(mission_ui="classic")._ui_mode == "classic"


class TestPlannerRoutes:

    @pytest.mark.asyncio
    async def test_index_and_generation_routes(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        client = await _planner_client(_make_planner())
        try:
            await _assert_index(client, "/", PLANNER_STATIC, "modern")
            await _assert_index(client, "/?ui=classic", PLANNER_STATIC, "classic")
            await _assert_index(client, "/classic", PLANNER_STATIC, "classic")
            await _assert_index(client, "/modern", PLANNER_STATIC, "modern")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_wide_classic_mode_keeps_documented_redirect(self, monkeypatch):
        """/wide is a documented classroom URL: classic keeps the Phase G
        /?layout=wide redirect verbatim (plan §1.2, risk R6)."""
        monkeypatch.delenv("SMO_UI", raising=False)
        client = await _planner_client(_make_planner(ui_mode="classic"))
        try:
            resp = await client.get("/wide", allow_redirects=False)
            assert resp.status == 302
            assert resp.headers["Location"] == "/?layout=wide"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_wide_modern_mode_serves_distinct_wall_page(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        client = await _planner_client(_make_planner(ui_mode="modern"))
        try:
            resp = await client.get("/wide", allow_redirects=False)
            assert resp.status == 200, "modern /wide is a page, not a redirect"
            assert resp.headers.get("Cache-Control") == "no-cache"
            text = await resp.text()
            wide = PLANNER_STATIC / "modern" / "wide.html"
            if wide.exists():
                assert text == wide.read_text()
            else:
                assert "not installed" in text
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_wide_query_override_both_ways(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        # modern server + ?ui=classic -> classic redirect, ui carried through
        client = await _planner_client(_make_planner(ui_mode="modern"))
        try:
            resp = await client.get("/wide?ui=classic", allow_redirects=False)
            assert resp.status == 302
            assert resp.headers["Location"] == "/?layout=wide&ui=classic"
        finally:
            await client.close()
        # classic server + ?ui=modern -> the wall page, not the redirect
        client = await _planner_client(_make_planner(ui_mode="classic"))
        try:
            resp = await client.get("/wide?ui=modern", allow_redirects=False)
            assert resp.status == 200
        finally:
            await client.close()


# ════════════════════════════ Instructor ═══════════════════════════

async def _instructor_client(ui_mode=None) -> TestClient:
    app = create_instructor_app(MagicMock(), ui_mode=ui_mode)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class TestInstructorResolution:

    def test_arg_env_default_precedence(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        # flipped from classic on 2026-08-18 (Agent-5 gate passed)
        assert create_instructor_app(MagicMock())["ui_mode"] == "modern"
        monkeypatch.setenv("SMO_UI", "classic")
        assert create_instructor_app(MagicMock())["ui_mode"] == "classic"
        assert create_instructor_app(
            MagicMock(), ui_mode="classic")["ui_mode"] == "classic"

    def test_unknown_mode_falls_back_to_classic(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        assert create_instructor_app(
            MagicMock(), ui_mode="neon")["ui_mode"] == "classic"


class TestInstructorRoutes:

    @pytest.mark.asyncio
    async def test_index_query_override_and_generation_routes(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        client = await _instructor_client()
        try:
            await _assert_index(client, "/", INSTRUCTOR_STATIC, "modern")
            await _assert_index(client, "/?ui=classic", INSTRUCTOR_STATIC, "classic")
            await _assert_index(client, "/classic", INSTRUCTOR_STATIC, "classic")
            await _assert_index(client, "/modern", INSTRUCTOR_STATIC, "modern")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_ui_composes_with_sub_deep_dive_param(self, monkeypatch):
        """?ui= must compose with the existing ?sub= (plan §1.2, risk R10):
        resolution reads only `ui`; `sub` still reaches the served page."""
        monkeypatch.delenv("SMO_UI", raising=False)
        client = await _instructor_client(ui_mode="modern")
        try:
            # sub alone: resolved (modern) page, sub untouched
            await _assert_index(client, "/?sub=eps", INSTRUCTOR_STATIC, "modern")
            # sub + ui override, both orders
            await _assert_index(client, "/?sub=eps&ui=classic",
                                INSTRUCTOR_STATIC, "classic")
            await _assert_index(client, "/?ui=classic&sub=eps",
                                INSTRUCTOR_STATIC, "classic")
        finally:
            await client.close()

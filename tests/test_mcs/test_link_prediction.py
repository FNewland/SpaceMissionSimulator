"""Predicted link endpoint (POSITION_NEEDS G5).

GET /api/displays/link-prediction: range → FSPL → expected Eb/N0 per tier
for the current/next pass, from the MCS's own propagator + the RF
constants in config — same arithmetic as tools/link_budget_ladder.py.
"""
import math
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_mcs.link_prediction import LinkPredictor
from smo_mcs.server import MCSServer

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("SMO_TM_ARCHIVE", str(tmp_path / "a.db"))
    return MCSServer(config_dir=str(CONFIG_DIR), http_port=0)


class TestBudgetArithmetic:
    """The chain must reproduce the ladder tool's numbers."""

    def test_fspl_matches_report_table(self, server):
        lp = server._link_predictor
        # Report table B.1 at 450 km / 2200 MHz: 1944.5 km at 5 deg.
        d = 1944.5
        expected = (20 * math.log10(d * 1000)
                    + 20 * math.log10(2200.0e6) - 147.55)
        assert lp.fspl_db(d) == pytest.approx(expected, abs=0.01)

    def test_point_has_all_config_tiers(self, server):
        p = server._link_predictor.point(2000.0, 10.0)
        assert set(p["tiers"]) == {"SAFE", "LOW", "HIGH"}
        for tv in p["tiers"].values():
            assert tv["margin_db"] == pytest.approx(
                tv["eb_n0_db"] - tv["required_eb_n0_db"], abs=0.02)

    def test_higher_rate_means_lower_eb_n0(self, server):
        p = server._link_predictor.point(2000.0, 10.0)
        assert p["tiers"]["HIGH"]["eb_n0_db"] < p["tiers"]["LOW"]["eb_n0_db"]
        # 16k -> 128k is 9.03 dB of rate.
        assert (p["tiers"]["LOW"]["eb_n0_db"]
                - p["tiers"]["HIGH"]["eb_n0_db"]) == pytest.approx(9.03,
                                                                  abs=0.05)

    def test_unconfigured_dir_is_unavailable_not_fabricated(self, tmp_path):
        lp = LinkPredictor(tmp_path, None)
        out = lp.predict(None)
        assert out["available"] is False
        assert out["reason"]


class TestEndpoint:
    async def _client(self, server):
        app = web.Application()
        app.router.add_get("/api/displays/link-prediction",
                           server._handle_link_prediction)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_endpoint_shape_for_overlay(self, server):
        client = await self._client(server)
        try:
            resp = await client.get("/api/displays/link-prediction")
            assert resp.status == 200
            data = await resp.json()
            assert data["time_base"] == "sim"
            assert data["available"] is True
            assert data["pass"]["which"] in ("current", "next")
            assert data["pass"]["gs_name"]
            s = data["series"]
            for key in ("range_km", "fspl_db", "elevation_deg"):
                assert s[key], f"empty series {key}"
                t, v = s[key][0]
                assert isinstance(t, (int, float))
                assert isinstance(v, (int, float))
            # Per-tier Eb/N0 series with matching timestamps.
            assert set(s["eb_n0_db"]) == {t["name"] for t in data["tiers"]}
            for series in s["eb_n0_db"].values():
                assert len(series) == len(s["range_km"])
            # Every tier carries the required Eb/N0 so the client can draw
            # the threshold line.
            for t in data["tiers"]:
                assert "required_eb_n0_db" in t
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_endpoint_without_propagator_is_honest(self, server):
        server._link_predictor._orbit_prop = None
        client = await self._client(server)
        try:
            resp = await client.get("/api/displays/link-prediction")
            assert resp.status == 200
            data = await resp.json()
            assert data["available"] is False
            assert "propagator" in data["reason"]
        finally:
            await client.close()

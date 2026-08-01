"""Smoke + functional tests for the ADCS failure-scenario viewer (tools/).

The viewer is a thin aiohttp wrapper around ``ADCS_failure_sim/cubesat_adcs_sim.py``
(the shared physics module). These tests check that:

* the module imports and its three routes plus the defaults endpoint are registered;
* the physics module accepts the viewer's parameter dict, honours it, and reports
  the three storyline events (impact, recovery, automatic safe-mode re-trip);
* the viewer's stats/CSV helpers work against main's channel names, including the
  channels the fork's embedded copy did not have (``est_err_deg``, ``triad_used``,
  ``wheel_rpm_*``);
* a run served over HTTP returns non-empty plots and a downloadable CSV.

Everything runs a *very* short scenario (a few hundred seconds of sim time) so the
whole file stays well under a couple of seconds.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "ADCS_failure_sim"))

import adcs_sim_viewer as viewer  # noqa: E402
import cubesat_adcs_sim as adcs  # noqa: E402


SHORT = {
    "t_total": 240.0,
    "dt": 1.0,
    "t_impact": 60.0,
    "t_recovery": 120.0,
    "corrupted_axis": 0,
    "corruption_factor": 10.0,
}


def test_routes_registered():
    app = viewer.create_app()
    routes = {(r.method, r.resource.canonical) for r in app.router.routes()}
    assert ("GET", "/") in routes
    assert ("POST", "/run") in routes
    assert ("GET", "/download") in routes
    assert ("GET", "/api/defaults") in routes


def test_viewer_uses_shared_physics_module():
    """The viewer must not carry its own copy of the physics."""
    assert viewer.adcs.__file__ == str(REPO / "ADCS_failure_sim" / "cubesat_adcs_sim.py")
    assert not hasattr(viewer, "run_sim"), "viewer should not embed a simulation loop"


def test_defaults_match_the_configuration_block():
    payload = viewer._defaults_payload()
    assert payload["defaults"]["t_impact"] == adcs.T_IMPACT
    assert payload["defaults"]["corruption_factor"] == adcs.CORRUPTION_FACTOR
    assert payload["defaults"]["epoch"] == adcs.EPOCH_UTC.strftime("%Y-%m-%dT%H:%M")


def test_unknown_parameter_is_rejected():
    with pytest.raises(ValueError):
        adcs.resolve_params({"not_a_parameter": 1})


def test_short_run_produces_events_stats_and_csv():
    log, events, cfg = viewer.run_scenario(SHORT)

    # honours the parameters it was given
    assert cfg["t_total"] == SHORT["t_total"]
    assert len(log) == int(SHORT["t_total"] / SHORT["dt"]) + 1

    # main's superset of channels is present
    for key in ("utc", "triad_used", "est_err_deg", "wheel_rpm_x", "tumble_rate_dps"):
        assert key in log[0]
    assert len(log[0]) == 38

    # the three storyline events fire, in order
    kinds = [e["type"] for e in events]
    assert kinds[:2] == ["impact", "recovery"]
    assert "autosafe" in kinds
    assert events[0]["t"] == SHORT["t_impact"]
    assert events[1]["t"] == SHORT["t_recovery"]

    stats = viewer.compute_stats(log, cfg)
    assert stats["samples"] == len(log)
    assert stats["channels"] == 38
    assert stats["max_h_pct"] > 0
    assert stats["max_tumble"] > 0
    assert stats["peak_wheel"] in ("X", "Y", "Z")

    csv_text = viewer.make_csv(log)
    assert csv_text.splitlines()[0].startswith("t,utc,mode")
    assert len(csv_text.splitlines()) == len(log) + 1


def test_cli_entry_point_still_works_with_defaults():
    """``run()`` with no arguments must reproduce the module constants."""
    cfg = adcs.resolve_params()
    assert cfg["t_total"] == adcs.T_TOTAL
    assert cfg["t_impact"] == adcs.T_IMPACT
    assert cfg["dt"] == adcs.DT


@pytest.mark.skipif(not viewer.HAVE_MATPLOTLIB, reason="matplotlib not installed")
async def test_http_run_and_download():
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(viewer.create_app())) as client:
        resp = await client.get("/")
        assert resp.status == 200
        assert "ADCS Failure-Scenario Viewer" in await resp.text()

        resp = await client.post("/run", json=SHORT)
        assert resp.status == 200
        data = await resp.json()
        assert set(data["plots"]) == {"sensors", "estimator", "actuators", "zoom"}
        for name, b64 in data["plots"].items():
            png = base64.b64decode(b64)
            assert png.startswith(b"\x89PNG"), name
            assert len(png) > 5000, name
        assert data["events"]
        assert data["stats"]["channels"] == 38

        resp = await client.get(f"/download?id={data['run_id']}")
        assert resp.status == 200
        assert (await resp.text()).splitlines()[0].startswith("t,utc,mode")

        assert (await client.get("/download?id=nosuchrun")).status == 404
        assert (await client.post("/run", json={"bogus": 1})).status == 400

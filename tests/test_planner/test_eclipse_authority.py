"""The planner's eclipse reads come from the single authority.

Owner report: "AOCS seems to be in eclipse when the planner etc. show we
should not be." Root cause: the planner propagated its OWN orbit on its OWN
anchored clock to answer "is it eclipse right now", so near a terminator
crossing it disagreed with the spacecraft — by up to (anchor age x speed)
seconds, which at 60x is ~90 s twice per orbit.

Split of responsibility now:

* PRESENT time  — read from the spacecraft (``/api/state`` carries
  ``in_eclipse`` / ``sun_fraction`` from the on-board authority). The planner
  caches it in ``_sim_illum`` and ``_get_spacecraft_state`` prefers it.
* FUTURE time   — the planner must propagate (ground track, eclipse arcs,
  power budget over a horizon). Legitimate, but it runs the SAME geometry
  function, so a prediction for instant T equals what the spacecraft reports
  when it gets to T.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from smo_common.orbit.eclipse import SHADOW_MODEL, shadow_state
from smo_common.orbit.propagator import OrbitPropagator
from smo_planner.orbit_planner import OrbitPlanner
from smo_planner.server import PlannerServer

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"


def _orbit_cfg() -> dict:
    return yaml.safe_load((CONFIG_DIR / "orbit.yaml").read_text())


def _prop() -> OrbitPropagator:
    cfg = _orbit_cfg()
    return OrbitPropagator(cfg["tle_line1"], cfg["tle_line2"],
                           earth_radius_km=cfg.get("earth_radius_km", 6371.0))


def _t0() -> datetime:
    return datetime.fromisoformat(
        str(_orbit_cfg()["t0_epoch"]).replace("Z", "+00:00"))


def test_ground_track_carries_the_authority_fields():
    """Every predicted point exposes the same geometry the spacecraft flies."""
    planner = OrbitPlanner(_prop())
    track = planner.predict_ground_track(_t0(), duration_hours=2.0, step_s=30.0)
    assert track
    ref = _prop()
    saw_eclipse = saw_sun = False
    for pt in track:
        t = datetime.fromisoformat(pt["utc"])
        ref.reset(t)
        expect = shadow_state(ref.state.pos_eci, ref.state.sun_eci, 6371.0)
        assert pt["in_eclipse"] == expect.in_eclipse, pt["utc"]
        assert pt["sun_fraction"] == pytest.approx(expect.sun_fraction, abs=1e-4)
        assert pt["in_eclipse"] == (pt["sun_fraction"] <= 0.0)
        saw_eclipse |= pt["in_eclipse"]
        saw_sun |= not pt["in_eclipse"]
    assert saw_eclipse and saw_sun


def test_planner_prediction_matches_the_spacecraft_when_it_gets_there():
    """A FUTURE eclipse prediction is the same computation as the on-board
    present-time flag, so it is right when the time arrives."""
    ground = _prop()
    onboard = _prop()             # stands in for the simulator's propagator
    t0 = _t0()
    for offset in range(0, 6000, 47):
        t = t0 + timedelta(seconds=offset)
        ground.reset(t)
        onboard.propagate_to(t)
        assert ground.state.in_eclipse == onboard.state.in_eclipse
        assert ground.state.sun_fraction == pytest.approx(
            onboard.state.sun_fraction, abs=1e-12)
        assert ground.state.shadow_model == SHADOW_MODEL


def _fake_planner() -> PlannerServer:
    srv = PlannerServer.__new__(PlannerServer)
    srv._time_source = "sim"
    srv._sim_illum = None
    return srv


def test_present_time_eclipse_is_read_from_the_spacecraft():
    """When the sim anchor is fresh, the planner reports the spacecraft's
    flag rather than re-deriving one on its own clock."""
    srv = _fake_planner()
    # The propagated (ground) view says SUNLIT...
    state = SimpleNamespace(
        utc=_t0(), lat_deg=0.0, lon_deg=0.0, alt_km=513.0,
        vel_x=7.6, vel_y=0.0, in_eclipse=False, sun_fraction=1.0,
        in_contact=False, active_gs=None, solar_beta_deg=-30.6,
        gs_elevation_deg=-10.0, gs_range_km=3000.0, shadow_model=SHADOW_MODEL)
    srv._live_prop = SimpleNamespace(
        reset=lambda t: None, advance=lambda dt: state)
    srv._get_now = lambda: _t0()

    # ...with no spacecraft data, that is what is served, honestly labelled.
    out = PlannerServer._get_spacecraft_state(srv)
    assert out["in_eclipse"] is False
    assert out["eclipse_source"] == "ground_propagator"

    # ...but the spacecraft says UMBRA, and the spacecraft wins.
    srv._sim_illum = {"in_eclipse": True, "sun_fraction": 0.0,
                      "in_penumbra": False, "shadow_model": SHADOW_MODEL,
                      "wall": time.time()}
    out = PlannerServer._get_spacecraft_state(srv)
    assert out["in_eclipse"] is True
    assert out["sun_fraction"] == 0.0
    assert out["eclipse_source"] == "spacecraft"

    # A stale anchor must not be trusted — fall back and say so.
    srv._sim_illum["wall"] = time.time() - 3600.0
    out = PlannerServer._get_spacecraft_state(srv)
    assert out["in_eclipse"] is False
    assert out["eclipse_source"] == "ground_propagator"


def test_real_mode_never_uses_the_sim_illumination_cache():
    srv = _fake_planner()
    srv._time_source = "real"
    state = SimpleNamespace(
        utc=_t0(), lat_deg=0.0, lon_deg=0.0, alt_km=513.0,
        vel_x=7.6, vel_y=0.0, in_eclipse=False, sun_fraction=1.0,
        in_contact=False, active_gs=None, solar_beta_deg=-30.6,
        gs_elevation_deg=-10.0, gs_range_km=3000.0, shadow_model=SHADOW_MODEL)
    srv._live_prop = SimpleNamespace(
        reset=lambda t: None, advance=lambda dt: state)
    srv._get_now = lambda: _t0()
    srv._sim_illum = {"in_eclipse": True, "sun_fraction": 0.0,
                      "in_penumbra": False, "shadow_model": SHADOW_MODEL,
                      "wall": time.time()}
    out = PlannerServer._get_spacecraft_state(srv)
    assert out["in_eclipse"] is False
    assert out["eclipse_source"] == "ground_propagator"

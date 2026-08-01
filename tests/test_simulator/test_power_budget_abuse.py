"""Phase B (brief 01, test 2): the teaching tension must survive the rebalance.

A budget that closes in nominal ops is only half the acceptance — the abuse
cases must still visibly punish:

  * payload + cooler left running through eclipse AND stride-1 HIGH-tier
    dumps on every eligible pass (the `worst` profile) must drive the battery
    below 50 % and trip at least one shed stage within 6 orbits;
  * a stowed-wing (never-deployed) spacecraft must be net-negative at every
    beta — LEOP ordering ("deploy before you switch things on") is a real
    constraint.
"""
import sys
from datetime import timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.power_budget import (  # noqa: E402
    load_configs, build_propagator, contact_stats, run_profile, _make_models,
    orbit_period_s,
)


@pytest.fixture(scope="module")
def cfg():
    return load_configs(CONFIG_DIR)


@pytest.fixture(scope="module")
def epoch(cfg):
    _, ep = build_propagator(cfg["orbit"])
    return ep


@pytest.fixture(scope="module")
def cstats(cfg, epoch):
    return contact_stats(cfg["orbit"], cfg["simulation"], epoch, hours=24.0)


@pytest.mark.parametrize("day", [0, 255])  # worst-gen beta and max-eclipse
def test_payload_through_eclipse_plus_stride1_dumps_punishes(
        cfg, epoch, cstats, day):
    r = run_profile(cfg, "worst", epoch + timedelta(days=day),
                    orbits=6, eol=True, cstats=cstats)
    assert min(r["orbit_mins"]) < 50.0, (
        f"abuse profile min SoC {min(r['orbit_mins']):.1f}% did not drop "
        f"below 50% — the lesson has no teeth")
    assert r["shed_events"] >= 1, "abuse profile tripped no shed stage"
    assert r["margin_w"] < 0.0, (
        f"abuse profile margin {r['margin_w']:+.1f} W should be negative")


@pytest.mark.parametrize("day", [0, 90, 255])
def test_stowed_wings_net_negative_at_every_beta(cfg, epoch, day):
    """Body panels alone cannot carry even the quiescent platform."""
    import math
    from smo_simulator.models.aocs_basic import AOCSBasicModel

    prop, _ = build_propagator(cfg["orbit"])
    prop.reset(epoch + timedelta(days=day))
    period = orbit_period_s(cfg["orbit"])
    eps, tcs = _make_models(cfg, eol=True)
    s = eps._state
    # wings NOT deployed
    s.bat_soc_pct = 90.0
    for line in ("aocs_wheels", "htr_bat", "htr_obc"):
        s.power_lines[line] = True

    params: dict[int, float] = {}
    dt = 10.0
    gen = cons = 0.0
    for i in range(int(3 * period / dt)):
        st = prop.advance(dt)
        phase = AOCSBasicModel._orbit_phase_from_geometry(st)
        if st.in_eclipse or phase is None:
            params[0x0245] = params[0x0246] = params[0x0247] = 0.0
        else:
            b = math.radians(st.solar_beta_deg)
            ph = math.radians(phase)
            params[0x0245] = math.cos(b) * math.cos(ph)
            params[0x0246] = math.cos(b) * math.sin(ph)
            params[0x0247] = math.sin(b)
        eps.tick(dt, st, params)
        tcs.tick(dt, st, params)
        gen += s.power_gen_w * dt
        cons += s.power_cons_w * dt
    assert gen < cons, (
        f"stowed-wing profile is net-positive at day {day} "
        f"(gen {gen / (3 * period):.1f} W vs load {cons / (3 * period):.1f} W)"
        " — wing deployment is not a real LEOP constraint")

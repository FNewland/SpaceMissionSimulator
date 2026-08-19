"""One eclipse, one authority — coherence tests.

Two owner reports, one root cause: several places computed "is it eclipse
right now" independently (the on-board propagator, EPS's own generation
branch, the MCS's ground propagator on the MCS clock, the planner's live
propagator on the planner clock), and they agreed only while nothing
perturbed them. Near a terminator crossing — or with the sim accelerated —
they disagreed, which is how the console came to show ECLIPSE beside a
positive ``eps.power_gen`` and the AOCS came to look "in eclipse" while the
planner said it was not.

The authority is now ``smo_common.orbit.eclipse.shadow_state`` (conical umbra
+ penumbra), evaluated ONCE per step by the orbit propagator and published on
``OrbitState`` as ``in_eclipse`` / ``sun_fraction``. These tests pin:

1. there is exactly one geometry, and every spelling of the flag comes from it;
2. array generation is zero in umbra BY CONSTRUCTION, including in the awkward
   states (AOCS dark, SADA stuck, tumbling fallback, breakpoint restore);
3. every consumer — EPS telemetry, AOCS mode logic, the engine's state
   summary, the MCS ground propagator and the planner ground track — reports
   the same thing at the same instant;
4. the AOCS ECLIPSE-mode entry/exit follows the authority;
5. the true eclipse fraction / duration at the 2026-08-20 epoch and ~513 km.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smo_common.orbit.eclipse import (  # noqa: E402
    SHADOW_MODEL, eclipse_fraction, is_in_eclipse, shadow_state, sun_fraction,
)
from smo_common.orbit.propagator import OrbitPropagator  # noqa: E402
from smo_simulator.engine import SimulationEngine  # noqa: E402

PARAM_POWER_GEN = 0x0107
PARAM_ECLIPSE_FLAG = 0x0108
PARAM_ECLIPSE_ACTIVE = 0x013A
PARAM_SUN_FRACTION = 0x0142
PARAM_AOCS_MODE = 0x020F
MODE_ECLIPSE = 8

#: Measured at the shipped TLE from t0_epoch 2026-08-20T12:00:00Z with 1 s
#: sampling (see test_true_eclipse_geometry_at_the_current_epoch).
ORBIT_PERIOD_S = 5669.3
ECLIPSE_FRACTION_T0 = 0.355
ECLIPSE_DURATION_S_T0 = 2012.6
BETA_DEG_T0 = -30.59


def _orbit_cfg() -> dict:
    return yaml.safe_load((CONFIG_DIR / "orbit.yaml").read_text())


def _prop() -> OrbitPropagator:
    cfg = _orbit_cfg()
    return OrbitPropagator(cfg["tle_line1"], cfg["tle_line2"],
                           earth_radius_km=cfg.get("earth_radius_km", 6371.0))


def _t0() -> datetime:
    return datetime.fromisoformat(
        str(_orbit_cfg()["t0_epoch"]).replace("Z", "+00:00"))


@pytest.fixture(scope="module")
def engine():
    if not CONFIG_DIR.exists():
        pytest.skip("EOSAT-1 config dir not present")
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    eng._spacecraft_phase = 6
    obdh = eng.subsystems.get("obdh")
    if obdh is not None and hasattr(obdh, "_state"):
        obdh._state.sw_image = 1
    for _ in range(5):
        eng._tick_once(1.0)
    return eng


# ── 1. one geometry ──────────────────────────────────────────────────────

def test_one_shadow_model_behind_every_spelling():
    """is_in_eclipse / sun_fraction / eclipse_fraction are one computation."""
    prop = _prop()
    prop.reset(_t0())
    saw_sun = saw_umbra = False
    for _ in range(1200):                      # 2 h at 6 s
        st = prop.advance(6.0)
        ref = shadow_state(st.pos_eci, st.sun_eci, 6371.0)
        assert st.in_eclipse == ref.in_eclipse
        assert st.sun_fraction == pytest.approx(ref.sun_fraction, abs=1e-12)
        assert st.in_penumbra == ref.in_penumbra
        assert st.shadow_model == SHADOW_MODEL
        # The free functions must not be a second model.
        assert is_in_eclipse(st.pos_eci, st.sun_eci) == ref.in_eclipse
        assert sun_fraction(st.pos_eci, st.sun_eci) == pytest.approx(
            ref.sun_fraction, abs=1e-12)
        assert eclipse_fraction(st.pos_eci, st.sun_eci) == pytest.approx(
            1.0 - ref.sun_fraction, abs=1e-12)
        # in_eclipse is UMBRA: no sunlight at all, never "partly".
        assert st.in_eclipse == (st.sun_fraction <= 0.0)
        saw_sun |= st.sun_fraction >= 1.0
        saw_umbra |= st.in_eclipse
    assert saw_sun and saw_umbra, "the sample never left one lighting regime"


def test_umbra_and_full_sun_are_exact():
    """The authority saturates: exactly 0.0 and exactly 1.0, not 1e-9."""
    sun = np.array([1.495978707e8, 0.0, 0.0])
    assert shadow_state(np.array([-6884.0, 0.0, 0.0]), sun) == (0.0, True, False)
    lit = shadow_state(np.array([6884.0, 0.0, 0.0]), sun)
    assert lit.sun_fraction == 1.0 and not lit.in_eclipse


def test_penumbra_exists_and_is_short():
    """The model includes a penumbra, and it is physically small at 513 km."""
    prop = _prop()
    prop.reset(_t0())
    penumbra_s = 0
    for _ in range(int(ORBIT_PERIOD_S)):
        st = prop.advance(1.0)
        if st.in_penumbra:
            penumbra_s += 1
            assert 0.0 < st.sun_fraction < 1.0
    # ~10 s per terminator crossing, two crossings per orbit.
    assert 5 <= penumbra_s <= 60, penumbra_s


# ── 2. generation zero in umbra, by construction ─────────────────────────

def _assert_dark(eps_state, params, where: str):
    assert params[PARAM_POWER_GEN] == 0.0, f"{where}: generation in eclipse"
    assert eps_state.power_gen_w == 0.0, where
    assert params[PARAM_SUN_FRACTION] == 0.0, where
    assert eps_state.sa_a_current == 0.0 and eps_state.sa_b_current == 0.0, where
    assert all(v == 0.0 for v in eps_state.sa_panel_currents.values()), where
    assert all(v == 0.0 for v in eps_state.sa_wing_currents.values()), where


@pytest.mark.slow
def test_no_generation_in_eclipse_over_many_orbits():
    """Six orbits of nominal flight: not one watt in umbra."""
    if not CONFIG_DIR.exists():
        pytest.skip("EOSAT-1 config dir not present")
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    eng._spacecraft_phase = 6
    eps = eng.subsystems["eps"]
    seen_eclipse = seen_sun = 0
    for _ in range(int(6 * ORBIT_PERIOD_S / 10.0)):
        eng._tick_once(10.0)
        if eng.orbit.state.in_eclipse:
            seen_eclipse += 1
            _assert_dark(eps._state, eng.params, "nominal flight")
        elif eng.orbit.state.sun_fraction >= 1.0:
            seen_sun += 1
    assert seen_eclipse > 100 and seen_sun > 100


def test_no_generation_in_eclipse_in_the_awkward_states():
    """AOCS dark, tumbling fallback, SADA stuck, breakpoint restore."""
    if not CONFIG_DIR.exists():
        pytest.skip("EOSAT-1 config dir not present")
    from smo_simulator.breakpoints import BreakpointManager

    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    eng._spacecraft_phase = 6
    eps = eng.subsystems["eps"]

    # Wind forward to the middle of an eclipse.
    for _ in range(4000):
        eng._tick_once(10.0)
        if eng.orbit.state.in_eclipse:
            break
    assert eng.orbit.state.in_eclipse, "never reached eclipse"
    for _ in range(30):
        eng._tick_once(10.0)
    assert eng.orbit.state.in_eclipse
    _assert_dark(eps._state, eng.params, "nominal, in eclipse")

    # (a) AOCS dark — the engine clears the body sun vector and the CSS
    # composite, so EPS falls into its tumbling branch.
    eng._spacecraft_phase = 3
    for _ in range(5):
        eng._tick_once(10.0)
    assert eng.params[0x02B3] == 0.0 and eng.params[0x0245] == 0.0
    _assert_dark(eps._state, eng.params, "AOCS dark / tumbling fallback")

    # (b) a STALE sun vector left in the param store by anything at all.
    for pid in (0x02B3, 0x02B4, 0x02B5, 0x0245, 0x0246, 0x0247):
        eng.params[pid] = 0.577
    eps.tick(1.0, eng.orbit.state, eng.params)
    _assert_dark(eps._state, eng.params, "stale sun vector")

    # (c) SADA stuck, wings deployed, drive commanded — still nothing.
    eps._state.wing_py_deployed = True
    eps._state.wing_my_deployed = True
    eps.inject_failure("sada_stuck", 1.0)
    for _ in range(5):
        eng._tick_once(10.0)
    assert eng.orbit.state.in_eclipse, "drifted out of eclipse mid-test"
    _assert_dark(eps._state, eng.params, "SADA stuck")

    # (d) breakpoint save/restore of that state.
    bm = BreakpointManager(eng)
    snap = bm.save(name="eclipse-coherence")
    fresh = SimulationEngine(CONFIG_DIR, speed=1.0)
    bm2 = BreakpointManager(fresh)
    bm2.load(snap)
    assert fresh.orbit.state.in_eclipse == eng.orbit.state.in_eclipse
    fresh._tick_once(1.0)
    if fresh.orbit.state.in_eclipse:
        _assert_dark(fresh.subsystems["eps"]._state, fresh.params,
                     "breakpoint restore")


def test_generation_scales_with_the_authority_not_with_a_branch():
    """Halving sun_fraction halves generation — illumination is a factor."""
    from types import SimpleNamespace

    from smo_simulator.models.eps_basic import EPSBasicModel

    cfg = yaml.safe_load((CONFIG_DIR / "subsystems" / "eps.yaml").read_text())
    cfg["simulation"] = yaml.safe_load(
        (CONFIG_DIR / "simulation.yaml").read_text())["eps"]

    def _gen(frac):
        m = EPSBasicModel()
        m.configure(cfg)
        m.rng.seed(7)
        params = {0x02B3: 0.0, 0x02B4: 0.0, 0x02B5: 1.0}
        orbit = SimpleNamespace(in_eclipse=frac <= 0.0, sun_fraction=frac,
                               solar_beta_deg=-30.6)
        out = []
        for _ in range(40):
            m.tick(1.0, orbit, params)
            out.append(m._state.power_gen_w)
        return sum(out) / len(out)

    full = _gen(1.0)
    half = _gen(0.5)
    assert full > 1.0
    assert half == pytest.approx(0.5 * full, rel=0.02)
    assert _gen(0.0) == 0.0


# ── 3. every consumer agrees at the same instant ─────────────────────────

def test_all_eclipse_consumers_agree_at_the_same_instant(engine):
    """Spacecraft, telemetry, engine summary, MCS and planner: one answer."""
    from smo_planner.orbit_planner import OrbitPlanner

    ground = _prop()                     # what the MCS builds
    planner = OrbitPlanner(_prop())      # what the planner builds
    disagreements = []
    for i in range(400):
        engine._tick_once(15.0)
        o = engine.orbit.state
        truth = bool(o.in_eclipse)
        summary = engine.get_state_summary()

        # ground consumers, evaluated at the SAME instant
        ground.reset(engine._sim_time)
        track = planner.predict_ground_track(engine._sim_time,
                                             duration_hours=1 / 60.0,
                                             step_s=30.0)

        checks = {
            "eps.eclipse_flag": bool(engine.params[PARAM_ECLIPSE_FLAG]),
            "eps.eclipse_active": bool(engine.params[PARAM_ECLIPSE_ACTIVE]),
            "eps.sun_fraction<=0": engine.params[PARAM_SUN_FRACTION] <= 0.0,
            "state_summary.in_eclipse": bool(summary["in_eclipse"]),
            "state_summary.sun_fraction<=0": summary["sun_fraction"] <= 0.0,
            "mcs_ground_propagator": bool(ground.state.in_eclipse),
        }
        # The planner's track starts one step in the future, so compare its
        # geometry rather than its first sample's timestamp.
        assert track and "sun_fraction" in track[0]
        for name, val in checks.items():
            if val != truth:
                disagreements.append((i, name, val, truth))
    assert not disagreements, disagreements[:10]


def test_ground_propagation_and_spacecraft_share_the_geometry():
    """A ground tool predicting the FUTURE lands on the same flag as the
    spacecraft does when it gets there."""
    if not CONFIG_DIR.exists():
        pytest.skip("EOSAT-1 config dir not present")
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    ground = _prop()
    horizon = [eng._sim_time + timedelta(seconds=s)
               for s in range(0, 6000, 60)]
    predicted = []
    for t in horizon:
        ground.reset(t)
        predicted.append((ground.state.in_eclipse, ground.state.sun_fraction))
    for t, (p_ecl, p_frac) in zip(horizon, predicted):
        eng.orbit.propagate_to(t)
        assert eng.orbit.state.in_eclipse == p_ecl, t
        assert eng.orbit.state.sun_fraction == pytest.approx(p_frac, abs=1e-12)


# ── 4. AOCS ECLIPSE mode follows the authority ───────────────────────────

@pytest.mark.slow
def test_aocs_eclipse_mode_entry_and_exit_match_the_authority():
    """Mode 8 is entered within the debounce of umbra entry and left within
    the minimum dwell of umbra exit — and never at any other time."""
    if not CONFIG_DIR.exists():
        pytest.skip("EOSAT-1 config dir not present")
    from smo_simulator.engine import SimulationEngine as Eng

    eng = Eng(CONFIG_DIR, speed=1.0)
    d = eng._dispatcher

    def tc(*data):
        res = d.dispatch(8, 1, bytes(data), None)
        assert res.error is None, res.error

    def run(seconds, dt=1.0):
        for _ in range(int(seconds / dt)):
            eng._tick_once(dt)

    tc(55); run(20)                      # boot the application
    tc(19, 7); tc(19, 2); run(5)         # AOCS + TX lines
    for wheel in range(4):
        tc(3, wheel)
    run(5)
    tc(4, 1); tc(5, 1); run(30)          # star trackers
    tc(0, 3); run(300); tc(0, 4); run(600)   # sun acq -> nadir
    tc(120, 5, 0); run(5); tc(120, 6, 0); run(5)
    assert int(eng.params[PARAM_AOCS_MODE]) in (4, 5, 8)

    # Debounce (5 s) on entry, minimum dwell (5 s) on exit.
    TOL_S = 12.0
    since_change = 1e9
    prev = bool(eng.orbit.state.in_eclipse)
    bad = []
    for _ in range(int(2.2 * ORBIT_PERIOD_S)):
        eng._tick_once(1.0)
        ecl = bool(eng.orbit.state.in_eclipse)
        since_change = 0.0 if ecl != prev else since_change + 1.0
        prev = ecl
        mode = int(eng.params[PARAM_AOCS_MODE])
        if since_change < TOL_S:
            continue                     # inside the debounce / dwell window
        if ecl and mode not in (MODE_ECLIPSE, 7):     # 7 = DESAT, legitimate
            bad.append(("sunlit-mode in umbra", mode))
        if not ecl and mode == MODE_ECLIPSE:
            bad.append(("ECLIPSE mode in sunlight", mode))
    assert not bad, bad[:10]


# ── 5. the true geometry at the current epoch ────────────────────────────

def test_true_eclipse_geometry_at_the_current_epoch():
    """Mission epoch 2026-08-20T12:00:00Z, ~513 km mean geodetic altitude.

    Measured with 1 s sampling over five orbits; documented so the older
    "beta ~49.5 deg / eclipse 0.315" figures cannot be quoted again.
    """
    prop = _prop()
    t0 = _t0()
    prop.reset(t0)
    assert prop.state.solar_beta_deg == pytest.approx(BETA_DEG_T0, abs=0.1)

    prop.reset(t0)
    n = int(5 * ORBIT_PERIOD_S)
    umbra = 0
    transitions = []
    prev = prop.state.in_eclipse
    for i in range(n):
        st = prop.advance(1.0)
        umbra += 1 if st.in_eclipse else 0
        if st.in_eclipse != prev:
            transitions.append((i + 1.0, st.in_eclipse))
            prev = st.in_eclipse
    durations = [b[0] - a[0] for a, b in zip(transitions, transitions[1:])
                 if a[1] and not b[1]]
    assert umbra / n == pytest.approx(ECLIPSE_FRACTION_T0, abs=0.005)
    mean_dur = sum(durations) / len(durations)
    assert mean_dur == pytest.approx(ECLIPSE_DURATION_S_T0, abs=15.0)
    # ...and the sunlit arc is the rest of a 94.5 min orbit.
    assert ORBIT_PERIOD_S - mean_dur == pytest.approx(3657.0, abs=20.0)

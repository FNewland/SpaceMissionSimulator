"""2026-08b Phase 2b: the generation-path bugs.

Each test here locks one defect that made a documented, commandable, or
injectable behaviour a measured no-op:

1. the solar-array-drive device gate (0x0101) zeroed the BODY panels too;
2. the AOCS-published body sun vector 0x02B3-B5 went stale (never refreshed,
   never cleared) whenever AOCS was not being ticked;
3. `sa_a_degradation` / `sa_b_degradation` were applied only in the tumbling
   fallback, so `solar_array_partial` moved generation by 0.4 W;
4. the array aging rate in code and in eps.yaml disagreed by 10x;
6. the per-subsystem power breakdown did not sum to the bus load;
7. wing status was invisible in the default HK set.

(5 — the dead SADA rate limit and the un-triggerable stuck-drive failure —
is covered in test_eps_generation_physics.py, next to the rest of the drive.)
"""
import math
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smo_simulator.models.eps_basic import EPSBasicModel  # noqa: E402


class _Orbit:
    def __init__(self, beta=20.0, eclipse=False):
        self.solar_beta_deg = beta
        self.in_eclipse = eclipse


#: A sun direction well off every axis, so body faces AND wings are all lit.
SUN = (0.70, 0.34, -0.63)


def _model(**state):
    cfg = yaml.safe_load((CONFIG_DIR / "subsystems" / "eps.yaml").read_text())
    cfg["simulation"] = yaml.safe_load(
        (CONFIG_DIR / "simulation.yaml").read_text())["eps"]
    m = EPSBasicModel()
    m.configure(cfg)
    m.rng.seed(20260812)
    s = m._state
    s.wing_py_deployed = True
    s.wing_my_deployed = True
    s.bat_soc_pct = 90.0
    s._prev_soc = 90.0
    for k, v in state.items():
        setattr(s, k, v)
    return m


def _mean_gen(m, n=40, sun=SUN, orbit=None, settle=180):
    """Mean generation AFTER the solar array drive has finished slewing.

    2026-08b: the shaft is rate-limited to `sada_max_rate_deg_s`, so a
    freshly-configured model starts at angle 0 and takes up to 180 s to reach
    the tracked bearing. Measuring through that transient compares a settled
    array against an unsettled one.
    """
    orbit = orbit or _Orbit()
    params = {0x02B3: sun[0], 0x02B4: sun[1], 0x02B5: sun[2]}
    for _ in range(settle):
        m.tick(1.0, orbit, params)
    total = 0.0
    for _ in range(n):
        m.tick(1.0, orbit, params)
        total += m._state.power_gen_w
    return total / n


class TestDeviceGateSparesBodyPanels:
    """Fix 1. Body cells are bonded to the structure and wired straight to the
    MPPT — they need no drive."""

    def test_residual_is_the_body_contribution(self):
        healthy = _mean_gen(_model())
        m = _model()
        m.set_device_state(0x0101, False)
        gated = _mean_gen(m)
        assert 0.0 < gated < 0.5 * healthy, (
            f"SADA-off generation {gated:.1f} W against {healthy:.1f} W "
            f"healthy — body panels must survive, wings must not")

    def test_wing_currents_are_the_thing_that_zeroes(self):
        m = _model()
        m.set_device_state(0x0101, False)
        _mean_gen(m, n=5, settle=10)
        assert m._state.sa_wing_currents["py"] == 0.0
        assert m._state.sa_wing_currents["my"] == 0.0
        # +X and +Y are lit by SUN; the ±Y faces keep their BODY share only,
        # so py's current must survive but be well below the healthy value
        # (which includes the 0.120 m2 wing on top of the 0.030 m2 face).
        healthy = _model()
        _mean_gen(healthy, n=5, settle=10)
        assert m._state.sa_panel_currents["px"] > 0.0
        assert m._state.sa_panel_currents["py"] > 0.0
        assert m._state.sa_panel_currents["py"] < \
            0.5 * healthy._state.sa_panel_currents["py"]

    def test_ab_split_still_reconciles_with_generation(self):
        m = _model()
        m.set_device_state(0x0101, False)
        _mean_gen(m, n=5, settle=10)
        s = m._state
        bus = max(s.bus_voltage, 20.0)
        assert (s.sa_a_current + s.sa_b_current) * bus == pytest.approx(
            s.power_gen_w, rel=0.02)


class TestStringDegradationBitesWhenSunlit:
    """Fix 3. Measured before: 86.20 W -> 85.78 W at A = B = 1.0."""

    def test_partial_array_a_costs_real_watts(self):
        full = _mean_gen(_model())
        half = _mean_gen(_model(sa_a_degradation=0.5))
        assert half < 0.9 * full, (
            f"solar_array_partial is still a no-op: {full:.2f} -> {half:.2f} W")

    def test_both_halves_dead_means_no_generation(self):
        dead = _mean_gen(_model(sa_a_degradation=0.0, sa_b_degradation=0.0))
        assert dead == pytest.approx(0.0, abs=0.01)

    def test_disabling_a_string_removes_its_faces(self):
        full = _mean_gen(_model())
        a_off = _mean_gen(_model(sa_a_enabled=False))
        assert 0.0 < a_off < 0.8 * full

    def test_injection_path_end_to_end(self):
        full = _mean_gen(_model())
        m = _model()
        m.inject_failure("solar_array_partial", magnitude=0.6, array="A")
        degraded = _mean_gen(m)
        assert degraded < 0.85 * full
        m.clear_failure("solar_array_partial", array="A")
        assert _mean_gen(m) == pytest.approx(full, rel=0.05)


class TestAgingRateReconciled:
    """Fix 4. Code said 3.14e-6/sunlit-hour (~1.79 %/yr); eps.yaml said
    0.00275 (0.275 %/yr); the loss-stack comment and the tests both assumed
    2.75 %/yr. The config value is now the single source."""

    def test_config_is_the_source_of_the_live_rate(self):
        m = _model()
        annual = yaml.safe_load(
            (CONFIG_DIR / "subsystems" / "eps.yaml").read_text()
        )["array_losses"]["annual_degradation"]
        assert annual == pytest.approx(0.0275)
        expected = annual / (8760.0 * EPSBasicModel.SUNLIT_FRACTION)
        assert m._sa_degradation_per_hour == pytest.approx(expected)

    def test_three_year_eol_factor(self):
        m = _model()
        m._state.sa_lifetime_hours = 3.0 * 8760.0 * EPSBasicModel.SUNLIT_FRACTION
        m.tick(1.0, _Orbit(eclipse=True), {})
        assert m._state.sa_age_factor == pytest.approx(0.9175, abs=0.002)

    def test_aging_only_accumulates_in_sunlight(self):
        m = _model()
        before = m._state.sa_lifetime_hours
        for _ in range(100):
            m.tick(36.0, _Orbit(eclipse=True), {})
        assert m._state.sa_lifetime_hours == before


class TestBreakdownReconciles:
    """Fix 6. sum(0x013C..0x0141) must equal 0x0106."""

    @staticmethod
    def _sum(params):
        return sum(params[p] for p in
                   (0x013C, 0x013D, 0x013E, 0x013F, 0x0140, 0x0141))

    def _run(self, m, params=None):
        p = dict(params or {})
        p.update({0x02B3: SUN[0], 0x02B4: SUN[1], 0x02B5: SUN[2]})
        m.tick(1.0, _Orbit(), p)
        return p

    def test_quiescent(self):
        m = _model()
        p = self._run(m)
        assert self._sum(p) == pytest.approx(p[0x0106], rel=1e-6)

    def test_all_lines_on(self):
        m = _model()
        for ln in ("ttc_tx", "payload", "fpa_cooler", "htr_bat", "htr_obc",
                   "aocs_wheels", "prop"):
            m._state.power_lines[ln] = True
        p = self._run(m, {0x0600: 2, 0x040C: 1, 0x02C8: 9.0, 0x0B1B: 14.0})
        assert self._sum(p) == pytest.approx(p[0x0106], rel=1e-6)

    def test_propulsion_is_not_lost(self):
        """The prop line belonged to no bucket at all."""
        m = _model()
        m._state.power_lines["prop"] = True
        p_off = self._run(m, {0x0B1B: 0.0})
        eps_off = p_off[0x013C]
        p_on = self._run(m, {0x0B1B: 14.0})
        # power_cons_w carries a 1 W-sigma measurement noise term.
        assert p_on[0x013C] > eps_off + 10.0

    def test_bus_short_shows_up_somewhere(self):
        """An injected 80 W bus short used to vanish from the breakdown."""
        m = _model()
        p_clean = self._run(m)
        m.inject_failure("bus_short", magnitude=1.0)
        p_short = self._run(m)
        assert self._sum(p_short) == pytest.approx(p_short[0x0106], rel=1e-6)
        assert p_short[0x013C] > p_clean[0x013C] + 70.0

    def test_eps_bucket_is_not_hard_wired_zero(self):
        m = _model()
        p = self._run(m)
        assert p[0x013C] == pytest.approx(
            EPSBasicModel.EPS_HOUSEKEEPING_SHARE_W, abs=3.0)


class TestWingStatusIsVisibleByDefault:
    """Fix 7. 0x0144 lived only in SID 8, which is not default-enabled — an
    operator could run a whole session at 18.6 W with the wings stowed and
    nothing said so."""

    def test_0x0144_is_in_sid_1(self):
        hk = yaml.safe_load(
            (CONFIG_DIR / "telemetry" / "hk_structures.yaml").read_text())
        sid1 = next(s for s in hk["structures"] if s["sid"] == 1)
        ids = [int(p["param_id"]) for p in sid1["parameters"]]
        assert 0x0144 in ids, (
            "wing_status is not in SID 1 — wings-stowed is invisible again")

    def test_sid_1_is_default_enabled(self):
        sim = yaml.safe_load((CONFIG_DIR / "simulation.yaml").read_text())
        sids = ((sim.get("telemetry") or {}).get("default_enabled_sids")
                or (sim.get("hk") or {}).get("default_enabled_sids") or [1, 11])
        assert 1 in sids

    def test_wing_status_bitmask_tracks_deployment(self):
        m = _model()
        m._state.wing_py_deployed = False
        m._state.wing_my_deployed = False
        p = {}
        m.tick(1.0, _Orbit(), p)
        assert p[0x0144] == 0.0
        m._state.wing_py_deployed = True
        m.tick(1.0, _Orbit(), p)
        assert int(p[0x0144]) & 0x01

    def test_stowed_wings_are_power_negative_and_now_diagnosable(self):
        """The behaviour the telemetry now explains."""
        deployed = _mean_gen(_model())
        stowed = _mean_gen(_model(wing_py_deployed=False, wing_my_deployed=False))
        assert stowed < 0.4 * deployed


class TestStaleSunVectorIsCleared:
    """Fix 2. `_tick_substep` ticks only {eps, ttc, obdh} below phase 4, so
    the AOCS-published body sun vector 0x02B3-B5 was never refreshed — but it
    was never cleared either, and eps_basic reads it unconditionally.

    Reproduced live: after an OBC reboot reverted phase 4 -> 3, the sun vector
    froze and generation sat pinned at ~122 W through orbit-phase changes that
    should have moved it by tens of watts.
    """

    def test_engine_clears_the_vector_when_aocs_is_not_ticked(self):
        from smo_simulator.engine import SimulationEngine
        eng = SimulationEngine(CONFIG_DIR, speed=1.0)
        # Pretend AOCS ran and left a full-scale sun vector behind.
        for pid, val in ((0x02B3, SUN[0]), (0x02B4, SUN[1]), (0x02B5, SUN[2]),
                         (0x0245, SUN[0]), (0x0246, SUN[1]), (0x0247, SUN[2])):
            eng.params[pid] = val
        eng._spacecraft_phase = 3          # bootloader/early application
        eng._tick_substep(1.0, 1.0, True)
        for pid in (0x02B3, 0x02B4, 0x02B5, 0x0245, 0x0246, 0x0247):
            assert eng.params[pid] == 0.0, (
                f"0x{pid:04X} still stale with AOCS not ticking")

    def test_eps_falls_back_to_the_tumbling_model(self):
        """With the vector cleared, EPS must use its honest Cauchy 1/4
        projection rather than a frozen attitude — a large drop, which is the
        point: an uncontrolled spacecraft does not generate sun-pointing
        power."""
        held = _mean_gen(_model())
        m = _model()
        tumbling = 0.0
        n = 40
        for _ in range(n):
            m.tick(1.0, _Orbit(), {0x02B3: 0.0, 0x02B4: 0.0, 0x02B5: 0.0})
            tumbling += m._state.power_gen_w
        tumbling /= n
        assert 0.0 < tumbling < 0.6 * held

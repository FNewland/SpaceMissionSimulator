"""Power-budget regression tests — rebuilt for Phase B (brief 01).

The previous version of this file ticked the EPS model against a synthetic
beta = 0 orbit with a hand-coded 0.38 eclipse fraction and a free-running
5700 s sun sweep — an attitude/season the real orbit never reaches (brief 01
findings 3 and 7). This rebuild drives the production EPS + TCS models from
the repo's own propagator at three epochs spanning the real annual beta range
(~49.8 / ~31.9 / ~24.2 deg), derives the TX timeline from contact_windows()
plus the configured auto-TX hold, and asserts:

  * per-orbit minimum SoC stays above 70 % for the quiescent and nominal-ops
    profiles at every beta, with ZERO load-shed events;
  * the per-orbit minimum CONVERGES (no walk-down);
  * at the worst beta, orbit-average generation EXCEEDS orbit-average load in
    nominal ops with the full EOL loss stack, the Phase-C AOCS suite draw,
    payload standby and realistic five-station TX duty plus the classroom
    commanding duty — Franz's binding acceptance criterion, held at a
    >= 1.25x margin ratio (2026-08b: the old absolute +10 W bound was sized
    against the pre-R1 0.180 m2/wing array).

The SoC shed-ladder protection tests are retained unchanged in spirit.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.power_budget import (  # noqa: E402
    load_configs, build_propagator, contact_stats, run_profile,
)
from datetime import timedelta  # noqa: E402


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


# Day offsets spanning the annual beta range of this orbit:
# day 0 ~ 49.8 deg (worst generation), +90 d ~ 32 deg, +255 d ~ 24 deg
# (max eclipse).
EPOCH_DAYS = [0, 90, 255]


class TestStandardOperationsBudgetCloses:
    @pytest.mark.parametrize("day", EPOCH_DAYS)
    @pytest.mark.parametrize("profile", ["quiescent", "nominal"])
    def test_profile_closes_at_beta(self, cfg, epoch, cstats, day, profile):
        r = run_profile(cfg, profile, epoch + timedelta(days=day),
                        orbits=8, eol=True, cstats=cstats)
        mins = r["orbit_mins"]
        assert r["shed_events"] == 0, (
            f"{profile} day {day}: load shed tripped ({r['shed_events']}x)")
        assert min(mins) > 70.0, (
            f"{profile} day {day}: per-orbit min SoC {min(mins):.1f}% <= 70%")
        # Converging, not walking down. With the REAL contact geometry the
        # TX duty differs orbit-to-orbit (3.38 passes/orbit is not integer),
        # so per-orbit minima legitimately scatter by ~1 point — the criterion
        # is a bounded band over the last three orbits, not a fixed point.
        assert max(mins[-3:]) - min(mins[-3:]) < 2.0, (
            f"{profile} day {day}: still trending: {mins[-4:]}")
        assert mins[0] - mins[-1] < 5.0, (
            f"{profile} day {day}: per-orbit minimum walked down: {mins}")

    def test_worst_beta_nominal_margin(self, cfg, epoch, cstats):
        """THE acceptance criterion: at the WORST beta, with the honest EOL
        loss stack and ALL nominal systems on (OBC, RX, the AOCS suite,
        heaters at thermostat duty, payload standby, real five-station TX
        duty plus the commanding duty), orbit-average generation EXCEEDS
        orbit-average load.

        2026-08b: the absolute bound was +10 W / 1.25x, which was a Phase-B
        target sized against a 0.180 m2/wing array that no 6U flies (R1:
        "a cubesat ... would struggle to generate 114 W peak"). Re-derived
        against real 6U flight hardware the array is 0.120 m2/wing, and the
        tool now also bills the classroom commanding duty the auto-TX hold
        creates (P-e), which the +10 W figure never saw.

        The criterion that BINDS is Franz's, and it is a ratio, not a fixed
        wattage: generation must exceed load with everything nominal on,
        judged against the NOMINAL/nadir case. Held at 1.25x — comfortably
        above the tool's own 1.10 closure gate, and still the tightest
        constraint on any future array change. See
        docs/plan_2026-08b/POWER_SIZING.md.
        """
        r = run_profile(cfg, "nominal", epoch, orbits=8, eol=True,
                        cstats=cstats)
        assert r["margin_w"] > 0.0, (
            f"worst-beta nominal margin {r['margin_w']:+.1f} W — generation "
            f"does not exceed load with all nominal systems on "
            f"(gen {r['gen_avg_w']:.1f}, load {r['load_avg_w']:.1f})")
        assert r["margin_ratio"] >= 1.25, (
            f"worst-beta nominal margin ratio {r['margin_ratio']:.2f} < 1.25")

    def test_imaging_closes_by_duty_cycling(self, cfg, epoch, cstats):
        """2026-08b: an imaging DUTY-CYCLE policy is how the imaging case
        closes on a 6U — three ~90 s captures per orbit with the cooler
        running around them. Holding FINE and the cooler for a whole orbit
        (the `worst` profile) is power-negative by design and is the
        exercise, not a sizing failure."""
        r = run_profile(cfg, "nominal_imaging", epoch, orbits=8, eol=True,
                        cstats=cstats)
        assert r["margin_w"] > 0.0, (
            f"duty-cycled imaging does not close: {r['margin_w']:+.1f} W "
            f"(gen {r['gen_avg_w']:.1f}, load {r['load_avg_w']:.1f})")
        assert r["shed_events"] == 0


class TestSocLoadShedStagesTrip:
    """Defect #49 — the SoC shed ladder must actually trigger, in order."""

    @pytest.fixture
    def engine(self):
        from smo_simulator.engine import SimulationEngine
        return SimulationEngine(CONFIG_DIR, speed=1.0)

    def test_stage_thresholds_come_from_simulation_yaml(self, engine):
        assert engine._load_shedding.soc_stage_thresholds() == [30.0, 20.0, 10.0]

    def test_stages_trip_in_order_when_generation_zeroed(self, engine):
        eps = engine.subsystems["eps"]
        # Kill generation: arrays disabled and permanent eclipse.
        eps._state.sa_a_enabled = False
        eps._state.sa_b_enabled = False
        for line in ("htr_bat", "htr_obc", "aocs_wheels", "payload", "ttc_tx"):
            eps._state.power_lines[line] = True
        eps._state.payload_mode = 2

        class _Orbit:
            in_eclipse = True
            solar_beta_deg = 0.0

        orbit = _Orbit()
        params: dict[int, float] = {}

        stages_seen: list[int] = []
        dt = 10.0
        for _ in range(int(24 * 3600 / dt)):
            eps.tick(dt, orbit, params)
            engine._tick_fdir_advanced(dt)
            stage = engine._current_load_shed_stage
            if not stages_seen or stage != stages_seen[-1]:
                stages_seen.append(stage)
            if eps._state.bat_soc_pct <= 1.0:
                break

        assert stages_seen[0] == 0
        assert stages_seen == sorted(stages_seen), f"stages out of order: {stages_seen}"
        assert stages_seen[-1] == 3, f"deepest stage not reached: {stages_seen}"
        assert [1, 2, 3] == [s for s in stages_seen if s > 0]

    def test_soc_read_uses_bat_soc_pct(self, engine):
        """Regression for the exact defect: SoC must be read, not defaulted."""
        eps = engine.subsystems["eps"]
        eps._state.bat_soc_pct = 25.0
        engine._tick_fdir_advanced(1.0)
        assert engine._current_load_shed_stage == 1, (
            "load shedding did not see the battery SoC (dead `soc` attribute?)")

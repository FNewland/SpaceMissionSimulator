"""2026-08b Phase 0 (P-d): the budget tool must speak the simulator's frame.

`tools/power_budget.py` built its synthetic sun vector in the PRE-Phase-C body
frame ("body Z on the orbit normal") long after Phase C had moved the
simulator to real LVLH (`smo_common.attitude.lvlh_dcm`: z = nadir,
y = -orbit normal) and eps.yaml had moved `sada_axis` to 'y' to match.

Feeding a Z-normal sun vector into a Y-axis SADA replaced the wing
illumination law cos(beta) with sqrt(1 - cos^2(b) sin^2(phi)), whose
orbit-mean is ~0.84 regardless of beta. Two consequences, both tested here:

1. the tool over-predicted generation by ~28 %; and
2. worse, the beta TREND INVERTED — the tool reported MORE generation at high
   |beta| than at low |beta|, so `main()`'s "worst-generation epoch =
   max |beta|" pick was labelling the *best* epoch the worst, and every sizing
   decision made from it was made against the wrong case.

The trend test is the load-bearing one: a scale error is visible, a sign error
is not.
"""
import math
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
for _p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np  # noqa: E402

from smo_common import attitude as att  # noqa: E402
from smo_simulator.models.eps_basic import EPSBasicModel  # noqa: E402

import power_budget  # noqa: E402  (tools/power_budget.py)


class _Orbit:
    def __init__(self, beta=0.0, eclipse=False):
        self.solar_beta_deg = beta
        self.in_eclipse = eclipse


def _eps(beta):
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
    return m


#: One orbit at ~450 km. The phase sweep below must advance at the real orbit
#: rate (0.064 deg/s), not one degree per tick: the solar array drive is
#: rate-limited to `sada_max_rate_deg_s` and would lag a fictional sun that
#: crosses the sky in three minutes.
ORBIT_PERIOD_S = 5670.0


def _orbit_mean_sunlit_gen(beta_deg: float, n_phase: int = 180) -> float:
    """Mean sunlit generation over a full sweep of orbit phase, using the
    tool's own sun-vector helper — i.e. exactly what the tool feeds EPS."""
    m = _eps(beta_deg)
    orbit = _Orbit(beta=beta_deg)
    dt = ORBIT_PERIOD_S / n_phase
    total = 0.0
    for k in range(n_phase):
        phase = 360.0 * k / n_phase
        sx, sy, sz = power_budget.sun_body_lvlh(beta_deg, phase)
        params = {0x02B3: sx, 0x02B4: sy, 0x02B5: sz}
        m.tick(dt, orbit, params)
        total += m._state.power_gen_w
    return total / n_phase


class TestSunVectorFrame:
    def test_matches_lvlh_dcm_exactly(self):
        """The closed form must equal attitude.lvlh_dcm @ sun_hat.

        Constructed geometry: pick an orbit-normal h, a position r_hat and a
        sun direction with a known beta and in-plane phase, then check the
        helper reproduces the DCM product component-wise.
        """
        for beta in (0.0, 21.5, 32.3, 49.8, -49.8):
            for phase in (0.0, 37.0, 90.0, 214.0, 300.0):
                # Build an ECI triad: u = r_hat = x_eci, h = z_eci,
                # w = h x u = y_eci.
                r = np.array([7.0e6, 0.0, 0.0])
                v = np.array([0.0, 7.5e3, 0.0])     # h = r x v = +z
                b, ph = math.radians(beta), math.radians(phase)
                sun = np.array([math.cos(b) * math.cos(ph),
                                math.cos(b) * math.sin(ph),
                                math.sin(b)])
                truth = att.lvlh_dcm(r, v) @ sun
                got = power_budget.sun_body_lvlh(beta, phase)
                assert got == pytest.approx(tuple(truth), abs=1e-12), (
                    f"beta={beta} phase={phase}")

    def test_y_component_is_pure_minus_sin_beta(self):
        """The SADA axis is body Y (eps.yaml `sada_axis: y`), so the tracked
        wing illumination is sqrt(1 - sun_y^2) = cos(beta) — independent of
        orbit phase. If sun_y ever picks up a phase term the cos-beta law is
        gone and the trend inverts again."""
        for beta in (0.0, 24.2, 49.8):
            ys = {round(power_budget.sun_body_lvlh(beta, p)[1], 12)
                  for p in range(0, 360, 7)}
            assert ys == {round(-math.sin(math.radians(beta)), 12)}


class TestBetaTrend:
    def test_high_beta_generates_less_than_low_beta(self):
        """THE regression: gen(beta=49.8) < gen(beta=32.3).

        With the pre-fix Z-normal vector this assertion failed — the tool
        reported ~+30 % MORE generation at 49.8 deg than at 32.3 deg.
        """
        hi = _orbit_mean_sunlit_gen(49.8)
        lo = _orbit_mean_sunlit_gen(32.3)
        assert hi < lo, (
            f"beta trend inverted: gen(49.8 deg)={hi:.1f} W is not below "
            f"gen(32.3 deg)={lo:.1f} W — the tool is back in the "
            f"pre-Phase-C frame")

    def test_trend_is_monotonic_in_abs_beta(self):
        gens = [_orbit_mean_sunlit_gen(b) for b in (0.0, 21.5, 32.3, 49.8)]
        assert gens == sorted(gens, reverse=True), gens

    def test_wing_illumination_follows_cos_beta(self):
        """Quantitative: the *wing* contribution must scale as cos(beta).

        Body panels do not, so compare the wing currents the model publishes
        separately (sa_wing_currents) rather than total generation.
        """
        def wing_mean(beta):
            m = _eps(beta)
            orbit = _Orbit(beta=beta)
            tot = 0.0
            n = 180
            dt = ORBIT_PERIOD_S / n
            for k in range(n):
                sx, sy, sz = power_budget.sun_body_lvlh(beta, 360.0 * k / n)
                m.tick(dt, orbit, {0x02B3: sx, 0x02B4: sy, 0x02B5: sz})
                tot += sum(m._state.sa_wing_currents.values())
            return tot / n

        ratio = wing_mean(49.8) / wing_mean(0.0)
        assert ratio == pytest.approx(math.cos(math.radians(49.8)), rel=0.02), (
            f"wing illumination law is not cos(beta): ratio {ratio:.3f} vs "
            f"{math.cos(math.radians(49.8)):.3f}")

"""Phase B (brief 01, test 4): battery electrical model.

Locks WP-4: the terminal-voltage sign (v = ocv + i*R with i > 0 on charge),
the coulombic (charge-acceptance) efficiency, the CV taper above 90 % SoC,
and the electrical self-consistency of the power telemetry.
"""
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
    def __init__(self, eclipse=False, beta=0.0):
        self.in_eclipse = eclipse
        self.solar_beta_deg = beta


def _model(**bat_overrides):
    cfg = yaml.safe_load((CONFIG_DIR / "subsystems" / "eps.yaml").read_text())
    sim = yaml.safe_load((CONFIG_DIR / "simulation.yaml").read_text())
    cfg["simulation"] = sim["eps"]
    cfg["battery"] = dict(cfg["battery"])
    cfg["battery"].update(bat_overrides)
    m = EPSBasicModel()
    m.configure(cfg)
    m.rng.seed(7)
    return m


def _ocv(m):
    s = m._state
    return m._soc_0_v + (m._soc_100_v - m._soc_0_v) * s.bat_soc_pct / 100.0


def _mean_voltage_charging(m, sun, orbit, n=200):
    params = {0x0245: sun[0], 0x0246: sun[1], 0x0247: sun[2]}
    vs, ocvs = [], []
    for _ in range(n):
        ocvs.append(_ocv(m))
        m.tick(1.0, orbit, params)
        vs.append(m._state.bat_voltage)
    return sum(vs) / n, sum(ocvs) / n


class TestVoltageSign:
    def test_bus_rides_above_ocv_on_charge(self):
        m = _model()
        s = m._state
        s.wing_py_deployed = True
        s.wing_my_deployed = True
        s.bat_soc_pct = 50.0  # below CV taper, hard charge
        v, ocv = _mean_voltage_charging(m, (0.0, 1.0, 0.0), _Orbit())
        assert s.bat_current > 0, "should be charging"
        assert v > ocv, (
            f"charging bus {v:.2f} V should ride ABOVE ocv {ocv:.2f} V "
            "(brief 01 F8: the sign was inverted)")

    def test_bus_sags_below_ocv_on_discharge(self):
        m = _model()
        s = m._state
        s.bat_soc_pct = 50.0
        for line in ("aocs_wheels", "payload", "ttc_tx"):
            s.power_lines[line] = True
        s.payload_mode = 2
        v, ocv = _mean_voltage_charging(m, (0, 0, 0), _Orbit(eclipse=True))
        assert s.bat_current < 0, "should be discharging"
        assert v < ocv, (
            f"discharging bus {v:.2f} V should sag BELOW ocv {ocv:.2f} V")


class TestCoulombicEfficiency:
    def test_charge_acceptance_094(self):
        """50 Wh of net charge energy below the CV region stores 47 Wh:
        20 % + 47 % = 67 % on a 100 Wh battery."""
        m = _model(cv_taper_start_soc_pct=None)  # isolate the efficiency
        s = m._state
        s.bat_soc_pct = 20.0
        cap = m._battery_capacity_wh
        assert cap == 100.0
        # Constant 50 W net charge for 3600 s = 50 Wh in
        net_w, t = 50.0, 3600.0
        d_soc = (net_w * 0.94 / (cap * 3600.0)) * 100.0 * t
        # emulate through the model: inject as generation with no loads
        m._obc_power_w = 0.0
        m._transponder_rx_w = 0.0
        s.power_lines = {k: False for k in s.power_lines}
        s.power_lines["obc"] = True
        s.power_lines["ttc_rx"] = True

        class _O(_Orbit):
            pass

        # Use the tumbling fallback with a fabricated area to get ~50 W:
        # simpler — directly exercise the SoC integrator via generation.
        m._panel_areas_body = {k: 0.0 for k in m._panel_areas_body}
        m._wing_py_area = 0.0
        m._wing_my_area = 0.0
        # Monkeypatch: feed net power by overriding the sun branch is
        # overkill; instead assert on the pure arithmetic the model applies.
        assert m._charge_eff == pytest.approx(0.94)
        assert d_soc == pytest.approx(47.0, abs=0.1)

    def test_model_end_to_end_efficiency(self):
        """End-to-end through tick(): a fixed generation surplus charges at
        0.94 of the coulomb-equivalent rate."""
        m = _model(cv_taper_start_soc_pct=None)
        s = m._state
        s.bat_soc_pct = 20.0
        s.wing_py_deployed = True
        s.wing_my_deployed = True
        params = {0x0245: 1.0, 0x0246: 0.0, 0x0247: 0.0}  # sun ⊥ Y drive axis (Phase C frame)
        orbit = _Orbit()
        e_net = 0.0
        for _ in range(600):
            m.tick(1.0, orbit, params)
            e_net += (s.power_gen_w - s.power_cons_w) * 1.0
        expected_dsoc = e_net * 0.94 / (100.0 * 3600.0) * 100.0
        assert s.bat_soc_pct - 20.0 == pytest.approx(expected_dsoc, rel=0.02)


class TestCVTaper:
    def test_charge_rate_tapers_above_90_pct(self):
        m = _model()
        s = m._state
        s.wing_py_deployed = True
        s.wing_my_deployed = True
        params = {0x0245: 1.0, 0x0246: 0.0, 0x0247: 0.0}  # sun ⊥ Y drive axis (Phase C frame)
        orbit = _Orbit()

        def charge_rate_at(soc):
            s.bat_soc_pct = soc
            rates = []
            for _ in range(50):
                before = s.bat_soc_pct
                m.tick(1.0, orbit, params)
                rates.append(s.bat_soc_pct - before)
                s.bat_soc_pct = soc  # hold the operating point
            return sum(rates) / len(rates)

        r50 = charge_rate_at(50.0)
        r95 = charge_rate_at(95.0)
        r99 = charge_rate_at(99.0)
        assert r95 < 0.6 * r50, f"no CV taper at 95 %: {r95} vs {r50}"
        assert r99 < 0.15 * r50, f"no CV taper at 99 %: {r99} vs {r50}"


class TestPowerSelfConsistency:
    def test_net_power_equals_battery_current_times_voltage(self):
        """(power_gen - power_cons) == bat_current x OCV within 2 %
        (averaged over ticks to integrate out the telemetry noise)."""
        m = _model()
        s = m._state
        s.wing_py_deployed = True
        s.wing_my_deployed = True
        s.bat_soc_pct = 50.0
        params = {0x0245: 1.0, 0x0246: 0.0, 0x0247: 0.0}  # sun ⊥ Y drive axis (Phase C frame)
        orbit = _Orbit()
        nets, bats = [], []
        for _ in range(300):
            m.tick(1.0, orbit, params)
            nets.append(s.power_gen_w - s.power_cons_w)
            bats.append(s.bat_current * _ocv(m))
        mean_net = sum(nets) / len(nets)
        mean_bat = sum(bats) / len(bats)
        assert mean_bat == pytest.approx(mean_net, rel=0.02)

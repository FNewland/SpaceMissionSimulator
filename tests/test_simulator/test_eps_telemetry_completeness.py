"""Phase B (brief 01, test 6): EPS telemetry completeness and consistency.

* Every parameter declared with `subsystem: eps` in parameters.yaml is
  written by the models/engine on every tick, or is explicitly marked
  on_demand/stub.
* The 0x0129 / 0x012A / 0x013A identities hold (the collision fix): 0x0129 is
  the engine's spacecraft phase, 0x012A the PDM unswitched status, 0x013A the
  eclipse flag.
* Electrical cross-checks an operator would do actually work:
  Σ(line_current) x V_bus == power_cons and
  Σ(panel_current) x V_bus == power_gen, both within 0.5 W (averaged to
  integrate out the modelled telemetry noise).
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


@pytest.fixture(scope="module")
def engine():
    from smo_simulator.engine import SimulationEngine
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    # Application phase so every subsystem ticks.
    eng._spacecraft_phase = 6
    obdh = eng.subsystems.get("obdh")
    if obdh is not None and hasattr(obdh, "_state"):
        obdh._state.sw_image = 1
    for _ in range(20):
        eng._tick_once(1.0)
    return eng


def _param_defs():
    data = yaml.safe_load((CONFIG_DIR / "telemetry" / "parameters.yaml").read_text())
    return data["parameters"]


def test_every_declared_eps_param_is_written(engine):
    missing = []
    for p in _param_defs():
        if p.get("subsystem") != "eps":
            continue
        if p.get("on_demand") or p.get("stub"):
            continue
        if p["id"] not in engine.params:
            missing.append(f"0x{p['id']:04X} {p['name']}")
    assert not missing, (
        "declared cyclic EPS parameters written by nothing: "
        + ", ".join(missing))


def test_no_duplicate_ids_in_catalogue():
    seen = {}
    for p in _param_defs():
        assert p["id"] not in seen, (
            f"duplicate parameter id 0x{p['id']:04X}: "
            f"{seen[p['id']]} vs {p['name']}")
        seen[p["id"]] = p["name"]


def test_collision_trio_identities(engine):
    """0x0129 = spacecraft phase (engine), 0x012A = pdm_unsw_status,
    0x013A = eclipse flag — names in parameters.yaml match the writers."""
    names = {p["id"]: p["name"] for p in _param_defs()}
    assert names[0x0129] == "eps.spacecraft_phase"
    assert names[0x012A] == "eps.pdm_unsw_status"
    assert names[0x013A] == "eps.eclipse_active"

    eps = engine.subsystems["eps"]
    assert engine.params[0x0129] == engine._spacecraft_phase
    assert engine.params[0x012A] == float(eps._state.pdm_unsw_status)
    assert engine.params[0x013A] == (1.0 if eps._state.in_eclipse else 0.0)


def test_wing_and_load_current_params(engine):
    """New Phase B params exist and are self-consistent."""
    p = engine.params
    eps = engine.subsystems["eps"]
    assert 0x0146 in p and 0x0147 in p and 0x0148 in p and 0x0149 in p
    assert p[0x0148] == pytest.approx(
        sum(eps._state.line_currents.values()), abs=1e-6)


class TestElectricalCrossChecks:
    def _run(self, n=120):
        from smo_simulator.models.eps_basic import EPSBasicModel
        cfg = yaml.safe_load((CONFIG_DIR / "subsystems" / "eps.yaml").read_text())
        sim = yaml.safe_load((CONFIG_DIR / "simulation.yaml").read_text())
        cfg["simulation"] = sim["eps"]
        m = EPSBasicModel()
        m.configure(cfg)
        m.rng.seed(11)
        s = m._state
        s.wing_py_deployed = True
        s.wing_my_deployed = True
        for line in ("aocs_wheels", "payload", "ttc_tx"):
            s.power_lines[line] = True
        s.payload_mode = 1

        class _Orbit:
            in_eclipse = False
            solar_beta_deg = 25.0

        beta = math.radians(25.0)
        params = {0x0245: math.cos(beta), 0x0246: 0.3, 0x0247: math.sin(beta)}
        cons_err = gen_err = 0.0
        for _ in range(n):
            m.tick(1.0, _Orbit(), params)
            bus = max(s.bus_voltage, 20.0)
            cons_err += (sum(s.line_currents.values()) * bus - s.power_cons_w)
            gen_err += (sum(s.sa_panel_currents.values()) * bus - s.power_gen_w)
        return cons_err / n, gen_err / n

    def test_line_currents_times_bus_equal_power_cons(self):
        cons_err, _ = self._run()
        assert abs(cons_err) < 0.5, (
            f"Σ(line_current)·V_bus deviates from power_cons by "
            f"{cons_err:+.2f} W on average")

    def test_panel_currents_times_bus_equal_power_gen(self):
        _, gen_err = self._run()
        assert abs(gen_err) < 0.5, (
            f"Σ(panel_current)·V_bus deviates from power_gen by "
            f"{gen_err:+.2f} W on average")

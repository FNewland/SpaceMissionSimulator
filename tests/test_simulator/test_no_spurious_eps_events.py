"""Phase B (brief 01, test 8 / WP-10): the array-degraded alarm is honest.

The old expected-power reference used the dead legacy `arrays:` key
(0.628 m2 -> 244 W "expected" against a real peak of ~130 W), so event 0x010B
fired on the first sunlit tick of EVERY run and latched — and a genuine array
failure afterwards produced no event.

* A clean multi-orbit nominal run on the real propagator emits ZERO 0x010B
  events.
* Injecting a 50 % loss on the +Y face (which carries a wing) emits EXACTLY
  one 0x010B event (rising edge).
"""
import math
import queue
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.power_budget import load_configs, build_propagator, orbit_period_s  # noqa: E402
from smo_simulator.models.aocs_basic import AOCSBasicModel  # noqa: E402
from smo_simulator.models.eps_basic import EPSBasicModel  # noqa: E402


class _StubEngine:
    """Minimal engine stand-in: captures model-pushed events."""

    def __init__(self):
        self._model_event_queue = queue.Queue()
        self._sim_time = None

    def drain(self):
        out = []
        while not self._model_event_queue.empty():
            out.append(self._model_event_queue.get_nowait())
        return out


def _run(orbits=3, inject_at_orbit=None):
    cfg = load_configs(CONFIG_DIR)
    prop, epoch = build_propagator(cfg["orbit"])
    prop.reset(epoch)
    period = orbit_period_s(cfg["orbit"])

    eps = EPSBasicModel()
    eps_cfg = dict(cfg["eps"])
    eps_cfg["simulation"] = cfg["simulation"].get("eps", {})
    eps.configure(eps_cfg)
    eps.rng.seed(5)
    stub = _StubEngine()
    eps._engine = stub
    s = eps._state
    s.wing_py_deployed = True
    s.wing_my_deployed = True
    for line in ("aocs_wheels", "htr_bat", "htr_obc"):
        s.power_lines[line] = True

    params = {}
    dt = 10.0
    events_0x010b = 0
    injected = False
    for i in range(int(orbits * period / dt)):
        t = i * dt
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
        if (inject_at_orbit is not None and not injected
                and t >= inject_at_orbit * period):
            eps.inject_failure("solar_panel_loss", 0.5, face="py")
            eps.inject_failure("solar_panel_loss", 0.5, face="my")
            injected = True
        eps.tick(dt, st, params)
        for evt in stub.drain():
            evt_id = evt[0] if isinstance(evt, tuple) else evt.get("event_id")
            if evt_id == 0x010B:
                events_0x010b += 1
    return events_0x010b


def test_clean_nominal_run_emits_zero_array_degraded_events():
    assert _run(orbits=3) == 0


def test_injected_panel_loss_emits_exactly_one_event():
    assert _run(orbits=3, inject_at_orbit=1) == 1

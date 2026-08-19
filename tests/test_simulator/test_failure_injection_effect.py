"""Every declared failure injection must actually DO something — and survive.

This is the standing version of the failure-injection audit. For each
(subsystem, failure) in the catalogue it boots two identical engines from
``configs/eosat1``, ticks them in lockstep, injects into one of them, and
requires that they diverge. Because both engines run the same seed and the
same number of ticks, free-running physics cancels out: anything that differs
is the injection's own doing.

The defect class this closes is "the injection is accepted, the failure shows
up in the active-failures list, and the spacecraft behaves exactly as if
nothing had happened":

* ``prop.tank_overpressure`` wrote ``tank_p_bar``, which the blowdown relation
  recomputed from the propellant load on the very next tick;
* ``aocs.st_blind`` set ``st{n}_status = 3``, which ``_tick_star_trackers``
  recomputed from sun geometry on the very next tick.

It also round-trips each injected failure through a breakpoint save/load into
a *fresh* engine, which is the other repeat bug here — persistent failure
state that lives on a model attribute rather than in its dataclass gets
dropped by ``get_state()`` and silently heals on restore (``eps.sada_stuck``,
``eps.overvoltage``, ``obdh.cpu_spike``).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from smo_simulator import failure_catalog as fc
from smo_simulator.breakpoints import BreakpointManager
from smo_simulator.engine import SimulationEngine

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"

ALL_PAIRS = sorted(fc.all_failure_names())
PAIR_IDS = [f"{s}.{f}" for s, f in ALL_PAIRS]

# Fields that free-run and are not deterministic between two engines once
# either one's RNG stream has been touched. Excluded from the comparison.
_NOISE = ("_rng_state", "uptime", "time_in_mode", "obc_time_s", "_ekf",
          "elapsed", "sim_", "att_err", "rw_speed", "total_momentum",
          "gyro_bias_", "wheel_", "_h_", "h_body", "boot_timer", "last_sync",
          "_prev", "prev_", "seu", "scrub", "watchdog_timer")


def _boot(ticks: int = 25) -> SimulationEngine:
    """An engine in application phase with every power line energised."""
    if not CONFIG_DIR.exists():
        pytest.skip(f"EOSAT-1 config dir not present at {CONFIG_DIR}")
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    eng.subsystems["obdh"]._state.sw_image = 1
    for _ in range(3):
        eng._tick_once(1.0)
    eps = eng.subsystems["eps"]
    for line in list(eps._state.power_lines):
        eps._state.power_lines[line] = True
    eps._state.bat_soc_pct = 80.0
    aocs = eng.subsystems["aocs"]
    aocs._state.st1_status = 2
    aocs._state.st2_status = 2
    for _ in range(ticks):
        eng._tick_once(1.0)
    return eng


def _snapshot(eng: SimulationEngine) -> dict:
    out = {"params": {hex(k): v for k, v in eng.params.items()}}
    for name, model in eng.subsystems.items():
        out[name] = json.loads(json.dumps(model.get_state(), default=str))
    return out


def _diff(a, b, prefix="", out=None) -> list[tuple]:
    if out is None:
        out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for key in set(a) | set(b):
            _diff(a.get(key), b.get(key), f"{prefix}.{key}", out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{prefix}[{i}]", out)
    elif isinstance(a, float) and isinstance(b, float):
        if abs(a - b) > 1e-9:
            out.append((prefix, a, b))
    elif a != b:
        out.append((prefix, a, b))
    return out


def _significant(diffs) -> list[tuple]:
    return [d for d in diffs if not any(n in d[0] for n in _NOISE)]


@pytest.mark.slow
@pytest.mark.parametrize("subsystem,failure", ALL_PAIRS, ids=PAIR_IDS)
def test_injection_changes_observable_state(subsystem, failure):
    """Injecting must move the spacecraft away from an un-injected control."""
    spec = fc.get_spec(subsystem, failure)
    control = _boot()
    injected = _boot()
    for _ in range(3):
        control._tick_once(1.0)
        injected._tick_once(1.0)
    assert not _significant(_diff(_snapshot(control), _snapshot(injected))), (
        "the two control engines diverged before any injection — this test's "
        "premise (a deterministic seeded engine) no longer holds")

    injected._failure_manager.inject(
        subsystem, failure, magnitude=spec.default_magnitude(),
        onset="step", **spec.defaults())
    for _ in range(5):
        control._tick_once(1.0)
        injected._tick_once(1.0)

    effect = _significant(_diff(_snapshot(control), _snapshot(injected)))
    assert effect, (
        f"{subsystem}.{failure} injected cleanly but changed NOTHING: the "
        f"injected engine is identical to the un-injected control. Its "
        f"declared effect is {spec.effect!r}. Either the model writes state "
        f"that its own tick overwrites, or the write has no consumer.")


@pytest.mark.slow
@pytest.mark.parametrize("subsystem,failure", ALL_PAIRS, ids=PAIR_IDS)
def test_injected_failure_survives_a_breakpoint_round_trip(subsystem, failure):
    """A saved-and-restored failure must still be applied, not healed."""
    spec = fc.get_spec(subsystem, failure)
    eng = _boot()
    eng._failure_manager.inject(
        subsystem, failure, magnitude=spec.default_magnitude(),
        onset="step", **spec.defaults())
    for _ in range(5):
        eng._tick_once(1.0)
    before = _snapshot(eng)

    # Through JSON, exactly as a breakpoint file goes to disk and back.
    saved = json.loads(json.dumps(BreakpointManager(eng).save("gate"),
                                  default=str))
    restored_engine = _boot()
    assert BreakpointManager(restored_engine).load(saved)
    healed = _significant(_diff(before, _snapshot(restored_engine)))
    assert not healed, (
        f"{subsystem}.{failure} did not survive a breakpoint save/load — "
        f"restore lost {[h[0] for h in healed[:8]]}. Persistent failure state "
        f"must be in the model's get_state()/set_state(), not only on a "
        f"model attribute.")

    # The failure manager itself must still list it after the restore.
    active = {f["failure"] for f in restored_engine._failure_manager.active_failures()}
    assert failure in active, (
        f"{subsystem}.{failure} is missing from active_failures() after a "
        f"breakpoint restore — the instructor console would show a clean "
        f"spacecraft that is in fact still faulted")

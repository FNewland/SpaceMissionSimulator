"""Plan A-2 — seeded, reproducible randomness.

Same seed + same t0_epoch ⇒ two independently constructed engines produce an
identical parameter trace over 500 ticks. A different seed produces a
different trace. RNG state round-trips through a breakpoint save/load.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from smo_simulator.engine import SimulationEngine

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"

TICKS = 500
SNAPSHOT_EVERY = 50


def _make_engine() -> SimulationEngine:
    if not CONFIG_DIR.exists():
        pytest.skip(f"EOSAT-1 config dir not present at {CONFIG_DIR}")
    return SimulationEngine(CONFIG_DIR, speed=1.0)


def _trace(eng: SimulationEngine, ticks: int = TICKS,
           every: int = SNAPSHOT_EVERY) -> list[dict]:
    """Tick `ticks` times, snapshotting engine.params every `every` ticks."""
    snaps = []
    for i in range(1, ticks + 1):
        eng._tick_once(1.0)
        if i % every == 0:
            snaps.append(dict(eng.params))
    return snaps


def test_same_seed_identical_trace():
    a = _make_engine()
    b = _make_engine()
    assert a.seed == b.seed  # both from configs/eosat1/simulation.yaml

    trace_a = _trace(a)
    trace_b = _trace(b)

    assert len(trace_a) == TICKS // SNAPSHOT_EVERY
    for i, (sa, sb) in enumerate(zip(trace_a, trace_b)):
        assert sa == sb, (
            f"Parameter snapshot at tick {(i + 1) * SNAPSHOT_EVERY} differs "
            f"between two engines with the same seed"
        )


def test_different_seed_differs():
    a = _make_engine()
    b = _make_engine()
    # Rebase engine B's RNG streams onto a different seed. reseed() resets
    # every stream to its start, so A must also be rebased (onto its own
    # seed) to keep the comparison symmetric.
    a.reseed(a.seed)
    b.reseed(a.seed + 1)

    trace_a = _trace(a)
    trace_b = _trace(b)

    assert any(sa != sb for sa, sb in zip(trace_a, trace_b)), (
        "Two engines with different seeds produced identical parameter traces"
    )


def test_seed_comes_from_config():
    eng = _make_engine()
    assert eng.seed == 20260731  # configs/eosat1/simulation.yaml `seed:`


def _rng_states(eng: SimulationEngine) -> dict:
    states = {"engine": eng.rng.getstate(),
              "failure_manager": eng._failure_manager.rng.getstate()}
    for name, model in eng.subsystems.items():
        states[f"model:{name}"] = model.rng.getstate()
    return states


def test_rng_state_round_trips_through_breakpoint():
    """Every RNG stream is restored exactly by a breakpoint load.

    (The full parameter-trace deep-compare after a load lives in
    test_breakpoint_round_trip.py — it needs the A-4 complete state
    round-trip, not just the RNG streams.)
    """
    from smo_simulator.breakpoints import BreakpointManager

    eng = _make_engine()
    for _ in range(20):
        eng._tick_once(1.0)

    snap = BreakpointManager(eng).save(name="rng_rt")
    at_save = _rng_states(eng)

    # Perturb the RNG streams thoroughly, then restore the breakpoint.
    for _ in range(37):
        eng._tick_once(1.0)
    assert _rng_states(eng) != at_save  # ticking really did advance them
    assert BreakpointManager(eng).load(state=snap)

    assert _rng_states(eng) == at_save, (
        "RNG streams did not round-trip through the breakpoint")


def test_models_have_engine_seeded_rngs():
    from smo_common.state import derive_rng
    eng = _make_engine()
    for name, model in eng.subsystems.items():
        assert hasattr(model, "rng"), f"model {name} has no rng attribute"
        assert model.rng is not eng.rng, (
            f"model {name} shares the engine RNG object; each component "
            f"must own an independent derived stream"
        )
    assert eng._failure_manager.rng is not eng.rng

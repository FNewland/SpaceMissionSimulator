"""Regression tests for orbit/eclipse state across breakpoint save/load.

Bug under test: when a breakpoint is saved and then loaded, the orbital
position (and the corresponding sunlight/eclipse state) must be restored to
exactly the saved instant. Previously the restored param store said one thing
while the freshly-propagated orbit said another, because the orbit propagator's
clock was not being put back to the saved epoch consistently with the eclipse
telemetry parameter the MCS displays.

The MCS shows eclipse/sunlight via EPS param 0x0108 (eclipse_flag), which the
EPS model writes each tick from ``orbit_state.in_eclipse``. So after a load,
the restored param 0x0108 and the next freshly-propagated orbit's in_eclipse
MUST agree, and the orbit position must match the saved snapshot.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from smo_simulator.engine import SimulationEngine
from smo_simulator.breakpoints import BreakpointManager

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "eosat1"

ECLIPSE_PARAM = 0x0108  # EPS eclipse_flag


def _make_engine() -> SimulationEngine:
    if not CONFIG_DIR.exists():
        pytest.skip(f"EOSAT-1 config dir not present at {CONFIG_DIR}")
    return SimulationEngine(CONFIG_DIR, speed=1.0)


def _tick(eng: SimulationEngine, n: int, dt: float = 60.0) -> None:
    """Advance the engine's orbit + EPS subsystem n times by dt seconds each.

    Mirrors the real run loop closely enough to move the orbit appreciably and
    keep the eclipse_flag param in lock-step with orbit_state.in_eclipse.
    """
    eps = eng.subsystems.get("eps")
    for _ in range(n):
        orbit_state = eng.orbit.advance(dt)
        if eps is not None:
            eps.tick(dt, orbit_state, eng.params)
        eng._sim_time = eng._sim_time + _td(dt)
        eng._tick_count += 1


def _td(seconds: float):
    from datetime import timedelta
    return timedelta(seconds=seconds)


def _find_eclipse_crossing(eng: SimulationEngine, dt: float = 60.0, max_ticks: int = 200) -> int:
    """Tick until the eclipse state flips at least once; return ticks taken."""
    start = eng.orbit.state.in_eclipse
    for i in range(1, max_ticks + 1):
        _tick(eng, 1, dt)
        if eng.orbit.state.in_eclipse != start:
            return i
    return max_ticks


def test_breakpoint_restores_sim_time_in_api_state():
    """A loaded breakpoint must put the engine's sim epoch back so the
    simulator's /api/state (get_state_summary) reports the saved sim_time.

    This is the value the MCS (_sim_state_poll_loop) and Planner
    (_refresh_sim_anchor) poll and re-anchor their ground clocks to when
    time_source=sim, so a breakpoint load is picked up by both within one poll.
    The orbit epoch is restored alongside it (already covered above).
    """
    eng = _make_engine()
    _tick(eng, 5)

    sim_time_saved = eng._sim_time
    orbit_saved = eng.orbit.utc
    state = BreakpointManager(eng).save(name="time_chk")
    assert eng.get_state_summary()["sim_time"] == sim_time_saved.isoformat()

    # Advance well past the saved instant so a failure to restore is obvious.
    _tick(eng, 120)
    assert eng.get_state_summary()["sim_time"] != sim_time_saved.isoformat()

    assert BreakpointManager(eng).load(state=state) is True

    # The engine clock and the served /api/state sim_time are both back to the
    # saved epoch — i.e. the MCS/Planner will re-anchor to it.
    assert eng._sim_time == sim_time_saved
    assert eng.get_state_summary()["sim_time"] == sim_time_saved.isoformat()
    assert abs((eng.orbit.utc - orbit_saved).total_seconds()) < 1.0


def test_orbit_and_eclipse_restored_by_breakpoint():
    eng = _make_engine()

    # Run enough ticks that the orbit moves appreciably and crosses an eclipse
    # boundary, so the saved snapshot captures a meaningful position/eclipse.
    _find_eclipse_crossing(eng)
    _tick(eng, 30)  # move a bit further past the boundary

    saved_pos = np.array(eng.orbit.state.pos_eci, dtype=float)
    saved_lat = eng.orbit.state.lat_deg
    saved_lon = eng.orbit.state.lon_deg
    saved_alt = eng.orbit.state.alt_km
    saved_eclipse = bool(eng.orbit.state.in_eclipse)
    saved_utc = eng.orbit.utc

    snapshot = BreakpointManager(eng).save(name="orbit-eclipse")

    # The eclipse param the MCS displays must be in the snapshot and consistent
    # with the saved orbit state at save time.
    assert int(snapshot["params"][str(ECLIPSE_PARAM)]) == (1 if saved_eclipse else 0)

    # Now run far ahead so the orbit moves a long way and eclipse flips again.
    for _ in range(10):
        if _find_eclipse_crossing(eng) < 200:
            break
    _tick(eng, 50)

    moved_pos = np.array(eng.orbit.state.pos_eci, dtype=float)
    assert np.linalg.norm(moved_pos - saved_pos) > 100.0, (
        "Orbit did not move appreciably between save and load; test is not exercising the bug"
    )

    # Restore the breakpoint.
    ok = BreakpointManager(eng).load(state=snapshot)
    assert ok is True

    # 1) The orbit clock must be back at the saved instant.
    assert abs((eng.orbit.utc - saved_utc).total_seconds()) < 1.0, (
        f"Orbit clock not restored: got {eng.orbit.utc}, expected {saved_utc}"
    )

    # 2) The restored orbit position must match the saved snapshot tightly.
    restored_pos = np.array(eng.orbit.state.pos_eci, dtype=float)
    assert np.linalg.norm(restored_pos - saved_pos) < 1.0, (
        f"pos_eci not restored: |delta|={np.linalg.norm(restored_pos - saved_pos):.3f} km"
    )
    assert abs(eng.orbit.state.lat_deg - saved_lat) < 1e-3
    assert abs(eng.orbit.state.lon_deg - saved_lon) < 1e-3
    assert abs(eng.orbit.state.alt_km - saved_alt) < 1e-3

    # 3) The freshly-propagated eclipse state must match what was saved.
    assert bool(eng.orbit.state.in_eclipse) == saved_eclipse

    # 4) Crucial consistency: the restored eclipse PARAM (what the MCS shows)
    #    must agree with the freshly-propagated orbit after one more tick.
    eps = eng.subsystems.get("eps")
    assert eps is not None
    orbit_state = eng.orbit.advance(0.0)  # re-propagate at the restored epoch
    eps.tick(0.0, orbit_state, eng.params)
    assert int(eng.params[ECLIPSE_PARAM]) == (1 if saved_eclipse else 0), (
        "Restored eclipse param disagrees with restored orbit position"
    )


def test_orbit_and_eclipse_restored_via_disk_json(tmp_path: Path):
    """Same as above but through a real JSON file round-trip (production path).

    In production a breakpoint is written to workspace/breakpoints/*.json and
    re-read with json.load, so the snapshot dict that reaches load() has had its
    param keys stringified and its orbit_utc passed through json. This is the
    path the operator actually exercises, and where the orbit/eclipse drift was
    reported.
    """
    eng = _make_engine()

    _find_eclipse_crossing(eng)
    _tick(eng, 30)

    saved_pos = np.array(eng.orbit.state.pos_eci, dtype=float)
    saved_eclipse = bool(eng.orbit.state.in_eclipse)
    saved_utc = eng.orbit.utc

    bp_file = tmp_path / "orbit_bp.json"
    BreakpointManager(eng).save(name="orbit-disk", path=bp_file)
    assert bp_file.exists()

    raw = json.loads(bp_file.read_text())
    assert "orbit_utc" in raw, "orbit_utc missing from persisted snapshot"
    assert int(raw["params"][str(ECLIPSE_PARAM)]) == (1 if saved_eclipse else 0)

    # Move far ahead.
    for _ in range(10):
        if _find_eclipse_crossing(eng) < 200:
            break
    _tick(eng, 50)
    assert np.linalg.norm(np.array(eng.orbit.state.pos_eci, dtype=float) - saved_pos) > 100.0

    # Load from the file exactly like the engine does.
    ok = BreakpointManager(eng).load(path=bp_file)
    assert ok is True

    assert abs((eng.orbit.utc - saved_utc).total_seconds()) < 1.0
    restored_pos = np.array(eng.orbit.state.pos_eci, dtype=float)
    assert np.linalg.norm(restored_pos - saved_pos) < 1.0
    assert bool(eng.orbit.state.in_eclipse) == saved_eclipse

    eps = eng.subsystems.get("eps")
    orbit_state = eng.orbit.advance(0.0)
    eps.tick(0.0, orbit_state, eng.params)
    assert int(eng.params[ECLIPSE_PARAM]) == (1 if saved_eclipse else 0)

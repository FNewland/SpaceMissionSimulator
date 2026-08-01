"""Plan A-3 — sub-stepped tick: time acceleration must not change the physics.

The engine splits any tick with dt_sim > tick.dt_max_s (1 s for eosat1) into
equal sub-steps, so the same 3600 s of sim time integrates the same equations
with the same step size whether it is run as 3600 x 1 s ticks or 60 x 60 s
ticks. End-state agreement is asserted on battery SoC, a thermal node and the
eclipse-entry time; a dt_sim = 600 run must stay finite and bounded (the raw
detumble/OU models diverge for large single Euler steps — sub-stepping is
what keeps them in their stable region).
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from smo_simulator.engine import SimulationEngine

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"

SIM_SECONDS = 3600


def _make_engine() -> SimulationEngine:
    if not CONFIG_DIR.exists():
        pytest.skip(f"EOSAT-1 config dir not present at {CONFIG_DIR}")
    return SimulationEngine(CONFIG_DIR, speed=1.0)


def _run(dt_sim: float, total_s: int = SIM_SECONDS) -> dict:
    eng = _make_engine()
    n_ticks = int(round(total_s / dt_sim))
    eclipse_entry_s = None
    elapsed = 0.0
    for _ in range(n_ticks):
        eng._tick_once(dt_sim)
        elapsed += dt_sim
        if eclipse_entry_s is None and eng.orbit.state.in_eclipse:
            eclipse_entry_s = elapsed
    return {
        "soc": eng.subsystems["eps"]._state.bat_soc_pct,
        "bat_temp": eng.subsystems["tcs"]._state.temp_battery,
        "eclipse_entry_s": eclipse_entry_s,
        "engine": eng,
    }


def test_speed_60_matches_realtime_end_state():
    real = _run(dt_sim=1.0)
    fast = _run(dt_sim=60.0)

    # Battery SoC within half a point
    assert real["soc"] == pytest.approx(fast["soc"], abs=0.5), (
        f"SoC diverged under x60: {real['soc']:.3f} vs {fast['soc']:.3f}")

    # Battery thermal node within half a degree
    assert real["bat_temp"] == pytest.approx(fast["bat_temp"], abs=0.5), (
        f"Battery temp diverged under x60: "
        f"{real['bat_temp']:.3f} vs {fast['bat_temp']:.3f}")

    # Eclipse entry seen within one accelerated tick (60 s) of the real one
    assert real["eclipse_entry_s"] is not None, (
        "expected an eclipse inside the first 3600 s of the eosat1 orbit")
    assert fast["eclipse_entry_s"] is not None
    assert abs(real["eclipse_entry_s"] - fast["eclipse_entry_s"]) <= 60.0


def test_dt_600_remains_finite_and_bounded():
    """Sub-stepping keeps first-order model states in their stable region.

    Without it, a single 600 s Euler step makes the detumble damping factor
    negative (divergence to ~1e39 deg/s, brief 02 §3) and overshoots every
    thermal/OU relaxation.
    """
    eng = _make_engine()
    # Put AOCS into DETUMBLE with a genuine tumble so the damping loop runs.
    aocs = eng.subsystems["aocs"]
    aocs._state.mode = 1  # MODE_DETUMBLE
    aocs._state.rate_roll = 0.3
    aocs._state.rate_pitch = -0.25
    aocs._state.rate_yaw = 0.2

    for _ in range(6):  # 6 x 600 s = 3600 s of sim time
        eng._tick_once(600.0)

    s = aocs._state
    for name in ("rate_roll", "rate_pitch", "rate_yaw"):
        v = getattr(s, name)
        assert math.isfinite(v), f"{name} is not finite after dt=600 ticks"
        assert abs(v) <= 10.0, f"{name} diverged under dt=600: {v}"

    tcs = eng.subsystems["tcs"]._state
    for name in ("temp_battery", "temp_obc", "temp_fpa"):
        v = getattr(tcs, name)
        assert math.isfinite(v), f"tcs.{name} is not finite"
        assert -150.0 <= v <= 150.0, f"tcs.{name} out of physical range: {v}"

    soc = eng.subsystems["eps"]._state.bat_soc_pct
    assert math.isfinite(soc) and 0.0 <= soc <= 100.0


def test_substep_count_and_cap():
    """N = ceil(dt/dt_max) capped at max_substeps; sim time never truncated."""
    eng = _make_engine()
    assert eng._dt_max_s == 1.0 and eng._max_substeps == 600

    t0 = eng._sim_time
    eng._tick_once(0.5)     # below dt_max: single pass
    eng._tick_once(60.0)    # 60 sub-steps
    eng._tick_once(1200.0)  # cap binds: 600 sub-steps of 2 s (documented)
    elapsed = (eng._sim_time - t0).total_seconds()
    assert elapsed == pytest.approx(0.5 + 60.0 + 1200.0)

    # The tick counter is an OUTER-tick counter.
    assert eng._tick_count == 3

"""Performance benchmarks for the EOSAT-1 simulator.

These are *real* benchmarks executed inside the test suite — not micro
fixtures. They verify that with all the recently-added subsystem realism,
failure modes, breakpoint serialisation, MCS HK ingest path, instructor
snapshot tabbing, and so on, the simulator can still meet its published
performance targets:

  Engine target  : 1 Hz tick rate (architecture.md), real-time or faster
                   on standard hardware
  Concurrency    : 1 instructor + up to 30 MCS TM viewers + 15 commanders
                   + 10 planners (design_document.html §12.3) ⇒ ~56 clients

The thresholds chosen here are conservative (≈10× margin under target) so
that CI on a slow runner still passes, but a real regression would still be
caught with high signal.

The benchmark exercises:
  1. **Engine inner-tick wall time** — full physics tick with all 6
     subsystems active and 6 simultaneous failures injected.
  2. **Instructor snapshot serialisation** — the JSON the instructor UI
     polls every 2 seconds and that the bench-fired worst case is one HTTP
     request per second per connected client.
  3. **State summary serialisation** — the per-second poll the MCS UI hits.
  4. **Sustained load projection** — given 1 + 2 + 3 measured per-call costs,
     project the wall-time spent per simulated second under the design
     concurrency target and assert it stays well under 1.0 s.

All tests skip cleanly if the EOSAT-1 config tree is missing.
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import pytest

from smo_simulator.engine import SimulationEngine

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "eosat1"

# Number of times to repeat each timed call.
TICK_ITERATIONS = 100
SNAPSHOT_ITERATIONS = 50
SUMMARY_ITERATIONS = 100

# Performance budgets — well under design targets so CI on slow machines
# still passes, but a real regression would blow these.
TICK_P95_BUDGET_S = 0.20      # 1 Hz tick budget is 1.0 s; require ≤200 ms
SNAPSHOT_P95_BUDGET_S = 0.10  # 100 ms per snapshot serialisation
SUMMARY_P95_BUDGET_S = 0.05   # 50 ms per state summary serialisation
SUSTAINED_BUDGET_S = 0.50     # ≤500 ms per simulated second under full load


def _make_engine() -> SimulationEngine:
    if not CONFIG_DIR.exists():
        pytest.skip(f"EOSAT-1 config dir not present at {CONFIG_DIR}")
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    # Force the spacecraft fully out of bootloader / into nominal phase 6 so
    # ALL subsystems tick (the bench measures the worst case, not the early
    # commissioning case where AOCS/PL/TCS are gated off).
    eng._spacecraft_phase = 6
    obdh = eng.subsystems.get("obdh")
    if obdh is not None and hasattr(obdh, "_state"):
        obdh._state.sw_image = 1
        if hasattr(obdh._state, "boot_app_pending"):
            obdh._state.boot_app_pending = False
    return eng


def _inject_realistic_failure_load(eng: SimulationEngine) -> None:
    """Inject one failure on every subsystem so the per-tick code paths that
    only execute under fault conditions are also measured."""
    eng._failure_manager.inject(
        "eps", "solar_array_partial", magnitude=0.6, onset="step", array="A")
    eng._failure_manager.inject(
        "aocs", "rw_bearing", magnitude=0.3, onset="step", wheel=0)
    eng._failure_manager.inject(
        "tcs", "sensor_drift", magnitude=1.0, onset="step", zone="battery")
    eng._failure_manager.inject(
        "obdh", "memory_segment_fail", magnitude=1.0, onset="step", segment=2)
    eng._failure_manager.inject(
        "ttc", "high_ber", magnitude=1.0, onset="step", offset=5.0)
    eng._failure_manager.inject(
        "payload", "image_corrupt", magnitude=1.0, onset="step", count=3)


def _percentile(samples, pct):
    if not samples:
        return float("nan")
    s = sorted(samples)
    k = max(0, min(len(s) - 1, int(round(pct / 100.0 * (len(s) - 1)))))
    return s[k]


def _stats(samples):
    return {
        "n": len(samples),
        "mean_ms": 1000 * statistics.fmean(samples),
        "median_ms": 1000 * statistics.median(samples),
        "p95_ms": 1000 * _percentile(samples, 95),
        "max_ms": 1000 * max(samples),
    }


def _run_one_engine_tick(eng: SimulationEngine, dt_sim: float = 1.0) -> None:
    """Run the work that happens inside a single engine tick body — excluding
    the rate-limit sleep — so we can measure the CPU cost of one second of
    simulated time."""
    # Mirrors the body of SimulationEngine._run_loop().
    eng._drain_instr_queue()
    eng._drain_tc_queue()

    current_cuc = eng._get_cuc_time()
    due_tcs = eng._tc_scheduler.tick(current_cuc)
    for tc_pkt in due_tcs:
        eng._dispatch_tc(tc_pkt)

    orbit_state = eng.orbit.advance(dt_sim)
    eng._in_contact = orbit_state.in_contact
    eng.params[0x05FF] = 1 if eng._override_passes else 0

    eng._tick_spacecraft_phase(dt_sim)

    for name, model in eng.subsystems.items():
        try:
            model.tick(dt_sim, orbit_state, eng.params)
        except Exception:
            pass

    eps = eng.subsystems.get("eps")
    tcs = eng.subsystems.get("tcs")
    if eps and tcs and hasattr(eps, "set_bat_ambient_temp") and hasattr(tcs, "get_battery_temp"):
        eps.set_bat_ambient_temp(tcs.get_battery_temp())

    eng._tick_s12_monitoring()
    if eng._fdir_enabled:
        eng._tick_fdir()
        eng._tick_fdir_advanced(dt_sim)
    eng._check_subsystem_events()
    eng._check_transitions(orbit_state)
    eng._emit_hk_packets(dt_sim)
    eng._tick_dump_emission(dt_sim)
    eng._failure_manager.tick(dt_sim)

    eng._tick_count += 1


# ─────────────────────────────────────────────────────────────────────────────
# 1. Engine inner-tick wall time
# ─────────────────────────────────────────────────────────────────────────────

def test_engine_tick_meets_realtime_budget_under_failure_load():
    eng = _make_engine()
    _inject_realistic_failure_load(eng)

    # Warm-up — first tick allocates caches and JIT-loads modules.
    for _ in range(5):
        _run_one_engine_tick(eng)

    samples = []
    for _ in range(TICK_ITERATIONS):
        t0 = time.perf_counter()
        _run_one_engine_tick(eng)
        samples.append(time.perf_counter() - t0)

    s = _stats(samples)
    print(f"\n[perf] engine inner-tick: {s}")
    assert s["p95_ms"] / 1000.0 < TICK_P95_BUDGET_S, (
        f"Engine inner tick p95={s['p95_ms']:.1f} ms exceeds budget "
        f"{TICK_P95_BUDGET_S*1000:.0f} ms (1 Hz wall budget is 1000 ms)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Instructor snapshot serialisation
# ─────────────────────────────────────────────────────────────────────────────

def test_instructor_snapshot_serialisation_is_cheap():
    eng = _make_engine()
    _inject_realistic_failure_load(eng)
    # Tick a few times so subsystems publish full state.
    for _ in range(3):
        _run_one_engine_tick(eng)

    samples = []
    for _ in range(SNAPSHOT_ITERATIONS):
        t0 = time.perf_counter()
        snap = eng.get_instructor_snapshot()
        # Snapshot must be JSON-serialisable — that's what the HTTP layer does.
        json.dumps(snap, default=str)
        samples.append(time.perf_counter() - t0)

    s = _stats(samples)
    print(f"\n[perf] instructor snapshot: {s}")
    assert s["p95_ms"] / 1000.0 < SNAPSHOT_P95_BUDGET_S, (
        f"Instructor snapshot p95={s['p95_ms']:.1f} ms exceeds budget "
        f"{SNAPSHOT_P95_BUDGET_S*1000:.0f} ms"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. State summary serialisation (MCS poll)
# ─────────────────────────────────────────────────────────────────────────────

def test_state_summary_serialisation_is_cheap():
    eng = _make_engine()
    _inject_realistic_failure_load(eng)
    for _ in range(3):
        _run_one_engine_tick(eng)

    samples = []
    for _ in range(SUMMARY_ITERATIONS):
        t0 = time.perf_counter()
        summary = eng.get_state_summary()
        json.dumps(summary, default=str)
        samples.append(time.perf_counter() - t0)

    s = _stats(samples)
    print(f"\n[perf] state summary: {s}")
    assert s["p95_ms"] / 1000.0 < SUMMARY_P95_BUDGET_S, (
        f"State summary p95={s['p95_ms']:.1f} ms exceeds budget "
        f"{SUMMARY_P95_BUDGET_S*1000:.0f} ms"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Sustained-load projection — does the design concurrency budget fit?
# ─────────────────────────────────────────────────────────────────────────────
# Design concurrency target (design_document.html §12.3):
#   * 1 instructor polling /api/instructor/snapshot every 2 s
#   * 30 MCS TM viewers polling /api/state at 1 Hz
#   * 15 MCS commanders polling /api/state at 1 Hz
#   * 10 planners polling /api/spacecraft-state at 1 Hz (cheap)
# Per simulated second of wall clock we therefore pay:
#   tick_cost + 0.5 * snapshot_cost + 45 * summary_cost + 10 * cheap_cost
# Cheap path is dwarfed by the others; we approximate it with summary_cost / 4.

INSTRUCTOR_POLL_RATE_HZ = 0.5    # one snapshot every 2 s
N_MCS_TM_VIEWERS = 30
N_MCS_COMMANDERS = 15
N_PLANNERS = 10


def test_sustained_load_at_design_concurrency_fits_realtime():
    eng = _make_engine()
    _inject_realistic_failure_load(eng)

    # Warm-up.
    for _ in range(5):
        _run_one_engine_tick(eng)

    # Per-call cost samples.
    tick_samples, snap_samples, sum_samples = [], [], []
    for _ in range(30):
        t0 = time.perf_counter()
        _run_one_engine_tick(eng)
        tick_samples.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        snap = eng.get_instructor_snapshot()
        json.dumps(snap, default=str)
        snap_samples.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        summary = eng.get_state_summary()
        json.dumps(summary, default=str)
        sum_samples.append(time.perf_counter() - t0)

    tick = statistics.fmean(tick_samples)
    snap = statistics.fmean(snap_samples)
    summ = statistics.fmean(sum_samples)
    cheap = summ * 0.25  # spacecraft-state is a small slice of summary

    # Cost per simulated second of wall time at full concurrency:
    cost_per_sim_second = (
        tick
        + INSTRUCTOR_POLL_RATE_HZ * snap
        + (N_MCS_TM_VIEWERS + N_MCS_COMMANDERS) * summ
        + N_PLANNERS * cheap
    )

    print(
        f"\n[perf] sustained-load projection at design concurrency:"
        f"\n  tick                    : {tick*1000:7.2f} ms"
        f"\n  snapshot (×0.5/s)       : {snap*1000:7.2f} ms each"
        f"\n  state summary (×45/s)   : {summ*1000:7.2f} ms each"
        f"\n  spacecraft-state (×10/s): {cheap*1000:7.2f} ms each"
        f"\n  --------------------------------"
        f"\n  total per sim-second    : {cost_per_sim_second*1000:7.2f} ms"
        f"\n  realtime budget (1 Hz)  : 1000.00 ms"
        f"\n  headroom factor         : {1.0 / cost_per_sim_second:6.1f}x realtime"
    )

    assert cost_per_sim_second < SUSTAINED_BUDGET_S, (
        f"Projected sustained load {cost_per_sim_second*1000:.0f} ms/sim-s "
        f"exceeds {SUSTAINED_BUDGET_S*1000:.0f} ms budget at "
        f"({N_MCS_TM_VIEWERS + N_MCS_COMMANDERS} commanders/viewers + "
        f"{N_PLANNERS} planners + 1 instructor)"
    )

"""End-to-end tests for BreakpointManager save/restore.

These verify that a complete simulator state — including engine bookkeeping,
shared params, FDIR state, every subsystem's internal state, and (critically)
all the new failure-mode fields added in the recent changes — survives a
JSON round-trip both in-memory and via a file on disk.

The previous test suite only contained `test_eps_breakpoint_roundtrip`, which
exercised one EPS field through the model's get_state/set_state directly and
never touched BreakpointManager. This file fills that gap.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from smo_simulator.engine import SimulationEngine
from smo_simulator.breakpoints import BreakpointManager


CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "eosat1"


def _make_engine() -> SimulationEngine:
    if not CONFIG_DIR.exists():
        pytest.skip(f"EOSAT-1 config dir not present at {CONFIG_DIR}")
    return SimulationEngine(CONFIG_DIR, speed=1.0)


def _dirty_engine(eng: SimulationEngine) -> None:
    """Mutate every part of engine state we care about so an identity restore
    is meaningful (and a missing field shows up as a diff)."""
    eng._tick_count = 12345
    eng.speed = 4.0
    eng.sc_mode = 1
    eng.params[0x0FFE] = 999.5
    eng.params[0x0FFF] = -42.0
    eng._fdir_triggered["unit_test_action"] = True
    if eng._hk_timers:
        first = next(iter(eng._hk_timers))
        eng._hk_timers[first] = 7.25

    # Touch each subsystem so the model state diverges from defaults.
    if "eps" in eng.subsystems:
        eng.subsystems["eps"]._state.bat_soc_pct = 42.0
    if "obdh" in eng.subsystems:
        # Hits the new memory_segments + boot_inhibit fields.
        eng.subsystems["obdh"].inject_failure("memory_segment_fail", segment=1)
        eng.subsystems["obdh"].inject_failure("stuck_in_bootloader")
    if "ttc" in eng.subsystems:
        eng.subsystems["ttc"].inject_failure("antenna_deploy_failed")
    if "tcs" in eng.subsystems:
        eng.subsystems["tcs"].inject_failure(
            "temp_anomaly", zone="battery", offset_c=25.0
        )


def _state_fingerprint(eng: SimulationEngine) -> dict:
    return {
        "tick_count": eng._tick_count,
        "speed": eng.speed,
        "sc_mode": eng.sc_mode,
        "params": {int(k): v for k, v in eng.params.items()},
        "fdir": dict(eng._fdir_triggered),
        "hk_timers": {int(k): v for k, v in eng._hk_timers.items()},
        "obdh_segments": list(
            eng.subsystems["obdh"]._state.memory_segments
        ) if "obdh" in eng.subsystems else None,
        "obdh_boot_inhibit": (
            eng.subsystems["obdh"]._state.boot_inhibit
            if "obdh" in eng.subsystems else None
        ),
        "obdh_sw_image": (
            eng.subsystems["obdh"]._state.sw_image
            if "obdh" in eng.subsystems else None
        ),
        "ttc_antenna_sensor": (
            eng.subsystems["ttc"]._state.antenna_deployment_sensor
            if "ttc" in eng.subsystems else None
        ),
        "tcs_sensor_drift": (
            dict(eng.subsystems["tcs"]._state.sensor_drift)
            if "tcs" in eng.subsystems else None
        ),
        "eps_soc": (
            eng.subsystems["eps"]._state.bat_soc_pct
            if "eps" in eng.subsystems else None
        ),
    }


def test_breakpoint_save_load_in_memory_round_trip():
    eng = _make_engine()
    _dirty_engine(eng)
    before = _state_fingerprint(eng)

    bp = BreakpointManager(eng)
    snapshot = bp.save(name="ut-roundtrip")
    assert snapshot["name"] == "ut-roundtrip"
    assert snapshot["tick_count"] == 12345

    # Build a fresh engine and restore into it.
    fresh = _make_engine()
    BreakpointManager(fresh).load(state=snapshot)
    after = _state_fingerprint(fresh)

    assert after == before, (
        "Breakpoint round-trip lost state. Diff keys: "
        f"{[k for k in before if before[k] != after.get(k)]}"
    )


def test_breakpoint_save_load_via_disk_round_trip(tmp_path: Path):
    eng = _make_engine()
    _dirty_engine(eng)
    before = _state_fingerprint(eng)

    bp_file = tmp_path / "bp.json"
    BreakpointManager(eng).save(name="ut-disk", path=bp_file)
    assert bp_file.exists()

    # File must be valid JSON and round-trippable through json.load.
    raw = json.loads(bp_file.read_text())
    assert raw["name"] == "ut-disk"

    fresh = _make_engine()
    ok = BreakpointManager(fresh).load(path=bp_file)
    assert ok is True
    after = _state_fingerprint(fresh)
    assert after == before


def test_breakpoint_load_handles_missing_file_gracefully(tmp_path: Path):
    eng = _make_engine()
    bp = BreakpointManager(eng)
    # Loading from a path that doesn't exist returns False rather than crashing.
    assert bp.load(path=tmp_path / "does_not_exist.json") is False
    # Loading with no state and no path returns False.
    assert bp.load() is False


def test_breakpoint_round_trip_preserves_new_failure_mode_fields():
    """Pinpoint test: every field added by the recent failure-mode work must
    survive serialise → JSON → deserialise."""
    eng = _make_engine()
    if "obdh" not in eng.subsystems or "ttc" not in eng.subsystems or "tcs" not in eng.subsystems:
        pytest.skip("required subsystems not loaded")

    eng.subsystems["obdh"].inject_failure("memory_segment_fail", segment=3)
    eng.subsystems["obdh"].inject_failure("stuck_in_bootloader")
    eng.subsystems["ttc"].inject_failure("antenna_deploy_failed")
    eng.subsystems["tcs"].inject_failure("temp_anomaly", zone="obc", offset_c=15.0)

    snap = BreakpointManager(eng).save()
    json_blob = json.dumps(snap, default=str)  # must be JSON-safe
    reloaded = json.loads(json_blob)

    fresh = _make_engine()
    BreakpointManager(fresh).load(state=reloaded)

    obdh_after = fresh.subsystems["obdh"]._state
    ttc_after = fresh.subsystems["ttc"]._state
    tcs_after = fresh.subsystems["tcs"]._state

    assert obdh_after.memory_segments[3] is False
    assert obdh_after.boot_inhibit is True
    assert obdh_after.boot_image_corrupt is True
    assert ttc_after.antenna_deployed is False
    assert ttc_after.antenna_deployment_sensor == 3
    assert tcs_after.sensor_drift.get("obc", 0.0) >= 14.9

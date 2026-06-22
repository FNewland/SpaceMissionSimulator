"""Tests for the authoritative spacecraft-mode (sc_mode) convention.

Convention (must match the MCS and the instructor UI):
    SAFE=0, NOMINAL=1, SCIENCE=2, EMERGENCY=3.

sc_mode is DERIVED state recomputed each tick by Engine._update_sc_mode(),
driven by the latched FDIR flags (_fdir_safe_active / _fdir_emergency_active),
the spacecraft phase, and the payload mode (param 0x0600). These tests drive
the inputs directly and call _update_sc_mode() to assert the derivation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from smo_simulator.engine import (
    SimulationEngine,
    SC_SAFE,
    SC_NOMINAL,
    SC_SCIENCE,
    SC_EMERGENCY,
    SC_PHASE_NOMINAL,
    PAYLOAD_MODE_IMAGING,
)


CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


def _make_engine() -> SimulationEngine:
    if not CONFIG_DIR.exists():
        pytest.skip(f"EOSAT-1 config dir not present at {CONFIG_DIR}")
    return SimulationEngine(CONFIG_DIR, speed=1.0)


def _clear(eng: SimulationEngine) -> None:
    """Put the engine in a clean, contingency-free, commissioned baseline."""
    eng._fdir_safe_active = False
    eng._fdir_emergency_active = False
    eng._spacecraft_phase = SC_PHASE_NOMINAL
    eng.params[0x0600] = 0  # payload OFF


def test_constants_match_convention():
    assert (SC_SAFE, SC_NOMINAL, SC_SCIENCE, SC_EMERGENCY) == (0, 1, 2, 3)


def test_fresh_engine_constructs_in_safe():
    eng = _make_engine()
    # The spacecraft boots in SAFE before any tick has derived a mode.
    assert eng.sc_mode == SC_SAFE


def test_bootloader_leop_phases_derive_safe():
    eng = _make_engine()
    _clear(eng)
    for phase in range(0, SC_PHASE_NOMINAL):  # 0..5 -> below NOMINAL
        eng._spacecraft_phase = phase
        eng._update_sc_mode()
        assert eng.sc_mode == SC_SAFE, f"phase {phase} should derive SAFE"


def test_commissioned_no_contingency_derives_nominal():
    eng = _make_engine()
    _clear(eng)
    eng._update_sc_mode()
    assert eng.sc_mode == SC_NOMINAL


def test_safe_contingency_forces_safe():
    eng = _make_engine()
    _clear(eng)
    eng._fdir_safe_active = True
    eng._update_sc_mode()
    assert eng.sc_mode == SC_SAFE


def test_emergency_contingency_forces_emergency():
    eng = _make_engine()
    _clear(eng)
    eng._fdir_emergency_active = True
    eng._update_sc_mode()
    assert eng.sc_mode == SC_EMERGENCY


def test_emergency_takes_precedence_over_safe():
    eng = _make_engine()
    _clear(eng)
    eng._fdir_safe_active = True
    eng._fdir_emergency_active = True
    eng._update_sc_mode()
    assert eng.sc_mode == SC_EMERGENCY


def test_emergency_recovers_when_cleared():
    eng = _make_engine()
    _clear(eng)
    eng._fdir_emergency_active = True
    eng._update_sc_mode()
    assert eng.sc_mode == SC_EMERGENCY

    # Clear the emergency but keep a safe-level contingency latched -> SAFE.
    eng._fdir_emergency_active = False
    eng._fdir_safe_active = True
    eng._update_sc_mode()
    assert eng.sc_mode == SC_SAFE

    # Clear everything on a commissioned spacecraft -> recovers to NOMINAL.
    eng._fdir_safe_active = False
    eng._update_sc_mode()
    assert eng.sc_mode == SC_NOMINAL


def test_payload_imaging_in_nominal_derives_science():
    eng = _make_engine()
    _clear(eng)
    eng.params[0x0600] = PAYLOAD_MODE_IMAGING
    eng._update_sc_mode()
    assert eng.sc_mode == SC_SCIENCE


def test_contingency_overrides_imaging():
    eng = _make_engine()
    _clear(eng)
    eng.params[0x0600] = PAYLOAD_MODE_IMAGING

    # A safe contingency while imaging -> SAFE, not SCIENCE.
    eng._fdir_safe_active = True
    eng._update_sc_mode()
    assert eng.sc_mode == SC_SAFE

    # An emergency while imaging -> EMERGENCY.
    eng._fdir_safe_active = False
    eng._fdir_emergency_active = True
    eng._update_sc_mode()
    assert eng.sc_mode == SC_EMERGENCY


def test_imaging_before_commissioning_stays_safe():
    eng = _make_engine()
    _clear(eng)
    eng._spacecraft_phase = SC_PHASE_NOMINAL - 1
    eng.params[0x0600] = PAYLOAD_MODE_IMAGING
    eng._update_sc_mode()
    assert eng.sc_mode == SC_SAFE

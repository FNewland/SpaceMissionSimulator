"""Defect D: eclipse / sunlight telemetry display.

The simulator emits the eclipse flag under JSON key `in_eclipse`, but the MCS
power-budget display previously read `eclipse_active` (never produced), so the
eclipse badge was always False. It also read time_to_eclipse_entry/exit_s which
had no producer.

This test verifies:
  1. The display consumes the `in_eclipse` key the simulator actually emits.
  2. The orbit propagator's next_eclipse_transition() produces the time fields.
"""
import pytest

from smo_mcs.displays.power_budget import PowerBudgetMonitor


def test_display_reflects_in_eclipse_key():
    """The power-budget display must pick up the simulator's `in_eclipse` key."""
    mon = PowerBudgetMonitor()
    mon.update_from_telemetry({"in_eclipse": True, "eps": {}})
    assert mon.get_display_data()["eclipse_active"] is True

    mon.update_from_telemetry({"in_eclipse": False, "eps": {}})
    assert mon.get_display_data()["eclipse_active"] is False


def test_display_falls_back_to_eclipse_active():
    """Legacy `eclipse_active` key still works when `in_eclipse` is absent."""
    mon = PowerBudgetMonitor()
    mon.update_from_telemetry({"eclipse_active": True, "eps": {}})
    assert mon.get_display_data()["eclipse_active"] is True


def test_display_surfaces_time_to_eclipse_fields():
    mon = PowerBudgetMonitor()
    mon.update_from_telemetry({
        "in_eclipse": False,
        "time_to_eclipse_entry_s": 480.0,
        "time_to_eclipse_exit_s": 2460.0,
        "eps": {},
    })
    data = mon.get_display_data()
    assert data["time_to_eclipse_entry_s"] == 480.0
    assert data["time_to_eclipse_exit_s"] == 2460.0


def test_engine_state_summary_emits_eclipse_transition():
    """End-to-end: the engine state summary feeds the display correctly."""
    from smo_simulator.engine import SimulationEngine

    eng = SimulationEngine("configs/eosat1")
    ss = eng.get_state_summary()
    assert "in_eclipse" in ss
    assert "time_to_eclipse_entry_s" in ss
    assert "time_to_eclipse_exit_s" in ss

    mon = PowerBudgetMonitor()
    mon.update_from_telemetry(ss)
    data = mon.get_display_data()
    # The display's eclipse badge must match the simulator's eclipse state.
    assert data["eclipse_active"] == bool(ss["in_eclipse"])


def test_propagator_next_eclipse_transition_shape():
    """The forward-scan producer returns the expected fields and is bounded."""
    from smo_simulator.engine import SimulationEngine

    eng = SimulationEngine("configs/eosat1")
    result = eng.orbit.next_eclipse_transition(duration_s=7200.0, step_s=60.0)
    assert set(result) == {
        "in_eclipse", "time_to_eclipse_entry_s", "time_to_eclipse_exit_s"
    }
    # At least one transition should be found within a 2-hour LEO horizon.
    assert (result["time_to_eclipse_entry_s"] is not None
            or result["time_to_eclipse_exit_s"] is not None)

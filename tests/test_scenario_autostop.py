"""Tests for scenario auto-stop on duration expiry.

Bug: failure-script scenarios never stopped at the end. ScenarioEngine.tick()
only logged "duration expired" but left self._active set, so the engine's main
loop (which ticks the scenario only while se.is_active()) kept running it
forever. The fix deactivates the scenario, caches a debrief, clears injected
failures, and resets fired-event state when elapsed >= duration_s.
"""
import logging

import pytest

from smo_simulator.scenario_engine import (
    ScenarioDefinition,
    ScenarioEngine,
    ScenarioEvent,
)

logging.disable(logging.CRITICAL)


class FakeFailureManager:
    """Minimal stand-in recording inject/clear so we can assert on them."""

    def __init__(self):
        self.injected = []
        self.cleared = []
        self._counter = 0

    def inject(self, subsystem="", failure="", magnitude=1.0, onset="step",
               duration_s=0.0, **extra):
        self._counter += 1
        fid = f"{subsystem}.{failure}.{self._counter}"
        self.injected.append(fid)
        return fid

    def clear(self, failure_id):
        self.cleared.append(failure_id)
        return True

    def clear_all(self):
        self.cleared.append("__ALL__")


def _make_engine(duration_s=10.0, with_inject=False):
    se = ScenarioEngine(failure_manager=FakeFailureManager())
    events = []
    if with_inject:
        events.append(ScenarioEvent(
            time_offset_s=1.0, action="inject",
            params={"subsystem": "eps", "failure": "bus_undervolt",
                    "duration_s": 0},  # indefinite — must be cleared on stop
        ))
    defn = ScenarioDefinition(
        name="autostop_test", duration_s=duration_s, events=events,
        expected_responses=[{"category": "detect"}],
    )
    se._scenarios[defn.name] = defn
    return se


def test_scenario_auto_stops_at_duration():
    se = _make_engine(duration_s=10.0)
    assert se.start("autostop_test")
    assert se.is_active()

    # Tick up to just before duration — still active.
    se.tick(9.0, {})
    assert se.is_active(), "scenario ended early"
    assert se.last_debrief() is None

    # Tick past duration — must auto-stop.
    se.tick(2.0, {})  # elapsed = 11 >= 10
    assert not se.is_active(), "scenario did not auto-stop at duration"
    debrief = se.last_debrief()
    assert debrief is not None, "no debrief available after auto-stop"
    assert debrief.name == "autostop_test"
    assert debrief.duration_s >= 10.0


def test_auto_stop_is_idempotent_and_does_not_crash():
    se = _make_engine(duration_s=5.0)
    se.start("autostop_test")
    se.tick(6.0, {})              # auto-stops
    assert not se.is_active()
    # Further ticks on an inactive engine are no-ops, don't raise.
    se.tick(100.0, {})
    se.tick(100.0, {})
    assert not se.is_active()
    # stop() on an already-stopped scenario returns None, no crash.
    assert se.stop() is None


def test_injected_failures_cleared_on_auto_stop():
    se = _make_engine(duration_s=5.0, with_inject=True)
    fm = se._fm
    se.start("autostop_test")
    se.tick(2.0, {})             # fires the inject at t=1.0
    assert len(fm.injected) == 1, "inject event did not fire"
    se.tick(4.0, {})            # elapsed=6 >= 5 -> auto-stop
    assert not se.is_active()
    assert fm.injected[0] in fm.cleared, \
        "scenario-injected failure not cleared on auto-stop"


def test_fired_flags_reset_so_scenario_can_rerun():
    se = _make_engine(duration_s=5.0, with_inject=True)
    se.start("autostop_test")
    se.tick(6.0, {})            # fire + auto-stop
    defn = se._scenarios["autostop_test"]
    assert all(not ev.fired for ev in defn.events), \
        "fired flags not reset after stop; scenario could not be re-run"

    # Re-run: events must fire again.
    fm = se._fm
    n_before = len(fm.injected)
    se.start("autostop_test")
    se.tick(2.0, {})
    assert len(fm.injected) == n_before + 1, "event did not re-fire on rerun"


def test_manual_stop_still_returns_debrief():
    se = _make_engine(duration_s=100.0)
    se.start("autostop_test")
    se.tick(3.0, {})
    debrief = se.stop()
    assert debrief is not None
    assert not se.is_active()
    assert se.last_debrief() is debrief

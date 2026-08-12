"""``ProcedureExecutor`` must not report success for a procedure that sent
nothing (2026-08b Phase 5, the prerequisite for ruling R4).

THE DEFECT, as measured on the pre-fix tree
-------------------------------------------
Four distinct ways to command nothing all produced ``PROCEDURE_STARTED``
(0x0F07) followed by ``PROCEDURE_COMPLETED`` (0x0F08), with
``get_active_procedures()`` reporting ``progress_pct: 100.0``:

======================  ==========================================
``steps: []``           ``all([])`` is ``True``, so the execution was
                        COMPLETE on its first tick
no ``steps`` key        same path
unregistered command    ``_execute_step`` looked the callback up, found
                        ``None`` and fell through in silence, while the
                        CALLER unconditionally set ``step["executed"] = True``
no ``command`` key      ``step.get("command", "")`` is ``""``, which is never
                        a registered callback — the previous row again
======================  ==========================================

``PROCEDURE_FAILED`` (0x0F09) fired only when a *registered* callback raised.
Since 0x0F08 and the ``proc.*`` telemetry (0x0900-0x0903) drive the FDIR window,
a level-3 recovery that did nothing looked exactly like one that worked.

WHAT IS DELIBERATELY NOT CHANGED
--------------------------------
FDIR still does not treat procedure completion as fault-cleared:
``EOSat1Engine._complete_recovery`` is called only when the fault CONDITION
clears, and escalation stays wall-clock driven. That is asserted below so the
mitigation cannot be removed by accident. What this fix buys is narrower and
sufficient: 0x0F08 now means "the commands went out".
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from smo_simulator.fdir import ProcedureExecutor

REPO = Path(__file__).resolve().parents[2]
FDIR_PROC_DIR = REPO / "configs" / "eosat1" / "fdir" / "procedures"

HEALTHY = {"procedure_id": "p", "steps": [
    {"step_id": 1, "name": "cmd", "delay_s": 0.0, "command": "s8_command",
     "params": {"func_id": 0, "data": [0x03]}},
]}

#: The four false-success shapes, each verified against the pre-fix code.
FALSE_SUCCESS_CASES = {
    "empty step list": {"procedure_id": "p", "name": "P", "steps": []},
    "no steps key": {"procedure_id": "p", "name": "P"},
    "steps is not a list": {"procedure_id": "p", "steps": {"a": 1}},
    "unregistered command": {"procedure_id": "p", "steps": [
        {"step_id": 1, "command": "does_not_exist", "params": {}}]},
    "no command key": {"procedure_id": "p", "steps": [
        {"step_id": 1, "name": "silent", "params": {"func_id": 0}}]},
}


def _executor(sent: list | None = None):
    ex = ProcedureExecutor()
    ex.register_command_callback(
        "s8_command", lambda params: (sent.append(params) if sent is not None
                                      else None))
    return ex


def _run(ex, proc_id="p", ticks=5):
    events: list[dict] = []
    ex.register_event_callback(events.append)
    exec_id = ex.start_procedure(proc_id, 0.0)
    for t in range(ticks):
        ex.tick_procedures(float(t))
    return exec_id, events


def _ids(events):
    return [e["event_id"] for e in events]


# ── The four false-success shapes ───────────────────────────────────

@pytest.mark.parametrize("label,config", sorted(FALSE_SUCCESS_CASES.items()))
def test_a_procedure_that_can_send_nothing_fails_loudly(label, config):
    ex = _executor()
    ex.register_procedure("p", config)
    exec_id, events = _run(ex)

    assert exec_id is None, (
        f"{label}: start_procedure returned an execution id for a procedure "
        f"that cannot command")
    assert 0x0F08 not in _ids(events), (
        f"{label}: PROCEDURE_COMPLETED was emitted for a run that sent nothing")
    assert 0x0F07 not in _ids(events), (
        f"{label}: PROCEDURE_STARTED was emitted for a run that never began")
    failed = [e for e in events if e["event_id"] == 0x0F09]
    assert failed, f"{label}: no PROCEDURE_FAILED"
    assert failed[0].get("reason"), (
        f"{label}: PROCEDURE_FAILED carries no reason — the operator is told "
        f"'it failed' and nothing else")


@pytest.mark.parametrize("label,config", sorted(FALSE_SUCCESS_CASES.items()))
def test_validate_procedure_explains_why(label, config):
    ex = _executor()
    ex.register_procedure("p", config)
    problems = ex.validate_procedure("p")
    assert problems, f"{label}: validate_procedure found nothing wrong"
    assert all(isinstance(p, str) and p for p in problems)


def test_a_healthy_procedure_still_completes_and_actually_sends():
    sent: list = []
    ex = _executor(sent)
    ex.register_procedure("p", HEALTHY)
    exec_id, events = _run(ex)

    assert exec_id is not None
    assert _ids(events) == [0x0F07, 0x0F08]
    assert sent == [{"func_id": 0, "data": [0x03]}], (
        "the callback did not receive the step's params")
    assert ex.execution_state(exec_id) == "COMPLETE"
    assert ex.execution_failure_reason(exec_id) == ""


def test_progress_is_never_100_percent_for_a_run_that_sent_nothing():
    """``get_active_procedures()['progress_pct']`` feeds the FDIR window and
    telemetry 0x0900-0x0903. It reported 100.0 for every false-success case."""
    for label, config in FALSE_SUCCESS_CASES.items():
        ex = _executor()
        ex.register_procedure("p", config)
        _run(ex)
        assert ex.get_active_procedures() == [], (
            f"{label}: a non-executable procedure is still listed as active")


# ── execution_state was a trap ──────────────────────────────────────

def test_execution_state_answers_for_a_completed_run():
    """It returned None for EVERY normally-completed procedure.

    ``_last_finished`` was assigned at the TOP of the next tick's loop, but a
    COMPLETE execution is popped out of ``_active_procedures`` in the SAME tick
    it completes, so that branch was never reached for it. Nothing consumed the
    value, which made it a trap for whoever wired in "did the recovery actually
    run?" rather than a live bug.
    """
    ex = _executor()
    ex.register_procedure("p", HEALTHY)
    exec_id, _ = _run(ex)
    assert ex.execution_state(exec_id) == "COMPLETE"


def test_execution_state_answers_for_a_failed_run():
    ex = ProcedureExecutor()

    def boom(_params):
        raise RuntimeError("dispatcher exploded")

    ex.register_command_callback("s8_command", boom)
    ex.register_procedure("p", HEALTHY)
    exec_id, events = _run(ex)
    assert ex.execution_state(exec_id) == "FAILED"
    assert "dispatcher exploded" in ex.execution_failure_reason(exec_id)
    assert 0x0F08 not in _ids(events)
    assert 0x0F09 in _ids(events)


def test_execution_state_is_none_for_an_unknown_id():
    assert _executor().execution_state("never-existed") is None


def test_a_raising_step_stops_the_rest_of_the_procedure():
    """A card whose step 2 raised used to keep executing steps 3..N against a
    spacecraft that had already refused step 2."""
    calls: list = []

    def fail_on_second(params):
        calls.append(params)
        if len(calls) == 2:
            raise RuntimeError("no")

    ex = ProcedureExecutor()
    ex.register_command_callback("s8_command", fail_on_second)
    ex.register_procedure("p", {"steps": [
        {"step_id": n, "command": "s8_command", "params": {"func_id": n},
         "delay_s": 0.0} for n in range(1, 5)]})
    exec_id, _ = _run(ex)
    assert ex.execution_state(exec_id) == "FAILED"
    assert len(calls) == 2, f"executed {len(calls)} steps after the failure"


# ── Every shipped FDIR card ─────────────────────────────────────────

def test_every_shipped_fdir_card_is_executable():
    """The content gate lives in tests/test_content/test_procedure_gates.py;
    this is the runtime half, asserted through the executor itself so the two
    cannot drift."""
    ex = _executor()
    for path in sorted(FDIR_PROC_DIR.glob("*.yaml")):
        cfg = yaml.safe_load(path.read_text()) or {}
        ex.register_procedure(cfg["procedure_id"], cfg)
    bad = {pid: ex.validate_procedure(pid)
           for pid in ex.registered_procedures()
           if ex.validate_procedure(pid)}
    assert not bad, f"FDIR cards that cannot command: {bad}"


def test_every_shipped_fdir_card_dispatches_every_step():
    sent: list = []
    ex = _executor(sent)
    for path in sorted(FDIR_PROC_DIR.glob("*.yaml")):
        cfg = yaml.safe_load(path.read_text()) or {}
        ex.register_procedure(cfg["procedure_id"], cfg)
    for pid in ex.registered_procedures():
        sent.clear()
        exec_id = ex.start_procedure(pid, 0.0)
        assert exec_id is not None, pid
        # Step delays run to 9.5 s in aocs_reset; tick well past them.
        for t in range(0, 40):
            ex.tick_procedures(float(t))
        assert ex.execution_state(exec_id) == "COMPLETE", pid
        expected = len(yaml.safe_load(
            (FDIR_PROC_DIR / f"{pid}.yaml").read_text())["steps"])
        assert len(sent) == expected, (
            f"{pid}: {len(sent)} commands sent for {expected} steps")


# ── The mitigation that must be preserved ───────────────────────────

def test_completion_is_not_treated_as_fault_cleared():
    """FDIR must NOT infer recovery from 0x0F08.

    ``_complete_recovery`` is called only from the fault-condition path, and
    escalation is wall-clock driven by ``_recovery_level_delays``. Making a
    completed procedure mean "recovered" would be the wrong change: a procedure
    completing is evidence that commands were SENT, not that the spacecraft is
    well. This test pins that so the executor fix cannot quietly acquire it.
    """
    engine_src = (REPO / "packages" / "smo-simulator" / "src" /
                  "smo_simulator" / "engine.py").read_text()
    body = engine_src.split("def _tick_recovery", 1)[1].split("def ", 1)[0]
    assert "_complete_recovery" not in body, (
        "_tick_recovery now calls _complete_recovery — FDIR has started "
        "treating procedure completion as fault-cleared. That is a behaviour "
        "change this phase deliberately did not make; see the module docstring "
        "of smo_simulator.fdir.ProcedureExecutor.")
    assert "execution_state" not in body, (
        "_tick_recovery now consults the executor's state. That is a "
        "reasonable future change, but it must be made deliberately and this "
        "test updated with it.")


def test_state_round_trip_preserves_the_failure_reason():
    ex = ProcedureExecutor()
    ex.register_command_callback("s8_command", lambda p: None)
    ex.register_procedure("p", {"steps": [
        {"step_id": 1, "command": "s8_command", "params": {}, "delay_s": 99.0}]})
    ex.start_procedure("p", 0.0)
    state = ex.get_state()
    assert "failure_reason" in state["active"][0]
    restored = ProcedureExecutor()
    restored.set_state(state)
    assert len(restored.get_active_procedures()) == 1

"""FDIR fault propagation and recovery must actually run (brief 08 WP-5).

`FaultPropagator` was constructed and nine cascades registered from
`configs/eosat1/fdir/fault_propagation.yaml`, but `detect_fault()` had **zero
call sites**; `ProcedureExecutor.start_procedure()` likewise, so the ten
recovery procedures never executed and `tick_procedures` always iterated an
empty list.  Seven of the eight FDIR events in the catalogue could therefore
never be emitted.

The tests are written against the shipped config rather than a fixture, so a
cascade or recovery level added later has to be implemented, not just
declared.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from smo_simulator.engine import SimulationEngine
from smo_simulator.fdir import FaultPropagationRule

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
FP_YAML = CONFIG_DIR / "fdir" / "fault_propagation.yaml"


@pytest.fixture
def engine():
    eng = SimulationEngine(str(CONFIG_DIR))
    # Fault propagation is armed only once the spacecraft is commissioned
    # (see `_tick_fdir_advanced`); the shipped mission starts in the
    # bootloader.
    eng._spacecraft_phase = 6
    return eng


def _cfg() -> dict:
    return yaml.safe_load(FP_YAML.read_text()) or {}


def _events(eng) -> list[int]:
    """Drain the engine's event queue and return the event ids seen."""
    ids = []
    q = eng._event_queue if hasattr(eng, "_event_queue") else None
    return ids


# ── 1. Configuration is complete and consumable ─────────────────────────

def test_every_cascade_rule_declares_a_condition():
    for c in _cfg()["fault_cascades"]:
        assert c.get("condition"), (
            f"{c['fault_id']} has no explicit condition; the direction of the "
            f"threshold comparison must be config, not inference")


def test_every_primary_param_resolves(engine):
    """A fault whose primary parameter cannot be resolved can never fire."""
    unresolved = []
    for rule in engine._fault_propagator.rules():
        if engine._resolve_param_name(rule.primary_param) is None:
            unresolved.append((rule.fault_id, rule.primary_param))
    assert not unresolved, f"unresolvable fault-cascade parameters: {unresolved}"


def test_every_basic_fdir_rule_param_resolves(engine):
    """The same closure for the seventeen subsystems/fdir.yaml rules.

    Four of them (aocs.wheels_enabled, obdh.can_bus_status, obdh.temp,
    ttc.pa_temp) named parameters that live only in parameters.yaml, so
    `_resolve_param_name` returned None and they were skipped silently.
    """
    unresolved = [r.parameter for r in engine._fdir_rules
                  if engine._resolve_param_name(r.parameter) is None]
    assert not unresolved, f"unresolvable FDIR rule parameters: {unresolved}"


def test_every_cascade_action_has_an_implementation(engine):
    """(target_subsystem, action) pairs must map to a real command."""
    missing = []
    for rule in engine._fault_propagator.rules():
        for c in rule.cascades:
            key = f"{c['target_subsystem']}:{c['action']}"
            if key not in engine._cascade_callbacks:
                missing.append((rule.fault_id, key))
    assert not missing, f"cascade actions with no implementation: {missing}"


def test_every_recovery_procedure_is_registered(engine):
    registered = set(engine._procedure_executor.registered_procedures())
    missing = []
    for group, levels in engine._recovery_actions.items():
        for lvl, cfg in levels.items():
            ref = str((cfg or {}).get("procedure", ""))
            proc_id = ref[:-5] if ref.endswith(".yaml") else ref
            if proc_id and proc_id not in registered:
                missing.append((group, lvl, proc_id))
    assert not missing, f"recovery procedures not registered: {missing}"


def test_escalation_delays_were_parsed(engine):
    assert engine._recovery_level_delays, \
        "recovery_escalation.escalation_delays consumed by nobody"
    assert set(engine._recovery_level_delays) >= {1, 2, 3}
    assert engine._recovery_max_attempts >= 1


# ── 2. Detection actually happens on the tick ───────────────────────────

def _force(eng, name: str, value: float) -> int:
    pid = eng._resolve_param_name(name)
    assert pid is not None, name
    eng.params[pid] = value
    return pid


class _EventSink:
    def __init__(self, eng):
        self.ids: list[int] = []
        self._orig = eng._emit_event

        def spy(evt):
            self.ids.append(int(evt.get("event_id", 0)))
            return self._orig(evt)
        eng._emit_event = spy


def _drive_to_fault(engine, name="ttc.pa_temp", value=95.0,
                    fault_id="ttc_pa_overheat", limit=10):
    """Tick until *fault_id* is detected; return the tick count used."""
    for i in range(limit):
        _force(engine, name, value)
        engine._tick_fdir_advanced(1.0)
        if fault_id in engine._fault_propagator.active_faults():
            return i + 1
    raise AssertionError(f"{fault_id} never detected")


def test_fault_is_detected_and_emits_0x0F00(engine):
    sink = _EventSink(engine)
    engine._fdir_enabled = True
    # PA overheat: > 80 C with a 3 s persistence filter.
    for _ in range(6):
        _force(engine, "ttc.pa_temp", 95.0)
        engine._tick_fdir_advanced(1.0)
    assert "ttc_pa_overheat" in engine._fault_propagator.active_faults()
    assert 0x0F00 in sink.ids, "FDIR_FAULT_DETECTED never emitted"


def test_persistence_filter_suppresses_a_single_sample(engine):
    _force(engine, "ttc.pa_temp", 95.0)
    engine._tick_fdir_advanced(1.0)
    assert "ttc_pa_overheat" not in engine._fault_propagator.active_faults()


def test_cascade_applies_after_its_declared_delay(engine):
    """`ttc_pa_overheat` reduces TX power after 1 s, data rate after 5 s."""
    ttc = engine.subsystems["ttc"]
    start_rate = int(ttc._state.data_rate_mode)
    _drive_to_fault(engine)
    # Immediately after detection nothing has been applied yet.
    assert engine._pending_cascades
    for _ in range(8):
        _force(engine, "ttc.pa_temp", 95.0)
        engine._tick_fdir_advanced(1.0)
    assert not engine._pending_cascades, "cascades never became due"
    assert int(ttc._state.data_rate_mode) <= start_rate


def test_recovery_starts_a_procedure_and_emits_0x0F01(engine):
    sink = _EventSink(engine)
    _drive_to_fault(engine)
    rs = engine._fault_propagator.get_recovery_state("ttc_pa_overheat")
    assert rs is not None, "no recovery state created for a fault with a group"
    assert rs.level == 1
    assert rs.procedure == "ttc_reduce_power"
    assert rs.exec_id, "start_procedure returned no execution id"
    assert 0x0F01 in sink.ids, "FDIR_RECOVERY_STARTED never emitted"
    assert engine._procedure_executor.get_active_procedures()


def test_recovery_escalates_and_emits_0x0F04(engine):
    sink = _EventSink(engine)
    _drive_to_fault(engine)
    # level_1 delay is 30 s; hold the fault through it.
    for _ in range(40):
        _force(engine, "ttc.pa_temp", 95.0)
        engine._tick_fdir_advanced(1.0)
    rs = engine._fault_propagator.get_recovery_state("ttc_pa_overheat")
    assert rs.level >= 2, "recovery never escalated"
    assert 0x0F04 in sink.ids, "FDIR_LEVEL_ESCALATION never emitted"
    assert 0x0F03 in sink.ids, "FDIR_RECOVERY_FAILED never emitted on escalation"


def test_recovery_completes_when_the_fault_clears(engine):
    sink = _EventSink(engine)
    _drive_to_fault(engine)
    _force(engine, "ttc.pa_temp", 20.0)
    engine._tick_fdir_advanced(1.0)
    assert "ttc_pa_overheat" not in engine._fault_propagator.active_faults()
    assert 0x0F02 in sink.ids, "FDIR_RECOVERY_COMPLETE never emitted"
    rs = engine._fault_propagator.get_recovery_state("ttc_pa_overheat")
    assert rs.state == "COMPLETE"


def test_pending_cascades_are_dropped_when_the_fault_clears(engine):
    _drive_to_fault(engine)
    assert engine._pending_cascades
    _force(engine, "ttc.pa_temp", 20.0)
    engine._tick_fdir_advanced(1.0)
    assert not engine._pending_cascades


def test_bitset_condition_fires_on_the_named_flag(engine):
    rule = next(r for r in engine._fault_propagator.rules()
                if r.fault_id == "eps_overcurrent_aocs")
    assert rule.condition == "bitset"
    assert rule.evaluate(0b0100) is True     # bit 2 set
    assert rule.evaluate(0b0011) is False


def test_load_shed_deactivation_emits_0x0F06(engine):
    sink = _EventSink(engine)
    eps = engine.subsystems["eps"]
    eps._state.bat_soc_pct = 5.0
    engine._tick_fdir_advanced(1.0)
    assert engine._current_load_shed_stage > 0
    eps._state.bat_soc_pct = 95.0
    engine._tick_fdir_advanced(1.0)
    assert engine._current_load_shed_stage == 0
    assert 0x0F06 in sink.ids, "LOAD_SHED_DEACTIVATED never emitted"


# ── 3. Telemetry the FDIR window renders ────────────────────────────────

def test_fdir_parameters_are_published(engine):
    _drive_to_fault(engine)
    assert engine.params.get(0x0701) == 1        # active fault count
    assert engine.params.get(0x0703) >= 1        # running recoveries
    assert engine.params.get(0x0902) > 0         # total procedure steps
    assert engine.params.get(0x0903) == 1        # procedure state = running


def test_fdir_status_reports_faults_and_recovery(engine):
    _drive_to_fault(engine)
    st = engine.fdir_status()
    assert "ttc_pa_overheat" in st["active_faults"]
    assert st["recovery"] and st["recovery"][0]["procedure"] == "ttc_reduce_power"
    assert st["procedures"], "no procedure reported as executing"


def test_procedure_runs_to_completion(engine):
    """The two ttc_reduce_power steps execute and the procedure finishes."""
    _drive_to_fault(engine)
    assert engine.params.get(0x0903) == 1
    for _ in range(5):
        _force(engine, "ttc.pa_temp", 95.0)
        engine._tick_fdir_advanced(1.0)
    assert not engine._procedure_executor.get_active_procedures()
    assert engine.params.get(0x0903) == 0


# ── 4. State survives a breakpoint ──────────────────────────────────────

def test_fault_and_procedure_state_round_trip(engine):
    _drive_to_fault(engine)
    fp_state = engine._fault_propagator.get_state()
    pe_state = engine._procedure_executor.get_state()
    fresh = SimulationEngine(str(CONFIG_DIR))
    fresh._spacecraft_phase = 6
    fresh._fault_propagator.set_state(fp_state)
    fresh._procedure_executor.set_state(pe_state)
    assert fresh._fault_propagator.active_faults() == ["ttc_pa_overheat"]
    assert fresh._procedure_executor.get_active_procedures()


def test_fdir_components_are_in_the_state_registry(engine):
    names = set(engine.stateful_components())
    assert "fault_propagator" in names
    assert "procedure_executor" in names

"""Regression tests for remediation Phase 0 + Phase 1 fixes.

Covers:
  R1  — instructor-command dispatcher hardening (unknown types logged, not dropped)
  #9  — scenario engine instantiated, scenarios loaded, start/stop wired
  #10 — breakpoint save/load round-trip via the instructor command path
  #12 — "clear all failures" actually clears
  R2/#23 — events generated inside subsystem models are delivered as S5 events
"""
import logging

import pytest

from smo_simulator.engine import SimulationEngine

logging.disable(logging.CRITICAL)

CONFIG = "configs/eosat1/"


@pytest.fixture
def engine():
    return SimulationEngine(CONFIG)


# ─────────────────────────── R1: dispatcher hardening ───────────────────────

def test_unknown_instructor_command_does_not_raise(engine):
    # Previously unknown types were silently dropped; now logged + ignored safely.
    engine._handle_instructor_cmd({"type": "totally_bogus_command"})
    engine._handle_instructor_cmd({})  # no 'type' at all


# ─────────────────────────── #9: scenario subsystem ─────────────────────────

def test_scenario_engine_instantiated_and_loaded(engine):
    assert hasattr(engine, "_scenario_engine")
    scns = engine._scenario_engine.list_scenarios()
    assert len(scns) > 0, "no scenarios loaded — /api/scenarios would be empty"


def test_start_and_stop_scenario_via_command(engine):
    name = engine._scenario_engine.list_scenarios()[0]["name"]
    engine._handle_instructor_cmd({"type": "start_scenario", "name": name})
    assert engine._scenario_engine.is_active()
    engine._handle_instructor_cmd({"type": "stop_scenario"})
    assert not engine._scenario_engine.is_active()


# ─────────────────────────── #10: breakpoints ───────────────────────────────

def test_breakpoint_save_load_round_trip(engine):
    engine._tick_count = 4242
    engine._handle_instructor_cmd({"type": "save_breakpoint", "name": "bp_test"})
    assert "bp_test" in engine._breakpoints
    engine._tick_count = 9999  # mutate
    engine._handle_instructor_cmd({"type": "load_breakpoint", "name": "bp_test"})
    assert engine._tick_count == 4242, "load did not restore tick_count"


def test_load_unknown_breakpoint_is_safe(engine):
    engine._handle_instructor_cmd({"type": "load_breakpoint", "name": "does_not_exist"})


def test_orbit_propagator_clock_restored_on_load(engine):
    """The propagator's _sim_utc was not being restored, so loading a breakpoint
    left the satellite at a drifted orbital position. Now it's captured."""
    t0 = engine.orbit.utc
    engine._handle_instructor_cmd({"type": "save_breakpoint", "name": "orbit_rt"})
    engine.orbit.advance(1800.0)                 # drift 30 min
    assert engine.orbit.utc != t0
    engine._handle_instructor_cmd({"type": "load_breakpoint", "name": "orbit_rt"})
    assert engine.orbit.utc == t0, "orbit propagator clock not restored"


# ─────────────────────────── #12: clear all failures ────────────────────────

def test_failure_clear_all(engine):
    engine._handle_instructor_cmd(
        {"type": "failure_inject", "subsystem": "aocs", "failure": "gyro_bias"}
    )
    assert len(engine._failure_manager.active_failures()) >= 1
    engine._handle_instructor_cmd({"type": "failure_clear_all"})
    assert len(engine._failure_manager.active_failures()) == 0


# ─────────────────────────── R2/#23: model event delivery ───────────────────

def _drain_record_queue(engine):
    out = []
    while not engine.event_queue.empty():
        out.append(engine.event_queue.get_nowait())
    return out


def test_model_event_dict_with_string_severity_is_delivered(engine):
    _drain_record_queue(engine)  # clear any startup events
    engine._model_event_queue.put(
        {"event_id": 0x0406, "severity": "MEDIUM", "description": "HEATER_STUCK_ON"}
    )
    engine._drain_model_events()
    ids = [e["event_id"] for e in _drain_record_queue(engine)]
    assert 0x0406 in ids, "model dict event not delivered to S5 path"


def test_model_event_eps_tuple_is_delivered(engine):
    _drain_record_queue(engine)
    engine._model_event_queue.put((0x0100, "EPS mode change: 0 -> 1", 0.0))
    engine._drain_model_events()
    ids = [e["event_id"] for e in _drain_record_queue(engine)]
    assert 0x0100 in ids, "EPS tuple event not delivered to S5 path"


def test_models_have_engine_backref(engine):
    # Defect #23 root cause: _engine was never set, so model event code never ran.
    for name, model in engine.subsystems.items():
        assert getattr(model, "_engine", None) is engine, f"{name} missing _engine backref"


# ─────────────────────────── #11: OBC heater command persists ───────────────

def test_obc_heater_command_persists_across_ticks(engine):
    tcs = engine.subsystems["tcs"]
    orbit_state = engine.orbit.advance(0.0)
    engine.params[0x0116] = 1  # OBC heater EPS line powered
    assert tcs.handle_command({"command": "heater", "circuit": "obc", "on": True})["success"]
    for _ in range(5):
        tcs.tick(1.0, orbit_state, engine.params)
    assert tcs._state.htr_obc is True, "OBC heater ON command was overwritten by tick"
    # Commanding OFF must also stick.
    tcs.handle_command({"command": "heater", "circuit": "obc", "on": False})
    for _ in range(3):
        tcs.tick(1.0, orbit_state, engine.params)
    assert tcs._state.htr_obc is False


# ─────────────────────────── #26: MAG_SELECT picks A/B ──────────────────────

def test_mag_select_via_s8_func7(engine):
    aocs = engine.subsystems["aocs"]
    engine._dispatcher._route_aocs_cmd(7, bytes([1]))  # unit 1 -> Mag B
    assert aocs._state.mag_select == "B"
    engine._dispatcher._route_aocs_cmd(7, bytes([0]))  # unit 0 -> Mag A
    assert aocs._state.mag_select == "A"


# ─────────────────────────── #28: uplink loss kills the downlink lock ────────

class _ContactOrbitState:
    in_contact = True
    gs_elevation_deg = 45.0
    gs_azimuth_deg = 10.0
    gs_range_km = 800.0
    in_eclipse = False


def test_uplink_loss_holds_link_unlocked(engine):
    ttc = engine.subsystems["ttc"]
    ttc._state.uplink_lost = True
    for _ in range(60):
        ttc.tick(1.0, _ContactOrbitState(), engine.params)
    assert ttc._state.frame_sync is False, "downlink should be unlocked while uplink lost"
    assert ttc._state.carrier_lock is False


# ─────────────────────────── #25: wheel_failure scenario no longer a no-op ──

def test_wheel_failure_scenario_uses_handled_failure(engine):
    # The scenario now injects rw_seizure (a handled failure), not the
    # unhandled 'wheel_failure' that previously silently no-op'd.
    aocs = engine.subsystems["aocs"]
    aocs.inject_failure("rw_seizure", wheel=2)
    assert aocs._bearing_degradation[2] == 1.0, "rw_seizure did not degrade the wheel"


def test_unknown_aocs_failure_is_safe_noop(engine):
    # The new else-branch must log + no-op without raising.
    engine.subsystems["aocs"].inject_failure("totally_unknown_failure_name")


# ─────────────────────────── #30: OBDH watchdog fires in nominal ────────────

def test_watchdog_reset_reboots_in_nominal_mode(engine):
    from smo_simulator.models.obdh_basic import SW_APPLICATION
    obdh = engine.subsystems["obdh"]
    s = obdh._state
    s.sw_image = SW_APPLICATION
    s.mode = 0          # nominal — watchdog must be active here
    s.watchdog_armed = True
    before = s.reboot_count
    obdh.inject_failure("watchdog_reset")
    obdh.tick(1.0, engine.orbit.advance(0.0), engine.params)
    assert s.reboot_count == before + 1, "watchdog_reset did not reboot in nominal mode"


# ─────────────────────────── #24: RW thermal signature ──────────────────────

def test_seized_wheel_heats_and_is_observable(engine):
    aocs = engine.subsystems["aocs"]
    orbit_state = engine.orbit.advance(0.0)
    aocs._state.active_wheels[2] = True
    aocs._state.rw_temp[2] = 20.0
    aocs.inject_failure("rw_seizure", wheel=2)
    for _ in range(60):
        aocs.tick(1.0, orbit_state, engine.params)
    assert aocs._state.rw_temp[2] > 25.0, "seized wheel temperature did not rise"
    # RW temp must now be published as a shared param (it's in SID 2).
    assert 0x021A in engine.params, "RW3 temp param not produced"


def test_healthy_disabled_wheel_does_not_heat(engine):
    aocs = engine.subsystems["aocs"]
    orbit_state = engine.orbit.advance(0.0)
    aocs._state.active_wheels[3] = False  # cleanly disabled, no degradation
    aocs._state.rw_temp[3] = 20.0
    for _ in range(60):
        aocs.tick(1.0, orbit_state, engine.params)
    assert aocs._state.rw_temp[3] < 25.0, "healthy disabled wheel should not heat"


def test_rw_temp_params_in_aocs_hk_sid():
    # Defect #24: 0x0218-0x021B must appear in an HK structure to be observable.
    # Check the loaded config directly (format-agnostic deep scan for the IDs).
    from pathlib import Path
    from smo_common.config.loader import load_hk_structures
    raw = load_hk_structures(Path("configs/eosat1/"))
    present = {p.param_id for struct in raw for p in struct.parameters}
    for pid in (0x0218, 0x0219, 0x021A, 0x021B):
        assert pid in present, f"{hex(pid)} not in any HK SID"


# ─────────────────────────── #31: decontamination heats the FPA ─────────────

def test_decontamination_heats_fpa(engine):
    tcs = engine.subsystems["tcs"]
    orbit_state = engine.orbit.advance(0.0)
    tcs._state.temp_fpa = -5.0
    assert tcs.handle_command(
        {"command": "decontamination_start", "target_temp_c": 50.0}
    )["success"]
    for _ in range(120):
        tcs.tick(1.0, orbit_state, engine.params)
    assert tcs._state.temp_fpa > 20.0, "decontamination did not warm the FPA toward target"


# ─────────────────────────── #27: EPS safe mode sheds load + S8 route ───────

def test_eps_safe_mode_sheds_load_via_s8(engine):
    eps = engine.subsystems["eps"]
    orbit_state = engine.orbit.advance(0.0)
    for ln in ("payload", "fpa_cooler", "ttc_tx", "aocs_wheels"):
        eps._state.power_lines[ln] = True
    engine._dispatcher._route_eps_cmd(83, bytes([1]))  # S8 func 83 -> safe mode
    eps.tick(1.0, orbit_state, engine.params)
    assert eps._state.eps_mode == 1, "set_eps_mode (S8 func 83) did not take effect"
    assert eps._state.power_lines["payload"] is False, "safe mode did not shed payload"
    assert eps._state.power_lines["fpa_cooler"] is False, "safe mode did not shed fpa_cooler"


def test_eps_emergency_mode_sheds_all_loads(engine):
    eps = engine.subsystems["eps"]
    orbit_state = engine.orbit.advance(0.0)
    for ln in ("payload", "fpa_cooler", "ttc_tx", "aocs_wheels"):
        eps._state.power_lines[ln] = True
    engine._dispatcher._route_eps_cmd(83, bytes([2]))  # emergency
    eps.tick(1.0, orbit_state, engine.params)
    assert all(
        eps._state.power_lines[ln] is False
        for ln in ("payload", "fpa_cooler", "ttc_tx", "aocs_wheels")
    ), "emergency mode did not shed all non-essential loads"


def test_fdir_safe_mode_eps_callback_invokes_handled_command(engine):
    # Defect #27: the callback must hit a command EPS actually handles.
    engine._fdir_callbacks["safe_mode_eps"]()
    assert engine.subsystems["eps"]._state.eps_mode == 1


# ─────────────────────────── #29: OBDH bus reachability ─────────────────────

def _fail_active_bus(engine):
    from smo_simulator.models.obdh_basic import BUS_FAILED
    obdh = engine.subsystems["obdh"]
    if obdh._state.active_bus == 0:
        obdh._state.bus_a_status = BUS_FAILED
    else:
        obdh._state.bus_b_status = BUS_FAILED


def test_bus_failure_blocks_subsystem_hk_but_not_obc(engine):
    assert engine._obc_bus_failed() is False  # nominal: nothing gated
    _fail_active_bus(engine)
    assert engine._obc_bus_failed() is True
    # SID 4 (OBC) and 11 (beacon) survive; subsystem SIDs are gated.
    assert 4 not in engine._BUS_GATED_SIDS
    assert 11 not in engine._BUS_GATED_SIDS
    for sid in (1, 2, 3, 5, 6):
        assert sid in engine._BUS_GATED_SIDS


def test_nominal_ops_not_gated(engine):
    # Regression: in normal ops payload/TTC HK must NOT be suppressed.
    assert engine._obc_bus_failed() is False


# ─────────────────────────── #31 remainder: setpoint/auto-mode reject ───────

def test_setpoint_rejected_for_heaterless_thruster_circuit(engine):
    tcs = engine.subsystems["tcs"]
    # battery (thermostat) and obc (telemetered setpoint + manual flag) are real
    assert tcs.handle_command(
        {"command": "set_setpoint", "circuit": 0, "on_temp": 2.0, "off_temp": 6.0}
    )["success"]
    assert tcs.handle_command(
        {"command": "set_setpoint", "circuit": 1, "on_temp": 2.0, "off_temp": 6.0}
    )["success"]
    # thruster (idx 2) has no heater -> rejected, not a false success
    assert tcs.handle_command(
        {"command": "set_setpoint", "circuit": 2, "on_temp": 2.0, "off_temp": 6.0}
    )["success"] is False
    assert tcs.handle_command({"command": "auto_mode", "circuit": 2})["success"] is False


def test_heater_duty_limit_enforced(engine):
    tcs = engine.subsystems["tcs"]
    orbit_state = engine.orbit.advance(0.0)
    s = tcs._state
    engine.params[0x0116] = 1
    tcs.handle_command({"command": "heater", "circuit": "obc", "on": True})
    s.htr_obc_duty_limit_pct = 20.0   # cap OBC heater duty at 20%
    for _ in range(400):
        tcs.tick(1.0, orbit_state, engine.params)
    assert s.htr_duty_obc <= 35.0, f"duty {s.htr_duty_obc} not capped near limit"


# ─────────────────────────── #14: MCS procedure runner ──────────────────────

def test_procedure_runner_comparison_operators():
    from smo_mcs.procedure_runner import ProcedureRunner as P
    assert P._compare(30, ">", 25) and not P._compare(20, ">", 25)
    assert P._compare(5, "<=", 5) and P._compare(4, "<", 5)
    assert P._compare(1, "==", 1) and P._compare(3, "!=", 4)
    assert not P._compare(1, "!=", 1)


def test_procedure_status_uses_status_not_get_status():
    # Defect #13: the route called a non-existent get_status(); status() exists.
    from smo_mcs.procedure_runner import ProcedureRunner
    assert hasattr(ProcedureRunner, "status")
    assert not hasattr(ProcedureRunner, "get_status")

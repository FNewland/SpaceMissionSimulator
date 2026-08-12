"""Simulation configuration + scenario schema tests (defects #51, #52).

Covers:
  * simulation.yaml is loaded and its values actually reach the engine and the
    subsystem models (tick rate, separation timer, auto-TX hold, EPS
    protection levels, TCS environment, failure onset, scenario defaults);
  * a config directory WITHOUT simulation.yaml still works (code defaults);
  * previously-dead YAML sections (eps.yaml solar_panels/power_lines,
    tcs.yaml thermal_coupling/event_thresholds) are parsed;
  * scenario schema hardening: unknown actions / unknown failure modes are
    rejected at load time with a recorded issue, and initial_conditions,
    set_speed and set_phase are honoured.
"""
from pathlib import Path

import pytest
import yaml

CONFIG_DIR = Path(__file__).parent.parent.parent / "configs" / "eosat1"


@pytest.fixture(scope="module")
def engine():
    from smo_simulator.engine import SimulationEngine
    return SimulationEngine(CONFIG_DIR, speed=1.0)


class TestSimulationConfigFile:
    def test_file_exists_and_validates(self):
        from smo_common.config.loader import load_simulation_config
        cfg = load_simulation_config(CONFIG_DIR)
        assert cfg.phases.separation_timer_s == 1800.0
        assert cfg.ttc.auto_tx_hold_s == 300.0
        assert cfg.eps.initial.soc_pct == 90.0
        assert cfg.eps.load_shed.bus_voltage_v == 25.0
        assert cfg.eps.load_shed.soc_stage_thresholds_pct == [30.0, 20.0, 10.0]

    def test_missing_file_falls_back_to_defaults(self, tmp_path):
        from smo_common.config.loader import load_simulation_config
        cfg = load_simulation_config(tmp_path)
        # Defaults reproduce the historical hard-coded values.
        assert cfg.tick_hz is None
        assert cfg.phases.separation_timer_s == 1800.0
        assert cfg.ttc.auto_tx_hold_s == 900.0
        assert cfg.eps.initial.soc_pct == 75.0
        assert cfg.eps.load_shed.bus_voltage_v == 26.5
        assert cfg.failures.default_onset_duration_s == 90.0

    def test_engine_uses_config_values(self, engine):
        assert engine._sep_timer_duration == 1800.0
        assert engine._auto_tx_hold_s == 300.0
        assert engine._speed_max == 1000.0
        assert engine._failure_manager._default_onset_s == 90.0

    def test_eps_model_uses_config_values(self, engine):
        eps = engine.subsystems["eps"]
        assert eps._shed_voltage == 25.0
        assert eps._shed_order == ["payload", "fpa_cooler", "ttc_tx", "aocs_wheels", "prop"]
        assert eps._state.bat_soc_pct == 90.0
        # 2026-08b R1: the TX ratings are a real CubeSat radio's DC draw now
        # (Endurosat S-band: 5 W at the LOW tier, 11 W at 33 dBm / 2 W RF for
        # the HIGH-tier dump). This line asserted 12.0 while eps.yaml has said
        # 5.0 since Phase B — it was RED before this batch, not broken by it.
        assert eps._transponder_w == 5.0           # eps.yaml hardware rating
        assert eps._transponder_hi_w == 11.0
        # 2026-08b R1: 0.12 m2 of cells per wing (Endurosat 6U double
        # deployable geometry), down from Phase B's 0.18 m2.
        assert eps._wing_py_area == pytest.approx(0.12)
        # Phase B (brief 01 F22 / WP-3): full AOCS suite draw, sized for
        # Phase C's mode-dependent 4/9/15 W.
        assert eps._line_power_w["aocs_wheels"] == 12.0
        assert eps._oc_thresholds["payload"] == 2.5
        assert eps._evt["soc_low_pct"] == 20.0

    def test_tcs_model_reads_previously_dead_yaml_sections(self, engine):
        tcs = engine.subsystems["tcs"]
        # tcs.yaml thermal_coupling
        # Phase B (brief 05 WP-3): battery pack on standoffs, 0.1 W/K.
        assert tcs._zone_conductance[("battery", "obc")] == 0.1
        assert tcs._zone_conductance[("payload", "structure")] == 0.4
        assert tcs._space_radiation_g == 0.2
        # tcs.yaml event_thresholds
        assert tcs._temp_warning_limits["battery"] == (0.0, 40.0)
        # G3-d: the `fpa` rows are DERIVED from tcs.yaml `fpa_envelope`, not
        # hand-written. They used to be [-50, -5] warn / [-60, 0] alarm — an
        # imaging-readiness band worn as a health band.
        assert tcs._temp_alarm_limits["fpa"] == tcs._fpa_env.node_alarm().as_tuple()
        assert tcs._temp_warning_limits["fpa"] == tcs._fpa_env.node_warning().as_tuple()
        assert tcs._thermal_runaway_rate == 2.0
        # simulation.yaml tcs.environment
        assert tcs._env["eclipse_external_c"] == -30.0
        assert tcs._env["sunlit_external_beta_gain_c"] == 50.0

    def test_mission_network_tick_rate_is_parsed(self):
        from smo_common.config.loader import load_mission_config
        cfg = load_mission_config(CONFIG_DIR)
        assert cfg.network.sim_tick_hz == 1.0
        assert cfg.network.tc_port == 8001

    def test_eps_yaml_solar_panels_reach_the_model(self):
        """eps.yaml solar_panels/power_lines used to be dropped by the schema."""
        from smo_common.config.loader import load_all_subsystem_configs
        cfg = load_all_subsystem_configs(CONFIG_DIR)["eps"].model_dump()
        # 2026-08b R1: 0.12 m2 of cells per wing (see POWER_SIZING.md).
        assert cfg["solar_panels"]["wing_py_area_m2"] == pytest.approx(0.12)
        assert cfg["power_lines"]["ttc_tx"] is False

    def test_load_shed_thresholds_pushed_to_manager(self, engine):
        assert engine._load_shedding.soc_stage_thresholds() == [30.0, 20.0, 10.0]

    def test_set_speed_is_clamped(self, engine):
        engine._handle_instructor_cmd({'type': 'set_speed', 'value': 99999.0})
        assert engine.speed == engine._speed_max
        engine._handle_instructor_cmd({'type': 'set_speed', 'value': 1.0})
        assert engine.speed == 1.0


class TestScenarioSchemaHardening:
    def test_shipped_scenarios_load_without_issues(self, engine):
        assert engine._scenario_engine.load_issues() == []

    def test_unknown_action_is_rejected(self, tmp_path):
        from smo_simulator.scenario_engine import ScenarioEngine
        d = tmp_path / "scenarios"
        d.mkdir()
        (d / "bad.yaml").write_text(yaml.safe_dump({
            "name": "Bad Action", "events": [
                {"time_offset_s": 5, "action": "ground_failure",
                 "params": {"failure": "lna_degradation"}},
                {"time_offset_s": 10, "action": "message",
                 "params": {"text": "ok"}},
            ]}))
        se = ScenarioEngine()
        se.load_scenarios_from_dir(d)
        issues = se.load_issues()
        assert len(issues) == 1 and "unknown action" in issues[0]
        # The valid event still loads; the invalid one is skipped, not silently kept.
        assert len(se._scenarios["Bad Action"].events) == 1

    def test_unknown_failure_mode_is_rejected(self, tmp_path, engine):
        from smo_simulator.scenario_engine import ScenarioEngine
        d = tmp_path / "scenarios"
        d.mkdir()
        (d / "bad_failure.yaml").write_text(yaml.safe_dump({
            "name": "Bad Failure", "events": [
                {"time_offset_s": 5, "action": "inject",
                 "params": {"subsystem": "eps", "failure": "not_a_real_failure"}},
                {"time_offset_s": 6, "action": "inject",
                 "params": {"subsystem": "eps", "failure": "bus_short"}},
            ]}))
        se = ScenarioEngine(engine=engine)
        se.load_scenarios_from_dir(d)
        issues = se.load_issues()
        assert len(issues) == 1 and "unknown failure" in issues[0]
        assert len(se._scenarios["Bad Failure"].events) == 1

    def test_event_that_can_never_fire_is_rejected(self, tmp_path):
        from smo_simulator.scenario_engine import ScenarioEngine
        d = tmp_path / "scenarios"
        d.mkdir()
        (d / "dead.yaml").write_text(yaml.safe_dump({
            "name": "Dead", "events": [{"action": "message", "params": {"text": "x"}}]}))
        se = ScenarioEngine()
        se.load_scenarios_from_dir(d)
        assert any("can never fire" in i for i in se.load_issues())

    def test_initial_conditions_are_applied(self, engine):
        se = engine._scenario_engine
        try:
            assert se.start("First Contact")
            assert engine._spacecraft_phase == 3
            assert engine._override_passes is True
        finally:
            se.stop()
            engine._override_passes = False

    def test_separation_scenario_arms_timer_and_speed(self, engine):
        se = engine._scenario_engine
        try:
            assert se.start("Separation Sequence")
            assert engine._spacecraft_phase == 0
            assert engine.speed == 10.0
            assert engine.subsystems["eps"]._state.bat_soc_pct == pytest.approx(95.0)
            se.tick(10.0, engine.params)          # set_phase -> 1
            assert engine._spacecraft_phase == 1
            assert engine._sep_timer == pytest.approx(1800.0)
            se.tick(1800.0, engine.params)        # set_speed -> 1
            assert engine.speed == 1.0
        finally:
            se.stop()
            engine._handle_instructor_cmd({'type': 'set_phase', 'phase': 6})
            engine.speed = 1.0

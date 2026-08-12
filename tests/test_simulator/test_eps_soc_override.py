"""2026-08b Phase 2c: the instructor battery-SoC override.

Franz: "It would also be good to add a power override 'failure' (or other
mechanism) on the simulator that allows us to set the battery level higher
than it currently is, to allow us to continue running a sim where the battery
is getting low."

Nothing existing did this. `initial_conditions.soc_pct` fires only at scenario
start; `inject_failure("undervoltage")` only DRAINS; `set_parameter` is
overwritten by EPS on the next tick; breakpoints are far too blunt. It is
therefore a first-class instructor command — NOT a fake failure, because it is
not a fault: it is the instructor editing the world, and the event log has to
say so.
"""
from pathlib import Path

import pytest

CONFIG_DIR = Path(__file__).parent.parent.parent / "configs" / "eosat1"


@pytest.fixture(scope="module")
def engine():
    from smo_simulator.engine import SimulationEngine
    return SimulationEngine(CONFIG_DIR, speed=1.0)


class TestValidation:
    def test_command_type_is_registered(self):
        from smo_simulator.engine import SimulationEngine
        assert 'set_battery_soc' in SimulationEngine.INSTRUCTOR_CMD_TYPES

    def test_range_guard_present(self):
        from smo_simulator.engine import SimulationEngine
        assert SimulationEngine._INSTRUCTOR_NUMERIC_FIELDS['set_battery_soc'] \
            == [('soc_pct', 0.0, 100.0)]

    @pytest.mark.parametrize("soc", [0.0, 1.0, 50.0, 99.9, 100.0])
    def test_in_range_accepted(self, soc):
        from smo_simulator.engine import SimulationEngine
        ok, why = SimulationEngine.validate_instructor_cmd(
            {'type': 'set_battery_soc', 'soc_pct': soc})
        assert ok, why

    @pytest.mark.parametrize("bad", [-0.1, 100.1, 1e9, "high", float('nan')])
    def test_out_of_range_rejected_at_the_door(self, bad):
        """Rejected by the shared validator, so a malformed value never
        reaches — or raises inside — the tick thread."""
        from smo_simulator.engine import SimulationEngine
        ok, _ = SimulationEngine.validate_instructor_cmd(
            {'type': 'set_battery_soc', 'soc_pct': bad})
        assert not ok


class TestApplication:
    def test_raises_a_low_battery(self, engine):
        eps = engine.subsystems["eps"]
        eps._state.bat_soc_pct = 12.0
        eps._state.bat_dod_pct = 88.0
        engine._handle_instructor_cmd(
            {'type': 'set_battery_soc', 'soc_pct': 85.0})
        assert eps._state.bat_soc_pct == pytest.approx(85.0)
        assert eps._state.bat_dod_pct == pytest.approx(15.0)

    def test_clamped_to_0_100(self, engine):
        eps = engine.subsystems["eps"]
        engine._handle_instructor_cmd(
            {'type': 'set_battery_soc', 'soc_pct': 400.0})
        assert eps._state.bat_soc_pct == 100.0
        engine._handle_instructor_cmd(
            {'type': 'set_battery_soc', 'soc_pct': -50.0})
        assert eps._state.bat_soc_pct == 0.0

    def test_moves_the_edge_detector_so_no_bogus_alarm_fires(self, engine):
        """THE subtle one. SOC_LOW / SOC_CRITICAL / CHARGE_COMPLETE are edge
        triggered off `_prev_soc`. If the override moves bat_soc_pct without
        moving _prev_soc, the very next tick sees a 15 -> 90 % step and fires
        a bogus event. An override that lies in the event log is worse than
        no override at all."""
        eps = engine.subsystems["eps"]
        eps._state.bat_soc_pct = 15.0
        eps._state._prev_soc = 15.0
        engine._handle_instructor_cmd(
            {'type': 'set_battery_soc', 'soc_pct': 90.0})
        assert eps._state._prev_soc == pytest.approx(90.0)

    def test_emits_a_clearly_labelled_instructor_event(self, engine):
        events = []
        orig = engine._emit_event
        engine._emit_event = events.append
        try:
            engine._handle_instructor_cmd(
                {'type': 'set_battery_soc', 'soc_pct': 77.0})
        finally:
            engine._emit_event = orig
        assert len(events) == 1
        desc = events[0]['description']
        assert 'INSTRUCTOR OVERRIDE' in desc
        assert 'not physics' in desc
        assert '77.0' in desc

    def test_voltage_rederives_from_soc_on_the_next_tick(self, engine):
        """The override sets SoC only; the battery OCV, bus voltage and DoD
        all fall out of the model's own physics on the following tick."""
        eps = engine.subsystems["eps"]

        class _Orbit:
            solar_beta_deg = 0.0
            in_eclipse = True

        engine._handle_instructor_cmd(
            {'type': 'set_battery_soc', 'soc_pct': 20.0})
        eps.tick(1.0, _Orbit(), dict(engine.params))
        v_low = eps._state.bat_voltage

        engine._handle_instructor_cmd(
            {'type': 'set_battery_soc', 'soc_pct': 95.0})
        eps.tick(1.0, _Orbit(), dict(engine.params))
        v_high = eps._state.bat_voltage

        assert v_high > v_low + 3.0, (
            f"battery voltage did not follow the SoC override: "
            f"{v_low:.2f} V at 20 % vs {v_high:.2f} V at 95 %")


class TestInstructorConsole:
    """The console is the only place an instructor can reach this."""

    HTML = (Path(__file__).parent.parent.parent / "packages" / "smo-simulator"
            / "src" / "smo_simulator" / "instructor" / "static" / "index.html")

    def test_button_input_and_handler_exist(self):
        html = self.HTML.read_text()
        assert 'id="soc-override-input"' in html
        assert 'id="btn-set-soc"' in html
        assert "function setBatterySoc()" in html
        assert "type: 'set_battery_soc'" in html
        # Wired up, not just declared.
        assert "_soc.addEventListener('click', setBatterySoc)" in html

"""Tests for simulator subsystem models."""
import pytest
from unittest.mock import MagicMock


def make_orbit_state():
    state = MagicMock()
    state.in_eclipse = False
    state.solar_beta_deg = 20.0
    state.lat_deg = 45.0
    state.lon_deg = 10.0
    state.alt_km = 500.0
    state.vel_x = 0.0
    state.vel_y = 7.5
    state.vel_z = 0.0
    state.in_contact = False
    state.gs_elevation_deg = -10.0
    state.gs_azimuth_deg = 0.0
    state.gs_range_km = 2000.0
    return state


def test_eps_basic_tick():
    from smo_simulator.models.eps_basic import EPSBasicModel
    model = EPSBasicModel()
    model.configure({"battery": {"capacity_wh": 120.0}})
    params = {}
    orbit = make_orbit_state()
    model.tick(1.0, orbit, params)
    assert 0x0101 in params  # bat_soc
    assert params[0x0101] > 0


def test_aocs_basic_tick():
    from smo_simulator.models.aocs_basic import AOCSBasicModel
    model = AOCSBasicModel()
    model.configure({})
    params = {}
    orbit = make_orbit_state()
    model.tick(1.0, orbit, params)
    assert 0x020F in params  # aocs_mode


def test_tcs_basic_tick():
    from smo_simulator.models.tcs_basic import TCSBasicModel
    model = TCSBasicModel()
    model.configure({})
    params = {}
    orbit = make_orbit_state()
    model.tick(1.0, orbit, params)
    assert 0x0408 in params  # temp_fpa
    # DEFECT 3 fix: Verify setpoint readback parameters are populated
    assert 0x0330 in params  # setpoint_bat (battery heater ON setpoint)
    assert 0x0331 in params  # setpoint_obc (OBC heater ON setpoint)
    assert params[0x0330] > 0  # Should have default value
    assert params[0x0331] > 0  # Should have default value


def test_obdh_basic_tick():
    from smo_simulator.models.obdh_basic import OBDHBasicModel
    model = OBDHBasicModel()
    model.configure({})
    params = {}
    orbit = make_orbit_state()
    model.tick(1.0, orbit, params)
    assert 0x0302 in params  # cpu_load


def test_payload_basic_tick():
    from smo_simulator.models.payload_basic import PayloadBasicModel
    model = PayloadBasicModel()
    model.configure({})
    params = {}
    orbit = make_orbit_state()
    model.tick(1.0, orbit, params)
    assert 0x0600 in params  # pli_mode


def test_eps_failure_injection():
    from smo_simulator.models.eps_basic import EPSBasicModel
    model = EPSBasicModel()
    model.configure({})
    model.inject_failure("bus_short", 1.0)
    assert model._state.bus_short == True
    model.clear_failure("bus_short")
    assert model._state.bus_short == False


def test_eps_breakpoint_roundtrip():
    from smo_simulator.models.eps_basic import EPSBasicModel
    model = EPSBasicModel()
    model.configure({})
    model._state.bat_soc_pct = 42.0
    state = model.get_state()
    model2 = EPSBasicModel()
    model2.configure({})
    model2.set_state(state)
    assert model2._state.bat_soc_pct == 42.0


def test_tcs_setpoint_readback_defect3():
    """Test DEFECT 3 fix: Setpoint readback via 0x0330/0x0331.

    After commanding HEATER_SET_SETPOINT, operator can read back the new
    setpoint values via S20 parameter read (0x0330, 0x0331).
    """
    from smo_simulator.models.tcs_basic import TCSBasicModel
    model = TCSBasicModel()
    model.configure({})
    params = {}
    orbit = make_orbit_state()

    # Initial tick — should have default setpoints
    model.tick(1.0, orbit, params)
    assert params[0x0330] == 1.0  # Default battery ON setpoint
    assert params[0x0331] == 5.0  # Default OBC ON setpoint

    # Command new setpoints via set_setpoint
    model.handle_command({
        "command": "set_setpoint",
        "circuit": 0,  # Battery
        "on_temp": 3.0,
        "off_temp": 8.0
    })

    # Tick and verify new setpoint is reflected in params
    params = {}
    model.tick(1.0, orbit, params)
    assert params[0x0330] == 3.0, "Battery setpoint should be readable after command"

    # Command OBC setpoint
    model.handle_command({
        "command": "set_setpoint",
        "circuit": 1,  # OBC
        "on_temp": 8.0,
        "off_temp": 13.0
    })

    # Tick and verify
    params = {}
    model.tick(1.0, orbit, params)
    assert params[0x0331] == 8.0, "OBC setpoint should be readable after command"

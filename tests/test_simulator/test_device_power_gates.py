"""Tests for S2 device-access power gates and the AOCS GPS start-mode command.

These prove that S2 device on/off commands (previously dead flags whose
device_states the models never read) now have a real, observable effect on
subsystem telemetry, and that the new AOCS GPS start-mode S8 function (func 84)
reaches the model handler.
"""
from unittest.mock import MagicMock


def make_orbit_state(in_eclipse=False):
    state = MagicMock()
    state.in_eclipse = in_eclipse
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


def _make_aocs_nominal():
    from smo_simulator.models.aocs_basic import AOCSBasicModel
    model = AOCSBasicModel()
    model.configure({})
    # Put AOCS in a powered, sensor-active mode (NOMINAL_NADIR = 4).
    model.handle_command({"command": "set_mode", "mode": 4})
    return model


# ── AOCS device gates ──────────────────────────────────────────────────

def test_aocs_star_tracker_device_off_invalidates_validity():
    model = _make_aocs_nominal()
    orbit = make_orbit_state()
    # Warm up so trackers reach TRACKING.
    for _ in range(40):
        model.tick(1.0, orbit, {})
    s = model._state
    s.st_selected = 1
    # Baseline: selected ST powered → valid is achievable.
    model.tick(1.0, orbit, {})

    # Switch off star tracker 1 (device 0x0204) via S2 device access.
    assert model.set_device_state(0x0204, False) is True
    model.tick(1.0, orbit, {})
    assert s.st1_status == 0          # forced OFF
    assert s.st1_num_stars == 0
    assert s.st_valid is False        # selected tracker no longer valid

    # Re-power the device and re-issue the ST power-on command → recovers
    # (the camera boots, then reaches TRACKING again).
    model.set_device_state(0x0204, True)
    model.handle_command({"command": "st_power", "unit": 1, "on": True})
    for _ in range(40):
        model.tick(1.0, orbit, {})
    assert s.st1_status != 0


def test_aocs_sun_sensor_device_off_collapses_sun_vector():
    model = _make_aocs_nominal()
    orbit = make_orbit_state()
    model.tick(1.0, orbit, {})
    s = model._state

    model.set_device_state(0x020E, False)  # sun sensor array off
    model.tick(1.0, orbit, {})
    assert s.css_valid is False
    assert s.css_sun_x == 0.0 and s.css_sun_y == 0.0 and s.css_sun_z == 0.0


def test_aocs_magnetorquer_device_off_zeros_duty():
    model = _make_aocs_nominal()
    orbit = make_orbit_state()
    s = model._state
    s.mtq_x_duty = 0.7  # pretend a commanded dipole
    model.set_device_state(0x020B, False)  # MTQ X off
    model.tick(1.0, orbit, {})
    assert s.mtq_x_duty == 0.0


def test_aocs_reaction_wheel_device_off_marks_inactive():
    model = _make_aocs_nominal()
    orbit = make_orbit_state()
    s = model._state
    s.rw_speed[0] = 3000.0
    model.set_device_state(0x0200, False)  # RW0 off
    model.tick(1.0, orbit, {})
    assert s.active_wheels[0] is False
    assert s.rw_speed[0] == 0.0
    assert s.rw_current[0] == 0.0


def test_aocs_magnetometer_both_off_invalidates():
    model = _make_aocs_nominal()
    orbit = make_orbit_state()
    s = model._state
    model.tick(1.0, orbit, {})
    model.set_device_state(0x0209, False)  # Mag A off
    model.set_device_state(0x020A, False)  # Mag B off
    model.tick(1.0, orbit, {})
    assert s.mag_valid is False


# ── AOCS GPS start-mode command (S8 func 84) ───────────────────────────

def test_aocs_gps_set_start_mode_handler():
    model = _make_aocs_nominal()
    result = model.handle_command({"command": "gps_set_start_mode", "start_mode": 2})
    assert result["success"] is True
    assert model._state.gps_start_mode == 2  # HOT


def test_gps_start_mode_dispatch_func_84():
    """func_id 84 routed to the AOCS model sets gps_start_mode."""
    from smo_simulator.service_dispatch import ServiceDispatcher

    aocs = _make_aocs_nominal()
    engine = MagicMock()
    engine.subsystems = {"aocs": aocs}
    disp = ServiceDispatcher(engine)
    # S8.1: func_id 84 (0x54) + start_mode byte 0x01 (WARM)
    disp.dispatch(8, 1, bytes([84, 1]))
    assert aocs._state.gps_start_mode == 1  # WARM


# ── Payload device gates ───────────────────────────────────────────────

def test_payload_fpa_device_off_forces_off_mode():
    from smo_simulator.models.payload_basic import PayloadBasicModel
    model = PayloadBasicModel()
    model.configure({})
    s = model._state
    s.mode = 2          # IMAGING
    s.fpa_ready = True
    orbit = make_orbit_state()
    model.set_device_state(0x0600, False)  # FPA off
    model.tick(1.0, orbit, {})
    assert s.mode == 0
    assert s.fpa_ready is False


def test_payload_cooler_device_off_stops_cooler():
    from smo_simulator.models.payload_basic import PayloadBasicModel
    model = PayloadBasicModel()
    model.configure({})
    s = model._state
    s.cooler_active = True
    orbit = make_orbit_state()
    model.set_device_state(0x0601, False)  # FPA cooler off
    model.tick(1.0, orbit, {})
    assert s.cooler_active is False


def test_payload_shutter_device_off_closes_shutter():
    from smo_simulator.models.payload_basic import PayloadBasicModel
    model = PayloadBasicModel()
    model.configure({})
    s = model._state
    s.shutter_position = 1  # OPEN
    orbit = make_orbit_state()
    model.set_device_state(0x0603, False)  # shutter mechanism off
    model.tick(1.0, orbit, {})
    assert s.shutter_position == 0  # CLOSED


def test_payload_compression_device_off_collapses_ratio():
    from smo_simulator.models.payload_basic import PayloadBasicModel
    model = PayloadBasicModel()
    model.configure({})
    s = model._state
    orbit = make_orbit_state()
    model.set_device_state(0x0604, False)  # compression unit off
    model.tick(1.0, orbit, {})
    assert s.compression_ratio == 1.0


# ── OBDH device gates ──────────────────────────────────────────────────

def test_obdh_watchdog_device_off_disarms():
    from smo_simulator.models.obdh_basic import OBDHBasicModel
    model = OBDHBasicModel()
    model.configure({})
    s = model._state
    s.watchdog_armed = True
    orbit = make_orbit_state()
    model.set_device_state(0x0503, False)  # watchdog timer off
    model.tick(1.0, orbit, {})
    assert s.watchdog_armed is False


def test_obdh_obc_b_device_off_reports_off():
    from smo_simulator.models.obdh_basic import OBDHBasicModel, OBC_OFF
    model = OBDHBasicModel()
    model.configure({})
    s = model._state
    orbit = make_orbit_state()
    model.set_device_state(0x0501, False)  # OBC-B off
    model.tick(1.0, orbit, {})
    assert s.obc_b_status == OBC_OFF


def test_obdh_mass_memory_device_off_zeros_usage():
    from smo_simulator.models.obdh_basic import OBDHBasicModel
    model = OBDHBasicModel()
    model.configure({})
    s = model._state
    s.mmm_used_pct = 42.0
    orbit = make_orbit_state()
    model.set_device_state(0x0502, False)  # mass memory off
    model.tick(1.0, orbit, {})
    assert s.mmm_used_pct == 0.0


def test_obdh_can_bus_device_off_degrades_active_bus():
    from smo_simulator.models.obdh_basic import OBDHBasicModel, BUS_DEGRADED
    model = OBDHBasicModel()
    model.configure({})
    s = model._state
    s.active_bus = 0
    s.bus_a_status = 0  # BUS_OK
    orbit = make_orbit_state()
    model.set_device_state(0x0504, False)  # CAN bus interface off
    model.tick(1.0, orbit, {})
    assert s.bus_a_status == BUS_DEGRADED

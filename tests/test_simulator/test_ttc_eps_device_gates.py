"""Tests for the TT&C and EPS S2 device-access power gates.

These prove that the S2_TTC_* and S2_EPS_* device on/off commands — previously
dead flags whose ``device_states`` the models never consumed — now have a real,
observable effect on the model, while leaving the default (all-devices-ON)
behaviour identical to before.

Wired devices (see ttc_basic.py / eps_basic.py):
  TTC: 0x0400 Transponder A, 0x0401 Transponder B, 0x0402 Power amplifier,
       0x0403 LNA, 0x0404 Antenna drive.
  EPS: 0x0101 Solar-array drive, 0x010F Battery charge regulator.
       (0x0100 battery heater and 0x0102-0x0104 PDU buses are left decorative —
       see the audit report — to avoid double-control / inventing fake physics.)
"""
from unittest.mock import MagicMock

from smo_simulator.models.ttc_basic import TTCBasicModel
from smo_simulator.models.eps_basic import EPSBasicModel


# ── orbit-state fixtures ────────────────────────────────────────────────

def make_ttc_orbit(in_contact=True, range_km=1000.0, elevation=30.0):
    state = MagicMock()
    state.in_eclipse = False
    state.solar_beta_deg = 20.0
    state.lat_deg = 45.0
    state.lon_deg = 10.0
    state.alt_km = 500.0
    state.vel_x = 0.0
    state.vel_y = 7.5
    state.vel_z = 0.0
    state.in_contact = in_contact
    state.gs_elevation_deg = elevation
    state.gs_azimuth_deg = 90.0
    state.gs_range_km = range_km
    return state


def make_eps_orbit(in_eclipse=False, beta_deg=0.0):
    state = MagicMock()
    state.in_eclipse = in_eclipse
    state.solar_beta_deg = beta_deg
    state.lat_deg = 0.0
    state.lon_deg = 0.0
    state.alt_km = 500.0
    state.vel_x = 0.0
    state.vel_y = 7.5
    state.vel_z = 0.0
    state.in_contact = False
    state.gs_elevation_deg = -10.0
    state.gs_azimuth_deg = 0.0
    state.gs_range_km = 2000.0
    return state


def _make_ttc(antenna_deployed=True):
    model = TTCBasicModel()
    model.configure({})
    if antenna_deployed:
        model._state.antenna_deployed = True
        model._state.antenna_deployment_sensor = 2
    return model


def _lock_ttc(model, orbit, ticks=14):
    """Tick the TTC model long enough to reach frame sync."""
    for _ in range(ticks):
        model.tick(1.0, orbit, {})


# ── TTC: 0x0402 Power amplifier ─────────────────────────────────────────

def test_ttc_pa_device_default_on_transmits():
    model = _make_ttc()
    orbit = make_ttc_orbit()
    _lock_ttc(model, orbit)
    s = model._state
    assert s.frame_sync is True
    assert s.pa_on is True
    assert s.tx_fwd_power > 0.0  # downlink active


def test_ttc_pa_device_off_no_downlink():
    model = _make_ttc()
    orbit = make_ttc_orbit()
    _lock_ttc(model, orbit)
    assert model.set_device_state(0x0402, True) is True  # valid device id

    model.set_device_state(0x0402, False)  # power amplifier OFF
    model.tick(1.0, orbit, {})
    s = model._state
    assert s.pa_on is False
    assert s.tx_fwd_power == 0.0  # no TX / downlink


def test_ttc_pa_device_off_overrides_cmd_channel():
    """The dedicated-command-channel override keeps the PA on regardless of OBC
    state — but must not re-power a PA whose device is switched off."""
    model = _make_ttc()
    orbit = make_ttc_orbit()
    model.handle_command({"command": "cmd_channel_start"})  # forces pa_on
    model.set_device_state(0x0402, False)
    model.tick(1.0, orbit, {})
    assert model._state.pa_on is False
    assert model._state.tx_fwd_power == 0.0


def test_ttc_pa_device_on_resumes_normal_pa_logic():
    model = _make_ttc()
    orbit = make_ttc_orbit()
    model.set_device_state(0x0402, False)
    model.tick(1.0, orbit, {})
    assert model._state.pa_on is False
    # Re-power the PA device and command pa_on → normal TX resumes.
    model.set_device_state(0x0402, True)
    model.handle_command({"command": "pa_on"})
    _lock_ttc(model, orbit)
    assert model._state.pa_on is True
    assert model._state.tx_fwd_power > 0.0


# ── TTC: 0x0400 / 0x0401 Transponders ───────────────────────────────────

def test_ttc_transponder_a_off_kills_primary_rx_tx():
    model = _make_ttc()
    orbit = make_ttc_orbit()
    _lock_ttc(model, orbit)
    assert model._state.frame_sync is True  # baseline locked on primary

    model._state.mode = 0  # primary selected
    model.set_device_state(0x0400, False)  # Transponder A OFF
    _lock_ttc(model, orbit)
    s = model._state
    # Primary transponder unavailable → no contact, no lock, no TX.
    assert s.frame_sync is False
    assert s.carrier_lock is False
    assert s.tx_fwd_power == 0.0
    # Failure flag must NOT be set — this is a device gate, not a failure.
    assert s.primary_failed is False


def test_ttc_transponder_b_off_kills_redundant_only():
    model = _make_ttc()
    orbit = make_ttc_orbit()
    model._state.mode = 1  # redundant selected
    model.set_device_state(0x0401, False)  # Transponder B OFF
    _lock_ttc(model, orbit)
    s = model._state
    assert s.frame_sync is False
    assert s.redundant_failed is False
    # Switching back to primary (device A still on) recovers.
    model._state.mode = 0
    _lock_ttc(model, orbit)
    assert model._state.frame_sync is True


# ── TTC: 0x0403 LNA ─────────────────────────────────────────────────────

def test_ttc_lna_off_no_uplink_lock():
    model = _make_ttc()
    orbit = make_ttc_orbit()
    _lock_ttc(model, orbit)
    assert model._state.frame_sync is True

    model.set_device_state(0x0403, False)  # LNA OFF → receiver dead
    _lock_ttc(model, orbit)
    s = model._state
    assert s.carrier_lock is False
    assert s.bit_sync is False
    assert s.frame_sync is False

    # Re-power the LNA → lock re-acquires.
    model.set_device_state(0x0403, True)
    _lock_ttc(model, orbit)
    assert model._state.frame_sync is True


# ── TTC: 0x0404 Antenna drive ───────────────────────────────────────────

def test_ttc_antenna_drive_off_blocks_deploy():
    model = _make_ttc(antenna_deployed=False)
    model._state.antenna_deployed = False
    model.set_device_state(0x0404, False)  # antenna drive OFF
    result = model.handle_command({"command": "deploy_antennas"})
    assert result["success"] is False
    assert model._state.antenna_deployed is False


def test_ttc_antenna_drive_on_allows_deploy():
    model = _make_ttc(antenna_deployed=False)
    model._state.antenna_deployed = False
    assert model._state.device_states.get(0x0404, True) is True  # default ON
    result = model.handle_command({"command": "deploy_antennas"})
    assert result["success"] is True
    assert model._state.antenna_deployed is True


def test_ttc_antenna_drive_off_does_not_undeploy():
    """Switching the drive off must not retract an already-deployed antenna."""
    model = _make_ttc(antenna_deployed=True)
    assert model._state.antenna_deployed is True
    model.set_device_state(0x0404, False)
    orbit = make_ttc_orbit()
    model.tick(1.0, orbit, {})
    assert model._state.antenna_deployed is True  # still deployed


# ── EPS: 0x010F Battery charge regulator ────────────────────────────────

def _make_eps():
    model = EPSBasicModel()
    model.configure({})
    return model


def test_eps_charge_regulator_default_on_charges_in_sunlight():
    model = _make_eps()
    orbit = make_eps_orbit(in_eclipse=False, beta_deg=0.0)
    s = model._state
    s.bat_soc_pct = 50.0
    # Provide a strong sun vector via shared params so generation is high.
    sp = {0x0245: 1.0, 0x0246: 0.0, 0x0247: 0.0}
    start_soc = s.bat_soc_pct
    for _ in range(30):
        model.tick(1.0, orbit, sp)
    assert s.power_gen_w > s.power_cons_w  # net positive in sunlight
    assert s.bat_soc_pct > start_soc       # SoC rises (charging)


def test_eps_charge_regulator_off_blocks_charging():
    model = _make_eps()
    orbit = make_eps_orbit(in_eclipse=False, beta_deg=0.0)
    s = model._state
    s.bat_soc_pct = 50.0
    sp = {0x0245: 1.0, 0x0246: 0.0, 0x0247: 0.0}
    model.set_device_state(0x010F, False)  # charge regulator OFF
    start_soc = s.bat_soc_pct
    for _ in range(30):
        model.tick(1.0, orbit, sp)
    # Array still generates, but SoC must not rise (battery can't take charge).
    assert s.power_gen_w > 0.0
    assert s.bat_soc_pct <= start_soc
    assert s.actual_charge_current_a == 0.0


# ── EPS: 0x0101 Solar-array drive ───────────────────────────────────────

def test_eps_solar_array_drive_default_on_generates():
    model = _make_eps()
    orbit = make_eps_orbit(in_eclipse=False, beta_deg=0.0)
    sp = {0x0245: 1.0, 0x0246: 0.0, 0x0247: 0.0}
    for _ in range(3):
        model.tick(1.0, orbit, sp)
    assert model._state.power_gen_w > 0.0


def test_eps_solar_array_drive_off_zeros_generation():
    model = _make_eps()
    orbit = make_eps_orbit(in_eclipse=False, beta_deg=0.0)
    sp = {0x0245: 1.0, 0x0246: 0.0, 0x0247: 0.0}
    model.set_device_state(0x0101, False)  # solar array drive OFF
    for _ in range(3):
        model.tick(1.0, orbit, sp)
    s = model._state
    assert s.power_gen_w == 0.0
    assert s.sa_a_current == 0.0
    assert s.sa_b_current == 0.0


def test_eps_solar_array_drive_recovers_when_repowered():
    model = _make_eps()
    orbit = make_eps_orbit(in_eclipse=False, beta_deg=0.0)
    sp = {0x0245: 1.0, 0x0246: 0.0, 0x0247: 0.0}
    model.set_device_state(0x0101, False)
    model.tick(1.0, orbit, sp)
    assert model._state.power_gen_w == 0.0
    model.set_device_state(0x0101, True)
    for _ in range(3):
        model.tick(1.0, orbit, sp)
    assert model._state.power_gen_w > 0.0


# ── EPS: decorative devices stay no-ops (default behaviour unchanged) ────

def test_eps_decorative_devices_do_not_break_power_balance():
    """The battery heater (0x0100) and PDU buses (0x0102-0x0104) are left
    decorative; toggling them must not alter the power balance."""
    model = _make_eps()
    orbit = make_eps_orbit(in_eclipse=False, beta_deg=0.0)
    sp = {0x0245: 1.0, 0x0246: 0.0, 0x0247: 0.0}
    for _ in range(5):
        model.tick(1.0, orbit, sp)
    gen_before = model._state.power_gen_w
    for dev in (0x0100, 0x0102, 0x0103, 0x0104):
        model.set_device_state(dev, False)
    for _ in range(5):
        model.tick(1.0, orbit, sp)
    # Generation unaffected by the decorative device toggles.
    assert model._state.power_gen_w > 0.0
    assert abs(model._state.power_gen_w - gen_before) < gen_before * 0.5

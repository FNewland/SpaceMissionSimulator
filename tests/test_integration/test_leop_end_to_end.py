"""Integration tests for full LEOP sequence.

Covers:
  - Separation -> timer -> power on -> beacon -> boot app
  - Sequential power-on of subsystems
  - ADCS commissioning sequence
  - Transition to nominal operations
"""
import math

import pytest
from unittest.mock import MagicMock

from smo_simulator.models.eps_basic import EPSBasicModel, POWER_LINE_SWITCHABLE
from smo_simulator.models.obdh_basic import (
    OBDHBasicModel, SW_BOOTLOADER, SW_APPLICATION,
)
from smo_simulator.models.ttc_basic import TTCBasicModel
from smo_simulator.models.aocs_basic import AOCSBasicModel, MODE_DETUMBLE, MODE_NOMINAL
from smo_simulator.models.tcs_basic import TCSBasicModel
from smo_simulator.models.payload_basic import PayloadBasicModel


def make_orbit_state(in_eclipse=False, in_contact=False, beta=20.0):
    state = MagicMock()
    state.in_eclipse = in_eclipse
    state.solar_beta_deg = beta
    state.lat_deg = 45.0
    state.lon_deg = 10.0
    state.alt_km = 500.0
    state.vel_x = 0.0
    state.vel_y = 7.5
    state.vel_z = 0.0
    state.in_contact = in_contact
    state.gs_elevation_deg = -10.0
    state.gs_azimuth_deg = 0.0
    state.gs_range_km = 2000.0
    return state


class TestSeparationSequence:
    """Test the separation to initial power-on sequence."""

    def test_separation_turns_off_switchable_lines(self):
        """At separation, all switchable EPS lines should be OFF."""
        eps = EPSBasicModel()
        eps.configure({})
        # Simulate separation
        for line_name in eps._state.power_lines:
            if POWER_LINE_SWITCHABLE.get(line_name, False):
                eps._state.power_lines[line_name] = False
        for line_name, switchable in POWER_LINE_SWITCHABLE.items():
            if switchable:
                assert eps._state.power_lines[line_name] is False

    def test_unswitchable_lines_remain_on(self):
        """OBC and RX power lines are unswitchable and stay on."""
        eps = EPSBasicModel()
        eps.configure({})
        assert POWER_LINE_SWITCHABLE["obc"] is False
        assert POWER_LINE_SWITCHABLE["ttc_rx"] is False
        assert eps._state.power_lines["obc"] is True
        assert eps._state.power_lines["ttc_rx"] is True

    def test_obdh_enters_bootloader_after_power_on(self):
        """After timer expires, OBDH should enter bootloader mode."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        obdh._state.sw_image = SW_BOOTLOADER
        assert obdh._state.sw_image == SW_BOOTLOADER

    def test_obdh_cpu_load_low_in_bootloader(self):
        """In bootloader mode, CPU load should be low (~15%)."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        obdh._state.sw_image = SW_BOOTLOADER
        params = {}
        orbit = make_orbit_state()
        obdh.tick(1.0, orbit, params)
        assert params[0x0302] < 25.0  # CPU load under 25% in bootloader


class TestBootloaderToApplication:
    """Test transitioning from bootloader to application software."""

    def test_boot_app_command(self):
        """obc_boot_app should initiate boot process."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        obdh._state.sw_image = SW_BOOTLOADER
        result = obdh.handle_command({"command": "obc_boot_app"})
        assert result["success"] is True
        assert obdh._state.boot_app_pending is True
        assert obdh._state.boot_app_timer == pytest.approx(10.0)

    def test_boot_app_completes_after_timer(self):
        """After 10s boot timer, sw_image should be APPLICATION."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        obdh._state.sw_image = SW_BOOTLOADER
        obdh.handle_command({"command": "obc_boot_app"})
        params = {}
        orbit = make_orbit_state()
        # Tick 11 seconds (1s each)
        for _ in range(11):
            obdh.tick(1.0, orbit, params)
        assert obdh._state.sw_image == SW_APPLICATION
        assert obdh._state.mode == 0  # nominal after boot

    def test_corrupt_image_stays_in_bootloader(self):
        """If boot image is corrupt, should stay in bootloader."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        obdh._state.sw_image = SW_BOOTLOADER
        obdh.inject_failure("boot_image_corrupt")
        obdh.handle_command({"command": "obc_boot_app"})
        params = {}
        orbit = make_orbit_state()
        for _ in range(15):
            obdh.tick(1.0, orbit, params)
        assert obdh._state.sw_image == SW_BOOTLOADER

    def test_boot_inhibit_prevents_auto_boot(self):
        """With boot_inhibit=True, reboot should not auto-boot to app."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        obdh.handle_command({"command": "obc_boot_inhibit", "inhibit": True})
        assert obdh._state.boot_inhibit is True
        obdh.handle_command({"command": "obc_reboot"})
        assert obdh._state.boot_app_pending is False


class TestTTCBeaconMode:
    """Test TTC beacon mode during early operations."""

    def test_beacon_mode_low_rate(self):
        """In beacon mode, TTC is pinned to the BEACON tier (1 kbps)."""
        ttc = TTCBasicModel()
        ttc.configure({})
        ttc.handle_command({"command": "set_beacon_mode", "on": True})
        params = {}
        orbit = make_orbit_state()
        ttc.tick(1.0, orbit, params)
        # Tier renamed BEACON -> SAFE (2026-08-19): "beacon" now always means
        # the UHF whip; the S-band survival rung is SAFE (1 kbps).
        assert ttc.tier_name(ttc._state.tm_tier) == "SAFE"
        assert ttc._state.tm_data_rate == 1000

    def test_antenna_deployment_increases_rate(self):
        """Leaving beacon mode moves the spacecraft from the 1 kbps BEACON
        lifeline to the LOW (16 kbps) routine-operations rate.

        Deploying the antenna does NOT by itself unlock the HIGH tier: that
        needs DOWNLINK/dump mode and elevation >= 10 deg (defect #85).
        """
        ttc = TTCBasicModel()
        ttc.configure({})
        ttc.handle_command({"command": "set_beacon_mode", "on": True})
        ttc.tick(1.0, make_orbit_state(), {})
        assert ttc._state.tm_data_rate == 1000

        ttc.handle_command({"command": "deploy_antennas"})
        ttc.handle_command({"command": "set_beacon_mode", "on": False})
        params = {}
        ttc.tick(1.0, make_orbit_state(), params)
        assert ttc.tier_name(ttc._state.tm_tier) == "LOW"
        assert ttc._state.tm_data_rate == 16000


class TestSequentialPowerOn:
    """Test sequential power-on of subsystems."""

    def test_eps_ticks_in_early_phase(self):
        """EPS should tick in phase >= 2 (early operations)."""
        eps = EPSBasicModel()
        eps.configure({})
        params = {}
        orbit = make_orbit_state()
        eps.tick(1.0, orbit, params)
        assert 0x0101 in params  # bat_soc

    def test_aocs_initializes_in_detumble(self):
        """AOCS should start in detumble mode during LEOP."""
        aocs = AOCSBasicModel()
        aocs.configure({})
        aocs.handle_command({"command": "set_mode", "mode": MODE_DETUMBLE})
        assert aocs._state.mode == MODE_DETUMBLE

    def test_aocs_detumble_reduces_rates(self):
        """DETUMBLE (magnetometer-driven B-dot) damps the RATE MAGNITUDE.

        The law is m = -k*Bdot with Bdot a first difference of the MEASURED
        field, giving tau = m x B = -k*|B|^2*w_perp. Two consequences drive
        the numbers below:

        * Only the component of w PERPENDICULAR to B is damped at any instant,
          and the field direction sweeps through the body as the spacecraft
          tumbles. Energy therefore moves BETWEEN axes while the total falls,
          so a per-axis assertion is not a statement about the law: measured
          from (1.0, 0.8, 0.5) deg/s, |rate_roll| is 1.05 deg/s at t = 50 s
          (up from 1.0) while the magnitude has already fallen 1.37 -> 1.28.
          The pre-B-dot implementation fed back the TRUTH body rate, which
          damped every axis directly, which is why the old 50 s per-axis
          expectation held.
        * The rate time constant is tau = I/(k*|B|^2) = 0.18/(5e5*(3e-5)^2)
          ~ 400 s, and the 1.6 A.m^2 rods saturate above ~1 deg/s, so
          convergence takes 10^3 s, not 50 s. Measured on this model from
          |w0| = 1.37 deg/s: 1.13 at 100 s, 0.95 at 200 s, 0.63 at 600 s,
          0.55 at 900 s (and below the 0.5 deg/s DETUMBLE exit guard at
          t = 1166 s). From a 14 deg/s separation tumble it takes ~3.5e3 s.
          The 0.70 deg/s threshold at 900 s leaves ~28 % margin on 0.55.
        """
        def rate_mag(model):
            s = model._state
            return math.sqrt(s.rate_roll ** 2 + s.rate_pitch ** 2
                             + s.rate_yaw ** 2)

        aocs = AOCSBasicModel()
        aocs.configure({})
        aocs._state.mode = MODE_DETUMBLE
        aocs._state.rate_roll = 1.0
        aocs._state.rate_pitch = 0.8
        aocs._state.rate_yaw = 0.5
        params = {}
        orbit = make_orbit_state()
        initial = rate_mag(aocs)
        at_100 = None
        for t in range(900):
            aocs.tick(1.0, orbit, params)
            if t == 99:
                at_100 = rate_mag(aocs)
        final = rate_mag(aocs)

        assert final < 0.70, (
            f"B-dot did not damp the tumble: {initial:.2f} -> {final:.2f} "
            f"deg/s in 900 s")
        assert final < at_100 < initial, (
            f"rate magnitude must fall throughout: {initial:.2f} -> "
            f"{at_100:.2f} (100 s) -> {final:.2f} (900 s)")

        # Control case: B-dot is a MAGNETOMETER law. With the magnetometer
        # failed there is no damping at all — the mode's real failure
        # signature, and the guard against a "detumble" that secretly reads
        # the truth rate.
        blind = AOCSBasicModel()
        blind.configure({})
        blind._state.mode = MODE_DETUMBLE
        blind._state.mag_failed = True
        blind._state.rate_roll = 1.0
        blind._state.rate_pitch = 0.8
        blind._state.rate_yaw = 0.5
        for _ in range(900):
            blind.tick(1.0, orbit, {})
        assert rate_mag(blind) > 0.9 * initial, (
            "rates decayed with the magnetometer failed — DETUMBLE is not "
            "using the magnetometer")

    def test_tcs_heater_operates_independently(self):
        """TCS battery heater should operate regardless of LEOP phase."""
        tcs = TCSBasicModel()
        tcs.configure({})
        tcs._state.temp_battery = -10.0  # Cold
        tcs._state.htr_battery_manual = False
        tcs._thermostat_control("battery", tcs._state.temp_battery)
        assert tcs._state.htr_battery is True


class TestADCSCommissioning:
    """Test AOCS commissioning sequence."""

    def test_star_tracker_boot(self):
        """Star tracker boot should take 60 seconds."""
        aocs = AOCSBasicModel()
        aocs.configure({})
        aocs.handle_command({"command": "st_power", "unit": 1, "on": True})
        assert aocs._state.st1_status == 1  # BOOTING
        params = {}
        orbit = make_orbit_state()
        # Tick 61 seconds
        for _ in range(61):
            aocs.tick(1.0, orbit, params)
        assert aocs._state.st1_status == 2  # TRACKING

    def test_dual_tracker_commissioning(self):
        """Both star trackers can be booted and selected."""
        aocs = AOCSBasicModel()
        aocs.configure({})
        # Boot ST1
        aocs.handle_command({"command": "st_power", "unit": 1, "on": True})
        params = {}
        orbit = make_orbit_state()
        for _ in range(65):
            aocs.tick(1.0, orbit, params)
        # Boot ST2
        aocs.handle_command({"command": "st_power", "unit": 2, "on": True})
        for _ in range(65):
            aocs.tick(1.0, orbit, params)
        # Select ST2
        result = aocs.handle_command({"command": "st_select", "unit": 2})
        assert result["success"] is True
        assert aocs._state.st_selected == 2


class TestTransitionToNominal:
    """Test transition from commissioning to nominal operations."""

    def test_aocs_transition_to_nominal(self):
        """AOCS can be set to nominal mode."""
        aocs = AOCSBasicModel()
        aocs.configure({})
        aocs.handle_command({"command": "set_mode", "mode": MODE_NOMINAL})
        assert aocs._state.mode == MODE_NOMINAL

    def test_payload_standby_mode(self):
        """Payload should be commandable to standby mode."""
        pl = PayloadBasicModel()
        pl.configure({})
        result = pl.handle_command({"command": "set_mode", "mode": 1})
        assert result["success"] is True
        assert pl._state.mode == 1

    def test_all_subsystems_tick_in_nominal(self):
        """All subsystems should tick successfully in nominal phase."""
        subsystems = {
            "eps": EPSBasicModel(),
            "obdh": OBDHBasicModel(),
            "ttc": TTCBasicModel(),
            "aocs": AOCSBasicModel(),
            "tcs": TCSBasicModel(),
            "payload": PayloadBasicModel(),
        }
        for model in subsystems.values():
            model.configure({})

        params = {}
        orbit = make_orbit_state()
        for name, model in subsystems.items():
            model.tick(1.0, orbit, params)

        # Verify key params exist
        assert 0x0101 in params  # EPS bat_soc
        assert 0x0302 in params  # OBDH cpu_load
        assert 0x020F in params  # AOCS mode
        assert 0x0408 in params  # TCS fpa temp
        assert 0x0600 in params  # Payload mode

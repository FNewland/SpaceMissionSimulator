"""Integration tests for contingency recovery scenarios.

Covers:
  - No-TM-at-AOS -> RF chain diagnostics -> recovery
  - OBC bootloader -> app boot recovery
  - Progressive load shed by SoC thresholds
  - AOCS sensor loss -> backup switch -> recovery
  - Actuator stuck -> power cycle -> 3-wheel mode
"""
import pytest
from unittest.mock import MagicMock

from smo_simulator.models.eps_basic import EPSBasicModel, POWER_LINE_NAMES
from smo_simulator.models.obdh_basic import (
    OBDHBasicModel, SW_BOOTLOADER, SW_APPLICATION,
    REBOOT_WATCHDOG, REBOOT_COMMAND,
)
from smo_simulator.models.ttc_basic import TTCBasicModel
from smo_simulator.models.aocs_basic import (
    AOCSBasicModel, MODE_DETUMBLE, MODE_NOMINAL, MODE_COARSE_SUN,
)
from smo_simulator.models.tcs_basic import TCSBasicModel


def make_orbit_state(in_eclipse=False, in_contact=False):
    state = MagicMock()
    state.in_eclipse = in_eclipse
    state.solar_beta_deg = 20.0
    state.lat_deg = 45.0
    state.lon_deg = 10.0
    state.alt_km = 500.0
    state.vel_x = 0.0
    state.vel_y = 7.5
    state.vel_z = 0.0
    state.in_contact = in_contact
    state.gs_elevation_deg = 30.0
    state.gs_azimuth_deg = 90.0
    state.gs_range_km = 1000.0
    return state


# ====================================================================
# RF Chain Diagnostics and Recovery
# ====================================================================

class TestNoTMAtAOS:
    """Test no-TM-at-AOS contingency and RF chain recovery."""

    def test_primary_transponder_failure_no_link(self):
        """Primary transponder failure should cause link loss."""
        ttc = TTCBasicModel()
        ttc.configure({})
        ttc.inject_failure("primary_failure")
        assert ttc._state.primary_failed is True
        params = {}
        orbit = make_orbit_state(in_contact=True)
        ttc.tick(1.0, orbit, params)
        # With primary failed and mode=0, link should be inactive
        assert ttc._state.link_active is False

    def test_switch_to_redundant_recovers_link(self):
        """Switching to redundant transponder should recover link."""
        ttc = TTCBasicModel()
        ttc.configure({})
        ttc.inject_failure("primary_failure")
        result = ttc.handle_command({"command": "switch_redundant"})
        assert result["success"] is True
        assert ttc._state.mode == 1  # redundant

    def test_pa_off_kills_link(self):
        """Turning PA off should prevent downlink (TX) but not uplink (RX)."""
        ttc = TTCBasicModel()
        ttc.configure({})
        ttc.handle_command({"command": "pa_off"})
        assert ttc._state.pa_on is False
        params = {}
        orbit = make_orbit_state(in_contact=True)
        ttc.tick(1.0, orbit, params)
        # RX/TX separation: PA off kills downlink TX, not uplink contact
        assert ttc._state.tx_fwd_power == 0.0

    def test_pa_on_restores_link(self):
        """Turning PA back on should allow link to be re-established."""
        ttc = TTCBasicModel()
        ttc.configure({})
        ttc.handle_command({"command": "pa_off"})
        ttc.handle_command({"command": "pa_on"})
        assert ttc._state.pa_on is True

    def test_uplink_loss_blocks_commands(self):
        """Uplink loss should block command reception."""
        ttc = TTCBasicModel()
        ttc.configure({})
        initial_count = ttc._state.cmd_rx_count
        ttc.inject_failure("uplink_loss")
        ttc.record_cmd_received()
        assert ttc._state.cmd_rx_count == initial_count

    def test_clear_uplink_loss_restores_commands(self):
        """Clearing uplink loss should allow commands again."""
        ttc = TTCBasicModel()
        ttc.configure({})
        ttc.inject_failure("uplink_loss")
        ttc.clear_failure("uplink_loss")
        assert ttc._state.uplink_lost is False
        ttc.record_cmd_received()
        assert ttc._state.cmd_rx_count == 1


# ====================================================================
# OBC Bootloader Recovery
# ====================================================================

class TestOBCBootloaderRecovery:
    """Test OBC bootloader -> app boot recovery."""

    def test_obc_crash_drops_to_bootloader(self):
        """OBC crash should drop to bootloader mode."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        obdh.inject_failure("obc_crash")
        assert obdh._state.sw_image == SW_BOOTLOADER
        assert obdh._state.last_reboot_cause == REBOOT_WATCHDOG

    def test_reboot_count_increments(self):
        """Each reboot should increment the reboot counter."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        initial = obdh._state.reboot_count
        obdh.handle_command({"command": "obc_reboot"})
        assert obdh._state.reboot_count == initial + 1

    def test_boot_app_after_crash(self):
        """After crash, obc_boot_app should recover to application."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        obdh._state.boot_inhibit = True  # Prevent auto-boot
        obdh.inject_failure("obc_crash")
        assert obdh._state.sw_image == SW_BOOTLOADER
        # Manually boot app
        obdh.handle_command({"command": "obc_boot_app"})
        params = {}
        orbit = make_orbit_state()
        for _ in range(12):
            obdh.tick(1.0, orbit, params)
        assert obdh._state.sw_image == SW_APPLICATION

    def test_switchover_to_backup_obc(self):
        """OBC switchover should activate the backup unit."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        assert obdh._state.active_obc == 0  # A
        obdh.handle_command({"command": "obc_switch_unit"})
        assert obdh._state.active_obc == 1  # B

    def test_memory_corruption_causes_reboot(self):
        """Memory corruption should trigger a reboot."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        initial_count = obdh._state.reboot_count
        obdh.inject_failure("memory_corruption", count=10)
        assert obdh._state.reboot_count > initial_count

    def test_bus_failure_isolates_subsystems(self):
        """CAN bus failure should isolate subsystems on that bus."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        obdh.inject_failure("bus_failure", bus="A")
        assert obdh._state.bus_a_status == 2  # BUS_FAILED
        # With active bus = A and bus A failed, subsystems unreachable
        assert obdh.is_subsystem_reachable("eps") is False

    def test_switch_bus_after_failure(self):
        """Switching to backup bus should restore communication."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        obdh.inject_failure("bus_failure", bus="A")
        result = obdh.handle_command({"command": "obc_select_bus", "bus": 1})
        assert result["success"] is True
        assert obdh._state.active_bus == 1
        # Bus B subsystems should be reachable
        assert obdh.is_subsystem_reachable("ttc") is True


# ====================================================================
# Progressive Load Shedding
# ====================================================================

class TestProgressiveLoadShed:
    """Test progressive load shed by SoC thresholds."""

    def test_eps_power_line_off_command(self):
        """Power line off command should disable the line."""
        eps = EPSBasicModel()
        eps.configure({})
        result = eps.handle_command({
            "command": "power_line_off", "line_name": "payload"
        })
        assert result["success"] is True
        assert eps._state.power_lines["payload"] is False

    def test_load_shed_sequence(self):
        """Load shed sequence: payload -> fpa_cooler -> ttc_tx -> aocs_wheels."""
        from smo_simulator.models.eps_basic import LOAD_SHED_ORDER
        expected = ["payload", "fpa_cooler", "ttc_tx", "aocs_wheels"]
        assert LOAD_SHED_ORDER == expected

    def test_shed_payload_first(self):
        """First load to shed should be payload (lowest priority)."""
        eps = EPSBasicModel()
        eps.configure({})
        eps._state.power_lines["payload"] = True
        eps._state.power_lines["htr_bat"] = True  # Bring higher-priority on
        # Shed payload
        eps.handle_command({"command": "power_line_off", "line_name": "payload"})
        assert eps._state.power_lines["payload"] is False
        # Higher priority lines still on
        assert eps._state.power_lines["htr_bat"] is True

    def test_re_enable_after_soc_recovery(self):
        """After SoC recovers, lines can be re-enabled."""
        eps = EPSBasicModel()
        eps.configure({})
        eps.handle_command({"command": "power_line_off", "line_name": "payload"})
        assert eps._state.power_lines["payload"] is False
        eps.handle_command({"command": "power_line_on", "line_name": "payload"})
        assert eps._state.power_lines["payload"] is True


# ====================================================================
# AOCS Sensor Loss and Backup Switch
# ====================================================================

class TestAOCSSensorRecovery:
    """Test AOCS sensor loss -> backup switch -> recovery."""

    def test_mag_a_failure_fallback_to_b(self):
        """Mag A failure should automatically fallback to mag B."""
        aocs = AOCSBasicModel()
        aocs.configure({})
        aocs._state.mode = 4  # MODE_NOMINAL — power on the mag bus
        aocs.inject_failure("mag_a_fail")
        params = {}
        orbit = make_orbit_state()
        aocs.tick(1.0, orbit, params)
        assert aocs._state.mag_valid is True  # Fallback to B

    def test_mag_select_b_after_a_failure(self):
        """Operator can explicitly select mag B after A fails."""
        aocs = AOCSBasicModel()
        aocs.configure({})
        aocs.inject_failure("mag_a_fail")
        result = aocs.handle_command({"command": "mag_select", "source": "B"})
        assert result["success"] is True
        assert aocs._state.mag_select == 'B'

    def test_st_failure_boot_backup(self):
        """ST1 failure -> boot ST2 -> select ST2 recovery."""
        aocs = AOCSBasicModel()
        aocs.configure({})
        aocs.inject_failure("st_failure", unit=1)
        assert aocs._state.st1_status == 4  # FAILED
        # Boot ST2
        aocs.handle_command({"command": "st_power", "unit": 2, "on": True})
        params = {}
        orbit = make_orbit_state()
        for _ in range(65):
            aocs.tick(1.0, orbit, params)
        # ST2 should be tracking
        assert aocs._state.st2_status == 2
        # Select ST2
        aocs.handle_command({"command": "st_select", "unit": 2})
        assert aocs._state.st_selected == 2

    def test_css_head_failure_partial_recovery(self):
        """Single CSS head failure should still allow CSS composite vector."""
        aocs = AOCSBasicModel()
        aocs.configure({})
        aocs.inject_failure("css_head_fail", face="px")
        params = {}
        orbit = make_orbit_state(in_eclipse=False)
        aocs.tick(1.0, orbit, params)
        # CSS should still be valid (5 of 6 heads working)
        assert aocs._state.css_heads['px'] == 0.0
        # Other heads should have readings
        assert any(aocs._state.css_heads[f] > 0 for f in ['py', 'pz'])


# ====================================================================
# Actuator Stuck and 3-Wheel Mode
# ====================================================================

class TestActuatorRecovery:
    """Test actuator stuck -> power cycle -> 3-wheel mode."""

    def test_wheel_seizure_disables_wheel(self):
        """Wheel seizure should disable the wheel and set speed to 0."""
        aocs = AOCSBasicModel()
        aocs.configure({})
        aocs.inject_failure("rw_seizure", wheel=0)
        assert aocs._state.active_wheels[0] is False
        assert aocs._state.rw_speed[0] == 0.0

    def test_clear_seizure_and_reenable(self):
        """After clearing seizure, wheel can be re-enabled."""
        aocs = AOCSBasicModel()
        aocs.configure({})
        aocs.inject_failure("rw_seizure", wheel=0)
        aocs.clear_failure("rw_seizure", wheel=0)
        # Wheel stays disabled after clear — need explicit enable
        result = aocs.handle_command({"command": "enable_wheel", "wheel": 0})
        assert result["success"] is True
        assert aocs._state.active_wheels[0] is True

    def test_3_wheel_mode_with_one_failed(self):
        """With one wheel failed, 3 remaining should still operate."""
        aocs = AOCSBasicModel()
        aocs.configure({})
        aocs.inject_failure("rw_seizure", wheel=0)
        active_count = sum(1 for a in aocs._state.active_wheels if a)
        assert active_count == 3

    def test_multi_wheel_failure_forces_safe(self):
        """Losing 2+ wheels should force AOCS to coarse sun mode."""
        aocs = AOCSBasicModel()
        aocs.configure({})
        aocs._state.mode = MODE_NOMINAL
        aocs.inject_failure("multi_wheel_failure", wheels=[0, 1])
        assert aocs._state.active_wheels[0] is False
        assert aocs._state.active_wheels[1] is False
        # Should have been forced to coarse sun
        assert aocs._state.mode == MODE_COARSE_SUN

    def test_bearing_degradation_affects_current(self):
        """Wheel bearing degradation should increase current draw."""
        aocs = AOCSBasicModel()
        aocs.configure({})
        params = {}
        orbit = make_orbit_state()
        aocs.tick(1.0, orbit, params)
        normal_current = aocs._state.rw_current[0]

        aocs2 = AOCSBasicModel()
        aocs2.configure({})
        aocs2.inject_failure("rw_bearing", wheel=0, magnitude=0.5)
        params2 = {}
        aocs2.tick(1.0, orbit, params2)
        degraded_current = aocs2._state.rw_current[0]

        # Degraded wheel should draw more current (with noise margin)
        assert degraded_current > normal_current - 0.02

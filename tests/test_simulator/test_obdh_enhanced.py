"""Tests for the enhanced OBDH model — dual OBC, boot loader / application
software image, dual CAN bus with failure isolation, buffer management,
and comprehensive failure injection."""
import pytest
from unittest.mock import MagicMock

from smo_simulator.models.obdh_basic import (
    OBDHBasicModel, SW_BOOTLOADER, SW_APPLICATION,
    BUS_OK, BUS_FAILED, REBOOT_COMMAND, REBOOT_WATCHDOG,
    REBOOT_SWITCHOVER, REBOOT_MEMORY_ERROR,
)


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


class TestOBDHEnhanced:
    """Enhanced OBDH model tests covering dual OBC, boot loader, dual CAN
    bus, buffer management, and failure injection."""

    def _make_model(self):
        """Create a configured OBDHBasicModel for testing."""
        model = OBDHBasicModel()
        model.configure({})
        return model

    # ------------------------------------------------------------------
    # 1. Initial state
    # ------------------------------------------------------------------
    def test_initial_state(self):
        """Verify default values after configure: active_obc=0,
        sw_image=SW_BOOTLOADER (cold-boot), active_bus=0, bus statuses=BUS_OK."""
        from smo_simulator.models.obdh_basic import SW_BOOTLOADER
        model = self._make_model()
        s = model._state

        assert s.active_obc == 0, "Default active OBC should be 0 (A)"
        assert s.sw_image == SW_BOOTLOADER, "Default sw_image should be SW_BOOTLOADER (cold-boot)"
        assert s.active_bus == 0, "Default active bus should be 0 (A)"
        assert s.bus_a_status == BUS_OK, "Default bus A status should be BUS_OK"
        assert s.bus_b_status == BUS_OK, "Default bus B status should be BUS_OK"

    # ------------------------------------------------------------------
    # 2. OBC reboot drops to bootloader
    # ------------------------------------------------------------------
    def test_obc_reboot_drops_to_bootloader(self):
        """handle_command obc_reboot should drop sw_image to SW_BOOTLOADER,
        set mode to 1 (safe), increment reboot_count and boot_count_a,
        and set last_reboot_cause to REBOOT_COMMAND."""
        model = self._make_model()
        initial_reboot = model._state.reboot_count
        initial_boot_a = model._state.boot_count_a

        result = model.handle_command({"command": "obc_reboot"})

        assert result["success"] is True
        s = model._state
        assert s.sw_image == SW_BOOTLOADER, "After reboot, sw_image should be SW_BOOTLOADER"
        assert s.mode == 1, "After reboot, mode should be 1 (safe)"
        assert s.reboot_count == initial_reboot + 1, "reboot_count should increase by 1"
        assert s.boot_count_a == initial_boot_a + 1, "boot_count_a should increase by 1"
        assert s.last_reboot_cause == REBOOT_COMMAND, (
            "last_reboot_cause should be REBOOT_COMMAND"
        )

    # ------------------------------------------------------------------
    # 3. OBC reboot auto-boots application after 10s
    # ------------------------------------------------------------------
    def test_obc_reboot_auto_boots_app(self):
        """After reboot, ticking 11 seconds should transition sw_image
        back to SW_APPLICATION (boot_app_timer starts at 10s)."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        model.handle_command({"command": "obc_reboot"})
        assert model._state.sw_image == SW_BOOTLOADER

        # Tick 11 seconds (each tick is 1s)
        for _ in range(11):
            model.tick(1.0, orbit, params)

        assert model._state.sw_image == SW_APPLICATION, (
            "sw_image should transition to SW_APPLICATION after 10s boot timer"
        )

    # ------------------------------------------------------------------
    # 4. Boot inhibit prevents auto-boot
    # ------------------------------------------------------------------
    def test_boot_inhibit_prevents_auto_boot(self):
        """When boot_inhibit is set before reboot, auto-boot should not
        occur even after 15 seconds of ticking."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        # Set boot inhibit before reboot
        result = model.handle_command({"command": "obc_boot_inhibit", "inhibit": True})
        assert result["success"] is True

        model.handle_command({"command": "obc_reboot"})
        assert model._state.sw_image == SW_BOOTLOADER

        # Tick 15 seconds
        for _ in range(15):
            model.tick(1.0, orbit, params)

        assert model._state.sw_image == SW_BOOTLOADER, (
            "sw_image should remain SW_BOOTLOADER when boot_inhibit is active"
        )

    # ------------------------------------------------------------------
    # 5. Corrupt boot image stays in bootloader
    # ------------------------------------------------------------------
    def test_boot_image_corrupt_stays_in_bootloader(self):
        """When boot_image_corrupt failure is injected, the CRC check
        fails and sw_image stays at SW_BOOTLOADER even after auto-boot
        timer expires."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        # Inject boot image corruption
        model.inject_failure("boot_image_corrupt")

        model.handle_command({"command": "obc_reboot"})
        assert model._state.sw_image == SW_BOOTLOADER

        # Tick 15 seconds (well past the 10s boot timer)
        for _ in range(15):
            model.tick(1.0, orbit, params)

        assert model._state.sw_image == SW_BOOTLOADER, (
            "sw_image should remain SW_BOOTLOADER when boot image is corrupt"
        )

    # ------------------------------------------------------------------
    # 6. obc_boot_app command boots from bootloader
    # ------------------------------------------------------------------
    def test_obc_boot_app_command(self):
        """After reboot with boot_inhibit active, an explicit obc_boot_app
        command should trigger the boot sequence. After 11 ticks, sw_image
        should be SW_APPLICATION (since boot_image_corrupt is False)."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        # Set boot inhibit so auto-boot does not fire
        model.handle_command({"command": "obc_boot_inhibit", "inhibit": True})
        model.handle_command({"command": "obc_reboot"})
        assert model._state.sw_image == SW_BOOTLOADER

        # Manually trigger boot to application
        result = model.handle_command({"command": "obc_boot_app"})
        assert result["success"] is True

        # Tick 11 seconds to allow the 10s CRC verification to complete
        for _ in range(11):
            model.tick(1.0, orbit, params)

        assert model._state.sw_image == SW_APPLICATION, (
            "obc_boot_app command should transition to SW_APPLICATION after CRC timer"
        )

    # ------------------------------------------------------------------
    # 7. obc_boot_app rejects if already in application
    # ------------------------------------------------------------------
    def test_obc_boot_app_rejects_if_already_application(self):
        """Calling obc_boot_app when already in SW_APPLICATION should
        return success=False."""
        model = self._make_model()
        # Cold boot starts in bootloader; force application for this test.
        model._state.sw_image = SW_APPLICATION
        assert model._state.sw_image == SW_APPLICATION

        result = model.handle_command({"command": "obc_boot_app"})
        assert result["success"] is False, (
            "obc_boot_app should reject when already in application mode"
        )

    # ------------------------------------------------------------------
    # 8. Switchover changes active OBC
    # ------------------------------------------------------------------
    def test_switchover_changes_active_obc(self):
        """obc_switch_unit should change active_obc from 0 to 1 and set
        last_reboot_cause to REBOOT_SWITCHOVER."""
        model = self._make_model()
        assert model._state.active_obc == 0

        result = model.handle_command({"command": "obc_switch_unit"})
        assert result["success"] is True

        s = model._state
        assert s.active_obc == 1, "active_obc should change from 0 to 1 after switchover"
        assert s.last_reboot_cause == REBOOT_SWITCHOVER, (
            "last_reboot_cause should be REBOOT_SWITCHOVER"
        )

    # ------------------------------------------------------------------
    # 9. Switchover resets state (cold redundant fresh start)
    # ------------------------------------------------------------------
    def test_switchover_resets_state(self):
        """After switchover, uptime should be 0, tc_rx_count should be 0,
        and sw_image should be SW_BOOTLOADER (cold redundant fresh start)."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        # Accumulate some state
        model.tick(1.0, orbit, params)
        model.tick(1.0, orbit, params)
        model.record_tc_received()
        model.record_tc_received()
        assert model._state.uptime_s > 0
        assert model._state.tc_rx_count > 0

        model.handle_command({"command": "obc_switch_unit"})

        s = model._state
        assert s.uptime_s == 0, "uptime should be reset to 0 after switchover"
        assert s.tc_rx_count == 0, "tc_rx_count should be reset to 0 after switchover"
        assert s.sw_image == SW_BOOTLOADER, (
            "sw_image should be SW_BOOTLOADER after cold redundant switchover"
        )

    # ------------------------------------------------------------------
    # 10. Bus select command
    # ------------------------------------------------------------------
    def test_bus_select_command(self):
        """obc_select_bus with bus=1 should set active_bus to 1."""
        model = self._make_model()
        assert model._state.active_bus == 0

        result = model.handle_command({"command": "obc_select_bus", "bus": 1})
        assert result["success"] is True
        assert model._state.active_bus == 1, "active_bus should be 1 after select"

    # ------------------------------------------------------------------
    # 11. Bus select rejects failed bus
    # ------------------------------------------------------------------
    def test_bus_select_rejects_failed_bus(self):
        """Inject bus_failure on bus A, then try obc_select_bus bus=0.
        Should return success=False."""
        model = self._make_model()

        # Switch to bus B first so we can try to switch back
        model.handle_command({"command": "obc_select_bus", "bus": 1})

        # Fail bus A
        model.inject_failure("bus_failure", bus="A")
        assert model._state.bus_a_status == BUS_FAILED

        # Try to select bus A
        result = model.handle_command({"command": "obc_select_bus", "bus": 0})
        assert result["success"] is False, (
            "Selecting a FAILED bus should return success=False"
        )

    # ------------------------------------------------------------------
    # 12. Subsystem reachable on active bus
    # ------------------------------------------------------------------
    def test_subsystem_reachable_on_active_bus(self):
        """Default bus A has ['eps', 'tcs', 'aocs']. Verify eps is
        reachable and ttc (on bus B) is NOT reachable."""
        model = self._make_model()
        assert model._state.active_bus == 0  # Bus A active

        assert model.is_subsystem_reachable("eps") is True, (
            "eps should be reachable on bus A"
        )
        assert model.is_subsystem_reachable("tcs") is True, (
            "tcs should be reachable on bus A"
        )
        assert model.is_subsystem_reachable("aocs") is True, (
            "aocs should be reachable on bus A"
        )
        assert model.is_subsystem_reachable("ttc") is False, (
            "ttc should NOT be reachable on bus A (it is on bus B)"
        )

    # ------------------------------------------------------------------
    # 13. Subsystem unreachable on failed bus
    # ------------------------------------------------------------------
    def test_subsystem_unreachable_on_failed_bus(self):
        """Inject bus_failure on bus A (which is active). eps should no
        longer be reachable."""
        model = self._make_model()
        assert model._state.active_bus == 0

        model.inject_failure("bus_failure", bus="A")
        assert model._state.bus_a_status == BUS_FAILED

        assert model.is_subsystem_reachable("eps") is False, (
            "eps should NOT be reachable when bus A is FAILED"
        )

    # ------------------------------------------------------------------
    # 14. Bus failure injection and clear
    # ------------------------------------------------------------------
    def test_bus_failure_injection_and_clear(self):
        """Inject bus_failure on A, verify bus_a_status=BUS_FAILED.
        Clear it, verify bus_a_status=BUS_OK."""
        model = self._make_model()

        model.inject_failure("bus_failure", bus="A")
        assert model._state.bus_a_status == BUS_FAILED, (
            "bus_a_status should be BUS_FAILED after injection"
        )

        model.clear_failure("bus_failure", bus="A")
        assert model._state.bus_a_status == BUS_OK, (
            "bus_a_status should be BUS_OK after clearing"
        )

    # ------------------------------------------------------------------
    # 15. Buffer fill tracking (event buffer)
    # ------------------------------------------------------------------
    def test_buffer_fill_tracking(self):
        """record_event should increase event_buf_fill. Buffer should
        stop accepting events when full."""
        model = self._make_model()
        initial = model._state.event_buf_fill
        assert initial == 0

        # Record a few events
        for _ in range(5):
            result = model.record_event()
            assert result is True

        assert model._state.event_buf_fill == 5, (
            "event_buf_fill should be 5 after recording 5 events"
        )

        # Fill to capacity
        capacity = model._state.event_buf_capacity
        model._state.event_buf_fill = capacity

        # Attempt to record one more
        result = model.record_event()
        assert result is False, (
            "record_event should return False when event buffer is full"
        )
        assert model._state.event_buf_fill == capacity, (
            "event_buf_fill should not exceed capacity"
        )

    # ------------------------------------------------------------------
    # 16. Alarm buffer
    # ------------------------------------------------------------------
    def test_alarm_buffer(self):
        """record_alarm should increase alarm_buf_fill. Filling past
        capacity should return False."""
        model = self._make_model()
        assert model._state.alarm_buf_fill == 0

        # Record a few alarms
        for _ in range(3):
            result = model.record_alarm()
            assert result is True

        assert model._state.alarm_buf_fill == 3, (
            "alarm_buf_fill should be 3 after recording 3 alarms"
        )

        # Fill to capacity
        capacity = model._state.alarm_buf_capacity
        model._state.alarm_buf_fill = capacity

        # Attempt to record one more
        result = model.record_alarm()
        assert result is False, (
            "record_alarm should return False when alarm buffer is full"
        )
        assert model._state.alarm_buf_fill == capacity, (
            "alarm_buf_fill should not exceed capacity"
        )

    # ------------------------------------------------------------------
    # 17. Clear reboot count
    # ------------------------------------------------------------------
    def test_clear_reboot_count(self):
        """After setting some boot counts, obc_clear_reboot_cnt should
        reset all counters to 0."""
        model = self._make_model()

        # Accumulate some reboots
        model.handle_command({"command": "obc_reboot"})
        model.handle_command({"command": "obc_reboot"})
        assert model._state.reboot_count >= 2
        assert model._state.boot_count_a >= 2

        result = model.handle_command({"command": "obc_clear_reboot_cnt"})
        assert result["success"] is True

        s = model._state
        assert s.reboot_count == 0, "reboot_count should be 0 after clear"
        assert s.boot_count_a == 0, "boot_count_a should be 0 after clear"
        assert s.boot_count_b == 0, "boot_count_b should be 0 after clear"

    # ------------------------------------------------------------------
    # 18. CPU load lower in bootloader
    # ------------------------------------------------------------------
    def test_cpu_load_lower_in_bootloader(self):
        """In bootloader mode, cpu_load should hover around 15.0 (baseline).
        In application mode, cpu_load should hover around 35.0 (baseline)."""
        model = self._make_model()
        # Cold boot starts in bootloader; force application for the first leg.
        model._state.sw_image = SW_APPLICATION
        orbit = make_orbit_state()
        params = {}

        # Application mode CPU load (baseline 35.0)
        app_loads = []
        for _ in range(20):
            model.tick(1.0, orbit, params)
            app_loads.append(model._state.cpu_load)
        app_avg = sum(app_loads) / len(app_loads)

        # Reboot to drop to bootloader
        model.handle_command({"command": "obc_reboot"})
        # Prevent auto-boot so we stay in bootloader
        model._state.boot_app_pending = False
        assert model._state.sw_image == SW_BOOTLOADER

        # Bootloader mode CPU load (baseline 15.0)
        bl_loads = []
        for _ in range(20):
            model.tick(1.0, orbit, params)
            bl_loads.append(model._state.cpu_load)
        bl_avg = sum(bl_loads) / len(bl_loads)

        assert bl_avg < app_avg, (
            f"Bootloader CPU avg ({bl_avg:.1f}) should be lower than "
            f"application CPU avg ({app_avg:.1f})"
        )
        # Bootloader baseline is 15.0, application baseline is 35.0
        assert bl_avg < 25.0, (
            f"Bootloader CPU avg ({bl_avg:.1f}) should be near 15.0"
        )
        assert app_avg > 25.0, (
            f"Application CPU avg ({app_avg:.1f}) should be near 35.0"
        )

    # ------------------------------------------------------------------
    # 19. OBC crash failure
    # ------------------------------------------------------------------
    def test_obc_crash_failure(self):
        """Inject obc_crash, verify reboot occurred: sw_image should be
        SW_BOOTLOADER and last_reboot_cause should be REBOOT_WATCHDOG."""
        model = self._make_model()
        # Force application image so the crash transition is meaningful.
        model._state.sw_image = SW_APPLICATION
        assert model._state.sw_image == SW_APPLICATION

        model.inject_failure("obc_crash")

        s = model._state
        assert s.sw_image == SW_BOOTLOADER, (
            "sw_image should be SW_BOOTLOADER after OBC crash"
        )
        assert s.last_reboot_cause == REBOOT_WATCHDOG, (
            "last_reboot_cause should be REBOOT_WATCHDOG after OBC crash"
        )

    # ------------------------------------------------------------------
    # 20. Memory corruption failure
    # ------------------------------------------------------------------
    def test_memory_corruption_failure(self):
        """Inject memory_corruption, verify reboot occurred and
        mem_errors increased."""
        model = self._make_model()
        initial_mem_errors = model._state.mem_errors

        model.inject_failure("memory_corruption")

        s = model._state
        assert s.sw_image == SW_BOOTLOADER, (
            "sw_image should be SW_BOOTLOADER after memory corruption"
        )
        assert s.last_reboot_cause == REBOOT_MEMORY_ERROR, (
            "last_reboot_cause should be REBOOT_MEMORY_ERROR"
        )
        assert s.mem_errors > initial_mem_errors, (
            "mem_errors should increase after memory corruption injection"
        )

    # ------------------------------------------------------------------
    # 21. New params written to shared_params
    # ------------------------------------------------------------------
    def test_new_params_written(self):
        """After a tick, verify params 0x030C through 0x0318 exist in
        shared_params."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        model.tick(1.0, orbit, params)

        expected_params = [
            0x030C,  # active_obc
            0x030D,  # obc_b_status
            0x030E,  # active_bus
            0x030F,  # bus_a_status
            0x0310,  # bus_b_status
            0x0311,  # sw_image
            0x0312,  # hktm_buf_fill
            0x0313,  # event_buf_fill
            0x0314,  # alarm_buf_fill
            0x0316,  # last_reboot_cause
            0x0317,  # boot_count_a
            0x0318,  # boot_count_b
        ]

        for addr in expected_params:
            assert addr in params, (
                f"Param 0x{addr:04X} missing from shared_params after tick"
            )

    # ------------------------------------------------------------------
    # 22. Heat dissipation parameter (defects/reviews/obdh.md)
    # ------------------------------------------------------------------
    def test_heat_dissipation_in_application_mode(self):
        """In application mode, heat_dissipation_w should be approximately
        15W nominal (ranging 5-25W based on CPU load and mode). Parameter
        0x031F should reflect this."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        # Ensure in application mode, nominal mode
        model._state.sw_image = SW_APPLICATION
        model._state.mode = 0  # nominal
        model._state.cpu_load = 35.0  # Default CPU load

        # Tick and check
        model.tick(1.0, orbit, params)

        heat = model._state.heat_dissipation_w
        assert 5.0 <= heat <= 35.0, (
            f"heat_dissipation_w should be clamped to [5, 35]W; got {heat}W"
        )
        # Verify parameter 0x031F is written
        assert 0x031F in params, "Parameter 0x031F (heat_dissipation_w) missing from shared_params"
        assert params[0x031F] == heat, "Parameter 0x031F should match internal heat_dissipation_w"

    def test_heat_dissipation_in_bootloader_mode(self):
        """In bootloader mode, heat_dissipation_w should be minimal (~2W)
        since bootloader has minimal activity."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        # Force bootloader mode
        model._state.sw_image = SW_BOOTLOADER

        # Tick and check
        model.tick(1.0, orbit, params)

        heat = model._state.heat_dissipation_w
        assert 1.5 <= heat <= 2.5, (
            f"heat_dissipation_w should be ~2W in bootloader mode; got {heat}W"
        )

        # Verify parameter 0x031F is written
        assert 0x031F in params, "Parameter 0x031F missing in bootloader mode"
        assert abs(params[0x031F] - heat) < 0.01, "Parameter 0x031F mismatch"

    def test_heat_dissipation_is_computed(self):
        """heat_dissipation_w should be computed for every tick in
        application mode and reflect the current system state."""
        model = self._make_model()
        orbit = make_orbit_state()

        # Application mode, nominal
        model._state.sw_image = SW_APPLICATION
        model._state.mode = 0

        # Over multiple ticks, heat_dissipation_w should vary within
        # the expected range due to CPU load and random variation
        min_heat = float('inf')
        max_heat = float('-inf')

        for _ in range(50):
            params = {}
            model.tick(1.0, orbit, params)
            heat = model._state.heat_dissipation_w
            min_heat = min(min_heat, heat)
            max_heat = max(max_heat, heat)

        # Over 50 ticks, we should see variability, not a constant value
        assert max_heat > min_heat, (
            "heat_dissipation_w should vary over multiple ticks due to "
            "randomness in CPU load and noise"
        )
        # Check range is reasonable
        assert min_heat >= 5.0 and max_heat <= 35.0, (
            f"heat_dissipation_w should stay in [5, 35]W range; "
            f"saw [{min_heat:.1f}, {max_heat:.1f}]W"
        )

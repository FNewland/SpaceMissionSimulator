"""Comprehensive tests for the enhanced AOCS state machine model.

Covers the 9-mode state machine, automatic and emergency transitions,
dual star trackers, CSS, magnetorquers, reaction wheels, failure injection,
and telemetry parameter output.
"""
import pytest
from unittest.mock import MagicMock

from smo_simulator.models.aocs_basic import (
    AOCSBasicModel,
    MODE_OFF,
    MODE_SAFE_BOOT,
    MODE_DETUMBLE,
    MODE_COARSE_SUN,
    MODE_NOMINAL,
    MODE_FINE_POINT,
    MODE_SLEW,
    MODE_DESAT,
    MODE_ECLIPSE,
)


def make_orbit_state(in_eclipse=False, beta=20.0):
    state = MagicMock()
    state.in_eclipse = in_eclipse
    state.solar_beta_deg = beta
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


class TestAOCSStateMachine:
    """Tests for the AOCS 9-mode state machine and subsystem behaviour."""

    def _make_model(self):
        """Create a configured AOCSBasicModel ready for testing."""
        model = AOCSBasicModel()
        model.configure({})
        return model

    # ------------------------------------------------------------------
    # 1. Initial mode
    # ------------------------------------------------------------------

    def test_initial_mode_is_off(self):
        """Model starts in mode 0 (OFF) for safe boot. See defects/reviews/aocs.md Defect #1."""
        model = self._make_model()
        assert model._state.mode == MODE_OFF
        assert model._state.mode == 0

    # ------------------------------------------------------------------
    # 2. set_mode command for every valid mode
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("target_mode", range(9))
    def test_set_mode_command(self, target_mode):
        """set_mode command to each valid mode (0-8) succeeds and changes mode."""
        model = self._make_model()
        result = model.handle_command({"command": "set_mode", "mode": target_mode})
        assert result["success"] is True
        assert model._state.mode == target_mode

    # ------------------------------------------------------------------
    # 3. set_mode rejects invalid mode
    # ------------------------------------------------------------------

    def test_set_mode_rejects_invalid(self):
        """set_mode to 9 (or any out-of-range value) returns failure."""
        model = self._make_model()
        result = model.handle_command({"command": "set_mode", "mode": 9})
        assert result["success"] is False
        # Mode should remain unchanged (OFF)
        assert model._state.mode == MODE_OFF

    # ------------------------------------------------------------------
    # 4. SAFE_BOOT -> DETUMBLE auto-transition after 30s
    # ------------------------------------------------------------------

    def test_safe_boot_to_detumble_transition(self):
        """After 30+ seconds in SAFE_BOOT, auto-transition to DETUMBLE."""
        model = self._make_model()
        model.handle_command({"command": "set_mode", "mode": MODE_SAFE_BOOT})
        assert model._state.mode == MODE_SAFE_BOOT

        orbit = make_orbit_state()
        params = {}

        # Tick 31 seconds (1s per tick)
        for _ in range(31):
            model.tick(1.0, orbit, params)

        assert model._state.mode == MODE_DETUMBLE

    # ------------------------------------------------------------------
    # 5. DETUMBLE -> COARSE_SUN when rates are low for 30s
    # ------------------------------------------------------------------

    def test_detumble_to_coarse_sun_transition(self):
        """In DETUMBLE, when rates stay below 0.5 deg/s for 30s, transition
        to COARSE_SUN."""
        model = self._make_model()
        model.handle_command({"command": "set_mode", "mode": MODE_DETUMBLE})
        assert model._state.mode == MODE_DETUMBLE

        # Set rates very low so they remain below 0.5 deg/s threshold
        model._state.rate_roll = 0.1
        model._state.rate_pitch = 0.1
        model._state.rate_yaw = 0.1

        orbit = make_orbit_state()
        params = {}

        # Tick enough to pass minimum dwell (10s) and guard timer (30s)
        # Use small dt to keep rates low and avoid noise accumulation
        # We need time_in_mode >= 10 (dwell) and guard_timer >= 30
        for _ in range(50):
            # Keep rates pinned low before each tick so noise doesn't accumulate
            model._state.rate_roll = 0.1
            model._state.rate_pitch = 0.1
            model._state.rate_yaw = 0.1
            model.tick(1.0, orbit, params)

        assert model._state.mode == MODE_COARSE_SUN

    # ------------------------------------------------------------------
    # 6. Emergency DETUMBLE on high rates
    # ------------------------------------------------------------------

    def test_emergency_detumble_on_high_rates(self):
        """When in NOMINAL and body rates exceed 2.0 deg/s, emergency
        transition to DETUMBLE is triggered."""
        model = self._make_model()
        # First set to NOMINAL (starts in OFF per defects/reviews/aocs.md)
        model.handle_command({"command": "set_mode", "mode": MODE_NOMINAL})
        assert model._state.mode == MODE_NOMINAL

        # Set rates above emergency threshold (2.0 deg/s)
        model._state.rate_roll = 3.0
        model._state.rate_pitch = 3.0
        model._state.rate_yaw = 3.0

        orbit = make_orbit_state()
        params = {}
        model.tick(1.0, orbit, params)

        assert model._state.mode == MODE_DETUMBLE

    # ------------------------------------------------------------------
    # 7. Emergency NOT triggered when already in DETUMBLE
    # ------------------------------------------------------------------

    def test_emergency_not_triggered_in_detumble(self):
        """Already in DETUMBLE with high rates -- stays in DETUMBLE,
        emergency does not re-trigger or switch mode."""
        model = self._make_model()
        model.handle_command({"command": "set_mode", "mode": MODE_DETUMBLE})
        assert model._state.mode == MODE_DETUMBLE

        # Set high rates
        model._state.rate_roll = 3.0
        model._state.rate_pitch = 3.0
        model._state.rate_yaw = 3.0

        orbit = make_orbit_state()
        params = {}
        model.tick(1.0, orbit, params)

        # Should still be in DETUMBLE (not reset or switched)
        assert model._state.mode == MODE_DETUMBLE

    # ------------------------------------------------------------------
    # 8. time_in_mode tracking
    # ------------------------------------------------------------------

    def test_time_in_mode_tracking(self):
        """time_in_mode accumulates correctly across ticks."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        n_ticks = 10
        dt = 1.0
        for _ in range(n_ticks):
            model.tick(dt, orbit, params)

        # time_in_mode should be approximately n_ticks * dt
        assert model._state.time_in_mode == pytest.approx(
            n_ticks * dt, abs=0.1
        )

    # ------------------------------------------------------------------
    # 9. Star tracker boot time
    # ------------------------------------------------------------------

    def test_star_tracker_boot_time(self):
        """Power on ST2 (initially OFF): status goes to BOOTING (1),
        then after 60s becomes TRACKING (2)."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        # ST2 starts OFF
        assert model._state.st2_status == 0

        # Power on ST2
        result = model.handle_command(
            {"command": "st_power", "unit": 2, "on": True}
        )
        assert result["success"] is True
        assert model._state.st2_status == 1  # BOOTING

        # Tick 59 seconds -- should still be BOOTING
        for _ in range(59):
            model.tick(1.0, orbit, params)
        assert model._state.st2_status == 1  # still BOOTING

        # Tick 2 more seconds (total 61) -- should transition to TRACKING
        for _ in range(2):
            model.tick(1.0, orbit, params)
        assert model._state.st2_status == 2  # TRACKING

    # ------------------------------------------------------------------
    # 10. Star tracker failure
    # ------------------------------------------------------------------

    def test_star_tracker_failure(self):
        """Inject st_failure on unit 1: status becomes 4 (FAILED),
        num_stars becomes 0."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        # Power on ST1 and boot it to TRACKING (STs start OFF at power-up)
        model.handle_command({"command": "st_power", "unit": 1, "on": True})
        for _ in range(61):
            model.tick(1.0, orbit, params)
        assert model._state.st1_status == 2

        model.inject_failure("st_failure", unit=1)
        assert model._state.st1_status == 4
        assert model._state.st1_num_stars == 0

        # After a tick, should remain failed
        model.tick(1.0, orbit, params)
        assert model._state.st1_status == 4
        assert model._state.st1_num_stars == 0

    # ------------------------------------------------------------------
    # 11. Star tracker select
    # ------------------------------------------------------------------

    def test_star_tracker_select(self):
        """Power on and boot ST2, then select it as primary."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        # Power on ST2 and boot it
        model.handle_command({"command": "st_power", "unit": 2, "on": True})
        for _ in range(61):
            model.tick(1.0, orbit, params)
        assert model._state.st2_status == 2  # TRACKING

        # Select ST2
        result = model.handle_command({"command": "st_select", "unit": 2})
        assert result["success"] is True
        assert model._state.st_selected == 2

    # ------------------------------------------------------------------
    # 12. CSS invalid in eclipse
    # ------------------------------------------------------------------

    def test_css_invalid_in_eclipse(self):
        """When orbiting in eclipse, CSS should be invalid."""
        model = self._make_model()
        orbit = make_orbit_state(in_eclipse=True)
        params = {}

        model.tick(1.0, orbit, params)

        assert model._state.css_valid is False

    # ------------------------------------------------------------------
    # 13. CSS valid in sunlight
    # ------------------------------------------------------------------

    def test_css_valid_in_sunlight(self):
        """When in sunlight with beta=20, CSS should be valid."""
        model = self._make_model()
        orbit = make_orbit_state(in_eclipse=False, beta=20.0)
        params = {}

        model.tick(1.0, orbit, params)

        assert model._state.css_valid is True

    # ------------------------------------------------------------------
    # 14. CSS failure
    # ------------------------------------------------------------------

    def test_css_failure(self):
        """Inject css_failure: css_valid becomes False, sun vector zeroed."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        model.inject_failure("css_failure")
        model.tick(1.0, orbit, params)

        assert model._state.css_valid is False
        assert model._state.css_sun_x == 0.0
        assert model._state.css_sun_y == 0.0
        assert model._state.css_sun_z == 0.0

    # ------------------------------------------------------------------
    # 15. Magnetometer failure
    # ------------------------------------------------------------------

    def test_magnetometer_failure(self):
        """Inject mag_failure: mag_valid becomes False."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        model.inject_failure("mag_failure")
        model.tick(1.0, orbit, params)

        assert model._state.mag_valid is False

    # ------------------------------------------------------------------
    # 16. Wheel enable / disable
    # ------------------------------------------------------------------

    def test_wheel_enable_disable(self):
        """Disable wheel 2 via command, verify disabled. Enable it back,
        verify restored."""
        model = self._make_model()

        # Disable wheel 2
        result = model.handle_command({"command": "disable_wheel", "wheel": 2})
        assert result["success"] is True
        assert model._state.active_wheels[2] is False
        assert model._state.rw_enabled[2] is False

        # Enable wheel 2
        result = model.handle_command({"command": "enable_wheel", "wheel": 2})
        assert result["success"] is True
        assert model._state.active_wheels[2] is True
        assert model._state.rw_enabled[2] is True

    # ------------------------------------------------------------------
    # 17. Magnetorquer enable / disable
    # ------------------------------------------------------------------

    def test_mtq_enable_disable(self):
        """Disable magnetorquers, verify disabled and duties zeroed.
        Enable them back."""
        model = self._make_model()

        # Disable
        result = model.handle_command({"command": "mtq_disable"})
        assert result["success"] is True
        assert model._state.mtq_enabled is False
        assert model._state.mtq_x_duty == 0.0
        assert model._state.mtq_y_duty == 0.0
        assert model._state.mtq_z_duty == 0.0

        # Enable
        result = model.handle_command({"command": "mtq_enable"})
        assert result["success"] is True
        assert model._state.mtq_enabled is True

    # ------------------------------------------------------------------
    # 18. Multi-wheel failure forces COARSE_SUN
    # ------------------------------------------------------------------

    def test_multi_wheel_failure_forces_coarse_sun(self):
        """Inject multi_wheel_failure on wheels [0, 1]: with fewer than
        3 active wheels, mode transitions to COARSE_SUN."""
        model = self._make_model()
        # First set to NOMINAL (starts in OFF per defects/reviews/aocs.md)
        model.handle_command({"command": "set_mode", "mode": MODE_NOMINAL})
        assert model._state.mode == MODE_NOMINAL

        model.inject_failure("multi_wheel_failure", wheels=[0, 1])

        assert model._state.mode == MODE_COARSE_SUN
        assert model._state.active_wheels[0] is False
        assert model._state.active_wheels[1] is False

    # ------------------------------------------------------------------
    # 19. Total momentum computed
    # ------------------------------------------------------------------

    def test_total_momentum_computed(self):
        """After ticking in NOMINAL mode, total_momentum should be > 0
        (non-zero wheel speeds contribute momentum)."""
        model = self._make_model()
        # Set to NOMINAL (starts in OFF per defects/reviews/aocs.md)
        model.handle_command({"command": "set_mode", "mode": MODE_NOMINAL})
        orbit = make_orbit_state()
        params = {}

        model.tick(1.0, orbit, params)

        assert model._state.total_momentum > 0

    # ------------------------------------------------------------------
    # 20. Reaction wheel current params
    # ------------------------------------------------------------------

    def test_rw_current_params(self):
        """After tick, shared_params contain RW current entries at
        0x0250-0x0253, each >= 0."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        model.tick(1.0, orbit, params)

        for pid in (0x0250, 0x0251, 0x0252, 0x0253):
            assert pid in params, f"Param {hex(pid)} missing"
            assert params[pid] >= 0, f"Param {hex(pid)} is negative"

    # ------------------------------------------------------------------
    # 21. Submode and time-in-mode params
    # ------------------------------------------------------------------

    def test_submode_and_time_in_mode_params(self):
        """After tick, shared_params contain aocs_submode (0x0262) and
        time_in_mode (0x0264)."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        model.tick(1.0, orbit, params)

        assert 0x0262 in params, "aocs_submode param missing"
        assert 0x0264 in params, "time_in_mode param missing"

    # ------------------------------------------------------------------
    # 22. DESAT mode reduces wheel speeds toward desat_speed
    # ------------------------------------------------------------------

    def test_desat_mode_reduces_wheel_speeds(self):
        """Set high initial wheel speeds, enter DESAT, tick many times.
        Wheel speeds should decrease toward desat_speed (200 rpm)."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        # Set all wheel speeds high
        initial_speed = 4000.0
        for i in range(4):
            model._state.rw_speed[i] = initial_speed

        # Enter DESAT mode
        model.handle_command({"command": "set_mode", "mode": MODE_DESAT})
        assert model._state.mode == MODE_DESAT

        # Tick many times to allow desaturation
        for _ in range(200):
            model.tick(1.0, orbit, params)
            # If mode auto-transitions to NOMINAL, force back to DESAT
            # (guard might trigger once speeds are low enough)
            if model._state.mode != MODE_DESAT:
                break

        # All wheel speeds should have decreased significantly
        for i in range(4):
            assert abs(model._state.rw_speed[i]) < initial_speed, (
                f"Wheel {i} speed {model._state.rw_speed[i]} did not decrease "
                f"from initial {initial_speed}"
            )

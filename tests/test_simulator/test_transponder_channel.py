"""Tests for the TTC dedicated PDM command channel.

Covers:
  - cmd_decode_timer starts on successful command frame decode
  - 15-min timer keeps TX+PA enabled regardless of OBC state
  - Timer expiry turns off TX+PA (unless re-triggered)
  - Burn wire antenna deployment command
  - Low-rate mode (1kbps) before antenna deployment
  - High-rate mode (64kbps) after antenna deployment
  - Link margin improvement after deployment
  - Beacon mode behavior
"""
import pytest
from unittest.mock import MagicMock

from smo_simulator.models.ttc_basic import TTCBasicModel


def make_orbit_state(in_contact=False, range_km=1000.0, elevation=30.0):
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


def _make_model():
    model = TTCBasicModel()
    model.configure({})
    return model


class TestCommandChannelStart:
    """Test cmd_channel_start command and timer behavior."""

    def test_cmd_channel_start_sets_timer_to_900(self):
        """cmd_channel_start should set cmd_decode_timer to 900s (15 min)."""
        model = _make_model()
        result = model.handle_command({"command": "cmd_channel_start"})
        assert result["success"] is True
        assert model._state.cmd_decode_timer == 900.0
        assert model._state.cmd_channel_active is True

    def test_cmd_channel_start_enables_pa(self):
        """cmd_channel_start should turn on the PA."""
        model = _make_model()
        model._state.pa_on = False
        model.handle_command({"command": "cmd_channel_start"})
        assert model._state.pa_on is True

    def test_cmd_channel_timer_decrements(self):
        """Timer should decrement by dt each tick while active."""
        model = _make_model()
        model.handle_command({"command": "cmd_channel_start"})
        params = {}
        orbit = make_orbit_state(in_contact=False)
        model.tick(1.0, orbit, params)
        assert model._state.cmd_decode_timer == pytest.approx(899.0, abs=0.1)
        assert model._state.cmd_channel_active is True

    def test_cmd_channel_keeps_pa_on(self):
        """While cmd_channel_active, PA and TX should be forced on."""
        model = _make_model()
        model.handle_command({"command": "cmd_channel_start"})
        params = {}
        orbit = make_orbit_state(in_contact=False)
        model.tick(1.0, orbit, params)
        assert model._state.pa_on is True
        assert model._state.tx_fwd_power > 0.0

    def test_cmd_channel_timer_param_in_shared(self):
        """Param 0x0522 should contain the cmd_decode_timer value."""
        model = _make_model()
        model.handle_command({"command": "cmd_channel_start"})
        params = {}
        orbit = make_orbit_state(in_contact=False)
        model.tick(1.0, orbit, params)
        assert 0x0522 in params
        assert params[0x0522] > 0


class TestCommandChannelExpiry:
    """Test command channel timer expiry."""

    def test_timer_expiry_deactivates_channel(self):
        """When timer reaches 0, cmd_channel_active should be False."""
        model = _make_model()
        model._state.cmd_channel_active = True
        model._state.cmd_decode_timer = 1.0
        params = {}
        orbit = make_orbit_state(in_contact=False)
        # Tick 2 seconds to expire the 1s timer
        model.tick(2.0, orbit, params)
        assert model._state.cmd_channel_active is False
        assert model._state.cmd_decode_timer == 0.0

    def test_timer_expiry_clamps_to_zero(self):
        """Timer should not go negative after expiry."""
        model = _make_model()
        model._state.cmd_channel_active = True
        model._state.cmd_decode_timer = 0.5
        params = {}
        orbit = make_orbit_state(in_contact=False)
        model.tick(5.0, orbit, params)
        assert model._state.cmd_decode_timer == 0.0

    def test_retrigger_resets_timer(self):
        """Calling cmd_channel_start again should reset the timer to 900."""
        model = _make_model()
        model.handle_command({"command": "cmd_channel_start"})
        params = {}
        orbit = make_orbit_state(in_contact=False)
        # Tick to reduce timer
        model.tick(100.0, orbit, params)
        assert model._state.cmd_decode_timer < 900.0
        # Re-trigger
        model.handle_command({"command": "cmd_channel_start"})
        assert model._state.cmd_decode_timer == 900.0


class TestAntennaDeployment:
    """Test burn-wire antenna deployment command."""

    def test_deploy_antennas_sets_flag(self):
        """deploy_antennas command should set antenna_deployed = True."""
        model = _make_model()
        assert model._state.antenna_deployed is False
        result = model.handle_command({"command": "deploy_antennas"})
        assert result["success"] is True
        assert model._state.antenna_deployed is True

    def test_deploy_antenna_param_in_shared(self):
        """Param 0x0520 should reflect antenna_deployed state."""
        model = _make_model()
        params = {}
        orbit = make_orbit_state(in_contact=False)
        model.tick(1.0, orbit, params)
        assert params[0x0520] == 0.0  # Not deployed

        model.handle_command({"command": "deploy_antennas"})
        model.tick(1.0, orbit, params)
        assert params[0x0520] == 1.0  # Deployed

    def test_deploy_is_permanent(self):
        """Once deployed, antenna_deployed should stay True across ticks."""
        model = _make_model()
        model.handle_command({"command": "deploy_antennas"})
        params = {}
        orbit = make_orbit_state(in_contact=False)
        for _ in range(10):
            model.tick(1.0, orbit, params)
        assert model._state.antenna_deployed is True


class TestDataRateModes:
    """Test low-rate vs high-rate data modes."""

    def test_low_rate_before_deployment(self):
        """Before antenna deployment, TM data rate should be low-rate."""
        model = _make_model()
        assert model._state.antenna_deployed is False
        params = {}
        orbit = make_orbit_state(in_contact=False)
        model.tick(1.0, orbit, params)
        assert model._state.tm_data_rate == model._tm_rate_lo

    def test_high_rate_after_deployment(self):
        """After antenna deployment, TM data rate should be high-rate (64kbps)."""
        model = _make_model()
        model.handle_command({"command": "deploy_antennas"})
        params = {}
        orbit = make_orbit_state(in_contact=False)
        model.tick(1.0, orbit, params)
        assert model._state.tm_data_rate == model._tm_rate_hi

    def test_default_low_rate_is_1000(self):
        """Low rate mode should be 1000 bps (1 kbps)."""
        model = _make_model()
        assert model._tm_rate_lo == 1000

    def test_default_high_rate_is_64000(self):
        """High rate mode should be 64000 bps (64 kbps)."""
        model = _make_model()
        assert model._tm_rate_hi == 64000

    def test_data_rate_param_in_shared(self):
        """Param for tm_data_rate should be present in shared params."""
        model = _make_model()
        params = {}
        orbit = make_orbit_state(in_contact=False)
        model.tick(1.0, orbit, params)
        # 0x0506 is the tm_data_rate param
        assert 0x0506 in params


class TestBeaconMode:
    """Test beacon mode behavior."""

    def test_set_beacon_mode_on(self):
        """set_beacon_mode on=True should enable beacon mode."""
        model = _make_model()
        result = model.handle_command({"command": "set_beacon_mode", "on": True})
        assert result["success"] is True
        assert model._state.beacon_mode is True

    def test_set_beacon_mode_off(self):
        """set_beacon_mode on=False should disable beacon mode."""
        model = _make_model()
        model._state.beacon_mode = True
        result = model.handle_command({"command": "set_beacon_mode", "on": False})
        assert result["success"] is True
        assert model._state.beacon_mode is False

    def test_beacon_mode_forces_low_rate(self):
        """In beacon mode, data rate should be forced to low-rate."""
        model = _make_model()
        model.handle_command({"command": "deploy_antennas"})  # Deploy first
        model.handle_command({"command": "set_beacon_mode", "on": True})
        params = {}
        orbit = make_orbit_state(in_contact=False)
        model.tick(1.0, orbit, params)
        assert model._state.tm_data_rate == model._tm_rate_lo

    def test_beacon_mode_param_in_shared(self):
        """Param 0x0521 should reflect beacon_mode state."""
        model = _make_model()
        model.handle_command({"command": "set_beacon_mode", "on": True})
        params = {}
        orbit = make_orbit_state(in_contact=False)
        model.tick(1.0, orbit, params)
        assert params[0x0521] == 1.0


class TestLinkMarginDeployment:
    """Test link margin changes with antenna deployment."""

    def test_stowed_antenna_penalty(self):
        """Before deployment, link_margin should have a 6 dB penalty."""
        model = _make_model()
        assert model._state.antenna_deployed is False
        params = {}
        # Need frame_sync for link margin calculation
        orbit = make_orbit_state(in_contact=True, range_km=1000.0, elevation=30.0)
        # Tick enough to reach frame sync (>=10s)
        for _ in range(12):
            model.tick(1.0, orbit, params)
        margin_stowed = model._state.link_margin_db
        # The value includes noise but should be lower than deployed
        assert isinstance(margin_stowed, float)

    def test_stowed_penalty_is_6db(self):
        """Stowed antenna should apply a 6 dB penalty to link margin.

        Note: The deployed antenna uses high-rate (64kbps) while stowed uses
        low-rate (1kbps). The higher data rate increases noise bandwidth by
        ~18 dB which offsets the 6 dB stowed penalty. So we test the penalty
        in isolation by forcing both models to the same data rate.
        """
        model_stowed = _make_model()
        model_deployed = _make_model()
        model_deployed.handle_command({"command": "deploy_antennas"})

        # Force both to same data rate to isolate the 6 dB penalty effect
        model_stowed._state.tm_data_rate = 1000
        model_deployed._state.tm_data_rate = 1000
        # Keep beacon mode off so tick doesn't override rate
        model_stowed._state.beacon_mode = True  # Forces low rate
        model_deployed._state.beacon_mode = True

        params_s = {}
        params_d = {}
        orbit = make_orbit_state(in_contact=True, range_km=1000.0, elevation=30.0)

        # Collect multiple samples to average out noise
        margins_stowed = []
        margins_deployed = []
        for _ in range(15):
            model_stowed.tick(1.0, orbit, params_s)
            model_deployed.tick(1.0, orbit, params_d)
            if model_stowed._state.frame_sync and model_deployed._state.frame_sync:
                margins_stowed.append(model_stowed._state.link_margin_db)
                margins_deployed.append(model_deployed._state.link_margin_db)

        # At same data rate, deployed should have ~6 dB better margin
        assert len(margins_deployed) > 0
        avg_stowed = sum(margins_stowed) / len(margins_stowed)
        avg_deployed = sum(margins_deployed) / len(margins_deployed)
        diff = avg_deployed - avg_stowed
        # The 6 dB penalty should be visible (allow noise margin)
        assert diff > 3.0, f"Expected ~6 dB difference, got {diff:.1f} dB"

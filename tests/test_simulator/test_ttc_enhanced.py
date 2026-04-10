"""Tests for the enhanced TTC model — lock acquisition sequence, PA thermal
model with auto-shutdown, BER/Eb-N0 link budget, failure injection, and
new telemetry parameters."""
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


class TestTTCEnhanced:
    """Enhanced TTC model tests covering lock sequence, PA thermal,
    BER/Eb-N0, commands, failure injection, and new params."""

    def _make_model(self):
        """Create a configured TTCBasicModel for testing."""
        model = TTCBasicModel()
        model.configure({})
        return model

    # ------------------------------------------------------------------
    # 1. Lock sequence timing
    # ------------------------------------------------------------------
    def test_lock_sequence_timing(self):
        """Tick with in_contact=True repeatedly. Verify carrier_lock at 2s,
        bit_sync at 5s, and frame_sync at 10s (cumulative from AOS)."""
        model = self._make_model()
        orbit = make_orbit_state(in_contact=True, range_km=1000.0)
        params = {}

        # After 1s total: no carrier_lock yet (need 2s)
        model.tick(1.0, orbit, params)
        assert model._state.carrier_lock is False, (
            "Carrier lock should not be acquired after only 1s"
        )

        # After 3s total: carrier_lock=True, no bit_sync yet (need 5s)
        model.tick(2.0, orbit, params)
        assert model._state.carrier_lock is True, (
            "Carrier lock should be acquired after 3s (>= 2s delay)"
        )
        assert model._state.bit_sync is False, (
            "Bit sync should not be acquired after only 3s"
        )

        # After 6s total: bit_sync=True, no frame_sync yet (need 10s)
        model.tick(3.0, orbit, params)
        assert model._state.bit_sync is True, (
            "Bit sync should be acquired after 6s (>= 5s delay)"
        )
        assert model._state.frame_sync is False, (
            "Frame sync should not be acquired after only 6s"
        )

        # After 11s total: frame_sync=True
        model.tick(5.0, orbit, params)
        assert model._state.frame_sync is True, (
            "Frame sync should be acquired after 11s (>= 10s delay)"
        )

    # ------------------------------------------------------------------
    # 2. Lock reset on LOS
    # ------------------------------------------------------------------
    def test_lock_reset_on_los(self):
        """Achieve full lock, then tick with in_contact=False, verify all
        locks are cleared."""
        model = self._make_model()
        orbit_aos = make_orbit_state(in_contact=True, range_km=1000.0)
        params = {}

        # Achieve full lock (11s cumulative)
        model.tick(11.0, orbit_aos, params)
        assert model._state.frame_sync is True, (
            "Frame sync should be acquired after 11s"
        )

        # LOS: tick with in_contact=False
        orbit_los = make_orbit_state(in_contact=False, range_km=1000.0)
        model.tick(1.0, orbit_los, params)

        assert model._state.carrier_lock is False, (
            "Carrier lock should be cleared on LOS"
        )
        assert model._state.bit_sync is False, (
            "Bit sync should be cleared on LOS"
        )
        assert model._state.frame_sync is False, (
            "Frame sync should be cleared on LOS"
        )

    # ------------------------------------------------------------------
    # 3. BER computed during contact
    # ------------------------------------------------------------------
    def test_ber_computed_during_contact(self):
        """Tick enough for frame_sync (11s), verify BER and Eb/N0 params
        have non-default values."""
        model = self._make_model()
        orbit = make_orbit_state(in_contact=True, range_km=1000.0)
        params = {}

        # Tick to achieve frame_sync, then one more tick to compute link budget
        model.tick(11.0, orbit, params)
        model.tick(1.0, orbit, params)

        # BER should not be the default -10.0 when link is active
        assert params[0x050C] != -10.0, (
            "BER param 0x050C should not be default -10.0 during active link"
        )
        # Eb/N0 should not be 0.0 (the default for no-link)
        assert params[0x0519] != 0.0, (
            "Eb/N0 param 0x0519 should not be 0.0 during active link"
        )

    # ------------------------------------------------------------------
    # 4. PA on/off commands
    # ------------------------------------------------------------------
    def test_pa_on_off_commands(self):
        """pa_off command should set pa_on=False and tx_fwd_power=0.
        pa_on command should re-enable pa_on=True."""
        model = self._make_model()

        # PA off
        result = model.handle_command({"command": "pa_off"})
        assert result["success"] is True
        assert model._state.pa_on is False, (
            "pa_on should be False after pa_off command"
        )
        assert model._state.tx_fwd_power == 0.0, (
            "tx_fwd_power should be 0.0 after pa_off command"
        )

        # PA on
        result = model.handle_command({"command": "pa_on"})
        assert result["success"] is True
        assert model._state.pa_on is True, (
            "pa_on should be True after pa_on command"
        )

    # ------------------------------------------------------------------
    # 5. PA overheat auto-shutdown
    # ------------------------------------------------------------------
    def test_pa_overheat_auto_shutdown(self):
        """Inject pa_overheat failure, tick many times until pa_temp >= 70,
        verify pa_on becomes False and pa_overheat_shutdown is True."""
        model = self._make_model()
        orbit = make_orbit_state(in_contact=True, range_km=1000.0)
        params = {}

        # Inject extra heat to drive PA temperature up
        model.inject_failure("pa_overheat", heat_w=20.0)

        # Tick many times with large dt to heat up the PA
        for _ in range(500):
            model.tick(1.0, orbit, params)
            if model._state.pa_overheat_shutdown:
                break

        assert model._state.pa_overheat_shutdown is True, (
            "PA should auto-shutdown when temperature reaches threshold"
        )
        assert model._state.pa_on is False, (
            "pa_on should be False after overheat auto-shutdown"
        )

    # ------------------------------------------------------------------
    # 6. PA on rejected during overheat
    # ------------------------------------------------------------------
    def test_pa_on_rejected_during_overheat(self):
        """Set pa_overheat_shutdown=True, pa_on command should fail."""
        model = self._make_model()
        model._state.pa_overheat_shutdown = True

        result = model.handle_command({"command": "pa_on"})
        assert result["success"] is False, (
            "pa_on should be rejected when pa_overheat_shutdown is True"
        )
        assert "overheat" in result["message"].lower(), (
            "Rejection message should mention overheat"
        )

    # ------------------------------------------------------------------
    # 7. PA cooldown clears overheat
    # ------------------------------------------------------------------
    def test_pa_cooldown_clears_overheat(self):
        """After overheat, set pa_temp below hysteresis threshold (70-15=55),
        tick, verify pa_overheat_shutdown clears."""
        model = self._make_model()
        model._state.pa_overheat_shutdown = True
        model._state.pa_on = False

        # Set PA temp well below hysteresis threshold (70 - 15 = 55)
        model._state.pa_temp = 50.0

        orbit = make_orbit_state(in_contact=False)
        params = {}
        model.tick(1.0, orbit, params)

        assert model._state.pa_overheat_shutdown is False, (
            "pa_overheat_shutdown should clear when pa_temp < shutdown_temp - 15"
        )

    # ------------------------------------------------------------------
    # 8. Set TX power
    # ------------------------------------------------------------------
    def test_set_tx_power(self):
        """set_tx_power to 3.0 should change _pa_nominal_power_w.
        Out-of-range values (0 or > max) should be rejected."""
        model = self._make_model()

        # Valid power
        result = model.handle_command({"command": "set_tx_power", "power_w": 3.0})
        assert result["success"] is True
        assert model._pa_nominal_power_w == pytest.approx(3.0), (
            "_pa_nominal_power_w should be updated to 3.0"
        )

        # Reject power = 0 (must be > 0)
        result = model.handle_command({"command": "set_tx_power", "power_w": 0.0})
        assert result["success"] is False, (
            "set_tx_power should reject power_w = 0.0"
        )

        # Reject power > max (default max is 5.0)
        result = model.handle_command({"command": "set_tx_power", "power_w": 10.0})
        assert result["success"] is False, (
            "set_tx_power should reject power_w > pa_max_power_w"
        )

    # ------------------------------------------------------------------
    # 9. High BER failure injection
    # ------------------------------------------------------------------
    def test_high_ber_failure(self):
        """Inject high_ber, tick with contact and frame_sync, verify BER is
        worse (higher log10 value, closer to -1) compared to no injection."""
        model = self._make_model()
        # Use a long range so baseline BER isn't at the -12.0 floor
        # (UHF at 401.5 MHz has ~15 dB less FSPL than S-band, so need longer range)
        orbit = make_orbit_state(in_contact=True, range_km=15000.0)
        params = {}

        # Achieve frame_sync and get baseline BER
        model.tick(11.0, orbit, params)
        model.tick(1.0, orbit, params)
        baseline_ber = model._state.ber

        # Inject high BER with a large offset to overcome any margin
        model.inject_failure("high_ber", 1.0, offset=25.0)
        model.tick(1.0, orbit, params)
        degraded_ber = model._state.ber

        # Higher BER (log10 scale) means worse — value is closer to 0 or -1
        assert degraded_ber > baseline_ber, (
            f"BER with high_ber injection ({degraded_ber}) should be worse "
            f"(higher log10 value) than baseline ({baseline_ber})"
        )

    # ------------------------------------------------------------------
    # 10. Uplink loss failure
    # ------------------------------------------------------------------
    def test_uplink_loss_failure(self):
        """Inject uplink_loss, verify carrier_lock=False.
        Also verify record_cmd_received does nothing when uplink_lost."""
        model = self._make_model()

        # Inject uplink loss
        model.inject_failure("uplink_loss")
        assert model._state.uplink_lost is True, (
            "uplink_lost should be True after injection"
        )
        assert model._state.carrier_lock is False, (
            "carrier_lock should be False after uplink_loss injection"
        )

        # record_cmd_received should not increment counter
        initial_count = model._state.cmd_rx_count
        model.record_cmd_received()
        assert model._state.cmd_rx_count == initial_count, (
            "cmd_rx_count should not increase when uplink is lost"
        )

    # ------------------------------------------------------------------
    # 11. Receiver degrade failure
    # ------------------------------------------------------------------
    def test_receiver_degrade_failure(self):
        """Inject receiver_degrade, tick with contact, verify Eb/N0 is lower
        than without the degradation."""
        model = self._make_model()
        orbit = make_orbit_state(in_contact=True, range_km=1000.0)
        params = {}

        # Achieve frame_sync and get baseline Eb/N0
        model.tick(11.0, orbit, params)
        model.tick(1.0, orbit, params)
        baseline_eb_n0 = model._state.eb_n0

        # Inject receiver degradation
        model.inject_failure("receiver_degrade", 1.0, nf_db=10.0)
        model.tick(1.0, orbit, params)
        degraded_eb_n0 = model._state.eb_n0

        assert degraded_eb_n0 < baseline_eb_n0, (
            f"Eb/N0 with receiver_degrade ({degraded_eb_n0:.2f} dB) should be "
            f"lower than baseline ({baseline_eb_n0:.2f} dB)"
        )

    # ------------------------------------------------------------------
    # 12. New params written to shared_params
    # ------------------------------------------------------------------
    def test_new_params_written(self):
        """Tick with contact, verify new params 0x050C, 0x050D, 0x050F,
        0x0510, 0x0511, 0x0512, 0x0513, 0x0516, 0x0519 all exist."""
        model = self._make_model()
        orbit = make_orbit_state(in_contact=True, range_km=1000.0)
        params = {}

        model.tick(1.0, orbit, params)

        expected_params = [
            0x050C,  # ber
            0x050D,  # tx_fwd_power
            0x050F,  # pa_temp
            0x0510,  # carrier_lock
            0x0511,  # bit_sync
            0x0512,  # frame_sync
            0x0513,  # cmd_rx_count
            0x0516,  # pa_on
            0x0519,  # eb_n0
        ]
        for addr in expected_params:
            assert addr in params, (
                f"Param 0x{addr:04X} missing from shared_params"
            )

    # ------------------------------------------------------------------
    # 13. Command RX counter
    # ------------------------------------------------------------------
    def test_cmd_rx_counter(self):
        """Call record_cmd_received() 3 times, verify cmd_rx_count = 3."""
        model = self._make_model()

        assert model._state.cmd_rx_count == 0, (
            "cmd_rx_count should start at 0"
        )

        model.record_cmd_received()
        model.record_cmd_received()
        model.record_cmd_received()

        assert model._state.cmd_rx_count == 3, (
            f"cmd_rx_count should be 3 after 3 calls, got {model._state.cmd_rx_count}"
        )

    # ------------------------------------------------------------------
    # DEFECT FIX #1: Downlink frequency validation (UHF 400–410 MHz)
    # ------------------------------------------------------------------
    def test_downlink_frequency_uhf_valid(self):
        """DEFECT FIX #1: set_dl_freq should accept UHF range 400–410 MHz."""
        model = self._make_model()

        # Valid UHF frequencies should succeed
        result = model.handle_command({"command": "set_dl_freq", "freq_mhz": 400.0})
        assert result["success"] is True, (
            f"set_dl_freq(400.0) should succeed for UHF, got: {result}"
        )

        result = model.handle_command({"command": "set_dl_freq", "freq_mhz": 401.5})
        assert result["success"] is True, (
            f"set_dl_freq(401.5) should succeed for UHF, got: {result}"
        )

        result = model.handle_command({"command": "set_dl_freq", "freq_mhz": 410.0})
        assert result["success"] is True, (
            f"set_dl_freq(410.0) should succeed for UHF, got: {result}"
        )

    def test_downlink_frequency_out_of_range(self):
        """DEFECT FIX #1: set_dl_freq should reject frequencies outside UHF band."""
        model = self._make_model()

        # S-band frequency (old range) should fail
        result = model.handle_command({"command": "set_dl_freq", "freq_mhz": 2200.0})
        assert result["success"] is False, (
            f"set_dl_freq(2200.0) should fail (S-band, not UHF), got: {result}"
        )

        # Out of range low
        result = model.handle_command({"command": "set_dl_freq", "freq_mhz": 390.0})
        assert result["success"] is False, (
            f"set_dl_freq(390.0) should fail (below UHF band), got: {result}"
        )

        # Out of range high
        result = model.handle_command({"command": "set_dl_freq", "freq_mhz": 420.0})
        assert result["success"] is False, (
            f"set_dl_freq(420.0) should fail (above UHF band), got: {result}"
        )

    # ------------------------------------------------------------------
    # DEFECT FIX #4: Antenna deployment sensor telemetry
    # ------------------------------------------------------------------
    def test_antenna_deployment_sensor_initial_state(self):
        """DEFECT FIX #4: Antenna deployment sensor should be stowed initially."""
        model = self._make_model()
        orbit = make_orbit_state(in_contact=False)
        params = {}

        model.tick(1.0, orbit, params)

        # Check sensor telemetry in params
        assert params.get(0x0535) == 1.0, (
            "antenna_deployment_ready (0x0535) should be 1.0 (ready) initially"
        )
        assert params.get(0x0536) == 1.0, (
            "antenna_deployment_sensor (0x0536) should be 1.0 (stowed) initially"
        )

    def test_antenna_deployment_command_updates_sensor(self):
        """DEFECT FIX #4: deploy_antennas command should update sensor to deployed."""
        model = self._make_model()
        orbit = make_orbit_state(in_contact=False)
        params = {}

        # Initial state
        model.tick(1.0, orbit, params)
        assert model._state.antenna_deployment_sensor == 1, (
            "antenna_deployment_sensor should be 1 (stowed) initially"
        )

        # Deploy antennas
        result = model.handle_command({"command": "deploy_antennas"})
        assert result["success"] is True, (
            f"deploy_antennas should succeed, got: {result}"
        )

        # After deployment, sensor should show deployed
        assert model._state.antenna_deployment_sensor == 2, (
            "antenna_deployment_sensor should be 2 (deployed) after deploy command"
        )

        # Verify telemetry
        model.tick(1.0, orbit, params)
        assert params.get(0x0536) == 2.0, (
            "antenna_deployment_sensor (0x0536) should be 2.0 (deployed) after deploy"
        )

    def test_antenna_deployment_ready_fault(self):
        """DEFECT FIX #4: deploy_antennas should fail if deployment_ready=False."""
        model = self._make_model()

        # Inject fault: set deployment_ready to False
        model._state.antenna_deployment_ready = False

        result = model.handle_command({"command": "deploy_antennas"})
        assert result["success"] is False, (
            f"deploy_antennas should fail when deployment_ready=False, got: {result}"
        )
        assert "not ready" in result["message"].lower() or "fault" in result["message"].lower(), (
            f"Error message should mention fault, got: {result['message']}"
        )

        # Antenna should remain stowed
        assert model._state.antenna_deployment_sensor == 1, (
            "antenna_deployment_sensor should remain 1 (stowed) after failed deploy"
        )

    # ------------------------------------------------------------------
    # DEFECT FIX #3: PA on/off command correctly rejects during thermal shutdown
    # ------------------------------------------------------------------
    def test_pa_on_rejected_during_overheat_shutdown(self):
        """DEFECT FIX #3: pa_on command should fail when pa_overheat_shutdown=True."""
        model = self._make_model()

        # First, turn PA off explicitly
        model.handle_command({"command": "pa_off"})
        assert model._state.pa_on is False, (
            "PA should be off after pa_off command"
        )

        # Simulate overheat shutdown active
        model._state.pa_overheat_shutdown = True
        model._state.pa_temp = 75.0

        # Try to turn PA on while overheat shutdown is active
        result = model.handle_command({"command": "pa_on"})
        assert result["success"] is False, (
            f"pa_on should fail when pa_overheat_shutdown=True, got: {result}"
        )
        assert "overheat" in result["message"].lower(), (
            f"Error message should mention overheat, got: {result['message']}"
        )

        # PA should remain off
        assert model._state.pa_on is False, (
            "PA should remain off when overheat shutdown is active"
        )

    def test_pa_on_succeeds_after_cooldown(self):
        """DEFECT FIX #3: pa_on command should succeed after overheat shutdown clears."""
        model = self._make_model()

        # Simulate thermal recovery: overheat cleared
        model._state.pa_overheat_shutdown = False
        model._state.pa_temp = 50.0

        result = model.handle_command({"command": "pa_on"})
        assert result["success"] is True, (
            f"pa_on should succeed after cooldown, got: {result}"
        )

        assert model._state.pa_on is True, (
            "PA should be on after successful pa_on command"
        )

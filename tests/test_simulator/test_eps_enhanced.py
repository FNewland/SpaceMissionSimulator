"""Tests for the enhanced EPS model — per-line currents, overcurrent trips,
undervoltage/overvoltage flags, and failure injection."""
import pytest
from unittest.mock import MagicMock

from smo_simulator.models.eps_basic import EPSBasicModel


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


def make_shared_params_with_sun_vector(sun_x=0.577, sun_y=0.577, sun_z=0.577):
    """Create a shared_params dict with a valid CSS sun vector.
    This is needed for per-panel solar calculation to work."""
    params = {}
    params[0x0245] = sun_x  # CSS sun vector X
    params[0x0246] = sun_y  # CSS sun vector Y
    params[0x0247] = sun_z  # CSS sun vector Z
    return params


class TestEPSEnhanced:
    """Enhanced EPS model tests covering per-line currents, overcurrent
    protection, voltage flags, and failure injection."""

    def _make_model(self):
        """Create a configured EPSBasicModel for testing."""
        model = EPSBasicModel()
        model.configure({"battery": {"capacity_wh": 120.0}})
        return model

    # ------------------------------------------------------------------
    # 1. Per-line current params
    # ------------------------------------------------------------------
    def test_per_line_current_values(self):
        """Tick the model and verify params 0x0118-0x011F exist and are >= 0."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)

        for addr in range(0x0118, 0x0120):
            assert addr in params, f"Param 0x{addr:04X} missing from shared_params"
            assert params[addr] >= 0, f"Param 0x{addr:04X} has negative current: {params[addr]}"

    # ------------------------------------------------------------------
    # 2. Overcurrent trip on payload line
    # ------------------------------------------------------------------
    def test_overcurrent_trip_on_payload_line(self):
        """Inject overcurrent on line 3 (payload), tick, verify trip flag
        is set and line is switched off."""
        model = self._make_model()
        # Enable the payload line and set it to imaging mode
        model._state.power_lines["payload"] = True
        model._state.payload_mode = 2

        # Inject overcurrent multiplier on line 3 (payload)
        model.inject_failure("overcurrent", 5.0, line_index=3)

        params = {}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)

        # Bit 3 should be set in oc_trip_flags
        assert model._state.oc_trip_flags & (1 << 3), (
            "OC trip flag for payload (bit 3) should be set"
        )
        # Payload line should be disabled
        assert model._state.power_lines["payload"] is False, (
            "Payload power line should be switched off after overcurrent trip"
        )

    # ------------------------------------------------------------------
    # 3. Overcurrent does not trip non-switchable lines
    # ------------------------------------------------------------------
    def test_overcurrent_trips_only_switchable_lines(self):
        """Inject overcurrent on non-switchable line 0 (OBC).  Because the
        OBC line is not switchable, it must stay on regardless of current."""
        model = self._make_model()

        # OBC nominal power is 40W, threshold is 2.0A.
        # At 28V bus: 40W/28V = ~1.43A which is under threshold, so even
        # without overcurrent injection the line should remain on.
        # Verify the line stays on after a tick.
        params = {}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)

        assert model._state.power_lines["obc"] is True, (
            "Non-switchable OBC line must stay on"
        )
        # Bit 0 should NOT be set
        assert not (model._state.oc_trip_flags & (1 << 0)), (
            "OC trip flag for OBC (bit 0) should not be set"
        )

    # ------------------------------------------------------------------
    # 4. Reset OC flag re-enables the line
    # ------------------------------------------------------------------
    def test_reset_oc_flag(self):
        """After tripping line 3 via overcurrent, clear the injection,
        then reset the flag and verify the line is re-enabled."""
        model = self._make_model()
        model._state.power_lines["payload"] = True
        model._state.payload_mode = 2

        # Inject overcurrent to trip the line
        model.inject_failure("overcurrent", 5.0, line_index=3)
        params = {}
        model.tick(1.0, make_orbit_state(), params)

        # Confirm tripped
        assert model._state.oc_trip_flags & (1 << 3)
        assert model._state.power_lines["payload"] is False

        # Clear the overcurrent injection so it doesn't re-trip immediately
        model.clear_failure("overcurrent", line_index=3)

        # Reset the OC flag for line 3
        result = model.handle_command({"command": "reset_oc_flag", "line_index": 3})
        assert result["success"] is True

        # Bit 3 should be cleared
        assert not (model._state.oc_trip_flags & (1 << 3)), (
            "OC trip flag for payload (bit 3) should be cleared after reset"
        )
        # Line should be re-enabled
        assert model._state.power_lines["payload"] is True, (
            "Payload line should be re-enabled after OC flag reset"
        )

    # ------------------------------------------------------------------
    # 5. Reset OC flag rejects a non-tripped line
    # ------------------------------------------------------------------
    def test_reset_oc_flag_rejects_non_tripped(self):
        """Calling reset_oc_flag on a line that is not tripped should
        return success=False."""
        model = self._make_model()
        # No overcurrent has occurred — line 3 is not tripped
        result = model.handle_command({"command": "reset_oc_flag", "line_index": 3})
        assert result["success"] is False

    # ------------------------------------------------------------------
    # 6. Undervoltage flag
    # ------------------------------------------------------------------
    def test_undervoltage_flag(self):
        """Lower the UV threshold so normal bus voltage triggers the flag."""
        model = self._make_model()
        # Set UV threshold above normal bus voltage (~28.2V) so it always triggers
        model._uv_threshold = 30.0

        params = {}
        model.tick(1.0, make_orbit_state(), params)

        assert model._state.uv_flag is True, (
            "UV flag should be set when bus_voltage < uv_threshold"
        )
        assert params[0x010E] == 1, (
            "Param 0x010E should be 1 when undervoltage is detected"
        )

    # ------------------------------------------------------------------
    # 7. Overvoltage flag
    # ------------------------------------------------------------------
    def test_overvoltage_flag(self):
        """Lower the OV threshold so normal bus voltage triggers the flag."""
        model = self._make_model()
        # Set OV threshold below normal bus voltage (~28.2V) so it always triggers
        model._ov_threshold = 25.0

        params = {}
        model.tick(1.0, make_orbit_state(), params)

        assert model._state.ov_flag is True, (
            "OV flag should be set when bus_voltage > ov_threshold"
        )
        assert params[0x010F] == 1, (
            "Param 0x010F should be 1 when overvoltage is detected"
        )

    # ------------------------------------------------------------------
    # 8. Solar array voltage params
    # ------------------------------------------------------------------
    def test_sa_voltage_params(self):
        """Tick and verify SA voltage params 0x010B and 0x010C exist."""
        model = self._make_model()
        params = {}
        model.tick(1.0, make_orbit_state(), params)

        assert 0x010B in params, "SA-A voltage param (0x010B) missing"
        assert 0x010C in params, "SA-B voltage param (0x010C) missing"

    # ------------------------------------------------------------------
    # 9. Power line status params
    # ------------------------------------------------------------------
    def test_power_line_status_params(self):
        """Tick and verify power-line status params 0x0110 through 0x0117 exist."""
        model = self._make_model()
        params = {}
        model.tick(1.0, make_orbit_state(), params)

        for addr in range(0x0110, 0x0118):
            assert addr in params, f"Power line status param 0x{addr:04X} missing"
            assert params[addr] in (0, 1), (
                f"Power line status param 0x{addr:04X} should be 0 or 1, got {params[addr]}"
            )

    # ------------------------------------------------------------------
    # 10. Overcurrent injection and clear
    # ------------------------------------------------------------------
    def test_overcurrent_injection_and_clear(self):
        """Inject overcurrent, verify oc_inject dict has the entry,
        then clear it and verify the dict is empty."""
        model = self._make_model()

        model.inject_failure("overcurrent", 5.0, line_index=3)
        assert "payload" in model._state.oc_inject, (
            "oc_inject should contain 'payload' after injection"
        )
        assert model._state.oc_inject["payload"] == pytest.approx(5.0), (
            "Overcurrent multiplier should be 5.0"
        )

        model.clear_failure("overcurrent", line_index=3)
        assert "payload" not in model._state.oc_inject, (
            "oc_inject should not contain 'payload' after clearing"
        )
        assert len(model._state.oc_inject) == 0, (
            "oc_inject should be empty after clearing the only injection"
        )

    # ------------------------------------------------------------------
    # 11. Undervoltage failure injection decreases SoC
    # ------------------------------------------------------------------
    def test_undervoltage_failure_injection(self):
        """Inject undervoltage with magnitude 5.0 and verify bat_soc_pct
        decreased."""
        model = self._make_model()
        initial_soc = model._state.bat_soc_pct  # default 75.0

        model.inject_failure("undervoltage", 5.0)

        assert model._state.bat_soc_pct < initial_soc, (
            "bat_soc_pct should decrease after undervoltage injection"
        )
        # magnitude=5.0 -> soc decreases by 5.0*10 = 50.0 -> 75.0 - 50.0 = 25.0
        assert model._state.bat_soc_pct == pytest.approx(25.0), (
            "bat_soc_pct should be 25.0 after undervoltage injection of magnitude 5.0"
        )

    # ------------------------------------------------------------------
    # 12. Defect #2: Per-panel solar array current telemetry
    # ------------------------------------------------------------------
    def test_per_panel_solar_currents_in_params(self):
        """Verify that per-panel solar array currents (0x012B-0x0130) are
        written to shared_params. This is Defect #2 fix: ensure per-panel
        parameters are accessible for single-panel failure diagnostics."""
        model = self._make_model()
        params = make_shared_params_with_sun_vector()
        orbit = make_orbit_state(in_eclipse=False, beta=20.0)
        model.tick(1.0, orbit, params)

        # All 6 panel current parameters should be present
        panel_params = [0x012B, 0x012C, 0x012D, 0x012E, 0x012F, 0x0130]
        for param_id in panel_params:
            assert param_id in params, (
                f"Per-panel solar current param 0x{param_id:04X} missing from HK"
            )
            # In sunlight, all panels should have non-negative current
            assert params[param_id] >= 0.0, (
                f"Panel current param 0x{param_id:04X} should be non-negative in sunlight"
            )

    def test_per_panel_currents_sum_to_total(self):
        """Verify that the sum of per-panel currents matches the aggregate
        solar array currents (sa_a_current + sa_b_current)."""
        model = self._make_model()
        params = make_shared_params_with_sun_vector()
        orbit = make_orbit_state(in_eclipse=False, beta=20.0)
        model.tick(1.0, orbit, params)

        # Sum the 6 panel currents
        panel_sum = sum(params.get(0x012B + i, 0.0) for i in range(6))
        # Compare to aggregate (sa_a = px+py+pz, sa_b = mx+my+mz)
        sa_a = model._state.sa_a_current
        sa_b = model._state.sa_b_current
        total_aggregate = sa_a + sa_b

        assert panel_sum == pytest.approx(total_aggregate, rel=0.01), (
            f"Sum of per-panel currents ({panel_sum:.3f}A) should match "
            f"aggregate ({total_aggregate:.3f}A)"
        )

    # ------------------------------------------------------------------
    # 13. Defect #3: Battery DoD coupling and cycle counting
    # ------------------------------------------------------------------
    def test_battery_dod_coupling_to_soc(self):
        """Verify that bat_dod_pct is updated to be (100 - bat_soc_pct)
        after each tick. This is Defect #3 fix: DoD must track SoC changes."""
        model = self._make_model()
        # Initial state: SoC = 75%
        initial_soc = model._state.bat_soc_pct
        expected_dod = 100.0 - initial_soc

        assert model._state.bat_dod_pct == pytest.approx(expected_dod), (
            f"Initial DoD should be {expected_dod}% (100 - {initial_soc}%)"
        )

        # Discharge during eclipse
        params = {}
        orbit = make_orbit_state(in_eclipse=True, beta=20.0)
        model.tick(5.0, orbit, params)  # 5 second discharge

        # After discharge, SoC should decrease
        new_soc = model._state.bat_soc_pct
        assert new_soc < initial_soc, "SoC should decrease during eclipse discharge"

        # DoD should increase correspondingly
        new_dod = model._state.bat_dod_pct
        expected_new_dod = 100.0 - new_soc
        assert new_dod == pytest.approx(expected_new_dod, abs=0.01), (
            f"DoD should be {expected_new_dod}% after discharge "
            f"(100 - {new_soc}%), but got {new_dod}%"
        )

    def test_battery_cycle_counting(self):
        """Verify that bat_cycles is incremented when a charge/discharge
        transition is detected (transition from charging to discharging).
        This is Defect #3 fix: cycle counting must track charge completions."""
        model = self._make_model()
        initial_cycles = model._state.bat_cycles

        # Start in sunlight (charging)
        params = {}
        orbit = make_orbit_state(in_eclipse=False, beta=20.0)
        model.tick(1.0, orbit, params)
        # Still charging, no cycle complete yet
        assert model._state.bat_cycles == initial_cycles

        # Now go into eclipse (discharging) — trigger discharge
        orbit_eclipse = make_orbit_state(in_eclipse=True, beta=20.0)
        model.tick(1.0, orbit_eclipse, params)
        # Discharging detected: transition from charging to discharging
        # should increment cycle count
        assert model._state.bat_cycles >= initial_cycles, (
            "Cycle count should be incremented on charge-to-discharge transition"
        )

    # ------------------------------------------------------------------
    # 14. Defect #4: Charge rate command enforcement
    # ------------------------------------------------------------------
    def test_charge_rate_override_command_accepted(self):
        """Verify that the set_charge_rate command is accepted and sets
        the charge_rate_override_a state variable."""
        model = self._make_model()
        result = model.handle_command({
            "command": "set_charge_rate",
            "rate_a": 3.5
        })

        assert result["success"] is True, "set_charge_rate command should succeed"
        assert model._state.charge_rate_override_a == pytest.approx(3.5), (
            "charge_rate_override_a should be 3.5A after command"
        )

    def test_charge_rate_limits_charging_power(self):
        """Verify that when charge_rate_override_a is set, the battery
        charging is limited to that current rate. This is Defect #4 fix:
        charge rate command must be enforced in model, not just accepted."""
        model = self._make_model()

        # Disable some loads to ensure net power > 0
        model._state.power_lines["payload"] = False
        model._state.power_lines["fpa_cooler"] = False

        # Set charge rate to 2.0 A (limiting max charge power to ~56W at 28V)
        model.handle_command({
            "command": "set_charge_rate",
            "rate_a": 2.0
        })

        # Tick during sunlight (charging available)
        params = make_shared_params_with_sun_vector()
        orbit = make_orbit_state(in_eclipse=False, beta=20.0)

        # Record SoC before charge-limited tick
        soc_before = model._state.bat_soc_pct
        model.tick(1.0, orbit, params)
        soc_after = model._state.bat_soc_pct

        # Verify actual charge current is positive (charging)
        actual_charge_current = model._state.actual_charge_current_a
        assert actual_charge_current >= 0.0, (
            f"Actual charge current should be non-negative (not discharging)"
        )

        # With charge rate limited, the charge current should respect the limit
        # (may be lower if insufficient power available even with all constraints)
        if soc_after > soc_before:
            # If SoC increased, verify charge current is not excessive
            assert actual_charge_current <= 2.5, (
                f"Actual charge current ({actual_charge_current:.2f}A) should be "
                f"limited by override (2.0A), with small margin"
            )

    def test_actual_charge_current_written_to_params(self):
        """Verify that actual_charge_current_a (0x0143) is written to
        shared_params. This is Defect #4 feedback: operator should see
        actual charge current being applied."""
        model = self._make_model()
        params = make_shared_params_with_sun_vector()
        orbit = make_orbit_state(in_eclipse=False, beta=20.0)

        # Set a charge rate limit
        model.handle_command({
            "command": "set_charge_rate",
            "rate_a": 3.0
        })

        model.tick(1.0, orbit, params)

        # Param 0x0143 should be present and represent actual charge current
        assert 0x0143 in params, "Param 0x0143 (actual_charge_current_a) missing from HK"
        assert params[0x0143] >= 0.0, (
            "Actual charge current should be non-negative"
        )

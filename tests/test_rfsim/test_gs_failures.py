"""Tests for ground segment failure injection via the TTC model.

Ground failures are injected through the simulator's instructor interface
(same as spacecraft failures), not through the Radio display.
"""

import pytest
from smo_simulator.models.ttc_basic import TTCBasicModel


class TestGroundSegmentFailures:
    def _make_model(self):
        model = TTCBasicModel()
        model.configure({})
        model._state.antenna_deployed = True
        model._state.antenna_deployment_sensor = 2
        return model

    def test_inject_lna_degradation(self):
        model = self._make_model()
        model.inject_failure("gs_lna_degradation", magnitude=0.5)
        assert model._state.gs_lna_degrade_db == 3.0  # 0.5 * 6

    def test_inject_tracking_loss(self):
        model = self._make_model()
        model.inject_failure("gs_tracking_loss", magnitude=1.0)
        assert model._state.gs_tracking_loss_db == 15.0

    def test_clear_ground_failure(self):
        model = self._make_model()
        model.inject_failure("gs_antenna_mispoint", magnitude=0.8)
        assert model._state.gs_antenna_mispoint_db == 8.0  # 0.8 * 10
        model.clear_failure("gs_antenna_mispoint")
        assert model._state.gs_antenna_mispoint_db == 0.0

    def test_ground_penalty_affects_eb_n0(self):
        """Ground failures should reduce the effective Eb/N0 in the link budget."""
        from unittest.mock import MagicMock
        model = self._make_model()
        orbit = MagicMock()
        orbit.in_contact = True
        orbit.gs_elevation_deg = 45.0
        orbit.gs_azimuth_deg = 90.0
        orbit.gs_range_km = 500.0
        orbit.in_eclipse = False
        orbit.solar_beta_deg = 20.0
        orbit.lat_deg = 45.0
        orbit.lon_deg = 10.0
        orbit.alt_km = 450.0
        orbit.vel_x = 0.0
        orbit.vel_y = 7.5
        orbit.vel_z = 0.0
        params = {}

        # Get baseline after full lock
        for _ in range(15):
            model.tick(1.0, orbit, params)
        baseline_eb_n0 = model._state.eb_n0

        # Inject ground failure
        model.inject_failure("gs_lna_degradation", magnitude=1.0)  # 6 dB
        model.tick(1.0, orbit, params)
        degraded_eb_n0 = model._state.eb_n0

        assert degraded_eb_n0 < baseline_eb_n0 - 4.0, \
            f"Ground LNA failure should reduce Eb/N0 by ~6 dB: {baseline_eb_n0:.1f} → {degraded_eb_n0:.1f}"

    def test_multiple_ground_failures_stack(self):
        model = self._make_model()
        model.inject_failure("gs_lna_degradation", magnitude=1.0)  # 6 dB
        model.inject_failure("gs_feed_loss", magnitude=1.0)  # 4 dB
        model.inject_failure("gs_rfi_interference", magnitude=0.5)  # 4 dB
        total = (model._state.gs_lna_degrade_db
                 + model._state.gs_feed_loss_db
                 + model._state.gs_rfi_db)
        assert abs(total - 14.0) < 0.01  # 6 + 4 + 4

    def test_ground_penalty_exported(self):
        """Ground penalty should be exported via shared_params."""
        from unittest.mock import MagicMock
        model = self._make_model()
        model.inject_failure("gs_tracking_loss", magnitude=1.0)
        orbit = MagicMock()
        orbit.in_contact = False
        orbit.gs_elevation_deg = -90.0
        orbit.gs_azimuth_deg = 0.0
        orbit.gs_range_km = 0.0
        orbit.in_eclipse = False
        orbit.solar_beta_deg = 20.0
        orbit.lat_deg = 0.0
        orbit.lon_deg = 0.0
        orbit.alt_km = 450.0
        orbit.vel_x = 0.0
        orbit.vel_y = 7.5
        orbit.vel_z = 0.0
        params = {}
        model.tick(1.0, orbit, params)
        assert params.get(0x0538, 0) == 15.0  # tracking loss penalty

    def test_all_ground_failure_types(self):
        """All 7 ground failure types should be injectable and clearable."""
        model = self._make_model()
        failures = [
            ("gs_lna_degradation", "gs_lna_degrade_db", 6.0),
            ("gs_antenna_mispoint", "gs_antenna_mispoint_db", 10.0),
            ("gs_feed_loss", "gs_feed_loss_db", 4.0),
            ("gs_rfi_interference", "gs_rfi_db", 8.0),
            ("gs_hpa_degradation", "gs_hpa_degrade_db", 5.0),
            ("gs_ref_osc_drift", "gs_ref_osc_drift_db", 3.0),
            ("gs_tracking_loss", "gs_tracking_loss_db", 15.0),
        ]
        for fail_name, attr_name, max_penalty in failures:
            model.inject_failure(fail_name, magnitude=1.0)
            assert getattr(model._state, attr_name) == max_penalty, \
                f"{fail_name}: expected {max_penalty}, got {getattr(model._state, attr_name)}"
            model.clear_failure(fail_name)
            assert getattr(model._state, attr_name) == 0.0, \
                f"{fail_name} not cleared"

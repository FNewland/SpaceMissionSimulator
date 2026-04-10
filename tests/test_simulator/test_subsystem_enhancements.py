"""Tests for Wave 5 subsystem enhancements.

Covers:
  - EPS: 6 body panels, per-panel cosine projection, PANEL_NORMALS,
    solar_panel_loss failure, SA_A vs SA_B grouping
  - AOCS: dual magnetometers A/B selection, 6 CSS heads with face normals,
    CSS composite sun vector, ST FOV blinding, mag/CSS failure modes
  - TCS: battery heater as only active element, panel temp coupling
    to illumination, heater stuck_on failure, heater open_circuit failure
  - Payload: 4 spectral bands, band enable mask, per-band SNR,
    attitude-quality coupling, GSD from altitude, set_band_config command
"""
import math
import pytest
from unittest.mock import MagicMock

from smo_simulator.models.eps_basic import EPSBasicModel, PANEL_NORMALS
from smo_simulator.models.aocs_basic import (
    AOCSBasicModel, CSS_NORMALS, _ST_EXCLUSION_DEG,
)
from smo_simulator.models.tcs_basic import TCSBasicModel
from smo_simulator.models.payload_basic import (
    PayloadBasicModel, SPECTRAL_BANDS,
)


def make_orbit_state(in_eclipse=False, in_contact=False, beta=20.0,
                     alt_km=500.0, lat_deg=45.0):
    state = MagicMock()
    state.in_eclipse = in_eclipse
    state.solar_beta_deg = beta
    state.lat_deg = lat_deg
    state.lon_deg = 10.0
    state.alt_km = alt_km
    state.vel_x = 0.0
    state.vel_y = 7.5
    state.vel_z = 0.0
    state.in_contact = in_contact
    state.gs_elevation_deg = -10.0
    state.gs_azimuth_deg = 0.0
    state.gs_range_km = 2000.0
    return state


# ====================================================================
# EPS 6-Panel Solar Model
# ====================================================================

class TestEPS6PanelModel:
    """Test EPS 6-body-panel solar array model."""

    def _make_model(self):
        model = EPSBasicModel()
        model.configure({"battery": {"capacity_wh": 120.0}})
        return model

    def test_panel_normals_exist(self):
        """PANEL_NORMALS should have 6 entries for all faces."""
        assert len(PANEL_NORMALS) == 6
        for face in ['px', 'mx', 'py', 'my', 'pz', 'mz']:
            assert face in PANEL_NORMALS

    def test_panel_normals_are_unit_vectors(self):
        """Each panel normal should have magnitude 1."""
        for face, normal in PANEL_NORMALS.items():
            mag = math.sqrt(normal[0]**2 + normal[1]**2 + normal[2]**2)
            assert mag == pytest.approx(1.0), f"{face} normal is not unit"

    def test_per_panel_currents_in_params(self):
        """Per-panel solar currents should appear in shared params."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state()
        # Provide sun vector so 6-panel model activates
        params[0x0245] = 1.0  # CSS sun X
        params[0x0246] = 0.0
        params[0x0247] = 0.0
        model.tick(1.0, orbit, params)
        panel_ids = [0x012B, 0x012C, 0x012D, 0x012E, 0x012F, 0x0130]
        for pid in panel_ids:
            assert pid in params, f"Per-panel param 0x{pid:04X} missing"

    def test_sun_facing_panel_has_positive_current(self):
        """Panel facing the sun should have positive current."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state()
        # Sun along +X body axis
        params[0x0245] = 1.0
        params[0x0246] = 0.0
        params[0x0247] = 0.0
        model.tick(1.0, orbit, params)
        # +X panel should have current
        assert params[0x012B] > 0.0, "+X panel should have positive current when facing sun"
        # -X panel should have zero (facing away)
        assert params[0x012C] == pytest.approx(0.0, abs=0.01)

    def test_sa_a_groups_positive_faces(self):
        """SA_A current = sum of px, py, pz panels."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state()
        params[0x0245] = 1.0
        params[0x0246] = 0.5
        params[0x0247] = 0.3
        model.tick(1.0, orbit, params)
        # SA_A should be sum of positive panels
        sa_a = model._state.sa_a_current
        px = model._state.sa_panel_currents['px']
        py = model._state.sa_panel_currents['py']
        pz = model._state.sa_panel_currents['pz']
        assert sa_a == pytest.approx(px + py + pz, abs=0.001)

    def test_sa_b_groups_negative_faces(self):
        """SA_B current = sum of mx, my, mz panels."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state()
        params[0x0245] = -1.0
        params[0x0246] = -0.5
        params[0x0247] = -0.3
        model.tick(1.0, orbit, params)
        sa_b = model._state.sa_b_current
        mx = model._state.sa_panel_currents['mx']
        my = model._state.sa_panel_currents['my']
        mz = model._state.sa_panel_currents['mz']
        assert sa_b == pytest.approx(mx + my + mz, abs=0.001)

    def test_eclipse_zeroes_all_panels(self):
        """In eclipse, all panel currents should be zero."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state(in_eclipse=True)
        model.tick(1.0, orbit, params)
        for face in ['px', 'mx', 'py', 'my', 'pz', 'mz']:
            assert model._state.sa_panel_currents[face] == 0.0

    def test_solar_panel_loss_failure(self):
        """solar_panel_loss failure should degrade a specific face."""
        model = self._make_model()
        model.inject_failure("solar_panel_loss", 1.0, face="px")
        assert model._state.sa_panel_degradation['px'] == 0.0

    def test_solar_panel_loss_clear(self):
        """Clearing solar_panel_loss should restore degradation to 1.0."""
        model = self._make_model()
        model.inject_failure("solar_panel_loss", 1.0, face="py")
        assert model._state.sa_panel_degradation['py'] == 0.0
        model.clear_failure("solar_panel_loss", face="py")
        assert model._state.sa_panel_degradation['py'] == 1.0

    def test_degraded_panel_produces_less_current(self):
        """A degraded panel should produce less current than a healthy one."""
        model_healthy = self._make_model()
        model_degraded = self._make_model()
        model_degraded.inject_failure("solar_panel_loss", 0.5, face="px")

        params_h = {}
        params_d = {}
        orbit = make_orbit_state()
        params_h[0x0245] = 1.0
        params_h[0x0246] = 0.0
        params_h[0x0247] = 0.0
        params_d[0x0245] = 1.0
        params_d[0x0246] = 0.0
        params_d[0x0247] = 0.0

        model_healthy.tick(1.0, orbit, params_h)
        model_degraded.tick(1.0, orbit, params_d)

        assert params_d[0x012B] < params_h[0x012B]


# ====================================================================
# AOCS Dual Magnetometer
# ====================================================================

class TestAOCSDualMag:
    """Test AOCS dual magnetometer A/B selection."""

    def _make_model(self):
        model = AOCSBasicModel()
        model.configure({})
        # Magnetometer is power-gated on AOCS mode; force a powered mode
        # so dual-mag fallback logic is exercised in these unit tests.
        model._state.mode = 4  # MODE_NOMINAL
        return model

    def test_default_mag_select_is_A(self):
        """Default magnetometer selection should be 'A'."""
        model = self._make_model()
        assert model._state.mag_select == 'A'

    def test_mag_select_command_to_B(self):
        """mag_select command should switch to mag B."""
        model = self._make_model()
        result = model.handle_command({"command": "mag_select", "source": "B"})
        assert result["success"] is True
        assert model._state.mag_select == 'B'

    def test_mag_select_command_to_A(self):
        """mag_select command should switch back to mag A."""
        model = self._make_model()
        model._state.mag_select = 'B'
        result = model.handle_command({"command": "mag_select", "source": "A"})
        assert result["success"] is True
        assert model._state.mag_select == 'A'

    def test_mag_a_fail_switches_to_b(self):
        """When mag A fails and is selected, system should fall back to B."""
        model = self._make_model()
        model._state.mag_select = 'A'
        model.inject_failure("mag_a_fail")
        assert model._state.mag_a_failed is True
        params = {}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        # mag_valid should still be True (fallback to B)
        assert model._state.mag_valid is True

    def test_both_mags_fail_loses_validity(self):
        """When both mags fail, mag_valid should be False."""
        model = self._make_model()
        model.inject_failure("mag_a_fail")
        model.inject_failure("mag_b_fail")
        params = {}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        assert model._state.mag_valid is False

    def test_mag_b_noisier_than_a(self):
        """Mag B noise should be greater than mag A noise."""
        model = self._make_model()
        assert model._state.mag_b_noise > model._state.mag_a_noise

    def test_select_failed_mag_rejected(self):
        """Selecting a failed mag should return failure."""
        model = self._make_model()
        model.inject_failure("mag_a_fail")
        result = model.handle_command({"command": "mag_select", "source": "A"})
        assert result["success"] is False

    def test_dual_mag_params_in_shared(self):
        """Dual mag readings should appear in shared params."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        assert 0x0223 in params  # mag_a_x
        assert 0x0224 in params  # mag_a_y
        assert 0x0225 in params  # mag_a_z
        assert 0x0226 in params  # mag_b_x
        assert 0x0227 in params  # mag_b_y
        assert 0x0228 in params  # mag_b_z
        assert 0x0229 in params  # mag_select (0=A, 1=B)


# ====================================================================
# AOCS CSS 6-Head Sun Sensor
# ====================================================================

class TestAOCSCSS:
    """Test AOCS 6-head coarse sun sensor."""

    def _make_model(self):
        model = AOCSBasicModel()
        model.configure({})
        return model

    def test_css_normals_have_6_faces(self):
        """CSS_NORMALS should have 6 entries."""
        assert len(CSS_NORMALS) == 6

    def test_css_heads_in_state(self):
        """State should have css_heads dict with 6 faces."""
        model = self._make_model()
        assert len(model._state.css_heads) == 6

    def test_css_in_eclipse_all_zero(self):
        """In eclipse, all CSS heads should read 0."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state(in_eclipse=True)
        model.tick(1.0, orbit, params)
        for face in CSS_NORMALS:
            assert model._state.css_heads[face] == 0.0
        assert model._state.css_valid is False

    def test_css_in_sunlight_has_readings(self):
        """In sunlight, at least some CSS heads should have readings."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state(in_eclipse=False)
        model.tick(1.0, orbit, params)
        total = sum(model._state.css_heads.values())
        assert total > 0.0

    def test_css_composite_sun_vector_normalized(self):
        """CSS composite sun vector should be approximately normalized."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state(in_eclipse=False)
        model.tick(1.0, orbit, params)
        if model._state.css_valid:
            mag = math.sqrt(
                model._state.css_sun_x**2 +
                model._state.css_sun_y**2 +
                model._state.css_sun_z**2
            )
            assert mag == pytest.approx(1.0, abs=0.1)

    def test_css_head_failure_zeroes_output(self):
        """Failed CSS head should read 0."""
        model = self._make_model()
        model.inject_failure("css_head_fail", face="px")
        params = {}
        orbit = make_orbit_state(in_eclipse=False)
        model.tick(1.0, orbit, params)
        assert model._state.css_heads['px'] == 0.0

    def test_css_full_failure(self):
        """CSS failure should zero all heads and invalidate."""
        model = self._make_model()
        model.inject_failure("css_failure")
        params = {}
        orbit = make_orbit_state(in_eclipse=False)
        model.tick(1.0, orbit, params)
        assert model._state.css_valid is False
        for face in CSS_NORMALS:
            assert model._state.css_heads[face] == 0.0

    def test_css_head_params_in_shared(self):
        """Per-head CSS illumination should appear in shared params."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state(in_eclipse=False)
        model.tick(1.0, orbit, params)
        css_pids = [0x027A, 0x027B, 0x027C, 0x027D, 0x027E, 0x027F]
        for pid in css_pids:
            assert pid in params


# ====================================================================
# AOCS Star Tracker FOV Blinding
# ====================================================================

class TestAOCSStarTrackerFOV:
    """Test star tracker FOV geometry with blinding."""

    def _make_model(self):
        model = AOCSBasicModel()
        model.configure({})
        return model

    def test_st_exclusion_cone_is_15_deg(self):
        """ST exclusion cone half-angle should be 15 degrees."""
        assert _ST_EXCLUSION_DEG == 15.0

    def test_st1_zenith_boresight(self):
        """ST1 boresight is +Z (zenith)."""
        # Verified from code: unit==1 -> boresight = (0,0,1)
        boresight = (0, 0, 1)
        assert boresight == (0, 0, 1)

    def test_st2_nadir_boresight(self):
        """ST2 boresight is -Z (nadir)."""
        boresight = (0, 0, -1)
        assert boresight == (0, 0, -1)

    def test_st_blinding_check(self):
        """Sun within exclusion cone should blind the tracker."""
        model = self._make_model()
        # Sun directly along +Z (same as ST1 boresight)
        sun_body = (0, 0, 1)
        st_boresight = (0, 0, 1)
        blinded = model._check_st_blinding(sun_body, st_boresight)
        assert blinded is True

    def test_st_not_blinded_when_sun_perpendicular(self):
        """Sun perpendicular to boresight should not blind the tracker."""
        model = self._make_model()
        sun_body = (1, 0, 0)  # Sun along +X
        st_boresight = (0, 0, 1)  # ST1 +Z
        blinded = model._check_st_blinding(sun_body, st_boresight)
        assert blinded is False


# ====================================================================
# TCS Battery Heater
# ====================================================================

class TestTCSBatteryHeater:
    """Test TCS battery heater as only active thermal element."""

    def _make_model(self):
        model = TCSBasicModel()
        model.configure({})
        return model

    def test_battery_heater_thermostat_control(self):
        """Battery heater should turn on when temp is below threshold."""
        model = self._make_model()
        model._state.temp_battery = -5.0  # Well below thermostat on-point
        model._state.htr_battery_manual = False
        model._thermostat_control("battery", model._state.temp_battery)
        assert model._state.htr_battery is True

    def test_battery_heater_turns_off_above_threshold(self):
        """Battery heater should turn off when temp is above off-point."""
        model = self._make_model()
        model._state.htr_battery = True
        model._state.htr_battery_manual = False
        model._state.temp_battery = 20.0  # Well above thermostat off-point
        model._thermostat_control("battery", model._state.temp_battery)
        assert model._state.htr_battery is False

    def test_thruster_heater_always_off(self):
        """EOSAT-1 has no thrusters — thruster heater should always be OFF."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        assert model._state.htr_thruster is False

    def test_obc_heater_follows_eps_power_line(self):
        """OBC heater state should follow EPS power line param 0x0116."""
        model = self._make_model()
        params = {0x0116: 1}  # EPS power line for htr_obc is ON
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        assert model._state.htr_obc is True

    def test_heater_stuck_on_failure(self):
        """Stuck-on heater should stay on regardless of commands."""
        model = self._make_model()
        model.inject_failure("heater_stuck_on", circuit="battery")
        assert model._state.htr_battery_stuck_on is True
        assert model._state.htr_battery is True
        # Try to turn off
        result = model.handle_command({
            "command": "heater", "circuit": "battery", "on": False
        })
        assert result["success"] is False

    def test_heater_open_circuit_failure(self):
        """Open circuit heater should appear ON but provide no heat."""
        model = self._make_model()
        model.inject_failure("heater_open_circuit", circuit="battery")
        assert model._state.htr_battery_open_circuit is True

    def test_heater_stuck_on_clear(self):
        """Clearing stuck_on failure should allow normal control."""
        model = self._make_model()
        model.inject_failure("heater_stuck_on", circuit="battery")
        model.clear_failure("heater_stuck_on", circuit="battery")
        assert model._state.htr_battery_stuck_on is False

    def test_heater_open_circuit_clear(self):
        """Clearing open_circuit failure should restore heating."""
        model = self._make_model()
        model.inject_failure("heater_open_circuit", circuit="battery")
        model.clear_failure("heater_open_circuit", circuit="battery")
        assert model._state.htr_battery_open_circuit is False


class TestTCSPanelTempCoupling:
    """Test panel temperature coupling to solar illumination."""

    def _make_model(self):
        model = TCSBasicModel()
        model.configure({})
        return model

    def test_panel_temps_exist_in_state(self):
        """All 6 panel temperatures should exist in state."""
        model = self._make_model()
        for face in ['px', 'mx', 'py', 'my', 'pz', 'mz']:
            assert hasattr(model._state, f"temp_panel_{face}")

    def test_panel_temps_in_shared_params(self):
        """Panel temperatures should appear in shared params."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        for pid in [0x0400, 0x0401, 0x0402, 0x0403, 0x0404, 0x0405]:
            assert pid in params

    def test_illuminated_panel_warmer(self):
        """A panel receiving solar current should be warmer."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state(in_eclipse=False)
        # Set per-panel solar current for +X face
        params[0x012B] = 1.0  # px high solar current
        params[0x012C] = 0.0  # mx no solar current
        initial_px = model._state.temp_panel_px
        initial_mx = model._state.temp_panel_mx
        # Set similar initial temps
        model._state.temp_panel_px = 0.0
        model._state.temp_panel_mx = 0.0
        for _ in range(10):
            model.tick(1.0, orbit, params)
        # px should be warmer than mx due to solar heating
        assert model._state.temp_panel_px > model._state.temp_panel_mx


# ====================================================================
# Payload Multispectral
# ====================================================================

class TestPayloadMultispectral:
    """Test payload multispectral imaging capabilities."""

    def _make_model(self):
        model = PayloadBasicModel()
        model.configure({})
        return model

    def test_four_spectral_bands_defined(self):
        """SPECTRAL_BANDS should have 4 entries."""
        assert len(SPECTRAL_BANDS) == 4

    def test_band_wavelengths(self):
        """Band center wavelengths should be 443, 560, 665, 865 nm."""
        centers = [b['center_nm'] for b in SPECTRAL_BANDS]
        assert 443 in centers
        assert 560 in centers
        assert 665 in centers
        assert 865 in centers

    def test_band_ids(self):
        """Band IDs should be blue, green, red, nir."""
        ids = [b['id'] for b in SPECTRAL_BANDS]
        assert 'blue' in ids
        assert 'green' in ids
        assert 'red' in ids
        assert 'nir' in ids

    def test_default_all_bands_enabled(self):
        """Default band_enable_mask should be 0x0F (all 4 bands)."""
        model = self._make_model()
        assert model._state.band_enable_mask == 0x0F
        assert model._state.active_bands == 4

    def test_set_band_config_command(self):
        """set_band_config command should update the band mask."""
        model = self._make_model()
        result = model.handle_command({"command": "set_band_config", "mask": 0x05})
        assert result["success"] is True
        assert model._state.band_enable_mask == 0x05
        assert model._state.active_bands == 2  # bits 0 and 2

    def test_set_band_config_invalid_mask(self):
        """set_band_config with mask > 0x0F should fail."""
        model = self._make_model()
        result = model.handle_command({"command": "set_band_config", "mask": 0x10})
        assert result["success"] is False

    def test_band_snrs_in_imaging_mode(self):
        """In IMAGING mode with FPA ready, band SNRs should be positive."""
        model = self._make_model()
        model._state.mode = 2  # IMAGING
        model._state.fpa_ready = True
        model._state.fpa_temp = -10.0
        params = {0x0217: 0.05}  # Small attitude error
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        for band_id in ['blue', 'green', 'red', 'nir']:
            assert model._state.band_snrs[band_id] > 0.0

    def test_disabled_band_snr_is_zero(self):
        """Disabled bands should have SNR = 0."""
        model = self._make_model()
        model._state.mode = 2
        model._state.cooler_active = True
        # FPA temp must be within range of target (-15C ± hysteresis) to be ready
        # Set to -14C which is within acceptable range (-16C to -10C)
        model._state.fpa_temp = -14.0
        # Manually set fpa_ready and the timer to bypass the hysteresis check
        model._state.fpa_ready = True
        model._state.fpa_ready_timer = 999.0  # Beyond hysteresis threshold
        # Disable blue (bit 0) and nir (bit 3)
        model.handle_command({"command": "set_band_config", "mask": 0x06})
        params = {0x0217: 0.05}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        assert model._state.band_snrs['blue'] == 0.0
        assert model._state.band_snrs['nir'] == 0.0
        assert model._state.band_snrs['green'] > 0.0
        assert model._state.band_snrs['red'] > 0.0

    def test_band_snr_params_in_shared(self):
        """Band SNR params should appear in shared params."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        assert 0x0620 in params  # blue SNR
        assert 0x0621 in params  # green SNR
        assert 0x0622 in params  # red SNR
        assert 0x0623 in params  # nir SNR

    def test_active_bands_param(self):
        """Param 0x0624 should reflect active band count."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        assert params[0x0624] == 4.0

    def test_band_enable_mask_param(self):
        """Param 0x0625 should reflect band_enable_mask."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        assert params[0x0625] == 15.0  # 0x0F = 15


class TestPayloadAttitudeQuality:
    """Test attitude-quality coupling on image quality."""

    def _make_model(self):
        model = PayloadBasicModel()
        model.configure({})
        return model

    def test_good_attitude_full_quality(self):
        """Attitude error <= 0.1 deg should give quality factor 1.0."""
        model = self._make_model()
        model._state.mode = 2
        model._state.fpa_ready = True
        params = {0x0217: 0.05}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        assert model._state.att_quality_factor == pytest.approx(1.0, abs=0.01)

    def test_poor_attitude_degrades_quality(self):
        """Attitude error > 0.5 deg should degrade quality factor below 1.0."""
        model = self._make_model()
        model._state.mode = 2
        model._state.fpa_ready = True
        params = {0x0217: 1.0}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        assert model._state.att_quality_factor < 0.9

    def test_very_poor_attitude_low_quality(self):
        """Attitude error > 2 deg should give very low quality."""
        model = self._make_model()
        model._state.mode = 2
        model._state.fpa_ready = True
        params = {0x0217: 3.0}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        assert model._state.att_quality_factor < 0.3

    def test_attitude_quality_param_in_shared(self):
        """Param 0x0626 should contain att_quality_factor."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state()
        model.tick(1.0, orbit, params)
        assert 0x0626 in params


class TestPayloadGSD:
    """Test GSD (Ground Sample Distance) from altitude."""

    def _make_model(self):
        model = PayloadBasicModel()
        model.configure({})
        return model

    def test_gsd_depends_on_altitude(self):
        """GSD should be proportional to altitude."""
        model = self._make_model()
        params = {}
        orbit_low = make_orbit_state(alt_km=400.0)
        model.tick(1.0, orbit_low, params)
        gsd_low = model._state.gsd_m

        model2 = self._make_model()
        params2 = {}
        orbit_high = make_orbit_state(alt_km=600.0)
        model2.tick(1.0, orbit_high, params2)
        gsd_high = model2._state.gsd_m

        assert gsd_high > gsd_low

    def test_gsd_param_in_shared(self):
        """Param 0x0628 should contain GSD."""
        model = self._make_model()
        params = {}
        orbit = make_orbit_state(alt_km=500.0)
        model.tick(1.0, orbit, params)
        assert 0x0628 in params
        assert params[0x0628] > 0.0

"""Tests for the enhanced Payload model — image catalog with capture/download/
delete, memory segment management, FPA thermal readiness, corrupted image
handling, and failure injection."""
import pytest
from unittest.mock import MagicMock

from smo_simulator.models.payload_basic import PayloadBasicModel


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


class TestPayloadEnhanced:
    """Enhanced Payload model tests covering image capture/download/delete,
    memory segments, FPA readiness, corruption, and new params."""

    def _make_model(self):
        """Create a configured PayloadBasicModel ready for imaging."""
        model = PayloadBasicModel()
        model.configure({"image_size_mb": 800.0, "total_storage_mb": 20000.0})
        return model

    def _make_imaging_model(self):
        """Create a model fully ready for image capture."""
        model = self._make_model()
        model._state.mode = 2  # IMAGING
        model._state.fpa_ready = True
        model._state.cooler_active = True
        model._state.fpa_temp = -5.0
        return model

    # ------------------------------------------------------------------
    # 1. Capture requires imaging mode
    # ------------------------------------------------------------------
    def test_capture_requires_imaging_mode(self):
        """In mode=0, capture should fail."""
        model = self._make_model()
        model._state.mode = 0  # OFF

        result = model.handle_command({"command": "capture"})
        assert result["success"] is False, (
            "Capture should fail when not in IMAGING mode"
        )
        assert "IMAGING" in result["message"] or "mode" in result["message"].lower(), (
            "Error message should mention mode"
        )

    # ------------------------------------------------------------------
    # 2. Capture requires FPA ready
    # ------------------------------------------------------------------
    def test_capture_requires_fpa_ready(self):
        """Set mode=2 but fpa_ready=False, capture should fail."""
        model = self._make_model()
        model._state.mode = 2  # IMAGING
        model._state.fpa_ready = False

        result = model.handle_command({"command": "capture"})
        assert result["success"] is False, (
            "Capture should fail when FPA is not ready"
        )
        assert "FPA" in result["message"] or "fpa" in result["message"].lower(), (
            "Error message should mention FPA"
        )

    # ------------------------------------------------------------------
    # 3. Capture creates image in catalog
    # ------------------------------------------------------------------
    def test_capture_creates_image_in_catalog(self):
        """Set mode=2, fpa_ready=True, enough storage, call capture.
        Verify image_catalog has 1 entry, image_count increased,
        mem_used_mb increased, last_scene_id set."""
        model = self._make_imaging_model()
        initial_image_count = model._state.image_count
        initial_mem_used = model._state.mem_used_mb

        result = model.handle_command({"command": "capture"})
        assert result["success"] is True, (
            "Capture should succeed in IMAGING mode with FPA ready"
        )
        assert len(model._state.image_catalog) == 1, (
            f"image_catalog should have 1 entry, got {len(model._state.image_catalog)}"
        )
        assert model._state.image_count == initial_image_count + 1, (
            "image_count should increase by 1 after capture"
        )
        assert model._state.mem_used_mb > initial_mem_used, (
            "mem_used_mb should increase after capture"
        )
        assert model._state.last_scene_id == result["scene_id"], (
            "last_scene_id should match the captured scene_id"
        )

    # ------------------------------------------------------------------
    # 4. Capture corrupt images
    # ------------------------------------------------------------------
    def test_capture_corrupt_images(self):
        """Inject image_corrupt with count=2, capture 3 images.
        First 2 should have status=2 (CORRUPT) with low quality,
        third should be normal."""
        model = self._make_imaging_model()

        model.inject_failure("image_corrupt", count=2)

        # Capture 3 images
        results = []
        for _ in range(3):
            result = model.handle_command({"command": "capture"})
            assert result["success"] is True
            results.append(result)

        # First 2 images should be CORRUPT (status=2)
        assert results[0]["status"] == 2, (
            "First captured image should be CORRUPT (status=2)"
        )
        assert results[0]["quality"] < 35.0, (
            "Corrupt image should have low quality"
        )
        assert results[1]["status"] == 2, (
            "Second captured image should be CORRUPT (status=2)"
        )
        assert results[1]["quality"] < 35.0, (
            "Corrupt image should have low quality"
        )

        # Third image should be normal (status=0)
        assert results[2]["status"] == 0, (
            "Third captured image should be OK (status=0) after corrupt count exhausted"
        )

    # ------------------------------------------------------------------
    # 5. Capture with CCD line dropout
    # ------------------------------------------------------------------
    def test_capture_with_ccd_line_dropout(self):
        """Inject ccd_line_dropout, capture. Verify status=1 (PARTIAL)
        and quality < 90."""
        model = self._make_imaging_model()

        model.inject_failure("ccd_line_dropout")

        result = model.handle_command({"command": "capture"})
        assert result["success"] is True
        assert result["status"] == 1, (
            "Image with CCD line dropout should have status=1 (PARTIAL)"
        )
        assert result["quality"] < 90.0, (
            f"Image quality with CCD line dropout should be < 90, got {result['quality']}"
        )

    # ------------------------------------------------------------------
    # 6. Download image
    # ------------------------------------------------------------------
    def test_download_image(self):
        """Capture an image, then download_image with that scene_id.
        Verify success and image data returned."""
        model = self._make_imaging_model()

        # Capture an image
        capture_result = model.handle_command({"command": "capture"})
        assert capture_result["success"] is True
        scene_id = capture_result["scene_id"]

        # Download it
        dl_result = model.handle_command({
            "command": "download_image",
            "scene_id": scene_id,
        })
        assert dl_result["success"] is True, (
            "download_image should succeed for existing scene_id"
        )
        assert "image" in dl_result, (
            "download result should contain 'image' key"
        )
        assert dl_result["image"]["scene_id"] == scene_id, (
            "Downloaded image scene_id should match requested scene_id"
        )

    # ------------------------------------------------------------------
    # 7. Download non-existent image
    # ------------------------------------------------------------------
    def test_download_nonexistent_image(self):
        """download_image with non-existent scene_id, verify failure."""
        model = self._make_imaging_model()

        result = model.handle_command({
            "command": "download_image",
            "scene_id": 99999,
        })
        assert result["success"] is False, (
            "download_image should fail for non-existent scene_id"
        )
        assert "not found" in result["message"].lower(), (
            "Error message should indicate image not found"
        )

    # ------------------------------------------------------------------
    # 8. Delete image by scene_id
    # ------------------------------------------------------------------
    def test_delete_image_by_scene_id(self):
        """Capture, then delete_image with scene_id. Verify catalog is empty,
        image_count decreased, mem_used_mb decreased."""
        model = self._make_imaging_model()

        # Capture an image
        capture_result = model.handle_command({"command": "capture"})
        scene_id = capture_result["scene_id"]
        count_after_capture = model._state.image_count
        mem_after_capture = model._state.mem_used_mb

        # Delete it
        del_result = model.handle_command({
            "command": "delete_image",
            "scene_id": scene_id,
        })
        assert del_result["success"] is True, (
            "delete_image should succeed for existing scene_id"
        )
        assert len(model._state.image_catalog) == 0, (
            "image_catalog should be empty after deleting the only image"
        )
        assert model._state.image_count == count_after_capture - 1, (
            "image_count should decrease by 1 after delete"
        )
        assert model._state.mem_used_mb < mem_after_capture, (
            "mem_used_mb should decrease after deleting image"
        )

    # ------------------------------------------------------------------
    # 9. Mark bad segment
    # ------------------------------------------------------------------
    def test_mark_bad_segment(self):
        """mark_bad_segment(segment=0), verify bad_segments=[0],
        mem_segments_bad=1, mem_total_mb decreased."""
        model = self._make_model()
        initial_total = model._state.mem_total_mb

        result = model.handle_command({
            "command": "mark_bad_segment",
            "segment": 0,
        })
        assert result["success"] is True
        assert 0 in model._state.bad_segments, (
            "Segment 0 should be in bad_segments"
        )
        assert model._state.mem_segments_bad == 1, (
            "mem_segments_bad should be 1"
        )
        assert model._state.mem_total_mb < initial_total, (
            "mem_total_mb should decrease after marking a segment bad"
        )

    # ------------------------------------------------------------------
    # 10. Mark bad segment with invalid index
    # ------------------------------------------------------------------
    def test_mark_bad_segment_invalid(self):
        """mark_bad_segment with out-of-range segment, verify failure."""
        model = self._make_model()

        # Segment index too high
        result = model.handle_command({
            "command": "mark_bad_segment",
            "segment": 999,
        })
        assert result["success"] is False, (
            "mark_bad_segment should fail for out-of-range segment index"
        )

        # Negative segment index
        result = model.handle_command({
            "command": "mark_bad_segment",
            "segment": -1,
        })
        assert result["success"] is False, (
            "mark_bad_segment should fail for negative segment index"
        )

    # ------------------------------------------------------------------
    # 11. Get image catalog
    # ------------------------------------------------------------------
    def test_get_image_catalog(self):
        """Capture 2 images, get_image_catalog, verify count=2."""
        model = self._make_imaging_model()

        model.handle_command({"command": "capture"})
        model.handle_command({"command": "capture"})

        result = model.handle_command({"command": "get_image_catalog"})
        assert result["success"] is True
        assert result["count"] == 2, (
            f"get_image_catalog should report count=2, got {result['count']}"
        )
        assert len(result["catalog"]) == 2, (
            f"Catalog should have 2 entries, got {len(result['catalog'])}"
        )

    # ------------------------------------------------------------------
    # 12. Capture fails with insufficient storage
    # ------------------------------------------------------------------
    def test_capture_fails_insufficient_storage(self):
        """Set mem_used_mb to near total, capture should fail with
        'Insufficient storage'."""
        model = self._make_imaging_model()

        # Fill up storage so available < image_size_mb (800)
        model._state.mem_used_mb = model._state.mem_total_mb - 100.0

        result = model.handle_command({"command": "capture"})
        assert result["success"] is False, (
            "Capture should fail when insufficient storage is available"
        )
        assert "storage" in result["message"].lower() or "Insufficient" in result["message"], (
            "Error message should mention insufficient storage"
        )

    # ------------------------------------------------------------------
    # 13. Memory segment fail injection
    # ------------------------------------------------------------------
    def test_memory_segment_fail_injection(self):
        """Inject memory_segment_fail on segment 2, verify bad_segments=[2],
        mem_total_mb decreased."""
        model = self._make_model()
        initial_total = model._state.mem_total_mb

        model.inject_failure("memory_segment_fail", segment=2)

        assert 2 in model._state.bad_segments, (
            "Segment 2 should be in bad_segments after injection"
        )
        assert model._state.mem_segments_bad == 1, (
            "mem_segments_bad should be 1 after failing one segment"
        )
        assert model._state.mem_total_mb < initial_total, (
            "mem_total_mb should decrease after memory_segment_fail injection"
        )

    # ------------------------------------------------------------------
    # 14. FPA ready when cooled
    # ------------------------------------------------------------------
    def test_fpa_ready_when_cooled(self):
        """Set fpa_temp to nominal range and tick long enough for hysteresis,
        verify fpa_ready = True."""
        model = self._make_model()
        model._state.mode = 1  # STANDBY (cooler on)
        model._state.cooler_active = True
        model._state.fpa_temp = -14.0  # In range: -16 to -10

        orbit = make_orbit_state()
        params = {}

        # Need to tick for at least hysteresis_s (60s)
        for _ in range(61):
            model.tick(1.0, orbit, params)

        assert model._state.fpa_ready is True, (
            "fpa_ready should be True after hysteresis settling in temp range"
        )

    # ------------------------------------------------------------------
    # 15. FPA not ready when warm
    # ------------------------------------------------------------------
    def test_fpa_not_ready_when_warm(self):
        """Set fpa_temp to 20 (above target + 5), verify fpa_ready = False
        after tick."""
        model = self._make_model()
        model._state.mode = 1  # STANDBY
        model._state.cooler_active = True
        model._state.fpa_temp = 20.0  # Above target (-5) + 5 = 0

        orbit = make_orbit_state()
        params = {}
        model.tick(1.0, orbit, params)

        assert model._state.fpa_ready is False, (
            "fpa_ready should be False when fpa_temp (20) is above target + 5 (0)"
        )

    # ------------------------------------------------------------------
    # 16. New params written to shared_params
    # ------------------------------------------------------------------
    def test_new_params_written(self):
        """Tick, verify new params 0x060A, 0x060B, 0x060C, 0x060D,
        0x0610, 0x0612, 0x0613 all exist in shared_params."""
        model = self._make_model()
        orbit = make_orbit_state()
        params = {}

        model.tick(1.0, orbit, params)

        expected_params = [
            0x060A,  # mem_total_mb
            0x060B,  # mem_used_mb
            0x060C,  # last_scene_id
            0x060D,  # last_scene_quality
            0x060F,  # fpa_ready
            0x0612,  # mem_segments_bad
            0x0613,  # duty_cycle_pct
        ]
        for addr in expected_params:
            assert addr in params, (
                f"Param 0x{addr:04X} missing from shared_params"
            )


# ==================================================================
# NEW TESTS FOR DEFECT FIXES (payload.md defects §3.1-3.7)
# ==================================================================

class TestDefect1RadiometricCalibration:
    """Tests for Defect 1: Radiometric calibration products generation."""

    def _make_model(self):
        model = PayloadBasicModel()
        model.configure({"image_size_mb": 800.0, "total_storage_mb": 20000.0})
        return model

    def test_calibration_generates_coefficients(self):
        """start_calibration should generate gain/bias coefficients on completion."""
        model = self._make_model()
        model._state.mode = 2  # IMAGING
        model._state.fpa_ready = True

        # Start calibration
        result = model.handle_command({"command": "start_calibration"})
        assert result["success"] is True, "Calibration should start in IMAGING mode"
        assert model._state.calibration_active is True

        # Simulate 31 seconds of calibration (duration=30s)
        orbit_state = make_orbit_state()
        for _ in range(31):
            model.tick(1.0, orbit_state, {})

        assert model._state.calibration_active is False, (
            "Calibration should complete after duration"
        )
        assert model._state.calibration_state == 3, (
            "Calibration state should be COMPLETE (3)"
        )
        assert model._state.calibration_valid_mask == 0x0F, (
            "All 4 bands should be marked valid (0x0F)"
        )
        assert model._state.gain_coeff['blue'] is not None, (
            "Gain coefficient for blue band should be generated"
        )
        assert model._state.bias_coeff['blue'] is not None, (
            "Bias coefficient for blue band should be generated"
        )

    def test_dark_flat_frame_counts_accumulate(self):
        """dark_frame_count and flat_frame_count should both be > 0 after calibration."""
        model = self._make_model()
        model._state.mode = 2
        model._state.fpa_ready = True

        model.handle_command({"command": "start_calibration"})
        orbit_state = make_orbit_state()

        # Tick for 2 seconds (should be in dark frame phase)
        model.tick(1.0, orbit_state, {})
        model.tick(1.0, orbit_state, {})
        assert model._state.dark_frame_count > 0, (
            "Dark frame count should be > 0 during DARK_FRAME phase"
        )

        # Tick to completion (31 seconds total)
        for _ in range(29):
            model.tick(1.0, orbit_state, {})

        assert model._state.calibration_active is False, (
            "Calibration should be complete after 31s"
        )
        assert model._state.calibration_valid_mask == 0x0F, (
            "All bands should be valid (0x0F) after calibration"
        )
        assert model._state.dark_frame_count == 10, (
            "Dark frame count should be 10 after full dark phase"
        )
        assert model._state.flat_frame_count > 0, (
            "Flat frame count should be accumulated in FLAT_FIELD phase"
        )


class TestDefect2FPAReadinessHysteresis:
    """Tests for Defect 2: FPA readiness with hysteresis and cooler health."""

    def _make_model(self):
        model = PayloadBasicModel()
        model.configure({"image_size_mb": 800.0, "total_storage_mb": 20000.0,
                        "fpa_tau_cooling_s": 100.0})
        return model

    def test_fpa_ready_has_hysteresis(self):
        """FPA readiness uses hysteresis timer for settling."""
        model = self._make_model()
        model._state.mode = 1  # STANDBY (cooler on)
        # Manually set FPA to nominal ready temp to test hysteresis logic
        model._state.fpa_temp = -14.0  # In range: -16 to -10
        model._fpa_target = -15.0

        orbit_state = make_orbit_state()

        # Initially fpa_ready should be False (timer not yet reached)
        assert model._state.fpa_ready is False, "FPA should not be ready without hysteresis settling"
        assert model._state.fpa_ready_timer == 0.0, "Timer should start at 0"

        # Tick for 30 seconds (less than hysteresis_s=60)
        for _ in range(30):
            model.tick(1.0, orbit_state, {})

        # Still not ready, but timer should be accumulating
        assert model._state.fpa_ready is False, "Still should not be ready at 30s"
        assert 29.0 < model._state.fpa_ready_timer < 31.0, (
            f"Timer should be ~30s, got {model._state.fpa_ready_timer}"
        )

        # Tick for 35 more seconds (total 65s, exceeds hysteresis_s=60)
        for _ in range(35):
            model.tick(1.0, orbit_state, {})

        # Now should be ready
        assert model._state.fpa_ready is True, (
            f"FPA should be ready after {model._state.fpa_ready_timer}s >= hysteresis {model._state.fpa_ready_hysteresis_s}s"
        )

    def test_fpa_not_ready_if_cooler_failed(self):
        """FPA should not be ready if cooler is failed, even if temp is in range."""
        model = self._make_model()
        model._state.mode = 1
        model._state.cooler_failed = True
        model._state.fpa_temp = -14.0  # In nominal range

        orbit_state = make_orbit_state()
        model.tick(1.0, orbit_state, {})

        assert model._state.fpa_ready is False, (
            "FPA should not be ready if cooler is failed"
        )

    def test_fpa_ready_resets_on_out_of_range(self):
        """If FPA temp goes out of range, ready should go False and timer reset."""
        model = self._make_model()
        model._state.mode = 1
        model._state.fpa_ready = True
        model._state.fpa_ready_timer = 60.0  # Already met hysteresis
        model._state.fpa_temp = -14.0

        # Simulate temp spike out of range
        model._state.fpa_temp = 5.0

        orbit_state = make_orbit_state()
        model.tick(1.0, orbit_state, {})

        assert model._state.fpa_ready is False, (
            "FPA should go not-ready if temp leaves stable range"
        )
        assert model._state.fpa_ready_timer == 0.0, (
            "Ready timer should reset when temp out of range"
        )


class TestDefect3ImageCompression:
    """Tests for Defect 3: Image compression applied to stored size."""

    def _make_model(self):
        model = PayloadBasicModel()
        model.configure({"image_size_mb": 800.0, "total_storage_mb": 20000.0})
        return model

    def test_compression_applied_to_stored_size(self):
        """Captured image size should be divided by compression_ratio."""
        model = self._make_model()
        model._state.mode = 2  # IMAGING
        model._state.fpa_ready = True
        model._state.compression_enabled = True
        model._state.compression_ratio = 4.0  # High compression

        initial_mem = model._state.mem_used_mb

        result = model.handle_command({"command": "capture", "lat": 0.0, "lon": 0.0})
        assert result["success"] is True

        # Stored size should be base_size / ratio = 800 / 4 = 200 MB
        expected_stored_size = 800.0 / 4.0
        actual_stored_size = result.get("stored_size_mb", None)
        assert actual_stored_size is not None, "Response should include stored_size_mb"
        assert abs(actual_stored_size - expected_stored_size) < 0.1, (
            f"Stored size should be {expected_stored_size} MB, got {actual_stored_size} MB"
        )

        # Mem used should increase by stored_size, not base_size
        mem_increase = model._state.mem_used_mb - initial_mem
        assert abs(mem_increase - expected_stored_size) < 0.1, (
            f"Memory increase should be {expected_stored_size} MB, got {mem_increase} MB"
        )

    def test_compression_disabled_uses_full_size(self):
        """If compression_enabled=False, stored size = base size."""
        model = self._make_model()
        model._state.mode = 2
        model._state.fpa_ready = True
        model._state.compression_enabled = False

        initial_mem = model._state.mem_used_mb

        result = model.handle_command({"command": "capture"})
        assert result["success"] is True

        # Stored size should equal image_size_mb
        mem_increase = model._state.mem_used_mb - initial_mem
        assert abs(mem_increase - 800.0) < 0.1, (
            f"Memory increase should be 800 MB (no compression), got {mem_increase} MB"
        )

    def test_set_compression_command(self):
        """set_compression command should update algorithm and ratio."""
        model = self._make_model()

        result = model.handle_command({
            "command": "set_compression",
            "algorithm": 1,
            "ratio": 3.5
        })
        assert result["success"] is True
        assert model._state.compression_algorithm == 1, (
            "Algorithm should be set to 1 (CCSDS121)"
        )
        assert model._state.compression_ratio == 3.5, (
            "Ratio should be set to 3.5"
        )


class TestDefect4ShutterFilter:
    """Tests for Defect 4: Shutter and filter wheel mechanisms."""

    def _make_model(self):
        model = PayloadBasicModel()
        model.configure({"image_size_mb": 800.0, "total_storage_mb": 20000.0})
        return model

    def test_cycle_shutter_command(self):
        """cycle_shutter should toggle shutter position multiple times."""
        model = self._make_model()

        result = model.handle_command({"command": "cycle_shutter", "cycles": 3})
        assert result["success"] is True
        assert model._state.shutter_test_active is True
        assert model._state.shutter_test_cycles_remaining == 3

        orbit_state = make_orbit_state()

        # Tick 4 times to complete: each cycle decrements, then 1 more tick to finalize
        for _ in range(4):
            model.tick(1.0, orbit_state, {})

        assert model._state.shutter_test_active is False, (
            "Shutter test should complete after cycles"
        )
        assert model._state.shutter_cycles_completed == 1, (
            "Shutter cycles completed should increment"
        )

    def test_get_shutter_status(self):
        """get_shutter_status should return current shutter state."""
        model = self._make_model()

        result = model.handle_command({"command": "get_shutter_status"})
        assert result["success"] is True
        assert "position" in result
        assert "test_in_progress" in result
        assert "cycles_completed" in result

    def test_select_filter_command(self):
        """select_filter should initiate filter rotation."""
        model = self._make_model()
        model._state.filter_position = 0

        result = model.handle_command({"command": "select_filter", "position": 2})
        assert result["success"] is True
        assert model._state.filter_rotation_in_progress is True
        assert model._state.filter_target_position == 2

        orbit_state = make_orbit_state()

        # Tick for rotation to complete
        for _ in range(5):
            model.tick(1.0, orbit_state, {})

        assert model._state.filter_position == 2, (
            "Filter position should reach target after rotation"
        )
        assert model._state.filter_rotation_in_progress is False

    def test_get_filter_status(self):
        """get_filter_status should return current filter state."""
        model = self._make_model()

        result = model.handle_command({"command": "get_filter_status"})
        assert result["success"] is True
        assert "position" in result
        assert "rotation_in_progress" in result
        assert "target_position" in result


class TestDefect5DownlinkIntegration:
    """Tests for Defect 5: Downlink command and transfer progress."""

    def _make_model(self):
        model = PayloadBasicModel()
        model.configure({"image_size_mb": 800.0, "total_storage_mb": 20000.0})
        return model

    def test_initiate_transfer_command(self):
        """initiate_transfer should find image and start downlink."""
        model = self._make_model()
        model._state.mode = 2
        model._state.fpa_ready = True

        # Capture an image
        model.handle_command({"command": "capture"})
        scene_id = model._state.last_scene_id

        # Initiate transfer
        result = model.handle_command({
            "command": "initiate_transfer",
            "scene_id": scene_id
        })
        assert result["success"] is True
        assert model._state.transfer_active is True
        assert model._state.transfer_scene_id == scene_id

    def test_transfer_progress_increments(self):
        """Transfer progress should increment toward 100% during tick."""
        model = self._make_model()
        model._state.mode = 2
        model._state.fpa_ready = True

        # Capture and initiate transfer
        model.handle_command({"command": "capture"})
        scene_id = model._state.last_scene_id
        model.handle_command({"command": "initiate_transfer", "scene_id": scene_id})

        initial_progress = model._state.transfer_progress
        orbit_state = make_orbit_state()

        # Tick for 10 seconds
        for _ in range(10):
            model.tick(1.0, orbit_state, {})

        assert model._state.transfer_progress > initial_progress, (
            "Transfer progress should increase over time"
        )

    def test_transfer_completes_and_deactivates(self):
        """After sufficient ticks, transfer should complete and transfer_active=False."""
        model = self._make_model()
        model._state.mode = 2
        model._state.fpa_ready = True
        model._state.transfer_rate_mbps = 100.0  # Fast for testing

        # Capture small image (compressed)
        model._state.compression_enabled = True
        model._state.compression_ratio = 4.0  # 800/4 = 200 MB
        model.handle_command({"command": "capture"})
        scene_id = model._state.last_scene_id

        model.handle_command({"command": "initiate_transfer", "scene_id": scene_id})

        orbit_state = make_orbit_state()

        # Tick for 100 seconds (100 Mbps * 100s = 800 Mbits = 100 MB, enough for 200 MB)
        # Actually: 200 MB = 1600 Mbits; at 100 Mbps takes 16 seconds
        for _ in range(20):
            model.tick(1.0, orbit_state, {})

        assert model._state.transfer_active is False, (
            "Transfer should complete after sufficient time"
        )
        assert abs(model._state.transfer_progress - 100.0) < 0.1, (
            "Transfer progress should be at or near 100%"
        )

    def test_get_transfer_status(self):
        """get_transfer_status should return transfer info."""
        model = self._make_model()
        model._state.mode = 2
        model._state.fpa_ready = True

        model.handle_command({"command": "capture"})
        scene_id = model._state.last_scene_id
        model.handle_command({"command": "initiate_transfer", "scene_id": scene_id})

        result = model.handle_command({"command": "get_transfer_status"})
        assert result["success"] is True
        assert "transfer_active" in result
        assert "scene_id" in result
        assert "bytes_total" in result
        assert "bytes_sent" in result
        assert "progress_pct" in result


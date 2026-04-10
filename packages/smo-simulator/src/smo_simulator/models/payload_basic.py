"""SMO Simulator — Enhanced Payload Model.

Image catalog with metadata, memory segment model with individual failure,
FPA thermal model, image capture/download/delete commands, corrupted image
handling, comprehensive failure injection, and ocean current multispectral
imaging with per-band SNR, attitude-quality coupling, and scene-dependent
compression.
"""
import random
import time
from dataclasses import dataclass, field
from typing import Any

from smo_common.models.subsystem import SubsystemModel

# ── Spectral band definitions for multispectral imaging ──
SPECTRAL_BANDS = [
    {'id': 'blue', 'center_nm': 443, 'bandwidth_nm': 20, 'snr_nominal': 40.0, 'bit': 0},
    {'id': 'green', 'center_nm': 560, 'bandwidth_nm': 20, 'snr_nominal': 50.0, 'bit': 1},
    {'id': 'red', 'center_nm': 665, 'bandwidth_nm': 20, 'snr_nominal': 48.0, 'bit': 2},
    {'id': 'nir', 'center_nm': 865, 'bandwidth_nm': 40, 'snr_nominal': 42.0, 'bit': 3},
]


@dataclass
class ImageMetadata:
    """Metadata for a captured image."""
    scene_id: int = 0
    timestamp: float = 0.0
    lat: float = 0.0
    lon: float = 0.0
    quality: float = 100.0  # 0-100 pct
    status: int = 0  # 0=OK, 1=PARTIAL, 2=CORRUPT
    size_mb: float = 800.0
    segment: int = 0  # Which memory segment stores it


@dataclass
class PayloadState:
    mode: int = 0  # 0=OFF, 1=STANDBY(cooler on), 2=IMAGING
    fpa_temp: float = 5.0
    cooler_active: bool = False
    imager_temp: float = 5.0
    store_used_pct: float = 20.0
    image_count: int = 0
    current_scene_id: int = 0
    line_rate: float = 0.0
    data_rate_mbps: float = 0.0
    checksum_errors: int = 0
    cooler_on_time_s: float = 0.0
    image_size_mb: float = 800.0
    total_storage_mb: float = 20000.0
    cooler_failed: bool = False
    fpa_degraded: bool = False

    # ── Memory segments ──
    num_segments: int = 8
    segment_size_mb: float = 2500.0  # total_storage / num_segments
    bad_segments: list[int] = field(default_factory=list)  # Indices

    # ── Image catalog ──
    image_catalog: list[dict] = field(default_factory=list)

    # ── Enhanced telemetry ──
    mem_total_mb: float = 20000.0
    mem_used_mb: float = 4000.0
    last_scene_id: int = 0
    last_scene_quality: float = 100.0
    fpa_ready: bool = False
    mem_segments_bad: int = 0
    duty_cycle_pct: float = 0.0

    # ── Phase 4: Flight hardware realism ──
    compression_ratio: float = 2.0   # Image compression ratio (1.0=raw)
    compression_algorithm: int = 3   # 0=none, 1=CCSDS121, 2=CCSDS122, 3=CCSDS123 (Defect 3)
    compression_enabled: bool = True
    cal_lamp_on: bool = False        # Onboard calibration lamp status
    snr: float = 45.0               # Signal-to-noise ratio (dB)
    detector_temp_c: float = -5.0    # CCD/CMOS detector temperature
    integration_time_ms: float = 2.0 # Detector integration time
    swath_width_km: float = 30.0     # Ground swath width

    # ── Multispectral imaging ──
    band_enable_mask: int = 0x0F  # All 4 bands enabled (bits 0-3)
    active_bands: int = 4
    band_snrs: dict = field(default_factory=lambda: {
        'blue': 40.0, 'green': 50.0, 'red': 48.0, 'nir': 42.0
    })
    att_quality_factor: float = 1.0
    att_error_deg: float = 0.0
    gsd_m: float = 10.0
    scene_entropy: float = 0.0

    # ── Failure injection ──
    corrupt_remaining: int = 0  # Next N images are corrupt
    ccd_line_dropout: bool = False  # Reduces image quality

    # ── Radiometric Calibration (Defect 1 — payload.md §3.1) ──
    dark_frame_buffer: dict = field(default_factory=lambda: {
        'blue': None, 'green': None, 'red': None, 'nir': None
    })
    flat_frame_buffer: dict = field(default_factory=lambda: {
        'blue': None, 'green': None, 'red': None, 'nir': None
    })
    gain_coeff: dict = field(default_factory=lambda: {
        'blue': None, 'green': None, 'red': None, 'nir': None
    })
    bias_coeff: dict = field(default_factory=lambda: {
        'blue': None, 'green': None, 'red': None, 'nir': None
    })
    calibration_valid_mask: int = 0x00  # Bitwise flags for 4 bands
    dark_frame_count: int = 0
    flat_frame_count: int = 0

    # ── Calibration state machine ──
    calibration_active: bool = False  # Currently calibrating
    calibration_progress: float = 0.0  # 0-100%
    calibration_state: int = 0  # 0=IDLE, 1=DARK_FRAME, 2=FLAT_FIELD, 3=COMPLETE
    last_calibration_time: float = 0.0  # Timestamp when cal completed
    calibration_duration_s: float = 30.0  # How long calibration takes
    calibration_timer: float = 0.0  # Countdown timer

    # ── Detector settings ──
    integration_time_blue: float = 2.0  # Per-band integration time (ms)
    integration_time_green: float = 2.0
    integration_time_red: float = 2.0
    integration_time_nir: float = 2.0
    detector_gain: float = 1.0  # Detector gain/offset
    cooler_setpoint_c: float = -15.0  # Cooler target temperature
    compression_override: float = 0.0  # 0=auto, else manual ratio

    # ── FPA Readiness Hysteresis (Defect 2 — payload.md §3.2) ──
    fpa_ready_timer: float = 0.0  # Time temp has been in range
    fpa_ready_hysteresis_s: float = 60.0  # Settling time before ready

    # ── Transfer state (Defect 5 — payload.md §3.7) ──
    transfer_active: bool = False
    transfer_scene_id: int = 0
    transfer_bytes_total: int = 0
    transfer_bytes_sent: int = 0
    transfer_rate_mbps: float = 10.0  # TT&C downlink rate
    transfer_progress: float = 0.0  # 0-100%

    # ── Shutter & Filter Wheel (Defect 4 — payload.md §3.3) ──
    shutter_position: int = 1  # 0=CLOSED, 1=OPEN
    shutter_test_active: bool = False
    shutter_test_cycles_remaining: int = 0
    shutter_cycles_completed: int = 0
    filter_position: int = 0
    filter_rotation_in_progress: bool = False
    filter_target_position: int = 0
    filter_rotation_timer: float = 0.0
    filter_rotation_time_s: float = 2.0  # Per 90° rotation

    # ── Edge detection for event generation ──
    prev_mode: int = 0
    prev_fpa_temp: float = 5.0
    prev_store_used_pct: float = 20.0

    # S2 Device Access — device on/off states (device_id -> on/off)
    device_states: dict = field(default_factory=lambda: {
        0x0600: True,   # Focal plane array (FPA)
        0x0601: True,   # FPA cooler
        0x0602: True,   # Calibration lamp
        0x0603: True,   # Shutter mechanism
        0x0604: True,   # Compression unit
    })


class PayloadBasicModel(SubsystemModel):
    """Enhanced payload with image catalog, memory segments, and capture."""

    def __init__(self):
        self._state = PayloadState()
        self._fpa_target = -15.0
        self._fpa_ambient = 5.0
        self._tau_cool = 100.0
        self._tau_warm = 120.0
        self._cooler_power_w = 15.0
        self._line_rate_hz = 500.0
        self._data_rate_mbps = 80.0
        self._param_ids: dict[str, int] = {}
        self._next_scene_id = 100

    @property
    def name(self) -> str:
        return "payload"

    def configure(self, config: dict[str, Any]) -> None:
        self._fpa_target = config.get("fpa_cooler_target_c", -15.0)
        self._fpa_ambient = config.get("fpa_ambient_c", 5.0)
        self._tau_cool = config.get("fpa_tau_cooling_s", 100.0)
        self._tau_warm = config.get("fpa_tau_warming_s", 120.0)
        self._state.image_size_mb = config.get("image_size_mb", 800.0)
        self._state.total_storage_mb = config.get("total_storage_mb", 20000.0)
        self._cooler_power_w = config.get("fpa_cooler_power_w", 15.0)
        self._line_rate_hz = config.get("line_rate_hz", 500.0)
        self._data_rate_mbps = config.get("data_rate_mbps", 80.0)

        # Memory segments
        num_seg = config.get("num_memory_segments", 8)
        self._state.num_segments = num_seg
        self._state.segment_size_mb = (
            self._state.total_storage_mb / num_seg
        )
        self._state.mem_total_mb = self._state.total_storage_mb
        self._state.mem_used_mb = (
            self._state.store_used_pct / 100.0 * self._state.total_storage_mb
        )

        self._param_ids = config.get("param_ids", {
            "pli_mode": 0x0600, "fpa_temp": 0x0601, "cooler_pwr": 0x0602,
            "imager_temp": 0x0603, "store_used": 0x0604,
            "image_count": 0x0605, "scene_id": 0x0606,
            "line_rate": 0x0607, "data_rate": 0x0608,
            "checksum_errors": 0x0609,
            # New params
            "mem_total_mb": 0x060A, "mem_used_mb": 0x060B,
            "last_scene_id": 0x060C, "last_scene_quality": 0x060D,
            "fpa_ready": 0x060F, "mem_segments_bad": 0x0612,
            "duty_cycle_pct": 0x0613,
        })

    def _available_storage_mb(self) -> float:
        """Compute available storage accounting for bad segments."""
        s = self._state
        usable_segments = s.num_segments - len(s.bad_segments)
        usable_mb = usable_segments * s.segment_size_mb
        return max(0.0, usable_mb - s.mem_used_mb)

    def _find_free_segment(self) -> int:
        """Find a non-bad segment with space. Returns -1 if none."""
        s = self._state
        for seg in range(s.num_segments):
            if seg not in s.bad_segments:
                return seg
        return -1

    def tick(self, dt: float, orbit_state: Any,
             shared_params: dict[int, float]) -> None:
        s = self._state
        events_to_emit = []

        # ── EPS power-line gate ──
        # The payload imager is powered from the `payload` line (param
        # 0x0113). If EPS has the line off (default at LEOP, operator
        # command, overcurrent, load shed) the imager must collapse to
        # OFF — no calibration, no integration, no FPA cooling, no
        # fresh telemetry — even if `s.mode` was previously commanded
        # to STANDBY/IMAGING and never reset.
        # If 0x0113 is absent the test/standalone harness has no EPS in the
        # loop — keep the legacy behaviour (mode is operator-set).
        _line_payload_on = bool(shared_params.get(0x0113, 1)) if 0x0113 in shared_params else True
        if not _line_payload_on and s.mode != 0:
            s.mode = 0
            s.fpa_ready = False
            s.fpa_ready_timer = 0.0
            s.cooler_active = False
            s.calibration_active = False
            s.calibration_state = 0
            s.calibration_progress = 0.0
            s.dark_frame_count = 0
            s.flat_frame_count = 0

        # ── Calibration timer tick (Defect 1 — payload.md §3.1) ──
        # Calibration requires the imager to be powered. If the payload mode
        # drops to OFF (line trip, OBC command, load shed) the dark/flat
        # acquisition must abort — the detector is no longer integrating.
        if s.calibration_active and s.mode <= 0:
            s.calibration_active = False
            s.calibration_state = 0
            s.calibration_progress = 0.0
            s.dark_frame_count = 0
            s.flat_frame_count = 0
        if s.calibration_active:
            s.calibration_timer -= dt
            half_duration = s.calibration_duration_s / 2
            if s.calibration_state == 1:  # DARK_FRAME
                # Accumulate dark frames: ~10 frames over half duration
                if s.calibration_timer <= 0:
                    # Timer underflow, shouldn't happen but protect against it
                    s.dark_frame_count = 10
                else:
                    elapsed = half_duration - s.calibration_timer
                    s.dark_frame_count = max(0, int((elapsed / half_duration) * 10))
                s.calibration_progress = 100.0 * (half_duration - s.calibration_timer) / half_duration
                if s.calibration_timer <= half_duration:
                    s.calibration_state = 2  # FLAT_FIELD
                    s.calibration_timer = half_duration
                    s.dark_frame_count = 10  # Ensure at least 10
            elif s.calibration_state == 2:  # FLAT_FIELD
                # Accumulate flat frames: ~10 frames over half duration
                if s.calibration_timer <= 0:
                    s.flat_frame_count = 10
                else:
                    elapsed = half_duration - s.calibration_timer
                    s.flat_frame_count = max(0, int((elapsed / half_duration) * 10))
                s.calibration_progress = 50.0 + 50.0 * (half_duration - s.calibration_timer) / half_duration
                if s.calibration_timer <= 0:
                    # Compute gain/bias coefficients
                    for band_id in ['blue', 'green', 'red', 'nir']:
                        s.bias_coeff[band_id] = 0.0  # Simplified: would normally compute from dark_frame_buffer
                        s.gain_coeff[band_id] = 1.0  # Simplified: would normally compute from flat_frame_buffer
                    s.calibration_valid_mask = 0x0F  # All bands valid
                    s.calibration_active = False
                    s.calibration_state = 3  # COMPLETE
                    s.calibration_progress = 100.0
                    s.last_calibration_time = time.time()
                    events_to_emit.append(0x060B)  # CALIBRATION_COMPLETE

        # ── Shutter cycle test (Defect 4 — payload.md §3.3) ──
        if s.shutter_test_active:
            if s.shutter_test_cycles_remaining > 0:
                s.shutter_test_cycles_remaining -= 1
                s.shutter_position = 0 if s.shutter_position == 1 else 1  # Toggle
            else:
                s.shutter_test_active = False
                s.shutter_cycles_completed += 1
                events_to_emit.append(0x060E)  # SHUTTER_TEST_COMPLETE

        # ── Filter wheel rotation ──
        if s.filter_rotation_in_progress:
            s.filter_rotation_timer -= dt
            if s.filter_rotation_timer <= 0:
                s.filter_position = s.filter_target_position
                s.filter_rotation_in_progress = False
                events_to_emit.append(0x060F)  # FILTER_ROTATION_COMPLETE

        # FPA thermal model
        if s.cooler_active and not s.cooler_failed:
            target, tau = self._fpa_target, self._tau_cool
            s.cooler_on_time_s += dt
        else:
            target, tau = self._fpa_ambient, self._tau_warm
            s.cooler_on_time_s = 0.0
        s.fpa_temp += (
            (target - s.fpa_temp) / tau * dt + random.gauss(0, 0.02)
        )

        # Imager temperature
        env_img = 5.0 if not orbit_state.in_eclipse else -5.0
        s.imager_temp += (
            (env_img - s.imager_temp) / 400.0 * dt + random.gauss(0, 0.03)
        )

        # FPA readiness with hysteresis and cooler health check (Defect 2 — payload.md §3.2)
        temp_in_range = (self._fpa_target - 1.0) <= s.fpa_temp <= (self._fpa_target + 5.0)
        cooler_ok = not s.cooler_failed
        if temp_in_range and cooler_ok:
            s.fpa_ready_timer += dt
            if s.fpa_ready_timer >= s.fpa_ready_hysteresis_s:
                s.fpa_ready = True
        else:
            s.fpa_ready = False
            s.fpa_ready_timer = 0.0

        # Mode behaviour
        if s.mode == 0:  # OFF
            s.cooler_active = False
            s.line_rate = 0.0
            s.data_rate_mbps = 0.0
            s.duty_cycle_pct = 0.0
        elif s.mode == 1:  # STANDBY (cooler on)
            s.cooler_active = True
            s.line_rate = 0.0
            s.data_rate_mbps = 0.0
            s.duty_cycle_pct = 0.0
        elif s.mode == 2:  # IMAGING
            if s.fpa_ready and not s.cooler_failed:
                s.line_rate = self._line_rate_hz + random.gauss(0, 2.0)
                s.data_rate_mbps = self._data_rate_mbps + random.gauss(0, 0.5)
                data_mb = s.data_rate_mbps * 1e6 / 8 / 1e6 * dt
                s.mem_used_mb = min(
                    s.mem_total_mb, s.mem_used_mb + data_mb
                )
                s.store_used_pct = min(
                    100.0, s.mem_used_mb / s.mem_total_mb * 100.0
                )
                # Checksum errors
                error_prob = 0.0001 * (2 if s.fpa_degraded else 1)
                if s.ccd_line_dropout:
                    error_prob *= 3
                if random.random() < error_prob:
                    s.checksum_errors += 1
                s.duty_cycle_pct = min(
                    100.0,
                    s.duty_cycle_pct + dt / 60.0 * 10.0,
                )
            else:
                s.line_rate = 0.0
                s.data_rate_mbps = 0.0

        # ── Attitude-quality coupling ──
        att_error = shared_params.get(0x0217, 0.0)  # AOCS att_error_deg
        if att_error <= 0.1:
            att_quality = 1.0
        elif att_error <= 0.5:
            att_quality = 1.0 - (att_error - 0.1) * 0.25  # Linear 1.0 -> 0.9
        elif att_error <= 1.0:
            att_quality = 0.9 - (att_error - 0.5) * 0.6  # 0.9 -> 0.6
        elif att_error <= 2.0:
            att_quality = 0.6 - (att_error - 1.0) * 0.4  # 0.6 -> 0.2
        else:
            att_quality = max(0.0, 0.2 - (att_error - 2.0) * 0.1)

        s.att_quality_factor = att_quality
        s.att_error_deg = att_error

        # ── Swath width from altitude ──
        alt_km = orbit_state.alt_km if hasattr(orbit_state, 'alt_km') else 500.0
        ifov_urad = 20.0  # Instantaneous field of view
        pixels_cross = 5000
        fov_rad = pixels_cross * ifov_urad * 1e-6
        s.swath_width_km = alt_km * fov_rad
        s.gsd_m = alt_km * 1000.0 * ifov_urad * 1e-6

        # ── Per-band SNR and aggregate SNR model ──
        if s.mode == 2 and s.fpa_ready:
            fpa_temp = s.fpa_temp
            degrade_factor = 0.85 if s.fpa_degraded else 1.0
            for band in SPECTRAL_BANDS:
                band_id = band['id']
                bit = band['bit']

                if not (s.band_enable_mask & (1 << bit)):
                    s.band_snrs[band_id] = 0.0
                    continue

                # Temperature-dependent SNR (degrades ~3dB per 10C above -20C)
                temp_factor = max(0.3, 1.0 - max(0.0, (fpa_temp + 20.0)) * 0.03)

                # Attitude-dependent SNR
                snr = band['snr_nominal'] * temp_factor * att_quality * degrade_factor
                snr += random.gauss(0, 0.5)  # Measurement noise
                s.band_snrs[band_id] = max(0.0, snr)

            # Aggregate SNR is worst-case across enabled bands
            enabled_snrs = [s.band_snrs[b['id']] for b in SPECTRAL_BANDS
                            if s.band_enable_mask & (1 << b['bit'])]
            if enabled_snrs:
                s.snr = min(enabled_snrs)

            s.active_bands = bin(s.band_enable_mask & 0x0F).count('1')
        elif s.mode == 1:
            s.snr = 0.0  # Standby, not measuring
        else:
            s.snr = 0.0

        # ── Scene-dependent compression ratio ──
        if s.mode == 2:  # IMAGING
            lat = orbit_state.lat_deg if hasattr(orbit_state, 'lat_deg') else 0.0
            # Scene entropy estimation
            abs_lat = abs(lat)
            entropy = 0.2  # Base ocean
            if 20 < abs_lat < 60:
                entropy += 0.3 * (1.0 - abs(abs_lat - 40) / 20.0)
            entropy += random.gauss(0, 0.05)
            entropy = max(0.0, min(1.0, entropy))
            s.scene_entropy = entropy

            # Map entropy to compression ratio (ocean=4.0, complex=1.5)
            s.compression_ratio = 4.0 - entropy * 2.5
        else:
            s.compression_ratio = 2.0
            s.scene_entropy = 0.0

        # Detector temperature tracks FPA temp closely
        s.detector_temp_c = s.fpa_temp + 0.5 + random.gauss(0, 0.05)

        # ── Downlink progress (Defect 5 — payload.md §3.7) ──
        if s.transfer_active and s.transfer_bytes_total > 0:
            # Downlink rate: bytes per second
            bytes_per_sec = s.transfer_rate_mbps * 1e6 / 8
            bytes_this_tick = bytes_per_sec * dt
            s.transfer_bytes_sent = min(s.transfer_bytes_total, s.transfer_bytes_sent + bytes_this_tick)
            s.transfer_progress = 100.0 * s.transfer_bytes_sent / s.transfer_bytes_total

            if s.transfer_bytes_sent >= s.transfer_bytes_total:
                s.transfer_active = False
                s.transfer_progress = 100.0
                events_to_emit.append(0x0611)  # TRANSFER_COMPLETE

        # Effective data accounting with compression
        # (compression_ratio > 1 means smaller stored size)

        # Update derived values
        s.mem_segments_bad = len(s.bad_segments)

        # ── Event generation with edge detection ──
        # IMAGING_START / IMAGING_STOP
        if s.mode == 2 and s.prev_mode != 2:
            events_to_emit.append(0x0600)  # IMAGING_START
        elif s.mode != 2 and s.prev_mode == 2:
            events_to_emit.append(0x0601)  # IMAGING_STOP

        # PAYLOAD_MODE_CHANGE
        if s.mode != s.prev_mode:
            events_to_emit.append(0x0602)  # PAYLOAD_MODE_CHANGE

        # STORAGE_WARNING (90%)
        if s.store_used_pct >= 90.0 and s.prev_store_used_pct < 90.0:
            events_to_emit.append(0x0603)  # STORAGE_WARNING

        # STORAGE_CRITICAL (95%)
        if s.store_used_pct >= 95.0:
            events_to_emit.append(0x060C)  # STORAGE_CRITICAL

        # STORAGE_FULL (100%)
        if s.store_used_pct >= 100.0:
            events_to_emit.append(0x0609)  # STORAGE_FULL

        # FPA_OVERTEMP (> -3C)
        if s.fpa_temp > -3.0 and s.prev_fpa_temp <= -3.0:
            events_to_emit.append(0x0604)  # FPA_OVERTEMP

        # FPA_UNDERTEMP (< -15C)
        if s.fpa_temp < -15.0 and s.prev_fpa_temp >= -15.0:
            events_to_emit.append(0x0605)  # FPA_UNDERTEMP

        # COOLER_FAILURE
        if s.cooler_failed:
            events_to_emit.append(0x0606)  # COOLER_FAILURE

        # IMAGE_CHECKSUM_ERROR
        if s.checksum_errors > 0 and random.random() < 0.01:
            events_to_emit.append(0x0607)  # IMAGE_CHECKSUM_ERROR

        # SNR_DEGRADED (< 25 dB)
        if s.snr < 25.0:
            events_to_emit.append(0x0608)  # SNR_DEGRADED

        # BAD_SEGMENT_DETECTED
        if s.mem_segments_bad > 0:
            events_to_emit.append(0x060A)  # BAD_SEGMENT_DETECTED

        # COMPRESSION_ERROR (compression ratio anomaly)
        if s.compression_ratio < 0.5 or s.compression_ratio > 10.0:
            events_to_emit.append(0x060D)  # COMPRESSION_ERROR

        # Store edge detection values for next tick
        s.prev_mode = s.mode
        s.prev_fpa_temp = s.fpa_temp
        s.prev_store_used_pct = s.store_used_pct

        # Emit collected events to engine if available
        if hasattr(self, '_engine') and self._engine:
            for evt_id in events_to_emit:
                try:
                    # Map event IDs to severity levels
                    severity_map = {
                        0x0600: "INFO", 0x0601: "INFO", 0x0602: "INFO",
                        0x0603: "LOW", 0x0604: "MEDIUM", 0x0605: "MEDIUM",
                        0x0606: "HIGH", 0x0607: "MEDIUM", 0x0608: "LOW",
                        0x0609: "HIGH", 0x060A: "MEDIUM", 0x060B: "INFO",
                        0x060C: "HIGH", 0x060D: "MEDIUM"
                    }
                    severity = severity_map.get(evt_id, "INFO")
                    event_names = {
                        0x0600: "IMAGING_START", 0x0601: "IMAGING_STOP", 
                        0x0602: "PAYLOAD_MODE_CHANGE", 0x0603: "STORAGE_WARNING",
                        0x0604: "FPA_OVERTEMP", 0x0605: "FPA_UNDERTEMP",
                        0x0606: "COOLER_FAILURE", 0x0607: "IMAGE_CHECKSUM_ERROR",
                        0x0608: "SNR_DEGRADED", 0x0609: "STORAGE_FULL",
                        0x060A: "BAD_SEGMENT_DETECTED", 0x060B: "CALIBRATION_COMPLETE",
                        0x060C: "STORAGE_CRITICAL", 0x060D: "COMPRESSION_ERROR"
                    }
                    name = event_names.get(evt_id, "UNKNOWN")
                    self._engine.event_queue.put_nowait({
                        'event_id': evt_id,
                        'severity': severity,
                        'subsystem': 'payload',
                        'description': name
                    })
                except:
                    pass

        # Write params
        p = self._param_ids
        shared_params[p.get("pli_mode", 0x0600)] = s.mode
        shared_params[p.get("fpa_temp", 0x0601)] = s.fpa_temp
        shared_params[p.get("cooler_pwr", 0x0602)] = (
            self._cooler_power_w if s.cooler_active else 0.0
        )
        shared_params[p.get("imager_temp", 0x0603)] = s.imager_temp
        shared_params[p.get("store_used", 0x0604)] = s.store_used_pct
        shared_params[p.get("image_count", 0x0605)] = s.image_count
        shared_params[p.get("scene_id", 0x0606)] = s.current_scene_id
        shared_params[p.get("line_rate", 0x0607)] = s.line_rate
        shared_params[p.get("data_rate", 0x0608)] = s.data_rate_mbps
        shared_params[p.get("checksum_errors", 0x0609)] = s.checksum_errors
        # New params
        shared_params[p.get("mem_total_mb", 0x060A)] = s.mem_total_mb
        shared_params[p.get("mem_used_mb", 0x060B)] = s.mem_used_mb
        shared_params[p.get("last_scene_id", 0x060C)] = s.last_scene_id
        shared_params[p.get("last_scene_quality", 0x060D)] = (
            s.last_scene_quality
        )
        shared_params[p.get("fpa_ready", 0x060F)] = 1 if s.fpa_ready else 0
        shared_params[p.get("mem_segments_bad", 0x0612)] = s.mem_segments_bad
        shared_params[p.get("duty_cycle_pct", 0x0613)] = s.duty_cycle_pct
        # Phase 4: Flight hardware params
        shared_params[0x0614] = s.compression_ratio
        shared_params[0x0615] = 1 if s.cal_lamp_on else 0
        shared_params[0x0616] = s.snr
        shared_params[0x0617] = s.detector_temp_c
        shared_params[0x0618] = s.integration_time_ms
        shared_params[0x0619] = s.swath_width_km

        # ── Multispectral band params ──
        shared_params[0x0620] = s.band_snrs.get('blue', 0.0)
        shared_params[0x0621] = s.band_snrs.get('green', 0.0)
        shared_params[0x0622] = s.band_snrs.get('red', 0.0)
        shared_params[0x0623] = s.band_snrs.get('nir', 0.0)
        shared_params[0x0624] = float(s.active_bands)
        shared_params[0x0625] = float(s.band_enable_mask)

        # ── Attitude quality ──
        shared_params[0x0626] = s.att_quality_factor
        shared_params[0x0627] = s.att_error_deg

        # ── GSD ──
        shared_params[0x0628] = s.gsd_m

        # ── Scene entropy ──
        shared_params[0x0629] = s.scene_entropy

        # ── Calibration telemetry ──
        shared_params[0x062A] = 1.0 if s.calibration_active else 0.0
        shared_params[0x062B] = s.calibration_progress
        shared_params[0x062C] = s.last_calibration_time

        # ── Transfer telemetry ──
        shared_params[0x062D] = 1.0 if s.transfer_active else 0.0
        shared_params[0x062E] = s.transfer_progress

        # ── Integration time (multi-band) ──
        shared_params[0x062F] = s.integration_time_ms

    def get_telemetry(self) -> dict[int, float]:
        return {0x0601: self._state.fpa_temp}

    def handle_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        command = cmd.get("command", "")
        s = self._state

        if command == "set_mode":
            m = int(cmd.get("mode", 0))
            if m in (0, 1, 2):
                s.mode = m
                return {"success": True}

        elif command == "set_scene":
            s.current_scene_id = int(cmd.get("scene_id", 0))
            return {"success": True}

        elif command == "capture":
            # Capture an image at current position
            if s.mode != 2:
                return {
                    "success": False,
                    "message": "Not in IMAGING mode",
                }
            if not s.fpa_ready:
                return {"success": False, "message": "FPA not ready"}

            # Compute actual stored size with compression (Defect 3 — payload.md §3.6)
            base_size = s.image_size_mb
            if s.compression_enabled:
                stored_size = base_size / s.compression_ratio
            else:
                stored_size = base_size

            if self._available_storage_mb() < stored_size:
                return {"success": False, "message": "Insufficient storage"}

            seg = self._find_free_segment()
            if seg < 0:
                return {
                    "success": False,
                    "message": "No usable memory segments",
                }

            # Determine image quality/status
            quality = 100.0
            status = 0  # OK
            if s.corrupt_remaining > 0:
                status = 2  # CORRUPT
                quality = random.uniform(10.0, 30.0)
                s.corrupt_remaining -= 1
                s.checksum_errors += 1
            elif s.ccd_line_dropout:
                status = 1  # PARTIAL
                quality = random.uniform(60.0, 85.0)
            elif s.fpa_degraded:
                quality = random.uniform(80.0, 95.0)

            self._next_scene_id += 1
            img = {
                "scene_id": self._next_scene_id,
                "timestamp": time.time(),
                "lat": cmd.get("lat", 0.0),
                "lon": cmd.get("lon", 0.0),
                "quality": quality,
                "status": status,
                "size_mb": stored_size,  # CHANGED: now uses compressed size
                "compression_ratio": s.compression_ratio,
                "compression_algorithm": s.compression_algorithm,
                "segment": seg,
            }
            s.image_catalog.append(img)
            s.image_count += 1
            s.mem_used_mb += stored_size
            s.store_used_pct = min(
                100.0, s.mem_used_mb / s.mem_total_mb * 100.0
            )
            s.current_scene_id = self._next_scene_id
            s.last_scene_id = self._next_scene_id
            s.last_scene_quality = quality

            return {
                "success": True,
                "scene_id": self._next_scene_id,
                "quality": quality,
                "status": status,
                "stored_size_mb": stored_size,
                "compression_ratio": s.compression_ratio,
            }

        elif command == "download_image":
            scene_id = int(cmd.get("scene_id", 0))
            for img in s.image_catalog:
                if img["scene_id"] == scene_id:
                    return {
                        "success": True,
                        "image": img,
                    }
            return {"success": False, "message": "Image not found"}

        elif command == "delete_image":
            scene_id = cmd.get("scene_id")
            if scene_id is not None:
                # Delete specific image by scene_id
                scene_id = int(scene_id)
                for i, img in enumerate(s.image_catalog):
                    if img["scene_id"] == scene_id:
                        freed = img["size_mb"]
                        s.mem_used_mb = max(0.0, s.mem_used_mb - freed)
                        s.store_used_pct = max(
                            0.0, s.mem_used_mb / s.mem_total_mb * 100.0
                        )
                        s.image_count = max(0, s.image_count - 1)
                        s.image_catalog.pop(i)
                        return {"success": True}
                return {"success": False, "message": "Image not found"}
            else:
                # Legacy: delete by count
                cnt = int(cmd.get("count", 1))
                freed = s.image_size_mb * cnt / s.total_storage_mb * 100.0
                s.store_used_pct = max(0.0, s.store_used_pct - freed)
                s.image_count = max(0, s.image_count - cnt)
                # Remove from catalog
                for _ in range(min(cnt, len(s.image_catalog))):
                    if s.image_catalog:
                        img = s.image_catalog.pop(0)
                        s.mem_used_mb = max(
                            0.0, s.mem_used_mb - img["size_mb"]
                        )
                return {"success": True}

        elif command == "mark_bad_segment":
            seg = int(cmd.get("segment", 0))
            if 0 <= seg < s.num_segments:
                if seg not in s.bad_segments:
                    s.bad_segments.append(seg)
                    s.mem_segments_bad = len(s.bad_segments)
                    # Reduce effective total storage
                    usable = s.num_segments - len(s.bad_segments)
                    s.mem_total_mb = usable * s.segment_size_mb
                return {"success": True}
            return {"success": False, "message": "Invalid segment index"}

        elif command == "get_image_catalog":
            return {
                "success": True,
                "catalog": list(s.image_catalog),
                "count": len(s.image_catalog),
            }

        elif command == "set_band_config":
            mask = int(cmd.get("mask", 0x0F))
            if 0 <= mask <= 0x0F:
                s.band_enable_mask = mask
                s.active_bands = bin(mask).count('1')
                return {"success": True}
            return {"success": False, "message": "Invalid band mask (0x00-0x0F)"}

        elif command == "set_integration_time":
            # Set per-band integration times (4 float values)
            times = cmd.get("times", [2.0, 2.0, 2.0, 2.0])
            if len(times) >= 4:
                s.integration_time_blue = times[0]
                s.integration_time_green = times[1]
                s.integration_time_red = times[2]
                s.integration_time_nir = times[3]
                s.integration_time_ms = sum(times) / 4.0  # Average
                return {"success": True}
            return {"success": False, "message": "Invalid integration times"}

        elif command == "set_detector_gain":
            # Set detector gain/offset per band
            gain = float(cmd.get("gain", 1.0))
            s.detector_gain = max(0.1, min(10.0, gain))
            return {"success": True}

        elif command == "set_cooler_setpoint":
            # Adjust cooler temperature target
            target = float(cmd.get("setpoint", -15.0))
            if -20.0 <= target <= 0.0:
                self._fpa_target = target
                s.cooler_setpoint_c = target
                return {"success": True}
            return {"success": False, "message": "Invalid cooler setpoint (-20C to 0C)"}

        elif command == "start_calibration":
            # Begin calibration sequence
            if s.calibration_active:
                return {"success": False, "message": "Calibration already in progress"}
            if s.mode != 2:
                return {"success": False, "message": "Must be in IMAGING mode"}
            s.calibration_active = True
            s.calibration_state = 1  # DARK_FRAME
            s.calibration_progress = 0.0
            s.calibration_timer = s.calibration_duration_s / 2  # Half duration for DARK_FRAME phase (Defect 1)
            s.dark_frame_count = 0  # Initialize frame counters
            s.flat_frame_count = 0
            return {"success": True, "message": "Calibration started"}

        elif command == "stop_calibration":
            # Abort calibration
            if not s.calibration_active:
                return {"success": False, "message": "No active calibration"}
            s.calibration_active = False
            s.calibration_state = 0  # IDLE
            s.calibration_progress = 0.0
            s.calibration_timer = 0.0
            return {"success": True, "message": "Calibration aborted"}

        elif command == "set_compression":
            # Override compression ratio and algorithm (Defect 3 — payload.md §3.6)
            algo = int(cmd.get("algorithm", s.compression_algorithm))
            if 0 <= algo <= 3:
                s.compression_algorithm = algo
            ratio = float(cmd.get("ratio", 0.0))
            s.compression_override = ratio
            if ratio > 0:
                s.compression_ratio = ratio
            return {"success": True}

        # ── Shutter commands (Defect 4 — payload.md §3.3) ──
        elif command == "cycle_shutter":
            cycles = int(cmd.get("cycles", 1))
            if not s.shutter_test_active:
                s.shutter_test_active = True
                s.shutter_test_cycles_remaining = cycles
                s.shutter_position = 0 if s.shutter_position == 1 else 1  # Toggle
                return {"success": True, "message": f"Starting shutter cycle {cycles}"}
            else:
                return {"success": False, "message": "Shutter test already in progress"}

        elif command == "get_shutter_status":
            return {
                "success": True,
                "position": s.shutter_position,
                "test_in_progress": s.shutter_test_active,
                "cycles_completed": s.shutter_cycles_completed,
            }

        # ── Filter wheel commands (Defect 4 — payload.md §3.3) ──
        elif command == "select_filter":
            position = int(cmd.get("position", 0))
            num_filters = 4  # Default: 4 filter positions
            if 0 <= position < num_filters:
                s.filter_target_position = position
                s.filter_rotation_in_progress = True
                s.filter_rotation_timer = s.filter_rotation_time_s * abs(position - s.filter_position)
                return {"success": True, "message": f"Rotating to filter {position}"}
            else:
                return {"success": False, "message": f"Invalid filter position (0-{num_filters-1})"}

        elif command == "get_filter_status":
            return {
                "success": True,
                "position": s.filter_position,
                "rotation_in_progress": s.filter_rotation_in_progress,
                "target_position": s.filter_target_position,
            }

        # ── Downlink commands (Defect 5 — payload.md §3.7) ──
        elif command == "initiate_transfer":
            scene_id = int(cmd.get("scene_id", 0))
            # Find image in catalog
            for img in s.image_catalog:
                if img["scene_id"] == scene_id:
                    s.transfer_scene_id = scene_id
                    s.transfer_bytes_total = int(img["size_mb"] * 1e6)
                    s.transfer_bytes_sent = 0
                    s.transfer_active = True
                    s.transfer_progress = 0.0
                    # Event 0x0610 (TRANSFER_START) will be emitted in next tick
                    return {"success": True, "message": f"Transfer {scene_id} initiated",
                            "bytes_total": s.transfer_bytes_total}
            return {"success": False, "message": f"Image {scene_id} not found"}

        elif command == "get_transfer_status":
            return {
                "success": True,
                "transfer_active": s.transfer_active,
                "scene_id": s.transfer_scene_id,
                "bytes_total": s.transfer_bytes_total,
                "bytes_sent": s.transfer_bytes_sent,
                "progress_pct": s.transfer_progress,
            }

        return {"success": False, "message": f"Unknown: {command}"}

    def inject_failure(self, failure: str, magnitude: float = 1.0,
                       **kw) -> None:
        s = self._state

        if failure == "cooler_failure":
            s.cooler_failed = bool(magnitude)
            if magnitude:
                s.cooler_active = False

        elif failure == "fpa_degraded":
            s.fpa_degraded = bool(magnitude)

        elif failure == "image_corrupt":
            count = int(kw.get("count", 3))
            s.corrupt_remaining = count

        elif failure == "memory_segment_fail":
            seg = int(kw.get("segment", 0))
            if 0 <= seg < s.num_segments and seg not in s.bad_segments:
                s.bad_segments.append(seg)
                s.mem_segments_bad = len(s.bad_segments)
                usable = s.num_segments - len(s.bad_segments)
                s.mem_total_mb = usable * s.segment_size_mb

        elif failure == "ccd_line_dropout":
            s.ccd_line_dropout = bool(magnitude)

    def clear_failure(self, failure: str, **kw) -> None:
        s = self._state

        if failure == "cooler_failure":
            s.cooler_failed = False

        elif failure == "fpa_degraded":
            s.fpa_degraded = False

        elif failure == "image_corrupt":
            s.corrupt_remaining = 0

        elif failure == "memory_segment_fail":
            seg = int(kw.get("segment", -1))
            if seg >= 0 and seg in s.bad_segments:
                s.bad_segments.remove(seg)
            else:
                s.bad_segments.clear()
            s.mem_segments_bad = len(s.bad_segments)
            usable = s.num_segments - len(s.bad_segments)
            s.mem_total_mb = usable * s.segment_size_mb

        elif failure == "ccd_line_dropout":
            s.ccd_line_dropout = False

    def get_state(self) -> dict[str, Any]:
        import dataclasses
        d = dataclasses.asdict(self._state)
        # image_catalog contains dicts, already serializable
        return d

    def set_state(self, state: dict[str, Any]) -> None:
        for k, v in state.items():
            if hasattr(self._state, k):
                setattr(self._state, k, v)

    # S2 Device Access — device-level on/off control
    def set_device_state(self, device_id: int, on_off: bool) -> bool:
        """Set device on/off state. Returns True if successful."""
        if device_id not in self._state.device_states:
            return False
        self._state.device_states[device_id] = on_off
        return True

    def get_device_state(self, device_id: int) -> bool:
        """Get device on/off state. Returns True if on, False if off or invalid."""
        return self._state.device_states.get(device_id, False)

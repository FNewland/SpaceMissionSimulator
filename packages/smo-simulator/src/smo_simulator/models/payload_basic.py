"""SMO Simulator — Enhanced Payload Model.

Image catalog with metadata, memory segment model with individual failure,
FPA thermal model, image capture/download/delete commands, corrupted image
handling, comprehensive failure injection, and ocean current multispectral
imaging with per-band SNR, attitude-quality coupling, and scene-dependent
compression.
"""
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any

from smo_common.config.fpa_envelope import FPAEnvelope
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
    size_mb: float = 4.0
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
    image_size_mb: float = 4.0   # raw frame; payload.yaml image_size_mb (defect #82)
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

    # ── Phase C: AOCS fine-pointing gate (WP-6) ──
    # Last-seen AOCS pointing state from shared params during tick; None-like
    # sentinel aocs_seen=False keeps standalone harnesses (no AOCS in the
    # loop) permissive, matching the tcs_fpa pattern above.
    aocs_seen: bool = False
    aocs_mode_seen: int = 0
    aocs_total_err_seen: float = 0.0
    aocs_rate_stab_seen: float = 0.0
    aocs_settled_seen: bool = True
    # Strip-acquisition monitor: while > 0 the last capture is still being
    # scanned; losing FINE (or the error gate) mid-strip marks it PARTIAL
    # with the worst actual pointing error stamped into the catalog entry.
    strip_remaining_s: float = 0.0
    strip_worst_err_deg: float = 0.0
    strip_scene_id: int = 0

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
    calibration_mode: int = 2   # Phase E WP-7: 0=DARK only, 1=FLAT only, 2=BOTH
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

    # ── Phase E WP-1: on-board geolocation stamp ──
    # Sub-satellite point sampled from orbit_state each tick; `capture`
    # stamps it into the image catalogue (geodesy is NOT a TC argument —
    # the WHERE is set by the WHEN via S11).
    orbit_lat_deg: float = 0.0
    orbit_lon_deg: float = 0.0

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
    # Rising-edge state for level/threshold events that previously fired every
    # tick while the condition held (storage critical/full, cooler failure,
    # SNR degraded, bad-segment, compression error).
    _prev_storage_critical: bool = False
    _prev_storage_full: bool = False
    _prev_cooler_failed: bool = False
    _prev_snr_degraded: bool = False
    _prev_bad_segment: bool = False
    _prev_compression_error: bool = False

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
        # Per-model RNG (plan A-2). Standalone construction gets a fresh
        # unseeded stream so existing unit tests keep working; the engine
        # replaces this with a Random derived from simulation.yaml `seed`.
        import random as _random
        self.rng = _random.Random(20260731)  # deterministic standalone fallback; engine overrides via derive_rng
        self._fpa_target = -15.0
        self._fpa_ambient = 5.0
        self._tau_cool = 100.0
        self._tau_warm = 120.0
        # Documented detector (cold-finger) offset from the TCS FPA bracket
        # node while the cooler runs (brief 05 WP-1): 0x0601 = 0x0408 + offset.
        self._detector_offset_c = -0.5
        # Envelope defaults, replaced by configure() with the TCS-owned block
        # the engine injects. Set here too so a directly instantiated model
        # (tests, tooling) still has the readiness and event rules.
        self._fpa_env = FPAEnvelope()
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
        self._detector_offset_c = config.get("detector_offset_c", -0.5)
        # FPA envelope — single-sourced in subsystems/tcs.yaml and injected by
        # the engine (DECISIONS §3 / G3-d). The payload reads the TCS-owned
        # node, so it must use the same readiness rule and the same over/under
        # -temp thresholds; a second copy is the defect this ruling removes.
        self._fpa_env = FPAEnvelope.from_config(config)
        self._state.image_size_mb = config.get("image_size_mb", 4.0)
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
            # 0x0610, not 0x060F: parameters.yaml and HK SID 5 both use
            # 0x0610 — the model wrote 0x060F so the "FPA Ready" indicator
            # was hard-zero forever (brief 05 finding 11 / WP-7).
            "fpa_ready": 0x0610, "mem_segments_bad": 0x0612,
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

    def _apply_device_power_gates(self, s: "PayloadState") -> None:
        """Force payload telemetry to a powered-down state for any device
        switched OFF via S2 device access (device_states). Wires the
        S2_PAYLOAD_* on/off commands, which were previously dead flags.

        Devices:
          0x0600 FPA            — no imaging without the focal-plane array;
                                  force OFF mode, not ready, no data.
          0x0601 FPA cooler     — cooler cannot run; FPA warms toward ambient.
          0x0602 Calibration lamp — lamp telemetry off; abort any cal sequence.
          0x0603 Shutter        — mechanism unpowered; report CLOSED, abort test.
          0x0604 Compression    — no compression engine; ratio collapses to 1.0.
        """
        ds = s.device_states

        if not ds.get(0x0600, True):  # Focal plane array
            s.mode = 0
            s.fpa_ready = False
            s.fpa_ready_timer = 0.0
            s.line_rate = 0.0
            s.data_rate_mbps = 0.0
            s.duty_cycle_pct = 0.0

        if not ds.get(0x0601, True):  # FPA cooler
            s.cooler_active = False

        if not ds.get(0x0602, True):  # Calibration lamp
            s.cal_lamp_on = False
            if s.calibration_active:
                s.calibration_active = False
                s.calibration_state = 0
                s.calibration_progress = 0.0

        if not ds.get(0x0603, True):  # Shutter mechanism
            s.shutter_position = 0  # CLOSED
            s.shutter_test_active = False

        if not ds.get(0x0604, True):  # Compression unit
            s.compression_ratio = 1.0

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
            # Phase E WP-7: single-phase runs (mode 0 = DARK only, 1 = FLAT
            # only) complete after their one phase; mode 2 = BOTH keeps the
            # legacy dark-then-flat sequence.
            dark_only = (s.calibration_mode == 0)
            if s.calibration_state == 1:  # DARK_FRAME
                # Accumulate dark frames: ~10 frames over half duration
                if s.calibration_timer <= 0:
                    # Timer underflow, shouldn't happen but protect against it
                    s.dark_frame_count = 10
                else:
                    elapsed = half_duration - s.calibration_timer
                    s.dark_frame_count = max(0, int((elapsed / half_duration) * 10))
                if dark_only:
                    s.calibration_progress = min(
                        100.0,
                        100.0 * (half_duration - s.calibration_timer) / half_duration)
                    if s.calibration_timer <= 0:
                        s.dark_frame_count = 10
                        for band_id in ['blue', 'green', 'red', 'nir']:
                            s.bias_coeff[band_id] = 0.0
                        s.calibration_active = False
                        s.calibration_state = 3  # COMPLETE (dark set only)
                        s.calibration_progress = 100.0
                        s.last_calibration_time = self._now()
                        events_to_emit.append(0x060B)  # CALIBRATION_COMPLETE
                else:
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
                if s.calibration_mode == 1:   # FLAT only: full-range progress
                    s.calibration_progress = min(
                        100.0,
                        100.0 * (half_duration - s.calibration_timer) / half_duration)
                else:
                    s.calibration_progress = 50.0 + 50.0 * (half_duration - s.calibration_timer) / half_duration
                if s.calibration_timer <= 0:
                    # Compute gain/bias coefficients
                    for band_id in ['blue', 'green', 'red', 'nir']:
                        s.bias_coeff[band_id] = 0.0  # Simplified: would normally compute from dark_frame_buffer
                        s.gain_coeff[band_id] = 1.0  # Simplified: would normally compute from flat_frame_buffer
                    if s.calibration_mode == 2:
                        s.calibration_valid_mask = 0x0F  # All bands valid
                    s.calibration_active = False
                    s.calibration_state = 3  # COMPLETE
                    s.calibration_progress = 100.0
                    s.last_calibration_time = self._now()
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

        # FPA thermal model (brief 05 defect 1 / WP-1): TCS owns the FPA
        # thermal node (0x0408); the payload's fpa_temp is the DETECTOR
        # (cold-finger) side — a fast first-order lag onto the TCS bracket
        # node plus a small documented gradient. Previously this was a second,
        # fully independent integrator with its own target and its own
        # unlinked cooler flag, and the two "FPA temperature" curves diverged
        # by up to 26 C on the command path the shipped procedures use.
        tcs_fpa = shared_params.get(0x0408)
        if tcs_fpa is not None:
            cooler_running = bool(shared_params.get(0x040C, 0))
            # Mirror the ACTUAL cooler state (unified flag) so 0x0602 cooler
            # power telemetry and cooler_on_time agree with TCS/EPS (WP-2).
            s.cooler_active = cooler_running and not s.cooler_failed
            if s.cooler_active:
                target = tcs_fpa + self._detector_offset_c
                tau = self._tau_cool
                s.cooler_on_time_s += dt
            else:
                target, tau = tcs_fpa, self._tau_warm
                s.cooler_on_time_s = 0.0
        elif s.cooler_active and not s.cooler_failed:
            # Standalone fallback (no TCS in the loop): legacy self-integration
            target, tau = self._fpa_target, self._tau_cool
            s.cooler_on_time_s += dt
        else:
            target, tau = self._fpa_ambient, self._tau_warm
            s.cooler_on_time_s = 0.0
        # Integrator process noise: sqrt(dt) scaling (plan A-3)
        s.fpa_temp += (
            (target - s.fpa_temp) / tau * dt
            + self.rng.gauss(0, 0.02) * math.sqrt(dt)
        )

        # Imager temperature
        env_img = 5.0 if not orbit_state.in_eclipse else -5.0
        s.imager_temp += (
            (env_img - s.imager_temp) / 400.0 * dt
            + self.rng.gauss(0, 0.03) * math.sqrt(dt)
        )

        # FPA readiness with hysteresis and cooler health check (Defect 2 —
        # payload.md §3.2). G3-d: the window is now the shared envelope rule —
        # settled about the ACTIVE setpoint AND cold enough to image — so TCS
        # (0x040A/0x040B) and the payload (0x0610) can no longer disagree
        # about whether the same detector is ready.
        temp_in_range = self._fpa_env.is_ready(s.fpa_temp, self._fpa_target)
        cooler_ok = not s.cooler_failed
        if temp_in_range and cooler_ok:
            s.fpa_ready_timer += dt
            if s.fpa_ready_timer >= s.fpa_ready_hysteresis_s:
                s.fpa_ready = True
        else:
            s.fpa_ready = False
            s.fpa_ready_timer = 0.0

        # Mode behaviour. With TCS in the loop (0x0408 present) cooler_active
        # is the ACTUAL unified cooler state set above, not the mode request —
        # the request reaches TCS through service dispatch (brief 05 WP-2).
        _tcs_in_loop = tcs_fpa is not None
        if s.mode == 0:  # OFF
            if not _tcs_in_loop:
                s.cooler_active = False
            s.line_rate = 0.0
            s.data_rate_mbps = 0.0
            s.duty_cycle_pct = 0.0
        elif s.mode == 1:  # STANDBY (cooler on)
            if not _tcs_in_loop:
                s.cooler_active = True
            s.line_rate = 0.0
            s.data_rate_mbps = 0.0
            s.duty_cycle_pct = 0.0
        elif s.mode == 2:  # IMAGING
            if s.fpa_ready and not s.cooler_failed:
                s.line_rate = self._line_rate_hz + self.rng.gauss(0, 2.0)
                s.data_rate_mbps = self._data_rate_mbps + self.rng.gauss(0, 0.5)
                data_mb = s.data_rate_mbps * 1e6 / 8 / 1e6 * dt
                s.mem_used_mb = min(
                    s.mem_total_mb, s.mem_used_mb + data_mb
                )
                s.store_used_pct = min(
                    100.0, s.mem_used_mb / s.mem_total_mb * 100.0
                )
                # Checksum errors
                # Rate per unit sim time, not per tick (plan A-3)
                error_prob = 0.0001 * dt * (2 if s.fpa_degraded else 1)
                if s.ccd_line_dropout:
                    error_prob *= 3
                if self.rng.random() < error_prob:
                    s.checksum_errors += 1
                s.duty_cycle_pct = min(
                    100.0,
                    s.duty_cycle_pct + dt / 60.0 * 10.0,
                )
            else:
                s.line_rate = 0.0
                s.data_rate_mbps = 0.0

        # ── Attitude-quality coupling ──
        # Phase C (brief 02 WP-6): curve rescaled to the REAL error
        # magnitudes — the old breakpoints (knee at 0.1°) scored NOMINAL
        # (0.06°) and FINE (0.02°) identically. Same shape, new knees:
        # full quality below 0.02°, knee at 0.05°, zero above 0.2°.
        att_error = shared_params.get(0x0217, 0.0)  # AOCS att_error_deg
        if att_error <= 0.02:
            att_quality = 1.0
        elif att_error <= 0.05:
            att_quality = 1.0 - (att_error - 0.02) / 0.03 * 0.1   # 1.0 -> 0.9
        elif att_error <= 0.1:
            att_quality = 0.9 - (att_error - 0.05) / 0.05 * 0.3   # 0.9 -> 0.6
        elif att_error <= 0.2:
            att_quality = 0.6 - (att_error - 0.1) / 0.1 * 0.4     # 0.6 -> 0.2
        else:
            att_quality = max(0.0, 0.2 - (att_error - 0.2) * 1.0)

        s.att_quality_factor = att_quality
        s.att_error_deg = att_error

        # ── Phase C: sample the AOCS pointing state for the capture gate ──
        aocs_mode = shared_params.get(0x020F)
        if aocs_mode is not None and 0x02AA in shared_params:
            s.aocs_seen = True
            s.aocs_mode_seen = int(aocs_mode)
            s.aocs_total_err_seen = float(shared_params.get(0x02AA, 0.0))
            s.aocs_rate_stab_seen = float(shared_params.get(0x02C3, 0.0))
            s.aocs_settled_seen = bool(
                shared_params.get(0x0292, 0.0) < 0.5)   # slew idle

        # ── Strip-acquisition monitor (WP-6): PARTIAL on FINE loss ──
        if s.strip_remaining_s > 0.0:
            s.strip_remaining_s = max(0.0, s.strip_remaining_s - dt)
            if s.aocs_seen:
                err_now = s.aocs_total_err_seen
                fine_lost = (s.aocs_mode_seen != 5) or (err_now > 0.05)
                if fine_lost and err_now > s.strip_worst_err_deg:
                    s.strip_worst_err_deg = err_now
                if fine_lost:
                    for img in reversed(s.image_catalog):
                        if img.get("scene_id") == s.strip_scene_id:
                            if img.get("status", 0) == 0:
                                img["status"] = 1  # PARTIAL
                                img["quality"] = min(
                                    img.get("quality", 100.0),
                                    self.rng.uniform(55.0, 80.0))
                            img["pointing_err_deg"] = round(
                                max(err_now, s.strip_worst_err_deg), 4)
                            s.last_scene_quality = img["quality"]
                            break

        # ── Phase E WP-1: sample the sub-satellite point for the capture
        # geolocation stamp (what a real spacecraft does — resolved on board
        # from the propagated ephemeris at shutter open, not commanded).
        if hasattr(orbit_state, 'lat_deg'):
            s.orbit_lat_deg = float(orbit_state.lat_deg)
        if hasattr(orbit_state, 'lon_deg'):
            s.orbit_lon_deg = float(orbit_state.lon_deg)

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
            int_times = {
                'blue': s.integration_time_blue,
                'green': s.integration_time_green,
                'red': s.integration_time_red,
                'nir': s.integration_time_nir,
            }
            for band in SPECTRAL_BANDS:
                band_id = band['id']
                bit = band['bit']

                if not (s.band_enable_mask & (1 << bit)):
                    s.band_snrs[band_id] = 0.0
                    continue

                # Temperature-dependent SNR (degrades ~3dB per 10C above -20C)
                temp_factor = max(0.3, 1.0 - max(0.0, (fpa_temp + 20.0)) * 0.03)

                # ── Phase E WP-8 (DECISIONS.md section 6): integration time
                # and detector gain have radiometric consequences. Signal
                # scales with t_int, so SNR gains ~3 dB per doubling
                # (10*log10(t/t0), read-noise-limited detector); gain lifts
                # the signal out of the read-noise floor at ~1.5 dB per
                # doubling until it saturates the ADC — beyond gain x
                # t_int/t0 > 8 the well fills and SNR collapses 6 dB per
                # further doubling (the COM-012 saturation decision point).
                t_ratio = max(0.05, int_times[band_id] / 2.0)
                radiometric_db = 10.0 * math.log10(t_ratio)
                gain = max(0.1, s.detector_gain)
                radiometric_db += 5.0 * math.log10(gain)
                exposure = gain * t_ratio
                if exposure > 8.0:      # ADC full-well: overexposed
                    radiometric_db -= 20.0 * math.log10(exposure / 8.0)
                # Bound the term so a pathological setting cannot produce a
                # negative-noise fantasy (raw dynamic range ~ +12 dB).
                radiometric_db = max(-30.0, min(12.0, radiometric_db))

                # Attitude-dependent SNR
                snr = band['snr_nominal'] * temp_factor * att_quality * degrade_factor
                snr += radiometric_db
                snr += self.rng.gauss(0, 0.5)  # Measurement noise
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
            entropy += self.rng.gauss(0, 0.05)
            entropy = max(0.0, min(1.0, entropy))
            s.scene_entropy = entropy

            # Map entropy to compression ratio (ocean=4.0, complex=1.5)
            s.compression_ratio = 4.0 - entropy * 2.5
        else:
            s.compression_ratio = 2.0
            s.scene_entropy = 0.0

        # Detector temperature tracks FPA temp closely
        s.detector_temp_c = s.fpa_temp + 0.5 + self.rng.gauss(0, 0.05)

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

        # STORAGE_CRITICAL (95%) — rising-edge only
        storage_critical = s.store_used_pct >= 95.0
        if storage_critical and not s._prev_storage_critical:
            events_to_emit.append(0x060C)  # STORAGE_CRITICAL
        s._prev_storage_critical = storage_critical

        # STORAGE_FULL (100%) — rising-edge only
        storage_full = s.store_used_pct >= 100.0
        if storage_full and not s._prev_storage_full:
            events_to_emit.append(0x0609)  # STORAGE_FULL
        s._prev_storage_full = storage_full

        # FPA_OVERTEMP — warmer than the science can tolerate. Derived from
        # fpa_envelope.imaging_max_c (G3-d), which is where the textbook's
        # -3 C upper bound and the planner precondition now both come from.
        _over = self._fpa_env.overtemp_c()
        if s.fpa_temp > _over and s.prev_fpa_temp <= _over:
            events_to_emit.append(0x0604)  # FPA_OVERTEMP

        # FPA_UNDERTEMP — colder than the loop can command. The old -15.0
        # threshold sat exactly ON the cooler setpoint and fired on every
        # nominal cooldown; the -18.0 that replaced it then fired on any
        # setpoint at or below -17 C, which Phase E made an ordinary thing to
        # command. Derived: setpoint_min - 3 sigma = -20.5 C.
        _under = self._fpa_env.undertemp_c()
        if s.fpa_temp < _under and s.prev_fpa_temp >= _under:
            events_to_emit.append(0x0605)  # FPA_UNDERTEMP

        # COOLER_FAILURE — rising-edge only
        if s.cooler_failed and not s._prev_cooler_failed:
            events_to_emit.append(0x0606)  # COOLER_FAILURE
        s._prev_cooler_failed = s.cooler_failed

        # IMAGE_CHECKSUM_ERROR — probabilistic, each fire is a distinct event
        if s.checksum_errors > 0 and self.rng.random() < 0.01 * dt:
            events_to_emit.append(0x0607)  # IMAGE_CHECKSUM_ERROR

        # SNR_DEGRADED (< 25 dB) — rising-edge only.
        # Only meaningful while actually imaging (snr is forced to 0.0 in
        # STANDBY/OFF, which would otherwise look "degraded" forever).
        snr_degraded = (s.mode == 2 and s.fpa_ready and s.snr < 25.0)
        if snr_degraded and not s._prev_snr_degraded:
            events_to_emit.append(0x0608)  # SNR_DEGRADED
        s._prev_snr_degraded = snr_degraded

        # BAD_SEGMENT_DETECTED — rising-edge only (fires when a new bad segment
        # pushes the count above zero from zero; re-arms once all clear).
        bad_segment = s.mem_segments_bad > 0
        if bad_segment and not s._prev_bad_segment:
            events_to_emit.append(0x060A)  # BAD_SEGMENT_DETECTED
        s._prev_bad_segment = bad_segment

        # COMPRESSION_ERROR (compression ratio anomaly) — rising-edge only
        compression_error = s.compression_ratio < 0.5 or s.compression_ratio > 10.0
        if compression_error and not s._prev_compression_error:
            events_to_emit.append(0x060D)  # COMPRESSION_ERROR
        s._prev_compression_error = compression_error

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
                    self._engine._model_event_queue.put_nowait({
                        'event_id': evt_id,
                        'severity': severity,
                        'subsystem': 'payload',
                        'description': name
                    })
                except Exception:
                    pass  # event queue full or format error

        # ── S2 device-access power gates ──
        # Collapse payload telemetry for any device the operator has switched
        # OFF via S2 device access. Previously every entry in device_states was
        # a dead flag — the S2_PAYLOAD_* on/off commands were accepted but the
        # model never read the dict, so they had no effect. Gating runs after
        # the physics tick so a powered-off device reads as down; all devices
        # default ON so nominal behaviour and existing tests are unchanged.
        self._apply_device_power_gates(s)

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
        shared_params[p.get("fpa_ready", 0x0610)] = 1 if s.fpa_ready else 0
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

        # ── Phase E WP-7: real telemetry for the previously-stub settings ──
        shared_params[0x0631] = s.cooler_setpoint_c        # payload.fpa_setpoint
        shared_params[0x0635] = s.integration_time_ms      # payload.integration_ms
        shared_params[0x0636] = s.detector_gain            # payload.gain_code
        shared_params[0x0638] = s.integration_time_blue    # payload.int_time_blue
        shared_params[0x0639] = s.integration_time_green   # payload.int_time_green
        shared_params[0x063A] = s.integration_time_red     # payload.int_time_red
        shared_params[0x063B] = s.integration_time_nir     # payload.int_time_nir
        shared_params[0x063C] = float(s.shutter_position)  # payload.shutter_pos
        shared_params[0x063D] = float(s.filter_position)   # payload.filter_pos
        shared_params[0x063E] = float(s.compression_algorithm)  # payload.compression_algo
        shared_params[0x0640] = 1.0 if s.calibration_active else 0.0  # payload.cal_flag
        shared_params[0x0642] = float(s.dark_frame_count)  # payload.dark_frame_cnt
        shared_params[0x0643] = float(s.flat_frame_count)  # payload.bias_frame_cnt

    def _now(self) -> float:
        """Mission time: engine sim time when hosted (deterministic under a
        pinned t0_epoch, plan A-2), wall clock when standalone."""
        eng = getattr(self, "_engine", None)
        sim_time = getattr(eng, "_sim_time", None)
        if sim_time is not None:
            return sim_time.timestamp()
        return time.time()

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
            # ── Phase C WP-6: fine-pointing gate ──
            # Capture requires AOCS FINE_POINT held with total pointing error
            # < 0.05° and rate stability < 0.005 °/s. Distinct failure codes
            # so the operator sees WHY. Standalone harnesses without an AOCS
            # in the loop (aocs_seen False) are not gated.
            if s.aocs_seen and not cmd.get("force", False):
                if s.aocs_mode_seen != 5:
                    return {"success": False,
                            "code": "POINTING_NOT_FINE",
                            "error_code": 0x0021,
                            "message": ("Capture refused: AOCS not in "
                                        "FINE_POINT (mode %d)"
                                        % s.aocs_mode_seen)}
                if s.aocs_total_err_seen > 0.05:
                    return {"success": False,
                            "code": "POINTING_ERROR_HIGH",
                            "error_code": 0x0022,
                            "message": ("Capture refused: total pointing "
                                        "error %.3f deg > 0.05 deg"
                                        % s.aocs_total_err_seen)}
                if s.aocs_rate_stab_seen > 0.005:
                    return {"success": False,
                            "code": "RATE_STABILITY_HIGH",
                            "error_code": 0x0023,
                            "message": ("Capture refused: rate stability "
                                        "%.4f deg/s > 0.005 deg/s"
                                        % s.aocs_rate_stab_seen)}

            # ── Phase E WP-1: commanded scene_id + lines ──
            # scene_id 0 = auto-assign (preserves the legacy behaviour);
            # a ground-assigned id lets the pass plan pre-book the downlink
            # (PAYLOAD_DOWNLOAD_IMAGE / DELETE_IMAGE address it later).
            req_scene = int(cmd.get("scene_id", 0) or 0)
            if req_scene and any(img.get("scene_id") == req_scene
                                 for img in s.image_catalog):
                return {"success": False, "error_code": 0x0003,
                        "message": f"Scene id {req_scene} already in the "
                                   "image catalogue"}
            lines = int(cmd.get("lines", 0) or 0)
            if lines == 0:
                lines = 2048           # catalogue default
            if not (256 <= lines <= 20000):
                return {"success": False, "error_code": 0x0003,
                        "message": f"lines = {lines} outside 256-20000"}

            # ── Phase E WP-8: frame size follows the commanded acquisition.
            # Reference frame (payload.yaml image_size_mb, 4.0 MB) is 2048
            # lines x 4 bands; raw volume scales with lines and enabled
            # bands — the arithmetic the pass-planning lesson rests on.
            active_bands = max(1, bin(s.band_enable_mask & 0x0F).count('1'))
            base_size = s.image_size_mb * (lines / 2048.0) * (active_bands / 4.0)
            # Compute actual stored size with compression (Defect 3 — payload.md §3.6)
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
                quality = self.rng.uniform(10.0, 30.0)
                s.corrupt_remaining -= 1
                s.checksum_errors += 1
            elif s.ccd_line_dropout:
                status = 1  # PARTIAL
                quality = self.rng.uniform(60.0, 85.0)
            elif s.fpa_degraded:
                quality = self.rng.uniform(80.0, 95.0)

            if req_scene:
                scene_id = req_scene
                # Keep the auto-assign counter ahead of ground-assigned ids
                # so a later auto capture cannot collide.
                self._next_scene_id = max(self._next_scene_id, req_scene)
            else:
                self._next_scene_id += 1
                scene_id = self._next_scene_id
            img = {
                "scene_id": scene_id,
                "timestamp": self._now(),
                # Phase E WP-1: geolocation stamped ON BOARD from the
                # propagated sub-satellite point at shutter open (sampled
                # from orbit_state in tick) — not a TC argument.
                "lat": round(s.orbit_lat_deg, 4),
                "lon": round(s.orbit_lon_deg, 4),
                "lines": lines,
                "quality": quality,
                "status": status,
                "size_mb": stored_size,  # CHANGED: now uses compressed size
                "raw_size_mb": base_size,
                "compression_ratio": s.compression_ratio,
                "compression_algorithm": s.compression_algorithm,
                "segment": seg,
                # Phase C WP-6: actual pointing error at capture start,
                # updated with the worst error if FINE is lost mid-strip.
                "pointing_err_deg": round(s.aocs_total_err_seen, 4)
                    if s.aocs_seen else None,
                "aocs_mode": s.aocs_mode_seen if s.aocs_seen else None,
            }
            s.image_catalog.append(img)
            # Start the strip-acquisition monitor over the COMMANDED strip
            # length (lines / line_rate; 2048 @ 500 Hz = 4.1 s)
            line_rate = max(1.0, self._line_rate_hz)
            s.strip_remaining_s = float(lines) / line_rate
            s.strip_worst_err_deg = (s.aocs_total_err_seen
                                     if s.aocs_seen else 0.0)
            s.strip_scene_id = scene_id
            # Cap catalog to prevent unbounded memory growth
            if len(s.image_catalog) > 1000:
                s.image_catalog.pop(0)  # evict oldest
            s.image_count += 1
            s.mem_used_mb += stored_size
            s.store_used_pct = min(
                100.0, s.mem_used_mb / s.mem_total_mb * 100.0
            )
            s.current_scene_id = scene_id
            s.last_scene_id = scene_id
            s.last_scene_quality = quality

            return {
                "success": True,
                "scene_id": scene_id,
                "lat": img["lat"],
                "lon": img["lon"],
                "lines": lines,
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
            if mask == 0:
                # Phase E WP-3: all-bands-off is REJECTED — an imager with no
                # enabled band cannot produce a product, and the old silent
                # accept made every subsequent capture 0-band garbage.
                return {"success": False, "error_code": 0x0003,
                        "message": "Band mask 0x00 rejected — at least one "
                                   "band (bits 0-3) must be enabled"}
            if 0 < mask <= 0x0F:
                s.band_enable_mask = mask
                s.active_bands = bin(mask).count('1')
                return {"success": True}
            return {"success": False, "error_code": 0x0003,
                    "message": "Invalid band mask (0x01-0x0F)"}

        elif command == "set_integration_time":
            # Set per-band integration times (ms, blue/green/red/nir).
            times = cmd.get("times", [2.0, 2.0, 2.0, 2.0])
            if len(times) >= 4:
                bad = [t for t in times[:4] if not (0.1 <= float(t) <= 655.35)]
                if bad:
                    return {"success": False, "error_code": 0x0003,
                            "message": f"Integration time {bad[0]} ms outside "
                                       "0.10-655.35 ms"}
                s.integration_time_blue = float(times[0])
                s.integration_time_green = float(times[1])
                s.integration_time_red = float(times[2])
                s.integration_time_nir = float(times[3])
                s.integration_time_ms = sum(float(t) for t in times[:4]) / 4.0
                return {"success": True}
            return {"success": False, "error_code": 0x0003,
                    "message": "Invalid integration times"}

        elif command == "set_detector_gain":
            # Phase E WP-3: REJECT out-of-envelope gain (never clamp — the
            # readback must equal the command bit-for-bit or refuse).
            gain = float(cmd.get("gain", 1.0))
            if not (0.1 <= gain <= 10.0):
                return {"success": False, "error_code": 0x0003,
                        "message": f"Detector gain {gain:.3f} outside "
                                   "0.100-10.000"}
            s.detector_gain = gain
            return {"success": True}

        elif command == "set_cooler_setpoint":
            # Adjust cooler temperature target
            env = self._fpa_env
            target = float(cmd.get("setpoint", env.setpoint_default_c))
            if env.setpoint_min_c <= target <= env.setpoint_max_c:
                self._fpa_target = target
                s.cooler_setpoint_c = target
                return {"success": True}
            return {"success": False, "error_code": 0x0003,
                    "message": f"Cooler setpoint {target:.2f} C outside the "
                               f"Peltier envelope ({env.setpoint_min_c:.0f} C "
                               f"to {env.setpoint_max_c:.0f} C)"}

        elif command == "start_calibration":
            # Begin calibration sequence. Phase E WP-7: `mode` selects the
            # phase set — 0 = DARK only (COM-011 step 2), 1 = FLAT only,
            # 2 = BOTH (legacy dark-then-flat sequence).
            if s.calibration_active:
                return {"success": False, "message": "Calibration already in progress"}
            if s.mode != 2:
                return {"success": False, "message": "Must be in IMAGING mode"}
            cal_mode = int(cmd.get("mode", 2))
            if cal_mode not in (0, 1, 2):
                return {"success": False, "error_code": 0x0003,
                        "message": f"Unknown calibration mode {cal_mode} "
                                   "(0=DARK, 1=FLAT, 2=BOTH)"}
            s.calibration_active = True
            s.calibration_mode = cal_mode
            s.calibration_state = 2 if cal_mode == 1 else 1
            s.calibration_progress = 0.0
            s.calibration_timer = s.calibration_duration_s / 2  # Half duration per phase (Defect 1)
            s.dark_frame_count = 0  # Initialize frame counters
            s.flat_frame_count = 0
            return {"success": True, "message": "Calibration started"}

        elif command == "set_cal_lamp":
            # Phase E WP-7 — PAYLOAD_CAL_LAMP (func 95). Honours the S2
            # device gate: an unpowered lamp (device 0x0602 off) cannot light.
            on = bool(cmd.get("on", False))
            if on and not s.device_states.get(0x0602, True):
                return {"success": False, "error_code": 0x0007,
                        "message": "Calibration lamp device (0x0602) is "
                                   "powered off"}
            s.cal_lamp_on = on
            return {"success": True}

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
            # Override compression ratio and algorithm (Defect 3 — payload.md §3.6).
            # Phase E WP-3: the algorithm octet is now on the wire (func 39
            # octet 1); ratio must be 0 (AUTO) or within the scene model's
            # 1.0-4.0 envelope — rejected otherwise, never clamped.
            algo = int(cmd.get("algorithm", s.compression_algorithm))
            if not (0 <= algo <= 3):
                return {"success": False, "error_code": 0x0003,
                        "message": f"Unknown compression algorithm {algo} "
                                   "(0=NONE, 1=CCSDS-121, 2=CCSDS-122, "
                                   "3=CCSDS-123)"}
            ratio = float(cmd.get("ratio", 0.0))
            if ratio != 0.0 and not (1.0 <= ratio <= 4.0):
                return {"success": False, "error_code": 0x0003,
                        "message": f"Compression ratio {ratio:.1f} outside "
                                   "1.0-4.0 (0 = auto)"}
            s.compression_algorithm = algo
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
        from smo_common.state import rng_state_to_json
        d = dataclasses.asdict(self._state)
        # image_catalog contains dicts, already serializable
        d["_rng_state"] = rng_state_to_json(self.rng.getstate())
        return d

    def set_state(self, state: dict[str, Any]) -> None:
        # Container fields are deep-copied by restore_dataclass_state: the
        # plain setattr this used to do aliased the snapshot's own dicts and
        # lists into live model state (G2-f).
        from smo_common.state import restore_dataclass_state, restore_rng
        restore_rng(self.rng, state.get("_rng_state"))
        restore_dataclass_state(self._state, state, skip=("_rng_state",))

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

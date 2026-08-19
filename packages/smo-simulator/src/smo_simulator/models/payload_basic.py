"""SMO Simulator — Enhanced Payload Model.

Image catalog with metadata, memory segment model with individual failure,
FPA thermal model, image capture/download/delete commands, corrupted image
handling, comprehensive failure injection, and ocean current multispectral
imaging with per-band SNR, attitude-quality coupling, and scene-dependent
compression.
"""
import hashlib
import math
import random
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

from smo_common.config.fpa_envelope import FPAEnvelope
from smo_common.models.subsystem import SubsystemModel
from smo_simulator.failure_spec import (
    FailureSpec, ParamSpec, T_BOOL, enum, number,
)

# ── Wire-decode epsilon for the FPA setpoint envelope (2026-08b) ──
# PAYLOAD_COOLER_SETPOINT (S8 func 36) carries the setpoint as unsigned
# CENTI-KELVIN, and the dispatcher decodes it as `raw * 0.01 - 273.15`. The
# documented top of the envelope, raw 27315 = 0.00 degC, comes out of that
# expression as +5.684341886080802e-14 — genuinely greater than the 0.0 upper
# bound, so the ONE value the catalogue prints as the maximum was rejected with
# "0.00 C outside the envelope (-20 C to 0 C)": a refusal whose own message
# says the value is legal. The same 1e-6 K tolerance is applied in
# tcs_basic.set_fpa_setpoint, which decodes the identical field.
SETPOINT_EPSILON_C = 1e-6

# ── Payload event table (0x0600-0x06FF) ──
# ONE table, id -> (name, severity), and it must agree line-for-line with
# configs/eosat1/events/event_catalog.yaml. Before 2026-08b the model carried
# two separate literal dicts that stopped at 0x060D, so 0x060E
# SHUTTER_TEST_COMPLETE and 0x060F FILTER_ROTATION_COMPLETE — both of which the
# model already emitted — downlinked to the ground as "UNKNOWN", and 0x0611
# TRANSFER_COMPLETE did too. 0x0610 TRANSFER_START was promised by a code
# comment ("will be emitted in next tick") and sent by nothing at all.
PAYLOAD_EVENTS: dict[int, tuple[str, str]] = {
    0x0600: ("IMAGING_START", "INFO"),
    0x0601: ("IMAGING_STOP", "INFO"),
    0x0602: ("PAYLOAD_MODE_CHANGE", "INFO"),
    0x0603: ("STORAGE_WARNING", "LOW"),
    0x0604: ("FPA_OVERTEMP", "MEDIUM"),
    0x0605: ("FPA_UNDERTEMP", "MEDIUM"),
    0x0606: ("COOLER_FAILURE", "HIGH"),
    0x0607: ("IMAGE_CHECKSUM_ERROR", "MEDIUM"),
    0x0608: ("SNR_DEGRADED", "LOW"),
    0x0609: ("STORAGE_FULL", "HIGH"),
    0x060A: ("BAD_SEGMENT_DETECTED", "MEDIUM"),
    0x060B: ("CALIBRATION_COMPLETE", "INFO"),
    0x060C: ("STORAGE_CRITICAL", "HIGH"),
    0x060D: ("COMPRESSION_ERROR", "MEDIUM"),
    0x060E: ("SHUTTER_TEST_COMPLETE", "INFO"),
    0x060F: ("FILTER_ROTATION_COMPLETE", "INFO"),
    0x0610: ("TRANSFER_START", "INFO"),
    0x0611: ("TRANSFER_COMPLETE", "INFO"),
    0x0612: ("CALIBRATION_ABORTED", "MEDIUM"),
    0x0613: ("IMAGE_CAPTURED", "INFO"),
    0x0614: ("IMAGE_RELEASED", "INFO"),
}


def snap_to_envelope(value: float, low: float, high: float,
                     eps: float = SETPOINT_EPSILON_C) -> float:
    """Snap a wire-decoded temperature onto an envelope bound within `eps`.

    Nothing a 0.01 K/LSB field can express is finer than 1e-6 K, so a value
    that close to a bound IS that bound; snapping is exact, not lenient.
    """
    if low - eps <= value < low:
        return low
    if high < value <= high + eps:
        return high
    return value

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
    # 0=OFF, 1=STANDBY (cooler on), 2=IMAGING, 3=CALIBRATION.
    #
    # 2026-08b PL-d: mode 3 CALIBRATION is new. Radiometric calibration is a
    # shutter-closed / lamp-lit activity that a real EO payload performs in its
    # OWN mode — it is not a kind of imaging, and the old gate ("must be in
    # IMAGING") forced the operator to open the shutter and start integrating
    # the scene before asking for a dark frame, which is the one thing a dark
    # frame must not have. The value 2 is deliberately UNCHANGED: AOCS keys its
    # automatic desaturation inhibit on `payload.mode == 2` meaning IMAGING
    # (DISPLAY_REQUESTS_AOCS.md section 3, item 3), so CALIBRATION had to be a
    # NEW value, not a renumbering.
    mode: int = 0
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
    # Ground swath width. Recomputed from the optics and the live altitude
    # every tick; ~273 km at the TLE-true 513 km mean altitude (2026-08
    # epoch/altitude ruling — was quoted as 240 km at the retired 450 km
    # declaration).
    swath_width_km: float = 273.5

    # ── Multispectral imaging ──
    band_enable_mask: int = 0x0F  # All 4 bands enabled (bits 0-3)
    active_bands: int = 4
    band_snrs: dict = field(default_factory=lambda: {
        'blue': 40.0, 'green': 50.0, 'red': 48.0, 'nir': 42.0
    })
    att_quality_factor: float = 1.0
    att_error_deg: float = 0.0
    gsd_m: float = 33.4   # Ground sample distance at 513 km (see swath_width_km)
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
    # 0x0299 aocs.fine_degraded — FINE_POINT being flown on a 3-wheel set
    # (2026-08b R3). Recorded, not gated on; see the note in `capture`.
    aocs_fine_degraded_seen: bool = False
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
    # 2026-08b PL-d: why the last calibration stopped. 0 = ran to completion or
    # never started; non-zero = a precondition was violated mid-sequence and the
    # products were discarded. Downlinked at 0x0645 so "my calibration silently
    # produced nothing" is diagnosable from the ground.
    cal_abort_reason: int = 0   # 0=none 1=power 2=shutter 3=lamp 4=thermal 5=operator

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

    # ── Image data path (2026-08b PL-c) ──
    # Every stored product carries a `data_seed`; the bytes S13.3 serves are
    # DERIVED from it (see `image_block`), so the ground can checksum what it
    # received without the simulator holding megabytes of pixels in RAM. The
    # counter is part of the dataclass so it survives a breakpoint — a restored
    # spacecraft must not re-issue a seed a downlinked product already used.
    next_data_seed: int = 1
    # Bytes actually occupied by CATALOGUED products, tracked separately from
    # mem_used_mb (which also carries the store's pre-existing fill). Published
    # at 0x0647 so "what is in the store" and "what can I downlink" agree.
    product_bytes: int = 0
    # Achievable FPA floor and margin, sampled from the TCS cooler-authority
    # model each tick (2026-08b: cooling authority is no longer a flat 40 K).
    cooler_floor_c: float = 0.0
    cooler_margin_k: float = 0.0

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
        # NOTE (2026-08b, defect #132's payload twin): there is deliberately no
        # `self._fpa_target` here any more. It was a SECOND copy of the active
        # cooler setpoint that lived outside the dataclass, so `get_state()`
        # never captured it: after a breakpoint load with a non-default
        # setpoint, TCS regulated to the restored -20.5 C while the payload's
        # readiness window was still centred on the constructor's -15 C, and
        # 0x0610 FPA READY read NO forever with 0x0631 still showing the
        # commanded value. The active setpoint is `_state.cooler_setpoint_c`,
        # which is already in the dataclass, already persisted and already
        # published at 0x0631 — one setpoint, one copy.
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
        self._line_rate_hz = 244.0
        self._data_rate_mbps = 96.0
        # Focal-plane geometry (subsystems/payload.yaml). Defaults are the
        # flight values so a directly instantiated model still reports the
        # 240 km swath: 8192 px x 5.5 um on an 84.5 mm telescope.
        self._pixels_across_track = 8192
        self._pixel_pitch_um = 5.5
        self._focal_length_mm = 84.5
        self._bits_per_pixel = 12
        self._param_ids: dict[str, int] = {}
        self._next_scene_id = 100

    @property
    def name(self) -> str:
        return "payload"

    def configure(self, config: dict[str, Any]) -> None:
        # The ACTIVE setpoint lives in the dataclass (see __init__) so that a
        # breakpoint round-trip restores it. configure() seeds it; every later
        # write goes through `set_cooler_setpoint`.
        self._state.cooler_setpoint_c = config.get("fpa_cooler_target_c", -15.0)
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
        self._line_rate_hz = config.get("line_rate_hz", 244.0)
        self._data_rate_mbps = config.get("data_rate_mbps", 96.0)
        # Optics — the single source of swath (0x0619) and GSD (0x0628).
        self._pixels_across_track = int(config.get("pixels_across_track", 8192))
        self._pixel_pitch_um = float(config.get("pixel_pitch_um", 5.5))
        self._focal_length_mm = float(config.get("focal_length_mm", 84.5))
        self._bits_per_pixel = int(config.get("bits_per_pixel", 12))

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

    def _event_description(self, evt_id: int, name: str) -> str:
        """Event text. A few events carry the number the operator needs."""
        s = self._state
        if evt_id == 0x0612:
            why = self._CAL_ABORT_NAMES.get(s.cal_abort_reason, "unknown")
            return f"{name}: {why}"
        if evt_id in (0x0610, 0x0611):
            return (f"{name}: scene {s.transfer_scene_id}, "
                    f"{s.transfer_bytes_total} bytes")
        if evt_id in (0x0613, 0x0614):
            return f"{name}: scene {s.last_scene_id}"
        return name

    # ── Image products: bytes without megabytes (2026-08b PL-c) ──────────
    #
    # "Where do the image packets go?" — before this, nowhere: `capture` built
    # a metadata dict and no bytes existed anywhere, so S13.3 answered every
    # segment request with `bytes([(segment_num + i) % 256 ...])`, a ramp whose
    # own source comment admitted it was fake. The ground could not checksum a
    # downlink, and a CORRUPT product looked byte-for-byte identical to a good
    # one.
    #
    # Holding real pixels is not an option — a 20 GB store would be 20 GB of
    # RAM, and breakpoints serialise the model. So each product carries a
    # `data_seed` and its bytes are DERIVED from it: block N is a keyed BLAKE2b
    # stream over (seed, block index). That is deterministic (the same block
    # requested twice is identical, across restarts and breakpoint restores),
    # O(1) in memory, and seekable — S13.3 can serve block 900 without
    # generating blocks 0-899.
    BLOCK_BYTES = 1024

    @staticmethod
    def _derive_block(data_seed: int, block_index: int,
                      block_bytes: int = BLOCK_BYTES) -> bytes:
        """Deterministic pseudo-random block for (product seed, block index)."""
        out = bytearray()
        counter = 0
        key = struct.pack('>Q', data_seed & 0xFFFFFFFFFFFFFFFF)
        while len(out) < block_bytes:
            h = hashlib.blake2b(struct.pack('>II', block_index, counter),
                                key=key, digest_size=64)
            out += h.digest()
            counter += 1
        return bytes(out[:block_bytes])

    def image_block(self, scene_id: int, block_index: int) -> bytes | None:
        """Bytes of one 1024-byte block of a stored product, or None.

        A CORRUPT product (status 2) serves DAMAGED bytes: every 37th block is
        XORed with 0xFF. The catalogue's `checksum` is the one the instrument
        computed over the CLEAN stream at capture, so a ground CRC of what was
        received disagrees — a corrupted image now actually looks corrupted,
        which is the point of the corrupt-image failure injection.
        """
        img = self.find_image(scene_id)
        if img is None:
            return None
        total = self.image_block_count(img)
        if not (0 <= block_index < total):
            return None
        blk = self._derive_block(int(img.get("data_seed", 0)), block_index)
        if int(img.get("status", 0)) == 2 and block_index % 37 == 0:
            blk = bytes(b ^ 0xFF for b in blk)
        return blk

    def find_image(self, scene_id: int) -> dict | None:
        for img in self._state.image_catalog:
            if img.get("scene_id") == scene_id:
                return img
        return None

    @classmethod
    def image_size_bytes(cls, img: dict) -> int:
        return max(1, int(round(float(img.get("size_mb", 0.0)) * 1e6)))

    @classmethod
    def image_block_count(cls, img: dict) -> int:
        return (cls.image_size_bytes(img) + cls.BLOCK_BYTES - 1) \
            // cls.BLOCK_BYTES

    def image_checksum(self, scene_id: int) -> int | None:
        """CRC-32 over the product's CLEAN byte stream, cached in the entry.

        Computed lazily (a 1.14 MB product is ~1120 blocks) so a capture stays
        cheap; the ground asks for it once, when it opens the transfer.
        """
        img = self.find_image(scene_id)
        if img is None:
            return None
        cached = img.get("checksum")
        if cached is not None:
            return int(cached)
        seed = int(img.get("data_seed", 0))
        crc = 0
        for i in range(self.image_block_count(img)):
            crc = zlib.crc32(self._derive_block(seed, i), crc)
        img["checksum"] = crc & 0xFFFFFFFF
        return img["checksum"]

    def release_image(self, scene_id: int) -> dict:
        """Delete a product and give its storage back (S13.5 completion).

        The old S13.5 popped a dictionary out of the dispatcher and freed
        nothing: `mem_used_mb` was measured unchanged after ending a transfer,
        so a spacecraft could downlink its entire store and still report it
        full. Downlinking is not deleting on a real spacecraft either — but
        S13.5 is "complete transfer session AND FREE RESOURCES" in this
        mission's own catalogue, so it is the delete point.
        """
        s = self._state
        img = self.find_image(scene_id)
        if img is None:
            return {"success": False, "error_code": 0x0003,
                    "message": f"Scene {scene_id} not in the image catalogue"}
        freed_mb = float(img.get("size_mb", 0.0))
        s.image_catalog.remove(img)
        s.mem_used_mb = max(0.0, s.mem_used_mb - freed_mb)
        s.product_bytes = max(0, s.product_bytes - self.image_size_bytes(img))
        s.store_used_pct = max(0.0, s.mem_used_mb / s.mem_total_mb * 100.0)
        s.image_count = max(0, s.image_count - 1)
        s.last_scene_id = scene_id
        self._queue_event(0x0614)   # IMAGE_RELEASED
        return {"success": True, "scene_id": scene_id, "freed_mb": freed_mb}

    def _queue_event(self, evt_id: int) -> None:
        """Emit one event immediately (command paths, outside the tick loop)."""
        eng = getattr(self, "_engine", None)
        if eng is None:
            return
        name, severity = PAYLOAD_EVENTS.get(evt_id, ("UNKNOWN", "INFO"))
        try:
            eng._model_event_queue.put_nowait({
                'event_id': evt_id, 'severity': severity,
                'subsystem': 'payload',
                'description': self._event_description(evt_id, name),
            })
        except Exception:
            pass

    # ── Calibration preconditions (2026-08b PL-d) ────────────────────────
    #
    # A radiometric calibration frame is defined by the optical path it is
    # taken through, so the model gates on that path rather than on a mode
    # word. The three phases need mutually exclusive optics:
    #
    #   DARK  shutter CLOSED, lamp OFF   — measures the detector's own dark
    #                                      current and offset, so ANY photon
    #                                      reaching the array invalidates it.
    #   FLAT  shutter OPEN,   lamp ON    — measures per-pixel gain against a
    #                                      uniform source, so it needs the
    #                                      lamp lit and the light path open.
    #   BOTH  starts in the DARK configuration; the on-board sequencer opens
    #         the shutter and lights the lamp at the DARK -> FLAT transition,
    #         because no ground step can land between two phases 15 s apart.
    #
    # All three additionally need the FPA at its operating temperature: dark
    # current roughly doubles every 6-8 K, so coefficients taken at the wrong
    # temperature describe a detector the mission does not have.
    _CAL_ABORT_NAMES = {
        0: "none", 1: "detector unpowered", 2: "shutter moved",
        3: "calibration lamp state changed", 4: "FPA left its regulation band",
        5: "operator abort",
    }

    def _calibration_precondition_error(self, cal_mode: int):
        """Return an error dict if `cal_mode` cannot start now, else None."""
        s = self._state
        if s.mode == 0:
            return {"success": False, "error_code": 0x0004,
                    "message": "Payload is OFF — calibration needs the "
                               "detector powered (PAYLOAD_SET_MODE 1 or 3)"}
        if s.mode == 2:
            # Never from IMAGING: the scene is on the detector.
            # 2026-08b: dedicated codes 0x0024-0x0027 (DISPLAY_REQUESTS_PAYLOAD
            # §8.1) now exist in smo_common.protocol.pus_services.FAILURE_CODES,
            # so each refusal renders as its own sentence instead of the
            # generic 0x0005 / 0x0007. 0x0021-0x0023 stay reserved for the
            # three CAPTURE pointing refusals.
            return {"success": False, "code": "CAL_REFUSED_IMAGING",
                    "error_code": 0x0024,
                    "message": "Calibration refused in IMAGING — a calibration "
                               "frame must not see the scene. Command "
                               "PAYLOAD_SET_MODE 1 (STANDBY) or 3 "
                               "(CALIBRATION) first"}
        if not s.device_states.get(0x0600, True):
            return {"success": False, "error_code": 0x0007,
                    "message": "Focal plane array device (0x0600) is powered "
                               "off"}
        if not s.fpa_ready:
            return {"success": False, "code": "CAL_FPA_NOT_READY",
                    "error_code": 0x0025,
                    "message": (
                        "Calibration refused: FPA not thermally ready "
                        f"({s.fpa_temp:.1f} C, setpoint "
                        f"{s.cooler_setpoint_c:.1f} C). Dark current is "
                        "temperature-dependent — coefficients taken off "
                        "setpoint describe a different detector")}
        wants_dark = cal_mode in (0, 2)
        wants_flat = cal_mode in (1, 2)
        if wants_dark:
            if not s.device_states.get(0x0603, True):
                return {"success": False, "error_code": 0x0007,
                        "message": "Shutter mechanism device (0x0603) is "
                                   "powered off — cannot verify the dark path"}
            if s.shutter_position != 0:
                return {"success": False, "code": "CAL_SHUTTER_OPEN",
                        "error_code": 0x0026,
                        "message": "Calibration refused: shutter is OPEN. A "
                                   "dark frame is a shutter-closed acquisition "
                                   "— PAYLOAD_SET_SHUTTER 0 first"}
            if s.cal_lamp_on:
                return {"success": False, "code": "CAL_LAMP_ON",
                        "error_code": 0x0027,
                        "message": "Calibration refused: calibration lamp is "
                                   "ON during the dark phase — "
                                   "PAYLOAD_CAL_LAMP 0 first"}
        if wants_flat and not s.device_states.get(0x0602, True):
            return {"success": False, "error_code": 0x0007,
                    "message": "Calibration lamp device (0x0602) is powered "
                               "off — the flat-field phase has no source"}
        if cal_mode == 1:
            # FLAT-only: the ground sets the optics up itself.
            if s.shutter_position != 1:
                return {"success": False, "code": "CAL_SHUTTER_CLOSED",
                        "error_code": 0x0026,
                        "message": "Calibration refused: flat field needs the "
                                   "shutter OPEN (PAYLOAD_SET_SHUTTER 1)"}
            if not s.cal_lamp_on:
                return {"success": False, "code": "CAL_LAMP_OFF",
                        "error_code": 0x0027,
                        "message": "Calibration refused: flat field needs the "
                                   "calibration lamp ON (PAYLOAD_CAL_LAMP 1)"}
        return None

    # ── Deferred completion (verification-semantics ruling, 2026-08) ──

    #: Deferred-completion key for PAYLOAD_START_CALIBRATION. The run is a
    #: 30 s two-phase state machine (dark then flat) that discards its
    #: half-made products on abort, so "execution complete" on acceptance was
    #: a claim about frames that had not been integrated yet.
    COMPLETION_CALIBRATION = "payload.calibration"

    def _complete(self, key: str, success: bool = True,
                  error_code: int = 0x0001, reason: str = "") -> None:
        """Queue a deferred-completion report for the engine to drain."""
        if not hasattr(self, "_completions"):
            self._completions = []
        self._completions.append({"key": str(key), "success": bool(success),
                                  "error_code": int(error_code),
                                  "reason": str(reason or "")})

    def pop_completions(self) -> list[dict]:
        out = getattr(self, "_completions", [])
        self._completions = []
        return out

    def _abort_calibration(self, reason: int) -> None:
        """Stop a running calibration and discard its half-made products."""
        s = self._state
        self._complete(
            self.COMPLETION_CALIBRATION, False, 0x0026,
            "PAYLOAD_START_CALIBRATION aborted: "
            + self._CAL_ABORT_NAMES.get(reason, "unknown")
            + " — the half-made products were discarded")
        s.calibration_active = False
        s.calibration_state = 0
        s.calibration_progress = 0.0
        s.calibration_timer = 0.0
        s.dark_frame_count = 0
        s.flat_frame_count = 0
        s.cal_abort_reason = reason
        if s.mode == 3:
            s.mode = 1   # fall back to STANDBY; the cooler stays commanded

    def _refresh_calibration_valid_mask(self) -> None:
        """A band is calibrated when it has BOTH a bias and a gain coefficient.

        2026-08b PL-d: the old code set the mask to 0x0F only on the `mode == 2`
        (BOTH) completion path, so a DARK-only or FLAT-only run — the route
        COM-011 actually recommends — completed with the mask still 0x00 and
        the spacecraft could never reach a valid-calibration state. Deriving
        the mask from the coefficients instead of from the run type means a
        DARK run that completes an earlier FLAT run validates the pair, which
        is how radiometric calibration actually accumulates.
        """
        s = self._state
        mask = 0
        for band in SPECTRAL_BANDS:
            bid = band['id']
            if s.bias_coeff.get(bid) is not None \
                    and s.gain_coeff.get(bid) is not None:
                mask |= (1 << band['bit'])
        s.calibration_valid_mask = mask

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
            self._abort_calibration(1)   # detector unpowered
        # ── 2026-08b PL-d: hold the optical preconditions for the WHOLE run ──
        # Checking them only at start would let an operator (or a procedure)
        # open the shutter or light the lamp half way through a dark phase and
        # still get a "CALIBRATION_COMPLETE" and a valid mask over frames that
        # saw the scene. The sequencer owns the optics only across the DARK ->
        # FLAT transition of a BOTH run; outside that, any change aborts.
        if s.calibration_active:
            if s.calibration_state == 1:      # DARK: closed and dark
                if s.shutter_position != 0:
                    self._abort_calibration(2)
                elif s.cal_lamp_on:
                    self._abort_calibration(3)
            elif s.calibration_state == 2 and s.calibration_mode != 0:
                if s.shutter_position != 1:   # FLAT: open and lit
                    self._abort_calibration(2)
                elif not s.cal_lamp_on:
                    self._abort_calibration(3)
            if s.calibration_active and not s.fpa_ready:
                self._abort_calibration(4)
            if not s.calibration_active:
                events_to_emit.append(0x0612)  # CALIBRATION_ABORTED
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
                        self._refresh_calibration_valid_mask()
                        s.calibration_active = False
                        s.calibration_state = 3  # COMPLETE (dark set only)
                        s.calibration_progress = 100.0
                        s.last_calibration_time = self._now()
                        if s.mode == 3:
                            s.mode = 1   # CALIBRATION -> STANDBY
                        events_to_emit.append(0x060B)  # CALIBRATION_COMPLETE
                        self._complete(self.COMPLETION_CALIBRATION, True)
                else:
                    s.calibration_progress = 50.0 * (half_duration - s.calibration_timer) / half_duration
                    # 2026-08b PL-d: `<= 0`, not `<= half_duration`. The phase
                    # is entered with `calibration_timer = half_duration`, so
                    # the old comparison was true on the FIRST tick and a BOTH
                    # run skipped its entire dark phase in one step: 15 s of
                    # dark integration collapsed to one dt, and the dark
                    # coefficients were fabricated rather than accumulated.
                    if s.calibration_timer <= 0:
                        for band_id in ['blue', 'green', 'red', 'nir']:
                            s.bias_coeff[band_id] = 0.0
                        s.calibration_state = 2  # FLAT_FIELD
                        s.calibration_timer = half_duration
                        s.dark_frame_count = 10  # Ensure at least 10
                        # The on-board sequencer owns the optics across the
                        # phase boundary — no ground step can land between two
                        # phases 15 s apart. Honour the S2 device gates: an
                        # unpowered shutter or lamp aborts instead of pretending.
                        if not s.device_states.get(0x0603, True):
                            self._abort_calibration(2)
                            events_to_emit.append(0x0612)
                        elif not s.device_states.get(0x0602, True):
                            self._abort_calibration(3)
                            events_to_emit.append(0x0612)
                        else:
                            s.shutter_position = 1   # open the light path
                            s.cal_lamp_on = True     # light the flat-field lamp
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
                    # Compute the FLAT product: per-band gain. The dark phase
                    # (BOTH) or an earlier DARK run supplies the bias half.
                    for band_id in ['blue', 'green', 'red', 'nir']:
                        s.gain_coeff[band_id] = 1.0  # Simplified: would normally compute from flat_frame_buffer
                    # Derived from the coefficients that now exist, not from
                    # which run type produced them (see the helper).
                    self._refresh_calibration_valid_mask()
                    s.calibration_active = False
                    s.calibration_state = 3  # COMPLETE
                    s.calibration_progress = 100.0
                    s.last_calibration_time = self._now()
                    if s.calibration_mode == 2:
                        # The sequencer lit the lamp at the phase boundary; it
                        # owns switching it off again.
                        s.cal_lamp_on = False
                    if s.mode == 3:
                        s.mode = 1   # CALIBRATION -> STANDBY
                    events_to_emit.append(0x060B)  # CALIBRATION_COMPLETE
                    self._complete(self.COMPLETION_CALIBRATION, True)

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
            target, tau = s.cooler_setpoint_c, self._tau_cool
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
        temp_in_range = self._fpa_env.is_ready(s.fpa_temp,
                                               s.cooler_setpoint_c)
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
        elif s.mode in (1, 3):  # STANDBY / CALIBRATION (cooler on, no scene)
            # CALIBRATION is thermally and electrically a STANDBY: the cooler
            # runs and the detector is powered, but nothing is being read out
            # to the mass memory — the frames are consumed on board into
            # coefficients, they are not products.
            if not _tcs_in_loop:
                s.cooler_active = True
            s.line_rate = 0.0
            s.data_rate_mbps = 0.0
            s.duty_cycle_pct = 0.0
        elif s.mode == 2:  # IMAGING
            # ── 2026-08b PL-b: readout ONLY during a commanded strip ──
            # This block used to run whenever the payload was in IMAGING with a
            # ready FPA, and it accreted `data_rate_mbps` worth of volume every
            # tick with no capture, no catalogue entry and no product: measured
            # 9.735 MB/s, which fills the 20 GB store in ~33 minutes from empty
            # with ZERO PAYLOAD_CAPTURE commands. One real capture stores
            # 1.14 MB, so the phantom stream outran real products ~9000:1, and
            # it contradicted NOM-002's teaching note ("images do NOT appear on
            # their own"), subsystems/payload.yaml's own design rationale and
            # the pass-planning arithmetic the link-rate ladder was built on.
            #
            # A push-broom imager reads out while it is scanning a commanded
            # strip and is otherwise idle. `strip_remaining_s` is exactly that
            # window (set by `capture` from lines / line_rate_hz), and the
            # volume is booked ONCE, by `capture`, from the frame it actually
            # produced. Nothing is accreted here.
            reading_out = s.strip_remaining_s > 0.0
            if reading_out and s.fpa_ready and not s.cooler_failed:
                s.line_rate = self._line_rate_hz + self.rng.gauss(0, 2.0)
                s.data_rate_mbps = self._data_rate_mbps + self.rng.gauss(0, 0.5)
                # Checksum errors
                # Rate per unit sim time, not per tick (plan A-3)
                error_prob = 0.0001 * dt * (2 if s.fpa_degraded else 1)
                if s.ccd_line_dropout:
                    error_prob *= 3
                if self.rng.random() < error_prob:
                    s.checksum_errors += 1
            else:
                s.line_rate = 0.0
                s.data_rate_mbps = 0.0
            # Duty cycle is "how hard has the instrument been worked", which is
            # a property of being in IMAGING (thermal load, TEC demand), not of
            # reading out — it keeps its previous meaning.
            if s.fpa_ready and not s.cooler_failed:
                s.duty_cycle_pct = min(
                    100.0,
                    s.duty_cycle_pct + dt / 60.0 * 10.0,
                )

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
            s.aocs_fine_degraded_seen = bool(
                shared_params.get(0x0299, 0.0))         # 3-wheel FINE

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

        # ── Swath width and GSD from the optics and the altitude ──
        #
        # The geometry is derived from the focal-plane parameters in
        # subsystems/payload.yaml, not from hard-coded constants (this used to
        # be ifov = 20 urad x 5000 px = 0.1 rad -> 45 km, which is not the
        # instrument the courseware describes). The optics were sized to give
        # the owner-ruled 240 km swath at the previously declared 450 km
        # altitude; the 2026-08 TLE-truth ruling puts the mean altitude at
        # 513 km, where the same FOV gives ~273 km.
        #
        #   ifov      = pixel_pitch / focal_length          (rad per pixel)
        #   full FOV  = 2 * atan(N * pitch / 2 / focal_length)
        #   swath     = 2 * alt * tan(FOV/2)   (flat-Earth, same formula the
        #                                       planner uses — the two agree)
        #   GSD       = alt * ifov                          (at nadir)
        #
        # With 8192 px x 5.5 um and f = 84.5 mm (FOV = 29.856 deg):
        #   at 450 km: swath = 239.9 km, GSD = 29.3 m
        #   at 513 km (TLE mean altitude, 2026-08): swath = 273.5 km,
        #   GSD = 33.4 m.
        alt_km = orbit_state.alt_km if hasattr(orbit_state, 'alt_km') else 500.0
        half_fov_rad = math.atan(
            (self._pixels_across_track * self._pixel_pitch_um * 1e-3)
            / 2.0 / self._focal_length_mm)
        s.swath_width_km = 2.0 * alt_km * math.tan(half_fov_rad)
        ifov_rad = (self._pixel_pitch_um * 1e-3) / self._focal_length_mm
        s.gsd_m = alt_km * 1000.0 * ifov_rad

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
        # 2026-08b PL-c: the transfer used to drain its byte counter at a
        # hard-coded 10 Mbps with no reference to contact, to the transmitter,
        # or to the TT&C rate tier — an on-board "downlink" that ran through
        # eclipse, out of contact, with the PA off, at 78x the spacecraft's own
        # top tier (128 kbps). It now moves bytes at the ACTIVE information
        # rate (0x0506) and only while the downlink is really up (0x0501 link
        # status), so a transfer stalls when the pass ends and resumes on the
        # next one — which is the whole pass-planning lesson.
        if s.transfer_active and s.transfer_bytes_total > 0:
            rate_bps = shared_params.get(0x0506)
            link_up = shared_params.get(0x0501)
            if rate_bps is None:
                # No TT&C in the loop (unit harness): keep the configured rate.
                bytes_per_sec = s.transfer_rate_mbps * 1e6 / 8
            elif not link_up:
                bytes_per_sec = 0.0
            else:
                bytes_per_sec = float(rate_bps) / 8.0
            s.transfer_rate_mbps = bytes_per_sec * 8.0 / 1e6
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
                    name, severity = PAYLOAD_EVENTS.get(evt_id,
                                                        ("UNKNOWN", "INFO"))
                    self._engine._model_event_queue.put_nowait({
                        'event_id': evt_id,
                        'severity': severity,
                        'subsystem': 'payload',
                        'description': self._event_description(evt_id, name),
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

        # ── 2026-08b: the payload state the ground could not see ──
        shared_params[0x0644] = float(s.calibration_mode)      # cal_mode
        shared_params[0x0645] = float(s.cal_abort_reason)      # cal_abort_reason
        shared_params[0x0646] = float(s.calibration_valid_mask)  # cal_valid_mask
        shared_params[0x0647] = float(s.product_bytes) / 1e6   # product volume MB
        shared_params[0x0648] = float(len(s.image_catalog))    # products held
        shared_params[0x0649] = 1.0 if s.strip_remaining_s > 0.0 else 0.0
        # Cooler authority, read back from the TCS model (0x0424/0x0425) so the
        # operator can see how much lift is LEFT rather than only whether the
        # setpoint is currently held. Falls back to the setpoint when no TCS is
        # in the loop, i.e. margin 0 rather than a fabricated number.
        _floor = shared_params.get(0x0424)
        s.cooler_floor_c = float(_floor) if _floor is not None \
            else s.cooler_setpoint_c
        s.cooler_margin_k = s.cooler_setpoint_c - s.cooler_floor_c
        shared_params[0x064A] = s.cooler_floor_c
        shared_params[0x064B] = s.cooler_margin_k

    def _now(self) -> float:
        """ONBOARD time in CUC seconds since the mission epoch (J2000).

        Both users of this helper are timestamps the spacecraft stamps onto
        things it downlinks — ``last_calibration_time`` (0x062C, sent next to
        a CUC secondary header) and the capture time in the image catalogue —
        so they must be on the OBC's own clock, drift and all, not on sim time
        and certainly not on the Unix epoch this used to return (spec Part
        C.7). Standalone (no engine) it is 0.0, which is J2000 — a legitimate
        onboard time for an OBC that has not been set.
        """
        eng = getattr(self, "_engine", None)
        clock = getattr(eng, "_get_cuc_time", None)
        return float(clock()) if callable(clock) else 0.0

    def get_telemetry(self) -> dict[int, float]:
        return {0x0601: self._state.fpa_temp}

    def handle_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        command = cmd.get("command", "")
        s = self._state

        if command == "set_mode":
            m = int(cmd.get("mode", 0))
            # 3 = CALIBRATION (2026-08b PL-d). Leaving CALIBRATION while a
            # sequence is running aborts it rather than orphaning it.
            if m in (0, 1, 2, 3):
                if s.calibration_active and m != 3:
                    self._abort_calibration(5)   # operator abort
                    self._queue_event(0x0612)
                s.mode = m
                return {"success": True}
            return {"success": False, "error_code": 0x0003,
                    "message": f"Payload mode {m} outside 0-3 (0=OFF, "
                               "1=STANDBY, 2=IMAGING, 3=CALIBRATION)"}

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
            # ── 2026-08b PL-d: the shutter is part of the optical path ──
            # `capture` never read `shutter_position` at all, so an image could
            # be acquired with the shutter CLOSED and the catalogue would carry
            # a 100 %-quality product of the back of the shutter blade.
            if s.shutter_position != 1:
                return {"success": False,
                        "code": "SHUTTER_CLOSED",
                        "error_code": 0x0028,
                        "message": "Capture refused: shutter is CLOSED "
                                   "(PAYLOAD_SET_SHUTTER 1 to open)"}
            if not s.device_states.get(0x0600, True):
                return {"success": False, "error_code": 0x0007,
                        "message": "Focal plane array device (0x0600) is "
                                   "powered off"}
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
                # 2026-08b: the AOCS owner has made FINE_POINT legal on a
                # 3-wheel set with a relaxed jitter spec (0x0299
                # aocs.fine_degraded). A degraded FINE set IS permitted to
                # capture — the gate above already enforces the numbers that
                # matter (0.05 deg total error, 0.005 deg/s rate stability),
                # and a wheel count is not a pointing spec. It IS product
                # provenance, so it is stamped into the catalogue entry: a
                # reprocessing campaign can find every strip flown on three
                # wheels without re-deriving it from the AOCS history.
                "fine_degraded": bool(s.aocs_fine_degraded_seen)
                    if s.aocs_seen else None,
                # 2026-08b PL-c: the product's byte identity. The bytes S13.3
                # serves are derived from this seed (see `image_block`), so a
                # downlink can be checksummed against `checksum`.
                "data_seed": s.next_data_seed,
                "checksum": None,     # filled lazily by image_checksum()
            }
            s.next_data_seed += 1
            s.image_catalog.append(img)
            s.product_bytes += self.image_size_bytes(img)
            # Start the strip-acquisition monitor over the COMMANDED strip
            # length (lines / line_rate; 2048 @ 244 Hz = 8.4 s)
            line_rate = max(1.0, self._line_rate_hz)
            s.strip_remaining_s = float(lines) / line_rate
            s.strip_worst_err_deg = (s.aocs_total_err_seen
                                     if s.aocs_seen else 0.0)
            s.strip_scene_id = scene_id
            # Catalogue cap. The old code popped the oldest entry SILENTLY, so
            # a product the ground had already been told about vanished with no
            # event and no storage accounting. Refusing is the honest answer:
            # the store is a finite resource and "delete something first" is a
            # real operational constraint. (The cap is never reached in normal
            # use — at 1.14 MB a product, 1000 products is 1.14 GB of a 20 GB
            # store; it exists to bound memory, not to manage capacity.)
            if len(s.image_catalog) > 1000:
                s.image_catalog.remove(img)
                s.product_bytes -= self.image_size_bytes(img)
                return {"success": False, "error_code": 0x0003,
                        "message": "Image catalogue full (1000 products) — "
                                   "delete or downlink a product first"}
            s.image_count += 1
            s.mem_used_mb += stored_size
            s.store_used_pct = min(
                100.0, s.mem_used_mb / s.mem_total_mb * 100.0
            )
            s.current_scene_id = scene_id
            s.last_scene_id = scene_id
            s.last_scene_quality = quality
            self._queue_event(0x0613)   # IMAGE_CAPTURED

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
                "image": img,
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
                        s.product_bytes = max(
                            0, s.product_bytes - self.image_size_bytes(img))
                        s.image_catalog.pop(i)
                        return {"success": True, "freed_mb": freed}
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
                        s.product_bytes = max(
                            0, s.product_bytes - self.image_size_bytes(img))
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

        elif command == "release_image":
            # S13.5 completion path (2026-08b PL-c).
            return self.release_image(int(cmd.get("scene_id", 0)))

        elif command == "get_image_catalog":
            return {
                "success": True,
                "catalog": list(s.image_catalog),
                "count": len(s.image_catalog),
                "product_mb": s.product_bytes / 1e6,
                "free_mb": self._available_storage_mb(),
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
            target = snap_to_envelope(target, env.setpoint_min_c,
                                      env.setpoint_max_c)
            if env.setpoint_min_c <= target <= env.setpoint_max_c:
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
            cal_mode = int(cmd.get("mode", 2))
            if cal_mode not in (0, 1, 2):
                return {"success": False, "error_code": 0x0003,
                        "message": f"Unknown calibration mode {cal_mode} "
                                   "(0=DARK, 1=FLAT, 2=BOTH)"}
            # ── 2026-08b PL-d: gate on the physics, not on the mode word ──
            # The old gate was `s.mode != 2` — calibration REQUIRED IMAGING —
            # and it checked none of the four things that actually decide
            # whether a calibration frame is worth anything: the shutter, the
            # lamp, the lamp's device power and the detector temperature. So
            # the shipped COM-011 card had to command PAYLOAD_SET_MODE(2)
            # immediately before START_CALIBRATION(DARK), i.e. open the shutter
            # onto the scene to take a dark frame.
            err = self._calibration_precondition_error(cal_mode)
            if err is not None:
                return err
            s.calibration_active = True
            s.calibration_mode = cal_mode
            s.calibration_state = 2 if cal_mode == 1 else 1
            s.calibration_progress = 0.0
            # Half the duration per PHASE; a single-phase run (DARK-only /
            # FLAT-only) therefore takes half as long as BOTH, which is what
            # "half duration per phase" has always meant.
            s.calibration_timer = s.calibration_duration_s / 2
            s.dark_frame_count = 0  # Initialize frame counters
            s.flat_frame_count = 0
            s.cal_abort_reason = 0
            # A calibration is its own instrument mode. Promoting STANDBY ->
            # CALIBRATION makes 0x0600 tell the truth about what the payload is
            # doing, and keeps `mode == 2` meaning IMAGING for the AOCS desat
            # inhibit (DISPLAY_REQUESTS_AOCS.md section 3).
            if s.mode == 1:
                s.mode = 3
            return {"success": True,
                    "deferred": self.COMPLETION_CALIBRATION,
                    "deferred_timeout_s": 4.0 * s.calibration_duration_s + 60.0,
                    "deferred_label": "PAYLOAD_START_CALIBRATION",
                    "message": "Calibration started"}

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
        elif command == "set_shutter":
            # 2026-08b PL-d: ABSOLUTE shutter commanding. There was none —
            # `cycle_shutter` was the only shutter command in the whole
            # catalogue, and a relative toggle cannot be used to reach a known
            # state, which is exactly what a dark frame needs. Idempotent by
            # construction: commanding CLOSED twice leaves it CLOSED.
            position = int(cmd.get("position", 1))
            if position not in (0, 1):
                return {"success": False, "error_code": 0x0003,
                        "message": f"Shutter position {position} invalid "
                                   "(0=CLOSED, 1=OPEN)"}
            if not s.device_states.get(0x0603, True):
                return {"success": False, "error_code": 0x0007,
                        "message": "Shutter mechanism device (0x0603) is "
                                   "powered off"}
            if s.shutter_test_active:
                return {"success": False, "error_code": 0x0005,
                        "message": "Shutter cycle test in progress — "
                                   "position is not commandable"}
            s.shutter_position = position
            return {"success": True, "position": position,
                    "message": "Shutter CLOSED" if position == 0
                               else "Shutter OPEN"}

        elif command == "cycle_shutter":
            cycles = int(cmd.get("cycles", 1))
            if s.shutter_test_active:
                return {"success": False, "message": "Shutter test already in progress"}
            if not s.device_states.get(0x0603, True):
                return {"success": False, "error_code": 0x0007,
                        "message": "Shutter mechanism device (0x0603) is "
                                   "powered off"}
            s.shutter_test_active = True
            s.shutter_test_cycles_remaining = cycles
            # 2026-08b PL-d: the toggle that used to be HERE is gone. It made
            # every cycle count off by one — `handle_command` toggled once and
            # then `tick` toggled once per remaining count, so cycles=1 moved
            # the blade TWICE and the shutter always ended where it started.
            # The shutter could therefore never be commanded closed, and
            # COM-011 step 2 ("cycle to CLOSED, verify 0x063C == 0 within
            # 10 s") could never pass. `tick` owns the mechanism; one
            # transition per tick, `cycles` transitions in total.
            return {"success": True, "message": f"Starting shutter cycle {cycles}"}

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
            img = self.find_image(scene_id)
            if img is None:
                return {"success": False, "error_code": 0x0003,
                        "message": f"Image {scene_id} not found in the "
                                   "catalogue"}
            if s.transfer_active:
                return {"success": False, "error_code": 0x0005,
                        "message": (f"Transfer of scene {s.transfer_scene_id} "
                                    f"already in progress "
                                    f"({s.transfer_progress:.0f} %)")}
            s.transfer_scene_id = scene_id
            s.transfer_bytes_total = self.image_size_bytes(img)
            s.transfer_bytes_sent = 0
            s.transfer_active = True
            s.transfer_progress = 0.0
            # 0x0610 TRANSFER_START. The old comment promised "will be emitted
            # in next tick" and nothing anywhere sent it; the event is raised
            # HERE, on the command that starts the transfer.
            self._queue_event(0x0610)
            return {"success": True, "message": f"Transfer {scene_id} initiated",
                    "bytes_total": s.transfer_bytes_total,
                    "blocks": self.image_block_count(img),
                    "checksum": self.image_checksum(scene_id)}

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

    # ─── Failure injection ───────────────────────────────────────────
    # Declarative parameter specs — single source for the instructor form,
    # FailureManager validation and the scenario content gate.
    FAILURE_SPECS = {
        "cooler_failure": FailureSpec(
            name="cooler_failure", label="FPA cooler failure",
            effect="The miniature Stirling-class FPA cooler stops: the focal "
                   "plane warms off its centi-kelvin setpoint, dark current "
                   "rises and per-band SNR collapses.",
            magnitude=ParamSpec(name="magnitude", type=T_BOOL,
                                label="Failed", default=True),
        ),
        "fpa_degraded": FailureSpec(
            name="fpa_degraded", label="Focal plane degraded",
            effect="Detector responsivity degraded — image quality metric and "
                   "per-band SNR fall even at the correct FPA temperature.",
            magnitude=ParamSpec(name="magnitude", type=T_BOOL,
                                label="Degraded", default=True),
        ),
        "image_corrupt": FailureSpec(
            name="image_corrupt", label="Corrupted image products",
            effect="The next N captured images are marked corrupt (bad CRC on "
                   "download), then capture returns to normal.",
            magnitude=None,
            params=(number("count", "Images to corrupt", unit="images", lo=1,
                           hi=1000, default=3, integer=True),),
        ),
        "memory_segment_fail": FailureSpec(
            name="memory_segment_fail", label="Payload memory segment failure",
            effect="Fails one of the 8 payload mass-memory segments; usable "
                   "storage shrinks by one segment.",
            magnitude=None,
            params=(enum("segment", "Memory segment", list(range(8)),
                         labels=[f"Segment {i}" for i in range(8)], default=0),),
        ),
        "ccd_line_dropout": FailureSpec(
            name="ccd_line_dropout", label="CCD line dropout",
            effect="Dropped detector lines: a striped product, visible in the "
                   "image quality metric and the downloaded thumbnail.",
            magnitude=ParamSpec(name="magnitude", type=T_BOOL,
                                label="Dropping lines", default=True),
        ),
    }

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

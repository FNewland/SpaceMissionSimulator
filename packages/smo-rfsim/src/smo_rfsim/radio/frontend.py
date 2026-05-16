"""Radio front-end status engine.

Purely observational — tracks RF link indicators as reported by the
spacecraft downlink and ground station receiver chain.
"""

import math
import random
import time
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class LockState(str, Enum):
    LOCKED = "LOCKED"
    ACQUIRING = "ACQUIRING"
    UNLOCKED = "UNLOCKED"


class LEDColor(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


@dataclass
class RadioStatus:
    """Current front-end status snapshot."""
    # Lock indicators
    carrier_lock: LockState = LockState.UNLOCKED
    bit_sync: LockState = LockState.UNLOCKED
    frame_sync: LockState = LockState.UNLOCKED
    # RF measurements
    rssi_dbm: float = -120.0
    eb_n0_db: float = 0.0
    ber_log10: float = -1.0
    link_margin_db: float = 0.0
    # Dynamics
    doppler_hz: float = 0.0
    range_km: float = 0.0
    data_rate_kbps: float = 32.0
    # Frame counters
    good_frames: int = 0
    bad_frames: int = 0
    # VC activity (last frame time per VCID)
    vc_active: dict[int, float] = field(default_factory=dict)
    # TC/CLTU status
    cltu_sent: int = 0
    cltu_acked: int = 0
    # Operating mode
    mode: str = "PACKET"
    # Modulation scheme (0=BPSK, 1=QPSK, 2=8PSK, 3=OQPSK)
    modulation: int = 0
    modulation_name: str = "BPSK"
    # Constellation I/Q samples (baseband observation)
    iq_samples: list[list[float]] = field(default_factory=list)
    # Ground segment Eb/N0 penalty from RF chain degradation (dB)
    gs_penalty_db: float = 0.0
    # Channel model status
    frequency_hz: float = 437.0e6
    bandwidth_hz: float = 0.0
    phase_noise_enabled: bool = False
    fading_enabled: bool = False
    fading_k_db: float = 10.0
    n_interferers: int = 0
    # Link budget breakdown
    eirp_dbw: float = 0.0
    fspl_db: float = 0.0
    cn0_dbhz: float = 0.0
    coding_gain_db: float = 6.0
    # Spectrum data (FFT magnitudes for display)
    spectrum_db: list[float] = field(default_factory=list)
    spectrum_freq_range_hz: float = 0.0
    # Eye diagram samples (symbol-aligned for display)
    eye_i: list[float] = field(default_factory=list)
    eye_q: list[float] = field(default_factory=list)
    # Timestamp
    timestamp: float = 0.0

    def carrier_led(self) -> LEDColor:
        if self.carrier_lock == LockState.LOCKED:
            return LEDColor.GREEN
        elif self.carrier_lock == LockState.ACQUIRING:
            return LEDColor.YELLOW
        return LEDColor.RED

    def bit_sync_led(self) -> LEDColor:
        if self.bit_sync == LockState.LOCKED:
            return LEDColor.GREEN
        elif self.bit_sync == LockState.ACQUIRING:
            return LEDColor.YELLOW
        return LEDColor.RED

    def frame_sync_led(self) -> LEDColor:
        if self.frame_sync == LockState.LOCKED:
            return LEDColor.GREEN
        elif self.frame_sync == LockState.ACQUIRING:
            return LEDColor.YELLOW
        return LEDColor.RED

    def vc_led(self, vcid: int, stale_seconds: float = 5.0) -> LEDColor:
        last = self.vc_active.get(vcid, 0)
        age = self.timestamp - last
        if age < stale_seconds:
            return LEDColor.GREEN
        elif age < stale_seconds * 3:
            return LEDColor.YELLOW
        return LEDColor.RED


class RadioFrontend:
    """Aggregates status from the bridge processing chain.

    This is a read-only observation point. It does not inject failures
    or modify the signal chain. All it does is display what the ground
    station receiver sees.
    """

    IQ_BUFFER_SIZE = 128

    # Ideal constellation reference points per modulation
    _CONSTELLATIONS = {
        0: [(-1.0, 0.0), (1.0, 0.0)],  # BPSK
        1: [(0.707, 0.707), (-0.707, 0.707),
            (-0.707, -0.707), (0.707, -0.707)],  # QPSK
        2: [(1.0, 0.0), (0.707, 0.707), (0.0, 1.0), (-0.707, 0.707),
            (-1.0, 0.0), (-0.707, -0.707), (0.0, -1.0), (0.707, -0.707)],  # 8PSK
        3: [(0.707, 0.707), (-0.707, 0.707),
            (-0.707, -0.707), (0.707, -0.707)],  # OQPSK
    }
    _MOD_NAMES = {
        0: "BPSK", 1: "QPSK", 2: "8PSK", 3: "OQPSK",
        4: "16-APSK", 5: "π/4-DQPSK", 6: "GMSK", 7: "GFSK",
    }

    def __init__(self):
        self.status = RadioStatus()
        self._rng = random.Random(42)

    def update_frame_counts(self, good: int, bad: int):
        self.status.good_frames = good
        self.status.bad_frames = bad

    def update_rf(self, eb_n0_db: float, doppler_hz: float = 0.0,
                  range_km: float = 0.0, rssi_dbm: float = -80.0):
        self.status.eb_n0_db = eb_n0_db
        self.status.doppler_hz = doppler_hz
        self.status.range_km = range_km
        self.status.rssi_dbm = rssi_dbm
        if eb_n0_db > 0:
            ber = max(1e-12, 0.5 * math.erfc(math.sqrt(10 ** (eb_n0_db / 10.0))))
            self.status.ber_log10 = math.log10(ber) if ber > 0 else -12.0
        else:
            self.status.ber_log10 = -1.0  # no meaningful BER
        self.status.link_margin_db = eb_n0_db - 9.6

    def update_lock(self, carrier: LockState, bit_sync: LockState,
                    frame_sync: LockState):
        self.status.carrier_lock = carrier
        self.status.bit_sync = bit_sync
        self.status.frame_sync = frame_sync

    def update_vc_activity(self, vcid: int):
        self.status.vc_active[vcid] = time.time()

    def update_cltu(self, sent: int, acked: int):
        self.status.cltu_sent = sent
        self.status.cltu_acked = acked

    def update_modulation(self, mod_mode: int):
        self.status.modulation = mod_mode
        self.status.modulation_name = self._MOD_NAMES.get(mod_mode, "BPSK")

    def update_gs_penalty(self, penalty_db: float):
        self.status.gs_penalty_db = penalty_db

    def update_channel_status(self, channel_status: dict):
        """Update channel model fields from SpaceLinkChannel.get_status()."""
        self.status.frequency_hz = channel_status.get("frequency_hz", 437e6)
        self.status.phase_noise_enabled = channel_status.get("phase_noise_enabled", False)
        self.status.fading_enabled = channel_status.get("fading_enabled", False)
        self.status.fading_k_db = channel_status.get("fading_k_factor_db", 10.0)
        self.status.n_interferers = channel_status.get("n_interferers", 0)
        self.status.bandwidth_hz = channel_status.get("sample_rate", 0) / 2.0

    def update_link_budget(self, budget: dict):
        """Update link budget breakdown from LinkBudget.compute()."""
        self.status.eirp_dbw = budget.get("eirp_dbw", 0.0)
        self.status.fspl_db = budget.get("fspl_db", 0.0)
        self.status.cn0_dbhz = budget.get("cn0_dbhz", 0.0)
        self.status.coding_gain_db = budget.get("coding_gain_db", 6.0)

    def update_spectrum(self, samples, max_points: int = 128):
        """Compute and store spectrum (FFT magnitude) from baseband samples."""
        import numpy as np
        if samples is None or len(samples) < 64:
            return
        # Use last N samples for FFT
        n = min(len(samples), 512)
        s = np.array(samples[-n:])
        fft = np.fft.fftshift(np.fft.fft(s * np.hanning(n)))
        mag_db = 20.0 * np.log10(np.abs(fft) + 1e-10)
        # Downsample to max_points
        step = max(1, len(mag_db) // max_points)
        self.status.spectrum_db = [round(float(x), 1)
                                    for x in mag_db[::step][:max_points]]
        self.status.spectrum_freq_range_hz = self.status.bandwidth_hz * 2

    def update_eye_diagram(self, symbols, sps: int = 8, traces: int = 20):
        """Store symbol-aligned samples for eye diagram display."""
        import numpy as np
        if symbols is None or len(symbols) < sps * 3:
            return
        # Take last N symbol periods, fold at 2× symbol period
        period = sps * 2
        n_periods = min(traces, len(symbols) // period)
        if n_periods < 2:
            return
        start = len(symbols) - n_periods * period
        eye_data = np.array(symbols[start:start + n_periods * period])
        self.status.eye_i = [round(float(x), 3) for x in eye_data.real[:period * traces]]
        self.status.eye_q = [round(float(x), 3) for x in eye_data.imag[:period * traces]]

    def snapshot(self) -> RadioStatus:
        """Return current status. I/Q samples are set by the bridge from
        the DSP processor — they are real demodulated samples, not synthetic."""
        self.status.timestamp = time.time()
        return self.status

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
from typing import Optional

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
    # TM data-rate ladder (defect #85). The tier drives the bit rate, the
    # modulation and the FEC, so the constellation, the occupied bandwidth and
    # the eye diagram all move when the tier moves.
    tm_tier: int = 0
    tm_tier_name: str = ""
    tm_coding: str = "RS"
    occupied_bw_hz: float = 0.0
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
    # Eye diagram samples (oversampled, symbol-aligned for display)
    eye_i: list[float] = field(default_factory=list)
    eye_q: list[float] = field(default_factory=list)
    eye_sps: int = 8
    # Measured receiver quality (decision-directed SNR estimate)
    evm_snr_db: float = 0.0
    # Pipeline
    symbol_rate_bd: float = 32000.0
    # Effective configuration + pipeline diagnostics (control plane)
    config: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    control_enabled: bool = False
    # Achieved downlink throughput measured at the receiver
    throughput_bps: float = 0.0
    # ── Spacecraft transmitter state, from the sim's tx_active gate
    # (2026-08c item 6). None = no sim feedback yet. The radio page shows
    # this so "PA off" and "out of view" are distinguishable at a glance,
    # and so the instructor pass override (which forces GEOMETRIC
    # visibility only) is visibly unable to resurrect a dead transmitter.
    sc_tx_on: Optional[bool] = None
    sc_pa_on: Optional[bool] = None
    pass_override: bool = False
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

    def update_sc_tx(self, tx_on: Optional[bool] = None,
                     pa_on: Optional[bool] = None,
                     pass_override: bool = False):
        """Spacecraft transmitter state from the sim (2026-08c item 6)."""
        if tx_on is not None:
            self.status.sc_tx_on = bool(tx_on)
        if pa_on is not None:
            self.status.sc_pa_on = bool(pa_on)
        self.status.pass_override = bool(pass_override)

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

    def update_tm_tier(self, tier: int = None, tier_name: str = None,
                       coding: str = None, occupied_bw_hz: float = None):
        """Publish the spacecraft's active TM rate tier (defect #85).

        Lets the radio UI label the rate `HIGH (128.0 kbps, QPSK, RS)`
        rather than showing a bare number whose provenance is invisible.
        """
        if tier is not None:
            self.status.tm_tier = int(tier)
        if tier_name is not None:
            self.status.tm_tier_name = str(tier_name)
        if coding is not None:
            self.status.tm_coding = str(coding)
        if occupied_bw_hz is not None:
            self.status.occupied_bw_hz = float(occupied_bw_hz)

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

    def update_pipeline_status(self, symbol_rate_bd: float = None,
                               evm_snr_db: float = None,
                               throughput_bps: float = None):
        """Live pipeline figures: occupied symbol rate, measured receiver
        SNR, and achieved downlink throughput."""
        if symbol_rate_bd is not None:
            self.status.symbol_rate_bd = float(symbol_rate_bd)
        if evm_snr_db is not None:
            self.status.evm_snr_db = float(evm_snr_db)
        if throughput_bps is not None:
            self.status.throughput_bps = float(throughput_bps)

    def update_config(self, config: dict, control_enabled: bool = False):
        """Publish the effective configuration to the UI control panel."""
        self.status.config = config or {}
        self.status.control_enabled = bool(control_enabled)

    def update_diagnostics(self, diagnostics: dict):
        """Publish pipeline counters (previously log-only)."""
        self.status.diagnostics = diagnostics or {}

    def update_link_budget(self, budget: dict):
        """Update link budget breakdown from LinkBudget.compute()."""
        self.status.eirp_dbw = budget.get("eirp_dbw", 0.0)
        self.status.fspl_db = budget.get("fspl_db", 0.0)
        self.status.cn0_dbhz = budget.get("cn0_dbhz", 0.0)
        self.status.coding_gain_db = budget.get("coding_gain_db", 6.0)

    def update_spectrum(self, samples, max_points: int = 128,
                        nfft: int = 512, segments: int = 8,
                        sample_rate: float = None):
        """Compute and store a Welch-averaged baseband power spectrum.

        Fed from the post-impairment sample stream (``ChannelStage``) — i.e.
        exactly what a ground-station SDR sees: the RRC main lobe, the
        roll-off shoulders, the noise floor rising as Eb/N0 drops, CW
        interferers as spikes, and the whole spectrum sliding under Doppler.

        Averaging ``segments`` periodograms (Welch, Hann window, 50 %
        overlap) instead of a single FFT keeps the trace readable — one
        512-point FFT of a noisy stream is visually very jumpy.

        Returns True if a spectrum was produced.
        """
        import numpy as np
        if samples is None:
            return False
        s = np.asarray(samples)
        if s.size < 64:
            return False
        nfft = min(int(nfft), int(s.size))
        if nfft < 64:
            return False
        window = np.hanning(nfft)
        win_power = float(np.sum(window ** 2)) or 1.0
        hop = max(1, nfft // 2)
        # Take the most recent segments (newest data last)
        max_segs = 1 + (s.size - nfft) // hop
        n_segs = max(1, min(int(segments), int(max_segs)))
        start0 = s.size - nfft - (n_segs - 1) * hop
        psd = np.zeros(nfft)
        for i in range(n_segs):
            start = start0 + i * hop
            seg = s[start:start + nfft] * window
            psd += np.abs(np.fft.fftshift(np.fft.fft(seg))) ** 2
        psd /= (n_segs * win_power)
        mag_db = 10.0 * np.log10(psd + 1e-12)
        # Decimate to max_points, keeping the peak of each bin group so a
        # narrow CW interferer is never averaged away
        step = max(1, len(mag_db) // max_points)
        usable = (len(mag_db) // step) * step
        binned = mag_db[:usable].reshape(-1, step).max(axis=1)[:max_points]
        self.status.spectrum_db = [round(float(x), 1) for x in binned]
        if sample_rate:
            self.status.spectrum_freq_range_hz = float(sample_rate)
            self.status.bandwidth_hz = float(sample_rate) / 2.0
        else:
            self.status.spectrum_freq_range_hz = self.status.bandwidth_hz * 2
        return True

    def update_eye_diagram(self, samples, sps: int = 8, traces: int = 20,
                           timing_offset: int = 0):
        """Store oversampled, symbol-aligned traces for the eye diagram.

        ``samples`` must be the **oversampled** post-matched-filter waveform
        (sps samples per symbol), not the symbol-rate decimated
        constellation tap — folding one-sample-per-symbol data at a
        2·sps period produces noise, not an eye (defect #53).

        ``timing_offset`` is the index of the first decision instant within
        ``samples``; traces are aligned so the decision instant lands at the
        centre of each 2-symbol trace.

        Returns True if an eye was produced.
        """
        import numpy as np
        if samples is None or sps < 2:
            return False
        s = np.asarray(samples)
        period = sps * 2
        if s.size < period * 2:
            return False
        # Align so each trace starts half a symbol before a decision instant
        # → the eye opening sits in the middle of the drawn trace.
        first = (int(timing_offset) - sps // 2) % sps
        avail = (s.size - first) // period
        if avail < 2:
            return False
        n_traces = int(min(traces, avail))
        start = first + (avail - n_traces) * period
        eye = s[start:start + n_traces * period]
        self.status.eye_i = [round(float(x), 3) for x in np.real(eye)]
        self.status.eye_q = [round(float(x), 3) for x in np.imag(eye)]
        self.status.eye_sps = int(sps)
        return True

    def snapshot(self) -> RadioStatus:
        """Return current status. I/Q samples are set by the bridge from
        the DSP processor — they are real demodulated samples, not synthetic."""
        self.status.timestamp = time.time()
        return self.status

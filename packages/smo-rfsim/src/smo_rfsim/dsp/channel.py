"""Baseband channel model — applies AWGN, frequency offset, and timing jitter
to complex sample streams.

This operates on actual complex samples, not on bit-level data.
"""

import math
import numpy as np


class BasebandChannel:
    """Applies realistic channel impairments to complex baseband samples.

    Parameters:
        eb_n0_db: Energy per bit to noise density ratio (dB)
        sps: samples per symbol (must match modulator)
        bits_per_symbol: for Eb/N0 → Es/N0 conversion
        freq_offset_hz: carrier frequency offset (Doppler)
        sample_rate: sample rate in Hz (= symbol_rate × sps)
        timing_offset: fractional sample timing error (0.0 = none)
    """

    def __init__(self, eb_n0_db: float = 10.0, sps: int = 8,
                 bits_per_symbol: int = 1, freq_offset_hz: float = 0.0,
                 sample_rate: float = 256000.0, seed: int = 42):
        self.eb_n0_db = eb_n0_db
        self.sps = sps
        self.bits_per_symbol = bits_per_symbol
        self.freq_offset_hz = freq_offset_hz
        self.sample_rate = sample_rate
        self._rng = np.random.default_rng(seed)
        self._phase_acc = 0.0  # accumulated phase for freq offset

    def set_eb_n0(self, eb_n0_db: float):
        self.eb_n0_db = eb_n0_db

    def set_freq_offset(self, freq_offset_hz: float):
        self.freq_offset_hz = freq_offset_hz

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Apply channel impairments to complex samples.

        The signal is assumed to have unit average symbol energy.
        """
        out = samples.astype(np.complex64).copy()
        n = len(out)

        # 1. Frequency offset (Doppler)
        if self.freq_offset_hz != 0:
            t = np.arange(n) / self.sample_rate
            phase = 2 * math.pi * self.freq_offset_hz * t + self._phase_acc
            out *= np.exp(1j * phase).astype(np.complex64)
            self._phase_acc = phase[-1] if n > 0 else self._phase_acc

        # 2. AWGN
        # Es/N0 = Eb/N0 + 10*log10(bits_per_symbol)
        # Noise variance per dimension = 1/(2 * Es/N0_linear * sps)
        if self.eb_n0_db < 50:  # skip noise at very high SNR
            es_n0_db = self.eb_n0_db + 10 * math.log10(max(1, self.bits_per_symbol))
            es_n0_lin = 10 ** (es_n0_db / 10.0)
            noise_var = 1.0 / (2.0 * es_n0_lin * self.sps)
            noise_std = math.sqrt(max(0, noise_var))
            noise = (self._rng.normal(0, noise_std, n) +
                     1j * self._rng.normal(0, noise_std, n))
            out += noise.astype(np.complex64)

        return out

    @property
    def noise_power_per_sample(self) -> float:
        """Current noise variance per complex dimension."""
        es_n0_db = self.eb_n0_db + 10 * math.log10(max(1, self.bits_per_symbol))
        es_n0_lin = 10 ** (es_n0_db / 10.0)
        return 1.0 / (2.0 * es_n0_lin * self.sps)

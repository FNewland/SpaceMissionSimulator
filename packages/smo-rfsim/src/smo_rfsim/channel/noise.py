"""Configurable noise source models for space link simulation.

Provides independent, composable noise generators:
- AWGN (thermal noise floor)
- Phase noise (oscillator jitter — Wiener process)
- CW interferer (narrow-band tone)
- Wideband interferer (band-limited noise)
- Swept interferer (chirp/frequency-hopping)

All operate on complex baseband samples (complex64/complex128).
"""

import math
import numpy as np
import logging

logger = logging.getLogger(__name__)


class AWGNSource:
    """Additive white Gaussian noise at specified Eb/N0.

    Computes noise variance from Eb/N0, bits-per-symbol, and
    samples-per-symbol so the noise power is correctly scaled
    to the signal level (assumed unit energy per symbol).
    """

    def __init__(self, eb_n0_db: float = 10.0, sps: int = 8,
                 bits_per_symbol: int = 1):
        self._sps = sps
        self._bps = bits_per_symbol
        self.set_eb_n0(eb_n0_db)

    def set_eb_n0(self, db: float):
        self.eb_n0_db = db
        es_n0_db = db + 10.0 * math.log10(max(self._bps, 1))
        es_n0_lin = 10.0 ** (es_n0_db / 10.0)
        self._noise_var = 1.0 / (2.0 * max(es_n0_lin, 1e-10) * self._sps)

    def set_bits_per_symbol(self, bps: int):
        self._bps = bps
        self.set_eb_n0(self.eb_n0_db)

    def apply(self, samples: np.ndarray) -> np.ndarray:
        """Add AWGN to complex samples."""
        sigma = math.sqrt(self._noise_var)
        noise = (np.random.normal(0, sigma, len(samples))
                 + 1j * np.random.normal(0, sigma, len(samples)))
        return samples + noise.astype(samples.dtype)


class PhaseNoiseSource:
    """Oscillator phase noise modeled as a Wiener process.

    The 3 dB linewidth determines the phase variance per sample:
    Δφ² = 2π · Δf_3dB · T_sample

    At typical cubesat oscillator quality (Δf = 10-100 Hz),
    this produces realistic carrier phase wander.
    """

    def __init__(self, linewidth_hz: float = 10.0,
                 sample_rate: float = 256000.0):
        self._phase = 0.0
        self.set_params(linewidth_hz, sample_rate)

    def set_params(self, linewidth_hz: float, sample_rate: float):
        self._linewidth = linewidth_hz
        self._sample_rate = sample_rate
        # Phase variance per sample (Wiener process increment)
        self._phase_var = 2.0 * math.pi * linewidth_hz / sample_rate

    def apply(self, samples: np.ndarray) -> np.ndarray:
        """Apply phase noise to complex samples."""
        if self._phase_var <= 0 or self._linewidth <= 0:
            return samples
        n = len(samples)
        # Cumulative random walk
        phase_increments = np.random.normal(
            0, math.sqrt(self._phase_var), n)
        phase_walk = np.cumsum(phase_increments) + self._phase
        self._phase = phase_walk[-1] if n > 0 else self._phase
        # Wrap to prevent float overflow
        self._phase %= (2.0 * math.pi)
        return samples * np.exp(1j * phase_walk).astype(samples.dtype)


class CWInterferer:
    """Continuous-wave (narrow-band) interferer at a fixed frequency offset.

    Models a co-channel or adjacent-channel transmitter.
    """

    def __init__(self, freq_offset_hz: float = 5000.0,
                 power_dbm: float = -90.0,
                 sample_rate: float = 256000.0):
        self._phase = 0.0
        self.freq_offset_hz = freq_offset_hz
        self.power_dbm = power_dbm
        self._sample_rate = sample_rate

    def apply(self, samples: np.ndarray) -> np.ndarray:
        """Add CW tone to complex samples."""
        n = len(samples)
        # Convert power from dBm to linear amplitude (relative to unit signal)
        amplitude = 10.0 ** ((self.power_dbm + 30.0) / 20.0)
        t = np.arange(n) / self._sample_rate
        phase = 2.0 * math.pi * self.freq_offset_hz * t + self._phase
        self._phase = phase[-1] if n > 0 else self._phase
        self._phase %= (2.0 * math.pi)
        tone = amplitude * np.exp(1j * phase)
        return samples + tone.astype(samples.dtype)


class WidebandInterferer:
    """Band-limited noise interferer (e.g., another spread-spectrum signal).

    Models interference from nearby transmitters or self-interference.
    """

    def __init__(self, bandwidth_hz: float = 50000.0,
                 power_dbm: float = -80.0,
                 center_offset_hz: float = 0.0,
                 sample_rate: float = 256000.0):
        self.bandwidth_hz = bandwidth_hz
        self.power_dbm = power_dbm
        self.center_offset_hz = center_offset_hz
        self._sample_rate = sample_rate
        self._phase = 0.0

    def apply(self, samples: np.ndarray) -> np.ndarray:
        """Add band-limited noise interference."""
        n = len(samples)
        amplitude = 10.0 ** ((self.power_dbm + 30.0) / 20.0)
        # Generate white noise, then bandpass filter
        noise = amplitude * (np.random.normal(0, 1, n)
                             + 1j * np.random.normal(0, 1, n))
        # Apply frequency shift to center
        if self.center_offset_hz != 0:
            t = np.arange(n) / self._sample_rate
            phase = 2.0 * math.pi * self.center_offset_hz * t + self._phase
            self._phase = phase[-1] if n > 0 else self._phase
            noise = noise * np.exp(1j * phase)
        # Simple bandwidth limiting via moving average
        bw_samples = max(1, int(self._sample_rate / max(self.bandwidth_hz, 1)))
        if bw_samples > 1 and bw_samples < n:
            kernel = np.ones(bw_samples) / bw_samples
            noise = np.convolve(noise, kernel, mode='same')
        return samples + noise.astype(samples.dtype)

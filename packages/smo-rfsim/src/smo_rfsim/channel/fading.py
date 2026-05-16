"""Multipath fading channel models for space link simulation.

Implements Rician and Rayleigh fading using the Jakes model
(sum-of-sinusoids) for realistic time-varying channel response.

For LEO cubesat links:
- Direct path (line-of-sight) typically dominant → Rician with high K
- Atmospheric scintillation at low elevation → Rician with lower K
- Urban/ground-bounce multipath (rare for space) → Rayleigh

Reference: CCSDS 401.0-B, 3GPP TR 38.811 (NTN channel models)
"""

import math
import numpy as np
import logging

logger = logging.getLogger(__name__)


class RicianFading:
    """Rician fading channel with configurable K-factor.

    K = ratio of LOS power to scattered power (dB).
    K = inf → no fading (pure LOS)
    K = 0 → Rayleigh fading (no LOS)
    K = 10 dB → typical LEO space link at high elevation

    Uses Jakes model with N_paths scattered components.
    """

    def __init__(self, k_factor_db: float = 10.0,
                 max_doppler_hz: float = 50.0,
                 sample_rate: float = 256000.0,
                 n_paths: int = 16,
                 seed: int = 42):
        self._sample_rate = sample_rate
        self._n_paths = n_paths
        self._rng = np.random.RandomState(seed)
        self._sample_counter = 0
        self.set_params(k_factor_db, max_doppler_hz)

    def set_params(self, k_factor_db: float, max_doppler_hz: float):
        self.k_factor_db = k_factor_db
        self.max_doppler_hz = max_doppler_hz
        # K-factor: linear ratio of LOS to scattered power
        self._k_lin = 10.0 ** (k_factor_db / 10.0)
        # LOS and scattered amplitudes (total power = 1)
        self._los_amp = math.sqrt(self._k_lin / (1.0 + self._k_lin))
        self._scatter_amp = math.sqrt(1.0 / (1.0 + self._k_lin))
        # Jakes model: N sinusoids with uniformly spaced arrival angles
        self._angles = self._rng.uniform(0, 2 * math.pi, self._n_paths)
        self._phases = self._rng.uniform(0, 2 * math.pi, self._n_paths)

    def apply(self, samples: np.ndarray) -> np.ndarray:
        """Apply Rician fading to complex samples."""
        if self.max_doppler_hz <= 0 or self._k_lin > 1e6:
            return samples  # no fading if K very high or no Doppler

        n = len(samples)
        t = (np.arange(n) + self._sample_counter) / self._sample_rate
        self._sample_counter += n

        # Scattered component (Jakes model: sum of sinusoids)
        scatter_i = np.zeros(n)
        scatter_q = np.zeros(n)
        for k in range(self._n_paths):
            fd = self.max_doppler_hz * math.cos(self._angles[k])
            scatter_i += np.cos(2 * math.pi * fd * t + self._phases[k])
            scatter_q += np.sin(2 * math.pi * fd * t + self._phases[k])
        scatter_i *= self._scatter_amp / math.sqrt(self._n_paths)
        scatter_q *= self._scatter_amp / math.sqrt(self._n_paths)

        # LOS component (constant amplitude, no Doppler)
        h = (self._los_amp + scatter_i + 1j * scatter_q).astype(samples.dtype)

        return samples * h


class RayleighFading(RicianFading):
    """Rayleigh fading (Rician with K=0, no line-of-sight).

    Rarely applicable for space links but useful for ground-bounce
    multipath testing and urban scenarios.
    """

    def __init__(self, max_doppler_hz: float = 50.0,
                 sample_rate: float = 256000.0,
                 n_paths: int = 16, seed: int = 42):
        super().__init__(k_factor_db=-30.0,  # effectively K≈0
                         max_doppler_hz=max_doppler_hz,
                         sample_rate=sample_rate,
                         n_paths=n_paths, seed=seed)


class MultipathChannel:
    """Multi-tap delay-line channel model.

    Models multiple propagation paths with independent delays,
    amplitudes, and Doppler spreads. Each tap can have its own
    fading characteristics.
    """

    def __init__(self, taps: list = None,
                 sample_rate: float = 256000.0):
        self._sample_rate = sample_rate
        if taps is None:
            # Default: single LOS tap (no multipath)
            taps = [{"delay_us": 0.0, "power_db": 0.0, "doppler_hz": 0.0}]
        self._taps = taps
        # Convert delays to sample offsets
        self._delays_samples = [
            int(t["delay_us"] * 1e-6 * sample_rate) for t in taps
        ]
        self._powers = [10.0 ** (t["power_db"] / 10.0) for t in taps]
        # Normalize total power
        total = sum(self._powers)
        self._amplitudes = [math.sqrt(p / total) for p in self._powers]

    def apply(self, samples: np.ndarray) -> np.ndarray:
        """Apply multipath channel (delay-line FIR model)."""
        if len(self._taps) <= 1:
            return samples * self._amplitudes[0] if self._amplitudes else samples

        max_delay = max(self._delays_samples)
        out = np.zeros(len(samples) + max_delay, dtype=samples.dtype)
        for i, (delay, amp) in enumerate(
                zip(self._delays_samples, self._amplitudes)):
            out[delay:delay + len(samples)] += amp * samples
        return out[:len(samples)]

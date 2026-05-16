"""Baseband demodulator — carrier recovery, clock recovery, and symbol decision.

Implements:
  - AGC (automatic gain control)
  - Costas loop for carrier/phase recovery (BPSK/QPSK)
  - Mueller & Muller clock recovery
  - Hard decision symbol slicer
  - Constellation tap for I/Q display

All numpy-based. When GNU Radio is available, its blocks provide
higher-performance equivalents with identical behaviour.
"""

import math
import numpy as np
from typing import Optional

from .modulator import CONSTELLATION_MAPS, BITS_PER_SYMBOL


class Demodulator:
    """Recovers symbols from noisy, frequency-offset baseband samples.

    Parameters:
        modulation: 0=BPSK, 1=QPSK, 2=8PSK, 3=OQPSK
        sps: samples per symbol
        loop_bw: PLL loop bandwidth (normalized, 0.001–0.1)
        rolloff: RRC rolloff (must match modulator)
    """

    def __init__(self, modulation: int = 0, sps: int = 8,
                 loop_bw: float = 0.01, rolloff: float = 0.35):
        self.modulation = modulation
        self.sps = sps
        self._bps = BITS_PER_SYMBOL.get(modulation, 1)
        self._map = CONSTELLATION_MAPS.get(modulation, CONSTELLATION_MAPS[0])
        self._order = len(self._map)

        # Costas loop state
        self._phase = 0.0
        self._freq = 0.0
        self._loop_bw = loop_bw
        # Loop filter coefficients (2nd order)
        denom = 1.0 + 2.0 * 0.707 * loop_bw + loop_bw ** 2
        self._alpha = 4 * 0.707 * loop_bw / denom  # proportional
        self._beta = 4 * loop_bw ** 2 / denom       # integral

        # M&M clock recovery state
        self._mu = 0.0           # fractional sample offset
        self._omega = float(sps) # samples per symbol estimate
        self._gain_mu = 0.01     # timing gain
        self._gain_omega = 0.25 * self._gain_mu ** 2
        self._last_sample = 0 + 0j

        # AGC state
        self._agc_gain = 1.0
        self._agc_rate = 0.01
        self._agc_ref = 1.0

        # Matched filter (RRC)
        self._rrc = self._make_rrc(sps, rolloff)

        # Output buffers
        self.constellation_points: list[complex] = []
        self.carrier_locked = False
        self.clock_locked = False
        self._lock_detector = 0.0

    def set_modulation(self, modulation: int):
        self.modulation = modulation
        self._bps = BITS_PER_SYMBOL.get(modulation, 1)
        self._map = CONSTELLATION_MAPS.get(modulation, CONSTELLATION_MAPS[0])
        self._order = len(self._map)

    @staticmethod
    def _make_rrc(sps: int, rolloff: float, ntaps: int = 101) -> np.ndarray:
        n = np.arange(ntaps) - (ntaps - 1) / 2
        t = n / sps
        taps = np.zeros(ntaps)
        for i, ti in enumerate(t):
            if ti == 0:
                taps[i] = 1.0 - rolloff + 4 * rolloff / math.pi
            elif rolloff > 0 and abs(abs(ti) - 1 / (4 * rolloff)) < 1e-8:
                taps[i] = (rolloff / math.sqrt(2)) * (
                    (1 + 2 / math.pi) * math.sin(math.pi / (4 * rolloff)) +
                    (1 - 2 / math.pi) * math.cos(math.pi / (4 * rolloff)))
            else:
                num = (math.sin(math.pi * ti * (1 - rolloff)) +
                       4 * rolloff * ti * math.cos(math.pi * ti * (1 + rolloff)))
                den = math.pi * ti * (1 - (4 * rolloff * ti) ** 2)
                taps[i] = num / den if den != 0 else 0
        return taps / np.sqrt(np.sum(taps ** 2))

    def demodulate(self, samples: np.ndarray) -> bytes:
        """Demodulate complex baseband samples back to data bytes.

        Also populates self.constellation_points with the recovered
        symbol-rate I/Q samples (the constellation tap).
        """
        # Matched filter
        filtered = np.convolve(samples.astype(np.complex64),
                               self._rrc.astype(np.complex64), mode='same')

        # Process sample-by-sample through AGC + Costas + M&M
        recovered_symbols = []
        self.constellation_points = []
        idx = 0
        n = len(filtered)

        while idx < n - 1:
            # AGC
            sample = filtered[idx] * self._agc_gain
            mag = abs(sample)
            self._agc_gain += self._agc_rate * (self._agc_ref - mag)
            self._agc_gain = max(0.01, min(100.0, self._agc_gain))

            # Costas loop: phase correction
            corrected = sample * np.exp(-1j * self._phase)

            # M&M clock recovery: decide if this is a symbol sample
            self._mu -= 1.0
            if self._mu <= 0:
                # This is a symbol-rate sample
                self.constellation_points.append(corrected)
                recovered_symbols.append(corrected)

                # M&M timing error detector
                err_t = (corrected.real * self._last_sample.real -
                         self._last_sample.real * corrected.real)
                # Simplified: use sign of derivative
                err_t = corrected.real * (self._last_sample.real - corrected.real)
                self._omega += self._gain_omega * err_t
                self._omega = max(self.sps * 0.8, min(self.sps * 1.2, self._omega))
                self._mu += self._omega
                self._last_sample = corrected

                # Costas loop phase error
                if self.modulation == 0:  # BPSK
                    phase_err = corrected.real * corrected.imag
                elif self.modulation in (1, 3):  # QPSK/OQPSK
                    phase_err = (np.sign(corrected.real) * corrected.imag -
                                 np.sign(corrected.imag) * corrected.real)
                else:  # 8PSK — decision-directed
                    decided = self._hard_decide(corrected)
                    phase_err = (corrected * np.conj(decided)).imag
                self._freq += self._beta * phase_err
                self._phase += self._alpha * phase_err + self._freq
                # Wrap phase
                self._phase = self._phase % (2 * math.pi)

                # Lock detector (exponential average of |phase_err|)
                self._lock_detector = 0.99 * self._lock_detector + 0.01 * abs(phase_err)
                self.carrier_locked = self._lock_detector < 0.3
                self.clock_locked = abs(self._omega - self.sps) < self.sps * 0.1
            else:
                self._mu -= 0  # advance

            idx += 1

        # Symbol decisions → bits → bytes
        bits = []
        for sym in recovered_symbols:
            decided_idx = self._symbol_to_index(sym)
            for b in range(self._bps - 1, -1, -1):
                bits.append((decided_idx >> b) & 1)

        # Pack bits to bytes
        out = bytearray()
        for i in range(0, len(bits) - 7, 8):
            byte_val = 0
            for b in range(8):
                byte_val = (byte_val << 1) | bits[i + b]
            out.append(byte_val)
        return bytes(out)

    def _hard_decide(self, sample: complex) -> complex:
        """Hard decision: find nearest constellation point."""
        distances = np.abs(self._map - sample)
        return self._map[np.argmin(distances)]

    def _symbol_to_index(self, sample: complex) -> int:
        """Map a received sample to the nearest constellation index."""
        distances = np.abs(self._map - sample)
        return int(np.argmin(distances))

    def get_constellation_iq(self, max_points: int = 128) -> list[list[float]]:
        """Return recent constellation points as [[I, Q], ...] for display."""
        points = self.constellation_points[-max_points:]
        return [[round(float(p.real), 3), round(float(p.imag), 3)] for p in points]

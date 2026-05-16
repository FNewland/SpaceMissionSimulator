"""Baseband modulator — maps bits to complex symbols with pulse shaping.

Supports BPSK, QPSK, 8PSK, and OQPSK. Applies root-raised-cosine (RRC)
pulse shaping at the configured samples-per-symbol rate.
"""

import math
import numpy as np
from typing import Optional

# Constellation maps: symbol index → complex point (unit energy)
BPSK_MAP = np.array([-1.0 + 0j, 1.0 + 0j])

QPSK_MAP = np.array([
    1 + 1j, -1 + 1j, -1 - 1j, 1 - 1j
]) / math.sqrt(2)

PSK8_MAP = np.array([
    np.exp(1j * 2 * math.pi * k / 8) for k in range(8)
])

# OQPSK uses same map as QPSK but with half-symbol offset on Q
OQPSK_MAP = QPSK_MAP.copy()

CONSTELLATION_MAPS = {0: BPSK_MAP, 1: QPSK_MAP, 2: PSK8_MAP, 3: OQPSK_MAP}
BITS_PER_SYMBOL = {0: 1, 1: 2, 2: 3, 3: 2}


def _rrc_taps(sps: int, rolloff: float = 0.35, ntaps: int = 101) -> np.ndarray:
    """Root-raised-cosine filter taps."""
    n = np.arange(ntaps) - (ntaps - 1) / 2
    t = n / sps
    taps = np.zeros(ntaps)
    for i, ti in enumerate(t):
        if ti == 0:
            taps[i] = 1.0 - rolloff + 4 * rolloff / math.pi
        elif abs(abs(ti) - 1 / (4 * rolloff)) < 1e-8 and rolloff > 0:
            taps[i] = (rolloff / math.sqrt(2)) * (
                (1 + 2 / math.pi) * math.sin(math.pi / (4 * rolloff)) +
                (1 - 2 / math.pi) * math.cos(math.pi / (4 * rolloff)))
        else:
            num = (math.sin(math.pi * ti * (1 - rolloff)) +
                   4 * rolloff * ti * math.cos(math.pi * ti * (1 + rolloff)))
            den = math.pi * ti * (1 - (4 * rolloff * ti) ** 2)
            taps[i] = num / den if den != 0 else 0
    return taps / np.sqrt(np.sum(taps ** 2))


class Modulator:
    """Maps data bytes to pulse-shaped complex baseband samples.

    Parameters:
        modulation: 0=BPSK, 1=QPSK, 2=8PSK, 3=OQPSK
        sps: samples per symbol (oversampling factor)
        rolloff: RRC rolloff factor (0.2–0.5 typical)
    """

    def __init__(self, modulation: int = 0, sps: int = 8, rolloff: float = 0.35):
        self.modulation = modulation
        self.sps = sps
        self.rolloff = rolloff
        self._map = CONSTELLATION_MAPS.get(modulation, BPSK_MAP)
        self._bps = BITS_PER_SYMBOL.get(modulation, 1)
        self._rrc = _rrc_taps(sps, rolloff)

    def set_modulation(self, modulation: int):
        self.modulation = modulation
        self._map = CONSTELLATION_MAPS.get(modulation, BPSK_MAP)
        self._bps = BITS_PER_SYMBOL.get(modulation, 1)

    def modulate(self, data: bytes) -> np.ndarray:
        """Convert data bytes to complex baseband samples.

        Returns pulse-shaped complex64 array at sps × symbol rate.
        """
        # Unpack bytes to bits
        bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))

        # Pad to multiple of bits_per_symbol
        pad = (-len(bits)) % self._bps
        if pad:
            bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])

        # Map bits to symbol indices
        n_symbols = len(bits) // self._bps
        symbols = np.zeros(n_symbols, dtype=np.complex64)
        for i in range(n_symbols):
            idx = 0
            for b in range(self._bps):
                idx = (idx << 1) | int(bits[i * self._bps + b])
            symbols[i] = self._map[idx % len(self._map)]

        # Upsample: insert zeros between symbols
        upsampled = np.zeros(n_symbols * self.sps, dtype=np.complex64)
        upsampled[::self.sps] = symbols

        # Pulse shaping via RRC filter
        shaped = np.convolve(upsampled, self._rrc.astype(np.complex64), mode='same')
        return shaped

    @property
    def symbol_map(self) -> np.ndarray:
        """Return the ideal constellation points."""
        return self._map.copy()

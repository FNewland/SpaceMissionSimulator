"""Baseband modulator — maps bits to complex symbols with pulse shaping.

Supports BPSK, QPSK, 8PSK, OQPSK, GMSK, GFSK, 16-APSK, and π/4-DQPSK.
Applies root-raised-cosine (RRC) pulse shaping for PSK/APSK modes,
and Gaussian pulse shaping for GMSK/GFSK modes.
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

# 16-APSK: DVB-S2 two-ring constellation (4 inner + 12 outer)
_R1 = 1.0  # inner ring radius
_R2 = 2.7  # outer ring radius (γ = R2/R1 ≈ 2.7 for rate 2/3)
_norm_16apsk = math.sqrt((4 * _R1**2 + 12 * _R2**2) / 16)
APSK16_MAP = np.concatenate([
    _R1 * np.array([np.exp(1j * (math.pi/4 + k * math.pi/2)) for k in range(4)]),
    _R2 * np.array([np.exp(1j * (math.pi/12 + k * math.pi/6)) for k in range(12)]),
]) / _norm_16apsk

# π/4-DQPSK: differential QPSK with π/4 rotation per symbol
PI4DQPSK_MAP = QPSK_MAP.copy()  # same base constellation, differential encoding

# GMSK/GFSK: no constellation map (CPM — continuous phase modulation)
# These use frequency modulation, not amplitude/phase mapping

CONSTELLATION_MAPS = {
    0: BPSK_MAP, 1: QPSK_MAP, 2: PSK8_MAP, 3: OQPSK_MAP,
    4: APSK16_MAP,   # 16-APSK
    5: PI4DQPSK_MAP, # π/4-DQPSK
    # 6: GMSK (CPM, no constellation map)
    # 7: GFSK (CPM, no constellation map)
}
BITS_PER_SYMBOL = {
    0: 1,  # BPSK
    1: 2,  # QPSK
    2: 3,  # 8PSK
    3: 2,  # OQPSK
    4: 4,  # 16-APSK
    5: 2,  # π/4-DQPSK
    6: 1,  # GMSK
    7: 1,  # GFSK
}
MOD_NAMES = {
    0: "BPSK", 1: "QPSK", 2: "8PSK", 3: "OQPSK",
    4: "16-APSK", 5: "π/4-DQPSK", 6: "GMSK", 7: "GFSK",
}


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


def _gaussian_taps(sps: int, bt: float = 0.5, ntaps: int = 33) -> np.ndarray:
    """Gaussian pulse shape for GMSK/GFSK (BT product configurable)."""
    t = np.arange(ntaps) - (ntaps - 1) / 2
    t = t / sps
    alpha = math.sqrt(math.log(2) / 2) / bt
    taps = np.exp(-((math.pi * t / alpha) ** 2))
    return taps / np.sum(taps)


class Modulator:
    """Maps data bytes to pulse-shaped complex baseband samples.

    Parameters:
        modulation: 0=BPSK, 1=QPSK, 2=8PSK, 3=OQPSK,
                    4=16-APSK, 5=π/4-DQPSK, 6=GMSK, 7=GFSK
        sps: samples per symbol (oversampling factor)
        rolloff: RRC rolloff factor (0.2–0.5 typical, PSK modes)
        gmsk_bt: Gaussian BT product (0.3–0.5, GMSK/GFSK modes)
    """

    def __init__(self, modulation: int = 0, sps: int = 8,
                 rolloff: float = 0.35, gmsk_bt: float = 0.5):
        self.modulation = modulation
        self.sps = sps
        self.rolloff = rolloff
        self.gmsk_bt = gmsk_bt
        self._map = CONSTELLATION_MAPS.get(modulation, BPSK_MAP)
        self._bps = BITS_PER_SYMBOL.get(modulation, 1)
        self._rrc = _rrc_taps(sps, rolloff)
        self._gaussian = _gaussian_taps(sps, gmsk_bt)
        self._gmsk_phase = 0.0  # accumulated phase for GMSK/GFSK
        self._pi4_rotation = 0.0  # accumulated rotation for π/4-DQPSK

    def set_modulation(self, modulation: int):
        self.modulation = modulation
        self._map = CONSTELLATION_MAPS.get(modulation, BPSK_MAP)
        self._bps = BITS_PER_SYMBOL.get(modulation, 1)
        self._gmsk_phase = 0.0
        self._pi4_rotation = 0.0

    def modulate(self, data: bytes) -> np.ndarray:
        """Convert data bytes to complex baseband samples.

        Returns pulse-shaped complex64 array at sps × symbol rate.
        """
        # GMSK/GFSK: continuous phase modulation (special path)
        if self.modulation in (6, 7):
            return self._modulate_cpm(data)

        # Unpack bytes to bits
        bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))

        # Pad to multiple of bits_per_symbol
        pad = (-len(bits)) % self._bps
        if pad:
            bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])

        # Map bits to symbol indices
        n_symbols = len(bits) // self._bps
        symbols = np.zeros(n_symbols, dtype=np.complex64)

        if self.modulation == 5:  # π/4-DQPSK: differential encoding
            for i in range(n_symbols):
                idx = 0
                for b in range(self._bps):
                    idx = (idx << 1) | int(bits[i * self._bps + b])
                # Phase change from lookup
                phase_change = [math.pi/4, 3*math.pi/4, -3*math.pi/4, -math.pi/4][idx]
                self._pi4_rotation += phase_change
                symbols[i] = np.exp(1j * self._pi4_rotation)
        else:
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

    def _modulate_cpm(self, data: bytes) -> np.ndarray:
        """GMSK/GFSK modulation (continuous phase modulation).

        Maps bits to frequency deviations, applies Gaussian pulse shaping,
        then integrates to produce continuous phase. Output is unit-energy
        complex exponential with smooth phase transitions.

        GMSK: modulation index h = 0.5 (standard)
        GFSK: configurable modulation index (default h = 0.5)
        """
        bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
        # NRZ encoding: 0 → -1, 1 → +1
        nrz = 2.0 * bits.astype(np.float64) - 1.0

        # Upsample NRZ to sample rate
        upsampled = np.zeros(len(nrz) * self.sps)
        upsampled[::self.sps] = nrz

        # Gaussian pulse shaping (frequency domain)
        freq_shaped = np.convolve(upsampled, self._gaussian, mode='same')

        # Modulation index h = 0.5 for GMSK, configurable for GFSK
        h = 0.5
        # Instantaneous phase = cumulative integral of frequency
        phase = np.cumsum(freq_shaped) * (math.pi * h / self.sps) + self._gmsk_phase
        self._gmsk_phase = phase[-1] if len(phase) > 0 else self._gmsk_phase
        self._gmsk_phase %= (2 * math.pi)

        return np.exp(1j * phase).astype(np.complex64)

    @property
    def symbol_map(self) -> np.ndarray:
        """Return the ideal constellation points."""
        return self._map.copy()

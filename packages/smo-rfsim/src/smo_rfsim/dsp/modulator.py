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


def _gray_psk_map(m: int, phase_offset: float = 0.0) -> np.ndarray:
    """Gray-coded M-PSK constellation map.

    Returns an array indexed by the *bit pattern* such that geometrically
    adjacent constellation points differ in exactly one bit — the property
    that makes measured BER match the textbook Gray-coded curves (a symbol
    error at high Eb/N0 almost always costs a single bit).

    ``pts[gray(p)] = exp(j(phase_offset + 2*pi*p/M))`` where
    ``gray(p) = p XOR (p >> 1)``.
    """
    pts = np.zeros(m, dtype=complex)
    for p in range(m):
        g = p ^ (p >> 1)
        pts[g] = np.exp(1j * (phase_offset + 2 * math.pi * p / m))
    return pts


# Gray-coded variants (default from 2026-07 RF fixes — see defect #54).
# The natural-binary maps above remain selectable via gray_coding=False.
QPSK_GRAY_MAP = _gray_psk_map(4, math.pi / 4)
PSK8_GRAY_MAP = _gray_psk_map(8, 0.0)

# OQPSK uses the same constellation as QPSK; the defining difference is a
# half-symbol (sps/2) delay applied to the Q rail in Modulator.modulate(),
# which bounds phase transitions to +/-90 deg and avoids envelope zero
# crossings. See _shape_psk().
OQPSK_MAP = QPSK_MAP.copy()
OQPSK_GRAY_MAP = QPSK_GRAY_MAP.copy()

# 16-APSK: DVB-S2 two-ring constellation (4 inner + 12 outer)
_R1 = 1.0  # inner ring radius
_R2 = 2.7  # outer ring radius (γ = R2/R1 ≈ 2.7 for rate 2/3)
_norm_16apsk = math.sqrt((4 * _R1**2 + 12 * _R2**2) / 16)
APSK16_MAP = np.concatenate([
    _R1 * np.array([np.exp(1j * (math.pi/4 + k * math.pi/2)) for k in range(4)]),
    _R2 * np.array([np.exp(1j * (math.pi/12 + k * math.pi/6)) for k in range(12)]),
]) / _norm_16apsk

# π/4-DQPSK: differential QPSK with π/4 rotation per symbol.
# The transmitted symbols land on the union of two QPSK sets rotated by
# π/4 — i.e. the 8PSK ring — so PSK8_MAP is the right reference for EVM
# and lock detection. Information is carried in the phase *change*.
PI4DQPSK_MAP = QPSK_MAP.copy()  # base constellation; encoding is differential

# Phase change per dibit. Natural: index order π/4, 3π/4, -3π/4, -π/4.
# Gray: geometrically adjacent phase changes differ in one bit.
PI4_PHASE_CHANGES_NATURAL = [math.pi / 4, 3 * math.pi / 4,
                             -3 * math.pi / 4, -math.pi / 4]
PI4_PHASE_CHANGES_GRAY = [-3 * math.pi / 4, -math.pi / 4,
                          3 * math.pi / 4, math.pi / 4]

# GMSK/GFSK: no constellation map (CPM — continuous phase modulation)
# These use frequency modulation, not amplitude/phase mapping

CONSTELLATION_MAPS = {
    0: BPSK_MAP, 1: QPSK_MAP, 2: PSK8_MAP, 3: OQPSK_MAP,
    4: APSK16_MAP,   # 16-APSK
    5: PI4DQPSK_MAP, # π/4-DQPSK
    # 6: GMSK (CPM, no constellation map)
    # 7: GFSK (CPM, no constellation map)
}

# Gray-coded symbol maps. 16-APSK keeps the DVB-S2 ring geometry with
# natural-binary ring labelling (no clean Gray labelling exists for a
# 4+12 two-ring constellation); it is self-consistent TX↔RX either way.
GRAY_CONSTELLATION_MAPS = {
    0: BPSK_MAP, 1: QPSK_GRAY_MAP, 2: PSK8_GRAY_MAP, 3: OQPSK_GRAY_MAP,
    4: APSK16_MAP,
    5: PI4DQPSK_MAP,
}


def constellation_map(modulation: int, gray_coding: bool = True) -> np.ndarray:
    """Return the symbol map for a modulation, Gray- or natural-coded.

    Both the modulator and the receiver call this so the two sides can
    never disagree about the labelling.
    """
    table = GRAY_CONSTELLATION_MAPS if gray_coding else CONSTELLATION_MAPS
    return table.get(modulation, BPSK_MAP)


def pi4_phase_changes(gray_coding: bool = True) -> list:
    """Return the π/4-DQPSK dibit → phase-change table."""
    return (PI4_PHASE_CHANGES_GRAY if gray_coding
            else PI4_PHASE_CHANGES_NATURAL)
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
                 rolloff: float = 0.35, gmsk_bt: float = 0.5,
                 gray_coding: bool = True, gfsk_mod_index: float = 0.5):
        self.modulation = modulation
        self.sps = sps
        self.rolloff = rolloff
        self.gmsk_bt = gmsk_bt
        self.gray_coding = gray_coding
        self.gfsk_mod_index = gfsk_mod_index
        self._map = constellation_map(modulation, gray_coding)
        self._bps = BITS_PER_SYMBOL.get(modulation, 1)
        self._rrc = _rrc_taps(sps, rolloff)
        self._gaussian = _gaussian_taps(sps, gmsk_bt)
        self._gmsk_phase = 0.0  # accumulated phase for GMSK/GFSK
        self._pi4_rotation = 0.0  # accumulated rotation for π/4-DQPSK
        # OQPSK: the half-symbol Q delay pushes sps/2 samples of the Q rail
        # past the end of each chunk. They are carried here so the offset is
        # continuous across successive modulate() calls.
        self._oqpsk_q_tail = np.zeros(sps // 2, dtype=np.float64)

    def set_modulation(self, modulation: int):
        self.modulation = modulation
        self._map = constellation_map(modulation, self.gray_coding)
        self._bps = BITS_PER_SYMBOL.get(modulation, 1)
        self._gmsk_phase = 0.0
        self._pi4_rotation = 0.0
        self._oqpsk_q_tail = np.zeros(self.sps // 2, dtype=np.float64)

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
            table = pi4_phase_changes(self.gray_coding)
            for i in range(n_symbols):
                idx = 0
                for b in range(self._bps):
                    idx = (idx << 1) | int(bits[i * self._bps + b])
                # Phase change from lookup
                phase_change = table[idx]
                self._pi4_rotation += phase_change
                symbols[i] = np.exp(1j * self._pi4_rotation)
        else:
            for i in range(n_symbols):
                idx = 0
                for b in range(self._bps):
                    idx = (idx << 1) | int(bits[i * self._bps + b])
                symbols[i] = self._map[idx % len(self._map)]

        return self._shape_psk(symbols)

    def _shape_psk(self, symbols: np.ndarray) -> np.ndarray:
        """Upsample and RRC pulse-shape a symbol sequence.

        For OQPSK (modulation 3) the Q rail is delayed by half a symbol
        period (sps/2 samples) *before* pulse shaping. This is the defining
        feature of offset QPSK: I and Q never transition at the same instant,
        so the phase trajectory is bounded to ±90° and the envelope never
        passes through zero. The delay is continuous across calls via
        ``_oqpsk_q_tail``.
        """
        n_symbols = len(symbols)
        rrc = self._rrc.astype(np.float64)

        if self.modulation != 3 or self.sps < 2:
            upsampled = np.zeros(n_symbols * self.sps, dtype=np.complex64)
            upsampled[::self.sps] = symbols
            return np.convolve(upsampled,
                               self._rrc.astype(np.complex64), mode='same')

        half = self.sps // 2
        up_i = np.zeros(n_symbols * self.sps, dtype=np.float64)
        up_q = np.zeros(n_symbols * self.sps, dtype=np.float64)
        up_i[::self.sps] = symbols.real
        up_q[::self.sps] = symbols.imag

        # Delay Q by half a symbol, carrying the tail across chunks.
        delayed_q = np.concatenate([self._oqpsk_q_tail, up_q])
        self._oqpsk_q_tail = delayed_q[len(up_q):].copy()
        delayed_q = delayed_q[:len(up_q)]

        shaped_i = np.convolve(up_i, rrc, mode='same')
        shaped_q = np.convolve(delayed_q, rrc, mode='same')
        return (shaped_i + 1j * shaped_q).astype(np.complex64)

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

        # Modulation index: h = 0.5 for GMSK (MSK-family, CCSDS 401.0-B
        # 2.4.17B), configurable for GFSK.
        h = 0.5 if self.modulation == 6 else self.gfsk_mod_index
        # Instantaneous phase = cumulative integral of frequency
        phase = np.cumsum(freq_shaped) * (math.pi * h / self.sps) + self._gmsk_phase
        self._gmsk_phase = phase[-1] if len(phase) > 0 else self._gmsk_phase
        self._gmsk_phase %= (2 * math.pi)

        return np.exp(1j * phase).astype(np.complex64)

    @property
    def symbol_map(self) -> np.ndarray:
        """Return the ideal constellation points."""
        return self._map.copy()

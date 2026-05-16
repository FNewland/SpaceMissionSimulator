"""End-to-end DSP processor — ties modulator, channel, and demodulator together.

Provides a single process() call that takes frame bytes in and returns
frame bytes out, plus a constellation tap for the Radio display.
"""

import logging
from typing import Optional

import numpy as np

from .modulator import Modulator, BITS_PER_SYMBOL
from .channel import BasebandChannel
from .demodulator import Demodulator

logger = logging.getLogger(__name__)


class DSPProcessor:
    """Real baseband signal processing chain.

    Modulates data bytes into complex samples, passes them through a
    channel model with AWGN and frequency offset, then demodulates
    with carrier and clock recovery. The constellation tap provides
    actual demodulated I/Q points for display.

    Parameters:
        modulation: 0=BPSK, 1=QPSK, 2=8PSK, 3=OQPSK
        sps: samples per symbol
        eb_n0_db: initial Eb/N0 in dB
        symbol_rate: symbol rate in Hz
        freq_offset_hz: carrier frequency offset (Doppler)
    """

    def __init__(self, modulation: int = 0, sps: int = 8,
                 eb_n0_db: float = 10.0, symbol_rate: float = 32000.0,
                 freq_offset_hz: float = 0.0, seed: int = 42):
        self.sps = sps
        self.symbol_rate = symbol_rate
        sample_rate = symbol_rate * sps
        bps = BITS_PER_SYMBOL.get(modulation, 1)

        self.modulator = Modulator(modulation=modulation, sps=sps)
        self.channel = BasebandChannel(
            eb_n0_db=eb_n0_db, sps=sps, bits_per_symbol=bps,
            freq_offset_hz=freq_offset_hz, sample_rate=sample_rate,
            seed=seed)
        self.demodulator = Demodulator(modulation=modulation, sps=sps)

        self._modulation = modulation
        self._active = False  # True when link is up
        self._rng = np.random.default_rng(seed + 1)

    @property
    def carrier_locked(self) -> bool:
        return self.demodulator.carrier_locked

    @property
    def clock_locked(self) -> bool:
        return self.demodulator.clock_locked

    def set_modulation(self, modulation: int):
        """Change modulation scheme (called when spacecraft changes mode)."""
        if modulation == self._modulation:
            return
        self._modulation = modulation
        bps = BITS_PER_SYMBOL.get(modulation, 1)
        self.modulator.set_modulation(modulation)
        self.demodulator.set_modulation(modulation)
        self.channel.bits_per_symbol = bps
        logger.info("DSP modulation changed to %d (%d bps)", modulation, bps)

    def set_eb_n0(self, eb_n0_db: float):
        self.channel.set_eb_n0(eb_n0_db)

    def set_freq_offset(self, freq_offset_hz: float):
        self.channel.set_freq_offset(freq_offset_hz)

    def set_active(self, active: bool):
        """Set whether the RF link is active (in pass)."""
        self._active = active

    def process(self, frame_bytes: bytes) -> Optional[bytes]:
        """Process frame bytes through the full mod→channel→demod chain.

        Returns demodulated bytes, or None if link is not active.
        Constellation points are available via get_constellation().
        """
        if not self._active:
            # No link — demodulator sees only noise
            noise_samples = (self._rng.normal(0, 0.3, 128) +
                             1j * self._rng.normal(0, 0.3, 128))
            self.demodulator.constellation_points = list(noise_samples.astype(np.complex64))
            self.demodulator.carrier_locked = False
            self.demodulator.clock_locked = False
            return None

        # 1. Modulate
        tx_samples = self.modulator.modulate(frame_bytes)

        # 2. Channel impairments
        rx_samples = self.channel.process(tx_samples)

        # 3. Demodulate (populates constellation tap)
        recovered = self.demodulator.demodulate(rx_samples)

        return recovered

    def get_constellation(self, max_points: int = 128) -> list[list[float]]:
        """Get the most recent constellation I/Q points from the demodulator.

        These are REAL demodulated samples from the carrier/clock recovery
        output, not synthesized from an Eb/N0 number.
        """
        return self.demodulator.get_constellation_iq(max_points)

    def get_noise_only_constellation(self, max_points: int = 128) -> list[list[float]]:
        """Generate a noise-only constellation (no signal)."""
        noise = self._rng.normal(0, 0.3, (max_points, 2))
        return [[round(float(n[0]), 3), round(float(n[1]), 3)] for n in noise]

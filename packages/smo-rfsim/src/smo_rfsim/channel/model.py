"""Channel impairment model — AWGN, path loss, and propagation delay.

In FRAME mode this provides a pure-Python BER-based bit-flip injection
model. In RF mode (Phase 2) the GNU Radio channel model replaces this.
"""

import math
import logging

logger = logging.getLogger(__name__)


def eb_n0_to_ber_bpsk(eb_n0_db: float) -> float:
    """Theoretical BER for uncoded BPSK given Eb/N0 in dB.

    BER = 0.5 * erfc(sqrt(Eb/N0))
    """
    eb_n0_linear = 10 ** (eb_n0_db / 10.0)
    return 0.5 * math.erfc(math.sqrt(eb_n0_linear))


def eb_n0_to_ber(eb_n0_db: float, modulation: int = 0) -> float:
    """Theoretical BER for the given modulation scheme.

    Args:
        eb_n0_db: Eb/N0 in dB
        modulation: 0=BPSK, 1=QPSK, 2=8PSK, 3=OQPSK

    Returns:
        Bit error rate (probability)
    """
    eb_n0_linear = 10 ** (eb_n0_db / 10.0)
    if eb_n0_linear <= 0:
        return 0.5
    if modulation in (0, 1, 3):  # BPSK, QPSK, OQPSK
        return 0.5 * math.erfc(math.sqrt(eb_n0_linear))
    elif modulation == 2:  # 8PSK
        return (2.0 / 3.0) * math.erfc(math.sqrt(0.4 * eb_n0_linear))
    return 0.5 * math.erfc(math.sqrt(eb_n0_linear))


def free_space_path_loss(distance_km: float, freq_mhz: float = 2200.0) -> float:
    """Free-space path loss in dB.

    FSPL(dB) = 20*log10(d) + 20*log10(f) + 32.45
    where d is in km and f is in MHz.
    """
    if distance_km <= 0:
        return 0.0
    return 20 * math.log10(distance_km) + 20 * math.log10(freq_mhz) + 32.45


class ChannelModel:
    """Configurable channel impairment model.

    Applies bit-error injection to frame bytes based on the current
    Eb/N0 setting, using a deterministic pseudo-random pattern.
    """

    def __init__(self, eb_n0_db: float = 10.0, seed: int = 42):
        self.eb_n0_db = eb_n0_db
        self._seed = seed
        self._rng_state = seed
        # Statistics
        self.total_bits = 0
        self.error_bits = 0
        self.total_frames = 0

    @property
    def measured_ber(self) -> float:
        if self.total_bits == 0:
            return 0.0
        return self.error_bits / self.total_bits

    @property
    def theoretical_ber(self) -> float:
        return eb_n0_to_ber_bpsk(self.eb_n0_db)

    def _next_random(self) -> float:
        """Simple LCG PRNG for deterministic bit errors."""
        self._rng_state = (self._rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        return self._rng_state / 0x7FFFFFFF

    def impair(self, frame_bytes: bytes) -> bytes:
        """Apply bit-error injection to a frame.

        Each bit is independently flipped with probability = BER.
        """
        ber = self.theoretical_ber
        if ber <= 0 or ber < 1e-12:
            self.total_bits += len(frame_bytes) * 8
            self.total_frames += 1
            return frame_bytes

        result = bytearray(frame_bytes)
        errors = 0
        for i in range(len(result)):
            for bit in range(8):
                if self._next_random() < ber:
                    result[i] ^= (1 << bit)
                    errors += 1

        self.total_bits += len(frame_bytes) * 8
        self.error_bits += errors
        self.total_frames += 1

        if errors > 0:
            logger.debug("Channel: injected %d bit errors in %d-byte frame "
                         "(BER=%.2e)", errors, len(frame_bytes), ber)
        return bytes(result)

    def reset_stats(self):
        self.total_bits = 0
        self.error_bits = 0
        self.total_frames = 0

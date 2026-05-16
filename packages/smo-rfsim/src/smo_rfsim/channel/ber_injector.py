"""BER-driven bit-flip injector using Python's random module.

This is a higher-quality alternative to the LCG in model.py,
using Python's Mersenne Twister for better statistical properties.
"""

import random
import logging

logger = logging.getLogger(__name__)


class BERInjector:
    """Inject bit errors at a specified BER using random module."""

    def __init__(self, ber: float = 1e-6, seed: int | None = None):
        self.ber = ber
        self._rng = random.Random(seed)
        self.total_bits = 0
        self.error_bits = 0

    def inject(self, data: bytes) -> bytes:
        """Flip bits with probability = self.ber."""
        if self.ber <= 0:
            self.total_bits += len(data) * 8
            return data

        result = bytearray(data)
        errors = 0
        for i in range(len(result)):
            for bit in range(8):
                if self._rng.random() < self.ber:
                    result[i] ^= (1 << bit)
                    errors += 1

        self.total_bits += len(data) * 8
        self.error_bits += errors
        return bytes(result)

    @property
    def measured_ber(self) -> float:
        if self.total_bits == 0:
            return 0.0
        return self.error_bits / self.total_bits

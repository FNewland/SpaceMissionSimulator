"""CCSDS pseudo-random sequence generator (scrambler/randomizer).

Applies the standard CCSDS randomization sequence to TM frame data
to ensure bit transitions for clock recovery. Uses the polynomial:
    x^8 + x^7 + x^5 + x^3 + 1

The same sequence is applied to both scramble (TX) and descramble (RX)
since XOR is its own inverse. The sequence resets at every frame
(synchronized to the ASM which is NOT scrambled).

Reference: CCSDS 131.0-B-4 §7 (Pseudo-Randomizer)
"""

import numpy as np


# Pre-computed CCSDS pseudo-random sequence (255 bytes, repeating)
def _generate_sequence(length: int = 255) -> bytes:
    """Generate CCSDS randomization sequence.

    LFSR polynomial: x^8 + x^7 + x^5 + x^3 + 1
    Initial state: all ones (0xFF)
    """
    register = 0xFF  # initial state
    sequence = bytearray()
    for _ in range(length):
        byte_val = 0
        for bit in range(8):
            # Output bit is the MSB
            output = (register >> 7) & 1
            byte_val = (byte_val << 1) | output
            # Feedback: x^8 + x^7 + x^5 + x^3 + 1
            feedback = ((register >> 7) ^ (register >> 5)
                        ^ (register >> 3) ^ register) & 1
            register = ((register << 1) | feedback) & 0xFF
        sequence.append(byte_val)
    return bytes(sequence)


# Pre-computed sequence (cached at module load)
CCSDS_SEQUENCE = _generate_sequence(2048)  # enough for any frame size


def scramble(data: bytes) -> bytes:
    """Apply CCSDS randomization to frame data (after ASM, before coding).

    The sequence resets at every frame boundary (synchronized to ASM).
    """
    n = len(data)
    # Extend sequence if needed
    seq = CCSDS_SEQUENCE
    while len(seq) < n:
        seq = seq + seq
    return bytes(a ^ b for a, b in zip(data, seq[:n]))


def descramble(data: bytes) -> bytes:
    """Remove CCSDS randomization from received frame data.

    Identical to scramble() since XOR is self-inverse.
    """
    return scramble(data)

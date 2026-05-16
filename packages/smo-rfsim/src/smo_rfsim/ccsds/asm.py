"""CCSDS Attached Sync Marker (ASM) for TM Transfer Frames.

The ASM is a 32-bit pattern (0x1ACFFC1D) prepended to every TM transfer
frame to allow the receiver to locate frame boundaries.

BPSK receivers may lock at 180° phase offset, inverting all bits.
The inverted ASM (0xE53003E2) is checked alongside the normal pattern
so the frame synchronizer can detect and correct the inversion.

Reference: CCSDS 131.0-B-4 (TM Synchronization and Channel Coding)
"""

import struct

# Standard CCSDS TM ASM (32 bits)
ASM_BYTES = b'\x1A\xCF\xFC\x1D'
ASM_INT = 0x1ACFFC1D
ASM_LENGTH = 4  # bytes

# Bit-inverted ASM for 180° BPSK phase ambiguity detection
ASM_INVERTED_INT = ASM_INT ^ 0xFFFFFFFF  # 0xE53003E2


def attach_asm(frame: bytes) -> bytes:
    """Prepend the ASM to a transfer frame."""
    return ASM_BYTES + frame


def strip_asm(data: bytes) -> bytes:
    """Remove the ASM from the front of a transfer frame."""
    if data[:ASM_LENGTH] == ASM_BYTES:
        return data[ASM_LENGTH:]
    return data


def correlate_asm(data: bytes, offset: int = 0, max_bit_errors: int = 3) -> int:
    """Find the ASM in a byte stream using bit-error-tolerant correlation.

    Returns the byte offset of the ASM start, or -1 if not found.
    The correlator allows up to *max_bit_errors* bit mismatches.
    """
    if len(data) < offset + ASM_LENGTH:
        return -1
    for i in range(offset, len(data) - ASM_LENGTH + 1):
        candidate = struct.unpack('>I', data[i:i + ASM_LENGTH])[0]
        xor = candidate ^ ASM_INT
        bit_errors = bin(xor).count('1')
        if bit_errors <= max_bit_errors:
            return i
    return -1


def correlate_asm_with_inversion(data: bytes, offset: int = 0,
                                  max_bit_errors: int = 3) -> tuple[int, bool]:
    """Find ASM or inverted ASM in a byte stream.

    Returns (byte_offset, inverted) where inverted=True means the
    stream has 180° BPSK phase ambiguity (all bits flipped).
    Returns (-1, False) if neither pattern is found.
    """
    if len(data) < offset + ASM_LENGTH:
        return -1, False
    for i in range(offset, len(data) - ASM_LENGTH + 1):
        candidate = struct.unpack('>I', data[i:i + ASM_LENGTH])[0]
        # Check normal ASM
        xor_normal = candidate ^ ASM_INT
        if bin(xor_normal).count('1') <= max_bit_errors:
            return i, False
        # Check inverted ASM (180° phase ambiguity)
        xor_inv = candidate ^ ASM_INVERTED_INT
        if bin(xor_inv).count('1') <= max_bit_errors:
            return i, True
    return -1, False

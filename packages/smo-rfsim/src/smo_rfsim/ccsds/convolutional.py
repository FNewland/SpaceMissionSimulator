"""Rate-1/2, constraint length K=7 convolutional encoder.

This is the standard CCSDS convolutional code used for TM downlink.
Generator polynomials: G1 = 0x79 (1111001), G2 = 0x5B (1011011).

Pure-Python encoder only — Viterbi decoder is deferred to Phase 2
(GNU Radio provides a high-performance implementation).

Reference: CCSDS 131.0-B-4 (TM Synchronization and Channel Coding)
"""

G1 = 0x79  # generator polynomial 1: 1111001
G2 = 0x5B  # generator polynomial 2: 1011011
K = 7      # constraint length
RATE_INV = 2  # rate = 1/2, so each input bit produces 2 output bits


def _parity(value: int) -> int:
    """Return parity (0 or 1) of an integer's set bits."""
    p = 0
    while value:
        p ^= 1
        value &= value - 1
    return p


def encode(data: bytes) -> bytes:
    """Convolutional encode a byte sequence at rate 1/2.

    Input: N bytes → Output: 2N bytes (each input bit becomes 2 output bits).
    The encoder is flushed with K-1 = 6 zero bits at the end.
    """
    register = 0
    output_bits = []

    for byte in data:
        for bit_pos in range(7, -1, -1):
            input_bit = (byte >> bit_pos) & 1
            register = ((register << 1) | input_bit) & 0x7F
            g1_out = _parity(register & G1)
            g2_out = _parity(register & G2)
            output_bits.append(g1_out)
            output_bits.append(g2_out)

    # Flush with K-1 zero bits
    for _ in range(K - 1):
        register = (register << 1) & 0x7F
        g1_out = _parity(register & G1)
        g2_out = _parity(register & G2)
        output_bits.append(g1_out)
        output_bits.append(g2_out)

    # Pack output bits into bytes
    out = bytearray()
    for i in range(0, len(output_bits), 8):
        byte_val = 0
        for j in range(8):
            if i + j < len(output_bits):
                byte_val = (byte_val << 1) | output_bits[i + j]
            else:
                byte_val <<= 1
        out.append(byte_val)
    return bytes(out)


def encoded_length(input_length: int) -> int:
    """Compute output byte length for a given input byte length."""
    total_bits = (input_length * 8 + (K - 1)) * RATE_INV
    return (total_bits + 7) // 8

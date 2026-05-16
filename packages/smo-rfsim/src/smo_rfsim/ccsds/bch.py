"""BCH(64,56) encoder/decoder for TC CLTU code blocks.

Each CLTU code block is 8 bytes: 7 data bytes + 1 parity byte.
The parity byte is computed using the BCH(63,56) shortened code
with generator polynomial g(x) = x^7 + x^6 + x^2 + 1 (0xC5),
plus a filler bit set to complement the last parity bit.

Reference: CCSDS 232.0-B-4 (TC Space Data Link Protocol)
"""


def _bch_parity(data_7bytes: bytes) -> int:
    """Compute the BCH parity byte for 7 data bytes.

    Uses shift-register implementation of the generator polynomial
    g(x) = x^7 + x^6 + x^2 + 1.
    """
    sr = 0  # 7-bit shift register
    for byte in data_7bytes:
        for bit_pos in range(7, -1, -1):
            input_bit = (byte >> bit_pos) & 1
            feedback = input_bit ^ ((sr >> 6) & 1)
            sr = ((sr << 1) & 0x7F)
            if feedback:
                sr ^= 0x45  # x^6 + x^2 + x^0 (taps from g(x))
    # Filler bit: complement of bit 0 of the shift register
    filler = (~sr) & 1
    parity = ((sr & 0x7F) << 1) | filler
    return parity & 0xFF


def encode_code_block(data_7bytes: bytes) -> bytes:
    """Encode 7 data bytes into an 8-byte BCH(64,56) code block."""
    if len(data_7bytes) != 7:
        raise ValueError(f"Expected 7 bytes, got {len(data_7bytes)}")
    parity = _bch_parity(data_7bytes)
    return data_7bytes + bytes([parity])


def check_code_block(block_8bytes: bytes) -> tuple[bytes, bool]:
    """Check and return (data_7bytes, is_valid) for an 8-byte code block."""
    if len(block_8bytes) != 8:
        raise ValueError(f"Expected 8 bytes, got {len(block_8bytes)}")
    data = block_8bytes[:7]
    expected = _bch_parity(data)
    return data, block_8bytes[7] == expected


def correct_code_block(block_8bytes: bytes) -> tuple[bytes, bool, int]:
    """Attempt single-bit error correction on a code block.

    Returns (corrected_data, is_valid, num_corrections).
    """
    data, valid = check_code_block(block_8bytes)
    if valid:
        return data, True, 0
    # Try flipping each bit in the 64-bit block
    block_arr = bytearray(block_8bytes)
    for byte_idx in range(8):
        for bit_idx in range(8):
            block_arr[byte_idx] ^= (1 << bit_idx)
            d, v = check_code_block(bytes(block_arr))
            if v:
                return d, True, 1
            block_arr[byte_idx] ^= (1 << bit_idx)  # restore
    return data, False, 0

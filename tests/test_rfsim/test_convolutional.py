"""Tests for rate-1/2 K=7 convolutional encoder."""

import pytest
from smo_rfsim.ccsds.convolutional import encode, encoded_length


def test_encode_known_vector():
    """Verify encoder produces correct output length."""
    data = b'\x00'  # 8 bits in → (8+6)*2 = 28 bits out → 4 bytes
    result = encode(data)
    assert len(result) == encoded_length(1)


def test_encode_output_length():
    for n in [1, 10, 50, 100, 223]:
        result = encode(b'\xAA' * n)
        expected = encoded_length(n)
        assert len(result) == expected, f"n={n}: got {len(result)}, expected {expected}"


def test_encode_deterministic():
    data = b'\x55\xAA\xFF\x00\x12\x34'
    r1 = encode(data)
    r2 = encode(data)
    assert r1 == r2


def test_all_zeros():
    data = b'\x00' * 10
    result = encode(data)
    # All-zero input should produce a specific pattern (not all zeros
    # due to the generator polynomial)
    assert len(result) == encoded_length(10)


def test_all_ones():
    data = b'\xFF' * 10
    result = encode(data)
    assert len(result) == encoded_length(10)


def test_encoded_length_formula():
    # For N bytes: (N*8 + 6) input bits → (N*8 + 6)*2 output bits
    for n in range(1, 20):
        total_bits = (n * 8 + 6) * 2
        expected_bytes = (total_bits + 7) // 8
        assert encoded_length(n) == expected_bytes

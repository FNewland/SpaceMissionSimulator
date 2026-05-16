"""Tests for Viterbi decoder."""

import pytest
from smo_rfsim.ccsds.convolutional import encode, encoded_length
from smo_rfsim.ccsds.viterbi import decode


def test_roundtrip_simple():
    """Encode then decode should recover original data."""
    data = b'\xAA\xBB\xCC\xDD'
    encoded = encode(data)
    decoded = decode(encoded, original_length=len(data))
    assert decoded == data


def test_roundtrip_all_zeros():
    data = b'\x00' * 10
    encoded = encode(data)
    decoded = decode(encoded, original_length=len(data))
    assert decoded == data


def test_roundtrip_all_ones():
    data = b'\xFF' * 10
    encoded = encode(data)
    decoded = decode(encoded, original_length=len(data))
    assert decoded == data


def test_roundtrip_varied_data():
    data = bytes(range(256))[:50]
    encoded = encode(data)
    decoded = decode(encoded, original_length=len(data))
    assert decoded == data


def test_single_bit_error_correction():
    """Viterbi should correct a single bit error."""
    data = b'\x12\x34\x56\x78\x9A'
    encoded = bytearray(encode(data))
    encoded[3] ^= 0x10  # flip one bit
    decoded = decode(bytes(encoded), original_length=len(data))
    assert decoded == data


def test_two_bit_errors_correction():
    """Viterbi should handle a couple of spread-out bit errors."""
    data = b'\xAA\xBB\xCC\xDD\xEE\xFF'
    encoded = bytearray(encode(data))
    encoded[1] ^= 0x04
    encoded[8] ^= 0x20  # errors spread across different symbols
    decoded = decode(bytes(encoded), original_length=len(data))
    assert decoded == data


def test_roundtrip_single_byte():
    data = b'\x42'
    encoded = encode(data)
    decoded = decode(encoded, original_length=1)
    assert decoded == data


def test_without_original_length():
    """Decode without knowing original length (strips flush bits).

    Without original_length, the decoder strips K-1=6 flush bits,
    which may not align to a byte boundary perfectly. We verify
    the data prefix matches.
    """
    data = b'\xDE\xAD\xBE\xEF'
    encoded = encode(data)
    decoded = decode(encoded)
    # The decoded output may have partial trailing byte from bit alignment
    assert decoded[:len(data)] == data


def test_deterministic():
    data = b'\x55' * 20
    encoded = encode(data)
    d1 = decode(encoded, original_length=20)
    d2 = decode(encoded, original_length=20)
    assert d1 == d2

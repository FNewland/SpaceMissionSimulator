"""Tests for RS(255,223) Reed-Solomon encoder."""

import pytest
from smo_rfsim.ccsds.reed_solomon import encode, check, strip_parity, PARITY_LENGTH, K


def test_encode_output_length():
    data = b'\x42' * 223
    codeword = encode(data)
    assert len(codeword) == 223 + PARITY_LENGTH


def test_encode_check_roundtrip():
    data = b'\xAA\xBB\xCC' * 74 + b'\xDD'  # 223 bytes
    codeword = encode(data)
    assert check(codeword)


def test_check_detects_error():
    data = b'\x01\x02\x03\x04\x05' * 44 + b'\x01\x02\x03'  # 223 bytes
    codeword = bytearray(encode(data))
    codeword[10] ^= 0xFF  # corrupt 1 byte
    assert not check(bytes(codeword))


def test_strip_parity():
    data = b'\x42' * 100
    codeword = encode(data)
    assert strip_parity(codeword) == data


def test_shortened_code():
    """RS can encode messages shorter than 223 bytes."""
    data = b'\x55' * 50
    codeword = encode(data)
    assert len(codeword) == 50 + PARITY_LENGTH
    assert check(codeword)


def test_single_byte_message():
    data = b'\xFF'
    codeword = encode(data)
    assert check(codeword)
    assert strip_parity(codeword) == data


def test_max_length_exceeded():
    with pytest.raises(ValueError):
        encode(b'\x00' * (K + 1))

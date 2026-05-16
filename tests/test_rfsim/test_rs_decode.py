"""Tests for full RS(255,223) decoder with error correction."""

import pytest
from smo_rfsim.ccsds.reed_solomon import encode, decode, check, strip_parity, T


def test_decode_no_errors():
    data = b'\x42' * 100
    codeword = encode(data)
    result = decode(codeword)
    assert result == data


def test_decode_single_symbol_error():
    data = b'\xAA' * 100
    codeword = bytearray(encode(data))
    codeword[50] ^= 0xFF  # corrupt 1 symbol
    result = decode(bytes(codeword))
    assert result is not None
    assert result == data


def test_decode_few_symbol_errors():
    data = b'\x55' * 100
    codeword = bytearray(encode(data))
    # Corrupt 3 symbols in data region
    codeword[10] ^= 0x0F
    codeword[30] ^= 0xF0
    codeword[70] ^= 0xAA
    result = decode(bytes(codeword))
    assert result is not None
    assert result == data


def test_decode_parity_symbol_error():
    data = b'\x33' * 50
    codeword = bytearray(encode(data))
    # Corrupt a parity symbol
    codeword[-5] ^= 0xFF
    result = decode(bytes(codeword))
    assert result is not None
    assert result == data


def test_decode_shortened_code():
    data = b'\xBB' * 10
    codeword = bytearray(encode(data))
    codeword[5] ^= 0x42  # corrupt 1 symbol
    result = decode(bytes(codeword))
    assert result is not None
    assert result == data


def test_decode_full_223_bytes():
    data = bytes(range(223))
    codeword = bytearray(encode(data))
    codeword[0] ^= 0xFF
    codeword[100] ^= 0xFF
    result = decode(bytes(codeword))
    assert result is not None
    assert result == data


def test_check_valid():
    data = b'\x42' * 100
    codeword = encode(data)
    assert check(codeword)


def test_check_invalid():
    data = b'\x42' * 100
    codeword = bytearray(encode(data))
    codeword[10] ^= 0xFF
    assert not check(bytes(codeword))


def test_strip_parity_roundtrip():
    data = b'\xDE\xAD' * 50
    codeword = encode(data)
    assert strip_parity(codeword) == data

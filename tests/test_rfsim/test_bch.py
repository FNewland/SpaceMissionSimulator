"""Tests for BCH(64,56) encoder/decoder."""

import pytest
from smo_rfsim.ccsds.bch import encode_code_block, check_code_block, correct_code_block


def test_encode_decode_roundtrip():
    data = b'\x01\x02\x03\x04\x05\x06\x07'
    block = encode_code_block(data)
    assert len(block) == 8
    recovered, valid = check_code_block(block)
    assert valid
    assert recovered == data


def test_all_zeros():
    data = b'\x00' * 7
    block = encode_code_block(data)
    recovered, valid = check_code_block(block)
    assert valid
    assert recovered == data


def test_all_ones():
    data = b'\xFF' * 7
    block = encode_code_block(data)
    recovered, valid = check_code_block(block)
    assert valid


def test_detect_single_bit_error():
    data = b'\xAA\xBB\xCC\xDD\xEE\xFF\x11'
    block = bytearray(encode_code_block(data))
    block[3] ^= 0x10  # flip 1 bit
    _, valid = check_code_block(bytes(block))
    assert not valid


def test_correct_single_bit_error():
    data = b'\xAA\xBB\xCC\xDD\xEE\xFF\x11'
    block = bytearray(encode_code_block(data))
    block[2] ^= 0x04  # flip 1 bit in data
    corrected, valid, corrections = correct_code_block(bytes(block))
    assert valid
    assert corrections == 1
    assert corrected == data


def test_correct_parity_bit_error():
    data = b'\x12\x34\x56\x78\x9A\xBC\xDE'
    block = bytearray(encode_code_block(data))
    block[7] ^= 0x02  # flip 1 bit in parity byte
    corrected, valid, corrections = correct_code_block(bytes(block))
    assert valid
    assert corrections == 1
    assert corrected == data


def test_uncorrectable_two_bit_error():
    data = b'\x01\x02\x03\x04\x05\x06\x07'
    block = bytearray(encode_code_block(data))
    block[0] ^= 0x01
    block[1] ^= 0x01  # 2 bit errors
    _, valid, corrections = correct_code_block(bytes(block))
    assert not valid


def test_invalid_length():
    with pytest.raises(ValueError):
        encode_code_block(b'\x00' * 6)
    with pytest.raises(ValueError):
        check_code_block(b'\x00' * 7)

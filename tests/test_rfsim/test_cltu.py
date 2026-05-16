"""Tests for TC CLTU encoder/decoder."""

import pytest
from smo_common.protocol.ecss_packet import build_tc_packet
from smo_rfsim.ccsds.tc_cltu import (
    encode_cltu, decode_cltu, START_SEQUENCE, TAIL_SEQUENCE
)


def test_encode_decode_roundtrip():
    tc = build_tc_packet(1, 8, 1, b'\x01\x02\x03')
    cltu = encode_cltu(tc)
    assert cltu[:2] == START_SEQUENCE
    assert cltu[-8:] == TAIL_SEQUENCE
    decoded = decode_cltu(cltu)
    assert decoded is not None
    # Decoded may have padding — check prefix
    assert decoded[:len(tc)] == tc


def test_minimal_packet():
    tc = build_tc_packet(1, 17, 1)  # test service, no data
    cltu = encode_cltu(tc)
    decoded = decode_cltu(cltu)
    assert decoded is not None
    assert decoded[:len(tc)] == tc


def test_bad_start_sequence():
    tc = build_tc_packet(1, 8, 1)
    cltu = bytearray(encode_cltu(tc))
    cltu[0] = 0x00  # corrupt start
    assert decode_cltu(bytes(cltu)) is None


def test_bad_tail_sequence():
    tc = build_tc_packet(1, 8, 1)
    cltu = bytearray(encode_cltu(tc))
    cltu[-1] = 0x00  # corrupt tail
    assert decode_cltu(bytes(cltu)) is None


def test_single_bit_error_corrected():
    tc = build_tc_packet(1, 6, 5, b'\xAA' * 10)
    cltu = bytearray(encode_cltu(tc))
    # Corrupt one bit in a code block (between start and tail)
    cltu[4] ^= 0x08
    decoded = decode_cltu(bytes(cltu), correct_errors=True)
    assert decoded is not None
    assert decoded[:len(tc)] == tc


def test_two_bit_error_rejected():
    tc = build_tc_packet(1, 6, 5, b'\xBB' * 10)
    cltu = bytearray(encode_cltu(tc))
    # Corrupt two bits in the same code block
    cltu[4] ^= 0x01
    cltu[5] ^= 0x01
    decoded = decode_cltu(bytes(cltu), correct_errors=True)
    assert decoded is None


def test_cltu_too_short():
    assert decode_cltu(b'\xEB\x90') is None

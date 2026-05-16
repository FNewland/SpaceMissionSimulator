"""Tests for CCSDS Attached Sync Marker."""

import pytest
from smo_rfsim.ccsds.asm import (
    ASM_BYTES, ASM_LENGTH, attach_asm, strip_asm, correlate_asm
)


def test_asm_constants():
    assert ASM_BYTES == b'\x1A\xCF\xFC\x1D'
    assert ASM_LENGTH == 4


def test_attach_strip_roundtrip():
    frame = b'\x00' * 100
    with_asm = attach_asm(frame)
    assert with_asm[:4] == ASM_BYTES
    assert len(with_asm) == 104
    stripped = strip_asm(with_asm)
    assert stripped == frame


def test_strip_asm_no_marker():
    data = b'\xFF' * 10
    assert strip_asm(data) == data


def test_correlate_exact():
    data = b'\x00\x00' + ASM_BYTES + b'\x00' * 20
    pos = correlate_asm(data)
    assert pos == 2


def test_correlate_with_bit_errors():
    # Flip 1 bit in the ASM
    corrupted = bytearray(ASM_BYTES)
    corrupted[0] ^= 0x01  # 1 bit error
    data = b'\x00\x00' + bytes(corrupted) + b'\x00' * 20
    pos = correlate_asm(data, max_bit_errors=1)
    assert pos == 2


def test_correlate_too_many_errors():
    corrupted = bytearray(ASM_BYTES)
    corrupted[0] ^= 0xFF  # 8 bit errors
    data = bytes(corrupted) + b'\x00' * 20
    pos = correlate_asm(data, max_bit_errors=3)
    assert pos == -1


def test_correlate_not_found():
    data = b'\x00' * 100
    assert correlate_asm(data) == -1


def test_correlate_at_offset():
    data = b'\xFF' * 50 + ASM_BYTES + b'\x00' * 20
    pos = correlate_asm(data, offset=40)
    assert pos == 50

"""Tests for smo_common.protocol modules."""
import pytest
from smo_common.protocol.ecss_packet import (
    PrimaryHeader, SecondaryHeader, crc16_ccitt,
    build_tm_packet, build_tc_packet, decommutate_packet,
)
from smo_common.protocol.framing import frame_packet, deframe_sync
from smo_common.protocol.pus_services import (
    VerificationData, HousekeepingData, EventData,
)


def test_crc16():
    data = b'\x00\x01\x02\x03'
    crc = crc16_ccitt(data)
    assert isinstance(crc, int)
    assert 0 <= crc <= 0xFFFF


def test_primary_header_pack_unpack():
    hdr = PrimaryHeader(apid=1, sequence_count=42, data_length=10)
    packed = hdr.pack()
    assert len(packed) == 6
    restored = PrimaryHeader.unpack(packed)
    assert restored.apid == 1
    assert restored.sequence_count == 42
    assert restored.data_length == 10


def test_build_tm_packet():
    pkt = build_tm_packet(apid=1, service=3, subtype=25,
                          data=b'\x00\x01', seq_count=1)
    assert len(pkt) > 8
    # Verify it can be decommutated
    parsed = decommutate_packet(pkt)
    assert parsed is not None
    assert parsed.secondary.service == 3
    assert parsed.secondary.subtype == 25
    assert parsed.crc_valid


def test_build_tc_packet():
    pkt = build_tc_packet(apid=1, service=8, subtype=1, data=b'\x00')
    assert len(pkt) > 8
    parsed = decommutate_packet(pkt)
    assert parsed is not None
    assert parsed.primary.packet_type == 1  # TC
    assert parsed.secondary is not None
    assert parsed.secondary.service == 8
    assert parsed.secondary.subtype == 1
    assert parsed.data_field == b'\x00'
    assert parsed.crc_valid


def test_tc_packet_minimal():
    """TC packet with no data payload still parses correctly."""
    pkt = build_tc_packet(apid=1, service=17, subtype=1, data=b'')
    parsed = decommutate_packet(pkt)
    assert parsed is not None
    assert parsed.secondary is not None
    assert parsed.secondary.service == 17
    assert parsed.secondary.subtype == 1
    assert parsed.data_field == b''


def test_tc_packet_roundtrip_all_sizes():
    """TC packets of various data sizes all parse with correct secondary header."""
    for size in (0, 1, 2, 4, 10, 50):
        data = bytes(range(size % 256)) if size > 0 else b''
        pkt = build_tc_packet(apid=1, service=8, subtype=1, data=data)
        parsed = decommutate_packet(pkt)
        assert parsed is not None, f"Failed to parse TC with {size}-byte data"
        assert parsed.secondary is not None, f"No secondary header for TC with {size}-byte data"
        assert parsed.secondary.service == 8
        assert parsed.secondary.subtype == 1
        assert parsed.data_field == data, f"Data mismatch for {size}-byte TC"


def test_framing():
    data = b'\x01\x02\x03\x04\x05'
    framed = frame_packet(data)
    assert len(framed) == len(data) + 2

    buf = bytearray(framed + framed)
    packets = deframe_sync(buf)
    assert len(packets) == 2
    assert packets[0] == data
    assert packets[1] == data


def test_verification_parse():
    import struct
    data = struct.pack('>I', (1 << 14) | 42)
    vd = VerificationData.parse(data, 1)
    assert vd.tc_seq == 42


def test_housekeeping_parse():
    import struct
    data = struct.pack('>H', 1)  # SID=1
    hk = HousekeepingData.parse(data)
    assert hk.sid == 1

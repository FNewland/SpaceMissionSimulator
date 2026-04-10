"""Tests for smo-mcs — TCManager command building and verification tracking.

Covers:
  - TCManager.__init__ defaults (apid, seq_count)
  - build_command increments sequence counter with 14-bit wrap
  - build_command returns valid ECSS TC packet (decommutable)
  - S3 builders: hk_request, hk_enable, hk_disable, hk_set_interval
  - S5 builders: event_enable, event_disable
  - S6 builders: mem_load, mem_dump, mem_check
  - S8 builder: func command with params
  - S12 builders: mon_enable, mon_disable, mon_add, mon_delete
  - S19 builders: ea_add, ea_delete, ea_enable, ea_disable
  - S20 builders: param_request, param_set
  - Verification tracking: track, accept, complete, reject
  - Sequence counter wraps at 0x3FFF
"""
import struct

import pytest

from smo_mcs.tc_manager import TCManager
from smo_common.protocol.ecss_packet import decommutate_packet, PacketType


# ── Helpers ─────────────────────────────────────────────────────────

def _decom(pkt_bytes: bytes):
    """Decommutate a TC packet and verify basic validity."""
    parsed = decommutate_packet(pkt_bytes)
    assert parsed is not None, "Failed to decommutate packet"
    assert parsed.crc_valid, "CRC check failed"
    assert parsed.primary.packet_type == PacketType.TC
    assert parsed.secondary is not None
    return parsed


# ── Init ────────────────────────────────────────────────────────────

class TestTCManagerInit:
    """Test TCManager initialization."""

    def test_default_init(self):
        tc = TCManager()
        assert tc._apid == 1
        assert tc._seq_count == 0
        assert tc._pending_verifications == {}

    def test_custom_apid(self):
        tc = TCManager(apid=42)
        assert tc._apid == 42


# ── build_command ───────────────────────────────────────────────────

class TestBuildCommand:
    """Test generic command building."""

    def test_build_command_returns_bytes(self):
        tc = TCManager()
        pkt = tc.build_command(8, 1, b"\x10")
        assert isinstance(pkt, bytes)
        assert len(pkt) > 0

    def test_build_command_increments_seq(self):
        tc = TCManager()
        tc.build_command(8, 1)
        assert tc._seq_count == 1
        tc.build_command(8, 1)
        assert tc._seq_count == 2

    def test_build_command_seq_wraps_at_14bits(self):
        import itertools
        tc = TCManager()
        # Advance counter to just before the 14-bit wrap point
        tc._seq_counter = itertools.count(0x3FFF)
        tc.build_command(8, 1)
        assert tc._seq_count == 0x3FFF
        tc.build_command(8, 1)
        assert tc._seq_count == 0  # wrapped (0x4000 & 0x3FFF == 0)

    def test_build_command_decommutable(self):
        tc = TCManager(apid=5)
        pkt = tc.build_command(8, 1, b"\x42")
        parsed = _decom(pkt)
        assert parsed.primary.apid == 5
        assert parsed.secondary.service == 8
        assert parsed.secondary.subtype == 1

    def test_build_command_with_no_data(self):
        tc = TCManager()
        pkt = tc.build_command(17, 1)  # S17 connection test
        parsed = _decom(pkt)
        assert parsed.secondary.service == 17
        assert parsed.secondary.subtype == 1


# ── S3 Housekeeping ─────────────────────────────────────────────────

class TestS3Builders:
    """Test S3 housekeeping command builders."""

    def test_s3_hk_request(self):
        tc = TCManager()
        pkt = tc.build_s3_hk_request(sid=1)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 3
        assert parsed.secondary.subtype == 27
        sid = struct.unpack('>H', parsed.data_field[:2])[0]
        assert sid == 1

    def test_s3_hk_enable(self):
        tc = TCManager()
        pkt = tc.build_s3_hk_enable(sid=2)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 3
        assert parsed.secondary.subtype == 5
        sid = struct.unpack('>H', parsed.data_field[:2])[0]
        assert sid == 2

    def test_s3_hk_disable(self):
        tc = TCManager()
        pkt = tc.build_s3_hk_disable(sid=3)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 3
        assert parsed.secondary.subtype == 6

    def test_s3_hk_set_interval(self):
        tc = TCManager()
        pkt = tc.build_s3_hk_set_interval(sid=1, interval_s=5.0)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 3
        assert parsed.secondary.subtype == 31
        sid, interval = struct.unpack('>Hf', parsed.data_field[:6])
        assert sid == 1
        assert abs(interval - 5.0) < 0.01


# ── S5 Event Reporting ──────────────────────────────────────────────

class TestS5Builders:
    """Test S5 event reporting command builders."""

    def test_s5_event_enable(self):
        tc = TCManager()
        pkt = tc.build_s5_event_enable(event_type=3)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 5
        assert parsed.secondary.subtype == 5
        assert parsed.data_field[0] == 3

    def test_s5_event_disable(self):
        tc = TCManager()
        pkt = tc.build_s5_event_disable(event_type=2)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 5
        assert parsed.secondary.subtype == 6
        assert parsed.data_field[0] == 2


# ── S6 Memory Management ───────────────────────────────────────────

class TestS6Builders:
    """Test S6 memory management command builders."""

    def test_s6_mem_load(self):
        tc = TCManager()
        pkt = tc.build_s6_mem_load(address=0x1000, data=b"\xDE\xAD")
        parsed = _decom(pkt)
        assert parsed.secondary.service == 6
        assert parsed.secondary.subtype == 2
        addr = struct.unpack('>I', parsed.data_field[:4])[0]
        assert addr == 0x1000
        assert parsed.data_field[4:6] == b"\xDE\xAD"

    def test_s6_mem_dump(self):
        tc = TCManager()
        pkt = tc.build_s6_mem_dump(address=0x2000, length=256)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 6
        assert parsed.secondary.subtype == 5
        addr, length = struct.unpack('>IH', parsed.data_field[:6])
        assert addr == 0x2000
        assert length == 256

    def test_s6_mem_check(self):
        tc = TCManager()
        pkt = tc.build_s6_mem_check(address=0x3000, length=128)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 6
        assert parsed.secondary.subtype == 9


# ── S8 Function Management ─────────────────────────────────────────

class TestS8Builder:
    """Test S8 function management command builder."""

    def test_s8_command_basic(self):
        tc = TCManager()
        pkt = tc.build_s8_command(func_id=0x10)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 8
        assert parsed.secondary.subtype == 1
        assert parsed.data_field[0] == 0x10

    def test_s8_command_with_params(self):
        tc = TCManager()
        pkt = tc.build_s8_command(func_id=0x20, params=b"\x01\x02\x03")
        parsed = _decom(pkt)
        assert parsed.data_field[0] == 0x20
        assert parsed.data_field[1:4] == b"\x01\x02\x03"


# ── S12 On-Board Monitoring ────────────────────────────────────────

class TestS12Builders:
    """Test S12 monitoring command builders."""

    def test_s12_mon_enable(self):
        tc = TCManager()
        pkt = tc.build_s12_mon_enable()
        parsed = _decom(pkt)
        assert parsed.secondary.service == 12
        assert parsed.secondary.subtype == 1

    def test_s12_mon_disable(self):
        tc = TCManager()
        pkt = tc.build_s12_mon_disable()
        parsed = _decom(pkt)
        assert parsed.secondary.service == 12
        assert parsed.secondary.subtype == 2

    def test_s12_mon_add(self):
        tc = TCManager()
        pkt = tc.build_s12_mon_add(
            param_id=100, check_type=1,
            low_limit=10.0, high_limit=90.0,
        )
        parsed = _decom(pkt)
        assert parsed.secondary.service == 12
        assert parsed.secondary.subtype == 6
        pid, ctype, low, high = struct.unpack(
            '>HBff', parsed.data_field[:11]
        )
        assert pid == 100
        assert ctype == 1
        assert abs(low - 10.0) < 0.01
        assert abs(high - 90.0) < 0.01

    def test_s12_mon_delete(self):
        tc = TCManager()
        pkt = tc.build_s12_mon_delete(param_id=200)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 12
        assert parsed.secondary.subtype == 7
        pid = struct.unpack('>H', parsed.data_field[:2])[0]
        assert pid == 200


# ── S19 Event-Action ───────────────────────────────────────────────

class TestS19Builders:
    """Test S19 event-action command builders."""

    def test_s19_ea_add(self):
        tc = TCManager()
        pkt = tc.build_s19_ea_add(ea_id=1, event_type=3, action_func_id=0x10)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 19
        assert parsed.secondary.subtype == 1
        ea_id, evt, func = struct.unpack('>HBB', parsed.data_field[:4])
        assert ea_id == 1
        assert evt == 3
        assert func == 0x10

    def test_s19_ea_delete(self):
        tc = TCManager()
        pkt = tc.build_s19_ea_delete(ea_id=5)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 19
        assert parsed.secondary.subtype == 2

    def test_s19_ea_enable(self):
        tc = TCManager()
        pkt = tc.build_s19_ea_enable(ea_id=7)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 19
        assert parsed.secondary.subtype == 4
        ea_id = struct.unpack('>H', parsed.data_field[:2])[0]
        assert ea_id == 7

    def test_s19_ea_disable(self):
        tc = TCManager()
        pkt = tc.build_s19_ea_disable(ea_id=9)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 19
        assert parsed.secondary.subtype == 5


# ── S20 Parameter Management ──────────────────────────────────────

class TestS20Builders:
    """Test S20 parameter management command builders."""

    def test_s20_param_request(self):
        tc = TCManager()
        pkt = tc.build_s20_param_request(param_id=0x0100)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 20
        assert parsed.secondary.subtype == 3
        pid = struct.unpack('>H', parsed.data_field[:2])[0]
        assert pid == 0x0100

    def test_s20_param_set(self):
        tc = TCManager()
        pkt = tc.build_s20_param_set(param_id=0x0200, value=3.14)
        parsed = _decom(pkt)
        assert parsed.secondary.service == 20
        assert parsed.secondary.subtype == 1
        pid, val = struct.unpack('>Hf', parsed.data_field[:6])
        assert pid == 0x0200
        assert abs(val - 3.14) < 0.01


# ── Verification Tracking ─────────────────────────────────────────

class TestVerificationTracking:
    """Test TC verification state machine."""

    def test_track_verification_creates_entry(self):
        tc = TCManager()
        tc.track_verification(42, "Enable Payload")
        assert 42 in tc._pending_verifications
        entry = tc._pending_verifications[42]
        assert entry["name"] == "Enable Payload"
        assert entry["accepted"] is False
        assert entry["completed"] is False

    def test_process_verification_accept(self):
        tc = TCManager()
        tc.track_verification(100, "HK Request")
        result = tc.process_verification(100, subtype=1)
        assert "accepted" in result
        assert tc._pending_verifications[100]["accepted"] is True

    def test_process_verification_complete(self):
        tc = TCManager()
        tc.track_verification(100, "HK Request")
        tc.process_verification(100, subtype=1)  # accept
        result = tc.process_verification(100, subtype=7)  # complete
        assert "completed" in result
        assert tc._pending_verifications[100]["completed"] is True

    def test_process_verification_reject(self):
        tc = TCManager()
        tc.track_verification(100, "Bad Cmd")
        result = tc.process_verification(100, subtype=2)
        assert "REJECTED" in result

    def test_process_verification_unknown_seq(self):
        tc = TCManager()
        result = tc.process_verification(999, subtype=1)
        assert result is None

    def test_process_verification_extracts_seq_from_request_id(self):
        """request_id is masked with 0x3FFF to extract sequence count."""
        tc = TCManager()
        tc.track_verification(42, "TestCmd")
        # request_id with upper bits set: 0xC000 | 42 = 0xC02A
        result = tc.process_verification(0xC02A, subtype=1)
        assert result is not None
        assert "accepted" in result

    def test_process_verification_unknown_subtype_returns_none(self):
        tc = TCManager()
        tc.track_verification(42, "TestCmd")
        result = tc.process_verification(42, subtype=99)
        assert result is None

    def test_multiple_commands_tracked_independently(self):
        tc = TCManager()
        tc.track_verification(1, "Cmd1")
        tc.track_verification(2, "Cmd2")
        tc.process_verification(1, subtype=1)
        assert tc._pending_verifications[1]["accepted"] is True
        assert tc._pending_verifications[2]["accepted"] is False

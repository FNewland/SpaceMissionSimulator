"""Tests for CCSDS TM Transfer Frame builder and parser."""

import struct
import pytest
from smo_common.protocol.ecss_packet import build_tm_packet
from smo_rfsim.ccsds.tm_frame import (
    TMFrameHeader, TMFrameBuilder, TMFrameParser,
    TF_HEADER_LENGTH, FIRST_HEADER_POINTER_NO_PACKET,
    FIRST_HEADER_POINTER_IDLE,
)


class TestTMFrameHeader:
    def test_pack_unpack_roundtrip(self):
        hdr = TMFrameHeader(scid=1, vcid=2, mc_frame_count=10,
                            vc_frame_count=5, first_header_pointer=42)
        packed = hdr.pack()
        assert len(packed) == TF_HEADER_LENGTH
        parsed = TMFrameHeader.unpack(packed)
        assert parsed.scid == 1
        assert parsed.vcid == 2
        assert parsed.mc_frame_count == 10
        assert parsed.vc_frame_count == 5
        assert parsed.first_header_pointer == 42

    def test_header_fields_range(self):
        hdr = TMFrameHeader(scid=0x3FF, vcid=7, mc_frame_count=255,
                            vc_frame_count=255, first_header_pointer=0x7FF)
        packed = hdr.pack()
        parsed = TMFrameHeader.unpack(packed)
        assert parsed.scid == 0x3FF
        assert parsed.vcid == 7
        assert parsed.first_header_pointer == 0x7FF


class TestTMFrameBuilder:
    def test_build_idle_frame(self):
        builder = TMFrameBuilder(scid=1, frame_length=100, fecf_present=True)
        frame = builder.build_idle_frame()
        assert frame.is_idle
        assert frame.header.vcid == 7
        # Data should be all 0xFE
        assert all(b == 0xFE for b in frame.data)

    def test_small_packet_produces_flush(self):
        builder = TMFrameBuilder(scid=1, frame_length=100, fecf_present=True)
        # data zone = 100 - 6 - 2 = 92 bytes
        pkt = build_tm_packet(1, 3, 25, b'\x01' * 10)
        frames = builder.add_packet(pkt, vcid=0)
        # Packet is smaller than data zone, so no complete frame yet
        assert len(frames) == 0
        # Flush to get the padded frame
        flushed = builder.flush(vcid=0)
        assert len(flushed) == 1

    def test_large_packet_spans_frames(self):
        builder = TMFrameBuilder(scid=1, frame_length=50, fecf_present=True)
        # data zone = 50 - 6 - 2 = 42 bytes
        pkt = build_tm_packet(1, 3, 25, b'\xAA' * 100)
        frames = builder.add_packet(pkt, vcid=0)
        # Should produce at least 2 frames
        assert len(frames) >= 2


class TestTMFrameParser:
    def test_parse_valid_frame(self):
        builder = TMFrameBuilder(scid=1, frame_length=100, fecf_present=True)
        frame = builder.build_idle_frame()
        raw = frame.header.pack() + frame.data + struct.pack('>H', frame.fecf)
        parser = TMFrameParser(frame_length=100, fecf_present=True)
        parsed = parser.parse_frame(raw)
        assert parsed is not None
        assert parsed.is_idle
        assert parser.good_frames == 1
        assert parser.bad_frames == 0

    def test_parse_corrupted_fecf(self):
        builder = TMFrameBuilder(scid=1, frame_length=100, fecf_present=True)
        frame = builder.build_idle_frame()
        raw = bytearray(frame.header.pack() + frame.data + struct.pack('>H', frame.fecf))
        raw[-1] ^= 0xFF  # corrupt FECF
        parser = TMFrameParser(frame_length=100, fecf_present=True)
        parsed = parser.parse_frame(bytes(raw))
        assert parsed is None
        assert parser.bad_frames == 1

    def test_extract_packets_roundtrip(self):
        builder = TMFrameBuilder(scid=1, frame_length=200, fecf_present=True)
        original = build_tm_packet(1, 3, 25, b'\x42' * 30)
        builder.add_packet(original, vcid=0)
        frames = builder.flush(vcid=0)
        assert len(frames) >= 1

        parser = TMFrameParser(frame_length=200, fecf_present=True)
        recovered = []
        for f in frames:
            raw = f.header.pack() + f.data + struct.pack('>H', f.fecf)
            parsed = parser.parse_frame(raw)
            if parsed:
                recovered.extend(parser.extract_packets(parsed))
        assert len(recovered) == 1
        assert recovered[0] == original


class TestFlushDoesNotLeakPacketOffsets:
    """Regression: TMFrameBuilder.flush() used to leave stale packet offsets.

    ``add_packet`` records where each packet starts so ``_drain_frames`` can
    compute the First Header Pointer. ``flush`` cleared the byte buffer but not
    those offsets, so every later frame inherited a stale offset and advertised
    a wrong FHP. The receiver then resynchronised at the wrong byte, emitting
    garbage packets and — when the leftover was a mid-packet continuation —
    dropping a whole frame's worth of telemetry.

    Symptom: acceptance commands intermittently getting no S1.1 ACK at all
    (test_commands.py::TestAOCSCommandsRF::test_st2_power_on).
    """

    @staticmethod
    def _batch(builder, parser, sizes):
        """Push one batch of packets through builder.flush() and recover them."""
        sent, frames = [], []
        for n in sizes:
            pkt = build_tm_packet(1, 3, 25, b'\x5A' * (n - 13))
            sent.append(pkt)
            frames.extend(builder.add_packet(pkt, vcid=0))
        frames.extend(builder.flush(vcid=0))

        recovered = []
        for f in frames:
            raw = f.header.pack() + f.data + struct.pack('>H', f.fecf)
            parsed = parser.parse_frame(raw)
            if parsed:
                recovered.extend(parser.extract_packets(parsed))
        return sent, recovered

    def test_flush_clears_recorded_offsets(self):
        builder = TMFrameBuilder(scid=1, frame_length=1115, fecf_present=True)
        builder.add_packet(build_tm_packet(1, 3, 25, b'\x00' * 40), vcid=0)
        builder.flush(vcid=0)
        assert builder._get_pkt_offsets(0) == [], (
            "flush() left packet-start offsets behind; the next frame will "
            "advertise a stale First Header Pointer")
        assert builder._get_buffer(0) == bytearray()

    def test_flushed_frame_fhp_points_at_a_real_packet_header(self):
        """A flush whose leftover is a continuation must not advertise FHP=0."""
        builder = TMFrameBuilder(scid=1, frame_length=1115, fecf_present=True)
        data_zone = builder.data_zone_length
        # One packet that overruns the data zone by a few bytes, so the flushed
        # frame starts mid-packet and contains no packet header at all.
        big = build_tm_packet(1, 3, 25, b'\x11' * (data_zone + 20 - 13))
        frames = builder.add_packet(big, vcid=0)
        frames += builder.flush(vcid=0)

        assert len(frames) == 2
        assert frames[0].header.first_header_pointer == 0
        assert frames[1].header.first_header_pointer == FIRST_HEADER_POINTER_IDLE, (
            "the continuation frame claimed a packet starts at byte 0")

    def test_repeated_batches_recover_every_packet_exactly_once(self):
        """Six add/flush cycles across a frame boundary lose and invent nothing."""
        builder = TMFrameBuilder(scid=1, frame_length=1115, fecf_present=True)
        parser = TMFrameParser(frame_length=1115, fecf_present=True)
        # Sizes chosen to overrun the 1107-byte data zone by ~30 bytes, i.e. the
        # telemetry burst an acceptance-test command produces.
        sizes = [103, 145, 70, 62, 73, 19, 17, 19, 103, 103, 103, 103, 145, 47, 26]

        for batch in range(6):
            sent, recovered = self._batch(builder, parser, sizes)
            assert recovered == sent, (
                f"batch {batch}: recovered {len(recovered)} packets, "
                f"sent {len(sent)} — FHP desynchronisation")

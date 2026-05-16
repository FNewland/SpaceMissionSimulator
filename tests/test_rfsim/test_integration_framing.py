"""Integration test: full TM framing chain round-trip.

Packet → FrameBuilder → ASM → Channel → FrameSync → FrameParser → Packet
"""

import struct
import pytest
from smo_common.protocol.ecss_packet import build_tm_packet, decommutate_packet
from smo_rfsim.ccsds.asm import attach_asm
from smo_rfsim.ccsds.tm_frame import TMFrameBuilder, TMFrameParser
from smo_rfsim.ccsds.frame_sync import FrameSynchronizer, SyncState
from smo_rfsim.channel.model import ChannelModel


FRAME_LEN = 200


def _frame_to_wire(frame) -> bytes:
    """Serialize a TMFrame to wire format with ASM."""
    raw = frame.header.pack() + frame.data
    if frame.fecf is not None:
        raw += struct.pack('>H', frame.fecf)
    return attach_asm(raw)


class TestFullFramingChain:
    def test_clean_channel_roundtrip(self):
        """Packets survive frame→channel(clean)→sync→extract roundtrip."""
        builder = TMFrameBuilder(scid=1, frame_length=FRAME_LEN, fecf_present=True)
        parser = TMFrameParser(frame_length=FRAME_LEN, fecf_present=True)
        sync = FrameSynchronizer(frame_length=FRAME_LEN, verify_count=1)

        # Build 3 different TM packets
        packets = [
            build_tm_packet(1, 3, 25, b'\x01' * 30, seq_count=i)
            for i in range(3)
        ]

        recovered = []
        for pkt in packets:
            builder.add_packet(pkt, vcid=0)

        # Flush all frames
        all_frames = builder.flush(vcid=0)
        wire = b''.join(_frame_to_wire(f) for f in all_frames)

        # Run through sync
        raw_frames = sync.feed(wire)

        for rf in raw_frames:
            parsed = parser.parse_frame(rf)
            if parsed:
                extracted = parser.extract_packets(parsed)
                recovered.extend(extracted)

        assert len(recovered) == len(packets)
        for orig, recv in zip(packets, recovered):
            assert orig == recv

    def test_noisy_channel_high_ebn0(self):
        """At 15 dB Eb/N0, packets should survive despite tiny BER."""
        builder = TMFrameBuilder(scid=1, frame_length=FRAME_LEN, fecf_present=True)
        parser = TMFrameParser(frame_length=FRAME_LEN, fecf_present=True)
        sync = FrameSynchronizer(frame_length=FRAME_LEN, verify_count=1)
        channel = ChannelModel(eb_n0_db=15.0, seed=42)

        pkt = build_tm_packet(1, 3, 25, b'\xAA' * 50)
        builder.add_packet(pkt, vcid=0)
        frames = builder.flush(vcid=0)
        wire = b''.join(_frame_to_wire(f) for f in frames)

        # Apply channel
        impaired = channel.impair(wire)

        raw_frames = sync.feed(impaired)
        recovered = []
        for rf in raw_frames:
            parsed = parser.parse_frame(rf)
            if parsed:
                recovered.extend(parser.extract_packets(parsed))

        # At 15 dB the BER is ~3.6e-12, so we expect zero errors
        assert len(recovered) == 1
        assert recovered[0] == pkt

    def test_noisy_channel_low_ebn0_drops_frames(self):
        """At 3 dB Eb/N0, frames should be corrupted and FECF fails."""
        builder = TMFrameBuilder(scid=1, frame_length=FRAME_LEN, fecf_present=True)
        parser = TMFrameParser(frame_length=FRAME_LEN, fecf_present=True)
        sync = FrameSynchronizer(frame_length=FRAME_LEN, verify_count=1)
        channel = ChannelModel(eb_n0_db=3.0, seed=42)

        pkt = build_tm_packet(1, 3, 25, b'\xBB' * 50)
        builder.add_packet(pkt, vcid=0)
        frames = builder.flush(vcid=0)
        wire = b''.join(_frame_to_wire(f) for f in frames)

        impaired = channel.impair(wire)
        raw_frames = sync.feed(impaired)

        bad_count = 0
        for rf in raw_frames:
            parsed = parser.parse_frame(rf)
            if parsed is None:
                bad_count += 1

        # At 3 dB BER~0.023, most frames will have errors
        assert bad_count > 0 or parser.bad_frames > 0


class TestCLTURoundtrip:
    def test_tc_cltu_clean(self):
        """TC packet survives CLTU encode/decode on clean channel."""
        from smo_rfsim.ccsds.tc_cltu import encode_cltu, decode_cltu
        from smo_common.protocol.ecss_packet import build_tc_packet

        tc = build_tc_packet(1, 8, 1, b'\x42' * 20)
        cltu = encode_cltu(tc)
        decoded = decode_cltu(cltu)
        assert decoded is not None
        assert decoded[:len(tc)] == tc

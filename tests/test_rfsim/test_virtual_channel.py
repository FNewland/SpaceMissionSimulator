"""Tests for Virtual Channel multiplexer/demultiplexer."""

import pytest
from smo_common.protocol.ecss_packet import build_tm_packet
from smo_rfsim.ccsds.virtual_channel import VCMultiplexer, VCDemultiplexer


FRAME_LEN = 200


class TestVCMultiplexer:
    def test_idle_when_empty(self):
        mux = VCMultiplexer(scid=1, frame_length=FRAME_LEN)
        assert not mux.has_data()
        frame = mux.get_next_frame()
        assert frame.header.vcid == 7  # idle
        assert frame.is_idle

    def test_priority_vc0_over_vc1(self):
        mux = VCMultiplexer(scid=1, frame_length=FRAME_LEN)
        # Flush both VCs so pending queues have frames
        pkt1 = build_tm_packet(1, 15, 1, b'\x00' * 50)
        pkt0 = build_tm_packet(1, 3, 25, b'\x00' * 50)
        mux.add_packet(pkt1, vcid=1)
        mux.add_packet(pkt0, vcid=0)
        # Flush to create pending frames for both VCs
        flushed = mux.flush_all()
        # Re-queue the flushed frames manually via the pending deques
        for f in flushed:
            mux._pending[f.header.vcid].append(f)
        # VC0 should come first (lower VCID = higher priority)
        frame = mux.get_next_frame()
        assert frame.header.vcid == 0

    def test_flush_all(self):
        mux = VCMultiplexer(scid=1, frame_length=FRAME_LEN)
        pkt = build_tm_packet(1, 3, 25, b'\x11' * 30)
        mux.add_packet(pkt, vcid=0)
        mux.add_packet(pkt, vcid=1)
        flushed = mux.flush_all()
        assert len(flushed) >= 2  # at least one per VC


class TestVCDemultiplexer:
    def test_route_by_vcid(self):
        mux = VCMultiplexer(scid=1, frame_length=FRAME_LEN)
        demux = VCDemultiplexer()

        pkt = build_tm_packet(1, 3, 25, b'\x00' * 50)
        mux.add_packet(pkt, vcid=0)
        # Flush to produce frames
        flushed = mux.flush_all()
        for frame in flushed:
            vcid = demux.process_frame(frame)
            assert vcid == 0

        assert demux.frame_counts.get(0, 0) >= 1

    def test_idle_frames_counted(self):
        mux = VCMultiplexer(scid=1, frame_length=FRAME_LEN)
        demux = VCDemultiplexer()

        idle = mux.get_next_frame()
        vcid = demux.process_frame(idle)
        assert vcid == 7
        assert demux.frame_counts[7] == 1

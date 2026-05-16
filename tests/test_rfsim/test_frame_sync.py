"""Tests for TM Frame Synchronizer."""

import struct
import pytest
from smo_rfsim.ccsds.asm import attach_asm, ASM_BYTES
from smo_rfsim.ccsds.tm_frame import TMFrameBuilder
from smo_rfsim.ccsds.frame_sync import FrameSynchronizer, SyncState


FRAME_LEN = 100  # small frame for testing


def _make_raw_frame(builder: TMFrameBuilder) -> bytes:
    """Build a raw frame with ASM prepended."""
    frame = builder.build_idle_frame()
    raw = frame.header.pack() + frame.data
    if frame.fecf is not None:
        raw += struct.pack('>H', frame.fecf)
    return attach_asm(raw)


class TestFrameSynchronizer:
    def test_initial_state(self):
        fs = FrameSynchronizer(frame_length=FRAME_LEN)
        assert fs.state == SyncState.SEARCH

    def test_single_frame_extraction(self):
        builder = TMFrameBuilder(scid=1, frame_length=FRAME_LEN, fecf_present=True)
        fs = FrameSynchronizer(frame_length=FRAME_LEN, verify_count=1)

        raw = _make_raw_frame(builder)
        frames = fs.feed(raw)
        assert len(frames) == 1
        assert len(frames[0]) == FRAME_LEN

    def test_multiple_frames_lock(self):
        builder = TMFrameBuilder(scid=1, frame_length=FRAME_LEN, fecf_present=True)
        fs = FrameSynchronizer(frame_length=FRAME_LEN, verify_count=2)

        # Feed 5 consecutive frames
        stream = b''
        for _ in range(5):
            stream += _make_raw_frame(builder)

        frames = fs.feed(stream)
        assert len(frames) >= 3  # at least some frames extracted
        assert fs.state == SyncState.LOCK

    def test_garbage_prefix_ignored(self):
        builder = TMFrameBuilder(scid=1, frame_length=FRAME_LEN, fecf_present=True)
        fs = FrameSynchronizer(frame_length=FRAME_LEN, verify_count=1)

        garbage = b'\xDE\xAD' * 50
        raw = _make_raw_frame(builder)
        frames = fs.feed(garbage + raw)
        assert len(frames) == 1

    def test_incremental_feed(self):
        builder = TMFrameBuilder(scid=1, frame_length=FRAME_LEN, fecf_present=True)
        fs = FrameSynchronizer(frame_length=FRAME_LEN, verify_count=1)

        raw = _make_raw_frame(builder)
        # Feed byte by byte
        all_frames = []
        for byte in raw:
            result = fs.feed(bytes([byte]))
            all_frames.extend(result)
        assert len(all_frames) == 1

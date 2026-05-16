"""Virtual Channel multiplexer/demultiplexer for TM Transfer Frames.

Supports priority-based multiplexing: VC0 (realtime HK) > VC1 (playback) > VC7 (idle).

Reference: CCSDS 132.0-B-3
"""

import logging
from collections import deque
from typing import Optional

from .tm_frame import TMFrame, TMFrameBuilder

logger = logging.getLogger(__name__)


class VCMultiplexer:
    """Multiplexes packets from multiple virtual channels into a single
    output stream of TM Transfer Frames.

    Priority order: lower VCID = higher priority. VC7 is always idle fill.
    """

    def __init__(self, scid: int = 1, frame_length: int = 1115,
                 fecf_present: bool = True,
                 vc_ids: tuple[int, ...] = (0, 1, 7)):
        self.vc_ids = vc_ids
        self._builders: dict[int, TMFrameBuilder] = {}
        self._pending: dict[int, deque[TMFrame]] = {}
        for vcid in vc_ids:
            self._builders[vcid] = TMFrameBuilder(
                scid=scid, frame_length=frame_length, fecf_present=fecf_present)
            self._pending[vcid] = deque()

    def add_packet(self, packet: bytes, vcid: int = 0) -> None:
        """Enqueue an ECSS packet into a virtual channel."""
        if vcid not in self._builders:
            logger.warning("Unknown VCID %d, dropping packet", vcid)
            return
        frames = self._builders[vcid].add_packet(packet, vcid)
        for f in frames:
            self._pending[vcid].append(f)

    def get_next_frame(self) -> TMFrame:
        """Get the next frame to transmit, respecting VC priority.

        Returns idle frame (VC7) if no data is pending.
        """
        for vcid in self.vc_ids:
            if vcid == 7:
                continue  # idle channel handled below
            if self._pending[vcid]:
                return self._pending[vcid].popleft()
        # No data pending — send idle frame
        if 7 in self._builders:
            return self._builders[7].build_idle_frame()
        return self._builders[self.vc_ids[0]].build_idle_frame(vcid=7)

    def has_data(self) -> bool:
        """Check if any non-idle VC has pending frames."""
        return any(self._pending[vcid] for vcid in self.vc_ids if vcid != 7)

    def flush_all(self) -> list[TMFrame]:
        """Flush all VC buffers and return remaining frames."""
        frames = []
        for vcid in self.vc_ids:
            if vcid == 7:
                continue
            flushed = self._builders[vcid].flush(vcid)
            frames.extend(flushed)
        return frames


class VCDemultiplexer:
    """Demultiplexes TM frames by virtual channel ID."""

    def __init__(self):
        self._vc_frames: dict[int, list[TMFrame]] = {}
        self.frame_counts: dict[int, int] = {}

    def process_frame(self, frame: TMFrame) -> int:
        """Route a frame to its VC and return the VCID."""
        vcid = frame.header.vcid
        if vcid not in self._vc_frames:
            self._vc_frames[vcid] = []
            self.frame_counts[vcid] = 0
        self._vc_frames[vcid].append(frame)
        self.frame_counts[vcid] = self.frame_counts.get(vcid, 0) + 1
        return vcid

    def get_frames(self, vcid: int) -> list[TMFrame]:
        """Retrieve and clear accumulated frames for a VC."""
        frames = self._vc_frames.pop(vcid, [])
        return frames

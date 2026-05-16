"""CCSDS TM Transfer Frame builder and parser.

Reference: CCSDS 132.0-B-3 (TM Space Data Link Protocol)

Frame structure (no ASM, no coding):
  [Primary Header (6 bytes)]
  [Data Field (variable)]
  [FECF (2 bytes, optional)]

Primary Header fields:
  - Transfer Frame Version (2 bits) = 0
  - Spacecraft ID (10 bits)
  - Virtual Channel ID (3 bits)
  - OCF Flag (1 bit)
  - Master Channel Frame Count (8 bits)
  - Virtual Channel Frame Count (8 bits)
  - TF Data Field Status (16 bits):
      - Secondary Header Flag (1 bit)
      - Synch Flag (1 bit)
      - Packet Order Flag (1 bit)
      - Segment Length ID (2 bits)
      - First Header Pointer (11 bits)
"""

import struct
import logging
from dataclasses import dataclass
from typing import Optional

from smo_common.protocol.ecss_packet import crc16_ccitt

logger = logging.getLogger(__name__)

# Frame constants
TF_HEADER_LENGTH = 6
FECF_LENGTH = 2
FIRST_HEADER_POINTER_IDLE = 0x7FE  # no packet starts in this frame
FIRST_HEADER_POINTER_NO_PACKET = 0x7FF  # only idle data


@dataclass
class TMFrameHeader:
    """TM Transfer Frame Primary Header."""
    version: int = 0
    scid: int = 1
    vcid: int = 0
    ocf_flag: int = 0
    mc_frame_count: int = 0
    vc_frame_count: int = 0
    sec_hdr_flag: int = 0
    synch_flag: int = 0
    packet_order: int = 0
    segment_length_id: int = 3  # 3 = unsegmented
    first_header_pointer: int = 0

    def pack(self) -> bytes:
        """Encode the 6-byte primary header."""
        word0 = ((self.version & 0x3) << 14) | ((self.scid & 0x3FF) << 4) | \
                ((self.vcid & 0x7) << 1) | (self.ocf_flag & 0x1)
        word2 = ((self.sec_hdr_flag & 0x1) << 15) | \
                ((self.synch_flag & 0x1) << 14) | \
                ((self.packet_order & 0x1) << 13) | \
                ((self.segment_length_id & 0x3) << 11) | \
                (self.first_header_pointer & 0x7FF)
        return struct.pack('>HBBH', word0, self.mc_frame_count & 0xFF,
                           self.vc_frame_count & 0xFF, word2)

    @classmethod
    def unpack(cls, data: bytes) -> "TMFrameHeader":
        """Decode a 6-byte primary header."""
        if len(data) < TF_HEADER_LENGTH:
            raise ValueError(f"Need {TF_HEADER_LENGTH} bytes, got {len(data)}")
        word0, mc_count, vc_count, word2 = struct.unpack('>HBBH', data[:6])
        return cls(
            version=(word0 >> 14) & 0x3,
            scid=(word0 >> 4) & 0x3FF,
            vcid=(word0 >> 1) & 0x7,
            ocf_flag=word0 & 0x1,
            mc_frame_count=mc_count,
            vc_frame_count=vc_count,
            sec_hdr_flag=(word2 >> 15) & 0x1,
            synch_flag=(word2 >> 14) & 0x1,
            packet_order=(word2 >> 13) & 0x1,
            segment_length_id=(word2 >> 11) & 0x3,
            first_header_pointer=word2 & 0x7FF,
        )


@dataclass
class TMFrame:
    """A complete TM Transfer Frame (without ASM)."""
    header: TMFrameHeader
    data: bytes
    fecf: Optional[int] = None  # 16-bit Frame Error Control Field

    @property
    def is_idle(self) -> bool:
        return self.header.first_header_pointer == FIRST_HEADER_POINTER_NO_PACKET


class TMFrameBuilder:
    """Builds TM Transfer Frames from ECSS packets.

    Packs packets into fixed-length frames, handling continuation
    across frame boundaries via the First Header Pointer.
    """

    def __init__(self, scid: int = 1, frame_length: int = 1115,
                 fecf_present: bool = True):
        self.scid = scid
        self.frame_length = frame_length
        self.fecf_present = fecf_present
        # Data zone = total - ASM(4) - header(6) - FECF(2 if present)
        # But frame_length here is without ASM
        fecf_size = FECF_LENGTH if fecf_present else 0
        self.data_zone_length = frame_length - TF_HEADER_LENGTH - fecf_size
        self._mc_count = 0
        self._vc_counts: dict[int, int] = {}
        self._vc_buffers: dict[int, bytearray] = {}
        self._pkt_offsets: dict[int, list[int]] = {}

    def _next_mc_count(self) -> int:
        c = self._mc_count
        self._mc_count = (self._mc_count + 1) & 0xFF
        return c

    def _next_vc_count(self, vcid: int) -> int:
        c = self._vc_counts.get(vcid, 0)
        self._vc_counts[vcid] = (c + 1) & 0xFF
        return c

    def _get_buffer(self, vcid: int) -> bytearray:
        if vcid not in self._vc_buffers:
            self._vc_buffers[vcid] = bytearray()
        return self._vc_buffers[vcid]

    def _get_pkt_offsets(self, vcid: int) -> list:
        if vcid not in self._pkt_offsets:
            self._pkt_offsets[vcid] = []
        return self._pkt_offsets[vcid]

    def add_packet(self, packet: bytes, vcid: int = 0) -> list[TMFrame]:
        """Add an ECSS packet and return any complete frames produced."""
        buf = self._get_buffer(vcid)
        offsets = self._get_pkt_offsets(vcid)
        offsets.append(len(buf))  # record where this packet starts
        buf.extend(packet)
        return self._drain_frames(vcid)

    def flush(self, vcid: int = 0) -> list[TMFrame]:
        """Flush remaining data as a padded frame (idle fill)."""
        buf = self._get_buffer(vcid)
        if not buf:
            return []
        frames = self._drain_frames(vcid)
        # If there's still leftover data, pad to fill a frame
        if buf:
            remaining = bytes(buf)
            buf.clear()
            pad_len = self.data_zone_length - len(remaining)
            data_zone = remaining + (b'\xFE' * pad_len)
            # FHP = 0: a packet starts at the beginning of this data zone
            fhp = 0
            frame = self._build_frame(vcid, data_zone, fhp)
            frames.append(frame)
        return frames

    def build_idle_frame(self, vcid: int = 7) -> TMFrame:
        """Build an idle fill frame (all 0xFE in data zone)."""
        data_zone = b'\xFE' * self.data_zone_length
        return self._build_frame(vcid, data_zone, FIRST_HEADER_POINTER_NO_PACKET)

    def _drain_frames(self, vcid: int) -> list[TMFrame]:
        """Extract as many complete frames as possible from the VC buffer.

        Uses the packet-start offsets recorded by add_packet() to compute
        the correct First Header Pointer (FHP) for each frame. The FHP
        tells the receiver where the first new packet header starts,
        allowing resynchronisation after frame loss.
        """
        buf = self._get_buffer(vcid)
        offsets = self._get_pkt_offsets(vcid)
        frames = []
        consumed = 0  # total bytes consumed from buffer so far
        while len(buf) >= self.data_zone_length:
            chunk = bytes(buf[:self.data_zone_length])
            del buf[:self.data_zone_length]
            # Find the first packet-start offset that falls within this chunk
            chunk_start = consumed
            chunk_end = consumed + self.data_zone_length
            fhp = FIRST_HEADER_POINTER_IDLE
            for off in offsets:
                if chunk_start <= off < chunk_end:
                    fhp = off - chunk_start
                    break
            # Remove offsets we've passed
            while offsets and offsets[0] < chunk_end:
                offsets.pop(0)
            frame = self._build_frame(vcid, chunk, fhp)
            frames.append(frame)
            consumed = chunk_end
        # Adjust remaining offsets relative to new buffer start
        for i in range(len(offsets)):
            offsets[i] -= consumed
        return frames

    def _build_frame(self, vcid: int, data_zone: bytes,
                     first_header_pointer: int) -> TMFrame:
        """Assemble a single TM frame."""
        header = TMFrameHeader(
            scid=self.scid,
            vcid=vcid,
            mc_frame_count=self._next_mc_count(),
            vc_frame_count=self._next_vc_count(vcid),
            first_header_pointer=first_header_pointer,
        )
        raw = header.pack() + data_zone
        fecf = None
        if self.fecf_present:
            fecf = crc16_ccitt(raw)
            raw += struct.pack('>H', fecf)
        return TMFrame(header=header, data=data_zone, fecf=fecf)


class TMFrameParser:
    """Parses TM Transfer Frames back into ECSS packets."""

    def __init__(self, frame_length: int = 1115, fecf_present: bool = True):
        self.frame_length = frame_length
        self.fecf_present = fecf_present
        fecf_size = FECF_LENGTH if fecf_present else 0
        self.data_zone_length = frame_length - TF_HEADER_LENGTH - fecf_size
        self._reassembly_buffer = bytearray()
        self.good_frames = 0
        self.bad_frames = 0

    def parse_frame(self, frame_bytes: bytes) -> Optional[TMFrame]:
        """Parse raw frame bytes (without ASM) and validate FECF."""
        if len(frame_bytes) < TF_HEADER_LENGTH:
            self.bad_frames += 1
            return None
        header = TMFrameHeader.unpack(frame_bytes[:TF_HEADER_LENGTH])
        if self.fecf_present:
            body = frame_bytes[:-FECF_LENGTH]
            fecf_received = struct.unpack('>H', frame_bytes[-FECF_LENGTH:])[0]
            fecf_computed = crc16_ccitt(body)
            if fecf_received != fecf_computed:
                self.bad_frames += 1
                logger.debug("Frame FECF mismatch: recv=%04X calc=%04X",
                             fecf_received, fecf_computed)
                return None
            data = frame_bytes[TF_HEADER_LENGTH:-FECF_LENGTH]
            fecf = fecf_received
        else:
            data = frame_bytes[TF_HEADER_LENGTH:]
            fecf = None
        self.good_frames += 1
        return TMFrame(header=header, data=data, fecf=fecf)

    def extract_packets(self, frame: TMFrame) -> list[bytes]:
        """Extract ECSS packets from a parsed TM frame.

        Uses the First Header Pointer (FHP) from the frame header to
        resynchronise packet extraction after any disruption (frame loss,
        byte-alignment slip, FEC failure). Without FHP-based resync, a
        single lost frame permanently misaligns the reassembly buffer
        and causes all subsequent packets to be lost.
        """
        if frame.is_idle:
            return []

        fhp = frame.header.first_header_pointer

        if fhp == FIRST_HEADER_POINTER_IDLE:
            # No new packet starts in this frame — all data is
            # continuation of a spanning packet from the previous frame.
            self._reassembly_buffer.extend(frame.data)
        elif fhp == 0:
            # A packet starts at byte 0. If the reassembly buffer has
            # a partial packet that we were building, try to extract it
            # first (it won't succeed since we don't have its tail, but
            # _extract_from_buffer handles that gracefully). Then start
            # fresh with this frame's data.
            if self._reassembly_buffer:
                # Check if the partial data could form a valid packet
                # (i.e., we have a valid header with a length we're
                # waiting for). If not, it's orphaned — discard it.
                if len(self._reassembly_buffer) >= 6:
                    dl = struct.unpack('>H', self._reassembly_buffer[4:6])[0]
                    needed = 6 + dl + 1
                    if needed > 4096 or needed <= len(self._reassembly_buffer):
                        # Corrupted or already complete — discard
                        self._reassembly_buffer.clear()
                    # else: valid partial, but we lost its tail — discard
                    else:
                        self._reassembly_buffer.clear()
                else:
                    self._reassembly_buffer.clear()
            self._reassembly_buffer.extend(frame.data)
        else:
            # FHP > 0: data before FHP completes a spanning packet,
            # data from FHP onwards starts a new packet.
            self._reassembly_buffer.extend(frame.data[:fhp])
            packets_pre = self._extract_from_buffer()
            self._reassembly_buffer = bytearray(frame.data[fhp:])
            packets_post = self._extract_from_buffer()
            return packets_pre + packets_post

        return self._extract_from_buffer()

    def _extract_from_buffer(self) -> list[bytes]:
        """Extract complete CCSDS packets from the reassembly buffer."""
        # Cap reassembly buffer to prevent unbounded growth from corrupted data
        if len(self._reassembly_buffer) > self.data_zone_length * 5:
            logger.warning("Reassembly buffer overflow (%d bytes), clearing",
                           len(self._reassembly_buffer))
            self._reassembly_buffer.clear()
            return []
        packets = []
        while len(self._reassembly_buffer) >= 7:
            data_length = struct.unpack('>H', self._reassembly_buffer[4:6])[0]
            total_pkt_len = 6 + data_length + 1
            if total_pkt_len > 4096:
                # Corrupted length — discard one byte and retry
                self._reassembly_buffer.pop(0)
                continue
            if len(self._reassembly_buffer) < total_pkt_len:
                break
            pkt = bytes(self._reassembly_buffer[:total_pkt_len])
            del self._reassembly_buffer[:total_pkt_len]
            # Skip idle fill bytes (0xFE)
            while self._reassembly_buffer and self._reassembly_buffer[0] == 0xFE:
                self._reassembly_buffer.pop(0)
            packets.append(pkt)
        return packets

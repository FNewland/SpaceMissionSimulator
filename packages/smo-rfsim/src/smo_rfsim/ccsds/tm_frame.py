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

    def add_packet(self, packet: bytes, vcid: int = 0) -> list[TMFrame]:
        """Add an ECSS packet and return any complete frames produced."""
        buf = self._get_buffer(vcid)
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

        Computes the correct First Header Pointer (FHP) for each frame
        by scanning the data zone for CCSDS packet boundaries. The FHP
        tells the receiver where the first packet header starts, allowing
        it to resynchronise after frame loss.
        """
        buf = self._get_buffer(vcid)
        frames = []
        while len(buf) >= self.data_zone_length:
            chunk = bytes(buf[:self.data_zone_length])
            del buf[:self.data_zone_length]
            # Compute FHP: find the first CCSDS packet header in this chunk.
            # Walk through the chunk using packet lengths to find boundaries.
            fhp = FIRST_HEADER_POINTER_IDLE  # assume no new packet starts
            offset = 0
            # If this is the first frame or follows a frame where we know
            # the alignment, byte 0 is a packet start.
            # Track packet boundaries by reading length fields.
            while offset + 6 <= len(chunk):
                pkt_data_len = struct.unpack('>H', chunk[offset+4:offset+6])[0]
                pkt_total = 6 + pkt_data_len + 1
                if pkt_total > 65535 or pkt_total < 7:
                    break  # not a valid packet header
                if fhp == FIRST_HEADER_POINTER_IDLE:
                    fhp = offset  # first packet header found
                next_offset = offset + pkt_total
                if next_offset > len(chunk):
                    break  # packet spans into next frame
                offset = next_offset
                # Skip idle fill
                while offset < len(chunk) and chunk[offset] == 0xFE:
                    offset += 1
            if fhp == FIRST_HEADER_POINTER_IDLE:
                fhp = 0  # fallback: assume packet starts at byte 0
            frame = self._build_frame(vcid, chunk, fhp)
            frames.append(frame)
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

        # If FHP indicates no packet starts in this frame, just append
        # the data (it's a continuation of a spanning packet).
        if fhp == FIRST_HEADER_POINTER_IDLE:
            self._reassembly_buffer.extend(frame.data)
        else:
            # FHP points to the first packet header in this frame's data.
            # Any data before FHP is the tail of a spanning packet from
            # the previous frame — append it to complete that packet.
            if fhp > 0:
                self._reassembly_buffer.extend(frame.data[:fhp])
            else:
                # FHP == 0: packet starts at byte 0, no continuation data.
                # If the reassembly buffer has orphaned partial data from
                # a lost frame, discard it — we can't recover that packet.
                if self._reassembly_buffer:
                    logger.debug("FHP resync: discarding %d orphaned bytes",
                                 len(self._reassembly_buffer))
                    self._reassembly_buffer.clear()

            # Now extract the completed spanning packet (if any) before
            # switching to the new data starting at FHP.
            packets_pre = self._extract_from_buffer()

            # Replace buffer with data from FHP onwards (fresh alignment)
            self._reassembly_buffer = bytearray(frame.data[fhp:])
            packets_post = self._extract_from_buffer()
            return packets_pre + packets_post

        return self._extract_from_buffer()

    def _extract_from_buffer(self) -> list[bytes]:
        """Extract complete CCSDS packets from the reassembly buffer."""
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

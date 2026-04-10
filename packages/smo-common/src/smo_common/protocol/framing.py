"""SMO Common — TCP Length-Prefixed Framing.

All TM/TC traffic over TCP uses a 2-byte big-endian length prefix
followed by the raw ECSS/CCSDS packet bytes.
"""
import asyncio
import struct
import logging
from typing import Optional

logger = logging.getLogger(__name__)

FRAME_HEADER_SIZE = 2  # 2-byte big-endian length prefix
MAX_PACKET_SIZE = 65535


def frame_packet(packet: bytes) -> bytes:
    """Wrap a packet with a 2-byte big-endian length prefix."""
    length = len(packet)
    if length > MAX_PACKET_SIZE:
        raise ValueError(f"Packet too large: {length} > {MAX_PACKET_SIZE}")
    return struct.pack('>H', length) + packet


async def read_framed_packet(reader: asyncio.StreamReader) -> Optional[bytes]:
    """Read a single length-prefixed packet from an asyncio stream."""
    try:
        header = await reader.readexactly(FRAME_HEADER_SIZE)
        length = struct.unpack('>H', header)[0]
        if length == 0 or length > MAX_PACKET_SIZE:
            logger.warning("Invalid frame length: %d", length)
            return None
        data = await reader.readexactly(length)
        return data
    except asyncio.IncompleteReadError:
        return None
    except Exception as e:
        logger.debug("Frame read error: %s", e)
        return None


def deframe_sync(buffer: bytearray) -> list[bytes]:
    """
    Extract complete framed packets from a byte buffer (synchronous).
    Returns list of extracted packets and modifies buffer in-place.
    """
    packets = []
    while len(buffer) >= FRAME_HEADER_SIZE:
        length = struct.unpack('>H', buffer[:FRAME_HEADER_SIZE])[0]
        if length == 0 or length > MAX_PACKET_SIZE:
            # Invalid frame — skip 1 byte and try to resync
            buffer.pop(0)
            continue
        total = FRAME_HEADER_SIZE + length
        if len(buffer) < total:
            break  # incomplete packet, wait for more data
        packets.append(bytes(buffer[FRAME_HEADER_SIZE:total]))
        del buffer[:total]
    return packets

"""SMO Gateway — Upstream Connection Management."""
import asyncio
import logging
from typing import Optional

from smo_common.protocol.framing import frame_packet, read_framed_packet

logger = logging.getLogger(__name__)


class UpstreamConnection:
    """Manages the connection to the simulator or real spacecraft GSE."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False

    async def connect(self) -> bool:
        try:
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
            self._connected = True
            logger.info("Upstream connected to %s:%d", self.host, self.port)
            return True
        except Exception as e:
            logger.error("Upstream connection failed: %s", e)
            return False

    async def read_packet(self) -> Optional[bytes]:
        if not self._connected or not self._reader:
            return None
        return await read_framed_packet(self._reader)

    async def send_packet(self, packet: bytes) -> bool:
        if not self._connected or not self._writer:
            return False
        try:
            self._writer.write(frame_packet(packet))
            await self._writer.drain()
            return True
        except Exception as e:
            logger.warning("Upstream send error: %s", e)
            self._connected = False
            return False

    async def close(self) -> None:
        self._connected = False
        if self._writer:
            self._writer.close()

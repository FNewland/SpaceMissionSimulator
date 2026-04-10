"""SMO Gateway — Downstream Multi-Client Management."""
import asyncio
import logging
from typing import Optional

from smo_common.protocol.framing import frame_packet, read_framed_packet

logger = logging.getLogger(__name__)


class DownstreamManager:
    """Manages multiple downstream MCS client connections."""

    def __init__(self):
        self._clients: list[asyncio.StreamWriter] = []
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def add_client(self, writer: asyncio.StreamWriter) -> None:
        async with self._lock:
            self._clients.append(writer)

    async def remove_client(self, writer: asyncio.StreamWriter) -> None:
        async with self._lock:
            if writer in self._clients:
                self._clients.remove(writer)
                writer.close()

    async def broadcast_tm(self, packet: bytes) -> None:
        """Send a TM packet to all connected downstream clients."""
        frame = frame_packet(packet)
        async with self._lock:
            clients = list(self._clients)
        disconnected = []
        for writer in clients:
            try:
                writer.write(frame)
                await writer.drain()
            except Exception:
                disconnected.append(writer)
        if disconnected:
            async with self._lock:
                for w in disconnected:
                    if w in self._clients:
                        self._clients.remove(w)
                        w.close()

    async def close_all(self) -> None:
        for w in list(self._clients):
            try:
                w.close()
            except Exception:
                pass
        self._clients.clear()

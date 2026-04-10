"""SMO Gateway — Bidirectional TM/TC Relay.

Connects upstream to a simulator (or real spacecraft GSE) and
fans out TM to multiple downstream MCS clients. Routes TC upstream.
"""
import asyncio
import logging
import argparse
from typing import Optional

from smo_common.protocol.framing import frame_packet, read_framed_packet

logger = logging.getLogger(__name__)


class Gateway:
    """Transparent TM/TC relay gateway."""

    def __init__(self, upstream_host: str, upstream_port: int,
                 listen_host: str = "0.0.0.0", listen_port: int = 10025):
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.listen_host = listen_host
        self.listen_port = listen_port
        self._upstream_reader: Optional[asyncio.StreamReader] = None
        self._upstream_writer: Optional[asyncio.StreamWriter] = None
        self._downstream_clients: list[asyncio.StreamWriter] = []
        self._clients_lock = asyncio.Lock()
        self._running = False

    async def start(self) -> None:
        self._running = True
        # Connect upstream
        logger.info("Connecting upstream to %s:%d", self.upstream_host, self.upstream_port)
        self._upstream_reader, self._upstream_writer = await asyncio.open_connection(
            self.upstream_host, self.upstream_port)
        logger.info("Upstream connected")

        # Start downstream server
        server = await asyncio.start_server(
            self._handle_downstream_client, self.listen_host, self.listen_port)
        logger.info("Gateway listening on %s:%d", self.listen_host, self.listen_port)

        # Run TM relay
        async with server:
            await asyncio.gather(
                server.serve_forever(),
                self._relay_tm_downstream(),
            )

    async def _handle_downstream_client(self, reader: asyncio.StreamReader,
                                         writer: asyncio.StreamWriter) -> None:
        """Handle a new downstream MCS connection."""
        addr = writer.get_extra_info('peername')
        logger.info("Downstream client connected: %s", addr)
        async with self._clients_lock:
            self._downstream_clients.append(writer)
        try:
            # Relay TC from this client upstream
            while self._running:
                packet = await read_framed_packet(reader)
                if packet is None:
                    break
                # Forward TC upstream
                if self._upstream_writer:
                    self._upstream_writer.write(frame_packet(packet))
                    await self._upstream_writer.drain()
        except Exception as e:
            logger.debug("Downstream client error: %s", e)
        finally:
            async with self._clients_lock:
                self._downstream_clients.remove(writer)
            writer.close()
            logger.info("Downstream client disconnected: %s", addr)

    async def _relay_tm_downstream(self) -> None:
        """Read TM from upstream and fan out to all downstream clients."""
        while self._running:
            try:
                packet = await read_framed_packet(self._upstream_reader)
                if packet is None:
                    logger.warning("Upstream connection lost")
                    break
                # Broadcast to all downstream clients
                frame = frame_packet(packet)
                async with self._clients_lock:
                    clients = list(self._downstream_clients)
                disconnected = []
                for writer in clients:
                    try:
                        writer.write(frame)
                        await writer.drain()
                    except Exception:
                        disconnected.append(writer)
                if disconnected:
                    async with self._clients_lock:
                        for w in disconnected:
                            self._downstream_clients.remove(w)
                            w.close()
            except Exception as e:
                logger.warning("TM relay error: %s", e)
                break

    async def stop(self) -> None:
        self._running = False
        if self._upstream_writer:
            self._upstream_writer.close()
        for w in self._downstream_clients:
            w.close()


def main():
    parser = argparse.ArgumentParser(description="SMO Gateway")
    parser.add_argument("--upstream", default="localhost:10025",
                        help="Upstream address (host:port)")
    parser.add_argument("--listen", default="0.0.0.0:10025",
                        help="Listen address (host:port)")
    args = parser.parse_args()

    up_host, up_port = args.upstream.rsplit(":", 1)
    listen_host, listen_port = args.listen.rsplit(":", 1)

    logging.basicConfig(level=logging.INFO)
    gw = Gateway(up_host, int(up_port), listen_host, int(listen_port))
    asyncio.run(gw.start())


if __name__ == "__main__":
    main()

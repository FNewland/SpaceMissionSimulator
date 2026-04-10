"""SMO Simulator — TCP TM/TC Server + Instructor WebSocket.

Runs the simulation engine and exposes:
- TCP port for TM downlink (broadcast to clients)
- TCP port for TC uplink
- HTTP/WebSocket for instructor interface
"""
import asyncio
import json
import logging
import argparse
from pathlib import Path
from typing import Optional

from aiohttp import web

from smo_common.protocol.framing import frame_packet, read_framed_packet
from smo_simulator.engine import SimulationEngine
from smo_simulator.instructor.app import create_instructor_app

logger = logging.getLogger(__name__)


class SimulatorServer:
    """Main simulator server with TM/TC sockets and HTTP/WS."""

    def __init__(self, engine: SimulationEngine, config: dict | None = None):
        self.engine = engine
        self._config = config or {}
        self._tm_clients: list[asyncio.StreamWriter] = []
        self._ws_clients: list[web.WebSocketResponse] = []
        self._tc_port = self._config.get("tc_port", 8001)
        self._tm_port = self._config.get("tm_port", 8002)
        self._http_port = self._config.get("http_port", 8080)
        self._running = False

    async def start(self) -> None:
        self._running = True
        self.engine.start()

        # Start TCP servers
        tc_server = await asyncio.start_server(self._handle_tc_client, "0.0.0.0", self._tc_port)
        tm_server = await asyncio.start_server(self._handle_tm_client, "0.0.0.0", self._tm_port)

        # Start HTTP/WS server using instructor app (serves UI + all APIs)
        self._app = create_instructor_app(self.engine)
        app = self._app
        # Add additional server-specific routes
        app.router.add_get("/api/catalog", self._handle_api_catalog)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self._http_port)
        await site.start()

        logger.info("Simulator server started — TC:%d TM:%d HTTP:%d",
                     self._tc_port, self._tm_port, self._http_port)

        # Run TM broadcast loop
        await asyncio.gather(
            tc_server.serve_forever(),
            tm_server.serve_forever(),
            self._tm_broadcast_loop(),
            self._ws_broadcast_loop(),
        )

    async def _handle_tc_client(self, reader, writer):
        addr = writer.get_extra_info('peername')
        logger.info("TC client connected: %s", addr)
        try:
            while self._running:
                packet = await read_framed_packet(reader)
                if packet is None:
                    break
                self.engine.tc_queue.put_nowait(packet)
        except Exception as e:
            logger.debug("TC client error: %s", e)
        finally:
            writer.close()

    async def _handle_tm_client(self, reader, writer):
        addr = writer.get_extra_info('peername')
        logger.info("TM client connected: %s", addr)
        self._tm_clients.append(writer)
        try:
            # Keep connection alive, actual data sent by broadcast loop
            while self._running:
                await asyncio.sleep(1)
                if writer.is_closing():
                    break
        except Exception:
            pass
        finally:
            if writer in self._tm_clients:
                self._tm_clients.remove(writer)
            writer.close()

    async def _tm_broadcast_loop(self):
        while self._running:
            try:
                pkt = self.engine.tm_queue.get_nowait()
                frame = frame_packet(pkt)
                disconnected = []
                for w in self._tm_clients:
                    try:
                        w.write(frame)
                        await w.drain()
                    except Exception:
                        disconnected.append(w)
                for w in disconnected:
                    if w in self._tm_clients:
                        self._tm_clients.remove(w)
                    w.close()
            except Exception:
                await asyncio.sleep(0.05)

    async def _ws_broadcast_loop(self):
        while self._running:
            await asyncio.sleep(1.0)
            ws_clients = self._app["ws_clients"]
            if not ws_clients:
                continue
            state = self.engine.get_state_summary()
            msg = json.dumps({"type": "state_update", "state": state})
            disconnected = []
            for ws in ws_clients:
                try:
                    await ws.send_str(msg)
                except Exception:
                    disconnected.append(ws)
            for ws in disconnected:
                ws_clients.remove(ws)

    async def _handle_api_catalog(self, request):
        return web.json_response({"failures": [], "commands": []})


def main():
    parser = argparse.ArgumentParser(description="SMO Spacecraft Simulator")
    parser.add_argument("--config", default="configs/eosat1/", help="Config directory path")
    parser.add_argument("--speed", type=float, default=1.0, help="Simulation speed multiplier")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    engine = SimulationEngine(config_dir=args.config, speed=args.speed)
    server = SimulatorServer(engine, config={
        "tc_port": 8001, "tm_port": 8002, "http_port": 8080,
    })
    asyncio.run(server.start())


if __name__ == "__main__":
    main()

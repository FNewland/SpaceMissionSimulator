"""SMO Simulator — TCP TM/TC Server + Instructor WebSocket.

Runs the simulation engine and exposes:
- TCP port for TM downlink (broadcast to clients)
- TCP port for TC uplink
- HTTP/WebSocket for instructor interface
"""
import asyncio
import json
import logging
import os
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
        # Bind address (D-2). start.sh printed "LOCAL mode: services bound to
        # loopback where supported" while this hard-coded 0.0.0.0 regardless,
        # so the banner was untrue. `--host` / $SMO_BIND now decide.
        self._bind_host = (self._config.get("bind_host")
                           or os.environ.get("SMO_BIND") or "0.0.0.0")
        self._running = False
        self.tm_packets_broadcast: int = 0

    async def start(self) -> None:
        self._running = True
        self.engine.start()

        # Start TCP servers
        tc_server = await asyncio.start_server(
            self._handle_tc_client, self._bind_host, self._tc_port)
        tm_server = await asyncio.start_server(
            self._handle_tm_client, self._bind_host, self._tm_port)

        # Start HTTP/WS server using instructor app (serves UI + all APIs)
        self._app = create_instructor_app(self.engine)
        app = self._app
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self._bind_host, self._http_port)
        await site.start()

        logger.info("Simulator server started on %s — TC:%d TM:%d HTTP:%d",
                    self._bind_host, self._tc_port, self._tm_port,
                    self._http_port)

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
                try:
                    self.engine.tc_queue.put_nowait(packet)
                except Exception:
                    logger.warning("TC queue full — command dropped (queue=%d)",
                                   self.engine.tc_queue.maxsize)
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
                self.tm_packets_broadcast += 1
                frame = frame_packet(pkt)
                disconnected = []
                for w in self._tm_clients:
                    try:
                        w.write(frame)
                        # Drain with timeout so a slow client can't stall
                        # the entire broadcast loop and cause tm_queue to
                        # overflow.
                        await asyncio.wait_for(w.drain(), timeout=2.0)
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

DEFAULT_HTTP_PORT = 8080


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SMO Spacecraft Simulator")
    parser.add_argument("--config", default="configs/eosat1/", help="Config directory path")
    parser.add_argument("--speed", type=float, default=1.0, help="Simulation speed multiplier")
    parser.add_argument("--host", default=None,
                        help="Bind address for TC/TM/HTTP (default $SMO_BIND "
                             "or 0.0.0.0; use 127.0.0.1 for loopback only)")
    # smo-mcs and smo-planner both take --port; the simulator hard-coded 8080,
    # so on a host where 8080 was taken the whole suite was unstartable. The
    # env fallback is $SMO_SIM_HTTP_PORT, the name smo-mcs already reads when
    # it works out the simulator state URL, so setting it once moves both ends.
    parser.add_argument("--port", type=int, default=None,
                        help="HTTP/WebSocket port for the instructor UI "
                             "(default $SMO_SIM_HTTP_PORT or 8080)")
    return parser


def resolve_http_port(port: int | None, env: dict | None = None) -> int:
    """--port wins, then $SMO_SIM_HTTP_PORT, then 8080."""
    if port is not None:
        return int(port)
    env = os.environ if env is None else env
    return int(env.get("SMO_SIM_HTTP_PORT") or DEFAULT_HTTP_PORT)


def main():
    args = build_parser().parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    http_port = resolve_http_port(args.port)

    engine = SimulationEngine(config_dir=args.config, speed=args.speed)
    server = SimulatorServer(engine, config={
        "tc_port": 8001, "tm_port": 8002, "http_port": http_port,
        "bind_host": args.host,
    })
    asyncio.run(server.start())


if __name__ == "__main__":
    main()

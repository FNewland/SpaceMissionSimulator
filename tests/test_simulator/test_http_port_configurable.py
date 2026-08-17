"""The simulator HTTP port is configurable (D: 8080 was hard-coded).

smo-mcs and smo-planner both took ``--port``; the simulator did not, and 8080
was baked into ``server.py`` and into every launcher. On a host where 8080 was
already in use the entire suite was unstartable, and ``deploy/`` made it worse:
``SIMULATOR_HTTP_PORT`` was advertised in ``smo-env.conf`` and used for the
readiness probe and the printed URLs, but never passed to the simulator, so
setting it pointed the probe at a port nothing was listening on.

Precedence: ``--port`` > ``$SMO_SIM_HTTP_PORT`` > 8080. The env variable is the
name smo-mcs already reads when it derives the simulator state URL, so one
setting moves both ends of the link.
"""
import asyncio
import re
import socket
from pathlib import Path

import pytest

from smo_simulator.server import (DEFAULT_HTTP_PORT, SimulatorServer,
                                  build_parser, resolve_http_port)

REPO = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestPortFlagIsAccepted:

    def test_flag_parses(self):
        args = build_parser().parse_args(["--port", "18080"])
        assert args.port == 18080

    def test_default_is_unchanged(self, monkeypatch):
        monkeypatch.delenv("SMO_SIM_HTTP_PORT", raising=False)
        args = build_parser().parse_args([])
        assert args.port is None
        assert resolve_http_port(args.port) == 8080 == DEFAULT_HTTP_PORT

    def test_env_var_is_honoured(self):
        assert resolve_http_port(None, {"SMO_SIM_HTTP_PORT": "18081"}) == 18081

    def test_flag_beats_env(self):
        assert resolve_http_port(18082, {"SMO_SIM_HTTP_PORT": "18081"}) == 18082

    def test_blank_env_falls_back_to_default(self):
        assert resolve_http_port(None, {"SMO_SIM_HTTP_PORT": ""}) == 8080


class TestPortTakesEffect:

    def test_server_binds_the_configured_port(self, monkeypatch):
        """Not just parsed — `start()` binds the HTTP site to that port.

        The instructor app is stubbed out so this exercises the wiring rather
        than the whole simulation engine.
        """
        from aiohttp import web

        import smo_simulator.server as srv

        monkeypatch.setattr(srv, "create_instructor_app",
                            lambda engine: web.Application())

        class _StubEngine:
            def start(self):
                pass

            def get_state_summary(self):
                return {}

        port = _free_port()
        server = SimulatorServer(_StubEngine(), config={
            "tc_port": _free_port(), "tm_port": _free_port(),
            "http_port": port, "bind_host": "127.0.0.1",
        })
        assert server._http_port == port

        async def _run():
            task = asyncio.ensure_future(server.start())
            try:
                for _ in range(100):          # up to ~5 s
                    if task.done():
                        task.result()          # surface a start-up error
                    try:
                        _, writer = await asyncio.open_connection(
                            "127.0.0.1", port)
                    except OSError:
                        await asyncio.sleep(0.05)
                        continue
                    writer.close()
                    await writer.wait_closed()
                    return True
                return False
            finally:
                server._running = False
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        assert asyncio.run(_run()), \
            f"simulator did not listen on the configured port {port}"


class TestLaunchersPassThePort:

    @pytest.mark.parametrize("script", [
        "start.sh",
        "deploy/start-all.sh",
        "deploy/start-simulator.sh",
    ])
    def test_launcher_does_not_hard_code_the_simulator_port(self, script):
        text = (REPO / script).read_text()
        # the smo-simulator command, plus any backslash-continued lines
        m = re.search(r"^(?:exec\s+)?smo-simulator\b((?:[^\n]*\\\n)*[^\n]*)",
                      text, re.M)
        assert m, f"{script} has no smo-simulator invocation"
        block = m.group(1)
        assert "--port" in block, f"{script} does not pass --port"
        assert not re.search(r"--port\s+\"?8080", block), \
            f"{script} passes a hard-coded simulator port"

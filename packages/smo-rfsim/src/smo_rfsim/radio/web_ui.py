"""Radio web dashboard + control plane served via aiohttp.

Observation is always available: a real-time WebSocket feed of RF link
status and a static HTML dashboard page.

The control plane (POST /api/channel, /api/pipeline, /api/link) is only
mounted when the bridge is started with ``--radio-control``
(``network.radio_control_enabled``). With it off the routes return 403 and
the UI hides the configuration panel, preserving the original "purely
observational" contract.
"""

import asyncio
import json
import logging
from pathlib import Path
from dataclasses import asdict

from aiohttp import web

from .frontend import RadioFrontend

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "static"


def _json(obj):
    return json.dumps(obj, default=str)


async def _read_json(request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text=_json({"error": "invalid JSON body"}),
                                 content_type="application/json")
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text=_json({"error": "body must be an object"}),
                                 content_type="application/json")
    return payload


def _control_route(control, method_name: str):
    """Wrap a bridge control method as an aiohttp POST handler."""
    async def handler(request):
        if control is None:
            return web.json_response({"error": "control plane unavailable"},
                                     status=503, dumps=_json)
        payload = await _read_json(request)
        method = getattr(control, method_name, None)
        if method is None:
            return web.json_response({"error": f"{method_name} unsupported"},
                                     status=501, dumps=_json)
        try:
            result = method(payload)
        except PermissionError as e:
            return web.json_response({"error": str(e)}, status=403, dumps=_json)
        except (ValueError, RuntimeError) as e:
            return web.json_response({"error": str(e)}, status=400, dumps=_json)
        return web.json_response({"ok": True, "config": result}, dumps=_json)
    return handler


async def run_web_ui(frontend: RadioFrontend, host: str = "0.0.0.0",
                     port: int = 8094, control=None):
    """Start the Radio web UI server.

    ``control`` is the RFSimBridge (or any object exposing
    ``get_effective_config``, ``get_diagnostics``, ``apply_channel``,
    ``apply_pipeline`` and ``apply_link``). When omitted the server is
    read-only exactly as before.
    """
    app = web.Application()

    async def index_handler(request):
        index_path = STATIC_DIR / "radio.html"
        if index_path.exists():
            return web.FileResponse(index_path)
        return web.Response(text="Radio Web UI — static file not found",
                            content_type="text/html")

    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        logger.info("Radio WS client connected")
        try:
            while True:
                status = frontend.snapshot()
                data = asdict(status)
                await ws.send_str(json.dumps(data, default=str))
                await asyncio.sleep(0.5)
        except Exception:
            pass
        finally:
            logger.info("Radio WS client disconnected")
        return ws

    async def api_status(request):
        status = frontend.snapshot()
        return web.json_response(asdict(status), dumps=_json)

    async def api_config(request):
        if control is None or not hasattr(control, "get_effective_config"):
            return web.json_response(frontend.status.config or {}, dumps=_json)
        return web.json_response(control.get_effective_config(), dumps=_json)

    async def api_diagnostics(request):
        if control is None or not hasattr(control, "get_diagnostics"):
            return web.json_response(frontend.status.diagnostics or {},
                                     dumps=_json)
        return web.json_response(control.get_diagnostics(), dumps=_json)

    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/config", api_config)
    app.router.add_get("/api/diagnostics", api_diagnostics)
    app.router.add_post("/api/channel", _control_route(control, "apply_channel"))
    app.router.add_post("/api/pipeline", _control_route(control, "apply_pipeline"))
    app.router.add_post("/api/link", _control_route(control, "apply_link"))
    if STATIC_DIR.exists():
        app.router.add_static("/static/", STATIC_DIR)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Radio web UI at http://%s:%d (control plane %s)", host, port,
                "enabled" if getattr(control, "config", None) is not None
                and getattr(control.config.network, "radio_control_enabled", False)
                else "read-only")
    while True:
        await asyncio.sleep(3600)


def build_app(frontend: RadioFrontend, control=None) -> web.Application:
    """Build the aiohttp app without binding a port (used by tests)."""
    app = web.Application()

    async def api_status(request):
        return web.json_response(asdict(frontend.snapshot()), dumps=_json)

    async def api_config(request):
        if control is None or not hasattr(control, "get_effective_config"):
            return web.json_response(frontend.status.config or {}, dumps=_json)
        return web.json_response(control.get_effective_config(), dumps=_json)

    async def api_diagnostics(request):
        if control is None or not hasattr(control, "get_diagnostics"):
            return web.json_response(frontend.status.diagnostics or {},
                                     dumps=_json)
        return web.json_response(control.get_diagnostics(), dumps=_json)

    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/config", api_config)
    app.router.add_get("/api/diagnostics", api_diagnostics)
    app.router.add_post("/api/channel", _control_route(control, "apply_channel"))
    app.router.add_post("/api/pipeline", _control_route(control, "apply_pipeline"))
    app.router.add_post("/api/link", _control_route(control, "apply_link"))
    return app

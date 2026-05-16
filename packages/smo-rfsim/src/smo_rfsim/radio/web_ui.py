"""Radio web-based dashboard served via aiohttp.

Purely observational — provides a real-time WebSocket feed of RF link
status and a static HTML dashboard page. Does not inject failures.
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


async def run_web_ui(frontend: RadioFrontend, host: str = "0.0.0.0",
                     port: int = 8094):
    """Start the Radio web UI server."""
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
        return web.json_response(asdict(status),
                                 dumps=lambda o: json.dumps(o, default=str))

    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/status", api_status)
    if STATIC_DIR.exists():
        app.router.add_static("/static/", STATIC_DIR)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Radio web UI at http://%s:%d", host, port)
    while True:
        await asyncio.sleep(3600)

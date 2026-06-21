"""SMO Simulator — Instructor Web App.

aiohttp app for the instructor control interface:
scenario management, failure injection, breakpoint control, time controls.
"""
import json
import logging
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)


def create_instructor_app(engine) -> web.Application:
    """Create the instructor aiohttp application."""
    app = web.Application()
    app["engine"] = engine
    app["ws_clients"] = []

    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", handle_ws)
    app.router.add_get("/api/state", handle_state)
    app.router.add_get("/api/instructor/snapshot", handle_instructor_snapshot)
    app.router.add_post("/api/command", handle_command)
    app.router.add_get("/api/scenarios", handle_scenarios)
    app.router.add_get("/api/scenario/debrief", handle_scenario_debrief)
    app.router.add_get("/api/failures", handle_failures)
    app.router.add_get("/api/breakpoints", handle_breakpoint_list)
    app.router.add_post("/api/breakpoint/save", handle_breakpoint_save)
    app.router.add_post("/api/breakpoint/load", handle_breakpoint_load)

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.router.add_static("/static/", static_dir)

    return app


async def handle_index(request):
    index_file = Path(__file__).parent / "static" / "index.html"
    if index_file.exists():
        return web.FileResponse(index_file)
    return web.Response(text="<h1>SMO Instructor — static/index.html not found</h1>",
                        content_type="text/html")


async def handle_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    engine = request.app["engine"]
    ws_clients = request.app["ws_clients"]
    ws_clients.append(ws)
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                cmd = json.loads(msg.data)
                engine.instr_queue.put_nowait(cmd)
                await ws.send_str(json.dumps({"status": "ok"}))
    except Exception:
        pass
    finally:
        if ws in ws_clients:
            ws_clients.remove(ws)
    return ws


async def handle_state(request):
    engine = request.app["engine"]
    return web.json_response(engine.get_state_summary())


async def handle_command(request):
    """Instructor-only command handler.

    Accepts only simulation control commands: set_speed, freeze, resume,
    inject, clear_failure, failure_inject, failure_clear, override_passes,
    set_phase, scenario_* operations.

    Rejects spacecraft telecommands with HTTP 403. The MCS must use the TC TCP
    socket for all commands, not this HTTP back-channel.
    """
    engine = request.app["engine"]
    try:
        cmd = await request.json()
        cmd_type = cmd.get("type", "")

        # List of allowed instructor-only command types
        allowed_types = {
            "set_speed", "freeze", "resume", "inject", "clear_failure",
            "failure_inject", "failure_clear", "failure_clear_all", "override_passes",
            "set_phase", "start_scenario", "stop_scenario",
            "save_breakpoint", "load_breakpoint",
        }

        # Reject spacecraft telecommands (look like svc/sub/data_hex)
        if ("service" in cmd or "subtype" in cmd or "data_hex" in cmd
            or (isinstance(cmd_type, str) and cmd_type.startswith(("S", "TC")))):
            return web.json_response(
                {"status": "denied", "message":
                 "Spacecraft telecommands must be sent via the TC TCP socket (port 8001), "
                 "not via HTTP. The MCS uses /api/pus-command on the TC socket."},
                status=403,
            )

        # Reject unknown command types
        if cmd_type not in allowed_types:
            return web.json_response(
                {"status": "denied", "message": f"Command type '{cmd_type}' not allowed. "
                 "Allowed types: " + ", ".join(sorted(allowed_types))},
                status=403,
            )

        engine.instr_queue.put_nowait(cmd)
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_scenarios(request):
    engine = request.app["engine"]
    if hasattr(engine, '_scenario_engine'):
        return web.json_response(engine._scenario_engine.list_scenarios())
    return web.json_response([])


async def handle_scenario_debrief(request):
    """Return the debrief for the most recently finished scenario.

    A scenario can now end on its own when its ``duration_s`` elapses (it no
    longer runs forever). The auto-stop builds and caches a debrief; this
    endpoint lets the instructor UI poll for and display it once the scenario
    has finished, whether it ended automatically or via stop_scenario.

    Also reports whether a scenario is currently active so the UI can poll.
    """
    engine = request.app["engine"]
    se = getattr(engine, "_scenario_engine", None)
    if se is None:
        return web.json_response({"active": False, "debrief": None})
    debrief = se.last_debrief()
    payload = None
    if debrief is not None:
        payload = {
            "name": debrief.name,
            "duration_s": debrief.duration_s,
            "score_pct": debrief.score_pct,
            "mttd_s": debrief.mttd_s,
            "mtti_s": debrief.mtti_s,
            "mttr_s": debrief.mttr_s,
            "responses": debrief.responses,
        }
    return web.json_response({
        "active": se.is_active(),
        "current": se.current_name(),
        "debrief": payload,
    })


async def handle_failures(request):
    engine = request.app["engine"]
    return web.json_response(engine._failure_manager.active_failures())


async def handle_breakpoint_list(request):
    """List the breakpoints the engine currently holds (defect #10).

    Lets the instructor UI render LOAD buttons for breakpoints saved earlier,
    including across page reloads, rather than only for same-session saves.
    """
    engine = request.app["engine"]
    store = getattr(engine, "_breakpoints", {}) or {}
    out = []
    for name, state in store.items():
        out.append({
            "name": name,
            "tick": (state or {}).get("tick_count"),
            "sim_time": (state or {}).get("sim_time"),
        })
    return web.json_response(out)


async def handle_breakpoint_save(request):
    engine = request.app["engine"]
    from smo_simulator.breakpoints import BreakpointManager
    bm = BreakpointManager(engine)
    data = await request.json()
    name = data.get("name", "")
    state = bm.save(name=name)
    return web.json_response({"status": "saved", "name": state["name"]})


async def handle_breakpoint_load(request):
    engine = request.app["engine"]
    from smo_simulator.breakpoints import BreakpointManager
    bm = BreakpointManager(engine)
    data = await request.json()
    success = bm.load(state=data.get("state"))
    return web.json_response({"status": "loaded" if success else "failed"})


async def handle_instructor_snapshot(request):
    """Instructor-only endpoint returning ground-truth state, bypassing RF link gating.

    This endpoint exposes the complete internal state of all subsystem models
    and the simulation engine, suitable for the instructor/operator display which
    has god-mode visibility into the sim (not constrained by simulated RF link status).
    """
    engine = request.app["engine"]
    return web.json_response(engine.get_instructor_snapshot())

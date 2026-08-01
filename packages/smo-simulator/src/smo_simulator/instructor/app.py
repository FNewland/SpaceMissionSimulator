"""SMO Simulator — Instructor Web App.

aiohttp app for the instructor control interface:
scenario management, failure injection, breakpoint control, time controls.
"""
import asyncio
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

    # Delayed-TM (S15) dump store. This process is the WRITER of the archive
    # files, so it can always serve them even when the MCS runs on another
    # machine with no shared filesystem (defect #90). The MCS mounts the same
    # routes; students normally use the MCS copy.
    try:
        from smo_common.dump_store import register_dump_routes
        register_dump_routes(
            app,
            lambda: getattr(engine, "_dump_dir", Path("workspace/dumps")),
            owner="simulator",
        )
    except Exception as e:  # noqa: BLE001 — never let this block the sim UI
        logger.warning("Delayed-TM dump API not mounted: %s", e)

    # Delayed-TM report generator (Phase F §A) — same library/routes the MCS
    # mounts, so a split deployment still has a report surface on 8080.
    try:
        from smo_common.reporting import register_report_routes
        register_report_routes(
            app,
            lambda: getattr(engine, "_dump_dir", Path("workspace/dumps")),
            getattr(engine, "config_dir", Path("configs/eosat1")),
            owner="simulator",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Delayed-TM report API not mounted: %s", e)

    # Safe config editor (Phase F §B). Port 8080 is the INSTRUCTOR's door
    # (docs/DEPLOYMENT.md) — this is the only host that mounts the write
    # surface; the MCS/planner mount the same routes readonly, so students
    # can browse configuration and its history but can never mutate it.
    # While a training scenario is active, apply/revert are refused unless
    # explicitly forced with a recorded reason (brief 06 safety rule 3).
    try:
        from smo_common.confedit import register_config_routes

        def _scenario_active() -> bool:
            se = getattr(engine, "_scenario_engine", None)
            try:
                return bool(se and se.is_active())
            except Exception:  # noqa: BLE001
                return False

        register_config_routes(
            app,
            getattr(engine, "config_dir", Path("configs/eosat1")),
            readonly=False,
            page_path="/config",
            owner="instructor",
            scenario_active=_scenario_active,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Config editor not mounted: %s", e)

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.router.add_static("/static/", static_dir)

    return app



TC_BACK_CHANNEL_DENIED = (
    "Spacecraft telecommands must be sent via the TC TCP socket (port 8001), "
    "not via HTTP. The MCS uses /api/pus-command on the TC socket."
)


def _is_spacecraft_telecommand(cmd) -> bool:
    """True if `cmd` looks like a PUS TC smuggled onto the instructor channel."""
    if not isinstance(cmd, dict):
        return False
    cmd_type = cmd.get("type", "")
    return ("service" in cmd or "subtype" in cmd or "data_hex" in cmd
            or (isinstance(cmd_type, str) and cmd_type.startswith(("S", "TC"))))


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
                # Roadmap N-1 / DI-01: this path had NO allowlist and no type
                # validation, while the HTTP path did — two channels, two trust
                # levels, one queue. A malformed payload (e.g.
                # {"type":"set_speed","value":"fast"}) reached the tick thread
                # and killed it silently. Both channels now go through the same
                # SimulationEngine.submit_instructor_cmd validator.
                try:
                    cmd = json.loads(msg.data)
                except (ValueError, TypeError) as e:
                    await ws.send_str(json.dumps(
                        {"status": "denied", "message": f"malformed JSON: {e}"}))
                    continue
                if _is_spacecraft_telecommand(cmd):
                    await ws.send_str(json.dumps(
                        {"status": "denied",
                         "message": TC_BACK_CHANNEL_DENIED}))
                    continue
                ok, reason = engine.submit_instructor_cmd(cmd)
                await ws.send_str(json.dumps(
                    {"status": "ok"} if ok
                    else {"status": "denied", "message": reason}))
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

        # Reject spacecraft telecommands (look like svc/sub/data_hex)
        if _is_spacecraft_telecommand(cmd):
            return web.json_response(
                {"status": "denied", "message": TC_BACK_CHANNEL_DENIED},
                status=403,
            )

        # One allowlist + type/range validator for every channel (roadmap N-1);
        # SimulationEngine.INSTRUCTOR_CMD_TYPES is its single definition.
        ok, reason = engine.submit_instructor_cmd(cmd)
        if not ok:
            return web.json_response(
                {"status": "denied", "message": reason
                 + ". Allowed types: "
                 + ", ".join(sorted(type(engine).INSTRUCTOR_CMD_TYPES))},
                status=403,
            )
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
    """POST /api/breakpoint/save — routed through the instructor queue (A-3).

    `BreakpointManager.save()/load()` reassign `engine.params`, `_sim_time` and
    every model's state. They used to run inline on the aiohttp thread with no
    coordination with `_tick_once`, so a load landing mid-tick produced a
    spacecraft state with no physical cause. Both now execute on the tick
    thread between ticks and the handler waits for the result.
    """
    engine = request.app["engine"]
    data = await request.json()
    name = data.get("name", "")
    ok, reason = await asyncio.get_running_loop().run_in_executor(
        None, engine.submit_instructor_cmd_sync,
        {"type": "save_breakpoint", "name": name})
    if not ok:
        return web.json_response({"status": "failed", "error": reason},
                                 status=500)
    return web.json_response({"status": "saved", "name": name})


async def handle_breakpoint_load(request):
    """POST /api/breakpoint/load — routed through the instructor queue (A-3)."""
    engine = request.app["engine"]
    data = await request.json()
    cmd = {"type": "load_breakpoint", "name": data.get("name", "")}
    if data.get("state") is not None:
        cmd["state"] = data["state"]
    ok, reason = await asyncio.get_running_loop().run_in_executor(
        None, engine.submit_instructor_cmd_sync, cmd)
    return web.json_response({"status": "loaded" if ok else "failed",
                              **({"error": reason} if not ok else {})})


async def handle_instructor_snapshot(request):
    """Instructor-only endpoint returning ground-truth state, bypassing RF link gating.

    This endpoint exposes the complete internal state of all subsystem models
    and the simulation engine, suitable for the instructor/operator display which
    has god-mode visibility into the sim (not constrained by simulated RF link status).
    """
    engine = request.app["engine"]
    return web.json_response(engine.get_instructor_snapshot())

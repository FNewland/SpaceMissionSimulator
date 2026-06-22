"""SMO Planner — Web Server.

Loads orbit config, computes real contact windows using SGP4 propagation.
Provides ground-track and spacecraft state APIs for 2D map visualization.
Includes pass-based scheduling, power/data budget tracking, and imaging
target planning (Wave 7).
"""
import asyncio
import json
import logging
import argparse
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp
from aiohttp import web

from smo_common.config.loader import load_orbit_config
from smo_common.orbit.propagator import OrbitPropagator, GroundStation
from smo_planner.orbit_planner import OrbitPlanner
from smo_planner.activity_scheduler import ActivityScheduler, ActivityState
from smo_planner.budget_tracker import BudgetTracker
from smo_planner.imaging_planner import ImagingPlanner

logger = logging.getLogger(__name__)


class PlannerServer:
    def __init__(self, config_dir: str | Path, http_port: int = 9091,
                 time_source: str | None = None, sim_state_url: str | None = None,
                 connect_host: str = "localhost"):
        self.config_dir = Path(config_dir)
        self.http_port = http_port

        # ── Time source resolution ──────────────────────────────────
        # Precedence: explicit ctor arg (from CLI) > env SMO_TIME_SOURCE >
        # mission-config time_source field > built-in default ("sim").
        # In SIM mode, _get_now() returns the simulator's current sim_time
        # (anchored + extrapolated by speed, refreshed at most ~1/s). In
        # REAL mode, _get_now() returns wall-clock UTC. The explicit
        # ?epoch= query override always takes precedence over _get_now().
        import os as _os
        _cfg_time_source: str | None = None
        _cfg_sim_state_url: str | None = None
        try:
            from smo_common.config.loader import load_mission_config
            _mc = load_mission_config(self.config_dir)
            _cfg_time_source = _mc.time_source
            _cfg_sim_state_url = _mc.sim_state_url
        except Exception as e:
            logger.warning("Could not load mission config for time source: %s", e)
        self._time_source = (
            time_source
            or _os.environ.get("SMO_TIME_SOURCE")
            or _cfg_time_source
            or "sim"
        ).strip().lower()
        self._sim_state_url = (
            sim_state_url
            or _os.environ.get("SMO_SIM_STATE_URL")
            or _cfg_sim_state_url
            or f"http://{connect_host}:8080/api/state"
        )
        logger.info("Planner time source: %s (sim_state_url=%s)",
                    self._time_source, self._sim_state_url)
        # Sim-clock anchor cache (sim mode). anchor_sim_time + (wall-now -
        # anchor_wall)*speed = current sim time. Refreshed by _refresh_sim_anchor
        # at most ~1/s; falls back to last anchor then wall clock if unreachable.
        self._sim_anchor_time: datetime | None = None
        self._sim_anchor_wall: float = 0.0
        self._sim_anchor_speed: float = 1.0
        self._sim_anchor_refreshed_wall: float = 0.0

        # Load orbit config and build propagator + ground stations
        orbit_cfg = load_orbit_config(self.config_dir)
        self._gs_list = []
        for gs_cfg in orbit_cfg.ground_stations:
            self._gs_list.append(GroundStation(
                name=gs_cfg.name, lat_deg=gs_cfg.lat_deg,
                lon_deg=gs_cfg.lon_deg, alt_km=gs_cfg.alt_km,
                min_elevation_deg=gs_cfg.min_elevation_deg,
            ))
        self._tle1 = orbit_cfg.tle_line1
        self._tle2 = orbit_cfg.tle_line2
        self._earth_r = orbit_cfg.earth_radius_km

        # Orbit planner for ground track prediction
        self._orbit_planner = OrbitPlanner(OrbitPropagator(
            self._tle1, self._tle2,
            ground_stations=self._gs_list,
            earth_radius_km=self._earth_r,
        ))

        # Live propagator for current spacecraft state
        self._live_prop = OrbitPropagator(
            self._tle1, self._tle2,
            ground_stations=self._gs_list,
            earth_radius_km=self._earth_r,
        )
        self._live_prop.reset(self._get_now())

        # Load activity types
        self._activity_types: list[dict] = []
        try:
            import yaml
            at_path = self.config_dir / "planning" / "activity_types.yaml"
            if at_path.exists():
                with open(at_path) as f:
                    data = yaml.safe_load(f)
                self._activity_types = data.get("activity_types", [])
        except Exception:
            pass

        # Activity scheduler (seeded with loaded activity types)
        self._scheduler = ActivityScheduler(self._activity_types)

        # MCS URL for procedure upload
        self._mcs_url = "http://localhost:9090"

        # Cache for contact windows (recomputed periodically)
        self._contacts_cache: list[dict] = []
        self._contacts_computed_at: datetime | None = None

        # Cache for ground track (recomputed periodically)
        self._ground_track_cache: list[dict] = []
        self._ground_track_computed_at: datetime | None = None

        # Imaging planner
        self._imaging_planner = ImagingPlanner()
        try:
            targets_path = self.config_dir / "planning" / "imaging_targets.yaml"
            if targets_path.exists():
                self._imaging_planner.load_targets_from_config(targets_path)
        except Exception:
            pass

    async def _sim_anchor_loop(self):
        """Background refresh of the sim-clock anchor (sim mode only).

        Keeps _get_now() fresh even with no inbound request traffic. The
        synchronous fetch is short and rate-limited; run off-thread so the
        event loop is never blocked.
        """
        if self._time_source != "sim":
            return
        while True:
            try:
                await asyncio.to_thread(self._refresh_sim_anchor, True)
            except Exception as e:
                logger.debug("planner sim-anchor loop error: %s", e)
            await asyncio.sleep(1.5)

    async def start(self) -> None:
        # In sim mode, kick off the background clock-anchor refresher first
        # so contacts/ground-track compute against sim time from the start.
        if self._time_source == "sim":
            asyncio.create_task(self._sim_anchor_loop())
        # Compute initial contacts and ground track
        self._compute_contacts()
        self._compute_ground_track()

        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/wide", self._handle_index_wide)
        app.router.add_get("/api/contacts", self._handle_contacts)
        app.router.add_get("/api/schedule", self._handle_schedule)
        app.router.add_get("/api/ground-stations", self._handle_ground_stations)
        app.router.add_get("/api/ground-track", self._handle_ground_track)
        app.router.add_get("/api/spacecraft-state", self._handle_spacecraft_state)
        app.router.add_post("/api/schedule", self._handle_add_activity)
        app.router.add_put("/api/schedule/{id}", self._handle_update_activity)
        app.router.add_delete("/api/schedule/{id}", self._handle_delete_activity)
        app.router.add_post("/api/schedule/upload", self._handle_upload_schedule)
        app.router.add_get("/api/schedule/validate", self._handle_validate_schedule)
        app.router.add_get("/api/activity-types", self._handle_activity_types)
        # Wave 7: Pass-based scheduling
        app.router.add_post("/api/schedule/pass-activity",
                            self._handle_pass_activity)
        # Wave 7: Power/Data budget
        app.router.add_get("/api/budget/power", self._handle_power_budget)
        app.router.add_get("/api/budget/data", self._handle_data_budget)
        # Wave 7: Imaging targets
        app.router.add_get("/api/imaging/targets", self._handle_imaging_targets)
        app.router.add_get("/api/imaging/opportunities",
                           self._handle_imaging_opportunities)
        app.router.add_post("/api/imaging/schedule",
                            self._handle_imaging_schedule)
        # Constraint validation endpoints
        app.router.add_get("/api/constraints/validate",
                           self._handle_validate_constraints)
        app.router.add_get("/api/constraints/power",
                           self._handle_check_power)
        app.router.add_get("/api/constraints/aocs",
                           self._handle_check_aocs)
        app.router.add_get("/api/constraints/thermal",
                           self._handle_check_thermal)
        app.router.add_get("/api/constraints/data-volume",
                           self._handle_check_data_volume)
        app.router.add_get("/api/constraints/conflicts",
                           self._handle_check_conflicts)
        static_dir = Path(__file__).parent / "static"
        # Serve the top-level vendor directory so that requests for
        # /static/vendor/js/svg-world-map.js resolve even when the
        # symlink inside static/ is broken (points to an absolute
        # path that doesn't exist in the current environment).
        # Walk up from this file to find the project root (contains vendor/).
        vendor_dir = None
        _d = Path(__file__).parent
        for _ in range(10):
            _d = _d.parent
            if (_d / "vendor").is_dir():
                vendor_dir = _d / "vendor"
                break
        if vendor_dir and vendor_dir.exists():
            app.router.add_static("/static/vendor/", vendor_dir)
        if static_dir.exists():
            app.router.add_static("/static/", static_dir)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.http_port)
        await site.start()
        logger.info("Planner server started on port %d", self.http_port)

        # Recompute contacts and ground track every 10 minutes
        while True:
            await asyncio.sleep(600)
            self._compute_contacts()
            self._compute_ground_track()

    def _fetch_sim_state(self) -> dict | None:
        """Synchronous, short, cached fetch of the simulator state endpoint.

        Used to seed/refresh the sim-clock anchor without an event loop
        (e.g. at construction and as a fallback). Returns the parsed JSON
        dict or None on any failure. Kept tiny (1.5 s timeout); callers
        must only invoke it at most ~1/s via the anchor-age guard.
        """
        try:
            import urllib.request
            with urllib.request.urlopen(self._sim_state_url, timeout=1.5) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            logger.debug("planner sim-state fetch failed: %s", e)
            return None

    def _refresh_sim_anchor(self, force: bool = False) -> None:
        """Refresh the cached sim-clock anchor at most ~1/s (sim mode only)."""
        if getattr(self, "_time_source", "real") != "sim":
            return
        wall = __import__("time").time()
        if not force and (wall - self._sim_anchor_refreshed_wall) < 1.0:
            return
        self._sim_anchor_refreshed_wall = wall
        payload = self._fetch_sim_state()
        if not payload:
            return
        sim_time_str = payload.get("sim_time")
        if not sim_time_str:
            return
        try:
            parsed = datetime.fromisoformat(str(sim_time_str).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            self._sim_anchor_time = parsed
            self._sim_anchor_wall = wall
            speed = payload.get("speed")
            if speed is not None:
                self._sim_anchor_speed = float(speed)
        except Exception as e:
            logger.debug("planner sim-anchor parse failed: %s", e)

    def _get_now(self) -> datetime:
        """Return the planner's notion of "now".

        - SIM mode: simulator sim_time, extrapolated from the cached anchor
          by speed (anchor_sim_time + (wall-now - anchor_wall)*speed),
          refreshed at most ~1/s. If the sim was never reachable, falls back
          to wall-clock UTC.
        - REAL mode: wall-clock UTC.

        The explicit ?epoch= query override (handled by callers) takes
        precedence over this method.
        """
        if getattr(self, "_time_source", "real") != "sim":
            return datetime.now(timezone.utc)
        # Refresh the anchor (cheap; rate-limited internally).
        self._refresh_sim_anchor()
        if getattr(self, "_sim_anchor_time", None) is None:
            # Never reached the sim — best-effort wall clock.
            return datetime.now(timezone.utc)
        wall = __import__("time").time()
        elapsed = (wall - self._sim_anchor_wall) * self._sim_anchor_speed
        return self._sim_anchor_time + timedelta(seconds=elapsed)

    def _compute_contacts(self, epoch: datetime | None = None) -> None:
        """Compute contact windows for the next 24 hours from epoch (or now)."""
        try:
            now = epoch or self._get_now()
            prop = OrbitPropagator(
                self._tle1, self._tle2,
                ground_stations=self._gs_list,
                earth_radius_km=self._earth_r,
            )
            prop.reset(now)

            all_windows = []
            for gs in self._gs_list:
                # Reset propagator for each GS to avoid cumulative drift
                prop.reset(now)
                windows = prop.contact_windows(
                    duration_s=86400.0,  # 24 hours
                    step_s=10.0,         # 10s step for accuracy
                    gs=gs,
                )
                all_windows.extend(windows)

            all_windows.sort(key=lambda w: w["aos"])
            self._contacts_cache = all_windows
            self._contacts_computed_at = now
            logger.info("Computed %d contact windows for next 24h", len(all_windows))
        except Exception as e:
            logger.error("Failed to compute contacts: %s", e)

    def _compute_ground_track(self, epoch: datetime | None = None) -> None:
        """Compute ground track for the next ~3 hours (2 orbits) from epoch."""
        try:
            now = epoch or self._get_now()
            prop = OrbitPropagator(
                self._tle1, self._tle2,
                ground_stations=self._gs_list,
                earth_radius_km=self._earth_r,
            )
            planner = OrbitPlanner(prop)
            self._ground_track_cache = planner.predict_ground_track(
                now, duration_hours=3.0, step_s=30.0
            )
            self._ground_track_computed_at = now
            logger.info("Computed ground track: %d points for next 3h",
                        len(self._ground_track_cache))
        except Exception as e:
            logger.error("Failed to compute ground track: %s", e)

    def _get_spacecraft_state(self) -> dict:
        """Get current spacecraft position and velocity."""
        now = self._get_now()
        self._live_prop.reset(now)
        state = self._live_prop.advance(0.0)

        # Compute heading from velocity vector
        vx = state.vel_x if hasattr(state, 'vel_x') else 0.0
        vy = state.vel_y if hasattr(state, 'vel_y') else 0.0
        heading_deg = math.degrees(math.atan2(vy, vx)) % 360

        return {
            "utc": state.utc.isoformat(),
            "lat": round(state.lat_deg, 4),
            "lon": round(state.lon_deg, 4),
            "alt_km": round(state.alt_km, 2),
            "in_eclipse": bool(state.in_eclipse),
            "in_contact": bool(state.in_contact),
            "heading_deg": round(heading_deg, 1),
            "solar_beta_deg": round(state.solar_beta_deg, 1),
            "gs_elevation_deg": round(state.gs_elevation_deg, 1)
                if state.gs_elevation_deg is not None else None,
            "gs_range_km": round(state.gs_range_km, 1)
                if state.gs_range_km is not None else None,
        }

    async def _handle_index(self, request):
        index_file = Path(__file__).parent / "static" / "index.html"
        if index_file.exists():
            return web.FileResponse(index_file)
        return web.Response(text="<h1>SMO Planner — static/index.html not found</h1>",
                            content_type="text/html")

    async def _handle_index_wide(self, request):
        """Serve the wide-screen (5760x1080) planner UI."""
        wide_file = Path(__file__).parent / "static" / "index-wide.html"
        if wide_file.exists():
            return web.FileResponse(wide_file)
        return web.Response(
            text="<h1>SMO Planner — Wide-screen UI not found</h1>"
                 "<p>Expected: static/index-wide.html</p>",
            content_type="text/html")

    async def _handle_contacts(self, request):
        epoch_str = request.query.get("epoch")
        if epoch_str:
            try:
                epoch = datetime.fromisoformat(epoch_str.replace("Z", "+00:00"))
                self._compute_contacts(epoch)
            except ValueError:
                return web.json_response(
                    {"error": "Invalid epoch format. Use ISO 8601."}, status=400)
        return web.json_response({
            "contacts": self._contacts_cache,
            "computed_at": self._contacts_computed_at.isoformat() if self._contacts_computed_at else None,
            "ground_stations": [gs.name for gs in self._gs_list],
        })

    async def _handle_schedule(self, request):
        return web.json_response({"schedule": self._scheduler.get_schedule()})

    async def _handle_ground_stations(self, request):
        return web.json_response({
            "ground_stations": [
                {"name": gs.name, "lat_deg": gs.lat_deg, "lon_deg": gs.lon_deg,
                 "alt_km": gs.alt_km, "min_elevation_deg": gs.min_elevation_deg}
                for gs in self._gs_list
            ]
        })

    async def _handle_ground_track(self, request):
        """Return predicted ground track.

        Query params:
          duration_hours (float): Track duration (default 3.0)
          step_s (float): Step size in seconds (default 30.0)
          offset_minutes (float): Start offset from now in minutes (negative = past)
        """
        duration = float(request.query.get("duration_hours", 3.0))
        step = float(request.query.get("step_s", 30.0))
        offset_min = float(request.query.get("offset_minutes", 0))
        use_cache = (duration == 3.0 and step == 30.0 and offset_min == 0)
        if not use_cache:
            try:
                now = self._get_now()
                start_time = now + timedelta(minutes=offset_min)
                prop = OrbitPropagator(
                    self._tle1, self._tle2,
                    ground_stations=self._gs_list,
                    earth_radius_km=self._earth_r,
                )
                planner = OrbitPlanner(prop)
                track = planner.predict_ground_track(
                    start_time, duration_hours=duration, step_s=step
                )
                return web.json_response({
                    "ground_track": track,
                    "computed_at": now.isoformat(),
                    "offset_minutes": offset_min,
                })
            except Exception as e:
                return web.json_response(
                    {"error": str(e)}, status=500)
        return web.json_response({
            "ground_track": self._ground_track_cache,
            "computed_at": self._ground_track_computed_at.isoformat()
                if self._ground_track_computed_at else None,
        })

    async def _handle_spacecraft_state(self, request):
        """Return current spacecraft position, velocity, and status."""
        try:
            state = self._get_spacecraft_state()
            return web.json_response(state)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_add_activity(self, request):
        """Add an activity to the schedule."""
        try:
            data = await request.json()
            name = data.get("name", "")
            start_time = data.get("start_time", "")
            if not name or not start_time:
                return web.json_response(
                    {"error": "name and start_time are required"}, status=400)
            activity = self._scheduler.add_activity(name, start_time, **{
                k: v for k, v in data.items() if k not in ("name", "start_time")
            })
            return web.json_response({"activity": activity}, status=201)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_delete_activity(self, request):
        """Remove an activity from the schedule by ID."""
        try:
            activity_id = int(request.match_info["id"])
            if self._scheduler.delete_activity(activity_id):
                return web.json_response({"status": "deleted"})
            return web.json_response(
                {"error": "Activity not found"}, status=404)
        except ValueError:
            return web.json_response(
                {"error": "Invalid activity id"}, status=400)

    async def _handle_update_activity(self, request):
        """Update an activity's state or fields."""
        try:
            activity_id = int(request.match_info["id"])
            body = await request.json()
            activity = self._scheduler.get_activity(activity_id)
            if not activity:
                return web.json_response(
                    {"error": "Activity not found"}, status=404)

            # Update state if provided
            new_state = body.get("state")
            if new_state is not None:
                try:
                    state_enum = ActivityState[new_state.upper()]
                    self._scheduler.update_state(activity_id, state_enum)
                except KeyError:
                    return web.json_response(
                        {"error": f"Invalid state: {new_state}"}, status=400)

            # Update other mutable fields
            for key in ("start_time", "duration_s", "priority"):
                if key in body:
                    activity[key] = body[key]

            return web.json_response(
                {"activity": self._scheduler.get_activity(activity_id)})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_upload_schedule(self, request):
        """Upload an activity's command sequence to MCS for execution."""
        try:
            import aiohttp as _aiohttp
            body = await request.json()
            activity_id = body.get("activity_id")
            step_by_step = body.get("step_by_step", False)

            if activity_id is None:
                return web.json_response(
                    {"error": "activity_id is required"}, status=400)

            activity = self._scheduler.get_activity(int(activity_id))
            if not activity:
                return web.json_response(
                    {"error": "Activity not found"}, status=404)

            steps = activity.get("command_sequence", [])
            if not steps:
                return web.json_response(
                    {"error": "Activity has no command sequence"}, status=400)

            # Send to MCS procedure/load endpoint
            payload = {
                "name": activity["name"],
                "steps": steps,
                "procedure_ref": activity.get("procedure_ref", ""),
                "step_by_step": step_by_step,
            }
            async with _aiohttp.ClientSession() as session:
                url = f"{self._mcs_url}/api/procedure/load"
                async with session.post(
                    url, json=payload,
                    timeout=_aiohttp.ClientTimeout(total=5),
                ) as resp:
                    result = await resp.json()
                    if resp.status == 200:
                        self._scheduler.update_state(
                            int(activity_id), ActivityState.UPLOADED
                        )
                    return web.json_response(result, status=resp.status)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_validate_schedule(self, request):
        """Validate the schedule for conflicts and constraint violations.

        Defect #15: now runs the comprehensive ``validate_pass_plan`` check (time
        overlaps, pass-boundary violations, pre-conditions, name conflicts) rather
        than the name-conflict-only ``validate_schedule``. Pre-conditions are
        skipped when no live telemetry is available (the planner has no telemetry
        client), so they are reported as unevaluated rather than falsely passing.
        """
        contacts = self._contacts_cache if self._contacts_cache else []
        issues = self._scheduler.validate_pass_plan(contacts, telemetry=None)
        return web.json_response({
            "valid": len(issues) == 0,
            "issues": issues,
            "activity_count": len(self._scheduler.get_schedule()),
        })

    async def _handle_activity_types(self, request):
        """Return available activity types from config."""
        return web.json_response({"activity_types": self._activity_types})

    # ── Wave 7: Pass-based scheduling ─────────────────────────────

    async def _handle_pass_activity(self, request):
        """Schedule an activity relative to a contact pass.

        POST /api/schedule/pass-activity
        Body: {pass_id, offset_min, name, ...extra fields}
        """
        try:
            data = await request.json()
            pass_id = data.get("pass_id")
            offset_min = data.get("offset_min", 0)
            name = data.get("name", "")

            if pass_id is None:
                return web.json_response(
                    {"error": "pass_id is required"}, status=400)
            if not name:
                return web.json_response(
                    {"error": "name is required"}, status=400)

            contacts = self._contacts_cache
            if not contacts:
                return web.json_response(
                    {"error": "No contact windows computed"}, status=400)

            extra = {
                k: v for k, v in data.items()
                if k not in ("pass_id", "offset_min", "name")
            }

            activity = self._scheduler.schedule_pass_activity(
                pass_id=int(pass_id),
                offset_min=float(offset_min),
                activity_name=name,
                contacts=contacts,
                **extra,
            )
            return web.json_response({"activity": activity}, status=201)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    # ── Wave 7: Power/Data budget ─────────────────────────────────

    async def _handle_power_budget(self, request):
        """Return 24-hour power budget with SoC predictions.

        GET /api/budget/power
        """
        try:
            tracker = BudgetTracker(
                contacts=self._contacts_cache,
                schedule=self._scheduler.get_schedule(),
                ground_track=self._ground_track_cache,
            )
            budget = tracker.compute_power_budget()
            return web.json_response(budget)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_data_budget(self, request):
        """Return data volume budget.

        GET /api/budget/data
        """
        try:
            tracker = BudgetTracker(
                contacts=self._contacts_cache,
                schedule=self._scheduler.get_schedule(),
                ground_track=self._ground_track_cache,
            )
            budget = tracker.compute_data_budget()
            return web.json_response(budget)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ── Wave 7: Imaging targets ───────────────────────────────────

    async def _handle_imaging_targets(self, request):
        """Return configured imaging targets.

        GET /api/imaging/targets
        """
        return web.json_response({
            "targets": self._imaging_planner.get_targets(),
        })

    async def _handle_imaging_opportunities(self, request):
        """Compute upcoming imaging windows for the next 24 hours.

        GET /api/imaging/opportunities
        """
        try:
            # Use a 24-hour ground track for opportunity computation
            now = self._get_now()
            prop = OrbitPropagator(
                self._tle1, self._tle2,
                ground_stations=self._gs_list,
                earth_radius_km=self._earth_r,
            )
            planner = OrbitPlanner(prop)
            track = planner.predict_ground_track(
                now, duration_hours=24.0, step_s=60.0
            )

            opportunities = self._imaging_planner.compute_opportunities(track)
            return web.json_response({
                "opportunities": opportunities,
                "computed_at": now.isoformat(),
                "target_count": len(self._imaging_planner.get_targets()),
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_imaging_schedule(self, request):
        """Schedule an imaging activity for a specific target.

        POST /api/imaging/schedule
        Body: {target_id, start_time, lat (optional), lon (optional)}
        """
        try:
            data = await request.json()
            target_id = data.get("target_id", "")
            start_time = data.get("start_time", "")

            if not target_id or not start_time:
                return web.json_response(
                    {"error": "target_id and start_time are required"},
                    status=400,
                )

            capture = self._imaging_planner.generate_capture_sequence(
                target_id=target_id,
                start_time=start_time,
                lat=data.get("lat"),
                lon=data.get("lon"),
            )

            # Add to the scheduler
            activity = self._scheduler.add_activity(
                capture["name"],
                capture["start_time"],
                duration_s=capture["duration_s"],
                power_w=capture["power_w"],
                data_volume_mb=capture["data_volume_mb"],
                priority=capture["priority"],
                procedure_ref=capture["procedure_ref"],
                command_sequence=capture["command_sequence"],
                target_id=capture["target_id"],
                target_name=capture["target_name"],
                capture_lat=capture["capture_lat"],
                capture_lon=capture["capture_lon"],
            )
            return web.json_response({"activity": activity}, status=201)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    # ── Constraint Validation Endpoints ──────────────────────────────

    async def _handle_validate_constraints(self, request):
        """Comprehensive constraint validation across all subsystems.

        GET /api/constraints/validate
        Query params:
          battery_soc (float): Current battery SoC % (default 80)
        """
        try:
            battery_soc = float(request.query.get("battery_soc", 80.0))
            result = self._scheduler.validate_constraints(
                contacts=self._contacts_cache,
                ground_track=self._ground_track_cache,
                battery_soc_percent=battery_soc,
            )
            return web.json_response(result.to_dict())
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_check_power(self, request):
        """Check power budget constraints.

        GET /api/constraints/power
        """
        try:
            result = self._scheduler.check_power_constraints(
                ground_track=self._ground_track_cache
            )
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_check_aocs(self, request):
        """Check AOCS constraints (slew, momentum).

        GET /api/constraints/aocs
        """
        try:
            result = self._scheduler.check_aocs_constraints()
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_check_thermal(self, request):
        """Check thermal constraints (duty cycle, cooldown).

        GET /api/constraints/thermal
        """
        try:
            result = self._scheduler.check_thermal_constraints()
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_check_data_volume(self, request):
        """Check data volume and storage constraints.

        GET /api/constraints/data-volume
        Query params:
          current_onboard_mb (float): Current onboard data volume
        """
        try:
            current_onboard = float(
                request.query.get("current_onboard_mb", 0.0)
            )
            result = self._scheduler.check_data_volume_constraints(
                current_onboard_mb=current_onboard
            )
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_check_conflicts(self, request):
        """Check for resource conflicts between activities.

        GET /api/constraints/conflicts
        """
        try:
            result = self._scheduler.check_resource_conflicts()
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)


def main():
    parser = argparse.ArgumentParser(description="SMO Mission Planner")
    parser.add_argument("--config", default="configs/eosat1/")
    parser.add_argument("--port", type=int, default=9091)
    parser.add_argument("--connect-host", default="localhost",
                        help="Host of the simulator (used to derive the default "
                             "sim_state_url http://<host>:8080/api/state).")
    parser.add_argument("--time-source", default=None, choices=["sim", "real"],
                        help="Planner clock source: 'sim' (follow simulator "
                             "sim_time/speed) or 'real' (wall-clock UTC). Overrides "
                             "SMO_TIME_SOURCE env and mission-config time_source.")
    parser.add_argument("--sim-state-url", default=None,
                        help="Simulator state endpoint to poll for sim_time/speed in "
                             "sim mode. Overrides SMO_SIM_STATE_URL env and "
                             "mission-config sim_state_url. "
                             "Default http://<connect_host>:8080/api/state.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(PlannerServer(
        args.config, args.port,
        time_source=args.time_source,
        sim_state_url=args.sim_state_url,
        connect_host=args.connect_host,
    ).start())

if __name__ == "__main__":
    main()

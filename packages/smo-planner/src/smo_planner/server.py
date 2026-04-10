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
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from smo_common.config.loader import load_orbit_config
from smo_common.orbit.propagator import OrbitPropagator, GroundStation
from smo_planner.orbit_planner import OrbitPlanner
from smo_planner.activity_scheduler import ActivityScheduler, ActivityState
from smo_planner.budget_tracker import BudgetTracker
from smo_planner.imaging_planner import ImagingPlanner

logger = logging.getLogger(__name__)


class PlannerServer:
    def __init__(self, config_dir: str | Path, http_port: int = 9091):
        self.config_dir = Path(config_dir)
        self.http_port = http_port

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
        self._live_prop.reset(datetime.now(timezone.utc))

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

    async def start(self) -> None:
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

    def _compute_contacts(self, epoch: datetime | None = None) -> None:
        """Compute contact windows for the next 24 hours from epoch (or now)."""
        try:
            now = epoch or datetime.now(timezone.utc)
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
            now = epoch or datetime.now(timezone.utc)
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
        now = datetime.now(timezone.utc)
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
                from datetime import timedelta
                now = datetime.now(timezone.utc)
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
        """Validate the schedule for conflicts and constraint violations."""
        contacts = self._contacts_cache if self._contacts_cache else None
        issues = self._scheduler.validate_schedule(contacts)
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
            now = datetime.now(timezone.utc)
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
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(PlannerServer(args.config, args.port).start())

if __name__ == "__main__":
    main()

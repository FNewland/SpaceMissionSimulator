"""SMO Planner — Web Server.

Loads orbit config, computes real contact windows using SGP4 propagation.
Provides ground-track and spacecraft state APIs for 2D map visualization.
Includes pass-based scheduling, power/data budget tracking, and imaging
target planning (Wave 7).

Planner → MCS commanding chain (defect #44)
-------------------------------------------
POST /api/schedule/upload {activity_id, mode?, step_by_step?} uploads a
scheduled activity to the MCS in one of two modes:

* mode="s11" (default) — time-correct path. The planner POSTs
  {name, start_time, steps, procedure_ref} to MCS
  ``/api/schedule/upload-s11``; the MCS converts each command step into a
  TC(11,4) whose payload is ``exec_time_cuc(4) + <complete embedded TC
  packet>`` and uplinks them immediately. The simulator's on-board S11
  scheduler stores the embedded packets and fires them at the activity's
  planned start time (plus per-step wait offsets), producing normal S1
  acceptance/execution verification for each embedded command.

Time domains (TIME_ARCHITECTURE.md Parts E.1/E.2/E.4)
-----------------------------------------------------
Every time the planner holds, displays or uploads is an absolute ISO-8601 UTC
instant on **SIM TIME**. MET is read from the simulator's ``/api/state``
``met_s`` — the planner never recomputes it. The sim-time -> on-board-CUC
conversion for an S11 upload is done **once, by the MCS**, through its time
correlation (``_utc_to_obc_cuc``, anchored on OBC time telemetry 0x0309), not
by a naive ``datetime_to_cuc`` here. That means an OBC clock running 40 s fast
makes the schedule fire 40 s early in sim time, and the planner deliberately
does not correct for it: closing that gap is the operator's job (NOM-008), not
the planner's.
* mode="immediate" — legacy path. The planner POSTs the command sequence
  (now including ``start_time`` as metadata) to MCS ``/api/procedure/load``;
  the operator starts the ProcedureRunner manually from the Procedures tab.

Activity command sequences come from ``planning/activity_types.yaml`` /
``ImagingPlanner``; their S8 func_ids are verified against the simulator's
``service_dispatch`` routing table (defect #45).
"""
import asyncio
import json
import logging
import os
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
from smo_planner.budget_tracker import (
    BATTERY_CAPACITY_WH,
    DUMP_RATE_BPS,
    ELEVATION_MASK_DEG,
    MIN_DUMP_ELEVATION_DEG,
    ORBIT_ALTITUDE_KM,
    BudgetTracker,
    budget_params_from_config,
)
from smo_planner.imaging_planner import ImagingPlanner
from smo_planner.planner_config import (
    load_planner_config, check_station_alignment, format_alignment_report,
)
from smo_planner.utils import parse_iso as _parse_iso_utc
from smo_planner.plan_templates import (
    load_standard_plans, validate_templates, instantiate_template,
)

logger = logging.getLogger(__name__)


class PlannerServer:
    def __init__(self, config_dir: str | Path, http_port: int = 9091,
                 time_source: str | None = None, sim_state_url: str | None = None,
                 connect_host: str = "localhost", bind_host: str | None = None,
                 ui_mode: str | None = None):
        self.config_dir = Path(config_dir)
        self.http_port = http_port
        # Bind address (D-2): was hard-coded 0.0.0.0 while start.sh claimed
        # "LOCAL mode: services bound to loopback where supported".
        self.bind_host = bind_host or os.environ.get("SMO_BIND") or "0.0.0.0"

        # ── Time source resolution ──────────────────────────────────
        # Precedence: explicit ctor arg (from CLI) > env SMO_TIME_SOURCE >
        # mission-config time_source field > built-in default ("sim").
        # In SIM mode, _get_now() returns the simulator's current sim_time
        # (anchored + extrapolated by speed, refreshed at most ~1/s). In
        # REAL mode, _get_now() returns wall-clock UTC. The explicit
        # ?epoch= query override always takes precedence over _get_now().
        import os as _os

        # ── Planner's own config (planning/planner.yaml) ────────────
        # Horizons, refresh intervals, budget parameters, default-plan
        # behaviour, per-station planning attributes and the station-alignment
        # policy all live there (defect #57).
        try:
            self._planner_cfg = load_planner_config(self.config_dir)
        except Exception as e:
            logger.error("planner.yaml failed validation (%s) — using defaults", e)
            from smo_planner.planner_config import PlannerConfig
            self._planner_cfg = PlannerConfig()

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
            or self.planner_cfg.time.default_time_source
            or "sim"
        ).strip().lower()
        self._sim_state_url = (
            sim_state_url
            or _os.environ.get("SMO_SIM_STATE_URL")
            or _cfg_sim_state_url
            or self.planner_cfg.time.sim_state_url
            or f"http://{connect_host}:8080/api/state"
        )
        logger.info("Planner time source: %s (sim_state_url=%s)",
                    self._time_source, self._sim_state_url)
        # ── UI mode resolution (design_2026-08 plan §1.2) ────────────
        # House precedence, same as time_source: CLI --ui > SMO_UI env >
        # mission-config `ui:` > default "classic" (flips to modern only
        # after the Agent-5 verification gate). Per-request `?ui=`, /classic
        # and /modern override for one response; /wide branches on the same
        # resolution (see _handle_index_wide).
        try:
            _cfg_ui = _mc.ui  # mission config loaded above; None if absent
        except Exception:  # noqa: BLE001 — config never loaded
            _cfg_ui = None
        self._ui_mode = (
            ui_mode
            or _os.environ.get("SMO_UI")
            or _cfg_ui
            or "modern"   # default flipped to modern (Agent-5 gate passed)
        ).strip().lower()
        if self._ui_mode not in ("classic", "modern"):
            logger.warning("Unknown ui mode %r — falling back to classic",
                           self._ui_mode)
            self._ui_mode = "classic"
        # Sim-clock anchor cache (sim mode). anchor_sim_time + (wall-now -
        # anchor_wall)*speed = current sim time. Refreshed by _refresh_sim_anchor
        # at most ~1/s; falls back to last anchor then wall clock if unreachable.
        self._sim_anchor_time: datetime | None = None
        self._sim_anchor_wall: float = 0.0
        self._sim_anchor_speed: float = 1.0
        self._sim_anchor_refreshed_wall: float = 0.0
        # MET is NOT computed here. The simulator owns the single definition
        # (`met_s = sim_time - t0_epoch`, TIME_ARCHITECTURE Part C.2) and
        # publishes it on /api/state; the planner caches that value with the
        # sim-time anchor it arrived with and advances it by exactly the same
        # extrapolation as the clock (see _get_met_s). A planner-local
        # `now - t0_epoch` would be a third definition of MET and is exactly
        # what the spec forbids.
        self._sim_anchor_met_s: float | None = None
        # Sim-clock discontinuity state (defect #59). A breakpoint load, a
        # scenario reset or an instructor jump moves sim_time away from what
        # the previous anchor predicted; when that happens the cached contact
        # windows / ground track describe the WRONG epoch and must be dropped
        # immediately rather than up to `recompute_interval_s` later.
        self._plan_dirty: bool = False
        self._last_jump_utc: str | None = None
        self._last_jump_delta_s: float = 0.0
        self._jump_count: int = 0
        self._sim_reachable: bool = False
        self._wall_clock_fallback: bool = False
        self._warned_wall_clock: bool = False

        # Load orbit config and build propagator + ground stations
        orbit_cfg = load_orbit_config(self.config_dir)
        self._orbit_cfg = orbit_cfg
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

        # ── Ground-station alignment check (load-time validation) ───
        # orbit.yaml is canonical; planner.yaml adds planning attributes on
        # top. Divergence is reported here and served at
        # /api/config/alignment (see planner_config.check_station_alignment).
        self._alignment_report = self._check_station_alignment()

        # Orbit planner for ground track prediction
        self._orbit_planner = OrbitPlanner(OrbitPropagator(
            self._tle1, self._tle2,
            ground_stations=self._gs_list,
            earth_radius_km=self._earth_r,
        ))

        # Present-time illumination reported by the spacecraft (single
        # eclipse authority); None in real mode or before the first anchor.
        self._sim_illum: dict | None = None

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

        # Budget parameters resolved from the mission config (payload.yaml
        # storage/image size, ttc.yaml downlink rate, eps.yaml battery) with
        # planner.yaml overrides — no module constants (defect #60).
        try:
            self._budget_params = budget_params_from_config(
                self.config_dir, self._planner_cfg)
        except Exception as e:
            logger.warning("Budget params unresolved (%s) — defaults used", e)
            self._budget_params = {}

        # Activity scheduler. NOTE: the argument is the activity TYPE
        # catalogue; the schedule itself starts EMPTY and stays empty until the
        # operator adds an activity or loads a standard-plan template.
        self._scheduler = ActivityScheduler(
            self._activity_types, data_params=self._budget_params)

        # Standard-activity templates ("Load standard activities").
        self._plan_templates = self._load_plan_templates()

        # MCS URL for procedure/S11 upload (SMO_MCS_URL for distributed
        # deployments — mirrors the MCS side's SMO_PLANNER_URL).
        self._mcs_url = _os.environ.get("SMO_MCS_URL", "http://localhost:9090")

        # Cache for contact windows (recomputed periodically)
        self._contacts_cache: list[dict] = []
        self._contacts_computed_at: datetime | None = None

        # Cache for ground track (recomputed periodically)
        self._ground_track_cache: list[dict] = []
        self._ground_track_computed_at: datetime | None = None

        # Imaging planner — camera/feasibility parameters from planner.yaml,
        # altitude from orbit.yaml, image size from payload.yaml.
        try:
            self._imaging_planner = ImagingPlanner.from_config(
                targets_path=self.config_dir / "planning" / "imaging_targets.yaml",
                imaging_cfg=self.planner_cfg.imaging,
                altitude_km=orbit_cfg.altitude_km,
                image_size_mb=self._budget_params.get("image_size_mb"),
            )
        except Exception as e:
            logger.error("Imaging planner config failed (%s) — defaults used", e)
            self._imaging_planner = ImagingPlanner()
            try:
                targets_path = self.config_dir / "planning" / "imaging_targets.yaml"
                if targets_path.exists():
                    self._imaging_planner.load_targets_from_config(targets_path)
            except Exception:
                pass

    # ── Config plumbing ─────────────────────────────────────────────

    @property
    def planner_cfg(self):
        """Resolved planning/planner.yaml (all-defaults if not loaded).

        Read through a property so handlers stay safe on a partially
        constructed server (tests build one via ``__new__``).
        """
        cfg = self.__dict__.get("_planner_cfg")
        if cfg is None:
            from smo_planner.planner_config import PlannerConfig
            cfg = PlannerConfig()
            self._planner_cfg = cfg
        return cfg

    def _check_station_alignment(self) -> dict:
        """Compare planner.yaml stations with orbit.yaml and report divergence.

        Runs at construction. ``planner.yaml alignment.on_divergence`` decides
        the severity: ``warn`` (default) logs each issue, ``error`` raises so a
        misconfigured deployment fails fast, ``ignore`` stays quiet.
        """
        try:
            report = check_station_alignment(
                self._planner_cfg, self._orbit_cfg.ground_stations)
        except Exception as e:                       # pragma: no cover
            logger.error("Station alignment check failed: %s", e)
            return {"aligned": True, "issues": [], "stations": [],
                    "error": str(e)}

        policy = (self.planner_cfg.alignment.on_divergence or "warn").lower()
        if report["aligned"]:
            logger.info(
                "Ground-station alignment OK: %d station(s) shared between "
                "orbit.yaml and planner.yaml",
                report["orbit_station_count"])
        elif policy == "ignore":
            logger.debug("Station divergence ignored by config: %s", report["issues"])
        else:
            for issue in report["issues"]:
                logger.warning("GROUND-STATION CONFIG DIVERGENCE: %s", issue)
            logger.warning(
                "Run `python -m smo_planner.planner_config --config %s` for a "
                "side-by-side report (orbit.yaml is canonical).", self.config_dir)
            if policy == "error":
                raise ValueError(
                    "planner.yaml ground stations diverge from orbit.yaml: "
                    + "; ".join(report["issues"]))
        return report

    def _load_plan_templates(self):
        """Load + validate planning/standard_plans.yaml."""
        rel = self.planner_cfg.default_plan.templates_file
        path = self.config_dir / rel if not Path(rel).is_absolute() else Path(rel)
        try:
            cfg = load_standard_plans(path)
        except Exception as e:
            logger.error("standard_plans.yaml failed validation (%s)", e)
            from smo_planner.plan_templates import StandardPlansConfig
            return StandardPlansConfig()
        issues = validate_templates(
            cfg, {a.get("name") for a in self._activity_types})
        for issue in issues:
            logger.warning("STANDARD PLAN TEMPLATE: %s", issue)
        return cfg

    async def _sim_anchor_loop(self):
        """Background refresh of the sim-clock anchor (sim mode only).

        Keeps _get_now() fresh even with no inbound request traffic. The
        synchronous fetch is short and rate-limited; run off-thread so the
        event loop is never blocked.
        """
        if self._time_source != "sim":
            return
        period = max(0.2, float(self.planner_cfg.time.anchor_refresh_s))
        while True:
            try:
                await asyncio.to_thread(self._refresh_sim_anchor, True)
            except Exception as e:
                logger.debug("planner sim-anchor loop error: %s", e)
            await asyncio.sleep(period)

    async def _plan_refresh_loop(self):
        """Recompute contacts + ground track on a jump or when they age out.

        Wakes once a second so a sim-clock discontinuity (breakpoint load,
        scenario reset, instructor jump) is picked up in ~1 s instead of
        waiting out the fixed 600 s wall-clock timer that used to be the only
        trigger (defect #59). Also recomputes when the cached products are
        older than ``sim_recompute_interval_s`` of SIM time, which is what
        matters in a time-accelerated run.
        """
        wall_period = max(1.0, float(self.planner_cfg.refresh.recompute_interval_s))
        sim_period = max(60.0, float(self.planner_cfg.refresh.sim_recompute_interval_s))
        import time as _time
        last_wall = _time.time()
        while True:
            await asyncio.sleep(1.0)
            try:
                if self._plan_dirty:
                    logger.info("Recomputing plan products after sim-time jump")
                    self.recompute_plan_products()
                    last_wall = _time.time()
                    continue
                now_wall = _time.time()
                if (now_wall - last_wall) >= wall_period:
                    self.recompute_plan_products()
                    last_wall = now_wall
                    continue
                if self._contacts_computed_at is not None:
                    aged = abs((self._get_now() - self._contacts_computed_at)
                               .total_seconds())
                    if aged >= sim_period:
                        self.recompute_plan_products()
                        last_wall = now_wall
            except Exception as e:                   # pragma: no cover
                logger.debug("plan refresh loop error: %s", e)

    def recompute_plan_products(self) -> None:
        """Drop and rebuild every cached product that depends on "now"."""
        now = self._get_now()
        try:
            self._live_prop.reset(now)
        except Exception:                            # pragma: no cover
            pass
        self._compute_contacts(now)
        self._compute_ground_track(now)
        self._plan_dirty = False

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
        # Explicit generation routes (plan §1.2): compare classic and modern
        # live in two tabs regardless of the resolved default.
        app.router.add_get("/classic", self._handle_index_classic)
        app.router.add_get("/modern", self._handle_index_modern)
        app.router.add_get("/wide", self._handle_index_wide)
        app.router.add_get("/api/contacts", self._handle_contacts)
        app.router.add_get("/api/schedule", self._handle_schedule)
        app.router.add_get("/api/ground-stations", self._handle_ground_stations)
        app.router.add_get("/api/ground-track", self._handle_ground_track)
        app.router.add_get("/api/spacecraft-state", self._handle_spacecraft_state)
        app.router.add_post("/api/schedule", self._handle_add_activity)
        app.router.add_delete("/api/schedule", self._handle_clear_schedule)
        app.router.add_get("/api/schedule/templates", self._handle_plan_templates)
        app.router.add_post("/api/schedule/load-template",
                            self._handle_load_template)
        app.router.add_get("/api/config/alignment", self._handle_alignment)
        app.router.add_get("/api/config/planner", self._handle_planner_config)
        app.router.add_put("/api/schedule/{id}", self._handle_update_activity)
        app.router.add_delete("/api/schedule/{id}", self._handle_delete_activity)
        app.router.add_post("/api/schedule/upload", self._handle_upload_schedule)
        app.router.add_get("/api/schedule/validate", self._handle_validate_schedule)
        app.router.add_get("/api/activity-types", self._handle_activity_types)
        app.router.add_get("/api/time-source", self._handle_time_source)
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
        # Read-only configuration browser (Phase F, brief 06 §B). Students
        # can browse the mission config and its edit history from the
        # planner; every mutating route is absent by construction
        # (readonly=True). The write surface lives only on the simulator
        # instructor server (8080). Coexists with the planner's own
        # /api/config/alignment and /api/config/planner routes above.
        try:
            from smo_common.confedit import register_config_routes
            register_config_routes(app, self.config_dir, readonly=True,
                                   page_path="/config", owner="planner")
        except Exception as e:  # noqa: BLE001 — never block the planner UI
            logger.warning("Config browser not mounted: %s", e)
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
        # Shared design system (design_2026-08 plan §1.1): one canonical
        # runtime copy served from smo_common package data at the SAME URL
        # on all three servers. Mounted BEFORE /static/ so the broader mount
        # cannot shadow it (aiohttp resolves in registration order).
        import smo_common as _smo_common
        _shared_ui = Path(_smo_common.__file__).parent / "ui"
        if _shared_ui.is_dir():
            app.router.add_static("/static/shared/", _shared_ui)
        else:  # pragma: no cover — packaging error, say so loudly
            logger.warning("smo_common ui/ package data missing (%s) — "
                           "modern pages will render unstyled", _shared_ui)
        if static_dir.exists():
            app.router.add_static("/static/", static_dir)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.bind_host, self.http_port)
        await site.start()
        logger.info("Planner server started on %s:%d", self.bind_host,
                    self.http_port)

        # Contacts/ground track are refreshed by _plan_refresh_loop: on the
        # configured interval AND immediately on a sim-time discontinuity.
        await self._plan_refresh_loop()

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
        if not force and (wall - getattr(self, "_sim_anchor_refreshed_wall", 0.0)) < 1.0:
            return
        self._sim_anchor_refreshed_wall = wall
        payload = self._fetch_sim_state()
        if not payload:
            self._sim_reachable = False
            return
        sim_time_str = payload.get("sim_time")
        if not sim_time_str:
            return
        try:
            parsed = datetime.fromisoformat(str(sim_time_str).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            # ── Discontinuity detection (defect #59) ────────────────
            # Compare the new sim_time against what the PREVIOUS anchor
            # predicted for this wall instant. A breakpoint load can move sim
            # time arbitrarily far backward or forward; the clock follows
            # (the anchor is simply replaced) but _contacts_cache /
            # _ground_track_cache do not, so passes, budgets and validation
            # stay wrong until something invalidates them. That is what
            # _plan_dirty does here.
            if getattr(self, "_sim_anchor_time", None) is not None:
                predicted = self._sim_anchor_time + timedelta(
                    seconds=(wall - getattr(self, "_sim_anchor_wall", wall))
                    * getattr(self, "_sim_anchor_speed", 1.0))
                delta_s = (parsed - predicted).total_seconds()
                tol = max(
                    float(self.planner_cfg.time.jump_tolerance_s),
                    3.0 * float(self.planner_cfg.time.anchor_refresh_s)
                    * max(1.0, abs(self._sim_anchor_speed)),
                )
                if abs(delta_s) > tol:
                    self._plan_dirty = True
                    self._jump_count += 1
                    self._last_jump_utc = parsed.isoformat()
                    self._last_jump_delta_s = delta_s
                    logger.info(
                        "Sim clock discontinuity: %+.1f s (now %s) — "
                        "invalidating contact/ground-track caches",
                        delta_s, parsed.isoformat())
                    # The live propagator is reset per request, but reset it
                    # here too so the jump drives it rather than luck.
                    try:
                        self._live_prop.reset(parsed)
                    except Exception:                # pragma: no cover
                        pass

            self._sim_anchor_time = parsed
            self._sim_anchor_wall = wall
            # ── Present-time illumination: read, never re-derive ──────────
            # The planner propagates its own orbit because it must PREDICT
            # (ground track, eclipse arcs, power budget over a horizon). For
            # "is it eclipse right now" it consumes the spacecraft's value —
            # the single authority in smo_common.orbit.eclipse evaluated on
            # the simulator's clock. Re-deriving it here on the planner's
            # anchored clock is what made the planner disagree with the AOCS
            # near the terminator.
            if "in_eclipse" in payload:
                try:
                    self._sim_illum = {
                        "in_eclipse": bool(payload.get("in_eclipse")),
                        "sun_fraction": (
                            float(payload["sun_fraction"])
                            if payload.get("sun_fraction") is not None
                            else (0.0 if payload.get("in_eclipse") else 1.0)),
                        "in_penumbra": bool(payload.get("in_penumbra", False)),
                        "shadow_model": payload.get("shadow_model") or "",
                        "wall": wall,
                    }
                except (TypeError, ValueError):
                    self._sim_illum = None
            self._sim_reachable = True
            self._wall_clock_fallback = False
            speed = payload.get("speed")
            if speed is not None:
                self._sim_anchor_speed = float(speed)
            # MET comes from the simulator, never from a local subtraction
            # (TIME_ARCHITECTURE Part E.2). It is anchored together with
            # sim_time so the two always describe the same instant. A
            # simulator that does not publish met_s leaves it unknown (None)
            # rather than getting a guess.
            met = payload.get("met_s")
            try:
                self._sim_anchor_met_s = None if met is None else float(met)
            except (TypeError, ValueError):
                self._sim_anchor_met_s = None

            # ── Phase D: adopt the simulator's post-burn element set ────
            # A propulsive burn re-fits the TLE and bumps orbit_epoch_version
            # in /api/state. That is EXACTLY the same class of event as a
            # sim-time jump: the cached contacts/ground track describe an
            # orbit that no longer exists. Swap in the new TLE, rebuild the
            # propagators and mark the plan dirty (defect-#59 machinery).
            #
            # SIM-ONLY (disposition L3): the elset comes from the simulator
            # and only exists in training. In real mode the planner keeps
            # the configured TLE — honest behaviour; operator TLE upload is
            # future work with its own path. _refresh_sim_anchor already
            # early-returns in real mode; this explicit guard is defence in
            # depth so a future caller cannot re-open the coupling.
            ev = payload.get("orbit_epoch_version")
            if self._time_source != "sim":
                ev = None
            if ev is not None and int(ev) != getattr(
                    self, "_orbit_epoch_version", 0):
                elset = payload.get("orbit_elset") or {}
                l1 = elset.get("tle_line1")
                l2 = elset.get("tle_line2")
                if l1 and l2:
                    self._tle1, self._tle2 = str(l1), str(l2)
                elif int(ev) == 0:
                    # Reverted to the un-manoeuvred config orbit
                    self._tle1 = self._orbit_cfg.tle_line1
                    self._tle2 = self._orbit_cfg.tle_line2
                self._orbit_epoch_version = int(ev)
                try:
                    self._live_prop = OrbitPropagator(
                        self._tle1, self._tle2,
                        ground_stations=self._gs_list,
                        earth_radius_km=self._earth_r)
                    self._live_prop.reset(parsed)
                    self._orbit_planner = OrbitPlanner(OrbitPropagator(
                        self._tle1, self._tle2,
                        ground_stations=self._gs_list,
                        earth_radius_km=self._earth_r))
                except Exception as e:            # pragma: no cover
                    logger.warning("post-burn propagator rebuild failed: %s",
                                   e)
                self._plan_dirty = True
                logger.info(
                    "Orbit element set updated (epoch_version %s) — "
                    "invalidating contact/ground-track caches", ev)
        except Exception as e:
            logger.debug("planner sim-anchor parse failed: %s", e)

    #: How stale (wall seconds) the cached spacecraft illumination may be
    #: before the planner falls back to its own propagator.
    _SIM_ILLUM_MAX_AGE_S = 4.5

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
            # Never reached the sim — best-effort wall clock. This used to be
            # silent while /api/time-source still reported "sim", so the UI
            # badge said SIM over wall-clock data. It is now flagged on
            # /api/time-source (sim_reachable/wall_clock_fallback) and warned
            # about once here.
            self._wall_clock_fallback = True
            if not getattr(self, "_warned_wall_clock", False):
                self._warned_wall_clock = True
                logger.warning(
                    "Planner time_source=sim but the simulator at %s has never "
                    "been reachable — falling back to WALL-CLOCK UTC. Plan "
                    "times will not match sim time.",
                    getattr(self, "_sim_state_url", "<unset>"))
            return datetime.now(timezone.utc)
        wall = __import__("time").time()
        elapsed = (wall - getattr(self, "_sim_anchor_wall", wall)) * getattr(
            self, "_sim_anchor_speed", 1.0)
        return self._sim_anchor_time + timedelta(seconds=elapsed)

    def _get_met_s(self) -> float | None:
        """Mission Elapsed Time in seconds, or None if unknown.

        MET has exactly one definition and the simulator owns it
        (``met_s = sim_time - t0_epoch``, TIME_ARCHITECTURE Part C.2). The
        planner reads that value off ``/api/state`` and carries it forward by
        the same interval its sim clock advanced since the anchor — MET and
        sim time run at the same rate by construction, so this stays the
        simulator's number rather than becoming a second one.

        Returns None in REAL (wall-clock) mode, before the simulator has ever
        been reached, and when the simulator publishes no ``met_s``. The
        planner does not invent a fallback: an unknown MET is reported as
        unknown.
        """
        if getattr(self, "_time_source", "real") != "sim":
            return None
        now = self._get_now()
        anchor_met = getattr(self, "_sim_anchor_met_s", None)
        anchor_time = getattr(self, "_sim_anchor_time", None)
        if anchor_met is None or anchor_time is None:
            return None
        return anchor_met + (now - anchor_time).total_seconds()

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

            horizon_s = float(self.planner_cfg.horizons.contacts_hours) * 3600.0
            step_s = float(self.planner_cfg.horizons.contacts_step_s)
            all_windows = []
            for gs in self._gs_list:
                # Reset propagator for each GS to avoid cumulative drift
                prop.reset(now)
                windows = prop.contact_windows(
                    duration_s=horizon_s,   # planner.yaml horizons.contacts_hours
                    step_s=step_s,          # planner.yaml horizons.contacts_step_s
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
                now,
                duration_hours=float(self.planner_cfg.horizons.ground_track_hours),
                step_s=float(self.planner_cfg.horizons.ground_track_step_s),
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

        # Active ground station name (JSON-safe: only pass real strings through)
        active_gs = getattr(state, "active_gs", None)
        if not isinstance(active_gs, str):
            active_gs = None

        # PRESENT-time eclipse comes from the spacecraft when the sim anchor
        # is fresh (see _refresh_sim_anchor); the propagation above remains
        # the source for geometry the spacecraft does not downlink, and the
        # only source in real mode.
        in_eclipse = bool(state.in_eclipse)
        try:
            sun_frac = float(getattr(state, "sun_fraction"))
            if not 0.0 <= sun_frac <= 1.0:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            # Reduced harnesses hand in stubs without the field.
            sun_frac = 0.0 if in_eclipse else 1.0
        shadow_model = getattr(state, "shadow_model", "")
        if not isinstance(shadow_model, str):
            shadow_model = ""
        eclipse_source = "ground_propagator"
        illum = getattr(self, "_sim_illum", None)
        if (illum and getattr(self, "_time_source", "real") == "sim"
                and (__import__("time").time() - illum["wall"])
                <= self._SIM_ILLUM_MAX_AGE_S):
            in_eclipse = illum["in_eclipse"]
            sun_frac = illum["sun_fraction"]
            eclipse_source = "spacecraft"

        return {
            "utc": state.utc.isoformat(),
            "lat": round(state.lat_deg, 4),
            "lon": round(state.lon_deg, 4),
            "alt_km": round(state.alt_km, 2),
            "in_eclipse": in_eclipse,
            "sun_fraction": round(sun_frac, 4),
            "eclipse_source": eclipse_source,
            "shadow_model": shadow_model,
            "in_contact": bool(state.in_contact),
            "active_gs": active_gs,
            "heading_deg": round(heading_deg, 1),
            "solar_beta_deg": round(state.solar_beta_deg, 1),
            "gs_elevation_deg": round(state.gs_elevation_deg, 1)
                if state.gs_elevation_deg is not None else None,
            "gs_range_km": round(state.gs_range_km, 1)
                if state.gs_range_km is not None else None,
        }

    # ── UI switch (design_2026-08 plan §1.2) ──────────────────────────
    # Both UIs hit the same API; these handlers only decide WHICH page is
    # served. Index responses carry Cache-Control: no-cache so a mode flip
    # is never masked by a cached index (risk R9).

    def _resolve_ui(self, request) -> str:
        """Per-request UI mode: ?ui= wins over the resolved server mode."""
        q = (request.query.get("ui") or "").strip().lower()
        if q in ("classic", "modern"):
            return q
        # getattr: house tests build servers via __new__ without __init__.
        return getattr(self, "_ui_mode", "classic")

    def _serve_page(self, rel: str, missing_note: str):
        page = Path(__file__).parent / "static" / rel
        if page.exists():
            return web.FileResponse(
                page, headers={"Cache-Control": "no-cache"})
        return web.Response(
            text=f"<h1>SMO Planner — {missing_note}</h1>"
                 "<p>The classic planner is at <a href=\"/classic\">"
                 "/classic</a>.</p>",
            content_type="text/html",
            headers={"Cache-Control": "no-cache"})

    def _serve_index(self, mode: str):
        if mode == "modern":
            return self._serve_page("modern/index.html",
                                    "modern UI assets not installed")
        return self._serve_page("index.html", "static/index.html not found")

    async def _handle_index(self, request):
        return self._serve_index(self._resolve_ui(request))

    async def _handle_index_classic(self, request):
        """GET /classic — always the classic page (live A/B comparison)."""
        return self._serve_index("classic")

    async def _handle_index_modern(self, request):
        """GET /modern — always the modern page (live A/B comparison)."""
        return self._serve_index("modern")

    async def _handle_index_wide(self, request):
        """Serve the wall-display planner UI (documented classroom URL).

        Phase G: this used to serve `static/index-wide.html`, a second 1822-line
        planner UI that drifted independently of `index.html` (planner_audit.md
        records the two being fixed separately, with different ground-station
        lists). `/wide` is a documented classroom URL — the installation manual
        and DEPLOYMENT.md both cite it — so the route stays. Behaviour by UI
        mode (design_2026-08 plan §1.2 / §2.3):

        - classic: redirect to `/?layout=wide` — the classic page's layout
          mode (the Phase G consolidation, preserved verbatim). The `ui`
          override is carried through so `/wide?ui=classic` stays classic
          after the redirect even when the server default is modern.
        - modern: serve `static/modern/wide.html`, a DISTINCT non-interactive
          wall page (own layout, not a class toggle on the interactive page).
        """
        if self._resolve_ui(request) == "modern":
            return self._serve_page("modern/wide.html",
                                    "modern wide-display assets not installed")
        if (request.query.get("ui") or "").strip().lower() == "classic":
            raise web.HTTPFound("/?layout=wide&ui=classic")
        raise web.HTTPFound("/?layout=wide")

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
        cache_hours = float(self.planner_cfg.horizons.ground_track_hours)
        cache_step = float(self.planner_cfg.horizons.ground_track_step_s)
        duration = float(request.query.get("duration_hours", cache_hours))
        step = float(request.query.get("step_s", cache_step))
        offset_min = float(request.query.get("offset_minutes", 0))
        use_cache = (duration == cache_hours and step == cache_step
                     and offset_min == 0)
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

    async def _handle_clear_schedule(self, request):
        """Clear the whole schedule (back to the empty default state).

        DELETE /api/schedule
        """
        removed = self._scheduler.clear_schedule()
        return web.json_response({"status": "cleared", "removed": removed})

    async def _handle_plan_templates(self, request):
        """List the configured standard-activity templates.

        GET /api/schedule/templates
        """
        return web.json_response({
            "templates": [
                {
                    "id": t.id,
                    "name": t.name or t.id,
                    "description": t.description,
                    "horizon_hours": t.horizon_hours,
                    "activity_count": sum(max(1, a.repeat) for a in t.activities),
                    "activity_types": sorted({a.type for a in t.activities}),
                }
                for t in self._plan_templates.templates
            ],
            "default_template": self.planner_cfg.default_plan.default_template,
            "start_empty": self.planner_cfg.default_plan.start_empty,
        })

    async def _handle_load_template(self, request):
        """Instantiate a standard-activity template at the current sim time.

        POST /api/schedule/load-template
        Body: {template?: id, t0?: ISO 8601, clear?: bool}

        Anchors are resolved against the planner's CURRENT time (sim time in
        sim mode) and the REAL contact windows / ground track, so a loaded
        template lands on real passes at the real epoch — which is exactly what
        the deleted JS fixtures only pretended to do (defect #57).
        """
        try:
            try:
                data = await request.json()
            except Exception:
                data = {}
            template_id = (data.get("template")
                           or self.planner_cfg.default_plan.default_template)
            template = self._plan_templates.get(template_id)
            if template is None:
                return web.json_response(
                    {"error": f"Unknown template '{template_id}'",
                     "available": [t.id for t in self._plan_templates.templates]},
                    status=404)

            t0 = self._get_now()
            if data.get("t0"):
                parsed = datetime.fromisoformat(
                    str(data["t0"]).replace("Z", "+00:00"))
                t0 = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

            if data.get("clear"):
                self._scheduler.clear_schedule()

            # daylight_relative anchors need a track covering the template's
            # horizon; the cached one is only horizons.ground_track_hours long.
            track = self._ground_track_cache
            if template.horizon_hours > float(
                    self.planner_cfg.horizons.ground_track_hours):
                try:
                    prop = OrbitPropagator(
                        self._tle1, self._tle2,
                        ground_stations=self._gs_list,
                        earth_radius_km=self._earth_r,
                    )
                    track = OrbitPlanner(prop).predict_ground_track(
                        t0, duration_hours=template.horizon_hours,
                        step_s=float(self.planner_cfg.horizons.imaging_step_s),
                    )
                except Exception as e:
                    logger.warning(
                        "Could not extend the ground track to %.1f h (%s) — "
                        "using the cached %.1f h track",
                        template.horizon_hours, e,
                        self.planner_cfg.horizons.ground_track_hours)

            specs, warnings = instantiate_template(
                template, t0,
                contacts=self._contacts_cache,
                ground_track=track,
                # The catalogue is what tells the resolver each activity's
                # duration, resources and mutual exclusions, so it can place
                # the flexible anchors around the pass-pinned ones instead of
                # on top of them.
                activity_types=self._activity_types,
            )
            added = []
            for spec in specs:
                added.append(self._scheduler.add_activity(
                    spec["name"], spec["start_time"], **spec["extras"]))

            return web.json_response({
                "template": template.id,
                "t0": t0.isoformat(),
                "added": len(added),
                "activities": added,
                "warnings": warnings,
            }, status=201)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_alignment(self, request):
        """Planner<->orbit.yaml ground-station alignment report.

        GET /api/config/alignment?recheck=1
        """
        if request.query.get("recheck"):
            try:
                self._orbit_cfg = load_orbit_config(self.config_dir)
                self._planner_cfg = load_planner_config(self.config_dir)
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)
            self._alignment_report = self._check_station_alignment()
        report = dict(self._alignment_report)
        report["text"] = format_alignment_report(self._alignment_report)
        return web.json_response(report)

    async def _handle_planner_config(self, request):
        """Return the resolved planner configuration (planning/planner.yaml)."""
        return web.json_response({
            "planner_config": self.planner_cfg.model_dump(mode="json"),
            "budget_params": {
                k: v for k, v in self._budget_params.items()
                if k != "sources"
            },
            "budget_sources": self._budget_params.get("sources", {}),
        })

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
        """Upload an activity's command sequence to the MCS.

        Body: {activity_id, mode?: "s11"|"immediate", step_by_step?}.

        mode="s11" (default): the activity becomes on-board time-tagged
        commands via MCS ``/api/schedule/upload-s11`` — each command fires
        at the activity's planned ``start_time`` (defect #44: the scheduled
        time was previously discarded and execution was immediate/manual).

        mode="immediate": legacy ProcedureRunner load via
        ``/api/procedure/load``; ``start_time`` is forwarded as metadata.

        **The planner uploads sim time, not CUC** (TIME_ARCHITECTURE Part
        E.4). ``start_time`` goes out as the ISO-8601 sim-time instant the
        operator planned. The MCS performs the single sim-time -> on-board-CUC
        conversion via its time correlation, so the tag lands on the OBC's own
        clock. If that clock is 40 s fast the activity executes 40 s early in
        sim time; the planner must NOT pre-compensate, because hiding the
        offset would hide the reason for synchronising the clock at all. The
        fix is a clock sync (NOM-008 / LEOP-006), after which the same plan
        fires at the planned sim time with no change to the plan.
        """
        try:
            import aiohttp as _aiohttp
            body = await request.json()
            activity_id = body.get("activity_id")
            step_by_step = body.get("step_by_step", False)
            mode = str(body.get("mode", "s11")).strip().lower()

            if activity_id is None:
                return web.json_response(
                    {"error": "activity_id is required"}, status=400)
            if mode not in ("s11", "immediate"):
                return web.json_response(
                    {"error": f"Invalid mode '{mode}' (s11|immediate)"},
                    status=400)

            activity = self._scheduler.get_activity(int(activity_id))
            if not activity:
                return web.json_response(
                    {"error": "Activity not found"}, status=404)

            steps = activity.get("command_sequence", [])
            if not steps:
                return web.json_response(
                    {"error": "Activity has no command sequence"}, status=400)

            # Both paths carry the planned start time (defect #44).
            payload = {
                "name": activity["name"],
                "steps": steps,
                "procedure_ref": activity.get("procedure_ref", ""),
                "start_time": activity.get("start_time", ""),
            }
            if mode == "immediate":
                payload["step_by_step"] = step_by_step
                url = f"{self._mcs_url}/api/procedure/load"
            else:
                url = f"{self._mcs_url}/api/schedule/upload-s11"

            async with _aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    timeout=_aiohttp.ClientTimeout(total=5),
                ) as resp:
                    result = await resp.json()
                    if resp.status == 200:
                        self._scheduler.update_state(
                            int(activity_id), ActivityState.UPLOADED
                        )
                    if isinstance(result, dict):
                        result.setdefault("mode", mode)
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

    async def _handle_time_source(self, request):
        """Return the resolved time-source mode and the planner's clock.

        The frontend uses this both to label its data-source badges
        (sim -> SIM/SIMULATED, real -> LIVE/LIVE DATA) AND to run its own
        clock: ``sim_time`` + ``speed`` + the wall instant they were read at
        let the page extrapolate sim time locally instead of using the
        operator's wall clock for the NOW marker, the timeline origin, the
        contact filter and the Add-Activity default (defect #58).

        ``sim_reachable``/``wall_clock_fallback`` expose the previously silent
        degradation where time_source=sim but the simulator was never
        reachable, so the badge said SIM over wall-clock data.
        """
        import time as _time
        now = self._get_now()
        met_s = self._get_met_s()
        sim = getattr(self, "_time_source", "real") == "sim"
        anchor_time = getattr(self, "_sim_anchor_time", None)
        anchor_age = (
            _time.time() - getattr(self, "_sim_anchor_wall", 0.0)
            if sim and anchor_time is not None else None
        )
        stale_after = float(self.planner_cfg.time.stale_anchor_s)
        return web.json_response({
            "time_source": getattr(self, "_time_source", "real"),
            "now": now.isoformat(),
            "sim_time": now.isoformat() if sim else None,
            # MET as published by the simulator (its single definition),
            # carried forward with the sim-clock anchor. None when unknown.
            "met_s": round(met_s, 3) if met_s is not None else None,
            "speed": getattr(self, "_sim_anchor_speed", 1.0) if sim else 1.0,
            "sim_reachable": bool(getattr(self, "_sim_reachable", False)) if sim else None,
            "wall_clock_fallback": bool(sim and anchor_time is None),
            "anchor_age_s": round(anchor_age, 2) if anchor_age is not None else None,
            "degraded": bool(sim and (anchor_time is None
                                      or (anchor_age or 0.0) > stale_after)),
            "sim_state_url": getattr(self, "_sim_state_url", None),
            "last_jump_utc": getattr(self, "_last_jump_utc", None),
            "last_jump_delta_s": round(getattr(self, "_last_jump_delta_s", 0.0), 2),
            "jump_count": getattr(self, "_jump_count", 0),
            "plan_dirty": bool(getattr(self, "_plan_dirty", False)),
            "contacts_computed_at": (
                self._contacts_computed_at.isoformat()
                if getattr(self, "_contacts_computed_at", None) else None),
        })

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

    def _illumination_track(self) -> list[dict]:
        """Ground track covering the CONTACT horizon, for the power budget.

        ``_ground_track_cache`` is only ``horizons.ground_track_hours`` (3 h)
        long because it exists to draw the map, while the power budget
        integrates over the whole 24 h contact horizon. Handing the map's track
        to the budget left 21 of 24 hours with no eclipse or beta data — and
        the tracker used to paper over that with constants, which is the root
        cause of the "final SoC is 0 on one machine and 100 on another" report
        (see BudgetTracker.compute_power_budget). The tracker now refuses to
        integrate on data it does not have, so the server has to actually
        supply it.

        Cached per epoch; falls back to the map track (and the budget then
        honestly reports itself as not computable) if propagation fails.
        """
        contacts = self._contacts_cache or []
        if not contacts:
            return self._ground_track_cache
        t0 = _parse_iso_utc(contacts[0].get("aos", ""))
        t_end = max(
            (p for p in (_parse_iso_utc(c.get("los", "")) for c in contacts)
             if p is not None), default=None)
        if t0 is None or t_end is None or t_end <= t0:
            return self._ground_track_cache

        hours = (t_end - t0).total_seconds() / 3600.0 + 0.5
        key = (t0.isoformat(), round(hours, 2))
        if getattr(self, "_illum_track_key", None) == key:
            return self._illum_track_cache
        try:
            prop = OrbitPropagator(
                self._tle1, self._tle2, ground_stations=self._gs_list,
                earth_radius_km=self._earth_r,
            )
            track = OrbitPlanner(prop).predict_ground_track(
                t0, duration_hours=hours,
                step_s=float(self.planner_cfg.horizons.ground_track_step_s),
            )
        except Exception as e:
            logger.warning(
                "Could not propagate the %.1f h illumination track for the "
                "power budget (%s) — the budget will report itself as not "
                "computable rather than assume an eclipse fraction", hours, e)
            return self._ground_track_cache
        self._illum_track_key = key
        self._illum_track_cache = track
        return track

    def _make_budget_tracker(self, initial_onboard_mb: float = 0.0,
                             ground_track: list[dict] | None = None
                             ) -> BudgetTracker:
        """Budget tracker built from the mission config (defect #60).

        Storage capacity, image size, downlink rates and battery capacity come
        from payload.yaml / ttc.yaml / eps.yaml (with planner.yaml overrides),
        never from module constants. The downlink is a LADDER, not a rate
        (defect #86): the DUMP rung (ttc.yaml's top tier) and the ROUTINE rung
        (its ``default_tier``) are both carried, with the elevation gate
        between them.
        """
        p = getattr(self, '_budget_params', None) or {}
        return BudgetTracker(
            contacts=self._contacts_cache,
            schedule=self._scheduler.get_schedule(),
            ground_track=(self._ground_track_cache if ground_track is None
                          else ground_track),
            initial_soc=p.get("initial_soc", 80.0),
            battery_capacity_wh=p.get("battery_capacity_wh", BATTERY_CAPACITY_WH),
            storage_capacity_mb=p.get("storage_capacity_mb", 20000.0),
            downlink_rate_bps=p.get("downlink_rate_bps", DUMP_RATE_BPS),
            protocol_overhead=p.get("protocol_overhead", 0.20),
            image_size_mb=p.get("image_size_mb"),
            hk_background_mb_per_hour=p.get("hk_background_mb_per_hour", 0.0),
            storage_margin_percent=p.get("storage_margin_percent", 10.0),
            station_downlink_rate_bps=p.get("station_downlink_rate_bps"),
            min_dump_elevation_deg=p.get("min_dump_elevation_deg"),
            initial_onboard_mb=initial_onboard_mb,
            sources=p.get("sources"),
            routine_rate_bps=p.get("routine_rate_bps"),
            dump_rate_bps=p.get("dump_rate_bps"),
            routine_tier_name=p.get("routine_tier_name", "LOW"),
            dump_tier_name=p.get("dump_tier_name", "HIGH"),
            default_min_dump_elevation_deg=p.get(
                "default_min_dump_elevation_deg", MIN_DUMP_ELEVATION_DEG),
            elevation_mask_deg=p.get("elevation_mask_deg", ELEVATION_MASK_DEG),
            orbit_altitude_km=p.get("orbit_altitude_km", ORBIT_ALTITUDE_KM),
            hk_stream_bps=p.get("hk_stream_bps", 0.0),
            station_setup_s=p.get("station_setup_s"),
            station_teardown_s=p.get("station_teardown_s"),
        )

    async def _handle_power_budget(self, request):
        """Return 24-hour power budget with SoC predictions.

        GET /api/budget/power

        The budget may legitimately answer "I cannot compute this"
        (``computable: false`` with a ``reason`` and ``final_soc: null``). That
        is a 200, not a 500 — it is a result about the plan's inputs, and the
        UI is required to render the reason rather than a 0 % / 100 % bar.
        """
        try:
            tracker = self._make_budget_tracker(
                ground_track=self._illumination_track())
            budget = tracker.compute_power_budget()
            return web.json_response(budget)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_data_budget(self, request):
        """Return data volume budget.

        GET /api/budget/data
        """
        try:
            onboard = float(request.query.get("current_onboard_mb", 0.0))
            tracker = self._make_budget_tracker(initial_onboard_mb=onboard)
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
                now,
                duration_hours=float(self.planner_cfg.horizons.imaging_hours),
                step_s=float(self.planner_cfg.horizons.imaging_step_s),
            )

            # include_infeasible=1 also returns the windows rejected by the
            # off-nadir / solar-elevation / revisit constraints, with the
            # reason — useful for teaching why an opportunity is not offered.
            include_infeasible = bool(request.query.get("include_infeasible"))
            opportunities = self._imaging_planner.compute_opportunities(
                track, include_infeasible=include_infeasible)
            return web.json_response({
                "opportunities": opportunities,
                "computed_at": now.isoformat(),
                "target_count": len(self._imaging_planner.get_targets()),
                "feasible_count": sum(
                    1 for o in opportunities if o.get("feasible", True)),
                "swath_km": round(self._imaging_planner.swath_km, 1),
                "max_off_nadir_deg": self._imaging_planner.max_off_nadir_deg,
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

            # lat/lon are optional: when the caller does not supply them the
            # planner uses the ACCESS POINT computed for that opportunity (the
            # swath-crossing point), not the region centre (defect #61).
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
                aim_source=capture["aim_source"],
                off_nadir_deg=capture.get("off_nadir_deg"),
                solar_elevation_deg=capture.get("solar_elevation_deg"),
            )
            # Record the capture so revisit_days is enforced against it when
            # the next opportunity list is computed.
            self._imaging_planner.note_scheduled(target_id, capture["start_time"])
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
    parser.add_argument("--host", default=None,
                        help="HTTP bind address (default $SMO_BIND or 0.0.0.0)")
    parser.add_argument("--connect-host", default="localhost",
                        help="Host of the simulator (used to derive the default "
                             "sim_state_url when neither --sim-state-url, "
                             "SMO_SIM_STATE_URL nor the mission/planner config "
                             "provides one).")
    parser.add_argument("--time-source", default=None, choices=["sim", "real"],
                        help="Planner clock source: 'sim' (follow simulator "
                             "sim_time/speed) or 'real' (wall-clock UTC). Overrides "
                             "SMO_TIME_SOURCE env and mission-config time_source.")
    # P6 (design_2026-08 §4): the help text no longer advertises a
    # ":8080/api/state" literal — the default is config-resolved
    # (planner.yaml time.sim_state_url / mission.yaml sim_state_url).
    parser.add_argument("--sim-state-url", default=None,
                        help="Simulator state endpoint to poll for sim_time/speed in "
                             "sim mode. Overrides SMO_SIM_STATE_URL env and the "
                             "mission/planner config sim_state_url.")
    parser.add_argument("--ui", default=None, choices=["classic", "modern"],
                        help="Console generation served at '/': 'classic' or 'modern'. "
                             "Overrides SMO_UI env and mission-config ui. Per-request "
                             "?ui= and the /classic and /modern routes win over all; "
                             "/wide follows the same resolution.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(PlannerServer(
        args.config, args.port,
        time_source=args.time_source,
        sim_state_url=args.sim_state_url,
        connect_host=args.connect_host,
        bind_host=args.host,
        ui_mode=args.ui,
    ).start())

if __name__ == "__main__":
    main()

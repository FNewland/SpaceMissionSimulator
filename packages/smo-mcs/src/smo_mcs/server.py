"""SMO MCS — Web Server.

Connects to simulator/gateway via TCP, serves config-driven operator displays.
Supports position-based command access control.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import argparse
import time as _time_mod
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
from collections import deque

import aiohttp
from aiohttp import web

from smo_common.protocol.framing import frame_packet, read_framed_packet
from smo_common.protocol.ecss_packet import decommutate_packet

# ── Field-name translation ──────────────────────────────────────────
# The MCS UI (index.html) binds to field names with unit suffixes
# (e.g., soc_pct, rssi_dbm, temp_obc_C).  The server builds grouped
# subsystem dicts from parameters.yaml short_keys (e.g., bat_soc,
# rssi, temp_obc).  This map bridges the gap.
_UI_KEY_MAP: dict[str, str] = {
    # EPS
    "bat_soc": "soc_pct",
    "bat_voltage": "bat_voltage_V",
    "bus_voltage": "bus_voltage_V",
    "bat_temp": "bat_temp_C",
    "bat_current": "bat_current_A",
    "sa_a_current": "sa_a_A",
    "sa_b_current": "sa_b_A",
    "power_gen": "power_gen_W",
    "power_cons": "power_cons_W",
    # TCS
    "temp_obc": "temp_obc_C",
    "temp_battery": "temp_bat_C",
    "temp_fpa": "temp_fpa_C",
    "temp_panel_px": "temp_px_C",
    "temp_panel_mx": "temp_mx_C",
    "temp_panel_py": "temp_py_C",
    "temp_panel_my": "temp_my_C",
    "temp_panel_pz": "temp_pz_C",
    "temp_panel_mz": "temp_mz_C",
    "htr_battery": "htr_bat",
    # AOCS
    "att_error": "att_error_deg",
    "rw1_speed": "rw1_rpm",
    "rw2_speed": "rw2_rpm",
    "rw3_speed": "rw3_rpm",
    "rw4_speed": "rw4_rpm",
    # TTC
    "rssi": "rssi_dbm",
    "link_margin": "link_margin_db",
    "tm_data_rate": "data_rate_bps",
    "contact_elevation": "elevation_deg",
    # OBDH — "temp" is ambiguous without subsystem, remap to obc_temp
    "temp": "obc_temp",
    "uptime": "uptime_s",
    "obc_time": "obc_time_cuc",
    # Payload
    "store_used": "store_used_pct",
}
from smo_common.config.loader import (
    load_mcs_displays, load_mission_config, load_positions, load_tc_catalog,
)
from smo_mcs.tc_manager import TCManager
from smo_mcs.procedure_runner import ProcedureRunner
from smo_mcs.tm_archive import TMArchive
from smo_mcs.displays.contact_pass_scheduler import ContactScheduler
from smo_mcs.displays.power_budget import PowerBudgetMonitor
from smo_mcs.displays.fdir_alarm_panel import FDIRAlarmPanel
from smo_mcs.displays.procedure_status import ProcedureStatusPanel
from smo_mcs.displays.system_overview import SystemOverviewDashboard

logger = logging.getLogger(__name__)


class MCSServer:
    """Mission Control System web server."""

    def __init__(self, config_dir: str | Path, connect_host: str = "localhost",
                 connect_port: int = 8002, http_port: int = 9090,
                 tc_port: int = 8001, sim_epoch: str | None = None):
        self.config_dir = Path(config_dir)
        self.connect_host = connect_host
        self.connect_port = connect_port
        self.http_port = http_port
        self.tc_port = tc_port
        self._ws_clients: list[tuple[web.WebSocketResponse, str]] = []
        self._latest_state: dict = {}
        self._running = False
        # Ground time system: MCS maintains its own clock.
        # - sim_epoch=None → use real UTC (wall clock)
        # - sim_epoch="2026-03-10T00:00:00Z" → fixed simulation epoch
        self._ground_epoch: Optional[datetime] = None
        self._ground_start_wall: float = _time_mod.time()
        self._ground_time_offset: float = 0.0  # manual adjustment (seconds)
        self._sim_speed: float = 1.0
        if sim_epoch:
            self._ground_epoch = datetime.fromisoformat(sim_epoch.replace("Z", "+00:00"))
        # Orbit propagator (initialised from TLE config, if available)
        self._orbit_prop = None
        self._init_orbit_propagator()
        self._displays = load_mcs_displays(self.config_dir)

        # Parameter cache: populated ONLY from TM TCP socket HK packets via
        # the TMProcessor decoder (which inverts the engine's HK packing using
        # hk_structures.yaml). The MCS has no other source of spacecraft state.
        # Maps param_id (int) -> {"value": float, "last_update_ts": float, "sid": int}
        self._param_cache: dict[int, dict] = {}
        self._param_cache_lock = asyncio.Lock()
        self._last_tm_frame_ts: Optional[float] = None  # epoch time of last received TM frame

        # Wire up the real HK decoder. Two prior fix passes left _process_tm
        # only logging packet metadata into a debug dict; the param cache was
        # never populated and /api/state always returned empty params. Using
        # TMProcessor (smo_mcs.tm_processor) to do the actual decommutation
        # against hk_structures.yaml so this can never silently regress.
        from smo_mcs.tm_processor import TMProcessor
        from smo_common.config.loader import load_hk_structures
        try:
            hk_defs = load_hk_structures(self.config_dir)
            hk_struct_map: dict[int, list[tuple]] = {}
            for hk in hk_defs:
                hk_struct_map[hk.sid] = [
                    (p.param_id, p.pack_format, p.scale) for p in hk.parameters
                ]
        except Exception as e:
            logger.warning("Failed to load hk_structures.yaml: %s", e)
            hk_struct_map = {}
        self._tm_processor = TMProcessor(hk_structures=hk_struct_map)

        # Build param_id -> (subsystem, name, units) lookup once, from
        # parameters.yaml. Used by _state_poll_loop to expose both a flat
        # by-ID view and a subsystem-grouped view (which is what the existing
        # MCS frontend cards bind to: state.eps.*, state.aocs.*, etc.).
        self._param_meta: dict[int, dict] = {}
        try:
            import yaml as _yaml
            params_path = self.config_dir / "telemetry" / "parameters.yaml"
            with open(params_path) as f:
                pdata = _yaml.safe_load(f) or {}
            for p in pdata.get("parameters", []) or []:
                try:
                    pid = int(p["id"]) if isinstance(p["id"], int) else int(str(p["id"]), 0)
                except Exception:
                    continue
                full_name = str(p.get("name", ""))
                # eps.bat_voltage -> short_key=bat_voltage
                short_key = full_name.split(".", 1)[1] if "." in full_name else full_name
                self._param_meta[pid] = {
                    "subsystem": str(p.get("subsystem", "")),
                    "name": full_name,
                    "short_key": short_key,
                    "units": str(p.get("units", "")),
                }
        except Exception as e:
            logger.warning("Failed to load parameters.yaml for param meta: %s", e)

        # Position access control
        self._positions = load_positions(self.config_dir)

        # TC catalog for command metadata
        try:
            self._tc_catalog = load_tc_catalog(self.config_dir)
        except Exception:
            self._tc_catalog = []

        # TC connection
        self._tc_writer: Optional[asyncio.StreamWriter] = None
        self._tc_manager = TCManager(apid=1)

        # Verification log — last 200 commands
        self._verification_log: deque[dict] = deque(maxlen=200)

        # Shift handover log
        self._handover_log: list[dict] = []

        # GO/NO-GO coordination state
        self._go_nogo_active = False
        self._go_nogo_label = ""
        self._go_nogo_responses: dict[str, str] = {}  # position -> GO/NOGO/STANDBY
        self._go_nogo_initiator = ""

        # Procedure execution engine
        self._procedure_runner = ProcedureRunner(
            send_command_fn=self._proc_send_command,
            get_telemetry_fn=self._proc_get_telemetry,
        )

        # Load activity types for procedure browser
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

        # Alarm journal — last 1000 alarms (Feature 2)
        self._alarm_journal: deque[dict] = deque(maxlen=1000)
        self._alarm_id_counter = 0

        # Persistent TM archive (SQLite)
        archive_dir = self.config_dir / ".." / "data"
        archive_dir.mkdir(parents=True, exist_ok=True)
        self._archive = TMArchive(archive_dir / "tm_archive.db")
        self._archive.open()
        self._archive_tick = 0  # counter to batch parameter archiving

        # TM dump playback data (Feature 3)
        self._tm_dump_data: dict[str, list[dict]] = {}

        # Concurrency locks
        self._ws_lock = asyncio.Lock()
        self._go_nogo_lock = asyncio.Lock()
        self._tc_send_lock = asyncio.Lock()
        self._cmd_queue: asyncio.Queue = asyncio.Queue()

        # Planner API base URL for contacts proxy (Feature 1)
        # Prefer SMO_PLANNER_URL env var for distributed deployments
        import os
        self._planner_api_base = os.environ.get(
            "SMO_PLANNER_URL", f"http://{connect_host}:9091"
        )

        # Load procedure index for procedure browser
        self._procedure_index: list[dict] = []
        try:
            import yaml
            pi_path = self.config_dir / "procedures" / "procedure_index.yaml"
            if pi_path.exists():
                with open(pi_path) as f:
                    data = yaml.safe_load(f)
                self._procedure_index = data.get("procedures", [])
        except Exception:
            pass

        # Initialize new display panels
        self._contact_scheduler = ContactScheduler()
        self._power_budget_monitor = PowerBudgetMonitor()
        self._fdir_alarm_panel = FDIRAlarmPanel()
        self._procedure_status_panel = ProcedureStatusPanel()
        self._system_overview_dashboard = SystemOverviewDashboard()
        self._procedure_status_panel.load_procedure_index(self._procedure_index)

    def _check_position_access(self, position: str, service: int,
                               subtype: int, data_hex: str) -> tuple[bool, str]:
        """Check if the given position is allowed to send this command.

        Returns (allowed, reason).
        """
        if not self._positions:
            return True, ""  # No position config loaded

        pos_config = self._positions.get(position)
        if pos_config is None:
            return True, ""  # Unknown position, allow by default

        # "all" positions bypass
        if pos_config.allowed_commands == "all":
            return True, ""

        # Check service access
        if pos_config.allowed_services and service not in pos_config.allowed_services:
            return False, f"Service {service} not allowed for position {position}"

        # For S8 commands, also check func_id
        if service == 8 and subtype == 1 and data_hex:
            try:
                data_bytes = bytes.fromhex(data_hex.replace(" ", "").strip())
                if data_bytes:
                    func_id = data_bytes[0]
                    if pos_config.allowed_func_ids and func_id not in pos_config.allowed_func_ids:
                        return False, f"Function {func_id} not allowed for position {position}"
            except (ValueError, IndexError):
                pass

        return True, ""

    # ── Ground-time & orbit ────────────────────────────────────────

    def _init_orbit_propagator(self):
        """Initialise an SGP4 orbit propagator from TLE and ground station config."""
        self._t0_epoch = None
        try:
            import yaml as _yaml
            from smo_common.orbit.propagator import OrbitPropagator, GroundStation

            # Load orbit config
            orbit_path = self.config_dir / "orbit.yaml"
            with open(orbit_path) as f:
                orbit_cfg = _yaml.safe_load(f) or {}
            tle1 = orbit_cfg.get("tle_line1") or orbit_cfg.get("TLE_LINE1", "")
            tle2 = orbit_cfg.get("tle_line2") or orbit_cfg.get("TLE_LINE2", "")
            if not tle1 or not tle2:
                logger.warning("No TLE found in orbit.yaml — orbit propagation disabled")
                return

            # T-0 for MET computation
            self._t0_epoch = orbit_cfg.get("t0_epoch")

            # Load ground stations — try orbit.yaml first, then planning dir
            gs_list = []
            gs_sources = [
                orbit_cfg.get("ground_stations", []),
            ]
            gs_path = self.config_dir / "planning" / "ground_stations.yaml"
            if gs_path.exists():
                with open(gs_path) as f:
                    gs_cfg = _yaml.safe_load(f) or {}
                gs_sources.append(gs_cfg.get("ground_stations", []))

            for gs_set in gs_sources:
                for gs in (gs_set or []):
                    gs_list.append(GroundStation(
                        name=gs.get("name", "GS"),
                        lat_deg=gs["lat_deg"],
                        lon_deg=gs["lon_deg"],
                        alt_km=gs.get("alt_km", 0),
                        min_elevation_deg=gs.get("min_elevation_deg", 5.0),
                    ))

            # De-duplicate by name (orbit.yaml stations take precedence)
            seen = set()
            unique_gs = []
            for gs in gs_list:
                if gs.name not in seen:
                    seen.add(gs.name)
                    unique_gs.append(gs)

            self._orbit_prop = OrbitPropagator(tle1, tle2, ground_stations=unique_gs)
            logger.info("Orbit propagator initialised: %d ground stations", len(unique_gs))
        except Exception as e:
            logger.warning("Orbit propagator init failed: %s", e)

    def get_ground_utc(self) -> datetime:
        """Return the current ground UTC.

        - If a sim epoch was given at startup, ground UTC = epoch + wall-clock
          elapsed (adjusted by speed and manual offset).
        - Otherwise, ground UTC = real UTC + manual offset.
        """
        now_wall = _time_mod.time()
        elapsed = (now_wall - self._ground_start_wall) * self._sim_speed
        if self._ground_epoch:
            return self._ground_epoch + timedelta(seconds=elapsed + self._ground_time_offset)
        return datetime.fromtimestamp(now_wall + self._ground_time_offset, tz=timezone.utc)

    def _compute_orbital_state(self) -> dict:
        """Propagate TLE to current ground UTC and return orbital metadata."""
        if self._orbit_prop is None:
            return {}
        try:
            ground_utc = self.get_ground_utc()
            self._orbit_prop.reset(ground_utc)
            o = self._orbit_prop.state
            return {
                "lat": round(o.lat_deg, 4),
                "lon": round(o.lon_deg, 4),
                "alt_km": round(o.alt_km, 2),
                "in_eclipse": bool(o.in_eclipse),
                "in_contact": bool(o.in_contact),
                "gs_elevation_deg": round(o.gs_elevation_deg, 1),
            }
        except Exception as e:
            logger.debug("Orbit propagation error: %s", e)
            return {}

    def _compute_time_state(self) -> dict:
        """Build time fields for the UI from ground clock and TM."""
        ground_utc = self.get_ground_utc()
        met_s = 0.0
        if self._t0_epoch:
            try:
                t0 = datetime.fromisoformat(
                    self._t0_epoch.replace("Z", "+00:00")
                )
                met_s = (ground_utc - t0).total_seconds()
            except Exception:
                met_s = (_time_mod.time() - self._ground_start_wall) * self._sim_speed
        else:
            met_s = (_time_mod.time() - self._ground_start_wall) * self._sim_speed

        # Spacecraft time from OBDH TM params
        sc_uptime = None
        sc_obc_time_cuc = None
        cache = self._param_cache
        uptime_entry = cache.get(0x0308)
        if uptime_entry:
            sc_uptime = uptime_entry["value"]
        obc_time_entry = cache.get(0x0309)
        if obc_time_entry:
            sc_obc_time_cuc = obc_time_entry["value"]

        # Build SC time ISO string from CUC (Unix epoch seconds)
        sc_time_iso = None
        if sc_obc_time_cuc is not None and sc_obc_time_cuc > 1e9:
            try:
                sc_time_iso = datetime.fromtimestamp(
                    sc_obc_time_cuc, tz=timezone.utc
                ).isoformat()
            except Exception:
                pass

        return {
            "sim_time": ground_utc.isoformat(),
            "sc_time": sc_time_iso,  # from TM param 0x0309 (CUC)
            "sc_obc_time_cuc": sc_obc_time_cuc,
            "met_s": met_s,
            "speed": self._sim_speed,
            "sc_uptime_s": sc_uptime,
        }

    async def start(self) -> None:
        self._running = True
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/ws", self._handle_ws)
        app.router.add_get("/api/displays", self._handle_displays)
        app.router.add_get("/api/state", self._handle_state)
        app.router.add_get("/api/sim-state", self._handle_sim_state)
        app.router.add_post("/api/command", self._handle_command)
        app.router.add_post("/api/pus-command", self._handle_pus_command)
        app.router.add_get("/api/verification-log", self._handle_verification_log)
        app.router.add_get("/api/procedures", self._handle_procedures_list)
        app.router.add_get("/api/procedures/{path:.+}", self._handle_procedure_file)
        app.router.add_get("/api/manual", self._handle_manual_list)
        app.router.add_get("/api/manual/{path:.+}", self._handle_manual_file)
        app.router.add_get("/api/positions", self._handle_positions)
        app.router.add_get("/api/tc-catalog", self._handle_tc_catalog)
        app.router.add_get("/api/param-catalog", self._handle_param_catalog)
        app.router.add_get("/catalog", self._handle_catalog_legacy)
        app.router.add_post("/api/ground-time", self._handle_ground_time_adjust)
        # Procedure execution endpoints
        app.router.add_post("/api/procedure/load", self._handle_proc_load)
        app.router.add_post("/api/procedure/start", self._handle_proc_start)
        app.router.add_post("/api/procedure/pause", self._handle_proc_pause)
        app.router.add_post("/api/procedure/resume", self._handle_proc_resume)
        app.router.add_post("/api/procedure/abort", self._handle_proc_abort)
        app.router.add_post("/api/procedure/step", self._handle_proc_step)
        app.router.add_post("/api/procedure/skip", self._handle_proc_skip)
        app.router.add_get("/api/procedure/status", self._handle_proc_status)
        app.router.add_post("/api/procedure/override-command", self._handle_proc_override)
        app.router.add_get("/api/procedure/activity-types", self._handle_activity_types)
        app.router.add_get("/api/procedure/index", self._handle_proc_index)
        # Procedure builder save/load
        app.router.add_post("/api/procedure/save", self._handle_proc_save)
        app.router.add_get("/api/procedure/custom", self._handle_proc_custom_list)
        # GO/NO-GO coordination
        app.router.add_get("/api/go-nogo/status", self._handle_go_nogo_status)
        app.router.add_post("/api/go-nogo/poll", self._handle_go_nogo_poll)
        app.router.add_post("/api/go-nogo/respond", self._handle_go_nogo_respond)
        # Shift handover
        app.router.add_get("/api/handover", self._handle_handover_get)
        app.router.add_post("/api/handover", self._handle_handover_post)
        # Alarm journal (Feature 2)
        app.router.add_get("/api/alarms", self._handle_alarms_get)
        app.router.add_post("/api/alarms/{alarm_id}/ack", self._handle_alarm_ack)
        # Client config endpoint (planner URL for distributed deployments)
        app.router.add_get("/api/client-config", self._handle_client_config)
        # Contact window timeline proxy (Feature 1)
        app.router.add_get("/api/contacts", self._handle_contacts_proxy)
        # Stored TM playback (Feature 3)
        app.router.add_post("/api/tm-dump", self._handle_tm_dump)
        app.router.add_get("/api/tm-dump-data", self._handle_tm_dump_data)
        # TM archive query endpoints (persistent SQLite)
        app.router.add_get("/api/archive/parameters", self._handle_archive_params)
        app.router.add_get("/api/archive/events", self._handle_archive_events)
        app.router.add_get("/api/archive/alarms", self._handle_archive_alarms)
        app.router.add_get("/api/archive/playback", self._handle_archive_playback)
        # New display panels (Feature 1-6)
        app.router.add_get("/api/displays/contact-schedule", self._handle_contact_schedule)
        app.router.add_get("/api/displays/power-budget", self._handle_power_budget)
        app.router.add_get("/api/displays/fdir-alarms", self._handle_fdir_alarms)
        app.router.add_get("/api/displays/procedure-status", self._handle_procedure_status_display)
        app.router.add_get("/api/displays/system-overview", self._handle_system_overview)
        app.router.add_post("/api/displays/alarms/{alarm_id}/ack", self._handle_alarm_ack_display)
        app.router.add_post("/api/displays/alarm-trends", self._handle_alarm_trends)
        static_dir = Path(__file__).parent / "static"
        # Serve the top-level vendor directory directly so that
        # /static/vendor/ requests resolve even when the symlink
        # inside static/ points to an absolute path that doesn't
        # exist in the current environment.
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
        logger.info("MCS server started on port %d", self.http_port)

        # Start command queue processor as a background task
        asyncio.create_task(self._command_processor())

        # Start state polling, TM receive, and TC connection in parallel
        await asyncio.gather(
            self._tm_receive_loop(),
            self._state_poll_loop(),
            self._tc_connect_loop(),
        )

    async def _command_processor(self):
        """Serialize all TC commands through a single queue for FIFO ordering."""
        while True:
            cmd_coro = await self._cmd_queue.get()
            try:
                await cmd_coro
            except Exception as e:
                logger.error("Command queue error: %s", e)
            finally:
                self._cmd_queue.task_done()

    async def _tc_connect_loop(self):
        """Maintain persistent TCP connection to simulator TC port."""
        while self._running:
            try:
                _, writer = await asyncio.open_connection(
                    self.connect_host, self.tc_port)
                self._tc_writer = writer
                logger.info("Connected to TC port at %s:%d",
                            self.connect_host, self.tc_port)
                while self._running:
                    if writer.is_closing():
                        break
                    await asyncio.sleep(2)
            except Exception as e:
                logger.warning("TC connection error: %s, reconnecting...", e)
                self._tc_writer = None
                await asyncio.sleep(3)

    async def _tm_receive_loop(self):
        while self._running:
            try:
                reader, writer = await asyncio.open_connection(
                    self.connect_host, self.connect_port)
                logger.info("Connected to TM source at %s:%d",
                            self.connect_host, self.connect_port)
                while self._running:
                    pkt = await read_framed_packet(reader)
                    if pkt is None:
                        break
                    parsed = decommutate_packet(pkt)
                    if parsed and parsed.secondary:
                        await self._process_tm(parsed)
            except Exception as e:
                logger.warning("TM connection error: %s, reconnecting...", e)
                await asyncio.sleep(2)

    async def _process_tm(self, pkt) -> None:
        # Update last TM frame timestamp for staleness tracking
        now = _time_mod.time()

        info = {
            "service": pkt.secondary.service,
            "subtype": pkt.secondary.subtype,
            "apid": pkt.primary.apid,
            "seq": pkt.primary.sequence_count,
            "data_len": len(pkt.data_field),
        }

        # ── REAL HK DECODE ────────────────────────────────────────────
        # Hand the raw S3.25 payload to TMProcessor which inverts the
        # engine's HK packing using hk_structures.yaml. Every decoded
        # parameter goes into _param_cache with the receive timestamp.
        # This is the ONLY path by which spacecraft state reaches the MCS.
        if pkt.secondary.service == 3 and pkt.secondary.subtype == 25:
            # Any S3.25 frame proves the link is alive, even if its SID is
            # unknown to us — bump the liveness timer unconditionally.
            async with self._param_cache_lock:
                self._last_tm_frame_ts = now
            decoded = self._tm_processor._process_hk(pkt.data_field)
            if decoded and "params" in decoded:
                async with self._param_cache_lock:
                    for pid, value in decoded["params"].items():
                        self._param_cache[int(pid)] = {
                            "value": float(value),
                            "last_update_ts": now,
                            "sid": int(decoded.get("sid", 0)),
                        }
                info["hk_decoded"] = {
                    "sid": decoded.get("sid"),
                    "param_count": len(decoded["params"]),
                }
            else:
                info["hk_decoded"] = {
                    "sid": decoded.get("sid") if decoded else None,
                    "param_count": 0,
                    "unknown_sid": True,
                }
        elif pkt.secondary.service == 20 and pkt.secondary.subtype == 2 \
                and len(pkt.data_field) >= 7:
            # S20.2 Parameter Value Report: param_id (H), type (B), value (f)
            try:
                pid = struct.unpack('>H', pkt.data_field[:2])[0]
                value = struct.unpack('>f', pkt.data_field[3:7])[0]
                async with self._param_cache_lock:
                    self._last_tm_frame_ts = now
                    self._param_cache[int(pid)] = {
                        "value": float(value),
                        "last_update_ts": now,
                        "sid": -1,  # not from a SID; on-demand S20 read
                    }
                info["s20_param"] = {"id": pid, "value": value}
            except Exception:
                pass
        else:
            # Any other TM packet still updates the link liveness timer.
            async with self._param_cache_lock:
                self._last_tm_frame_ts = now

        # Parse S1 verification TM
        if pkt.secondary.service == 1 and len(pkt.data_field) >= 4:
            request_id = struct.unpack('>I', pkt.data_field[:4])[0]
            tc_seq = request_id & 0x3FFF
            error_code = 0
            if pkt.secondary.subtype in (2, 8) and len(pkt.data_field) >= 6:
                error_code = struct.unpack('>H', pkt.data_field[4:6])[0]

            verif = {
                "tc_seq": tc_seq,
                "subtype": pkt.secondary.subtype,
                "error_code": error_code,
            }
            info["verification"] = verif

            for entry in self._verification_log:
                if entry.get("seq") == tc_seq:
                    if pkt.secondary.subtype == 1:
                        entry["state"] = "ACCEPTED"
                    elif pkt.secondary.subtype == 2:
                        entry["state"] = "REJECTED"
                        entry["error_code"] = error_code
                    elif pkt.secondary.subtype == 7:
                        entry["state"] = "COMPLETED"
                    elif pkt.secondary.subtype == 8:
                        entry["state"] = "FAILED"
                        entry["error_code"] = error_code
                    break

        # (S3.25 housekeeping and S20.2 parameter reports are now decoded
        # above into _param_cache via TMProcessor — no separate metadata
        # logging block needed.)

        # Parse S5 events
        if pkt.secondary.service == 5 and len(pkt.data_field) >= 3:
            event_id = struct.unpack('>H', pkt.data_field[:2])[0]
            severity = pkt.data_field[2]
            aux_text = ""
            if len(pkt.data_field) >= 7:
                cuc_time = struct.unpack('>I', pkt.data_field[3:7])[0]
                if len(pkt.data_field) > 7:
                    text_len = pkt.data_field[7]
                    if len(pkt.data_field) >= 8 + text_len:
                        aux_text = pkt.data_field[8:8+text_len].decode(
                            'ascii', errors='ignore'
                        )
            info["event"] = {
                "event_id": event_id,
                "severity": severity,
                "description": aux_text,
            }

        # Parse S12 monitoring transition reports
        if pkt.secondary.service == 12 and pkt.secondary.subtype in (9, 10):
            if len(pkt.data_field) >= 6:
                param_id = struct.unpack('>H', pkt.data_field[:2])[0]
                value = struct.unpack('>f', pkt.data_field[2:6])[0]
                info["monitoring"] = {
                    "param_id": param_id,
                    "value": value,
                    "transition": "out_of_limits" if pkt.secondary.subtype == 9 else "back_to_nominal",
                }

        # -- Alarm journal: capture S5 events (severity >= 3) and S12 OOL --
        if pkt.secondary.service == 5 and info.get("event"):
            evt = info["event"]
            # Archive every S5 event
            try:
                self._archive.store_event(
                    evt["event_id"], evt.get("severity", 0),
                    self._event_id_to_subsystem(evt["event_id"]),
                    evt.get("description", ""),
                )
            except Exception:
                pass

            if evt.get("severity", 0) >= 3:
                self._alarm_id_counter += 1
                alarm = {
                    "id": self._alarm_id_counter,
                    "timestamp": _time_mod.time(),
                    "severity": evt["severity"],
                    "subsystem": self._event_id_to_subsystem(evt["event_id"]),
                    "parameter": f"EVT-{evt['event_id']}",
                    "value": evt.get("description", ""),
                    "limit": "",
                    "acknowledged": False,
                    "source": "S5",
                }
                self._alarm_journal.appendleft(alarm)
                self._fdir_alarm_panel.add_alarm(alarm)  # Feed to FDIR panel
                try:
                    self._archive.store_alarm(alarm)
                except Exception:
                    pass
                # Broadcast alarm to all WS clients
                alarm_msg = json.dumps({"type": "alarm", "alarm": alarm})
                async with self._ws_lock:
                    clients = list(self._ws_clients)
                for ws, _pos in clients:
                    try:
                        await ws.send_str(alarm_msg)
                    except Exception:
                        pass

        if pkt.secondary.service == 12 and pkt.secondary.subtype == 9:
            mon = info.get("monitoring")
            if mon:
                self._alarm_id_counter += 1
                alarm = {
                    "id": self._alarm_id_counter,
                    "timestamp": _time_mod.time(),
                    "severity": 3,
                    "subsystem": self._param_id_to_subsystem(mon["param_id"]),
                    "parameter": f"0x{mon['param_id']:04X}",
                    "value": str(round(mon["value"], 4)),
                    "limit": "OOL",
                    "acknowledged": False,
                    "source": "S12",
                }
                self._alarm_journal.appendleft(alarm)
                self._fdir_alarm_panel.add_alarm(alarm)  # Feed to FDIR panel
                try:
                    self._archive.store_alarm(alarm)
                except Exception:
                    pass
                alarm_msg = json.dumps({"type": "alarm", "alarm": alarm})
                async with self._ws_lock:
                    clients = list(self._ws_clients)
                for ws, _pos in clients:
                    try:
                        await ws.send_str(alarm_msg)
                    except Exception:
                        pass

        # Broadcast to WebSocket clients
        msg = json.dumps({"type": "tm", "packet": info})
        async with self._ws_lock:
            clients = list(self._ws_clients)
        disconnected = []
        for ws, _pos in clients:
            try:
                await ws.send_str(msg)
            except Exception:
                disconnected.append((ws, _pos))
        if disconnected:
            async with self._ws_lock:
                self._ws_clients = [
                    c for c in self._ws_clients if c not in disconnected
                ]

    async def _state_poll_loop(self):
        """Periodically build state from param_cache and broadcast.

        Parameters are populated only from TM TCP socket HK packets.
        If no TM frame received in 60s, mark state as stale.
        """
        while self._running:
            try:
                now = _time_mod.time()
                async with self._param_cache_lock:
                    cache = dict(self._param_cache)  # Snapshot
                    last_tm_ts = self._last_tm_frame_ts

                # Flat by-ID dict (hex string keys for JSON friendliness)
                params_flat: dict[str, float] = {}
                for pid, entry in cache.items():
                    params_flat[f"0x{pid:04X}"] = entry["value"]

                # Subsystem-grouped view with UI-friendly field names.
                # Built from _param_meta (parameters.yaml) then translated
                # via _UI_KEY_MAP so the frontend can bind by its expected
                # names (e.g., soc_pct, rssi_dbm, temp_obc_C).
                grouped: dict[str, dict] = {}
                for pid, entry in cache.items():
                    meta = self._param_meta.get(pid)
                    if not meta:
                        continue
                    sub = meta["subsystem"] or "other"
                    short = meta["short_key"] or f"p_{pid:04x}"
                    ui_key = _UI_KEY_MAP.get(short, short)
                    grouped.setdefault(sub, {})[ui_key] = entry["value"]
                    # Also keep the original key so param lookups still work
                    if ui_key != short:
                        grouped[sub][short] = entry["value"]

                # Compute liveness/staleness
                if last_tm_ts is None:
                    stale = True
                    age_s: Optional[float] = None
                else:
                    age_s = now - last_tm_ts
                    stale = age_s > 60.0

                data: dict = {
                    "stale": stale,
                    "last_frame_age_s": age_s,
                    "params": params_flat,
                    "param_count": len(cache),
                }
                # Merge grouped subsystem dicts at top level for legacy bindings
                data.update(grouped)
                # Ground-system computed data: orbit from TLE propagation,
                # time from ground clock, contact from orbital geometry.
                data.update(self._compute_orbital_state())
                data.update(self._compute_time_state())

                self._latest_state = data
                msg = json.dumps({
                    "type": "state",
                    "data": self._latest_state,
                })
                async with self._ws_lock:
                    clients = list(self._ws_clients)
                disconnected = []
                for ws, _pos in clients:
                    try:
                        await ws.send_str(msg)
                    except Exception:
                        disconnected.append((ws, _pos))
                if disconnected:
                    async with self._ws_lock:
                        self._ws_clients = [
                            c for c in self._ws_clients
                            if c not in disconnected
                        ]

                # Archive parameters every 10 polls (~10 s)
                self._archive_tick += 1
                if self._archive_tick % 10 == 0:
                    self._archive_state_snapshot(data)
            except Exception as e:
                logger.warning("State poll loop error: %s", e)
            await asyncio.sleep(1.0)

    async def _handle_sim_state(self, request):
        return web.json_response(self._latest_state)

    async def _handle_ground_time_adjust(self, request):
        """Adjust MCS ground time.

        POST /api/ground-time
        Body: {"offset_s": <float>}     — add seconds to ground time
          or: {"epoch": "<ISO 8601>"}    — reset ground time to new epoch
          or: {"speed": <float>}         — set simulation speed multiplier
        """
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        if "offset_s" in body:
            self._ground_time_offset += float(body["offset_s"])
        if "epoch" in body:
            self._ground_epoch = datetime.fromisoformat(
                body["epoch"].replace("Z", "+00:00"))
            self._ground_start_wall = _time_mod.time()
            self._ground_time_offset = 0.0
        if "speed" in body:
            self._sim_speed = float(body["speed"])
        return web.json_response({
            "ground_utc": self.get_ground_utc().isoformat(),
            "speed": self._sim_speed,
            "offset_s": self._ground_time_offset,
        })

    def _archive_state_snapshot(self, data: dict) -> None:
        """Extract key parameters from a state poll and store them."""
        params: dict[str, float] = {}
        for subsys in ("eps", "aocs", "tcs", "ttc", "payload", "obdh"):
            sub = data.get(subsys)
            if not isinstance(sub, dict):
                continue
            for key, val in sub.items():
                if isinstance(val, (int, float)):
                    params[f"{subsys}.{key}"] = float(val)
        if params:
            try:
                self._archive.store_parameters(params)
            except Exception as e:
                logger.debug("Archive store error: %s", e)

    # ── TM archive query endpoints ────────────────────────────────

    async def _handle_archive_params(self, request):
        """GET /api/archive/parameters?name=eps.bat_soc&start=<epoch>&end=<epoch>&limit=3600"""
        name = request.query.get("name", "")
        if not name:
            return web.json_response({"error": "name parameter required"}, status=400)
        start = float(request.query["start"]) if "start" in request.query else None
        end = float(request.query["end"]) if "end" in request.query else None
        limit = int(request.query.get("limit", 3600))
        data = self._archive.query_parameters(name, start, end, limit)
        return web.json_response({"param_name": name, "data": data})

    async def _handle_archive_events(self, request):
        """GET /api/archive/events?start=<epoch>&end=<epoch>&severity=0&limit=500"""
        start = float(request.query["start"]) if "start" in request.query else None
        end = float(request.query["end"]) if "end" in request.query else None
        sev = int(request.query.get("severity", 0))
        limit = int(request.query.get("limit", 500))
        data = self._archive.query_events(start, end, sev, limit)
        return web.json_response({"events": data})

    async def _handle_archive_alarms(self, request):
        """GET /api/archive/alarms?start=<epoch>&end=<epoch>&limit=500"""
        start = float(request.query["start"]) if "start" in request.query else None
        end = float(request.query["end"]) if "end" in request.query else None
        limit = int(request.query.get("limit", 500))
        data = self._archive.query_alarms(start, end, limit)
        return web.json_response({"alarms": data})

    async def _handle_archive_playback(self, request):
        """GET /api/archive/playback?subsystem=eps&start=<epoch>&end=<epoch>"""
        subsystem = request.query.get("subsystem", "")
        if not subsystem:
            return web.json_response({"error": "subsystem parameter required"}, status=400)
        start = float(request.query["start"]) if "start" in request.query else None
        end = float(request.query["end"]) if "end" in request.query else None
        data = self._archive.get_playback_data(subsystem, start, end)
        return web.json_response({"subsystem": subsystem, "data": data})

    async def _handle_command(self, request):
        """Deprecated endpoint. All commands must use /api/pus-command (TC TCP socket).

        This HTTP endpoint is disabled to enforce RF link gating and command logging.
        The MCS sends all telecommands via the TC TCP socket (port 8001), not HTTP.
        """
        return web.json_response(
            {"status": "gone", "message":
             "Endpoint /api/command is no longer available. "
             "Use /api/pus-command to send telecommands via the TC TCP socket."},
            status=410,
        )

    async def _handle_pus_command(self, request):
        """Build and send a real ECSS PUS TC packet to the simulator."""
        try:
            body = await request.json()
            service = int(body.get("service", 0))
            subtype = int(body.get("subtype", 0))
            data_hex = body.get("data_hex", "")
            name = body.get("name", f"S{service}.{subtype}")
            position = body.get("position", "flight_director")

            # Position-based access control
            allowed, reason = self._check_position_access(
                position, service, subtype, data_hex
            )
            if not allowed:
                return web.json_response(
                    {"status": "denied", "message": reason}, status=403
                )

            # Sanitise hex string: strip whitespace, ensure even length
            clean_hex = data_hex.replace(" ", "").strip() if data_hex else ""
            data = bytes.fromhex(clean_hex) if clean_hex else b''
            pkt = self._tc_manager.build_command(service, subtype, data)
            seq = self._tc_manager._seq_count

            # Track in verification log
            import time
            entry = {
                "seq": seq,
                "name": name,
                "service": service,
                "subtype": subtype,
                "state": "SENT",
                "timestamp": time.time(),
                "error_code": 0,
                "position": position,
            }
            self._verification_log.appendleft(entry)
            self._tc_manager.track_verification(seq, name)

            # Send over TC TCP connection
            if self._tc_writer is None or self._tc_writer.is_closing():
                return web.json_response(
                    {"status": "error", "message": "No TC connection"},
                    status=503,
                )

            framed = frame_packet(pkt)
            async with self._tc_send_lock:
                self._tc_writer.write(framed)
                await self._tc_writer.drain()

            return web.json_response({"status": "sent", "seq": seq})
        except Exception as e:
            logger.warning("PUS command error: %s", e)
            return web.json_response(
                {"status": "error", "message": str(e)}, status=400
            )

    async def _handle_verification_log(self, request):
        return web.json_response({"log": list(self._verification_log)})

    async def _handle_index(self, request):
        index_file = Path(__file__).parent / "static" / "index.html"
        if index_file.exists():
            return web.FileResponse(index_file)
        return web.Response(
            text="<h1>SMO MCS — static/index.html not found</h1>",
            content_type="text/html",
        )

    async def _handle_ws(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # Position from query param: /ws?position=eps_tcs
        position = request.query.get("position", "flight_director")
        async with self._ws_lock:
            self._ws_clients.append((ws, position))
        try:
            async for msg in ws:
                # Handle incoming WS messages (e.g., position change)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        if data.get("type") == "set_position":
                            new_pos = data.get("position", "flight_director")
                            async with self._ws_lock:
                                for i, (w, p) in enumerate(self._ws_clients):
                                    if w is ws:
                                        self._ws_clients[i] = (ws, new_pos)
                                        break
                        elif data.get("type") == "go_nogo_response":
                            # Handle inline GO/NO-GO response via WS
                            go_pos = data.get("position", position)
                            go_resp = data.get("response", "").upper()
                            status_msg = None
                            async with self._go_nogo_lock:
                                if (self._go_nogo_active
                                        and go_resp in ("GO", "NOGO", "STANDBY")):
                                    self._go_nogo_responses[go_pos] = go_resp
                                    status_msg = json.dumps({
                                        "type": "go_nogo_status",
                                        "active": True,
                                        "label": self._go_nogo_label,
                                        "responses": dict(self._go_nogo_responses),
                                    })
                            if status_msg is not None:
                                async with self._ws_lock:
                                    clients = list(self._ws_clients)
                                for w, _p in clients:
                                    try:
                                        await w.send_str(status_msg)
                                    except Exception:
                                        pass
                    except (json.JSONDecodeError, KeyError):
                        pass
        finally:
            async with self._ws_lock:
                self._ws_clients = [
                    (w, p) for w, p in self._ws_clients if w is not ws
                ]
        return ws

    async def _handle_displays(self, request):
        return web.json_response(
            self._displays.model_dump()
            if hasattr(self._displays, 'model_dump')
            else {}
        )

    async def _handle_state(self, request):
        return web.json_response(self._latest_state)

    async def _handle_positions(self, request):
        """Return available positions and their access control config."""
        result = {}
        for name, pos in self._positions.items():
            result[name] = {
                "label": pos.label or pos.display_name,
                "display_name": pos.display_name,
                "subsystems": pos.subsystems,
                "allowed_commands": pos.allowed_commands,
                "allowed_services": pos.allowed_services,
                "allowed_func_ids": pos.allowed_func_ids,
                "visible_tabs": pos.visible_tabs,
                "overview_subsystems": pos.overview_subsystems,
                "manual_sections": pos.manual_sections,
            }
        return web.json_response({"positions": result})

    async def _handle_param_catalog(self, request):
        """Return the full parameter catalog with HK/on-demand classification.

        Each entry includes: id, name, subsystem, units, description, plus
        ``in_hk`` (bool — appears in at least one HK structure) and
        ``on_demand`` (bool — explicitly tagged for S20.3 one-shot fetch).
        Used by the MCS ON-DEMAND TM tab to let operators pick diagnostic
        parameters that are not part of any periodic HK packet and request
        a live value via PUS Service 20 subtype 3.
        """
        try:
            import yaml  # local import; config layer already uses yaml
            params_path = self.config_dir / "telemetry" / "parameters.yaml"
            hk_path = self.config_dir / "telemetry" / "hk_structures.yaml"
            with open(params_path) as f:
                pdata = yaml.safe_load(f) or {}
            with open(hk_path) as f:
                hkdata = yaml.safe_load(f) or {}
            hk_ids: set[int] = set()
            for s in hkdata.get("structures", []) or []:
                for pp in s.get("parameters", []) or []:
                    pid_raw = pp.get("param_id")
                    try:
                        hk_ids.add(int(pid_raw) if isinstance(pid_raw, int)
                                   else int(str(pid_raw), 0))
                    except Exception:
                        pass
            out = []
            for p in pdata.get("parameters", []) or []:
                try:
                    pid = int(p["id"]) if isinstance(p["id"], int) else int(str(p["id"]), 0)
                except Exception:
                    continue
                out.append({
                    "id": pid,
                    "hex": f"0x{pid:04X}",
                    "name": p.get("name", ""),
                    "subsystem": p.get("subsystem", ""),
                    "units": p.get("units", ""),
                    "description": p.get("description", ""),
                    "in_hk": pid in hk_ids,
                    "on_demand": bool(p.get("on_demand", False)),
                    "stub": bool(p.get("stub", False)),
                })
            return web.json_response({"parameters": out})
        except Exception as e:
            logger.exception("param-catalog error: %s", e)
            return web.json_response({"parameters": [], "error": str(e)},
                                     status=500)

    async def _handle_tc_catalog(self, request):
        """Return TC catalog with command metadata."""
        position = request.query.get("position", "flight_director")
        commands = []
        for cmd in self._tc_catalog:
            cmd_dict = cmd.model_dump() if hasattr(cmd, 'model_dump') else {}
            # Filter by position access
            service = cmd_dict.get("service", 0)
            subtype = cmd_dict.get("subtype", 0)
            func_id = cmd_dict.get("func_id")
            data_hex = f"{func_id:02X}" if func_id is not None else ""
            allowed, _ = self._check_position_access(
                position, service, subtype, data_hex
            )
            cmd_dict["allowed"] = allowed
            commands.append(cmd_dict)
        return web.json_response({"commands": commands})

    async def _handle_catalog_legacy(self, request):
        """Legacy /catalog endpoint for backward compatibility with HTML pages.

        Returns catalog in format expected by sys.html and other display pages:
        {
          "tc": [list of commands with label, position, service, subtype, fields],
          "failures": {subsystem: [mode1, mode2, ...]}
        }
        """
        position = request.query.get("pos", "sys")

        # Build TC catalog in legacy format
        tc_list = []
        for cmd in self._tc_catalog:
            cmd_dict = cmd.model_dump() if hasattr(cmd, 'model_dump') else {}
            # Only include commands accessible to this position
            service = cmd_dict.get("service", 0)
            subtype = cmd_dict.get("subtype", 0)
            func_id = cmd_dict.get("func_id")
            data_hex = f"{func_id:02X}" if func_id is not None else ""
            allowed, _ = self._check_position_access(position, service, subtype, data_hex)
            if allowed:
                tc_item = {
                    "label": cmd_dict.get("name", f"S{service}.{subtype}"),
                    "position": cmd_dict.get("position", "sys"),
                    "service": service,
                    "subtype": subtype,
                    "fields": cmd_dict.get("fields", []),
                }
                tc_list.append(tc_item)

        # Build failures map — common spacecraft subsystem failures
        failures_map = {
            "aocs": ["st1_failure", "st2_failure", "rw1_failure", "rw2_failure", "rw3_failure", "rw4_failure", "mtq_failure", "css_failure"],
            "eps": ["battery_failure", "sa_deployment_failure", "regulator_failure", "pcu_failure"],
            "ttc": ["rx_failure", "tx_failure", "antenna_failure", "transponder_failure"],
            "obdh": ["cpu_failure", "memory_failure", "bus_failure", "watchdog_failure"],
            "payload": ["ccd_failure", "cooler_failure", "memory_failure"],
            "tcs": ["heater_failure", "radiator_failure", "thermostat_failure"],
        }

        return web.json_response({
            "tc": tc_list,
            "failures": failures_map,
        })

    # ── Procedure runner helpers ────────────────────────────────────

    async def _proc_send_command(
        self, service: int, subtype: int, data_hex: str = ""
    ) -> dict:
        """Send a PUS command on behalf of the procedure runner."""
        import time as _time
        clean_hex = data_hex.replace(" ", "").strip() if data_hex else ""
        data = bytes.fromhex(clean_hex) if clean_hex else b""
        pkt = self._tc_manager.build_command(service, subtype, data)
        seq = self._tc_manager._seq_count

        entry = {
            "seq": seq,
            "name": f"PROC:S{service}.{subtype}",
            "service": service,
            "subtype": subtype,
            "state": "SENT",
            "timestamp": _time.time(),
            "error_code": 0,
            "position": "procedure_runner",
        }
        self._verification_log.appendleft(entry)
        self._tc_manager.track_verification(seq, entry["name"])

        if self._tc_writer is None or self._tc_writer.is_closing():
            return {"status": "error", "message": "No TC connection"}

        framed = frame_packet(pkt)
        async with self._tc_send_lock:
            self._tc_writer.write(framed)
            await self._tc_writer.drain()
        return {"status": "sent", "seq": seq}

    def _proc_get_telemetry(self, param_path: str) -> Any:
        """Read a telemetry value by dot-path from the latest state."""
        state = self._latest_state
        if not state:
            return None
        # Try direct subsystem.param lookup in latest state dict
        parts = param_path.split(".", 1)
        if len(parts) == 2:
            subsystem, param = parts
            sub_data = state.get(subsystem, {})
            if isinstance(sub_data, dict):
                return sub_data.get(param)
        return state.get(param_path)

    # ── Procedure API handlers ───────────────────────────────────────

    async def _handle_proc_load(self, request):
        """Load a procedure by activity type name or direct command sequence."""
        try:
            body = await request.json()
            name = body.get("name", "")
            steps = body.get("steps")
            procedure_ref = body.get("procedure_ref", "")
            step_by_step = body.get("step_by_step", False)

            # If steps not provided, look up by activity type name
            if steps is None:
                for at in self._activity_types:
                    if at["name"] == name:
                        steps = at.get("command_sequence", [])
                        procedure_ref = procedure_ref or at.get("procedure_ref", "")
                        break
                if steps is None:
                    return web.json_response(
                        {"error": f"Activity type '{name}' not found"}, status=404
                    )

            result = self._procedure_runner.load(
                name, steps, procedure_ref=procedure_ref,
                step_by_step=step_by_step,
            )
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_proc_start(self, request):
        result = await self._procedure_runner.start()
        return web.json_response(result)

    async def _handle_proc_pause(self, request):
        result = await self._procedure_runner.pause()
        return web.json_response(result)

    async def _handle_proc_resume(self, request):
        result = await self._procedure_runner.resume()
        return web.json_response(result)

    async def _handle_proc_abort(self, request):
        result = await self._procedure_runner.abort()
        return web.json_response(result)

    async def _handle_proc_step(self, request):
        result = await self._procedure_runner.step_advance()
        return web.json_response(result)

    async def _handle_proc_skip(self, request):
        result = await self._procedure_runner.skip_step()
        return web.json_response(result)

    async def _handle_proc_status(self, request):
        return web.json_response(self._procedure_runner.status())

    async def _handle_proc_override(self, request):
        try:
            body = await request.json()
            service = int(body.get("service", 0))
            subtype = int(body.get("subtype", 0))
            data_hex = body.get("data_hex", "")
            result = await self._procedure_runner.override_command(
                service, subtype, data_hex
            )
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_activity_types(self, request):
        return web.json_response({"activity_types": self._activity_types})

    async def _handle_proc_index(self, request):
        return web.json_response({"procedures": self._procedure_index})

    # ── Shift handover ───────────────────────────────────────────────

    async def _handle_handover_get(self, request):
        return web.json_response({"notes": self._handover_log})

    async def _handle_handover_post(self, request):
        import time as _time
        try:
            body = await request.json()
            note = body.get("note", "").strip()
            position = body.get("position", "unknown")
            if not note:
                return web.json_response(
                    {"error": "Note text required"}, status=400)
            entry = {
                "timestamp": _time.time(),
                "position": position,
                "note": note,
            }
            self._handover_log.append(entry)
            return web.json_response({"status": "ok", "entry": entry})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    def _scan_docs(self, base_dir: Path) -> list[dict]:
        """Scan a directory tree for markdown files."""
        entries = []
        if not base_dir.exists():
            return entries
        for md_file in sorted(base_dir.rglob("*.md")):
            rel = md_file.relative_to(base_dir)
            category = rel.parent.name if rel.parent != rel.parent.parent else "general"
            entries.append({
                "category": category or "general",
                "name": md_file.stem.replace("_", " ").title(),
                "path": str(rel),
                "filename": md_file.name,
            })
        return entries

    async def _handle_procedures_list(self, request):
        proc_dir = self.config_dir / "procedures"
        entries = self._scan_docs(proc_dir)
        return web.json_response({"procedures": entries})

    async def _handle_procedure_file(self, request):
        rel_path = request.match_info["path"]
        proc_dir = self.config_dir / "procedures"
        target = (proc_dir / rel_path).resolve()
        if not str(target).startswith(str(proc_dir.resolve())):
            return web.json_response({"error": "Invalid path"}, status=403)
        if not target.exists() or not target.is_file():
            return web.json_response({"error": "Not found"}, status=404)
        content = target.read_text(encoding="utf-8")
        title = (
            content.split("\n", 1)[0].lstrip("# ").strip()
            if content
            else rel_path
        )
        return web.json_response({"title": title, "content_md": content})

    async def _handle_manual_list(self, request):
        manual_dir = self.config_dir / "manual"
        entries = self._scan_docs(manual_dir)
        return web.json_response({"pages": entries})

    async def _handle_manual_file(self, request):
        rel_path = request.match_info["path"]
        manual_dir = self.config_dir / "manual"
        target = (manual_dir / rel_path).resolve()
        if not str(target).startswith(str(manual_dir.resolve())):
            return web.json_response({"error": "Invalid path"}, status=403)
        if not target.exists() or not target.is_file():
            return web.json_response({"error": "Not found"}, status=404)
        content = target.read_text(encoding="utf-8")
        title = (
            content.split("\n", 1)[0].lstrip("# ").strip()
            if content
            else rel_path
        )
        return web.json_response({"title": title, "content_md": content})

    # ── Procedure builder save/load ──────────────────────────────────

    async def _handle_proc_save(self, request):
        """Save a custom procedure built in the procedure builder."""
        import yaml
        try:
            body = await request.json()
            name = body.get("name", "").strip()
            steps = body.get("steps", [])
            description = body.get("description", "")
            position = body.get("position", "unknown")
            if not name:
                return web.json_response(
                    {"error": "Procedure name required"}, status=400)

            custom_dir = self.config_dir / "procedures" / "custom"
            custom_dir.mkdir(parents=True, exist_ok=True)

            # Sanitize filename
            safe_name = "".join(
                c if c.isalnum() or c in "-_" else "_"
                for c in name.lower().replace(" ", "_")
            )
            filepath = custom_dir / f"{safe_name}.yaml"

            proc_data = {
                "name": name,
                "description": description,
                "created_by": position,
                "steps": steps,
            }
            with open(filepath, "w") as f:
                yaml.dump(proc_data, f, default_flow_style=False)

            return web.json_response({
                "status": "saved",
                "path": f"custom/{safe_name}.yaml",
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_proc_custom_list(self, request):
        """List custom procedures."""
        custom_dir = self.config_dir / "procedures" / "custom"
        entries = []
        if custom_dir.exists():
            import yaml
            for f in sorted(custom_dir.glob("*.yaml")):
                try:
                    with open(f) as fh:
                        data = yaml.safe_load(fh)
                    entries.append({
                        "name": data.get("name", f.stem),
                        "description": data.get("description", ""),
                        "path": f"custom/{f.name}",
                        "steps": data.get("steps", []),
                        "created_by": data.get("created_by", "unknown"),
                    })
                except Exception:
                    pass
        return web.json_response({"procedures": entries})

    # ── Alarm journal helpers ────────────────────────────────────────

    @staticmethod
    def _event_id_to_subsystem(event_id: int) -> str:
        """Map event ID range to subsystem name."""
        if 0x0100 <= event_id <= 0x01FF:
            return "eps"
        elif 0x0200 <= event_id <= 0x02FF:
            return "aocs"
        elif 0x0300 <= event_id <= 0x03FF:
            return "obdh"
        elif 0x0400 <= event_id <= 0x04FF:
            return "tcs"
        elif 0x0500 <= event_id <= 0x05FF:
            return "ttc"
        elif 0x0600 <= event_id <= 0x06FF:
            return "payload"
        return "unknown"

    @staticmethod
    def _param_id_to_subsystem(param_id: int) -> str:
        """Map parameter ID range to subsystem name."""
        if 0x0100 <= param_id <= 0x01FF:
            return "eps"
        elif 0x0200 <= param_id <= 0x02FF:
            return "aocs"
        elif 0x0300 <= param_id <= 0x03FF:
            return "obdh"
        elif 0x0400 <= param_id <= 0x04FF:
            return "tcs"
        elif 0x0500 <= param_id <= 0x05FF:
            return "ttc"
        elif 0x0600 <= param_id <= 0x06FF:
            return "payload"
        return "unknown"

    # ── Alarm journal API handlers ────────────────────────────────────

    async def _handle_alarms_get(self, request):
        """Return alarm journal entries, optionally filtered."""
        subsystem = request.query.get("subsystem")
        severity = request.query.get("severity")
        alarms = list(self._alarm_journal)
        if subsystem:
            alarms = [a for a in alarms if a["subsystem"] == subsystem]
        if severity:
            try:
                sev_min = int(severity)
                alarms = [a for a in alarms if a["severity"] >= sev_min]
            except ValueError:
                pass
        return web.json_response({"alarms": alarms})

    async def _handle_alarm_ack(self, request):
        """Acknowledge an alarm by ID."""
        try:
            alarm_id = int(request.match_info["alarm_id"])
            for alarm in self._alarm_journal:
                if alarm["id"] == alarm_id:
                    alarm["acknowledged"] = True
                    return web.json_response({
                        "status": "acknowledged", "id": alarm_id
                    })
            return web.json_response(
                {"error": f"Alarm {alarm_id} not found"}, status=404
            )
        except (ValueError, KeyError) as e:
            return web.json_response({"error": str(e)}, status=400)

    # ── Client config (distributed deployment) ──────────────────────

    async def _handle_client_config(self, request):
        """Return runtime config for the browser client."""
        return web.json_response({
            "planner_url": self._planner_api_base,
        })

    # ── Contact window timeline proxy (Feature 1) ────────────────────

    async def _handle_contacts_proxy(self, request):
        """Proxy contact window data from the planner API."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self._planner_api_base}/api/contacts"
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return web.json_response(data)
                    return web.json_response(
                        {"contacts": [], "error": f"Planner returned {resp.status}"},
                        status=resp.status,
                    )
        except Exception as e:
            # Planner not available — return synthetic data from state
            contacts = self._build_synthetic_contacts()
            return web.json_response({"contacts": contacts, "synthetic": True})

    def _build_synthetic_contacts(self) -> list[dict]:
        """Build synthetic contact windows from current state for when
        the planner is not available."""
        contacts = []
        now = _time_mod.time()
        gs_list = [
            {"name": "Iqaluit", "color": "#3b82f6"},
            {"name": "Troll", "color": "#22c55e"},
        ]
        # Generate 6 synthetic passes over 24h
        for i, gs in enumerate(gs_list):
            for j in range(3):
                aos = now + (j * 4 + i * 2) * 3600 + 1800
                los = aos + 600 + j * 120
                contacts.append({
                    "ground_station": gs["name"],
                    "color": gs["color"],
                    "aos_utc": aos,
                    "los_utc": los,
                    "max_elevation_deg": 25 + j * 15 + i * 10,
                    "duration_s": los - aos,
                })
        contacts.sort(key=lambda c: c["aos_utc"])
        return contacts

    # ── Stored TM dump (Feature 3) ────────────────────────────────────

    async def _handle_tm_dump(self, request):
        """Send S15.9 dump command and return acknowledgment."""
        try:
            body = await request.json()
            store_id = int(body.get("store_id", 1))
            subsystem = body.get("subsystem", "unknown")

            # Send S15.9 (start dump) command
            data = bytes([store_id])
            pkt = self._tc_manager.build_command(15, 9, data)
            seq = self._tc_manager._seq_count

            entry = {
                "seq": seq,
                "name": f"TM_DUMP_S15.9_store{store_id}",
                "service": 15,
                "subtype": 9,
                "state": "SENT",
                "timestamp": _time_mod.time(),
                "error_code": 0,
                "position": "tm_dump",
            }
            self._verification_log.appendleft(entry)

            if self._tc_writer is None or self._tc_writer.is_closing():
                return web.json_response(
                    {"status": "error", "message": "No TC connection"},
                    status=503,
                )

            from smo_common.protocol.framing import frame_packet
            framed = frame_packet(pkt)
            async with self._tc_send_lock:
                self._tc_writer.write(framed)
                await self._tc_writer.drain()

            # Try to use real archived data; fall back to synthetic
            dump_key = f"{subsystem}_{store_id}"
            archived = self._archive.get_playback_data(subsystem)
            if archived:
                self._tm_dump_data[dump_key] = archived
            else:
                self._tm_dump_data[dump_key] = self._generate_dump_data(
                    subsystem, store_id
                )

            return web.json_response({
                "status": "sent",
                "seq": seq,
                "dump_key": dump_key,
            })
        except Exception as e:
            logger.warning("TM dump error: %s", e)
            return web.json_response(
                {"status": "error", "message": str(e)}, status=400
            )

    async def _handle_tm_dump_data(self, request):
        """Return stored TM dump data for playback."""
        dump_key = request.query.get("key", "")
        data = self._tm_dump_data.get(dump_key, [])
        return web.json_response({"dump_key": dump_key, "data": data})

    def _generate_dump_data(self, subsystem: str, store_id: int) -> list[dict]:
        """Generate synthetic historical TM data for dump playback."""
        import math
        import random
        now = _time_mod.time()
        points = []
        # Generate 60 data points going back 1 hour
        for i in range(60):
            t = now - (60 - i) * 60  # 1-minute intervals
            point = {"timestamp": t, "index": i}
            if subsystem == "eps":
                point["soc_pct"] = max(20, min(100,
                    70 + 15 * math.sin(i * 0.1) + random.gauss(0, 1)))
                point["power_gen_W"] = max(0,
                    30 + 25 * max(0, math.sin(i * 0.08)) + random.gauss(0, 2))
                point["power_cons_W"] = 22 + random.gauss(0, 1.5)
            elif subsystem == "aocs":
                point["rate_roll"] = random.gauss(0, 0.02)
                point["rate_pitch"] = random.gauss(0, 0.02)
                point["rate_yaw"] = random.gauss(0, 0.02)
                point["att_error_deg"] = abs(random.gauss(0.1, 0.05))
            elif subsystem == "tcs":
                point["temp_obc_C"] = 25 + 5 * math.sin(i * 0.05) + random.gauss(0, 0.5)
                point["temp_battery_C"] = 20 + 3 * math.sin(i * 0.05) + random.gauss(0, 0.3)
                point["temp_fpa_C"] = -15 + 2 * math.sin(i * 0.05) + random.gauss(0, 0.2)
            elif subsystem == "ttc":
                in_contact = math.sin(i * 0.15) > 0.3
                point["rssi_dbm"] = -85 + (20 if in_contact else 0) + random.gauss(0, 2)
            elif subsystem == "obdh":
                point["cpu_load"] = 15 + 5 * math.sin(i * 0.1) + random.gauss(0, 2)
            elif subsystem == "payload":
                point["fpa_temp"] = -15 + 2 * math.sin(i * 0.05) + random.gauss(0, 0.3)
            points.append(point)
        return points

    # ── GO/NO-GO coordination ────────────────────────────────────────

    async def _handle_go_nogo_status(self, request):
        """Return current GO/NO-GO poll state."""
        async with self._go_nogo_lock:
            result = {
                "active": self._go_nogo_active,
                "label": self._go_nogo_label,
                "initiator": self._go_nogo_initiator,
                "responses": dict(self._go_nogo_responses),
            }
        return web.json_response(result)

    async def _handle_go_nogo_poll(self, request):
        """Initiate a GO/NO-GO poll (Flight Director only)."""
        try:
            body = await request.json()
            position = body.get("position", "")
            label = body.get("label", "GO/NO-GO Poll")

            if position != "flight_director":
                return web.json_response(
                    {"error": "Only Flight Director can initiate polls"},
                    status=403,
                )

            async with self._go_nogo_lock:
                self._go_nogo_active = True
                self._go_nogo_label = label
                self._go_nogo_initiator = position
                self._go_nogo_responses = {
                    "flight_director": "GO",
                }

            # Broadcast poll to all WebSocket clients
            msg = json.dumps({
                "type": "go_nogo_poll",
                "label": label,
                "initiator": position,
            })
            async with self._ws_lock:
                clients = list(self._ws_clients)
            disconnected = []
            for ws, _pos in clients:
                try:
                    await ws.send_str(msg)
                except Exception:
                    disconnected.append((ws, _pos))
            if disconnected:
                async with self._ws_lock:
                    self._ws_clients = [
                        c for c in self._ws_clients if c not in disconnected
                    ]

            return web.json_response({"status": "poll_started", "label": label})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_go_nogo_respond(self, request):
        """Submit a GO/NO-GO response for a position."""
        try:
            body = await request.json()
            position = body.get("position", "")
            response = body.get("response", "").upper()

            async with self._go_nogo_lock:
                if not self._go_nogo_active:
                    return web.json_response(
                        {"error": "No active poll"}, status=400)

                if response not in ("GO", "NOGO", "STANDBY"):
                    return web.json_response(
                        {"error": "Response must be GO, NOGO, or STANDBY"},
                        status=400,
                    )

                self._go_nogo_responses[position] = response

                # Snapshot state for broadcast
                status_msg = json.dumps({
                    "type": "go_nogo_status",
                    "active": True,
                    "label": self._go_nogo_label,
                    "responses": dict(self._go_nogo_responses),
                })

                # Check if all positions have responded
                positions_with_access = set(self._positions.keys())
                responded = set(self._go_nogo_responses.keys())
                result_msg = None
                if positions_with_access <= responded:
                    all_go = all(
                        v == "GO" for v in self._go_nogo_responses.values()
                    )
                    result = "ALL_GO" if all_go else "NO_GO"
                    self._go_nogo_active = False

                    result_msg = json.dumps({
                        "type": "go_nogo_result",
                        "result": result,
                        "label": self._go_nogo_label,
                        "responses": dict(self._go_nogo_responses),
                    })

                all_responses = dict(self._go_nogo_responses)

            # Broadcast updated status to all clients (outside lock)
            async with self._ws_lock:
                clients = list(self._ws_clients)
            disconnected = []
            for ws, _pos in clients:
                try:
                    await ws.send_str(status_msg)
                except Exception:
                    disconnected.append((ws, _pos))
            if disconnected:
                async with self._ws_lock:
                    self._ws_clients = [
                        c for c in self._ws_clients if c not in disconnected
                    ]

            if result_msg is not None:
                async with self._ws_lock:
                    clients = list(self._ws_clients)
                for ws, _pos in clients:
                    try:
                        await ws.send_str(result_msg)
                    except Exception:
                        pass

            return web.json_response({
                "status": "recorded",
                "position": position,
                "response": response,
                "all_responses": all_responses,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    # ── New Display Panels (Features 1-6) ──────────────────────────────

    async def _handle_contact_schedule(self, request):
        """GET /api/displays/contact-schedule — Ground station pass schedule."""
        current_time = _time_mod.time()
        # Try to fetch from planner API if available
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._planner_api_base}/api/contacts",
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        passes = data.get("contacts", [])
                        self._contact_scheduler.update_passes(passes)
        except Exception:
            pass

        next_passes = self._contact_scheduler.get_next_passes(10, current_time)
        current_status = self._contact_scheduler.get_current_contact_status(current_time)

        return web.json_response({
            "next_passes": next_passes,
            "current_contact_status": current_status,
            "timestamp": current_time,
        })

    async def _handle_power_budget(self, request):
        """GET /api/displays/power-budget — Power generation, consumption, battery."""
        self._power_budget_monitor.update_from_telemetry(self._latest_state)
        return web.json_response(self._power_budget_monitor.get_display_data())

    async def _handle_fdir_alarms(self, request):
        """GET /api/displays/fdir-alarms — FDIR status and active alarms."""
        return web.json_response(self._fdir_alarm_panel.get_display_data())

    async def _handle_procedure_status_display(self, request):
        """GET /api/displays/procedure-status — Procedure execution status."""
        proc_status = self._procedure_runner.get_status()
        if proc_status:
            self._procedure_status_panel.set_executing_procedure(proc_status)
        return web.json_response(self._procedure_status_panel.get_display_data())

    async def _handle_system_overview(self, request):
        """GET /api/displays/system-overview — Top-level system status dashboard."""
        self._system_overview_dashboard.update_from_telemetry(self._latest_state)
        return web.json_response(self._system_overview_dashboard.get_display_data())

    async def _handle_alarm_ack_display(self, request):
        """POST /api/displays/alarms/{alarm_id}/ack — Acknowledge an alarm."""
        try:
            alarm_id = int(request.match_info.get("alarm_id", 0))
            self._fdir_alarm_panel.acknowledge_alarm(alarm_id)
            return web.json_response({"status": "acknowledged", "alarm_id": alarm_id})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_alarm_trends(self, request):
        """POST /api/displays/alarm-trends — Get alarm trend data for charting."""
        try:
            body = await request.json()
            subsystem = body.get("subsystem", "")
            limit = int(body.get("limit", 100))

            # Query archive for alarms in this subsystem
            all_alarms = self._fdir_alarm_panel.get_alarm_journal()
            filtered = [
                a for a in all_alarms
                if subsystem == "" or a.get("subsystem") == subsystem
            ][:limit]

            return web.json_response({
                "subsystem": subsystem,
                "alarms": filtered,
                "count": len(filtered),
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)


def main():
    parser = argparse.ArgumentParser(description="SMO Mission Control System")
    parser.add_argument("--connect", default="localhost:8002",
                        help="TM source host:port")
    parser.add_argument("--config", default="configs/eosat1/",
                        help="Config directory")
    parser.add_argument("--port", type=int, default=9090, help="HTTP port")
    parser.add_argument("--sim-epoch", default=None,
                        help="Fixed simulation epoch (ISO 8601 UTC, e.g. 2026-03-10T00:00:00Z). "
                             "If omitted, ground time uses real wall-clock UTC.")
    args = parser.parse_args()

    host, port = args.connect.rsplit(":", 1)
    logging.basicConfig(level=logging.INFO)
    server = MCSServer(args.config, host, int(port), args.port,
                       sim_epoch=args.sim_epoch)
    asyncio.run(server.start())


if __name__ == "__main__":
    main()

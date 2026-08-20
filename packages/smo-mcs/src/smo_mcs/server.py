"""SMO MCS — Web Server.

Connects to simulator/gateway via TCP, serves config-driven operator displays.
Supports position-based command access control.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
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
from smo_common.telemetry.eng_values import build_state_tables
from smo_common.protocol.ecss_packet import (
    CUC_FINE_TICKS, cuc_seconds_to_datetime, datetime_to_cuc_seconds,
    decommutate_packet, get_time_epoch,
)
from smo_common.protocol.pus_services import (
    COMMAND_REJECTED_EVENT_ID, FAILURE_CODES, MEMORY_FAILURE_CODES,
    VerificationData, failure_code_text, parse_command_rejection,
)
from smo_mcs.tc_lengths import annotate_command, expected_data_bytes

#: SID of the OBC bootloader beacon (mirrors
#: ``smo_simulator.engine.BOOTLOADER_BEACON_SID``). It is emitted on the
#: bootloader APID and is the spacecraft's only signal of life in phases <= 3.
BOOTLOADER_BEACON_SID = 11

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
from smo_common.procedures import (
    CANONICAL_SCHEMA_VERSION, from_markdown, to_canonical, validate_steps,
)
from smo_mcs.tc_manager import TCManager
from smo_mcs.procedure_runner import ProcedureRunner
from smo_mcs.tm_archive import TMArchive
from smo_mcs.displays.contact_pass_scheduler import ContactScheduler
from smo_mcs.displays.power_budget import PowerBudgetMonitor
from smo_mcs.displays.fdir_alarm_panel import FDIRAlarmPanel
from smo_mcs.displays.fdir_panel import FDIRDisplayModel
from smo_mcs.displays.system_overview import SystemOverviewDashboard
from smo_mcs import pus_reports

logger = logging.getLogger(__name__)

#: Display colours for the ground network, assigned by list order. The old UI
#: hard-coded {'Iqaluit': blue, 'Troll': green} so the three stations added
#: since were drawn in the same fallback colour (defect #79).
_GS_COLORS = ("#3b82f6", "#22c55e", "#f59e0b", "#a78bfa", "#ec4899",
              "#14b8a6", "#ef4444", "#84cc16")


def _station_id(name: str) -> str:
    """DOM-safe id for a station name (``O'Higgins`` -> ``o-higgins``)."""
    out = []
    for ch in str(name).lower():
        out.append(ch if ch.isalnum() else "-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "gs"


class MCSServer:
    """Mission Control System web server."""

    def __init__(self, config_dir: str | Path, connect_host: str = "localhost",
                 connect_port: int = 8002, http_port: int = 9090,
                 tc_port: int = 8001, sim_epoch: str | None = None,
                 time_source: str | None = None, sim_state_url: str | None = None,
                 bind_host: str | None = None, ui_mode: str | None = None):
        self.config_dir = Path(config_dir)
        self.connect_host = connect_host
        self.connect_port = connect_port
        self.http_port = http_port
        self.tc_port = tc_port
        # Bind address (D-2). This was hard-coded to 0.0.0.0 while start.sh
        # printed "services bound to loopback where supported" in LOCAL mode,
        # so the banner was untrue for the three main servers.
        self.bind_host = bind_host or os.environ.get("SMO_BIND") or "0.0.0.0"
        self._ws_clients: list[tuple[web.WebSocketResponse, str]] = []
        self._latest_state: dict = {}
        self._running = False
        # Ground time system: MCS maintains its own clock.
        # - sim_epoch=None → use real UTC (wall clock)
        # - sim_epoch="2026-08-20T12:00:00Z" → fixed simulation epoch
        self._ground_epoch: Optional[datetime] = None
        self._ground_start_wall: float = _time_mod.time()
        self._ground_time_offset: float = 0.0  # manual adjustment (seconds)
        # Present-time illumination as reported by the spacecraft (the single
        # eclipse authority). Filled by _ingest_sim_illumination from the
        # sim-state poll; None in real mode or before the first poll.
        self._sim_illum: Optional[dict] = None
        self._sim_speed: float = 1.0
        if sim_epoch:
            self._ground_epoch = datetime.fromisoformat(sim_epoch.replace("Z", "+00:00"))

        # ── Time source resolution ──────────────────────────────────
        # Precedence: explicit ctor arg (from CLI) > env SMO_TIME_SOURCE >
        # mission-config time_source field > built-in default ("sim").
        # In SIM mode the MCS closed-loop syncs its ground clock to the
        # simulator's sim_time/speed via _sim_state_poll_loop (re-anchoring
        # the open-loop clock periodically). In REAL mode it leaves
        # _ground_epoch=None so sim_now() returns wall-clock UTC.
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
        # ── UI mode resolution (design_2026-08 plan §1.2) ────────────
        # Same house precedence as time_source: CLI --ui > SMO_UI env >
        # mission-config `ui:` > built-in default. Default is "classic"
        # until the modern console passes the Agent-5 verification gate
        # (the flip is a one-line change here + start.sh).
        # Per-request `?ui=` and the /classic and /modern routes override
        # this for a single response (see _handle_index).
        try:
            _cfg_ui = _mc.ui  # mission config loaded above; None if absent
        except Exception:
            _cfg_ui = None    # config never loaded — env/CLI/default decide
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
        # Default sim-state port comes from the shared NetworkConfig schema
        # (or SMO_SIM_HTTP_PORT env) rather than a hard-coded literal, so the
        # MCS source carries no simulator-HTTP back-channel constant (HA-006).
        from smo_common.config.schemas import NetworkConfig as _SimNetDefaults
        _sim_http_port = _os.environ.get(
            "SMO_SIM_HTTP_PORT", str(_SimNetDefaults().http_port))
        self._sim_state_url = (
            sim_state_url
            or _os.environ.get("SMO_SIM_STATE_URL")
            or _cfg_sim_state_url
            or f"http://{connect_host}:{_sim_http_port}/api/state"
        )
        logger.info("MCS time source: %s (sim_state_url=%s)",
                    self._time_source, self._sim_state_url)
        if self._time_source == "real":
            # Real mission: wall-clock UTC, no sim anchor, no polling.
            self._ground_epoch = None
        else:
            # Sim mode: seed the open-loop clock with an immediate best-effort
            # anchor so the clock is sane before the first poll completes.
            # _sim_state_poll_loop re-anchors continuously once started.
            if self._ground_epoch is None:
                self._ground_epoch = datetime.now(timezone.utc)
                self._ground_start_wall = _time_mod.time()
        # Orbit propagator (initialised from TLE config, if available)
        self._orbit_prop = None
        self._init_orbit_propagator()
        self._displays = load_mcs_displays(self.config_dir)
        self._tm_rate_ladder = self._load_tm_rate_ladder()
        # Ladder state absorbed from the simulator's /api/state `ttc` block:
        # active tier, commanded tier, coding, symbol rate, DOWNLINK mode.
        self._link_tier_state: dict = {}

        # Parameter cache: populated ONLY from TM TCP socket HK packets via
        # the TMProcessor decoder (which inverts the engine's HK packing using
        # hk_structures.yaml). The MCS has no other source of spacecraft state.
        # Maps param_id (int) -> {"value": float, "last_update_ts": float, "sid": int}
        self._param_cache: dict[int, dict] = {}
        self._param_cache_lock = asyncio.Lock()
        # SIM-TIME epoch of the last received TM frame. Ages are computed
        # against sim_now_ts(), never the wall clock (spec Part D.2).
        self._last_tm_frame_ts: Optional[float] = None
        # Full-precision on-board CUC seconds of the newest packet received,
        # for archive correlation. None until the first packet arrives.
        self._last_obc_cuc: Optional[float] = None
        self.tm_packets_received: int = 0
        self._storage_status: list[dict] = []
        # Per-SID observation: epoch time each HK SID was last decoded, and any
        # enable state the spacecraft has actually reported (as opposed to the
        # UI's old assumption that everything is always on). `None` for a SID
        # means "nobody told us" — the UI infers PERIODIC/SILENT from the age.
        self._sid_last_seen: dict[int, float] = {}
        self._sid_enabled_reported: dict[int, bool] = {}
        # Why the on-board HK enable set last changed on its own — a boot or a
        # bootloader entry rather than an operator TC(3,5)/TC(3,6). Without
        # this the HK Manager shows SIDs flipping to DISABLED with no
        # explanation and the operator's next clue is a hole in the S15 dump.
        # `None` = the spacecraft has never reported such a reset.
        self._hk_enable_reset: dict | None = None

        # Wire up the real HK decoder. Two prior fix passes left _process_tm
        # only logging packet metadata into a debug dict; the param cache was
        # never populated and /api/state always returned empty params. Using
        # TMProcessor (smo_mcs.tm_processor) to do the actual decommutation
        # against hk_structures.yaml so this can never silently regress.
        from smo_mcs.tm_processor import TMProcessor
        from smo_common.config.loader import load_hk_structures, load_limits
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
            hk_defs = []
        # Ground limit checking (WP-3). `mcs/limits.yaml` was loaded by nobody
        # and TMProcessor was built without it, so `_check_limit` returned
        # immediately for every parameter — 51 declared bands, none enforced.
        # 30 of the 51 limited parameters have NO S12 definition on board
        # (all eight per-line currents, DoD, the three body rates, wheel
        # temperature, momentum, gyro temperature, PDOP, the three OBDH buffer
        # fills, SEU/stack/heap, AGC, the four rate-ladder parameters and the
        # payload detector temperature), so for those the ground check is the
        # only limit check that exists anywhere in the system.
        try:
            self._limit_defs = load_limits(self.config_dir)
        except Exception as e:
            logger.warning("Failed to load mcs/limits.yaml: %s", e)
            self._limit_defs = []
        self._tm_processor = TMProcessor(hk_structures=hk_struct_map,
                                         limits=self._limit_defs)

        # Mission APIDs — the beacon SID is emitted on the bootloader APID, so
        # the HK table can tell the operator which source a packet came from.
        self._spacecraft_apid = 1
        self._bootloader_apid = 2
        try:
            import yaml as _yaml
            with open(self.config_dir / "mission.yaml") as f:
                _mission = _yaml.safe_load(f) or {}
            self._spacecraft_apid = int(_mission.get("spacecraft_apid", 1))
            self._bootloader_apid = int(_mission.get("bootloader_apid", 2))
        except Exception as e:
            logger.debug("mission.yaml APIDs unavailable, using defaults: %s", e)

        # Authoritative HK SID table, straight from hk_structures.yaml.
        # Served by /api/hk-structures so the HK Manager never has to guess.
        self._hk_sid_meta: dict[int, dict] = {}
        for hk in hk_defs:
            beacon = (hk.sid == BOOTLOADER_BEACON_SID)
            self._hk_sid_meta[int(hk.sid)] = {
                "sid": int(hk.sid),
                "name": str(hk.name),
                "interval_s": float(hk.interval_s),
                "param_count": len(hk.parameters),
                "apid": self._bootloader_apid if beacon else self._spacecraft_apid,
                # The beacon is the spacecraft's only signal of life in
                # bootloader (phases <= 3); commanding it off from the console
                # is a hazard, so the row is presented read-only.
                "readonly": beacon,
                "note": (
                    "Bootloader beacon on APID 0x%03X — the only telemetry "
                    "available while the OBC runs the boot image. "
                    "Enable/disable is inhibited from the console; "
                    "one-shot Request (S3.27) remains available."
                    % self._bootloader_apid
                ) if beacon else "",
            }

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

        # ── The cadence join (Franz: "stale on the cadence x 1.5") ──────────
        # `/api/hk-structures` publishes `interval_s` per SID and
        # `/api/param-catalog` publishes in_hk / on_demand, but NOTHING said
        # which parameters ride in which structure, so the browser could not
        # derive a per-parameter staleness threshold from the real downlink
        # cadence and fell back to one hard-coded number for the whole
        # spacecraft. Join the two here, once, and serve the result.
        #
        # `_param_cadence`     pid -> {"interval_s": fastest, "sids": [...] }
        # `_subsystem_cadence` subsystem -> {"interval_s": fastest, "sids": [...]}
        #
        # "Fastest" is the right reduction: a parameter carried by both SID 1
        # (1 s) and SID 8 (8 s) is expected once a second whenever both are
        # enabled, and the disabled-SID state (see `_staleness_policy`) is what
        # covers the case where only the slow one is on.
        self._param_cadence: dict[int, dict] = {}
        self._subsystem_cadence: dict[str, dict] = {}
        for hk in hk_defs:
            interval = float(getattr(hk, "interval_s", 0.0) or 0.0)
            if interval <= 0:
                continue
            sid = int(hk.sid)
            for hp in hk.parameters:
                pid = int(hp.param_id)
                ent = self._param_cadence.setdefault(
                    pid, {"interval_s": interval, "sids": []})
                ent["interval_s"] = min(ent["interval_s"], interval)
                if sid not in ent["sids"]:
                    ent["sids"].append(sid)
                sub = (self._param_meta.get(pid, {}).get("subsystem") or "").strip()
                if not sub:
                    continue
                sent = self._subsystem_cadence.setdefault(
                    sub, {"interval_s": interval, "sids": []})
                sent["interval_s"] = min(sent["interval_s"], interval)
                if sid not in sent["sids"]:
                    sent["sids"].append(sid)
        for ent in self._param_cadence.values():
            ent["sids"].sort()
        for ent in self._subsystem_cadence.values():
            ent["sids"].sort()

        # Staleness factors/floors come from configuration, not from a JS
        # literal, so an instructor can retune the console without editing a
        # 10 000-line HTML file. See configs/<mission>/mcs/staleness.yaml.
        self._staleness_policy = self._load_staleness_policy()

        # Position access control
        self._positions = load_positions(self.config_dir)

        # TC catalog for command metadata
        try:
            self._tc_catalog = load_tc_catalog(self.config_dir)
        except Exception:
            self._tc_catalog = []

        # TCCommandDef defaults `fields` to [], which erases the difference
        # between "declared to take no arguments" and "the catalogue simply
        # does not say". For expected-length reporting that distinction
        # matters — "absent" must be shown as unknown, never as 0 (defect #67).
        # Recover it from the raw YAML.
        self._tc_names_without_fields: set[str] = set()
        # Phase E WP-5: the schema-driven QUICK COMMANDS list (command name +
        # ENGINEERING argument values; the hex is computed client-side by the
        # shared encoder, never hand-typed).
        self._tc_quick_commands: list[dict] = []
        try:
            import yaml as _yaml
            with open(self.config_dir / "commands" / "tc_catalog.yaml") as f:
                _raw = _yaml.safe_load(f) or {}
            _cmds = _raw.get("commands", _raw) if isinstance(_raw, dict) else _raw
            for _c in _cmds or []:
                if isinstance(_c, dict) and "fields" not in _c:
                    self._tc_names_without_fields.add(str(_c.get("name", "")))
            if isinstance(_raw, dict):
                self._tc_quick_commands = list(_raw.get("quick_commands") or [])
        except Exception as e:
            logger.debug("could not inspect raw tc_catalog.yaml: %s", e)

        # TC connection
        self._tc_writer: Optional[asyncio.StreamWriter] = None
        self._tc_manager = TCManager(apid=1)

        # Verification log — last 200 commands
        self._verification_log: deque[dict] = deque(maxlen=200)

        # Shift handover log
        self._handover_log: deque = deque(maxlen=500)

        # GO/NO-GO coordination state
        self._go_nogo_active = False
        self._go_nogo_label = ""
        self._go_nogo_responses: dict[str, str] = {}  # position -> GO/NOGO/STANDBY
        self._go_nogo_initiator = ""

        # Procedure execution engine
        self._procedure_runner = ProcedureRunner(
            send_command_fn=self._proc_send_command,
            get_telemetry_fn=self._proc_get_telemetry,
            time_fn=self.sim_now_ts,
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
        # Alarm → contingency-procedure linkage (operator gap 3). Config-driven
        # from mcs/alarm_procedures.yaml so "this alarm ⇒ run CTG-001" is a
        # Flight-Ops edit, not a code change.
        self._alarm_proc_by_param: dict[int, str] = {}
        self._alarm_proc_by_event: dict[int, str] = {}
        self._alarm_proc_by_subsystem: dict[str, str] = {}
        self._procedure_meta: dict[str, dict] = {}
        self._load_alarm_procedures()

        # Persistent TM archive (SQLite).
        #
        # SMO_TM_ARCHIVE relocates the file. It defaults to `configs/../data`,
        # which sits inside the checkout — and a checkout in a Dropbox /
        # iCloud / OneDrive folder or on a network mount is a filesystem whose
        # locking SQLite cannot rely on, which is a first-class way to get
        # "database disk image is malformed". TMArchive.open() detects that
        # (it checks the journal mode it ACTUALLY got) and says so loudly;
        # this is the knob for acting on it without editing the tree.
        env_path = os.environ.get("SMO_TM_ARCHIVE", "").strip()
        if env_path:
            archive_path = Path(env_path).expanduser()
            archive_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            archive_dir = self.config_dir / ".." / "data"
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_path = archive_dir / "tm_archive.db"
        self._archive = TMArchive(archive_path)
        self._archive.open()
        # Prune on start (D-5). `prune()` had zero callers, so a term's worth
        # of parameter rows plus the WAL accumulated inside the config tree
        # and was never bounded by anything.
        try:
            self._archive.prune(now_epoch=self.sim_now_ts())
        except Exception as e:
            logger.debug("Archive prune failed: %s", e)
        # Four archive conditions, four different things to tell the operator.
        # They are deliberately distinguishable: "there is no history because
        # this is a new file" is not the same problem as "the history is
        # unreadable", and both used to look like an empty trend page.
        _status = getattr(self._archive, "status", "ok")
        if not getattr(self._archive, "available", True):
            logger.error("MCS starting with a DEGRADED TM archive (%s): %s",
                         _status, self._archive.message())
        elif _status == "new":
            logger.info("TM archive: %s", self._archive.message())
        elif getattr(self._archive, "legacy_time_base", False):
            logger.warning(
                "TM archive on disk predates the sim-time migration; its rows "
                "are on the host wall clock and will not line up with the "
                "sim-time rows this session writes. See TMArchive.meta().")
        self._archive_tick = 0  # counter to batch parameter archiving
        # Prune again on a timer so a console left running for a whole term
        # bounds the SQLite file without an operator restart (D-5).
        # Interval and cutoff are both SIM seconds — same clock as the rows.
        self._archive_prune_interval_s = 3600.0
        self._archive_last_prune = self.sim_now_ts()

        # ── Ground-derived parameter block v2 (POSITION_NEEDS G1+G3) ────
        # One projection helper: archive slope + limits.yaml thresholds +
        # the orbit propagator. Values carry explicit valid/reason — an
        # empty archive yields INVALID entries, never fabricated numbers.
        from smo_mcs.derived_params import DerivedParamEngine
        self._derived_engine = DerivedParamEngine(
            self._archive, self._limit_defs, self._orbit_prop,
            self.sim_now_ts)
        try:
            import yaml as _yaml
            with open(self.config_dir / "subsystems" / "prop.yaml") as f:
                _prop_cfg = _yaml.safe_load(f) or {}
            self._derived_engine.set_catbed_threshold(
                (_prop_cfg.get("interlocks") or {}).get("catbed_min_c"))
        except Exception as e:  # noqa: BLE001 — absent config ⇒ honest invalid
            logger.debug("prop.yaml catbed threshold unavailable: %s", e)

        # ── Predicted link chain (POSITION_NEEDS G5) ────────────────────
        # Ground prediction only: RF constants from config + the same orbit
        # propagator; served at /api/displays/link-prediction.
        from smo_mcs.link_prediction import LinkPredictor
        self._link_predictor = LinkPredictor(self.config_dir,
                                             self._orbit_prop)

        # Delayed-TM (S15) dump store. These archive files are the ground
        # segment's copy of everything the spacecraft played back, so the MCS
        # — the service every operator already has open — owns serving them
        # (defect #90). Resolution order lives in smo_common.dump_store and is
        # shared with the simulator (the writer) and the standalone viewer.
        from smo_common.dump_store import resolve_dump_dir as _resolve_dump_dir
        _dump_mission_cfg = None
        try:
            from smo_common.config.loader import load_mission_config as _lmc
            _dump_mission_cfg = _lmc(self.config_dir)
        except Exception:  # noqa: BLE001 — a missing mission.yaml is not fatal
            pass
        self._dump_dir = _resolve_dump_dir(_dump_mission_cfg)
        logger.info("Delayed-TM dump store: %s", self._dump_dir)

        # TM dump playback data (Feature 3). Keys present in
        # _tm_dump_synthetic hold MCS-generated (not spacecraft) data — the
        # serving endpoints flag these payloads (DI-03).
        self._tm_dump_data: dict[str, list[dict]] = {}
        self._tm_dump_synthetic: set[str] = set()

        # Concurrency locks
        self._ws_lock = asyncio.Lock()
        self._go_nogo_lock = asyncio.Lock()
        self._tc_send_lock = asyncio.Lock()
        self._cmd_queue: asyncio.Queue = asyncio.Queue()

        # Planner API base URL for contacts proxy (Feature 1)
        # Prefer SMO_PLANNER_URL env var for distributed deployments
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
        self._system_overview_dashboard = SystemOverviewDashboard()

        # ── FDIR window state (defect #76) ──────────────────────────
        # The observed FDIR model. Seeded with the ground reference database
        # (monitor/rule names, event names, S8 function names, parameter
        # names — none of which are downlinked), then driven by TM(12,13),
        # TM(19,128), TM(12,9)/(12,10) and S5 events.
        self._fdir_model = FDIRDisplayModel()
        #: event_id -> {name, severity, subsystem, description} from the event
        #: catalogue; filled by _load_fdir_reference().
        self._event_meta: dict[int, dict] = {}
        self._load_fdir_reference()

        # Latest on-board schedule as reported by TM(11,18) (defect #77).
        self._schedule_report: dict = {}
        # Latest S6 memory dump / check results (defect #78), newest first.
        self._memory_dumps: deque[dict] = deque(maxlen=20)
        self._memory_checks: deque[dict] = deque(maxlen=20)
        # Declared address space, served to the UI so it stops carrying its
        # own hard-coded copy of the map.
        self._memory_map: dict = self._load_memory_map()

        # ── Sim-clock discontinuity state (defect #79) ──────────────
        # A breakpoint load / scenario reset / instructor jump moves sim_time
        # away from what the previous anchor predicted. The ground clock
        # simply follows, but everything the MCS derived at the OLD epoch —
        # the parameter cache, the schedule report, store status, the FDIR
        # journal timeline — describes a moment that is no longer "now".
        # Same problem, same fix as the planner's defect #59.
        self._last_jump_utc: Optional[str] = None
        self._last_jump_delta_s: float = 0.0
        self._jump_count: int = 0
        self._jump_tolerance_s: float = 10.0

        # Simulator-reported health (SIM mode only, tagged with its source).
        self._sim_health: dict = {}
        self._sim_autonomy: dict = {}

    # ── Alarm → procedure linkage ───────────────────────────────────

    @staticmethod
    def _as_key_int(v) -> Optional[int]:
        if isinstance(v, int):
            return v
        try:
            return int(str(v), 0)
        except (TypeError, ValueError):
            return None

    def _load_alarm_procedures(self) -> None:
        """Read `mcs/alarm_procedures.yaml` and the ops-procedure index.

        An alarm that does not name the procedure an operator should open is
        an alarm *list*, not an alarm *system* — the operator is left to grep
        64 markdown documents while the spacecraft misbehaves.
        """
        import yaml as _yaml
        path = self.config_dir / "mcs" / "alarm_procedures.yaml"
        try:
            with open(path) as f:
                raw = (_yaml.safe_load(f) or {}).get("alarm_procedures", {}) or {}
        except Exception as e:  # noqa: BLE001 — absent config is not fatal
            logger.debug("alarm_procedures.yaml unavailable: %s", e)
            raw = {}
        for key, target in (raw.get("by_parameter") or {}).items():
            pid = self._as_key_int(key)
            if pid is not None:
                self._alarm_proc_by_param[pid] = str(target)
        for key, target in (raw.get("by_event") or {}).items():
            eid = self._as_key_int(key)
            if eid is not None:
                self._alarm_proc_by_event[eid] = str(target)
        for key, target in (raw.get("by_subsystem") or {}).items():
            self._alarm_proc_by_subsystem[str(key).lower()] = str(target)

        # Procedure id → name/file, so the alarm row can render "CTG-001 —
        # Under-Voltage Load Shed" and the console can open the document.
        try:
            with open(self.config_dir / "procedures"
                      / "procedure_index.yaml") as f:
                index = (_yaml.safe_load(f) or {}).get("procedures", []) or []
            for proc in index:
                pid = str(proc.get("id", ""))
                if pid:
                    self._procedure_meta[pid] = {
                        "id": pid,
                        "name": str(proc.get("name", "")),
                        "file": str(proc.get("file", "")),
                        "category": str(proc.get("category", "")),
                    }
        except Exception as e:  # noqa: BLE001
            logger.debug("procedure_index.yaml unavailable: %s", e)

    def resolve_alarm_procedure(self, alarm: dict) -> Optional[dict]:
        """Most-specific contingency procedure for *alarm*, or None."""
        pid = self._as_key_int(alarm.get("parameter", "").replace("EVT-", "")) \
            if isinstance(alarm.get("parameter"), str) else None
        target = None
        if alarm.get("source") in ("LIMIT", "S12") and pid is not None:
            target = self._alarm_proc_by_param.get(pid)
        if target is None and alarm.get("source") == "S5":
            eid = alarm.get("event_id")
            if eid is None and isinstance(alarm.get("parameter"), str) \
                    and alarm["parameter"].startswith("EVT-"):
                eid = self._as_key_int(alarm["parameter"][4:])
            if eid is not None:
                target = self._alarm_proc_by_event.get(int(eid))
        if target is None:
            target = self._alarm_proc_by_subsystem.get(
                str(alarm.get("subsystem", "")).lower())
        if not target:
            return None
        meta = self._procedure_meta.get(target)
        return {"id": target,
                "name": (meta or {}).get("name", ""),
                "file": (meta or {}).get("file", "")}

    # ── FDIR reference database & config-driven maps ────────────────

    def _load_fdir_reference(self) -> None:
        """Seed the FDIR model with names the spacecraft does not downlink.

        TM(12,13) carries mon_id / param_id / limits and TM(19,128) carries
        ea_id / trigger / func — no names. Those live in the mission database
        on the ground, exactly as they would on a real programme, so the MCS
        reads them from the same YAML the spacecraft was built from and uses
        them purely for display resolution. The on-board report always wins on
        anything it actually reports.
        """
        import yaml as _yaml

        def _as_int(v, default=0):
            if isinstance(v, int):
                return v
            try:
                return int(str(v), 0)
            except (TypeError, ValueError):
                return default

        s12_ref: dict[int, dict] = {}
        try:
            path = self.config_dir / "monitoring" / "s12_definitions.yaml"
            with open(path) as f:
                raw = _yaml.safe_load(f) or {}
            # mon_id defaults to the loader's base + file index; mirror
            # smo_simulator.monitoring_loader.S12_CONFIG_MON_ID_BASE so the
            # ids line up with the ones the spacecraft reports in TM(12,13).
            base = 0x8000
            for idx, rule in enumerate(raw.get("s12_definitions", []) or []):
                mon_id = _as_int(rule.get("mon_id"), base + idx)
                s12_ref[mon_id] = {
                    "mon_id": mon_id,
                    "param_id": _as_int(rule.get("param_id")),
                    "check_type": _as_int(rule.get("check_type"), 0),
                    "low_limit": float(rule.get("low_limit", 0.0)),
                    "high_limit": float(rule.get("high_limit", 0.0)),
                    "enabled": bool(rule.get("enabled", True)),
                    "name": str(rule.get("name", f"rule_{idx}")),
                    "severity": str(rule.get("severity", "WARNING")),
                    "description": str(rule.get("description", "")),
                }
        except Exception as e:
            logger.warning("S12 reference definitions unavailable: %s", e)

        s19_ref: dict[int, dict] = {}
        try:
            path = self.config_dir / "monitoring" / "s19_rules.yaml"
            with open(path) as f:
                raw = _yaml.safe_load(f) or {}
            for rule in raw.get("s19_rules", []) or []:
                ea_id = _as_int(rule.get("ea_id"))
                trig = rule.get("trigger") or {}
                kind_raw = trig.get("kind", "event")
                kind = 1 if str(kind_raw).strip().lower() in ("param", "parameter", "1") else 0
                trigger_id = _as_int(trig.get("id"), _as_int(rule.get("event_type")))
                s19_ref[ea_id] = {
                    "ea_id": ea_id,
                    "name": str(rule.get("name", f"EA_{ea_id}")),
                    "trigger_kind": kind,
                    "trigger_id": trigger_id,
                    "action_func_id": _as_int(rule.get("action_func_id")),
                    "enabled": bool(rule.get("enabled", True)),
                    "description": str(rule.get("description", "")),
                }
        except Exception as e:
            logger.warning("S19 reference rules unavailable: %s", e)

        events: dict[int, dict] = {}
        try:
            from smo_common.config.loader import load_event_catalog
            catalog = load_event_catalog(self.config_dir)
            for ev in getattr(catalog, "events", []) or []:
                events[int(ev.id)] = {
                    "name": str(ev.name),
                    "severity": str(getattr(ev, "severity", "")),
                    "subsystem": str(getattr(ev, "subsystem", "")),
                    "description": str(getattr(ev, "description", "")),
                }
        except Exception as e:
            logger.debug("event catalog unavailable for FDIR labels: %s", e)
        # The event log and the S5 decode need the same names the FDIR journal
        # uses; keep one dict rather than two that can disagree.
        self._event_meta = events

        funcs: dict[int, str] = {}
        for cmd in self._tc_catalog:
            fid = getattr(cmd, "func_id", None)
            if fid is not None and int(fid) not in funcs:
                funcs[int(fid)] = str(getattr(cmd, "name", ""))

        params = {pid: meta.get("name", "") for pid, meta in self._param_meta.items()}

        self._fdir_model.load_reference(s12=s12_ref, s19=s19_ref,
                                        events=events, funcs=funcs,
                                        params=params)
        # The legacy /api/displays/fdir-alarms rule counters used to report 0
        # forever because nothing called these. Keep them consistent with the
        # new model so the two views cannot disagree.
        self._sync_legacy_fdir_panel()

    def _sync_legacy_fdir_panel(self) -> None:
        """Mirror the FDIR model into the older FDIRAlarmPanel counters."""
        try:
            self._fdir_alarm_panel.update_s12_rules([
                {"id": r["mon_id"], "name": r["name"], "subtype": 6,
                 "enabled": r["enabled"], "violation_count": r["violation_count"]}
                for r in self._fdir_model.monitor_rows()
            ])
            self._fdir_alarm_panel.update_s19_rules([
                {"id": r["ea_id"], "name": r["name"], "subtype": 1,
                 "enabled": r["enabled"], "violation_count": r["fire_count"]}
                for r in self._fdir_model.rule_rows()
            ])
            level = self._fdir_model.derive_level(
                self._fdir_alarm_panel.get_active_alarms(),
                self._load_shed_stage())
            self._fdir_alarm_panel.set_fdir_level(level)
        except Exception as e:      # pragma: no cover — display must not break TM
            logger.debug("legacy FDIR panel sync failed: %s", e)

    def _load_shed_stage(self) -> int:
        """Current EPS load-shed stage from telemetry (param 0x0134)."""
        entry = self._param_cache.get(0x0134)
        try:
            return int(entry["value"]) if entry else 0
        except (TypeError, ValueError, KeyError):
            return 0

    def _load_memory_map(self) -> dict:
        """Load the declared address space from memory/memory_map.yaml.

        The UI used to carry its own 11-region JavaScript copy of the map that
        disagreed with the spacecraft in both extent and access rights
        (defect #78). configs/<mission>/memory/memory_map.yaml is the single
        declaration; serve it rather than duplicating it.
        """
        import yaml as _yaml

        def _as_int(v, default=0):
            if isinstance(v, int):
                return v
            try:
                return int(str(v), 0)
            except (TypeError, ValueError):
                return default

        out: dict[str, Any] = {"regions": [], "limits": {}, "source": None}
        path = self.config_dir / "memory" / "memory_map.yaml"
        try:
            with open(path) as f:
                raw = _yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("memory_map.yaml unavailable (%s) — memory map "
                           "will be reported as undeclared", e)
            return out
        out["source"] = str(path)
        for r in raw.get("regions", []) or []:
            start = _as_int(r.get("start"))
            size = _as_int(r.get("size"))
            access = str(r.get("access", "rw")).lower()
            out["regions"].append({
                "name": str(r.get("name", "")),
                "start": start,
                "size": size,
                "end": start + size,
                "access": access,
                "writable": access == "rw" and bool(r.get("patchable", True)),
                "fill_kind": str(r.get("fill_kind", "zero")),
                "description": str(r.get("description", "")),
                "binding": r.get("binding") or None,
            })
        limits = raw.get("limits", {}) or {}
        out["limits"] = {
            "max_load_bytes": _as_int(limits.get("max_load_bytes"), 512),
            "max_dump_bytes": _as_int(limits.get("max_dump_bytes"), 1024),
            "max_check_bytes": _as_int(limits.get("max_check_bytes"), 0x100000),
        }
        return out

    def ground_stations(self) -> list[dict]:
        """The mission ground network, from orbit.yaml + RF augmentation.

        The browser hard-coded Iqaluit and Troll in three places while the
        configuration has carried five stations for some time (defect #79).
        The propagator here is already built from the real list; this exposes
        the same list to the UI.
        """
        import yaml as _yaml
        stations: dict[str, dict] = {}
        order: list[str] = []
        try:
            with open(self.config_dir / "orbit.yaml") as f:
                orbit_cfg = _yaml.safe_load(f) or {}
        except Exception as e:
            logger.debug("orbit.yaml unavailable for station list: %s", e)
            orbit_cfg = {}
        for gs in orbit_cfg.get("ground_stations", []) or []:
            name = str(gs.get("name", "GS"))
            stations[name] = {
                "name": name,
                "id": _station_id(name),
                "lat_deg": float(gs.get("lat_deg", 0.0)),
                "lon_deg": float(gs.get("lon_deg", 0.0)),
                "alt_km": float(gs.get("alt_km", 0.0)),
                "min_elevation_deg": float(gs.get("min_elevation_deg", 5.0)),
            }
            order.append(name)
        # RF augmentation layer — antenna / band / G/T, keyed by name.
        try:
            gs_path = self.config_dir / "planning" / "ground_stations.yaml"
            if gs_path.exists():
                with open(gs_path) as f:
                    rf_cfg = _yaml.safe_load(f) or {}
                for gs in rf_cfg.get("ground_stations", []) or []:
                    name = str(gs.get("name", "GS"))
                    entry = stations.setdefault(name, {
                        "name": name,
                        "id": _station_id(name),
                        "lat_deg": float(gs.get("lat_deg", 0.0)),
                        "lon_deg": float(gs.get("lon_deg", 0.0)),
                        "alt_km": float(gs.get("alt_km", 0.0)),
                        "min_elevation_deg": float(gs.get("min_elevation_deg", 5.0)),
                    })
                    if name not in order:
                        order.append(name)
                    for key in ("antenna_diameter_m", "band", "g_t_db",
                                "tx_power_w", "tx_freq_mhz", "rx_freq_mhz"):
                        if gs.get(key) is not None:
                            entry[key] = gs[key]
        except Exception as e:
            logger.debug("planning/ground_stations.yaml unavailable: %s", e)
        out = [stations[n] for n in order]
        # Stable display colours, assigned by list order rather than by name,
        # so adding a sixth station does not need a UI change.
        for i, gs in enumerate(out):
            gs["color"] = _GS_COLORS[i % len(_GS_COLORS)]
        return out

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

            # Load ground stations. orbit.yaml is the CANONICAL geometry
            # source (shared with the simulator and planner via
            # load_orbit_config); planning/ground_stations.yaml only augments
            # stations with RF fields (G/T, frequencies) and is used here as
            # a fallback source of extra stations. Geometry for same-named
            # stations is cross-checked and a warning logged if it diverges
            # (defect #42 — config duplication).
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

            # De-duplicate by name (orbit.yaml stations take precedence),
            # warning when the planning file's geometry diverges from the
            # canonical orbit.yaml values for the same station.
            seen: dict[str, GroundStation] = {}
            unique_gs = []
            for gs in gs_list:
                if gs.name not in seen:
                    seen[gs.name] = gs
                    unique_gs.append(gs)
                else:
                    canon = seen[gs.name]
                    if (abs(canon.lat_deg - gs.lat_deg) > 1e-3
                            or abs(canon.lon_deg - gs.lon_deg) > 1e-3
                            or abs(canon.min_elevation_deg - gs.min_elevation_deg) > 1e-3):
                        logger.warning(
                            "Ground station '%s' geometry in planning/"
                            "ground_stations.yaml diverges from canonical "
                            "orbit.yaml (using orbit.yaml values)", gs.name)

            self._orbit_prop = OrbitPropagator(tle1, tle2, ground_stations=unique_gs)
            logger.info("Orbit propagator initialised: %d ground stations", len(unique_gs))
        except Exception as e:
            logger.warning("Orbit propagator init failed: %s", e)

    def sim_now(self) -> datetime:
        """**The MCS clock.** Current SIM UTC — the absolute UTC of the
        simulated world, which is what everything the MCS says about the
        mission is timestamped with (spec Part D.1).

        Open-loop anchor-and-extrapolate:

        - SIM mode: ``sim_epoch + (wall elapsed since the anchor) x speed``,
          plus the operator's manual offset. ``_sim_state_poll_loop``
          re-anchors ``_ground_epoch`` / ``_ground_start_wall`` / ``_sim_speed``
          from the simulator every ~1.5 s (with jump detection), so the
          extrapolation only has to cover the gap between polls. Pause
          (``speed == 0``) freezes this clock; acceleration runs it fast —
          which is the point: every age, alarm timestamp and archive row
          derived from it then behaves the same way.
        - REAL mode: no anchor, so this is wall-clock UTC plus the offset.
          On a real mission sim time *is* wall time.

        Nothing mission-related may use ``time.time()``. Wall clock stays
        legitimate only for real-time network I/O timeouts and session log
        lines.
        """
        now_wall = _time_mod.time()
        elapsed = (now_wall - self._ground_start_wall) * self._sim_speed
        if self._ground_epoch:
            return self._ground_epoch + timedelta(seconds=elapsed + self._ground_time_offset)
        return datetime.fromtimestamp(now_wall + self._ground_time_offset, tz=timezone.utc)

    def sim_now_ts(self) -> float:
        """:meth:`sim_now` as a Unix epoch float.

        This is the stamp used for every age, every archive row and every
        alarm: the difference between two of these is **sim seconds**, which
        is the unit ``mcs/staleness.yaml`` thresholds are written in.
        """
        return self.sim_now().timestamp()

    def get_ground_utc(self) -> datetime:
        """Deprecated alias for :meth:`sim_now`.

        The old name said where the clock was *consumed* ("ground") rather
        than what it *is* (sim time), which is how ~20 wall-clock call sites
        came to sit next to it. Kept as a thin alias so an out-of-tree caller
        does not break.
        """
        return self.sim_now()

    def _compute_orbital_state(self) -> dict:
        """Propagate TLE to current ground UTC and return orbital metadata."""
        if self._orbit_prop is None:
            return {}
        try:
            sim_utc = self.sim_now()
            self._orbit_prop.reset(sim_utc)
            o = self._orbit_prop.state
            # Defect D: surface the eclipse transition fields the power-budget
            # display reads (in_eclipse + time-to-entry/exit). Coarse, bounded scan.
            try:
                ecl = self._orbit_prop.next_eclipse_transition()
            except Exception:
                ecl = {
                    "in_eclipse": bool(o.in_eclipse),
                    "time_to_eclipse_entry_s": None,
                    "time_to_eclipse_exit_s": None,
                }
            out = {
                "lat": round(o.lat_deg, 4),
                "lon": round(o.lon_deg, 4),
                "alt_km": round(o.alt_km, 2),
                "in_eclipse": bool(o.in_eclipse),
                "sun_fraction": float(getattr(
                    o, "sun_fraction", 0.0 if o.in_eclipse else 1.0)),
                "in_penumbra": bool(getattr(o, "in_penumbra", False)),
                "shadow_model": getattr(o, "shadow_model", ""),
                "eclipse_source": "ground_propagator",
                "in_contact": bool(o.in_contact),
                "ground_station_name": getattr(o, "active_gs", None) or "",
                "gs_elevation_deg": round(o.gs_elevation_deg, 1),
                "time_to_eclipse_entry_s": ecl["time_to_eclipse_entry_s"],
                "time_to_eclipse_exit_s": ecl["time_to_eclipse_exit_s"],
            }
            # PRESENT time belongs to the on-board authority; the ground
            # propagation above stays for the future-transition scan (and for
            # real mode, where there is no authority to read).
            illum = getattr(self, "_sim_illum", None)
            if (illum and self._time_source == "sim"
                    and (_time_mod.time() - illum["wall"]) <= self._SIM_ILLUM_MAX_AGE_S):
                out["in_eclipse"] = illum["in_eclipse"]
                out["sun_fraction"] = illum["sun_fraction"]
                out["in_penumbra"] = illum["in_penumbra"]
                out["shadow_model"] = illum["shadow_model"] or out["shadow_model"]
                out["eclipse_source"] = "spacecraft"
                for k in ("time_to_eclipse_entry_s", "time_to_eclipse_exit_s"):
                    if illum.get(k) is not None:
                        out[k] = illum[k]
            return out
        except Exception as e:
            logger.debug("Orbit propagation error: %s", e)
            return {}

    #: ``obdh.time_ref`` enumeration (spec Part B). NONE means the OBC has
    #: free-run from CUC 0 since the last bootloader reset and has never been
    #: told the time; GPS is the only value that implies no drift.
    TIME_REF_NAMES = {0: "NONE", 1: "GROUND", 2: "GPS", 3: "HOLDOVER"}

    #: ``obdh.time_since_sync_s`` saturates rather than wrapping; this value
    #: means "never synced" (spec Part B).
    TIME_SINCE_SYNC_NEVER = 0xFFFFFFFF

    def _param_id_by_name(self, name: str) -> Optional[int]:
        """Look a parameter id up by its ``parameters.yaml`` name.

        The Part B OBDH clock parameters (``obdh.obc_time_fine``,
        ``obdh.time_ref``, ``obdh.time_since_sync_s``) are allocated by the
        simulator/config owner, so the MCS resolves them by NAME rather than
        carrying a second copy of the id allocation that could drift out of
        step. Absent from the configuration ⇒ the field is simply not shown.
        """
        idx = getattr(self, "_param_id_index", None)
        if idx is None:
            idx = {str(m.get("name", "")): pid
                   for pid, m in self._param_meta.items()}
            self._param_id_index = idx
        return idx.get(name)

    def _cached_value(self, name: str) -> Optional[float]:
        """Latest cached value of a parameter named in ``parameters.yaml``."""
        pid = self._param_id_by_name(name)
        if pid is None:
            return None
        entry = self._param_cache.get(pid)
        return None if entry is None else float(entry["value"])

    def obc_cuc_now(self) -> Optional[float]:
        """The spacecraft's own clock, in full-precision CUC seconds.

        TM 0x0309 (coarse) plus ``obdh.obc_time_fine`` (1/65536 s) when the
        spacecraft downlinks one. ``None`` until OBDH housekeeping has been
        received. This is the ground's only knowledge of the on-board clock,
        and it is what every ground<->on-board time conversion anchors on.
        """
        entry = self._param_cache.get(0x0309)
        if entry is None:
            return None
        cuc = float(entry["value"])
        fine = self._cached_value("obdh.obc_time_fine")
        if fine is not None:
            cuc += float(fine) / CUC_FINE_TICKS
        return cuc

    def _compute_time_state(self) -> dict:
        """Build the console's time block: SIM time, on-board time and the
        correlation between them (spec Part D.9).

        Three clocks appear here and they are deliberately kept apart:

        * ``sim_time`` / ``ground_utc`` — :meth:`sim_now`, the ground
          segment's clock. Everything the MCS timestamps is on this.
        * ``sc_time`` — the spacecraft's own clock, decoded from TM 0x0309
          (plus the fine field when the spacecraft downlinks one) against the
          **J2000** mission epoch. It free-runs and drifts.
        * ``time_correlation.offset_s`` — the measured gap, ``sc - sim``. The
          ground can only *measure* this from telemetry; the spacecraft does
          not know it. Closing it is the operator's job in NOM-008.
        """
        sim_utc = self.sim_now()
        met_s = 0.0
        if self._t0_epoch:
            try:
                t0 = datetime.fromisoformat(
                    self._t0_epoch.replace("Z", "+00:00")
                )
                met_s = (sim_utc - t0).total_seconds()
            except Exception:
                met_s = (_time_mod.time() - self._ground_start_wall) * self._sim_speed
        else:
            met_s = (_time_mod.time() - self._ground_start_wall) * self._sim_speed

        # ── Spacecraft time from OBDH TM ────────────────────────────
        cache = self._param_cache
        uptime_entry = cache.get(0x0308)
        sc_uptime = uptime_entry["value"] if uptime_entry else None
        obc_time_entry = cache.get(0x0309)
        sc_obc_time_cuc = obc_time_entry["value"] if obc_time_entry else None

        # Sub-second half of the CUC, when the spacecraft downlinks it —
        # 1/65536 s ticks, the same units as the packet header's fine field.
        fine_raw = self._cached_value("obdh.obc_time_fine")
        sc_cuc_seconds = self.obc_cuc_now()

        # Decode against the MISSION EPOCH (J2000) — what the spacecraft
        # actually stamps with.
        #
        # The old code did `if sc_obc_time_cuc > 1e9: fromtimestamp(...)`: a
        # Unix decode behind a magnitude guard. A correct J2000 CUC is ~8.4e8
        # today, so the test never passed, `sc_time` was permanently None, and
        # it only began to "work" once a GPS sync wrote a Unix value into a
        # J2000 field. Both halves of that were wrong; the heuristic is gone
        # and the shared decoder does the work.
        sc_time_iso = None
        if sc_cuc_seconds is not None:
            try:
                sc_time_iso = cuc_seconds_to_datetime(
                    sc_cuc_seconds).replace(tzinfo=timezone.utc).isoformat()
            except Exception:
                sc_time_iso = None

        time_ref = self._cached_value("obdh.time_ref")
        time_ref_i = None if time_ref is None else int(round(time_ref))
        since_sync = self._cached_value("obdh.time_since_sync_s")
        since_sync_s: Optional[float] = None
        never_synced: Optional[bool] = None
        if since_sync is not None:
            never_synced = int(round(since_sync)) >= self.TIME_SINCE_SYNC_NEVER
            since_sync_s = None if never_synced else float(since_sync)
        elif time_ref_i is not None:
            never_synced = (time_ref_i == 0)

        # ── The correlation: on-board minus sim, in seconds ─────────
        offset_s = None
        if sc_cuc_seconds is not None:
            offset_s = sc_cuc_seconds - datetime_to_cuc_seconds(sim_utc)
        warn_s = float(self._staleness_policy.get("clock_offset_warn_s", 5.0))
        alarm_s = float(self._staleness_policy.get("clock_offset_alarm_s", 30.0))
        if offset_s is None:
            offset_state = "unknown"
        elif abs(offset_s) >= alarm_s:
            offset_state = "alarm"
        elif abs(offset_s) >= warn_s:
            offset_state = "warning"
        else:
            offset_state = "nominal"

        correlation = {
            "sim_utc": sim_utc.isoformat(),
            "ground_utc": sim_utc.isoformat(),
            "sc_utc": sc_time_iso,
            "sc_cuc_coarse": (None if sc_obc_time_cuc is None
                              else int(sc_obc_time_cuc)),
            "sc_cuc_fine": None if fine_raw is None else int(fine_raw),
            "sc_cuc_seconds": sc_cuc_seconds,
            "cuc_fine_available": fine_raw is not None,
            "time_ref": time_ref_i,
            "time_ref_name": (self.TIME_REF_NAMES.get(time_ref_i, "UNKNOWN")
                              if time_ref_i is not None else None),
            "time_since_sync_s": since_sync_s,
            "never_synced": never_synced,
            "offset_s": offset_s,
            "offset_state": offset_state,
            "offset_warn_s": warn_s,
            "offset_alarm_s": alarm_s,
            "epoch": get_time_epoch().replace(tzinfo=timezone.utc).isoformat(),
            "speed": self._published_speed(),
        }

        return {
            "sim_time": sim_utc.isoformat(),
            # The browser's S9/S11 time widgets read `ground_utc`; it was never
            # actually published, so they silently fell back to the browser's
            # own wall clock. Same clock, published under both names.
            "ground_utc": sim_utc.isoformat(),
            "sc_time": sc_time_iso,       # TM 0x0309 (+fine) decoded on J2000
            "sc_obc_time_cuc": sc_obc_time_cuc,
            "sc_obc_time_cuc_s": sc_cuc_seconds,
            "sc_time_ref": time_ref_i,
            "sc_time_since_sync_s": since_sync_s,
            "time_correlation": correlation,
            "met_s": met_s,
            "speed": self._published_speed(),
            "sc_uptime_s": sc_uptime,
        }

    def _published_speed(self) -> float:
        """Time-rate multiplier as PUBLISHED to displays (disposition M11).

        In real mode wall-clock UTC advances at exactly 1.0 by definition, so
        that is what gets published regardless of any residual _sim_speed
        state (e.g. a stray POST /api/ground-time {"speed": ...}). Publishing
        the raw multiplier there would let a training control leak into a
        real-mission console. Sim mode publishes the true multiplier; the
        modern UI shows it only inside the TRAINING chip (M1), which is
        absent in real mode.
        """
        return 1.0 if self._time_source == "real" else self._sim_speed

    async def start(self) -> None:
        self._running = True
        # FIELD DEFECT 2026-08-20 (owner: "you wiped out almost all the quick
        # commands"). Nothing was wiped -- the browser was running a CACHED
        # copy of the page's own JS against a freshly-served index.html. The
        # page carries `Cache-Control: no-cache`, but the module it loads is
        # referenced with a HARD-CODED `?v=1` and no cache directive, so
        # Chrome cached it heuristically and never revalidated. Every fix
        # shipped to these files stayed invisible until a manual hard-reload,
        # and a half-updated pair renders as MISSING UI.
        #
        # `no-cache` does not mean "do not cache" -- it means "revalidate
        # before use". With the ETag aiohttp already sends, an unchanged file
        # costs one 304 and no body. Applied to the app's OWN assets only;
        # /static/vendor/ (third-party, content-stable) keeps normal caching.
        @web.middleware
        async def _no_cache_own_assets(request, handler):
            resp = await handler(request)
            p = request.path
            if p.startswith("/static/modern/") or p.startswith("/static/shared/"):
                resp.headers["Cache-Control"] = "no-cache"
            return resp

        app = web.Application(middlewares=[_no_cache_own_assets])
        app.router.add_get("/", self._handle_index)
        # Explicit generation routes: /classic and /modern always serve their
        # page regardless of the resolved mode, so the two consoles can be
        # compared live in two tabs (plan §1.2).
        app.router.add_get("/classic", self._handle_index_classic)
        app.router.add_get("/modern", self._handle_index_modern)
        app.router.add_get("/ws", self._handle_ws)
        app.router.add_get("/api/displays", self._handle_displays)
        app.router.add_get("/api/state", self._handle_state)
        app.router.add_get("/api/sim-state", self._handle_sim_state)
        # NOTE: there is deliberately no `/api/command` route on the MCS.
        # It was a disabled 410 stub kept alive only by the dead
        # `sendLegacyCommand()` in old_index.html; both are gone. Every
        # telecommand goes out over the TC TCP socket via /api/pus-command,
        # which is what makes RF gating and command logging possible.
        app.router.add_post("/api/pus-command", self._handle_pus_command)
        app.router.add_get("/api/verification-log", self._handle_verification_log)
        app.router.add_get("/api/procedures", self._handle_procedures_list)
        app.router.add_get("/api/procedures/{path:.+}", self._handle_procedure_file)
        app.router.add_get("/api/manual", self._handle_manual_list)
        app.router.add_get("/api/manual/{path:.+}", self._handle_manual_file)
        app.router.add_get("/api/positions", self._handle_positions)
        app.router.add_get("/api/tc-catalog", self._handle_tc_catalog)
        app.router.add_get("/api/param-catalog", self._handle_param_catalog)
        app.router.add_get("/api/failure-codes", self._handle_failure_codes)
        app.router.add_get("/api/hk-structures", self._handle_hk_structures)
        # FDIR window + the PUS report displays it shares data with
        app.router.add_get("/api/fdir", self._handle_fdir_display)
        app.router.add_get("/api/schedule", self._handle_schedule_report)
        app.router.add_get("/api/memory-map", self._handle_memory_map)
        app.router.add_get("/api/ground-stations", self._handle_ground_stations)
        app.router.add_post("/api/ground-time", self._handle_ground_time_adjust)
        # Procedure execution endpoints
        app.router.add_post("/api/procedure/load", self._handle_proc_load)
        # Planner schedule → on-board S11 time-tagged commands (defect #44)
        app.router.add_post("/api/schedule/upload-s11",
                            self._handle_s11_schedule_upload)
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
        app.router.add_get("/api/procedure/cards", self._handle_proc_cards)
        app.router.add_get("/api/procedure/tlm-vocabulary",
                           self._handle_proc_tlm_vocabulary)
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
        app.router.add_get("/api/limits", self._handle_limits)
        app.router.add_get("/api/annotations", self._handle_annotations)
        app.router.add_get("/api/display-config", self._handle_display_config)
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
        app.router.add_get("/api/archive/commands", self._handle_archive_commands)
        app.router.add_get("/api/archive/param-names", self._handle_archive_param_names)
        # New display panels (Feature 1-6)
        app.router.add_get("/api/displays/contact-schedule", self._handle_contact_schedule)
        app.router.add_get("/api/displays/power-budget", self._handle_power_budget)
        app.router.add_get("/api/displays/fdir-alarms", self._handle_fdir_alarms)
        app.router.add_get("/api/displays/system-overview", self._handle_system_overview)
        app.router.add_post("/api/displays/alarms/{alarm_id}/ack", self._handle_alarm_ack_display)
        app.router.add_post("/api/displays/alarm-trends", self._handle_alarm_trends)

        # ── design_2026-08 POSITION_NEEDS additions (additive only) ────
        # G5: predicted link chain for the TT&C overlay.
        app.router.add_get("/api/displays/link-prediction",
                           self._handle_link_prediction)
        # G6: fault-propagation graph for first-out alarm grouping.
        app.router.add_get("/api/fault-graph", self._handle_fault_graph)
        # G7: the GO/NO-GO call matrix, config-mirrored from the manual.
        app.router.add_get("/api/gonogo-matrix", self._handle_gonogo_matrix)
        # G8: append-only console/mission log.
        app.router.add_get("/api/console-log", self._handle_console_log_get)
        app.router.add_post("/api/console-log", self._handle_console_log_post)

        # Delayed-TM (S15) dump store: JSON listing, raw download, decoded
        # summary and a self-contained browser page at /dumps. Served from the
        # MCS origin so any student browser on the network can retrieve
        # downlinked stored telemetry without a shared filesystem (defect #90).
        from smo_common.dump_store import register_dump_routes
        # `config_dir` puts parameter NAMES on the decoded S12 tables the
        # summary now returns — `eps.bat_soc`, not a bare `0x0101`.
        register_dump_routes(app, lambda: self._dump_dir, owner="MCS",
                             config_dir=self.config_dir)

        # Delayed-TM report generator (Phase F, brief 06 §A): template-driven
        # self-contained HTML reports over the dump archive. Same
        # one-implementation-many-hosts pattern as the dump store; generation
        # runs in a thread executor so the websocket keeps serving.
        try:
            from smo_common.reporting import register_report_routes
            register_report_routes(app, lambda: self._dump_dir,
                                   self.config_dir, owner="MCS",
                                   get_command_log=self._report_command_log)
        except Exception as e:  # noqa: BLE001 — never block the MCS UI
            logger.warning("Delayed-TM report API not mounted: %s", e)

        # Read-only configuration browser (Phase F, brief 06 §B). Students
        # can BROWSE configuration and its history from the MCS; every
        # mutating route is absent by construction (readonly=True) — the
        # write surface lives only on the simulator instructor server.
        try:
            from smo_common.confedit import register_config_routes
            register_config_routes(app, self.config_dir, readonly=True,
                                   page_path="/config", owner="MCS")
        except Exception as e:  # noqa: BLE001
            logger.warning("Config browser not mounted: %s", e)

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
        # Shared design system (design_2026-08 plan §1.1): one canonical
        # runtime copy served from smo_common package data, mounted at the
        # SAME URL by all three servers so pages and split deployments agree.
        # NOTE: /static/shared/ must be mounted BEFORE /static/ — aiohttp
        # resolves routes in registration order and the broader /static/
        # mount would otherwise shadow it.
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
        logger.info("MCS server started on %s:%d", self.bind_host,
                    self.http_port)

        # Start command queue processor as a background task
        asyncio.create_task(self._command_processor())

        # Start state polling, TM receive, and TC connection in parallel.
        # In SIM mode, also run the closed-loop sim-clock sync loop.
        loops = [
            self._tm_receive_loop(),
            self._state_poll_loop(),
            self._tc_connect_loop(),
        ]
        if self._time_source == "sim":
            loops.append(self._sim_state_poll_loop())
        try:
            await asyncio.gather(*loops)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Release resources that outlive the event loop (D-5).

        Nothing closed the SQLite handle, so the archive's WAL was left
        behind on every exit.
        """
        self._running = False
        try:
            self._archive.close()
        except Exception as e:
            logger.debug("Archive close failed: %s", e)

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
        self.tm_packets_received += 1
        # RECEIVE TIMESTAMP, in SIM TIME (spec Part D.2). Every age the
        # console shows — link liveness, per-parameter freshness, HK SID
        # PERIODIC/SILENT, the FDIR journal — is a difference against this, so
        # it has to be on the same clock as the thresholds in
        # mcs/staleness.yaml, which are sim seconds. On wall clock, pausing
        # the simulator made everything stale within a minute and running at
        # x60 made nothing ever stale.
        now = self.sim_now_ts()
        # The spacecraft's own timestamp for this packet, full precision
        # (coarse + fine/65536) on the J2000 mission epoch. Archived alongside
        # the sim-time stamp so a row can be correlated with the on-board
        # clock; `None` if the header could not be read.
        try:
            obc_cuc: Optional[float] = float(pkt.secondary.cuc_seconds)
        except Exception:
            obc_cuc = None
        self._last_obc_cuc = obc_cuc

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
            # Record the arrival of this SID regardless of whether we can decode
            # its parameters — that is what makes the HK Manager's PERIODIC /
            # SILENT state observed rather than assumed (defect #68).
            if decoded and decoded.get("sid") is not None:
                async with self._param_cache_lock:
                    self._sid_last_seen[int(decoded["sid"])] = now
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
                # On-demand reads are limit-checked too: four limited
                # parameters (0x0539-0x053C) are S20-only, so this is the only
                # path that will ever see them.
                self._tm_processor._check_limit(int(pid), float(value))
            except Exception:
                pass
        else:
            # Any other TM packet still updates the link liveness timer.
            async with self._param_cache_lock:
                self._last_tm_frame_ts = now

        # Parse S1 verification TM.
        #
        # S1.2 / S1.8 : request_id(u32) error_code(u16) [reason_len(u8) reason]
        # S1.4        : request_id(u16) error_code(u16) [reason_len(u8) reason]
        #
        # The trailing failure notice is optional and strictly appended, so a
        # spacecraft that does not send one decodes exactly as before
        # (defect #87). `VerificationData.parse` owns both layouts.
        if pkt.secondary.service == 1 and len(pkt.data_field) >= 4:
            vd = VerificationData.parse(pkt.data_field, pkt.secondary.subtype)
            tc_seq = vd.tc_seq
            error_code = vd.error_code
            reason = vd.reason

            verif = {
                "tc_seq": tc_seq,
                "subtype": pkt.secondary.subtype,
                "error_code": error_code,
                "reason": reason,
                "error_text": (failure_code_text(error_code)
                               if error_code else ""),
            }
            info["verification"] = verif

            # State machine shared by the session log and the archive: the
            # persistent `tc_log` row has to follow the same SENT → ACCEPTED →
            # COMPLETED / REJECTED / FAILED transitions, otherwise the debrief
            # export shows every command as merely SENT
            # (`update_command_state` had zero callers).
            new_state = None
            if pkt.secondary.subtype == 1:
                new_state = "ACCEPTED"
            elif pkt.secondary.subtype == 2:
                new_state = "REJECTED"
            elif pkt.secondary.subtype in (4, 8):
                new_state = "FAILED"
            elif pkt.secondary.subtype == 7:
                new_state = "COMPLETED"
            if new_state:
                self._archive_command_state(tc_seq, new_state)
                verif["state"] = new_state
                # Console/mission log (G8): the S1 verdict completes the TC
                # entry appended at send time. Terminal states only —
                # ACCEPTED is progress, not a verdict.
                if new_state in ("COMPLETED", "REJECTED", "FAILED"):
                    detail = f" — {reason}" if reason else ""
                    self._console_log(
                        "tc",
                        f"TC seq {tc_seq} S1 verdict: {new_state}{detail}",
                        ref=f"seq:{tc_seq}")

            for entry in self._verification_log:
                if entry.get("seq") == tc_seq:
                    if new_state:
                        entry["state"] = new_state
                    if pkt.secondary.subtype in (2, 4, 8):
                        entry["error_code"] = error_code
                        # Keep the on-board wording verbatim: it names the
                        # exact gate that refused the command.
                        entry["reason"] = reason
                        entry["error_text"] = failure_code_text(
                            error_code, service=entry.get("service"))
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
            # Defect #88: an operator reading the event log saw "EVT-779"
            # and a sentence. Attach the catalogue NAME and, for a rejection,
            # the failure code broken out with its plain-English meaning, so
            # every error surface can render "0x030B COMMAND_REJECTED —
            # 0x0007 Not permitted in the current state — <reason>" without
            # holding its own copy of either vocabulary.
            meta = self._event_meta.get(event_id, {})
            info["event"] = {
                "event_id": event_id,
                "severity": severity,
                "description": aux_text,
                "name": meta.get("name", ""),
                "subsystem": meta.get("subsystem", ""),
            }
            if event_id == COMMAND_REJECTED_EVENT_ID:
                rejection = parse_command_rejection(aux_text)
                if rejection:
                    info["event"]["rejection"] = rejection

        # ── FDIR / PUS report telemetry (defects #76, #77, #78) ──────
        # Every one of these used to fall through to an opaque "S12.13 (NB)"
        # line in the raw packet log: the MCS had no branch for the monitoring
        # definition table, the event-action table, the schedule report or the
        # memory reports, so no display could ever show them.
        svc, sub = pkt.secondary.service, pkt.secondary.subtype

        # S12.9 / S12.10 — check transition reports (now the full 18-byte form)
        if svc == 12 and sub in (9, 10):
            mon = pus_reports.parse_s12_transition(pkt.data_field, sub)
            if mon:
                info["monitoring"] = mon
                journal_entry = self._fdir_model.ingest_s12_transition(mon, ts=now)
                info["fdir_journal"] = journal_entry
                self._sync_legacy_fdir_panel()

        # S12.13 — full on-board monitoring definition table
        elif svc == 12 and sub == 13:
            defs = pus_reports.parse_s12_definitions(pkt.data_field)
            self._fdir_model.ingest_s12_definitions(defs, ts=now)
            self._sync_legacy_fdir_panel()
            info["monitor_defs"] = {"count": len(defs)}

        # S19.128 — full event-action rule table
        elif svc == 19 and sub == 128:
            rules = pus_reports.parse_s19_definitions(pkt.data_field)
            self._fdir_model.ingest_s19_definitions(rules, ts=now)
            self._sync_legacy_fdir_panel()
            info["ea_defs"] = {"count": len(rules)}

        # S11.18 — detailed on-board schedule report
        elif svc == 11 and sub == 18:
            report = pus_reports.parse_s11_schedule(pkt.data_field)
            if report is not None:
                self._annotate_schedule(report)
                self._schedule_report = report
                info["schedule"] = report

        # S6.6 / S6.10 — memory dump and memory check (CRC) reports
        elif svc == 6 and sub == 6:
            dump = pus_reports.parse_s6_dump(pkt.data_field)
            if dump:
                dump["received_ts"] = now
                dump["region"] = self._memory_region_name(dump["address"])
                self._memory_dumps.appendleft(dump)
                info["memory_dump"] = dump
        elif svc == 6 and sub == 10:
            chk = pus_reports.parse_s6_check(pkt.data_field)
            if chk:
                chk["received_ts"] = now
                chk["region"] = self._memory_region_name(chk["address"])
                self._memory_checks.appendleft(chk)
                info["memory_check"] = chk

        # -- Alarm journal: capture S5 events (severity >= 3) and S12 OOL --
        if pkt.secondary.service == 5 and info.get("event"):
            evt = info["event"]
            # FDIR journal + S19 rule-fire correlation. Synthetic 0x9xxx /
            # 0xAxxx S12 mirror events are filed against the parameter they
            # encode instead of showing as unknown event ids.
            entry = self._fdir_model.ingest_event(
                evt["event_id"], evt.get("severity", 1),
                evt.get("description", ""), ts=now,
                rejection=evt.get("rejection"))
            if entry is not None:
                info["fdir_journal"] = entry
            # Archive every S5 event
            try:
                self._archive.store_event(
                    evt["event_id"], evt.get("severity", 0),
                    self._event_id_to_subsystem(evt["event_id"]),
                    evt.get("description", ""),
                    utc_epoch=now,      # sim time the ground received it
                    obc_cuc=obc_cuc,    # on-board time the S5 packet carried
                )
            except Exception:
                pass

            if evt.get("severity", 0) >= 3:
                await self._raise_alarm({
                    "severity": evt["severity"],
                    "subsystem": self._event_id_to_subsystem(evt["event_id"]),
                    "parameter": f"EVT-{evt['event_id']}",
                    "event_id": int(evt["event_id"]),
                    "name": evt.get("name", ""),
                    "value": evt.get("description", ""),
                    "limit": "",
                    "source": "S5",
                })

        if pkt.secondary.service == 12 and pkt.secondary.subtype == 9:
            mon = info.get("monitoring")
            if mon:
                await self._raise_alarm({
                    "severity": 3,
                    "subsystem": self._param_id_to_subsystem(mon["param_id"]),
                    "parameter": f"0x{mon['param_id']:04X}",
                    "value": str(round(mon["value"], 4)),
                    "limit": "OOL",
                    "source": "S12",
                })

        # -- Ground limit checking (WP-3) -----------------------------------
        # TMProcessor raises one entry per limit-state *transition* (including
        # the return to nominal), so a parameter parked out of band does not
        # fill the journal at the HK rate. 4 of the 51 limited parameters have
        # no S12 definition on board; for those this is the only check there is.
        ground_alarms = self._tm_processor.pop_alarms()
        for ga in ground_alarms:
            pid = int(ga["param_id"])
            status = ga.get("status", "")
            if ga.get("cleared"):
                await self._clear_ground_alarm(pid, ga)
                continue
            if status == "monitoring_violation":
                # Raised by TMProcessor._process_monitoring; the S12 branch
                # above already journals those from the decoded packet.
                continue
            band = ga.get("band") or {}
            red = status.startswith("red")
            edge = ("low" if status.endswith("low") else "high")
            thresh = band.get(f"{'red' if red else 'yellow'}_{edge}")
            meta = self._param_meta.get(pid, {})
            await self._raise_alarm({
                "severity": 4 if red else 3,
                "subsystem": (meta.get("subsystem")
                              or self._param_id_to_subsystem(pid)),
                "parameter": f"0x{pid:04X}",
                "name": meta.get("name", ""),
                "value": str(round(float(ga.get("value", 0.0)), 4)),
                "limit": (f"{status.upper()} "
                          f"{'>' if edge == 'high' else '<'} {thresh}"
                          if thresh is not None else status.upper()),
                "units": meta.get("units", ""),
                "source": "LIMIT",
                "limit_status": status,
                "band": band,
            })

        # Parse S15.14 storage status reports and update cached store counts.
        # The record is VARIABLE length (12 + name_len); the previous
        # divide-the-payload stride guess only worked while every store's name
        # happened to be the same length (defect #73).
        if pkt.secondary.service == 15 and pkt.secondary.subtype == 14:
            try:
                stores = pus_reports.parse_s15_store_status(pkt.data_field)
                if stores:
                    info["storage_status"] = stores
                    async with self._param_cache_lock:
                        self._storage_status = stores
            except Exception as e:
                logger.debug("S15.14 parse failed: %s", e)

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

    async def _sim_state_poll_loop(self):
        """Closed-loop sim clock sync (SIM mode only).

        Every ~1.5 s, GET the simulator's state endpoint, parse `sim_time`
        (ISO) and `speed`, and RE-ANCHOR the open-loop ground clock so
        sim_now() tracks the simulator's real sim_time through
        pause / speed change / breakpoint load (bounded drift = poll
        interval). On poll failure, keep the last anchor (debug log).

        Does nothing in REAL mode — that path returns wall-clock UTC.
        This loop never touches spacecraft OBC time (TM 0x0309).
        """
        if self._time_source != "sim":
            return
        while self._running:
            try:
                timeout = aiohttp.ClientTimeout(total=2.0)
                async with aiohttp.ClientSession(timeout=timeout) as sess:
                    async with sess.get(self._sim_state_url) as resp:
                        payload = await resp.json()
                sim_time_str = payload.get("sim_time")
                speed = payload.get("speed")
                if sim_time_str:
                    parsed = datetime.fromisoformat(
                        str(sim_time_str).replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    self._detect_sim_time_jump(parsed)
                    # Re-anchor: ground_utc = epoch + (now-start)*speed + offset.
                    # We preserve any manual _ground_time_offset the operator set.
                    self._ground_epoch = parsed
                    self._ground_start_wall = _time_mod.time()
                    if speed is not None:
                        self._sim_speed = float(speed)
                # If the simulator ever reports its real HK enable bits, prefer
                # them over inference. Today it does not publish them; this is
                # deliberately optional so the display improves for free when a
                # sim-side `hk_enabled` map lands (defect #68 hand-off).
                self._ingest_reported_hk_enabled(payload.get("hk_enabled"))
                self._ingest_hk_enable_reset(payload.get("hk_enable_reset"))
                self._ingest_sim_health(payload)
                self._ingest_orbit_elset(payload)
                self._ingest_sim_illumination(payload)
            except Exception as e:
                logger.debug("sim-state poll failed (keeping last anchor): %s", e)
            await asyncio.sleep(1.5)

    def _ingest_sim_illumination(self, payload: dict) -> None:
        """Cache the simulator's PRESENT-TIME eclipse state (the authority).

        The MCS owns an orbit propagator because it has to PREDICT eclipse
        (next entry/exit, the SoC-at-eclipse-exit projection). It must not use
        it to re-derive what the eclipse state is RIGHT NOW: that answer is
        computed on board, from the same geometry but on the simulator's
        clock, and the MCS clock is an anchored extrapolation of that clock.
        Two clocks + one geometry = two answers near the terminator, which is
        exactly how the console came to show ECLIPSE next to a positive
        `eps.power_gen` (and how the AOCS looked "in eclipse" when the planner
        said otherwise).

        SIM-ONLY, like the elset ingest: in real mode there is no such
        endpoint and the ground propagator remains the only source.
        """
        if self._time_source != "sim" or not isinstance(payload, dict):
            return
        if "in_eclipse" not in payload:
            return
        try:
            self._sim_illum = {
                "in_eclipse": bool(payload.get("in_eclipse")),
                "sun_fraction": (float(payload["sun_fraction"])
                                 if payload.get("sun_fraction") is not None
                                 else (0.0 if payload.get("in_eclipse") else 1.0)),
                "in_penumbra": bool(payload.get("in_penumbra", False)),
                "shadow_model": payload.get("shadow_model") or "",
                "time_to_eclipse_entry_s": payload.get("time_to_eclipse_entry_s"),
                "time_to_eclipse_exit_s": payload.get("time_to_eclipse_exit_s"),
                "wall": _time_mod.time(),
            }
        except (TypeError, ValueError):
            self._sim_illum = None

    #: How stale (wall seconds) the cached sim illumination may be before the
    #: MCS falls back to its own propagator. Three poll intervals.
    _SIM_ILLUM_MAX_AGE_S = 4.5

    def _ingest_orbit_elset(self, payload: dict) -> None:
        """Adopt the simulator's post-burn element set (Phase D).

        A propulsive burn re-fits the TLE and bumps orbit_epoch_version in
        /api/state. The MCS ground track / orbital-state display propagates
        its OWN Satrec (built from orbit.yaml at startup), so without this
        the display would show the pre-burn orbit forever. Rebuild the
        propagator from the served TLE when the version changes; version 0
        (or an absent elset) means the un-manoeuvred config orbit.

        SIM-ONLY (disposition L3): the element set comes from the simulator's
        state endpoint, which only exists in training. In real mode the MCS
        keeps the configured TLE — honest behaviour, since no ground process
        exists yet to hand it a post-manoeuvre elset. Operator TLE upload is
        future work; when it lands it gets its own (real-mode) path, not this
        one. Today this is defence in depth — the only caller is
        _sim_state_poll_loop, which never runs in real mode.
        """
        if self._time_source != "sim":
            return
        try:
            ev = payload.get("orbit_epoch_version")
            if ev is None or int(ev) == getattr(self, "_orbit_epoch_version", 0):
                return
            elset = payload.get("orbit_elset") or {}
            l1, l2 = elset.get("tle_line1"), elset.get("tle_line2")
            if not (l1 and l2):
                if int(ev) != 0:
                    return
                # Reverted (e.g. breakpoint load of a pre-burn snapshot):
                # rebuild from orbit.yaml.
                self._orbit_epoch_version = 0
                self._init_orbit_propagator()
                return
            from smo_common.orbit.propagator import OrbitPropagator
            gs = getattr(getattr(self, "_orbit_prop", None),
                         "_ground_stations", None) or []
            self._orbit_prop = OrbitPropagator(str(l1), str(l2),
                                               ground_stations=list(gs))
            self._orbit_epoch_version = int(ev)
            logger.info("MCS orbit element set updated (epoch_version %s) — "
                        "ground track now follows the post-burn orbit", ev)
        except Exception as e:  # noqa: BLE001 — display must keep running
            logger.debug("orbit elset ingest failed: %s", e)

    def _detect_sim_time_jump(self, parsed: datetime) -> float:
        """Detect a sim-clock discontinuity and drop epoch-bound state.

        Defect #79 — the same gap the planner had (defect #59). The poll loop
        re-anchored the ground clock unconditionally, so a breakpoint load, a
        scenario reset or an instructor time jump moved ground UTC silently.
        Everything the MCS derived at the OLD epoch then described a moment
        that no longer exists: the schedule report's UTC execution times
        (converted through the OBC clock correlation), the S15 store status,
        the S6 dump/check results, and the FDIR journal's timeline.

        Compares the new sim_time against what the PREVIOUS anchor predicted
        for this wall instant; anything beyond the tolerance is a jump.
        Returns the delta in seconds (0.0 when no anchor exists yet).
        """
        if self._ground_epoch is None:
            return 0.0
        wall = _time_mod.time()
        predicted = self._ground_epoch + timedelta(
            seconds=(wall - self._ground_start_wall) * self._sim_speed)
        delta_s = (parsed - predicted).total_seconds()
        # Tolerate normal poll-interval slop, scaled by speed.
        tol = max(self._jump_tolerance_s,
                  3.0 * 1.5 * max(1.0, abs(self._sim_speed)))
        if abs(delta_s) <= tol:
            return delta_s
        self._jump_count += 1
        self._last_jump_utc = parsed.isoformat()
        self._last_jump_delta_s = delta_s
        logger.info(
            "Sim clock discontinuity: %+.1f s (now %s) — dropping "
            "epoch-bound MCS state (schedule, stores, memory reports)",
            delta_s, parsed.isoformat())
        # Drop everything whose meaning is tied to the old epoch. The
        # parameter cache is deliberately KEPT: its values are still the
        # spacecraft's last known state. Its `last_update_ts` stamps are on
        # SIM time, so a jump makes every age jump with it — which is the
        # honest answer: after a breakpoint load the cached values really do
        # describe a moment that is now `delta_s` away, and the staleness gate
        # should say so rather than pretend the data is fresh.
        self._schedule_report = {}
        self._storage_status = []
        self._memory_dumps.clear()
        self._memory_checks.clear()
        # Re-propagate immediately at the new epoch rather than waiting for the
        # next state poll to notice.
        if self._orbit_prop is not None:
            try:
                self._orbit_prop.reset(parsed)
            except Exception:           # pragma: no cover
                pass
        return delta_s

    # ── TM data-rate ladder (defect #86) ────────────────────────────

    #: How a coding scheme is written on the console. The values are the
    #: `coding` enum of ttc.yaml tm_rate_tiers.
    _CODING_LABELS = {
        "CONV_RS": "conv+RS",
        "RS": "RS(255,223)",
        "NONE": "uncoded",
    }

    def _load_tm_rate_ladder(self) -> list[dict]:
        """Read ``subsystems/ttc.yaml tm_rate_tiers`` for the displays.

        The console must never carry its own copy of the rate table. The old
        UI printed a bare number ("64000 bps"); with a ladder that is not
        enough — an operator needs the TIER, because the tier is what carries
        the modulation, the coding and the permission gates. Every label the
        TT&C page draws is built from this list, so changing a `bps` in
        ttc.yaml changes what the console says.
        """
        tiers: list[dict] = []
        try:
            import yaml as _yaml
            path = self.config_dir / "subsystems" / "ttc.yaml"
            cfg = _yaml.safe_load(path.read_text()) or {}
            for idx, t in enumerate(cfg.get("tm_rate_tiers", []) or []):
                bps = int(t.get("bps", 0))
                coding = str(t.get("coding", "RS")).upper()
                modulation = str(t.get("modulation", "BPSK")).upper()
                tiers.append({
                    "index": idx,
                    "name": str(t.get("name", f"TIER{idx}")),
                    "bps": bps,
                    "kbps": round(bps / 1000.0, 3),
                    "modulation": modulation,
                    "coding": coding,
                    "coding_label": self._CODING_LABELS.get(coding, coding),
                    "command_code": int(t.get("command_code", idx)),
                    "beacon_only": bool(t.get("beacon_only", False)),
                    "requires_antenna": bool(t.get("requires_antenna", True)),
                    "requires_downlink_mode": bool(
                        t.get("requires_downlink_mode", False)),
                    "min_elevation_deg": float(t.get("min_elevation_deg", 0.0) or 0.0),
                    "label": self._tier_label_for(
                        str(t.get("name", f"TIER{idx}")), bps, modulation, coding),
                })
        except Exception as e:
            logger.warning("ttc.yaml tm_rate_tiers unreadable (%s) — the TT&C "
                           "page will show bare bit rates", e)
        return tiers

    @classmethod
    def _tier_label_for(cls, name: str, bps: int, modulation: str,
                        coding: str) -> str:
        """``LOW (16 kbps, BPSK, conv+RS)`` — the console's rate string."""
        kbps = bps / 1000.0
        rate = f"{kbps:g} kbps" if bps >= 1000 else f"{bps} bps"
        return (f"{name} ({rate}, {modulation}, "
                f"{cls._CODING_LABELS.get(coding, coding)})")

    def _tier_by_index(self, index: Any) -> dict | None:
        try:
            i = int(index)
        except (TypeError, ValueError):
            return None
        if 0 <= i < len(self._tm_rate_ladder):
            return self._tm_rate_ladder[i]
        return None

    def _build_link_tier_view(self, ttc_group: dict) -> dict:
        """Resolve the ACTIVE and COMMANDED tiers for the console.

        Telemetry is preferred (0x0539 active, 0x053A commanded, 0x053B symbol
        rate, 0x053C DOWNLINK mode); the simulator's state block is the
        fallback for a console running without those parameters downlinked.

        The active/commanded distinction is the point of the display. They
        differ exactly when a permission gate has demoted the link — antenna
        stowed, DOWNLINK mode not entered, or elevation below the HIGH tier's
        10 deg — and hiding that would turn a diagnosable condition ("HIGH was
        commanded, the gate refused it") into a mystery ("why is the dump
        slow?").
        """
        sim = self._link_tier_state or {}
        view: dict[str, Any] = {}

        active_idx = ttc_group.get("tm_tier", sim.get("tm_tier"))
        cmd_idx = ttc_group.get("tm_tier_cmd", sim.get("tm_tier_cmd"))
        if cmd_idx is None:
            cmd_idx = active_idx

        active = self._tier_by_index(active_idx)
        commanded = self._tier_by_index(cmd_idx)

        if active is not None:
            view["tm_tier"] = active["index"]
            view["tm_tier_name"] = active["name"]
            view["tm_tier_label"] = active["label"]
            view["tm_coding"] = active["coding"]
            view["tm_modulation"] = active["modulation"]
        elif sim.get("tm_tier_name"):
            view["tm_tier_name"] = sim["tm_tier_name"]
            view["tm_tier_label"] = sim["tm_tier_name"]
        if sim.get("tm_coding") and "tm_coding" not in view:
            view["tm_coding"] = sim["tm_coding"]

        if commanded is not None:
            view["tm_tier_cmd"] = commanded["index"]
            view["tm_tier_cmd_name"] = commanded["name"]
            view["tm_tier_cmd_label"] = commanded["label"]

        if active is not None and commanded is not None:
            demoted = commanded["index"] > active["index"]
            view["tm_tier_demoted"] = demoted
            if demoted:
                view["tm_tier_demotion_reason"] = self._demotion_reason(
                    commanded, ttc_group, sim)

        sym = ttc_group.get("tm_symbol_rate", sim.get("symbol_rate_bd"))
        if sym is not None:
            view["symbol_rate_bd"] = float(sym)
        dl = ttc_group.get("downlink_mode", sim.get("downlink_mode"))
        if dl is not None:
            view["downlink_mode"] = bool(dl)
        return view

    @staticmethod
    def _demotion_reason(commanded: dict, ttc_group: dict, sim: dict) -> str:
        """Best available explanation for why the commanded tier is not active."""
        if commanded.get("requires_antenna") and not (
                ttc_group.get("antenna_deployed") or sim.get("antenna_deployed")):
            return f"{commanded['name']} needs the antenna deployed"
        if commanded.get("requires_downlink_mode") and not (
                ttc_group.get("downlink_mode") or sim.get("downlink_mode")):
            return (f"{commanded['name']} needs DOWNLINK mode "
                    f"(TTC_SET_DOWNLINK_MODE, S8 func 79)")
        # 2026-08c item 5: min_elevation_deg is a GROUND policy now — the
        # spacecraft no longer demotes on elevation, so an active/commanded
        # mismatch is never explained by it. The remaining on-board gates
        # are antenna, DOWNLINK mode and the beacon-mode latch.
        return (f"{commanded['name']} refused by an on-board permission gate "
                f"(antenna / DOWNLINK mode / beacon-mode latch)")

    def _ingest_sim_health(self, payload: dict) -> None:
        """Absorb the simulator-reported health fields the MCS can display.

        ``sc_mode`` is NOT downlinked as telemetry, so the FDIR
        window would have nothing to show for spacecraft mode. (The FDIR
        summary parameters 0x0700-0x0703 now ride platform HK SID 4 —
        disposition M5 — so the modern console derives its FDIR story from
        TM; this sim-state path remains for the classic page and carries its
        source tag.) The MCS already
        polls this endpoint for its clock anchor and for `hk_enabled`; taking
        `sc_mode` from the same place is the same trade. It is tagged with its
        source so the display can say "SIM" rather than implying telemetry.
        """
        if not isinstance(payload, dict):
            return
        self._fdir_model.set_sc_mode(payload.get("sc_mode"), source="sim_state",
                                     ts=self.sim_now_ts())
        self._sim_health = {
            "engine_fault": payload.get("engine_fault"),
            "engine_fault_count": payload.get("engine_fault_count", 0),
            "tm_rate_bps": payload.get("tm_rate_bps"),
            "rt_tm_backlog": payload.get("rt_tm_backlog"),
            "sc_mode": payload.get("sc_mode"),
            "source": "sim_state",
        }
        sched = payload.get("scheduler")
        if isinstance(sched, dict):
            self._sim_health["scheduler_enabled"] = sched.get("enabled")
        # Autonomy block (WP-5): active faults, pending cascades and recovery
        # procedure progress. Until Phase G the fault-cascade model and the ten
        # recovery procedures never ran, so the "what is the spacecraft doing
        # about it" half of the FDIR window had no data source at all.
        fdir_block = payload.get("fdir")
        if isinstance(fdir_block, dict):
            self._sim_autonomy = {**fdir_block, "source": "sim_state"}
        # TM rate ladder (defect #86). The simulator's `ttc` block carries the
        # tier state; it is the console's fallback for the ladder parameters
        # (0x0539-0x053C) when they are not in the downlinked HK set.
        ttc_block = payload.get("ttc")
        if isinstance(ttc_block, dict):
            self._link_tier_state = {
                k: ttc_block[k] for k in (
                    "tm_tier", "tm_tier_name", "tm_coding", "symbol_rate_bd",
                    "downlink_mode", "data_rate_bps", "elevation_deg",
                ) if k in ttc_block
            }
            self._sim_health["tm_tier"] = ttc_block.get("tm_tier")
            self._sim_health["tm_tier_name"] = ttc_block.get("tm_tier_name")

    def _ingest_reported_hk_enabled(self, reported: Any) -> None:
        """Absorb an authoritative ``{sid: bool}`` HK enable map, if offered.

        Accepts either a mapping or a list of ``{"sid":…, "enabled":…}`` dicts,
        and tolerates string keys. Anything unparseable is ignored — a missing
        or malformed map must never degrade the observed-state display.
        """
        if not reported:
            return
        pairs: list[tuple[Any, Any]] = []
        if isinstance(reported, dict):
            pairs = list(reported.items())
        elif isinstance(reported, list):
            for item in reported:
                if isinstance(item, dict) and "sid" in item:
                    pairs.append((item.get("sid"), item.get("enabled")))
        for sid, enabled in pairs:
            try:
                self._sid_enabled_reported[int(sid)] = bool(enabled)
            except (TypeError, ValueError):
                continue

    def _ingest_hk_enable_reset(self, reported: Any) -> None:
        """Absorb the spacecraft's explanation of a wholesale HK enable reset.

        The simulator raises S5 0x0056 and publishes the same record on its
        state endpoint whenever a boot or a bootloader entry replaces the HK
        enable set. Keeping it here lets the HK Manager answer the operator's
        actual question — "who turned my SIDs off?" — instead of leaving them
        to infer it from a gap in the next dump.
        """
        if not isinstance(reported, dict):
            return
        reason = reported.get("reason")
        if not reason:
            return
        self._hk_enable_reset = {
            "reason": str(reason),
            "sids_disabled": [int(s) for s in reported.get("sids_disabled", [])
                              if isinstance(s, (int, float, str))
                              and str(s).lstrip("-").isdigit()],
            "sids_enabled": [int(s) for s in reported.get("sids_enabled", [])
                             if isinstance(s, (int, float, str))
                             and str(s).lstrip("-").isdigit()],
            "enabled_now": [int(s) for s in reported.get("enabled_now", [])
                            if isinstance(s, (int, float, str))
                            and str(s).lstrip("-").isdigit()],
            "obc_time_s": reported.get("obc_time_s"),
            "source": "sim_state",
        }

    async def _state_poll_loop(self):
        """Periodically build state from param_cache and broadcast.

        Parameters are populated only from TM TCP socket HK packets.
        If no TM frame received in 60s, mark state as stale.
        """
        while self._running:
            try:
                # Sim time: every age published below is a difference against
                # this and a sim-second threshold (spec Part D.2).
                now = self.sim_now_ts()
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

                # Compute liveness/staleness — ages in SIM SECONDS.
                #
                # Clamped at zero because sim time can jump BACKWARDS (a
                # breakpoint load, a scenario reset). A negative age is not a
                # useful thing to publish; "0 s" says "we have telemetry for
                # this instant", which is true, and the next frame corrects it.
                if last_tm_ts is None:
                    stale = True
                    age_s: Optional[float] = None
                else:
                    age_s = max(0.0, now - last_tm_ts)
                    stale = age_s > 60.0

                data: dict = {
                    "stale": stale,
                    "last_frame_age_s": age_s,
                    # Tells the browser (and anyone reading /api/state) which
                    # clock every *_age_s and *_ts field below is on, so it
                    # never has to guess whether to age them with Date.now().
                    "age_time_base": "sim",
                    "params": params_flat,
                    "param_count": len(cache),
                }
                # Merge grouped subsystem dicts at top level for legacy bindings
                data.update(grouped)
                # TM data-rate ladder (defect #86): the console shows the TIER,
                # not a bare bit rate, and shows ACTIVE vs COMMANDED so a
                # gate-demoted link is visible instead of merely slow.
                if self._tm_rate_ladder:
                    data["tm_rate_ladder"] = self._tm_rate_ladder
                ttc_view = self._build_link_tier_view(data.get("ttc") or {})
                if ttc_view:
                    data.setdefault("ttc", {}).update(ttc_view)
                # Ground-system computed data: orbit from TLE propagation,
                # time from ground clock, contact from orbital geometry.
                data.update(self._compute_orbital_state())
                data.update(self._compute_time_state())

                # Per-subsystem staleness (age of newest param in each group)
                subsystem_staleness = {}
                for pid, entry in cache.items():
                    meta = self._param_meta.get(pid)
                    if not meta:
                        continue
                    sub = meta["subsystem"] or "other"
                    param_age = max(0.0, now - entry.get("last_update_ts", 0))
                    if sub not in subsystem_staleness or param_age < subsystem_staleness[sub]:
                        subsystem_staleness[sub] = param_age
                data["subsystem_staleness"] = {
                    k: round(v, 1) for k, v in subsystem_staleness.items()
                }
                # Per-parameter age in seconds — lets the UI mark individual
                # telemetry cells as STALE when their last update is older than
                # the expected HK interval, not just the subsystem as a whole.
                data["params_age"] = {
                    str(pid): round(max(0.0, now - entry.get("last_update_ts", 0)), 1)
                    for pid, entry in cache.items()
                }
                # Ground limit state per parameter (WP-3). The displays colour
                # from this; `param_limit_bands` gives them the numbers for the
                # tooltip so the band does not have to be duplicated in JS.
                data["param_limit_state"] = {
                    f"0x{pid:04X}": status
                    for pid, status in self._tm_processor.limit_statuses().items()
                }
                data["unacked_alarms"] = self.unacknowledged_alarm_count()
                # Ground-derived parameter block v2 (G1+G3): projections
                # from received TM + archive slope + orbit state. Each entry
                # carries {id, value, valid, reason}; invalid entries are
                # published AS invalid — displays must show the state, not
                # substitute a number.
                try:
                    data["derived"] = self._derived_engine.compute(data)
                except Exception as e:  # noqa: BLE001
                    logger.debug("derived-param compute failed: %s", e)
                    data["derived"] = {}
                data["sa"] = self._build_sa_block(data, age_s, stale)
                # Per-SID observation for the HK Manager: how long since each
                # configured SID last arrived, and any enable bit the
                # spacecraft actually reported. Lets the table show PERIODIC /
                # SILENT from evidence instead of assuming everything is on.
                async with self._param_cache_lock:
                    sid_seen = dict(self._sid_last_seen)
                    sid_reported = dict(self._sid_enabled_reported)
                data["hk_sid_age_s"] = {
                    str(sid): round(max(0.0, now - ts), 1)
                    for sid, ts in sid_seen.items()
                }
                if sid_reported:
                    data["hk_enabled"] = {str(k): v for k, v in sid_reported.items()}
                if self._hk_enable_reset:
                    # "Why did my SIDs go quiet?" — the HK Manager renders
                    # this as a banner next to the enable column.
                    data["hk_enable_reset"] = self._hk_enable_reset

                # Include S15 storage status if available
                async with self._param_cache_lock:
                    if self._storage_status:
                        data["storage_status"] = self._storage_status

                # Simulator-reported health + spacecraft mode. Everything here
                # comes from the sim state endpoint, not from telemetry, and
                # carries `source: sim_state` so displays can say so.
                if self._sim_health:
                    data["sim_health"] = self._sim_health
                    if self._sim_health.get("sc_mode") is not None:
                        data["sc_mode"] = self._sim_health["sc_mode"]
                # Sim-clock discontinuity (defect #79). Non-zero jump_count
                # means the ground clock was re-anchored across a break in sim
                # time and anything cached at the old epoch was dropped.
                # OMITTED in real mode (disposition M10): wall-clock UTC
                # cannot jump, so publishing the field there would present a
                # training concept as a mission one. Modern pages never read
                # it; classic tolerates its absence (renders nothing).
                if self._time_source == "sim":
                    data["sim_time_jump"] = {
                        "count": self._jump_count,
                        "last_utc": self._last_jump_utc,
                        "last_delta_s": round(self._last_jump_delta_s, 2),
                    }
                # Latest on-board schedule, so the S11 display survives a tab
                # switch without re-issuing TC(11,17).
                if self._schedule_report:
                    data["schedule"] = self._schedule_report

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
                    self._maybe_prune_archive()
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
            "ground_utc": self.sim_now().isoformat(),
            "speed": self._sim_speed,
            "offset_s": self._ground_time_offset,
        })

    #: Page-level LIVE/STALE thresholds, in seconds since the newest TM frame.
    #: Deliberately the same numbers the per-plot policy uses (defect #65:
    #: TM_FRESH_S / TM_STALE_S in index.html) so the header and the charts
    #: cannot disagree about whether the console is live.
    SA_FRESH_S = 5.0
    SA_DEGRADED_S = 15.0

    def _build_sa_block(self, data: dict, age_s: Optional[float],
                        stale: bool) -> dict:
        """Situational-awareness header block (brief 08, operator gap 7).

        Everything an operator should be able to read without changing tab:
        link freshness, the data-rate tier the spacecraft is ACTUALLY using
        versus the one the ground commanded, how full the on-board stores
        are, how many alarms are unacknowledged, and the sim speed.
        AOS/LOS with the station name is added client-side from
        /api/displays/contact-schedule, which owns the pass list.
        """
        ttc = data.get("ttc") or {}
        obdh = data.get("obdh") or {}

        if age_s is None:
            link = "none"
        elif stale:
            link = "stale"
        elif age_s < self.SA_FRESH_S:
            link = "live"
        elif age_s < self.SA_DEGRADED_S:
            link = "degraded"
        else:
            link = "stale"

        # On-board store fill: prefer the S15.14 store report (authoritative,
        # per store); fall back to the OBDH buffer-fill parameters.
        stores = list(self._storage_status or [])
        fill_pct = None
        fill_source = ""
        if stores:
            fill_pct = max((float(s.get("fill_pct", 0.0)) for s in stores),
                           default=None)
            fill_source = "S15.14"
        else:
            buf = [obdh.get(k) for k in
                   ("hktm_buf_fill", "event_buf_fill", "alarm_buf_fill")]
            vals = [float(v) for v in buf if isinstance(v, (int, float))]
            if vals:
                fill_pct = max(vals)
                fill_source = "HK SID 4 buffers"

        return {
            "link": link,
            "last_frame_age_s": age_s,
            # M11: real mode publishes exactly 1.0 (see _published_speed).
            "speed": self._published_speed(),
            "unacked_alarms": self.unacknowledged_alarm_count(),
            "tm_tier": ttc.get("tm_tier"),
            "tm_tier_name": ttc.get("tm_tier_name"),
            "tm_tier_cmd": ttc.get("tm_tier_cmd"),
            "tm_tier_cmd_name": ttc.get("tm_tier_cmd_name"),
            "tm_tier_demoted": ttc.get("tm_tier_demoted", False),
            "tm_tier_demotion_reason": ttc.get("tm_tier_demotion_reason", ""),
            "store_fill_pct": (round(fill_pct, 1)
                               if fill_pct is not None else None),
            "store_fill_source": fill_source,
            "stores": [
                {"id": s.get("id"), "name": s.get("name"),
                 "fill_pct": s.get("fill_pct"), "overflow": s.get("overflow")}
                for s in stores
            ],
            "in_contact": data.get("in_contact"),
            "ground_station_name": data.get("ground_station_name", ""),
            "in_eclipse": data.get("in_eclipse"),
        }

    def _maybe_prune_archive(self) -> None:
        """Hourly `prune()` (D-5) — bounds the file on a console left running.

        Both the interval and the cutoff are in SIM seconds: the rows being
        pruned carry sim-time epochs, so an age computed from the wall clock
        would either delete the whole archive on the first pass or never
        delete anything, depending on where the sim epoch sits.
        """
        now = self.sim_now_ts()
        if abs(now - self._archive_last_prune) < self._archive_prune_interval_s:
            return
        self._archive_last_prune = now
        try:
            self._archive.prune(now_epoch=now)
        except Exception as e:  # noqa: BLE001
            logger.debug("Archive prune failed: %s", e)

    def _archive_state_snapshot(self, data: dict) -> None:
        """Extract key parameters from a state poll and store them.

        The row is stamped with SIM TIME (spec Part D.4) and carries the
        on-board CUC of the newest packet behind it, so an archived value can
        be lined up against the spacecraft's own clock rather than against the
        host that happened to run the console.
        """
        params: dict[str, float] = {}
        for subsys in ("eps", "aocs", "tcs", "ttc", "payload", "obdh", "prop"):
            sub = data.get(subsys)
            if not isinstance(sub, dict):
                continue
            for key, val in sub.items():
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    params[f"{subsys}.{key}"] = float(val)
        # Ground-derived context series (WP-4). The trend page shades eclipse
        # and contact arcs; those are computed by the ground segment from the
        # TLE, are in no HK SID and would otherwise have no history at all,
        # leaving a plot of battery SoC with no way to see WHY it fell.
        for key, src in (("orbit.eclipse", "in_eclipse"),
                         ("orbit.contact", "in_contact")):
            val = data.get(src)
            if isinstance(val, bool):
                params[key] = 1.0 if val else 0.0
        if isinstance(data.get("alt_km"), (int, float)):
            params["orbit.alt_km"] = float(data["alt_km"])
        # Ground-derived v2 projections (G1): archive only the VALID values —
        # an invalid projection has no number to store, and storing a
        # placeholder would fabricate history.
        for name, ent in (data.get("derived") or {}).items():
            if isinstance(ent, dict) and ent.get("valid") \
                    and isinstance(ent.get("value"), (int, float)):
                params[name] = float(ent["value"])
        if params:
            try:
                self._archive.store_parameters(
                    params,
                    utc_epoch=self.sim_now_ts(),
                    obc_cuc=self._last_obc_cuc,
                )
            except Exception as e:
                logger.debug("Archive store error: %s", e)

    # ── TM archive query endpoints ────────────────────────────────

    def _archive_meta(self) -> dict:
        """Provenance for every archive response.

        Archive rows are on SIM TIME (schema v2). A pre-migration file holds
        wall-clock epochs, which are not comparable, so every query answer
        carries the file's declared time base rather than leaving a client to
        assume one (spec Part D.4).
        """
        try:
            meta = self._archive.meta()
            if isinstance(meta, dict):
                meta.setdefault("available", True)
                meta.setdefault("status", "ok")
                meta.setdefault("message", meta.get("note", ""))
                return meta
        except Exception:  # noqa: BLE001 — a MagicMock archive in tests
            pass
        return {"schema_version": None, "time_base": "unknown",
                "sim_time": False, "note": "", "status": "unknown",
                "available": False,
                "message": "Telemetry archive state is unknown."}

    def _archive_available(self) -> bool:
        """False when the archive is corrupt/unopenable (degraded mode)."""
        try:
            return bool(self._archive.available)
        except Exception:  # noqa: BLE001 — a MagicMock archive in tests
            return True

    def _archive_query(self, what: str, fn, *args, **kwargs):
        """Call one archive accessor and NEVER let a driver error escape.

        `database disk image is malformed` is a SQLite string. It must not
        appear in an operator panel — the panel gets an explicit
        "archive unavailable" state and the driver text goes to the log.
        Returns ``(rows, degraded)``.
        """
        if not self._archive_available():
            return [], True
        try:
            return fn(*args, **kwargs), False
        except sqlite3.Error as e:
            logger.error("TM archive %s failed, degrading to "
                         "read-unavailable: %s", what, e)
            return [], True
        except Exception as e:  # noqa: BLE001
            logger.error("TM archive %s failed: %s", what, e)
            return [], True

    async def _handle_archive_params(self, request):
        """GET /api/archive/parameters?name=eps.bat_soc&start=<epoch>&end=<epoch>&limit=3600"""
        name = request.query.get("name", "")
        if not name:
            return web.json_response({"error": "name parameter required"}, status=400)
        start = float(request.query["start"]) if "start" in request.query else None
        end = float(request.query["end"]) if "end" in request.query else None
        limit = int(request.query.get("limit", 3600))
        data, degraded = self._archive_query(
            "query_parameters", self._archive.query_parameters,
            name, start, end, limit)
        return web.json_response({"param_name": name, "data": data,
                                  "archive_unavailable": degraded,
                                  "archive": self._archive_meta()})

    async def _handle_archive_param_names(self, request):
        """GET /api/archive/param-names — what the TRENDS tab can plot.

        The archive is keyed by ``<subsystem>.<ui_key>``, which is the frontend
        naming (`_UI_KEY_MAP`), not the raw `parameters.yaml` name, so the trend
        page must ask the archive rather than the parameter catalogue. Each
        name carries its ground limit band where one exists, so the limit-band
        overlay costs no second round trip.
        """
        names, degraded = self._archive_query(
            "parameter_names", self._archive.parameter_names)
        band_by_name: dict[str, dict] = {}
        for pid, band in self._tm_processor.limit_bands().items():
            meta = self._param_meta.get(pid)
            if not meta or not band:
                continue
            sub = meta.get("subsystem", "")
            short = meta.get("short_key", "")
            keys = {meta.get("name", ""), f"{sub}.{short}",
                    f"{sub}.{_UI_KEY_MAP.get(short, short)}"}
            for key in keys:
                if key:
                    band_by_name[key] = {**band,
                                         "param_id": f"0x{pid:04X}",
                                         "units": meta.get("units", "")}
        return web.json_response({
            "names": names,
            "bands": {n: band_by_name[n] for n in names if n in band_by_name},
            "archive_unavailable": degraded,
            "archive": self._archive_meta(),
        })

    async def _handle_archive_events(self, request):
        """GET /api/archive/events?start=<epoch>&end=<epoch>&severity=0&limit=500"""
        start = float(request.query["start"]) if "start" in request.query else None
        end = float(request.query["end"]) if "end" in request.query else None
        sev = int(request.query.get("severity", 0))
        limit = int(request.query.get("limit", 500))
        data, degraded = self._archive_query(
            "query_events", self._archive.query_events, start, end, sev, limit)
        return web.json_response({"events": data,
                                  "archive_unavailable": degraded,
                                  "archive": self._archive_meta()})

    async def _handle_archive_alarms(self, request):
        """GET /api/archive/alarms?start=<epoch>&end=<epoch>&limit=500"""
        start = float(request.query["start"]) if "start" in request.query else None
        end = float(request.query["end"]) if "end" in request.query else None
        limit = int(request.query.get("limit", 500))
        data, degraded = self._archive_query(
            "query_alarms", self._archive.query_alarms, start, end, limit)
        return web.json_response({"alarms": data,
                                  "archive_unavailable": degraded,
                                  "archive": self._archive_meta()})

    async def _handle_archive_playback(self, request):
        """GET /api/archive/playback?subsystem=eps&start=<epoch>&end=<epoch>"""
        subsystem = request.query.get("subsystem", "")
        if not subsystem:
            return web.json_response({"error": "subsystem parameter required"}, status=400)
        start = float(request.query["start"]) if "start" in request.query else None
        end = float(request.query["end"]) if "end" in request.query else None
        data, degraded = self._archive_query(
            "get_playback_data", self._archive.get_playback_data,
            subsystem, start, end, now_epoch=self.sim_now_ts())
        return web.json_response({"subsystem": subsystem, "data": data,
                                  "archive_unavailable": degraded,
                                  "archive": self._archive_meta()})

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
            # Defect #46: S11.4 payloads built as exec_time+svc+st+data must
            # carry a complete CCSDS/PUS TC packet — re-embed if needed.
            data = self._embed_s11_packet(service, subtype, data)
            pkt = self._tc_manager.build_command(service, subtype, data)
            seq = self._tc_manager._seq_count

            # Track in verification log, stamped with SIM TIME (Part D.5).
            entry = {
                "seq": seq,
                "name": name,
                "service": service,
                "subtype": subtype,
                "state": "SENT",
                "timestamp": self.sim_now_ts(),
                "error_code": 0,
                "position": position,
            }
            # An S11.4 built by hand in the console carries its own on-board
            # time tag in the first four octets; record what it was scheduled
            # for so the command history can show ground time and on-board
            # time side by side.
            if service == 11 and subtype == 4 and len(data) >= 4:
                entry["sched_cuc"] = float(struct.unpack(">I", data[:4])[0])
            self._record_command(entry, data_hex=clean_hex)
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

    # Fields every verification-log row carries, so the UI can render the
    # failure columns without per-row existence checks (defect #87).
    _VERIF_ROW_DEFAULTS = {"error_code": 0, "error_text": "", "reason": ""}

    def _record_command(self, entry: dict, data_hex: str = "") -> None:
        """Log one outgoing TC to the session log *and* the archive.

        `TMArchive.store_command` had zero callers, so `tc_log` was a
        permanently empty schema and the in-memory `_verification_log`
        (200 rows, lost on restart) was the only command history that
        existed. The post-exercise "what did this team send, when, and what
        came back" debrief artefact needs the persistent one.

        Two times are recorded, and they are not the same clock (spec Part
        D.5):

        * ``timestamp`` — SIM TIME the ground uplinked the command. Defaulted
          here so no caller can leave a wall-clock stamp behind.
        * ``sched_cuc`` — the ON-BOARD CUC the command is time-tagged for,
          where one exists (an S11.4 insert and the TC embedded in it). If the
          OBC clock is off, this fires at a different sim time than intended,
          and having both columns is what lets a debrief show that.
        """
        entry.setdefault("timestamp", self.sim_now_ts())
        entry.setdefault("time_base", "sim")
        self._verification_log.appendleft(entry)
        sched_cuc = entry.get("sched_cuc")
        try:
            self._archive.store_command(
                seq=int(entry.get("seq", 0)),
                name=str(entry.get("name", "")),
                service=int(entry.get("service", 0)),
                subtype=int(entry.get("subtype", 0)),
                data_hex=str(data_hex or ""),
                position=str(entry.get("position", "")),
                state=str(entry.get("state", "SENT")),
                utc_epoch=float(entry["timestamp"]),
                sched_cuc=None if sched_cuc is None else float(sched_cuc),
            )
        except Exception as e:  # noqa: BLE001 — archiving must never block a TC
            logger.debug("store_command failed: %s", e)
        # Console/mission log (G8): every TC send is a decision-record line.
        # The S1 verdict is appended by _process_tm when it arrives.
        self._console_log(
            "tc",
            f"TC sent: {entry.get('name', '')} "
            f"(S{entry.get('service', 0)}.{entry.get('subtype', 0)}, "
            f"seq {entry.get('seq', 0)})",
            position=str(entry.get("position", "")),
            ref=f"seq:{entry.get('seq', 0)}")

    def _archive_command_state(self, seq: int, state: str) -> None:
        """Transition the archived command row on an S1 verification report."""
        try:
            self._archive.update_command_state(int(seq), str(state))
        except Exception as e:  # noqa: BLE001
            logger.debug("update_command_state failed: %s", e)

    async def _handle_verification_log(self, request):
        log = [{**self._VERIF_ROW_DEFAULTS, **e}
               for e in self._verification_log]
        return web.json_response({"log": log})

    async def _handle_archive_commands(self, request):
        """GET /api/archive/commands?start=&end=&limit=500 — command history.

        Survives an MCS restart, unlike `/api/verification-log`, which is a
        200-entry in-memory deque. This is the debrief artefact.

        Each row carries two clocks: ``utc_epoch`` (SIM TIME the ground sent
        it) and ``sched_cuc`` (the ON-BOARD CUC it was time-tagged for, where
        one exists). Reading them together is how a student sees an S11
        schedule fire at the wrong sim time because the OBC clock was wrong.
        """
        start = float(request.query["start"]) if "start" in request.query else None
        end = float(request.query["end"]) if "end" in request.query else None
        limit = int(request.query.get("limit", 500))
        rows, degraded = self._archive_query(
            "query_commands", self._archive.query_commands, start, end, limit)
        return web.json_response({"commands": rows,
                                  "archive_unavailable": degraded,
                                  "archive": self._archive_meta()})

    # ── UI switch (design_2026-08 plan §1.2) ──────────────────────────
    # Both UIs hit the same API; these handlers only decide WHICH page is
    # served. Index responses carry Cache-Control: no-cache so flipping the
    # mode (or the final classic->modern default flip) is never masked by a
    # cached index (risk R9).

    def _resolve_ui(self, request) -> str:
        """Per-request UI mode: ?ui= wins over the resolved server mode."""
        q = (request.query.get("ui") or "").strip().lower()
        if q in ("classic", "modern"):
            return q
        # getattr: house tests build servers via __new__ without __init__.
        return getattr(self, "_ui_mode", "classic")

    def _index_file_for(self, mode: str) -> Path:
        static_dir = Path(__file__).parent / "static"
        if mode == "modern":
            return static_dir / "modern" / "index.html"
        return static_dir / "index.html"

    def _serve_index(self, mode: str):
        index_file = self._index_file_for(mode)
        if index_file.exists():
            return web.FileResponse(
                index_file, headers={"Cache-Control": "no-cache"})
        if mode == "modern":
            # Modern assets not shipped (or not yet built) — say so rather
            # than serving a blank page; /classic is the escape hatch (R5).
            return web.Response(
                text="<h1>SMO MCS — modern UI assets not installed</h1>"
                     "<p>The classic console is at <a href=\"/classic\">"
                     "/classic</a>.</p>",
                content_type="text/html",
                headers={"Cache-Control": "no-cache"},
            )
        return web.Response(
            text="<h1>SMO MCS — static/index.html not found</h1>",
            content_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )

    async def _handle_index(self, request):
        return self._serve_index(self._resolve_ui(request))

    async def _handle_index_classic(self, request):
        """GET /classic — always the classic page (live A/B comparison)."""
        return self._serve_index("classic")

    async def _handle_index_modern(self, request):
        """GET /modern — always the modern page (live A/B comparison)."""
        return self._serve_index("modern")

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

    async def _handle_failure_codes(self, request):
        """Serve the S1 failure-code vocabulary (defect #88).

        There is exactly one copy of the code -> meaning map, in
        ``smo_common.protocol.pus_services``, and the spacecraft's dispatcher
        emits from the same module. Every ground display — the verification
        log, the event log, the FDIR journal and the OBDH parameter pane —
        reads it from here rather than carrying its own table, so a new code
        cannot mean one thing on board and another in the browser.

        ``memory_codes`` is the S6-specific reading of the low codes; it only
        applies to a failure raised by a memory request.
        """
        return web.json_response({
            "codes": {f"0x{code:04X}": text
                      for code, text in sorted(FAILURE_CODES.items())},
            "memory_codes": {f"0x{code:04X}": text
                             for code, text in sorted(MEMORY_FAILURE_CODES.items())},
            "command_rejected_event_id": f"0x{COMMAND_REJECTED_EVENT_ID:04X}",
        })

    def _report_command_log(self) -> dict[int, dict]:
        """`{seq: {name, service, subtype, data_hex}}` for report generation.

        Field report 2026-08-20: the delayed-TM "sent commands" section showed
        only a sequence counter and an execution state. It could not show more:
        its only source is PUS S1 verification TELEMETRY, and an S1 report
        identifies a telecommand by request-id -- by protocol it never carries
        the command's service, subtype or arguments.

        The identity exists on the ground. `tc_log` records name, service,
        subtype and the argument octets at the moment the MCS sends each TC,
        indexed by the same CCSDS sequence count the S1 report echoes back. So
        this is a join, not a decode. Keyed by `seq`, which wraps at 14 bits --
        acceptable here because a report covers one pass, and a wrap inside a
        single pass would need 16384 commands.
        """
        archive = getattr(self, "_tm_archive", None)
        if archive is None:
            return {}
        try:
            rows = archive.query_commands(limit=20000)
        except Exception:  # noqa: BLE001 - a report without names still helps
            logger.exception("tc_log unavailable for report generation")
            return {}
        out: dict[int, dict] = {}
        for r in rows:
            seq = r.get("seq")
            if seq is None:
                continue
            out[int(seq)] = {"name": r.get("name") or "",
                             "service": r.get("service"),
                             "subtype": r.get("subtype"),
                             "data_hex": r.get("data_hex") or ""}
        return out

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
            state_tables = build_state_tables(
                pdata.get("parameters", []) or [], self.config_dir)
            for p in pdata.get("parameters", []) or []:
                try:
                    pid = int(p["id"]) if isinstance(p["id"], int) else int(str(p["id"]), 0)
                except Exception:
                    continue
                cad = self._param_cadence.get(pid)
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
                    # The cadence join: which HK structures carry this
                    # parameter and how often the fastest of them is sent.
                    # `None` means aperiodic — no structure carries it, so its
                    # only source is an on-demand TC(20,3) fetch and it must
                    # not be judged against any HK interval.
                    "hk_sids": list(cad["sids"]) if cad else [],
                    "hk_interval_s": cad["interval_s"] if cad else None,
                    # Engineering values for discrete parameters (field report
                    # 2026-08-20: "only the raw values seem to display").
                    # {code: name}, or absent when the parameter is not
                    # enumerated -- see smo_common.telemetry.eng_values, which
                    # derives these from configuration and never invents one.
                    "states": {str(k): v for k, v in
                               (state_tables.get(pid) or {}).items()} or None,
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
            # Derive the expected data-field length so the command builder can
            # show "expected N data bytes" instead of guessing (defect #67).
            if cmd_dict.get("name") in self._tc_names_without_fields:
                # The YAML has no `fields` key at all — report it as undeclared
                # rather than letting the schema's default [] imply zero.
                probe = {k: v for k, v in cmd_dict.items() if k != "fields"}
                cmd_dict.update(expected_data_bytes(probe))
            else:
                annotate_command(cmd_dict)
            commands.append(cmd_dict)
        # Phase E WP-5: quick commands ride along, filtered to the commands
        # this position may send, so the panel renders only what is allowed.
        allowed_names = {c["name"] for c in commands if c.get("allowed")}
        quick = [q for q in self._tc_quick_commands
                 if q.get("command") in allowed_names]
        return web.json_response({"commands": commands,
                                  "quick_commands": quick})

    async def _handle_hk_structures(self, request):
        """Return the authoritative HK SID table.

        The MCS used to drive its HK Manager from a hardcoded `hkSids` array in
        index.html whose names, intervals and parameter counts were all wrong
        (defect #68). The definitions have always been loaded here, so serve
        them and let the UI render whatever the configuration actually says.

        `observed` reports the real behaviour of the spacecraft: when each SID
        was last seen and whether it is currently arriving at its configured
        rate. That is honest telemetry-derived state rather than the assumption
        the old UI made (every SID enabled, forever).

        The observed-cadence measurement is in **sim seconds** (spec Part
        D.2): `interval_s` comes from hk_structures.yaml and is a sim-time
        cadence, so `age` has to be too. Against the wall clock a paused
        simulator turned every SID SILENT within a couple of cycles, and at
        x60 a genuinely dead SID stayed PERIODIC for an hour of sim time.
        """
        now = self.sim_now_ts()
        async with self._param_cache_lock:
            last_seen = dict(self._sid_last_seen)
            reported = dict(self._sid_enabled_reported)
        out = []
        for sid in sorted(self._hk_sid_meta):
            meta = dict(self._hk_sid_meta[sid])
            seen_ts = last_seen.get(sid)
            age = None if seen_ts is None else round(max(0.0, now - seen_ts), 1)
            interval = float(meta.get("interval_s") or 0.0)
            # Two missed cycles (plus a second of slack) before calling it silent.
            silent_after = interval * 2.5 + 1.0 if interval > 0 else 30.0
            if age is None:
                observed = "never_seen"
            elif age <= silent_after:
                observed = "periodic"
            else:
                observed = "silent"
            meta["last_seen_age_s"] = age
            meta["observed"] = observed
            # Only report an enable flag if something actually told us one.
            # `None` means "unknown — infer from `observed`", which is the
            # truthful answer for this build.
            meta["enabled"] = reported.get(sid)
            out.append(meta)
        return web.json_response({"structures": out,
                                  "age_time_base": "sim",
                                  # Why the enable set last reset itself, if it
                                  # ever has (see _ingest_hk_enable_reset).
                                  "enable_reset": self._hk_enable_reset,
                                  "spacecraft_apid": self._spacecraft_apid,
                                  "bootloader_apid": self._bootloader_apid})

    # ── Procedure runner helpers ────────────────────────────────────

    async def _proc_send_command(
        self, service: int, subtype: int, data_hex: str = ""
    ) -> dict:
        """Send a PUS command on behalf of the procedure runner."""
        clean_hex = data_hex.replace(" ", "").strip() if data_hex else ""
        data = bytes.fromhex(clean_hex) if clean_hex else b""
        data = self._embed_s11_packet(service, subtype, data)
        pkt = self._tc_manager.build_command(service, subtype, data)
        seq = self._tc_manager._seq_count

        entry = {
            "seq": seq,
            "name": f"PROC:S{service}.{subtype}",
            "service": service,
            "subtype": subtype,
            "state": "SENT",
            "timestamp": self.sim_now_ts(),
            "error_code": 0,
            "position": "procedure_runner",
        }
        if service == 11 and subtype == 4 and len(data) >= 4:
            entry["sched_cuc"] = float(struct.unpack(">I", data[:4])[0])
        self._record_command(entry, data_hex=clean_hex)
        self._tc_manager.track_verification(seq, entry["name"])

        if self._tc_writer is None or self._tc_writer.is_closing():
            return {"status": "error", "message": "No TC connection"}

        framed = frame_packet(pkt)
        async with self._tc_send_lock:
            self._tc_writer.write(framed)
            await self._tc_writer.drain()
        return {"status": "sent", "seq": seq}

    def _proc_get_telemetry(self, param_path: str) -> Any:
        """Read a telemetry value by dot-path for a procedure step.

        TWO vocabularies, because there are two (2026-08d PLAN-9):

        * the engine's STATE SUMMARY, whose keys carry display unit suffixes
          (``aocs.att_error_deg``, ``payload.fpa_temp_C``,
          ``ttc.data_rate_bps``) and which omits fields nobody drew yet;
        * ``telemetry/parameters.yaml``, the mission's own parameter names
          (``aocs.att_error``, ``payload.fpa_temp``, ``ttc.tm_data_rate``,
          ``aocs.fine_ready``), which is what procedure cards, activity
          templates and every operator document are written in.

        Only the first was resolvable, so a ``tlm_check`` written in the
        mission's vocabulary either failed validation ("not a telemetry
        parameter the procedure runner can read") or, before that check
        existed, timed out after 30 s mid-pass. The shipped ``data_dump``
        activity's ``wait_for ttc.tm_data_rate == 128000`` is exactly that
        case: a real parameter (0x0506) the runner could not read, because the
        summary calls it ``data_rate_bps``.

        The state summary still WINS on a name collision, so nothing that
        resolved before resolves differently now; the catalogue is a fallback
        served from the MCS's own parameter cache.
        """
        state = self._latest_state or {}
        # Try direct subsystem.param lookup in latest state dict
        parts = param_path.split(".", 1)
        if len(parts) == 2:
            subsystem, param = parts
            sub_data = state.get(subsystem, {})
            if isinstance(sub_data, dict) and param in sub_data:
                return sub_data.get(param)
        if param_path in state:
            return state.get(param_path)
        # Fallback: the mission's own parameter catalogue, via the id cache.
        return self._cached_value(param_path)

    # ── S11 time-tagged scheduling helpers (defects #44/#46) ─────────

    def _embed_s11_packet(self, service: int, subtype: int,
                          data: bytes) -> bytes:
        """Ensure an S11.4 (insert time-tagged TC) payload embeds a FULL TC.

        The simulator stores ``data[4:]`` verbatim and, at fire time, runs
        ``decommutate_packet`` on it (engine.py `_dispatch_tc`). A bare
        ``svc(1)+st(1)+data`` blob therefore died as "malformed packet" at
        execution time (defect #46). If the embedded bytes do not already
        parse as a CCSDS/PUS packet, re-wrap them via ``tc_manager`` into a
        complete framed TC (with its own APID/seq/CRC) so the on-board
        scheduler can actually execute it. Non-S11.4 data passes through
        untouched.
        """
        if service != 11 or subtype != 4 or len(data) < 6:
            return data
        embedded = data[4:]
        try:
            parsed = decommutate_packet(embedded)
        except Exception:
            parsed = None
        if parsed is not None and parsed.secondary is not None:
            return data  # already a complete embedded TC packet
        inner_svc = embedded[0]
        inner_st = embedded[1]
        inner_data = embedded[2:]
        inner_pkt = self._tc_manager.build_command(inner_svc, inner_st,
                                                   inner_data)
        inner_seq = self._tc_manager._seq_count
        # Track the embedded TC too, so its S1 reports at fire time correlate.
        self._tc_manager.track_verification(
            inner_seq, f"S11-DEFERRED:S{inner_svc}.{inner_st}")
        self._record_command({
            "seq": inner_seq,
            "name": f"S11-DEFERRED:S{inner_svc}.{inner_st}",
            "service": inner_svc,
            "subtype": inner_st,
            "state": "SCHEDULED",
            "timestamp": self.sim_now_ts(),
            # The on-board CUC this deferred TC will fire at, straight off the
            # S11.4 time tag in front of it.
            "sched_cuc": float(struct.unpack(">I", data[:4])[0]),
            "error_code": 0,
            "position": "s11_scheduler",
        }, data_hex=inner_data.hex())
        return data[:4] + inner_pkt

    def _utc_to_obc_cuc_seconds(self, dt: datetime) -> float:
        """Convert a SIM UTC datetime to on-board CUC seconds, full precision.

        This is the ground's time correlation, and it is the ONLY correct way
        to turn a sim-time activity start into an on-board time tag: anchor on
        the latest OBC clock telemetry (0x0309, plus the fine field if it is
        downlinked) and add the sim-time delta, so the tag lands on the
        spacecraft's clock *including* its error. If the OBC is 40 s fast the
        schedule fires 40 s early in sim time — that is the correct, teachable
        behaviour, and neither the MCS nor the planner may silently correct it.

        Fallback (no OBC telemetry yet): the shared mission epoch, i.e. assume
        a perfect clock. That is the best the ground can do before it has
        heard from the spacecraft.
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        obc_cuc = self.obc_cuc_now()
        if obc_cuc is not None and obc_cuc > 0:
            sim_now = self.sim_now()
            if sim_now.tzinfo is None:
                sim_now = sim_now.replace(tzinfo=timezone.utc)
            return float(obc_cuc) + (dt - sim_now).total_seconds()
        return datetime_to_cuc_seconds(dt)

    def _utc_to_obc_cuc(self, dt: datetime) -> int:
        """Whole-second (coarse) form of :meth:`_utc_to_obc_cuc_seconds`.

        The S11.4 time tag on the wire is still a bare ``uint32`` of coarse
        seconds, so this is what goes into the packet.
        """
        return int(math.floor(self._utc_to_obc_cuc_seconds(dt)))

    async def _handle_s11_schedule_upload(self, request):
        """POST /api/schedule/upload-s11 — convert a planner activity into
        on-board S11 time-tagged commands.

        Body: {name, start_time (ISO 8601), steps: [...], procedure_ref}.
        Each command step becomes one TC(11,4) whose payload is
        exec_time_cuc(4) + a complete embedded TC packet; ``wait_s`` steps
        advance the execution offset; ``wait_for``/telemetry checks cannot
        run on board and are reported back as skipped. This is the
        time-correct path for planner schedules (defect #44) — the
        immediate ProcedureRunner path remains available via
        /api/procedure/load.
        """
        try:
            body = await request.json()
            name = body.get("name", "")
            start_time = body.get("start_time")
            steps = body.get("steps") or []
            if not start_time:
                return web.json_response(
                    {"error": "start_time is required"}, status=400)
            if not steps:
                return web.json_response(
                    {"error": "steps are required"}, status=400)
            try:
                start_dt = datetime.fromisoformat(
                    str(start_time).replace("Z", "+00:00"))
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                return web.json_response(
                    {"error": f"Invalid start_time: {start_time}"}, status=400)

            if self._tc_writer is None or self._tc_writer.is_closing():
                return web.json_response(
                    {"status": "error", "message": "No TC connection"},
                    status=503)

            offset_s = 0.0
            scheduled: list[dict] = []
            skipped: list[dict] = []
            for step in steps:
                if "wait_s" in step or step.get("type") == "wait":
                    offset_s += float(step.get("wait_s",
                                               step.get("seconds", 0)))
                    continue
                if "wait_for" in step or step.get("type") in ("tlm_check",
                                                              "go_nogo"):
                    skipped.append({
                        "step": ProcedureRunner._build_data_hex(step)
                        if "service" in step else str(step),
                        "reason": "telemetry checks / operator gates cannot "
                                  "run in the on-board S11 schedule",
                    })
                    continue
                if "service" not in step:
                    skipped.append({"step": str(step),
                                    "reason": "unrecognised step type"})
                    continue

                svc = int(step["service"])
                st = int(step["subtype"])
                data_hex = step.get("data_hex", "") or \
                    ProcedureRunner._build_data_hex(step)
                clean = data_hex.replace(" ", "").strip()
                inner_data = bytes.fromhex(clean) if clean else b""
                exec_dt = start_dt + timedelta(seconds=offset_s)
                exec_cuc = self._utc_to_obc_cuc(exec_dt)
                s11_data = struct.pack(">I", exec_cuc) + \
                    self._tc_manager.build_command(svc, st, inner_data)
                inner_seq = self._tc_manager._seq_count
                self._tc_manager.track_verification(
                    inner_seq, f"S11-DEFERRED:S{svc}.{st}")
                outer_pkt = self._tc_manager.build_command(11, 4, s11_data)
                outer_seq = self._tc_manager._seq_count

                desc = step.get("description", f"S{svc}.{st}")
                self._record_command({
                    "seq": outer_seq,
                    "name": f"S11.4[{name}:{desc}]",
                    "service": 11,
                    "subtype": 4,
                    "state": "SENT",
                    "timestamp": self.sim_now_ts(),
                    "sched_cuc": float(exec_cuc),
                    "error_code": 0,
                    "position": "s11_scheduler",
                }, data_hex=s11_data.hex())
                self._tc_manager.track_verification(
                    outer_seq, f"S11.4[{name}:{desc}]")

                framed = frame_packet(outer_pkt)
                async with self._tc_send_lock:
                    self._tc_writer.write(framed)
                    await self._tc_writer.drain()

                scheduled.append({
                    "description": desc,
                    "service": svc,
                    "subtype": st,
                    "exec_time_utc": exec_dt.isoformat(),
                    "exec_time_cuc": exec_cuc,
                    "s11_seq": outer_seq,
                    "embedded_seq": inner_seq,
                })
                # Keep strict on-board ordering between successive commands.
                offset_s += 1.0

            return web.json_response({
                "status": "scheduled",
                "name": name,
                "start_time": start_time,
                "count": len(scheduled),
                "scheduled": scheduled,
                "skipped": skipped,
            })
        except Exception as e:
            logger.warning("S11 schedule upload error: %s", e)
            return web.json_response(
                {"status": "error", "message": str(e)}, status=400)

    # ── Procedure API handlers ───────────────────────────────────────

    # ── Canonical procedure resolution (defect #83) ──────────────────

    def _tlm_vocabulary(self) -> set[str] | None:
        """Dot-paths ``_proc_get_telemetry`` can actually resolve.

        This is the runner's REAL vocabulary — the flattened live state
        summary — not ``telemetry/parameters.yaml``. The two differ (the
        catalogue lists ``ttc.pa_on``, which the state summary does not carry),
        and a ``tlm_check`` on a parameter the runner cannot read fails as a
        30 s timeout mid-procedure. Returns ``None`` when no telemetry has been
        received yet, in which case nothing can be validated.
        """
        state = self._latest_state or {}
        if not state:
            return None
        paths: set[str] = set()
        for key, value in state.items():
            if isinstance(value, dict):
                paths.update(f"{key}.{sub}" for sub in value)
                paths.add(key)
            else:
                paths.add(key)
        # 2026-08d PLAN-9: plus the mission's own parameter names, which
        # `_proc_get_telemetry` now resolves from the parameter cache. See the
        # note there: procedure cards and planner activity templates are
        # written in `telemetry/parameters.yaml` names, not in the state
        # summary's display names, and rejecting them made a correct step
        # unrunnable.
        paths.update(str(m.get("name", "")) for m in self._param_meta.values()
                     if m.get("name"))
        return paths

    def _custom_procedure_path(self, rel_path: str) -> Path | None:
        """Resolve ``custom/foo.yaml`` under the procedures tree, safely."""
        proc_dir = (self.config_dir / "procedures").resolve()
        target = (proc_dir / rel_path).resolve()
        if not str(target).startswith(str(proc_dir)):
            return None
        if not target.exists() or not target.is_file():
            return None
        return target

    def _load_saved_procedure(self, rel_path: str) -> dict | None:
        """Read a builder-saved (or hand-written) procedure YAML."""
        import yaml
        target = self._custom_procedure_path(rel_path)
        if target is None or target.suffix not in (".yaml", ".yml"):
            return None
        try:
            data = yaml.safe_load(target.read_text()) or {}
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return {
            "name": data.get("name", target.stem),
            "description": data.get("description", ""),
            "steps": to_canonical(data.get("steps", [])),
            "procedure_ref": data.get("procedure_ref", ""),
            "source": f"saved:{rel_path}",
        }

    def _activity_type_procedure(self, name: str) -> dict | None:
        for at in self._activity_types:
            if at.get("name") == name:
                return {
                    "name": name,
                    "description": at.get("description", ""),
                    "steps": to_canonical(at.get("command_sequence", [])),
                    "procedure_ref": at.get("procedure_ref", ""),
                    "source": f"activity_type:{name}",
                }
        return None

    def _indexed_procedure(self, procedure_id: str) -> dict | None:
        """Resolve a ``procedure_index.yaml`` id to a RUNNABLE definition.

        The 64 shipped markdown procedures are the operations manual and are
        not rewritten. An entry becomes runnable only by declaring a
        ``runnable:`` link:

            runnable: {kind: activity_type, ref: imaging_pass}
            runnable: {kind: steps_file,    ref: nominal/foo.steps.yaml}
            runnable: {kind: markdown}      # derive from the **TC:** annotations
        """
        entry = next((e for e in self._procedure_index
                      if e.get("id") == procedure_id), None)
        if entry is None:
            return None
        link = entry.get("runnable")
        if not isinstance(link, dict):
            return None
        kind = link.get("kind", "")
        resolved: dict | None = None
        if kind == "activity_type":
            resolved = self._activity_type_procedure(link.get("ref", ""))
        elif kind == "steps_file":
            resolved = self._load_saved_procedure(link.get("ref", ""))
        elif kind == "markdown":
            target = self._custom_procedure_path(entry.get("file", ""))
            if target is not None:
                catalog: dict[str, dict] = {}
                for cmd in (self._tc_catalog or []):
                    cmd_entry = {"service": cmd.service, "subtype": cmd.subtype,
                                 "func_id": cmd.func_id}
                    catalog[cmd.name] = cmd_entry
                    for alias in (cmd.aliases or []):
                        catalog.setdefault(alias, cmd_entry)
                resolved = {
                    "name": entry.get("name", procedure_id),
                    "description": "",
                    "steps": from_markdown(target.read_text(encoding="utf-8"),
                                           catalog),
                    "procedure_ref": procedure_id,
                    "source": f"markdown:{entry.get('file', '')}",
                }
        if resolved is None:
            return None
        resolved["name"] = entry.get("name", resolved["name"])
        resolved["procedure_ref"] = procedure_id
        resolved["document"] = entry.get("file", "")
        return resolved

    async def _handle_proc_load(self, request):
        """Load a procedure for execution.

        Accepts, in priority order (defect #83 — before this, only the first
        was reachable from the UI, so a builder-saved procedure could be
        edited but never run):

          ``steps``        an explicit canonical/legacy step list
          ``path``         a saved procedure, e.g. ``custom/my_proc.yaml``
          ``procedure_id`` a ``procedure_index.yaml`` id with a ``runnable`` link
          ``name``         an activity type from ``activity_types.yaml``
        """
        try:
            body = await request.json()
            name = body.get("name", "")
            steps = body.get("steps")
            procedure_ref = body.get("procedure_ref", "")
            step_by_step = body.get("step_by_step", False)
            start_time = body.get("start_time")
            document = ""

            if steps is None and body.get("path"):
                saved = self._load_saved_procedure(body["path"])
                if saved is None:
                    return web.json_response(
                        {"error": f"Saved procedure '{body['path']}' not found"},
                        status=404)
                steps, name = saved["steps"], name or saved["name"]
                procedure_ref = procedure_ref or saved["procedure_ref"]

            if steps is None and body.get("procedure_id"):
                indexed = self._indexed_procedure(body["procedure_id"])
                if indexed is None:
                    return web.json_response(
                        {"error": f"Procedure '{body['procedure_id']}' has no "
                                  f"runnable definition"}, status=404)
                steps, name = indexed["steps"], name or indexed["name"]
                procedure_ref = procedure_ref or indexed["procedure_ref"]
                document = indexed.get("document", "")

            # If steps not provided, look up by activity type name
            if steps is None:
                activity = self._activity_type_procedure(name)
                if activity is None:
                    return web.json_response(
                        {"error": f"Activity type '{name}' not found"}, status=404
                    )
                steps = activity["steps"]
                procedure_ref = procedure_ref or activity["procedure_ref"]

            problems = validate_steps(steps, self._tlm_vocabulary())
            if problems:
                return web.json_response(
                    {"error": "Procedure is not runnable", "problems": problems},
                    status=400)

            result = self._procedure_runner.load(
                name, steps, procedure_ref=procedure_ref,
                step_by_step=step_by_step, start_time=start_time,
            )
            if not document and procedure_ref:
                entry = next((e for e in self._procedure_index
                              if e.get("id") == procedure_ref), None)
                document = (entry or {}).get("file", "")
            result["document"] = document
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_proc_tlm_vocabulary(self, request):
        """Telemetry dot-paths a ``tlm_check`` step may reference.

        The Procedure Builder uses this to offer a real vocabulary instead of
        free text (audit Q7.1: a typo used to cost a 30 s timeout and a failed
        procedure, with no hint of what the legal names were).
        """
        vocabulary = self._tlm_vocabulary()
        if vocabulary is None:
            return web.json_response(
                {"parameters": [], "source": "none",
                 "note": "No spacecraft telemetry received yet — parameter "
                         "names cannot be validated."})
        return web.json_response(
            {"parameters": sorted(vocabulary), "source": "live_state"})

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
        """The 64 shipped ops procedures, with their runnable link resolved.

        Each entry gains ``runnable`` (bool) and ``runnable_steps`` (count) so
        the Procedures tab can show which manual procedures can be executed
        from the console and which are read-only (defect #83).
        """
        out = []
        for entry in self._procedure_index:
            item = dict(entry)
            resolved = self._indexed_procedure(entry.get("id", ""))
            item["runnable"] = resolved is not None
            item["runnable_steps"] = len(resolved["steps"]) if resolved else 0
            if resolved:
                item["runnable_source"] = resolved.get("source", "")
            out.append(item)
        return web.json_response({"procedures": out,
                                  "schema_version": CANONICAL_SCHEMA_VERSION})

    def _step_owner_positions(self, step: dict) -> list[str]:
        """Which console position owns a procedure step.

        No step in configuration carries an owner, and inventing one would be
        worse than showing none. But a command step DOES carry the thing the
        authority model is keyed on: for S8.1 the func_id, otherwise the
        service. positions.yaml already says which func_ids and services each
        position may command, so the owner is a JOIN against the authority
        that is already enforced when the command is sent -- not a guess.

        A position with `allowed_commands: "all"` (the Flight Director) is
        deliberately excluded: FD may send anything, so counting FD as an owner
        would mark every step as everyone's and highlight nothing.
        """
        if step.get("type") != "command":
            return []
        service = step.get("service")
        subtype = step.get("subtype")
        func_id = step.get("func_id")
        owners: list[str] = []
        for name, pos in (self._positions or {}).items():
            if getattr(pos, "allowed_commands", None) == "all":
                continue
            func_ids = list(getattr(pos, "allowed_func_ids", []) or [])
            services = list(getattr(pos, "allowed_services", []) or [])
            if service == 8 and subtype == 1 and func_id is not None:
                if func_id in func_ids:
                    owners.append(name)
                continue
            # Service-level authority is NOT attribution. Every position may
            # send S17 pings and S3 HK requests, so a service match "owns" a
            # step for six positions at once and highlights nothing useful --
            # measured: CONNECTION_TEST came back owned by all six. Only the
            # func_id grain discriminates, so only it is claimed.
            _ = services
        return owners

    async def _handle_proc_cards(self, request):
        """Procedure cards with per-step ownership resolved for a position.

        Field report 2026-08-20: "could the MAN page display the procedure
        cards per position with the specific steps to be executed by that
        position highlighted somehow (so this changes by position)?"

        Two grades of attribution, and the card is explicit about which it is
        showing, because they are not equally certain:

        * whole-procedure — `required_positions` / `position_roles` from
          procedure_index.yaml. Authored by a human, exact.
        * per-step — derived by joining the step's func_id (or service) against
          the position's commanding authority. Sound, but it answers "who is
          allowed to send this" rather than "who was told to". Steps that are
          not commands (GO/NO-GO calls, telemetry checks) have no owner at all
          and are never highlighted.
        """
        position = (request.query.get("position") or "").strip()
        pos_cfg = (self._positions or {}).get(position)
        sees_all = (position == "flight_director"
                    or getattr(pos_cfg, "allowed_commands", None) == "all")
        out = []
        for entry in self._procedure_index:
            required = list(entry.get("required_positions") or [])
            roles = dict(entry.get("position_roles") or {})
            mine = sees_all or (position in required)
            steps_out = []
            n_mine = 0
            for i, step in enumerate(self._procedure_steps_for(entry)):
                owners = self._step_owner_positions(step)
                step_mine = bool(position) and position in owners
                if step_mine:
                    n_mine += 1
                steps_out.append({
                    # `attribution` says how the owner was established, so the
                    # card can be honest that an unattributed step is not
                    # "nobody's" -- it is a step whose owner configuration does
                    # not record at this grain (a GO/NO-GO call, a telemetry
                    # check, or a service-level command anyone may send).
                    "attribution": "func_id" if owners else "none",
                    "index": i,
                    "type": step.get("type", ""),
                    "description": step.get("description", ""),
                    "mnemonic": step.get("mnemonic", ""),
                    "service": step.get("service"),
                    "subtype": step.get("subtype"),
                    "func_id": step.get("func_id"),
                    "owners": owners,
                    "mine": step_mine,
                })
            out.append({
                "id": entry.get("id", ""),
                "name": entry.get("name", ""),
                "category": entry.get("category", ""),
                "file": entry.get("file", ""),
                "required_positions": required,
                "position_roles": roles,
                "my_role": roles.get(position, ""),
                "relevant": mine,
                "my_step_count": n_mine,
                "steps": steps_out,
            })
        return web.json_response({
            "position": position, "sees_all": sees_all, "cards": out,
            "step_attribution": "derived from commanding authority "
                                "(positions.yaml allowed_func_ids/services)"})

    def _procedure_steps_for(self, entry: dict) -> list[dict]:
        """Canonical steps for an index entry, or [] when it is prose-only."""
        resolved = self._indexed_procedure(entry.get("id", ""))
        if resolved and resolved.get("steps"):
            return list(resolved["steps"])
        rel = entry.get("file", "")
        if not rel:
            return []
        path = self.config_dir / "procedures" / rel
        if not path.is_file():
            return []
        try:
            from smo_common.procedures import markdown_steps
            steps, _ = markdown_steps(path.read_text())
            return list(steps)
        except Exception:  # noqa: BLE001 - a card without steps is still a card
            logger.debug("no steps extractable from %s", rel)
            return []

    # ── Shift handover ───────────────────────────────────────────────

    async def _handle_handover_get(self, request):
        return web.json_response({"notes": list(self._handover_log)})

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
                # Sim time: a handover note is a mission log line, rendered
                # in the console next to telemetry timestamps.
                "timestamp": self.sim_now_ts(),
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

            # Normalise into the canonical schema and validate BEFORE writing
            # (defect #83). An unknown tlm_check parameter used to be accepted
            # silently and then cost a 30 s timeout and a FAILED procedure the
            # first time an operator ran it.
            steps = to_canonical(steps)
            problems = validate_steps(steps, self._tlm_vocabulary())
            if problems:
                return web.json_response(
                    {"error": "Procedure has invalid steps",
                     "problems": problems}, status=400)

            custom_dir = self.config_dir / "procedures" / "custom"
            custom_dir.mkdir(parents=True, exist_ok=True)

            # Sanitize filename
            safe_name = "".join(
                c if c.isalnum() or c in "-_" else "_"
                for c in name.lower().replace(" ", "_")
            )
            filepath = custom_dir / f"{safe_name}.yaml"

            proc_data = {
                "schema_version": CANONICAL_SCHEMA_VERSION,
                "name": name,
                "description": description,
                "created_by": position,
                "steps": steps,
            }
            with open(filepath, "w") as f:
                yaml.dump(proc_data, f, default_flow_style=False, sort_keys=False)

            return web.json_response({
                "status": "saved",
                "path": f"custom/{safe_name}.yaml",
                "schema_version": CANONICAL_SCHEMA_VERSION,
                "steps": steps,
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
                        # Canonicalised on the way out so a procedure saved by
                        # an older build still lists and runs (defect #83).
                        "steps": to_canonical(data.get("steps", [])),
                        "schema_version": data.get(
                            "schema_version", CANONICAL_SCHEMA_VERSION),
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

    # ── Schedule / memory report helpers ─────────────────────────────

    def _obc_cuc_to_utc(self, cuc: float) -> Optional[str]:
        """Inverse of ``_utc_to_obc_cuc``: on-board CUC seconds -> SIM UTC.

        Anchored on the latest OBC clock telemetry when available, so the
        schedule display shows the sim time the command will *actually* fire,
        clock error included; otherwise the shared mission epoch.
        """
        try:
            obc_cuc = self.obc_cuc_now()
            if obc_cuc is not None and obc_cuc > 0:
                sim_now = self.sim_now()
                if sim_now.tzinfo is None:
                    sim_now = sim_now.replace(tzinfo=timezone.utc)
                delta_s = float(cuc) - float(obc_cuc)
                return (sim_now + timedelta(seconds=delta_s)).isoformat()
            return cuc_seconds_to_datetime(float(cuc)).replace(
                tzinfo=timezone.utc).isoformat()
        except Exception:
            return None

    def _resolve_command_name(self, service: int, subtype: int,
                              data_hex: str = "") -> str:
        """Resolve service/subtype (+ S8 func id) to a catalogue mnemonic.

        This is what turns "S8.1" in the schedule table into
        "PLD_CAPTURE_IMAGE" — an operator can read the second one.
        """
        func_id = None
        if service == 8 and subtype == 1 and data_hex:
            try:
                func_id = int(data_hex[:2], 16)
            except ValueError:
                func_id = None
        fallback = None
        for cmd in self._tc_catalog:
            if int(getattr(cmd, "service", -1)) != int(service):
                continue
            if int(getattr(cmd, "subtype", -1)) != int(subtype):
                continue
            cmd_func = getattr(cmd, "func_id", None)
            if func_id is not None:
                if cmd_func is not None and int(cmd_func) == func_id:
                    return str(getattr(cmd, "name", ""))
                continue
            if cmd_func is None:
                return str(getattr(cmd, "name", ""))
            if fallback is None:
                fallback = str(getattr(cmd, "name", ""))
        if fallback:
            return fallback
        return f"S{service}.{subtype}"

    def _annotate_schedule(self, report: dict) -> None:
        """Add ground-side resolution to a parsed TM(11,18) report.

        The spacecraft reports ids and bytes. The operator needs a mnemonic
        and a wall-clock time — both are ground knowledge (tc_catalog.yaml and
        the OBC clock correlation), so they are added here rather than asked
        of the spacecraft.
        """
        for e in report.get("entries", []):
            e["exec_utc"] = self._obc_cuc_to_utc(e["exec_time_cuc"])
            e["mnemonic"] = self._resolve_command_name(
                e["service"], e["subtype"], e.get("data_hex", ""))
        # Sim time: the console renders this as "reported N s ago" against the
        # same clock it ages telemetry with.
        report["reported_ts"] = self.sim_now_ts()

    def _memory_region_name(self, address: int) -> str:
        """Name of the declared region containing `address` ('' if unmapped)."""
        for r in self._memory_map.get("regions", []):
            if r["start"] <= address < r["end"]:
                return r["name"]
        return ""

    # ── FDIR / schedule / memory / station API handlers ──────────────

    async def _handle_fdir_display(self, request):
        """GET /api/fdir — everything the FDIR window renders.

        Panels (a) state strip, (b) S12 monitoring table, (c) S19 event-action
        table, (d) alarm/anomaly journal and (e) the controls' current state,
        assembled from the same model so they cannot disagree.
        """
        async with self._param_cache_lock:
            values = {pid: e["value"] for pid, e in self._param_cache.items()}
        active = self._fdir_alarm_panel.get_active_alarms()
        data = self._fdir_model.display_data(
            active_alarms=active,
            param_values=values,
            load_shed_stage=self._load_shed_stage(),
            now=self.sim_now_ts(),
        )
        data["active_alarms"] = active
        data["alarm_count_by_severity"] = {
            sev: len([a for a in active if a["severity"] == sev])
            for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
        }
        # Link state on the FDIR strip (defect #86). FDIR's `ttc_reduce_power`
        # action drops the TM tier, so "which rung are we on, and is it the one
        # the ground asked for" belongs next to the FDIR level. Named tier, not
        # a bare bit rate, and both active and commanded — a demoted link is
        # exactly the condition this window exists to make visible.
        ttc_group = {
            "tm_tier": values.get(0x0539),
            "tm_tier_cmd": values.get(0x053A),
            "tm_symbol_rate": values.get(0x053B),
            "downlink_mode": (None if values.get(0x053C) is None
                              else bool(values.get(0x053C))),
            "data_rate_bps": values.get(0x0506),
            "antenna_deployed": (None if values.get(0x0520) is None
                                 else bool(values.get(0x0520))),
            "elevation_deg": values.get(0x050A),
        }
        link = self._build_link_tier_view(
            {k: v for k, v in ttc_group.items() if v is not None})
        link["tm_rate_ladder"] = self._tm_rate_ladder
        data["link"] = link
        data["state"]["tm_tier_name"] = link.get("tm_tier_name")
        data["state"]["tm_tier_label"] = link.get("tm_tier_label")
        data["state"]["tm_tier_demoted"] = link.get("tm_tier_demoted", False)
        # On-board autonomy: faults detected, cascades pending and the recovery
        # procedure in progress (WP-5).
        data["autonomy"] = self._sim_autonomy
        return web.json_response(data)

    async def _handle_schedule_report(self, request):
        """GET /api/schedule — the last TM(11,18) on-board schedule report."""
        if not self._schedule_report:
            return web.json_response({
                "count": 0, "entries": [], "enabled": None, "next_id": None,
                "never_reported": True,
            })
        return web.json_response(self._schedule_report)

    async def _handle_memory_map(self, request):
        """GET /api/memory-map — the declared on-board address space."""
        return web.json_response({
            **self._memory_map,
            "dumps": list(self._memory_dumps),
            "checks": list(self._memory_checks),
        })

    async def _handle_ground_stations(self, request):
        """GET /api/ground-stations — the mission's real ground network."""
        return web.json_response({"ground_stations": self.ground_stations()})

    # ── Alarm journal ─────────────────────────────────────────────────

    async def _raise_alarm(self, alarm: dict) -> dict:
        """File one alarm: journal, FDIR panel, archive, WS broadcast.

        Every alarm source (S5 events, S12 out-of-limit reports and the
        ground limit check) funnels through here so they carry the same
        shape, the same id sequence and — importantly for acknowledgement
        persistence — the archive row id (``db_id``) that
        ``TMArchive.acknowledge_alarm`` needs.
        """
        self._alarm_id_counter += 1
        # RAISE TIME IS SIM TIME (spec Part D.3). An alarm describes a moment
        # in the mission, so it has to be timestamped on the mission's clock —
        # the same one the telemetry value that raised it is stamped with, and
        # the same one the archive row lands on.
        alarm.setdefault("timestamp", self.sim_now_ts())
        alarm["time_base"] = "sim"
        alarm.setdefault("acknowledged", False)
        alarm.setdefault("limit", "")
        alarm["id"] = self._alarm_id_counter
        # "This alarm ⇒ run CTG-001". Resolved once, at raise time, so the
        # journal row, the WS broadcast and the archived copy all agree.
        proc = self.resolve_alarm_procedure(alarm)
        if proc:
            alarm["procedure"] = proc
        self._alarm_journal.appendleft(alarm)
        try:
            self._fdir_alarm_panel.add_alarm(alarm)
        except Exception:
            pass
        try:
            row_id = self._archive.store_alarm(
                alarm, utc_epoch=float(alarm["timestamp"]))
            if row_id and row_id > 0:
                alarm["db_id"] = row_id
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
        return alarm

    async def _clear_ground_alarm(self, param_id: int, ga: dict) -> None:
        """A limited parameter came back inside its band.

        The journal entry stays (it is a log), but it is marked cleared and
        auto-acknowledged so a transient does not leave the header counter
        permanently lit; the WS message lets open consoles restyle the row.
        """
        tag = f"0x{param_id:04X}"
        # CLEAR TIME IS SIM TIME, like the raise (spec Part D.3): the two are
        # subtracted to give the duration of the excursion, so they must be on
        # one clock.
        cleared_ts = self.sim_now_ts()
        cleared: list[int] = []
        for a in self._alarm_journal:
            if a.get("source") == "LIMIT" and a.get("parameter") == tag \
                    and not a.get("cleared"):
                a["cleared"] = True
                a["cleared_ts"] = cleared_ts
                a["acknowledged"] = True
                cleared.append(a["id"])
                db_id = a.get("db_id")
                if db_id:
                    try:
                        self._archive.acknowledge_alarm(int(db_id))
                    except Exception:
                        pass
        if not cleared:
            return
        msg = json.dumps({
            "type": "alarm_cleared",
            "parameter": tag,
            "ids": cleared,
            "value": ga.get("value"),
        })
        async with self._ws_lock:
            clients = list(self._ws_clients)
        for ws, _pos in clients:
            try:
                await ws.send_str(msg)
            except Exception:
                pass

    async def _handle_limits(self, request):
        """GET /api/limits — the ground limit bands, and the live state.

        Ground limit checking is what makes a parameter turn amber/red on the
        console without the spacecraft having to tell us; 4 of the limited
        parameters have no S12 monitor on board at all.
        """
        bands = {}
        for pid, band in self._tm_processor.limit_bands().items():
            meta = self._param_meta.get(pid, {})
            bands[f"0x{pid:04X}"] = {
                **(band or {}),
                "name": meta.get("name", ""),
                "subsystem": meta.get("subsystem", ""),
                "units": meta.get("units", ""),
            }
        return web.json_response({
            "limits": bands,
            "state": {f"0x{pid:04X}": s
                      for pid, s in self._tm_processor.limit_statuses().items()},
            "unacknowledged": self.unacknowledged_alarm_count(),
        })

    async def _handle_annotations(self, request):
        """GET /api/annotations — RETIRED teaching layer; always serves ``{}``.

        The guided-mode annotation content was deleted 2026-08-19 (owner
        ruling: the operator UI must be intuitive without a training mode).
        The route is kept for URL stability (route-snapshot contract) and
        now always returns an empty mapping. The two operational tables that
        used to ride in annotations.yaml (capture gates, health baselines)
        moved to display_config.yaml — see /api/display-config.
        """
        return web.json_response({})

    async def _handle_display_config(self, request):
        """GET /api/display-config — operational display configuration.

        Serves ``configs/<mission>/mcs/display_config.yaml`` verbatim as
        JSON: the NOM-002 ``capture_gates`` table (G4) and the commissioning
        ``health_baselines`` snapshot (G15). An absent or unreadable file is
        not an error — the console renders its "no gate table / no baseline"
        affordances — so this degrades to ``{}``.
        """
        path = self.config_dir / "mcs" / "display_config.yaml"
        if not path.exists():
            return web.json_response({})
        try:
            import yaml as _yaml
            with open(path) as f:
                data = _yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                logger.warning("display_config.yaml is not a mapping — serving {}")
                data = {}
            return web.json_response(data)
        except Exception as e:  # noqa: BLE001 — display config must not 500
            logger.warning("display_config.yaml unreadable (%s) — serving {}", e)
            return web.json_response({})

    # ── POSITION_NEEDS G5/G6/G7/G8 handlers (additive) ───────────────

    async def _handle_link_prediction(self, request):
        """GET /api/displays/link-prediction — predicted range/FSPL/Eb-N0.

        Ground prediction for the current-or-next pass, shaped for overlay
        drawing (arrays of [t, value], t = sim-time Unix epoch seconds).
        ``available: false`` with a reason when the prediction cannot be
        computed — never a fabricated curve.
        """
        try:
            payload = self._link_predictor.predict(self.sim_now())
        except Exception as e:  # noqa: BLE001
            payload = {"available": False, "reason": str(e), "tiers": []}
        payload["time_base"] = "sim"
        return web.json_response(payload)

    async def _handle_fault_graph(self, request):
        """GET /api/fault-graph — the parsed fault-propagation graph (G6)."""
        from smo_mcs.fault_graph import load_fault_graph
        names_to_ids = {m.get("name", ""): pid
                        for pid, m in self._param_meta.items()}
        return web.json_response(
            load_fault_graph(self.config_dir, names_to_ids))

    async def _handle_gonogo_matrix(self, request):
        """GET /api/gonogo-matrix — the GO/NO-GO call matrix (G7).

        Serves ``configs/<mission>/mcs/gonogo_matrix.yaml`` (the config
        mirror of the manual §"GO/NO-GO Call Matrix"). Each row carries
        ``situation``, ``who_calls``, ``who_responds`` (position ids or the
        literals ALL/affected), ``fd_decision`` and ``critical`` — the
        GO/NO-GO panel renders the matching row when a poll opens (FD3).
        Absent file ⇒ ``{"available": false}`` — the panel says so instead
        of inventing rows.
        """
        path = self.config_dir / "mcs" / "gonogo_matrix.yaml"
        if not path.exists():
            return web.json_response(
                {"available": False,
                 "reason": "mcs/gonogo_matrix.yaml not found", "matrix": []})
        try:
            import yaml as _yaml
            data = _yaml.safe_load(path.read_text()) or {}
            rows = data.get("gonogo_matrix", [])
            if not isinstance(rows, list):
                rows = []
            return web.json_response({
                "available": True,
                "source": "mcs/gonogo_matrix.yaml (mirrored in "
                          "manual/10_desk_procedures.md)",
                "matrix": rows,
            })
        except Exception as e:  # noqa: BLE001
            return web.json_response(
                {"available": False, "reason": str(e), "matrix": []})

    # ── Console / mission log (G8) ───────────────────────────────────

    def _console_log(self, kind: str, text: str, *, position: str = "",
                     ref: str = "", source: str = "server") -> None:
        """Append a server-generated console-log entry, never raising."""
        try:
            self._archive.store_console_entry(
                utc_epoch=self.sim_now_ts(), kind=kind, text=text,
                position=position, ref=ref, source=source)
        except Exception as e:  # noqa: BLE001 — logging must not block ops
            logger.debug("console-log append failed: %s", e)

    async def _handle_console_log_post(self, request):
        """POST /api/console-log — append one operator entry.

        Body: ``{ts?, position, kind, text, ref?}``. ``kind`` is one of
        tc|ack|shelve|procedure|gonogo|mark|note. ``ts`` (sim-time Unix
        epoch) defaults to the MCS clock. The store is append-only.
        """
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        kind = str(body.get("kind", "")).strip().lower()
        text = str(body.get("text", "")).strip()
        if kind not in TMArchive.CONSOLE_LOG_KINDS:
            return web.json_response(
                {"error": f"kind must be one of "
                          f"{list(TMArchive.CONSOLE_LOG_KINDS)}"},
                status=400)
        if not text:
            return web.json_response({"error": "text is required"},
                                     status=400)
        try:
            ts = float(body["ts"]) if "ts" in body else self.sim_now_ts()
        except (TypeError, ValueError):
            return web.json_response({"error": "ts must be a number"},
                                     status=400)
        position = str(body.get("position", ""))
        ref = str(body.get("ref", ""))
        try:
            row_id = self._archive.store_console_entry(
                utc_epoch=ts, kind=kind, text=text, position=position,
                ref=ref, source="operator")
        except Exception as e:  # noqa: BLE001
            return web.json_response(
                {"error": f"console-log store unavailable: {e}"}, status=503)
        return web.json_response({
            "status": "logged",
            "entry": {"id": row_id, "utc_epoch": ts, "position": position,
                      "kind": kind, "text": text, "ref": ref,
                      "source": "operator"},
            "time_base": "sim",
        })

    async def _handle_console_log_get(self, request):
        """GET /api/console-log?since=&until=&limit= — entries, oldest first."""
        def _qf(key):
            v = request.query.get(key)
            return None if v in (None, "") else float(v)
        try:
            since = _qf("since")
            until = _qf("until")
            limit = int(request.query.get("limit", 1000))
        except (TypeError, ValueError):
            return web.json_response(
                {"error": "since/until/limit must be numeric"}, status=400)
        entries, degraded = self._archive_query(
            "query_console_log", self._archive.query_console_log,
            since, until, limit)
        return web.json_response({
            "entries": entries or [],
            "archive_unavailable": degraded,
            "archive": self._archive_meta(),
            "time_base": "sim",
        })

    def _persist_alarm_ack(self, alarm: dict) -> None:
        db_id = alarm.get("db_id")
        if not db_id:
            return
        try:
            self._archive.acknowledge_alarm(int(db_id))
        except Exception as e:
            logger.debug("acknowledge_alarm failed: %s", e)

    def unacknowledged_alarm_count(self) -> int:
        return sum(1 for a in self._alarm_journal if not a.get("acknowledged"))

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
        """Acknowledge an alarm by ID.

        The acknowledgement is written through to the archive so it survives
        an MCS restart — ``TMArchive.acknowledge_alarm`` previously had zero
        callers and every archived alarm read back as unacknowledged.
        """
        try:
            alarm_id = int(request.match_info["alarm_id"])
            for alarm in self._alarm_journal:
                if alarm["id"] == alarm_id:
                    alarm["acknowledged"] = True
                    self._persist_alarm_ack(alarm)
                    try:
                        self._fdir_alarm_panel.acknowledge_alarm(alarm_id)
                    except Exception:
                        pass
                    # Console/mission log (G8): alarm handling is part of
                    # the decision record.
                    self._console_log(
                        "ack",
                        f"Alarm {alarm_id} acknowledged"
                        f" ({alarm.get('parameter') or alarm.get('subsystem', '')})",
                        ref=f"alarm:{alarm_id}")
                    return web.json_response({
                        "status": "acknowledged", "id": alarm_id,
                        "unacknowledged": self.unacknowledged_alarm_count(),
                    })
            return web.json_response(
                {"error": f"Alarm {alarm_id} not found"}, status=404
            )
        except (ValueError, KeyError) as e:
            return web.json_response({"error": str(e)}, status=400)

    # ── Client config (distributed deployment) ──────────────────────

    #: Fallback staleness policy, used when mcs/staleness.yaml is absent or
    #: unreadable. Identical to the shipped file so a missing config degrades
    #: to the documented behaviour rather than to the old hard-coded 5/15 s.
    DEFAULT_STALENESS_POLICY = {
        "stale_factor": 1.5,
        "fresh_factor": 0.9,
        "floor_fresh_s": 10.0,
        "floor_stale_s": 20.0,
        "aperiodic_fresh_s": 300.0,
        "aperiodic_stale_s": 900.0,
        # Time-correlation thresholds (spec Part D.9): how far the on-board
        # clock may sit from sim time before the console says so. In SIM
        # SECONDS, like everything else here. At the nominal 12 ppm drift the
        # OBC gains ~1 s/day, so 5 s is roughly "you have not synced in the
        # best part of a week" and 30 s is "this will visibly mis-fire an S11
        # schedule". Both are overridable from mcs/staleness.yaml.
        "clock_offset_warn_s": 5.0,
        "clock_offset_alarm_s": 30.0,
    }

    def _load_staleness_policy(self) -> dict:
        """Load the display staleness policy from mcs/staleness.yaml.

        Every threshold here is in **sim seconds** and is compared against an
        age derived from :meth:`sim_now_ts`. Before the Part D change the ages
        were wall-clock, so a paused simulator made the whole console stale
        and an accelerated one made nothing ever stale.
        """
        policy = dict(self.DEFAULT_STALENESS_POLICY)
        try:
            import yaml as _yaml
            path = self.config_dir / "mcs" / "staleness.yaml"
            with open(path) as f:
                data = _yaml.safe_load(f) or {}
            block = data.get("staleness") or {}
            for key in policy:
                if block.get(key) is not None:
                    policy[key] = float(block[key])
        except FileNotFoundError:
            logger.info("mcs/staleness.yaml absent — using built-in display "
                        "staleness policy %s", policy)
        except Exception as e:  # noqa: BLE001 — never block start-up on it
            logger.warning("Failed to load mcs/staleness.yaml (%s); "
                           "using built-in display staleness policy", e)
        return policy

    async def _handle_client_config(self, request):
        """Return runtime config for the browser client.

        Carries the display **staleness policy** and the **cadence join** the
        browser needs to derive a per-parameter freshness threshold from the
        real downlink cadence (Franz: "can the stale data flag be set on the
        cadence of the telemetry times 1.5"). Before this, the page carried
        `TM_FRESH_S = 5` / `TM_STALE_S = 15` and `_STALE_DEFAULT_S = 8.0` as JS
        literals and applied them to a 60 s structure.

        `param_cadence` is deliberately compact — ``hex -> [interval_s, sid…]``
        — because it covers every HK parameter on the spacecraft.
        """
        return web.json_response({
            "planner_url": self._planner_api_base,
            # Resolved time source ("sim" | "real") so the modern page can
            # decide TRAINING-chip visibility locally (additive; Agent 5).
            "time_source": self._time_source,
            "staleness": dict(self._staleness_policy),
            "param_cadence": {
                f"0x{pid:04X}": [ent["interval_s"], *ent["sids"]]
                for pid, ent in sorted(self._param_cadence.items())
            },
            "subsystem_cadence": {
                sub: {"interval_s": ent["interval_s"], "sids": ent["sids"]}
                for sub, ent in sorted(self._subsystem_cadence.items())
            },
            "sid_intervals": {
                str(sid): meta.get("interval_s")
                for sid, meta in sorted(self._hk_sid_meta.items())
            },
            "sid_names": {
                str(sid): meta.get("name")
                for sid, meta in sorted(self._hk_sid_meta.items())
            },
        })

    # ── Contact window timeline proxy (Feature 1) ────────────────────

    @staticmethod
    def _normalize_contact(c: dict) -> dict:
        """Translate a planner contact window into the MCS display schema.

        The planner emits ``aos``/``los`` (ISO 8601 strings), ``gs_name`` and
        ``max_elevation_deg``; the MCS UI timeline reads ``aos_utc``/
        ``los_utc`` (unix epoch floats) + ``ground_station``, and the
        contact-schedule panel reads ``aos_time``/``los_time`` +
        ``max_elevation``. Normalising here (the consumer side — defect #47)
        makes every real planner pass render; original keys are preserved.
        """
        out = dict(c)

        def _iso_to_epoch(v):
            if isinstance(v, (int, float)):
                return float(v)
            try:
                dtp = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                if dtp.tzinfo is None:
                    dtp = dtp.replace(tzinfo=timezone.utc)
                return dtp.timestamp()
            except (ValueError, TypeError):
                return None

        if "aos_utc" not in out and "aos" in out:
            e = _iso_to_epoch(out["aos"])
            if e is not None:
                out["aos_utc"] = e
        if "los_utc" not in out and "los" in out:
            e = _iso_to_epoch(out["los"])
            if e is not None:
                out["los_utc"] = e
        if "ground_station" not in out:
            out["ground_station"] = (out.get("gs_name")
                                     or out.get("gs")
                                     or out.get("station", "Unknown"))
        if "max_elevation" not in out and "max_elevation_deg" in out:
            out["max_elevation"] = out["max_elevation_deg"]
        if "aos_time" not in out and "aos_utc" in out:
            out["aos_time"] = out["aos_utc"]
        if "los_time" not in out and "los_utc" in out:
            out["los_time"] = out["los_utc"]
        if ("duration_s" not in out and "aos_utc" in out
                and "los_utc" in out):
            out["duration_s"] = out["los_utc"] - out["aos_utc"]
        return out

    async def _handle_contacts_proxy(self, request):
        """Proxy contact window data from the planner API.

        Contacts are normalised into the display schema (see
        ``_normalize_contact``). When the planner is unreachable the MCS no
        longer fabricates plausible-looking passes (DI-03): it returns an
        empty list with ``planner_unavailable`` set so the UI can show an
        honest "no data" state.
        """
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self._planner_api_base}/api/contacts"
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        data["contacts"] = [
                            self._normalize_contact(c)
                            for c in data.get("contacts", [])
                        ]
                        return web.json_response(data)
                    return web.json_response(
                        {"contacts": [], "error": f"Planner returned {resp.status}"},
                        status=resp.status,
                    )
        except Exception as e:
            # Planner not available — report honestly, never fabricate passes.
            return web.json_response({
                "contacts": [],
                "planner_unavailable": True,
                "error": f"Planner unreachable: {e}",
            })

    # ── Stored TM dump (Feature 3) ────────────────────────────────────

    async def _handle_tm_dump(self, request):
        """Send S15.9 dump command and return acknowledgment."""
        store_id, subsystem, seq = 1, "unknown", None
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
                "timestamp": self.sim_now_ts(),
                "error_code": 0,
                "position": "tm_dump",
            }
            self._record_command(entry, data_hex=data.hex())

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

            # Try to use real archived data; fall back to synthetic — but
            # tag the fallback explicitly (DI-03) so trainees can never
            # mistake fabricated points for spacecraft telemetry.
            dump_key = f"{subsystem}_{store_id}"
            # A damaged archive must NOT take out the S15 dump view. The
            # command went to the spacecraft either way; the ground-side
            # playback simply falls back to (clearly flagged) synthetic data
            # and the response says the archive is unavailable.
            archived, archive_degraded = self._archive_query(
                "get_playback_data", self._archive.get_playback_data,
                subsystem, now_epoch=self.sim_now_ts())
            if archived:
                self._tm_dump_data[dump_key] = archived
                self._tm_dump_synthetic.discard(dump_key)
            else:
                self._tm_dump_data[dump_key] = self._generate_dump_data(
                    subsystem, store_id
                )
                self._tm_dump_synthetic.add(dump_key)

            return web.json_response({
                "status": "sent",
                "seq": seq,
                "dump_key": dump_key,
                "synthetic": dump_key in self._tm_dump_synthetic,
                "archive_unavailable": archive_degraded,
                "archive": self._archive_meta(),
            })
        except sqlite3.Error as e:
            # The archive is ground-side storage; its failure is not a
            # command failure. Log the driver text, show the operator a
            # sentence about the archive, and keep a 200 so the dump view
            # renders its degraded state instead of an error toast reading
            # "database disk image is malformed".
            logger.error("TM dump: archive error, degrading: %s", e)
            return web.json_response({
                "status": "sent",
                "seq": seq,
                "dump_key": f"{subsystem}_{store_id}",
                "synthetic": False,
                "archive_unavailable": True,
                "archive": self._archive_meta(),
            })
        except Exception as e:
            logger.warning("TM dump error: %s", e)
            return web.json_response(
                {"status": "error", "message": str(e)}, status=400
            )

    async def _handle_tm_dump_data(self, request):
        """Return stored TM dump data for playback.

        ``synthetic`` is True when the points were generated by the MCS
        because the archive had nothing (DI-03) — the UI must render this
        visibly; each synthetic point also carries its own flag.
        """
        dump_key = request.query.get("key", "")
        data = self._tm_dump_data.get(dump_key, [])
        return web.json_response({
            "dump_key": dump_key,
            "data": data,
            "synthetic": dump_key in self._tm_dump_synthetic,
        })

    def _generate_dump_data(self, subsystem: str, store_id: int) -> list[dict]:
        """Generate SYNTHETIC historical TM data for dump playback.

        Only used when the archive holds no real data; every point is
        tagged ``synthetic: True`` and the serving endpoints flag the whole
        payload (DI-03).
        """
        import random
        # Sim time — synthetic points must sit on the same axis as archived
        # ones, or the trend page draws them a decade apart.
        now = self.sim_now_ts()
        points = []
        # Generate 60 data points going back 1 hour
        for i in range(60):
            t = now - (60 - i) * 60  # 1-minute intervals
            point = {"timestamp": t, "index": i, "synthetic": True}
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

            # Console/mission log (G8): poll opened.
            self._console_log("gonogo", f"GO/NO-GO poll opened: {label}",
                              position=position)

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

            # Console/mission log (G8): poll resolved (outside the lock).
            if result_msg is not None:
                votes = ", ".join(f"{p}:{v}"
                                  for p, v in sorted(all_responses.items()))
                self._console_log(
                    "gonogo",
                    f"GO/NO-GO result: {result} — {self._go_nogo_label} "
                    f"({votes})")

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
        """GET /api/displays/contact-schedule — Ground station pass schedule.

        Uses the MCS ground clock (sim time in sim mode) as "now" — planner
        passes are computed against sim time, so classifying them against
        wall clock mislabelled every pass (defect #47).
        """
        current_time = self.sim_now_ts()
        # Try to fetch from planner API if available
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._planner_api_base}/api/contacts",
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        passes = [self._normalize_contact(c)
                                  for c in data.get("contacts", [])]
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
        """GET /api/displays/fdir-alarms — FDIR status and active alarms.

        The rule counters here used to be permanently zero and `fdir_level`
        permanently "nominal" because nothing in the repo ever called
        `update_s12_rules` / `update_s19_rules` / `set_fdir_level`. They are
        now driven from the same FDIR model as /api/fdir, so this legacy shape
        is consistent with the window rather than contradicting it.
        """
        self._sync_legacy_fdir_panel()
        return web.json_response(self._fdir_alarm_panel.get_display_data())

    async def _handle_system_overview(self, request):
        """GET /api/displays/system-overview — Top-level system status board.

        Now fed by three things that previously fed nothing: the ground limit
        check (subsystem health was hard GREEN forever), the alarm journal
        (alarm counts were hard zero) and the link-freshness verdict.
        """
        dash = self._system_overview_dashboard
        dash.update_from_telemetry(self._latest_state)
        # Ground limit state, grouped by subsystem.
        by_sub: dict[str, dict] = {}
        for pid, status in self._tm_processor.limit_statuses().items():
            meta = self._param_meta.get(pid, {})
            sub = (meta.get("subsystem")
                   or self._param_id_to_subsystem(pid) or "other")
            by_sub.setdefault(sub, {})[f"0x{pid:04X}"] = status
        dash.update_health_from_limits(by_sub)
        journal = list(self._alarm_journal)
        open_alarms = [a for a in journal
                       if not a.get("acknowledged") and not a.get("cleared")]
        dash.set_alarm_counts(
            total=len(open_alarms),
            high=sum(1 for a in open_alarms if int(a.get("severity", 0)) >= 4),
        )
        data = dash.get_display_data()
        data["link"] = {
            "stale": bool(self._latest_state.get("stale", True)),
            "last_frame_age_s": self._latest_state.get("last_frame_age_s"),
            "tm_packets_received": self.tm_packets_received,
        }
        data["unacknowledged_alarms"] = self.unacknowledged_alarm_count()
        return web.json_response(data)

    async def _handle_alarm_ack_display(self, request):
        """POST /api/displays/alarms/{alarm_id}/ack — Acknowledge an alarm."""
        try:
            alarm_id = int(request.match_info.get("alarm_id", 0))
            self._fdir_alarm_panel.acknowledge_alarm(alarm_id)
            # Keep the journal and the archive in step — the display route
            # used to ack only the in-memory FDIR panel, so the same alarm
            # read back unacknowledged from /api/alarms and from the archive.
            for alarm in self._alarm_journal:
                if alarm.get("id") == alarm_id:
                    alarm["acknowledged"] = True
                    self._persist_alarm_ack(alarm)
                    self._console_log(
                        "ack",
                        f"Alarm {alarm_id} acknowledged"
                        f" ({alarm.get('parameter') or alarm.get('subsystem', '')})",
                        ref=f"alarm:{alarm_id}")
                    break
            return web.json_response({"status": "acknowledged", "alarm_id": alarm_id})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_alarm_trends(self, request):
        """POST /api/displays/alarm-trends — alarm counts over a time window.

        Rewritten in Phase G against the **archive** rather than the in-memory
        `FDIRAlarmPanel` journal. The old version answered "the last N alarms
        this process happens to remember", which is not a trend: it lost every
        alarm raised before the last MCS restart and could not be windowed.
        Body: ``{subsystem, start, end, bucket_s, limit}``.
        """
        try:
            body = await request.json()
        except Exception:
            body = {}
        subsystem = str(body.get("subsystem", "") or "")
        # Archive rows are on sim time, so the default window must be too.
        now = self.sim_now_ts()
        end = float(body.get("end", now))
        start = float(body.get("start", end - 24 * 3600))
        bucket_s = max(60.0, float(body.get("bucket_s", 3600)))
        limit = int(body.get("limit", 500))
        try:
            rows = self._archive.query_alarms(start, end, limit)
        except Exception as e:  # noqa: BLE001
            logger.debug("query_alarms failed: %s", e)
            rows = []
        if subsystem:
            rows = [a for a in rows if a.get("subsystem") == subsystem]

        buckets: dict[float, dict] = {}
        by_subsystem: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for a in rows:
            t0 = start + math.floor((a["utc_epoch"] - start) / bucket_s) * bucket_s
            b = buckets.setdefault(t0, {"t": t0, "total": 0, "high": 0,
                                        "unacknowledged": 0})
            b["total"] += 1
            if int(a.get("severity", 0)) >= 4:
                b["high"] += 1
            if not a.get("acknowledged"):
                b["unacknowledged"] += 1
            sub = a.get("subsystem") or "unknown"
            by_subsystem[sub] = by_subsystem.get(sub, 0) + 1
            src = a.get("source") or "unknown"
            by_source[src] = by_source.get(src, 0) + 1

        return web.json_response({
            "subsystem": subsystem,
            "start": start, "end": end, "bucket_s": bucket_s,
            "buckets": [buckets[k] for k in sorted(buckets)],
            "by_subsystem": by_subsystem,
            "by_source": by_source,
            "alarms": rows,
            "count": len(rows),
        })


def main():
    parser = argparse.ArgumentParser(description="SMO Mission Control System")
    parser.add_argument("--connect", default="localhost:8002",
                        help="TM source host:port")
    parser.add_argument("--config", default="configs/eosat1/",
                        help="Config directory")
    parser.add_argument("--port", type=int, default=9090, help="HTTP port")
    parser.add_argument("--host", default=None,
                        help="HTTP bind address (default $SMO_BIND or 0.0.0.0; "
                             "use 127.0.0.1 for a loopback-only console)")
    parser.add_argument("--tc-port", type=int, default=None,
                        help="TC destination port (default: same host, port 8001)")
    parser.add_argument("--sim-epoch", default=None,
                        help="Fixed simulation epoch (ISO 8601 UTC, e.g. 2026-08-20T12:00:00Z). "
                             "If omitted, ground time uses real wall-clock UTC.")
    parser.add_argument("--time-source", default=None, choices=["sim", "real"],
                        help="Ground clock source: 'sim' (closed-loop follow simulator "
                             "sim_time/speed) or 'real' (wall-clock UTC). Overrides "
                             "SMO_TIME_SOURCE env and mission-config time_source.")
    parser.add_argument("--sim-state-url", default=None,
                        help="Simulator state endpoint to poll for sim_time/speed in sim mode. "
                             "Overrides SMO_SIM_STATE_URL env and mission-config sim_state_url. "
                             "Default http://<connect_host>:<sim-http-port>/api/state "
                             "(port from smo_common NetworkConfig or SMO_SIM_HTTP_PORT).")
    parser.add_argument("--ui", default=None, choices=["classic", "modern"],
                        help="Console generation served at '/': 'classic' or 'modern'. "
                             "Overrides SMO_UI env and mission-config ui. Per-request "
                             "?ui= and the /classic and /modern routes win over all.")
    args = parser.parse_args()

    host, port = args.connect.rsplit(":", 1)
    tc_port = args.tc_port if args.tc_port else 8001
    logging.basicConfig(level=logging.INFO)
    server = MCSServer(args.config, host, int(port), args.port,
                       tc_port=tc_port, sim_epoch=args.sim_epoch,
                       time_source=args.time_source,
                       sim_state_url=args.sim_state_url,
                       bind_host=args.host, ui_mode=args.ui)
    asyncio.run(server.start())


if __name__ == "__main__":
    main()

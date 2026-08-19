"""SMO Simulator — Simulation Engine.

Config-driven central tick loop. Loads subsystem models from the registry,
manages shared parameter store, PUS packet queues, and FDIR callbacks.
"""
import math
import threading
import time
import queue
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from smo_common.config.loader import (
    load_mission_config, load_orbit_config, load_all_subsystem_configs,
    load_fdir_config, load_hk_structures, load_simulation_config,
)
from smo_common.state import DEFAULT_SEED, derive_rng
from smo_common.orbit.propagator import OrbitPropagator, GroundStation
from smo_common.models.registry import create_model
from smo_common.telemetry.tm_builder import TMBuilder
from smo_common.protocol.pus_services import format_command_rejection
from smo_common.protocol.ecss_packet import CucTime
from smo_simulator.tc_scheduler import TCScheduler
from smo_simulator.tm_storage import OnboardTMStorage
from smo_simulator.fdir import FaultPropagationRule

logger = logging.getLogger(__name__)

# Single minimal HK SID emitted by the bootloader. While the OBC is running the
# bootloader (phases ≤ 3) only this SID is active, routed from the bootloader
# APID. All other SIDs come online only when the application software has
# booted (phase ≥ 4). Keeping this to a single SID matches real flight practice
# where the bootloader has no knowledge of platform/payload telemetry.
BOOTLOADER_BEACON_SID = 11

# Authoritative spacecraft-mode (sc_mode) convention. This MUST match the MCS
# (smo-mcs/static/index.html) and the instructor UI. sc_mode is a DERIVED state
# recomputed each tick by Engine._update_sc_mode(); these constants are the only
# place the numeric values are defined.
SC_SAFE = 0       # uncommissioned (phase < NOMINAL) OR an FDIR safe contingency
SC_NOMINAL = 1    # commissioned, no contingency, not imaging
SC_SCIENCE = 2    # commissioned, no contingency, payload imaging
SC_EMERGENCY = 3  # an FDIR emergency contingency is latched active

# Spacecraft phase value at which the platform is considered commissioned
# (NOMINAL). Below this the spacecraft is in bootloader/LEOP/commissioning and
# sc_mode derives to SAFE.
SC_PHASE_NOMINAL = 6

# Payload model mode value meaning "actively imaging" (param 0x0600).
PAYLOAD_MODE_IMAGING = 2

# Map the string severity labels used by subsystem-model event code onto the
# integer scale (1=INFO, 2=WARNING, 3=ALARM, 4=CRITICAL) used by _emit_event.
_SEVERITY_NAME_TO_INT = {
    "INFO": 1, "LOW": 1, "DEBUG": 1,
    "WARNING": 2, "WARN": 2, "MEDIUM": 2,
    "ALARM": 3, "HIGH": 3, "ERROR": 3,
    "CRITICAL": 4, "SEVERE": 4, "FATAL": 4,
}


def _put_tm(engine, pkt: bytes) -> None:
    """Put a packet on an engine's outbound TM queue, counting drops.

    A module-level function rather than a method so it still does real work
    when called on the ``MagicMock(spec=SimulationEngine)`` engines several
    unit tests drive ``_enqueue_tm`` with.
    """
    try:
        engine.tm_queue.put_nowait(pkt)
        engine.tm_packets_enqueued += 1
    except queue.Full:
        engine.tm_queue_drops += 1
        logger.warning("TM queue full (%d) — packet dropped",
                       engine.tm_queue.maxsize)


class SimulationEngine:
    """Config-driven simulation engine.

    Reads mission config, instantiates subsystem models from registry,
    runs tick loop, manages shared parameter store and packet queues.
    """

    def __init__(self, config_dir: str | Path, speed: float | None = None):
        self.config_dir = Path(config_dir)
        # speed=None => use simulation.yaml speed.default (1.0 when absent).
        # An explicit argument always wins, so existing callers are unchanged.
        self._speed_arg = speed
        self.speed = 1.0 if speed is None else speed
        self.running = False
        self._tick_count = 0

        # Shared parameter store
        self.params: dict[int, float] = {}
        self._params_lock = threading.Lock()

        # Thread-safe queues
        self.tm_queue: queue.Queue = queue.Queue(maxsize=2000)
        self.tc_queue: queue.Queue = queue.Queue(maxsize=500)
        self.instr_queue: queue.Queue = queue.Queue(maxsize=200)
        self.event_queue: queue.Queue = queue.Queue(maxsize=500)
        # Inbound channel for events generated *inside* subsystem models
        # (defect #23). Models push raw events here; the run loop drains it
        # through _emit_event so they become S5 telemetry + S19 triggers.
        # Kept separate from event_queue (the _emit_event record buffer that
        # test_53 drains) to avoid a re-emit feedback loop.
        self._model_event_queue: queue.Queue = queue.Queue(maxsize=500)

        # Diagnostic counters
        self.tm_queue_drops: int = 0
        self.tm_packets_enqueued: int = 0

        # Tick-loop fault surface (roadmap N-1 / DI-01). `engine_fault` is
        # None while the loop is healthy; it is reported in /api/state and the
        # instructor snapshot so a crashed tick is visible instead of looking
        # like frozen telemetry.
        self.engine_fault: str | None = None
        self.engine_fault_count: int = 0
        self._engine_fault_last_log: float = 0.0
        self.instr_cmd_rejects: int = 0

        # Immutable per-tick state snapshot (roadmap N-2 / DI-02). The tick
        # thread publishes under _params_lock; readers take the published
        # object and never touch live state, so they cannot observe a torn
        # multi-field read. A lock held only by the readers — which is what
        # was here before — provides no mutual exclusion at all.
        self._snapshot: dict | None = None
        self._snapshot_tick: int = -1

        # ── THE sim clock (TIME_ARCHITECTURE Part C.1) ────────────────────
        # The engine owns the one and only sim clock. Absolute UTC of the
        # simulated world; starts at orbit.yaml t0_epoch (or the wall clock in
        # `epoch: wall` mode), advances by dt_sim, and DRIVES the orbit
        # propagator (which keeps no clock of its own). Nothing else may own a
        # sim clock. Provisional value here; re-pinned to t0_epoch below.
        self._sim_time = datetime.now(timezone.utc)
        # MET reference: t0_epoch when the run is pinned, else the sim time at
        # construction. Set once, below, next to _t0_epoch.
        self._met_epoch: datetime = self._sim_time

        # Load configs
        self._mission_cfg = load_mission_config(self.config_dir)
        self._orbit_cfg = load_orbit_config(self.config_dir)
        self._subsys_configs = load_all_subsystem_configs(self.config_dir)
        self._fdir_cfg = load_fdir_config(self.config_dir)
        # Simulation tuning layer (configs/<mission>/simulation.yaml). Absent
        # file => all-defaults object reproducing the previous hard-coded
        # behaviour, so minimal test config dirs keep working (defect #51).
        self._sim_cfg = load_simulation_config(self.config_dir)
        if self._speed_arg is None:
            self.speed = float(self._sim_cfg.speed.default)

        # Seeded RNGs (plan A-2). One master seed from simulation.yaml; every
        # component (engine + each model + failure manager) gets its own
        # random.Random derived from (seed, component-name), so tick order
        # cannot couple the streams and each stream round-trips through
        # breakpoints independently.
        self.seed: int = int(getattr(self._sim_cfg, 'seed', DEFAULT_SEED))
        self.rng = derive_rng(self.seed, "engine")

        # Sub-stepped tick integration limits (plan A-3).
        tick_cfg = getattr(self._sim_cfg, 'tick', None)
        self._dt_max_s: float = float(getattr(tick_cfg, 'dt_max_s', 1.0))
        self._max_substeps: int = max(1, int(getattr(tick_cfg, 'max_substeps', 600)))

        # Init orbit propagator
        gs_list = []
        for gs_cfg in self._orbit_cfg.ground_stations:
            gs_list.append(GroundStation(
                name=gs_cfg.name, lat_deg=gs_cfg.lat_deg,
                lon_deg=gs_cfg.lon_deg, alt_km=gs_cfg.alt_km,
                min_elevation_deg=gs_cfg.min_elevation_deg,
            ))
        self.orbit = OrbitPropagator(
            tle_line1=self._orbit_cfg.tle_line1,
            tle_line2=self._orbit_cfg.tle_line2,
            ground_stations=gs_list,
            earth_radius_km=self._orbit_cfg.earth_radius_km,
        )
        # Reproducible runs (roadmap N-5 / DI-16): start the propagator at the
        # configured reference epoch instead of the wall clock, so every cohort
        # sees the same passes, eclipses and SoC trace. simulation.yaml
        # `epoch: wall` restores the old behaviour; so does an orbit.yaml with
        # no t0_epoch.
        self._t0_epoch: datetime | None = self._resolve_t0_epoch()
        if self._t0_epoch is not None:
            self._sim_time = self._t0_epoch
            logger.info("Orbit epoch pinned to t0_epoch %s (reproducible run)",
                        self._t0_epoch.isoformat())
        else:
            logger.info("Orbit epoch = wall clock (run is not reproducible)")
        # One clock: the propagator is driven from the engine's sim time in
        # both modes. It used to seed itself from its own datetime.now() call,
        # so even a pinned run started the orbit and the engine microseconds
        # apart and an unpinned run started them milliseconds apart.
        self._met_epoch = self._sim_time
        self.orbit.propagate_to(self._sim_time)

        # Instantiate subsystem models from config
        self.subsystems: dict[str, Any] = {}
        for name, cfg in self._subsys_configs.items():
            model_name = cfg.model if hasattr(cfg, 'model') else f"{name}_basic"
            try:
                cfg_dict = cfg.model_dump() if hasattr(cfg, 'model_dump') else {}
                # Merge the simulation.yaml tuning block for this subsystem
                # (eps:/tcs:) under a reserved key. The hardware sheet
                # (subsystems/*.yaml) stays authoritative for hardware ratings;
                # simulation.yaml owns protection levels, initial conditions and
                # environment. No key is defined in both files.
                sim_block = getattr(self._sim_cfg, name, None)
                if sim_block is not None and hasattr(sim_block, 'model_dump'):
                    cfg_dict["simulation"] = sim_block.model_dump()
                # The FPA thermal envelope is single-sourced in
                # subsystems/tcs.yaml because TCS owns the FPA node
                # (DECISIONS §3 / G3-d). The payload reads that node and needs
                # the same envelope for its readiness rule and its over/under
                # -temp events, so it is injected here rather than duplicated
                # into subsystems/payload.yaml — a second copy is exactly the
                # defect this ruling removes.
                if name == "payload" and "fpa_envelope" not in cfg_dict:
                    tcs_cfg = self._subsys_configs.get("tcs")
                    env = getattr(tcs_cfg, "fpa_envelope", None)
                    if env is not None:
                        cfg_dict["fpa_envelope"] = (
                            env.model_dump() if hasattr(env, "model_dump")
                            else dict(env))
                model = create_model(model_name, cfg_dict)
                # Defect #23: give the model a back-reference to the engine so its
                # event-generation code (guarded by `if self._engine`) actually
                # runs and its events get delivered via _drain_model_events().
                model._engine = self
                # Deterministic per-model RNG stream (plan A-2), replacing the
                # module-global `random` the models used to draw from.
                model.rng = derive_rng(self.seed, f"model:{name}")
                self.subsystems[name] = model
                logger.info("Loaded subsystem model: %s (%s)", name, model_name)
            except Exception as e:
                logger.warning("Failed to load model %s for %s: %s", model_name, name, e)

        # LEGACY reproducibility shim (plan A-2). The OLD OBDH model
        # initialised its CUC clock from the wall clock in configure(), so two
        # same-seed runs started a second apart disagreed on param 0x0309
        # (obdh.obc_time); the engine re-pinned it to t0_epoch to compensate.
        #
        # TIME_ARCHITECTURE rule 5 says nothing outside the OBDH model may
        # write onboard time, and Part B gives the OBDH model its own clock
        # starting deterministically at CUC 0. This block is therefore
        # SKIPPED as soon as that model lands (detected by the new float
        # `obc_time_s` field) and can be deleted with the last legacy model.
        obdh = self.subsystems.get("obdh")
        _obdh_state = getattr(obdh, "_state", None)
        if (self._t0_epoch is not None and _obdh_state is not None
                and not hasattr(_obdh_state, "obc_time_s")
                and hasattr(_obdh_state, "obc_time_cuc")):
            from smo_common.protocol.ecss_packet import get_time_epoch
            t0_naive = self._t0_epoch.astimezone(timezone.utc).replace(tzinfo=None)
            _obdh_state.obc_time_cuc = int(
                (t0_naive - get_time_epoch()).total_seconds())

        # TM builder — two APIDs: bootloader and application.
        # At power-up we use the bootloader APID; on OBC app boot (phase 3→4) we
        # switch to the application APID.
        self._application_apid = int(self._mission_cfg.spacecraft_apid) & 0x7FF
        self._bootloader_apid = int(getattr(
            self._mission_cfg, 'bootloader_apid', 0x02)) & 0x7FF
        self.tm_builder = TMBuilder(
            apid=self._bootloader_apid if getattr(self._mission_cfg, 'start_in_bootloader', True)
                else self._application_apid,
            time_source=self._get_cuc_time,
        )

        # Initialize _override_passes before initial tick (used in parameter initialization)
        self._override_passes: bool = False

        # Initialize parameters: do a zero-dt tick of all subsystems to populate shared_params
        # This ensures parameters are available for API queries even before the main loop starts.
        # This is critical for the MCS to display parameters on startup.
        try:
            initial_orbit_state = self.orbit.propagate_to(self._sim_time)
            self.params[0x05FF] = 1 if self._override_passes else 0
            for name, model in self.subsystems.items():
                try:
                    model.tick(0.0, initial_orbit_state, self.params)
                except Exception as e:
                    logger.debug("Initial tick error for %s: %s", name, e)
        except Exception as e:
            logger.warning("Parameter initialization error: %s", e)

        # HK structures and timers
        self._hk_structures_raw = load_hk_structures(self.config_dir)
        self._hk_structures: dict[int, list[tuple]] = {}
        self._hk_intervals: dict[int, float] = {}
        self._hk_timers: dict[int, float] = {}
        self._hk_enabled: dict[int, bool] = {}
        # Default HK enable state is config-driven (defect #75). The operations
        # manual says HK generation is enabled *by command*; the engine used to
        # switch every SID on at startup, which made TC(3,5) HK_ENABLE a no-op
        # and skipped the commissioning step entirely. Default now: only the
        # essential SIDs — 1 (EPS platform vitals, needed to judge whether the
        # spacecraft is power-safe before anything else is switched on) and 11
        # (the APID 2 beacon, the only telemetry the bootloader produces).
        self._hk_default_enabled_sids = self._resolve_default_hk_sids()
        for hk in self._hk_structures_raw:
            params_list = [(p.param_id, p.pack_format, p.scale) for p in hk.parameters]
            self._hk_structures[hk.sid] = params_list
            self._hk_intervals[hk.sid] = hk.interval_s
            self._hk_timers[hk.sid] = 0.0
            self._hk_enabled[hk.sid] = hk.sid in self._hk_default_enabled_sids

        # FDIR state
        # sc_mode is DERIVED state recomputed each tick by _update_sc_mode().
        # It starts SAFE (the spacecraft boots in SAFE) and is driven by the
        # latched FDIR flags below plus spacecraft phase / payload mode.
        self.sc_mode = SC_SAFE
        self._fdir_safe_active = False       # an FDIR L>=2 (safe) rule is latched
        self._fdir_emergency_active = False  # an FDIR L>=3 (emergency) rule is latched
        self._fdir_enabled = self._fdir_cfg.enabled
        self._fdir_rules = self._fdir_cfg.rules
        self._fdir_triggered: dict[str, bool] = {}

        # Advanced FDIR: Fault Propagation, Load Shedding, Recovery, Procedures
        from smo_simulator.fdir import (
            FaultPropagator, LoadSheddingManager, ProcedureExecutor,
            FaultPropagationRule, LOAD_SHED_STAGE_NORMAL
        )
        self._fault_propagator = FaultPropagator()
        self._load_shedding = LoadSheddingManager()
        self._procedure_executor = ProcedureExecutor()
        self._current_load_shed_stage = LOAD_SHED_STAGE_NORMAL
        # The FDIR module's OWN accumulator of sim seconds ticked through
        # _tick_fdir_advanced: the time base for load-shed dwell, cascade
        # due-times and recovery back-off. It is NOT MET (it only starts
        # accumulating when advanced FDIR first ticks, and it stops whenever
        # that tick is skipped) — it was exported as `met_s` for a while
        # because the two happened to agree. MET is `self.met_s`.
        self._fdir_elapsed_s = 0.0
        # Fault-cascade / recovery runtime (WP-5). Detection had no call site
        # at all before Phase G, so none of this existed.
        self._recovery_max_attempts = 3
        self._recovery_initial_level = 1
        self._recovery_level_delays: dict[int, float] = {}
        self._recovery_actions: dict[str, dict] = {}
        self._cascade_callbacks: dict[str, Any] = {}
        self._pending_cascades: list[dict] = []   # scheduled by delay_s
        self._fault_persist: dict[str, float] = {}
        # Edge detector for SAFE_MODE_ENTRY/EXIT. Seeded from the initial mode
        # so power-up (which starts SAFE, uncommissioned) does not fire a
        # spurious 0x0F0A on the first tick — that event drives S19 rule 8005
        # PROP_DISARM, so a false entry has real consequences.
        self._prev_sc_safe = True
        self._prev_eps_mode: int | None = None
        self._param_catalogue_ids()   # warm the name->id map off the tick thread
        self._load_fault_propagation_config()

        # Transition tracking
        self._prev_in_contact = False
        self._prev_in_eclipse = False
        self._in_contact = False  # current contact state for TM gating
        self._active_gs: str | None = None   # ground station currently in view
        self._prev_active_gs: str | None = None  # station of the last contact (for LOS event)

        # TC Scheduler (S11)
        self._tc_scheduler = TCScheduler()

        # On-Board Control Procedures (S18 OBCP). Boot-time procedures are
        # pre-loaded from configs/<mission>/obcp/*.yaml (like the S19 rules)
        # so students can activate them immediately.
        from smo_simulator.obcp import ObcpManager
        self._obcp_manager = ObcpManager(self)
        try:
            self._obcp_manager.load_from_dir(self.config_dir / "obcp")
        except Exception as e:  # noqa: BLE001 — bad OBCP config must not block boot
            logger.warning("OBCP boot-load failed: %s", e)

        # Delayed-TM (S15) dump archive directory. The resolution order
        # (SMO_DUMP_DIR > mission.yaml dump_dir > workspace/dumps) lives in
        # smo_common.dump_store so the writer here and every reader — the MCS
        # dump API, the simulator's own dump API, tools/delayed_tm_viewer.py —
        # agree on one location (defect #90).
        from smo_common.dump_store import resolve_dump_dir as _resolve_dump_dir
        self._dump_dir = _resolve_dump_dir(self._mission_cfg)
        logger.info("Delayed-TM dump archive dir: %s", self._dump_dir)

        # Onboard TM Storage (S15)
        # S15 stores are stamped with ONBOARD time, so a store's oldest/newest
        # timestamps are on the same clock as the CUC headers of the packets
        # inside it (spec Part C.5). Every call site that omits an explicit
        # timestamp — including service_dispatch's science-record path — picks
        # this up.
        self._tm_storage = OnboardTMStorage(time_source=self._get_cuc_time)
        # S15 dump pacing: pending packets released according to TTC data rate.
        # Each item: (pkt_bytes, store_id)
        self._dump_pending: list[tuple[bytes, int]] = []
        self._dump_byte_budget: float = 0.0
        # Stores currently being dumped (auto-cleared on completion)
        self._dump_active_stores: set[int] = set()

        # Real-time TM pacing (defect #74): backlog + byte budget, mirroring
        # the S15 dump pacer but for HK / event / verification telemetry.
        self._rt_pending: list[bytes] = []
        self._rt_byte_budget: float = 0.0
        self._rt_backlog_max: int = int(
            getattr(self._sim_cfg.ttc, 'rt_tm_backlog_max', 2000))
        self._force_rt_pacing: bool = False

        # Subsystem event edge-detection. There is no `_prev_aocs_mode`: the
        # AOCS model owns the 0x0200 mode-change event (see
        # `_check_subsystem_events`).
        self._prev_payload_mode: Optional[int] = None
        self._prev_obdh_mode: Optional[int] = None
        self._event_flags: dict[str, bool] = {}

        # Spacecraft phase state machine
        # Default NOMINAL (6) so existing tests that instantiate without the
        # eosat1 mission config behave as before. If mission config requests
        # `start_in_bootloader`, we drop to phase 3 below after storage init.
        self._spacecraft_phase = 6   # 0=PRE_SEP,1=SEP_TIMER,2=INIT_PWR,3=BOOT,4=LEOP,5=COMM,6=NOM
        self._sep_timer = 0.0        # separation timer countdown (seconds)
        # simulation.yaml phases.separation_timer_s (default 1800 s = 30 min)
        self._sep_timer_duration = float(self._sim_cfg.phases.separation_timer_s)

        # Auto-TX hold-down. On reception of any accepted TC, the platform
        # autonomously powers the TTC TX line on for AUTO_TX_HOLD_S seconds
        # so the operator gets a downlink for at least one ground pass after
        # the first valid uplink. Each new accepted TC re-arms the timer.
        # This mirrors a common LEOP autonomy: "if I just heard a good
        # command, the ground is talking to me — go ahead and reply".
        # simulation.yaml ttc.auto_tx_hold_s (default 900 s; EOSAT-1 uses 300 s
        # so the hold does not defeat the ttc_tx load-shed stage during passes).
        self._auto_tx_hold_s: float = float(self._sim_cfg.ttc.auto_tx_hold_s)
        self._auto_tx_remaining: float = 0.0
        # Instructor speed clamp (simulation.yaml speed.max)
        self._speed_max: float = float(self._sim_cfg.speed.max)
        # Phase D: latched once per burn when the engine clamps acceleration
        # to prop.max_speed_during_burn (event 0x0811 fires on the edge).
        self._prop_speed_clamped: bool = False

        # S12 monitoring: edge-triggered violation tracking.  Only the
        # transition into violation (OK → violated) emits an S5 event.
        self._s12_violation_active: set[int] = set()
        # mon_id -> last violation record, so the clear transition (TM 12,10)
        # can name the parameter and limits it belongs to.
        self._s12_violation_records: dict[int, dict] = {}

        # Failure manager
        from smo_simulator.failure_manager import FailureManager
        self._failure_manager = FailureManager(
            inject_fn=self._handle_failure_inject,
            clear_fn=self._handle_failure_clear,
            config=self._sim_cfg.failures,   # simulation.yaml failures: block
            rng=derive_rng(self.seed, "failure_manager"),  # plan A-2
        )

        # Named state breakpoints (snapshot store for instructor save/load).
        # Maps breakpoint name -> full state dict captured by BreakpointManager.
        # Persisted to disk (workspace/breakpoints/) so they survive a sim restart.
        self._breakpoints: dict[str, dict] = {}
        self._load_persisted_breakpoints()

        # Scenario engine (defect #9): instantiate and load YAML scenarios so the
        # instructor UI's scenario list / start / stop are actually functional.
        # Previously this was never created, so /api/scenarios always returned []
        # and start_scenario/stop_scenario were silently dropped.
        from smo_simulator.scenario_engine import ScenarioEngine
        self._scenario_engine = ScenarioEngine(
            failure_manager=self._failure_manager, engine=self,
            defaults=self._sim_cfg.scenario_defaults,
        )
        try:
            self._scenario_engine.load_scenarios_from_dir(self.config_dir / "scenarios")
        except Exception as e:  # noqa: BLE001 — a bad scenario dir must not block startup
            logger.warning("Scenario load failed: %s", e)

        # Service dispatcher (persistent instance for S12/S19 state)
        from smo_simulator.service_dispatch import ServiceDispatcher
        self._dispatcher = ServiceDispatcher(self)

        # S6 on-board memory: config-declared map + sparse patchable image.
        # One declaration (configs/<mission>/memory/memory_map.yaml) replaces
        # the three disagreeing hard-coded maps (defect #71).
        from smo_simulator.memory_map import MemoryImage, MemoryMap
        self._memory_map = MemoryMap.load(self.config_dir)
        self._memory = MemoryImage(self._memory_map, self)
        self._dispatcher._memory = self._memory

        # Scenario schema problems found at load time become real telemetry
        # (SCENARIO_CONFIG_INVALID). Deferred to here because _emit_event needs
        # the TM builder and dispatcher, which are created after scenario load.
        try:
            self._scenario_engine.emit_load_issues()
        except Exception as e:  # noqa: BLE001 — never block startup
            logger.debug("Scenario load-issue emission failed: %s", e)

        # Load S12/S19 configurations at startup
        self._load_monitoring_configs()

        # Wire FDIR callbacks
        self._fdir_callbacks: dict[str, Any] = {}
        self._wire_fdir()
        self._wire_load_shedding()
        self._wire_fault_cascades()
        self._wire_procedure_executor()

        # If mission config requests bootloader boot-up, put the spacecraft
        # into pure beacon mode right now: phase 3, bootloader APID, only
        # SID 11 enabled, all TM stores empty & disabled, AOCS/payload/TCS
        # not ticking (handled by phase gate in main loop).
        if getattr(self._mission_cfg, 'start_in_bootloader', True):
            self._enter_bootloader_mode()

        # ── Stateful-component registry (plan A-4) ──
        # Every component holding mutable simulation state MUST be registered
        # here; the BreakpointManager serialises exactly this set (plus the
        # legacy top-level fields), and a save failure in ANY component fails
        # the whole save. tests/test_simulator/test_breakpoint_round_trip.py
        # asserts completeness: any engine-reachable object with a tick()
        # method must be registered or explicitly allowlisted with a reason.
        self._stateful_components: dict[str, Any] = {}
        self.register_stateful("engine", self)
        for name, model in self.subsystems.items():
            self.register_stateful(f"model:{name}", model)
        self.register_stateful("tm_storage", self._tm_storage)
        self.register_stateful("obcp", self._obcp_manager)
        self.register_stateful("memory", self._memory)
        self.register_stateful("tc_scheduler", self._tc_scheduler)
        self.register_stateful("failure_manager", self._failure_manager)
        self.register_stateful("scenario_engine", self._scenario_engine)
        self.register_stateful("dispatcher", self._dispatcher)
        self.register_stateful("load_shedding", self._load_shedding)
        self.register_stateful("fault_propagator", self._fault_propagator)
        self.register_stateful("procedure_executor", self._procedure_executor)

    def _resolve_default_hk_sids(self) -> set[int]:
        """SIDs that are enabled without a ground command (defect #75)."""
        hk_cfg = getattr(self._sim_cfg, 'housekeeping', None)
        all_sids = {hk.sid for hk in self._hk_structures_raw}
        if hk_cfg is None:
            return all_sids
        if getattr(hk_cfg, 'enable_all_by_default', False):
            return all_sids
        sids = {int(s) for s in getattr(hk_cfg, 'default_enabled_sids', []) or []}
        # The bootloader beacon SID is not optional — without it the spacecraft
        # is silent at power-up and cannot be commissioned at all.
        sids.add(BOOTLOADER_BEACON_SID)
        return sids & all_sids if all_sids else sids

    def _resolve_t0_epoch(self) -> Optional[datetime]:
        """Return the reference epoch to start the propagator at, or None.

        None means "use the wall clock" — either simulation.yaml asked for it
        (`epoch: wall`) or the orbit config declares no `t0_epoch`.
        """
        mode = str(getattr(self._sim_cfg, 'epoch', 't0') or 't0').strip().lower()
        if mode == 'wall':
            return None
        raw = getattr(self._orbit_cfg, 't0_epoch', None)
        if not raw:
            return None
        try:
            epoch = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        except ValueError:
            logger.warning("orbit.yaml t0_epoch %r is not ISO-8601 — "
                           "falling back to wall clock", raw)
            return None
        if epoch.tzinfo is None:
            epoch = epoch.replace(tzinfo=timezone.utc)
        return epoch.astimezone(timezone.utc)

    def _enter_bootloader_mode(self) -> None:
        """Configure the engine for bootloader/beacon operation.

        - Phase = 3 (BOOTLOADER_OPS), separation timer expired
        - Only SID 11 (Beacon) enabled in the HK scheduler
        - All TM stores cleared AND disabled (bootloader has no store service)
        - TM builder APID switched to bootloader_apid
        - OBDH sw_image forced to 0 (bootloader)
        - AOCS mode OFF; platform subsystems stay OFF via phase gating
        """
        self._spacecraft_phase = 3
        self._sep_timer = 0.0

        # HK gating: only beacon SID active
        for sid in self._hk_enabled:
            self._hk_enabled[sid] = (sid == BOOTLOADER_BEACON_SID)
        logger.info("Bootloader boot-up: HK restricted to SID %d (Beacon)",
                    BOOTLOADER_BEACON_SID)

        # TM builder: switch to bootloader APID
        try:
            self.tm_builder.set_apid(self._bootloader_apid)
        except Exception:
            pass

        # TM storage: clear and disable all stores — stores don't "exist"
        # in bootloader mode. They come online on transition to phase 4.
        if self._tm_storage is not None:
            for sid in list(self._tm_storage._stores.keys()):
                self._tm_storage.delete_store(sid)
                self._tm_storage.disable_store(sid)
            logger.info("Bootloader boot-up: all TM stores cleared and disabled")

        # OBDH → bootloader image
        obdh = self.subsystems.get("obdh")
        if obdh and hasattr(obdh, '_state'):
            obdh._state.sw_image = 0
            if hasattr(obdh._state, 'boot_app_pending'):
                obdh._state.boot_app_pending = False

        # AOCS off (no attitude control in bootloader). Sensors and
        # actuators are unpowered at boot-up — operators must explicitly
        # power them on via the commissioning procedure. Leaving STs, MTQs,
        # wheels, CSS etc. in their "on" dataclass defaults would generate
        # spurious alarms (e.g. ST sun-exclusion blinding) before the
        # spacecraft has been commanded through AOCS checkout.
        aocs = self.subsystems.get("aocs")
        if aocs and hasattr(aocs, '_state'):
            s = aocs._state
            s.mode = 0
            # Star trackers OFF (no power)
            s.st1_status = 0
            s.st2_status = 0
            s.st1_num_stars = 0
            s.st2_num_stars = 0
            s.st1_failed = False
            s.st2_failed = False
            s.st_valid = False
            s.st_selected = 1
            if hasattr(s, '_prev_st1_status'):
                s._prev_st1_status = 0
            if hasattr(s, '_prev_st2_status'):
                s._prev_st2_status = 0
            # CSS / magnetometer invalid until sensors are powered and healthy
            s.css_valid = False
            s.css_sun_x = 0.0
            s.css_sun_y = 0.0
            s.css_sun_z = 0.0
            s.mag_valid = False
            # Reaction wheels disabled
            if hasattr(s, 'rw_enabled'):
                s.rw_enabled = [False] * 4
            if hasattr(s, 'active_wheels'):
                s.active_wheels = [False] * 4
            if hasattr(s, 'rw_speed'):
                s.rw_speed = [0.0] * 4
            # Magnetorquers disabled
            s.mtq_enabled = False
            s.mtq_x_duty = 0.0
            s.mtq_y_duty = 0.0
            s.mtq_z_duty = 0.0
            # GPS no fix
            if hasattr(s, 'gps_fix'):
                s.gps_fix = 0
            if hasattr(s, 'gps_num_sats'):
                s.gps_num_sats = 0

        # TTC in beacon mode: PA off, antenna stowed, low-rate TM
        ttc = self.subsystems.get("ttc")
        if ttc and hasattr(ttc, '_state'):
            t = ttc._state
            t.pa_on = False
            t.beacon_mode = True
            t.antenna_deployed = False
            # SAFE tier (1 kbps, ex-BEACON) — the bootloader's only rate (defect #85).
            t.data_rate_mode = getattr(ttc, '_beacon_tier', 0)
            t.downlink_mode = False
            if hasattr(ttc, '_apply_tier'):
                # Apply immediately so the rate is right before the first tick.
                ttc._apply_tier(t, t.data_rate_mode)

        # Payload off
        payload = self.subsystems.get("payload")
        if payload and hasattr(payload, '_state'):
            payload._state.mode = 0

        # Phase parameter + sw_image parameter (kept in sync with OBDH state
        # so that downstream gates — HK emission, TC acceptance, UI — see the
        # bootloader state immediately, not on the next tick).
        self.params[0x0129] = 3
        self.params[0x0311] = 0

    def _exit_bootloader_mode(self) -> None:
        """Transition from bootloader to application software running.

        Called when phase advances from 3 → ≥4. Switches TM builder back to
        the application APID, re-enables all TM stores, and enables all HK
        SIDs so platform/payload telemetry starts flowing.
        """
        # TM builder → application APID
        try:
            self.tm_builder.set_apid(self._application_apid)
        except Exception:
            pass

        # Restore the *configured* default HK enable state — not "everything on"
        # (defect #75). The application has full telemetry access, but the
        # operator still has to command HK_ENABLE for the non-essential SIDs,
        # which is what the operations manual and the commissioning cards say.
        self._apply_default_hk_enable()

        # TM stores come online (empty but enabled)
        if self._tm_storage is not None:
            for sid in list(self._tm_storage._stores.keys()):
                self._tm_storage.enable_store(sid)

        # ── TM data-rate ladder (defect #85) ──
        # Leaving the bootloader leaves the SAFE (1 kbps) tier behind. The
        # application comes up on the configured SAFE tier (LOW, 16 kbps) —
        # enough for real-time HK plus stored-HK playback with the antenna
        # still stowed — and the operator commands NOMINAL once the antenna
        # is deployed. Nominal operations never default to the top tier.
        # While beacon mode is still latched the tick pins the tier to SAFE
        # anyway; once TTC_SET_BEACON_MODE(off) is sent the bootloader-only
        # tier is no longer permitted and the model steps down to this one.
        ttc = self.subsystems.get("ttc")
        if ttc is not None and hasattr(ttc, '_state') \
                and not ttc._state.beacon_mode:
            ttc._state.data_rate_mode = getattr(ttc, '_safe_tier', 1)
        logger.info(
            "Application booted: APID=0x%03X, default HK SIDs %s enabled, stores online",
            self._application_apid, sorted(self._hk_default_enabled_sids))

    def _apply_default_hk_enable(self) -> None:
        """Set every HK SID to its configured power-up enable state."""
        for sid in self._hk_enabled:
            self._hk_enabled[sid] = sid in self._hk_default_enabled_sids

    # ── Clocks (TIME_ARCHITECTURE Part C) ─────────────────────────────
    #
    # SIM TIME    self.sim_time  — absolute UTC of the simulated world; the
    #                              engine owns it and drives the propagator.
    # MET         self.met_s     — (sim_time - t0_epoch), the ONE definition.
    # ONBOARD     _get_cuc_time  — the OBDH clock, the single seam between the
    #                              simulator and everything the spacecraft
    #                              emits. Never sim time, never wall clock.

    @property
    def sim_time(self) -> datetime:
        """Absolute UTC of the simulated world (the one sim clock)."""
        return self._sim_time

    @property
    def met_s(self) -> float:
        """Mission Elapsed Time in seconds: ``sim_time - t0_epoch``.

        The single definition (spec Part C.2). Everything that wants MET —
        /api/state, the planner, models — takes it from here rather than
        deriving a second one. In `epoch: wall` mode (no t0_epoch) the
        reference is the sim time at engine construction.
        """
        return (self._sim_time - self._met_epoch).total_seconds()

    def _obdh_clock_seconds(self) -> float | None:
        """Onboard seconds since the mission epoch, from the OBDH model.

        Adapter over the OBDH clock (spec Part B, owned by the OBDH model).
        It reads, in order of preference:

        * ``obdh.get_obc_time_s()`` -> float      (Part B: THE accessor)
        * ``obdh._state.obc_time_s``  float       (Part B state, sub-second)
        * ``obdh._state.obc_time_cuc`` int        (legacy whole seconds)

        Returns None when there is no OBDH model at all (bare unit-test
        engines), which the callers treat as "clock at CUC 0" — a legitimate
        onboard time now that the OBC boots at J2000.
        """
        obdh = self.subsystems.get("obdh")
        if obdh is None:
            return None
        getter = getattr(obdh, "get_obc_time_s", None)
        if callable(getter):
            try:
                return float(getter())
            except Exception:  # noqa: BLE001 — never break packet emission
                logger.warning("OBDH clock read failed", exc_info=True)
        st = getattr(obdh, "_state", None)
        if st is None:
            return None
        for attr in ("obc_time_s", "obc_time_cuc"):
            val = getattr(st, attr, None)
            if val is not None:
                return float(val)
        return None

    def _get_cuc_time(self) -> float:
        """THE onboard time source (spec Part C.3).

        Onboard CUC seconds since the mission epoch, full precision — the
        TMBuilder time-source contract accepts a float and carries the
        fraction into the CUC fine field rather than discarding it.

        Handed to the TMBuilder, the S11 scheduler and the S15 stores, so
        every packet the spacecraft emits and every store timestamp carries
        the OBC's own clock — including its drift and any pre-sync offset.
        Never sim time, never wall clock.
        """
        return float(self._obdh_clock_seconds() or 0.0)

    def _get_cuc(self) -> CucTime:
        """The same onboard time as a :class:`CucTime` (coarse, fine)."""
        return CucTime.from_seconds(self._get_cuc_time())

    def _load_monitoring_configs(self) -> None:
        """Load S12 on-board monitoring and S19 event-action configs from YAML.

        Called during __init__ to populate the ServiceDispatcher with monitoring
        definitions and event-action rules before the simulation starts.
        """
        from smo_simulator.monitoring_loader import (
            load_s12_definitions, load_s19_rules,
            register_s12_definitions, register_s19_rules,
        )

        # Load S12 definitions
        s12_defs = load_s12_definitions(self.config_dir)
        register_s12_definitions(self._dispatcher, s12_defs)

        # Load S19 rules
        s19_rules = load_s19_rules(self.config_dir)
        register_s19_rules(self._dispatcher, s19_rules)

    def _wire_fdir(self) -> None:
        """Register FDIR action callbacks to subsystem methods."""
        eps = self.subsystems.get("eps")
        aocs = self.subsystems.get("aocs")
        tcs = self.subsystems.get("tcs")
        obdh = self.subsystems.get("obdh")
        payload = self.subsystems.get("payload")

        if payload:
            self._fdir_callbacks["payload_poweroff"] = lambda: payload.handle_command({"command": "set_mode", "mode": 0})
        if tcs:
            self._fdir_callbacks["heater_on_battery"] = lambda: tcs.handle_command({"command": "heater", "circuit": "battery", "on": True})
            self._fdir_callbacks["heater_off_battery"] = lambda: tcs.handle_command({"command": "heater", "circuit": "battery", "on": False})
        if aocs:
            # Phase C WP-9: AOCS safe mode = COARSE_SUN (mode 3), a sun-safe
            # power-positive hold — not DETUMBLE (mode 2), which is a rate-
            # damping mode with no pointing and was semantically wrong here.
            self._fdir_callbacks["safe_mode_aocs"] = lambda: aocs.handle_command({"command": "set_mode", "mode": 3})
            for i in range(4):
                self._fdir_callbacks[f"disable_rw{i+1}"] = (lambda idx=i: aocs.handle_command({"command": "disable_wheel", "wheel": idx}))
        if obdh:
            self._fdir_callbacks["safe_mode_obc"] = lambda: obdh.handle_command({"command": "set_mode", "mode": 1})
        if eps:
            # Defect #27: EPS has no "set_mode" command — its mode command is
            # "set_eps_mode". The old name silently returned success:False, so
            # FDIR-driven EPS safing never happened.
            self._fdir_callbacks["safe_mode_eps"] = lambda: eps.handle_command({"command": "set_eps_mode", "mode": 1})
        # sc_mode is now derived; the emergency callback latches a flag that
        # _update_sc_mode() reads. The flag is cleared when the triggering FDIR
        # rule un-triggers (see _tick_fdir).
        self._fdir_callbacks["spacecraft_emergency"] = lambda: setattr(self, '_fdir_emergency_active', True)

    def _load_fault_propagation_config(self) -> None:
        """Load fault propagation configuration from YAML."""
        import yaml
        propagation_file = self.config_dir / "fdir" / "fault_propagation.yaml"
        if not propagation_file.exists():
            logger.warning("Fault propagation config not found: %s", propagation_file)
            return

        try:
            with open(propagation_file) as f:
                config = yaml.safe_load(f) or {}

            # Register fault cascade rules
            for cascade_cfg in config.get("fault_cascades", []):
                rule = FaultPropagationRule(
                    fault_id=cascade_cfg.get("fault_id", ""),
                    description=cascade_cfg.get("description", ""),
                    primary_param=cascade_cfg.get("primary_param", ""),
                    threshold=cascade_cfg.get("threshold"),
                    cascades=cascade_cfg.get("cascades", []),
                    condition=cascade_cfg.get("condition", "<"),
                    flags=[int(f) for f in cascade_cfg.get("flags", []) or []],
                    persist_s=float(cascade_cfg.get("persist_s", 0.0) or 0.0),
                    recovery=str(cascade_cfg.get("recovery", "") or ""),
                    requires=dict(cascade_cfg.get("requires", {}) or {}),
                )
                self._fault_propagator.register_rule(rule)

            # Recovery escalation (WP-5). These four entries were parsed by
            # nobody; they are what turns a detected fault into a *started*
            # recovery procedure.
            esc = config.get("recovery_escalation", {}) or {}
            self._recovery_max_attempts = int(esc.get("max_attempts", 3))
            self._recovery_initial_level = int(esc.get("initial_level", 1))
            self._recovery_level_delays = {}
            for lvl_key, lvl_cfg in (esc.get("escalation_delays", {}) or {}).items():
                try:
                    self._recovery_level_delays[int(str(lvl_key).split("_")[-1])] = \
                        float((lvl_cfg or {}).get("delay_s", 0.0))
                except (ValueError, AttributeError, TypeError):
                    continue
            self._recovery_actions = esc.get("recovery_actions", {}) or {}

            # Register load shedding stages. The SoC entry thresholds are owned
            # by simulation.yaml (eps.load_shed.soc_stage_thresholds_pct); the
            # per-stage `soc_threshold` values here are cross-checked so the two
            # files cannot silently diverge (defect #51).
            shed_thresholds = list(
                self._sim_cfg.eps.load_shed.soc_stage_thresholds_pct)
            self._load_shedding.set_soc_stage_thresholds(shed_thresholds)
            for stage_key, stage_cfg in config.get("load_shedding_stages", {}).items():
                stage_num = int(stage_key.split("_")[-1])
                self._load_shedding.register_stage_config(stage_num, stage_cfg)
                if 1 <= stage_num <= len(shed_thresholds):
                    yaml_thr = stage_cfg.get("soc_threshold")
                    if (yaml_thr is not None
                            and abs(float(yaml_thr) - shed_thresholds[stage_num - 1]) > 1e-6):
                        logger.warning(
                            "Load-shed stage %d soc_threshold in fault_propagation.yaml "
                            "(%.1f%%) diverges from simulation.yaml (%.1f%%) — "
                            "simulation.yaml wins",
                            stage_num, float(yaml_thr), shed_thresholds[stage_num - 1])

            logger.info("Loaded fault propagation configuration from %s", propagation_file)
        except Exception as e:
            logger.warning("Failed to load fault propagation config: %s", e)

    def _wire_load_shedding(self) -> None:
        """Register load shedding callbacks to subsystems."""
        payload = self.subsystems.get("payload")
        aocs = self.subsystems.get("aocs")
        tcs = self.subsystems.get("tcs")
        ttc = self.subsystems.get("ttc")

        if payload:
            self._load_shedding.register_callback(
                "payload_mode",
                lambda val: payload.handle_command({"command": "set_mode", "mode": int(val)})
            )

        if aocs:
            self._load_shedding.register_callback(
                "aocs_mode",
                lambda val: aocs.handle_command({"command": "set_mode", "mode": int(val)})
            )

        if tcs:
            # Phase G: TCS has no "heater_duty" command (same class as defect
            # #27 for EPS/TTC) — handle_command fell through and returned
            # success:False, so load shedding never reduced heater duty. The
            # real command is `set_heater_duty_limit`, per circuit, in percent.
            self._load_shedding.register_callback(
                "tcs_heater_duty",
                lambda val: self._tcs_duty_limit_all(float(val) * 100.0)
            )

        if ttc:
            # Defect #27: TTC has no "power_level" command — its power command is
            # "set_tx_power" and it reads the "power_w" key. The old name/key was
            # silently ignored, so load-shedding never reduced TTC TX power.
            self._load_shedding.register_callback(
                "ttc_power_level",
                lambda val: ttc.handle_command({"command": "set_tx_power", "power_w": float(val)})
            )

    def _wire_fault_cascades(self) -> None:
        """Bind every ``(target_subsystem, action)`` pair declared in
        ``fault_propagation.yaml`` to a real subsystem command (WP-5).

        Nine cascade rules were registered and none of their actions had an
        implementation, because ``detect_fault()`` had no call site — the
        actions were never reached, so nothing ever complained.  The
        contract test ``test_fault_propagation.py`` now asserts this map
        covers every declared pair, so a new cascade action cannot be added
        to the config without an implementation.
        """
        eps = self.subsystems.get("eps")
        aocs = self.subsystems.get("aocs")
        tcs = self.subsystems.get("tcs")
        ttc = self.subsystems.get("ttc")
        payload = self.subsystems.get("payload")
        cb = self._cascade_callbacks

        if aocs:
            # Same sun-safe hold Phase C made the FDIR safe mode (mode 3
            # COARSE_SUN); mode 2 is DETUMBLE and is not a safe mode.
            cb["aocs:enter_safe_mode"] = lambda: aocs.handle_command(
                {"command": "set_mode", "mode": 3})
        if payload:
            cb["payload:power_off"] = lambda: payload.handle_command(
                {"command": "set_mode", "mode": 0})
            cb["payload:standby"] = lambda: payload.handle_command(
                {"command": "set_mode", "mode": 1})
            cb["payload:reduce_duty_cycle"] = lambda: payload.handle_command(
                {"command": "set_mode", "mode": 1})
        if tcs:
            # TCS has no "heater_duty" command — the duty control is
            # `set_heater_duty_limit` with a per-circuit percentage. (The
            # load-shedding wiring still uses the non-existent name; see
            # `_wire_load_shedding`, fixed there too.)
            cb["tcs:heater_standby"] = lambda: self._tcs_duty_limit_all(30.0)
            cb["tcs:heater_emergency_off"] = lambda: self._tcs_duty_limit_all(0.0)
        if ttc:
            cb["ttc:reduce_tx_power"] = lambda: ttc.handle_command(
                {"command": "set_tx_power", "power_w": 1.0})
            cb["ttc:standby"] = lambda: ttc.handle_command(
                {"command": "set_tx_power", "power_w": 0.5})
            # Drop one rung on the rate ladder rather than jumping to the
            # bottom: the lesson is that FDIR demotes the link, and the
            # console shows active-vs-commanded tier (defect #86).
            cb["ttc:reduce_data_rate"] = self._cascade_reduce_data_rate
        if eps:
            cb["eps:enter_safe_mode"] = lambda: eps.handle_command(
                {"command": "set_eps_mode", "mode": 1})
            cb["eps:reduce_charge_rate"] = lambda: eps.handle_command(
                {"command": "set_charge_rate", "rate_a": 0.5})
            cb["eps:reduce_power_to_aocs"] = lambda: eps.handle_command(
                {"command": "power_line_off", "line_name": "aocs_wheels"})

    def _tcs_duty_limit_all(self, duty_pct: float) -> None:
        tcs = self.subsystems.get("tcs")
        if tcs is None:
            return
        for circuit in (0, 1, 2):   # battery, obc, thruster
            tcs.handle_command({"command": "set_heater_duty_limit",
                                "circuit": circuit,
                                "duty_limit_pct": duty_pct})

    def _cascade_reduce_data_rate(self) -> None:
        ttc = self.subsystems.get("ttc")
        if ttc is None or not hasattr(ttc, "_state"):
            return
        cur = int(getattr(ttc._state, "data_rate_mode", 1))
        ttc.handle_command({"command": "set_data_rate_mode",
                            "mode": max(0, cur - 1)})

    def _wire_procedure_executor(self) -> None:
        """Register procedure commands and callbacks."""
        from smo_simulator.fdir import ProcedureExecutor
        import yaml

        # Register procedure event callback to emit S5 events
        self._procedure_executor.register_event_callback(self._emit_event)

        # Register common procedure commands (S8 function IDs)
        dispatcher = self._dispatcher

        def execute_s8_command(params):
            """Execute S8 command from procedure step."""
            service = 8
            subtype = 1
            func_id = params.get("func_id", 0)
            data = params.get("data", [])
            # Convert data list to bytes if needed
            if isinstance(data, list):
                data = bytes(data)
            elif isinstance(data, str):
                data = data.encode()
            elif not isinstance(data, bytes):
                data = b""
            func_data = bytes([func_id]) + data
            responses = dispatcher.dispatch(service, subtype, func_data)
            for resp in responses:
                self._enqueue_tm(resp)

        self._procedure_executor.register_command_callback("s8_command", execute_s8_command)

        # Load procedures from YAML files
        procedures_dir = self.config_dir / "fdir" / "procedures"
        if procedures_dir.exists():
            try:
                for proc_file in sorted(procedures_dir.glob("*.yaml")):
                    try:
                        with open(proc_file, 'r') as f:
                            proc_config = yaml.safe_load(f) or {}

                        proc_id = proc_config.get("procedure_id")
                        if not proc_id:
                            logger.warning("Procedure file %s missing procedure_id", proc_file.name)
                            continue

                        # Normalize steps: convert data lists to bytes for execution
                        steps = proc_config.get("steps", [])
                        for step in steps:
                            if "params" in step and "data" in step["params"]:
                                data = step["params"]["data"]
                                if isinstance(data, list):
                                    step["params"]["data"] = data  # Keep as list for YAML compatibility

                        self._procedure_executor.register_procedure(proc_id, proc_config)
                        logger.info("Loaded procedure: %s from %s", proc_id, proc_file.name)
                    except Exception as e:
                        logger.warning("Failed to load procedure from %s: %s", proc_file.name, e)
            except Exception as e:
                logger.warning("Failed to scan procedures directory: %s", e)

        # Register default safe mode entry procedure if not loaded from file
        if "safe_mode_entry" not in self._procedure_executor._procedures:
            safe_mode_proc = {
                "proc_id": "safe_mode_entry",
                "steps": [
                    {"command": "s8_command", "params": {"func_id": 0, "data": [0x02]}, "delay_s": 0.0},
                ]
            }
            self._procedure_executor.register_procedure("safe_mode_entry", safe_mode_proc)

    def _tick_fdir_advanced(self, dt: float) -> None:
        """Advanced FDIR tick: fault propagation, load shedding, recovery.

        Phase G (WP-5). Before this, ``FaultPropagator.detect_fault`` and
        ``ProcedureExecutor.start_procedure`` had zero call sites, so the nine
        configured cascades never cascaded, the ten recovery procedures never
        ran, and seven of the eight FDIR events in the catalogue could not be
        emitted. Only load shedding was live.
        """
        self._fdir_elapsed_s += dt
        now = self._fdir_elapsed_s

        # Check load shedding based on battery SoC
        eps = self.subsystems.get("eps")
        if eps and hasattr(eps, '_state'):
            # Defect #49: this read `eps._state.soc`, an attribute that does not
            # exist on EPSState — so the default 100.0 was used on every tick and
            # the configured SoC load-shed stages could never trigger. The real
            # attribute is `bat_soc_pct`.
            soc = getattr(eps._state, 'bat_soc_pct', 100.0)
            prev_stage = self._current_load_shed_stage
            new_stage = self._load_shedding.update_stage(soc, now)
            if new_stage is not None:
                self._current_load_shed_stage = new_stage
                self._load_shedding.execute_stage(new_stage)
                if new_stage > prev_stage:
                    self._emit_event({
                        'event_id': 0x0F05,  # LOAD_SHED_ACTIVATED
                        'severity': 2,
                        'description': (f"Load shedding activated: "
                                        f"{self._load_shedding.stage_name(new_stage)}"),
                    })
                else:
                    # Recovering up the ladder is an event in its own right —
                    # 0x0F06 was in the catalogue and emitted by nothing.
                    self._emit_event({
                        'event_id': 0x0F06,  # LOAD_SHED_DEACTIVATED
                        'severity': 1,
                        'description': (f"Load shedding relaxed to "
                                        f"{self._load_shedding.stage_name(new_stage)}"),
                    })

        # Fault detection + cascading.
        #
        # Armed only once the spacecraft is commissioned, which is how a real
        # FDIR is configured: during LEOP the reaction wheels are unpowered, so
        # `aocs.wheels_enabled` is legitimately 0 and the
        # `aocs_wheel_failure_dual` rule would declare a double wheel failure
        # on the first tick and cut AOCS bus power — a deadlock the operator
        # cannot command their way out of. Likewise `aocs_safe_mode_entry`
        # (mode == COARSE_SUN) is the *expected* state through commissioning.
        # Load shedding above stays armed in every phase: it is battery
        # protection, not fault reasoning.
        if self._spacecraft_phase >= SC_PHASE_NOMINAL:
            self._tick_fault_detection(dt, now)
            self._tick_pending_cascades(now)
            self._tick_recovery(now)
        elif self._fault_propagator.active_faults() or self._pending_cascades:
            # Dropping back below NOMINAL (e.g. an instructor rewind) disarms
            # the model rather than leaving stale faults latched.
            for fid in list(self._fault_propagator.active_faults()):
                self._fault_propagator.clear_fault(fid)
            self._pending_cascades.clear()
            self._fault_persist.clear()

        # Tick procedure executor
        self._procedure_executor.tick_procedures(now)
        self._publish_fdir_parameters()

    # ─── Fault detection / cascade / recovery (WP-5) ──────────────────

    def _tick_fault_detection(self, dt: float, now: float) -> None:
        """Evaluate every fault-cascade rule against live telemetry."""
        for rule in self._fault_propagator.rules():
            if not self._fault_precondition_met(rule):
                # Not judgeable right now (e.g. the subsystem is unpowered).
                # Clear any latched fault so the model does not hold a verdict
                # it can no longer justify.
                if rule.fault_id in self._fault_propagator.active_faults():
                    self._fault_propagator.clear_fault(rule.fault_id)
                self._fault_persist.pop(rule.fault_id, None)
                continue
            pid = self._resolve_param_name(rule.primary_param)
            if pid is None:
                continue
            raw = self.params.get(pid)
            if raw is None:
                continue          # parameter not produced — nothing to judge
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue

            present = rule.evaluate(value)
            # Persistence: the same filter Phase C added to the basic FDIR
            # rules, so a single noisy sample cannot cascade a spacecraft.
            if rule.persist_s > 0.0:
                acc = self._fault_persist.get(rule.fault_id, 0.0)
                acc = acc + dt if present else 0.0
                self._fault_persist[rule.fault_id] = acc
                if present and acc < rule.persist_s:
                    present = False

            already = rule.fault_id in self._fault_propagator.active_faults()
            if present and not already:
                cascades = self._fault_propagator.detect_fault(rule.fault_id, now)
                self._emit_event({
                    'event_id': 0x0F00,  # FDIR_FAULT_DETECTED
                    'severity': 3,
                    'description': (f"FDIR fault {rule.fault_id}: "
                                    f"{rule.primary_param}={value:.3f} "
                                    f"{rule.condition} {rule.threshold}"),
                })
                for c in cascades:
                    c["due"] = now + float(c.get("delay_s", 0.0) or 0.0)
                    self._pending_cascades.append(c)
                if rule.recovery:
                    self._start_recovery(rule, now)
            elif not present and already:
                self._fault_propagator.clear_fault(rule.fault_id)
                # Drop any cascade for this fault that has not fired yet — the
                # condition it was a response to no longer holds.
                self._pending_cascades = [
                    c for c in self._pending_cascades
                    if c.get("fault_id") != rule.fault_id
                ]
                self._complete_recovery(rule.fault_id, now)

    def _fault_precondition_met(self, rule) -> bool:
        """Is this rule's `requires:` precondition satisfied?"""
        req = getattr(rule, "requires", None)
        if not req:
            return True
        name = req.get("param")
        if not name:
            return True
        pid = self._resolve_param_name(str(name))
        if pid is None:
            return True
        raw = self.params.get(pid)
        if raw is None:
            return False
        try:
            return float(raw) >= float(req.get("min", 1.0))
        except (TypeError, ValueError):
            return False

    def _tick_pending_cascades(self, now: float) -> None:
        """Apply cascades whose declared ``delay_s`` has elapsed."""
        if not self._pending_cascades:
            return
        due = [c for c in self._pending_cascades if now >= c.get("due", 0.0)]
        if not due:
            return
        self._pending_cascades = [c for c in self._pending_cascades
                                  if now < c.get("due", 0.0)]
        for c in due:
            key = f"{c['target_subsystem']}:{c['action']}"
            cb = self._cascade_callbacks.get(key)
            if cb is None:
                logger.warning("No cascade implementation for %s (fault %s)",
                               key, c.get("fault_id"))
                continue
            try:
                cb()
            except Exception as e:  # noqa: BLE001
                logger.warning("Cascade %s failed: %s", key, e)
                continue
            self._emit_event({
                'event_id': 0x0F00,  # FDIR_FAULT_DETECTED (cascade applied)
                'severity': 2,
                'description': (f"Cascade {c['fault_id']} -> "
                                f"{c['target_subsystem']}.{c['action']}"),
            })

    def _start_recovery(self, rule, now: float) -> None:
        """Begin (or restart) the escalating recovery for a fault."""
        group = self._recovery_actions.get(rule.recovery)
        if not group:
            return
        rs = self._fault_propagator.create_recovery_state(
            rule.fault_id, rule.recovery, now,
            max_attempts=self._recovery_max_attempts)
        rs.group = rule.recovery
        rs.level = 0
        self._enter_recovery_level(rs, self._recovery_initial_level, now,
                                   escalated=False)

    def _enter_recovery_level(self, rs, level: int, now: float,
                              escalated: bool) -> None:
        group = self._recovery_actions.get(rs.group) or {}
        action_cfg = group.get(f"level_{level}")
        if not action_cfg:
            return
        rs.level = level
        rs.level_started = now
        rs.state = "RUNNING"
        rs.recovery_attempts += 1
        rs.last_attempt_time = now
        # The config names a procedure *file*; the executor is keyed by the
        # `procedure_id` inside it, which is the stem in every shipped file.
        proc_ref = str(action_cfg.get("procedure", ""))
        proc_id = proc_ref[:-5] if proc_ref.endswith(".yaml") else proc_ref
        rs.procedure = proc_id
        exec_id = self._procedure_executor.start_procedure(proc_id, now)
        rs.exec_id = exec_id or ""
        if escalated:
            self._emit_event({
                'event_id': 0x0F04,  # FDIR_LEVEL_ESCALATION
                'severity': 4,
                'description': (f"FDIR escalation L{level} for {rs.fault_id}: "
                                f"{action_cfg.get('action', '')}"),
            })
        self._emit_event({
            'event_id': 0x0F01,  # FDIR_RECOVERY_STARTED
            'severity': 3,
            'description': (f"Recovery L{level} {proc_id} for {rs.fault_id}"
                            + ("" if exec_id else " (procedure not registered)")),
        })
        if exec_id is None:
            rs.state = "FAILED"
            self._emit_event({
                'event_id': 0x0F03,  # FDIR_RECOVERY_FAILED
                'severity': 4,
                'description': (f"Recovery L{level} for {rs.fault_id} could not "
                                f"start: unknown procedure {proc_id}"),
            })

    def _tick_recovery(self, now: float) -> None:
        """Escalate recoveries whose level delay has expired."""
        for fault_id, rs in self._fault_propagator.recovery_states().items():
            if rs.state in ("COMPLETE", "FAILED"):
                continue
            if fault_id not in self._fault_propagator.active_faults():
                continue
            delay = self._recovery_level_delays.get(rs.level, 0.0)
            if delay <= 0.0 or now - rs.level_started < delay:
                continue
            next_level = rs.level + 1
            group = self._recovery_actions.get(rs.group) or {}
            if f"level_{next_level}" in group and \
                    rs.recovery_attempts < rs.max_attempts:
                self._emit_event({
                    'event_id': 0x0F03,  # FDIR_RECOVERY_FAILED (this level)
                    'severity': 3,
                    'description': (f"Recovery L{rs.level} for {fault_id} did not "
                                    f"clear the fault; escalating"),
                })
                self._enter_recovery_level(rs, next_level, now, escalated=True)
            else:
                rs.state = "FAILED"
                self._emit_event({
                    'event_id': 0x0F03,  # FDIR_RECOVERY_FAILED (terminal)
                    'severity': 4,
                    'description': (f"Recovery exhausted for {fault_id} after "
                                    f"L{rs.level}"),
                })

    def _complete_recovery(self, fault_id: str, now: float) -> None:
        rs = self._fault_propagator.get_recovery_state(fault_id)
        if rs is None or rs.state in ("COMPLETE",):
            return
        rs.state = "COMPLETE"
        self._emit_event({
            'event_id': 0x0F02,  # FDIR_RECOVERY_COMPLETE
            'severity': 1,
            'description': (f"Fault {fault_id} cleared"
                            + (f" after recovery L{rs.level}" if rs.level else "")),
        })

    def _check_safe_mode_events(self) -> None:
        """Emit SAFE_MODE_ENTRY/EXIT and EPS_SAFE_MODE_ENTRY (WP-5).

        0x0F0A, 0x0F0B and 0x0111 were in the event catalogue and raised by
        nothing, so entering safe mode — the single most important thing the
        spacecraft can do autonomously — produced no event on the ground.
        """
        safe = (self.sc_mode == SC_SAFE)
        if safe != self._prev_sc_safe:
            self._prev_sc_safe = safe
            self._emit_event({
                'event_id': 0x0F0A if safe else 0x0F0B,
                'severity': 3 if safe else 1,
                'description': ("Spacecraft entered SAFE mode"
                                if safe else "Spacecraft exited SAFE mode"),
            })
        eps = self.subsystems.get("eps")
        if eps is not None and hasattr(eps, '_state'):
            mode = int(getattr(eps._state, 'eps_mode', 0) or 0)
            if self._prev_eps_mode is None:
                self._prev_eps_mode = mode
            elif mode != self._prev_eps_mode:
                if mode != 0:
                    self._emit_event({
                        'event_id': 0x0111,  # EPS_SAFE_MODE_ENTRY
                        'severity': 2,
                        'description': f"EPS entered contingency mode {mode}",
                    })
                self._prev_eps_mode = mode

    def _publish_fdir_parameters(self) -> None:
        """Populate fdir.* and proc.* telemetry (0x0700-3, 0x0900-3).

        These eight parameters were defined in the catalogue and written by
        nothing; the FDIR window's "what is the spacecraft doing about it"
        half had no data source at all.
        """
        faults = self._fault_propagator.active_faults()
        recoveries = self._fault_propagator.recovery_states().values()
        running = [r for r in recoveries if r.state == "RUNNING"]
        level = 0
        if self._fdir_emergency_active:
            level = 3
        elif self._fdir_safe_active or running:
            level = 2
        elif faults:
            level = 1
        self.params[0x0700] = level
        self.params[0x0701] = len(faults)
        self.params[0x0702] = sum(1 for r in recoveries if r.state == "FAILED")
        self.params[0x0703] = len(running)

        active = self._procedure_executor.get_active_procedures()
        if active:
            proc = active[0]
            self.params[0x0900] = round(proc["progress_pct"], 1)
            self.params[0x0901] = proc["step"]
            self.params[0x0902] = proc["total_steps"]
            self.params[0x0903] = 1  # running
        else:
            self.params[0x0900] = 0.0
            self.params[0x0901] = 0
            self.params[0x0902] = 0
            self.params[0x0903] = 0

        # sat.mode (0x0001) is NOT written here any more — see
        # _update_sc_mode(). It used to be derived from the FDIR latches
        # alone, ignoring the phase gate that _update_sc_mode() applies, so
        # the spacecraft telemetered sat.mode = NOMINAL while the console
        # header (fed by sc_mode) said SAFE, all the way through LEOP. Two
        # things called "spacecraft mode", disagreeing. There is now one
        # derivation and 0x0001 is its telemetered copy.

        # ttc.active_gs (0x0523) — 1-based index into orbit.yaml's station
        # list, 0 when out of contact. Same defect: catalogued, unwritten,
        # and the SA header's "which station" now has a telemetry source
        # rather than an instructor-console-only fact.
        gs_name = self._active_gs
        idx = 0
        if gs_name:
            for i, gs in enumerate(self._orbit_cfg.ground_stations, start=1):
                if gs.name == gs_name:
                    idx = i
                    break
        self.params[0x0523] = float(idx)

    # ------------------------------------------------------------------
    # Spacecraft phase state machine
    # ------------------------------------------------------------------

    def apply_phase_change(self, new_phase: int) -> int:
        """Move the spacecraft to ``new_phase`` and bring the world with it.

        Returns the phase we came from. This is the single place a phase is
        set out-of-band — by the instructor console (``set_phase``), by a
        scenario, and by the ground telecommand SYS_SET_MISSION_PHASE (S8.1
        func 120). Each caller raises its own S5 event; the mechanics of HK
        SID gating, TM APID, on-board stores, the OBDH software image and the
        separation timer live here so they cannot diverge.

        Guards belong to the CALLER: the telecommand refuses pre-separation
        phases, bootloader-to-application jumps and unforced backwards
        transitions (see ``ServiceDispatcher._route_system_cmd``), while the
        instructor console is deliberately allowed to set anything — it is
        the training environment, not the spacecraft.
        """
        new_phase = int(new_phase)
        old_phase = int(self._spacecraft_phase)
        self._spacecraft_phase = new_phase
        if new_phase <= 3:
            # Bootloader/beacon mode: only SID 11 active, bootloader APID.
            self._enter_bootloader_mode()
            self._spacecraft_phase = new_phase
            # Arm the separation timer AFTER _enter_bootloader_mode(), which
            # zeroes it — otherwise commanding phase 1 left the timer at 0 and
            # it expired on the very next tick.
            if new_phase == 1:
                self._sep_timer = self._sep_timer_duration
        else:
            # Application mode: HK per the configured defaults, application
            # APID, stores online. Force OBDH sw_image to APPLICATION so the
            # reboot-detection logic in _tick_spacecraft_phase does not
            # immediately revert us to bootloader on the next tick.
            obdh = self.subsystems.get("obdh")
            if obdh and hasattr(obdh, '_state'):
                obdh._state.sw_image = 1
                if hasattr(obdh._state, 'boot_app_pending'):
                    obdh._state.boot_app_pending = False
            if old_phase <= 3:
                self._exit_bootloader_mode()
            else:
                self._apply_default_hk_enable()
        self.params[0x0129] = self._spacecraft_phase
        return old_phase

    def _tick_spacecraft_phase(self, dt: float) -> None:
        """Manage spacecraft phase state machine.

        Phases:
        0 = PRE_SEPARATION   — everything OFF, no subsystem ticks
        1 = SEPARATION_TIMER — 30-min timer running, everything still OFF
        2 = INITIAL_POWER_ON — timer expired, unswitchable lines enable (RX + OBC)
        3 = BOOTLOADER_OPS   — OBC running bootloader, beacon HK only
        4 = LEOP             — application software booted, sequential checkout
        5 = COMMISSIONING    — all subsystems being checked out
        6 = NOMINAL          — normal operations
        """
        phase = self._spacecraft_phase

        if phase == 0:  # PRE_SEPARATION
            # Everything stays OFF, no subsystem ticks should run
            self.params[0x0129] = 0
            self.params[0x0127] = 0
            self.params[0x0128] = 0.0
            return

        elif phase == 1:  # SEPARATION_TIMER
            self._sep_timer -= dt
            self.params[0x0128] = max(0.0, self._sep_timer)
            self.params[0x0127] = 1  # timer active
            self.params[0x0129] = 1
            if self._sep_timer <= 0:
                # Timer expired — enable unswitchable power lines
                self._spacecraft_phase = 2
                self._emit_event({
                    'event_id': 0x0050,
                    'severity': 2,
                    'description': "Separation timer expired — initial power-on",
                })
            return

        elif phase == 2:  # INITIAL_POWER_ON
            # OBC and RX are on (unswitchable), everything else OFF
            # Set OBDH to bootloader mode (sw_image=0)
            obdh = self.subsystems.get("obdh")
            if obdh and hasattr(obdh, '_state'):
                obdh._state.sw_image = 0
            # Transition to bootloader ops after 1 tick
            self._spacecraft_phase = 3
            self.params[0x0129] = 2
            self._emit_event({
                'event_id': 0x0051,
                'severity': 2,
                'description': "OBC bootloader started — beacon mode",
            })
            # Fall through to let subsystems tick

        elif phase == 3:  # BOOTLOADER_OPS
            self.params[0x0129] = 3
            # Check if OBC has booted to application software
            obdh = self.subsystems.get("obdh")
            if obdh and hasattr(obdh, '_state') and obdh._state.sw_image == 1:
                # Application software running — transition to LEOP
                self._spacecraft_phase = 4
                # Switch APID, enable all HK SIDs, bring stores online
                self._exit_bootloader_mode()
                logger.info("OBC application booted — phase → LEOP")
                self._emit_event({
                    'event_id': 0x0052,
                    'severity': 2,
                    'description': "OBC application booted — LEOP phase started",
                })

        # Phases 4-6: detect OBC reboot back into bootloader (sw_image → 0).
        # If the application was running and the OBC has dropped to bootloader,
        # we must revert to bootloader/beacon mode (single SID, bootloader APID,
        # stores torn down) until the application re-boots.
        if phase >= 4:
            obdh = self.subsystems.get("obdh")
            if obdh and hasattr(obdh, '_state') and obdh._state.sw_image == 0:
                logger.warning(
                    "OBC reverted to bootloader (reboot detected) — phase → BOOTLOADER_OPS")
                self._enter_bootloader_mode()
                self._emit_event({
                    'event_id': 0x0054,
                    'severity': 3,
                    'description': "OBC reboot — reverted to bootloader/beacon mode",
                })

        # Phases 4-6 are normal operations with increasing capability
        self.params[0x0129] = self._spacecraft_phase
        self.params[0x0127] = 0  # timer not active
        self.params[0x0128] = 0.0  # timer remaining = 0

    # ------------------------------------------------------------------
    # Auto-TX hold-down (uplink-triggered transmitter wake-up)
    # ------------------------------------------------------------------
    def _arm_auto_tx_hold(self) -> None:
        """Power TTC TX line on and (re)arm the hold-down timer.

        Called from _dispatch_tc immediately after S1.1 acceptance, so any
        valid uplink command guarantees a downlink for the next
        ``_auto_tx_hold_s`` seconds. The operator does not have to send a
        separate POWER_LINE_ON(ttc_tx) before commanding from cold boot.
        """
        eps = self.subsystems.get("eps")
        if eps and hasattr(eps, "_state") and hasattr(eps._state, "power_lines"):
            already_on = bool(eps._state.power_lines.get("ttc_tx", False))
            eps._state.power_lines["ttc_tx"] = True
            if not already_on:
                logger.info(
                    "Auto-TX hold-down: TTC TX line energised on accepted TC "
                    "(hold %d s)", int(self._auto_tx_hold_s))
        self._auto_tx_remaining = float(self._auto_tx_hold_s)

    def _tick_auto_tx_hold(self, dt: float) -> None:
        """Decay the auto-TX hold-down timer; turn TX off when expired.

        Operator-commanded POWER_LINE_OFF(ttc_tx) still works — once we
        turn the line off here we leave it off until the next accepted
        TC re-arms the timer.
        """
        if self._auto_tx_remaining <= 0.0:
            return
        self._auto_tx_remaining -= dt
        if self._auto_tx_remaining <= 0.0:
            self._auto_tx_remaining = 0.0
            eps = self.subsystems.get("eps")
            if eps and hasattr(eps, "_state") and hasattr(eps._state, "power_lines"):
                if eps._state.power_lines.get("ttc_tx", False):
                    eps._state.power_lines["ttc_tx"] = False
                    logger.info(
                        "Auto-TX hold-down expired: TTC TX line de-energised")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    @property
    def downlink_active(self) -> bool:
        """S-band TM downlink: is the prime transmitter actually radiating?

        Gated on ``ttc.tx_active`` (0x0525) — PA commanded on and out of
        thermal shutdown, the selected transponder alive, the EPS ``ttc_tx``
        power line hot, and a station in view. ``TTCBasicModel.tick`` computes
        it; 0x052B carries the per-condition breakdown a console renders as
        TRANSMITTING / INHIBITED (PA OFF) / INHIBITED (ttc_tx LINE OFF) /
        NO CONTACT / BEACON ONLY.

        WHAT THIS USED TO BE, and why it was wrong (2026-08b, finding T-a).
        It read 0x0501 ``link_status``, which the TT&C model derives from
        ``frame_sync``/``carrier_lock`` — i.e. from the UPLINK RECEIVE chain.
        It was a receiver-lock gate wearing a downlink label, and it was the
        only thing in the whole TM path that even pretended to look at the
        transmitter: ``pa_on`` was read in exactly four places (forward-power
        reporting, the link-budget branch, the PA thermal load and HK 0x0516),
        none of them on the path a packet takes. Result, reproduced on the
        shipped build: ``pa_on = False``, ``tx_fwd_power = 0.0 W``,
        ``link_status = LOCKED``, telemetry live. Two further faults sat in
        the same three lines:

        * ``bool(link_status)`` treated ACQUIRING(1) as link-OK, so full-rate
          TM flowed on carrier lock alone with no frame sync;
        * the ``is not None else True`` default failed **open** — before the
          TT&C model's first tick, a spacecraft with no transmitter at all was
          considered to be transmitting. It now fails **closed**, which is the
          direction a safety gate is allowed to be wrong in.

        The instructor pass override is no longer OR-ed in blindly. Keeping
        the blanket OR would have re-opened the whole defect for any session
        running with the override on — PA off, line dead, telemetry flowing.
        It now substitutes for the CONTACT leg only, which is all it ever
        meant ("pretend a station is in view"), and it still cannot
        manufacture a transmitter: 0x052B bits 0 and 1 (PA on, ``ttc_tx`` line
        hot) must both be set. In a running simulator the override is
        published to 0x05FF and the TT&C model raises ``tx_active`` by itself;
        this branch is what keeps a harness that sets the flag directly, and
        an override toggled between ticks, behaving sensibly.

        The UHF beacon transmitter is deliberately NOT part of this. It has
        its own gate (``beacon_active``, 0x0527), carries HK SID 11 only, and
        emits through ``TTCBasicModel._tick_beacon_emission`` precisely so it
        survives everything above — Franz's ruling R2.
        """
        tx_active = self.params.get(0x0525)
        if tx_active is None:
            # Fail closed: no 0x0525 means the TT&C model has not ticked, and
            # a spacecraft whose transmitter state is unknown is not one you
            # may assume is transmitting.
            return False
        if tx_active:
            return True
        # ── The bootloader downlink is the UHF beacon radio ──────────────
        # While the OBC is in the bootloader (``obdh.sw_image`` 0x0311 == 0)
        # the S-band chain is inhibited by construction — ``_enter_bootloader
        # _mode`` sets pa_on False, the antenna stowed and the ttc_tx line
        # cold — and the beacon transmitter is the only thing on the air.
        # That is the architecture, and it is also how a real bootloader
        # works: its comms live on the beacon radio, so the LEOP link carries
        # both the beacon and the command verification for the handful of
        # commands the bootloader accepts. It cannot carry anything else,
        # because `_emit_hk_packets` already restricts bootloader HK to SIDs
        # 10 and 11 and the bootloader has no storage, dump or playback
        # service to generate the rest.
        #
        # Once the application boots this branch closes and the S-band chain
        # is the only downlink again, which is where Franz's finding lives:
        # in nominal operations, PA off means no telemetry.
        if int(self.params.get(0x0311, 1)) == 0 and self.params.get(0x0527):
            return True
        # `tx_active` is a snapshot from the last TT&C tick, and two of its
        # three legs can legitimately change between that tick and this call:
        #   * `_arm_auto_tx_hold` energises the `ttc_tx` line at TC-ACCEPTANCE
        #     time, so the S1.1/S1.3/S1.7 for the very command that powered
        #     the transmitter would otherwise be dropped against a param that
        #     is one tick stale;
        #   * the instructor pass override is a ground-side flag that stands
        #     in for "a station is in view".
        # Both are granted a one-tick grace. THE PA IS NOT, ever: bit 0 of
        # 0x052B must be set. That asymmetry is the whole fix — a stale power
        # line or an artificial pass is a bookkeeping lag, whereas a
        # transmitter an operator has switched off is the thing Franz asked us
        # to stop lying about.
        gate = self.params.get(0x052B)
        if gate is None or not (int(gate) & 0x01):
            return False
        return bool(self._override_passes
                    or getattr(self, "_auto_tx_remaining", 0.0) > 0.0)

    @property
    def uplink_active(self) -> bool:
        """TC uplink: has the ground station's uplink locked to the spacecraft?

        This is the half of the old shared gate that genuinely WAS about the
        lock chain, and it keeps it: 0x0501 ``link_status`` is derived from
        carrier lock and frame sync, which is exactly the right question for
        "can this spacecraft receive and act on a telecommand". Splitting it
        away from ``downlink_active`` is what lets each gate mean one thing.

        Two changes (2026-08b, finding T-a):

        * **LOCKED(2) is required, not merely truthy.** ``bool(link_status)``
          also accepted ACQUIRING(1) — carrier lock without bit or frame sync
          — so telecommands were dispatched during an acquisition sequence
          that has not yet produced a decodable frame. The lock ladder exists
          to be climbed; accepting rung 1 as rung 2 discards it.
        * **It fails closed before the first tick**, for the same reason
          ``downlink_active`` does.

        The instructor pass override still forces the uplink, because that is
        what the override is FOR: it lets an instructor command outside a real
        pass. It no longer forces the downlink.
        """
        if self._override_passes:
            return True
        link_status = self.params.get(0x0501)
        if link_status is None:
            return False
        return bool(self._in_contact) and int(link_status) >= 2  # 2 = LOCKED

    # Rate-limit for the tick-fault traceback (roadmap N-1: a crash loop must
    # not fill the disk with identical stack traces).
    _ENGINE_FAULT_LOG_INTERVAL_S = 10.0

    # ------------------------------------------------------------------
    # State round-trip (plan A-4)
    # ------------------------------------------------------------------

    def register_stateful(self, name: str, component: Any) -> None:
        """Register a component whose mutable state must survive breakpoints.

        Raises TypeError when the component does not implement the Stateful
        protocol — a new stateful component without get_state/set_state must
        fail loudly, not silently lose its state on the next breakpoint load.
        """
        if not (callable(getattr(component, 'get_state', None))
                and callable(getattr(component, 'set_state', None))):
            raise TypeError(
                f"Component {name!r} ({type(component).__name__}) does not "
                f"implement the Stateful protocol (get_state/set_state)")
        self._stateful_components[name] = component

    def stateful_components(self) -> dict[str, Any]:
        """Name → component map the BreakpointManager serialises (plan A-4)."""
        return dict(self._stateful_components)

    def get_state(self) -> dict[str, Any]:
        """Serialise ENGINE-LEVEL mutable state (plan A-4).

        Everything here used to be silently lost on breakpoint load (brief 08
        B-2): the phase machine, edge detectors (whose loss re-fired spurious
        AOS/eclipse/mode events on the first tick after a load), dump/RT
        pacing backlogs, FDIR latches, TM sequence counters and the RNG.
        Params/sim-time/speed/sc_mode/HK timers stay top-level in the
        breakpoint for backwards compatibility with existing snapshots.
        """
        from smo_common.state import rng_state_to_json
        return {
            "spacecraft_phase": self._spacecraft_phase,
            "sep_timer": self._sep_timer,
            "sep_timer_duration": self._sep_timer_duration,
            "auto_tx_remaining": self._auto_tx_remaining,
            "current_load_shed_stage": self._current_load_shed_stage,
            # FDIR's own elapsed-time accumulator (see __init__). The legacy
            # key name is kept so older snapshots keep loading.
            "fdir_elapsed_s": self._fdir_elapsed_s,
            "sim_elapsed_fdir": self._fdir_elapsed_s,
            "fdir_safe_active": self._fdir_safe_active,
            "fdir_emergency_active": self._fdir_emergency_active,
            "prev_in_contact": self._prev_in_contact,
            "prev_in_eclipse": self._prev_in_eclipse,
            "in_contact": self._in_contact,
            "active_gs": self._active_gs,
            "prev_active_gs": self._prev_active_gs,
            "prev_payload_mode": self._prev_payload_mode,
            "prev_obdh_mode": self._prev_obdh_mode,
            "event_flags": dict(self._event_flags),
            "s12_violation_active": sorted(self._s12_violation_active),
            "s12_violation_records": {
                str(k): dict(v)
                for k, v in self._s12_violation_records.items()},
            "dump_pending": [[pkt.hex(), int(store_id)]
                             for pkt, store_id in self._dump_pending],
            "dump_active_stores": sorted(self._dump_active_stores),
            "dump_byte_budget": self._dump_byte_budget,
            "rt_pending": [pkt.hex() for pkt in self._rt_pending],
            "rt_byte_budget": self._rt_byte_budget,
            "override_passes": self._override_passes,
            "tm_apid": self.tm_builder.apid,
            "tm_seq_count": self.tm_builder._seq_count,
            "seed": self.seed,
            "rng_state": rng_state_to_json(self.rng.getstate()),
            "prop_speed_clamped": bool(getattr(self, "_prop_speed_clamped",
                                               False)),
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore engine-level mutable state (see get_state)."""
        from smo_common.state import restore_rng
        g = state.get
        self._spacecraft_phase = int(g("spacecraft_phase",
                                       self._spacecraft_phase))
        self._sep_timer = float(g("sep_timer", self._sep_timer))
        self._sep_timer_duration = float(g("sep_timer_duration",
                                           self._sep_timer_duration))
        self._auto_tx_remaining = float(g("auto_tx_remaining",
                                          self._auto_tx_remaining))
        self._current_load_shed_stage = int(
            g("current_load_shed_stage", self._current_load_shed_stage))
        self._fdir_elapsed_s = float(g("fdir_elapsed_s",
                                       g("sim_elapsed_fdir",
                                         self._fdir_elapsed_s)))
        self._fdir_safe_active = bool(g("fdir_safe_active",
                                        self._fdir_safe_active))
        self._fdir_emergency_active = bool(g("fdir_emergency_active",
                                             self._fdir_emergency_active))
        self._prev_in_contact = bool(g("prev_in_contact",
                                       self._prev_in_contact))
        self._prev_in_eclipse = bool(g("prev_in_eclipse",
                                       self._prev_in_eclipse))
        self._in_contact = bool(g("in_contact", self._in_contact))
        self._active_gs = g("active_gs", self._active_gs)
        self._prev_active_gs = g("prev_active_gs", self._prev_active_gs)
        self._prev_payload_mode = g("prev_payload_mode",
                                    self._prev_payload_mode)
        self._prev_obdh_mode = g("prev_obdh_mode", self._prev_obdh_mode)
        self._event_flags = dict(g("event_flags", {}))
        self._s12_violation_active = set(
            int(i) for i in g("s12_violation_active", []))
        self._s12_violation_records = {
            int(k): dict(v)
            for k, v in g("s12_violation_records", {}).items()}
        self._dump_pending = [
            (bytes.fromhex(pkt), int(store_id))
            for pkt, store_id in g("dump_pending", [])]
        self._dump_active_stores = set(
            int(i) for i in g("dump_active_stores", []))
        self._dump_byte_budget = float(g("dump_byte_budget",
                                         self._dump_byte_budget))
        self._rt_pending = [bytes.fromhex(pkt) for pkt in g("rt_pending", [])]
        self._rt_byte_budget = float(g("rt_byte_budget",
                                       self._rt_byte_budget))
        self._override_passes = bool(g("override_passes",
                                       self._override_passes))
        if "tm_apid" in state:
            # set_apid resets the sequence counter on change — restore the
            # counter AFTER the APID so both survive exactly.
            self.tm_builder.set_apid(int(state["tm_apid"]))
        if "tm_seq_count" in state:
            self.tm_builder._seq_count = int(state["tm_seq_count"])
        if "seed" in state and state["seed"] is not None:
            self.seed = int(state["seed"])
        self._prop_speed_clamped = bool(g("prop_speed_clamped",
                                          getattr(self, "_prop_speed_clamped",
                                                  False)))
        restore_rng(self.rng, state.get("rng_state"))

    def reseed(self, seed: int) -> None:
        """Re-derive every component RNG from `seed` (plan A-2).

        Resets the engine's, each model's and the failure manager's random
        streams to the start of the streams that a fresh engine constructed
        with this seed would use. Lets tests (and instructors) rebase a live
        engine onto a different — or the same — reproducible universe.
        """
        self.seed = int(seed)
        self.rng = derive_rng(self.seed, "engine")
        for name, model in self.subsystems.items():
            model.rng = derive_rng(self.seed, f"model:{name}")
        fm = getattr(self, '_failure_manager', None)
        if fm is not None:
            fm.rng = derive_rng(self.seed, "failure_manager")

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="sim-engine")
        self._thread.start()
        logger.info("SimulationEngine started (speed=%.1fx)", self.speed)

    def stop(self) -> None:
        self.running = False
        logger.info("SimulationEngine stopping")

    def _run_loop(self) -> None:
        # Tick rate: simulation.yaml tick_hz, else mission.yaml
        # network.sim_tick_hz, else 1 Hz (previous hard-coded default).
        tick_hz = self._sim_cfg.tick_hz
        if tick_hz is None:
            tick_hz = getattr(getattr(self._mission_cfg, 'network', None),
                              'sim_tick_hz', None) or 1.0
        tick_hz = float(tick_hz) if float(tick_hz) > 0 else 1.0
        dt_real = 1.0 / tick_hz
        logger.info("Sim loop tick rate: %.3f Hz (dt_real=%.3f s)", tick_hz, dt_real)
        last_wall = time.monotonic()

        while self.running:
            dt_sim = dt_real * self.speed
            # Roadmap N-1 / DI-01: a single exception out of _tick_once used to
            # kill this daemon thread while `running` stayed True and every
            # server kept serving the last frozen state — the sim looked like
            # it had a spacecraft symptom rather than a software fault. Now the
            # fault is caught, counted, surfaced as `engine_fault` in
            # /api/state, and the loop continues.
            try:
                self._tick_once(dt_sim)
            except Exception as e:  # noqa: BLE001 — deliberately broad
                self._record_engine_fault(e)

            # Rate control
            elapsed = time.monotonic() - last_wall
            sleep_t = max(0.0, dt_real - elapsed)
            if sleep_t > 0:
                time.sleep(sleep_t)
            last_wall = time.monotonic()

    def _apply_prop_impulse(self, imp: dict) -> None:
        """Apply a finished burn's delta-V to the orbit propagator.

        The element re-fit is timestamped at the burn MID-POINT (utc_mid);
        on success epoch_version increments (0x0B19 + /api/state) so the
        planner and MCS invalidate their contact/ground-track caches. A
        divergent fit leaves the orbit unchanged and raises 0x0812.
        """
        from smo_common.orbit.propagator import DeltaVFitError
        utc = self._sim_time
        if imp.get("utc_mid"):
            try:
                utc = datetime.fromisoformat(imp["utc_mid"])
            except Exception:  # noqa: BLE001 — fall back to "now"
                pass
        try:
            res = self.orbit.apply_delta_v(imp["dv_eci_km_s"], utc)
        except DeltaVFitError as e:
            logger.warning("Post-burn orbit fit failed: %s", e)
            self._emit_event({
                'event_id': 0x0812, 'severity': 'HIGH',
                'description': f"PROP_ORBIT_FIT_FAILED: {e} — element set "
                               f"unchanged, flight dynamics to investigate",
            })
            return
        logger.info("Orbit updated after burn: da=%+.3f km, epoch_version=%d",
                    res["da_km"], res["epoch_version"])
        self._emit_event({
            'event_id': 0x080F, 'severity': 'INFO',
            'description': (f"PROP_ORBIT_UPDATED: dv "
                            f"{imp.get('dv_mag_m_s', 0.0):.3f} m/s, da "
                            f"{res['da_km']:+.3f} km, epoch_version "
                            f"{res['epoch_version']}"),
        })

    def _tick_prop_speed_clamp(self, prop) -> None:
        """Clamp time acceleration to <= prop.max_speed_during_burn while a
        burn is FIRING (event 0x0811). At 1000x one tick outlasts a whole
        burn and every mid-burn lesson would be unobservable."""
        try:
            firing = int(getattr(prop._state, 'state', 0)) == 2
        except Exception:  # noqa: BLE001
            firing = False
        if not firing:
            self._prop_speed_clamped = False
            return
        max_speed = float(getattr(prop, 'max_speed_during_burn', 10.0))
        if self.speed > max_speed:
            logger.info("Speed clamped %.0fx -> %.0fx during burn",
                        self.speed, max_speed)
            self.speed = max_speed
            if not getattr(self, "_prop_speed_clamped", False):
                self._prop_speed_clamped = True
                self._emit_event({
                    'event_id': 0x0811, 'severity': 'INFO',
                    'description': (f"PROP_SPEED_CLAMPED: time acceleration "
                                    f"clamped to {max_speed:.0f}x during "
                                    f"burn"),
                })

    def _record_engine_fault(self, exc: BaseException) -> None:
        """Record a tick-loop fault without spamming the log (roadmap N-1)."""
        self.engine_fault_count += 1
        self.engine_fault = f"{type(exc).__name__}: {exc}"
        now = time.monotonic()
        if now - self._engine_fault_last_log >= self._ENGINE_FAULT_LOG_INTERVAL_S:
            self._engine_fault_last_log = now
            logger.exception(
                "Simulation tick failed (%d total) — loop continues: %s",
                self.engine_fault_count, self.engine_fault)

    def _tick_once(self, dt_sim: float) -> None:
        """Execute one simulation step. Extracted from _run_loop so tests (and
        any future scheduler) can drive the loop body deterministically without
        the real-time pacing/threading of start().

        Sub-stepped integration (plan A-3): when dt_sim exceeds
        ``simulation.yaml tick.dt_max_s`` (i.e. under time acceleration), the
        physics chain — orbit, phase machine, subsystem models, coupling,
        S12/FDIR, edge detectors, failure manager — runs
        N = ceil(dt_sim / dt_max_s) times with dt_sim/N each, so the model
        equations see the same step size at x60 as at x1 and edge-triggered
        events fire once per transition at the sub-step where it happens.
        N is capped at ``tick.max_substeps``; if the cap binds the per-substep
        dt grows above dt_max_s (simulated time is never truncated).

        Per-tick OUTPUTS stay at the outer cadence: TC drain, S11 scheduler
        release, OBCP and scenario advance, HK sampling, real-time TM pacing
        and S15 dump pacing run once per outer tick with the full dt_sim,
        embedded in the LAST sub-step at exactly their historical position in
        the chain (so a dt_sim <= dt_max_s tick is byte-identical to the old
        single-pass behaviour).
        """
        # Process instructor commands first (may set override flags
        # needed by orbit/subsystem logic below).
        self._drain_instr_queue()

        n_sub = 1
        if self._dt_max_s > 0 and dt_sim > self._dt_max_s:
            n_sub = min(int(math.ceil(dt_sim / self._dt_max_s)),
                        self._max_substeps)
        dt_sub = dt_sim / n_sub

        for i in range(n_sub):
            # ONE clock (spec C.1): the engine advances sim time and the
            # sub-step propagates the orbit TO it. The orbit used to advance
            # itself by dt at the top of the sub-step while _sim_time was
            # still bumped at the bottom, so the two agreed only by
            # convention — and only at tick boundaries.
            self._sim_time += timedelta(seconds=dt_sub)
            self._tick_substep(dt_sub, dt_sim, last=(i == n_sub - 1))

        self._tick_count += 1

        # Publish the immutable per-tick snapshot readers consume (N-2).
        self._publish_snapshot()

    def _tick_substep(self, dt: float, dt_outer: float, last: bool) -> None:
        """One pass of the subsystem tick chain (plan A-3).

        ``dt`` is the physics step; ``dt_outer`` is the full outer-tick dt
        handed to the once-per-tick handlers, which only run when ``last``.
        """
        # Propagate the orbit to the engine's sim time (the propagator keeps
        # no clock of its own — spec C.1).
        orbit_state = self.orbit.propagate_to(self._sim_time)
        self._in_contact = orbit_state.in_contact
        self._active_gs = getattr(orbit_state, 'active_gs', None)
        self.params[0x05FF] = 1 if self._override_passes else 0
        # Engine speed for models that change fidelity above a threshold
        # (AOCS secular mode above 60x — Phase C WP-1).
        self.params[0x05FE] = float(self.speed)

        # Spacecraft phase state machine (must run before subsystem ticks)
        self._tick_spacecraft_phase(dt)

        # Auto-TX hold-down decay (set by _dispatch_tc on accepted TCs).
        self._tick_auto_tx_hold(dt)

        # Determine which subsystems to tick based on spacecraft phase.
        # EPS, TTC, and OBDH are always-on (OBC + TTC RX are non-switchable
        # power lines), so they tick even in bootloader phases 0-1 to keep
        # beacon telemetry, link budget, and power modelling alive.
        _ALWAYS_ON = {"eps", "ttc", "obdh"}
        if self._spacecraft_phase < 2:
            active_subsystems = _ALWAYS_ON
        elif self._spacecraft_phase < 4:
            # Early application phases: critical subsystems + TCS
            active_subsystems = _ALWAYS_ON | {"tcs"}
        else:
            active_subsystems = set(self.subsystems.keys())

        # ── Stale attitude-derived telemetry (2026-08b P-c) ──
        # 0x02B3-B5 is the TRUE sun vector in the body frame, published by the
        # AOCS model and consumed unconditionally by EPS (eps_basic.py:405) as
        # its PREFERRED illumination source. When AOCS is not in
        # active_subsystems the parameter is never refreshed — but it was never
        # CLEARED either, so the last value AOCS wrote stayed in self.params
        # forever and EPS kept computing generation from an attitude the
        # spacecraft no longer has. Reproduced live: after an OBC reboot
        # reverted the phase 4 -> 3, the sun vector froze and generation sat
        # pinned at ~122 W through orbit-phase changes that should have moved
        # it by tens of watts.
        #
        # Clearing it makes EPS fall back to its honest tumbling model (the
        # `sun_mag < 0.01` branch: Cauchy 1/4 projection over the real panel
        # areas), which is the right physics for a spacecraft whose attitude
        # control is not running.
        # The CSS composite 0x0245-47 is EPS's fallback when 0x02B3-B5 is zero
        # and is published by the same un-ticked model, so it must be cleared
        # with it — otherwise the staleness simply moves one branch down.
        if "aocs" not in active_subsystems:
            for _stale in (0x02B3, 0x02B4, 0x02B5, 0x0245, 0x0246, 0x0247):
                self.params[_stale] = 0.0

        for name, model in self.subsystems.items():
            if name not in active_subsystems:
                continue
            try:
                model.tick(dt, orbit_state, self.params)
            except Exception as e:
                logger.warning("Subsystem %s tick error: %s", name, e)

        if last:
            # Scenario engine tick (defect #9): advances the active scenario
            # so its timed/conditional events fire. Once per outer tick, full
            # dt — scenario timelines deal in elapsed sim time, not physics
            # steps. No-op when no scenario is active.
            se = getattr(self, '_scenario_engine', None)
            if se is not None and se.is_active():
                try:
                    se.tick(dt_outer, self.params)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Scenario engine tick error: %s", e)

            # Process TCs AFTER subsystem ticks so that param 0x0501
            # (TTC link status) is current when _enqueue_tm() checks
            # downlink_active.  Previously TCs were drained before
            # subsystem ticks, causing S1 ACKs to be silently dropped
            # because downlink_active read stale link-status values.
            self._drain_tc_queue()

            # Execute time-tagged commands from scheduler
            # S11 is an ONBOARD schedule: it fires on the OBC's own clock, so
            # a wrong OBC clock fires it at the wrong sim time (spec C.6).
            current_cuc = self._get_cuc_time()
            due_tcs = self._tc_scheduler.tick(current_cuc)
            for tc_pkt in due_tcs:
                self._dispatch_tc(tc_pkt)

            # On-board control procedures (S18 OBCP): advance running
            # procedures on sim time; due steps self-dispatch via _dispatch_tc.
            try:
                self._obcp_manager.tick(dt_outer)
            except Exception as e:  # noqa: BLE001 — OBCP must not kill the loop
                logger.warning("OBCP tick error: %s", e)

        # Cross-subsystem coupling
        eps = self.subsystems.get("eps")
        tcs = self.subsystems.get("tcs")
        if eps and tcs and hasattr(eps, 'set_bat_ambient_temp') and hasattr(tcs, 'get_battery_temp'):
            eps.set_bat_ambient_temp(tcs.get_battery_temp())

        # Phase D propulsion coupling: apply a completed/aborted burn's
        # delta-V to the orbit (single impulse at the burn mid-point, via a
        # TLE differential-correction re-fit that bumps epoch_version), and
        # clamp time acceleration while a burn is FIRING so the burn — and
        # the abort-on-attitude-error lesson — stays observable.
        prop = self.subsystems.get("prop")
        if prop is not None and hasattr(prop, 'consume_pending_impulse'):
            try:
                imp = prop.consume_pending_impulse()
            except Exception as e:  # noqa: BLE001
                logger.warning("Prop impulse coupling error: %s", e)
                imp = None
            if imp and imp.get("dv_eci_km_s"):
                self._apply_prop_impulse(imp)
            self._tick_prop_speed_clamp(prop)

        # S12 on-board monitoring — check parameter limits
        self._tick_s12_monitoring()

        # FDIR
        if self._fdir_enabled:
            self._tick_fdir()
            self._tick_fdir_advanced(dt)

        # Derive spacecraft mode (sc_mode) AFTER FDIR has updated its latched
        # flags this tick. Runs unconditionally (even if FDIR is disabled) so
        # phase/payload-driven SAFE/NOMINAL/SCIENCE transitions still happen.
        self._update_sc_mode()
        self._check_safe_mode_events()

        # Subsystem event generation
        self._check_subsystem_events()

        # Deliver events raised inside the subsystem models (defect #23).
        self._drain_model_events()

        # Transitions
        self._check_transitions(orbit_state)

        if last:
            # HK emission
            self._emit_hk_packets(dt_outer)

            # Real-time TM release, paced at the commanded TM data rate.
            self._tick_rt_emission(dt_outer)

            # S15 paced dump emission (after HK so real-time HK gets priority)
            self._tick_dump_emission(dt_outer)

        # Failure manager (onset ramps + flicker are sim-time physics)
        self._failure_manager.tick(dt)

    # ------------------------------------------------------------------
    # FDIR
    # ------------------------------------------------------------------

    def _fdir_rule_armed(self, rule) -> bool:
        """Is this legacy FDIR rule armed in the current spacecraft phase?

        Per-rule ``min_phase`` (``subsystems/fdir.yaml``), defaulting to "no
        gate". The advanced fault-propagation model is already disarmed below
        ``SC_PHASE_NOMINAL`` wholesale (see ``_tick_fdir_advanced``), for the
        reason spelled out there: during LEOP the reaction wheels are
        legitimately unpowered, so a rule reading ``aocs.wheels_enabled`` fires
        on the first tick and latches a contingency the operator cannot
        command their way out of.

        The same blanket gate is WRONG for the legacy rule set, because most
        of it is hardware protection — battery SoC and bus voltage, battery
        and OBC temperature, wheel and PA temperature, reboot count, CAN bus.
        Those must stay armed through LEOP; that is exactly when a mishandled
        spacecraft flattens its battery. So the gate is per rule, declared in
        configuration, and carried only by the rules whose trigger condition
        is the EXPECTED state of an uncommissioned spacecraft.
        """
        min_phase = getattr(rule, 'min_phase', None)
        if min_phase is None:
            return True
        return int(getattr(self, '_spacecraft_phase',
                           SC_PHASE_NOMINAL)) >= int(min_phase)

    def _tick_fdir(self) -> None:
        for rule in self._fdir_rules:
            param_name = rule.parameter
            threshold = rule.threshold
            if threshold is None:
                continue

            # Phase gate. A disarmed rule is not merely "not evaluated": any
            # latch it is holding is dropped and its persistence accumulator
            # is reset, so dropping back below min_phase (an instructor
            # rewind, an OBC reboot to bootloader) cannot leave a stale
            # contingency pinning sc_mode to SAFE.
            if not self._fdir_rule_armed(rule):
                gated_key = f"{param_name}_{rule.action}"
                self._fdir_triggered[gated_key] = False
                if hasattr(self, '_fdir_persist'):
                    self._fdir_persist[gated_key] = 0.0
                continue

            # Resolve param name to ID
            param_id = self._resolve_param_name(param_name)
            if param_id is None:
                continue

            # An FDIR rule must not fire against a parameter the spacecraft
            # does not produce. `.get(pid, 0.0)` made every unwritten parameter
            # read as 0.0, so a `< 1` rule latched from the first tick.
            raw = self.params.get(param_id)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue

            cond = rule.condition.strip()
            condition_met = False
            if cond.startswith("<"):
                condition_met = value < threshold
            elif cond.startswith(">"):
                condition_met = value > threshold

            rule_key = f"{param_name}_{rule.action}"
            was_triggered = self._fdir_triggered.get(rule_key, False)

            # Persistence filter (Phase C): with real attitude transients a
            # commanded acquisition legitimately holds att_error above the
            # anomaly threshold for minutes — a rule with persist_s only
            # fires when the condition has been continuously true that long.
            persist = float(getattr(rule, 'persist_s', 0.0) or 0.0)
            if persist > 0.0:
                if not hasattr(self, '_fdir_persist'):
                    self._fdir_persist = {}
                if condition_met:
                    acc = self._fdir_persist.get(rule_key, 0.0) + 1.0
                    self._fdir_persist[rule_key] = acc
                    if acc < persist:
                        condition_met = False
                else:
                    self._fdir_persist[rule_key] = 0.0

            if condition_met and not was_triggered:
                self._fdir_triggered[rule_key] = True
                action = rule.action
                cb = self._fdir_callbacks.get(action)
                if cb:
                    try:
                        cb()
                    except Exception as e:
                        logger.warning("FDIR action %s error: %s", action, e)

                self._emit_event({
                    'event_id': 0x8000 | (param_id & 0x0FFF),
                    'severity': rule.level + 1,
                    'description': f"FDIR L{rule.level}: {action} | param={param_name} val={value:.2f}",
                })
            elif not condition_met and was_triggered:
                self._fdir_triggered[rule_key] = False

        # Recompute the latched FDIR mode flags that drive sc_mode. A safe- or
        # emergency-level flag is active whenever ANY rule of that level is still
        # triggered; it clears automatically once the triggering rule un-triggers.
        # This replaces the old direct sc_mode writes and lets EMERGENCY recover
        # to SAFE/NOMINAL as contingencies clear (see _update_sc_mode).
        safe_active = False
        emergency_active = False
        for rule in self._fdir_rules:
            if not self._fdir_rule_armed(rule):
                continue
            rule_key = f"{rule.parameter}_{rule.action}"
            if not self._fdir_triggered.get(rule_key, False):
                continue
            if rule.level >= 3:
                emergency_active = True
            if rule.level >= 2:
                safe_active = True
        self._fdir_safe_active = safe_active
        # The spacecraft_emergency callback may have latched emergency for a rule
        # that isn't itself L>=3; honour the recomputed level-based result, which
        # is the authoritative source for emergency latching.
        self._fdir_emergency_active = emergency_active

    def _update_sc_mode(self) -> None:
        """Recompute the derived spacecraft mode (sc_mode).

        Precedence (highest first):
          EMERGENCY(3): an FDIR emergency contingency is latched active.
          SAFE(0):      an FDIR safe contingency is latched active, OR the
                        spacecraft is not yet commissioned (phase < NOMINAL) —
                        this keeps it SAFE through bootloader/LEOP at startup.
          SCIENCE(2):   commissioned, no contingency, payload actively imaging.
          NOMINAL(1):   otherwise (commissioned, no contingency, not imaging).

        Because this is recomputed every tick, EMERGENCY recovers to SAFE then
        NOMINAL automatically once the triggering contingencies clear.

        ``sat.mode`` (parameter 0x0001) is the telemetered copy of exactly
        this value, written here so the two cannot drift. It previously had
        its own derivation in ``_publish_fdir_parameters`` that read the FDIR
        latches and ignored the phase gate, so an S20 read of 0x0001 said
        NOMINAL while the console header said SAFE throughout LEOP.
        """
        if self._fdir_emergency_active:
            self.sc_mode = SC_EMERGENCY
        else:
            phase = int(getattr(self, '_spacecraft_phase', SC_PHASE_NOMINAL))
            if self._fdir_safe_active or phase < SC_PHASE_NOMINAL:
                self.sc_mode = SC_SAFE
            elif int(self.params.get(0x0600, 0)) == PAYLOAD_MODE_IMAGING:
                self.sc_mode = SC_SCIENCE
            else:
                self.sc_mode = SC_NOMINAL

        # One derivation, one convention (SC_SAFE/NOMINAL/SCIENCE/EMERGENCY).
        self.params[0x0001] = float(self.sc_mode)

    def _resolve_param_name(self, name: str) -> Optional[int]:
        """Resolve a dotted parameter name to its id.

        Subsystem ``param_ids`` blocks are partial — they carry the handful of
        ids the model needs, not the catalogue. Four of the seventeen shipped
        FDIR rules (``aocs.wheels_enabled``, ``obdh.can_bus_status``,
        ``obdh.temp``, ``ttc.pa_temp``) named parameters that appear only in
        ``telemetry/parameters.yaml``, so this returned None and the rules
        were skipped every tick without a word. The catalogue is now the
        fallback and is the authority on names.
        """
        parts = name.split(".", 1)
        if len(parts) != 2:
            return None
        subsys_name, param_key = parts
        cfg = self._subsys_configs.get(subsys_name)
        if cfg and hasattr(cfg, 'model_dump'):
            d = cfg.model_dump()
            pids = d.get("param_ids", {})
            if param_key in pids:
                return pids[param_key]
        return self._param_catalogue_ids().get(name)

    def _param_catalogue_ids(self) -> dict[str, int]:
        """name -> id from telemetry/parameters.yaml (built once, at init).

        Read straight from YAML rather than through the pydantic loader: this
        is a name lookup, and validating 330 ParameterDef models cost ~130 ms
        the first time a rule resolved a name — on the tick thread.
        """
        cached = getattr(self, '_param_name_ids', None)
        if cached is not None:
            return cached
        names: dict[str, int] = {}
        try:
            import yaml as _yaml
            path = self.config_dir / "telemetry" / "parameters.yaml"
            with open(path) as f:
                data = _yaml.safe_load(f) or {}
            for entry in data.get("parameters", []) or []:
                raw = entry.get("id")
                name = entry.get("name")
                if raw is None or not name:
                    continue
                try:
                    names[str(name)] = (int(raw) if isinstance(raw, int)
                                        else int(str(raw), 0))
                except (TypeError, ValueError):
                    continue
        except Exception as e:  # noqa: BLE001
            logger.warning("Parameter catalogue unavailable for name "
                           "resolution: %s", e)
        self._param_name_ids = names
        return names

    # ------------------------------------------------------------------
    # TC handling
    # ------------------------------------------------------------------

    def _drain_tc_queue(self) -> None:
        while not self.tc_queue.empty():
            try:
                raw = self.tc_queue.get_nowait()
                if not self.uplink_active:
                    # Uplink not available — reject with S1.2 (error 0x0005)
                    from smo_common.protocol.ecss_packet import decommutate_packet
                    pkt = decommutate_packet(raw)
                    if pkt and pkt.primary:
                        # Link-side refusal, not an on-board one: the S1.2
                        # carries the reason (defect #87) but no
                        # COMMAND_REJECTED event is raised — the OBSW never
                        # saw the telecommand.
                        rej = self.tm_builder.build_verification_failure(
                            pkt.primary.apid, pkt.primary.sequence_count,
                            0x0005,
                            "Uplink is not available (no command channel "
                            "lock); the telecommand was not accepted")
                        self._enqueue_tm(rej)
                    continue
                self._dispatch_tc(raw)
            except queue.Empty:
                break
            except Exception as e:
                logger.warning("TC dispatch error: %s", e)

    def _check_tc_acceptance(self, service: int, subtype: int, data: bytes) -> tuple[bool, int]:
        """Validate TC before acceptance.

        Returns (accepted, error_code). Error codes:
        0x0001 = unknown service
        0x0002 = unknown subtype
        0x0003 = invalid data length
        0x0004 = subsystem power off
        """
        # 13 was missing (2026-08c item 9): ServiceDispatcher._handle_s13
        # implements 13,1/13,3/13,5/13,9 and the catalogue ships
        # S13_CREATE_TRANSFER etc., but every S13 TC arriving over the real
        # uplink was refused here with 0x0001 "unknown service" before the
        # handler could run — NOM-003's whole downlink chain was dead on
        # the wire while working from the raw dispatcher.
        known_services = {2, 3, 5, 6, 8, 9, 11, 12, 13, 15, 17, 18, 19, 20}
        if service not in known_services:
            return False, 0x0001

        valid_subtypes = {
            2: {1, 5, 6},   # S2 Device Access: distribute on/off (1/5), report status (6)
            3: {1, 2, 3, 4, 5, 6, 27, 31},
            5: {5, 6, 7, 8},
            6: {2, 5, 9},
            8: {1},
            9: {1, 2},
            11: {4, 7, 9, 11, 13, 17},
            # S12: 1/2 global enable-disable, 3/4 per-definition
            # enable-disable, 6 add, 7 delete, 12 report definitions ->
            # TM(12,13). 3, 4 and 12 were implemented in ServiceDispatcher.
            # _handle_s12 and catalogued as MON_ENABLE_DEF / MON_DISABLE_DEF /
            # MON_REPORT_DEFS, but missing from this list, so all three were
            # refused at acceptance with "S12.12 is not an implemented
            # subtype" and the MCS "Refresh from Spacecraft" button could
            # never populate the monitoring table.
            12: {1, 2, 3, 4, 6, 7, 12},
            # S13 large data transfer: 1 create, 3 download segment,
            # 5 end transfer, 9 report status (2, 4, 10, 11 are TM replies).
            13: {1, 3, 5, 9},
            15: {1, 2, 9, 11, 13},
            17: {1},
            18: {1, 2, 3, 4, 6},
            19: {1, 2, 4, 5, 8},
            20: {1, 3},
        }
        if service in valid_subtypes and subtype not in valid_subtypes[service]:
            return False, 0x0002

        # S8 data length check
        if service == 8 and subtype == 1 and len(data) < 1:
            return False, 0x0003

        return True, 0

    @staticmethod
    def _acceptance_failure_reason(service: int, subtype: int, data: bytes,
                                   error_code: int) -> str:
        """Human-readable reason for a ``_check_tc_acceptance`` refusal.

        Kept separate from ``_check_tc_acceptance`` so its (accepted, code)
        contract is unchanged; the text is what the S1.2 failure notice
        carries to the ground (defect #87).
        """
        if error_code == 0x0001:
            return (f"Service {service} is not implemented by this on-board "
                    f"software")
        if error_code == 0x0002:
            return f"S{service}.{subtype} is not an implemented subtype"
        if error_code == 0x0003:
            return (f"S{service}.{subtype}: application data field is too "
                    f"short ({len(data)} octets)")
        return f"S{service}.{subtype} refused at acceptance"

    # S5 event raised whenever a telecommand is refused, at acceptance or at
    # execution. See configs/<mission>/events/event_catalog.yaml — the
    # rejection is operationally interesting in its own right, so it belongs
    # in the event log and the FDIR journal, not only against the one S1
    # verification entry the operator may not be watching (defect #87).
    EV_COMMAND_REJECTED = 0x030B

    @staticmethod
    def _tc_identity(service: int, subtype: int, data: bytes) -> str:
        """Short identity of a telecommand: ``S8.1 func 65`` / ``S3.6``."""
        ident = f"S{service}.{subtype}"
        if service == 8 and subtype == 1 and data:
            ident += f" func {data[0]}"
        return ident

    def _emit_command_rejected(self, service: int, subtype: int, data: bytes,
                               error_code: int, reason: str,
                               stage: str) -> None:
        """Raise the COMMAND_REJECTED S5 event for a refused telecommand.

        ``stage`` is "acceptance" or "execution" — the same event covers both
        so an operator has one thing to look for.

        This is also the single choke point through which the failure code
        reaches *telemetry*: the OBDH model latches it into
        ``obdh.last_tc_error_code`` (0x0315) so the code is visible on a
        parameter display and not only in the event text (defect #88).
        """
        ident = self._tc_identity(service, subtype, data)
        obdh = self.subsystems.get("obdh")
        if obdh is not None and hasattr(obdh, 'record_tc_failure_code'):
            try:
                obdh.record_tc_failure_code(int(error_code))
            except Exception as e:  # noqa: BLE001 — telemetry must not block TM
                logger.debug("Could not latch last TC failure code: %s", e)
        try:
            self._emit_event({
                'event_id': self.EV_COMMAND_REJECTED,
                'severity': 'MEDIUM',
                'description': format_command_rejection(
                    ident, stage, int(error_code), reason),
                'service': int(service),
                'subtype': int(subtype),
                'func_id': int(data[0]) if (service == 8 and subtype == 1
                                            and data) else None,
                'error_code': int(error_code),
                'reason': reason,
                'stage': stage,
            })
        except Exception as e:  # noqa: BLE001 — a rejection must still report
            logger.warning("Failed to emit COMMAND_REJECTED event: %s", e)

    def _reject_tc(self, pkt, error_code: int, reason: str) -> None:
        """Refuse a telecommand at acceptance: S1.2 + COMMAND_REJECTED event.

        Every acceptance-time refusal goes through here so the reason reaches
        the ground exactly once, in both the verification report and the event
        log, without each gate having to remember to do it (defect #87).
        """
        svc = pkt.secondary.service if pkt.secondary else 0
        sub = pkt.secondary.subtype if pkt.secondary else 0
        rej = self.tm_builder.build_verification_failure(
            pkt.primary.apid, pkt.primary.sequence_count, error_code, reason)
        self._enqueue_tm(rej)
        obdh = self.subsystems.get("obdh")
        if obdh and hasattr(obdh, 'record_tc_rejected'):
            obdh.record_tc_rejected()
        logger.info("TC rejected at acceptance — %s (err 0x%04X)",
                    reason, error_code)
        self._emit_command_rejected(svc, sub, pkt.data_field or b'',
                                    error_code, reason, "acceptance")

    def _dispatch_tc(self, raw: bytes) -> None:
        from smo_common.protocol.ecss_packet import decommutate_packet
        pkt = decommutate_packet(raw)
        if pkt is None or pkt.secondary is None:
            logger.warning("TC dropped: malformed packet (%d bytes, cannot decommutate)",
                           len(raw))
            return
        obdh = self.subsystems.get("obdh")
        if obdh and hasattr(obdh, 'record_tc_received'):
            obdh.record_tc_received()

        svc = pkt.secondary.service
        sub = pkt.secondary.subtype

        # Bootloader command restriction.
        #
        # The bootloader implements only a minimal subset of PUS services.
        # Any other TC must be rejected at acceptance (S1.2) BEFORE the
        # engine runs execution start / completion, so operators never see
        # spurious S1.7s for commands that cannot actually run in the
        # bootloader. We gate on spacecraft phase (authoritative) rather
        # than params[0x0311] because the phase is set synchronously when
        # _enter_bootloader_mode() runs, while the param is only refreshed
        # once per tick — early TCs after boot could otherwise slip through.
        #
        # Allowed in bootloader:
        #   • S17.1  connection test (ping)
        #   • S9.1   set OBT
        #   • S8.1 with the OBDH bootloader-maintenance func_ids — these
        #     mirror obdh_basic._BOOTLOADER_ALLOWED. The OBDH route table
        #     places these in the 50–62 range:
        #       52 obc_reboot         53 obc_switch_unit   54 obc_select_bus
        #       55 obc_boot_app       56 obc_boot_inhibit  57 obc_clear_reboot_cnt
        #       61 diagnostic
        #     Plus EPS power-line on/off so the operator can energise
        #     ttc_tx (and any other line) before exiting the bootloader:
        #       19 power_line_on      20 power_line_off
        #   • S3.1 / S3.3 / S3.5 / S3.6 / S3.9: housekeeping enable/disable/
        #     report for SID 11 (beacon) so operators can request a one-shot
        #     beacon packet from the ground.
        #   • S1 telemetry is produced by the engine, not dispatched, so no
        #     TC gate required.
        in_bootloader = int(getattr(self, '_spacecraft_phase', 6)) <= 3
        if in_bootloader:
            allowed = False
            if svc == 17 and sub == 1:
                allowed = True
            elif svc == 9 and sub == 1:
                allowed = True
            elif svc == 3 and sub in (1, 3, 5, 6, 9, 27):
                # HK management for the beacon SID is allowed; the dispatcher
                # will reject anything that isn't SID 11 via normal validation.
                allowed = True
            elif svc == 20 and sub in (1, 3):
                # S20 parameter get/set — needed for pass override (0x05FF)
                # and general parameter management in bootloader
                allowed = True
            elif svc == 8 and sub == 1 and len(pkt.data_field) >= 1:
                func_id = pkt.data_field[0]
                # OBDH boot maintenance + EPS power-line control +
                # TTC antenna deploy (69) + PA on/off (66/67) for LEOP
                # first-contact downlink establishment + the mission-phase
                # declaration (120). 120 is in the subset so the one command
                # that owns the phase is never itself locked out by the phase
                # gate; its own guards refuse an application phase while the
                # OBC is in the bootloader, so being reachable here cannot be
                # used to skip OBC_BOOT_APP.
                if func_id in {19, 20, 52, 53, 54, 55, 56, 57, 61, 66, 67, 69,
                               120}:
                    allowed = True
            if not allowed:
                # Reject at acceptance — no S1.3/S1.7 is emitted.
                ident = self._tc_identity(svc, sub, pkt.data_field)
                self._reject_tc(
                    pkt, 0x0006,
                    f"{ident} is not in the bootloader command subset "
                    f"(phase {int(self._spacecraft_phase)}); boot the "
                    f"application (S8.1 func 55) first")
                return

        # Bus-reachability gate (defect #29): if the active CAN bus has failed,
        # reject S8 function commands aimed at bus-attached subsystems. OBDH
        # function commands (func 50-62, 80 — including obc_select_bus=54, the
        # recovery action) are NOT gated, since the OBC executes them directly.
        if svc == 8 and sub == 1 and pkt.data_field and self._obc_bus_failed():
            func_id = pkt.data_field[0]
            is_obdh_func = (50 <= func_id <= 62) or func_id == 80
            if not is_obdh_func:
                self._reject_tc(
                    pkt, 0x0004,
                    f"S8.1 func {func_id}: target subsystem is unreachable — "
                    f"the OBC's active CAN bus has failed (recover with "
                    f"func 54 OBC_SELECT_BUS)")
                return

        # Check acceptance
        accepted, error_code = self._check_tc_acceptance(svc, sub, pkt.data_field)
        if not accepted:
            # Send S1.2 acceptance failure
            self._reject_tc(pkt, error_code, self._acceptance_failure_reason(
                svc, sub, pkt.data_field, error_code))
            return

        # Centralised power-state gate. Reject TCs whose target unit
        # has its EPS power line OFF *at acceptance time* — emit S1.2
        # with error 0x0004 ("subsystem power off") and stop. This
        # prevents the engine from later running execution and
        # truthfully reporting S1.7 success for a unit that should
        # never have responded.
        #
        # Skip the power gate entirely for commands that already passed
        # the bootloader allowlist. In bootloader phases, the OBC and
        # TTC RX are the only powered equipment, but the operator still
        # needs S20 GET/SET for pass override (0x05FF), S8.1 for
        # antenna deploy (func_id 69) and power-line control (19/20),
        # etc. The bootloader gate is the authoritative filter.
        dispatcher = self._dispatcher
        power_ok, reason = True, ""
        if not in_bootloader:
            power_ok, reason = dispatcher.check_power_state(svc, sub, pkt.data_field)
        if not power_ok:
            self._reject_tc(pkt, 0x0004, reason)
            return

        # Send S1.1 acceptance success
        acc = self.tm_builder.build_verification_acceptance(pkt.primary.apid, pkt.primary.sequence_count)
        self._enqueue_tm(acc)
        if obdh and hasattr(obdh, 'record_tc_accepted'):
            obdh.record_tc_accepted()

        # Auto-TX hold-down: a valid TC was just accepted, so the
        # spacecraft autonomously energises the TTC TX line and (re)arms
        # the hold-down timer. This guarantees the operator can see the
        # S1.1 acceptance and any subsequent S1.3/S1.7 / store-dump TM
        # without first having to manually power the transmitter on.
        self._arm_auto_tx_hold()

        # S1.3 Execution start
        exec_start_reports = dispatcher.generate_s1_reports(
            pkt.primary.sequence_count, svc, sub)
        for rpt in exec_start_reports:
            self._enqueue_tm(rpt)

        # Execute. The outcome comes back WITH the packets (WP-7): the old
        # `_last_error` / `_last_exec_fail` side-channels were reset here and
        # read back after the call, which is not reentrant — an S11 release, an
        # S19 event-action or an S18 OBCP step dispatches from inside this one,
        # and the inner command's flag became the outer command's verdict.
        result = dispatcher.dispatch(svc, sub, pkt.data_field, pkt.primary)
        for resp_pkt in result:
            self._enqueue_tm(resp_pkt)

        # The model's own words go into the S1.8 failure notice AND into a
        # COMMAND_REJECTED event: an S1.8 carrying only "0x0007" left the
        # operator with no way to tell which of the several gates sharing that
        # code had refused the command (defect #87).
        if result.failure_kind == "execution":
            exec_reason = result.reason
            exec_code = result.error_code or 0x0001
            fail = self.tm_builder.build_execution_failure(
                pkt.primary.apid, pkt.primary.sequence_count,
                exec_code, exec_reason)
            self._enqueue_tm(fail)
            logger.info("TC S%d.%d failed in execution — %s (err 0x%04X)",
                        svc, sub, exec_reason, int(exec_code))
            self._emit_command_rejected(svc, sub, pkt.data_field or b'',
                                        exec_code, exec_reason, "execution")
        elif result.failure_kind == "exec_start":
            # The dispatcher downlinked its own S1.4 execution-START failure
            # (every S6 memory refusal, and the S3/S12/S19 argument gates).
            # The S1.4 now carries the real sequence count, so the MCS can
            # attach it to the verification row; the engine must still not
            # follow it with an S1.7 "execution complete" on the same sequence
            # number, which used to turn the row green (defect #88).
            logger.info("TC S%d.%d refused at execution start — %s (err 0x%04X)",
                        svc, sub, result.reason, int(result.error_code))
            self._emit_command_rejected(svc, sub, pkt.data_field or b'',
                                        result.error_code, result.reason,
                                        "execution")
        else:
            # Send S1.7 execution complete
            comp = self.tm_builder.build_verification_completion(pkt.primary.apid, pkt.primary.sequence_count)
            self._enqueue_tm(comp)

        if obdh and hasattr(obdh, 'record_tc_executed'):
            obdh.record_tc_executed()

    # ------------------------------------------------------------------
    # HK emission
    # ------------------------------------------------------------------

    # SIDs owned by bus-attached subsystems (not the OBC itself). When the OBC's
    # active CAN bus has failed these stop being downlinked (defect #29): SID 1
    # EPS, 2 AOCS, 3 TCS, 5 payload, 6 TTC. SID 4 (OBC) and 11 (beacon) survive.
    _BUS_GATED_SIDS = frozenset({1, 2, 3, 5, 6})

    def _obc_bus_failed(self) -> bool:
        """True when the OBC's *active* CAN bus has failed (defect #29).

        While true, bus-attached subsystems are unreachable: their periodic HK is
        suppressed and commands to them are rejected. The operator recovers by
        switching the active bus (obc_select_bus), which is an OBDH command and is
        therefore never gated.
        """
        obdh = self.subsystems.get("obdh")
        st = getattr(obdh, "_state", None)
        if st is None:
            return False
        try:
            from smo_simulator.models.obdh_basic import BUS_FAILED
        except Exception:
            BUS_FAILED = 2
        if getattr(st, "active_bus", 0) == 0:
            return getattr(st, "bus_a_status", 0) == BUS_FAILED
        return getattr(st, "bus_b_status", 0) == BUS_FAILED

    def _emit_hk_packets(self, dt_sim: float) -> None:
        # Bootloader HK gating: only emit minimal SIDs when in bootloader
        sw_image = int(self.params.get(0x0311, 1))
        # Bus-reachability gate (defect #29): if the active CAN bus has failed,
        # bus-attached subsystems can't reach the OBC, so their HK goes silent.
        bus_blocked = self._obc_bus_failed()
        # Power gate: do not emit periodic HK from a SID whose owning EPS
        # power line is OFF. Without this, S3.25 packets keep streaming from
        # an unpowered payload/AOCS even though the on-demand S3.27 path is
        # already gated. The mapping must mirror ServiceDispatcher._SID_OWNER.
        eps = self.subsystems.get("eps")
        eps_lines = (
            getattr(getattr(eps, "_state", None), "power_lines", None) or {}
        )
        sid_power_owner = {
            1: None,            # EPS — always on
            2: "aocs_wheels",   # AOCS sensors / wheels
            3: None,            # TCS — runs on OBC
            4: None,            # Platform composite
            5: "payload",       # Payload imager
            6: None,            # TTC RX — always on (TX gated separately on link)
            11: None,           # Beacon
        }
        for sid, interval in self._hk_intervals.items():
            if not self._hk_enabled.get(sid, True):
                continue
            if sw_image == 0 and sid not in (10, 11):
                continue
            if bus_blocked and sid in self._BUS_GATED_SIDS:
                # Active bus failed — this subsystem is unreachable (defect #29).
                self._hk_timers[sid] = 0.0
                continue
            owner = sid_power_owner.get(sid)
            if owner is not None and not eps_lines.get(owner, True):
                # Powered down — drop the timer so we don't burst on repower,
                # and skip emission entirely.
                self._hk_timers[sid] = 0.0
                continue
            self._hk_timers[sid] = self._hk_timers.get(sid, 0.0) + dt_sim
            if self._hk_timers[sid] >= interval:
                self._hk_timers[sid] = 0.0
                hk_struct = self._hk_structures.get(sid)
                pkt = self.tm_builder.build_hk_packet(sid, self.params, hk_structure=hk_struct)
                if pkt:
                    self._enqueue_tm(pkt)
                    obdh = self.subsystems.get("obdh")
                    if obdh and hasattr(obdh, 'record_tm_packet'):
                        obdh.record_tm_packet()

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def _check_transitions(self, orbit_state) -> None:
        gs_name = getattr(orbit_state, 'active_gs', None)
        if orbit_state.in_contact and not self._prev_in_contact:
            gs_tag = f" {gs_name}" if gs_name else ""
            self._emit_event({'event_id': 0x0001, 'severity': 1,
                              'description': f"AOS{gs_tag} — elevation {orbit_state.gs_elevation_deg:.1f}°"})
        elif not orbit_state.in_contact and self._prev_in_contact:
            gs_tag = f" {self._prev_active_gs}" if self._prev_active_gs else ""
            self._emit_event({'event_id': 0x0002, 'severity': 1, 'description': f"LOS{gs_tag}"})
        if orbit_state.in_contact and gs_name:
            self._prev_active_gs = gs_name
        if orbit_state.in_eclipse and not self._prev_in_eclipse:
            self._emit_event({'event_id': 0x0010, 'severity': 1, 'description': "Eclipse entry"})
        elif not orbit_state.in_eclipse and self._prev_in_eclipse:
            self._emit_event({'event_id': 0x0011, 'severity': 1, 'description': "Sunlight entry"})
        self._prev_in_contact = orbit_state.in_contact
        self._prev_in_eclipse = orbit_state.in_eclipse

    # TTC signal-quality parameter IDs that should be suppressed
    # when a pass override is active (forced pass = guaranteed good link).
    _TTC_SIGNAL_PARAMS = frozenset([0x0502, 0x0503, 0x0519, 0x050C])

    # Track which S12 monitoring IDs are currently in violation so we
    # only emit an S5 event on the *transition* into violation, not every tick.
    _s12_violation_active: set  # initialised in __init__

    def _tick_s12_monitoring(self) -> None:
        """Check S12 on-board monitoring definitions and emit events on violations.

        Events are edge-triggered: an S5 event fires only when a parameter
        *enters* violation (OK → violated).  A second event fires when it
        *clears* (violated → OK).  No events are emitted while the parameter
        remains continuously in violation.
        """
        violations = self._dispatcher.check_monitoring()
        currently_violated: set[int] = set()
        # Snapshot of each violating definition, so a *clear* transition can
        # still report the parameter and limits it belongs to.
        for v in violations:
            self._s12_violation_records[v.get('mon_id', v['param_id'])] = v

        for v in violations:
            mon_id = v.get('mon_id', v['param_id'])
            currently_violated.add(mon_id)

            # Skip if already in violation (no transition)
            if mon_id in self._s12_violation_active:
                continue

            # When passes are forced via override, suppress signal-strength
            # alarms — the link is deliberately established by the instructor
            # so RSSI / Eb/N0 / link-margin / BER violations are spurious.
            if self._override_passes and v['param_id'] in self._TTC_SIGNAL_PARAMS:
                continue

            severity = v.get('severity', 2)
            param_id = v['param_id']
            value = v['value']
            low_limit = v['low_limit']
            high_limit = v['high_limit']
            name = v.get('name', f"param_0x{param_id:04X}")
            description = v.get('description', '')

            # Real S12 check-transition report, TM(12,9) "limit entered".
            # This is what the ground actually decodes as monitoring telemetry;
            # without it every S12 alarm arrived only as a synthesised 0x9xxx
            # S5 event and was filed under "unknown" (defect #69).
            self._emit_s12_transition(v, entered=True)

            # Generate S5 event for the violation (transition IN). Kept in
            # addition to TM(12,9) because the S19 event-action path and the
            # alarm store both hang off _emit_event.
            event_id = 0x9000 | (param_id & 0x0FFF)

            self._emit_event({
                'event_id': event_id,
                'severity': severity,
                'description': (
                    f"{name}: {description} | "
                    f"value={value:.2f} limits=[{low_limit:.2f}, {high_limit:.2f}]"
                ),
            })

        # Emit "return to nominal" reports for violations that have cleared
        cleared = self._s12_violation_active - currently_violated
        for mon_id in cleared:
            record = self._s12_violation_records.pop(mon_id, None)
            if record is not None:
                self._emit_s12_transition(record, entered=False)
            self._emit_event({
                'event_id': 0xA000 | (mon_id & 0x0FFF),
                'severity': 1,  # INFO
                'description': f"S12 mon {mon_id}: parameter returned to nominal",
            })

        self._s12_violation_active = currently_violated

    def _emit_s12_transition(self, violation: dict, entered: bool) -> None:
        """Queue a TM(12,9)/(12,10) check-transition report for `violation`."""
        try:
            pkt = self._dispatcher.build_s12_transition_report(violation, entered)
        except Exception as e:  # noqa: BLE001 — never let a report kill the tick
            logger.warning("S12 transition report build failed: %s", e)
            return
        if pkt:
            self._enqueue_tm(pkt)

    def _emit_event(self, ev: dict) -> None:
        event_id = ev.get('event_id', 0)
        severity = ev.get('severity', 1)
        # Subsystem models express severity as strings ('LOW'/'MEDIUM'/'HIGH'/...).
        # Normalise to the integer scale (1=INFO, 2=WARNING, 3=ALARM, 4=CRITICAL)
        # the rest of _emit_event (alarm-store gate, TM builder) expects.
        if isinstance(severity, str):
            severity = _SEVERITY_NAME_TO_INT.get(severity.strip().upper(), 1)
        description = ev.get('description', '')

        pkt = self.tm_builder.build_event_packet(
            event_id=event_id, severity=severity,
            aux_text=description, params=self.params,
        )
        if pkt:
            self._enqueue_tm(pkt)
            # Alarm store: duplicate any ALARM/CRITICAL (severity >= 2) event
            # into store 4 so operators can dump the alarm history post-facto.
            if (severity >= 2 and self._tm_storage is not None
                    and int(self.params.get(0x0311, 1)) != 0):
                try:
                    self._tm_storage.store_alarm(
                        pkt, timestamp=self._get_cuc_time())
                except Exception:
                    pass
            try:
                self.event_queue.put_nowait(ev)
            except queue.Full:
                pass

            # S19 event-action: trigger any matching event-action rules.
            # Event ids and monitored parameter ids share 0x0000-0x0FFF, so the
            # namespace must be passed explicitly (defect #70). S12 limit
            # violations reach us encoded as 0x9000 | param_id; everything else
            # is a genuine S5 event id.
            from smo_simulator.monitoring_loader import (
                TRIGGER_KIND_EVENT, TRIGGER_KIND_PARAM,
            )
            if (event_id & 0xF000) == 0x9000:
                self._dispatcher.trigger_event_action(
                    event_id & 0x0FFF, kind=TRIGGER_KIND_PARAM)
            else:
                self._dispatcher.trigger_event_action(
                    event_id, kind=TRIGGER_KIND_EVENT)

    def _drain_model_events(self) -> None:
        """Deliver events generated inside subsystem models (defect #23).

        Models push raw events onto ``_model_event_queue`` (as dicts, or as
        ``(event_id, description[, time])`` tuples for EPS). We normalise each to
        the dict shape ``_emit_event`` expects and emit it, so model-internal
        events (heater stuck-on, thermal runaway, SEU, boot/bus failure, FPA
        readiness, etc.) become real S5 telemetry and fire S19 rules — instead of
        being silently discarded as before.
        """
        while True:
            try:
                ev = self._model_event_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(ev, (tuple, list)):
                # EPS shape: (event_id, description, [time])
                ev = {
                    'event_id': ev[0] if len(ev) > 0 else 0,
                    'description': ev[1] if len(ev) > 1 else '',
                    'severity': 2,  # EPS pushes power-state changes — treat as WARNING
                }
            elif not isinstance(ev, dict):
                logger.warning("Dropping malformed model event: %r", ev)
                continue
            try:
                self._emit_event(ev)
            except Exception as e:  # noqa: BLE001 — one bad event must not kill the loop
                logger.warning("Failed to emit model event %r: %s", ev, e)

    # ------------------------------------------------------------------
    # S15 TM Storage dump pacing
    # ------------------------------------------------------------------

    def queue_dump(self, store_id: int) -> int:
        """Queue all packets from a store for paced downlink.

        Returns the number of packets queued. Called from S15.9 handler.
        Packets are released at the TTC TM data rate on each tick. If the
        downlink is not active at the moment a packet is released, that
        packet is lost (consistent with real RF behaviour). Override-on
        forces downlink_active True so packets reach the MCS.

        The source store is auto-cleared after the dump completes.

        Side effect: the full raw dump (all packets, regardless of whether
        they make it across the downlink) is archived to
        ``workspace/dumps/dump_<sid>_<ISO>.bin`` as length-prefixed raw
        packets (uint32 big-endian length, then bytes). This gives the
        between-pass report tool a complete snapshot for processing even
        when radio coverage was partial.
        """
        packets = self._tm_storage.start_dump(store_id)
        if not packets:
            return 0
        # Cap dump queue to prevent unbounded memory growth
        MAX_DUMP_PENDING = 5000
        for pkt in packets:
            if len(self._dump_pending) >= MAX_DUMP_PENDING:
                logger.warning("Dump queue full (%d), discarding remaining packets",
                               MAX_DUMP_PENDING)
                break
            self._dump_pending.append((pkt, store_id))
        self._dump_active_stores.add(store_id)

        # Archive raw dump for between-pass processing
        try:
            import struct as _struct
            dump_dir = self._dump_dir
            dump_dir.mkdir(parents=True, exist_ok=True)
            # The mission timestamp in a dump filename is SIM time, not the
            # host's wall clock: two runs of the same scenario must produce
            # the same names, and the name must match the telemetry inside.
            # Two dumps of the same store inside one sim second (or a re-run
            # over an old workspace) would collide, so step the name forward a
            # second at a time. The name must stay on the canonical
            # dump_store.DUMP_NAME_RE pattern or the report tooling ignores
            # the file, so a "_1" style suffix is not an option.
            stamp = self._sim_time.astimezone(timezone.utc)
            for _ in range(60):
                ts = stamp.strftime("%Y%m%dT%H%M%SZ")
                path = dump_dir / f"dump_sid{store_id:02d}_{ts}.bin"
                if not path.exists():
                    break
                stamp += timedelta(seconds=1)
            with path.open("wb") as f:
                for pkt in packets:
                    f.write(_struct.pack(">I", len(pkt)))
                    f.write(pkt)
            logger.info("Dump archived: %s (%d packets, %d bytes)",
                        path, len(packets), path.stat().st_size)
        except Exception as e:
            logger.warning("Dump archive failed: %s", e)

        logger.info(
            "S15 dump queued: store=%d packets=%d (paced emission)",
            store_id, len(packets),
        )
        return len(packets)

    def _tick_dump_emission(self, dt: float) -> None:
        """Release paced dump packets according to TTC TM data rate.

        Runs every tick. Budget grows by (data_rate_bps/8)*dt bytes per tick.
        Each packet consumes its size in bytes from the budget. If downlink
        is not active, packets are dropped (lost) but still consume budget —
        the radio would have transmitted into the void.
        """
        if not self._dump_pending:
            self._dump_byte_budget = 0.0
            return

        # Resolve the ACTIVE tier's rate (bps) from the TTC subsystem. The S15
        # dump pacer and the real-time pacer share one source of truth, so a
        # tier change moves both (defect #85).
        rate_bps = self._current_tm_rate_bps()

        # Accumulate byte budget
        self._dump_byte_budget += (rate_bps / 8.0) * dt

        downlink = self.downlink_active
        released_stores: set[int] = set()
        while self._dump_pending and self._dump_byte_budget >= len(self._dump_pending[0][0]):
            pkt, store_id = self._dump_pending.pop(0)
            self._dump_byte_budget -= len(pkt)
            released_stores.add(store_id)
            if downlink:
                # Bypass _enqueue_tm to avoid re-storing the dumped packet
                try:
                    self.tm_queue.put_nowait(pkt)
                except queue.Full:
                    pass
            # else: lost — RF off, radio transmitted into the void

        # If we exhausted the budget mid-packet, cap it to avoid unbounded growth
        if self._dump_pending:
            max_budget = len(self._dump_pending[0][0])
            if self._dump_byte_budget > max_budget:
                self._dump_byte_budget = max_budget

        # If no more packets pending for a store, auto-clear it
        if not self._dump_pending:
            for sid in list(self._dump_active_stores):
                self._tm_storage.delete_store(sid)
            self._dump_active_stores.clear()
            logger.info("S15 dump complete: stores auto-cleared")

    def _check_subsystem_events(self) -> None:
        """Check for subsystem state transitions and emit S5 events."""
        p = self.params

        # AOCS mode change: emitted by the AOCS MODEL
        # (aocs_basic._generate_tick_events), which owns `_MODE_NAMES` for all
        # ten modes and carries the transition reason. The duplicate that used
        # to live here carried a 5-entry table from before the 9-mode machine
        # (OFF/DETUMBLE/SAFE/NADIR/TARGET), so every label was wrong and modes
        # 5-9 rendered as "?" — NOMINAL->ECLIPSE downlinked as
        # "AOCS mode: TARGET -> ?" alongside the model's correct event. Deleted
        # 2026-08b; do not reintroduce a second emitter for 0x0200.

        # Payload mode change
        payload_mode = int(p.get(0x0600, 0))
        if self._prev_payload_mode is not None and payload_mode != self._prev_payload_mode:
            modes = {0: 'OFF', 1: 'STANDBY', 2: 'IMAGING', 3: 'PLAYBACK'}
            sev = 1
            if payload_mode == 2:
                self._emit_event({
                    'event_id': 0x0600,
                    'severity': 1,
                    'description': f"Imaging started",
                })
            elif self._prev_payload_mode == 2:
                self._emit_event({
                    'event_id': 0x0601,
                    'severity': 1,
                    'description': f"Imaging stopped",
                })
            else:
                self._emit_event({
                    'event_id': 0x0602,
                    'severity': sev,
                    'description': f"Payload mode: {modes.get(self._prev_payload_mode, '?')} -> {modes.get(payload_mode, '?')}",
                })
        self._prev_payload_mode = payload_mode

        # OBDH mode change
        obdh_mode = int(p.get(0x0300, 0))
        if self._prev_obdh_mode is not None and obdh_mode != self._prev_obdh_mode:
            modes = {0: 'NOMINAL', 1: 'SAFE', 2: 'EMERGENCY'}
            self._emit_event({
                'event_id': 0x0300,
                'severity': 3 if obdh_mode >= 2 else 2 if obdh_mode == 1 else 1,
                'description': f"OBC mode: {modes.get(self._prev_obdh_mode, '?')} -> {modes.get(obdh_mode, '?')}",
            })
        self._prev_obdh_mode = obdh_mode

        # EPS threshold checks
        soc = p.get(0x0101, 75.0)
        if soc < 20 and not self._event_flags.get('low_soc_20'):
            self._event_flags['low_soc_20'] = True
            self._emit_event({
                'event_id': 0x0100,
                'severity': 4,
                'description': f"Battery SoC critical: {soc:.1f}%",
            })
        elif soc >= 22:
            self._event_flags['low_soc_20'] = False

        if soc < 30 and not self._event_flags.get('low_soc_30'):
            self._event_flags['low_soc_30'] = True
            self._emit_event({
                'event_id': 0x0101,
                'severity': 3,
                'description': f"Battery SoC low: {soc:.1f}%",
            })
        elif soc >= 32:
            self._event_flags['low_soc_30'] = False

        bus_v = p.get(0x0105, 28.0)
        if bus_v < 24.0 and not self._event_flags.get('undervoltage'):
            self._event_flags['undervoltage'] = True
            self._emit_event({
                'event_id': 0x0102,
                'severity': 4,
                'description': f"Bus undervoltage: {bus_v:.1f}V",
            })
        elif bus_v >= 25.0:
            self._event_flags['undervoltage'] = False

        # Payload storage warning
        store_pct = p.get(0x0604, 0)
        if store_pct > 90 and not self._event_flags.get('store_warn'):
            self._event_flags['store_warn'] = True
            self._emit_event({
                'event_id': 0x0603,
                'severity': 2,
                'description': f"Payload storage > 90%: {store_pct:.1f}%",
            })
        elif store_pct <= 85:
            self._event_flags['store_warn'] = False

    # ------------------------------------------------------------------
    # Real-time TM rate pacing (defect #74)
    # ------------------------------------------------------------------

    @property
    def _rt_pacing_active(self) -> bool:
        """True when real-time TM should be paced at the commanded TM rate.

        `simulation.yaml ttc.rt_tm_pacing`:
          auto (default) — pace only while the engine owns the clock, i.e. the
                           tick loop is running. Unit tests that dispatch a TC
                           and read tm_queue without ticking are unaffected.
          on  — always pace.   off — never pace (previous behaviour).
        """
        if getattr(self, '_rt_pending', None) is None:
            return False  # engine not fully constructed (mock/partial engines)
        sim_cfg = getattr(self, '_sim_cfg', None)
        mode = str(getattr(getattr(sim_cfg, 'ttc', None),
                           'rt_tm_pacing', 'auto')).lower()
        if mode == 'on':
            return True
        if mode == 'off':
            return False
        return bool(getattr(self, 'running', False)) or bool(
            getattr(self, '_force_rt_pacing', False))

    #: Rate assumed when no TTC model is available (partial/mock engines).
    #: The SAFE rung of the ladder — deliberately the slowest thing on the
    #: ladder so a missing model can never flatter the downlink.
    _TM_RATE_FALLBACK_BPS = 1000.0

    def _current_tm_rate_bps(self) -> float:
        """Information rate (bps) of the TTC model's ACTIVE ladder tier.

        Both TM pacers — real-time (`_tick_rt_emission`) and S15 dump
        (`_tick_dump_emission`) — read this, so both honour the tier.
        """
        try:
            ttc = self.subsystems.get("ttc")
            if ttc is not None and hasattr(ttc, "_state"):
                return float(getattr(ttc._state, "tm_data_rate", 0)
                             or self._TM_RATE_FALLBACK_BPS)
        except (TypeError, ValueError, AttributeError):
            pass
        return self._TM_RATE_FALLBACK_BPS

    def _current_tm_tier(self) -> tuple[int, str]:
        """(index, name) of the TTC model's active TM rate tier."""
        try:
            ttc = self.subsystems.get("ttc")
            if ttc is not None and hasattr(ttc, "_state"):
                idx = int(getattr(ttc._state, "tm_tier", 0))
                return idx, ttc.tier_name(idx)
        except (TypeError, ValueError, AttributeError):
            pass
        return 0, "UNKNOWN"

    def _current_tm_coding(self) -> str:
        """FEC of the active TM tier ('RS' | 'CONV_RS' | 'NONE')."""
        try:
            ttc = self.subsystems.get("ttc")
            if ttc is not None and hasattr(ttc, "active_coding"):
                return str(ttc.active_coding)
        except (TypeError, ValueError, AttributeError):
            pass
        return "RS"

    def _tick_rt_emission(self, dt: float) -> None:
        """Release backlogged real-time TM according to the TM data rate.

        The same byte-budget model as `_tick_dump_emission`, applied to
        real-time HK / event / verification TM. Without it `tm_data_rate` was
        read by the dump pacer alone, so commanding 64 kbps changed nothing
        about what the radio and the MCS actually saw (defect #74).
        """
        self._rt_byte_budget += (self._current_tm_rate_bps() / 8.0) * max(dt, 0.0)
        while self._rt_pending and self._rt_byte_budget >= len(self._rt_pending[0]):
            pkt = self._rt_pending.pop(0)
            self._rt_byte_budget -= len(pkt)
            _put_tm(self, pkt)
        if self._rt_pending:
            # Cap the carried budget so an idle period cannot bank credit and
            # then burst the whole backlog in one tick.
            cap = float(len(self._rt_pending[0]))
            if self._rt_byte_budget > cap:
                self._rt_byte_budget = cap
        else:
            self._rt_byte_budget = min(self._rt_byte_budget,
                                       self._current_tm_rate_bps() / 8.0)

    def _enqueue_tm(self, pkt: bytes) -> None:
        # Only downlink TM when RF link is active (orbit contact + transponder OK)
        if not self.downlink_active:
            # TM silently dropped — log for S1 verification packets so the
            # operator understands why commands appear to vanish
            if len(pkt) > 13:
                try:
                    from smo_common.protocol.ecss_packet import decommutate_packet as _decom
                    _p = _decom(pkt)
                    if _p and _p.secondary and _p.secondary.service == 1:
                        logger.debug("S1.%d verification dropped (downlink inactive)",
                                     _p.secondary.subtype)
                except Exception:
                    pass
        if self.downlink_active:
            # `_rt_pending is None` => partially-constructed / mocked engine;
            # such callers get the direct, unpaced path.
            pending = getattr(self, '_rt_pending', None)
            if pending is not None and self._rt_pacing_active:
                # Rate-paced: the packet joins the real-time backlog and is
                # released by _tick_rt_emission at the ACTIVE tier's rate. The
                # shipped HK structures generate 1351.5 bps of packets
                # (~1557 bps of channel after RS + frame overhead), so at
                # SAFE (1 kbps) the backlog grows without bound and packets
                # are dropped, while at LOW (16 kbps) and above it drains —
                # which is what makes the tier observable at all (defect #85).
                if len(pending) >= self._rt_backlog_max:
                    self.tm_queue_drops += 1
                    logger.warning(
                        "Real-time TM backlog full (%d) at %.0f bps — packet dropped",
                        self._rt_backlog_max, self._current_tm_rate_bps())
                else:
                    pending.append(pkt)
            else:
                _put_tm(self, pkt)
        # Route to onboard storage regardless of contact — but ONLY when the
        # nominal OBSW is running. The bootloader has no PUS-C S15 storage
        # service, so nothing should accumulate in HK/Event/Science/Alarm
        # stores while sw_image == 0. This matches flight behaviour: the
        # stores are created and populated by the nominal application.
        if self._tm_storage and len(pkt) > 13:
            sw_image = int(self.params.get(0x0311, 1))
            if sw_image != 0:
                from smo_common.protocol.ecss_packet import decommutate_packet
                parsed = decommutate_packet(pkt)
                if parsed and parsed.secondary:
                    # Stamp the store with the packet's OWN onboard CUC, so a
                    # dumped store's oldest/newest bracket the packets in it.
                    self._tm_storage.store_packet(
                        parsed.secondary.service, pkt,
                        timestamp=getattr(parsed.secondary, "cuc_seconds",
                                          None))

    # ------------------------------------------------------------------
    # Instructor commands
    # ------------------------------------------------------------------

    # Instructor command types the engine accepts. Applied by
    # `validate_instructor_cmd` to BOTH the HTTP and WebSocket paths — the WS
    # handler previously had no allowlist at all, so an unvalidated payload
    # went straight onto the same queue as a validated one (roadmap N-1).
    INSTRUCTOR_CMD_TYPES = frozenset({
        'set_speed', 'freeze', 'resume', 'inject', 'clear_failure',
        'failure_inject', 'failure_clear', 'failure_clear_all',
        'override_passes', 'set_phase', 'pause_scenario', 'start_separation',
        'save_breakpoint', 'load_breakpoint', 'start_scenario', 'stop_scenario',
        # 2026-08b Phase 2c: a first-class battery-SoC override, so an
        # instructor can keep a lesson running when the battery is running
        # down. Deliberately NOT a fake failure — nothing existing did this:
        # scenario initial_conditions.soc_pct fires only at scenario start,
        # inject_failure('undervoltage') only DRAINS, set_parameter is
        # overwritten by EPS on the next tick, and breakpoints are far too
        # blunt.
        'set_battery_soc',
    })

    # Numeric fields validated (and range-checked) before enqueueing, so a
    # malformed value is rejected at the door instead of raising inside the
    # tick thread. `None` bound = unbounded on that side.
    _INSTRUCTOR_NUMERIC_FIELDS = {
        'set_speed': [('value', 0.0, None)],
        'set_phase': [('phase', 0, 6)],
        'set_battery_soc': [('soc_pct', 0.0, 100.0)],
        'failure_inject': [('magnitude', None, None),
                           ('duration_s', 0.0, None),
                           ('onset_duration_s', 0.0, None)],
    }

    @classmethod
    def validate_instructor_cmd(cls, cmd: Any) -> tuple[bool, str]:
        """Validate an instructor command payload. Returns (ok, reason).

        One implementation for every channel: the HTTP route, the instructor
        WebSocket and any scenario-driven caller. Rejecting here means a
        `{"type":"set_speed","value":"fast"}` can no longer reach — and kill —
        the tick thread (roadmap N-1 / DI-01).
        """
        if not isinstance(cmd, dict):
            return False, "instructor command must be an object"
        t = cmd.get('type')
        if not isinstance(t, str) or t not in cls.INSTRUCTOR_CMD_TYPES:
            return False, f"unknown instructor command type: {t!r}"
        for field, lo, hi in cls._INSTRUCTOR_NUMERIC_FIELDS.get(t, ()):
            if field not in cmd or cmd[field] is None:
                continue
            value = cmd[field]
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                return False, f"{t}.{field} must be a number"
            try:
                value = float(value)
            except (TypeError, ValueError):
                return False, f"{t}.{field} must be a number, got {cmd[field]!r}"
            if value != value or value in (float('inf'), float('-inf')):
                return False, f"{t}.{field} must be finite"
            if lo is not None and value < lo:
                return False, f"{t}.{field} must be >= {lo}"
            if hi is not None and value > hi:
                return False, f"{t}.{field} must be <= {hi}"
        return True, ""

    def submit_instructor_cmd(self, cmd: Any) -> tuple[bool, str]:
        """Validate then enqueue an instructor command. Returns (ok, reason)."""
        ok, reason = self.validate_instructor_cmd(cmd)
        if not ok:
            self.instr_cmd_rejects += 1
            logger.warning("Instructor command rejected: %s (cmd=%r)", reason, cmd)
            return False, reason
        try:
            self.instr_queue.put_nowait(cmd)
        except queue.Full:
            self.instr_cmd_rejects += 1
            return False, "instructor queue full"
        return True, ""

    def submit_instructor_cmd_sync(self, cmd: dict,
                                   timeout_s: float = 10.0) -> tuple[bool, str]:
        """Enqueue an instructor command and wait for the tick thread to run it.

        Used by the HTTP breakpoint routes (A-3 / roadmap X-6). `save()` and
        `load()` reassign `engine.params`, `_sim_time` and every model's state;
        running them inline on the aiohttp thread meant a load landing
        mid-tick produced a spacecraft state with no physical cause — the worst
        thing to hand a student mid-exercise.

        When the tick thread is not running (unit tests, a frozen-but-not-
        started engine) the command is executed inline: single-threaded by
        construction, so there is nothing to coordinate with.
        """
        done = threading.Event()
        outcome: dict = {}
        cmd = dict(cmd)
        cmd['_done'] = done
        cmd['_outcome'] = outcome
        if not self.running or not getattr(self, '_thread', None):
            try:
                self._handle_instructor_cmd(cmd)
            except Exception as e:  # noqa: BLE001
                return False, str(e)
            finally:
                done.set()
            return bool(outcome.get('ok', True)), str(outcome.get('reason', ''))
        ok, reason = self.submit_instructor_cmd(cmd)
        if not ok:
            return False, reason
        if not done.wait(timeout_s):
            return False, "timed out waiting for the tick thread"
        return bool(outcome.get('ok', True)), str(outcome.get('reason', ''))

    def _drain_instr_queue(self) -> None:
        while not self.instr_queue.empty():
            try:
                cmd = self.instr_queue.get_nowait()
            except queue.Empty:
                break
            # Second line of defence: anything that bypassed
            # submit_instructor_cmd (a direct instr_queue.put by a test or a
            # scenario) must still not be able to kill the tick thread.
            try:
                self._handle_instructor_cmd(cmd)
            except Exception as e:  # noqa: BLE001 — deliberately broad
                self.instr_cmd_rejects += 1
                logger.warning("Instructor command %r failed: %s", cmd, e)
                if isinstance(cmd, dict) and isinstance(cmd.get('_outcome'), dict):
                    cmd['_outcome'].update({'ok': False, 'reason': str(e)})
            finally:
                if isinstance(cmd, dict) and isinstance(cmd.get('_done'),
                                                        threading.Event):
                    cmd['_done'].set()

    def _handle_instructor_cmd(self, cmd: dict) -> None:
        if not isinstance(cmd, dict):
            logger.warning("Dropping malformed instructor command: %r", cmd)
            return
        t = cmd.get('type')
        if t == 'set_speed':
            # Clamped to simulation.yaml speed.max (default 1000x) so a stray
            # UI/scenario value cannot stall the loop with an absurd dt_sim.
            try:
                requested = float(cmd.get('value', 1.0))
            except (TypeError, ValueError):
                logger.warning("set_speed value %r is not a number — ignored",
                               cmd.get('value'))
                self.instr_cmd_rejects += 1
                return
            self.speed = max(0.0, min(requested, self._speed_max))
            if self.speed != requested:
                logger.warning("set_speed %.1f clamped to %.1f (speed.max)",
                               requested, self.speed)
            # Phase D: an active burn re-clamps immediately (0x0811 fires on
            # the edge in _tick_prop_speed_clamp on the next sub-step).
            prop = self.subsystems.get("prop")
            if prop is not None:
                self._tick_prop_speed_clamp(prop)
        elif t == 'freeze':
            self.speed = 0.0
        elif t == 'resume':
            self.speed = max(1.0, self.speed) if self.speed == 0 else self.speed
        elif t == 'inject':
            self._handle_failure_inject(cmd)
        elif t == 'clear_failure':
            self._handle_failure_clear(cmd)
        elif t == 'failure_inject':
            from smo_simulator.failure_manager import ONSET_STEP
            # Support both 'failure' (backend) and 'mode' (UI) parameter names
            failure_mode = cmd.get('failure') or cmd.get('mode', '')
            self._failure_manager.inject(
                subsystem=cmd.get('subsystem', ''),
                failure=failure_mode,
                magnitude=float(cmd.get('magnitude', 1.0)),
                onset=cmd.get('onset', ONSET_STEP),
                duration_s=float(cmd['duration_s']) if cmd.get('duration_s') else 0.0,
                onset_duration_s=float(cmd.get('onset_duration_s', 90.0)),
                **{k: v for k, v in cmd.items()
                   if k not in ('type', 'subsystem', 'failure', 'mode', 'magnitude',
                                'onset', 'duration_s', 'onset_duration_s')},
            )
        elif t == 'override_passes':
            # Robust override handling: if the command carries an explicit desired
            # state under any of the accepted keys, set it; otherwise toggle the
            # current value. This keeps the engine in sync no matter which payload
            # schema the UI uses, and makes a bare {'type':'override_passes'} a
            # safe toggle. The new value is published to 0x05FF on the next tick
            # (and immediately below so a polled snapshot reflects it at once).
            explicit = None
            for key in ('enabled', 'on', 'value', 'state'):
                if key in cmd:
                    explicit = bool(cmd[key])
                    break
            if explicit is None:
                self._override_passes = not self._override_passes
            else:
                self._override_passes = explicit
            # Publish immediately so /api/state and the instructor snapshot report
            # the new value without waiting for the next main-loop tick.
            self.params[0x05FF] = 1 if self._override_passes else 0
        elif t == 'failure_clear':
            fid = cmd.get('failure_id')
            if fid:
                self._failure_manager.clear(fid)
            else:
                self._failure_manager.clear_all()
        elif t == 'set_phase':
            new_phase = int(cmd.get('phase', 6))
            if 0 <= new_phase <= 6:
                old_phase = self.apply_phase_change(new_phase)
                self._emit_event({
                    'event_id': 0x0052,
                    'severity': 2,
                    'description': f"Phase change: {old_phase} -> {new_phase}",
                })
        elif t == 'set_battery_soc':
            # ── Battery SoC override (2026-08b Phase 2c, Franz's request) ──
            # "allow us to set the battery level higher than it currently is,
            # to allow us to continue running a sim where the battery is
            # getting low."
            #
            # A first-class instructor command rather than a fake failure,
            # because it is not a fault: it is the instructor editing the
            # world. It runs on the tick thread via instr_queue, so there is
            # no threading hazard, and voltage / bus voltage re-derive from
            # SoC on the same tick.
            eps = self.subsystems.get("eps")
            if eps is None or not hasattr(eps, '_state'):
                logger.warning("set_battery_soc: no EPS model loaded")
                return
            try:
                soc = float(cmd.get('soc_pct', eps._state.bat_soc_pct))
            except (TypeError, ValueError):
                logger.warning("set_battery_soc soc_pct %r is not a number",
                               cmd.get('soc_pct'))
                self.instr_cmd_rejects += 1
                return
            soc = max(0.0, min(100.0, soc))
            old_soc = eps._state.bat_soc_pct
            eps._state.bat_soc_pct = soc
            eps._state.bat_dod_pct = 100.0 - soc
            # The SOC_LOW / SOC_CRITICAL / CHARGE_COMPLETE events are EDGE
            # triggered off _prev_soc. Without moving it too, a jump from 15 %
            # to 90 % makes the next tick look like a discharge edge and fires
            # a bogus alarm — an override that lies in the event log is worse
            # than no override.
            eps._state._prev_soc = soc
            self._emit_event({
                'event_id': 0x0054,
                'severity': 2,
                'description': (f"INSTRUCTOR OVERRIDE: battery SoC set "
                                f"{old_soc:.1f} % -> {soc:.1f} % (not physics)"),
            })
            logger.info("Instructor override: battery SoC %.1f -> %.1f %%",
                        old_soc, soc)
        elif t == 'pause_scenario':
            # Pause active scenario by setting speed to 0, but keep state intact
            if hasattr(self, '_scenario_engine') and self._scenario_engine.is_active():
                # Save current speed to resume later
                if not hasattr(self, '_pause_saved_speed'):
                    self._pause_saved_speed = self.speed
                self.speed = 0.0
                logger.info("Scenario paused")
            else:
                # Speed=0 also pauses scenario effectively
                self.speed = 0.0
        elif t == 'start_separation':
            self.configure_separation_state()
            self._emit_event({
                'event_id': 0x0053,
                'severity': 2,
                'description': "Separation initiated — 30 min timer started",
            })
        elif t == 'failure_clear_all':
            # Clear every active injected failure (defect #12).
            self._failure_manager.clear_all()
        elif t == 'save_breakpoint':
            self._handle_save_breakpoint(cmd)
        elif t == 'load_breakpoint':
            self._handle_load_breakpoint(cmd)
        elif t == 'start_scenario':
            self._handle_start_scenario(cmd)
        elif t == 'stop_scenario':
            self._handle_stop_scenario(cmd)
        else:
            # Defect #9/#10/#12 class: previously, unknown instructor command
            # types were silently dropped here, which is how broken UI buttons
            # went unnoticed. Log loudly instead so future drift is obvious.
            logger.warning("Unknown instructor command type: %r (cmd=%r)", t, cmd)

    # Directory where breakpoint snapshots are persisted (relative to the sim's
    # working dir, alongside workspace/dumps/). Survives restarts.
    _BREAKPOINT_DIR = Path("workspace/breakpoints")

    @staticmethod
    def _safe_breakpoint_filename(name: str) -> str:
        """Sanitise a breakpoint name into a safe .json filename."""
        safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name)
        return (safe or "breakpoint") + ".json"

    def _load_persisted_breakpoints(self) -> None:
        """Load breakpoint snapshots saved to disk in a previous run (defect #10)."""
        import json
        try:
            if not self._BREAKPOINT_DIR.exists():
                return
            for path in sorted(self._BREAKPOINT_DIR.glob("*.json")):
                try:
                    with open(path) as f:
                        state = json.load(f)
                    name = state.get("name") or path.stem
                    self._breakpoints[name] = state
                except Exception as e:  # noqa: BLE001
                    logger.warning("Could not load persisted breakpoint %s: %s", path, e)
            if self._breakpoints:
                logger.info("Loaded %d persisted breakpoint(s) from %s",
                            len(self._breakpoints), self._BREAKPOINT_DIR)
        except Exception as e:  # noqa: BLE001 — never let this block startup
            logger.warning("Persisted-breakpoint load skipped: %s", e)

    def _handle_save_breakpoint(self, cmd: dict) -> None:
        """Capture a full state snapshot, store it by name, and persist to disk (defect #10)."""
        from smo_simulator.breakpoints import BreakpointManager
        name = cmd.get('name') or ''
        try:
            # Persist to disk so the breakpoint survives a sim restart; save()
            # writes the JSON file when given a path and also returns the state.
            path = self._BREAKPOINT_DIR / self._safe_breakpoint_filename(name) if name \
                else None
            state = BreakpointManager(self).save(name=name, path=path)
            self._breakpoints[state['name']] = state
            # If the name was empty, save() generated one — persist under it now.
            if path is None:
                gen_path = self._BREAKPOINT_DIR / self._safe_breakpoint_filename(state['name'])
                BreakpointManager(self).save(name=state['name'], path=gen_path)
            logger.info("Breakpoint saved: %s (tick=%s)", state['name'],
                        state.get('tick_count'))
            if isinstance(cmd.get('_outcome'), dict):
                cmd['_outcome'].update({'ok': True, 'name': state['name']})
        except Exception as e:  # noqa: BLE001 — never let a bad snapshot kill the loop
            logger.error("Breakpoint save failed for %r: %s", name, e)
            if isinstance(cmd.get('_outcome'), dict):
                cmd['_outcome'].update({'ok': False, 'reason': str(e)})

    def _handle_load_breakpoint(self, cmd: dict) -> None:
        """Restore a previously-saved snapshot by name (defect #10).

        Accepts either a stored breakpoint ``name`` or an inline ``state`` dict.
        """
        from smo_simulator.breakpoints import BreakpointManager
        name = cmd.get('name') or ''
        state = cmd.get('state')
        if state is None:
            state = self._breakpoints.get(name)
        if state is None:
            logger.warning("Breakpoint load failed: no snapshot named %r", name)
            if isinstance(cmd.get('_outcome'), dict):
                cmd['_outcome'].update(
                    {'ok': False, 'reason': f"no snapshot named {name!r}"})
            return
        try:
            ok = BreakpointManager(self).load(state=state)
            if ok:
                # Force a fresh HK burst so the operator UI / MCS see the restored
                # values immediately rather than staying on cached, now-stale data
                # until the natural HK interval rolls around. Push each SID's HK
                # timer past its interval so the next tick emits.
                for sid, interval in self._hk_intervals.items():
                    self._hk_timers[sid] = interval + 0.001
                # Announce the load as an S5 event so downstream consumers know
                # the state was rewound (also a visible cue in the event log).
                try:
                    self._emit_event({
                        'event_id': 0x0054,
                        'severity': 2,
                        'description': f"Breakpoint loaded: {name}",
                    })
                except Exception:
                    pass
            logger.info("Breakpoint load %s: %s",
                        "succeeded" if ok else "returned False", name)
            if isinstance(cmd.get('_outcome'), dict):
                cmd['_outcome'].update(
                    {'ok': bool(ok),
                     'reason': "" if ok else "BreakpointManager.load returned False"})
        except Exception as e:  # noqa: BLE001
            logger.error("Breakpoint load failed for %r: %s", name, e)
            if isinstance(cmd.get('_outcome'), dict):
                cmd['_outcome'].update({'ok': False, 'reason': str(e)})

    def _handle_start_scenario(self, cmd: dict) -> None:
        """Start a training scenario by name (defect #9)."""
        name = cmd.get('name') or cmd.get('scenario') or ''
        se = getattr(self, '_scenario_engine', None)
        if se is None:
            logger.warning("start_scenario ignored: no scenario engine loaded")
            return
        try:
            se.start(name)
            logger.info("Scenario started: %s", name)
        except Exception as e:  # noqa: BLE001
            logger.error("start_scenario failed for %r: %s", name, e)

    def _handle_stop_scenario(self, cmd: dict) -> None:
        """Stop the active training scenario (defect #9)."""
        se = getattr(self, '_scenario_engine', None)
        if se is None:
            logger.warning("stop_scenario ignored: no scenario engine loaded")
            return
        try:
            se.stop()
            logger.info("Scenario stopped")
        except Exception as e:  # noqa: BLE001
            logger.error("stop_scenario failed: %s", e)

    def configure_separation_state(self) -> None:
        """Configure spacecraft for post-separation state.

        Sets all subsystems to their initial separation configuration:
        - Spacecraft phase: 0 (PRE_SEPARATION) → 1 (SEPARATION_TIMER)
        - Separation timer: 30 minutes (1800s)
        - EPS: Battery at 95% SoC, all switchable power lines OFF
        - AOCS: Mode OFF (0), tumbling with random rates ~1-2 deg/s
        - TCS: All heaters OFF, zones at ambient temperature (~20°C)
        - TTC: Antenna NOT deployed, beacon mode ON, PA OFF
        - OBDH: SW image = bootloader (0), not running application
        - Payload: Mode OFF (0)
        """
        # Set spacecraft phase
        self._spacecraft_phase = 1  # SEPARATION_TIMER
        self._sep_timer = self._sep_timer_duration  # 30 minutes = 1800s

        # EPS configuration
        eps = self.subsystems.get("eps")
        if eps and hasattr(eps, '_state'):
            s = eps._state
            s.bat_soc_pct = 95.0  # Freshly charged pre-launch
            # Turn off all switchable power lines
            from smo_simulator.models.eps_basic import POWER_LINE_SWITCHABLE
            for line_name in s.power_lines:
                if POWER_LINE_SWITCHABLE.get(line_name, False):
                    s.power_lines[line_name] = False
            logger.info("EPS configured: 95%% SoC, all switchable lines OFF")

        # AOCS configuration
        aocs = self.subsystems.get("aocs")
        if aocs and hasattr(aocs, '_state'):
            s = aocs._state
            s.mode = 0  # MODE_OFF — no attitude control
            # Set tumbling rates from separation impulse (~1-2 deg/s per axis)
            s.rate_roll = self.rng.uniform(0.8, 2.0) * (1 if self.rng.random() > 0.5 else -1)
            s.rate_pitch = self.rng.uniform(0.8, 2.0) * (1 if self.rng.random() > 0.5 else -1)
            s.rate_yaw = self.rng.uniform(0.8, 2.0) * (1 if self.rng.random() > 0.5 else -1)
            # Sensors and actuators unpowered at separation — operators
            # power them on during AOCS commissioning.
            s.st1_status = 0
            s.st2_status = 0
            s.st1_num_stars = 0
            s.st2_num_stars = 0
            s.st1_failed = False
            s.st2_failed = False
            s.st_valid = False
            s.st_selected = 1
            if hasattr(s, '_prev_st1_status'):
                s._prev_st1_status = 0
            if hasattr(s, '_prev_st2_status'):
                s._prev_st2_status = 0
            s.css_valid = False
            s.css_sun_x = 0.0
            s.css_sun_y = 0.0
            s.css_sun_z = 0.0
            s.mag_valid = False
            if hasattr(s, 'rw_enabled'):
                s.rw_enabled = [False] * 4
            if hasattr(s, 'active_wheels'):
                s.active_wheels = [False] * 4
            if hasattr(s, 'rw_speed'):
                s.rw_speed = [0.0] * 4
            s.mtq_enabled = False
            s.mtq_x_duty = 0.0
            s.mtq_y_duty = 0.0
            s.mtq_z_duty = 0.0
            if hasattr(s, 'gps_fix'):
                s.gps_fix = 0
            if hasattr(s, 'gps_num_sats'):
                s.gps_num_sats = 0
            logger.info("AOCS configured: mode=OFF, rates=[%.2f, %.2f, %.2f] deg/s, sensors/actuators unpowered",
                       s.rate_roll, s.rate_pitch, s.rate_yaw)

        # TCS configuration
        tcs = self.subsystems.get("tcs")
        if tcs and hasattr(tcs, '_state'):
            s = tcs._state
            # All heaters OFF
            s.htr_battery = False
            s.htr_obc = False
            s.htr_thruster = False
            # Set all zones to ambient temperature (~20°C)
            ambient = 20.0
            s.temp_panel_px = ambient
            s.temp_panel_mx = ambient
            s.temp_panel_py = ambient
            s.temp_panel_my = ambient
            s.temp_panel_pz = ambient
            s.temp_panel_mz = ambient
            s.temp_obc = ambient
            s.temp_battery = ambient
            s.temp_fpa = ambient
            s.temp_thruster = ambient
            logger.info("TCS configured: all heaters OFF, zones at %.1f°C", ambient)

        # TTC configuration
        ttc = self.subsystems.get("ttc")
        if ttc and hasattr(ttc, '_state'):
            s = ttc._state
            s.antenna_deployed = False  # Antenna NOT deployed
            s.beacon_mode = True  # Bootloader beacon mode
            s.pa_on = False  # Power amplifier OFF
            s.cmd_decode_timer = 900.0  # 15 min command decoder enable timer
            s.cmd_channel_active = True  # Command channel monitoring active
            # SAFE tier — 1 kbps, BPSK, conv(7,1/2)+RS. This is the ONLY
            # rung of the ladder the bootloader ever uses, and the only place
            # 1 kbps appears at all now (defect #85).
            s.data_rate_mode = getattr(ttc, '_beacon_tier', 0)
            s.downlink_mode = False
            if hasattr(ttc, '_apply_tier'):
                ttc._apply_tier(s, s.data_rate_mode)
            logger.info("TTC configured: antenna=NOT_DEPLOYED, beacon_mode=ON, PA=OFF, "
                        "TM tier=SAFE")

        # OBDH configuration
        obdh = self.subsystems.get("obdh")
        if obdh and hasattr(obdh, '_state'):
            s = obdh._state
            s.sw_image = 0  # SW_BOOTLOADER
            s.boot_app_pending = False
            s.boot_app_timer = 0.0
            s.cpu_load = 15.0  # Minimal CPU load (bootloader idle)
            logger.info("OBDH configured: sw_image=BOOTLOADER, not booted")

        # Payload configuration
        payload = self.subsystems.get("payload")
        if payload and hasattr(payload, '_state'):
            s = payload._state
            s.mode = 0  # MODE_OFF
            s.cooler_active = False
            logger.info("Payload configured: mode=OFF, cooler=OFF")

        # HK gating: In bootloader, only SID 11 (Beacon) is active. Full
        # subsystem HK requires application software running. Also switch TM
        # builder to bootloader APID and tear down TM stores.
        for sid in self._hk_enabled:
            self._hk_enabled[sid] = (sid == BOOTLOADER_BEACON_SID)
        try:
            self.tm_builder.set_apid(self._bootloader_apid)
        except Exception:
            pass
        if self._tm_storage is not None:
            for sid in list(self._tm_storage._stores.keys()):
                self._tm_storage.delete_store(sid)
                self._tm_storage.disable_store(sid)
        logger.info("HK gating: only SID %d enabled; bootloader APID 0x%03X; stores torn down",
                    BOOTLOADER_BEACON_SID, self._bootloader_apid)

    def _handle_failure_inject(self, cmd: dict) -> None:
        subsys = cmd.get('subsystem', '')
        failure = cmd.get('failure', '')
        magnitude = float(cmd.get('magnitude', 1.0))
        model = self.subsystems.get(subsys)
        if model:
            extra = {k: v for k, v in cmd.items()
                     if k not in ('type', 'subsystem', 'failure', 'magnitude')}
            model.inject_failure(failure, magnitude, **extra)

    def _handle_failure_clear(self, cmd: dict) -> None:
        subsys = cmd.get('subsystem', '')
        failure = cmd.get('failure', '')
        model = self.subsystems.get(subsys)
        if model:
            extra = {k: v for k, v in cmd.items()
                     if k not in ('type', 'subsystem', 'failure')}
            model.clear_failure(failure, **extra)

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def _get_eclipse_transition(self) -> dict:
        """Return eclipse state + time-to-entry/exit for the power-budget display.

        Defect D: produce `in_eclipse`, `time_to_eclipse_entry_s` and
        `time_to_eclipse_exit_s` from the orbit propagator's forward scan.
        Cached by tick so it runs at most once per state snapshot, and the
        underlying scan is coarse/bounded (2-hour horizon, 60-s step) to keep
        the cost off the hot tick loop.
        """
        cache = getattr(self, "_eclipse_transition_cache", None)
        if cache is not None and cache[0] == self._tick_count:
            return cache[1]
        try:
            result = self.orbit.next_eclipse_transition()
        except Exception:
            # Never let the display producer break state generation.
            result = {
                "in_eclipse": bool(self.orbit.state.in_eclipse),
                "time_to_eclipse_entry_s": None,
                "time_to_eclipse_exit_s": None,
            }
        self._eclipse_transition_cache = (self._tick_count, result)
        return result

    # ------------------------------------------------------------------
    # Threading contract (roadmap N-2 / DI-02)
    # ------------------------------------------------------------------
    # WRITER: the tick thread (`_run_loop` -> `_tick_once`) exclusively owns
    #   `self.params`, every subsystem `_state`, and all engine state. At the
    #   end of each tick it publishes an immutable snapshot under
    #   `_params_lock`.
    # READERS: aiohttp handlers, the WS broadcaster and the MCS poller call
    #   `get_state_summary()` / `get_instructor_snapshot()`. Both take the
    #   *published snapshot* under the same lock and never read live state, so
    #   they cannot see two fields from different ticks (a "torn" read) nor
    #   `RuntimeError: dictionary changed size during iteration`.
    # Before this the lock was acquired only by the readers, which provided
    # zero mutual exclusion while reading as if it were a design decision.

    def _publish_snapshot(self) -> None:
        """Publish this tick's immutable state snapshots for readers (N-2)."""
        try:
            snapshot = self._build_state_summary()
        except Exception as e:  # noqa: BLE001 — never let export kill the tick
            logger.warning("State snapshot build failed: %s", e)
            return
        # A-1: the instructor snapshot is built HERE, on the tick thread, with
        # deep copies. It used to be assembled on the aiohttp thread: it read
        # `self.orbit.state` live and walked every subsystem's `_state.__dict__`
        # while this thread was mutating it, storing dict/list values (power
        # lines, wheel speeds, the image catalogue) BY REFERENCE. json.dumps on
        # the response thread could raise "dictionary changed size during
        # iteration" against a growing image_catalog, and a caller mutating the
        # response would have corrupted the simulation.
        try:
            instructor = self._build_instructor_snapshot()
        except Exception as e:  # noqa: BLE001
            logger.warning("Instructor snapshot build failed: %s", e)
            instructor = None
        with self._params_lock:
            self._snapshot = snapshot
            self._snapshot_tick = self._tick_count
            if instructor is not None:
                self._instructor_snapshot = instructor

    def get_state_summary(self) -> dict:
        """Return the most recently published spacecraft state summary.

        Reader side of the threading contract above: this hands back the
        snapshot the tick thread published, so every field in it comes from the
        same tick. Before the first tick completes there is no snapshot yet, so
        one is built inline (single-threaded at that point, by construction).
        """
        with self._params_lock:
            snapshot = getattr(self, '_snapshot', None)
        if snapshot is None:
            return self._build_state_summary()
        return snapshot

    def _build_state_summary(self) -> dict:
        """Build the state summary from live state. Tick-thread only.

        Field names include unit suffixes to match hardcoded HTML UI expectations
        (e.g., power_gen_W, rssi_dbm, att_error_deg, soc_pct, etc.).
        The MCS web UI hardcodes these field names, so changing them will break
        the display. Once displays become config-driven, this can be refactored.
        """
        o = self.orbit.state
        p = dict(self.params)

        # Build state grouped by subsystem
        summary = {
            'tick': self._tick_count,
            'sim_time': self._sim_time.isoformat(),
            # MET, the single definition (spec C.2): sim_time - t0_epoch.
            # This used to export the FDIR module's accumulator.
            'met_s': self.met_s,
            'speed': self.speed,
            'sc_mode': self.sc_mode,
            'in_eclipse': bool(o.in_eclipse),
            'in_contact': bool(o.in_contact),
            'ground_station_name': getattr(o, 'active_gs', None) or '',
            'gs_elevation_deg': round(o.gs_elevation_deg, 1),
            'gs_range_km': round(o.gs_range_km, 1),
            **self._get_eclipse_transition(),
            'lat': round(o.lat_deg, 4),
            'lon': round(o.lon_deg, 4),
            'alt_km': round(o.alt_km, 2),
            # Phase D: versioned element set. epoch_version increments on
            # every applied burn; planner/MCS treat a change like a time jump
            # (drop contact/ground-track caches, adopt the new TLE).
            'orbit_epoch_version': int(getattr(self.orbit, 'epoch_version', 0)),
            'orbit_elset': (self.orbit.get_elset()
                            if hasattr(self.orbit, 'get_elset') else None),
            'eps': {
                'soc_pct': round(p.get(0x0101, 0), 1),
                'bat_voltage_V': round(p.get(0x0100, 0), 2),
                'bus_voltage_V': round(p.get(0x0105, 0), 2),
                'bat_temp_C': round(p.get(0x0102, 0), 1),
                'bat_current_A': round(p.get(0x0109, 0), 2),
                'sa_a_A': round(p.get(0x0103, 0), 2),
                'sa_b_A': round(p.get(0x0104, 0), 2),
                'power_gen_W': round(p.get(0x0107, 0), 1),
                'power_cons_W': round(p.get(0x0106, 0), 1),
                'power_lines': self._get_power_lines_state(),
            },
            'aocs': {
                'mode': int(p.get(0x020F, 0)),
                'att_error_deg': round(p.get(0x0217, 0), 3),
                'rate_roll': round(p.get(0x0204, 0), 4),
                'rate_pitch': round(p.get(0x0205, 0), 4),
                'rate_yaw': round(p.get(0x0206, 0), 4),
                'rw1_rpm': round(p.get(0x0207, 0)),
                'rw2_rpm': round(p.get(0x0208, 0)),
                'rw3_rpm': round(p.get(0x0209, 0)),
                'rw4_rpm': round(p.get(0x020A, 0)),
            },
            'tcs': {
                'temp_obc_C': round(p.get(0x0406, 0), 1),
                'temp_bat_C': round(p.get(0x0407, 0), 1),
                'temp_fpa_C': round(p.get(0x0408, 0), 1),
                'htr_bat': bool(p.get(0x040A, 0)),
                'htr_obc': bool(p.get(0x040B, 0)),
                'cooler_fpa': bool(p.get(0x040C, 0)),
            },
            'obdh': {
                'mode': int(p.get(0x0300, 0)),
                'cpu_load': round(p.get(0x0302, 0), 1),
                'mem_used_pct': round(p.get(0x0303, 0), 1),
                'reboot_count': int(p.get(0x030A, 0)),
            },
            'ttc': {
                'mode': int(p.get(0x0500, 0)),
                'link_status': int(p.get(0x0501, 0)),
                'rssi_dbm': round(p.get(0x0502, 0), 1),
                'link_margin_dB': round(p.get(0x0503, 0), 1),
                'range_km': round(p.get(0x0509, 0), 1),
                'elevation_deg': round(p.get(0x050A, 0), 1),
                # RF bridge fields
                'eb_n0_db': round(p.get(0x0519, 0), 1),
                'ber': round(p.get(0x050C, 0), 2),
                'carrier_lock': bool(p.get(0x0510, 0)),
                'bit_sync': bool(p.get(0x0511, 0)),
                'frame_sync': bool(p.get(0x0512, 0)),
                'doppler_hz': round(p.get(0x051B, 0), 1),
                'modulation_mode': int(p.get(0x0537, 0)),
                'gs_penalty_db': round(p.get(0x0538, 0), 1),
                'pa_temp_C': round(p.get(0x050F, 0), 1),
                'tx_power_W': round(p.get(0x050D, 0), 2),
                'data_rate_bps': int(p.get(0x0506, 0)),
                # TM rate ladder (defect #85): the RF bridge retunes the
                # pipeline from these, and the MCS can label the rate.
                'tm_tier': self._current_tm_tier()[0],
                'tm_tier_name': self._current_tm_tier()[1],
                'tm_coding': self._current_tm_coding(),
                'symbol_rate_bd': round(p.get(0x053B, 0), 1),
                'downlink_mode': bool(p.get(0x053C, 0)),
                # ── Transmitter state (2026-08b, findings T-a / 6, ruling R2)
                # These were all missing, which is why neither the RF bridge
                # nor the MCS *could* gate or explain on TX state, and why the
                # MCS's `_demotion_reason` antenna branch was dead code: it
                # tested a field the snapshot never carried. A console cannot
                # tell an operator "the PA is off" from data it is not given.
                'pa_on': bool(p.get(0x0516, 0)),
                'tx_active': bool(p.get(0x0525, 0)),
                'tx_gate_status': int(p.get(0x052B, 0)),
                'eirp_dbw': round(p.get(0x052C, 0), 2),
                'antenna_deployed': bool(p.get(0x0520, 0)),
                # `beacon_mode` is the S-band RATE LADDER rung; `beacon_tx_on`
                # and `beacon_active` are the separate UHF TRANSMITTER. Both
                # are published because both gate something and they are
                # routinely confused.
                'beacon_mode': bool(p.get(0x0521, 0)),
                'beacon_tx_on': bool(p.get(0x0526, 0)),
                'beacon_active': bool(p.get(0x0527, 0)),
                'beacon_margin_dB': round(p.get(0x0528, 0), 1),
                'beacon_dl_freq_mhz': round(p.get(0x0529, 0), 3),
                'beacon_tx_power_W': round(p.get(0x052A, 0), 2),
                # The EPS line the S-band chain hangs off (eps.pl_ttc_tx).
                # Mirrored here so the TT&C tab does not have to reach into
                # the EPS block to explain an inhibited downlink.
                'ttc_tx_line_on': bool(p.get(0x0112, 0)),
            },
            'payload': {
                'mode': int(p.get(0x0600, 0)),
                'fpa_temp_C': round(p.get(0x0601, 0), 1),
                'store_used_pct': round(p.get(0x0604, 0), 1),
                'image_count': int(p.get(0x0605, 0)),
            },
            'prop': {
                'state': int(p.get(0x0B00, 0)),
                'tank_pressure_bar': round(p.get(0x0B01, 0), 2),
                'propellant_g': round(p.get(0x0B02, 0), 1),
                'catbed_temp_C': round(p.get(0x0B05, 0), 1),
                'dv_accum_m_s': round(p.get(0x0B0B, 0), 3),
                'dv_remaining_m_s': round(p.get(0x0B0C, 0), 2),
                'burn_status': int(p.get(0x0B0D, 0)),
                'arm_inhibit_mask': int(p.get(0x0B15, 0)),
            },
            'spacecraft_phase': self._spacecraft_phase,
            'downlink_active': self.downlink_active,
            'uplink_active': self.uplink_active,
            'override_passes': self._override_passes,
            'active_failures': self._failure_manager.active_failures(),
            'scheduler': self._tc_scheduler.get_status(),
            'tm_stores': self._tm_storage.get_status(),
            # Tick-loop health (roadmap N-1): non-null means a tick raised and
            # the loop recovered — telemetry is stale-but-live, not frozen.
            'engine_fault': getattr(self, 'engine_fault', None),
            'engine_fault_count': getattr(self, 'engine_fault_count', 0),
            'hk_enabled': dict(getattr(self, '_hk_enabled', {}) or {}),
            'tm_rate_bps': int(self._current_tm_rate_bps()),
            'tm_tier': self._current_tm_tier()[0],
            'tm_tier_name': self._current_tm_tier()[1],
            'rt_tm_backlog': len(getattr(self, '_rt_pending', None) or []),
            # Autonomy picture (WP-5): active faults, pending cascades and the
            # running recovery procedure. Same route as `sc_mode` — not
            # downlinked telemetry, so the console tags it as coming from the
            # simulator rather than from the spacecraft.
            'fdir': self.fdir_status(),
            # Include raw params dict for MCS to access via param(id) function
            'params': p,
        }
        return summary

    def _get_power_lines_state(self) -> dict[str, bool]:
        """Get power line states from EPS model."""
        eps = self.subsystems.get("eps")
        if eps and hasattr(eps, '_state') and hasattr(eps._state, 'power_lines'):
            return dict(eps._state.power_lines)
        return {}

    def get_instructor_snapshot(self) -> dict:
        """Return complete ground-truth state for instructor display.

        Reader side of the threading contract (see `_publish_snapshot`): this
        hands back the snapshot the TICK thread built, already deep-copied, so
        an aiohttp handler never touches live state (A-1). Before the first
        tick completes there is no snapshot yet, so one is built inline —
        single-threaded at that point, by construction.
        """
        with self._params_lock:
            published = getattr(self, '_instructor_snapshot', None)
        if published is not None:
            return published
        return self._build_instructor_snapshot()

    def _build_instructor_snapshot(self) -> dict:
        """Build the instructor snapshot from live state. Tick-thread only.

        Instructor has god-mode visibility, so this bypasses RF link gating and
        returns every parameter + every subsystem model's internal state.
        """
        import copy as _copy
        o = self.orbit.state
        with self._params_lock:
            snapshot = getattr(self, '_snapshot', None)
        p = dict(snapshot['params']) if snapshot else dict(self.params)

        # Build comprehensive snapshot with all subsystem internals + params
        out = {
            'meta': {
                'timestamp': self._sim_time.isoformat(),
                'tick': self._tick_count,
                'speed': self.speed,
                'spacecraft_phase': self._spacecraft_phase,
            },
            'orbit': {
                'lat_deg': round(o.lat_deg, 4),
                'lon_deg': round(o.lon_deg, 4),
                'alt_km': round(o.alt_km, 2),
                'in_eclipse': bool(o.in_eclipse),
                'in_contact': bool(o.in_contact),
                'active_gs': getattr(o, 'active_gs', None),
                'semi_major_axis_km': round(o.semi_major_axis_km, 2) if hasattr(o, 'semi_major_axis_km') else None,
                'eccentricity': round(o.eccentricity, 6) if hasattr(o, 'eccentricity') else None,
                'inclination_deg': round(o.inclination_deg, 4) if hasattr(o, 'inclination_deg') else None,
                'raan_deg': round(o.raan_deg, 4) if hasattr(o, 'raan_deg') else None,
                'arg_perigee_deg': round(o.arg_perigee_deg, 4) if hasattr(o, 'arg_perigee_deg') else None,
                'true_anomaly_deg': round(o.true_anomaly_deg, 4) if hasattr(o, 'true_anomaly_deg') else None,
            },
            'spacecraft': {
                'mode': self.sc_mode,
                'downlink_active': self.downlink_active,
                'uplink_active': self.uplink_active,
                'override_passes': self._override_passes,
            },
            'parameters': p,  # All raw parameters by ID
            'subsystems': self._get_all_subsystem_states(),
            # deepcopy: both of these can hand back live model containers, and
            # the reader is a different thread (A-1).
            'tm_stores': _copy.deepcopy(self._tm_storage.get_status())
                         if hasattr(self, '_tm_storage') else {},
            'active_failures': _copy.deepcopy(
                self._failure_manager.active_failures())
                if hasattr(self, '_failure_manager') else [],
            'fdir': self.fdir_status(),
            # Ground truth for the clock exercise. INSTRUCTOR ONLY — see
            # _build_clock_truth(); never add this to _build_state_summary().
            'clock_truth': self._build_clock_truth(),
            'engine_fault': self.engine_fault,
            'engine_fault_count': self.engine_fault_count,
            'instr_cmd_rejects': self.instr_cmd_rejects,
            'hk_enabled': dict(self._hk_enabled),
        }
        return out

    def _build_clock_truth(self) -> dict:
        """The onboard-vs-sim clock ground truth. **Instructor console only.**

        Spec Part B, last paragraph: the true onboard-vs-sim offset must NOT be
        downlinked — the spacecraft cannot know it, and the students' whole job
        in NOM-008 is to measure it from the correlation. It is exposed here,
        on the instructor snapshot, as the answer key: the offset the OBC clock
        currently carries, together with the drift ppm actually drawn for this
        run (a seeded per-unit draw, so it differs run to run).

        Deliberately NOT in `_build_state_summary()`, which is what the MCS
        polls and what feeds every ground display; and nothing here is a
        telemetry parameter, so it cannot reach a TM packet.

        `offset_s` is positive when the OBC clock is AHEAD of sim time.
        """
        from smo_common.protocol.ecss_packet import datetime_to_cuc_seconds

        sim_cuc_s = float(datetime_to_cuc_seconds(self._sim_time))
        obdh = self.subsystems.get("obdh")
        obc_time_s = self._obdh_clock_seconds()

        out: dict = {
            'sim_cuc_s': sim_cuc_s,
            'sim_time': self._sim_time.isoformat(),
            'obc_time_s': obc_time_s,
            # The answer key: OBC clock minus sim time, in seconds.
            'offset_s': (None if obc_time_s is None
                         else obc_time_s - sim_cuc_s),
            'drift_ppm': None,
            'time_ref': None,
            'time_ref_name': None,
            'time_since_sync_s': None,
            'gps_disciplined': False,
        }
        if obdh is None:
            return out

        st = getattr(obdh, '_state', None)
        if st is not None:
            out['drift_ppm'] = float(getattr(st, 'clock_drift_ppm', 0.0) or 0.0)
        ref = getattr(obdh, 'get_time_ref', None)
        if callable(ref):
            try:
                from smo_simulator.models.obdh_basic import (
                    TIME_REF_GPS, TIME_REF_NAMES)
                r = int(ref())
                out['time_ref'] = r
                out['time_ref_name'] = TIME_REF_NAMES.get(r, 'UNKNOWN')
                out['gps_disciplined'] = (r == TIME_REF_GPS)
            except Exception:  # noqa: BLE001 — never break the snapshot
                logger.debug("time_ref read failed", exc_info=True)
        tss = getattr(obdh, 'time_since_sync_s', None)
        if callable(tss):
            try:
                out['time_since_sync_s'] = tss()
            except Exception:  # noqa: BLE001
                logger.debug("time_since_sync read failed", exc_info=True)
        return out

    def fdir_status(self) -> dict:
        """The FDIR picture the ground displays render (WP-5).

        `active_faults`, `recovery` and `procedures` are new: the FDIR window
        could only ever show S12 transitions, S19 firings and command
        rejections, because the fault-cascade model and the ten recovery
        procedures behind it never ran.
        """
        recoveries = self._fault_propagator.recovery_states()
        return {
            'enabled': self._fdir_enabled,
            'triggered_rules': dict(self._fdir_triggered),
            'load_shed_stage': self._current_load_shed_stage,
            'level': int(self.params.get(0x0700, 0) or 0),
            'active_faults': self._fault_propagator.active_faults(),
            'pending_cascades': [
                {'fault_id': c.get('fault_id'),
                 'target': c.get('target_subsystem'),
                 'action': c.get('action'),
                 'due_in_s': round(max(0.0, c.get('due', 0.0)
                                       - self._fdir_elapsed_s), 1)}
                for c in self._pending_cascades
            ],
            'recovery': [
                {'fault_id': fid, 'group': rs.group, 'level': rs.level,
                 'state': rs.state, 'procedure': rs.procedure,
                 'attempts': rs.recovery_attempts,
                 'max_attempts': rs.max_attempts,
                 'exec_id': rs.exec_id}
                for fid, rs in recoveries.items()
            ],
            'procedures': self._procedure_executor.get_active_procedures(),
        }

    def _get_all_subsystem_states(self) -> dict:
        """Extract complete internal state from all subsystem models.

        Tick-thread only (A-1). Container values are DEEP-COPIED: they used to
        be stored by reference, so `power_lines`, `rw_speed` and the growing
        `image_catalog` were handed to the aiohttp thread as live objects.
        """
        import copy
        states = {}
        for name, model in self.subsystems.items():
            if hasattr(model, '_state'):
                state_obj = model._state
                # Convert dataclass to dict
                if hasattr(state_obj, '__dict__'):
                    state_dict = {}
                    for key, value in list(state_obj.__dict__.items()):
                        # Skip private fields
                        if key.startswith('_'):
                            continue
                        # Convert complex types to simple types for JSON
                        if isinstance(value, (dict, list, tuple, set)):
                            try:
                                state_dict[key] = copy.deepcopy(value)
                            except Exception:  # noqa: BLE001
                                state_dict[key] = str(value)
                        elif isinstance(value, bool):
                            state_dict[key] = value
                        elif isinstance(value, (int, float)):
                            state_dict[key] = value
                        else:
                            state_dict[key] = str(value)
                    states[name] = state_dict
                else:
                    states[name] = str(state_obj)
            else:
                states[name] = {}
        return states

    def set_hk_enabled(self, sid: int, enabled: bool) -> None:
        self._hk_enabled[sid] = enabled

    def set_hk_interval(self, sid: int, interval_s: float) -> None:
        self._hk_intervals[sid] = interval_s

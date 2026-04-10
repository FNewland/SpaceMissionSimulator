"""SMO Simulator — Simulation Engine.

Config-driven central tick loop. Loads subsystem models from the registry,
manages shared parameter store, PUS packet queues, and FDIR callbacks.
"""
import threading
import time
import queue
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from smo_common.config.loader import (
    load_mission_config, load_orbit_config, load_all_subsystem_configs,
    load_fdir_config, load_hk_structures,
)
from smo_common.orbit.propagator import OrbitPropagator, GroundStation
from smo_common.models.registry import create_model
from smo_common.telemetry.tm_builder import TMBuilder
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


class SimulationEngine:
    """Config-driven simulation engine.

    Reads mission config, instantiates subsystem models from registry,
    runs tick loop, manages shared parameter store and packet queues.
    """

    def __init__(self, config_dir: str | Path, speed: float = 1.0):
        self.config_dir = Path(config_dir)
        self.speed = speed
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

        self._sim_time = datetime.now(timezone.utc)

        # Load configs
        self._mission_cfg = load_mission_config(self.config_dir)
        self._orbit_cfg = load_orbit_config(self.config_dir)
        self._subsys_configs = load_all_subsystem_configs(self.config_dir)
        self._fdir_cfg = load_fdir_config(self.config_dir)

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

        # Instantiate subsystem models from config
        self.subsystems: dict[str, Any] = {}
        for name, cfg in self._subsys_configs.items():
            model_name = cfg.model if hasattr(cfg, 'model') else f"{name}_basic"
            try:
                model = create_model(model_name, cfg.model_dump() if hasattr(cfg, 'model_dump') else {})
                self.subsystems[name] = model
                logger.info("Loaded subsystem model: %s (%s)", name, model_name)
            except Exception as e:
                logger.warning("Failed to load model %s for %s: %s", model_name, name, e)

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
            initial_orbit_state = self.orbit.advance(0.0)
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
        for hk in self._hk_structures_raw:
            params_list = [(p.param_id, p.pack_format, p.scale) for p in hk.parameters]
            self._hk_structures[hk.sid] = params_list
            self._hk_intervals[hk.sid] = hk.interval_s
            self._hk_timers[hk.sid] = 0.0
            self._hk_enabled[hk.sid] = True

        # FDIR state
        self.sc_mode = 0  # 0=nominal, 1=safe, 2=emergency
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
        self._sim_elapsed_fdir = 0.0
        self._load_fault_propagation_config()

        # Transition tracking
        self._prev_in_contact = False
        self._prev_in_eclipse = False
        self._in_contact = False  # current contact state for TM gating

        # TC Scheduler (S11)
        self._tc_scheduler = TCScheduler()

        # Onboard TM Storage (S15)
        self._tm_storage = OnboardTMStorage()
        # S15 dump pacing: pending packets released according to TTC data rate.
        # Each item: (pkt_bytes, store_id)
        self._dump_pending: list[tuple[bytes, int]] = []
        self._dump_byte_budget: float = 0.0
        # Stores currently being dumped (auto-cleared on completion)
        self._dump_active_stores: set[int] = set()

        # Subsystem event edge-detection
        self._prev_aocs_mode: Optional[int] = None
        self._prev_payload_mode: Optional[int] = None
        self._prev_obdh_mode: Optional[int] = None
        self._event_flags: dict[str, bool] = {}

        # Spacecraft phase state machine
        # Default NOMINAL (6) so existing tests that instantiate without the
        # eosat1 mission config behave as before. If mission config requests
        # `start_in_bootloader`, we drop to phase 3 below after storage init.
        self._spacecraft_phase = 6   # 0=PRE_SEP,1=SEP_TIMER,2=INIT_PWR,3=BOOT,4=LEOP,5=COMM,6=NOM
        self._sep_timer = 0.0        # separation timer countdown (seconds)
        self._sep_timer_duration = 1800.0  # 30 minutes default

        # Auto-TX hold-down. On reception of any accepted TC, the platform
        # autonomously powers the TTC TX line on for AUTO_TX_HOLD_S seconds
        # so the operator gets a downlink for at least one ground pass after
        # the first valid uplink. Each new accepted TC re-arms the timer.
        # This mirrors a common LEOP autonomy: "if I just heard a good
        # command, the ground is talking to me — go ahead and reply".
        self._auto_tx_hold_s: float = 15.0 * 60.0   # 15 minutes
        self._auto_tx_remaining: float = 0.0

        # S12 monitoring: edge-triggered violation tracking.  Only the
        # transition into violation (OK → violated) emits an S5 event.
        self._s12_violation_active: set[int] = set()

        # Failure manager
        from smo_simulator.failure_manager import FailureManager
        self._failure_manager = FailureManager(
            inject_fn=self._handle_failure_inject,
            clear_fn=self._handle_failure_clear,
        )

        # Service dispatcher (persistent instance for S12/S19 state)
        from smo_simulator.service_dispatch import ServiceDispatcher
        self._dispatcher = ServiceDispatcher(self)

        # Load S12/S19 configurations at startup
        self._load_monitoring_configs()

        # Wire FDIR callbacks
        self._fdir_callbacks: dict[str, Any] = {}
        self._wire_fdir()
        self._wire_load_shedding()
        self._wire_procedure_executor()

        # If mission config requests bootloader boot-up, put the spacecraft
        # into pure beacon mode right now: phase 3, bootloader APID, only
        # SID 11 enabled, all TM stores empty & disabled, AOCS/payload/TCS
        # not ticking (handled by phase gate in main loop).
        if getattr(self._mission_cfg, 'start_in_bootloader', True):
            self._enter_bootloader_mode()

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
            t.data_rate_mode = 0

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

        # Re-enable all HK SIDs (application has full telemetry access)
        for sid in self._hk_enabled:
            self._hk_enabled[sid] = True

        # TM stores come online (empty but enabled)
        if self._tm_storage is not None:
            for sid in list(self._tm_storage._stores.keys()):
                self._tm_storage.enable_store(sid)
        logger.info("Application booted: APID=0x%03X, all HK SIDs enabled, stores online",
                    self._application_apid)

    def _get_cuc_time(self) -> int:
        obdh = self.subsystems.get("obdh")
        if obdh and hasattr(obdh, '_state'):
            return getattr(obdh._state, 'obc_time_cuc', 0)
        return 0

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
            self._fdir_callbacks["safe_mode_aocs"] = lambda: aocs.handle_command({"command": "set_mode", "mode": 2})
            for i in range(4):
                self._fdir_callbacks[f"disable_rw{i+1}"] = (lambda idx=i: aocs.handle_command({"command": "disable_wheel", "wheel": idx}))
        if obdh:
            self._fdir_callbacks["safe_mode_obc"] = lambda: obdh.handle_command({"command": "set_mode", "mode": 1})
        if eps:
            self._fdir_callbacks["safe_mode_eps"] = lambda: eps.handle_command({"command": "set_mode", "mode": 1})
        self._fdir_callbacks["spacecraft_emergency"] = lambda: setattr(self, 'sc_mode', 2)

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
                )
                self._fault_propagator.register_rule(rule)

            # Register load shedding stages
            for stage_key, stage_cfg in config.get("load_shedding_stages", {}).items():
                stage_num = int(stage_key.split("_")[-1])
                self._load_shedding.register_stage_config(stage_num, stage_cfg)

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
            self._load_shedding.register_callback(
                "tcs_heater_duty",
                lambda val: tcs.handle_command({"command": "heater_duty", "value": float(val)})
            )

        if ttc:
            self._load_shedding.register_callback(
                "ttc_power_level",
                lambda val: ttc.handle_command({"command": "power_level", "value": int(val)})
            )

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
        """Advanced FDIR tick: handle fault propagation, load shedding, recovery."""
        self._sim_elapsed_fdir += dt

        # Check load shedding based on battery SoC
        eps = self.subsystems.get("eps")
        if eps and hasattr(eps, '_state'):
            soc = getattr(eps._state, 'soc', 100.0)
            new_stage = self._load_shedding.update_stage(soc, self._sim_elapsed_fdir)
            if new_stage is not None:
                self._current_load_shed_stage = new_stage
                self._load_shedding.execute_stage(new_stage)
                self._emit_event({
                    'event_id': 0x0F05,  # LOAD_SHED_ACTIVATED
                    'severity': 2,
                    'description': f"Load shedding activated: {self._load_shedding.stage_name(new_stage)}",
                })

        # Tick procedure executor
        self._procedure_executor.tick_procedures(self._sim_elapsed_fdir)

    # ------------------------------------------------------------------
    # Spacecraft phase state machine
    # ------------------------------------------------------------------

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
        """TM downlink: requires orbital contact AND TTC link_active, OR override.

        Downlink requires:
        1. Spacecraft in orbital view with ground station (_in_contact), AND
        2. TTC transponder/PA functional (param 0x0501), OR
        3. Instructor override (pass prediction) is active

        Note: During startup, TTC may not have ticked yet, so we default to True
        to ensure HK is emitted. The actual link status (0x0501) gates RF transmission
        in the real hardware, but in simulation we always want to queue TM for downlink.

        This mirrors uplink_active which also requires BOTH contact AND link.
        """
        # Check TTC link status
        link_status = self.params.get(0x0501)
        ttc_link_ok = link_status if link_status is not None else True

        # Downlink requires: (orbital contact AND TTC link OK) OR override
        return ((self._in_contact and bool(ttc_link_ok)) or self._override_passes)

    @property
    def uplink_active(self) -> bool:
        """TC uplink: requires orbital contact AND TTC link status OK, OR override.

        Checks both orbital contact and TTC transponder/PA status via param 0x0501.
        TTC failures (transponder, PA off, antenna stowed, wrong band, etc.) block
        uplink even if spacecraft is in orbital view.

        Override (pass prediction forced by instructor) allows uplink regardless.
        """
        # Check if TTC link is active via param 0x0501 (set by TTC model)
        # Default to True during startup before TTC ticks
        link_status = self.params.get(0x0501)
        ttc_link_ok = link_status if link_status is not None else True

        # Uplink requires: (orbital contact AND TTC link OK) OR override
        return ((self._in_contact and bool(ttc_link_ok)) or self._override_passes)

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="sim-engine")
        self._thread.start()
        logger.info("SimulationEngine started (speed=%.1fx)", self.speed)

    def stop(self) -> None:
        self.running = False
        logger.info("SimulationEngine stopping")

    def _run_loop(self) -> None:
        tick_hz = 1.0  # default
        dt_real = 1.0 / tick_hz
        last_wall = time.monotonic()

        while self.running:
            dt_sim = dt_real * self.speed

            # Process queues
            self._drain_instr_queue()
            self._drain_tc_queue()

            # Execute time-tagged commands from scheduler
            current_cuc = self._get_cuc_time()
            due_tcs = self._tc_scheduler.tick(current_cuc)
            for tc_pkt in due_tcs:
                self._dispatch_tc(tc_pkt)

            # Advance orbit
            orbit_state = self.orbit.advance(dt_sim)
            self._in_contact = orbit_state.in_contact
            self.params[0x05FF] = 1 if self._override_passes else 0

            # Spacecraft phase state machine (must run before subsystem ticks)
            self._tick_spacecraft_phase(dt_sim)

            # Auto-TX hold-down decay (set by _dispatch_tc on accepted TCs).
            self._tick_auto_tx_hold(dt_sim)

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

            for name, model in self.subsystems.items():
                if name not in active_subsystems:
                    continue
                try:
                    model.tick(dt_sim, orbit_state, self.params)
                except Exception as e:
                    logger.warning("Subsystem %s tick error: %s", name, e)

            # Cross-subsystem coupling
            eps = self.subsystems.get("eps")
            tcs = self.subsystems.get("tcs")
            if eps and tcs and hasattr(eps, 'set_bat_ambient_temp') and hasattr(tcs, 'get_battery_temp'):
                eps.set_bat_ambient_temp(tcs.get_battery_temp())

            # S12 on-board monitoring — check parameter limits
            self._tick_s12_monitoring()

            # FDIR
            if self._fdir_enabled:
                self._tick_fdir()
                self._tick_fdir_advanced(dt_sim)

            # Subsystem event generation
            self._check_subsystem_events()

            # Transitions
            self._check_transitions(orbit_state)

            # HK emission
            self._emit_hk_packets(dt_sim)

            # S15 paced dump emission (after HK so real-time HK gets priority)
            self._tick_dump_emission(dt_sim)

            # Failure manager
            self._failure_manager.tick(dt_sim)

            self._tick_count += 1
            self._sim_time += timedelta(seconds=dt_sim)

            # Rate control
            elapsed = time.monotonic() - last_wall
            sleep_t = max(0.0, dt_real - elapsed)
            if sleep_t > 0:
                time.sleep(sleep_t)
            last_wall = time.monotonic()

    # ------------------------------------------------------------------
    # FDIR
    # ------------------------------------------------------------------

    def _tick_fdir(self) -> None:
        for rule in self._fdir_rules:
            param_name = rule.parameter
            threshold = rule.threshold
            if threshold is None:
                continue

            # Resolve param name to ID
            param_id = self._resolve_param_name(param_name)
            if param_id is None:
                continue

            value = self.params.get(param_id, 0.0)
            try:
                value = float(value)
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

            if condition_met and not was_triggered:
                self._fdir_triggered[rule_key] = True
                action = rule.action
                cb = self._fdir_callbacks.get(action)
                if cb:
                    try:
                        cb()
                    except Exception as e:
                        logger.warning("FDIR action %s error: %s", action, e)

                if rule.level >= 2 and self.sc_mode == 0:
                    self.sc_mode = 1
                if rule.level >= 3:
                    self.sc_mode = 2

                self._emit_event({
                    'event_id': 0x8000 | (param_id & 0x0FFF),
                    'severity': rule.level + 1,
                    'description': f"FDIR L{rule.level}: {action} | param={param_name} val={value:.2f}",
                })
            elif not condition_met and was_triggered:
                self._fdir_triggered[rule_key] = False

    def _resolve_param_name(self, name: str) -> Optional[int]:
        """Resolve dotted param name to param ID using subsystem configs."""
        # Simple lookup from known param_ids in subsystem configs
        parts = name.split(".", 1)
        if len(parts) != 2:
            return None
        subsys_name, param_key = parts
        cfg = self._subsys_configs.get(subsys_name)
        if cfg and hasattr(cfg, 'model_dump'):
            d = cfg.model_dump()
            pids = d.get("param_ids", {})
            return pids.get(param_key)
        return None

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
                        rej = self.tm_builder.build_verification_failure(
                            pkt.primary.apid, pkt.primary.sequence_count, 0x0005)
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
        known_services = {3, 5, 6, 8, 9, 11, 12, 15, 17, 19, 20}
        if service not in known_services:
            return False, 0x0001

        valid_subtypes = {
            3: {1, 2, 3, 4, 5, 6, 27, 31},
            5: {5, 6, 7, 8},
            6: {2, 5, 9},
            8: {1},
            9: {1, 2},
            11: {4, 7, 9, 11, 13, 17},
            12: {1, 2, 6, 7},
            15: {1, 2, 9, 11, 13},
            17: {1},
            19: {1, 2, 4, 5, 8},
            20: {1, 3},
        }
        if service in valid_subtypes and subtype not in valid_subtypes[service]:
            return False, 0x0002

        # S8 data length check
        if service == 8 and subtype == 1 and len(data) < 1:
            return False, 0x0003

        return True, 0

    def _dispatch_tc(self, raw: bytes) -> None:
        from smo_common.protocol.ecss_packet import decommutate_packet
        pkt = decommutate_packet(raw)
        if pkt is None or pkt.secondary is None:
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
                # TTC antenna deploy (69) for VHF/UHF antenna release
                if func_id in {19, 20, 52, 53, 54, 55, 56, 57, 61, 69}:
                    allowed = True
            if not allowed:
                # Reject at acceptance — no S1.3/S1.7 is emitted.
                rej = self.tm_builder.build_verification_failure(
                    pkt.primary.apid, pkt.primary.sequence_count, 0x0006)
                self._enqueue_tm(rej)
                logger.info(
                    "Bootloader: rejected TC S%d.%d (phase=%d, not in allowed subset)",
                    svc, sub, int(self._spacecraft_phase))
                return

        # Check acceptance
        accepted, error_code = self._check_tc_acceptance(svc, sub, pkt.data_field)
        if not accepted:
            # Send S1.2 acceptance failure
            rej = self.tm_builder.build_verification_failure(
                pkt.primary.apid, pkt.primary.sequence_count, error_code)
            self._enqueue_tm(rej)
            if obdh and hasattr(obdh, 'record_tc_rejected'):
                obdh.record_tc_rejected()
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
            rej = self.tm_builder.build_verification_failure(
                pkt.primary.apid, pkt.primary.sequence_count, 0x0004)
            self._enqueue_tm(rej)
            if obdh and hasattr(obdh, 'record_tc_rejected'):
                obdh.record_tc_rejected()
            logger.info("TC rejected at acceptance — %s", reason)
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

        # Execute
        dispatcher._last_error = None
        dispatcher._last_error_code = 0
        responses = dispatcher.dispatch(svc, sub, pkt.data_field, pkt.primary)
        for resp_pkt in responses:
            self._enqueue_tm(resp_pkt)

        # Check if dispatcher signaled an error
        if getattr(dispatcher, '_last_error', None):
            fail = self.tm_builder.build_execution_failure(
                pkt.primary.apid, pkt.primary.sequence_count,
                getattr(dispatcher, '_last_error_code', 0x0001))
            self._enqueue_tm(fail)
        else:
            # Send S1.7 execution complete
            comp = self.tm_builder.build_verification_completion(pkt.primary.apid, pkt.primary.sequence_count)
            self._enqueue_tm(comp)

        if obdh and hasattr(obdh, 'record_tc_executed'):
            obdh.record_tc_executed()

    # ------------------------------------------------------------------
    # HK emission
    # ------------------------------------------------------------------

    def _emit_hk_packets(self, dt_sim: float) -> None:
        # Bootloader HK gating: only emit minimal SIDs when in bootloader
        sw_image = int(self.params.get(0x0311, 1))
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
        if orbit_state.in_contact and not self._prev_in_contact:
            self._emit_event({'event_id': 0x0001, 'severity': 1,
                              'description': f"AOS — elevation {orbit_state.gs_elevation_deg:.1f}°"})
        elif not orbit_state.in_contact and self._prev_in_contact:
            self._emit_event({'event_id': 0x0002, 'severity': 1, 'description': "LOS"})
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

            # Generate S5 event for the violation (transition IN)
            event_id = 0x9000 | (param_id & 0x0FFF)

            self._emit_event({
                'event_id': event_id,
                'severity': severity,
                'description': (
                    f"{name}: {description} | "
                    f"value={value:.2f} limits=[{low_limit:.2f}, {high_limit:.2f}]"
                ),
            })

        # Emit "return to nominal" events for violations that have cleared
        cleared = self._s12_violation_active - currently_violated
        for mon_id in cleared:
            self._emit_event({
                'event_id': 0xA000 | (mon_id & 0x0FFF),
                'severity': 1,  # INFO
                'description': f"S12 mon {mon_id}: parameter returned to nominal",
            })

        self._s12_violation_active = currently_violated

    def _emit_event(self, ev: dict) -> None:
        event_id = ev.get('event_id', 0)
        severity = ev.get('severity', 1)
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
                    self._tm_storage.store_alarm(pkt, timestamp=self._sim_time.timestamp())
                except Exception:
                    pass
            try:
                self.event_queue.put_nowait(ev)
            except queue.Full:
                pass

            # S19 event-action: trigger any matching event-action rules
            # Pass the actual event_id so S19 can match it properly
            self._dispatcher.trigger_event_action(event_id)

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
        for pkt in packets:
            self._dump_pending.append((pkt, store_id))
        self._dump_active_stores.add(store_id)

        # Archive raw dump for between-pass processing
        try:
            import struct as _struct
            from datetime import datetime as _dt
            dump_dir = Path("workspace/dumps")
            dump_dir.mkdir(parents=True, exist_ok=True)
            ts = _dt.utcnow().strftime("%Y%m%dT%H%M%SZ")
            path = dump_dir / f"dump_sid{store_id:02d}_{ts}.bin"
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

        # Resolve current TM data rate (bps) from TTC subsystem, fall back to 1 kbps
        rate_bps = 1000.0
        ttc = self.subsystems.get("ttc")
        if ttc is not None and hasattr(ttc, "_state"):
            rate_bps = float(getattr(ttc._state, "tm_data_rate", 1000) or 1000)

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

        # AOCS mode change
        aocs_mode = int(p.get(0x020F, 0))
        if self._prev_aocs_mode is not None and aocs_mode != self._prev_aocs_mode:
            modes = {0: 'OFF', 1: 'DETUMBLE', 2: 'SAFE', 3: 'NADIR', 4: 'TARGET'}
            self._emit_event({
                'event_id': 0x0200,
                'severity': 2 if aocs_mode <= 2 else 1,
                'description': f"AOCS mode: {modes.get(self._prev_aocs_mode, '?')} -> {modes.get(aocs_mode, '?')}",
            })
        self._prev_aocs_mode = aocs_mode

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

    def _enqueue_tm(self, pkt: bytes) -> None:
        # Only downlink TM when RF link is active (orbit contact + transponder OK)
        if self.downlink_active:
            try:
                self.tm_queue.put_nowait(pkt)
            except queue.Full:
                pass
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
                    self._tm_storage.store_packet(parsed.secondary.service, pkt)

    # ------------------------------------------------------------------
    # Instructor commands
    # ------------------------------------------------------------------

    def _drain_instr_queue(self) -> None:
        while not self.instr_queue.empty():
            try:
                cmd = self.instr_queue.get_nowait()
                self._handle_instructor_cmd(cmd)
            except queue.Empty:
                break

    def _handle_instructor_cmd(self, cmd: dict) -> None:
        t = cmd.get('type')
        if t == 'set_speed':
            self.speed = float(cmd.get('value', 1.0))
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
            self._override_passes = bool(cmd.get('enabled', False))
        elif t == 'failure_clear':
            fid = cmd.get('failure_id')
            if fid:
                self._failure_manager.clear(fid)
            else:
                self._failure_manager.clear_all()
        elif t == 'set_phase':
            new_phase = int(cmd.get('phase', 6))
            if 0 <= new_phase <= 6:
                old_phase = self._spacecraft_phase
                self._spacecraft_phase = new_phase
                if new_phase == 1:
                    self._sep_timer = self._sep_timer_duration
                # Gate HK SIDs, APID, and stores based on new phase
                if new_phase <= 3:
                    # Bootloader/beacon mode: only SID 11 active, bootloader APID
                    self._enter_bootloader_mode()
                    self._spacecraft_phase = new_phase
                else:
                    # Application mode: all HK enabled, application APID, stores online.
                    # Force OBDH sw_image to APPLICATION so the reboot-detection
                    # logic in _tick_spacecraft_phase doesn't immediately revert
                    # us back to bootloader on the next tick.
                    obdh = self.subsystems.get("obdh")
                    if obdh and hasattr(obdh, '_state'):
                        obdh._state.sw_image = 1
                        if hasattr(obdh._state, 'boot_app_pending'):
                            obdh._state.boot_app_pending = False
                    if old_phase <= 3:
                        self._exit_bootloader_mode()
                    for sid in self._hk_enabled:
                        self._hk_enabled[sid] = True
                self._emit_event({
                    'event_id': 0x0052,
                    'severity': 2,
                    'description': f"Phase change: {old_phase} -> {new_phase}",
                })
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
        import random

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
            s.rate_roll = random.uniform(0.8, 2.0) * (1 if random.random() > 0.5 else -1)
            s.rate_pitch = random.uniform(0.8, 2.0) * (1 if random.random() > 0.5 else -1)
            s.rate_yaw = random.uniform(0.8, 2.0) * (1 if random.random() > 0.5 else -1)
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
            s.data_rate_mode = 0  # Low-rate (1 kbps) in beacon mode
            logger.info("TTC configured: antenna=NOT_DEPLOYED, beacon_mode=ON, PA=OFF")

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

    def get_state_summary(self) -> dict:
        """Return spacecraft state summary with parameters mapped by subsystem.

        Field names include unit suffixes to match hardcoded HTML UI expectations
        (e.g., power_gen_W, rssi_dbm, att_error_deg, soc_pct, etc.).
        The MCS web UI hardcodes these field names, so changing them will break
        the display. Once displays become config-driven, this can be refactored.
        """
        o = self.orbit.state
        with self._params_lock:
            p = dict(self.params)

        # Build state grouped by subsystem
        summary = {
            'tick': self._tick_count,
            'sim_time': self._sim_time.isoformat(),
            'met_s': self._sim_elapsed_fdir,
            'speed': self.speed,
            'sc_mode': self.sc_mode,
            'in_eclipse': bool(o.in_eclipse),
            'in_contact': bool(o.in_contact),
            'lat': round(o.lat_deg, 4),
            'lon': round(o.lon_deg, 4),
            'alt_km': round(o.alt_km, 2),
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
            },
            'payload': {
                'mode': int(p.get(0x0600, 0)),
                'fpa_temp_C': round(p.get(0x0601, 0), 1),
                'store_used_pct': round(p.get(0x0604, 0), 1),
                'image_count': int(p.get(0x0605, 0)),
            },
            'spacecraft_phase': self._spacecraft_phase,
            'downlink_active': self.downlink_active,
            'uplink_active': self.uplink_active,
            'override_passes': self._override_passes,
            'active_failures': self._failure_manager.active_failures(),
            'scheduler': self._tc_scheduler.get_status(),
            'tm_stores': self._tm_storage.get_status(),
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

        Instructor has god-mode visibility, so this bypasses RF link gating and
        returns every parameter + every subsystem model's internal state directly.
        Used by the instructor/operator UI to show complete visibility.
        """
        o = self.orbit.state
        with self._params_lock:
            p = dict(self.params)

        # Build comprehensive snapshot with all subsystem internals + params
        snapshot = {
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
            'tm_stores': self._tm_storage.get_status() if hasattr(self, '_tm_storage') else {},
            'active_failures': self._failure_manager.active_failures() if hasattr(self, '_failure_manager') else [],
            'fdir': {
                'enabled': self._fdir_enabled,
                'triggered_rules': dict(self._fdir_triggered),
                'load_shed_stage': self._current_load_shed_stage,
            },
        }
        return snapshot

    def _get_all_subsystem_states(self) -> dict:
        """Extract complete internal state from all subsystem models."""
        states = {}
        for name, model in self.subsystems.items():
            if hasattr(model, '_state'):
                state_obj = model._state
                # Convert dataclass to dict
                if hasattr(state_obj, '__dict__'):
                    state_dict = {}
                    for key, value in state_obj.__dict__.items():
                        # Skip private fields
                        if not key.startswith('_'):
                            # Convert complex types to simple types for JSON serialization
                            if isinstance(value, (dict, list)):
                                state_dict[key] = value
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

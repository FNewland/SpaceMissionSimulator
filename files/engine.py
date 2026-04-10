"""
EO Mission Simulator — Simulation Engine
Central tick loop, subsystem registry, shared parameter store,
PUS packet queue, and FDIR callback wiring.
"""
import threading
import time
import queue
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from config import (
    SIM_TICK_HZ, SPACECRAFT_APID, TIME_EPOCH,
    SC_MODE_NOMINAL, SC_MODE_SAFE, SC_MODE_EMERGENCY,
    AOCS_MODE_SAFE, OBC_MODE_SAFE,
)
PLI_MODE_OFF = 0
from orbit import OrbitPropagator
from eps    import EPSSubsystem
from aocs   import AOCSSubsystem
from tcs    import TCSSubsystem
from obdh   import OBDHSubsystem
from ttc    import TTCSubsystem
from payload import PayloadSubsystem
from fdir   import FDIRSubsystem
from tm_builder    import TMBuilder
from failure_manager import FailureManager, ONSET_STEP, ONSET_GRADUAL, ONSET_INTERMITTENT
from scenario_engine import ScenarioEngine
from service_handlers import monitoring_tick

logger = logging.getLogger(__name__)


class SimulationEngine:
    """
    Central simulation engine.

    • Runs a tick loop at SIM_TICK_HZ (default 1 Hz real-time).
    • Speed multiplier >1 allows fast-forward (instructor use).
    • All subsystems share a flat parameter dict keyed by PUS param ID.
    • Generated PUS TM packets are placed on self.tm_queue (bytes).
    • Incoming TC bytes are placed on self.tc_queue (bytes) by the server.
    • Failure injection commands arrive on self.instr_queue (dict).
    """

    def __init__(self, speed: float = 1.0):
        self.speed         = speed          # simulation speed multiplier
        self.running       = False
        self._tick_count   = 0
        self._last_eclipse  = None          # track transitions for events

        # Shared parameter store: param_id (int) → value (float|int)
        self.params: Dict[int, float] = {}

        # Thread-safe queues
        self.tm_queue:    queue.Queue = queue.Queue(maxsize=2000)
        self.tc_queue:    queue.Queue = queue.Queue(maxsize=500)
        self.instr_queue: queue.Queue = queue.Queue(maxsize=200)
        self.event_queue: queue.Queue = queue.Queue(maxsize=500)

        # Real simulation time (advances with each tick)
        self._sim_time = datetime.now(timezone.utc)

        # Subsystems
        dt = 1.0 / SIM_TICK_HZ
        self.orbit   = OrbitPropagator()
        self.eps     = EPSSubsystem(dt_s=dt)
        self.aocs    = AOCSSubsystem(dt_s=dt)
        self.tcs     = TCSSubsystem(dt_s=dt)
        self.obdh    = OBDHSubsystem(dt_s=dt)
        self.ttc     = TTCSubsystem(dt_s=dt)
        self.payload = PayloadSubsystem(dt_s=dt)
        self.fdir    = FDIRSubsystem()

        # TM packet builder
        self.tm_builder = TMBuilder(apid=SPACECRAFT_APID, obdh=self.obdh)

        # HK collection timers (seconds since last emission, keyed by SID)
        self._hk_timers: Dict[int, float] = {}
        self._hk_intervals: Dict[int, float] = {}

        # Previous contact state for AOS/LOS events
        self._prev_in_contact = False
        self._prev_in_eclipse = False

        # Phase 2: Failure Manager and Scenario Engine
        self._failure_manager = FailureManager(
            inject_fn=self._handle_failure_inject,
            clear_fn=self._handle_failure_clear,
        )
        self._scenario_engine = ScenarioEngine(self._failure_manager, self)

        # Wire FDIR callbacks
        self._wire_fdir()

        # Import HK intervals from config
        self._load_hk_intervals()

    # ------------------------------------------------------------------
    # FDIR callback wiring
    # ------------------------------------------------------------------

    def _wire_fdir(self) -> None:
        f = self.fdir
        f.register_callback('payload_poweroff',  lambda: self.payload.cmd_set_mode(0))
        f.register_callback('heater_on_battery', lambda: self.tcs.cmd_heater('battery', True))
        f.register_callback('heater_off_battery',lambda: self.tcs.cmd_heater('battery', False))
        f.register_callback('safe_mode_aocs',    lambda: self.aocs.cmd_set_mode(AOCS_MODE_SAFE))
        f.register_callback('safe_mode_obc',     lambda: self.obdh.cmd_set_mode(OBC_MODE_SAFE))
        f.register_callback('safe_mode_eps',     lambda: self.payload.cmd_set_mode(0))
        f.register_callback('disable_rw1',       lambda: self.aocs.cmd_disable_wheel(0))
        f.register_callback('disable_rw2',       lambda: self.aocs.cmd_disable_wheel(1))
        f.register_callback('disable_rw3',       lambda: self.aocs.cmd_disable_wheel(2))
        f.register_callback('disable_rw4',       lambda: self.aocs.cmd_disable_wheel(3))

    def _load_hk_intervals(self) -> None:
        from config import (
            HK_SID_EPS, HK_SID_AOCS, HK_SID_TCS, HK_SID_PLATFORM,
            HK_SID_PAYLOAD, HK_SID_TTC,
            HK_INTERVAL_EPS, HK_INTERVAL_AOCS, HK_INTERVAL_TCS,
            HK_INTERVAL_PLATFORM, HK_INTERVAL_PAYLOAD, HK_INTERVAL_TTC,
        )
        self._hk_intervals = {
            HK_SID_EPS:      HK_INTERVAL_EPS,
            HK_SID_AOCS:     HK_INTERVAL_AOCS,
            HK_SID_TCS:      HK_INTERVAL_TCS,
            HK_SID_PLATFORM: HK_INTERVAL_PLATFORM,
            HK_SID_PAYLOAD:  HK_INTERVAL_PAYLOAD,
            HK_SID_TTC:      HK_INTERVAL_TTC,
        }
        self._hk_enabled = {sid: True for sid in self._hk_intervals}
        self._hk_timers  = {sid: 0.0   for sid in self._hk_intervals}

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="sim-engine")
        self._thread.start()
        logger.info("SimulationEngine started (speed=%.1f×)", self.speed)

    def stop(self) -> None:
        self.running = False
        logger.info("SimulationEngine stopping…")

    def _run_loop(self) -> None:
        dt_real    = 1.0 / SIM_TICK_HZ           # real seconds per tick
        dt_sim     = dt_real * self.speed          # sim seconds per tick
        last_wall  = time.monotonic()

        while self.running:
            # Process pending TCs and instructor commands first
            self._drain_tc_queue()
            self._drain_instr_queue()

            # Advance orbit
            orbit_state = self.orbit.advance(dt_sim)

            # Tick all subsystems
            self.eps.tick(orbit_state, self.params)
            # Cross-subsystem: feed battery bay temperature to EPS
            self.eps.set_bat_ambient_temp(self.tcs.state.temp_battery)

            self.aocs.tick(orbit_state, self.params)
            self.tcs.tick(orbit_state, self.params)
            self.obdh.tick(orbit_state, self.params)
            self.ttc.tick(orbit_state, self.params)
            self.payload.tick(orbit_state, self.params)

            # FDIR check
            self.fdir.tick(self.params)
            for ev in self.fdir.pop_events():
                self._emit_event(ev)

            # Eclipse / contact transition events
            self._check_transitions(orbit_state)

            # HK packet emission
            self._emit_hk_packets(dt_sim)

            # Phase 2: S4 statistics accumulation + S12 limit checking
            monitoring_tick(self, dt_sim)

            # Phase 2: Failure progression (gradual/intermittent onset models)
            self._failure_manager.tick(dt_sim)

            # Phase 2: Scenario engine (timed events + condition checks)
            self._scenario_engine.tick(dt_sim, self.params)

            self._tick_count += 1
            self._sim_time += __import__('datetime').timedelta(seconds=dt_sim)

            # Rate control — sleep to maintain real-time tick rate
            elapsed = time.monotonic() - last_wall
            sleep_t = max(0.0, dt_real - elapsed)
            if sleep_t > 0:
                time.sleep(sleep_t)
            last_wall = time.monotonic()

    # ------------------------------------------------------------------
    # TC handling
    # ------------------------------------------------------------------

    def _drain_tc_queue(self) -> None:
        from service_handlers import ServiceDispatcher
        while not self.tc_queue.empty():
            try:
                raw = self.tc_queue.get_nowait()
                self.obdh.record_tc_received()
                responses = ServiceDispatcher.dispatch(raw, self)
                for pkt in responses:
                    self._enqueue_tm(pkt)
                    self.obdh.record_tm_packet()
            except queue.Empty:
                break
            except Exception as e:
                logger.warning("TC dispatch error: %s", e)
                self.obdh.record_tc_rejected()

    # ------------------------------------------------------------------
    # HK emission
    # ------------------------------------------------------------------

    def _emit_hk_packets(self, dt_sim: float) -> None:
        from config import (HK_SID_EPS, HK_SID_AOCS, HK_SID_TCS,
                                    HK_SID_PLATFORM, HK_SID_PAYLOAD, HK_SID_TTC)
        for sid, interval in self._hk_intervals.items():
            if not self._hk_enabled.get(sid, True):
                continue
            self._hk_timers[sid] = self._hk_timers.get(sid, 0.0) + dt_sim
            if self._hk_timers[sid] >= interval:
                self._hk_timers[sid] = 0.0
                pkt = self.tm_builder.build_hk_packet(sid, self.params)
                if pkt:
                    self._enqueue_tm(pkt)
                    self.obdh.record_tm_packet()

    # ------------------------------------------------------------------
    # Transition event detection
    # ------------------------------------------------------------------

    def _check_transitions(self, orbit_state) -> None:
        # AOS event
        if orbit_state.in_contact and not self._prev_in_contact:
            self._emit_event({
                'event_id': 0x0001,
                'severity': 1,
                'description': f"AOS — elevation {orbit_state.gs_elevation_deg:.1f}°",
            })
        # LOS event
        elif not orbit_state.in_contact and self._prev_in_contact:
            self._emit_event({
                'event_id': 0x0002,
                'severity': 1,
                'description': "LOS",
            })
        # Eclipse entry
        if orbit_state.in_eclipse and not self._prev_in_eclipse:
            self._emit_event({
                'event_id': 0x0010,
                'severity': 1,
                'description': "Eclipse entry",
            })
        # Eclipse exit
        elif not orbit_state.in_eclipse and self._prev_in_eclipse:
            self._emit_event({
                'event_id': 0x0011,
                'severity': 1,
                'description': "Sunlight entry",
            })
        self._prev_in_contact = orbit_state.in_contact
        self._prev_in_eclipse = orbit_state.in_eclipse

    def _emit_event(self, ev: dict) -> None:
        pkt = self.tm_builder.build_event_packet(
            event_id  = ev.get('event_id', 0),
            severity  = ev.get('severity', 1),
            aux_text  = ev.get('description', ''),
            params    = self.params,
        )
        if pkt:
            self._enqueue_tm(pkt)
            self.event_queue.put_nowait(ev)

    def _enqueue_tm(self, pkt: bytes) -> None:
        try:
            self.tm_queue.put_nowait(pkt)
        except queue.Full:
            pass   # drop if queue full (shouldn't happen at 1 Hz)

    # ------------------------------------------------------------------
    # Instructor command processing
    # ------------------------------------------------------------------

    def _drain_instr_queue(self) -> None:
        while not self.instr_queue.empty():
            try:
                cmd = self.instr_queue.get_nowait()
                self._handle_instructor_cmd(cmd)
            except queue.Empty:
                break
            except Exception as e:
                logger.warning("Instructor cmd error: %s", e)

    def _handle_instructor_cmd(self, cmd: dict) -> None:
        t = cmd.get('type')
        if t == 'set_speed':
            self.speed = float(cmd.get('value', 1.0))
        elif t == 'freeze':
            self.running = False
        elif t == 'resume':
            self.running = True
        # Legacy simple inject (direct subsystem call)
        elif t == 'inject':
            self._handle_failure_inject(cmd)
        elif t == 'clear_failure':
            self._handle_failure_clear(cmd)
        # Phase 2: FailureManager-based injection with onset models
        elif t == 'failure_inject':
            onset          = cmd.get('onset', ONSET_STEP)
            onset_duration = float(cmd.get('onset_duration_s', 90.0))
            duration       = cmd.get('duration_s', None)
            magnitude      = float(cmd.get('magnitude', 1.0))
            fid = self._failure_manager.inject(
                subsystem      = cmd.get('subsystem', ''),
                failure        = cmd.get('failure', ''),
                magnitude      = magnitude,
                onset          = onset,
                duration_s     = float(duration) if duration is not None else None,
                onset_duration_s = onset_duration,
                **{k: v for k, v in cmd.items()
                   if k not in ('type','subsystem','failure','magnitude',
                                'onset','duration_s','onset_duration_s')},
            )
            logger.info("FailureManager injection: %s", fid)
        elif t == 'failure_clear':
            fid = cmd.get('failure_id')
            if fid:
                self._failure_manager.clear(fid)
            else:
                self._failure_manager.clear_all()
        elif t == 'failure_list':
            # Instructor channel feedback — not directly sent here;
            # sim_server must pull get_state_summary which embeds active failures
            pass
        # Phase 2: Scenario engine commands
        elif t == 'scenario_start':
            name = cmd.get('name', '')
            if name:
                self._scenario_engine.start(name)
                logger.info("Scenario started: %s", name)
        elif t == 'scenario_stop':
            report = self._scenario_engine.stop()
            if report:
                logger.info("Scenario debrief: score=%.0f%%  MTTD=%.0fs",
                            report.score_pct, report.mttd_s or 0)
        elif t == 'scenario_list':
            # Instructor must call scenario_engine.list_scenarios() via sim_server
            pass
        elif t == 'scenario_record':
            category = cmd.get('category', 'detect')
            description = cmd.get('description', '')
            self._scenario_engine.record_response(category, description)
        elif t == 'reset_scenario':
            self._scenario_engine.stop()


    def _handle_failure_inject(self, cmd: dict) -> None:
        subsys    = cmd.get('subsystem', '')
        failure   = cmd.get('failure', '')
        magnitude = float(cmd.get('magnitude', 1.0))

        if subsys == 'eps':
            if failure == 'solar_array_partial':
                array = cmd.get('array', 'A')
                self.eps.inject_sa_degradation(array, magnitude)
            elif failure == 'bat_cell':
                self.eps.inject_bat_cell_failure(bool(magnitude))
            elif failure == 'bus_short':
                self.eps.inject_bus_short(bool(magnitude))

        elif subsys == 'aocs':
            if failure == 'rw_bearing':
                wheel = int(cmd.get('wheel', 0))
                self.aocs.inject_bearing_degradation(wheel, magnitude)
            elif failure == 'rw_seizure':
                wheel = int(cmd.get('wheel', 0))
                self.aocs.inject_bearing_degradation(wheel, 1.0)
            elif failure == 'gyro_bias':
                axis = int(cmd.get('axis', 0))
                bias = float(cmd.get('bias', 0.05))
                self.aocs.inject_gyro_bias(axis, bias)
            elif failure == 'st_blind':
                self.aocs.inject_star_tracker_blind(bool(magnitude))

        elif subsys == 'tcs':
            if failure == 'heater_failure':
                circuit = cmd.get('circuit', 'battery')
                self.tcs.inject_heater_failure(circuit, bool(magnitude))
            elif failure == 'cooler_failure':
                self.tcs.inject_cooler_failure(bool(magnitude))
            elif failure == 'obc_thermal':
                self.tcs.inject_obc_thermal(float(cmd.get('heat_w', 30.0)))

        elif subsys == 'obdh':
            if failure == 'watchdog_reset':
                self.obdh.inject_watchdog_reset()
            elif failure == 'memory_errors':
                self.obdh.inject_memory_errors(int(cmd.get('count', 5)))

        elif subsys == 'ttc':
            if failure == 'primary_failure':
                self.ttc.inject_primary_failure(bool(magnitude))
            elif failure == 'redundant_failure':
                self.ttc.inject_redundant_failure(bool(magnitude))

        elif subsys == 'payload':
            if failure == 'cooler_failure':
                self.payload.inject_cooler_failure(bool(magnitude))
            elif failure == 'fpa_degraded':
                self.payload.inject_fpa_degradation(bool(magnitude))

        logger.info("Failure injected: %s/%s mag=%.2f", subsys, failure, magnitude)

    def _handle_failure_clear(self, cmd: dict) -> None:
        subsys  = cmd.get('subsystem', '')
        failure = cmd.get('failure', '')

        if subsys == 'eps':
            if failure == 'solar_array_partial':
                self.eps.inject_sa_degradation(cmd.get('array', 'A'), 0.0)
            elif failure == 'bat_cell':
                self.eps.inject_bat_cell_failure(False)
        elif subsys == 'aocs':
            if failure == 'rw_bearing':
                self.aocs.inject_bearing_degradation(int(cmd.get('wheel', 0)), 0.0)
            elif failure == 'st_blind':
                self.aocs.inject_star_tracker_blind(False)
        elif subsys == 'tcs':
            if failure == 'heater_failure':
                self.tcs.inject_heater_failure(cmd.get('circuit', 'battery'), False)
            elif failure == 'cooler_failure':
                self.tcs.inject_cooler_failure(False)
            elif failure == 'obc_thermal':
                self.tcs.inject_obc_thermal(0.0)
        elif subsys == 'ttc':
            if failure == 'primary_failure':
                self.ttc.inject_primary_failure(False)

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_state_summary(self) -> dict:
        """Full state dict for MCS WebSocket broadcast (1 Hz)."""
        o = self.orbit.state
        p = self.params
        summary = {
            # ── Header ──────────────────────────────────────────────────
            'tick':        self._tick_count,
            'sim_time':    self._sim_time.isoformat(),
            'speed':       self.speed,
            'sc_mode':     self.fdir.sc_mode,
            'in_eclipse':  bool(o.in_eclipse),
            'in_contact':  bool(o.in_contact),
            'lat':         round(o.lat_deg, 4),
            'lon':         round(o.lon_deg, 4),
            'alt_km':      round(o.alt_km, 2),
            # ── EPS ─────────────────────────────────────────────────────
            'eps': {
                'soc_pct':        round(p.get(0x0101, 0), 1),
                'bat_voltage_V':  round(p.get(0x0100, 0), 2),
                'bus_voltage_V':  round(p.get(0x0105, 0), 2),
                'bat_temp_C':     round(p.get(0x0102, 0), 1),
                'bat_current_A':  round(p.get(0x0109, 0), 2),
                'sa_a_A':         round(p.get(0x0103, 0), 2),
                'sa_b_A':         round(p.get(0x0104, 0), 2),
                'power_gen_W':    round(p.get(0x0107, 0), 1),
                'power_cons_W':   round(p.get(0x0106, 0), 1),
            },
            # ── AOCS ────────────────────────────────────────────────────
            'aocs': {
                'mode':           int(p.get(0x020F, 0)),
                'att_error_deg':  round(p.get(0x0217, 0), 3),
                'rate_roll':      round(p.get(0x0204, 0), 4),
                'rate_pitch':     round(p.get(0x0205, 0), 4),
                'rate_yaw':       round(p.get(0x0206, 0), 4),
                'rw1_rpm':        round(p.get(0x0207, 0)),
                'rw2_rpm':        round(p.get(0x0208, 0)),
                'rw3_rpm':        round(p.get(0x0209, 0)),
                'rw4_rpm':        round(p.get(0x020A, 0)),
                'rw1_temp_C':     round(p.get(0x0218, 0), 1),
                'rw2_temp_C':     round(p.get(0x0219, 0), 1),
                'rw3_temp_C':     round(p.get(0x021A, 0), 1),
                'rw4_temp_C':     round(p.get(0x021B, 0), 1),
            },
            # ── TCS ─────────────────────────────────────────────────────
            'tcs': {
                'temp_obc_C':     round(p.get(0x0406, 0), 1),
                'temp_bat_C':     round(p.get(0x0407, 0), 1),
                'temp_fpa_C':     round(p.get(0x0408, 0), 1),
                'temp_panel_px':  round(p.get(0x0400, 0), 1),
                'temp_panel_py':  round(p.get(0x0402, 0), 1),
                'htr_bat':        bool(p.get(0x040A, 0)),
                'htr_obc':        bool(p.get(0x040B, 0)),
                'cooler_fpa':     bool(p.get(0x040C, 0)),
            },
            # ── OBDH ────────────────────────────────────────────────────
            'obdh': {
                'mode':           int(p.get(0x0300, 0)),
                'cpu_load_pct':   round(p.get(0x0302, 0), 1),
                'mem_used_pct':   round(p.get(0x0303, 0), 1),
                'tc_rx':          int(p.get(0x0304, 0)),
                'tc_acc':         int(p.get(0x0305, 0)),
                'tc_rej':         int(p.get(0x0306, 0)),
                'reboot_count':   int(p.get(0x030A, 0)),
            },
            # ── TT&C ────────────────────────────────────────────────────
            'ttc': {
                'mode':           int(p.get(0x0500, 0)),
                'link_status':    int(p.get(0x0501, 0)),
                'rssi_dBm':       round(p.get(0x0502, 0), 1),
                'link_margin_dB': round(p.get(0x0503, 0), 1),
                'range_km':       round(p.get(0x0509, 0), 1),
                'elevation_deg':  round(p.get(0x050A, 0), 1),
                'xpdr_temp_C':    round(p.get(0x0507, 0), 1),
            },
            # ── Payload ─────────────────────────────────────────────────
            'payload': {
                'mode':           int(p.get(0x0600, 0)),
                'fpa_temp_C':     round(p.get(0x0601, 0), 1),
                'cooler_W':       round(p.get(0x0602, 0), 1),
                'store_used_pct': round(p.get(0x0604, 0), 1),
                'image_count':    int(p.get(0x0605, 0)),
                'checksum_errs':  int(p.get(0x0609, 0)),
            },
            # ── Scenario & Failures ──────────────────────────────────────
            'active_failures':     self._failure_manager.active_failures(),
            'scenario_active':     self._scenario_engine.is_active(),
            'scenario_name':       self._scenario_engine.current_name(),
            'scenario_elapsed_s':  self._scenario_engine.elapsed_s,
            'available_scenarios': self._scenario_engine.list_scenarios(),
        }
        return summary

    def set_hk_enabled(self, sid: int, enabled: bool) -> None:
        self._hk_enabled[sid] = enabled

    def set_hk_interval(self, sid: int, interval_s: float) -> None:
        self._hk_intervals[sid] = interval_s

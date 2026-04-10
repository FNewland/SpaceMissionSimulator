"""SMO Simulator — Enhanced OBDH Model.

Dual cold-redundant OBC (A/B), boot loader / application software model,
dual CAN bus with failure isolation, buffer management with stop-when-full,
and comprehensive failure injection.
"""
import struct
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from smo_common.models.subsystem import SubsystemModel

# Software image constants
SW_BOOTLOADER = 0
SW_APPLICATION = 1

# OBC unit status
OBC_OFF = 0
OBC_STANDBY = 1
OBC_ACTIVE = 2

# Bus status
BUS_OK = 0
BUS_DEGRADED = 1
BUS_FAILED = 2

# Reboot causes
REBOOT_NONE = 0
REBOOT_COMMAND = 1
REBOOT_WATCHDOG = 2
REBOOT_MEMORY_ERROR = 3
REBOOT_SWITCHOVER = 4


@dataclass
class OBDHState:
    # OBC mode: 0=nominal, 1=safe, 2=maintenance
    mode: int = 0
    temp: float = 25.0
    cpu_load: float = 35.0
    mmm_used_pct: float = 20.0

    # TC/TM counters
    tc_rx_count: int = 0
    tc_acc_count: int = 0
    tc_rej_count: int = 0
    tm_pkt_count: int = 0

    # Timing
    uptime_s: int = 0
    obc_time_cuc: int = 0

    # Reboot
    reboot_count: int = 0
    sw_version: int = 0x0100
    mem_errors: int = 0

    # Watchdog
    watchdog_armed: bool = True
    watchdog_timer: int = 0
    watchdog_period: int = 30

    # ── Dual OBC ──
    active_obc: int = 0  # 0=A, 1=B
    obc_b_status: int = OBC_STANDBY  # OFF/STANDBY (backup cold redundant)
    boot_count_a: int = 0
    boot_count_b: int = 0

    # ── Software image ──
    sw_image: int = SW_BOOTLOADER  # 0=bootloader, 1=application — boots into bootloader, app loaded by ground
    boot_app_timer: float = 0.0  # Countdown for application boot (10s)
    boot_app_pending: bool = False
    boot_inhibit: bool = False  # Prevent auto-boot
    boot_image_corrupt: bool = False  # Application image CRC fail

    # ── Dual CAN Bus ──
    active_bus: int = 0  # 0=A, 1=B
    bus_a_status: int = BUS_OK
    bus_b_status: int = BUS_OK

    # ── Buffer management ──
    hktm_buf_fill: float = 0.0  # stored as PERCENTAGE (0.0–100.0)
    hktm_buf_capacity: int = 1000
    event_buf_fill: int = 0
    event_buf_capacity: int = 500
    alarm_buf_fill: int = 0
    alarm_buf_capacity: int = 200

    # ── Last reboot cause ──
    last_reboot_cause: int = REBOOT_NONE

    # ── Memory segment health (per-segment fault status for CON memory_segment_failure) ──
    # Segment 0..3 → True == healthy, False == failed
    memory_segments: list[bool] = field(
        default_factory=lambda: [True, True, True, True]
    )

    # ── Phase 4: Flight hardware realism ──
    seu_count: int = 0              # Single-event upset counter
    scrub_progress_pct: float = 0.0 # Memory scrub progress (0-100%)
    scrub_active: bool = False      # Memory scrub in progress
    task_count: int = 12            # Active OS tasks
    stack_usage_pct: float = 35.0   # Stack usage percentage
    heap_usage_pct: float = 40.0    # Heap usage percentage
    heat_dissipation_w: float = 0.0 # OBC nominal heat dissipation (Watts)

    # ── Bus equipment mapping (which subsystems are on which bus) ──
    # In a real system this is hardware-defined; here it's configurable
    bus_a_subsystems: list[str] = field(
        default_factory=lambda: ["eps", "tcs", "aocs"]
    )
    bus_b_subsystems: list[str] = field(
        default_factory=lambda: ["ttc", "payload"]
    )

    # S2 Device Access — device on/off states (device_id -> on/off)
    device_states: dict = field(default_factory=lambda: {
        0x0500: True,   # OBC-A
        0x0501: True,   # OBC-B
        0x0502: True,   # Mass memory unit
        0x0503: True,   # Watchdog timer
        0x0504: True,   # CAN bus interface
    })


class OBDHBasicModel(SubsystemModel):
    """Enhanced OBDH with dual OBC, boot loader, dual CAN bus,
    and buffer management."""

    def __init__(self):
        self._state = OBDHState()
        self._cpu_base = 35.0
        self._param_ids: dict[str, int] = {}
        self._event_to_emit: tuple[int, str] | None = None  # (event_id, event_name)
        self._prev_mode = 0
        self._prev_reboot_count = 0
        self._prev_bus_a_status = 0
        self._prev_bus_b_status = 0
        self._prev_active_obc = 0
        self._prev_hktm_buf_fill = 0
        self._prev_event_buf_fill = 0

    @property
    def name(self) -> str:
        return "obdh"

    def configure(self, config: dict[str, Any]) -> None:
        self._state.watchdog_period = config.get("watchdog_period_ticks", 30)
        self._cpu_base = config.get("cpu_baseline_pct", 35.0)

        # Buffer capacities
        buffers = config.get("buffers", {})
        self._state.hktm_buf_capacity = buffers.get("hktm_capacity", 1000)
        self._state.event_buf_capacity = buffers.get("event_capacity", 500)
        self._state.alarm_buf_capacity = buffers.get("alarm_capacity", 200)

        # Bus equipment mapping
        bus_config = config.get("bus_mapping", {})
        if "bus_a" in bus_config:
            self._state.bus_a_subsystems = list(bus_config["bus_a"])
        if "bus_b" in bus_config:
            self._state.bus_b_subsystems = list(bus_config["bus_b"])

        self._param_ids = config.get("param_ids", {
            "obc_mode": 0x0300, "obc_temp": 0x0301, "cpu_load": 0x0302,
            "mmm_used": 0x0303, "tc_rx": 0x0304, "tc_acc": 0x0305,
            "tc_rej": 0x0306, "tm_pkt": 0x0307, "uptime": 0x0308,
            "obc_time": 0x0309, "reboot_count": 0x030A,
            "sw_version": 0x030B,
            # New params
            "active_obc": 0x030C, "obc_b_status": 0x030D,
            "active_bus": 0x030E, "bus_a_status": 0x030F,
            "bus_b_status": 0x0310, "sw_image": 0x0311,
            "hktm_buf_fill": 0x0312, "event_buf_fill": 0x0313,
            "alarm_buf_fill": 0x0314,
            "last_reboot_cause": 0x0316,
            "boot_count_a": 0x0317, "boot_count_b": 0x0318,
        })

        # Init CUC time
        from smo_common.protocol.ecss_packet import TIME_EPOCH
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self._state.obc_time_cuc = int((now - TIME_EPOCH).total_seconds())

    def tick(self, dt: float, orbit_state: Any,
             shared_params: dict[int, float]) -> None:
        s = self._state
        s.uptime_s += int(dt)
        s.obc_time_cuc += int(dt)

        # Read OBC temperature from TCS via shared_params
        tcs_obc_temp = shared_params.get(0x0406)
        if tcs_obc_temp is not None:
            s.temp = tcs_obc_temp

        # Boot application pending (10s simulated CRC check)
        if s.boot_app_pending:
            s.boot_app_timer -= dt
            if s.boot_app_timer <= 0.0:
                s.boot_app_pending = False
                if s.boot_image_corrupt:
                    # CRC fail — stay in boot loader, generate BOOT_FAILURE event
                    s.sw_image = SW_BOOTLOADER
                    self._event_to_emit = (0x0305, "BOOT_FAILURE")
                else:
                    s.sw_image = SW_APPLICATION
                    s.mode = 0  # nominal

        # CPU load depends on mode and sw_image
        if s.sw_image == SW_BOOTLOADER:
            # Boot loader: minimal CPU, limited functionality
            s.cpu_load = max(0, min(100, 15.0 + random.gauss(0, 1.0)))
        else:
            mode_load = {0: 0, 1: -10, 2: 20}
            s.cpu_load = max(
                0,
                min(
                    100,
                    self._cpu_base
                    + mode_load.get(s.mode, 0)
                    + random.gauss(0, 2.0),
                ),
            )

        # Watchdog (only in application mode)
        if s.sw_image == SW_APPLICATION:
            if s.watchdog_armed and s.mode == 0:
                s.watchdog_timer = 0
            else:
                s.watchdog_timer += 1
                if s.watchdog_timer >= s.watchdog_period:
                    self._event_to_emit = (0x0303, "WATCHDOG_TIMEOUT")
                    self._reboot(REBOOT_WATCHDOG)
        else:
            # Boot loader doesn't arm watchdog
            s.watchdog_timer = 0

        # Phase 4: SEU simulation (South Atlantic Anomaly model)
        # ~1 SEU per orbit on average for LEO spacecraft
        seu_prob = 0.00015 * dt  # ~1 per 6600s orbit
        if random.random() < seu_prob:
            s.seu_count += 1
            s.mem_errors += 1
            self._event_to_emit = (0x0307, "SEU_DETECTED")

        # Memory scrub progress
        if s.scrub_active:
            s.scrub_progress_pct += dt / 60.0 * 5.0  # ~20 min for full scrub
            if s.scrub_progress_pct >= 100.0:
                s.scrub_progress_pct = 0.0
                s.scrub_active = False
                # Scrub corrects SEU errors
                s.mem_errors = max(0, s.mem_errors - 3)
                self._event_to_emit = (0x0308, "SCRUB_COMPLETE")

        # Task count and stack/heap usage
        if s.sw_image == SW_APPLICATION:
            s.task_count = 12 + (3 if s.mode == 0 else 0)
            s.stack_usage_pct = 30.0 + s.cpu_load * 0.3 + random.gauss(0, 1.0)
            s.stack_usage_pct = max(10.0, min(95.0, s.stack_usage_pct))
            s.heap_usage_pct = 35.0 + s.mmm_used_pct * 0.4 + random.gauss(0, 0.5)
            s.heap_usage_pct = max(10.0, min(95.0, s.heap_usage_pct))
        else:
            s.task_count = 4  # Boot loader has minimal tasks
            s.stack_usage_pct = 15.0 + random.gauss(0, 0.5)
            s.heap_usage_pct = 10.0 + random.gauss(0, 0.5)

        # OBC nominal heat dissipation (defects/reviews/obdh.md)
        # ~15W in application mode, ~2W in bootloader (minimal activity)
        if s.sw_image == SW_APPLICATION:
            # Base 12W nominal + 2W per 10% CPU load + mode variation
            mode_heat = {0: 3.0, 1: 0.5, 2: 6.0}  # nominal, safe, maintenance
            s.heat_dissipation_w = 12.0 + (s.cpu_load / 10.0 * 2.0) + mode_heat.get(s.mode, 0) + random.gauss(0, 0.5)
            s.heat_dissipation_w = max(5.0, min(35.0, s.heat_dissipation_w))  # Clamp to realistic range
        else:
            # Bootloader: minimal power consumption
            s.heat_dissipation_w = 2.0 + random.gauss(0, 0.1)

        # Buffer fill simulation — expressed as a PERCENTAGE (0..100) so
        # that the MCS "Buffer Fill – HK TM" widget (param 0x0312) never
        # exceeds 100%. Previously the counter was a raw packet count
        # clamped to `hktm_buf_capacity` (=1000), but the widget was
        # rendering that number directly with a '%' suffix — producing
        # physically impossible readings (observed 353%). See
        # defects/01-obdh-hk-tm-buffer-fill-overflow.md.
        #
        # The authoritative S15 store fill is computed in
        # OnboardTMStorage.get_status(); until this model is wired to the
        # real store, we simulate a well-behaved percentage here that:
        #   - accumulates slowly while the application is running,
        #   - drains when the downlink is active (TTC carrier lock),
        #   - is hard-clamped to [0, 100].
        if s.sw_image == SW_APPLICATION:
            # Accumulate a small percentage every tick (equivalent to the
            # previous raw count scaled by 100/capacity).
            accumulate_pct = random.uniform(0.0, 2.0) * (100.0 / max(1, s.hktm_buf_capacity))
            s.hktm_buf_fill = min(100.0, float(s.hktm_buf_fill) + accumulate_pct)
            # Drain when downlink active (TTC carrier lock)
            carrier_lock = shared_params.get(0x0510, 0)
            if carrier_lock:
                drain_pct = random.uniform(1.0, 5.0) * (100.0 / max(1, s.hktm_buf_capacity))
                s.hktm_buf_fill = max(0.0, float(s.hktm_buf_fill) - drain_pct)
                s.event_buf_fill = max(0, s.event_buf_fill - random.randint(0, 2))
        # Hard-clamp in all cases (belt and braces)
        s.hktm_buf_fill = max(0.0, min(100.0, float(s.hktm_buf_fill)))

        # Event generation — edge detection and dispatch to engine
        events_to_emit = []

        # OBDH_MODE_CHANGE (0x0300)
        if s.mode != self._prev_mode:
            events_to_emit.append((0x0300, "OBDH_MODE_CHANGE", "INFO"))
            self._event_to_emit = (0x0300, "OBDH_MODE_CHANGE")
            self._prev_mode = s.mode

        # OBC_REBOOT (0x0301) — generated by _reboot method
        if s.reboot_count != self._prev_reboot_count:
            events_to_emit.append((0x0301, "OBC_REBOOT", "HIGH"))
            self._event_to_emit = (0x0301, "OBC_REBOOT")
            self._prev_reboot_count = s.reboot_count

        # MEMORY_ERROR (0x0302) — injected via failure system
        # (already handled in inject_failure)

        # BUS_FAILURE (0x0304) — CAN bus failure detected
        if s.bus_a_status != self._prev_bus_a_status and s.bus_a_status == BUS_FAILED:
            events_to_emit.append((0x0304, "BUS_FAILURE_A", "HIGH"))
            self._event_to_emit = (0x0304, "BUS_FAILURE_A")
        if s.bus_b_status != self._prev_bus_b_status and s.bus_b_status == BUS_FAILED:
            events_to_emit.append((0x0304, "BUS_FAILURE_B", "HIGH"))
            self._event_to_emit = (0x0304, "BUS_FAILURE_B")
        self._prev_bus_a_status = s.bus_a_status
        self._prev_bus_b_status = s.bus_b_status

        # OBC_SWITCHOVER (0x0306) — redundant OBC activated
        if s.active_obc != self._prev_active_obc:
            events_to_emit.append((0x0306, "OBC_SWITCHOVER", "MEDIUM"))
            self._event_to_emit = (0x0306, "OBC_SWITCHOVER")
            self._prev_active_obc = s.active_obc

        # TC_QUEUE_OVERFLOW (0x0309) — S11 queue full
        # (monitored via record methods)
        # TM_STORE_OVERFLOW (0x030A) — S15 store full
        # (checked in tick by storage monitoring)

        # Emit events to engine if available
        if hasattr(self, '_engine') and self._engine:
            for evt_id, evt_name, severity in events_to_emit:
                try:
                    self._engine.event_queue.put_nowait({
                        'event_id': evt_id,
                        'severity': severity,
                        'subsystem': 'obdh',
                        'description': evt_name
                    })
                except:
                    pass

        # Write params
        p = self._param_ids
        shared_params[p.get("obc_mode", 0x0300)] = s.mode
        shared_params[p.get("obc_temp", 0x0301)] = s.temp
        shared_params[p.get("cpu_load", 0x0302)] = s.cpu_load
        shared_params[p.get("mmm_used", 0x0303)] = s.mmm_used_pct
        shared_params[p.get("tc_rx", 0x0304)] = s.tc_rx_count
        shared_params[p.get("tc_acc", 0x0305)] = s.tc_acc_count
        shared_params[p.get("tc_rej", 0x0306)] = s.tc_rej_count
        shared_params[p.get("tm_pkt", 0x0307)] = s.tm_pkt_count
        shared_params[p.get("uptime", 0x0308)] = s.uptime_s
        shared_params[p.get("obc_time", 0x0309)] = s.obc_time_cuc
        shared_params[p.get("reboot_count", 0x030A)] = s.reboot_count
        shared_params[p.get("sw_version", 0x030B)] = s.sw_version
        # New params
        shared_params[p.get("active_obc", 0x030C)] = s.active_obc
        shared_params[p.get("obc_b_status", 0x030D)] = s.obc_b_status
        shared_params[p.get("active_bus", 0x030E)] = s.active_bus
        shared_params[p.get("bus_a_status", 0x030F)] = s.bus_a_status
        shared_params[p.get("bus_b_status", 0x0310)] = s.bus_b_status
        shared_params[p.get("sw_image", 0x0311)] = s.sw_image
        shared_params[p.get("hktm_buf_fill", 0x0312)] = s.hktm_buf_fill
        shared_params[p.get("event_buf_fill", 0x0313)] = s.event_buf_fill
        shared_params[p.get("alarm_buf_fill", 0x0314)] = s.alarm_buf_fill
        shared_params[p.get("last_reboot_cause", 0x0316)] = s.last_reboot_cause
        shared_params[p.get("boot_count_a", 0x0317)] = s.boot_count_a
        shared_params[p.get("boot_count_b", 0x0318)] = s.boot_count_b
        # Phase 4: Flight hardware params
        shared_params[0x0319] = float(s.seu_count)
        shared_params[0x031A] = s.scrub_progress_pct
        shared_params[0x031B] = float(s.task_count)
        shared_params[0x031C] = s.stack_usage_pct
        shared_params[0x031D] = s.heap_usage_pct
        shared_params[0x031E] = float(s.mem_errors)
        # Heat dissipation (defects/reviews/obdh.md — nominal ~15W in app mode)
        shared_params[0x031F] = s.heat_dissipation_w

    def _reboot(self, cause: int = REBOOT_COMMAND) -> None:
        """Reboot OBC — drop to boot loader, reset counters."""
        s = self._state
        s.reboot_count += 1
        s.last_reboot_cause = cause
        s.uptime_s = 0
        s.tc_rx_count = 0
        s.tc_acc_count = 0
        s.tc_rej_count = 0
        s.tm_pkt_count = 0
        s.watchdog_timer = 0
        s.sw_image = SW_BOOTLOADER
        s.mode = 1  # safe mode
        s.boot_app_pending = False
        s.boot_app_timer = 0.0

        # Increment per-unit boot count
        if s.active_obc == 0:
            s.boot_count_a += 1
        else:
            s.boot_count_b += 1

        # Auto-boot to application after 10s (unless inhibited)
        if not s.boot_inhibit:
            s.boot_app_pending = True
            s.boot_app_timer = 10.0

    def _switchover(self) -> None:
        """Switch to the other OBC unit (cold redundant)."""
        s = self._state
        old_unit = s.active_obc
        new_unit = 1 - old_unit
        s.active_obc = new_unit
        # Old unit goes to standby, new unit activates
        if old_unit == 0:
            s.obc_b_status = OBC_ACTIVE
        else:
            s.obc_b_status = OBC_STANDBY
        # Cold redundant: fresh start (no state transfer)
        s.last_reboot_cause = REBOOT_SWITCHOVER
        self._reboot(REBOOT_SWITCHOVER)

    def is_subsystem_reachable(self, subsystem_name: str) -> bool:
        """Check if a subsystem is reachable via the active CAN bus."""
        s = self._state
        active = s.active_bus  # 0=A, 1=B

        # Check if active bus is OK
        if active == 0 and s.bus_a_status == BUS_FAILED:
            return False
        if active == 1 and s.bus_b_status == BUS_FAILED:
            return False

        # Check if subsystem is on the active bus
        if active == 0:
            return subsystem_name in s.bus_a_subsystems
        else:
            return subsystem_name in s.bus_b_subsystems

    def record_tc_received(self):
        self._state.tc_rx_count += 1

    def record_tc_accepted(self):
        self._state.tc_acc_count += 1

    def record_tc_rejected(self):
        self._state.tc_rej_count += 1

    def record_tc_executed(self):
        # Best-effort: if a separate execution counter exists use it,
        # otherwise this is a no-op (acceptance counter is already
        # incremented at S1.1 emit time).
        if hasattr(self._state, "tc_exec_count"):
            self._state.tc_exec_count += 1

    def record_tm_packet(self):
        self._state.tm_pkt_count += 1

    def get_pending_event(self) -> tuple[int, str] | None:
        """Get and clear the pending event (if any)."""
        event = self._event_to_emit
        self._event_to_emit = None
        return event

    def record_event(self) -> bool:
        """Record an event in event buffer. Returns False if buffer full."""
        s = self._state
        if s.event_buf_fill >= s.event_buf_capacity:
            return False
        s.event_buf_fill += 1
        return True

    def record_alarm(self) -> bool:
        """Record an alarm in alarm buffer. Returns False if buffer full."""
        s = self._state
        if s.alarm_buf_fill >= s.alarm_buf_capacity:
            return False
        s.alarm_buf_fill += 1
        return True

    def get_telemetry(self) -> dict[int, float]:
        return {
            0x0302: self._state.cpu_load,
            0x030A: float(self._state.reboot_count),
        }

    # Commands accepted while running the bootloader image. Anything else
    # belongs to the application image and must be rejected pre-boot, otherwise
    # operators can drive memory scrubs, watchdog config, set_time, etc. before
    # the application is even running.
    _BOOTLOADER_ALLOWED = frozenset({
        "obc_boot_app",
        "obc_boot_inhibit",
        "obc_reboot",
        "obc_switch_unit",
        "obc_select_bus",
        "obc_clear_reboot_cnt",
        "diagnostic",
        "error_log",
    })

    def handle_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        command = cmd.get("command", "")

        # Bootloader gate: while sw_image == SW_BOOTLOADER, the application
        # command set is not yet loaded.
        if self._state.sw_image == SW_BOOTLOADER and command not in self._BOOTLOADER_ALLOWED:
            return {"success": False, "message": f"Bootloader: {command} not available pre-boot"}

        if command == "set_mode":
            m = int(cmd.get("mode", 0))
            if m in (0, 1, 2):
                self._state.mode = m
                return {"success": True}

        elif command == "set_time":
            self._state.obc_time_cuc = int(cmd.get("cuc", 0))
            return {"success": True}

        elif command == "gps_time_sync":
            # Synchronise onboard clock with GPS time.
            # GPS provides epoch seconds derived from the engine's sim_time.
            sp = cmd.get("shared_params", {})
            gps_fix = sp.get(0x0274, 0.0)  # gps_fix: 0=none, 1=2D, 2=3D, 3=3D+vel
            if gps_fix < 2:
                return {"success": False, "message": "GPS time sync requires 3D fix (current fix type: %d)" % int(gps_fix)}
            # Use engine sim_time as the "true" GPS time
            sim_time = cmd.get("sim_time")
            if sim_time is not None:
                gps_time_cuc = int(sim_time.timestamp())
            else:
                import time as _tm
                gps_time_cuc = int(_tm.time())
            old_cuc = self._state.obc_time_cuc
            time_jump_s = float(gps_time_cuc - old_cuc) if old_cuc > 0 else 0.0
            self._state.obc_time_cuc = gps_time_cuc
            return {"success": True, "time_jump_s": time_jump_s,
                    "message": f"OBC time synced to GPS: {gps_time_cuc} (jump {time_jump_s:.1f}s)"}

        elif command == "memory_scrub":
            self._state.scrub_active = True
            self._state.scrub_progress_pct = 0.0
            return {"success": True}

        elif command == "obc_reboot":
            self._reboot(REBOOT_COMMAND)
            return {"success": True}

        elif command == "obc_switch_unit":
            self._switchover()
            return {"success": True}

        elif command == "obc_select_bus":
            bus = int(cmd.get("bus", 0))
            if bus not in (0, 1):
                return {"success": False, "message": "Invalid bus (0=A, 1=B)"}
            # Check target bus status
            if bus == 0 and self._state.bus_a_status == BUS_FAILED:
                return {"success": False, "message": "Bus A is FAILED"}
            if bus == 1 and self._state.bus_b_status == BUS_FAILED:
                return {"success": False, "message": "Bus B is FAILED"}
            self._state.active_bus = bus
            return {"success": True}

        elif command == "obc_boot_app":
            s = self._state
            if s.sw_image == SW_APPLICATION:
                return {"success": False, "message": "Already in application"}
            s.boot_app_pending = True
            s.boot_app_timer = 10.0  # 10s CRC verification
            return {"success": True}

        elif command == "obc_boot_inhibit":
            inhibit = bool(cmd.get("inhibit", True))
            self._state.boot_inhibit = inhibit
            return {"success": True}

        elif command == "obc_clear_reboot_cnt":
            self._state.boot_count_a = 0
            self._state.boot_count_b = 0
            self._state.reboot_count = 0
            return {"success": True}

        elif command == "set_watchdog_period":
            period = int(cmd.get("period", 30))
            self._state.watchdog_period = max(1, period)
            return {"success": True}

        elif command == "watchdog_enable":
            self._state.watchdog_armed = True
            return {"success": True}

        elif command == "watchdog_disable":
            self._state.watchdog_armed = False
            return {"success": True}

        elif command == "diagnostic":
            s = self._state
            # Return a health summary as bytes
            health_data = struct.pack('>BfBBBBBBBf',
                s.mode,
                s.cpu_load,
                1 if s.watchdog_armed else 0,
                s.active_obc,
                s.obc_b_status,
                s.active_bus,
                s.bus_a_status,
                s.bus_b_status,
                s.sw_image,
                s.temp
            )
            return {"success": True, "data": health_data}

        elif command == "error_log":
            s = self._state
            # Return recent error entries: mem_errors, reboot_count, last_reboot_cause, seu_count
            error_data = struct.pack('>HHHH',
                s.mem_errors,
                s.reboot_count,
                s.last_reboot_cause,
                s.seu_count
            )
            return {"success": True, "data": error_data}

        return {"success": False, "message": f"Unknown: {command}"}

    def inject_failure(self, failure: str, magnitude: float = 1.0,
                       **kw) -> None:
        s = self._state

        if failure == "watchdog_reset":
            s.watchdog_armed = True
            s.watchdog_timer = s.watchdog_period

        elif failure == "memory_errors":
            s.mem_errors += int(kw.get("count", 5))

        elif failure == "cpu_spike":
            self._cpu_base = float(kw.get("load", 95.0))

        elif failure == "obc_crash":
            # Simulate OBC crash -> watchdog timeout -> boot loader
            self._reboot(REBOOT_WATCHDOG)

        elif failure == "bus_failure":
            bus = kw.get("bus", "A").upper()
            if bus == "A":
                s.bus_a_status = BUS_FAILED
            elif bus == "B":
                s.bus_b_status = BUS_FAILED

        elif failure == "boot_image_corrupt":
            s.boot_image_corrupt = True

        elif failure == "memory_corruption":
            # EDAC uncorrectable error — triggers reboot
            s.mem_errors += int(kw.get("count", 10))
            self._event_to_emit = (0x0302, "MEMORY_ERROR")
            self._reboot(REBOOT_MEMORY_ERROR)

        elif failure == "memory_segment_fail":
            # Permanent failure of a single mass-memory segment.
            # Drives the memory_segment_failure contingency procedure.
            seg = int(kw.get("segment", 0))
            if 0 <= seg < len(s.memory_segments):
                s.memory_segments[seg] = False
                # Increment uncorrectable error counter, raise mmm fill so the
                # operator can see capacity has shrunk.
                s.mem_errors += 1
                healthy = sum(1 for h in s.memory_segments if h)
                total = len(s.memory_segments)
                # Effective usage rises proportionally to the lost capacity.
                if total > 0 and healthy < total:
                    shrink = (total - healthy) / total
                    s.mmm_used_pct = min(100.0, s.mmm_used_pct + shrink * 100.0 / total)
                self._event_to_emit = (0x0302, "MEMORY_SEGMENT_FAIL")

        elif failure == "stuck_in_bootloader":
            # Persistent stuck-in-bootloader state — boot_image_corrupt is set
            # AND we drop the OBC into the bootloader image right now so the
            # operator sees sw_image=0 immediately and the recovery procedure
            # can be drilled without waiting for a reboot.
            s.boot_image_corrupt = True
            s.sw_image = SW_BOOTLOADER
            s.boot_app_pending = False
            s.boot_inhibit = True
            self._event_to_emit = (0x0305, "BOOT_FAILURE")

    def clear_failure(self, failure: str, **kw) -> None:
        s = self._state

        if failure == "watchdog_reset":
            # One-shot — just disarm the timer so it doesn't fire again.
            s.watchdog_timer = 0

        elif failure == "memory_errors":
            # Errors that already happened stay counted, but mark the inject
            # "cleared" by clamping the running counter (operator-driven scrub).
            s.mem_errors = max(0, s.mem_errors - int(kw.get("count", 5)))

        elif failure == "cpu_spike":
            self._cpu_base = 35.0

        elif failure == "bus_failure":
            bus = kw.get("bus", "A").upper()
            if bus == "A":
                s.bus_a_status = BUS_OK
            elif bus == "B":
                s.bus_b_status = BUS_OK

        elif failure == "boot_image_corrupt":
            s.boot_image_corrupt = False

        elif failure == "memory_corruption":
            s.mem_errors = 0

        elif failure == "obc_crash":
            pass  # Crash is a one-time event, no persistent state

        elif failure == "memory_segment_fail":
            seg = int(kw.get("segment", 0))
            if 0 <= seg < len(s.memory_segments):
                s.memory_segments[seg] = True

        elif failure == "stuck_in_bootloader":
            s.boot_image_corrupt = False
            s.boot_inhibit = False

    def get_state(self) -> dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self._state)

    def set_state(self, state: dict[str, Any]) -> None:
        for k, v in state.items():
            if hasattr(self._state, k):
                setattr(self._state, k, v)

    # S2 Device Access — device-level on/off control
    def set_device_state(self, device_id: int, on_off: bool) -> bool:
        """Set device on/off state. Returns True if successful."""
        if device_id not in self._state.device_states:
            return False
        self._state.device_states[device_id] = on_off
        return True

    def get_device_state(self, device_id: int) -> bool:
        """Get device on/off state. Returns True if on, False if off or invalid."""
        return self._state.device_states.get(device_id, False)

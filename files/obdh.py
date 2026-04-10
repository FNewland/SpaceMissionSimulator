"""
EO Mission Simulator — On-Board Data Handling (OBDH)
OBC mode state machine, TC/TM counters, uptime, memory management,
on-board time keeping, and failure injection.
"""
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict

from config import (
    P_OBC_MODE, P_OBC_TEMP, P_OBC_CPU_LOAD, P_MMM_USED_PCT,
    P_TC_RX_COUNT, P_TC_ACC_COUNT, P_TC_REJ_COUNT,
    P_TM_PKT_COUNT, P_UPTIME_S, P_OBC_TIME_CUC, P_REBOOT_COUNT,
    P_SW_VERSION,
    OBC_MODE_NOMINAL, OBC_MODE_SAFE, OBC_MODE_EMERGENCY,
    SC_MODE_NOMINAL, SC_MODE_SAFE,
    TIME_EPOCH,
)


@dataclass
class OBDHState:
    mode:          int   = OBC_MODE_NOMINAL
    temp:          float = 25.0
    cpu_load:      float = 35.0
    mmm_used_pct:  float = 20.0
    tc_rx_count:   int   = 0
    tc_acc_count:  int   = 0
    tc_rej_count:  int   = 0
    tm_pkt_count:  int   = 0
    uptime_s:      int   = 0
    reboot_count:  int   = 0
    sw_version:    int   = 0x0100   # v1.0
    # Memory checksum errors
    mem_errors:    int   = 0
    # Simulated OBC time (CUC seconds since J2000)
    obc_time_cuc:  int   = 0
    # Watchdog state
    _watchdog_armed: bool = True
    _watchdog_timer: int  = 0
    _watchdog_period: int = 30   # ticks before reboot if not patted


class OBDHSubsystem:
    """
    On-Board Data Handling subsystem.
    Maintains counters, mode state machine, and watchdog timer.
    """

    def __init__(self, dt_s: float = 1.0, epoch: datetime = None):
        self.dt    = dt_s
        self.state = OBDHState()
        self._epoch = epoch or TIME_EPOCH
        # Calculate initial CUC from current time
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.state.obc_time_cuc = int((now - self._epoch).total_seconds())
        self._boot_time = now
        # CPU load noise
        self._cpu_base = 35.0

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self, orbit_state, shared_params: Dict) -> None:
        s  = self.state
        dt = self.dt

        # --- Uptime & OBC time ---
        s.uptime_s      += int(dt)
        s.obc_time_cuc  += int(dt)

        # --- CPU load (idle baseline + noise + mode overhead) ---
        mode_load = {OBC_MODE_NOMINAL: 0, OBC_MODE_SAFE: -10, OBC_MODE_EMERGENCY: 20}
        s.cpu_load = (self._cpu_base + mode_load.get(s.mode, 0)
                      + random.gauss(0, 2.0))
        s.cpu_load = max(0.0, min(100.0, s.cpu_load))

        # --- Watchdog pat ---
        if s._watchdog_armed and s.mode == OBC_MODE_NOMINAL:
            s._watchdog_timer = 0   # automatically patted during nominal ops
        else:
            s._watchdog_timer += 1
            if s._watchdog_timer >= s._watchdog_period:
                self._reboot()

        # --- Shared params ---
        shared_params[P_OBC_MODE]       = s.mode
        shared_params[P_OBC_TEMP]       = s.temp        # updated by TCS
        shared_params[P_OBC_CPU_LOAD]   = s.cpu_load
        shared_params[P_MMM_USED_PCT]   = s.mmm_used_pct
        shared_params[P_TC_RX_COUNT]    = s.tc_rx_count
        shared_params[P_TC_ACC_COUNT]   = s.tc_acc_count
        shared_params[P_TC_REJ_COUNT]   = s.tc_rej_count
        shared_params[P_TM_PKT_COUNT]   = s.tm_pkt_count
        shared_params[P_UPTIME_S]       = s.uptime_s
        shared_params[P_OBC_TIME_CUC]   = s.obc_time_cuc
        shared_params[P_REBOOT_COUNT]   = s.reboot_count
        shared_params[P_SW_VERSION]     = s.sw_version

    # ------------------------------------------------------------------
    # TC accounting (called by service_handlers on each TC received)
    # ------------------------------------------------------------------

    def record_tc_received(self) -> None:
        self.state.tc_rx_count += 1

    def record_tc_accepted(self) -> None:
        self.state.tc_acc_count += 1

    def record_tc_rejected(self) -> None:
        self.state.tc_rej_count += 1

    def record_tm_packet(self) -> None:
        self.state.tm_pkt_count += 1

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def cmd_set_mode(self, mode: int) -> bool:
        if mode not in (OBC_MODE_NOMINAL, OBC_MODE_SAFE, OBC_MODE_EMERGENCY):
            return False
        self.state.mode = mode
        return True

    def cmd_set_time(self, cuc_seconds: int) -> bool:
        self.state.obc_time_cuc = cuc_seconds
        return True

    def cmd_update_mmm_used(self, used_pct: float) -> None:
        self.state.mmm_used_pct = max(0.0, min(100.0, used_pct))

    def cmd_memory_scrub(self) -> bool:
        """Reduce memory error count (S6 memory management)."""
        self.state.mem_errors = max(0, self.state.mem_errors - 1)
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reboot(self) -> None:
        s = self.state
        s.reboot_count += 1
        s.uptime_s      = 0
        s.tc_rx_count   = 0
        s.tc_acc_count  = 0
        s.tc_rej_count  = 0
        s.tm_pkt_count  = 0
        s.mode          = OBC_MODE_SAFE
        s._watchdog_timer = 0
        # OBC time is retained (battery-backed)

    # ------------------------------------------------------------------
    # Failure injection
    # ------------------------------------------------------------------

    def inject_watchdog_reset(self) -> None:
        """Force a watchdog timeout → reboot."""
        self.state._watchdog_armed  = True
        self.state._watchdog_timer  = self.state._watchdog_period
        # Reboot triggers on next tick

    def inject_memory_errors(self, count: int) -> None:
        self.state.mem_errors += count

    def inject_cpu_spike(self, load: float) -> None:
        """Override CPU load temporarily."""
        self._cpu_base = load

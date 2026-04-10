"""
EO Mission Simulator — FDIR State Machine
Three-level autonomous fault response coupled to S12 limit monitoring.
Level 1: per-parameter event-actions
Level 2: subsystem safe mode transitions
Level 3: spacecraft safe mode
"""
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field

from config import (
    P_BAT_SOC, P_BAT_TEMP, P_BUS_VOLTAGE,
    P_AOCS_MODE, P_ATT_ERROR, P_OBC_MODE,
    P_TEMP_OBC, P_REBOOT_COUNT,
    P_RW1_TEMP, P_RW2_TEMP, P_RW3_TEMP, P_RW4_TEMP,
    OBC_MODE_NOMINAL, OBC_MODE_SAFE,
    AOCS_MODE_NOMINAL, AOCS_MODE_SAFE,
    SC_MODE_NOMINAL, SC_MODE_SAFE, SC_MODE_EMERGENCY,
)


@dataclass
class FDIREvent:
    param_id:  int
    condition: str   # 'low' or 'high'
    threshold: float
    level:     int   # 1, 2, or 3
    action:    str   # description of action taken
    triggered: bool  = False
    count:     int   = 0


class FDIRSubsystem:
    """
    On-board FDIR manager.
    Checked each tick; autonomous actions are injected back into
    subsystem objects via callback closures set up by the SimulationEngine.
    """

    def __init__(self):
        self.sc_mode: int = SC_MODE_NOMINAL
        self._level1_callbacks: Dict[str, Callable] = {}
        self._events: List[FDIREvent] = []
        self._fdir_event_queue: List[dict] = []   # events to generate as S5 packets
        self._fdir_enabled: bool = True
        self._setup_default_rules()

    def _setup_default_rules(self) -> None:
        """Install default FDIR rule set."""
        self._events = [
            # Level 1 — parameter-level responses
            FDIREvent(P_BAT_SOC,    'low', 20.0, 1, 'payload_poweroff'),
            FDIREvent(P_BAT_SOC,    'low', 15.0, 2, 'safe_mode_eps'),
            FDIREvent(P_BUS_VOLTAGE,'low', 26.0, 2, 'safe_mode_eps'),
            FDIREvent(P_BAT_TEMP,   'high', 42.0, 1, 'heater_off_battery'),
            FDIREvent(P_BAT_TEMP,   'low',   1.0, 1, 'heater_on_battery'),
            FDIREvent(P_ATT_ERROR,  'high',  5.0, 2, 'safe_mode_aocs'),
            FDIREvent(P_TEMP_OBC,   'high', 65.0, 2, 'safe_mode_obc'),
            FDIREvent(P_REBOOT_COUNT,'high', 4.0, 3, 'spacecraft_emergency'),
            FDIREvent(P_RW1_TEMP,   'high', 65.0, 1, 'disable_rw1'),
            FDIREvent(P_RW2_TEMP,   'high', 65.0, 1, 'disable_rw2'),
            FDIREvent(P_RW3_TEMP,   'high', 65.0, 1, 'disable_rw3'),
            FDIREvent(P_RW4_TEMP,   'high', 65.0, 1, 'disable_rw4'),
        ]

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self, shared_params: Dict) -> None:
        if not self._fdir_enabled:
            return

        for ev in self._events:
            value = shared_params.get(ev.param_id, 0.0)
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue

            condition_met = (
                (ev.condition == 'low'  and value < ev.threshold) or
                (ev.condition == 'high' and value > ev.threshold)
            )

            if condition_met and not ev.triggered:
                ev.triggered = True
                ev.count    += 1
                self._execute_action(ev, value, shared_params)
            elif not condition_met and ev.triggered:
                ev.triggered = False   # reset when condition clears

    def _execute_action(self, ev: FDIREvent, value: float, shared_params: Dict) -> None:
        action = ev.action
        cb = self._level1_callbacks

        # Emit a FDIR event for S5 packet generation
        self._fdir_event_queue.append({
            'event_id':   0x8000 | (ev.param_id & 0x0FFF),
            'severity':   ev.level + 1,  # map FDIR level to EventSeverity
            'description': f"FDIR L{ev.level}: {action} | param=0x{ev.param_id:04X} val={value:.2f}",
        })

        if action == 'payload_poweroff' and 'payload_poweroff' in cb:
            cb['payload_poweroff']()
        elif action == 'heater_on_battery' and 'heater_on_battery' in cb:
            cb['heater_on_battery']()
        elif action == 'heater_off_battery' and 'heater_off_battery' in cb:
            cb['heater_off_battery']()
        elif action == 'safe_mode_eps' and 'safe_mode_eps' in cb:
            cb['safe_mode_eps']()
            if self.sc_mode == SC_MODE_NOMINAL:
                self.sc_mode = SC_MODE_SAFE
        elif action == 'safe_mode_aocs' and 'safe_mode_aocs' in cb:
            cb['safe_mode_aocs']()
            if self.sc_mode == SC_MODE_NOMINAL:
                self.sc_mode = SC_MODE_SAFE
        elif action == 'safe_mode_obc' and 'safe_mode_obc' in cb:
            cb['safe_mode_obc']()
            if self.sc_mode == SC_MODE_NOMINAL:
                self.sc_mode = SC_MODE_SAFE
        elif action == 'spacecraft_emergency':
            self.sc_mode = SC_MODE_EMERGENCY
        elif action.startswith('disable_rw') and action in cb:
            cb[action]()

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def register_callback(self, action_name: str, fn: Callable) -> None:
        self._level1_callbacks[action_name] = fn

    # ------------------------------------------------------------------
    # Command control
    # ------------------------------------------------------------------

    def cmd_enable_fdir(self, enabled: bool) -> None:
        self._fdir_enabled = enabled

    def cmd_set_sc_mode(self, mode: int) -> bool:
        if mode not in (SC_MODE_NOMINAL, SC_MODE_SAFE, SC_MODE_EMERGENCY):
            return False
        self.sc_mode = mode
        return True

    # ------------------------------------------------------------------
    # Event retrieval (consumed by engine for S5 packet generation)
    # ------------------------------------------------------------------

    def pop_events(self) -> List[dict]:
        evs = list(self._fdir_event_queue)
        self._fdir_event_queue.clear()
        return evs

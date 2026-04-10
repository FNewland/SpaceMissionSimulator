"""SMO Simulator — Advanced FDIR with fault cascading, load shedding, and recovery.

This module provides:
- Fault propagation and cascading effects across subsystems
- Priority-based load shedding with SoC thresholds
- Recovery state machine with escalation tracking
- Procedure execution integration
- FDIR event generation for S5 reporting
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Callable
from enum import IntEnum

logger = logging.getLogger(__name__)

SC_MODE_NOMINAL = 0
SC_MODE_SAFE = 1
SC_MODE_EMERGENCY = 2

LOAD_SHED_STAGE_NORMAL = 0
LOAD_SHED_STAGE_1 = 1
LOAD_SHED_STAGE_2 = 2
LOAD_SHED_STAGE_3 = 3


def evaluate_condition(condition: str, value: float, threshold: float) -> bool:
    """Evaluate an FDIR condition string."""
    cond = condition.strip()
    if cond.startswith("<"):
        return value < threshold
    elif cond.startswith(">"):
        return value > threshold
    elif cond.startswith("=="):
        return abs(value - threshold) < 0.001
    return False


@dataclass
class FaultPropagationRule:
    """Models how a fault cascades to other subsystems."""
    fault_id: str
    description: str
    primary_param: str
    threshold: Optional[float] = None
    cascades: list[dict] = field(default_factory=list)


@dataclass
class RecoveryState:
    """Tracks recovery attempts for a specific fault."""
    fault_id: str
    response_taken: str
    timestamp: float
    recovery_attempts: int = 0
    max_attempts: int = 3
    last_attempt_time: float = 0.0
    state: str = "IDLE"  # IDLE, RUNNING, COMPLETE, FAILED


class FaultPropagator:
    """Manages fault cascading and cross-subsystem effects."""

    def __init__(self):
        self._propagation_rules: dict[str, FaultPropagationRule] = {}
        self._active_faults: dict[str, float] = {}  # fault_id -> time detected
        self._recovery_states: dict[str, RecoveryState] = {}
        self._callbacks: dict[str, Callable] = {}

    def register_rule(self, rule: FaultPropagationRule) -> None:
        """Register a fault propagation rule."""
        self._propagation_rules[rule.fault_id] = rule
        logger.info("Registered propagation rule: %s", rule.fault_id)

    def register_callback(self, action: str, callback: Callable) -> None:
        """Register a callback for a cascading action."""
        self._callbacks[action] = callback

    def detect_fault(self, fault_id: str, current_time: float) -> list[dict]:
        """Detect a fault and return cascading actions."""
        if fault_id in self._active_faults:
            return []  # Already detected

        self._active_faults[fault_id] = current_time
        rule = self._propagation_rules.get(fault_id)
        if not rule:
            return []

        cascades = []
        for cascade in rule.cascades:
            cascades.append({
                "fault_id": fault_id,
                "target_subsystem": cascade["target_subsystem"],
                "action": cascade["action"],
                "description": cascade.get("description", ""),
                "delay_s": cascade.get("delay_s", 0.0),
                "timestamp": current_time,
            })
        return cascades

    def clear_fault(self, fault_id: str) -> None:
        """Clear a detected fault."""
        self._active_faults.pop(fault_id, None)
        logger.info("Fault cleared: %s", fault_id)

    def active_faults(self) -> list[str]:
        """Return list of currently active fault IDs."""
        return list(self._active_faults.keys())

    def get_recovery_state(self, fault_id: str) -> Optional[RecoveryState]:
        """Get recovery state for a fault."""
        return self._recovery_states.get(fault_id)

    def create_recovery_state(self, fault_id: str, response: str,
                            current_time: float, max_attempts: int = 3) -> RecoveryState:
        """Create a new recovery state for a fault."""
        rs = RecoveryState(
            fault_id=fault_id,
            response_taken=response,
            timestamp=current_time,
            max_attempts=max_attempts,
        )
        self._recovery_states[fault_id] = rs
        return rs

    def increment_recovery_attempt(self, fault_id: str, current_time: float) -> bool:
        """Increment recovery attempt counter. Returns True if more attempts available."""
        rs = self._recovery_states.get(fault_id)
        if not rs:
            return False
        rs.recovery_attempts += 1
        rs.last_attempt_time = current_time
        return rs.recovery_attempts < rs.max_attempts


class LoadSheddingManager:
    """Manages priority-based load shedding based on battery SoC."""

    def __init__(self):
        self._current_stage = LOAD_SHED_STAGE_NORMAL
        self._stage_config: dict[int, dict] = {}
        self._callbacks: dict[str, Callable] = {}
        self._last_stage_change = 0.0

    def register_stage_config(self, stage: int, config: dict) -> None:
        """Register configuration for a load shedding stage."""
        self._stage_config[stage] = config
        logger.info("Registered load shedding stage %d: %s", stage, config.get("name", ""))

    def register_callback(self, action: str, callback: Callable) -> None:
        """Register a callback for a subsystem action."""
        self._callbacks[action] = callback

    def get_required_stage(self, soc: float) -> int:
        """Determine required load shedding stage based on SoC."""
        if soc >= 30.0:
            return LOAD_SHED_STAGE_NORMAL
        elif soc >= 20.0:
            return LOAD_SHED_STAGE_1
        elif soc >= 10.0:
            return LOAD_SHED_STAGE_2
        else:
            return LOAD_SHED_STAGE_3

    def update_stage(self, soc: float, current_time: float) -> Optional[int]:
        """Check if stage change is needed. Returns new stage or None."""
        required_stage = self.get_required_stage(soc)
        if required_stage != self._current_stage:
            self._current_stage = required_stage
            self._last_stage_change = current_time
            return required_stage
        return None

    def execute_stage(self, stage: int) -> None:
        """Execute all commands for a given stage."""
        config = self._stage_config.get(stage)
        if not config:
            return

        logger.info("Executing load shedding stage %d: %s", stage, config.get("name", ""))

        # Execute subsystem commands
        for cmd in config.get("subsystem_commands", []):
            action_key = f"{cmd['subsystem']}_{cmd['command']}"
            callback = self._callbacks.get(action_key)
            if callback:
                try:
                    callback(cmd.get("value"))
                except Exception as e:
                    logger.warning("Load shed command error (%s): %s", action_key, e)

    def current_stage(self) -> int:
        """Return current load shedding stage."""
        return self._current_stage

    def stage_name(self, stage: int) -> str:
        """Return name of a stage."""
        config = self._stage_config.get(stage, {})
        return config.get("name", f"Stage {stage}")


class ProcedureExecutor:
    """Manages procedure loading and execution."""

    def __init__(self):
        self._procedures: dict[str, dict] = {}
        self._active_procedures: dict[str, "ProcedureExecution"] = {}
        self._callbacks: dict[str, Callable] = {}
        self._event_callback: Optional[Callable] = None

    def register_procedure(self, proc_id: str, config: dict) -> None:
        """Register a procedure definition."""
        self._procedures[proc_id] = config
        logger.info("Registered procedure: %s", proc_id)

    def register_command_callback(self, cmd_name: str, callback: Callable) -> None:
        """Register a callback for a procedure command."""
        self._callbacks[cmd_name] = callback

    def register_event_callback(self, callback: Callable) -> None:
        """Register callback for procedure events."""
        self._event_callback = callback

    def start_procedure(self, proc_id: str, current_time: float) -> Optional[str]:
        """Start executing a procedure. Returns execution ID."""
        import copy
        config = self._procedures.get(proc_id)
        if not config:
            logger.warning("Unknown procedure: %s", proc_id)
            return None

        exec_id = f"{proc_id}_{int(current_time)}"
        # Deep copy steps to avoid modifying original procedure config
        steps_copy = copy.deepcopy(config.get("steps", []))
        execution = ProcedureExecution(
            exec_id=exec_id,
            proc_id=proc_id,
            steps=steps_copy,
            start_time=current_time,
        )
        self._active_procedures[exec_id] = execution

        if self._event_callback:
            self._event_callback({
                "event_id": 0x0F07,  # PROCEDURE_STARTED
                "procedure_id": proc_id,
                "execution_id": exec_id,
                "timestamp": current_time,
            })

        logger.info("Started procedure: %s (execution: %s)", proc_id, exec_id)
        return exec_id

    def tick_procedures(self, current_time: float) -> None:
        """Advance all active procedures."""
        completed = []
        for exec_id, proc in list(self._active_procedures.items()):
            if proc.state == "COMPLETE" or proc.state == "FAILED":
                completed.append(exec_id)
                continue

            proc.tick(current_time)

            # Execute steps that are due
            for step in proc.steps:
                if step.get("executed"):
                    continue
                step_delay = step.get("delay_s", 0.0)
                if current_time - proc.start_time >= step_delay:
                    self._execute_step(proc, step, current_time)
                    step["executed"] = True

            # Check if procedure complete
            all_done = all(s.get("executed", False) for s in proc.steps)
            if all_done and proc.state == "RUNNING":
                proc.state = "COMPLETE"
                if self._event_callback:
                    self._event_callback({
                        "event_id": 0x0F08,  # PROCEDURE_COMPLETED
                        "procedure_id": proc.proc_id,
                        "execution_id": exec_id,
                        "timestamp": current_time,
                    })
                completed.append(exec_id)

        for exec_id in completed:
            self._active_procedures.pop(exec_id, None)

    def _execute_step(self, proc: "ProcedureExecution", step: dict,
                     current_time: float) -> None:
        """Execute a single procedure step."""
        cmd = step.get("command", "")
        params = step.get("params", {})

        callback = self._callbacks.get(cmd)
        if callback:
            try:
                callback(params)
                logger.info("Procedure step executed: %s -> %s", proc.proc_id, cmd)
            except Exception as e:
                logger.warning("Procedure step error (%s): %s", cmd, e)
                proc.state = "FAILED"
                if self._event_callback:
                    self._event_callback({
                        "event_id": 0x0F09,  # PROCEDURE_FAILED
                        "procedure_id": proc.proc_id,
                        "execution_id": proc.exec_id,
                        "timestamp": current_time,
                    })

    def get_active_procedures(self) -> list[dict]:
        """Return list of active procedure executions."""
        return [
            {
                "exec_id": proc.exec_id,
                "proc_id": proc.proc_id,
                "state": proc.state,
                "step": proc.current_step,
                "total_steps": len(proc.steps),
            }
            for proc in self._active_procedures.values()
        ]


@dataclass
class ProcedureExecution:
    """Tracks the execution state of a procedure."""
    exec_id: str
    proc_id: str
    steps: list[dict]
    start_time: float
    state: str = "RUNNING"
    current_step: int = 0
    completed_steps: int = 0

    def tick(self, current_time: float) -> None:
        """Advance procedure execution."""
        elapsed = current_time - self.start_time
        # Track elapsed time for step delays

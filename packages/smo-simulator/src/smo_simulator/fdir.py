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
    """Models how a fault cascades to other subsystems.

    ``condition`` is explicit in the configuration rather than inferred from
    the parameter name: the file declares thresholds like ``2.0`` for
    "fewer than two wheels" and ``80.0`` for "hotter than 80 C", and guessing
    the direction from the description is exactly the kind of drift the
    contract tests exist to stop.  Supported: ``<``, ``>``, ``==``,
    ``bitset`` (any bit named in ``flags`` set in an integer bitmask).
    """
    fault_id: str
    description: str
    primary_param: str
    threshold: Optional[float] = None
    cascades: list[dict] = field(default_factory=list)
    condition: str = "<"
    flags: list[int] = field(default_factory=list)
    persist_s: float = 0.0
    recovery: str = ""
    #: Optional precondition: {"param": <dotted name>, "min": <float>}. The
    #: rule is not evaluated at all unless the named parameter is >= min.
    #: Without this, "fewer than two wheels are enabled" is true whenever the
    #: reaction-wheel bus is simply unpowered, and FDIR declares a double wheel
    #: failure on a spacecraft that has not been switched on yet.
    requires: dict = field(default_factory=dict)

    def evaluate(self, value: float) -> bool:
        """True when *value* means this fault is present."""
        cond = (self.condition or "<").strip()
        if cond == "bitset":
            try:
                mask = int(value)
            except (TypeError, ValueError):
                return False
            return any((mask >> int(b)) & 1 for b in self.flags)
        if self.threshold is None:
            return False
        if cond.startswith("<"):
            return value < self.threshold
        if cond.startswith(">"):
            return value > self.threshold
        if cond.startswith("=="):
            return abs(value - self.threshold) < 0.5
        return False


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
    level: int = 0            # current escalation level (1..3), 0 = not started
    level_started: float = 0.0
    exec_id: str = ""         # active procedure execution, if any
    procedure: str = ""       # procedure id started at this level
    group: str = ""           # recovery_actions group in fault_propagation.yaml


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

    def rules(self) -> list[FaultPropagationRule]:
        return list(self._propagation_rules.values())

    def fault_detected_at(self, fault_id: str) -> Optional[float]:
        return self._active_faults.get(fault_id)

    def recovery_states(self) -> dict[str, RecoveryState]:
        return dict(self._recovery_states)

    # ─── State save/restore (plan A-4) ───────────────────────────────

    def get_state(self) -> dict:
        return {
            "active_faults": dict(self._active_faults),
            "recovery_states": {
                fid: {
                    "fault_id": rs.fault_id, "response_taken": rs.response_taken,
                    "timestamp": rs.timestamp,
                    "recovery_attempts": rs.recovery_attempts,
                    "max_attempts": rs.max_attempts,
                    "last_attempt_time": rs.last_attempt_time,
                    "state": rs.state, "level": rs.level,
                    "level_started": rs.level_started, "exec_id": rs.exec_id,
                    "procedure": rs.procedure, "group": rs.group,
                }
                for fid, rs in self._recovery_states.items()
            },
        }

    def set_state(self, state: dict) -> None:
        self._active_faults = {str(k): float(v) for k, v in
                               (state.get("active_faults") or {}).items()}
        self._recovery_states = {}
        for fid, d in (state.get("recovery_states") or {}).items():
            self._recovery_states[fid] = RecoveryState(**d)


class LoadSheddingManager:
    """Manages priority-based load shedding based on battery SoC."""

    # Entry thresholds (battery SoC %) for stages 1/2/3, highest first.
    # Module-level fallback only — the engine overrides these from
    # simulation.yaml eps.load_shed.soc_stage_thresholds_pct (defect #51).
    DEFAULT_SOC_STAGE_THRESHOLDS = (30.0, 20.0, 10.0)

    def __init__(self):
        self._current_stage = LOAD_SHED_STAGE_NORMAL
        self._stage_config: dict[int, dict] = {}
        self._callbacks: dict[str, Callable] = {}
        self._last_stage_change = 0.0
        self._soc_thresholds: list[float] = list(self.DEFAULT_SOC_STAGE_THRESHOLDS)

    def set_soc_stage_thresholds(self, thresholds: list[float]) -> None:
        """Set stage 1/2/3 SoC entry thresholds (descending)."""
        vals = [float(t) for t in thresholds if t is not None]
        if len(vals) != 3:
            logger.warning("Load shed: expected 3 SoC stage thresholds, got %r "
                           "— keeping %r", thresholds, self._soc_thresholds)
            return
        self._soc_thresholds = sorted(vals, reverse=True)
        logger.info("Load shed SoC stage thresholds: %s", self._soc_thresholds)

    def soc_stage_thresholds(self) -> list[float]:
        return list(self._soc_thresholds)

    def register_stage_config(self, stage: int, config: dict) -> None:
        """Register configuration for a load shedding stage."""
        self._stage_config[stage] = config
        logger.info("Registered load shedding stage %d: %s", stage, config.get("name", ""))

    def register_callback(self, action: str, callback: Callable) -> None:
        """Register a callback for a subsystem action."""
        self._callbacks[action] = callback

    def get_required_stage(self, soc: float) -> int:
        """Determine required load shedding stage based on SoC.

        Thresholds come from simulation.yaml (via ``set_soc_stage_thresholds``)
        rather than being hard-coded here, so the ladder has a single
        configurable source.
        """
        t1, t2, t3 = self._soc_thresholds
        if soc >= t1:
            return LOAD_SHED_STAGE_NORMAL
        elif soc >= t2:
            return LOAD_SHED_STAGE_1
        elif soc >= t3:
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

    # ─── State save/restore (plan A-4) ───────────────────────────────
    # The stage used to reset to NORMAL on breakpoint load while the restored
    # power lines were still shed (brief 08 B-2).

    def get_state(self) -> dict:
        return {"current_stage": self._current_stage,
                "last_stage_change": self._last_stage_change}

    def set_state(self, state: dict) -> None:
        self._current_stage = int(state.get("current_stage",
                                            LOAD_SHED_STAGE_NORMAL))
        self._last_stage_change = float(state.get("last_stage_change", 0.0))


class ProcedureExecutor:
    """Manages procedure loading and execution."""

    def __init__(self):
        self._procedures: dict[str, dict] = {}
        self._active_procedures: dict[str, "ProcedureExecution"] = {}
        self._callbacks: dict[str, Callable] = {}
        self._event_callback: Optional[Callable] = None
        self._last_finished: Optional["ProcedureExecution"] = None

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
                self._last_finished = proc
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

            # Step counters drive the proc.* telemetry (0x0900-0x0903) and the
            # progress bar in the FDIR window. `ProcedureExecution.tick` never
            # updated them, so both read 0 for the whole run.
            proc.completed_steps = sum(1 for s in proc.steps if s.get("executed"))
            proc.current_step = min(proc.completed_steps + 1, len(proc.steps)) \
                if proc.steps else 0

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
                "completed_steps": proc.completed_steps,
                "total_steps": len(proc.steps),
                "progress_pct": (100.0 * proc.completed_steps / len(proc.steps)
                                 if proc.steps else 0.0),
            }
            for proc in self._active_procedures.values()
        ]

    def execution_state(self, exec_id: str) -> Optional[str]:
        proc = self._active_procedures.get(exec_id)
        if proc is not None:
            return proc.state
        if self._last_finished is not None and self._last_finished.exec_id == exec_id:
            return self._last_finished.state
        return None

    def registered_procedures(self) -> list[str]:
        return sorted(self._procedures)

    # ─── State save/restore (plan A-4) ───────────────────────────────

    def get_state(self) -> dict:
        # `steps` is deep-copied out and in: the step dicts carry the per-run
        # `executed` flag that `tick_procedures` writes, so handing the live
        # list to a breakpoint (or restoring the breakpoint's own list into a
        # live execution) let snapshot and running procedure mutate each
        # other (G2-f).
        import copy
        return {
            "active": [
                {
                    "exec_id": p.exec_id, "proc_id": p.proc_id,
                    "steps": copy.deepcopy(p.steps), "start_time": p.start_time,
                    "state": p.state, "current_step": p.current_step,
                    "completed_steps": p.completed_steps,
                }
                for p in self._active_procedures.values()
            ],
        }

    def set_state(self, state: dict) -> None:
        import copy
        self._active_procedures = {}
        for d in state.get("active") or []:
            ex = ProcedureExecution(
                exec_id=d["exec_id"], proc_id=d["proc_id"],
                steps=copy.deepcopy(d.get("steps", [])),
                start_time=d.get("start_time", 0.0),
                state=d.get("state", "RUNNING"),
                current_step=d.get("current_step", 0),
                completed_steps=d.get("completed_steps", 0),
            )
            self._active_procedures[ex.exec_id] = ex


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

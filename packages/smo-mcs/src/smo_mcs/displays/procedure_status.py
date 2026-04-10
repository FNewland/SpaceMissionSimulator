"""Procedure Status Panel.

Displays procedure execution status, available procedures, and execution log.
"""
from dataclasses import dataclass
from typing import Optional
from enum import Enum


class ProcedureState(Enum):
    """Procedure execution state."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABORTED = "aborted"
    ERROR = "error"


@dataclass
class ProcedureStep:
    """Single procedure step."""
    step_number: int
    name: str
    description: str
    command: Optional[str]
    wait_time_s: float
    verification: Optional[str]


@dataclass
class ExecutingProcedure:
    """Currently executing procedure."""
    procedure_id: str
    name: str
    description: str
    state: ProcedureState
    current_step: int
    total_steps: int
    steps: list[ProcedureStep]
    start_time: float
    estimated_end_time: Optional[float]


class ProcedureStatusPanel:
    """Manages procedure status display."""

    def __init__(self):
        self._available_procedures: list[dict] = []
        self._executing_procedure: Optional[ExecutingProcedure] = None
        self._execution_log: list[dict] = []
        self._procedure_index: list[dict] = []

    def load_procedure_index(self, index: list[dict]) -> None:
        """Load available procedures from index."""
        self._available_procedures = index

    def set_executing_procedure(self, proc: dict) -> None:
        """Set the currently executing procedure."""
        try:
            steps = []
            for i, step_dict in enumerate(proc.get("steps", [])):
                step = ProcedureStep(
                    step_number=i + 1,
                    name=step_dict.get("name", f"Step {i+1}"),
                    description=step_dict.get("description", ""),
                    command=step_dict.get("command"),
                    wait_time_s=float(step_dict.get("wait_time_s", 0)),
                    verification=step_dict.get("verification"),
                )
                steps.append(step)

            state_str = proc.get("state", "idle").lower()
            try:
                state = ProcedureState(state_str)
            except ValueError:
                state = ProcedureState.IDLE

            self._executing_procedure = ExecutingProcedure(
                procedure_id=proc.get("id", ""),
                name=proc.get("name", "Unknown"),
                description=proc.get("description", ""),
                state=state,
                current_step=proc.get("current_step", 0),
                total_steps=len(steps),
                steps=steps,
                start_time=proc.get("start_time", 0),
                estimated_end_time=proc.get("estimated_end_time"),
            )
        except Exception:
            self._executing_procedure = None

    def log_step_execution(self, step_num: int, status: str, message: str = "") -> None:
        """Log a step execution result."""
        entry = {
            "step": step_num,
            "status": status,
            "message": message,
            "timestamp": __import__("time").time(),
        }
        self._execution_log.append(entry)
        # Keep last 100 entries
        self._execution_log = self._execution_log[-100:]

    def clear_executing_procedure(self) -> None:
        """Clear the executing procedure."""
        self._executing_procedure = None

    def get_display_data(self) -> dict:
        """Get procedure status panel display data."""
        result = {
            "available_procedures": self._available_procedures,
            "executing_procedure": None,
            "execution_log": self._execution_log[-20:],  # Last 20 entries
        }

        if self._executing_procedure:
            proc = self._executing_procedure
            result["executing_procedure"] = {
                "procedure_id": proc.procedure_id,
                "name": proc.name,
                "description": proc.description,
                "state": proc.state.value,
                "current_step": proc.current_step,
                "total_steps": proc.total_steps,
                "progress_percent": (
                    int((proc.current_step / proc.total_steps) * 100)
                    if proc.total_steps > 0
                    else 0
                ),
                "steps": [
                    {
                        "number": s.step_number,
                        "name": s.name,
                        "description": s.description,
                        "command": s.command,
                        "wait_time_s": s.wait_time_s,
                        "is_current": s.step_number == proc.current_step,
                        "is_completed": s.step_number < proc.current_step,
                    }
                    for s in proc.steps
                ],
            }

        return result

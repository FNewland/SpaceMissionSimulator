"""SMO MCS — Procedure Execution Engine.

Executes structured command sequences from activity types / procedures.
Supports states: IDLE → LOADED → RUNNING → PAUSED → COMPLETED / ABORTED / FAILED.
Manual override: pause, skip step, insert ad-hoc commands, resume.
Step-by-step mode: advance one step at a time with operator confirmation.
"""
from __future__ import annotations

import asyncio
import logging
import struct
import time
from enum import IntEnum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class ProcedureState(IntEnum):
    IDLE = 0
    LOADED = 1
    RUNNING = 2
    PAUSED = 3
    COMPLETED = 4
    ABORTED = 5
    FAILED = 6


class StepResult(IntEnum):
    PENDING = 0
    RUNNING = 1
    PASSED = 2
    FAILED = 3
    SKIPPED = 4
    TIMEOUT = 5


class ProcedureRunner:
    """Executes command sequences with verification, waits, and manual override.

    Parameters
    ----------
    send_command_fn : async callable(service, subtype, data_hex) -> dict
        Function to send a PUS TC and return {"status": "sent", "seq": N}.
    get_telemetry_fn : callable(param_path) -> float | int | bool | None
        Function to read a current telemetry value by dot-path (e.g. "payload.mode").
    """

    def __init__(
        self,
        send_command_fn: Callable[..., Coroutine],
        get_telemetry_fn: Callable[[str], Any],
    ):
        self._send_cmd = send_command_fn
        self._get_tm = get_telemetry_fn

        self.state = ProcedureState.IDLE
        self.procedure_name: str = ""
        self.procedure_ref: str = ""
        self.steps: list[dict] = []
        self.current_step: int = -1
        self.step_results: list[dict] = []
        self.step_by_step: bool = False
        self._execution_log: list[dict] = []
        self._task: asyncio.Task | None = None
        self._step_event: asyncio.Event = asyncio.Event()
        self._pause_event: asyncio.Event = asyncio.Event()
        self._pause_event.set()  # Not paused initially

    # ── Loading ──────────────────────────────────────────────────────

    def load(
        self,
        name: str,
        steps: list[dict],
        procedure_ref: str = "",
        step_by_step: bool = False,
    ) -> dict:
        """Load a procedure/command sequence for execution."""
        if self.state in (ProcedureState.RUNNING, ProcedureState.PAUSED):
            return {"error": "A procedure is currently active. Abort it first."}

        self.procedure_name = name
        self.procedure_ref = procedure_ref
        self.steps = list(steps)
        self.current_step = -1
        self.step_by_step = step_by_step
        self.step_results = [
            {"index": i, "description": _step_desc(s), "result": int(StepResult.PENDING)}
            for i, s in enumerate(steps)
        ]
        self.state = ProcedureState.LOADED
        self._execution_log = []
        self._log("Procedure loaded", f"{name} ({len(steps)} steps)")
        return self.status()

    # ── Execution control ────────────────────────────────────────────

    async def start(self) -> dict:
        """Begin procedure execution."""
        if self.state != ProcedureState.LOADED:
            return {"error": f"Cannot start from state {self.state.name}"}

        self.state = ProcedureState.RUNNING
        self._pause_event.set()
        self._step_event.set()
        self._log("Procedure started", self.procedure_name)
        self._task = asyncio.create_task(self._run_loop())
        return self.status()

    async def pause(self) -> dict:
        """Pause execution after current step completes."""
        if self.state != ProcedureState.RUNNING:
            return {"error": f"Cannot pause from state {self.state.name}"}
        self.state = ProcedureState.PAUSED
        self._pause_event.clear()
        self._log("Procedure paused", f"at step {self.current_step}")
        return self.status()

    async def resume(self) -> dict:
        """Resume a paused procedure."""
        if self.state != ProcedureState.PAUSED:
            return {"error": f"Cannot resume from state {self.state.name}"}
        self.state = ProcedureState.RUNNING
        self._pause_event.set()
        if self.step_by_step:
            self._step_event.set()
        self._log("Procedure resumed", f"from step {self.current_step}")
        return self.status()

    async def abort(self) -> dict:
        """Abort execution immediately."""
        if self.state not in (ProcedureState.RUNNING, ProcedureState.PAUSED):
            return {"error": f"Cannot abort from state {self.state.name}"}
        self.state = ProcedureState.ABORTED
        self._pause_event.set()  # Unblock if paused
        self._step_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
        self._log("Procedure aborted", f"at step {self.current_step}")
        return self.status()

    async def step_advance(self) -> dict:
        """In step-by-step mode, advance to the next step."""
        if self.state not in (ProcedureState.RUNNING, ProcedureState.PAUSED):
            return {"error": f"Cannot step from state {self.state.name}"}
        if self.state == ProcedureState.PAUSED:
            self.state = ProcedureState.RUNNING
            self._pause_event.set()
        self._step_event.set()
        return self.status()

    async def skip_step(self) -> dict:
        """Skip the current step and move to the next."""
        if self.state not in (ProcedureState.RUNNING, ProcedureState.PAUSED):
            return {"error": f"Cannot skip from state {self.state.name}"}
        if 0 <= self.current_step < len(self.step_results):
            self.step_results[self.current_step]["result"] = int(StepResult.SKIPPED)
            self._log("Step skipped", f"step {self.current_step}")
        if self.state == ProcedureState.PAUSED:
            self.state = ProcedureState.RUNNING
            self._pause_event.set()
        self._step_event.set()
        return self.status()

    async def override_command(
        self, service: int, subtype: int, data_hex: str = ""
    ) -> dict:
        """Inject a manual command while procedure is paused."""
        if self.state != ProcedureState.PAUSED:
            return {"error": "Manual override only available when paused"}
        self._log("Manual override command", f"S{service}.{subtype} data={data_hex}")
        result = await self._send_cmd(service, subtype, data_hex)
        return {"override_result": result}

    # ── Status ───────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "state": self.state.name,
            "state_code": int(self.state),
            "procedure_name": self.procedure_name,
            "procedure_ref": self.procedure_ref,
            "total_steps": len(self.steps),
            "current_step": self.current_step,
            "step_by_step": self.step_by_step,
            "step_results": self.step_results,
            "log": self._execution_log[-50:],
        }

    # ── Internal execution loop ──────────────────────────────────────

    async def _run_loop(self) -> None:
        """Main execution loop — iterates through steps."""
        try:
            for idx in range(len(self.steps)):
                if self.state in (
                    ProcedureState.ABORTED,
                    ProcedureState.FAILED,
                ):
                    break

                # Wait if paused
                await self._pause_event.wait()
                if self.state == ProcedureState.ABORTED:
                    break

                # In step-by-step mode, wait for advance signal
                if self.step_by_step and idx > 0:
                    self._step_event.clear()
                    self._log("Awaiting step advance", f"step {idx}")
                    # Auto-pause in step-by-step mode
                    self.state = ProcedureState.PAUSED
                    self._pause_event.clear()
                    await self._step_event.wait()
                    if self.state == ProcedureState.ABORTED:
                        break
                    if self.state != ProcedureState.RUNNING:
                        self.state = ProcedureState.RUNNING

                self.current_step = idx
                step = self.steps[idx]

                # Check if this step was skipped
                if self.step_results[idx]["result"] == int(StepResult.SKIPPED):
                    continue

                self.step_results[idx]["result"] = int(StepResult.RUNNING)
                success = await self._execute_step(idx, step)

                if self.state == ProcedureState.ABORTED:
                    break

                if success:
                    self.step_results[idx]["result"] = int(StepResult.PASSED)
                elif self.step_results[idx]["result"] != int(StepResult.SKIPPED):
                    self.step_results[idx]["result"] = int(StepResult.FAILED)
                    self.state = ProcedureState.FAILED
                    self._log("Procedure failed", f"step {idx} failed")
                    return

            if self.state == ProcedureState.RUNNING:
                self.state = ProcedureState.COMPLETED
                self._log("Procedure completed", self.procedure_name)

        except asyncio.CancelledError:
            self._log("Procedure cancelled", "task cancelled")
        except Exception as exc:
            self.state = ProcedureState.FAILED
            self._log("Procedure error", str(exc))
            logger.exception("Procedure runner error")

    async def _execute_step(self, idx: int, step: dict) -> bool:
        """Execute a single step. Returns True on success."""
        # Timed wait step
        if "wait_s" in step:
            wait_s = float(step["wait_s"])
            self._log("Wait", f"{wait_s}s")
            await asyncio.sleep(wait_s)
            return True

        # Wait-for-condition step
        if "wait_for" in step:
            return await self._wait_for_condition(step["wait_for"])

        # Command step
        if "service" in step:
            return await self._execute_command(idx, step)

        # Unknown step type — skip
        self._log("Unknown step type", str(step))
        return True

    async def _execute_command(self, idx: int, step: dict) -> bool:
        """Send a PUS command and optionally verify."""
        service = int(step["service"])
        subtype = int(step["subtype"])
        desc = step.get("description", f"S{service}.{subtype}")

        # Build data_hex from step fields
        data_hex = step.get("data_hex", "")
        if not data_hex:
            data_hex = self._build_data_hex(step)

        self._log("Sending command", f"{desc} (S{service}.{subtype})")

        try:
            result = await self._send_cmd(service, subtype, data_hex)
            if isinstance(result, dict) and result.get("status") == "error":
                self._log("Command send failed", result.get("message", ""))
                return False
            self._log("Command sent", f"seq={result.get('seq', '?')}")
        except Exception as exc:
            self._log("Command error", str(exc))
            return False

        # If step has a verify block, check it
        if "verify" in step:
            return await self._wait_for_condition(step["verify"])

        return True

    async def _wait_for_condition(self, condition: dict) -> bool:
        """Poll telemetry until condition met or timeout."""
        param = condition.get("parameter", "")
        expected = condition.get("value")
        timeout_s = float(condition.get("timeout_s", 30))
        self._log("Wait for condition", f"{param} == {expected} (timeout {timeout_s}s)")

        deadline = time.monotonic() + timeout_s
        poll_interval = 1.0

        while time.monotonic() < deadline:
            if self.state == ProcedureState.ABORTED:
                return False

            current = self._get_tm(param)
            if current is not None and self._values_match(current, expected):
                self._log("Condition met", f"{param} = {current}")
                return True

            await asyncio.sleep(poll_interval)

        self._log("Condition timeout", f"{param} != {expected} after {timeout_s}s")
        self.step_results[self.current_step]["result"] = int(StepResult.TIMEOUT)
        return False

    @staticmethod
    def _values_match(actual: Any, expected: Any) -> bool:
        """Flexible comparison: handles bool, int, float, string."""
        if isinstance(expected, bool):
            if isinstance(actual, (int, float)):
                return bool(actual) == expected
            return actual == expected
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            return abs(float(actual) - float(expected)) < 0.01
        return str(actual) == str(expected)

    @staticmethod
    def _build_data_hex(step: dict) -> str:
        """Build data_hex from step fields (func_id, sid, store_id, etc.)."""
        parts = bytearray()
        if "func_id" in step:
            fid = step["func_id"]
            if isinstance(fid, str):
                fid = int(fid, 16) if fid.startswith("0x") else int(fid)
            parts.append(fid & 0xFF)
            if "params" in step:
                for p in step["params"]:
                    parts.append(int(p) & 0xFF)
        elif "sid" in step:
            sid = int(step["sid"])
            parts.extend(struct.pack(">H", sid))
        elif "store_id" in step:
            sid = int(step["store_id"])
            parts.extend(struct.pack(">H", sid))
        elif "address" in step:
            addr = int(step["address"])
            parts.extend(struct.pack(">I", addr))
            if "data" in step:
                parts.extend(bytes.fromhex(step["data"]))
        elif "param_id" in step:
            pid = int(step["param_id"])
            parts.extend(struct.pack(">H", pid))
            if "value" in step:
                parts.extend(struct.pack(">f", float(step["value"])))
        return parts.hex()

    def _log(self, action: str, detail: str = "") -> None:
        entry = {
            "timestamp": time.time(),
            "step": self.current_step,
            "action": action,
            "detail": detail,
        }
        self._execution_log.append(entry)
        logger.info("ProcRunner [step %d] %s: %s", self.current_step, action, detail)


def _step_desc(step: dict) -> str:
    """Generate a human-readable description for a step."""
    if "wait_s" in step:
        return f"Wait {step['wait_s']}s"
    if "wait_for" in step:
        wf = step["wait_for"]
        return f"Wait for {wf.get('parameter', '?')} == {wf.get('value', '?')}"
    if "service" in step:
        return step.get("description", f"S{step['service']}.{step['subtype']}")
    return str(step)

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

from smo_common.procedures import describe_step
from smo_common.procedures.schema import MAX_LOOP_ITERATIONS, canonical_step

logger = logging.getLogger(__name__)

#: Step types this runner knows how to execute. Anything else FAILS CLOSED —
#: see `_execute_step`. Keep in sync with smo_common.procedures.schema.
KNOWN_STEP_TYPES = (
    "command", "wait", "tlm_check", "go_nogo", "note", "label", "branch",
)


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
    time_fn : callable() -> float
        **Sim time** as a Unix epoch float (``MCSServer.sim_now_ts``). Every
        execution-log line is stamped with it, so a procedure's timeline lines
        up with the telemetry, alarms and archive rows it produced. Defaults
        to the wall clock only so a bare ``ProcedureRunner()`` in a test still
        works; the MCS always passes its sim clock.

    Note on step timeouts: ``wait``/``tlm_check``/``branch`` deadlines use
    ``time.monotonic()`` deliberately. They bound how long the runner blocks a
    real operator at a real console — a real-time concern, like a network I/O
    timeout — and must not become unbounded when the simulator is paused.
    """

    def __init__(
        self,
        send_command_fn: Callable[..., Coroutine],
        get_telemetry_fn: Callable[[str], Any],
        time_fn: Callable[[], float] | None = None,
    ):
        self._send_cmd = send_command_fn
        self._get_tm = get_telemetry_fn
        self._time_fn = time_fn or time.time

        self.state = ProcedureState.IDLE
        self.procedure_name: str = ""
        self.procedure_ref: str = ""
        self.scheduled_start_time: str | None = None
        self.steps: list[dict] = []
        self.current_step: int = -1
        self.step_results: list[dict] = []
        self.step_by_step: bool = False
        #: label name -> step index (first declaration wins), built at load.
        self._label_index: dict[str, int] = {}
        #: (branch step index, target label) -> times that arm has been taken.
        #: Bounds backward jumps (`max_taken`), so a loop cannot run forever.
        self._jump_counts: dict[tuple[int, str], int] = {}
        #: Set by a branching step to redirect the run loop to another index.
        self._pending_jump: int | None = None
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
        start_time: str | None = None,
    ) -> dict:
        """Load a procedure/command sequence for execution.

        ``start_time`` (ISO 8601, optional) is the planner-scheduled start of
        the activity. The ground-side runner still executes on operator
        start, but the scheduled time is preserved and surfaced in status()
        so it is no longer discarded at the planner→MCS interface (defect
        #44). Time-correct on-board execution uses the S11 upload path
        (MCS /api/schedule/s11-upload) instead.
        """
        if self.state in (ProcedureState.RUNNING, ProcedureState.PAUSED):
            return {"error": "A procedure is currently active. Abort it first."}

        self.procedure_name = name
        self.procedure_ref = procedure_ref
        self.scheduled_start_time = start_time
        # Normalise every accepted dialect into the ONE canonical step schema
        # (smo_common.procedures, defect #83) before execution, so the runner
        # has a single shape to reason about and the operator sees a single
        # rendering. The untagged and tagged branches in _execute_step remain
        # as a safety net for anything constructed directly in a test.
        self.steps = _prepare_steps(steps)
        self.current_step = -1
        self.step_by_step = step_by_step
        self.step_results = [
            {"index": i, "description": _step_desc(s), "result": int(StepResult.PENDING)}
            for i, s in enumerate(self.steps)
        ]
        self.state = ProcedureState.LOADED
        self._label_index = _build_label_index(self.steps)
        self._jump_counts = {}
        self._pending_jump = None
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
            "scheduled_start_time": self.scheduled_start_time,
            "total_steps": len(self.steps),
            "current_step": self.current_step,
            "step_by_step": self.step_by_step,
            "step_results": self.step_results,
            "log": self._execution_log[-50:],
        }

    # ── Internal execution loop ──────────────────────────────────────

    async def _run_loop(self) -> None:
        """Main execution loop.

        Steps are addressed by a program counter rather than a ``for`` range,
        because ``branch`` (and a ``go_nogo`` carrying targets) can redirect
        execution to a labelled step. Two independent guards keep that finite:
        the per-arm ``max_taken`` bound enforced in ``_resolve_jump``, and the
        whole-procedure step budget below.
        """
        try:
            idx = 0
            executed = 0
            # Whole-procedure guard: even with per-arm bounds, a pathological
            # nest of jumps must not spin forever. Every legal bounded run is
            # comfortably inside this.
            budget = 64 + len(self.steps) * (MAX_LOOP_ITERATIONS + 2)
            while 0 <= idx < len(self.steps):
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
                if self.step_by_step and executed > 0:
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
                    idx += 1
                    continue

                executed += 1
                if executed > budget:
                    self.step_results[idx]["result"] = int(StepResult.FAILED)
                    self.state = ProcedureState.FAILED
                    self._log(
                        "Procedure failed",
                        f"step budget of {budget} exhausted at step {idx} — "
                        "the branch structure is looping; failing closed",
                    )
                    return

                self._pending_jump = None
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

                jump = self._pending_jump
                self._pending_jump = None
                idx = idx + 1 if jump is None else jump

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
        # Defect #14: the Procedure Builder emits `type`-tagged steps
        # ('wait'/'tlm_check'/'go_nogo'/'command'). Translate them to the
        # runner's native handling so builder-authored procedures actually run
        # (previously every builder step hit the "unknown" branch and was
        # silently counted as PASSED).
        step_type = step.get("type")
        if step_type == "wait":
            wait_s = float(step.get("seconds", step.get("wait_s", 0)))
            self._log("Wait", f"{wait_s}s")
            await asyncio.sleep(wait_s)
            return True
        if step_type == "go_nogo":
            # Manual go/no-go gate. In an automated run we log and proceed; in
            # step-by-step mode the runner already pauses between steps so the
            # operator makes the call.
            self._log("GO/NO-GO", step.get("label", "operator decision"))
            # A gate carrying targets is a real fork (schema: on_go/on_nogo are
            # label NAMES). An unattended run takes the GO arm — that is what
            # "log and proceed" already meant — but it must JUMP there, because
            # falling through would execute the NO-GO arm that follows it.
            if step.get("on_go") or step.get("on_nogo"):
                return self._resolve_jump(idx, step.get("on_go"), step, "GO")
            return True
        if step_type == "label":
            # Non-executable jump target.
            self._log("Label", str(step.get("name") or ""))
            return True
        if step_type == "note":
            # Non-executable operator instruction: sends nothing, checks
            # nothing. It is inert BY DESIGN, not by failure to understand it.
            self._log("Note", str(step.get("text") or step.get("description") or ""))
            return True
        if step_type == "branch":
            return await self._execute_branch(idx, step)
        if step_type == "tlm_check":
            return await self._wait_for_condition({
                "parameter": step.get("parameter"),
                "value": step.get("value"),
                "operator": step.get("condition", "=="),
                "timeout_s": step.get("timeout_s", 30),
            })
        if step_type == "command":
            return await self._execute_command(idx, step)

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

        # Defect #14 / #9 (2026-08b MCS_REQUESTS §9): fail closed on an
        # unrecognised step rather than reporting a false PASS. A step the
        # runner cannot execute is a step whose effect on the spacecraft is
        # unknown; a procedure containing one must stop, not look successful.
        self._log(
            "Unknown step type — failing closed",
            f"type={step_type!r} (known: {', '.join(KNOWN_STEP_TYPES)}) {step}",
        )
        return False

    # ── Branching ────────────────────────────────────────────────────

    async def _execute_branch(self, idx: int, step: dict) -> bool:
        """Evaluate a ``branch`` predicate and jump to one of its two labels.

        Semantics (smo_common.procedures.schema, 2026-08b ruling R4):

        * the predicate is ``parameter <condition> value`` read from telemetry,
          polled for up to ``timeout_s`` — the card's
          ``**BRANCH:** `tcs.htr_battery == 0` -> GO `STEP-5` | NO-GO `STEP-3`
          (timeout 70 s)`` means "wait up to 70 s for the heater to report OFF";
        * predicate true within the window  -> jump to ``on_true``  (the GO arm);
        * still false when the window closes -> jump to ``on_false`` (NO-GO);
        * an empty target on the chosen arm means "fall through to the next
          step" — it is not a failure;
        * a target naming a label the procedure does not declare, an
          undecidable predicate (no parameter), or a branch with no targets at
          all is a FAILURE: the procedure stops rather than guessing an arm.

        A branch never fails just because its predicate is false — that is the
        NO-GO arm doing its job, not an error.
        """
        parameter = str(step.get("parameter") or "").strip()
        on_true = str(step.get("on_true") or "").strip()
        on_false = str(step.get("on_false") or "").strip()

        if not parameter:
            self._log("Branch undecidable — failing closed",
                      f"no parameter in {step}")
            return False
        if not on_true and not on_false:
            self._log("Branch has no targets — failing closed", str(step))
            return False

        timeout_s = float(step.get("timeout_s", 30) or 30)
        expected = step.get("value")
        operator = step.get("condition", "==")
        self._log("Branch", f"{parameter} {operator} {expected} "
                            f"(timeout {timeout_s:g}s) — GO -> "
                            f"{on_true or 'next'}, NO-GO -> {on_false or 'next'}")

        taken = await self._poll_predicate(parameter, operator, expected, timeout_s)
        arm, target = ("GO", on_true) if taken else ("NO-GO", on_false)
        self._log(f"Branch {arm}", f"{parameter} {operator} {expected} is "
                                   f"{'true' if taken else 'false'} -> "
                                   f"{target or 'next step'}")
        return self._resolve_jump(idx, target, step, arm)

    async def _poll_predicate(
        self, parameter: str, operator: str, expected: Any, timeout_s: float
    ) -> bool:
        """True as soon as the predicate holds; False when the window closes.

        Unlike ``_wait_for_condition`` this never marks the step TIMEOUT: for a
        branch, "false" is an answer rather than a failure.
        """
        deadline = time.monotonic() + timeout_s
        poll_interval = min(1.0, max(0.02, timeout_s / 5.0))
        while True:
            if self.state == ProcedureState.ABORTED:
                return False
            current = self._get_tm(parameter)
            if current is not None and self._compare(current, operator, expected):
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(poll_interval)

    def _resolve_jump(
        self, idx: int, target: str | None, step: dict, arm: str = ""
    ) -> bool:
        """Set the pending jump for ``target``. False = fail closed.

        Enforces the schema's bounded-loop rule: a jump to a label at or before
        the jumping step is a LOOP, and is only allowed with a ``max_taken``
        bound of 1..MAX_LOOP_ITERATIONS. Once that arm has been taken
        ``max_taken`` times the runner stops taking it and falls through to the
        next step, exactly as the schema requires.
        """
        target = str(target or "").strip()
        if not target:
            return True                      # fall through to the next step

        if target not in self._label_index:
            self._log(
                "Branch target missing — failing closed",
                f"{arm or 'branch'} arm jumps to label {target!r}, which this "
                f"procedure does not declare "
                f"(declared: {sorted(self._label_index) or 'none'})",
            )
            return False

        dest = self._label_index[target]
        if dest <= idx:                      # backward jump == loop
            bound = step.get("max_taken")
            try:
                bound = int(bound)
            except (TypeError, ValueError):
                bound = None
            if bound is None or bound < 1:
                self._log(
                    "Unbounded backward jump — failing closed",
                    f"{arm or 'branch'} arm jumps back to {target!r} "
                    f"(step {dest}) with no max_taken bound",
                )
                return False
            bound = min(bound, MAX_LOOP_ITERATIONS)
            key = (idx, target)
            already = self._jump_counts.get(key, 0)
            if already >= bound:
                self._log(
                    "Loop bound reached",
                    f"{arm or 'branch'} arm to {target!r} already taken "
                    f"{already}x (max {bound}) — falling through to step {idx + 1}",
                )
                return True                  # no jump; continue in sequence
            self._jump_counts[key] = already + 1

        self._pending_jump = dest
        self._log("Jump", f"{arm or 'branch'} -> {target} (step {dest})")
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
        operator = condition.get("operator", "==")
        timeout_s = float(condition.get("timeout_s", 30))
        self._log("Wait for condition", f"{param} {operator} {expected} (timeout {timeout_s}s)")

        deadline = time.monotonic() + timeout_s
        poll_interval = 1.0

        while time.monotonic() < deadline:
            if self.state == ProcedureState.ABORTED:
                return False

            current = self._get_tm(param)
            if current is not None and self._compare(current, operator, expected):
                self._log("Condition met", f"{param} = {current}")
                return True

            await asyncio.sleep(poll_interval)

        self._log("Condition timeout", f"{param} {operator} {expected} after {timeout_s}s")
        self.step_results[self.current_step]["result"] = int(StepResult.TIMEOUT)
        return False

    @classmethod
    def _compare(cls, actual: Any, operator: str, expected: Any) -> bool:
        """Compare telemetry against an expected value with an operator.

        Defect #14: the Procedure Builder's tlm_check steps carry a comparison
        operator (>, <, >=, <=, !=, ==); previously only equality was supported.
        """
        op = (operator or "==").strip()
        if op in ("==", "=", "eq"):
            return cls._values_match(actual, expected)
        if op in ("!=", "ne"):
            return not cls._values_match(actual, expected)
        try:
            a, e = float(actual), float(expected)
        except (TypeError, ValueError):
            return False
        if op in (">", "gt"):
            return a > e
        if op in ("<", "lt"):
            return a < e
        if op in (">=", "ge"):
            return a >= e
        if op in ("<=", "le"):
            return a <= e
        return cls._values_match(actual, expected)

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
        """Build data_hex from step fields (func_id, sid, store_id, etc.).

        Phase E (brief 04 WP-1/WP-6): a step may carry named ``args`` in
        ENGINEERING units — they are packed by the shared catalogue encoder
        (``smo_common.commanding.encode``), the same code path the OBCP
        loader, the planner expander and the S11 embedder use, so the five
        ground-side encoders cannot diverge. The old hard-coded
        ``parameters{target_lat, target_lon} -> struct.pack('>ff', ...)``
        special case is gone with the lat/lon capture encoding itself.
        """
        # ── Named engineering args -> shared catalogue encoder ──
        if isinstance(step.get("args"), dict) and \
                ("service" in step or "command" in step):
            try:
                from smo_common.commanding import encode
                if step.get("command"):
                    return encode(str(step["command"]), step["args"]).hex()
                svc = int(step.get("service", 8))
                sub = int(step.get("subtype", 1))
                fid = step.get("func_id")
                if isinstance(fid, str):
                    fid = int(fid, 16) if fid.startswith("0x") else int(fid)
                return encode((svc, sub, fid), step["args"]).hex()
            except Exception as exc:
                logger.warning("catalogue encode failed for step %s: %s",
                               step.get("description", step), exc)
                # fall through to the legacy positional packing
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
            # Sim time — see `time_fn` on __init__.
            "timestamp": self._time_fn(),
            "step": self.current_step,
            "action": action,
            "detail": detail,
        }
        self._execution_log.append(entry)
        # Cap log to prevent unbounded growth during long procedures
        if len(self._execution_log) > 500:
            self._execution_log = self._execution_log[-500:]
        logger.info("ProcRunner [step %d] %s: %s", self.current_step, action, detail)


def _prepare_steps(steps: list[dict] | None) -> list[dict]:
    """Canonicalise a step list WITHOUT laundering unrecognised steps.

    ``canonical_step`` turns anything it does not recognise into a ``note`` so
    it stays visible in the operator's step list. A note is inert, so if the
    runner simply executed the canonical form, a step of an unknown type would
    once again be reported PASSED — the exact defect this guards against. So
    the unrecognised ones are re-tagged with their original (unknown) type and
    hit the fail-closed branch of ``_execute_step``.
    """
    prepared: list[dict] = []
    for raw in steps or []:
        step = canonical_step(raw)
        tag = ""
        authored_note = False
        if isinstance(raw, dict):
            tag = str(raw.get("type", "") or "").strip().lower()
            authored_note = tag == "note" or "text" in raw or "note" in raw
        if (tag and tag not in KNOWN_STEP_TYPES) or (
            step.get("type") == "note" and not authored_note
        ):
            step = dict(step)
            step["type"] = tag or "unknown"
            step["description"] = f"Unrecognised step: {raw!r}"
        prepared.append(step)
    return prepared


def _build_label_index(steps: list[dict]) -> dict[str, int]:
    """Map label name -> step index. First declaration wins.

    A duplicated label is a defect the schema's validator reports; here it is
    resolved deterministically (and logged) rather than silently picking the
    last one.
    """
    index: dict[str, int] = {}
    for i, step in enumerate(steps):
        if step.get("type") != "label":
            continue
        name = str(step.get("name") or "").strip()
        if not name:
            logger.warning("procedure step %d is a label with no name", i)
            continue
        if name in index:
            logger.warning(
                "procedure declares label %r twice (steps %d and %d); "
                "jumps resolve to the first", name, index[name], i)
            continue
        index[name] = i
    return index


def _step_desc(step: dict) -> str:
    """Human-readable description for a step, in any dialect.

    Delegates to the one canonical renderer (defect #83). It used to fall
    through to ``str(step)`` for every ``type``-tagged step, so the MCS step
    list showed operators raw Python dicts for builder-authored waits,
    telemetry checks and GO/NO-GO gates.
    """
    if isinstance(step, dict):
        kind = str(step.get("type", "") or "").strip().lower()
        if kind and kind not in KNOWN_STEP_TYPES and step.get("description"):
            # Re-tagged unrecognised step (see _prepare_steps): say so plainly
            # rather than rendering a raw dict.
            return str(step["description"])
    return describe_step(step)

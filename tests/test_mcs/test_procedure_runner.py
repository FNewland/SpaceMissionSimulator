"""Tests for Phase 5 — ProcedureRunner (smo-mcs).

Covers:
  - load procedure (IDLE -> LOADED)
  - load while running returns error
  - start procedure (LOADED -> RUNNING)
  - start from wrong state returns error
  - pause / resume cycle
  - abort from running and paused states
  - step_advance in step-by-step mode
  - skip_step
  - override_command while paused / when not paused
  - status returns correct fields
  - _build_data_hex for func_id, sid, store_id, param_id
  - _values_match for bool, int, float, string
  - _step_desc for different step types
  - Command step execution (mock send_command_fn)
  - Wait step execution
  - Wait-for-condition step
  - Full procedure execution to completion
  - Procedure failing on command error
"""
import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest
from smo_mcs.procedure_runner import ProcedureRunner, ProcedureState, StepResult, _step_desc


# ── Helpers ──────────────────────────────────────────────────────────

def _make_runner(
    send_result=None,
    telemetry_values=None,
):
    """Create a ProcedureRunner with mock functions."""
    if send_result is None:
        send_result = {"status": "sent", "seq": 1}

    send_fn = AsyncMock(return_value=send_result)

    tm_store = telemetry_values or {}
    get_tm_fn = MagicMock(side_effect=lambda path: tm_store.get(path))

    runner = ProcedureRunner(send_fn, get_tm_fn)
    return runner, send_fn, get_tm_fn


SIMPLE_STEPS = [
    {"service": 8, "subtype": 1, "func_id": "0x10", "description": "Enable payload"},
    {"wait_s": 0.05},
    {"service": 8, "subtype": 1, "func_id": "0x11", "description": "Start imaging"},
]


# ── Load ─────────────────────────────────────────────────────────────

class TestLoad:
    """Test loading a procedure."""

    def test_load_from_idle(self):
        runner, _, _ = _make_runner()
        result = runner.load("TestProc", SIMPLE_STEPS, procedure_ref="PROC-001")
        assert result["state"] == "LOADED"
        assert runner.state == ProcedureState.LOADED
        assert runner.procedure_name == "TestProc"
        assert runner.procedure_ref == "PROC-001"
        assert len(runner.steps) == 3
        assert runner.current_step == -1

    def test_load_creates_step_results(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        assert len(runner.step_results) == 3
        for sr in runner.step_results:
            assert sr["result"] == int(StepResult.PENDING)

    def test_load_step_by_step_mode(self):
        runner, _, _ = _make_runner()
        result = runner.load("TestProc", SIMPLE_STEPS, step_by_step=True)
        assert result["step_by_step"] is True
        assert runner.step_by_step is True

    @pytest.mark.asyncio
    async def test_load_while_running_returns_error(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        await runner.start()
        result = runner.load("AnotherProc", SIMPLE_STEPS)
        assert "error" in result
        assert "active" in result["error"].lower() or "currently" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_load_while_paused_returns_error(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        await runner.start()
        await runner.pause()
        result = runner.load("AnotherProc", SIMPLE_STEPS)
        assert "error" in result

    def test_load_from_completed_state(self):
        """After completion, should be able to load a new procedure."""
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        runner.state = ProcedureState.COMPLETED
        result = runner.load("NewProc", SIMPLE_STEPS)
        assert result["state"] == "LOADED"


# ── Start ────────────────────────────────────────────────────────────

class TestStart:
    """Test starting a procedure."""

    @pytest.mark.asyncio
    async def test_start_from_loaded(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        result = await runner.start()
        assert result["state"] == "RUNNING"
        assert runner.state == ProcedureState.RUNNING
        # Allow the task to proceed
        await asyncio.sleep(0)
        # Clean up
        await runner.abort()

    @pytest.mark.asyncio
    async def test_start_from_idle_returns_error(self):
        runner, _, _ = _make_runner()
        result = await runner.start()
        assert "error" in result
        assert "IDLE" in result["error"]

    @pytest.mark.asyncio
    async def test_start_from_completed_returns_error(self):
        runner, _, _ = _make_runner()
        runner.state = ProcedureState.COMPLETED
        result = await runner.start()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        await runner.start()
        assert runner._task is not None
        await runner.abort()


# ── Pause / Resume ──────────────────────────────────────────────────

class TestPauseResume:
    """Test pause and resume cycle."""

    @pytest.mark.asyncio
    async def test_pause_from_running(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        await runner.start()
        result = await runner.pause()
        assert result["state"] == "PAUSED"
        assert runner.state == ProcedureState.PAUSED
        await runner.abort()

    @pytest.mark.asyncio
    async def test_pause_from_non_running_returns_error(self):
        runner, _, _ = _make_runner()
        result = await runner.pause()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_resume_from_paused(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        await runner.start()
        await runner.pause()
        result = await runner.resume()
        assert result["state"] == "RUNNING"
        assert runner.state == ProcedureState.RUNNING
        await runner.abort()

    @pytest.mark.asyncio
    async def test_resume_from_non_paused_returns_error(self):
        runner, _, _ = _make_runner()
        result = await runner.resume()
        assert "error" in result


# ── Abort ────────────────────────────────────────────────────────────

class TestAbort:
    """Test aborting a procedure."""

    @pytest.mark.asyncio
    async def test_abort_from_running(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        await runner.start()
        result = await runner.abort()
        assert result["state"] == "ABORTED"
        assert runner.state == ProcedureState.ABORTED

    @pytest.mark.asyncio
    async def test_abort_from_paused(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        await runner.start()
        await runner.pause()
        result = await runner.abort()
        assert result["state"] == "ABORTED"
        assert runner.state == ProcedureState.ABORTED

    @pytest.mark.asyncio
    async def test_abort_from_idle_returns_error(self):
        runner, _, _ = _make_runner()
        result = await runner.abort()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_abort_from_loaded_returns_error(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        result = await runner.abort()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_abort_cancels_task(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        await runner.start()
        task = runner._task
        await runner.abort()
        # Give task a moment to be cancelled
        await asyncio.sleep(0.05)
        assert task.done() or task.cancelled()


# ── Step Advance ─────────────────────────────────────────────────────

class TestStepAdvance:
    """Test step-by-step execution."""

    @pytest.mark.asyncio
    async def test_step_advance_from_paused(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS, step_by_step=True)
        await runner.start()
        # Let first step execute, then auto-pauses before step 2
        await asyncio.sleep(0.1)
        assert runner.state == ProcedureState.PAUSED
        result = await runner.step_advance()
        assert "error" not in result
        assert runner.state == ProcedureState.RUNNING
        await asyncio.sleep(0.01)
        await runner.abort()

    @pytest.mark.asyncio
    async def test_step_advance_from_idle_returns_error(self):
        runner, _, _ = _make_runner()
        result = await runner.step_advance()
        assert "error" in result


# ── Skip Step ────────────────────────────────────────────────────────

class TestSkipStep:
    """Test skipping steps."""

    @pytest.mark.asyncio
    async def test_skip_step_marks_skipped(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS, step_by_step=True)
        await runner.start()
        # Let first step execute, then auto-pauses before step 2
        await asyncio.sleep(0.1)
        # current_step should be 0 or 1 at this point
        step_idx = runner.current_step
        if step_idx >= 0:
            result = await runner.skip_step()
            assert "error" not in result
            assert runner.step_results[step_idx]["result"] == int(StepResult.SKIPPED)
        await runner.abort()

    @pytest.mark.asyncio
    async def test_skip_step_from_idle_returns_error(self):
        runner, _, _ = _make_runner()
        result = await runner.skip_step()
        assert "error" in result


# ── Override Command ─────────────────────────────────────────────────

class TestOverrideCommand:
    """Test manual command injection."""

    @pytest.mark.asyncio
    async def test_override_while_paused(self):
        runner, send_fn, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        await runner.start()
        await runner.pause()
        result = await runner.override_command(3, 25, "AABB")
        assert "override_result" in result
        assert result["override_result"]["status"] == "sent"
        send_fn.assert_awaited_with(3, 25, "AABB")
        await runner.abort()

    @pytest.mark.asyncio
    async def test_override_when_not_paused_returns_error(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS)
        await runner.start()
        result = await runner.override_command(3, 25, "AABB")
        assert "error" in result
        assert "paused" in result["error"].lower()
        await runner.abort()

    @pytest.mark.asyncio
    async def test_override_from_idle_returns_error(self):
        runner, _, _ = _make_runner()
        result = await runner.override_command(3, 25, "AABB")
        assert "error" in result


# ── Status ───────────────────────────────────────────────────────────

class TestStatus:
    """Test status reporting."""

    def test_status_returns_required_fields(self):
        runner, _, _ = _make_runner()
        s = runner.status()
        required = [
            "state", "state_code", "procedure_name", "procedure_ref",
            "total_steps", "current_step", "step_by_step", "step_results",
            "log",
        ]
        for key in required:
            assert key in s, f"Missing key: {key}"

    def test_status_after_load(self):
        runner, _, _ = _make_runner()
        runner.load("TestProc", SIMPLE_STEPS, procedure_ref="PROC-001")
        s = runner.status()
        assert s["state"] == "LOADED"
        assert s["state_code"] == int(ProcedureState.LOADED)
        assert s["procedure_name"] == "TestProc"
        assert s["procedure_ref"] == "PROC-001"
        assert s["total_steps"] == 3
        assert s["current_step"] == -1
        assert s["step_by_step"] is False

    def test_status_log_truncated_to_50(self):
        runner, _, _ = _make_runner()
        # Inject more than 50 log entries
        for i in range(60):
            runner._execution_log.append({"action": f"entry-{i}"})
        s = runner.status()
        assert len(s["log"]) == 50


# ── _build_data_hex ──────────────────────────────────────────────────

class TestBuildDataHex:
    """Test building hex data from step fields."""

    def test_func_id_hex_string(self):
        step = {"func_id": "0x42"}
        result = ProcedureRunner._build_data_hex(step)
        assert result == "42"

    def test_func_id_decimal_string(self):
        step = {"func_id": "66"}
        result = ProcedureRunner._build_data_hex(step)
        assert result == "42"  # 66 == 0x42

    def test_func_id_int(self):
        step = {"func_id": 0x42}
        result = ProcedureRunner._build_data_hex(step)
        assert result == "42"

    def test_func_id_with_params(self):
        step = {"func_id": "0x10", "params": [1, 2, 3]}
        result = ProcedureRunner._build_data_hex(step)
        assert result == "10010203"

    def test_sid(self):
        step = {"sid": 256}
        result = ProcedureRunner._build_data_hex(step)
        expected = struct.pack(">H", 256).hex()
        assert result == expected

    def test_store_id(self):
        step = {"store_id": 1}
        result = ProcedureRunner._build_data_hex(step)
        expected = struct.pack(">H", 1).hex()
        assert result == expected

    def test_param_id_with_value(self):
        step = {"param_id": 100, "value": 3.14}
        result = ProcedureRunner._build_data_hex(step)
        expected_pid = struct.pack(">H", 100).hex()
        expected_val = struct.pack(">f", 3.14).hex()
        assert result == expected_pid + expected_val

    def test_param_id_without_value(self):
        step = {"param_id": 100}
        result = ProcedureRunner._build_data_hex(step)
        expected = struct.pack(">H", 100).hex()
        assert result == expected

    def test_empty_step(self):
        step = {}
        result = ProcedureRunner._build_data_hex(step)
        assert result == ""

    def test_address_with_data(self):
        step = {"address": 0x1000, "data": "DEADBEEF"}
        result = ProcedureRunner._build_data_hex(step)
        expected_addr = struct.pack(">I", 0x1000).hex()
        assert result == expected_addr + "deadbeef"

    def test_address_without_data(self):
        step = {"address": 0x2000}
        result = ProcedureRunner._build_data_hex(step)
        expected = struct.pack(">I", 0x2000).hex()
        assert result == expected


# ── _values_match ────────────────────────────────────────────────────

class TestValuesMatch:
    """Test flexible comparison logic."""

    def test_bool_match(self):
        assert ProcedureRunner._values_match(True, True) is True
        assert ProcedureRunner._values_match(False, False) is True

    def test_bool_mismatch(self):
        assert ProcedureRunner._values_match(True, False) is False

    def test_bool_expected_with_int_actual(self):
        assert ProcedureRunner._values_match(1, True) is True
        assert ProcedureRunner._values_match(0, False) is True
        assert ProcedureRunner._values_match(0, True) is False

    def test_bool_expected_with_float_actual(self):
        assert ProcedureRunner._values_match(1.0, True) is True
        assert ProcedureRunner._values_match(0.0, False) is True

    def test_int_match(self):
        assert ProcedureRunner._values_match(42, 42) is True

    def test_int_mismatch(self):
        assert ProcedureRunner._values_match(42, 43) is False

    def test_float_match_within_tolerance(self):
        assert ProcedureRunner._values_match(3.14, 3.14) is True
        assert ProcedureRunner._values_match(3.145, 3.14) is True  # diff < 0.01

    def test_float_mismatch_beyond_tolerance(self):
        assert ProcedureRunner._values_match(3.14, 3.20) is False

    def test_int_float_cross_comparison(self):
        assert ProcedureRunner._values_match(42, 42.0) is True
        assert ProcedureRunner._values_match(42.0, 42) is True

    def test_string_match(self):
        assert ProcedureRunner._values_match("ON", "ON") is True

    def test_string_mismatch(self):
        assert ProcedureRunner._values_match("ON", "OFF") is False

    def test_string_fallback_for_mixed_types(self):
        assert ProcedureRunner._values_match("42", 42) is True


# ── _step_desc ───────────────────────────────────────────────────────

class TestStepDesc:
    """Test step description generation."""

    def test_wait_step(self):
        desc = _step_desc({"wait_s": 5})
        assert "Wait" in desc
        assert "5" in desc

    def test_wait_for_step(self):
        desc = _step_desc({"wait_for": {"parameter": "payload.mode", "value": 2}})
        assert "payload.mode" in desc
        assert "2" in desc

    def test_command_step_with_description(self):
        desc = _step_desc({"service": 8, "subtype": 1, "description": "Enable payload"})
        assert desc == "Enable payload"

    def test_command_step_without_description(self):
        desc = _step_desc({"service": 8, "subtype": 1})
        assert "S8.1" in desc

    def test_unknown_step(self):
        desc = _step_desc({"custom_field": "value"})
        assert isinstance(desc, str)


# ── Command Step Execution ──────────────────────────────────────────

class TestCommandStepExecution:
    """Test command step execution with mocked send_command_fn."""

    @pytest.mark.asyncio
    async def test_command_step_calls_send_fn(self):
        runner, send_fn, _ = _make_runner()
        steps = [{"service": 8, "subtype": 1, "func_id": "0x10", "description": "Test cmd"}]
        runner.load("CmdTest", steps)
        await runner.start()
        await asyncio.sleep(0.1)
        send_fn.assert_awaited()
        # The command was sent with the correct service/subtype
        call_args = send_fn.call_args
        assert call_args[0][0] == 8   # service
        assert call_args[0][1] == 1   # subtype

    @pytest.mark.asyncio
    async def test_command_step_uses_data_hex_from_step(self):
        runner, send_fn, _ = _make_runner()
        steps = [{"service": 3, "subtype": 25, "data_hex": "AABB"}]
        runner.load("CmdTest", steps)
        await runner.start()
        await asyncio.sleep(0.1)
        call_args = send_fn.call_args
        assert call_args[0][2] == "AABB"  # data_hex

    @pytest.mark.asyncio
    async def test_command_step_builds_data_hex_from_func_id(self):
        runner, send_fn, _ = _make_runner()
        steps = [{"service": 8, "subtype": 1, "func_id": "0x42"}]
        runner.load("CmdTest", steps)
        await runner.start()
        await asyncio.sleep(0.1)
        call_args = send_fn.call_args
        assert call_args[0][2] == "42"  # built from func_id


# ── Wait Step Execution ─────────────────────────────────────────────

class TestWaitStepExecution:
    """Test timed wait step execution."""

    @pytest.mark.asyncio
    async def test_wait_step_completes(self):
        runner, _, _ = _make_runner()
        steps = [{"wait_s": 0.05}]
        runner.load("WaitTest", steps)
        await runner.start()
        await asyncio.sleep(0.2)
        assert runner.state == ProcedureState.COMPLETED
        assert runner.step_results[0]["result"] == int(StepResult.PASSED)


# ── Wait-for-Condition Step ─────────────────────────────────────────

class TestWaitForConditionStep:
    """Test wait-for-condition steps with telemetry polling."""

    @pytest.mark.asyncio
    async def test_condition_met_immediately(self):
        runner, _, get_tm = _make_runner(
            telemetry_values={"payload.mode": 2}
        )
        steps = [{
            "wait_for": {
                "parameter": "payload.mode",
                "value": 2,
                "timeout_s": 2,
            }
        }]
        runner.load("CondTest", steps)
        await runner.start()
        await asyncio.sleep(0.3)
        assert runner.state == ProcedureState.COMPLETED
        assert runner.step_results[0]["result"] == int(StepResult.PASSED)
        get_tm.assert_called_with("payload.mode")

    @pytest.mark.asyncio
    async def test_condition_timeout(self):
        runner, _, _ = _make_runner(
            telemetry_values={"payload.mode": 0}  # Will never match 2
        )
        steps = [{
            "wait_for": {
                "parameter": "payload.mode",
                "value": 2,
                "timeout_s": 1.0,
            }
        }]
        runner.load("CondTest", steps)
        await runner.start()
        # Wait longer than timeout_s + poll_interval (1.0s) to ensure
        # the condition loop exits and the procedure transitions to FAILED
        await asyncio.sleep(2.5)
        assert runner.state == ProcedureState.FAILED
        # Note: _wait_for_condition sets TIMEOUT, but _run_loop overwrites
        # non-SKIPPED failed results with FAILED. The procedure still fails.
        assert runner.step_results[0]["result"] == int(StepResult.FAILED)


# ── Full Procedure Execution ────────────────────────────────────────

class TestFullExecution:
    """Test executing a complete procedure to completion."""

    @pytest.mark.asyncio
    async def test_full_procedure_completes(self):
        runner, send_fn, _ = _make_runner()
        steps = [
            {"service": 8, "subtype": 1, "func_id": "0x10", "description": "Step 1"},
            {"wait_s": 0.05},
            {"service": 8, "subtype": 1, "func_id": "0x11", "description": "Step 2"},
        ]
        runner.load("FullTest", steps)
        await runner.start()
        # Wait for the procedure to complete
        await asyncio.sleep(0.5)
        assert runner.state == ProcedureState.COMPLETED
        # All steps should have passed
        for sr in runner.step_results:
            assert sr["result"] == int(StepResult.PASSED)
        # Commands should have been sent twice (steps 0 and 2)
        assert send_fn.await_count == 2

    @pytest.mark.asyncio
    async def test_full_procedure_with_verify(self):
        runner, send_fn, get_tm = _make_runner(
            telemetry_values={"payload.mode": 2}
        )
        steps = [
            {
                "service": 8,
                "subtype": 1,
                "func_id": "0x10",
                "description": "Enable payload",
                "verify": {
                    "parameter": "payload.mode",
                    "value": 2,
                    "timeout_s": 2,
                },
            },
        ]
        runner.load("VerifyTest", steps)
        await runner.start()
        await asyncio.sleep(0.5)
        assert runner.state == ProcedureState.COMPLETED
        assert runner.step_results[0]["result"] == int(StepResult.PASSED)

    @pytest.mark.asyncio
    async def test_execution_log_populated(self):
        runner, _, _ = _make_runner()
        steps = [{"service": 8, "subtype": 1, "func_id": "0x10"}]
        runner.load("LogTest", steps)
        await runner.start()
        await asyncio.sleep(0.3)
        log = runner._execution_log
        assert len(log) > 0
        # Log entries should have required fields
        for entry in log:
            assert "timestamp" in entry
            assert "action" in entry


# ── Procedure Failing ────────────────────────────────────────────────

class TestProcedureFailure:
    """Test procedure failure scenarios."""

    @pytest.mark.asyncio
    async def test_command_error_fails_procedure(self):
        runner, send_fn, _ = _make_runner(
            send_result={"status": "error", "message": "TC rejected"}
        )
        steps = [{"service": 8, "subtype": 1, "func_id": "0x10"}]
        runner.load("FailTest", steps)
        await runner.start()
        await asyncio.sleep(0.3)
        assert runner.state == ProcedureState.FAILED
        assert runner.step_results[0]["result"] == int(StepResult.FAILED)

    @pytest.mark.asyncio
    async def test_command_exception_fails_procedure(self):
        runner, send_fn, _ = _make_runner()
        send_fn.side_effect = ConnectionError("Link down")
        steps = [{"service": 8, "subtype": 1, "func_id": "0x10"}]
        runner.load("ExcTest", steps)
        await runner.start()
        await asyncio.sleep(0.3)
        assert runner.state == ProcedureState.FAILED

    @pytest.mark.asyncio
    async def test_failure_stops_subsequent_steps(self):
        runner, send_fn, _ = _make_runner(
            send_result={"status": "error", "message": "TC rejected"}
        )
        steps = [
            {"service": 8, "subtype": 1, "func_id": "0x10", "description": "Will fail"},
            {"service": 8, "subtype": 1, "func_id": "0x11", "description": "Never runs"},
        ]
        runner.load("StopTest", steps)
        await runner.start()
        await asyncio.sleep(0.3)
        assert runner.state == ProcedureState.FAILED
        assert runner.step_results[0]["result"] == int(StepResult.FAILED)
        assert runner.step_results[1]["result"] == int(StepResult.PENDING)

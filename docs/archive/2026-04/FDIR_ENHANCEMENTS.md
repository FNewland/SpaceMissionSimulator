# EOSAT-1 FDIR System Enhancements

## Overview

This document describes the comprehensive enhancements made to the FDIR (Fault Detection, Isolation & Recovery) system in the EOSAT-1 spacecraft simulator. The improvements add cross-subsystem fault cascading, priority-based load shedding, recovery state tracking, and automated procedure execution.

## 1. Cross-Subsystem Fault Cascading

### FaultPropagator Class

**Location:** `smo_simulator/fdir.py`

The `FaultPropagator` class models how faults cascade from one subsystem to others:

```python
class FaultPropagator:
    - register_rule(rule: FaultPropagationRule)
    - detect_fault(fault_id: str, current_time: float) -> list[dict]
    - clear_fault(fault_id: str)
    - active_faults() -> list[str]
    - get_recovery_state(fault_id: str) -> Optional[RecoveryState]
```

### Fault Propagation Rules

Rules are defined in `configs/eosat1/fdir/fault_propagation.yaml` and model these cascading scenarios:

#### EPS Failures
- **eps_undervoltage** (bus voltage < 26V)
  - Cascades to: AOCS enters safe mode (5s delay), Payload powers off (2s), TCS heaters standby (1s)
  - Rationale: Reduced bus current critical, shed loads in priority order

- **eps_overcurrent_aocs** (overcurrent trip on AOCS bus)
  - Cascades to: AOCS safe mode (0.5s), Payload off (3s)
  - Rationale: Bus power lost, attitude control compromised

- **eps_low_soc** (battery SoC < 10%)
  - Cascades to: Payload off (1s), AOCS safe (2s), TTC reduce TX (3s)
  - Rationale: Survival mode, only essential systems remain

#### AOCS Failures
- **aocs_wheel_failure_dual** (2+ wheels disabled)
  - Cascades to: AOCS safe mode (1s), Payload off (2s), AOCS power reduced (3s)
  - Rationale: Cannot maintain 3-axis control with <2 wheels

- **aocs_safe_mode_entry** (AOCS enters safe mode)
  - Cascades to: Payload enters standby (5s)
  - Rationale: Pointing accuracy degraded, protect payload

#### TCS Failures
- **tcs_battery_overtemp** (battery temp > 45°C)
  - Cascades to: EPS reduce charge rate (2s), Heater emergency off (0.5s), Payload duty cycle reduction (5s)
  - Rationale: Prevent thermal runaway

#### OBDH Failures
- **obdh_can_bus_failure** (CAN bus communication loss)
  - Cascades to: AOCS safe (2s), Payload off (1s), EPS safe mode (3s), TTC standby (1s)
  - Rationale: Cannot command subsystems, enter safe states autonomously

#### TTC Failures
- **ttc_pa_overheat** (PA temperature > 80°C)
  - Cascades to: Reduce TX power (1s), Reduce data rate (5s)
  - Rationale: Thermal protection of power amplifier

### Integration

Cascading effects are checked in:
```python
engine._tick_fdir_advanced(dt_sim)
```

The engine loads propagation rules from YAML during initialization:
```python
engine._load_fault_propagation_config()
```

## 2. Priority-Based Load Shedding Strategy

### LoadSheddingManager Class

**Location:** `smo_simulator/fdir.py`

Manages four-stage load shedding tied to battery state of charge:

```python
class LoadSheddingManager:
    - register_stage_config(stage: int, config: dict)
    - get_required_stage(soc: float) -> int
    - update_stage(soc: float, current_time: float) -> Optional[int]
    - execute_stage(stage: int)
    - current_stage() -> int
```

### Load Shedding Stages

Defined in `configs/eosat1/fdir/fault_propagation.yaml`:

#### Stage 0: Normal Operations (SoC >= 30%)
- All loads operational
- No subsystem commands

#### Stage 1: Power Conservation (SoC 20-30%)
- Non-essential heaters reduced to 50% duty
- Payload enters standby mode (reduced power)
- Non-essential auxiliary loads off
- Triggered at SoC < 30%

#### Stage 2: Payload Offline (SoC 10-20%)
- Payload completely powered off
- AOCS enters safe mode (attitude hold, minimal momentum management)
- All non-essential heaters off
- Non-essential auxiliary power off
- Triggered at SoC < 20%

#### Stage 3: Survival Mode (SoC < 10%)
- **Only essential systems remain:**
  - OBC (Onboard Computer): Full operational capability
  - TTC (Telemetry/Telecommand): Minimal power (recv only, reduced TX)
  - Battery heater: 10% duty cycle (essential thermal management)
  - AOCS: Safe mode with minimal momentum management
- All payload/imaging systems off
- All non-essential loads off
- Triggered at SoC < 10%

### Automatic Triggering

Load shedding stages are checked every tick in `_tick_fdir_advanced()`:

```python
def _tick_fdir_advanced(self, dt: float) -> None:
    # Get battery SoC from EPS subsystem
    soc = eps._state.soc

    # Check if stage transition needed
    new_stage = self._load_shedding.update_stage(soc, self._sim_elapsed_fdir)
    if new_stage is not None:
        # Execute all commands for new stage
        self._load_shedding.execute_stage(new_stage)
        # Emit LOAD_SHED_ACTIVATED event (0x0F05)
```

### Callbacks

Load shedding invokes subsystem commands via registered callbacks:
- `payload_mode`: Sets payload to off/standby
- `aocs_mode`: Sets AOCS to safe mode
- `tcs_heater_duty`: Reduces heater duty cycle
- `ttc_power_level`: Reduces TX power level

## 3. Recovery State Machine

### RecoveryState Dataclass

**Location:** `smo_simulator/fdir.py`

Tracks recovery attempts for each fault:

```python
@dataclass
class RecoveryState:
    fault_id: str
    response_taken: str
    timestamp: float
    recovery_attempts: int = 0
    max_attempts: int = 3
    last_attempt_time: float = 0.0
    state: str = "IDLE"  # IDLE, RUNNING, COMPLETE, FAILED
```

### Recovery Escalation

From `fault_propagation.yaml`:

- **Level 1 Response:** Immediate action (safe mode, power reduction, etc.)
  - If unsuccessful after 30 seconds, escalate to Level 2

- **Level 2 Response:** More aggressive action (reset subsystems, switch modes)
  - If unsuccessful after 60 seconds, escalate to Level 3

- **Level 3 Response:** Emergency action (emergency power down, full safe mode)
  - After Level 3, system is in stable state or unrecoverable

### Automatic Escalation

Recovery state is tracked by `FaultPropagator`:

```python
propagator.increment_recovery_attempt(fault_id, current_time)
# Returns True if more attempts available, False if max attempts reached
```

When max attempts exceeded, FDIR can escalate to next level (implemented via procedure chain).

## 4. Procedure Execution System

### ProcedureExecutor Class

**Location:** `smo_simulator/fdir.py`

Manages procedure loading and sequential execution:

```python
class ProcedureExecutor:
    - register_procedure(proc_id: str, config: dict)
    - register_command_callback(cmd_name: str, callback: Callable)
    - start_procedure(proc_id: str, current_time: float) -> str
    - tick_procedures(current_time: float)
```

### Procedure Format

Procedures are YAML files with sequential steps:

```yaml
procedure_id: "safe_mode_entry"
steps:
  - step_id: 1
    name: "Payload Safe"
    delay_s: 0.0
    command: "s8_command"
    params:
      func_id: 20
      data: AAA=  # Binary data

  - step_id: 2
    name: "AOCS Safe Mode"
    delay_s: 1.0
    command: "s8_command"
    params:
      func_id: 0
      data: Ag==
```

Each step:
- Executes after `delay_s` seconds from procedure start
- Invokes a registered command callback
- Can pass arbitrary parameters to the callback

### Available Procedures

**Location:** `configs/eosat1/fdir/procedures/`

#### 1. safe_mode_entry.yaml
Automated transition to safe mode:
1. Payload off (0s)
2. AOCS safe mode (1s)
3. Reduce TCS heaters (2s)
4. EPS safe mode (3s)

Wired to FDIR Level 2 rules that trigger safe mode responses.

#### 2. load_shed_stage1.yaml
Stage 1 load shedding (SoC < 30%):
1. Payload standby (0s)
2. Reduce heaters to 50% (1s)
3. Disable aux loads (2s)

#### 3. load_shed_stage2.yaml
Stage 2 load shedding (SoC < 20%):
1. Payload off (0s)
2. AOCS safe (1s)
3. Heaters off (2s)
4. Reduce AOCS power (3s)

#### 4. load_shed_stage3.yaml
Survival mode (SoC < 10%):
1. Payload off (0s)
2. AOCS safe (0.5s)
3. TTC minimum power (1s)
4. Battery heater 10% (1.5s)
5. Disable aux systems (2s)

#### 5. emergency_power_down.yaml
Last resort emergency shutdown:
1. Payload emergency off (0s)
2. TCS emergency shutdown (0.5s)
3. AOCS safe (1s)
4. TTC minimum power (1.5s)
5. EPS emergency profile (2s)

### Procedure Execution Integration

Procedures can be triggered:

1. **Automatically by FDIR rules:** When a Level 2+ rule triggers, associated procedure executes
2. **On demand via S8 commands:** Future enhancement to allow ground station to trigger procedures
3. **Cascading responses:** One procedure can trigger another (via event callbacks)

## 5. FDIR Events

New event IDs added to event catalog (`configs/eosat1/events/event_catalog.yaml`):

### FDIR Advanced Events (0x0F00-0x0F0B)

| Event ID | Name | Severity | Description |
|----------|------|----------|-------------|
| 0x0F00 | FDIR_FAULT_DETECTED | MEDIUM | Fault detected by FDIR |
| 0x0F01 | FDIR_RECOVERY_STARTED | MEDIUM | Recovery action initiated |
| 0x0F02 | FDIR_RECOVERY_COMPLETE | INFO | Recovery completed successfully |
| 0x0F03 | FDIR_RECOVERY_FAILED | HIGH | Recovery failed, escalating |
| 0x0F04 | FDIR_LEVEL_ESCALATION | HIGH | FDIR escalated to higher level |
| 0x0F05 | LOAD_SHED_ACTIVATED | MEDIUM | Load shedding stage activated |
| 0x0F06 | LOAD_SHED_DEACTIVATED | INFO | Load shedding stage deactivated |
| 0x0F07 | PROCEDURE_STARTED | INFO | Procedure execution started |
| 0x0F08 | PROCEDURE_COMPLETED | INFO | Procedure execution completed |
| 0x0F09 | PROCEDURE_FAILED | HIGH | Procedure execution failed |
| 0x0F0A | SAFE_MODE_ENTRY | MEDIUM | Safe mode entry commanded |
| 0x0F0B | SAFE_MODE_EXIT | INFO | Safe mode exit commanded |

Events are emitted via `_emit_event()` and appear in S5 telemetry stream and event logs.

## 6. Configuration Files

### Fault Propagation Configuration

**File:** `configs/eosat1/fdir/fault_propagation.yaml`

Defines:
- Fault cascade rules (source fault → target subsystems)
- Load shedding stages (SoC thresholds → subsystem commands)
- Recovery escalation levels (delay times, max attempts)

### FDIR Rules Configuration

**File:** `configs/eosat1/subsystems/fdir.yaml`

Enhanced with:
- Original rules (battery, attitude, temperature monitoring)
- New cascading rules (bus voltage drops, wheel failures, CAN bus loss)

### Event Catalog

**File:** `configs/eosat1/events/event_catalog.yaml`

Added 12 new FDIR event IDs (0x0F00-0x0F0B).

## 7. Engine Integration

### Initialization

`SimulationEngine.__init__()`:
- Creates `FaultPropagator`, `LoadSheddingManager`, `ProcedureExecutor` instances
- Loads fault propagation configuration from YAML
- Registers load shedding callbacks to subsystems
- Registers procedure command callbacks
- Wires FDIR action callbacks

### Main Loop

`_run_loop()` → `_tick_fdir_advanced()`:
- Checks battery SoC and updates load shedding stage
- Ticks all active procedures
- Emits FDIR events to telemetry stream

### FDIR Tick Order

1. `_tick_fdir()` - Traditional parameter limit checking
2. `_tick_fdir_advanced()` - Load shedding and procedures
3. Subsystem events checked
4. HK packets emitted with FDIR events

## 8. Scenarios and Testing

### Updated Scenario Files

The following scenarios test the new FDIR enhancements:

1. **eps_undervoltage.yaml** - Tests fault propagation from EPS to AOCS/payload
2. **aocs_wheel_failure.yaml** - Tests multi-wheel failure cascading
3. **obc_watchdog.yaml** - Tests CAN bus failure effects

### Expected Behavior

When a scenario injects a fault:
1. FDIR detects violation of parameter limits
2. Primary action triggered (safe mode, power reduction)
3. Fault propagation rule fires, triggering cascading effects
4. Load shedding adjusts stages if SoC affected
5. Associated procedure executes (if configured)
6. FDIR events emitted to S5 stream
7. Recovery state tracked for escalation

### Example: EPS Undervoltage Scenario

```
T+0: Bus voltage drops below 26V
  → FDIR_FAULT_DETECTED event (0x0F00)
  → Payload powers off (FDIR action)
  → PROCEDURE_STARTED: safe_mode_entry

T+1: Payload now standby, AOCS safe mode triggers (cascade)
  → Load shedding enters Stage 2 (if SoC affected)
  → LOAD_SHED_ACTIVATED event (0x0F05)

T+5: AOCS safe mode active, TCS heaters reduced
  → FDIR_RECOVERY_STARTED event (0x0F01)

T+10: EPS enters safe mode (last step of procedure)
  → PROCEDURE_COMPLETED event (0x0F08)
  → FDIR_RECOVERY_COMPLETE event (0x0F02)
```

## 9. Future Enhancements

Potential improvements for future iterations:

1. **Predictive FDIR:** Use slope detection (rate-of-change) to predict failures before limits crossed
2. **Machine Learning:** Train models to predict optimal recovery sequences
3. **Autonomous Safe Return:** After recovery, autonomously navigate to predefined safe orbit
4. **Cross-system Recovery Trees:** Model complex interdependencies (e.g., attitude control affects thermal)
5. **Failure Mode Database:** Reference external FMEA database for mission-tailored responses
6. **Ground Station Integration:** Allow ground to dynamically update procedure configurations
7. **Redundancy Management:** Track and switch between redundant subsystems automatically
8. **Performance Metrics:** Log MTTD, MTTI, MTTR per fault type for training analysis

## 10. References

### Files Modified
- `smo_simulator/fdir.py` - Added all new classes
- `engine.py` - Integrated load shedding and procedures
- `configs/eosat1/subsystems/fdir.yaml` - Enhanced rules
- `configs/eosat1/events/event_catalog.yaml` - Added event IDs

### Files Created
- `configs/eosat1/fdir/fault_propagation.yaml` - Cascading rules and stages
- `configs/eosat1/fdir/procedures/*.yaml` - Procedure definitions (5 procedures)

### Key Classes
- `FaultPropagator` - Manages fault detection and cascading
- `LoadSheddingManager` - Manages SoC-based load shedding
- `ProcedureExecutor` - Manages procedure execution
- `RecoveryState` - Tracks recovery attempts per fault
- `FaultPropagationRule` - Models cascade relationships
- `ProcedureExecution` - Tracks active procedure state

## 11. Code Examples

### Triggering a Procedure

```python
# From FDIR rule callback or manual command
exec_id = engine._procedure_executor.start_procedure("safe_mode_entry", current_time)
# Procedure now executes steps sequentially
```

### Checking Recovery State

```python
recovery = engine._fault_propagator.get_recovery_state("eps_undervoltage")
if recovery and recovery.recovery_attempts < recovery.max_attempts:
    # Can attempt recovery again
    engine._fault_propagator.increment_recovery_attempt("eps_undervoltage", current_time)
```

### Getting Load Shedding Status

```python
current_stage = engine._load_shedding.current_stage()
stage_name = engine._load_shedding.stage_name(current_stage)
# Returns: "Stage 0", "Power Conservation", "Payload Offline", "Survival Mode"
```

### Registering Custom Procedures

```python
custom_proc = {
    "procedure_id": "my_recovery",
    "steps": [
        {"command": "s8_command", "params": {...}, "delay_s": 0.0},
        {"command": "s8_command", "params": {...}, "delay_s": 2.0},
    ]
}
engine._procedure_executor.register_procedure("my_recovery", custom_proc)
engine._procedure_executor.start_procedure("my_recovery", current_time)
```

---

## Summary

The enhanced FDIR system provides:

1. **Comprehensive fault modeling** through cascading rules
2. **Automated power management** via intelligent load shedding
3. **Intelligent recovery** with escalation tracking
4. **Automated procedures** for complex responses
5. **Rich event reporting** for ground station monitoring
6. **Extensible architecture** for future improvements

The system is fully integrated with the simulation engine and ready for testing with the 51 existing procedures in the mission database.

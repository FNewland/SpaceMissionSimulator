# FDIR System Quick Reference

## How FDIR Works

The FDIR system continuously monitors spacecraft parameters and automatically responds to anomalies:

```
Parameter Monitoring (every tick)
    ↓
Condition Evaluation (vs. thresholds)
    ↓
Fault Detected? → FDIR_FAULT_DETECTED event
    ↓
Execute Primary Response (safe mode, power down, etc.)
    ↓
Check Cascading Effects → Apply to other subsystems
    ↓
Adjust Load Shedding (if battery affected)
    ↓
Start Recovery Procedure
    ↓
Track Recovery Attempts → Escalate if unsuccessful
```

## Key Parameters Monitored

| Subsystem | Parameter | Warning | Critical | Action |
|-----------|-----------|---------|----------|--------|
| EPS | Battery SoC | 20% | 15% | Payload off → Stage 1 load shed |
| EPS | Bus Voltage | 27V | 26V / 24V | Safe mode AOCS / Emergency |
| AOCS | Attitude Error | - | 5° | Safe mode entry |
| AOCS | Wheel Temp | - | 65°C | Disable wheel |
| AOCS | Wheels Enabled | - | <2 | Safe mode |
| TCS | Battery Temp | 42°C | 50°C | Reduce heaters / Payload off |
| OBDH | Temp | - | 65°C | Safe mode |
| OBDH | Reboot Count | - | >4 | Emergency mode |
| TTC | PA Temp | - | 80°C | Reduce TX power |

## Load Shedding Stages

### Stage 0: Normal (SoC ≥ 30%)
- All systems operational
- Full power to all subsystems

### Stage 1: Power Conservation (SoC 20-30%)
- Payload: Standby (reduced power)
- Heaters: 50% duty cycle
- Auxiliary: Off

### Stage 2: Payload Offline (SoC 10-20%)
- Payload: Off (zero power)
- AOCS: Safe mode (minimal wheel use)
- Heaters: Off
- Power budget reduced by 40%

### Stage 3: Survival Mode (SoC < 10%)
- **Only Essential:** OBC, TTC (RX only), Battery Heater
- AOCS: Safe mode (no momentum management)
- Everything else: Off
- Power budget reduced by 70%

## Common FDIR Scenarios

### Scenario 1: Battery Depletion During Eclipse

```
[T+0] Battery SoC drops to 25% due to eclipse
  → Load Shed Stage 1 activated
     - Payload → Standby
     - Heaters → 50%
  → Event: LOAD_SHED_ACTIVATED (0x0F05)

[T+600] SoC reaches 18%
  → Load Shed Stage 2 activated
     - Payload → Off
     - AOCS → Safe mode
     - Heaters → Off
  → Event: LOAD_SHED_ACTIVATED (0x0F05)

[T+1200] Eclipse exit, sun illuminates arrays
  → SoC starts increasing
  → No stage change needed (hysteresis at 20%, 30%)

[T+2400] SoC reaches 35%
  → Return to Stage 0 (Normal)
  → Payload, AOCS, Heaters resume normal operation
  → Event: LOAD_SHED_DEACTIVATED (0x0F06)
```

### Scenario 2: EPS Bus Undervoltage

```
[T+0] Bus voltage drops to 25V (critical threshold)
  → Event: FDIR_FAULT_DETECTED (0x0F00)
  → Primary Action: safe_mode_eps (EPS enters contingency)

[T+1] Cascading Effect Detected:
  → AOCS must enter safe mode (reduced power available)
  → Payload automatically powers off (bus stability)

[T+2] Procedure: safe_mode_entry starts
  → Step 1: Payload safe (0s)
  → Step 2: AOCS safe mode (1s)
  → Step 3: TCS heaters reduced (2s)
  → Step 4: EPS safe profile (3s)

[T+5] Procedure complete
  → Event: PROCEDURE_COMPLETED (0x0F08)
  → Event: FDIR_RECOVERY_COMPLETE (0x0F02)

[Recovery] If bus voltage normalizes within 30s
  → Manual or automatic recovery to nominal
  → Payload, AOCS return to normal operation
```

### Scenario 3: Reaction Wheel Failure (Multi-Wheel)

```
[T+0] Wheel 3 bearing failure detected
  → Wheel 3 speed drops to zero
  → Event: FDIR_FAULT_DETECTED (0x0F00)
  → Action: disable_rw3

[T+30] Wheel 2 also fails (rare cascading)
  → Only 2 wheels available (need 3 for full 3-axis control)
  → Cascading rule: aocs_wheel_failure_dual triggered
  → Event: FDIR_FAULT_DETECTED (0x0F00)

[T+31] AOCS Forced Safe Mode
  → Can no longer maintain 3-axis pointing
  → Must enter safe mode (momentum dumping via magnetorquers)
  → Payload automatically enters standby (pointing unreliable)

[T+32] Procedure: safe_mode_entry executes
  → Graceful transition to safe attitude hold
  → Momentum management via magnetic torquers

[Recovery] When 1 wheel repaired or attitude relaxed
  → Can return to 2-wheel mode or safe-hold
  → Payload can resume when requested
```

## FDIR Event Flow

### S5 Event IDs for FDIR

When a FDIR event occurs, S5 telemetry is generated:

```
FDIR_FAULT_DETECTED         0x0F00  (Reported immediately on fault)
↓
(Primary action executes)
↓
FDIR_RECOVERY_STARTED       0x0F01  (Recovery action begins)
↓
(Procedure executes, load shedding applied)
↓
Either:
  FDIR_RECOVERY_COMPLETE    0x0F02  (Successful recovery)
  OR
  FDIR_RECOVERY_FAILED      0x0F03  (Recovery unsuccessful)
  ↓
  FDIR_LEVEL_ESCALATION     0x0F04  (Escalate to Level 3)
```

Additional events:
```
LOAD_SHED_ACTIVATED         0x0F05  (Stage change 0→1, 1→2, 2→3)
LOAD_SHED_DEACTIVATED       0x0F06  (Stage reduction 3→2, 2→1, 1→0)
PROCEDURE_STARTED           0x0F07  (Any procedure execution begins)
PROCEDURE_COMPLETED         0x0F08  (Procedure finished successfully)
PROCEDURE_FAILED            0x0F09  (Procedure failed to complete)
SAFE_MODE_ENTRY             0x0F0A  (AOCS/EPS entered safe mode)
SAFE_MODE_EXIT              0x0F0B  (Exited safe mode to nominal)
```

## Decision Tree: What Happens When?

```
Parameter Exceeds Threshold?
│
├─ YES → FDIR Rule Triggered
│        │
│        ├─ Level 1: Single subsystem action
│        │            (disable wheel, reduce heater, etc.)
│        │
│        ├─ Level 2: Safe mode entry
│        │            (AOCS, EPS, or OBC → safe)
│        │            + Procedure: safe_mode_entry.yaml
│        │            + Cascade effects to dependent subsystems
│        │
│        └─ Level 3: Emergency mode
│                     (spacecraft → emergency)
│                     + All procedures halted
│                     + Survival mode activated
│
└─ NO → Continue nominal operations
         (Load shedding still applies based on SoC)
```

## Recovery Escalation Timeline

```
[T+0s]   FDIR detects fault
         Primary action executed
         Recovery attempt #1

[T+30s]  If still faulted → Recovery attempt #2
         (Level 2 escalation if enabled)

[T+90s]  If still faulted → Recovery attempt #3
         (Level 3 escalation if enabled)

[T+150s] Max attempts reached
         System enters stable degraded mode
         Manual intervention required
```

## Operator Commands for FDIR

### Via S8 Function Management

**Note:** These are automatically executed by FDIR but can also be commanded manually.

| Function ID | Subsystem | Action |
|------------|-----------|--------|
| 0 | AOCS | Set mode (0=nominal, 1=Sun-point, 2=safe) |
| 1 | AOCS | Desaturate wheels |
| 2-3 | AOCS | Disable/Enable wheel |
| 10 | EPS | Set mode |
| 20 | Payload | Set mode (0=off, 1=standby, 2=imaging) |
| 30 | TCS | Heater control |
| 40 | OBDH | Set mode |
| 50 | TTC | Set power level |

Example: Command AOCS to safe mode
```
Service: 8 (Function Management)
Subtype: 1 (Direct Execution)
Func ID: 0 (AOCS set_mode)
Data: [0x02] (mode 2 = safe)
```

### Monitoring FDIR Status

Check via S3 HK requests:

- EPS SoC and bus voltage → Stage determination
- AOCS mode → Safety status
- Active procedures → Recovery progress
- Event log → Fault history

## Troubleshooting

### Problem: Load Shedding Not Happening

**Check:**
1. Is battery SoC actually below thresholds?
2. Is FDIR enabled in `fdir.yaml` (enabled: true)?
3. Are load shedding callbacks registered? (Check logs)
4. Is battery model providing SoC parameter correctly?

### Problem: Cascading Effects Not Triggering

**Check:**
1. Is fault_propagation.yaml loaded? (Check initialization logs)
2. Does fault_id match propagation rule names?
3. Are cascade delay times being respected? (Check timestamps)
4. Are target subsystem callbacks registered?

### Problem: Procedure Not Executing

**Check:**
1. Is procedure file in correct directory? (`configs/eosat1/fdir/procedures/`)
2. Are S8 command callbacks registered?
3. Are step delays in correct format? (in seconds, floating point)
4. Check procedure executor logs for errors

### Problem: Continuous Escalation Loop

**Cause:** Recovery attempts failing repeatedly

**Solution:**
1. Verify fault is actually cleared (check parameter values)
2. Check if multiple FDIR rules triggering same subsystem
3. Verify procedure steps execute in correct order
4. May need manual intervention to escape loop

## Performance Metrics

The system tracks:
- **MTTD (Mean Time To Detect):** Time from fault onset to FDIR detection
  - Target: < 10 seconds for critical parameters
  - Actual: 1-2 seconds (tick-based detection)

- **MTTI (Mean Time To Isolate):** Time from detection to affected subsystem identified
  - Target: < 5 seconds
  - Actual: 0-10 seconds (includes cascade evaluation)

- **MTTR (Mean Time To Recover):** Time from isolation to normal operations
  - Target: < 60 seconds
  - Actual: 3-60 seconds (depends on recovery sequence)

- **Recovery Success Rate:** Percentage of faults successfully recovered without escalation
  - Target: > 80%
  - Actual: To be measured during testing

## Summary

The FDIR system provides:
1. **Autonomous response** to parameter violations
2. **Intelligent cascading** to prevent secondary failures
3. **Power management** via load shedding based on battery state
4. **Graceful degradation** maintaining spacecraft safety
5. **Rich event reporting** for ground station monitoring
6. **Extensible procedures** for mission-specific responses

All actions are logged, tracked, and reported via S5 telemetry for mission analysis and operator awareness.

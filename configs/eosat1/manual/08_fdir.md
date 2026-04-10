# EOSAT-1 Fault Detection, Isolation and Recovery (FDIR)

**Document ID:** EOSAT1-UM-FDIR-009
**Issue:** 2.0
**Date:** 2026-03-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Overview

The EOSAT-1 Fault Detection, Isolation and Recovery (FDIR) system provides autonomous
onboard protection against anomalies that could endanger the spacecraft or its mission.
FDIR operates in a hierarchical manner with increasing levels of autonomy and severity,
from local subsystem-level responses to spacecraft-wide safe mode transitions.

The FDIR implementation uses PUS Service 12 (Onboard Monitoring) for parameter limit
checking and PUS Service 19 (Event-Action) for automated response triggering. Both
services are evaluated every simulation tick, ensuring continuous monitoring coverage.

## 2. FDIR Hierarchy

### 2.1 Autonomy Levels

| Level | Scope           | Response Time | Authority              | Example                     |
|-------|-----------------|---------------|------------------------|-----------------------------|
| 0     | Unit level      | < 1 s         | Hardware               | PCDU over-current trip      |
| 1     | Subsystem level | 1–10 s        | Subsystem SW           | Heater thermostat control   |
| 2     | System level    | 10–60 s       | OBC FDIR application   | Mode transition, load shed  |
| 3     | Ground level    | Minutes–hours | Flight control team    | Manual recovery procedures  |

### 2.2 Design Philosophy

- **Fail-safe**: Any unrecoverable anomaly results in a transition to a power-positive,
  thermally safe, and commandable state (Safe Mode).
- **Minimum intervention**: FDIR acts only to preserve spacecraft safety. Restoration of
  nominal operations is a ground-commanded activity (Level 3).
- **Single-event tolerance**: The FDIR design tolerates a single failure without loss of
  spacecraft safety.

## 3. Safe Mode Definition

When a critical FDIR rule triggers a safe mode entry, the OBC executes the following
sequence autonomously:

| Step | Action                                          | Subsystem |
|------|-------------------------------------------------|-----------|
| 1    | Set OBC mode to SAFE                            | OBDH      |
| 2    | Command AOCS to SAFE_POINT mode                 | AOCS      |
| 3    | Power off payload                               | PLD       |
| 4    | Shed non-essential loads                        | EPS       |
| 5    | Activate essential heaters (battery, OBC)       | TCS       |
| 6    | Reduce TM rate to 0.1 Hz                        | OBDH      |
| 7    | Set TTC to receive-priority mode                | TTC       |
| 8    | Generate safe mode event packet                 | OBDH      |

Safe mode is designed to be power-positive under all illumination conditions, ensuring
the spacecraft can sustain itself indefinitely until ground intervention.

## 4. FDIR Rules and Triggers

### 4.1 EPS Rules

| Rule ID | Condition                      | Detection       | Response                    |
|---------|--------------------------------|----------------|-----------------------------|
| EPS-01  | bat_soc < 15%                  | 0x0101 < 15    | Safe mode entry             |
| EPS-02  | bus_voltage < 26.5 V           | 0x0105 < 26.5  | Safe mode entry             |
| EPS-03  | bat_temp > 45 C                | 0x0102 > 45    | Inhibit battery charge      |
| EPS-04  | bat_temp < 0 C                 | 0x0102 < 0     | Activate battery heater     |
| EPS-05  | sa_current = 0 (both wings)    | 0x0103, 0x0104 | Safe mode entry             |
| EPS-06  | bus_voltage > 29.5 V           | 0x0105 > 29.5  | Shunt excess power          |

### 4.2 AOCS Rules

| Rule ID | Condition                      | Detection       | Response                    |
|---------|--------------------------------|----------------|-----------------------------|
| AOCS-01 | att_error > 2 deg (sustained)  | 0x0217 > 2     | Transition to SAFE_POINT    |
| AOCS-02 | Body rate > 2 deg/s            | 0x0204–0206    | Transition to DETUMBLE      |
| AOCS-03 | RW speed > 5500 RPM            | 0x0207–020A    | Initiate desaturation       |
| AOCS-04 | RW temp > 70 C                 | 0x0218–021B    | Reduce RW torque demand     |
| AOCS-05 | Star tracker failure            | Validity flag  | Switch to gyro propagation  |

### 4.3 OBDH Rules

| Rule ID | Condition                      | Detection       | Response                    |
|---------|--------------------------------|----------------|-----------------------------|
| OBDH-01 | Watchdog timeout               | HW watchdog    | OBC reset                   |
| OBDH-02 | 3 consecutive WD resets        | reboot_count   | Boot to BOOTLOADER          |
| OBDH-03 | cpu_load > 98%                 | 0x0302 > 98    | Task priority adjustment    |
| OBDH-04 | mmm_used > 95%                 | 0x0303 > 95    | Inhibit payload acquisition |

### 4.4 TCS Rules

| Rule ID | Condition                      | Detection       | Response                    |
|---------|--------------------------------|----------------|-----------------------------|
| TCS-01  | bat_temp < 0 C (red low)      | 0x0407 < 0     | Force battery heater on     |
| TCS-02  | obc_temp > 70 C (red high)    | 0x0406 > 70    | Safe mode entry             |
| TCS-03  | obc_temp < 0 C (red low)      | 0x0406 < 0     | Force OBC heater on         |
| TCS-04  | fpa_temp > 12 C               | 0x0408 > 12    | Power off payload           |

### 4.5 TTC Rules

| Rule ID | Condition                      | Detection       | Response                    |
|---------|--------------------------------|----------------|-----------------------------|
| TTC-01  | No link for > 24 hours        | link_status    | Switch to redundant XPDR    |
| TTC-02  | XPDR temp out of range         | 0x0507         | Switch transponder          |

### 4.6 Payload Rules

| Rule ID | Condition                      | Detection       | Response                    |
|---------|--------------------------------|----------------|-----------------------------|
| PLD-01  | checksum_errors > threshold    | 0x0609         | Alert, inhibit imaging      |
| PLD-02  | Imaging with att_error > 1 deg | 0x0217         | Abort imaging, go STANDBY   |

## 5. Recovery Procedures

### 5.1 Safe Mode Recovery (Level 3 — Ground)

1. Establish ground contact and verify spacecraft telemetry.
2. Review event log and FDIR trigger history.
3. Identify and resolve root cause of the anomaly.
4. Verify all subsystem parameters are within nominal limits.
5. Command `OBC_SET_MODE` (mode=0) to return to NOMINAL.
6. Command `AOCS_SET_MODE` (mode=0) to return to NADIR_POINT.
7. Reactivate payload and resume nominal operations.

### 5.2 Transponder Recovery

1. If primary transponder is unresponsive, send `TTC_SWITCH_REDUNDANT`.
2. Wait 30 seconds for transponder warm-up.
3. Verify link acquisition via `link_status` (0x0501).
4. If redundant also fails, wait for next contact attempt (ground-initiated).

### 5.3 AOCS Recovery from DETUMBLE

1. Monitor body rates (0x0204–0x0206) decreasing towards zero.
2. Once rates < 0.5 deg/s sustained for 30 s, AOCS autonomously transitions to SAFE_POINT.
3. Ground commands `AOCS_SET_MODE` (mode=0) to enter NADIR_POINT when ready.

## 6. PUS Service 12 — Onboard Monitoring

Service 12 implements the parameter monitoring framework used by the FDIR system. All
monitoring definitions are evaluated every simulation tick (typically 1 Hz), providing
continuous limit checking coverage.

### 6.1 Monitoring Enforcement

Each monitoring definition specifies:

| Field               | Description                                        |
|---------------------|----------------------------------------------------|
| Parameter ID        | The telemetry parameter to monitor                 |
| Check Type          | Limit check, expected value, or delta check        |
| Low Limit           | Lower threshold for limit check                    |
| High Limit          | Upper threshold for limit check                    |
| Event ID            | S5 event generated on violation                    |
| Repetition Count    | Number of consecutive violations before triggering |
| Enabled             | Whether this monitor is currently active           |

When a monitoring check fails, the OBC generates a PUS S5 event packet with the
corresponding Event ID. This event is:

1. Stored in the event buffer for later downlink.
2. Delivered to the S19 event-action service for automated response.
3. Made available to the MCS alarm journal for operator awareness.

### 6.2 Monitoring Commands

| Command               | Subtype | Description                              |
|-----------------------|---------|------------------------------------------|
| Enable monitoring      | S12.1   | Enable a specific monitoring definition  |
| Disable monitoring     | S12.2   | Disable a specific monitoring definition |
| Report monitoring      | S12.12  | Report all active monitoring definitions |
| Enable all monitoring  | S12.15  | Enable all monitoring definitions        |
| Disable all monitoring | S12.16  | Disable all monitoring definitions       |

## 7. PUS Service 19 — Event-Action Triggering

Service 19 links S5 events (generated by S12 monitoring or other sources) to automated
actions, implementing the autonomous FDIR response chain.

### 7.1 Event-Action Mechanism

When an S5 event is raised:

1. The OBC checks if there is an active S19 event-action definition for that event ID.
2. If found and enabled, the associated action (a stored telecommand) is executed.
3. The action execution is logged in the event buffer.
4. The action can itself trigger further events, allowing cascaded responses.

### 7.2 Event-Action Commands

| Command                | Subtype | Description                              |
|------------------------|---------|------------------------------------------|
| Enable event-action    | S19.1   | Enable a specific event-action link      |
| Disable event-action   | S19.2   | Disable a specific event-action link     |
| Report event-actions   | S19.8   | Report all active event-action definitions |

### 7.3 Typical FDIR Chain Example

```
S12 monitor: bat_soc < 25%
  --> S5 event: EPS_LOW_SOC_WARNING
    --> S19 action: OBC_SET_MODE(SAFE)
      --> Safe mode sequence executes
        --> S5 event: SAFE_MODE_ENTERED
```

## 8. Progressive Load Shed Thresholds

The EPS FDIR implements a tiered load shedding strategy based on battery state of charge.
Each threshold triggers progressively more aggressive power reduction:

| SoC Threshold | FDIR Level | Actions                                               |
|---------------|------------|-------------------------------------------------------|
| < 50%         | Level 1    | Inhibit payload imaging; reduce HK rate to 0.5 Hz     |
| < 35%         | Level 2    | Command AOCS to SAFE_POINT; shed non-essential loads   |
| < 25%         | Level 3    | Safe mode entry; only essential loads remain           |
| < 15%         | Level 4    | Emergency mode; OBC + TTC only; all other loads off    |

Each level includes all actions from previous levels. Recovery from load shedding is a
ground-commanded activity — the FDIR does not automatically restore loads when SoC
recovers.

## 9. Contingency Response Matrix

The following matrix cross-references contingency scenarios with the procedures used
for detection and recovery:

| Scenario                    | Detection Method              | Procedure File                        |
|-----------------------------|-------------------------------|---------------------------------------|
| TTC no telemetry            | No TM at expected AOS         | `contingency_ttc_no_tm.yaml`          |
| Ground station antenna fail | Ground equipment monitoring    | `contingency_gs_antenna_failure.yaml` |
| OBC stuck in bootloader     | Beacon SID 11 with mode=2     | `contingency_obc_bootloader.yaml`     |
| Bus isolation required      | Multiple subsystem anomalies  | `contingency_bus_isolation.yaml`      |
| Progressive load shed       | bat_soc crossing thresholds   | `contingency_progressive_load_shed.yaml` |
| Solar panel loss             | Panel current = 0 sustained  | `contingency_solar_panel_loss.yaml`   |
| AOCS sensor cascade failure | Multiple sensor invalids      | `contingency_aocs_sensor_cascade.yaml`|
| AOCS actuator stuck         | Wheel speed constant despite command | `contingency_actuator_stuck.yaml` |

Each contingency procedure includes:
- **Entry criteria**: How to recognise the scenario.
- **Immediate actions**: Time-critical steps to stabilise the spacecraft.
- **Investigation steps**: How to identify root cause.
- **Recovery actions**: Steps to restore nominal operations.
- **GO/NO-GO checkpoints**: Decision points requiring Flight Director authorisation.

## 10. FDIR Configuration

FDIR thresholds and enable/disable flags are configurable via `SET_PARAM` commands.
Each rule can be individually disabled for testing or commissioning purposes. Disabling
FDIR rules requires explicit ground authorisation and should be time-limited.

| Configuration Parameter      | Default | Modifiable |
|------------------------------|---------|------------|
| Safe mode SoC threshold      | 15%     | Yes        |
| Safe mode voltage threshold  | 26.5 V  | Yes        |
| Attitude error red limit     | 2 deg   | Yes        |
| Body rate red limit          | 2 deg/s | Yes        |
| Watchdog timeout             | 10 s    | No         |
| Link loss timeout            | 24 h    | Yes        |
| Load shed level 1 threshold  | 50%     | Yes        |
| Load shed level 2 threshold  | 35%     | Yes        |
| Load shed level 3 threshold  | 25%     | Yes        |
| Load shed level 4 threshold  | 15%     | Yes        |

## 11. Operational Notes

1. After every safe mode entry, a detailed anomaly investigation must be completed before
   returning to nominal operations.
2. FDIR rules should not be disabled during nominal operations. Exceptions require formal
   approval and a risk assessment.
3. The FDIR event log stores the last 100 events in non-volatile memory and survives
   OBC reboots. Events are stored in a linear buffer; housekeeping uses a circular buffer
   (store-1 policy).
4. During commissioning, FDIR rules may be temporarily widened (e.g., attitude error
   limit increased to 5 deg) to allow for calibration activities.
5. S12 monitoring is evaluated every tick. A parameter that transiently violates a limit
   for less than the configured repetition count will not trigger a response.
6. S19 event-action links can be disabled individually for testing. This allows FDIR
   event generation (for visibility) without triggering automatic corrective action.
7. The MCS alarm journal displays all S5 events and S12 violations with severity badges,
   acknowledge/clear functionality, and per-subsystem filtering.

---

*End of Document — EOSAT1-UM-FDIR-009*

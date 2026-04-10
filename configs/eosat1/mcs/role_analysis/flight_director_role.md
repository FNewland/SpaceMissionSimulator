# Flight Director -- Role Analysis

**Position ID:** `flight_director`
**Display Name:** Flight Director
**Subsystems:** All (eps, aocs, tcs, obdh, ttc, payload)
**Visible Tabs:** overview, eps, aocs, tcs, obdh, ttc, payload, commanding, pus, procedures, manual
**Manual Sections:** All

## 1. Mission Lifecycle Phases and Applicable Procedures

### LEOP (Launch and Early Orbit Phase)

The Flight Director has authority over every LEOP procedure:

| Procedure | ID | FD Role |
|---|---|---|
| First Acquisition of Signal | LEOP-001 | Authorize pass start, GO/NO-GO decisions |
| Initial Health Check | LEOP-002 | Coordinate checkout sequence |
| Initial Orbit Determination | LEOP-003 | Approve orbit solution |
| Solar Array Verification | LEOP-004 | Authorize deployment verification |
| Sun Acquisition | LEOP-005 | Authorize attitude maneuver |
| Time Synchronisation | LEOP-006 | Execute time sync, verify accuracy |
| LEOP Summary Checkout | LEOP-007 | Review all subsystem health before commissioning |

### Commissioning

| Procedure | ID | FD Role |
|---|---|---|
| EPS Checkout | COM-001 | Authorize power line switching |
| TCS Verification | COM-002 | Authorize heater activation |
| AOCS Sensor Calibration | COM-003 | Authorize calibration sequence |
| AOCS Actuator Checkout | COM-004 | Authorize actuator tests |
| AOCS Mode Transitions | COM-005 | Authorize mode changes |
| TTC Link Verification | COM-006 | Coordinate link test |
| OBDH Checkout | COM-007 | Authorize OBC tests |
| FDIR Configuration | COM-008 | Approve FDIR configuration |
| Payload Power On | COM-009 | Authorize payload activation |
| FPA Cooler Activation | COM-010 | Authorize cooler start |
| Payload Calibration | COM-011 | Authorize calibration |
| First Light | COM-012 | Authorize first imaging |

### Nominal Operations

| Procedure | ID | FD Role |
|---|---|---|
| Pass Startup | NOM-001 | Authorize pass start, initial GO/NO-GO |
| Software Upload | NOM-006 | Authorize upload, GO/NO-GO at each stage |
| Clock Synchronisation | NOM-008 | Verify time delta threshold |
| Routine Health Check | NOM-009 | Review all subsystem parameters |
| Shift Handover | NOM-012 | Conduct handover briefing and log |

### Contingency

| Procedure | ID | FD Role |
|---|---|---|
| Under-Voltage Load Shed | CTG-001 | Authorize load shedding sequence |
| AOCS Anomaly Recovery | CTG-002 | Authorize recovery actions |
| TTC Link Loss Recovery | CTG-003 | Coordinate link recovery |
| Thermal Exceedance | CTG-004 | Authorize thermal response |
| EPS Safe Mode | CTG-005 | Authorize EPS recovery |
| Payload Anomaly | CTG-006 | Authorize payload response |
| Reaction Wheel Anomaly | CTG-007 | Authorize RW recovery |
| Star Tracker Failure | CTG-008 | Authorize ST recovery |
| Solar Array Degradation | CTG-009 | Authorize power budget adjustment |
| OBDH Watchdog Recovery | CTG-010 | Authorize OBC recovery actions |
| OBC Redundancy Switchover | CTG-011 | Authorize OBC switchover |
| Overcurrent Response | CTG-012 | Authorize power line reset |
| Battery Cell Failure | CTG-013 | Authorize battery management |
| BER Anomaly | CTG-014 | Authorize link reconfiguration |
| Memory Segment Failure | CTG-016 | Authorize memory management |
| Bus Failure Switchover | CTG-017 | Authorize bus switchover |
| Boot Loader Recovery | CTG-018 | Authorize boot recovery |

### Emergency

| Procedure | ID | FD Role |
|---|---|---|
| Emergency Safe Mode | EMG-001 | Command immediate safe mode entry |
| Total Power Failure | EMG-002 | Coordinate emergency response |
| OBC Reboot | EMG-003 | Authorize emergency reboot |
| Loss of Communication | EMG-004 | Coordinate comms recovery |
| Loss of Attitude | EMG-005 | Authorize emergency attitude recovery |
| Thermal Runaway | EMG-006 | Coordinate thermal emergency |

## 2. Available Commands and Telemetry

### Commands

The Flight Director has `allowed_commands: "all"`, granting access to every command in the TC catalog across all PUS services (1, 3, 5, 6, 8, 9, 11, 12, 15, 17, 19, 20) and all func_ids (0-55).

**Key commands frequently exercised by this position:**

- `CONNECTION_TEST` (S17.1) -- Link verification during pass startup
- `HK_REQUEST` (S3.27) -- One-shot health check across all SIDs (1-6, 10)
- `OBC_SET_MODE` (S8, func_id 40) -- Direct spacecraft mode control
- `SET_TIME` (S9.1) -- Time synchronisation authority
- All critical commands (OBC_REBOOT, OBC_SWITCH_UNIT) require FD authorization

### Telemetry

Full visibility into all HK structures:

| SID | Name | Interval | Key Parameters |
|---|---|---|---|
| 1 | EPS | 1 s | bat_soc, bus_voltage, power_gen, power_cons, oc_trip_flags |
| 2 | AOCS | 4 s | att_error, mode, body rates, RW speeds |
| 3 | TCS | 60 s | Panel and component temperatures, heater status |
| 4 | Platform (OBDH) | 8 s | cpu_load, reboot_count, active_obc, bus status |
| 5 | Payload | 8 s | mode, fpa_temp, store_used, image_count |
| 6 | TTC | 8 s | link_status, rssi, link_margin, BER |
| 10 | BootLoader | 16 s | Minimal set for boot-loader recovery |

**Overview Display Widgets:**
- Battery SoC gauge (0-100%)
- Link status indicator
- Attitude error gauge (0-10 deg)
- CPU load gauge (0-100%)
- SW image status indicator
- Value table: bus_voltage, aocs.mode, obdh.mode, ttc.link_margin, payload.mode

## 3. Inter-Position Coordination Needs

The Flight Director is the coordination hub for all cross-position operations:

| Coordination Scenario | Positions Involved | FD Responsibility |
|---|---|---|
| LEOP first acquisition | ttc | Authorize pass start, issue GO/NO-GO |
| Initial health check | eps_tcs, fdir_systems | Sequence checkout across subsystems |
| Commissioning payload | payload_ops, eps_tcs | Authorize activation, monitor power budget |
| First light imaging | payload_ops, aocs | Authorize imaging, confirm pointing readiness |
| Eclipse transition | eps_tcs, aocs | No direct command role; monitors for anomalies |
| Thermal exceedance | eps_tcs, payload_ops | Authorize response, coordinate safing |
| Software upload | fdir_systems | GO/NO-GO at each upload stage |
| Any contingency | Varies | Authorize all recovery actions |

**Shift Handover (NOM-012):** The Flight Director conducts the formal handover briefing, transferring console authority, open action items, and spacecraft state summary to the incoming shift.

## 4. GO/NO-GO Responsibilities

The Flight Director holds GO/NO-GO authority for every major operational decision:

### Pass-Level GO/NO-GO
- **Pass Startup (NOM-001, LEOP-001):** Verify link acquired, all positions report nominal, authorize operations for the pass.

### Phase Transition GO/NO-GO
- **LEOP-to-Commissioning (LEOP-007):** Review all subsystem health; declare GO for commissioning.
- **Commissioning-to-Nominal (COM-012):** Confirm first light success and subsystem maturity.

### Activity GO/NO-GO
- **Mode transitions:** Every AOCS mode change, OBC mode change, or payload mode change requires FD authorization.
- **Critical commands:** OBC_REBOOT (func_id 42, criticality: critical), OBC_SWITCH_UNIT (func_id 43, criticality: critical), DELETE_ALL_SCHEDULED (S11.11, criticality: caution), DELETE_STORE (S15.11, criticality: caution).
- **Software upload:** GO/NO-GO at each stage (memory load, verify, boot).

### Emergency Authority
- **EMG-001 Emergency Safe Mode:** The Flight Director is the sole position authorized to command immediate safe mode.
- **Anomaly Coordination:** For any red-limit violation or FDIR-triggered event, the FD coordinates the response and authorizes all recovery actions before specialist positions execute them.

---
*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

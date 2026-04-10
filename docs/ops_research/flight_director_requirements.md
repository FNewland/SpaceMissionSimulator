# EOSAT-1 Flight Director Position -- Comprehensive Requirements Document

**Document ID:** EOSAT1-REQ-FD-001
**Issue:** 1.0
**Date:** 2026-03-12
**Classification:** UNCLASSIFIED -- For Simulation Use Only
**Position:** Flight Director (`flight_director`)

---

## Table of Contents

1. [Scope and Authority](#1-scope-and-authority)
2. [Equipment Under FD Responsibility](#2-equipment-under-fd-responsibility)
3. [Commands and Telemetry Requirements](#3-commands-and-telemetry-requirements)
4. [Operational Procedures](#4-operational-procedures)
5. [Training Scenarios](#5-training-scenarios)
6. [MCS Display and Tool Requirements](#6-mcs-display-and-tool-requirements)
7. [Planner Requirements](#7-planner-requirements)
8. [Simulator Fidelity Requirements](#8-simulator-fidelity-requirements)
9. [LEOP / Separation Timeline Requirements](#9-leop--separation-timeline-requirements)
10. [Pass Plan Visualization Requirements](#10-pass-plan-visualization-requirements)
11. [Dual Ground Station Coordination Model](#11-dual-ground-station-coordination-model)
12. [Alarm Escalation Workflow](#12-alarm-escalation-workflow)
13. [Command Verification Gate Requirements](#13-command-verification-gate-requirements)
14. [GO/NO-GO Coordination Requirements](#14-gono-go-coordination-requirements)
15. [Shift Handover Requirements](#15-shift-handover-requirements)

---

## 1. Scope and Authority

### 1.1 Position Definition

The Flight Director is the senior operational authority during all mission phases. As defined in `configs/eosat1/mcs/positions.yaml`, the flight_director position has:

- **Subsystem visibility:** All subsystems (eps, aocs, tcs, obdh, ttc, payload)
- **Command authority:** `allowed_commands: "all"` -- unrestricted access to every PUS service and every S8 func_id
- **Tab visibility:** overview, eps, aocs, tcs, obdh, ttc, payload, commanding, pus, procedures, manual
- **Overview subsystems:** All six (eps, aocs, tcs, obdh, ttc, payload)
- **Manual sections:** All

### 1.2 Authority Boundaries

| Authority | Description |
|---|---|
| GO/NO-GO | Sole position authorised to initiate polls (`/api/go-nogo/poll` restricted to `position == "flight_director"`) |
| Emergency declaration | Only FD may command EMG-001 (Emergency Safe Mode) unilaterally |
| Critical command approval | OBC_REBOOT (func_id 42), OBC_SWITCH_UNIT (func_id 43), DELETE_ALL_SCHEDULED (S11.11), DELETE_STORE (S15.11) all require FD authorisation |
| Phase transitions | LEOP-to-Commissioning (LEOP-007), Commissioning-to-Nominal (COM-012) |
| Shift handover | FD conducts formal console authority transfer (NOM-012) |

---

## 2. Equipment Under FD Responsibility

The Flight Director monitors all spacecraft equipment. The following maps hardware equipment to the relevant subsystem abbreviation, HK SID, and key telemetry parameters.

### 2.1 Electrical Power Subsystem (EPS) -- HK SID 1 (1 Hz)

| Equipment | Parameters | Param IDs |
|---|---|---|
| GaAs Solar Array A | sa_a_current, sa_a_voltage | 0x0103, 0x010B |
| GaAs Solar Array B | sa_b_current, sa_b_voltage | 0x0104, 0x010C |
| Li-Ion Battery (40 Ah, 28V) | bat_voltage, bat_soc, bat_current, bat_temp | 0x0100, 0x0101, 0x0102, 0x0109 |
| 28V Regulated Bus | bus_voltage | 0x0105 |
| Power Distribution Unit (8 lines) | pl_status_0..7, pl_current_0..7, oc_trip_flags | 0x0110-0x0117, 0x0118-0x011F, 0x010D |
| MPPT Controller | mppt_efficiency_a, mppt_efficiency_b | 0x0122, 0x0123 |
| Battery Management | bat_dod, bat_cycles | 0x0120, 0x0121 |

### 2.2 Attitude and Orbit Control System (AOCS) -- HK SID 2 (4 Hz)

| Equipment | Parameters | Param IDs |
|---|---|---|
| Reaction Wheels 1-4 | rw1-rw4_speed, rw1-rw4_current, rw1-rw4_enabled | 0x0207-0x020A, 0x0250-0x0253, 0x0254-0x0257 |
| Star Tracker 1 | st1_status, st1_num_stars | 0x0240, 0x0241 |
| Star Tracker 2 | st2_status | 0x0243 |
| Coarse Sun Sensors | css_x, css_y, css_z, css_valid | 0x0245-0x0248 |
| Magnetorquers X/Y/Z | mtq_x_duty, mtq_y_duty, mtq_z_duty | 0x0258-0x025A |
| Rate Gyroscopes | gyro_bias_x/y/z, gyro_temp | 0x0270-0x0272, 0x0273 |
| GPS Receiver | gps_valid, gps_pdop, gps_sats, gps_tow | 0x0274-0x0277 |
| AOCS Controller | mode, att_error, rates (roll/pitch/yaw), total_momentum, submode, time_in_mode | 0x020F, 0x0217, 0x0204-0x0206, 0x025B, 0x0262, 0x0264 |

### 2.3 Thermal Control Subsystem (TCS) -- HK SID 3 (60s)

| Equipment | Parameters | Param IDs |
|---|---|---|
| Structural Panels (+X/-X/+Y/-Y/+Z/-Z) | temp_panel_px/mx/py/my/pz/mz | 0x0400-0x0405 |
| OBC Heater | temp_obc, htr_obc, htr_obc_duty | 0x0406, 0x040B, 0x040F |
| Battery Heater | temp_battery, htr_battery, htr_battery_duty | 0x0407, 0x040A, 0x040E |
| FPA Cooler | temp_fpa, cooler_fpa | 0x0408, 0x040C |
| Thruster Heater | temp_thruster, htr_thruster_duty | 0x0409, 0x0410 |
| Total Heater Power | total_heater_power | 0x0411 |

### 2.4 On-Board Data Handling (OBDH) -- HK SID 4 (8s)

| Equipment | Parameters | Param IDs |
|---|---|---|
| OBC-A (Primary) | active_obc, sw_image, mode, cpu_load, uptime, reboot_count | 0x030C, 0x030E, 0x0300, 0x0302, 0x0308, 0x030A |
| OBC-B (Redundant) | obc_b_status, boot_count_a, boot_count_b | 0x030D, 0x0310, 0x0311 |
| CAN Bus A/B | active_bus, bus_a_status, bus_b_status | 0x030F, 0x030D, (bus status) |
| TM/TC Processing | tc_rx_count, tc_acc_count, tc_rej_count, tm_count | 0x0304, 0x0305, 0x0306, 0x0307 |
| Data Buffers | hktm_buf_fill, event_buf_fill, alarm_buf_fill | 0x0312, 0x0313, 0x0314 |
| Memory Health | seu_count, scrub_progress, stack_usage, heap_usage | 0x0319, 0x031A, 0x031C, 0x031D |
| Last Reboot Cause | last_reboot_cause | 0x0316 |

### 2.5 Telemetry, Tracking and Command (TTC) -- HK SID 6 (8s)

| Equipment | Parameters | Param IDs |
|---|---|---|
| Primary S-Band Transponder | mode, link_status, rssi, link_margin, xpdr_temp | 0x0500, 0x0501, 0x0502, 0x0503, 0x050A |
| Redundant Transponder | (switchable via TTC_SWITCH_REDUNDANT) | -- |
| Power Amplifier | pa_on, pa_temp, tx_fwd_power | 0x0510, 0x050F, 0x050D |
| RF Chain | carrier_lock, bit_sync, frame_sync, ber, eb_n0 | 0x0511, 0x0512, 0x0513, 0x050C, 0x0507 |
| Data Handling | tm_data_rate, cmd_rx_count | 0x0506, 0x0509 |
| Ranging | range_km, agc_level, doppler_hz, range_rate_km_s | (via planner gs_range_km, 0x051A, 0x051B, 0x051C) |

### 2.6 Payload -- HK SID 5 (8s)

| Equipment | Parameters | Param IDs |
|---|---|---|
| Multispectral Imager | mode, fpa_temp, fpa_ready, duty_cycle_pct, imaging_active | 0x0600, 0x0601, 0x0610, 0x0612 |
| FPA Cooler | cooler_pwr | 0x0602 |
| Payload Mass Memory | store_used, mem_used_mb, mem_total_mb, mem_segments_bad | 0x0604, 0x060A, 0x060B, 0x060C |
| Image Catalog | image_count, checksum_errors, last_scene_id, last_scene_quality | 0x0605, 0x0609, 0x060D, (quality) |
| Detector | detector_temp, snr | 0x0617, 0x0616 |
| Calibration | compression_ratio, cal_lamp_on | 0x0614, 0x0615 |

### 2.7 Boot Loader Mode -- HK SID 10 (16s)

Minimal telemetry available when OBC is in boot loader:

| Parameter | Param ID | Purpose |
|---|---|---|
| active_obc | 0x030C | Which OBC is running |
| sw_image | 0x030E | Current software image |
| bus_voltage | 0x0105 | Power state |
| obdh.temp | 0x0301 | OBC temperature |
| uptime | 0x0308 | Time since boot |
| reboot_count | 0x030A | Cumulative reboots |
| boot_count_b | 0x0311 | Boot counter |
| last_reboot_cause | 0x0316 | Reboot reason |

---

## 3. Commands and Telemetry Requirements

### 3.1 PUS Services Required by the Flight Director

The FD requires access to all 12 enabled PUS services. The following table maps each service to its FD-specific use cases.

| Service | Label | Key Subtypes | FD Use Case |
|---|---|---|---|
| S1 | Request Verification | 1 (accepted), 2 (rejected), 7 (completed), 8 (failed) | Monitor all command verification reports; track in verification log |
| S3 | Housekeeping | 5/6 (enable/disable), 25/27 (request), 31 (set interval) | Request all SIDs (1-6, 10) for health checks; adjust reporting rates |
| S5 | Event Reporting | 5/6 (enable/disable), 7/8 (enable/disable all) | Monitor all events; enable/disable event IDs during anomaly investigation |
| S6 | Memory Management | 2 (load), 5 (dump), 9 (check) | Authorise software uploads; verify memory integrity |
| S8 | Function Management | 1 (all func_ids 0-55) | Direct subsystem control; emergency mode commands |
| S9 | Time Management | 1 (set time), 2 (request time) | LEOP time synchronisation; periodic clock drift correction |
| S11 | Scheduling | 4 (schedule), 7 (delete), 9 (disable), 11 (delete all), 13 (enable), 17 (list) | Review/manage onboard schedule; verify at shift handover |
| S12 | Onboard Monitoring | 1/2 (enable/disable), 6/7 (add/delete definition) | Review FDIR monitoring config; approve changes during commissioning |
| S15 | Onboard Storage | 1/2 (enable/disable), 9 (dump), 11 (delete), 13 (status) | Manage HK/Event/Science/Alarm stores (IDs 1-4) |
| S17 | Connection Test | 1 (ping) | Verify bidirectional link at every pass startup |
| S19 | Event-Action | 1 (add), 2 (delete), 4/5 (enable/disable) | Approve FDIR event-action rules during COM-008 |
| S20 | Parameter Management | 1 (set), 3 (get) | Direct parameter reads for anomaly investigation |

### 3.2 Critical Commands Requiring FD Authorisation

These commands have `criticality: critical` or `criticality: caution` in the TC catalog and must not be sent without FD approval:

| Command | Service | Func ID | Criticality | Impact |
|---|---|---|---|---|
| OBC_REBOOT | S8.1 | 42 | Critical | Forces OBC to boot loader; loses onboard schedule, active HK |
| OBC_SWITCH_UNIT | S8.1 | 43 | Critical | Switches to redundant OBC; config differences possible |
| OBC_SELECT_BUS | S8.1 | 44 | Caution | Switches CAN bus; momentary subsystem communication loss |
| OBC_BOOT_APP | S8.1 | 45 | Caution | Boots application software from boot loader |
| DELETE_ALL_SCHEDULED | S11.11 | -- | Caution | Deletes all onboard scheduled commands |
| DELETE_STORE | S15.11 | -- | Caution | Deletes entire contents of a TM store |

### 3.3 FD-Specific Command Sequences

#### 3.3.1 Pass Startup Verification Sequence
```
S17.1  CONNECTION_TEST                    -- Verify link
S3.27  HK_REQUEST(sid=4)                 -- OBDH first (mode check)
S3.27  HK_REQUEST(sid=1)                 -- EPS (power budget)
S3.27  HK_REQUEST(sid=2)                 -- AOCS (pointing)
S3.27  HK_REQUEST(sid=3)                 -- TCS (thermal)
S3.27  HK_REQUEST(sid=5)                 -- Payload
S3.27  HK_REQUEST(sid=6)                 -- TTC (link quality)
```

#### 3.3.2 Emergency Safe Mode Entry Sequence
```
S8.1   OBC_SET_MODE(mode=2)              -- func_id 40, EMERGENCY mode
S3.27  HK_REQUEST(sid=1)                 -- Verify load shedding
S8.1   PAYLOAD_SET_MODE(mode=0)          -- func_id 20, payload OFF (if needed)
S8.1   HEATER_CONTROL(circuit=obc, off)  -- func_id 31 (if needed)
S8.1   AOCS_SET_MODE(mode=2)             -- func_id 0, SAFE_POINT
S3.27  HK_REQUEST(sid=2)                 -- Verify AOCS safe-pointing
```

#### 3.3.3 Shift Handover Telemetry Sweep
```
S3.27  HK_REQUEST(sid=1) through (sid=6) -- All subsystem HK
S11.17 LIST_SCHEDULE                      -- Onboard command schedule
```

### 3.4 Telemetry Monitoring Requirements

The FD must have continuous monitoring of the following pass-level decision parameters:

| Parameter | Param ID | Pass GO Threshold | Red Limit |
|---|---|---|---|
| eps.bat_soc | 0x0101 | > 50% | < 15% |
| eps.bus_voltage | 0x0105 | > 27.0V | < 26.5V |
| eps.power_gen - eps.power_cons | 0x0107 - 0x0106 | > 0 W (sunlit) | N/A |
| aocs.att_error | 0x0217 | < 1.0 deg | > 2.0 deg |
| aocs.rate_roll/pitch/yaw | 0x0204-0x0206 | < 0.05 deg/s | > 2.0 deg/s |
| obdh.mode | 0x0300 | = 0 (NOMINAL) | = 2 (EMERGENCY) |
| obdh.cpu_load | 0x0302 | < 80% | > 98% |
| obdh.reboot_count | 0x030A | stable | > 5 |
| ttc.link_status | 0x0501 | = 1 (LOCKED) | = 0 (UNLOCKED) |
| ttc.rssi | 0x0502 | > -100 dBm | N/A |
| ttc.link_margin | 0x0503 | > 3.0 dB | N/A |
| payload.mode | 0x0600 | as planned | N/A |
| tcs.temp_battery | 0x0407 | 0-40 C | < 0 C or > 45 C |

---

## 4. Operational Procedures

### 4.1 Procedure Coverage Matrix

The Flight Director is listed as a `required_position` in the following procedures from `configs/eosat1/procedures/procedure_index.yaml`:

#### 4.1.1 LEOP Procedures (7 total)

| ID | Name | FD Role | Other Positions | PUS Services |
|---|---|---|---|---|
| LEOP-001 | First Acquisition of Signal | Authorize pass start, GO/NO-GO decisions | ttc | 8, 9, 17 |
| LEOP-002 | Initial Health Check | Coordinate checkout sequence | eps_tcs, fdir_systems | 3, 5, 8, 17 |
| LEOP-003 | Initial Orbit Determination | Approve orbit solution | aocs, ttc | 8, 9 |
| LEOP-004 | Solar Array Verification | Authorize deployment verification | eps_tcs | 8 |
| LEOP-005 | Sun Acquisition | Authorize attitude maneuver | aocs | 8 |
| LEOP-006 | Time Synchronisation | Execute time sync, verify accuracy | (FD only) | 9 |
| LEOP-007 | LEOP Summary Checkout | Review all subsystem health before commissioning | (FD only) | 3, 5 |

#### 4.1.2 Commissioning Procedures (12 total)

| ID | Name | FD Role | Other Positions | PUS Services |
|---|---|---|---|---|
| COM-001 | EPS Checkout | Authorize power line switching | eps_tcs | 3, 8, 20 |
| COM-002 | TCS Verification | Authorize heater activation | eps_tcs | 3, 8, 20 |
| COM-003 | AOCS Sensor Calibration | Authorize calibration sequence | aocs | 3, 8, 20 |
| COM-004 | AOCS Actuator Checkout | Authorize actuator tests | aocs | 3, 8, 20 |
| COM-005 | AOCS Mode Transitions | Authorize mode changes | aocs | 3, 8, 20 |
| COM-006 | TTC Link Verification | Coordinate link test | ttc | 3, 8, 17, 20 |
| COM-007 | OBDH Checkout | Authorize OBC tests | fdir_systems | 3, 6, 8, 20 |
| COM-008 | FDIR Configuration | Approve FDIR configuration | fdir_systems | 3, 5, 8, 12, 19, 20 |
| COM-009 | Payload Power On | Authorize payload activation | payload_ops, eps_tcs | 3, 8, 15, 20 |
| COM-010 | FPA Cooler Activation | Authorize cooler start | payload_ops, eps_tcs | 3, 8, 20 |
| COM-011 | Payload Calibration | Authorize calibration | payload_ops | 3, 8, 15, 20 |
| COM-012 | First Light | Authorize first imaging | payload_ops, aocs | 3, 8, 11, 15, 20 |

#### 4.1.3 Nominal Procedures (FD-relevant, 5 of 12)

| ID | Name | FD Role | Other Positions | PUS Services |
|---|---|---|---|---|
| NOM-001 | Pass Startup | Authorize pass start, initial GO/NO-GO | ttc | 3, 8, 17 |
| NOM-006 | Software Upload | Authorize upload, GO/NO-GO at each stage | fdir_systems | 6, 8 |
| NOM-008 | Clock Synchronisation | Verify time delta threshold | fdir_systems | 9 |
| NOM-009 | Routine Health Check | Review all subsystem parameters | (FD only) | 3, 5 |
| NOM-012 | Shift Handover | Conduct handover briefing and log | (FD only) | -- |

#### 4.1.4 Contingency Procedures (FD-relevant, 17 of 18)

| ID | Name | FD Role | Key Decision |
|---|---|---|---|
| CTG-001 | Under-Voltage Load Shed | Authorize load shedding | Load shed priority order |
| CTG-002 | AOCS Anomaly Recovery | Authorize recovery actions | Mode fallback selection |
| CTG-003 | TTC Link Loss Recovery | Coordinate link recovery | Primary/redundant selection |
| CTG-004 | Thermal Exceedance | Authorize thermal response | Payload safing decision |
| CTG-005 | EPS Safe Mode | Authorize EPS recovery | Power restoration sequence |
| CTG-006 | Payload Anomaly | Authorize payload response | Power-off vs. restart |
| CTG-007 | Reaction Wheel Anomaly | Authorize RW recovery | 3-wheel mode approval |
| CTG-008 | Star Tracker Failure | Authorize ST recovery | Redundant unit switch |
| CTG-009 | Solar Array Degradation | Authorize power budget adjustment | Reduced operations plan |
| CTG-010 | OBDH Watchdog Recovery | Authorize OBC recovery | Reboot vs. mode change |
| CTG-011 | OBC Redundancy Switchover | Authorize OBC switchover | Critical: OBC_SWITCH_UNIT |
| CTG-012 | Overcurrent Response | Authorize power line reset | Re-enable vs. isolate |
| CTG-013 | Battery Cell Failure | Authorize battery management | Charge limit adjustment |
| CTG-014 | BER Anomaly | Authorize link reconfiguration | Data rate/power change |
| CTG-016 | Memory Segment Failure | Authorize memory management | Segment remap |
| CTG-017 | Bus Failure Switchover | Authorize bus switchover | CAN bus A/B selection |
| CTG-018 | Boot Loader Recovery | Authorize boot recovery | Boot app vs. inhibit |

#### 4.1.5 Emergency Procedures (6 total, all FD-led)

| ID | Name | FD Role | Autonomy Level |
|---|---|---|---|
| EMG-001 | Emergency Safe Mode | Command immediate safe mode entry | FD sole authority |
| EMG-002 | Total Power Failure | Coordinate emergency response | FD + eps_tcs |
| EMG-003 | OBC Reboot | Authorize emergency reboot | FD + fdir_systems |
| EMG-004 | Loss of Communication | Coordinate comms recovery | FD + ttc |
| EMG-005 | Loss of Attitude | Authorize emergency attitude recovery | FD + aocs |
| EMG-006 | Thermal Runaway | Coordinate thermal emergency | FD + eps_tcs + payload_ops |

### 4.2 Missing Procedures (Gaps Identified)

The following operational scenarios lack dedicated procedures and should be developed:

| Gap | Description | Recommendation |
|---|---|---|
| LEOP-to-Commissioning Transition | No formal phase transition procedure beyond LEOP-007 | Create FD-led transition checklist with GO/NO-GO for each subsystem's commissioning readiness |
| Dual Ground Station Handover | No procedure for mid-pass handover between Iqaluit and Troll | Create CTG procedure for ground station failover |
| Pass Closure | No formal pass-end procedure (complement to NOM-001) | Create NOM procedure for pass closeout, data archival, log completion |
| Conjunction Assessment | No COLA procedure (note: EOSAT-1 has no propulsion for evasive maneuvers) | Monitor conjunction predictions; passive avoidance only |
| Decommissioning | Referenced in mission phases but no procedures exist | Future development for end-of-life |

---

## 5. Training Scenarios

### 5.1 Existing Scenarios (from `configs/eosat1/scenarios/`)

The following 15 training scenarios are available, mapped to FD-relevant learning objectives:

#### 5.1.1 Basic (BEGINNER)

| Scenario File | Name | FD Training Focus |
|---|---|---|
| `nominal_ops.yaml` | Nominal Operations | Pass flow, eclipse monitoring, AOS/LOS recognition |
| `payload_corrupt_image.yaml` | Image Corruption During Capture | Anomaly detection, payload safing decision |

#### 5.1.2 Intermediate

| Scenario File | Name | FD Training Focus |
|---|---|---|
| `eps_anomaly.yaml` | Solar Array Degradation | Power budget assessment, load shed authorisation |
| `eps_overcurrent.yaml` | LCL Overcurrent Trip | Overcurrent isolation, line re-enable decision |
| `eps_undervoltage.yaml` | Battery Depletion During Eclipse | Eclipse power management, safe mode decision |
| `obc_watchdog.yaml` | OBC Watchdog Reset | Reboot detection, mode recovery authorisation |
| `ttc_pa_overheat.yaml` | PA Overheat and Auto-Shutdown | Thermal/comms coordination, TX power management |
| `payload_memory_failure.yaml` | Payload Memory Segment Corruption | Storage management, imaging plan adjustment |

#### 5.1.3 Advanced

| Scenario File | Name | FD Training Focus |
|---|---|---|
| `transponder_failure.yaml` | Transponder Primary Failure | Comms recovery coordination, redundancy switch |
| `aocs_wheel_failure.yaml` | Reaction Wheel Seizure | 3-wheel mode authorisation, pointing degradation |
| `aocs_star_tracker_failure.yaml` | Star Tracker Failure | Attitude degradation assessment, ST redundancy |
| `obc_crash.yaml` | OBC Watchdog Reset to Boot Loader | Boot loader recovery, OBC_BOOT_APP authorisation |
| `obc_bus_failure.yaml` | CAN Bus A Failure | Bus switchover authorisation, subsystem recovery |

### 5.2 Missing Training Scenarios (Required)

The following FD-specific scenarios should be developed:

| Priority | Scenario | Difficulty | Injection Description | FD Learning Objective |
|---|---|---|---|---|
| **HIGH** | LEOP First Acquisition | ADVANCED | Simulate separation, first contact window with timing uncertainty | Pass timing, GO/NO-GO at every step, multi-position coordination |
| **HIGH** | Multi-Failure Cascade | ADVANCED | EPS undervoltage + AOCS anomaly simultaneously | Emergency safe mode decision, triage across subsystems |
| **HIGH** | Loss of Communication | ADVANCED | Both transponders fail; blind commanding required | EMG-004 execution, ground station coordination, wait-and-retry |
| **HIGH** | Total Power Failure Recovery | ADVANCED | Battery depletion to < 15%; FDIR triggers emergency | EMG-002 recovery timeline, orbit-by-orbit recovery gates |
| **MEDIUM** | Shift Handover with Active Anomaly | INTERMEDIATE | Anomaly injected during handover procedure | Authority transfer protocol, anomaly disposition |
| **MEDIUM** | Full LEOP Sequence | ADVANCED | End-to-end LEOP-001 through LEOP-007 | Sequential procedure execution, phase transition GO/NO-GO |
| **MEDIUM** | Ground Station Priority Conflict | INTERMEDIATE | Two stations visible simultaneously with conflicting pass plans | Dual-GS coordination, pass priority assignment |
| **LOW** | Software Upload Failure | INTERMEDIATE | Memory CRC failure during software upload | NOM-006 abort/retry, memory verification |
| **LOW** | Thermal Runaway | ADVANCED | Multiple heater failures causing temperature exceedance | EMG-006 coordination across payload, thermal, power |

### 5.3 Scenario Configuration Requirements

Each scenario file uses the following structure (from existing YAML files):

```yaml
name: "Scenario Name"
difficulty: BASIC | INTERMEDIATE | ADVANCED
duration_s: <integer>
briefing: |
  Multi-line briefing text for the trainee.
events:
  - time_offset_s: <integer>
    action: inject
    params:
      subsystem: <eps|aocs|tcs|obdh|ttc|payload>
      failure: <failure_type>
      magnitude: <float>
      onset: step | gradual
      onset_duration_s: <integer>  # if gradual
expected_responses:
  - { category: detect, description: "..." }
  - { category: isolate, description: "..." }
  - { category: recover, description: "..." }
```

**REQ-SCN-001:** All FD training scenarios must include expected_responses with detect, isolate, and recover categories.

**REQ-SCN-002:** Multi-failure scenarios must inject events with staggered time_offset_s to test prioritisation.

**REQ-SCN-003:** Each scenario must reference the applicable procedure IDs from the procedure index so that trainee can practice procedure look-up.

---

## 6. MCS Display and Tool Requirements

### 6.1 Overview Tab Requirements

The FD Overview tab (as configured in `configs/eosat1/mcs/displays.yaml` under `flight_director`) must display:

| Widget Type | Parameter(s) | Requirement |
|---|---|---|
| Gauge | eps.bat_soc (0-100%) | Real-time battery state |
| Status Indicator | ttc.link_status | Green/red link state with WCAG AA |
| Gauge | aocs.att_error (0-10 deg) | Attitude accuracy |
| Gauge | obdh.cpu_load (0-100%) | OBC health |
| Status Indicator | obdh.sw_image | Boot loader vs. application |
| Value Table | bus_voltage, aocs.mode, obdh.mode, ttc.link_margin, payload.mode | Multi-parameter summary |

**REQ-DSP-001:** The FD Overview tab must include a world map showing current spacecraft position, ground track (past 50 min and future 50 min), ground station locations, and contact windows. This is currently implemented via Leaflet with the planner's `offset_minutes` parameter.

**REQ-DSP-002:** The FD Overview must display the current simulation time (`sim_time`), simulation speed factor, and tick count prominently.

**REQ-DSP-003:** The FD Overview must show an event log filtered to show all subsystem events (no per-subsystem filtering for FD).

**REQ-DSP-004:** The FD must have access to the full SVG block diagrams for all subsystems: EPS power distribution, TCS thermal map, AOCS equipment schematic, TTC RF signal chain, OBDH dual-OBC, and Overview spacecraft bus.

### 6.2 Commanding Tab Requirements

**REQ-DSP-005:** The Commanding tab must display the full TC catalog with all commands visible (no position-based filtering for `allowed_commands: "all"`).

**REQ-DSP-006:** The PUS Command Builder must support structured field entry via `PUS_FIELD_DEFS` with dynamic forms and auto-packing for all 12 PUS services.

**REQ-DSP-007:** PUS subtype validation (`VALID_PUS_SUBTYPES`) must prevent invalid service/subtype combinations before transmission.

**REQ-DSP-008:** The Verification Log must display the last 200 commands with states (SENT, ACCEPTED, REJECTED, COMPLETED, FAILED) and error codes.

### 6.3 Procedure Tab Requirements

**REQ-DSP-009:** The Procedures tab must support browsing all 54 procedures across 5 categories (leop/commissioning/nominal/contingency/emergency) via the procedure index at `/api/procedure/index`.

**REQ-DSP-010:** The procedure runner must support step-by-step execution with pause, resume, abort, skip, and override-command capabilities.

**REQ-DSP-011:** The procedure builder must allow FD to create custom procedures and save them to `configs/eosat1/procedures/custom/` via `/api/procedure/save`.

### 6.4 GO/NO-GO Panel Requirements

**REQ-DSP-012:** The FD position must have a dedicated GO/NO-GO panel with:
- "Initiate Poll" button (FD-only, enforced server-side at `_handle_go_nogo_poll`)
- Configurable poll label field
- Real-time response grid showing all 6 positions with GO/NOGO/STANDBY states
- Visual distinction: green (GO), red (NO-GO), yellow (STANDBY), grey (not responded)
- Auto-close when all positions have responded
- Result broadcast: ALL_GO or NO_GO via WebSocket `go_nogo_result` message

**REQ-DSP-013:** GO/NO-GO responses must be receivable via both the REST endpoint (`/api/go-nogo/respond`) and WebSocket (`go_nogo_response` message type).

### 6.5 Shift Handover Panel Requirements

**REQ-DSP-014:** The MCS must provide a shift handover log accessible via `/api/handover` with:
- Timestamped notes with position attribution
- Full shift history retained during session
- Ability for any position to add notes but FD to review all

### 6.6 Chart Requirements

**REQ-DSP-015:** The FD must have access to the following time-series charts (via Chart.js):
- Battery SoC trend (3-hour window, SID 1 at 1 Hz)
- Power balance (generation vs. consumption, 10-minute window)
- Body rates (roll/pitch/yaw, 5-minute window)
- Attitude error (10-minute window)
- CPU load trend (10-minute window)
- RSSI and link margin (10-minute window)
- PA temperature (10-minute window)
- Wheel speeds (10-minute window)
- Panel and component temperatures (30-minute window)

### 6.7 Accessibility Requirements

**REQ-DSP-016:** All MCS displays must maintain WCAG 2.1 AA compliance:
- Skip links for keyboard navigation
- Semantic heading hierarchy (h1/h2)
- Color contrast ratios meeting AA standards
- LED status indicators with role="status", aria-labels, and border-style differentiation
- Keyboard help dialog (? key, F6 for tab navigation, arrow keys, Escape)

---

## 7. Planner Requirements

### 7.1 Contact Window Prediction

The planner (`packages/smo-planner/src/smo_planner/`) must provide:

**REQ-PLN-001:** Compute contact windows for all 4 ground stations (Svalbard, Troll, Inuvik, O'Higgins) for the next 24 hours using SGP4 propagation with 10-second step resolution.

**REQ-PLN-002:** For each contact window, provide:
- AOS/LOS times (ISO 8601 UTC)
- Ground station name
- Maximum elevation angle
- Duration
- Overlap detection with other ground station windows

**REQ-PLN-003:** Contact windows must be auto-recomputed every 10 minutes (current implementation in `PlannerServer.start()`).

### 7.2 Activity Scheduling

**REQ-PLN-004:** The planner must support 6 activity types as defined in `configs/eosat1/planning/activity_types.yaml`:
- `imaging_pass` (120s, 60W, requires daylight + nominal attitude)
- `data_dump` (600s, 25W, requires contact)
- `calibration` (180s, 35W, requires daylight)
- `housekeeping_collection` (60s, 5W, no constraints)
- `software_upload` (1200s, 15W, requires contact + SoC > 60%)
- `momentum_desaturation` (600s, 30W, conflicts with imaging)

**REQ-PLN-005:** Activity scheduling must enforce:
- Conflict detection between mutually exclusive activities (e.g., imaging vs. momentum desaturation)
- Pre-condition validation (power budget, attitude mode, link status)
- State lifecycle tracking: PLANNED -> VALIDATED -> UPLOADED -> EXECUTING -> COMPLETED/FAILED/CANCELLED
- Procedure reference linking (`procedure_ref` field)

**REQ-PLN-006:** The planner must support uploading activity command sequences to the MCS procedure runner via `/api/procedure/load` for real-time execution.

### 7.3 Ground Track Prediction

**REQ-PLN-007:** The planner must provide ground track prediction for the next 3 hours (approximately 2 orbits) with 30-second resolution, accessible via `/api/ground-track`.

**REQ-PLN-008:** The ground track API must support `offset_minutes` parameter for past track display (used by the MCS Overview map at +/-50 minutes).

### 7.4 Spacecraft State

**REQ-PLN-009:** The planner must provide real-time spacecraft state via `/api/spacecraft-state` including:
- Latitude, longitude, altitude
- Eclipse state
- Contact state (in_contact)
- Heading (from velocity vector)
- Solar beta angle
- Ground station elevation and range (when in contact)

### 7.5 FD-Specific Planner Requirements

**REQ-PLN-010:** The FD requires a pass plan overview showing:
- Timeline view of all contact windows in the next 24 hours
- Scheduled activities overlaid on contact windows
- Visual indication of which activities require contact (data_dump, software_upload) and which do not
- Pass-level power budget projection (total power_w of scheduled activities vs. available power)

**REQ-PLN-011:** The planner must provide a schedule validation endpoint (`/api/schedule/validate`) that checks for:
- Activity-contact conflicts (contact-required activities scheduled outside contact)
- Mutual exclusion conflicts
- Power budget exceedance during concurrent activities
- Pre-condition failures

---

## 8. Simulator Fidelity Requirements

### 8.1 PUS Service Fidelity

The simulator (`packages/smo-simulator/`) must faithfully implement the following PUS services for FD training:

| Service | Current Coverage | FD Requirement |
|---|---|---|
| S1 (Verification) | Acceptance (1.1/1.2) and completion (1.7/1.8) | Full chain including S1 error codes for each rejection reason |
| S3 (Housekeeping) | SIDs 1-6, 10 with configurable intervals | All 7 SIDs must generate correctly formatted HK packets |
| S5 (Events) | Event generation by severity; S5.7/S5.8 (enable/disable all) | All 27 event IDs from `event_catalog.yaml` must fire under correct conditions |
| S6 (Memory) | Load, dump, check | Memory load must support CRC verification failure injection |
| S8 (Functions) | All func_ids 0-55 dispatched | All commands must produce observable state changes |
| S9 (Time) | Set and request time | Must support time drift simulation for clock sync training |
| S11 (Scheduling) | Schedule, delete, list | Schedule must execute commands at specified CUC times |
| S12 (Monitoring) | S12.12 (report), S12.9/12.10 transitions | Out-of-limit events must trigger based on configurable thresholds |
| S15 (Storage) | Enable, disable, dump, delete, status | Store fill levels must be observable; dump must produce TM packets |
| S17 (Connection) | Echo response | Must respond within realistic latency |
| S19 (Event-Action) | S19.8 (report); add/delete/enable/disable | Event-action rules must execute autonomously when events fire |
| S20 (Parameters) | Set and get | Must support all 200+ parameters in `parameters.yaml` |

### 8.2 Subsystem Model Fidelity

**REQ-SIM-001:** EPS model must simulate:
- Solar array current as a function of sun angle and eclipse state
- Battery charge/discharge cycle with SoC tracking
- Bus voltage regulation with undervoltage/overvoltage behaviour
- Per-line overcurrent detection and LCL tripping
- Power balance (generation minus consumption)
- Solar array degradation (gradual, as in `eps_anomaly.yaml`)
- Battery DoD tracking and cycle counting

**REQ-SIM-002:** AOCS model must simulate:
- AOCS mode transitions: OFF -> SAFE_BOOT -> DETUMBLE -> COARSE_SUN -> NOMINAL_NADIR -> FINE_POINT -> SLEW -> DESATURATION -> ECLIPSE_PROPAGATE
- Body rate damping in detumble mode (B-dot algorithm)
- Attitude error convergence in pointing modes
- Reaction wheel speed dynamics with momentum accumulation
- Wheel failure injection (bearing seizure, speed anomaly)
- Star tracker failure injection (blind, hardware fail)
- Desaturation using magnetorquers
- Attitude error increase when sensors fail

**REQ-SIM-003:** OBDH model must simulate:
- OBC mode states: NOMINAL (0), SAFE (1), EMERGENCY (2)
- CPU load variation with task scheduling
- Watchdog reset triggering and reboot to boot loader
- Dual OBC (A/B) with switchover capability
- CAN Bus A/B with failure injection and switchover
- Reboot counter tracking
- Boot loader mode with minimal HK (SID 10 only)
- Memory scrub and SEU injection

**REQ-SIM-004:** TTC model must simulate:
- RF link budget (RSSI, link margin, Eb/N0) as a function of elevation and range
- Power amplifier thermal model with auto-shutdown at 70 C
- Primary/redundant transponder switching
- Data rate change (1 kbps / 64 kbps)
- BER variation with link quality
- Carrier lock / bit sync / frame sync sequence
- Transponder failure injection

**REQ-SIM-005:** TCS model must simulate:
- Thermal transients during eclipse entry/exit
- Heater thermostat control with configurable setpoints
- FPA cooler operation
- Temperature response to power dissipation changes
- Heater failure injection

**REQ-SIM-006:** Payload model must simulate:
- Payload mode transitions: OFF -> STANDBY -> IMAGING
- FPA temperature response to cooler operation
- Image capture with scene ID tracking and storage consumption
- Checksum error injection
- Memory segment failure injection
- Storage capacity management (20 GB total, segment-based)

### 8.3 FDIR Model Fidelity

**REQ-SIM-007:** The simulator must implement all 12 FDIR rules from `configs/eosat1/subsystems/fdir.yaml`:

| Rule | Trigger | Action |
|---|---|---|
| eps.bat_soc < 20% | Level 1 | payload_poweroff |
| eps.bat_soc < 15% | Level 2 | safe_mode_eps |
| eps.bus_voltage < 26V | Level 2 | safe_mode_eps |
| tcs.temp_battery > 42 C | Level 1 | heater_off_battery |
| tcs.temp_battery < 1 C | Level 1 | heater_on_battery |
| aocs.att_error > 5 deg | Level 2 | safe_mode_aocs |
| obdh.temp_obc > 65 C | Level 2 | safe_mode_obc |
| obdh.reboot_count > 4 | Level 3 | spacecraft_emergency |
| aocs.rw1_temp through rw4_temp > 65 C | Level 1 | disable_rw1..4 |

**REQ-SIM-008:** FDIR actions must generate S5 events that are visible to the MCS event display.

### 8.4 Failure Injection

**REQ-SIM-009:** The simulator must support the following failure injection types (from scenario definitions):

| Failure Type | Subsystem | Parameters |
|---|---|---|
| solar_array_partial | eps | magnitude, onset (step/gradual), onset_duration_s, array (A/B) |
| undervoltage | eps | magnitude, onset, onset_duration_s |
| overcurrent | eps | magnitude, onset, line_index |
| cpu_spike | obdh | load (%) |
| watchdog_reset | obdh | (triggers reboot) |
| obc_crash | obdh | magnitude, onset (step) |
| bus_failure | obdh | magnitude, onset, bus (A/B) |
| primary_failure | ttc | magnitude |
| pa_overheat | ttc | magnitude, onset, onset_duration_s |
| wheel_failure | aocs | magnitude, onset, onset_duration_s, wheel (0-3) |
| st_failure | aocs | magnitude, onset, unit (1/2) |
| image_corrupt | payload | magnitude, onset, count |
| memory_segment_fail | payload | magnitude, onset, segment (0-79) |

### 8.5 Orbit Model Fidelity

**REQ-SIM-010:** The orbit model must faithfully simulate:
- Sun-synchronous orbit at 500 km altitude, 97.4 deg inclination
- ~94.6 minute orbital period
- ~35 minute eclipse duration with seasonal variation
- Contact windows for all 4 ground stations with realistic elevation profiles
- Ground station handover timing
- Doppler shift on S-band frequencies

---

## 9. LEOP / Separation Timeline Requirements

### 9.1 Pre-Separation Timeline

| Time Relative to Sep | Event | FD Action |
|---|---|---|
| Sep - 4 h | Launch window opens | Confirm MCS staffed, all positions nominal |
| Sep - 2 h | All systems GO poll | Initiate GO/NO-GO for launch readiness |
| Sep - 1 h | Final trajectory update | Verify TLE loaded into planner |
| Sep - 30 min | Ground station AOS prediction | Confirm Svalbard/Iqaluit antenna scheduled |
| Sep - 10 min | Launch vehicle telemetry | Monitor via launch provider feed |
| Sep + 0 s | **SEPARATION** | Start LEOP clock |

### 9.2 Post-Separation LEOP Timeline

| Time | Event | Procedure | FD Decision Point |
|---|---|---|---|
| Sep + 0 s | Separation confirmed | -- | Start LEOP timeline |
| Sep + 0 to +30 s | Tip-off rates acquired | -- | (Autonomous: AOCS boots to DETUMBLE) |
| Sep + ~20 min | Predicted first AOS (Svalbard/Iqaluit) | LEOP-001 | GO/NO-GO for acquisition attempt |
| Sep + ~22 min | Carrier lock achieved | LEOP-001 Step 3 | GO/NO-GO: stable carrier lock |
| Sep + ~23 min | Uplink established | LEOP-001 Step 4 | GO/NO-GO: bidirectional link |
| Sep + ~24 min | Time synchronisation | LEOP-001 Step 5 / LEOP-006 | Verify S9.1 accepted |
| Sep + ~25 min | First HK received | LEOP-001 Step 6 | GO/NO-GO: telemetry valid |
| Sep + ~28 min | Full health check | LEOP-002 | GO/NO-GO: each subsystem |
| Sep + ~35 min | Solar array verification | LEOP-004 | GO/NO-GO: deployment confirmed |
| Sep + ~40 min | LOS (first pass ends) | -- | Log pass, prepare for next |
| Orbit 2-3 | Rate damping monitored | LEOP-005 | GO/NO-GO: rates < 0.5 deg/s |
| Orbit 3-4 | Safe_point transition | LEOP-005 Step 6 | GO/NO-GO: sun pointing achieved |
| Orbit 4-5 | Orbit determination | LEOP-003 | Approve orbit solution |
| Orbit 5-8 | Stabilisation and monitoring | LEOP-007 | Monitor all subsystems |
| ~Day 3 | LEOP-to-Commissioning transition | LEOP-007 | **Phase transition GO/NO-GO** |

### 9.3 LEOP Contact Strategy

**REQ-LEOP-001:** During LEOP, all available ground stations must be scheduled for maximum contact coverage. The first contact at the highest-latitude station (Svalbard at 78.2 N or Iqaluit at 63.7 N) should occur approximately 20-30 minutes after separation, depending on injection orbit.

**REQ-LEOP-002:** LEOP contact windows must be computed with a wider AOS tolerance (AOS -5 min) to account for launcher injection uncertainty.

**REQ-LEOP-003:** The FD must have a LEOP timeline display showing:
- Elapsed time since separation
- Current orbit number
- Predicted next AOS/LOS for all ground stations
- Procedure completion status (LEOP-001 through LEOP-007)
- Subsystem health traffic-light summary (green/yellow/red per subsystem)

### 9.4 LEOP GO/NO-GO Gates

| Gate | Criteria | Fall-Back |
|---|---|---|
| First Acquisition | Signal detected within AOS +5 min, carrier lock, uplink accepted | Search mode +/- 100 kHz; alert alternate stations |
| Time Sync | S9.1 accepted, time delta < 1 s | Retry on next pass |
| Health Check | All 6 SIDs received, all params within post-separation limits | Defer non-essential ops; prioritise sun acquisition |
| Solar Arrays | Both arrays generating > 0.5A; power_gen > 80W | Verify deployment mechanism; single-array contingency |
| Sun Acquisition | Rates < 0.1 deg/s, att_error < 10 deg, power positive | Remain in DETUMBLE; retry on next sunlit pass |
| Orbit Solution | Orbit determination accuracy within 1 km | Continue with launcher TLE; refine on subsequent passes |
| LEOP-to-Commissioning | All subsystems nominal, attitude stable, power positive, link margin > 3 dB | Extend LEOP; address open items |

---

## 10. Pass Plan Visualization Requirements

### 10.1 Timeline View

**REQ-VIZ-001:** The MCS must provide a Gantt-style pass timeline showing:
- Horizontal axis: UTC time spanning the next 24 hours
- Rows: One row per ground station (Svalbard, Troll, Inuvik, O'Higgins)
- Contact windows displayed as coloured bars (green = available, blue = scheduled, grey = low elevation)
- Maximum elevation annotation on each bar
- Current time indicator (vertical red line)

**REQ-VIZ-002:** Scheduled activities must be displayed as overlaid blocks on the relevant contact window with:
- Colour coding by activity type (imaging = blue, data dump = orange, calibration = yellow, HK collection = green, software upload = purple, momentum desat = cyan)
- Activity name label
- Duration bar proportional to actual duration
- State indicator (PLANNED/VALIDATED/UPLOADED/EXECUTING/COMPLETED/FAILED)

### 10.2 World Map View

**REQ-VIZ-003:** The Overview tab world map (Leaflet) must display:
- Current spacecraft position (icon with heading)
- Ground track: past 50 minutes (dashed) and future 50 minutes (solid)
- Ground station markers at Svalbard (78.2N, 15.4E), Troll (72.0S, 2.5E), Inuvik (68.3N, 133.5W), O'Higgins (63.3S, 57.9W)
- Ground station visibility circles (5 deg minimum elevation)
- Eclipse indicator (day/night terminator or spacecraft icon change)
- Contact state highlighting (green border on active GS, grey on idle)

Note: The mission-specified ground stations are Iqaluit (63.747N, 68.518W) and Troll (72.012S, 2.535E), but the codebase currently configures Svalbard, Troll, Inuvik, and O'Higgins in `configs/eosat1/orbit.yaml` and `configs/eosat1/planning/ground_stations.yaml`. The simulator should be updated to use the mission-specified stations, or both sets should be supported.

### 10.3 Contact Detail Panel

**REQ-VIZ-004:** Clicking a contact window must display:
- Ground station name and coordinates
- AOS/LOS times (UTC)
- Duration
- Maximum elevation and time of closest approach (TCA)
- Scheduled activities within this window
- Link budget estimate (RSSI, margin) based on elevation profile
- Next contact at any station after this window's LOS

---

## 11. Dual Ground Station Coordination Model

### 11.1 Ground Station Configuration

The mission specifies two primary ground stations:

| Station | Latitude | Longitude | Antenna | Role |
|---|---|---|---|---|
| Iqaluit | 63.747 N | 68.518 W | S-band | Primary TT&C + Data Dump |
| Troll | 72.012 S | 2.535 E | 7.3m S-band | Secondary TT&C |

The simulator codebase additionally includes Svalbard (78.2N) and Inuvik (68.3N) from `orbit.yaml`. For operational fidelity, the coordination model should address all configured stations.

### 11.2 Coordination Requirements

**REQ-GS-001:** The FD must be able to designate a "primary" ground station for each pass and assign activities accordingly.

**REQ-GS-002:** When multiple ground stations have overlapping visibility windows, the system must:
- Display overlap periods on the timeline view
- Allow the FD to assign a specific station for commanding (uplink)
- Allow simultaneous TM reception from multiple stations (diversity combining)

**REQ-GS-003:** Ground station priority rules:
- For LEOP: Use highest-latitude station first (maximum contact duration)
- For nominal ops: Alternate based on data volume and link margin
- For contingency: All stations on standby for maximum coverage

**REQ-GS-004:** The planner must compute inter-station gap analysis:
- Maximum gap between consecutive contacts across all stations
- Worst-case gap for autonomous operations planning
- Gap reduction benefit of adding stations

### 11.3 Handover Protocol

**REQ-GS-005:** When handing over between ground stations mid-orbit (e.g., LOS at Iqaluit, AOS at Troll 20 minutes later):
- FD must issue pass closure on outgoing station
- Verify last TC verification status
- Confirm handover notes updated
- Issue pass startup on incoming station (NOM-001)

---

## 12. Alarm Escalation Workflow

### 12.1 Alarm Severity Levels

Based on the event catalog (`configs/eosat1/events/event_catalog.yaml`) and limit definitions (`configs/eosat1/mcs/limits.yaml`):

| Level | Colour | Source | FD Action Required |
|---|---|---|---|
| INFO | White/Blue | Orbital events (AOS/LOS, eclipse entry/exit), mode changes | Monitor, no action |
| LOW | Yellow | Solar array degradation, payload mode changes, storage warning, FPA temp warning | Monitor, assess trend |
| MEDIUM | Orange | Under-voltage, overcurrent, attitude warning, OBDH mode change, temperature exceedance, memory error, wheel disabled | Investigate, coordinate with position operator |
| HIGH | Red | Critical SoC, bus undervoltage, OBC reboot, heater failure | Immediate response, authorise contingency procedure |

### 12.2 FDIR-Generated Alarms

The FDIR rules in `configs/eosat1/subsystems/fdir.yaml` generate autonomous actions at 3 severity levels:

| FDIR Level | Conditions | Autonomous Action | FD Notification Required |
|---|---|---|---|
| Level 1 | bat_soc < 20%, temp thresholds, RW over-temp | payload_poweroff, heater control, wheel disable | Yes -- FD must be notified of FDIR action and acknowledge |
| Level 2 | bat_soc < 15%, bus_voltage < 26V, att_error > 5 deg, OBC over-temp | safe_mode (EPS/AOCS/OBC) | Yes -- FD must authorise recovery from safe mode |
| Level 3 | reboot_count > 4 | spacecraft_emergency | Yes -- FD must declare emergency, execute EMG-001 |

### 12.3 Escalation Workflow

```
EVENT DETECTED (S5 or S12 transition)
    |
    v
MCS Limit Checker evaluates against limits.yaml
    |
    +-- Within limits --> INFO log only
    |
    +-- Yellow limit violated --> CAUTION
    |       |
    |       +-- Notify responsible position operator
    |       +-- Start trend monitoring
    |       +-- Log in alarm buffer (Store ID 4)
    |
    +-- Red limit violated --> WARNING
            |
            +-- [REQ-ALM-001] Audio alarm at FD console
            +-- [REQ-ALM-002] Visual alarm on FD Overview (flashing parameter)
            +-- [REQ-ALM-003] Auto-scroll event log to alarm entry
            +-- FD acknowledges alarm
            |
            +-- FDIR fires autonomous action
            |       |
            |       +-- [REQ-ALM-004] FDIR action notification on FD console
            |       +-- FD reviews autonomous action result
            |       +-- FD decides: accept FDIR action OR override
            |
            +-- FD initiates contingency/emergency procedure
                    |
                    +-- GO/NO-GO poll if time permits
                    +-- Direct commanding if time-critical
```

**REQ-ALM-005:** All alarms must remain visible until explicitly acknowledged by the FD.

**REQ-ALM-006:** The alarm buffer (`obdh.alarm_buf_fill`, param 0x0314) must be downloadable via S15.9 (DUMP_STORE, store_id=4) for post-pass analysis.

**REQ-ALM-007:** The MCS must support alarm history with timestamps, severity, parameter involved, and operator acknowledgement record.

---

## 13. Command Verification Gate Requirements

### 13.1 Verification Chain

Every command sent through the MCS must pass through the following verification gates:

```
COMMAND ENTRY (PUS Command Builder or Procedure Runner)
    |
    v
GATE 1: Position Access Control
    [server.py: _check_position_access()]
    - Verify position has service access (allowed_services)
    - Verify S8 func_id access (allowed_func_ids)
    - For FD: allowed_commands="all" => bypass
    |
    v
GATE 2: PUS Subtype Validation
    [index.html: VALID_PUS_SUBTYPES]
    - Verify service/subtype combination is valid
    - Reject invalid subtypes before sending
    |
    v
GATE 3: Critical Command Confirmation
    [REQ-VER-001] Commands with criticality: critical (OBC_REBOOT, OBC_SWITCH_UNIT)
    must display a confirmation dialog with:
    - Command name and parameters
    - Potential impact description
    - "Are you sure?" confirmation
    - FD position verification
    |
    v
GATE 4: Packet Construction
    [tc_manager.py: build_command()]
    - Build ECSS PUS TC packet with sequence counter
    - Track in verification log with state=SENT
    |
    v
GATE 5: TC Transmission
    [server.py: _handle_pus_command()]
    - Send framed packet over TCP to simulator
    - Return sequence number to operator
    |
    v
GATE 6: Acceptance Verification (S1.1/S1.2)
    - S1.1 (subtype=1): ACCEPTED => state = "ACCEPTED"
    - S1.2 (subtype=2): REJECTED => state = "REJECTED" + error_code
    |
    v
GATE 7: Execution Verification (S1.7/S1.8)
    - S1.7 (subtype=7): COMPLETED => state = "COMPLETED"
    - S1.8 (subtype=8): FAILED => state = "FAILED" + error_code
```

### 13.2 Verification Log Requirements

**REQ-VER-002:** The verification log (200-entry deque in `server.py`) must track:
- Sequence number (tc_seq)
- Command name (including "PROC:" prefix for procedure-generated commands)
- PUS service and subtype
- Verification state (SENT -> ACCEPTED/REJECTED -> COMPLETED/FAILED)
- Error code (if rejected or failed)
- Timestamp
- Originating position

**REQ-VER-003:** The verification log must be accessible via `/api/verification-log` and via WebSocket real-time updates (verification TM in `_process_tm`).

**REQ-VER-004:** The FD must be able to view all commands from all positions in the verification log (no position filtering for FD).

### 13.3 Procedure-Level Verification

**REQ-VER-005:** When a procedure is running via the procedure runner:
- Each step's command must appear in the verification log with the "PROC:" prefix
- The procedure runner must wait for S1.1 (acceptance) before advancing to the next step
- If S1.2 (rejection) is received, the procedure runner must pause and alert the FD
- The FD must be able to override or skip individual steps

### 13.4 FD-Specific Verification Requirements

**REQ-VER-006:** The FD console must display a "command pending" indicator whenever:
- A critical command is awaiting S1.1 acceptance
- A procedure step is awaiting verification
- A GO/NO-GO poll is active and awaiting responses

**REQ-VER-007:** The FD must receive a notification for every REJECTED (S1.2) or FAILED (S1.8) command from any position, not just their own commands.

---

## 14. GO/NO-GO Coordination Requirements

### 14.1 Poll Mechanics

The GO/NO-GO system is implemented in `server.py` with the following architecture:

| Component | Endpoint/Message | Direction |
|---|---|---|
| Initiate Poll | POST `/api/go-nogo/poll` | FD -> Server |
| Respond to Poll | POST `/api/go-nogo/respond` or WS `go_nogo_response` | Position -> Server |
| Status Query | GET `/api/go-nogo/status` | Any -> Server |
| Poll Broadcast | WS `go_nogo_poll` | Server -> All Clients |
| Status Broadcast | WS `go_nogo_status` | Server -> All Clients |
| Result Broadcast | WS `go_nogo_result` | Server -> All Clients |

### 14.2 Poll Types Required

**REQ-GNG-001:** The FD must be able to initiate the following poll types:

| Poll Label | When Used | Expected Responses |
|---|---|---|
| "Pass Startup GO/NO-GO" | NOM-001 Step 7 | All 6 positions |
| "LEOP Phase Gate" | LEOP-007 | All 6 positions |
| "Commissioning Complete" | COM-012 | All 6 positions |
| "Software Upload GO/NO-GO" | NOM-006 (each stage) | flight_director, fdir_systems |
| "Emergency Response" | EMG-001-006 | All available positions |
| "Imaging GO/NO-GO" | NOM-002 (pre-imaging) | payload_ops, aocs |
| "Mode Transition" | Any mode change | Affected positions |

### 14.3 Position Response Matrix

All 6 positions that must respond to a full GO/NO-GO poll:

| Position | Key GO Criteria |
|---|---|
| flight_director | Overall coordination; auto-votes GO on poll initiation |
| eps_tcs | SoC > 50%, bus_voltage > 27V, temps nominal |
| aocs | att_error < 1 deg, rates < 0.05 deg/s, mode = NOMINAL |
| ttc | link_status = LOCKED, RSSI > -100 dBm, margin > 3 dB |
| payload_ops | mode as planned, storage < 90%, FPA temp nominal |
| fdir_systems | obdh.mode = NOMINAL, CPU < 80%, no active FDIR events |

### 14.4 Result Handling

**REQ-GNG-002:** When all positions have responded:
- If all GO: result = "ALL_GO"; broadcast `go_nogo_result` with result="ALL_GO"; proceed with planned activity
- If any NO-GO or STANDBY: result = "NO_GO"; broadcast `go_nogo_result` with result="NO_GO"; FD must investigate NO-GO reason before re-polling or proceeding

**REQ-GNG-003:** The poll must auto-close when all positions in `self._positions.keys()` have responded (current implementation checks `positions_with_access <= responded`).

**REQ-GNG-004:** The FD must be able to cancel an active poll and manually override the result in time-critical situations.

---

## 15. Shift Handover Requirements

### 15.1 Handover Procedure (NOM-012 / PROC-FD-001)

As defined in `configs/eosat1/procedures/nominal/shift_handover.md`, the handover has 5 formal steps:

1. **Review All Subsystem States** -- Full HK sweep (SIDs 1-6), verbal status from each position
2. **Check Pending Alarms and Anomalies** -- Alarm buffer review, rejected TC disposition
3. **Review Upcoming Ground Station Contacts** -- Next 12 hours of contact schedule
4. **Verify Stored Commands and Schedule** -- S11.17 LIST_SCHEDULE, buffer status
5. **Formal Handover Declaration** -- Verbal declaration, log entry, authority transfer

### 15.2 MCS Support for Handover

**REQ-HND-001:** The MCS must provide a Shift Handover panel accessible via the `/api/handover` endpoint with:
- Timestamped handover notes (current implementation in `_handover_log`)
- Position attribution on each note
- Full session persistence (notes retained for entire MCS session)

**REQ-HND-002:** The handover panel must support generating a structured handover report containing:
- Current datetime (handover time)
- Spacecraft state summary (all 6 subsystems, one line each)
- Active alarms and pending items
- Upcoming contact schedule (next 12 hours)
- Onboard command schedule summary
- Open action items

**REQ-HND-003:** During handover:
- The outgoing FD retains console authority until Step 5 is complete
- If an anomaly is detected during handover HK sweep, outgoing FD retains authority and leads response
- Both FDs must be able to view all displays simultaneously (no position lock-out during handover)

### 15.3 Handover Data Requirements

| Data Item | Source | Retention |
|---|---|---|
| Subsystem HK snapshot | S3.27 all SIDs | Archived per pass |
| Alarm buffer status | obdh.alarm_buf_fill (0x0314) | Current value |
| TC rejected count | obdh.tc_rej_count (0x0306) | Delta since shift start |
| Reboot count | obdh.reboot_count (0x030A) | Delta since shift start |
| Contact schedule | Planner /api/contacts (24h) | Updated at handover |
| Onboard schedule | S11.17 LIST_SCHEDULE | Current snapshot |
| Handover notes | /api/handover | Cumulative log |

---

## Appendix A: Configuration File Cross-Reference

| File Path | Content | Relevance to FD |
|---|---|---|
| `configs/eosat1/mission.yaml` | APID (1), PUS version (2), time epoch | Packet identification |
| `configs/eosat1/orbit.yaml` | TLE, altitude (500 km), inclination (97.4 deg), ground stations | Pass prediction |
| `configs/eosat1/mcs/positions.yaml` | Position definitions, access control | FD authority model |
| `configs/eosat1/mcs/displays.yaml` | Widget configurations per position | Display layout |
| `configs/eosat1/mcs/limits.yaml` | Yellow/red limit thresholds | Alarm generation |
| `configs/eosat1/mcs/pus_services.yaml` | PUS service configuration (intervals, capacities) | Service capabilities |
| `configs/eosat1/commands/tc_catalog.yaml` | Full TC catalog (47 commands) | Command reference |
| `configs/eosat1/events/event_catalog.yaml` | 27 event definitions by subsystem | Event monitoring |
| `configs/eosat1/telemetry/hk_structures.yaml` | HK SID structures (SID 1-6, 10) | TM interpretation |
| `configs/eosat1/telemetry/parameters.yaml` | Full parameter database | Parameter lookup |
| `configs/eosat1/subsystems/fdir.yaml` | 12 FDIR rules with thresholds and actions | Autonomous response |
| `configs/eosat1/planning/activity_types.yaml` | 7 activity type definitions | Scheduling |
| `configs/eosat1/planning/ground_stations.yaml` | Ground station configurations (4 stations) | Contact planning |
| `configs/eosat1/procedures/procedure_index.yaml` | 54 procedure index entries | Procedure lookup |
| `configs/eosat1/scenarios/*.yaml` | 15 training scenarios | Training |
| `configs/eosat1/mcs/role_analysis/flight_director_role.md` | FD role analysis document | Authority reference |

## Appendix B: Requirements Traceability Summary

| Requirement ID | Category | Description |
|---|---|---|
| REQ-SCN-001 | Training | Scenario expected_responses must include detect/isolate/recover |
| REQ-SCN-002 | Training | Multi-failure scenarios must stagger injections |
| REQ-SCN-003 | Training | Scenarios must reference procedure IDs |
| REQ-DSP-001 | Display | Overview world map with ground track and contact windows |
| REQ-DSP-002 | Display | Simulation time, speed, tick display |
| REQ-DSP-003 | Display | Unfiltered event log for FD |
| REQ-DSP-004 | Display | Full SVG block diagrams for all subsystems |
| REQ-DSP-005 | Display | Full TC catalog visibility |
| REQ-DSP-006 | Display | Structured PUS command builder |
| REQ-DSP-007 | Display | PUS subtype validation |
| REQ-DSP-008 | Display | Verification log (200 entries, 5 states) |
| REQ-DSP-009 | Display | Procedure browser (54 procedures, 5 categories) |
| REQ-DSP-010 | Display | Procedure runner with step controls |
| REQ-DSP-011 | Display | Custom procedure builder and save |
| REQ-DSP-012 | Display | GO/NO-GO panel with FD-only initiation |
| REQ-DSP-013 | Display | GO/NO-GO via REST and WebSocket |
| REQ-DSP-014 | Display | Shift handover log |
| REQ-DSP-015 | Display | Time-series charts for all subsystems |
| REQ-DSP-016 | Display | WCAG 2.1 AA accessibility compliance |
| REQ-PLN-001 | Planner | 24-hour contact windows, 4 stations, 10s resolution |
| REQ-PLN-002 | Planner | Contact window detail (AOS/LOS, elevation, overlap) |
| REQ-PLN-003 | Planner | Auto-recompute every 10 minutes |
| REQ-PLN-004 | Planner | 7 activity types supported |
| REQ-PLN-005 | Planner | Conflict detection, pre-conditions, state lifecycle |
| REQ-PLN-006 | Planner | Upload command sequences to MCS |
| REQ-PLN-007 | Planner | 3-hour ground track, 30s resolution |
| REQ-PLN-008 | Planner | offset_minutes parameter for past/future track |
| REQ-PLN-009 | Planner | Real-time spacecraft state API |
| REQ-PLN-010 | Planner | Pass plan overview with activity overlay |
| REQ-PLN-011 | Planner | Schedule validation endpoint |
| REQ-SIM-001 | Simulator | EPS model fidelity (solar arrays, battery, PDU, MPPT) |
| REQ-SIM-002 | Simulator | AOCS model fidelity (9 modes, wheels, sensors, actuators) |
| REQ-SIM-003 | Simulator | OBDH model fidelity (dual OBC, bus, memory, boot loader) |
| REQ-SIM-004 | Simulator | TTC model fidelity (link budget, PA thermal, transponders) |
| REQ-SIM-005 | Simulator | TCS model fidelity (thermal transients, heaters, cooler) |
| REQ-SIM-006 | Simulator | Payload model fidelity (modes, FPA, memory, imaging) |
| REQ-SIM-007 | Simulator | All 12 FDIR rules implemented |
| REQ-SIM-008 | Simulator | FDIR actions generate S5 events |
| REQ-SIM-009 | Simulator | 13 failure injection types supported |
| REQ-SIM-010 | Simulator | Orbit model (SSO, eclipse, contacts, Doppler) |
| REQ-LEOP-001 | LEOP | All stations scheduled during LEOP |
| REQ-LEOP-002 | LEOP | Wider AOS tolerance (AOS -5 min) |
| REQ-LEOP-003 | LEOP | LEOP timeline display |
| REQ-GS-001 | Ground Stations | Primary station designation per pass |
| REQ-GS-002 | Ground Stations | Overlapping window management |
| REQ-GS-003 | Ground Stations | Station priority rules by mission phase |
| REQ-GS-004 | Ground Stations | Inter-station gap analysis |
| REQ-GS-005 | Ground Stations | Station handover protocol |
| REQ-ALM-001 | Alarm | Audio alarm at FD console on red limit |
| REQ-ALM-002 | Alarm | Visual flashing alarm on FD Overview |
| REQ-ALM-003 | Alarm | Auto-scroll event log to alarm |
| REQ-ALM-004 | Alarm | FDIR action notification |
| REQ-ALM-005 | Alarm | Alarms persist until FD acknowledgement |
| REQ-ALM-006 | Alarm | Alarm buffer downloadable via S15.9 |
| REQ-ALM-007 | Alarm | Alarm history with timestamps and ack record |
| REQ-VER-001 | Verification | Critical command confirmation dialog |
| REQ-VER-002 | Verification | Verification log fields (seq, name, state, error, position) |
| REQ-VER-003 | Verification | Verification log via REST and WebSocket |
| REQ-VER-004 | Verification | FD sees all positions' commands |
| REQ-VER-005 | Verification | Procedure-level verification with pause on rejection |
| REQ-VER-006 | Verification | Command pending indicator |
| REQ-VER-007 | Verification | FD notification on any REJECTED/FAILED from any position |
| REQ-GNG-001 | GO/NO-GO | 7 poll types defined |
| REQ-GNG-002 | GO/NO-GO | ALL_GO / NO_GO result determination |
| REQ-GNG-003 | GO/NO-GO | Auto-close when all positions responded |
| REQ-GNG-004 | GO/NO-GO | FD manual override capability |
| REQ-HND-001 | Handover | Shift handover panel with timestamped notes |
| REQ-HND-002 | Handover | Structured handover report generation |
| REQ-HND-003 | Handover | Authority transfer protocol (outgoing retains during anomaly) |

---

*AIG -- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

*End of Document -- EOSAT1-REQ-FD-001*

# OBDH / FDIR Position Requirements Document

**Document ID:** EOSAT1-REQ-OBDH-FDIR-001
**Issue:** 1.0
**Date:** 2026-03-12
**Position:** Data Systems Engineer (OBDH / FDIR)
**Classification:** UNCLASSIFIED -- For Simulation Use Only

---

## 1. Scope and Purpose

This document defines the operational requirements for the Data Systems Engineer (OBDH/FDIR) position for the EOSAT-1 ocean current monitoring cubesat mission. It covers all equipment, commands, telemetry, procedures, training scenarios, MCS display requirements, planner needs, and simulator fidelity requirements that fall under the OBDH/FDIR operator's responsibility.

The OBDH/FDIR position (position ID: `fdir_systems`) is responsible for the On-Board Data Handling subsystem and the Fault Detection, Isolation and Recovery system. This encompasses:

- Dual cold-redundant OBC (A/B) with bootloader/application state machine
- Dual CAN bus (A/B) with subsystem equipment mapping and failure isolation
- Onboard buffer management (HK/TM circular, event linear, alarm linear)
- PUS Service 12 onboard monitoring (parameter limit checking)
- PUS Service 19 event-action (autonomous response triggering)
- FDIR rule configuration, threshold management, and recovery coordination

---

## 2. Equipment Under OBDH/FDIR Responsibility

### 2.1 On-Board Computer (Dual Cold-Redundant)

| Item | Specification |
|---|---|
| Processor | LEON3-FT (rad-hardened SPARC), 80 MHz |
| RAM | 256 MB (EDAC protected) |
| Mass Memory | 2 GB Flash (EDAC protected) |
| Operating System | RTEMS (real-time) |
| Data Bus | SpaceWire + MIL-STD-1553B |
| Redundancy | Cold-redundant (OBC-A primary, OBC-B backup) |
| Switchover | Cold -- no state transfer; backup boots fresh from bootloader |
| Boot loader | Minimal SW image with beacon-only HK (SID 10, 16 s interval) |
| Application SW | Full flight software with all PUS services and autonomy |

**Requirements:**

- **REQ-OBDH-001:** The system shall model two independent OBC units (A and B) in cold-redundant configuration. Only one unit is active at any time; the other is in STANDBY (status=1) or OFF (status=0).
- **REQ-OBDH-002:** Switchover from the active OBC to the standby OBC shall reset all volatile state (counters, buffers, timers) and boot the new unit from its bootloader. No state transfer occurs.
- **REQ-OBDH-003:** The active OBC shall be identified by parameter `obdh.active_obc` (0x030C): 0 = OBC-A, 1 = OBC-B.
- **REQ-OBDH-004:** The backup OBC status shall be reported by `obdh.obc_b_status` (0x030D): 0 = OFF, 1 = STANDBY.
- **REQ-OBDH-005:** Per-unit boot counters (`obdh.boot_count_a` 0x0317, `obdh.boot_count_b` 0x0318) shall be maintained and reportable in telemetry.

### 2.2 Software Image State Machine (Bootloader / Application)

The OBC runs one of two software images at any time:

```
Power-On / Reboot
      |
      v
  BOOTLOADER (sw_image=0)
      |
      | (10 s CRC verification, unless boot_inhibit=1)
      v
  APPLICATION (sw_image=1, mode=NOMINAL)
      |
      +--- NOMINAL (mode=0) <---> SAFE (mode=1)
      |
      +--- Watchdog timeout / Memory error / Crash
      |         |
      |         v
      |    REBOOT --> BOOTLOADER
      |
      +--- Mode 2: MAINTENANCE (elevated CPU, debug tasks)
```

**Requirements:**

- **REQ-OBDH-006:** On power-on or reboot, the OBC shall enter the BOOTLOADER image (`obdh.sw_image` 0x0311 = 0). The bootloader provides minimal CPU usage (approx. 15%), 4 OS tasks, and beacon-only HK (SID 10).
- **REQ-OBDH-007:** Unless `boot_inhibit` is set, the bootloader shall automatically attempt to load the application image after a 10-second CRC verification timer. If the CRC check passes, `sw_image` transitions to APPLICATION (1). If CRC fails (corrupt image), the OBC remains in BOOTLOADER.
- **REQ-OBDH-008:** The `obc_boot_inhibit` command shall allow the operator to prevent auto-boot (inhibit=1) or re-enable it (inhibit=0), providing a mechanism to keep the OBC in bootloader for diagnostic purposes.
- **REQ-OBDH-009:** The `obc_boot_app` command shall manually initiate the 10-second CRC verification and boot sequence from bootloader to application.
- **REQ-OBDH-010:** When running in BOOTLOADER mode, the OBC shall generate only the BootLoader HK packet (SID 10) at a 16-second interval containing: `active_obc`, `active_bus`, `bus_voltage`, `temp`, `uptime`, `reboot_count`, `sw_image`, `last_reboot_cause`.

### 2.3 Hardware Watchdog Timer

| Parameter | Value |
|---|---|
| Watchdog period | 30 ticks (configurable via `watchdog_period_ticks`) |
| Reset behaviour | OBC reboot to bootloader |
| Arming | Automatic in APPLICATION mode, NOMINAL state |

**Requirements:**

- **REQ-OBDH-011:** The hardware watchdog shall be armed automatically when the OBC is running APPLICATION software in NOMINAL mode (mode=0). The watchdog timer resets every tick while armed and nominal.
- **REQ-OBDH-012:** If the watchdog timer exceeds the configured period (default: 30 ticks), the OBC shall execute an automatic reboot with `last_reboot_cause` = 2 (REBOOT_WATCHDOG).
- **REQ-OBDH-013:** When the OBC is in SAFE mode (mode=1) or MAINTENANCE mode (mode=2), the watchdog timer shall increment (not be reset), creating the possibility of a watchdog timeout if the OBC remains in a non-nominal mode for an extended period.
- **REQ-OBDH-014:** The bootloader shall NOT arm the watchdog timer (watchdog_timer held at 0 during bootloader operation).

### 2.4 Dual CAN Bus

| Bus | Default Subsystems |
|---|---|
| Bus A | EPS, TCS, AOCS |
| Bus B | TTC, Payload |

**Requirements:**

- **REQ-OBDH-015:** The system shall model two independent CAN buses (A and B), each connecting to a defined set of subsystems. The active bus is identified by `obdh.active_bus` (0x030E): 0 = Bus A, 1 = Bus B.
- **REQ-OBDH-016:** Each bus shall have an independent status parameter: `obdh.bus_a_status` (0x030F) and `obdh.bus_b_status` (0x0310), with values 0 = OK, 1 = DEGRADED, 2 = FAILED.
- **REQ-OBDH-017:** When a bus is in FAILED state, subsystems on that bus shall be unreachable. The `is_subsystem_reachable()` method shall return False for any subsystem connected to a failed active bus.
- **REQ-OBDH-018:** The `obc_select_bus` command shall switch the active bus from A to B or B to A. The command shall be rejected if the target bus is in FAILED state.
- **REQ-OBDH-019:** Bus equipment mapping shall be configurable via the `bus_mapping` configuration key in `obdh.yaml`.

### 2.5 Onboard Buffers

| Buffer | Type | Default Capacity | Behaviour |
|---|---|---|---|
| HK/TM Buffer | Circular (incremental fill, drain on downlink) | 1000 packets | Fills incrementally (0-2 pkts/tick); drains when TTC carrier lock active (1-5 pkts/tick) |
| Event Buffer | Linear (stop-when-full) | 500 entries | Fills on event; returns False when full |
| Alarm Buffer | Linear (stop-when-full) | 200 entries | Fills on alarm; returns False when full |

**Requirements:**

- **REQ-OBDH-020:** The HK/TM buffer (`obdh.hktm_buf_fill` 0x0312) shall operate as a circular buffer that fills during APPLICATION mode and drains when the TTC carrier lock is active (downlink).
- **REQ-OBDH-021:** The Event buffer (`obdh.event_buf_fill` 0x0313) shall operate as a linear stop-when-full buffer. When the buffer is full, new events shall be rejected (record_event returns False).
- **REQ-OBDH-022:** The Alarm buffer (`obdh.alarm_buf_fill` 0x0314) shall operate as a linear stop-when-full buffer. When the buffer is full, new alarms shall be rejected (record_alarm returns False).
- **REQ-OBDH-023:** Buffer fill levels shall be reportable in telemetry as part of HK SID 4 (Platform).

### 2.6 Onboard TM Storage (S15)

| Store ID | Name | Capacity | Service Routing |
|---|---|---|---|
| 1 | HK_Store | 5000 packets | S3 (Housekeeping) |
| 2 | Event_Store | 1000 packets | S5 (Events) |
| 3 | Science_Store | 10000 packets | All other services |
| 4 | Alarm_Store | 500 packets | Alarms (direct store) |

**Requirements:**

- **REQ-OBDH-024:** All TM packets shall be routed to onboard storage regardless of whether the RF downlink is active. Packets are stored based on service type routing.
- **REQ-OBDH-025:** Each store shall implement stop-when-full behaviour with an overflow flag. When a store reaches capacity, new packets are rejected and the overflow flag is set.
- **REQ-OBDH-026:** The operator shall be able to enable/disable individual stores (S15.1/S15.2), dump stored packets (S15.9), delete store contents (S15.11), and request store status (S15.13).

---

## 3. OBC Modes

| Mode ID | Mode Name | CPU Baseline | HK Rate | Task Count | Description |
|---|---|---|---|---|---|
| 0 | NOMINAL | 35% + noise | 1 Hz (all SIDs) | 15 | Full functionality |
| 1 | SAFE | 25% + noise | 0.1 Hz (essentials) | 12 | Reduced operations, non-essential loads off |
| 2 | MAINTENANCE | 55% + noise | 1 Hz (all SIDs) | 12 | Debug/diagnostic mode |

**Requirements:**

- **REQ-OBDH-027:** The OBC mode shall be commandable via `OBC_SET_MODE` (func_id 40, S8) with mode values 0, 1, or 2.
- **REQ-OBDH-028:** CPU load shall reflect the current mode: NOMINAL baseline 35%, SAFE baseline 25% (-10 offset), MAINTENANCE baseline 55% (+20 offset), each with Gaussian noise (sigma = 2.0%).
- **REQ-OBDH-029:** In SAFE mode, the OBC shall: disable payload operations, command AOCS to SAFE_POINT, reduce HK rate to 0.1 Hz, activate only essential heaters (battery, OBC), and maintain TTC link.

---

## 4. Reboot and Recovery Sequence

### 4.1 Reboot Causes

| Cause Code | Name | Trigger |
|---|---|---|
| 0 | REBOOT_NONE | Initial state (no reboot has occurred) |
| 1 | REBOOT_COMMAND | Ground-commanded reboot (func_id 42) |
| 2 | REBOOT_WATCHDOG | Hardware watchdog timeout |
| 3 | REBOOT_MEMORY_ERROR | EDAC uncorrectable memory error |
| 4 | REBOOT_SWITCHOVER | OBC unit switchover (A to B or B to A) |

**Requirements:**

- **REQ-OBDH-030:** On reboot, the OBC shall: increment `reboot_count`, set `last_reboot_cause` to the appropriate cause code, reset `uptime` to 0, clear all TC/TM counters, reset the watchdog timer, transition to BOOTLOADER image, set mode to SAFE (1), and increment the per-unit boot counter.
- **REQ-OBDH-031:** Unless `boot_inhibit` is active, the OBC shall automatically begin the 10-second application boot timer after any reboot.
- **REQ-OBDH-032:** The `last_reboot_cause` parameter (0x0316) shall persist across the reboot so the operator can determine why the OBC rebooted.

### 4.2 OBC Crash to Bootloader Recovery Sequence

The expected operational recovery sequence after an OBC crash:

1. **Crash detected** -- OBC application software hangs or encounters a fatal error.
2. **Watchdog timeout** -- Hardware watchdog triggers after 30 ticks without heartbeat reset.
3. **Automatic reboot** -- OBC resets, enters BOOTLOADER, `last_reboot_cause` = 2 (WATCHDOG).
4. **CRC verification** -- Bootloader performs 10-second CRC check of application image.
5. **Application boot** -- If CRC passes, application loads; OBC enters SAFE mode.
6. **Ground verification** -- Operator requests HK (SID 4), verifies `sw_image`=1, `mode`=1.
7. **Subsystem verification** -- Operator requests all subsystem HK, confirms all responding.
8. **Mode restoration** -- Operator commands `OBC_SET_MODE(mode=0)` to return to NOMINAL.

**If CRC fails (corrupt image):**

5a. OBC remains in BOOTLOADER (`sw_image`=0).
6a. Operator detects bootloader state via SID 10 minimal beacon.
7a. Operator commands `OBC_BOOT_INHIBIT(inhibit=1)` to prevent boot loop.
8a. Operator executes `MEM_CHECK` (S6.9) to verify application image integrity.
9a. Operator uploads corrected image via `MEM_LOAD` (S6.2) or switches to redundant OBC.

**Requirements:**

- **REQ-OBDH-033:** The system shall model the complete crash-to-bootloader-to-recovery sequence including: watchdog timeout, automatic reboot, CRC verification (10 s), and conditional application boot.
- **REQ-OBDH-034:** The `boot_image_corrupt` failure injection shall cause the CRC check to fail, keeping the OBC in BOOTLOADER indefinitely until the corruption is cleared and a manual boot is commanded.
- **REQ-OBDH-035:** An OBC crash (failure injection `obc_crash`) shall trigger an immediate reboot with `last_reboot_cause` = REBOOT_WATCHDOG.

---

## 5. Commands and Telemetry

### 5.1 Allowed PUS Services

The FDIR/Systems position has access to the following PUS services: **1, 3, 5, 6, 8, 12, 17, 19, 20**.

### 5.2 OBDH Commands (S8 Function Management, func_ids 40-47)

| Command | func_id | Criticality | Parameters | Description |
|---|---|---|---|---|
| OBC_SET_MODE | 40 | Standard | mode: 0/1/2 | Set OBC operating mode |
| OBC_MEMORY_SCRUB | 41 | Standard | (none) | Initiate manual memory scrub (~20 min) |
| OBC_REBOOT | 42 | **Critical** | (none) | Force OBC reboot to bootloader |
| OBC_SWITCH_UNIT | 43 | **Critical** | (none) | Switch to redundant OBC (cold switchover) |
| OBC_SELECT_BUS | 44 | Caution | bus: 0=A, 1=B | Select active CAN bus |
| OBC_BOOT_APP | 45 | Caution | (none) | Boot application from bootloader |
| OBC_BOOT_INHIBIT | 46 | Standard | inhibit: 0/1 | Inhibit/allow auto-boot |
| OBC_CLEAR_REBOOT_CNT | 47 | Standard | (none) | Reset all reboot counters to zero |

### 5.3 Memory Management Commands (S6)

| Command | Subtype | Parameters | Description |
|---|---|---|---|
| MEM_LOAD | 2 | address(4B), data(nB) | Load data to onboard memory |
| MEM_DUMP | 5 | address(4B), length(2B) | Dump onboard memory contents |
| MEM_CHECK | 9 | address(4B), length(2B) | Check memory region CRC |

### 5.4 Onboard Monitoring Commands (S12)

| Command | Subtype | Parameters | Description |
|---|---|---|---|
| MON_ENABLE | 1 | (none) | Enable all parameter monitoring |
| MON_DISABLE | 2 | (none) | Disable all parameter monitoring |
| MON_ADD_DEF | 6 | param_id(2B), check_type(1B), low_limit(4B float), high_limit(4B float) | Add monitoring definition |
| MON_DELETE_DEF | 7 | param_id(2B) | Delete monitoring definition |
| MON_REPORT | 12 | (none) | Request report of all monitoring definitions |

### 5.5 Event-Action Commands (S19)

| Command | Subtype | Parameters | Description |
|---|---|---|---|
| EA_ADD | 1 | ea_id(2B), event_type(1B), action_func_id(1B) | Add event-action definition |
| EA_DELETE | 2 | ea_id(2B) | Delete event-action definition |
| EA_ENABLE | 4 | ea_id(2B) | Enable event-action rule |
| EA_DISABLE | 5 | ea_id(2B) | Disable event-action rule |
| EA_REPORT | 8 | (none) | Request report of all event-action definitions |

### 5.6 Housekeeping Commands (S3)

| Command | Subtype | Parameters | Description |
|---|---|---|---|
| HK_REQUEST | 27 | sid(2B) | One-shot HK report request |
| HK_ENABLE | 5 | sid(2B) | Enable periodic HK for SID |
| HK_DISABLE | 6 | sid(2B) | Disable periodic HK for SID |
| HK_SET_INTERVAL | 31 | sid(2B), interval_s(4B float) | Modify HK reporting interval |
| HK_CREATE | 1 | sid(2B), interval_s(4B float), param_count(1B), param_ids(2B each) | Create custom HK definition |
| HK_DELETE | 2 | sid(2B) | Delete HK definition |

### 5.7 Other Services

| Service | Command | Subtype | Description |
|---|---|---|---|
| S5 | EVENT_ENABLE | 5 | Enable specific event type |
| S5 | EVENT_DISABLE | 6 | Disable specific event type |
| S5 | EVENT_ENABLE_ALL | 7 | Enable all event types |
| S5 | EVENT_DISABLE_ALL | 8 | Disable all event types |
| S9 | SET_TIME | 1 | Set onboard time (CUC format) |
| S9 | GET_TIME | 2 | Request time report |
| S17 | CONNECTION_TEST | 1 | Connection test (ping) |
| S20 | SET_PARAM | 1 | Set individual parameter value |
| S20 | GET_PARAM | 3 | Read individual parameter value |

### 5.8 OBDH Telemetry Parameters

#### Core OBC Health

| Param ID | Name | Units | Description | HK SID |
|---|---|---|---|---|
| 0x0300 | obdh.mode | enum | OBC mode: 0=NOMINAL, 1=SAFE, 2=MAINTENANCE | 4 |
| 0x0301 | obdh.temp | deg C | OBC board temperature (from TCS cross-coupling) | 4, 10 |
| 0x0302 | obdh.cpu_load | % | CPU utilisation | 4 |
| 0x0303 | obdh.mmm_used | % | Mass memory utilisation | 4 |
| 0x0308 | obdh.uptime | s | Seconds since last boot | 4, 10 |
| 0x030A | obdh.reboot_count | count | Total reboots since deployment | 4, 10 |
| 0x030B | obdh.sw_version | hex | Software version identifier | 4 |

#### TC/TM Counters

| Param ID | Name | Description | HK SID |
|---|---|---|---|
| 0x0304 | obdh.tc_rx_count | Total TCs received | 4 |
| 0x0305 | obdh.tc_acc_count | TCs accepted | 4 |
| 0x0306 | obdh.tc_rej_count | TCs rejected | 4 |
| 0x0307 | obdh.tm_pkt_count | TM packets generated | 4 |

#### Dual OBC and Redundancy

| Param ID | Name | Description | HK SID |
|---|---|---|---|
| 0x030C | obdh.active_obc | Active unit: 0=A, 1=B | 4, 10 |
| 0x030D | obdh.obc_b_status | Backup status: 0=OFF, 1=STANDBY | 4 |
| 0x030E | obdh.active_bus | Active CAN bus: 0=A, 1=B | 4, 10 |
| 0x030F | obdh.bus_a_status | Bus A: 0=OK, 1=DEGRADED, 2=FAILED | 4 |
| 0x0310 | obdh.bus_b_status | Bus B: 0=OK, 1=DEGRADED, 2=FAILED | 4 |
| 0x0311 | obdh.sw_image | Running image: 0=bootloader, 1=application | 4, 10 |
| 0x0316 | obdh.last_reboot_cause | Reboot cause code (0-4) | 4, 10 |
| 0x0317 | obdh.boot_count_a | OBC-A total boot count | 4 |
| 0x0318 | obdh.boot_count_b | OBC-B total boot count | 4 |

#### Buffer Fill Levels

| Param ID | Name | Units | Description | HK SID |
|---|---|---|---|---|
| 0x0312 | obdh.hktm_buf_fill | count | HK/TM buffer fill level | 4 |
| 0x0313 | obdh.event_buf_fill | count | Event buffer fill level | 4 |
| 0x0314 | obdh.alarm_buf_fill | count | Alarm buffer fill level | 4 |

#### Flight Hardware Realism (Phase 4)

| Param ID | Name | Units | Description | HK SID |
|---|---|---|---|---|
| 0x0319 | obdh.seu_count | count | Single-event upset counter | 4 |
| 0x031A | obdh.scrub_progress | % | Memory scrub progress (0-100%) | 4 |
| 0x031B | obdh.task_count | count | Active OS tasks (12-15 nominal, 4 bootloader) | 4 |
| 0x031C | obdh.stack_usage | % | Stack memory usage | 4 |
| 0x031D | obdh.heap_usage | % | Heap memory usage | 4 |
| 0x031E | obdh.mem_errors | count | Memory error count (correctable + uncorrectable) | 4 |

### 5.9 Telemetry Limit Definitions

| Parameter | Yellow Low | Yellow High | Red Low | Red High |
|---|---|---|---|---|
| obdh.cpu_load (%) | -- | 85 | -- | 98 |
| obdh.reboot_count | -- | 2 | -- | 5 |
| obdh.hktm_buf_fill (%) | -- | 80 | -- | 95 |
| obdh.event_buf_fill (%) | -- | 80 | -- | 95 |
| obdh.alarm_buf_fill (%) | -- | 80 | -- | 95 |
| obdh.seu_count | -- | 10 | -- | 50 |
| obdh.stack_usage (%) | -- | 80 | -- | 95 |
| obdh.heap_usage (%) | -- | 80 | -- | 95 |

### 5.10 HK Structures Relevant to OBDH/FDIR

| SID | Name | Interval | Content | Usage |
|---|---|---|---|---|
| 4 | Platform | 8 s | All OBDH parameters (mode, CPU, memory, counters, dual-OBC, buffers, SEU, scrub, tasks) | Primary OBDH monitoring in APPLICATION mode |
| 10 | BootLoader | 16 s | Minimal set: active_obc, active_bus, bus_voltage, temp, uptime, reboot_count, sw_image, last_reboot_cause | Used only when OBC is in BOOTLOADER mode |

---

## 6. FDIR Rules and Configuration

### 6.1 FDIR Hierarchy

| Level | Scope | Response Time | Authority | Example |
|---|---|---|---|---|
| 0 | Unit level | < 1 s | Hardware | PCDU over-current trip |
| 1 | Subsystem level | 1-10 s | Subsystem SW | Heater thermostat, disable hot RW |
| 2 | System level | 10-60 s | OBC FDIR application | Mode transition, safe mode entry |
| 3 | Ground level | Minutes-hours | Flight control team | Manual recovery procedures |

### 6.2 FDIR Rules Under OBDH/FDIR Responsibility

The FDIR/Systems operator configures and maintains all onboard FDIR rules. These are defined in `configs/eosat1/subsystems/fdir.yaml`:

| Rule ID | Parameter | Condition | Level | Action | Description |
|---|---|---|---|---|---|
| EPS-01 | eps.bat_soc | < 20% | 1 | payload_poweroff | Shed payload load on low battery |
| EPS-02 | eps.bat_soc | < 15% | 2 | safe_mode_eps | Critical low battery -- safe mode |
| EPS-03 | eps.bus_voltage | < 26 V | 2 | safe_mode_eps | Bus undervoltage -- safe mode |
| TCS-01 | tcs.temp_battery | > 42 C | 1 | heater_off_battery | Prevent battery overheating |
| TCS-02 | tcs.temp_battery | < 1 C | 1 | heater_on_battery | Prevent battery freezing |
| AOCS-01 | aocs.att_error | > 5 deg | 2 | safe_mode_aocs | Attitude loss -- safe mode |
| OBDH-01 | obdh.temp_obc* | > 65 C | 2 | safe_mode_obc | OBC overtemperature -- safe mode |
| OBDH-02 | obdh.reboot_count | > 4 | 3 | spacecraft_emergency | Excessive reboots -- emergency |
| AOCS-RW1 | aocs.rw1_temp | > 65 C | 1 | disable_rw1 | Protect overheating RW |
| AOCS-RW2 | aocs.rw2_temp | > 65 C | 1 | disable_rw2 | Protect overheating RW |
| AOCS-RW3 | aocs.rw3_temp | > 65 C | 1 | disable_rw3 | Protect overheating RW |
| AOCS-RW4 | aocs.rw4_temp | > 65 C | 1 | disable_rw4 | Protect overheating RW |

*Note: `fdir.yaml` references `obdh.temp_obc` but the actual parameter in `parameters.yaml` is `obdh.temp` (0x0301). This is a known configuration issue tracked in the test suite as xfail.*

**Requirements:**

- **REQ-FDIR-001:** The FDIR engine shall evaluate all enabled rules on every simulation tick. When a rule condition transitions from not-met to met, the corresponding action callback shall be executed.
- **REQ-FDIR-002:** FDIR rules at Level 2 or above shall trigger a spacecraft mode transition to SAFE (sc_mode=1). Level 3 rules shall trigger EMERGENCY (sc_mode=2).
- **REQ-FDIR-003:** Each FDIR trigger shall generate an S5 event packet with a unique event ID (0x8000 | param_id) and severity equal to (level + 1).
- **REQ-FDIR-004:** FDIR rules shall use edge detection: a rule fires only on the transition from "condition not met" to "condition met." The rule does not re-fire while the condition remains true. It resets when the condition clears.
- **REQ-FDIR-005:** Individual FDIR rules shall be configurable (thresholds, enable/disable) via S20 SET_PARAM and S12 monitoring definitions.

### 6.3 S12 Onboard Monitoring

**Requirements:**

- **REQ-FDIR-006:** The S12 monitoring system shall support adding monitoring definitions (S12.6) with fields: param_id (2 bytes), check_type (1 byte: 0=absolute, 1=delta), low_limit (4 bytes float), high_limit (4 bytes float).
- **REQ-FDIR-007:** Monitoring shall be globally enabled (S12.1) or disabled (S12.2).
- **REQ-FDIR-008:** Individual monitoring definitions shall be deletable via S12.7 by param_id.
- **REQ-FDIR-009:** A report of all active monitoring definitions shall be available via S12.12, returned as a TM packet (S12.13).
- **REQ-FDIR-010:** Each tick, the `check_monitoring()` method shall compare current parameter values against all enabled monitoring definitions and return violations (param out of low/high limits).

### 6.4 S19 Event-Action

**Requirements:**

- **REQ-FDIR-011:** The S19 event-action system shall support adding event-action definitions (S19.1) with fields: ea_id (2 bytes), event_type (1 byte), action_func_id (1 byte).
- **REQ-FDIR-012:** Event-action definitions shall be deletable (S19.2), individually enabled (S19.4), or disabled (S19.5).
- **REQ-FDIR-013:** A report of all event-action definitions shall be available via S19.8, returned as a TM packet (S19.128).
- **REQ-FDIR-014:** When an event occurs (via `trigger_event_action(event_type)`), all enabled event-action definitions matching that event_type shall execute their associated S8 function (action_func_id).
- **REQ-FDIR-015:** Event-actions shall provide autonomous onboard response capability, linking S5 event occurrences to S8 function commands without ground intervention.

---

## 7. Operational Procedures

### 7.1 Procedures Led by FDIR/Systems Position

#### LEOP Phase

| Proc ID | Name | FDIR/Systems Role |
|---|---|---|
| LEOP-002 | Initial Health Check | Verify OBDH and HK configuration |

#### Commissioning Phase

| Proc ID | Name | FDIR/Systems Role |
|---|---|---|
| COM-007 | OBDH Checkout | Verify OBC, CAN bus, memory subsystems |
| COM-008 | FDIR Configuration | Configure S12 monitoring limits and S19 event-actions |

#### Nominal Operations

| Proc ID | Name | FDIR/Systems Role |
|---|---|---|
| NOM-005 | HK Configuration | Adjust HK reporting rates and SID configuration |
| NOM-006 | Software Upload | Execute memory load (S6.2), verify (S6.9), boot |
| NOM-008 | Clock Synchronisation | Execute time update command (via S9) |

#### Contingency

| Proc ID | Name | FDIR/Systems Role |
|---|---|---|
| CTG-010 | OBDH Watchdog Recovery | Assess watchdog event, verify OBC state, determine root cause |
| CTG-011 | OBC Redundancy Switchover | Execute OBC_SWITCH_UNIT, verify new OBC online, restore subsystems |
| CTG-016 | Memory Segment Failure | Isolate bad segment, remap storage |
| CTG-017 | Bus Failure Switchover | Switch CAN bus, verify all subsystem communication restored |
| CTG-018 | Boot Loader Recovery | Diagnose boot failure, MEM_CHECK, MEM_LOAD, boot application |

#### Emergency

| Proc ID | Name | FDIR/Systems Role |
|---|---|---|
| EMG-003 | OBC Reboot | Execute commanded reboot, monitor boot sequence, verify recovery |

### 7.2 Procedure Execution Requirements

**Requirements:**

- **REQ-PROC-001:** All procedures involving `OBC_REBOOT` (func_id 42) and `OBC_SWITCH_UNIT` (func_id 43) shall require explicit Flight Director authorization before execution. These are classified as **critical** commands.
- **REQ-PROC-002:** The boot loader recovery procedure (CTG-018) shall support the complete diagnostic path: detect bootloader state, assess reboot cause, attempt boot, inhibit auto-boot, memory check, memory reload, and redundant OBC switchover as last resort.
- **REQ-PROC-003:** The bus failure switchover procedure (CTG-017) shall include: detection of missing HK from subsystems, bus status diagnosis, switchover command, and verification of all subsystem HK resumption.
- **REQ-PROC-004:** After any OBC reboot or switchover, the operator shall verify: `sw_image`=1, correct `sw_version`, `cpu_load` < 50%, `mem_errors`=0, all subsystem HK received, and `reboot_count` incremented by exactly 1.

---

## 8. Bus Failure Isolation

### 8.1 Failure Detection

Bus failure is detected through loss of housekeeping telemetry from subsystems connected to the failed bus. The OBC itself remains responsive (it is not on either CAN bus in the same way).

**Default bus-to-subsystem mapping:**
- **Bus A failure** -- Loss of EPS, TCS, and AOCS telemetry.
- **Bus B failure** -- Loss of TTC and Payload telemetry.

### 8.2 Isolation and Recovery

**Requirements:**

- **REQ-BUS-001:** The `bus_failure` failure injection shall set the specified bus (A or B) to FAILED status (BUS_FAILED=2).
- **REQ-BUS-002:** When the active bus is in FAILED state, all subsystems on that bus shall be unreachable. Subsystem HK packets from those subsystems shall not be generated.
- **REQ-BUS-003:** The operator shall be able to switch to the redundant bus via `OBC_SELECT_BUS` (func_id 44). The command shall validate that the target bus is not FAILED before executing.
- **REQ-BUS-004:** After bus switchover, the operator shall verify all subsystem HK responses are received (all 6 SIDs).
- **REQ-BUS-005:** If both buses are in FAILED state, the spacecraft is in a critical single-point failure condition. The OBC can still communicate via TTC link (TTC interfaces directly with the RF chain, not via CAN), but subsystem commanding and telemetry are lost.

---

## 9. SEU and Memory Management

### 9.1 Single-Event Upset Model

**Requirements:**

- **REQ-SEU-001:** The simulator shall model SEU occurrences with a probability of approximately 1.5e-4 per second per tick (approximately 1 SEU per 6600-second orbit, consistent with LEO South Atlantic Anomaly exposure).
- **REQ-SEU-002:** Each SEU shall increment both `obdh.seu_count` (0x0319) and `obdh.mem_errors` (0x031E).
- **REQ-SEU-003:** The `memory_scrub` command (func_id 41) shall initiate a memory scrub that progresses at approximately 5% per minute (full scrub in ~20 minutes). On completion, up to 3 memory errors shall be corrected.
- **REQ-SEU-004:** The `memory_corruption` failure injection shall add a configurable number of memory errors (default 10) and trigger an immediate reboot with `last_reboot_cause` = REBOOT_MEMORY_ERROR.

---

## 10. Training Scenarios

The following training scenarios shall be supported by the simulator for the OBDH/FDIR position:

### 10.1 Bootloader Stuck Recovery

**Scenario:** OBC is stuck in bootloader after a watchdog reset with a corrupt application image.
**Injection:** `boot_image_corrupt` on OBDH subsystem.
**Objective:** Operator detects bootloader state via SID 10 beacon, commands boot inhibit, performs memory check, uploads corrected image or switches to redundant OBC.
**Key Skills:** SID 10 interpretation, boot inhibit management, S6 memory commands, boot sequence monitoring.

### 10.2 CAN Bus Failure

**Scenario:** Bus A fails, causing loss of EPS, TCS, and AOCS telemetry.
**Injection:** `bus_failure` on OBDH subsystem with `bus=A`.
**Objective:** Operator detects missing HK, diagnoses bus failure via bus status parameters, commands bus switchover, verifies all subsystems resume.
**Key Skills:** Bus status monitoring, HK gap detection, bus switchover procedure.

### 10.3 OBC Crash and Watchdog Recovery

**Scenario:** OBC application crashes due to a software anomaly, triggering watchdog reset.
**Injection:** `obc_crash` on OBDH subsystem.
**Objective:** Operator detects unexpected reboot via reboot_count increment, verifies OBC recovered to application mode, checks for boot loop, assesses root cause.
**Key Skills:** Reboot cause analysis, post-reboot verification checklist, SAFE-to-NOMINAL recovery.

### 10.4 Memory Corruption and SEU Storm

**Scenario:** Multiple SEUs in the South Atlantic Anomaly cause memory corruption, triggering an EDAC uncorrectable error and automatic reboot.
**Injection:** `memory_corruption` on OBDH subsystem with `count=10`.
**Objective:** Operator detects reboot with `last_reboot_cause`=3 (MEMORY_ERROR), initiates memory scrub, monitors `seu_count` trend, assesses whether to switch OBC units.
**Key Skills:** SEU monitoring, memory scrub management, EDAC error assessment.

### 10.5 OBC Switchover Under Failure

**Scenario:** OBC-A exhibits persistent watchdog reboots (reboot_count climbing). FDIR Level 3 triggers spacecraft emergency when reboot_count > 4.
**Injection:** `obc_crash` on OBDH repeatedly (or `cpu_spike` to cause watchdog timeouts).
**Objective:** Operator recognises escalating reboot count, coordinates with FD, executes OBC switchover to OBC-B, verifies clean boot on backup unit, restores all subsystems.
**Key Skills:** Reboot count trend monitoring, FD coordination, switchover procedure execution, post-switchover subsystem verification.

### 10.6 Dual Bus Failure (Critical)

**Scenario:** Both CAN buses fail sequentially.
**Injection:** `bus_failure` on OBDH with `bus=A`, then `bus=B`.
**Objective:** Operator recognises total bus communication loss, identifies that TTC link remains (OBC-TTC path independent), escalates to emergency, and assesses options (limited to OBC-internal actions).
**Key Skills:** Critical failure recognition, emergency escalation protocol, understanding of system architecture single-point failures.

### 10.7 FDIR Configuration Verification

**Scenario:** During commissioning, operator must configure and verify all S12 monitoring definitions and S19 event-actions.
**Injection:** None (nominal commissioning scenario).
**Objective:** Operator uses S12.6 to add monitoring definitions for key parameters, S19.1 to add event-action rules, verifies via S12.12 and S19.8 reports, then tests by injecting edge-case parameter values.
**Key Skills:** S12/S19 command construction, verification via TM reports, understanding of FDIR thresholds.

### 10.8 Boot Loop Detection and Arrest

**Scenario:** OBC enters a boot loop (application crashes immediately after boot, causing repeated watchdog reboots).
**Injection:** `boot_image_corrupt` combined with auto-boot enabled.
**Objective:** Operator detects rapidly incrementing reboot_count, commands `OBC_BOOT_INHIBIT(inhibit=1)` to arrest the loop, then follows boot loader recovery procedure.
**Key Skills:** Rapid anomaly detection, boot inhibit command, boot loop arrest.

---

## 11. MCS Display and Tool Requirements

### 11.1 OBDH Tab Requirements

The MCS OBDH tab (visible to the `fdir_systems` position) shall provide the following display elements:

#### OBC Status Section

- **REQ-MCS-001:** An OBC mode indicator displaying the current mode (NOMINAL/SAFE/MAINTENANCE) with colour coding: green for NOMINAL, amber for SAFE, red for MAINTENANCE/EMERGENCY.
- **REQ-MCS-002:** A CPU load gauge (0-100%) with yellow band at 85% and red band at 98%.
- **REQ-MCS-003:** A value table showing: mode, uptime, reboot_count, sw_version, last_reboot_cause, tc_rx_count, tc_acc_count, tc_rej_count, tm_pkt_count.
- **REQ-MCS-004:** An active OBC indicator clearly showing which unit (A or B) is active, and the backup unit status (OFF/STANDBY).
- **REQ-MCS-005:** A software image indicator clearly showing whether BOOTLOADER or APPLICATION is running, with a visual warning (e.g., flashing amber) when in bootloader mode.

#### Bus and Buffers Section

- **REQ-MCS-006:** An active bus indicator showing Bus A or Bus B, with status LEDs for each bus (green=OK, amber=DEGRADED, red=FAILED).
- **REQ-MCS-007:** Buffer fill gauges for HK/TM, Event, and Alarm buffers, each with yellow (80%) and red (95%) bands.
- **REQ-MCS-008:** A subsystem reachability matrix showing which subsystems are connected to the active bus and whether they are responding.
- **REQ-MCS-009:** A value table showing: obc_b_status, last_reboot_cause, boot_count_a, boot_count_b.

#### OBC Trends Section

- **REQ-MCS-010:** A time-series chart of CPU load over the last 10 minutes.
- **REQ-MCS-011:** A time-series chart of buffer fill levels (HK, Event, Alarm) over the last 10 minutes.
- **REQ-MCS-012:** A time-series chart of SEU count and memory errors.

#### Flight Hardware Realism Section

- **REQ-MCS-013:** Display of SEU count, scrub progress (with progress bar during active scrub), task count, stack usage, and heap usage.
- **REQ-MCS-014:** Memory error count with limit colouring.

### 11.2 OBDH SVG Block Diagram

- **REQ-MCS-015:** The OBDH tab shall include an SVG block diagram showing: dual OBC units (A/B) with active/standby indication, dual CAN buses (A/B) with active/failed indication, bus-to-subsystem connections, buffer blocks (HK, Event, Alarm) with fill indicators, and the bootloader/application state.

### 11.3 Overview Tab OBDH Summary

- **REQ-MCS-016:** The Overview tab shall include an OBDH summary widget showing: OBC mode, CPU load, reboot count, active OBC, and sw_image status. This widget shall use colour coding consistent with limit definitions.

### 11.4 Commanding Tools

- **REQ-MCS-017:** The PUS command builder shall support all S8 function commands (func_ids 40-47) with appropriate form fields and validation.
- **REQ-MCS-018:** The PUS command builder shall support S6 memory management commands (subtypes 2, 5, 9) with address/length/data fields.
- **REQ-MCS-019:** The PUS command builder shall support S12 monitoring commands (subtypes 1, 2, 6, 7) and S19 event-action commands (subtypes 1, 2, 4, 5, 8).
- **REQ-MCS-020:** Critical commands (OBC_REBOOT func_id 42, OBC_SWITCH_UNIT func_id 43) shall display a confirmation dialog before execution.

### 11.5 Event Log Filtering

- **REQ-MCS-021:** The OBDH tab event log shall filter for OBDH-related events (event_id range 0x0300-0x03FF and FDIR events 0x8000+).

---

## 12. Planner Requirements

### 12.1 Activity Scheduling

- **REQ-PLN-001:** The mission planner shall account for OBC memory scrub windows (~20 min duration) when scheduling payload imaging activities, as scrub increases CPU load.
- **REQ-PLN-002:** The planner shall schedule clock synchronisation (S9.1) at least once per day, accounting for the expected 1 ms/day drift of the temperature-compensated crystal oscillator.
- **REQ-PLN-003:** The planner shall ensure that software upload activities (NOM-006) are scheduled during ground contacts with sufficient uplink capacity and do not overlap with payload imaging.

### 12.2 FDIR-Aware Planning

- **REQ-PLN-004:** The planner shall account for FDIR-triggered safe mode entries when computing power budgets. Safe mode reduces power consumption (payload off, reduced HK rate) but eliminates science data acquisition.
- **REQ-PLN-005:** The planner shall flag activities that require both CAN buses (simultaneous subsystem commanding across buses) and schedule them when bus health is confirmed.

### 12.3 Contact Window Utilisation

- **REQ-PLN-006:** The planner shall prioritise buffer dump activities (S15.9) during ground contacts when buffer fill levels exceed 80%, to prevent stop-when-full overflow.
- **REQ-PLN-007:** The planner shall schedule HK reporting rate increases (via S3.31) during ground contacts to increase data return, and rate decreases after LOS to conserve buffer space.

---

## 13. Simulator Fidelity Requirements

### 13.1 Timing and State Machine Fidelity

- **REQ-SIM-001:** The simulator shall model the 10-second CRC verification delay during bootloader-to-application transitions. This is not instantaneous.
- **REQ-SIM-002:** The simulator shall model the watchdog timer tick-by-tick, including the differential behaviour between NOMINAL (timer resets) and SAFE/MAINTENANCE (timer increments).
- **REQ-SIM-003:** Cold redundant switchover shall model the complete sequence: halt active OBC, switch `active_obc`, boot new unit from bootloader, no state transfer.

### 13.2 CPU and Resource Modelling

- **REQ-SIM-004:** CPU load shall be mode-dependent with Gaussian noise: BOOTLOADER ~15% (sigma=1), APPLICATION/NOMINAL ~35% (sigma=2), APPLICATION/SAFE ~25%, APPLICATION/MAINTENANCE ~55%.
- **REQ-SIM-005:** Task count shall reflect the running image: 4 tasks in BOOTLOADER, 12-15 in APPLICATION (15 if NOMINAL, 12 otherwise).
- **REQ-SIM-006:** Stack and heap usage shall correlate with CPU load and memory usage respectively, with Gaussian noise.

### 13.3 SEU and Memory Fidelity

- **REQ-SIM-007:** SEU occurrence shall follow a probabilistic model approximating 1 event per orbit (p=1.5e-4 per second per tick), consistent with LEO radiation environment.
- **REQ-SIM-008:** Memory scrub shall progress at 5%/min and correct up to 3 memory errors on completion.
- **REQ-SIM-009:** Memory corruption (EDAC uncorrectable) shall trigger an immediate reboot with `last_reboot_cause` = REBOOT_MEMORY_ERROR.

### 13.4 Bus and Communication Fidelity

- **REQ-SIM-010:** Bus failure injection shall make all subsystems on the affected bus unreachable. The `is_subsystem_reachable()` check shall be consulted before subsystem access.
- **REQ-SIM-011:** Bus equipment mapping (which subsystems are on which bus) shall be configurable via YAML configuration.
- **REQ-SIM-012:** Buffer fill/drain dynamics shall be realistic: HK buffer fills incrementally during application mode and drains when TTC carrier lock is active.

### 13.5 Failure Injection Capabilities

The simulator shall support the following failure injections for the OBDH subsystem:

| Failure | Effect | Recovery |
|---|---|---|
| `watchdog_reset` | Forces watchdog timer to period, triggering reset | Automatic reboot |
| `memory_errors` | Adds configurable number of memory errors | Memory scrub |
| `cpu_spike` | Sets CPU baseline to specified load (default 95%) | `clear_failure` resets to 35% |
| `obc_crash` | Immediate reboot with REBOOT_WATCHDOG cause | Boot sequence |
| `bus_failure` | Sets specified bus (A/B) to FAILED | `clear_failure` resets to OK |
| `boot_image_corrupt` | Application image CRC fails, OBC stays in bootloader | `clear_failure` restores image |
| `memory_corruption` | Adds memory errors + triggers REBOOT_MEMORY_ERROR | Boot sequence + scrub |

**Requirements:**

- **REQ-SIM-013:** All failure injections listed above shall be available through the instructor interface (failure_inject / failure_clear commands).
- **REQ-SIM-014:** Failure injections shall support configurable onset modes (step, gradual) and durations where applicable.
- **REQ-SIM-015:** The `clear_failure` mechanism shall restore the affected state to nominal values (e.g., CPU baseline to 35%, bus status to OK, boot image to uncorrupt).

### 13.6 Cross-Subsystem Coupling

- **REQ-SIM-016:** OBC temperature (`obdh.temp` 0x0301) shall be sourced from the TCS subsystem via shared parameter 0x0406 (`tcs.temp_obc`), modelling the physical thermal coupling.
- **REQ-SIM-017:** HK buffer drain rate shall depend on TTC carrier lock (shared parameter 0x0510), modelling the downlink capacity relationship.
- **REQ-SIM-018:** The FDIR engine shall access all subsystem parameters via the shared parameter store to evaluate rules spanning multiple subsystems (e.g., EPS battery SoC, AOCS attitude error, TCS temperatures).

---

## 14. TC Acceptance Validation

**Requirements:**

- **REQ-TC-001:** The TC acceptance check shall validate incoming telecommands against the set of known services: {3, 5, 6, 8, 9, 11, 12, 15, 17, 19, 20}. Unknown services shall be rejected with error code 0x0001.
- **REQ-TC-002:** For each known service, the TC acceptance check shall validate the subtype against the set of valid subtypes for that service. Unknown subtypes shall be rejected with error code 0x0002.
- **REQ-TC-003:** S8 commands (Function Management) shall be validated for minimum data length (at least 1 byte for func_id). Insufficient data shall be rejected with error code 0x0003.
- **REQ-TC-004:** S8 commands shall be validated for subsystem power state before execution. If the target subsystem power line is OFF, the command shall be rejected with error code 0x0004 and an S1.8 execution failure report generated.

Valid subtypes by service:

| Service | Valid Subtypes |
|---|---|
| S3 | 1, 2, 3, 4, 5, 6, 27, 31 |
| S5 | 5, 6, 7, 8 |
| S6 | 2, 5, 9 |
| S8 | 1 |
| S9 | 1, 2 |
| S11 | 4, 7, 9, 11, 13, 17 |
| S12 | 1, 2, 6, 7 |
| S15 | 1, 2, 9, 11, 13 |
| S17 | 1 |
| S19 | 1, 2, 4, 5, 8 |
| S20 | 1, 3 |

---

## 15. Known Configuration Issues

The following configuration issues are documented and tracked as xfail in the test suite:

1. **fdir.yaml** references `obdh.temp_obc` but the actual parameter in `parameters.yaml` is `obdh.temp` (0x0301). The FDIR engine's `_resolve_param_name()` method will fail to find the parameter ID, causing this rule to be silently skipped.

2. **hk_structures.yaml** SID 6 (TTC) references `param_id` 0x0508 which is not defined in `parameters.yaml`. This causes a missing parameter during HK packet construction.

3. **displays.yaml** references stale parameter names: `eps.pl_current_*`, `tcs.htr_thruster`, `ttc.data_rate_bps`, `ttc.elevation_deg` -- none of which exist in the current `parameters.yaml`.

---

## 16. GO/NO-GO Responsibilities

The FDIR/Systems position provides GO/NO-GO assessment to the Flight Director for:

| Assessment Area | GO Criteria |
|---|---|
| OBC Health | `cpu_load` < 85%, `reboot_count` acceptable, `mem_errors` = 0, `sw_image` = 1 (application), no recent watchdog events |
| Software Upload Readiness | OBC stable (uptime > 600 s), no recent reboots, memory CRC verified, both buses healthy |
| FDIR Configuration | All S12 monitoring definitions active, S19 event-actions enabled, thresholds correctly loaded |
| Bus Health | Both CAN buses OK (`bus_a_status` = 0, `bus_b_status` = 0), active bus communicating normally |
| Post-Anomaly Clearance | Correct `sw_image` running, HK flowing, `reboot_count` stable, all buffers draining normally |

**Critical Decision Points:**

- `reboot_count` > 2 (yellow): Recommend root cause investigation before further commanding.
- `reboot_count` > 4 (red): FDIR autonomously triggers `spacecraft_emergency`. Immediate coordination with FD required.
- `cpu_load` > 85% (yellow): Investigate task count; recommend deferring non-essential commanding.
- `seu_count` increasing: Recommend triggering `OBC_MEMORY_SCRUB` (func_id 41).
- `stack_usage` or `heap_usage` > 80% (yellow): Alert FD to potential OBC instability.
- Any bus status showing ERROR: Recommend bus switchover (CTG-017) before the remaining bus also fails.
- `sw_image` = 0 (bootloader) unexpectedly: Use SID 10 for minimal telemetry; exercise caution with auto-boot.

---

## 17. Inter-Position Coordination

| Scenario | Coordinating With | Details |
|---|---|---|
| Initial Health Check (LEOP-002) | Flight Director, EPS/TCS | Verify OBDH nominal after power/thermal confirmed |
| OBDH Checkout (COM-007) | Flight Director | FD authorizes; FDIR/Systems tests OBC, bus, memory |
| FDIR Configuration (COM-008) | Flight Director | FD approves; FDIR/Systems configures S12 and S19 |
| Software Upload (NOM-006) | Flight Director | FD GO/NO-GO at each stage; FDIR/Systems executes MEM_LOAD/CHECK/BOOT |
| Clock Sync (NOM-008) | Flight Director | FD verifies time delta; FDIR/Systems executes SET_TIME |
| Watchdog Recovery (CTG-010) | Flight Director | FD authorizes; FDIR/Systems verifies OBC recovered |
| OBC Switchover (CTG-011) | Flight Director, all positions | FD authorizes critical command; all positions prepare for TM loss |
| Bus Failure (CTG-017) | Flight Director | FD authorizes; FDIR/Systems switches bus, verifies nodes |
| Boot Loader Recovery (CTG-018) | Flight Director | FD authorizes; FDIR/Systems manages boot sequence |
| OBC Reboot (EMG-003) | Flight Director | FD authorizes critical command; FDIR/Systems executes and monitors |

---

## 18. Requirements Traceability Summary

| Requirement Group | Count | Prefix |
|---|---|---|
| OBDH Equipment | REQ-OBDH-001 to REQ-OBDH-035 | 35 |
| FDIR System | REQ-FDIR-001 to REQ-FDIR-015 | 15 |
| Procedures | REQ-PROC-001 to REQ-PROC-004 | 4 |
| Bus Isolation | REQ-BUS-001 to REQ-BUS-005 | 5 |
| SEU/Memory | REQ-SEU-001 to REQ-SEU-004 | 4 |
| MCS Displays | REQ-MCS-001 to REQ-MCS-021 | 21 |
| Planner | REQ-PLN-001 to REQ-PLN-007 | 7 |
| Simulator Fidelity | REQ-SIM-001 to REQ-SIM-018 | 18 |
| TC Validation | REQ-TC-001 to REQ-TC-004 | 4 |
| **Total** | | **113** |

---

*AIG -- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

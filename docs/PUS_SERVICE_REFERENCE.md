# PUS Service Reference — EOSAT-1 Spacecraft Simulator

**ECSS-E-70-41C Standard Compliance**

This document provides a complete reference for all PUS (Packet Utilization Standard) services implemented in the EOSAT-1 simulator. Use this guide to understand how to command the spacecraft and interpret telemetry responses.

---

## Table of Contents

1. [Service Overview](#service-overview)
2. [Service 1: Request Verification](#service-1-request-verification)
3. [Service 2: Device Access](#service-2-device-access)
4. [Service 3: Housekeeping](#service-3-housekeeping)
5. [Service 5: Event Reporting](#service-5-event-reporting)
6. [Service 6: Memory Management](#service-6-memory-management)
7. [Service 8: Function Management](#service-8-function-management)
8. [Service 9: Time Management](#service-9-time-management)
9. [Service 11: Activity Scheduling](#service-11-activity-scheduling)
10. [Service 12: On-Board Monitoring](#service-12-on-board-monitoring)
11. [Service 13: Large Data Transfer](#service-13-large-data-transfer)
12. [Service 15: TM Storage](#service-15-tm-storage)
13. [Service 17: Connection Test](#service-17-connection-test)
14. [Service 18: On-Board Control Procedures](#service-18-on-board-control-procedures)
15. [Service 19: Event-Action](#service-19-event-action)
16. [Service 20: Parameter Management](#service-20-parameter-management)

---

## Service Overview

| Service | Subtypes implemented (TC → TM) | Purpose | Subsystems |
|---------|-------------------------------|---------|-----------|
| S1 | (TM only) 1, 2, 3, 4, 5, 6, 7, 8 | Command acceptance & execution reports | All |
| S2 | 1, 5, 6 → 6 | Low-level device on/off + status report | EPS, AOCS, TCS, TTC, OBDH, Payload |
| S3 | 1, 2, 3, 4, 5, 6, 27, 31 → 25 | Periodic telemetry data collection | All |
| S5 | 5, 6, 7, 8 → 1, 2, 3, 4 | Autonomous event generation | All |
| S6 | 2, 5, 9 → 6, 10 | Onboard memory dump/load/check | OBDH |
| S8 | 1 (func_id 0–84, 100–107) → — | Direct function/command execution | All |
| S9 | 1, 2 → 2 | Spacecraft time sync & correlation | OBDH, TTC |
| S11 | 4, 7, 9, 11, 13, 17 → 5, 18 | Telecommand scheduling with timing | OBDH |
| S12 | 1, 2, 3, 4, 6, 7, 12 → 9, 10, 13 | Parameter threshold monitoring | All |
| S13 | 1, 3, 5, 9 → 2, 4, 10 | Block-based data downlink (images) | Payload |
| S15 | 1, 2, 9, 11, 13 → 14 | Telemetry storage management | OBDH |
| S17 | 1 → 2 | Connection test, link validation | TTC |
| S18 | 1, 2, 3, 4, 6 → 7 | On-board control procedures (OBCP) | OBDH |
| S19 | 1, 2, 4, 5, 8 → 128 | Autonomous event-triggered response | All |
| S20 | 1 (set), 3 (get) → 2 (report) | Parameter set / on-demand read. **Subtype allocation deviates from ECSS — see Service 20** | All subsystems |

S3 subtypes 3/4 are legacy aliases of the ECSS 5/6 (enable/disable periodic HK);
S3.1/S3.2 create and delete a custom SID. S5 subtypes 7/8 are enable-all and
disable-all shorthands.

The authoritative list is `ServiceDispatcher.dispatch()` in
`packages/smo-simulator/src/smo_simulator/service_dispatch.py`; any service or
subtype not in the table above is silently ignored by the OBC (logged at DEBUG,
no S1.2 rejection).

---

## Service 1: Request Verification

**Purpose:** Provide command acceptance, execution status, and completion reports.

**Subtypes:**

| Subtype | Direction | Description |
|---------|-----------|-------------|
| TC 1 | TC → — | Report request (request acceptance report) |
| TC 2 | TC → — | Report request (request execution failure report) |
| TM 1 | — → TM | Acceptance report (command accepted by onboard) |
| TM 2 | — → TM | Acceptance failure (command rejected) |
| TM 3 | — → TM | Execution start report |
| TM 4 | — → TM | Execution start failure (command execution failed) |
| TM 5 | — → TM | Execution progress (step N of M) |
| TM 7 | — → TM | Execution completion (command finished) |

**Lifecycle Example:**

```
Ground sends S8 command (SET_AOCS_MODE)
  ↓
Spacecraft S1.1: Acceptance report (request_id=1234)
  ↓
Spacecraft S1.3: Execution start (request_id=1234)
  ↓
Spacecraft S1.5: Progress (request_id=1234, step=1/3)
  ↓
Spacecraft S1.5: Progress (request_id=1234, step=2/3)
  ↓
Spacecraft S1.5: Progress (request_id=1234, step=3/3)
  ↓
Spacecraft S1.7: Execution complete (request_id=1234, result=OK)
```

**Error Handling:**

If command is rejected at acceptance:
```
Ground sends invalid S8 command
  ↓
Spacecraft S1.2: Acceptance failure (request_id=1234, error_code=0x0001)
```

If command starts but fails during execution:
```
Spacecraft S1.3: Execution start (request_id=1234)
  ↓
[Error occurs during execution]
  ↓
Spacecraft S1.4: Execution failure (request_id=1234, error_code=0x0005)
```

### Failure reports and the failure notice (defect #87)

ECSS-E-ST-70-41 allows an S1 failure report to carry a **failure notice** data
field after the failure code. EOSAT-1 uses that slot for the human-readable
reason the on-board software produced when it refused the command. Before this
existed, `ttc_basic._tier_permission_error` wrote an excellent reason ("TM rate
is forced to the beacon tier in beacon mode (LOW refused)"), the dispatcher
stored it, and the S1.8 packed only the code — so the operator saw a bare
`1,8` with a number and no way to tell which of the several gates sharing code
`0x0007` had fired.

**Wire format.** The notice is `reason_len(uint8) + reason(UTF-8)`, appended
**strictly after** the historical fields. It is omitted entirely when there is
no reason, and truncated to **160 octets** (never mid-character) when longer.

| Report | Wire format |
|--------|-------------|
| TM 1,1 / 1,7 | `request_id(u32)` |
| TM 1,2 acceptance failure | `request_id(u32) + error_code(u16) [+ reason_len(u8) + reason]` |
| TM 1,4 execution-start failure | `request_id(u16) + error_code(u16) [+ reason_len(u8) + reason]` |
| TM 1,8 execution failure | `request_id(u32) + error_code(u16) [+ reason_len(u8) + reason]` |
| TM 1,3 | `request_id(u16)` |
| TM 1,5 | `request_id(u16) + step(u8)` |

1,4 keeps a **16-bit** request id because that is what
`ServiceDispatcher.generate_s1_exec_fail` has always emitted; 1,2 and 1,8 keep
the 32-bit `((apid & 0x7FF) << 14) | (seq & 0x3FFF)` form built by
`TMBuilder`. Both layouts are decoded by
`smo_common.protocol.pus_services.VerificationData.parse`.

**Backward compatibility.** Because the notice is appended after the fixed
fields, a parser that reads only `>IH` (1,2 / 1,8) or `>HH` (1,4) is
unaffected and keeps working against a spacecraft that sends one. Equally, a
ground segment that reads the notice handles a spacecraft that sends none —
the field simply decodes as absent (empty string).

**Failure codes.** The vocabulary is
`smo_common.protocol.pus_services.FAILURE_CODES`, shared by the on-board
dispatcher and the MCS so the two cannot drift:

| Code | Meaning |
|------|---------|
| `0x0001` | Generic failure / unknown service |
| `0x0002` | Unknown subtype |
| `0x0003` | Invalid parameter or data length |
| `0x0004` | Subsystem unpowered or unreachable |
| `0x0005` | Invalid in the current mode |
| `0x0006` | Not permitted in bootloader |
| `0x0007` | Not permitted in the current state |

Service 6 reuses the low codes with memory-specific meanings
(`MEMORY_FAILURE_CODES`: illegal address, write protected, bad argument,
length exceeded) — see [Service 6](#service-6-memory-management).

The code alone is deliberately not sufficient: several distinct gates share
`0x0007`, which is exactly why the notice exists. **Read the notice, not the
number.**

**Event.** Every refusal — at acceptance *or* at execution — also raises
S5 event `0x030B COMMAND_REJECTED` (severity MEDIUM), whose auxiliary text
carries the command identity (`S8.1 func 65`), the failure code and the same
reason. It therefore appears in the event log, the on-board alarm store and
the MCS FDIR journal, not only against the one verification row the operator
may not be watching. `TC(5,6)` with event type 2 suppresses it if a training
scenario needs the noise gone.

---

## Service 2: Device Access

**Purpose:** Switch individual on-board devices on and off below the level of
the subsystem function commands (Service 8), and read back their state. This is
what an operator uses to unpower a specific unit — an S12 monitor whose source
device is off is suppressed rather than raising a spurious limit alarm.

**Subtypes:**

| Subtype | Direction | Meaning | Wire format |
|---------|-----------|---------|-------------|
| 1 | TC | Distribute on/off | `device_id (uint16) + state (uint8)` |
| 5 | TC | Distribute on/off with verification → TM(2,6) | `device_id (uint16) + state (uint8)` |
| 6 | TC | Report on/off status → TM(2,6) | `device_id (uint16)` |
| 6 | TM | Device status report | `device_id (uint16) + state (uint8)` |

**Device ID ranges** (`ServiceDispatcher._set_device_state`): 0x0100–0x010F EPS,
0x0200–0x021F AOCS, 0x0300–0x030F TCS, 0x0400–0x040F TT&C, 0x0500–0x050F OBDH,
0x0600–0x060F Payload. The catalogue's `S2_*` entries name the shipped devices.

Subtypes 2 and 3 (register load / register dump) are **declared in the source
comment but not implemented** — they are accepted and ignored.

---

## Service 3: Housekeeping

**Purpose:** Request or receive periodic telemetry data (HK reports).

**Structures:**

| SID | Name | Interval | Parameters | Subsystem |
|-----|------|----------|------------|-----------|
| 1 | HK_EPS | 1 s | Battery, solar, bus, power | EPS |
| 2 | HK_AOCS | 4 s | Attitude, rates, wheels, sensors | AOCS |
| 3 | HK_TCS | 60 s | Temperature per zone | TCS |
| 4 | HK_OBDH | 8 s | OBC mode, memory, CPU, CAN | OBDH |
| 5 | HK_Payload | 8 s | FPA temp, storage, compression | Payload |
| 6 | HK_TTC | 8 s | Link margin, PA temp, lock state | TTC |
| 11 | Beacon | 30 s | Condensed spacecraft health beacon — the ONLY HK emitted in bootloader mode (phase ≤ 3), sourced from bootloader APID 0x002 | Bootloader / All |

**Bootloader-mode telemetry contract:** While the OBC is running the bootloader (phases 0–3), only SID 11 is active, emitted from the distinct bootloader APID (0x002 by default). SIDs 1–6 only come online after the application software has booted (phase ≥ 4) and are emitted from the application APID (0x001). Ground operators should expect to see traffic switch from APID 0x002 → 0x001 as the visible sign of successful OBC application boot. An unplanned OBC reboot reverts the spacecraft to bootloader mode automatically and traffic falls back to APID 0x002 / SID 11 only.

**Commands:**

### S3 Subtype 27 — Request One-Shot HK Report
```
Parameter: SID (structure ID, uint16)
Example: Request HK_EPS (SID=1)
Response: HK report with 35 parameters (battery SoC, voltage, current, etc.)
```

### S3 Subtype 5 — Enable Periodic HK
```
Parameter: SID (structure ID, uint16)
Default interval: 4 seconds
Response: HK reports sent every 4 s until disabled
```

### S3 Subtype 6 — Disable Periodic HK
```
Parameter: SID (structure ID, uint16)
Response: HK transmission stops (can re-enable with S3.5)
```

### S3 Subtype 31 — Change HK Interval
```
Parameters: SID (uint16), interval_s (float32)
Example: Change HK_AOCS interval to 2 seconds
Response: HK reports now sent every 2 s
Valid range: 1 — 3600 seconds
```

**HK Parameter Examples:**

**EPS Structure (SID=1):**
```
eps.bat_voltage        [V]      Battery voltage (nominal 28.5V)
eps.bat_soc            [%]      Battery state of charge (0-100%)
eps.bat_temp           [C]      Battery temperature (-5 to +50°C)
eps.bus_voltage        [V]      Main bus voltage (27-35V nominal)
eps.power_gen          [W]      Total solar power generation
eps.power_cons         [W]      Total power consumption
eps.load_shed_stage    [0-3]    Current load shedding stage
eps.power_margin_w     [W]      Generation - consumption
```

**AOCS Structure (SID=2):**
```
aocs.att_q1, q2, q3, q4  [—]    Attitude quaternion (xyzw)
aocs.rate_roll/pitch/yaw [deg/s] Angular velocity
aocs.rw1/2/3/4_speed     [RPM]  Reaction wheel speeds
aocs.mode                 [0-8]  AOCS mode (0=off, 1=safe, etc.)
aocs.total_momentum       [Nms]  System angular momentum
aocs.att_error           [deg]  Pointing error magnitude
```

**TTC Structure (SID=6):**
```
ttc.link_margin        [dB]   Eb/N0 value (link quality)
ttc.pa_temp            [C]    Power amplifier temperature
ttc.carrier_locked     [0/1]  Carrier lock status
ttc.bit_sync_locked    [0/1]  Bit synchronization status
ttc.frame_sync_locked  [0/1]  Frame synchronization status
ttc.ber                [—]    Bit error rate (1e-6 to 1e-4)
```

---

## Service 5: Event Reporting

**Purpose:** Report autonomous spacecraft events (alarms, state changes, sensor anomalies).

**Event Categories:**

| Category | ID Range | Examples | Severity |
|----------|----------|----------|----------|
| Orbital | 0x0001-0x001F | AOS, LOS, Eclipse entry/exit | INFO |
| EPS | 0x0100-0x011F | SoC warning, bus undervoltage, load shed | INFO to HIGH |
| AOCS | 0x0200-0x02FF | Mode change, RW overspeed, ST blind | INFO to HIGH |
| TCS | 0x0400-0x04FF | Temperature alarms, heater stuck | INFO to HIGH |
| TTC | 0x0500-0x05FF | Link margin, PA thermal, lock lost | INFO to HIGH |
| OBDH | 0x0300-0x03FF | Reboot, memory error, bus failure | INFO to HIGH |
| Payload | 0x0600-0x06FF | FPA thermal, storage full, checksum error | INFO to HIGH |
| FDIR | 0x0F00-0x0F0B | Fault detected, recovery started | INFO to HIGH |

**Example Events:**

```
0x0101: BATTERY_SOC_WARNING
  Severity: MEDIUM (yellow alert)
  When: Battery SoC drops below 20%
  Action: Reduce non-critical loads (via S19)

0x0201: RW_OVERSPEED
  Severity: MEDIUM (yellow alert)
  When: Reaction wheel speed exceeds ±5000 RPM
  Action: Disable overspecc wheel or trigger desaturation

0x0401: TCS_OVERTEMP_ALARM
  Severity: HIGH (red alert)
  When: Thermal zone exceeds alarm threshold (e.g., FPA > -3°C)
  Action: Payload off (via S19)

0x0501: CARRIER_LOCK_LOST
  Severity: MEDIUM (yellow alert)
  When: Downlink carrier lock lost
  Action: Increase TX power (via S19)
```

**Commands:**

### S5 Subtype 5 — Enable Event Type
```
Parameter: event_id (uint16)
Example: Enable RW_OVERSPEED (0x0201)
Response: S5 packets will be generated when wheel overspeed detected
```

### S5 Subtype 6 — Disable Event Type
```
Parameter: event_id (uint16)
Example: Disable BATTERY_SOC_WARNING (0x0101)
Response: S5 packets for this event will not be generated
```

**Event Packet Format:**
```
Primary Header (PUS)
Time stamp [8 bytes]: UTC seconds + microseconds
Event ID [2 bytes]: Unique event identifier
Event type [1 byte]: 0=INFO, 1=WARNING, 2=ALARM, 3=CRITICAL
Message [0-256 bytes]: Optional event-specific data
```

---

## Service 6: Memory Management

**Purpose:** Load/dump/verify onboard memory (SRAM, EEPROM).

**Commands:**

### S6 Subtype 2 — Load Memory
```
Parameters:
  memory_id [0=SRAM, 1=EEPROM]
  address [uint32, bytes from start]
  data [variable length]
Example: Load 256 bytes of software patch to EEPROM starting at 0x8000
Response: S1 verification report
Limitations: Simplified implementation, no CRC per block
```

### S6 Subtype 5 — Dump Memory
```
Parameters:
  memory_id [0=SRAM, 1=EEPROM]
  address [uint32]
  length [uint16]
Example: Dump 512 bytes from SRAM starting at 0x2000
Response: TM packet with memory contents (may span multiple packets for large dumps)
Limitations: Returns mock data, no real memory model
```

### S6 Subtype 9 — Check Memory CRC
```
Parameters:
  memory_id [0=SRAM, 1=EEPROM]
  address [uint32]
  length [uint16]
Example: Verify CRC of application firmware (64 KB block)
Response: S1 success with CRC value (hardcoded for now)
Limitations: Simplified, does not reflect real memory state
```

---

## Service 8: Function Management

**Purpose:** Execute spacecraft functions (commands to each subsystem).

**Structure:**
```
Service: 8
Subtype: 1 (execute function)
Function_ID: 0-255 (command selector)
Data: function-specific parameters
```

**AOCS Functions (func_id 0-15):**

| Func ID | Name | Parameters | Effect |
|---------|------|-----------|--------|
| 0 | AOCS_SET_MODE | mode (0-8) | Set operational mode (safe, detumble, fine point, etc.) |
| 1 | AOCS_DESATURATE | — | Start reaction wheel desaturation sequence |
| 2 | AOCS_DISABLE_WHEEL | wheel_idx (0-3) | Disable specific reaction wheel |
| 3 | AOCS_ENABLE_WHEEL | wheel_idx (0-3) | Enable specific reaction wheel |
| 4 | ST1_POWER | on (0/1) | Power on/off star tracker 1 |
| 5 | ST2_POWER | on (0/1) | Power on/off star tracker 2 |
| 6 | ST_SELECT | unit (0/1) | Select primary star tracker (0=ST1, 1=ST2) |
| 7 | MAG_SELECT | unit (0/1) | Select magnetometer source |
| 8 | RW_SET_SPEED_BIAS | wheel, bias_rpm | Set reaction wheel speed bias |
| 9 | MTQ_ENABLE | enable (0/1) | Enable/disable magnetorquers |
| 10 | AOCS_SLEW_TO | q_x, q_y, q_z, q_w, rate_dps | Slew to target quaternion |
| 11 | AOCS_CHECK_MOMENTUM | — | Query momentum saturation status |
| 12 | AOCS_BEGIN_ACQUISITION | — | Start automated attitude acquisition |
| 13 | AOCS_GYRO_CALIBRATION | — | Reset gyroscope bias estimate |
| 14 | AOCS_RW_RAMP_DOWN | wheel_idx/255, target_rpm | Gracefully reduce wheel speed |
| 15 | AOCS_SET_DEADBAND | deadband_deg | Set attitude error deadband |

**EPS Functions (func_id 16-25):**

| Func ID | Name | Parameters | Effect |
|---------|------|-----------|--------|
| 16 | EPS_PAYLOAD_MODE | mode (0=off, 1=standby, 2=imaging) | Control payload power |
| 17 | EPS_SWITCH_LOAD | line (0-7), state (0/1) | Switch individual power line |
| 18 | EPS_BATTERY_HEATER | setpoint_C (float) | Set battery heater temperature |
| 19 | EPS_CHARGE_RATE | rate_A (float) | Override battery charge rate |
| 20 | EPS_SOLAR_DRIVE | angle_deg (float) | Set solar array drive angle |
| 21 | EPS_EMERGENCY_SHED | — | Trigger emergency load shedding |
| 22 | EPS_BUS_ISOLATION | — | Isolate failing bus segment |
| 23 | EPS_LOAD_SHED_STAGE_1 | — | Activate stage 1 (non-critical loads) |
| 24 | EPS_LOAD_SHED_STAGE_2 | — | Activate stage 2 (payload, TTC) |
| 25 | EPS_LOAD_SHED_STAGE_3 | — | Activate stage 3 (AOCS only) |

**Payload Functions (func_id 26-39):**

| Func ID | Name | Parameters | Effect |
|---------|------|-----------|--------|
| 26 | PAYLOAD_SET_MODE | mode (0=off, 1=standby, 2=imaging) | Set payload operating mode |
| 27 | PAYLOAD_SET_SCENE | scene_id (uint16) | Set scene ID for imaging |
| 28 | PAYLOAD_CAPTURE | scene_id, lines | Trigger image capture |
| 29 | PAYLOAD_DOWNLOAD_IMAGE | scene_id | Queue image for downlink |
| 30 | PAYLOAD_DELETE_IMAGE | scene_id | Delete image from memory |
| 31 | PAYLOAD_MARK_BAD_SEGMENT | segment_id (0-7) | Mark memory segment unusable (num_memory_segments = 8) |
| 32 | PAYLOAD_GET_IMAGE_CATALOG | — | Request stored image catalog |
| 33 | PAYLOAD_SET_BAND_CONFIG | mask (bits 0-3: B/G/R/NIR) | Set spectral band enable mask |
| 34 | PAYLOAD_SET_INTEGRATION_TIME | blue/green/red/nir_ms | Set per-band integration times |
| 35 | PAYLOAD_SET_GAIN | gain (0.1-10.0) | Set detector gain |
| 36 | PAYLOAD_COOLER_SETPOINT | setpoint_C (-20 to 0) | Set FPA cooler target temp |
| 37 | PAYLOAD_START_CALIBRATION | — | Begin calibration sequence |
| 38 | PAYLOAD_STOP_CALIBRATION | — | Abort calibration sequence |
| 39 | PAYLOAD_SET_COMPRESSION | ratio (0=auto, else manual) | Override compression ratio |

**TCS Functions (func_id 40-49):**

| Func ID | Name | Parameters | Effect |
|---------|------|-----------|--------|
| 40 | HEATER_BATTERY | on (0/1) | Battery heater on/off |
| 41 | HEATER_OBC | on (0/1) | OBC heater on/off |
| 42 | HEATER_THRUSTER | on (0/1) | Thruster heater on/off |
| 43 | FPA_COOLER | on (0/1) | FPA cooler on/off |
| 44 | HEATER_SET_SETPOINT | circuit, on_temp, off_temp | Set thermostat setpoints |
| 45 | HEATER_AUTO_MODE | circuit (0-2) | Return heater to auto control |
| 46 | TCS_SET_HEATER_DUTY_LIMIT | circuit, max_duty_pct | Limit heater duty cycle |
| 47 | TCS_DECONTAMINATION_START | target_temp_c | Begin decontamination heating |
| 48 | TCS_DECONTAMINATION_STOP | — | Abort decontamination |
| 49 | TCS_GET_THERMAL_MAP | — | Request thermal status all zones |

**OBDH Functions (func_id 50-62):**

| Func ID | Name | Parameters | Effect |
|---------|------|-----------|--------|
| 50 | OBC_SET_MODE | mode (0=nom, 1=safe, 2=emerg) | Set OBC operating mode |
| 51 | OBC_MEMORY_SCRUB | — | Trigger manual memory scrub |
| 52 | OBC_REBOOT | — | Force OBC reboot (CRITICAL) |
| 53 | OBC_SWITCH_UNIT | — | Switch to redundant OBC (CRITICAL) |
| 54 | OBC_SELECT_BUS | bus (0=A, 1=B) | Select active CAN bus |
| 55 | OBC_BOOT_APP | — | Boot application from bootloader |
| 56 | OBC_BOOT_INHIBIT | inhibit (0/1) | Inhibit/allow auto-boot |
| 57 | OBC_CLEAR_REBOOT_CNT | — | Reset reboot counter |
| 58 | OBC_SET_WATCHDOG_PERIOD | period_ticks (1-1000) | Configure watchdog timeout |
| 59 | OBC_WATCHDOG_ENABLE | — | Enable watchdog monitoring |
| 60 | OBC_WATCHDOG_DISABLE | — | Disable watchdog monitoring |
| 61 | OBC_DIAGNOSTIC | — | Request OBC health diagnostic |
| 62 | OBC_ERROR_LOG | — | Request recent error log |

**TTC Functions (func_id 63-79):**

| Func ID | Name | Parameters | Effect |
|---------|------|-----------|--------|
| 63 | TTC_SWITCH_PRIMARY | — | Switch to primary transponder |
| 64 | TTC_SWITCH_REDUNDANT | — | Switch to redundant transponder |
| 65 | TTC_SET_DATA_RATE | rate (uint8 tier code: 0=LOW, 1=HIGH, 255=BEACON) | Select a rung of the TM data-rate ladder — see below |
| 66 | TTC_PA_ON | — | Enable power amplifier |
| 67 | TTC_PA_OFF | — | Disable power amplifier |
| 68 | TTC_SET_TX_POWER | level uint8 (0=LOW 1.0 W, 1=NOMINAL 2.0 W, 2=HIGH 5.0 W) | Select an SSPA operating point. Discrete steps only; an unknown code is rejected with S1.8 error 0x0003, never clamped. The old '2 = 8 W' exceeded pa_max_power_w = 5.0 W and was never executable. |
| 69 | TTC_DEPLOY_ANTENNA | — | Deploy antenna (burn-wire) |
| 70 | TTC_SET_BEACON_MODE | on (0/1) | Enable/disable beacon mode |
| 71 | TTC_CMD_CHANNEL_START | — | Start command channel (15-min) |
| 72 | TTC_SET_UL_FREQ | freq_mhz (2000-2100) | Set uplink frequency |
| 73 | TTC_SET_DL_FREQ | freq_mhz (8400-8500) | Set downlink frequency |
| 74 | TTC_SET_MODULATION | mode (0=BPSK, 1=QPSK) | Set modulation mode |
| 75 | TTC_SET_RX_GAIN | agc_db (-100 to 0) | Set receiver AGC target |
| 76 | TTC_RANGING_START | — | Begin ranging sequence |
| 77 | TTC_RANGING_STOP | — | Stop ranging sequence |
| 78 | TTC_SET_COHERENT_MODE | mode (0/1) | Select coherent/non-coherent |
| 79 | TTC_SET_DOWNLINK_MODE | on (uint8, 0/1) | Enter/leave DOWNLINK (payload-dump) mode — the mode gate on the HIGH tier |

#### Func 65 — TTC_SET_DATA_RATE and the TM rate ladder

The downlink is a three-rung ladder defined in
`configs/eosat1/subsystems/ttc.yaml tm_rate_tiers`, not a binary high/low switch.
Func 65 takes **one uint8** carrying a tier `command_code`:

| Code | Tier | Rate | Modulation / coding | Symbol rate | Selectable? |
|------|------|------|---------------------|-------------|-------------|
| `0x00` | LOW | 16 000 bps | BPSK, conv(7,1/2)+RS | 16 kBd | Always. **The routine operations rate** and the safe-mode / FDIR fallback |
| `0x01` | HIGH | 128 000 bps | QPSK, RS(255,223) | 64 kBd | Only with the antenna deployed, DOWNLINK mode on (func 79) and elevation >= 10 deg |
| `0xFF` | BEACON | 1 000 bps | BPSK, conv(7,1/2)+RS | 1 kBd | **Not** by this command — entered by func 70 (`TTC_SET_BEACON_MODE`) or by the bootloader |

**The legacy encoding is preserved verbatim.** `rate=0` has always meant "the low rate"
and `rate=1` "the high rate"; they now select LOW and HIGH. Existing procedures, OBCPs
and the FDIR `ttc_reduce_power` action therefore keep their meaning and land on usable
operational rates. A 4-byte big-endian raw-bps form is still accepted for older clients:
a value equal to a configured tier's `bps` selects that tier.

**Rejection, not clamping.** A tier whose gates are not satisfied is refused with an
S1.8 execution failure, error `0x0007`, and the failure text names the open gate. An
unrecognised tier code gives error `0x0003`. Nothing is silently substituted.

**Commanded vs active.** Func 65 sets the COMMANDED tier (`ttc.tm_tier_cmd`, 0x053A).
The TT&C tick resolves the ACTIVE tier (`ttc.tm_tier`, 0x0539) from it by walking down
the ladder until every gate is satisfied, so HIGH auto-reverts as a pass drops below
10 deg and resumes on the next pass without re-commanding.

Related telemetry: `ttc.tm_data_rate` (0x0506, active bit rate), `ttc.tm_tier` (0x0539),
`ttc.tm_tier_cmd` (0x053A), `ttc.tm_symbol_rate` (0x053B), `ttc.downlink_mode` (0x053C).

#### Func 79 — TTC_SET_DOWNLINK_MODE

```
Parameter: on (uint8) — 0 = leave DOWNLINK mode, 1 = enter DOWNLINK/dump mode
```

DOWNLINK mode is the *mode* gate on the HIGH tier. Bulk downlink has to be entered
deliberately, so an S13 image dump or an S15 delayed-telemetry playback is a planned pass
activity rather than something the spacecraft drifts into. Entering DOWNLINK mode does
not by itself change the rate; leaving it does not clear the commanded tier — the ACTIVE
tier simply falls back to LOW until DOWNLINK mode is re-entered.

**Example S8 Command Sequence — Imaging Pass:**

```
1. Enable AOCS fine point mode
   S8 func_id=0, mode=5

2. Set payload to imaging mode
   S8 func_id=26 (PAYLOAD_SET_MODE), mode=2

3. Begin imaging (assuming ground target in view)
   S8 func_id=28 (PAYLOAD_CAPTURE), scene_id=42, lines=4096
   S5 event: IMAGING_START (0x0600)

4. Check image storage
   S3 request SID=5 (HK_Payload)
   Response: storage_used=1200 MB, SNR=35 dB

5. Download image
   S8 func_id=29 (PAYLOAD_DOWNLOAD_IMAGE), scene_id=42
   S13 request transfer (see Service 13)
```

---

## Service 9: Time Management

**Purpose:** Synchronize spacecraft time with ground time.

**Implementation:** Handled by OBDH and TTC subsystems.
- OBC maintains internal UTC clock
- TTC can correlate command timestamp with spacecraft time
- Time stamps on all TM packets (8 bytes: seconds + microseconds)

---

## Service 11: Activity Scheduling

**Purpose:** Schedule telecommands for execution at specific times.

**Subtypes:**

| Subtype | Direction | Meaning | Wire format |
|---------|-----------|---------|-------------|
| 4 | TC | **Insert** a time-tagged command | `exec_time_cuc (uint32)` + the complete embedded TC packet |
| 7 | TC | Delete ONE scheduled command | `cmd_id (uint16)` |
| 9 | TC | Disable the schedule (queue retained, nothing fires) | — |
| 11 | TC | Delete ALL scheduled commands | — |
| 13 | TC | Enable the schedule | — |
| 17 | TC | Request detailed schedule report | — |
| 5 | TM | Insert acknowledgement | `cmd_id (uint16)` — the handle for subtype 7 |
| 18 | TM | Detailed schedule report (response to 17) | see below |

**Insert is subtype 4, not subtype 11.** Earlier revisions of this document
described "S11 subtype 11 (insert scheduled activity)". Subtype 11 is
*delete-all* — sending it in place of an insert silently wipes the whole
on-board schedule. See `ServiceDispatcher._handle_s11`.

**Example:** Command imaging to occur at AOS (acquisition of signal):

```
TC (from MCS):
  S11 subtype 4 (insert time-tagged command)
  Data: exec_time_cuc (uint32, absolute CUC seconds — TLE propagation + AOS prediction)
      + the whole TC packet to run, e.g. S8.1 func_id=28 (PAYLOAD_CAPTURE),
        scene_id=42, lines=4096

Response:
  TM(11,5): cmd_id (uint16) — the on-board handle for this activity
  S1.1 / S1.7 for the S11 telecommand itself (it was accepted and executed)
  [At the scheduled CUC time]
  The embedded TC is dispatched exactly as if uplinked, so it produces
  its own S1.1 / S1.7 and any S5 events (e.g. IMAGING_START).
```

**TM(11,18) — detailed schedule report** (extended by defect #72 so the report
carries command *identity*, not just a timestamp):

```
Header: count (uint16) + schedule_enabled (uint8) + next_id (uint16)
Record: id (uint16) + exec_time_cuc (uint32) + apid (uint16)
      + service (uint8) + subtype (uint8) + data_len (uint8)
      + n_bytes (uint8) + data[n_bytes]
```

The embedded packet itself stays on board; only its identity and up to 255
bytes of its data field are reported.

**Constraints:**
- The scheduler queue is **unbounded** (`TCScheduler._commands` is a plain
  dict). Earlier revisions quoted a 1000-activity cap; there is no such limit.
- **Absolute** CUC time scheduling only. There is no relative/offset subtype —
  the ground computes the absolute execution time.
- Command IDs are allocated monotonically from 1 and are not reused after
  deletion.
- Execution verification: the *scheduled* command emits its own S1 reports when
  it fires. A scheduled dump that fires outside a contact window still consumes
  the store, and its packets are lost (see Service 15).

**Ground-side path.** You do not have to hand-assemble the TC(11,4) data field:
the MCS exposes `POST /api/schedule/upload-s11`, which converts a planner
activity's command steps into time-tagged S11 inserts at the planned execution
time (defect #46). Telemetry-check and go/no-go steps cannot be time-tagged and
are dropped with an explicit warning — the on-board scheduler executes
commands, not decisions. Use an OBCP (Service 18) when the sequence needs to
wait on or branch upon telemetry.

---

## Service 12: On-Board Monitoring

**Purpose:** Autonomous parameter threshold monitoring. Violations generate a
TM(12,9) check-transition report, an S5 event, and may trigger an S19 action.

**Subtypes:**

| Subtype | Direction | Meaning | Wire format |
|---------|-----------|---------|-------------|
| 1 / 2 | TC | Enable / disable monitoring globally | — |
| 3 / 4 | TC | Enable / disable ONE definition | `mon_id (uint16)` |
| 6 | TC | Add a definition | 13 bytes: `mon_id(u16) + param_id(u16) + check_type(u8) + low(f32) + high(f32)`. An 11-byte legacy form omits `mon_id` and reuses `param_id`. |
| 7 | TC | Delete a definition | `mon_id (uint16)` |
| 12 | TC | Report all definitions → TM(12,13) | — |
| 9 / 10 | TM | Check transition **entered** / **returned to nominal** | emitted per transition |
| 13 | TM | Definition report (response to 12) | — |

**check_type:** `0` = absolute (the value must stay inside `[low, high]`),
`1` = delta (the *change* since the previous check must stay inside `[low, high]`;
the first sample after a start or a power-off has no baseline and cannot violate).
Both are implemented; **all 38 shipped definitions use check_type 0**.

**Monitoring Rules: 38 configured** (`configs/eosat1/monitoring/s12_definitions.yaml`
— 30 WARNING, 8 ALARM)

| Parameter | Rule | Threshold | Event | S19 Action |
|-----------|------|-----------|-------|-----------|
| `eps.bat_soc` | Low | < 20% | 0x0101 | Reduce loads |
| `eps.bat_soc` | Critical | < 10% | 0x0102 | Emergency shed |
| `eps.bus_voltage` | Low | < 27V | 0x0103 | Payload off |
| `eps.bus_voltage` | Critical | < 25V | 0x0104 | All non-essential off |
| `aocs.rw1_speed` | Overspeed | > ±5000 RPM | 0x0201 | Disable wheel 1 |
| `aocs.total_momentum` | Saturation | > 90 Nms | 0x0205 | Trigger desaturation |
| `aocs.att_error` | High | > 1 deg | 0x0206 | Safe mode |
| `tcs.fpa_temp` | Overtemp | > -3°C | 0x0604 | Payload off |
| `ttc.link_margin` | Critical | < 3 dB | 0x0507 | Increase TX power |
| `ttc.pa_temp` | Shutdown | > 65°C | 0x0509 | PA off |

**Violation Reporting** — violations are **edge-triggered**: one report when the
parameter *enters* violation and one when it *clears*. Nothing is emitted while
it stays in violation.

```
When a parameter crosses a threshold (OK → violated):
  1. S12 violation detected by ServiceDispatcher.check_monitoring()
     (skipped entirely if the parameter's source subsystem is unpowered)
  2. TM(12,9) check-transition report "limit entered" — the real monitoring
     telemetry the ground decodes (added by defect #69)
  3. S5 event generated, event_id = 0x9000 | (param_id & 0x0FFF)
  4. S19 rule triggered (if one matches the event id, or the parameter id in
     the parameter namespace — trigger_kind disambiguates the two)
  5. S8 command executed autonomously
  6. Event logged in S15 TM storage

When it recovers (violated → OK):
  1. TM(12,10) check-transition report "returned to nominal"
  2. S5 event, event_id = 0xA000 | (mon_id & 0x0FFF), severity INFO
```

The event id is `0x9000 | param_id`, **not** `0x8000 + param_id`.

**Example Flow — Battery SoC Warning:**

```
Battery SoC drops from 22% to 18%
  ↓
S12 rule: "EPS_BATTERY_SOC_WARNING" detects < 20%
  ↓
S5 event: 0x0101 (BATTERY_SOC_WARNING) generated
  ↓
S19 rule: "Battery SoC critical → Payload off" matches
  ↓
S8 command: func_id=26, mode=0 (PAYLOAD_SET_MODE off)
  ↓
S1 verification: Execution complete
  ↓
S5 event: 0x0602 (PAYLOAD_MODE_CHANGE) generated
  ↓
MCS displays alert: Battery critical, Payload disabled
```

---

## Service 13: Large Data Transfer

**Purpose:** Efficient block-based download of large telemetry (payload images).

**Session Management:**

```
1. Ground: S13 subtype 1 (initiate transfer)
   Parameters: transfer_id, data_type (image), block_size
   Response: S1.1 (accepted), TM packet with transfer_id

2. Ground: S13 subtype 3 (request data block)
   Parameters: transfer_id, block_number
   Response: TM packet with 4 KB of image data + CRC

3. Ground: S13 subtype 3 (repeat for all blocks)
   Image size: 2 MB → 512 blocks × 4 KB

4. Ground: S13 subtype 5 (end transfer)
   Parameters: transfer_id
   Response: S1.7 (transfer complete)
```

**Transfer Protocol:**

| Phase | Subtype | Direction | Purpose |
|-------|---------|-----------|---------|
| Init | 1 | TC → — | Start transfer session |
| Data | 3 | TC → TM | Request/receive data block |
| End | 5 | TC → — | Finalize transfer |
| Abort | 7 | TC → — | Cancel transfer (error recovery) |

**Example Flow — Image Download:**

```
Ground: S13.1 initiate transfer
  Parameters: transfer_id=1, data_type=image_id_42, block_size=4096
  Response: S1.1 (accepted)

Ground: S13.3 request block 0
  Parameters: transfer_id=1, block_num=0
  Response: TM (4096 bytes of image data + CRC)

Ground: [verify CRC, store block]

Ground: S13.3 request block 1
  ...repeat for blocks 1-511...

Ground: S13.5 end transfer
  Parameters: transfer_id=1
  Response: S1.7 (transfer complete, total_bytes=2097152)

Result: 2 MB image downloaded in ~10 seconds over 1 Mbps link
```

**Features:**
- Up to 16 concurrent transfer sessions
- Block size: 256 bytes to 64 KB (configurable)
- CRC-32 per block (automatic error detection)
- Resume capability (skip already-downloaded blocks)
- Transfer timeout: 60 seconds idle
- Maximum transfer size: 256 MB per session

---

## Service 15: TM Storage

**Purpose:** Onboard storage and replay of telemetry packets.

**Structure (as implemented):**

```
4 stores:
  Store 1: HK_Store       — circular,  cap 5000   packets (routed from S3 HK)
  Store 2: Event_Store    — linear,    cap 1000   packets (routed from S5 events)
  Store 3: Science_Store  — linear,    cap 10000  packets (catch-all: payload, science, etc.)
  Store 4: Alarm_Store    — linear,    cap 500    packets (severity ≥ ALARM S5 events)

Routing: Service 3 (HK) → Store 1; Service 5 (Events) → Store 2 and (if severity≥2) Store 4;
         all other TM → Store 3.
Circular store 1 overwrites oldest packet when full. Stores 2–4 stop recording
on overflow and set an overflow flag reported by the status query.
All stores are enabled at boot; S15.1/S15.2 only toggle the enable flag.
```

**Commands:**

### S15 Subtype 1 — Enable Store
```
Parameter: store_id (uint8)
Effect: Allows packets to be recorded into the store. (Stores are already enabled
        at boot, so this is only useful after S15.2.)
```

### S15 Subtype 2 — Disable Store
```
Parameter: store_id (uint8)
Effect: Stops recording into the store. Existing packets are retained.
```

### S15 Subtype 9 — Dump Store (paced)
```
Parameter: store_id (uint8)
Effect: Queues ALL packets currently in the store for paced downlink. Packets
        are released over multiple simulation ticks at the ACTIVE rung of the
        TM data-rate ladder — 16 kbps at LOW, 128 kbps at HIGH (and 1 kbps in
        beacon mode, where a dump makes no sense). Raise the rate to HIGH
        first: func 79 to enter DOWNLINK mode, then func 65 with rate=1. The
        store is automatically cleared once the dump completes.
Response: S1.5 progress report indicating the number of packets queued.
Downlink behaviour:
  - If the downlink is active when a packet is released, it reaches the MCS.
  - If the downlink is NOT active, that packet is LOST (the radio would have
    transmitted into the void); the dump continues consuming the store.
  - The simulator's override-passes flag forces downlink_active=True and will
    therefore deliver all dumped packets regardless of orbital contact.
  - Time-tagged dumps (via S11 activity scheduling) work identically: if the
    scheduled dump fires outside a contact window, the packets are lost.
```

### S15 Subtype 11 — Delete Store
```
Parameter: store_id (uint8)
Effect: Clears all packets in the store, resets overflow flag and timestamps.
```

### S15 Subtype 13 — Status Request
```
Parameters: none
Response: S15.14 status report listing all stores with:
            - store_id (uint8)
            - packet count (uint16)
            - capacity (uint16)
            - enabled flag (uint8)
```

### S15 Subtype 14 — Status Report
```
TM response to S15.13. See format above.
```

**Notes and limitations:**
- There is no per-packet time-range filter on dumps; S15.9 dumps the entire store.
- There is no retention/TTL command; expiry is only by linear overflow or S15.11 clear.
- Dump pacing follows the current `ttc.tm_data_rate`. Switching data rate mid-dump
  (func_id 65, TTC_SET_DATA_RATE) will immediately change the release rate.

---

## Service 17: Connection Test

**Purpose:** Validate command link (NOOP echo test).

**Commands:**

### S17 Subtype 1 — Connection Test
```
Parameters: none used. Any data field is accepted and IGNORED.
Response:  S1.1 (accepted) + TM(17,2) Connection Test Report with an EMPTY
           data field (only the CUC timestamp in the secondary header).
```

This is a connection test, **not** an echo: `build_connection_test_report()`
(`packages/smo-common/src/smo_common/telemetry/tm_builder.py`) packs
`data=b''`. Do not expect uplinked bytes to come back.

**Use Case:** Verify uplink connectivity before critical commanding — the
arrival of TM(17,2) proves the OBC accepted and executed a telecommand.

---

## Service 18: On-Board Control Procedures

**Purpose:** Load, start, stop and monitor OBCPs — small step programs the OBC
runs autonomously, without ground in the loop. The step interpreter lives in
`ObcpManager` (`packages/smo-simulator/src/smo_simulator/obcp.py`) and is ticked
by the engine main loop. Shipped procedures live in `configs/eosat1/obcp/`
(pass preparation, safe-mode entry, payload checkout).

**Subtypes:**

| Subtype | Direction | Meaning | Wire format |
|---------|-----------|---------|-------------|
| 1 | TC | Direct-load a procedure | `proc_id` + name + serialised steps (see `ObcpManager.parse_load_blob`) |
| 2 | TC | Delete a procedure | `proc_id (uint16)` |
| 3 | TC | Activate (start) a procedure | `proc_id (uint16)` + optional `param (uint8)` |
| 4 | TC | Stop / abort a procedure | `proc_id (uint16)`; `0` = stop all |
| 6 | TC | Request procedure status → TM(18,7) | — |
| 7 | TM | Procedure status report | `count(u8)` + per procedure `proc_id(u16) + status(u8) + current_step(u8) + n_steps(u8)` |

**Failures are reported, not swallowed:** a load whose blob will not parse, a
load into a full store, a delete/stop of a procedure that is not there, and a
start of a procedure that is not loaded or is already running each produce an
S1.8 execution-failure report (error codes 0x0003 bad argument / 0x0007 unknown
object). See `docs/OBCP_GUIDE.md` for authoring procedures.

---

## Service 19: Event-Action

**Purpose:** Autonomous response to events without ground intervention.

**Subtypes:**

| Subtype | Direction | Meaning | Wire format |
|---------|-----------|---------|-------------|
| 1 | TC | Add an event-action definition | 6 bytes: `ea_id(u16) + trigger_kind(u8) + trigger_id(u16) + action_func_id(u8)`. A 4-byte legacy form (`ea_id(u16) + event_type(u8) + action_func_id(u8)`) is still accepted but cannot express any real EOSAT-1 event id — all are ≥ 0x0100. |
| 2 | TC | Delete a definition | `ea_id (uint16)` |
| 4 / 5 | TC | Enable / disable a definition | `ea_id (uint16)` |
| 8 | TC | Report all definitions → TM(19,128) | — |
| 128 | TM | Definition report | `count(u16)` + per rule `ea_id(u16) + trigger_kind(u8) + trigger_id(u16) + action_func_id(u8) + enabled(u8)` |

**`trigger_kind` matters.** S5 event ids and S12-monitored parameter ids both
live in 0x0000–0x0FFF, so an id alone is ambiguous; matching requires the id
*and* the namespace to agree. Before defect #70 the id alone was matched and 17
of the 26 shipped rules dual-triggered.

**An action is exactly one parameter-less S8 function call** — no sequences, no
arguments, no delays, no conditionals. Use an OBCP (Service 18) when you need a
sequence.

**Bootloader gate:** funcs 52, 53, 55 and 56 (reboot, OBC unit switch, boot app,
boot inhibit) are refused on the autonomous S19 path while the OBC is in the
bootloader, so no rule can boot or reboot the OBC behind the operator's back.

**Rules: 26 configured** (`configs/eosat1/monitoring/s19_rules.yaml`)

> **Known open item:** 13 of the 26 shipped rules dispatch an `action_func_id`
> that does not match their `description` (e.g. ea 1001 "payload off" dispatches
> func 10, which is `AOCS_SLEW_TO`). The mechanism is correct; the *content* is
> under review. The table below shows intended behaviour, not current wiring.

| Rule ID | Trigger | Action | Purpose |
|---------|---------|--------|---------|
| 1001 | Bus undervoltage (0x0103) | Payload off | Power conservation |
| 1002 | Battery SoC critical (0x0102) | FPA cooler off | Power conservation |
| 2001 | Momentum saturation (0x0205) | Desaturate wheels | AOCS control |
| 2002 | Star tracker 1 blind (0x0203) | Switch to ST2 | Attitude control |
| 3001 | FPA overtemp (0x0604) | Payload off | Thermal protection |
| 4001 | Link margin critical (0x0507) | Increase TX power | Link recovery |
| 5001 | OBC reboot (0x0301) | Acknowledge | System monitoring |
| 6001 | Storage full (0x0609) | Stop imaging | Data management |

**Execution Flow:**

```
Event triggered (S5):
  ↓
S19 rule matches (event_type in rule database)
  ↓
Check rule enabled flag
  ↓
Execute action_func_id (S8 function)
  ↓
S1 verification generated
  ↓
New event emitted (LOAD_SHED_ACTIVATED, etc.)
  ↓
MCS alerts operator (informational)
```

**Configuration Example:**

```yaml
s19_rules:
  - ea_id: 1001
    event_type: 0x0103  # Bus undervoltage
    action_func_id: 26  # PAYLOAD_SET_MODE OFF
    description: "Undervoltage -> Payload off"
    enabled: true
```

---

## Service 20: Parameter Management

**Purpose:** Adjust telecommand parameters (gains, offsets, constants), and
read any catalogued parameter on demand.

> **Subtype allocation — a deliberate deviation from ECSS-E-ST-70-41.**
>
> | Subtype | This suite | ECSS-E-ST-70-41 |
> |---|---|---|
> | **20,1** | **set** parameter (`param_id` + float32 value) | report parameter values *request* |
> | **20,2** | parameter value report (downlink) | parameter values report |
> | **20,3** | **get** parameter (`param_id` → 20,2 report) | set parameter values |
>
> The implementation is `service_dispatch._handle_s20`, and `tc_catalog.yaml`,
> the MCS builder, the procedures and `tools/procedure_audit.py` all follow
> **this table**, not the standard. Until Phase G the *procedures* followed
> ECSS while the wire followed the implementation, so an operator working a
> card sent the wrong subtype in 79 places across ten files — the card said
> "GET_PARAM (Service 20, Subtype 1)" and the spacecraft set the parameter to
> whatever happened to be in the buffer.
>
> The deviation is not defended as good design; it is recorded because
> re-allocating a live wire protocol across six consumers is a larger change
> than the confusion it removes, and `tests/test_content/test_procedure_audit_gate.py`
> now pins every consumer to one answer.

**Use Cases:**
- Adjust AOCS control gains
- Update attitude error deadband
- Modify EPS charging thresholds
- Calibrate sensor zero-points

**Example:** Change AOCS attitude deadband

```
TC (from MCS):
  S20 subtype 1 (set parameter)
  param_id: 0x2064 (aocs.att_deadband)
  value: 0.5 (degrees)

Response:
  S1.1: Parameter set accepted
  S1.7: Parameter update complete
  S3 HK: New value reflected in next report
```

---

## Commanding from the MCS

**Step-by-step example: Execute imaging pass**

### 1. Check Power Budget
```
MCS: S3 request HK_EPS (SID=1)
Response:
  Battery SoC: 78%
  Power generation: 1200 W
  Power consumption: 850 W
  Load shedding stage: 0 (no shedding)
Status: OK to proceed
```

### 2. Set Attitude
```
MCS: S8 func_id=0 (AOCS_SET_MODE), param=5 (fine_point)
Response:
  S1.1: Accepted
  S1.3: Execution start
  S1.7: Completion
  S5: AOCS_MODE_CHANGE (0x0200)
```

### 3. Enable Imaging
```
MCS: S8 func_id=26 (PAYLOAD_SET_MODE), param=2 (imaging)
Response:
  S1.1: Accepted
  S1.7: Completion
  S5: PAYLOAD_MODE_CHANGE (0x0602)
```

### 4. Capture Image
```
MCS: S8 func_id=28 (PAYLOAD_CAPTURE), scene_id=42, lines=4096
Response:
  S1.1: Accepted
  S1.3: Execution start
  S5: IMAGING_START (0x0600)
  [60 seconds of imaging]
  S5: IMAGING_STOP (0x0601)
  S1.7: Completion
```

### 5. Download Image
```
MCS: S13 subtype 1 (initiate transfer), transfer_id=1
Response:
  S1.1: Transfer initiated

MCS: S13 subtype 3 (request blocks 0-511)
Response:
  512 × TM packets, each 4 KB image data
  [Block download continues in background]

MCS: S13 subtype 5 (end transfer)
Response:
  S1.7: Transfer complete
```

### 6. Verify Status
```
MCS: S3 request HK_Payload (SID=5)
Response:
  Storage used: 1200 MB (image added)
  FPA temp: -8°C (nominal)
  Cooler: ON
  Compression ratio: 4.2:1
```

---

## MCS Command Dialog (Text Example)

```
MCS> list_commands AOCS
Available AOCS commands:
  func 0:  AOCS_SET_MODE              (mode 0-8)
  func 1:  AOCS_DESATURATE            (-)
  func 10: AOCS_SLEW_TO               (q_x, q_y, q_z, q_w, rate_dps)
  func 11: AOCS_CHECK_MOMENTUM        (-)
  func 12: AOCS_BEGIN_ACQUISITION     (-)

MCS> send_command AOCS 10 0.0 0.707 0.0 0.707 5.0
Command: AOCS_SLEW_TO
  Parameters: q_x=0.0, q_y=0.707, q_z=0.0, q_w=0.707, rate_dps=5.0
  Target: Body +Z axis pointing to spacecraft +X axis
  Rate: 5 deg/sec

Status: S1.1 Acceptance report received
Status: S1.3 Execution start
Status: S1.5 Progress (step 1/3)
Status: S1.5 Progress (step 2/3)
Status: S1.5 Progress (step 3/3)
Status: S1.7 Execution complete (result=OK)

MCS> query_telemetry aocs.att_q1 aocs.att_q2 aocs.att_q3 aocs.att_q4
AOCS Attitude:
  q1 (X): 0.002
  q2 (Y): 0.706
  q3 (Z): -0.004
  q4 (W): 0.708
Status: Slew complete, fine point attitude achieved
```

---

## Error Handling

**Common Error Codes:**

| Code | Meaning | Recovery |
|------|---------|----------|
| 0x0001 | Invalid command format | Check parameter types and ranges |
| 0x0002 | Unsupported function | Verify function ID is implemented |
| 0x0003 | Command rejected (safety) | Check spacecraft state, power, thermal |
| 0x0004 | Hardware failure | Check FDIR status, may require recovery |
| 0x0005 | Execution failure | Check S1.4 report for details |
| 0x0006 | Resource unavailable | Wait for resource (scheduler, transfer slot) |
| 0x0007 | Invalid parameter range | Adjust parameter value within limits |
| 0x0008 | Timeout | Command execution exceeded time limit |

**Example — Safe Mode Transition Failed:**

```
MCS: Send S8 func_id=0 (AOCS_SET_MODE), mode=1 (safe)

Response:
  S1.2: Acceptance failure (error_code=0x0003)
  Message: "AOCS mode transition rejected: wheels not desaturated"

Troubleshooting:
  1. Check momentum saturation
  2. Send AOCS_DESATURATE (func_id=1)
  3. Wait for DESATURATION_COMPLETE event
  4. Retry AOCS_SET_MODE to safe
```

---

## Quick Reference — Common Command Sequences

### Safe Mode Recovery (after anomaly)
```
1. S8 func_id=26 (PAYLOAD_SET_MODE, mode=0 → payload off)
2. S8 func_id=12 (AOCS_BEGIN_ACQUISITION)
3. S8 func_id=0 (AOCS_SET_MODE, mode=1)
4. Verify S5: SAFE_MODE_ENTRY
```

### Load Shedding (power emergency)
```
There is ONE load-shed function; the stage is its argument.
1. S8 func_id=25 (EPS_EMERGENCY_LOAD_SHED), stage=1
2. S8 func_id=25 (EPS_EMERGENCY_LOAD_SHED), stage=2
3. S8 func_id=25 (EPS_EMERGENCY_LOAD_SHED), stage=3
4. Verify S5: LOAD_SHED_STAGE_N events
(func_id 23 is EPS_SET_CHARGE_RATE and 24 is EPS_SOLAR_ARRAY_DRIVE — earlier
 revisions of this document mislabelled 23/24/25 as three shed stages.)
Alternatively S8 func_id=83 (EPS_SET_MODE), mode=1 (safe) or 2 (emergency)
sheds the two least-essential loads, or all of them, in one command.
```

### Momentum Desaturation
```
1. S8 func_id=1 (AOCS_DESATURATE)
2. S3 request HK_AOCS (SID=2)
3. Monitor total_momentum until < 80 Nms
4. Verify S5: DESATURATION_COMPLETE
```

### Imaging Pass (automated)
```
1. S11 subtype 4 (insert time-tagged command)   ← subtype 4, NOT 11
   Data: exec_time_cuc (absolute CUC, from TLE propagation + AOS prediction)
       + the embedded TC: S8.1 func_id=28 (PAYLOAD_CAPTURE)
   Reply: TM(11,5) with the on-board cmd_id
2. Optional: S11 subtype 17 → TM(11,18) to read the schedule back and confirm
   the queued command's apid/service/subtype before the pass
3. Spacecraft executes automatically at the scheduled time
4. Ground downloads via S13 after LOS
(Subtype 11 is DELETE-ALL; sending it here wipes the on-board schedule.)
```

---

## Implementation Notes

- All time stamps are UTC (coordinated universal time)
- Spacecraft time is maintained by OBDH, synchronous with ground
- Events are time-ordered and logged to S15 storage
- S19 actions execute immediately (no ground approval required), except the
  OBC-critical funcs 52/53/55/56, which are blocked while the OBC is in the
  bootloader
- S12 violations are **edge-triggered**: one TM(12,9) + S5 event on entry and
  one TM(12,10) + S5 event on recovery. They do NOT repeat every check interval
  while the parameter stays out of limits
- S12 checks are suppressed entirely for parameters whose source subsystem is
  unpowered, so a cold sensor does not generate a limit alarm
- Telemetry can be stored onboard (S15) if downlink unavailable

---

## References

- ECSS-E-70-41C: Telemetry and Telecommand Packet Utilization Standard
- EOSAT-1 Configuration: `configs/eosat1/commands/tc_catalog.yaml`
- Event Definitions: `configs/eosat1/events/event_catalog.yaml`
- Monitoring Rules: `configs/eosat1/monitoring/s12_definitions.yaml`
- Event-Action Rules: `configs/eosat1/monitoring/s19_rules.yaml`
- On-Board Control Procedures: `configs/eosat1/obcp/`, `docs/OBCP_GUIDE.md`
- **Authoritative implementation:**
  `packages/smo-simulator/src/smo_simulator/service_dispatch.py` — when this
  document and the dispatcher disagree, the dispatcher is right.

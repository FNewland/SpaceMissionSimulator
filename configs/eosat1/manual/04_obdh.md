# EOSAT-1 On-Board Data Handling (OBDH)

**Document ID:** EOSAT1-UM-OBDH-005
**Issue:** 2.0
**Date:** 2026-03-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Overview

The On-Board Data Handling (OBDH) subsystem provides central processing, data storage,
telecommand decoding, telemetry formatting, and time management for the EOSAT-1 spacecraft.
It consists of a cold-redundant On-Board Computer pair (OBC-A and OBC-B) with integrated mass
memory, a hardware watchdog, and interfaces to all other subsystems via a dual CAN bus.

## 2. Architecture

### 2.1 On-Board Computer (Cold-Redundant A/B)

EOSAT-1 carries two identical OBCs in a cold-redundant configuration:

| Parameter            | OBC-A (Primary)               | OBC-B (Redundant)             |
|----------------------|-------------------------------|-------------------------------|
| Processor            | LEON3-FT (rad-hardened SPARC) | LEON3-FT (rad-hardened SPARC) |
| Clock Frequency      | 80 MHz                        | 80 MHz                        |
| RAM                  | 256 MB (EDAC protected)       | 256 MB (EDAC protected)       |
| Mass Memory          | 2 GB (Flash, EDAC protected)  | 2 GB (Flash, EDAC protected)  |
| Operating System     | RTEMS (real-time)             | RTEMS (real-time)             |
| Data Bus             | Dual CAN bus (A + B)          | Dual CAN bus (A + B)          |
| PDM Line             | Unswitchable (UNSW-1)         | Switchable (SW-6)             |

OBC-A is powered via an unswitchable PDM line, ensuring it is always operational after
separation. OBC-B is on a switchable PDM line and is normally powered off (cold standby).
Both OBCs share access to the dual CAN bus for subsystem communication.

### 2.2 Watchdog Timer

The hardware watchdog requires a periodic heartbeat from the OBC application software. If
the heartbeat is not received within 10 seconds, the watchdog initiates an OBC reset. After
three consecutive watchdog resets, the OBC boots into BOOTLOADER mode and awaits ground
intervention.

### 2.3 Mass Memory Management

Mass memory is used for:

- **Payload data storage**: Image files from the multispectral imager.
- **Telemetry archive**: Stored housekeeping and event packets for later downlink.
- **Software uploads**: Patched application images uploaded from ground.

The `mmm_used` parameter (0x0303) reports current memory utilisation as a percentage.

## 3. OBC Modes

| Mode ID | Mode Name    | Description                                          |
|---------|--------------|------------------------------------------------------|
| 0       | NOMINAL      | Full functionality, all subsystems active             |
| 1       | SAFE         | Reduced operations, non-essential loads powered down  |
| 2       | BOOTLOADER   | Minimal boot image, restricted commands, beacon only  |

### 3.1 Mode Transitions

```
Power-on --> BOOTLOADER --> NOMINAL
                 |             |
                 |         SAFE <--> NOMINAL
                 |             |
                 +<--- 3x WD --+
                 |
                 +<--- OBC crash/reset
```

- **Power-on to BOOTLOADER**: Always. The OBC always boots into BOOTLOADER first.
- **BOOTLOADER to NOMINAL**: Ground commanded via `OBC_SET_MODE` (mode=0) after self-test.
  The bootloader does NOT auto-transition to application software — ground command is required.
- **NOMINAL to SAFE**: Ground commanded via `OBC_SET_MODE` (mode=1) or autonomous FDIR.
- **SAFE to NOMINAL**: Ground commanded via `OBC_SET_MODE` (mode=0) after anomaly clearance.
- **Any to BOOTLOADER**: Three consecutive watchdog resets or ground command (mode=2).
- **OBC crash to BOOTLOADER**: An unrecoverable software exception causes a reset, and after
  3 consecutive resets, the OBC remains in BOOTLOADER awaiting ground intervention.

### 3.2 Bootloader State Machine

The BOOTLOADER mode is a minimal operating environment designed for spacecraft recovery.
It provides limited functionality to ensure the spacecraft remains commandable.
Telemetry in BOOTLOADER is routed from the dedicated **bootloader APID (0x002)** —
distinct from the application APID (0x001). Only **SID 11 (Beacon)** is emitted;
all onboard TM stores, AOCS, TCS, and payload operations are unavailable until the
application software has booted (phase ≥ 4). An OBC reboot reverts the spacecraft
to this bootloader/beacon-only state automatically.

#### Bootloader Capabilities

| Capability              | Available | Notes                                      |
|-------------------------|-----------|--------------------------------------------|
| Beacon telemetry (SID 11) | Yes     | Minimal HK: OBC temp, voltage, mode, uptime |
| Full housekeeping       | No        | Application-level HK not available         |
| Command reception       | Yes       | Via dedicated PDM command channel           |
| Command set             | Restricted | Only essential commands accepted           |
| AOCS control            | No        | No attitude control in BOOTLOADER          |
| Payload operations      | No        | Payload power off                          |
| Subsystem commanding    | No        | Only OBC-internal commands                 |

#### Restricted Command Set in BOOTLOADER

Only the following commands are accepted in BOOTLOADER mode:

| Command              | Purpose                                          |
|----------------------|--------------------------------------------------|
| OBC_SET_MODE (0)     | Transition to APPLICATION/NOMINAL                |
| OBC_SET_MODE (2)     | Stay in BOOTLOADER (no-op)                       |
| SET_TIME             | Synchronise onboard clock                        |
| HK_REQUEST (SID 11)  | Request beacon housekeeping packet               |
| SW_UPLOAD            | Upload new application software image            |
| SW_ACTIVATE          | Activate uploaded software image                 |

All other commands are rejected with a `tc_reject_count` increment.

#### Beacon Packet (SID 11)

The beacon packet transmitted in BOOTLOADER mode contains a minimal set of parameters:

| Parameter       | Content                               |
|-----------------|---------------------------------------|
| obc_mode        | 2 (BOOTLOADER)                        |
| obc_temp        | OBC board temperature                 |
| bat_voltage     | Battery voltage                       |
| bat_soc         | Battery state of charge               |
| uptime          | Seconds since last boot               |
| reboot_count    | Total reboot count                    |
| sw_version      | Bootloader version identifier         |

This beacon is transmitted at low rate (0.01 Hz) to conserve power and bandwidth.

### 3.3 Bootloader to Application Transition

The transition from BOOTLOADER to application (NOMINAL) mode proceeds as follows:

1. Ground receives beacon packet and verifies OBC health.
2. Ground sends `OBC_SET_MODE` (mode=0).
3. OBC loads application software from flash memory.
4. Application performs power-on self-test (POST).
5. If POST passes, OBC transitions to NOMINAL mode.
6. Full housekeeping telemetry begins at 1 Hz.
7. Ground verifies all telemetry parameters are nominal.

If POST fails, the OBC remains in BOOTLOADER and generates a failure event. Ground must
then investigate (potentially upload a patched application image via `SW_UPLOAD`).

### 3.4 OBC Crash Recovery Workflow

When the OBC experiences an unrecoverable crash:

1. **Crash detected**: Hardware watchdog timeout (no heartbeat for 10 seconds).
2. **First reset**: OBC reboots into BOOTLOADER, then auto-attempts application boot.
3. **Second reset**: Same as first. `reboot_count` increments.
4. **Third reset**: OBC remains in BOOTLOADER mode permanently until ground command.
   No auto-boot attempt.
5. **Ground recovery**: During next contact, ground observes beacon packet with
   `obc_mode = 2` and elevated `reboot_count`. Ground investigates telemetry archive,
   uploads software patch if needed, and commands `OBC_SET_MODE` (mode=0) to restart.

### 3.5 Safe Mode Behaviour

In SAFE mode, the OBC:

- Disables payload operations and power.
- Commands AOCS to SAFE_POINT mode.
- Reduces housekeeping telemetry rate to 0.1 Hz.
- Activates battery heater (the only active thermal element).
- Maintains TTC link for ground commanding.

### 3.6 CAN Bus Architecture and Failure Isolation

EOSAT-1 uses a dual CAN bus (Bus A and Bus B) for internal communication between the OBC
and all subsystems:

| Bus   | Primary Role                | Subsystems Connected          |
|-------|-----------------------------|-------------------------------|
| CAN-A | Primary data bus            | All subsystems                |
| CAN-B | Redundant data bus          | All subsystems                |

Both buses carry identical traffic under nominal conditions. If CAN-A experiences a fault
(bus-off, stuck dominant, etc.), the OBC automatically switches to CAN-B for all subsystem
communication.

**CAN Bus Failure Isolation Procedure:**

1. Monitor `can_a_errors` and `can_b_errors` telemetry counters.
2. If error count on one bus exceeds threshold, OBC isolates that bus.
3. All communication continues on the remaining healthy bus.
4. Ground investigates the failed bus during next contact window.
5. If both buses fail, only the OBC internal functions remain operational — subsystem
   commanding is lost. This is a spacecraft emergency requiring immediate investigation.

## 4. Telecommand Processing

### 4.1 TC Counters

The OBDH maintains three telecommand counters:

| Param ID | Name              | Description                                |
|----------|-------------------|--------------------------------------------|
| 0x0304   | tc_recv_count     | Total telecommands received                |
| 0x0305   | tc_exec_count     | Telecommands successfully executed         |
| 0x0306   | tc_reject_count   | Telecommands rejected (CRC/auth/invalid)   |

A discrepancy between `tc_recv_count` and `tc_exec_count + tc_reject_count` indicates
a processing anomaly and should be investigated.

### 4.2 TC Authentication

All telecommands are validated using a CRC-16 checksum. Additionally, critical commands
(mode changes, heater control) require a command authentication code. Commands failing
validation are rejected and increment `tc_reject_count`.

## 5. Telemetry Generation

Housekeeping telemetry is generated at configurable rates:

| Mode      | HK Rate    | Packet Size | Content                     |
|-----------|------------|-------------|-----------------------------|
| NOMINAL   | 1 Hz       | 256 bytes   | All subsystem parameters    |
| SAFE      | 0.1 Hz     | 128 bytes   | Essential parameters only   |
| BOOTLOADER| 0.01 Hz    | 64 bytes    | OBC health only             |

The `tm_pkt_count` parameter (0x0307) tracks the total number of telemetry packets generated
since the last reboot.

## 6. Telemetry Parameters

| Param ID | Name            | Unit   | Description                          |
|----------|-----------------|--------|--------------------------------------|
| 0x0300   | obc_mode        | enum   | Current OBC mode (0/1/2)             |
| 0x0301   | obc_temp        | deg C  | OBC board temperature                |
| 0x0302   | cpu_load        | %      | OBC CPU utilisation                  |
| 0x0303   | mmm_used        | %      | Mass memory utilisation              |
| 0x0304   | tc_recv_count   | count  | Total TCs received                   |
| 0x0305   | tc_exec_count   | count  | TCs executed successfully            |
| 0x0306   | tc_reject_count | count  | TCs rejected                         |
| 0x0307   | tm_pkt_count    | count  | Total TM packets generated           |
| 0x0308   | uptime          | s      | Seconds since last OBC boot          |
| 0x030A   | reboot_count    | count  | Total OBC reboots since deployment   |

## 7. Limit Definitions

| Parameter       | Yellow Low | Yellow High | Red Low | Red High |
|-----------------|------------|-------------|---------|----------|
| cpu_load (%)    | 0          | 85          | 0       | 98       |

A sustained CPU load above 85% indicates excessive processing demand and should prompt
investigation of running tasks. Loads above 98% risk watchdog timeout.

## 8. Commands

| Command           | Service  | Parameters         | Description                        |
|-------------------|----------|--------------------|------------------------------------|
| OBC_SET_MODE      | S8,S1    | mode (0/1/2)       | Set OBC operating mode             |
| HK_REQUEST        | S3,S27   | sid=3              | Request OBDH housekeeping packet   |
| SET_TIME          | S9,S1    | cuc_seconds        | Set on-board time (CUC format)     |
| GET_PARAM         | S20,S3   | param_id           | Read individual OBDH parameter     |
| SET_PARAM         | S20,S1   | param_id, value    | Modify OBDH configuration parameter|

### 8.1 Time Management

The OBC maintains on-board time in CCSDS Unsegmented Code (CUC) format with 1-second
resolution. Time is synchronised from ground using the `SET_TIME` command. Time drift is
expected to be less than 1 ms/day due to the temperature-compensated crystal oscillator.

## 9. Operational Notes

1. After each OBC reboot, the `reboot_count` (0x030A) increments. Check `uptime` (0x0308)
   to confirm the OBC has not undergone an unexpected reset.
2. Mass memory usage (`mmm_used`) should be monitored and maintained below 90% to ensure
   sufficient space for payload data and telemetry archiving.
3. Software patches are uploaded via a dedicated memory load service and require ground
   verification before activation.
4. The OBDH housekeeping packet (SID=3) includes all parameters listed in Section 6.

---

*End of Document — EOSAT1-UM-OBDH-005*

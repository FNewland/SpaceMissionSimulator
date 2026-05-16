# EOSAT-1 Platform Acceptance Test Plan

**Document ID:** EOSAT1-TP-ATP-001
**Issue:** 1.0
**Date:** 2026-05-16
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Purpose

This document defines the complete acceptance test suite for the SMO Space Mission Simulator platform. It covers every command, PUS service, injectable failure, operational procedure, ground tool, and HK telemetry structure — verifying that each behaves as expected for a real spacecraft and is observable from the ground segment tools.

## 2. Scope

| Category | Count | Phase |
|----------|-------|-------|
| S8.1 Function Commands | 83 | 1 |
| PUS Service Subtypes | ~40 | 2 |
| Injectable Failure Modes | 42 | 3 |
| Operational Procedures | 57 | 4 |
| Ground Tool Verifications | ~25 | 5 |
| HK SID Completeness | 7 | 6 |
| **Total Test Cases** | **~254** | |

## 3. Test Environment

- Simulator running in RF mode (smo-simulator + smo-rfsim bridge + smo-mcs)
- Playwright headless browser for UI-level tests
- Pass override for continuous contact during unit-level tests
- Scripted pass windows for operational sequence tests
- All tests automated via pytest

---

## 4. Phase 1: Individual Command Tests (83 tests)

Each test sends one S8.1 command, verifies S1.1 acceptance ACK, and checks the expected telemetry parameter change.

### 4.1 AOCS Commands (func_id 0–15)

| Test ID | func_id | Command | Data (hex) | Verify Parameter | Expected |
|---------|---------|---------|------------|------------------|----------|
| CMD-AOCS-001 | 0 | set_mode(OFF) | 00 00 | aocs.mode (0x020F) | 0 |
| CMD-AOCS-002 | 0 | set_mode(DETUMBLE) | 00 02 | aocs.mode | 2 |
| CMD-AOCS-003 | 0 | set_mode(COARSE_SUN) | 00 03 | aocs.mode | 3 |
| CMD-AOCS-004 | 0 | set_mode(NOMINAL) | 00 04 | aocs.mode | 4 |
| CMD-AOCS-005 | 0 | set_mode(FINE_POINT) | 00 05 | aocs.mode | 5 |
| CMD-AOCS-006 | 0 | set_mode(DESAT) | 00 07 | aocs.mode | 7 |
| CMD-AOCS-007 | 1 | desaturate | 01 | aocs.mode | 7 (DESAT) |
| CMD-AOCS-008 | 2 | disable_wheel(0) | 02 00 | aocs.rw0_active | False |
| CMD-AOCS-009 | 3 | enable_wheel(0) | 03 00 | aocs.rw0_active | True |
| CMD-AOCS-010 | 4 | st1_power(on) | 04 01 | aocs.st1_power | True |
| CMD-AOCS-011 | 5 | st2_power(on) | 05 01 | aocs.st2_power | True |
| CMD-AOCS-012 | 6 | st_select(2) | 06 02 | aocs.st_primary | 2 |
| CMD-AOCS-013 | 7 | mag_select(B) | 07 01 | aocs.mag_select | 'B' |
| CMD-AOCS-014 | 9 | mtq_enable | 09 01 | aocs.mtq_enabled | True |
| CMD-AOCS-015 | 13 | gyro_calibration | 0D | aocs.gyro_cal_status | IN_PROGRESS |
| CMD-AOCS-016 | 15 | set_deadband(0.5°) | 0F 3F000000 | aocs.deadband_deg | 0.5 |

### 4.2 EPS Commands (func_id 16–25, 81–82)

| Test ID | func_id | Command | Data (hex) | Verify Parameter | Expected |
|---------|---------|---------|------------|------------------|----------|
| CMD-EPS-001 | 19 | power_line_on(aocs_wheels) | 13 07 | eps.power_lines.aocs_wheels | True |
| CMD-EPS-002 | 20 | power_line_off(aocs_wheels) | 14 07 | eps.power_lines.aocs_wheels | False |
| CMD-EPS-003 | 19 | power_line_on(payload) | 13 04 | eps.power_lines.payload | True |
| CMD-EPS-004 | 19 | power_line_on(htr_bat) | 13 05 | eps.power_lines.htr_bat | True |
| CMD-EPS-005 | 19 | power_line_on(ttc_tx) | 13 03 | eps.power_lines.ttc_tx | True |
| CMD-EPS-006 | 21 | reset_oc_flag(line 3) | 15 03 | eps.oc_flag[3] | False |
| CMD-EPS-007 | 25 | emergency_load_shed(1) | 19 01 | eps.load_shed_stage | 1 |
| CMD-EPS-008 | 81 | deploy_wing(both) | 51 02 | eps.wing_status | 0x03 |
| CMD-EPS-009 | 23 | set_charge_rate(2.0A) | 17 40000000 | eps.charge_rate_a | 2.0 |
| CMD-EPS-010 | 16 | set_payload_mode(OFF) | 10 00 | payload.mode | 0 |
| CMD-EPS-011 | 17 | fpa_cooler(on) | 11 01 | tcs.cooler_active | True |

### 4.3 Payload Commands (func_id 26–39)

| Test ID | func_id | Command | Data (hex) | Verify Parameter | Expected |
|---------|---------|---------|------------|------------------|----------|
| CMD-PLD-001 | 26 | set_mode(STANDBY) | 1A 01 | payload.mode | 1 |
| CMD-PLD-002 | 26 | set_mode(IMAGING) | 1A 02 | payload.mode | 2 |
| CMD-PLD-003 | 33 | set_band_config(all) | 21 0F | payload.band_mask | 0x0F |
| CMD-PLD-004 | 35 | set_detector_gain(2.0) | 23 40000000 | payload.detector_gain | 2.0 |
| CMD-PLD-005 | 36 | set_cooler_setpoint(-30°C) | 24 C1F00000 | tcs.fpa_setpoint | -30.0 |
| CMD-PLD-006 | 37 | start_calibration | 25 | payload.cal_status | IN_PROGRESS |
| CMD-PLD-007 | 38 | stop_calibration | 26 | payload.cal_status | IDLE |
| CMD-PLD-008 | 39 | set_compression(4.0) | 27 40800000 | payload.compression | 4.0 |
| CMD-PLD-009 | 28 | capture_image(45.0,10.0) | 1C 42340000 41200000 | payload.scene_count | +1 |
| CMD-PLD-010 | 29 | download_image(0) | 1D 0000 | S15 packets queued | >0 |
| CMD-PLD-011 | 30 | delete_image(0) | 1E 0000 | payload.scene_count | -1 |
| CMD-PLD-012 | 32 | get_image_catalog | 20 | S8.2 response | catalog data |
| CMD-PLD-013 | 34 | set_integration_time | 22 + 16 bytes | payload.int_times | set |
| CMD-PLD-014 | 31 | mark_bad_segment(0) | 1F 00 | payload.bad_segments | [0] |

### 4.4 TCS Commands (func_id 40–49)

| Test ID | func_id | Command | Data (hex) | Verify Parameter | Expected |
|---------|---------|---------|------------|------------------|----------|
| CMD-TCS-001 | 40 | heater_battery(on) | 28 01 | tcs.htr_bat_on | True |
| CMD-TCS-002 | 40 | heater_battery(off) | 28 00 | tcs.htr_bat_on | False |
| CMD-TCS-003 | 41 | heater_obc(on) | 29 01 | tcs.htr_obc_on | True |
| CMD-TCS-004 | 43 | fpa_cooler(on) | 2B 01 | tcs.cooler_active | True |
| CMD-TCS-005 | 44 | set_setpoint(bat,5,10) | 2C 01 40A00000 41200000 | tcs.htr_bat_on_temp | 5.0 |
| CMD-TCS-006 | 45 | auto_mode(battery) | 2D 01 | tcs.htr_bat_auto | True |
| CMD-TCS-007 | 46 | set_duty_limit(bat,80%) | 2E 01 50 | tcs.htr_bat_duty_limit | 80 |
| CMD-TCS-008 | 47 | decontamination(50°C) | 2F 42480000 | tcs.decon_active | True |
| CMD-TCS-009 | 48 | decontamination_stop | 30 | tcs.decon_active | False |
| CMD-TCS-010 | 49 | get_thermal_map | 31 | S8.2 response | thermal data |

### 4.5 OBDH Commands (func_id 50–62, 80)

| Test ID | func_id | Command | Data (hex) | Verify Parameter | Expected |
|---------|---------|---------|------------|------------------|----------|
| CMD-OBD-001 | 50 | set_mode(NOMINAL) | 32 01 | obdh.mode | 1 |
| CMD-OBD-002 | 51 | memory_scrub | 33 | obdh.scrub_count | +1 |
| CMD-OBD-003 | 52 | obc_reboot | 34 | obdh.reboot_count | +1 |
| CMD-OBD-004 | 53 | obc_switch_unit | 35 | obdh.active_obc | toggled |
| CMD-OBD-005 | 54 | obc_select_bus(A) | 36 00 | obdh.active_bus | 'A' |
| CMD-OBD-006 | 55 | obc_boot_app | 37 | obdh.sw_image | 1 |
| CMD-OBD-007 | 56 | boot_inhibit(true) | 38 01 | obdh.boot_inhibit | True |
| CMD-OBD-008 | 57 | clear_reboot_cnt | 39 | obdh.reboot_count | 0 |
| CMD-OBD-009 | 58 | set_watchdog(5000ms) | 3A 1388 | obdh.watchdog_ms | 5000 |
| CMD-OBD-010 | 59 | watchdog_enable | 3B | obdh.watchdog_enabled | True |
| CMD-OBD-011 | 60 | watchdog_disable | 3C | obdh.watchdog_enabled | False |
| CMD-OBD-012 | 61 | obc_diagnostic | 3D | S8.2 response | health data |
| CMD-OBD-013 | 62 | obc_error_log | 3E | S8.2 response | log data |
| CMD-OBD-014 | 80 | gps_time_sync | 50 | obdh.gps_sync | True |

### 4.6 TTC Commands (func_id 63–78)

| Test ID | func_id | Command | Data (hex) | Verify Parameter | Expected |
|---------|---------|---------|------------|------------------|----------|
| CMD-TTC-001 | 63 | switch_primary | 3F | ttc.mode | 0 (primary) |
| CMD-TTC-002 | 64 | switch_redundant | 40 | ttc.mode | 1 (redundant) |
| CMD-TTC-003 | 66 | pa_on | 42 | ttc.pa_on | True |
| CMD-TTC-004 | 67 | pa_off | 43 | ttc.pa_on | False |
| CMD-TTC-005 | 69 | deploy_antennas | 45 | ttc.antenna_deployed | True |
| CMD-TTC-006 | 70 | set_beacon_mode(on) | 46 01 | ttc.beacon_mode | True |
| CMD-TTC-007 | 70 | set_beacon_mode(off) | 46 00 | ttc.beacon_mode | False |
| CMD-TTC-008 | 71 | cmd_channel_start | 47 | ttc.cmd_channel_active | True |
| CMD-TTC-009 | 74 | set_modulation(BPSK) | 4A 00 | ttc.modulation_mode | 0 |
| CMD-TTC-010 | 74 | set_modulation(QPSK) | 4A 01 | ttc.modulation_mode | 1 |
| CMD-TTC-011 | 76 | ranging_start | 4C | ttc.ranging_active | True |
| CMD-TTC-012 | 77 | ranging_stop | 4D | ttc.ranging_active | False |
| CMD-TTC-013 | 78 | set_coherent_mode(on) | 4E 01 | ttc.coherent_mode | True |
| CMD-TTC-014 | 65 | set_tm_rate(64000) | 41 0000FA00 | ttc.tm_data_rate | 64000 |
| CMD-TTC-015 | 68 | set_tx_power(1.5W) | 44 3FC00000 | ttc.tx_fwd_power | 1.5 |
| CMD-TTC-016 | 75 | set_rx_gain(-80dB) | 4B C2A00000 | ttc.agc_level_db | -80.0 |

---

## 5. Phase 2: PUS Service Tests (40 tests)

### 5.1 S3 — Housekeeping

| Test ID | Subtype | Test | Expected |
|---------|---------|------|----------|
| PUS-S3-001 | 27 | One-shot HK request (SID 1) | S3.25 packet with EPS data |
| PUS-S3-002 | 27 | One-shot HK request (SID 11) | S3.25 beacon packet |
| PUS-S3-003 | 5 | Disable SID 2 | No SID 2 packets emitted |
| PUS-S3-004 | 6 | Enable SID 2 | SID 2 packets resume |
| PUS-S3-005 | 31 | Modify SID 1 interval to 5s | SID 1 emits every 5s |
| PUS-S3-006 | 1 | Create new HK definition | S1.1 ACK, new SID emits |
| PUS-S3-007 | 3 | Delete HK definition | S1.1 ACK, SID stops |

### 5.2 S5 — Event Reporting

| Test ID | Subtype | Test | Expected |
|---------|---------|------|----------|
| PUS-S5-001 | 5 | Enable event type (info) | Info events resume |
| PUS-S5-002 | 6 | Disable event type (info) | Info events suppressed |
| PUS-S5-003 | 7 | Enable all events | All severity levels active |
| PUS-S5-004 | 8 | Disable all events | No events emitted |

### 5.3 S6 — Memory Management

| Test ID | Subtype | Test | Expected |
|---------|---------|------|----------|
| PUS-S6-001 | 2 | Memory load (16 bytes) | S1.7 ACK, data written |
| PUS-S6-002 | 5 | Memory dump | S6.6 with data |
| PUS-S6-003 | 9 | Memory check (CRC) | S6.10 with checksum |

### 5.4 S9 — Time Management

| Test ID | Subtype | Test | Expected |
|---------|---------|------|----------|
| PUS-S9-001 | 1 | Set CUC time | OBC clock updated |
| PUS-S9-002 | 2 | Time report request | S9.3 with current CUC |
| PUS-S9-003 | 1 | Large time jump (>5s) | AOCS forced to SAFE_BOOT |

### 5.5 S11 — Time-Tagged Scheduling

| Test ID | Subtype | Test | Expected |
|---------|---------|------|----------|
| PUS-S11-001 | 4 | Schedule TC at T+60 | S11.5 ACK with cmd_id |
| PUS-S11-002 | 17 | List scheduled commands | S11.18 with schedule |
| PUS-S11-003 | 7 | Delete scheduled command | S1.7 ACK |
| PUS-S11-004 | 9 | Disable scheduler | Scheduled TCs not executed |
| PUS-S11-005 | 13 | Enable scheduler | Scheduled TCs execute |
| PUS-S11-006 | — | Verify timed execution | TC executes at scheduled CUC |

### 5.6 S12 — On-Board Monitoring

| Test ID | Subtype | Test | Expected |
|---------|---------|------|----------|
| PUS-S12-001 | 1 | Enable monitoring | Limit checks active |
| PUS-S12-002 | 2 | Disable monitoring | Limit checks inactive |
| PUS-S12-003 | 6 | Add limit check (bus_v < 26V) | Param monitored |
| PUS-S12-004 | 7 | Delete limit check | Monitoring removed |
| PUS-S12-005 | — | Trigger violation | S5 event generated |

### 5.7 S15 — On-Board TM Storage

| Test ID | Subtype | Test | Expected |
|---------|---------|------|----------|
| PUS-S15-001 | 1 | Enable store 0 | Storage recording |
| PUS-S15-002 | 2 | Disable store 0 | Recording stops |
| PUS-S15-003 | 9 | Dump store 0 | Paced TM packets |
| PUS-S15-004 | 11 | Delete store 0 | Store cleared |
| PUS-S15-005 | 13 | Status report | S15.14 with capacity |

### 5.8 S17, S19, S20

| Test ID | Svc | Subtype | Test | Expected |
|---------|-----|---------|------|----------|
| PUS-S17-001 | 17 | 1 | Connection test | S1.1 + S17.2 |
| PUS-S19-001 | 19 | 1 | Define event-action rule | S1.1 ACK |
| PUS-S19-002 | 19 | 4 | Enable event-action | Rule active |
| PUS-S19-003 | 19 | — | Trigger event → verify action | S8.1 auto-executed |
| PUS-S20-001 | 20 | 1 | Set parameter (bat_soc) | Value updated |
| PUS-S20-002 | 20 | 3 | Get parameter (bus_v) | S20.4 with value |

---

## 6. Phase 3: Failure Injection Tests (42 tests)

Each test: inject failure → verify detection in telemetry → send recovery command → verify recovery.

### 6.1 AOCS Failures (12 tests)

| Test ID | Failure | Injection | Detection | Recovery | Verify |
|---------|---------|-----------|-----------|----------|--------|
| FLT-AOCS-001 | rw_seizure | wheel=0 | RW0 speed=0, temp rising | func=2 disable_wheel(0) | 3-wheel mode stable |
| FLT-AOCS-002 | rw_bearing | wheel=1, mag=0.5 | RW1 vibration | func=14 ramp_down(1) | Speed reduced |
| FLT-AOCS-003 | gyro_bias | axis=0, bias=1.0 | Attitude error drift | func=13 gyro_cal | Bias corrected |
| FLT-AOCS-004 | st_blind | unit=1 | ST1 status=BLIND | Wait for recovery | ST1→TRACKING |
| FLT-AOCS-005 | st_failure | unit=1 | ST1 status=FAILED | func=6 select ST2 | ST2 primary |
| FLT-AOCS-006 | css_failure | — | css_valid=False | Mode→DETUMBLE | Rates damped |
| FLT-AOCS-007 | mag_failure | — | mag_valid=False | Mode→DETUMBLE | Gyro-only |
| FLT-AOCS-008 | mag_a_fail | — | mag_a_failed=True | func=7 select MAG-B | MAG-B active |
| FLT-AOCS-009 | mag_b_fail | — | mag_b_failed=True | func=7 select MAG-A | MAG-A active |
| FLT-AOCS-010 | css_head_fail | face=px | CSS head px dead | Other 5 heads OK | Degraded pointing |
| FLT-AOCS-011 | mtq_failure | axis=x | MTQ-X inop | Desat limited | Y/Z still work |
| FLT-AOCS-012 | multi_wheel | wheels=[0,1] | 2 wheels dead | Mode→COARSE_SUN | Stable on 2 wheels |

### 6.2 EPS Failures (8 tests)

| Test ID | Failure | Detection | Recovery | Verify |
|---------|---------|-----------|----------|--------|
| FLT-EPS-001 | solar_array_partial (A, 0.5) | Power gen drops 50% | — (degraded ops) | SoC trend |
| FLT-EPS-002 | bat_cell | Voltage drops | Safe mode | Cell isolated |
| FLT-EPS-003 | bus_short | Bus V drops sharply | Emergency load shed | Bus recovers |
| FLT-EPS-004 | overcurrent (line 3) | OC flag set, line off | func=21 reset, func=19 on | Line restored |
| FLT-EPS-005 | undervoltage (1.0) | SoC drops 10% | Load shed payload | SoC stabilizes |
| FLT-EPS-006 | overvoltage | Bus V > 30V | Shunt mode | Voltage normal |
| FLT-EPS-007 | solar_panel_loss (px) | Panel px current=0 | — (5-panel ops) | Power budget OK |
| FLT-EPS-008 | solar_array_total_loss | All power=0 | Battery only, shed | Emergency mode |

### 6.3 TTC Failures (14 tests)

| Test ID | Failure | Detection | Recovery | Verify |
|---------|---------|-----------|----------|--------|
| FLT-TTC-001 | primary_failure | Link lost | func=64 switch redundant | Link restored |
| FLT-TTC-002 | redundant_failure | Redundant dead | func=63 switch primary | Primary active |
| FLT-TTC-003 | high_ber (0.5) | BER increases | Reduce data rate | BER acceptable |
| FLT-TTC-004 | pa_overheat | PA temp rising | func=67 PA off, cool | PA temp drops |
| FLT-TTC-005 | uplink_loss | No cmd reception | Wait for pass | Uplink restored |
| FLT-TTC-006 | receiver_degrade (3dB) | RSSI drops | Increase TX power | Margin OK |
| FLT-TTC-007 | antenna_deploy_failed | Deploy stuck | — (stowed ops) | Low-rate mode |
| FLT-TTC-008 | gs_lna_degradation | Eb/N0 drops | Switch ground station | Link restored |
| FLT-TTC-009 | gs_antenna_mispoint | Signal loss | Ground tracking fix | Signal returns |
| FLT-TTC-010 | gs_feed_loss | Eb/N0 drops | Ground maintenance | Margin recovers |
| FLT-TTC-011 | gs_rfi_interference | Noise floor rises | RFI mitigation | BER drops |
| FLT-TTC-012 | gs_hpa_degradation | Uplink weak | Ground HPA repair | Uplink strong |
| FLT-TTC-013 | gs_ref_osc_drift | Demod errors | Ground osc replace | Locked clean |
| FLT-TTC-014 | gs_tracking_loss | Total signal loss | Ground tracking fix | Full recovery |

### 6.4 OBDH Failures (9 tests)

| Test ID | Failure | Detection | Recovery | Verify |
|---------|---------|-----------|----------|--------|
| FLT-OBD-001 | watchdog_reset | Reboot event | func=57 clear count | Uptime resets |
| FLT-OBD-002 | memory_errors (10) | EDAC count rises | func=51 scrub | Errors corrected |
| FLT-OBD-003 | cpu_spike (90%) | CPU load 90% | Shed background tasks | Load drops |
| FLT-OBD-004 | obc_crash | Reboot to bootloader | func=55 boot app | App running |
| FLT-OBD-005 | bus_failure (A) | Bus A status FAILED | func=54 select bus B | Bus B active |
| FLT-OBD-006 | boot_image_corrupt | Boot fails | func=55 retry + func=53 switch OBC | App boots |
| FLT-OBD-007 | memory_corruption | EDAC uncorrectable | Reboot expected | Clean restart |
| FLT-OBD-008 | memory_segment_fail | Segment marked bad | func=51 scrub | Degraded mem |
| FLT-OBD-009 | stuck_in_bootloader | Can't exit boot | func=56 uninhibit + func=55 | App boots |

### 6.5 Payload Failures (5 tests)

| Test ID | Failure | Detection | Recovery | Verify |
|---------|---------|-----------|----------|--------|
| FLT-PLD-001 | cooler_failure | FPA temp rising | func=26 mode OFF | Payload safe |
| FLT-PLD-002 | fpa_degraded | Image quality drops | Recalibrate | Compensated |
| FLT-PLD-003 | image_corrupt (3) | Bad images in catalog | func=30 delete | Catalog clean |
| FLT-PLD-004 | memory_segment_fail | Capacity reduced | func=31 mark bad | Degraded ops |
| FLT-PLD-005 | ccd_line_dropout | Stripe in images | Ground processing | Flagged |

### 6.6 TCS Failures (7 tests)

| Test ID | Failure | Detection | Recovery | Verify |
|---------|---------|-----------|----------|--------|
| FLT-TCS-001 | heater_failure (bat) | Heater inop, temp drops | Backup heater circuit | Temp stable |
| FLT-TCS-002 | cooler_failure | FPA cooler inop | func=26 payload OFF | FPA warms |
| FLT-TCS-003 | obc_thermal (5W) | OBC temp rising | Reduce CPU load | Temp stable |
| FLT-TCS-004 | sensor_drift (obc, 5°C) | OBC temp reads 5°C high | Ground calibration | Corrected |
| FLT-TCS-005 | heater_stuck_on (bat) | Heater always ON | func=20 power off line | Heater stops |
| FLT-TCS-006 | heater_open_circuit (obc) | Heater draws no power | Switch to backup | Backup heats |
| FLT-TCS-007 | temp_anomaly (bat, +20) | Battery temp alarm | Load shed + heater off | Temp drops |

---

## 7. Phase 4: Procedure Execution Tests (57 tests)

Each test loads and executes a procedure via the MCS API, verifying completion.

### 7.1 LEOP Procedures (7 tests)

| Test ID | Procedure | Key Verification |
|---------|-----------|------------------|
| PROC-LEOP-001 | First Acquisition | sw_image=1, frame_sync locked |
| PROC-LEOP-002 | Initial Health Check | All subsystem params in range |
| PROC-LEOP-003 | Initial Orbit Determination | GPS tracking, altitude 450±10 km |
| PROC-LEOP-004 | Solar Array Verification | Both wings deployed, power > 20W |
| PROC-LEOP-005 | Sun Acquisition | Rates < 0.5°/s, sun error < 30° |
| PROC-LEOP-006 | Time Synchronisation | Clock delta < 1s |
| PROC-LEOP-007 | Sequential Power-On | All lines ON, all SIDs emitting |

### 7.2 Commissioning Procedures (13 tests)

| Test ID | Procedure | Key Verification |
|---------|-----------|------------------|
| PROC-COM-001 | EPS Checkout | Bus 27-29V, SoC > 60%, power balance positive |
| PROC-COM-002 | TCS Verification | All temps in range, heater response < 3 min |
| PROC-COM-003 | AOCS Sensor Cal | Gyro bias < 0.5°/s, mag residual < 500 nT |
| PROC-COM-004 | AOCS Actuator Checkout | All 4 wheels spin ±2000 RPM |
| PROC-COM-005 | AOCS Mode Transitions | All mode transitions complete < 30s |
| PROC-COM-006 | TTC Link Verification | RSSI vs elevation profile, redundant switch |
| PROC-COM-007 | OBDH Checkout | Memory scrub clean, CAN bus healthy |
| PROC-COM-008 | FDIR Configuration | S12 limits set, S19 rules active |
| PROC-COM-009 | Payload Power On | Mode STANDBY, FPA cooling |
| PROC-COM-010 | FPA Cooler Activation | FPA temp < -25°C within 15 min |
| PROC-COM-011 | Payload Calibration | Cal status COMPLETE, coefficients updated |
| PROC-COM-012 | First Light | Image captured, downloaded, bands valid |
| PROC-COM-013 | ADCS Full Commissioning | Fine-pointing < 0.05°, all sensors validated |

### 7.3 Nominal Procedures (12 tests)
### 7.4 Contingency Procedures (26 tests)
### 7.5 Emergency Procedures (6 tests)

*(Same pattern: load → execute → verify post-condition)*

---

## 8. Phase 5: Ground Tool Tests (25 tests)

### 8.1 MCS Web UI (8 tests)

| Test ID | Test | Verification |
|---------|------|-------------|
| GT-MCS-001 | All 12 tabs load | No JS errors, content renders |
| GT-MCS-002 | WebSocket state updates | Parameters refresh every 1s |
| GT-MCS-003 | Command builder sends PUS TC | S1.1 ACK in verification log |
| GT-MCS-004 | Procedure loader | Activity types listed, load succeeds |
| GT-MCS-005 | Position switching | Access control enforced per role |
| GT-MCS-006 | Alarm journal | Alarms appear, acknowledge works |
| GT-MCS-007 | TM playback (S15) | Dashed overlay on Chart.js |
| GT-MCS-008 | Contact window timeline | 24h bar with station colors |

### 8.2 Delayed TM Viewer (4 tests)

| Test ID | Test | Verification |
|---------|------|-------------|
| GT-DTM-001 | Dump file listing | Files from workspace/dumps/ listed |
| GT-DTM-002 | Packet decode | HK packets decoded with param values |
| GT-DTM-003 | **Plot rendering** | Chart.js charts render (FIX: bundle locally) |
| GT-DTM-004 | Anomaly detection | OOL values flagged |

### 8.3 Orbit Tools (3 tests)

| Test ID | Test | Verification |
|---------|------|-------------|
| GT-ORB-001 | TLE → TC hex conversion | 6 S20.1 commands with valid param IDs |
| GT-ORB-002 | State vector → TC hex | Same output format |
| GT-ORB-003 | Orbital elements computed | a, e, i, RAAN match TLE |

### 8.4 Radio Dashboard (4 tests)

| Test ID | Test | Verification |
|---------|------|-------------|
| GT-RAD-001 | Lock LEDs reflect pipeline state | Green when locked, red when unlocked |
| GT-RAD-002 | Eb/N0 chart updates | Rolling 120-point history |
| GT-RAD-003 | Constellation diagram | I/Q points cluster at BPSK ±1 |
| GT-RAD-004 | VC activity LEDs | VC0 green during HK flow |

### 8.5 Planner (4 tests)

| Test ID | Test | Verification |
|---------|------|-------------|
| GT-PLN-001 | Contact window prediction | Matches TLE ground stations |
| GT-PLN-002 | Ground track display | SVG map renders |
| GT-PLN-003 | Power budget | Eclipse/sunlit power balance |
| GT-PLN-004 | Imaging planner | Target opportunities computed |

### 8.6 Instructor UI (2 tests)

| Test ID | Test | Verification |
|---------|------|-------------|
| GT-INS-001 | Speed control | Sim speed changes via API |
| GT-INS-002 | Failure injection panel | Inject + clear via UI |

---

## 9. Phase 6: HK Completeness Tests (7 tests)

| Test ID | SID | Params | Verification |
|---------|-----|--------|-------------|
| HK-001 | 1 (EPS) | 62 | All 62 params decoded, values in range |
| HK-002 | 2 (AOCS) | 55 | All 55 params decoded, quaternion normalized |
| HK-003 | 3 (TCS) | 14 | All 14 temps in [-40°C, +60°C] |
| HK-004 | 4 (Platform) | 24 | OBC mode valid, CAN status healthy |
| HK-005 | 5 (Payload) | 20 | Mode valid, memory usage < 100% |
| HK-006 | 6 (TTC) | 20 | RSSI > -130 dBm when in contact |
| HK-007 | 11 (Beacon) | 6 | All 6 params match bootloader state |

---

## 10. Traceability Matrix

Each procedure maps to the commands and failures it exercises:

| Procedure | Commands Used | Failures Tested |
|-----------|--------------|-----------------|
| LEOP-001 | S17.1, S8.1(55), S9.1, S3.27 | — |
| LEOP-007 | S8.1(19×5), S8.1(0) | — |
| CTG-001 | S8.1(25), S8.1(20) | undervoltage |
| CTG-007 | S8.1(2), S8.1(1) | rw_seizure |
| CTG-008 | S8.1(6), S8.1(4,5) | st_failure |
| CTG-012 | S8.1(21), S8.1(19) | overcurrent |
| EMG-001 | S8.1(0→OFF) | multiple |
| ... | ... | ... |

---

## 11. Issue Tracking

Issues discovered during testing are tracked here and fixed inline:

| # | Phase | Description | Status |
|---|-------|-------------|--------|
| 1 | GT-DTM-003 | Delayed TM viewer plots use CDN Chart.js, fail offline | TO FIX |
| 2 | Mission-Val | Commands return "unknown" status when link degraded | TO INVESTIGATE |
| 3 | Mission-Val | FDIR safe mode triggered by rapid commanding | EXPECTED (add dwell times) |

---

## 12. Execution Schedule

| Phase | Tests | Est. Implementation | Est. Runtime |
|-------|-------|-------------------|-------------|
| 1. Commands | 83 | 1 session | ~5 min |
| 2. PUS Services | 40 | 1 session | ~3 min |
| 3. Failures | 42 | 1 session | ~5 min |
| 4. Procedures | 57 | 2 sessions | ~15 min |
| 5. Ground Tools | 25 | 1 session (+ fixes) | ~10 min |
| 6. HK Completeness | 7 | 0.5 session | ~2 min |
| **Total** | **254** | **~6 sessions** | **~40 min** |

Full mission validation (browser, RF mode): additional ~30 min.

# CHANGELOG — EOSAT-1 Spacecraft Simulator

**Updated: April 2026**

This document tracks all improvements and features added to the EOSAT-1 simulator since the initial development phase.

---

## Summary of Improvements (April 2026)

| Phase | Count | Status |
|-------|-------|--------|
| **S8 Commands** | 50+ functions | Complete |
| **Telemetry Parameters** | 120+ parameters | Complete |
| **Events** | 120+ event definitions | Complete |
| **S12 Monitoring Rules** | 25+ rules | Complete |
| **S19 Event-Action Rules** | 20+ rules | Complete |
| **S1 Verification** | Full lifecycle | Complete |
| **MCS Displays** | 7 displays | Complete |
| **FDIR Cascading** | Cross-subsystem | Complete |
| **S13 Large Data Transfer** | Full implementation | Complete |

---

## Subsystem Updates

### AOCS (Attitude & Orbit Control System)

**New S8 Commands (6 functions, IDs 10-15):**
- `AOCS_SLEW_TO` (func_id 10): Execute quaternion slew maneuver with rate control (0.1-10.0 deg/s)
- `AOCS_CHECK_MOMENTUM` (func_id 11): Query reaction wheel momentum saturation status
- `AOCS_BEGIN_ACQUISITION` (func_id 12): Start automated attitude acquisition sequence (detumble→coarse→fine point)
- `AOCS_GYRO_CALIBRATION` (func_id 13): Reset gyroscope bias estimate
- `AOCS_RW_RAMP_DOWN` (func_id 14): Graceful reaction wheel speed reduction (per wheel or all)
- `AOCS_SET_DEADBAND` (func_id 15): Set attitude error deadband threshold (0-1 degree)

**Telemetry Parameters (12 new):**
- `aocs.sun_body_x/y/z` (0x0220-0x0222): Sun vector in body frame (from CSS composite)
- `aocs.mag_a_x/y/z` (0x0223-0x0225): Magnetometer A X/Y/Z
- `aocs.mag_b_x/y/z` (0x0226-0x0228): Magnetometer B X/Y/Z
- `aocs.mag_select` (0x0229): Active magnetometer selection (0=A, 1=B)
- `aocs.css_px/mx/py/my/pz/mz` (0x027A-0x027F): Individual CSS head illumination values
- `aocs.gyro_bias_x/y/z` (0x0270-0x0272): Gyroscope bias estimates
- `aocs.gps_fix` (0x0274): GPS fix type (0=none, 1=2D, 2=3D, 3=3D+velocity)
- `aocs.gps_pdop` (0x0275): GPS position dilution of precision
- `aocs.gps_num_sats` (0x0276): GPS tracked satellite count

**Events Generated:**
- `AOCS_MODE_CHANGE` (0x0200): Mode transition events
- `RW_OVERSPEED` (0x0201): Reaction wheel speed > 5000 RPM
- `RW_BEARING_DEGRADED` (0x0202): Bearing health < 50%
- `ST_BLIND` (0x0203): Star tracker lock loss
- `ST_RECOVERY` (0x0204): Star tracker reacquired lock
- `MOMENTUM_SATURATION` (0x0205): Momentum > 90% of maximum
- `ATTITUDE_ERROR_HIGH` (0x0206): Pointing error > threshold
- `DESATURATION_START` (0x0207): Wheel desaturation sequence started
- `DESATURATION_COMPLETE` (0x0208): Wheel desaturation completed
- `GPS_LOCK_LOST` (0x0209): GPS lock lost
- `GPS_LOCK_ACQUIRED` (0x020A): GPS lock acquired
- `GYRO_BIAS_HIGH` (0x020B): Gyroscope bias drift high
- `CSS_DEGRADED` (0x020C): Coarse sun sensor multiple failures

**S12 Monitoring Rules (5 total):**
1. Reaction Wheel 1 Overspeed: -5000 to +5000 RPM (WARNING)
2. Reaction Wheel 2 Overspeed: -5000 to +5000 RPM (WARNING)
3. Reaction Wheel 3 Overspeed: -5000 to +5000 RPM (WARNING)
4. Reaction Wheel 4 Overspeed: -5000 to +5000 RPM (WARNING)
5. Total Momentum Saturation: -90 to +90 Nms (WARNING)
6. Attitude Error High: 0 to 1 degree (WARNING)
7. Star Tracker 1 Blind: Status 0-2.5 (ALARM if > 2.5)
8. Star Tracker 2 Blind: Status 0-2.5 (ALARM if > 2.5)

**S19 Event-Action Rules (5 total):**
1. Momentum saturation → Trigger desaturation (func_id 1)
2. ST1 blind → Switch to ST2 (func_id 6)
3. ST2 blind → ST fallback (func_id 6)
4. Attitude error high → Safe mode (func_id 0)
5. RW overspeed (per wheel) → Disable specific wheel (func_id 2)

---

### EPS (Electrical Power System)

**New S8 Commands (10 functions, IDs 16-25):**
- `EPS_PAYLOAD_MODE` (func_id 16): Set payload power mode (0=off, 1=standby, 2=imaging)
- `EPS_SWITCH_LOAD` (func_id 17): Switch individual power line (0-7)
- `EPS_BATTERY_HEATER` (func_id 18): Set battery heater setpoint (°C)
- `EPS_CHARGE_RATE` (func_id 19): Override battery charge rate (A)
- `EPS_SOLAR_DRIVE` (func_id 20): Set solar array drive angle (deg)
- `EPS_EMERGENCY_SHED` (func_id 21): Trigger emergency load shedding
- `EPS_BUS_ISOLATION` (func_id 22): Isolate failing bus segment
- `EPS_LOAD_SHED_STAGE_1` (func_id 23): Activate stage 1 load shedding
- `EPS_LOAD_SHED_STAGE_2` (func_id 24): Activate stage 2 load shedding
- `EPS_LOAD_SHED_STAGE_3` (func_id 25): Activate stage 3 load shedding

**Telemetry Parameters (9 new):**
- `eps.per_switch_state` (0x0131): Bitmask of all load switch states
- `eps.charge_rate_a` (0x0132): Battery charge rate override (A)
- `eps.solar_array_drive_angle` (0x0133): Solar array drive angle (deg)
- `eps.load_shed_stage` (0x0134): Current load shedding stage (0-3)
- `eps.power_margin_w` (0x0135): Power generation - consumption (W)
- `eps.battery_health_pct` (0x0136): Battery health percentage
- `eps.eps_mode` (0x0137): EPS mode (0=nominal, 1=safe, 2=emergency)
- `eps.battery_heater_on` (0x0138): Battery heater status
- `eps.battery_heater_setpoint` (0x0139): Battery heater setpoint (°C)

**Events Generated:**
- `EPS_MODE_CHANGE` (0x0100): Mode transition events
- `BATTERY_SOC_WARNING` (0x0101): Battery SoC < 20%
- `BATTERY_SOC_CRITICAL` (0x0102): Battery SoC < 10%
- `BUS_UNDERVOLTAGE_WARNING` (0x0103): Bus voltage < 27V
- `BUS_UNDERVOLTAGE_CRITICAL` (0x0104): Bus voltage < 25V
- `BUS_OVERCURRENT` (0x0105): Bus current > limit
- `LOAD_SHED_STAGE_1` (0x0106): Stage 1 activated
- `LOAD_SHED_STAGE_2` (0x0107): Stage 2 activated
- `LOAD_SHED_STAGE_3` (0x0108): Stage 3 activated
- `BATTERY_OVERTEMP` (0x0109): Battery temp > 45°C
- `BATTERY_UNDERTEMP` (0x010A): Battery temp < -5°C
- `SOLAR_ARRAY_DEGRADED` (0x010B): Solar output below expected
- `ECLIPSE_ENTRY_EPS` (0x010C): Eclipse entry (solar→zero)
- `ECLIPSE_EXIT_EPS` (0x010D): Eclipse exit (solar→resume)
- `CHARGE_COMPLETE` (0x010E): Battery reached 100% SoC
- `PDU_OVERCURRENT` (0x010F): Individual power line overcurrent trip
- `POWER_LINE_SWITCHED` (0x0110): Power line state changed
- `EPS_SAFE_MODE_ENTRY` (0x0111): EPS entered safe mode

**S12 Monitoring Rules (8 total):**
1. Battery SoC Warning: < 20% (WARNING)
2. Battery SoC Critical: < 10% (ALARM)
3. Bus Voltage Low: < 27V (WARNING)
4. Bus Voltage Critical: < 25V (ALARM)
5. Battery Overcurrent: |I| > 15A (WARNING)
6. Battery Overtemp: > 45°C (WARNING)
7. Battery Undertemp: < -5°C (WARNING)
8. Solar Array Degradation: < 0.1A in sunlight (WARNING)

**S19 Event-Action Rules (4 total):**
1. Bus undervoltage → Payload off (func_id 16, mode=0)
2. Battery SoC critical → FPA cooler off (func_id 11)
3. Battery SoC critical → Payload power off (func_id 17)
4. Battery overtemp → Transponder TX off (func_id 12)

---

### TCS (Thermal Control System)

**New S8 Commands (4 functions, IDs 26-29):**
- `TCS_HEATER_SETPOINT` (func_id 26): Adjust heater setpoint (°C)
- `TCS_DECONTAMINATION` (func_id 27): Start decontamination heating sequence
- `TCS_COOLER_SETPOINT` (func_id 28): Set cooler target temperature (°C)
- `TCS_ZONE_PRIORITY` (func_id 29): Set thermal zone priority (0-10)

**Events Generated:**
- `TCS_OVERTEMP_WARNING` (0x0400): Zone temp > warning threshold
- `TCS_OVERTEMP_ALARM` (0x0401): Zone temp > alarm threshold
- `TCS_UNDERTEMP_WARNING` (0x0402): Zone temp < warning threshold
- `TCS_UNDERTEMP_ALARM` (0x0403): Zone temp < alarm threshold
- `HEATER_ON` (0x0404): Heater activated
- `HEATER_OFF` (0x0405): Heater deactivated
- `HEATER_STUCK_ON` (0x0406): Heater stuck-on failure
- `HEATER_STUCK_OFF` (0x0407): Heater stuck-off failure
- `TCS_MODE_CHANGE` (0x0408): Mode transition (e.g., decontamination)
- `THERMAL_RUNAWAY` (0x0409): dT/dt > threshold
- `FPA_THERMAL_READY` (0x040A): FPA in operational range (−3 to −15°C)
- `FPA_THERMAL_NOT_READY` (0x040B): FPA outside operational range

**S12 Monitoring Rules (8 total):**
1. Battery Zone Overtemp Warning: > 40°C (WARNING)
2. Battery Zone Overtemp Alarm: > 45°C (ALARM)
3. Battery Zone Undertemp Warning: < -5°C (WARNING)
4. Battery Zone Undertemp Alarm: < -10°C (ALARM)
5. OBC Zone Overtemp Warning: > 50°C (WARNING)
6. OBC Zone Overtemp Alarm: > 55°C (ALARM)
7. FPA Operational Range: −15°C to −3°C (ALARM if outside)
8. Thermal Runaway: dT/dt > 5°C/min (ALARM)

**S19 Event-Action Rules (3 total):**
1. FPA overtemp → Payload off (func_id 16, mode=0)
2. Battery overtemp → Transponder TX off (func_id 12)
3. OBC overtemp → OBC safe mode (func_id 40)

---

### TT&C (Telemetry, Tracking & Command)

**New S8 Commands (9 functions, IDs 30-38):**
- `TTC_FREQUENCY_SELECT` (func_id 30): Select UL/DL frequency band
- `TTC_MODULATION_MODE` (func_id 31): Set modulation (0=BPSK, 1=QPSK)
- `TTC_RECEIVER_GAIN` (func_id 32): Set receiver AGC target (dB)
- `TTC_RANGING_START` (func_id 33): Start ranging acquisition sequence
- `TTC_RANGING_STOP` (func_id 34): Stop ranging
- `TTC_COHERENT_MODE` (func_id 35): Select coherent/non-coherent mode
- `TTC_ANTENNA_SELECT` (func_id 36): Select antenna diversity
- `TTC_PA_POWER` (func_id 37): Set PA output power level
- `TTC_SWITCH_TRANSPONDER` (func_id 38): Switch primary/redundant transponder

**Events Generated:**
- `CARRIER_LOCK_ACQUIRED` (0x0500): Carrier lock acquired
- `CARRIER_LOCK_LOST` (0x0501): Carrier lock lost
- `BIT_SYNC_ACQUIRED` (0x0502): Bit sync acquired
- `BIT_SYNC_LOST` (0x0503): Bit sync lost
- `FRAME_SYNC_ACQUIRED` (0x0504): Frame sync acquired
- `FRAME_SYNC_LOST` (0x0505): Frame sync lost
- `LINK_MARGIN_WARNING` (0x0506): Eb/N0 < 6 dB
- `LINK_MARGIN_CRITICAL` (0x0507): Eb/N0 < 3 dB
- `PA_OVERTEMP_WARNING` (0x0508): PA temp > 55°C
- `PA_OVERTEMP_SHUTDOWN` (0x0509): PA temp > 65°C (shutdown)
- `PA_OVERTEMP_RECOVERY` (0x050A): PA temp < 50°C (recovery)
- `TRANSPONDER_SWITCH` (0x050B): Transponder switched (primary/redundant)
- `BER_THRESHOLD_EXCEEDED` (0x050C): BER > 1e-5
- `ANTENNA_DEPLOYED` (0x050D): Antenna deployment confirmed
- `RANGING_ACQUIRED` (0x050E): Ranging sequence acquired
- `RANGING_LOST` (0x050F): Ranging sequence lost
- `AGC_SATURATION` (0x0510): AGC saturation detected
- `UPLINK_TIMEOUT` (0x0511): No valid uplink (timeout)

**S12 Monitoring Rules (4 total):**
1. Link Margin Warning: Eb/N0 < 6 dB (WARNING)
2. Link Margin Critical: Eb/N0 < 3 dB (ALARM)
3. PA Overtemp Warning: > 55°C (WARNING)
4. BER Threshold: > 1e-5 (WARNING)

**S19 Event-Action Rules (3 total):**
1. Link margin critical → Increase TX power (func_id 37)
2. PA overtemp → PA shutdown (func_id 37, power=0)
3. High BER → Increase TX power (func_id 37)

---

### OBDH (On-Board Data Handling)

**New S8 Commands (4 functions, IDs 39-42):**
- `OBDH_DIAGNOSTIC` (func_id 39): Run onboard diagnostics (system health check)
- `OBDH_SAFE_MODE` (func_id 40): Transition OBC to safe mode
- `OBDH_MEMORY_SCRUB` (func_id 41): Trigger memory scrub operation
- `OBDH_CAN_SWITCH` (func_id 42): Switch CAN bus (primary/redundant)

**Events Generated:**
- `OBDH_MODE_CHANGE` (0x0300): OBC mode transition
- `OBC_REBOOT` (0x0301): OBC reboot occurred
- `MEMORY_ERROR` (0x0302): Memory error detected
- `WATCHDOG_TIMEOUT` (0x0303): Watchdog triggered reboot
- `BUS_FAILURE` (0x0304): CAN bus failure detected
- `BOOT_FAILURE` (0x0305): Application CRC check failed
- `OBC_SWITCHOVER` (0x0306): Redundant OBC activated
- `SEU_DETECTED` (0x0307): Single-event upset detected
- `SCRUB_COMPLETE` (0x0308): Memory scrub completed
- `TC_QUEUE_OVERFLOW` (0x0309): S11 TC scheduler queue full
- `TM_STORE_OVERFLOW` (0x030A): S15 TM storage full

**S12 Monitoring Rules (5 total):**
1. OBC CPU Load High: > 80% (WARNING)
2. Memory Error Threshold: > 5 errors/orbit (WARNING)
3. Watchdog Trigger Count: > 3 per day (ALARM)
4. TC Queue Overflow: (ALARM)
5. TM Store Overflow: (ALARM)

**S19 Event-Action Rules (4 total):**
1. OBC reboot → Acknowledge (informational)
2. Excessive reboots → Switch CAN bus (func_id 42)
3. Memory errors → Trigger memory scrub (func_id 41)
4. CPU overload → Safe mode transition (func_id 40)

---

### Payload (Imaging Instrument)

**New S8 Commands (5 functions, IDs 43-47):**
- `PAYLOAD_INTEGRATION_TIME` (func_id 43): Set integration time per band (ms)
- `PAYLOAD_GAIN_OFFSET` (func_id 44): Adjust gain/offset per band
- `PAYLOAD_COOLER_SETPOINT` (func_id 45): Set FPA cooler target (°C)
- `PAYLOAD_CALIBRATION_START` (func_id 46): Start calibration sequence
- `PAYLOAD_CALIBRATION_STOP` (func_id 47): Stop calibration sequence

**Telemetry Parameters (5 new):**
- Enhanced FPA thermal monitoring
- Per-band SNR tracking
- Compression ratio and efficiency metrics
- Cooler performance indicators
- Image segment health status

**Events Generated:**
- `IMAGING_START` (0x0600): Imaging operation started
- `IMAGING_STOP` (0x0601): Imaging operation stopped
- `PAYLOAD_MODE_CHANGE` (0x0602): Payload mode changed
- `STORAGE_WARNING` (0x0603): Storage > 90%
- `FPA_OVERTEMP` (0x0604): FPA temp > -3°C
- `FPA_UNDERTEMP` (0x0605): FPA temp < -15°C
- `COOLER_FAILURE` (0x0606): Cooler malfunction
- `IMAGE_CHECKSUM_ERROR` (0x0607): Checksum error in image
- `SNR_DEGRADED` (0x0608): SNR < 25 dB
- `STORAGE_FULL` (0x0609): Storage at 100%
- `BAD_SEGMENT_DETECTED` (0x060A): Memory segment failure
- `CALIBRATION_COMPLETE` (0x060B): Calibration sequence finished
- `STORAGE_CRITICAL` (0x060C): Storage > 95%
- `COMPRESSION_ERROR` (0x060D): Compression ratio anomaly

**S13 Large Data Transfer:**
- Full implementation for payload image downlink
- Block retrieval with CRC verification
- Transfer session management
- Incremental download with resume capability

**S12 Monitoring Rules (6 total):**
1. FPA Overtemp: > -3°C (WARNING)
2. FPA Undertemp: < -15°C (WARNING)
3. Storage Capacity Warning: > 90% (WARNING)
4. Storage Capacity Critical: > 95% (ALARM)
5. SNR Degradation: < 25 dB (WARNING)
6. Compression Anomaly: ratio > threshold (WARNING)

**S19 Event-Action Rules (3 total):**
1. Storage full → Stop imaging (func_id 16, mode=0)
2. FPA overtemp → Payload standby (func_id 16, mode=1)
3. Checksum errors → Verify data (func_id 48, diagnostic)

---

### FDIR (Fault Detection, Isolation & Recovery)

**S12 Monitoring Rules: 25 total**
- Comprehensive parameter threshold monitoring across all subsystems
- Severity levels: WARNING (yellow), ALARM (red)
- Delta checks for rate-of-change detection
- Configurable thresholds in s12_definitions.yaml

**S19 Event-Action Rules: 20 total**
- Autonomous response to monitored violations
- Cross-subsystem cascading (EPS fault → load shed → AOCS safe mode → Payload off)
- Priority-based load shedding (3 stages)
- Thermal runaway response
- Momentum saturation management
- Link margin degradation handling

**Cascading FDIR:**
1. **EPS Bus Undervoltage** → Generate event (0x0103)
   - S12 rule triggers (bus voltage < 27V)
   - S19 rule executes → Payload mode OFF
   - Event: LOAD_SHED_STAGE_1 (0x0106)

2. **Thermal Runaway (OBC)** → Generate event (0x0409)
   - S12 rule triggers (dT/dt > threshold)
   - S19 rule executes → OBC safe mode
   - AOCS transitions to nominal nadir (low power)
   - Payload disabled

3. **Momentum Saturation (AOCS)** → Generate event (0x0205)
   - S12 rule triggers (momentum > 90%)
   - S19 rule executes → Trigger desaturation
   - RW ramp down initiated

4. **PA Overheat (TTC)** → Generate event (0x0509)
   - S12 rule triggers (PA temp > 65°C)
   - S19 rule executes → PA shutdown
   - Link margin reduced, may trigger load shedding

---

## MCS Display Enhancements

**System Overview Display:**
- Spacecraft attitude quaternion visualization
- Orbit position with ground track
- Solar beta angle and eclipse indicator
- Spacecraft phase indicator

**Power Budget Monitor:**
- Battery SoC trending with confidence bands
- Solar array output per panel
- Power consumption breakdown (subsystem allocation)
- Power margin computation and history
- Load shedding stage indicator and threshold lines

**FDIR Alarm Panel:**
- Active S12 parameter violations (color-coded)
- S19 event-action execution log with timestamps
- Procedure status (active, pending, completed)
- Recommended recovery actions
- Fault history with escalation timeline

**Contact Schedule Display:**
- Ground station visibility windows (AOS/LOS predictions)
- Link margin vs. range curve
- Estimated downlink volume per pass
- Doppler correction requirements
- Pass planning interface

**Procedure Status Panel:**
- Active procedure progress (step-by-step)
- Scheduled procedures from S11 TC scheduler
- Procedure performance metrics
- Manual invocation interface
- Rollback/abort controls

**Telecommand Interface:**
- Per-subsystem command palette (organized by function ID)
- Parameter builder with type validation
- Command history with echo reports (S1 verification)
- Execution statistics

**Event & Alert Monitor:**
- Real-time S5 event stream (sortable, filterable)
- Subsystem and severity filters
- Event statistics (count, trend, rate)
- Alert acknowledgment tracking

---

## Operational Improvements

**S1 Request Verification (Complete):**
- S1.1: Acceptance report (command accepted)
- S1.3: Execution start (command began execution)
- S1.5: Progress report (step N of M)
- S1.4: Execution failure (with error code)
- S1.7: Completion report (with result code)

**S13 Large Data Transfer (Complete):**
- Transfer session management (up to 16 concurrent)
- Block-based retrieval (configurable block size)
- CRC-32 verification per block
- Incremental download with resume
- Maximum transfer size: 256 MB per session

**Event Generation Activation:**
All subsystems now actively emit S5 events during normal operation:
- AOCS: Mode changes, RW overspeed, ST blind, momentum saturation
- EPS: Mode changes, SoC warnings, bus undervoltage, load shedding stages
- TCS: Temperature violations, heater stuck faults, thermal runaway
- TTC: Lock state changes, link margin, PA thermal, BER threshold
- OBDH: Boot failures, reboot, watchdog timeout, memory errors
- Payload: Imaging state, FPA thermal, storage full, checksum errors

---

## Configuration Files Updated

| File | Changes |
|------|---------|
| `tc_catalog.yaml` | 50+ S8 commands added, organized by subsystem |
| `event_catalog.yaml` | 120+ events with severity and subsystem labels |
| `parameters.yaml` | 120+ telemetry parameters with units and descriptions |
| `s12_definitions.yaml` | 25+ monitoring rules with thresholds |
| `s19_rules.yaml` | 20+ event-action rules for autonomous response |
| `aocs.yaml` | Slew command support, new modes |
| `eps.yaml` | Load shedding stages, power margin computation |
| `tcs.yaml` | Decontamination sequence, zone prioritization |
| `ttc.yaml` | Frequency selection, modulation control |
| `obdh.yaml` | Diagnostic functions, safe mode transition |
| `payload.yaml` | Integration time control, calibration sequence |
| `fdir.yaml` | Cascading FDIR rules, procedure invocation |

---

## Performance & Scalability

**Simulator Throughput:**
- Real-time or faster on standard hardware (10-50 Hz simulation rate)
- Typical latency: <100 ms command-to-telemetry (S1 verification)
- Telemetry output: 1-100 Hz per structure (configurable)

**MCS Scalability:**
- Up to 8 simultaneous MCS clients per simulator instance
- Gateway enables distributed multi-site deployment
- Network bandwidth: 1-10 Mbps typical (telemetry + telecommand)

**Data Storage:**
- S13 payload downlink: 256 MB max per session
- S15 TM storage: 4 circular/linear buffers with configurable retention
- Event history: 10,000 events per buffer (5 Mbytes)

---

## Known Limitations & Future Enhancements

**Current Limitations:**
- Service 2 (Device Access) not implemented (all control via S8)
- Service 18 (Procedures) defined but not automatically invoked
- Memory model (S6) simplified: no real memory simulation
- Atmospheric losses not modeled (rain, gaseous absorption)
- Gravity gradient torques not included

**Planned Enhancements (Future Releases):**
- Service 2 implementation for low-level device control
- Service 18 procedure automation with state machine execution
- Real memory model with actual memory layout and EEPROM simulation
- Atmospheric loss model with rain attenuation databases
- Gravity gradient and solar radiation pressure torques
- Advanced FDIR with probabilistic fault diagnosis
- Mission planning optimization with constraint propagation
- Ground station network multi-hop communication simulation

---

## Documentation

Complete documentation available in `/docs/`:
- `architecture.md` — System architecture and design
- `PUS_SERVICE_REFERENCE.md` — PUS service command reference
- `OPERATIONS_GUIDE.md` — Practical operations procedures
- `CHANGELOG.md` — This file

---

## Development Team

EOSAT-1 Simulator Development — April 2026

# Commissioning Contingency Procedures

## Overview

This document serves as a companion reference to the **Commissioning Sequence Walkthrough** (09_leop.md). It maps each commissioning phase and its potential failure modes to the specific contingency and emergency procedures to follow when a step fails.

Each failure mode includes:
- **Symptoms:** Observable indicators on the MCS (Mission Control System) and telemetry
- **Procedure:** Reference code (CTG-XXX for contingency, EMG-XXX for emergency) and the procedure name
- **Recovery:** High-level summary of expected outcomes and next steps

During commissioning, consult this document immediately when encountering unexpected behavior. Follow the referenced procedures exactly, and escalate to Mission Control if procedures do not resolve the issue.

---

## Phase 0 — First Contact (Pass 1)

### Failure: No Telemetry Received

**Symptoms:**
- MCS shows no received signal strength indicator (RSSI)
- No telemetry frames decoded after full pass duration
- Ground station receiver operational and pointed correctly

**Procedure:** [CTG-014](../procedures/contingency/no_telemetry_at_pass.md) (no_telemetry_at_pass.md)

**Recovery:** Follow troubleshooting tree for receiver chain, transmitter power, and antenna orientation. Schedule follow-up passes at different ground stations if available. Verify spacecraft orientation and solar array deployment status in next pass.

---

### Failure: Telemetry Received but TTC Link Won't Lock

**Symptoms:**
- RSSI signal detected and stable
- Bit error rate (BER) > 1×10⁻³ and fluctuating
- No locked symbol timing in demodulator
- Ranging codes not acquired

**Procedure:** [CTG-025](../procedures/contingency/ttc_link_loss.md) (ttc_link_loss.md)

**Recovery:** Check frequency offset and Doppler compensation. Verify RF power amplifier output. If link locks after frequency adjustment, proceed with normal sequence. If link remains unstable, suspect antenna deployment issue and reference Phase 2 failure modes.

---

### Failure: OBC in Unexpected State (Not Bootloader)

**Symptoms:**
- Telemetry received but beacon message unrecognized or corrupted
- OBC appears to be in science mode or unknown state
- Watchdog timeout not observed
- Memory or image corruption suspected

**Procedure:** [CTG-009](../procedures/contingency/corrupted_image.md) (corrupted_image.md) or [CTG-015](../procedures/contingency/obc_bootloader_recovery.md) (obc_bootloader_recovery.md)

**Recovery:** Assess severity based on telemetry content. If image is corrupted but bootloader responsive, proceed to boot recovery (CTG-015). If image is intact but in unexpected mode, follow safe shutdown and reboot sequence. If corruption prevents safe state, escalate to emergency procedures.

---

## Phase 1 — Spacecraft Activation

### Failure: Command Uplinking Fails

**Symptoms:**
- Transmitted uplink command not acknowledged
- No command echo in telemetry
- BER acceptable but command parser not responding

**Procedure:** [CTG-025](../procedures/contingency/ttc_link_loss.md) (ttc_link_loss.md)

**Recovery:** Verify command format against S-band protocol specification. Confirm OBC is listening on command receiver (may require blind command to switch modes). If receiver switching fails, wait for automatic failover to redundant transponder (if available).

---

### Failure: Spacecraft Battery Not Responding to Commands

**Symptoms:**
- No power draw change after EPS commands
- Battery voltage static in beacon
- No response to power-on commands for any subsystem

**Procedure:** [CTG-004](../procedures/contingency/battery_cell_failure.md) (battery_cell_failure.md) or contact Flight Director for guidance

**Recovery:** Check battery isolation switch status via telemetry if available. May require EVA intervention if mechanical switch is stuck. If battery is truly failed, operate on UPS backup (typically 30–60 minutes) while planning recovery.

---

## Phase 2 — Antenna Deployment

### Failure: Antenna Deployment Command Acknowledged but No Physical Deployment

**Symptoms:**
- Deployment command echoed in telemetry
- RSSI drops immediately after command (loss of whip)
- Status register still shows antenna in STOWED state
- Signal strength does not improve

**Procedure:** [CTG-012](../procedures/contingency/gs_antenna_failure.md) (gs_antenna_failure.md) — check antenna_deployment_sensor for JAMMED state

**Recovery:** Inspect telemetry for sensor fault flags. If sensor shows JAMMED, mechanical deployment may be stuck—do not retry. If sensor is functional but antenna not deployed, check EPS power delivery to deployment actuator. May require mechanical intervention after mission review.

---

### Failure: Antenna Deploys but Link Quality Doesn't Improve

**Symptoms:**
- Antenna status shows DEPLOYED
- RSSI still low or unstable
- BER remains high after deployment
- Signal characteristic similar to whip antenna

**Procedure:** [CTG-012](../procedures/contingency/gs_antenna_failure.md) (gs_antenna_failure.md) — verify TTC chain integrity

**Recovery:** Suspect failed deployment (antenna physically stowed despite register state). Follow full TTC diagnostics: check RF switch states, diplexer continuity, LNA gain, and transmitter output. If all ground-side systems nominal, likely antenna stuck mechanically. Schedule retransmit of deployment command in next pass.

---

## Phase 3 — OBC Boot

### Failure: OBC Remains in Bootloader After Boot Command

**Symptoms:**
- Bootloader heartbeat continues after S1 boot command
- OBC mode register shows BOOTLOADER_STATE
- No attempt to load application image
- Timeout occurs without fallback

**Procedure:** [CTG-015](../procedures/contingency/obc_bootloader_recovery.md) (obc_bootloader_recovery.md)

**Recovery:** Follow recovery tree to identify image corruption, memory fault, or OBC crash. If image is intact, issue forced boot command. If image corrupted, use contingency boot image (if pre-loaded). Escalate to CTG-009 if corruption confirmed.

---

### Failure: OBC Boots Successfully but Reboots Within Minutes

**Symptoms:**
- Telemetry shows OBC in NOMINAL mode initially
- Unexpected reboot after 2–10 minutes
- Watchdog timeout or external reset indicated in logs
- Pattern repeats across multiple passes

**Procedure:** [CTG-016](../procedures/contingency/obc_redundancy_switchover.md) (obc_redundancy_switchover.md)

**Recovery:** Issue redundancy switchover command to secondary OBC CPU. If switchover succeeds, use secondary for commissioning and diagnose primary offline. If both CPUs exhibit same fault, suspect memory or power supply fault—escalate to CTG-013 or CTG-004.

---

### Failure: Memory Segment Failure Detected During Boot

**Symptoms:**
- OBC reports memory EDAC errors in health telemetry
- Boot process completes but error count increasing
- Errors localized to specific address range
- System appears to operate otherwise normally

**Procedure:** [CTG-013](../procedures/contingency/memory_segment_failure.md) (memory_segment_failure.md)

**Recovery:** Remap faulty memory segment out of service if possible via OBC command. If errors are in critical code region, proceed cautiously and monitor error rate growth. Schedule offloading of memory to ground if errors escalate. Do not proceed to next phases until error rate stabilizes.

---

## Phase 4 — Health Check

### Failure: Battery Voltage Below Minimum Threshold

**Symptoms:**
- Battery voltage telemetry < 26.0 V (or defined minimum)
- Voltage trending downward across consecutive beacons
- EPS load shedding may be active
- Battery temperature nominal

**Procedure:** [CTG-026](../procedures/contingency/undervoltage_loadshed.md) (undervoltage_loadshed.md) or [CTG-019](../procedures/contingency/progressive_load_shed.md) (progressive_load_shed.md)

**Recovery:** Issue load-shed command to disable non-essential subsystems. EPS will prioritize: OBDH > TTC > TCS > Payload. Recharge battery by pointing solar arrays to sun. Resume commissioning only after voltage recovers above 27.5 V for at least 30 minutes. If voltage does not recover, suspect battery degradation—reference CTG-004.

---

### Failure: Battery Cell Failure or Imbalance Detected

**Symptoms:**
- Telemetry shows cell voltage spread > 0.5 V between cells
- Individual cell voltage < 3.2 V while others nominal
- Battery temperature spike (> 45°C)
- Voltage fluctuates with load transients

**Procedure:** [CTG-004](../procedures/contingency/battery_cell_failure.md) (battery_cell_failure.md)

**Recovery:** Disable non-essential loads and stabilize battery state. EPS may automatically bypass failed cell if redundancy available. If cell failure persists, operate on reduced power budget and extend mission timeline. Notify Flight Director for risk assessment before proceeding.

---

### Failure: Bus Voltage Out of Range

**Symptoms:**
- Primary bus voltage outside 28 V ± 4 V window
- Secondary bus voltage also affected if powered
- Transient spikes or sustained deviation observed
- Correlates with load switching events

**Procedure:** [CTG-007](../procedures/contingency/bus_failure_isolation.md) (bus_failure_isolation.md) or [CTG-008](../procedures/contingency/bus_failure_switchover.md) (bus_failure_switchover.md)

**Recovery:** Issue isolation command to identify faulty power distribution path. If primary bus fails, switch loads to secondary bus (CTG-008). If both buses affected, suspect battery or regulator fault—escalate to CTG-004 or contact Flight Director. Return to normal operations only after voltage stable for 10 minutes.

---

### Failure: Temperature Out of Range (Subsystem)

**Symptoms:**
- Subsystem temperature exceeds nominal limits (typically -20°C to +50°C)
- Specific thermal zone affected (e.g., battery, OBC, payload)
- Temperature alarm raised in health telemetry
- Rate of change indicates active heating or loss of cooling

**Procedure:** [CTG-024](../procedures/contingency/thermal_exceedance.md) (thermal_exceedance.md)

**Recovery:** Reduce power to overheating subsystem if safe (may trigger shutdown). Orient spacecraft to improve radiator visibility to deep space if possible. If over-temperature persists, reduce overall power budget and delay high-power phases. If temperature remains high for > 30 minutes, escalate to EMG-006 (thermal runaway).

---

## Phase 5 — TM Buffering Configuration

### Failure: Housekeeping Not Flowing After Enable Command

**Symptoms:**
- Housekeeping enable command acknowledged
- No HK frames visible in telemetry downlink
- HK subsystem status shows NOT_ACTIVE
- OBC mode may not support HK streaming

**Procedure:** Verify OBC mode and subsystem status via direct telemetry read. Reference commissioning walkthrough Phase 5 diagnostics.

**Recovery:** Confirm OBC is in NOMINAL mode (not BOOTLOADER or SAFE). Issue explicit HK start command with correct stream ID. Check S5 parameters are correctly set in OBC memory. If still no data, suspect OBC software issue—escalate to CTG-015.

---

### Failure: Buffer Fill Anomaly or Excessive Buffer Usage

**Symptoms:**
- Buffer fill percentage increasing unexpectedly
- HK frame rate higher than commanded
- Memory usage alarming in health telemetry
- Risk of buffer overflow and data loss

**Procedure:** Reference [CTG-013](../procedures/contingency/memory_segment_failure.md) (memory_segment_failure.md) if memory issue suspected

**Recovery:** Reduce HK telemetry rate via S5.x command. Offload buffer contents to ground immediately via high-rate downlink pass. Check OBC task scheduling for runaway tasks. If buffer overflow occurs, data loss is expected—document loss period and resume with reduced rate.

---

## Phase 6 — Set Time

### Failure: Time Set Command Rejected

**Symptoms:**
- S9.2 command rejected or not acknowledged
- Time value in OBC not updating
- OBC may be in wrong mode for time commands
- Time offset error flagged in response

**Procedure:** Verify OBC is in NOMINAL mode. Check S9 parameter set is accessible. Confirm time format matches expected format (e.g., UTC seconds since epoch).

**Recovery:** Retry time set with verified accurate time and correct format. If still rejected, OBC may have time-sync lockout—wait for automatic sync event or issue mode reset. Do not proceed to Phase 7 with unset time, as TLE propagation and command scheduling depend on accurate time.

---

### Failure: Time Sync with GPS Causes AOCS Disruption

**Symptoms:**
- GPS time tag command issued successfully
- Immediately after, AOCS enters safe mode or detumble anomaly
- Attitude rates spike or star tracker loses lock
- Time mismatch between OBC and AOCS subsystem

**Procedure:** [CTG-027](../procedures/contingency/gps_time_sync_recovery.md) (gps_time_sync_recovery.md)

**Recovery:** AOCS anomaly triggered by sudden time jump. Issue AOCS safe-mode exit command and allow re-acquisition of attitude. Resync time using smaller increments if available, or coordinate time update during eclipse when AOCS less sensitive. Verify AOCS clock offset after recovery.

---

## Phase 7 — Power On (Subsystems)

### Failure: Power Line Won't Switch Despite Command

**Symptoms:**
- Power-on command acknowledged
- Subsystem does not respond on bus
- Subsystem status remains OFF in telemetry
- EPS relay or breaker not actuating

**Procedure:** Check EPS mode and verify no overcurrent lockout. Reference [CTG-017](../procedures/contingency/overcurrent_response.md) (overcurrent_response.md) if overcurrent detected.

**Recovery:** Issue redundant power line command if available. Verify EPS is not in safe mode (CTG-010). If mechanical relay is stuck, attempt warm-up cycle (brief power pulse) or wait for next temperature cycle. Escalate to Flight Director if line remains inert.

---

### Failure: Overcurrent Trip on Power Line

**Symptoms:**
- Power-on command triggers immediate current spike
- EPS overcurrent breaker opens automatically
- Subsystem shuts down after < 1 second
- Current limit alarm in telemetry

**Procedure:** [CTG-017](../procedures/contingency/overcurrent_response.md) (overcurrent_response.md)

**Recovery:** Suspect short circuit or internal subsystem fault. Do not retry power-on immediately—wait 5 minutes for breaker thermal reset. Issue diagnostics to check subsystem for fault indications. If overcurrent repeats, subsystem is faulty; isolate line and notify Flight Director. Move to next subsystem and return to this line after mission analysis.

---

### Failure: Solar Array Issues (Underperforming or Intermittent)

**Symptoms:**
- Solar array current lower than expected for sun angle
- Current fluctuates or drops to zero
- Voltage on unregulated bus unstable
- Power generation insufficient for load

**Procedure:** [CTG-021](../procedures/contingency/solar_array_degradation.md) (solar_array_degradation.md) or [CTG-022](../procedures/contingency/solar_panel_loss_response.md) (solar_panel_loss_response.md)

**Recovery:** Confirm solar array orientation and sun angle. Check for shadow from deployed appendages. If partial array loss, reduce power budget and adjust attitude to maximize remaining panel exposure. If complete array failure suspected, switch to battery and escalate—spacecraft cannot sustain operations without solar generation.

---

### Failure: EPS Enters Safe Mode Unexpectedly

**Symptoms:**
- EPS subsystem suddenly in SAFE_MODE state
- No load shedding command issued
- Power regulation abnormal
- Battery discharging despite sun-pointed attitude

**Procedure:** [CTG-010](../procedures/contingency/eps_safe_mode.md) (eps_safe_mode.md)

**Recovery:** Follow safe-mode exit procedure: confirm bus voltages stable, load shedding sequence, and trigger explicit mode transition command. Identify trigger condition (undervoltage, overvoltage, temperature fault) from telemetry and address root cause. Resume normal power sequencing only after safe exit complete.

---

### Failure: AOCS Won't Enter DETUMBLE Mode

**Symptoms:**
- DETUMBLE mode command not acknowledged or rejected
- AOCS remains in STANDBY or previous mode
- Reaction wheels not spun up
- Spacecraft attitude not stabilizing

**Procedure:** [CTG-002](../procedures/contingency/aocs_anomaly.md) (aocs_anomaly.md)

**Recovery:** Verify AOCS has power, OBC is operational, and attitude sensor data valid. Check reaction wheel health telemetry for mechanical faults. Retry detumble command with brief delay. If AOCS remains unresponsive, confirm star tracker and rate gyro powered. Escalate to CTG-001 if reaction wheel suspected stuck.

---

### Failure: Reaction Wheel Stuck or Unresponsive

**Symptoms:**
- Reaction wheel speed command not resulting in speed change
- Current draw at zero despite command
- Mechanical vibration or friction noise detected on spacecraft
- Torque produced by wheel significantly reduced

**Procedure:** [CTG-001](../procedures/contingency/aocs_actuator_stuck_recovery.md) (aocs_actuator_stuck_recovery.md) or [CTG-020](../procedures/contingency/reaction_wheel_anomaly.md) (reaction_wheel_anomaly.md)

**Recovery:** If wheel is stuck, do not force—confirm fault via telemetry. AOCS can continue with 2 of 3 wheels; isolate faulty wheel and use redundant wheels for attitude control. Issue mechanical impulse command (brief reverse acceleration) to free wheel if safe. If wheel remains stuck, document for post-mission analysis and continue with available actuation.

---

## Phase 8 — TLE Upload and Orbit Propagation

### Failure: Orbit State Does Not Propagate Correctly

**Symptoms:**
- S20 parameters appear accepted (no error response)
- Orbit_tools offline propagation output does not match OBC calculation
- Predicted pass times misaligned with actual passes
- Position ephemeris diverging from GPS updates (Phase 11)

**Procedure:** Verify S20 parameters are correctly formatted and accepted. Check orbit_tools configuration matches OBC model. Reference commissioning walkthrough Phase 8 diagnostics.

**Recovery:** Re-transmit TLE with verified propagation constants. Confirm OBC has latest atmospheric density model. If mismatch persists after re-upload, compare OBC propagation output with ground-truth GPS fixes in Phase 11 to quantify error. Document bias for mission planning.

---

## Phase 9 — AOCS Mode Progression (Detumble → Nadir → Sun-Point)

### Failure: Attitude Rates Not Damping During Detumble

**Symptoms:**
- Spacecraft angular rates remain high (> 5 deg/s)
- Reaction wheels active but not effectively damping
- Detumble mode active for > 20 minutes with no improvement
- Possible solar panel shadowing causing control authority loss

**Procedure:** [CTG-002](../procedures/contingency/aocs_anomaly.md) (aocs_anomaly.md)

**Recovery:** Confirm reaction wheels have adequate electrical power and torque authority. Check rate gyro sensor data for scale factor errors or bias. Verify sun-pointing orientation allows solar arrays to generate power. If gyros or wheels faulty, escalate to CTG-020 or CTG-003. Extend detumble timeout or switch to magnetic control if available.

---

### Failure: Sensor Loss During Mode Transition

**Symptoms:**
- Star tracker or rate gyro loses lock/signal
- Attitude uncertainty increases
- Mode transition command stalls pending sensor recovery
- Possible thermal or power issue affecting sensor

**Procedure:** [CTG-003](../procedures/contingency/aocs_sensor_loss_recovery.md) (aocs_sensor_loss_recovery.md)

**Recovery:** Confirm sensor power is active. Check thermal conditions—if sensor over-temperature, reduce power or wait for cool-down. Allow sensor cold-start and reacquisition time (up to 60 seconds). Verify redundant sensor is available and switch if primary fails. If both sensors lost, escalate to EMG and revert to safe mode.

---

### Failure: AOCS Mode Transition Fails (Cannot Reach Sun-Point or Nadir)

**Symptoms:**
- Mode transition command sent but not executed
- AOCS remains in previous mode
- No error response indicating why transition rejected
- Possible control law or sensor data issue

**Procedure:** [CTG-002](../procedures/contingency/aocs_anomaly.md) (aocs_anomaly.md)

**Recovery:** Verify AOCS has valid sensor data (rates, sun vector, star tracker). Check control law gains are not saturated. Confirm attitude target (sun-point vector or nadir vector) is reachable with available actuation. Retry mode transition. If repeated failures, suspect OBC software or communication fault—escalate to CTG-015.

---

## Phase 10 — Whole-Orbit Verification

### Failure: Thermal Runaway Detected

**Symptoms:**
- Temperature increasing uncontrollably in thermal zone
- Rate of increase > 1°C per minute
- Thermal control system cannot arrest rise
- Overheat alarm imminent or triggered

**Procedure:** [EMG-006](../procedures/emergency/thermal_runaway.md) (thermal_runaway.md)

**Recovery:** This is an emergency condition. Issue immediate load-shedding to affected subsystems if possible. Orient radiators away from sun (emergency attitude). Initiate safe-mode transition to minimize power dissipation. If runaway continues, prepare for forced shutdown of affected subsystem or spacecraft-level safe mode. Contact Flight Director immediately.

---

### Failure: Total Power Failure

**Symptoms:**
- All subsystems lose power simultaneously
- Telemetry downlink ceases
- Only beacon heartbeat continues (if on independent battery)
- Battery voltage at zero or regulator output failed

**Procedure:** [EMG-001](../procedures/emergency/total_power_failure.md) (total_power_failure.md)

**Recovery:** This is an emergency. If this occurs, the spacecraft has lost primary power bus. Recovery depends on availability of backup power (UPS). If UPS deployed, expect limited functionality and reduced battery. Wait for solar charging recovery on next sunny pass. If no power recovery detected after 2 passes, spacecraft is likely non-recoverable.

---

### Failure: Unexpected Safe Mode Triggered

**Symptoms:**
- AOCS suddenly enters safe mode without command
- Power load-shedding active
- Attitude control on magnetic torquers only
- FDIR logic may have detected fault

**Procedure:** [EMG-002](../procedures/emergency/loss_of_attitude.md) (loss_of_attitude.md)

**Recovery:** Check telemetry for FDIR trigger condition: sensor loss, rate limit exceeded, power under-voltage, or command timeout. Verify all systems nominal. Issue safe-mode exit command when conditions stable. If re-entry to safe mode occurs repeatedly, escalate to Flight Director. Do not force exit if root cause not identified.

---

## Phase 11 — GPS Activation and Time Recovery

### Failure: GPS Won't Acquire Lock

**Symptoms:**
- GPS receiver powered on but not reporting lock
- Satellite count at zero or very low
- Position/time output unreliable or frozen
- Receiver warm-start recovery insufficient

**Procedure:** Allow GPS cold start (5–20 minutes depending on receiver type). Verify GPS antenna has clear view of sky. Check receiver power and signal quality. Confirm OBC is not suppressing GPS updates.

**Recovery:** GPS acquisition can take considerable time on first power-up (cold start). Proceed with commissioning using propagated orbit until lock achieved. Once GPS locks, validate position against propagated orbit for consistency (typically < 10 km). If GPS continues to fail, may be antenna issue or receiver fault—escalate to Flight Director for further diagnosis.

---

### Failure: GPS Time Sync Causes AOCS Reset or Anomaly

**Symptoms:**
- GPS time-tag command executed
- AOCS immediately enters safe mode or reports anomaly
- Time discontinuity detected in logs
- Attitude control degraded after sync

**Procedure:** [CTG-027](../procedures/contingency/gps_time_sync_recovery.md) (gps_time_sync_recovery.md)

**Recovery:** GPS time sync causes sudden time jump, which AOCS may not tolerate. AOCS will recover during next eclipse or after mode restart. Issue AOCS safe-mode exit and allow re-initialization. For future syncs, coordinate with AOCS mode (e.g., during STANDBY) to minimize disruption. Validate time offset before next sync.

---

## Phase 12 — Star Tracker Checkout

### Failure: Star Tracker Stays in BOOT State

**Symptoms:**
- Star tracker power confirmed but not transitioning to IDLE
- Status remains BOOT after > 60 seconds
- No attitude data output
- May be power or initialization issue

**Procedure:** Verify star tracker power is stable. Allow full 60-second boot time. Check power line for brownout conditions. Confirm no over-temperature shutdown occurred.

**Recovery:** Allow additional boot time (up to 120 seconds total). If still in BOOT, power-cycle the line (off 30 seconds, then on). Verify power supply voltage is within spec (12V ± 2V typical). If star tracker still not responsive, suspect internal fault and escalate to Flight Director. Continue with gyro-only attitude control.

---

### Failure: Star Tracker Blinded (No Stars Visible)

**Symptoms:**
- Star tracker reports insufficient star count
- Attitude output unavailable or erratic
- Typically occurs near sun in FOV
- Normal during daylight half of orbit

**Procedure:** Star tracker blinding is normal if sun is in field of view. No corrective action needed.

**Recovery:** This is expected behavior. Star tracker will recover once sun leaves FOV or in eclipse. Do not attempt to recover blinded tracker—wait for natural recovery during eclipse or orbit position change. Plan commissioning activities to avoid sun glint periods if extended attitude measurements required.

---

### Failure: Star Tracker Hardware Failure

**Symptoms:**
- Star tracker reports internal error code
- Persistent loss of attitude data even out of sun glint
- Power cycling does not recover
- Error persists across multiple passes

**Procedure:** [CTG-023](../procedures/contingency/star_tracker_failure.md) (star_tracker_failure.md)

**Recovery:** Isolate faulty star tracker if redundancy available. AOCS can operate with gyro-only control or with secondary star tracker. Notify Flight Director of primary failure. If both star trackers fail, escalate to CTG-003 (aocs_sensor_loss_recovery.md) and prepare for gyro-only or magnetic control mode.

---

### Failure: Both Star Trackers Fail or Unavailable

**Symptoms:**
- Primary and secondary star trackers both non-operational
- Attitude control relies on rate gyros alone
- No external reference for attitude determination
- Gyro drift limits mission duration

**Procedure:** [CTG-003](../procedures/contingency/aocs_sensor_loss_recovery.md) (aocs_sensor_loss_recovery.md)

**Recovery:** This is a degraded configuration. AOCS can continue with gyro-based control but with increasing uncertainty over time. Increase ground-based attitude determination frequency. Consider using magnetic coils for attitude correction if available. Proceed with critical commissioning activities; defer non-essential activities pending star tracker recovery. Escalate to Flight Director if no attitude reference available.

---

## Phase 13 — Redundancy Checkout

### Failure: Bus Switchover Fails

**Symptoms:**
- Switchover command acknowledged but not executed
- Primary bus still supplying loads
- Redundant bus not activated
- Power distribution unchanged

**Procedure:** [CTG-007](../procedures/contingency/bus_failure_isolation.md) (bus_failure_isolation.md) or [CTG-008](../procedures/contingency/bus_failure_switchover.md) (bus_failure_switchover.md)

**Recovery:** Verify EPS switchover hardware is functional. Check for voltage conditions preventing switch (may require manual threshold override). Confirm secondary bus is healthy before retrying. If switchover command still fails, suspect EPS firmware or switch hardware fault. Escalate to Flight Director and proceed with remaining redundancy tests on other systems.

---

### Failure: Redundant Transponder Communication Fails

**Symptoms:**
- Switchover to secondary transponder commanded
- No downlink signal on secondary frequency
- Primary transponder still operating
- Receiver lock time exceeds normal threshold on secondary

**Procedure:** [CTG-025](../procedures/contingency/ttc_link_loss.md) (ttc_link_loss.md)

**Recovery:** Verify secondary transponder has RF power and is transmitting on correct frequency. Check ground station receiver is tuned to secondary frequency. If secondary has hardware fault, continue with primary transponder. If both operational but secondary shows poor performance, document for post-mission analysis and use primary for remainder of mission. Escalate critical tests to Flight Director if cannot verify secondary redundancy.

---

### Failure: Loss of Communication During Redundancy Test

**Symptoms:**
- Uplink command received but communication drops
- Downlink signal lost for > 5 minutes (entire pass)
- Beacon stops being received
- Likely caused by mode transition error during test

**Procedure:** [EMG-004](../procedures/emergency/loss_of_communication.md) (loss_of_communication.md)

**Recovery:** This is an emergency condition. Spacecraft may be in safe mode with beacon only. Wait for next pass to re-establish contact. Do not issue further commands until communication restored. Verify redundancy test command was safe (e.g., switching back to known-good state). When communication restored, confirm spacecraft mode and retry test with more controlled sequence.

---

## Phase 14 — Time-Tagged Commands

### Failure: Scheduled Command Does Not Execute at Designated Time

**Symptoms:**
- Command uploaded and verified in schedule memory
- Designated execution time passes without command effect
- No command echo in telemetry
- Possible time synchronization or schedule parsing issue

**Procedure:** Verify OBC time is synchronized (compare with downlinked timestamp). Check S11 schedule memory status and command count. Confirm command format and execution time are correctly encoded.

**Recovery:** Validate OBC time via ground-truth source or GPS time. Resend command with verified time offset. Check for OBC command queue saturation (too many pending commands). If scheduling subsystem faulty, issue commands immediately without scheduling. Escalate to CTG-015 if OBC command processor faulty.

---

### Failure: Bit Error Rate Anomaly During Command Downlink

**Symptoms:**
- Received command echo showing bit inversions
- BER spike during specific command types or uplink windows
- Command corruption detected by CRC
- Intermittent errors suggest RF or receiver issue

**Procedure:** [CTG-005](../procedures/contingency/ber_anomaly.md) (ber_anomaly.md)

**Recovery:** Increase uplink power (if ground station capability available) or retransmit over multiple frames with error correction coding. Verify ground station receiver is functioning normally. If BER remains elevated, suspect spacecraft receiver degradation. For critical commands, implement command repetition and verification sequence. Proceed with redundant uplink path if available.

---

## Phase 15 — Payload Commissioning

### Failure: Payload Won't Power On

**Symptoms:**
- Payload power-on command acknowledged
- Payload does not respond on data bus
- Current draw not observed on power line
- Payload status shows OFF

**Procedure:** Verify payload power line is enabled in EPS. Check for overcurrent lockout (CTG-017). Confirm payload is receiving stable power supply voltage.

**Recovery:** Retry power-on command. Verify EPS is not in load-shed mode affecting payload line. If overcurrent observed, suspect internal payload short—isolate line and notify Flight Director before retrying. If no power delivery to payload, may be mechanical or electrical connection fault requiring investigation post-mission.

---

### Failure: Focal Plane Array (FPA) Won't Cool

**Symptoms:**
- FPA power on but cooler not activated
- FPA temperature not dropping toward setpoint
- Cooler current draw at zero despite power
- Possible compressor or thermal control system fault

**Procedure:** Verify cooler power line is active. Check thermal control system (TCS) status in telemetry. Confirm coolant loop is not blocked or frozen.

**Recovery:** Verify TCS is configured for FPA cooling (may be in standby). Check cooler inlet/outlet temperature sensors for anomalies. If cooler mechanically stuck, attempt warm-up cycle (brief power pulse). If FPA temperature rises above limits during science operation, switch to secondary cooler (if available) or implement reduced duty-cycle imaging with natural cooling. Escalate to Flight Director if thermal control cannot be restored.

---

### Failure: Payload Anomaly or Malfunction

**Symptoms:**
- Payload health telemetry shows error code
- Science data output corrupted or missing
- Payload mode transitions fail or stall
- Possible internal firmware or hardware issue

**Procedure:** [CTG-018](../procedures/contingency/payload_anomaly.md) (payload_anomaly.md)

**Recovery:** Reference payload-specific anomaly procedures. Power-cycle payload if anomaly persists. Verify OBC-to-payload command interface is functioning. Check for data bus collisions or timing issues. If anomaly unresolved, isolate payload from science operations and document for post-mission troubleshooting. Proceed with other commissioning phases using secondary payload if available.

---

### Failure: Image Capture Fails or No Image Data Received

**Symptoms:**
- Image capture command acknowledged
- No image telemetry data downlinked
- Image buffer status shows no data
- AOCS pointing attitude appears correct

**Procedure:** Verify AOCS attitude is pointed toward target scene. Confirm payload is in image mode (not standby). Check downlink bandwidth allocation for image data streaming. Verify FPA is powered and cooled to operating temperature.

**Recovery:** Retry image capture with verified target pointing. Confirm AOCS has converged to attitude command (wait for control loop to settle). Check payload mode status and clock—image capture may require specific OBC time window. If images still not captured, verify FPA sensor is operational via power-up diagnostics. Escalate to CTG-018 if image capture hardware faulty.

---

## Phase 16 — Handover to Mission Operations

### Failure: Power Budget Negative (Insufficient Generation)

**Symptoms:**
- Average power consumption exceeds solar generation capability
- Battery discharging even in sunlit portion of orbit
- Voltage trending downward across consecutive orbits
- Cannot sustain current configuration indefinitely

**Procedure:** [CTG-019](../procedures/contingency/progressive_load_shed.md) (progressive_load_shed.md)

**Recovery:** Implement progressive load shedding to reduce power consumption. Prioritize OBDH and TTC; defer payload science or reduce duty cycle. Adjust attitude to maximize solar array power generation. Extend eclipse survival time if needed. Coordinate with mission operations to establish sustainable power profile before final handover. If power budget remains negative, mission duration is limited—escalate to Flight Director for contingency planning.

---

### Failure: Subsystem Not Nominal at End of Commissioning

**Symptoms:**
- One or more subsystem in DEGRADED or SAFE mode
- Unresolved anomaly or partial failure identified
- Functional but not meeting operational design specs
- Contingency procedure did not fully recover system

**Procedure:** Refer back to relevant phase (0–15) and applicable contingency procedure. Reference the contingency procedure index for the specific subsystem.

**Recovery:** Document the degraded state and failure mode. Implement workaround procedures in mission operations if safe. If subsystem is critical path for mission, escalate to Flight Director for contingency decision: proceed with reduced capability, or defer mission start pending repair/investigation. Ensure all known limitations are communicated to mission ops and documented in final handover briefing.

---

## General Recovery Principles

### When Any Procedure Fails

1. **Document:** Record all telemetry, commands issued, and timing
2. **Escalate:** Contact Flight Director immediately; do not continue without guidance
3. **Verify:** Recheck all assumptions and measurement before retrying
4. **Sequence:** Follow procedure exactly; do not skip steps or reorder
5. **Cool-down:** Allow 10–30 minutes between failed attempts unless otherwise directed

### Communication During Contingencies

- Maintain real-time link with Flight Director during active recovery
- Report status every 5 minutes if no progress
- Use established voice net procedures (not uplinked commands)
- Prepare contingency briefing for next pass if issue unresolved

### Post-Recovery Steps

- Return affected subsystem to nominal state after procedure completes
- Run follow-up health check (Phase 4) to confirm stability
- Document all anomalies for mission analysis
- Debrief Flight Director before proceeding to next phase

---

**Document Control:** Commissioning Contingency Procedures Reference v1.0  
**Last Updated:** 2026-04-09  
**Approver:** Flight Director  
**Related Documents:** Commissioning Sequence Walkthrough (09_leop.md), Subsystem Manuals (01–08), Procedure Index

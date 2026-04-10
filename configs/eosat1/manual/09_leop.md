# EOSAT-1 LEOP Operations Guide

**Document ID:** EOSAT1-UM-LEOP-010
**Issue:** 1.0
**Date:** 2026-03-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Purpose

This document provides the complete operations guide for the Launch and Early Orbit Phase
(LEOP) of the EOSAT-1 mission. It covers the separation sequence, first ground contact,
bootloader operations, application boot, sequential subsystem power-on, ADCS commissioning,
and the transition to nominal operations. All GO/NO-GO checkpoints are identified.

## 2. LEOP Overview

LEOP begins at spacecraft separation from the launcher and concludes when the spacecraft
is declared operational in nominal mode. The phase typically lasts 1-3 days and proceeds
through the following major milestones:

| Milestone                  | Approx. Time    | Description                          |
|----------------------------|-----------------|--------------------------------------|
| Separation                 | T+0             | Launcher release, timer start        |
| Antenna deployment         | T+30 min        | Burn wire activation                 |
| First contact              | T+30 min to T+6 h | Beacon acquisition at Iqaluit or Troll |
| Application boot           | First contact   | Transition from BOOTLOADER           |
| Detumble complete          | T+1 to T+12 h  | Body rates < 0.5 deg/s              |
| Sequential power-on        | T+12 to T+24 h | Subsystem activation                 |
| ADCS commissioning         | T+24 to T+72 h | Sensor/actuator checkout             |
| Nominal transition         | T+48 to T+72 h | Full operational capability          |

## 3. Separation Sequence

### 3.1 Separation State Machine

The separation sequence follows a 7-phase state machine implemented in the OBC:

| Phase             | Trigger                    | Duration   | Actions                           |
|-------------------|----------------------------|------------|-----------------------------------|
| PRE_SEPARATION    | Power-on (in launcher)     | —          | All systems off except timer circuit |
| SEP_DETECTED      | Separation switch opens    | Instant    | Timer starts; unswitchable PDM on |
| POWER_STABILIZE   | Unswitchable PDM active    | ~30 s      | OBC boots; RX powers on           |
| TIMER_WAIT        | OBC boot complete          | ~30 min    | No RF transmissions; OBC in BOOTLOADER |
| ANTENNA_DEPLOY    | 30-min timer expires       | ~60 s      | Burn wire commanded; antenna deploys |
| BEACON_START      | Antenna deploy confirmed   | ~10 s      | TX enabled; beacon SID 11 at 1 kbps |
| NOMINAL           | Beacon transmission active | —          | Ready for ground contact          |

### 3.2 Unswitchable Line Power-On

Upon separation detection, the unswitchable PDM lines activate automatically:

- **UNSW-1 (OBC)**: The primary OBC (OBC-A) powers on and boots into BOOTLOADER mode.
- **UNSW-2 (RX)**: The transponder receiver powers on, ready to accept ground commands
  via the dedicated PDM command channel.

No ground command is required for these lines. They are hardwired to activate when the
separation switch opens and the timer circuit initialises.

### 3.3 30-Minute Timer Hold

During the TIMER_WAIT phase:

- The OBC is operational in BOOTLOADER mode but does not transmit.
- The receiver is powered and listening for commands (not expected pre-deployment).
- All switchable PDM lines remain inhibited.
- The spacecraft is tumbling freely (no attitude control in BOOTLOADER).
- Battery power supports this minimal configuration indefinitely.

The 30-minute hold satisfies regulatory requirements prohibiting RF transmissions
immediately after separation from the launcher.

### 3.4 Antenna Deployment

After the 30-minute timer expires:

1. OBC commands the primary burn wire circuit.
2. Current passes through the restraining wire (~2 A for 5 seconds).
3. The wire melts and spring-loaded hinges deploy the antenna elements.
4. Deployment is verified by monitoring microswitches (if available) or by RSSI
   improvement once transmissions begin.
5. If the primary burn wire fails to deploy the antenna, the OBC commands the
   redundant burn wire circuit after a 60-second timeout.

### 3.5 Beacon Transmission Start

Once the antenna is deployed:

1. The TX switchable PDM line (SW-1) is enabled.
2. The transmitter and power amplifier power on.
3. The OBC begins transmitting beacon packets (**SID 11**) at 1 kbps low-rate, sourced from the **bootloader APID (0x002)**. This is the ONLY HK packet emitted in bootloader mode — SIDs 1–6 remain silent until application boot. No TM storage, no attitude control, and no payload operations are possible while in bootloader.
4. The 15-minute TX auto-off timer starts, but is reset each time a beacon is transmitted.

The spacecraft is now ready for ground acquisition.

**GO/NO-GO Checkpoint 1: Separation Sequence Complete**
- Criteria: Beacon transmission active, antenna deployed.
- Decision: Proceed to first contact operations.

## 4. First Contact — RF Acquisition

### 4.1 Ground Station Preparation

Before the expected first contact window:

1. Configure ground station antenna for VHF/UHF acquisition at predicted AOS azimuth/elevation.
2. Set receiver for low-rate mode (1 kbps BPSK demodulation).
3. Load EOSAT-1 frequency plan (401.5 MHz downlink, 449.0 MHz uplink).
4. Prepare pre-planned LEOP command sequence.

### 4.2 Beacon TM Interpretation

The beacon packet (SID 11) contains minimal but critical health information:

| Parameter       | Expected Value         | Concern If                        |
|-----------------|------------------------|-----------------------------------|
| obc_mode        | 2 (BOOTLOADER)         | Any other value                   |
| bat_voltage     | 27-29 V                | < 26 V (low battery)             |
| bat_soc         | > 60%                  | < 40% (unexpected power drain)    |
| obc_temp        | 10-40 deg C            | Outside range (thermal issue)     |
| uptime          | ~1800+ s (30 min+)     | < 1800 s (recent reset)          |
| reboot_count    | 0 or 1                 | > 1 (multiple resets, investigate)|

### 4.3 First Contact Procedure

1. Acquire beacon signal at AOS. Verify carrier lock on ground receiver.
2. Decode beacon packet (SID 11). Record all parameter values.
3. Verify spacecraft health from beacon parameters (see table above).
4. Uplink time synchronisation command (`SET_TIME`) to correct onboard clock.
5. Verify time sync by checking next beacon packet timestamp.

**GO/NO-GO Checkpoint 2: First Contact Health Assessment**
- Criteria: Beacon received, all parameters nominal, OBC in BOOTLOADER.
- Decision: Proceed to application boot.

## 5. Bootloader Operations

### 5.1 Limited HK in BOOTLOADER

While in BOOTLOADER mode, **only SID 11 (Beacon)** is emitted, and it is sourced from the dedicated **bootloader APID (0x002)** — distinct from the application APID (0x001) used once the OBC application is running. This gives ground a clear, unambiguous signature of bootloader vs. application state:

- Beacon packet (SID 11) every 30 s on bootloader APID 0x002.
- No TM storage exists — commanded dumps will fail, there are no stores to enable.
- No AOCS / TCS / Payload HK and no subsystem-level telemetry.
- No periodic event log: only severity ≥ 2 events generated by the bootloader itself are emitted.

Full housekeeping telemetry (per-subsystem SIDs 1–6) and onboard TM stores come online only after the application software has booted successfully (phase 4). An OBC reboot at any later phase automatically reverts the spacecraft to this bootloader/beacon-only state until the application re-boots.

### 5.2 Restricted Commands in BOOTLOADER

Only the following commands are accepted:

| Command              | Use During LEOP                                   |
|----------------------|---------------------------------------------------|
| OBC_SET_MODE (0)     | Boot application software                        |
| SET_TIME             | Synchronise onboard clock                        |
| HK_REQUEST (SID 11)  | Request immediate beacon packet (vs. waiting 100 s) |
| SW_UPLOAD            | Upload software patch (if needed)                 |
| SW_ACTIVATE          | Activate uploaded software (if needed)            |

All other commands will be rejected. Do not attempt subsystem commanding in BOOTLOADER.

## 6. Application Boot and Verification

### 6.1 Boot Procedure

1. Verify beacon health is nominal (Checkpoint 2 passed).
2. Send `OBC_SET_MODE` (mode=0) to command transition from BOOTLOADER to APPLICATION.
3. Wait 30 seconds for application software to load and perform POST.
4. Monitor for the appearance of full housekeeping telemetry (SID 1-6).
5. If no HK appears within 60 seconds, the application boot may have failed.
   Check for beacon reappearance (indicating fallback to BOOTLOADER).

### 6.2 Post-Boot Verification

Once application software is running, verify:

| Check                          | Parameter        | Expected Value              |
|--------------------------------|------------------|-----------------------------|
| OBC mode                       | obc_mode (0x0300)| 0 (NOMINAL) or 1 (SAFE)    |
| CPU load                       | cpu_load (0x0302)| < 50% (post-boot settling)  |
| HK generation                  | tm_pkt_count     | Incrementing at 1 Hz        |
| Battery health                 | bat_soc (0x0101) | > 50%                       |
| Bus voltage                    | bus_voltage      | 27-29 V                     |
| TC processing                  | tc_exec_count    | Incrementing normally       |

### 6.3 Recovery from Failed Boot

If the application fails to start:

1. Wait for OBC to fall back to BOOTLOADER (3 consecutive watchdog resets).
2. Assess reboot_count in beacon packet.
3. Consider uploading a patched application via `SW_UPLOAD`.
4. If patch is not available, maintain spacecraft in BOOTLOADER with beacon mode
   until a patch can be prepared and uplinked during subsequent contacts.

**GO/NO-GO Checkpoint 3: Application Boot Successful**
- Criteria: Full HK telemetry flowing, OBC in NOMINAL or SAFE mode.
- Decision: Proceed to sequential power-on.

## 7. Sequential Power-On Procedure

After application boot, subsystems are powered on one at a time to verify health and
manage the power budget. The order is critical — each step must be verified before
proceeding.

### 7.1 Power-On Sequence

| Step | Subsystem           | PDM Line | Verification                          | Power Impact |
|------|---------------------|----------|---------------------------------------|--------------|
| 1    | Battery heater      | SW-5     | htr_battery = 1; bat_temp stabilising | +8 W         |
| 2    | Magnetometer A      | SW-2     | mag_a_valid = 1; mag readings nominal | +1 W         |
| 3    | CSS (6 heads)       | SW-2     | css_1 through css_6 responding        | +0.5 W       |
| 4    | Gyros               | SW-2     | Rate readings nominal                 | +3 W         |
| 5    | Star camera (zenith)| SW-2     | Valid attitude fix (if conditions met) | +2 W         |
| 6    | GPS receiver        | SW-2     | Position fix (may take 15-30 min)     | +1 W         |
| 7    | Reaction wheels     | SW-3     | All 4 wheels responding, 0 RPM        | +4 W (idle)  |
| 8    | Magnetorquers       | SW-3     | Coil currents responding to commands  | +2 W         |

**Note:** Do not power on the payload (SW-4) or redundant units (SW-6, SW-7) during LEOP
unless specifically required for recovery.

### 7.2 Power Budget Monitoring

After each power-on step, verify:

- `power_cons` (0x0106) has increased by the expected amount.
- `bat_soc` (0x0101) is not declining excessively.
- `power_gen` (0x0107) exceeds `power_cons` during sunlit portions of the orbit.

**GO/NO-GO Checkpoint 4: Sequential Power-On Complete**
- Criteria: All AOCS sensors and actuators powered and healthy.
- Decision: Proceed to ADCS commissioning.

## 8. ADCS Commissioning Sequence

The ADCS commissioning verifies each attitude determination and control component in a
specific order: sensors first, then determination algorithms, then actuators, then
control loops.

### 8.1 Phase 1: Sensor Commissioning

#### Step 1: Magnetometer Calibration

1. Verify MAG-A is powered and `mag_a_valid` = 1.
2. Record magnetometer readings over one full orbit.
3. Compare measured magnetic field against the IGRF model for the current position.
4. Compute and upload calibration offsets if residuals exceed 500 nT.
5. Verify calibrated readings match model to within 200 nT.

#### Step 2: CSS Verification

1. During sunlit portion, verify all 6 CSS heads are reporting non-zero values on
   illuminated faces.
2. Verify that eclipsed faces report near-zero values.
3. Check that the computed Sun vector direction is consistent with the orbital model
   (expected Sun direction for current orbit position and season).
4. Verify geometric consistency: opposite faces should have complementary readings
   (if one face sees the Sun, the opposite face should be dark).

#### Step 3: Star Camera Commissioning

1. Ensure body rates are below 0.1 deg/s (detumble must be sufficient).
2. Power on zenith star camera (ST-Zenith on -Z face).
3. Wait for star camera to perform lost-in-space acquisition (up to 60 seconds).
4. Verify valid attitude quaternion output (all four components non-zero).
5. Compare star camera attitude against Sun vector (from CSS) for consistency check.
6. Verify star camera is not blinded (check Sun angle to -Z boresight > 15 deg).

#### Step 4: Gyro Bias Calibration

1. With star camera providing valid attitude, enable gyro bias estimation algorithm.
2. Accumulate calibration data for at least 10 minutes.
3. Verify gyro bias estimates converge (bias values stabilise).
4. Accept calibration and store in OBC non-volatile memory.
5. Verify calibrated rate measurements match star camera-derived rates to within
   0.01 deg/s.

**GO/NO-GO Checkpoint 5: Sensor Commissioning Complete**
- Criteria: All sensors calibrated and providing valid data.
- Decision: Proceed to attitude determination validation.

### 8.2 Phase 2: Attitude Determination Validation

1. Verify fused attitude solution (combining star camera, magnetometer, gyros) is stable.
2. Confirm `att_error` (0x0217) is computed and reasonable.
3. Verify attitude quaternion is continuous (no jumps between updates).
4. Cross-check GPS-derived position with expected orbit for consistency.
5. Record attitude solution over at least one full orbit for ground analysis.

**GO/NO-GO Checkpoint 6: Attitude Determination Validated**
- Criteria: Stable attitude solution, consistent across sensors.
- Decision: Proceed to actuator commissioning.

### 8.3 Phase 3: Actuator Commissioning

#### Step 5: Magnetorquer Sign Check

1. Command a known current to each magnetorquer coil (X, Y, Z) individually.
2. Observe the resulting torque on the spacecraft via gyro rate measurements.
3. Verify the torque direction is consistent with the expected interaction between
   the commanded dipole moment and the measured magnetic field.
4. Confirm no sign errors (wrong direction would indicate wiring issue).
5. If a sign error is detected, update the magnetorquer polarity configuration in software.

#### Step 6: Reaction Wheel Spin-Up Test

1. Command each wheel individually to a low speed (+100 RPM).
2. Verify wheel speed telemetry matches the commanded value.
3. Verify the spacecraft experiences a small counter-torque in the expected direction.
4. Spin each wheel to -100 RPM and verify correct reverse direction.
5. Return all wheels to 0 RPM.
6. Verify wheel temperatures remain nominal throughout.

#### Step 7: Control Gain Verification

1. Enable the AOCS control law with conservative (low) gains.
2. Command a small attitude offset (1 deg) and observe the response.
3. Verify the spacecraft responds in the correct direction and at a reasonable rate.
4. Check for overshoot or oscillation — adjust gains if necessary.
5. Return to zero offset and verify convergence.

**GO/NO-GO Checkpoint 7: Actuator Commissioning Complete**
- Criteria: All actuators verified, correct direction and magnitude.
- Decision: Proceed to active control tests.

### 8.4 Phase 4: Active Control Validation

#### Step 8: Rate Damping Test

1. Command AOCS to DETUMBLE mode (if not already).
2. If rates are already near zero, introduce a small perturbation via magnetorquers.
3. Verify magnetorquer-based rate damping converges to < 0.5 deg/s.
4. Verify autonomous transition to SAFE_POINT when rates are sufficiently low.

#### Step 9: Sun-Pointing Verification

1. In SAFE_POINT mode, verify spacecraft orients +Y towards the Sun.
2. Monitor `att_error` converging towards zero.
3. Verify CSS-derived Sun vector and star camera attitude are consistent.
4. Monitor power generation — confirm `power_gen` increases as Sun-pointing improves.

#### Step 10: Nadir-Pointing Test

1. Command AOCS to NADIR_POINT mode via `AOCS_SET_MODE` (mode=0).
2. Verify attitude transitions to nadir-pointing.
3. Monitor `att_error` — should converge to < 0.1 deg within 5 minutes.
4. Verify reaction wheel speeds remain within nominal range.
5. Monitor for at least one full orbit to verify stability through eclipse transitions.

**GO/NO-GO Checkpoint 8: ADCS Commissioning Complete**
- Criteria: Nadir-pointing stable, all ADCS components verified.
- Decision: Proceed to nominal transition.

## 9. Transition to Nominal Operations

### 9.1 Pre-Transition Checklist

Before declaring the spacecraft operational, verify the following:

| Item | Check                                    | Status Required           |
|------|------------------------------------------|---------------------------|
| 1    | OBC mode                                 | NOMINAL (mode=0)          |
| 2    | AOCS mode                                | NADIR_POINT (mode=0)      |
| 3    | Attitude error                           | < 0.5 deg (sustained)     |
| 4    | Battery SoC                              | > 60%                     |
| 5    | Power margin                             | power_gen > power_cons    |
| 6    | All sensors operational                  | Valid readings from all   |
| 7    | All actuators operational                | Responding to commands    |
| 8    | TTC link                                 | Stable at both stations   |
| 9    | GPS position fix                         | Valid and consistent      |
| 10   | Thermal environment                      | All zones within limits   |
| 11   | S12 monitoring enabled                   | All FDIR rules active     |
| 12   | S19 event-actions enabled                | All automated responses active |

### 9.2 Nominal Transition Command Sequence

1. Verify pre-transition checklist (all items pass).
2. Flight Director initiates GO/NO-GO poll of all positions.
3. All positions report GO.
4. Enable all S12 monitoring definitions (if not already enabled).
5. Enable all S19 event-action definitions.
6. Command full-rate housekeeping telemetry (1 Hz, all SIDs).
7. Switch TTC to high-rate mode (64 kbps).
8. Record transition time for mission log.

**GO/NO-GO Checkpoint 9: Transition to Nominal Operations**
- Criteria: All checklist items passed, all positions report GO.
- Decision: Spacecraft declared operational; LEOP phase complete.

### 9.3 Post-LEOP Activities

After the LEOP phase is complete, the following commissioning activities continue during
the Commissioning phase:

| Activity                           | Timeline        | Responsibility |
|------------------------------------|-----------------|----------------|
| Payload commissioning              | Day 3–7         | PLD operator   |
| First imaging test                 | Day 7–10        | PLD + FD       |
| Orbit determination convergence    | Day 3–5         | AOCS operator  |
| Thermal model correlation          | Day 3–14        | PT operator    |
| FDIR threshold refinement          | Day 7–21        | All positions  |
| Ground station contact optimisation| Day 3–10        | TTC operator   |
| First science imaging              | Day 14–30       | PLD + FD       |

## 10. Emergency Procedures During LEOP

### 10.1 No Beacon After Separation

If no beacon is received at the expected first contact:

1. Wait for the next contact window at the alternate station.
2. If still no signal, command a blind uplink of `HK_REQUEST` (SID 11) at the predicted
   spacecraft position.
3. Listen for any RF energy on the expected downlink frequency.
4. If no response after 24 hours of attempts, consider the possibility of antenna
   deployment failure and command the redundant burn wire circuit via blind uplink.

### 10.2 OBC Stuck in BOOTLOADER

If the OBC cannot transition from BOOTLOADER to APPLICATION:

1. Request beacon packet via `HK_REQUEST` (SID 11).
2. Check `reboot_count` — if elevated, the application may be crashing.
3. Attempt `OBC_SET_MODE` (mode=0) again.
4. If repeated failures, prepare a software patch for upload via `SW_UPLOAD`.
5. Refer to `contingency_obc_bootloader.yaml` for detailed recovery procedure.

### 10.3 Excessive Tumble Rates

If body rates exceed 5 deg/s after detumble attempt:

1. Verify magnetometer readings are valid (not stuck or noisy).
2. If magnetometer is faulty, switch to MAG-B.
3. If rates continue to increase, check for stuck magnetorquer (driving torque in wrong
   direction).
4. As a last resort, power off all AOCS actuators and allow passive magnetic damping
   (requires hours to days).

---

*This document was generated with AI assistance. Source: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

---

*End of Document — EOSAT1-UM-LEOP-010*

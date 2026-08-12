# CTG-019: No Telemetry at Pass Start

**Category:** Contingency
**Position Lead:** TT&C
**Cross-Position:** Flight Director, EPS/TCS
**Difficulty:** Advanced

## Objective
Recover telemetry downlink when no spacecraft signal is received at the predicted
acquisition-of-signal (AOS) time. This procedure systematically walks through the
RF chain from the ground station through the spacecraft transponder, power amplifier,
and antenna system to identify and resolve the root cause. It covers EPS power line
verification, PA commanding, antenna deployment checks, and transponder switchover.

## Prerequisites
- [ ] Ground station is tracking spacecraft with valid antenna pointing
- [ ] Orbit prediction is current (propagated within last 6 hours)
- [ ] Ground station RF chain verified: uplink transmitter on, receiver locked to expected frequency
- [ ] Doppler pre-compensation applied for current pass geometry
- [ ] At least 8 minutes of predicted pass time remaining
- [ ] Flight Director notified of no-TM condition

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| ttc.link_status | 0x0501 | 2 (LOCKED) — currently 0 (NO_LINK) or 1 (ACQUIRING) (triggering condition) |
| ttc.rssi | 0x0502 | > -95 dBm when link is active |
| ttc.link_margin | 0x0503 | > 3.0 dB when link is active |
| ttc.pa_on | 0x0516 | 1 (PA enabled) |
| ttc.pa_temp | 0x050F | < 55 C |
| ttc.tx_fwd_power | 0x050D | Nominal forward power |
| ttc.carrier_lock | 0x0510 | 1 (locked) |
| ttc.bit_sync | 0x0511 | 1 (synchronized) |
| ttc.antenna_deployed | 0x0520 | 1 (deployed) |
| ttc.eb_n0 | 0x0519 | > 10 dB when link is active |
| ttc.ber | 0x050C | < -5 when link is active |
| eps.pl_ttc_tx | 0x0112 | 1 (TX power line energised) |
| eps.bus_voltage | 0x0105 | > 27.0 V |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | -- | Request one-shot HK report |
| EPS_POWER_ON | 8 | 1 | 19 | Switch power line ON |
| TTC_PA_ON | 8 | 1 | 66 | Enable power amplifier |
| TTC_PA_OFF | 8 | 1 | 67 | Disable power amplifier |
| TTC_DEPLOY_ANTENNA | 8 | 1 | 69 | Fire burn-wire antenna deployment |
| TTC_SWITCH_REDUNDANT | 8 | 1 | 64 | Switch to redundant transponder |
| TTC_SWITCH_PRIMARY | 8 | 1 | 63 | Switch to primary transponder |
| TTC_SET_TX_POWER | 8 | 1 | 68 | Set transmit power level |
| CONNECTION_TEST | 17 | 1 | -- | S17 connection test (ping) |

## Procedure Steps

### Step 1: Confirm Ground Station Is Nominal
**Action:** Verify ground station antenna is tracking the spacecraft with valid pointing data.
**Action:** Confirm uplink transmitter is powered and set to correct frequency and polarisation.
**Action:** Verify Doppler pre-compensation is applied for current pass geometry.
**Action:** Check for local RFI or adverse weather conditions at the ground station.
**Action:** Confirm receiver is scanning on the expected downlink frequency.
**Note:** If the ground station is found to be at fault (antenna stow, TX off, wrong
frequency), resolve locally. Do not proceed with spacecraft-side recovery until ground
is confirmed nominal.
**GO/NO-GO:** Ground station confirmed nominal — proceed with spacecraft-side diagnosis.

### Step 2: Check TTC TX Power Line Status
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — Send blind HK request — EPS
**Note:** This is a blind command — no telemetry return is expected if downlink is dead.
The purpose is to trigger downlink activity and check for faint signal.
**Action:** Wait 30 s and check ground station receiver for any signal indication.
**Action:** If ground receiver detects a weak carrier, proceed to Step 4 (PA investigation).
**Action:** If no signal at all, assess whether the TTC TX power line may be off.
**Note:** The `eps.pl_ttc_tx` (0x0112) parameter indicates TX power line state, but this
requires a working downlink to read. If the TX power line was tripped by an overcurrent
event, the transponder has no power to transmit.
**TC:** `EPS_POWER_ON(line_index=2)` (Service 8, Subtype 1, func_id 19) — Send blind power-on command
this commands the TTC TX power line ON.
**Verify:** Wait 30 s for transponder to power up and re-acquire.
**Verify:** Check ground station receiver for downlink carrier.
**GO/NO-GO:** If signal appears, proceed to Step 6 (link verification). If still no signal, proceed to Step 3.

### Step 3: Command Transponder Switch (Blind)
**Note:** The primary transponder may have failed. Attempt blind switchover:
**TC:** `TTC_SWITCH_REDUNDANT` (Service 8, Subtype 1, func_id 64) — blind command
**Verify:** Wait 45 s for redundant transponder power-up and frequency lock.
**Verify:** Check ground station receiver for downlink carrier on redundant transponder frequency.
**Action:** If signal detected on redundant: proceed to Step 6 (link verification).
**Note:** Only if the redundant transponder produced no signal either.
**TC:** `TTC_SWITCH_PRIMARY` (Service 8, Subtype 1, func_id 63) — revert to the primary transponder
**Action:** Wait 30 s.
**GO/NO-GO:** If neither transponder produces signal, proceed to Step 4 (PA check).

### Step 4: Command PA ON (Blind)
**Note:** The PA may be disabled. Send blind PA enable command:
**TC:** `TTC_PA_ON` (Service 8, Subtype 1, func_id 66)
**Verify:** Wait 15 s for PA power-up.
**Verify:** Check ground station receiver for downlink carrier.
**Action:** If signal detected: proceed to Step 6 (link verification).
**Note:** If no signal: the PA enable may not have taken effect. Try cycling the PA:
**TC:** `TTC_PA_OFF` (Service 8, Subtype 1, func_id 67) — wait 10 s
**TC:** `TTC_PA_ON` (Service 8, Subtype 1, func_id 66) — wait 15 s
**Verify:** Check ground station receiver for downlink carrier.
**GO/NO-GO:** If signal detected, proceed to Step 6. If still no signal, proceed to Step 5.

### Step 5: Check Antenna Deployment and Deploy If Needed
**Action:** If the spacecraft is in early mission (post-separation) or the antenna
deployment status is uncertain, the antennas may still be stowed. A stowed antenna
will produce a severely attenuated signal (>20 dB loss).
**Note:** Send the blind antenna deployment command. The mnemonic is
`TTC_DEPLOY_ANTENNA` (singular) and the function is **69**.
**DANGER — corrected 2026-08b:** this card previously named `func_id 56`.
Func 56 is `OBC_BOOT_INHIBIT`, whose data byte makes the OBC stay in the
bootloader. Sent blind, on a spacecraft that already has no telemetry, on the
hypothesis that its antenna is stowed, it would have INHIBITED AUTO-BOOT — the
one action that turns a recoverable RF problem into a spacecraft that comes up
in the bootloader with the S-band PA inhibited and only HK SID 11 on the UHF
beacon. It was also blind, so nobody would have known.
**TC:** `TTC_DEPLOY_ANTENNA` (Service 8, Subtype 1, func_id 69) — fires the burn-wire for antenna release
**CAUTION:** This is an irreversible, one-shot pyrotechnic command. Only execute if
antenna deployment status is genuinely uncertain or known to be stowed.
**Verify:** Wait 30 s for the deployment mechanism to actuate and the antenna to
extend. If any telemetry is being received, `ttc.antenna_deployed` (0x0520)
goes to 1 and `ttc.antenna_deployment_sensor` (0x0536) reads 2 (DEPLOYED);
`ttc.antenna_deployment_ready` (0x0535) = 0 before the attempt would mean
the pyro continuity is already faulty and the command will not fire.
**Verify:** Check ground station receiver for downlink carrier. Antenna deployment should
produce a dramatic improvement in signal level (15-25 dB gain increase).
**GO/NO-GO:** If signal detected, proceed to Step 6 (link verification). If still no
signal after all RF chain checks, proceed to Step 7 (escalation).

### Step 6: Verify Link Quality and Subsystem Health
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — Request TTC housekeeping
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED)
**Verify:** `ttc.rssi` (0x0502) > -90 dBm (nominal signal strength)
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB
**Verify:** `ttc.pa_on` (0x0516) = 1
**Verify:** `ttc.pa_temp` (0x050F) < 55 C
**Verify:** `ttc.antenna_deployed` (0x0520) = 1
**Verify:** `ttc.carrier_lock` (0x0510) = 1
**Verify:** `ttc.bit_sync` (0x0511) = 1
**Verify:** `ttc.eb_n0` (0x0519) > 10 dB
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — Request EPS housekeeping
**Verify:** `eps.bus_voltage` (0x0105) > 27.0 V — confirm no EPS anomaly
**Verify:** `eps.pl_ttc_tx` (0x0112) = 1 — TX power line confirmed ON
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) — Request OBDH housekeeping
**Verify:** `obdh.mode` (0x0300) — confirm OBC did not enter safe/emergency mode
**Action:** Log which recovery step restored the link:
- Step 2: TX power line was off
- Step 3: Primary transponder failure, now on redundant
- Step 4: PA was disabled
- Step 5: Antennas were stowed
**GO/NO-GO:** Link margin > 3 dB, all subsystems nominal — recovery complete.

### Step 7: Escalation — No Link Recovery
**Action:** Log all blind commands sent with timestamps.
**Action:** Notify Flight Director and Mission Director of sustained no-TM condition.
**Action:** Confirm spacecraft autonomous safe mode timer is active (24h no-contact trigger).
**Action:** Schedule next available ground station pass for re-acquisition attempt.
**TC:** `TTC_SET_TX_POWER(level=2)` (Service 8, Subtype 1, func_id 68) — Consider increasing TX power for next attempt: prepare — high power mode for improved link budget.
**GO/NO-GO:** If no link after two consecutive pass attempts, escalate to EMG-004
(Loss of Communication Emergency).

## Verification Criteria
- [ ] `ttc.link_status` (0x0501) = 2 (LOCKED link established)
- [ ] `ttc.rssi` (0x0502) > -90 dBm
- [ ] `ttc.link_margin` (0x0503) > 3.0 dB
- [ ] `ttc.pa_on` (0x0516) = 1
- [ ] `ttc.antenna_deployed` (0x0520) = 1
- [ ] `eps.pl_ttc_tx` (0x0112) = 1 (TX power line ON)
- [ ] Root cause identified and documented
- [ ] If unrecovered: EMG-004 escalation initiated

## Contingency
- If link restores but RSSI < -95 dBm or margin < 1.5 dB: Step down to LOW
  (16 kbps) via `TTC_SET_DATA_RATE(rate=0)` (func_id 65) to gain 9.03 dB of Eb/N0
  plus 1.5 dB of coding gain. LOW is the routine operations rate, so this costs
  nothing but the ability to dump. Investigate antenna or PA degradation.
- If PA cycles on but immediately shuts down: PA may have a thermal or overcurrent
  protection trip. Check `ttc.pa_temp` (0x050F). If > 65 C, PA thermal protection
  is active. Wait for cooldown before retry.
- If antenna deployment command was sent but signal did not improve: Deployment
  mechanism may have failed (burn-wire did not fire, antenna stuck). This is a
  permanent hardware failure. Rely on stub antenna with reduced link budget.
- If OBC is found in SAFE or EMERGENCY mode after link restoration: Execute
  CTG-010 (OBDH Watchdog Recovery) or CTG-010 as appropriate before resuming
  nominal operations.
- If signal is intermittent (toggling on/off): Suspect attitude issue causing
  antenna null pattern. Request AOCS telemetry: check `aocs.att_error` (0x0217)
  and `aocs.mode` (0x020F).

# PROC-TTC-OFF-002: No Telemetry at Pass Start

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
| EPS_POWER_ON | 8 | 1 | 13 | Switch power line ON |
| TTC_PA_ON | 8 | 1 | 53 | Enable power amplifier |
| TTC_PA_OFF | 8 | 1 | 54 | Disable power amplifier |
| TTC_DEPLOY_ANTENNAS | 8 | 1 | 56 | Fire burn-wire antenna deployment |
| TTC_SWITCH_REDUNDANT | 8 | 1 | 51 | Switch to redundant transponder |
| TTC_SWITCH_PRIMARY | 8 | 1 | 50 | Switch to primary transponder |
| TTC_SET_TX_POWER | 8 | 1 | 55 | Set transmit power level |
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
**Action:** Send blind HK request: `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — EPS
**Note:** This is a blind command — no telemetry return is expected if downlink is dead.
The purpose is to trigger downlink activity and check for faint signal.
**Action:** Wait 30 s and check ground station receiver for any signal indication.
**Action:** If ground receiver detects a weak carrier, proceed to Step 4 (PA investigation).
**Action:** If no signal at all, assess whether the TTC TX power line may be off.
**Note:** The `eps.pl_ttc_tx` (0x0112) parameter indicates TX power line state, but this
requires a working downlink to read. If the TX power line was tripped by an overcurrent
event, the transponder has no power to transmit.
**Action:** Send blind power-on command: `EPS_POWER_ON(line_index=2)` (func_id 13) —
this commands the TTC TX power line ON.
**Verify:** Wait 30 s for transponder to power up and re-acquire.
**Verify:** Check ground station receiver for downlink carrier.
**GO/NO-GO:** If signal appears, proceed to Step 6 (link verification). If still no signal, proceed to Step 3.

### Step 3: Command Transponder Switch (Blind)
**Action:** The primary transponder may have failed. Attempt blind switchover:
`TTC_SWITCH_REDUNDANT` (func_id 51) — blind command
**Verify:** Wait 45 s for redundant transponder power-up and frequency lock.
**Verify:** Check ground station receiver for downlink carrier on redundant transponder frequency.
**Action:** If signal detected on redundant: proceed to Step 6 (link verification).
**Action:** If still no signal: revert to primary: `TTC_SWITCH_PRIMARY` (func_id 50).
Wait 30 s.
**GO/NO-GO:** If neither transponder produces signal, proceed to Step 4 (PA check).

### Step 4: Command PA ON (Blind)
**Action:** The PA may be disabled. Send blind PA enable command:
`TTC_PA_ON` (func_id 53)
**Verify:** Wait 15 s for PA power-up.
**Verify:** Check ground station receiver for downlink carrier.
**Action:** If signal detected: proceed to Step 6 (link verification).
**Action:** If no signal: the PA enable may not have taken effect. Try cycling the PA:
- `TTC_PA_OFF` (func_id 54) — wait 10 s
- `TTC_PA_ON` (func_id 53) — wait 15 s
**Verify:** Check ground station receiver for downlink carrier.
**GO/NO-GO:** If signal detected, proceed to Step 6. If still no signal, proceed to Step 5.

### Step 5: Check Antenna Deployment and Deploy If Needed
**Action:** If the spacecraft is in early mission (post-separation) or the antenna
deployment status is uncertain, the antennas may still be stowed. A stowed antenna
will produce a severely attenuated signal (>20 dB loss).
**Action:** Send blind antenna deployment command:
`TTC_DEPLOY_ANTENNAS` (func_id 56) — fires burn-wire for antenna release
**CAUTION:** This is an irreversible, one-shot pyrotechnic command. Only execute if
antenna deployment status is genuinely uncertain or known to be stowed.
**Verify:** Wait 30 s for deployment mechanism to actuate and antennas to extend.
**Verify:** Check ground station receiver for downlink carrier. Antenna deployment should
produce a dramatic improvement in signal level (15-25 dB gain increase).
**GO/NO-GO:** If signal detected, proceed to Step 6 (link verification). If still no
signal after all RF chain checks, proceed to Step 7 (escalation).

### Step 6: Verify Link Quality and Subsystem Health
**Action:** Request TTC housekeeping: `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED)
**Verify:** `ttc.rssi` (0x0502) > -90 dBm (nominal signal strength)
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB
**Verify:** `ttc.pa_on` (0x0516) = 1
**Verify:** `ttc.pa_temp` (0x050F) < 55 C
**Verify:** `ttc.antenna_deployed` (0x0520) = 1
**Verify:** `ttc.carrier_lock` (0x0510) = 1
**Verify:** `ttc.bit_sync` (0x0511) = 1
**Verify:** `ttc.eb_n0` (0x0519) > 10 dB
**Action:** Request EPS housekeeping: `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) > 27.0 V — confirm no EPS anomaly
**Verify:** `eps.pl_ttc_tx` (0x0112) = 1 — TX power line confirmed ON
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
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
**Action:** Consider increasing TX power for next attempt: prepare `TTC_SET_TX_POWER(level=2)`
(func_id 55) — high power mode for improved link budget.
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
- If link restores but RSSI < -95 dBm or margin < 1.5 dB: Reduce data rate via
  `TTC_SET_DATA_RATE(rate=0)` (func_id 52) to improve margin. Investigate antenna
  or PA degradation.
- If PA cycles on but immediately shuts down: PA may have a thermal or overcurrent
  protection trip. Check `ttc.pa_temp` (0x050F). If > 65 C, PA thermal protection
  is active. Wait for cooldown before retry.
- If antenna deployment command was sent but signal did not improve: Deployment
  mechanism may have failed (burn-wire did not fire, antenna stuck). This is a
  permanent hardware failure. Rely on stub antenna with reduced link budget.
- If OBC is found in SAFE or EMERGENCY mode after link restoration: Execute
  CON-005 (OBDH Watchdog Recovery) or CON-010 as appropriate before resuming
  nominal operations.
- If signal is intermittent (toggling on/off): Suspect attitude issue causing
  antenna null pattern. Request AOCS telemetry: check `aocs.att_error` (0x0217)
  and `aocs.mode` (0x020F).

# CTG-014: BER Anomaly

**Category:** Contingency
**Position Lead:** TT&C
**Cross-Position:** Flight Director
**Difficulty:** Intermediate

## Objective
Investigate an anomalous increase in bit error rate (BER) on the telemetry downlink.
This procedure systematically checks link quality parameters, power amplifier health,
and transponder condition to identify the root cause. Corrective actions include data
rate reduction and transponder switchover if the primary unit is degraded.

## Prerequisites
- [ ] BER anomaly detected — `ttc.ber` (0x050C) >= -5 (BER worse than 1e-5)
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 2 (LOCKED)
- [ ] Ground station has confirmed the anomaly is not ground-segment related
- [ ] Flight Director notified

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| ttc.ber | 0x050C | < -5 nominal; >= -5 triggers this procedure |
| ttc.eb_n0 | 0x0519 | > 10 dB nominal |
| ttc.pa_temp | 0x050F | < 55 C nominal |
| ttc.pa_on | 0x0516 | 1 (PA active) |
| ttc.tx_fwd_power | 0x050D | Nominal forward power |
| ttc.link_margin | 0x0503 | > 3 dB nominal |
| ttc.rssi | 0x0502 | Signal strength |
| ttc.xpdr_temp | 0x0507 | < 50 C nominal |
| ttc.carrier_lock | 0x0510 | 1 (locked) |
| ttc.bit_sync | 0x0511 | 1 (synchronized) |
| ttc.frame_sync | 0x0512 | 1 (synchronized) |
| ttc.tm_data_rate | 0x0506 | Active information rate (SAFE 1000 / LOW 16000 / HIGH 128000 bps) |
| ttc.tm_tier | 0x0539 | ACTIVE tier index (0=SAFE, 1=LOW, 2=HIGH) |
| ttc.tm_tier_cmd | 0x053A | COMMANDED tier index |
| ttc.downlink_mode | 0x053C | 1 while a dump is in progress |
| ttc.contact_elevation | 0x050A | Current elevation (HIGH needs >= 10 deg) |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| TTC_SET_DATA_RATE | 8 | 1 | 65 | Step down the TM rate ladder (0=LOW 16 kbps, 1=HIGH 128 kbps) |
| TTC_SET_DOWNLINK_MODE | 8 | 1 | 79 | Leave DOWNLINK mode when abandoning a high-rate dump |
| TTC_SWITCH_REDUNDANT | 8 | 1 | 64 | Switch to redundant transponder |
| TTC_SWITCH_PRIMARY | 8 | 1 | 63 | Switch back to primary transponder |
| TTC_PA_OFF | 8 | 1 | 67 | Disable power amplifier |
| TTC_PA_ON | 8 | 1 | 66 | Enable power amplifier |
| TTC_SET_TX_POWER | 8 | 1 | 68 | Adjust transmit power level |

## Procedure Steps

### Step 1: Characterize BER Anomaly
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — Request TTC housekeeping
**Verify:** `ttc.ber` (0x050C) — record current value (expected >= -5)
**Verify:** `ttc.eb_n0` (0x0519) — record Eb/N0 value
**Verify:** `ttc.link_margin` (0x0503) — record link margin
**Verify:** `ttc.rssi` (0x0502) — record RSSI
**Verify:** `ttc.tm_data_rate` (0x0506) — record current data rate
**Verify:** `ttc.contact_elevation` (0x050A) — record elevation angle
**Verify:** `ttc.carrier_lock` (0x0510), `ttc.bit_sync` (0x0511), `ttc.frame_sync` (0x0512)
**Note:** If elevation is low (< 10 deg), the BER degradation may be expected due to
atmospheric attenuation and low gain. Wait for higher elevation before declaring anomaly.
**GO/NO-GO:** BER anomaly confirmed not due to geometry — proceed to PA investigation.

### Step 2: Check Power Amplifier Temperature
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — Request TTC housekeeping
**Verify:** `ttc.pa_temp` (0x050F) — record value
**Verify:** `ttc.pa_on` (0x0516) = 1 (PA active)
**Verify:** `ttc.tx_fwd_power` (0x050D) — record forward power
**Action:** Assess PA health:
- If `ttc.pa_temp` > 55 C: PA is overheating. Proceed to Step 3 (thermal mitigation).
- If `ttc.pa_temp` < 55 C and `ttc.tx_fwd_power` is lower than expected: PA may be
  degraded. Proceed to Step 4 (rate reduction).
- If PA parameters are all nominal: The issue may be ground-segment or interference
  related. Proceed to Step 4 (rate reduction) as mitigation.
**GO/NO-GO:** PA assessment complete — follow appropriate branch.

### Step 3: PA Overheating — Thermal Mitigation
**Note:** If PA temperature > 55 C, reduce thermal load:
**TC:** `TTC_PA_OFF` (Service 8, Subtype 1, func_id 67) — Command PA off
**Note:** Wait 120 s for cooldown
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — Request TTC housekeeping
**Note:** **Verify:** `ttc.pa_temp` (0x050F) trending downward
**TC:** `TTC_PA_ON` (Service 8, Subtype 1, func_id 66) — Command PA back on
**Note:** **Verify:** `ttc.pa_on` (0x0516) = 1 within 5 s
**Note:** If PA temperature is still > 50 C after PA-on, reduce TX power level:
**TC:** `TTC_SET_TX_POWER(level=0)` (Service 8, Subtype 1, func_id 68) — set to low power (1 W)
**Verify:** `ttc.pa_temp` (0x050F) stabilizing
**Verify:** `ttc.ber` (0x050C) — check if BER improves with cooler PA
**Note:** If PA overheating persists, follow EMG-006 for thermal investigation
of the transponder zone.
**GO/NO-GO:** If BER improved, continue monitoring. If not, proceed to Step 4.

### Step 4: Step Down the Rate Ladder
**Note:** Only worth doing if the ACTIVE tier (0x0539) is above LOW. A step from HIGH
(128 kbps) to LOW (16 kbps) buys 9.03 dB of Eb/N0 — the `-10*log10(R)` term — and also
changes the waveform from QPSK RS(255,223) to BPSK conv(7,1/2)+RS, worth a further
1.5 dB of coding gain. If the link is already at LOW there is no operational rung below
it: SAFE (1 kbps, ex-BEACON) is the bootloader lifeline and is not commandable here, so skip to Step 5.
**TC:** `TTC_SET_DATA_RATE(rate=0)` (Service 8, Subtype 1, func_id 65) — Step down to LOW
**TC:** `TTC_SET_DATA_RATE(rate=0)` (Service 8, Subtype 1, func_id 65)
**Verify:** `ttc.tm_data_rate` (0x0506) = 16000 bps within 10 s
**Verify:** `ttc.tm_tier` (0x0539) = 1 (LOW) and equal to `ttc.tm_tier_cmd` (0x053A)
**Verify:** `ttc.tm_symbol_rate` (0x053B) = 16000 Bd (BPSK)
**Verify:** `ttc.carrier_lock` (0x0510) = 1
**Verify:** `ttc.bit_sync` (0x0511) = 1
**Action:** If a dump was in progress, release the gate as well:
**TC:** `TTC_SET_DOWNLINK_MODE(on=0)` (Service 8, Subtype 1, func_id 79)
**Verify:** `ttc.downlink_mode` (0x053C) = 0
**Note:** Wait 30 s for BER to stabilize, then request TTC housekeeping:
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `ttc.ber` (0x050C) < -5 (BER improved at the lower rung)
**Verify:** `ttc.eb_n0` (0x0519) > 10 dB at the lower rung
**Action:** If BER improved:
- Root cause is likely insufficient link margin at HIGH for this pass geometry.
- Continue at LOW for this contact — LOW is the routine operations rate, so nothing is
  lost except the dump. Re-plan the dump onto a higher pass and reassess the link
  budget (`tools/link_budget_ladder.py`) before the next high-rate attempt.
**Action:** If BER did NOT improve at LOW: the issue is deeper than data rate.
Proceed to Step 5 (transponder switchover).
**GO/NO-GO:** If BER acceptable at LOW, monitor and log. If not, proceed to Step 5.

### Step 5: Try Transponder Switchover
**TC:** `TTC_SWITCH_REDUNDANT` (Service 8, Subtype 1, func_id 64) — Switch to redundant transponder
**Verify:** Wait 15 s for redundant transponder to initialize
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — Request TTC housekeeping
**Verify:** `ttc.carrier_lock` (0x0510) = 1 (re-acquired on redundant unit)
**Verify:** `ttc.bit_sync` (0x0511) = 1
**Verify:** `ttc.frame_sync` (0x0512) = 1
**Verify:** `ttc.ber` (0x050C) — check if BER improved
**Verify:** `ttc.xpdr_temp` (0x0507) — record redundant transponder temperature
**Action:** If BER improved on redundant transponder: The primary transponder is degraded.
Log as hardware anomaly. Continue operations on redundant unit.
**Note:** If BER NOT improved: Issue is likely external (interference, ground segment,
**TC:** `TTC_SWITCH_PRIMARY` (Service 8, Subtype 1, func_id 63) — or antenna issue). Revert to primary transponder:   .
**Note:** Continue at LOW (16 kbps) and escalate for ground segment investigation.
**GO/NO-GO:** Transponder assessment complete. Log findings and proceed with best
configuration.

## Verification Criteria
- [ ] `ttc.ber` (0x050C) < -5 (acceptable BER restored)
- [ ] `ttc.carrier_lock` (0x0510) = 1
- [ ] `ttc.bit_sync` (0x0511) = 1 and `ttc.frame_sync` (0x0512) = 1
- [ ] `ttc.pa_temp` (0x050F) < 55 C
- [ ] Root cause identified or narrowed down
- [ ] Anomaly report filed with all recorded telemetry values

## Contingency
- If link is lost during transponder switchover: Ground station should sweep for signal
  on new transponder frequency. Wait up to 60 s for re-acquisition. If no link after
  60 s, attempt to switch back: `TTC_SWITCH_PRIMARY` (func_id 63). If still no link,
  follow CTG-003.
- If both transponders show degraded BER: Suspect antenna or feed network issue. Hold
  at LOW (16 kbps — the lowest commandable operational rung; the SAFE rung is bootloader-only)
  and increase TX power to high: `TTC_SET_TX_POWER(level=2)`.
  Escalate to engineering team.
- If PA fails to re-enable after cooldown: PA may have permanent failure. Switch to
  redundant transponder which has its own PA: `TTC_SWITCH_REDUNDANT` (func_id 64).
- If BER continues to degrade during the contact: Risk of complete link loss. Uplink
  any critical commands immediately while link remains. Prepare for autonomous
  operations until next contact.

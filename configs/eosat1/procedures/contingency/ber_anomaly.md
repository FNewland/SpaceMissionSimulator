# PROC-TTC-OFF-001: BER Anomaly Investigation

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
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
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
| ttc.tm_data_rate | 0x0506 | Current data rate |
| ttc.contact_elevation | 0x050A | Current elevation |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| TTC_SET_DATA_RATE | 8 | 1 | 52 | Reduce data rate |
| TTC_SWITCH_REDUNDANT | 8 | 1 | 51 | Switch to redundant transponder |
| TTC_SWITCH_PRIMARY | 8 | 1 | 50 | Switch back to primary transponder |
| TTC_PA_OFF | 8 | 1 | 54 | Disable power amplifier |
| TTC_PA_ON | 8 | 1 | 53 | Enable power amplifier |
| TTC_SET_TX_POWER | 8 | 1 | 55 | Adjust transmit power level |

## Procedure Steps

### Step 1: Characterize BER Anomaly
**Action:** Request TTC housekeeping: `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
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
**Action:** Request TTC housekeeping: `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
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
**Action:** If PA temperature > 55 C, reduce thermal load:
- Command PA off: `TTC_PA_OFF` (func_id 54)
- Wait 120 s for cooldown
- Request TTC housekeeping: `HK_REQUEST(sid=5)`
- **Verify:** `ttc.pa_temp` (0x050F) trending downward
- Command PA back on: `TTC_PA_ON` (func_id 53)
- **Verify:** `ttc.pa_on` (0x0516) = 1 within 5 s
**Action:** If PA temperature is still > 50 C after PA-on, reduce TX power level:
`TTC_SET_TX_POWER(level=0)` (func_id 55) — set to low power (1 W)
**Verify:** `ttc.pa_temp` (0x050F) stabilizing
**Verify:** `ttc.ber` (0x050C) — check if BER improves with cooler PA
**Note:** If PA overheating persists, follow PROC-TCS-OFF-001 for thermal investigation
of the transponder zone.
**GO/NO-GO:** If BER improved, continue monitoring. If not, proceed to Step 4.

### Step 4: Try Data Rate Reduction
**Action:** Reduce data rate to LOW: `TTC_SET_DATA_RATE(rate=0)` (func_id 52)
**Verify:** `ttc.tm_data_rate` (0x0506) = 1000 bps within 10 s
**Verify:** `ttc.carrier_lock` (0x0510) = 1
**Verify:** `ttc.bit_sync` (0x0511) = 1
**Action:** Wait 30 s for BER to stabilize, then request TTC housekeeping:
`HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Verify:** `ttc.ber` (0x050C) < -5 (BER improved at lower rate)
**Verify:** `ttc.eb_n0` (0x0519) > 10 dB at lower rate
**Action:** If BER improved:
- Root cause is likely insufficient link margin at the higher rate.
- Continue at low rate for this contact. Reassess link budget before next high-rate
  attempt.
**Action:** If BER did NOT improve at low rate: The issue is deeper than data rate.
Proceed to Step 5 (transponder switchover).
**GO/NO-GO:** If BER acceptable at low rate, monitor and log. If not, proceed to Step 5.

### Step 5: Try Transponder Switchover
**Action:** Switch to redundant transponder: `TTC_SWITCH_REDUNDANT` (func_id 51)
**Verify:** Wait 15 s for redundant transponder to initialize
**Action:** Request TTC housekeeping: `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Verify:** `ttc.carrier_lock` (0x0510) = 1 (re-acquired on redundant unit)
**Verify:** `ttc.bit_sync` (0x0511) = 1
**Verify:** `ttc.frame_sync` (0x0512) = 1
**Verify:** `ttc.ber` (0x050C) — check if BER improved
**Verify:** `ttc.xpdr_temp` (0x0507) — record redundant transponder temperature
**Action:** If BER improved on redundant transponder: The primary transponder is degraded.
Log as hardware anomaly. Continue operations on redundant unit.
**Action:** If BER NOT improved: Issue is likely external (interference, ground segment,
or antenna issue). Revert to primary transponder: `TTC_SWITCH_PRIMARY` (func_id 50).
Continue at low rate and escalate for ground segment investigation.
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
  60 s, attempt to switch back: `TTC_SWITCH_PRIMARY` (func_id 50). If still no link,
  follow PROC-TTC-LINK-LOSS.
- If both transponders show degraded BER: Suspect antenna or feed network issue. Reduce
  to minimum data rate, increase TX power to high: `TTC_SET_TX_POWER(level=2)`.
  Escalate to engineering team.
- If PA fails to re-enable after cooldown: PA may have permanent failure. Switch to
  redundant transponder which has its own PA: `TTC_SWITCH_REDUNDANT` (func_id 51).
- If BER continues to degrade during the contact: Risk of complete link loss. Uplink
  any critical commands immediately while link remains. Prepare for autonomous
  operations until next contact.

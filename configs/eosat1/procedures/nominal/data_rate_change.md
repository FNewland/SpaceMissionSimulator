# PROC-TTC-NOM-001: TTC Data Rate Change

**Category:** Nominal
**Position Lead:** TT&C
**Cross-Position:** Payload Operations
**Difficulty:** Beginner

## Objective
Change the telemetry downlink data rate during a ground station contact to optimize
data throughput based on current link conditions. This procedure verifies adequate link
margin before commanding a rate change and confirms that the new bit error rate remains
acceptable after the transition.

## Prerequisites
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] Carrier lock established — `ttc.carrier_lock` (0x0510) = 1
- [ ] Ground station contact in progress with sufficient remaining contact time (> 2 min)
- [ ] Current link conditions assessed and target rate identified

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| ttc.link_status | 0x0501 | 1 (active) |
| ttc.link_margin | 0x0503 | > 3.0 dB for high rate, > 0 dB minimum |
| ttc.tm_data_rate | 0x0506 | Current data rate in bps |
| ttc.ber | 0x050C | < -5 (i.e., BER < 1e-5) |
| ttc.eb_n0 | 0x0519 | > 10.0 dB for high rate |
| ttc.rssi | 0x0502 | Signal strength |
| ttc.carrier_lock | 0x0510 | 1 (locked) |
| ttc.bit_sync | 0x0511 | 1 (synchronized) |
| ttc.frame_sync | 0x0512 | 1 (synchronized) |
| ttc.contact_elevation | 0x050A | Elevation angle |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| TTC_SET_DATA_RATE | 8 | 1 | 52 | Set TM data rate (0=low, 1=high) |

## Procedure Steps

### Step 1: Assess Current Link Conditions
**Action:** Request TTC housekeeping: `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Verify:** `ttc.link_status` (0x0501) = 1
**Verify:** `ttc.carrier_lock` (0x0510) = 1
**Verify:** `ttc.bit_sync` (0x0511) = 1
**Verify:** `ttc.link_margin` (0x0503) — record current value
**Verify:** `ttc.ber` (0x050C) — record current value (expected < -5)
**Verify:** `ttc.eb_n0` (0x0519) — record current value
**Verify:** `ttc.contact_elevation` (0x050A) — record current elevation
**Note:** Record all values as pre-change baseline.
**GO/NO-GO:** Link is active and stable — proceed.

### Step 2: Verify Link Margin for Target Rate
**Action:** Evaluate link margin for the requested rate change:
- For switch to HIGH rate (64 kbps): Require `ttc.link_margin` (0x0503) > 3.0 dB
  and `ttc.eb_n0` (0x0519) > 10.0 dB and `ttc.contact_elevation` (0x050A) > 10 deg.
- For switch to LOW rate (1 kbps): No minimum margin required (always acceptable).
**GO/NO-GO:** If switching to HIGH rate and margin <= 3.0 dB, do NOT proceed. Remain
at low rate. Consider waiting for higher elevation before retrying.

### Step 3: Command Data Rate Change
**Action:** Command rate change: `TTC_SET_DATA_RATE(rate=1)` for HIGH or
`TTC_SET_DATA_RATE(rate=0)` for LOW (func_id 52)
**Verify:** `ttc.tm_data_rate` (0x0506) reflects new rate within 10 s:
- HIGH: 64000 bps
- LOW: 1000 bps
**Verify:** `ttc.carrier_lock` (0x0510) = 1 (maintained through transition)
**Verify:** `ttc.bit_sync` (0x0511) = 1 (re-acquired within 5 s)
**Verify:** `ttc.frame_sync` (0x0512) = 1 (re-acquired within 10 s)
**GO/NO-GO:** Data rate changed and synchronization confirmed — proceed to BER check.

### Step 4: Verify New BER Is Acceptable
**Action:** Wait 30 s for BER to stabilize, then request TTC housekeeping:
`HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Verify:** `ttc.ber` (0x050C) < -5 (BER better than 1e-5)
**Verify:** `ttc.link_margin` (0x0503) > 1.0 dB at new rate
**Verify:** `ttc.eb_n0` (0x0519) > 8.0 dB
**Action:** If BER >= -5, immediately revert to previous rate (see Contingency).
**GO/NO-GO:** BER acceptable at new rate — rate change complete.

## Verification Criteria
- [ ] `ttc.tm_data_rate` (0x0506) matches commanded rate
- [ ] `ttc.ber` (0x050C) < -5 at new rate
- [ ] `ttc.carrier_lock` (0x0510) = 1 maintained throughout
- [ ] `ttc.link_margin` (0x0503) > 1.0 dB at new rate
- [ ] No frame sync losses detected during or after transition

## Contingency
- If carrier lock is lost during rate change: Wait 15 s for automatic re-acquisition.
  If not recovered, command rate back to LOW: `TTC_SET_DATA_RATE(rate=0)`. If still
  no lock, escalate to PROC-TTC-OFF-001 (BER Anomaly Investigation).
- If BER exceeds -5 at new rate: Immediately revert to previous rate using
  `TTC_SET_DATA_RATE`. Verify BER returns to acceptable levels. Log anomaly.
- If bit/frame sync not acquired within 30 s: Revert to previous rate. If sync still
  not acquired, check transponder health — follow PROC-TTC-OFF-001.
- If contact is about to end (< 1 min remaining): Do NOT attempt rate change. Wait
  for next contact.

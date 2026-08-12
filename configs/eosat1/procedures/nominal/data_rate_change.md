# NOM-011: Data Rate Change

**Category:** Nominal
**Position Lead:** TT&C
**Cross-Position:** Payload Operations
**Difficulty:** Beginner

## Objective
Move the telemetry downlink between rungs of the TM data-rate ladder during a ground
station contact. The ladder is defined in `subsystems/ttc.yaml tm_rate_tiers` and has
three rungs:

| Tier | Rate | Modulation / coding | Purpose | Gates |
|---|---|---|---|---|
| BEACON | 1 000 bps | BPSK, conv(7,1/2)+RS | Bootloader / beacon lifeline | Not commandable — entered by `TTC_SET_BEACON_MODE` or by the bootloader |
| LOW | 16 000 bps | BPSK, conv(7,1/2)+RS | **Routine operations** and the safe-mode / FDIR fallback | None. Works with the antenna stowed and at any elevation |
| HIGH | 128 000 bps | QPSK, RS(255,223) | Delayed-telemetry playback and image dumps ONLY | Antenna deployed **and** DOWNLINK mode **and** elevation >= 10 deg |

The spacecraft sits at **LOW** for routine operations. HIGH is not a "better" rate to
be left on: it is earned for a dump and released afterwards. A tier whose gates are not
satisfied is **rejected** with an S1.8 execution failure (error 0x0007) — never silently
clamped — so a failed rate change tells you exactly which gate is open.

This procedure verifies adequate link margin before commanding a rate change and
confirms that the new bit error rate remains acceptable after the transition.

## Prerequisites
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 2 (LOCKED)
- [ ] Carrier lock established — `ttc.carrier_lock` (0x0510) = 1
- [ ] Ground station contact in progress with sufficient remaining contact time (> 2 min)
- [ ] Ground station modem configured for the target tier's symbol rate and modulation
- [ ] Current link conditions assessed and target tier identified

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| ttc.link_status | 0x0501 | 2 (LOCKED) |
| ttc.link_margin | 0x0503 | > 3.0 dB for HIGH, > 0 dB minimum |
| ttc.tm_data_rate | 0x0506 | Active information rate (1000 / 16000 / 128000 bps) |
| ttc.tm_tier | 0x0539 | ACTIVE tier index (0=BEACON, 1=LOW, 2=HIGH) |
| ttc.tm_tier_cmd | 0x053A | COMMANDED tier index — differs from 0x0539 when a gate refuses the tier |
| ttc.tm_symbol_rate | 0x053B | 16000 Bd at LOW (BPSK), 64000 Bd at HIGH (QPSK) |
| ttc.downlink_mode | 0x053C | 1 required before HIGH is accepted |
| ttc.antenna_deployed | 0x0520 | 1 required before HIGH is accepted |
| ttc.ber | 0x050C | < -5 (i.e., BER < 1e-5) |
| ttc.eb_n0 | 0x0519 | > 10.0 dB for HIGH |
| ttc.rssi | 0x0502 | Signal strength |
| ttc.carrier_lock | 0x0510 | 1 (locked) |
| ttc.bit_sync | 0x0511 | 1 (synchronized) |
| ttc.frame_sync | 0x0512 | 1 (synchronized) |
| ttc.contact_elevation | 0x050A | >= 10.0 deg required for HIGH |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| TTC_SET_DOWNLINK_MODE | 8 | 1 | 79 | Enter/leave DOWNLINK mode — the mode gate on HIGH |
| TTC_SET_DATA_RATE | 8 | 1 | 65 | Select a ladder tier (0=LOW, 1=HIGH; 255=BEACON, bootloader only) |

## Procedure Steps

### Step 1 --- Assess Current Link Conditions
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- request TT&C housekeeping
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED)
**Verify:** `ttc.carrier_lock` (0x0510) = 1
**Verify:** `ttc.bit_sync` (0x0511) = 1
**Verify:** `ttc.tm_tier` (0x0539) --- record the ACTIVE tier
**Verify:** `ttc.tm_tier_cmd` (0x053A) --- record the COMMANDED tier
**Verify:** `ttc.link_margin` (0x0503) --- record current value
**Verify:** `ttc.ber` (0x050C) --- record current value (expected < -5)
**Verify:** `ttc.eb_n0` (0x0519) --- record current value
**Verify:** `ttc.contact_elevation` (0x050A) --- record current elevation
**Note:** If 0x0539 and 0x053A already disagree, a gate is refusing the commanded tier.
Resolve that before commanding anything else — the ladder is not "stuck", it is obeying
a rule, and 0x0520 / 0x053C / 0x050A say which one.
**Note:** Record all values as pre-change baseline.
**GO/NO-GO:** Link is active and stable --- proceed.

### Step 2 --- Verify the Gates and the Margin for the Target Tier
**Action:** For a change **down to LOW (16 kbps)**: no gate and no minimum margin.
LOW is the routine operations rate and is always acceptable. Skip to Step 4.
**Action:** For a change **up to HIGH (128 kbps)**, all four must hold:
- `ttc.antenna_deployed` (0x0520) = 1
- `ttc.contact_elevation` (0x050A) >= 10.0 deg **and** staying above 10 deg for the
  planned dump duration (a 4 MiB image is 328 s of contact at HIGH)
- `ttc.link_margin` (0x0503) > 3.0 dB and `ttc.eb_n0` (0x0519) > 10.0 dB
- Ground station modem reconfigured for 64 kBd QPSK RS(255,223)
**Note:** The tier step from LOW to HIGH is 9.03 dB of the link budget
(`-10*log10(128000/16000)`). Margin at HIGH is 12 dB below margin at LOW at the same
geometry — see `docs/LINK_BUDGET_AND_RATES.md` for the per-station table.
**GO/NO-GO:** If any gate is open or margin <= 3.0 dB, do NOT proceed. Remain at LOW.
Consider waiting for higher elevation before retrying.

### Step 3 --- Enter DOWNLINK Mode (HIGH only)
**TC:** `TTC_SET_DOWNLINK_MODE(on=1)` (Service 8, Subtype 1, func_id 79)
**Verify:** `ttc.downlink_mode` (0x053C) = 1 within 5 s
**Note:** DOWNLINK mode is the deliberate act that makes a bulk dump a planned pass
activity rather than something the spacecraft drifts into. It does not by itself change
the rate.
**GO/NO-GO:** DOWNLINK mode confirmed --- proceed.

### Step 4 --- Command the Tier Change
**TC:** `TTC_SET_DATA_RATE(rate=1)` (Service 8, Subtype 1, func_id 65) --- select HIGH
**TC:** `TTC_SET_DATA_RATE(rate=0)` (Service 8, Subtype 1, func_id 65) --- select LOW
**Note:** Send ONE of the two above. The legacy encoding is preserved verbatim:
rate=0 has always meant "the low rate" and rate=1 "the high rate". They now land on the
LOW and HIGH rungs of the ladder — usable operational rates — rather than on the two
rates this mission flew before the ladder was introduced.
**Verify:** `ttc.tm_data_rate` (0x0506) reflects the new rate within 10 s:
- HIGH: 128000 bps
- LOW: 16000 bps
**Verify:** `ttc.tm_tier` (0x0539) = `ttc.tm_tier_cmd` (0x053A) --- active equals
commanded, i.e. no gate demoted the link
**Verify:** `ttc.tm_symbol_rate` (0x053B) = 64000 Bd at HIGH, 16000 Bd at LOW
**Verify:** `ttc.carrier_lock` (0x0510) = 1 (maintained through transition)
**Verify:** `ttc.bit_sync` (0x0511) = 1 (re-acquired within 5 s)
**Verify:** `ttc.frame_sync` (0x0512) = 1 (re-acquired within 10 s)
**Note:** If the command is rejected with error 0x0007, the S1.8 failure text names the
open gate. Do not retry blindly.
**GO/NO-GO:** Tier changed, active = commanded, synchronization confirmed --- proceed
to the BER check.

### Step 5 --- Verify the New BER Is Acceptable
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- request TT&C housekeeping after
allowing 30 s for BER to stabilize
**Verify:** `ttc.ber` (0x050C) < -5 (BER better than 1e-5)
**Verify:** `ttc.link_margin` (0x0503) > 1.0 dB at the new tier
**Verify:** `ttc.eb_n0` (0x0519) > 8.0 dB
**Action:** If BER >= -5, immediately revert to LOW (see Contingency).
**GO/NO-GO:** BER acceptable at the new tier --- rate change complete.

### Step 6 --- Release HIGH After the Dump (HIGH only)
**TC:** `TTC_SET_DATA_RATE(rate=0)` (Service 8, Subtype 1, func_id 65) --- back to LOW
**TC:** `TTC_SET_DOWNLINK_MODE(on=0)` (Service 8, Subtype 1, func_id 79)
**Verify:** `ttc.tm_data_rate` (0x0506) = 16000 bps within 10 s
**Verify:** `ttc.downlink_mode` (0x053C) = 0
**Note:** HIGH is released as deliberately as it was taken. Leaving DOWNLINK mode does
not clear the commanded tier; the ACTIVE tier simply falls back to LOW, which is where
routine operations belong.

## Verification Criteria
- [ ] `ttc.tm_data_rate` (0x0506) matches the commanded tier's rate
- [ ] `ttc.tm_tier` (0x0539) = `ttc.tm_tier_cmd` (0x053A)
- [ ] `ttc.tm_symbol_rate` (0x053B) matches the tier's modulation
- [ ] `ttc.ber` (0x050C) < -5 at the new tier
- [ ] `ttc.carrier_lock` (0x0510) = 1 maintained throughout
- [ ] `ttc.link_margin` (0x0503) > 1.0 dB at the new tier
- [ ] No frame sync losses detected during or after transition
- [ ] After a dump: back at LOW with `ttc.downlink_mode` (0x053C) = 0

## Contingency
- If the tier command is rejected (S1.8, error 0x0007): read the failure text. It names
  the gate — antenna stowed, DOWNLINK mode off, or elevation below 10 deg. Fix the gate
  or accept LOW for this pass.
- If `ttc.tm_tier` (0x0539) stays below `ttc.tm_tier_cmd` (0x053A) after the command
  was accepted: the elevation gate is demoting the ACTIVE tier as the pass descends.
  This is expected behaviour near LOS; the commanded tier is retained and HIGH resumes
  on the next pass above 10 deg. Do not re-command.
- If carrier lock is lost during the change: wait 15 s for automatic re-acquisition.
  If not recovered, command back to LOW: `TTC_SET_DATA_RATE(rate=0)`. If still no lock,
  escalate to CTG-014 (BER Anomaly Investigation).
- If BER exceeds -5 at HIGH: immediately revert to LOW using `TTC_SET_DATA_RATE(rate=0)`.
  Verify BER returns to acceptable levels. Log the anomaly and re-baseline the link
  budget before the next high-rate pass.
- If bit/frame sync is not acquired within 30 s: revert to LOW. If sync is still not
  acquired, check that the ground modem was reconfigured for the new symbol rate and
  modulation, then check transponder health — follow CTG-014.
- If contact is about to end (< 1 min remaining): do NOT attempt a rate change. Wait
  for the next contact.

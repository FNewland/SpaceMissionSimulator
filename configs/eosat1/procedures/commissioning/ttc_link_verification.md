# COM-006: TTC Link Verification
**Subsystem:** TT&C
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Verify the S-band TT&C link performance using both primary and redundant transponders.
Measure received signal strength (RSSI), link margin at various elevation angles, and
confirm reliable command/telemetry exchange through both ground stations (Iqaluit and
Troll). Validate link budget predictions against measured values.

## Link Margin Baselines (re-measured 2026-07-29)

This card is where the mission's link-budget baselines are *established*, so the numbers
it verifies against must be the ones the flight model actually produces. They were
re-measured with `python tools/link_budget_ladder.py`, which drives the same
`TTCBasicModel.link_margin_for` the simulator flies, against the shipped
`subsystems/ttc.yaml` (EIRP 10 dBW + 3 dBi, atm 0.5 / pol 0.3 / impl 2.0 dB, G/T
de-rated -1.5 dB below 10 deg and -0.5 dB below 30 deg, 9.6 dB uncoded requirement less
the tier's coding gain).

**Link margin (dB) by tier, station and elevation:**

| Station | G/T | BEACON @5 deg | LOW @5 deg | HIGH @5 deg | BEACON @30 deg | LOW @30 deg | HIGH @30 deg | BEACON @90 deg | LOW @90 deg | HIGH @90 deg |
|---|---|---|---|---|---|---|---|---|---|---|
| Svalbard | 21.5 | 59.3 | 47.2 | 36.7 | 70.6 | 58.5 | 48.0 | 75.8 | 63.8 | 53.3 |
| Inuvik | 22.0 | 59.8 | 47.7 | 37.2 | 71.1 | 59.0 | 48.5 | 76.3 | 64.3 | 53.8 |
| Iqaluit | 20.0 | 57.8 | 45.7 | 35.2 | 69.1 | 57.0 | 46.5 | 74.3 | 62.3 | 51.8 |
| O'Higgins | 20.0 | 57.8 | 45.7 | 35.2 | 69.1 | 57.0 | 46.5 | 74.3 | 62.3 | 51.8 |
| Troll | 18.5 | 56.3 | 44.2 | 33.7 | 67.6 | 55.5 | 45.0 | 72.8 | 60.8 | 50.3 |

**Worst case in the whole matrix: HIGH at Troll, 5 deg = 33.7 dB.** Every rung closes
with tens of dB at every station and every geometry — this link is not power-limited.
Two consequences for this card:

* The GO/NO-GO thresholds below (3 dB, 6 dB) are *sanity* thresholds. A measurement
  anywhere near them means something is wrong with the model, the attitude or the ground
  station — not that the geometry is marginal.
* The **10 deg gate on the HIGH tier is a policy gate, not a margin gate.** HIGH closes
  with 33.7 dB at 5 deg. It is withheld below 10 deg because bulk downlink is a planned,
  earned activity, and because tracking, Doppler rate and multipath at low elevation make
  a 64 kBd QPSK dump a poor bet — not because the link fails.

The rungs are separated by exactly what the budget says: BEACON to LOW is -12.04 dB
(16x rate), LOW to HIGH is -10.53 dB (8x rate = -9.03 dB, plus conv+RS 7.5 dB -> RS
6.0 dB = -1.5 dB of coding). Any measured set that does not show those two steps
indicates a modelling error, not a link problem.

## Prerequisites
- [ ] COM-001 through COM-003 completed
- [ ] OBC in NOMINAL mode
- [ ] Spacecraft attitude stable in COARSE_SUN (`aocs.mode` 0x020F = 3) or
      NOMINAL (4). There is no COARSE_SUN or NOMINAL mode — the codes are
      in `configs/eosat1/aocs/aocs_mode_codes.yaml`.
- [ ] Ground station passes scheduled for at least two stations
- [ ] Primary transponder currently active
- [ ] Bidirectional S-band link active — `ttc.link_status` (0x0501) = 2 (LOCKED)
- [ ] OBC in the APPLICATION image (`obdh.sw_image` 0x0311 = 1). Booting the
      application is what LIFTS the S-band PA inhibit the bootloader asserts;
      until then the UHF beacon is the only downlink and Step 0 below cannot
      be verified.

## Procedure Steps

### Step 0 — Configure the S-Band Chain for Measurement
**Note:** This step did not exist, and without it Step 2a could not succeed.
`TTCBasicModel._tier_refusal` gates the rate ladder on four things this card
never established: the `ttc_tx` EPS power line, the PA, a DEPLOYED antenna
(`requires_antenna` is true for both LOW and HIGH), and `beacon_mode` being
clear — while beacon mode is latched, BOTH operational tiers are refused with
S1.8 error `0x0007`, "TM rate is forced to the beacon tier in beacon mode".
Beacon mode survives an application boot, so a spacecraft can arrive at this
card still latched.
**TC:** `EPS_POWER_ON(line_index=2)` (Service 8, Subtype 1, func_id 19) — the `ttc_tx` line
**TC:** `TTC_PA_ON` (Service 8, Subtype 1, func_id 66) — S-band power amplifier
**TC:** `TTC_SET_BEACON_MODE(on=0)` (Service 8, Subtype 1, func_id 70) — release the forced beacon tier
**TC:** `TTC_DEPLOY_ANTENNA` (Service 8, Subtype 1, func_id 69) — S-band antenna deployment
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — TT&C housekeeping
**Verify:** `eps.pl_ttc_tx == 1` (0x0112, timeout 10 s) — transmitter line energised
**Verify:** `ttc.pa_on == 1` (0x0516, timeout 10 s) — PA on
**Verify:** `ttc.beacon_mode == 0` (0x0521, timeout 10 s) — no forced tier
**Verify:** `ttc.antenna_deployed == 1` (0x0520, timeout 30 s) — antenna deployed
**Verify:** `ttc.tx_active == 1` (0x0525, timeout 10 s) — the S-band prime chain is
actually radiating. This is the gate parameter added in Phase 3; if it is 0,
`ttc.tx_gate_status` (0x052B) says which of the four permissions is missing
(bit0 PA, bit1 `ttc_tx` line, bit2 station in view, bit6 antenna).
**Note:** The UHF beacon transmitter is a SEPARATE radio on the non-switchable
`ttc_rx` line and is untouched by this card. `ttc.beacon_active` (0x0527) stays
1 throughout, which is why HK SID 11 keeps arriving even if the S-band chain is
mis-configured. Do not confuse `ttc.beacon_mode` (0x0521, the forced TM tier,
cleared above) with `ttc.beacon_tx_on` (0x0526, the UHF transmitter).
**GO/NO-GO:** S-band chain configured, antenna deployed, no forced tier

### Step 1 — Baseline Primary Transponder Performance
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED) within 10s
**Verify:** `ttc.rssi` (0x0502) > -100 dBm within 10s
**Verify:** `ttc.link_margin` (0x0503) > 6.0 dB within 10s
**Action:** Record values at current elevation angle. Note ground station and antenna configuration.
**GO/NO-GO:** Primary transponder link nominal

### Step 2 — Measure RSSI vs Elevation Profile
**Action:** During the ground station pass, request TT&C HK at low (10 deg), medium (30 deg), and high (60+ deg) elevation angles to characterize link performance.
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — TT&C housekeeping at ~10 deg elevation
**Verify:** `ttc.rssi` (0x0502) > -108 dBm (low elevation) within 10s
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB (minimum at low elevation) within 10s
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — TT&C housekeeping at ~30 deg elevation
**Verify:** `ttc.rssi` (0x0502) > -100 dBm (medium elevation) within 10s
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — TT&C housekeeping at ~60 deg elevation
**Verify:** `ttc.rssi` (0x0502) > -95 dBm (high elevation) within 10s
**GO/NO-GO:** RSSI profile consistent with link budget prediction (+/- 3 dB)

### Step 2a — Baseline Each Rung of the TM Rate Ladder
**Action:** At a stable elevation above 30 deg, step the ladder and record the margin at
each rung. This is the measurement that establishes the mission baselines in the table
above.
**TC:** `TTC_SET_DATA_RATE(rate=0)` (Service 8, Subtype 1, func_id 65) — LOW, 16 kbps
**Verify:** `ttc.tm_data_rate` (0x0506) = 16000 bps and `ttc.tm_tier` (0x0539) = 1
**Verify:** `ttc.link_margin` (0x0503) — record; expect 55-59 dB at 30 deg
**TC:** `TTC_SET_DOWNLINK_MODE(on=1)` (Service 8, Subtype 1, func_id 79)
**Verify:** `ttc.downlink_mode` (0x053C) = 1
**TC:** `TTC_SET_DATA_RATE(rate=1)` (Service 8, Subtype 1, func_id 65) — HIGH, 128 kbps
**Verify:** `ttc.tm_data_rate` (0x0506) = 128000 bps and `ttc.tm_tier` (0x0539) = 2
**Verify:** `ttc.tm_symbol_rate` (0x053B) = 64000 Bd (the QPSK step is visible on the
ground station spectrum analyser: same information rate, half the occupied bandwidth)
**Verify:** `ttc.link_margin` (0x0503) — record; expect 45-48 dB at 30 deg
**Action:** Compute the step: margin(LOW) - margin(HIGH). It must be 10.5 +/- 0.5 dB.
**TC:** `TTC_SET_DATA_RATE(rate=0)` (Service 8, Subtype 1, func_id 65) — drop back to LOW
**TC:** `TTC_SET_DOWNLINK_MODE(on=0)` (Service 8, Subtype 1, func_id 79) — release DOWNLINK mode
**Verify:** `ttc.tm_data_rate == 16000` (0x0506, timeout 10 s)
**Verify:** `ttc.downlink_mode == 0` (0x053C, timeout 10 s)
**Note:** The release used to be prose inside the previous line's `**TC:**`
annotation, so the extractor produced one command where two are needed — while
this card's Post-Conditions demand `downlink_mode` = 0. A card that leaves
DOWNLINK mode latched hands the next shift a spacecraft that will accept a HIGH
tier it did not ask for.
**Note:** The BEACON rung cannot be measured here — it is not selectable by func 65 in
nominal software. Its baseline comes from the LEOP first-acquisition pass (LEOP-001).
**GO/NO-GO:** Both operational rungs measured, and the step between them is 10.5 +/- 0.5 dB

### Step 2b — Confirm the HIGH-Tier Elevation Gate
**Action:** As the pass descends, wait until `ttc.contact_elevation` (0x050A) is below
10 deg, then attempt to raise the tier.
**TC:** `TTC_SET_DATA_RATE(rate=1)` (Service 8, Subtype 1, func_id 65)
**Verify:** The command is REJECTED with an S1.8 execution failure, error 0x0007, whose
text names the elevation gate. `ttc.tm_data_rate` (0x0506) remains 16000 bps.
**Note:** This is the intended behaviour and is not an anomaly. A rejected tier is how
the spacecraft tells the ground that a rule was not satisfied; a silent clamp would hide
it. Record the rejection in the Link Budget Report as evidence the gate works.
**GO/NO-GO:** The gate rejects HIGH below 10 deg and the link stays at LOW

### Step 3 — Switch to Redundant Transponder
**TC:** `TTC_SWITCH_REDUNDANT` (Service 8, Subtype 1, func_id 64)
**Note:** Annotated `Service 11, Subtype 2` until 2026-08b. Service 11 is
time-tagged scheduling; the transponder select is S8 function-perform func 64.
**Action:** Wait for redundant transponder to lock. Ground station may need brief reacquisition.
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED) within 60s
**Verify:** `ttc.rssi` (0x0502) > -100 dBm within 60s
**GO/NO-GO:** Redundant transponder operational and locked

### Step 4 — Redundant Transponder Performance Check
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `ttc.link_margin` (0x0503) > 6.0 dB within 10s
**Action:** Compare redundant transponder RSSI and link margin with primary at similar elevation. Values should be within 2 dB of primary.
**Verify:** |RSSI_redundant - RSSI_primary| < 2.0 dB
**GO/NO-GO:** Redundant transponder performance comparable to primary

### Step 5 — Restore Primary Transponder
**TC:** `TTC_SWITCH_PRIMARY` (Service 8, Subtype 1, func_id 63)
**Note:** Annotated `Service 11, Subtype 1` until 2026-08b — see Step 3.
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED) within 60s
**Verify:** `ttc.rssi` (0x0502) > -100 dBm within 10s
**GO/NO-GO:** Primary transponder restored and locked

### Step 6 — Command Throughput Test
**TC:** `GET_PARAM(param_id=0x0105)` (Service 20, Subtype 3) — Send a burst of commands in sequence to verify TC processing rate and TM response time.
**TC:** `GET_PARAM(param_id=0x0105)` through `GET_PARAM(param_id=0x010A)` (Service 20, Subtype 3) — 6 rapid requests
**Verify:** All 6 responses received within 30s
**TC:** `GET_PARAM(param_id=0x0306)` (Service 20, Subtype 3) — `obdh.tc_rej_count`
**Verify:** `obdh.tc_rej_count` (0x0306) unchanged across the burst. (This read
0x0311, which is `obdh.sw_image` — the running image, not a rejection counter.)
**GO/NO-GO:** Command throughput nominal

### Step 7 — Multi-Station Verification (deferred passes)
**Action:** Schedule identical link checks (Steps 1-2) during passes over remaining ground stations. Record RSSI and link margin from each.
- Iqaluit (63.7N): primary northern polar station — expect 2-4 passes/day
- Troll (72.0S): southern polar coverage — expect 2-4 passes/day
**Verify:** Link margin > 3 dB at 10 deg elevation from each station
**GO/NO-GO:** All ground stations verified (may span multiple orbits)

### Step 8 — Compile Link Budget Report
**Action:** Tabulate measured RSSI and link margin versus elevation **and versus rate
tier** for each station. Compare against the baseline table at the head of this card
(regenerate it with `python tools/link_budget_ladder.py` if the RF configuration has
changed). Document any discrepancies. Establish the minimum operational elevation angle
for each station, and confirm that the 10 deg HIGH-tier gate is enforced at each.
**GO/NO-GO:** Link budget verification complete

### Step 9 — Safe the Link
**Note:** This step did not exist. The card left the spacecraft in whatever
state Step 8's analysis found it in, while its own Post-Conditions asserted
LOW and `downlink_mode` = 0. Configuration a card asserts but never commands is
configuration the next shift inherits by luck.
**TC:** `TTC_SET_DATA_RATE(rate=0)` (Service 8, Subtype 1, func_id 65) — LOW is the routine tier
**TC:** `TTC_SET_DOWNLINK_MODE(on=0)` (Service 8, Subtype 1, func_id 79) — DOWNLINK mode released
**TC:** `TTC_SWITCH_PRIMARY` (Service 8, Subtype 1, func_id 63) — primary transponder for nominal ops
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — TT&C housekeeping
**Verify:** `ttc.tm_tier == 1` (0x0539, timeout 10 s) — ACTIVE tier is LOW
**Verify:** `ttc.tm_tier_cmd == 1` (0x053A, timeout 10 s) — and the COMMANDED tier agrees
**Verify:** `ttc.downlink_mode == 0` (0x053C, timeout 10 s)
**Verify:** `ttc.beacon_mode == 0` (0x0521, timeout 10 s) — not re-latched
**Verify:** `ttc.antenna_deployed == 1` (0x0520, timeout 10 s) — deployment is one-way and stays
**Verify:** `ttc.pa_on == 1` (0x0516, timeout 10 s) — the PA stays ON for nominal operations
**Note:** The antenna is NOT re-stowed and the PA is NOT switched off. Both are
deliberate: deployment is irreversible burn-wire, and a commissioning card that
ends by silencing the transmitter would hand the next pass a spacecraft on
beacon only.
**GO/NO-GO:** Link left at LOW on the primary transponder, DOWNLINK mode released

## Off-Nominal Handling
- If redundant transponder fails to lock: Allow up to 120s. If no lock, switch back to primary via `TTC_SWITCH_PRIMARY`. Log redundant transponder as anomalous. Continue commissioning on primary — schedule transponder investigation.
- If link margin < 3 dB at any elevation > 10 deg: Check spacecraft attitude — mispointing reduces antenna gain. Verify ground station antenna tracking. If margin consistently low, investigate on-board antenna pattern.
- If RSSI differs from prediction by > 5 dB: Check for interference at ground station. Verify transponder output power via `GET_PARAM(param_id=0x050D)` — `ttc.tx_fwd_power`
  in dBm — and the live EIRP at `ttc.eirp_dbw` (0x052C). (This card read 0x0510,
  which is `ttc.carrier_lock`, a 0/1 flag: an operator checking for "low output
  power" was reading a boolean.) If the forward power is 0, check `ttc.tx_active`
  (0x0525) and `ttc.tx_gate_status` (0x052B) before suspecting the hardware.
- If TC rejected during throughput test: Reduce command rate. Check for authentication or sequence counter errors. Verify TC packet formatting.
- If one ground station cannot achieve lock: Verify station frequency configuration. Check TLE accuracy for pass prediction. Attempt next pass. If persistent, investigate station-specific issues.

## Post-Conditions
- [ ] Primary transponder verified — RSSI and link margin nominal
- [ ] Redundant transponder verified — performance comparable to primary
- [ ] RSSI vs elevation profile characterized
- [ ] Link margin measured at LOW and HIGH, and the 10.5 dB rung step confirmed
- [ ] HIGH-tier elevation gate verified by rejection below 10 deg
- [ ] Spacecraft left at LOW with `ttc.downlink_mode` (0x053C) = 0 — COMMANDED by
      Step 9, not merely assumed
- [ ] `ttc.antenna_deployed` (0x0520) = 1 and `ttc.beacon_mode` (0x0521) = 0
- [ ] `ttc.tx_active` (0x0525) = 1 during contact, `ttc.tx_gate_status` (0x052B) = 0xFF
- [ ] Command throughput nominal
- [ ] Multi-station verification completed (or scheduled)
- [ ] Link Budget Report generated
- [ ] Primary transponder active for nominal operations
- [ ] GO decision for COM-005 (AOCS Sensor Calibration)

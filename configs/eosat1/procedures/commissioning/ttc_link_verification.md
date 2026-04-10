# COM-004: TT&C Link Budget Verification
**Subsystem:** TT&C
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Verify the VHF/UHF TT&C link performance using both primary and redundant transponders.
Measure received signal strength (RSSI), link margin at various elevation angles, and
confirm reliable command/telemetry exchange through all four ground stations (Svalbard,
Troll, Inuvik, O'Higgins). Validate link budget predictions against measured values.

## Prerequisites
- [ ] COM-001 through COM-003 completed
- [ ] OBC in NOMINAL mode
- [ ] Spacecraft in SAFE_POINT mode with stable attitude
- [ ] Ground station passes scheduled for at least two stations
- [ ] Primary transponder currently active
- [ ] Bidirectional VHF/UHF link active

## Procedure Steps

### Step 1 — Baseline Primary Transponder Performance
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Verify:** `ttc.link_status` (0x0501) = 1 (LOCKED) within 10s
**Verify:** `ttc.rssi` (0x0502) > -100 dBm within 10s
**Verify:** `ttc.link_margin` (0x0503) > 6.0 dB within 10s
**Action:** Record values at current elevation angle. Note ground station and antenna configuration.
**GO/NO-GO:** Primary transponder link nominal

### Step 2 — Measure RSSI vs Elevation Profile
**Action:** During the ground station pass, request TT&C HK at low (10 deg), medium (30 deg), and high (60+ deg) elevation angles to characterize link performance.
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) — at ~10 deg elevation
**Verify:** `ttc.rssi` (0x0502) > -108 dBm (low elevation) within 10s
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB (minimum at low elevation) within 10s
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) — at ~30 deg elevation
**Verify:** `ttc.rssi` (0x0502) > -100 dBm (medium elevation) within 10s
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) — at ~60 deg elevation
**Verify:** `ttc.rssi` (0x0502) > -95 dBm (high elevation) within 10s
**GO/NO-GO:** RSSI profile consistent with link budget prediction (+/- 3 dB)

### Step 3 — Switch to Redundant Transponder
**TC:** `TTC_SWITCH_REDUNDANT` (Service 11, Subtype 2)
**Action:** Wait for redundant transponder to lock. Ground station may need brief reacquisition.
**Verify:** `ttc.link_status` (0x0501) = 1 (LOCKED) within 60s
**Verify:** `ttc.rssi` (0x0502) > -100 dBm within 60s
**GO/NO-GO:** Redundant transponder operational and locked

### Step 4 — Redundant Transponder Performance Check
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Verify:** `ttc.link_margin` (0x0503) > 6.0 dB within 10s
**Action:** Compare redundant transponder RSSI and link margin with primary at similar elevation. Values should be within 2 dB of primary.
**Verify:** |RSSI_redundant - RSSI_primary| < 2.0 dB
**GO/NO-GO:** Redundant transponder performance comparable to primary

### Step 5 — Restore Primary Transponder
**TC:** `TTC_SWITCH_PRIMARY` (Service 11, Subtype 1)
**Verify:** `ttc.link_status` (0x0501) = 1 (LOCKED) within 60s
**Verify:** `ttc.rssi` (0x0502) > -100 dBm within 10s
**GO/NO-GO:** Primary transponder restored and locked

### Step 6 — Command Throughput Test
**Action:** Send a burst of 10 `GET_PARAM` commands in sequence to verify TC processing rate and TM response time.
**TC:** `GET_PARAM(0x0105)` through `GET_PARAM(0x010A)` (Service 20, Subtype 1) — 6 rapid requests
**Verify:** All 6 responses received within 30s
**Verify:** No TC rejection reported (check `GET_PARAM(0x0311)` — TC rejected counter unchanged)
**GO/NO-GO:** Command throughput nominal

### Step 7 — Multi-Station Verification (deferred passes)
**Action:** Schedule identical link checks (Steps 1-2) during passes over remaining ground stations. Record RSSI and link margin from each.
- Svalbard (78.2N): primary polar station — expect 2-4 passes/day
- Troll (72.0S): southern polar coverage — expect 2-4 passes/day
- Inuvik (68.4N): northern coverage backup — expect 2-3 passes/day
- O'Higgins (63.3S): southern backup — expect 2-3 passes/day
**Verify:** Link margin > 3 dB at 10 deg elevation from each station
**GO/NO-GO:** All ground stations verified (may span multiple orbits)

### Step 8 — Compile Link Budget Report
**Action:** Tabulate measured RSSI and link margin versus elevation for each station. Compare with predicted link budget. Document any discrepancies. Establish minimum operational elevation angle for each station.
**GO/NO-GO:** Link budget verification complete

## Off-Nominal Handling
- If redundant transponder fails to lock: Allow up to 120s. If no lock, switch back to primary via `TTC_SWITCH_PRIMARY`. Log redundant transponder as anomalous. Continue commissioning on primary — schedule transponder investigation.
- If link margin < 3 dB at any elevation > 10 deg: Check spacecraft attitude — mispointing reduces antenna gain. Verify ground station antenna tracking. If margin consistently low, investigate on-board antenna pattern.
- If RSSI differs from prediction by > 5 dB: Check for interference at ground station. Verify transponder output power via `GET_PARAM(0x0510)`. If output power low, may indicate transponder degradation.
- If TC rejected during throughput test: Reduce command rate. Check for authentication or sequence counter errors. Verify TC packet formatting.
- If one ground station cannot achieve lock: Verify station frequency configuration. Check TLE accuracy for pass prediction. Attempt next pass. If persistent, investigate station-specific issues.

## Post-Conditions
- [ ] Primary transponder verified — RSSI and link margin nominal
- [ ] Redundant transponder verified — performance comparable to primary
- [ ] RSSI vs elevation profile characterized
- [ ] Command throughput nominal
- [ ] Multi-station verification completed (or scheduled)
- [ ] Link Budget Report generated
- [ ] Primary transponder active for nominal operations
- [ ] GO decision for COM-005 (AOCS Sensor Calibration)

# CTG-027: GPS Time Synchronisation and AOCS Recovery

**Category:** Contingency
**Position Lead:** OBDH
**Cross-Position:** Flight Director, AOCS
**Difficulty:** Advanced

## Objective
Recover from time synchronisation anomalies caused by loss of GPS 3D fix. The OBC_GPS_TIME_SYNC
function (func_id 80) requires a valid 3D GPS fix to synchronise the onboard time source. If a
time jump exceeds 5 seconds, the AOCS system automatically transitions to SAFE_BOOT mode as a
protective measure. This procedure verifies GPS lock status, executes the time synchronisation
command, and if necessary, re-commissions the AOCS through the required state transitions.

## Prerequisites
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 2 (LOCKED)
- [ ] GPS receiver has been operating for >= 5 minutes to acquire fix
- [ ] Flight Director notified of time synchronisation event
- [ ] AOCS subsystem responding to commands

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| aocs.gps_fix | 0x0274 | 3 (3D fix + velocity) — required for time sync |
| aocs.gps_num_sats | 0x0276 | >= 5 satellites tracked |
| aocs.gps_pdop | 0x0275 | < 5.0 (position dilution of precision) |
| obdh.obc_time | 0x0309 | Current onboard CUC time (seconds since epoch) |
| aocs.mode | 0x020F | 0 = OFF, 1 = SAFE_BOOT, 2 = DETUMBLE, 3 = COARSE_SUN, 4 = NOMINAL, 5 = FINE_POINT, 6 = SLEW, 7 = DESAT, 8 = ECLIPSE, 9 = INERTIAL |
| aocs.att_error | 0x0217 | Pointing error for the current mode |

**Note:** The GPS parameters belong to the AOCS subsystem (HK SID 2). There is no
`gps.*` namespace, no `obdh.time_src` and no `aocs.state` in
`telemetry/parameters.yaml`; 0x0605/0x0606/0x0609/0x060A are payload parameters,
0x0303 is `obdh.mmm_used` and 0x0218 is `aocs.rw1_temp`.

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| OBC_GPS_TIME_SYNC | 8 | 1 | 80 | Synchronise OBC time to GPS UTC |
| AOCS_SET_MODE | 8 | 1 | 0 | Command AOCS to specified mode |

## Procedure Steps

### Step 1: Verify GPS 3D Fix Status
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — Request AOCS housekeeping (the GPS parameters live in the AOCS subsystem, SID 2; 0x0605-0x060A are payload parameters)
**Verify:** `aocs.gps_fix` (0x0274) = 3 (3D fix + velocity)
**Verify:** `aocs.gps_num_sats` (0x0276) >= 5 satellites tracked
**Verify:** `aocs.gps_pdop` (0x0275) < 5.0 (acceptable position dilution of precision)
**Note:** There is no GPS-UTC telemetry parameter in `telemetry/parameters.yaml`; the onboard clock is read from `obdh.obc_time` (0x0309) and the GPS-derived correction is applied by `OBC_GPS_TIME_SYNC` (S8 func 80).
**Action:** If fix_type < 3:
- Wait 1-2 minutes and request HK again (GPS may be re-acquiring lock)
- If 3D fix not achieved after 2 retries, escalate to engineering (GPS receiver malfunction)
**GO/NO-GO:** 3D GPS fix confirmed with valid UTC time — proceed to Step 2.

### Step 2: Record Current AOCS Mode
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — Request AOCS housekeeping
**Verify:** Record the current `aocs.mode` (0x020F) value. Codes per `aocs/aocs_mode_codes.yaml`:
- 0 = OFF
- 1 = SAFE_BOOT
- 2 = DETUMBLE
- 3 = COARSE_SUN
- 4 = NOMINAL
- 5 = FINE_POINT
- 6 = SLEW
- 7 = DESAT
- 8 = ECLIPSE
- 9 = INERTIAL
**Note:** If the AOCS is already in SAFE_BOOT, the mode value is recorded and will be used
to determine re-commissioning sequence in Step 5.
**Verify:** Record `aocs.att_error` (0x0217) for reference in post-event assessment.
(0x0218 is `aocs.rw1_temp`, not an AOCS state word — there is no `aocs.state` parameter.)
**Action:** Also note current `obdh.obc_time` (0x0309) and compare with the ground-stamped
UTC to estimate time jump magnitude.
**GO/NO-GO:** Current AOCS mode recorded — proceed to Step 3.

### Step 3: Send OBC GPS Time Synchronisation Command
**TC:** `OBC_GPS_TIME_SYNC` (Service 8, Subtype 1, func_id 80) — Transmit the GPS time sync command
**Note:** This command reads the GPS UTC time and updates the onboard RTC. If the time jump
exceeds 5 seconds, the AOCS will automatically trigger a protective transition to SAFE_BOOT.
**Verify:** Wait 5 seconds for command execution and telemetry update.
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) — Request OBDH housekeeping
**Verify:** `obdh.obc_time` (0x0309) steps to the GPS-derived value — this is the evidence the sync occurred. (There is no `obdh.time_src` parameter; 0x0303 is `obdh.mmm_used`, mass-memory usage.)
**Verify:** Compare `obdh.obc_time` (0x0309) to expected GPS time — should now match GPS UTC
**Verify:** Request AOCS housekeeping: `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Check:** If `aocs.mode` (0x020F) = 1 (SAFE_BOOT):
- Time jump exceeded 5 seconds threshold
- AOCS autonomously entered SAFE_BOOT protection mode
- Proceed to Step 5 (AOCS re-commissioning)
**Check:** If `aocs.mode` (0x020F) has not changed:
- Time jump was < 5 seconds or AOCS did not trigger protection
- Proceed to Step 4 (verification only)
**GO/NO-GO:** OBC time synchronized to GPS and AOCS mode status known — proceed to Step 4 or 5.

### Step 4: Verify Continued AOCS Operation (if not in SAFE_BOOT)
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — Request full AOCS housekeeping
**Verify:** `aocs.mode` (0x020F) remains at pre-sync value (not SAFE_BOOT)
**Verify:** `aocs.att_error` (0x0217) < threshold for current mode:
- NOMINAL: < 5.0 degrees
- FINE_POINT: < 1.0 degree
**Verify:** Attitude rates within nominal envelope
**Action:** Monitor AOCS for 2-3 orbits to confirm stable operation post-sync
**GO/NO-GO:** AOCS operating nominally — time sync recovery complete. Exit procedure.

### Step 5: AOCS Re-Commissioning (if entered SAFE_BOOT)
**Objective:** Transition AOCS from SAFE_BOOT through required commissioning phases to restore
full attitude control capability. Follow the commissioning state machine defined in LEOP-004
(AOCS Commissioning Walkthrough), phases 9 and 11.

**Phase 9 — DETUMBLE Mode:**
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 1, func_id 0) — DETUMBLE
**Rationale:** DETUMBLE uses magnetorquers and sun sensor to stabilize spacecraft rates.
Reference: LEOP-004, Phase 9 — Initial Rate Damping.
**Verify:** `aocs.mode` (0x020F) = 2 (DETUMBLE) within 10 seconds
**Verify:** Angular rates begin decreasing: `aocs.rate_x`, `aocs.rate_y`, `aocs.rate_z` trending toward zero
**Monitor:** Duration: ~3-5 orbits in DETUMBLE (rates must decay to < 1 deg/s)
**Verify:** Attitude error: `aocs.att_error` (0x0217) < 30 degrees
**Wait:** Until rates < 0.5 deg/s and attitude is coarse sun-pointed.
**GO/NO-GO:** Rates damped and sun acquisition stable — proceed to Phase 11.

**Phase 11 — Transition to NOMINAL and FINE_POINT:**
**Note:** Once DETUMBLE is stable, transition through COARSE_SUN → NOMINAL → FINE_POINT:
**TC:** `AOCS_SET_MODE(mode=3)` (Service 8, Subtype 1, func_id 0) — COARSE_SUN — wait 30 seconds, verify `aocs.mode` = 3
**TC:** `AOCS_SET_MODE(mode=4)` (Service 8, Subtype 1, func_id 0) — NOMINAL — wait 30 seconds, verify `aocs.mode` = 4
**TC:** `AOCS_SET_MODE(mode=5)` (Service 8, Subtype 1, func_id 0) — FINE_POINT — wait 30 seconds, verify `aocs.mode` = 5
**Rationale:** Each transition allows attitude loop gain to re-establish at the new level.
Reference: LEOP-004, Phase 11 — Fine Attitude Lock and Payload Commissioning.
**Verify:** After each transition, confirm `aocs.mode` equals the commanded value
**Verify:** `aocs.att_error` (0x0217) reduces with each phase:
- COARSE_SUN target: < 10 degrees
- NOMINAL target: < 5 degrees
- FINE_POINT target: < 1 degree
**Monitor:** `aocs.att_error` (0x0217) for convergence
**GO/NO-GO:** AOCS in FINE_POINT with `aocs.att_error` < 1 degree — re-commissioning complete.

### Step 6: Post-Recovery Verification
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) — Platform/OBDH housekeeping
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — AOCS housekeeping (GPS lives here)
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — TT&C housekeeping
**Verify:** `obdh.obc_time` (0x0309) = current GPS UTC within 1 s
**Verify:** `aocs.mode` (0x020F) = 5 (FINE_POINT) or pre-event mode value
**Verify:** `aocs.att_error` (0x0217) < mode-specific threshold
**Verify:** `aocs.gps_fix` (0x0274) = 3 (3D fix maintained)
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED) — communication remained stable
**Action:** Log the event with:
- Time jump magnitude (OBC time before sync vs GPS time)
- Whether AOCS entered SAFE_BOOT
- Duration of DETUMBLE phase (if applicable)
- Time to achieve FINE_POINT lock post-recovery
**GO/NO-GO:** All systems nominal, time synchronized, AOCS commissioned — recovery complete.

## Verification Criteria
- [ ] GPS 3D fix confirmed (`aocs.gps_fix` 0x0274 = 3)
- [ ] `obdh.obc_time` (0x0309) synchronised to GPS UTC within 1 s
- [ ] AOCS mode either stable in pre-event mode OR successfully re-commissioned to FINE_POINT
- [ ] `aocs.att_error` (0x0217) within acceptable limits for final mode
- [ ] TT&C link remains active throughout procedure
- [ ] No secondary anomalies triggered (watchdog resets, safe mode entries, etc.)

## Contingency
- If GPS 3D fix cannot be acquired after 5 minutes: GPS receiver may have intermittent lock.
  Request engineering analysis of GPS signal quality and ionospheric conditions. Retry after
  1 hour if conditions may have improved.
- If OBC time sync command fails (returns error): Verify GPS receiver is responding and HK
  can be decoded. If receiver failure suspected, escalate to engineering.
- If AOCS remains in SAFE_BOOT after Step 5 Phase 9 (rates not damping): The magnetorquer
  or sun sensor may be inoperative. Request engineering to assess AOCS hardware status.
  Consider contingency attitude control procedures if hardware failure confirmed.
- If AOCS mode transitions in Step 5 fail to execute: AOCS subsystem may have secondary
  fault. Request complete AOCS diagnostics before attempting further re-commissioning.
- If time jump occurred but AOCS did not enter SAFE_BOOT: Verify that the 5-second threshold
  has not been redefined. Confirm AOCS autonomy logic version matches flight software expected
  behaviour.

## References
- LEOP-004: AOCS Commissioning Walkthrough, Phase 9 (Initial Rate Damping) and Phase 11 (Fine Attitude Lock)
- Data dictionary for GPS receiver, OBDH, and AOCS subsystems
- EMG-005: AOCS Safe Mode Recovery (if AOCS re-commissioning fails)

# PROC-CON-011: GPS Time Synchronisation and AOCS Recovery

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
| gps.fix_type | 0x0605 | 3 (3D fix) — required for time sync |
| gps.num_sats | 0x0606 | >= 5 satellites tracked |
| gps.hdop | 0x060A | < 5.0 (horizontal dilution of precision) |
| gps.utc_time | 0x0609 | Valid UTC timestamp |
| obdh.obc_time | 0x0302 | Current onboard time |
| obdh.time_src | 0x0303 | 0 = internal RTC, 1 = GPS synced |
| aocs.mode | 0x020F | 0 = DETUMBLE, 1 = COARSE_SUN, 2 = NOMINAL, 3 = FINE_POINT, 4 = SAFE_BOOT |
| aocs.state | 0x0218 | State within current mode |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| OBC_GPS_TIME_SYNC | 8 | 1 | 80 | Synchronise OBC time to GPS UTC |
| AOCS_SET_MODE | 8 | 4 | — | Command AOCS to specified mode |

## Procedure Steps

### Step 1: Verify GPS 3D Fix Status
**Action:** Request GPS housekeeping: `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `gps.fix_type` (0x0605) = 3 (3D fix)
**Verify:** `gps.num_sats` (0x0606) >= 5 satellites tracked
**Verify:** `gps.hdop` (0x060A) < 5.0 (acceptable horizontal precision)
**Verify:** `gps.utc_time` (0x0609) is valid and incrementing
**Action:** If fix_type < 3:
- Wait 1-2 minutes and request HK again (GPS may be re-acquiring lock)
- If 3D fix not achieved after 2 retries, escalate to engineering (GPS receiver malfunction)
**GO/NO-GO:** 3D GPS fix confirmed with valid UTC time — proceed to Step 2.

### Step 2: Record Current AOCS Mode
**Action:** Request AOCS housekeeping: `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Verify:** Record the current `aocs.mode` (0x020F) value:
- 0 = DETUMBLE
- 1 = COARSE_SUN
- 2 = NOMINAL
- 3 = FINE_POINT
- 4 = SAFE_BOOT
**Note:** If the AOCS is already in SAFE_BOOT, the mode value is recorded and will be used
to determine re-commissioning sequence in Step 5.
**Verify:** Record `aocs.state` (0x0218) for reference in post-event assessment.
**Action:** Also note current `obdh.obc_time` (0x0302) and compare with `gps.utc_time` (0x0609)
to estimate time jump magnitude.
**GO/NO-GO:** Current AOCS mode recorded — proceed to Step 3.

### Step 3: Send OBC GPS Time Synchronisation Command
**Action:** Transmit the GPS time sync command: `OBC_GPS_TIME_SYNC` (Service 8, Subtype 1, func_id 80)
**Note:** This command reads the GPS UTC time and updates the onboard RTC. If the time jump
exceeds 5 seconds, the AOCS will automatically trigger a protective transition to SAFE_BOOT.
**Verify:** Wait 5 seconds for command execution and telemetry update.
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** Check `obdh.time_src` (0x0303) = 1 (GPS synced) — indicates time sync occurred
**Verify:** Compare `obdh.obc_time` (0x0302) to expected GPS time — should now match GPS UTC
**Verify:** Request AOCS housekeeping: `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Check:** If `aocs.mode` (0x020F) = 4 (SAFE_BOOT):
- Time jump exceeded 5 seconds threshold
- AOCS autonomously entered SAFE_BOOT protection mode
- Proceed to Step 5 (AOCS re-commissioning)
**Check:** If `aocs.mode` (0x020F) has not changed:
- Time jump was < 5 seconds or AOCS did not trigger protection
- Proceed to Step 4 (verification only)
**GO/NO-GO:** OBC time synchronized to GPS and AOCS mode status known — proceed to Step 4 or 5.

### Step 4: Verify Continued AOCS Operation (if not in SAFE_BOOT)
**Action:** Request full AOCS housekeeping: `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
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
**Action:** Send mode command: `AOCS_SET_MODE(mode=0)` (DETUMBLE)
**Rationale:** DETUMBLE uses magnetorquers and sun sensor to stabilize spacecraft rates.
Reference: LEOP-004, Phase 9 — Initial Rate Damping.
**Verify:** `aocs.mode` (0x020F) = 0 (DETUMBLE) within 10 seconds
**Verify:** Angular rates begin decreasing: `aocs.rate_x`, `aocs.rate_y`, `aocs.rate_z` trending toward zero
**Monitor:** Duration: ~3-5 orbits in DETUMBLE (rates must decay to < 1 deg/s)
**Verify:** Attitude error: `aocs.att_error` (0x0217) < 30 degrees
**Wait:** Until rates < 0.5 deg/s and attitude is coarse sun-pointed.
**GO/NO-GO:** Rates damped and sun acquisition stable — proceed to Phase 11.

**Phase 11 — Transition to NOMINAL and FINE_POINT:**
**Action:** Once DETUMBLE is stable, transition through COARSE_SUN → NOMINAL → FINE_POINT:
- `AOCS_SET_MODE(mode=1)` (COARSE_SUN) — wait 30 seconds, verify `aocs.mode` = 1
- `AOCS_SET_MODE(mode=2)` (NOMINAL) — wait 30 seconds, verify `aocs.mode` = 2
- `AOCS_SET_MODE(mode=3)` (FINE_POINT) — wait 30 seconds, verify `aocs.mode` = 3
**Rationale:** Each transition allows attitude loop gain to re-establish at the new level.
Reference: LEOP-004, Phase 11 — Fine Attitude Lock and Payload Commissioning.
**Verify:** After each transition, confirm `aocs.mode` equals the commanded value
**Verify:** `aocs.att_error` (0x0217) reduces with each phase:
- COARSE_SUN target: < 10 degrees
- NOMINAL target: < 5 degrees
- FINE_POINT target: < 1 degree
**Monitor:** `aocs.state` (0x0218) for convergence (e.g., "att_lock_achieved")
**GO/NO-GO:** AOCS in FINE_POINT with `aocs.att_error` < 1 degree — re-commissioning complete.

### Step 6: Post-Recovery Verification
**Action:** Request full housekeeping across OBC, AOCS, and GPS: `HK_REQUEST` SIDs 4, 5, 6
**Verify:** `obdh.obc_time` (0x0302) = current GPS UTC (from `gps.utc_time`, 0x0609)
**Verify:** `obdh.time_src` (0x0303) = 1 (GPS synced)
**Verify:** `aocs.mode` (0x020F) = 3 (FINE_POINT) or pre-event mode value
**Verify:** `aocs.att_error` (0x0217) < mode-specific threshold
**Verify:** `gps.fix_type` (0x0605) = 3 (3D fix maintained)
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED) — communication remained stable
**Action:** Log the event with:
- Time jump magnitude (OBC time before sync vs GPS time)
- Whether AOCS entered SAFE_BOOT
- Duration of DETUMBLE phase (if applicable)
- Time to achieve FINE_POINT lock post-recovery
**GO/NO-GO:** All systems nominal, time synchronized, AOCS commissioned — recovery complete.

## Verification Criteria
- [ ] GPS 3D fix confirmed (`gps.fix_type` 0x0605 = 3)
- [ ] `obdh.time_src` (0x0303) = 1 (OBC time synchronized to GPS)
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
- PROC-EMG-002: AOCS Safe Mode Recovery (if AOCS re-commissioning fails)

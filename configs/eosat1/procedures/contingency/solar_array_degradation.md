# CON-008: Solar Array Degradation Response
**Subsystem:** EPS
**Phase:** CONTINGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Respond to detected degradation in solar array power output, indicated by array current
falling below the expected value for the current illumination conditions. EOSAT-1 has two
deployable solar arrays (SA-A and SA-B) that together produce approximately 80W at optimal
incidence. A sustained current shortfall of more than 20% from predicted values on either
array requires investigation and potential operational adjustments to maintain a positive
power budget throughout each orbit.

## Prerequisites
- [ ] EPS telemetry (SID 1) is being received at >= 0.5 Hz
- [ ] Current orbit illumination data is available — beta angle, sunlit fraction, incidence angles
- [ ] Predicted SA-A and SA-B current values for current attitude and illumination are on console (ref: EOSAT1-PWR-BUD-001)
- [ ] AOCS is in NADIR_POINT or SAFE_POINT with known solar array orientation
- [ ] `eps.bat_soc` (0x0101) > 25% — sufficient margin for investigation period

## Procedure Steps

### Step 1 — Quantify the Degradation
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 25)
**Verify:** `eps.sa_a_current` (0x0103) — record value and compare to predicted (expected > 1.0A at optimal)
**Verify:** `eps.sa_b_current` (0x0104) — record value and compare to predicted (expected > 1.0A at optimal)
**Verify:** `eps.power_gen` (0x0107) — record total generation
**Action:** Calculate percentage shortfall: (predicted - actual) / predicted x 100 for each array
**GO/NO-GO:** If shortfall > 20% on either array during confirmed sunlit phase, proceed. If < 20%, log for trend monitoring only.

### Step 2 — Compare SA-A versus SA-B Performance
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 25) — sample 3 times at 30s intervals
**Action:** Compute SA-A to SA-B current ratio (nominal ratio is 0.9 to 1.1)
**Verify:** `eps.sa_a_current` (0x0103) and `eps.sa_b_current` (0x0104) — determine if degradation is on one or both arrays
**Action:** If only one array degraded, suspect localised failure (cell damage, wiring, deployment issue). If both arrays degraded proportionally, suspect attitude error or environmental factor.
**GO/NO-GO:** Degradation source identified (single array vs both) — proceed

### Step 3 — Verify Attitude Is Not the Cause
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 25)
**Verify:** `aocs.mode` (0x020F) = 3 (NADIR_POINT) or 2 (SAFE_POINT)
**Verify:** `aocs.att_error` (0x0217) < 2.0 deg — large attitude error could reduce array illumination
**Action:** If attitude error > 5 deg, execute CON-001 first, then reassess array performance
**GO/NO-GO:** Attitude confirmed nominal — degradation is intrinsic to array hardware or environment

### Step 4 — Recalculate Power Budget
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 25)
**Verify:** `eps.power_gen` (0x0107) — actual generation
**Verify:** `eps.power_cons` (0x0106) — current consumption
**Action:** Compute power margin = generation - consumption (must be > 0W averaged over orbit)
**Action:** Compute worst-case eclipse energy deficit using degraded generation values
**Verify:** `eps.bat_soc` (0x0101) — project minimum SoC at end of next eclipse
**GO/NO-GO:** If projected minimum SoC > 25%, continue nominal ops with monitoring. If < 25%, proceed to Step 5.

### Step 5 — Reduce Loads to Match Degraded Power Budget
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1) — reduce from IMAGING to STANDBY
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10s
**Verify:** `eps.power_cons` (0x0106) decreases by >= 12W
**Action:** If margin still insufficient, command `PAYLOAD_SET_MODE(mode=0)` for full payload shutdown
**Action:** Disable non-essential heaters: `HEATER_CONTROL(circuit=thruster, on=false)` if not in use
**Verify:** `eps.power_cons` (0x0106) — recompute margin with reduced loads
**GO/NO-GO:** Power margin is positive with at least 3W reserve — proceed

### Step 6 — Optimise Attitude for Solar Array Illumination
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 1) — SAFE_POINT for sun-optimal orientation
**Verify:** `aocs.mode` (0x020F) = 2 within 15s
**Verify:** `eps.sa_a_current` (0x0103) and `eps.sa_b_current` (0x0104) — observe improvement in current
**Verify:** `eps.power_gen` (0x0107) increases relative to NADIR_POINT values
**GO/NO-GO:** Generation improved sufficiently to maintain positive margin — proceed to monitoring

### Step 7 — Establish Long-Term Monitoring Plan
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 25) — baseline measurement
**Action:** Record SA-A current, SA-B current, power generation, and SoC as new baseline
**Action:** Schedule daily trend comparison for array current vs prediction over next 7 days
**Action:** Coordinate with mission planning to adjust imaging schedule based on degraded power budget
**Action:** If single-array failure confirmed, assess operations on single-array power (approx 40W peak)
**GO/NO-GO:** Monitoring plan established and operations plan adjusted — recovery complete

## Off-Nominal Handling
- If both arrays drop below 0.2A during sunlit phase: Suspect total array failure or severe attitude error — execute CON-002 (EPS Safe Mode Recovery) immediately
- If SoC drops below 20% during this procedure: Interrupt and execute CON-002
- If attitude error is the root cause: Execute CON-001, then re-enter this procedure at Step 1
- If degradation is progressive (> 5% per day): Accelerate mission data downlink priorities, plan reduced-power mission profile

## Post-Conditions
- [ ] Array degradation quantified (percentage shortfall per array documented)
- [ ] Power budget recalculated with degraded values and confirmed positive
- [ ] Operations plan adjusted (imaging frequency, heater usage, AOCS mode)
- [ ] Trend monitoring initiated with daily reporting to Mission Director
- [ ] Root cause hypothesis documented (cell degradation, deployment, wiring, radiation)

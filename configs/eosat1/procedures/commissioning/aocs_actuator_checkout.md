# COM-006: Reaction Wheel Commissioning
**Subsystem:** AOCS
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Commission all four reaction wheels individually. Perform controlled spin-up and
spin-down tests on each wheel, verify speed control accuracy, measure wheel
temperatures, and validate momentum management (desaturation) capability using
magnetorquers.

## Prerequisites
- [ ] COM-005 (AOCS Sensor Calibration) completed — star tracker active
- [ ] AOCS in SAFE_POINT mode (mode 2)
- [ ] Body rates < 0.05 deg/s on all axes
- [ ] All four wheels reporting telemetry (confirmed in LEOP-002)
- [ ] `eps.bat_soc` (0x0101) > 65%
- [ ] Bidirectional VHF/UHF link active
- [ ] Minimum 20 minutes of ground station pass remaining

## Procedure Steps

### Step 1 — Record Baseline Wheel Speeds
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.rw1_speed` (0x0207) reported within 10s — record value
**Verify:** `aocs.rw2_speed` (0x0208) reported within 10s — record value
**Verify:** `aocs.rw3_speed` (0x0209) reported within 10s — record value
**Verify:** `aocs.rw4_speed` (0x020A) reported within 10s — record value
**Action:** Wheels in SAFE_POINT may have non-zero speeds from attitude control. Record as baseline.
**GO/NO-GO:** All four wheels responding with valid speed readings

### Step 2 — Reaction Wheel 1 Spin-Up Test
**TC:** `SET_PARAM(0x0280, 2000)` (Service 20, Subtype 3) — command RW1 to +2000 RPM
**Verify:** `aocs.rw1_speed` (0x0207) reaches 1800-2200 RPM within 60s
**Action:** Monitor attitude disturbance — remaining wheels should compensate.
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg within 60s
**TC:** `SET_PARAM(0x0280, -2000)` (Service 20, Subtype 3) — command RW1 to -2000 RPM
**Verify:** `aocs.rw1_speed` (0x0207) reaches -2200 to -1800 RPM within 120s
**TC:** `SET_PARAM(0x0280, 0)` (Service 20, Subtype 3) — return RW1 to zero
**Verify:** `aocs.rw1_speed` (0x0207) returns to < 100 RPM within 60s
**GO/NO-GO:** RW1 speed control verified in both directions

### Step 3 — Reaction Wheel 2 Spin-Up Test
**TC:** `SET_PARAM(0x0281, 2000)` (Service 20, Subtype 3) — command RW2 to +2000 RPM
**Verify:** `aocs.rw2_speed` (0x0208) reaches 1800-2200 RPM within 60s
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg within 60s
**TC:** `SET_PARAM(0x0281, -2000)` (Service 20, Subtype 3) — command RW2 to -2000 RPM
**Verify:** `aocs.rw2_speed` (0x0208) reaches -2200 to -1800 RPM within 120s
**TC:** `SET_PARAM(0x0281, 0)` (Service 20, Subtype 3) — return RW2 to zero
**Verify:** `aocs.rw2_speed` (0x0208) returns to < 100 RPM within 60s
**GO/NO-GO:** RW2 speed control verified in both directions

### Step 4 — Reaction Wheel 3 Spin-Up Test
**TC:** `SET_PARAM(0x0282, 2000)` (Service 20, Subtype 3) — command RW3 to +2000 RPM
**Verify:** `aocs.rw3_speed` (0x0209) reaches 1800-2200 RPM within 60s
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg within 60s
**TC:** `SET_PARAM(0x0282, -2000)` (Service 20, Subtype 3) — command RW3 to -2000 RPM
**Verify:** `aocs.rw3_speed` (0x0209) reaches -2200 to -1800 RPM within 120s
**TC:** `SET_PARAM(0x0282, 0)` (Service 20, Subtype 3) — return RW3 to zero
**Verify:** `aocs.rw3_speed` (0x0209) returns to < 100 RPM within 60s
**GO/NO-GO:** RW3 speed control verified in both directions

### Step 5 — Reaction Wheel 4 Spin-Up Test
**TC:** `SET_PARAM(0x0283, 2000)` (Service 20, Subtype 3) — command RW4 to +2000 RPM
**Verify:** `aocs.rw4_speed` (0x020A) reaches 1800-2200 RPM within 60s
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg within 60s
**TC:** `SET_PARAM(0x0283, -2000)` (Service 20, Subtype 3) — command RW4 to -2000 RPM
**Verify:** `aocs.rw4_speed` (0x020A) reaches -2200 to -1800 RPM within 120s
**TC:** `SET_PARAM(0x0283, 0)` (Service 20, Subtype 3) — return RW4 to zero
**Verify:** `aocs.rw4_speed` (0x020A) returns to < 100 RPM within 60s
**GO/NO-GO:** RW4 speed control verified in both directions

### Step 6 — Check Wheel Temperatures
**TC:** `GET_PARAM(0x028A)` (Service 20, Subtype 1) — RW1 temperature
**TC:** `GET_PARAM(0x028B)` (Service 20, Subtype 1) — RW2 temperature
**TC:** `GET_PARAM(0x028C)` (Service 20, Subtype 1) — RW3 temperature
**TC:** `GET_PARAM(0x028D)` (Service 20, Subtype 1) — RW4 temperature
**Verify:** All wheel temperatures in range [-10C, +55C] within 10s
**Action:** Post-test temperatures may be slightly elevated. Record for trending.
**GO/NO-GO:** All wheel temperatures within operational limits

### Step 7 — Momentum Desaturation Test
**Action:** Command desaturation to unload accumulated momentum using magnetorquers.
**TC:** `AOCS_DESATURATE` (Service 8, Subtype 1)
**Verify:** `aocs.rw1_speed` (0x0207) trending toward 0 RPM within 300s
**Verify:** `aocs.rw2_speed` (0x0208) trending toward 0 RPM within 300s
**Verify:** `aocs.rw3_speed` (0x0209) trending toward 0 RPM within 300s
**Verify:** `aocs.rw4_speed` (0x020A) trending toward 0 RPM within 300s
**Action:** Desaturation may take 5-10 minutes depending on momentum level and magnetic field strength. Monitor attitude stability during desaturation.
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg during desaturation
**GO/NO-GO:** Desaturation effective — wheel speeds trending toward zero

### Step 8 — Compile Wheel Commissioning Report
**Action:** Record spin-up/spin-down response times, speed control accuracy, temperatures, and desaturation performance for each wheel. Compare with specification. Document any anomalies.
**GO/NO-GO:** All four reaction wheels commissioned successfully

## Off-Nominal Handling
- If any wheel fails to reach commanded speed within 90s: Reduce commanded speed to +1000 RPM as diagnostic. If still unresponsive, check wheel power supply via `GET_PARAM(0x028E)`. If power nominal but wheel unresponsive, flag as failed. AOCS can operate on three wheels.
- If wheel temperature > +55C during testing: Halt testing on that wheel. Allow 10-minute cooldown period. Re-check temperature before resuming. If temperature remains high, reduce test speed to +1000 RPM.
- If attitude error > 10 deg during wheel test: Halt wheel test immediately via `SET_PARAM(0x028X, 0)`. Verify AOCS still controlling. Check if multiple wheels are loaded simultaneously (should not be). Resume testing after attitude recovers.
- If desaturation does not reduce wheel speeds: Verify magnetorquer operation via `GET_PARAM(0x0220)`. Check current orbit position for magnetic field strength (desaturation less effective near equator). Retry during polar passage.
- If one wheel shows significantly different response than others: Record anomaly. Possible bearing wear or drive electronics issue. Continue commissioning with remaining wheels. Schedule detailed investigation.

## Post-Conditions
- [ ] RW1 spin-up/spin-down verified in both directions
- [ ] RW2 spin-up/spin-down verified in both directions
- [ ] RW3 spin-up/spin-down verified in both directions
- [ ] RW4 spin-up/spin-down verified in both directions
- [ ] All wheel temperatures within operational limits
- [ ] Momentum desaturation via magnetorquers verified
- [ ] Wheel Commissioning Report generated
- [ ] GO decision for COM-007 (AOCS Mode Transition Testing)

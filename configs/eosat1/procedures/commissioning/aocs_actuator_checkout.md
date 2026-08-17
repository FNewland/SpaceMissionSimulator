# COM-004: AOCS Actuator Checkout
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
- [ ] COM-003 (AOCS Sensor Calibration) completed — star tracker active
- [ ] AOCS in COARSE_SUN mode (`aocs.mode` 0x020F = 3)
- [ ] Body rates < 0.05 deg/s on all axes
- [ ] All four wheels reporting telemetry (confirmed in LEOP-002)
- [ ] `eps.bat_soc` (0x0101) > 65%
- [ ] Bidirectional S-band link active
- [ ] Minimum 20 minutes of ground station pass remaining

## Procedure Steps

### Step 1 — Record Baseline Wheel Speeds
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.rw1_speed` (0x0207) reported within 10s — record value
**Verify:** `aocs.rw2_speed` (0x0208) reported within 10s — record value
**Verify:** `aocs.rw3_speed` (0x0209) reported within 10s — record value
**Verify:** `aocs.rw4_speed` (0x020A) reported within 10s — record value
**Action:** Wheels in COARSE_SUN may have non-zero speeds from attitude control. Record as baseline.
**GO/NO-GO:** All four wheels responding with valid speed readings

### Step 2 — Reaction Wheel 1 Spin-Up Test
**TC:** `RW_SET_SPEED_BIAS(wheel_idx=0, bias_rpm=2000.0)` (Service 8, Subtype 1, func_id 8) — bias RW1 by +2000 RPM
**Note:** `RW_SET_SPEED_BIAS` applies a bias *added to the wheel's current speed*; the Step 1 baseline is the reference for every check below. Wheel speeds (0x0207-0x020A) are READ-ONLY telemetry and cannot be commanded with `SET_PARAM`.
**Verify:** `aocs.rw1_speed` (0x0207) reaches baseline +1800 to +2200 RPM within 60s
**Action:** Monitor attitude disturbance — remaining wheels should compensate.
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg within 60s
**TC:** `RW_SET_SPEED_BIAS(wheel_idx=0, bias_rpm=-4000.0)` (Service 8, Subtype 1, func_id 8) — swing RW1 through zero to baseline -2000 RPM
**Verify:** `aocs.rw1_speed` (0x0207) reaches baseline -2200 to -1800 RPM within 120s
**TC:** `AOCS_RW_RAMP_DOWN(wheel_idx=0, target_rpm=0.0)` (Service 8, Subtype 1, func_id 14) — return RW1 to zero
**Verify:** `aocs.rw1_speed` (0x0207) returns to < 100 RPM within 60s
**GO/NO-GO:** RW1 speed control verified in both directions

### Step 3 — Reaction Wheel 2 Spin-Up Test
**TC:** `RW_SET_SPEED_BIAS(wheel_idx=1, bias_rpm=2000.0)` (Service 8, Subtype 1, func_id 8) — bias RW2 by +2000 RPM
**Verify:** `aocs.rw2_speed` (0x0208) reaches baseline +1800 to +2200 RPM within 60s
**Action:** Monitor attitude disturbance — remaining wheels should compensate.
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg within 60s
**TC:** `RW_SET_SPEED_BIAS(wheel_idx=1, bias_rpm=-4000.0)` (Service 8, Subtype 1, func_id 8) — swing RW2 through zero to baseline -2000 RPM
**Verify:** `aocs.rw2_speed` (0x0208) reaches baseline -2200 to -1800 RPM within 120s
**TC:** `AOCS_RW_RAMP_DOWN(wheel_idx=1, target_rpm=0.0)` (Service 8, Subtype 1, func_id 14) — return RW2 to zero
**Verify:** `aocs.rw2_speed` (0x0208) returns to < 100 RPM within 60s
**GO/NO-GO:** RW2 speed control verified in both directions

### Step 4 — Reaction Wheel 3 Spin-Up Test
**TC:** `RW_SET_SPEED_BIAS(wheel_idx=2, bias_rpm=2000.0)` (Service 8, Subtype 1, func_id 8) — bias RW3 by +2000 RPM
**Verify:** `aocs.rw3_speed` (0x0209) reaches baseline +1800 to +2200 RPM within 60s
**Action:** Monitor attitude disturbance — remaining wheels should compensate.
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg within 60s
**TC:** `RW_SET_SPEED_BIAS(wheel_idx=2, bias_rpm=-4000.0)` (Service 8, Subtype 1, func_id 8) — swing RW3 through zero to baseline -2000 RPM
**Verify:** `aocs.rw3_speed` (0x0209) reaches baseline -2200 to -1800 RPM within 120s
**TC:** `AOCS_RW_RAMP_DOWN(wheel_idx=2, target_rpm=0.0)` (Service 8, Subtype 1, func_id 14) — return RW3 to zero
**Verify:** `aocs.rw3_speed` (0x0209) returns to < 100 RPM within 60s
**GO/NO-GO:** RW3 speed control verified in both directions

### Step 5 — Reaction Wheel 4 Spin-Up Test
**TC:** `RW_SET_SPEED_BIAS(wheel_idx=3, bias_rpm=2000.0)` (Service 8, Subtype 1, func_id 8) — bias RW4 by +2000 RPM
**Verify:** `aocs.rw4_speed` (0x020A) reaches baseline +1800 to +2200 RPM within 60s
**Action:** Monitor attitude disturbance — remaining wheels should compensate.
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg within 60s
**TC:** `RW_SET_SPEED_BIAS(wheel_idx=3, bias_rpm=-4000.0)` (Service 8, Subtype 1, func_id 8) — swing RW4 through zero to baseline -2000 RPM
**Verify:** `aocs.rw4_speed` (0x020A) reaches baseline -2200 to -1800 RPM within 120s
**TC:** `AOCS_RW_RAMP_DOWN(wheel_idx=3, target_rpm=0.0)` (Service 8, Subtype 1, func_id 14) — return RW4 to zero
**Verify:** `aocs.rw4_speed` (0x020A) returns to < 100 RPM within 60s
**GO/NO-GO:** RW4 speed control verified in both directions

### Step 6 — Check Wheel Temperatures
**TC:** `GET_PARAM(param_id=0x0218)` (Service 20, Subtype 3) — `aocs.rw1_temp`
**TC:** `GET_PARAM(param_id=0x0219)` (Service 20, Subtype 3) — `aocs.rw2_temp`
**TC:** `GET_PARAM(param_id=0x021A)` (Service 20, Subtype 3) — `aocs.rw3_temp`
**TC:** `GET_PARAM(param_id=0x021B)` (Service 20, Subtype 3) — `aocs.rw4_temp`
**Verify:** All wheel temperatures in range [-10C, +55C] within 10s
**Note:** 0x028A-0x028D are CSS head health percentages (`aocs.css_head_health_py/my/pz/mz`),
not wheel temperatures.
**Action:** Post-test temperatures may be slightly elevated. Record for trending.
**GO/NO-GO:** All wheel temperatures within operational limits

### Step 7 — Momentum Desaturation Test
**Action:** Command desaturation to unload accumulated momentum using magnetorquers.
**TC:** `AOCS_DESATURATE` (Service 8, Subtype 1, func_id 1)
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
- If any wheel fails to reach commanded speed within 90s: Reduce the commanded bias to +1000 RPM as diagnostic. If still unresponsive, check that the wheel line is powered via `GET_PARAM(param_id=0x0117)` (`eps.pl_aocs_wheels`) and its draw via `GET_PARAM(param_id=0x011F)` (`eps.line_current_7`). If power nominal but wheel unresponsive, flag as failed. AOCS can operate on three wheels.
- If wheel temperature > +55C during testing: Halt testing on that wheel. Allow 10-minute cooldown period. Re-check temperature before resuming. If temperature remains high, reduce the test bias to +1000 RPM.
- If attitude error > 10 deg during wheel test: Halt the wheel test immediately via `AOCS_RW_RAMP_DOWN(wheel_idx=255, target_rpm=0.0)` (Service 8, Subtype 1, func_id 14 — 255 = all wheels). Verify AOCS still controlling. Check if multiple wheels are loaded simultaneously (should not be). Resume testing after attitude recovers.
- If desaturation does not reduce wheel speeds: Verify magnetorquer operation via `GET_PARAM(param_id=0x025E)` (`aocs.mtq_enabled`). Check current orbit position for magnetic field strength (desaturation less effective near equator). Retry during polar passage.
- If one wheel shows significantly different response than others: Record anomaly. Possible bearing wear or drive electronics issue. Continue commissioning with remaining wheels. Schedule detailed investigation.

## Post-Conditions
- [ ] RW1 spin-up/spin-down verified in both directions
- [ ] RW2 spin-up/spin-down verified in both directions
- [ ] RW3 spin-up/spin-down verified in both directions
- [ ] RW4 spin-up/spin-down verified in both directions
- [ ] All wheel temperatures within operational limits
- [ ] Momentum desaturation via magnetorquers verified
- [ ] Wheel Commissioning Report generated
- [ ] GO decision for COM-005 (AOCS Mode Transitions)

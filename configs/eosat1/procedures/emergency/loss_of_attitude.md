# PROC-EMG-002: Loss of Attitude Control
**Subsystem:** AOCS
**Phase:** EMERGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Recovery procedure for EOSAT-1 when the spacecraft is tumbling with angular rates exceeding
2 deg/s on any axis. This procedure commands a controlled detumble sequence, sheds non-essential
loads to preserve power during potential unfavourable sun geometry, and restores safe sun-pointing
attitude. Critical constraint: attitude must be stabilised before battery depletion during eclipse
passes (~35 min max eclipse, minimum survival SoC = 10%).

## Prerequisites
- [ ] Telemetry confirms `aocs.rate_roll` (0x0204), `aocs.rate_pitch` (0x0205), or `aocs.rate_yaw` (0x0206) > 2.0 deg/s
- [ ] Communication link active --- `ttc.link_status` (0x0501) = LOCKED
- [ ] Current `eps.bat_soc` (0x0101) > 20% (if below 20%, execute PROC-EMG-003 in parallel)
- [ ] AOCS engineering telemetry enabled (SID 2)
- [ ] Flight Dynamics team on console with real-time attitude estimation

## Procedure Steps

### Step 1 --- Immediate Load Shedding
**TC:** `PAYLOAD_SET_MODE(mode=0)` (Service 8, Subtype 5) --- payload OFF.
**TC:** `HEATER_CONTROL(circuit=obc, on=false)` (Service 8, Subtype 7) --- disable non-critical heaters.
**Verify:** `payload.mode` (0x0600) = OFF within 5s.
**Verify:** `eps.power_cons` (0x0106) decreases by >= 15W within 10s.
**Rationale:** Reduce power consumption to extend battery endurance during tumble when solar array
output may be severely degraded due to unfavourable orientation.
**GO/NO-GO:** Power consumption < 60W confirmed before proceeding.

### Step 2 --- Command Detumble Mode
**TC:** `AOCS_SET_MODE(mode=1)` (Service 8, Subtype 4) --- DETUMBLE mode.
**Verify:** `aocs.mode` (0x020F) = DETUMBLE (1) within 5s.
**Verify:** Magnetorquer activation confirmed via AOCS telemetry.
**Monitor:** `aocs.rate_roll` (0x0204), `aocs.rate_pitch` (0x0205), `aocs.rate_yaw` (0x0206) ---
expect rates to begin decreasing within 2--3 minutes.
**GO/NO-GO:** AOCS mode confirmed as DETUMBLE and at least one magnetorquer axis active.

### Step 3 --- Assess Reaction Wheel Status
**Monitor:** `aocs.rw1_speed` (0x0207), `aocs.rw2_speed` (0x0208), `aocs.rw3_speed` (0x0209),
`aocs.rw4_speed` (0x020A).
**Verify:** At least 3 of 4 wheels responding and speeds within operational limits (< 6000 RPM).
**Action:** If any wheel reports speed > 6000 RPM or not responding, note wheel ID for later
investigation. Detumble mode uses magnetorquers only and does not require wheels.
**GO/NO-GO:** Magnetorquer-based detumble confirmed active regardless of wheel status.

### Step 4 --- Monitor Rate Reduction (Orbit 1)
**Duration:** Monitor for 1 full orbit (~95 minutes).
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 1) --- request AOCS HK every 30s during pass.
**Expected:** Angular rates should decrease by approximately 50% per orbit under B-dot control.
**Verify:** All axes rates < 1.0 deg/s by end of first orbit.
**Critical:** Track `eps.bat_soc` (0x0101) throughout --- if SoC drops below 15% at any point,
immediately execute PROC-EMG-003 Step 1 (minimum survival load shed).
**GO/NO-GO:** Rates trending downward on all three axes.

### Step 5 --- Monitor Rate Reduction (Orbit 2)
**Duration:** Continue monitoring through second orbit.
**Expected:** All axis rates < 0.5 deg/s.
**Verify:** `eps.bat_soc` (0x0101) stable or increasing during sunlit phase.
**Verify:** `eps.power_gen` (0x0107) recovering as tumble rate decreases (better solar array illumination).
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 1) --- full EPS assessment.
**GO/NO-GO:** All rates < 0.5 deg/s AND `eps.bat_soc` (0x0101) > 15%.

### Step 6 --- Confirm Detumble Complete
**Verify:** `aocs.rate_roll` (0x0204) < 0.1 deg/s.
**Verify:** `aocs.rate_pitch` (0x0205) < 0.1 deg/s.
**Verify:** `aocs.rate_yaw` (0x0206) < 0.1 deg/s.
**Duration:** All rates must remain below 0.1 deg/s for at least 10 consecutive minutes.
**GO/NO-GO:** Stable detumbled state confirmed. If rates oscillating above threshold,
continue detumble for one additional orbit before proceeding.

### Step 7 --- Command Safe Sun-Pointing
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 4) --- SAFE_POINT (sun acquisition).
**Verify:** `aocs.mode` (0x020F) = SAFE_POINT (2) within 5s.
**Verify:** `aocs.att_error` (0x0217) decreasing toward 0 deg within 10 minutes.
**Monitor:** Reaction wheel speeds --- expect ramp-up as wheels acquire sun-pointing attitude.
**Verify:** `eps.power_gen` (0x0107) increasing as solar arrays achieve sun illumination.
**GO/NO-GO:** `aocs.att_error` (0x0217) < 5.0 deg within 15 minutes.

### Step 8 --- Verify Power-Positive State
**Verify:** `eps.power_gen` (0x0107) > `eps.power_cons` (0x0106) (power-positive).
**Verify:** `eps.bat_soc` (0x0101) trending upward.
**Verify:** `eps.sa_a_current` (0x0103) and `eps.sa_b_current` (0x0104) both > 0.5A (both arrays illuminated).
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 1) --- confirm full EPS state.
**Duration:** Maintain SAFE_POINT for minimum 2 orbits before any recovery actions.
**GO/NO-GO:** Power-positive state confirmed. Proceed to PROC-EMG-004 for staged recovery to nominal.

## Timing Constraints
| Condition | Threshold | Action |
|---|---|---|
| Battery SoC during tumble | < 15% | Execute PROC-EMG-003 in parallel |
| Eclipse entry while rates > 1 deg/s | ~35 min to depletion | Priority: reduce rate before eclipse |
| Detumble not converging (rates constant after 2 orbits) | ~190 min | Investigate magnetorquer failure, consider wheel-based backup |
| Sun acquisition timeout | > 20 min at att_error > 10 deg | Re-command SAFE_POINT, check sun sensor data |

## Off-Nominal Handling
- If magnetorquers non-responsive: attempt wheel-based rate reduction by commanding individual wheel speeds via direct AOCS override. Requires Flight Dynamics specialist.
- If rates increasing instead of decreasing: possible magnetic field model error or magnetorquer polarity inversion. Command AOCS OFF, wait one orbit, re-attempt detumble.
- If only 2 of 4 wheels operational after detumble: remain in SAFE_POINT using reduced wheel set. Do NOT attempt NADIR_POINT until wheel anomaly resolved.
- If power drops below 10% during detumble: battery preservation takes priority. Command `OBC_SET_MODE(mode=2)` to enter emergency mode, which disables all non-survival loads.
- If attitude oscillating in SAFE_POINT: check for stuck thruster or unbalanced wheel. Revert to DETUMBLE, investigate sensor data.

## Post-Conditions
- [ ] All body rates < 0.1 deg/s sustained for >= 10 minutes
- [ ] `aocs.mode` (0x020F) = SAFE_POINT (2) and `aocs.att_error` (0x0217) < 5.0 deg
- [ ] Spacecraft power-positive: `eps.power_gen` (0x0107) > `eps.power_cons` (0x0106)
- [ ] `eps.bat_soc` (0x0101) > 20% and trending upward
- [ ] Reaction wheel status assessed --- minimum 3 of 4 operational
- [ ] Root cause of attitude loss under investigation
- [ ] Transition to PROC-EMG-004 for staged recovery to nominal operations

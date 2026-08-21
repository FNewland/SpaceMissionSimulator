# COM-003: AOCS Sensor Calibration
**Subsystem:** AOCS
**Phase:** COMMISSIONING
**Revision:** 3.0 --- 2026-08c: runs BEFORE COM-004. The calibration
precondition is no longer a mode gate: it is the estimator configuration
itself (selected ST tracking >= 5 stars AND `aocs.est_source` 0x02C1 bit 0 ---
which COARSE_SUN reaches once quiescent below 0.25 deg/s) plus all gyro units
powered and |rate - reference| <= 0.10 deg/s. Wheels are NOT required and are
still disabled when this card flies.
**Approved:** Flight Operations Director

**Entry state:** COM-002 exit state --- spacecraft in the COARSE_SUN hold left
by LEOP (wheels DISABLED, `aocs.wheels_enabled` 0x0260 = 0), star trackers and
gyro units OFF, SID 2 downlinking, mission phase 5.

## Purpose
Commission the AOCS determination chain: bring all three gyro units on line,
bring a star tracker into the determination loop, and run the on-board gyro
bias calibration so the rate channel is bias-compensated before the spacecraft
is asked to hold fine pointing.

**Revision 2.0 — what changed and why.** Revision 1.0 of this card was almost
entirely fabricated. It commanded the calibration with
`SET_PARAM(param_id=0x0250, value=1)`, read status from `0x0251` and bias from
`0x0252-0x0254`, started a magnetometer calibration at `0x0260`, powered the
star tracker with `0x0270` and enabled it in the loop with `0x0275`. **None of
those parameters exist**, `SET_PARAM` cannot write a command anyway, and there
is no on-board magnetometer calibration at all. An operator following the old
card sent seven telecommands that did nothing and then recorded made-up
numbers. The real command is `AOCS_GYRO_CALIBRATION` (Service 8, Subtype 1,
func_id 13), and until this revision it was itself a stub that zeroed an
internal number and reported success without estimating anything.

**On IMU power.** EO-Sat-1 carries three single-axis gyros and **no redundant
IMU and no IMU power line**. The units are powered individually as S2 devices
`S2_AOCS_GYROSCOPE_0/1/2` (device ids 0x0206, 0x0207, 0x0208 = 518, 519, 520),
one per body axis. There is no "IMU ON" telecommand to look for; these three
device commands are it.

## Prerequisites
- [ ] COM-001 and COM-002 completed. COM-004 is NOT a prerequisite --- this card
      deliberately runs with the wheels disabled (`aocs.wheels_enabled`
      0x0260 == 0), which is the whole point of the 2026-08c precondition
      change: bias calibration needs a tracking head feeding the filter and
      low rates, not wheel control
- [ ] `eps.pl_aocs_wheels` (0x0117) == 1 — the AOCS suite line is ON (LEOP-007)
- [ ] `eps.bat_soc` (0x0101) > 60 %
- [ ] OBC running the application (not the bootloader)
- [ ] Bidirectional S-band link active
- [ ] At least 20 minutes of ground station pass remaining. The calibration
      itself is a 300 s arc and the card holds a further 300 s of NOMINAL after
      it, so a short pass will not cover the whole procedure.
- [ ] **Not** in eclipse entry/exit within the next 10 minutes — the AOCS hands
      over to ECLIPSE (mode 8) at the terminator. That is harmless for the
      calibration (the tracker stays in the loop) but it changes `aocs.mode`
      under the operator, which the verifications below allow for.

## Procedure Steps

### Step 1 — Enable the AOCS Determination Housekeeping
**TC:** `HK_ENABLE(sid=2)` (Service 3, Subtype 5) --- AOCS housekeeping, 8 s
**TC:** `HK_ENABLE(sid=7)` (Service 3, Subtype 5) --- AOCS determination, 4 s
**Note:** SID 7 `AOCS_DETERMINATION` carries the calibration channels
(`aocs.gyro_cal_state` 0x02C9, `aocs.gyro_cal_progress` 0x02CA,
`aocs.gyro_cal_bias_x/y/z` 0x02CB-0x02CD, `aocs.gyro_cal_comp_mag` 0x02CE,
`aocs.gyro_cal_result` 0x02CF, `aocs.gyro_cal_reject` 0x02D0) at a 4 s cadence.
Without it enabled the calibration runs blind from the ground.
**Verify:** `obdh.hk_sids_enabled` reported within 20s
**GO/NO-GO:** Both AOCS housekeeping structures are downlinking

### Step 2 — Confirm All Three Gyro Units Are Powered
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.gyro_units_on` (0x0294) == 3 within 20s
**Verify:** `aocs.gyro_units_mask` (0x02D1) == 7 within 20s
**Note:** 0x02D1 is a bitmask — bit0 = X unit (device 0x0206), bit1 = Y
(0x0207), bit2 = Z (0x0208). The count alone cannot tell you *which* channel is
missing, and losing pitch on a nadir-tracking spacecraft is a different problem
from losing roll.
**Note:** If any unit is off, switch it on with the matching device command
before continuing; a calibration with a dead channel is refused with code
0x0A02.
**TC:** `S2_AOCS_GYROSCOPE_0(device_id=518, state=1)` (Service 2, Subtype 1) --- X unit ON
**TC:** `S2_AOCS_GYROSCOPE_1(device_id=519, state=1)` (Service 2, Subtype 1) --- Y unit ON
**TC:** `S2_AOCS_GYROSCOPE_2(device_id=520, state=1)` (Service 2, Subtype 1) --- Z unit ON
**Verify:** `aocs.gyro_units_mask` (0x02D1) == 7 within 20s
**GO/NO-GO:** All three gyro units powered

### Step 3 — Bring a Star Tracker Into the Determination Loop
**TC:** `ST1_POWER(on=1)` (Service 8, Subtype 1, func_id 4)
**TC:** `ST2_POWER(on=1)` (Service 8, Subtype 1, func_id 5)
**TC:** `ST_SELECT(unit=1)` (Service 8, Subtype 1, func_id 6) --- select ST1.
Wire encoding is **1 = ST1, 2 = ST2; 0 is REFUSED** (2026-08c ruling --- the
old "0=ST1, 1=ST2" text sent operators a refused byte for ST1 and silently
selected ST1 when they wanted ST2).
**Note:** Each tracker takes about 60 s to boot and acquire.
**Verify:** `aocs.st1_status` (0x0240) == 2 within 120s --- TRACKING
**Verify:** `aocs.st1_num_stars` (0x0241) >= 5 within 120s
**Verify:** `aocs.st2_status` (0x0243) == 2 within 120s --- redundant unit also tracking
**Note:** There is no separate "tracker valid" telemetry channel; the
selected unit's status (`aocs.st_selected` 0x0244 picks it) IS the validity.
**GO/NO-GO:** The selected tracker is tracking at least five stars

### Step 4 — Wait for the Tracker to Aid the Estimator (no mode command)
**Action:** Stay in the COARSE_SUN hold. Once the vehicle is quiescent
(|rate| < 0.25 deg/s) and the selected tracker holds >= 5 stars, the ST aids
the MEKF and `aocs.est_source` (0x02C1) bit 0 sets --- IN COARSE_SUN. That is
the 2026-08c precondition change: no NOMINAL-family mode is required, so no
mode is commanded here. (DETUMBLE never sets bit 0; a calibration there is
still refused with 0x0A03.)
**Note:** Once the tracker joins, the COARSE_SUN hold will AUTO-PROMOTE to the
commanded mode (fallback NOMINAL) after ~60 s of sun lock + tracking ---
COARSE_SUN is a hold, never a destination. That promotion is expected DURING
this card and is harmless to the calibration: modes 3, 4 and 8 all satisfy the
estimator precondition once bit 0 is set. Do not verify mode == 3 after the
lock holds.
**Verify:** `aocs.est_source` (0x02C1) bit 0 set within 600s --- ST aiding
**Verify:** `aocs.knowledge_err_3sig` (0x02A7) < 300 within 600s --- arcsec
**GO/NO-GO:** Star tracker feeding the estimator

### Step 5 — Record the Pre-Calibration Bias
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.gyro_bias_x` (0x0270) reported within 20s --- record
**Verify:** `aocs.gyro_bias_y` (0x0271) reported within 20s --- record
**Verify:** `aocs.gyro_bias_z` (0x0272) reported within 20s --- record
**Verify:** `aocs.gyro_bias_resid` (0x02D2) reported within 20s --- record
**Note:** 0x0270-0x0272 report the bias present at the gyro **output**, i.e.
after any compensation already loaded; 0x02D2 is the magnitude of the same
quantity. On a healthy uncalibrated unit expect a few times 1e-5 deg/s
(0.1 deg/h bias instability). A value near or above 0.05 deg/s means a real
bias is present and `GYRO_BIAS_HIGH` (0x020B) will have been raised.
**GO/NO-GO:** Pre-calibration bias recorded

### Step 6 — Command the Gyro Bias Calibration
**TC:** `AOCS_GYRO_CALIBRATION(action=0, duration_s=300)` (Service 8, Subtype 1, func_id 13)
**Note:** `duration_s=0` selects the configured default arc
(`sensors.gyro.calibration.duration_s`, 300 s); any other value is clamped to
[30, 1800] s. The spacecraft answers TM(8,130) with state, progress and the arc
it actually accepted.
**Note:** The spacecraft averages the attitude filter's observed gyro bias over
the arc (discarding the first 30 s while the filter settles) and then writes the
result into an on-board compensation table applied on the gyro measurement path.
It is not instantaneous and it is not a reset.
**Verify:** `aocs.gyro_cal_state` (0x02C9) == 1 within 20s --- RUNNING
**Verify:** `event 0x0210 AOCS_GYRO_CAL_START` received within 20s
**Note:** The S1.7 execution complete for this telecommand arrives when the ARC ENDS, not on acceptance — the verification row stays open for the whole arc, and an abort part-way through reports S1.8 against the same telecommand carrying the blocking code.
**Note:** If the command is refused, the S1.8 failure notice names the gate.
0x0A01 = AOCS unpowered, 0x0A02 = a gyro unit is off (Step 2), 0x0A03 = no star
tracker in the loop (Steps 3-4), 0x0A04 = body rate above the 0.10 deg/s
calibration limit, 0x0A05 = a calibration is already running. The same code is
latched in `aocs.gyro_cal_reject` (0x02D0).
**Note:** 0x0A04 is COMMON in the COARSE_SUN hold: the magnetorquer control
hunts at up to ~0.2 deg/s. Wait for a quiet spell (all rates < 0.08 deg/s on
SID 2) and retry; after the auto-promotion to NOMINAL the vehicle settles and
the calibration usually starts first time. Retrying is the card's nominal
loop, not an anomaly.
**⚠ 2026-08 (epoch/altitude ruling):** at the re-epoched mission T-0
(2026-08-20T12:00:00Z, TLE-true ~513 km orbit) the wheels-off MTQ control
hunts at 0.1–0.4 deg/s **continuously** — measured attempts every 10 s over
three orbits never found a spell below the 0.10 deg/s gate. Until the owner
rules (relax the gate, allow an MTQ-quiet calibration arc, or move this card
after COM-004), expect Step 6 to refuse with 0x0A04 indefinitely at this
epoch; record the refusals and escalate rather than looping forever.
**GO/NO-GO:** Calibration RUNNING

### Step 7 — Monitor the Calibration Arc
**TC:** `HK_REQUEST(sid=7)` (Service 3, Subtype 27) --- poll every 30 s
**Verify:** `aocs.gyro_cal_progress` (0x02CA) > 0 within 60s
**Verify:** `aocs.gyro_cal_progress` (0x02CA) >= 100 within 330s
**Note:** Do NOT slew, desaturate or command a mode change during the arc.
Any manoeuvre above the 0.10 deg/s quiescence limit aborts the calibration
with `AOCS_GYRO_CAL_ABORT` (0x0212) rather than average nonsense. The
AUTOMATIC COARSE_SUN -> NOMINAL promotion may itself abort a running arc if
the promotion transient exceeds the limit --- that is the 0x0A04 abort case;
re-run from Step 6 once settled (off-nominal handling below).
**Note:** To abandon the run deliberately, send
`AOCS_GYRO_CALIBRATION(action=1, duration_s=0)`; the abort is reported with
code 0x0A08.
**GO/NO-GO:** Calibration reached 100 % without an abort

### Step 8 — Verify the Calibration Result
**TC:** `HK_REQUEST(sid=7)` (Service 3, Subtype 27)
**Verify:** `aocs.gyro_cal_state` (0x02C9) == 2 within 30s --- COMPLETE
**Verify:** `aocs.gyro_cal_result` (0x02CF) == 1 within 30s --- SUCCESS
**Verify:** `aocs.gyro_cal_reject` (0x02D0) == 0 within 30s
**Verify:** `event 0x0211 AOCS_GYRO_CAL_COMPLETE` received within 30s
**TC:** `GET_PARAM(param_id=0x02CB)` (Service 20, Subtype 3) --- estimated X bias
**TC:** `GET_PARAM(param_id=0x02CC)` (Service 20, Subtype 3) --- estimated Y bias
**TC:** `GET_PARAM(param_id=0x02CD)` (Service 20, Subtype 3) --- estimated Z bias
**Verify:** `aocs.gyro_cal_bias_x` (0x02CB) reported within 20s --- record
**Verify:** `aocs.gyro_cal_bias_y` (0x02CC) reported within 20s --- record
**Verify:** `aocs.gyro_cal_bias_z` (0x02CD) reported within 20s --- record
**Note:** Each axis should be within +/- 0.5 deg/s. A healthy unit calibrates
out at a few times 1e-5 deg/s; a value near 0.05 deg/s means a real bias was
present and has now been compensated, which is a successful calibration, not a
failure.
**GO/NO-GO:** Calibration reports SUCCESS with a plausible per-axis bias

### Step 9 — Confirm the Compensation Took Effect
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.gyro_bias_resid` (0x02D2) < 0.005 within 60s
**TC:** `HK_REQUEST(sid=7)` (Service 3, Subtype 27)
**Verify:** `aocs.gyro_cal_comp_mag` (0x02CE) reported within 60s --- record
**Note:** These two numbers are the whole point of the card and they are
deliberately different quantities. 0x02CE is the magnitude of the compensation
now applied on board; 0x02D2 is what is LEFT at the gyro output. A successful
calibration moves the bias from 0x02D2 into 0x02CE.
**Note:** `aocs.gyro_bias_injected` (0x0296) is instructor visibility of an
injected `gyro_bias` hardware fault and is **deliberately unchanged** by a
calibration. Compensating a bias is not repairing the unit: 0x0296 keeps
reporting the fault while 0x02D2 shows the vehicle is no longer suffering from
it. If 0x0296 is non-zero, that is a training scenario, not an anomaly.
**GO/NO-GO:** Residual bias compensated

### Step 10 — Post-Calibration Determination Check
**TC:** `HK_REQUEST(sid=7)` (Service 3, Subtype 27) --- poll every 60 s for 5 minutes
**Verify:** `aocs.knowledge_err_3sig` (0x02A7) < 120 within 300s --- arcsec
**Verify:** `aocs.control_err` (0x02A8) < 1.0 within 300s --- deg
**Verify:** `aocs.est_source` (0x02C1) >= 9 within 300s
**Verify:** `aocs.gyro_cal_state` (0x02C9) == 2 within 300s --- still COMPLETE, not re-aborted
**Note:** The operational value of the calibration is only visible when the
tracker is NOT available: with the gyro compensated the estimate coasts on the
gyro through a tracker outage almost flat, where an uncompensated 0.05 deg/s
bias walks the estimate tens of degrees off in ten minutes and trips the
`aocs.att_error` FDIR rule. Do not manufacture a tracker outage to prove this on
a real spacecraft; it is demonstrated in the training scenario.
**GO/NO-GO:** AOCS determination stable after calibration

## Off-Nominal Handling
- **Command refused, `aocs.gyro_cal_reject` (0x02D0) = 0x0A02.** A gyro unit is
  off. Read `aocs.gyro_units_mask` (0x02D1) to see which, then power it with the
  matching `S2_AOCS_GYROSCOPE_n` command and retry the calibration.
- **Refused with 0x0A03.** The star tracker is not in the determination loop.
  Either the tracker is not tracking (`aocs.st1_status` 0x0240 != 2 — repeat the
  tracker power-on) or the AOCS is in DETUMBLE/COARSE_SUN, where the tracker is
  not used at all (re-command NOMINAL and wait for it).
- **Refused with 0x0A04.** Body rate is above 0.10 deg/s relative to the
  reference. Wait for the slew or the desaturation to finish
  (`aocs.slew_state` 0x0292 = 0, `aocs.mode` 0x020F != 7) and retry.
- **Refused with 0x0A05.** A calibration is already running. Either wait for it
  (`aocs.gyro_cal_progress` 0x02CA) or abort it with `action=1`.
- **`AOCS_GYRO_CAL_ABORT` (0x0212) raised mid-arc.** The averaging was
  discarded and NOTHING was applied — the previous compensation, if any, is
  still in force and `aocs.gyro_cal_result` (0x02CF) reads 2. Fix the cause
  named by 0x02D0 and re-run from Step 6. Aborting is the correct behaviour; a
  calibration that had lost its attitude reference would otherwise load a bias
  table built from noise.
- **Estimated bias > 0.5 deg/s on any axis.** Do not accept it. Re-run with a
  longer arc (`duration_s=900`) after confirming the vehicle is genuinely
  quiescent. If it persists, the unit is out of specification — raise a
  spacecraft anomaly report and consider flying with that axis's unit off,
  accepting the degraded knowledge on that axis (`aocs.gyro_units_mask` 0x02D1
  records the configuration).
- **Star tracker fails to acquire within 120 s.** Check for the sun or the moon
  in the field of view (`aocs.st1_status` 0x0240 = 3 is BLIND, not failed) and
  wait for the attitude to evolve. Try the redundant unit with
  `ST_SELECT(unit=2)` (Service 8, Subtype 1, func_id 6).
- **Magnetometer calibration.** Revision 1.0 of this card ran a magnetometer
  hard-iron/soft-iron calibration against IGRF at parameters 0x0260-0x0262.
  **There is no such function on board and there are no such parameters.** The
  magnetometer is used as-is on the coarse two-vector path; nothing in this card
  calibrates it. Do not attempt to command it.

## Post-Conditions
- [ ] All three gyro units powered — `aocs.gyro_units_mask` (0x02D1) == 7
- [ ] Star tracker tracking >= 5 stars (`aocs.st1_status` 0x0240 == 2) and in
      the determination loop —
      `aocs.est_source` (0x02C1) bit0 and bit3 both set
- [ ] Gyro bias calibration COMPLETE and SUCCESS —
      `aocs.gyro_cal_state` (0x02C9) == 2, `aocs.gyro_cal_result` (0x02CF) == 1
- [ ] Per-axis estimated bias recorded from 0x02CB-0x02CD
- [ ] Compensation applied (`aocs.gyro_cal_comp_mag` 0x02CE) and residual bias
      below 0.005 deg/s (`aocs.gyro_bias_resid` 0x02D2)
- [ ] Attitude knowledge error below 120 arcsec 3σ (`aocs.knowledge_err_3sig`
      0x02A7)
- [ ] Sensor Calibration Report generated with the recorded bias values and the
      pre/post residual

## Exit state
- All three gyro units ON, both trackers ON, ST1 selected and tracking
- Gyro bias calibration COMPLETE/SUCCESS with compensation applied
- AOCS very likely AUTO-PROMOTED to NOMINAL (mode 4, or 8 in eclipse) --- the
  expected side effect of bringing the tracker up in a COARSE_SUN hold;
  wheels still DISABLED (0x0260 = 0)
- No power-line or TTC state touched

**NEXT:** COM-004 (AOCS Actuator Checkout --- enables the wheels)

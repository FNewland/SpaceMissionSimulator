# NOM-013: Orbit Maintenance Manoeuvre Execution

**Subsystem:** Propulsion
**Phase:** NOMINAL
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Execute a planned delta-V burn (debris-avoidance or drag make-up, from
NOM-004): catbed preheat, arming, burn, live monitoring with abort criteria,
and post-burn orbit determination. Typically flown over a ground station for
direct observation; the burn itself is 8-260 s depending on dV and tank
pressure (0.85 m/s ~ 32 s at beginning of life).

> Note (book reconciliation): the facilitator book numbers this card NOM-005.
> Sim NOM-005 is the shipped HK Configuration procedure, so the execution
> card is NOM-013 here; the book carries an editor's note.

## Prerequisites
- [ ] NOM-004 completed: burn parameters authorised, S11 sequence loaded (or
      manual execution authorised in-pass)
- [ ] AOCS in NOMINAL (4), FINE_POINT (5), or INERTIAL (9) hold for
      retrograde: `aocs.aocs_mode` (0x020F)
- [ ] Attitude error < 2.0 deg: `aocs.att_error` (0x0217)
- [ ] Battery SoC > 45 %: `eps.bat_soc` (0x0101)
- [ ] Not in eclipse (arm interlock)
- [ ] PROP housekeeping enabled (SID 9)

## Procedure Steps

### Step 1 --- Power and Catbed Preheat (T-25 min)
**TC:** `EPS_POWER_ON` line_index=8 (Service 8, Subtype 1) func_id = 19 --- energise the propulsion line
**Verify:** `eps.pl_prop` (0x014A) = 1
**TC:** `HEATER_THRUSTER` on=1 (Service 8, Subtype 1) func_id = 42 --- catbed preheat, 12 W
**Verify:** `tcs.htr_thruster` (0x041B) = 1; `tcs.temp_thruster` (0x0409)
rising (~10 C/min initially)
**Note:** Preheat to the 180 C arm interlock takes ~18-20 min (4 Wh). A
manoeuvre is a POWER activity: preheat + burn must fit the daylight power
budget.

### Step 2 --- Attitude for Burn
**Verify:** `aocs.aocs_mode` (0x020F) = 4 (prograde) or 9 with the 180-deg yaw
target quaternion loaded (retrograde)
**Verify:** `aocs.att_error` (0x0217) < 0.5 deg and stable
**Note:** The thrust axis is +X body: nominal nadir attitude gives a prograde
burn. Every degree of attitude error costs cos(theta) of along-track dV and
leaks sin(theta) into a lateral (orbit-normal) component.

### Step 3 --- Load and Arm
**TC:** `PROP_SET_BURN` dv, direction, mode (Service 8, Subtype 1) func_id = 110 --- load the manoeuvre
**Verify:** `prop.burn_dv_cmd` (0x0B0E) = planned dV; `prop.burn_time_cmd`
(0x0B10) within 10 % of the FDS prediction — a disagreement means the ground
model and the spacecraft disagree about tank state: STOP and resolve.
**Verify:** `tcs.temp_thruster` (0x0409) >= 180; `prop.arm_inhibit_mask`
(0x0B15) = 0
**TC:** `PROP_ARM` arm_code=0xA5C3 (Service 8, Subtype 1) func_id = 108 --- arm (within 300 s of SET_BURN)
**Verify:** `prop.state` (0x0B00) = 1 (ARMED); event PROP_ARMED (0x0800)
**Note:** A rejection returns a distinct code per interlock and sets the
blocking bit in 0x0B15 — read the mask before re-trying anything.

### Step 4 --- Execute and Monitor
**TC:** `PROP_EXECUTE` (Service 8, Subtype 1) func_id = 111 --- start the burn (or S11 fires it)
**Verify:** `prop.state` (0x0B00) = 2 (FIRING); event PROP_BURN_START (0x0803)
**Monitor (continuous, whole burn):**
  - `prop.thrust_est` (0x0B09) ~ 330 mN at BOL, tracking tank pressure
  - `aocs.att_error` (0x0217) — the on-board rule aborts above 1.0 deg
    (S19 rule 8001) and the model aborts at 2.0 deg
  - `prop.burn_time_elapsed` (0x0B11) vs `prop.burn_time_cmd` (0x0B10)
**Caution:** Time acceleration is clamped to 10x during the burn
(PROP_SPEED_CLAMPED 0x0811) — do not fight the clamp.

### Step 5 --- Abort Criteria (standing, no FD call required)
**TC:** `PROP_ABORT` (Service 8, Subtype 1) func_id = 112 --- immediate manual abort
**Evaluate:** Abort NOW if: attitude error > 1.0 deg and growing; thrust < 60 %
of prediction; tank pressure falling faster than blowdown; any AOCS mode drop.
**Verify (after any abort):** `prop.burn_status` (0x0B0D) = 4 and
`prop.abort_reason` (0x0B14) identifies the cause. The partial dV already
delivered HAS changed the orbit — post-burn OD (Step 6) is still mandatory.
**Note:** An off-axis burn abort is an AOCS anomaly first: GO CTG-002, then
re-plan the remainder under NOM-004. A second abort latches INHIBIT (0x0810);
only PROP_INHIBIT_CLR (func 117) clears it after investigation.

### Step 6 --- Post-Burn Orbit Determination
**Verify:** event PROP_BURN_COMPLETE (0x0804); `prop.burn_dv_achieved`
(0x0B0F) vs `prop.burn_dv_cmd` (0x0B0E) — within 2 % nominally
**Verify:** `prop.sma_km` (0x0B1A) moved by the predicted da (1.79 km per
m/s prograde); `prop.orbit_epoch_version` (0x0B19) incremented — planner and
MCS contact predictions now follow the new orbit
**Evaluate:** At the next pass, compare observed AOS against the pre-burn
prediction: a 1.0 m/s burn shifts pass times ~34 s/day. Achieved-under-plan
by > 5 % = underperforming thruster: re-plan a trim burn under NOM-004 and
raise a ticket (possible catbed degradation — see CTG-028).

### Step 7 --- Safing and Closeout
**TC:** `PROP_DISARM` (Service 8, Subtype 1) func_id = 109 --- ARMED -> SAFED
**TC:** `HEATER_THRUSTER` on=0 (Service 8, Subtype 1) func_id = 42 --- catbed heater off
**TC:** `EPS_POWER_OFF` line_index=8 (Service 8, Subtype 1) func_id = 20 --- de-energise the propulsion line
**Verify:** `prop.state` (0x0B00) = 0; `eps.pl_prop` (0x014A) = 0
**Verify:** `aocs.total_momentum` (0x025B) < 0.02 N m s. The thrust-offset
torque loads the wheels during a burn — if momentum approached the envelope,
GO NOM-007 (Momentum Management).
**Record:** Achieved dV, propellant consumed (0x0B02 delta), burn count
(0x0B12), remaining envelope (0x0B0C) into the dV budget log.

## Deviations
- **Burn does not start** -> check `prop.arm_inhibit_mask` (0x0B15) and the
  EPS prop line; a shed line disarms the system by design.
- **Burn under-performs** -> complete post-burn OD, then re-plan a trim burn
  (NOM-004). Do not extend the burn ad hoc.
- **AOCS off-axis during burn** -> the auto-abort fires; GO CTG-002.

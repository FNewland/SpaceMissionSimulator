# CTG-028: Propulsion Anomaly

**Subsystem:** Propulsion
**Phase:** CONTINGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Respond to a propulsion FDIR trigger — low thrust, valve fault, leak, or tank
pressure out of band — by isolating the propellant, bounding the remaining
usable delta-V, and protecting the debris-avoidance reserve. On EO-Sat-1 the
propulsion system exists FOR collision avoidance: the recovery objective is
not "fix the thruster", it is "know exactly how much CAM capability remains
and keep it safe".

> Note (book reconciliation): the facilitator book numbers this card CTG-006
> and its step 1 says "SID 5 / propulsion". Sim CTG-006 is Payload Anomaly
> and SID 5 is Payload; the propulsion card is CTG-028 and the propulsion
> housekeeping structure is SID 9.

## Entry Criteria
Any of:
- S12 alarm on `prop.tank_pressure` (0x0B01) — absolute band or the DELTA
  (rate) monitor, which is what catches a slow leak
- Event PROP_LEAK_DETECTED (0x080B), PROP_VALVE_FAULT (0x080A),
  PROP_UNDERPERFORMANCE (0x080C), PROP_TANK_PRESSURE_HIGH (0x0807)
- Post-burn OD shows achieved dV < 90 % of plan (from NOM-013 step 6)

## Procedure Steps

### Step 1 --- Read Full Propulsion Telemetry
**TC:** `HK_ENABLE(sid=9)` (Service 3, Subtype 5) --- ensure the PROP structure is live
**Verify:** Capture and record ALL of: `prop.state` (0x0B00),
`prop.tank_pressure` (0x0B01) + trend, `prop.propellant_mass` (0x0B02),
`prop.tank_temp` (0x0B04), `prop.catbed_temp` (0x0B05), valve states
(0x0B06/0x0B07/0x0B08), `prop.leak_rate` (0x0B16), `prop.abort_reason`
(0x0B14).
**Evaluate:** Pressure falling with propellant mass constant = pressurant
leak; pressure and mass falling together = propellant leak; pressure rising
= thermal (check 0x0B04 and heater states).

### Step 2 --- Isolate
**TC:** `PROP_ISO_CLOSE(branch=2)` (Service 8, Subtype 1) func_id = 114 --- close BOTH latch-valve branches
**Verify:** `prop.latch_a_state` (0x0B06) = 0 and `prop.latch_b_state`
(0x0B07) = 0. A branch reporting 2 (fault) did NOT close — it is stuck:
close the other branch, note that isolation is partial, and treat the
propellant downstream of the stuck valve as lost.
**Verify:** Leak rate (0x0B16) and the 0x0B01 pressure rate after isolation —
a continuing fall means the leak is upstream of the latch valves (tank side):
it cannot be isolated, only bounded.
**Note:** The on-board rule 8004 auto-isolates on a leak event; this step
verifies and completes what autonomy started.

### Step 3 --- Bound the Remaining Usable Delta-V
**Verify:** `prop.dv_remaining` (0x0B0C) — the on-board Tsiolkovsky figure on
the surviving propellant.
**Evaluate:** Correct it for the failure mode: subtract propellant behind a
stuck-open branch; for an unisolatable tank leak, project mass at the current
leak rate to the next credible conjunction epoch. **Record the bounded CAM
reserve and report it to the Flight Director** — below ~1 m/s the spacecraft
has effectively lost its debris-avoidance capability and the conjunction
posture changes (screening thresholds, earlier decisions).

### Step 4 --- Inhibit and Ticket
**TC:** `PROP_INHIBIT_SET(on=1)` (Service 8, Subtype 1) func_id = 116 --- latch the ground inhibit while the anomaly is open
**Verify:** `prop.arm_inhibit_mask` (0x0B15) bit 9 set.
**Record:** Anomaly ticket: trigger, telemetry capture, isolation state,
bounded dV. Underperformance (0x080C) with nominal pressure indicates catbed
degradation — future burns re-planned with the measured performance factor.

### Step 5 --- Escalation
**Evaluate:** RISING tank pressure (0x0807) that does not respond to heater
safing threatens the burst disc: GO CTG-004 (Thermal Runaway response) and
prepare EMG-001 (Safe Mode) — safe mode disarms propulsion by rule 8005.

## Exit Criteria
- Propellant isolated (or the leak explicitly bounded as unisolatable)
- Bounded remaining dV recorded and briefed to the FD
- Ground inhibit latched; clearing it (PROP_INHIBIT_CLR, func 117) requires a
  closed anomaly ticket and FD authorisation

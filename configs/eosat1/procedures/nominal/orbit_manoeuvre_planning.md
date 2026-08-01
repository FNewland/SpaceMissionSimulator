# NOM-004: Orbit Maintenance Manoeuvre Planning

**Subsystem:** Propulsion / Flight Dynamics
**Phase:** NOMINAL
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Plan a delta-V manoeuvre — a **debris-avoidance manoeuvre (CAM)** or a routine
**drag make-up / semi-major-axis maintenance burn** — and load it to the
spacecraft as an S11 time-tagged command sequence. Flight Dynamics (FDS)
produces the manoeuvre vector; the Propulsion Engineer verifies system health;
the Flight Director authorises execution.

EO-Sat-1's propulsion is a minimal 1U green-monopropellant module sized for
debris avoidance and limited orbit maintenance only (66.9 m/s total). There is
no propulsive deorbit: end-of-life disposal is by natural decay, which meets
the FCC 5-year rule from this orbit. Every burn spends the collision-avoidance
reserve — the dV budget is a safety budget, not a fuel gauge.

## Prerequisites
- [ ] Spacecraft in NOMINAL phase; no active FDIR safing
- [ ] Propulsion health verified: `prop.state` (0x0B00) = 0 (SAFED),
      `prop.arm_inhibit_mask` (0x0B15) shows no latched inhibit (bit 9 clear)
- [ ] Tank pressure in band: `prop.tank_pressure` (0x0B01) 6.0-24.0 bar
- [ ] Remaining envelope: `prop.dv_remaining` (0x0B0C) > planned dV + 1.0 m/s
      CAM reserve margin
- [ ] If the propulsion health flag is NOT nominal: defer the manoeuvre and
      run CTG-028 (Propulsion Anomaly) first
- [ ] Battery SoC > 55 %: `eps.bat_soc` (0x0101) — the catbed preheat costs
      4 Wh and the arm interlock needs > 45 %

## Procedure Steps

### Step 1 --- FDS Manoeuvre Design
**Evaluate:** For a conjunction warning: required along-track miss distance at
TCA, lead time, and the resulting dV (a 500 m along-track miss at >= 3 orbits
lead needs ~15 mm/s; 0.05 m/s carries 3x margin). For drag make-up: SMA decay
since the last burn (trend `prop.sma_km` 0x0B1A) and the dV to restore the
reference SMA (da = 2a*dv/v: 1.79 km per 1.0 m/s).
**Note:** Retrograde burns (out-manoeuvre reversal, over-boost trim) require a
180-degree yaw to an INERTIAL hold — coordinate with the AOCS Engineer.
**Record:** dv (m/s), direction (prograde/retrograde), burn epoch (CUC),
predicted burn duration and predicted AOS shift at the next pass.

### Step 2 --- Propulsion Health Check
**TC:** `HK_ENABLE` SID=9 (Service 3, Subtype 5) --- enable the PROP housekeeping structure
**Verify:** `prop.state` (0x0B00) = 0, `prop.tank_pressure` (0x0B01) in band,
`prop.propellant_mass` (0x0B02) consistent with the book-kept load,
`prop.dv_remaining` (0x0B0C) covers the plan, `prop.arm_inhibit_mask` (0x0B15)
expected pattern (catbed cold + no burn loaded is normal at this point).
**GO/NO-GO:** Any latch-valve fault (0x0B06/0x0B07 = 2) or pressure
out-of-band -> NO-GO, run CTG-028.

### Step 3 --- Flight Director Authorisation
**Evaluate:** Burn conflicts: imaging, dumps and desaturation are inhibited
around the manoeuvre (planner activity `orbit_manoeuvre` declares the
conflicts). Confirm power: preheat + burn inside daylight.
**GO/NO-GO:** FD authorises the manoeuvre vector and the execution pass.

### Step 4 --- S11 Time-Tagged Upload
**TC:** `SCHEDULE_TC` (Service 11, Subtype 4) --- time-tag `PROP_SET_BURN` at T-5 min
**TC:** `SCHEDULE_TC` (Service 11, Subtype 4) --- time-tag `PROP_ARM` at T-2 min
**TC:** `SCHEDULE_TC` (Service 11, Subtype 4) --- time-tag `PROP_EXECUTE` at T-0
**Note:** PROP_ARM must land within 300 s of PROP_SET_BURN (the arm window)
and after the catbed preheat completes — schedule HEATER_THRUSTER ON at
T-25 min via the same S11 sequence or execute NOM-013 manually in-pass.
**TC:** `LIST_SCHEDULE` (Service 11, Subtype 17) --- request the schedule summary
**Verify:** The summary lists the three commands at the planned CUC times.

### Step 5 --- Handover to Execution
**Note:** Execution, live monitoring, abort criteria and post-burn orbit
determination are NOM-013 (Orbit Maintenance Manoeuvre Execution).

## Deviations
- **Propulsion health flag not nominal** -> defer, run CTG-028.
- **Conjunction lead time < 1 orbit** -> a CAM is no longer effective;
  coordinate screening authority for a revised TCA solution instead of
  attempting a late burn.
- **dv_remaining below plan + reserve** -> FD decision: accept reserve
  erosion only for a genuine conjunction threat, never for routine drag
  make-up.

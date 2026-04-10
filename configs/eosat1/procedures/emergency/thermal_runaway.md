# PROC-TCS-OFF-001: Thermal Runaway Emergency

**Category:** Emergency
**Position Lead:** Power & Thermal (EPS/TCS)
**Cross-Position:** Flight Director
**Difficulty:** Advanced

## Objective
Respond to a thermal runaway condition where a heater circuit is stuck ON and the
associated thermal zone temperature is rising uncontrollably. This procedure attempts
to disable the heater through normal commands, applies manual override, and if the
heater does not respond, sheds the heater power line via EPS to stop the heating. The
temperature is then monitored to confirm the runaway has been arrested.

## Prerequisites
- [ ] Rising temperature detected on a thermal zone with heater showing ON continuously
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] Flight Director notified — this is an EMERGENCY procedure
- [ ] EPS operator available to shed power lines if needed

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| tcs.temp_battery | 0x0407 | Monitor if battery heater affected |
| tcs.temp_obc | 0x0406 | Monitor if OBC heater affected |
| tcs.temp_thruster | 0x0409 | Monitor if thruster heater affected |
| tcs.htr_battery | 0x040A | Heater status (0=off, 1=on) |
| tcs.htr_obc | 0x040B | Heater status |
| tcs.htr_thruster | 0x040D | Heater status |
| tcs.temp_panel_px | 0x0400 | Panel temperatures for context |
| tcs.temp_panel_mx | 0x0401 | Panel temperatures for context |
| eps.pl_htr_bat | 0x0115 | Power line status — battery heater |
| eps.pl_htr_obc | 0x0116 | Power line status — OBC heater |
| eps.line_current_5 | 0x011D | Battery heater line current |
| eps.line_current_6 | 0x011E | OBC heater line current |
| eps.bus_voltage | 0x0105 | Monitor bus voltage |
| eps.power_cons | 0x0106 | Monitor power consumption |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| HEATER_BATTERY | 8 | 1 | 40 | Battery heater on/off |
| HEATER_OBC | 8 | 1 | 41 | OBC heater on/off |
| HEATER_THRUSTER | 8 | 1 | 42 | Thruster heater on/off |
| HEATER_SET_SETPOINT | 8 | 1 | 44 | Modify heater thermostat setpoints |
| HEATER_AUTO_MODE | 8 | 1 | 45 | Return to autonomous thermostat |
| EPS_POWER_OFF | 8 | 1 | 14 | Shed heater power line |

## Procedure Steps

### Step 1: Detect Temperature Rise and Identify Affected Zone
**Action:** Request TCS housekeeping: `HK_REQUEST(sid=3)` (Service 3, Subtype 27)
**Verify:** Identify which temperature is rising anomalously:
- `tcs.temp_battery` (0x0407) — if rising above 40 C with `tcs.htr_battery` (0x040A) = 1
- `tcs.temp_obc` (0x0406) — if rising above 55 C with `tcs.htr_obc` (0x040B) = 1
- `tcs.temp_thruster` (0x0409) — if rising with `tcs.htr_thruster` (0x040D) = 1
**Verify:** Confirm the heater for the affected zone is showing ON status.
**Verify:** Record temperature and confirm it is above the heater OFF setpoint
(the heater should have turned off by now).
**Note:** Battery heater: OFF setpoint = 5 C, OBC heater: OFF setpoint = 10 C,
Thruster heater: OFF setpoint = 8 C. If temperature is well above these values and
heater is still ON, the thermostat has failed.
**GO/NO-GO:** Thermal runaway confirmed — proceed to command heater OFF.

### Step 2: Command Heater OFF via Normal Command
**Note:** For battery heater, verify that the EPS power line 5 (`eps.pl_htr_bat`, 0x0115) is ON.
The battery heater thermostat requires this power line to operate. If the power line is OFF,
the thermostat will not function and the heater cannot respond to commands.
**Action:** If addressing battery heater: verify `eps.pl_htr_bat` (0x0115) = 1 (ON).
If the power line is OFF, first send: `EPS_POWER_ON(line_index=5)` (func_id 13) — battery heater power line.
**Action:** Command the affected heater OFF:
- Battery heater: `HEATER_BATTERY(on=0)` (func_id 40)
- OBC heater: `HEATER_OBC(on=0)` (func_id 41)
- Thruster heater: `HEATER_THRUSTER(on=0)` (func_id 42)
**Verify:** Wait 10 s, then request TCS housekeeping: `HK_REQUEST(sid=3)`
**Verify:** Check heater status register:
- `tcs.htr_battery` (0x040A) should = 0 if battery heater commanded OFF
- `tcs.htr_obc` (0x040B) should = 0 if OBC heater commanded OFF
- `tcs.htr_thruster` (0x040D) should = 0 if thruster heater commanded OFF
**Action:** If heater status = 0 (OFF):
- Heater responded to command. Monitor temperature for 60 s to confirm it stops rising.
- Proceed to Step 5 (monitor temperature decrease).
**Action:** If heater status still = 1 (ON):
- Heater did NOT respond to OFF command. Proceed to Step 3 (manual override).
**GO/NO-GO:** If heater turned off — skip to Step 5. If still on — proceed to Step 3.

### Step 3: Command Manual Override via Setpoint Change
**Action:** Attempt to force heater off by setting an extremely high setpoint (heater
will never turn on):
- `HEATER_SET_SETPOINT(circuit=N, on_temp=100.0, off_temp=100.0)` (func_id 44)
  where N = 0 (battery), 1 (obc), or 2 (thruster)
**Verify:** Wait 10 s, then request TCS housekeeping: `HK_REQUEST(sid=3)`
**Verify:** Check heater status — should now be 0 (OFF) since temperature is below
the new absurd setpoint of 100 C.
**Action:** If heater is now OFF:
- Override worked. Proceed to Step 5 (monitor temperature decrease).
**Action:** If heater is STILL ON despite setpoint change:
- The heater relay or switch is stuck in the ON position. Software control is
  ineffective. Proceed to Step 4 (power line shed).
**GO/NO-GO:** If override worked — skip to Step 5. If still on — proceed to Step 4.

### Step 4: Shed Heater Power Line via EPS
**Action:** As a last resort, cut power to the heater by disabling its EPS power line:
- Battery heater (line 5): `EPS_POWER_OFF(line_index=5)` (func_id 14)
- OBC heater (line 6): `EPS_POWER_OFF(line_index=6)` (func_id 14)
- Thruster heater: Check which power line supplies the thruster heater and shed it.
**Verify:** Check power line status:
- `eps.pl_htr_bat` (0x0115) = 0 if battery heater line shed
- `eps.pl_htr_obc` (0x0116) = 0 if OBC heater line shed
**Verify:** Check per-line current dropped to 0:
- `eps.line_current_5` (0x011D) = 0 A (battery heater)
- `eps.line_current_6` (0x011E) = 0 A (OBC heater)
**Verify:** `eps.power_cons` (0x0106) decreases by the heater power:
- Battery heater: ~6 W
- OBC heater: ~4 W
- Thruster heater: ~8 W
**Note:** WARNING — Shedding the heater power line means the heater cannot provide
thermal protection if the zone later gets cold (e.g., during eclipse). Monitor
the temperature closely.
**GO/NO-GO:** Power line shed confirmed — proceed to temperature monitoring.

### Step 5: Monitor Temperature Decrease
**Action:** Monitor TCS telemetry at 60 s intervals: `HK_REQUEST(sid=3)` (Service 3, Subtype 27)
**Verify:** Temperature of the affected zone is no longer rising.
**Verify:** Temperature begins to decrease or stabilize:
- Battery temperature: Should stabilize and then decrease toward ambient
- OBC temperature: Should stabilize and then decrease
- Thruster temperature: Should stabilize and then decrease
**Verify:** Record temperature every 60 s for at least 10 minutes to confirm trend.
**Action:** If temperature continues to rise despite heater being OFF/power line shed:
- The heat source is NOT the heater. Investigate other possible causes (e.g., adjacent
  equipment, solar heating, internal dissipation). Escalate to engineering team.
**GO/NO-GO:** Temperature decreasing — runaway arrested. Continue monitoring.

### Step 6: Post-Event Assessment
**Action:** Once temperature has returned to a safe range:
- Battery: < 35 C
- OBC: < 50 C
- Thruster: within limits
**Action:** Assess whether the heater can be safely restored:
- If the heater responded to commands in Step 2: Return to auto mode:
  `HEATER_AUTO_MODE(circuit=N)` (func_id 45). Monitor for recurrence.
- If the heater required power line shed: Do NOT re-enable the power line without
  engineering assessment. The relay or switch is suspected stuck.
**Action:** Log the thermal runaway event:
- Affected zone, peak temperature reached
- Which intervention was required (command OFF / setpoint override / power shed)
- Duration of the runaway event
- Current thermal configuration
**Verify:** `eps.bus_voltage` (0x0105) > 28.0 V — power budget is stable
**Note:** If the heater power line was shed, plan for thermal management during the
next eclipse without that heater (may need to shed other loads to maintain temperature
or accept a cold soak on that zone).

## Verification Criteria
- [ ] Heater confirmed OFF (command, override, or power shed)
- [ ] Temperature of affected zone is decreasing
- [ ] Peak temperature did not exceed hardware damage threshold
- [ ] No secondary effects on other subsystems
- [ ] `eps.bus_voltage` (0x0105) and `eps.power_cons` (0x0106) nominal
- [ ] Anomaly report filed with full temperature timeline

## Contingency
- If temperature reaches hardware damage threshold before heater can be disabled:
  - Battery: > 50 C — risk of battery thermal runaway (chemical). Command
    `OBC_SET_MODE(mode=2)` for EMERGENCY. Shed ALL loads to stop battery discharge
    heat generation.
  - OBC: > 70 C — risk of processor damage. Consider OBC switchover to redundant unit
    per PROC-OBC-OFF-003.
  - Thruster: > design limit — risk of propellant issues if present.
- If power line shed does not stop the temperature rise: The heat source is not the
  heater. Check if the zone is sun-facing (solar heating). Check if adjacent equipment
  is dissipating unexpected heat. Consider attitude maneuver to change thermal exposure.
- If the heater is needed for eclipse survival but power line was shed: Evaluate whether
  the zone can survive eclipse without active heating. If not, consider brief
  re-enabling of the power line during eclipse only, with continuous monitoring.
  This requires Flight Director approval and continuous operator attention.

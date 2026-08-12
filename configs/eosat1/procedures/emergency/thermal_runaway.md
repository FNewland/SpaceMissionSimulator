# EMG-006: Thermal Runaway

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
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 2 (LOCKED)
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
| tcs.htr_thruster | 0x041B | Heater status |
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
| EPS_POWER_ON | 8 | 1 | 19 | Restore heater power line |
| EPS_POWER_OFF | 8 | 1 | 20 | Shed heater power line |

## Procedure Steps

**Runnability note.** This card is `runnable: {kind: markdown}` and its
machine-readable arm is the **battery** heater (EPS line 5, circuit 0) — the
zone where a runaway is genuinely dangerous, because a lithium cell above
50 C is a chemical hazard rather than an inconvenience. The OBC (line 6,
circuit 1) and thruster (circuit 2) variants are given as notes beside each
step; to run those, substitute the command arguments shown and follow the same
branch structure. Making one zone executable and naming the substitutions is
honest; parameterising a `**TC:**` line on "the affected zone" would produce a
card that looks runnable and sends nothing.

### Step 1: Detect Temperature Rise and Identify Affected Zone
**LABEL:** `STEP-1`
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27) — TCS housekeeping
**Verify:** Identify which temperature is rising anomalously:
- `tcs.temp_battery` (0x0407) — if rising above 40 C with `tcs.htr_battery` (0x040A) = 1
- `tcs.temp_obc` (0x0406) — if rising above 55 C with `tcs.htr_obc` (0x040B) = 1
- `tcs.temp_thruster` (0x0409) — if rising with `tcs.htr_thruster` (0x041B) = 1
**Verify:** `tcs.htr_battery == 1` (0x040A, timeout 70 s) — the runaway
signature: the heater is reporting ON. HK SID 3 is a 60 s structure, so the
timeout has to exceed one collection interval or the check races the downlink.
**Verify:** Record temperature and confirm it is above the heater OFF setpoint
(the heater should have turned off by now).
**Note:** Setpoints from `subsystems/tcs.yaml`: battery ON +5 C / OFF +10 C
(8 W), OBC ON +5 C / OFF +10 C (4 W), thruster catbed ON +190 C / OFF +230 C
(12 W). If temperature is well above these values
and the heater is still ON, the thermostat has failed.
**GO/NO-GO:** Thermal runaway confirmed — proceed to command the heater OFF

### Step 2: Command Heater OFF via Normal Command
**LABEL:** `STEP-2`
**Note:** The battery heater thermostat only operates while EPS power line 5
(`eps.pl_htr_bat`, 0x0115) is energised. A heater on a dark line cannot respond
to an OFF command because it is not running at all — so line 5 is energised
FIRST, unconditionally. `EPS_POWER_ON` on a line that is already on is a no-op,
which is why this can be a straight step rather than another branch.
**TC:** `EPS_POWER_ON(line_index=5)` (Service 8, Subtype 1, func_id 19) — battery heater line
**TC:** `HEATER_BATTERY(on=0)` (Service 8, Subtype 1, func_id 40) — command the heater OFF
**Note:** OBC heater: `HEATER_OBC(on=0)` (func_id 41) with `EPS_POWER_ON(line_index=6)`.
Thruster heater: `HEATER_THRUSTER(on=0)` (func_id 42).
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27) — re-read the heater status
**BRANCH:** `tcs.htr_battery == 0` -> GO `STEP-5` | NO-GO `STEP-3` (timeout 70 s)
**Note:** GO — the heater obeyed; there is nothing to override and nothing to
shed, so Steps 3 and 4 are SKIPPED. NO-GO — the heater did not respond; fall
through to the setpoint override. This is the fork that makes the card safe to
execute: run flat, Step 4 would shed the heater power line on a spacecraft
whose heater had already turned itself off.

### Step 3: Command Manual Override via Setpoint Change
**LABEL:** `STEP-3`
**Note:** Force the thermostat to a state in which it can never call for heat,
by moving both setpoints far above any temperature the zone can reach.
**TC:** `HEATER_SET_SETPOINT(circuit=0, on_temp=100.0, off_temp=100.0)` (Service 8, Subtype 1, func_id 44) — circuit 0 = battery
**Note:** OBC = circuit 1, thruster = circuit 2.
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27)
**BRANCH:** `tcs.htr_battery == 0` -> GO `STEP-5` | NO-GO `STEP-4` (timeout 70 s)
**Note:** NO-GO here means software control is ineffective: the relay or switch
is stuck closed, and only removing the power is left.

### Step 4: Shed Heater Power Line via EPS
**LABEL:** `STEP-4`
**Note:** Last resort. Cut power to the heater by de-energising its EPS line.
**TC:** `EPS_POWER_OFF(line_index=5)` (Service 8, Subtype 1, func_id 20) — battery heater line
**Note:** OBC heater is line 6. The thruster heater has no dedicated switchable
line; if the thruster zone is the affected one, this step is not available and
the escalation ends at Step 3 — record that and go to the Contingency section.
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — EPS housekeeping for the line state
**Verify:** `eps.pl_htr_bat == 0` (0x0115, timeout 10 s) — the line is shed
**Verify:** `eps.line_current_5 == 0` (0x011D, timeout 10 s) — and drawing nothing
**Verify:** `eps.power_cons` (0x0106) decreases by the heater power:
- Battery heater: 8 W
- OBC heater: 4 W
**Note:** WARNING — shedding the heater power line means the heater cannot
provide thermal protection if the zone later gets cold (e.g. during eclipse).
Monitor the temperature closely.
**Note:** A `htr_battery_stuck_on` failure leaves `tcs.htr_battery` (0x040A)
reading 1 even after the line is shed, because the failure model asserts the
status flag after the power gate. That is why this step verifies the EPS line
and its current rather than the heater status word: 0x0115 and 0x011D are the
measurements that cannot lie about whether current is flowing.

### Step 5: Monitor Temperature Decrease
**LABEL:** `STEP-5`
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27) — monitor at 60 s intervals
**Verify:** Temperature of the affected zone is no longer rising.
**Verify:** Temperature begins to decrease or stabilize:
- Battery temperature: Should stabilize and then decrease toward ambient
- OBC temperature: Should stabilize and then decrease
- Thruster temperature: Should stabilize and then decrease
**Verify:** Record temperature every 60 s for at least 10 minutes to confirm trend.
**Note:** If temperature continues to rise despite the heater being OFF or its
power line shed, the heat source is NOT the heater. Investigate other causes
(adjacent equipment, solar heating, internal dissipation) and escalate to the
engineering team — see the Contingency section.
**GO/NO-GO:** Temperature decreasing — runaway arrested, continue monitoring

### Step 6: Post-Event Assessment
**LABEL:** `STEP-6`
**Note:** Once temperature has returned to a safe range — battery < 35 C,
OBC < 50 C, thruster within limits — assess whether the heater can be restored.
**Note:** If the heater responded in Step 2, return it to autonomous control
with `HEATER_AUTO_MODE(circuit=0)` (func_id 45) and monitor for recurrence.
This is deliberately NOT a `**TC:**` step: restoring a thermostat that has just
misbehaved is a Flight-Director decision, and an executable card must not make
it automatically. Send it by hand once the decision is taken.
**Note:** If the heater required a power line shed, do NOT re-enable the line
without engineering assessment. The relay or switch is suspected stuck.
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — final power check
**Verify:** `eps.bus_voltage > 28.0` (0x0105, timeout 10 s) — power budget is stable
**Note:** Log the thermal runaway event: affected zone, peak temperature
reached, which intervention was required (command OFF / setpoint override /
power shed), duration of the runaway, and the current thermal configuration.
**Note:** If the heater power line was shed, plan for thermal management during
the next eclipse without that heater (may need to shed other loads to maintain
temperature, or accept a cold soak on that zone).

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
    per CTG-011.
  - Thruster: > design limit — risk of propellant issues if present.
- If power line shed does not stop the temperature rise: The heat source is not the
  heater. Check if the zone is sun-facing (solar heating). Check if adjacent equipment
  is dissipating unexpected heat. Consider attitude maneuver to change thermal exposure.
- If the heater is needed for eclipse survival but power line was shed: Evaluate whether
  the zone can survive eclipse without active heating. If not, consider brief
  re-enabling of the power line during eclipse only, with continuous monitoring.
  This requires Flight Director approval and continuous operator attention.

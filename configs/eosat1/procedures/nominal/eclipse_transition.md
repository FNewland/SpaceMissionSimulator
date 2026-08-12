# NOM-010: Eclipse Transition

**Category:** Nominal
**Position Lead:** Power & Thermal (EPS/TCS)
**Cross-Position:** Flight Dynamics (AOCS), TT&C
**Difficulty:** Intermediate

## Objective
Coordinate a controlled spacecraft transition through an orbital eclipse period, ensuring
battery state-of-charge is sufficient, thermal heater setpoints are configured for the
cold phase, AOCS eclipse propagation mode is engaged, and bus voltage remains stable
throughout. This procedure also covers verification of power generation resumption after
eclipse exit.

## Prerequisites
- [ ] Spacecraft in nominal operations — `obdh.mode` (0x0300) = 0 (NOMINAL)
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 2 (LOCKED)
- [ ] Current orbit prediction available with eclipse entry/exit times
- [ ] No pending anomalies or active contingency procedures
- [ ] Payload imaging sessions complete or paused before eclipse entry

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| eps.bat_soc | 0x0101 | > 60% prior to eclipse entry |
| eps.bus_voltage | 0x0105 | > 28.0 V |
| eps.eclipse_flag | 0x0108 | 0 (sunlit) before entry, 1 during eclipse |
| eps.power_gen | 0x0107 | > 0 W in sunlight, ~0 W during eclipse |
| eps.bat_current | 0x0109 | Negative (discharging) during eclipse |
| tcs.htr_battery | 0x040A | Heater status |
| tcs.htr_obc | 0x040B | Heater status |
| tcs.temp_battery | 0x0407 | Within 0 to 40 C |
| tcs.temp_obc | 0x0406 | Within 5 to 55 C |
| aocs.mode | 0x020F | 8 (ECLIPSE_PROPAGATE) during eclipse |
| aocs.att_error | 0x0217 | < 1.0 deg |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| HEATER_SET_SETPOINT | 8 | 1 | 44 | Modify heater thermostat setpoints for eclipse |
| HEATER_AUTO_MODE | 8 | 1 | 45 | Return heater to autonomous thermostat control |
| AOCS_SET_MODE | 8 | 1 | 0 | Set AOCS to eclipse propagation mode |
| FPA_COOLER | 8 | 1 | 43 | Disable FPA cooler to save power |

## Procedure Steps

### Step 1: Verify Battery State of Charge
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — Request EPS housekeeping
**Verify:** `eps.bat_soc` (0x0101) > 60%
**Verify:** `eps.bus_voltage` (0x0105) > 28.0 V
**Verify:** `eps.bat_temp` (0x0102) within 0 to 40 C
**Verify:** `eps.bat_current` (0x0109) — record baseline value
**GO/NO-GO:** If SoC <= 60%, HOLD. Consider deferring non-essential loads or waiting
for additional charging time. If SoC < 50%, do NOT proceed — execute load reduction
and reassess after one additional sunlit pass.

### Step 2: Verify and Adjust Heater Setpoints for Eclipse
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27) — Request TCS housekeeping
**Verify:** `tcs.htr_battery` (0x040A) — record current state
**Verify:** `tcs.htr_obc` (0x040B) — record current state
**Verify:** `tcs.temp_battery` (0x0407) > 5 C
**Verify:** `tcs.temp_obc` (0x0406) > 10 C
**Note:** If battery temperature margin is tight (< 8 C), raise battery heater
**TC:** `HEATER_SET_SETPOINT(circuit=0, on_temp=3.0, off_temp=8.0)` (Service 8, Subtype 1, func_id 44) — setpoint
**Note:** If OBC temperature margin is tight (< 12 C), raise OBC heater
**TC:** `HEATER_SET_SETPOINT(circuit=1, on_temp=8.0, off_temp=13.0)` (Service 8, Subtype 1, func_id 44) — setpoint
**GO/NO-GO:** Heater setpoints configured for eclipse cold phase — proceed.

### Step 3: Disable FPA Cooler to Conserve Power
**Note:** If payload is in STANDBY or OFF, disable FPA cooler to reduce eclipse
**TC:** `FPA_COOLER(on=0)` (Service 8, Subtype 1, func_id 43) — power draw
**Verify:** `tcs.cooler_fpa` (0x040C) = 0 (OFF) within 5 s
**Verify:** `eps.power_cons` (0x0106) decreases by ~15 W
**Note:** FPA cooler will be re-enabled after eclipse exit when power budget is positive.
**GO/NO-GO:** Cooler disabled and power consumption reduced — proceed.

### Step 4: Verify AOCS Eclipse Mode Transition
**TC:** `AOCS_SET_MODE(mode=8)` (Service 8, Subtype 1, func_id 0) — Command AOCS to eclipse propagation mode
**Verify:** `aocs.mode` (0x020F) = 8 (ECLIPSE_PROPAGATE) within 15 s
**Verify:** `aocs.att_error` (0x0217) < 1.0 deg
**Note:** In eclipse propagation mode, AOCS uses gyro-only propagation without star
tracker updates (star tracker may lose tracking in Earth shadow).
**GO/NO-GO:** AOCS confirmed in eclipse propagation mode — proceed.

### Step 5: Monitor Bus Voltage During Eclipse Entry
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — Monitor EPS telemetry at 30 s intervals
**Verify:** `eps.eclipse_flag` (0x0108) = 1 (eclipse confirmed)
**Verify:** `eps.power_gen` (0x0107) drops to ~0 W (solar arrays in shadow)
**Verify:** `eps.bus_voltage` (0x0105) remains > 27.0 V
**Verify:** `eps.bat_soc` (0x0101) — monitor discharge rate, record SoC every 5 min
**Verify:** `eps.bat_current` (0x0109) is negative (battery discharging)
**Action:** If bus voltage drops below 27.0 V, proceed to load shedding per
CTG-001 (Undervoltage Load Shedding).
**GO/NO-GO:** Bus voltage stable above 27.0 V — continue monitoring.

### Step 6: Monitor Thermal Conditions During Eclipse
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27) — Monitor TCS telemetry at 60 s intervals
**Verify:** `tcs.temp_battery` (0x0407) remains > 0 C
**Verify:** `tcs.temp_obc` (0x0406) remains > 5 C
**Verify:** `tcs.htr_battery` (0x040A) — heater cycling as expected
**Verify:** `tcs.htr_obc` (0x040B) — heater cycling as expected
**Action:** If any temperature approaches lower limit, verify heater is ON. If heater
is not responding, follow EMG-006 (Thermal Runaway Emergency) for heater
diagnostics.
**GO/NO-GO:** All temperatures within limits — continue monitoring.

### Step 7: Verify Power Generation Resumes After Eclipse Exit
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — Monitor EPS telemetry as spacecraft exits eclipse
**Verify:** `eps.eclipse_flag` (0x0108) = 0 (sunlit)
**Verify:** `eps.power_gen` (0x0107) > 25 W within 60 s of eclipse exit
**Verify:** `eps.sa_a_current` (0x0103) > 0.3 A
**Verify:** `eps.sa_b_current` (0x0104) > 0.3 A
**Verify:** `eps.bus_voltage` (0x0105) recovering above 28.0 V
**Verify:** `eps.bat_soc` (0x0101) — record post-eclipse SoC and confirm charging trend
**GO/NO-GO:** Power generation confirmed and battery charging — proceed to restoration.

### Step 8: Restore Nominal Configuration
**TC:** `AOCS_SET_MODE(mode=4)` (Service 8, Subtype 1, func_id 0) — Command AOCS back to nominal nadir pointing
**Verify:** `aocs.mode` (0x020F) = 4 (NOMINAL_NADIR) within 30 s
**Verify:** `aocs.att_error` (0x0217) < 0.1 deg within 300 s (Phase C: gyro-only eclipse drift must be re-estimated and the PD re-converges at its real settling time)
**Note:** Only if imaging is planned this orbit — the cooler is a 15 W load.
**TC:** `FPA_COOLER(on=1)` (Service 8, Subtype 1, func_id 43) — re-enable the FPA cooler.
Since Phase 4 this path also energises EPS line 4 (`eps.pl_fpa_cooler`, 0x0114);
it used not to, which is why the FPA never reached its setpoint.
**Verify:** `tcs.cooler_fpa` (0x040C) = 1 (ACTIVE) within 5 s
**Note:** Restore heater setpoints to nominal if they were modified in Step 2.
**TC:** `HEATER_AUTO_MODE(circuit=0)` (Service 8, Subtype 1, func_id 45) — battery circuit back to thermostat
**TC:** `HEATER_AUTO_MODE(circuit=1)` (Service 8, Subtype 1, func_id 45) — OBC circuit back to thermostat
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — EPS housekeeping
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — AOCS housekeeping
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27) — TCS housekeeping
**Verify:** All subsystems nominal in the full HK sweep.

## Verification Criteria
- [ ] Battery SoC remained above 40% throughout eclipse (record minimum SoC)
- [ ] Bus voltage remained above 27.0 V throughout eclipse
- [ ] All temperatures remained within operational limits
- [ ] AOCS maintained attitude within 1.0 deg during eclipse propagation
- [ ] Power generation resumed within 60 s of eclipse exit
- [ ] All subsystems returned to nominal configuration post-eclipse

## Contingency
- If bus voltage drops below 27.0 V during eclipse: Execute CTG-001
  (Undervoltage Load Shedding) — shed loads in priority order.
- If battery SoC drops below 30% during eclipse: Shed payload and cooler loads
  immediately. If SoC drops below 20%, execute CTG-001.
- If heater fails to activate and temperature drops toward lower limit: Command manual
  heater ON. If no response, consider shedding heater power line and accepting
  temperature exceedance until eclipse exit.
- If AOCS does not transition to eclipse propagation mode: Maintain current mode.
  Monitor attitude error closely. If attitude error exceeds 5 deg, execute
  CTG-008 or safe mode entry.
- If power generation does not resume after eclipse exit: Verify solar array orientation.
  Check `eps.sa_a_current` and `eps.sa_b_current`. If both near zero, suspect array or
  AOCS attitude issue. Execute AOCS sun-pointing mode recovery.

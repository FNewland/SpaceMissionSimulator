# PROC-EPS-OFF-002: Undervoltage Load Shedding

**Category:** Contingency
**Position Lead:** Power & Thermal (EPS/TCS)
**Cross-Position:** Flight Director, all positions
**Difficulty:** Advanced

## Objective
Respond to an undervoltage condition on the main power bus by performing manual load
shedding in a controlled priority order. Loads are shed starting with the lowest
priority (payload) and progressing through the FPA cooler, TX transmitter, and reaction
wheels to arrest bus voltage decline and protect critical spacecraft functions. Recovery
is monitored until battery state-of-charge and bus voltage return to safe levels.

## Prerequisites
- [ ] Undervoltage flag detected — `eps.uv_flag` (0x010E) = 1 or bus voltage observed < 27.0 V
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] Flight Director notified and authorizes manual load shedding
- [ ] Current orbit prediction available (eclipse entry/exit times)

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| eps.uv_flag | 0x010E | 1 (undervoltage detected) |
| eps.bus_voltage | 0x0105 | Monitoring — expected < 27.0 V at detection |
| eps.bat_soc | 0x0101 | Monitoring — record trend |
| eps.bat_voltage | 0x0100 | Monitoring — record value |
| eps.bat_current | 0x0109 | Negative (discharging) |
| eps.power_cons | 0x0106 | Monitoring — record before/after each shed |
| eps.power_gen | 0x0107 | Record — may be 0 if in eclipse |
| eps.eclipse_flag | 0x0108 | Current sun/eclipse state |
| payload.mode | 0x0600 | Current payload mode |
| tcs.cooler_fpa | 0x040C | Current cooler state |
| ttc.pa_on | 0x0516 | Current PA state |
| aocs.mode | 0x020F | Current AOCS mode |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| PAYLOAD_SET_MODE | 8 | 1 | 20 | Set payload mode to OFF |
| FPA_COOLER | 8 | 1 | 33 | Disable FPA cooler |
| TTC_PA_OFF | 8 | 1 | 54 | Disable power amplifier (TX) |
| EPS_POWER_OFF | 8 | 1 | 14 | Switch power line OFF |
| OBC_SET_MODE | 8 | 1 | 40 | Set OBC mode (safe/emergency) |

## Procedure Steps

### Step 1: Detect and Confirm Undervoltage Condition
**Action:** Request EPS housekeeping: `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.uv_flag` (0x010E) = 1
**Verify:** `eps.bus_voltage` (0x0105) — record current value (expected < 27.0 V)
**Verify:** `eps.bat_soc` (0x0101) — record current value
**Verify:** `eps.bat_voltage` (0x0100) — record current value
**Verify:** `eps.power_cons` (0x0106) — record total power consumption
**Verify:** `eps.power_gen` (0x0107) — record power generation
**Verify:** `eps.eclipse_flag` (0x0108) — determine if in eclipse
**Action:** Announce "UNDERVOLTAGE — initiating manual load shed" on the operations loop.
**GO/NO-GO:** Undervoltage confirmed. Flight Director authorizes load shedding — proceed.

### Step 2: Shed Load Priority 1 — Payload OFF
**Action:** Command payload to OFF: `PAYLOAD_SET_MODE(mode=0)` (func_id 20)
**Verify:** `payload.mode` (0x0600) = 0 (OFF) within 10 s
**Verify:** `eps.power_cons` (0x0106) decreases (expected -45 W if imaging, -8 W if standby)
**Action:** Wait 30 s and re-check bus voltage: `HK_REQUEST(sid=1)`
**Verify:** `eps.bus_voltage` (0x0105) — record new value
**GO/NO-GO:** If bus voltage stabilizing and > 26.5 V, HOLD at this level and monitor.
If still declining or < 26.5 V, proceed to next shed level.

### Step 3: Shed Load Priority 2 — FPA Cooler OFF
**Action:** Disable FPA cooler: `FPA_COOLER(on=0)` (func_id 33)
**Verify:** `tcs.cooler_fpa` (0x040C) = 0 (OFF) within 5 s
**Verify:** `eps.power_cons` (0x0106) decreases by ~15 W
**Action:** Wait 30 s and re-check bus voltage: `HK_REQUEST(sid=1)`
**Verify:** `eps.bus_voltage` (0x0105) — record new value
**GO/NO-GO:** If bus voltage stabilizing and > 26.0 V, HOLD and monitor. If still
declining or < 26.0 V, proceed to next shed level.

### Step 4: Shed Load Priority 3 — TX Transmitter OFF
**Action:** Disable power amplifier: `TTC_PA_OFF` (func_id 54)
**Verify:** `ttc.pa_on` (0x0516) = 0 within 5 s
**Verify:** `eps.power_cons` (0x0106) decreases by ~20 W
**Note:** WARNING — This will terminate downlink capability. Uplink (RX) remains active.
The spacecraft can still receive commands but cannot send telemetry.
**Action:** Wait 30 s and re-check bus voltage: `HK_REQUEST(sid=1)`
**Verify:** `eps.bus_voltage` (0x0105) — record new value
**GO/NO-GO:** If bus voltage stabilizing and > 25.5 V, HOLD and monitor. If still
declining, proceed to next shed level.

### Step 5: Shed Load Priority 4 — Reaction Wheels OFF
**Action:** Power off AOCS wheels: `EPS_POWER_OFF(line_index=7)` (func_id 14)
**Verify:** `eps.pl_aocs_wheels` (0x0117) = 0 (OFF) within 5 s
**Note:** WARNING — Spacecraft will lose active attitude control. Residual momentum
will cause slow drift. Magnetic detumbling (if available) or gravity gradient will
provide passive stabilization only.
**Action:** Wait 30 s and re-check: `HK_REQUEST(sid=1)`
**Verify:** `eps.bus_voltage` (0x0105) — record new value
**GO/NO-GO:** If bus voltage is now stabilizing, monitor for recovery. If bus voltage
continues to decline below 25.0 V, command `OBC_SET_MODE(mode=2)` for EMERGENCY.

### Step 6: Monitor Recovery
**Action:** Monitor EPS telemetry at 60 s intervals: `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) — trending upward
**Verify:** `eps.bat_soc` (0x0101) — trending upward (especially in sunlit phase)
**Verify:** `eps.power_gen` (0x0107) > 0 W when in sunlight
**Verify:** `eps.bat_current` (0x0109) — positive (charging) in sunlight
**Action:** Continue monitoring until:
- `eps.bus_voltage` > 28.0 V
- `eps.bat_soc` > 40%
**Note:** Recovery may take multiple orbits depending on depth of discharge and
available solar array power.
**GO/NO-GO:** Bus voltage > 28.0 V and SoC > 40% — proceed to restore loads.

### Step 7: Restore Loads in Reverse Priority Order
**Action:** Re-enable loads one at a time, waiting 5 min between each to verify stability:
1. AOCS wheels: `EPS_POWER_ON(line_index=7)` (func_id 13) — verify `aocs.mode` (0x020F)
   returns to controllable mode. May need `AOCS_SET_MODE(mode=3)` for COARSE_SUN first.
2. TX transmitter: `TTC_PA_ON` (func_id 53) — verify `ttc.pa_on` (0x0516) = 1
3. FPA cooler: `FPA_COOLER(on=1)` (func_id 33) — verify `tcs.cooler_fpa` (0x040C) = 1
4. Payload to STANDBY: `PAYLOAD_SET_MODE(mode=1)` (func_id 20) — verify `payload.mode` = 1
**Verify:** After each load addition, confirm `eps.bus_voltage` (0x0105) remains > 27.5 V
and `eps.bat_soc` (0x0101) remains > 35%.
**GO/NO-GO:** All loads restored and power budget positive — procedure complete.

## Verification Criteria
- [ ] `eps.uv_flag` (0x010E) = 0 (cleared)
- [ ] `eps.bus_voltage` (0x0105) > 28.0 V
- [ ] `eps.bat_soc` (0x0101) > 40%
- [ ] All subsystems restored to nominal operating modes
- [ ] Power budget is positive (generation > consumption) with margin
- [ ] No repeat undervoltage events for at least 2 orbits

## Contingency
- If bus voltage drops below 25.0 V after all loads are shed: Command
  `OBC_SET_MODE(mode=2)` for EMERGENCY mode. Spacecraft should enter minimum power
  configuration autonomously.
- If bus voltage does not recover after eclipse exit: Suspect solar array failure.
  Check `eps.sa_a_current` (0x0103) and `eps.sa_b_current` (0x0104). If both near
  zero, escalate to solar array degradation procedure.
- If battery SoC drops below 10%: Risk of battery deep discharge and permanent damage.
  Command EMERGENCY mode immediately. All non-essential functions will be disabled
  by onboard FDIR.
- If loads cannot be restored without triggering undervoltage again: Reduce the
  operational power budget. Operate with reduced payload duty cycle or without FPA
  cooler. Consult with mission planning team.
- If undervoltage recurs within 2 orbits of restoration: Suspect persistent power
  deficit. Re-evaluate power budget with flight dynamics (array degradation,
  eclipse duration, attitude bias).

## Recovery Path — Re-Powering Shed Loads
After this procedure has stabilised the bus and the root cause is understood, **any subsystems whose power lines were de-energised during load shed must be brought back up in the controlled order defined by LEOP-007 (Sequential Power-On)**. Do **not** simply re-enable lines ad-hoc: LEOP-007 enforces the correct subsystem→line→mode sequencing, the inrush spacing, the thermal pre-conditioning of the OBC/battery heaters, and the AOCS/payload set_mode exemptions to the two-stage TC power gate. Cross-reference: `configs/eosat1/procedures/leop/sequential_power_on.md` (PROC-LEOP-007).

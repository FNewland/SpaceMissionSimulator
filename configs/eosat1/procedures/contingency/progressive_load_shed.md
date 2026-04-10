# CON-019: Progressive Load Shedding During Extended Eclipse
**Subsystem:** EPS
**Phase:** CONTINGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Execute a controlled, progressive load shedding sequence during an extended eclipse
when battery state-of-charge (SoC) is declining and the spacecraft cannot generate
power. Loads are shed in strict priority order at defined SoC thresholds to maximise
battery life and prevent deep discharge damage. Once eclipse ends and battery begins
recovering, loads are restored in reverse priority order with stability verification
at each step.

This procedure differs from CON-001 (Undervoltage Load Shed) in that it is triggered
by declining SoC rather than a voltage threshold, and includes explicit GO/NO-GO
checkpoints at each shedding level to allow the Flight Director to make informed
decisions about the pace and depth of load shedding.

## Prerequisites
- [ ] Battery SoC declining — `eps.bat_soc` (0x0101) trending downward
- [ ] Eclipse phase confirmed — `eps.eclipse_flag` (0x0108) = 1
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] Flight Director notified and aware of extended eclipse scenario
- [ ] Current orbit prediction available — eclipse exit time known
- [ ] Power budget (generation vs consumption) calculated and negative

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| eps.bat_soc | 0x0101 | Monitoring — declining toward thresholds |
| eps.bus_voltage | 0x0105 | Monitoring — expected to decline with SoC |
| eps.bat_voltage | 0x0100 | Monitoring — record trend |
| eps.bat_current | 0x0109 | Negative (discharging) during eclipse |
| eps.power_cons | 0x0106 | Record before/after each shed step |
| eps.power_gen | 0x0107 | 0 W during eclipse |
| eps.eclipse_flag | 0x0108 | 1 (eclipse) |
| payload.mode | 0x0600 | Current payload mode |
| tcs.cooler_fpa | 0x040C | Current FPA cooler state |
| ttc.pa_on | 0x0516 | Current PA state |
| aocs.mode | 0x020F | Current AOCS mode |
| eps.uv_flag | 0x010E | Monitor — may trigger during deep discharge |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| PAYLOAD_SET_MODE | 8 | 1 | 20 | Set payload mode to OFF |
| FPA_COOLER | 8 | 1 | 33 | Disable FPA cooler |
| TTC_PA_OFF | 8 | 1 | 54 | Disable power amplifier (TX) |
| EPS_POWER_OFF | 8 | 1 | 14 | Switch power line OFF |
| EPS_POWER_ON | 8 | 1 | 13 | Switch power line ON |
| TTC_PA_ON | 8 | 1 | 53 | Enable power amplifier (TX) |
| AOCS_SET_MODE | 8 | 1 | 0 | Set AOCS mode |
| OBC_SET_MODE | 8 | 1 | 40 | Set OBC mode (safe/emergency) |

## Procedure Steps

### Step 1: Confirm Extended Eclipse and Establish Baseline
**Action:** Request EPS housekeeping: `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.eclipse_flag` (0x0108) = 1 — spacecraft is in eclipse
**Verify:** `eps.bat_soc` (0x0101) — record current value (expected ~70% at start)
**Verify:** `eps.bus_voltage` (0x0105) — record baseline
**Verify:** `eps.bat_current` (0x0109) — confirm negative (discharging)
**Verify:** `eps.power_cons` (0x0106) — record total consumption
**Verify:** `eps.power_gen` (0x0107) = 0 W — no solar generation
**Action:** Calculate discharge rate: power_cons / bat_capacity to estimate time to
each threshold.
**Action:** Obtain predicted eclipse exit time from flight dynamics.
**Action:** Announce "EXTENDED ECLIPSE — battery declining, initiating load shed
monitoring" on the operations loop.
**GO/NO-GO:** Eclipse confirmed, SoC declining, discharge rate calculated — proceed
to threshold monitoring.

### Step 2: SoC < 50% — Shed Load Priority 1 (Payload OFF)
**Action:** Monitor `eps.bat_soc` (0x0101) at 60 s intervals via `HK_REQUEST(sid=1)`
**Trigger:** When `eps.bat_soc` < 50%:
**Action:** Announce "SoC below 50% — shedding payload" on the operations loop.
**Action:** Command payload to OFF: `PAYLOAD_SET_MODE(mode=0)` (func_id 20)
**Verify:** `payload.mode` (0x0600) = 0 (OFF) within 10 s
**Verify:** `eps.power_cons` (0x0106) decreases (expected -45 W if imaging, -8 W if standby)
**Action:** Wait 30 s and re-check: `HK_REQUEST(sid=1)`
**Verify:** `eps.bat_soc` (0x0101) — record new value
**Verify:** `eps.bus_voltage` (0x0105) — record new value
**Action:** Recalculate discharge rate with reduced consumption.
**GO/NO-GO:** Payload shed. If discharge rate is sustainable until predicted eclipse
exit, HOLD at this level and monitor. If SoC will reach 35% before eclipse exit,
prepare for next shed level.

### Step 3: SoC < 35% — Shed Load Priority 2 (FPA Cooler OFF)
**Trigger:** When `eps.bat_soc` (0x0101) < 35%:
**Action:** Announce "SoC below 35% — shedding FPA cooler" on the operations loop.
**Action:** Disable FPA cooler: `FPA_COOLER(on=0)` (func_id 33)
**Verify:** `tcs.cooler_fpa` (0x040C) = 0 (OFF) within 5 s
**Verify:** `eps.power_cons` (0x0106) decreases by ~15 W
**Note:** FPA detector temperature will begin to rise. Record current FPA temperature
for post-recovery assessment. Cooler restart will require gradual cool-down.
**Action:** Wait 30 s and re-check: `HK_REQUEST(sid=1)`
**Verify:** `eps.bat_soc` (0x0101) — record new value
**Verify:** `eps.bus_voltage` (0x0105) — record new value
**Action:** Recalculate discharge rate.
**GO/NO-GO:** FPA cooler shed. If discharge rate sustainable until eclipse exit,
HOLD and monitor. If SoC will reach 25% before eclipse exit, prepare for next level.

### Step 4: SoC < 25% — Shed Load Priority 3 (TTC Transmitter OFF)
**Trigger:** When `eps.bat_soc` (0x0101) < 25%:
**Action:** Announce "SoC below 25% — shedding TTC transmitter. DOWNLINK WILL BE
LOST. Spacecraft will receive commands only." on the operations loop.
**Action:** Disable power amplifier: `TTC_PA_OFF` (func_id 54)
**Verify:** `ttc.pa_on` (0x0516) = 0 within 5 s
**Verify:** `eps.power_cons` (0x0106) decreases by ~20 W
**Note:** WARNING — Telemetry downlink is now OFF. The spacecraft can still receive
commands (RX is on unswitchable power) but cannot send telemetry. All subsequent
verification must rely on command acceptance (service 1 verification reports will
be queued but not transmitted).
**Action:** Wait 30 s and send `HK_REQUEST(sid=1)` — response will be queued onboard.
**GO/NO-GO:** TX shed. Bus voltage should stabilise. If SoC continues to decline
below 15%, proceed to emergency level. Flight Director must authorise next step.

### Step 5: SoC < 15% — Shed Load Priority 4 (AOCS Wheels OFF) — EMERGENCY
**Trigger:** When `eps.bat_soc` (0x0101) < 15% (estimated from pre-TX-shed rate):
**Action:** Announce "SoC below 15% — EMERGENCY — shedding AOCS reaction wheels.
Spacecraft will LOSE active attitude control." on the operations loop.
**Action:** Power off AOCS wheels: `EPS_POWER_OFF(line_index=7)` (func_id 14)
**Note:** WARNING — The spacecraft will lose active three-axis attitude control.
Residual angular momentum will cause slow tumbling. Passive magnetic detumbling
(if available) provides only coarse stabilisation. Solar arrays may not be optimally
pointed at eclipse exit, delaying power recovery.
**Action:** If available, command `AOCS_SET_MODE(mode=1)` (DETUMBLE) to engage
magnetorquer-only stabilisation before wheels power down.
**GO/NO-GO:** All non-essential loads shed. Monitor bus voltage for stabilisation.
If bus voltage drops below 25.0 V despite all loads shed, command
`OBC_SET_MODE(mode=2)` for EMERGENCY mode as last resort.

### Step 6: Monitor for Eclipse Exit and Battery Recovery
**Action:** Monitor `eps.eclipse_flag` (0x0108) — wait for transition to 0 (sunlit).
**Note:** If TX is off, operator cannot receive telemetry. Use predicted eclipse exit
time from flight dynamics. After predicted exit + 60 s, attempt to re-enable TX:
**Action:** (If TX was shed) Re-enable power amplifier: `TTC_PA_ON` (func_id 53)
**Verify:** `ttc.pa_on` (0x0516) = 1 within 10 s (if telemetry resumes)
**Action:** Request EPS housekeeping: `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.eclipse_flag` (0x0108) = 0 — sunlit confirmed
**Verify:** `eps.power_gen` (0x0107) > 0 W — solar generation active
**Verify:** `eps.bat_current` (0x0109) — positive (charging)
**Verify:** `eps.bat_soc` (0x0101) — record and begin tracking recovery trend
**Action:** Monitor at 60 s intervals until `eps.bat_soc` > 25%.
**GO/NO-GO:** Eclipse exited and battery charging confirmed — proceed to restoration.

### Step 7: Restore Loads in Reverse Priority Order
**Action:** Restore loads one at a time, waiting at least 5 minutes between each
to verify power budget stability. Only restore when SoC is above the shed
threshold + 10% margin.

**Step 7a — Restore AOCS Wheels (if shed) — when SoC > 25%:**
**Action:** `EPS_POWER_ON(line_index=7)` (func_id 13)
**Action:** Command `AOCS_SET_MODE(mode=2)` (COARSE_SUN) to begin attitude recovery.
**Verify:** `aocs.mode` (0x020F) responds within 15 s
**Verify:** Body rates `aocs.rate_roll` (0x0204), `aocs.rate_pitch` (0x0205),
`aocs.rate_yaw` (0x0206) — decreasing toward zero
**Verify:** `eps.bus_voltage` (0x0105) remains > 26.0 V after wheel spin-up
**Action:** Wait 5 minutes and confirm SoC still trending upward.
**GO/NO-GO:** AOCS wheels restored and attitude recovering — proceed.

**Step 7b — Restore TTC TX (if shed) — when SoC > 35%:**
**Action:** `TTC_PA_ON` (func_id 53)
**Verify:** `ttc.pa_on` (0x0516) = 1 within 5 s
**Verify:** `eps.bus_voltage` (0x0105) remains > 26.5 V
**Action:** Wait 5 minutes and confirm SoC still trending upward.
**GO/NO-GO:** TX restored and telemetry flowing — proceed.

**Step 7c — Restore FPA Cooler — when SoC > 45%:**
**Action:** `FPA_COOLER(on=1)` (func_id 33)
**Verify:** `tcs.cooler_fpa` (0x040C) = 1 within 5 s
**Note:** FPA cool-down from ambient to operating temperature takes approximately
20 minutes. Payload imaging should not resume until FPA reaches operating temperature.
**Verify:** `eps.bus_voltage` (0x0105) remains > 27.0 V
**Action:** Wait 5 minutes and confirm SoC still trending upward.
**GO/NO-GO:** FPA cooler restored — proceed.

**Step 7d — Restore Payload to STANDBY — when SoC > 60%:**
**Action:** `PAYLOAD_SET_MODE(mode=1)` (func_id 20)
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10 s
**Verify:** `eps.bus_voltage` (0x0105) remains > 27.5 V
**Verify:** `eps.bat_soc` (0x0101) remains > 55%
**GO/NO-GO:** All loads restored. Power budget is positive — procedure complete.

### Step 8: Post-Recovery Verification
**Action:** Request full housekeeping: `HK_REQUEST(sid=1)` and `HK_REQUEST(sid=2)`
**Verify:** `eps.bus_voltage` (0x0105) > 28.0 V
**Verify:** `eps.bat_soc` (0x0101) > 55% and trending upward
**Verify:** `eps.power_gen` (0x0107) > `eps.power_cons` (0x0106) — positive margin
**Verify:** `aocs.mode` (0x020F) = nominal mode (3 = NADIR_POINT if restored)
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY)
**Verify:** All subsystem temperatures within nominal ranges
**GO/NO-GO:** All systems restored and nominal — procedure complete.

## Verification Criteria
- [ ] All shed/restore decisions documented with SoC and bus voltage at each step
- [ ] `eps.uv_flag` (0x010E) = 0 (cleared)
- [ ] `eps.bus_voltage` (0x0105) > 28.0 V
- [ ] `eps.bat_soc` (0x0101) > 55% and trending upward
- [ ] All subsystems restored to nominal operating modes
- [ ] Power budget is positive (generation > consumption) with at least 5 W margin
- [ ] No repeat undervoltage events for at least 2 orbits after restoration

## Off-Nominal Handling
- If bus voltage drops below 25.0 V after all loads are shed: Command
  `OBC_SET_MODE(mode=2)` for EMERGENCY mode. The spacecraft will enter minimum
  power configuration autonomously.
- If SoC drops below 10%: Risk of battery deep discharge and permanent cell damage.
  Command EMERGENCY mode immediately. Notify mission director of potential
  battery degradation.
- If eclipse duration exceeds prediction by more than 10 minutes: Re-evaluate
  orbit parameters with flight dynamics. Possible unplanned eclipse from debris
  or manoeuvre error.
- If power does not recover after eclipse exit: Suspect solar array failure. Check
  per-panel currents `eps.sa_px_current` (0x012B) through `eps.sa_mz_current`
  (0x0130). If all near zero, escalate to CON-008 (Solar Array Degradation Response).
- If loads cannot be restored without SoC declining again: The spacecraft may be
  in a persistent negative power budget. Reduce operational duty cycle, disable
  payload imaging, and operate in power-conservation mode. Coordinate with mission
  planning for revised power profile.
- If AOCS cannot recover attitude after wheel restoration: Command
  `AOCS_SET_MODE(mode=1)` (DETUMBLE) first, then transition to COARSE_SUN once
  rates are below 0.5 deg/s. Fine pointing may not be achievable until battery
  is fully recovered.

## Recovery Path — Re-Powering Shed Loads
After this procedure has stabilised the bus and the root cause is understood, **any subsystems whose power lines were de-energised during load shed must be brought back up in the controlled order defined by LEOP-007 (Sequential Power-On)**. Do **not** simply re-enable lines ad-hoc: LEOP-007 enforces the correct subsystem→line→mode sequencing, the inrush spacing, the thermal pre-conditioning of the OBC/battery heaters, and the AOCS/payload set_mode exemptions to the two-stage TC power gate. Cross-reference: `configs/eosat1/procedures/leop/sequential_power_on.md` (PROC-LEOP-007).

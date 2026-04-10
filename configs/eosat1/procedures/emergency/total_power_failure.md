# PROC-EMG-003: Critical Power Emergency
**Subsystem:** EPS
**Phase:** EMERGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Emergency procedure for EOSAT-1 when battery state of charge falls below 10% or bus voltage
drops below 26V. This procedure defines immediate load shedding to reach minimum survival
configuration (~45W), ensures solar array sun-pointing for maximum power generation, and
provides a staged recovery plan. No loads may be re-enabled until SoC exceeds 30%.

## Prerequisites
- [ ] `eps.bat_soc` (0x0101) < 10% OR `eps.bus_voltage` (0x0105) < 26.0V confirmed
- [ ] Communication link active --- `ttc.link_status` (0x0501) = LOCKED (if not, procedure is executed blind)
- [ ] EPS telemetry stream active or last known values < 60s old
- [ ] Power Systems Engineer on console
- [ ] Flight Director has declared POWER EMERGENCY

## Power Budget --- Minimum Survival Configuration
| Subsystem | Component | Power (W) | Status |
|---|---|---|---|
| OBC | Flight computer (emergency mode) | 12 | ON --- essential |
| TT&C | Transponder (receive only) | 8 | ON --- essential |
| AOCS | Magnetorquers + sun sensor | 10 | ON --- for sun acquisition |
| EPS | Power control unit | 5 | ON --- essential |
| TCS | Battery heater (thermostat) | 8 | THERMOSTAT --- survival only |
| Payload | Instrument | 0 | OFF |
| TCS | OBC heater | 0 | OFF |
| AOCS | Reaction wheels (standby) | 2 | STANDBY |
| **Total** | | **~45W** | |

Solar array capability at sun-pointing: ~120W (BOL, 500km SSO). Eclipse fraction: ~37%.
Orbit-average power generation at sun-pointing: ~76W. Margin over survival: ~31W for charging.

## Procedure Steps

### Step 1 --- Immediate Load Shed (CRITICAL --- Execute Within 60s)
**TC:** `PAYLOAD_SET_MODE(mode=0)` (Service 8, Subtype 5) --- payload OFF immediately.
**TC:** `HEATER_CONTROL(circuit=obc, on=false)` (Service 8, Subtype 7) --- OBC heater OFF.
**TC:** `HEATER_CONTROL(circuit=thruster, on=false)` (Service 8, Subtype 7) --- thruster heater OFF.
**Verify:** `payload.mode` (0x0600) = OFF within 5s.
**Verify:** `eps.power_cons` (0x0106) < 50W within 15s.
**GO/NO-GO:** Total power consumption confirmed below 50W. If not, identify and disable remaining non-essential loads.

### Step 2 --- Set Battery Heater to Thermostat-Only Mode
**Check:** Verify EPS power line 5 for battery heater (`eps.pl_htr_bat`, 0x0115) is ON.
The battery heater thermostat requires this power line to operate.
**Action:** If `eps.pl_htr_bat` (0x0115) = 0 (OFF), send: `EPS_POWER_ON(line_index=5)` (func_id 13) 
to enable the battery heater power line.
**TC:** `HEATER_CONTROL(circuit=battery, on=true)` (Service 8, Subtype 7) --- ensure battery heater
is enabled but under thermostat control only (activates below -5 degC, deactivates above 0 degC).
**Verify:** `eps.pl_htr_bat` (0x0115) = 1 (power line ON) — prerequisite for thermostat operation.
**Verify:** `tcs.htr_battery` (0x040A) = THERMOSTAT mode.
**Verify:** `tcs.temp_battery` (0x0407) within survival range (-10 degC to +45 degC).
**Rationale:** Battery must not freeze during eclipse but heater must not run continuously at this power state.
The power line must be ON for the thermostat to engage.
**GO/NO-GO:** Battery temperature > -5 degC (heater not drawing power in sunlit phase) and `eps.pl_htr_bat` = 1.

### Step 3 --- Command OBC Emergency Mode
**TC:** `OBC_SET_MODE(mode=2)` (Service 8, Subtype 3) --- EMERGENCY mode.
**Verify:** `obdh.mode` (0x0300) = EMERGENCY (2) within 5s.
**Effect:** Onboard software disables all autonomous payload scheduling, reduces housekeeping
generation rate to 1/60s, disables non-essential data recording.
**GO/NO-GO:** OBC in emergency mode confirmed.

### Step 4 --- Verify or Command Sun Acquisition
**Check:** `aocs.mode` (0x020F) --- if not SAFE_POINT (2), command sun acquisition.
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 4) --- SAFE_POINT mode (if needed).
**Verify:** `aocs.mode` (0x020F) = SAFE_POINT (2) within 5s.
**Verify:** `aocs.att_error` (0x0217) < 10.0 deg within 15 minutes.
**Monitor:** `eps.power_gen` (0x0107) --- expect increase as solar arrays achieve sun-pointing.
**Critical:** If AOCS unavailable (rates > 2 deg/s), execute PROC-EMG-002 Step 2 first.
**GO/NO-GO:** Solar arrays illuminated --- `eps.sa_a_current` (0x0103) > 0.3A and `eps.sa_b_current` (0x0104) > 0.3A.

### Step 5 --- Monitor Power Recovery (First Sunlit Phase)
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 1) --- EPS HK every 60s.
**Monitor:** `eps.bat_soc` (0x0101) --- must be trending upward.
**Monitor:** `eps.bus_voltage` (0x0105) --- must be stable or increasing.
**Monitor:** `eps.bat_voltage` (0x0100) --- verify battery accepting charge (voltage > 27V).
**Monitor:** `eps.power_gen` (0x0107) vs `eps.power_cons` (0x0106) --- confirm power-positive.
**Expected:** At ~45W load and ~120W generation, SoC should increase ~2--3% per sunlit phase (~60 min).
**GO/NO-GO:** Power-positive state confirmed (generation > consumption).

### Step 6 --- Eclipse Monitoring (CRITICAL)
**Pre-eclipse check:** Record `eps.bat_soc` (0x0101) value at eclipse entry.
**Critical threshold:** If SoC < 8% at eclipse entry, spacecraft may enter under-voltage lockout.
**Monitor:** `eps.bus_voltage` (0x0105) throughout eclipse --- must remain > 25.0V.
**Monitor:** `tcs.temp_battery` (0x0407) --- verify battery heater thermostat activates if needed.
**Duration:** Eclipse lasts ~35 minutes at 500km SSO.
**Expected drain:** ~45W x 35 min = ~26 Wh consumed. Battery capacity ~180 Wh, so ~14% SoC consumed.
**GO/NO-GO:** Bus voltage remains > 25.0V throughout eclipse. If voltage drops below 25.0V,
under-voltage protection will disconnect non-essential buses automatically.

### Step 7 --- Sustained Recovery Monitoring (Orbits 2--5)
**Duration:** Monitor for minimum 5 orbits (~8 hours).
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 1) --- every pass.
**Track:** SoC recovery trend --- target minimum 3% net gain per orbit.
**Verify:** `tcs.temp_obc` (0x0406) within operational range (-20 degC to +50 degC).
**Verify:** `tcs.temp_battery` (0x0407) within operational range (-10 degC to +45 degC).
**Action:** If SoC not trending upward after 3 orbits, investigate solar array anomaly
(`eps.sa_a_current` (0x0103) and `eps.sa_b_current` (0x0104) comparison).
**GO/NO-GO:** SoC above 20% and consistently trending upward.

### Step 8 --- Load Recovery Gate (SoC > 30%)
**Gate:** DO NOT re-enable ANY loads until `eps.bat_soc` (0x0101) > 30% confirmed on 3 consecutive HK samples.
**TC:** `HEATER_CONTROL(circuit=obc, on=true)` (Service 8, Subtype 7) --- restore OBC heater first.
**Verify:** `eps.power_cons` (0x0106) increase < 10W after heater enable.
**Verify:** Spacecraft remains power-positive after heater restore.
**Next:** When SoC > 40%, transition to PROC-EMG-004 for staged recovery to nominal.
**GO/NO-GO:** SoC > 30% stable, power-positive maintained with OBC heater restored.

## Recovery Staging Thresholds
| SoC Threshold | Allowed Action | Estimated Time from Emergency |
|---|---|---|
| < 10% | Survival loads only (~45W) | T+0 (entry condition) |
| 10--20% | No changes, monitoring only | T+3 to T+6 hours |
| 20--30% | No changes, prepare recovery plan | T+6 to T+10 hours |
| 30--40% | Restore OBC heater, increase TM rate | T+10 to T+14 hours |
| 40--60% | Restore AOCS to full mode, enable thruster heater | T+14 to T+20 hours |
| > 60% | Restore payload standby, resume nominal ops | T+20 to T+30 hours |

## Off-Nominal Handling
- If bus voltage drops below 25.0V: under-voltage protection activates autonomously. Expect loss of TT&C until next sunlit phase. Monitor via ground station for beacon re-acquisition.
- If SoC does not increase in sunlit phase: possible solar array failure. Compare `eps.sa_a_current` (0x0103) vs `eps.sa_b_current` (0x0104). If one array at zero, single-array survival is possible but with reduced margin (~60W generation).
- If battery temperature drops below -10 degC: battery heater thermostat may have failed. Command `HEATER_CONTROL(circuit=battery, on=true)` manually. Accept increased power consumption to prevent battery damage.
- If `obdh.reboot_count` (0x030A) incrementing: OBC may be experiencing brown-out resets. Disable all remaining optional loads and accept reduced telemetry.
- If AOCS cannot maintain sun-pointing: accept tumbling with degraded average power generation (~40W average). Survival is still possible if battery temperature maintained.

## Post-Conditions
- [ ] `eps.bat_soc` (0x0101) > 30% and trending upward
- [ ] `eps.bus_voltage` (0x0105) > 27.0V stable
- [ ] `eps.power_gen` (0x0107) > `eps.power_cons` (0x0106) confirmed over 3+ orbits
- [ ] Battery temperature within nominal range: `tcs.temp_battery` (0x0407) between -5 degC and +35 degC
- [ ] Root cause of power emergency identified or investigation ongoing
- [ ] Staged recovery plan agreed with Flight Director for load restoration
- [ ] Transition to PROC-EMG-004 when SoC > 40% for return to nominal operations

## Recovery Path — Re-Powering Shed Loads
After this procedure has stabilised the bus and the root cause is understood, **any subsystems whose power lines were de-energised during load shed must be brought back up in the controlled order defined by LEOP-007 (Sequential Power-On)**. Do **not** simply re-enable lines ad-hoc: LEOP-007 enforces the correct subsystem→line→mode sequencing, the inrush spacing, the thermal pre-conditioning of the OBC/battery heaters, and the AOCS/payload set_mode exemptions to the two-stage TC power gate. Cross-reference: `configs/eosat1/procedures/leop/sequential_power_on.md` (PROC-LEOP-007).

# PROC-EPS-OFF-003: Battery Cell Failure

**Category:** Contingency
**Position Lead:** Power & Thermal (EPS/TCS)
**Cross-Position:** (Flight Director for awareness)
**Difficulty:** Advanced

## Objective
Respond to a suspected battery cell failure indicated by anomalous voltage drop, and
assess whether the remaining battery capacity is sufficient to sustain spacecraft
operations through eclipse periods. This procedure monitors battery health parameters,
reduces loads if needed to match reduced energy storage, and verifies the spacecraft
can safely complete an eclipse with the degraded battery.

## Prerequisites
- [ ] Battery voltage anomaly detected — `eps.bat_voltage` (0x0100) lower than expected
  for the current state of charge, or sudden voltage drop observed
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] Flight Director notified of battery anomaly
- [ ] Current orbit prediction available (eclipse duration and timing)

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| eps.bat_voltage | 0x0100 | Anomalous — lower than expected for current SoC |
| eps.bat_soc | 0x0101 | Record current value and trend |
| eps.bat_temp | 0x0102 | Within 0 to 40 C (critical — overheating = danger) |
| eps.bat_current | 0x0109 | Record charge/discharge current |
| eps.bat_capacity_wh | 0x010A | Reduced from nominal 120 Wh |
| eps.bus_voltage | 0x0105 | Monitor — may drop under load |
| eps.power_cons | 0x0106 | Current total power consumption |
| eps.power_gen | 0x0107 | Current power generation |
| eps.eclipse_flag | 0x0108 | Current sun/eclipse state |
| tcs.temp_battery | 0x0407 | Cross-check with EPS battery temp |
| tcs.htr_battery | 0x040A | Battery heater status |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| PAYLOAD_SET_MODE | 8 | 1 | 20 | Reduce payload load |
| FPA_COOLER | 8 | 1 | 33 | Disable FPA cooler |
| TTC_PA_OFF | 8 | 1 | 54 | Disable PA if needed |
| HEATER_BATTERY | 8 | 1 | 30 | Control battery heater |
| HEATER_SET_SETPOINT | 8 | 1 | 34 | Adjust battery heater setpoints |
| EPS_POWER_OFF | 8 | 1 | 14 | Shed power lines if needed |

## Procedure Steps

### Step 1: Detect and Characterize Battery Voltage Anomaly
**Action:** Request EPS housekeeping: `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bat_voltage` (0x0100) — record current value
**Verify:** `eps.bat_soc` (0x0101) — record current value
**Verify:** `eps.bat_capacity_wh` (0x010A) — record value (nominal = 120 Wh)
**Verify:** `eps.bat_current` (0x0109) — record current (positive = charging, negative = discharging)
**Action:** Calculate expected voltage for current SoC:
- SoC 100% corresponds to ~29.2 V, SoC 0% corresponds to ~21.5 V (from battery model)
- Expected voltage = 21.5 + (SoC/100) * (29.2 - 21.5) = 21.5 + SoC * 0.077
**Action:** Compare measured voltage with expected voltage:
- If measured voltage is > 1.0 V below expected: Possible cell failure.
- If measured voltage is within 0.5 V of expected: May be normal variation.
**Note:** A single cell failure in a series string reduces the total voltage by the
cell voltage (~3.6 V nominal for Li-ion).
**GO/NO-GO:** Voltage anomaly confirmed (> 1.0 V deviation) — proceed to thermal assessment.

### Step 2: Check Battery Temperature
**Action:** Request TCS housekeeping: `HK_REQUEST(sid=3)` (Service 3, Subtype 27)
**Verify:** `eps.bat_temp` (0x0102) — record value
**Verify:** `tcs.temp_battery` (0x0407) — cross-check with TCS sensor
**Action:** Assess battery thermal condition:
- If battery temperature > 40 C: Anomalous heating. Possible internal short in failed
  cell. This is DANGEROUS — risk of thermal runaway.
  - Immediately reduce charge/discharge current by shedding loads.
  - Disable battery heater: `HEATER_BATTERY(on=0)` (func_id 30)
  - Monitor temperature closely (every 30 s).
  - If temperature exceeds 50 C, execute EMERGENCY mode per PROC-TCS-OFF-001.
- If battery temperature is within 0 to 35 C: Thermal condition is currently safe.
  Continue with capacity assessment.
**Verify:** `tcs.htr_battery` (0x040A) — if heater is ON and battery is warm, disable it
to reduce unnecessary heating.
**GO/NO-GO:** Battery temperature safe — proceed. If overheating, address thermal
issue first before continuing.

### Step 3: Assess Remaining Battery Capacity
**Action:** Request EPS housekeeping: `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bat_capacity_wh` (0x010A) — record reported capacity
**Action:** If capacity reporting is available, use the reported value. Otherwise,
estimate remaining capacity:
- Nominal capacity: 120 Wh
- If one cell failed (open circuit): Capacity may drop to ~0 (series string broken)
  unless the battery management system can bypass the cell.
- If one cell failed (short circuit): Capacity reduced by ~15-20% (one cell of a
  multi-cell string, voltage reduced but capacity partially maintained).
**Action:** Calculate eclipse survival budget:
- Maximum eclipse duration (from orbit prediction): typically ~35 min for LEO
- Required power during eclipse: `eps.power_cons` (0x0106) current value
- Required energy = power * eclipse_duration / 60 (in Wh)
- Available energy = bat_capacity_wh * (current_SoC/100 - 0.20)
  (reserve 20% SoC as minimum)
- Margin = available energy - required energy
**GO/NO-GO:** If margin > 0: Battery can survive eclipse at current load. If margin < 0:
Load reduction required. Proceed to Step 4.

### Step 4: Reduce Loads If Needed
**Action:** If eclipse survival margin is negative, reduce loads in priority order:
1. Payload OFF (saves ~8-45 W): `PAYLOAD_SET_MODE(mode=0)` (func_id 20)
   **Verify:** `payload.mode` (0x0600) = 0 within 10 s
2. FPA cooler OFF (saves ~15 W): `FPA_COOLER(on=0)` (func_id 33)
   **Verify:** `tcs.cooler_fpa` (0x040C) = 0 within 5 s
3. TX OFF (saves ~20 W) — only if eclipse is imminent and load budget is still negative:
   `TTC_PA_OFF` (func_id 54)
   **Verify:** `ttc.pa_on` (0x0516) = 0 within 5 s
**Action:** After each load shed, recalculate eclipse survival margin.
**Verify:** `eps.power_cons` (0x0106) — confirm reduced power consumption
**Action:** Adjust battery heater setpoints to reduce heater duty cycle during eclipse:
`HEATER_SET_SETPOINT(circuit=0, on_temp=-2.0, off_temp=2.0)` (func_id 34)
**Note:** This allows the battery to get slightly colder during eclipse to save energy,
but still protects against dangerously low temperatures.
**GO/NO-GO:** Eclipse survival margin is now positive — proceed to monitoring.

### Step 5: Monitor Battery Through Eclipse (If Applicable)
**Action:** If an eclipse is approaching or in progress, monitor battery at 60 s intervals:
`HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bat_voltage` (0x0100) — does NOT drop below 24.0 V
**Verify:** `eps.bat_soc` (0x0101) — does NOT drop below 20%
**Verify:** `eps.bat_temp` (0x0102) — remains within 0 to 40 C
**Verify:** `eps.bus_voltage` (0x0105) — remains > 26.5 V
**Action:** If `eps.bus_voltage` drops below 26.5 V during eclipse: Execute additional
load shedding per PROC-EPS-OFF-002 (Undervoltage Load Shedding).
**Action:** After eclipse exit, verify:
- `eps.power_gen` (0x0107) > 25 W (charging resumes)
- `eps.bat_soc` (0x0101) trending upward
- `eps.bat_temp` (0x0102) stable
**GO/NO-GO:** Battery survived eclipse — proceed to long-term assessment.

### Step 6: Long-Term Capacity Verification
**Action:** Over the next 2-3 orbits, monitor battery charge/discharge behavior:
**Verify:** `eps.bat_soc` (0x0101) — does SoC fully recover to > 90% during sunlit phase?
**Verify:** `eps.bat_voltage` (0x0100) — does voltage reach > 28.5 V at full charge?
**Verify:** `eps.bat_capacity_wh` (0x010A) — has the reported capacity changed?
**Action:** Assess long-term impact:
- If capacity is reduced but sufficient for nominal operations with margin: Continue
  operations with adjusted power budget.
- If capacity is reduced below the level needed for nominal operations: Reduce payload
  duty cycle, limit imaging to sunlit-only operations, or reduce data downlink volume.
**Action:** Document findings in anomaly report:
- Measured voltage vs. expected voltage at multiple SoC points
- Estimated capacity loss
- Eclipse survival margins at current and reduced load levels
- Recommended operational constraints
**Action:** Notify Flight Director and mission planning team of any operational constraints.

## Verification Criteria
- [ ] Battery voltage anomaly characterized (cell failure type assessed)
- [ ] Battery temperature within safe limits (0 to 40 C)
- [ ] Remaining capacity sufficient for eclipse survival (with margin > 10%)
- [ ] Loads reduced if necessary to match degraded battery capacity
- [ ] Battery successfully completed at least one eclipse at reduced capacity
- [ ] Long-term operational constraints documented
- [ ] Anomaly report filed

## Contingency
- If battery temperature exceeds 45 C at any point: Risk of thermal runaway. Disable
  battery heater immediately. Shed all non-essential loads. If temperature exceeds
  50 C, command `OBC_SET_MODE(mode=2)` for EMERGENCY. Follow PROC-TCS-OFF-001.
- If battery voltage drops below 22.0 V: Risk of deep discharge and permanent damage.
  Immediately shed all loads except OBC and TTC RX. Command EMERGENCY mode.
- If battery SoC cannot be maintained above 20% through a full orbit: The battery
  degradation is too severe for current operations. Must operate in a severely
  reduced mode (minimum loads, sunlit-only operations). Consult engineering team.
- If battery shows signs of intermittent shorts (voltage fluctuations): Monitor
  closely. Intermittent shorts may evolve into permanent shorts or thermal events.
  Consider proactive load reduction even if current margins seem adequate.
- If second cell failure occurs: Battery capacity may be critically reduced. Assess
  immediately whether spacecraft can survive eclipse. If not, request emergency
  contact scheduling for continuous ground support.

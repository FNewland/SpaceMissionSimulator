# COM-001: EPS Power System Checkout
**Subsystem:** EPS
**Phase:** COMMISSIONING
**Revision:** 2.0 --- 2026-08c: entry/exit states; the load-transient test no
longer borrows the payload (its line is OFF until COM-009 --- the old Step 7
commanded a payload mode on a dark line and the mode silently reverted);
OBC_SET_MODE encodings corrected (0=NOMINAL, 1=SAFE).
**Approved:** Flight Operations Director

**Entry state:** LEOP-003 exit state --- mission phase 5, COARSE_SUN sun hold,
wheels disabled, payload/cooler/heater(unless LEOP-007 needed one)/prop lines
OFF, downlink LOW, SIDs 1/2/3/4/6/11/12 enabled.

## Purpose
Perform a comprehensive checkout of the Electrical Power System. Verify bus voltage
regulation, battery health and charge/discharge characteristics, solar array performance
at various sun angles, and overall power budget. Establish baseline performance values
for mission operations.

## Prerequisites
- [ ] LEOP completed --- `eps.spacecraft_phase` (0x0129) = 5
- [ ] Spacecraft in the COARSE_SUN sun hold (`aocs.mode` 0x020F = 3 --- still
      valid here: no star tracker is powered until COM-003, so the hold has
      nothing to auto-promote on)
- [ ] At least 2 full orbits of continuous telemetry archived since LEOP completion
- [ ] Bidirectional S-band link active with link margin > 3 dB
- [ ] Battery SOC > 70% at start of procedure
- [ ] No active anomalies on EPS subsystem

## Procedure Steps

### Step 1 — Baseline Bus Voltage Measurement
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) in range [27.5V, 28.5V] within 10s
**Action:** Record bus voltage. Nominal regulation point is 28.0V +/- 0.5V.
**GO/NO-GO:** Bus voltage regulated within specification

### Step 2 — Battery Voltage and State of Charge
**TC:** `GET_PARAM(param_id=0x0100)` (Service 20, Subtype 3) — battery voltage
**TC:** `GET_PARAM(param_id=0x0101)` (Service 20, Subtype 3) — battery SOC
**Verify:** `eps.bat_voltage` (0x0100) in range [14.4V, 16.8V] within 10s
**Verify:** `eps.bat_soc` (0x0101) > 70% within 10s
**Action:** Record values. Li-Ion 40Ah battery nominal voltage range is 14.4V (empty) to 16.8V (full).
**GO/NO-GO:** Battery voltage and SOC within nominal range

### Step 3 — Solar Array Sunlit Performance
**Action:** Perform this step during sunlit orbit phase with stable sun-pointing.
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.power_gen` (0x0107) in range [40W, 65W] within 10s
**Action:** Record power generation. Expected ~64W peak at optimal sun angle with deployed wings (0.78 m², 29.5% GaAs cells). Actual value depends on current beta angle, wing deployment status, and attitude accuracy.
**GO/NO-GO:** Solar array power generation within expected range

### Step 4 — Eclipse Discharge Monitoring
**Action:** Monitor battery through a full eclipse period (~35 minutes at the mission's 450 km SSO).
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — at eclipse entry
**Verify:** `eps.power_gen` (0x0107) = ~0W within 30s of eclipse entry
**Verify:** `eps.bat_soc` (0x0101) decreasing at < 1.5% per minute within 60s
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — at mid-eclipse
**Verify:** `eps.bus_voltage` (0x0105) > 27.0V (battery sustaining bus) within 10s
**GO/NO-GO:** Battery discharge rate nominal, bus voltage maintained

### Step 5 — Eclipse Exit Charge Recovery
**Action:** Monitor battery charge recovery after eclipse exit.
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — at eclipse exit +5 min
**Verify:** `eps.power_gen` (0x0107) > 35W within 60s of eclipse exit
**Verify:** `eps.bat_soc` (0x0101) increasing within 120s of eclipse exit
**Action:** Record charge recovery rate. Battery should recover full charge within the sunlit portion of the orbit under nominal load.
**GO/NO-GO:** Charge recovery nominal after eclipse

### Step 6 — Power Budget Verification
**Action:** Calculate total spacecraft power consumption from telemetry. Compare against power generation and battery capacity. Verify positive energy balance over one complete orbit.
**TC:** `GET_PARAM(param_id=0x0108)` (Service 20, Subtype 3) — total load power
**Verify:** Orbit-average power generation > orbit-average power consumption
**Verify:** End-of-eclipse SOC > 50% under nominal load
**Action:** Document power budget with margins for commissioning and nominal operations.
**GO/NO-GO:** Positive orbit-average energy balance confirmed

### Step 7 — Bus Voltage Regulation Under Load Transient
**Action:** Test bus regulation by commanding a known load change. Do NOT use
the payload for this: its line is OFF until COM-009, and a mode command on a
dark line silently reverts. The battery-heater line (~6 W) is the transient
source, and it is restored to its entry state afterwards.
**Action:** Record the entry state of `eps.pl_htr_bat` (0x0115) first.
**TC:** `EPS_POWER_ON(line_index=5)` (Service 8, Subtype 1, func_id 19) — htr_bat (brief test)
**Verify:** `eps.bus_voltage` (0x0105) remains in range [26.5V, 29.5V] within 5s
**TC:** `EPS_POWER_OFF(line_index=5)` (Service 8, Subtype 1, func_id 20) — restore, ONLY if the line was off at entry
**Verify:** `eps.pl_htr_bat` (0x0115) back to its entry value within 5s
**Verify:** `eps.bus_voltage` (0x0105) returns to [27.0V, 29.0V] within 10s
**GO/NO-GO:** Bus regulation stable under transient load, line state restored

## Off-Nominal Handling
- If bus voltage < 27.0V: Check battery SOC. If SOC < 30%, reduce loads immediately. Disable non-essential heaters. Verify sun-pointing attitude. If bus voltage < 26.0V, execute CTG-001 (load shed) and, with FD approval, `OBC_SET_MODE(mode=1)` — 1 is SAFE (0 is NOMINAL; the old text had the encoding backwards).
- If power generation < 35W in sunlight with good sun-pointing: Check individual array string currents via `GET_PARAM(param_id=0x0112)` and `GET_PARAM(param_id=0x0113)`. If one wing underperforming, log anomaly. Assess single-wing power budget viability.
- If battery SOC < 50% at end of eclipse: Reduce operational load. Defer commissioning activities requiring high power. Re-assess power budget.
- If charge recovery rate below prediction: Check solar array degradation. Verify battery charge controller via `GET_PARAM(param_id=0x0109)`. If charge controller fault suspected, investigate redundant path.
- If bus voltage oscillation detected: Check regulation mode via `GET_PARAM(param_id=0x010A)`. Log anomaly for EPS engineer review. If unstable, command `OBC_SET_MODE(mode=1)` (SAFE) with FD approval; restore with mode=0 (NOMINAL) after investigation.

## Post-Conditions
- [ ] Bus voltage confirmed regulated at 28.0V +/- 0.5V
- [ ] Battery voltage and SOC nominal (> 70% after full charge)
- [ ] Solar array generating 40-65W in sunlight (wings deployed, sun-pointed)
- [ ] Eclipse discharge rate within prediction
- [ ] Charge recovery nominal after eclipse exit
- [ ] Positive orbit-average energy balance confirmed
- [ ] Bus regulation stable under transient loads
- [ ] EPS Checkout Report generated with baseline values

## Exit state
- IDENTICAL to the entry state: every line touched by this card (htr_bat in
  Step 7) is restored; no mode, tier or latch changed

**NEXT:** COM-002 (TCS Verification)

# COM-001: EPS Power System Checkout
**Subsystem:** EPS
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Perform a comprehensive checkout of the Electrical Power System. Verify bus voltage
regulation, battery health and charge/discharge characteristics, solar array performance
at various sun angles, and overall power budget. Establish baseline performance values
for mission operations.

## Prerequisites
- [ ] LEOP phase completed successfully
- [ ] Spacecraft in SAFE_POINT mode (sun-pointing)
- [ ] At least 2 full orbits of continuous telemetry archived since LEOP completion
- [ ] Bidirectional VHF/UHF link active with link margin > 3 dB
- [ ] Battery SOC > 70% at start of procedure
- [ ] No active anomalies on EPS subsystem

## Procedure Steps

### Step 1 — Baseline Bus Voltage Measurement
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) in range [27.5V, 28.5V] within 10s
**Action:** Record bus voltage. Nominal regulation point is 28.0V +/- 0.5V.
**GO/NO-GO:** Bus voltage regulated within specification

### Step 2 — Battery Voltage and State of Charge
**TC:** `GET_PARAM(0x0100)` (Service 20, Subtype 1) — battery voltage
**TC:** `GET_PARAM(0x0101)` (Service 20, Subtype 1) — battery SOC
**Verify:** `eps.bat_voltage` (0x0100) in range [14.4V, 16.8V] within 10s
**Verify:** `eps.bat_soc` (0x0101) > 70% within 10s
**Action:** Record values. Li-Ion 40Ah battery nominal voltage range is 14.4V (empty) to 16.8V (full).
**GO/NO-GO:** Battery voltage and SOC within nominal range

### Step 3 — Solar Array Sunlit Performance
**Action:** Perform this step during sunlit orbit phase with stable sun-pointing.
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.power_gen` (0x0107) in range [100W, 140W] within 10s
**Action:** Record power generation. Expected ~135W at optimal sun angle with GaAs arrays. Actual value depends on current beta angle and attitude accuracy.
**GO/NO-GO:** Solar array power generation within expected range

### Step 4 — Eclipse Discharge Monitoring
**Action:** Monitor battery through a full eclipse period (~35 minutes at 500 km SSO).
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — at eclipse entry
**Verify:** `eps.power_gen` (0x0107) = ~0W within 30s of eclipse entry
**Verify:** `eps.bat_soc` (0x0101) decreasing at < 1.5% per minute within 60s
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — at mid-eclipse
**Verify:** `eps.bus_voltage` (0x0105) > 27.0V (battery sustaining bus) within 10s
**GO/NO-GO:** Battery discharge rate nominal, bus voltage maintained

### Step 5 — Eclipse Exit Charge Recovery
**Action:** Monitor battery charge recovery after eclipse exit.
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — at eclipse exit +5 min
**Verify:** `eps.power_gen` (0x0107) > 80W within 60s of eclipse exit
**Verify:** `eps.bat_soc` (0x0101) increasing within 120s of eclipse exit
**Action:** Record charge recovery rate. Battery should recover full charge within the sunlit portion of the orbit under nominal load.
**GO/NO-GO:** Charge recovery nominal after eclipse

### Step 6 — Power Budget Verification
**Action:** Calculate total spacecraft power consumption from telemetry. Compare against power generation and battery capacity. Verify positive energy balance over one complete orbit.
**TC:** `GET_PARAM(0x0108)` (Service 20, Subtype 1) — total load power
**Verify:** Orbit-average power generation > orbit-average power consumption
**Verify:** End-of-eclipse SOC > 50% under nominal load
**Action:** Document power budget with margins for commissioning and nominal operations.
**GO/NO-GO:** Positive orbit-average energy balance confirmed

### Step 7 — Bus Voltage Regulation Under Load Transient
**Action:** Test bus regulation by commanding a known load change (enable payload standby).
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1) — STANDBY (brief test)
**Verify:** `eps.bus_voltage` (0x0105) remains in range [27.0V, 29.0V] within 5s
**TC:** `PAYLOAD_SET_MODE(mode=0)` (Service 8, Subtype 1) — OFF (restore)
**Verify:** `eps.bus_voltage` (0x0105) returns to [27.5V, 28.5V] within 10s
**GO/NO-GO:** Bus regulation stable under transient load

## Off-Nominal Handling
- If bus voltage < 27.0V: Check battery SOC. If SOC < 30%, reduce loads immediately. Disable non-essential heaters. Verify sun-pointing attitude. If bus voltage < 26.0V, enter safe mode via `OBC_SET_MODE(mode=0)`.
- If power generation < 80W in sunlight with good sun-pointing: Check individual array string currents via `GET_PARAM(0x0112)` and `GET_PARAM(0x0113)`. If one wing underperforming, log anomaly. Assess single-wing power budget viability.
- If battery SOC < 50% at end of eclipse: Reduce operational load. Defer commissioning activities requiring high power. Re-assess power budget.
- If charge recovery rate below prediction: Check solar array degradation. Verify battery charge controller via `GET_PARAM(0x0109)`. If charge controller fault suspected, investigate redundant path.
- If bus voltage oscillation detected: Check regulation mode via `GET_PARAM(0x010A)`. Log anomaly for EPS engineer review. If unstable, command `OBC_SET_MODE(mode=0)` for safe mode.

## Post-Conditions
- [ ] Bus voltage confirmed regulated at 28.0V +/- 0.5V
- [ ] Battery voltage and SOC nominal (> 70% after full charge)
- [ ] Solar array generating 100-140W in sunlight
- [ ] Eclipse discharge rate within prediction
- [ ] Charge recovery nominal after eclipse exit
- [ ] Positive orbit-average energy balance confirmed
- [ ] Bus regulation stable under transient loads
- [ ] EPS Checkout Report generated with baseline values
- [ ] GO decision for COM-002 (TCS Verification)

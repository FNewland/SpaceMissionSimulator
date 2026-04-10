# COM-102: FPA Cooler Activation
**Subsystem:** Payload / TCS
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Activate the Focal Plane Array (FPA) cooler on the multispectral imager. Monitor
the cooldown from ambient temperature to the operational temperature of -15C.
Verify cooler power consumption, cooldown rate, and thermal stability. The FPA must
reach and maintain the operational temperature before imaging operations can begin.

## Prerequisites
- [ ] COM-101 (Payload Power-On) completed — payload in STANDBY (mode 1)
- [ ] Payload self-test PASS confirmed
- [ ] `payload.fpa_temp` (0x0601) at ambient baseline (+5C to +25C)
- [ ] `eps.bat_soc` (0x0101) > 70% (cooler adds ~10W continuous load)
- [ ] `eps.power_gen` (0x0107) > 100W (sunlit phase)
- [ ] AOCS in SAFE_POINT (mode 2) or NOMINAL_POINT (mode 3)
- [ ] Bidirectional VHF/UHF link active
- [ ] Ground station pass covers at least 20 minutes (or multiple passes available)
- [ ] Payload and thermal engineers on console

## Procedure Steps

### Step 1 — Record Pre-Activation Thermal State
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27)
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `payload.fpa_temp` (0x0601) in range [+5C, +25C] within 10s
**Verify:** `tcs.temp_fpa` (0x0408) consistent with `payload.fpa_temp` within 10s
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10s
**Action:** Record baseline FPA temperature and timestamp. This marks cooldown T=0.
**GO/NO-GO:** FPA at ambient temperature, payload in STANDBY

### Step 2 — Enable FPA Cooler
**TC:** `SET_PARAM(0x0620, 1)` (Service 20, Subtype 3) — FPA cooler enable
**Verify:** FPA cooler status = ON (value 1) within 10s via `GET_PARAM(0x0620)`
**Action:** Cooler compressor starts. Expect slight increase in payload power draw (~8-12W additional). Audible vibration signature may appear in accelerometer data — this is normal.
**GO/NO-GO:** FPA cooler enabled and running

### Step 3 — Verify Cooler Power Draw
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**TC:** `GET_PARAM(0x0108)` (Service 20, Subtype 1) — total load power
**Verify:** Total power increase is 8-12W relative to pre-cooler baseline within 30s
**Verify:** `eps.bus_voltage` (0x0105) > 27.0V (bus stable under additional load) within 10s
**GO/NO-GO:** Cooler power draw within specification, bus stable

### Step 4 — Monitor Cooldown (First 10 Minutes)
**Action:** Sample FPA temperature every 2 minutes during initial cooldown.
**TC:** `GET_PARAM(0x0601)` (Service 20, Subtype 1) — at T+2 min
**Verify:** `payload.fpa_temp` < initial temperature (cooling trend) within 10s
**TC:** `GET_PARAM(0x0601)` (Service 20, Subtype 1) — at T+5 min
**Verify:** `payload.fpa_temp` < +15C within 10s (expected cooldown rate ~2-3C/min initially)
**TC:** `GET_PARAM(0x0601)` (Service 20, Subtype 1) — at T+10 min
**Verify:** `payload.fpa_temp` < 0C within 10s
**Action:** Record cooldown curve data points. Initial cooldown rate should be 2-3C per minute, slowing as temperature decreases.
**GO/NO-GO:** FPA temperature decreasing at expected rate

### Step 5 — Monitor Cooldown (10-30 Minutes)
**Action:** Continue monitoring at 5-minute intervals as cooldown rate decreases.
**TC:** `GET_PARAM(0x0601)` (Service 20, Subtype 1) — at T+15 min
**Verify:** `payload.fpa_temp` < -10C within 10s
**TC:** `GET_PARAM(0x0601)` (Service 20, Subtype 1) — at T+20 min
**Verify:** `payload.fpa_temp` < -20C within 10s
**TC:** `GET_PARAM(0x0601)` (Service 20, Subtype 1) — at T+30 min
**Verify:** `payload.fpa_temp` < -13C within 10s
**GO/NO-GO:** FPA approaching operational temperature

### Step 6 — Verify Operational Temperature Reached
**TC:** `GET_PARAM(0x0601)` (Service 20, Subtype 1)
**Verify:** `payload.fpa_temp` (0x0601) in range [-17C, -13C] within 10s
**Action:** Wait for temperature to stabilize at -15C +/- 2C. The cooler control loop regulates to the setpoint.
**TC:** `GET_PARAM(0x0621)` (Service 20, Subtype 1) — cooler setpoint
**Verify:** Cooler setpoint = -15C within 10s
**GO/NO-GO:** FPA at operational temperature -15C +/- 2C

### Step 7 — Verify Temperature Stability
**Action:** Monitor FPA temperature for 10 minutes to confirm stable regulation at setpoint.
**TC:** `GET_PARAM(0x0601)` (Service 20, Subtype 1) — at T+35 min
**TC:** `GET_PARAM(0x0601)` (Service 20, Subtype 1) — at T+40 min
**TC:** `GET_PARAM(0x0601)` (Service 20, Subtype 1) — at T+45 min
**Verify:** `payload.fpa_temp` remains in range [-17C, -13C] across all samples
**Verify:** Temperature variation < 1C peak-to-peak over 10 minutes
**Action:** Record stability data. Peak-to-peak variation < 0.5C indicates excellent cooler control.
**GO/NO-GO:** FPA temperature stable at operational setpoint

### Step 8 — Monitor Through Eclipse (if applicable)
**Action:** If pass spans an eclipse, verify cooler maintains FPA temperature.
**TC:** `GET_PARAM(0x0601)` (Service 20, Subtype 1) — during eclipse
**Verify:** `payload.fpa_temp` remains in range [-17C, -13C] within 10s
**Verify:** `eps.bus_voltage` (0x0105) > 27.0V (bus holds with cooler load) within 10s
**GO/NO-GO:** Cooler maintains setpoint through eclipse with adequate power

### Step 9 — Generate Cooler Activation Report
**Action:** Compile complete cooldown curve (temperature vs time), cooler power consumption, thermal stability data, and eclipse performance. Distribute FPA Cooler Activation Report. FPA is now ready for imaging.
**GO/NO-GO:** FPA cooler activated and stable — ready for COM-103

## Off-Nominal Handling
- If FPA temperature not decreasing after 5 minutes: Verify cooler status via `GET_PARAM(0x0620)`. If cooler ON but not cooling, check cooler power draw — zero draw indicates compressor failure. Power off cooler, power off payload. Escalate to payload thermal engineer.
- If cooldown rate slower than 1C/min from start: May indicate reduced cooler efficiency or higher thermal load than predicted. Allow additional time. If FPA cannot reach -13C within 60 minutes, log anomaly. Imaging may still be possible at higher FPA temperature with reduced performance.
- If bus voltage drops below 27.0V with cooler running: Disable cooler via `SET_PARAM(0x0620, 0)`. Check battery SOC. Wait for higher charge state or better illumination. Retry during next sunlit pass with full battery.
- If FPA temperature oscillating > 2C at setpoint: Cooler control loop may need tuning. Check setpoint via `GET_PARAM(0x0621)`. If oscillation persists, adjust cooler gain via `SET_PARAM(0x0622, <value>)` per payload manufacturer guidance.
- If cooler introduces excessive vibration (detected via accelerometer jitter): Compare AOCS attitude jitter with cooler ON vs OFF. If jitter exceeds pointing requirement, schedule cooler operation for non-imaging periods. Consult AOCS engineer for filter tuning.

## Post-Conditions
- [ ] FPA cooler activated and running
- [ ] FPA temperature stable at -15C +/- 2C
- [ ] Temperature stability < 1C peak-to-peak
- [ ] Cooler power draw 8-12W within specification
- [ ] Bus voltage maintained under cooler load
- [ ] Cooldown curve documented
- [ ] Eclipse performance verified (if tested)
- [ ] FPA Cooler Activation Report distributed
- [ ] GO decision for COM-103 (First Light Acquisition)

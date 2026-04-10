# COM-101: Payload Power-On Sequence
**Subsystem:** Payload / EPS
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Power on the multispectral imager payload for the first time in orbit. Transition
the payload from OFF to STANDBY mode. Verify payload electronics initialization,
telemetry generation, and confirm the power draw is within the EPS budget. Establish
the baseline payload state required for subsequent cooler activation and imaging.

## Prerequisites
- [ ] COM-001 through COM-008 completed — platform fully commissioned
- [ ] AOCS in SAFE_POINT mode (mode 2) minimum, preferably NOMINAL_POINT (mode 3)
- [ ] `eps.bat_soc` (0x0101) > 75% (payload power-on adds ~15W load)
- [ ] `eps.power_gen` (0x0107) > 100W (sunlit phase preferred)
- [ ] OBC in NOMINAL mode
- [ ] `tcs.temp_fpa` (0x0408) in range [+5C, +25C] (ambient, pre-cooler)
- [ ] Bidirectional VHF/UHF link active with link margin > 6 dB
- [ ] Payload team on console

## Procedure Steps

### Step 1 — Pre-Power-On EPS Baseline
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) in range [27.5V, 28.5V] within 10s
**Verify:** `eps.bat_soc` (0x0101) > 75% within 10s
**Verify:** `eps.power_gen` (0x0107) > 100W within 10s
**Action:** Record pre-power-on bus voltage, battery SOC, and power generation as baseline.
**GO/NO-GO:** EPS can support additional payload load

### Step 2 — Verify Payload Currently OFF
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `payload.mode` (0x0600) = 0 (OFF) within 10s
**GO/NO-GO:** Payload confirmed OFF — safe to power on

### Step 3 — Command Payload to STANDBY
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1)
**Action:** Payload power supply enabled. Payload electronics boot sequence initiates. Boot duration approximately 45 seconds.
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 60s
**GO/NO-GO:** Payload transitioned to STANDBY

### Step 4 — Verify Payload Power Draw
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Action:** Compare bus voltage and power consumption with pre-power-on baseline.
**Verify:** `eps.bus_voltage` (0x0105) > 27.0V within 10s (bus maintained under new load)
**TC:** `GET_PARAM(0x0108)` (Service 20, Subtype 1) — total load power
**Verify:** Total load increase is 10-20W relative to baseline within 10s
**GO/NO-GO:** Payload power draw within expected range

### Step 5 — Verify Payload Electronics Health
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**TC:** `GET_PARAM(0x0602)` (Service 20, Subtype 1) — payload firmware version
**TC:** `GET_PARAM(0x0603)` (Service 20, Subtype 1) — payload self-test result
**Verify:** Payload firmware version matches expected value within 10s
**Verify:** Payload self-test result = PASS (value 0) within 10s
**GO/NO-GO:** Payload electronics healthy — firmware and self-test nominal

### Step 6 — Check FPA Temperature (Pre-Cooler)
**TC:** `GET_PARAM(0x0601)` (Service 20, Subtype 1) — FPA temperature
**Verify:** `payload.fpa_temp` (0x0601) in range [+5C, +30C] within 10s
**Action:** Record FPA ambient temperature before cooler activation (COM-102).
**GO/NO-GO:** FPA temperature at expected ambient level

### Step 7 — Verify Payload Data Interface
**TC:** `GET_PARAM(0x0604)` (Service 20, Subtype 1) — data interface status
**Verify:** Data interface status = READY (value 1) within 10s
**TC:** `GET_PARAM(0x0605)` (Service 20, Subtype 1) — onboard storage free (MB)
**Verify:** Onboard storage free > 90% of capacity within 10s
**Action:** Confirm payload can write image data to mass memory.
**GO/NO-GO:** Data interface operational, storage available

### Step 8 — Maintain STANDBY for Thermal Stabilization
**Action:** Hold payload in STANDBY for 10 minutes to allow electronics thermal stabilization. Monitor payload and platform temperatures.
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27) — at T+5 min
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — at T+10 min
**Verify:** `tcs.temp_obc` (0x0406) stable (not affected by payload heat) within 10s
**Verify:** `payload.fpa_temp` (0x0601) stable within 10s
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) maintained within 10s
**GO/NO-GO:** Payload thermally stable in STANDBY

### Step 9 — Log Payload Power-On Report
**Action:** Record all power-on telemetry including timing, power draw, firmware version, self-test results, FPA temperature, and data interface status. Distribute Payload Power-On Report.
**GO/NO-GO:** Payload successfully powered on in STANDBY — ready for COM-102

## Off-Nominal Handling
- If `payload.mode` does not transition to STANDBY within 90s: Retry `PAYLOAD_SET_MODE(mode=1)` once. If still no transition, check payload power bus via `GET_PARAM(0x0610)`. If power bus OFF, investigate EPS switch. If power ON but payload unresponsive, power off via `PAYLOAD_SET_MODE(mode=0)` and escalate.
- If bus voltage drops below 27.0V on payload power-on: Immediately power off payload via `PAYLOAD_SET_MODE(mode=0)`. Check battery SOC. If SOC < 60%, wait for higher charge state. If bus regulation issue, investigate EPS before retry.
- If payload self-test fails: Record failure code via `GET_PARAM(0x0603)`. Power off payload via `PAYLOAD_SET_MODE(mode=0)`. Do not proceed to cooler activation. Escalate to payload engineer with self-test diagnostic data.
- If payload power draw > 25W in STANDBY: Unexpected current draw may indicate electronics fault. Monitor for 5 minutes. If trending upward, power off payload. If stable at higher-than-expected level, consult payload manufacturer specifications.
- If FPA temperature reading anomalous: Check thermistor via `GET_PARAM(0x0611)`. If thermistor fault, note limitation for COM-102 (cooler activation will rely on alternative monitoring).

## Post-Conditions
- [ ] Payload in STANDBY mode (mode 1)
- [ ] Payload power draw within expected range (10-20W)
- [ ] Payload firmware version confirmed
- [ ] Payload self-test PASS
- [ ] FPA temperature at ambient baseline recorded
- [ ] Data interface operational, storage available
- [ ] Bus voltage stable under payload load
- [ ] Payload Power-On Report distributed
- [ ] GO decision for COM-102 (FPA Cooler Activation)

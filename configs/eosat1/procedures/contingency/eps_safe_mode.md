# CON-002: EPS Safe Mode Recovery
**Subsystem:** EPS
**Phase:** CONTINGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Recover the Electrical Power Subsystem from a low-energy state triggered by battery
state-of-charge falling below 20% or main bus voltage dropping below 26.5V. This procedure
sheds non-essential loads to arrest energy depletion, monitors solar array performance during
the sunlit orbital phase, and progressively re-enables loads once the battery has recovered
above 40% SoC.

## Prerequisites
- [ ] Spacecraft has entered OBC safe mode or operator has confirmed EPS anomaly via telemetry
- [ ] TT&C link is active — `ttc.link_status` (0x0501) = 1
- [ ] Current orbit prediction available — next sunlit/eclipse transition times known
- [ ] Power budget spreadsheet (ref: EOSAT1-PWR-BUD-001) accessible on console

## Procedure Steps

### Step 1 — Verify Safe Mode Entry and EPS State
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 25)
**Verify:** `obdh.mode` (0x0300) = 1 (SAFE) within 5s
**Verify:** `eps.bat_soc` (0x0101) — record current value (expected < 20%)
**Verify:** `eps.bus_voltage` (0x0105) — record current value (expected < 26.5V)
**Verify:** `eps.bat_voltage` (0x0100) — record current value
**GO/NO-GO:** Telemetry confirms low-energy state — proceed with load shedding

### Step 2 — Shed Non-Essential Loads: Payload OFF
**TC:** `PAYLOAD_SET_MODE(mode=0)` (Service 8, Subtype 1)
**Verify:** `payload.mode` (0x0600) = 0 (OFF) within 10s
**Verify:** `eps.power_cons` (0x0106) decreases by >= 15W within 15s
**GO/NO-GO:** Payload confirmed OFF and power consumption reduced — proceed

### Step 3 — Verify AOCS Is in Minimum Power Configuration
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 25)
**Verify:** `aocs.mode` (0x020F) = 2 (SAFE_POINT) — sun-pointing for maximum array illumination
**Note:** If AOCS is not in SAFE_POINT, command `AOCS_SET_MODE(mode=2)` and verify transition within 30s
**GO/NO-GO:** AOCS in SAFE_POINT ensuring optimal solar array orientation — proceed

### Step 4 — Assess Solar Array Performance
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 25)
**Verify:** `eps.sa_a_current` (0x0103) — record value (expected > 0.5A in sunlight)
**Verify:** `eps.sa_b_current` (0x0104) — record value (expected > 0.5A in sunlight)
**Verify:** `eps.power_gen` (0x0107) — record value (expected > 25W in sunlight, > 45W at optimal incidence)
**GO/NO-GO:** If both SA currents are < 0.2A during confirmed sunlit phase, suspect array failure — escalate to CON-008. Otherwise proceed.

### Step 5 — Monitor Battery Recovery During Sunlit Phase
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 25) — repeat every 60s
**Verify:** `eps.bat_soc` (0x0101) trending upward (minimum +1% per 10 minutes in sunlight)
**Verify:** `eps.bus_voltage` (0x0105) recovering above 27.0V
**Verify:** `eps.bat_temp` (0x0102) remains within 0 to 40 deg-C during charge
**GO/NO-GO:** SoC increasing and battery temp nominal — continue monitoring until SoC > 40%

### Step 6 — Re-Enable Loads: Payload to Standby
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1)
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10s
**Verify:** `eps.bat_soc` (0x0101) remains > 35% after load addition (allow 60s stabilisation)
**Verify:** `eps.power_cons` (0x0106) increase is consistent with payload standby draw (~8W)
**GO/NO-GO:** SoC stable above 35% with payload in standby — proceed

### Step 7 — Restore Nominal AOCS Mode
**TC:** `AOCS_SET_MODE(mode=3)` (Service 8, Subtype 1)
**Verify:** `aocs.mode` (0x020F) = 3 (NADIR_POINT) within 30s
**Verify:** `eps.bat_soc` (0x0101) remains > 30% after AOCS transition
**GO/NO-GO:** Attitude nominal and SoC stable — proceed to restore OBC mode

### Step 8 — Restore OBC to Nominal Mode
**TC:** `OBC_SET_MODE(mode=0)` (Service 8, Subtype 1)
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) within 10s
**Verify:** `eps.bat_soc` (0x0101) > 40%
**Verify:** `eps.bus_voltage` (0x0105) > 28.0V
**GO/NO-GO:** All subsystems nominal and energy budget positive — recovery complete

## Off-Nominal Handling
- If SoC drops below 10% at any step: Command `OBC_SET_MODE(mode=2)` for EMERGENCY, execute EMG-002
- If battery temperature exceeds 45 deg-C during charge: Command `HEATER_CONTROL(circuit=battery, on=false)` and reduce charge rate via `SET_PARAM(param_id=eps.charge_limit, value=0.5)`
- If solar array output remains < 0.2A for full sunlit pass: Execute CON-008 (Solar Array Degradation)
- If bus voltage drops below 25.0V: Execute EMG-002 immediately

## Post-Conditions
- [ ] `eps.bat_soc` (0x0101) > 40%
- [ ] `eps.bus_voltage` (0x0105) > 28.0V
- [ ] `obdh.mode` (0x0300) = 0 (NOMINAL)
- [ ] `payload.mode` (0x0600) >= 1 (STANDBY or higher)
- [ ] Power budget is positive with margin > 5W confirmed by Flight Dynamics

## Recovery Path — Re-Powering Shed Loads
After this procedure has stabilised the bus and the root cause is understood, **any subsystems whose power lines were de-energised during load shed must be brought back up in the controlled order defined by LEOP-007 (Sequential Power-On)**. Do **not** simply re-enable lines ad-hoc: LEOP-007 enforces the correct subsystem→line→mode sequencing, the inrush spacing, the thermal pre-conditioning of the OBC/battery heaters, and the AOCS/payload set_mode exemptions to the two-stage TC power gate. Cross-reference: `configs/eosat1/procedures/leop/sequential_power_on.md` (PROC-LEOP-007).

# Thermal Control Systems (TCS) Operability Review
## EO Satellite Mission Simulator

**Date:** 2026-04-06
**Reviewer:** Spacecraft Thermal Control Systems Expert
**Scope:** EOSAT-1 mission simulator thermal subsystem operability assessment per ECSS-E-ST-31C standards

---

## 1. Scope & Assumptions

### Mission Profile
- **Mission:** EOSAT-1 Earth observation satellite (Low Earth Orbit)
- **Orbital altitude:** ~500 km circular
- **Beta angle variation:** Seasonal, impacting eclipse duration and solar illumination
- **Mission phase focus:** LEOP commissioning, nominal eclipse operations, safe-mode thermal strategy

### Thermal Control Architecture
- **Active elements:** Battery heater (thermostat-controlled), OBC heater (manual via EPS power line), FPA cooler
- **Passive elements:** Multi-layer insulation (MLI) blankets, solar panel coatings, radiators
- **Thermal zones:** 6 solar panels (PX, MX, PY, MY, PZ, MZ), battery, OBC, FPA, thruster
- **Monitored components:** Battery, OBC, 6 solar panels, FPA (10 temperature points)
- **No thrusters on EOSAT-1:** Thruster zone is passive, heater always OFF

### Standards & References
- ECSS-E-ST-31C: Thermal control — general requirements and design (ESA, 2008)
- ECSS-E-31-04A: Thermal control — verification guidelines
- ECSS-E-HB-31A: Thermal control handbook
- PUS Service 20 (ESA PSS-07-208): Parameter Management for onboard parameter read/write
- PUS Service 8 (Function Management): Direct subsystem function execution

### Known Constraints
- Procedure steps reference func_id 33-35 for TCS commands, but these IDs are allocated to PAYLOAD in the catalog
- TCS commands are actually func_id 40-49 (per tc_catalog.yaml)
- S20 parameter support exists but may not cover all thermal setpoints
- MCS frontend thermal operator interface not yet reviewed (assumed minimal for this assessment)

---

## 2. Category 1 — Described, Implemented, Works

### Temperature Telemetry (HK Structure 3 — TCS)
**Status:** Fully implemented and operational

- **Temperature parameters (in SID 3):** All 10 zones documented and active
  - Panel temperatures: 0x0400-0x0405 (6 panels)
  - OBC/battery/FPA/thruster: 0x0406-0x0409
  - Reporting interval: 60 seconds (nominal for thermal monitoring)
  - Pack format: signed 16-bit, scale 100 (0.01°C resolution)

- **Heater status telemetry:**
  - htr_battery (0x040A): 1-byte boolean, on/off state
  - htr_obc (0x040B): 1-byte boolean, on/off state
  - cooler_fpa (0x040C): 1-byte boolean, on/off state
  - htr_thruster (0x040D): 1-byte boolean, always OFF for EOSAT-1

- **Phase 4 enhancements (duty cycles & power):**
  - htr_duty_battery (0x040E): duty cycle % over 10-minute sliding window
  - htr_duty_obc (0x040F): duty cycle %
  - htr_duty_thruster (0x0410): duty cycle %
  - htr_total_power_w (0x0411): aggregate heater power consumption (W)

- **Phase 5 advanced telemetry:**
  - decontamination_active (0x0418): mode flag for FPA decontamination
  - thermal_gradient (0x0419): max-min temperature spread across all zones

### Battery Heater Thermostat Control
**Status:** Fully implemented with ECSS compliance

- **Algorithm:** Hysteretic control with configurable ON/OFF setpoints
  - Default: ON at 1°C, OFF at 5°C (configurable via commands)
  - Prevents chatter near setpoint
  - Respects manual override flag (enters manual mode when commanded)

- **Fault injection support:**
  - Heater failure (stuck OFF)
  - Heater stuck ON (cannot be turned off)
  - Heater open-circuit (appears ON but provides no heat)
  - Sensor drift injection (simulates thermistor calibration error)

- **Operational test coverage:**
  - Test confirms heater ON when temp < on-point
  - Test confirms heater OFF when temp > off-point
  - Hysteresis behavior verified

### Thermal Physics Simulation
**Status:** Functional model with solar coupling and zone coupling

- **Heat transfer mechanisms:**
  - Conduction between battery-OBC (0.5 W/K)
  - Radiative cooling to 3K space sink (0.2 W/K per zone)
  - Solar heating from panel current (proxy for illumination ~30% conversion)
  - OBC internal dissipation (configurable, currently 0W nominal)

- **Time constants and capacitances:**
  - Panel zones: 500-800 s thermal time constant, 3000-5000 J/°C capacity
  - Electronics (battery/OBC): 900-1200 s, 800-2000 J/°C
  - FPA: 120 s, 100 J/°C (aggressive cooler response)
  - Realistic for CubeSat/SmallSat platform

- **Orbital state coupling:**
  - Eclipse detection: temperature environment drops to -30°C external, +10°C internal
  - Sunlit phase: environment -10°C base + solar coupling
  - Solar beta angle: weighted solar flux (cos(beta) factor)

### Manual Heater Control (OBC)
**Status:** Functional via EPS power line state

- **Design:** OBC heater reads EPS power line 6 status (0x0116)
  - No autonomous thermostat for OBC (operator controls via EPS PL switching)
  - Manual control strategy: allows operator to balance power budget vs. thermal margin

- **Telemetry link:** htr_obc (0x040B) reflects actual power line state

### FPA Cooler Control
**Status:** Fully implemented

- **Features:**
  - On/off control via S8 function (FPA_COOLER, func_id 43)
  - Target setpoint: -15°C default, configurable via command
  - Power consumption: ~15W when active (implicit in EPS load budget)
  - Thermal readiness events: emitted when FPA enters/exits operating range

### Procedures & Operator Workflows
**Status:** Well-documented, contingency coverage present

- **Eclipse transition procedure (PROC-EPS-NOM-001):**
  - Pre-eclipse heater setpoint adjustment step
  - Battery SoC verification (>60%)
  - Cooler disable to conserve power
  - Temperature monitoring during eclipse
  - Heater auto-mode restoration post-eclipse

- **Health check procedures:**
  - Routine temperature monitoring (routine_health_check.md)
  - Heater state correlation with orbital position
  - Thermal gradient monitoring
  - Power consumption trending

- **Contingency procedures:**
  - Thermal runaway response
  - Heater failure recovery
  - Safe-mode thermal strategy references

---

## 3. Category 2 — Described but Not Yet Implemented

### Radiator Temperature Monitoring
**Status:** Partially stubbed, no physical model

- **Parameters defined but not functional:**
  - 0x0420: tcs.temp_radiator_n (North radiator, marked stub: true)
  - 0x0421: tcs.temp_radiator_s (South radiator, marked stub: true)
  - No update logic in tcs_basic.py model
  - No heat dissipation to radiators in thermal coupling

- **Impact:** Nominal missions don't strictly require radiator telemetry (passive elements), but for advanced thermal analysis (MLI degradation, contamination assessment), these would support longer-term trending.

### Comprehensive Setpoint Telemetry (Read-Back)
**Status:** Parameters stubbed, no mechanism to query current setpoints via S20

- **Parameters defined:**
  - 0x0330: tcs.setpoint_bat (Battery heater setpoint, marked stub: true)
  - 0x0331: tcs.setpoint_obc (OBC heater setpoint, marked stub: true)
  - 0x0326: tcs.htr_duty_total (Total duty, marked stub: true)

- **Missing:** S20 read-back of dynamic setpoints after HEATER_SET_SETPOINT command
  - Operator can command setpoint change (func_id 44) but cannot easily verify it was applied
  - Workaround: operator must correlate heater on/off behavior with telemetry

### Advanced Thermal FDIR Automation
**Status:** Events are generated, but Event-Action (S19) rules not pre-configured

- **Events supported:**
  - Temperature warning/alarm (battery overtemp/undertemp)
  - Heater stuck-on/failed
  - Thermal runaway detection (>2 deg/min)
  - FPA thermal readiness transitions
  - Decontamination mode changes

- **Gaps:**
  - No S19 pre-configured rules for automatic heater correction
  - No automated load shedding on thermal runaway
  - Operator must manually respond to events (FDIR is advisory, not autonomous)

### Payload Cooler Setpoint Command (Soft Control)
**Status:** S8 command exists (PAYLOAD_COOLER_SETPOINT, func_id 36), but no TCS integration

- **Issue:** Command targets payload subsystem, not TCS
- **Current state:** Cooler is on/off only; target temperature is hardcoded (-15°C)
- **Need:** Deferred for Phase 5+ — payload cooler should feed back to TCS power calculations

### Multi-Zone Heater Control
**Status:** Commands exist for independent circuits, but no zone-level S2 device access

- **Paradox:** tcs_basic.py state includes 10 device zones (0x0300-0x0309), but:
  - No heating power assigned to individual zones (only 3 heater circuits: battery, OBC, thruster)
  - S2 commands target device IDs in range 0x0300-0x0309 (10 zones), but model has only 3 circuits
  - Mismatch between declared devices and actual thermal power elements

---

## 4. Category 3 — Not Yet Described but Needed

### Safe-Mode Thermal Strategy (Specification Gap)
**Status:** Procedures reference safe-mode thermal strategy, but not formally defined

- **What we know:** eclipse_transition.md mentions "safe-mode entry" contingency link to PROC-AOCS-OFF-001
- **What's missing:** Formal definition of TCS response in safe mode:
  - Should heaters remain thermostat-controlled or revert to conservative setpoints?
  - Should cooler remain on or turn off to preserve power?
  - What is the minimum temperature set that safe-mode must maintain?
  - Is there an autonomous overheat shutdown?

- **Recommendation:** Define a safe-mode thermal envelope per ECSS-E-ST-31C Section 8.3.2 (safe-mode design requirements)

### Heater Power Budgeting & Duty Cycle Limits
**Status:** Command exists (TCS_SET_HEATER_DUTY_LIMIT, func_id 46), but no nominal limits documented

- **What's in code:** Duty cycle tracking (0-100% over 10-minute window), limit enforcement logic present
- **What's missing:**
  - Pre-mission thermal margin analysis (power budget vs. orbit, beta angle, mission mode)
  - Recommended duty limits per mission phase (LEOP vs. nominal vs. contingency)
  - Thermal sensitivity analysis (e.g., "battery heater +20% duty if beta angle > 60 deg")

- **Need:** Phase 5 analysis to establish operational duty limits for each heater circuit

### Thermal Vacuum Test Verification
**Status:** No test/verification parameters for thermal cycling

- **Parameters needed:**
  - Thermal cycle count / life estimate (thermal stress)
  - MLI degradation tracking (optical properties vs. contamination)
  - Heater cycling events (for component life prediction)
  - Sensor calibration health (drift rate monitoring)

### Coordinated EPS-TCS Command Sequencing
**Status:** Procedures describe sequencing, but no automated interlocks

- **Example:** FPA cooler power line and TCS cooler on/off state are independent
  - EPS can shut off cooler power line while TCS thinks cooler is ON
  - No cross-check in operator display or FDIR

- **Need:** S19 Event-Action rules to ensure power line state matches TCS state

### Thermal Modeling Validation Data
**Status:** Model parameters (tau, C, conductance) hardcoded, no justification

- **Parameters present:** 800 J/°C for battery, 0.5 W/K battery-OBC coupling, etc.
- **Missing:** Correlation to actual spacecraft design (mass, material, geometry)
- **Impact:** Procedures blindly follow model assumptions without verifiable link to engineering

---

## 5. Category 4 — Implemented but Not Helpful for This Mission

### Thruster Heater Circuit
**Status:** Fully implemented, but EOSAT-1 has no thrusters

- **Code:** htr_thruster state, thermostat, power calculations all present
- **Reality:** tcs_basic.py line 405: `s.htr_thruster = False  # Always off — no thrusters on EOSAT-1`
- **Bloat:** ~100 lines of code managing a circuit that never activates
- **Recommendation:** Remove for EOSAT-1 or flag as "unused, reserved for future expansion"

### OBC Internal Heat Injection (Failure Mode)
**Status:** Injection mechanism works, but no nominal OBC dissipation

- **Code:** `obc_internal_heat_w` field, inject_failure("obc_thermal", heat_w=30.0) supported
- **Issue:** Used only for failure simulation; nominal OBC dissipation is 0W
  - Real OBC dissipates ~10-20W nominal
  - This causes OBC temperature to respond *only* to ambient + conduction from battery
  - Unrealistic thermal coupling makes eclipse scenarios non-representative

- **Recommendation:** Add nominal OBC dissipation (estimate ~15W) to thermal model

### Device Access (S2) for TCS
**Status:** S2 device on/off commands exist for heater zones, but unexercised

- **Declarations:** 10 TCS device zones (0x0300-0x0309) + decontam heater (0x030B)
- **Code:** S2.1/S2.5 handlers route device commands to tcs.set_device_state()
- **Reality:** No procedure calls S2_TCS_HEATER_ZONE_*, all heater control is via S8 functions
- **Impact:** Redundant interface adds confusion without operational benefit

---

## 6. Category 5 — Inconsistent or Incoherent Implementation

### Procedure/Catalog Func_ID Mismatch (CRITICAL)
**Severity:** High — procedures are inoperable as written

**Problem:**
- Eclipse transition procedure (PROC-EPS-NOM-001) references:
  - Step 2: `HEATER_SET_SETPOINT(circuit=0, on_temp=3.0, off_temp=8.0)` (func_id 34)
  - Step 2: `HEATER_SET_SETPOINT(circuit=1, on_temp=8.0, off_temp=13.0)` (func_id 34)
  - Step 3: `FPA_COOLER(on=0)` (func_id 33)
  - Step 8: `HEATER_AUTO_MODE(circuit=0)` (func_id 35)

**Actual catalog (tc_catalog.yaml):**
  - func_id 33: PAYLOAD_SET_BAND_CONFIG (payload, NOT thermal)
  - func_id 34: PAYLOAD_SET_INTEGRATION_TIME (payload, NOT thermal)
  - func_id 35: PAYLOAD_SET_GAIN (payload, NOT thermal)
  - func_id 40: HEATER_BATTERY (correct, but not 34)
  - func_id 43: FPA_COOLER (correct, but not 33)
  - func_id 44: HEATER_SET_SETPOINT (correct, but not 34)
  - func_id 45: HEATER_AUTO_MODE (correct, but not 35)

**Impact:** Any operator following procedure as written will control payload, not thermal system. Critical operational error.

**Fix Required:**
1. Update procedures to use correct func_ids: 44 (setpoint), 45 (auto), 43 (cooler)
2. OR: Reallocate func_ids in catalog to match procedures (not recommended — cascading impact)

### EPS vs. TCS Heater State Inconsistency
**Severity:** Medium — operational complexity

**Problem:**
- Battery heater has dual control:
  1. TCS model: autonomous thermostat (htr_battery_manual flag, setpoint on/off logic)
  2. EPS model: battery_heater_on state (separate S8 command in eps_basic.py)
  - Telemetry: tcs.htr_battery (0x040A) and eps.battery_heater_on (0x0138) report independently
  - No enforcement that they stay synchronized
  - Operator confusion: which one controls actual heater?

- OBC heater coupling to EPS power line:
  1. TCS reads EPS PL 6 state (0x0116) at each tick
  2. Operator can turn OBC heater "on" via TCS S8 command, but thermal control has no power line write
  3. Real heater state depends on EPS power line, not TCS command

**Impact:**
- Telemetry may show conflicting heater states
- Procedures don't clarify whether to command TCS or EPS for battery heater
- Safe-mode recovery ambiguous: does TCS command override EPS power line?

**Fix Required:**
- Clarify in manual: TCS battery heater is primary (thermostat override via S8), EPS status is read-only confirmation
- OR: Eliminate duplicate EPS battery heater state, command only via TCS
- Document that OBC heater is controlled *exclusively* via EPS power line, not TCS

### Setpoint Command Syntax Ambiguity
**Severity:** Low — documentation issue

**Problem:**
- HEATER_SET_SETPOINT command (func_id 44) takes `circuit` (0=bat, 1=obc, 2=thrusters)
- But HEATER_AUTO_MODE (func_id 45) also takes `circuit`
- No validation in code that circuit value is in range 0-2
- What happens if circuit=99 is sent? Returns success silently or error?

**Impact:** Operator may send invalid circuit and miss error message (if not logged visibly)

**Fix:** Add bounds checking and S1 failure report if circuit out of range

### Device State (S2) vs. Functional Control (S8) Collision
**Severity:** Low — design confusion

**Problem:**
- S2 commands can turn heater zones on/off at device level (device_states dict in TCSState)
- S8 commands control heaters via functional commands (heater, set_setpoint, auto_mode)
- Both map to the same underlying state (htr_battery, htr_obc, etc.), but through different paths
- No mutual exclusion: if S2 sets device OFF, does S8 command still execute? Does S8 command re-enable?

**Code:** device_states dict exists but appears to be unused in tick() logic. The actual heater state (htr_battery) is independent.

**Impact:** Low risk for EOSAT-1 (procedures use S8, not S2), but potential confusion for future operators

**Fix:** Document that S2 device access is NOT used for TCS heaters; S8 is primary interface

---

## 7. Top-5 Prioritised Defects

### Defect 1: CRITICAL — Procedure Func_ID Mismatch
**Title:** Eclipse Transition Procedure References Wrong PUS Function IDs for Heater/Cooler Commands
**Severity:** CRITICAL (Operability-blocking)
**Category:** Category 5 — Inconsistent implementation

**Description:**
Procedures (PROC-EPS-NOM-001, eclipse_transition.md) command HEATER_SET_SETPOINT and FPA_COOLER using func_id 33-35, but these IDs are allocated to PAYLOAD subsystem in tc_catalog.yaml. Correct TCS function IDs are 40-45. Any operator following documented procedure will send payload commands instead of thermal commands, resulting in mission failure during eclipse (heaters not armed, payload misconfiguration). This blocks nominal LEOP and eclipse operations.

**Files Affected:**
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/configs/eosat1/procedures/nominal/eclipse_transition.md` (lines 41, 44, 65, 72, 125)
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/configs/eosat1/commands/tc_catalog.yaml` (lines 410-445, payload func_ids 33-35 conflict)

**Suggested Fix:**
1. Update eclipse_transition.md:
   - Line 41: Change "func_id 34" → "func_id 44"
   - Line 44: Change "func_id 33" → "func_id 43"
   - Line 65: Change "func_id 34" → "func_id 44"
   - Line 72: Change "func_id 33" → "func_id 43"
   - Line 125: Change "func_id 35" → "func_id 45"
2. Verify all other procedures use correct func_ids (grep for func_id 33-35 in procedures/)
3. Update command reference manual (09_command_reference.md) if it exists

---

### Defect 2: HIGH — Missing OBC Nominal Dissipation
**Title:** OBC Internal Heat Dissipation Not Modeled, Causing Unrealistic Thermal Response During Eclipse
**Severity:** HIGH (Thermal margin underestimation)
**Category:** Category 4 — Not helpful; Category 5 — Incoherent

**Description:**
Real OBC dissipates ~15-20W continuously. Current model sets obc_internal_heat_w = 0.0 by default. This makes OBC temperature in eclipse scenarios respond *only* to battery thermal coupling and ambient sink, underestimating the actual overheat risk. During LEOP/commissioning, operators will not see representative thermal stress; procedures calibrated to model temps will have insufficient margin in flight.

**Files Affected:**
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/tcs_basic.py` (line 29, initialization; line 398, tick() logic)

**Suggested Fix:**
1. In TCSState.__init__, change `obc_internal_heat_w: float = 0.0` → `obc_internal_heat_w: float = 15.0`
2. Document assumption: "OBC nominal dissipation = 15W (processor + memory + bus activity)"
3. In thermal coupling, add OBC dissipation heat to energy balance: `heat_input = obc_internal_heat_w + conduction_from_battery`
4. Validate tick() line 398 includes the dissipation term (currently: `(pwr+s.obc_internal_heat_w)` — already correct, just missing nominal value)
5. Update procedures to reflect realistic OBC temps during eclipse (may require heater setpoint adjustment)

---

### Defect 3: HIGH — Setpoint Telemetry Parameters Stubbed, No Readback Mechanism
**Title:** Operator Cannot Verify Heater Setpoint After Command; S20 Parameter Readback Not Implemented
**Severity:** HIGH (Operator verification gap)
**Category:** Category 2 — Described but not implemented

**Description:**
Parameters 0x0330 (setpoint_bat) and 0x0331 (setpoint_obc) exist in parameter list but are marked `stub: true` and have no update logic in tcs_basic.py. After operator commands HEATER_SET_SETPOINT (func_id 44), they cannot query back the new values via S20. Operator must infer setpoint from heater behavior (on/off cycling pattern), which is error-prone and violates ECSS telemetry completeness requirements (ECSS-E-ST-31C 4.3.5).

**Files Affected:**
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/configs/eosat1/telemetry/parameters.yaml` (lines 330-331, both marked stub: true)
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/tcs_basic.py` (no lines writing 0x0330/0x0331 to shared_params)

**Suggested Fix:**
1. In tcs_basic.py tick() method (around line 447), add:
   ```python
   shared_params[0x0330] = s.htr_battery_setpoint_on_c
   shared_params[0x0331] = s.htr_obc_setpoint_on_c
   ```
2. Remove `stub: true` from parameters.yaml lines 330-331
3. Add corresponding OFF setpoints (0x0332, 0x0333) if operator needs both
4. Test: Command HEATER_SET_SETPOINT, then request HK to verify new setpoint appears in telemetry

---

### Defect 4: MEDIUM — EPS/TCS Battery Heater Dual Control Ambiguity
**Title:** Battery Heater Has Dual, Unsynchronized Control Paths (EPS and TCS); Operator Manual Unclear
**Severity:** MEDIUM (Procedural confusion)
**Category:** Category 5 — Inconsistent implementation

**Description:**
Battery heater can be controlled via two independent S8 commands:
- TCS command: `HEATER_BATTERY` (func_id 40, targets tcs subsystem)
- EPS command: `set_battery_heater` (eps_basic.py, separate subsystem state)
- Two separate telemetry outputs: eps.battery_heater_on (0x0138) vs. tcs.htr_battery (0x040A)

During commissioning, operator may command TCS to turn heater on, but not realize EPS has separate state. If EPS is in manual "battery heater OFF" mode, the TCS command has no effect. Conversely, EPS may have heater ON while TCS thermostat is disabled (manual mode). Procedures don't clarify which is authoritative.

**Files Affected:**
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (lines with battery_heater_on state)
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/tcs_basic.py` (htr_battery state, manual flag)
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/configs/eosat1/procedures/nominal/*.md` (all eclipse/thermal procedures)

**Suggested Fix:**
1. In manual (09_command_reference.md or operational handbook), add explicit guidance:
   - "Battery heater is TCS-primary. Use HEATER_BATTERY command (S8, func_id 40) to toggle; this sets manual override in TCS."
   - "EPS battery_heater_on status (0x0138) reflects EPS power line state for diag purposes only."
   - "If heater not responding to TCS command, check EPS power line 5 (0x0115) status."
2. OR: Remove EPS battery heater state entirely, make EPS read-only (monitor battery_heater_on from TCS).
3. Add cross-check in FDIR: if tcs.htr_battery=ON but eps.pl_htr_bat=OFF, generate advisory event.

---

### Defect 5: MEDIUM — Unexercised S2 Device Access for Heater Zones Creates Interface Confusion
**Title:** S2 Heater Zone On/Off Commands Exist but Unused; No Procedures Reference Them
**Severity:** MEDIUM (Interface bloat, future maintenance confusion)
**Category:** Category 5 — Inconsistent implementation

**Description:**
tc_catalog.yaml declares 10 S2 device commands for TCS heater zones (0x0300-0x0309) and decontam heater (0x030B). ServiceDispatcher has handler logic (lines 161-181). However, tcs_basic.py device_states dict is never read during thermal calculations — it exists but is unused. All actual heater control is via S8 functional commands. This creates two parallel interfaces to the same hardware, introducing:
1. Confusion: which interface should operator use?
2. Inconsistency: if operator sends S2 AND S8 commands, which wins?
3. Maintenance burden: dead code that may require updates if thermal model changes

**Files Affected:**
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/configs/eosat1/commands/tc_catalog.yaml` (lines 179-245, S2 TCS zone commands)
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/tcs_basic.py` (lines 85-99, device_states dict; unused in tick)
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/service_dispatch.py` (lines 161-181, S2 device routing)

**Suggested Fix:**
1. Add note in command reference: "S2 device commands (0x0300-0x0309) are NOT used for EOSAT-1 thermal control. Use S8 functional commands (HEATER_*, func_id 40-49) instead."
2. Option A (preferred for EOSAT-1): Remove S2_TCS_HEATER_ZONE_* entries from tc_catalog.yaml
3. Option B (if planning multi-zone future missions): Document intended zone-level control architecture and implement actual heat distribution in model
4. Remove device_states dict from TCSState if not used elsewhere

---

## 8. Parameter/Command Coverage Table

### Telemetry Parameters (S3 HK, SID 3 — TCS)
| Parameter | ID | Name | Subsystem | HK? | S20 Read? | Description | Notes |
|---|---|---|---|---|---|---|---|
| Panel PX Temp | 0x0400 | tcs.temp_panel_px | tcs | YES | YES | +X panel temperature | SID 3, scale 100 |
| Panel MX Temp | 0x0401 | tcs.temp_panel_mx | tcs | YES | YES | -X panel temperature | SID 3 |
| Panel PY Temp | 0x0402 | tcs.temp_panel_py | tcs | YES | YES | +Y panel temperature | SID 3 |
| Panel MY Temp | 0x0403 | tcs.temp_panel_my | tcs | YES | YES | -Y panel temperature | SID 3 |
| Panel PZ Temp | 0x0404 | tcs.temp_panel_pz | tcs | YES | YES | +Z panel temperature | SID 3 |
| Panel MZ Temp | 0x0405 | tcs.temp_panel_mz | tcs | YES | YES | -Z panel temperature | SID 3 |
| OBC Temp | 0x0406 | tcs.temp_obc | tcs | YES | YES | Onboard computer temperature | SID 3, critical for safe-mode |
| Battery Temp | 0x0407 | tcs.temp_battery | tcs | YES | YES | Battery thermal sensor | SID 3, heater control feedback |
| FPA Temp | 0x0408 | tcs.temp_fpa | tcs | YES | YES | Focal plane array temperature | SID 3, imaging readiness |
| Thruster Temp | 0x0409 | tcs.temp_thruster | tcs | YES | YES | Propellant line temp (unused) | SID 3, always cold (no thrusters) |
| Heater Battery | 0x040A | tcs.htr_battery | tcs | YES | YES | Battery heater on/off state | SID 3, boolean |
| Heater OBC | 0x040B | tcs.htr_obc | tcs | YES | YES | OBC heater on/off state | SID 3, boolean |
| Cooler FPA | 0x040C | tcs.cooler_fpa | tcs | YES | YES | FPA cooler on/off state | SID 3, boolean |
| Heater Duty Bat | 0x040E | tcs.htr_duty_battery | tcs | YES | YES | Battery heater duty cycle % | SID 3, Phase 4 |
| Heater Duty OBC | 0x040F | tcs.htr_duty_obc | tcs | YES | YES | OBC heater duty cycle % | SID 3, Phase 4 |
| Heater Duty Thrust | 0x0410 | tcs.htr_duty_thruster | tcs | YES | YES | Thruster heater duty % | SID 3, Phase 4, always 0 |
| Total Heater Power | 0x0411 | tcs.htr_total_power_w | tcs | YES | YES | Sum of active heaters (W) | SID 3, Phase 4, power budget check |
| Panel PX Temp (ILM) | 0x0412 | tcs.temp_panel_px_ilm | tcs | NO | YES | Panel PX illumination-coupled | On-demand, Phase 4 |
| Panel MX Temp (ILM) | 0x0413 | tcs.temp_panel_mx_ilm | tcs | NO | YES | Panel MX illumination-coupled | On-demand |
| Panel PY Temp (ILM) | 0x0414 | tcs.temp_panel_py_ilm | tcs | NO | YES | Panel PY illumination-coupled | On-demand |
| Panel MY Temp (ILM) | 0x0415 | tcs.temp_panel_my_ilm | tcs | NO | YES | Panel MY illumination-coupled | On-demand |
| Panel PZ Temp (ILM) | 0x0416 | tcs.temp_panel_pz_ilm | tcs | NO | YES | Panel PZ illumination-coupled | On-demand |
| Panel MZ Temp (ILM) | 0x0417 | tcs.temp_panel_mz_ilm | tcs | NO | YES | Panel MZ illumination-coupled | On-demand |
| Decontam Active | 0x0418 | tcs.decontamination_active | tcs | YES | YES | Decontamination mode flag | SID 3, Phase 5 |
| Thermal Gradient | 0x0419 | tcs.thermal_gradient | tcs | YES | YES | Max-min temp spread (K) | SID 3, Phase 5 |
| Duty Total | 0x0326 | tcs.htr_duty_total | tcs | NO | YES | Total duty cycle (stub) | On-demand, not implemented |
| Setpoint Bat | 0x0330 | tcs.setpoint_bat | tcs | NO | YES | Battery heater ON setpoint (stub) | On-demand, not implemented (DEFECT 3) |
| Setpoint OBC | 0x0331 | tcs.setpoint_obc | tcs | NO | YES | OBC heater ON setpoint (stub) | On-demand, not implemented (DEFECT 3) |
| Radiator N | 0x0420 | tcs.temp_radiator_n | tcs | NO | YES | North radiator temp (stub) | On-demand, not implemented |
| Radiator S | 0x0421 | tcs.temp_radiator_s | tcs | NO | YES | South radiator temp (stub) | On-demand, not implemented |

**Summary:**
- **Periodic HK (SID 3):** 18 parameters, all implemented and functioning
- **On-demand (S20):** 6 parameters functional, 3 stubbed (radiators, duty total, setpoints)
- **S20 Read capability:** All implemented parameters are readable
- **S20 Write capability:** NONE — all thermal parameters are read-only (control is S8 function-based)

---

### Control Commands (S8 Function Management, Subtype 1)

| Command | Func ID | Service | Fields | Implemented? | Procedures Use? | Notes |
|---|---|---|---|---|---|---|
| HEATER_BATTERY | 40 | S8.1 | on (bool) | YES | eclipse_transition.md (indirect via setpoint) | Manual control; overrides auto thermostat |
| HEATER_OBC | 41 | S8.1 | on (bool) | YES | Not directly (via EPS PL) | Not thermostat-controlled |
| HEATER_THRUSTER | 42 | S8.1 | on (bool) | YES | Never (no thrusters) | Always OFF for EOSAT-1 |
| FPA_COOLER | 43 | S8.1 | on (bool) | YES | eclipse_transition.md step 3 (func_id 33 — **WRONG**) | Payload thermal control |
| HEATER_SET_SETPOINT | 44 | S8.1 | circuit (0-2), on_temp (f32), off_temp (f32) | YES | eclipse_transition.md step 2 (func_id 34 — **WRONG**) | Sets hysteresis for automatic control |
| HEATER_AUTO_MODE | 45 | S8.1 | circuit (0-2) | YES | eclipse_transition.md step 8 (func_id 35 — **WRONG**) | Disables manual, re-engages thermostat |
| TCS_SET_HEATER_DUTY_LIMIT | 46 | S8.1 | circuit (0-2), max_duty_pct (0-100) | YES | No procedures | Limits thermal cycling stress |
| TCS_DECONTAMINATION_START | 47 | S8.1 | target_temp_c (f32) | YES | No procedures | FPA decontam heating (not in nominal mission) |
| TCS_DECONTAMINATION_STOP | 48 | S8.1 | none | YES | No procedures | Abort decontam |
| TCS_GET_THERMAL_MAP | 49 | S8.1 | none | YES | No procedures | Query full thermal state |

**Summary:**
- **Thermal-specific S8 commands:** 10 total (func_id 40-49)
- **Procedures reference correct func_ids?** NO — eclipse_transition.md uses wrong IDs (33-35)
- **All commands routed through service_dispatch.py?** YES, via _handle_s8() dispatching
- **Bounds checking?** Minimal — circuit parameter not validated to 0-2 range
- **S1 reporting?** Supported (success/failure reports generated)

---

### Device Access Commands (S2 Device Access, Subtype 1)
| Command | Device ID | Service | State | Implemented? | Procedures Use? | Notes |
|---|---|---|---|---|---|
| S2_TCS_HEATER_ZONE_1 | 0x0300 | S2.1 | on/off | YES (routed) | NO | Unused for EOSAT-1 (DEFECT 5) |
| S2_TCS_HEATER_ZONE_2 | 0x0301 | S2.1 | on/off | YES | NO | Unused (9 more zones similar) |
| ... (zones 3-9) | 0x0302-0x0309 | S2.1 | on/off | YES | NO | All unused |
| S2_TCS_DECONTAM_HEATER | 0x030B | S2.1 | on/off | YES | NO | Unused; no zone-level control model |
| S2_TCS_THERMISTOR_ARRAY | 0x030A | S2.1 | on/off | YES | NO | Unused; device_states exists but untouched |

**Summary:**
- **S2 device commands for TCS:** 12 total (zones 1-10, thermistor, decontam)
- **Routing:** ServiceDispatcher._handle_s2() correctly routes to tcs.set_device_state()
- **Actual model use:** NONE — device_states dict exists but tick() does not read it
- **Procedures:** NONE reference S2 device commands for thermal control

---

### Housekeeping Structures (S3)
| SID | Name | Interval | Parameters | Thermal? | Status |
|---|---|---|---|---|---|
| 1 | EPS | 1.0 s | 54 params | YES (bat_temp, bat_heater, power lines) | Operational |
| 2 | AOCS | 4.0 s | 50+ params | Minor (RW temps on-demand) | Operational |
| 3 | TCS | 60.0 s | 18 params (all thermal) | YES (primary) | Operational |
| 4 | Platform | 8.0 s | 20+ params | Minor (OBC temp on-demand) | Operational |
| 5 | Payload | 8.0 s | 20+ params | YES (FPA temp + cooler) | Operational |
| 6 | TTC | 8.0 s | 20+ params | NO | Operational |
| 11 | Beacon | 30.0 s | 6 params | YES (bat, OBC mode) | Operational |

**Summary:**
- **Thermal-specific HK:** SID 3 (TCS) — all 18 params implemented and in use
- **Thermal monitoring intervals:**
  - SID 3 (TCS): 60 s (suitable for eclipse/thermal cycling monitoring)
  - SID 1 (EPS): 1 s (battery voltage/current; used for power budget cross-checks)
  - SID 11 (Beacon): 30 s (minimal set; suitable for boot/early LEOP)
- **Operator can change interval:** S3.31 command to modify SID 3 rate dynamically

---

## 9. Recommendations & Next Steps

### Priority 1 (Operability-blocking)
1. **Fix procedure func_ids** (Defect 1): Update all thermal procedures to use correct S8 function IDs (40-49, not 33-35)
2. **Implement setpoint telemetry** (Defect 3): Add 0x0330/0x0331 write in tcs_basic.py tick() so operators can verify heater setpoints after commands
3. **Document EPS/TCS battery heater control authority** (Defect 4): Clarify in operator manual which control path is used for each mission phase

### Priority 2 (Thermal realism)
4. **Add OBC nominal dissipation** (Defect 5): Set obc_internal_heat_w = 15W to make eclipse scenarios representative
5. **Clarify device/function interface** (Defect 5): Document that S2 device commands are NOT used for EOSAT-1; remove from procedures/training materials

### Priority 3 (ECSS compliance)
6. **Formalize safe-mode thermal strategy:** Define minimum temperature envelope, heater duty limits, cooler behavior when power constrained
7. **Implement S19 autonomous thermal FDIR:** Pre-configure Event-Action rules for heater stuck-on, thermal runaway, and FPA readiness transitions
8. **Add thermal margin analysis:** Document heater power budget vs. orbital beta angle, eclipse duration, mission mode

### Phase 5 Enhancements (not blocking LEOP)
9. Implement radiator temperature monitoring (0x0420/0x0421) if MLI degradation tracking is needed
10. Add multi-zone heater control model if future missions require distributed thermal management
11. Integrate cooler setpoint command from payload (currently hardcoded to -15°C)

---

## 10. References

### Standards & Documents
- ECSS-E-ST-31C (2008): Thermal control — general requirements. [https://ecss.nl/standard/ecss-e-st-31c-thermal-control/](https://ecss.nl/standard/ecss-e-st-31c-thermal-control/)
- ECSS-E-HB-31A: Thermal control handbook (ESA)
- ESA PSS-07-208: PUS Standard (Packet Utilization Standard), Service 8 & 20

### Codebase References
- Thermal Model: `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/tcs_basic.py`
- Procedures: `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/configs/eosat1/procedures/nominal/*.md`
- Command Catalog: `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/configs/eosat1/commands/tc_catalog.yaml`
- Telemetry Config: `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/configs/eosat1/telemetry/hk_structures.yaml`, `parameters.yaml`
- Service Dispatch: `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/service_dispatch.py`

---

**Report End**
**Reviewer:** Spacecraft Thermal Control Systems Expert
**Date:** 2026-04-06

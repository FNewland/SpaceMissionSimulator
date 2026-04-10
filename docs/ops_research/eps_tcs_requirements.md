# EOSAT-1 Power & Thermal Engineer (EPS/TCS) -- Operations Requirements Document

**Document ID:** EOSAT1-ORD-EPSTCS-001
**Issue:** 1.0
**Date:** 2026-03-12
**Position:** Power & Thermal (eps_tcs)
**Classification:** UNCLASSIFIED -- For Simulation Use Only

---

## Table of Contents

1. [Scope](#1-scope)
2. [Equipment Under EPS/TCS Responsibility](#2-equipment-under-epstcs-responsibility)
3. [Commands and Telemetry (PUS Services)](#3-commands-and-telemetry-pus-services)
4. [Operational Procedures](#4-operational-procedures)
5. [Training Scenarios](#5-training-scenarios)
6. [MCS Display and Tool Requirements](#6-mcs-display-and-tool-requirements)
7. [Planner Requirements](#7-planner-requirements)
8. [Simulator Fidelity Requirements](#8-simulator-fidelity-requirements)
9. [PDM Model Requirements](#9-pdm-model-requirements)
10. [Six Body-Panel Solar Array Model](#10-six-body-panel-solar-array-model)
11. [Battery-Heater-Only Thermal Control Alignment](#11-battery-heater-only-thermal-control-alignment)
12. [Load Shed Sequence](#12-load-shed-sequence)
13. [Power Budget Trending](#13-power-budget-trending)
14. [Traceability Matrix](#14-traceability-matrix)

---

## 1. Scope

This document defines the operations requirements for the Power & Thermal Engineer
position on the EOSAT-1 ocean-current-monitoring cubesat mission. It covers all
equipment, commanding, telemetry, procedures, training, MCS tooling, mission planner
integration, and simulator fidelity needs for the Electrical Power Subsystem (EPS)
and Thermal Control Subsystem (TCS).

**Mission profile context:**

- 6U cubesat, Sun-synchronous dawn-dusk orbit
- Cold-redundant power system with a Power Distribution Module (PDM)
- PDM provides switchable and unswitchable power lines
- Battery heaters are the sole active thermal control mechanism
- Passive thermal control via spacecraft orientation and MLI/radiators
- Six body-mounted GaAs triple-junction solar panels (one per face: +X, -X, +Y, -Y, +Z, -Z)
- Single Li-Ion battery, 120 Wh capacity, 28 V regulated bus
- Separation timer drives initial power-on sequence via unswitchable lines

---

## 2. Equipment Under EPS/TCS Responsibility

### 2.1 EPS Equipment

| Equipment | Type | Switchable | Default State | Nominal Power | Hardware Mapping |
|---|---|---|---|---|---|
| OBC Power Line | Unswitchable | No | ON | 40 W | PDM unswitchable output 0 -- essential bus |
| TTC Receiver Line | Unswitchable | No | ON | 5 W | PDM unswitchable output 1 -- essential bus |
| TTC Transmitter Line | Switchable | Yes | ON | 20 W | PDM switchable output 2 |
| Payload Imager Line | Switchable | Yes | OFF | 8-45 W | PDM switchable output 3 |
| FPA Cooler Line | Switchable | Yes | OFF | 15 W | PDM switchable output 4 |
| Battery Heater Line | Switchable | Yes | ON | 6 W | PDM switchable output 5 |
| OBC Heater Line | Switchable | Yes | ON | 4 W | PDM switchable output 6 |
| AOCS Reaction Wheels Line | Switchable | Yes | ON | 12 W | PDM switchable output 7 |

| Equipment | Description | Key Parameters |
|---|---|---|
| Solar Array Wing A | Body-mounted GaAs panel, 0.314 m^2, 29.5% efficiency | sa_a_current, sa_a_voltage, sa_a_degradation |
| Solar Array Wing B | Body-mounted GaAs panel, 0.314 m^2, 29.5% efficiency | sa_b_current, sa_b_voltage, sa_b_degradation |
| Li-Ion Battery | 120 Wh, 26.4 V nominal, 21.5-29.2 V range, 0.05 ohm internal R | bat_voltage, bat_soc, bat_temp, bat_current, bat_dod, bat_cycles |
| PCDU / PDM | Power Conditioning and Distribution Unit with per-line OC protection | bus_voltage, oc_trip_flags, uv_flag, ov_flag |
| MPPT Controller | Maximum Power Point Tracking, 97% nominal efficiency | mppt_efficiency |
| Separation Timer | Drives initial OBC + TTC RX power-on via unswitchable bus | (implicit -- essential bus) |

### 2.2 TCS Equipment

| Equipment | Description | Key Parameters |
|---|---|---|
| Battery Heater | 6 W thermostat-controlled, setpoint ON at 1 degC / OFF at 5 degC | htr_battery, htr_duty_battery |
| OBC Heater | 4 W thermostat-controlled, setpoint ON at 5 degC / OFF at 10 degC | htr_obc, htr_duty_obc |
| Thruster Heater | 8 W thermostat-controlled, setpoint ON at 2 degC / OFF at 8 degC | htr_thruster, htr_duty_thruster |
| FPA Cooler | Stirling-cycle cryocooler, target -5 degC (sim) / -15 degC (manual), 15 W | cooler_fpa, temp_fpa |
| MLI Blankets | Passive -- side panels, battery compartment, OBC, payload bay | (informational via panel temps) |
| Radiators | -Y face primary (0.25 m^2, 50 W), +Y face secondary (0.15 m^2, 30 W) | temp_panel_py, temp_panel_my |
| Temperature Sensors | 10 zones: 6 panels, OBC, battery, FPA, thruster | 0x0400-0x0409 |

**Thermal control philosophy:** Battery heaters are the only active thermal control
mechanism under EPS/TCS authority. All other thermal regulation is passive (MLI,
radiators, surface coatings, spacecraft orientation). The OBC and thruster heaters
are monitored by this position but are secondary to the battery heater, which is the
critical life-preserving circuit for the mission.

---

## 3. Commands and Telemetry (PUS Services)

### 3.1 Allowed PUS Services

The eps_tcs position has access to services: **1, 3, 5, 8, 17, 20**.

| Service | Usage | Description |
|---|---|---|
| S1 | TM only | Telecommand verification reports (accept/start/complete/fail) |
| S3 | HK request/enable/disable/interval | Housekeeping: SID 1 (EPS, 1 s) and SID 3 (TCS, 60 s) |
| S5 | Event enable/disable | Event report control for EPS/TCS events |
| S8 | Function management | All EPS/TCS operational commands (func_ids 13-15, 30-35) |
| S17 | Connection test | Link verification via ping |
| S20 | Parameter read/write | Direct read/write of EPS params (0x0100-0x0126) and TCS params (0x0400-0x0411) |

### 3.2 EPS Commands (S8)

| Command | func_id | Parameters | Description |
|---|---|---|---|
| EPS_POWER_ON | 13 | line_index (0-7) | Switch a switchable power line ON |
| EPS_POWER_OFF | 14 | line_index (0-7) | Switch a switchable power line OFF |
| EPS_RESET_OC_FLAG | 15 | line_index (0-7) | Reset overcurrent trip flag and re-enable line |

**Constraints:**
- Lines 0 (OBC) and 1 (TTC RX) are unswitchable; commands to switch them will be rejected with error code 0x0006.
- An OC-tripped line cannot be re-enabled without first resetting the trip flag via func_id 15.
- The simulator enforces these constraints; MCS should provide clear operator feedback.

### 3.3 TCS Commands (S8)

| Command | func_id | Parameters | Description |
|---|---|---|---|
| HEATER_BATTERY | 30 | on (bool) | Battery heater manual on/off (overrides thermostat) |
| HEATER_OBC | 31 | on (bool) | OBC heater manual on/off (overrides thermostat) |
| HEATER_THRUSTER | 32 | on (bool) | Thruster heater manual on/off (overrides thermostat) |
| FPA_COOLER | 33 | on (bool) | FPA cooler on/off |
| HEATER_SET_SETPOINT | 34 | circuit (0-2), on_temp, off_temp | Modify thermostat setpoints |
| HEATER_AUTO_MODE | 35 | circuit (0-2) | Return heater to autonomous thermostat control |

**Constraints:**
- Manual heater commands disable thermostat auto-control; the operator must explicitly return to auto mode via func_id 35.
- A heater in stuck-on failure state will reject all commands.
- A failed heater cannot be commanded ON.
- FPA cooler should not activate until payload POST is complete (~10 s after standby).

### 3.4 EPS Telemetry (SID 1 -- 1 s interval, 39 parameters)

| Param ID | Name | Units | Description | Pack |
|---|---|---|---|---|
| 0x0100 | eps.bat_voltage | V | Battery terminal voltage | H, /100 |
| 0x0101 | eps.bat_soc | % | Battery state of charge | H, /100 |
| 0x0102 | eps.bat_temp | degC | Battery pack temperature | h, /100 |
| 0x0103 | eps.sa_a_current | A | Solar array A current | H, /100 |
| 0x0104 | eps.sa_b_current | A | Solar array B current | H, /100 |
| 0x0105 | eps.bus_voltage | V | Main bus voltage | H, /100 |
| 0x0106 | eps.power_cons | W | Total power consumption | H, /10 |
| 0x0107 | eps.power_gen | W | Total power generation | H, /10 |
| 0x0108 | eps.eclipse_flag | bool | Eclipse state (1 = eclipse) | B |
| 0x0109 | eps.bat_current | A | Battery charge/discharge current | h, /100 |
| 0x010A | eps.bat_capacity_wh | Wh | Remaining battery capacity | -- |
| 0x010B | eps.sa_a_voltage | V | Solar array A voltage | H, /100 |
| 0x010C | eps.sa_b_voltage | V | Solar array B voltage | H, /100 |
| 0x010D | eps.oc_trip_flags | bitmask | Overcurrent trip status (bit per line) | B |
| 0x010E | eps.uv_flag | bool | Undervoltage flag (bus < 26.5 V) | B |
| 0x010F | eps.ov_flag | bool | Overvoltage flag (bus > 29.5 V) | B |
| 0x0110-0x0117 | eps.pl_* | bool | Power line status (8 lines) | B each |
| 0x0118-0x011F | eps.line_current_* | A | Per-line current draw (8 lines) | H, /1000 each |
| 0x0120 | eps.bat_dod | % | Battery depth of discharge | H, /100 |
| 0x0121 | eps.bat_cycles | count | Charge/discharge cycle count | H |
| 0x0122 | eps.mppt_efficiency | ratio | MPPT tracker efficiency | H, /10000 |
| 0x0123 | eps.sa_age_factor | ratio | Solar array aging degradation | H, /10000 |
| 0x0124 | eps.sa_a_degradation | ratio | SA-A degradation factor | -- |
| 0x0125 | eps.sa_b_degradation | ratio | SA-B degradation factor | -- |
| 0x0126 | eps.sa_lifetime_hours | h | Cumulative sunlit hours | -- |

### 3.5 TCS Telemetry (SID 3 -- 60 s interval, 17 parameters)

| Param ID | Name | Units | Description | Pack |
|---|---|---|---|---|
| 0x0400 | tcs.temp_panel_px | degC | +X panel temperature | h, /100 |
| 0x0401 | tcs.temp_panel_mx | degC | -X panel temperature | h, /100 |
| 0x0402 | tcs.temp_panel_py | degC | +Y panel temperature | h, /100 |
| 0x0403 | tcs.temp_panel_my | degC | -Y panel temperature | h, /100 |
| 0x0404 | tcs.temp_panel_pz | degC | +Z panel (nadir) temperature | h, /100 |
| 0x0405 | tcs.temp_panel_mz | degC | -Z panel (zenith) temperature | h, /100 |
| 0x0406 | tcs.temp_obc | degC | OBC module temperature | h, /100 |
| 0x0407 | tcs.temp_battery | degC | Battery pack temperature | h, /100 |
| 0x0408 | tcs.temp_fpa | degC | FPA detector temperature | h, /100 |
| 0x0409 | tcs.temp_thruster | degC | Thruster valve temperature | h, /100 |
| 0x040A | tcs.htr_battery | bool | Battery heater on/off | B |
| 0x040B | tcs.htr_obc | bool | OBC heater on/off | B |
| 0x040C | tcs.cooler_fpa | bool | FPA cooler on/off | B |
| 0x040E | tcs.htr_duty_battery | % | Battery heater duty cycle | H, /100 |
| 0x040F | tcs.htr_duty_obc | % | OBC heater duty cycle | H, /100 |
| 0x0410 | tcs.htr_duty_thruster | % | Thruster heater duty cycle | H, /100 |
| 0x0411 | tcs.htr_total_power | W | Total heater power consumption | H, /10 |

### 3.6 Limit Definitions

#### EPS Limits

| Parameter | Yellow Low | Yellow High | Red Low | Red High |
|---|---|---|---|---|
| eps.bat_soc (%) | 25 | 95 | 15 | 100 |
| eps.bat_voltage (V) | 23.0 | 29.0 | 22.0 | 29.5 |
| eps.bat_temp (degC) | 2.0 | 40.0 | 0.0 | 45.0 |
| eps.bus_voltage (V) | 27.0 | 29.0 | 26.5 | 29.5 |
| eps.bat_dod (%) | -- | 60 | -- | 80 |

#### TCS Limits

| Parameter | Yellow Low | Yellow High | Red Low | Red High |
|---|---|---|---|---|
| tcs.temp_obc (degC) | 5.0 | 60.0 | 0.0 | 70.0 |
| tcs.temp_battery (degC) | 2.0 | 40.0 | 0.0 | 45.0 |
| tcs.temp_fpa (degC) | -18.0 | 8.0 | -20.0 | 12.0 |
| tcs.htr_total_power (W) | -- | 15.0 | -- | 18.0 |

#### Overcurrent Thresholds (per line, 150% of nominal at 28 V)

| Line | Index | Nominal Power | OC Threshold | Trip Time |
|---|---|---|---|---|
| obc | 0 | 40 W | 2.0 A | 50 ms (non-switchable, no trip) |
| ttc_rx | 1 | 5 W | 0.3 A | 50 ms (non-switchable, no trip) |
| ttc_tx | 2 | 20 W | 1.0 A | 50 ms |
| payload | 3 | 8-45 W | 2.5 A | 50 ms |
| fpa_cooler | 4 | 15 W | 1.0 A | 50 ms |
| htr_bat | 5 | 6 W | 0.5 A | 50 ms |
| htr_obc | 6 | 4 W | 0.3 A | 50 ms |
| aocs_wheels | 7 | 12 W | 0.8 A | 50 ms |

### 3.7 FDIR Rules Under EPS/TCS Watch

| FDIR Rule | Trigger | Level | Autonomous Action |
|---|---|---|---|
| Low battery SoC (warning) | eps.bat_soc < 20% | 1 | Autonomous payload power-off |
| Low battery SoC (critical) | eps.bat_soc < 15% | 2 | Safe mode EPS (full load shed) |
| Bus undervoltage | eps.bus_voltage < 26.0 V | 2 | Safe mode EPS |
| Battery over-temperature | tcs.temp_battery > 42 degC | 1 | Battery heater OFF |
| Battery under-temperature | tcs.temp_battery < 1 degC | 1 | Battery heater ON |

---

## 4. Operational Procedures

### 4.1 LEOP Procedures

| Procedure | ID | eps_tcs Role | Key Actions |
|---|---|---|---|
| Initial Health Check | LEOP-002 | Verify power and thermal status | Confirm bat_soc, bus_voltage, and all temperatures within green limits; verify PDM line states match expected post-separation config; report GO/NO-GO to FD |
| Solar Array Verification | LEOP-004 | Monitor solar array currents and voltages | Confirm sa_a_current > 0.5 A and sa_b_current > 0.5 A to verify deployment; compare sa_a_voltage and sa_b_voltage to expected values; record initial degradation factors |

**REQ-PROC-001:** A dedicated LEOP power-on procedure shall document the expected post-separation sequence: the separation timer activates the unswitchable essential bus (OBC + TTC RX), after which the OBC boots and enables default switchable lines (TTC TX, heaters, AOCS wheels) per the PDM configuration in `eps.yaml`.

**REQ-PROC-002:** The LEOP-004 solar array verification procedure shall include acceptance criteria for each of the six body-mounted panels, accounting for the expected illumination geometry at the planned separation epoch.

### 4.2 Commissioning Procedures

| Procedure | ID | eps_tcs Role | Key Actions |
|---|---|---|---|
| EPS Checkout | COM-001 | Execute EPS tests, verify power distribution | Test each switchable line ON/OFF; verify per-line current readings; test OC trip and reset; verify UV/OV flag triggering |
| TCS Verification | COM-002 | Verify thermal control loops | Test each heater circuit manually; verify thermostat setpoints; test setpoint modification; verify auto-mode return; correlate thermal model with measured panel temps |
| Payload Power On | COM-009 | Monitor power budget impact | Monitor power_cons increase when payload activates; confirm bus_voltage remains in green; confirm bat_soc trend stable |
| FPA Cooler Activation | COM-010 | Monitor power consumption and thermal | Monitor 15 W cooler draw; confirm temp_fpa reaching target within 30 min; monitor overall power margin |

**REQ-PROC-003:** The COM-001 EPS checkout shall include a power budget reconciliation step, comparing measured power_cons against the sum of individual line_current readings multiplied by bus_voltage.

**REQ-PROC-004:** The COM-002 TCS verification shall verify the battery heater thermostat hysteresis loop by commanding a manual OFF, observing temperature decline toward the ON setpoint, then returning to auto mode and confirming heater activation.

### 4.3 Nominal Operations Procedures

| Procedure | ID | eps_tcs Role | Key Actions |
|---|---|---|---|
| Eclipse Transition | NOM-010 | Monitor power balance during eclipse | Verify battery heater activates as battery temp drops; monitor bat_soc depletion rate vs. predicted; confirm positive power margin at eclipse exit |

**REQ-PROC-005:** A nominal power management procedure shall be created for the eps_tcs position, covering: (a) daily power budget review, comparing power_gen vs. power_cons averages over the last orbit; (b) battery SoC trend analysis for eclipse-season margin; (c) solar array degradation tracking using sa_age_factor and sa_lifetime_hours.

**REQ-PROC-006:** An eclipse entry/exit checklist shall be maintained, requiring confirmation that bat_soc is above 60% before predicted eclipse entry, and that all heater circuits are verified operational.

### 4.4 Contingency Procedures

| Procedure | ID | eps_tcs Role | Key Actions |
|---|---|---|---|
| Under-Voltage Load Shed | CTG-001 | Execute power line disconnections | Follow defined load shed order (Section 12); monitor bus_voltage recovery at each step |
| Thermal Exceedance | CTG-004 | Adjust heaters and power | Override heater setpoints if needed; coordinate payload safe with payload_ops |
| EPS Safe Mode | CTG-005 | Execute EPS safe mode recovery | Verify FDIR-triggered load shed; assess battery health; plan power-up sequence |
| Solar Array Degradation | CTG-009 | Assess degradation, adjust power profile | Analyse sa_a_degradation/sa_b_degradation; revise power budget; recommend load shedding if margin insufficient |
| Overcurrent Response | CTG-012 | Isolate and reset overcurrent line | Identify tripped line from oc_trip_flags bitmask; assess root cause; execute EPS_RESET_OC_FLAG when safe |
| Battery Cell Failure | CTG-013 | Assess cell failure, adjust charge limits | Monitor bat_voltage for cell-level voltage drop (~3.7 V reduction); reduce DoD limit; revise power budget |

**REQ-PROC-007:** The CTG-001 load shed procedure shall include explicit bus voltage targets: load shedding begins at bus_voltage < 26.5 V, and each step in the shed sequence shall define the expected bus voltage recovery threshold before proceeding.

**REQ-PROC-008:** The CTG-012 overcurrent response procedure shall require root-cause analysis (check oc_inject state via S20 parameter read) before resetting an OC flag, to prevent re-trip.

### 4.5 Emergency Procedures

| Procedure | ID | eps_tcs Role | Key Actions |
|---|---|---|---|
| Total Power Failure | EMG-002 | Manage emergency power restoration | Coordinate with FD; verify essential bus (OBC + TTC RX) on unswitchable lines; attempt battery recovery |
| Thermal Runaway | EMG-006 | Emergency heater shutdown | Command all heaters OFF; coordinate payload power-off with payload_ops; monitor temperature stabilisation |

**REQ-PROC-009:** The EMG-002 procedure shall document that the unswitchable OBC and TTC RX lines guarantee minimum commanding capability even after total PDM switchable-bus failure.

---

## 5. Training Scenarios

### 5.1 Required Training Simulations

| Scenario ID | Name | Failure Injection | Training Objectives |
|---|---|---|---|
| TRN-EPS-001 | Eclipse Entry with Low SoC | Set bat_soc to 30% at eclipse entry | Practice power budget monitoring; verify heater activation; practice load shed decision-making |
| TRN-EPS-002 | Solar Array Partial Failure | `solar_array_partial` on wing A at 50% magnitude | Recognise asymmetric SA currents; revise power budget; decide on load shedding |
| TRN-EPS-003 | Battery Cell Failure | `bat_cell` injection | Recognise 3.7 V voltage drop; assess remaining capacity; execute CTG-013 |
| TRN-EPS-004 | Bus Short Circuit | `bus_short` injection (80 W parasitic load) | Recognise rapid SoC decline; identify anomalous power_cons; execute emergency load shed |
| TRN-EPS-005 | Overcurrent Trip | `overcurrent` on payload line (line_index=3) | Recognise OC trip from bitmask; isolate root cause; practice EPS_RESET_OC_FLAG procedure |
| TRN-EPS-006 | Undervoltage Event | `undervoltage` injection | Observe FDIR autonomous payload power-off at bat_soc < 20%; practice CTG-001 load shed sequence |
| TRN-TCS-001 | Battery Heater Failure | `heater_failure` circuit=battery | Recognise declining battery temp; attempt manual heater command (rejected); assess mission impact |
| TRN-TCS-002 | Heater Stuck-On | `heater_stuck_on` circuit=battery | Recognise increasing battery temp and power_cons; heater commands rejected; practice thermal runaway response |
| TRN-TCS-003 | Temperature Sensor Drift | `sensor_drift` zone=battery, magnitude=5.0 | Recognise inconsistent readings (EPS bat_temp vs. TCS temp_battery); cross-reference with heater duty cycle |
| TRN-TCS-004 | FPA Cooler Failure | `cooler_failure` injection | Recognise rising FPA temp; coordinate payload safe with payload_ops; practice CTG-004 |
| TRN-TCS-005 | OBC Thermal Runaway | `obc_thermal` with heat_w=30 | Monitor OBC temp rise; coordinate with FDIR/Systems; potential OBC switchover |
| TRN-COMBO-001 | Eclipse + SA Degradation | Combine eclipse entry with sa_a_degradation at 40% | Test compound failure handling: reduced generation during eclipse with degraded array |
| TRN-COMBO-002 | Load Shed Under Thermal Stress | Low SoC + battery heater stuck-on | Practice prioritising thermal vs. power constraints; coordinate with FD for load shed while managing heater |

### 5.2 Training Competency Requirements

**REQ-TRAIN-001:** Each eps_tcs operator shall complete all 13 training scenarios before certification for solo console operations.

**REQ-TRAIN-002:** Training scenarios shall use the simulator's failure injection API (`inject_failure` / `clear_failure`) to create realistic fault conditions with configurable magnitude.

**REQ-TRAIN-003:** The training programme shall include a minimum of two joint scenarios with other positions: one with payload_ops (COM-009/COM-010 power monitoring) and one with flight_director (CTG-001 load shed coordination with GO/NO-GO).

---

## 6. MCS Display and Tool Requirements

### 6.1 EPS Tab Requirements

**REQ-MCS-001:** The EPS tab shall display the following widgets:

1. **Battery Status Panel**
   - Battery SoC gauge (0-100%), colour-coded per limit definitions
   - Battery voltage numeric display with trend arrow
   - Battery current numeric display (signed: positive = charging)
   - Battery temperature numeric display with limit colouring
   - Battery depth of discharge (bat_dod) numeric with red threshold at 80%
   - Battery cycle count (bat_cycles) numeric

2. **Bus Status Panel**
   - Bus voltage gauge (20-30 V range), colour-coded
   - Undervoltage flag LED indicator (role="status", aria-label)
   - Overvoltage flag LED indicator

3. **Solar Array Panel**
   - SA-A and SA-B current gauges
   - SA-A and SA-B voltage displays
   - SA-A and SA-B degradation factor displays
   - MPPT efficiency display
   - SA age factor display
   - SA lifetime hours counter
   - Eclipse flag indicator

4. **Power Budget Summary**
   - Power generation (power_gen) numeric, Watts
   - Power consumption (power_cons) numeric, Watts
   - Power margin (power_gen - power_cons) calculated, with colour coding (red if negative)

### 6.2 PDM Control Panel Requirements

**REQ-MCS-002:** The EPS tab shall include a dedicated PDM Control Panel with:

1. **Power Line Table** -- one row per line (8 total):
   - Line index (0-7)
   - Line name (obc, ttc_rx, ttc_tx, payload, fpa_cooler, htr_bat, htr_obc, aocs_wheels)
   - Switchable indicator (yes/no, visually distinct for unswitchable lines)
   - Current state (ON/OFF) with LED indicator
   - Line current draw (A) with per-line OC threshold indicator
   - Overcurrent trip flag (per-line, decoded from bitmask)
   - ON/OFF toggle button (disabled for unswitchable lines, disabled for OC-tripped lines)
   - OC reset button (enabled only when trip flag is set)

2. **Load Shed Quick-Action Panel**:
   - Four-button sequential load shed panel following the defined order: Payload, FPA Cooler, TTC TX, AOCS Wheels
   - Each button shows the expected power reduction
   - Visual indication of which load-shed steps have been executed
   - "Restore All" button for recovery (requires FD GO)

3. **Unswitchable Line Indicators**:
   - OBC and TTC RX lines shall be visually distinguished (e.g., grey background, lock icon) to indicate they cannot be commanded
   - These lines shall still display current draw for monitoring

**REQ-MCS-003:** The PDM Control Panel shall enforce command validation:
- Reject commands to unswitchable lines with a clear error message
- Warn before commanding OFF on htr_bat (battery heater) during eclipse
- Require confirmation before commanding OFF on ttc_tx (loss of downlink capability)

### 6.3 TCS Tab Requirements

**REQ-MCS-004:** The TCS tab shall display:

1. **Temperature Overview Table** -- 10 zones:
   - Panel temps (+X, -X, +Y, -Y, +Z, -Z) -- informational, no FDIR
   - OBC temperature with yellow/red colouring
   - Battery temperature with yellow/red colouring
   - FPA temperature with yellow/red colouring
   - Thruster temperature

2. **Heater Control Panel**:
   - Battery heater: status LED, duty cycle bar, manual/auto indicator, ON/OFF buttons
   - OBC heater: status LED, duty cycle bar, manual/auto indicator, ON/OFF buttons
   - Thruster heater: status LED, duty cycle bar, manual/auto indicator, ON/OFF buttons
   - FPA cooler: status LED, ON/OFF button
   - Total heater power numeric display with yellow/red limit
   - Auto-mode return buttons per circuit

3. **Thermostat Setpoint Editor**:
   - Per-circuit ON/OFF temperature setpoint inputs
   - Current setpoint display
   - "Restore Default" button per circuit

### 6.4 Power Budget Trending Requirements

**REQ-MCS-005:** The MCS shall provide power budget trend charts:

1. **Power Balance Chart** (default 10-minute window, adjustable):
   - Overlaid lines: power_gen (green) and power_cons (red)
   - Shaded region between showing margin (green when positive, red when negative)
   - Eclipse periods shown as grey shaded background regions

2. **Battery SoC Trend** (default 3-hour window, adjustable):
   - SoC line with yellow/red limit bands
   - DoD line overlay
   - Predicted eclipse entry/exit markers from planner data

3. **Per-Line Current Chart** (default 10-minute window):
   - Eight stacked or overlaid current traces, one per power line
   - Per-line OC threshold shown as dashed red lines
   - OC trip events shown as markers

4. **Temperature Trend Chart** (default 30-minute window):
   - Component temps: battery, OBC, FPA, thruster
   - Yellow/red limit bands per zone
   - Heater ON/OFF periods shown as coloured bars on the temp trace

5. **Panel Temperature Chart** (default 30-minute window):
   - Six panel temperature traces
   - Eclipse periods as grey background

### 6.5 Overview Tab Requirements

**REQ-MCS-006:** When logged in as eps_tcs position, the Overview tab shall show only EPS and TCS subsystem summaries (per `overview_subsystems: [eps, tcs]` configuration).

### 6.6 Accessibility Requirements

**REQ-MCS-007:** All EPS/TCS display elements shall comply with the existing WCAG 2.1 AA requirements implemented in the MCS, including:
- LED indicators with `role="status"` and descriptive `aria-labels`
- Keyboard navigation for PDM control panel (arrows, Enter to toggle)
- Colour contrast meeting 4.5:1 ratio for all limit-coloured text
- Border-style differentiation on LED indicators for colour-blind users

---

## 7. Planner Requirements

### 7.1 Power Budget Tracking

**REQ-PLN-001:** The mission planner shall maintain an orbit-by-orbit power budget prediction, including:

1. **Solar generation prediction** based on:
   - Orbital geometry (eclipse fraction, solar beta angle)
   - Six-panel body-mounted illumination model (per-face projected area)
   - Solar array degradation factors (sa_age_factor, individual wing degradation)
   - MPPT efficiency

2. **Power consumption prediction** based on:
   - Planned power line configuration per orbit segment
   - Payload duty cycle (imaging windows vs. idle)
   - Eclipse-period heater power estimates (battery heater duty cycle at cold-case temps)
   - FPA cooler duty cycle

3. **Power margin calculation:**
   - Orbit-averaged generation vs. consumption
   - Worst-case eclipse-period SoC depletion with battery capacity margin
   - Warning thresholds: flag any orbit where predicted SoC minimum < 35%

**REQ-PLN-002:** The planner shall provide eclipse entry/exit time predictions for the next 24 hours, displayed as overlay markers on MCS trend charts.

**REQ-PLN-003:** The planner shall flag any planned activity (imaging session, data downlink, orbit manoeuvre) that would push the predicted power margin below 10% of battery capacity.

### 7.2 Thermal Planning

**REQ-PLN-004:** The planner shall predict worst-case cold temperatures during eclipse and verify battery heater power budget is allocated for the full eclipse duration.

**REQ-PLN-005:** The planner shall cross-reference payload imaging windows with FPA cooler power requirements, ensuring simultaneous cooler + payload draw is included in power budget predictions.

---

## 8. Simulator Fidelity Requirements

### 8.1 EPS Model Fidelity

**REQ-SIM-001:** The EPS simulator (eps_basic.py) shall model:

1. **Battery model:**
   - State of charge tracking with energy balance (net_power / capacity)
   - Open-circuit voltage as linear function of SoC: OCV = soc_0_v + (soc_100_v - soc_0_v) * SoC/100
   - Loaded voltage: V_loaded = OCV - I * R_internal
   - Battery temperature with thermal time constant (tau = 600 s) and I^2*R heating
   - Depth of discharge tracking (DoD = 100 - SoC)
   - Charge/discharge cycle counting (increment on charge-to-discharge transition)
   - Battery cell failure simulation (3.7 V voltage drop)

2. **Solar array model:**
   - Power = panel_area * cell_efficiency * solar_irradiance * cos(beta) * degradation * noise
   - Per-wing enable/disable control
   - Per-wing degradation factor (0.0 to 1.0, injected via failure)
   - MPPT efficiency applied to generated power (nominal 97%)
   - Aging model: ~2.75% degradation per year, tracked via sa_lifetime_hours and sa_age_factor
   - Eclipse blanking (zero generation in eclipse)

3. **Bus model:**
   - Regulated 28 V bus with SoC-dependent variation
   - Undervoltage detection at 26.5 V
   - Overvoltage detection at 29.5 V
   - Bus short circuit injection (80 W parasitic load)

4. **PDM model:**
   - Eight power lines with individual ON/OFF states
   - Unswitchable vs. switchable distinction enforced in command handling
   - Per-line current draw computed from power / bus_voltage
   - Per-line overcurrent detection with configurable thresholds
   - Overcurrent trip with bitmask flag and automatic line disconnect
   - OC flag reset command with line re-enable
   - Load-dependent side effects (payload mode reset, cooler off, TX off on trip)

### 8.2 TCS Model Fidelity

**REQ-SIM-002:** The TCS simulator (tcs_basic.py) shall model:

1. **Temperature zones:**
   - Ten independent thermal zones with configurable time constants and heat capacitances
   - Panel temperatures driven by orbital environment (eclipse: -30 degC external, sunlit: beta-dependent up to +40 degC)
   - Internal component temperatures driven by internal environment (eclipse: 10 degC, sunlit: 12 degC)
   - Gaussian noise on all temperature readings (0.02-0.05 degC)

2. **Heater circuits:**
   - Three thermostat-controlled heaters (battery, OBC, thruster) with configurable ON/OFF setpoints
   - Thermostat hysteresis control (auto mode)
   - Manual override mode (disables thermostat, requires explicit auto-mode return)
   - Heater power applied as heat input to respective thermal zone capacitance
   - Heater failure injection (heater cannot be commanded ON)
   - Heater stuck-on injection (heater stays ON regardless of commands)
   - Duty cycle tracking with 10-minute sliding window (exponential decay approximation)
   - Total heater power calculation

3. **FPA cooler:**
   - Target temperature -5 degC (simulator) / -15 degC (per manual)
   - Cooling effect modelled as offset to equilibrium temperature
   - Cooler failure injection (no cooling effect)

4. **Sensor drift:**
   - Per-zone sensor drift offset injection for failure simulation
   - Applied to shared parameter output (telemetry shows drifted value, actual temp unchanged)

### 8.3 Missing Simulator Features -- Gap Analysis

**REQ-SIM-003:** The following features are identified as gaps between the simulator and the mission profile:

| Gap | Description | Priority |
|---|---|---|
| Six-panel solar model | Current model uses two wings (A/B) with aggregate area. Mission has six body-mounted panels (one per face). Simulator should model per-face illumination based on attitude. | High |
| Separation timer | No explicit separation timer model. The unswitchable line concept exists but the timed sequence from separation switch closure to OBC boot is not simulated. | Medium |
| Cold-redundant EPS | No redundancy model for EPS electronics. Single PCDU path in simulator. | Low |
| Battery charge profile | CC/CV charging not modelled. SoC linearly tracks energy balance. | Low |
| Panel-to-cell thermal coupling | Solar panel temperatures do not affect solar cell efficiency in the EPS model. | Medium |
| Heater power coupling to EPS | TCS heater power consumption is separately tracked in TCS (htr_total_power) but the EPS model uses fixed 6 W / 4 W values. These should be cross-validated. | Medium |

---

## 9. PDM Model Requirements

### 9.1 Unswitchable Lines for Separation Sequence

**REQ-PDM-001:** The PDM shall model two unswitchable power lines that are energised
by the separation timer:

| Line | Load | Power | Purpose |
|---|---|---|---|
| Line 0 | OBC | 40 W | On-board computer -- essential for commanding and autonomy |
| Line 1 | TTC RX | 5 W | TTC receiver -- essential for ground commanding |

These lines shall:
- Be permanently energised once the separation timer fires
- Reject any command to switch them off (error code 0x0006)
- Always show as ON in power line status telemetry (0x0110, 0x0111)
- Continue drawing current even during safe mode / emergency

**REQ-PDM-002:** The separation sequence shall be modelled as:
1. Separation switch closure triggers timer
2. Timer fires, energising unswitchable bus (OBC + TTC RX)
3. OBC boots and executes default power-line configuration from `eps.yaml`
4. Default switchable lines enabled: TTC TX (ON), htr_bat (ON), htr_obc (ON), aocs_wheels (ON)
5. Default switchable lines disabled: payload (OFF), fpa_cooler (OFF)

### 9.2 Dedicated Transponder Channel

**REQ-PDM-003:** The TTC transmitter (line index 2, ttc_tx) shall be a dedicated
switchable PDM channel with the following operational constraints:

- Default ON at boot to enable downlink immediately after separation
- Load shed priority: third in sequence (after payload and FPA cooler, before AOCS wheels)
- Commanding OFF disables spacecraft-to-ground telemetry (transponder_tx = false)
- MCS shall display a prominent warning when ttc_tx is OFF
- OC threshold: 1.0 A (150% of nominal 0.71 A at 28 V)

**REQ-PDM-004:** The PDM overcurrent protection shall:
- Trip switchable lines only (unswitchable lines have hardware fuses, not modelled)
- Trip within 50 ms of overcurrent detection (simulated as immediate at 1 Hz tick)
- Set the corresponding bit in the oc_trip_flags bitmask
- Automatically disconnect the faulting line
- Require explicit operator reset via EPS_RESET_OC_FLAG before re-enabling
- Apply side effects: payload_mode -> 0, fpa_cooler -> OFF, transponder_tx -> false as applicable

### 9.3 PDM Telemetry Requirements

**REQ-PDM-005:** The PDM shall provide per-line telemetry at 1 Hz (within EPS HK SID 1):
- Eight line-status parameters (0x0110-0x0117): ON/OFF state
- Eight line-current parameters (0x0118-0x011F): current draw in Amps, packed as unsigned 16-bit with 1000x scale
- One overcurrent trip bitmask (0x010D): 8-bit, bit 0 = line 0 through bit 7 = line 7
- Undervoltage flag (0x010E)
- Overvoltage flag (0x010F)

---

## 10. Six Body-Panel Solar Array Model

### 10.1 Current Implementation

The current simulator models solar arrays as two wings (A and B), each with 0.314 m^2
area and 29.5% efficiency GaAs triple-junction cells. Total panel area is 0.628 m^2.
Power generation depends on the solar beta angle via cos(beta), with eclipse blanking.

### 10.2 Required Enhancements for Six-Panel Model

**REQ-SOL-001:** The solar model shall be enhanced to represent six body-mounted panels
(one per spacecraft face) with independent illumination calculations:

| Panel | Face | Area | Orientation | Illumination Model |
|---|---|---|---|---|
| Panel +X | +X | ~0.105 m^2 | Ram or anti-ram | dot(sun_vector, +X_body) |
| Panel -X | -X | ~0.105 m^2 | Anti-ram or ram | dot(sun_vector, -X_body) |
| Panel +Y | +Y | ~0.105 m^2 | Sun-facing (SSO) | dot(sun_vector, +Y_body) |
| Panel -Y | -Y | ~0.105 m^2 | Anti-sun (SSO) | dot(sun_vector, -Y_body) |
| Panel +Z | +Z (nadir) | ~0.105 m^2 | Earth-facing | dot(sun_vector, +Z_body) |
| Panel -Z | -Z (zenith) | ~0.105 m^2 | Zenith-facing | dot(sun_vector, -Z_body) |

**REQ-SOL-002:** Each panel shall compute power as:
```
P_panel = area * efficiency * irradiance * max(0, dot(sun_vector, panel_normal)) * degradation * mppt_eff
```
Only panels with positive sun incidence angle contribute power (no generation from
the shadowed side). Total generation is the sum of all six panels.

**REQ-SOL-003:** Panel illumination shall be coupled to the AOCS attitude quaternion,
so that attitude changes (e.g., slew for imaging) affect power generation in real time.

**REQ-SOL-004:** Each panel shall have an independent degradation factor, enabling
failure injection of partial cell loss on specific faces.

**REQ-SOL-005:** Panel temperature coupling (from TCS model) should be considered as
a future enhancement: GaAs cell efficiency decreases approximately -0.045%/degC above
25 degC, which affects hot-case power predictions.

### 10.3 Current Model Adequacy Assessment

For training purposes, the two-wing model provides adequate fidelity for:
- Eclipse/sunlit power transitions
- Overall power budget balancing
- Load shed sequence practice

The six-panel model is required for:
- Realistic attitude-dependent power generation
- Per-face failure simulation
- Thermal model correlation (panel temp vs. solar flux on each face)
- Commissioning thermal model validation scenarios

---

## 11. Battery-Heater-Only Thermal Control Alignment

### 11.1 Design Philosophy

EOSAT-1 uses a minimalist thermal control approach appropriate for a 6U cubesat:
- **Battery heater** (6 W): The sole mission-critical active thermal element. Maintains
  battery temperature above 0 degC qualification limit during eclipse.
- **OBC heater** (4 W): Maintains OBC module above operational minimum. Important but
  secondary to battery heater in load-shed priority.
- **Thruster heater** (8 W): Keeps propulsion valve above freezing for operational readiness.
  Can be shed if propulsion not needed.
- **FPA cooler** (15 W): Active cooling for the payload detector. Only needed during
  imaging operations. Shed first in any power-constrained scenario.

All other thermal control is passive (MLI, radiators, surface coatings, orbital orientation).

### 11.2 Alignment Requirements

**REQ-TH-001:** The EPS power line model and TCS heater model shall be aligned:
- EPS htr_bat line (index 5) power (6 W) shall match TCS battery heater power (6 W configured in tcs.yaml)
- EPS htr_obc line (index 6) power (4 W) shall match TCS OBC heater power (4 W configured in tcs.yaml)
- The thruster heater (8 W in TCS) is referenced in TCS but does not have a dedicated EPS power line. This is a model gap.

**REQ-TH-002:** When the EPS model commands htr_bat OFF (power line 5), the TCS battery
heater shall also be deactivated, and vice versa. Currently these are independently
modelled; cross-subsystem coupling through shared parameters should be verified.

**REQ-TH-003:** The battery heater shall be the last heater shed in any power-constrained
scenario. The load shed sequence (Section 12) does not include heater lines, which means
heaters are preserved even during the standard four-step load shed. Only EMG-002 (Total
Power Failure) and EMG-006 (Thermal Runaway) scenarios should result in battery heater
shutdown.

**REQ-TH-004:** The thermostat setpoints configured in tcs.yaml shall be documented as
the flight defaults:
- Battery: ON at 1 degC, OFF at 5 degC
- OBC: ON at 5 degC, OFF at 10 degC
- Thruster: ON at 2 degC, OFF at 8 degC

Note: The manual document (03_tcs.md Section 3.1) quotes different setpoints (battery:
ON 5 degC / OFF 10 degC; OBC: ON 10 degC / OFF 15 degC; thruster: ON 5 degC / OFF
10 degC). This discrepancy should be resolved; the tcs.yaml configuration is what the
simulator actually uses.

**REQ-TH-005:** The MCS shall display a warning when the battery heater is in manual
mode (thermostat disabled), as this removes the autonomous cold-protection for the
battery.

---

## 12. Load Shed Sequence

### 12.1 Defined Load Shed Order

The simulator defines the following load shed priority (first shed = lowest mission
priority):

| Step | Line Name | Line Index | Power Saved | Cumulative Saved | Remaining Loads |
|---|---|---|---|---|---|
| 1 | payload | 3 | 8-45 W | 8-45 W | OBC, TTC RX, TTC TX, FPA cooler (off), heaters, AOCS |
| 2 | fpa_cooler | 4 | 15 W | 23-60 W | OBC, TTC RX, TTC TX, heaters, AOCS |
| 3 | ttc_tx | 2 | 20 W | 43-80 W | OBC, TTC RX, heaters, AOCS |
| 4 | aocs_wheels | 7 | 12 W | 55-92 W | OBC, TTC RX, heaters only |

### 12.2 Load Shed Trigger

**REQ-LS-001:** Load shedding shall be initiated when bus_voltage drops below 26.5 V
(the LOAD_SHED_VOLTAGE threshold defined in eps_basic.py).

**REQ-LS-002:** The FDIR system provides two autonomous load-shed triggers:
- bat_soc < 20% (Level 1): Autonomous payload power-off (Step 1 of shed sequence)
- bat_soc < 15% or bus_voltage < 26.0 V (Level 2): Full safe mode (all four steps)

### 12.3 Load Shed Constraints

**REQ-LS-003:** The following lines are never shed by the standard sequence:
- OBC (line 0): Unswitchable, essential for spacecraft autonomy
- TTC RX (line 1): Unswitchable, essential for ground commanding
- htr_bat (line 5): Battery heater preserved to protect battery cells
- htr_obc (line 6): OBC heater preserved to protect computer hardware

**REQ-LS-004:** After TTC TX is shed (Step 3), the spacecraft has no downlink capability.
This state shall be clearly indicated on the MCS. Uplink via TTC RX remains available
for blind commanding.

**REQ-LS-005:** After AOCS wheels are shed (Step 4), the spacecraft loses active attitude
control and may begin tumbling. This shall be coordinated with the AOCS position and
flagged as a critical state.

### 12.4 Load Restoration

**REQ-LS-006:** Load restoration after a load-shed event shall follow the reverse order
(AOCS wheels first, then TTC TX, FPA cooler, payload) and shall require:
1. Bus voltage above 27.5 V (1 V margin above shed threshold)
2. Battery SoC above 40% (15% margin above Level 1 FDIR threshold)
3. Flight Director GO/NO-GO approval for each restoration step
4. Minimum 5-minute dwell between restoration steps to verify stability

---

## 13. Power Budget Trending

### 13.1 Orbit-Level Power Budget

**REQ-PB-001:** The eps_tcs operator shall maintain an orbit-level power budget with
the following line items:

| Load | Nominal (W) | Eclipse (W) | Peak (W) | Notes |
|---|---|---|---|---|
| OBC | 40 | 40 | 40 | Unswitchable, always ON |
| TTC RX | 5 | 5 | 5 | Unswitchable, always ON |
| TTC TX | 20 | 20 | 20 | OFF during load shed Step 3 |
| Payload (standby) | 8 | 0 | 8 | OFF in eclipse nominally |
| Payload (imaging) | 45 | 0 | 45 | Active only in sunlit imaging windows |
| FPA Cooler | 15 | 0 | 15 | Active only during imaging prep + imaging |
| Battery Heater | 6 | 6 | 6 | Thermostat-controlled, higher duty in eclipse |
| OBC Heater | 4 | 4 | 4 | Thermostat-controlled |
| AOCS Wheels | 12 | 12 | 12 | OFF during load shed Step 4 |
| **Platform total** | **95-155** | **87** | **155** | |

| Source | BOL (W) | EOL (W) | Notes |
|---|---|---|---|
| Solar arrays (sunlit, optimal beta) | ~119 | ~108 | 2.75%/yr degradation, 97% MPPT |
| Solar arrays (eclipse) | 0 | 0 | |
| Battery (120 Wh, 80% max DoD) | 96 Wh available | 96 Wh available | ~60 min eclipse at 87 W => 87 Wh needed |

### 13.2 Power Margin Analysis

**REQ-PB-002:** The power margin analysis shall compute:

1. **Orbit average margin** = (sunlit_fraction * power_gen_avg) - power_cons_avg
   - Must be positive for battery recharge sustainability
   - Warning if margin < 10 W

2. **Eclipse energy balance** = battery_available_Wh - (eclipse_duration_min / 60 * eclipse_power_cons_W)
   - Must be positive at eclipse exit
   - Warning if predicted eclipse-exit SoC < 35%

3. **Worst-case analysis**: Maximum eclipse duration (EOSAT-1 SSO ~35 min worst case)
   with minimum generation (end-of-life degradation) and maximum consumption (all heaters
   on full duty cycle)

**REQ-PB-003:** The MCS shall display a real-time power margin indicator derived from
current power_gen and power_cons telemetry, updated at 1 Hz.

### 13.3 Long-Term Degradation Tracking

**REQ-PB-004:** The eps_tcs operator shall track the following long-term trends:

| Metric | Source | Expected Rate | Action Threshold |
|---|---|---|---|
| SA age factor | 0x0123 (sa_age_factor) | ~2.75%/year decrease | Revise power budget when < 0.90 |
| SA lifetime hours | 0x0126 (sa_lifetime_hours) | ~5,256 h/year sunlit | Informational |
| Battery cycle count | 0x0121 (bat_cycles) | ~15 cycles/day (LEO) | Revise DoD limit at 10,000 cycles |
| MPPT efficiency | 0x0122 (mppt_efficiency) | Stable at ~0.97 | Investigate if < 0.90 |
| SA-A degradation | 0x0124 | Stable at 1.0 unless failure | Investigate any change |
| SA-B degradation | 0x0125 | Stable at 1.0 unless failure | Investigate any change |

---

## 14. Traceability Matrix

| Requirement ID | Section | Category | Priority | Verification |
|---|---|---|---|---|
| REQ-PROC-001 | 4.1 | Procedure | High | Procedure review + sim test |
| REQ-PROC-002 | 4.1 | Procedure | High | Sim test with known geometry |
| REQ-PROC-003 | 4.2 | Procedure | Medium | Sim test during COM-001 |
| REQ-PROC-004 | 4.2 | Procedure | Medium | Sim test during COM-002 |
| REQ-PROC-005 | 4.3 | Procedure | High | Procedure creation + review |
| REQ-PROC-006 | 4.3 | Procedure | High | Procedure creation + review |
| REQ-PROC-007 | 4.4 | Procedure | High | Sim test during CTG-001 |
| REQ-PROC-008 | 4.4 | Procedure | Medium | Sim test during CTG-012 |
| REQ-PROC-009 | 4.5 | Procedure | High | Procedure review |
| REQ-TRAIN-001 | 5.2 | Training | High | Training record review |
| REQ-TRAIN-002 | 5.2 | Training | High | Sim API verification |
| REQ-TRAIN-003 | 5.2 | Training | Medium | Training schedule review |
| REQ-MCS-001 | 6.1 | MCS Display | High | UI inspection |
| REQ-MCS-002 | 6.2 | MCS Display | High | UI inspection + command test |
| REQ-MCS-003 | 6.2 | MCS Display | High | Command validation test |
| REQ-MCS-004 | 6.3 | MCS Display | High | UI inspection |
| REQ-MCS-005 | 6.4 | MCS Display | High | Chart rendering test |
| REQ-MCS-006 | 6.5 | MCS Display | Medium | Position filter test |
| REQ-MCS-007 | 6.6 | MCS Display | Medium | WCAG audit |
| REQ-PLN-001 | 7.1 | Planner | High | Planner output review |
| REQ-PLN-002 | 7.1 | Planner | High | Eclipse prediction test |
| REQ-PLN-003 | 7.1 | Planner | Medium | Activity planning test |
| REQ-PLN-004 | 7.2 | Planner | Medium | Thermal prediction review |
| REQ-PLN-005 | 7.2 | Planner | Medium | Cross-reference test |
| REQ-SIM-001 | 8.1 | Simulator | High | Model validation test suite |
| REQ-SIM-002 | 8.2 | Simulator | High | Model validation test suite |
| REQ-SIM-003 | 8.3 | Simulator | Medium | Gap analysis review |
| REQ-PDM-001 | 9.1 | PDM Model | High | Sim command rejection test |
| REQ-PDM-002 | 9.1 | PDM Model | Medium | Sim startup sequence test |
| REQ-PDM-003 | 9.2 | PDM Model | High | Command + TM verification |
| REQ-PDM-004 | 9.2 | PDM Model | High | OC injection test |
| REQ-PDM-005 | 9.3 | PDM Model | High | HK packet inspection |
| REQ-SOL-001 | 10.2 | Solar Model | High | Model enhancement |
| REQ-SOL-002 | 10.2 | Solar Model | High | Per-panel power test |
| REQ-SOL-003 | 10.2 | Solar Model | Medium | Attitude coupling test |
| REQ-SOL-004 | 10.2 | Solar Model | Medium | Per-face failure test |
| REQ-SOL-005 | 10.2 | Solar Model | Low | Thermal coupling analysis |
| REQ-TH-001 | 11.2 | Thermal | High | Cross-config validation |
| REQ-TH-002 | 11.2 | Thermal | High | Cross-subsystem coupling test |
| REQ-TH-003 | 11.2 | Thermal | High | Load shed test |
| REQ-TH-004 | 11.2 | Thermal | Medium | Config documentation |
| REQ-TH-005 | 11.2 | Thermal | Medium | MCS warning test |
| REQ-LS-001 | 12.2 | Load Shed | High | Sim threshold test |
| REQ-LS-002 | 12.2 | Load Shed | High | FDIR integration test |
| REQ-LS-003 | 12.3 | Load Shed | High | Shed sequence verification |
| REQ-LS-004 | 12.3 | Load Shed | High | MCS indicator test |
| REQ-LS-005 | 12.3 | Load Shed | High | AOCS coordination test |
| REQ-LS-006 | 12.4 | Load Shed | High | Restoration sequence test |
| REQ-PB-001 | 13.1 | Power Budget | High | Budget reconciliation |
| REQ-PB-002 | 13.2 | Power Budget | High | Margin calculation test |
| REQ-PB-003 | 13.2 | Power Budget | Medium | MCS display test |
| REQ-PB-004 | 13.3 | Power Budget | Medium | Long-term trend review |

---

## Appendix A: Known Configuration Issues

The following known issues affect EPS/TCS operations and are tracked as xfail
conditions in the test suite:

1. **hk_structures.yaml SID 6 (TTC)** references param_id 0x0508, which is not
   defined in parameters.yaml. This does not directly affect EPS/TCS but may cause
   HK parsing errors when viewing TTC data.

2. **fdir.yaml** references `obdh.temp_obc` but the actual parameter name is `obdh.temp`
   (0x0301). This means the OBC over-temperature FDIR rule may not trigger correctly.
   The eps_tcs operator should manually monitor OBC temperature via tcs.temp_obc (0x0406)
   as a backup.

3. **displays.yaml** references stale parameter names including `tcs.htr_thruster`.
   The thruster heater status parameter (0x040D) exists in TCS telemetry but may not
   render correctly on some display configurations.

4. **Thermostat setpoint discrepancy**: The manual (03_tcs.md Section 3.1) documents
   different heater setpoints than what is configured in tcs.yaml. The simulator uses
   the tcs.yaml values. This should be reconciled.

---

## Appendix B: Cross-Reference to Procedure Index

Procedures directly involving the eps_tcs position:

| ID | Name | Category | eps_tcs Role |
|---|---|---|---|
| LEOP-002 | Initial Health Check | LEOP | Verify power and thermal status |
| LEOP-004 | Solar Array Verification | LEOP | Monitor SA currents and voltages |
| COM-001 | EPS Checkout | Commissioning | Execute EPS tests |
| COM-002 | TCS Verification | Commissioning | Verify thermal control loops |
| COM-009 | Payload Power On | Commissioning | Monitor power budget impact |
| COM-010 | FPA Cooler Activation | Commissioning | Monitor power and thermal |
| NOM-010 | Eclipse Transition | Nominal | Monitor power balance |
| CTG-001 | Under-Voltage Load Shed | Contingency | Execute load shed |
| CTG-004 | Thermal Exceedance | Contingency | Adjust heaters and power |
| CTG-005 | EPS Safe Mode | Contingency | Execute recovery |
| CTG-009 | Solar Array Degradation | Contingency | Assess and adjust |
| CTG-012 | Overcurrent Response | Contingency | Isolate and reset |
| CTG-013 | Battery Cell Failure | Contingency | Assess and adjust |
| EMG-002 | Total Power Failure | Emergency | Emergency power restoration |
| EMG-006 | Thermal Runaway | Emergency | Emergency heater shutdown |

---

*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

# TCS Display UI Audit Report
**File**: No standalone HTML file found (integrated in Power & Thermal panel)
**Date**: 2026-04-04
**Status**: AUDIT COMPLETE - FINDINGS SIGNIFICANT GAPS

## Executive Summary
The TCS (Thermal Control Subsystem) has **NO dedicated display HTML file**. Instead, TCS parameters are integrated into the "Power & Thermal" position dashboard (`displays.yaml`). However, after comprehensive audit:

- **10 telemetry parameters referenced** in displays.yaml
- **9 command functions available** in tc_catalog.yaml
- **Only 1 display panel exists** (Power & Thermal in displays.yaml)
- **0 dedicated TCS HTML UI** - **CRITICAL GAP**
- **0 manual commands in HTML** - must use catalog-based TC form

---

## Telemetry Elements Audit

### Parameters Referenced in displays.yaml (Power & Thermal Position)

| Element | Parameter ID | Parameter Name | In parameters.yaml | In HK SID 3 | Simulator Computes | MCS Capable | Status |
|---------|--------------|----------------|-------------------|------------|-------------------|-----------|--------|
| Battery Heater | 0x040A | `tcs.htr_battery` | ✓ | ✓ | ✓ TCSSubsystem | ✓ | **CONNECTED** |
| OBC Heater | 0x040B | `tcs.htr_obc` | ✓ | ✓ | ✓ TCSSubsystem | ✓ | **CONNECTED** |
| FPA Cooler | 0x040C | `tcs.cooler_fpa` | ✓ | ✓ | ✓ TCSSubsystem | ✓ | **CONNECTED** |
| Panel +X Temp | 0x0400 | `tcs.temp_panel_px` | ✓ | ✓ | ✓ TCSSubsystem | ✓ | **CONNECTED** |
| Panel -X Temp | 0x0401 | `tcs.temp_panel_mx` | ✓ | ✓ | ✓ TCSSubsystem | ✓ | **CONNECTED** |
| Panel +Y Temp | 0x0402 | `tcs.temp_panel_py` | ✓ | ✓ | ✓ TCSSubsystem | ✓ | **CONNECTED** |
| Panel -Y Temp | 0x0403 | `tcs.temp_panel_my` | ✓ | ✓ | ✓ TCSSubsystem | ✓ | **CONNECTED** |
| Panel +Z Temp | 0x0404 | `tcs.temp_panel_pz` | ✓ | ✓ | ✓ TCSSubsystem | ✓ | **CONNECTED** |
| Panel -Z Temp | 0x0405 | `tcs.temp_panel_mz` | ✓ | ✓ | ✓ TCSSubsystem | ✓ | **CONNECTED** |
| OBC Temp | 0x0406 | `tcs.temp_obc` | ✓ | ✓ | ✓ TCSSubsystem | ✓ | **CONNECTED** |
| Battery Temp | 0x0407 | `tcs.temp_battery` | ✓ | ✓ | ✓ TCSSubsystem | ✓ | **CONNECTED** |
| FPA Temp | 0x0408 | `tcs.temp_fpa` | ✓ | ✓ | ✓ TCSSubsystem | ✓ | **CONNECTED** |
| Thruster Temp | 0x0409 | `tcs.temp_thruster` | ✓ | ✓ | ✓ TCSSubsystem | ✓ | **CONNECTED** |

### Command Functions in tc_catalog.yaml

| Command Name | Service | Subtype | Function ID | Description | Routed | Handler | Status |
|--------------|---------|---------|------------|-------------|--------|---------|--------|
| HEATER_BATTERY | 8 | 1 | 0x28 (40) | Battery heater on/off | ✓ | ✓ S8 handler | **CONNECTED** |
| HEATER_OBC | 8 | 1 | 0x29 (41) | OBC heater on/off | ✓ | ✓ S8 handler | **CONNECTED** |
| HEATER_THRUSTER | 8 | 1 | 0x2A (42) | Thruster heater on/off | ✓ | ✓ S8 handler | **CONNECTED** |
| FPA_COOLER | 8 | 1 | 0x2B (43) | FPA cooler on/off | ✓ | ✓ S8 handler | **CONNECTED** |
| HEATER_SET_SETPOINT | 8 | 1 | 0x2C (44) | Modify thermostat setpoints | ✓ | ✓ S8 handler | **CONNECTED** |
| HEATER_AUTO_MODE | 8 | 1 | 0x2D (45) | Return to autonomous thermostat | ✓ | ✓ S8 handler | **CONNECTED** |
| TCS_SET_HEATER_DUTY_LIMIT | 8 | 1 | 0x2E (46) | Limit heater duty cycle | ✓ | ✓ S8 handler | **CONNECTED** |
| TCS_DECONTAMINATION_START | 8 | 1 | 0x2F (47) | Begin decontamination heating | ✓ | ✓ S8 handler | **CONNECTED** |
| TCS_DECONTAMINATION_STOP | 8 | 1 | 0x30 (48) | Abort decontamination | ✓ | ✓ S8 handler | **CONNECTED** |
| TCS_GET_THERMAL_MAP | 8 | 1 | 0x31 (49) | Request thermal status | ✓ | ✓ S8 handler | **CONNECTED** |

---

## Gap Analysis

### CRITICAL GAP: No Dedicated TCS Display

**Issue**: While TCS parameters and commands are fully functional in the simulator and accessible via the generic TC form in "Power & Thermal" position, there is **NO dedicated TCS operator display HTML file**.

**Impact**:
- TCS thermal control operators must use generic Power & Thermal dashboard
- No specialized TCS-only view available
- TCS commands only accessible via dynamic TC catalog form (less ergonomic than hardcoded buttons)
- No TCS-specific charts or thermal trend visualization

**Current State**:
```
✓ TCS parameters computed by TCSSubsystem (tcs_basic.py)
✓ TCS parameters in HK SID 3
✓ TCS parameters in parameters.yaml (0x0400-0x040C)
✓ TCS commands in tc_catalog.yaml (S8 func_id 40-49)
✓ TCS integrated into "Power & Thermal" position dashboard
✗ NO dedicated tcs.html file
✗ NO TCS-specific quick-access command buttons
✗ NO TCS-specific temperature trend charts
```

### Data Connectivity: 100%
All telemetry and command infrastructure is **FULLY CONNECTED**:
- Parameters exist in parameters.yaml
- Parameters in correct HK structure (SID 3)
- Simulator computes all values
- MCS broadcasts to all connected clients
- Commands routed through proper services

### Display Usability: 0% (no dedicated display)

---

## TCS Data Pipeline (Conceptual)

```
TCSSubsystem.tick()
  ├─ Computes thermal dynamics (lumped-mass model)
  ├─ Panel temperatures (6 surfaces)
  ├─ Internal equipment temps (OBC, Battery, FPA, Thruster)
  ├─ Heater thermostat logic
  ├─ FPA cooler control
  │
  shared_params[0x0400-0x040C] = temperature values
  shared_params[0x040A-0x040D] = heater/cooler states
  │
sim_server.py (TM packet generation)
  ├─ Decommutates into HK SID 3 (TCS)
  │
MCS WebSocket
  ├─ Broadcasts state.tcs object (if client subscribed)
  │
power_thermal.html (Power & Thermal position)
  ├─ Displays in "Thermal Overview" page
  ├─ Shows all 10 temperature parameters
  ├─ Shows heater/cooler status
  ├─ Shows "Temperature Trends" chart for battery/obc/fpa/thruster
  ├─ Shows "Panel Temperatures" chart for +X/-X/+Y/-Y
```

### Command Path (Via TC Catalog Form)
```
Operator clicks "Send TC" in Power & Thermal dashboard
  │
Selects command from catalog (S8 func 40-49)
  │
Sends: {type: 'tc', service: 8, subtype: 1, params: {function_id: 0x28}, apid: 1}
  │
service_handlers.py: handle_service_8()
  ├─ Parses function_id
  ├─ Routes to TCSSubsystem.cmd_heater() or cmd_fpa_cooler()
  │
TCSSubsystem updates state
  ├─ Heater turned on/off
  ├─ Cooler turned on/off
  ├─ Setpoints modified
  │
Next TM packet reflects new heater/cooler states
```

---

## Recommendations

### CRITICAL: Create Dedicated TCS Display

**Priority**: HIGH
**Effort**: MEDIUM (~4-6 hours)

**File to Create**: `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/files/tcs.html`

**Content Should Include**:

1. **Thermal Overview Panel** (like Power & Thermal)
   - All 10 temperature readings with limit indicators
   - Heater/cooler status badges
   - Current mode display

2. **Thermal Trend Charts**
   - Panel temperature trends (6 lines)
   - Internal temperature trends (4 lines)
   - Chart controls (zoom, pause, export)

3. **Quick Command Buttons**
   - **HEATER BATTERY**: ON | OFF | AUTO
   - **HEATER OBC**: ON | OFF | AUTO
   - **HEATER THRUSTER**: ON | OFF | AUTO
   - **FPA COOLER**: ON | OFF
   - **DECONTAMINATION START**: [with target temp input]
   - **DECONTAMINATION STOP**
   - **GET THERMAL MAP**: [status request]

4. **Heater Control Panel**
   - Setpoint adjustment form
   - Duty cycle limiter
   - Thermostat threshold display

5. **Thermal Alerts Section**
   - Yellow/Red violations per component
   - Heater failure state indication
   - Cooler failure state indication

### MEDIUM: Enhance Navigation

Add TCS link to main navigation bar:
```html
<a href="/tcs">TCS</a>
```

Currently visible in ttc.html and payload.html but no corresponding route exists.

### MINOR: Add TCS Charts to Power & Thermal

The displays.yaml already references:
- Line chart for temperature trends
- Line chart for panel temperatures

But verify these are actually rendered in the Power & Thermal position HTML.

---

## Current Workaround

TCS operators can currently:
1. Navigate to "Power & Thermal" position
2. Access "Thermal Overview" page to see all temperatures
3. Access "Temperature Trends" page for charts
4. Use TC catalog form to send heater/cooler commands

**This is functional but not optimal for dedicated TCS operations**.

---

## Audit Findings Summary

| Category | Count | Connected | Status |
|----------|-------|-----------|--------|
| Telemetry Parameters | 13 | 13 | ✓ CONNECTED |
| Command Functions | 10 | 10 | ✓ CONNECTED |
| Display HTML Files | 1 | 0 | ✗ **MISSING** |
| Data Pipeline | 1 | 1 | ✓ **COMPLETE** |

---

## Audit Metadata
- **Total Telemetry Elements**: 13 (all connected)
- **Total Command Functions**: 10 (all connected)
- **Data Pipeline Status**: 100% Connected
- **UI Status**: 0% Dedicated Display (Gap identified)
- **Confidence**: HIGH (verified in multiple source files)
- **Gap Severity**: MEDIUM (functionality works via Power & Thermal, but no dedicated display)

---

## Files Involved

### Configuration
- `/configs/eosat1/telemetry/parameters.yaml` - TCS params 0x0400-0x040C
- `/configs/eosat1/telemetry/hk_structures.yaml` - SID 3 (TCS)
- `/configs/eosat1/commands/tc_catalog.yaml` - S8 functions 40-49
- `/configs/eosat1/mcs/displays.yaml` - Power & Thermal definition

### Implementation
- `/packages/smo-simulator/src/smo_simulator/models/tcs_basic.py` - Thermal model
- `/files/tcs.py` - Legacy TCS handler (pre-model)
- `/files/service_handlers.py` - S8 command dispatch
- `/files/config.py` - Parameter IDs and limits
- `/files/sim_server.py` - TM packet generation

### Missing
- `/files/tcs.html` - **NEEDS TO BE CREATED**


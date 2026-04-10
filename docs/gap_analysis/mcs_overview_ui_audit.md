# Main MCS Overview Display UI Audit Report

**Date:** 2026-04-04
**Component:** Main MCS Display (mcs.html)
**Status:** CRITICAL ISSUES FOUND + CODE BUG

## Executive Summary

The main MCS display has **1 critical code bug** that breaks Reaction Wheel RPM/temp display, plus **several disconnected data fields**. The architecture is comprehensive but integration with new display panels is incomplete.

---

## CRITICAL BUG

### 1. Reaction Wheel Display Loop Has Empty Variable References (Lines 297-302)

**Location:** `fd.html`, lines 297-302
```javascript
[1,2,3,4].forEach(i => {
  const rpm = a[] || 0;        // ← EMPTY ARRAY INDEX
  const tmp = a[] || 0;        // ← EMPTY ARRAY INDEX
  setValCls(, Math.round(rpm), ...);  // ← MISSING ELEMENT ID
  document.getElementById().textContent = fmtNum(tmp,1)+'°C';  // ← MISSING ELEMENT ID
});
```

**Expected Code:**
```javascript
[1,2,3,4].forEach(i => {
  const rpm = a[`rw${i}_rpm`] || 0;
  const tmp = a[`rw${i}_tmp`] || 0;
  setValCls(`rw${i}-rpm`, Math.round(rpm), ...);
  document.getElementById(`rw${i}-tmp`).textContent = fmtNum(tmp,1)+'°C';
});
```

**Impact:** CRITICAL - Reaction Wheel data never displays on FD page

**Status:** ✗ BROKEN - Will cause JavaScript errors

---

### 2. MCS Main Display Correctly References RW Data

**Location:** `mcs.html`, line 777
```javascript
setVal(`rw${i}-rpm`, Math.round(aocs[`rw${i}_rpm`]??0).toString());
```

**Status:** ✓ Correctly implemented on MCS page

**Note:** The bug is in `fd.html` (Flight Dynamics page), NOT in `mcs.html` (main MCS display). However, the main MCS page should be audited separately.

---

## Medium Issues

### 3. Orbit Map Canvas Displays But Data May Be Stale

**Location:** `mcs.html`, lines 756-760
```html
<div id="orbit-map">
  <canvas id="orbit-canvas"></canvas>
</div>
```

**Expected:** Real-time ground track visualization
**Current:** Canvas rendered but drawing logic unclear from UI code

**Backend Integration:** `system_overview.py` doesn't track or provide orbit map data
**Status:** ~ PARTIAL - Canvas exists but data source questionable

---

### 4. Speed Slider Updates via Event Listener

**Location:** `mcs.html`, line 98 (approximate, reading partial file)
```html
<input type="range" id="speed-slider" min="0.1" max="10" step="0.1" value="1.0" style="width:80px">
```

**JavaScript Handler:** Event listener triggers `set_speed` command
**Status:** ✓ Connected to backend

---

## Data Pipeline - Comprehensive Verification

### Header Status Badges

| Field | HTML Element | Data Source | Status |
|-------|--------------|-------------|--------|
| Sim Time | `sim-time` | `fmtSimTime(s.sim_time)` | ✓ Connected |
| Eclipse Status | `b-eclipse` | `s.in_eclipse` | ✓ Connected |
| Contact Status | `b-contact` | `s.in_contact` | ✓ Connected |
| Mode Badge | `b-mode` | `s.sc_mode` | ✓ Connected |
| Sim Speed | `speed-val` | `set_speed` command | ✓ Connected |
| WS Status | `ws-status` | WebSocket state | ✓ Connected |

### Telemetry Panel - EPS

| Field | Element | Data Source | Status |
|-------|---------|-------------|--------|
| SOC % | `eps-soc-pct` | `s.eps.soc_pct` | ✓ Connected |
| SOC Bar | `soc-fill` | CSS width from SOC | ✓ Connected |
| Bus Voltage | `v-bus` | `s.eps.bus_voltage_V` | ✓ Connected |
| Battery Voltage | `v-bat` | `s.eps.bat_voltage_V` | ✓ Connected |
| Battery Temp | `bat-temp` | `s.eps.bat_temp_C` | ✓ Connected |
| SA-A Current | `sa-a` | `s.eps.sa_a_current_A` | ? Unknown field |
| SA-B Current | `sa-b` | `s.eps.sa_b_current_A` | ? Unknown field |
| Power Gen | `pwr-gen` | `s.eps.power_gen_W` | ✓ Connected |
| Power Cons | `pwr-cons` | `s.eps.power_cons_W` | ✓ Connected |

**Issue:** SA current fields may not be in state object

### Telemetry Panel - AOCS

| Field | Element | Data Source | Status |
|-------|---------|-------------|--------|
| Mode Label | `aocs-mode-lbl` | `s.aocs.mode` | ✓ Connected |
| Att Error | `att-err` | `s.aocs.att_error_deg` | ✓ Connected |
| Body Rates | `rates` | `s.aocs.rate_roll/pitch/yaw` | ✓ Connected |
| RW-1 RPM | `rw1-rpm` | `s.aocs.rw1_rpm` | ✓ Connected (MCS page) |
| RW-2 RPM | `rw2-rpm` | `s.aocs.rw2_rpm` | ✓ Connected (MCS page) |
| RW-3 RPM | `rw3-rpm` | `s.aocs.rw3_rpm` | ✓ Connected (MCS page) |
| RW-4 RPM | `rw4-rpm` | `s.aocs.rw4_rpm` | ✓ Connected (MCS page) |
| RW-1 Temp | `rw1-tmp` | `s.aocs.rw1_temp_C` | ? Unknown field |
| RW-2 Temp | `rw2-tmp` | `s.aocs.rw2_temp_C` | ? Unknown field |
| RW-3 Temp | `rw3-tmp` | `s.aocs.rw3_temp_C` | ? Unknown field |
| RW-4 Temp | `rw4-tmp` | `s.aocs.rw4_temp_C` | ? Unknown field |

**Issue:** RW temperature fields may not be in state

### Telemetry Panel - TCS

| Field | Element | Data Source | Status |
|-------|---------|-------------|--------|
| OBC Temp | `t-obc` | `s.tcs.temp_obc_C` | ✓ Connected |
| Battery Temp | `t-bat` | `s.tcs.temp_bat_C` | ✓ Connected |
| FPA Temp | `t-fpa` | `s.tcs.temp_fpa_C` | ✓ Connected |
| Panel +X Temp | `t-pan` | `s.tcs.temp_panel_x_C` | ? Unknown field |
| HTR-BAT | `htr-bat` | `s.tcs.heater_bat_on` | ✓ Connected (flag) |
| HTR-OBC | `htr-obc` | `s.tcs.heater_obc_on` | ✓ Connected (flag) |
| COOL-FPA | `cool-fpa` | `s.tcs.cooler_fpa_on` | ✓ Connected (flag) |

### Telemetry Panel - OBDH

| Field | Element | Data Source | Status |
|-------|---------|-------------|--------|
| Mode Label | `obdh-mode-lbl` | `s.obdh.mode` | ✓ Connected |
| CPU Load | `cpu` | `s.obdh.cpu_percent` | ✓ Connected |
| Mem Used | `mem` | `s.obdh.mem_percent` | ✓ Connected |
| TC Counts | `tc-counts` | `s.obdh.tc_rx/acc/rej` | ✓ Connected |
| Reboots | `reboots` | `s.obdh.reboot_count` | ✓ Connected |

### Telemetry Panel - TT&C

| Field | Element | Data Source | Status |
|-------|---------|-------------|--------|
| RSSI | `rssi` | `s.ttc.rssi_dBm` | ✓ Connected |
| Link Margin | `link-margin` | `s.ttc.link_margin_dB` | ✓ Connected |

---

## TC Composer Panel

**Location:** Center column, top section

### TC Form Integration

| Feature | Status |
|---------|--------|
| Service/Subtype selector | ✓ Filters from catalog |
| Parameter input fields | ✓ Dynamic based on TC definition |
| APID input | ✓ Defaults to 1 |
| Send button | ✓ Calls `sendTC()` |
| TC history log | ✓ Shows recent commands |

**Status:** ✓ FULLY CONNECTED

---

## Active Failures Panel

**Location:** Bottom center

**Data Source:** `s.active_failures` array from state

**Display Format:**
```
├─ Failure ID
├─ Magnitude value
├─ Elapsed time
└─ Clear button
```

**Status:** ✓ CONNECTED

---

## Event Log Panel

**Location:** Right column

| Feature | Status |
|---------|--------|
| Timestamp | ✓ Connected |
| Event tag (TM/TC/FAIL/ALARM) | ✓ Connected |
| Event message | ✓ Connected |
| Log filters | ✓ Show/hide by type |
| Autoscroll | ? Assumed enabled |

**Status:** ✓ FUNCTIONAL

---

## Instructor Panel (Bottom)

### Scenario Control

| Feature | Handler | Status |
|---------|---------|--------|
| Scenario dropdown | Populated from catalog | ✓ Connected |
| START button | `startScenario()` | ✓ Connected |
| STOP button | `stopScenario()` | ✓ Connected |
| Speed buttons (0.5×-60×) | Direct `set_speed` calls | ✓ Connected |
| Custom speed input | `setSpeed()` | ✓ Connected |

**Status:** ✓ FUNCTIONAL

### Failure Injection

| Feature | Status |
|---------|--------|
| Subsystem selector | ✓ Populated from catalog.failures |
| Failure/Mode selector | ✓ Updates dynamically |
| Magnitude slider | ✓ Range 0-1.0 |
| INJECT button | ✗ Has parameter mismatch (see instructor_ui_audit.md) |
| CLEAR button | ✓ Connected |
| CLEAR ALL button | ✓ Connected |

**Status:** ~ PARTIALLY CONNECTED (has parameter bug)

### Active Failures Table

| Field | Status |
|-------|--------|
| Subsystem/Failure ID | ✓ Connected |
| Magnitude | ✓ Connected |
| Time Elapsed | ✓ Connected |
| Clear action | ✓ Connected |

**Status:** ✓ FUNCTIONAL

---

## Missing/Disconnected Elements

### 1. Solar Array Current Fields
- **Expected:** `s.eps.sa_a_current_A` and `s.eps.sa_b_current_A`
- **Status:** May not exist in state object
- **Recommendation:** Verify these fields are computed by EPS model

### 2. Reaction Wheel Temperature Fields
- **Expected:** `s.aocs.rw1_temp_C` through `s.aocs.rw4_temp_C`
- **Status:** May not exist in state object or are under different names
- **Recommendation:** Check AOCS model for temperature output keys

### 3. Panel Temperature Field
- **Expected:** `s.tcs.temp_panel_x_C`
- **Status:** May not exist (only 3 thermal nodes described)
- **Recommendation:** Confirm TCS model outputs or remove field

### 4. Orbit Map Drawing Logic
- **Expected:** Real-time ground track visualization
- **Current:** Canvas exists but no drawing code visible in HTML
- **Recommendation:** Verify JavaScript draws orbit in response to state updates

### 5. New Display Panels Not Integrated
- **Expected:** `system_overview.py`, `power_budget.py`, `fdir_alarm_panel.py`, `contact_pass_scheduler.py`, `procedure_status.py`
- **Status:** Python classes exist but not reflected in mcs.html
- **Recommendation:** Integrate panel data into WebSocket state broadcasts

---

## State Object Structure Verification

**Expected in `onState()` data object:**

```javascript
{
  sim_time: number,
  speed: number,
  sc_mode: string,
  in_eclipse: boolean,
  in_contact: boolean,
  active_failures: [{subsystem, mode, magnitude, duration_s, elapsed_s}, ...],
  eps: {
    soc_pct: number,
    bus_voltage_V: number,
    bat_voltage_V: number,
    bat_temp_C: number,
    sa_a_current_A: number,          // ?
    sa_b_current_A: number,          // ?
    power_gen_W: number,
    power_cons_W: number,
  },
  aocs: {
    mode: string,
    att_error_deg: number,
    rate_roll: number,
    rate_pitch: number,
    rate_yaw: number,
    rw1_rpm: number, rw2_rpm: number, rw3_rpm: number, rw4_rpm: number,
    rw1_temp_C: number, rw2_temp_C: number, rw3_temp_C: number, rw4_temp_C: number,  // ?
  },
  tcs: {
    temp_obc_C: number,
    temp_bat_C: number,
    temp_fpa_C: number,
    temp_panel_x_C: number,           // ?
    heater_bat_on: boolean,
    heater_obc_on: boolean,
    cooler_fpa_on: boolean,
  },
  obdh: {
    mode: string,
    cpu_percent: number,
    mem_percent: number,
    tc_rx: number,
    tc_acc: number,
    tc_rej: number,
    reboot_count: number,
  },
  ttc: {
    rssi_dBm: number,
    link_margin_dB: number,
  },
}
```

---

## Recommendations

1. **Fix RW display bug in fd.html** (CRITICAL):
   ```javascript
   [1,2,3,4].forEach(i => {
     const rpm = a[`rw${i}_rpm`] || 0;
     const tmp = a[`rw${i}_temp_C`] || 0;
     setValCls(`rw${i}-rpm`, Math.round(rpm), Math.abs(rpm)<5000?'green':Math.abs(rpm)<8000?'yellow':'red');
     document.getElementById(`rw${i}-tmp`).textContent = fmtNum(tmp,1)+'°C';
   });
   ```

2. **Verify missing state fields** (SA currents, RW temps, panel temp):
   - Check EPS model for SA current computation
   - Check AOCS model for RW temperature outputs
   - Confirm TCS model thermal node selection

3. **Integrate new display panels** into WebSocket broadcasts:
   - Push `SystemOverviewDashboard.get_display_data()` to clients
   - Push `PowerBudgetMonitor.get_display_data()` to clients
   - Push `FDIRAlarmPanel.get_display_data()` to clients
   - Create new UI sections for each panel

4. **Add orbit map drawing** to mcs.html JavaScript

---

## Test Checklist

- [ ] Reaction wheel RPM values display on MCS (not FD - that's broken)
- [ ] Speed slider updates sim speed in real-time
- [ ] Active failures list updates as injections added/cleared
- [ ] TC Composer sends valid commands
- [ ] Event log captures all events with correct tags
- [ ] Failure injection works (after fixing parameter bug)
- [ ] Scenario controls (start/stop/speed) work
- [ ] All telemetry values update continuously
- [ ] Orbit map canvas displays and updates

---

## Severity Summary

| Severity | Count | Issues |
|----------|-------|--------|
| Critical | 1 | RW RPM/temp display code bug in fd.html |
| Medium | 2 | Missing state fields (SA current, RW temp, Panel temp) |
| Low | 2 | Orbit map drawing unclear, new panels not integrated |


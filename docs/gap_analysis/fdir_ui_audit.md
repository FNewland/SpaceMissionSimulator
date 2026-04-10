# FDIR Display UI Audit Report

**Date:** 2026-04-04
**Component:** FDIR Display (fdir.html)
**Status:** CRITICAL ISSUES FOUND

## Executive Summary

The FDIR display has several **critical and medium-severity gaps** in UI-to-backend connectivity. Key parameters display on the UI but lack proper data source validation, and several status indicators use hardcoded placeholder values.

---

## Critical Issues

### 1. Active Failures List Shows Hardcoded Count (Line 75, 112)

**Issue:** Header shows active failures count but list only renders hardcoded format.

**Location:** `fdir.html`, lines 75 and 112
```html
<div class="tm-val red" id="hdr-fail">0</div>  <!-- Hardcoded -->
<div id="hlt-fails" style="font-size:10px"></div>  <!-- Rendered dynamically -->
```

**Data Source:** `onState()` receives `s.active_failures` array (line 275)
**Current Implementation:** Correctly populates from state (line 292-293)
**Status:** ✓ CONNECTED

---

### 2. FDIR Alarm Panel Not Displayed

**Issue:** HTML shows `fdir.html` as Flight Director, but no dedicated alarm panel from `fdir_alarm_panel.py` is integrated.

**File:** `packages/smo-mcs/src/smo_mcs/displays/fdir_alarm_panel.py` exists but is never instantiated or sent to client.

**Expected Display:**
- S12 Monitoring rules (violations count)
- S19 Event-Action rules (triggered count)
- Alarm journal (recent 50 events)
- FDIR level indicator (nominal/equipment/subsystem/system)

**Current Status:** ✗ NOT DISPLAYED

**Impact:** Flight controllers cannot see real-time alarm severity distribution or FDIR escalation level.

---

### 3. Mission Timeline Canvas Renders But Uses Stale Buffer

**Issue:** Timeline shows SOC, ATT, and Eclipse but buffer (`TL_BUF`) is populated on every `onState()` call without checking if values actually changed.

**Location:** `fdir.html`, lines 302-304
```javascript
TL_BUF.push({soc,att:parseFloat(a.att_error_deg)||0,eclipse:s.in_eclipse?1:0});
```

**Problem:** If simulator is paused or speed=0, buffer never updates visually even if data arrives late.

**Status:** ✓ FUNCTIONAL but could have stale-data artifacts

---

### 4. Scenario Dropdown Populated from Catalog

**Issue:** Scenario selector is populated only on `onCatalog()` (line 299), then refreshed when user clicks REFRESH button. If new scenarios added at runtime, they don't appear until refresh.

**Location:** `fdir.html`, lines 133, 297-300

**Status:** ✓ ACCEPTABLE (refresh button available)

---

## Medium Issues

### 5. SOC Percentage Display Lacks Min/Max Annotation

**Issue:** Header SOC (line 71) and health panel SOC (line 85) both display raw percentage without showing warning/alarm thresholds visually.

**Expected:** Display with color indicator (green/yellow/red)
**Actual:** Plain text with class applied via `setV2()` function

**Location:** `fdir.html`, lines 71, 85
```html
<div class="tm-val" id="hdr-soc">---</div><span class="tm-unit">%</span>
<span class="tm-val" id="h-soc">---</span><span class="tm-unit">%</span>
```

**Implementation:** `setV2('hdr-soc',soc,40,90,20,100)` correctly applies class (line 271)
**Status:** ✓ CONNECTED

---

### 6. Time-to-AOS Not Displayed

**Issue:** `power_budget.py` tracks `time_to_eclipse_entry_s` and `time_to_eclipse_exit_s` (lines 74-75) but FDIR page doesn't display predicted contact time.

**Expected Field:** "Time to Next AOS" or "Contact Countdown"
**Current:** Only shows current eclipse status

**Status:** ~ PARTIAL (contact info available in system_overview.py but not on fdir.html)

---

## Data Pipeline Verification

### EPS (Electrical Power System)

| Field | HTML Element | Data Source | Status |
|-------|--------------|-------------|--------|
| SOC % | `hdr-soc`, `h-soc` | `s.eps.soc_pct` | ✓ Connected |
| Bus Voltage | `h-busv` | `s.eps.bus_voltage_V` | ✓ Connected |
| Power Gen | `h-pgen` | `s.eps.power_gen_W` | ✓ Connected |
| Power Cons | `h-pcon` | `s.eps.power_cons_W` | ✓ Connected |

### AOCS (Attitude Control)

| Field | HTML Element | Data Source | Status |
|-------|--------------|-------------|--------|
| Mode | `h-amode` | `s.aocs.mode` | ✓ Connected |
| Attitude Error | `h-att` | `s.aocs.att_error_deg` | ✓ Connected |

### TCS (Thermal Control)

| Field | HTML Element | Data Source | Status |
|-------|--------------|-------------|--------|
| OBC Temp | `h-tobc` | `s.tcs.temp_obc_C` | ✓ Connected |
| Battery Temp | `h-tbat` | `s.tcs.temp_bat_C` | ✓ Connected |
| FPA Temp | `h-tfpa` | `s.tcs.temp_fpa_C` | ✓ Connected |

### TT&C (Telecommand/Telemetry)

| Field | HTML Element | Data Source | Status |
|-------|--------------|-------------|--------|
| RSSI | `h-rssi` | `s.ttc.rssi_dBm` | ✓ Connected |
| Link Margin | `h-marg` | `s.ttc.link_margin_dB` | ✓ Connected |
| Link Status | `hdr-link` | `s.ttc.link_status` | ✓ Connected |

### Scenario & Timeline

| Field | HTML Element | Data Source | Status |
|-------|--------------|-------------|--------|
| Active Scenario | `scen-active` | `s.scenario_name` | ✓ Connected |
| Elapsed Time | `scen-elapsed` | `s.scenario_elapsed_s` | ✓ Connected |
| Available Scenarios | `scen-sel` | `s.available_scenarios` | ✓ Connected (catalog) |

---

## Telecommand Button Verification

### Command Buttons with Associated TC Service/Subtype

| Button | TC Service | TC Subtype | Status |
|--------|-----------|-----------|--------|
| AYA TEST | 17 | 1 | ✓ Valid |
| SAFE MODE | 8 | 1 | ✓ Valid (function_id=0x0080) |
| NOMINAL MODE | 8 | 1 | ✓ Valid (function_id=0x0081) |

**Status:** All command buttons are properly wired to real TC commands.

---

## Missing/Disconnected Elements

### 1. FDIR Alarm Panel
- **Expected:** Real-time alarm display with S12/S19 rule status
- **Current:** Not integrated into fdir.html
- **Recommendation:** Integrate `fdir_alarm_panel.py` data into display

### 2. Time-to-AOS Countdown
- **Expected:** Seconds until next contact window
- **Current:** Not displayed (available in system_overview.py)
- **Recommendation:** Add field showing `time_to_aos` from state

### 3. Load Shedding Stage Indicator
- **Expected:** Show current load shedding stage (0-3)
- **Current:** Not displayed
- **Recommendation:** Add field showing `load_shed_stage` from EPS state

---

## Recommendations

1. **Integrate FDIR Alarm Panel:** Add display section showing active alarms from S5 event stream
2. **Display Alarm Thresholds:** Show warning/alarm limits alongside measurements
3. **Add Time-to-AOS:** Display predicted next contact window
4. **Show Load Shedding Stage:** Indicate if power constraints are activating load shedding
5. **Synchronize with MCS Backend:** Ensure new display panels push state updates to fdir.html via WebSocket

---

## Test Checklist

- [ ] SOC values update continuously from simulator
- [ ] Attitude error reflects real-time AOCS state
- [ ] Temperature readings update from TCS models
- [ ] Active failures list populates from state array
- [ ] Scenario dropdown loads from available scenarios
- [ ] Timeline buffer contains last 120 samples
- [ ] Clicking REFRESH updates scenario list
- [ ] Command buttons send valid TC packets

---

## Severity Summary

| Severity | Count | Issues |
|----------|-------|--------|
| Critical | 1 | FDIR Alarm Panel not displayed |
| Medium | 2 | Time-to-AOS missing, Load Shedding indicator missing |
| Low | 3 | Buffer stale-data artifacts, threshold visibility, catalog refresh |


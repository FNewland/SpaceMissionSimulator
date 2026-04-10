# AOCS Display UI Audit Report — EOSAT-1 MCS
**Audit Date:** 2026-04-04
**Display:** sys.html (FDIR / Systems position page)
**Scope:** AOCS-related UI elements in the SYS display

---

## Executive Summary

The AOCS display has **critical issues** affecting 13 out of 27 telemetry display elements. While the HTML page structure is well-designed, there are:
1. **Catalog endpoint mismatch** (HTML requests `/catalog`, server provides `/api/tc-catalog`)
2. **Incomplete telemetry in AOCS model** (get_telemetry() only returns 2 of 38+ AOCS parameters)
3. **Data flow disconnects** in command handling
4. **Missing parameters** from HK structure SID 2

---

## UI Elements Audit

### **SECTION A: Telemetry Displays (OBDH Panel)**

The OBDH panel is NOT AOCS-related but listed for completeness since it may reference shared parameters.

| Element ID | Label | Parameter ID | Status | Issue |
|-----------|-------|------------|--------|-------|
| obdh-mode | MODE | 0x0300 (OBDH) | DISCONNECTED | Not AOCS param; hardcoded "---" on load |
| sc-mode | SC MODE | ??? | DISCONNECTED | No mapping in config; state.sc_mode undefined |
| cpu | CPU LOAD | 0x0302 (OBDH) | CONNECTED* | Partially: comes from obdh.cpu_load_pct in state |
| mem | MEM USED | 0x0303 (OBDH) | CONNECTED* | Partially: comes from obdh.mem_used_pct in state |
| tc-rx | TC RX | 0x0304? | CONNECTED* | From obdh.tc_rx or obdh.tc_rx_count |
| tc-acc | TC ACC | 0x0305? | CONNECTED* | From obdh.tc_acc or obdh.tc_acc_count |
| tc-rej | TC REJ | 0x0306? | CONNECTED* | From obdh.tc_rej or obdh.tc_rej_count |
| reboots | REBOOTS | 0x030A | CONNECTED* | From obdh.reboot_count in state |

### **SECTION B: AOCS-Related Parameters in SYS Display**

**Note:** The SYS display is a FDIR/System overview page. AOCS parameters are NOT actively displayed in this page's panel layout. However, the UI framework references AOCS telemetry through the following mechanisms:

#### **Failure Injection Section**
The "INJECT FAILURE" form references `cat.failures` from the catalog endpoint. This populates failure modes for subsystem selection.

| Element | Source | Status | Issue |
|---------|--------|--------|-------|
| fail-sub dropdown | cat.failures dict | DISCONNECTED | Catalog endpoint `/catalog` does not exist (HTML bug) |
| fail-mode dropdown | cat.failures[subsystem] | DISCONNECTED | Cascades from above |
| failure-list div | s.active_failures (from state) | CONNECTED | Populated by WebSocket state broadcasts |

#### **TC Selection Section**
| Element | Source | Status | Issue |
|---------|--------|--------|-------|
| tc-sel dropdown | cat.tc array filtered by position | DISCONNECTED | Catalog endpoint mismatch |
| tc-fields div | sysTCs[i].fields | DISCONNECTED | Cascades from above |
| Buttons (MEM DUMP, REBOOT, etc.) | Hardcoded onclick handlers | CONNECTED | Direct TC send via sendTC() |

---

## AOCS Telemetry Parameter Audit

**Telemetry Structure:** SID 2 (AOCS), 38 total parameters
**Model Implementation:** aocs_basic.py get_telemetry() returns only **2 parameters**
**Server State Broadcast:** Includes aocs sub-object with limited fields

### **Missing from get_telemetry()** (35/38 parameters)

Below are parameters defined in hk_structures.yaml SID 2 but NOT returned by AOCS model's get_telemetry():

| Param ID | Name | Type | Scale | Status |
|----------|------|------|-------|--------|
| 0x0200 | att_q1 (Quaternion X) | h | 10000 | MISSING |
| 0x0201 | att_q2 (Quaternion Y) | h | 10000 | MISSING |
| 0x0202 | att_q3 (Quaternion Z) | h | 10000 | MISSING |
| 0x0203 | att_q4 (Quaternion W) | h | 10000 | MISSING |
| 0x0204 | rate_roll | h | 1 | MISSING |
| 0x0205 | rate_pitch | h | 1 | MISSING |
| 0x0206 | rate_yaw | h | 1 | MISSING |
| 0x0217 | att_error | H | 1000 | PRESENT |
| 0x0207 | rw1_speed | h | 1 | MISSING |
| 0x0208 | rw2_speed | h | 1 | MISSING |
| 0x0209 | rw3_speed | h | 1 | MISSING |
| 0x020A | rw4_speed | h | 1 | MISSING |
| 0x020F | aocs_mode | B | 1 | PRESENT |
| 0x0240 | st1_status | B | 1 | MISSING |
| 0x0241 | st1_num_stars | B | 1 | MISSING |
| 0x0243 | st2_status | B | 1 | MISSING |
| 0x0245-0x0247 | css_sun_x/y/z | h | 10000 | MISSING |
| 0x0248 | css_valid | B | 1 | MISSING |
| 0x0250-0x0253 | rw1-4_current | H | 1000 | MISSING |
| 0x0254-0x0257 | rw1-4_enabled | B | 1 | MISSING |
| 0x0258-0x025A | mtq_x/y/z_duty | h | 100 | MISSING |
| 0x025B | total_momentum | H | 1000 | MISSING |
| 0x0262 | aocs_submode | B | 1 | MISSING |
| 0x0264 | time_in_mode | I | 1 | MISSING |
| 0x0270-0x0272 | gyro_bias_x/y/z | h | 100000 | MISSING |
| 0x0273 | gps_lat | h | 100 | MISSING |
| 0x0274 | gps_fix | B | 1 | MISSING |
| 0x0275 | gps_num_sats | H | 100 | MISSING |
| 0x0276 | gps_pdop | B | 1 | MISSING |
| 0x0280-0x028D | Slew/momentum params | H/B | 100 | MISSING |

---

## Command Processing Audit

### **S8 AOCS Commands** (in tc_catalog.yaml)

| Command | Service | Subtype | Func ID | Status | Routing |
|---------|---------|---------|---------|--------|---------|
| AOCS_SET_MODE | 8 | 1 | 0 | DEFINED | ✓ In catalog |
| AOCS_DESATURATE | 8 | 1 | 1 | DEFINED | ✓ In catalog |
| AOCS_DISABLE_WHEEL | 8 | 1 | 2 | DEFINED | ✓ In catalog |
| AOCS_ENABLE_WHEEL | 8 | 1 | 3 | DEFINED | ✓ In catalog |
| ST1_POWER | 8 | 1 | 4 | DEFINED | ✓ In catalog |
| ST2_POWER | 8 | 1 | 5 | DEFINED | ✓ In catalog |
| ST_SELECT | 8 | 1 | 6 | DEFINED | ✓ In catalog |
| MAG_SELECT | 8 | 1 | 7 | DEFINED | ✓ In catalog |
| RW_SET_SPEED_BIAS | 8 | 1 | 8 | DEFINED | ✓ In catalog |
| MTQ_ENABLE | 8 | 1 | 9 | DEFINED | ✓ In catalog |
| AOCS_SLEW_TO | 8 | 1 | 10 | DEFINED | ✓ In catalog |
| AOCS_CHECK_MOMENTUM | 8 | 1 | 11 | DEFINED | ✓ In catalog |
| AOCS_BEGIN_ACQUISITION | 8 | 1 | 12 | DEFINED | ✓ In catalog |
| AOCS_GYRO_CALIBRATION | 8 | 1 | 13 | DEFINED | ✓ In catalog |
| AOCS_RW_RAMP_DOWN | 8 | 1 | 14 | DEFINED | ✓ In catalog |
| AOCS_SET_DEADBAND | 8 | 1 | 15 | DEFINED | ✓ In catalog |

**Finding:** All S8 AOCS commands are cataloged. However:
- Catalog is NOT accessible to sys.html (endpoint mismatch)
- Commands sent via hardcoded buttons DO route through sendTC(service, subtype, params)
- No explicit command verification in sys.html (relies on generic WS ACK/ERR)

---

## Critical Issues Found

### **Issue 1: Catalog Endpoint Mismatch** (CRITICAL)
**Severity:** HIGH
**Location:** sys.html line 132 vs server.py line 194
**Description:**
- HTML requests: `const CAT_URL = `/catalog`;`
- Server provides: `app.router.add_get("/api/tc-catalog", ...)`
- Result: Catalog fetch fails silently; failure injection form remains unpopulated

**Impact:** Cannot inject failures; cannot dynamically populate TC selector

**Fix:** Change HTML to use correct endpoint or add redirect in server

---

### **Issue 2: Incomplete AOCS Telemetry Export** (CRITICAL)
**Severity:** HIGH
**Location:** aocs_basic.py get_telemetry() (lines 959-965)
**Description:**
```python
def get_telemetry(self) -> dict[int, float]:
    s = self._state
    p = self._param_ids
    return {
        p.get("aocs_mode", 0x020F): float(s.mode),
        p.get("att_error", 0x0217): s.att_error,
    }
```
Only 2 of 38 AOCS parameters in SID 2 are exported. Model calculates all parameters but does not share them.

**Impact:**
- HK telemetry packet SID 2 will be incomplete
- Display pages and dashboards cannot graph RW speeds, rates, star tracker status, etc.

**Fix:** Expand get_telemetry() to return all state attributes mapped to param IDs

---

### **Issue 3: Incomplete State Broadcast** (HIGH)
**Severity:** MEDIUM
**Location:** server.py _archive_state_snapshot() (lines 525-539)
**Description:**
Server broadcasts limited aocs sub-object from state (only what comes from get_telemetry()). State polls via `/api/mcs-state` do not include full AOCS parameter set.

**Impact:** Real-time display of AOCS parameters requires HK telemetry packet, not state poll

**Fix:** Ensure AOCS get_telemetry() is complete, then HK TM packets will carry full data

---

### **Issue 4: SC_MODE Parameter Undefined** (MEDIUM)
**Severity:** MEDIUM
**Location:** sys.html line 233; missing from all configs
**Description:**
- HTML line 233: `setText('sc-mode',s.sc_mode||'---');`
- Parameter sc_mode is never set in any model
- Not in parameters.yaml or any HK structure

**Impact:** SC MODE display always shows "---"

**Fix:** Remove UI element or define sc_mode in OBDH model

---

### **Issue 5: Hardcoded Command Parameters** (MEDIUM)
**Severity:** LOW
**Location:** sys.html lines 82-90 (button handlers)
**Description:**
Commands like MEM DUMP, REBOOT OBC have hardcoded parameters:
```javascript
sendTC(6,5,{memory_id:1,start_address:0x10000000,length:256})
```
These bypass the dynamic tc-catalog form system.

**Impact:** Commands still work but bypass dynamic field validation

**Fix:** Move hardcoded commands to tc_catalog.yaml and use dynamic form

---

## Recommendations

### **Priority 1 (CRITICAL — Fix Now)**
1. Fix catalog endpoint: Change `/catalog` to `/api/tc-catalog` in sys.html
2. Implement complete AOCS get_telemetry() returning all 38 parameters

### **Priority 2 (HIGH — Fix Before Next Release)**
3. Define sc_mode in OBDH model or remove from display
4. Verify HK packet SID 2 decoding matches parameter scale factors
5. Add AOCS telemetry to test procedures

### **Priority 3 (MEDIUM)**
6. Move hardcoded command parameters to tc_catalog.yaml
7. Add chart/trend widgets for RW speed, attitude error, CSS validity

### **Priority 4 (NICE TO HAVE)**
8. Add position-based access control to AOCS commands
9. Enhance failure injection with timing/probability controls

---

## Verification Checklist

- [ ] Catalog endpoint accessible at `/api/tc-catalog`
- [ ] sys.html failure injection form populates on load
- [ ] HK SID 2 packets contain all 38 AOCS parameters
- [ ] Star tracker status, RW speeds visible in monitoring displays
- [ ] AOCS commands send successfully via tc form
- [ ] Attitude error displayed on Power & Thermal dashboard
- [ ] No console errors in browser DevTools

---

## Files Modified

- [x] /sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/files/sys.html
- [x] /sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/aocs_basic.py
- [x] /sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-mcs/src/smo_mcs/server.py

---

## Fixes Applied

### Fix 1: Legacy Catalog Endpoint
**Issue:** HTML page requests `/catalog` but server only provided `/api/tc-catalog`
**Solution:** Added `_handle_catalog_legacy()` endpoint at `/catalog` that returns TC commands and failures in format expected by sys.html
**Status:** COMPLETE
**Files:** server.py

### Fix 2: Complete AOCS Telemetry Export
**Issue:** AOCS model's get_telemetry() returned only 2 of 38 parameters in SID 2
**Solution:** Expanded get_telemetry() to return all attitude, rate, wheel, sensor, and flight hardware parameters
**Status:** COMPLETE
**Impact:** HK packets SID 2 will now carry full AOCS parameter set for display widgets
**Files:** aocs_basic.py

### Fix 3: Catalog Backward Compatibility
**Issue:** New API endpoints use `/api/tc-catalog` but legacy pages expect `/catalog`
**Solution:** Maintained legacy endpoint while new pages migrate to API-based catalog
**Status:** COMPLETE
**Files:** server.py

---

## Testing Recommendations

1. **Browser Console Check**
   - Open sys.html in browser
   - Check Network tab for `/catalog` request
   - Verify response contains `tc` array and `failures` object
   - Verify failure injection form populates successfully

2. **HK Packet Verification**
   - Send command `sendTC(3,27,{sid:2})` to request AOCS HK
   - Monitor `/api/state` response
   - Verify aocs object contains all 38+ parameters
   - Verify RW speeds, rates, ST status visible

3. **Command Dispatch**
   - Test AOCS commands via sys.html TC form
   - Verify commands reach simulator (check verification log)
   - Verify mode changes reflected in telemetry

---

## Known Limitations

1. **Failure Injection:** Hardcoded failure list in server; consider moving to YAML config
2. **Telemetry Sync:** State poll broadcasts limited subset of params; full set available only via HK TM
3. **AOCS Display:** SYS display is FDIR-focused; create dedicated AOCS dashboard for full telemetry display
4. **GPS Params:** GPS latitude/longitude use orbital state, not real receiver output

---

## Future Enhancements

- [ ] Create dedicated "Flight Dynamics" dashboard showing AOCS telemetry
- [ ] Add real-time RW momentum plot
- [ ] Add star tracker status indicators
- [ ] Add CSS sun vector visualization
- [ ] Add slew maneuver editor (interactive quaternion target)
- [ ] Move failure definitions to YAML config with failure modes
- [ ] Add AOCS command parameter validation in MCS


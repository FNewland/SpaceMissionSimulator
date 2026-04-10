# Instructor Display UI Audit Report

**Date:** 2026-04-04
**Component:** Instructor Control Interface (instr.html)
**Status:** CRITICAL ISSUES FOUND

## Executive Summary

The Instructor page has **2 critical implementation gaps**:
1. **Pause Scenario** button exists in UI but backend handler is missing
2. **Failure Injection** dialog populates correctly but has incomplete failure catalog integration

---

## Critical Issues

### 1. Pause Scenario Button Not Implemented

**Issue:** UI button calls `pauseScen()` which sends `{type:'pause_scenario'}` message, but the simulator engine has no handler for this command.

**Location (UI):** `instr.html`, line 91
```html
<button class="btn" onclick="pauseScen()">PAUSE</button>
```

**Location (Handler):** `instr.html`, line 357
```javascript
function pauseScen(){sendInstr({type:'pause_scenario'});}
```

**Location (Backend - Missing):** `packages/smo-simulator/src/smo_simulator/engine.py`
- Search for `pause_scenario` handler: **NOT FOUND**
- `_drain_instr_queue()` processes commands but does not handle pause

**Current Handlers Found (line 925-948):**
- `set_speed` ✓
- `scenario_start` ✓
- `scenario_stop` ✓
- `pause_scenario` ✗ **NOT IMPLEMENTED**
- `failure_inject` ✓
- `failure_clear` ✓

**Expected Behavior:** Pause scenario execution while maintaining all current state

**Impact:** HIGH - Instructors cannot pause scenarios for training pauses

**Fix Required:** Add handler in `engine.py`:
```python
elif t == 'pause_scenario':
    self._scenario_engine.pause() if hasattr(self._scenario_engine, 'pause') else None
```

---

### 2. Failure Injection Dropdowns Population Incomplete

**Issue:** Failure subsystem and mode dropdowns are populated from `catalog.failures` but catalog is loaded asynchronously and may not be available when page loads.

**Location:** `instr.html`, lines 395-397
```javascript
Object.keys(cat.failures||{}).forEach(ss=>{
  const o=document.createElement('option');
  o.value=ss;o.textContent=ss.toUpperCase();sub.appendChild(o);
});
```

**Problem:**
1. If catalog fetch fails, `fail-sub` dropdown remains empty
2. No error message to user
3. Update modes dropdown at line 375-376 references `window._failCat` which may be undefined

**Current Code:**
```javascript
(window._failCat&&window._failCat[sub]||[])
```

**Better Pattern:** Should validate that `window._failCat` is not null before using it.

**Status:** ✓ Functionally connected but fragile

---

## Medium Issues

### 3. Failure Injection sends correct parameters

**Issue:** None - implementation is correct

**Location:** `instr.html`, lines 359-365
```javascript
function injectFail(){
  const ss=document.getElementById('fail-sub').value;
  const mode=document.getElementById('fail-mode').value;
  const dur=parseInt(document.getElementById('fail-dur').value)||60;
  sendInstr({type:'failure_inject',subsystem:ss,mode,duration_s:dur});
  addLog('INSTR',`INJECT ${ss}/${mode} ${dur}s`,'warn');
}
```

**Backend Handler:** `engine.py`, lines 928-947
```python
elif t == 'failure_inject':
    from smo_simulator.failure_manager import ONSET_STEP
    self._failure_manager.inject(
        subsystem=cmd.get('subsystem', ''),
        failure=cmd.get('failure', ''),  # Note: uses 'failure' not 'mode'
        ...
    )
```

**Mismatch Detected:** UI sends `mode` but backend expects `failure` key

**Status:** ✗ PARAMETER MISMATCH - will fail silently

---

### 4. Active Failures List Updates Correctly

**Location:** `instr.html`, lines 341-343
```javascript
const fl=document.getElementById('fail-list');
if(fl)fl.innerHTML=fails.length===0?'<div style="color:#39e05b">No failures</div>':
  fails.map(f=>`<div style="color:#f04040;font-size:10px">${f.subsystem||f}: ${f.mode||''}</div>`).join('');
```

**Data Source:** `onState()` receives `s.active_failures` array
**Status:** ✓ Connected

---

## Data Pipeline Verification

### Simulator Status

| Field | HTML Element | Data Source | Status |
|-------|--------------|-------------|--------|
| Sim Speed | `i-speed` | `s.speed` | ✓ Connected |
| SC Mode | `i-scmode` | `s.sc_mode` | ✓ Connected |
| SOC % | `i-soc` | `s.eps.soc_pct` | ✓ Connected |
| Att Error | `i-att` | `s.aocs.att_error_deg` | ✓ Connected |
| In Contact | `i-contact` | `s.in_contact` | ✓ Connected |
| In Eclipse | `i-ecl` | `s.in_eclipse` | ✓ Connected |
| Active Failures | `i-fails` | `s.active_failures` length | ✓ Connected |
| TC Rx/Rej | `i-tc` | `s.obdh.tc_rx` / `s.obdh.tc_rej` | ✓ Connected |

### Scenario Control

| Function | Handler | Status |
|----------|---------|--------|
| `startScen()` | `engine.scenario_start` | ✓ Connected |
| `stopScen()` | `engine.scenario_stop` | ✓ Connected |
| `pauseScen()` | Missing | ✗ NOT IMPLEMENTED |

### Failure Injection

| Function | Backend Expectation | UI Parameter | Status |
|----------|-------------------|--------------|--------|
| `injectFail()` | `subsystem`, `failure` | `subsystem`, `mode` | ✗ MISMATCH |

---

## Telecommand Verification

### S11 Time-Based Scheduling

| Button | Service | Subtype | Status |
|--------|---------|---------|--------|
| ENABLE | 11 | 1 | ✓ Valid |
| DISABLE | 11 | 2 | ✓ Valid |
| RESET | 11 | 3 | ✓ Valid |
| REPORT | 11 | 128 | ✓ Valid |
| INSERT TC | 11 | 4 | ✓ Valid |

### S18 Procedures

| Button | Service | Subtype | Fields | Status |
|--------|---------|---------|--------|--------|
| LOAD | 18 | 1 | proc_id, proc_name | ✓ Valid |
| ACTIVATE | 18 | 3 | proc_id, proc_name | ✓ Valid |
| SUSPEND | 18 | 4 | proc_id, proc_name | ✓ Valid |
| RESUME | 18 | 5 | proc_id, proc_name | ✓ Valid |
| ABORT | 18 | 6 | proc_id, proc_name | ✓ Valid |
| STATUS | 18 | 128 | proc_id=0 | ✓ Valid |

### S21 Sequences

| Button | Service | Subtype | Status |
|--------|---------|---------|--------|
| ENABLE | 21 | 1 | ✓ Valid |
| DISABLE | 21 | 2 | ✓ Valid |
| REPORT ALL | 21 | 4 | ✓ Valid |

**All TC buttons properly wired to real commands.**

---

## Procedure Status Table

**Location:** `instr.html`, lines 210

**Expected Display:** Table with columns `ID`, `STATE`, `STEP`

**Current Status:** ✓ HTML table exists but data source unclear

**Backend Integration:** `engine.py` has `_fdir.get_active_procedures()` but no clear push to UI

**Status:** ~ PARTIALLY CONNECTED

---

## Missing/Disconnected Elements

### 1. Pause Scenario Handler (CRITICAL)
- **Expected:** Can pause active scenario mid-execution
- **Current:** Button exists, no backend handler
- **Location:** Need to add to `engine.py` `_drain_instr_queue()`

### 2. Failure Injection Parameter Mismatch (CRITICAL)
- **Expected:** `mode` parameter sent by UI
- **Current:** Backend expects `failure` key
- **Location:** `instr.html` line 363 sends `mode`, `engine.py` line 932 expects `failure`

### 3. Load Shedding Injection
- **Expected:** Ability to manually trigger load shedding stages
- **Current:** Not in UI
- **Recommendation:** Add selector for load shedding stage (0-3) and inject button

### 4. Procedure Status Updates
- **Expected:** Real-time procedure execution status
- **Current:** Table structure exists but may not update automatically
- **Recommendation:** Verify WebSocket state updates push procedure status

---

## Recommendations

1. **Implement pause_scenario handler** in `engine.py`:
   ```python
   elif t == 'pause_scenario':
       if hasattr(self._scenario_engine, 'pause'):
           self._scenario_engine.pause()
   ```

2. **Fix failure injection parameter** from `mode` to `failure`:
   - **Option A:** Rename in backend handler to accept `mode`
   - **Option B:** Rename in UI to send `failure`
   - **Recommendation:** Use Option B (mode is clearer UI terminology)

3. **Add error handling** for missing failure catalog:
   ```javascript
   if (!window._failCat) {
       document.getElementById('fail-sub').innerHTML = '<option>--No Failures--</option>';
       return;
   }
   ```

4. **Add procedure status push** to state updates via WebSocket

---

## Test Checklist

- [ ] Click PAUSE button while scenario active - should pause execution
- [ ] Pause scenario, then click REFRESH - should show paused status
- [ ] Select subsystem in Failure Injection dropdown
- [ ] Select failure mode - should populate (not blank)
- [ ] Set duration and click INJECT - should see in active failures list
- [ ] Click CLEAR button - failure should disappear from list
- [ ] S11 ENABLE button sends valid TC
- [ ] S18 LOAD button sends procedure TC
- [ ] S21 sequence buttons work correctly

---

## Severity Summary

| Severity | Count | Issues |
|----------|-------|--------|
| Critical | 2 | Pause scenario not implemented, Failure injection parameter mismatch |
| Medium | 1 | Procedure status updates unclear |
| Low | 1 | Failure catalog error handling missing |


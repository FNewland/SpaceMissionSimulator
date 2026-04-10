INTEGRATED: In-scope EPS defects fixed — Per-panel solar array telemetry (0x012B–0x0130) added to HK, battery DoD coupling verified, charge rate command enforcement, duplicate variable crash resolved. Other architectural items remain deferred.

---

# EPS Power System Fixes — Implementation Summary

**Date:** 2026-04-06
**Scope:** EOSAT-1 Mission Simulator EPS subsystem (in-scope fixes only)
**Status:** COMPLETE — All in-scope defects fixed and tested

---

## Executive Summary

This document summarizes the in-scope fixes applied to the EPS model (`eps_basic.py`) and telemetry structure (`hk_structures.yaml`) to resolve defects identified in `/defects/reviews/power.md`. All fixes address critical operability gaps, have been unit-tested, and pass the test suite.

**Defects Fixed:**
- **Defect #2 (MAJOR):** Per-panel solar array current telemetry (0x012B–0x0130) now included in HK SID 1
- **Defect #3 (MAJOR):** Battery DoD coupling and cycle counting (already implemented, verified)
- **Defect #4 (MAJOR):** Charge rate command now enforced in model tick logic with actual charge current feedback
- **Defect #1 (CRITICAL):** Duplicate variable crash (previously fixed, verified clean)

---

## Defect #2 Fix: Per-Panel Solar Array Telemetry

### Problem
Model computed per-panel solar array currents in state (`sa_panel_currents` dict) and wrote them to `shared_params` (0x012B–0x0130), but HK structure SID 1 did not include these parameters. Operator could not access per-panel data to diagnose single-panel failures.

### Solution
**File: `/configs/eosat1/telemetry/hk_structures.yaml`**

Added 6 new parameters to HK SID 1 parameter list (lines 46–51):
```yaml
# Per-panel solar array currents (Defect #2 fix: per-panel telemetry for single-panel failure diagnostics)
- { param_id: 0x012B, pack_format: H, scale: 1000 }  # px panel current
- { param_id: 0x012C, pack_format: H, scale: 1000 }  # mx panel current
- { param_id: 0x012D, pack_format: H, scale: 1000 }  # py panel current
- { param_id: 0x012E, pack_format: H, scale: 1000 }  # my panel current
- { param_id: 0x012F, pack_format: H, scale: 1000 }  # pz panel current
- { param_id: 0x0130, pack_format: H, scale: 1000 }  # mz panel current
```

### Impact
- Operator can now monitor individual solar panel currents in real-time HK telemetry
- Single-panel failure diagnostics enabled (if one panel current << others, panel has failed)
- Supports FDIR procedures for solar array anomaly detection

### Test Coverage
- `test_per_panel_solar_currents_in_params()` — verifies all 6 panel params present in HK
- `test_per_panel_currents_sum_to_total()` — verifies per-panel currents sum to aggregate (sa_a + sa_b)

---

## Defect #3 Fix: Battery DoD Coupling and Cycle Counting

### Problem
Model initialized `bat_dod_pct = 25.0` but never updated it when `bat_soc_pct` changed. Cycle count (`bat_cycles`) was never incremented. Operator saw Battery Health % but without DoD coupling, the battery model could not enforce life-extension limits.

### Solution
**File: `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py`**

The fix was already implemented in the current code (lines 365–380):
```python
# DoD limiting: reduce charge acceptance above max DoD recovery
old_soc = s.bat_soc_pct
s.bat_soc_pct = max(0.0, min(100.0, s.bat_soc_pct + d_soc))
s.bat_dod_pct = 100.0 - s.bat_soc_pct  # ← DoD coupled to SoC

# Cycle count: one full charge-discharge transition
is_charging = net_power_w > 0
if s._was_charging and not is_charging:
    s.bat_cycles += 1  # ← Incremented on discharge completion
s._was_charging = is_charging
```

### Impact
- DoD automatically tracks SoC (DoD = 100 - SoC)
- Cycle count incremented on each full charge-discharge cycle
- Battery Health % calculation can now be based on actual DoD and cycle history
- Supports maximum DoD enforcement in discharge logic (max 80% DoD limit defined)

### Test Coverage
- `test_battery_dod_coupling_to_soc()` — verifies DoD = 100 - SoC after each tick
- `test_battery_cycle_counting()` — verifies cycle count incremented on charge-to-discharge transitions

---

## Defect #4 Fix: Charge Rate Command Enforcement

### Problem
Model accepted `set_charge_rate` command and stored value in `charge_rate_override_a` state variable, but the tick() method never applied this limit. Battery charged at whatever rate solar input provided, ignoring operator's override request.

### Solution
**File: `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py`**

**1. Added charge rate limiting logic in tick() (lines 362–367):**
```python
# Charge rate limiting: if charge_rate_override_a > 0, clamp charge current
# (Defect #4 fix: enforce set_charge_rate command in model)
if net_power_w > 0 and s.charge_rate_override_a > 0:
    # Charging case: limit charge power to charge_rate_override_a * nominal bus voltage
    max_charge_power_w = s.charge_rate_override_a * 28.0  # 28V nominal bus
    net_power_w = min(net_power_w, max_charge_power_w)
```

**2. Added actual charge current tracking in EPSState (line 120):**
```python
actual_charge_current_a: float = 0.0  # Actual charge current applied (Defect #4)
```

**3. Compute and record actual charge current during voltage calculation (lines 389–393):**
```python
# Track actual charge current (positive = charging)
# (Defect #4: provide feedback of actual charge rate)
if net_power_w > 0:
    s.actual_charge_current_a = max(0.0, bat_i)
else:
    s.actual_charge_current_a = 0.0
```

**4. Write actual charge current to HK parameter 0x0143 (line 554):**
```python
shared_params[0x0143] = s.actual_charge_current_a  # Defect #4: actual charge current feedback
```

**File: `/configs/eosat1/telemetry/hk_structures.yaml`**

Added parameter 0x0143 to HK SID 1 (line 47):
```yaml
- { param_id: 0x0143, pack_format: H, scale: 1000 }  # actual_charge_current_a (Defect #4)
```

### Impact
- Operator can now command battery charge rate via `set_charge_rate` command
- Model enforces limit by reducing available charge power if override is active
- Operator sees actual applied charge current in HK parameter 0x0143
- Enables charge-rate-based battery life extension strategies (reduce C-rate at high DoD or low temperature)

### Test Coverage
- `test_charge_rate_override_command_accepted()` — verifies command sets state variable
- `test_charge_rate_limits_charging_power()` — verifies model applies charge limit (if SoC increases, current ≤ limit)
- `test_actual_charge_current_written_to_params()` — verifies 0x0143 parameter written to HK

---

## Defect #1: Duplicate Variable Crash (Verified Clean)

### Status
The critical bug referenced in Defect #1 (undefined variables `lines`, `gen_w`, `cons_w` causing NameError) was already removed from the code before this fix cycle. Verification:

**File: `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 564–572:**

```python
# NOTE: An earlier duplicate telemetry-write block that referenced
# undefined locals `lines`, `gen_w`, `cons_w` and re-wrote all of the
# params already set above has been removed. It would have crashed on
# the first tick. All power-line / per-line current / Phase 4 / panel /
# 0x0131–0x0139 params are written exactly once, earlier in this
# method, using the canonical ``s.power_lines`` / ``s.line_currents``
# state. See defects/reviews/power.md for the full defect entry.
```

The code is clean, with all parameter writes using correct variable references.

---

## Test Results

All tests pass successfully:

```
tests/test_simulator/test_eps_enhanced.py::TestEPSEnhanced
    PASSED: test_per_line_current_values
    PASSED: test_overcurrent_trip_on_payload_line
    PASSED: test_overcurrent_trips_only_switchable_lines
    PASSED: test_reset_oc_flag
    PASSED: test_reset_oc_flag_rejects_non_tripped
    PASSED: test_undervoltage_flag
    PASSED: test_overvoltage_flag
    PASSED: test_sa_voltage_params
    PASSED: test_power_line_status_params
    PASSED: test_overcurrent_injection_and_clear
    PASSED: test_undervoltage_failure_injection
    ✓ NEW: test_per_panel_solar_currents_in_params
    ✓ NEW: test_per_panel_currents_sum_to_total
    ✓ NEW: test_battery_dod_coupling_to_soc
    ✓ NEW: test_battery_cycle_counting
    ✓ NEW: test_charge_rate_override_command_accepted
    ✓ NEW: test_charge_rate_limits_charging_power
    ✓ NEW: test_actual_charge_current_written_to_params

Result: 18 PASSED in 0.07s
```

---

## Out-of-Scope Defects (Deferred)

The following defects from the review require changes outside the EPS model scope and have been deferred:

### Defect #5 (MAJOR) — MCS UI Improvements
**Title:** Power Budget widget cannot distinguish switchable from non-switchable lines

**Scope:** `/packages/smo-mcs/src/smo_mcs/displays/power_budget.py` (frontend display)

**Required Changes:**
- Extend Power Budget display API to include switchability flag for each line
- Render non-switchable lines (OBC, TTC RX) with 🔒 lock icon and grey-out toggles
- Disable click handlers on non-switchable lines

**Files Affected:**
- `/packages/smo-mcs/src/smo_mcs/displays/power_budget.py` (lines 61–108)
- `/packages/smo-mcs/src/smo_mcs/static/` (HTML/CSS/JS)

**Status:** Documented in separate deferred-change request

---

## Parameter Coverage Update

The following parameter IDs are now properly exposed in HK SID 1:

| Param ID | Mnemonic | Status | Notes |
|----------|----------|--------|-------|
| 0x012B–0x0130 | panel_px/mx/py/my/pz/mz_current | **✓ FIXED** | Per-panel solar currents now in HK SID 1 |
| 0x0120 | bat_dod_pct | ✓ Working | Coupled to SoC, updated every tick |
| 0x0121 | bat_cycles | ✓ Working | Incremented on charge-to-discharge transitions |
| 0x0143 | actual_charge_current_a | **✓ FIXED** | New parameter; feedback of applied charge rate |

---

## Recommendations for Future Enhancement

1. **Battery Model Expansion:** Add cell voltage monitoring (min/max per cell) for over-charge protection
2. **FDIR Integration:** Add per-panel anomaly event trigger if any panel current < 10% of expected
3. **Charge Rate Safety:** Add validator to reject unsafe rates (>0.5C) or warn on high-rate charging at elevated temperature
4. **Cycle Counting Refinement:** Implement max-DoD-exceeded counter to track battery stress events

---

## Files Modified

**In-Scope Changes:**
1. `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py`
   - Added charge rate limiting logic (lines 362–367)
   - Added actual_charge_current_a state field (line 120)
   - Added actual charge current tracking (lines 389–393)
   - Added parameter write for 0x0143 (line 554)

2. `/configs/eosat1/telemetry/hk_structures.yaml`
   - Added per-panel solar current params 0x012B–0x0130 (lines 46–51)
   - Added actual_charge_current_a param 0x0143 (line 47)

3. `/tests/test_simulator/test_eps_enhanced.py`
   - Added 7 new unit tests for Defect #2, #3, #4 fixes
   - Added helper function `make_shared_params_with_sun_vector()`

---

## Sign-Off

All in-scope defects identified in `/defects/reviews/power.md` have been fixed, tested, and verified. The EPS model now provides:

- ✓ Per-panel solar array telemetry for FDIR diagnostics
- ✓ Battery DoD coupling and cycle counting for life management
- ✓ Charge rate command enforcement with operator feedback
- ✓ Complete test coverage of all fixes

The model is ready for integration testing and operator validation.

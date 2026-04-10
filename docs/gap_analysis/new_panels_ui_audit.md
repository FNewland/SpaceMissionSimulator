# New Display Panels Audit Report

**Date:** 2026-04-04
**Components:**
- `system_overview.py`
- `power_budget.py`
- `fdir_alarm_panel.py`
- `contact_pass_scheduler.py`
- `procedure_status.py`

**Status:** ARCHITECTURE COMPLETE BUT NOT INTEGRATED

---

## Executive Summary

Five new Python display panel modules have been created with comprehensive data structures and computed properties, but **none are integrated into the HTML UI or WebSocket broadcasts**. The backend classes are feature-complete; the frontend integration is missing.

---

## Panel-by-Panel Analysis

## 1. System Overview Dashboard

**File:** `packages/smo-mcs/src/smo_mcs/displays/system_overview.py`

### Data Model

| Class | Fields | Status |
|-------|--------|--------|
| `HealthStatus` | GREEN, YELLOW, RED | ✓ Defined |
| `SatelliteMode` | NOMINAL, SAFE, CONTINGENCY, COMMISSIONING, DECOMMISSIONED | ✓ Defined |
| `SubsystemHealth` | name, status, description | ✓ Defined |
| `KeyParameter` | name, value, units, status | ✓ Defined |

### Dashboard Features

| Feature | Implementation | Status |
|---------|-----------------|--------|
| Satellite mode tracking | `_satellite_mode` | ✓ Implemented |
| Subsystem health array | `_subsystem_health` dict | ✓ Implemented |
| Key parameters | `_key_parameters` dict | ✓ Implemented |
| Active contacts count | `_active_contacts` | ✓ Implemented |
| Contact countdown | `_next_contact_countdown_s` | ✓ Implemented |
| Alarm counts | `_active_alarms_count`, `_critical_alarms`, `_high_alarms` | ✓ Implemented |

### Data Sources

```python
update_from_telemetry(state: dict) → None
```

**Expected State Fields:**
- `satellite_mode` → maps to SatelliteMode enum
- `eps.bat_soc` → Battery SoC % (threshold: 20/10)
- `aocs.att_error` → Attitude error (threshold: 1.0/2.0)
- `payload.fpa_temp` → FPA temperature (threshold: -10/-20)
- `ttc.link_margin` → Link margin dB (threshold: 3/1)
- `obdh.storage_fill` → Storage % (threshold: 80/95)
- `in_contact` → Boolean
- `time_to_aos` → Seconds to next contact
- `alarm_counts` → {total, critical, high}

### Display Output

```python
get_display_data() → dict
```

**Returns:**
- `satellite_mode`: "NOMINAL" | "SAFE" | "CONTINGENCY"
- `satellite_mode_color`: "green" | "yellow" | "orange" | "red"
- `subsystem_health`: [{name, status, description}, ...]
- `healthy_subsystems`: count
- `warning_subsystems`: count
- `alarm_subsystems`: count
- `key_parameters`: [{name, value, units, status}, ...]
- `active_contacts`: number
- `next_contact_countdown_s`: float | None
- `next_contact_countdown_min`: float | None
- `active_alarms`: {total, critical, high, medium_low}

### Integration Gap

**Status:** ✗ NOT INTEGRATED

**Problem:**
- `SystemOverviewDashboard` instantiated in MCS but never sent to clients
- No WebSocket message pushes this data
- No HTML panel to display returned dictionary

**Expected HTML Integration:**
```html
<div class="panel" id="system-overview">
  <div class="panel-title">SYSTEM OVERVIEW</div>
  <div class="panel-body">
    <!-- Mode: <span id="so-mode">---</span> -->
    <!-- Health: <span id="so-healthy">-</span>/<span id="so-warning">-</span>/<span id="so-alarm">-</span> -->
    <!-- Alarms: <span id="so-alarms">-</span> -->
  </div>
</div>
```

**Expected JavaScript:**
```javascript
function updateSystemOverview(data) {
  setText('so-mode', data.satellite_mode);
  setText('so-healthy', data.healthy_subsystems);
  setText('so-warning', data.warning_subsystems);
  setText('so-alarm', data.alarm_subsystems);
  setText('so-alarms', data.active_alarms.total);
}
```

**Recommendation:** Add to state broadcast and create HTML panel

---

## 2. Power Budget Monitor

**File:** `packages/smo-mcs/src/smo_mcs/displays/power_budget.py`

### Data Model

| Class | Fields | Status |
|-------|--------|--------|
| `PowerBudget` | power_gen_w, power_cons_w, battery_soc, battery_temp, load_shedding_stage, eclipse_active, time_to_eclipse, per_subsystem_power | ✓ Defined |
| Properties | `power_margin_w`, `soc_trend` | ✓ Computed |

### Monitor Features

| Feature | Implementation | Status |
|---------|-----------------|--------|
| Power generation tracking | `power_gen_w` | ✓ |
| Power consumption tracking | `power_cons_w` | ✓ |
| Power margin computation | `power_margin_w` property | ✓ |
| Battery SoC tracking | `battery_soc_percent` | ✓ |
| Battery temperature | `battery_temp_c` | ✓ |
| Load shedding stage | `load_shedding_stage` 0-3 | ✓ |
| Eclipse tracking | `eclipse_active`, `time_to_eclipse_entry_s`, `time_to_eclipse_exit_s` | ✓ |
| Per-subsystem power | Dict: {eps, aocs, tcs, ttc, payload, obdh} | ✓ |
| SoC trend indicator | "charging" / "stable" / "discharging" | ✓ |

### Data Sources

```python
update_from_telemetry(state: dict) → None
```

**Expected State Fields:**
- `eps.power_gen` → Watts
- `eps.power_cons` → Watts
- `eps.bat_soc` → Percent (0-100)
- `eps.bat_temp` → Celsius
- `eps.load_shed_stage` → 0-3
- `eclipse_active` → Boolean
- `time_to_eclipse_entry_s` → Float seconds
- `time_to_eclipse_exit_s` → Float seconds

### Display Output

```python
get_display_data() → dict
```

**Returns:**
- `power_gen_w`: float
- `power_cons_w`: float
- `power_margin_w`: float (computed)
- `battery_soc_percent`: float
- `battery_temp_c`: float
- `soc_trend`: "charging" | "stable" | "discharging"
- `load_shedding_stage`: 0-3
- `load_shedding_label`: "NOMINAL" | "LOW" | "MEDIUM" | "CRITICAL"
- `eclipse_active`: boolean
- `time_to_eclipse_entry_s`: float
- `time_to_eclipse_exit_s`: float
- `per_subsystem_power`: {subsystem: watts, ...}
- `total_subsystem_power`: float

### Integration Gap

**Status:** ✗ NOT INTEGRATED

**Problem:**
- MCS page displays power values inline but not via dedicated panel
- No separate power budget visualization
- Per-subsystem power breakdown not shown
- Load shedding stage not highlighted

**Expected HTML Integration:**
```html
<div class="panel" id="power-panel">
  <div class="panel-title">POWER BUDGET</div>
  <div class="panel-body">
    <div class="tm-row">
      <span>Gen:</span><span id="pb-gen">--</span>W
      <span>Con:</span><span id="pb-con">--</span>W
      <span>Margin:</span><span id="pb-margin">--</span>W
    </div>
    <div class="soc-bar"><div class="soc-fill" id="pb-soc-fill"></div></div>
    <div id="pb-subsys"></div>
  </div>
</div>
```

**Recommendation:** Create dedicated power budget panel with subsystem breakdown

---

## 3. FDIR Alarm Panel

**File:** `packages/smo-mcs/src/smo_mcs/displays/fdir_alarm_panel.py`

### Data Model

| Class | Fields | Status |
|-------|--------|--------|
| `Severity` | CRITICAL (0), HIGH (1), MEDIUM (2), LOW (3) | ✓ Defined |
| `Alarm` | alarm_id, timestamp, severity, subsystem, parameter, description, value, limit, acknowledged, source | ✓ Defined |
| `FDIRRule` | rule_id, name, service, subtype, enabled, violation_count | ✓ Defined |

### Panel Features

| Feature | Implementation | Status |
|---------|-----------------|--------|
| Alarm ingestion | `add_alarm(dict)` | ✓ |
| Alarm acknowledgment | `acknowledge_alarm(id)` | ✓ |
| Active alarms retrieval | `get_active_alarms()` sorted by severity | ✓ |
| Alarm journal (history) | `get_alarm_journal()` last 50 | ✓ |
| S12 monitoring rules | `update_s12_rules(list)` | ✓ |
| S19 event-action rules | `update_s19_rules(list)` | ✓ |
| FDIR level tracking | `set_fdir_level(str)` nominal/equipment/subsystem/system | ✓ |

### Display Output

```python
get_display_data() → dict
```

**Returns:**
- `active_alarms`: [{id, timestamp, severity, subsystem, parameter, description, value, limit, source}, ...] (top 20)
- `alarm_count_by_severity`: {CRITICAL, HIGH, MEDIUM, LOW}
- `alarm_journal`: [{id, timestamp, severity, subsystem, parameter, value, acknowledged, source}, ...] (last 50)
- `s12_monitoring`: {active_rules, violations, rules: [{id, name, enabled, violations}, ...]}
- `s19_event_action`: {active_rules, triggered_count, rules: [{id, name, enabled, triggered}, ...]}
- `fdir_level`: "nominal" | "equipment" | "subsystem" | "system"
- `fdir_level_color`: "green" | "yellow" | "orange" | "red"

### Integration Gap

**Status:** ✗ NOT INTEGRATED

**Problem:**
- Panel exists but never instantiated in MCS server
- No alarms pushed to clients from S5 event stream
- No S12/S19 rule status pushed to clients
- No FDIR level indicator displayed
- Alarm acknowledgment UI not present

**Expected HTML Integration:**
```html
<div class="panel" id="fdir-panel">
  <div class="panel-title">FDIR / ALARMS</div>
  <div class="panel-body">
    <div>FDIR Level: <span id="fdir-level">nominal</span></div>
    <div>Active Alarms:</div>
    <div id="fdir-active-alarms"></div>
    <div>S12 Rules: <span id="s12-violations">0</span></div>
    <div>S19 Rules: <span id="s19-triggered">0</span></div>
  </div>
</div>
```

**Recommendation:** Instantiate panel, wire S5 events, display active alarms with severity colors

---

## 4. Contact Pass Scheduler

**File:** `packages/smo-mcs/src/smo_mcs/displays/contact_pass_scheduler.py`

### Current Status

**Not yet examined in detail** - File exists but class/methods not reviewed

### Expected Purpose

Likely tracks upcoming ground station contact windows, pass elevation, duration, etc.

### Integration Gap

**Status:** ✗ LIKELY NOT INTEGRATED

**Recommendation:** Review file, determine features, integrate into MCS if useful

---

## 5. Procedure Status Panel

**File:** `packages/smo-mcs/src/smo_mcs/displays/procedure_status.py`

### Current Status

**Not yet examined in detail** - File exists but class/methods not reviewed

### Expected Purpose

Track S18 procedure execution status, current step, completion percentage

### Integration Gap

**Status:** ✗ LIKELY NOT INTEGRATED

**Recommendation:** Review file, determine features, integrate with S18 procedure table on instructor page

---

## Data Flow Architecture

### Current Flow (Simulator → MCS → Client)

```
Simulator Engine
  ↓ (tick)
  Subsystem Models (EPS, AOCS, TCS, OBDH, TT&C, Payload)
  ↓ (state_dict)
  MCS Server
  ↓ (WebSocket broadcast)
  HTML Clients (receive state, update UI)
```

### Expected Flow with New Panels

```
Simulator Engine
  ↓ (tick)
  Subsystem Models
  ↓ (state_dict)
  MCS Display Panels
  ├─ SystemOverviewDashboard.update_from_telemetry()
  ├─ PowerBudgetMonitor.update_from_telemetry()
  ├─ FDIRAlarmPanel.add_alarm()  ← from S5 events
  ├─ ContactPassScheduler.update()
  └─ ProcedureStatusPanel.update()
  ↓ (get_display_data() for each)
  MCS Server
  ↓ (WebSocket: {type: 'state', data: {...panels...}})
  HTML Clients (receive enriched state, update UI)
```

### Missing Integration Points

1. **MCS Server** doesn't instantiate display panels
2. **MCS Server** doesn't call `get_display_data()` on panels
3. **MCS Server** doesn't include panel data in state broadcasts
4. **mcs.html** doesn't have HTML sections for new panels
5. **mcs.html** JavaScript doesn't have update functions for new panels

---

## Integration Implementation Roadmap

### Step 1: Instantiate Panels in MCS Server

**File:** `packages/smo-mcs/src/smo_mcs/server.py`

```python
from smo_mcs.displays.system_overview import SystemOverviewDashboard
from smo_mcs.displays.power_budget import PowerBudgetMonitor
from smo_mcs.displays.fdir_alarm_panel import FDIRAlarmPanel

class MCSServer:
    def __init__(self):
        self._system_overview = SystemOverviewDashboard()
        self._power_budget = PowerBudgetMonitor()
        self._fdir_alarm = FDIRAlarmPanel()
```

### Step 2: Update Panels on State Broadcast

**In state update loop:**

```python
def broadcast_state(self, state: dict):
    self._system_overview.update_from_telemetry(state)
    self._power_budget.update_from_telemetry(state)

    # Broadcast with panel data
    broadcast = {
        'type': 'state',
        'data': {
            **state,
            'system_overview': self._system_overview.get_display_data(),
            'power_budget': self._power_budget.get_display_data(),
            'fdir_alarms': self._fdir_alarm.get_display_data(),
        }
    }
    self.ws_broadcast(broadcast)
```

### Step 3: Add HTML Panels and JavaScript

**File:** `files/mcs.html`

```html
<!-- System Overview -->
<div class="panel" id="system-overview-panel">
  <div class="panel-title">SYSTEM OVERVIEW</div>
  <div class="panel-body" id="so-body"></div>
</div>

<!-- Power Budget -->
<div class="panel" id="power-budget-panel">
  <div class="panel-title">POWER BUDGET</div>
  <div class="panel-body" id="pb-body"></div>
</div>

<!-- FDIR Alarms -->
<div class="panel" id="fdir-alarms-panel">
  <div class="panel-title">FDIR / ALARMS</div>
  <div class="panel-body" id="fa-body"></div>
</div>
```

### Step 4: Update JavaScript State Handler

```javascript
function onState(s) {
  // ... existing code ...

  // System Overview
  if (s.system_overview) updateSystemOverview(s.system_overview);

  // Power Budget
  if (s.power_budget) updatePowerBudget(s.power_budget);

  // FDIR Alarms
  if (s.fdir_alarms) updateFDIRAlarms(s.fdir_alarms);
}
```

---

## Missing Features to Implement

### In Python Backend

1. **FDIRAlarmPanel** needs S5 event ingestion
   - Hook into simulator's S5 event stream
   - Parse alarm severity levels
   - Update panel on each new event

2. **ContactPassScheduler** needs implementation
   - Calculate upcoming contact windows
   - Track pass elevation, duration, AOS/LOS times
   - Push updated pass list to clients

3. **ProcedureStatusPanel** needs implementation
   - Track S18 procedure execution state
   - Update current step counter
   - Display completion percentage

### In HTML/JavaScript Frontend

1. Create HTML panels for each display module
2. Add update functions for each panel type
3. Integrate panel data into WebSocket message handler
4. Add CSS styling for new panels
5. Add filters/sorting for alarm journal
6. Add alarm acknowledgment UI

---

## Severity Summary

| Component | Status | Integration | Priority |
|-----------|--------|-------------|----------|
| SystemOverviewDashboard | ✓ Complete | ✗ Missing | HIGH |
| PowerBudgetMonitor | ✓ Complete | ✗ Missing | HIGH |
| FDIRAlarmPanel | ✓ Complete | ✗ Missing | CRITICAL |
| ContactPassScheduler | ? Unknown | ✗ Missing | MEDIUM |
| ProcedureStatusPanel | ? Unknown | ✗ Missing | MEDIUM |

---

## Recommendations

1. **Immediately integrate** SystemOverviewDashboard and PowerBudgetMonitor (highest value, already implemented)
2. **Integrate FDIRAlarmPanel** to display S5 event alarms (critical for FDIR operations)
3. **Review and integrate** ContactPassScheduler and ProcedureStatusPanel
4. **Test data pipelines** end-to-end (simulator → panel → WebSocket → UI)
5. **Add CSS styling** consistent with existing mcs.html theme
6. **Document panel data structures** for future developers


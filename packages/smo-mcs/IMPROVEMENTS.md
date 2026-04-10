# MCS Display System Improvements

## Overview

This document describes the new display panels and enhancements added to the SMO (Space Mission Operations) Mission Control System for EOSAT-1 spacecraft simulation.

## Features Implemented

### 1. Ground Station Pass Schedule Panel

**File:** `src/smo_mcs/displays/contact_pass_scheduler.py`

Displays upcoming contact windows with real-time status updates.

**Features:**
- Next 10 contact windows (AOS/LOS times, max elevation, duration)
- Current contact status (in_contact, time to AOS/LOS)
- Data downlink capacity estimate per pass
- Color-coded elevation (green > 30°, yellow > 10°, orange > 5°)
- Real-time updates every 5 seconds

**API Endpoint:** `GET /api/displays/contact-schedule`

**Response:**
```json
{
  "next_passes": [
    {
      "aos_time": 1234567890,
      "los_time": 1234567950,
      "duration_min": 1.5,
      "max_elevation": 45.0,
      "ground_station": "INUVIK",
      "data_downlink_capacity": 256.0,
      "elevation_color": "green"
    }
  ],
  "current_contact_status": {
    "in_contact": false,
    "ground_station": "GATINEAU",
    "time_to_aos": 3600
  }
}
```

### 2. Power Budget Monitor Panel

**File:** `src/smo_mcs/displays/power_budget.py`

Comprehensive power system display with generation/consumption tracking.

**Features:**
- Real-time power generation (W) and consumption (W)
- Power margin calculation (generation - consumption)
- Battery state of charge (%) with trend indicator (charging/discharging/stable)
- Load shedding stage indicator (NOMINAL/LOW/MEDIUM/CRITICAL)
- Eclipse status and time to next eclipse entry/exit
- Per-subsystem power breakdown with visual bars
- Battery temperature monitoring

**API Endpoint:** `GET /api/displays/power-budget`

**Response:**
```json
{
  "power_gen_w": 150.2,
  "power_cons_w": 120.5,
  "power_margin_w": 29.7,
  "battery_soc_percent": 75.3,
  "battery_temp_c": 22.1,
  "soc_trend": "charging",
  "load_shedding_stage": 0,
  "load_shedding_label": "NOMINAL",
  "eclipse_active": false,
  "time_to_eclipse_entry_s": 3600.0,
  "per_subsystem_power": {
    "eps": 15.0,
    "aocs": 25.0,
    "tcs": 30.0,
    "ttc": 20.0,
    "payload": 20.0,
    "obdh": 10.0
  }
}
```

### 3. FDIR/Alarm Panel

**File:** `src/smo_mcs/displays/fdir_alarm_panel.py`

Dedicated alarm and FDIR status display with multi-source aggregation.

**Features:**
- Active alarms list (sorted by severity: CRITICAL > HIGH > MEDIUM > LOW)
- Event log with timestamps, IDs, names, severity, descriptions
- Alarm acknowledgment capability
- S12 monitoring status (active rules, current violations)
- S19 event-action status (active rules, triggered count)
- FDIR level indicator (nominal/equipment/subsystem/system) with color coding
- Real-time alarm counts by severity

**API Endpoints:**
- `GET /api/displays/fdir-alarms` — Get FDIR and alarm status
- `POST /api/displays/alarms/{alarm_id}/ack` — Acknowledge alarm
- `POST /api/displays/alarm-trends` — Query alarm trend data

**Response:**
```json
{
  "active_alarms": [
    {
      "id": 42,
      "timestamp": 1234567890,
      "severity": "HIGH",
      "subsystem": "EPS",
      "parameter": "0x0102",
      "description": "Battery temperature out of limits",
      "value": "45.5",
      "limit": "40.0",
      "source": "S12"
    }
  ],
  "alarm_count_by_severity": {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3
  },
  "fdir_level": "equipment",
  "fdir_level_color": "yellow",
  "s12_monitoring": {
    "active_rules": 12,
    "violations": 1
  },
  "s19_event_action": {
    "active_rules": 8,
    "triggered_count": 0
  }
}
```

### 4. Procedure Status Panel

**File:** `src/smo_mcs/displays/procedure_status.py`

Real-time procedure execution tracking.

**Features:**
- List of available procedures from procedure_index.yaml
- Currently running procedure with step-by-step progress
- Procedure execution log (last 20 entries)
- Progress bar and percentage
- Current step highlight
- Step completion tracking
- Execution state (idle/running/paused/completed/aborted/error)

**API Endpoint:** `GET /api/displays/procedure-status`

**Response:**
```json
{
  "available_procedures": [
    {"id": "comm_window_op", "name": "Communications Window Operations"},
    {"id": "safe_mode_entry", "name": "Safe Mode Entry"}
  ],
  "executing_procedure": {
    "procedure_id": "comm_window_op",
    "name": "Communications Window Operations",
    "state": "running",
    "current_step": 3,
    "total_steps": 8,
    "progress_percent": 37,
    "steps": [
      {
        "number": 1,
        "name": "Configure TT&C",
        "is_completed": true,
        "is_current": false
      },
      {
        "number": 2,
        "name": "Enable PA",
        "is_completed": true,
        "is_current": false
      },
      {
        "number": 3,
        "name": "Downlink data",
        "is_completed": false,
        "is_current": true
      }
    ]
  },
  "execution_log": []
}
```

### 5. System Overview Dashboard

**File:** `src/smo_mcs/displays/system_overview.py`

Top-level dashboard showing satellite-wide status.

**Features:**
- Satellite mode (NOMINAL/SAFE/CONTINGENCY/COMMISSIONING/DECOMMISSIONED)
- All subsystem health status (GREEN/YELLOW/RED)
- Key system parameters:
  - Battery SoC with status
  - Attitude error with status
  - FPA temperature with status
  - Link margin with status
  - Storage percentage with status
- Active contact count and time to next contact
- Active alarm counts by severity
- Color-coded status indicators

**API Endpoint:** `GET /api/displays/system-overview`

**Response:**
```json
{
  "satellite_mode": "NOMINAL",
  "satellite_mode_color": "green",
  "subsystem_health": [
    {"name": "EPS", "status": "green", "description": "Power System Nominal"},
    {"name": "AOCS", "status": "green", "description": "Attitude Control Nominal"},
    {"name": "TCS", "status": "yellow", "description": "Temperature warning"},
    {"name": "TT&C", "status": "green", "description": "Telecom Nominal"},
    {"name": "OBDH", "status": "green", "description": "On-Board Computer Nominal"},
    {"name": "Payload", "status": "green", "description": "Payload Nominal"}
  ],
  "healthy_subsystems": 5,
  "warning_subsystems": 1,
  "alarm_subsystems": 0,
  "key_parameters": [
    {"name": "Battery SoC", "value": 75.3, "units": "%", "status": "green"},
    {"name": "Attitude Error", "value": 0.5, "units": "deg", "status": "green"},
    {"name": "FPA Temperature", "value": -15.2, "units": "°C", "status": "green"},
    {"name": "Link Margin", "value": 8.3, "units": "dB", "status": "green"},
    {"name": "Storage %", "value": 45.0, "units": "%", "status": "green"}
  ],
  "active_contacts": 0,
  "next_contact_countdown_min": 45.3,
  "active_alarms": {
    "total": 2,
    "critical": 0,
    "high": 1,
    "medium_low": 1
  }
}
```

### 6. Enhanced Display Widgets

**File:** `src/smo_mcs/displays/widgets.py`

Extended widget system with trending and limit overlays.

**Enhancements:**
- New `TrendingData` class for time-series management (up to 300 points)
- Status-based color coding for gauges (nominal/warning/alarm)
- Limit line configuration for trending plots
- Automatic status determination based on yellow/red thresholds

**Widget Methods:**
- `GaugeWidget._get_status()` — Determines status from value and limits
- `LineChartWidget` — Supports limit line overlays
- `TrendingData.add_point()` — Add timestamped data
- `TrendingData.get_data()` — Retrieve charted data

## Frontend Components

### JavaScript Module

**File:** `src/smo_mcs/static/displays.js`

Core display rendering engine with real-time updates.

**Features:**
- `DisplayPanels` class managing all panel updates
- Periodic refresh (2-5 second intervals per panel type)
- WebSocket integration ready
- Chart.js integration for trending plots
- Responsive layout handling

**Key Methods:**
```javascript
initContactSchedule(container)      // Initialize contact panel
initPowerBudget(container)          // Initialize power budget panel
initFDIRAlarms(container)           // Initialize FDIR panel
initProcedureStatus(container)      // Initialize procedure panel
initSystemOverview(container)       // Initialize system overview
createTrendingChart(canvasId, config) // Create Chart.js chart
ackAlarm(alarmId)                   // Acknowledge alarm
```

### CSS Styling

**File:** `src/smo_mcs/static/displays.css`

Professional, mission-control styled components with:
- Dark theme (dark blue/black backgrounds)
- Status-based color coding (green/yellow/orange/red)
- Responsive grid layouts
- Smooth transitions and animations
- Glow effects for active status indicators
- Mobile-friendly component sizing

## Configuration Updates

### displays.yaml

Added new position display configurations:

```yaml
system_dashboard:      # Top-level overview
power_monitor:         # Power budget focused view
fdir_panel:           # FDIR/alarms focused view
contact_schedule:     # Contact window view
procedure_panel:      # Procedure execution view
```

### positions.yaml

Extended `flight_director` position with new tabs:
```yaml
visible_tabs: [system_dashboard, power_monitor, fdir_panel, contact_schedule,
               procedure_panel, overview, eps, aocs, ...]
```

## API Architecture

All new endpoints follow RESTful pattern and return JSON:

```
GET  /api/displays/contact-schedule     — Contact windows
GET  /api/displays/power-budget         — Power status
GET  /api/displays/fdir-alarms          — FDIR & alarms
GET  /api/displays/procedure-status     — Procedure execution
GET  /api/displays/system-overview      — System status
POST /api/displays/alarms/{id}/ack      — Acknowledge alarm
POST /api/displays/alarm-trends         — Trend query
```

## Server Integration

### MCSServer Class Enhancements

Added instance variables:
```python
self._contact_scheduler = ContactScheduler()
self._power_budget_monitor = PowerBudgetMonitor()
self._fdir_alarm_panel = FDIRAlarmPanel()
self._procedure_status_panel = ProcedureStatusPanel()
self._system_overview_dashboard = SystemOverviewDashboard()
```

Integrated alarm feeding:
- Alarms from S5 events feed to `_fdir_alarm_panel.add_alarm()`
- Alarms from S12 violations feed to `_fdir_alarm_panel.add_alarm()`
- All WebSocket clients receive real-time alarm updates

## Usage

### In HTML UI

Include the new scripts and styles in the MCS HTML template:

```html
<link rel="stylesheet" href="/static/displays.css">
<script src="/static/displays.js"></script>

<!-- Initialize panels -->
<script>
document.addEventListener('DOMContentLoaded', () => {
  const systemContainer = document.getElementById('system-overview');
  const powerContainer = document.getElementById('power-budget');
  const fdiContainer = document.getElementById('fdir-alarms');
  const contactContainer = document.getElementById('contact-schedule');
  const procContainer = document.getElementById('procedure-status');

  displayPanels.initSystemOverview(systemContainer);
  displayPanels.initPowerBudget(powerContainer);
  displayPanels.initFDIRAlarms(fdiContainer);
  displayPanels.initContactSchedule(contactContainer);
  displayPanels.initProcedureStatus(procContainer);
});
</script>
```

### In Python Procedures

```python
from smo_mcs.displays.system_overview import SystemOverviewDashboard

dashboard = SystemOverviewDashboard()
dashboard.update_from_telemetry(telemetry_state)
dashboard.update_subsystem_health("eps", "yellow", "Battery temp warning")
display_data = dashboard.get_display_data()
```

## Data Flow

```
Simulator
  ↓
[TM Telemetry via TCP]
  ↓
MCSServer._tm_receive_loop()
  ├→ Decommutate packets
  ├→ Generate alarms (S5 events, S12 limits)
  ├→ Feed to _fdir_alarm_panel
  └→ Broadcast via WebSocket
  ↓
[State polling every 1 second]
  ↓
MCSServer._state_poll_loop()
  ├→ HTTP GET /api/mcs-state
  ├→ Update all panel monitors
  └→ Broadcast to WebSocket clients
  ↓
[HTTP API endpoints]
  ├→ GET /api/displays/contact-schedule
  ├→ GET /api/displays/power-budget
  ├→ GET /api/displays/fdir-alarms
  ├→ GET /api/displays/procedure-status
  └→ GET /api/displays/system-overview
  ↓
Frontend (JavaScript)
  ├→ displayPanels.js renders panels
  ├→ Chart.js renders trending plots
  └→ displays.css styles components
  ↓
Operator Browser UI
```

## Performance Considerations

- Contact scheduler updates: 5 second intervals
- Power budget updates: 3 second intervals
- FDIR alarms updates: 2 second intervals
- Procedure status updates: 3 second intervals
- System overview updates: 5 second intervals

All updates are asynchronous to prevent UI blocking.

## Testing

### Unit Tests

Each module includes a `get_display_data()` method that can be tested independently:

```python
def test_power_budget_monitor():
    monitor = PowerBudgetMonitor()
    monitor.update_from_telemetry(mock_state)
    data = monitor.get_display_data()
    assert data['power_margin_w'] > 0
    assert data['battery_soc_percent'] <= 100
```

### Integration Tests

Test the full data flow:

```python
async def test_contact_schedule_endpoint():
    server = MCSServer(config_dir)
    resp = await client.get('/api/displays/contact-schedule')
    assert resp.status == 200
    data = await resp.json()
    assert 'next_passes' in data
    assert 'current_contact_status' in data
```

## Future Enhancements

1. **Real-time trending graphs** using Chart.js with archival data
2. **Customizable thresholds** for alarm limits per subsystem
3. **Alarm suppression rules** (e.g., suppress low-priority during eclipse)
4. **Procedure progress estimation** based on historical execution times
5. **Custom dashboard layouts** (drag-drop widget arrangement)
6. **Export functionality** (CSV/PDF reports)
7. **Predictive analytics** (battery depletion forecasting)
8. **Multi-satellite support** (aggregate fleet status)

## Files Modified/Created

### New Files
- `src/smo_mcs/displays/contact_pass_scheduler.py`
- `src/smo_mcs/displays/power_budget.py`
- `src/smo_mcs/displays/fdir_alarm_panel.py`
- `src/smo_mcs/displays/procedure_status.py`
- `src/smo_mcs/displays/system_overview.py`
- `src/smo_mcs/static/displays.js`
- `src/smo_mcs/static/displays.css`

### Modified Files
- `src/smo_mcs/server.py` (added imports, handler methods, panel integrations)
- `src/smo_mcs/displays/__init__.py` (added exports)
- `src/smo_mcs/displays/widgets.py` (enhanced with trending and status)
- `configs/eosat1/mcs/displays.yaml` (added new display positions)
- `configs/eosat1/mcs/positions.yaml` (added new visible tabs)

## Maintenance Notes

1. **Archive retention:** Alarms stored for last 1000 entries in memory (max)
2. **Chart data points:** Trending limited to 300 points per series
3. **Update intervals:** Tune based on network bandwidth and UI responsiveness
4. **Memory usage:** Estimated < 10 MB for full panel system with 100 passes + 1000 alarms

---

**Version:** 1.0
**Last Updated:** 2026-04-04

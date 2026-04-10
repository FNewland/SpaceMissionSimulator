# MCS Display Panels API Reference

## Quick Start

All new display endpoints return JSON responses with real-time data.

### Base URL
```
http://mcs-host:9090/api/displays/
```

---

## Endpoints

### 1. Contact Schedule Panel

**Endpoint:** `GET /api/displays/contact-schedule`

**Description:** Retrieves next 10 contact windows and current contact status.

**Query Parameters:** None

**Response Example:**
```json
{
  "next_passes": [
    {
      "index": 0,
      "aos_time": 1712280000.0,
      "los_time": 1712280600.0,
      "duration_min": 10.0,
      "max_elevation": 45.5,
      "ground_station": "INUVIK",
      "data_downlink_capacity": 256.0,
      "status": {
        "status": "upcoming",
        "time_to_aos": 3600.0
      },
      "elevation_color": "green"
    }
  ],
  "current_contact_status": {
    "in_contact": false,
    "ground_station": "None",
    "time_to_aos": 3600.0
  },
  "timestamp": 1712276400.0
}
```

**Response Fields:**
- `next_passes[]` — Array of next contact windows
  - `aos_time` — Acquisition of Signal (Unix timestamp)
  - `los_time` — Loss of Signal (Unix timestamp)
  - `duration_min` — Duration in minutes
  - `max_elevation` — Maximum elevation in degrees
  - `ground_station` — Ground station name
  - `data_downlink_capacity` — Estimated data transfer in MB
  - `elevation_color` — Color indicator ("green", "yellow", "orange", "red")
- `current_contact_status` — Current or next contact information

---

### 2. Power Budget Monitor

**Endpoint:** `GET /api/displays/power-budget`

**Description:** Real-time power generation, consumption, battery status, and subsystem breakdown.

**Query Parameters:** None

**Response Example:**
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
  "time_to_eclipse_exit_s": null,
  "per_subsystem_power": {
    "eps": 15.0,
    "aocs": 25.0,
    "tcs": 30.0,
    "ttc": 20.0,
    "payload": 20.0,
    "obdh": 10.0
  },
  "total_subsystem_power": 120.0
}
```

**Response Fields:**
- `power_gen_w` — Current power generation in watts
- `power_cons_w` — Current power consumption in watts
- `power_margin_w` — Difference (generation - consumption)
- `battery_soc_percent` — Battery state of charge 0-100%
- `battery_temp_c` — Battery temperature in Celsius
- `soc_trend` — "charging", "discharging", or "stable"
- `load_shedding_stage` — 0-3 indicator
- `load_shedding_label` — Human-readable stage label
- `eclipse_active` — Boolean, true if in eclipse
- `time_to_eclipse_entry_s` — Seconds to eclipse entry (null if n/a)
- `time_to_eclipse_exit_s` — Seconds to eclipse exit (null if n/a)
- `per_subsystem_power` — Map of subsystem name to power draw in watts

---

### 3. FDIR/Alarm Panel

**Endpoint:** `GET /api/displays/fdir-alarms`

**Description:** Active alarms, FDIR level, S12/S19 monitoring status.

**Query Parameters:** None

**Response Example:**
```json
{
  "active_alarms": [
    {
      "id": 42,
      "timestamp": 1712276400.0,
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
  "alarm_journal": [...],
  "s12_monitoring": {
    "active_rules": 12,
    "violations": 1,
    "rules": [...]
  },
  "s19_event_action": {
    "active_rules": 8,
    "triggered_count": 0,
    "rules": [...]
  },
  "fdir_level": "equipment",
  "fdir_level_color": "yellow"
}
```

**Response Fields:**
- `active_alarms[]` — Active (unacknowledged) alarms, sorted by severity
  - `id` — Unique alarm ID
  - `timestamp` — Unix timestamp when alarm was raised
  - `severity` — "CRITICAL", "HIGH", "MEDIUM", or "LOW"
  - `subsystem` — Subsystem name (EPS, AOCS, TCS, etc.)
  - `parameter` — Parameter ID or name that triggered alarm
  - `description` — Human description
  - `value` — Current value
  - `limit` — Limit that was exceeded
  - `source` — Source service (S5, S12, S19)
- `alarm_count_by_severity` — Count of active alarms by severity
- `alarm_journal[]` — Last 50 alarm events (including acknowledged)
- `s12_monitoring` — S12 parameter monitoring status
- `s19_event_action` — S19 event-action rules status
- `fdir_level` — "nominal", "equipment", "subsystem", or "system"
- `fdir_level_color` — Corresponding color ("green", "yellow", "orange", "red")

---

### 4. Acknowledge Alarm

**Endpoint:** `POST /api/displays/alarms/{alarm_id}/ack`

**Description:** Mark an alarm as acknowledged.

**Path Parameters:**
- `alarm_id` — Integer alarm ID

**Request Body:** None (empty JSON object acceptable)

**Response Example:**
```json
{
  "status": "acknowledged",
  "alarm_id": 42
}
```

---

### 5. Alarm Trends Query

**Endpoint:** `POST /api/displays/alarm-trends`

**Description:** Query historical alarm data for a subsystem.

**Request Body:**
```json
{
  "subsystem": "EPS",
  "limit": 100
}
```

**Response Example:**
```json
{
  "subsystem": "EPS",
  "alarms": [
    {
      "id": 40,
      "timestamp": 1712276350.0,
      "severity": "MEDIUM",
      "subsystem": "EPS",
      "parameter": "0x0101",
      "value": "92.5",
      "acknowledged": true,
      "source": "S12"
    }
  ],
  "count": 1
}
```

---

### 6. Procedure Status Panel

**Endpoint:** `GET /api/displays/procedure-status`

**Description:** Current procedure execution status and available procedures.

**Query Parameters:** None

**Response Example:**
```json
{
  "available_procedures": [
    {"id": "comm_window_op", "name": "Communications Window Operations"},
    {"id": "safe_mode_entry", "name": "Safe Mode Entry"}
  ],
  "executing_procedure": {
    "procedure_id": "comm_window_op",
    "name": "Communications Window Operations",
    "description": "Establish contact and downlink science data",
    "state": "running",
    "current_step": 3,
    "total_steps": 8,
    "progress_percent": 37,
    "steps": [
      {
        "number": 1,
        "name": "Configure TT&C",
        "description": "Set TT&C to communication mode",
        "command": "S8.1 data=0x01",
        "wait_time_s": 0.0,
        "is_current": false,
        "is_completed": true
      },
      {
        "number": 2,
        "name": "Enable PA",
        "description": "Power on RF power amplifier",
        "command": "S8.1 data=0x02",
        "wait_time_s": 5.0,
        "is_current": false,
        "is_completed": true
      },
      {
        "number": 3,
        "name": "Downlink data",
        "description": "Begin telemetry downlink",
        "command": "S8.1 data=0x03",
        "wait_time_s": 30.0,
        "is_current": true,
        "is_completed": false
      }
    ]
  },
  "execution_log": []
}
```

**Response Fields:**
- `available_procedures[]` — List of available procedures
- `executing_procedure` — Currently executing procedure (null if none)
  - `procedure_id` — Unique procedure identifier
  - `name` — Human-readable name
  - `state` — "idle", "running", "paused", "completed", "aborted", "error"
  - `current_step` — Current step number
  - `total_steps` — Total steps in procedure
  - `progress_percent` — Completion percentage 0-100
  - `steps[]` — Array of procedure steps
    - `number` — Step sequence number
    - `name` — Step name
    - `description` — Step description
    - `command` — PUS command to execute (if applicable)
    - `wait_time_s` — Wait time after command (seconds)
    - `is_current` — Boolean, true for current step
    - `is_completed` — Boolean, true for completed steps

---

### 7. System Overview Dashboard

**Endpoint:** `GET /api/displays/system-overview`

**Description:** Top-level satellite status including mode, subsystem health, key parameters, contacts, and alarms.

**Query Parameters:** None

**Response Example:**
```json
{
  "satellite_mode": "NOMINAL",
  "satellite_mode_color": "green",
  "subsystem_health": [
    {
      "name": "EPS",
      "status": "green",
      "description": "Power System Nominal"
    },
    {
      "name": "AOCS",
      "status": "green",
      "description": "Attitude Control Nominal"
    },
    {
      "name": "TCS",
      "status": "yellow",
      "description": "Temperature warning on battery heater"
    },
    {
      "name": "TT&C",
      "status": "green",
      "description": "Telecom Nominal"
    },
    {
      "name": "OBDH",
      "status": "green",
      "description": "On-Board Computer Nominal"
    },
    {
      "name": "Payload",
      "status": "green",
      "description": "Payload Nominal"
    }
  ],
  "healthy_subsystems": 5,
  "warning_subsystems": 1,
  "alarm_subsystems": 0,
  "key_parameters": [
    {
      "name": "Battery SoC",
      "value": 75.3,
      "units": "%",
      "status": "green"
    },
    {
      "name": "Attitude Error",
      "value": 0.5,
      "units": "deg",
      "status": "green"
    },
    {
      "name": "FPA Temperature",
      "value": -15.2,
      "units": "°C",
      "status": "green"
    },
    {
      "name": "Link Margin",
      "value": 8.3,
      "units": "dB",
      "status": "green"
    },
    {
      "name": "Storage %",
      "value": 45.0,
      "units": "%",
      "status": "green"
    }
  ],
  "active_contacts": 0,
  "next_contact_countdown_s": 3600.0,
  "next_contact_countdown_min": 60.0,
  "active_alarms": {
    "total": 2,
    "critical": 0,
    "high": 1,
    "medium_low": 1
  }
}
```

**Response Fields:**
- `satellite_mode` — "NOMINAL", "SAFE", "CONTINGENCY", "COMMISSIONING", or "DECOMMISSIONED"
- `satellite_mode_color` — Color indicator for mode
- `subsystem_health[]` — Array of all subsystem health statuses
  - `name` — Subsystem name
  - `status` — "green", "yellow", or "red"
  - `description` — Human description of status
- `healthy_subsystems` — Count of GREEN subsystems
- `warning_subsystems` — Count of YELLOW subsystems
- `alarm_subsystems` — Count of RED subsystems
- `key_parameters[]` — Key system parameters
  - `name` — Parameter name
  - `value` — Current value
  - `units` — Unit string
  - `status` — "green", "yellow", or "red"
- `active_contacts` — Number of active contacts
- `next_contact_countdown_s` — Seconds to next AOS (null if no future contacts)
- `next_contact_countdown_min` — Minutes to next AOS (null if no future contacts)
- `active_alarms` — Alarm count breakdown

---

## HTTP Response Codes

| Code | Meaning |
|------|---------|
| 200 | Success, valid response |
| 400 | Bad request (invalid parameters) |
| 404 | Resource not found |
| 500 | Server error |

---

## Data Types

### Common Response Fields

**Unix Timestamp**
- Seconds since 1970-01-01T00:00:00Z
- Example: `1712276400.0`

**Severity Levels**
- "CRITICAL" (0) — System-level fault
- "HIGH" (1) — Subsystem-level issue
- "MEDIUM" (2) — Equipment-level warning
- "LOW" (3) — Informational

**Colors**
- "green" — Nominal/OK
- "yellow" — Warning/Caution
- "orange" — Elevated warning
- "red" — Alarm/Failure

---

## Rate Limiting Notes

- Contact schedule: Updated every 5 seconds
- Power budget: Updated every 3 seconds
- FDIR alarms: Updated every 2 seconds
- Procedure status: Updated every 3 seconds
- System overview: Updated every 5 seconds

All endpoints should be polled or long-polled at appropriate intervals. For real-time updates, use WebSocket: `GET /ws`

---

## WebSocket Events

When subscribed via WebSocket (`/ws`), the following messages are broadcast:

### Alarm Event
```json
{
  "type": "alarm",
  "alarm": {
    "id": 42,
    "timestamp": 1712276400.0,
    "severity": 1,
    "subsystem": "EPS",
    "parameter": "0x0102",
    "value": "45.5",
    "limit": "40.0",
    "acknowledged": false,
    "source": "S12"
  }
}
```

### State Update
```json
{
  "type": "state",
  "data": {
    "eps": { "bat_soc": 75.3, "power_gen": 150.2, ... },
    "aocs": { "att_error": 0.5, ... },
    ...
  }
}
```

---

## Examples

### JavaScript Fetch
```javascript
// Get power budget
fetch('/api/displays/power-budget')
  .then(r => r.json())
  .then(data => {
    console.log(`Battery: ${data.battery_soc_percent}%`);
    console.log(`Power margin: ${data.power_margin_w}W`);
  });

// Acknowledge alarm
fetch('/api/displays/alarms/42/ack', { method: 'POST' })
  .then(r => r.json())
  .then(data => console.log(data.status));
```

### cURL
```bash
# Get contact schedule
curl http://localhost:9090/api/displays/contact-schedule

# Get FDIR status
curl http://localhost:9090/api/displays/fdir-alarms

# Acknowledge alarm 42
curl -X POST http://localhost:9090/api/displays/alarms/42/ack
```

### Python
```python
import requests
import json

# Get system overview
resp = requests.get('http://localhost:9090/api/displays/system-overview')
data = resp.json()
print(f"Satellite Mode: {data['satellite_mode']}")
print(f"Alarms: {data['active_alarms']['total']}")

# Get power budget
resp = requests.get('http://localhost:9090/api/displays/power-budget')
data = resp.json()
print(f"Power Margin: {data['power_margin_w']}W")
```

---

**Version:** 1.0
**Last Updated:** 2026-04-04

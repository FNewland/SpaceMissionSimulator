# MCS and Planner Gap Analysis

**Document ID:** EOSAT1-GAP-MCS-PLN-001
**Date:** 2026-03-12
**Scope:** Comparison of 6 operations research documents against implemented MCS and Planner source code
**Classification:** UNCLASSIFIED -- For Simulation Use Only

---

## Methodology

Six operations research documents were reviewed:

1. `aocs_requirements.md` (EOSAT1-REQ-ADCS-001)
2. `eps_tcs_requirements.md` (EOSAT1-ORD-EPSTCS-001)
3. `flight_director_requirements.md` (EOSAT1-REQ-FD-001)
4. `obdh_fdir_requirements.md` (EOSAT1-REQ-OBDH-FDIR-001)
5. `payload_requirements.md` (EOSAT1-REQ-PLD-001)
6. `ttc_requirements.md` (EOSAT1-OPS-REQ-TTC-001)

Requirements were compared against the actual implementation in:

- `packages/smo-mcs/src/smo_mcs/server.py` (MCS backend)
- `packages/smo-mcs/src/smo_mcs/static/index.html` (MCS frontend, 4325 lines)
- `packages/smo-planner/src/smo_planner/server.py` (Planner backend)
- `packages/smo-planner/src/smo_planner/activity_scheduler.py` (Activity scheduler)
- `packages/smo-planner/src/smo_planner/orbit_planner.py` (Orbit planner)
- `packages/smo-planner/src/smo_planner/contact_planner.py` (Contact planner)
- `configs/eosat1/mcs/positions.yaml` (Position access control)
- `configs/eosat1/mcs/displays.yaml` (Display widget config)

**Effort estimates:** S = Small (< 1 day), M = Medium (1--3 days), L = Large (> 3 days)

---

## Summary Statistics

| Category | Gaps Found | S | M | L |
|---|---|---|---|---|
| MCS Display Gaps | 29 | 6 | 15 | 8 |
| MCS Commanding / Workflow Gaps | 14 | 4 | 6 | 4 |
| Planner Gaps | 18 | 3 | 8 | 7 |
| Configuration / Data Gaps | 8 | 5 | 3 | 0 |
| **Total** | **69** | **18** | **32** | **19** |

---

## 1. MCS Display Gaps

### 1.1 AOCS -- Missing Display Widgets

| # | Gap | Source Req | Severity | Effort | Details |
|---|---|---|---|---|---|
| 1 | Gyroscope monitoring panel missing | REQ-DISP-001 (AOCS) | Medium | M | `displays.yaml` flight_dynamics pages lack gyro_bias_x/y/z and gyro_temp. No line chart for gyro bias trend (30 min). Parameters exist in telemetry (0x0270-0x0273) but are not rendered in any display page. |
| 2 | GPS/Navigation panel missing | REQ-DISP-002 (AOCS) | Medium | M | No display widget for gps_fix, gps_pdop, gps_num_sats, gps_lat/lon/alt, or gps_vx/vy/vz. These are available via the planner spacecraft-state API but not shown on the AOCS tab. |
| 3 | Magnetometer comparison panel missing | REQ-DISP-003 (AOCS) | Medium | M | displays.yaml has no widget for mag_x/y/z, mag_field_total, or active magnetometer indicator. No magnetometer field magnitude line chart. |
| 4 | Star camera detailed status missing | REQ-DISP-004 (AOCS) | Low | S | Current display shows ST1 status, ST1 num_stars, ST2 status, and CSS valid in a value table, but lacks colour-coded status per state (OFF/BOOTING/TRACKING/BLIND/FAILED), boot progress indicator, or "selected primary" indicator. |
| 5 | Reaction wheel temperature trend chart missing | REQ-DISP-005 (AOCS) | Medium | S | Wheel speeds chart exists; wheel temperature chart (rw1_temp through rw4_temp, 30 min) does not. RW temps are the earliest bearing degradation indicator. |
| 6 | AOCS mode state machine visualisation missing | REQ-DISP-006 (AOCS) | Low | L | No graphical mode state machine diagram showing 9 modes as nodes, transition arrows, current mode highlight, guard condition status. Would require a custom SVG or canvas widget. |
| 7 | Momentum budget dashboard missing | REQ-DISP-007 (AOCS) | Medium | M | total_momentum is in the Actuators value table but there is no gauge (0-1.0 Nms with yellow/red zones), no per-wheel momentum bar chart, no estimated time to saturation, no desat status widget. |
| 8 | CSS sun vector 3D visualisation missing | REQ-DISP-008 (AOCS) | Low | L | No 3D unit-sphere widget for CSS sun vector. Would require a WebGL or Three.js component. |

### 1.2 EPS/TCS -- Missing Display Widgets

| # | Gap | Source Req | Severity | Effort | Details |
|---|---|---|---|---|---|
| 9 | PDM control panel missing | REQ-MCS-002 (EPS/TCS) | High | L | No interactive power line table with per-line ON/OFF toggles, OC reset buttons, unswitchable line distinction, load shed quick-action panel, or "Restore All" button. The current EPS display only shows line currents and OC trip flags as value/status indicators. |
| 10 | Heater control panel missing | REQ-MCS-004 (EPS/TCS) | Medium | M | TCS displays show heater status LEDs and temperature values but lack: manual/auto mode indicator, per-circuit ON/OFF buttons, duty cycle bars, thermostat setpoint editor, or auto-mode return buttons. |
| 11 | Power budget summary missing | REQ-MCS-001 (EPS/TCS) | Medium | M | No calculated power margin display (power_gen - power_cons) with colour coding. The power balance line chart exists in the Trends page but there is no real-time numeric margin indicator. |
| 12 | Battery DoD and cycle count not displayed | REQ-MCS-001 (EPS/TCS) | Low | S | bat_dod (0x0120) and bat_cycles (0x0121) exist in telemetry but are not in any displays.yaml widget. |
| 13 | Eclipse periods not shown on charts | REQ-MCS-005 (EPS/TCS) | Medium | M | Power balance chart and battery SoC trend chart lack grey shaded background regions for eclipse periods. Chart.js annotation plugin would be needed. |
| 14 | Per-line current chart with OC thresholds missing | REQ-MCS-005 (EPS/TCS) | Medium | M | The Line Currents chart shows 4 lines (0-3) but not all 8, and lacks per-line OC threshold dashed red lines or OC trip event markers. |
| 15 | Heater ON/OFF periods on temperature charts missing | REQ-MCS-005 (EPS/TCS) | Low | M | Temperature trend charts lack coloured bars showing heater ON/OFF periods overlaid on temperature traces. |
| 16 | Battery heater manual mode warning missing | REQ-TH-005 (EPS/TCS) | Medium | S | No MCS warning when the battery heater is in manual mode (thermostat disabled). |

### 1.3 TTC -- Missing Display Widgets

| # | Gap | Source Req | Severity | Effort | Details |
|---|---|---|---|---|---|
| 17 | Antenna deployment status indicator missing | REQ-TTC-MCS-040 (TTC) | Medium | M | No display element for antenna_deploy_status (STOWED/DEPLOYING/DEPLOYED/FAILED). The simulator does not yet model this parameter (0x0522), so both simulator and MCS changes needed. |
| 18 | PDM channel status display missing | REQ-TTC-MCS-050 (TTC) | Medium | M | No PDM timer countdown, PDM decode status, or TX+PA-via-PDM indicator. Depends on simulator PDM model (not yet implemented). |
| 19 | Ground station health status display missing | REQ-TTC-MCS-020 (TTC) | Medium | M | MCS Overview map shows ground station markers but no per-station health panel (tracking state, last contact, next AOS/LOS, G/T status, uplink/downlink status). |
| 20 | Link budget display missing | REQ-TTC-MCS-010 (TTC) | Medium | L | No computed-vs-measured RSSI comparison, FSPL display, stowed-vs-deployed gain assumption, or RSSI vs elevation scatter plot. |
| 21 | Doppler trend chart missing | REQ-TTC-MCS-030 (TTC) | Low | S | TTC Link Trends page has RSSI/margin, BER, and PA temp charts but no Doppler (doppler_hz) chart. |

### 1.4 Payload -- Missing Display Widgets

| # | Gap | Source Req | Severity | Effort | Details |
|---|---|---|---|---|---|
| 22 | SNR display and chart missing | REQ-PLD-MCS-001, REQ-PLD-MCS-003 (Payload) | Medium | M | SNR (0x0616), compression_ratio (0x0614), cal_lamp_on (0x0615), detector_temp (0x0617), integration_time (0x0618), swath_width_km (0x0619) are all collected by the simulator but not shown on the Payload tab. SNR time-series chart is absent. |
| 23 | FPA temperature reference line missing | REQ-PLD-MCS-002 (Payload) | Low | S | FPA temperature chart exists but lacks a horizontal reference line at the cooler target temperature. |
| 24 | Image catalog table missing | REQ-PLD-MCS-004 (Payload) | Medium | M | No tabular image catalog showing scene ID, timestamp, lat/lon, quality, status, size, and memory segment per image. Only last_scene_id and last_scene_quality are displayed. |

### 1.5 OBDH -- Missing Display Widgets

| # | Gap | Source Req | Severity | Effort | Details |
|---|---|---|---|---|---|
| 25 | SEU and memory health display missing | REQ-MCS-013, REQ-MCS-014 (OBDH) | Medium | M | seu_count (0x0319), scrub_progress (0x031A), task_count (0x031B), stack_usage (0x031C), heap_usage (0x031D), mem_errors (0x031E) are not displayed. The OBC Trends page has CPU load and buffer fill charts but no SEU/memory chart. |
| 26 | Subsystem reachability matrix missing | REQ-MCS-008 (OBDH) | Low | M | No visual matrix showing which subsystems are connected to the active CAN bus and whether they are responding. |

### 1.6 Flight Director -- Missing Display Widgets

| # | Gap | Source Req | Severity | Effort | Details |
|---|---|---|---|---|---|
| 27 | Pass plan timeline (Gantt) view missing | REQ-VIZ-001, REQ-VIZ-002 (FD) | High | L | No Gantt-style pass timeline showing 24-hour contact windows per station with scheduled activity overlays, colour coding by type, and state indicators. The planner has a static UI but the MCS has no timeline view. |
| 28 | LEOP timeline display missing | REQ-LEOP-003 (FD) | Medium | L | No elapsed-time-since-separation display, orbit number, or procedure completion status tracker for LEOP. |
| 29 | Contact detail panel missing | REQ-VIZ-004 (FD) | Low | M | No click-to-expand panel for individual contact windows showing AOS/LOS, max elevation, scheduled activities, and link budget estimate. |

---

## 2. MCS Commanding and Workflow Gaps

| # | Gap | Source Req | Severity | Effort | Details |
|---|---|---|---|---|---|
| 30 | Mode transition confirmation dialog missing | REQ-CMD-001 (AOCS) | Medium | M | When commanding AOCS_SET_MODE, no dialog shows current mode, target mode, pre-conditions, or warnings if pre-conditions are unmet. The PUS command builder sends commands directly after form completion. |
| 31 | Desaturation pre-check missing | REQ-CMD-002 (AOCS) | Medium | M | No automatic check of MTQ enabled, MAG valid, not-in-eclipse, or wheel speed display before sending AOCS_DESATURATE. |
| 32 | Wheel enable/disable safeguard missing | REQ-CMD-003 (AOCS) | Medium | S | No warning showing how many wheels remain active or red warning when disabling would leave fewer than 3 active wheels. |
| 33 | Critical command confirmation dialog incomplete | REQ-VER-001 (FD) | Medium | M | server.py does not enforce critical command confirmation for OBC_REBOOT (func_id 42) or OBC_SWITCH_UNIT (func_id 43). Position access control exists but there is no criticality-level gating or impact-description dialog in the MCS frontend. |
| 34 | Command pending indicator missing | REQ-VER-006 (FD) | Low | S | No visual indicator when a critical command is awaiting S1.1 acceptance or when a GO/NO-GO poll is awaiting responses. |
| 35 | FD notification on any REJECTED/FAILED missing | REQ-VER-007 (FD) | Medium | M | All TM (including S1.2/S1.8) is broadcast to all WS clients, but there is no FD-specific notification or alarm when any position's command is rejected or fails. The verification log shows these events but no proactive alert is generated. |
| 36 | Alarm acknowledgement workflow missing | REQ-ALM-005 (FD) | High | L | Alarms (red limit violations) do not persist until FD acknowledgement. There is no alarm acknowledgement mechanism in server.py or index.html. Alarm badges show counts but reset automatically. |
| 37 | Audio alarm missing | REQ-ALM-001 (FD) | Medium | S | No audio alert on red limit violations at the FD console. The `--status-alarm` CSS class handles visual styling only. |
| 38 | GO/NO-GO poll cancel and override missing | REQ-GNG-004 (FD) | Low | S | FD cannot cancel an active poll or manually override the result. The `_handle_go_nogo_poll` creates a poll but there is no cancel endpoint. |
| 39 | Structured handover report generation missing | REQ-HND-002 (FD) | Medium | M | The handover API (`/api/handover`) stores timestamped notes but does not generate a structured report with subsystem state summary, active alarms, upcoming contacts, or onboard schedule. |
| 40 | PDM control panel command validation missing | REQ-MCS-003 (EPS/TCS) | Medium | M | No command validation logic to reject commands to unswitchable lines with clear error message, warn before commanding OFF on htr_bat during eclipse, or require confirmation before commanding OFF on ttc_tx. The server-side access control checks position/service/func_id but not operational context. |
| 41 | Imaging pre-condition check missing | REQ-PLD-CMD-002 (Payload) | Medium | M | PAYLOAD_SET_MODE(mode=2) to IMAGING has no MCS-side pre-condition check for fpa_ready, att_error < 0.5, bat_soc > 40%, or store_used < 90%. |
| 42 | Procedure-level verification with pause on rejection missing | REQ-VER-005 (FD) | Medium | M | The procedure runner sends commands with "PROC:" prefix but does not wait for S1.1 acceptance before advancing. If S1.2 rejection is received, there is no automatic pause or FD alert. The `ProcedureRunner` class does not consume verification feedback from the verification log. |
| 43 | Cross-position telemetry visibility for Payload Ops missing | REQ-PLD-MCS-007 (Payload) | Low | S | Payload Ops cannot see AOCS att_error or EPS bat_soc on the Overview tab. Position filtering via `overview_subsystems: [payload]` excludes EPS and AOCS from the Payload Ops Overview. |

---

## 3. Planner Gaps

### 3.1 Missing Planner Features -- AOCS-Related

| # | Gap | Source Req | Severity | Effort | Details |
|---|---|---|---|---|---|
| 44 | Momentum prediction model missing | REQ-PLAN-001 (AOCS) | High | L | The planner has no momentum accumulation model. It cannot predict when desaturation will be needed or automatically schedule desaturation activities when predicted total_momentum exceeds 0.4 Nms. |
| 45 | Eclipse-aware scheduling missing | REQ-PLAN-002 (AOCS) | High | M | The activity scheduler does not check eclipse state when scheduling. Desaturation activities can be placed during eclipse (MTQ needs B-field). Imaging activities requiring FINE_POINT are not excluded from eclipse or 120s eclipse boundary windows. `validate_schedule()` does not check eclipse constraints. |
| 46 | Attitude settling time not modeled | REQ-PLAN-003 (AOCS) | Medium | M | The scheduler does not account for settling time (60s post-slew, 30s post-desat) when scheduling imaging after attitude manoeuvres. |
| 47 | Wheel speed constraint checking missing | REQ-PLAN-004 (AOCS) | Medium | M | No prediction of wheel speed evolution or avoidance of activities that would increase momentum when any wheel exceeds 4500 RPM. |
| 48 | GPS fix requirement for orbit events missing | REQ-PLAN-005 (AOCS) | Low | S | The planner does not verify gps_fix >= 2 before relying on GPS-derived eclipse/contact times. No fallback to ground-computed ephemeris. |

### 3.2 Missing Planner Features -- EPS/TCS-Related

| # | Gap | Source Req | Severity | Effort | Details |
|---|---|---|---|---|---|
| 49 | Orbit-level power budget prediction missing | REQ-PLN-001 (EPS/TCS) | High | L | The planner does not maintain a power budget prediction based on orbital geometry, solar array degradation, planned power line config, or eclipse heater estimates. |
| 50 | Eclipse entry/exit markers on MCS charts missing | REQ-PLN-002 (EPS/TCS) | Medium | M | The planner provides eclipse state in ground track data, but eclipse entry/exit predictions are not delivered to the MCS as overlay markers for trend charts. |
| 51 | Power margin flagging for planned activities missing | REQ-PLN-003 (EPS/TCS) | Medium | M | `validate_schedule()` checks for name-based conflicts but not for aggregate power consumption exceeding the power budget. The `power_w` field exists on activities but is not summed or validated against available power. |
| 52 | Thermal planning for eclipse missing | REQ-PLN-004 (EPS/TCS) | Low | M | No prediction of worst-case cold temperatures during eclipse or verification of battery heater power budget allocation. |
| 53 | FPA cooler power cross-reference missing | REQ-PLN-005 (EPS/TCS) | Low | S | The planner does not cross-reference payload imaging windows with FPA cooler power requirements in power budget predictions. |

### 3.3 Missing Planner Features -- TTC-Related

| # | Gap | Source Req | Severity | Effort | Details |
|---|---|---|---|---|---|
| 54 | 72-hour planning horizon not supported | REQ-TTC-PLN-003 (TTC) | Low | S | `_compute_contacts()` uses a 24-hour horizon. The planner should support at least 72 hours for multi-day scheduling. The `compute_windows` method in `contact_planner.py` accepts `duration_hours` so this is a server configuration gap. |
| 55 | Contact gap analysis missing | REQ-TTC-PLN-005, REQ-GS-004 (TTC/FD) | Medium | M | The planner does not compute or flag contact gaps exceeding 4 hours (approaching the 6-hour autonomous recovery timer). No worst-case gap computation or gap reduction analysis. |
| 56 | Data volume per pass estimation missing | REQ-TTC-PLN-011 (TTC) | Medium | M | The planner does not estimate transferable data volume per pass based on duration at each rate threshold, time above 10 deg elevation, or lock acquisition overhead. |
| 57 | Pass prioritisation missing | REQ-TTC-PLN-010 (TTC) | Low | M | No pass priority ranking by max elevation, duration, data backlog, or station availability. Contacts are sorted by AOS time only. |

### 3.4 Missing Planner Features -- Payload-Related

| # | Gap | Source Req | Severity | Effort | Details |
|---|---|---|---|---|---|
| 58 | Imaging opportunity calculator missing | REQ-PLD-PLAN-001 (Payload) | High | L | No function to identify overpasses where the ground track places ocean target regions within the 60 km imaging swath. The planner has ground track prediction but no target region database or overflight intersection computation. |
| 59 | Imaging-downlink coordination missing | REQ-PLD-PLAN-002 (Payload) | Medium | M | When an imaging activity is scheduled, no automatic check that a contact window is available within 6 hours for data downlink. |
| 60 | Multi-day imaging campaign planner missing | REQ-PLD-PLAN-004 (Payload) | Low | L | No campaign planner that generates multi-day imaging schedules balanced against downlink capacity, power budget, and thermal constraints. |
| 61 | Imaging resource constraints not enforced | REQ-PLD-PLAN-003 (Payload) | Medium | M | The scheduler has a `conflicts_with` mechanism but does not enforce the 10-minute max imaging duration, 20-minute cooldown between sessions, or storage capacity constraints. |

### 3.5 Missing Planner Features -- OBDH/FD-Related

| # | Gap | Source Req | Severity | Effort | Details |
|---|---|---|---|---|---|
| 62 | Memory scrub window scheduling missing | REQ-PLN-001 (OBDH) | Low | S | The planner does not avoid scheduling payload imaging during OBC memory scrub windows. |
| 63 | Clock sync scheduling missing | REQ-PLN-002 (OBDH) | Low | S | No automatic daily clock synchronisation activity scheduling. |
| 64 | Schedule validation does not check contact requirement | REQ-PLN-011 (FD) | Medium | M | `validate_schedule()` checks for name-based conflicts but does not verify that contact-required activities (data_dump, software_upload) are scheduled within contact windows. The contacts list is passed to `validate_schedule()` but is not used for contact-overlap validation. |

---

## 4. Configuration and Data Gaps

| # | Gap | Source Req | Severity | Effort | Details |
|---|---|---|---|---|---|
| 65 | Ground station configuration mismatch | REQ-TTC-GS-IQ-001 (TTC) | Medium | S | `orbit.yaml` and `ground_stations.yaml` define Svalbard, Troll, Inuvik, and O'Higgins. The mission profile specifies Iqaluit (63.747N, 68.518W) and Troll. Iqaluit is not in the configuration. |
| 66 | displays.yaml position key mismatch | -- (Config) | Low | S | `displays.yaml` uses `power_thermal` and `flight_dynamics` as position keys; `positions.yaml` uses `eps_tcs` and `aocs`. The MCS may not correctly match position-specific display pages to logged-in positions. |
| 67 | Stale parameter references in displays.yaml | Known Issue | Low | S | `tcs.temp_thruster` used as a status_indicator parameter but the thruster heater status is at 0x040D (htr_thruster). `displays.yaml` references `tcs.temp_thruster` not `tcs.htr_thruster`. This is a known xfail issue. |
| 68 | Missing FDIR rules for payload FPA temperature | REQ-PLD-FDIR-002 (Payload) | Medium | S | `fdir.yaml` lacks `payload.fpa_temp > 12 C -> payload_poweroff` and `payload.store_used > 98% -> imaging_inhibit` rules. Not a direct MCS/planner gap but affects operational workflows. |
| 69 | TTC FDIR rules not in fdir.yaml | REQ-TTC-FDIR-001 (TTC) | Medium | S | `fdir.yaml` lacks TTC-specific rules: 24-hour no-contact autonomous transponder switch, transponder temperature out-of-range switch. |
| 70 | Proposed TTC parameters not in parameters.yaml | REQ-TTC-TM-001 (TTC) | Medium | M | Parameters 0x0520-0x0525 (pdm_timer_active, pdm_timer_remaining, antenna_deploy_status, burn_wire_armed, beacon_mode, gs_id) are proposed in the TTC requirements doc but do not exist in the parameter database or simulator. |
| 71 | Proposed TTC commands not in tc_catalog.yaml | REQ-TTC-CMD-001 (TTC) | Medium | M | func_ids 56-59 (antenna arm/fire/disarm, PDM reset) are proposed but do not exist in the TC catalog or simulator dispatch. |
| 72 | Payload phase-4 params not in payload.yaml | Known Issue (Payload) | Low | S | Parameters 0x0614-0x0619 (compression_ratio, cal_lamp, snr, detector_temp, integration_time, swath_width) are generated by the simulator but not listed in `payload.yaml` param_ids. |

---

## 5. Prioritised Implementation Roadmap

### Priority 1 -- High-Impact, Foundational (7 items)

| # | Gap | Effort | Rationale |
|---|---|---|---|
| 9 | PDM control panel | L | Core EPS commanding capability for load shed, overcurrent reset, and power line management. Required for 6 contingency procedures. |
| 36 | Alarm acknowledgement workflow | L | Without persistent alarms and FD acknowledgement, red-limit events go untracked. Critical for FDIR awareness and emergency response training. |
| 27 | Pass plan timeline (Gantt) view | L | The FD has no scheduling overview. Contact windows and activities are available via API but no visual timeline exists in the MCS. |
| 44 | Momentum prediction model | L | The planner cannot proactively schedule desaturation, which is a daily operational necessity. |
| 49 | Orbit-level power budget prediction | L | Without power budget prediction, the planner cannot flag activities that risk battery depletion during eclipse. |
| 45 | Eclipse-aware scheduling | M | Scheduling imaging or desaturation during eclipse is operationally invalid. The planner must use eclipse data it already computes. |
| 58 | Imaging opportunity calculator | L | Core payload operations requirement. Without target overflight detection, imaging campaigns cannot be planned systematically. |

### Priority 2 -- Medium-Impact, Operational Quality (16 items)

| # | Gap | Effort | Rationale |
|---|---|---|---|
| 1 | Gyroscope monitoring panel | M | Needed for sensor commissioning and bias drift detection. |
| 2 | GPS/Navigation panel | M | Needed for orbit determination validation. |
| 3 | Magnetometer comparison panel | M | Needed for dual-MAG switchover operations. |
| 7 | Momentum budget dashboard | M | Total momentum is displayed but lacks gauges, limits, and estimated time to saturation. |
| 10 | Heater control panel | M | Manual heater control and thermostat setpoint editing not available in MCS. |
| 11 | Power budget summary | M | No real-time power margin numeric display. |
| 22 | SNR and payload phase-4 telemetry | M | 6 payload parameters collected but not rendered. |
| 30 | Mode transition confirmation dialog | M | AOCS mode changes lack pre-condition verification in the MCS. |
| 33 | Critical command confirmation | M | OBC_REBOOT and OBC_SWITCH_UNIT lack confirmation dialogs. |
| 39 | Structured handover report | M | Handover log exists but no auto-generated structured report. |
| 42 | Procedure verification feedback | M | Procedure runner does not pause on command rejection. |
| 51 | Power margin flagging | M | Activities have power_w but aggregate is not validated. |
| 55 | Contact gap analysis | M | No flagging of gaps exceeding 4 hours. |
| 64 | Schedule contact validation | M | Contact-required activities not validated against contact windows. |
| 25 | SEU and memory health display | M | 6 OBDH phase-4 parameters not rendered. |
| 24 | Image catalog table | M | Only last_scene data shown; full catalog not accessible in UI. |

### Priority 3 -- Lower-Impact, Enhancement (17+ items)

All remaining gaps from sections 1-4, including CSS 3D visualisation (L), AOCS mode state machine SVG (L), Doppler chart (S), star camera colour coding (S), RW temperature chart (S), battery DoD display (S), audio alarm (S), GO/NO-GO cancel (S), and various low-priority planner enhancements.

---

## 6. Cross-Cutting Observations

### 6.1 Planner -- Activity Scheduler Limitations

The `ActivityScheduler` class (`activity_scheduler.py`, 143 lines) provides basic CRUD operations and name-based conflict detection but lacks:

- **Time overlap computation**: `check_conflicts()` compares names against `conflicts_with` lists but does not compute actual time overlaps using `start_time` and `duration_s`. Two simultaneous activities that do not conflict by name are allowed.
- **Contact window awareness**: The `contacts` parameter is accepted by `validate_schedule()` but is not used -- the method only calls `check_conflicts()`.
- **Pre-condition evaluation**: The `pre_conditions` list is stored on activities but never evaluated against spacecraft state.
- **State transitions**: `update_state()` allows any state transition without validation (e.g., COMPLETED -> PLANNED is allowed).

### 6.2 MCS -- Planner Integration

The MCS server does not directly consume planner APIs. The planner runs on port 9091 and the MCS on port 9090. Integration is limited to:

- The MCS `index.html` fetches ground track from the planner via the `/api/ground-track` endpoint (with `offset_minutes`).
- The planner uploads command sequences to the MCS via `/api/procedure/load`.

Missing integrations:
- MCS does not display planner contact windows or schedule.
- MCS does not overlay eclipse predictions on trend charts.
- MCS does not display planner pass timeline.
- No real-time spacecraft state from the planner is used by the MCS for context-aware commanding.

### 6.3 displays.yaml -- Position Key Alignment

The `displays.yaml` file uses different position keys (`power_thermal`, `flight_dynamics`) than `positions.yaml` (`eps_tcs`, `aocs`). This means the MCS backend's `load_mcs_displays()` may not correctly associate display pages with logged-in positions. The MCS frontend appears to use hardcoded subsystem tab rendering rather than `displays.yaml` widget definitions for most content.

### 6.4 Ground Station Configuration Drift

The codebase configures 4 ground stations (Svalbard, Troll, Inuvik, O'Higgins) in `orbit.yaml` and `ground_stations.yaml`. The mission profile and TTC requirements document specify 2 stations (Iqaluit, Troll). This configuration drift affects:
- Contact window predictions (more stations = more contacts = different training scenarios)
- Coverage analysis (2-station failure modes are more severe than 4-station)
- Ground track map display (wrong station markers)

---

*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

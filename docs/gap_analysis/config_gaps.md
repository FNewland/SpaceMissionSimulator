# EOSAT-1 Configuration and Procedure Gap Analysis

**Document ID:** EOSAT1-GAP-001
**Date:** 2026-03-12
**Mission:** EOSAT-1 Ocean Current Monitoring -- 6U multispectral imaging cubesat, 450 km sun-synchronous orbit (98 deg), ground stations at Iqaluit + Troll, cold-redundant systems, battery-heater-only thermal, 6 body-mounted solar panels.

**Method:** Cross-reference of 6 operations research documents (`docs/ops_research/`) against all EOSAT-1 configuration files, procedure index, and scenario definitions.

---

## Table of Contents

1. [Orbit and Mission Configuration Gaps](#1-orbit-and-mission-configuration-gaps)
2. [Ground Station Configuration Gaps](#2-ground-station-configuration-gaps)
3. [EPS Subsystem Configuration Gaps](#3-eps-subsystem-configuration-gaps)
4. [TCS Subsystem Configuration Gaps](#4-tcs-subsystem-configuration-gaps)
5. [TTC Subsystem Configuration Gaps](#5-ttc-subsystem-configuration-gaps)
6. [AOCS Subsystem Configuration Gaps](#6-aocs-subsystem-configuration-gaps)
7. [OBDH Subsystem Configuration Gaps](#7-obdh-subsystem-configuration-gaps)
8. [Payload Subsystem Configuration Gaps](#8-payload-subsystem-configuration-gaps)
9. [FDIR Configuration Gaps](#9-fdir-configuration-gaps)
10. [Telemetry Parameter Gaps](#10-telemetry-parameter-gaps)
11. [HK Structure Gaps](#11-hk-structure-gaps)
12. [Procedure Gaps](#12-procedure-gaps)
13. [Scenario Gaps](#13-scenario-gaps)
14. [Known Configuration Issues (Pre-Existing)](#14-known-configuration-issues-pre-existing)
15. [Summary Statistics](#15-summary-statistics)

---

## 1. Orbit and Mission Configuration Gaps

**Files examined:**
- `configs/eosat1/orbit.yaml`
- `configs/eosat1/mission.yaml`

### 1.1 Orbit Altitude Discrepancy

| Item | Config Value | Mission Profile | Research Docs |
|------|-------------|-----------------|---------------|
| Altitude | 450 km (`orbit.yaml`) | 450 km | 500 km (TTC doc Sec 12.1, AOCS doc Sec 1) |
| Inclination | 98.0 deg (`orbit.yaml`) | 98 deg | 97.4 deg (TTC doc, AOCS doc) |
| Orbital period | ~94 min (derived) | -- | ~95 min (TTC doc), ~94.6 min (AOCS doc) |

**GAP-ORB-001:** The orbit.yaml specifies 450 km altitude and 98 deg inclination, matching the mission profile. However, the research documents reference 500 km altitude and 97.4 deg inclination in several places. This internal inconsistency in the research documents should be resolved; the config files are correct per the mission brief.

**CHANGE NEEDED:** Update research documents to consistently use 450 km / 98 deg, or update orbit.yaml if the mission profile has changed. No config change required if 450 km / 98 deg is authoritative.

### 1.2 Mission Configuration

| Item | Current | Required |
|------|---------|----------|
| Payload bands | [443, 560, 665, 865] nm | [490, 560, 665, 842] nm per payload research doc Sec 2.2 |

**GAP-ORB-002:** `mission.yaml` lists payload bands as `[443, 560, 665, 865]` nm. The payload requirements document specifies `[490, 560, 665, 842]` nm for ocean color science (Blue at 490, Green at 560, Red at 665, NIR at 842). The Blue band center wavelength differs by 47 nm and the NIR differs by 23 nm.

**CHANGE NEEDED in `configs/eosat1/mission.yaml`:**
```yaml
payload:
  bands: [490, 560, 665, 842]  # nm -- ocean color (Blue, Green, Red, NIR)
```

---

## 2. Ground Station Configuration Gaps

**Files examined:**
- `configs/eosat1/orbit.yaml`
- `configs/eosat1/planning/ground_stations.yaml`

### 2.1 Ground Station Network -- ALIGNED

The config files correctly specify the mission-profile ground stations (Iqaluit + Troll). The old four-station network (Svalbard, Troll, Inuvik, O'Higgins) referenced in the FD research document has been replaced.

| Station | orbit.yaml | ground_stations.yaml | Mission Profile | Status |
|---------|-----------|---------------------|-----------------|--------|
| Iqaluit | 63.747N, 68.518W | 63.747N, 68.518W | 63.747N, 68.518W | OK |
| Troll | 72.012S, 2.535E | 72.012S, 2.535E | 72.012S, 2.535E | OK |

### 2.2 Ground Station Parameter Gaps

| Parameter | ground_stations.yaml | TTC Research Doc |
|-----------|---------------------|------------------|
| Iqaluit antenna diameter | 9.0 m | 7.3 m |
| Iqaluit G/T | 20.0 dB | Not specified |
| Troll antenna diameter | 7.3 m | 7.3 m |
| Troll G/T | 18.5 dB | 20 dB/K (from link budget table) |

**GAP-GS-001:** Iqaluit antenna diameter is 9.0 m in `ground_stations.yaml` but the TTC research document specifies 7.3 m. Verify with mission profile which is correct.

**CHANGE NEEDED in `configs/eosat1/planning/ground_stations.yaml`** (if 7.3 m is correct):
```yaml
  - name: Iqaluit
    antenna_diameter_m: 7.3
```

### 2.3 Missing Ground Station Failure Parameters

**GAP-GS-002:** The TTC research document (REQ-TTC-GSF-001 through GSF-005) requires ground station antenna failure modelling (total failure, tracking failure, reduced G/T, uplink-only, receive-only) with per-station injectable faults. No ground station failure parameters exist in any config file.

**CHANGE NEEDED:** Add ground station failure injection parameters to `ground_stations.yaml`:
```yaml
  - name: Iqaluit
    # ... existing params ...
    failure_modes:
      antenna_failure: false
      tracking_failure: false
      gt_degradation_db: 0.0
      tx_failure: false
      rx_failure: false
  - name: Troll
    # ... same structure ...
```

---

## 3. EPS Subsystem Configuration Gaps

**File examined:** `configs/eosat1/subsystems/eps.yaml`

### 3.1 Solar Array Model -- Two Wings vs. Six Panels

**GAP-EPS-001 (HIGH):** The mission profile specifies 6 body-mounted solar panels (one per face: +X, -X, +Y, -Y, +Z, -Z). The EPS config models only two wings (A and B), each 0.314 m2. The EPS/TCS research document (REQ-SOL-001 through REQ-SOL-005) requires a six-panel model with per-face illumination based on attitude.

The `parameters.yaml` already defines per-panel current parameters (0x012B--0x0130: `eps.sa_px_current` through `eps.sa_mz_current`), but `eps.yaml` does not define the six individual panels.

**CHANGE NEEDED in `configs/eosat1/subsystems/eps.yaml`:**
```yaml
arrays:
  # Retain legacy A/B for backward compatibility, map to aggregate
  - name: A
    area_m2: 0.314
    efficiency: 0.295
  - name: B
    area_m2: 0.314
    efficiency: 0.295

# Six body-mounted panels (per-face model)
body_panels:
  - { name: px, face: "+X", area_m2: 0.105, efficiency: 0.295 }
  - { name: mx, face: "-X", area_m2: 0.105, efficiency: 0.295 }
  - { name: py, face: "+Y", area_m2: 0.105, efficiency: 0.295 }
  - { name: my, face: "-Y", area_m2: 0.105, efficiency: 0.295 }
  - { name: pz, face: "+Z", area_m2: 0.105, efficiency: 0.295 }
  - { name: mz, face: "-Z", area_m2: 0.105, efficiency: 0.295 }
```

### 3.2 Missing EPS Phase 4 Parameters in Config

**GAP-EPS-002:** The following parameters exist in `parameters.yaml` but are NOT listed in `eps.yaml` `param_ids`:

| Parameter | ID | In parameters.yaml | In eps.yaml |
|-----------|-----|--------------------:|------------:|
| eps.sa_a_voltage | 0x010B | Yes | No |
| eps.sa_b_voltage | 0x010C | Yes | No |
| eps.oc_trip_flags | 0x010D | Yes | No |
| eps.uv_flag | 0x010E | Yes | No |
| eps.ov_flag | 0x010F | Yes | No |
| eps.pl_obc through eps.pl_aocs_wheels | 0x0110--0x0117 | Yes | No |
| eps.line_current_0 through _7 | 0x0118--0x011F | Yes | No |
| eps.bat_dod | 0x0120 | Yes | No |
| eps.bat_cycles | 0x0121 | Yes | No |
| eps.mppt_efficiency | 0x0122 | Yes | No |
| eps.sa_age_factor | 0x0123 | Yes | No |
| eps.sa_a_degradation | 0x0124 | Yes | No |
| eps.sa_b_degradation | 0x0125 | Yes | No |
| eps.sa_lifetime_hours | 0x0126 | Yes | No |
| eps.sep_timer_active | 0x0127 | Yes | No |
| eps.sep_timer_remaining | 0x0128 | Yes | No |
| eps.pdm_unsw_status | 0x0129 | Yes | No |
| eps.spacecraft_phase | 0x012A | Yes | No |
| eps.sa_px_current through sa_mz_current | 0x012B--0x0130 | Yes | No |

**CHANGE NEEDED in `configs/eosat1/subsystems/eps.yaml`:** Add all missing param_ids to maintain config-to-parameter traceability.

### 3.3 Missing Overcurrent Threshold Configuration

**GAP-EPS-003:** The EPS/TCS research document (Sec 3.6) defines per-line overcurrent thresholds. No overcurrent threshold configuration exists in `eps.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/eps.yaml`:**
```yaml
overcurrent_thresholds:
  obc: 2.0          # A (non-switchable, no trip)
  ttc_rx: 0.3       # A (non-switchable, no trip)
  ttc_tx: 1.0       # A
  payload: 2.5      # A
  fpa_cooler: 1.0   # A
  htr_bat: 0.5      # A
  htr_obc: 0.3      # A
  aocs_wheels: 0.8  # A
```

### 3.4 Missing Load Shed Sequence Configuration

**GAP-EPS-004:** The EPS/TCS research document (Sec 12) defines a four-step load shed sequence. This is not explicitly configured in `eps.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/eps.yaml`:**
```yaml
load_shed_sequence:
  - { step: 1, line: payload, index: 3 }
  - { step: 2, line: fpa_cooler, index: 4 }
  - { step: 3, line: ttc_tx, index: 2 }
  - { step: 4, line: aocs_wheels, index: 7 }
load_shed_voltage_threshold: 26.5
load_restore_voltage_threshold: 27.5
load_restore_soc_threshold: 40.0
```

### 3.5 Missing Bus Voltage Limits

**GAP-EPS-005:** No undervoltage/overvoltage thresholds are configured in `eps.yaml`. The EPS/TCS research document specifies UV at 26.5 V and OV at 29.5 V.

**CHANGE NEEDED in `configs/eosat1/subsystems/eps.yaml`:**
```yaml
bus:
  nominal_v: 28.0
  undervoltage_threshold_v: 26.5
  overvoltage_threshold_v: 29.5
  regulated: true
```

---

## 4. TCS Subsystem Configuration Gaps

**File examined:** `configs/eosat1/subsystems/tcs.yaml`

### 4.1 Missing TCS Phase 4 Parameters in Config

**GAP-TCS-001:** The following parameters exist in `parameters.yaml` but are NOT in `tcs.yaml` `param_ids`:

| Parameter | ID | Status |
|-----------|-----|--------|
| tcs.htr_thruster | 0x040D | In tcs.yaml but not in param_ids section -- it is in the zones definition |
| tcs.htr_duty_battery | 0x040E | Not in tcs.yaml param_ids |
| tcs.htr_duty_obc | 0x040F | Not in tcs.yaml param_ids |
| tcs.htr_duty_thruster | 0x0410 | Not in tcs.yaml param_ids |
| tcs.htr_total_power | 0x0411 | Not in tcs.yaml param_ids |

**CHANGE NEEDED in `configs/eosat1/subsystems/tcs.yaml`:** Add missing param_ids:
```yaml
param_ids:
  # ... existing entries ...
  htr_thruster: 0x040D
  htr_duty_battery: 0x040E
  htr_duty_obc: 0x040F
  htr_duty_thruster: 0x0410
  htr_total_power: 0x0411
```

### 4.2 Missing Internal Component Thermal Zones

**GAP-TCS-002:** `tcs.yaml` defines 6 panel zones but lacks explicit definitions for the 4 internal component zones (OBC, battery, FPA, thruster). These are implicitly modelled in the simulator but not configured.

**CHANGE NEEDED in `configs/eosat1/subsystems/tcs.yaml`:**
```yaml
internal_zones:
  - name: obc
    initial_temp_c: 25.0
    capacitance_j_per_c: 8000
    time_constant_s: 1200
  - name: battery
    initial_temp_c: 22.0
    capacitance_j_per_c: 10000
    time_constant_s: 1500
  - name: fpa
    initial_temp_c: 5.0
    capacitance_j_per_c: 2000
    time_constant_s: 100
  - name: thruster
    initial_temp_c: 20.0
    capacitance_j_per_c: 3000
    time_constant_s: 800
```

### 4.3 FPA Cooler Target Temperature Discrepancy

**GAP-TCS-003:** The FPA cooler target temperature is inconsistent across sources:

| Source | Value |
|--------|-------|
| `tcs.yaml` | -5.0 deg C |
| `payload.yaml` | -5.0 deg C |
| Payload manual (06_payload.md) | -15.0 deg C |
| Commissioning procedures | -30.0 deg C |
| Payload research doc Sec 2.3 | -5.0 (sim), -15.0 (manual), -30.0 (procedures) |

**CHANGE NEEDED:** Reconcile the FPA cooler target across all documents. If -15 deg C is the flight value, update both `tcs.yaml` and `payload.yaml`:
```yaml
fpa_cooler_target_c: -15.0
```

### 4.4 Missing Thruster Heater Power Line Mapping

**GAP-TCS-004:** The EPS/TCS research document (REQ-TH-001) notes that the thruster heater (8 W) does not have a dedicated EPS power line. The thruster heater is configured in `tcs.yaml` but there is no corresponding EPS power line for it.

**CHANGE NEEDED:** Either add a dedicated EPS power line for the thruster heater or document that the thruster heater draws from a shared bus and is not independently switchable.

---

## 5. TTC Subsystem Configuration Gaps

**File examined:** `configs/eosat1/subsystems/ttc.yaml`

### 5.1 Missing PDM Channel Configuration

**GAP-TTC-001 (HIGH):** The TTC research document (REQ-TTC-PDM-001 through PDM-006, REQ-TTC-CFG-002) requires PDM command channel configuration. None exists in `ttc.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/ttc.yaml`:**
```yaml
# PDM Command Channel
pdm_enabled: true
pdm_timer_duration_s: 900  # 15 minutes
pdm_commands:
  - switch_primary
  - switch_redundant
  - enable_pa
  - force_obc_reset
```

### 5.2 Missing Antenna Deployment Configuration

**GAP-TTC-002 (HIGH):** The TTC research document (REQ-TTC-BWD-001 through BWD-006, REQ-TTC-CFG-002) requires antenna deployment configuration. None exists in `ttc.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/ttc.yaml`:**
```yaml
# Antenna Deployment
antenna_deployed: false
antenna_stowed_gain_penalty_db: -8.0
burn_wire_current_a: 2.0
burn_wire_duration_s: 5.0
burn_wire_arm_timeout_s: 30.0
```

### 5.3 Missing Beacon Configuration

**GAP-TTC-003:** The TTC research document (REQ-TTC-BCN-001 through BCN-004) requires beacon configuration. None exists in `ttc.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/ttc.yaml`:**
```yaml
# Beacon
beacon_rate_bps: 1000
beacon_interval_s: 16.0
beacon_hk_sid: 10
```

### 5.4 Missing PA Thermal Configuration

**GAP-TTC-004:** The TTC research document specifies PA auto-shutdown at 70 deg C with 15 deg C hysteresis (re-enable at 55 deg C). These thresholds are not in `ttc.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/ttc.yaml`:**
```yaml
pa_shutdown_temp_c: 70.0
pa_reenable_temp_c: 55.0
pa_nominal_power_w: 2.0
pa_max_power_w: 5.0
```

### 5.5 Missing TTC Phase 4+ Parameters in Config

**GAP-TTC-005:** The following parameters exist in `parameters.yaml` but are NOT in `ttc.yaml` `param_ids`:

| Parameter | ID | Status |
|-----------|-----|--------|
| ttc.ber | 0x050C | Missing from ttc.yaml |
| ttc.tx_fwd_power | 0x050D | Missing |
| ttc.pa_temp | 0x050F | Missing |
| ttc.carrier_lock | 0x0510 | Missing |
| ttc.bit_sync | 0x0511 | Missing |
| ttc.frame_sync | 0x0512 | Missing |
| ttc.cmd_rx_count | 0x0513 | Missing |
| ttc.pa_on | 0x0516 | Missing |
| ttc.eb_n0 | 0x0519 | Missing |
| ttc.agc_level | 0x051A | Missing |
| ttc.doppler_hz | 0x051B | Missing |
| ttc.range_rate | 0x051C | Missing |
| ttc.cmd_auth_status | 0x051D | Missing |
| ttc.total_bytes_tx | 0x051E | Missing |
| ttc.total_bytes_rx | 0x051F | Missing |
| ttc.antenna_deployed | 0x0520 | Missing |
| ttc.beacon_mode | 0x0521 | Missing |
| ttc.cmd_decode_timer | 0x0522 | Missing |
| ttc.active_gs | 0x0523 | Missing |
| ttc.gs_equipment_status | 0x0524 | Missing |

**CHANGE NEEDED in `configs/eosat1/subsystems/ttc.yaml`:** Add all 20 missing param_ids.

### 5.6 Missing TTC Proposed New Parameters

**GAP-TTC-006:** The TTC research document (Sec 4.3, REQ-TTC-TM-001) proposes additional parameters not yet in `parameters.yaml`:

| Parameter | Proposed ID | Description |
|-----------|-------------|-------------|
| ttc.pdm_timer_active | 0x0520 | Conflicts with existing `ttc.antenna_deployed` |
| ttc.pdm_timer_remaining | 0x0521 | Conflicts with existing `ttc.beacon_mode` |
| ttc.burn_wire_armed | 0x0523 | Conflicts with existing `ttc.active_gs` |
| ttc.gs_id | 0x0525 | New -- currently tracked ground station |

**CHANGE NEEDED:** The research document proposed IDs conflict with already-allocated IDs. Either:
- (a) Reallocate the proposed parameters to unused IDs (0x0525+), or
- (b) Verify that `ttc.antenna_deployed` (0x0520) already covers the antenna deployment status requirement, and `ttc.cmd_decode_timer` (0x0522) already covers the PDM timer requirement.

The existing parameters partially cover the requirement but miss `ttc.pdm_timer_active`, `ttc.burn_wire_armed`, and `ttc.gs_id`. Add:
```yaml
# In parameters.yaml
- { id: 0x0525, name: ttc.pdm_timer_active, subsystem: ttc, description: "PDM 15-min TX timer active (0/1)" }
- { id: 0x0526, name: ttc.burn_wire_armed, subsystem: ttc, description: "Burn wire arm status (0=safe, 1=armed)" }
- { id: 0x0527, name: ttc.gs_id, subsystem: ttc, description: "Currently tracked ground station ID" }
```

### 5.7 Missing TTC Limit Definitions

**GAP-TTC-007:** The TTC research document (Sec 4.4) defines limit monitoring thresholds for TTC parameters. These should be added to the limits configuration.

| Parameter | Yellow Low | Yellow High | Red Low | Red High |
|-----------|-----------|-------------|---------|----------|
| ttc.ber | -- | -5.0 | -- | -4.0 |
| ttc.pa_temp | 0.0 | 55.0 | -10.0 | 65.0 |
| ttc.xpdr_temp | 0.0 | 50.0 | -10.0 | 60.0 |
| ttc.agc_level | -80.0 | -- | -100.0 | -20.0 |
| ttc.link_margin | 3.0 | -- | 1.0 | -- |

**CHANGE NEEDED in limits configuration.**

### 5.8 Missing TTC Commands (Antenna Deployment)

**GAP-TTC-008 (HIGH):** The TTC research document (REQ-TTC-CMD-001) requires 4 new commands for antenna deployment and PDM reset (func_ids 56--59). These are not in the TC catalog.

| Command | func_id | Description |
|---------|---------|-------------|
| TTC_DEPLOY_ANTENNA_ARM | 56 | Arm burn wire deployment |
| TTC_DEPLOY_ANTENNA_FIRE | 57 | Fire burn wire (must be armed) |
| TTC_DEPLOY_ANTENNA_DISARM | 58 | Disarm burn wire |
| TTC_PDM_RESET | 59 | Reset PDM timer (commissioning) |

**CHANGE NEEDED in TC catalog configuration.**

---

## 6. AOCS Subsystem Configuration Gaps

**File examined:** `configs/eosat1/subsystems/aocs.yaml`

### 6.1 Incomplete Mode List

**GAP-AOCS-001:** The AOCS research document defines 9 modes (OFF=0, SAFE_BOOT=1, DETUMBLE=2, COARSE_SUN=3, NOMINAL=4, FINE_POINT=5, SLEW=6, DESAT=7, ECLIPSE=8). The `aocs.yaml` lists only 5 modes: `[nominal, detumble, safe, wheel_desat, slew]`.

**CHANGE NEEDED in `configs/eosat1/subsystems/aocs.yaml`:**
```yaml
modes: [off, safe_boot, detumble, coarse_sun, nominal, fine_point, slew, desat, eclipse]
```

### 6.2 Missing AOCS Phase 4 Parameters in Config

**GAP-AOCS-002:** The following parameters exist in `parameters.yaml` but are NOT in `aocs.yaml` `param_ids`:

| Parameter | ID | Status |
|-----------|-----|--------|
| aocs.st1_status | 0x0240 | Missing |
| aocs.st1_num_stars | 0x0241 | Missing |
| aocs.st2_status | 0x0243 | Missing |
| aocs.css_sun_x/y/z | 0x0245--0x0247 | Missing |
| aocs.css_valid | 0x0248 | Missing |
| aocs.rw1--4_current | 0x0250--0x0253 | Missing |
| aocs.rw1--4_enabled | 0x0254--0x0257 | Missing |
| aocs.mtq_x/y/z_duty | 0x0258--0x025A | Missing |
| aocs.total_momentum | 0x025B | Missing |
| aocs.submode | 0x0262 | Missing |
| aocs.time_in_mode | 0x0264 | Missing |
| aocs.gyro_bias_x/y/z | 0x0270--0x0272 | Missing |
| aocs.gyro_temp | 0x0273 | Missing |
| aocs.gps_fix | 0x0274 | Missing |
| aocs.gps_pdop | 0x0275 | Missing |
| aocs.gps_num_sats | 0x0276 | Missing |
| aocs.mag_field_total | 0x0277 | Missing |

**CHANGE NEEDED in `configs/eosat1/subsystems/aocs.yaml`:** Add all 30+ missing param_ids.

### 6.3 Missing Dual Magnetometer Configuration

**GAP-AOCS-003:** The AOCS research document (REQ-SIM-001) specifies dual redundant magnetometers (MAG-A and MAG-B) with independent failure injection. No dual magnetometer configuration exists in `aocs.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/aocs.yaml`:**
```yaml
magnetometers:
  - name: MAG-A
    role: primary
    mounting: boom
  - name: MAG-B
    role: redundant
    mounting: boom
```

### 6.4 Missing Star Camera Configuration

**GAP-AOCS-004:** The AOCS research document specifies dual cold-redundant star cameras (ST1 zenith, ST2 nadir) with 60 s boot time. No star camera configuration exists in `aocs.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/aocs.yaml`:**
```yaml
star_cameras:
  - name: ST1
    mounting: zenith_pz
    boot_time_s: 60
    redundancy: cold
    default_state: tracking
  - name: ST2
    mounting: nadir_mz
    boot_time_s: 60
    redundancy: cold
    default_state: off
```

### 6.5 Missing CSS Configuration

**GAP-AOCS-005:** The AOCS research document specifies 6 individual CSS heads (one per face). No CSS configuration exists in `aocs.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/aocs.yaml`:**
```yaml
css_heads: 6
css_faces: [px, mx, py, my, pz, mz]
```

### 6.6 Missing GPS Configuration

**GAP-AOCS-006:** The AOCS research document specifies a GPS receiver with fix types, PDOP limits, and satellite count. No GPS configuration exists in `aocs.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/aocs.yaml`:**
```yaml
gps:
  antenna_face: pz  # zenith
  nominal_fix: 3    # 3D + velocity
  pdop_yellow: 4.0
  pdop_red: 6.0
```

### 6.7 Missing Mode Transition Configuration

**GAP-AOCS-007:** The AOCS research document (Sec 3.3) defines detailed transition guards and dwell times for each mode transition. None of this is in `aocs.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/aocs.yaml`:**
```yaml
mode_transitions:
  safe_boot_to_detumble:
    guard: time_in_mode >= 30
    min_dwell_s: 5
  detumble_to_coarse_sun:
    guard: rate_magnitude < 0.5 for 30s
    min_dwell_s: 10
  coarse_sun_to_nominal:
    guard: css_valid AND att_error < 10 for 60s AND st_valid
    min_dwell_s: 20
  emergency_rate_threshold_deg_s: 2.0
```

### 6.8 Missing Momentum Budget Configuration

**GAP-AOCS-008:** No desaturation target or momentum limit configuration exists.

**CHANGE NEEDED in `configs/eosat1/subsystems/aocs.yaml`:**
```yaml
momentum:
  yellow_limit_nms: 0.5
  red_limit_nms: 0.8
  desaturation_target_rpm: 200
```

### 6.9 NOM-004 Orbit Maintenance Inapplicability

**GAP-AOCS-009:** The AOCS research document (Sec 5.4.2) notes that EOSAT-1 has no propulsion system. However, `procedure_index.yaml` lists NOM-004 "Orbit Maintenance" with AOCS role "Plan and execute orbit maneuver" and command services `[8, 11]`. The procedure and activity type reference thruster commands that do not apply.

**CHANGE NEEDED in `configs/eosat1/procedures/procedure_index.yaml`:** Update NOM-004:
```yaml
  - id: NOM-004
    name: "Orbit Monitoring"
    position_roles:
      aocs: "Monitor orbit parameters, validate GPS solution, update ephemeris"
    command_services: [3, 20]  # Remove S8/S11 thruster commands
```

---

## 7. OBDH Subsystem Configuration Gaps

**File examined:** `configs/eosat1/subsystems/obdh.yaml`

### 7.1 Missing Dual OBC Configuration

**GAP-OBDH-001:** The OBDH research document specifies cold-redundant dual OBCs (A/B) with detailed state machine. The `obdh.yaml` contains only basic mode list and param_ids. Missing configuration:

**CHANGE NEEDED in `configs/eosat1/subsystems/obdh.yaml`:**
```yaml
dual_obc:
  active: A
  redundancy: cold
  switchover_type: cold  # no state transfer
  boot_crc_check_time_s: 10

bootloader:
  hk_sid: 10
  hk_interval_s: 16
  cpu_baseline_pct: 15.0
  task_count: 4

application:
  nominal_cpu_pct: 35.0
  safe_cpu_pct: 25.0
  maintenance_cpu_pct: 55.0
  nominal_task_count: 15
  safe_task_count: 12
```

### 7.2 Missing OBDH Phase 4 Parameters in Config

**GAP-OBDH-002:** The following parameters exist in `parameters.yaml` but are NOT in `obdh.yaml` `param_ids`:

| Parameter | ID |
|-----------|-----|
| obdh.active_obc | 0x030C |
| obdh.obc_b_status | 0x030D |
| obdh.active_bus | 0x030E |
| obdh.bus_a_status | 0x030F |
| obdh.bus_b_status | 0x0310 |
| obdh.sw_image | 0x0311 |
| obdh.hktm_buf_fill | 0x0312 |
| obdh.event_buf_fill | 0x0313 |
| obdh.alarm_buf_fill | 0x0314 |
| obdh.last_reboot_cause | 0x0316 |
| obdh.boot_count_a | 0x0317 |
| obdh.boot_count_b | 0x0318 |
| obdh.seu_count | 0x0319 |
| obdh.scrub_progress | 0x031A |
| obdh.task_count | 0x031B |
| obdh.stack_usage | 0x031C |
| obdh.heap_usage | 0x031D |
| obdh.mem_errors | 0x031E |

**CHANGE NEEDED in `configs/eosat1/subsystems/obdh.yaml`:** Add all 18 missing param_ids.

### 7.3 Missing CAN Bus Configuration

**GAP-OBDH-003:** The OBDH research document (REQ-OBDH-015 through 019) specifies dual CAN buses with subsystem equipment mapping. No bus configuration exists in `obdh.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/obdh.yaml`:**
```yaml
bus_mapping:
  bus_a: [eps, tcs, aocs]
  bus_b: [ttc, payload]
```

### 7.4 Missing Buffer Configuration

**GAP-OBDH-004:** The OBDH research document (REQ-OBDH-020 through 023) specifies buffer capacities. No buffer configuration exists in `obdh.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/obdh.yaml`:**
```yaml
buffers:
  hktm: { capacity: 1000, type: circular }
  event: { capacity: 500, type: linear_stop_when_full }
  alarm: { capacity: 200, type: linear_stop_when_full }
```

### 7.5 Missing Reboot Cause Enumeration

**GAP-OBDH-005:** The `parameters.yaml` documents reboot causes as `(0=none,1=watchdog,2=memory,3=switchover,4=commanded)` but the OBDH research document uses `(0=NONE,1=COMMAND,2=WATCHDOG,3=MEMORY_ERROR,4=SWITCHOVER)`. The enumeration order is inconsistent.

**CHANGE NEEDED:** Reconcile reboot cause codes between `parameters.yaml` and the research document.

---

## 8. Payload Subsystem Configuration Gaps

**File examined:** `configs/eosat1/subsystems/payload.yaml`

### 8.1 Missing Payload Phase 4 Parameters in Config

**GAP-PLD-001:** The following parameters exist in `parameters.yaml` but are NOT in `payload.yaml` `param_ids`:

| Parameter | ID |
|-----------|-----|
| payload.mem_total_mb | 0x060A |
| payload.mem_used_mb | 0x060B |
| payload.last_scene_id | 0x060C |
| payload.last_scene_quality | 0x060D |
| payload.fpa_ready | 0x0610 |
| payload.mem_segments_bad | 0x0612 |
| payload.duty_cycle_pct | 0x0613 |
| payload.compression_ratio | 0x0614 |
| payload.cal_lamp_on | 0x0615 |
| payload.snr | 0x0616 |
| payload.detector_temp | 0x0617 |
| payload.integration_time | 0x0618 |
| payload.swath_width_km | 0x0619 |

**CHANGE NEEDED in `configs/eosat1/subsystems/payload.yaml`:** Add all 13 missing param_ids.

### 8.2 Missing Spectral Band Configuration

**GAP-PLD-002:** The payload research document (Sec 2.2) defines four spectral bands (Blue 490 nm, Green 560 nm, Red 665 nm, NIR 842 nm) with detailed ocean color applications. No band configuration exists in `payload.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/payload.yaml`:**
```yaml
spectral_bands:
  - { name: Blue, center_nm: 490, bandwidth_nm: 65, snr_factor: 0.8 }
  - { name: Green, center_nm: 560, bandwidth_nm: 35, snr_factor: 1.0 }
  - { name: Red, center_nm: 665, bandwidth_nm: 30, snr_factor: 0.9 }
  - { name: NIR, center_nm: 842, bandwidth_nm: 115, snr_factor: 0.7 }
```

### 8.3 Missing Memory Segment Configuration

**GAP-PLD-003:** The payload research document specifies 8 memory segments of 2500 MB each. No segment configuration exists in `payload.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/payload.yaml`:**
```yaml
memory_segments: 8
segment_size_mb: 2500.0
```

### 8.4 Data Rate and Storage Discrepancies

**GAP-PLD-004:** Multiple values are inconsistent:

| Parameter | payload.yaml | Manual (06_payload.md) | Research Doc |
|-----------|-------------|----------------------|--------------|
| Data rate | 80 Mbps | ~40 Mbps | 80 Mbps (sim), ~40 Mbps (manual) |
| Total storage | 20,000 MB | 2 GB | 20 GB (sim), 2 GB (manual) |
| Image size | 800 MB | 50--200 MB | 800 MB (sim), 50--200 MB (manual) |
| Swath width | Not in config | 60 km | 30 km (sim default), 60 km (manual) |

**CHANGE NEEDED:** Document these as known simulation simplifications, or reconcile values. At minimum, add swath width to `payload.yaml`:
```yaml
swath_width_km: 60.0  # nominal ground swath at 450 km altitude
```

### 8.5 Missing Compression Ratio Configuration

**GAP-PLD-005:** The payload research document specifies a configurable compression ratio (4:1 lossy nominal, 2:1 in simulator). No default compression ratio is in `payload.yaml`.

**CHANGE NEEDED in `configs/eosat1/subsystems/payload.yaml`:**
```yaml
compression_ratio: 2.0  # simulator default; flight target is 4.0
```

---

## 9. FDIR Configuration Gaps

**File examined:** `configs/eosat1/subsystems/fdir.yaml`

### 9.1 Missing FPA Over-Temperature FDIR Rule

**GAP-FDIR-001 (HIGH):** The payload research document (REQ-PLD-FDIR-002) identifies that no FPA over-temperature FDIR rule exists. The current FDIR rules only shed payload load on low battery SoC. The following rules are needed:

**CHANGE NEEDED in `configs/eosat1/subsystems/fdir.yaml`:**
```yaml
  - parameter: payload.fpa_temp
    condition: "> 12"
    threshold: 12.0
    level: 1
    action: payload_poweroff
  - parameter: payload.fpa_temp
    condition: "> 25"
    threshold: 25.0
    level: 2
    action: payload_emergency_off
```

### 9.2 Missing Storage Overflow FDIR Rule

**GAP-FDIR-002:** The payload research document suggests a storage overflow prevention rule.

**CHANGE NEEDED in `configs/eosat1/subsystems/fdir.yaml`:**
```yaml
  - parameter: payload.store_used
    condition: "> 98"
    threshold: 98.0
    level: 1
    action: payload_imaging_inhibit
```

### 9.3 Missing TTC FDIR Rules

**GAP-FDIR-003:** The TTC research document (Sec 7) defines two TTC-related FDIR rules that are not in `fdir.yaml`:

| Rule | Condition | Action |
|------|-----------|--------|
| TTC-01 | No link for > 24 hours | Switch to redundant XPDR |
| TTC-02 | XPDR temp out of range (0--50 deg C) | Switch transponder |

**CHANGE NEEDED in `configs/eosat1/subsystems/fdir.yaml`:**
```yaml
  - parameter: ttc.xpdr_temp
    condition: "> 50"
    threshold: 50.0
    level: 1
    action: switch_transponder
  - parameter: ttc.xpdr_temp
    condition: "< 0"
    threshold: 0.0
    level: 1
    action: switch_transponder
```

Note: The 24-hour no-link rule requires a timer-based implementation, not a simple parameter threshold. This is a simulator enhancement requirement.

### 9.4 Known `obdh.temp_obc` vs `obdh.temp` Issue

**GAP-FDIR-004 (PRE-EXISTING):** `fdir.yaml` rule OBDH-01 references `obdh.temp_obc` but the actual parameter is `obdh.temp` (0x0301). The config currently uses the correct parameter name `obdh.temp` (line 37 of fdir.yaml), so this known xfail issue appears to have been already resolved in the config. However, the xfail test may still reference the old name.

---

## 10. Telemetry Parameter Gaps

**File examined:** `configs/eosat1/telemetry/parameters.yaml`

### 10.1 Missing AOCS GPS Velocity Parameters (Research Doc)

The AOCS research document lists GPS velocity components (0x0213 `gps_vx`, 0x0214 `gps_vy`, 0x0215 `gps_vz`). These ARE present in `parameters.yaml`. No gap.

### 10.2 Missing TTC Parameters for Research Requirements

**GAP-TM-001:** The following TTC parameters proposed in the research document are not yet in `parameters.yaml`:

| Parameter | Proposed ID | Description |
|-----------|-------------|-------------|
| ttc.pdm_timer_active | 0x0525 | PDM 15-min TX timer active flag |
| ttc.burn_wire_armed | 0x0526 | Burn wire arm status |
| ttc.gs_id | 0x0527 | Currently tracked ground station ID |

**CHANGE NEEDED in `configs/eosat1/telemetry/parameters.yaml`:** Add 3 new parameters (see GAP-TTC-006).

### 10.3 Missing Contact Azimuth Parameter

**GAP-TM-002:** The `ttc.yaml` param_ids reference `contact_az: 0x050B` but this parameter is NOT in `parameters.yaml`. The TTC research document (Sec 4.1) lists it as `ttc.contact_az`.

**CHANGE NEEDED in `configs/eosat1/telemetry/parameters.yaml`:**
```yaml
  - { id: 0x050B, name: ttc.contact_az, subsystem: ttc, units: deg, description: "Ground station azimuth angle" }
```

---

## 11. HK Structure Gaps

**File examined:** `configs/eosat1/telemetry/hk_structures.yaml`

### 11.1 SID 6 (TTC) Missing Phase 4 Parameters

**GAP-HK-001:** The TTC HK structure (SID 6) does not include the following Phase 4 parameters that are generated by the simulator:

| Parameter | ID | Status in SID 6 |
|-----------|-----|-----------------|
| ttc.total_bytes_tx | 0x051E | Missing |
| ttc.total_bytes_rx | 0x051F | Missing |
| ttc.antenna_deployed | 0x0520 | Missing |
| ttc.beacon_mode | 0x0521 | Missing |
| ttc.cmd_decode_timer | 0x0522 | Missing |
| ttc.active_gs | 0x0523 | Missing |

**CHANGE NEEDED in `configs/eosat1/telemetry/hk_structures.yaml`:** Add these to SID 6 parameters.

### 11.2 SID 1 (EPS) Missing Per-Panel Solar Currents

**GAP-HK-002:** The EPS HK structure (SID 1) does not include the 6 per-panel solar current parameters (0x012B--0x0130), separation timer params (0x0127--0x0128), or spacecraft phase (0x012A).

**CHANGE NEEDED:** Either add to SID 1 or create a new SID for extended EPS data.

### 11.3 SID 5 (Payload) Missing Parameters

**GAP-HK-003:** The Payload HK structure (SID 5) does not include `payload.integration_time` (0x0618) or `payload.swath_width_km` (0x0619).

**CHANGE NEEDED in `configs/eosat1/telemetry/hk_structures.yaml`:** Add to SID 5:
```yaml
      - { param_id: 0x0618, pack_format: H, scale: 100 }
      - { param_id: 0x0619, pack_format: H, scale: 10 }
```

### 11.4 SID 4 (Platform) Missing Parameters

**GAP-HK-004:** SID 4 does not include `obdh.boot_count_a` (0x0317) or `obdh.boot_count_b` (0x0318) despite these being defined as SID 4 parameters in the OBDH research document.

**CHANGE NEEDED in `configs/eosat1/telemetry/hk_structures.yaml`:** Add to SID 4:
```yaml
      - { param_id: 0x0317, pack_format: H, scale: 1 }
      - { param_id: 0x0318, pack_format: H, scale: 1 }
```

### 11.5 Known SID 6 Parameter 0x0508 Issue

**GAP-HK-005 (PRE-EXISTING):** SID 6 references param_id 0x0508 (`ttc.ranging_status`). This parameter now exists in `parameters.yaml` (added since the xfail was documented). The xfail test should be re-evaluated.

---

## 12. Procedure Gaps

**File examined:** `configs/eosat1/procedures/procedure_index.yaml` (54 procedures)

### 12.1 Missing Procedures Identified by Research Documents

The following procedures are required by the research documents but do not exist in the procedure index:

| ID | Name | Source | Category | Priority |
|----|------|--------|----------|----------|
| **LEOP-008** | Antenna Deployment | TTC research (REQ-TTC-ANT-001) | LEOP | HIGH |
| **NOM-013** | Pass Closure | FD research (Sec 4.2) | nominal | MEDIUM |
| **NOM-014** | Power Budget Review | EPS/TCS research (REQ-PROC-005) | nominal | HIGH |
| **NOM-015** | Eclipse Entry/Exit Checklist | EPS/TCS research (REQ-PROC-006) | nominal | HIGH |
| **NOM-016** | Calibration Lamp Acquisition | Payload research (REQ-PLD-CAL-001) | nominal | MEDIUM |
| **NOM-017** | Transponder Switchover | TTC research (REQ-TTC-SW-001) | nominal | MEDIUM |
| **CTG-019** | Ground Station Failure | TTC research (REQ-TTC-GSF-001) | contingency | MEDIUM |
| **CTG-020** | Antenna Deploy Failure Recovery | TTC research (REQ-TTC-ANT-003) | contingency | HIGH |
| **CTG-021** | PDM Channel Recovery | TTC research (REQ-TTC-EMG-001) | contingency | HIGH |
| **CTG-022** | Magnetometer Switchover | AOCS research (Sec 6.2.2) | contingency | MEDIUM |
| **CTG-023** | GPS Anomaly Response | AOCS research (Sec 6.2.5) | contingency | LOW |
| **CTG-024** | Multi-Wheel Failure | AOCS research (Sec 6.2.4) | contingency | HIGH |
| **CTG-025** | Eclipse ST Blinding Recovery | AOCS research (Sec 6.2.3) | contingency | MEDIUM |
| **EMG-007** | Boot Loop Arrest | OBDH research (Sec 10.8) | emergency | HIGH |

Total: **14 new procedures needed**.

### 12.2 Procedure Content Gaps in Existing Procedures

| Procedure | Gap | Source |
|-----------|-----|--------|
| NOM-004 | References orbit maneuver/thruster commands that EOSAT-1 does not have | AOCS research Sec 5.4.2 |
| CTG-001 | Needs explicit bus voltage targets per load shed step | EPS/TCS research REQ-PROC-007 |
| CTG-012 | Needs root cause analysis step before OC flag reset | EPS/TCS research REQ-PROC-008 |
| COM-001 | Needs power budget reconciliation step | EPS/TCS research REQ-PROC-003 |
| COM-002 | Needs battery heater thermostat hysteresis verification step | EPS/TCS research REQ-PROC-004 |
| LEOP-001 | Needs antenna deployment sub-sequence post health check | TTC research Sec 6.2 |
| COM-006 | Should include antenna deploy verification as part of TTC link commissioning | TTC research REQ-TTC-ANT-001 |

---

## 13. Scenario Gaps

**Files examined:** All 15 files in `configs/eosat1/scenarios/`

### 13.1 Existing Scenarios (15 total)

| File | Subsystem | Difficulty |
|------|-----------|------------|
| nominal_ops.yaml | -- | BASIC |
| eps_anomaly.yaml | EPS | INTERMEDIATE |
| eps_overcurrent.yaml | EPS | INTERMEDIATE |
| eps_undervoltage.yaml | EPS | INTERMEDIATE |
| fpa_overtemp.yaml | Payload | INTERMEDIATE |
| rw_bearing.yaml | AOCS | ADVANCED |
| obc_watchdog.yaml | OBDH | INTERMEDIATE |
| ttc_pa_overheat.yaml | TTC | INTERMEDIATE |
| transponder_failure.yaml | TTC | ADVANCED |
| aocs_wheel_failure.yaml | AOCS | ADVANCED |
| aocs_star_tracker_failure.yaml | AOCS | ADVANCED |
| obc_crash.yaml | OBDH | ADVANCED |
| obc_bus_failure.yaml | OBDH | ADVANCED |
| payload_memory_failure.yaml | Payload | INTERMEDIATE |
| payload_corrupt_image.yaml | Payload | BASIC |

### 13.2 Missing Scenarios Identified by Research Documents

The research documents collectively identify the following missing training scenarios. These are organized by priority and mapped to the requesting research document:

#### HIGH Priority

| Scenario ID | Name | Subsystem | Difficulty | Source |
|-------------|------|-----------|------------|--------|
| **SCN-LEOP-001** | LEOP First AOS Nominal | TTC | ADVANCED | TTC Sec 8.1 (TRN-TTC-001) |
| **SCN-LEOP-002** | LEOP Delayed AOS | TTC | ADVANCED | TTC Sec 8.1 (TRN-TTC-002) |
| **SCN-LEOP-003** | LEOP Bootloader Beacon Only | TTC/OBDH | ADVANCED | TTC Sec 8.1 (TRN-TTC-003) |
| **SCN-LEOP-004** | LEOP Antenna Deploy Failure | TTC | ADVANCED | TTC Sec 8.1 (TRN-TTC-004) |
| **SCN-LEOP-005** | LEOP Low Battery | TTC/EPS | ADVANCED | TTC Sec 8.1 (TRN-TTC-005) |
| **SCN-LEOP-006** | LEOP Detumble and Sun Acquisition | AOCS | BEGINNER | AOCS Sec 6.2.1 |
| **SCN-LEOP-007** | Full LEOP Sequence | Multi | ADVANCED | FD Sec 5.2 |
| **SCN-EPS-001** | Eclipse Entry with Low SoC | EPS | INTERMEDIATE | EPS/TCS Sec 5.1 (TRN-EPS-001) |
| **SCN-EPS-002** | Battery Cell Failure | EPS | ADVANCED | EPS/TCS Sec 5.1 (TRN-EPS-003) |
| **SCN-EPS-003** | Bus Short Circuit | EPS | ADVANCED | EPS/TCS Sec 5.1 (TRN-EPS-004) |
| **SCN-TCS-001** | Battery Heater Failure | TCS | INTERMEDIATE | EPS/TCS Sec 5.1 (TRN-TCS-001) |
| **SCN-TCS-002** | Heater Stuck-On | TCS | INTERMEDIATE | EPS/TCS Sec 5.1 (TRN-TCS-002) |
| **SCN-TCS-003** | FPA Cooler Failure (EPS/TCS view) | TCS | INTERMEDIATE | EPS/TCS Sec 5.1 (TRN-TCS-004) |
| **SCN-OBDH-001** | Bootloader Stuck Recovery | OBDH | ADVANCED | OBDH Sec 10.1 |
| **SCN-OBDH-002** | Memory Corruption / SEU Storm | OBDH | ADVANCED | OBDH Sec 10.4 |
| **SCN-OBDH-003** | OBC Switchover Under Failure | OBDH | ADVANCED | OBDH Sec 10.5 |
| **SCN-OBDH-004** | Boot Loop Detection | OBDH | ADVANCED | OBDH Sec 10.8 |
| **SCN-FD-001** | Multi-Failure Cascade | Multi | ADVANCED | FD Sec 5.2 |
| **SCN-FD-002** | Loss of Communication (Both XPDR) | TTC | ADVANCED | FD Sec 5.2 |
| **SCN-FD-003** | Total Power Failure Recovery | EPS | ADVANCED | FD Sec 5.2 |

#### MEDIUM Priority

| Scenario ID | Name | Subsystem | Difficulty | Source |
|-------------|------|-----------|------------|--------|
| **SCN-TTC-001** | Standard Data Pass | TTC | BASIC | TTC Sec 8.2 (TRN-TTC-010) |
| **SCN-TTC-002** | Rate Margin Limited Pass | TTC | INTERMEDIATE | TTC Sec 8.2 (TRN-TTC-011) |
| **SCN-TTC-003** | PA Thermal Management | TTC | INTERMEDIATE | TTC Sec 8.2 (TRN-TTC-012) |
| **SCN-TTC-004** | BER Degradation | TTC | INTERMEDIATE | TTC Sec 8.3 (TRN-TTC-021) |
| **SCN-TTC-005** | Uplink Loss | TTC | INTERMEDIATE | TTC Sec 8.3 (TRN-TTC-022) |
| **SCN-TTC-006** | GS Failure During Campaign | TTC | ADVANCED | TTC Sec 8.3 (TRN-TTC-025) |
| **SCN-TTC-007** | PDM Recovery | TTC/OBDH | ADVANCED | TTC Sec 8.4 (TRN-TTC-032) |
| **SCN-AOCS-001** | Dual Magnetometer Switchover | AOCS | INTERMEDIATE | AOCS Sec 6.2.2 |
| **SCN-AOCS-002** | Eclipse ST Blinding | AOCS | INTERMEDIATE | AOCS Sec 6.2.3 |
| **SCN-AOCS-003** | Multi-Wheel Failure | AOCS | ADVANCED | AOCS Sec 6.2.4 |
| **SCN-AOCS-004** | GPS Loss | AOCS | INTERMEDIATE | AOCS Sec 6.2.5 |
| **SCN-AOCS-005** | Momentum Saturation Emergency | AOCS | INTERMEDIATE | AOCS Sec 6.2.6 |
| **SCN-EPS-004** | Temperature Sensor Drift | TCS | INTERMEDIATE | EPS/TCS Sec 5.1 (TRN-TCS-003) |
| **SCN-EPS-005** | OBC Thermal Runaway | TCS/OBDH | ADVANCED | EPS/TCS Sec 5.1 (TRN-TCS-005) |
| **SCN-COMBO-001** | Eclipse + SA Degradation | EPS | ADVANCED | EPS/TCS Sec 5.1 (TRN-COMBO-001) |
| **SCN-COMBO-002** | Load Shed Under Thermal Stress | EPS/TCS | ADVANCED | EPS/TCS Sec 5.1 (TRN-COMBO-002) |
| **SCN-OBDH-005** | Dual Bus Failure | OBDH | ADVANCED | OBDH Sec 10.6 |
| **SCN-OBDH-006** | FDIR Configuration Verification | OBDH | BASIC | OBDH Sec 10.7 |
| **SCN-FD-004** | Shift Handover with Active Anomaly | Multi | INTERMEDIATE | FD Sec 5.2 |
| **SCN-FD-005** | Ground Station Priority Conflict | Multi | INTERMEDIATE | FD Sec 5.2 |

#### LOW Priority

| Scenario ID | Name | Subsystem | Difficulty | Source |
|-------------|------|-----------|------------|--------|
| **SCN-TTC-008** | Receiver Degradation | TTC | INTERMEDIATE | TTC Sec 8.3 (TRN-TTC-023) |
| **SCN-TTC-009** | Single Station Emergency | TTC | ADVANCED | TTC Sec 8.4 (TRN-TTC-031) |
| **SCN-PLD-001** | Nominal Ocean Color Imaging | Payload | BASIC | Payload Sec 12.2 (NOM-PLD-01) |
| **SCN-PLD-002** | Cooler Fails During Commissioning | Payload | INTERMEDIATE | Payload Sec 12.1 (COMM-PLD-02) |
| **SCN-PLD-003** | CCD Line Dropout | Payload | INTERMEDIATE | Payload Sec 12.3 (CTG-PLD-04) |
| **SCN-FD-006** | Software Upload Failure | OBDH | INTERMEDIATE | FD Sec 5.2 |
| **SCN-FD-007** | Thermal Runaway Multi-Position | Multi | ADVANCED | FD Sec 5.2 |

Total: **47 new scenarios needed** (20 HIGH, 20 MEDIUM, 7 LOW).

---

## 14. Known Configuration Issues (Pre-Existing)

These issues were already documented in the project memory and are included here for completeness:

| Issue | Location | Status |
|-------|----------|--------|
| SID 6 references param_id 0x0508 not in parameters.yaml | hk_structures.yaml | **Resolved** -- 0x0508 (`ttc.ranging_status`) now exists in parameters.yaml. Re-evaluate xfail. |
| fdir.yaml references `obdh.temp_obc` | fdir.yaml | **Resolved** -- fdir.yaml line 37 now uses `obdh.temp`. Re-evaluate xfail. |
| displays.yaml references stale param names | displays.yaml | **Open** -- `eps.pl_current_*`, `tcs.htr_thruster`, `ttc.data_rate_bps`, `ttc.elevation_deg` still need updating. |
| Thermostat setpoint discrepancy | tcs.yaml vs manual 03_tcs.md | **Open** -- tcs.yaml uses ON 1/OFF 5 for battery; manual says ON 5/OFF 10. |

---

## 15. Summary Statistics

### Config Changes Required

| Category | Count |
|----------|-------|
| Subsystem YAML config changes | 35 |
| New parameters in parameters.yaml | 4 |
| HK structure additions | 9 parameters across 3 SIDs |
| FDIR rule additions | 5 new rules |
| Parameter ID registrations in subsystem configs | 80+ missing entries |
| Limit definition additions | 5 TTC limits |
| TC catalog additions | 4 new commands (func_ids 56--59) |
| Mission.yaml corrections | 1 (band wavelengths) |

### Procedures Required

| Category | Existing | New Needed | Total |
|----------|---------|-----------|-------|
| LEOP | 7 | 1 | 8 |
| Commissioning | 12 | 0 | 12 |
| Nominal | 12 | 5 | 17 |
| Contingency | 18 | 7 | 25 |
| Emergency | 6 | 1 | 7 |
| **Total** | **55** | **14** | **69** |

### Scenarios Required

| Priority | Count |
|----------|-------|
| HIGH | 20 |
| MEDIUM | 20 |
| LOW | 7 |
| **Total New** | **47** |
| Existing | 15 |
| **Grand Total** | **62** |

### Top 10 Highest Priority Gaps

| Rank | Gap ID | Description | Impact |
|------|--------|-------------|--------|
| 1 | GAP-TTC-002 | No antenna deployment configuration | Cannot train LEOP antenna deploy |
| 2 | GAP-TTC-001 | No PDM command channel configuration | Cannot train last-resort recovery |
| 3 | GAP-EPS-001 | Two-wing vs. six-panel solar model | Power generation not attitude-coupled |
| 4 | GAP-FDIR-001 | No FPA over-temperature FDIR rule | No autonomous payload protection |
| 5 | GAP-TTC-008 | Missing antenna deploy commands | No func_ids for burn wire sequence |
| 6 | GAP-AOCS-001 | Incomplete AOCS mode list (5 vs 9) | Missing SAFE_BOOT, COARSE_SUN, FINE_POINT, ECLIPSE modes |
| 7 | GAP-OBDH-001 | No dual OBC configuration | Switchover fidelity limited |
| 8 | GAP-OBDH-003 | No CAN bus mapping configuration | Bus failure isolation not configurable |
| 9 | GAP-FDIR-003 | No TTC-related FDIR rules | No autonomous transponder recovery |
| 10 | GAP-AOCS-009 | NOM-004 references nonexistent propulsion | Misleading procedure content |

---

![AIG - Artificial Intelligence Generated](../../Screenshot%202026-03-10%20at%202.31.22%20PM.png)

*This document was generated with AI assistance.*
*AIG logo source: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

---

*End of Document -- EOSAT1-GAP-001*

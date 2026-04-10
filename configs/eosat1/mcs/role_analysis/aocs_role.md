# Flight Dynamics (aocs) -- Role Analysis

**Position ID:** `aocs`
**Display Name:** Flight Dynamics
**Subsystems:** aocs
**Allowed PUS Services:** 1, 3, 5, 8, 11, 17, 20
**Allowed func_ids:** 0, 1, 2, 3, 4, 5, 6, 7, 8, 9
**Visible Tabs:** overview, aocs, commanding, procedures, manual
**Manual Sections:** 02_aocs, 07_orbit_ops

## 1. Mission Lifecycle Phases and Applicable Procedures

### LEOP

| Procedure | ID | aocs Role |
|---|---|---|
| Initial Orbit Determination | LEOP-003 | Process orbit data, update onboard ephemeris |
| Sun Acquisition | LEOP-005 | Command AOCS mode transition, monitor rates |

### Commissioning

| Procedure | ID | aocs Role |
|---|---|---|
| AOCS Sensor Calibration | COM-003 | Execute sensor calibration, validate output |
| AOCS Actuator Checkout | COM-004 | Test reaction wheels, magnetorquers |
| AOCS Mode Transitions | COM-005 | Execute mode transitions, verify stability |
| First Light | COM-012 | Ensure pointing accuracy for imaging |

### Nominal Operations

| Procedure | ID | aocs Role |
|---|---|---|
| Imaging Session | NOM-002 | Ensure pointing for imaging windows |
| Momentum Management | NOM-005 | Execute momentum desaturation |
| Eclipse Transition | NOM-010 | Monitor attitude during eclipse transition |

### Contingency

| Procedure | ID | aocs Role |
|---|---|---|
| AOCS Anomaly Recovery | CTG-002 | Diagnose and recover AOCS |
| Reaction Wheel Anomaly | CTG-007 | Diagnose RW issue, switch to backup |
| Star Tracker Failure | CTG-008 | Switch to backup ST, recalibrate |

### Emergency

| Procedure | ID | aocs Role |
|---|---|---|
| Loss of Attitude | EMG-005 | Execute detumble and reacquisition |

## 2. Available Commands and Telemetry

### Commands

#### AOCS Function Commands (S8, func_ids 0-9)

| Command | func_id | Description | Fields |
|---|---|---|---|
| AOCS_SET_MODE | 0 | Set AOCS mode | mode: 0=off, 1=safe_boot, 2=detumble, 3=coarse_sun, 4=nominal_nadir, 5=fine_point, 6=slew, 7=desaturation, 8=eclipse_propagate |
| AOCS_DESATURATE | 1 | Start wheel desaturation | (none) |
| AOCS_DISABLE_WHEEL | 2 | Disable specific reaction wheel | wheel_idx (0-3) |
| AOCS_ENABLE_WHEEL | 3 | Enable specific reaction wheel | wheel_idx (0-3) |
| ST1_POWER | 4 | Star tracker 1 power on/off | on (0/1) |
| ST2_POWER | 5 | Star tracker 2 power on/off | on (0/1) |
| ST_SELECT | 6 | Select primary star tracker | unit (0=ST1, 1=ST2) |
| MAG_SELECT | 7 | Select magnetometer source | unit (0=primary, 1=redundant) |
| RW_SET_SPEED_BIAS | 8 | Set reaction wheel speed bias | wheel_idx, bias_rpm |
| MTQ_ENABLE | 9 | Enable/disable magnetorquers | enable (0/1) |

#### General Services

| Service | Commands | Description |
|---|---|---|
| S1 | (TM only) | Request verification reports |
| S3 | HK_REQUEST, HK_ENABLE, HK_DISABLE, HK_SET_INTERVAL | Housekeeping for SID 2 (AOCS) |
| S5 | EVENT_ENABLE, EVENT_DISABLE | Event report control |
| S11 | SCHEDULE_TC, DELETE_SCHEDULED, ENABLE/DISABLE_SCHEDULE, DELETE_ALL_SCHEDULED, LIST_SCHEDULE | Time-tagged command scheduling (orbit maneuvers, imaging pointing) |
| S17 | CONNECTION_TEST | Link verification |
| S20 | SET_PARAM, GET_PARAM | Direct parameter read/write for AOCS parameters |

### Telemetry

#### AOCS Parameters (SID 2, 4 s interval)

**Attitude and Rates:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| aocs.att_q1..q4 | 0x0200-0x0203 | -- | Attitude quaternion components |
| aocs.rate_roll | 0x0204 | deg/s | Roll rate |
| aocs.rate_pitch | 0x0205 | deg/s | Pitch rate |
| aocs.rate_yaw | 0x0206 | deg/s | Yaw rate |
| aocs.att_error | 0x0217 | deg | Attitude error |
| aocs.mode | 0x020F | -- | Current AOCS mode |
| aocs.submode | 0x0262 | -- | AOCS sub-mode |
| aocs.time_in_mode | 0x0264 | s | Time in current mode |
| aocs.solar_beta | 0x0216 | deg | Solar beta angle |

**Reaction Wheels:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| aocs.rw1_speed..rw4_speed | 0x0207-0x020A | RPM | Wheel speeds |
| aocs.rw1_temp..rw4_temp | 0x0218-0x021B | C | Wheel temperatures |
| aocs.rw1_current..rw4_current | 0x0250-0x0253 | A | Wheel currents |
| aocs.rw1_enabled..rw4_enabled | 0x0254-0x0257 | -- | Wheel enabled flags |
| aocs.total_momentum | 0x025B | Nms | Total system angular momentum |

**Sensors:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| aocs.st1_status | 0x0240 | -- | ST1 status (off/boot/tracking/blind/failed) |
| aocs.st1_num_stars | 0x0241 | -- | ST1 tracked star count |
| aocs.st2_status | 0x0243 | -- | ST2 status |
| aocs.css_sun_x..z | 0x0245-0x0247 | -- | Coarse sun sensor vector |
| aocs.css_valid | 0x0248 | -- | CSS valid flag |
| aocs.mag_x..z | 0x020B-0x020D | nT | Magnetometer readings |
| aocs.mag_field_total | 0x0277 | nT | Total field magnitude |
| aocs.gyro_bias_x..z | 0x0270-0x0272 | deg/s | Gyroscope bias estimates |
| aocs.gyro_temp | 0x0273 | C | Gyroscope temperature |

**Navigation:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| aocs.gps_lat | 0x0210 | deg | GPS latitude |
| aocs.gps_lon | 0x0211 | deg | GPS longitude |
| aocs.gps_alt | 0x0212 | km | GPS altitude |
| aocs.gps_fix | 0x0274 | -- | GPS fix type |
| aocs.gps_pdop | 0x0275 | -- | Position dilution of precision |
| aocs.gps_num_sats | 0x0276 | -- | Tracked satellite count |

**Actuators:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| aocs.mtq_x_duty..z_duty | 0x0258-0x025A | % | Magnetorquer duty cycles |

#### Limit Monitoring

| Parameter | Yellow | Red |
|---|---|---|
| aocs.att_error | -1.0 -- 1.0 deg | -2.0 -- 2.0 deg |
| aocs.rate_roll/pitch/yaw | -0.5 -- 0.5 deg/s | -2.0 -- 2.0 deg/s |
| aocs.rw1_speed (all RWs) | -5000 -- 5000 RPM | -5500 -- 5500 RPM |
| aocs.rw1_temp (all RWs) | 0.0 -- 60.0 C | -5.0 -- 70.0 C |
| aocs.total_momentum | 0.0 -- 0.5 Nms | 0.0 -- 0.8 Nms |
| aocs.gyro_temp | 0.0 -- 50.0 C | -10.0 -- 60.0 C |
| aocs.gps_pdop | 0.0 -- 4.0 | 0.0 -- 6.0 |

### Display Widgets

**Attitude page:** Attitude error gauge (0-10 deg), body rates table, mode/submode/time_in_mode, star tracker status, CSS valid, magnetorquer duty cycles, total momentum.
**Reaction Wheels page:** Wheel speeds chart (10 min), speed/current/enabled tables.
**Attitude Trends page:** Attitude error chart (10 min), body rates chart (5 min).

## 3. Inter-Position Coordination Needs

| Scenario | Coordinating With | Coordination Details |
|---|---|---|
| Initial orbit determination (LEOP-003) | flight_director, ttc | TTC provides range/Doppler data; AOCS processes orbit solution; FD approves |
| Sun acquisition (LEOP-005) | flight_director | FD authorizes attitude maneuver; AOCS commands mode transition |
| Imaging session (NOM-002) | payload_ops | AOCS ensures fine_point or slew mode for pointing; payload_ops triggers capture |
| First light (COM-012) | flight_director, payload_ops | AOCS confirms pointing accuracy; payload_ops captures image |
| Eclipse transition (NOM-010) | eps_tcs | AOCS monitors attitude during eclipse; eps_tcs manages power balance |
| Momentum management (NOM-007) | (independent) | Typically autonomous; may inform FD if momentum approaching limits |
| RW anomaly (CTG-007) | flight_director | FD authorizes; AOCS disables faulty wheel, reconfigures control law |
| ST failure (CTG-008) | flight_director | FD authorizes; AOCS switches to backup tracker |
| Loss of attitude (EMG-005) | flight_director | FD authorizes emergency recovery; AOCS executes detumble and sun reacquisition |

### FDIR Rules Relevant to This Position

| FDIR Rule | Trigger | Action |
|---|---|---|
| aocs.att_error > 5 deg | Level 2 | safe_mode_aocs |
| aocs.rw1_temp > 65 C | Level 1 | disable_rw1 |
| aocs.rw2_temp > 65 C | Level 1 | disable_rw2 |
| aocs.rw3_temp > 65 C | Level 1 | disable_rw3 |
| aocs.rw4_temp > 65 C | Level 1 | disable_rw4 |

## 4. GO/NO-GO Responsibilities

The Flight Dynamics position provides GO/NO-GO input to the Flight Director for:

- **Imaging readiness:** Confirm AOCS in fine_point or nominal_nadir mode, att_error within limits, all required RWs healthy, ST tracking with sufficient star count.
- **Orbit maneuver execution:** Confirm orbit solution valid (GPS fix 3D), momentum within budget, schedule loaded and verified.
- **Mode transitions:** Confirm current mode stable (time_in_mode sufficient, rates converged) before authorizing next mode step.
- **Eclipse entry:** Confirm attitude stable, eclipse_propagate mode ready, momentum margin adequate for eclipse duration.
- **Post-anomaly recovery:** Confirm attitude converged and rates damped before declaring AOCS GO for nominal operations.

**Critical Decision Points:**
- If total_momentum exceeds 0.5 Nms (yellow), recommend desaturation to Flight Director.
- If any RW speed approaches saturation (+/-5000 RPM yellow), recommend momentum management.
- If att_error exceeds 1.0 deg, recommend aborting imaging session and investigating.
- If both star trackers are in blind or failed state, recommend coarse_sun mode and abort fine pointing.

---
*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

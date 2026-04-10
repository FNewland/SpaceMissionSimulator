# PROC-NOM-006: Routine Housekeeping Assessment
**Subsystem:** ALL
**Phase:** NOMINAL
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Perform a comprehensive health assessment of all spacecraft subsystems by
collecting and evaluating housekeeping telemetry from all six HK service IDs.
This procedure is a passive monitoring activity --- no commanding is performed
unless an anomaly is detected. It is executed at least once per ground contact
and serves as the primary tool for early anomaly detection and trend analysis.

## Prerequisites
- [ ] TTC link established: `ttc.link_status` (0x0501) = 1
- [ ] MCS limit checker database loaded with current limit sets
- [ ] Previous pass HK baseline available for trend comparison
- [ ] Operator has access to trending display and anomaly log

## Procedure Steps

### Step 1 --- Request All Housekeeping Packets
**TC:** `HK_REQUEST` SID=1 (Service 3, Subtype 25) --- EPS housekeeping
**TC:** `HK_REQUEST` SID=2 (Service 3, Subtype 25) --- AOCS housekeeping
**TC:** `HK_REQUEST` SID=3 (Service 3, Subtype 25) --- TCS housekeeping
**TC:** `HK_REQUEST` SID=4 (Service 3, Subtype 25) --- OBDH housekeeping
**TC:** `HK_REQUEST` SID=5 (Service 3, Subtype 25) --- TTC housekeeping
**TC:** `HK_REQUEST` SID=6 (Service 3, Subtype 25) --- Payload housekeeping
**Verify:** All 6 HK packets received within 30 s
**Action:** If any packet missing, re-request once after 15 s timeout.

### Step 2 --- EPS Health Assessment
**Verify:** `eps.bat_soc` (0x0101) > 40 % --- NOMINAL range 40-100 %
**Verify:** `eps.bus_voltage` (0x0105) in range 27.0 - 32.0 V
**Verify:** `eps.power_gen` (0x0107) > 0 W (if in sunlight; 0 W acceptable in eclipse)
**Verify:** `eps.power_cons` (0x0106) < 350 W (nominal bus budget)
**Verify:** Power margin: `eps.power_gen` - `eps.power_cons` > -50 W
**Trend:** Compare SoC with previous pass --- expected variation < 10 % per orbit.
**Flag:** If SoC trending downward over multiple passes, investigate power balance.

### Step 3 --- AOCS Health Assessment
**Verify:** `aocs.mode` (0x020F) = 0 (NOMINAL / NADIR_POINT)
**Verify:** `aocs.att_error` (0x0217) < 1.0 deg
**Verify:** `aocs.rate_roll` (0x0204) < 0.05 deg/s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.05 deg/s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.05 deg/s
**Verify:** |`aocs.rw1_speed`| (0x0207) < 5000 RPM
**Verify:** |`aocs.rw2_speed`| (0x0208) < 5000 RPM
**Verify:** |`aocs.rw3_speed`| (0x0209) < 5000 RPM
**Verify:** |`aocs.rw4_speed`| (0x020A) < 5000 RPM
**Trend:** Compare wheel speeds with previous pass. Monotonic increase indicates
  insufficient desaturation --- schedule PROC-NOM-004.
**Flag:** If any wheel > 4500 RPM, recommend immediate desaturation.

### Step 4 --- TCS Health Assessment
**Verify:** `tcs.temp_obc` (0x0406) in range -10.0 to +50.0 C
**Verify:** `tcs.temp_battery` (0x0407) in range +5.0 to +35.0 C
**Verify:** `tcs.temp_fpa` (0x0408) < 0.0 C (if cooler active, expect < -25 C)
**Verify:** `tcs.htr_battery` (0x040A) = expected state (ON if temp < +10 C)
**Verify:** `tcs.cooler_fpa` (0x040C) = expected state (ON if payload not OFF)
**Trend:** Compare battery temperature with orbital position --- should follow
  predictable eclipse/sunlight pattern.
**Flag:** If battery temp outside +5 to +35 C, investigate heater and thermal path.

### Step 5 --- OBDH Health Assessment
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL)
**Verify:** `obdh.cpu_load` (0x0302) < 80 %
**Verify:** `obdh.uptime` (0x0308) consistent with expected value (no unexpected
  reboots --- uptime should be monotonically increasing since last known reset)
**Trend:** CPU load should be stable. Increasing trend may indicate memory leak
  or runaway task.
**Flag:** If uptime is unexpectedly low, an OBC reset has occurred --- investigate
  event log for cause.

### Step 6 --- TTC Health Assessment
**Verify:** `ttc.link_status` (0x0501) = 1 (LINK_UP)
**Verify:** `ttc.rssi` (0x0502) > -105 dBm
**Verify:** `ttc.link_margin` (0x0503) > 2.0 dB
**Trend:** RSSI should follow expected pass profile (increase to TCA, decrease
  after). Consistent low RSSI may indicate antenna degradation.
**Flag:** If link margin < 2.0 dB at high elevation, investigate RF chain.

### Step 7 --- Payload Health Assessment
**Verify:** `payload.mode` (0x0600) = expected state (0=OFF, 1=STANDBY, 2=IMAGING)
**Verify:** `payload.fpa_temp` (0x0601) within expected range for current mode
**Verify:** `payload.store_used` (0x0604) < 95 % (alert threshold)
**Verify:** `payload.image_count` (0x0605) matches expected count from pass plan
**Trend:** Storage usage should decrease after downlink passes, increase after
  imaging passes. Unexpected changes indicate anomaly.
**Flag:** If storage > 90 %, prioritize downlink at next available pass.

### Step 8 --- Summary and Anomaly Logging
**Action:** Compile health assessment summary:
  - EPS: SoC=___%, bus=___V, margin=___W --- NOMINAL / CAUTION / WARNING
  - AOCS: mode=___, att_err=___deg, max_rw=___RPM --- NOMINAL / CAUTION / WARNING
  - TCS: OBC=___C, battery=___C, FPA=___C --- NOMINAL / CAUTION / WARNING
  - OBDH: mode=___, CPU=___%, uptime=___s --- NOMINAL / CAUTION / WARNING
  - TTC: RSSI=___dBm, margin=___dB --- NOMINAL / CAUTION / WARNING
  - Payload: mode=___, store=___%, images=___ --- NOMINAL / CAUTION / WARNING
**Log:** Record all parameter values and any flags in the pass health log.
**Action:** If any parameter flagged CAUTION or WARNING, notify Flight Director
  and reference appropriate contingency or nominal corrective procedure.

## Off-Nominal Handling
- If any HK packet not received after two requests: Flag subsystem communication
  anomaly. Verify OBDH packet routing and TTC link integrity.
- If any parameter in WARNING (red) limit: Immediately notify Flight Director.
  Execute relevant contingency procedure without waiting for full assessment.
- If multiple subsystems show CAUTION simultaneously: May indicate a systemic
  issue (power, thermal, or OBC). Correlate parameters before taking action.
- No commanding is performed in this procedure unless a WARNING-level anomaly
  is detected requiring immediate response.

## Post-Conditions
- [ ] All 6 HK packets collected and archived
- [ ] All parameters checked against nominal limits
- [ ] Trend comparison with previous pass completed
- [ ] Anomaly log updated (entries added if any flags raised)
- [ ] Flight Director briefed on overall spacecraft health status
- [ ] Corrective actions scheduled if any CAUTION flags raised

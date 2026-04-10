# OPERATIONS GUIDE — EOSAT-1 Spacecraft Simulator

**Practical guide for mission operations, scenario execution, and anomaly response**

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Simulator Startup](#simulator-startup)
3. [Mission Control System (MCS)](#mission-control-system-mcs)
4. [Telemetry Monitoring](#telemetry-monitoring)
5. [Commanding the Spacecraft](#commanding-the-spacecraft)
6. [Mission Planner Usage](#mission-planner-usage)
7. [Anomaly Response Procedures](#anomaly-response-procedures)
8. [Common Operational Scenarios](#common-operational-scenarios)
9. [Troubleshooting](#troubleshooting)
10. [Training Scenarios](#training-scenarios)

---

## Quick Start

**Prerequisites:**
- Python 3.11+ with dependencies installed
- EOSAT-1 configuration files in place
- MCS web browser (Chrome/Firefox recommended)

**30-second startup:**

```bash
# Terminal 1: Start Simulator
cd /sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation
python -m smo_simulator --config configs/eosat1/ --port 5678

# Terminal 2: Start MCS
cd /sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation
python -m smo_mcs --simulator-host localhost:5678 --mcs-port 8080

# Browser: Open MCS
Navigate to: http://localhost:8080/
Login with default credentials (if required)
```

**First actions (5 minutes):**

1. Open MCS System Overview → Verify attitude quaternion, orbit position
2. Request HK (S3 subtype 27) → Confirm telemetry flowing
3. Send NOOP echo (S17 subtype 1) → Verify command link
4. Enable periodic HK (S3 subtype 5, SID=1) → Start EPS telemetry streaming

---

## Simulator Startup

### Option 1: Standard Mode (Real-time Simulation)

```bash
python -m smo_simulator \
  --config configs/eosat1/ \
  --port 5678 \
  --time-scale 1.0
```

**Parameters:**
- `--config`: Path to EOSAT-1 configuration
- `--port`: TCP port for MCS connection (default 5678)
- `--time-scale`: 1.0 = real-time, 2.0 = 2x speed, 0.5 = half speed
- `--scenario`: Load failure scenario (optional)

### Option 2: Accelerated Simulation (Testing)

```bash
python -m smo_simulator \
  --config configs/eosat1/ \
  --port 5678 \
  --time-scale 10.0
```

**Use case:** Test 24-hour operations in 2.4 hours.

### Option 3: With Failure Scenario

```bash
python -m smo_simulator \
  --config configs/eosat1/ \
  --port 5678 \
  --scenario configs/eosat1/scenarios/battery_discharge.yaml
```

**Available scenarios:** See `configs/eosat1/scenarios/` directory

### Option 4: Distributed (Gateway Mode)

```bash
# Start Simulator (headless, on server)
python -m smo_simulator --config configs/eosat1/ --port 5678

# Start Gateway (relays to network)
python -m smo_gateway --simulator localhost:5678 --listen 0.0.0.0:6789

# Connect MCS (from laptop, different machine)
python -m smo_mcs --simulator-host gateway.corp.net:6789 --mcs-port 8080
```

---

## Mission Control System (MCS)

### Dashboard Layout

**System Overview Panel (top-left):**
```
Spacecraft: EOSAT-1
Phase: Nominal Operations
Orbit: 98.2° sun-synchronous, 705 km altitude

Attitude:
  Quaternion: (0.001, 0.707, -0.002, 0.707)
  Pointing: Nadir (Earth center)
  Error: 0.05°

Power:
  Battery SoC: 78%
  Load Shedding: Stage 0 (None)
  Power Margin: +250 W

Thermal:
  Hottest Zone: OBC (+32°C)
  FPA: -8°C (Operational)
```

**Telemetry Plot (top-right):**
- Selectable parameters for real-time trending
- 1-hour history by default
- Zoom/pan controls

**Command Panel (bottom-left):**
- Subsystem selector (AOCS, EPS, TCS, TTC, OBDH, Payload)
- Command function list with parameter builders
- Command history log

**Alert Panel (bottom-right):**
- Real-time S5 event stream (color-coded by severity)
- Event filter by subsystem
- Event acknowledgment tracking

### Key Display Tabs

1. **System Overview** — Spacecraft state summary
2. **Power Budget** — Battery, solar, load shedding, power margin
3. **Attitude & Control** — Quaternion, rates, momentum, wheel status
4. **Thermal Monitor** — Zone temperatures, heater/cooler status
5. **Link Quality** — Carrier lock, link margin, PA temp
6. **Payload Status** — FPA temp, storage, image catalog
7. **FDIR Alarms** — S12 violations, S19 rule triggers
8. **Contact Schedule** — Ground station visibility windows
9. **Procedure Status** — Active/scheduled procedures
10. **Event Log** — Historical S5 events (queryable)

---

## Telemetry Monitoring

### Enable Periodic Housekeeping

By default, HK is **not streaming**. Enable it for continuous telemetry:

```
MCS Command: S3 subtype 5 (Enable HK)
Parameter: SID = 1 (EPS structure)
Response: S1.1 Acceptance report
Effect: EPS HK packets sent every 4 seconds
```

**Repeat for other structures:**
- SID 2: AOCS (attitude, wheels, sensors)
- SID 3: TCS (thermal zones)
- SID 4: OBDH (CPU, memory, storage)
- SID 5: Payload (FPA, imaging, storage)
- SID 6: TTC (link quality, PA thermal)

### Key Parameters to Monitor

**Critical (check first):**

| Parameter | Range | Alert Threshold | Subsystem |
|-----------|-------|-----------------|-----------|
| `eps.bat_soc` | 0-100% | < 10% (CRITICAL) | EPS |
| `eps.bus_voltage` | 25-35 V | < 25V (CRITICAL) | EPS |
| `aocs.att_error` | 0-180° | > 1° (WARNING) | AOCS |
| `aocs.total_momentum` | -100 to +100 Nms | > 90 Nms (WARNING) | AOCS |
| `tcs.fpa_temp` | -20 to 0°C | > -3°C (ALARM) | TCS |
| `ttc.link_margin` | 0-15 dB | < 3 dB (CRITICAL) | TTC |
| `ttc.pa_temp` | 20-80°C | > 65°C (SHUTDOWN) | TTC |

**Useful (monitor periodically):**

| Parameter | Typical | Watch For |
|-----------|---------|-----------|
| `eps.power_gen` | 1000-1500 W | Degradation trend |
| `eps.power_cons` | 800-1200 W | Spike anomalies |
| `aocs.rw_speed` (all 4) | 0-2000 RPM | Imbalance, overspeed |
| `tcs.battery_temp` | -5 to +40°C | > 45°C (heater off) |
| `ttc.ber` | < 1e-6 | Rising trend |

### Telemetry Plots

**Battery State of Charge (SoC) over 24 hours:**
```
Plot battery SoC vs. time
Observe: Sawtooth pattern (discharge in eclipse, charge in sunlight)
Normal: SoC varies 30-90% (does not reach 0% or 100%)
Anomaly: Monotonic rise/fall (charge/discharge not balancing)
```

**Reaction Wheel Speed (all 4 wheels):**
```
Plot all 4 RW speeds vs. time
Observe: Wheels maintain balanced speeds (±100 RPM of each other)
Normal: Gradual increase during slew, rapid decrease during desat
Anomaly: One wheel much faster than others (imbalance) → Check FDIR
```

**Link Margin over Ground Pass:**
```
Plot Eb/N0 (link_margin) vs. time during AOS-LOS window
Observe: Rise as ground station approaches (stronger signal)
         Peak at max elevation, fall as station recedes
Expected: Margin > 6 dB throughout pass
Anomaly: Margin < 3 dB → S19 rule triggers PA power increase
```

---

## Commanding the Spacecraft

### Command Workflow

1. **Verify spacecraft state** (health check)
2. **Check power/thermal budgets** (ensure safe to execute)
3. **Compose command** (fill parameters)
4. **Submit command** (click Send or CLI)
5. **Monitor S1 reports** (acceptance, execution, completion)
6. **Verify result** (HK telemetry, events)

### Example 1: Simple Mode Change

**Objective:** Switch AOCS from Nominal Nadir to Safe Mode

**Step 1: Verify Safe to Proceed**

```
MCS: Query HK_AOCS (S3 subtype 27, SID=2)
Check:
  - aocs.mode = 4 (nominal_nadir)
  - aocs.att_error < 0.5° (attitude good)
  - aocs.total_momentum < 50 Nms (wheels not saturated)
  - Power: eps.bus_voltage > 27V (sufficient power)
Status: OK, proceed
```

**Step 2: Compose Command**

```
MCS Command Builder:
  Service: 8 (Function Management)
  Subtype: 1 (Execute Function)
  Function ID: 0 (AOCS_SET_MODE)
  Parameter: mode = 1 (safe_boot)
```

**Step 3: Submit**

```
MCS: Click "Send Command"
Response (immediate):
  S1.1: Acceptance report (request_id=1234)
```

**Step 4: Monitor Execution**

```
MCS Event Log (real-time):
  T+0 sec:  S1.1 Acceptance (mode transition accepted)
  T+0 sec:  S1.3 Execution start
  T+1 sec:  S1.5 Progress (step 1/3 - stabilize quaternion)
  T+2 sec:  S1.5 Progress (step 2/3 - reduce wheel speeds)
  T+3 sec:  S1.5 Progress (step 3/3 - coarse sun pointing)
  T+5 sec:  S1.7 Completion (mode transition complete)
  T+5 sec:  S5.0200 AOCS_MODE_CHANGE event
```

**Step 5: Verify**

```
MCS: Query HK_AOCS (S3 subtype 27, SID=2)
Check:
  - aocs.mode = 1 (safe_boot) ✓
  - aocs.att_error < 2° (stabilizing)
  - aocs.rw[1-4]_speed → 0 RPM (wheels spinning down)
Status: Transition complete, spacecraft safe
```

### Example 2: Complex Command — Imaging Pass

**Objective:** Capture image at specific geographic location during ground contact

**Timeline:**
- T-5 min: Set AOCS to fine point
- T-2 min: Enable imaging on payload
- T+0 min: Capture image (60 sec)
- T+65 min: Download image via ground station

**Step 1: Pre-Pass Preparation (T-5 min)**

```
MCS Command 1: AOCS fine point
  S8 func_id=0, mode=5
  Response: S1 reports, mode change event

MCS Command 2: Enable payload
  S8 func_id=26 (PAYLOAD_SET_MODE), mode=2
  Response: S1 reports, mode change event

MCS Command 3: Set cooler temperature
  S8 func_id=36 (PAYLOAD_COOLER_SETPOINT), temp=-8°C
  Response: S1 reports
```

**Step 2: Imaging (T+0 min)**

```
MCS Command 4: Capture image
  S8 func_id=28 (PAYLOAD_CAPTURE)
  Parameters: scene_id=42, lines=4096
  Response: S1.3 (execution start)

MCS Monitoring:
  T+0 sec:   S5.0600 IMAGING_START event
  T+30 sec:  HK_Payload shows image_count incrementing
  T+60 sec:  S5.0601 IMAGING_STOP event
  T+60 sec:  S1.7 Execution complete
```

**Step 3: Verify Image Captured**

```
MCS: Query HK_Payload (S3 subtype 27, SID=5)
Check:
  - image_count = previous+1
  - storage_used increased by ~2 MB
  - compression_ratio = 4.2:1
  - snr_avg = 32 dB (good quality)
Status: Image captured successfully
```

**Step 4: Schedule Download (T+30 min, during ground contact)**

Option A: **Immediate download** (if ground station in view)

```
MCS: Check contact schedule
  AOS (Acquisition of Signal): T+30 min
  LOS (Loss of Signal): T+37 min
  Link margin: 8 dB (adequate)
  Downlink capacity: ~3 Mbps

MCS Command: S13 subtype 1 (initiate transfer)
  transfer_id=1, data_type=image_0042, block_size=4096

MCS Monitoring: S13 subtype 3 (request blocks)
  [Automatically downloads 512 blocks × 4KB = 2 MB in ~10 seconds]
  MCS plots download progress bar
  CRC verified per block

MCS Command: S13 subtype 5 (end transfer)
  transfer_id=1
  Response: S1.7 (transfer complete)
```

Option B: **Schedule for later download** (if time-constrained)

```
MCS: Use Mission Planner
  Schedule imaging for known ocean target (7 targets configured)
  Planner outputs: AOS/LOS, contact duration, data volume
  Automatically schedules S11 TC activity

MCS: Monitor S11 execution at scheduled time
  S1 reports confirm auto-executed commands
```

### Command Error Recovery

**Scenario: Command rejected due to safety check**

```
MCS: Send S8 func_id=0 (AOCS_SET_MODE), mode=1 (safe)

Response (immediate):
  S1.2: Acceptance failure
  error_code=0x0003
  reason="Cannot transition to safe: wheels still desaturating"

Recovery:
  1. Check S5 event log
     Found: DESATURATION_START (5 min ago)
  2. Query HK_AOCS
     aocs.total_momentum = 45 Nms (still declining)
  3. Wait 2 minutes for desaturation to complete
  4. Monitor S5.0208 DESATURATION_COMPLETE event
  5. Retry command
     S1.1: Acceptance success
     Mode transition proceeds
```

---

## Mission Planner Usage

### Purpose

The Mission Planner schedules activities (imaging, downloads, maintenance) while respecting constraints:
- **Power:** Battery SoC and DoD limits
- **Thermal:** Zone temperature limits, cooler capacity
- **AOCS:** Slew time, momentum headroom, pointing accuracy
- **Data:** Payload storage limits, downlink capacity

### Workflow

**1. Define Mission Goals**

```
MCS Planner: Click "New Mission"
Mission name: "EOSAT-1 Week-3 Operations"
Start time: 2026-04-11 00:00 UTC
Duration: 7 days
```

**2. Define Constraints**

```
MCS Planner: Constraint Manager
Power:
  - Minimum battery SoC: 20%
  - Maximum DoD: 50%
Thermal:
  - Maximum battery temp: 40°C
  - Maximum FPA temp: -3°C
AOCS:
  - Pointing accuracy: < 0.5°
  - Momentum reserve: > 10 Nms
Data:
  - Maximum payload storage: 95%
  - Minimum downlink per pass: 500 MB
```

**3. Define Activities**

```
MCS Planner: Activity Library
Available activities:
  - IMAGING_PASS (5+ targets, configurable lat/lon)
  - DATA_DOWNLOAD (to ground station)
  - SAFE_MODE_RECOVERY (post-anomaly)
  - PAYLOAD_MAINTENANCE (calibration, diagnostics)
  - SYSTEM_HEALTH_CHECK (subsystem test)
```

**4. Schedule Activities**

```
MCS Planner: Schedule Builder
Activity 1: IMAGING_PASS
  Target: "Agulhas Current Region" (lat=−40, lon=20)
  Duration: 60 sec
  Power budget: 200 W additional
  Earliest start: 2026-04-11 02:15 UTC (next AOS over target)

Activity 2: DATA_DOWNLOAD
  Ground station: "Canberra" (Australia)
  Start: 2026-04-11 03:00 UTC (AOS)
  Duration: 7 min (contact window)
  Data volume: 500 MB (payload images)

Activity 3: SAFE_MODE_RECOVERY
  Start: 2026-04-12 12:00 UTC (scheduled maintenance)
  Duration: 15 min
  Power budget: 150 W

[Continue scheduling for 7 days...]
```

**5. Optimize Schedule**

```
MCS Planner: Constraint Checker
Analysis:
  ✓ All power constraints satisfied (SoC never < 22%)
  ✓ All thermal constraints satisfied (max temp 38°C)
  ✓ All AOCS constraints satisfied (momentum reserve maintained)
  ✓ Downlink capacity sufficient (15 GB/week, need 12 GB)

Warning:
  ! FPA cooling rate limiting on 2026-04-14 (3 consecutive imaging passes)
  Recommendation: Reduce 2nd pass duration to 45 sec, extend cooler off-time by 10 min

Optimization:
  Click "Apply Recommendation"
  [Plan updated, all constraints re-checked]
  Status: READY TO UPLOAD
```

**6. Upload to Spacecraft**

```
MCS Planner: Upload Mission Plan
Action: Convert plan to S11 TC activities
  [24 activities → 24 S11 telecommands]

MCS: Monitor S11 execution
  S1 reports for each scheduled activity
  Automatic execution at scheduled times
  Ground operator monitors and intervenes if needed

Event Log:
  T+0h:  Activity 1 (AOCS fine point) auto-executed
  T+10min: Activity 2 (Payload imaging) auto-executed
  T+70min: Activity 3 (Data download) auto-executed
  ...
  T+7d: Final activity complete
  Status: Mission plan executed successfully
```

### Ground Station Integration

**Canberra Station (Example):**

```
MCS Planner: Ground Stations
Station name: Canberra, Australia
Location: lat=−35.4, lon=149.0
Antenna: 70m parabolic, X-band
Elevation mask: 5° (minimum elevation for lock)
Downlink band: 8 GHz (center frequency)
Uplink band: 7 GHz (center frequency)

Contact prediction:
  AOS: 2026-04-11 03:15 UTC (elevation 5°)
  MAX: 2026-04-11 03:21 UTC (elevation 87°, max link margin)
  LOS: 2026-04-11 03:28 UTC (elevation 5°)
  Contact duration: 13 minutes
  Average link margin: 8 dB (good for high-rate downlink)
  Predicted data volume: 500 MB at 1 Mbps
```

---

## Anomaly Response Procedures

### AOCS Anomalies

#### Reaction Wheel Overspeed

**Event:** S5.0201 RW_OVERSPEED (wheel speed > 5000 RPM)

**Autonomous Response:**
- S12 monitoring detects violation
- S19 rule triggers: "RW overspeed → disable wheel"
- S8 AOCS_DISABLE_WHEEL executed automatically

**Manual Override (if autonomous recovery fails):**

```
MCS: Check which wheel is overspeed
  S3 request HK_AOCS (SID=2)
  aocs.rw1_speed = 5200 RPM ← problematic
  aocs.rw2_speed = 1800 RPM
  aocs.rw3_speed = 1850 RPM
  aocs.rw4_speed = 1900 RPM

MCS Command: Disable wheel 1
  S8 func_id=2 (AOCS_DISABLE_WHEEL), param=0
  Response: S1 reports

MCS Verification:
  Re-query HK_AOCS after 5 sec
  aocs.rw1_enabled = 0 (disabled)
  aocs.rw1_speed → 0 (spun down)
  S5.0201 event should stop repeating
```

#### Star Tracker Blinded

**Event:** S5.0203 ST_BLIND (star tracker lost lock)

**Autonomous Response:**
- S12 monitoring detects ST status = BLIND
- S19 rule triggers: "ST1 blind → switch to ST2"
- S8 ST_SELECT executed, switching to redundant star tracker

**Manual Recovery (if both trackers blinded):**

```
MCS: Check AOCS status
  S3 request HK_AOCS (SID=2)
  aocs.st1_status = 3 (blind)
  aocs.st2_status = 3 (blind)
  aocs.mode = 4 (nominal_nadir, unsustainable without ST)

MCS Commands:
  1. S8 func_id=0 (AOCS_SET_MODE), mode=3 (coarse_sun_pointing)
     Status: Mode transition accepted, spacecraft uses CSS + magnetometer

  2. S8 func_id=4 (ST1_POWER), on=0
     Status: Power off to let ST1 cool, clear Sun blindness

  3. [Wait 5 minutes]

  4. S8 func_id=4 (ST1_POWER), on=1
     Status: Power on, ST1 boots and reacquires

  5. Monitor S5.0204 ST_RECOVERY event

  6. S8 func_id=0 (AOCS_SET_MODE), mode=5 (fine_point)
     Status: Mode transition back to nominal
```

#### Momentum Saturation

**Event:** S5.0205 MOMENTUM_SATURATION (momentum > 90% of limit)

**Autonomous Response:**
- S12 monitoring detects total_momentum > 90 Nms
- S19 rule triggers: "Momentum saturated → desaturate"
- S8 AOCS_DESATURATE executed automatically

**Monitoring During Desaturation:**

```
MCS: Track momentum as wheels desat
  Plot: aocs.total_momentum vs. time

Normal behavior:
  T+0 sec:   momentum = 95 Nms (saturated)
  T+5 sec:   momentum = 80 Nms (declining)
  T+10 sec:  momentum = 50 Nms (good progress)
  T+15 sec:  momentum = 20 Nms (nearly complete)
  T+20 sec:  S5.0208 DESATURATION_COMPLETE event

Expected duration: 15-25 seconds depending on wheel authority
```

---

### EPS Anomalies

#### Battery SoC Critical

**Event:** S5.0102 BATTERY_SOC_CRITICAL (SoC < 10%)

**Autonomous Response:**
- S12 monitoring detects SoC < 10%
- S19 rules trigger:
  1. "SoC critical → Payload off" (reduce load)
  2. "SoC critical → FPA cooler off" (reduce thermal load)
- S8 commands executed, load shedding stage 1 activated

**Manual Intervention:**

```
MCS: Assess power situation
  S3 request HK_EPS (SID=1)
  eps.bat_soc = 8%
  eps.power_gen = 1100 W (sunlit)
  eps.power_cons = 400 W (after payload off)
  eps.power_margin = +700 W (positive, recovering)

Decision: Condition temporary, battery recovering in sunlight

MCS Monitoring:
  Re-query HK every 30 sec
  Watch eps.bat_soc trend upward
  Expected recovery time: 10-15 minutes (one orbit)

When recovered (SoC > 20%):
  S5.0101 BATTERY_SOC_WARNING event fires (informational)
  MCS alerts operator, no action required
```

#### Bus Undervoltage

**Event:** S5.0104 BUS_UNDERVOLTAGE_CRITICAL (bus voltage < 25V)

**Autonomous Response:**
- S12 monitoring detects bus voltage < 25V
- S19 rule triggers: "Undervoltage → Payload off"
- S8 EPS_PAYLOAD_MODE executed (mode=0, off)

**Diagnosis & Recovery:**

```
MCS: Investigate cause
  S3 request HK_EPS (SID=1)
  eps.bus_voltage = 24.5V (critically low)
  eps.bat_voltage = 28V (good)
  eps.eclipse_flag = 1 (in eclipse)

Scenario: Heavy power consumption in eclipse
  Power generation: 0 W (dark side of Earth)
  Power consumption: 1500 W (TTC TX active, AOCS wheels active)
  Battery discharge rate: 1500 W

MCS Command: Reduce power consumption
  1. S8 func_id=26 (PAYLOAD_SET_MODE), mode=0
     Already off (from S19 rule)

  2. S8 func_id=68 (TTC_SET_TX_POWER), level=1
     Reduce TTC transmitter power
     Response: S1 reports

  3. S8 func_id=25 (EPS_LOAD_SHED_STAGE_3)
     Activate maximum load shedding
     Response: S1 reports

  4. S3 request HK_EPS
     eps.bus_voltage = 26.5V (recovering)

MCS Monitoring:
  Wait for eclipse exit (AOS to illuminated region)
  eps.power_gen → 1200W (solar arrays reilluminated)
  eps.bus_voltage → 29V (nominal)
  eps.bat_soc recovering
```

---

### TCS Anomalies

#### FPA Overtemperature

**Event:** S5.0604 FPA_OVERTEMP (FPA temp > -3°C)

**Autonomous Response:**
- S12 monitoring detects FPA_temp > -3°C
- S19 rule triggers: "FPA overtemp → Payload off"
- S8 EPS_PAYLOAD_MODE executed (mode=0, off)
- S5 PAYLOAD_MODE_CHANGE event generated
- FPA cooler continues operating to cool down

**Manual Actions:**

```
MCS: Monitor FPA cooldown
  S3 request HK_Payload (SID=5)
  fpa_temp = -1°C (overtemp)
  fpa_cooler_state = ON

MCS Plot: FPA temperature vs. time
  T+0 min:   FPA = -1°C (overtemp)
  T+5 min:   FPA = -4°C (recovering)
  T+10 min:  FPA = -8°C (nominal range)

When cooled to nominal:
  S5.040A FPA_THERMAL_READY event fires
  MCS can re-enable payload if needed

MCS Command: Resume imaging (if mission-critical)
  S8 func_id=26 (PAYLOAD_SET_MODE), mode=2
  Response: S1 reports

  If FPA warms up again:
    S12 rule triggers again
    Payload auto-disabled (S19 rule)
    Indicates cooler failure (hardware fault)
    Escalate to FDIR procedures
```

---

### TTC Anomalies

#### Link Margin Critical

**Event:** S5.0507 LINK_MARGIN_CRITICAL (Eb/N0 < 3 dB)

**Autonomous Response:**
- S12 monitoring detects link_margin < 3 dB
- S19 rule triggers: "Link critical → increase TX power"
- S8 TTC_PA_POWER executed (increase to 80-90%)

**Manual Actions:**

```
MCS: Check contact window
  S3 request HK_TTC (SID=6)
  ttc.link_margin = 2.5 dB (critical)
  ttc.pa_temp = 45°C (room for power increase)
  ttc.ber = 3e-5 (some bit errors, acceptable)

MCS Command: Increase TX power further
  S8 func_id=68 (TTC_SET_TX_POWER), level=95
  Response: S1 reports

MCS Monitoring:
  Re-query HK every 10 sec
  ttc.link_margin improving as ground station gets closer

T+5 min: Link margin recovers to 6 dB (good threshold)
  Reduce TX power to 75% (heat management)
  S8 func_id=68 (TTC_SET_TX_POWER), level=75
```

#### PA Overheat Shutdown

**Event:** S5.0509 PA_OVERTEMP_SHUTDOWN (PA temp > 65°C)

**Autonomous Response:**
- S12 monitoring detects PA_temp > 65°C
- S19 rule triggers: "PA overheat → PA off"
- S8 TTC_PA_POWER executed (power=0, shutdown)
- Downlink lost until PA cools

**Manual Recovery:**

```
MCS: Assess situation
  S3 request HK_TTC (SID=6)
  ttc.pa_temp = 70°C (above shutdown limit)
  ttc.pa_powered = 0 (already off from S19)
  ttc.carrier_locked = 0 (no downlink)

  Contact window: 5 minutes remaining

MCS Actions:
  1. Wait 3 minutes for PA to cool (passive)
  2. S3 request HK_TTC
     ttc.pa_temp = 55°C (cooled sufficiently)

  3. S8 func_id=68 (TTC_SET_TX_POWER), level=25
     Low power restart to prevent thermal cycling
     Response: S1 reports

  4. Monitor for carrier lock acquisition
     S5.0500 CARRIER_LOCK_ACQUIRED
     Link margin should be adequate for low power

  5. Gradually increase TX power as contact continues
     func_id=68, level 0 → 1 → 2
```

---

## Common Operational Scenarios

### Scenario 1: Standard Imaging Pass (60 minutes)

**Timeline:**

| Time | Action | Command | Verification |
|------|--------|---------|--------------|
| T-5m | Enable AOCS fine point | S8 func 0, mode=5 | S1 complete, mode=5 |
| T-2m | Enable payload | S8 func 26, mode=2 | S1 complete, payload ON |
| T+0m | Start imaging | S8 func 28, scene/lines | S5.0600 IMAGING_START |
| T+30m | Mid-pass check | S3 SID=5 (HK_Payload) | Storage increasing, FPA nominal |
| T+60m | Stop imaging | (auto from duration) | S5.0601 IMAGING_STOP, S1.7 |
| T+70m | Download image | S13 init + blocks | S13 subtype 3, 512 blocks |
| T+80m | Verify download | S3 SID=5 (HK_Payload) | Storage decreased by 2 MB |

**Expected Power Impact:**
- Baseline: 800 W
- +Payload: +200 W
- +Imaging: +100 W
- Total: 1100 W (manageable in sunlight)

---

### Scenario 2: Safe Mode Recovery (Post-Anomaly)

**Trigger:** Spacecraft enters safe mode due to reaction wheel overspeed

**Recovery Timeline:**

```
T+0s:   Anomaly detected (S5.0201 RW_OVERSPEED)
        S19 rule: disable overspecc wheel
        Mode: Fine point → Coarse sun (CSS + mag)

T+30s:  MCS operator checks S5 event log
        Confirms: RW1 overspeed, now disabled
        Decision: Full safe mode transition

MCS Command 1: Set safe mode
  S8 func_id=0, mode=1 (safe_boot)
  Response: S1.3 (exec start), S5.0200 mode change

T+60s:  Mode transition complete
        Wheels spinning down to 0 RPM
        Attitude: Coarse sun pointing, body X toward sun
        Power: 600 W (all non-essential off)

T+120s: MCS operator verifies status
  HK_AOCS check:
    mode=1 ✓
    rw_speed all < 500 RPM ✓
    att_error < 2° ✓
  Status: Safe mode stable

T+600s: Decision to resume operations
  MCS Command 2: Exit safe mode
    S8 func_id=0, mode=5 (fine_point)
    S8 func_id=12 (BEGIN_ACQUISITION)
    Response: Attitude acquisition sequence starts

T+900s: Fine point attitude achieved
  S5.0204 ST_RECOVERY (both trackers locked)
  aocs.att_error < 0.1°
  Ready to resume mission

Total recovery time: 15 minutes
Imaging resume: +5 minutes
```

---

### Scenario 3: Battery Low in Eclipse (Contingency)

**Context:** Spacecraft in eclipse, battery SoC approaching critical

**Trigger:** S5.0102 BATTERY_SOC_CRITICAL (SoC < 10%)

**Autonomous Actions (S19 rules):**
1. Payload off (S8 func 26, mode=0)
2. FPA cooler off (S8 func 43, on=0)
3. Stage 1 load shedding activated

**If Still Declining:**

```
MCS Monitoring:
  T+0:  SoC = 10% (trigger threshold)
  T+30: SoC = 8% (still declining, eclipse continues 10 min more)
  T+60: SoC = 6% (critical, eclipse exit in 5 min)

MCS Command: Emergency load shedding stage 2
  S8 func_id=24 (EPS_LOAD_SHED_STAGE_2)
  Effect: TTC TX power OFF, AOCS wheels OFF
  Power consumption: 400 W → 150 W

MCS Monitoring:
  T+90:  SoC = 5% (still declining, 3 min to eclipse exit)
        Spacecraft in safe mode (wheels off)
  T+120: Eclipse exit, sunlight begins
        Power generation: 0 → 1200 W
        SoC reverses trend: 5% → 8% → 12% (recovering)

T+300: SoC = 25% (safe, load shedding stages off)
       Resume normal operations
       S8 func_id=0 (AOCS fine point mode)
       S8 func_id=26 (PAYLOAD_SET_MODE, mode=2)
```

---

### Scenario 4: Scheduled Maintenance Window

**Objective:** Perform planned diagnostics and parameter updates

**Timeline:**

```
Pre-maintenance:
  MCS: Schedule S11 activity (system maintenance at T+6 hours)

At T+6h:
  MCS Command 1: Safe mode entry
    S8 func_id=0, mode=1

  MCS Command 2: OBDH diagnostics
    S8 func_id=61 (OBC_DIAGNOSTIC)
    Response: S1 reports, diagnostic results in TM

  MCS Command 3: Update AOCS control gains
    S20 subtype 1 (set parameter)
    param_id: attitude_control_gain
    value: 1.2 (increase by 20%)
    Response: S1 reports

  MCS Command 4: Memory scrub
    S8 func_id=51 (OBC_MEMORY_SCRUB)
    Response: S1.3 (start), S1.5 (progress updates), S1.7 (complete)
    Duration: ~5 minutes

  MCS Command 5: Exit safe mode
    S8 func_id=0, mode=5 (fine point)
    S8 func_id=12 (BEGIN_ACQUISITION)

Verification:
  MCS Command 6: Request test HK
    S3 subtype 27 (HK_AOCS, SID=2)
    Verify new control law active (tighter attitude tracking)
```

---

## Troubleshooting

### MCS Won't Connect to Simulator

**Symptom:** MCS shows "Connection refused" error

**Diagnosis:**
```
1. Check simulator is running
   Terminal: ps aux | grep smo_simulator

2. Check port is correct
   MCS config: port 5678
   Simulator: --port 5678 (must match)

3. Check firewall
   Linux: sudo iptables -L | grep 5678

4. Check network route
   Terminal: netstat -tulpn | grep 5678
   Should show: LISTEN on 127.0.0.1:5678 (localhost)
```

**Solution:**
```bash
# Restart simulator on correct port
python -m smo_simulator --config configs/eosat1/ --port 5678

# In separate terminal, verify connection
telnet localhost 5678
# Should connect, then Ctrl+C to exit
```

---

### Commands Sent But No Response

**Symptom:** S1.1 acceptance report never received

**Diagnosis:**
```
1. Check telemetry is flowing
   MCS: Request HK (S3 subtype 27)
   If no response: Simulator may be hung

2. Check command queue
   MCS: View command history
   Command shows "pending" status?

3. Check simulator logs
   Terminal: tail -f /tmp/smo_simulator.log
   Look for error messages
```

**Solution:**
```bash
# Increase logging verbosity
python -m smo_simulator --config configs/eosat1/ --port 5678 --verbose

# Restart if simulator hung
pkill -f smo_simulator
python -m smo_simulator --config configs/eosat1/ --port 5678
```

---

### Anomalous Telemetry Values

**Symptom:** Battery SoC spiking, attitude quaternion invalid, etc.

**Diagnosis:**
```
1. Check time scale
   Simulator running at --time-scale 10.0?
   Telemetry may look wrong (1s = 10s simulated)

2. Check scenario is loaded
   MCS Contact Schedule shows realistic pass predictions?
   If ground stations always overhead: orbit may not be initialized

3. Check subsystem configuration
   configs/eosat1/subsystems/*.yaml
   Are parameter values realistic?

4. Check failure scenario
   Simulator started with --scenario battery_discharge.yaml?
   Intentional anomalies may cause "weird" data
```

**Solution:**
```bash
# Verify configuration
python -c "
import yaml
with open('configs/eosat1/mission.yaml') as f:
    cfg = yaml.safe_load(f)
print('Orbit:', cfg['orbit']['altitude_km'], 'km')
print('Initial SoC:', cfg['initial_state']['eps.bat_soc'], '%')
"

# Verify scenario (if loaded)
python -m smo_simulator --config configs/eosat1/ --scenario '' \
  # (empty scenario = no failures, nominal only)
```

---

### S19 Rules Not Triggering Autonomously

**Symptom:** Parameter violates S12 threshold, but no S19 action taken

**Diagnosis:**
```
1. Check S19 rules are enabled
   MCS: View S19 configuration
   All rules show "enabled: true"?

2. Check S12 rules detect violation
   MCS: View S12 violations
   Corresponding S5 event generated?

3. Check S19 rule action is valid
   Action func_id exists in S8 commands?

4. Check logs for errors
   Terminal: grep -i "S19" /tmp/smo_simulator.log
```

**Solution:**
```bash
# Reload S19 rules with verbose output
python -m smo_simulator --config configs/eosat1/ --verbose 2>&1 | grep -E "S12|S19|rule"

# Check rule syntax in YAML
python -c "
import yaml
with open('configs/eosat1/monitoring/s19_rules.yaml') as f:
    rules = yaml.safe_load(f)
for rule in rules['s19_rules']:
    print(f\"Rule {rule['ea_id']}: event 0x{rule['event_type']:04x} -> func {rule['action_func_id']}\")
"
```

---

## Training Scenarios

### Beginner: "Hello Spacecraft" (5 minutes)

**Objective:** Send first command and receive S1 verification report

**Steps:**
1. Start simulator (real-time)
2. Connect MCS
3. Send S17 NOOP echo command
4. Observe S1.1 acceptance report in MCS event log
5. Interpret: "Spacecraft received and acknowledged my command"

**Key Learning:** PUS S1 lifecycle (request → acceptance → completion)

---

### Intermediate: "Power Management Crisis" (30 minutes)

**Objective:** Respond to battery low condition

**Setup:**
```bash
python -m smo_simulator --config configs/eosat1/ --time-scale 5.0 \
  --scenario configs/eosat1/scenarios/battery_discharge.yaml
```

**Mission:**
1. Wait for S5.0102 BATTERY_SOC_CRITICAL event (SoC < 10%)
2. Recognize autonomous S19 response (payload off, cooler off)
3. Manually trigger additional load shedding to stabilize SoC
4. Monitor SoC recovery in sunlight
5. Resume normal operations after eclipse exit

**Expected Outcomes:**
- Understand S12/S19 autonomous operations
- Practice load shedding
- Learn power budgeting

---

### Advanced: "Imaging Campaign" (2 hours)

**Objective:** Plan and execute multi-pass imaging mission

**Mission:**
1. Define 5 ocean current targets (in planner)
2. Schedule 5 imaging passes over 24 hours (respecting power/thermal)
3. Upload S11 TC activities to spacecraft
4. Monitor auto-execution (S1 reports, S5 events)
5. Download all images via ground station contacts (S13)
6. Verify image quality (SNR, compression, checksums)

**Scenarios to Handle:**
- Payload storage approaches 95% (pause imaging)
- FPA thermal constraint (cool down period)
- Link margin degradation (manage TX power)
- Ground station contact window (prioritize downloads)

**Expected Outcomes:**
- Master mission planning
- Autonomous S11 scheduling
- S13 large data transfer
- Thermal/power constraint management

---

### Expert: "Anomaly Response Drill" (3 hours)

**Objective:** Diagnose and recover from cascading failures

**Setup:**
```bash
python -m smo_simulator --config configs/eosat1/ --time-scale 10.0 \
  --scenario configs/eosat1/scenarios/cascading_fdir.yaml
```

**Injected Failures:**
1. Star tracker 1 blinded (5 min into scenario)
2. Reaction wheel 1 overspeed (10 min)
3. Battery overheating (15 min)
4. PA overtemp in TTC (20 min)

**Operator Tasks:**
1. Recognize each failure (S5 event interpretation)
2. Assess autonomous response (S19 rule effectiveness)
3. Decide manual override (safety vs. mission)
4. Execute recovery commands
5. Document timeline and decision rationale

**Failure Tree to Uncover:**
```
RW1 overspeed (0x0201)
  ├─ Auto: Disable wheel (S19 rule)
  ├─ Manual: Check momentum saturation (may need desat first)
  └─ Recovery: Re-enable after cooling

ST1 blinded (0x0203)
  ├─ Auto: Switch to ST2 (S19 rule)
  ├─ Manual: If both blinded, safe mode + coarse sun
  └─ Recovery: Wait for sun to move, reacquire

Battery overtemp (0x0109)
  ├─ Auto: Reduce heating loads (payload off)
  ├─ Manual: If persists, reduce AOCS wheel speed bias
  └─ Recovery: Passive cooling in eclipse

PA overtemp (0x0509)
  ├─ Auto: PA shutdown (S19 rule)
  ├─ Manual: Wait 3-5 min for passive cooling
  └─ Recovery: Restart at low power, gradually increase
```

**Expected Outcomes:**
- Master failure diagnosis
- Understand FDIR cascading effects
- Practice manual recovery procedures
- Build confidence in anomaly response

---

## Appendix: Common Commands Quick Reference

### Quickest Commands (< 1 second)

```
S3 subtype 27:  Request one-shot HK
S17 subtype 1:  NOOP echo test
S5 subtype 5:   Enable event type
S8 func 0:      Set AOCS mode
```

### Slowest Commands (> 10 seconds)

```
S8 func 1:      Desaturate wheels (15-25 sec)
S8 func 12:     Attitude acquisition (60+ sec, mode-dependent)
S8 func 47:     Decontamination sequence (5-10 min)
S13 subtype 3:  Large data transfer (block time: 4KB block, ~10 ms)
```

### Most Critical (safety-limiting)

```
S8 func 0:      Mode transition (power, thermal, attitude checks)
S8 func 25:     Emergency load shedding (cascades to payload, AOCS)
S8 func 43:     FPA cooler control (thermal runaway if misused)
S13 subtype 1:  Transfer initiation (blocks other downlink activities)
```

---

## LEOP & Commissioning Sequence

### Overview

The EOSAT-1 Launch and Early Orbit Phase (LEOP) and commissioning sequence takes the spacecraft from separation through to nominal operations over approximately 24 hours across 16 ground station passes. The spacecraft uses a 450 km sun-synchronous orbit at 98° inclination (period ~93.4 min, 15.4 rev/day). Two ground stations provide coverage: Troll (Antarctica, -72.012°S) and Iqaluit (Canada, 63.747°N).

### Spacecraft Phases

The simulator models 7 discrete spacecraft phases:

| Phase | Name | HK SIDs Active | Description |
|-------|------|----------------|-------------|
| 0 | PRE_SEPARATION | None | On launcher, all systems off |
| 1 | SEPARATION_TIMER | None | 30-minute coast timer after separation |
| 2 | INITIAL_POWER_ON | 11 | OBC boots to bootloader |
| 3 | BOOTLOADER_OPS | 11 | Bootloader running, beacon only |
| 4 | LEOP | All | Application booted, full TM, stores online |
| 5 | COMMISSIONING | All | Subsystem checkout in progress |
| 6 | NOMINAL | All | Normal operations |

**Important:** In phases 0–3 only a single minimal SID is active: **SID 11 (Beacon, 30 s)**, emitted from the **bootloader APID** (0x002), not the application APID (0x001). No TM stores exist in bootloader mode, there is no attitude control, and only the EPS/TTC/OBDH subsystems tick. All other HK SIDs, all four onboard TM stores, AOCS/TCS/Payload operations, and the application-APID datastream come online only on the transition to phase 4 (LEOP) triggered by a successful `OBC_BOOT_APP` (func_id 55). An OBC reboot while in phases 4–6 automatically reverts the spacecraft to BOOTLOADER_OPS (beacon only, stores torn down) until the application re-boots.

### Ground Station Pass Schedule (Chennai Launch)

The following timeline assumes a Chennai/Sriharikota launch into a southward-descending initial orbit. Troll (Antarctic) gets first contact; Iqaluit (Arctic) comes into play after ~9 hours.

| Pass | Station | AOS (T+) | Duration | Primary Activity |
|------|---------|----------|----------|------------------|
| P1 | Troll | T+00:21 | ~8 min | Separation monitoring, first signal |
| P2 | Troll | T+01:53 | ~8 min | OBC boot & initial power check |
| P3 | Troll | T+03:25 | ~8 min | EPS commissioning & antenna deploy |
| P4 | Troll | T+04:58 | ~8 min | OBDH commissioning |
| P5 | Troll | T+06:34 | ~8 min | AOCS initial checkout |
| — | GAP | 2.5 h | — | Autonomous detumble (no contact) |
| P6 | Iqaluit | T+09:04 | ~8 min | AOCS commissioning — detumble verify |
| P7 | Iqaluit | T+10:36 | ~8 min | AOCS commissioning — fine pointing |
| P8 | Iqaluit | T+12:09 | ~8 min | TCS commissioning |
| P9 | Iqaluit | T+13:42 | ~8 min | Payload power-on |
| P10 | Troll | T+16:03 | ~8 min | Payload configuration |
| P11 | Troll | T+17:35 | ~8 min | Payload calibration |
| P12 | Iqaluit | T+18:14 | ~8 min | Fine pointing verification |
| P13 | Troll | T+19:08 | ~8 min | High-rate downlink test |
| P14 | Iqaluit | T+19:45 | ~8 min | FDIR arming & system test |
| P15 | Troll | T+20:40 | ~8 min | First operational imaging |
| P16 | Iqaluit | T+21:17 | ~8 min | Nominal operations transition |
| P17-19 | — | T+22–24 | — | Contingency reserve |

### Nominal Commissioning Sequence (73 Steps)

#### Pass 1 — Separation Monitoring (Troll, T+00:21)

**Objective:** Confirm separation, verify beacon, establish two-way link.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 1 | Wait for beacon acquisition | — | SID 11 beacon received on bootloader APID 0x002 |
| 2 | S17.1 NOOP echo | — | S1.1 acceptance |
| 3 | S3.27 request SID 11 (Beacon) | — | OBC alive in bootloader, sw_image=0 |
| 4 | Confirm bootloader APID (0x002) on all TM | — | No application-APID traffic yet |

**GO/NO-GO:** Beacon lock acquired, NOOP echo returned.
**Contingency:** If no beacon — verify antenna deploy timer, check TTC frequency. Retry on P2.

#### Pass 2 — OBC Boot & Initial Power (Troll, T+01:53)

**Objective:** Boot OBC application, enable full telemetry, initial EPS check.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 5 | S8.1 OBC_BOOT_APP | 55 | S1.7 complete, sw_image → 1 |
| 6 | S8.1 OBC_WATCHDOG_ENABLE | 59 | S1.7 complete |
| 7 | S3.5 Enable HK SID 1 (EPS) | — | EPS telemetry streaming |
| 8 | S3.5 Enable HK SID 4 (OBDH) | — | OBDH telemetry streaming |
| 9 | S3.27 request SID 1 (EPS) | — | bat_soc > 70%, bus_voltage > 27V |
| 10 | S3.27 request SID 4 (OBDH) | — | OBC mode=0 (nominal), reboot_cnt=0 |

**GO/NO-GO:** OBC in application mode, EPS healthy (SoC > 60%, bus > 27V).
**Contingency:** If OBC boot fails — S8.1 func_id=56 (BOOT_INHIBIT, inhibit=0), retry func_id=55. If repeated failure, stay in bootloader and escalate.

#### Pass 3 — EPS Commissioning & Antenna Deploy (Troll, T+03:25)

**Objective:** Full EPS checkout, deploy antenna, verify solar arrays.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 11 | S8.1 TTC_DEPLOY_ANTENNA | 69 | S1.7 complete, S5 event |
| 12 | S3.27 request SID 6 (TTC) | — | link_margin improved |
| 13 | S8.1 EPS_SWITCH_LOAD (line 0, on) | 17 | Power line 0 active |
| 14 | S8.1 EPS_SWITCH_LOAD (line 1, on) | 17 | Power line 1 active |
| 15 | S3.27 request SID 1 (EPS) | — | power_gen > 0W (sunlit) |
| 16 | S8.1 TTC_SET_DATA_RATE (high) | 65 | S1.7, rate=64kbps |
| 17 | S8.1 TTC_PA_ON | 66 | PA enabled, link margin up |

**GO/NO-GO:** Antenna deployed, PA on, link margin > 6 dB, solar power generating.
**Contingency:** If antenna deploy fails — retry func_id=69. If persistent, use backup TTC path (func_id=64, redundant transponder).

#### Pass 4 — OBDH Commissioning (Troll, T+04:58)

**Objective:** Verify OBC health, configure watchdog, run diagnostics.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 18 | S8.1 OBC_DIAGNOSTIC | 61 | Diagnostic report nominal |
| 19 | S8.1 OBC_ERROR_LOG | 62 | No critical errors |
| 20 | S8.1 OBC_SET_WATCHDOG_PERIOD | 58 | Period configured |
| 21 | S8.1 OBC_SELECT_BUS (Bus A) | 54 | CAN bus A active |
| 22 | S6.9 Memory CRC check | — | CRC match |
| 23 | S3.5 Enable HK SID 2 (AOCS) | — | AOCS telemetry streaming |
| 24 | S3.5 Enable HK SID 3 (TCS) | — | TCS telemetry streaming |

**GO/NO-GO:** OBC healthy, no memory errors, CAN bus nominal.
**Contingency:** If CAN bus errors — S8.1 func_id=54 (OBC_SELECT_BUS, bus=1) to switch to Bus B. If diagnostics fail — S8.1 func_id=53 (OBC_SWITCH_UNIT) to redundant OBC.

#### Pass 5 — AOCS Initial Checkout (Troll, T+06:34)

**Objective:** Power on attitude sensors, start detumble.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 25 | S3.27 request SID 2 (AOCS) | — | Rates visible (tumbling) |
| 26 | S8.1 AOCS_SET_MODE (detumble) | 0 | mode=2, S5 mode change |
| 27 | S8.1 MTQ_ENABLE | 9 | Magnetorquers active |
| 28 | S8.1 ST1_POWER (on) | 4 | Star tracker 1 powered |
| 29 | S8.1 ST2_POWER (on) | 5 | Star tracker 2 powered |

**GO/NO-GO:** Detumble mode active, rates decreasing, sensors powered.
**Autonomous (between passes):** Detumble continues autonomously for ~2.5 hours until P6. Expect rates to decrease from ~5 deg/s to < 0.5 deg/s.
**Contingency:** If magnetorquers fail — rely on gravity gradient for passive stabilisation; escalate at P6.

#### Pass 6 — AOCS Commissioning: Detumble Verify (Iqaluit, T+09:04)

**Objective:** Verify detumble success, begin attitude acquisition.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 30 | S3.27 request SID 2 (AOCS) | — | Rates < 1 deg/s |
| 31 | S8.1 AOCS_CHECK_MOMENTUM | 11 | Momentum < 50 Nms |
| 32 | S8.1 AOCS_BEGIN_ACQUISITION | 12 | Acquisition sequence started |
| 33 | S8.1 AOCS_SET_MODE (coarse sun) | 0 | mode=3, sun-pointing |

**GO/NO-GO:** Rates < 1 deg/s, sun-pointing achieved.
**Contingency:** If rates still high — S8.1 func_id=1 (DESATURATE), then retry detumble. If ST both blind — use CSS+mag mode (mode=3).

#### Pass 7 — AOCS Commissioning: Fine Pointing (Iqaluit, T+10:36)

**Objective:** Transition to fine pointing, verify attitude accuracy.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 34 | S8.1 ST_SELECT (unit 0) | 6 | Primary ST selected |
| 35 | S8.1 MAG_SELECT (unit 0) | 7 | Primary mag selected |
| 36 | S8.1 AOCS_SET_MODE (nadir) | 0 | mode=4, nadir pointing |
| 37 | S3.27 request SID 2 (AOCS) | — | att_error < 0.5° |
| 38 | S8.1 AOCS_SET_MODE (fine point) | 0 | mode=5, fine pointing |
| 39 | S8.1 AOCS_SET_DEADBAND | 15 | Deadband set to 0.1° |

**GO/NO-GO:** Fine pointing achieved, att_error < 0.1°, wheels balanced.
**Contingency:** If attitude error > 1° — revert to nadir mode, check ST status. S8.1 func_id=13 (GYRO_CALIBRATION) if gyro drift suspected.

#### Pass 8 — TCS Commissioning (Iqaluit, T+12:09)

**Objective:** Verify thermal status, configure heaters, prepare for payload.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 40 | S3.27 request SID 3 (TCS) | — | All zones in range |
| 41 | S8.1 HEATER_BATTERY (on) | 40 | Battery heater active |
| 42 | S8.1 HEATER_OBC (on) | 41 | OBC heater active |
| 43 | S8.1 HEATER_SET_SETPOINT (battery) | 44 | Setpoint configured |
| 44 | S8.1 HEATER_AUTO_MODE (battery) | 45 | Auto control active |
| 45 | S8.1 TCS_GET_THERMAL_MAP | 49 | Full thermal status |
| 46 | S8.1 FPA_COOLER (on) | 43 | Cooler starts cooling FPA |

**GO/NO-GO:** All thermal zones nominal, heaters responding, FPA cooling down.
**Contingency:** If heater stuck — S8.1 func_id=46 (SET_HEATER_DUTY_LIMIT) to limit duty cycle. If FPA cooler fails — delay payload commissioning, investigate on P10.

#### Pass 9 — Payload Power-On (Iqaluit, T+13:42)

**Objective:** Power on payload electronics, verify detector health.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 47 | S3.27 request SID 3 (TCS) | — | FPA temp < -5°C (cooled) |
| 48 | S8.1 PAYLOAD_SET_MODE (standby) | 26 | Payload in standby |
| 49 | S3.5 Enable HK SID 5 (Payload) | — | Payload telemetry streaming |
| 50 | S3.27 request SID 5 (Payload) | — | FPA temp nominal, storage OK |
| 51 | S3.5 Enable HK SID 6 (TTC) | — | TTC telemetry streaming |

**GO/NO-GO:** Payload powered, FPA < -5°C, detector responding.
**Contingency:** If FPA too warm — wait for cooler, retry P10. If payload no response — check EPS power line (func_id=17), toggle power.

#### Pass 10 — Payload Configuration (Troll, T+16:03)

**Objective:** Configure spectral bands, integration times, compression.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 52 | S8.1 PAYLOAD_SET_BAND_CONFIG | 33 | All 4 bands enabled |
| 53 | S8.1 PAYLOAD_SET_INTEGRATION_TIME | 34 | Per-band times set |
| 54 | S8.1 PAYLOAD_SET_GAIN | 35 | Gain nominal |
| 55 | S8.1 PAYLOAD_COOLER_SETPOINT | 36 | Target -8°C |
| 56 | S8.1 PAYLOAD_SET_COMPRESSION | 39 | Auto compression |

**GO/NO-GO:** All bands configured, integration times verified, compression active.
**Contingency:** If band mask wrong — re-send func_id=33 with correct mask. If integration times rejected — check parameter ranges in tc_catalog.yaml.

#### Pass 11 — Payload Calibration (Troll, T+17:35)

**Objective:** Run dark frame + flat field calibration.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 57 | S8.1 PAYLOAD_START_CALIBRATION | 37 | Calibration sequence running |
| 58 | S3.27 request SID 5 (Payload) | — | Calibration in progress |
| 59 | Monitor S5 events | — | CALIBRATION_COMPLETE event |
| 60 | S8.1 PAYLOAD_SET_MODE (imaging) | 26 | mode=2, ready to image |

**GO/NO-GO:** Calibration complete, payload ready for imaging.
**Contingency:** If calibration fails — S8.1 func_id=38 (STOP_CALIBRATION), retry. If repeated failure — proceed without calibration, flag for ground analysis.

#### Pass 12 — Fine Pointing Verification (Iqaluit, T+18:14)

**Objective:** Verify end-to-end pointing chain under payload load.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 61 | S3.27 request SID 2 (AOCS) | — | att_error < 0.1° |
| 62 | S8.1 AOCS_SLEW_TO (test target) | 10 | Slew complete |
| 63 | S3.27 request SID 2 (AOCS) | — | New attitude achieved |

**GO/NO-GO:** Pointing accuracy < 0.1° with payload active.
**Contingency:** If pointing degraded — check wheel momentum (func_id=11), desaturate if needed (func_id=1).

#### Pass 13 — High-Rate Downlink Test (Troll, T+19:08)

**Objective:** Verify high-rate TM downlink, test S13 large data transfer.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 64 | S8.1 TTC_SET_DATA_RATE (high) | 65 | 64 kbps confirmed |
| 65 | S13.1 Initiate test transfer | — | Transfer session open |
| 66 | S13.3 Request data blocks | — | Blocks received, CRC OK |
| 67 | S13.5 End transfer | — | Transfer complete |

**GO/NO-GO:** Downlink at full rate, no block errors.
**Contingency:** If BER high — reduce data rate (func_id=65, rate=0), check link margin. If transfer fails — retry with smaller block size.

#### Pass 14 — FDIR Arming & System Test (Iqaluit, T+19:45)

**Objective:** Enable autonomous fault protection, arm S12/S19 rules.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 68 | S12 Enable all monitoring rules | — | S12 armed |
| 69 | S19 Enable all event-action rules | — | S19 armed |
| 70 | S17.1 NOOP echo (final link check) | — | Echo received |

**GO/NO-GO:** All FDIR rules armed, system healthy across all subsystems.
**Contingency:** If S12/S19 rule activation fails — enable rules individually, check event_catalog.yaml for conflicts.

#### Pass 15 — First Operational Imaging (Troll, T+20:40)

**Objective:** Capture first image, verify end-to-end imaging chain.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 71 | S8.1 PAYLOAD_CAPTURE | 28 | S5 IMAGING_START event |
| 72 | S3.27 request SID 5 (Payload) | — | Image stored, SNR > 20 dB |

**GO/NO-GO:** Image captured and stored, SNR acceptable.
**Contingency:** If image corrupt — check FPA temp, re-run calibration (func_id=37). If capture timeout — check AOCS pointing, verify payload mode=2.

#### Pass 16 — Nominal Operations Transition (Iqaluit, T+21:17)

**Objective:** Declare nominal operations, final configuration.

| Step | Command | func_id | Verification |
|------|---------|---------|--------------|
| 73 | Instructor: set_phase 6 (NOMINAL) | — | Phase = NOMINAL |

**GO/NO-GO:** All subsystems commissioned, all FDIR armed, first image captured.

### Contingency Procedures Summary

#### OBC Boot Failure (Phase 3 stuck)
```
1. S8.1 func_id=56 (BOOT_INHIBIT), inhibit=0    — allow auto-boot
2. S8.1 func_id=55 (OBC_BOOT_APP)                — retry boot
3. If fails 3x → S8.1 func_id=53 (OBC_SWITCH_UNIT)  — switch to redundant OBC
4. If both OBCs fail → remain in bootloader, beacon SID 11 only (bootloader APID), escalate
```

#### Antenna Deploy Failure
```
1. Retry S8.1 func_id=69 (TTC_DEPLOY_ANTENNA)    — 2nd attempt
2. S8.1 func_id=64 (TTC_SWITCH_REDUNDANT)         — try backup transponder
3. If no link improvement → rely on dipole antenna (reduced margin)
4. Continue commissioning with low data rate
```

#### Detumble Failure (rates not decreasing)
```
1. S3.27 SID 2 → check rates and momentum
2. S8.1 func_id=1 (DESATURATE)                    — manual desaturation
3. S8.1 func_id=9 (MTQ_ENABLE), enable=1          — verify magnetorquers on
4. S8.1 func_id=0, mode=2 (detumble)              — restart detumble
5. If rates still high after 3 passes → safe mode, ground analysis
```

#### Power Emergency During Commissioning
```
1. S8.1 func_id=26 (PAYLOAD_SET_MODE), mode=0     — payload off
2. S8.1 func_id=43 (FPA_COOLER), on=0             — cooler off
3. S8.1 func_id=23 (EPS_LOAD_SHED_STAGE_1)        — non-critical loads off
4. Wait for eclipse exit, monitor SoC recovery
5. If SoC < 5% → S8.1 func_id=25 (EPS_LOAD_SHED_STAGE_3)
6. Resume commissioning when SoC > 30%
```

#### Star Tracker Blind (both units)
```
1. S8.1 func_id=0, mode=3 (coarse_sun_pointing)   — CSS+mag mode
2. S8.1 func_id=4 (ST1_POWER), on=0               — power off to clear Sun blind
3. Wait 5 minutes
4. S8.1 func_id=4 (ST1_POWER), on=1               — power back on
5. Monitor S5.0204 (ST_RECOVERY) event
6. Resume fine pointing when ST reacquires
```

### Instructor Commands for LEOP Training

The simulator supports instructor-driven phase control:

```
start_separation    — Begin LEOP from Phase 0 (pre-separation)
set_phase N         — Jump to any phase (0-6)
                      Phase ≤ 3: only SID 11 (Beacon) active on bootloader APID, no stores
                      Phase ≥ 4: application APID, all HK SIDs enabled, stores online
inject_fault NAME   — Inject failure scenario mid-LEOP
set_time_scale N    — Accelerate/decelerate sim time
```

For the full interactive commissioning pass plan with timeline visualisation, see `docs/EOSAT1_Commissioning_Pass_Plan.html`.

---

## Support & Documentation

- **PUS Service Reference:** `/docs/PUS_SERVICE_REFERENCE.md`
- **Architecture Document:** `/docs/architecture.md`
- **Changelog:** `/docs/CHANGELOG.md`
- **Configuration:** `/configs/eosat1/`
- **Simulator Code:** `/packages/smo-simulator/`
- **MCS Code:** `/packages/smo-mcs/`

---

**Version 1.0 — April 2026**

For issues, contact the EOSAT-1 project team.

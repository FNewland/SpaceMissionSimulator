# SMO Simulator Instructor/Operator Guide

## Overview

The SMO Simulator provides an instructor/operator window accessible at the web interface. This guide covers:
- **Scenarios**: Pre-defined training exercises with initial conditions and timed events
- **Breakpoints**: State snapshots for resuming simulation at key moments
- **Parameter Display**: Complete god-mode visibility into all spacecraft parameters

---

## SCENARIOS

### What Are Scenarios?

Scenarios are YAML-defined training exercises that initialize the simulator to a specific spacecraft state and optionally trigger time-tagged or conditional events. Each scenario has:
- **Name**: Unique identifier (e.g., "Post-Separation", "ACS Contingency")
- **Difficulty**: Training level (BASIC, INTERMEDIATE, ADVANCED)
- **Duration**: Scenario length in seconds
- **Briefing**: Operator instructions and mission objectives
- **Initial State**: Spacecraft mode, power status, thermal state (set at scenario start)
- **Events**: Time-tagged or condition-triggered actions (injected failures, telemetry commands, etc.)
- **Expected Responses**: Checklist of trainee actions to complete the scenario

### Scenario Files

Scenarios are stored as YAML files in `configs/eosat1/scenarios/`. Example schema:

```yaml
name: "Post-Separation"
difficulty: BASIC
duration_s: 3600
briefing: |
  Post-separation scenario. Spacecraft has just separated from launch vehicle.
  All subsystems powered down except OBC bootloader. Battery at 95% SoC.
  AOCS in OFF mode with random tumble rates (~1-2 deg/s) from separation impulse.

  Your mission:
  1. Monitor separation timer (30 min countdown)
  2. Boot OBC and establish telecommand link
  3. Deploy antenna and verify uplink
  4. Initiate AOCS detumble sequence
  5. Enable thermal control and nominal power loads

events:
  - time_offset_s: 300
    action: "log_event"
    params:
      message: "Separation timer has 25 minutes remaining"
  - time_offset_s: 1800
    action: "inject_failure"
    params:
      subsystem: "ttc"
      failure: "antenna_mispoint"
      magnitude: 0.5
      onset: "immediate"

expected_responses:
  - { category: monitor, description: "Observe separation timer countdown" }
  - { category: action, description: "Boot OBC when separation timer expires" }
  - { category: action, description: "Deploy antenna via mechanical deployment" }
  - { category: action, description: "Establish command link and verify comms" }
  - { category: action, description: "Initiate AOCS detumble sequence" }
```

### Creating a Scenario

1. Create a new YAML file in `configs/eosat1/scenarios/` (e.g., `my_scenario.yaml`)
2. Define the five required fields: `name`, `difficulty`, `duration_s`, `briefing`, `events`
3. Restart the simulator server (scenarios are loaded at startup)
4. Navigate to the instructor web UI; click "Refresh Scenarios" to load your new scenario

### Using a Scenario

1. **Load Scenarios**: Click the **REFRESH SCENARIOS** button in the left panel
2. **Select**: Click a scenario from the list to view its briefing
3. **Start**: Click **START** button to begin the scenario. The simulator resets to the scenario's initial state and begins executing timed events.
4. **Monitor**: The progress bar shows elapsed time and scenario duration
5. **Stop**: Click **STOP** to end the scenario. Active failures are preserved unless manually cleared.

### Event Actions

Common event action types:

| Action | Purpose | Example |
|--------|---------|---------|
| `log_event` | Record a message to the event log | Training milestone reached |
| `inject_failure` | Inject a subsystem failure at T+offset | Battery cell failure, thruster jam |
| `set_parameter` | Override a parameter value | Force SoC to 10% |
| `command_telecommand` | Send a simulated command | EPS mode change, AOCS detumble |
| `conditional_event` | Trigger if condition met (e.g., `att_error_deg > 5`) | Only if attitude error exceeds threshold |

---

## BREAKPOINTS

### What Are Breakpoints?

Breakpoints capture a complete simulation state snapshot at a moment in time, including:
- Simulation tick count and time
- All spacecraft parameters
- Subsystem internal states (EPS, AOCS, TCS, OBDH, TT&C, Payload, Flight Director)
- Spacecraft mode and FDIR triggers
- Housekeeping timers

Breakpoints are used to:
- **Resume Training**: Pause a scenario, save state, and resume later from that exact point
- **Debug Failures**: Capture state before/after a failure and compare
- **Teach Reference Points**: Show trainees the spacecraft state at key mission milestones

### Saving a Breakpoint

1. In the right panel, enter a name in the **Breakpoint name...** field (e.g., "End of LEOP", "Before AOCS anomaly")
2. Click **SAVE** or press Enter
3. The breakpoint is saved with:
   - Custom name
   - Current simulation time
   - Current tick count
   - Complete state snapshot

### Loading a Breakpoint

Breakpoints are listed in the **Breakpoints** section. To load:
1. Click the breakpoint name in the list (future: add UI button to load)
2. The simulator restores to that exact state
3. Training continues from that point

Currently, breakpoint restoration is handled via the `/api/breakpoint/load` API. Future UI enhancement: add a "LOAD" button next to each saved breakpoint.

### Stored Breakpoints

Breakpoint metadata (name, tick, time) is displayed in the instructor UI. Full state snapshots are available via the `/api/breakpoint/load` endpoint.

---

## PARAMETER DISPLAY

### Overview

The instructor/operator view displays **ALL spacecraft parameters** in real-time from the `/api/instructor/snapshot` endpoint. This provides god-mode visibility into:
- Orbital state (position, velocity, eclipse/contact status)
- All subsystem parameters (EPS, AOCS, TCS, OBDH, TT&C, Payload)
- Internal subsystem models (thermal, power, attitude)
- FDIR state and triggered rules
- Telemetry storage status

### Parameter Cards

Parameters are grouped by subsystem:

#### EPS (Electrical Power System)
- Battery State of Charge (SoC %)
- Bus Voltage, Battery Voltage
- Battery Temperature
- Power Generation, Power Consumption
- Solar Array Currents (A, B panels)
- Mode, Load Shedding Stage

#### AOCS (Attitude & Orbit Control)
- Attitude Error (deg)
- Angular Rates (roll, pitch, yaw in deg/s)
- Reaction Wheel speeds (RPM)
- Sun sensor / Star tracker status
- Control mode (OFF, DETUMBLE, SAFE_POINT, NADIR_POINT, TARGET_TRACK)

#### TCS (Thermal Control)
- OBC, Battery, FPA Temperatures
- Heater status (ON/OFF) for Battery and OBC
- Cooler status (FPA cooler)
- Radiator area utilization
- Thermal margins

#### OBDH (On-Board Data Handling)
- CPU Load (%)
- Memory Used (%)
- Storage Used (%)
- Reboot count
- Mode (NOMINAL, SAFE, BOOTLOADER)

#### TT&C (Telemetry, Tracking & Command)
- Link Status (UP/DOWN)
- RSSI (dBm)
- Link Margin (dB)
- Range (km)
- Elevation (deg)
- Mode, TX/RX rates

#### Payload (Imaging System)
- FPA Temperature
- Storage Used (%)
- Image count
- Mode (OFF, STANDBY, IMAGING, PLAYBACK)
- Cooler power status
- SNR per spectral band

#### Orbit Info
- Latitude, Longitude, Altitude
- Eclipse / Contact indicators
- Semi-major axis, eccentricity, inclination
- RAAN, argument of perigee, true anomaly

### Parameter Search

Use the **Parameter Search** box to quickly find a specific parameter:
- Type a parameter name (e.g., "SoC", "Temperature", "Mode")
- Type a parameter ID (hex, e.g., "0x0143")
- Results highlight matching parameters in green
- Clear search to restore normal view

### Raw Snapshot View

For debugging or when a parameter is not displayed in a standard card, expand the **Raw Snapshot (JSON)** section at the bottom of the page. This shows the complete `/api/instructor/snapshot` response with all parameters, subsystem states, and metadata.

---

## FAILURE INJECTION

### Injecting Failures

From the right panel:
1. Select a **Subsystem** (EPS, AOCS, TCS, OBDH, TT&C, Payload)
2. Select a **Failure Type** (e.g., "solar_array_partial", "rw_bearing", "heater_stuck_on")
3. Set **Magnitude** (0.0 = none, 1.0 = full failure)
4. Choose **Onset** type:
   - **Immediate**: Failure magnitude jumps to target instantly
   - **Gradual**: Magnitude ramps over "Onset Duration" seconds
5. Click **INJECT**

### Clearing Failures

- Click the **CLEAR** button next to an active failure to remove it
- Click **CLEAR ALL FAILURES** to remove all injected failures at once

### Failure Types by Subsystem

| Subsystem | Available Failures |
|-----------|-------------------|
| EPS | solar_array_partial, bat_cell (degradation), bus_short |
| AOCS | rw_bearing, gyro_bias, stuck_thruster |
| TCS | heater_stuck_on, heater_stuck_off, sensor_bias |
| OBDH | cpu_spike, watchdog_reset, memory_error |
| TT&C | primary_failure, antenna_mispoint, frequency_drift |
| Payload | fpa_overheat, memory_full, calibration_error |

---

## SIMULATION CONTROLS

### Time Controls

- **Speed Buttons**: 0.1x, 0.5x, 1.0x, 2.0x, 5.0x, 10.0x
  - 1.0x = real-time
  - >1.0x = accelerated (useful for long scenarios)
  - <1.0x = slowed (useful for detailed observation)

- **Freeze/Resume**: Pause the simulation completely; all parameters freeze
  - Useful for detailed inspection of a particular state
  - Resume to continue from that point

### Link Override

- **Override Passes**: Simulates continuous RF contact (overrides realistic pass schedule)
  - When ON: All telemetry and commanding always available
  - When OFF: Link only available during simulated ground station passes
  - Useful for training independent of ground station passes

### Event Log

The bottom panel shows a chronological log of:
- Scenario start/stop
- Failures injected/cleared
- Parameter changes
- Breakpoints saved
- System messages

Click **Clear Log** to reset the event log.

---

## TROUBLESHOOTING

### Scenarios Not Loading
- Ensure all YAML files in `configs/eosat1/scenarios/` are valid YAML
- Check server logs: `tail -f logs/simulator.log`
- Click **Refresh Scenarios** button in the UI

### Parameters Showing as "--" (No Data)
- Ensure simulator is running and WebSocket is connected (check "WS CONNECTED" status)
- Verify `/api/instructor/snapshot` endpoint is responding: `curl http://localhost:8000/api/instructor/snapshot`
- Some parameters may be N/A in certain spacecraft modes (e.g., payload SNR when payload is OFF)

### Breakpoint Load Fails
- Ensure breakpoint was saved with valid state
- If breakpoint is corrupted, delete it and save a new one
- Check server logs for state restoration errors

### Failure Injection Not Taking Effect
- Verify failure magnitude is > 0.0
- Check that subsystem is in a mode where failure can be observed (e.g., TTC failure requires link to be active)
- Some failures have onset delays; observe over time

---

## QUICK START EXAMPLE

### Training Scenario: "Post-Separation LEOP"

1. **Load Scenario**:
   - Click **REFRESH SCENARIOS** → Select "Post-Separation" → Click **START**

2. **Monitor Initial State**:
   - Observe Battery SoC = 95%, OBC mode = BOOTLOADER, AOCS mode = OFF
   - Angular rates ~1-2 deg/s from separation impulse
   - Watch the progress bar; scenario runs for 1 hour

3. **Trainee Actions** (expected per scenario):
   - Command: OBC boot sequence at T+00:10:00
   - Observe: AOCS mode transitions to DETUMBLE
   - Command: Antenna deployment at T+00:15:00
   - Monitor: Link margin ramps up as antenna angle stabilizes
   - Command: TCS enable nominal heater control
   - Verify: All subsystems operational, battery SoC stable

4. **Save a Breakpoint**:
   - At T+00:30:00 (end of detumble), enter "End AOCS Detumble" as breakpoint name and click SAVE
   - Later sessions can reload this state to practice from that point

5. **Inject a Failure** (to test trainee response):
   - While scenario running, inject "solar_array_partial" (EPS failure, magnitude 0.7)
   - Observe: Power generation drops, battery SoC begins declining
   - Trainee should respond with load shedding commands

6. **Stop Scenario**:
   - Click **STOP** when training complete or timer expires

---

## ADVANCED: API ENDPOINTS

For programmatic access:

- `GET /api/scenarios` → List available scenarios
- `POST /api/command` → Send commands (start/stop scenario, inject failure, etc.)
- `GET /api/instructor/snapshot` → Full state snapshot (all parameters, subsystems, FDIR)
- `POST /api/breakpoint/save` → Save current state
- `POST /api/breakpoint/load` → Restore a saved state

Example: Start a scenario via curl
```bash
curl -X POST http://localhost:8000/api/command \
  -H "Content-Type: application/json" \
  -d '{"type": "start_scenario", "scenario": "Post-Separation"}'
```

---

## GLOSSARY

| Term | Definition |
|------|-----------|
| **Scenario** | YAML-defined training exercise with initial state, timed events, and expected outcomes |
| **Breakpoint** | State snapshot for resuming simulation at a key moment |
| **God-mode** | Instructor visibility unbounded by simulated RF link status |
| **Event** | Time-tagged or condition-triggered action within a scenario |
| **FDIR** | Fault Detection, Isolation, and Recovery system |
| **Onset** | Failure ramp-up style (immediate or gradual) |
| **SoC** | State of Charge (battery percentage) |
| **TM** | Telemetry (downlink data) |
| **TC** | Telecommand (uplink commands) |
| **HK** | Housekeeping data (periodic status packets) |

---

**Version:** 1.0
**Last Updated:** 2026-04-06
**Maintainer:** Instructor/Operator Team

# SMO Simulator Instructor/Operator Guide

## Overview

The instructor console is at **`http://<server>:8080`**. It is the
instructor's port, not the students' — students use the MCS on 9090. This
guide covers:

- **Scenarios**: pre-defined training exercises with initial conditions and timed events
- **Breakpoints**: state snapshots for resuming a simulation at a key moment
- **Parameter display**: god-mode visibility into every spacecraft parameter
- **Failure injection** and the **simulation controls**
- **Reproducibility**: how to make an exercise behave the same way twice
- **Configuration editor** (8080/config): preparing a config set per exercise
- **Reports** (8080/reports): the debrief artefact
- **What the students see**, which changed substantially in this build

Start the suite with `./start.sh --public` for a classroom (loopback is the
default since the 2026-08 build — see `docs/DEPLOYMENT.md`).

---

## WHAT CHANGED IN THE 2026-08 BUILD

Read this section if you taught with an earlier version. Everything here
changes how an exercise behaves or what you can promise a student.

**The simulation is now reproducible.** Same seed + same epoch + same command
script ⇒ a byte-identical parameter trace. Nothing was seeded before, so no
exercise was repeatable and no worked example was checkable. Time acceleration
also no longer changes the physics up to ×60. Read
[`docs/DETERMINISM.md`](DETERMINISM.md) before you set a numerical exercise —
in particular the warning about grading pointing numbers taken above ×60.

**Breakpoints now capture everything.** The S11 on-board schedule, injected
failures, running scenarios, the bootloader phase, monitor enable flags, the
load-shed stage and every edge detector used to be lost on load. "Save →
inject → restore → compare" now works in both directions. See
[`docs/THREADING_AND_STATE.md`](THREADING_AND_STATE.md) §4.

**The power budget closes.** With all nominal systems on, orbit-average
generation exceeds load by a factor of **1.48 at worst (maximum eclipse)** and
**1.95 at best**. The old configuration failed in the *opposite* direction —
an FDIR bus rule tripped at 58 % SoC, inside the nominal band, during
*correct* operations, so students could not tell good planning from bad. The
teaching tension is preserved: leaving the payload on through one eclipse
costs about 46 % depth of discharge, and dumping on every eligible pass erodes
the margin visibly over 6–8 orbits.

**Attitude is real.** There was no attitude dynamics at all before: no inertia
tensor, no torque integration, no sensor that read anything but truth, no
estimator. There is now rigid-body dynamics, four pyramid wheels, magnetorquers,
modelled star tracker / sun sensors / magnetometer / gyro, a 6-state MEKF, and
a genuine difference between COARSE (~5°), NOMINAL (0.065°) and FINE (0.013°).
FINE entry is **refused** unless its preconditions are met, and the payload
**refuses to capture** without it. That chain is the point.

**The spacecraft has propulsion** (funcs 108–118). A ΔV re-fits the TLE, so
passes, eclipse, power and imaging opportunities all move. The engine clamps
time acceleration to ×10 during a burn.

**FDIR actually runs.** The fault-cascade model and the ten recovery
procedures were registered and never started. They execute now, and the FDIR
window's "what is the spacecraft doing about it" half populates.

**The control room grew.** Ground limit checking (51 bands) is live and drives
alarm colouring; there is an alarm *system* with acknowledge-persistence and an
unacked counter; a **TRENDS** page plots any parameter over any window with
limit bands and eclipse/contact shading; command history is archived for
debrief; and there is a situational-awareness header.

**Two new instructor tools:** the configuration editor (8080/config) and the
report generator (8080/reports, also on the MCS).

**The FPA thermal envelope was re-derived** from the corrected model. If you
taught "cool to −12 °C before imaging", stop: the gate is the `fpa_ready`
flag (0x0610), the setpoint is commandable across −20…0 °C, and cool-down
takes about **10 minutes**, not 30. See §FPA below.

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

Breakpoints capture the **complete** mutable simulation state at a moment in
time. "Complete" is now enforced: every stateful component is in a registry,
`BreakpointManager` iterates it, and a component that cannot serialise **fails
the whole save** rather than writing a snapshot that loads without complaint.

Captured:

- Simulation tick count and simulated time
- All spacecraft parameters
- Every subsystem model's internal state
- **Every component's RNG state** — so a load continues the same random stream
  rather than starting a new one
- **The S11 on-board time-tagged schedule** (this used to be destroyed by a
  load, which broke the exact workflow it was built for)
- Injected failures and the running scenario
- The spacecraft phase machine (bootloader vs application), the load-shed
  stage, S12 monitor enable flags *and* delta baselines, S19 definitions,
  in-flight S13 transfers, in-progress S15 dumps and the rate-pacing backlog
- Every `_prev_*` edge detector — so the first tick after a load no longer
  re-fires spurious AOS, eclipse-entry and mode-change events

**Not** captured, deliberately: wall-clock time, the MCS's alarm journal and
verification log (loading a breakpoint rewinds the spacecraft, not the control
room — which is realistic and worth saying to students), and connected
sockets.

Breakpoints are used to:
- **Resume training**: pause a scenario, save, resume later from that point
- **Debug failures**: capture state before and after a failure and compare
- **Teach reference points**: show trainees the state at key mission milestones
- **Build an exercise**: save a known-good starting state and hand every team
  the same spacecraft

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

Save and load are routed through the tick thread's instructor queue, so a
load can no longer land mid-tick and produce a spacecraft state with no
physical cause. Both are also available over REST
(`POST /api/breakpoint/save`, `POST /api/breakpoint/load`) for scripted
exercise setup.

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

- **Speed Buttons**: 0.1x, 0.5x, 1.0x, 2.0x, 5.0x, 10.0x (up to 1000x by API)
  - 1.0x = real-time
  - >1.0x = accelerated (useful for long scenarios)
  - <1.0x = slowed (useful for detailed observation)

**Acceleration and fidelity.** The tick is sub-stepped, so the models see the
same 1-second step at ×60 as at ×1: the battery, thermal, heater, wheel and
S12-delta answers are the same. Two exceptions, both deliberate and both
visible:

| Above | What happens | How you see it |
|---|---|---|
| **×60** | The AOCS fast loop is bypassed. Attitude is *sampled* from the mode's own closed-loop error distribution; wheel momentum, gyro bias and MEKF covariance still integrate exactly | An S5 event, and `aocs.fidelity_mode` = 1 in telemetry |
| **during a burn** | Speed is clamped to ×10 | `PROP_SPEED_CLAMPED` event |

So a ×1000 run is a declared fast-forward, not a silent lie. **Do not grade a
pointing number from a run above ×60.**

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

## REPRODUCIBILITY — MAKING AN EXERCISE BEHAVE TWICE

Three inputs decide a run, and nothing else:

| Input | Where | Shipped value |
|---|---|---|
| Seed | `configs/eosat1/simulation.yaml: seed` | `20260731` |
| Epoch | `configs/eosat1/orbit.yaml: t0_epoch` (selected by `simulation.yaml: epoch: t0`) | fixed |
| Command script | what the operator sends, and *when in sim time* | — |

Practical consequences for teaching:

- **Give each cohort its own seed** if you want a different but equally
  repeatable universe. Change one number in `simulation.yaml` — the
  configuration editor (below) is the safe way to do it, and it records who
  changed it and why.
- **A worked example is now checkable.** Publish the seed with the exercise
  and a student can reproduce your numbers exactly.
- **`epoch: wall`** starts from the wall clock. That is the one supported way
  to make a run non-reproducible; the log says so at start-up. Do not grade
  one.
- **Save a breakpoint to branch an exercise.** RNG state is in the snapshot,
  so a load continues the same stream — two teams loading the same breakpoint
  and doing nothing get identical telemetry.

The full statement of what is and is not reproducible, including the two time
axes (packet CUC is sim time; dump filenames are wall clock, and under
acceleration they are unrelated) is in
[`docs/DETERMINISM.md`](DETERMINISM.md).

---

## CONFIGURATION EDITOR (8080/config)

**Read-write on 8080 only.** The MCS and planner mount the same pages
read-only — the mutating routes do not exist there, they are not merely
disabled — so students can browse configuration and its history, which is
itself worth teaching, and cannot change it.

What it gives you:

- **Schema-validated editing** of everything under `configs/eosat1/`.
  Validation runs schema → range and physical plausibility → **cross-file
  consistency** before it will save.
- **Diff preview before apply.** You see exactly what will change.
- **Real history.** Revisions live in a shadow git repository at
  `workspace/config-history/` whose work-tree is `configs/`, so you get
  diffs, blame and tags without touching the source repository. If `git` is
  absent (the Windows lab installer does not ship it) it falls back to
  snapshots automatically.
- **Named baselines** are annotated tags. "Restore known-good" is one click.
- **Per-exercise variants are branches.** This is the most useful thing the
  feature buys: prepare *the config set for the CERISE ADCS exercise* ahead of
  class and switch between sessions.
- **Audit trailers** on every revision (`SMO-Actor`, `SMO-Role`,
  `SMO-Reason`, `SMO-CrossCheck`, `SMO-RestartScope`) so the audit survives a
  bare clone.

**Safety rule:** while a training scenario is running, apply and revert are
refused unless you force them with a recorded reason. Changing the spacecraft
under a team mid-exercise is almost never what you meant.

**Restart scope.** Some keys take effect on the next tick; some need the
service restarted. The editor tells you which, per key.

---

## REPORTS AND DEBRIEF (8080/reports, 9090/reports)

Three templates: **daily ops review**, **pass report**, **anomaly
investigation**. Output is self-contained HTML — hand the file to a student
directly, or print it.

Two things about the report generator are worth teaching in their own right:

1. **Archive minus live is a real number.** The S15 dump archive is the
   *complete* tape of what the spacecraft played back. The MCS live view is
   lossy: packets released while the link is down are discarded. The
   difference between them is exactly "what the link cost you", and it is a
   section of the report rather than a footnote.
2. **All period selection is CUC-based**, i.e. on spacecraft time. Dump
   *filenames* carry wall clock, and under time acceleration the two axes are
   unrelated — a tool that filtered by file timestamp would return the wrong
   hour of the mission.

Command history is archived, so a post-exercise "what did this team send, when,
and what came back" is now available; that is the artefact a debrief actually
needs.

---

## WHAT THE STUDENTS SEE (MCS, 9090)

Changed enough in this build to be worth a walk-through before a class.

| Feature | Why it matters to you |
|---|---|
| **Ground limit checking** | 51 bands from `mcs/limits.yaml` are enforced on every sample and colour the display. Previously a parameter went red only if the *spacecraft's* S12 said so, and 29 limited parameters have no S12 equivalent — so bus current, per-line currents, gyro temperature and GPS PDOP gave no visual cue at all |
| **Alarm system** | Acknowledge with persistence, unacked count in the header, severity filter that survives a reload, and alarm→procedure linkage ("this alarm ⇒ run CTG-001") |
| **TRENDS page** | Pick parameters, pick a window, overlay limit bands, see eclipse and contact arcs shaded. "Show me this parameter over the last 12 hours" is a real control room's most-used feature and it did not exist |
| **FDIR window** | Now populates on both halves: what the spacecraft detected *and* what it is doing about it |
| **Situational-awareness header** | Time to AOS/LOS with station name, active vs commanded data-rate tier, on-board store fill, unacked alarms, sim speed, and a page-level LIVE/STALE indicator |
| **Command builder** | Typed argument entry in engineering units with the live raw hex shown as you type, and reverse decode when you paste hex. The FPA setpoint is the worked example: `-15.00 degC` → `0x64D7` |
| **Delayed TM** | `9090/dumps` lists every archived dump with packet counts and a decoded summary |

---

## FPA THERMAL — WHAT TO TEACH NOW

The FPA had five different answers across the configuration and a sixth in the
textbook. It now has one, single-sourced in
`configs/eosat1/subsystems/tcs.yaml: fpa_envelope` and derived everywhere else.

| Question | Answer |
|---|---|
| Commandable setpoint range | **−20.0 … 0.0 °C** (`PAYLOAD_COOLER_SETPOINT`, func 36, uint16 centi-kelvin) |
| Default setpoint | **−15.0 °C** |
| Regulation performance | 3σ = **0.5 K** about the setpoint |
| Uncooled resting state | **≈ +11.5 °C** (peak +12.4). This is *normal* — the payload is off between passes |
| Health band (0x0408) | yellow **−22.0 … +14.0 °C**, red **−25.5 … +17.5 °C** |
| Imaging gate | **`fpa_ready` (0x0610)** — settled about the *active* setpoint (−1 K / +5 K) **and** at or below **−3.0 °C** |
| Cool-down | **≈ 10 minutes** to the setpoint band, plus 60 s readiness hysteresis |

**The lesson.** A limit answers "is the hardware healthy". A readiness flag
answers "can I take the picture". Conflating them is what produced five
different answers — including a `[-50, -5]` "warning band" that was really an
imaging band, and a detector low limit that a *legal* command drove straight
through. That is a better systems-engineering lesson than any number in the
table.

Decontamination (`TCS_DECONTAMINATION_START`, func 47) bakes the FPA to
+50 °C and will raise the red-high alarm. That is correct: you have
deliberately taken the detector out of its operating envelope and the console
should say so.


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

- `GET /api/scenarios` → list available scenarios
- `POST /api/command` → send commands (start/stop scenario, inject failure, set speed, save/load breakpoint)
- `GET /api/instructor/snapshot` → full state snapshot (every parameter, every subsystem, FDIR)
- `GET /api/state` → the RF-gated state summary (what the ground can see)
- `POST /api/breakpoint/save`, `POST /api/breakpoint/load` → scripted exercise setup
- `GET /api/scenario/debrief` → scenario debrief export
- `POST /api/ground-time` → ground time offset, for pass rehearsal
- `GET|PUT /api/config/*` → the configuration editor API (read-write on 8080 only)
- `GET|POST /api/reports/*` → the report generator API
- `GET /api/dumps`, `/api/dumps/file?name=…`, `/api/dumps/summary?name=…` → the delayed-TM store

Example: start a scenario via curl
```bash
curl -X POST http://localhost:8080/api/command \
  -H "Content-Type: application/json" \
  -d '{"type": "start_scenario", "scenario": "Post-Separation"}'
```

Every registered route is checked for a consumer by
`tests/test_content/test_endpoint_reachability.py`, so this list cannot
quietly go stale.

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

**Version:** 2.0
**Last Updated:** 2026-08-01
**Maintainer:** Instructor/Operator Team

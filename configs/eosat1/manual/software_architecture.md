# Space Mission Operations Simulator — Software Architecture

## Executive Summary

**SMO** (Space Mission Operations Simulator) is a high-fidelity spacecraft operations simulator implementing the ECSS-E-ST-70-41C (PUS-C) standard for the EOSAT-1 mission. EOSAT-1 is a 6U CubeSat platform designed for ocean current monitoring in a 450 km sun-synchronous orbit.

SMO is designed for four primary use cases:

1. **Operator Training**: Train flight operators in realistic mission phases (LEOP, nominal ops, contingency response)
2. **Commissioning Rehearsal**: Execute mission procedures before spacecraft contact
3. **FDIR Validation**: Test fault detection, isolation, and recovery algorithms in controlled scenarios
4. **Cyberrange Exercises**: Deploy isolated simulations for cybersecurity training

The simulator comprises five microservices:
- **Simulator** (8080): Core physics and PUS-C TC/TM engine
- **MCS** (9090): Web-based mission control center with role-based access
- **Planner** (9091): Mission planning and activity scheduling
- **Delayed TM Viewer** (8092): Archived telemetry browser
- **Orbit Tools** (8093): TLE/state vector conversion utilities

All services are orchestrated by a single shell script entry point with Redis-based inter-process communication and are designed for rapid deployment in air-gapped environments.

---

## System Architecture Overview

### Microservice Topology

```
┌─────────────────────────────────────────────────────────────────┐
│                    Operator Workstations                        │
│              (Web Browser, Desktop, Mobile)                     │
└────────────────────────────┬────────────────────────────────────┘
                             │
                ┌────────────┼────────────┐
                │            │            │
                ▼            ▼            ▼
        ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
        │    MCS       │ │   Planner    │ │Delayed TM V. │
        │   (9090)     │ │   (9091)     │ │   (8092)     │
        │   HTTP/WS    │ │   HTTP/WS    │ │   HTTP/WS    │
        └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
               │                │                │
               └────────────────┼────────────────┘
                                │
                   ┌────────────▼────────────┐
                   │                         │
                   │  Redis Message Bus      │
                   │  (TM/TC/Events)        │
                   │                         │
                   └────────────┬────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
        ▼                       ▼                       ▼
   ┌─────────────┐    ┌─────────────────┐    ┌──────────────┐
   │ Simulator   │    │  Orbit Tools    │    │  start.sh    │
   │   (8080)    │    │    (8093)       │    │ Process Mgmt │
   │             │    │   HTTP/WS       │    │              │
   │  TC:8001 ◄──┼────┤                 │    └──────────────┘
   │  TM:8002 ──►│    └─────────────────┘
   │  I/F:8003   │
   │  HTTP/WS    │
   └─────────────┘
```

### Service Specifications

#### **Simulator (Port 8080)**
Core simulation engine implementing ECSS-E-ST-70-41C (PUS-C).

**Network Interfaces:**
- **TC Uplink (TCP:8001)**: Single-threaded CCSDS space packet receiver; 6-byte primary header + PUS-C secondary header + data payload
- **TM Downlink (TCP:8002)**: Multi-client broadcast stream; real-time packets at 1 Hz (nominal) or configurable tick rate
- **Instructor Interface (TCP:8003)**: JSON-RPC commands for scenario control (pause/resume/reset/inject faults)
- **HTTP (8080)**: RESTful API for state queries, scenario management
- **WebSocket (/ws)**: Real-time telemetry subscription for MCS

**Core Components:**
- SimulationEngine: 1 Hz orchestrator (configurable speed 0.1x–10x real-time)
- ServiceDispatcher: 14 PUS services and 80+ function IDs
- 6 Subsystem Models: EPS, AOCS, TCS, OBDH, TTC, Payload
- FDIR Manager: Multi-tier fault response automation
- Packet Queues: tm_queue, tc_queue, instr_queue, event_queue

#### **Mission Control System (Port 9090)**
Web-based mission control center with single-page application interface.

**Features:**
- Real-time telemetry dashboard (8 subsystem tabs)
- Position-based command authority (6 flight positions)
- Telecommand builder with PUS-C service templates
- Procedure execution and monitoring
- Contact scheduling and power budget calculators
- Event and alarm annunciation
- Integration with archived telemetry viewer

**Protocol:**
- HTTP REST for state polling (100 ms cadence)
- WebSocket subscription to simulator TM stream
- Role-based access control enforced per-request

#### **Mission Planner (Port 9091)**
Activity scheduling and procedure composition tool.

**Responsibilities:**
- Load/edit/validate operational procedures
- Schedule time-tagged TCs (S11)
- Conflict detection and optimization
- Export to TC load files
- Integration with orbit prediction

#### **Delayed Telemetry Viewer (Port 8092)**
Archived telemetry browser for post-pass analysis.

**Functionality:**
- Browse HK packets by time range and SID
- Export CSV/HDF5 data products
- Overlay events and alarms
- Compare scenarios

#### **Orbit Tools (Port 8093)**
TLE/state vector utilities for ephemeris management.

**Capabilities:**
- Parse/validate NORAD TLE sets
- Propagate state vectors (SGP4)
- Compute contact windows for ground stations
- Generate pass predictions

---

## Package Architecture

All packages use **hatchling** build system (pyproject.toml) with pinned dependency versions.

### **smo-common** (Shared Library)

Core protocols, data models, and math libraries.

**Key Modules:**
- `config.py`: YAML configuration loading (pydantic v2 validated schemas)
- `orbit.py`: SGP4 propagation wrapper + sun vector computation
- `codec.py`: CCSDS primary/PUS-C secondary header encoder/decoder
- `telemetry.py`: Parameter catalog and HK packet definitions
- `models.py`: Base SubsystemModel abstract class (interface for subsystems)

**Dependencies:**
```
pydantic>=2.0
pyyaml>=6.0
sgp4>=2.20
numpy>=1.24
```

**Entry Points:** None (library only)

### **smo-simulator** (Core Engine)

Spacecraft simulation physics and PUS-C service implementation.

**Key Modules:**
- `engine.py`: SimulationEngine main loop orchestrator
- `service_dispatcher.py`: 14 PUS services + 80 function IDs (S1, S2, S3, S5, S6, S8, S9, S11, S12, S13, S15, S17, S19, S20)
- `subsystems/`:
  - `eps_basic.py`: Power budget, solar array, battery, 8 power lines
  - `aocs_basic.py`: Attitude control, star trackers, reaction wheels, GPS
  - `tcs_basic.py`: Thermal management, heater control
  - `obdh_basic.py`: On-board data handling, processor model
  - `ttc_basic.py`: Telecom link budget, antenna pointing
  - `payload_basic.py`: Imager operations, data storage
- `fdir.py`: On-board monitoring (S12) + event-action links (S19)
- `server.py`: aiohttp web server + TCP socket handlers
- `scenarios.py`: Pre-built mission scenario loader

**Dependencies:**
```
smo-common
aiohttp>=3.9
```

**Entry Point:**
```
[project.scripts]
smo-simulator = "smo_simulator.server:main"
```

### **smo-mcs** (Mission Control)

Web-based mission control interface.

**Key Modules:**
- `tm_processor.py`: Real-time HK stream decoder and caching
- `tc_manager.py`: Uplink queue and command status tracking
- `procedure_runner.py`: Procedure execution engine with breakpoint support
- `archive.py`: TM packet storage and query interface
- `web_ui.py`: Single-page HTML application (embedded)
- `server.py`: aiohttp RESTful API + WebSocket TM subscription

**Dependencies:**
```
smo-common
aiohttp>=3.9
```

**Entry Point:**
```
[project.scripts]
smo-mcs = "smo_mcs.server:main"
```

### **smo-planner** (Mission Planning)

Activity scheduling and procedure composition.

**Key Modules:**
- `procedures.py`: Procedure definition and validation
- `scheduler.py`: Time-tagged command scheduling (S11 integration)
- `conflict_checker.py`: Scheduling constraint enforcement
- `export.py`: TC load file generation

**Dependencies:**
```
smo-common
aiohttp>=3.9
```

**Entry Point:**
```
[project.scripts]
smo-planner = "smo_planner.server:main"
```

### **smo-gateway** (Network Relay)

Optional inter-segment TM/TC relay for distributed deployments.

**Key Modules:**
- `gateway.py`: TCP relay with filtering and rate-limiting
- `routing.py`: Service-to-host mapping

**Dependencies:**
```
smo-common
```

**Entry Point:**
```
[project.scripts]
smo-gateway = "smo_gateway.gateway:main"
```

---

## Simulation Engine

### SimulationEngine Main Loop

Located in `smo_simulator.engine:SimulationEngine`. Orchestrates the 1 Hz tick cycle:

```python
async def tick(dt_sim):
    # 1. Drain instructor commands (pause/resume/reset/inject faults)
    await self._drain_instr_queue()
    
    # 2. Process incoming TeleCommands via ServiceDispatcher
    await self._drain_tc_queue()
    
    # 3. Execute time-tagged TCs from S11 scheduler
    self._execute_scheduled_tcs()
    
    # 4. Propagate orbit state (SGP4)
    orbit_state = self.orbit.advance(dt_sim)
    
    # 5. Update spacecraft phase state machine
    self._tick_spacecraft_phase(dt_sim)
    
    # 6. Tick each subsystem (EPS, AOCS, TCS, OBDH, TTC, Payload)
    for subsystem in self.subsystems:
        subsystem.tick(dt_sim, orbit_state, self.shared_params)
    
    # 7. S12 Parameter Limit Monitoring
    self._tick_s12_monitoring()
    
    # 8. FDIR Detection/Isolation/Recovery
    self._tick_fdir()
    
    # 9. S5 Event Generation and Reporting
    self._check_subsystem_events()
    
    # 10. Contact/Eclipse Transition Detection
    self._check_transitions(orbit_state)
    
    # 11. Emit HK Reports (per SID schedule)
    await self._emit_hk_packets(dt_sim)
    
    # 12. S15 Paced Telemetry Dump
    await self._tick_dump_emission(dt_sim)
    
    # 13. Injected Failure Progression
    self.failure_manager.tick(dt_sim)
```

### Timing Model

- **dt_sim**: Simulated delta-time per tick = `(1.0 / tick_hz) * sim_speed`
- **tick_hz**: Configurable (default 1 Hz, range 0.1–100 Hz)
- **sim_speed**: Real-time multiplier (0.1x–10x)

Example: At 10 Hz tick rate with 2x speed: dt_sim = 0.1 s * 2.0 = 0.2 s/tick

### Shared Parameter Store

Central state dictionary indexed by parameter ID (hex):

```
shared_params: dict[int, float] = {
    0x0100: 85.5,      # battery_soc (%)
    0x0110: 28.3,      # battery_voltage (V)
    0x0200: 5,         # aocs_mode (enum)
    0x0240: 1,         # st1_status (online)
    ...
}
```

### Packet Queues

Four asynchronous queues:
- **tm_queue**: Outbound telemetry to TM:8002 and Redis
- **tc_queue**: Inbound telecommands from TC:8001
- **instr_queue**: Instructor commands (pause, reset, fault injection)
- **event_queue**: System events (S5 reported events)

---

## PUS-C Service Implementation

ServiceDispatcher routes all incoming TeleCommands (APID 0x0001) to 14 implemented services:

| Service | Name | Subtypes | Description |
|---------|------|----------|-------------|
| S1 | Request Verification | 1,2,3,4,5,6,7 | TC acceptance/acceptance + start/progress/completion success/failure reports |
| S2 | Device Access | 1,5,6 | Equipment on/off/verify status |
| S3 | Housekeeping | 1,2,25 | SID-based HK collection, set properties, HK report definition |
| S5 | Event Reporting | 1,2 | Enable/disable event reporting; event TM packets |
| S6 | Memory Management | 1,2 | Memory check and dump |
| S8 | Function Management | 1,2 | Execute/disable functions (80+ function IDs across all subsystems) |
| S9 | Time Management | 1,2 | Set/report onboard time |
| S11 | TC Scheduling | 4,5 | Time-tagged TC insert/delete/delete all |
| S12 | On-Board Monitoring | 1,2,5 | Define parameter limits, enable/disable monitoring, report limit violations |
| S13 | Large Data Transfer | 1,3 | Initiate/report segmented downlink |
| S15 | On-Board Storage | 1,2 | TM dump request/report |
| S17 | Test | 1 | Connection test (echo/ping) |
| S19 | Event-Action Links | 1,2 | Define/delete autonomous event-triggered TC rules |
| S20 | Parameter Management | 2 | Parameter value query/report |

### S8 Function Dispatch

Function IDs span six subsystem domains with two-stage authorization gate:

**AOCS (0–15):**
- 0: Mode DETUMBLE
- 1: Mode SUN_SAFE
- 2: Mode COARSE_POINTING
- 3: Mode FINE_POINTING
- 4: Mode TARGET_TRACKING
- 5: Enable Reaction Wheels
- 6: Disable Reaction Wheels
- 7: Enable Star Tracker 1
- 8: Disable Star Tracker 1
- 9: Enable Star Tracker 2
- 10: Disable Star Tracker 2
- 11: GPS Sync Time
- 12–15: Reserved

**EPS (16–25):**
- 16: Power Line ON (Payload)
- 17: Power Line OFF (Payload)
- 18: Power Line ON (FPA Cooler)
- 19: Power Line OFF (FPA Cooler)
- 20: Battery Heater ON
- 21: Battery Heater OFF
- 22: OBC Heater ON
- 23: OBC Heater OFF
- 24: Reaction Wheels Heater ON
- 25: Reaction Wheels Heater OFF

**Payload (26–39):**
- 26: Imager Power ON
- 27: Imager Power OFF
- 28: Imager Calibration
- 29–39: Reserved

**TCS (40–49):**
- 40: Thermal Mode NOMINAL
- 41: Thermal Mode SURVIVAL
- 42–49: Reserved

**OBDH (50–62):**
- 50: Reboot OBC
- 51: Reboot Memory
- 52–62: Reserved

**TTC (63–78):**
- 63: Transponder ON
- 64: Transponder OFF
- 65: TX OFF
- 66: TX ON
- 67: RX OFF
- 68: RX ON
- 69: Data Rate 4.8 kbps
- 70: Data Rate 19.2 kbps
- 71: Data Rate 76.8 kbps
- 72–78: Reserved

**Authority Gate:**

```python
def is_function_authorized(func_id, eps_state, subsys_state):
    # Stage 1: Check EPS power line for target subsystem
    subsys = get_subsystem_for_func(func_id)
    power_line = subsystem_to_power_line(subsys)
    
    if power_line_status(power_line) == OFF:
        return False  # EPS power gated
    
    # Stage 2: Check subsystem mode != MODE_OFF
    # Exception: Equipment management commands (func_ids in {0,2,3,4,5,6,7,9,26})
    #            bypass subsystem mode check (can power on equipment)
    
    if func_id not in EQUIPMENT_MGMT_BYPASS_SET:
        if subsys_state.mode == MODE_OFF:
            return False
    
    return True
```

---

## Subsystem Models

Each subsystem inherits from `SubsystemModel` (abstract base in smo-common) and implements `tick(dt, orbit_state, shared_params)`.

### EPS (Electric Power System) — `eps_basic.py`

**State Variables:**
- Battery: SoC [0–100%], voltage [23–34V], current [±50A]
- Solar array: 6 panels with sun-relative attitude weighting
- Power lines: 8 channels (5 switchable, 3 non-switchable)
- Load profile: Current draw per bus
- Thermal mode: Affects battery heater duty cycle

**Key Physics:**
- Sun vector projection per panel: P_gen = P_max * max(0, cos(sun_angle))
- Eclipse detection from orbit_state
- Battery charge/discharge: dSoC/dt = (P_in – P_out) / battery_capacity
- Overcurrent detection per power line: I > I_max → fault flag
- Load shedding cascade: non-critical loads → payload → reaction wheels → OBC heater

**Contact Conditions:**
- TTC TX authorized when: solar_array_power > 150W AND battery_soc > 20%
- TTC RX authorized when: battery_soc > 5%

### AOCS (Attitude and Orbit Control System) — `aocs_basic.py`

**State Variables:**
- Attitude: Quaternion (q0, q1, q2, q3) normalized
- Angular rates: ω [deg/s] in body frame
- Pointing error: Euler angles vs. target
- Mode FSM: 9 modes (OFF, DETUMBLE, SUN_SAFE, COARSE_POINTING, FINE_POINTING, TARGET_TRACKING, SLEW, SAFE, BOOT)

**Hardware Models:**
- Star Trackers: 2× cold redundant; boot timer scales with dt_sim; angular accuracy ±0.05°
- Coarse Sun Sensor: 8 analog channels; accuracy ±3°
- Gyroscopes: Bias drift (~0.1°/hr); noise floor ~0.01°/s
- Reaction Wheels: 4× tetrahedron config; angular momentum saturation; desaturation via magnetorquers
- Magnetometers: 2× redundant; Earth field model
- GPS Receiver: Fix quality (0=no fix, 1=SPS, 2=DGPS); PDOP [1–10]; num satellites [0–12]

**Detumble Mode Logic:**
```python
# Detumble via B-dot law (magnetometer feedback)
if mode == DETUMBLE:
    B_prev = magnetometer_measurement(time - dt)
    B_curr = magnetometer_measurement(time)
    dB_dt = (B_curr - B_prev) / dt
    M_desired = -k_bdot * dB_dt  # Magnetic dipole moment
    set_magnetorquer_moment(M_desired)
    
    # Exit condition: angular rate < 5°/s
    if np.linalg.norm(angular_rates) < np.radians(5):
        mode = SUN_SAFE
```

### TCS (Thermal Control System) — `tcs_basic.py`

**State Variables:**
- Component temperatures: OBC, battery, FPA, 6 external panels [K]
- Radiator model: Effective radiating area per panel
- Heater status: 3 heaters (OBC, battery, reaction wheels) with duty cycle [0–100%]
- Contact mode: Eclipse vs. sunlit

**Thermal Balance:**
```
dT_i/dt = (Q_solar_i + Q_internal_i + Q_heater_i - Q_rad_i) / (m_i * c_p_i)

Q_rad_i = σ * ε * A_rad * (T_i^4 - T_space^4)  # Stefan-Boltzmann
Q_solar_i = α * G_solar * A_panel * cos(sun_angle)
Q_internal_i = P_dissipation(subsystem_activity)
```

**Constraints:**
- OBC operating range: 0–50°C; safe mode activation if T > 60°C
- Battery range: −20–50°C; heater kicks in at T < 0°C
- FPA sensitive: 0–30°C optimal; degradation above 35°C

### OBDH (On-Board Data Handler) — `obdh_basic.py`

**State Variables:**
- Processor load: [0–100%]
- Memory usage: Total, free, occupied [bytes]
- Uptime counter: [seconds]
- Mode FSM: OFF, BOOT, NOMINAL, SAFE
- Software image: Version, CRC, boot flag

**Boot State Machine:**
```
OFF → BOOT (bootloader 30 s) → check software CRC
     ↓ CRC OK
   NOMINAL ← SAFE (if thermal safe mode triggered)
     ↓ processor load > 90% for 60 s
   SAFE (reduced TM rate, minimize power)
```

**Memory Model:**
- Total: 512 MB (flash) + 128 MB (RAM)
- Parameter cache: 2 KB
- HK SID definitions: 3 KB
- Event log: Circular buffer, 64 KB
- TC scheduler queue: Up to 100 time-tagged TCs

### TTC (TeleTeleCommunications) — `ttc_basic.py`

**State Variables:**
- Link budget: RSSI [dBm], lock state (locked/unlocked), data rate [kbps]
- Modulation: QPSK (4.8 kbps), OQPSK (19.2 kbps), OQPSK+FEC (76.8 kbps)
- Antenna pointing loss: Range [0–20 dB] based on spacecraft attitude relative to ground station
- Frame sync: Acquisition timeout 10 s; loss of lock after 2 consecutive frame errors

**Contact Detection:**
```python
# From orbit_state, compute ground station elevation angle
elevation = compute_elevation(orbit_state, ground_station_latlon)

# TTC transmit enabled if:
#   (1) elevation > 5° (radio horizon)
#   (2) EPS power > 150W (from EPS model)
#   (3) TTC mode != OFF

if elevation > 5:
    contact_active = True
    # Compute link budget (simplified)
    path_loss = -32.5 - 20*log10(range_km) - 20*log10(freq_mhz)
    antenna_gain_gs = 15 dBi
    antenna_gain_sc = 0 + pointing_loss  # Omni, but pointing loss
    RSSI = tx_power + path_loss + antenna_gain_gs + antenna_gain_sc
    lock = (RSSI > threshold_for_modulation)
else:
    contact_active = False
    lock = False
```

### Payload (Imager) — `payload_basic.py`

**State Variables:**
- Imager mode: OFF, STANDBY, IMAGING, CALIBRATION
- Image count: Total images acquired
- Data volume: [GB] on solid-state recorder
- Storage utilization: [0–100%]
- FPA temperature: Coupled to TCS model

**Operational Constraints:**
- Imaging only when: mode = IMAGING AND pointing_error < 0.5° AND sunlit (not in eclipse)
- Data generation rate: 2 GB/hour during imaging
- Max storage: 128 GB; payload disabled when utilization > 95%
- Calibration cycle: Recommended every 7 days; updates in-flight calibration table

---

## Telemetry Architecture

### Housekeeping SIDs (S3)

| SID | Subsystem | Rate | Parameters |
|-----|-----------|------|-----------|
| 1 | EPS | 1 Hz | Battery SoC, voltage, current, solar power per panel, power line status, load shedding state |
| 2 | AOCS | 1 Hz | Attitude quaternion, angular rates, pointing error, mode, wheel speeds, star tracker status, GPS state |
| 3 | TCS | 1 Hz | Component temperatures, radiator status, heater duty cycles, thermal mode |
| 4 | Platform | 1 Hz | Spacecraft phase, uptime, orbit position/velocity, eclipse flag, ground station contact pass |
| 5 | Payload | 1 Hz | Imager mode, image count, data volume, storage utilization, FPA temperature |
| 6 | TTC | 1 Hz | RSSI, lock state, data rate, antenna pointing loss, contact elevation, modulation mode |
| 11 | Beacon | 10 Hz | Battery SoC, mode, time, spacecraft health summary (paced downlink during contact) |

### Parameter Catalog

120+ parameters defined in `configs/eosat1/telemetry/parameters.yaml`. Sample entries:

```yaml
parameters:
  0x0100:
    name: battery_soc
    unit: "%"
    min: 0
    max: 100
    scale: 0.1
  0x0110:
    name: battery_voltage
    unit: "V"
    min: 23
    max: 34
    scale: 0.01
  0x0200:
    name: aocs_mode
    unit: "enum"
    values:
      0: OFF
      1: DETUMBLE
      2: SUN_SAFE
      5: FINE_POINTING
  0x0240:
    name: st1_status
    unit: "enum"
    values:
      0: OFFLINE
      1: ONLINE
      2: FAILED
```

### HK Packet Structures

Defined in `configs/eosat1/telemetry/hk_structures.yaml`:

```yaml
SID_1_EPS:
  parameters:
    - {param_id: 0x0100, offset: 0, bytes: 1}  # battery_soc
    - {param_id: 0x0110, offset: 1, bytes: 2}  # battery_voltage
    - {param_id: 0x0120, offset: 3, bytes: 2}  # battery_current
    - {param_id: 0x0140, offset: 5, bytes: 1}  # solar_power
    - {param_id: 0x0150, offset: 6, bytes: 1}  # power_line_status (bitmask)
  pack_format: ">BHhBBB"  # struct.pack format
  total_length: 8
```

### TM Packet Format

```
Byte Offset  |  Field                      |  Length (bytes)
─────────────┼─────────────────────────────┼─────────────────
   0–5       |  CCSDS Primary Header       |  6
   6–9       |  PUS-C Secondary Header     |  4
   10–(N-1)  |  Data Field (HK SID data)   |  Variable
   N–(N+1)   |  Error Control (CRC)        |  2
```

Primary Header: APID (11 bits), sequence counter, length
Secondary Header: PUS version, service, subtype, time

---

## FDIR Architecture

### On-Board Monitoring (S12)

Parameter limit definitions in `configs/eosat1/monitoring/limits.yaml`:

```yaml
0x0100_battery_soc:  # Parameter ID
  warning_high: 95
  warning_low: 20
  alarm_high: 100
  alarm_low: 5
  action_alarm_low: "LOAD_SHEDDING"
  
0x0110_battery_voltage:
  warning_high: 32.5
  warning_low: 26.0
  alarm_high: 34.0
  alarm_low: 23.5
  action_alarm_low: "SAFE_MODE"
```

Each HK tick, compare all parameters to thresholds; generate S5 events on transitions.

### Event-Action Links (S19)

Autonomous TC injection rules defined in `configs/eosat1/monitoring/event_actions.yaml`:

```yaml
event_52:  # Battery SoC Low Alarm
  actions:
    - send_tc: S8.1 func_id=20   # Battery Heater OFF
    - send_tc: S8.1 func_id=17   # Payload Power OFF
    - send_tc: S8.1 func_id=19   # FPA Cooler Power OFF
  delay_sec: 2
  max_repeat: 1
  
event_61:  # Temperature High Alarm (OBC)
  actions:
    - send_tc: S8.1 func_id=41   # Thermal Mode SURVIVAL
    - send_tc: S8.1 func_id=1    # AOCS Mode SUN_SAFE
  delay_sec: 5
  max_repeat: 3
```

### Advanced FDIR Manager

Multi-tier response strategy in `fdir.py`:

```python
def tick_fdir_advanced(dt_sim):
    # Tier 1: Warning thresholds
    for param_id, value in shared_params.items():
        if warning_low <= value <= warning_high:
            log_event(WARNING, param_id)  # S5 event
    
    # Tier 2: Isolation (equipment shutdown)
    for param_id, value in shared_params.items():
        if value > alarm_high or value < alarm_low:
            execute_isolation_procedure(param_id)
            log_event(ALARM, param_id)
    
    # Tier 3: Recovery (mode transition)
    if num_alarms > THRESHOLD:
        transition_to_safe_mode()
        send_event_report(S5.1)
    
    # Tier 4: Emergency (safe mode with minimal load)
    if battery_soc < 5 or any_structural_failure:
        activate_emergency_mode()
```

---

## Configuration Schema

Configuration hierarchy rooted at `configs/eosat1/`:

```
configs/eosat1/
├── mission.yaml               # Spacecraft identity, APID, PUS version
├── orbit.yaml                 # TLE, ground stations (Iqaluit, Troll)
├── subsystems/
│   ├── eps.yaml              # EPS model parameters
│   ├── aocs.yaml             # AOCS initial conditions
│   ├── tcs.yaml              # Thermal constants
│   ├── obdh.yaml             # Processor specs
│   ├── ttc.yaml              # Link budget params
│   └── payload.yaml          # Imager specifications
├── telemetry/
│   ├── parameters.yaml       # 120+ parameter definitions
│   ├── hk_structures.yaml    # SID packet layouts
│   └── event_catalog.yaml    # 100+ event definitions
├── commands/
│   └── tc_catalog.yaml       # TC definitions for all 14 services
├── monitoring/
│   ├── limits.yaml           # S12 parameter thresholds
│   └── event_actions.yaml    # S19 event-action rules
├── mcs/
│   └── positions.yaml        # Role-based access control (func_id ranges)
├── procedures/               # 51 operational procedures
│   ├── leop/
│   ├── nominal/
│   ├── contingency/
│   └── emergency/
├── scenarios/                # 29 pre-configured scenarios
│   ├── scenario_001_detumble.yaml
│   ├── scenario_002_sun_safe.yaml
│   └── ...
└── manual/                   # 14 operator manual sections
    ├── software_architecture.md
    ├── ops_procedures.md
    └── ...
```

### mission.yaml

```yaml
spacecraft:
  name: EOSAT-1
  form_factor: 6U_CubeSat
  dry_mass_kg: 6.5
  apid: 0x0001
  
pus_version: 3.0  # ECSS-E-ST-70-41C
time_epoch: "2024-01-01T00:00:00Z"

operators:
  flight_director:
    description: "Mission authority; full command access"
  eps_tcs:
    description: "Power and thermal subsystem ops"
  aocs:
    description: "Attitude control and pointing"
  ttc:
    description: "Communications and link budget"
  payload_ops:
    description: "Imager and data collection"
  fdir_systems:
    description: "Fault isolation and recovery"
```

---

## MCS Web Interface

Single-page application (SPA) with tabbed navigation and real-time telemetry subscription.

### Dashboard Tabs

1. **Overview**: Orbit ground track, contact pass prediction, spacecraft phase (BOOT/LEOP/NOMINAL), uptime
2. **EPS**: Battery state-of-charge graph, solar array power per panel, power line switch matrix, load shedding state
3. **AOCS**: Attitude quaternion/Euler angles, angular rate vector, pointing error, mode selector, reaction wheel speeds, star tracker status (cold/warm/ready), GPS position/velocity
4. **TCS**: Temperature telemetry (OBC/battery/FPA/panels), heater duty cycles, thermal mode selector
5. **TTC**: RSSI time-series, lock state indicator, data rate selector, contact elevation pass plot
6. **Payload**: Imager mode selector, image count, storage utilization bar, FPA temperature
7. **OBDH**: Processor load, memory utilization, software version, uptime counter
8. **Commanding**: Position-based TC builder with S8.1 function selector, raw PUS packet composer
9. **Procedures**: Load operational procedure file, execute step-by-step, set breakpoints, view event log
10. **On-Demand**: Contact scheduler, power budget calculator, FDIR alarm viewer
11. **Manual**: Integrated operator documentation (14 sections)

### Position-Based Access Control

Six flight positions with function_id restrictions:

```yaml
flight_director:
  func_ids_allowed: [0–78]      # Unrestricted
eps_tcs:
  func_ids_allowed: [16–25, 40–49]   # EPS + TCS only
aocs:
  func_ids_allowed: [0–15]       # AOCS only
ttc:
  func_ids_allowed: [63–78]      # TTC only
payload_ops:
  func_ids_allowed: [26–39]      # Payload only
fdir_systems:
  func_ids_allowed: [all with audit log]  # Any, but logged
```

### WebSocket Subscription

MCS establishes WS connection to simulator `/ws` endpoint:

```javascript
ws = new WebSocket("ws://localhost:8080/ws");

ws.onmessage = (event) => {
    const tm_packet = JSON.parse(event.data);
    // Parse CCSDS + PUS-C headers
    // Extract parameters from HK data field
    // Update dashboard widgets
};
```

---

## Network Protocol

### TC Uplink (TCP:8001)

Raw CCSDS space packet stream. Single-threaded server maintains 1-second receive timeout.

**Packet Format:**
```
Byte 0–1:  CCSDS Primary Header[0–15] (version=0, type=1 [TC], apid=0x0001)
Byte 2–3:  Sequence counter + length – 1
Byte 4–5:  PUS secondary header (svc_type, svc_subtype)
Byte 6–(N-2):  Service data field
Byte (N-1)–N:  Error control (CRC-16 CCITT)
```

### TM Downlink (TCP:8002)

Multi-client broadcast. All connected receivers get identical packet stream.

**Packet Format:** Identical to TC uplink (space packet structure).

**Output Rate:**
- Nominal: 1 HK packet/sec per SID at 1 Hz tick rate
- Contact mode: Beacon packets at 10 Hz
- Paced dump (S15): Configurable dump rate (e.g., 50 kbps)

### Instructor Interface (TCP:8003)

JSON-RPC commands for scenario control.

**Command Examples:**
```json
{"method": "pause", "params": {}}
{"method": "resume", "params": {}}
{"method": "reset", "params": {"scenario": "scenario_001"}}
{"method": "inject_fault", "params": {"subsystem": "eps", "fault_type": "battery_disconnect"}}
{"method": "set_sim_speed", "params": {"speed": 2.0}}
```

### HTTP REST (Port 8080)

```
GET  /api/state                   → Current spacecraft state (JSON)
GET  /api/orbits                  → Ephemeris query
POST /api/tc/send                 → Submit TC (raw bytes)
GET  /api/scenarios               → List pre-loaded scenarios
POST /api/scenarios/{id}/load     → Load scenario
GET  /api/hk/{sid}                → Query HK by SID
GET  /api/events                  → Event log
```

### WebSocket (/ws)

Real-time TM subscription (JSON-encoded HK packets).

---

## Test Architecture

**Test Suite:** 64 test files, pytest + pytest-asyncio framework.

**Coverage Areas:**
- Unit tests: Each subsystem model (6 modules × 4–6 tests each)
- Integration tests: End-to-end commissioning (LEOP → nominal contact)
- Service dispatch tests: All 14 PUS services (80+ function IDs)
- MCS synchronization: Real-time TM ingestion and dashboard updates
- Scenario loading: 29 pre-configured missions
- FDIR logic: Event-action rule evaluation

**Example Test:**
```python
@pytest.mark.asyncio
async def test_eps_load_shedding_on_low_battery():
    """S12 alarm → S8.1 load shedding TC injection via S19 event-action."""
    engine = SimulationEngine(scenario="battery_discharge")
    
    # Simulate 3 hours of eclipse
    for _ in range(3 * 3600):
        await engine.tick()
    
    # Verify battery SoC < 5%
    assert engine.shared_params[0x0100] < 5
    
    # Verify S19 triggered load shedding
    tc_queue_cmds = engine.tc_queue.get_all()
    assert any(tc.service == 8 and tc.func_id == 17 for tc in tc_queue_cmds)
```

---

## Deployment Modes

### Standalone (Single Host)

All services on localhost via `start.sh`:

```bash
./start.sh  # Launches:
#  - smo-simulator on 8080
#  - smo-mcs on 9090
#  - smo-planner on 9091
#  - Delayed TM Viewer on 8092
#  - Orbit Tools on 8093
#  - Redis (if not running)
```

**Use Case:** Training, development, commissioning rehearsal.

### Distributed (Multi-Host)

smo-gateway enables TM/TC relay across network segments.

**Topology:**
```
[TC Generator] → [Gateway:8001] → [Simulator:8001]
[Simulator:8002] → [Gateway:8002] → [MCS:8002 multi-cast]
```

**Configuration:**
```yaml
gateway:
  listen_tc_port: 8001
  forward_tc_to: simulator_host:8001
  listen_tm_port: 8002
  forward_tm_to: [mcs_host:8002, backup_mcs_host:8002]
```

**Use Case:** Geographically separated control centers (primary + backup).

### Cyberrange (Air-Gapped)

Isolated deployment with offline wheel packages.

**Build:**
```bash
pip install --offline smo-common*.whl smo-simulator*.whl smo-mcs*.whl
python -m venv /cyberrange/smo
source /cyberrange/smo/bin/activate
./start.sh --data-root /cyberrange/data
```

**Use Case:** Cybersecurity training without network connectivity.

### Development (Editable Installs)

For rapid iteration:

```bash
pip install -e ./smo-common
pip install -e ./smo-simulator
pip install -e ./smo-mcs
```

**Advantages:** Code changes reflected immediately (no rebuild); pytest integration.

---

## Conclusion

The Space Mission Operations Simulator provides a modular, high-fidelity architecture for spacecraft operations training and mission validation. Its microservice design, comprehensive PUS-C implementation, and rich configuration schema enable rapid scenario composition and deployment across diverse operational environments.

Key architectural strengths:
1. **Separation of Concerns**: Physics (simulator), UI (MCS), planning (Planner), networking (Gateway)
2. **Standards Compliance**: Implements ECSS-E-ST-70-41C (PUS-C) and CCSDS space packet format
3. **Extensibility**: Base SubsystemModel class enables custom subsystem implementations
4. **Testability**: Comprehensive test suite with pytest-asyncio for async validation
5. **Deployability**: Single-command launch via start.sh; isolated cyberrange mode supported

---

**Document Version:** 1.0  
**Last Updated:** 2026-04-09  
**Architecture Owner:** SMO Development Team

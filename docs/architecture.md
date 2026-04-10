# Space Mission Operations Suite — Architecture

## Overview
The SMO suite consists of 4 main tools plus shared infrastructure:

1. **Spacecraft Simulator** (`smo-simulator`) — config-driven spacecraft simulation
2. **Mission Control System** (`smo-mcs`) — operator displays and commanding
3. **Mission Planning Tool** (`smo-planner`) — orbit prediction and scheduling
4. **Network Gateway** (`smo-gateway`) — TM/TC relay for distributed deployment

## Shared Infrastructure
- **Common Library** (`smo-common`) — protocol, config schemas, orbit utilities
- **EOSAT-1 Config** (`configs/eosat1/`) — complete mission configuration

## Key Design Principles
- **Config-driven**: All spacecraft parameters, displays, and behaviors defined in YAML
- **Plugin architecture**: Subsystem models loaded via Python entry points
- **ECSS PUS-C compliant**: Standard space packet protocol throughout
- **Distributed-ready**: Gateway enables multi-machine deployment
- **Autonomous Operations**: S12/S19 framework for monitoring and event-triggered responses
- **Full FDIR Support**: Fault detection, isolation, recovery with cascading autonomy

## Network Architecture
```
Single machine:  [Simulator] <-TCP-> [MCS] <-WebSocket-> [Browser]
Distributed:     [Simulator] <-TCP-> [Gateway] <-TCP-> [MCS instances]
```

## Technology Stack
- Python 3.11+, Pydantic v2, aiohttp, asyncio, sgp4, numpy

---

## PUS Service Coverage

The simulator implements the following ECSS PUS-C services:

| Service | Name | Status | Notes |
|---------|------|--------|-------|
| **S1** | Request Verification | Complete | Full lifecycle: accepted, exec_start, progress, completed, failed |
| **S3** | Housekeeping | Complete | 6 data structures (HK_AOCS, HK_EPS, HK_TCS, HK_TTC, HK_OBDH, HK_Payload) |
| **S5** | Event Reporting | Complete | 120+ events defined, selectively enabled per subsystem |
| **S6** | Memory Management | Partial | Load/dump/check implemented (simplified, no real memory model) |
| **S8** | Function Management | Complete | 50+ functions implemented across all subsystems |
| **S9** | Time Management | Complete | OBDH time sync and TTC timestamp correlation |
| **S11** | Activity Scheduling | Complete | TC scheduling with timing constraints |
| **S12** | On-Board Monitoring | Complete | 25+ monitoring rules with absolute/delta checks |
| **S13** | Large Data Transfer | Complete | Payload image downlink with block retrieval |
| **S15** | TM Storage | Complete | 4-store circular/linear buffer model with S15.2/3/4/5 |
| **S17** | Connection Test | Basic | AOS/LOS signals and NOOP echo test |
| **S19** | Event-Action | Complete | 20+ rules for autonomous fault response and power management |
| **S20** | Parameter Management | Complete | Gain/offset updates, telecommand parameter injection |

---

## Subsystem Implementation Status

### 1. AOCS (Attitude & Orbit Control System)

**Physics Models:**
- Quaternion-based attitude dynamics with momentum exchange
- 4-wheel reaction wheel system: 0-5500 RPM with bearing thermal degradation
- Dual redundant star trackers (multi-star acquisition)
- 6-head coarse sun sensor array (composite sun vector)
- Dual magnetometers (primary/redundant) with B-dot control
- GPS receiver with 2D/3D/3D+velocity fix modes
- Gyroscope assembly with bias drift modeling

**Operational Modes (9 total):**
- Off, Safe Boot, Detumble (B-dot), Coarse Sun Pointing, Nominal Nadir
- Fine Point (star tracker lock), Slew (attitude maneuver), Desaturation, Eclipse Propagate

**Telemetry Parameters:** 45+ HK parameters (quaternion, rates, wheel speeds/temps, magnetometer, GPS, CSS, attitude error)

**S8 Commands:** 16 functions
- Mode control, wheel enable/disable, desaturation, ST1/ST2 power, magnetometer/ST selection
- **NEW**: SLEW_TO (quaternion slew with rate control), CHECK_MOMENTUM, BEGIN_ACQUISITION (automated sequence)
- **NEW**: GYRO_CALIBRATION (bias reset), RW_RAMP_DOWN (graceful spindown), SET_DEADBAND

**S12 Rules:** 5 rules
- Reaction wheel overspeed (per wheel, >5000 RPM)
- Total momentum saturation (>90% of max)
- Attitude error high (>1.0 degree)
- Star tracker blind detection (status > 2)

**S19 Rules:** 5 rules
- Momentum saturation → Trigger desaturation
- ST1 blind → Switch to ST2
- ST2 blind → ST fallback
- Attitude error high → Safe mode
- RW overspeed → Disable specific wheel

**Critical New Features (April 2026):**
- Automated slew command execution with quaternion targeting
- Momentum status checking and warning generation
- Attitude acquisition sequence automation
- Gyro bias calibration capability

---

### 2. EPS (Electrical Power System)

**Physics Models:**
- Battery pack (SoC, thermal model, cycle tracking, health degradation)
- 6 body-mounted solar panels (attitude-coupled, aging degradation)
- Power distribution unit with switchable load lines
- 3-stage load shedding (priority-based)
- Bus voltage regulation and overcurrent protection
- Charge regulator and power margin computation

**Bus Architecture:**
- Main bus (28V nominal), PDM with 8 power lines (OBC/TTC_RX/TTC_TX/Payload/FPA/Heater_Bat/Heater_OBC/AOCS)
- Overcurrent trip thresholds per line
- Separation timer for deployment phase

**Telemetry Parameters:** 35+ HK parameters (battery SoC/temp/voltage, solar currents, bus voltage, load states, per-line currents)

**S8 Commands:** 10 functions
- Payload mode (off/standby/imaging), TTC mode, thermal heater control
- **NEW**: Load line switching (EPS_SWITCH_LOAD), Battery heater setpoint, Charge rate override
- **NEW**: Solar array drive control, Emergency load shed, Bus isolation

**S12 Rules:** 8 rules
- Battery SoC < 20% (warning)
- Battery SoC < 10% (critical)
- Bus voltage < 27V (warning), < 25V (critical)
- Battery current overcurrent (>15A)
- Battery temperature high (>45°C), low (<-5°C)
- Solar array degradation (low current during sunlit)

**S19 Rules:** 4 rules
- Bus undervoltage → Payload off (load shedding)
- Battery SoC critical → FPA cooler off
- Battery SoC critical → Payload power off
- Battery overtemp → TTC TX off (reduce power)

**Critical New Features (April 2026):**
- S12/S19 framework fully configured (8+4 rules)
- Per-line current telemetry and switching control
- Battery health percentage tracking
- Load shedding stage status in telemetry
- Power margin computation (gen - cons)

---

### 3. TCS (Thermal Control System)

**Physics Models:**
- 10-zone lumped-mass thermal model
- Conduction between adjacent zones
- Solar heating and IR radiation
- Eclipse coupling (zero solar, reduced albedo)
- Heater thermostat control with setpoint override
- Temperature-dependent component performance

**Controlled Heaters:**
- Battery pack heater
- OBC electronics heater
- Decontamination heating sequence capability

**Telemetry Parameters:** 14+ HK parameters (zone temps: battery, OBC, FPA, structure panels, radiator)

**S8 Commands:** 4 functions
- Heater on/off, thermal mode selection
- **NEW**: Heater setpoint adjustment, Decontamination sequence, Cooler control, Thermal zone priority

**S12 Rules:** 8 rules
- Zone overtemp warning/alarm (per zone, configurable thresholds)
- Zone undertemp warning/alarm (per zone)
- FPA operational range (−3°C to −15°C)
- Thermal runaway detection (dT/dt > threshold)

**S19 Rules:** 3 rules
- FPA overtemp → Payload off
- Battery overtemp → Transponder TX off
- OBC overtemp → OBC safe mode

**Critical New Features (April 2026):**
- S12/S19 rules fully wired (8+3 rules)
- Decontamination heater command support
- FPA thermal readiness events generated
- Thermal zone monitoring with per-zone alarms

---

### 4. TT&C (Telemetry, Tracking & Command)

**Physics Models:**
- Dual transponder (primary/redundant) with mode switching
- Friis free-space path loss link budget
- Eb/N0 to BER mapping (BPSK modulation)
- Lock acquisition sequence: carrier → bit → frame with realistic delays
- Power amplifier thermal model (overtemp shutdown at 65°C)
- Doppler shift estimation and correction
- AGC and receiver signal strength monitoring

**Link States:**
- Carrier lock/unlock, bit sync, frame sync, ranging acquisition

**Telemetry Parameters:** 22+ HK parameters (link margin, BER, RSSI, AGC, PA temp, Doppler, bytes TX/RX)

**S8 Commands:** 9 functions
- Mode selection, PA control, antenna deploy, beacon mode
- **NEW**: Frequency selection (UL/DL bands), Modulation mode (BPSK/QPSK)
- **NEW**: Receiver gain control, Ranging start/stop, Coherent/non-coherent mode

**S12 Rules:** 4 rules
- Link margin warning (Eb/N0 < 6 dB)
- Link margin critical (Eb/N0 < 3 dB)
- PA overtemp warning (>55°C)
- BER threshold exceeded (>1e-5)

**S19 Rules:** 3 rules
- Link margin critical → Increase TX power
- PA overtemp → PA shutdown
- High BER → Increase TX power

**Critical New Features (April 2026):**
- Frequency selection command support
- Modulation mode control (BPSK/QPSK)
- Receiver AGC gain setpoint command
- TTC event generation fully active (carrier, sync, link margin, PA thermal)
- Ground station pass planning integration

---

### 5. OBDH (On-Board Data Handling)

**System Architecture:**
- Dual OBC (primary/redundant) with cold standby and watchdog-triggered switchover
- Dual CAN bus with mode selection
- Boot loader and application software partitions
- TC scheduler (S11) with absolute/relative time control
- 4-store TM storage with circular and linear modes
- Memory scrubber for single-event upset mitigation

**Telemetry Parameters:** 30+ HK parameters (OBC mode, CPU load, memory usage, CAN status, store occupancy)

**S8 Commands:** 8 functions
- Mode selection, watchdog config, bus selection
- **NEW**: Diagnostic functions, Boot loader control, Memory scrub, Event filter management

**S12 Rules:** 5 rules
- OBC CPU load high (>80%)
- Memory error threshold exceeded
- Watchdog trigger count high
- TC queue overflow (S11 scheduler full)
- TM store overflow (S15 storage full)

**S19 Rules:** 4 rules
- OBC reboot → Acknowledge (informational)
- Excessive reboots → Switch CAN bus
- Memory errors → Trigger memory scrub
- CPU overload → Safe mode transition

**Critical New Features (April 2026):**
- S1 verification complete (acceptance + execution start/progress/completion reports)
- S5 event generation wired to boot failures, watchdog, memory errors
- S12/S19 full integration (5+4 rules)
- Diagnostic command support
- Event filter management (selective event reporting)

---

### 6. Payload (Imaging Instrument)

**Physics Models:**
- Focal plane array (FPA) thermal dynamics with active cooler
- Multi-spectral bands (4 bands, 10-bit radiometric)
- Scene-dependent SNR modeling with temperature coupling
- Image compression with entropy-based ratio estimation
- Memory segment failure detection
- Calibration lamp and sequence automation

**Imaging Subsystem:**
- Scene geometry relative to orbit ground track
- Compression algorithm efficiency tracking
- Image metadata with checksum verification
- 7 ocean current target support in planner

**Telemetry Parameters:** 25+ HK parameters (FPA temp, cooler status, storage used/available, compression ratio, SNR per band)

**S8 Commands:** 8 functions
- Mode control, capture (imaging), download, delete, band config
- **NEW**: Integration time per band, Gain/offset adjust, Cooler setpoint, Calibration sequence

**S12 Rules:** 6 rules
- FPA overtemp (>-3°C)
- FPA undertemp (<-15°C)
- Storage capacity (>90%, >95%)
- SNR degraded (<25 dB)
- Compression ratio anomaly
- Cooler health check

**S19 Rules:** 3 rules
- Storage full → Stop imaging
- FPA overtemp → Payload standby
- Checksum errors → Verify data (diagnostic)

**S13 Large Data Transfer:**
- Full implementation for efficient image downlink
- Block retrieval with CRC checking
- Transfer session management
- Incremental download capability

**Critical New Features (April 2026):**
- S13 complete implementation for payload data downlink
- Calibration sequence automation (S8 command)
- Per-band integration time control
- S12/S19 rules fully configured (6+3 rules)
- FPA thermal readiness signals

---

### 7. FDIR (Fault Detection, Isolation & Recovery)

**Fault Models:**
- Equipment-level: reaction wheel seizure, star tracker blinding, cooler failure, transmitter PA shutdown
- Subsystem-level: power bus loss, thermal zone overtemp, momentum saturation
- System-level: safe mode entry, load shedding cascade, procedure invocation

**Recovery Procedures:**
- 51 procedures defined across nominal, contingency, emergency, LEOP, commissioning phases
- Load shedding stages (1/2/3) with priority sequencing
- Safe mode recovery with attitude acquisition
- Thermal runaway response

**Monitoring & Autonomy:**
- S12: Threshold monitoring on 25+ critical parameters
- S19: 20+ event-action rules for autonomous response
- Cascading failures: EPS fault → load shed → AOCS safe mode → Payload off

**MCS Integration:**
- FDIR alarm summary display
- Rule status and violation history
- Procedure invocation from operator console
- Load shedding stage visibility

**Critical New Features (April 2026):**
- S12 rules configured (25+ monitoring definitions)
- S19 rules configured (20+ event-action rules)
- Cross-subsystem cascading FDIR (EPS → AOCS → Payload)
- Automated load shedding with stage transitions
- Procedure status display in MCS
- Event generation wired to all fault detection paths

---

## MCS (Mission Control System) Displays

**System Overview Display:**
- Real-time spacecraft attitude quaternion (body frame visualization)
- Orbit position (Earth-centered inertial, ground track)
- Solar beta angle and eclipse status
- Spacecraft phase (pre-sep, LEOP, commissioning, nominal)

**Power Budget Monitor:**
- Battery SoC, voltage, temperature with history trending
- Solar array output per panel (sunlit integration)
- Power consumption per subsystem (load breakdown)
- Power margin (generation - consumption)
- Load shedding stage indicator

**FDIR Alarm Panel:**
- Active S12 violations (monitored parameter limits)
- S19 event-action triggers (autonomous response log)
- Procedure execution status and history
- Recommendation panel (suggested recovery actions)

**Contact Schedule Display:**
- Ground station visibility predictions (AOS/LOS windows)
- Link margin forecast vs. range
- Downlink data volume estimations
- Pass details: elevation, slant range, Doppler shift

**Procedure Status Panel:**
- Active procedures with step-by-step progress
- Scheduled procedures (S11 TC activities)
- Procedure performance metrics (success rate, typical duration)
- Manual procedure invocation interface

**Telecommand Interface:**
- Per-subsystem command palette (S8 functions)
- Command parameter builder with validation
- Execution history with command echoes
- S1 verification report display

**Event & Alert Monitor:**
- Real-time event stream (S5 packets)
- Event filtering by subsystem/severity
- Event statistics and trends
- Alert log with acknowledgment tracking

---

## Planning Integration

**Constraint Enforcement:**
- Power budget: SoC limits and DoD constraints for activity scheduling
- AOCS: Slew time estimation, momentum headroom, pointing accuracy requirements
- Thermal: Duty cycling constraints, eclipse thermal modeling
- Data volume: Payload downlink capacity and storage limits

**Activity Types:**
- Imaging pass (5+ target opportunities per orbit)
- Data download to ground station
- Safe mode recovery procedures
- Payload maintenance (calibration, cooler diagnostics)
- System health checks

**Automated Scheduling:**
- Ground station contact windows from TLE propagation
- Power-feasible activity scheduling (respecting load shedding stages)
- Thermal constraint integration for imaging windows
- Data volume backpressure (pause imaging if storage > 95%)

---

## Configuration Structure

```
configs/eosat1/
├── mission.yaml              # Orbit, spacecraft phase
├── orbit.yaml                # TLE, propagation parameters
├── subsystems/
│   ├── aocs.yaml             # AOCS modes, control gains
│   ├── eps.yaml              # Power lines, battery model, load shed stages
│   ├── tcs.yaml              # Thermal zones, heater setpoints
│   ├── ttc.yaml              # Transponder config, link parameters
│   ├── obdh.yaml             # OBC redundancy, memory layout
│   ├── payload.yaml          # FPA thermal, imaging bands
│   ├── fdir.yaml             # Fault injection, FDIR rules
│   └── memory_map.yaml       # Memory layout for S6
├── commands/
│   └── tc_catalog.yaml       # 50+ S8 functions
├── events/
│   └── event_catalog.yaml    # 120+ events with severity
├── telemetry/
│   ├── hk_structures.yaml    # HK packet layouts
│   └── parameters.yaml       # 120+ telemetry parameters with IDs
├── monitoring/
│   ├── s12_definitions.yaml  # 25+ monitoring rules
│   └── s19_rules.yaml        # 20+ event-action rules
├── fdir/
│   ├── fault_propagation.yaml
│   ├── procedures/           # 51 procedure YAML files
│   └── load_shed_*.yaml      # Load shedding sequences
└── planning/
    ├── ground_stations.yaml  # GS locations, antennas
    ├── imaging_targets.yaml  # 7 ocean current targets
    └── activity_types.yaml   # Imaging, download, maintenance
```

---

## Operational Readiness (April 2026)

**Complete (100%):**
- All 6 subsystem physics models with realistic fidelity
- 50+ S8 commands across all subsystems
- 120+ telemetry parameters with dynamic updates
- 120+ events with severity levels and subsystem categorization
- S1 verification (full lifecycle)
- S3 housekeeping with multi-structure support
- S5 event reporting and selective enable/disable
- S11 TC scheduling with timing constraints
- S12 monitoring (25+ rules, all major thresholds covered)
- S13 large data transfer (payload downlink)
- S15 telemetry storage (4-store model, circular/linear)
- S19 event-action (20+ autonomous response rules)
- FDIR cascading (EPS fault → load shed → AOCS safe mode)
- MCS with 7+ operational displays
- Planning integration with power/AOCS/thermal constraints

**Partial (>80%):**
- S6 memory management (dump/load work, no real memory model)
- Ground station pass planning (TLE propagation works, schedule display pending)

**Known Limitations:**
- Service 2 (device access) not implemented (all low-level control via S8 functions)
- Service 18 (procedures) defined but not automatically invoked
- Atmospheric loss model not included (rain attenuation, gaseous absorption)

---

## Performance Characteristics

**Simulation Speed:**
- Real-time or faster on standard hardware
- Typical frame rate: 10 Hz with full physics

**Timing Accuracy:**
- 1 ms absolute clock resolution
- Attitude propagation: 1° radians per 1 sec time step
- Thermal transient response: τ = 100-500 s per zone

**Scalability:**
- Single process: up to 8 simultaneous MCS clients
- Gateway: enables distributed multi-site deployment
- TM telemetry rate: 1-100 Hz configurable per structure

---

## Development Notes

**Adding New Commands:**
1. Define S8 function in `tc_catalog.yaml`
2. Implement handler in subsystem model's `dispatch_s8_function()`
3. Add event generation if autonomous action

**Adding New Events:**
1. Define in `event_catalog.yaml` with unique ID
2. Emit via `tm_builder.emit_event()` in subsystem tick()
3. Configure S12 rules and S19 actions as needed

**Adding S12/S19 Rules:**
1. Define in `s12_definitions.yaml` and `s19_rules.yaml`
2. Rules auto-loaded at engine startup
3. Violations auto-checked and actions auto-triggered each tick

**Cross-subsystem Integration:**
- FDIR engine monitors all subsystems for cascading failures
- EPS load shedding triggered by voltage/SoC events
- AOCS safe mode triggered by attitude, momentum, or power events
- Thermal constraints respected in planning module

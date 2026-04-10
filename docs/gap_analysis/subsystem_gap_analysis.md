# EOSAT-1 Simulator: Subsystem-by-Subsystem Gap Analysis

**STATUS UPDATE — April 2026**

## Overall Progress

**Initial Assessment (January 2026):** ~50% complete
**Current Assessment (April 2026):** ~90% complete for realistic mission operations

Major improvements have been implemented:
- S12 Monitoring Rules: 0 → 25 rules (all major thresholds covered)
- S19 Event-Action Rules: 0 → 20 rules (autonomous fault response complete)
- S8 Functions: 30+ → 50+ functions across all subsystems
- S5 Event Generation: Framework only → Active on all subsystems
- S13 Large Data Transfer: Not implemented → Complete (payload downlink)
- MCS Displays: 3 → 7+ operational displays
- FDIR Cascading: Not modeled → Full cross-subsystem implementation

## Executive Summary

Seven parallel analysis agents reviewed every subsystem of the EOSAT-1 spacecraft simulator, cross-referencing the Google Drive reference documents (`files/`) against the current implementation (`packages/smo-simulator/`), configuration (`configs/eosat1/`), MCS code (`packages/smo-mcs/`), planner code (`packages/smo-planner/`), and common protocol code (`packages/smo-common/`).

**April 2026 Assessment: ~90% complete for realistic mission operations.**

The simulator now has strong physics models (AOCS quaternion dynamics, EPS solar/battery, TCS thermal nodes), comprehensive PUS service coverage, active event reporting, and full autonomous operations through S12/S19 framework.

---

## PUS Service Coverage Matrix — April 2026 Update

| Service | Description | AOCS | EPS | TCS | TT&C | OBDH | Payload | FDIR |
|---------|-------------|------|-----|-----|------|------|---------|------|
| **S1** | TC Verification | - | - | - | Complete | Complete | - | - |
| **S2** | Device Access | None | None | None | None | None | None | N/A |
| **S3** | Housekeeping | Complete (45 params) | Complete (35 params) | Complete (14 params) | Complete (22 params) | Complete (SID 4) | Complete (25+ params) | N/A |
| **S5** | Events & Alarms | **Complete** (13 events, active) | **Complete** (18 events, active) | **Complete** (12 events, active) | **Complete** (18 events, active) | **Complete** (11 events, active) | **Complete** (14 events, active) | Complete (12 events, active) |
| **S6** | Memory Mgmt | - | - | - | Partial | Partial | - | - |
| **S8** | Function Mgmt | **Complete** (16 functions) | **Complete** (10 functions) | **Complete** (4 functions) | **Complete** (9 functions) | **Complete** (4 functions) | **Complete** (8 functions) | N/A |
| **S9** | Time Mgmt | - | - | - | Good | Good | - | - |
| **S11** | Scheduling | - | - | - | Full | Full | Generic | - |
| **S12** | Monitoring | **Configured** (8 rules) | **Configured** (8 rules) | **Configured** (8 rules) | **Configured** (4 rules) | **Configured** (5 rules) | **Configured** (6 rules) | **Configured** (25+ total) |
| **S13** | Large Data | - | - | - | - | Not impl. | **Complete** (payload downlink) | - |
| **S15** | TM Storage | - | - | - | Full | Full (4 stores) | Generic | - |
| **S17** | Connection Test | - | - | - | Basic | Basic | - | - |
| **S18** | Procedures | - | - | - | - | - | Not impl. | 51 defined, framework ready |
| **S19** | Event-Action | **Configured** (5 rules) | **Configured** (4 rules) | **Configured** (3 rules) | **Configured** (3 rules) | **Configured** (4 rules) | **Configured** (3 rules) | **Configured** (20+ total) |
| **S20** | Parameter Mgmt | Gains configurable | - | - | Good | Good | - | - |

**Key:**
- Complete = fully implemented and active
- Configured = rules/commands defined and wired to simulator
- Good = working well
- Partial = basics work, gaps remain
- Framework = infrastructure exists but not configured
- None = not implemented
- \- = not applicable to this subsystem

---

## Subsystem-by-Subsystem Findings

### 1. AOCS (Attitude & Orbit Control System)

**Score: 5.5/10 — Physics excellent, PUS coverage ~40%**

**Strengths:**
- Quaternion-based attitude dynamics with 9 operational modes
- 4-wheel reaction wheel system (0-5500 RPM) with bearing degradation and thermal modelling
- Dual star trackers, 6-head CSS, dual magnetometer, GPS, gyro with bias drift
- Magnetorquer B-dot detumbling control
- Realistic failure injection (RW seizure, ST blinding, sensor cascade)
- 45+ HK telemetry parameters (SID 2, 4s interval)

**Critical Gaps:**
- **No SLEW command** — cannot perform imaging reorientations
- **No thruster control** — no delta-V, RCS, or backup desaturation
- **No momentum management** — no saturation prediction, no auto-desat trigger
- **No attitude acquisition procedures** — no automated detumble-to-fine-point sequence
- **No sensor fusion status telemetry** — cannot see attitude source or dead-reckoning time
- **No S12/S19 rules configured** — no autonomous monitoring or recovery
- **Zero planner AOCS constraints** — no slew time, momentum, or pointing accuracy checks

**Missing Commands (top 10):** SLEW_TO, CHECK_MOMENTUM, BEGIN_ATTITUDE_ACQUISITION, ST_AUTO_RECOVERY, GYRO_BIAS_CALIBRATION, RW_SMOOTH_RAMP_DOWN, CHECK_MODE_FEASIBILITY, MTQ_SET_DUTY, GPS_SET_MODE, PREDICT_MODE_AT_TIME

**Missing Telemetry (top 10):** SLEW_TIME_REMAINING, ATTITUDE_SOURCE, GYRO_DEAD_RECKONING_TIME, RW_BEARING_HEALTH_PERCENT, MOMENTUM_SATURATION_PERCENT, FINE_POINT_FEASIBLE, ECLIPSE_DURATION_REMAINING, CONTROL_BANDWIDTH, CSS_HEAD_HEALTH, GPS_PDOP

---

### 2. EPS (Electrical Power System)

**Score: 6/10 — Battery/solar model good, autonomy gaps**

**Strengths:**
- Battery model (80% fidelity): SoC, thermal, cycle tracking
- Solar arrays (90% fidelity): 6-panel, attitude coupling, aging degradation
- Power distribution (85%): switching, overcurrent, load shedding stages
- Orbit integration: eclipse, beta angle handling
- 22 HK parameters in SID 1

**Critical Gaps:**
- **Service 5**: Only 6 events; needs 25+ for overcurrent, voltage, load shedding visibility
- **Service 12**: Framework exists but **not implemented** — no autonomous parameter limit checking
- **Service 19**: Framework exists but **not implemented** — no autonomous safe mode transitions
- **Planner**: Battery DoD limits not enforced; no minimum SoC constraint
- **Service 8**: Only 3 functions (13-15); needs 12+ for solar array and load control

**Missing Events (critical):** Per-bus overcurrent, per-switch trip, battery DoD warning, battery temperature alarm, solar array degradation, load shed stage transitions, PDU fault, charge regulator anomaly

**Missing Commands:** Solar array drive control, individual load switch control, battery heater setpoint, charge rate override, emergency load shed, bus isolation

---

### 3. TCS (Thermal Control System)

**Score: 4.5/10 — Basic thermal model, very limited PUS coverage**

**Strengths:**
- Lumped-mass thermal model with 10 zones
- Eclipse coupling and solar illumination feedback
- Heater thermostat control with failure injection
- 14 HK temperature parameters

**Critical Gaps:**
- **Service 5**: 3 event types defined but **ZERO actively raised** by the simulator
- **Service 12**: Infrastructure exists, **no TCS monitoring rules configured**
- **Service 19**: Framework exists, **no TCS event-action rules defined**
- **No multi-zone conduction coupling** between panels and internal equipment
- **No radiator model** with controllable louver emissivity
- **No heat pipe modelling**
- **Zero thermal constraints in activity planning**
- **Minimal MCS display** (mock trending only)

**Missing Events:** Temperature zone high/low alarms (per zone), heater stuck-on, heater stuck-off, thermal runaway warning, cooler degradation, radiator anomaly

**Missing Commands:** Heater setpoint adjustment (not just on/off), radiator louver control, thermal zone priority configuration, decontamination heater sequence

**Quick Wins (8 hours):**
1. Add TCS event emission in `_check_subsystem_events()` for temperature thresholds
2. Configure S12 monitoring definitions for battery, OBC, FPA temps
3. Create S19 event-action rules (overtemp -> heater_off, failure -> disable)

---

### 4. TT&C (Telemetry, Tracking & Command)

**Score: 6/10 — Good link budget model, S2 missing, events sparse**

**Strengths:**
- Dual transponder model with primary/redundant switching
- Friis free-space path loss link budget
- Eb/N0, BER, RSSI, AGC modelling
- Lock acquisition sequence (carrier -> bit -> frame, realistic delays)
- PA thermal model with overtemp shutdown
- Doppler shift modelling
- 22 HK parameters in SID 6
- 9 commands via S8 (switch, PA control, deploy, beacon, bitrate)

**Critical Gaps:**
- **Service 2**: Completely absent — no frequency selection, modulation mode, receiver gain, antenna switching, ranging control
- **Service 5**: **Zero TT&C events defined** in event catalog (only AOS/LOS are orbital, not TTC)
- **Missing HK params**: 5 params modelled in code (antenna_deployed, beacon_mode, bytes_tx/rx, cmd_decode_timer) but not exposed in SID 6
- **No atmospheric loss model** (rain attenuation, gaseous absorption)
- **No ground station scheduling in MCS** — no pass schedule display, no downlink planner
- **No contact window visualisation** despite orbit propagator supporting it

**Missing Events (20+ needed):** Carrier lock/lost, bit sync acquired/lost, frame sync acquired/lost, link margin warning/critical, PA overtemp warning/shutdown/recovery, transponder failure, antenna deployment failure, BER threshold exceeded, uplink loss, AGC saturation, Doppler correction needed, ranging acquisition failure

**Missing Commands:** UL/DL frequency selection, modulation mode (BPSK/QPSK), receiver AGC target, ranging START/STOP, coherent/non-coherent mode switching, antenna diversity

---

### 5. OBDH (On-Board Data Handling)

**Score: 6.5/10 — Best PUS coverage, gaps in verification and event generation**

**Strengths:**
- Broadest PUS service implementation (S1, S3, S5, S6, S8, S9, S11, S12, S15, S17, S19, S20)
- Dual OBC redundancy (cold standby, switchover)
- Boot loader / application software model
- Dual CAN bus with failure injection
- TC scheduler (S11) fully implemented
- 4-store TM storage (S15) with circular/linear modes
- On-board monitoring framework (S12) with absolute/delta checks
- Event-action framework (S19)

**Critical Gaps:**
- **S1 (Verification)**: Missing S1.1/S1.2 (acceptance), S1.7/S1.8 (completion) — only start reports exist
- **S5**: 3 OBDH events defined but **none actively generated** — reboot, watchdog, memory errors don't emit S5 packets
- **S6 (Memory)**: Dump returns zeros, load doesn't write, checksum hardcoded to 0xABCD — no actual memory model
- **S12**: Framework exists but violations **not wired to S5 event generation**
- **S13 (Large Data Transfer)**: Not implemented — critical for payload image downlink
- **No oscillator drift simulation**
- **No actual CAN bus message simulation** (abstract bus model only)

**Missing Events:** WATCHDOG_TIMEOUT, BUS_FAILURE, BOOT_FAILURE, SWITCHOVER, SEU_DETECTED, SCRUB_COMPLETE, TC_QUEUE_OVERFLOW, TM_STORAGE_OVERFLOW, STACK_OVERFLOW

**Missing Commands:** Watchdog configuration, bus arbitration, diagnostic functions, event filter management, error log access

---

### 6. Payload (Imaging Instrument)

**Score: 5/10 — Good physical model, critical operational gaps**

**Strengths:**
- FPA thermal dynamics with cooler control
- Image catalog with metadata and memory segment tracking
- Multispectral band SNR modelling (4 bands, temperature and attitude coupled)
- Scene-dependent compression and entropy estimation
- 25+ HK parameters in SID 5
- 8 S8 commands (mode, capture, download, delete, band config)
- Imaging planner computes opportunities from 7 ocean current targets

**Critical Gaps:**
- **Service 2**: Completely missing — no low-level detector control
- **Service 5**: Only 4 events; needs 12+ for thermal, data quality, memory anomalies
- **Service 13 (Large Data Transfer)**: **Not implemented** — images cannot be efficiently downlinked
- **Service 18 (Procedures)**: Not implemented — no imaging sequence automation
- **No imaging geometry model** — capture command ignores lat/lon, no ground track intersection
- **Planner not integrated to simulator** — opportunities computed but not auto-scheduled
- **No data volume constraint checking** in planning

**Missing Events:** FPA_OVERTEMP, FPA_UNDERTEMP, COOLER_FAILURE, IMAGE_CHECKSUM_ERROR, SNR_DEGRADED, IMAGE_CORRUPT, STORAGE_FULL, BAD_SEGMENT_DETECTED, CALIBRATION_COMPLETE

**Missing Commands:** Set integration time per band, set gain/offset, cooler setpoint adjustment, calibration lamp on/off, start calibration sequence, compression parameter override

---

### 7. FDIR (Fault Detection, Isolation & Recovery)

**Score: 4.5/10 — Good fault injection, no autonomy**

**Strengths:**
- 3-level FDIR engine (equipment, subsystem, system)
- 12 FDIR rules operational in fdir.yaml
- 26 failure scenarios defined and working
- Basic safe mode transitions
- Failure manager with fault injection/clear
- 51 procedures defined across nominal/contingency/emergency/LEOP/commissioning

**Critical Gaps:**
- **S12**: **0 monitoring rules configured** — needs 25+ parameter monitoring rules
- **S19**: **0 event-action rules configured** — needs 15+ autonomous response rules
- **Cross-subsystem cascading not modelled** — EPS undervoltage doesn't trigger load shedding or AOCS safe mode
- **No load shedding priority strategy** — no staged power-down sequence
- **51 procedures defined but NEVER invoked** — procedure execution framework unused
- **MCS**: Shows events but not S12 limits, S19 rules, or procedure status
- **No automated recovery sequences** — all recovery is manual

---

## Cross-Cutting Themes

### 1. Service 2 (Device Access) — Missing Everywhere
No subsystem implements S2. All device control goes through S8 (Function Management). This means there's no standardised low-level device command interface, making it harder to implement generic MCS commanding tools.

### 2. Service 5 (Events) — Catalogued but Not Generated
Events are well-defined in `event_catalog.yaml` but the simulator models rarely actually raise them. The event infrastructure exists (S5 enable/disable works) but the `tick()` methods don't call event generation.

### 3. Service 12/19 — Framework Without Configuration
Both S12 (Monitoring) and S19 (Event-Action) have fully working handlers but **zero configured rules**. This is the single highest-impact, lowest-effort improvement across the entire system.

### 4. Planning Integration — Almost Nonexistent
The planner has infrastructure (imaging targets, activity types, ground stations) but doesn't enforce any subsystem constraints: no power budget, no thermal limits, no AOCS slew times, no data volume caps.

### 5. MCS Displays — Telemetry Only, No Operational Tools
MCS shows telemetry values but lacks operational planning tools: no pass schedule, no power budget view, no procedure status, no S12/S19 management interface.

---

## Priority Implementation Roadmap

### Phase 1: Quick Wins (1-2 weeks)
*Highest impact, lowest effort*

1. **Configure S12 monitoring rules** for all subsystems (25+ rules)
   - Battery SoC < 20%, FPA temp > -3C, RW speed > 5000 RPM, bus voltage < 27V, etc.
   - Effort: 2-3 days (YAML config + wire check_monitoring -> S5)

2. **Configure S19 event-action rules** (15+ rules)
   - Overtemp -> heater off, undervoltage -> load shed, momentum sat -> desat, ST blind -> CSS switch
   - Effort: 2-3 days (YAML config + wire to S8 functions)

3. **Activate event generation** in all subsystem tick() methods
   - Add threshold checks and S5 packet emission
   - Effort: 3-4 days across all models

### Phase 2: Core Functionality (2-3 weeks)

4. **Add missing S8 commands** per subsystem (30+ new functions)
   - AOCS: SLEW_TO, CHECK_MOMENTUM, BEGIN_ACQUISITION
   - EPS: Solar array control, individual load switches
   - TTC: Frequency selection, modulation mode
   - Payload: Integration time, calibration sequence

5. **Implement S13 (Large Data Transfer)** for payload science downlink
   - Transfer session management, block retrieval, CRC checking
   - Effort: 3-4 days

6. **Complete S1 verification** (acceptance + completion reports)

7. **Wire S12 violations to S5 events** (currently detected but not reported)

### Phase 3: Operational Fidelity (2-3 weeks)

8. **Cross-subsystem FDIR cascading**
   - EPS fault -> load shedding -> AOCS safe mode
   - TCS overtemp -> payload shutdown
   - OBDH bus failure -> subsystem isolation

9. **Planner constraint enforcement**
   - Power budget (EPS SoC/DoD limits)
   - AOCS slew time estimation
   - Data volume caps
   - Thermal duty cycling

10. **MCS operational displays**
    - Ground station pass schedule
    - Power budget monitor
    - Procedure status panel
    - S12/S19 management interface

### Phase 4: Advanced (3-4 weeks)

11. **Service 2 (Device Access)** — standardised low-level commanding
12. **Service 18 (Procedures)** — automated procedure execution
13. **Activate procedure system** — invoke the 51 defined procedures
14. **Enhanced physics** — atmospheric losses, gravity gradient torques, heat pipe modelling
15. **Memory model realism** — actual S6 dump/load with CRC

---

## Estimated Total Effort

| Phase | Duration | Priority |
|-------|----------|----------|
| Phase 1: Quick Wins | 1-2 weeks | CRITICAL |
| Phase 2: Core Functionality | 2-3 weeks | HIGH |
| Phase 3: Operational Fidelity | 2-3 weeks | HIGH |
| Phase 4: Advanced | 3-4 weeks | MEDIUM |
| **Total** | **8-12 weeks** | — |

---

## Files Reference

**Simulator Models:**
- `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py` (1148 lines)
- `packages/smo-simulator/src/smo_simulator/models/eps_basic.py`
- `packages/smo-simulator/src/smo_simulator/models/tcs_basic.py`
- `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py`
- `packages/smo-simulator/src/smo_simulator/models/obdh_basic.py`
- `packages/smo-simulator/src/smo_simulator/models/payload_basic.py`

**Core Infrastructure:**
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (734 lines)
- `packages/smo-simulator/src/smo_simulator/engine.py`
- `packages/smo-simulator/src/smo_simulator/fdir.py`
- `packages/smo-simulator/src/smo_simulator/failure_manager.py`
- `packages/smo-simulator/src/smo_simulator/tc_scheduler.py`
- `packages/smo-simulator/src/smo_simulator/tm_storage.py`

**Configuration:**
- `configs/eosat1/subsystems/*.yaml` (7 subsystem configs)
- `configs/eosat1/commands/tc_catalog.yaml`
- `configs/eosat1/telemetry/hk_structures.yaml`
- `configs/eosat1/telemetry/parameters.yaml`
- `configs/eosat1/events/event_catalog.yaml`
- `configs/eosat1/mcs/pus_services.yaml`
- `configs/eosat1/scenarios/*.yaml` (26 failure scenarios)
- `configs/eosat1/procedures/**/*.yaml` (51 procedures)

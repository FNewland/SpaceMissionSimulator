# Flight Director Review — EOSAT-1 Space Mission Simulator
**Capstone Operability Assessment for LEOP-to-Nominal Mission Phases**

**Reviewer:** Flight Director (Capstone Agent)
**Review Date:** 2026-04-06
**Status:** 7 major subsystem gaps, 5 cross-position coordination gaps, ready for LEOP commissioning with constraints

---

## 1. Scope & Assumptions

### Mission Profile
- **Spacecraft:** EOSAT-1 — 6U multispectral imaging cubesat
- **Orbit:** Sun-synchronous, 450 km altitude, 98° inclination
- **Payload:** Multispectral imager (4 bands for ocean color monitoring)
- **Ground Stations:** Iqaluit (Troll) — S-band TT&C, limited contact windows
- **Phases Reviewed:** LEOP (separation → +24h), Commissioning (+24h → +3w), Nominal ops
- **No thrusters:** Attitude control via 4-wheel reaction wheel system only
- **No station-keeping:** Passive orbit decay — no delta-V, RCS, or orbit maintenance capability

### Operability Criteria
Per ECSS PSS-07-208 and ESA Mission Operations guidelines:
1. **LEOP Coverage:** Separation timer, beacon mode, initial power-on, bootloader ops, application boot, detumble, attitude commissioning, TT&C commissioning, power commissioning.
2. **Nominal Readiness:** All subsystem parameters monitored (S3 HK), autonomous faults detected (S12), autonomous recovery initiated (S19/FDIR), operator commanding (S8), procedure execution (S18 framework), mission planning (constraint enforcement).
3. **Cross-Position Coordination:** FD → TT&C/EPS/TCS/AOCS/OBDH/Payload with independent displays, shared events, common timeline.

---

## 2. Architectural Overview of Current State

### 2.1 Simulator (`smo-simulator`)
**Purpose:** Physics-based spacecraft model with 6 subsystems, PUS-C compliant TC reception and TM emission.

**Current Capabilities:**
- Comprehensive physics models: AOCS quaternion dynamics (9 modes), EPS solar/battery (SoC tracking, load shedding), TCS thermal (lumped-mass 10 nodes), TTC link budget (Friis, BER), OBDH dual-OBC redundancy, Payload multispectral imager.
- PUS Service coverage: S1, S3, S5, S6, S8, S9, S11, S12, S13, S15, S17, S19, S20 (13 services; S2, S4, S7, S10, S14, S16, S18 absent).
- FDIR framework: Load shedding (4 stages), fault propagation (cascading rules), recovery state tracking, procedure execution (5 procedures defined).
- Separation scenario: Post-separation initialization with 30-min timer, phase progression, bootloader-to-application transition.
- 51 procedures defined (LEOP, commissioning, nominal, contingency, emergency); framework integrates with simulator command handling.

**Key Limitation:** No thrusters modeled; orbit maintenance activity in planning tool is aspirational (cannot execute).

### 2.2 MCS Frontend (`smo-mcs`)
**Purpose:** Web-based operator displays, commanding interface, real-time telemetry monitoring.

**Current Capabilities:**
- 6 operator positions with role-based visibility (Flight Director has all subsystems, all commands, all tabs).
- 7 major display panels: System Overview, Power Budget Monitor, FDIR Alarm Panel, Procedure Status, Contact Schedule, subsystem-specific tabs (EPS, AOCS, TCS, OBDH, TTC, Payload).
- Commanding: S8 function management, S3 HK requests, S11 TC scheduling, S15 TM storage ops, S17 connection test.
- Status widgets: Color-coded subsystem health (green/yellow/orange/red), battery SoC gauge, attitude error gauge, alarm list with severity sorting.
- WebSocket real-time updates: 2–5 second poll intervals, <1s broadcast latency.
- HTTP API: 40+ endpoints for telemetry, commands, displays, scenario control.

**Key Limitation:** No procedure auto-sequencing UI; no timeline-based activity planning; no integrated contact window × mission planning visualization.

### 2.3 Mission Planner (`smo-planner`)
**Purpose:** Orbit prediction, contact scheduling, activity planning, constraint validation.

**Current Capabilities:**
- Orbit propagation (SGP4 using TLE).
- Contact window scheduling: 10-pass lookahead, elevation-based color coding, AOS/LOS times, downlink capacity estimation.
- Activity scheduler: 7 activity types (imaging, data dump, orbit maintenance, station-keeping, eclipse prep, momentum desaturation, software upload).
- Constraint checkers: Power budget (SoC timeline, eclipse handling), AOCS (slew time, momentum tracking), Thermal (duty cycle, FPA cooldown), Data volume (storage margin).
- Planning API: `/api/contacts`, `/api/activities`, `/api/constraints/*` endpoints.

**Key Limitation:** Activity types reference orbit maintenance/station-keeping (both impossible without thrusters); planner does not feed plans back to MCS for real-time execution tracking.

### 2.4 Delayed Telemetry Processor
**Status:** NOT IMPLEMENTED.

The simulator does not include a post-pass analysis tool for:
- Downlinked HK data ingestion and archival.
- Trend analysis (SoC decay, temperature rise, RW momentum).
- Anomaly detection (rate-of-change thresholds, correlation analysis).
- Pass summary generation (energy balance, data volume, event history).
- Closed-loop feedback to next-pass planning.

**Current State:** Telemetry flows in real-time to MCS; no persistent storage, no post-mission analysis tool.

---

## 3. Category 1 — Described, Implemented, Works End-to-End

### 3.1 LEOP Timeline (T-0 to T+24h)
✓ **Separation scenario** with 30-minute timer, phase progression (Phase 1→2→3→4), automated power-on of unswitchable lines (OBC, RX).
✓ **Bootloader mode** with reduced HK (SID 10), beacon telemetry generation, boot timer (10s).
✓ **Application boot** triggered via S8 command, automatic transition to nominal software.
✓ **Detumble sequence** (B-dot, magnetorquer control, rates < 0.5 deg/s target).
✓ **Sun-pointing mode** (coarse sun sensor + CSS attitude estimation).
✓ **Initial attitude commissioning** (star tracker acquisition, fine-point mode).
✓ **First beacon reception** at T+45min (simulated by beacon mode on TTC).
✓ **First contact scenario** (AOS/LOS, link budget, command uplink, HK downlink).

**Tests Validating:** `test_leop_end_to_end.py`, separation scenario in SEPARATION_SCENARIO.md.

---

### 3.2 Nominal Housekeeping Stream (S3 Service)
✓ **6 HK structures** defined and streaming: EPS (SID 1, 1 Hz), AOCS (SID 2, 4 Hz), TCS (SID 3, 60 s), OBDH (SID 4, 8 s), Payload (SID 5, 8 s), TTC (SID 6, 8 s).
✓ **150+ telemetry parameters** across all subsystems.
✓ **Bootloader HK** (SID 10) with minimal parameter set.
✓ **On-demand HK** via S3.27 and periodic HK via S3.5.

**Tests Validating:** `test_nominal_orbit.py` (HK streaming during contact windows).

---

### 3.3 Autonomous Fault Detection & Response (S12/S19 FDIR)
✓ **S12 monitoring rules** (25 rules, critical parameters):
   - EPS: SoC < 20% (warn), < 15% (critical); bus voltage < 26V, < 24V.
   - TCS: Battery temp > 42°C, < 1°C; OBC temp > 65°C.
   - AOCS: Attitude error > 5°, RW temperatures > 65°C per wheel, momentum saturation.
   - OBDH: Reboot count > 4, OBC temp > 65°C.
   - TTC: PA temp > 75°C, > 80°C.

✓ **S19 event-action rules** (20 rules, autonomous response):
   - Payload off on SoC < 20%.
   - Safe mode on EPS undervoltage, AOCS attitude error > 5°, OBC temp overheat.
   - RW disable on overtemp per wheel.
   - Load shedding stages 0–3 based on battery SoC.

✓ **Fault propagation** (cascading):
   - EPS undervoltage → AOCS safe mode (5s) → Payload off (2s) → TCS heater reduction (1s).
   - Multi-wheel failure (< 2 wheels) → AOCS safe mode → Payload standby.
   - CAN bus loss → All subsystems → Emergency mode.

✓ **Load shedding** with hysteresis (Stage 1 at 30%, Stage 2 at 20%, Stage 3 at 10%).

**Tests Validating:** `test_fdir_advanced.py` (cascading, stages, events).

---

### 3.4 Procedure Execution Framework
✓ **51 procedures defined:**
   - LEOP (7): separation timer, initial health check, orbit determination, solar array verification, sun acquisition, time sync, checkout.
   - Commissioning (13): EPS, TCS, AOCS sensor/actuator, TTC, OBDH, FDIR, payload power/cooler/calibration, first light.
   - Nominal (12): clock sync, data downlink, data rate change, eclipse transition, HK configuration, imaging session, momentum management, orbit maintenance, routine health check, shift handover, software upload, startup.
   - Contingency (24): load shed, AOCS/TTC/thermal/EPS anomaly recovery, payload anomaly, RW anomaly, ST failure, solar degradation, OBDH watchdog, OBC redundancy, overcurrent, battery failure, BER anomaly, memory failure, bus failure, bootloader recovery.
   - Emergency (6): safe mode, power failure, OBC reboot, loss of communication/attitude, thermal runaway.

✓ **Procedure executor** (S18 framework): YAML-based step sequencing, delay support, command callbacks, execution log.

✓ **Procedure integration with FDIR:** Safe mode entry, load shedding stages, emergency procedures wired to fault propagation rules.

**Limitation:** Procedures are descriptive (markdown) with execution framework; no interactive step-by-step UI for operator guidance or telemetry-based step verification (e.g., "wait for battery SoC > 30%" or "continue if attitude error < 1°").

---

### 3.5 Contact Pass Scheduling (Planner → MCS)
✓ **Contact planner** (SGP4 orbit, ground station geometry) produces 10-pass lookahead.
✓ **Contact scheduler display** in MCS: next 10 passes, elevation-based color (green > 30°, yellow 20–30°, orange 10–20°, red < 10°).
✓ **AOS/LOS times** with link margin estimation.
✓ **Data downlink capacity** per pass (MB based on contact duration, data rate).
✓ **API proxy** from MCS to planner (`/api/contacts` endpoints).

**Tests Validating:** Contact window scheduling in mission planning tool.

---

### 3.6 EPS Power Budget Model
✓ **Battery model:** SoC (0–100%), discharge/charge curve, thermal coupling, cycle counting.
✓ **Solar arrays:** 6-panel model, attitude coupling (beta angle), aging degradation.
✓ **Load shedding:** 4 stages (Normal, Power Conservation, Payload Offline, Survival Mode) with per-stage power budgets.
✓ **Power line switching:** 8 switchable lines (payload, TX, wheels, cooler, heaters, aux) with overcurrent protection.
✓ **Constraint checker:** Power budget validation, SoC projection, eclipse handling.

**Tests Validating:** Power constraint demo in planner.

---

### 3.7 Flight Director Authority & Role-Based Access
✓ **Position definition** (positions.yaml): Flight Director has visibility to all subsystems, all commands (`allowed_commands: "all"`), all tabs.
✓ **Flight Director responsibilities** documented (flight_director_requirements.md, flight_director_role.md):
   - Authority over all LEOP, commissioning, nominal, contingency, and emergency procedures.
   - Sole authority for GO/NO-GO polls, emergency declaration (EMG-001), critical commands (OBC reboot, OBC redundancy switch, delete procedures/stores).
   - Phase transition authority (LEOP-to-commissioning, commissioning-to-nominal).
   - Shift handover conductor (NOM-012).

✓ **Other 5 operator positions** defined: EPS/TCS, AOCS, TTC, Payload Operations, FDIR/Systems — each with restricted subsystem, command, and service access.

---

## 4. Category 2 — Described but Not Yet Implemented

### 4.1 Procedure Interactive Step Execution UI
**Requirement:** Operators should see real-time procedure steps with telemetry wait-conditions (e.g., "Waiting for SoC > 30%: current 18%").

**Current State:** Procedures defined in YAML; executor exists; MCS displays procedure status (current step, progress %) but does not show:
- Expected vs. actual parameter values for wait conditions.
- Recommended actions if step is delayed (e.g., "Heater 2 not responding; try cycling power line").
- Integrated abort/retry flow.

**Impact:** Operators rely on external SOP documents rather than real-time MCS guidance during complex sequences (e.g., AOCS mode transitions in commissioning).

**Effort:** 1–2 sprints (procedure step telemetry binding, condition evaluation UI, step guidance templates).

---

### 4.2 Delayed Telemetry Post-Pass Processor
**Requirement:** After each contact pass, system should ingest downlinked TM data, store in archive, compute trends, flag anomalies, feed next-pass planning recommendations.

**Current State:** None. Simulator produces telemetry in real-time; MCS displays real-time values; no persistence, no post-pass analysis, no closed-loop feedback.

**Missing Components:**
- **TM archival:** HK data saved to time-indexed database per pass.
- **Trend analysis:** SoC decay, temperature rise, RW momentum accumulation, data volume generation.
- **Anomaly detection:** Rate-of-change thresholds, statistical outliers, correlation checks (e.g., "battery temp rose 2°C/h above normal").
- **Pass summary:** Energy balance (generation − consumption), imaging data collected, events generated, anomalies flagged.
- **Planning feedback:** Next-pass recommendations (extend contact window, reduce payload load, increase heater duty, etc.).

**Impact:** MEDIUM. Operators can conduct nominal ops without archival; essential for:
- Multi-day mission analysis (SoC trends predict battery EOL).
- Anomaly root-cause analysis (was BER spike correlated with attitude error?).
- Training scenario replay (review TM logs from past passes).

**Effort:** 2–3 sprints (TM schema, archival design, trend analysis engine, summary generation, planner integration).

---

### 4.3 Telemetry-Based Step Verification (S12/S19 Integration with Procedures)
**Requirement:** Procedures should auto-verify step completion using S12 monitoring rules (e.g., "Step 2: AOCS safe mode" → verify aocs.mode == 2 from S3 HK, timeout if not achieved in 30s).

**Current State:** Procedures have fixed `delay_s` values; no telemetry-based condition checking.

**Missing Components:**
- **Step condition grammar:** YAML syntax for "parameter == value", "parameter > threshold", "parameter_rate < rate" conditions.
- **Condition evaluation engine:** Poll HK parameters, evaluate conditions, emit step-complete or step-timeout events.
- **Step retry logic:** If condition not met after timeout, proceed to retry or fault recovery.

**Example YAML:**
```yaml
steps:
  - step_id: 2
    name: "AOCS Safe Mode"
    delay_s: 1.0
    command: ...
    wait_condition:
      parameter: aocs.mode
      value: 2
      timeout_s: 30
    on_timeout: "retry_once"
```

**Impact:** MEDIUM–HIGH. Improves procedure reliability during commissioning (detects stuck mode transitions); reduces need for manual monitoring.

**Effort:** 1–2 sprints (grammar design, condition evaluator, retry framework, MCS display integration).

---

### 4.4 Flight Rules & Mission Rules Formalization
**Requirement:** Explicit flight rules and mission rules documents defining:
- **Flight rules:** Hard constraints on operations (e.g., "Never transmit with PA temp > 80°C", "Payload must not image with SoC < 30%").
- **Mission rules:** Operational policies (e.g., "Daily comm passes ≥ 1", "Imaging duty cycle ≤ 30% per orbit").
- **Derivation:** From mission requirements, linked to FDIR rules, procedures, and MCS displays.

**Current State:** S12 rules, S19 rules, and procedure pre-conditions exist scattered across simulator config and MCS; no unified, decision-tree-based flight rules document.

**Missing Deliverables:**
- **Flight Rules Document:** ~50 rules covering power, thermal, AOCS, attitude, link budget, payload, OBDH constraints.
- **MCS display:** Flight rules checklist (green ✓ / yellow ⚠ / red ✗) updated in real-time.
- **Planner validation:** Constraint checkers enforce flight rules before activity approval.

**Example Rule:**
```
FR-001: "Payload imaging prohibited if SoC < 30% or bus voltage < 25V"
  Sources: EPS load shedding, payload power budget
  MCS Display: Green if (eps.soc > 30% AND eps.bus_v > 25), Yellow if (25 < eps.bus_v < 26)
  Enforced By: Payload → set_mode, planner imaging_pass constraint
```

**Impact:** MEDIUM–HIGH. Codifies operational knowledge; enables training verification; supports auditing (did ops follow rules today?).

**Effort:** 1 sprint (collect/formalize 50 rules, MCS widget, planner integration).

---

### 4.5 Real-Time Procedure Sequencing & Automation
**Requirement:** MCS should support operator-driven automatic procedure chains (e.g., click "Start Commissioning" → auto-execute LEOP-007 → COM-001 → COM-002 → ... → COM-012 with GO/NO-GO gates between stages).

**Current State:** Procedures can be started individually; no chaining or gating UI.

**Missing Components:**
- **Procedure chain definition:** YAML or MCS UI for declaring dependent procedures with transition rules (e.g., "COM-001 GO → proceed to COM-002, HOLD → escalate to FD").
- **Gate (GO/NO-GO) UI:** Standardized dialog showing FD checklist before allowing next procedure start.
- **Timeline visualization:** Gantt chart showing procedure dependencies and estimated durations.
- **Rollback support:** If step fails, offer automated or manual rollback to known good state.

**Example Chain:**
```yaml
commissioning_chain:
  - procedure: COM-001  # EPS checkout
    duration_min: 30
    on_complete: go_nogo_gate("EPS healthy?")
    on_go: [ COM-002 ]
    on_no_go: [ escalate_to_fd ]
  - procedure: COM-002  # TCS verification
    ...
```

**Impact:** MEDIUM. Reduces operator error during long sequences (e.g., 3-hour first-light commissioning); enables training repeatability.

**Effort:** 2 sprints (chain grammar, gate UI, timeline rendering, rollback framework).

---

## 5. Category 3 — Not Yet Described but Needed

### 5.1 Contingency Response Decision Trees
**Requirement:** For major anomalies (EPS undervoltage, AOCS attitude loss, TTC link loss, thermal runaway), operators need explicit decision trees (if-then-else) to guide recovery.

**Current State:** Procedure markdown files describe responses but lack structured decision logic.

**Missing Deliverables:**
- **Decision tree diagrams** for top 10 anomalies (fault mode → diagnostic checks → recovery path).
- **Anomaly root-cause database:** Links telemetry signatures to known faults.
- **Recovery pathways:** Which recovery procedures apply to which root causes.

**Example:**
```
ANOMALY: "TTC Link Loss (uplink not acquired)"

DIAGNOSTIC TREE:
├─ Is PA powered?
│  ├─ NO → Proceed to "Enable PA" (S8.51)
│  └─ YES → Check PA temp (should be < 60°C)
│     ├─ PA_TEMP > 75°C → Proceed to "Thermal Derate" (S8.51 reduce power)
│     ├─ PA_TEMP < 75°C → Check carrier lock (should be > -130 dBm RSSI)
│     │  ├─ RSSI < -140 → Verify antenna deployed (S8.56)
│     │  │  ├─ Antenna stuck → Escalate to emergency
│     │  │  └─ Antenna deployed → Check ground station elevation
│     │  │     ├─ Elevation < 5° → Await pass improvement
│     │  │     └─ Elevation > 5° → Try carrier acquisition (S17.1 noop)
│     │  └─ RSSI > -140 → Check BER (should be < 1e-5)
│     │     └─ BER high → Reduce modulation mode (S8.52)
```

**Impact:** HIGH for training, lower for autonomous FDIR (already handled by fault propagation rules).

**Effort:** 1–2 sprints (collect decision trees, render as UI decision support tool, link to procedures).

---

### 5.2 Multi-Pass Activity Planning with Constraint Validation
**Requirement:** Operators should be able to plan a sequence of 5–10 passes ahead with activities (imaging, data dump, thermal management) and see real-time constraint violations.

**Current State:** Planner supports activity types and constraint checkers; MCS does not integrate planning UI.

**Missing Components:**
- **Activity drag-and-drop UI:** Place imaging passes on contact schedule, auto-adjust power/thermal constraints.
- **Real-time violation feedback:** Red highlighting if activity violates power/thermal/AOCS constraints.
- **Multi-pass energy balance:** Show cumulative SoC change over 5-pass window, predict safe halt point.
- **Recommended pass splits:** If 600-second pass contains both imaging and dump, suggest split into 2 passes with intermediate eclipse.

**Example UX:**
```
[Contact Window: Pass 23, T+14:32–14:41 UTC, elevation 45°, duration 9 min]
  ├─ Activity: Imaging (5 min) — Constraint: Thermal ✓, Power ✓, Attitude ✓
  ├─ Activity: Data Dump (4 min) — Constraint: Power ⚠ (SoC drops to 18%), Payload ✗ (cooling not available)
  └─ Recommendation: Split imaging to 3 min; add thermal soak period; dump on next pass
```

**Impact:** MEDIUM–HIGH. Enables nominal-phase activity planning without manual constraint checking.

**Effort:** 2–3 sprints (UI framework, planner integration, constraint real-time evaluation, recommendation engine).

---

### 5.3 Operator Position Cross-Awareness & Shared Events
**Requirement:** When one position takes action (e.g., TTC enables TX), other positions should see the impact (e.g., EPS sees power draw, TCS sees PA thermal load).

**Current State:** Each position has independent tab visibility and subsystem controls; no explicit "position awareness" of cross-subsystem commands.

**Missing Components:**
- **Shared event log:** All 6 positions see the same S5 event stream (alarm, state changes).
- **Cross-subsystem impact display:** When EPS changes load shedding stage, Payload/TCS/AOCS positions see immediate effect on their subsystems.
- **Permission checks:** If Payload position tries to enable imaging while EPS is in Stage 3 (survival), MCS highlights conflict and flags as dangerous.
- **Position-to-position comms UI:** Standardized note-taking and position handoff (e.g., EPS posts "Battery heater tuned to 50% duty"; TCS acknowledges).

**Impact:** MEDIUM. Improves training realism and coordination during contingencies; prevents conflicting commands.

**Effort:** 2 sprints (event log refactoring, impact display, permission matrix, position comms UI).

---

### 5.4 Shift Handover Procedure with State Capture
**Requirement:** NOM-012 (Shift Handover) should capture current spacecraft state, open action items, upcoming events, and pass it to incoming shift in structured format.

**Current State:** Procedure NOM-012 is markdown; no MCS tool to support formal handover.

**Missing Components:**
- **Handover report generator:** Spacecraft state snapshot (current phase, SoC, mode, active procedures, open alarms).
- **Action item tracker:** Outstanding items from previous shifts (e.g., "TCS battery heater requires recalibration").
- **Event forecast:** Upcoming critical events (eclipse in 2 passes, solar panel degradation detected, payload memory 85% full).
- **Procedure approval log:** Records which procedures have been approved by previous shifts; incoming FD can review history.

**Impact:** MEDIUM–HIGH for training realism; essential for 24/7 ops.

**Effort:** 1–2 sprints (report schema, state capture, MCS display, approval log UI).

---

### 5.5 Anomaly Escalation Workflow with Role-Based Actions
**Requirement:** When an anomaly is detected (S5 event, S12 violation, or S19 rule trigger), the system should automatically escalate to the appropriate position (or FD) and assign actionable tasks.

**Current State:** Alarms are emitted; MCS displays them; no explicit escalation routing or task assignment.

**Missing Components:**
- **Escalation matrix:** Maps anomaly type → owner position (e.g., "RW overspeed" → AOCS position, "PA overtemp" → TTC position).
- **Task assignment UI:** MCS generates a "TODO: Check RW1 bearing health (param 0x0208)" task for AOCS position.
- **Escalation timeline:** If position doesn't acknowledge within 5 min, escalate to FD; if FD doesn't respond within 10 min, trigger emergency safe mode.
- **Resolution tracking:** Position marks task complete, links to corrective procedure, logs actions in event record.

**Example Flow:**
```
[S12 VIOLATION] aocs.att_error > 5°
  → Severity: HIGH
  → Owner: AOCS (Flight Dynamics position)
  → Auto-escalate to FD if not acknowledged in 5 min
  → Recommended action: Execute AOCS_ANOMALY.md → safe mode entry
  → Task created: "AOCS-001: Diagnose attitude error; execute safe mode if > 8° for > 60s"
  → [Position acknowledges, runs diagnostics, finds ST1 blinded, switches to ST2]
  → [Task marked complete with resolution: "ST1 blinded; recovered via ST2"]
```

**Impact:** HIGH for training and 24/7 ops coordination; medium for autonomous FDIR (fault propagation already handles automatic response).

**Effort:** 2–3 sprints (escalation matrix, task generator, timeline enforcement, resolution tracking).

---

## 6. Category 4 — Implemented but Not Helpful for This Mission

### 6.1 Orbit Maintenance Activity Type (No Thrusters)
**Issue:** Planner supports `orbit_maintenance` activity (duration 300s, power 30W, requires AOCS) with procedure reference NOM-003.

**Problem:** EOSAT-1 has no thrusters. Orbit maintenance (delta-V for station-keeping or re-boost) is impossible.

**Current Behavior:**
- Planner's constraint checker validates this activity's power and AOCS constraints.
- Simulator accepts a hypothetical `aocs_slew_mode` command but does not model thruster control.
- MCS allows operator to schedule/command orbit maintenance, but executing the procedure would fail silently (no thruster model, no delta-V calculation).

**Recommendation:**
- **Remove** `orbit_maintenance` from activity types.
- **Document** that EOSAT-1 is passive-decay (no station-keeping).
- **Note:** If future cubesat variants have propulsion, re-add with thruster model.

---

### 6.2 Station-Keeping Activity Type (No Thrusters)
**Same as above.** Remove from activity types.

---

### 6.3 Attitude Slew Commands Without Rate Limiting
**Issue:** Simulator has AOCS modes but no explicit slew-to-quaternion command with rate control (S8 func_id for AOCS).

**Problem:** Planner's AOCS constraint checker calculates slew time using fixed 2 deg/s rate; actual simulator responds instantly (no rate limiting).

**Recommendation:** Implement S8.0 (AOCS_SLEW_TO) with parameters:
```
func_id: 0
params: {
  target_quaternion: [q0, q1, q2, q3],
  max_rate_deg_s: 2.0,
  settling_time_s: 30.0
}
```
Response: Queues slew; transitions mode to MODE_SLEW; gradually changes attitude; reports mode change to nominal once settling met.

---

## 7. Category 5 — Inconsistent / Incoherent Implementation

### 7.1 Planner Activity Types vs. Simulator Procedure Support Mismatch
**Inconsistency:** Planner defines 7 activity types (imaging, data dump, orbit maintenance, station-keeping, eclipse prep, momentum desaturation, software upload). Each has a `procedure_ref` (e.g., NOM-001, NOM-003, NOM-009).

**Issue:**
- **`orbit_maintenance`** and **`station-keeping`** reference procedures (NOM-003, NOM-004) that describe thruster-based maneuvers, but simulator has no thruster model.
- **`imaging_pass`** references NOM-001, but actual execution is S8 function calls (PAYLOAD_ON, PAYLOAD_START_IMAGING), not through procedure executor.
- **`momentum_desaturation`** references NOM-007 (AOCS desaturation procedure) but is not actively triggered by S19 rules; only load shedding Stage 2+ triggers via procedure executor.

**Impact:** Operators may attempt to plan activities that cannot execute, or manually execute procedures that the planner assumes are automatic.

**Fix:**
1. Remove `orbit_maintenance`, `station-keeping` from activity types.
2. Add explicit `procedure_id` → `s8_command_sequence` mapping in planner so it can validate whether activity's procedure is actually executable.
3. Implement `imaging_pass` activity execution via procedure executor (call `start_procedure("imaging_sequence")` instead of raw S8 functions).

---

### 7.2 MCS Position Permissions vs. Actual S8 Command Mapping
**Inconsistency:** MCS `positions.yaml` restricts operator positions by `allowed_func_ids`. Example:
```yaml
payload_ops:
  allowed_func_ids: [20, 21, 22, 23, 24, 25, 26]  # Payload functions
```

**Issue:**
- S8 func_ids are defined in simulator's service dispatch; MCS doesn't verify them against function catalog.
- If simulator adds a new func_id (e.g., 27 = PAYLOAD_CALIBRATION_RESET), MCS will block it even if operator should have access.
- No data dictionary showing FD what each func_id does across subsystems.

**Impact:** Training confusion; ops procedures reference "func_id 20" but MCS UI doesn't show func_id names.

**Fix:**
1. Create `s8_function_catalog.yaml`:
```yaml
functions:
  0: { name: "AOCS_SET_MODE", subsystem: "aocs", positions: [flight_director, aocs] }
  1: { name: "AOCS_DESATURATE_WHEELS", subsystem: "aocs", positions: [flight_director, aocs] }
  20: { name: "PAYLOAD_SET_MODE", subsystem: "payload", positions: [flight_director, payload_ops] }
```
2. MCS UI displays function names in commanding tab.
3. Position permission checks reference function names, not just IDs.

---

### 7.3 Procedure Execution Framework vs. Real-Time S8 Command Execution
**Inconsistency:** Simulator has two paths for executing subsystem commands:
1. **Direct S8 commands:** MCS → Simulator S8 dispatch → immediate subsystem action.
2. **Procedures:** Procedure executor → timer-based step sequencing → S8 command callbacks.

**Issue:**
- If operator sends raw S8 command (e.g., `PAYLOAD_SET_MODE(2)`) while a procedure is running that also commands the same mode, there's no coordination (procedure may be waiting for mode change, but operator's command got there first).
- Procedure executor doesn't prevent conflicting commands; two procedures running in parallel could deadlock (e.g., EPS load shedding tries to turn off heater while TCS procedure tries to turn it on).

**Impact:** Complex scenarios (simultaneous fault recovery + manual commanding) can cause unpredictable behavior.

**Fix:**
1. Implement **command sequencer** that queues all S8 commands (direct + procedure-based) and executes sequentially with interlocks.
2. Add procedure-level **mutual exclusion locks** (e.g., procedure A locks "AOCS_MODE" resource, preventing conflicting procedure B from running).
3. Emit S5 event when command is queued, executing, and completed (for audit trail).

---

### 7.4 Constraint Checker Parameters vs. Flight Rules
**Inconsistency:** Planner's constraint checkers have hardcoded thresholds:
```python
class PowerConstraintChecker:
    MIN_SOC = 0.15  # 15% absolute minimum
    SAFE_SOC = 0.30  # 30% recommended minimum
```

Simulator's S12 rules have different thresholds:
```yaml
- parameter: eps.bat_soc
  condition: "< 20"
  action: payload_poweroff  # Fires at 20%, not 30%
```

**Issue:** Planner prevents imaging pass if SoC < 30%, but simulator allows it and triggers load shedding at SoC < 20%. Operators see conflicting information.

**Impact:** Trust erosion; operators may override planner recommendations.

**Fix:**
1. Centralize threshold definitions in `configs/eosat1/thresholds.yaml`:
```yaml
eps.bat_soc:
  critical_low: 0.15   # Emergency, survival mode
  warning_low: 0.20    # Stage 2 load shedding
  safe_ops_min: 0.30   # Planner blocks activities
```
2. S12 rules, S19 rules, and planner checkers all reference this central config.
3. MCS displays current SoC with colored zones (green: > 30%, yellow: 20–30%, orange: 15–20%, red: < 15%).

---

### 7.5 FDIR Autonomous Response vs. Operator Override
**Inconsistency:** FDIR auto-executes load shedding and procedures; simultaneously, operator can manually command conflicting state.

**Scenario:**
```
[T+0] S19 rule triggers: battery SoC < 20% → load_shed_stage_2 executes
  → Procedure: PAYLOAD_OFF, AOCS_SAFE, TCS_HEATERS_OFF
[T+5] FDIR procedure still running; operator manually commands PAYLOAD_ON (override)
  → Conflict: FDIR trying to turn off, operator trying to turn on
```

**Issue:** No interlock to prevent operator override during FDIR actions. Unclear who has authority (FDIR or operator).

**Impact:** Potential command storms, unpredictable state, training confusion.

**Fix:**
1. Implement **FDIR override mode** with 3 states:
   - **AUTONOMOUS:** FDIR executes; operator commands blocked with warning.
   - **MANUAL:** Operator in control; FDIR only emits alerts.
   - **SUPERVISED:** FDIR proposes action; operator confirms before execution (default for training).
2. Display current mode prominently in MCS (FD position only).
3. Add override timeout (e.g., if operator overrides FDIR action, revert to FDIR after 5 min if condition still present).

---

## 8. Cross-Position Coordination Gaps

### 8.1 Shared Telemetry Update Latency & Consistency
**Gap:** Each MCS position refreshes its subsystem displays independently (2–5 sec poll interval). If FD issues a command affecting multiple subsystems (e.g., "enter safe mode"), TTC position may see link margin improve while Payload position still shows old imaging status.

**Impact:** Operators at different positions see inconsistent spacecraft state during fast-moving events (anomalies, mode transitions).

**Fix:**
- Implement **atomic telemetry snapshots:** All HK packets from single spacecraft tick bundled in one WebSocket broadcast.
- All positions refresh from same snapshot (eliminate partial updates).
- Latency impact: negligible; already polling at 1–2 Hz.

---

### 8.2 Cross-Subsystem Dependency Awareness
**Gap:** Payload position sees PAYLOAD_ON command accepted; doesn't know that EPS position, concurrently, is transitioning to load shedding Stage 1 (which would power off payload anyway). Payload operator assumes command succeeded; TCS operator sees unexpected cooler shutdown.

**Impact:** Confusion during contingency response; operators work at cross-purposes.

**Fix:**
- MCS displays **subsystem dependency graph** (read-only for non-FD positions):
  - Payload ← EPS load shedding
  - Cooler ← TCS thermostat AND EPS power line
  - RW desaturation ← AOCS momentum AND TTC contact availability
- When one position makes a change affecting another, emit **dependency change alert** to dependent position (e.g., "TTC just enabled TX; EPS power draw now +25W").

---

### 8.3 Contact Window Coordination Between TTC and Payload
**Gap:** TTC operator sees next contact window (AOS 14:32 UTC, 9 min duration). Payload operator independently plans imaging for same window. If imaging requires 5 min, and data dump requires 4 min, they overlap — contact window too short for both.

**Impact:** Operators discover conflict at last minute; scramble to reschedule.

**Fix:**
- Implement **activity conflict detector** in MCS (or planner):
  - When Payload operator schedules imaging for 14:32–14:37, auto-calculate remaining time for dump.
  - Display: "Imaging 5 min (14:32–14:37) → 4 min remaining for dump (14:37–14:41); 2 min margin lost."
  - Recommend: "Defer imaging to next pass [15:03 UTC, 12 min window] for more margin."

---

### 8.4 FDIR Escalation Routing to Correct Position
**Gap:** FDIR system detects RW overspeed (S12 violation) and auto-executes recovery (disable RW via S19). AOCS position doesn't know it was triggered; may assume RW is still available and attempt momentum management.

**Impact:** Recovery action fails silently; AOCS operator unaware.

**Fix:**
- Implement **escalation event** in S5 stream when FDIR triggers Level 2+ response:
  - Event includes: fault_id, owner_position, recommended_action, escalation_level.
  - MCS routes event notification to owner position (e.g., "AOCS position: RW1 overspeed detected; FDIR entered safe mode; check AOCS_ANOMALY procedure").
- AOCS position acknowledges (marks event read) or escalates to FD.

---

### 8.5 Command Verification & Approval Workflow Across Positions
**Gap:** Payload position sends "PAYLOAD_ON" command. EPS position concurrently detects battery SoC < 20% and tries to execute load shedding Stage 2 (which turns payload off). Both commands hit simulator; order of execution is undefined.

**Impact:** Command ordering bugs; unpredictable state after high-load events.

**Fix:**
- Implement **command approval gate** for critical operations (esp. during contingencies):
  - Commands that conflict with FDIR or other in-flight operations are held in **PENDING** state.
  - FD position sees pending queue, reviews conflicts, approves or rejects.
  - Non-FD positions see "command approved by FD at T+12:34" (audit trail).
- Examples of critical commands: PAYLOAD_ON (when SoC < 30%), OBC_REBOOT, DELETE_STORE, AOCS_MODE_CHANGE.

---

## 9. Top 10 Prioritised Defects for Issue Tracker

### Defect 1: "Procedure interactive step execution missing telemetry binding"
**Severity:** MEDIUM
**Category:** Feature (Category 2)
**Affected Components:** MCS displays, procedure executor, simulator HK stream
**Description:** Operators manually verify procedure step completion (e.g., "is AOCS in safe mode now?") by reading HK displays. Procedures should include telemetry wait-conditions and MCS should show real-time step verification status.
**Suggested Fix:**
1. Extend YAML procedure schema to support wait-conditions:
```yaml
  - step_id: 2
    name: "AOCS Safe Mode"
    command: ...
    wait_condition:
      parameter_path: "aocs.mode"
      expected_value: 2
      timeout_s: 30
```
2. Implement condition evaluator in procedure executor.
3. MCS displays step status with live parameter value (e.g., "Waiting for AOCS mode == 2: current 1").
4. Tests: Verify step passes when condition met, fails when timeout.

---

### Defect 2: "No post-pass delayed telemetry processor; cannot analyze trends"
**Severity:** MEDIUM–HIGH
**Category:** Not implemented (Category 3)
**Affected Components:** Simulator TM storage, planner, new TM archive/analysis tool
**Description:** After each contact pass, downlinked TM is lost. No archival, no trend analysis, no anomaly detection. Multi-day SoC decay, temperature rise, RW momentum trends cannot be studied; training scenario replay impossible.
**Suggested Fix:**
1. Add TM archival to MCS:
   - Store all HK packets to time-indexed SQLite DB per pass.
   - One table per HK SID (eps_hk, aocs_hk, ...).
2. Implement post-pass processor:
   - Compute energy balance (generation − consumption), delta-SoC.
   - Detect temperature rise (thermal capacity / cooling effectiveness).
   - Flag rate-of-change anomalies (SoC drop > 3%/h expected → anomaly).
3. Generate pass summary report (JSON).
4. Integrate with planner: Next-pass recommendations based on trend analysis.
5. Tests: Replay LEOP scenario, verify TM archival, check trend calculations.

---

### Defect 3: "Activity types include impossible procedures (orbit maintenance without thrusters)"
**Severity:** MEDIUM
**Category:** Inconsistent (Category 5)
**Affected Components:** Planner activity types, simulator procedure catalog
**Description:** EOSAT-1 has no thrusters; `orbit_maintenance` and `station_keeping` activity types reference procedures NOM-003, NOM-004 which assume thruster availability. Operators can schedule these activities; planner validates power constraints; but execution fails silently.
**Suggested Fix:**
1. Remove `orbit_maintenance`, `station_keeping` from `configs/eosat1/planning/activity_types.yaml`.
2. Document in mission.yaml: "EOSAT-1 is passive-decay; no active station-keeping."
3. Add constraint in planner: Reject any orbit-adjustment activity with error "No propulsion system available."
4. Tests: Verify planner rejects orbit maintenance activities; verify remaining 5 activity types execute correctly.

---

### Defect 4: "No centralized flight rules; S12/S19/planner thresholds inconsistent"
**Severity:** MEDIUM
**Category:** Not described (Category 3)
**Affected Components:** Simulator S12/S19 rules, planner constraint checkers, MCS displays
**Description:** Battery SoC thresholds vary: planner blocks imaging at 30%, simulator triggers load shedding at 20%, MCS displays warnings at multiple levels. No single source of truth.
**Suggested Fix:**
1. Create `configs/eosat1/flight_rules.yaml`:
```yaml
rules:
  FR-001:
    name: "Minimum battery SoC for imaging"
    threshold_soc: 0.30
    severity: ERROR
    action: "Block imaging pass in planner; auto-turn off payload"
    enforced_by: [planner, s19_rule, mcs_display]
```
2. S12, S19, planner all read this central config.
3. MCS displays thresholds in colored zones (green/yellow/orange/red).
4. Tests: Verify all components respect same thresholds; test planner and simulator agreement at boundary conditions.

---

### Defect 5: "MCS position permissions not data-driven; function catalog missing"
**Severity:** LOW–MEDIUM
**Category:** Inconsistent (Category 5)
**Affected Components:** MCS, simulator S8 dispatch
**Description:** MCS restricts positions by `allowed_func_ids` (hard-coded lists); no readable function catalog. Operators don't know which func_id corresponds to which command.
**Suggested Fix:**
1. Create `configs/eosat1/s8_function_catalog.yaml`:
```yaml
functions:
  0: { name: "AOCS_SET_MODE", subsystem: "aocs", description: "Change AOCS operational mode", positions: [flight_director, aocs] }
```
2. MCS loads catalog at startup.
3. Commanding tab displays function names (not just IDs).
4. Position permission checks reference names.
5. Tests: Verify catalog completeness against simulator; test position filtering.

---

### Defect 6: "No command sequencing / mutual exclusion for concurrent FDIR + operator commands"
**Severity:** MEDIUM–HIGH
**Category:** Inconsistent (Category 5)
**Affected Components:** Simulator service dispatch, procedure executor
**Description:** If FDIR executes load shedding (turn off payload) while operator manually commands payload on, no interlock prevents conflict. Both execute; final state undefined.
**Suggested Fix:**
1. Implement **command sequencer** in simulator:
   - Queue all S8 commands (direct + procedure-based).
   - Execute sequentially with locking per resource (e.g., "payload_mode" locked by active procedure).
2. Emit S5 events: COMMAND_QUEUED, COMMAND_EXECUTING, COMMAND_COMPLETED.
3. Add **FDIR override mode** (autonomous / manual / supervised).
4. MCS displays queue status and override mode (FD position only).
5. Tests: Simultaneous load shedding + operator payload command; verify no race conditions.

---

### Defect 7: "Planner-to-MCS activity execution feedback missing; no real-time plan tracking"
**Severity:** MEDIUM
**Category:** Not implemented (Category 3)
**Affected Components:** Planner API, MCS displays, simulator procedure executor
**Description:** Planner generates activity plan (10 passes, imaging at passes 3, 5, 7). MCS does not track whether planned activities are executing. Operators cannot see real-time progress against plan.
**Suggested Fix:**
1. Add planner endpoint `/api/activities/{pass_id}/execute` (MCS calls when procedure starts).
2. Planner tracks: activity_id, planned_time, actual_start_time, actual_end_time, outcome (success/timeout/abort).
3. MCS displays **activity timeline** (Gantt chart):
   - Planned bars (blue), actual progress bars (green if on-time, orange if delayed, red if failed).
   - Click to see activity constraints validated, actions logged, anomalies encountered.
4. Tests: Plan 5 passes with mixed activities; execute in simulator; verify timeline accuracy.

---

### Defect 8: "Cross-position dependency awareness missing; payload + EPS coordination unclear"
**Severity:** MEDIUM
**Category:** Not described (Category 3)
**Affected Components:** MCS displays (multiple positions), simulator event stream
**Description:** Payload operator schedules imaging; doesn't see that EPS is in load shedding Stage 1 (which reduces payload power). Operators work at cross-purposes.
**Suggested Fix:**
1. Implement **subsystem dependency alerts** in MCS:
   - When EPS changes load shedding stage, alert Payload, TCS, AOCS positions.
   - Display: "EPS entered Stage 1 at T+12:34; payload power limited; imaging may not complete."
2. Add **cross-position coordination checklist** in procedure executor:
   - Before payload imaging, verify: EPS SoC > 30%, TCS cooler available, AOCS attitude stable.
   - If not met, highlight dependencies (yellow) vs. show blocker (red).
3. Tests: Trigger load shedding during imaging plan; verify alerts reach dependent positions.

---

### Defect 9: "FDIR escalation routing not implemented; anomalies not routed to correct position"
**Severity:** MEDIUM
**Category:** Not described (Category 3)
**Affected Components:** Simulator S12/S19, MCS notifications, position routing
**Description:** Simulator detects RW overspeed; FDIR executes recovery; but AOCS position is unaware. No escalation routing or task assignment to correct operator.
**Suggested Fix:**
1. Extend S5 event catalog with escalation metadata:
```yaml
FDIR_FAULT_DETECTED:
  event_id: 0x0F00
  owner_position: "aocs"  # Route to this position
  recommended_procedure: "AOCS_ANOMALY"
  escalation_level: 2
```
2. MCS routes escalation events to owner position (banner notification, add to task list).
3. Position acknowledges (event marked read) or escalates to FD (event escalated to flight_director).
4. Escalation timeout: If position doesn't acknowledge in 5 min, auto-escalate to FD.
5. Tests: Trigger S12 violation; verify event routed to correct position; test escalation timeout.

---

### Defect 10: "No shared telemetry consistency guarantees; independent position refreshes cause drift"
**Severity:** LOW–MEDIUM
**Category:** Not described (Category 3)
**Affected Components:** MCS server, WebSocket broadcast, all position displays
**Description:** Each position refreshes HK independently (2–5 sec poll). During fast anomalies, TTC position sees updated link margin while Payload position still reads stale imaging_active flag. Inconsistent state across positions.
**Suggested Fix:**
1. Implement **atomic telemetry snapshots**:
   - Simulator bundles all HK packets from single tick into one snapshot.
   - MCS broadcasts snapshot to all positions via WebSocket.
   - All positions refresh from same snapshot (no stale data).
2. Add timestamp to snapshot (simulator tick time).
3. MCS display shows snapshot timestamp (debug info, non-FD positions see transparency).
4. Tests: Subscribe 6 positions to WebSocket; verify all see identical HK at same timestamp.

---

## 10. LEOP-to-Nominal Readiness Assessment

### Summary Scorecard by Subsystem

| Subsystem | LEOP Phase | Commissioning | Nominal | Constraint |
|-----------|------------|---------------|---------|-----------|
| **AOCS** | 🟢 GREEN | 🟡 AMBER | 🟡 AMBER | No slew command; momentum management manual |
| **EPS** | 🟢 GREEN | 🟢 GREEN | 🟢 GREEN | Load shedding fully autonomous; good |
| **TCS** | 🟢 GREEN | 🟡 AMBER | 🟡 AMBER | Thermal model weak; duty cycle constraints manual |
| **TTC** | 🟢 GREEN | 🟢 GREEN | 🟢 GREEN | Link budget realistic; no antenna issues |
| **OBDH** | 🟢 GREEN | 🟢 GREEN | 🟢 GREEN | Bootloader, app boot, CAN bus well-modeled |
| **Payload** | 🟡 AMBER | 🟡 AMBER | 🟡 AMBER | No imaging pre-conditions verified; cooler logic basic |
| **MCS** | 🟢 GREEN | 🟢 GREEN | 🟡 AMBER | Displays good; no procedure automation, no activity tracking |
| **Planner** | 🟢 GREEN | 🟡 AMBER | 🟡 AMBER | Constraint checking good; impossible activities present |
| **FDIR** | 🟢 GREEN | 🟢 GREEN | 🟢 GREEN | Autonomous fault response complete; manual override missing |
| **Procedures** | 🟢 GREEN | 🟢 GREEN | 🟡 AMBER | 51 procedures defined; no telemetry step verification |

---

### LEOP Phase: 🟢 GREEN (Ready)

**What's ready:**
- Separation scenario with 30-min timer and phase progression.
- Bootloader mode (SID 10, beacon telemetry).
- Application boot (10s timer, automatic transition).
- Initial power-on sequence (OBC/RX unswitchable, payload/TX/wheels switchable).
- Beacon reception and link acquisition (TTC model complete).
- Initial attitude (separation tumble, detumble via B-dot, coarse sun point via CSS).
- Dual-ground-station contact scheduling (planner SGP4).
- Time synchronization (S9.1 set_time).
- Initial health check procedures (LEOP-001 through LEOP-007 defined).

**Constraints:**
- AOCS slew rate fixed at 2 deg/s (no rate command); commissioning mode changes will be slow (30+ sec settling).
- Thermal control is heater on/off only (no active cooling during early ops); FPA temperature may rise during payload commissioning if not carefully scheduled.

**Readiness:** Operators can execute full LEOP sequence from separation through first contact and initial commissioning.

---

### Commissioning Phase: 🟡 AMBER (Ready with Manual Procedures)

**What's ready:**
- EPS power lines: all 8 switchable via S8.13-15 (manifest in positions.yaml).
- AOCS modes: 9 modes, transitions validated in tests.
- TTC antenna deployment and beacon-mode toggle.
- Payload power-on and initial diagnostics.
- TCS heater control (on/off, no setpoint adjustment).
- 13 commissioning procedures (COM-001 through COM-012) defined.

**Constraints:**
- Procedures are text-only (markdown); no interactive MCS guidance (e.g., "Waiting for AOCS mode transition...").
- AOCS momentum management manual (no auto-desaturation; operator must schedule desaturation windows).
- Thermal duty cycle manual (operator must track imaging cumulative time; cooler on/off manual).
- Payload cooler has basic model (setpoint control not exposed); FPA temperature rises quickly without active cooling.

**Readiness:** Commissioning can proceed with careful operator oversight; recommend pairing each procedure with a separate instructor/simulations engineer for real-time telemetry monitoring.

---

### Nominal Operations: 🟡 AMBER (Constrained, Functional)

**What's ready:**
- Routine HK streaming (S3 SID 1–6, 10).
- Periodic contact windows (planner produces 10-pass lookahead).
- Autonomous FDIR (S12 monitoring, S19 event-action, load shedding, fault propagation).
- Data downlink via S15 (TM storage, block retrieval).
- Imaging procedures (NOM-001 startup, imaging session sequences).
- Momentum management via manual desaturation (operator schedules, S8.1 command executes).
- Payload imaging with FPA cooler (manual on/off, no active thermal feedback).
- Software upload (S6 memory management, simplified model).

**Constraints:**
- **Activity planning:** Planner includes impossible activities (orbit maintenance, station-keeping); remove or operators will waste time planning what cannot execute.
- **Procedure automation:** Activity scheduler does not execute plans; operator must manually start procedures each pass.
- **Trend analysis:** No post-pass archival or anomaly detection; SoC trends over 3–7 days cannot be visualized.
- **Attitude constraints:** Slew-to-quaternion command not implemented; reorienting to different target takes manual mode transitions (slow).
- **Thermal constraints:** Duty cycle enforcement manual; cooler on/off logic basic.

**Readiness:** Nominal ops can proceed with disciplined ops planning and real-time constraint checking via MCS. Recommend flight director performs daily pass planning and reviews power/thermal budgets manually.

---

### Overall LEOP-to-Nominal Readiness: 🟡 AMBER

**Green (Operational):**
- Simulator physics and PUS services (13 services, 50+ commands, 120+ events).
- MCS displays and commanding (6 positions, 40+ endpoints).
- FDIR autonomous response (S12 rules, S19 rules, load shedding, fault propagation).
- Procedure framework (51 procedures, executor, YAML sequencing).
- Contact scheduling (planner, 10-pass lookahead).

**Amber (Manual Workarounds Required):**
- AOCS slew command (use mode transitions instead; slower).
- Momentum management (manual scheduling; no auto-trigger on saturation).
- Thermal duty cycle (operator tracks cumulative imaging time; no real-time enforcement).
- Activity planning (planner includes impossible activities; editor removes them manually).
- Procedure step verification (operator reads HK displays; no auto-check).
- Closed-loop activity execution (operator must manually start each procedure; no automation).
- Trend analysis (no TM archival; cannot review multi-pass patterns).

**Red (Blocking or Critical):**
- None. Mission can operate.

**Recommendation:**
1. **Immediate (before LEOP):** Remove impossible activities (orbit maintenance, station-keeping) from planner.
2. **Pre-Nominal (by week 3 of commissioning):** Implement procedure step telemetry binding so operators don't manually verify each step.
3. **Post-First-Light:** Implement closed-loop activity execution (planner → MCS → procedure executor) so multi-pass imaging campaigns can run autonomously during eclipse phases.
4. **Ongoing:** Capture TM archival and trend analysis tool for 3–6 month mission analysis.

---

## Appendix: Sources & References

### Web Sources (Research Phase)
- [LEOP Best Practices for Small Satellites (SSC24)](https://digitalcommons.usu.edu/cgi/viewcontent.cgi?article=6094&context=smallsat)
- [Mission Control Room Coordination (ESA ESOC)](https://www.esa.int/Enabling_Support/Operations/Who_does_what_at_ESA_Mission_Control)
- [Flight Rules and Anomaly Response](https://www.space-track.org/documents/SFS_Handbook_For_Operators_V1.7.pdf)
- [Planning Tool / MCS Integration (Terma PLAN, Alén Space MCS)](https://www.terma.com/products/space/plan/)
- [Delayed Telemetry Analysis (OPS-SAT, ESA)](https://opssat.esa.int/notebooks/tm_analysis.html)

### Codebase References
- Architecture: `/docs/architecture.md`
- Implementation Summaries: `IMPLEMENTATION_SUMMARY.md`, `FDIR_ENHANCEMENTS.md`, `CONSTRAINT_IMPLEMENTATION_SUMMARY.md`
- Flight Director Requirements: `/docs/ops_research/flight_director_requirements.md`
- Flight Director Role Analysis: `/configs/eosat1/mcs/role_analysis/flight_director_role.md`
- Separation Scenario: `SEPARATION_SCENARIO.md`
- FDIR Quick Reference: `FDIR_QUICK_REFERENCE.md`
- Gap Analysis: `/docs/gap_analysis/subsystem_gap_analysis.md`
- Operations Guide: `/docs/OPERATIONS_GUIDE.md`
- Existing Defects: `/defects/01-*.md` through `/defects/07-*.md`

### Tests & Validation
- LEOP integration: `tests/test_integration/test_leop_end_to_end.py`
- Nominal operations: `tests/test_integration/test_nominal_orbit.py`
- Procedure testing: `tests/test_commissioning/test_leop_sequence.py`
- Simulator LEOP engine: `tests/test_simulator/test_leop_engine.py`

---

**End of Flight Director Review**

**Status:** Ready for LEOP. Recommend removing impossible activities (Defect 3) before operations commence. All phase transitions (separation → bootloader → app → commissioning → nominal) have simulator support, MCS visibility, and procedure definitions. FDIR autonomy is mature. Remaining gaps (Defects 1–2, 4–10) are enhancements for improved operator experience and training; not blockers.

**Next Review:** Post-first-light (Day 3 of commissioning) to assess actual LEOP performance against simulator predictions.

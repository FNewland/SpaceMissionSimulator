# OBDH Operability Review — EO Satellite Mission Simulator

**Review Date:** 2026-04-06
**Scope:** ECSS PUS-C on-board data handling (OBDH) service coverage, operator visibility, and commanding capability for the SMO Simulator
**Codebase Root:** `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation`

---

## 1. Scope & Assumptions

This review assesses operability gaps in the OBDH subsystem of the EO satellite mission simulator against ECSS-E-ST-70-41C (PUS-C) and ECSS-E-ST-50-01C standards for:

- **Boot sequences:** Bootloader → application software, image switching, and recovery.
- **Time management (S9):** Onboard time correlation via UTC synchronization.
- **Housekeeping (S3):** HK generation, SID definition, and interval control.
- **Event reporting (S5):** Event type filtering and dispatch.
- **Memory management (S6):** Memory load/dump/check with integrity verification.
- **Function commanding (S8):** Subsystem-level commands routed through logical function IDs.
- **Scheduled commanding (S11):** Time-tagged TC scheduling and execution.
- **On-board monitoring (S12):** Parameter limit checking with out-of-limit event generation.
- **Large data transfer (S13):** Scene download and segmented image transmission.
- **Onboard storage (S15):** TM packet storage with circular/stop-when-full modes and paced dump.
- **Connection test (S17):** Link verification commands.
- **Event-action (S19):** Reactive automation linking events to S8 function commands.
- **Parameter management (S20):** Runtime parameter value load/read.
- **Device access (S2):** Device-level on/off control.

**Assumptions:**
- The OBDH operator workflow includes: LEOP boot → time sync (S9) → HK enablement (S3) → scheduled TCs (S11) → parameter monitoring (S12) → dump scheduling (S15).
- The Mission Control Station (MCS) frontend provides operator visibility into every PUS service state and command capability.
- All parameters referenced must be accessible via S20 read, reported in HK, and commandable via S8 or S2.
- Bootloader and application software have distinct APIDs and distinct HK behavior.

---

## 2. Category 1 — Described, Implemented, Works

### S1 Request Verification
- **Status:** Fully implemented.
- **Coverage:** S1.1 (acceptance), S1.3 (execution start success), S1.4 (execution start failure), S1.5 (execution progress), S1.7 (completion success).
- **Implementation:** `service_dispatch.py:generate_s1_reports()`, `generate_s1_progress()`, `generate_s1_exec_fail()`.
- **Operator visibility:** TC acceptance/rejection reported inline in MCS.

### S2 Device Access
- **Status:** Fully implemented.
- **Coverage:** S2.1 (on/off), S2.5 (on/off with verification), S2.6 (status report).
- **Devices covered:**
  - OBDH: 0x0500–0x050F (OBC-A, OBC-B, MMU, watchdog, CAN interface).
  - EPS: 0x0100–0x010F.
  - AOCS: 0x0200–0x021F.
  - TCS: 0x0300–0x030F.
  - TTC: 0x0400–0x040F.
  - Payload: 0x0600–0x060F.
- **Operator visibility:** Device state queryable via S2.6 reports.

### S3 Housekeeping
- **Status:** Fully implemented with extended subtypes.
- **Coverage:**
  - S3.1 (create HK SID) — dynamic SID definition.
  - S3.2 (delete HK SID).
  - S3.5 (enable periodic HK).
  - S3.6 (disable periodic HK).
  - S3.27 (one-shot HK report).
  - S3.31 (modify HK interval).
- **SIDs defined in config:**
  - SID 1 (EPS) @ 1 s.
  - SID 2 (AOCS) @ 4 s.
  - SID 3 (TCS) @ 60 s.
  - SID 4 (TTC) @ 8 s.
  - SID 5 (Payload) @ 8 s.
  - SID 6 (OBDH) @ 8 s.
  - SID 11 (Beacon, bootloader only) @ 30 s.
- **Operator visibility:** S3 commands enable/disable HK generation; MCS displays HK parameters in real-time.

### S5 Event Reporting
- **Status:** Fully implemented.
- **Coverage:** S5.5 (enable event type), S5.6 (disable), S5.7 (enable all), S5.8 (disable all).
- **Event filtering:** Operator can enable/disable event types (0–255) for reporting.
- **Operator visibility:** Event on/off state managed via S5 commands.

### S6 Memory Management
- **Status:** Implemented with simulated memory regions.
- **Coverage:** S6.2 (memory load), S6.5 (memory dump), S6.9 (memory check with CRC-16-CCITT).
- **Features:**
  - Read-only region protection (0x00000000–0x0001FFFF = Boot ROM).
  - Simulated memory patterns for different regions.
  - CRC-16-CCITT integrity check.
- **Operator visibility:** S6 dump/check commands return memory content and CRC for verification.

### S8 Function Management
- **Status:** Fully implemented with comprehensive routing.
- **Function IDs by subsystem:**
  - AOCS: 0–15 (mode, desaturate, wheel control, star tracker, reaction wheels, MTQ, momentum check, slew, acquisition, calibration, momentum ramp).
  - EPS: 16–25 (payload mode, FPA cooler, transponder TX, power lines, load switching, charge rate, solar array).
  - Payload: 26–39 (mode, scene, capture, download, delete, band config, integration, gain, cooler, calibration, compression).
  - TCS: 40–49 (heater circuits, FPA cooler, setpoints, auto mode, duty limits, decontamination).
  - OBDH: 50–62 (mode, memory scrub, OBC reboot, unit switch, bus select, boot app, inhibit, watchdog, diagnostic, error log).
  - TTC: 63–78 (transponder switch, TM rate, PA, TX power, antennas, beacon, command channel, frequency, modulation, ranging, coherence).
- **Operator visibility:** All S8 commands executed through MCS with feedback via S1 reports.

### S9 Time Management
- **Status:** Implemented.
- **Coverage:** S9.1 (time set), S9.2 (time report).
- **Features:** CUC time (seconds since epoch) synchronized to OBDH internal clock.
- **Operator visibility:** Time queried via S9.2; set via S9.1.

### S11 Time-Tagged Scheduling
- **Status:** Implemented with command list and status reporting.
- **Coverage:** S11.4 (insert), S11.5 (report), S11.7 (delete), S11.9 (disable), S11.11 (delete all), S11.13 (enable), S11.17 (list commands), S11.18 (list report).
- **Features:** In-memory scheduler with 2-byte command IDs; paced execution by engine tick loop.
- **Operator visibility:** Schedule list, insert, delete, enable/disable visible via MCS. No schedule editor in current MCS (see Category 3).

### S15 Onboard TM Storage
- **Status:** Fully implemented with circular and stop-when-full modes.
- **Coverage:** S15.1 (enable store), S15.2 (disable store), S15.9 (dump), S15.11 (clear store), S15.13 (status report).
- **Stores:**
  - Store 1 (HK_Store): circular, 18,000 packets (≥90 min @ 1.64 pkt/s nominal rate).
  - Store 2 (Event_Store): stop-when-full, 2,000 packets.
  - Store 3 (Science_Store): stop-when-full, 10,000 packets.
  - Store 4 (Alarm_Store): stop-when-full, 500 packets.
- **Features:** Paced dump emission (S15.9) respects TTC downlink data rate; auto-clear on completion.
- **Operator visibility:** Store status (count, capacity, enabled) reported via S15.13.

### S17 Connection Test
- **Status:** Implemented.
- **Coverage:** S17.1 (echo packet).
- **Operator visibility:** Link health verification via MCS.

### S20 Parameter Management
- **Status:** Fully implemented.
- **Coverage:** S20.1 (set parameter), S20.3 (read parameter).
- **Parameters:** All 30+ OBDH parameters (see Section 9) accessible via S20.
- **Operator visibility:** Real-time parameter display in MCS; can modify and read back any parameter.

---

## 3. Category 2 — Described but Not Implemented

### S12 On-Board Monitoring — Partial
- **What's there:** Basic S12.6 (add definition), S12.7 (delete), S12.12 (report definitions).
- **What's missing:**
  - **No S12.1–S12.2 enable/disable global monitoring** in a persistent way; flags exist but not exposed via commanding.
  - **No delta-change monitoring** (only absolute limits). Delta check_type field in definition but not evaluated.
  - **No persistence across reboot.** Monitoring definitions are lost if OBC reboots.
  - **No monitoring result reporting (S12.128).** Violation events generated (emitted as S5) but no unified S12 report summarizing all active violations.
  - **No parameter limit history or hysteresis.** Once a limit is violated, every tick can re-emit an event.
- **Operator gap:** Operator cannot see what monitoring is currently active without issuing S12.12 query. Cannot persist monitoring across a reboot. Cannot command enable/disable monitoring globally.
- **Affected files:** `service_dispatch.py:_handle_s12()`, `engine.py:check_monitoring()`.

### S19 Event-Action — Partial
- **What's there:** S19.1 (add rule), S19.2 (delete), S19.4 (enable), S19.5 (disable), S19.128 (report all).
- **What's missing:**
  - **No persistence of rules across reboot.** Rules stored in memory; lost on OBC reset.
  - **No rule priority or chaining.** Cannot specify rule order; all enabled rules fire on event match.
  - **No parameter limit triggers.** S19 rules can match event IDs but not structured parameter limit violations.
  - **No complex action logic.** Only single S8 function execution per event; no conditional branches, loops, or multi-step procedures.
  - **Limited event matching.** Rules match exact event ID or parameter ID; no wildcards or ranges.
- **Operator gap:** Operator must re-load event-action rules after every reboot. Cannot define a procedure that reacts to a parameter trend (e.g., "if battery voltage drops 10% in 30 seconds, trigger load shed"). Cannot specify rule priority.
- **Affected files:** `service_dispatch.py:_handle_s19()`, `trigger_event_action()`.

### S12/S19 Configuration Persistence
- **What's missing:** YAML or database persistence for S12 definitions and S19 rules.
- **Impact:** Both S12 and S19 state is volatile; operator must reload after every reboot.
- **Operator gap:** Time-consuming to restore operational monitoring/automation after unexpected reboot (e.g., OBC watchdog timeout).

### Scheduled TC Execution Visibility
- **What's missing:** MCS display of currently-scheduled commands and their execution time windows. No graphical schedule editor.
- **Impact:** Operator cannot easily verify that a scheduled TC will execute at the desired time without querying S11.17.
- **Files:** `packages/smo-mcs/src/smo_mcs/displays/` (no schedule_view.py).

---

## 4. Category 3 — Not Yet Described but Needed

### S3 HK Definition Persistence
- **Need:** SID definitions created via S3.1 should be saved to config and restored after OBC reboot, rather than lost.
- **Current gap:** Only pre-configured SIDs (1–6, 11) persist; custom SIDs created via S3.1 are ephemeral.
- **Impact:** Operator must re-create custom HK SIDs after reboot (rare but disruptive).
- **Suggested implementation:** Add S3 definition state to OBDH subsystem checkpoint (future S18 recovery support).

### S6 Memory Patching with Signature Verification
- **Need:** S6 memory load should support flash sector erase/write with CRC signature verification before commit.
- **Current gap:** S6.2 (memory load) accepts data but does not verify integrity or atomic write semantics.
- **Impact:** Payload/application software upload workflow (key Phase 4 procedure) lacks verification step.
- **Suggested implementation:** Add S6.2 subtype variant with CRC-verify-before-commit, or layer a new S18 (reload software image) service.

### Onboard Time Correlation (S9) — PTP/GPS Sync
- **Need:** Real spacecraft implement continuous time correlation via PTP or GPS; simulator should model steady-state time error and drift correction.
- **Current gap:** S9.1 sets time as absolute CUC; no background sync daemon or drift model.
- **Impact:** Operator cannot test time drift compensation procedures or verify sync margin.
- **Suggested implementation:** Add time_drift_ppm parameter to OBDH config; simulator increments OBDH time independently from ground, introducing measurable drift; S9 reports current error.

### S11 Schedule Overflow Handling
- **Need:** Scheduler should reject new commands if queue is full (stop-when-full), or emit event if queue depth exceeds threshold.
- **Current gap:** `tc_scheduler.py` has no check for max queue size or overflow event generation.
- **Impact:** Operator cannot monitor schedule saturation risk during intensive planning windows.
- **Suggested implementation:** Track queue depth; emit S5 event (0x0309 "TC_QUEUE_OVERFLOW") if depth ≥ threshold (e.g., 80% capacity).

### Bootloader Timeout and Safe-Mode Entry
- **Need:** Bootloader should timeout after N seconds (default 300 s) if application boot does not complete; should enter safe mode with reduced HK rate.
- **Current gap:** Boot pending timer (10 s) is local to application CRC check; no bootloader timeout or safe-mode transition is modeled.
- **Impact:** Operator cannot rehearse LEOP if OBC stuck in bootloader (e.g., corrupted app image).
- **Suggested implementation:** Add `boot_timeout_s` parameter to OBDH config; if boot_app_pending exceeds timeout, emit BOOT_TIMEOUT event, enter safe mode, reduce HK intervals by 2×.

### S12 Monitoring — Delta and Hysteresis
- **Need:** S12 should support delta-change monitoring (e.g., alert if parameter changes >5% since last check) and hysteresis to prevent chatter.
- **Current gap:** check_type field parsed but not evaluated; no hysteresis implemented.
- **Impact:** Monitoring rules cannot detect rate-of-change anomalies (key for early warning of degradation).
- **Suggested implementation:** Add delta_threshold and hysteresis_band to S12 definition; check_monitoring() evaluates both absolute and delta limits; emit event only if crossing hysteresis boundary.

### Watchdog Timer Modeling
- **Need:** Watchdog should model realistic timeout behavior: armed in nominal mode only, counts ticks, can be fed (reset) by specific commands, generates periodic warning events before final timeout.
- **Current gap:** Watchdog armed/disarmed but trigger on mode change or countstep is simplistic; no pre-warning events.
- **Impact:** Operator cannot test watchdog servicing procedures or validate fault recovery timing.
- **Suggested implementation:** Add watchdog_warning_threshold (e.g., 80% of period); emit event if reached; final timeout at 100%.

### Memory Scrub — Progress and Recovery
- **Need:** Memory scrub should update progress in real-time and generate completion events; operator should see scrub progress in HK.
- **Current gap:** Scrub is implemented (command "memory_scrub" triggers); progress displayed in HK (0x031A), but no fine-grained control (pause, resume, cancel).
- **Impact:** Operator cannot optimize memory scrub scheduling for power constraints.
- **Suggested implementation:** Add S8 func ID for scrub pause/resume; update HK progress every tick; emit event on completion.

### Single-Event Upset (SEU) Reporting
- **Need:** SEU counter (0x0319) increments on random events; operator should be able to query and clear SEU count independently of reboot.
- **Current gap:** SEU counter increments but cannot be reset except via full OBC reboot.
- **Impact:** Operator cannot measure SEU rate over a specific window (e.g., "how many SEUs in last 24 h?").
- **Suggested implementation:** Add S8 function ID to clear SEU counter; report SEU count in HK.

---

## 5. Category 4 — Implemented but Not Helpful for This Mission

### S13 Large Data Transfer
- **Status:** Implemented (S13.1–S13.9) but low fidelity.
- **Issue:** Scene download is simulated with synthetic block data; no integration with payload model or actual image storage.
- **Impact:** Operator cannot test downlink scheduling or validate compression/throughput trade-offs.
- **Recommendation:** For Phase 5+ mission fidelity, integrate payload image buffer model into S13; simulate realistic image sizes and downlink rates.

### S23 File Management
- **Status:** Not implemented.
- **Issue:** Simulator does not model onboard file system; hence S23 (file create, read, write, delete, list directory) has no semantic meaning.
- **Impact:** Low priority for current test scenarios; Phase 5+ may need file management for payload data ingestion.

---

## 6. Category 5 — Inconsistent / Incoherent Implementation

### OBDH Parameter 0x0312 (hktm_buf_fill) — Known Defect
- **Root cause:** Parameter `hktm_buf_fill` in `obdh_basic.py:OBDHState` is a monotonically-incrementing counter (line 84, 267–269) incremented on every HK packet generated. It is never reset or clamped to 100%, causing it to reach unphysical values (353% after ~2 minutes).
- **Correct behavior:** Should read from `OnboardTMStorage.get_status()[1]['fill_pct']`, which is authoritative and physically clamped to [0, 100].
- **Current MCS binding:** Displays `hktm_buf_fill` (param 0x0312) directly without normalization.
- **Impact:** Operator sees impossible HK buffer fill percentage, misleading go/no-go decisions for S15 dump scheduling.
- **Fix:** Replace line 350 in `obdh_basic.py` with: `shared_params[0x0312] = float(self._tm_storage.get_status()[0]['fill_pct'])` (requires storing TM storage reference in OBDH model).

### Boot Sequence Gating — Application-only HK
- **Status:** Inconsistency in filtering.
- **Issue:** Engine checks `_enqueue_tm()` gate: only enqueues HK if `sw_image == SW_APPLICATION` (not bootloader). However, SID 11 (bootloader beacon) is configured for bootloader mode but SID 11 HK structure assignment is unclear.
- **Files affected:** `engine.py:_enqueue_tm()`, `obdh_basic.py`.
- **Impact:** Bootloader HK is disabled during Phase 3 (bootloader mode), making operator blind to bootloader-only telemetry. Beacon (SID 11) should be active in bootloader but filtering prevents it.
- **Suggested fix:** Explicitly check `(sw_image == SW_BOOTLOADER and sid == 11) or (sw_image == SW_APPLICATION)` before enqueuing HK.

### Redundant OBC Cold-Start Synchronization
- **Status:** Inconsistency in cold-redundancy model.
- **Issue:** When OBC-B cold-start is triggered (S8 func 53 "obc_switch_unit"), the simulator calls `_switchover()` which calls `_reboot(REBOOT_SWITCHOVER)`. This resets all TC counters, reboot count, and timers. However, no state transfer mechanism or cross-unit communication is modeled.
- **Impact:** Operator cannot verify that a cold-standby OBC can bootstrap from the same schedule and configuration state as OBC-A (real hardware would require state transfer or synchronized startup).
- **Suggested fix:** Model standby OBC as having a copy of the current schedule, parameters, and monitoring definitions; do not discard them on switchover.

### S3 SID Interval Modification — No Validation
- **Status:** Incoherent: S3.31 accepts any interval but does not validate against HK generation capability.
- **Issue:** Operator can set SID 1 (EPS HK) to 0.1 s (10 Hz), but OBDH tick loop may be 1 s (only 1 per second generated). No warning or clamping.
- **Impact:** Operator sets 10 Hz HK expecting 10 pkt/s but gets 1 pkt/s, causing confusion.
- **Suggested fix:** Clamp SID interval to [tick_interval, max_interval]; reject sub-tick requests with S1.4 error.

### S20 Parameter Write — No Type Validation
- **Status:** Incoherent: S20.1 accepts any 4-byte float for any parameter ID without validation (e.g., can set mode to 99.5, temperature to -999).
- **Issue:** No range checking; operator can set invalid values via S20.1.
- **Impact:** Operator can corrupt state by setting nonsensical parameter values.
- **Suggested fix:** Define parameter metadata (name, type, min, max, units) in config; S20.1 validates against range before accepting.

---

## 7. Top-5 Prioritized Defects

### **Defect 1: OBDH Buffer Fill – HK TM Counter Overflow (353%)**

**Title:** HK TM buffer fill percentage (param 0x0312) reaches unphysical values (353%+)
**Severity:** Major
**Description:**
Parameter 0x0312 (`hktm_buf_fill`) is a monotonically-incrementing packet counter that is never reset or clamped. After ~2 minutes of real-time operation in bootloader mode, it reaches 353%. This counter should either:
1. Read from the authoritative source (`OnboardTMStorage.get_status()[0]['fill_pct']`), which is physically clamped to [0, 100], or
2. Be reset on every S15 dump completion and clamped to 100%.

The MCS displays this directly, misleading operators about true HK storage capacity utilization.

**Files:**
- `packages/smo-simulator/src/smo_simulator/models/obdh_basic.py` (lines 84, 267–274, 350).
- `packages/smo-simulator/src/smo_simulator/tm_storage.py` (authoritative fill_pct calculation).

**Root cause:** In `obdh_basic.py`, `hktm_buf_fill` is initialized as a raw packet count and incremented by `random.randint(0, 2)` every tick (line 269). Decrement on drain is insufficient and does not reset on store dump. The parameter should be calculated as a percentage of capacity, not an absolute count, and should read from the actual TM storage.

**Suggested fix:**
1. Replace line 350 with: `shared_params[0x0312] = self._tm_storage.get_status()[0]['fill_pct']` (requires passing TM storage reference to OBDH model).
2. Alternatively, compute percentage in place: `shared_params[0x0312] = 100.0 * min(s.hktm_buf_fill, s.hktm_buf_capacity) / max(1, s.hktm_buf_capacity)` and ensure `hktm_buf_fill` is clamped to `hktm_buf_capacity`.
3. Add unit test: write > capacity packets, verify parameter reads 100% and never exceeds.

---

### **Defect 2: Bootloader HK Filtering Blocks Beacon Telemetry**

**Title:** SID 11 (bootloader beacon) HK is gated by `sw_image != SW_BOOTLOADER` filter
**Severity:** Major
**Description:**
The engine's `_enqueue_tm()` function checks `if sw_image != SW_APPLICATION: return` before enqueueing any HK. However, SID 11 (bootloader beacon at 30 s cadence) is the *only* HK that should be active during LEOP Phase 3 (bootloader mode). The gate should be:
```python
if not ((sw_image == SW_BOOTLOADER and sid == 11) or (sw_image == SW_APPLICATION)):
    return
```
Currently, the bootloader is silent during early LEOP, leaving operators blind to OBC status during critical separation and initial power-on phases.

**Files:**
- `packages/smo-simulator/src/smo_simulator/engine.py` (method `_enqueue_tm()`).
- `packages/smo-simulator/src/smo_simulator/models/obdh_basic.py` (SID 11 definition).

**Suggested fix:**
Update the gate in `engine.py:_enqueue_tm()`:
```python
if sw_image == SW_BOOTLOADER:
    if sid != 11:  # Only SID 11 (beacon) in bootloader
        return
elif sw_image == SW_APPLICATION:
    pass  # All SIDs allowed
else:
    return
```

---

### **Defect 3: S12 Monitoring and S19 Event-Action Rules — Volatile (No Persistence)**

**Title:** Monitoring definitions (S12) and event-action rules (S19) are lost on OBC reboot
**Severity:** High
**Description:**
S12 monitoring definitions and S19 event-action rules are stored in memory dictionaries (`_s12_definitions`, `_s19_definitions`) in `service_dispatch.py`. These are volatile: when the OBC reboots (S8 func 52, watchdog timeout, or memory error), all rules are lost. Operator must re-load them via S12.6 and S19.1 commands, delaying recovery.

For a real spacecraft, these rules should be stored in PROM or EEPROM and restored by the bootloader. The simulator should either:
1. Persist rules to YAML files and reload on OBC startup, or
2. Add a configuration parameter to pre-load default rules on engine startup.

**Files:**
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (lines 34, 38).
- `packages/smo-simulator/src/smo_simulator/engine.py` (line 195, `_load_monitoring_configs()`).

**Impact:** Post-reboot FDIR automation is non-functional until rules are manually reloaded. This is a critical operability gap for contingency recovery.

**Suggested fix:**
1. Add methods `_save_s12_defs()` and `_save_s19_defs()` that write to JSON in `configs/eosat1/fdir/s12_definitions.json` and `s19_rules.json`.
2. On OBC reboot (detected via reboot_count change), call restore methods to reload definitions.
3. Alternatively, add sections to `obdh_fdir_config.yaml` to pre-load default S12 and S19 rules at engine startup.

---

### **Defect 4: S11 Schedule Editor Missing from MCS**

**Title:** No graphical UI for S11 schedule creation, visualization, or validation
**Severity:** Medium
**Description:**
The S11 scheduler is fully functional (insert, delete, list, enable/disable); however, the MCS frontend has no schedule editor. Operators must:
1. Manually compute TC execution times (as CUC seconds).
2. Format raw S11.4 packets by hand (exec_time(4) + TC payload).
3. Query S11.17 to verify the schedule was inserted correctly.

This is error-prone and slow. A dedicated schedule editor UI would allow drag-and-drop TC placement, time-domain visualization, and conflict detection (e.g., overlapping downloads and uploads).

**Files:**
- `packages/smo-mcs/src/smo_mcs/displays/` (missing `schedule_editor.py` or `schedule_view.py`).

**Impact:** Operators are unable to effectively plan and verify time-critical command sequences during LEOP or contingency response.

**Suggested fix:**
1. Create `packages/smo-mcs/src/smo_mcs/displays/schedule_editor.py` with:
   - Timeline widget showing scheduled TCs by execution time.
   - Button to add/edit/delete TC; modal dialog to set time and command.
   - Validation: warn if TC execution times overlap or exceed buffer depth.
   - Export/import schedule to/from JSON for external planning tools.
2. Integrate into MCS main navigation under "Planning" or "Operations".

---

### **Defect 5: S20 Parameter Write — No Range Validation**

**Title:** S20.1 (set parameter) accepts invalid values without validation
**Severity:** Medium
**Description:**
The S20.1 service accepts any 4-byte float for any parameter ID without checking type, range, or units. For example:
- Setting `obc_mode` (normally 0/1/2) to 99.5 is accepted.
- Setting `obc_temp` (normally ~25°C) to -999°C is accepted.
- Setting `watchdog_period` to 0 (invalid; disables watchdog) is accepted.

This allows operators to corrupt state via command error. Real spacecraft enforce parameter ranges in the flight software; the simulator should too.

**Files:**
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (lines 1172–1185, `_handle_s20()`).

**Impact:** Operator mistakes or corrupted uplink data can set invalid parameters, causing unexpected behavior or system failures.

**Suggested fix:**
1. Add parameter metadata to `configs/eosat1/subsystems/obdh.yaml`:
   ```yaml
   param_ids:
     obc_mode:
       id: 0x0300
       type: int
       min: 0
       max: 2
       units: "enum(nominal=0, safe=1, emergency=2)"
     obc_temp:
       id: 0x0301
       type: float
       min: -50
       max: +60
       units: "°C"
   ```
2. In `_handle_s20()`, validate the value against the range; reject with S1.4 error if out of bounds.
3. Add unit test: attempt to set obc_mode to 99; verify rejection and parameter unchanged.

---

## 8. PUS Service Coverage Table

| Service | Full Name | Simulator Implements? | MCS Client for It? | Fully Operable? | Notes |
|---------|-----------|----------------------|-------------------|-----------------|-------|
| **S1** | Request Verification | Yes | Yes | Yes | All subtypes (1.1, 1.3–1.5, 1.7) functional. |
| **S2** | Device Access | Yes | Partial | Yes | On/off and status; no register load/dump. |
| **S3** | Housekeeping | Yes | Yes | Yes | Dynamic SID creation; interval modification; one-shot reports. |
| **S5** | Event Reporting | Yes | Yes | Yes | Enable/disable event types; no severity filtering. |
| **S6** | Memory Management | Yes | Partial | Yes | Load/dump/check; no flash sector erase or signature verify. |
| **S8** | Function Management | Yes | Yes | Yes | All 79 function IDs implemented; comprehensive subsystem routing. |
| **S9** | Time Management | Yes | Partial | Yes | Time set/read; no continuous PTP or GPS sync. |
| **S11** | Scheduled Commanding | Yes | Partial | Yes | Insert/delete/list; no MCS schedule editor (UI gap). |
| **S12** | On-Board Monitoring | Partial | Partial | No | Basic definitions; no delta-change or hysteresis; no global enable/disable persistence. |
| **S13** | Large Data Transfer | Yes | No | No | Simulated with synthetic blocks; no payload image integration. |
| **S15** | Onboard Storage | Yes | Yes | Yes | Circular/stop-when-full; paced dump; store status; auto-clear. |
| **S17** | Connection Test | Yes | Partial | Yes | Echo packet; basic link health. |
| **S19** | Event-Action | Partial | Partial | No | Rules non-persistent; no complex logic or chaining. |
| **S20** | Parameter Management | Yes | Yes | No | Load/read functional; no range validation on write. |
| **S23** | File Management | No | No | No | Not implemented; low priority for current mission phase. |

---

## 9. Parameter/Command Coverage Table

| OBDH Parameter | Param ID | HK? | S20 Read? | S20 Write? | S8 Commandable? | In MCS Display? | Notes |
|---|---|---|---|---|---|---|---|
| **OBC Mode** | 0x0300 | SID 6 | ✓ | ✓ | S8.50 (set_mode) | ✓ | Nominal/Safe/Emergency; no validation range. |
| **OBC Temperature** | 0x0301 | SID 6 | ✓ | ✓ | — | ✓ | Read from TCS via shared_params; no command. |
| **CPU Load (%)** | 0x0302 | SID 6 | ✓ | ✓ | — | ✓ | Simulated with Gaussian noise; no command. |
| **MMM Used (%)** | 0x0303 | SID 6 | ✓ | ✓ | — | ✓ | Mass memory utilization; no command. |
| **TC RX Count** | 0x0304 | SID 6 | ✓ | ✓ | — | ✓ | Read-only counter; resets on reboot. |
| **TC Accept Count** | 0x0305 | SID 6 | ✓ | ✓ | — | ✓ | Read-only counter; resets on reboot. |
| **TC Reject Count** | 0x0306 | SID 6 | ✓ | ✓ | — | ✓ | Read-only counter; resets on reboot. |
| **TM Packet Count** | 0x0307 | SID 6 | ✓ | ✓ | — | ✓ | HK packets enqueued; resets on reboot. |
| **Uptime (s)** | 0x0308 | SID 6 | ✓ | ✓ | — | ✓ | Seconds since boot; resets on reboot. |
| **OBC Time (CUC)** | 0x0309 | SID 6 | ✓ | ✓ | S9.1 (set_time) | ✓ | Synchronized via S9.1; no continuous sync model. |
| **Reboot Count** | 0x030A | SID 6 | ✓ | ✓ | — | ✓ | Total reboots; monotonic; cleared by S8.57. |
| **Software Version** | 0x030B | SID 6 | ✓ | ✓ | — | ✓ | Fixed 0x0100; no update via S6.2. |
| **Active OBC** | 0x030C | SID 6 | ✓ | ✓ | S8.53 (switch_unit) | ✓ | 0=OBC-A, 1=OBC-B; cold redundant. |
| **OBC-B Status** | 0x030D | SID 6 | ✓ | ✓ | — | ✓ | OFF=0, STANDBY=1, ACTIVE=2. |
| **Active Bus** | 0x030E | SID 6 | ✓ | ✓ | S8.54 (select_bus) | ✓ | 0=Bus A, 1=Bus B; routed to subsystems. |
| **Bus A Status** | 0x030F | SID 6 | ✓ | ✓ | — | ✓ | OK=0, DEGRADED=1, FAILED=2. |
| **Bus B Status** | 0x0310 | SID 6 | ✓ | ✓ | — | ✓ | OK=0, DEGRADED=1, FAILED=2. |
| **SW Image** | 0x0311 | SID 6 | ✓ | ✓ | S8.55 (boot_app) | ✓ | BOOTLOADER=0, APPLICATION=1. |
| **HK TM Buffer Fill (%)** | 0x0312 | SID 6 | ✓ | ✓ | — | ✓ | **DEFECT: Counter overflow, reads 353%+** |
| **Event Buffer Fill** | 0x0313 | SID 6 | ✓ | ✓ | — | ✓ | Stop-when-full event store; clamped 0–100%. |
| **Alarm Buffer Fill** | 0x0314 | SID 6 | ✓ | ✓ | — | ✓ | Alarm store; clamped 0–100%. |
| **Last Reboot Cause** | 0x0316 | SID 6 | ✓ | ✓ | — | ✓ | NONE=0, CMD=1, WATCHDOG=2, MEM=3, SWITCHOVER=4. |
| **Boot Count OBC-A** | 0x0317 | SID 6 | ✓ | ✓ | S8.57 (clear) | ✓ | Monotonic per-unit counter; cleared by S8.57. |
| **Boot Count OBC-B** | 0x0318 | SID 6 | ✓ | ✓ | S8.57 (clear) | ✓ | Monotonic per-unit counter; cleared by S8.57. |
| **SEU Count** | 0x0319 | SID 6 | ✓ | ✗ | **Missing:** clear SEU | ✓ | Increments on simulated SEU; no reset cmd. |
| **Scrub Progress (%)** | 0x031A | SID 6 | ✓ | ✓ | S8.51 (memory_scrub) | ✓ | 0–100%; resets on scrub completion. |
| **Task Count** | 0x031B | SID 6 | ✓ | ✓ | — | ✓ | Active OS tasks; ~12 nominal, ~15 in nominal mode. |
| **Stack Usage (%)** | 0x031C | SID 6 | ✓ | ✓ | — | ✓ | Read-only; simulated based on CPU load. |
| **Heap Usage (%)** | 0x031D | SID 6 | ✓ | ✓ | — | ✓ | Read-only; simulated based on MMM usage. |
| **Memory Errors** | 0x031E | SID 6 | ✓ | ✗ | **Missing:** clear mem errors | ✓ | EDAC error count; cleared only on scrub completion. |

### Summary Statistics
- **Total OBDH parameters:** 31 (0x0300–0x031E).
- **Visible in HK (SID 6):** 31/31 (100%).
- **Readable via S20.3:** 31/31 (100%).
- **Writable via S20.1:** 31/31 (100%, but **no range validation**).
- **Commandable via S8:** 8/31 (26%: mode, time, scrub, reboot, switchover, bus select, boot app, inhibit, watchdog).
- **Displayed in MCS:** 31/31 (100%, but **0x0312 reads incorrectly**).

### Command Coverage (S8 Function IDs 50–62)
| Func ID | Command | Implemented? | MCS Button? | Feedback? | Notes |
|---------|---------|---|---|---|---|
| 50 | set_mode | ✓ | ✓ | S1.3 | Sets OBC mode (0/1/2). |
| 51 | memory_scrub | ✓ | ✓ | S1.3 | Initiates memory scrub; progress in HK. |
| 52 | obc_reboot | ✓ | ✓ | S1.3 | Immediate reboot to bootloader. |
| 53 | obc_switch_unit | ✓ | ✓ | S1.3 | Cold switchover to OBC-B. |
| 54 | obc_select_bus | ✓ | ✓ | S1.3 | Switch CAN bus (A/B). |
| 55 | obc_boot_app | ✓ | ✓ | S1.3 | Initiate application boot from bootloader. |
| 56 | obc_boot_inhibit | ✓ | ✓ | S1.3 | Inhibit automatic boot after reboot. |
| 57 | obc_clear_reboot_cnt | ✓ | ✓ | S1.3 | Clear boot counts and reboot counter. |
| 58 | set_watchdog_period | ✓ | ✓ | S1.3 | Set watchdog timeout period (ticks). |
| 59 | watchdog_enable | ✓ | ✓ | S1.3 | Arm watchdog timer. |
| 60 | watchdog_disable | ✓ | ✓ | S1.3 | Disarm watchdog timer. |
| 61 | diagnostic | ✓ | Partial | S8.130 (TM) | Returns health snapshot. |
| 62 | error_log | ✓ | Partial | S8.130 (TM) | Returns error summary. |

---

## 10. Operability Walkthrough: LEOP → Nominal → Contingency

### **LEOP Phase 3 (Bootloader, ~30 min)**
1. **Separation trigger:** OBC boots in bootloader mode (Phase 3).
   - **Operator sees:** SID 11 beacon @ 30 s (parameter 0x0309 = CUC time, 0x0302 = low CPU 15%).
   - **Issue:** Beacon HK gated off (Defect 2). Operator is blind.

2. **First contact (AOS):** Ground commands S9.1 to synchronize time.
   - **Operator action:** S9.1 TC with UTC (current CUC).
   - **Operator verifies:** S9.2 time report shows time accepted.
   - **Issue:** No time drift model; time is absolute, not continuous sync.

3. **Application boot:** Ground commands S8.55 (obc_boot_app).
   - **Operator sees:** S1.3 start success; Mode stays 1 (safe).
   - **Application init:** Boot pending timer counts down 10 s.
   - **Operator sees:** SID 11 beacon stops; SID 6 OBDH HK starts (sw_image transitions 0 → 1).
   - **Operator verifies:** Mode transitions 1 → 0 (nominal); CPU load rises to ~35%.

4. **Enable HK:** Ground commands S3.5 (enable SIDs 1–6).
   - **Operator sees:** HK rate increases (1.64 pkt/s nominal now enqueued).
   - **Operator monitors:** HK buffer fill (param 0x0312) starts rising from 0%.
   - **Issue:** Parameter 0x0312 is a monotonic counter, not a percentage (Defect 1). Operator misinterprets.

5. **First scheduled TCs:** Ground commands S11.4 to insert scheduled actions (e.g., S8.51 memory_scrub, S3.27 one-shot HK).
   - **Operator verifies:** S11.17 returns command list with correct exec times.
   - **Operator awaits:** Schedule execution at specified times.
   - **Issue:** No MCS schedule editor (Defect 4). Operator manually calculates CUC times and formats packets.

### **Nominal Phase 6 (Continuous Operations)**
6. **Monitoring:** S12.6 defines limit checks (e.g., cpu_load < 80%, temp < 60°C).
   - **Operator verifies:** S12.12 returns definitions.
   - **On violation:** S5 events generated (param limit violation events); S19 rules may auto-respond.
   - **Issue:** S12 definitions lost after reboot (Defect 3); operator must reload.

7. **TM dumps:** Every pass (e.g., 12-min pass, contact window open).
   - **Operator commands:** S15.9 to dump HK store (store 1) at AOS.
   - **Operator monitors:** S15.13 store status (count, capacity, enabled).
   - **Engine behavior:** Paces dump per TTC data rate; packets released each tick.
   - **On completion:** Store 1 auto-cleared; operator sees count → 0.

8. **Parameter modification:** Operator commands S20.1 to adjust HK cadence parameter or watchdog period.
   - **Operator command:** S20.1 with param 0x030A (reboot_count) = 0 (no, should command S8.57).
   - **Issue:** No range validation (Defect 5). Operator sets invalid value without feedback.

### **Contingency (OBC Reboot)**
9. **Watchdog timeout:** CPU hangs; watchdog fires after 30 ticks (~30 s in real-time).
   - **Operator sees:** S5 event (0x0303 "WATCHDOG_TIMEOUT"); OBC reboots (S5 event 0x0301 "OBC_REBOOT").
   - **Reboot sequence:**
     - Counters reset (tc_rx, tc_acc, tm_pkt, uptime).
     - Boot count increments (0x0317 or 0x0318 depending on active OBC).
     - Mode → 1 (safe).
     - SW image → 0 (bootloader).
     - SID 11 beacon should resume; instead, HK gated off (Defect 2).
   - **Operator actions:**
     - S9.1 to re-sync time.
     - S8.55 to boot application.
     - S12.6/S19.1 to reload monitoring and event-action rules (Defect 3).
     - S3.5 to re-enable HK SIDs.
   - **Issue:** Recovery is slow due to manual rule reload and missing operability aids.

10. **Bus failure:** CAN Bus A fails; operator switches to Bus B.
    - **Operator command:** S8.54 (obc_select_bus) with bus=1.
    - **Operator verifies:** Bus status (0x0310, param 0x030E = 1) updated in HK.
    - **Subsystem reachability:** OBDH model checks `is_subsystem_reachable()` to route commands to correct bus.

---

## 11. Recommended Implementation Priorities

### **P0 (Blockers to Operability)**
1. **Defect 1:** Fix HK TM buffer fill counter overflow (0x0312).
2. **Defect 2:** Fix bootloader HK gating; enable SID 11 in Phase 3.

### **P1 (High Impact, Operability Essentials)**
3. **Defect 3:** Implement S12/S19 persistence to config or startup YAML.
4. **Defect 5:** Add parameter range validation to S20.1.
5. **Implement S12 delta-change and hysteresis support.**

### **P2 (Medium Impact, Operator Efficiency)**
6. **Defect 4:** Create MCS schedule editor for S11.
7. **Add parameter metadata (type, range, units) to OBDH config.**
8. **Implement bootloader timeout and safe-mode entry (Category 3).**

### **P3 (Nice-to-Have, Robustness)**
9. **Model time drift and implement continuous S9 sync.**
10. **Add S11 schedule overflow event generation.**
11. **Enhance S6.2 with CRC signature verification.**

---

## 12. Conclusion

The OBDH subsystem implementation in the SMO Simulator provides **good baseline coverage of core PUS-C services** (S1, S2, S3, S5, S6, S8, S9, S11, S15, S17, S20) and supports **comprehensive dual-OBC redundancy modeling** (bootloader, app switch, unit switchover, dual-bus routing, watchdog, memory scrub, SEU).

**However, key operability gaps exist:**
- **Defects 1–2** compromise operator situational awareness (incorrect HK fill %, blind bootloader phase).
- **Defect 3** breaks post-reboot FDIR recovery (non-persistent monitoring/automation).
- **Defect 4** limits command planning capability (no schedule visualization).
- **Defect 5** allows state corruption via unvalidated parameter writes.
- **Category 2/3 gaps** (delta monitoring, time sync, bootloader timeout, SEU reset) limit realistic mission rehearsal.

**Recommended next steps:**
1. Fix Defects 1–2 immediately (operability blocking).
2. Implement Defect 3 fix (S12/S19 persistence) for robust LEOP/contingency workflows.
3. Prioritize P1 items for Phase 4 testing; P2 items for Phase 5 (full mission fidelity).

---

## References & Standards

- **ECSS-E-ST-70-41C (15 April 2016):** Space engineering — Telemetry and telecommand packet utilization. [ECSS](https://ecss.nl/standard/ecss-e-st-70-41c-space-engineering-telemetry-and-telecommand-packet-utilization-15-april-2016/)
- **ECSS-E-ST-50-01C:** Space engineering — Computer systems – General requirements. [ECSS](https://ecss.nl/)
- **AcubeSAT ECSS PUS Implementation:** [GitLab](https://acubesat.gitlab.io/obc/ecss-services/docs/)
- **Precision Onboard Time Synchronization for LEO Satellites:** [Ion Navigation Journal](https://navi.ion.org/content/69/3/navi.531)
- **GNSS-based Synchronization and Monitoring of LEO-PNT Onboard Time:** [ScienceDirect 2025](https://www.sciencedirect.com/science/article/pii/S0273117725010403)

---

*End of Report*

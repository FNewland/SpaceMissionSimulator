# TT&C Operability Review
**Date:** 2026-04-06
**Reviewer:** TT&C Systems Expert
**Mission:** EOSAT-1 (EO satellite, S-band, 64/1 kbps nominal/low-rate)
**Scope:** TT&C simulator subsystem operability for LEOP, nominal, and contingency scenarios

---

## 1. Scope & Assumptions

This review assesses the TT&C (Telemetry, Tracking & Command) subsystem simulation for operability against ECSS PUS-C (ECSS-E-ST-70-41C) and CCSDS standards (TM: 132.0-B-3, TC: 232.0-B-4, Packets: 133.0-B-2). The review focuses on whether a human TT&C operator can observe every required status parameter and command every required function through the MCS frontend during Launch and Early Orbit Phase (LEOP), nominal operations, and contingency/anomaly recovery. The simulator models S-band transponders (primary/redundant), power amplifiers with thermal constraints, lock acquisition sequence (carrier→bit→frame), link budget with BER/Eb-N0, ranging, antenna deployment, and failure modes. Ground segment is modeled implicitly via parameter constraints and pass windows. The review assumes standard small-EO-mission ground station capabilities: S-band (2.0–2.1 GHz uplink, 2.2–2.3 GHz downlink), 64 kbps high-rate / 1 kbps low-rate telemetry, CCSDS TM/TC framing, and PUS services (S3 HK, S5 events, S8 functions, S20 parameters).

---

## 2. Category 1 — Described, Implemented, Works for Mission

### Link Budget & Signal Quality Telemetry
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:255–305`
- RSSI (0x0502), link margin (0x0503), Eb/N0 (0x0519), BER (0x050C) computed from Friis path loss, coding gain, and noise floor during frame sync
- Nominal range ~500–2500 km at elevation 5–90 degrees; link margin threshold 3 dB warning, 1 dB critical
- Telemetry in HK SID 6 (TTC) at 8 s interval
- **Test coverage:** `tests/test_simulator/test_ttc_enhanced.py:test_ber_computed_during_contact()`, `test_lock_sequence_timing()`

### Lock Acquisition Sequence
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:230–247`
- Carrier lock at AOS+2s, bit sync at AOS+5s, frame sync at AOS+10s (cumulative from AOS)
- Locks reset on LOS; frame sync gates downlink TM acceptance
- Telemetry: carrier_lock (0x0510), bit_sync (0x0511), frame_sync (0x0512) — all boolean flags in SID 6
- Events generated on lock transitions (IDs 0x0500–0x0505 for acquire/lose events)
- **Test coverage:** `tests/test_simulator/test_ttc_enhanced.py` lines 40–106 (lock timing and LOS reset)

### Transponder Mode Switching (Primary/Redundant)
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:589–602`
- Commands: `switch_redundant`, `switch_primary` (Service 8, func_id 63–64)
- State field: `mode` (0=primary, 1=redundant) in parameter 0x0500, telemetry in SID 6
- Failure injection: `primary_failed`, `redundant_failed` flags
- Link blocks if active mode failed
- **Test coverage:** Lines 589–602 (handle_command); TC catalog: `S2_TTC_TRANSPONDER_A/B` and `TTC_SWITCH_PRIMARY/REDUNDANT`

### PA Power Control (On/Off)
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:611–623`
- Commands: `pa_on`, `pa_off` (Service 8, func_id 66–67)
- State: `pa_on` boolean in parameter 0x0516; `tx_fwd_power` (0x050D) in watts
- Blocks transmission when off or in overheat shutdown
- **Test coverage:** Lines 133–154 (test_pa_on_off_commands)
- **MCS integration:** PA status visible in TTC summary panel and lock chain diagram

### PA Thermal Model with Auto-Shutdown
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:342–357`
- First-order thermal dynamics with 60 s time constant
- Auto-shutdown threshold: 70°C; hysteresis for re-enable: ≤55°C
- Temperature telemetry: `pa_temp` (0x050F) in SID 6
- Events: 0x0508 (overtemp warning >55°C), 0x0509 (shutdown at 70°C), 0x050A (recovered)
- **Test coverage:** Lines 158–216 (test_pa_overheat_auto_shutdown, test_pa_cooldown_clears_overheat, test_pa_on_rejected_during_overheat)
- **Observed issue:** PA temp not directly commandable; operator can only inject heat via FDIR scenario for testing

### Ranging Telemetry & Control
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:306, 676–684`
- Ranging active only when in contact AND frame sync established
- Parameter: `ranging_status` (0x0508) boolean
- Commands: `ranging_start`, `ranging_stop` (Service 8, func_id 76–77)
- Ground range: 0x0509 (km) updated from orbit state
- **Test coverage:** Lines 676–684 (handle_command for ranging); implicitly tested in lock sequence tests
- **Gap:** Ranging pulse-round-trip time and Doppler compensation not modeled

### Contact Window Geometry Telemetry
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:217–219`
- Elevation (0x050A), azimuth (0x050B), slant range (0x0509) populated from orbit propagator each tick
- Valid only during contact (elevation ≥ GS_MIN_ELEVATION ≈ 5°)
- Telemetry in SID 6
- **Test coverage:** Implicit in all contact-based tests

### Command Reception Counting
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:59, 693–696`
- Parameter `cmd_rx_count` (0x0513) incremented on each valid TC reception
- Cleared on link loss
- Blocks increment if uplink lost
- **Test coverage:** Lines 352–366 (test_cmd_rx_counter, test_uplink_loss_failure)

### Data Rate Selection (High/Low)
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:604–609`
- Command: `set_tm_rate` (Service 8, func_id 65)
- Parameter: `tm_data_rate` (0x0506) in bps
- Low-rate (1 kbps) forced during beacon mode and pre-deployment
- High-rate (64 kbps) available post-antenna deployment in nominal mode
- **Test coverage:** Implied in nominal telemetry initialization
- **MCS integration:** Visible in TTC panel as "Data Rate"

### Antenna Deployment Status & Command
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:70–72, 638–641`
- Boolean flag: `antenna_deployed` (0x0520)
- Command: `deploy_antennas` (Service 8, func_id 69)
- Effect: Pre-deployment forces low-rate (1 kbps) and applies −6 dB penalty to link margin
- Parameter 0x0520 in SID 6
- **Test coverage:** Implicit in link budget tests; no explicit deploy-to-normal-rate test
- **Critical for LEOP:** Deployment time-gated by uplink availability during first contact window

### Beacon Mode Status & Control
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:70–72, 199–202, 643–645`
- Boolean flag: `beacon_mode` (0x0521)
- Command: `set_beacon_mode` (Service 8, func_id TBD — not in catalog)
- Effect: Forces low-rate 1 kbps telemetry
- Telemetry in SID 6
- **Use case:** Safe-mode / bootloader recovery where OBC unable to command high-rate
- **Gap:** Not exposed in MCS widget; no command entry in tc_catalog.yaml

### Uplink Loss Detection & Timeout
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:81–82, 181–186, 555–563`
- Uplink timeout counter increments when link active but frame_sync = false
- Threshold: 300 s (5 min) default, tunable
- Event 0x0511: "Uplink timeout" at HIGH severity
- Failure injection: `uplink_lost` flag blocks TC reception
- **Test coverage:** Lines 275–294 (test_uplink_loss_failure)
- **Gap:** No operator widget or threshold control via MCS

### Frequency & Modulation Status (S-band Configuration)
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:132–135, 646–659, 661–667`
- Uplink frequency (0x0504): 2025.5 MHz nominal (configurable in ttc.yaml)
- Downlink frequency (0x0505): 2200.5 MHz nominal (configurable in ttc.yaml)
- Commands: `set_ul_freq`, `set_dl_freq` (range 2000–2100 MHz UL, 8400–8500 MHz DL — **note: DL range suspect**)
- Modulation: BPSK/QPSK supported via `set_modulation` command (func_id TBD)
- **Parameterization:** No FIR parameter in HK; frequencies hard-coded per mission or must be queried via S20
- **Gap:** DL frequency validation range (8400–8500 MHz) is X-band; S-band should be 2200–2290 MHz. **Defect candidate.**

### AGC, Doppler, Range Rate (Phase 4 Flight Hardware Realism)
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:308–327, 402–404`
- AGC level (0x051A): received signal after gain in dB
- Doppler shift (0x051B): frequency offset in Hz from range rate
- Range rate (0x051C): m/s (negative = approaching, positive = receding)
- All updated when in contact; reset to defaults (AGC -120, Doppler 0, RR 0) at LOS
- Telemetry in SID 6
- **Test coverage:** Implicitly set during contact; no explicit tests
- **Operator use:** Doppler essential for uplink pre-conditioning and frequency tracking

### BER Computation & Failure Injection
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:287–299, 710–711`
- BER derived from Eb/N0 using Q-function approximation (BPSK/QPSK)
- Failure: `high_ber` via offset to Eb/N0 (default 10 dB degradation, tunable)
- Failure: `receiver_degrade` via noise figure increase
- BER clamped at −12 (error-free) to −1 (very high)
- **Test coverage:** Lines 248–270 (test_high_ber_failure), 299–319 (test_receiver_degrade_failure)
- **Operator visibility:** BER (0x050C) in telemetry; no operator-settable threshold or detection

### Dedicated Command Channel (15-min Decode Timer)
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:69–71, 187–197, 632–636`
- Timer-based fallback for safe-mode commanding when OBC unresponsive
- Command: `cmd_channel_start` (Service 8, func_id TBD)
- Duration: 900 s (15 min) countdown; PA forced on during window
- Parameter: `cmd_decode_timer` (0x0522) in seconds
- **Rationale:** Allows uplink TC reception even if OBC locked in FDIR recovery
- **Gap:** Not documented in LEOP or contingency procedures; not visible in MCS widget

### Device Access (S2 Service) — Transponders, PA, LNA, Antenna Drive
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:93–100, 754–763`
- Device states dictionary: Xpdr A (0x0400), Xpdr B (0x0401), PA (0x0402), LNA (0x0403), Antenna drive (0x0404)
- Commands: Service 2, Subtype 1 (ON/OFF per device_id)
- Handler: `set_device_state()`, `get_device_state()`
- **Catalog entry:** `S2_TTC_TRANSPONDER_A`, `S2_TTC_TRANSPONDER_B`, `S2_TTC_POWER_AMPLIFIER`, `S2_TTC_LNA`, `S2_TTC_ANTENNA_DRIVE`
- **Test coverage:** No explicit S2 device tests in test suite
- **Gap:** Device state not exposed in telemetry; operator cannot verify device off/on except by side-effect on link/telemetry

### Event Reporting & Severity Levels
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:413–581`
- 13 TTC-specific events: carrier lock (0x0500), lock loss (0x0501–0x0505), link margin warning/critical (0x0506–0x0507), PA overtemp warning/shutdown (0x0508–0x050A), transponder mode switch (0x050B), BER threshold (0x050C), antenna deployed (0x050D), ranging state (0x050E–0x050F), AGC saturation (0x0510), uplink timeout (0x0511)
- Severity levels: INFO (acquisitions), MEDIUM (lock loss, warnings), HIGH (critical margins, shutdown, timeout)
- MCS receives events via S5 service TM; journal capability in place
- **Test coverage:** Events generated but not explicitly tested for correct IDs/text
- **Operator use:** FDIR alarm panel should trigger on HIGH/MEDIUM events

### PUS Service & TC Command Support
- **File:** `packages/smo-common/src/smo_common/protocol/ecss_packet.py`
- Implemented services: S1 (Verification), S2 (Device Access), S3 (Housekeeping), S5 (Event Reporting), S6 (Memory), S8 (Function Management), S9 (Time), S11 (Scheduling), S12 (On-Board Monitoring), S15 (On-Board Storage), S17 (Test), S19 (Event-Action), S20 (Parameter Management)
- **For TT&C:** S2 (device on/off), S8 (functions 63–77 for TTC commands), S20 (read/set parameters)
- CRC-16/CCITT-FALSE per CCSDS
- **Test coverage:** Protocol tests in `packages/smo-common` (not detailed here)

### MCS Frontend Display & UX
- **File:** `packages/smo-mcs/src/smo_mcs/static/index.html` (lines 1–5779)
- TTC tab panel shows:
  - Lock chain (carrier/bit/frame LEDs with live status)
  - RSSI, link margin, BER, Eb/N0 gauges
  - Elevation/azimuth/range from orbit
  - System Overview dashboard includes TTC subsystem health badge
- Commands available: (Implicit via procedure runner, not exposed as raw widgets)
- **Gap:** No standalone command buttons for PA on/off, antenna deploy, ranging control, data rate selection, frequency tuning

---

## 3. Category 2 — Described as Needed but Not Yet Implemented

### Command Authentication & Authorization (S20 Parameter Management)
- **Defined:** Parameter 0x051D (`cmd_auth_status`) modeled as state variable (0=disabled, 1=enabled, 2=locked out)
- **Missing:** No on-board implementation; always accepts commands
- **Needed for:** Safe-mode recovery where operator must unlock command path via FDIR rule or manual override
- **Fix location:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py` — add gate in `record_cmd_received()` to check auth status
- **Reference:** ECSS-E-ST-70-41C S20 (Parameter Management) does not directly cover auth, but spacecraft bus isolation and command rejection are part of contingency operations

### Command TX Power Level Tuning (Fine-Grained PA Control)
- **Defined:** Parameter 0x050D (`tx_fwd_power`) readable; command `set_tx_power` accepts 0–5 W
- **Missing:** No intermediate power levels (e.g., 0.5 W, 1.5 W, 3.0 W) for link margin optimization during extended passes
- **Needed for:** Energy budgeting during low-power contingencies; PA thermal margin preservation
- **Current behavior:** Power is `_pa_nominal_power_w` (default 2.0 W) when PA on, or 0.0 when off — binary effective
- **Fix location:** Modify `set_tx_power` command handler to actually adjust `_pa_nominal_power_w` dynamically and update telemetry each tick
- **Test needed:** Verify tx_fwd_power in telemetry matches command setpoint

### Uplink Frequency Doppler Pre-Compensation
- **Defined:** Doppler shift (0x051B) telemetry available to operator
- **Missing:** No automatic or operator-controlled frequency correction command
- **Needed for:** High-elevation passes (high Doppler rate); extended contact windows with >±40 kHz shift
- **Current workaround:** Operator uses measured Doppler and `set_ul_freq` command to correct
- **Fix location:** Add command `set_uplink_doppler_correction(freq_hz)` (Service 8, func_id TBD) or extend `set_ul_freq` with automatic tracking mode
- **Reference:** Typical LEO Doppler ±40 kHz at S-band; compensation critical for >30 s passes

### Receive Gain / LNA Control
- **Defined:** AGC level (0x051A) telemetry
- **Missing:** No operator command to adjust LNA gain setpoint or AGC target range
- **Needed for:** Weak-signal acquisition at LOS; high-signal saturation avoidance at AOS
- **Current behavior:** AGC computed passively from link budget; not settable
- **Fix location:** Add command `set_rx_gain(agc_target_db)` or `set_lna_gain(gain_db)`
- **Existing command:** `set_rx_gain` exists in handle_command (lines 669–674) but appears to just set the state variable, not actually affect link budget

### Modulation & Coding Rate Selection (FEC Tuning)
- **Defined:** Modulation mode (BPSK/QPSK) command exists (Service 8, func_id TBD)
- **Missing:** No selection of FEC scheme (convolutional, Reed-Solomon, LDPC) or coding rate (e.g., R=1/2, R=7/8)
- **Needed for:** Link margin recovery during degraded passes; energy-margin trade-offs
- **Current model:** Coding gain fixed at 3 dB (convolutional + RS); BER derived therefrom
- **Fix location:** Extend model with FEC parameter set; command handler to select; BER recomputed based on scheme
- **Reference:** CCSDS 131.0-B-4 (TM Channel Coding) defines multiple convolutional/LDPC options

### Coherent vs Non-Coherent Mode (Carrier Phase Locking)
- **Defined:** `set_coherent_mode` command handler exists (lines 686–689)
- **Missing:** No effect on link budget or BER; command just sets state variable
- **Needed for:** Weak-signal acquisition (non-coherent adds ~2 dB Eb/N0 requirement); power-limited cases
- **Fix location:** Add SNR/Eb/N0 penalty (≈2 dB) when coherent_mode=false; update link budget calculation
- **Test:** Verify BER worse by ~2 dB at same Eb/N0 when non-coherent

### Telemetry Downlink Frequency (Currently 8400–8500 MHz Validation — X-Band Error)
- **Defined:** Parameter 0x0505 (dl_freq); command `set_dl_freq` accepts "8400.0 <= freq <= 8500.0"
- **Missing:** Validation is for X-band (8 GHz), not S-band (2.2 GHz)
- **Needed for:** Frequency planning in multi-band missions; mission constraint enforcement
- **Fix location:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:654–659` — change range to "2200.0 <= freq <= 2290.0 MHz"
- **Impact:** Non-critical for this 2-GHz nominal mission but bug for extensibility

### On-Demand Telemetry Request for TTC Parameters
- **Described:** Parameters 0x0523 (active_gs), 0x0524 (gs_equipment_status) marked `on_demand: true` in parameters.yaml
- **Missing:** No handler to request/push these parameters via S20 or S3
- **Needed for:** Operator situational awareness of ground station state (which site active, equipment health)
- **Fix location:** Implement OBDH/FDIR logic to populate 0x0523/0x0524 from ground segment state; expose via S20 request
- **Test:** Verify S20 request for 0x0523 returns active ground station index

### Antenna Pointing Verification (Elevation Angle Limit Check)
- **Described:** Elevation (0x050A) telemetry available
- **Missing:** No hard limit enforcement or alert when elevation < 5° during attempted commanding
- **Needed for:** Prevent blind commanding to spacecraft below radio horizon
- **Current behavior:** Link inactive automatically at low elevation, but no explicit procedure step
- **Fix location:** Add validation rule in MCS command handler: reject TC submission if elevation < 5° and user not explicitly bypassed
- **Reference:** LEOP procedures step 3 (first_acquisition.md) implicitly assumes this

---

## 4. Category 3 — Not Yet Described or Implemented but Needed

### Ground Station Uplink Power / Effective Isotropic Radiated Power (EIRP) Command
- **Rationale:** Uplink link budget dependent on GS EIRP (default 10 dBW modeled)
- **Gap:** No parameter to adjust GS EIRP; operator cannot command GS to increase power for weak-spacecraft conditions
- **Needed for:** Contingency recovery when spacecraft receiver degraded or antenna mispointed
- **Suggestion:** Model as parameter (0x0525) with command to adjust; affects calculated Eb/N0 for uplink
- **Reference:** ECSS does not cover GS power (that is ground-segment domain), but CCSDS link budgets require it

### Uplink Loss Timeout Threshold (Operator-Configurable)
- **Rationale:** Current 300 s timeout is hard-coded
- **Gap:** No operator command to adjust threshold per pass or mission phase
- **Needed for:** Safe-mode where shorter TC windows are acceptable; extended passes where longer timeout needed
- **Suggestion:** Add S20 parameter 0x0526 (uplink_timeout_threshold_s) with read/write access; FDIR rule to escalate event if operator tunes it inappropriately
- **Reference:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:82` (uplink_timeout_threshold field exists but not exposed)

### Antenna Deployment Burn-Wire Firing Status & Verification
- **Rationale:** Antenna deployment is a critical LEOP milestone (procedure LEOP-002)
- **Gap:** Command exists (`deploy_antennas`, func_id 69) but no telemetry of burn-wire continuity, deployment sensor, or mechanical lock confirmation
- **Needed for:** Operator verification that deployment actually succeeded; alternative procedures if deploy failed
- **Suggestion:** Add parameters:
  - 0x0527: antenna_deploy_sensor (0=stowed, 1=deployed, 2=partial/jammed)
  - 0x0528: burnwire_status (0=armed, 1=fired, 2=timeout, 3=fault)
  - Procedure: deploy command activates timer; sensor updated when mechanical link broken
- **Reference:** LEOP procedures sequential_power_on.md lists antenna_deployed=1 as prerequisite for next stage

### Transponder Switchover Telemetry (Which Mode Is Active?)
- **Rationale:** Parameter 0x0500 (ttc.mode) shows requested mode, but not hardware confirmation
- **Gap:** No parameter confirming actual active transponder (e.g., if primary commanded but redundant actually active due to failure)
- **Needed for:** Operator to detect mode-switch failures or race conditions
- **Suggestion:** Add parameter 0x0529 (ttc.active_mode_confirmed) reflecting actual transceiver in use; S8 command validates against 0x0500
- **Reference:** Flight systems typically split "commanded" vs "actual" state for safety-critical switches

### Ground Station Handover Procedure & Telemetry
- **Rationale:** Small-EO missions use multiple ground stations (e.g., Svalbard, Troll, Inuvik); operator must coordinate handovers
- **Gap:** No MCS support for multi-site tracking or automatic handover indication
- **Needed for:** Extended pass planning; station switching during contingencies
- **Suggestion:** Add parameter 0x0523 (active_gs) with telemetry showing which site is in contact; MCS procedure template for "switch to next site"
- **Reference:** Procedures nominal/shift_handover.md references this concept but not implemented

### Carrier Recovery Sequence Monitoring (Signal Acquisition Detailed Steps)
- **Rationale:** Lock acquisition is modeled as time-series (2s carrier, 5s bit, 10s frame), but operator sees only final state
- **Gap:** No intermediate telemetry or detailed lock metrics (e.g., carrier phase error, bit-timing offset)
- **Needed for:** Troubleshooting slow locks or partial acquisitions in marginal link conditions
- **Suggestion:** Add parameters:
  - 0x052A: carrier_phase_error_rad (radians, updated each tick if in_contact)
  - 0x052B: bit_timing_offset_us (microseconds, after bit_sync acquired)
  - 0x052C: frame_boundary_confidence (0–100%, quality metric)
- **Reference:** CCSDS 131.0-B-5 (TM Synchronization) specifies lock metrics

### Transponder Temperature Setpoint & Thermal Control
- **Rationale:** PA thermal model includes time constant and shutdown threshold, but xpdr temperature is ambient-tracked
- **Gap:** No active thermal control (heaters) or setpoint; operator cannot maintain warm transponder in eclipse
- **Needed for:** Cold-soak recovery after eclipse; pre-heating before high-activity passes
- **Suggestion:** Add command `set_xpdr_heater(watts)` or S20 parameter 0x052D (xpdr_heater_power_w); add to EPS power budget accounting
- **Reference:** TCS subsystem (tcs_basic.py) models heaters but TT&C does not integrate

### Modulation Index / Deviation Monitoring (Frequency Stability)
- **Rationale:** TX frequency stability affects uplink capture range and interference avoidance
- **Gap:** No telemetry of frequency deviation or oscillator drift
- **Needed for:** Link margin budgeting; identifying frequency reference failures
- **Suggestion:** Add parameter 0x052E (tx_freq_deviation_hz) estimated from closed-loop Doppler tracking residuals
- **Reference:** ECSS-Q-60-60A (Design of Electronic Equipment for Space Applications) covers frequency stability

### Commanded vs Actual Data Rate Verification
- **Rationale:** TM data rate command (0x0506) set via `set_tm_rate`, but no confirmation of actual downlink rate
- **Gap:** Rate may be constrained by spacecraft mode or antenna deployment; operator sees only commanded value
- **Needed for:** Detecting mode conflicts (e.g., trying high-rate before antenna deployed)
- **Suggestion:** Split parameter:
  - 0x0506: tm_data_rate_commanded (writable via S20)
  - 0x052F: tm_data_rate_actual (read-only, reflects actual downlink)
- **Reference:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:204–210` already implements rate forcing; just needs telemetry split

### Ranging Round-Trip Delay & Distance Accuracy
- **Rationale:** Ranging is initiated and parameter 0x0509 (range_km) is available, but RTT and accuracy not modeled
- **Gap:** Operator cannot assess ranging session quality or detect range tracking errors
- **Needed for:** Precise orbit determination (POD); antenna boresight verification via range tracking
- **Suggestion:** Add parameters:
  - 0x0530: ranging_rtt_ms (round-trip time in milliseconds, 0 if not active)
  - 0x0531: range_std_dev_m (ranging measurement uncertainty in meters)
- **Reference:** CCSDS 414.0-G-1 (Ranging) specifies RTT and accuracy metrics

---

## 5. Category 4 — Described/Implemented but NOT Helpful for This Mission

### X-Band Downlink Support (8.4 GHz)
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:654–659`
- **Validation:** Command `set_dl_freq` accepts 8400–8500 MHz (X-band range)
- **Not applicable:** EOSAT-1 is S-band only (2.2–2.3 GHz downlink)
- **Impact:** No operational consequence; dead code path but confusing for mission planning
- **Action:** Document as out-of-scope or remove validation for single-band mission

### Triple-Modular Redundancy (TMR) Failover Logic
- **Implemented:** Dual transponder (primary/redundant) with independent failure flags
- **Not implemented:** Triple-redundant voting or automatic failover; only manual switchover
- **Not applicable:** EOSAT-1 is dual-redundant by design (cost/power constraints of EO satellite)
- **Impact:** No issue; implementation matches architecture

---

## 6. Category 5 — Inconsistent or Incoherent Implementation

### Device On/Off States Not Exposed in Telemetry
- **Issue:** Parameter device_states dict (0x0400–0x0404) maintained in state but never written to telemetry
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:93–100, 754–763, tick()` (lines 371–411)
- **Consequence:** Operator can issue S2 device on/off commands but cannot verify device state; only indirect observation via link activity
- **Needed:** Add parameters 0x0530–0x0534 (device on/off status flags); write in tick() method
- **Test:** No S2 device control tests in test suite

### PA On/Off vs PA Overheat Shutdown Ambiguity
- **Issue:** Two independent boolean fields: `pa_on` (user-controllable) and `pa_overheat_shutdown` (auto-set)
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:34–36, 175–177, 351–357`
- **Inconsistency:** `pa_on` command accepted even when `pa_overheat_shutdown=true`, leading to silent rejection at line 352
  ```python
  if not s.pa_on or s.pa_overheat_shutdown:
      in_contact = False
      s.tx_fwd_power = 0.0
  ```
  User-commanded `pa_on=true` is overridden but not reported in command response
- **Consequence:** Operator issues `pa_on` command, receives success response, but PA remains off due to thermal state
- **Fix location:** Check overheat state before accepting `pa_on` command; return failure if thermal lockout active
- **Test:** Line 184–195 partially covers this but command response not asserted

### Antenna Deployed Penalty Inconsistency
- **Issue:** Link margin calculation applies −6 dB penalty if antenna not deployed (line 284–285)
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:284–285`
- **Inconsistency:** Penalty applied after BER already computed; means BER is "correct" but link_margin shows worse than reality
- **Consequence:** Operator sees marginal link margin and may incorrectly conclude signal quality is poor when antenna is only partially deployed
- **Fix location:** Clarify whether penalty should apply pre or post BER calc; document assumption in code

### Frame Sync Gates Ranging but Not Explicitly Noted
- **Issue:** Ranging automatically deactivated at frame_sync=false (line 306)
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:306, 676–680`
- **Inconsistency:** Command `ranging_start` checks frame_sync and returns success/fail (good), but during normal operation ranging_status can flip without operator action if frame sync drops
- **Consequence:** Operator issues `ranging_start`, gets success, then sees ranging_status flip to 0 unexpectedly if brief frame loss
- **Fix:** Document dependency in command response or add event (0x0512?) when ranging auto-disables

### Command RX Counter Not Reset on Uplink Loss
- **Issue:** Parameter cmd_rx_count (0x0513) increments on valid TC, but doesn't clear on uplink_lost injection
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:693–696`
- **Inconsistency:** If uplink fails and is recovered, count reflects gap but doesn't indicate loss event
- **Consequence:** FDIR rule might not detect uplink fault because counter is monotonic
- **Fix:** Reset counter to 0 on uplink_lost=true, or add separate uplink_loss_count parameter

### Data Rate Forcing Logic Not in Configuration
- **Issue:** Low-rate is hardcoded to 1 kbps and high-rate to 64 kbps; pre-deployment and beacon mode force low-rate
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:204–210`
- **Inconsistency:** Rates are in ttc.yaml config (tm_rate_hi_bps, tm_rate_lo_bps) but forcing logic is hardcoded in tick()
- **Consequence:** Mission planning assumes high/low-rate values but logic to select them is opaque to operator
- **Fix:** Add S20 parameters for rate select:
  - 0x0535: data_rate_mode (0=low, 1=high, 2=auto)
  - 0x0536: antenna_deployment_forces_low_rate (boolean config)
  - Write these in tick() and allow read via S20

### Uplink/Downlink Frequencies Hard-Coded in Multiple Locations
- **Issue:** Frequencies defined in three places: ttc.yaml (config), ttc_basic.py init (constructor), parameters.yaml (metadata)
- **File:** `configs/eosat1/subsystems/ttc.yaml:2–3`, `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:116`, `configs/eosat1/telemetry/parameters.yaml` (no frequency params)
- **Inconsistency:** Parameters 0x0504/0x0505 exist in telemetry but are not in parameters.yaml definition; operator cannot know they exist
- **Consequence:** Operator unaware that frequency adjustment is possible; procedure documentation cannot reference parameter IDs
- **Fix:**
  1. Add 0x0504 and 0x0505 to parameters.yaml with units and description
  2. Move frequency defaults to ttc.yaml; read in configure() not __init__()

### BER Log Scale Inconsistent with Documentation
- **Issue:** Parameter 0x050C (ber) documented as "log10 scale" but values are confusing
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:40, 287–299`
- **Inconsistency:** BER clamped to max(−12.0, log10(ber_val)); if ber_val=1e-10, ber=−10. But "no link" sets ber=−10. Operator cannot distinguish "good link" from "no link".
- **Consequence:** Operator reads ber=−10 and doesn't know if link is marginal or absent
- **Fix:** Use −1.0 for "no link" (BER=0.1, nonsense value) and −12.0 for "excellent" (BER<1e-12)

### Beacon Mode Parameter Not in Catalog
- **Issue:** Beacon_mode (0x0521) is in HK SID 6 and has a telemetry definition, but no S20 parameter entry
- **File:** `configs/eosat1/telemetry/parameters.yaml:261`, no matching S20 parameter entry
- **Inconsistency:** Operator can read 0x0521 via HK but cannot command it via S20; only command is Service 8 func_id (not in catalog)
- **Consequence:** FDIR cannot easily trigger beacon mode; procedure must use S8 function which is not documented in tc_catalog.yaml
- **Fix:** Add parameter 0x0537 (beacon_mode_enabled, boolean, writable via S20)

### Command Decode Timer Not Integrated with Procedure Framework
- **Issue:** Cmd_channel_active (0x0522 timer) is modeled but not referenced in any LEOP or contingency procedure
- **File:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:187–197`
- **Inconsistency:** Feature exists (15-min command window for safe-mode recovery) but not documented or used
- **Consequence:** Operator unaware of feature; contingency recovery procedures do not exploit it
- **Fix:** Document in emergency/loss_of_communication.md procedure; add MCS widget to arm/disarm command channel

---

## 7. Top-5 Prioritised Defects for Issue Tracker

### DEFECT #1: Downlink Frequency Validation Range is X-Band, Not S-Band
**Severity:** Major
**Title:** TTC model rejects valid S-band downlink frequencies (2.2–2.3 GHz) due to X-band range check (8.4–8.5 GHz)

**Description:**
The `set_dl_freq` command handler in ttc_basic.py validates downlink frequency against the range 8400–8500 MHz, which is X-band (8–9 GHz). EOSAT-1 is a pure S-band mission (2.2–2.3 GHz downlink). If an operator or FDIR procedure attempts to set DL frequency, the command will be rejected because S-band values (e.g., 2200.5 MHz) are outside the range. This prevents legitimate frequency tuning during contingencies (e.g., shifting away from RFI).

**Affected Files:**
- `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:654–659`

**Suggested Fix:**
Change validation range from `8400.0 <= freq <= 8500.0` to `2200.0 <= freq <= 2290.0` (per ECSS S-band allocation). Make the range configurable via ttc.yaml to support multi-band future missions.

**Test Case:**
```python
# Should succeed
result = model.handle_command({"command": "set_dl_freq", "freq_mhz": 2210.5})
assert result["success"] is True

# Should fail
result = model.handle_command({"command": "set_dl_freq", "freq_mhz": 8400.0})
assert result["success"] is False  # Not in S-band range
```

---

### DEFECT #2: Device On/Off State (S2 Service) Not Exposed in Telemetry — Operator Cannot Verify Device Commands
**Severity:** Major
**Title:** Service 2 (Device Access) device on/off commands accepted but no telemetry feedback; operator blind to device state

**Description:**
The TTC model maintains internal device state for transponders, PA, LNA, and antenna drive (device_states dict, lines 93–100) and accepts Service 2 commands to toggle them. However, these states are never written to telemetry parameters. An operator issues an S2 command to turn on Transponder B (0x0401) and receives an acceptance acknowledgement, but cannot verify whether the device actually powered on. The only way to infer device state is indirectly via link parameters (e.g., link active/inactive), which is unreliable in contingencies.

**Affected Files:**
- `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:93–100, 754–763` (device_states dict and handlers)
- `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:371–411` (tick method — parameters written but not device states)
- `configs/eosat1/telemetry/hk_structures.yaml:213–246` (SID 6 TTC HK — device params missing)
- `configs/eosat1/telemetry/parameters.yaml` (no 0x0530–0x0534 device status params)

**Suggested Fix:**
1. Add parameters 0x0530–0x0534 to parameters.yaml for each device:
   - 0x0530: xpdr_a_on (boolean)
   - 0x0531: xpdr_b_on (boolean)
   - 0x0532: pa_on (already exists as 0x0516, but duplicate for clarity)
   - 0x0533: lna_on (boolean)
   - 0x0534: antenna_drive_on (boolean)
2. Add these to SID 6 (TTC HK) as pack_format: B, scale: 1
3. Update tick() method to write device_states values to shared_params
4. Add test case: issue S2 command, verify parameter reflects state

**Test Case:**
```python
# Set device on
result = model.handle_command({"command": "s2_device_on", "device_id": 0x0401})
assert result["success"] is True

# Tick to update telemetry
model.tick(1.0, orbit, params)

# Verify device param reflects state
assert params[0x0531] == 1.0  # 0x0531 = xpdr_b_on
```

---

### DEFECT #3: PA On/Off Command Silently Fails When Overheat Shutdown Active — No Error Feedback to Operator
**Severity:** Major
**Title:** `pa_on` command returns success even when thermal shutdown blocks PA; operator given false confidence

**Description:**
When `pa_on` command is issued while `pa_overheat_shutdown=true`, the command handler (line 611–618) returns `{"success": True}` without checking the shutdown flag. The PA remains off because the tick() method (lines 175–177) silently overrides `pa_on=true` when overheat shutdown is active. Operator sends `pa_on`, believes it worked, and does not realize PA is still disabled. This is a safety-critical gap: operator may miss the fact that thermal recovery is needed before transmission can resume.

**Affected Files:**
- `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:611–618` (pa_on command handler)
- `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:175–177` (tick override logic)

**Suggested Fix:**
In `handle_command` for `pa_on`, check `pa_overheat_shutdown` before returning success:
```python
elif command == "pa_on":
    if self._state.pa_overheat_shutdown:
        return {
            "success": False,
            "message": f"PA shutdown due to overheat ({self._state.pa_temp:.1f}C). "
                       f"Wait for cooldown to {self._pa_shutdown_temp - 15}C.",
        }
    self._state.pa_on = True
    return {"success": True}
```

**Test Case:**
```python
# Force overheat
model._state.pa_temp = 75.0
model._state.pa_overheat_shutdown = True

# Try to turn PA on
result = model.handle_command({"command": "pa_on"})
assert result["success"] is False
assert "overheat" in result["message"].lower()
```

---

### DEFECT #4: Antenna Deployed Status Not Available Pre-Deployment; Operator Cannot Plan Deployment Command Timing
**Severity:** Major
**Title:** `antenna_deployed` parameter reflects state but no pre-deployment sensor telemetry (e.g., pyro continuity, deployment sensor); operator cannot optimize burn-wire firing window

**Description:**
The antenna deployment model (line 638–641) provides a binary state (0=stowed, 1=deployed) and a command to fire the burn-wire. However, there is no telemetry of deployment readiness: operator cannot read antenna pyrotechnic continuity, arming status, or deployment-sensor health before attempting deployment. In a real LEOP scenario, mission ops would verify burn-wire circuit integrity and sensor function before committing to deployment. The current model forces an all-or-nothing decision: operator cannot abort deployment if sensor pre-checks fail.

**Affected Files:**
- `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:638–641` (deploy_antennas command)
- `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:70–72` (antenna_deployed state variable)
- `configs/eosat1/telemetry/parameters.yaml` (0x0520 antenna_deployed only)

**Suggested Fix:**
Add deployment sensor parameters:
- 0x0535: antenna_deployment_ready (boolean, 1=pyro continuity OK, 0=fault)
- 0x0536: antenna_deployment_sensor (0=unknown, 1=stowed, 2=deployed, 3=partial/jammed)
- 0x0537: antenna_deploy_command_accepted (timestamp of last burn-wire firing attempt)

Update LEOP-002 (sequential_power_on.md) to include pre-deployment health check step.

**Test Case:**
```python
# Pre-deployment: sensor should show stowed
assert params[0x0536] == 1  # stowed

# Deploy
result = model.handle_command({"command": "deploy_antennas"})
assert result["success"] is True

# Post-deployment: sensor should show deployed
model.tick(1.0, orbit, params)
assert params[0x0536] == 2  # deployed
```

---

### DEFECT #5: Uplink Timeout Detection and Recovery Strategy Not Documented; Operator Unaware of Autonomous Fallback
**Severity:** Major
**Title:** Uplink timeout event (0x0511) generated but fallback strategy (dedicated command channel) not explained in procedures; operator may incorrectly attempt manual recovery

**Description:**
The TTC model implements an uplink timeout alarm (event 0x0511) when no valid TC is received for 300 s. Additionally, there is a dedicated command channel feature (`cmd_channel_active`, `cmd_decode_timer`) that can be armed (15 min window) to allow TC reception without OBC acknowledgement — this is critical for safe-mode recovery. However, no procedure or MCS widget explains how to trigger the dedicated channel or under what conditions operator should do so. The uplink timeout event is generated but not correlated with a recovery action. Operator seeing the timeout event may not know to arm the command channel and may instead attempt futile retransmissions, wasting pass time.

**Affected Files:**
- `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:555–563` (uplink timeout event)
- `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:632–636` (cmd_channel_start command)
- `configs/eosat1/procedures/emergency/loss_of_communication.md` (no mention of dedicated command channel)
- `packages/smo-mcs/src/smo_mcs/static/index.html` (no widget to arm command channel)

**Suggested Fix:**
1. Add procedure step to emergency/loss_of_communication.md:
   ```
   ### Step X — Activate Dedicated Command Channel (15-min Recovery Window)
   **If:** Uplink timeout (event 0x0511) has fired and OBC not responding.
   **Action:** Issue command TTC_CMD_CHANNEL_START (Service 8, func_id XX) to activate 15-min TC window.
   **Verify:** Parameter 0x0522 (cmd_decode_timer) > 0 in next HK packet.
   **Recovery:** Transmit critical commands (e.g., antenna deploy, safe-mode trigger) during window.
   ```
2. Add MCS widget: "Activate Command Channel" button in TTC emergency controls panel.
3. Add test case to verify cmd_channel_active and timeout interaction.

**Test Case:**
```python
# Simulate uplink loss for >300s
model._state._uplink_timeout_counter = 310.0

# Verify timeout event is generated
events_generated = [...]  # Capture events from tick()
assert any(e['event_id'] == 0x0511 for e in events_generated)

# Activate dedicated channel
result = model.handle_command({"command": "cmd_channel_start"})
assert result["success"] is True
assert model._state.cmd_channel_active is True
assert model._state.cmd_decode_timer == 900.0  # 15 min
```

---

## 8. Parameter/Command Coverage Table

| Parameter ID | Parameter Name | Subsystem | In HK? (SID) | In Parameters.yaml? | Commandable via S20 or S8? | Reachable from MCS? |
|---|---|---|---|---|---|---|
| 0x0500 | ttc.mode | TTC | SID 6 | Yes | S8 func 63–64 (primary/redundant switch) | Yes (inferred from link status) |
| 0x0501 | ttc.link_status | TTC | SID 6 | Yes | No | Yes (LED indicator) |
| 0x0502 | ttc.rssi | TTC | SID 6 | Yes | No | Yes (gauge) |
| 0x0503 | ttc.link_margin | TTC | SID 6 | Yes | No | Yes (gauge) |
| 0x0504 | ttc.ul_freq | TTC | No (not in HK) | Yes | S8 func TBD (set_ul_freq) | No (inferred from config) |
| 0x0505 | ttc.dl_freq | TTC | No (not in HK) | Yes | S8 func TBD (set_dl_freq) | No (inferred from config) |
| 0x0506 | ttc.tm_data_rate | TTC | SID 6 | Yes | S8 func 65 (set_tm_rate) | No (read-only display) |
| 0x0507 | ttc.xpdr_temp | TTC | SID 6 | Yes | No | Yes (gauge) |
| 0x0508 | ttc.ranging_status | TTC | SID 6 | Yes | S8 func 76–77 (ranging_start/stop) | No (inferred from frame_sync) |
| 0x0509 | ttc.range_km | TTC | SID 6 | Yes | No | Yes (display) |
| 0x050A | ttc.contact_elevation | TTC | SID 6 | Yes | No | Yes (display) |
| 0x050B | ttc.contact_az | TTC | SID 6 | No (not in HK, but listed in procedure) | Yes | Yes (display, used for antenna pointing) |
| 0x050C | ttc.ber | TTC | SID 6 | Yes | No | Yes (gauge, "BER (log10)") |
| 0x050D | ttc.tx_fwd_power | TTC | SID 6 | Yes | S8 func TBD (set_tx_power) | No (read-only display) |
| 0x050F | ttc.pa_temp | TTC | SID 6 | Yes | No | Yes (gauge) |
| 0x0510 | ttc.carrier_lock | TTC | SID 6 | Yes | No | Yes (lock chain LED) |
| 0x0511 | ttc.bit_sync | TTC | SID 6 | Yes | No | Yes (lock chain LED) |
| 0x0512 | ttc.frame_sync | TTC | SID 6 | Yes | No | Yes (lock chain LED) |
| 0x0513 | ttc.cmd_rx_count | TTC | SID 6 | Yes | No | No (not displayed) |
| 0x0516 | ttc.pa_on | TTC | SID 6 | Yes | S8 func 66–67 (pa_on/off) | No (inferred from power state) |
| 0x0519 | ttc.eb_n0 | TTC | SID 6 | Yes | No | Yes (gauge) |
| 0x051A | ttc.agc_level | TTC | SID 6 | Yes | S8 func TBD (set_rx_gain) | No (read-only) |
| 0x051B | ttc.doppler_hz | TTC | SID 6 | Yes | No (read-only telemetry for operator reference) | No (read-only) |
| 0x051C | ttc.range_rate | TTC | SID 6 | Yes | No | No (read-only) |
| 0x051D | ttc.cmd_auth_status | TTC | SID 6 | Yes | No (should be commandable; gap) | No (read-only) |
| 0x051E | ttc.total_bytes_tx | TTC | SID 6 | Yes | No | No (not displayed) |
| 0x051F | ttc.total_bytes_rx | TTC | SID 6 | Yes | No | No (not displayed) |
| 0x0520 | ttc.antenna_deployed | TTC | SID 6 | Yes | S8 func 69 (deploy_antennas) | No (inferred from data rate) |
| 0x0521 | ttc.beacon_mode | TTC | SID 6 | Yes | S8 func TBD (set_beacon_mode) | No (not exposed) |
| 0x0522 | ttc.cmd_decode_timer | TTC | SID 6 | Yes | S8 func TBD (cmd_channel_start) | No (not exposed) |
| 0x0523 | ttc.active_gs | TTC | No | Yes (on_demand: true) | No | No |
| 0x0524 | ttc.gs_equipment_status | TTC | No | Yes (on_demand: true) | No | No |
| **Device State (S2)** | **transponder_a/b, pa, lna, antenna** | TTC | **No (gap)** | **No** | **S2 subtype 1 (on/off)** | **No (no feedback)** |

---

## Summary of Key Findings

1. **Working well:** Lock sequence, link budget, PA thermal model, event generation, transponder switchover, antenna deployment, ranging basic, HK telemetry integration, PUS protocol.

2. **Major gaps:**
   - Device on/off state (S2) has no telemetry feedback
   - PA on/off command silently fails if overheat shutdown active
   - Downlink frequency range is X-band not S-band (validation bug)
   - Device states and deployment sensors not observable pre/post action
   - Uplink timeout and recovery strategy (dedicated command channel) not proceduralized or exposed in MCS

3. **Minor gaps:**
   - Beacon mode and dedicated command channel not in tc_catalog.yaml or MCS widgets
   - Antenna deployment readiness (pyro continuity, sensor health) not modeled
   - Modulation/coding rate tuning not functional (parameters exist but effect on BER missing)
   - Coherent/non-coherent mode not affecting link budget
   - Frequency/power/AGC tuning commands exist but not integrated into operator workflow

4. **Operability readiness:** Operator can conduct LEOP, nominal contacts, and basic contingencies with current implementation. Critical blockers (device state feedback, PA thermal handling) would be discovered in integrated testing and should be resolved before LEOP simulations.


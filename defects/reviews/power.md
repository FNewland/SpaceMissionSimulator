# EPS Operability Review: EOSAT-1 Mission Simulator

**Review Date:** 2026-04-06
**Reviewer:** Spacecraft EPS Expert
**Scope:** Electrical Power System (EPS) simulator operability, operator control completeness, FDIR integration
**Standard Reference:** ECSS-E-ST-20C Rev. 2 (April 2022)

---

## 1. Scope & Assumptions

### Reviewed Artifacts

- EPS Model: `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (38 KB)
- Telemetry Structure: `/configs/eosat1/telemetry/hk_structures.yaml` (SID 1, EPS parameters 0x0100–0x0139)
- EPS Configuration: `/configs/eosat1/subsystems/eps.yaml`
- MCS Server: `/packages/smo-mcs/src/smo_mcs/server.py` (command handler, display routing)
- MCS Displays: Power Budget Monitor, System Overview Dashboard, Widgets
- Operator Procedures: LEOP-007 (power-on), COM-001 (EPS checkout), Emergency procedures
- Tests: `/tests/test_simulator/test_eps_enhanced.py` (per-line currents, overcurrent trips, UV/OV flags)
- FDIR Procedures: Load-shed stages, safe-mode entry, emergency power-down

### Mission Profile

- **Orbit:** 500 km SSO, ~95-minute period, ~35-minute eclipse duration
- **Power Source:** 6-face body-mounted GaAs (triple-junction) solar arrays, ~180 W peak (GEO beta)
- **Energy Storage:** Li-ion 120 Wh battery, nominal 26.4V, range 21.5–29.2V
- **Power Distribution:** 8 switchable/non-switchable power lines via PDM (Power Distribution Module)
- **Operating Modes:** Nominal, SAFE, Emergency; LEOP, Commissioning, Normal ops phases
- **Operators:** EPS/TCS position, Flight Director, TTC position

### ECSS Context

Per ECSS-E-ST-20C, an EPS shall provide:
- Continuous telemetry of battery voltage, current, temperature, and state-of-charge (SOC)
- Bus voltage regulation and under/over-voltage protection
- Latching or recoverable current limiters (LCL/FCL) with status and current monitoring
- Load shedding hierarchy with operator visibility
- Safing modes to preserve battery energy
- Full S8 (Function Management) commanding of switchable power lines and regulators

---

## 2. Category 1 — Described, Implemented, Works for the Mission

### 2.1 Basic Telemetry and Power Budget Monitoring

**Status:** IMPLEMENTED ✓

- Battery SoC, voltage, temperature, current (`0x0100–0x0102`, `0x0109`) streamed at 1 Hz to HK SID 1
- Bus voltage, solar array currents, power generation/consumption (`0x0105–0x0107`) available in every HK report
- Power margin calculation in MCS Power Budget Monitor widget (`power_gen - power_cons`)
- Eclipse flag (`0x0108`) and mode transitions auto-detected from orbit state
- Tests verify parameter presence and range (test_eps_enhanced.py lines 39–49)

**Operability:** EPS operator can monitor baseline power health at contact pass, detect eclipse entry/exit events, and correlate power balance with mode changes.

**Files:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 484–495 (shared_params writes)
- `/configs/eosat1/telemetry/hk_structures.yaml` lines 5–52 (SID 1 parameter list)
- `/packages/smo-mcs/src/smo_mcs/displays/power_budget.py` lines 61–75 (telemetry update)

### 2.2 Switchable Power Lines and Basic Control

**Status:** IMPLEMENTED ✓

- 8 power lines defined: OBC (non-switchable), TTC RX (non-switchable), TTC TX, Payload, FPA Cooler, Battery Heater, OBC Heater, AOCS Wheels (all switchable except OBC, RX)
- Power line on/off commands (`power_line_on`, `power_line_off`) implemented in EPS model
- Per-line status parameters (`0x0110–0x0117`) and per-line current parameters (`0x0118–0x011F`) written every tick
- LEOP-007 procedure explicitly commands `POWER_LINE_ON(line=5)` for battery heater, `POWER_LINE_ON(line=7)` for AOCS wheels
- All switchable lines verified in tests (test_eps_enhanced.py lines 200–210)

**Operability:** EPS/TCS operator can selectively enable/disable 6 switchable loads via MCS or procedure automation. Power-on sequence (LEOP-007) validates each subsystem power draw.

**Files:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 28–42 (POWER_LINE_DEFS), lines 620–623 (power_line_on/off handler)
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 504–510 (power line status telemetry)
- `/configs/eosat1/procedures/leop/sequential_power_on.md` (Steps 2–6, explicit line commands)

### 2.3 Overcurrent Protection with Trip Detection and Reset

**Status:** IMPLEMENTED ✓

- Per-line overcurrent thresholds defined (150% of nominal at 28V bus): OBC 2.0A, Payload 2.5A, TTC TX 1.0A, etc.
- OC detection logic: if line current exceeds threshold, trip flag set (bitmask `0x010D`)
- Tripped switchable lines automatically disabled to prevent damage
- OC trip flag parameter (`0x010D`) streamed in HK to inform operator
- Reset command (`reset_oc_flag`) clears trip and re-enables line (requires operator intent)
- Tests verify: OC injection trips line (lines 53–75), reset clears flag (lines 104–134), non-switchable lines immune (lines 80–99)

**Operability:** Automatic over-current cutoff protects PDM and subsystems. Operator sees trip status in HK and can reset after verifying fault cause.

**Files:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 44–49 (OC_THRESHOLDS, OC_TRIP_TIME_S)
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 350–370 (OC trip calculation and line disable)
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 701–714 (_reset_oc_flag handler)
- `/tests/test_simulator/test_eps_enhanced.py` lines 53–135 (comprehensive OC tests)

### 2.4 Under/Over-Voltage Detection and Flagging

**Status:** IMPLEMENTED ✓

- UV threshold: 26.5 V (bus); trip when `bus_voltage < 26.5`
- OV threshold: 29.5 V (bus); trip when `bus_voltage > 29.5`
- UV flag (`0x010E`), OV flag (`0x010F`) written to HK every tick
- EPS checkout procedure (COM-001, Step 1) validates bus voltage in range [27.5V, 28.5V] as nominal regulation point
- Tests verify flag setting (lines 150–183)

**Operability:** EPS operator sees voltage excursions in HK telemetry with dedicated flags. COM-001 procedure provides pass/fail criteria.

**Files:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 156–158 (_uv_threshold, _ov_threshold defaults)
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 330–340 (flag update logic)
- `/configs/eosat1/procedures/commissioning/eps_checkout.md` Step 1 (nominal range validation)

### 2.5 Eclipse Entry/Exit and Battery Discharge Modeling

**Status:** IMPLEMENTED ✓

- Eclipse state from orbit_state.in_eclipse drives solar generation to zero, battery discharges
- SoC decreases during eclipse proportional to current draw; increases during sunlit phase
- Events generated on eclipse entry/exit (`0x010C`, `0x010D`)
- COM-001 Step 4–5 monitor battery discharge and charge recovery through full eclipse cycle
- Battery temperature and thermal coupling model (TCS) coordinate with EPS heater control

**Operability:** Operator monitors eclipse power balance in real-time. HK contains eclipse indicator, battery current (charging/discharging direction), and SoC. Procedures specify acceptable discharge rate (<1.5%/min) and charge recovery expectations.

**Files:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 207–252 (eclipse detection and solar generation model)
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 213–217 (event generation on eclipse transitions)
- `/configs/eosat1/procedures/commissioning/eps_checkout.md` Steps 4–5 (eclipse monitoring and criteria)

### 2.6 Load Shedding Hierarchy and Control

**Status:** IMPLEMENTED ✓

- Load shed order defined: Payload → FPA Cooler → TTC TX → AOCS Wheels (first-shed = lowest priority)
- Load shed voltage threshold: 26.5 V bus
- Automatic progressive shedding: when bus voltage < 26.5V, shed one line per tick until voltage recovers
- Load shed stage parameter (`0x0134`) tracks current stage (0=none, 1/2/3=progressive)
- FDIR procedures (load_shed_stage1/2/3.yaml) provide operator steps to manually trigger stages
- EPS checkout procedure (COM-001, off-nominal handling) specifies load-shed trigger at <26V

**Operability:** Automatic load shedding safeguards battery. Operator sees load_shed_stage in HK and can verify which loads remain after shed events. Procedures provide manual override steps if automatic shedding fails.

**Files:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 51–53 (LOAD_SHED_ORDER, LOAD_SHED_VOLTAGE)
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 375–402 (progressive load shedding logic)
- `/configs/eosat1/procedures/commissioning/eps_checkout.md` off-nominal handling
- `/configs/eosat1/fdir/procedures/load_shed_stage*.yaml`

### 2.7 Battery Heater Control and Thermal Integration

**Status:** IMPLEMENTED ✓

- Battery heater on/off control command (`set_battery_heater`) with setpoint adjustment
- Heater state (`0x0138`) and setpoint (`0x0139`) in HK
- LEOP-007 Step 2 enables battery heater and validates temperature trending toward 5°C set point within 120s
- TCS model (tcs_basic.py) handles battery temperature dynamics; EPS and TCS coordinate via shared heater status
- Battery current calculation includes internal resistance heating (`bat_heat_w = I² × R_internal`)

**Operability:** EPS/TCS team controls thermal management. HK contains battery temperature (TCS `0x0407`) and heater status (EPS `0x0138`). LEOP validates thermal control path.

**Files:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 644–649 (set_battery_heater handler)
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 404–407 (battery thermal model)
- `/configs/eosat1/procedures/leop/sequential_power_on.md` Step 2
- `/packages/smo-simulator/src/smo_simulator/models/tcs_basic.py` (battery heater control integration)

### 2.8 Solar Array Degradation Tracking

**Status:** IMPLEMENTED ✓

- Solar array age factor (`sa_age_factor`) models GaAs degradation (~2.75%/year in LEO per physics model)
- Lifetime hours (`sa_lifetime_hours`) accumulated during sunlit phases
- Panel degradation factors per face (`sa_panel_degradation` dict) support single-panel failure scenarios
- Parameters `0x0123` (sa_age_factor), `0x0126` (sa_lifetime_hours) streamed to HK
- EPS checkout procedure (COM-001 Step 3) validates power generation within predicted range (100–140W) and allows assessment of degradation if underperforming

**Operability:** EPS operator can track solar array aging over mission lifetime and adjust power budget accordingly. If generation drops below prediction, operator can investigate single-panel loss via per-panel current analysis.

**Files:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 219–224 (degradation model)
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 516–520 (sa_age_factor, sa_lifetime_hours to HK)
- `/configs/eosat1/procedures/commissioning/eps_checkout.md` Step 3

### 2.9 Separation Timer and PDM Unswitchable Line Management

**Status:** IMPLEMENTED ✓

- Separation timer (`sep_timer_active`, `sep_timer_remaining`) and PDM unswitchable status (`pdm_unsw_status` bitmask) parameters present
- PDM unswitchable status (`0x0129`) streamed in HK
- OBC and RX lines hard-wired ON (non-switchable) to ensure minimum comms capability during LEOP
- Parameters allow LEOP procedure to verify that critical subsystems remain powered throughout separation transient

**Operability:** Flight Director can verify that OBC and RX remain powered during separation via `pdm_unsw_status` parameter. Separation event tracking enables post-separation power-up sequencing.

**Files:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 89–91 (separation state tracking)
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 522–525 (separation params to HK)

### 2.10 FDIR Safe Mode and Emergency Power Down

**Status:** IMPLEMENTED ✓

- EPS mode parameter (`eps_mode`: 0=nominal, 1=safe, 2=emergency) commandable via `set_eps_mode`
- FDIR procedures `safe_mode_entry.yaml`, `emergency_power_down.yaml` provide steps to transition modes
- Emergency load shed command (`emergency_load_shed`) can force shed to stage N immediately
- Procedures coordinate with OBC safe-mode entry and TCS/AOCS safing
- EPS checkout off-nominal handling specifies safe mode transition at `bus_voltage < 26.0V`

**Operability:** Flight Director can command EPS into safe mode (reduced power) or emergency mode (minimal power) via procedure or direct command. Operators see mode transitions in HK and follow coordinated FDIR playbooks.

**Files:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 122, 660–665 (eps_mode state, command handler)
- `/configs/eosat1/fdir/procedures/safe_mode_entry.yaml`, `emergency_power_down.yaml`
- `/configs/eosat1/procedures/commissioning/eps_checkout.md` off-nominal handling

---

## 3. Category 2 — Described as Needed but Not Yet Implemented

### 3.1 Battery State-of-Health (Depth of Discharge, Cycle Count, Cell Balancing)

**Status:** PARAMETERS PRESENT, CONTROL LOGIC MISSING ⚠

- Parameters `0x0120` (bat_dod_pct), `0x0121` (bat_cycles), `0x0136` (battery_health%) are written to HK
- Model initializes `bat_dod_pct = 25.0`, `bat_cycles = 0`, `bat_max_dod_pct = 80.0`
- **However:** DoD calculation is not coupled to SoC updates; cycle count never incremented; DoD limit is defined but not enforced in command processing
- Battery charge regulator (BCR) status is not represented; operator cannot command BCR into different modes (e.g., trickle charge, equalization)
- Cell balancing logic absent (Li-ion packs require active cell voltage balancing to extend life and avoid over-charge)

**Impact:** Operator sees Battery Health % in HK but cannot understand its basis. No mechanism to slow charging near end-of-charge or limit DoD to extend battery life. Multi-cell management not visible.

**ECSS Relevance:** ECSS-E-ST-20C requires battery management parameters (SoC, DoD, charge rate, cell voltages) and operator ability to adjust charge controller settings. This feature is partially visible but not controllable.

**Recommended Implementation:**
1. Integrate DoD with SoC: `DoD = 100 - SoC`; enforce max_DoD_pct limit in discharge logic
2. Implement cycle counting: increment `bat_cycles` on each charge/discharge transition
3. Add BCR mode command: allow operator to select Normal, Trickle, Pulse, or Equalize charge modes
4. Add cell voltage monitoring: expose min/max cell voltage to HK (simulate with +/-5% variation from pack average)

**Files Affected:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (lines 101–103, 318–330, handle_command)

### 3.2 MPPT (Maximum Power Point Tracking) Tracker Control and Efficiency Tuning

**Status:** PARAMETER PRESENT, NO OPERATOR CONTROL ⚠

- MPPT efficiency parameter (`0x0122`, `mppt_efficiency = 0.97`) is written to HK
- Model includes `mppt_efficiency` in solar generation calculation (line 453)
- **However:** Operator has no command to adjust MPPT operating point, enable/disable MPPT, or select tracking algorithm
- MPPT losses are simulated statically; dynamic tracking behavior (slew-rate limits, convergence time) not modeled
- No telemetry of MPPT input voltage, output current, or tracking mode

**Impact:** MPPT is a "black box" to operator. If solar arrays underperform, operator cannot diagnose whether issue is panel degradation, shadow, attitude error, or MPPT malfunction.

**ECSS Relevance:** ECSS-E-ST-20-20C (Power Supply Interface Standard) specifies MPPT status and control telemetry. EPS operators should see MPPT health and have ability to force bypass or alternate tracking mode.

**Recommended Implementation:**
1. Add MPPT mode command: Normal, Bypass, or Forced-Voltage mode
2. Add telemetry: MPPT input voltage, output current, tracking mode status
3. Model MPPT tracking transient: on solar eclipse exit, show 2–5s ramp-up to peak power
4. Failure injection: allow MPPT stuck-at-low-voltage or stuck-at-bypass scenarios

**Files Affected:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (lines 104, 453)

### 3.3 Battery Charge Rate Command and Adaptive Charging

**Status:** PARAMETER PRESENT, COMMAND INCOMPLETE ⚠

- Charge rate override parameter (`charge_rate_override_a`) state exists (line 118); command handler accepts `set_charge_rate` (lines 630–633)
- **However:** Command only sets internal state variable; does not actually limit charge current in tick logic
- No adaptive charging logic (e.g., C-rate limiting, temperature-compensated taper)
- No verification that requested charge rate is safe (e.g., limit to 0.5C for Li-ion, reduce at high temperature)
- No feedback on actual vs. requested charge current

**Impact:** Operator can command charge rate but has no assurance it is applied. EPS model ignores the override and charges at fixed rate based on solar input and bus regulation.

**ECSS Relevance:** Battery charge controllers must accept ground commands to adjust charge current for life extension (e.g., reduce C-rate in cold, reduce at high DoD).

**Recommended Implementation:**
1. In tick logic: if `charge_rate_override_a > 0`, clamp battery charge current to that value during sunlit phases
2. Add safety check: reject rates >0.5C; warn if rate >0.3C during high-temperature phases
3. Add feedback telemetry: `actual_charge_rate_a` parameter to HK showing achieved charge current
4. Model C-rate derating: if temp < 5°C or temp > 40°C, reduce safe charge rate

**Files Affected:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (lines 118, 630–633, tick method)

### 3.4 Per-Panel Solar Array Current Monitoring and Single-Panel Failure Diagnostics

**Status:** PARAMETERS PRESENT, OPERATOR VISIBILITY MISSING ⚠

- Per-panel currents stored in state (`sa_panel_currents` dict, 6 faces: px, mx, py, my, pz, mz)
- Per-panel current parameters written to HK (`0x012B–0x0130`) per tick (lines 528–530)
- Per-panel degradation factors present (`sa_panel_degradation` dict)
- **However:** Per-panel parameters NOT included in HK SID 1 (EPS structure in hk_structures.yaml only lists 0x0100–0x0139, stopping before per-panel data)
- MCS displays do not show per-panel breakdown; Power Budget widget only shows total array currents (sa_a_current, sa_b_current) as aggregates
- Procedure COM-001 Step 3 mentions "individual array string currents" but no off-nominal procedure to pull per-panel data

**Impact:** If a single solar panel fails, operator cannot quickly diagnose it without manual downlink request. Slow FDIR response time.

**ECSS Relevance:** EPS telemetry should include disaggregated power source and load monitoring to enable rapid failure detection.

**Recommended Implementation:**
1. Add per-panel current parameters to HK SID 1 (`0x012B–0x0130`)
2. Add per-panel status widget to MCS Power Budget display (6-panel array diagram with current bar for each)
3. Add per-panel anomaly event: if any panel current < 10% of expected (in sunlight), generate event
4. Add off-nominal procedure step: "Individual Panel Power Check" to pull per-panel currents and identify failed panel

**Files Affected:**
- `/configs/eosat1/telemetry/hk_structures.yaml` (add params 0x012B–0x0130 to SID 1)
- `/packages/smo-mcs/src/smo_mcs/displays/power_budget.py` (add per-panel breakdown display)
- `/configs/eosat1/procedures/commissioning/eps_checkout.md` (add Step 3b for per-panel diagnostics)

### 3.5 Solar Array Drive Angle Command Feedback and SADA (Solar Array Drive Actuator) Status

**Status:** COMMAND PRESENT, NO OPERATOR FEEDBACK ⚠

- Solar array drive angle command (`set_solar_array_drive`, lines 634–637) accepts angle in degrees and clamps to [-90, +90]
- Command stores angle in state but does not feed back to sun-pointing model
- No SADA motor status parameters (e.g., slew rate, current draw, position feedback error)
- EPS checkout procedure (COM-001) does not verify solar array orientation after power-on

**Impact:** Operator commands SADA but cannot verify whether command succeeded. No feedback of actual panel orientation or slew rate limit.

**ECSS Relevance:** SADA is a critical component for orbit-average power; its status must be monitored (motor current, position, speed, faults).

**Recommended Implementation:**
1. Add SADA status parameters to HK: `0x013A` (current position [deg]), `0x013B` (motor current [A]), `0x013C` (slew rate [deg/s])
2. Model SADA slew transient: position ramps at ~2 deg/s; motor draws ~0.5A during slew, ~0.1A holding
3. Add SADA failure mode: "stuck-at-angle" (position fails to update despite command)
4. Add LEOP procedure step: "Verify Solar Array Drive" — command SADA through full range and confirm position feedback

**Files Affected:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (add SADA status parameters and slew model)
- `/configs/eosat1/telemetry/hk_structures.yaml` (add SADA params)
- `/configs/eosat1/procedures/leop/sequential_power_on.md` (add SADA verification step)

### 3.6 Regulated vs. Unregulated Bus Telemetry

**Status:** SINGLE BUS VOLTAGE ONLY ⚠

- Model provides single bus voltage parameter (`0x0105`, bus_voltage = 28.2V nominal)
- Many spacecraft have separate regulated and unregulated buses (e.g., 28V regulated main bus, 42V unregulated for low-impedance payload loads)
- **However:** Only one bus model; no distinction between regulated output and unregulated battery voltage

**Impact:** Operator cannot independently monitor primary vs. secondary power architecture. Limited diagnostics if bus regulation fails.

**ECSS Relevance:** ECSS-E-ST-20-20C specifies regulated bus (typically 27–29V) and optional unregulated bus (battery voltage ~14.4–29.2V). Both should be telemetered.

**Recommended Implementation (Optional for EOSAT-1 if single-bus architecture confirmed):**
1. Verify EOSAT-1 power architecture (is it single-bus or dual-bus?)
2. If dual-bus: add parameter `0x013D` (unregulated_bus_v = battery voltage); add bus regulation mode to HK

**Files Affected:**
- TBD based on architecture confirmation

---

## 4. Category 3 — Not Yet Described or Implemented but Needed

### 4.1 Battery Cell Voltage Monitoring and Over-Charge Protection

**Status:** NOT IMPLEMENTED ❌

- Spacecraft with Li-ion battery packs require individual cell voltage monitoring to prevent over-charge (>4.2V/cell) and over-discharge (<2.5V/cell)
- Model provides only pack voltage and current; no cell-level telemetry
- No over-charge or over-discharge alarm thresholds

**Impact:** No visibility into cell imbalance or early warning of cell failure. Over-charge can trigger thermal runaway (catastrophic battery failure).

**Operator Needs:**
- HK parameter: min/max cell voltage (or at least pack average cell voltage)
- Command: adjust over-charge / over-discharge thresholds (if BCR supports user-tuning)
- Alarm: cell voltage out of bounds (red flag)

**Recommended Implementation:**
1. Add battery model: simulate 2-series battery pack (2S, 40 Ah nominal); each cell nominal 3.7V nominal
2. Add cell voltage telemetry: `0x013E` (min_cell_v), `0x013F` (max_cell_v)
3. Add cell balancing current: if max_cell_v - min_cell_v > 0.1V, activate passive balancing (small resistor bleed current on high cells)
4. Add alarms: if min_cell_v < 2.5V → battery undertemp/full, if max_cell_v > 4.2V → battery overtemp/charge error

**Files Affected:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (expand battery state, add cell voltage model)
- `/configs/eosat1/telemetry/hk_structures.yaml` (add cell voltage params)

### 4.2 Watchdog Timer Management and Latch-Up Protection (PDM Watchdog)

**Status:** NOT IMPLEMENTED ❌

- ECSS-E-ST-20C specifies that PDM switchable power outputs should have latch-up protection (autonomous shutdown if overcurrent persists)
- Watchdog timer status (time remaining, enable/disable) is not represented
- No watchdog reset command available to operator

**Impact:** If a line latches due to persistent fault, operator has no visibility into watchdog timeout or ability to reset watchdog before automatic safe mode triggers.

**Operator Needs:**
- HK parameter: `watchdog_timeout_s` (time until automatic safe mode)
- Command: reset watchdog timer (extends timeout if operator is actively managing recovery)
- Alarm: watchdog timeout imminent (yellow flag at <60s)

**Recommended Implementation:**
1. Add PDM watchdog state: `watchdog_enabled`, `watchdog_timeout_s`, `watchdog_time_remaining_s`
2. Add watchdog parameter to HK: `0x0140` (time_remaining_s)
3. Add reset command: `reset_watchdog` (if watchdog_time_remaining < 60s, extend by 5 minutes)
4. In tick: if watchdog expires, force safe mode entry (shed all non-critical loads)

**Files Affected:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (add watchdog state, tick logic, command)
- `/configs/eosat1/telemetry/hk_structures.yaml` (add watchdog param)

### 4.3 Redundant Solar Array String Telemetry and Foldback Current Limiter (FCL) Status

**Status:** PARTIALLY IMPLEMENTED (SA strings, but no FCL) ⚠❌

- Model supports sa_a_current and sa_b_current (two strings) per lines 62–67
- **However:** No FCL status parameters; if one string experiences under-current (e.g., connector failure, cell degradation), no operator alarm
- No FCL mode selection (auto-reactivate vs. manual reset)

**Impact:** Operator cannot quickly identify if one solar string has failed; only sees total current drop.

**Operator Needs:**
- HK parameters: `sa_a_fcl_status` (armed/tripped), `sa_b_fcl_status` (armed/tripped)
- Command: reset individual FCL (if manual-reset mode enabled)
- Alarm: one string current << other string current (string loss)

**Recommended Implementation:**
1. Add FCL status bitmask: bit 0 = SA-A FCL status, bit 1 = SA-B FCL status (1=armed, 0=tripped)
2. Add FCL mode command: `set_fcl_mode(string, auto_rearm|manual_reset)`
3. Add FCL reset command: `reset_fcl(string)`
4. Failure injection: "solar_string_loss" (one FCL trips, stays tripped until reset)
5. HK parameter: `0x0141` (fcl_status_bitmask)

**Files Affected:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (add FCL state, commands)
- `/configs/eosat1/telemetry/hk_structures.yaml` (add FCL param)
- `/tests/test_simulator/test_eps_enhanced.py` (add FCL trip scenario tests)

### 4.4 Power Line Switchable vs. Non-Switchable Visibility in MCS

**Status:** NOT VISIBLE IN UI ❌

- POWER_LINE_SWITCHABLE dict exists in model (line 40) indicating which lines can be switched
- **However:** MCS displays do NOT indicate which lines are switchable. User interface does not prevent attempted command to switch non-switchable lines (OBC, RX)
- Power Budget widget shows all 8 lines but does not grey out or lock non-switchable ones

**Impact:** Operator may attempt to switch OBC or RX line, command will fail with error message, causing confusion. No quick visual reference of architecture.

**Operator Needs:**
- Power Budget panel shows each line with visual lock icon (🔒) for non-switchable, unlock icon (🔓) for switchable
- Tooltip/legend explains that OBC and RX are always-on for mission safety
- MCS prevents drag/click on non-switchable lines (grey out, show "not switchable" tooltip)

**Recommended Implementation:**
1. In MCS Power Budget widget render: check POWER_LINE_SWITCHABLE for each line
2. Add CSS class "non-switchable" to locked lines; display lock icon
3. Disable click handler on non-switchable lines; show tooltip "This line is always-on"
4. Add legend to power budget panel explaining safety-critical vs. load-shed lines

**Files Affected:**
- `/packages/smo-mcs/src/smo_mcs/displays/power_budget.py` (lines 61–108, extend display logic)
- `/packages/smo-mcs/src/smo_mcs/static/` (add CSS and JavaScript for switchable line UI)

### 4.5 Energy Balance and Orbit-Average Power Calculation Assistance

**Status:** NOT IMPLEMENTED ❌

- Model calculates instantaneous power margin; no orbit-integrated energy balance or eclipse energy budget calculation
- COM-001 Step 6 requires operator to "Calculate total spacecraft power consumption from telemetry" and "Verify positive energy balance over one complete orbit" — this is a manual calculation
- No MCS tool to visualize orbit-average power generation vs. consumption

**Impact:** Operator must manually calculate or use external tools to verify power budget. Slow commissioning, high error risk.

**Operator Needs:**
- MCS display: Over one orbit, show cumulative energy generated in sunlit phase, cumulative energy consumed (sunlit + eclipse), and net energy balance
- Tool: given current average power, predict eclipse energy margin and safe payload power allocation
- Real-time margin indicator: "Safe eclipse margin: +23% SoC" (can tolerate 23% SoC loss before critical voltage)

**Recommended Implementation:**
1. Extend Power Budget widget: add orbit-average power trend (sunlit avg, eclipse avg)
2. Add new display panel: "Energy Balance" showing cumulative energy plot over one orbit
3. Add MCS calculator: given payload power request (e.g., "I want 20W for imaging"), calculate feasible duty cycle without violating DoD limits
4. Add HK parameter: `orbit_avg_power_gen_w`, `orbit_avg_power_cons_w` (computed by on-board EPS firmware, or manually by ground)

**Files Affected:**
- `/packages/smo-mcs/src/smo_mcs/displays/power_budget.py` (extend with orbit-average calculations)
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (add orbit-average tracking)
- New MCS widget: Energy Balance panel

### 4.6 Battery Thermal Runaway and Over-Temperature Safing

**Status:** PARTIALLY IMPLEMENTED (Temperature monitoring, no automatic safing) ⚠

- Battery temperature parameter (`0x0102`, `bat_temp`) is monitored
- Temperature event alarms generated at >45°C and <-5°C (lines 410–413)
- **However:** No automatic safing action (e.g., reduce charge rate, disable heater, shed loads) when battery overtemp detected
- TCS heater control can increase battery temperature, but no coordinated shutdown if overtemp risk

**Impact:** If battery temperature rises (e.g., high summer eclipse, internal short), no automatic protective action. Operator must manually reduce charge rate or shed loads.

**Operator Needs:**
- Automatic action: if `bat_temp > 50°C`, disable battery heater and set load_shed_stage to 1 (reduce power draw)
- Command: override automatic limits (allow charging at high temp if operator accepts risk)
- HK parameter: `battery_overtemp_shutdown_enabled` (boolean, controllable)

**Recommended Implementation:**
1. In tick: if `bat_temp > 50°C` and `battery_overtemp_shutdown_enabled`, execute load shedding to stage 1
2. Add command: `set_battery_overtemp_threshold(temp_c)` (default 50°C)
3. Add HK parameter: `0x0142` (overtemp_action_armed)
4. Coordinate with TCS: if battery heater would be enabled and `bat_temp > 45°C`, inhibit heater

**Files Affected:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (tick method, add thermal safing)
- `/packages/smo-simulator/src/smo_simulator/models/tcs_basic.py` (coordinate heater inhibit)
- `/configs/eosat1/telemetry/hk_structures.yaml` (add overtemp threshold param)

---

## 5. Category 4 — Described/Implemented but NOT Helpful for This Mission

*(No items in this category for EPS. All implemented features are operationally relevant.)*

---

## 6. Category 5 — Inconsistent or Incoherent Implementation

### 5.1 Duplicate Parameter Writes and Variable Naming Inconsistency

**Status:** BUG IN EPS MODEL ❌

**Location:** `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 500–582

**Issue:** In the same tick method, EPS parameters are written to `shared_params` multiple times with conflicting logic:

- Lines 504–506: `for i, line_name in enumerate(POWER_LINE_NAMES): shared_params[0x0110 + i] = 1 if s.power_lines.get(line_name, False) else 0`
- Lines 545–547: `for i, line_name in enumerate(POWER_LINE_NAMES): shared_params[0x0110 + i] = 1 if lines.get(line_name, False) else 0`
  **Error:** Variable `lines` is undefined; should be `s.power_lines`. This line will crash at runtime.

Similarly, lines 568–571 use undefined variable `lines` and undefined variable `gen_w`, `cons_w`:
```python
power_line_bitmask = 0
for i, line_name in enumerate(POWER_LINE_NAMES):
    if lines.get(line_name, False):  # ← undefined, should be s.power_lines
        power_line_bitmask |= (1 << i)
shared_params[0x0135] = max(-80.0, min(80.0, gen_w - cons_w))  # ← undefined
```

**Impact:**
- Model will raise `NameError: name 'lines' is not defined` on first tick
- Parameters 0x0110–0x0117 (power line status) will not be written correctly
- Parameter 0x0131 (power line bitmask) will not be written
- Parameter 0x0135 (power margin) will not be written

**This is a critical bug that prevents the model from running.**

**Fix:** Remove duplicate blocks (lines 545–551, 568–582). Keep only the first write (lines 504–510, 533–542) which correctly uses `s.power_lines`, `s.power_gen_w`, `s.power_cons_w`.

**Files Affected:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 500–582

### 5.2 Missing Initialization of lines Variable (Copy-Paste Error)

**Status:** BUG RELATED TO 5.1 ❌

**Location:** Lines 546–547, 570

**Issue:** Variable `lines` is referenced but never defined. Likely a copy-paste error where original code had `lines = s.power_lines` assignment that was removed.

**Fix:** Either:
- **Option A (preferred):** Delete the duplicate blocks and keep the first write
- **Option B:** Add `lines = s.power_lines` before the second block (not recommended — adds confusion)

**Files Affected:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 545–582

### 5.3 Inconsistent Parameter ID Mapping (Discrepancy Between model and config)

**Status:** SEMANTIC INCONSISTENCY ⚠

**Location:**
- Model param_ids defaults (lines 184–190): `bat_voltage: 0x0100, bat_soc: 0x0101, ...`
- EPS config file (eps.yaml lines 38–49): Same mapping

However, HK structure (hk_structures.yaml lines 5–52) includes EXTENDED parameters (0x0120–0x0139) that are NOT listed in eps.yaml param_ids section. This creates confusion about which parameters are "official" vs. "extended."

**Impact:** Operator manual and code comments may reference different parameter ranges. FDIR procedures may not align with all available parameters.

**Recommended Fix:** Document parameter ranges clearly:
- **Core EPS params** (0x0100–0x010A): battery, bus, solar generation
- **Enhanced EPS params** (0x010B–0x010F): array voltages, OC trip flags, UV/OV flags
- **Power line status** (0x0110–0x0117): per-line on/off
- **Per-line currents** (0x0118–0x011F): per-line current draw
- **Flight hardware realism** (0x0120–0x0126): DoD, cycles, MPPT, degradation
- **PDM and separation** (0x0127–0x012A)
- **Per-panel currents** (0x012B–0x0130)
- **Enhanced telemetry** (0x0131–0x0139): power line bitmask, charge rate, SADA angle, load shed stage, power margin, battery health, EPS mode, heater status

Add section to eps.yaml documenting this partitioning.

**Files Affected:**
- `/configs/eosat1/subsystems/eps.yaml` (add param range documentation)

### 5.4 OC Trip Flag Reset Does Not Match SID 1 HK Parameter Definition

**Status:** DESIGN INCONSISTENCY ⚠

**Location:**
- Model: `oc_trip_flags` is a bitmask (line 84), written to parameter 0x010D as uint8 (packed_format: B, line 18 of hk_structures.yaml)
- Test expects: reset_oc_flag clears bit N in oc_trip_flags (lines 128–130 of test_eps_enhanced.py)
- **However:** HK streams uint8; if >8 lines, cannot represent all trips

**Observation:** Currently 8 lines, so bitmask fits in uint8. But this is fragile. If design expands to 12+ lines, bitmask will overflow.

**Recommended Future-Proofing:**
- If lines increase, change parameter 0x010D to uint16 or uint32
- Document: "Bit N = Line N OC trip status (1=tripped, 0=armed)"

**Files Affected:**
- `/configs/eosat1/telemetry/hk_structures.yaml` line 18 (consider uint16 even if not needed now)
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` comment (clarify bitmask format)

---

## 7. Top-5 Prioritized Defects for Issue Tracker

### Defect #1: CRITICAL — Duplicate Variable Reference Crash in eps_basic.py

**Title:** `NameError: name 'lines' is not defined` in EPS model tick() method

**Severity:** CRITICAL (blocks model execution)

**Description:**
Lines 546–547 and 570–571 of `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` reference undefined variable `lines`. This is a copy-paste error where duplicate code blocks attempt to write power line status and current parameters to `shared_params` but use wrong variable names (`lines` instead of `s.power_lines`; `gen_w`/`cons_w` instead of `s.power_gen_w`/`s.power_cons_w`).

**Affected Files:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 545–582 (duplicate blocks)

**Affected Parameters:**
- 0x0110–0x0117 (power line status) — will not be written correctly
- 0x0131 (power line bitmask) — will not be written
- 0x0135 (power margin) — will not be written

**Suggested Fix:**
Remove the duplicate blocks (lines 545–551 and 568–582). Keep only the first write sequence (lines 504–510 and 533–542) which correctly uses `s.power_lines`, `s.power_gen_w`, `s.power_cons_w`. Verify against line 507 reference to `lines` variable in line 547.

**Test Case:**
Run `pytest tests/test_simulator/test_eps_enhanced.py::TestEPSEnhanced::test_per_line_current_values` — should fail with NameError.

### Defect #2: MAJOR — Per-Panel Solar Current Parameters Not in HK SID 1

**Title:** Solar array per-panel current telemetry (0x012B–0x0130) not exported in housekeeping

**Severity:** MAJOR (limits FDIR diagnostics for solar array failures)

**Description:**
Model writes per-panel solar array currents to `shared_params` (lines 528–530, 562–565), but HK structure SID 1 (hk_structures.yaml) does not include these parameters. Operator cannot quickly diagnose single-panel failure during eclipse or commissioning. COM-001 EPS checkout procedure mentions checking "individual array string currents" but provides no mechanism to retrieve them.

**Affected Files:**
- `/configs/eosat1/telemetry/hk_structures.yaml` (missing params 0x012B–0x0130)
- `/packages/smo-mcs/src/smo_mcs/displays/power_budget.py` (Power Budget widget does not show per-panel breakdown)

**Suggested Fix:**
1. Add lines to hk_structures.yaml SID 1 parameter list:
   ```yaml
   - { param_id: 0x012B, pack_format: H, scale: 100 }  # px panel current
   - { param_id: 0x012C, pack_format: H, scale: 100 }  # mx panel current
   - { param_id: 0x012D, pack_format: H, scale: 100 }  # py panel current
   - { param_id: 0x012E, pack_format: H, scale: 100 }  # my panel current
   - { param_id: 0x012F, pack_format: H, scale: 100 }  # pz panel current
   - { param_id: 0x0130, pack_format: H, scale: 100 }  # mz panel current
   ```
2. Update Power Budget widget to display 6-panel current array with current bar for each face
3. Add MCS alarm: if any panel current < 10% of expected in sunlight, generate warning event

**Impact on Procedures:**
- LEOP-007: Add verification step to confirm all 6 panels generating current after SADA deployment
- COM-001: Add Step 3b for per-panel power verification

### Defect #3: MAJOR — Battery Depth-of-Discharge Not Coupled to State-of-Charge

**Title:** `bat_dod_pct` parameter not updated with SoC changes; DoD limit not enforced

**Severity:** MAJOR (battery aging and protection incomplete)

**Description:**
Model initializes `bat_dod_pct = 25.0` (lines 101, 110) but never updates it in tick() method when `bat_soc_pct` changes. DoD should be `100 - SoC`, and discharge should be limited to `max_dod_pct = 80%` (deep discharge damage). Operator sees a Battery Health % parameter (0x0136) but it is calculated naively as `100 - (bat_cycles * 0.05)` without any coupling to actual DoD. Battery cycle count (`bat_cycles`) is never incremented.

**Affected Files:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 101–107 (DoD and cycle tracking state)
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` tick method (no DoD update logic)

**Suggested Fix:**
1. In tick(), after SoC is updated: `bat_dod_pct = 100.0 - bat_soc_pct`
2. Track charge/discharge transitions: `if bat_soc_pct < soc_prev and not _was_charging: _was_charging = False` (detect discharge start); `if bat_soc_pct > soc_prev and _was_charging: bat_cycles += 1` (detect charge completion)
3. Add discharge rate limit: if SoC would drop below (100 - max_dod_pct) = 20%, clamp SoC and prevent further discharge (safe mode trigger)
4. Recalculate battery health: `health % = 100 - (bat_cycles * 0.1) - (max_dod_exceeded_count * 5)`

**Test Case:**
Add test: Eclipse for 35 minutes at 50W discharge → SoC should drop by ~10%, DoD should increase from 25% to 35%, cycles should remain 0 (discharge only). At next eclipse, SoC should not drop below 20% (max DoD limit enforced).

### Defect #4: MAJOR — Battery Charge Rate Override Command Not Enforced in Model

**Title:** `set_charge_rate` command accepted but not applied; no feedback of actual charge rate

**Severity:** MAJOR (operator cannot control battery charge lifecycle)

**Description:**
Model has `charge_rate_override_a` state variable (line 118) and accepts `set_charge_rate` command (lines 630–633), but the tick() method does not apply this limit. Battery charges at whatever rate the solar array and bus regulation provide, ignoring the operator's requested rate. This violates ECSS requirement that operator can adjust charge current for life extension (e.g., reduce C-rate in cold or at high DoD).

**Affected Files:**
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 118, 630–633 (state and command handler)
- `/packages/smo-simulator/src/smo_simulator/models/eps_basic.py` lines 300–330 (charge logic in tick) — no rate limiting

**Suggested Fix:**
1. In tick(), calculate battery charge current from solar power and bus voltage: `charge_current = (power_gen - power_cons) / bus_voltage` (Amperes)
2. If `charge_rate_override_a > 0`, clamp charge current: `charge_current = min(charge_current, charge_rate_override_a)`
3. Add safety check: reject command if `rate_a > 0.5C` (0.5C = 20A for 40Ah battery); warn if `rate_a > 0.3C` during high temperature (>35°C)
4. Add HK parameter: `0x0143` (actual_charge_current_a) to show achieved charge rate

**Impact on Procedures:**
- COM-001: Add Step 6b for charge rate verification — confirm actual charge current = requested rate

### Defect #5: MAJOR — MCS Power Budget Widget Cannot Distinguish Switchable from Non-Switchable Lines

**Title:** UI provides no visual indication of which power lines can be switched vs. always-on

**Severity:** MAJOR (operator confusion, potential invalid commands)

**Description:**
MCS Power Budget monitor displays all 8 power lines with on/off toggle buttons, but does not indicate that OBC and TTC RX are non-switchable (always-on for mission safety). User may attempt to command these lines off, resulting in error. The POWER_LINE_SWITCHABLE dict exists in the model but is not exposed to the frontend.

**Affected Files:**
- `/packages/smo-mcs/src/smo_mcs/displays/power_budget.py` (render logic, lines 61–108)
- `/packages/smo-mcs/src/smo_mcs/static/` (HTML/CSS/JS for power budget panel)
- `/packages/smo-mcs/src/smo_mcs/server.py` (API endpoint for power line control may not return switchability info)

**Suggested Fix:**
1. Extend Power Budget display API to include switchability flag for each line: `{ line_name: "obc", on: true, switchable: false, status: "nominal" }`
2. In UI, render non-switchable lines with 🔒 lock icon and grey-out the toggle button
3. Add tooltip/legend: "Non-switchable lines (OBC, TTC RX) are always-on to ensure mission control capability during LEOP and contingencies"
4. Disable click handler on non-switchable lines; show alert if user attempts to toggle

**Test Case:**
MCS UI should show OBC and TTC RX with lock icons and disabled toggles. Other lines (payload, FPA cooler, heaters, wheels, TTC TX) should have enabled toggles.

---

## 8. Parameter/Command Coverage Table

| Param ID | Mnemonic | Unit | Implemented? | In HK SID 1? | Commandable (S8/S20)? | MCS Widget? | Notes |
|----------|----------|------|--------------|-------------|----------------------|------------|-------|
| 0x0100 | bat_voltage | V | ✓ | ✓ | S20 SET_PARAM | Gauge | Battery terminal voltage; nominal 26.4V |
| 0x0101 | bat_soc | % | ✓ | ✓ | S20 GET_PARAM | Gauge | State of charge 0–100%; target >60% pre-LEOP |
| 0x0102 | bat_temp | °C | ✓ | ✓ | S20 GET_PARAM | Gauge | Battery temperature; alarm >45°C, <-5°C |
| 0x0103 | sa_a_current | A | ✓ | ✓ | — | Gauge | Solar array A current; depends on beta angle |
| 0x0104 | sa_b_current | A | ✓ | ✓ | — | Gauge | Solar array B current; depends on beta angle |
| 0x0105 | bus_voltage | V | ✓ | ✓ | S20 GET_PARAM | Gauge | Regulated bus; nominal 28.0V ±0.5V |
| 0x0106 | power_cons | W | ✓ | ✓ | — | Gauge | Total power consumption; depends on load state |
| 0x0107 | power_gen | W | ✓ | ✓ | — | Gauge | Total power generation; zero in eclipse |
| 0x0108 | eclipse_flag | bool | ✓ | ✓ | — | Indicator | Eclipse active (1) or sunlit (0) |
| 0x0109 | bat_current | A | ✓ | ✓ | — | Gauge | Battery current; +charge, -discharge |
| 0x010A | bat_capacity | Wh | ✓ | ✓ | — | Gauge | Remaining capacity = SoC% × 120 Wh |
| 0x010B | sa_a_voltage | V | ✓ | ✓ | — | Gauge | Solar array A voltage; ~28.2V nominal |
| 0x010C | sa_b_voltage | V | ✓ | ✓ | — | Gauge | Solar array B voltage; ~28.2V nominal |
| 0x010D | oc_trip_flags | bitmask | ✓ | ✓ | — | Status | Per-line overcurrent trip flags (8 bits) |
| 0x010E | uv_flag | bool | ✓ | ✓ | — | Alarm | Undervoltage flag; alarm if <26.5V |
| 0x010F | ov_flag | bool | ✓ | ✓ | — | Alarm | Overvoltage flag; alarm if >29.5V |
| 0x0110 | line_0_obc | bool | ✓ | ✓ | — | Indicator | OBC power line status (always ON) |
| 0x0111 | line_1_ttc_rx | bool | ✓ | ✓ | — | Indicator | TTC RX power line status (always ON) |
| 0x0112 | line_2_ttc_tx | bool | ✓ | ✓ | S8 power_line_on/off | Toggle | TTC TX power line status (switchable) |
| 0x0113 | line_3_payload | bool | ✓ | ✓ | S8 power_line_on/off | Toggle | Payload power line status (switchable) |
| 0x0114 | line_4_fpa_cooler | bool | ✓ | ✓ | S8 power_line_on/off | Toggle | FPA cooler power line status (switchable) |
| 0x0115 | line_5_htr_bat | bool | ✓ | ✓ | S8 power_line_on/off + S8 set_battery_heater | Toggle | Battery heater power line status (switchable) |
| 0x0116 | line_6_htr_obc | bool | ✓ | ✓ | S8 power_line_on/off | Toggle | OBC heater power line status (switchable) |
| 0x0117 | line_7_aocs_wheels | bool | ✓ | ✓ | S8 power_line_on/off | Toggle | AOCS wheels power line status (switchable) |
| 0x0118–0x011F | line_0–7_currents | A | ✓ | ✓ | — | Gauge | Per-line current draw (8 lines) |
| 0x0120 | bat_dod | % | ✓ | ✓ | — | Gauge | Depth of discharge; NOT coupled to SoC (BUG) |
| 0x0121 | bat_cycles | count | ✓ | ✓ | — | Counter | Battery cycle count; never incremented (BUG) |
| 0x0122 | mppt_efficiency | ratio | ✓ | ✓ | — | Gauge | MPPT tracker efficiency (0.97 nominal) |
| 0x0123 | sa_age_factor | ratio | ✓ | ✓ | — | Gauge | Solar array age degradation factor (0.5–1.0) |
| 0x0124 | sa_a_degradation | ratio | ✓ | ✓ | — | Gauge | Solar array A degradation factor |
| 0x0125 | sa_b_degradation | ratio | ✓ | ✓ | — | Gauge | Solar array B degradation factor |
| 0x0126 | sa_lifetime_hours | hours | ✓ | ✓ | — | Counter | Cumulative sunlit hours on arrays |
| 0x0127 | sep_timer_active | bool | ✓ | ✓ | — | Indicator | Separation timer active during LEOP |
| 0x0128 | sep_timer_remaining | s | ✓ | ✓ | — | Counter | Separation timer countdown (seconds) |
| 0x0129 | pdm_unsw_status | bitmask | ✓ | ✓ | — | Status | PDM unswitchable line status bitmask |
| 0x012A | sc_phase | enum | ✓ | ✓ | S8 SET_PHASE | Enum | Spacecraft mission phase |
| 0x012B–0x0130 | panel_px/mx/py/my/pz/mz_current | A | ✓ | ✗ **MISSING** | — | None | Per-panel solar current; should be in HK |
| 0x0131 | power_line_bitmask | bitmask | ✓ | ✓ | — | Status | Composite on/off status of all 8 lines |
| 0x0132 | charge_rate_override | A | ✓ | ✓ | S8 set_charge_rate | Text Input | Requested charge rate; NOT enforced in model (BUG) |
| 0x0133 | sada_drive_angle | deg | ✓ | ✓ | S8 set_solar_array_drive | Slider | Solar array drive angle; no SADA status feedback |
| 0x0134 | load_shed_stage | enum | ✓ | ✓ | S8 emergency_load_shed | Indicator | Load shedding stage (0–3) |
| 0x0135 | power_margin | W | ✓ | ✓ | — | Gauge | Power generation - consumption margin |
| 0x0136 | battery_health | % | ✓ | ✓ | — | Gauge | Battery health estimate (naive calculation) |
| 0x0137 | eps_mode | enum | ✓ | ✓ | S8 set_eps_mode | Enum | EPS mode (0=nominal, 1=safe, 2=emergency) |
| 0x0138 | battery_heater_on | bool | ✓ | ✓ | S8 set_battery_heater | Toggle | Battery heater enabled state |
| 0x0139 | battery_heater_setpoint | °C | ✓ | ✓ | S8 set_battery_heater | Text Input | Battery heater temperature setpoint |

**Legend:**
- ✓ = Implemented in model
- ✗ = Missing
- **Commandable (S8/S20)?** = PUS service/subservice for command
- **MCS Widget?** = Display widget in MCS (Gauge, Toggle, Indicator, Counter, Text Input, Slider, Enum, or None)

**Issues Summary:**
- **0x012B–0x0130 (per-panel currents):** Written to shared_params but NOT in HK SID 1; MCS cannot display
- **0x0120 (bat_dod):** Not updated; should be `100 - soc`
- **0x0121 (bat_cycles):** Never incremented
- **0x0132 (charge_rate_override):** Command accepted but not enforced; no feedback
- **0x0133 (sada_drive_angle):** Command accepted but no position feedback or slew-rate modeling
- **0x0110–0x0117 (power line status):** MCS does not distinguish switchable from non-switchable

---

## Summary

### Operability Assessment

**Overall Status:** EPS simulator provides **solid core functionality** for nominal operations and basic FDIR, but has **critical bugs** preventing model execution and **significant gaps** in battery management, operator visibility, and advanced power control.

**Strengths:**
- Battery SoC, voltage, temperature monitoring and eclipse simulation mature
- Power line switchable/non-switchable architecture well-modeled
- Overcurrent protection and load shedding logic correctly implemented
- LEOP-007 and COM-001 procedures well-aligned with simulator features
- Event detection (eclipse, SoC transitions, OC trips) functional

**Critical Gaps:**
1. **Duplicate variable reference crash** (Defect #1) blocks simulator execution
2. **Battery DoD and cycle tracking broken** (Defect #3) — no battery aging or deep-discharge protection
3. **Charge rate command not enforced** (Defect #4) — operator cannot control battery lifecycle
4. **Per-panel solar telemetry unavailable** (Defect #2) — slow FDIR for solar array failures
5. **MCS UI ambiguity** (Defect #5) — non-switchable lines not visually distinguished

**ECSS Compliance Issues:**
- Per ECSS-E-ST-20C, battery state-of-health (DoD, cycles) and charge controller commands are mandatory for spacecraft >3-year life
- Per ECSS-E-ST-20-20C, power source (solar array) and load (per-subsystem) telemetry must be disaggregated to enable FDIR
- Per ECSS-E-ST-20C, all switchable protection devices (LCL, FCL, PDM) must have status visibility and operator reset capability

**Recommended Next Steps:**
1. **Immediate (Blocking LEOP):** Fix NameError crash (Defect #1)
2. **Pre-Commissioning (Blocking COM-001):** Implement DoD/cycle tracking (Defect #3), charge rate enforcement (Defect #4)
3. **Before FDIR Validation:** Add per-panel telemetry (Defect #2), improve MCS UI (Defect #5)
4. **Mission Operations Enhancement:** Implement battery thermal safing, MPPT control, watchdog visibility (Category 3 items 4.2, 3.2, 4.2)

---

**End of Review**


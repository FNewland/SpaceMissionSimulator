# Scenario Parameter Inventory (Review Item 7)

Repo root referenced below as `M = SpaceMissionSimulation/`. All line numbers verified 2026-07-28.
Goal audited: "all simulation scenario parameters clearly in a configuration file for ease of editing."

---

## 1. Where the scenario engine lives

| Component | Path |
|---|---|
| Runtime scenario engine (the one actually used) | `M/packages/smo-simulator/src/smo_simulator/scenario_engine.py` (292 lines) |
| Loaded from | `SimulationEngine.__init__` → `load_scenarios_from_dir(self.config_dir / "scenarios")` — `M/packages/smo-simulator/src/smo_simulator/engine.py:264-271` |
| Scenario YAML files (31) | `M/configs/eosat1/scenarios/*.yaml` |
| Duplicate/unused pydantic schema | `ScenarioConfig` / `ScenarioEventConfig` in `M/packages/smo-common/src/smo_common/config/schemas.py:218-230`, loader `load_scenarios()` at `config/loader.py:123` — **no caller found in the simulator**; the runtime engine does its own `yaml.safe_load` with no validation |
| Failure injection backend | `M/packages/smo-simulator/src/smo_simulator/failure_manager.py` → per-model `inject_failure()` |

## 2. Current scenario YAML schema (as parsed by the loader)

Parsing is `scenario_engine.py:66-93` (`load_scenarios_from_dir`). There is **no validation**: unknown top-level keys are silently ignored; a failed file is skipped with a log warning.

### Top-level keys

| Key | Type | Default | Notes |
|---|---|---|---|
| `name` | str | file stem | Also the dict key — two files with same `name` overwrite each other |
| `difficulty` | str | `"BASIC"` | Free text, not validated (files use BASIC/BEGINNER/INTERMEDIATE/ADVANCED) |
| `duration_s` | float | `1800.0` | Scenario auto-finishes when `elapsed >= duration_s` (`tick`, line 195-196) |
| `briefing` | str | `""` | Shown in UI |
| `events` | list | `[]` | See below |
| `expected_responses` | list[dict] | `[]` | Only the *count* is used for scoring (`_build_debrief`, line 279: `score = completed/max(expected,1)*100`); `category`/`description` free text. Response categories `detect`/`isolate`/`recover` map to MTTD/MTTI/MTTR |

### Event keys (`ScenarioEvent`, lines 17-23, parsed lines 73-79)

| Key | Type | Default | Semantics |
|---|---|---|---|
| `time_offset_s` | float\|None | None | Fires when scenario elapsed ≥ value |
| `condition` | str\|None | None | `"<param_id> <op> <value>"`, ops `< > == <= >=`; param resolved by **decimal string match against numeric param ids** (`_eval_condition`, lines 246-268); `==` tolerance hard-coded `0.01` |
| `action` | str | `""` | One of the 4 supported actions below |
| `params` | dict | `{}` | Action-specific |

### Supported actions (`_fire_event`, lines 198-244)

| Action | params | Backend |
|---|---|---|
| `inject` | `subsystem`, `failure`, `magnitude` (def 1.0), `onset` (def `step`; `gradual`/`intermittent`), `duration_s` (def 0 = indefinite), plus pass-through extras (`onset_duration_s`, `wheel`, `array`, `face`, `line_index`, `circuit`, …) | `FailureManager.inject` |
| `clear` | `failure_id` (empty → clear_all) | `FailureManager.clear` |
| `set_parameter` | `param_id` (numeric or hex-string), `value`; symbolic `parameter:` names **rejected with a warning** (no name→id registry) | writes `engine.params[pid]` directly |
| `message` | `text` | log only |

Anything else → `logger.warning("Scenario: unknown action …")` — event does nothing.

### Schema keys used in the 31 YAMLs that the loader does NOT support (silently dead config)

| File(s) | Dead key/action | Effect |
|---|---|---|
| `first_contact.yaml`, `separation_sequence.yaml`, `sequential_checkout.yaml` | top-level `initial_conditions:` (e.g. `spacecraft_phase: 0`, `speed: 10`) | **Ignored entirely** — no initial-condition mechanism exists in the loader; instructor must set phase/speed manually |
| `separation_sequence.yaml` | events use `time_s:` / `type: instructor` instead of `time_offset_s`, and actions `start_separation`, `set_speed` | `time_offset_s` parses as None and no condition → event never fires; actions unimplemented anyway |
| `gs_rf_degradation.yaml`, `gs_tracking_loss.yaml`, `gs_antenna_failure.yaml` | `action: ground_failure` (3 events) | Unknown action → warning only. Ground-segment failures actually live in the separate RF sim (`packages/smo-rfsim`, `configs/eosat1/rfsim.yaml`) |
| several | per-event `note:` | Harmless annotation, ignored |

**Actions used across all 31 files:** inject ×44, set_parameter ×5, message ×2, ground_failure ×3 (dead), set_speed ×1 (dead), start_separation ×1 (dead). Only 2 events use `condition:`.

## 3. Scenario-relevant values HARD-CODED in Python

Format: value — location — proposed config key (proposed file `configs/eosat1/simulation.yaml` unless noted).

### 3.1 Engine timing / phases / speed (`engine.py`)

| Value | Location | Proposed key |
|---|---|---|
| Tick rate `tick_hz = 1.0` (dt_real = 1 s) — **ignores `mission.yaml network.sim_tick_hz`** which exists in schema (`smo_common/config/schemas.py:32`) | `engine.py:823` (`_run_loop`) | wire up `mission.network.sim_tick_hz` |
| `dt_sim = dt_real * self.speed` (speed multiplies sim-seconds per tick; **no upper clamp on set_speed**) | `engine.py:828`, `set_speed` handler `engine.py:1755-1756` | `simulation.speed_max`, `simulation.speed_default` |
| Separation timer duration `1800.0` s (30 min) | `engine.py:232` (`_sep_timer_duration`) | `phases.separation_timer_s` |
| Auto-TX hold-down `15*60` s (TTC TX force-on after any accepted TC) | `engine.py:236` (`_auto_tx_hold_s`) | `ttc.auto_tx_hold_s` |
| Spacecraft phase enum & default phase 6 / bootloader start phase 3 | `engine.py:46` (`SC_PHASE_NOMINAL=6`), `engine.py:230`, `_enter_bootloader_mode` `engine.py:303` | `phases.*` (names, initial phase) |
| Phase→active-subsystem gating (`_ALWAYS_ON = {"eps","ttc","obdh"}`, TCS from phase 2, all from phase 4) | `engine.py:863-871` | `phases.subsystem_gating` |
| Bootloader beacon SID (single SID 11) | `engine.py:28-34` (`BOOTLOADER_BEACON_SID`) | `mission.bootloader_beacon_sid` |

### 3.2 Scenario engine defaults (`scenario_engine.py`)

| Value | Location | Proposed key |
|---|---|---|
| Default `duration_s = 1800.0`, difficulty `BASIC` | lines 29-30, 82-83 | fine as schema defaults, but should be validated |
| Score = completed/expected ×100, capped 100 | lines 277-281 | `scoring.*` if instructors need weighting |
| Condition `==` tolerance `0.01` | line 260 | `scenario_defaults.eq_tolerance` |

### 3.3 Failure manager (`failure_manager.py`)

| Value | Location | Proposed key |
|---|---|---|
| Default gradual `onset_duration_s = 90.0` | line 43 | `failures.default_onset_duration_s` |
| Intermittent first flicker `uniform(2, 6)` s | line 66 | `failures.intermittent.first_flicker_s` |
| Intermittent ON period `uniform(2.0, 8.0)` s / OFF `uniform(5.0, 20.0)` s | lines 118-122 | `failures.intermittent.on_s` / `.off_s` |

### 3.4 EPS model (`models/eps_basic.py`) — scenario-critical magnitudes

| Value | Location | Proposed key |
|---|---|---|
| `POWER_LINE_DEFS` table (names, switchability, default states, nominal W) — default states are overridable via `eps.yaml power_lines:` but the W column and switchability are not | lines 31-41 | `eps.power_lines[]` (full table) |
| `OC_THRESHOLDS` per line (A) + `OC_TRIP_TIME_S = 0.05` | lines 48-53 | `eps.overcurrent.*` |
| `LOAD_SHED_ORDER = ["payload","fpa_cooler","ttc_tx","aocs_wheels"]` | line 56 | `eps.load_shed.order` |
| `LOAD_SHED_VOLTAGE = 26.5` V | line 57 | `eps.load_shed.bus_voltage_v` |
| UV/OV thresholds 26.5 / 29.5 V (code defaults; **not present in eps.yaml**) | lines 176-178 | `eps.uv_threshold_v`, `eps.ov_threshold_v` |
| `ttc_rx` line power hard-coded `5.0` W — **ignores config `transponder_rx_power_w`** which is loaded into `_transponder_rx_w` but never used | line 351 vs 193 | use existing config key |
| `aocs_wheels` line power hard-coded `6.0` W (overrides the 12 W in POWER_LINE_DEFS) | line 385 | `eps.aocs_wheels_power_w` |
| `htr_bat` 6.0 W / `htr_obc` 4.0 W line powers (duplicate TCS `heaters[].power_w`) | lines 378-379 | derive from `tcs.yaml heaters` |
| `platform_idle_power_w` (eps.yaml: 70) loaded but **never used** in consumption | line 188 | delete or use |
| Bus-short extra load `80.0` W | line 417 | `eps.failure_magnitudes.bus_short_w` |
| Battery cell failure ΔV `−3.7` V | line 464 | `eps.failure_magnitudes.cell_fail_dv` |
| `undervoltage` inject = instant SoC drop of `magnitude × 10` % | line 883 | `eps.failure_magnitudes.undervoltage_soc_per_mag` |
| Event thresholds: SoC 10/20/99 %, bus 25/27 V, bat temp 45/−5 °C, SA-degraded 70 % of expected | lines 498-556 | `eps.event_thresholds.*` |
| Solar aging `3.14e-6`/sunlit-h, floor 0.5; MPPT eff 0.97; initial SoC 75 %, initial bat temp 15 °C | lines 264-268, 120, 63, 66 | `eps.solar_aging.*`, `eps.mppt_efficiency`, `eps.initial.soc_pct` (⚠ `separation.yaml` briefing promises 95 % SoC — not achievable via config today) |
| EPS-mode shed counts (safe→2 loads, emergency→all) | lines 586-590 | `eps.mode_shed_counts` |
| Battery thermal `tau 600 s`, env 15 °C, heat divisor 30.0 | lines 168-169, 494-495 | `eps.battery_thermal.*` |
| Telemetry noise sigmas (gen 0.005/0.0025, cons 1.0 W, current 0.1 A, voltages 0.02/0.01) | lines 290, 313, 418, 456, 465-471 | `eps.noise.*` |

### 3.5 FDIR / load-shedding duplication (`fdir.py`, `engine.py`, YAML)

| Value | Location | Issue / proposed key |
|---|---|---|
| SoC shed stages 30/20/10 % hard-coded in `LoadSheddingManager.get_required_stage` | `fdir.py:155-163` | **Duplicates** `fault_propagation.yaml load_shedding_stages[*].soc_threshold` (30/20/10), which is loaded but only its `subsystem_commands` are used — thresholds come from code. Move to config, single source |
| `_tick_fdir_advanced` reads `getattr(eps._state, 'soc', 100.0)` — attribute is `bat_soc_pct`, so **SoC always reads 100.0 and YAML-configured load-shed stages can never trigger** | `engine.py:618` | bug; fix + config |
| Recovery `max_attempts = 3` | `fdir.py:57, 115` | `fault_propagation.yaml recovery_escalation.max_attempts` exists but is not wired |
| Default `safe_mode_entry` procedure inline | `engine.py:605-613` | move to `fdir/procedures/` |

### 3.6 TCS model (`models/tcs_basic.py`)

Config (`subsystems/tcs.yaml`) covers zones, heaters, fpa target, coupling and event thresholds — but the code only reads `zones`, `heaters`, `fpa_cooler_target_c`, `param_ids` (`configure`, lines 160-198). Hard-coded despite YAML sections existing:

| Value | Location | Note |
|---|---|---|
| `thermal_coupling.zone_conductance` and `space_radiation_g` YAML section **not read** — code uses literals 0.5/0.8/0.4 and 0.2 W/K | lines 135-146 vs `tcs.yaml` | wire up existing YAML |
| `event_thresholds` YAML section **not read** — warning/alarm limits and 2.0 °C/min runaway rate are code literals | lines 148-158 | wire up existing YAML |
| Eclipse/sun environment temps: eclipse `(−30, 10)` °C, sunlit `−10 + 50·|cosβ|` | lines 384-389 | `tcs.environment.*` |
| Panel solar-heating scale (30 % of solar power ×10 gain) | lines 400-404 | `tcs.panel_solar_heating.*` |
| Heater powers/thermostats/τ/capacitance class defaults (battery 6 W (5,1 °C), obc 4 W (10,5), thruster 8 W) | lines 110-122 | already YAML-overridable — OK |

### 3.7 AOCS / orbit / TTC / payload (selected)

| Value | Location | Proposed key |
|---|---|---|
| Orbit-phase period `5700` s used for CSS sun geometry (TLE says 5669 s; Kepler for 450 km says 5607 s) | `models/aocs_basic.py:1108-1110` | derive from orbit config |
| Eclipse penumbra width `50` km | `smo_common/orbit/eclipse.py:41` | `orbit.penumbra_km` |
| Contact scan window 7200 s / 30 s step; eclipse scan 7200 s / 60 s | `orbit/propagator.py:93-95, 175-176` | `orbit.scan.*` |
| ST blinding sun-exclusion, wheel friction, detumble rates, momentum limits (many literals) | `models/aocs_basic.py` throughout | `aocs.*` (larger refactor) |
| TTC PA nominal/max power 2.0/5.0 W (config keys `pa_nominal_power_w`/`pa_max_power_w` exist with code defaults; **not present in ttc.yaml**) | `models/ttc_basic.py:141-142, 192-193` | add to `ttc.yaml` |
| PA overheat inject default heat 20 W | `ttc_basic.py:954` | `ttc.failure_magnitudes.pa_heat_w` |

## 4. Scenario-adjacent config scattered elsewhere (current map)

| File | Contents relevant to scenarios |
|---|---|
| `configs/eosat1/mission.yaml` | APIDs, bootloader start flag, time source, network ports, `sim_tick_hz` (unused) |
| `configs/eosat1/orbit.yaml` | TLE, T-0 epoch, ground stations, min elevation |
| `configs/eosat1/subsystems/*.yaml` (8 files) | model params + `param_ids` (the ids scenario `condition:`/`set_parameter` need — but only as decimal/hex numbers) |
| `configs/eosat1/subsystems/fdir.yaml` | 20+ threshold rules (SoC 20/15, bus 26/24 V, temps, reboot count…) |
| `configs/eosat1/fdir/fault_propagation.yaml` | fault cascades, load-shed stages (partially dead, §3.5), recovery escalation (dead) |
| `configs/eosat1/fdir/procedures/*.yaml` | FDIR auto procedures |
| `configs/eosat1/monitoring/s12_definitions.yaml` | 25+ limit-check rules (independent thresholds duplicating FDIR/eps event values) |
| `configs/eosat1/monitoring/s19_rules.yaml` | event→action autonomy rules |
| `configs/eosat1/events/event_catalog.yaml` | 105 event ids/severities |
| `configs/eosat1/rfsim.yaml` | ground-segment failure modes (the missing `ground_failure` backend) |
| `configs/eosat1/telemetry/`, `commands/`, `procedures/`, `planning/`, `aocs/`, `smdl/`, `mcs/` | HK structures/intervals, TC catalog, operator procedures, planner |

## 5. Proposed consolidated layout (design only)

Keep per-subsystem physics in `subsystems/`, but (a) make every literal above a config key, (b) give scenarios a validated, self-sufficient schema:

```
configs/eosat1/
  simulation.yaml            # NEW: tick_hz, speed_default/max, phases (names, sep timer,
                             #      subsystem gating), failure-manager onset defaults
  scenario_defaults.yaml     # NEW: default duration, scoring weights, eq tolerance
  scenarios/<name>.yaml      # EXTENDED schema:
    name / difficulty / duration_s / briefing        (as today, validated enum for difficulty)
    initial_conditions:      # NEW – honoured by engine on scenario start
      spacecraft_phase: 0
      speed: 10
      params: {eps.bat_soc: 95.0}      # symbolic names resolved via subsystems/*.yaml param_ids
      power_lines: {aocs_wheels: true}
    events:
      - at: 300              # alias of time_offset_s
        when: "eps.bat_soc < 55"       # symbolic-name conditions
        action: inject|clear|set_parameter|message|ground_failure|set_speed|set_phase
        params: {...}
    expected_responses: [...]
  subsystems/*.yaml          # absorb §3.4/3.6/3.7 literals (uv/ov, load-shed order+voltage,
                             #  per-line W incl. ttc_rx/aocs_wheels, event thresholds,
                             #  failure magnitudes, noise, environment temps)
  fdir/fault_propagation.yaml  # becomes the single source for SoC shed stages (fix engine.py:618)
```

Migration priorities: (1) fix the three broken scenario files (dead keys, §2) or implement `initial_conditions` + missing actions; (2) wire `sim_tick_hz`; (3) EPS literals (`ttc_rx` 5 W, wheels 6 W, LOAD_SHED_VOLTAGE, UV/OV); (4) de-duplicate SoC shed thresholds; (5) TCS dead YAML sections.

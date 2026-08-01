## Summary

The review goal "all simulation scenario parameters clearly in a configuration
file for ease of editing" was not met: the parameters that define how a
training session behaves were Python literals spread across five modules, with
no configuration home at all. An instructor could not change the separation
timer, the load-shed levels, the battery starting charge, the thermal
environment or the failure-onset timing without editing source.

Audited inventory (`/home/claude/work/scenario_config_inventory.md`):

| Area | Examples of hard-coded values |
|---|---|
| `engine.py` | tick rate 1.0 Hz (ignoring `mission.yaml network.sim_tick_hz`), separation timer 1800 s, auto-TX hold 900 s, no clamp on `set_speed` |
| `models/eps_basic.py` | `LOAD_SHED_VOLTAGE` 26.5 V, `LOAD_SHED_ORDER`, `OC_THRESHOLDS`, UV/OV 26.5/29.5 V, initial SoC 75 %, `ttc_rx` 5 W (ignoring the `transponder_rx_power_w` config key), `htr_bat`/`htr_obc`/`aocs_wheels` line loads, all EPS event thresholds (SoC 10/20/99 %, bus 25/27 V, battery ±temp, array-degraded 70 %) |
| `models/tcs_basic.py` | eclipse/sunlit environment temperatures, panel solar-heating scaling, zone conductances, warning/alarm limits, thermal-runaway rate |
| `fdir.py` | SoC shed ladder 30/20/10 % (duplicating `fault_propagation.yaml`) |
| `failure_manager.py` | gradual onset 90 s, intermittent flicker ranges |
| `scenario_engine.py` | default duration 1800 s, difficulty default, `==` tolerance 0.01 |

Worse, four configuration sections that DID exist were silently discarded
because their pydantic schemas did not declare them, so the values never
reached the models at all:

* `eps.yaml solar_panels` (body-panel and deployable-wing areas) — the wings
  therefore always used the 0.24 m² code default;
* `eps.yaml power_lines` (initial line states);
* `tcs.yaml thermal_coupling` and `tcs.yaml event_thresholds` — the reason
  these read as "never used" in earlier audits;
* `mission.yaml network` (ports and `sim_tick_hz`).

## Severity

**Major** — a stated system requirement was unmet, and four YAML sections were
inert config that looked authoritative to anyone reading the mission files.

## Status

**Fixed** — new `configs/eosat1/simulation.yaml` (48 keys) plus schema repairs:

1. **New schema + loader.** `SimulationConfig` (with `SimSpeedConfig`,
   `SimPhaseConfig`, `SimTTCConfig`, `SimEPSConfig` + load-shed/overcurrent/
   event-threshold sub-models, `SimTCSConfig`, `SimFailureConfig`,
   `SimScenarioDefaultsConfig`) in `smo_common/config/schemas.py`, loaded by
   `load_simulation_config()` following the `load_fdir_config` precedent.
   **Every field defaults to the previous hard-coded value**, and a missing
   file returns an all-defaults object, so config directories without
   `simulation.yaml` (including minimal test fixtures) behave exactly as before.
2. **Engine.** Tick rate now honours `simulation.yaml tick_hz`, else
   `mission.yaml network.sim_tick_hz`, else 1 Hz; separation timer, auto-TX
   hold and a new `set_speed` clamp come from config; the subsystem `eps:`/
   `tcs:` blocks are merged into each model's config dict under a reserved
   `simulation` key before `create_model`.
3. **EPS model.** Load-shed order/voltage/hysteresis, overcurrent thresholds
   and trip time, UV/OV, initial SoC and battery temperature, the
   `htr_bat`/`htr_obc`/`aocs_wheels` line loads, and all eight event
   thresholds are read from config with the module constants as fallback. The
   `ttc_rx` line now bills the configured `transponder_rx_power_w` instead of a
   hard-coded 5.0 W.
4. **TCS model.** `tcs.yaml thermal_coupling` (zone conductances, space
   radiation) and `event_thresholds` (warning/alarm limits, runaway rate) are
   now read — the model already had matching attributes, so they were wired in
   rather than deleted — and the thermal environment (eclipse/sunlit sink
   temperatures, panel solar-heating fraction and gain) comes from
   `simulation.yaml tcs.environment`.
5. **FDIR / failure manager / scenario engine.** SoC shed ladder, gradual and
   intermittent onset timing, and scenario defaults (duration, difficulty
   vocabulary, `==` tolerance) are configuration-driven.
6. **Schemas repaired** so existing YAML stops being dropped: `EPSConfig`
   gains `solar_panels`, `power_lines`, `obc_power_w`; `TCSConfig` gains
   `thermal_coupling`, `event_thresholds`; `MissionConfig` gains `network`.
7. **Documented.** Header comments in `simulation.yaml` state file ownership
   and precedence; `docs/SIMULATION_CONFIG.md` gives the full key → meaning →
   default → effect table plus the scenario YAML schema.

Ownership is explicit and non-duplicating: `simulation.yaml` owns simulation
behaviour, `subsystems/*.yaml` owns hardware ratings, `mission.yaml` owns
identity/ports, `orbit.yaml` owns geometry. Where two files necessarily
describe the same ladder (SoC shed stages vs `fault_propagation.yaml`),
`simulation.yaml` wins and the engine warns on divergence.

## Acceptance criteria

- [x] Every parameter listed in the inventory's §3.1–§3.6 priority set has a
      configuration key.
- [x] Defaults preserve pre-existing behaviour; a config dir without
      `simulation.yaml` is unchanged.
- [x] `eps.yaml solar_panels` / `power_lines`, `tcs.yaml thermal_coupling` /
      `event_thresholds` and `mission.yaml network` actually reach their
      consumers.
- [x] No key is defined in two files without an explicit precedence rule and a
      divergence warning.
- [x] Documented in the file header and `docs/SIMULATION_CONFIG.md`.

## Affected areas

- `configs/eosat1/simulation.yaml` (new), `configs/eosat1/subsystems/eps.yaml`
  (cross-reference comments), `docs/SIMULATION_CONFIG.md` (new)
- `packages/smo-common/src/smo_common/config/schemas.py`, `.../loader.py`
- `packages/smo-simulator/src/smo_simulator/engine.py` (config load, tick rate,
  phase timing, auto-TX hold, speed clamp, subsystem config merge, shed ladder)
- `packages/smo-simulator/src/smo_simulator/models/eps_basic.py`,
  `models/tcs_basic.py`, `fdir.py`, `failure_manager.py`, `scenario_engine.py`
- `tests/test_simulator/test_simulation_config.py`

## Related

- Defect #52 (scenario schema silent ignores) — the scenario half of the same
  review item.
- Defects #49 / #50 — the power tuning that this configuration layer carries.
- Defect #42 (ground-station config duplication) — the single-source principle
  applied here.

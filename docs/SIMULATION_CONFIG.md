# Simulation Configuration Reference (`configs/<mission>/simulation.yaml`)

Single, editable home for the **simulation / scenario** parameters that were
previously Python literals in `engine.py`, `models/eps_basic.py`,
`models/tcs_basic.py`, `fdir.py` and `failure_manager.py` (defect #51).

The file is optional: `load_simulation_config()` returns an all-defaults
`SimulationConfig` when it is missing, and every default reproduces the value
the simulator used before the file existed. Deleting `simulation.yaml` therefore
changes nothing except the item-9 power tuning captured in it.

Schema: `SimulationConfig` in
`packages/smo-common/src/smo_common/config/schemas.py`.
Loader: `load_simulation_config()` in `.../config/loader.py`.

## Which file owns what

| File | Owns |
|---|---|
| `simulation.yaml` | Sim behaviour: tick rate, speed limits, phase timing, protection ladders, initial conditions, thermal environment, failure-onset timing, scenario defaults |
| `subsystems/*.yaml` | **Hardware**: array areas, battery capacity, equipment power ratings, thermal zones/heaters, param ids |
| `mission.yaml` | Identity, APIDs, ports, `network.sim_tick_hz` |
| `orbit.yaml` | TLE, T-0 epoch, canonical ground stations |
| `fdir/`, `monitoring/` | FDIR rules, fault cascades, limit checks, autonomy rules |

No key is defined in two files. Where two files necessarily describe the same
ladder (`simulation.yaml eps.load_shed.soc_stage_thresholds_pct` vs
`fdir/fault_propagation.yaml` stage `soc_threshold`), `simulation.yaml` wins and
the engine logs a warning if the two diverge.

## Key reference

| Key | Meaning | Default | Effect when changed |
|---|---|---|---|
| `tick_hz` | Main-loop rate; `null` inherits `mission.yaml network.sim_tick_hz` | `null` (→1.0 Hz) | Wall-clock resolution of the sim loop; `dt_sim = speed / tick_hz` |
| `speed.default` | Startup time acceleration | 1.0 | Initial `engine.speed` |
| `speed.max` | Clamp applied to every `set_speed` (UI + scenarios) | 1000.0 | Prevents an absurd `dt_sim` stalling the loop |
| `phases.separation_timer_s` | PRE_SEPARATION → INITIAL_POWER_ON countdown | 1800.0 | Length of the LEOP separation wait |
| `ttc.auto_tx_hold_s` | TX line forced ON after any accepted TC (re-armed per TC) | 900.0 (**EOSAT-1: 300**) | Longer hold = more TX energy per pass and defeats the `ttc_tx` shed stage during contacts |
| `eps.initial.soc_pct` | Battery SoC at engine start | 75.0 (**EOSAT-1: 90**) | Starting energy margin; 75 % started inside the old shed band |
| `eps.initial.bat_temp_c` | Battery temperature at start (also the thermal environment) | 15.0 | Battery thermal transient |
| `eps.line_power_w.<line>` | Wattage billed for lines with no rating in `eps.yaml` (`htr_bat`, `htr_obc`, `aocs_wheels`) | 6/4/6 W (**EOSAT-1: 8/4/12**) | Platform load; `aocs_wheels` covers the whole AOCS suite (Phase B, sized for Phase C's 4/9/15 W modes) |
| `eps.heater_duty_from_tcs` | Bill heater lines from the ACTUAL TCS heater state (0x040A/0x040B) instead of 100 % duty while the line is ON | false (**EOSAT-1: true**) | Phase B (brief 01 WP-5): removes the ~8 W phantom heater bill |
| `eps.uv_threshold_v` / `ov_threshold_v` | Under/over-voltage flags | 26.5 / 29.5 | `uv_flag`/`ov_flag` telemetry and FDIR inputs |
| `eps.load_shed.order` | Bus-voltage shed priority (first = shed first) | payload, fpa_cooler, ttc_tx, aocs_wheels | Which load dies first in a brown-out |
| `eps.load_shed.bus_voltage_v` | Bus voltage at which the model sheds loads | 26.5 (**EOSAT-1: 25.0**) | 26.5 V ≈ 65 % SoC (inside the normal eclipse band → shed every orbit); 25.0 V ≈ 45 % SoC |
| `eps.load_shed.restore_hysteresis_v` | Restore margin above the shed voltage | 0.5 | Anti-chatter band |
| `eps.load_shed.auto_restore` | Restore shed lines (one per tick, reverse shed order) once the bus recovers above threshold + hysteresis | false (**EOSAT-1: true**) | Phase B (brief 01 WP-7): the documented restore now exists |
| `eps.load_shed.soc_stage_thresholds_pct` | FDIR shed stage 1/2/3 SoC entry levels | [30, 20, 10] | Canonical source for `LoadSheddingManager`; cross-checked against `fault_propagation.yaml` |
| `eps.overcurrent.thresholds_a.<line>` | Per-line trip current | 150 % of nominal at 28 V | Overcurrent trips and `oc_trip_flags` telemetry |
| `eps.overcurrent.trip_time_s` | Trip delay (immediate at 1 Hz) | 0.05 | Reserved for sub-second ticks |
| `eps.event_thresholds.soc_low_pct` / `soc_critical_pct` | S5 low/critical SoC events | 20 / 10 | Operator warning points |
| `eps.event_thresholds.soc_charge_complete_pct` | Charge-complete event | 99.0 | — |
| `eps.event_thresholds.bus_warning_v` / `bus_critical_v` | Bus undervoltage events | 27.0 / 25.0 | — |
| `eps.event_thresholds.bat_overtemp_c` / `bat_undertemp_c` | Battery temperature events | 45 / −5 | — |
| `eps.event_thresholds.sa_degraded_fraction` | Array-degraded event trigger (actual < fraction × expected) | 0.7 | Sensitivity of array-degradation detection |
| `tcs.environment.eclipse_external_c` / `eclipse_internal_c` | Eclipse sink temperatures | −30 / 10 | Zone cool-down rate in eclipse |
| `tcs.environment.sunlit_external_base_c` / `sunlit_external_beta_gain_c` | Sunlit env = base + gain·\|sin β\| (Phase B: was \|cos β\|, inverted) | −10 / 50 | Panel temperatures vs beta angle — orbit-average sink rises with \|β\| |
| `tcs.environment.sunlit_internal_c` | Sunlit internal sink | 12.0 | OBC zone equilibrium |
| `tcs.environment.eclipse_battery_c` / `sunlit_battery_c` | Battery-zone internal sink (Phase B, brief 05 WP-3) | inherit internal (**EOSAT-1: −8 / 0**) | Makes the battery thermostat genuinely cycle; the pack sits against the radiator side |
| `tcs.environment.panel_solar_heat_fraction` / `panel_solar_heat_gain` | Share of panel solar power becoming heat; gain is a TRIM multiplier on the physical 1/UA per node (Phase B: the old 10.0 was an ~80× error) | 0.3 / 1.0 | Panel temperature swing under illumination |
| `failures.default_onset_duration_s` | `onset: gradual` ramp time | 90.0 | How fast injected failures reach full magnitude |
| `failures.intermittent.first_flicker_s` / `on_s` / `off_s` | Intermittent-failure timing ranges | [2,6] / [2,8] / [5,20] | Flicker cadence of intermittent injects |
| `scenario_defaults.default_duration_s` | Scenario duration when the YAML omits it | 1800.0 | Auto-stop point |
| `scenario_defaults.default_difficulty` / `valid_difficulties` | Difficulty default and accepted values | BASIC / BASIC, BEGINNER, INTERMEDIATE, ADVANCED | Load-time validation warning |
| `scenario_defaults.eq_tolerance` | Tolerance of `==` in scenario `condition:` | 0.01 | Condition sensitivity |

## Scenario YAML schema (validated at load — defect #52)

Top level: `name`, `difficulty`, `duration_s`, `briefing`, `initial_conditions`,
`events`, `expected_responses`.

```yaml
initial_conditions:      # applied by the scenario engine on start()
  spacecraft_phase: 0    # 0..6, routed through the set_phase command
  speed: 10              # clamped to speed.max
  override_passes: true  # force RF contact
  soc_pct: 95.0          # battery SoC
  power_lines: {aocs_wheels: true}
  params: {0x0101: 95.0} # raw parameter pre-sets
  failures:              # pre-injected failures
    - {subsystem: eps, failure: bus_short, magnitude: 1.0}

events:
  - time_offset_s: 300   # or `condition: "<param_id> <op> <value>"`
    action: inject | clear | set_parameter | message | set_speed | set_phase
    params: {...}
```

Load-time validation rejects (skips, and records a `SCENARIO_CONFIG_INVALID`
event 0x0F20) any event that:

* uses an action outside the supported set;
* injects a failure mode the target subsystem model does not implement
  (the registry is introspected from the model's own `inject_failure`);
* has neither `time_offset_s` nor `condition` (it could never fire).

Ground-segment failures (`ground_failure`) are **not** simulator failures: the
ground station RF chain is modelled in `packages/smo-rfsim` and injected from
the Radio UI, so the affected scenarios carry instructor-cue `message` events
instead.

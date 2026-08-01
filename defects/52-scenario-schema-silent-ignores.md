## Summary

The scenario loader (`scenario_engine.py::load_scenarios_from_dir`) performed
no validation: it copied whatever the YAML contained into `ScenarioEvent`
objects. Anything the runtime did not implement was therefore accepted at load
time and then quietly did nothing — the instructor saw a scenario in the list,
started it, and part of it simply never happened.

Concrete dead configuration in the 31 shipped scenario files:

| File(s) | Dead content | Effect |
|---|---|---|
| `gs_rf_degradation.yaml` (×2), `gs_tracking_loss.yaml` | `action: ground_failure` | Unknown action → a log line at fire time; ground-segment failures actually live in the RF simulator (`packages/smo-rfsim`), which the scenario engine cannot drive |
| `separation_sequence.yaml` | `action: start_separation`, plus `time_s:` / `type: instructor` / `value:` keys the loader does not read | The first event referenced an action that never existed; the second parsed with `time_offset_s = None` and no condition, so it could never fire at all |
| `first_contact.yaml`, `separation_sequence.yaml`, `sequential_checkout.yaml` | top-level `initial_conditions:` (phase, speed, override_passes) | Parsed by nothing — scenarios that are supposed to begin in PRE_SEPARATION or BOOTLOADER_OPS with forced contact started in whatever state the sim happened to be in |

There was also no protection against a mistyped failure mode: an
`action: inject` naming a failure the target model does not implement loaded
happily and produced nothing at run time, and duplicate scenario `name:` values
silently overwrote each other.

## Severity

**Major** — training scenarios silently under-delivered. A scenario could look
correct in the UI and in review while a third of its events did nothing, and
three scenarios started from the wrong spacecraft state.

## Status

**Fixed** in `packages/smo-simulator/src/smo_simulator/scenario_engine.py`,
`engine.py`, and the affected scenario YAMLs:

1. **Load-time validation.** `SUPPORTED_ACTIONS` is now explicit
   (`inject`, `clear`, `set_parameter`, `message`, `set_speed`, `set_phase`).
   `_validate_event` rejects, and the loader SKIPS, any event that: uses an
   unsupported action; injects a failure mode the target subsystem model does
   not implement; is missing `subsystem`/`failure`; or has neither
   `time_offset_s` nor `condition` (i.e. could never fire). A non-standard
   `difficulty` and a duplicate scenario name are also reported.
2. **Failure-mode registry.** `_known_failures()` introspects the model's own
   `inject_failure` source (`failure == "..."` comparisons) so the registry can
   never drift from the implementation; when a model or its source is
   unavailable the check is skipped rather than producing false rejections.
3. **Issues are surfaced, not swallowed.** Each problem is logged as a warning
   and recorded in `load_issues()`; `emit_load_issues()` emits a
   `SCENARIO_CONFIG_INVALID` (0x0F20, new entry in
   `configs/eosat1/events/event_catalog.yaml`) S5 event per issue. The engine
   calls it once its TM/dispatcher plumbing exists (scenarios load earlier in
   construction).
4. **`set_speed` implemented** — clamped to `simulation.yaml speed.max` — and
   **`set_phase` added**, routing through the existing instructor `set_phase`
   command, which is what actually arms the separation timer.
5. **`initial_conditions:` implemented** in `ScenarioEngine.start()`:
   `spacecraft_phase`, `speed`, `override_passes`, `soc_pct`, `power_lines`,
   `params` (raw parameter pre-sets) and `failures` (pre-injected, tracked so
   they are cleared when the scenario ends). Unknown power lines and bad
   parameter ids are logged.
6. **Scenario YAMLs repaired rather than deleted.**
   `separation_sequence.yaml` now uses `time_offset_s` with
   `action: set_phase (phase 1)` and `action: set_speed`, and its
   `initial_conditions` add the 95 % SoC its briefing promises. The three
   `ground_failure` events became explicit instructor-cue `message` events
   naming the rfsim injection to perform, with a comment explaining that the
   ground segment is a separate simulator.
7. **Engine bug found while wiring this:** the instructor `set_phase` command
   set `_sep_timer` *before* calling `_enter_bootloader_mode()`, which zeroes
   it — so commanding phase 1 left the separation timer at 0 and it expired on
   the next tick. The assignment now happens after, and the timer arms
   correctly at 1800 s.

Scenario defaults (duration, difficulty vocabulary, `==` tolerance) come from
`simulation.yaml scenario_defaults` (defect #51).

## Acceptance criteria

- [x] An unknown action is rejected at load with a recorded issue and an event;
      the scenario's remaining valid events still load.
- [x] An unknown failure mode for a known subsystem is rejected the same way.
- [x] An event that can never fire is rejected.
- [x] All 31 shipped scenarios load with zero issues.
- [x] `initial_conditions` puts the simulator into the declared state
      (phase, speed, override, SoC verified).
- [x] `set_speed` and `set_phase` take effect at run time.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/scenario_engine.py`
  (validation, failure registry, load issues, `set_speed`/`set_phase`,
  `_apply_initial_conditions`, configurable defaults)
- `packages/smo-simulator/src/smo_simulator/engine.py`
  (`emit_load_issues` call, scenario defaults, `set_phase` timer ordering)
- `configs/eosat1/scenarios/separation_sequence.yaml`,
  `gs_rf_degradation.yaml`, `gs_tracking_loss.yaml`
- `configs/eosat1/events/event_catalog.yaml` (0x0F20 SCENARIO_CONFIG_INVALID)
- `docs/SIMULATION_CONFIG.md` (scenario schema section)
- `tests/test_simulator/test_simulation_config.py`
  (`TestScenarioSchemaHardening`)

## Related

- Defect #51 (hard-coded simulation parameters) — same review item; supplies
  the scenario defaults.
- Defect #34 (scenarios never auto-stop) and #9 (scenario engine never
  instantiated) — earlier repairs to the same subsystem.

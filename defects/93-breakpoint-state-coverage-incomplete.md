# 93 — a breakpoint load destroyed most of the spacecraft's state

**Status:** Fixed

## Summary

Breakpoints are the instructor's core tool: freeze a class at an interesting
moment, hand the same moment to everyone, reload after a mistake. The
implementation captured about **12 of the 93 attributes the engine assigns**,
so a load silently discarded most of what made that moment interesting.

## Root cause

`breakpoints.py` (baseline: 167 lines total) enumerated the state to save **by
hand**, one `state[...] = ...` at a time, and the list had never been kept in
step with the engine. What a load destroyed:

| Lost on load | Consequence |
|---|---|
| The entire S11 time-tagged schedule — `TCScheduler` had **no `get_state`/`set_state` at all** | Every command an operator had uplinked for later execution vanished; the schedule report then showed an empty table and the commands never fired |
| `_spacecraft_phase` | The console said "bootloader" while the engine accepted application commands |
| Injected failures | A load in the middle of a failure exercise silently repaired the spacecraft |
| Running scenarios | The scenario clock and its remaining actions were gone |
| S12 enable flags and delta baselines | Monitoring came back in a different configuration from the one saved |
| In-flight S13 large-data transfers | A dump resumed against no transfer |
| `_load_shed_stage` | The EPS came back unshed with the loads still off |
| Every `_prev_*` edge detector | **The first tick after a load re-fired spurious AOS, eclipse and mode-change events** — an event log full of things that had not happened |

The last row is the one that made the defect visible to students: reload a
breakpoint mid-eclipse and the spacecraft announced an eclipse entry it was
already 20 minutes into.

The save was also silently lossy: each model's `get_state()` was wrapped in
`try/except` that logged a warning and continued (`breakpoints.py:36-40`), so a
model that raised produced a breakpoint file that looked fine and restored a
different spacecraft.

## Fix

Hand enumeration replaced by a **`Stateful` protocol plus an engine registry**:

* every stateful component registers itself (`engine._register_stateful`),
  and a registered component **without** `get_state`/`set_state` fails at
  registration rather than being skipped;
* `TCScheduler`, the scenario engine, the failure manager, the S12/S19
  monitoring state, the S13 transfer manager and the FDIR components all gained
  real state methods;
* every `_prev_*` edge detector is part of its owner's state, so the tick after
  a load compares against the state at save;
* a **failed component save aborts the whole save** — a breakpoint file is now
  complete or absent, never quietly partial.

## Tests

`tests/test_simulator/test_breakpoint_round_trip.py`

* `test_save_perturb_load_deep_equals` — save, perturb hard, load, and the
  whole engine state deep-compares equal to the snapshot;
* `test_s11_schedule_survives_load` — the case that was totally absent;
* `test_fresh_engine_restore_no_spurious_events` — no event is emitted on the
  tick after a load that would not have been emitted without one;
* `test_failed_component_save_fails_whole_save` — the silent-partial path;
* `test_register_stateful_rejects_stateless_component` and
  `test_registry_completeness` — a component added tomorrow without state
  methods fails the suite tomorrow.

`test_prop_engine.py::TestBreakpoints` and
`test_fault_propagation.py::test_fault_and_procedure_state_round_trip` extend
the same guarantee to the Phase D and FDIR state added later.

## Files

* `packages/smo-simulator/src/smo_simulator/breakpoints.py` — registry-driven
  save/load
* `packages/smo-common/src/smo_common/state.py` — `Stateful` protocol
* `packages/smo-simulator/src/smo_simulator/tc_scheduler.py`,
  `scenario_engine.py`, `failure_manager.py`, `fdir.py`, `engine.py`
* `docs/THREADING_AND_STATE.md`

## Related

* **#10**, **#35**, **#59**, **#79** — earlier partial repairs of the same
  facility, each covering one missing field; this defect replaces the pattern
  that kept producing them.
* **#94** — the restore path also aliased and mutated the stored snapshot.
* **#132** — one field (`tcs._fpa_cooler_target`) still escaped, because it
  lived outside the model's state dataclass.

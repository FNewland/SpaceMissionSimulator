# 124 — the advanced FDIR subsystem had never executed

**Status:** Fixed

## Summary

`FaultPropagator.detect_fault()` and `ProcedureExecutor.start_procedure()` —
the two entry points of the advanced FDIR subsystem — had **zero call sites**.
Nine registered fault cascades and ten registered recovery procedures had
therefore never run, and eight catalogued FDIR events plus `0x0111
EPS_SAFE_MODE_ENTRY` could never be emitted by anything. The configuration
naming those cascades also referenced a parameter that does not exist anywhere
in the system.

## Root cause

```
$ git grep -n "detect_fault\|start_procedure" 7ccf41e -- packages/
fdir.py:79   def detect_fault(self, fault_id: str, current_time: float) -> list[dict]:
fdir.py:249  def start_procedure(self, proc_id: str, current_time: float) -> Optional[str]:
```

Two definitions, no callers. `fdir.py` builds a complete machine —
`fault_propagation.yaml` is parsed, the nine cascades are registered with their
delays, the ten recovery procedures are registered with their steps and
escalation levels — and the tick loop never asks it whether a fault is present.
`engine._tick_fdir_advanced(dt)` advanced the FDIR clock and evaluated the
*basic* rules only.

So the following events in `configs/eosat1/events/event_catalog.yaml:119-131`
were unreachable:

| id | name |
|---|---|
| `0x0F00` | FDIR_FAULT_DETECTED |
| `0x0F01` | FDIR_RECOVERY_STARTED |
| `0x0F02` | FDIR_RECOVERY_COMPLETE |
| `0x0F03` | FDIR_RECOVERY_FAILED |
| `0x0F04` | FDIR_LEVEL_ESCALATION |
| `0x0F06` | LOAD_SHED_DEACTIVATED |
| `0x0F07` / `0x0F08` / `0x0F09` | PROCEDURE_STARTED / COMPLETED / FAILED |

plus `0x0111 EPS_SAFE_MODE_ENTRY`. An instructor could run a fault exercise,
watch the FDIR window (**#76**), and see nothing — because there was nothing to
see, not because the fault had not propagated.

**And one cascade could never have fired even if called.**
`configs/eosat1/fdir/fault_propagation.yaml:27` names

```yaml
primary_param: "eps.overcurrent_flags"
```

which exists in no catalogue, no model and no telemetry packet. The loader
resolved it to nothing and carried on.

## Fix

* `detect_fault()` is called from the FDIR tick for every configured fault,
  with the persistence filter from **#112**; `start_procedure()` is called when
  a fault's recovery is due, and the executor is stepped each tick.
* The cascade condition vocabulary was made explicit (threshold, bitset flag)
  and **every `primary_param` must resolve** at load — `eps.overcurrent_flags`
  is corrected to the real parameter, and a phantom name is now a boot error.
* Every cascade action must have an implementation and every recovery
  procedure must be registered, checked at load rather than at fire time.
* The FDIR state (active faults, pending cascades, running procedures) is in
  the breakpoint registry (**#93**).

## Tests

`tests/test_simulator/test_fault_propagation.py` (22 tests)

* configuration closure — `test_every_cascade_rule_declares_a_condition`,
  `test_every_primary_param_resolves`,
  `test_every_basic_fdir_rule_param_resolves`,
  `test_every_cascade_action_has_an_implementation`,
  `test_every_recovery_procedure_is_registered`;
* execution — `test_fault_is_detected_and_emits_0x0F00`,
  `test_cascade_applies_after_its_declared_delay`,
  `test_recovery_starts_a_procedure_and_emits_0x0F01`,
  `test_recovery_escalates_and_emits_0x0F04`,
  `test_recovery_completes_when_the_fault_clears`,
  `test_procedure_runs_to_completion`,
  `test_load_shed_deactivation_emits_0x0F06`;
* observability — `test_fdir_parameters_are_published`,
  `test_fdir_status_reports_faults_and_recovery`.

`tests/test_content/test_event_catalogue_emissible.py::test_every_catalogued_event_is_emittable`
and `::test_the_fdir_events_are_emitted` make an unemittable catalogue entry a
build failure.

## Files

* `packages/smo-simulator/src/smo_simulator/fdir.py`, `engine.py`
* `configs/eosat1/fdir/fault_propagation.yaml`
* `configs/eosat1/events/event_catalog.yaml`
* `configs/eosat1/manual/08_fdir.md`

## Related

* **#76** — the FDIR window that had nothing to display.
* **#112** — persistence, without which running these rules against real
  transients would have been worse than not running them.
* **#9**, **#120** — the same "registered but never called" pattern.

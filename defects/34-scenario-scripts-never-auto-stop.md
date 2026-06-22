## Summary

Failure-script scenarios never stopped when their `duration_s` elapsed — they
ran forever. The engine's run loop ticks a scenario only while
`ScenarioEngine.is_active()` is true, but the "Auto-end on duration" block in
`scenario_engine.py:185-189` only **logged** "duration expired" and never set
`self._active = None`. The scenario therefore stayed active indefinitely: the
debrief was never produced, scenario-injected failures were never cleared, and
the definition could not be cleanly re-run because each event's `fired` flag
stayed set.

`ScenarioEngine.tick()` (`scenario_engine.py:170-189`) advances `_elapsed`,
fires due events, and reaches the duration check at `:188` — which previously
did nothing terminal.

## Severity

**Major** — a core training-loop behaviour (scenario completes, instructor gets
a debrief) was absent. Scenarios accumulated injected failures and could not be
re-run without restarting the simulator.

## Status

**Fixed** in `packages/smo-simulator/src/smo_simulator/scenario_engine.py` and
`packages/smo-simulator/src/smo_simulator/instructor/app.py`:

1. `tick()` now calls a new idempotent `_finish(reason)`
   (`scenario_engine.py:123-153`) when `_elapsed >= duration_s` (`:188-189`).
   `_finish` builds and caches the debrief on `self._last_debrief`
   (`:133-134`), retrievable via the new `last_debrief()` accessor (`:156-158`).
2. `_finish` clears scenario-injected failures — each injected failure id is now
   captured in `self._injected_fids` by `_fire_event` (`:191-208`, append at
   `:208`) and cleared via the failure manager (`:140`).
3. `_finish` resets every `event.fired = False` (`:148-149`) so the same
   definition can be re-run cleanly, logs the outcome (`:150-152`), and sets
   `self._active = None` (`:153`). It is safe to call twice (no-ops when
   `_active is None`, `:131-132`), so `stop_scenario()` (which also routes
   through `_finish`, `:121`) and the duration auto-stop cannot double-fire or
   crash.
4. New instructor endpoint `GET /api/scenario/debrief`
   (`instructor/app.py:27`, `:127-159`) exposes `{active, current, debrief}` so
   the UI can poll for the debrief after a scenario ends, whether it ended
   automatically or via `stop_scenario`.

## Acceptance criteria

- [x] A scenario stops itself once `_elapsed >= duration_s` and a debrief is
      cached and retrievable.
- [x] Auto-stop is idempotent and does not crash if invoked alongside a manual
      stop.
- [x] Scenario-injected failures are cleared on finish.
- [x] Per-event `fired` flags reset so the scenario can be re-run.
- [x] Manual `stop_scenario()` still works and produces a debrief.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/scenario_engine.py`
  (`tick`, new `_finish`, `last_debrief`, `_fire_event` injected-id capture)
- `packages/smo-simulator/src/smo_simulator/instructor/app.py`
  (`/api/scenario/debrief` endpoint)
- `tests/test_scenario_autostop.py` (5 tests) — auto-stop + debrief,
  idempotency / no-crash, injected-failure cleanup, fired-flag reset / re-run,
  and that manual stop still works.

## Related

- Defect #9 (`ScenarioEngine` never instantiated) — that wiring is what made
  this auto-stop path reachable in the first place; #23 (events) — debrief
  metrics depend on scenario events being observed.

## Summary

The entire instructor-led **Scenario** subsystem is non-functional. The
left-hand "Scenarios" panel of the instructor UI (scenario list, briefing,
START/STOP, progress bar) is fully wired in the frontend, but no backend
path ever loads a scenario or reacts to start/stop, so the panel always
shows "No scenarios available" and the START/STOP buttons do nothing.

Root cause is two disconnected gaps:

1. `ScenarioEngine` (`packages/smo-simulator/src/smo_simulator/scenario_engine.py:47`)
   is a fully-implemented class (load from YAML, start/stop, tick, timed and
   conditional event firing, scoring/debrief) but it is **never instantiated
   anywhere in the live package tree**. The engine only ever *reads* the
   attribute behind a `hasattr` guard that is therefore always false:
   - `engine.py:1593` — `if hasattr(self, '_scenario_engine') and self._scenario_engine.is_active(): ...`
   - `instructor/app.py:119-120` — `if hasattr(engine, '_scenario_engine'): return ...list_scenarios()` else returns `[]`, so **`/api/scenarios` always returns `[]`**.
   The only real assignment `self._scenario_engine = ScenarioEngine(...)` lives
   in the stale duplicate `files/engine.py:92`, which is outside `packages/`
   and is not imported by anything the suite runs.

2. Even if scenarios loaded, the UI's `startScenario()` / `stopScenario()`
   (`instructor/static/index.html:1874-1890`) send `{type:'start_scenario'}` /
   `{type:'stop_scenario'}`. These pass the instructor allowlist
   (`instructor/app.py:90`) but the engine's command handler
   `_handle_instructor_cmd` (`engine.py:1525-1608`) has **no branch** for
   `start_scenario` or `stop_scenario` — they are silently dropped off the
   instructor queue.

This was confirmed by grep: `ScenarioEngine(` has zero matches anywhere under
`packages/`, and `_handle_instructor_cmd` contains branches only for
`set_speed, freeze, resume, inject, clear_failure, failure_inject,
override_passes, failure_clear, set_phase, pause_scenario, start_separation`.

## Severity

**Critical** — for a training/simulator product the instructor-led scenario
capability is a headline feature. It is advertised in the UI but wholly
inoperable: an instructor cannot select, brief, start, or stop a scenario.
This is the "created but never connected" pattern at the subsystem level.

## Requirements for the fix

1. `ScenarioEngine` must be instantiated by the engine at construction,
   loaded from the scenario config directory, and ticked inside the run loop.
2. `/api/scenarios` must return the real scenario list once loaded.
3. `start_scenario` / `stop_scenario` instructor commands must be handled by
   the engine and routed to `ScenarioEngine.start()` / `.stop()`.
4. Scenario progress and the debrief returned by `stop()` must be surfaced to
   the UI (the panel already has a progress bar and result area).

## Suggested implementation

- In `engine.py.__init__`, create `self._scenario_engine = ScenarioEngine(...)`
  and call `load_scenarios_from_dir(config_dir / "scenarios")`.
- In `_run_loop`, call `self._scenario_engine.tick(...)` each step (the
  `engine.py:1593` guard then becomes live).
- Add `start_scenario` / `stop_scenario` branches to `_handle_instructor_cmd`
  that call `self._scenario_engine.start(name)` / `.stop()`.
- Surface `list_scenarios()` and the debrief object over the existing
  `/api/scenarios` and the WS state broadcast.

## Acceptance criteria

- With scenario YAML present in the config dir, `/api/scenarios` returns a
  non-empty list and the instructor panel renders selectable scenarios.
- Clicking START activates a scenario (`is_active()` true); timed and
  conditional events fire; the progress bar advances.
- Clicking STOP ends the scenario and the debrief (score / MTTD-MTTI-MTTR /
  responses) is returned and displayed.
- A test loads a sample scenario, ticks the engine, and asserts a timed event
  fired and a debrief was produced.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/engine.py` (instantiate + tick + command branches)
- `packages/smo-simulator/src/smo_simulator/scenario_engine.py` (already implemented; currently dead)
- `packages/smo-simulator/src/smo_simulator/instructor/app.py` (`/api/scenarios`)
- `packages/smo-simulator/src/smo_simulator/instructor/static/index.html` (already wired)

## Related

- The dead scenario-scoring machinery (`record_response`, `_build_debrief`,
  `ScenarioDebrief`) becomes reachable only once this is fixed.
- Same "command type accepted by the allowlist but unhandled by the engine"
  failure mode as defect #12 (CLEAR ALL FAILURES).

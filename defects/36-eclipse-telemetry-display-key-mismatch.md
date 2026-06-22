## Summary

The eclipse / sunlight state was never displayed because the MCS power-budget
display read telemetry keys the simulator never produced.

The simulator and engine emitted eclipse only as the JSON key `in_eclipse` (in
the engine state summary, passed through by the MCS server), but the MCS
power-budget display
(`packages/smo-mcs/src/smo_mcs/displays/power_budget.py`) read
`eclipse_active`, `time_to_eclipse_entry_s` and `time_to_eclipse_exit_s` — none
of which were produced. The eclipse badge was therefore permanently `False` and
the entry/exit countdown never rendered. This also made a breakpoint-restored
eclipse state look wrong on the display even when the engine-side restore
(defect #35) was correct.

## Severity

**Major** — eclipse state and the eclipse entry/exit countdown, which the
power-budget display is built around, were never shown to the operator.

## Status

**Fixed** across the simulator engine, the MCS server passthrough, and the MCS
display:

1. `power_budget.py:72-79` now reads `in_eclipse` (falling back to the legacy
   `eclipse_active` key for compatibility) and surfaces
   `time_to_eclipse_entry_s` / `time_to_eclipse_exit_s` (`:79-80`,
   `get_display_data` at `:94-96`).
2. New `OrbitPropagator.next_eclipse_transition(duration_s=7200, step_s=60)`
   (`packages/smo-common/src/smo_common/orbit/propagator.py:144-188`) — a
   bounded, coarse forward scan (~2-hour horizon, 60 s step, ~120 checks, no
   state mutation) returning `{in_eclipse, time_to_eclipse_entry_s,
   time_to_eclipse_exit_s}`.
3. The engine state summary now surfaces `in_eclipse`,
   `time_to_eclipse_entry_s` and `time_to_eclipse_exit_s`
   (`packages/smo-simulator/src/smo_simulator/engine.py:2053-2069`), computed at
   the state/HK cadence (off the hot loop), and the MCS server passes them
   through.

## Acceptance criteria

- [x] The display reads `in_eclipse` (with `eclipse_active` fallback) so the
      eclipse badge reflects real state.
- [x] Entry/exit countdowns are populated from the propagator's forward scan.
- [x] The forward scan does not mutate propagator state and is cheap enough to
      run at the state/HK cadence.
- [x] End-to-end engine → display flow shows the correct eclipse state and
      countdowns.

## Affected areas

- `packages/smo-mcs/src/smo_mcs/displays/power_budget.py` (eclipse key read +
  countdowns)
- `packages/smo-common/src/smo_common/orbit/propagator.py`
  (`next_eclipse_transition`)
- `packages/smo-simulator/src/smo_simulator/engine.py` (state-summary eclipse
  fields)
- `tests/test_mcs/test_eclipse_display.py` (5 tests, incl. end-to-end
  engine → display)

## Related

- Defect #35 (orbit restore) — the engine-side fix that this display fix
  completes; #5 (`sw_image`/`phase` not in HK) — a separate observability gap of
  the same flavour.

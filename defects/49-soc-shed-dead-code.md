## Summary

The FDIR SoC-based load-shedding ladder could never trigger. Every tick,
`SimulationEngine._tick_fdir_advanced` read the battery state of charge as:

```python
soc = getattr(eps._state, 'soc', 100.0)
```

`EPSState` has no `soc` attribute — the field is `bat_soc_pct` — so the
`getattr` default was returned on every single tick and
`LoadSheddingManager.update_stage()` was always fed `100.0`. The manager's
`get_required_stage()` therefore always returned `LOAD_SHED_STAGE_NORMAL`, no
`LOAD_SHED_ACTIVATED` (0x0F05) event was ever emitted, and the stage
definitions in `configs/eosat1/fdir/fault_propagation.yaml`
(`load_shedding_stages.stage_1/2/3`, with their `subsystem_commands`) were
dead configuration — loaded, registered, never executed.

The same code path also hard-coded the ladder thresholds (30/20/10 %) in
`fdir.py::LoadSheddingManager.get_required_stage`, duplicating the
`soc_threshold` values in `fault_propagation.yaml`.

Quantitatively the bug was masked in the shipped configuration: the in-model
bus-voltage shed fired at 26.5 V (~65 % SoC), i.e. above the 30 % stage-1
entry, so SoC never got low enough for the stages to matter. Once the shed
voltage is set sanely (defect #50, 25.0 V ≈ 45 % SoC) the SoC ladder becomes
the load-bearing protection layer, and with the shed defeated the repaired
ladder intervenes ~3.2 h earlier and preserves ~12 Wh of reserve.

## Severity

**Major** — an entire configured autonomy layer (three load-shedding stages
with per-subsystem commands) was inert, and would have stayed inert exactly
when it is needed: after the voltage-shed threshold is corrected.

## Status

**Fixed** in `packages/smo-simulator/src/smo_simulator/engine.py` and
`packages/smo-simulator/src/smo_simulator/fdir.py`:

1. `_tick_fdir_advanced` now reads
   `getattr(eps._state, 'bat_soc_pct', 100.0)` — the real attribute — so the
   manager sees live SoC and the stages trip.
2. `LoadSheddingManager` no longer hard-codes 30/20/10. It holds
   `_soc_thresholds` (module default `DEFAULT_SOC_STAGE_THRESHOLDS`) and a new
   `set_soc_stage_thresholds()` / `soc_stage_thresholds()` pair;
   `get_required_stage()` compares against those values.
3. `SimulationEngine._load_fault_propagation_config` pushes the canonical
   ladder from `simulation.yaml`
   (`eps.load_shed.soc_stage_thresholds_pct`, default `[30, 20, 10]`) into the
   manager, and logs a warning naming the stage if a
   `fault_propagation.yaml` stage `soc_threshold` diverges from it — so the
   two files cannot silently drift apart (see defect #51).

## Acceptance criteria

- [x] With generation zeroed, stages trip in order 1 → 2 → 3 as SoC decays.
- [x] Setting `bat_soc_pct = 25` and ticking FDIR once selects stage 1
      (previously stayed at stage 0 forever).
- [x] Stage thresholds come from configuration, with a single canonical source.
- [x] `LOAD_SHED_ACTIVATED` events are emitted on stage entry.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/engine.py`
  (`_tick_fdir_advanced`, `_load_fault_propagation_config`)
- `packages/smo-simulator/src/smo_simulator/fdir.py`
  (`LoadSheddingManager.get_required_stage`, `set_soc_stage_thresholds`)
- `configs/eosat1/simulation.yaml` (`eps.load_shed.soc_stage_thresholds_pct`)
- `tests/test_simulator/test_power_budget_regression.py`
  (`TestSocLoadShedStagesTrip`: ordered stage trip, threshold source, and a
  direct regression on the dead attribute read)

## Related

- Defect #50 (power budget does not close) — fixing the shed voltage is what
  makes this ladder load-bearing.
- Defect #51 (hard-coded simulation parameters) — the threshold duplication
  removed here is part of that consolidation.

# 94 — restoring a breakpoint consumed and then corrupted the snapshot

**Status:** Fixed

## Summary

Every model's `set_state` took the stored breakpoint dictionary and installed
its **values by reference**, and one model **removed** entries from it. So a
snapshot could be loaded once; after that it was a different snapshot, and the
running model went on editing the saved file's in-memory copy every tick.

## Root cause

Two variants of the same mistake.

**1. Destructive read.** `models/aocs_basic.py:1907`:

```python
def set_state(self, state: dict[str, Any]) -> None:
    self._gyro_bias = state.pop("_gyro_bias", [0.0] * 3)
    self._bearing_degradation = state.pop("_bearing_degradation", [0.0] * 4)
    self._gain_corruption_saved = state.pop("_gain_corruption_saved", {})
    for k, v in state.items():
        if hasattr(self._state, k):
            setattr(self._state, k, v)
```

`.pop()` on the caller's dict. Load a breakpoint, and the in-memory snapshot
loses `_gyro_bias`, `_bearing_degradation` and `_gain_corruption_saved`; load
the **same** snapshot object again — which is exactly what the instructor UI
does when a class reloads the same breakpoint twice — and the gyro bias and
wheel bearing degradation silently reset to zero. A repeated exercise was not
the same exercise.

**2. Aliasing.** The `for k, v in state.items(): setattr(...)` tail — present
in **all six models** and in `tcs_basic.py:725` in its purest form — installs
the snapshot's own list and dict objects into the live model. `_gyro_bias` is a
list; `sa_panel_degradation`, `device_states`, `line_currents`, `power_lines`
are dicts. After a load the model and the stored snapshot **are the same
objects**, so the next tick's `s.sa_panel_currents[face] = …` writes into the
saved state. The same aliasing existed for `engine._fdir_triggered`,
`ProcedureExecutor.steps` and `s3_custom_sids`.

Consequences: reloading one breakpoint twice gave two different spacecraft;
two engines restored from one snapshot shared mutable state; and a breakpoint
kept in memory for a "reset to here" button drifted with the simulation it was
supposed to be independent of.

**The existing round-trip test could not see any of this.** It saved, loaded
and compared — which passes when the loaded model *is* the snapshot. It never
ticked after loading, never loaded the same object twice, and never restored
two engines from one snapshot.

## Fix

* `set_state` deep-copies every container it installs, across all six models
  and the three engine-level structures; nothing is `pop`ped from the caller's
  dict.
* `get_state` deep-copies on the way out too, so a saved snapshot is not a
  window onto the live model.
* Shared helper in `smo_common.state` so a seventh model cannot reintroduce the
  pattern by writing its own loop.

## Tests

`tests/test_simulator/test_breakpoint_round_trip.py`

* `test_loaded_snapshot_is_not_mutated_by_later_ticks` — load, tick hard, and
  the snapshot dict is byte-identical to what was saved (this is the test the
  old suite structurally could not express);
* `test_two_engines_restored_from_one_snapshot_do_not_share_state` — perturb
  one engine, the other does not move;
* `test_save_perturb_load_deep_equals` run twice against the same snapshot
  object, which is the `.pop()` case.

## Files

* `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py`,
  `eps_basic.py`, `tcs_basic.py`, `ttc_basic.py`, `obdh_basic.py`,
  `payload_basic.py`, `prop_basic.py`
* `packages/smo-simulator/src/smo_simulator/engine.py` (`_fdir_triggered`,
  `s3_custom_sids`), `fdir.py` (`ProcedureExecutor.steps`)
* `packages/smo-common/src/smo_common/state.py`

## Related

* **#93** — coverage of the same facility; this defect is its correctness.
* **#127** — the instructor snapshot had the identical by-reference bug on the
  read side, where a *caller* could corrupt the simulation.

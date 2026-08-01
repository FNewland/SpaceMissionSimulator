# 91 — nothing in the flight software was seeded, so no run was reproducible

**Status:** Fixed

## Summary

`t0_epoch` in `simulation.yaml` exists so an instructor can hand a class the
same starting conditions and expect the same exercise. It never worked. Every
stochastic process in the simulator drew from the process-global `random`
module, which Python seeds from the OS at import, so two runs from the same
epoch with the same command script diverged from the first tick.

## Root cause

`seed(` had **zero hits anywhere in `packages/`** at baseline — the only
matches in the whole tree were `smo_rfsim/recording/iq_recorder.py:189`
(`np.random.default_rng(config.random_seed)`), an offline tool that nothing in
the live suite calls:

```
$ git grep -n "\.seed(" 7ccf41e -- packages/
packages/smo-rfsim/src/smo_rfsim/recording/iq_recorder.py:189  (offline recorder only)
```

Everything else used bare `random.gauss` / `random.random` / `random.uniform`:
separation tumble rates, star-tracker star counts, coarse-sun-sensor
illumination, magnetometer and gyro noise, gyro bias random walk, failure
onset flicker, and the per-panel and per-node process noise in `eps_basic`
(`eps_basic.py:376`, `eps_basic.py:407`) and `tcs_basic` (`tcs_basic.py:493`).

Two consequences beyond "the numbers differ":

1. **The RNG state was not in the breakpoint.** Load a breakpoint twice and
   the two continuations diverged, so a breakpoint was not a reproducible
   teaching artefact — it was a starting point for a different run.
2. **`obdh.obc_time_cuc` was initialised from the wall clock**, not from the
   configured epoch — `obdh_basic.py:189`:

   ```python
   self._state.obc_time_cuc = int((now - TIME_EPOCH).total_seconds())
   ```

   so the on-board time an exercise started from depended on when the
   instructor pressed start, and every CUC-keyed artefact (S15 dumps, S11
   schedules, archived telemetry) was unrepeatable even before any noise was
   drawn.

## Fix

* One `random.Random` per component, derived from `simulation.yaml: seed`
  through `smo_common.state.derive_rng(seed, component_name)`, so components
  are independently reproducible and adding a component does not shift another
  component's stream.
* Every `random.*` call site in `packages/` replaced with `self.rng.*`.
* The RNG state is part of each component's `get_state()`/`set_state()`
  (`_rng_state`), so a breakpoint reload continues the same stream.
* `obc_time_cuc` is derived from the configured `t0_epoch`, not `utcnow()`.

Same seed + same epoch + same command script now produces a byte-identical
parameter trace.

## Tests

`tests/test_simulator/test_determinism.py`

* `test_same_seed_identical_trace` — two engines, same seed, identical
  parameter trace over a run;
* `test_different_seed_differs` — the seed is actually load-bearing (a
  constant-output "RNG" would pass the test above);
* `test_seed_comes_from_config` — the value in `simulation.yaml` is the one
  used;
* `test_rng_state_round_trips_through_breakpoint` — save, tick, load, and the
  continuation matches the original;
* `test_models_have_engine_seeded_rngs` — a model that reuses the global
  `random` module fails the suite, so this cannot silently regress.

`tests/test_simulator/test_aocs_dynamics.py::TestStateRoundTrip::test_same_seed_identical_attitude_trace`
and `test_prop_engine.py::TestDeterminism::test_same_seed_same_burn_same_trace`
pin the same property through the two most stochastic subsystems.

## Files

* `packages/smo-common/src/smo_common/state.py` — `derive_rng`
* `packages/smo-simulator/src/smo_simulator/engine.py`,
  `models/*.py`, `failure_manager.py` — per-component RNGs
* `packages/smo-simulator/src/smo_simulator/models/obdh_basic.py` — CUC from
  the configured epoch
* `configs/eosat1/simulation.yaml` — `seed`
* `docs/DETERMINISM.md` (new) — the contract and how to break it

## Related

* **#92** — the other half of "the same inputs give the same answer": at
  ×60 the integrator changed the answer regardless of the seed.
* **#93**, **#94** — a breakpoint that does not carry the RNG state is not a
  reproducible artefact even with this defect fixed.

# 92 — time acceleration changed the physics, not just the clock

**Status:** Fixed

## Summary

Time acceleration is the feature that makes a 94-minute orbit teachable in a
90-minute session. It was implemented by handing the accelerated timestep
straight to every model, so **the answer depended on the speed setting**. At
×60 the battery, the thermal RC network, the heater hysteresis and the wheel
dynamics all integrated 60-second explicit Euler steps.

## Root cause

`engine.py:1043`:

```python
dt_sim = dt_real * self.speed
```

and then, unmodified, into every model at `engine.py:1111`:

```python
model.tick(dt_sim, orbit_state, self.params)
```

with `orbit.advance(dt_sim)`, `_tick_spacecraft_phase(dt_sim)`,
`_tick_fdir_advanced(dt_sim)`, `_failure_manager.tick(dt_sim)` all on the same
value. Every model integrator is first-order explicit — e.g.
`tcs_basic.py:493`:

```python
s.temp_fpa += (env_int + cool - s.temp_fpa) / self._TAU["fpa"] * dt + random.gauss(0, 0.02)
```

With `TAU["fpa"] = 100 s` and `dt = 60 s` the step is 0.6 τ; the heater
thermostat, which is a hysteresis comparator sampled once per step, degenerates
entirely. **Detumble diverged to 1.4e39 at dt = 1000.**

The same line carries the second defect: the process-noise term is added
**unscaled**. Discretised Brownian motion requires σ·√dt; adding a fixed σ once
per step makes the random-walk variance proportional to the number of steps,
i.e. **the accumulated noise variance was wrong by a factor of `speed`** — a
gyro bias walk that is realistic at ×1 is 60× too small in variance at ×60.

So neither half of "faster wall clock, same spacecraft" held: the deterministic
part changed because the step was too coarse, and the stochastic part changed
because the noise did not scale.

## Fix

* **Sub-stepped tick.** The engine splits `dt_sim` into sub-steps bounded by a
  configured maximum model step and iterates the models, so a model never sees
  a step large relative to its own time constant regardless of the speed
  setting. The sub-step count is capped so a pathological speed cannot stall
  the loop; AOCS runs its own fixed h = 0.5 s RK4 inside that (#108).
* **√dt noise scaling** at every process-noise site: `self.rng.gauss(0, σ) *
  math.sqrt(dt)`.
* Above ×60 AOCS declares a **SECULAR** fidelity mode rather than pretending:
  the fast loop is bypassed, attitude is sampled from the mode's own
  closed-loop error distribution, the slow states still integrate exactly, and
  an S5 event plus `aocs.fidelity_mode` make the bypass visible to the
  operator. That is a deliberate, declared approximation — not this defect.

## Effect

×1 and ×60 now agree on end-of-orbit SoC to 0.5 %, battery temperature to
0.5 K and eclipse entry to 60 s.

## Tests

`tests/test_simulator/test_acceleration_consistency.py`

* `test_speed_60_matches_realtime_end_state` — the ×1/×60 agreement above;
* `test_dt_600_remains_finite_and_bounded` — the divergence case is now
  bounded (it used to reach 1.4e39);
* `test_substep_count_and_cap` — the sub-step count is what the configuration
  says and the cap holds.

`tests/test_simulator/test_aocs_dynamics.py::TestAcceleration` repeats it for
attitude: `test_detumble_stable_at_any_dt` (parametrised over dt),
`test_substep_consistency_dt1_vs_dt60`, and
`test_secular_mode_honest_and_slow_states_advance`.

## Files

* `packages/smo-simulator/src/smo_simulator/engine.py` — sub-stepped
  `_tick_once`, model step bound
* `packages/smo-simulator/src/smo_simulator/models/*.py` — √dt on every
  process-noise term
* `configs/eosat1/simulation.yaml` — max model step, speed cap
* `docs/DETERMINISM.md`

## Related

* **#91** — same seed, same answer; this defect is same speed-independence.
* **#108** — the AOCS integrator that diverged.

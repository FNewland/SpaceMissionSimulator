# 97 — the AOCS sun sweep ran on a hard-coded 5700 s "orbit"

**Status:** Fixed

## Summary

The synthetic sun vector that drove all solar generation swept at a hard-coded
5700-second period against a real orbit period of 5669.3 s. The 30.7-second
difference is a beat: the phase between illumination and orbit position walked
by ~2° per orbit, moving orbit-average generation by ±8 W depending on where in
the beat cycle a run happened to start.

## Root cause

`models/aocs_basic.py:1109`:

```python
self._orbit_phase + dt * (360.0 / 5700.0)
```

5700 s is a round number that was never connected to `orbit.yaml`. The real
period comes from the propagator's mean motion — 5669.3 s for the shipped
450 km TLE — and the propagator was right there in the same tick, passed in as
`orbit_state`.

Because the sun vector is what `eps_basic` projects onto the panels
(`shared_params[0x0245..0x0247]`), the beat propagated straight into
generation, into SoC, and therefore into every power exercise. Two runs from
epochs a few days apart produced orbit-average generation 16 W apart with no
physical cause, which is larger than most of the design decisions students are
asked to reason about.

It also broke reproducibility of a subtler kind than **#91**: the same seed
gave the same trace, but the same *scenario* at a different epoch gave a
qualitatively different power picture.

## Fix

The sun direction is derived from the orbit state the propagator already
computes — beta angle and orbit phase from the active element set — so there is
one source of orbital geometry and no independent clock to drift against it.
After Phase C the sun vector is a genuine attitude-dependent quantity
(sun-vector in body frame from the true attitude and the true sun direction),
which removes the synthetic sweep entirely; the 5700 s constant is gone.

## Tests

`tests/test_simulator/test_eps_generation_physics.py::TestSunVectorDeterminism::test_generation_pure_function_of_geometry`
— generation at a given orbit position and beta is the same regardless of how
the run reached that position, which is exactly the property the beat broke.

`tests/test_simulator/test_power_budget_abuse.py::test_stowed_wings_net_negative_at_every_beta`
sweeps beta and epoch; under the old model the same case moved ±8 W with the
epoch.

## Files

* `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py`
* `packages/smo-common/src/smo_common/orbit/propagator.py` — beta and phase
  accessors

## Related

* **#95**, **#96** — the other two generation defects; all three had to be
  fixed before an orbit-average margin was a number worth quoting.
* **#108** — the attitude rebuild that replaced the synthetic sweep with a real
  sun vector.

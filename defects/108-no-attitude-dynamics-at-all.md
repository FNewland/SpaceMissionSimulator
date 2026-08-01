# 108 — the AOCS had no attitude dynamics

**Status:** Fixed

## Summary

The attitude control subsystem of a pointing spacecraft simulator did not
simulate attitude. There was **no inertia tensor anywhere in the repository**,
no torque integration, no sensor that read anything other than truth, and no
estimator. Pointing error was a number the mode routine decayed toward zero on
a hand-picked time constant. Every AOCS exercise, and every geolocation
consequence downstream of it, was theatre.

## Root cause

The only `inertia` in `packages/` at baseline was
`aocs_basic.py:222` — `self._rw_inertia = 0.005`, the *wheel* inertia, used
solely to convert wheel RPM into a momentum number for display. The spacecraft
body had none, because nothing integrated a body rate.

What existed instead, per mode (`aocs_basic.py:758-790`):

```python
def _tick_fine_point(self, s, dt):
    """FINE_POINT — tight control bandwidth, highest accuracy."""
    ...
    s.q[i] += random.gauss(0, 0.000005)
    ... + random.gauss(0, 0.0005)
```

i.e. a quaternion nudged by noise and an error figure driven to a mode-specific
floor. Consequences that mattered operationally:

* **Sensors read truth.** Star tracker, sun sensors, magnetometer and gyro all
  reported the state variable directly, so sensor selection, misalignment,
  noise, bias and blinding had no effect on control. `MAG_SELECT`,
  `ST_SELECT` and gyro calibration were commands with cosmetic outputs.
* **No estimator.** There was no distinction between *knowledge* and *truth*,
  so "we have lost the star tracker" could not degrade pointing — which is the
  central AOCS lesson.
* **No INERTIAL mode**, so an inertially-fixed attitude (the natural mode for
  a calibration or a burn) could not be commanded at all.
* **FINE_POINT was unreachable by any autonomous path.** It could only be
  entered by ground command, and it had **no exit branch** — once in, the
  spacecraft stayed in FINE_POINT until someone commanded otherwise, including
  through eclipse and through a slew.
* **FINE_POINT converged to ~0.0005°.** That is 1.8 arcseconds of total
  pointing on a 6U with a 0.05° star camera — better than the sensor. A
  student sizing a payload against it would have concluded that geolocation
  was free.

## Fix — a real attitude subsystem

* **Rigid-body dynamics.** Inertia tensor in `configs/eosat1/subsystems/aocs.yaml`,
  RK4 integration at fixed h = 0.5 s inside the sub-stepped tick (**#92**),
  four pyramid reaction wheels integrated analytically, magnetorquers, and
  disturbance torques: gravity gradient, aerodynamic, SRP and residual dipole.
* **Attitude-dependent sensor models** — star tracker with FOV and exclusion
  cones, coarse sun sensors, magnetometer, and a gyro with bias, noise and
  misalignment.
* **A 6-state MEKF**, so knowledge and truth are separate quantities and
  `aocs.att_knowledge_error` is a real parameter.
* **Quaternion-feedback PD with feed-forward**, LVLH and inertial reference
  generators, and **mode 9 = INERTIAL**.
* **The modes became physically real**: total pointing 3σ of ~5° (COARSE),
  0.065° (NOMINAL), 0.013° (FINE) — geolocation 39 km / 510 m / 100 m at
  450 km. FINE entry gained a precondition bitmask (`0x02C5`) and is
  **refused with TM(1,8)** when unmet; the payload refuses to capture without
  it.
* Telemetry block `0x02A0`–`0x02C8`, HK SID 7, and an MCS display.

## Tests

`tests/test_simulator/test_aocs_dynamics.py` (≈45 tests) — the physics is
tested against closed-form results, not against itself:

* `TestDynamics` — momentum conservation of a free body, constant torque gives
  the analytic rate, balanced pyramid has zero net momentum, MTQ dipole torque
  magnitude, wheel torque clamped at rated;
* `TestDisturbances` — magnitudes sane, gravity gradient at 45°, SRP off in
  eclipse;
* `TestEstimator` — MEKF converges from 10°, covariance consistent with the
  actual error, knowledge degrades without the star tracker, innovation gate
  rejects a wild fix;
* `TestPointingPerformance` — the three error bands, nadir tracking pitch
  rate, a 30° slew that completes and resumes, detumble convergence;
* `TestFineGating` — FINE refused from cold, refused with three wheels, the
  ready mask clears when ready.

## Files

* `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py`
* `packages/smo-simulator/src/smo_simulator/models/aocs_estimator.py` (new)
* `configs/eosat1/subsystems/aocs.yaml`,
  `configs/eosat1/telemetry/parameters.yaml`, `hk_structures.yaml`
* `configs/eosat1/manual/02_aocs.md`

## Related

* **#92** — the integrator that made a real dynamic model possible at speed.
* **#109**, **#110**, **#111**, **#112** — the AOCS defects that only became
  visible, or only became consequential, once the actuators and sensors did
  something.
* **#4** — "AOCS mode defaults to NOMINAL at construction"; the mode machine
  now starts where the phase says.

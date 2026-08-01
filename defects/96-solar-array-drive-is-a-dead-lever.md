# 96 — the solar array drive was a fully dead lever

**Status:** Fixed

## Summary

`EPS_SOLAR_DRIVE` (func 20) accepted an angle, the S2 device gate 0x0101 could
switch the drive off, and `eps.solar_array_drive_angle` (0x0133) was
downlinked in housekeeping. **Nothing in the generation path ever read the
angle.** The complete operator-facing appearance of a solar array drive existed
with no mechanism behind it — and the wings were mounted on the one axis where
that mattered most.

## Root cause

The command wrote the state (`eps_basic.py:841-844`):

```python
elif command == "set_solar_array_drive":
    self._state.solar_array_drive_angle = max(-90.0, min(90.0, angle))
    return {"success": True, ...}
```

the tick published it (`eps_basic.py:765`):

```python
shared_params[0x0133] = s.solar_array_drive_angle
```

and generation (`eps_basic.py:400-417`) projected the sun vector onto **fixed
body-frame panel normals**. `solar_array_drive_angle` appears in neither the
per-panel loop nor the tumbling fallback. The device gate did work, but only as
an on/off kill (`eps_basic.py:420-431`), which is what disguised the hole: the
drive visibly "did something", so nobody checked that the *angle* did nothing.

Geometrically the ±Y deployable wings — 0.180 m² each, the majority of the
array area — sat on the axis the implemented sun-vector model sweeps, so their
illumination went as `cos β · |sin φ|` over the orbit. Averaged over φ that is
2/π of what the same cells deliver if they are held normal to the sun's
projection: **0.66 m² of cells performed like 0.42 m².** Worse, the duty
depends on where in the orbit the run starts, so orbit-average generation moved
8 W between runs from different epochs — noise that swamped the real design
variables.

## Fix

A real single-axis drive:

```yaml
solar_panels:
  sada_present: true
  sada_axis: y             # rotation axis = body Y = orbit normal (LVLH)
  sada_tracking: true      # false => wings frozen at solar_array_drive_angle
  sada_max_rate_deg_s: 1.0
```

* With `sada_tracking` the wing pair follows the sun's projection
  perpendicular to the drive axis, converting the `cos β · |sin φ|` duty into
  continuous `cos β` — **1.56× the energy from the same area** — and
  `solar_array_drive_angle` is published as the achieved angle.
* With tracking off, generation is computed at the commanded angle: the
  command is now load-bearing and "SADA stuck at 30°" is a real failure mode
  with a real cost.
* Run-to-run alignment spread collapses from 8 W to 1 W.
* The Phase-C attitude frame put the orbit normal on body Y, so the axis was
  corrected from `z` to `y`; the config comment records that Phase B's
  synthetic frame had labelled the same geometric axis `z`.

The hardware layout itself — deployable wings on the ±Y faces of a 6U — is a
design decision and was kept; what changed is that the drive that layout
requires now exists.

## Tests

`tests/test_simulator/test_eps_generation_physics.py::TestSolarArrayDrive`

* `test_tracking_wing_power_flat_over_orbit_phase` — with tracking, wing power
  no longer depends on orbit phase (this is the `|sin φ|` duty being gone);
* `test_drive_angle_load_bearing_when_not_tracking` — the commanded angle
  changes generation, i.e. the lever is connected;
* `test_stuck_sada_costs_orbit_average_energy` — the failure mode has a
  measurable cost;
* `test_device_gate_still_zeroes_generation` — the pre-existing kill path is
  unchanged.

## Files

* `packages/smo-simulator/src/smo_simulator/models/eps_basic.py`
* `configs/eosat1/subsystems/eps.yaml`
* `configs/eosat1/manual/02_eps.md`,
  `configs/eosat1/procedures/leop/solar_array_verification.md`

## Related

* **#95** — the loss stack; both had to be right before any margin number
  meant anything.
* **#97** — the orbit-phase dependence that made run-to-run spread hard to see.

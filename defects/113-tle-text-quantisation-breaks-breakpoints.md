# 113 — a manoeuvred orbit could not survive a breakpoint round trip

**Status:** Fixed

## Summary

Phase D gave the spacecraft a propulsion system, so the orbit became something
the operator can change. The orbit state was serialised for breakpoints as
**TLE text**, and the TLE format quantises the angular elements to 1e-4 degrees
— about **1.3 m of along-track position**. Save a breakpoint after a burn, load
it, and the spacecraft was in a slightly different orbit from the one that was
saved, every time.

## Root cause

A TLE is a *transport* format with fixed-width, decimal-truncated fields:
inclination, RAAN, argument of perigee and mean anomaly each get 8 characters,
i.e. 1e-4 degrees of resolution. That is entirely adequate for what TLEs are
for — distributing element sets — and inadequate as a state snapshot, because
a snapshot has to reproduce the state it captured exactly or the "same moment"
promise of a breakpoint (**#93**) is false.

The error is small in absolute terms and cumulative in effect: reload a
breakpoint repeatedly during a manoeuvre exercise, as a class does, and the
pass geometry, eclipse times and imaging opportunities all drift away from the
briefing they were computed for. `epoch_version` — which the planner and the
MCS use as a cache key — would also flip caches on a "restore" that was
supposed to be a no-op.

## Fix

`get_elset()` serialises **both** representations
(`packages/smo-common/src/smo_common/orbit/propagator.py:108`):

```python
return {
    # Human/wire-friendly TLE (planner + MCS build propagators from these
    # two strings — no new code path).
    "tle_line1": ..., "tle_line2": ..., "epoch_version": ...,
    # Full-precision mean elements. The TLE text quantises angles to
    # 1e-4 deg (~1.3 m of position); breakpoint restore uses these so
    # a save/load round-trip is sub-millimetre.
    "elements": {"epoch_1949": …, "no_kozai": …, "ecco": …, "inclo": …,
                 "nodeo": …, "argpo": …, "mo": …, "bstar": …, …},
}
```

`set_elset()` prefers `elements` and rebuilds the `Satrec` through
`sgp4init`, falling back to `twoline2rv` on the text when `elements` is absent —
so a **pre-propulsion snapshot still loads**, and the ground segment keeps
using the two TLE strings with no new code path. `None` restores the
`orbit.yaml` TLE and `epoch_version = 0`, i.e. an old snapshot loads as
un-manoeuvred rather than as an error.

## Tests

`tests/test_simulator/test_prop_engine.py::TestBreakpoints`

* `test_burn_survives_save_ticks_load` — burn, save, tick, load, and the orbit
  matches to sub-millimetre rather than to TLE resolution;
* `test_pre_propulsion_snapshot_still_loads` — backward compatibility;
* `test_breakpoint_mid_burn_resumes_and_completes` — a burn interrupted by a
  save/load completes with the same total ΔV.

`tests/test_breakpoint_orbit.py` retains the pre-Phase-D orbital-position
round trip from **#35**.

## Files

* `packages/smo-common/src/smo_common/orbit/propagator.py`
* `packages/smo-simulator/src/smo_simulator/breakpoints.py`

## Related

* **#93** — breakpoint completeness; this is its precision.
* **#35**, **#59** — earlier orbit/breakpoint interactions and the planner
  caches keyed on `epoch_version`.

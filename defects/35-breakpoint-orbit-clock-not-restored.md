## Summary

Saving and re-loading a breakpoint did not restore the spacecraft's orbital
position or eclipse state, because the propagator clock was never captured.

`OrbitPropagator` advances its own clock `_sim_utc`
(`packages/smo-common/src/smo_common/orbit/propagator.py:70`, `:83`)
**independently** of `engine._sim_time` — each is stepped separately every
tick. SGP4 position and `OrbitState.in_eclipse` are determined solely by
`_sim_utc`. `BreakpointManager`
(`packages/smo-simulator/src/smo_simulator/breakpoints.py`) captured the
parameter store, subsystem states and the TM stores, but **not** the propagator
clock. A loaded breakpoint therefore left the orbit wherever the propagator had
drifted to, and the eclipse/sunlight telemetry (EPS writes eclipse from
`orbit_state.in_eclipse`) diverged from the saved state.

Empirically: save → run 54 sim-minutes → load left the orbit clock ~3240 s and
the position ~7115 km away from the snapshot.

## Severity

**Major** — a restored breakpoint did not reproduce the spacecraft's actual
orbital/eclipse state, so any training or replay built on breakpoints was
positionally wrong and the eclipse telemetry was inconsistent.

## Status

**Fixed** in `packages/smo-simulator/src/smo_simulator/breakpoints.py`:

1. `save()` now captures the propagator clock:
   `state["orbit_utc"] = orbit.utc.isoformat()` (`breakpoints.py:61`), guarded
   so a missing/odd propagator does not abort the save (`:60-63`).
2. `load()` restores it via
   `orbit.reset(datetime.fromisoformat(state.get("orbit_utc") or state.get("sim_time")))`
   (`breakpoints.py:106-113`). Pre-existing snapshots that lack `orbit_utc`
   fall back to the engine `sim_time` (negligible ~0.1 km error). No propagator
   change was needed — the `reset()` / `utc` contract
   (`propagator.py:74-78`, `:88-89`) was already sound.

**Operational note:** this fix (and the others in this session) is an
**uncommitted working-tree edit** relative to git HEAD. A running simulator
must be **restarted** to pick it up, and any breakpoint file written **before**
this fix lacks the `orbit_utc` field (and will use the sim-time fallback on
load).

## Acceptance criteria

- [x] A saved breakpoint records the propagator clock (`orbit_utc`).
- [x] A loaded breakpoint restores `pos_eci` (<1 km), lat/lon/alt (<1e-3), the
      orbit clock (<1 s) and `in_eclipse`.
- [x] The restored eclipse parameter (0x0108) matches the re-propagated orbit.
- [x] Snapshots predating the fix still load via the `sim_time` fallback.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/breakpoints.py` (`save`, `load`)
- `tests/test_breakpoint_orbit.py` (2 tests, written failing-first) —
  in-memory and disk-JSON round-trips assert restored ECI position, geodetic
  coordinates, orbit clock, `in_eclipse`, and that the restored eclipse param
  matches the re-propagated orbit.

## Related

- Defect #36 (eclipse telemetry display key mismatch) — even with the orbit
  correctly restored here, the MCS display still showed the eclipse state
  wrong until #36 was fixed; #10 (breakpoint save/load UI wiring).

## Summary

Loading a breakpoint moves the simulator's clock arbitrarily far — usually
**backward** (`breakpoints.py` restores `eng._sim_time` and calls
`orbit.reset(orbit_utc)`). The planner's clock followed within ~1.5 s, because
`_refresh_sim_anchor` simply overwrote the anchor. Its cached plan products did
not:

```python
while True:                    # server.py:248-252
    await asyncio.sleep(600)
    self._compute_contacts()
    self._compute_ground_track()
```

A fixed **600 wall-clock seconds**, independent of sim speed and of any
discontinuity. After a breakpoint that moved sim time back six hours,
`/api/contacts` kept serving windows computed for the *future* — every AOS in
the past relative to the new "now", i.e. **zero upcoming passes for up to ten
minutes** — and `/api/ground-track` served a stale 3 h arc whose past/future
colouring was inverted for its whole length. Everything downstream inherited
the error: `/api/budget/power`, `/api/budget/data` and `/api/schedule/validate`
all read `_contacts_cache`.

There was no discontinuity detection anywhere: `_refresh_sim_anchor` never
compared the new sim time with what the previous anchor predicted. The live
spacecraft marker self-healed only by accident (`_get_spacecraft_state` resets
`_live_prop` on every request) — nothing reset it *because* a jump had
happened.

## Severity

**Major** — after the single most common instructor action (load a breakpoint),
the planner showed wrong passes, wrong budgets and wrong validation for up to
ten minutes, with no indication.

## Status

**Fixed.**

1. **Discontinuity detection** in `_refresh_sim_anchor`: the new `sim_time` is
   compared against what the previous anchor predicted for this wall instant,
   with tolerance `max(planner.yaml time.jump_tolerance_s,
   3 * anchor_refresh_s * |speed|)` — so time acceleration and speed changes do
   not read as jumps. On a jump the planner logs
   `Sim clock discontinuity: +/-N s`, sets `_plan_dirty`, records
   `last_jump_utc` / `last_jump_delta_s` / `jump_count`, and resets `_live_prop`
   to the new epoch explicitly rather than relying on the per-request reset.
2. **The recompute is no longer a fixed wall timer.** `_plan_refresh_loop()`
   wakes once a second and recomputes contacts + ground track when
   `_plan_dirty` is set (so a jump is picked up in ~1 s), when
   `refresh.recompute_interval_s` of wall time has passed, **or** when the
   cached products are older than `refresh.sim_recompute_interval_s` of SIM
   time — which is what matters in a time-accelerated run.
   `recompute_plan_products()` rebuilds both caches, resets the live
   propagator and clears the flag.
3. **It is observable.** `/api/time-source` reports `last_jump_utc`,
   `last_jump_delta_s`, `jump_count` and `plan_dirty`, alongside
   `contacts_computed_at`.

## Acceptance criteria

- A backward jump (breakpoint load) and a large forward jump both set
  `_plan_dirty`; ordinary drift and a pure speed change do not.
- The jump resets `_live_prop` to the new epoch.
- `recompute_plan_products()` recomputes contacts and ground track at the NEW
  epoch and clears the flag; the stale windows are actually replaced.
- The jump is visible on `/api/time-source`.

## Affected areas

- `packages/smo-planner/src/smo_planner/server.py`
  (`_refresh_sim_anchor`, `_plan_refresh_loop`, `recompute_plan_products`,
  `start`, `_handle_time_source`)
- `configs/eosat1/planning/planner.yaml` (`time.jump_tolerance_s`,
  `refresh.*`)
- Tests: `tests/test_planner/test_breakpoint_jump.py`

## Related

- Defect #58 — same endpoint, same clock.
- `docs/review_2026-07/design_issues.md:196` flagged the fixed 600 s as a
  sim-speed problem; the breakpoint case made it a correctness problem.
- The MCS has the same gap (`smo-mcs/server.py:906` explicitly accepts
  "bounded drift = poll interval" for "pause / speed change / breakpoint
  load") — **not fixed here**, MCS is out of scope for this change.

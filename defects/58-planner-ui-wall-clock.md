## Summary

The planner backend resolves "now" correctly — CLI `--time-source` > env
`SMO_TIME_SOURCE` > `mission.yaml time_source` > default `"sim"`, then an
anchor refreshed from the simulator every 1.5 s and extrapolated by speed
(`server.py _get_now`). **The front end had no notion of sim time at all.**
Every clock, NOW marker, past/future split and default start time in
`static/index.html` was browser wall-clock:

| Site | Effect |
|---|---|
| `updateClock()` (`:1365`) | The header UTC clock/date showed the operator's wall clock in every mode |
| `drawTimeline()` (`:1594`) | The 24 h window origin and the red NOW line were wall-clock; real contacts rendered at the wrong X or off-canvas |
| `updateContactsTable()` (`:1726`) | "Upcoming" was filtered against wall clock; after a backward breakpoint jump the table emptied |
| `drawGroundTrack()` (`:1245`) | Past/future colouring inverted whenever sim time != wall time |
| `updateTerminator()` (`:1133`) | Day/night drawn for the wrong epoch, inconsistent with the API's eclipse flag |
| `openModal()` (`:1965`) | **Worst.** A new activity defaulted to wall-clock "now". With sim time at T-0 (2026-03-10) and wall clock in July, the operator scheduled activities ~4.5 months in the future: they never executed and never appeared in any pass window |

This was masked in a default run because the engine initialises `_sim_time` to
wall clock, and became visibly wrong the moment a breakpoint, a scenario or a
speed != 1 moved sim time away from it.

Compounding it, the **wall-clock fallback was silent**: with `time_source=sim`
and a simulator that was never reachable, `_get_now()` returned
`datetime.now(timezone.utc)` with no warning while `/api/time-source` still
reported `"sim"` — so the badge said SIM over wall-clock data.

## Severity

**Major** — the planner's primary output (a time-tagged plan) was authored on
the wrong clock, silently.

## Status

**Fixed.**

1. **`/api/time-source` now serves the planner's clock**, not just its mode:
   `now` / `sim_time`, `speed`, `sim_reachable`, `wall_clock_fallback`,
   `anchor_age_s`, `degraded`, `sim_state_url`, plus the jump fields from
   defect #59.
2. **The page runs on that clock.** `simNow()` = `serverNow + (Date.now() -
   anchorWall) * speed`, re-anchored every 5 s and on every poll.
   `updateClock`, `updateTerminator`, `drawGroundTrack`, `drawTimeline`,
   `updateContactsTable` and `openModal` all use it; no `Date.now()` /
   `new Date()` remains in any of those paths. The Add-Activity modal prefills
   sim time (compensating for `datetime-local`'s browser-local reading, both
   ways).
3. **Degradation is visible.** A `clockSourceBadge` in the header reads
   `SIM TIME` / `UTC (REAL)`, and turns red `WALL CLOCK` with a tooltip naming
   the unreachable `sim_state_url` when the planner has fallen back or the
   anchor is stale. The timeline's NOW label says "(wall clock)" in that state,
   the API status line says "connected (wall clock)", and the panel badges read
   `WALL` instead of `SIM`. Server side, the fallback logs a WARNING once
   (not per call) naming the URL.
4. The badge semantics were corrected while we were there: it names the
   **clock** the data is computed against (`SIM CLOCK` / `REAL UTC` /
   `WALL CLOCK`), not a data fidelity — the data itself is always the server's
   real computation (defect #57).

## Acceptance criteria

- No time-consuming draw path in `static/index.html` reads the browser clock.
- The Add-Activity modal defaults to the planner's sim time.
- `/api/time-source` reports `wall_clock_fallback: true` and `degraded: true`
  when `time_source=sim` and the simulator has never been reachable, and the UI
  says so rather than drifting silently.

## Affected areas

- `packages/smo-planner/src/smo_planner/server.py`
  (`_handle_time_source`, `_get_now`)
- `packages/smo-planner/src/smo_planner/static/index.html`
- Tests: `tests/test_planner/test_ui_data_sources.py` (`TestUIUsesServerTime`),
  `tests/test_planner/test_breakpoint_jump.py`
  (`test_time_source_endpoint_flags_silent_wall_clock_fallback`)

## Related

- Defect #57 — deletes the browser-side fixtures whose generators were the
  other wall-clock consumers.
- Defect #59 — the jump fields on the same endpoint.
- `docs/review_2026-07/design_issues.md` (blocking I/O in a handler) — the
  anchor refresh is still called from `_get_now()`; moving it entirely to the
  background task is left as follow-up.

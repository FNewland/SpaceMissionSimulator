## Summary

Breakpointing is "save-only" from any UI a user actually has, and even the
SAVE button does not capture real state. A working `BreakpointManager` with
**both** `save()` and `load()` exists
(`packages/smo-simulator/src/smo_simulator/breakpoints.py:20` and `:48`), and
HTTP endpoints that call them are registered
(`instructor/app.py:28-29` → `handle_breakpoint_save` / `handle_breakpoint_load`),
but the instructor UI never calls those endpoints:

- The only breakpoint control in the UI is a SAVE button (`btn-save-bp`,
  `instructor/static/index.html:1300`). Its handler `saveBreakpoint()`
  (`index.html:1987-2004`) posts to **`/api/command`** with
  `{type:'save_breakpoint', name, tick}` (line 2000) — **not** to
  `/api/breakpoint/save`. The engine's `_handle_instructor_cmd`
  (`engine.py:1525-1608`) has **no `save_breakpoint` branch**, so the command
  is dropped. The only effect of clicking SAVE is a client-side push to a
  local `breakpoints[]` array and a toast; no snapshot is ever captured.
- There is **no LOAD/RESTORE control at all**. `renderBreakpoints()`
  (`index.html:2006-2020`) renders each saved breakpoint as a static card with
  no button or click handler. Grep of `index.html` for
  `breakpoint/load | load_breakpoint | RESTORE | loadBreakpoint` returns
  nothing. The repo's own docs confirm it: `docs/INSTRUCTOR_GUIDE.md:128` —
  "Future UI enhancement: add a 'LOAD' button next to each saved breakpoint."

So the backend capability is complete and even exposed on routes, but the
single UI affordance targets a command the engine ignores, and the load path
is unreachable. This is the user-reported defect and the archetype of the
"implemented but not connected to a UI element" class.

## Severity

**Critical** — a save/restore feature that can neither truly save (the SAVE
button is a no-op against the engine) nor load is wholly broken. For a
training simulator, breakpoint restore is a core instructor workflow.

## Requirements for the fix

1. SAVE must capture real simulator state via `BreakpointManager.save()`.
2. Each saved breakpoint must offer a LOAD/RESTORE action that restores state
   via `BreakpointManager.load()`.
3. The breakpoint list should reflect snapshots that actually exist on disk /
   in the engine, not just a client-side array.

## Suggested implementation

Two equivalent options; pick one and be consistent:

- **Use the existing HTTP routes (preferred):** point `saveBreakpoint()` at
  `POST /api/breakpoint/save` and add a per-row LOAD button that calls
  `POST /api/breakpoint/load`. The routes already invoke `bm.save()` / `bm.load()`.
- **Use the instructor command channel:** add `save_breakpoint` and
  `load_breakpoint` branches to `_handle_instructor_cmd` (both types are
  already in the `instructor/app.py:90` allowlist) that call the
  `BreakpointManager`.

## Acceptance criteria

- Clicking SAVE produces a real snapshot (verify `tick_count`, `sim_time`,
  per-subsystem state captured) — confirmed via the returned state or a file.
- Each saved breakpoint has a LOAD button; clicking it restores tick, sim
  time, speed, params, and subsystem state, and the live displays reflect the
  restored values.
- A test does save → advance sim → load → asserts state matches the snapshot.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/instructor/static/index.html` (`saveBreakpoint`, `renderBreakpoints`, new LOAD control)
- `packages/smo-simulator/src/smo_simulator/instructor/app.py` (routes already exist)
- `packages/smo-simulator/src/smo_simulator/engine.py` (only if using the command-channel option)
- `packages/smo-simulator/src/smo_simulator/breakpoints.py` (already implemented)

## Related

- Same root failure mode as defect #9 and #12: the instructor UI emits a
  command `type` over `/api/command` that the engine's `_handle_instructor_cmd`
  has no branch for, so it is silently dropped.

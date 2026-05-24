## Summary

The MCS ships an entire "advanced displays" subsystem — five server-side panels
(system overview, power budget, FDIR/alarm panel, contact-pass scheduler,
procedure status), seven backend routes, and ~470 lines of client renderer
(`static/displays.js` + `displays.css`) — that **no live page ever loads**, so
an operator can never see or trigger any of it. Worse, one of its routes
crashes when called.

Evidence:

- `static/index.html` never references `displays.js` or `displays.css`
  (grep returns no match). `displays.js:467` constructs
  `const displayPanels = new DisplayPanels()` and defines
  `initSystemOverview/initPowerBudget/...`, but nothing ever calls those
  `init*` methods. The only intended bootstrap
  (`displayPanels.initSystemOverview(...)`) appears solely in
  `packages/smo-mcs/IMPROVEMENTS.md:382-386`, never in shipped code.
- The backing routes (`server.py:485-491`) —
  `/api/displays/{contact-schedule,power-budget,fdir-alarms,procedure-status,
  system-overview,alarm-trends}` and `/api/displays/alarms/{id}/ack` — have no
  consumer other than the dead `displays.js`.
- **Latent crash:** `_handle_procedure_status_display` (`server.py:1948`) calls
  `self._procedure_runner.get_status()`, but `ProcedureRunner` defines only
  `status()` (`procedure_runner.py:179`) — no `get_status` exists anywhere. So
  `GET /api/displays/procedure-status` raises `AttributeError` (HTTP 500) for
  any caller (e.g. curl), and would crash the panel the moment it were wired.
- The panels also render permanent placeholder data because their mutators are
  never called (covered under defect #20): `system_overview.update_subsystem_health`,
  `fdir_alarm_panel.update_s12_rules/update_s19_rules/set_fdir_level`,
  `power_budget` per-subsystem values, and the contact scheduler's key mismatch
  (`aos_time`/`los_time`/`max_elevation` vs the produced `aos`/`aos_utc`/
  `max_elevation_deg`).

## Severity

**Major** — a substantial, finished feature set is entirely inaccessible to
operators, and the one route reachable by direct HTTP is broken. Either it
should be wired up (after fixing the crash and the placeholder data), or
removed so the codebase doesn't imply capabilities it doesn't deliver.

## Requirements for the fix

1. Decide: wire the advanced displays into the live UI, or remove them.
2. If wiring: `index.html` must load `displays.js`/`displays.css`, provide the
   container elements, and bootstrap the `init*` methods.
3. `_handle_procedure_status_display` must call an existing method.
4. The panels must be fed real data, not placeholders (see defect #20).

## Suggested implementation

- If keeping: add `<script src="/static/displays.js">` + `<link ... displays.css>`
  and container `<div>`s to `index.html`, add an init bootstrap, and fix
  `server.py:1948` → `self._procedure_runner.status()`. Then feed live
  telemetry/FDIR/S12/S19 state into the display mutators.
- If removing: delete `displays.js`, `displays.css`, the `displays/` panel
  modules that are otherwise unused, and routes `server.py:485-491`.

## Acceptance criteria

- Either the five panels render with live, changing data in the running MCS,
  or the dead subsystem (JS, CSS, routes, unused panel modules) is removed.
- `GET /api/displays/procedure-status` returns 200 with real status (no 500).
- A test exercises each retained display route end to end.

## Affected areas

- `packages/smo-mcs/src/smo_mcs/static/index.html`
- `packages/smo-mcs/src/smo_mcs/static/displays.js`, `displays.css`
- `packages/smo-mcs/src/smo_mcs/server.py` (`:485-491`, `:1948`)
- `packages/smo-mcs/src/smo_mcs/displays/*.py`

## Related

- Defect #20 (MCS dead/unwired code) covers the placeholder-data mutators and
  the unused `displays/engine.py` + `displays/widgets.py` config-driven engine.
- Defect #6 (no parameter-watch widget) — the missing operability surface this
  subsystem was partly meant to address.

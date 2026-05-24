## Summary

Several fully-implemented planner REST endpoints are not reachable from either
planner UI (`static/index.html`, served at `/`, port 9091; or
`static/index-wide.html`, served at `/wide`). They are implemented and in some
cases tested, but no button or `fetch()` ever calls them:

- **Per-subsystem constraint checks (5 endpoints):**
  `/api/constraints/power`, `/aocs`, `/thermal`, `/data-volume`, `/conflicts`
  (`server.py:130-139`, handlers `_handle_check_power/aocs/thermal/data_volume/
  conflicts` at `server.py:632-694`, backed by real checkers in
  `constraint_checkers.py`). Grep of both HTML files and the whole repo finds
  zero references to these paths. Only the aggregate `/api/constraints/validate`
  (the "Validate Constraints" button, `index.html:2144`) is used, so the
  per-subsystem drill-down is dead API surface.
- **Pass-relative scheduling:** `POST /api/schedule/pass-activity`
  (`server.py:116` → `_handle_pass_activity` at `server.py:453` →
  `schedule_pass_activity` at `activity_scheduler.py:176`, which validates that
  the activity fits within AOS/LOS). No UI control posts to it; the prior repo
  audit (`docs/gap_analysis/planner_ui_audit.md:111`) already notes it is "not
  called by current UI". "Schedule N minutes after AOS of pass X" is a headline
  planning feature that is invisible to operators.
- **Activity update / state transition:** `PUT /api/schedule/{id}`
  (`server.py:110` → `_handle_update_activity` at `server.py:363`, supports
  PLANNED→VALIDATED→… transitions and field edits). Neither HTML file issues a
  PUT (only DELETE, `index.html:1786`), so activity state can only ever advance
  via the upload path; operators cannot manually set VALIDATED/CANCELLED, etc.
- **Imaging targets list:** `GET /api/imaging/targets` (`server.py:122`) is
  called only by `index-wide.html:1552`; the live `index.html` never shows the
  configured target list.

## Severity

**Major** — multiple finished planning capabilities (constraint drill-down,
pass-relative scheduling, activity state management) are unreachable from the
tool operators actually use. This is dead/disconnected API surface that implies
functionality the UI doesn't deliver.

## Requirements for the fix

1. Each endpoint should either be exposed via an appropriate UI control, or be
   removed if redundant.
2. Pass-relative scheduling and activity state transitions in particular are
   worth surfacing — they are core planning operations.

## Suggested implementation

- Add a constraints drill-down (per-subsystem tabs/panels) that calls the five
  `/api/constraints/*` endpoints, or remove them and rely on `/validate`.
- Add a pass-relative scheduling form to the activity modal (the UI already
  lists passes), posting to `/api/schedule/pass-activity`.
- Add activity state-change controls that issue `PUT /api/schedule/{id}`.
- Surface the target list (`/api/imaging/targets`) in the primary `index.html`,
  or accept it as wide-display-only and document that.

## Acceptance criteria

- Every retained planner route is reachable from a UI control and exercised by
  a test; removed routes are deleted along with their handlers.
- An operator can schedule a pass-relative activity and change an activity's
  state from the UI.

## Affected areas

- `packages/smo-planner/src/smo_planner/server.py` (routes `:110`, `:116`, `:122`, `:130-139`; handlers)
- `packages/smo-planner/src/smo_planner/static/index.html`, `static/index-wide.html`
- `packages/smo-planner/src/smo_planner/constraint_checkers.py`, `activity_scheduler.py`

## Related

- Defect #15 (full validation unreachable) — the same disconnect between
  implemented planner logic and the wired UI.

## Summary

The planner's most complete feasibility check is unreachable; the endpoint
operators actually hit runs a much weaker subset. `validate_pass_plan`
(`activity_scheduler.py:259-360`) performs name-conflict detection, time-overlap
detection (`check_time_overlap`), pre-condition evaluation
(`check_pre_conditions`), pass-boundary checks, and power/data budget warnings.
But the wired route `GET /api/schedule/validate` (`server.py:113`,
`_handle_validate_schedule` at `server.py:437`) calls
`self._scheduler.validate_schedule(contacts)` (`activity_scheduler.py:362`),
whose body only loops `self.check_conflicts(activity)` (name conflicts). Grep
shows `validate_pass_plan` is called only from
`tests/test_planner/test_pass_scheduling.py` — no route, no UI, no other
package.

A direct consequence: the **pre-condition evaluation engine is dead in
production**. `check_pre_conditions` (`activity_scheduler.py:380`) parses
telemetry-gated preconditions (e.g. `eps.bus_voltage > 24.0`), the schema
supports them (`schemas.py:330 pre_conditions: list[str]`), activities carry
them (`activity_scheduler.py:52`), and the UI even displays them
(`index.html:1888`) — but the only caller of `check_pre_conditions` is
`validate_pass_plan`, which is never wired. The planner also has no live
telemetry client, so no production code ever passes a `telemetry` dict to
evaluate against.

## Severity

**Major** — operators click "Validate" and receive only name-conflict
detection. Time overlaps, pass-boundary breaches, and preconditions —
explicitly authored and surfaced in the UI as if meaningful — are never
checked. This gives false confidence that a plan is feasible.

## Requirements for the fix

1. The validation endpoint must run the comprehensive checks (time overlap,
   pass-boundary, preconditions, budget warnings), not just name conflicts.
2. Pre-conditions must actually be evaluated, which requires the planner to
   obtain the telemetry values they reference.

## Suggested implementation

- Point `_handle_validate_schedule` at `validate_pass_plan`, passing the
  contacts and budget context it needs; or add a new comprehensive route and
  have the UI call it.
- Give the planner a telemetry source (poll the MCS/sim for the parameters
  named in preconditions) so `check_pre_conditions` has real values; until
  then, report preconditions as "unevaluated" rather than implying they passed.

## Acceptance criteria

- "Validate" reports time-overlap and pass-boundary violations, not only name
  conflicts.
- Preconditions are evaluated against real telemetry (or clearly flagged as
  unevaluated when no telemetry is available).
- Tests cover an overlapping-activity plan and a failing-precondition plan,
  asserting the endpoint surfaces both.

## Affected areas

- `packages/smo-planner/src/smo_planner/server.py` (`_handle_validate_schedule`)
- `packages/smo-planner/src/smo_planner/activity_scheduler.py` (`validate_schedule` vs `validate_pass_plan`, `check_pre_conditions`)
- Planner telemetry ingestion (new) for precondition evaluation

## Related

- Defect #16 (planner endpoints with no UI) covers the broader pattern of
  implemented planner capability that the frontend never reaches.

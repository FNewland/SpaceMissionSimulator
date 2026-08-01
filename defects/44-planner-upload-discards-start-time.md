# Defect 44 — Planner "Upload to MCS" discarded the scheduled start_time

## Summary

The planner's upload path (`smo-planner/server.py` `_handle_upload_schedule`)
POSTed only `{name, steps, procedure_ref, step_by_step}` to the MCS
`/api/procedure/load` endpoint. The activity's constraint-checked
`start_time` (daylight, contact, thermal windows) was never transmitted: the
ProcedureRunner had no concept of scheduled execution and ran the sequence
whenever the operator pressed start. Nothing bound execution to the planned
pass and nothing used the simulator's fully-working on-board S11 scheduler
(which was unreachable from the ground anyway — see defect #46).

## Severity

**Major** — the planner's core output (time-correct activity scheduling) was
thrown away at the planner→MCS interface; "planned" imaging/dump activities
executed at arbitrary operator-initiated times.

## Status

**Fixed**:

1. New MCS endpoint `POST /api/schedule/upload-s11`
   (`smo-mcs/server.py` `_handle_s11_schedule_upload`): converts an activity
   `{name, start_time, steps}` into TC(11,4) packets — each command step
   becomes `exec_time_cuc(4) + <complete embedded TC packet>` fired at
   `start_time` + cumulative `wait_s` offsets (+1 s ordering gap between
   commands). `wait_for`/telemetry-check steps are reported back as skipped
   (they cannot run on board). UTC→CUC conversion (`_utc_to_obc_cuc`)
   anchors on OBC time TM (param 0x0309) with a shared-TIME_EPOCH fallback.
2. Planner `_handle_upload_schedule` now sends `start_time` in every upload
   and supports `mode: "s11"` (default, time-tagged on-board schedule) and
   `mode: "immediate"` (legacy ProcedureRunner path). The chain is
   documented in the planner `server.py` module header.
3. The immediate path also preserves the time: `ProcedureRunner.load()`
   accepts/reports `scheduled_start_time` in `status()`.
4. Planner UI `uploadToMCS()` offers the choice (S11 default).

## Acceptance criteria

- [x] Default upload converts each command step into an S11.4 insert at the
      activity start time (tests/test_planner/test_upload_start_time.py,
      tests/test_mcs/test_s11_builder.py).
- [x] `start_time` reaches the MCS verbatim in both modes.
- [x] Scheduled TCs fire at the planned time in the simulator and execute
      with S1 verification
      (tests/test_integration/test_planner_mcs_s11_chain.py).
- [x] Immediate procedure path still works, now carrying start_time.

## Affected areas

- `packages/smo-planner/src/smo_planner/server.py`
- `packages/smo-planner/src/smo_planner/static/index.html`
- `packages/smo-mcs/src/smo_mcs/server.py`
- `packages/smo-mcs/src/smo_mcs/procedure_runner.py`

## Related

- Defect #45 (wrong func_ids — the uploaded content was also wrong),
  #46 (S11.4 ground builder produced unexecutable packets).

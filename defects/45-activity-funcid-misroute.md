# Defect 45 — Activity command sequences carried func_ids that route to the wrong subsystems

## Summary

The engine routes S8.1 functions by numeric range
(`smo-simulator/service_dispatch.py` `_handle_s8`: 0-15 AOCS, 16-25 EPS,
26-39 payload, 40-49 TCS, 50-62 OBDH, 63-78 TTC). The planner's
`configs/eosat1/planning/activity_types.yaml` and
`smo-planner/imaging_planner.py` used fabricated func_ids landing in the
wrong ranges:

- `imaging_pass` / imaging planner: `0x14`(20) "PAYLOAD_IMAGER_ON" → EPS
  power_line_off no-op; `0x16`(22) "PAYLOAD_START_IMAGING" → EPS
  switch_load no-op. The follow-up `wait_for payload.imaging_active`
  polled a telemetry parameter that does not exist, so the step always
  timed out. Capture lat/lon in the `parameters` dict were silently
  dropped by `ProcedureRunner._build_data_hex` (only a 1-byte `params`
  list was encoded) — see item C.
- `data_dump`: `0x32`(50) "TTC_HIGH_RATE" → OBDH set-mode.
- `calibration`: `0x08` "AOCS_CALIBRATE_ST" → RW speed-bias no-op (needs a
  5-byte arg).
- `thermal_rebalance`: `0x28`(40) → forced the battery heater ON.
- `load_shedding`: `0x1A`(26) "EPS_LOAD_SHED_ENABLE" → payload set_mode.
- `fdir_recovery`: `0x1B`(27) "EPS_SAFE_MODE_INIT" → payload set_scene;
  AOCS safe-mode command sent no mode byte.
- `software_upload`: S6.2 with no address/data — silent no-op.

Net effect: an "imaging pass" run through the whole chain performed zero
payload actions and failed its step; a "data dump" poked OBDH's mode.

## Severity

**Major** — the flagship planner activities commanded the wrong subsystems
or nothing at all, despite green transport/verification.

## Status

**Fixed** at the source, verified against the dispatcher routing table:

- `imaging_pass`/imaging planner → 26 (0x1A, payload set_mode, arg 2 =
  IMAGING) then 28 (0x1C, payload capture, two float32 lat/lon);
  `wait_for` now polls the real `payload.mode` parameter (0x0600).
- `data_dump` → 65 (0x41, TTC set TM rate; no arg = 64 kbps high rate).
- `calibration` → 13 (0x0D, AOCS gyro calibration).
- `thermal_rebalance` → 45 (0x2D, TCS auto-mode, circuit 0).
- `load_shedding` → 25 (0x19, EPS emergency load shed, stage 1).
- `fdir_recovery` → 0 with mode byte 1 (AOCS SAFE_BOOT) + 83 (0x53, EPS
  set-mode safe).
- `software_upload` → S6.2 with a writable address + data payload.
- `ProcedureRunner._build_data_hex` (and the MCS S11 upload path) now
  encode the imaging `parameters` dict as two big-endian float32 lat/lon
  after the func id — matching the payload capture handler
  (`service_dispatch.py` func 28).

## Acceptance criteria

- [x] Table-driven test over ALL activity types: every S8 func routes to a
      subsystem in the activity's `requires_subsystems`, and the real
      ServiceDispatcher delivers each command to that model
      (tests/test_planner/test_activity_funcids.py).
- [x] Imaging capture lat/lon survive into the TC data field and reach the
      payload model (tests/test_integration/test_planner_mcs_s11_chain.py).
- [x] No `wait_for` references a nonexistent telemetry parameter.

## Affected areas

- `configs/eosat1/planning/activity_types.yaml`
- `packages/smo-planner/src/smo_planner/imaging_planner.py`
- `packages/smo-mcs/src/smo_mcs/procedure_runner.py`
- `tests/test_planner/test_imaging_planner.py` (updated expectations)

## Related

- Defects #37/#38 (earlier func-id drift in S19/S8) — same root cause
  class: hand-maintained func tables (see DI-09).

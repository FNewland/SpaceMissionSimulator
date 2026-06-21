# Defect Register — SpaceMissionSoftware

Defects #1–8 were identified during the 2026-04-06 simulator review session.
Defects #9–22 were identified during the 2026-05-24 "dead / unwired code" audit,
which traced every backend capability to a UI/interface and every UI control to a
real backend, across the live suite (`packages/smo-*`) and `tools/`. Each entry
below (except the fileless fixed item #8) has a corresponding body file in
`defects/` which is used verbatim by `scripts/upload_and_file_defects.sh` when
creating GitHub issues.

| #   | Severity | Status     | Title                                                                       |
| --- | -------- | ---------- | --------------------------------------------------------------------------- |
| 1   | Major    | Open       | OBDH "Buffer Fill – HK TM" parameter exceeds 100% (observed 353%)            |
| 2   | Major    | Fixed      | HK_Store sized for <1 orbit of housekeeping (5000 → 18000)                   |
| 3   | Major    | Fixed      | TM packets routed to S15 stores during bootloader (sw_image == 0)            |
| 4   | Major    | Open       | AOCS mode defaults to NOMINAL(4) at construction — "DETUMBLE at start"       |
| 5   | Critical | Open       | `sw_image` (0x0311) and `phase` (0x0129) not in any HK SID — unobservable    |
| 6   | Major    | Open       | MCS has no generic parameter-watch widget / no S20 client                    |
| 7   | Minor    | Open       | Fill % > 100 should be impossible for a circular store — UI contract bug    |
| 8   | Major    | Fixed      | Instructor display shows only ~30 params; no subsystem internals visible     |
| 9   | Critical | Fixed      | Scenario subsystem non-functional — `ScenarioEngine` never instantiated     |
| 10  | Critical | Fixed      | Breakpoint save/load not wired to UI — SAVE no-ops, no LOAD control          |
| 11  | Major    | Fixed      | Heater control broken — OBC heater cmd no-ops; no heater UI affordance       |
| 12  | Major    | Fixed      | "CLEAR ALL FAILURES" button does nothing (HTTP 403 + unhandled WS)           |
| 13  | Major    | Partial    | MCS advanced displays unreachable (`displays.js` never loaded) + 500 crash   |
| 14  | Major    | Partial    | MCS Procedure Builder produces unrunnable procedures; steps skipped open     |
| 15  | Major    | Fixed      | Planner full validation unreachable — only weak name-conflict check wired    |
| 16  | Major    | Open       | Planner backend endpoints (constraints, pass-activity, PUT, targets) no UI   |
| 17  | Major    | Open       | smo-common orphaned PUS service parser — duplicated/diverged in MCS          |
| 18  | Major    | Open       | RF "via GNU Radio" never invoked — entire `gnuradio/` package is dead        |
| 19  | Major    | Open       | Radio dashboard panels (link budget/channel/spectrum/eye) show placeholders  |
| 20  | Minor    | Open       | MCS dead/unwired code & data-shape bugs (consolidated cleanup register)      |
| 21  | Minor    | Open       | Planner + smo-common dead/orphaned code (consolidated cleanup register)      |
| 22  | Minor    | Open       | RFsim + gateway + tools dead/unwired code (consolidated cleanup register)    |
| 23  | Major    | Fixed      | Subsystem-generated events never reach operator (`_engine` unset, queue undrained) |
| 24  | Major    | Fixed      | AOCS reaction-wheel thermal signature broken (temps not in HK; no rise on seizure) |
| 25  | Major    | Fixed      | AOCS `aocs_wheel_failure` scenario injects unhandled `wheel_failure` (no-op)  |
| 26  | Major    | Fixed      | AOCS MAG_SELECT cannot select redundant magnetometer (Mag B)                 |
| 27  | Major    | Fixed      | FDIR/load-shed callbacks invoke commands models don't handle (EPS safing, TTC power) |
| 28  | Major    | Fixed      | TTC `uplink_loss` failure does not suppress the downlink                      |
| 29  | Major    | Fixed      | OBDH bus-failure isolation is cosmetic (`is_subsystem_reachable` never consulted) |
| 30  | Major    | Fixed      | OBDH watchdog inverted; injected watchdog reset can't fire in nominal mode    |
| 31  | Major    | Fixed      | TCS advanced thermal commands inert (decontamination, duty-limit, setpoints)  |
| 32  | Minor    | Open       | Subsystem dead/inert commands & state (consolidated cleanup register)         |
| 33  | Minor    | Open       | Subsystem computed-but-unobservable telemetry (consolidated cleanup register) |
| 34  | Major    | Fixed      | Failure-script scenarios never stop at end of duration                       |
| 35  | Major    | Fixed      | Breakpoint save/load does not restore orbital position or eclipse state       |
| 36  | Major    | Fixed      | Eclipse / sunlight state never displayed — MCS reads keys sim never produces  |
| 37  | Critical | Fixed      | S19 rules mis-map S8 func_ids — autonomous OBC application boot at startup     |
| 38  | Major    | Fixed      | S8 function-management commands 100–107 (legacy EPS/TCS quick-actions) dropped |
| 39  | Minor    | Fixed      | S8 func 49 TCS_GET_THERMAL_MAP acknowledges but returns no telemetry           |
| 40  | Major    | Fixed      | tc_catalog.yaml on/off field names parse as booleans and abort the catalog load |

Severity key:
- **Critical** — mission cannot operate safely without this
- **Major** — significant operational impact, workaround exists
- **Minor** — cosmetic or convenience, no operational impact

Fixed items are retained in the register so the associated regression tests
and rationale are captured in the issue tracker for audit.

Status note (2026-05-24 remediation pass): the **entire smo-simulator package is
remediated** — defects #9, #10, #11, #12, #23, #24, #25, #26, #27, #28, #29, #30,
#31 are all **Fixed**, covered by 26 regression tests in
`tests/test_remediation_phase01.py` (full simulator + acceptance suites green:
585 passed). Additional **Fixed**: #15 (planner validation now runs `validate_pass_plan`).
**Partial**: #13 (procedure-status 500 crash fixed via `status()`; wiring the
display *panels* into the live page + feeding real data remains), #14 (runner now
executes builder `wait`/`tlm_check`/`go_nogo`/`command` steps with comparison
operators and fails closed on unknown steps; bridging *saved custom* procedures
into the runner's load path remains). Still **Open**: #16 (planner endpoint UI
controls), #17 (unify PUS parser), #18, #19 (RF bridge — needs GNU Radio to test),
#20, #21, #22, #32, #33 (cleanup registers). Remediation follows
`REMEDIATION_PLAN.md`. Note: running the test suite needs `pytest-asyncio`
installed (some MCS tests are async).

Status note (2026-06-20 session): three bugs reported by Franz are now **Fixed** —
scenario scripts never auto-stopping (#34), breakpoint save/load not restoring the
orbital position / eclipse state (#35) and the matching MCS eclipse-display
key mismatch (#36), and the OBC autonomously booting into its application at
startup via mis-mapped S19 rules (#37). A dispatch/catalog audit during the same
session found and fixed three dead command paths: dropped S8 legacy quick-actions
100–107 (#38), the S8 func-49 thermal map that acknowledged but never downlinked
(#39), and the `tc_catalog.yaml` `on`/`off` field names that parsed as YAML
booleans and aborted the **whole** catalog load (#40, now 182 commands loading).
A new **ADCS per-axis control-torque-gain feature** (S6 memory load/dump:
`AOCS_SET_TORQUE_GAIN_X/Y/Z`, `AOCS_SET_TORQUE_GAINS_ALL`,
`AOCS_DUMP_TORQUE_GAINS`) was also added — this is a feature, **not** a defect.
Operational caveat (repeated): all of these fixes are **uncommitted working-tree
edits** relative to git HEAD, so a running simulator must be **restarted** to pick
them up, and breakpoint files saved before #35 lack the `orbit_utc` field. The
GitHub-issue filing script `scripts/upload_and_file_defects.sh` would need its
`ISSUES` array extended for 34–40 before these can be filed as issues.

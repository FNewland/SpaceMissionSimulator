# SMO Suite — Remediation Plan (Defects 09–33)

This plan sequences the fixes for the 25 defects raised in the 2026-05-24
"defined-but-unimplemented / created-but-not-connected" audit (files
`defects/09-*.md` … `defects/33-*.md`; see `DEFECTS.md` for the register). It is
written to be executed top-to-bottom, with review checkpoints. Defects 01–08 are
out of scope here (separate prior register).

## Guiding principles

1. **Fix shared root causes before their symptoms.** Several defects are
   downstream of two engine-level gaps (the instructor-command dispatcher and the
   model→operator event path). Fixing those first shrinks or closes multiple
   defects at once and avoids duplicated work.
2. **Reject, don't silently no-op.** A recurring failure mode is commands/actions
   that return success while doing nothing. Where a capability is genuinely
   out of scope, make it return a clear failure (and log it) rather than a false
   success. Add a catch-all log for unknown command/failure names so future drift
   is loud.
3. **Every behavioural fix ships with a regression test.** The repo already has a
   `tests/` tree (pytest); each fix adds or tightens a test. Config-only changes
   (HK SID additions) get an observability assertion.
4. **Config and physics changes are reviewed against the scenario they unblock.**
   Many fixes exist to make a specific training scenario trainable; the
   acceptance check is "run that scenario and observe the intended cue".

## Cross-cutting root causes (do these first)

**R1 — Harden the engine instructor-command dispatcher.**
`engine._handle_instructor_cmd` (`engine.py:1525-1608`) silently drops command
`type`s it has no branch for, which is the direct cause of three defects. Add the
missing branches (`save_breakpoint`, `load_breakpoint`, `start_scenario`,
`stop_scenario`, `failure_clear_all`) and a final `else` that logs an unknown
type at WARNING. Unblocks **#9, #10, #12** and prevents recurrence.

**R2 — Wire the model→operator event path.**
Set `model._engine = self` when the engine creates models (around
`engine.py:90`), add a run-loop drain that forwards each model's queued events
through `engine._emit_event(...)` (normalising the per-model event shapes), and
fix the stray `self._engine._event_queue` reference at `service_dispatch.py:755`.
This is **#23** itself and is a precondition for the operator-visible half of
**#24, #25, #27, #29, #30, #31** (their state-transition events). Do it before the
subsystem-physics fixes so those fixes can be verified by the event they emit.

**R3 — Log failed FDIR/load-shed callbacks.** The engine swallows callback
exceptions/failures (`engine.py:878-883`). Have it log when a callback command
returns `success: False`. Small change; surfaces **#27** and future mismatches.

## Sequenced phases

| Phase | Defects | Rationale / dependency |
| --- | --- | --- |
| 0 — Foundations | R1, R2, R3, #23 | Shared root-cause fixes; unblock the rest |
| 1 — Critical | #9, #10 | Wholly-broken headline features; depend on R1 (and #9 on R2 for events) |
| 2 — Simulator Majors (control) | #11, #12, #26, #27, #28, #31 | False-success / inert-command fixes; #12 largely lands with R1; #27 with R3 |
| 3 — Simulator Majors (FDIR/scenario realism) | #24, #25, #29, #30 | Make flagship failure scenarios trainable; events depend on R2 |
| 4 — MCS Majors | #13, #14 | Operator-UI feature gaps; independent of the simulator phases |
| 5 — Planner / common Majors | #15, #16, #17 | Planner validation + endpoint wiring; shared-library parser |
| 6 — RF Majors | #18, #19 | Optional RF bridge; lowest operational priority among Majors |
| 7 — Minor cleanup registers | #20, #21, #22, #32, #33 | Dead-code removal, observability (HK SID) additions, write-only knobs |

**Checkpoint A** after Phase 1 (per your request): pause for review once R1/R2/R3
and the two Criticals (#9, #10) are done and tested.
**Checkpoint B** after Phase 3 (simulator fully remediated).
**Checkpoint C** after Phase 6 (all Majors done) before the Minor cleanup.

## Phase detail

### Phase 0 — Foundations
- **R1** `engine.py:_handle_instructor_cmd`: add branches + unknown-type WARNING.
- **R2 / #23** `engine.py` model-creation loop + run-loop drain + `_emit_event`
  normalisation; standardise the six models' event push shape (today AOCS pushes
  an object, EPS a tuple, others dicts); fix `service_dispatch.py:755`.
- **R3** `engine.py:878-883`: log callbacks that return `success: False`.
- *Tests:* unknown instructor type logs and is ignored safely; injecting a TCS
  `HEATER_STUCK_ON` (or similar) produces a downlinked S5 event.

### Phase 1 — Critical (Checkpoint A)
- **#9 Scenario subsystem:** instantiate `ScenarioEngine` in `engine.__init__`,
  load from the config scenarios dir, tick it in the run loop; implement
  `start_scenario`/`stop_scenario` (via R1) → `ScenarioEngine.start/stop`; make
  `/api/scenarios` return the real list; surface progress + debrief to the
  instructor UI. *Test:* load a sample scenario, start, tick, assert a timed event
  fired and a debrief was produced.
- **#10 Breakpoints:** point the instructor UI SAVE at `/api/breakpoint/save`
  (route already calls `BreakpointManager.save`), add a per-row LOAD control →
  `/api/breakpoint/load`; (or use the now-handled `save_breakpoint`/
  `load_breakpoint` instructor commands from R1). *Test:* save → advance → load →
  state matches snapshot.

### Phase 2 — Simulator Majors (control surface)
- **#11 Heaters:** honour `htr_obc_manual` in `tcs_basic.tick` before overwriting
  `htr_obc` from EPS PL6; audit every heater circuit so a "success" reply reflects
  real state; add a heater control affordance (operator UI via TC path, or
  permitted instructor control).
- **#12 Clear-all-failures:** lands mostly via R1 (add `failure_clear_all` branch
  → clear-all), or repoint the UI to `failure_clear` with no id; remove the 403
  path.
- **#26 MAG_SELECT:** send a concrete A/B selector from `_route_aocs_cmd` func 7;
  accept numeric `unit`; make selection observable.
- **#27 FDIR/load-shed callbacks:** fix command names (`safe_mode_eps` →
  `set_eps_mode`; `ttc_power_level` → `set_tx_power`); make `eps_mode` drive a
  real protective effect and route an S8 func to `set_eps_mode`.
- **#28 uplink_loss:** gate the TTC lock chain on `not uplink_lost`; align the
  scenario briefing with whether the whole link or only the uplink drops.
- **#31 TCS advanced commands:** drive FPA toward `decontam_fpa_target_c` when
  decontaminating; enforce heater duty-limit; make per-circuit setpoint/auto-mode
  either work or reject clearly; reconcile the −50/+50 default mismatch.
- *Tests:* one per defect asserting the commanded effect actually occurs (and
  unsupported commands return failure).

### Phase 3 — Simulator Majors (FDIR / scenario realism) (Checkpoint B)
- **#24 RW thermal:** add `0x0218`–`0x021B` to SID 2; model seized-wheel friction
  heating (and speed retention for the "stuck" case).
- **#25 wheel_failure:** map the scenario's `wheel_failure` to a handled failure
  (or add the branch); add the unknown-failure-name WARNING (mirrors R1).
- **#29 OBDH bus isolation:** consult `is_subsystem_reachable` in `_enqueue_tm`
  and command dispatch; restore on bus switch.
- **#30 OBDH watchdog:** invert the mode condition so it counts in nominal; make
  `watchdog_reset` deterministically reboot; tighten the acceptance test.
- *Tests:* run each affected scenario and assert the intended cue (temperature
  rise / loss of reachability / reboot) appears, including the S5 event via R2.

### Phase 4 — MCS Majors
- **#13 Advanced displays:** decide wire-up vs removal; if wiring, load
  `displays.js`/`.css` + bootstrap, fix `server.py:1948` `get_status()`→`status()`,
  and feed real data into the display mutators (closes the #20 placeholder items).
- **#14 Procedure Builder:** bridge save→runner; normalise builder step schema to
  the runner (`seconds`→`wait_s`, `tlm_check`→`wait_for` + operators, `go_nogo`
  handler); make `_execute_step` fail closed on unknown steps.

### Phase 5 — Planner / common Majors
- **#15 Validation:** wire `/api/schedule/validate` to `validate_pass_plan`; give
  the planner a telemetry source for pre-conditions (or mark unevaluated).
- **#16 Endpoints with no UI:** expose (or remove) the constraint, pass-activity,
  PUT-update, and imaging-targets endpoints.
- **#17 PUS parser:** unify on one parser (route MCS `tm_processor` through
  `parse_service_data`, extended for S12) or delete the orphan.

### Phase 6 — RF Majors (Checkpoint C)
- **#18 GNU Radio:** make the GR path selectable, or remove it and correct the
  start.sh/doc claims.
- **#19 Radio panels:** wire the link-budget/channel/spectrum/eye producers (and
  the `SpaceLinkChannel`/`LinkBudget` cluster) to live data, or remove the panels.

### Phase 7 — Minor cleanup registers
- **#20 / #21 / #22 / #32 / #33:** triage each item — wire, reject clearly, or
  delete; batch the HK SID observability additions (#33, and the #24 SID change)
  into one `hk_structures.yaml` change with one observability test per added SID.

## Test & verification strategy

- Run the existing `pytest` suite after each phase; no phase is "done" with a red
  suite.
- Behavioural fixes: add a focused regression test that fails before and passes
  after.
- Scenario-realism fixes (Phase 3): drive the relevant scenario YAML and assert
  the operator-visible cue (HK value, S5 event, reboot) — these double as
  acceptance tests for the defect.
- Config-only fixes: assert the parameter now appears in the relevant HK packet.
- Final pass: use a subagent to diff the whole change set against this plan and
  confirm each defect's acceptance criteria are met before marking it closed in
  `DEFECTS.md`.

## Notes & risks

- R2 changes the engine run loop and event flow — the highest-blast-radius change;
  do it behind the existing test suite and verify no duplicate/dropped events.
- Phase 2/3 physics changes alter telemetry values some tests may assert on;
  expect to update a few expected-value tests (legitimately).
- #13 and #18/#19 each carry a "wire vs delete" decision; default to wiring only
  if the feature is wanted, else delete to stop the code implying capability it
  doesn't deliver. Flag these for your call at the relevant checkpoint.

## Summary

On-board scripting was absent: no PUS Service 18 / OBCP capability existed
anywhere in the codebase, although the ops concept expects on-board automation.

`ServiceDispatcher` implemented S1, S3, S5, S6, S8, S9, S11, S12, S13, S15, S17,
S19 and S20 — service 18 was not in the `service == …` chain
(`service_dispatch.py:60-107`), not in the engine's `known_services` acceptance
set (`engine.py:_check_tc_acceptance`), not in
`configs/eosat1/mcs/pus_services.yaml`, and not in
`configs/eosat1/commands/tc_catalog.yaml`. A repository-wide search for
`obcp` / `S18` returned zero code hits, and no example scripts existed in
`configs/`.

The nearest substitutes each fell short of on-board scripting:

* **S11** time-tagged TCs — a flat one-shot queue, one TC per entry, no
  sequence, no relative timing.
* **S19** event-action — an "action" is exactly one *parameterless* S8 function
  call (`_s19_dispatch` → `_handle_s8(1, bytes([func_id]))`), so no sequences,
  arguments or delays.
* **MCS `ProcedureRunner`** — genuinely sequenced, but it runs on the *ground*,
  so every step needs live contact and the procedure dies at LOS.

Consequently no training exercise could demonstrate autonomous multi-step
behaviour (pass preparation, safe-ing, payload checkout) executing without
ground contact.

## Severity

**Major** — a whole ECSS service class expected by the operations concept was
missing, so the autonomous-operations part of the syllabus was unteachable. Not
a doc lie (nothing claimed OBCPs existed); a missing capability.

## Status

**Fixed** with a new S18 OBCP implementation:

1. **New `packages/smo-simulator/src/smo_simulator/obcp.py`** — `ObcpManager`:
   the procedure store plus a per-tick step interpreter. A procedure is an
   ordered list of steps, each `{delay_s (relative, sim seconds), tc (complete
   PUS TC packet)}`. Limits: 16 procedures × 32 steps.
2. **S18 handler added to `service_dispatch.py`** (`_handle_s18`, registered in
   `dispatch()`): `18,1` direct-load (serialized `proc_id + name + steps`
   blob), `18,2` delete, `18,3` activate/start (optional 1-byte argument),
   `18,4` stop/abort (`proc_id 0` = stop all), `18,6` status report →
   **TM(18,7)** (`count + [proc_id, status, current_step, n_steps]*`). Error
   paths route through the existing `_make_error_response`, so failures surface
   as normal S1.8 execution failures. The dispatcher's `threading.Lock` became
   an `RLock` because S18 handlers emit S5 events synchronously, and
   `_emit_event` re-enters the dispatcher via `trigger_event_action` on the
   same thread.
3. **Engine integration** (surgical: three edits) — `ObcpManager` instantiated
   in `__init__` next to `TCScheduler` and pre-loaded from
   `configs/eosat1/obcp/`; `self._obcp_manager.tick(dt_sim)` called from
   `_tick_once` immediately after the S11 scheduler, so procedures advance on
   **sim time** and respect the instructor's speed setting; service 18 and its
   subtypes `{1,2,3,4,6}` added to `_check_tc_acceptance`.
4. **Steps execute through the normal internal dispatch path**
   (`engine._dispatch_tc`), identical to uplinked TCs, so the bootloader
   allowlist, EPS power gate, bus-reachability gate, subtype validation and the
   full S1 verification chain all apply. Self-generated TCs are pre-validated
   against `_check_tc_acceptance` and tagged `source=OBCP` in the log, so a
   rejected step raises an event instead of vanishing into the
   malformed-packet log. A failed step aborts the remainder of the procedure.
5. **Events** via the existing `_emit_event` path (and therefore S19-triggerable
   and alarm-stored): `0x0700 OBCP_STARTED`, `0x0701 OBCP_COMPLETED`,
   `0x0702 OBCP_ABORTED`, `0x0703 OBCP_STEP_FAILED`, all added to
   `configs/eosat1/events/event_catalog.yaml`.
6. **Breakpoints** — `get_state`/`set_state` on `ObcpManager` (JSON-safe: step
   TCs hex-encoded) wired into `breakpoints.py` under the `obcp` key, following
   the `OnboardTMStorage` pattern. A procedure that was mid-run when a
   breakpoint was saved resumes at the same step index with the same remaining
   delay after a load.
7. **Example procedures** in `configs/eosat1/obcp/`, pre-loaded at boot like
   other config-driven stores: `01_pass_preparation.yaml` (TX line on, PA on,
   antenna deploy, beacon off, TM rate, transponder TX),
   `02_safe_mode_entry.yaml` (EPS SAFE, payload off + unpowered, cooler off,
   AOCS SAFE), `03_payload_checkout.yaml` (payload line on, STANDBY, IMAGING,
   capture, STANDBY). Every step uses a func_id genuinely routed by
   `service_dispatch.py` — asserted by a test, to avoid the fabricated-func_id
   disease found in `configs/eosat1/planning/activity_types.yaml`.
8. **Ground-segment reachability** — `configs/eosat1/commands/tc_catalog.yaml`
   gains `OBCP_LOAD` / `OBCP_DELETE` / `OBCP_START` / `OBCP_STOP` /
   `OBCP_STATUS_REPORT` (all names and field names quoted per defect #40), and
   `configs/eosat1/mcs/pus_services.yaml` gains the `s18` stanza. Start / stop /
   status / delete of pre-loaded procedures are fully usable from the MCS UI;
   a full multi-step load needs a serialized blob with embedded TC packets,
   which the catalog can only express as a raw hex `bytes` field (the same
   constraint as S11 `SCHEDULE_TC`) — so new procedures are normally added as a
   YAML file or built with `encode_load_blob` from a test/script. This is
   documented in `docs/OBCP_GUIDE.md`.

## Acceptance criteria

- [x] A procedure can be loaded via TC(18,1) and activated via TC(18,3).
- [x] Steps execute across engine ticks at their relative delays, on sim time
      (a 10× tick consumes 10 s of delay).
- [x] Steps route through the same dispatch as uplinked TCs, so acceptance
      rules apply and S1 verification is emitted.
- [x] TC(18,4) stops a run mid-procedure; remaining steps never execute and the
      procedure stays loaded so it can be restarted.
- [x] TC(18,6) returns a TM(18,7) status report listing every loaded procedure
      with run state and current step.
- [x] A bad step (unknown service, malformed bytes) raises `OBCP_STEP_FAILED`
      and aborts the procedure.
- [x] A running procedure survives breakpoint save + restore into a fresh
      engine and finishes correctly there.
- [x] The shipped example procedures load at boot and every step's func is
      verified implemented.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/obcp.py` (new — `ObcpManager`)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
  (added `_handle_s18` + registration; `Lock` → `RLock`)
- `packages/smo-simulator/src/smo_simulator/engine.py`
  (OBCP init + boot pre-load, tick hook, acceptance table)
- `packages/smo-simulator/src/smo_simulator/breakpoints.py` (save/restore)
- `configs/eosat1/obcp/01_pass_preparation.yaml`,
  `02_safe_mode_entry.yaml`, `03_payload_checkout.yaml` (new)
- `configs/eosat1/mcs/pus_services.yaml` (`s18` stanza)
- `configs/eosat1/commands/tc_catalog.yaml` (5 S18 commands)
- `configs/eosat1/events/event_catalog.yaml` (events `0x0700`–`0x0703`)
- `docs/OBCP_GUIDE.md` (new — format, TCs, examples, MCS usage)
- `tests/test_simulator/test_obcp_s18.py` (14 tests)

## Related

- Defect #37 (S19 rules mis-routed func_ids) — the same class of on-board
  autonomy; OBCP steps are protected against it by the acceptance pre-check and
  the func-implemented test.
- Defect #40 (tc_catalog boolean name fields) — the new S18 catalog entries
  quote every name and field name accordingly.
- Integration trace section 3 ("On-board scripting — ABSENT") is the source
  finding for this defect; its fix proposal 2 ("S18-lite") is what was built,
  extended with status reporting, breakpoint persistence and config-driven
  example procedures.

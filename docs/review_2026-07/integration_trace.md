# Integration Trace — Planner/MCS/Sim (items 3, 5, 6)

Repo root: `/sessions/rcw-01nwiaqbyeua69bcwofgtysz/mnt/SpaceMissionSimulation` (all paths below relative to it).
Date of trace: 2026-07-28. All line numbers verified against the mounted working copy.

---

## Section 1 — Planner → MCS integration (item 3)

**Verdict: PARTIAL** (a handful of read-only HTTP paths exist; every contact-display path is broken by data-contract or time-base mismatches; the MCS never sees the planner *schedule*; the sim disagrees with the planner about when contact even exists).

### 1.1 What interfaces exist

| Direction | Path | Code | Status |
|---|---|---|---|
| MCS → planner | `/api/contacts` proxy | `packages/smo-mcs/src/smo_mcs/server.py:530,1729-1746` (`_planner_api_base` at 275-279, `SMO_PLANNER_URL` or `:9091`) | transport works, payload contract broken (1.2) |
| MCS → planner | contact-schedule display | `server.py:2017-2038` (`_handle_contact_schedule`) | broken (1.2) |
| MCS UI → planner (direct, browser) | ground stations / ground track / spacecraft state for the world map | `packages/smo-mcs/src/smo_mcs/static/index.html:4308-4313` (`PLANNER_URL` from `/api/client-config`), 4390, 4409, 4461 | works (read-only display) |
| Planner → MCS | one-activity upload | `packages/smo-planner/src/smo_planner/server.py:520-562` → MCS `/api/procedure/load` | works as transport; see Section 2 for semantics |
| Planner → sim | sim-clock anchor | `smo-planner/server.py:63` (`http://host:8080/api/state`), refresh loop 138-151, anchor math 248-270 | works |

There is **no** file export/import, no S11 uplink path, no plan-to-pass binding, and no schedule feedback channel.

### 1.2 MCS does not know about planner schedules
- The planner exposes `GET /api/schedule` (`smo-planner/server.py:167`, handler 415-416), but **nothing in smo-mcs ever requests `/api/schedule`** — grep of `packages/smo-mcs/src` for `planner|9091` shows only the contacts proxy and client-config (`server.py:275-279, 527, 1723, 1729-1746, 2017-2038`). The MCS UI (`static/index.html`, `displays.js`) fetches contacts, ground track, ground stations, spacecraft state — never the activity schedule. Operators cannot see what is planned.
- No component consumes the plan autonomously. The only consumer is the manual, single-activity "Upload to MCS" button (Section 2).
- Activity lifecycle is open-loop: `ActivityState` has `EXECUTING=3, COMPLETED=4` (`smo-planner/src/smo_planner/activity_scheduler.py:20-25`) but the only automatic transition is PLANNED→UPLOADED on upload (`smo-planner/server.py:555-558`). The MCS never reports execution back; EXECUTING/COMPLETED are set only if a human PUTs a state change (`server.py:505`).

### 1.3 Contact data contract is broken in both MCS consumers
The planner emits windows keyed `aos`/`los` (**ISO strings**), `gs_name`, `max_elevation_deg`, `duration_s` (`packages/smo-common/src/smo_common/orbit/propagator.py:108-125` `contact_windows`, aggregated in `smo-planner/src/smo_planner/contact_planner.py:11-19`, served verbatim by `smo-planner/server.py:396-404` and passed through unmodified by the MCS proxy `smo-mcs/server.py:1735-1738`).

Both MCS consumers expect a **different schema**:
1. **UI contact timeline** (`smo-mcs/static/index.html:5805-5824`): reads `c.aos_utc`, `c.los_utc` (unix floats), `c.ground_station`. With the planner **up**, every pass has `aos_utc === undefined` → every window is skipped → the "CONTACT WINDOWS (24H)" panel is empty. The only schema that matches is the **synthetic fallback** generated when the planner is **down** (`smo-mcs/server.py:1748-1771`, `_build_synthetic_contacts` emits `aos_utc/los_utc/ground_station`) — i.e. the panel renders *fabricated* passes only when the planner is unreachable.
2. **Contact-schedule display** (`smo-mcs/src/smo_mcs/displays/contact_pass_scheduler.py:61-75`): reads `p.get("aos_time")`, `los_time`, `ground_station`, `max_elevation` — all absent from planner data — so every real pass defaults to `aos=now, los=now+600, 45°, "Unknown"`.

### 1.4 Time-source comparison
- **Sim engine**: own sim clock in the propagator (`smo-common/orbit/propagator.py:70` `_sim_utc`, stepped by `advance()` in `engine.py` `_tick_once` at `smo-simulator/src/smo_simulator/engine.py:847`), epoch from `configs/eosat1/orbit.yaml:7` (`t0_epoch: 2026-03-10`), rate = 1 Hz × instructor speed.
- **Planner**: correctly slaved to the sim clock — anchors on `:8080/api/state` and extrapolates `anchor_sim_time + (wall-now − anchor_wall)×speed` (`smo-planner/server.py:44-73, 248-270`); `VV_FINDINGS_2026-06.md:12` confirms this loop.
- **MCS**: the contact displays use **wall clock** — `Date.now()/1000` for the timeline window and now-marker (`smo-mcs/static/index.html:5778-5797`) and `time.time()` in `_handle_contact_schedule` (`smo-mcs/server.py:2016`). Since sim epoch is 2026-03-10 and wall is months later, even with matching field names all planner passes would classify as long-past. So the MCS contact display is doubly broken: schema **and** time base.

### 1.5 Contact-prediction inconsistency (planner: all stations; engine: Iqaluit only) — CONFIRMED
- Engine builds the propagator with both stations from `orbit.yaml:12-22` (Iqaluit first, Troll second) at `engine.py:103-115`, but the propagator computes live geometry **only for `_ground_stations[0]`**: `propagator.py:69` (`self._primary_gs = self._ground_stations[0] ...`) and `propagator.py:152-161` (`if self._primary_gs: ... in_contact = el_deg >= gs.min_elevation_deg`). `engine.py:849` copies that into `self._in_contact`.
- `uplink_active` / `downlink_active` require `self._in_contact` (`engine.py:792, 810`); out-of-contact TCs are rejected with S1.2 error 0x0005 (`engine.py:1061-1072`) and HK/TM is not downlinked.
- The planner computes windows for **all** stations (`contact_planner.py:14-18` loops `for gs in self._stations`).
- **Consequence**: every Troll pass the planner shows (and schedules `requires_contact` activities like `data_dump` into — `configs/eosat1/planning/activity_types.yaml:44-75`) is dead air in the sim: uplink rejected, no TM, and `data_dump`'s pre-condition `ttc.in_contact == true` can never be satisfied during a Troll-only window.

### 1.6 smo-gateway relevance
`packages/smo-gateway/src/smo_gateway/gateway.py:1-40` is a transparent TM/TC byte relay (one upstream sim/GSE, N downstream MCS clients, port 10025). It carries no planning semantics and is irrelevant to planner↔MCS integration.

### 1.7 Fix proposals (ranked by effort)
1. **Small**: Normalise the contact schema in the MCS proxy — map `aos`(ISO)→`aos_utc`(epoch), `gs_name`→`ground_station`, `max_elevation_deg`→both consumer spellings, in `_handle_contacts_proxy` and `_handle_contact_schedule` (`smo-mcs/server.py:1735, 2023`). ~30 lines; instantly makes both contact displays show real data.
2. **Small**: Use sim time, not wall time, for the timeline "now" (MCS already polls `/api/sim-state`; pass sim epoch to `renderContactTimeline` and to `ContactScheduler.get_next_passes`).
3. **Small/Medium**: Make the engine's contact flag multi-station — in `propagator._propagate_internal`, compute look angles for all stations and set `in_contact = any(...)` (keep `gs_elevation_deg` for the best station). ~20 lines in `propagator.py:150-161`; removes the planner/sim disagreement.
4. **Medium**: Add a "Mission Plan" panel to the MCS UI that polls planner `/api/schedule` and shows activity states; have MCS POST state transitions back (UPLOADED→EXECUTING→COMPLETED) when the procedure runner starts/finishes a loaded plan activity.
5. **Medium**: Make the planner's MCS URL configurable (`smo-planner/server.py:119` hardcodes `http://localhost:9090`; the MCS side already honours `SMO_PLANNER_URL`, the reverse does not exist).

---

## Section 2 — Mission-planning commanding chain end-to-end (item 5)

**Verdict: BROKEN** (a full transport chain exists and delivers packets, but the *content* is wrong for the flagship activities, the scheduled time is discarded, and the S11 on-board schedule is unloadable from the ground).

### 2.1 The chain that exists
1. Planner UI button **"Upload to MCS"** — `smo-planner/static/index.html:901` (button), `1866-1895` (`uploadToMCS()`): takes **only the first** PLANNED activity, POSTs `{activity_id, step_by_step}` to planner `/api/schedule/upload`.
2. Planner server `_handle_upload_schedule` (`smo-planner/server.py:520-562`): fetches the activity's `command_sequence` and POSTs `{name, steps, procedure_ref, step_by_step}` to `MCS /api/procedure/load` (`self._mcs_url` hardcoded `http://localhost:9090`, line 119). Marks activity UPLOADED.
3. MCS `_handle_proc_load` (`smo-mcs/server.py:1430-1457`) → `ProcedureRunner.load` (`smo-mcs/src/smo_mcs/procedure_runner.py:73-98`).
4. Operator must open the MCS **Procedures tab** and press start (`/api/procedure/start`, `server.py:504`). Runner executes steps (`procedure_runner.py:254-330`): commands via `_proc_send_command` (`server.py:1382-1412`) which builds a PUS packet (`tc_manager.build_command`), logs a verification entry, and writes it framed to the TC TCP socket (`connect_host:8001` per `server.py:90-96, 599-601`; via rfsim port 8011→8001 in RF mode, `smo-rfsim/src/smo_rfsim/config.py:54-58`, `bridge.py:124-220`).
5. Sim `server.py:32,43` receives TC on :8001 → engine `_drain_tc_queue` (`engine.py:1060-1072`, gated by `uplink_active`) → `_dispatch_tc` (`engine.py:1115`) → `ServiceDispatcher` → subsystem models. S1 acceptance/execution reports flow back; the MCS `track_verification` correlates them.

So packets genuinely reach the engine and are verified — **the transport is not the break**. The breaks:

### 2.2 Break A — scheduled time is discarded
The upload payload (`smo-planner/server.py:543-548`) contains no `start_time`; the runner has no concept of scheduled execution time and runs immediately when the operator presses start. The planner's carefully constraint-checked timing (daylight, contact, thermal — `constraint_checkers.py`) is thrown away at the interface. Nothing binds execution to the planned pass, and nothing uses S11 to schedule on board.

### 2.3 Break B — activity command sequences command the wrong subsystems
S8 func routing in the engine is by numeric range (`smo-simulator/src/smo_simulator/service_dispatch.py:461-487`): 0-15 AOCS, **16-25 EPS**, **26-39 payload**, 40-49 TCS, 50-62 OBDH, 63-78 TTC. `configs/eosat1/planning/activity_types.yaml` uses fabricated func_ids that do not exist in `configs/eosat1/commands/tc_catalog.yaml` and land in the wrong ranges:
- `imaging_pass` (`activity_types.yaml:17-28`): `0x14`(=20) "PAYLOAD_IMAGER_ON" → actually **EPS power_line_off** (engine bootloader comment `engine.py:~1146`: "19 power_line_on, 20 power_line_off"); with no line-index byte it is a silent no-op. `0x16`(=22) "PAYLOAD_START_IMAGING" → **EPS switch_load** (`service_dispatch.py:646-653`), no data → no-op. The following `wait_for payload.imaging_active==true` then times out (30 s) and the step fails. Real payload funcs are 26 set_mode / 27 set_scene / **28 capture** / 29 download (`service_dispatch.py:695-725`; `tc_catalog.yaml:703-709` PAYLOAD_CAPTURE func_id 28).
- `data_dump` (`activity_types.yaml:60-69`): `0x32`(=50) "TTC_HIGH_RATE" → actually **OBDH set-mode** (`_route_obdh_cmd` func 50). Real TM-rate func is TTC 65 (`_route_ttc_cmd`, "set tm rate").
- Contingency activities: `0x1A`(=26) "EPS_LOAD_SHED_ENABLE" → **payload set_mode**; `0x1B`(=27) "EPS_SAFE_MODE_INIT" → **payload set_scene** (`activity_types.yaml:222-262`). Only `0x01` AOCS_DESAT and `0x00` AOCS set-mode happen to be right.
- Same disease in the imaging planner's generated sequence: `imaging_planner.py:244-270` emits `func_id 0x14/0x16` with a `"parameters"` dict; the runner's `_build_data_hex` (`procedure_runner.py:396-405`) only reads a `"params"` **list** (1 byte each), so lat/lon are silently dropped even before the wrong-func problem.

Net effect: an "imaging pass" executed through the whole chain performs zero payload actions and reports a failed step; a "data dump" pokes OBDH's mode instead of raising the TM rate.

### 2.4 Break C — S11 exists on board but cannot be loaded from the ground
- On-board side **works**: `smo-simulator/src/smo_simulator/tc_scheduler.py` (insert/delete/enable/list/tick), wired at `service_dispatch.py:991-1021` (`_handle_s11`, subtype 4 = insert `exec_time(4) + full TC packet`, ack TM 11,5) and executed each tick at `engine.py:896-899` (`due_tcs = self._tc_scheduler.tick(current_cuc); self._dispatch_tc(tc_pkt)`), clock = OBC CUC (`engine.py:424-428`).
- Ground side is broken twice:
  1. The MCS "Schedule Absolute TC" builder (`smo-mcs/static/index.html:3296`, packed by `packPusFields` 3380-3400) sends `exec_time(4) + service(1) + subtype(1) + data` — but the engine stores `data[4:]` as the deferred packet (`service_dispatch.py:997-999`) and at fire time `_dispatch_tc` runs `decommutate_packet` on it (`engine.py:1115-1121`). A 2-byte `svc|st` blob is not a CCSDS/PUS packet → **"TC dropped: malformed packet"**; every UI-scheduled command dies silently at execution time (the operator saw a successful S1 ack of the *S11 request*, so it looks scheduled).
  2. The planner never touches S11 at all (no reference anywhere in `smo-planner`), and no ground tool converts UTC→OBC-CUC for `exec_time`, so even hand-crafting a valid embedded packet (possible via `SCHEDULE_TC`'s raw `tc_data` hex field, `tc_catalog.yaml:1096-1103`) requires the operator to know the OBC's CUC epoch.
- **Docs claim otherwise**: `docs/PUS_SERVICE_REFERENCE.md:452-472` and `938-944` describe automated imaging via "S11 subtype 11 (insert scheduled activity)" firing at AOS. In code subtype **4** inserts and subtype **11** is *delete-all* (`service_dispatch.py:997, 1008`) — the documented subtype would erase the schedule. Claimed-but-absent behaviour.

### 2.5 Fix proposals (ranked by effort)
1. **Small**: Fix `configs/eosat1/planning/activity_types.yaml` and `imaging_planner.py` func_ids to real ones (26/28 payload, 65 TTC rate, EPS 25 load-shed...), and make `_build_data_hex` encode the `parameters` dict for capture (2 floats, matching `service_dispatch.py:708-713`). Pure data + ~15 lines; makes the existing chain actually do what the labels say.
2. **Small**: Fix the MCS 11.4 builder to wrap svc/st/data into a complete PUS TC (client-side or, better, server-side in `_handle_pus_command`: detect S11.4 and re-embed via `tc_manager.build_command`), and add a UTC→CUC helper using `t0_epoch`.
3. **Medium**: Have the planner upload with `start_time`, and let the ProcedureRunner (or a new MCS scheduler) hold the procedure until sim-time T (it already polls `/api/sim-state`) — gives time-correct ground-initiated execution without S11.
4. **Medium/Large**: True S11 path: planner "commit plan" endpoint that converts each scheduled activity into S11.4 TCs (embedded full packets, CUC times) uplinked during the preceding contact; state transitions driven by S11.5 acks and S1.7s of the embedded packets.
5. **Trivial**: Correct `PUS_SERVICE_REFERENCE.md` S11 subtypes.

---

## Section 3 — On-board scripting (item 6)

**Verdict: ABSENT** (no S18/OBCP capability exists anywhere in the codebase; the nearest substitutes are S11 single time-tagged TCs, S19 single-function event-actions, and a *ground-side* procedure runner).

### 3.1 Search evidence
- `grep -ri "obcp"` across `packages/`, `configs/`, `docs/` → **zero code hits**; `"S18" / service 18` → zero (the only "18" in `service_dispatch.py` is TM(11,**18**) — the S11 summary report, line 1019).
- `ServiceDispatcher` docstring enumerates the implemented set: "S1, S3, S5, S6, S8, S9, S11, S12, S15, S17, S19, S20" (`service_dispatch.py:4-5`) — no S18.
- `configs/eosat1/mcs/pus_services.yaml` enumerates s1…s17 (+ nothing higher relevant; no s18 stanza).
- No example scripts anywhere in `configs/` (only ground procedures in `configs/eosat1/procedures/` executed by the MCS, and instructor scenario definitions in `configs/eosat1/scenarios/` executed by the *instructor-side* `scenario_engine.py`). No tests mention S18/OBCP (`tests/` grep empty).
- The architecture docx's only "scripting" mention is instructor-facing ("scripting, debrief replay, and trainee evaluation") — not spacecraft OBCPs. So nothing claims OBCPs exist either; the gap is a missing capability, not a doc lie.

### 3.2 What exists instead (and works)
- **S11** single time-tagged TCs: implemented and ticked (Section 2.4) — but a flat one-shot queue, not conditional/looping scripts, and unloadable from the ground as shipped.
- **S19 event-action** (closest to on-board autonomy): fully wired — definitions via TC(19,1) `ea_id + event_type + action_func_id` (`service_dispatch.py:1326-1363`), triggered from engine event generation (`engine.py:1472` → `trigger_event_action`, `service_dispatch.py:1386-1420+`), boot-time rules loaded from `configs/eosat1/monitoring/s19_rules.yaml` (`monitoring_loader.py:73-95`, registered `engine.py:446-447`), MCS UI builders for 19.1/2/4/5/8 (`smo-mcs/static/index.html:3313-3317`) and catalog entries (`tc_catalog.yaml:1226+`). Limitation: an "action" is exactly **one parameter-less S8 function call** (`_s19_dispatch` → `self._handle_s8(1, bytes([func_id]))`, `service_dispatch.py:1374-1385`) — no sequences, no arguments, no delays, no conditionals.
- **Ground-side sequences**: the MCS `ProcedureRunner` (Section 2) does support command/wait/verify/go-nogo steps — but it runs on the ground, so every step needs live contact (Iqaluit-only, per Section 1.5); it is not an on-board capability and dies at LOS.

### 3.3 Load→store→execute→report trace
Not applicable — there is no load path, no on-board script store, no interpreter, and no S18 TM reports to trace. First break is at square one: no service handler for 18 in the `service == …` chain (`service_dispatch.py:60-104`).

### 3.4 Fix proposals (ranked by effort)
1. **Small**: Document the actual autonomy model (S11 + S19 + FDIR) and stop implying OBCP-class capability; extend S19 to carry an argument byte-string per rule (~20 lines in `_handle_s19`/`_s19_dispatch`).
2. **Medium**: "Macro" service: add an S18-lite that stores named lists of (delay_s, service, subtype, data) tuples uploaded via TC, executed by a per-tick interpreter reusing `_dispatch_tc`; report start/finish via S5 events. Engine already has every building block (`tc_scheduler` pattern, `_dispatch_tc`, `tm_builder`).
3. **Large**: Full PUS-C ST[18] OBCP engine (load/activate/suspend/abort, step reporting, conditional logic), plus MCS UI editor and catalog entries, plus planner integration so an activity uploads as an OBCP + S11 activation — only worth it if the training syllabus needs autonomous-ops exercises.

---

## Cross-cutting note
`DEFECTS.md:27-33` (#15 fixed, #16 open "Planner backend endpoints … no UI", #21 open) and `VV_FINDINGS_2026-06.md` (F1 badge fix) show the team has audited the planner *internally* but never end-to-end: none of the audits caught the contact-schema mismatch, the Iqaluit-only contact gating vs all-station planning, the wrong activity func_ids, or the un-executable S11 packets — precisely the seams between packages that `tests/` (unit-scoped: `tests/test_planner/*`, `tests/test_mcs/*`) do not cross.

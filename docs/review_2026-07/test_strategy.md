# SpaceMissionSimulation — Functionality→Test Map & Test Strategy

Review item 2: "work out how to test all functionality".
Date: 2026-07-28. Repos: `SpaceMissionSimulation` (SMS, primary) and `SpaceCDF` (lighter pass, §7).
All paths relative to repo roots.

> **Status update — 2026-07-28 (P1 implementation pass).** All nine P1 items are
> now closed: six were implemented in this pass, three were already covered by
> work that landed after this strategy was written. The coverage matrix in §3
> and the P1 table in §4 reflect the current tree; §4a records the config/content
> defects the new tests found (pinned in allowlists, **not** silently fixed —
> they need a Flight Ops decision). P2/P3 in §4 remain forward work.

---

## 1. Test-suite inventory (as found)

**Framework:** pytest + pytest-asyncio. `tests/conftest.py` injects all `packages/smo-*/src`
dirs onto `sys.path` (so editable install is belt-and-braces), exposes `config_dir`
(→ `configs/eosat1`) and a rich `mock_engine` / `service_dispatcher` fixture pair for
ServiceDispatcher unit tests. `tests/test_acceptance/conftest.py` builds an `RFTestHarness`
that runs a **real `SimulationEngine` + real RF `PipelineCoordinator`** (TX→channel→RX),
so acceptance tests exercise genuine signal processing. `pyproject.toml` has
`[tool.pytest.ini_options] testpaths=["tests"]` and a `test = "pytest tests/ -v"` script.

**Counts (test functions, `grep -c "def test_"`):**

| Area | Files | Tests | Character |
|---|---|---|---|
| tests/test_simulator | 31 | 567 | Unit/engine-tick: models, PUS dispatch (incl. S13 edge paths, S18/OBCP), scheduler, TM storage, link gating, power gates, failure-manager onsets, multi-station contacts |
| tests/test_mcs | 13 | 278 | MCS server routes, tc_manager, procedure runner (69), go/no-go, alarms, playback, S11 builder, contact schema |
| tests/test_rfsim | 22 | 178 | CCSDS coding chain (RS/Viterbi/BCH/ASM/CLTU), DSP, channel model, GS failures, TM-frame FHP regression |
| tests/test_acceptance | 7 | 232 | Full engine+RF harness: 56 failure-mode tests, PUS services, commands, HK completeness |
| tests/test_planner | 10 | 196 | Activity scheduler, budgets, imaging, pass scheduling, planner HTTP server, **constraint endpoints**, activity func-ids, upload start time |
| tests/test_integration | 9 | 139 | End-to-end, LEOP, contingency recovery, mission scenarios, packet-loss diagnostics, planner→MCS→S11 chain |
| tests/test_common | 4 | 51 | Config integrity/validation, orbit (only 4), ECSS protocol (9) |
| **tests/test_content** | **3** | **35** | **NEW** — config/YAML content validation: all 31 scenarios, S19 rules, procedures↔tc_catalog |
| tests/test_gateway | 1 | 25 | Gateway upstream/downstream unit tests + framing |
| tests/test_commissioning | 1 | 28 | LEOP sequence |
| tests/test_e2e_browser | 2 | 35 | Playwright multi-window (MCS+instructor+planner) full-mission passes 01–13 |
| tests/ top level | 21 | 199 | Defect-regression tests (breakpoints, autostop, torque gain, commissioning seq 56, remediation 29…) |
| **Total** | **124** | **≈1,963 defs** | ≈2,660 collected cases after parametrisation |

**How the suite is run** (45 s bash cap ⇒ per-directory chunks): editable-install
`smo-common smo-simulator smo-gateway smo-mcs smo-planner smo-rfsim` + pydantic, sgp4,
aiohttp, reedsolo, pytest, pytest-asyncio. **Exclusions:** `tests/test_e2e_browser`
(Playwright, real browser) and `tests/test_integration/test_commissioning_rf.py` (needs
rfsim/GNU-Radio bridge).

**Suite status 2026-07-28: green.** Last full run (excluding the two exclusions above):
`test_simulator` 630 passed · `test_mcs+test_planner+test_common+test_content` 1168 passed,
1 skipped, 2 xfailed · `test_integration+test_acceptance` 382 passed ·
`test_rfsim+test_gateway+test_commissioning+top-level` 484 passed, 2 skipped.
The two xfails are `strict=True` gates on the outstanding Flight-Ops review items in §4a —
they will fail loudly (XPASS) the moment those items are fixed, prompting removal.
The previously-documented failure
`test_commissioning_sequence.py::TestPhase1::test_ttc_lock_acquisition_during_contact`
no longer reproduces.

---

## 2. Functionality inventory (what exists to be tested)

- **Subsystem models** (`packages/smo-simulator/src/smo_simulator/models/`): aocs_basic,
  eps_basic, tcs_basic, ttc_basic, obdh_basic, payload_basic — each with `handle_command`,
  failure injection (`failure == "…"` branches: AOCS 25, TTC 28, OBDH 18, EPS 16, TCS 14,
  payload 10 ⇒ **54 distinct failure modes**), state get/set (breakpoint persistence).
- **PUS services** (`service_dispatch.py`, 1603 lines): S2, S3, S5, S6, S8 (func map
  0–83 + 100–107), S9, S11, S12, S13 (large data transfer, lines 1161–1277), S15, S17,
  S19 (event-action + bootloader guard), S20. Autonomous S19 dispatch path with OBC-critical
  func refusal.
- **Engine** (`engine.py`, 2363 lines): tick loop, HK emission, phases, TC power/uplink
  gating, eclipse, contact gating, instructor queue.
- **Failure manager** (`failure_manager.py`): step / gradual / intermittent onsets,
  timed expiry, clear-all, per-wheel/array/circuit addressing.
- **Scenario engine** (`scenario_engine.py`): **31 scenario YAMLs** in
  `configs/eosat1/scenarios/` (events with time offsets, inject/clear actions,
  `duration_s` auto-stop, expected_responses → debrief).
- **Breakpoints** (`breakpoints.py` + instructor save/load routes, incl. orbit_utc capture).
- **FDIR / monitoring**: `fdir.py` (339 lines), `monitoring_loader.py`,
  `configs/eosat1/monitoring/s12_definitions.yaml` + `s19_rules.yaml` (26 rules),
  `configs/eosat1/fdir/fault_propagation.yaml`, subsystem `fdir.yaml`.
- **On-board TM storage** (`tm_storage.py`, S15 stores, dumps → `workspace/dumps`).
- **TC scheduler** (`tc_scheduler.py`, S11 time-tagged TCs, bypasses uplink gate).
- **Orbit / ground stations** (`smo-common/orbit/`): SGP4 propagator, eclipse model,
  contact windows; `configs/eosat1/planning/ground_stations.yaml`.
- **Protocol** (`smo-common/protocol/`): ECSS packet build/decommutate, framing, PUS defs.
- **Instructor API** (`instructor/app.py`): allowed command types = set_speed, freeze,
  resume, inject, clear_failure, failure_inject, failure_clear, failure_clear_all,
  override_passes, set_phase, start_scenario, stop_scenario, save_breakpoint,
  load_breakpoint; routes /api/state, /api/instructor/snapshot, /api/scenarios,
  /api/scenario/debrief, /api/failures, /api/breakpoints, breakpoint save/load;
  TC back-channel rejected with 403.
- **MCS** (`smo-mcs`): ~50 HTTP routes (state, pus-command, verification-log, procedures
  load/start/pause/resume/abort/step/skip/override, go-nogo, handover, alarms + ack,
  tm-dump, archive params/events/alarms/playback, displays: contact-schedule,
  power-budget, fdir-alarms, procedure-status, system-overview, alarm-trends),
  tc_manager with **183-command `tc_catalog.yaml`**, tm_processor, tm_archive (sqlite),
  procedure_runner, displays engine, positions.
- **Planner** (`smo-planner`): contacts, schedule (+upload/validate/pass-activity),
  ground track, power/data budgets, imaging targets/opportunities/schedule,
  **/api/constraints/{validate,power,aocs,thermal,data-volume,conflicts}**
  (constraint_checkers.py), activity_types, time-source.
- **Gateway** (`smo-gateway`): TCP upstream to sim, downstream fan-out to MCS clients.
- **rfsim** (`smo-rfsim`): CCSDS coding chain, DSP mod/demod, channel (link budget,
  fading, BER injection), pipeline coordinator, GNU-Radio bridge, radio front-end,
  web/terminal UI, IQ recorder.
- **Ops content**: procedures tree (leop/commissioning/nominal/contingency/emergency +
  procedure_index.yaml), manual/, mcs displays config, smdl, xtce.

---

## 3. Functionality × coverage matrix

Status: ✅ covered · 🟡 partial · ❌ uncovered. Evidence cites existing test files (tests/ prefix omitted).

| # | Functional area | Status | Evidence / what's missing |
|---|---|---|---|
| 1 | Subsystem models — nominal behaviour | ✅ | test_simulator/test_models, test_eps_enhanced, test_obdh_enhanced, test_payload_enhanced, test_ttc_enhanced, test_aocs_state_machine, test_subsystem_enhancements, test_sc_mode_convention |
| 2 | Failure modes catalog (54) | ✅ | test_acceptance/test_failures.py — 56 tests named per ID (flt_aocs_001…flt_tcs_*), all 6 subsystems; plus test_new_failure_modes.py (5) |
| 3 | Failure onset models (step/gradual/intermittent, timed expiry) | ✅ | **NEW** test_simulator/test_failure_manager_onsets.py (31): step immediacy, linear gradual ramp + clamp + config-driven default duration, seeded/monkeypatched intermittent flicker phasing & reproducibility, duration_s expiry boundary, per-wheel/array addressing, clear_all across onset types, simulation.yaml `failures:` overrides |
| 4 | PUS S2/S3/S5/S6/S8/S9/S11/S12/S15/S17/S19/S20 | ✅ | test_simulator/test_pus_services (26), test_pus_acceptance, test_tc_dispatch, test_s2_device_command_e2e, test_s12_s19_integration (24), test_scheduler (S11), test_tm_storage_enhanced (S15), test_acceptance/test_pus_services (42), test_integration/test_mission_scenarios |
| 5 | PUS S13 large data transfer | ✅ | **NEW** test_simulator/test_s13_large_data.py (35): create/size defaults/id uniqueness, segment NAK for unknown id, paused-transfer silence, retransmit idempotence, abort mid-transfer + session cleanup, double-end, status codes & progress, two concurrent transfers, unsupported subtypes, dispatch() routing. Note: **no session timeout exists** — pinned by `test_abandoned_transfer_is_not_auto_reaped` |
| 6 | S8 function map incl. 100–107 & func 49 | ✅ | test_pus_services TestS8NewRoutes; regression tests from audit #38/#39 |
| 7 | TC power/uplink gating, link gating | ✅ | test_tc_power_gate (9), test_ttc_eps_device_gates, test_device_power_gates, test_link_gating, test_ttc_uplink_lock_decoupled |
| 8 | Engine tick / HK emission / phases | ✅ | test_leop_engine, test_commissioning_sequence (56), test_acceptance/test_hk_completeness, test_obc_startup_bootloader |
| 9 | Scenario engine mechanics (start/stop/auto-stop/debrief) | ✅ | test_scenario_autostop (5), test_scenario_generator_sync (2), instructor debrief route; scenario_engine now validates actions/failures/initial_conditions at load time |
| 10 | **Scenario YAML content (31 files)** | ✅ | **NEW** test_content/test_scenarios_generic.py (191 collected): all 31 files load, loader reports **zero** validation issues, per-file metadata/difficulty/duration, only implemented actions, every event has a trigger, every inject (incl. `initial_conditions.failures`) names a failure the model really implements, expected_responses well-formed, **every scenario fast-run auto-stops at duration_s with injected failures cleared + debrief**, full fast-run scoring, re-run after auto-stop |
| 11 | Breakpoints (save/load, orbit clock, gains persistence) | ✅ | test_breakpoints (4), test_breakpoint_orbit (3, failing-first for defect #35), test_adcs_torque_gain persistence, instructor breakpoint routes |
| 12 | FDIR / S12 monitoring / S19 rules | ✅ (rules) / 🟡 (fault_propagation) | test_s12_s19_integration (24), test_event_edge_triggering (14) — mechanics. **NEW** test_content/test_s19_rules_content.py (138 collected + 1 xfail): all 26 rules load, unique ea_ids, every `event_type` resolves to an event-catalog id **or** an S12-monitored parameter (so the rule can fire), every `action_func_id` behaviourally routes to a real subsystem handler (probe via mock ServiceDispatcher), no rule uses OBC-critical funcs 52/53/55/56, bootloader gate blocks/allows correctly, `register_s19_rules` + `trigger_event_action` end-to-end. **13 rules dispatch a func that does not match their description — see §4a.** `fault_propagation.yaml` semantics still untested (P2) |
| 13 | On-board TM storage + dumps | ✅ | test_tm_storage_enhanced (10), test_dump_dir_configurable, test_delayed_tm_viewer, S15 acceptance |
| 14 | Orbit propagator / eclipse / contacts | 🟡 | test_common/test_orbit has only **4 tests**; contact behaviour mostly tested indirectly (planner pass_scheduling 24, ground_track 10, eclipse_display 5). No unit tests for eclipse-transition scan edge cases, contact-window boundary (AOS/LOS exactly at horizon), high-inclination/pole passes |
| 15 | ECSS protocol / framing | ✅ | test_common/test_protocol (9), rfsim test_integration_framing, gateway TestFramingIntegration; CRC/decommutate covered |
| 16 | Instructor API (auth boundary, snapshot, render) | ✅ | test_back_channel_closed (6, 403 path), test_instructor_snapshot, test_instructor_override_toggle, test_instructor_render_completeness (4) |
| 17 | MCS server routes | 🟡 | test_mcs_server (37), test_tc_manager (36), test_go_nogo (20), test_alarm_journal (17), test_tm_playback (10), test_position_filtering (33). **Display endpoints only partially: eclipse_display (5) exists but no direct tests for /api/displays/{power-budget,fdir-alarms,system-overview,alarm-trends,contact-schedule} response shape**; handover, ground-time-adjust untested by name |
| 18 | MCS procedures (runner + builder) | ✅ | test_procedure_runner (69), test_procedure_builder (13), integration test_position_workflows |
| 19 | **Shipped procedure content vs tc_catalog** | ✅ | **NEW** test_content/test_procedures_content.py (261 collected + 1 xfail): all 64 procedure `.md` files walked; every `**TC:**` mnemonic resolves to a tc_catalog name **or alias**; generic `FUNC_PERFORM`/`S8_COMMAND` wrappers must carry a func_id; every quoted S8 func_id routes to a real handler; `(Service N, Subtype M)` annotations match the catalog; alias-shadowing check; allowlist-hygiene test that fails on stale exemptions. **16 annotation drifts + 7 func-id intent drifts pinned — see §4a** |
| 20 | tc_catalog (183 commands) round-trip | ✅ | test_acceptance/test_commands.py iterates catalog through real dispatch; test_consistency_gaps (21) |
| 21 | Planner core (schedule, budgets, imaging, passes) | ✅ | test_planner/* (155) |
| 22 | **Planner constraint endpoints** | ✅ | **NEW** test_planner/test_constraint_endpoints.py (34): all 6 routes, each with a clean plan (shape) and a deliberately-breaching plan (SoC < 20 %, antipodal back-to-back slew, 40 min imaging/orbit + no FPA cooldown, 30 GB into a 20 GB store, overlapping antenna users, momentum saturation + desaturation reset); query-param plumbing (`battery_soc`, `current_onboard_mb`); CANCELLED-activity exclusion; /validate aggregation; a parametrised "no route may 500 on a fully-breaching plan" gate. **Found and fixed: `/api/constraints/power` returned 500 whenever a violation existed** (see §4a) |
| 23 | Gateway | ✅ | test_gateway (25): connect/reconnect, fan-out, disconnect pruning, framing |
| 24 | rfsim coding + DSP + channel | ✅ | 178 tests: test_reed_solomon/test_rs_decode, test_viterbi/test_convolutional, test_bch, test_asm, test_cltu, test_frame_sync, test_tm_frame (+**NEW** `TestFlushDoesNotLeakPacketOffsets`, 3), test_virtual_channel, test_dsp, test_modulation, test_channel_model, test_gs_failures, test_rf_integration |
| 25 | rfsim GNU-Radio bridge / radio UIs / IQ recorder | 🟡 | test_gr_bridge + test_iq_recorder exist (mocked); real GNU-Radio path only via excluded test_integration/test_commissioning_rf.py; `radio/web_ui.py`, `radio/terminal_ui.py` untested |
| 26 | Sim↔MCS live socket round trip | 🟡 | test_mcs_hk_round_trip only 3 tests; most integration goes through in-process harnesses, not real TCP gateway chain |
| 27 | LEOP / commissioning / contingency ops flows | ✅ | test_commissioning_sequence (56, 1 known fail), test_commissioning/test_leop_sequence (28), test_integration/{test_leop_end_to_end, test_contingency_recovery, test_nominal_orbit, test_end_to_end, test_packet_loss_diagnostics} |
| 28 | Browser UIs (MCS/instructor/planner rendering) | 🟡 | test_e2e_browser (35 Playwright tests, passes 01–13 incl. failures) — **manual-only**, excluded from routine runs; VV_FINDINGS shows UI-only bugs (F1 badge bug) that only live walkthroughs catch |
| 29 | Performance / memory | 🟡 | test_performance (4), test_acceptance/test_memory_profile — small; no tick-rate regression budget asserted in CI |

**Feel (updated 2026-07-28):** simulator core + PUS + failure catalog + MCS procedures
≈ 90 % covered; rfsim DSP/coding ≈ 88 %; planner ≈ 90 % (constraints hole closed);
**config/ops content ≈ 25 % → ≈ 85 %** — the biggest systematic hole is now covered by
`tests/test_content/` plus the loader-side validation that landed in `scenario_engine.py`.
Remaining content gaps: `fault_propagation.yaml` semantics (P2) and the MCS display
endpoint response shapes (P2). The rule of thumb below (every YAML the engine consumes
gets a loader-driven content test cross-checked against a code-derived catalog) is now
applied to scenarios, S19 rules and procedures; `fault_propagation.yaml` is the last
holdout.

---

## 4. Gap-ranked test strategy

### P1 — **CLOSED 2026-07-28**

| ID | Status | Where it landed |
|---|---|---|
| P1-1 | ✅ implemented | `tests/test_content/test_scenarios_generic.py` — `test_every_scenario_file_loads_into_a_definition`, `test_loader_reports_zero_validation_issues`, `test_scenario_declares_required_metadata` (×31) |
| P1-2 | ✅ implemented | same file — `test_scenario_injects_only_known_failures` (×31), `test_scenario_uses_only_implemented_actions` (×31), `test_scenario_events_can_fire` (×31). Failure catalog scraped from each model's `inject_failure()` source, so it cannot drift |
| P1-3 | ✅ implemented | same file — `test_scenario_auto_stops_at_declared_duration` (×31), `test_full_fast_run_fires_events_and_debriefs`, `test_scenario_can_be_rerun_after_auto_stop` |
| P1-4 | ✅ implemented | `tests/test_content/test_s19_rules_content.py` (all 26 rules × 5 per-rule checks + 6 dispatch/behaviour tests + 1 strict xfail). Routing is verified *behaviourally* (dispatch the func against a mock ServiceDispatcher and record which subsystem's `handle_command` fires) rather than by regex, so it survives S8 map refactors |
| P1-5 | ✅ implemented | `tests/test_simulator/test_s13_large_data.py` (35) |
| P1-6 | ✅ implemented | `tests/test_planner/test_constraint_endpoints.py` (34) |
| P1-7 | ✅ implemented | `tests/test_content/test_procedures_content.py` (261 collected + 1 strict xfail). Index-file existence was already covered by `test_common/test_config_integrity.py`, so this module covers file *contents* instead |
| P1-8 | ✅ resolved (different defect) | `test_ttc_lock_acquisition_during_contact` no longer reproduces. The *live* flake was `test_acceptance/test_commands.py::TestAOCSCommandsRF::test_st2_power_on` — root-caused to an RF frame-builder bug and fixed, see §4a |
| P1-9 | ✅ implemented | `tests/test_simulator/test_failure_manager_onsets.py` (31) |

**Already covered before this pass (do not re-implement):** multi-station contacts
(`test_simulator/test_multi_station_contacts.py`), the planner→MCS→S11 commanding chain
(`test_planner/test_activity_funcids.py`, `test_upload_start_time.py`,
`test_mcs/test_s11_builder.py`, `test_contact_schema.py`,
`test_simulator/test_s11_schedule_fire.py`, `test_integration/test_planner_mcs_s11_chain.py`),
on-board scripting S18/OBCP (`test_simulator/test_obcp_s18.py`), and config consolidation +
power (`test_simulator/test_power_budget_regression.py`, `test_simulation_config.py`).

**Delivered: 6 new test files + 1 extended (`test_rfsim/test_tm_frame.py`), 4 new test
directories/files' worth of ≈ 136 test functions ⇒ ≈ 700 collected cases after
parametrisation.**

### 4a — Defects found by the new tests

Three code defects were **fixed**; the config/ops-content drift is **pinned in allowlists
and reported**, not rewritten, because remapping an autonomous FDIR response or an ops
procedure is a Flight Ops decision.

**Fixed (code):**

1. **`smo_rfsim/ccsds/tm_frame.py::TMFrameBuilder.flush()` lost whole frames of telemetry.**
   `add_packet` records each packet's start offset so `_drain_frames` can compute the
   First Header Pointer. `flush()` cleared the byte buffer but **not** the offsets, and
   hard-coded `fhp = 0` on the padded frame even when the leftover was a mid-packet
   continuation. Result: every later frame inherited a stale offset and advertised a wrong
   FHP, the receiver resynchronised at the wrong byte, and a frame's worth of packets was
   silently dropped or turned into garbage. Reproduced deterministically: six add/flush
   cycles of a 15-packet burst recovered 16 then 29 packets per 15 sent; now exactly 15.
   **This was the root cause of the "order-dependent" acceptance flake** — the S1.1 ACK for
   `test_st2_power_on` was in the lost frame. `tests/test_acceptance/test_commands.py`
   now passes 85/85 repeatedly. Regression test: `TestFlushDoesNotLeakPacketOffsets` (3).
2. **`/api/constraints/power` returned HTTP 500 for every plan with a power violation.**
   `check_plan_power` returns `ConstraintViolation` *objects* (correct — `validate_plan`
   needs them to aggregate severities), but `ActivityScheduler.check_power_constraints`
   passed them straight to `web.json_response`. The route worked only while there was
   nothing to report. Fixed by serialising in the scheduler wrapper, leaving
   `validate_plan` untouched.
3. **`test_simulator/test_scheduler.py::test_power_consumption_changes` was inside the
   noise floor.** After the power-budget consolidation the AOCS wheel line is billed at
   `simulation.yaml line_power_w.aocs_wheels = 6 W` (was a hard-coded 12 W), while
   `power_cons_w` carries ±1 W gaussian noise per tick — a single-tick comparison against
   a fixed 5 W margin passed only ~1 run in 4. Now averages 25 ticks and compares against
   the model's own configured line power.

**Reported, pinned, needs a Flight Ops decision:**

4. **13 of 26 S19 rules dispatch an S8 func that does not match their description.**
   Same class as defect #37. Examples: `eps_bus_undervoltage_payload_off` (ea 1001) fires
   func 10 = **AOCS_SLEW_TO**, not payload-off; `ttc_pa_overheat_pa_off` (ea 4002) fires
   func 54 = **OBDH obc_select_bus**, not PA-off; the four OBDH rules (5001–5004) all land
   on **TCS heater** circuits. Full list in `KNOWN_MISROUTED` in
   `tests/test_content/test_s19_rules_content.py`, each with the routing it has today.
   None reach an OBC-critical func (52/53/55/56), and the bootloader gate is verified —
   so this is mis-targeted autonomy, not a boot hazard. The strict-xfail
   `test_no_rule_is_misrouted` is the review gate.
   *Sub-issue:* rules 2005–2008 ("disable wheel 1/2/3/4") all send bare func 2 with no
   wheel-index byte, so all four disable wheel 0.
5. **16 procedure `(Service, Subtype)` annotations disagree with `tc_catalog.yaml`**
   (254 occurrences). Two systematic causes: (a) the simulator implements **S20.1 = set,
   S20.3 = get**, the inverse of ECSS-E-ST-70-41, and the procedures follow the standard
   while the catalog follows the code (82 `GET_PARAM` + 32 `SET_PARAM` refs); (b) 79 + 17
   `HK_REQUEST` refs quote the S3.25 *report* (or S3.1) instead of the S3.27 *request*.
   Plus an "S8 subtype = subsystem" shorthand in the emergency procedures that the
   dispatcher does not implement. Pinned in `KNOWN_ANNOTATION_DRIFT`.
6. **7 raw S8 func_ids quoted in `emergency/obc_reboot.md` and `nominal/software_upload.md`
   as OBDH operations route elsewhere** — func 42/43 ("OBC reboot" / "switch to backup
   OBC") reach **TCS heater** circuits, func 21 ("select memory bank") reaches **EPS
   reset-OC-flag**, func 30 ("controlled OBC restart") reaches **payload**. Pinned in
   `KNOWN_FUNC_INTENT_DRIFT`; strict-xfail `test_obdh_procedures_command_the_obdh` is the
   review gate.
7. **No S13 session timeout exists.** A dump abandoned by a lost pass stays in
   `_s13_transfers` forever; the only cleanup is an explicit S13.5. Documented by
   `test_abandoned_transfer_is_not_auto_reaped` rather than asserted as correct.
8. **`ScenarioEngine` intermittent-onset phasing is inverted on the first transition.**
   `_flicker_on` starts `True`, so the first flicker *clears* (a no-op on a model that was
   never faulted) and the fault itself lands one interval later. Pinned by
   `test_first_transition_is_a_clear_then_it_flickers_on` — flipping it would shift every
   intermittent scenario's timeline.
9. **`/aocs` and `/validate` disagree on slew scope.** `check_aocs_constraints` filters the
   schedule to imaging activities before the slew check; `validate_plan` runs it over
   every activity, so a non-imaging activity between two imaging passes hides the slew
   violation from `/validate`. Pinned by `test_slew_check_scope_differs_between_routes`.

*All allowlists carry a hygiene test that fails if an exemption becomes stale, so fixing
any of the above forces the exemption to be removed rather than lingering as a permanent
excuse.*

### P2 — should-have (forward work, unchanged)

- `test_mcs/test_display_endpoints.py` — HTTP shape tests for power-budget, fdir-alarms,
  system-overview, contact-schedule, alarm-trends, handover GET/POST, ground-time adjust
  (producer/consumer key mismatches like defect #36 are exactly what these catch).
- `test_common/test_orbit_edges.py` — eclipse transition scan across terminator, AOS/LOS at
  min-elevation boundary, pass over station at orbit-plane edge, `next_eclipse_transition`
  bounded-scan termination.
- `test_integration/test_socket_round_trip.py` — real gateway TCP chain sim→gateway→MCS
  for TC send + HK receive (extend test_mcs_hk_round_trip to cover verification-log and
  event flow).
- `test_fault_propagation_content.py` — fault_propagation.yaml entries reference known
  failures/parameters.
- Performance guard: assert engine tick ≤ N ms at ×10 over 60 sim-min (extend
  test_performance with a hard budget, marked `slow`).
- Seeded end-to-end BER sweep in rfsim asserting frame-recovery threshold vs Eb/N0 curve.

*P2 additions from this pass:* a fixed-seed RF soak that pushes N packet bursts through
the real `PipelineCoordinator` and asserts zero packet loss (the frame-builder bug in §4a
was only visible as an intermittent ACK loss, never as a failing rfsim unit test);
`fault_propagation.yaml` content test; MCS display-endpoint response shapes.

### P3 — nice-to-have (forward work, unchanged)

- Playwright smoke subset (3 tests: each UI loads, one command round-trip) tagged for a
  weekly manual/desktop run; keep full 35-test mission validation as release gate.
- rfsim `radio/web_ui.py` + `terminal_ui.py` render tests (aiohttp client / snapshot).
- GNU-Radio bridge contract test behind `@pytest.mark.rfhw` (runs only where GR installed).
- Property-based (hypothesis) tests for ECSS packet build↔decommutate round-trip and
  CLTU/RS encode↔decode under random corruption.
- Instructor UI render-completeness expansion (every engine state key has a UI binding —
  extends test_instructor_render_completeness).

---

## 5. Execution plan

**Suite organisation** — `tests/test_content/` now exists and is where the config layer
lives (scenarios, S19 rules, procedures; `fault_propagation.yaml` to follow). Pytest
markers (`unit`, `engine`, `api`, `content`, `rf`, `slow`, `browser`, `rfhw`) are still
**not** added to pyproject — remaining P2 housekeeping.

**Runtime budget / chunking — measured 2026-07-28:**

| Invocation | Wall time |
|---|---|
| `pytest tests/test_simulator -q` | ~21 s |
| `pytest tests/test_mcs tests/test_planner tests/test_common tests/test_content -q` | ~24 s |
| `pytest tests/test_rfsim tests/test_gateway tests/test_commissioning tests/test_*.py -q` | ~52 s |
| `pytest tests/test_integration tests/test_acceptance -q --ignore=tests/test_integration/test_commissioning_rf.py` | ~166 s |

Only the acceptance/integration chunk exceeds the 45 s bash cap (it runs the real RF
pipeline in wall-clock time); split it by file if needed. The new content tests add ~2 s
total — the parametrised scenario auto-stop suite fast-ticks all 31 scenarios in under a
second by stepping at `duration_s/50`.

**CI vs manual:**
- Every change (CI tier 1): unit + content + api markers (~fast, deterministic).
- CI tier 2 (per merge): engine + rf acceptance + integration (excl. commissioning_rf).
- Manual/desktop only: `test_e2e_browser` (Playwright + real browser, use before demos/releases),
  `test_integration/test_commissioning_rf.py` (rfsim/GNU-Radio path), live V&V walkthrough
  per VV_FINDINGS checklist (catches UI-badge-class bugs no headless test sees).
- Weekly: `slow` performance guards + full browser mission validation.

**Generic scenario auto-validation (the key leverage) — now in place.** One parametrised
module (P1-1..3) took 31 scenario YAMLs from 19 %-tested to 100 %-load-tested at ~zero
marginal cost per future scenario; the same pattern is now applied to s19_rules (P1-4)
and procedures (P1-7); fault_propagation remains (P2). Rule of thumb adopted: **any YAML
the engine consumes gets a content test that loads it through the real loader and
cross-checks every reference against the code-derived catalog.**

**Second rule of thumb, learned in this pass:** where the catalog is a *dispatch table*
(S8 func ids), derive it **behaviourally** — dispatch the id against a mock-subsystem
`ServiceDispatcher` and record which model's `handle_command` fires — rather than by
scraping source with a regex. The behavioural probe found the S19/procedure func-id drift
that a range check would have called valid, and it keeps working across S8 refactors.

---

## 6. Known-issue ledger (updated 2026-07-28)

**Closed:**
- ~~Pre-existing fail: `test_commissioning_sequence.py::TestPhase1::test_ttc_lock_acquisition_during_contact`~~ — no longer reproduces; the whole top-level suite is green.
- ~~Order-dependent flake `test_acceptance/test_commands.py::TestAOCSCommandsRF::test_st2_power_on`~~ — root-caused (it was not order-dependent; it reproduced standalone at the same position) and fixed at source: `TMFrameBuilder.flush()` FHP/offset bug, §4a item 1.
- ~~S19 rule re-mapping (defect #37) had no domain-review harness~~ — `test_content/test_s19_rules_content.py` is now that harness, and it found 13 mis-targeted rules (§4a item 4).

**Open:**
- **13 mis-targeted S19 rules** (§4a item 4) — awaiting Flight Ops FDIR review. Gate: strict xfail `test_no_rule_is_misrouted`.
- **Procedure annotation + func-id drift** (§4a items 5–6) — awaiting Flight Ops doc review. Gate: strict xfail `test_obdh_procedures_command_the_obdh`; allowlists `KNOWN_ANNOTATION_DRIFT` / `KNOWN_FUNC_INTENT_DRIFT`.
- **S20 subtype convention is inverted vs ECSS** (20.1 = set, 20.3 = get in the simulator) — the root cause of the largest annotation-drift cluster. Decide whether to change the simulator or the docs.
- **No S13 session timeout** (§4a item 7).
- **Intermittent-failure first-transition phasing** (§4a item 8).
- VV finding F1 (planner "LIVE" badges hardcoded) — needs UI test or fix; only browser tier catches it.
- Audit notes: engine branches reachable only by direct API (`inject`, `clear_failure`, `set_phase`, `pause_scenario`, `start_separation`) — covered at API level, no UI button; keep API tests as their safety net.

---

## 7. SpaceCDF (lighter pass)

**Functionality:** `packages/` = spacecdf-server (FastAPI, **24 router modules**: design,
elements, engineering, requirements, risk, fmeca, thermal, compliance, conops, exports,
kb, sessions, snapshots, studies, optimize, positions, ground, lifecycle, ecss, agents,
ai, ws…), spacecdf-agents (tier1: 9 domain agents — aocs/power/thermal/link/orbit/mass/
propulsion/structure/data; tier2: 11 — cost/risk/reliability/debris/radiation/
sustainability/trl/systems/volume/conflicts/community; tier3 + exporters), spacecdf-common
(models, config, **physics: 15 modules** incl. link_budget, radiation, debris,
constellation, beyond_leo), spacecdf-ai, spacecdf-kb.

**Tests:** top level ≈ 143 (decision_tools 18, four_missions 18, level_workbench 27+24,
mission_design_scenario 11, phase4_invariants 7, physics_sanity 21, reactivity_e2e 17)
+ test_models 16 + test_repositories 15 + 5 package-local files (server: document
generators, mission design; ai: guard/config/context). **The directories `tests/test_agents`,
`tests/test_api`, `tests/test_convergence`, `tests/test_physics` are empty placeholders** —
intent exists, content doesn't.

**Coverage feel:** core workflows (mission design, workbench levels, reactivity,
invariants) ✅; physics sanity 🟡 (21 tests across 15 physics modules — thin per module);
**per-router API coverage ❌** (24 routers, no dedicated API tests); **per-agent coverage ❌**
(~30 agents, no dedicated tests — exercised only indirectly via four_missions/reactivity).

**SpaceCDF P1s:** (a) fill `tests/test_api` with FastAPI TestClient smoke per router
(200 + schema on list/get endpoints, one mutating round-trip each); (b) fill
`tests/test_agents` with a parametrised contract test: every tier1/tier2 agent runs on a
canonical mission state and returns outputs matching its declared schema, no NaN/negative
mass/power; (c) per-module physics spot checks against hand-computed values (link budget
closes, radiation dose monotonic with shielding, debris flux vs altitude shape).

---

*End of strategy. SMS P1 closed 2026-07-28 (6 files implemented, 3 already covered);
P2/P3 above remain forward work. SpaceCDF's 3 workstreams are untouched.*

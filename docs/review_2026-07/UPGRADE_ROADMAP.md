# EO-Sat-1 Simulator Suite — Prioritised Upgrade Roadmap

**Document:** SMO-ROADMAP-2026-07 · **Date:** 2026-07-28 · **Revised:** 2026-07-29
**Companion to:** `REVIEW_2026-07.md` (this document is the answer to review item 10)
**Scope:** `SpaceMissionSimulation` (primary), `SpaceCDF` (§S), courseware alignment (§C)

> **2026-07-29 status.** Eight of the nine Now-tier items are **done** — N-1, N-2,
> N-4, N-5, N-6, N-7, N-8 and N-9 all landed with defects #43 and #53–#75. Only
> **N-3 (commit and branch)** remains from the original Now tier, and it is now
> more urgent, not less: the tree carries roughly a hundred more uncommitted
> changed files than when this document was written. Two new Now-tier items are
> added below (**N-10** S19 action-func content, **N-11** the data-rate/image-size
> arithmetic), and four new Next-tier items (**X-12**…**X-15**) from the RF,
> planner, PUS-service and MCS-display audits of 2026-07-28/29. Completed items
> are kept in place with a **DONE** banner rather than deleted, so the reasoning
> that justified them stays readable.

---

## How to read this

Four workstreams, three time tiers plus a parallel courseware track:

| Tier | Horizon | Selection rule |
|---|---|---|
| **Now** | This term, before the next cohort | Anything that can silently corrupt a live exercise, make results irreproducible, or teach a physically wrong number. All small except N-2 and N-4. |
| **Next** | This academic year | Capability the syllabus needs, and the structural fixes that stop the same defect class recurring. |
| **Later** | Opportunistic / student project material | Decomposition and depth. High value, no urgency; several are good capstone or co-op projects. |
| **§C Courseware** | Parallel, continuous | Textbook and workbook corrections. Cheapest high-value work in the whole programme. |

**Effort:** **S** ≤ 1 day · **M** ≈ 2–5 days · **L** > 1 week.
**Risk** is the risk of *doing* the item (regression, scope creep), not of leaving it.
**Pedagogical value** is what the course gains — the reason to spend the effort at all.

Every item cites its source: a design-register finding (`DI-nn` in `design_issues.md`), an integration finding (`I-n`, `REVIEW_2026-07.md` §3), a defect (`#nn`), or a textbook-vs-sim divergence (`textbook_vs_sim.md` §3 row *n*).

---

## Tier 1 — NOW

**Now-tier status at 2026-07-29**

| ID | Item | Status | Landed as |
|---|---|---|---|
| N-1 | Tick guard + instructor input validation | ✅ **DONE** | `engine.py:113-119` (`engine_fault`, `engine_fault_count`), `:1025-1030` (`_record_engine_fault`); fault surfaced in `get_state_summary()` |
| N-2 | Threading contract / immutable snapshot | ✅ **DONE** | `engine.py:1164-1165` + `_publish_snapshot()` `:2619`; reader contract documented at `:2767` |
| N-3 | Commit and branch the working tree | ⬜ **OPEN** | — the only original Now item still outstanding |
| N-4 | S-band RF plan | ✅ **DONE** | `configs/eosat1/subsystems/ttc.yaml:1-13` (2025/2200 MHz), `ttc_basic.UL_BAND_MHZ`/`DL_BAND_MHZ`, per-station G/T via `LinkBudgetConfig.gt_for_station` |
| N-5 | Honour `t0_epoch` | ✅ **DONE** | `engine.py:162-167` (`_resolve_t0_epoch` → `orbit.reset(t0)`) |
| N-6 | ST2 RF-harness flake | ✅ **DONE** | Root-caused, not suppressed: defect **#43** — `TMFrameBuilder.flush()` left stale packet offsets and a hard-coded FHP of 0 |
| N-7 | S11 subtype error in `PUS_SERVICE_REFERENCE.md` | ✅ **DONE** | `docs/PUS_SERVICE_REFERENCE.md` — S11 subtype table (insert = **4**, 11 = delete-all), TM(11,5)/TM(11,18) formats, corrected "Imaging Pass (automated)" sequence, plus a new Service 18 section and a corrected Service 2/12/17/19 pass |
| N-8 | 64 kbps reachable from the ground | ✅ **DONE** | defect **#74** — func 65 sets `data_rate_mode` (`ttc_basic.py:824-859`), dispatcher accepts the catalogued 1-byte form (`service_dispatch.py:970-985`), and the rate now retunes the RF pipeline end to end (`bridge._apply_sim_feedback` → `PipelineCoordinator.set_data_rate_bps`) |
| N-9 | Retire the 245 W AOCS-off fallback array | ✅ **DONE** | `eps_basic.py:351-378` — cosine-of-beta over the configured panel areas with a Cauchy 1/4 tumbling projection factor |
| N-10 | S19 action-func content review | 🆕 **NEW — blocked on Franz** | see below |
| N-11 | Data-rate ↔ image-size arithmetic | ✅ **DONE** | defect **#82** — `payload.yaml image_size_mb: 800 → 4.0`; see below for the closing arithmetic |

### N-1 · Guard the tick thread and validate instructor input — ✅ DONE

| | |
|---|---|
| **Source** | DI-01 (Critical) |
| **Rationale** | `_run_loop` calls `_tick_once` with no exception guard, and `_drain_instr_queue` catches only `queue.Empty`. A `ValueError` from `_handle_instructor_cmd` — reachable today by sending `{"type":"set_speed","value":"fast"}` over the instructor WebSocket, which has **no allowlist** while the HTTP path does — kills the daemon sim thread. `self.running` stays `True`, the aiohttp and TCP servers keep serving the last frozen state, and the only symptom is that telemetry stops updating. Two channels, two trust levels, one queue. |
| **Scope** | Wrap `_tick_once` in `try/except`: log with traceback, set a visible `engine_fault` flag surfaced in `/api/state` and the instructor console, continue the loop. Extract the HTTP allowlist into one module constant and apply it to the WS handler. Validate command payload types and ranges once, before enqueueing. |
| **Effort** | **S** |
| **Risk** | Low. Additive; the only behaviour change is that a previously fatal exception becomes a logged fault. Take care that the fault flag does not mask a genuine crash loop — rate-limit the log. |
| **Pedagogical value** | Indirect but decisive: an exercise that freezes mid-anomaly with no error is the single worst failure mode for a class of 30 students, because it looks like a spacecraft symptom. This also removes the last silent-failure path in the instructor console. |

### N-2 · Fix the threading contract on the engine — ✅ DONE

| | |
|---|---|
| **Source** | DI-02 (Critical) |
| **Rationale** | `self.params` and every subsystem `_state` dataclass are written by the tick thread and read by aiohttp handlers, but `_params_lock` is acquired **only by the readers** (`get_state_summary`, `get_instructor_snapshot`). A lock taken on one side provides zero mutual exclusion. CPython's GIL prevents interpreter crashes but not torn multi-field reads (SoC and battery current from different ticks; `power_lines` copied mid-update) or `RuntimeError: dictionary changed size during iteration` on `dict(self.params)`. The lock's presence is worse than its absence, because it reads as a design decision. |
| **Scope** | Preferred: at the end of each tick, publish an immutable snapshot dict under the lock; readers take the snapshot and never touch live state. Alternative: route state export onto the tick thread via a request queue with futures. Either way, document the threading contract in the `SimulationEngine` docstring — currently there is none. |
| **Effort** | **M** |
| **Risk** | Medium — touches every reader (`/api/state`, instructor snapshot, 1 Hz WS broadcast, MCS polling). Mitigated by the 1970-test suite: the snapshot must reproduce `get_state_summary`'s exact key set, which `test_instructor_render_completeness` and the MCS route tests already pin. |
| **Pedagogical value** | Removes the "the display glitched, ignore it" folklore that erodes trust in every panel. Also a genuinely good teaching artefact in its own right: a small, real concurrency defect in a flight-like system, suitable for a software-engineering seminar. |

### N-3 · Commit and branch the working tree — ⬜ STILL OPEN

| | |
|---|---|
| **Source** | `design_issues.md` cross-cutting note; `DEFECTS.md` status note |
| **Rationale** | `DEFECTS.md` records that the entire 2026-05/06 remediation exists only as uncommitted working-tree edits. This session added 60 more changed files on top. There is currently no way to bisect a regression, review a change, or hand a known-good tree to a student. |
| **Scope** | Branch, commit in logical groups (the twelve defects make natural commit boundaries), tag a known-good baseline with the measured test result (1970 passing, 1 known flake). Add the `.gitignore` entries from DI-29 in the same pass so build artefacts, `.DS_Store`, screenshots and the live `configs/data/tm_archive.db*` stop being committed. |
| **Effort** | **S** |
| **Risk** | Low, but do it **before** N-2, so the concurrency work is reviewable. |
| **Pedagogical value** | Prerequisite for students contributing at all, and for the merge in review item 8. |

### N-4 · Make the RF frequency plan coherent — **position: move the simulator to S-band** — ✅ DONE

| | |
|---|---|
| **Source** | DI-05, DI-04; `textbook_vs_sim.md` §3 rows 6 and 7; workbook exercise COM-006 |
| **Rationale** | The simulator currently contradicts itself and the course. `configs/eosat1/subsystems/ttc.yaml:2-3` sets `ul_freq_mhz: 449.0` / `dl_freq_mhz: 401.5` — **UHF** — and `ttc_basic.py:185-190` computes the operational link budget from those values. Meanwhile `planning/ground_stations.yaml` declares S-band 2025/2200 MHz "per CCSDS 401.0-B", `rfsim.yaml:19-21` declares 2200 MHz with 169 dB path loss, and the textbook (pp. 309–310) states S-band prime with UHF for the bootloader beacon only. That is a **~15 dB FSPL difference at the same range**. Separately, the per-station `g_t_db` values (Iqaluit 20.0, Troll 18.5) are dead config: `ttc_basic.py:184` uses one global `gs_g_t_db`, so Troll's poorer G/T has no effect on anything — which now matters far more than it did, because defect #41 made Troll passes real. **Consequence:** the measured margins the book records as the mission baseline (14.5 dB @ 1 kbps, 6.8 dB @ 64 kbps, p. 396) cannot be reproduced, and COM-006 — the commissioning card whose whole purpose is to *establish* those baselines — produces numbers that contradict the printed ones. |
| **Position** | **Change the simulator, not the book.** S-band is what the ground stations, the RF simulator, the textbook and the real-world design case all say; UHF in `ttc.yaml` is the outlier and is almost certainly a legacy value. Changing the book instead would require rewriting the Ch 3.3 link chapter, App C.6–C.8, and the EOSAT-1 spacecraft description, and would leave the sim contradicting its own `rfsim.yaml`. |
| **Scope** | (a) `ttc.yaml` → 2025.0 / 2200.0 MHz. (b) Move the frequency plan and per-station G/T into the single ground-station file (completes DI-04) and have `ttc_basic` resolve the *current* station's G/T from `orbit_state.active_gs`, which defect #41 now provides. (c) Re-baseline: run COM-006 and record the actual margins at 1 and 64 kbps; if they land near 14.5 / 6.8 dB, adjust `eirp_dbw` / `sc_gain_dbi` to close the gap and the book needs no change at all. (d) Add `frequency_hz` to `rfsim` `ChannelConfig` or remove it from the YAML (DI-15). Retain the UHF numbers as the bootloader-beacon link, which is what the book actually describes. |
| **Effort** | **M** |
| **Risk** | Medium — link-margin values feed S12 monitors, FDIR `ttc_link_loss`, the rfsim channel model and several procedure pass/fail criteria. Do it with the acceptance RF harness running (`tests/test_acceptance`, 243 tests) and expect to re-tune two or three limit bands. |
| **Pedagogical value** | **Highest single item in the roadmap for the course.** It unlocks Ch 3.3 (18-line link budget), Ch 13.6 (link-margin sanity check), App C.6–C.8, and makes COM-006 a real measurement rather than a ritual. It also makes the two ground stations *differ* — Troll's 18.5 dB/K vs Iqaluit's 20 dB/K becomes an operational trade a student can observe, which is exactly the kind of thing that makes a two-station network worth teaching. |

### N-5 · Honour `t0_epoch` at engine start — ✅ DONE

| | |
|---|---|
| **Source** | DI-16; `textbook_vs_sim.md` §3 row 17 |
| **Rationale** | The propagator defaults `self._sim_utc = datetime.now(timezone.utc)` and the engine never calls `reset()` with an epoch, so **every run starts the orbit at the current wall clock** against a TLE with epoch day 26068.5, while the MCS computes MET against the configured `t0_epoch: 2026-03-10T00:00:00Z`. Three consequences: runs are not reproducible (eclipse and pass geometry differ every start, and SGP4 error grows with TLE age); scenario YAMLs that assume pass timing behave differently day to day; and the MET shown to trainees has no relation to the sim's elapsed time. Every printed worked example that depends on absolute timing is unreproducible for this reason alone. Note the pass statistics in `REVIEW_2026-07.md` §4.2 were produced by `tools/pass_gap_analysis.py`, which *does* call `prop.reset(t0)` — so the tool and the running simulator currently disagree about the orbit. |
| **Scope** | Engine constructs the propagator and calls `reset(cfg.t0_epoch)`. Add `epoch: t0 \| wall` to `simulation.yaml` for the rare case where wall-clock start is wanted (default `t0`). Add `t0_epoch` to the pydantic `OrbitConfig` schema — `pass_gap_analysis.py:44-46` currently has to re-read the raw YAML because the field is not in the schema, which is a small instance of DI-15's "config the loader drops". |
| **Effort** | **S** |
| **Risk** | Low-medium. Tests that implicitly assume "now" may need epochs pinned; that is an improvement, not a cost. |
| **Pedagogical value** | Turns the simulator into a *deterministic* teaching instrument: the same exercise gives the same passes, the same eclipses and the same SoC trace for every student and every cohort, and the book's worked examples become checkable. Also a prerequisite for grading anything timing-dependent. |

### N-6 · Dispose of the ST2 RF-harness flake — ✅ DONE

| | |
|---|---|
| **Source** | `REVIEW_2026-07.md` §0; `test_strategy.md` P1-8 |
| **Rationale** | `tests/test_acceptance/test_commands.py::TestAOCSCommandsRF::test_st2_power_on` passes in isolation and fails deterministically when the preceding commands in its class have run through the shared `RFTestHarness` (`[NO_RESP] S1.1=N S1.7=N (+0 TM pkts)` — the uplink CLTU is lost in the channel model at that point in the packet sequence). One red test in a 1970-test suite trains everyone to ignore red. |
| **Scope** | Diagnose: seed the channel model's RNG per test, or reset the harness's channel state between tests. If it is a genuine BER draw, seed it and assert the *retry* succeeds — which is a better test than the current one. If it cannot be resolved quickly, `@pytest.mark.xfail(strict=True)` with a defect reference so the suite is green-or-meaningfully-red. |
| **Effort** | **S** |
| **Risk** | Low. |
| **Pedagogical value** | Indirect: a green suite is the precondition for students trusting, and contributing to, the codebase. |

### N-7 · Correct the S11 subtype error in `PUS_SERVICE_REFERENCE.md` — ✅ DONE

| | |
|---|---|
| **Source** | `integration_trace.md` §2.4; defect #46 "Related" note |
| **Rationale** | `docs/PUS_SERVICE_REFERENCE.md:452-472` and `:938-944` describe automated imaging via "S11 subtype 11 (insert scheduled activity)". In code, subtype **4** inserts and subtype **11** is **delete-all** — a student following the documentation would erase the on-board schedule and see it succeed. |
| **Scope** | Correct both passages; add a one-line note that the ground-side S11 path now works (defect #46) and point at the new `POST /api/schedule/upload-s11` endpoint. |
| **Effort** | **S** (under an hour) |
| **Risk** | None. |
| **Pedagogical value** | Removes an actively harmful instruction from the reference students are told to read. |

### N-8 · Make the TTC high data rate reachable from the ground — ✅ DONE

| | |
|---|---|
| **Source** | New finding, `docs/OBCP_GUIDE.md` §6; `textbook_vs_sim.md` §3 row 9 |
| **Rationale** | S8 func 65 (`TTC_SET_DATA_RATE`) reaches the TTC model, but the TTC tick recomputes `tm_data_rate` from `data_rate_mode`, which **no telecommand can set**. The 64 kbps high rate is therefore unreachable from the ground, whether commanded directly, from a procedure, from a planner `data_dump` activity (which defect #45 just re-pointed at func 65) or from an OBCP. The textbook states flatly (p. 400) that "the EOSAT-1 simulator only supports the two rates above (`TTC_SET_DATA_RATE` accepts 0=low / 1=high)" — the claim is true about the catalogue and false about the effect. |
| **Scope** | Have func 65 set `data_rate_mode` (which the tick already consumes) rather than, or in addition to, `tm_data_rate`. Add an acceptance test asserting the commanded rate survives a tick, and extend `test_activity_funcids.py` to check the *effect* of `data_dump`, not only its routing. |
| **Effort** | **S** |
| **Risk** | Low. Check the S15 dump-pacing and link-budget paths, which read the rate. |
| **Pedagogical value** | Restores the data-rate half of the pass-planning lesson: at 1 kbps a pass moves ~50 kB, at 64 kbps ~3 MB, and choosing between them under a link margin is the core Ch 8 / Ch 13.6 trade. Also completes defect #45's `data_dump` fix — the activity currently commands the right function to no effect. |

### N-9 · Retire the AOCS-off fallback solar-array model — ✅ DONE

| | |
|---|---|
| **Source** | `power_model_extract.md` §4c; `power_analysis.md` §5; `textbook_vs_sim.md` §3 row 2 |
| **Rationale** | When the AOCS coarse-sun-sensor vector is invalid — which is *all* of phases 0–3, i.e. the whole bootloader and early-LEOP period — `eps_basic` falls back to a legacy two-array model generating **≈245 W sunlit** against the 6-panel model's ≈82 W. The real pre-wing-deploy generation is ~22 W sunlit average, which cannot sustain even the 30 W commissioned platform. LEOP is therefore only survivable in the simulator because of a bug, and every LEOP power exercise is physically meaningless. |
| **Scope** | Delete the fallback branch, or replace it with a cosine-of-beta model over the *actual* configured panel areas (0.06 + 0.03 m² pre-deploy) so it degrades gracefully instead of over-generating by 3×. Re-run the LEOP sequence tests and `test_commissioning/test_leop_sequence.py` (28 tests); expect the procedure order to matter — **wing deploy must precede wheel and heater commissioning**, which is the correct lesson and is currently hidden. |
| **Effort** | **S** (the change) / **M** (if procedures need re-ordering) |
| **Risk** | Medium — it will make LEOP genuinely power-limited, which may break scenario timings. That is the point, but budget for re-tuning `sequential_checkout.yaml` and LEOP-007. |
| **Pedagogical value** | High. Power-limited LEOP with a hard ordering constraint (deploy before you switch things on) is one of the most transferable lessons in spacecraft operations, and it is currently un-teachable because the sim over-generates by a factor of three exactly when it matters. |

### N-10 · Review the shipped S19 action functions — 🆕 **decision needed from Franz**

| | |
|---|---|
| **Source** | defect **#70** "Related" note; `test_strategy.md` §4a; new `tests/test_content/test_s19_rules_content.py` |
| **Rationale** | The S19 *mechanism* is now correct: defect #70 fixed the report packing, added `trigger_kind` so event ids and monitored-parameter ids stop colliding, and blocked the OBC-critical funcs 52/53/55/56 on the autonomous path. The *content* is not. **13 of the 26 rules in `configs/eosat1/monitoring/s19_rules.yaml` dispatch an `action_func_id` that contradicts their own `description`** — e.g. `ea_id 1001 "eps_bus_undervoltage_payload_off"` dispatches func **10**, which is `AOCS_SLEW_TO`; `1002 "fpa_cooler_off"` dispatches func **11** (`AOCS_CHECK_MOMENTUM`); `1003 "payload_power_off"` dispatches func **14** (`AOCS_RW_RAMP_DOWN`). Every autonomous response a student observes in an anomaly exercise is therefore the wrong response, delivered confidently. The new content test deliberately asserts only that each func *routes to a real handler*, not that it is the *right* handler, so the suite is green and the gap stays visible. |
| **Why it is not fixed already** | Changing `action_func_id` changes autonomous spacecraft behaviour in every scenario and every FDIR exercise. Which response is *intended* for each of the 13 rules is a courseware decision, not a code decision — it is **awaiting Franz's review**. The 13 candidate corrections are enumerable directly from the descriptions and the S8 catalogue; do not guess them. |
| **Scope** | (a) Franz reviews the 13 rules and states the intended func per rule. (b) Apply, one commit. (c) Tighten `test_s19_rules_content.py` from "routes to a handler" to "routes to *this* handler", pinning the reviewed mapping so it cannot drift again. (d) Re-run the FDIR and anomaly scenarios; expect a small number of scenario timings to change, which is the point. |
| **Effort** | **S** (the edit) once the review exists; the review is the long pole |
| **Risk** | Medium *until reviewed*, low after — the pinning test is what converts this from a recurring defect class into a one-off. |
| **Pedagogical value** | High and currently negative: autonomous FDIR response is the whole point of Ch 12/13's anomaly exercises, and 13 of 26 rules currently teach the wrong lesson. |

### N-11 · Reconcile the data-rate and image-size arithmetic — ✅ DONE

| | |
|---|---|
| **Source** | Unblocked by N-8; supersedes the decision half of **C-6** and **L-9**; `textbook_vs_sim.md` §3 rows 8 and 9 |
| **Rationale** | N-8 made 64 kbps genuinely reachable and made the *rate* observable end to end (the RF pipeline's symbol rate, occupied bandwidth and spectrum now follow the commanded rate). That closes the link half of the data-budget lesson and leaves the payload half glaringly open: the sim still models **800 MB/image** against a 20 GB store, so one image is **~28 hours** of 64 kbps downlink, against the book's ~4 MB/session and its "4 MB → 32 s" rule of thumb (pp. 360, 404). Every data-budget, store-management and S13 image-downlink exercise is unworkable by two orders of magnitude — and now that the rate is real, the mismatch is *visible* to students rather than merely latent. |
| **Scope** | Take the C-6 position (shrink the simulator: a few MB per image in `payload.yaml`, store scaled proportionally so `store_used > 95 %` stays meaningful), then re-derive the whole budget in one pass: image size → store capacity → S15 dump pacing at both 1 kbps and 64 kbps → the pass-duration numbers in the book's rule of thumb → the planner's `data_dump` activity duration model. Add one arithmetic content test — "one image fits in one nominal pass at the high rate" — so the two halves cannot drift apart again. |
| **Effort** | **S** (config) + **S** (the budget test); **M** if the planner's duration model needs rework |
| **Risk** | Low-medium — check the S13 transfer tests, the S15 dump-pacing tests and the payload store limits. |
| **Pedagogical value** | High. This is the arithmetic the whole pass-planning lesson rests on (Ch 3.3 §9, Ch 7.20 §2, App C.15), and it is the last thing standing between a student and a data budget that actually closes. |

**Resolved 2026-07-29 (defect #82).** Franz's decision: take the C-6 position and
shrink the simulator to the textbook's number.

| | Before | After |
|---|---|---|
| `payload.yaml image_size_mb` | 800.0 MB | **4.0 MB** |
| Effective downlink rate | 64 kbps × 0.8 = 51.2 kbps | unchanged |
| Daily downlink capacity (5 stations, 385 contact-min/day) | 116.7 MB/day | unchanged |
| Effective contact needed for ONE image | ~36 h ⇒ **6.8 days** of the whole network | **655 s** ≈ one good pass |
| Images/day the network could sustain | 0.15 | **29** |
| Shipped `nominal_ops_day` plan | generated 1660 MB, downlinked 7.3 MB, `plan_closes: false`, 14.2 days to drain | generated **8.6 MB**, downlinked **8.6 MB**, `plan_closes: **true**`, backlog 0 |
| Margin on the plan's own dump passes | −1653 MB | **+19.2 MB on 27.8 MB of capacity (69 %)** |

Two consequences were chased in the same pass rather than papered over:

* **The activity catalogue's other data volumes were scaled to the 800 MB world.**
  `calibration` claimed 50 MB from an `AOCS_GYRO_CALIBRATION` (2.2 Mbit/s of
  calibration telemetry) and `housekeeping_collection` 10 MB from one S3.27 HK
  request (1.3 Mbit/s of housekeeping). Now 0.5 MB and 0.05 MB, with a content
  test that every activity's volume is reachable at the payload's own 80 Mbps
  readout rate.
* **`nominal_ops_day` could not close at ANY image size.** Its four dumps were
  on the next four *consecutive* passes, all inside the first hour — before the
  second acquisition and the calibration. Changed to eight dumps at pass stride
  3, spanning ~9 h, so there is downlink capacity after the last data-generating
  activity.

Not done, deliberately: **the store was not rescaled.** Keeping 20 GB makes
downlink, not storage, the binding constraint — which is the lesson — but it
means the store now holds ~5000 raw images, so a fill-the-store exercise has to
be driven from the instructor console (`store_used` injection, or the
`payload_memory_failure` scenario) rather than by capturing images. Flagged
here as a separate, explicit decision rather than folded into this one.

Compression stayed coherent and needed no change: `image_size_mb` is the RAW
frame, and the on-board CCSDS-123 compressor (scene-dependent ratio 1.5–4.0)
stores 1.0–2.7 MB of it, so the planner's 4 MB is the uncompressed worst case.
`data_rate_mbps: 80` and `line_rate_hz: 500` describe the focal-plane readout,
not the 120 s acquisition window — a 4 MB frame is 0.4 s of readout inside a
window that is mostly slew, settle and cooler stabilisation. Both facts are now
written down in `payload.yaml`.

---

## Tier 2 — NEXT

### X-1 · Mission Plan panel in the MCS, and activity-state feedback

| | |
|---|---|
| **Source** | I-1, I-4 (`REVIEW_2026-07.md` §3); `textbook_vs_sim.md` §2.3 MPS row |
| **Rationale** | The planner exposes `GET /api/schedule`; **nothing in `smo-mcs/src` ever requests it**. Operators cannot see what is planned. The activity lifecycle is open-loop: `ActivityState` has `EXECUTING=3, COMPLETED=4`, but the only automatic transition is PLANNED→UPLOADED, and the MCS never reports execution back. Defect #44 fixed the *forward* path (the plan now reaches the spacecraft as time-tagged S11 commands at the planned time); the *return* path does not exist. The book teaches this as a closed loop (Ch 7.3, Ch 8). |
| **Scope** | (a) MCS UI panel polling `/api/schedule`, rendering activities with state, start time and station, on the same sim-time base the contact timeline now uses. (b) MCS POSTs state transitions back as the S11 schedule fires — the S1.7 reports for embedded commands, already correlated in the verification log by defect #46, are the natural trigger. (c) Make the planner's MCS URL configurable (see L-6). |
| **Effort** | **M** |
| **Risk** | Low-medium; additive, but adds a fourth consumer of the contact/schedule schema — reuse `_normalize_contact` rather than writing a second translator. |
| **Pedagogical value** | Completes the MPS→MCS→spacecraft→MCS loop that Ch 7.3 and Ch 8 describe. Without it a student plans a pass and then loses sight of it, which is the opposite of the lesson. |

### X-2 · One machine-readable PUS routing table

| | |
|---|---|
| **Source** | DI-09 (Major); defects #37, #38 |
| **Rationale** | The same service/subtype/function-ID truth is re-declared in at least four hand-maintained places: `engine._check_tc_acceptance`, the engine's bootloader func allowlist (whose own comment admits it "mirrors `obdh_basic._BOOTLOADER_ALLOWED`"), `service_dispatch.py`'s `_func_id_owner`/`_func_id_subsys` tables, `mcs/pus_services.yaml` (with a `load_pus_services` called by nothing), and `commands/tc_catalog.yaml`. `smo_common/protocol/pus_services.py` exists and is imported by no package. **This structure has already produced two real defects**: #37, where a stale S8 func map caused the spacecraft to autonomously boot its own OBC, and #38, where commands were silently dropped. Adding S18 this session required four coordinated hand-edits — the finding restated as lived experience. |
| **Scope** | Promote one table (config or a generated module) to the single source for: acceptance checks, dispatch routing, the bootloader allowlist, S19 action validation, S18 step pre-validation, and the MCS catalogue. Delete `pus_services.py` and `load_pus_services`, or wire them in. Add a content test asserting the four consumers agree — which is cheap once there is one table. |
| **Effort** | **M** |
| **Risk** | Medium — it is the spine of the command path. Do it with `tests/test_acceptance/test_commands.py` (which iterates the whole 183-command catalogue through real dispatch) as the gate. |
| **Pedagogical value** | Indirect but real: this is the defect *class* that has bitten the project hardest, and it is a textbook illustration of single-source-of-truth for a systems-engineering course. Worth writing up as a case study for Ch 4 of the book. |

### X-3 · Propulsion — **position: build the minimal model, keep the procedures**

| | |
|---|---|
| **Source** | `textbook_vs_sim.md` §1 divergence 1 and §2.2 propulsion row; `textbook_inventory.md` §4 flag 10 |
| **The divergence** | The book teaches propulsion **in depth**: ΔV budgeting and the rocket equation (pp. 122–123, App C.3 p. 530), four technology families with worked sizing and cost — chemical mono/bi-prop, cold gas, electric FEEP/Hall, and the no-propulsion trade (pp. 192–198), with a worked 100 m/s comparison on a 5 kg 3U. Part 2 then gives EOSAT-1 *operational* propulsion: orbit-maintenance procedures **NOM-004** and **NOM-005** (FDS ΔV plan → S11 time-tagged upload → `PROP_ARM` → burn → post-burn state vector, pp. 447–449), contingency **CTG-006**, and a **Propulsion Engineer** console position in Appendix A (p. 518). In the simulator there is **nothing**: `PROP_ARM` has zero hits anywhere in `configs/` or `packages/`; the S8 catalogue (0–84 + 100–107) has no propulsion block; no propulsion model exists. The only trace is the thruster *heater* circuit (`tc_catalog.yaml:805` HEATER_THRUSTER, circuit 2). NOM-004, NOM-005 and CTG-006 are un-runnable as written, and FDS manoeuvre planning (p. 350) has nothing behind it. The book half-flags this — §5.7's spacecraft description omits propulsion — but ships the procedures anyway. |
| **The two options** | **(a) Build a minimal propulsion model** — an S8 function block (arm/disarm, set burn parameters, execute, abort), a propellant-tank state (mass, pressure, temperature) feeding HK, an impulsive ΔV applied to the propagator's state vector, `PROP_ARM` as a genuine two-step safety interlock, and the thruster heater made load-bearing (arm refused below a minimum catalyst-bed temperature). **(b) Delete NOM-004, NOM-005, CTG-006, the Propulsion Engineer position and the FDS manoeuvre-planning claims** from the course, with a caveat box in Part 1 saying the design material is taught but not flown. |
| **Position** | **Build it (option a).** Three reasons. First, the *design* half is already the most heavily taught single subsystem in Part 1 — four technology families, worked sizing, App C.3 verification — so deleting the operations half leaves the course's deepest design topic with no operational counterpart, which is precisely the design→operations bridge the two-part structure exists to provide. Second, the procedures, the console position and the ΔV-planning FDS function are **already written** — the marginal cost of making them runnable is far below the cost of writing them, and deleting them is a permanent loss of existing courseware. Third, the enabling machinery now exists: the S11 ground path works (defect #46), the S18 OBCP interpreter can carry a burn sequence (defect #48), and `tools/orbit_tools.py` already does propagation — a burn is an impulsive ΔV on the propagator state, which is a genuinely small piece of physics. Scope it deliberately small: **impulsive, single-axis, no finite-burn attitude coupling** — enough to make NOM-004/005 runnable and the ΔV arithmetic real, not a propulsion research model. |
| **Scope sketch** | New `models/prop_basic.py` (tank mass/pressure/temp, valve and catalyst-bed state, arm interlock, thrust ×  Isp → ΔV, propellant depletion, 5–8 failure modes for the catalogue); S8 func block in a free range with `PROP_ARM`/`PROP_DISARM`/`PROP_SET_BURN`/`PROP_EXECUTE`/`PROP_ABORT`; `subsystems/prop.yaml` (Isp, thrust, tank capacity, min bed temperature) as the hardware sheet, with burn safety limits in `simulation.yaml`; propagator gains `apply_delta_v(dv_vector)` and a TLE re-fit or state-vector propagation mode; HK SID for propulsion; S12 monitors and one or two S19 rules; `tc_catalog.yaml` entries; an FDS ΔV-plan output in `tools/orbit_tools.py`; and re-validation of NOM-004/005/CTG-006 against the built model. |
| **Effort** | **M** (2–5 days for the model, catalogue and config) — **L** if the propagator is made to re-fit a TLE after a burn rather than switching to state-vector propagation. Recommend the simpler path first. |
| **Risk** | Medium. The propagator currently only knows how to advance an SGP4 TLE; applying a ΔV mid-run means either re-fitting elements or carrying a state-vector mode, and both interact with the eclipse/contact scan windows and with N-5's epoch handling. Sequence it **after N-5**. |
| **Pedagogical value** | **Very high.** It closes the largest book↔sim gap in the entire review, resurrects three written procedures and a console position, and gives students the one operation where a ground-computed number (a ΔV) is uploaded, executed autonomously on board, and then *verified against a measured orbit change* — the complete FDS loop, and the natural showcase for the new S11 and S18 capability. |

### X-4 · Delete `files/`

| | |
|---|---|
| **Source** | DI-10 (Major) |
| **Rationale** | A full pre-monorepo standalone implementation (20 modules: `engine.py` 553 lines, `service_handlers.py` 1450, `ecss_decommutator.py` 1600, `scenario_engine.py` 624, plus a parallel set of HTML UIs) sits beside its successor with overlapping module names, dated 2026-04-10 while `packages/` absorbed defects #23–#52. It has a populated `__pycache__`, so it has been executed from this tree. A grep for a bug lands in two `engine.py`s and a new contributor cannot tell which decommutator is real. |
| **Scope** | Move to an `attic/` branch or delete (git history preserves it — after N-3). If anything is still wanted, e.g. `ecss_decommutator` features, port it explicitly into `smo_common` first. |
| **Effort** | **S** |
| **Risk** | Low once N-3 is done. Grep for imports first. |
| **Pedagogical value** | Direct: students grep the repository constantly, and half their hits are currently three months stale. |

### X-5 · Content tests and CI tiers

| | |
|---|---|
| **Source** | `test_strategy.md` §4 P1 items; `REVIEW_2026-07.md` §2.3 |
| **Rationale** | Config and ops content is ~40 % covered and is historically the buggiest layer — defects #34, #37 and #40 were all config bugs, as were #45 and #52 this session. The rule that works is already proven in the tree: `test_activity_funcids.py` derives its expectations from the real dispatcher, and the scenario loader's failure registry introspects the models' own source. |
| **Scope** | Six test files: **P1-4** every `s19_rules.yaml` rule's `action_func_id` resolves to the intended handler and dangerous funcs (52/53/55/56 — reboot, redundant-OBC switch) appear only where intended; **P1-5** S13 abort / NAK / timeout / concurrent transfers; **P1-6** the six `/api/constraints/*` planner routes; **P1-7** every procedure-referenced command exists in `tc_catalog.yaml`; **P1-1/2/3** all 31 scenarios load, inject only known failures, auto-stop and debrief; **P2** display-endpoint shape tests. Plus pytest markers (`unit`, `engine`, `api`, `content`, `rf`, `slow`, `browser`, `rfhw`) and a CI config with the three tiers in `REVIEW_2026-07.md` §2.3. |
| **Effort** | **M** |
| **Risk** | Low. Expect the new tests to fail first — that is the finding, not the problem. |
| **Pedagogical value** | Moderate directly, high indirectly: it is how the book↔sim and config↔code drift documented throughout this review gets caught mechanically instead of by a review like this one. Also makes student contributions safe to accept. |

### X-6 · Route breakpoint save/load through the tick thread

| | |
|---|---|
| **Source** | DI-12 (Major) |
| **Rationale** | The HTTP handlers construct a `BreakpointManager` and call `save()`/`load()` **inline on the aiohttp thread** while the tick thread runs, whereas the WebSocket path correctly routes the same operation through `instr_queue`. One operation, two concurrency regimes. A load snapshots or restores every subsystem `_state` and the orbit clock while `_tick_once` may be mid-mutation — the bug class of fixed defect #35, reintroduced through the side door. The surface grew this session: `breakpoints.py` now also carries the `obcp` key with live interpreter state. |
| **Scope** | Route the HTTP handlers through `instr_queue` and return the result via a future, or pause the engine around save/load. |
| **Effort** | **S** |
| **Risk** | Low. Do it with or after N-2, which supplies the same machinery. |
| **Pedagogical value** | Protects the core teaching workflow — save → inject failure → load → compare. A torn restore produces an impossible spacecraft state that is extremely hard for a student to diagnose, because it has no physical cause. |

### X-7 · `dispatch()` returns a result object

| | |
|---|---|
| **Source** | DI-08 (Major) |
| **Rationale** | Command success/failure is signalled by mutating `dispatcher._last_error`, which the engine resets before and inspects after each call. Not reentrant, invisible in the signature, and any nested dispatch can clobber the outer command's flag — and there are now **three** sources of nested dispatch (S11 time-tagged release, S19 event-actions, S18 OBCP steps). S1.7 "execution complete" versus S1.8 "failure" is the operator's ground truth for every command in the course. |
| **Scope** | Return `(packets, error_code \| None)` from `dispatch()`; delete the side-channel and its resets. |
| **Effort** | **S** |
| **Risk** | Low-medium; ~5 call sites, all covered by acceptance tests. |
| **Pedagogical value** | Protects command verification, which is the single most-used operator skill in the whole syllabus. |

### X-8 · Fail loudly on unknown config keys

| | |
|---|---|
| **Source** | DI-15 (Major), DI-07 remainder |
| **Rationale** | `smo-rfsim`'s `from_yaml` silently ignores unknown keys, and is already dropping a real one — `rfsim.yaml:19` sets `channel.frequency_hz: 2200.0e6` and `ChannelConfig` has no such field, so the setting is discarded on every start-up. This session found four *more* silently-dropped sections on the pydantic side (`eps.yaml solar_panels` — which meant the solar wings ran on a code default regardless of the YAML — plus `power_lines`, `tcs.yaml thermal_coupling` and `event_thresholds`). Unvalidated config is indistinguishable from working config. |
| **Scope** | Log-or-raise on unknown keys in rfsim's `from_yaml`; set `model_config = ConfigDict(extra="forbid")` on the `smo_common` schemas (or `extra="warn"` behind a flag during migration); add `frequency_hz` to `ChannelConfig`; rename one of the two colliding `NetworkConfig` classes; finish DI-07 by loading the `mission.yaml network` ports in `server.py::main` or deleting the unused `instr_port` / `tm_max_clients`. |
| **Effort** | **S** |
| **Risk** | Low-medium — `extra="forbid"` will surface every stale key at once. Run it once, fix the list, then enable. |
| **Pedagogical value** | Direct: students edit YAML constantly, and a typo currently produces silence. |

### X-9 · Finish the synthetic-data removal

| | |
|---|---|
| **Source** | DI-03 remainder; defect #47 |
| **Rationale** | Defect #47 deleted `_build_synthetic_contacts` and tagged the stored-TM dump generator `synthetic: true` end-to-end with a red UI banner. The generator itself remains: on a dump command against an empty archive the MCS still invents 60 plausible SoC / rate / temperature points. In a training mission-control tool, plausible fabricated telemetry is a category error even when labelled. |
| **Scope** | Delete `_generate_dump_data`; return an explicit "no archived data" state the UI renders. If a demo fallback is wanted, keep it behind a config flag defaulting to **off**. |
| **Effort** | **S** |
| **Risk** | Low; the honest-empty-state pattern is already implemented for contacts. |
| **Pedagogical value** | Direct and important: anomaly investigation on invented data is worse than no exercise, because the student draws a confident wrong conclusion and is never corrected. |

### X-10 · Public API for the MCS data path

| | |
|---|---|
| **Source** | DI-14 (Major) |
| **Rationale** | `self._tm_processor._process_hk(pkt.data_field)` is, per its own comment, the **only** HK decode path in the system, and `self._tc_manager._seq_count` labels the verification log. Renaming either private silently breaks the sole route by which spacecraft state reaches mission control, with no interface contract and no test seam. |
| **Scope** | Promote `process_hk()` and `last_seq()` / `build_command() -> (pkt, seq)` to public API; keep thin private delegates during migration. |
| **Effort** | **S** |
| **Risk** | Low. |
| **Pedagogical value** | Indirect; makes the TM path testable in isolation, which X-5's display tests need. |

### X-11 · Complete the ground-station single source

| | |
|---|---|
| **Source** | DI-04 remainder; defect #42 |
| **Rationale** | Defect #42 declared `orbit.yaml` canonical, re-scoped `planning/ground_stations.yaml` as the RF layer, and added a divergence warning — but the geometry is still physically duplicated in two files and named a third time in `mission.yaml`. The warning makes drift visible; it does not prevent it. |
| **Scope** | One `ground_stations.yaml` holding geometry **and** RF (frequencies, G/T, antenna, TX power), referenced rather than copied by the orbit and planning concerns, with a single loader in `smo_common`. Delete the duplicated block and the `mission.yaml` name list. Naturally combined with **N-4**, which needs the per-station RF fields to be live anyway. |
| **Effort** | **S–M** |
| **Risk** | Low; three consumers, all tested. |
| **Pedagogical value** | Moderate; it is the worked example of the single-source principle the course teaches in Ch 4. |

### X-12 · Converge the four procedure representations — 🟡 PARTIALLY DONE (defect #83)

| | |
|---|---|
| **Source** | `pus_services_audit.md` §7.1–7.4 (2026-07-28) |
| **Rationale** | "Procedure" means four different things in the tree and only one path between them works. There are (1) 64 shipped markdown ops procedures with `**TC:**` annotations, (2) `activity_types.yaml` untagged step lists, (3) the MCS builder's tagged `{type: command\|wait\|tlm_check\|go_nogo\|note}` form, and (4) S18 OBCP step blobs — linked by nothing but a `procedure_ref` string. A planner-built procedure *can* be run by the MCS runner. Nothing else works: a builder-saved custom procedure cannot be run from the UI (`pbLoadCustom()` loads it into the *editor* only, though `_handle_proc_load` would accept it — the button is simply absent); the 64 markdown procedures are unreachable from the browser (`/api/procedures`, `/api/procedures/{path}` and `/api/procedure/index` have **no call sites** in `static/`); and no path emits an S18 load blob, so defect #48's OBCP capability is only reachable by hand-writing YAML. |
| **Scope** | (a) One canonical step schema — adopt the tagged form, model it in `smo_common`, and use one loader from the runner, the planner and the OBCP encoder; accept the untagged dialect for one release, then migrate `activity_types.yaml`. (b) Make the shipped procedures machine-readable: YAML front-matter or a sibling `.steps.yaml` generated from the `**TC:**` annotations — `tests/test_content/test_procedures_content.py` already parses and validates them, so reuse its parser. (c) Wire the three missing buttons (run-saved-custom, procedure-source selector, `tlm_check.parameter` from `/api/param-catalog`). (d) One procedure, three execution modes: ground-run, time-tagged via `/api/schedule/upload-s11`, or on-board via `ObcpManager.encode_load_blob` + TC(18,1)/TC(18,3), dropping telemetry-check and go/no-go steps with an explicit warning exactly as the S11 path already does. (e) Expose S18 in the MCS command builder. |
| **Effort** | **M** for (a)(c)(d)(e); **M/L** for (b) |
| **Risk** | Low-medium. Additive; the migration window is what keeps it low. |
| **Pedagogical value** | **High — the single biggest training-value win in the audit.** It makes the 64 real ops procedures browsable and partially runnable from the console the students actually use, and it turns the autonomy taxonomy (ground / time-tagged / on-board, from one source) into something a student can *execute* rather than read about — which is exactly the C-5 lesson. |

**Landed 2026-07-29 (defect #83)** — (a), (b-framework), (c) done; (d) and (e)
remain.

* **(a) One canonical schema.** `smo_common.procedures` defines it, with
  adapters from the untagged `activity_types.yaml` dialect, the builder's
  tagged dialect and the markdown `**TC:**` annotations.
  `ProcedureRunner.load()` canonicalises whatever it is given, so no config had
  to be migrated. Normative description: `docs/PROCEDURE_SCHEMA.md`.
* **(b) The 64 markdown procedures are reachable — framework + 4 exemplars.**
  `procedure_index.yaml` entries may now carry
  `runnable: {kind: activity_type|steps_file|markdown, ref: …}`;
  `GET /api/procedure/index` reports `runnable` / `runnable_steps`, and the
  Procedures tab shows the markdown alongside the run. Exemplars: NOM-002,
  NOM-003, NOM-009 (activity-type links) and NOM-008 (markdown adapter).
  Linking the remaining 60 is a per-procedure Flight-Ops decision, not a
  mechanical conversion, and is deliberately left open.
* **(c) The missing buttons.** A source selector (activity type / ops procedure
  / saved procedure), "Run These Steps" for the builder, `_step_desc` fixed so
  builder steps render as text, and `tlm_check.parameter` backed by the new
  `GET /api/procedure/tlm-vocabulary` and rejected at *save* time when it names
  something the runner cannot read. The builder's `command` step instantiates
  the existing `renderCommandBuilder()` component instead of a third packer.
* **(d) One procedure, three execution modes — NOT done.** Ground-run and
  time-tagged already existed; the ground → S18/OBCP compiler does not. The
  canonical `command` and `wait` steps map cleanly onto the OBCP step model, so
  this is now a small, well-defined piece of work rather than an open question.
* **(e) S18 in the command builder — NOT done.**

### X-13 · Draw the imaging targets on the planner map — 🆕

| | |
|---|---|
| **Source** | `planner_audit.md` §imaging (2026-07-28); defect **#61** |
| **Rationale** | Imaging targets are a fixed YAML list of lat/lon bounding boxes. There is **no map drawing, no target polygon, no swath, and no sub-satellite track** in the planner UI: `/api/imaging/targets` exists and is never called from the browser. Defect #61 made the opportunity geometry and constraints correct — so the planner now computes the right answer and shows the operator none of the reasoning. A student picks an imaging opportunity from a list of times with no way to see *why* that time and not another. |
| **Scope** | Draw the 7 target boxes from `/api/imaging/targets` on the existing planner map; overlay the sub-satellite point and the ground track for the selected opportunity window; shade the illuminated / eclipsed arcs and the elevation-mask cone. Read-only first — drawing *new* targets on the map is a separate, larger item. |
| **Effort** | **S** for the read-only overlay; **M** if target creation by drawing is wanted |
| **Risk** | Low; purely additive UI over an endpoint that already exists. |
| **Pedagogical value** | High for the imaging half of the planning lesson: opportunity selection is a geometry argument, and geometry arguments are made on maps. |

### X-14 · Give S12 delta monitoring some shipped coverage — 🆕

| | |
|---|---|
| **Source** | `pus_services_audit.md`; defect **#69** |
| **Rationale** | `ServiceDispatcher` implements both S12 check types — `0` absolute and `1` delta (rate-of-change since the previous check, with correct baseline invalidation on power-off and on re-enable) — and defect #69 added the real TM(12,9)/(12,10) transition reports. But **all 38 shipped definitions in `s12_definitions.yaml` use `check_type: 0`**. The delta path therefore has no shipped exercise, no operator-visible example, and only unit-test coverage. Rate-of-change limits are how real missions catch a battery discharging too fast, a wheel spinning up abnormally or an FPA warming after a cooler trip — none of which an absolute band catches until it is already too late. |
| **Scope** | Add 3–5 delta definitions where they carry the lesson: `eps.bat_soc` falling faster than N %/min, `aocs.rw*_rpm` changing faster than the wheel's commanded ramp, `tcs.fpa_temp` rising after cooler-off. Document `check_type` in the operations manual and in `PUS_SERVICE_REFERENCE.md` §12 (the wire format is now documented there). Extend the S12 content test to assert both check types are exercised. |
| **Effort** | **S** |
| **Risk** | Low — but tune the thresholds against a real run, since a delta limit that is too tight alarms on every tick-quantisation step. |
| **Pedagogical value** | Moderate-high: "the value is fine, the *trend* is not" is a core monitoring lesson that the shipped config currently cannot teach at all. |

### X-15 · Port the SatHack / offline impairment set into the live channel — 🆕

| | |
|---|---|
| **Source** | `rf_audit.md` Q6; defects **#53**, **#55**, **#56** |
| **Rationale** | Defect #56 instantiated `SpaceLinkChannel` in the live pipeline and added an **impairment plug-in registry** (`register_impairment(name, factory)` / `available_impairments()` / `build_impairment(spec, cfg)` in `channel/space_link.py`) reachable from `rfsim.yaml`, from `POST /api/channel` and from the radio UI without touching `SpaceLinkChannel`. Defect #53 made the spectrum analyser and eye diagram live, so an impairment is now *visible* for the first time. The landing zone exists and is empty: the well-developed offline chain in `recording/iq_recorder.py` (power-law phase noise, S-curve Doppler, I/Q imbalance, DC offset, quantisation, band-edge filtering, impulsive RFI, galactic noise) is still offline-only, and the SatHack impairment set is not ported at all. |
| **Scope** | Lift the receiver-artefact block out of `iq_recorder.py` into a reusable `dsp/frontend_artefacts.py` shared by the recorder and the live channel, and register each artefact as a plug-in. Port the SatHack impairments the same way. Then: a `doppler_rate_hz_s` / free-running mode so the RX has to *track* rather than being handed the known offset (`correlator_rx.py:150-158` currently pre-compensates with the truth), plus an instructor "Doppler compensation OFF" toggle. Expose the lot on the control API and the radio UI — Eb/N0 slider, phase-noise linewidth, fading K and rate, "add a CW interferer at ±X kHz, Y dB". Separately (one line, do it first): add the seven already-implemented `gs_*` ground-segment failures to the instructor dropdown, where they are currently invisible. |
| **Effort** | **M** (the port and the UI); **L** only if a non-linear PA model (Saleh AM/AM + AM/PM, for visible spectral regrowth) is added |
| **Risk** | Low-medium — check per-chunk state continuity for the stateful impairments and CPU cost at the 256 kHz–512 kHz sample rates the pipeline now reaches at 64 kbps. |
| **Pedagogical value** | High, and newly *unlocked*: "turn the noise up and watch the constellation open, the eye close and the interferer appear in the spectrum" is only teachable now that the three displays are live. A CW interferer at a settable offset is the single most valuable addition for a spectrum-analyser lesson. |

### §S · SpaceCDF-side items

### S-1 · Decompose `exports.py`

| | |
|---|---|
| **Source** | DI-18 (Major), DI-19 (Major) |
| **Rationale** | `routers/exports.py` is **2516 lines** (re-verified): 12 export endpoints plus ~20 `_derive_*` engineering functions — state machines, memory maps, TM/TC tables, fault handlers, architecture derivation — plus inline python-docx assembly and 23 `except Exception` sites, while a `services/` layer (`docx_generator.py`, `semp_generator.py`, `did_generator.py`) sits largely bypassed. It is the largest file in the repository and grows with every export type. **The derivation rules are the actual engineering content of the tool**, and they are buried in a web router where nobody looks and nothing can test them without HTTP. Compounding it, `export_test_plan` reaches into another router's module-level mutable global (`from ..routers.requirements import _requirements`, line 2069) while other exports read the database — two divergent sources of requirement truth, which breaks outright under multi-worker deployment and returns stale or empty test plans after a restart. |
| **Scope** | Move `_derive_*` into `services/` (or `spacecdf_common`) with direct unit tests; keep routers thin; share the docx assembly with the existing generators; read requirements through the repository layer and make the `requirements` router persist to the same store. |
| **Effort** | **L** |
| **Risk** | Medium — 12 endpoints, and the export bundles are review-gate deliverables (MCR/SRR/SDR/PDR/CDR packs). Do it export-by-export with a golden-file test per bundle. |
| **Pedagogical value** | High indirectly: the `_derive_*` functions *are* the design reasoning the tool teaches. Surfaced in a services layer with tests, they become readable and reviewable by students; buried in a router they are effectively private. |

### S-2 · Fill the four empty test directories

| | |
|---|---|
| **Source** | `test_strategy.md` §7 |
| **Rationale** | `tests/test_agents`, `tests/test_api`, `tests/test_convergence` and `tests/test_physics` exist and are **empty** (verified: 0 files each) — the intent is recorded, the content is not. Against that: **24 router modules with no per-router API tests**, ~30 tier-1/tier-2 agents with no dedicated tests (exercised only indirectly through `four_missions` and `reactivity_e2e`), and 21 physics-sanity tests spread across 15 physics modules. |
| **Scope** | (a) `test_api`: FastAPI `TestClient` smoke per router — 200 plus schema on list/get, one mutating round-trip each. (b) `test_agents`: one parametrised contract test — every tier-1/tier-2 agent runs on a canonical mission state and returns outputs matching its declared schema, with no NaN, negative mass or negative power. (c) `test_physics`: per-module spot checks against hand-computed values — link budget closes, radiation dose monotonic with shielding, debris flux versus altitude has the right shape. |
| **Effort** | **M** |
| **Risk** | Low. |
| **Pedagogical value** | Direct: Appendix D (p. 539) states that **every worked example in the 546-page textbook was computed by running the SpaceCDF tool**. An untested agent is an untested textbook answer. The physics spot checks are the guarantee that the book's numbers stay true. |

### S-3 · Version the SpaceCDF→SMO export contract

| | |
|---|---|
| **Source** | DI-20 (Major) |
| **Rationale** | The SpaceCDF exporter generates ~20 SMO YAML files with the SMO config schema **re-implemented by hand in a second repository**, with no import of, or generation from, `smo_common.config.schemas`. This is the only integration seam between the two systems and it is unversioned and untested against the real loaders. SMO-side schema changes will silently produce configs the pydantic loaders reject — or, worse, accept with changed meaning. **The risk just increased**: this session added `SimulationConfig` and declared `EPSConfig.solar_panels`/`power_lines`, `TCSConfig.thermal_coupling`/`event_thresholds` and `MissionConfig.network`. An exporter that does not know about `simulation.yaml` now produces a config set that boots with none of the item-9 power tuning. |
| **Scope** | Publish `smo_common.config.schemas` as the contract; SpaceCDF imports it and validates every generated file with the same pydantic models before writing. Add a round-trip test that boots the SMO loader (and ideally a `SimulationEngine`) on a freshly exported config set. Version the contract explicitly so a mismatch is an error, not a surprise. |
| **Effort** | **M** |
| **Risk** | Low-medium — it will fail immediately, which is the value. |
| **Pedagogical value** | High and unusually direct: this seam **is** the design→operations handover the whole two-part course is built around. A student who designs a mission in SpaceCDF and then flies it in the simulator is exercising exactly this interface, and right now it is the least-defended code in either repository. |

---

## Tier 3 — LATER

| ID | Item | Source | Scope sketch | Effort | Risk | Pedagogical value |
|---|---|---|---|---|---|---|
| **L-1** | Decompose `SimulationEngine` | DI-06 | 2363 lines, ~60 methods, 300-line constructor, reaching into subsystem privates (`obdh._state.sw_image`, 50 lines of `aocs._state.*`, `self._tm_storage._stores`), pervasive `hasattr` duck-typing that turns API drift into silent no-ops, and an `if True:` wrapping the tick body. Extract the phase/bootloader state machine, HK scheduler and dump manager into collaborators; give models an explicit `enter_unpowered()` / `set_boot_image()` interface; delete the `if True:`. | **L** | Med-High | Moderate direct; high as a student software-architecture exercise on a real system |
| **L-2** | Decompose `MCSServer` | DI-13 | 2128 lines, ~70 route handlers plus TM decode, TC building, ground time, orbit propagation, go/no-go, procedure glue, doc serving. Split into modules (tm, tc, time, displays, procedures, api); replace the raw `yaml.safe_load` + hand-rolled `tle_line1`/`TLE_LINE1` fallbacks with the validated `smo_common` loaders the engine and planner already use; hoist ~15 function-local imports. | **L** | Med | Moderate |
| **L-3** | Error-handling house rule | DI-17 | 160 broad `except Exception` sites; a substantial subset discard entirely — e.g. an APID switch failure falls through to `pass`, so the spacecraft keeps transmitting on the wrong APID. Rule: no bare `except Exception` without a `logger.warning` carrying context; narrow the single-known-failure guards to specific types. Prioritise the engine and the MCS TM path. | **M** | Low | Moderate; this idiom is how ten historic defects stayed hidden |
| **L-4** | Symbolic parameter names in scenarios | §7.3 | `set_parameter` and `condition:` still need numeric or hex parameter ids; symbolic names are rejected because there is no name→id registry — though the mapping already exists in `subsystems/*.yaml param_ids`. Build the registry at load; accept `eps.bat_soc < 55`. | **S** | Low | **High for instructors** — writing a scenario currently requires looking up hex ids |
| **L-5** | Generate the parameter-naming contract | DI-21 | `get_state_summary` hard-codes ~50 param ids and unit-suffixed field names (its docstring admits the MCS UI hard-codes them too), and `_UI_KEY_MAP` bridges a third scheme. Drift here caused defect #36. Generate the summary grouping and UI keys from `telemetry/parameters.yaml`. | **M** | Med | Moderate |
| **L-6** | Planner hygiene bundle | DI-25, DI-26, I-6 | Make the planner's MCS URL configurable (the MCS already honours `SMO_PLANNER_URL`; the reverse hard-codes `localhost:9090`); make `_get_now()` read only the cached anchor so a request handler cannot block on a 1.5 s synchronous `urlopen`; move the infinite `while True: sleep(600)` contact-recompute loop out of `start()` and make its interval sim-speed-aware (at 10× it is currently ~1.7 sim-hours stale). | **S** | Low | Moderate |
| **L-7** | OBCP conditional steps and step parameters | `docs/OBCP_GUIDE.md` §6 | Add `wait_until <param op value>` steps with a timeout, and consume the activation `param` byte for step-level substitution. Keep it deliberately bounded — full PUS-C ST[18] with suspend/resume and step reporting is only worth it if the syllabus grows an autonomous-ops module. | **M** | Med | High **if** an autonomy module is wanted; otherwise premature |
| **L-8** | Repo and packaging hygiene | DI-28, DI-29, DI-27 | Editable installs plus console-script entry points instead of hard-coded relative `sys.path.insert` in tools and tests (which currently lets tests pass against different code than production imports); relocate the runtime data directory out of `configs/` (a live SQLite archive with a WAL file currently lives in the config tree); package the vendored JS as package data instead of a 10-level parent walk. | **S** | Low | Moderate; removes a class of "works on my machine" |
| **L-9** | Payload data-volume and radiometric depth | `textbook_vs_sim.md` §3 row 8; §2.2 payload row | Sim: **800 MB/image**, 20 GB store, 80 Mbps internal. Book: **~4 MB/session** (pp. 360, 404). At the sim's 64 kbps maximum, one sim image is ~28 h of downlink — every data-budget exercise breaks. Band configuration (443/560/665/865 nm) is bookkeeping with no spectral or radiometric model behind it. Decide the image size (see C-6) and, optionally, add a minimal radiometric model so `snr` and `last_image_quality` mean something. | **M** | Low | High for the payload and data-budget lessons (Ch 3.3 §9, Ch 7.20 §2, App C.15) |
| **L-10** | SpaceCDF authz and docx convergence | DI-31, DI-32 | `session_manager.can_edit` returns `False` for every position except a hard-coded `"systems_engineer"` while its own log says "edit scoping disabled" — it fails **closed** while claiming to be off. Package the positions YAML as package data and make the miss behaviour match the message. Separately, converge the three overlapping Word-document stacks (1414-line `services/docx_generator.py`, 910-line agents-side generator, and inline assembly in `exports.py`) on the DID-driven renderer. | **M** | Low-Med | Moderate; the authz item directly affects the 15-position concurrent-design exercise |
| **L-11** | Small-fry bundle | DI-23, DI-24, DI-30, DI-22 remainder | `/api/catalog` returns a hard-coded empty catalogue while a real 183-command one exists — wire or delete it. The TM broadcast loop busy-polls at 20 Hz and serialises slow clients (one slow client adds up to 2 s per packet for everyone) — use per-client send queues. `orbit.yaml` describes the orbit three ways (TLE **plus** scalar `altitude_km`/`inclination_deg`, repeated in `mission.yaml`) and the scalars already disagree with the TLE's mean motion — derive them at load or mark them informational. Move the remaining engine literals (queue sizes, `SC_PHASE_NOMINAL`) into `simulation.yaml`. | **S** each | Low | Low-moderate |

---

## §C · Courseware alignment

This is a parallel, continuous workstream and the **cheapest high-value work available**. Source: `textbook_vs_sim.md`, which assessed 47 capability rows (18 Implemented, 12 Partial, 9 Absent, 8 Different-numbers) and checked all 28 explicit "the simulator does X" claims in the book (**13 TRUE, 10 PARTIAL, 3 FALSE, 2 not assessable**).

### C-1 · Correct the three FALSE claims — **do first, do now**

| Claim | Page | Reality | Fix |
|---|---|---|---|
| "In the simulator, the platform idles at **70 W**" | 309, restated 371, 413 | `platform_idle_power_w: 70.0` is **loaded and never used** (`eps_basic.py:188` versus the consumption model at `:346-419`). Actual idle is **20 W** (OBC 15 + TTC RX 5); full nominal platform is **36 W**. | Either re-word to 20 W idle / 36 W nominal, **or** wire the 70 W key so the book becomes true. **Position: re-word.** 70 W was already flagged in the book as an unrealistic "simulator artefact" (a real 6U idles at 8–14 W); 36 W is both closer to reality and true of the code. |
| The p. 371 eclipse-exit SoC worked example (~51 %) | 371 | Built entirely on the false 70 W premise. | Recompute at 36 W → roughly an **18 %** eclipse SoC drop, not ~34 %. Also revisit against the new 90 % start SoC (defect #50). |
| "The simulator's FPA Peltier cooler takes **60–90 minutes** to reach setpoint" | 402, restated 439 and on card COM-010 | `payload.yaml` sets `fpa_tau_cooling_s: 100.0` → setpoint in roughly **5–8 minutes**. Wrong by ~10×. | Either correct the text to ~5–10 min, **or** raise τ to ~1200 s so the card's timing is real. **Position: raise τ.** A cooler that reaches setpoint in five minutes removes the pass-planning constraint the exercise exists to teach; 60–90 min is also the physically honest figure for a Peltier-cooled FPA. **Effort S**, in `payload.yaml`, no code change. |

**Effort:** S. **Risk:** none (C-1a, C-1b) / low (C-1c — check COM-010 and the payload tests).

### C-2 · Propulsion caveat, or its removal

Coupled to **X-3**. If propulsion is built, this reduces to updating §5.7's spacecraft description (which currently omits propulsion entirely) and re-validating NOM-004/005/CTG-006 against the model. If it is not, one caveat box must cover NOM-004, NOM-005, CTG-006, the Propulsion Engineer position (p. 518), FDS manoeuvre planning (p. 350) **and** conjunction assessment (p. 350, also absent — zero "conjunction" hits repo-wide). **Effort:** S either way. **Position:** build (X-3) and write the short version.

### C-3 · Number corrections

All verified against code and config; several changed *this session*, so the errata must be written against the current tree.

| Quantity | Book | Simulator (now) | Note |
|---|---|---|---|
| Ground-system URLs | MCS at `localhost:8080`; MPS at `:8080/planning/` (pp. 332, 357) | Simulator API `:8080`, **MCS `:9090`**, **planner `:9091`** | Every "open the console" instruction currently points at the wrong service — highest-nuisance error in the book |
| S12 monitor count | 25 (p. 338) vs "38 active" (Ch 15.2, p. 497) | **38** | p. 338 wrong; `outputs/sot_index.md` does not exist in the repo |
| Procedure split | 7/13/**12**/27/**5** = 64 (p. 416) | 7/13/**11**/27/**6** = 64 | Total right, two categories off by one |
| S8 function range | 0–82 + 100–107 (p. 341) | 0–**84** + 100–107 (84 = `AOCS_GPS_SET_START_MODE`); **183** commands; **plus the new S18 block** | |
| ~~Beacon interval~~ | SID 11 @ 30 s | **30 s** — ✅ resolved 2026-07-29 | `hk_structures.yaml` SID 11 was the sole outlier at 10 s and is now 30 s, matching the book, `OPERATIONS_GUIDE.md`, `PUS_SERVICE_REFERENCE.md`, `architecture.md`, the `tm_storage.py` store-sizing budget and `test_commissioning_rf.py`. Param count is **6**, not 7 (`architecture.md` corrected); 6 was the deliberate choice in `SPACECDF_RECONCILE.md` item 8 — the beacon reports what the bootloader actually transmits |
| Passes per day | 4 Iqaluit + 3 Troll (p. 309) vs ~14/24 h (p. 358) | **19.5/day** measured (Iqaluit 8.5, Troll 11.0) at the configured 5° masks | Neither book figure matched even before defect #41; now both are wrong in the other direction. Either raise the masks to 10° (which would also make pass durations match the book's 8–12 min — the measured means are 6.8 and 8.1 min) **or** update both passages. **Position: raise the masks to 10°** — it reconciles pass count, duration and elevation in one config edit, and 10° is the more defensible operational mask |
| Solar array power | 28 W BOL (p. 309) | Sunlit average ≈ 82 W with wings deployed (now 0.300 m²); body-only ≈ 22 W; AOCS-off fallback ≈ 245 W (see N-9) | |
| Wheel limits | 5000 rpm = 8 mN·m·s; S12 yellow 6000 / red 8000; `aocs_wheel_saturation` > 8000 → desat (pp. 313, 414–415) | Max **5500 rpm**; S12 single band ±5000; S19 overspeed **disables the wheel** (func 2), desat is triggered separately by the momentum monitor (func 1) | Different limits **and** a different autonomous response — the Ch 13.7 exercise needs both corrected |
| Load-shed trigger | `battery_soc_pct` red **< 40 %** → shed; stages 0–3 (pp. 324, 342) | Bus-voltage shed now **25.0 V** (≈ 45.5 % SoC); SoC stage ladder 30/20/10 % now **live** (defect #49); S12 warn 20 / alarm 10 %; FDIR 20/15 % | Four shed truths before; three now, and the ladder finally works. Rewrite once against the current config |
| Star trackers | §5.7 says "single-head" (p. 310) | `ST1_POWER` / `ST2_POWER` / `ST_SELECT` all present — **redundant pair** | The book's own Ch 6 assumes redundancy; §5.7 is the outlier |
| S19 action expressiveness | Not stated | An action is exactly **one parameterless S8 function call** | Worth stating explicitly — it is the reason S18 was needed |
| PUS services | "12 declared in `pus_services.yaml`, plus S2 and S13 in `tc_catalog`" (p. 340) | All 14 declared in `pus_services.yaml`; **now 15 with S18** | One-word provenance fix plus the S18 addition |
| Manual chapters | `manual/00..11_*.md` (p. 310) | 00–**12**, with duplicate-numbered 09 and 10 pairs | Cite by name, not number |
| Image size | ~4 MB/session (pp. 360, 404) | **800 MB/image**, 20 GB store | See C-6 |

**Effort:** S for the errata sheet; the mask change in C-3 is a one-line config edit plus re-running `tools/pass_gap_analysis.py`.

### C-4 · Rewrite the nine un-runnable workbook exercises

| Exercise | Why it was broken | Status now |
|---|---|---|
| Ch 8 MPS exercise (plan → upload → pass execution) | Wrong URL; the imaging activity performed zero payload actions; the scheduled time was discarded; S11 was unloadable; the MCS showed no schedule | **Runnable** after defects #44–#47, except the URL (C-3) and the schedule display (X-1) |
| Any Troll-pass activity, including `data_dump` at Troll | Iqaluit-only contact gating | **Runnable** after defect #41; `data_dump` also needs N-8 to have an effect |
| Ch 12/15 staged load-shed drills (SoC 30/20/10) | Stage path was dead code | **Runnable** after defect #49 |
| Ch 13.3 / p. 371 eclipse-exit SoC | 70 W premise false | Needs C-1 |
| COM-010 "wait 60–90 min" cooler step | τ = 100 s | Needs C-1c |
| COM-006 link-margin baseline capture (14.5 / 6.8 dB) | Sim computes a UHF budget with one global G/T | Needs **N-4** |
| Ch 13.7 wheel-desat scheduling | 5000 rpm / 8 mN·m·s premise; sim is 5500 rpm and disables on overspeed | Needs C-3, or align the sim's limits |
| "4 MB image → 32 s at 1 Mbps" rule of thumb (p. 360) | No 1 Mbps rate exists; images are 800 MB | Needs C-6 (+ N-8) |
| NOM-004 / NOM-005 / CTG-006 orbit maintenance | No propulsion; `PROP_ARM` does not exist | Needs **X-3** |

**Effort:** M across the set. **Position:** rewrite them as each enabling item lands, rather than in one pass — five of the nine are already unblocked.

### C-5 · Introduce S18 OBCP as an extension module

The textbook explicitly and correctly states that EOSAT-1's autonomy is S11 time-tagged schedules plus S19 event-actions, with **no OBCP anywhere in the book**. S18 is therefore *sim-exceeds-book*, and should be introduced as an **extension exercise** rather than retrofitted into Part 2 — otherwise the book's carefully consistent autonomy story becomes wrong. Natural exercises: autonomous pass preparation executing across LOS (`01_pass_preparation.yaml`); autonomous safe-ing without ground contact (`02_safe_mode_entry.yaml`); and a comparison exercise contrasting ground procedures (die at LOS), S11 (one-shot), S19 (one function, no arguments) and S18 (multi-step, LOS-surviving) — which is a genuinely good autonomy-taxonomy lesson. Material already exists in `docs/OBCP_GUIDE.md`. **Effort:** S (one appendix section plus two exercise cards). **Pedagogical value:** high, and it is new differentiated content.

### C-6 · Decide the image size — **position: shrink the simulator**

| | |
|---|---|
| **Source** | `textbook_vs_sim.md` §3 row 8 |
| **The problem** | The sim models **800 MB/image** with a 20 GB store; the book uses **~4 MB/session** and builds the "4 MB → 32 s at 1 Mbps" rule of thumb on it. That is 200×. Combined with the sim's 64 kbps maximum downlink, **one simulated image is ~28 h of contact time** — so the S13 image-downlink workflow, the data-budget exercises (Ch 3.3 §9, Ch 7.20 §2, App C.15) and the store-management lessons are all unworkable. |
| **Position** | **Change the simulator, not the book.** Set the image size to a few MB in `payload.yaml`. The book's figure is the one that is arithmetically consistent with the rest of the mission (a 6U CubeSat on a 64 kbps link cannot move 800 MB in a 7-minute pass, and no amount of caveating fixes that), and 4 MB makes a full image downlink fit comfortably inside a single pass — which is what makes the exercise teachable. Scale the store proportionally so store-fill percentages and the `store_used > 95 %` red limit stay meaningful. Note the book's "at 1 Mbps" rule of thumb still needs correcting to 64 kbps regardless (C-3), unless N-8's rate work adds a higher rate. |
| **Effort** | **S** (config), plus re-validating the S13 and store-management tests |
| **Risk** | Low-medium — check the S15/S13 dump-pacing tests and the payload store telemetry limits |

---

## Sequencing summary

Original plan (2026-07-28), retained for the record:

```
NOW      N-3 commit  →  N-1 tick guard  →  N-2 threading  →  N-5 t0_epoch  →  N-4 RF/S-band
         (parallel, independent: N-6 flake, N-7 doc, N-8 TTC rate, N-9 fallback array)
         (parallel: C-1 the three FALSE claims, C-3 errata sheet)

NEXT     X-3 propulsion  (after N-5 — needs deterministic epoch handling)
         X-2 PUS table   (before any further service work)
         X-4 delete files/  ·  X-5 content tests + CI  ·  X-1 mission plan panel
         X-6..X-11 small structural items, any order
         S-1..S-3 SpaceCDF, independent of the SMO track
         C-4 rewrite exercises as each enabler lands  ·  C-5 OBCP module  ·  C-6 image size

LATER    L-1/L-2 decomposition — good student projects
         L-3..L-11 as opportunity allows
```

Revised plan (2026-07-29), after N-1/N-2/N-4/N-5/N-6/N-7/N-8/N-9 landed:

```
NOW      N-3 commit and branch   ← do this FIRST; ~100 more changed files landed
                                   this week and none of it is bisectable
         N-11 data-rate ↔ image-size arithmetic  (unblocked by N-8; small)
         N-10 S19 action-func review             (BLOCKED — awaiting Franz)
         (parallel: C-1 the three FALSE claims, C-3 errata sheet — now shorter,
          the beacon-interval and S8-range rows are resolved)

NEXT     X-12 procedure convergence   ← highest training value of the new items
         X-15 impairment porting      (unblocked by #53/#56 — the displays are live)
         X-13 imaging map overlay  ·  X-14 S12 delta monitors   (both S, both quick)
         X-3 propulsion  (after N-5, which is now done — no longer blocked)
         X-2 PUS table   (before any further service work)
         X-1 mission plan panel  ·  X-4 delete files/  ·  X-5 content tests + CI
         X-6..X-11 small structural items, any order
         S-1..S-3 SpaceCDF, independent of the SMO track
         C-4 rewrite exercises as each enabler lands  ·  C-5 OBCP module

LATER    L-1/L-2 decomposition — good student projects
         L-3..L-11 as opportunity allows  (L-9 payload volume folds into N-11)
```

**If only three things are done this term** (revised): **N-3** (commit — without it none of this week's work is reviewable or recoverable), **N-11** (make the data budget close, now that the rate half is real), and **N-10** (get Franz's S19 review, because 13 of 26 autonomous responses currently teach the wrong lesson). The original "three things" advice — N-1, N-4, C-1 — is superseded: N-1 and N-4 are done, and C-1 is now the only remaining part of it.

---

*Prepared 2026-07-28. Revised 2026-07-29 after defects #43 and #53–#80 landed: Now-tier completion marked, N-10/N-11 and X-12…X-15 added from the RF, planner, PUS-service and MCS-display audits. Effort estimates assume familiarity with the tree; every cited finding is reproducible from the analyses listed in `REVIEW_2026-07.md`.*

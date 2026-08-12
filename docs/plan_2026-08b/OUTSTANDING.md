# Outstanding work after the 2026-08-12 batch

Everything below is what did **not** make it into commit `a4b0f0a`
(`evolution/2026-08`, 179 files). That commit is intact on the Mac and contains
simulator phases 0-6. The container was rolled back afterwards, so the two items
below were completed in-session but never delivered, and must be redone.

Start a fresh task **on your computer** (desktop app → "Run this task" picker,
top right) rather than in the cloud — the whole batch depends on reading and
writing the repo directly, and this session lost that ability mid-way.

---

## 1. Textbook pass (Chapter 14 + Part 2) — REDO

This was completed once and lost. It is a large pass; budget for it.

Sources: `/Users/FNewland/SpaceCDF/docs/course/orbit_north_week3.md` (Part 2,
ch 5-16, Chapter 14 the procedure catalogue at ~line 4965),
`orbit_north_week3_workbook.md`, `learner_workbook_expanded.md`,
`instructor_book.md`, `facilitator_book_expanded.md`.

Ground truth is the POST-FIX simulator — verify every claim against the current
`configs/eosat1/` and models, not against any prior brief. Read first:
`docs/plan_2026-08b/COHERENCE_REVIEW.md`, `POWER_SIZING.md`, the four
`DISPLAY_REQUESTS_*.md` and `MCS_REQUESTS.md`; several contain explicit
"notes for the textbook owner" sections.

What changed and must be reflected:
- **Power**: the entire hardware sheet was re-derived from real 6U flight parts.
  Every wattage, array area, battery figure, generation number and margin in the
  book needs updating from POWER_SIZING.md. Biggest single numerical sweep.
- **AOCS**: FINE now works on 3 wheels (bitmask 0x02C5 bit 1 now means "fewer
  than THREE"); `fine_degraded`; desat now completes, honest dump time 20-50 min
  (not the 5-15 min previously documented), with a timeout, an imaging inhibit
  and return-to-pre-desat-mode; B-dot is genuinely magnetometer-driven;
  new gyro telemetry 0x0294-0x0296.
- **IMU**: STAYS in the book. ~12 fabricated citations must go — see the list in
  §3 below.
- **TT&C**: EO-Sat-1 now genuinely has two transmitters, S-band prime plus a
  separate UHF whip beacon. PA-off stops S-band TM while the beacon survives —
  that contrast is a teaching point worth making explicitly.
- **Payload**: CALIBRATION mode (3); dark cal no longer needs IMAGING; absolute
  shutter command; cooler reclassified Peltier → miniature-Stirling-class with
  authority that degrades with dT; the image path is real, so the book's
  "images do not appear on their own" note is now actually true.
- **Procedures**: eight cards are machine-runnable; the executor no longer
  reports success for a procedure that did nothing.
- **MCS**: staleness is cadence-derived; PROP tab; S12 visible in dumps;
  commanded-vs-actual mode. Do NOT describe the design study's proposed
  interface — it is not implemented.

Franz's named items: **LEOP-006 step 2** and **COM-006** (antenna deploy,
downlink-mode off, beacon off) — both shipped cards were rewritten in Phase 5;
mirror them into the book.

Tree-wide error families found in the shipped cards, very likely in the book too:
`link_status = 1` claimed as LOCKED (LOCKED is 2); HK SID off-by-one (real map:
1 EPS, 2 AOCS, 3 TCS, 4 OBDH, 5 Payload, 6 TT&C, 8 EPS-extended, 9 PROP,
11 Beacon); HK request quoted as S3 subtype 1 or 25 (it is S3.27); an invented
"S8 subtype names the subsystem" scheme; command arguments written outside the
backticks; parameter name/hex disagreements; AOCS mode names not matching
`aocs_mode_codes.yaml`.

---

## 2. Cleanup pass — 4 failing tests + 10 defects

### 2a. The 4 test failures in `a4b0f0a`
(`pytest tests/ -q --ignore=tests/test_e2e_browser --ignore=tests/test_adcs_sim_viewer.py`
→ 4 failed, 5796 passed)

1. `test_failure_clear_each.py::…[aocs.gyro_bias]` — gyro_bias injection was
   changed to a PERSISTENT offset (the old one healed itself in ~30 min, making
   the failure useless). `clear_failure` no longer removes it. Fix the clear,
   keep the persistence.
2. `test_leop_end_to_end.py::test_aocs_detumble_reduces_rates` — detumble was
   replaced with a genuine magnetometer B-dot law; real B-dot converges much more
   slowly (~4620 s from a 14 deg/s tumble). Verify the law is right, THEN rewrite
   the expectation with a comment explaining the number. Do not weaken it to hide
   a defect.
3. `test_position_workflows.py::test_procedure_command_services_are_not_empty…`
   — predates the `runnable: {kind: advisory}` concept introduced for CTG-020.
   Decide: add proper `advisory` fields to `ProcedureIndexEntry` in
   `smo_common/config/schemas.py` (a StrictModel), or keep the current encoding —
   then make schema, gates and test agree.
4. `test_scenario_generator_sync.py::…[eps-eps_basic.py]` — `tools/scenario_generator.py`
   is out of sync with the new `sada_stuck` failure.

### 2b. Ten defects found while auditing the shipped cards
1. `procedures/leop/sun_acquisition.md` — AOCS mode codes off by one throughout.
   Step 1 verifies mode = 1 as "DETUMBLE" (2 is DETUMBLE); Step 6 commands mode 2
   verifying "= 2 (COARSE_SUN)" (3 is COARSE_SUN). As written the card leaves the
   spacecraft detumbling when it should be sun-pointing. Phase 5 missed this file.
2. `procedures/leop/solar_array_verification.md` steps 2, 3, 6 — reads
   0x0110-0x0113 as "SA-A/B deploy switch status" / "string current". Those are
   power-LINE flags. Wing status is 0x0144; per-wing currents 0x0146/0x0147.
3. `procedures/commissioning/aocs_actuator_checkout.md` — commands wheel speeds
   via SET_PARAM on 0x0280-0x0283 (slew_time_remaining, slew_progress,
   momentum_saturation_pct, attitude_source) and reads wheel temps at
   0x028A-0x028D (CSS head health). Speeds are read-only at 0x0207-0x020A,
   temps at 0x0218-0x021B, commanding path is `RW_SET_SPEED_BIAS` (func 8).
4. `procedures/commissioning/ttc_link_verification.md` prerequisites — a mangled
   edit left "…(aocs.mode 0x020F = 3) or NOMINAL (4). There is no COARSE_SUN or
   NOMINAL mode".
5. `first_acquisition.md` Step 5 and `sequential_power_on.md` Step 6 still claim
   HK SIDs stream without HK_ENABLE. `default_enabled_sids` is `[1, 11]` and
   `engine.py:691-696` re-applies exactly that on application boot.
6. `manual/01_eps.md` §3.1 still calls the FPA cooler a "Thermo-electric
   (Peltier) stack" and `manual/09_command_reference.md:989` repeats
   "Peltier-class cooler envelope" — both contradict the Phase 4 reclassification.
   §2.3's PDM table also describes a seven-line architecture; the model has nine
   (obc, ttc_rx, ttc_tx, payload, fpa_cooler, htr_bat, htr_obc, aocs_wheels, prop).
7. `procedures/leop/time_sync.md:110` points at "NOM-006 (clock_sync.md)";
   the index has clock_sync as NOM-008 and NOM-006 as Software Upload.
8. **`smo-mcs/procedure_runner.py` `_execute_step` counts unknown step types as
   PASSED.** Cards now emit `label` and `branch` steps. A skipped branch is a
   procedure running straight past its own decision — this is the same defect
   class the batch closed on the simulator side, still open on the MCS side, and
   it makes runnable contingency cards unsafe. Implement label/branch AND fail
   closed on anything unknown.
9. `tcs.temp_fpa_cooler_floor_c` (0x0424) and `tcs.fpa_cooler_margin_k` (0x0425)
   are written by `tcs_basic` but not declared in `parameters.yaml` — undownlinkable.
10. `subsystems/tcs.yaml` documents `fpa_cooler_carnot_fraction`,
    `fpa_cooler_power_w`, `fpa_imaging_load_w` as config-read, but `TCSConfig` is
    a StrictModel that does not declare them — putting them in YAML would be
    REJECTED and the model defaults are live. Three parameters that look
    configurable and are not.

Plus: `engine.py:2874` emits a DUPLICATE 0x0200 AOCS-mode event using a 5-entry
mode table from before the 9-mode machine, so NOMINAL→ECLIPSE downlinks
"AOCS mode: TARGET -> ?" — every label wrong, modes 5-9 render as "?". The model
already emits a correct one; delete the duplicate. And `tc_catalog.yaml` lists
`SET_PHASE` as an alias of `OBC_BOOT_APP` (booting the app has nothing to do with
spacecraft phase) — remove the misleading alias. And the payload owner asked for
5 dedicated failure codes for calibration refusals (see
`DISPLAY_REQUESTS_PAYLOAD.md` §8.1).

### 2c. Regenerate + two long-standing items
- Regenerate the stale artifacts: `tools/gen_command_reference.py`,
  `generate_xtce.py`, `generate_smdl.py`, `gen_parameter_map.py`.
- **`test_regeneration_is_a_no_op` has failed on every day but the day the
  reference was generated since 2026-08-02** — `gen_command_reference.py` stamps
  `**Date:** <today>` into `manual/09_command_reference.md` and the `--check`
  gate diffs the whole file. Drop the date or exempt that line.
- **`smo-simulator` is the only service with no `--port` flag** (8080 hardcoded
  at `server.py:157` and ~8 places in `start.sh`) while smo-mcs and smo-planner
  both take one. On a machine where 8080 is unavailable the suite cannot start.

---

## 3. The IMU corrections (part of item 1, listed separately because they are exact)

The IMU stays. These citations are fabricated and must go — verify each against
the current tree, since Phase 1 changed the gyro wiring:

| location | wrong | right |
|---|---|---|
| `orbit_north_week3.md:1930` | "no discrete IMU enable" | `S2_AOCS_GYROSCOPE_0/1/2`, devices 0x0206-0x0208 |
| `:3633-3635` | "Enable IMU power"; "Enable IMU (S8 func_id 38)"; MTQ enable func 40 | no IMU power line exists; func 38 is `PAYLOAD_STOP_CALIBRATION`; func 40 is `HEATER_BATTERY` — MTQ enable is func 9 |
| `:3684` | "IMU cold-boot delay ~10 s" | not modelled — delete |
| `:5211` | `aocs_imu` / `aocs_sensors` ON | neither line exists |
| `:5216` | `aocs.body_rates` | `rate_roll/pitch/yaw`, 0x0204-0x0206 |
| `:5299-5301` | ground-computed bias upload | `AOCS_GYRO_CALIBRATION`, func 13, on-board |
| `:6149`, workbook `:1314` | `AOCS_EST_SET ss_imu` | does not exist; `AOCS_EST_RESET` (91) + `est_source` 0x02C1 |
| `:6170` | "switching to redundant IMU" | no redundant unit — three axes, no spare |
| `:6176` | `S2_TURN_OFF(line=imu)`; 0.02 deg/s threshold | device commands; reconcile against 2.8e-5 spec / 0.05 event / 0.5 S12 limit |
| `:299` | "single-head star tracker" | EO-Sat-1 flies two (ST1/ST2, `ST_SELECT` func 6) |

`instructor_book.md:100` advertises an "IMU drifts" scenario — Phase 1 added the
`gyro_bias` injection to `aocs_sensor_cascade.yaml`, so verify and keep it.

---

## 4. Decisions still needing Franz

D-1 instrument swath 45 km (model) vs 240 km (book) — recommend 45.
D-2 whether Part 1 §3.4 keeps its no-propulsion argument now that prop is confirmed.
D-3 the two orphan nominal cards (Pass Plan Upload, Long-Term Trend Review).
D-4 FDIR severity 0-4 vs autonomy level 0-3 — recommend keeping both, named distinctly.
D-5 phase-transition telecommand — recommend leaving it console-only and saying so.
D-6 the 13 misrouted S19 rules — still needs a per-rule ruling; strict-xfails
    carry a 2026-10 deadline. Do NOT blanket-remap.
D-7 two-station vs five-station teaching baseline.

---

## 5. Also carried forward
- The interface redesign is researched and mocked up in `docs/design_2026-08/`
  (three directions + audit, research, principles, recommendation). Franz asked
  for this version to be stable before any of it is implemented.
- `docs/STATUS.md` carries everything else still open.

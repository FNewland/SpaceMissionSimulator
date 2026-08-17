# Outstanding work after the 2026-08-13 batch

The previous `OUTSTANDING.md` is now closed except where noted below. This
session ran in the cloud with the device bridge, staged the repo into a
container, and delivered two tarballs:

- `_xfer/smo_batch2.tar.gz` — the simulator tree (overlay onto the repo with
  `cp -R staged/./ <repo>/`, NOT `tar x` over the mount)
- the five course files, written straight back into `SpaceCDF/docs/course/`

---

## Closed this session

### §2a — the four failing tests
All four fixed, root causes below.
1. `aocs.gyro_bias` — the *test* was probing the Gauss-Markov state
   `_gyro_bias[0]` while injection now writes the persistent offset
   `_gyro_bias_inject`. Predicates strengthened. **A real defect fell out of
   this**: `_gyro_bias_inject` was not in `_EXTRA_STATE_KEYS`/`get_state`/
   `set_state`, so a breakpoint or snapshot restore silently healed an active
   gyro_bias failure. Now saved and restored.
2. `test_aocs_detumble_reduces_rates` — the B-dot law was **verified correct
   before the test was touched**: gain sign confirmed by inversion (flipping it
   grows a 14 deg/s tumble to 50 deg/s), control power `w.tau` negative on 100 %
   of sub-steps, filtered estimate tracks `-w x B` at cos 0.95, dipole saturates
   at 1.6 A m^2 with direction preserved. The old test asserted a per-axis rate
   at 50 s, which is not a statement about B-dot at all (only the component
   perpendicular to B is damped, so energy moves between axes). Rewritten to
   assert rate *magnitude* over 900 s with the derivation in the docstring, plus
   a control case that must NOT decay with the magnetometer failed.
3. Advisory procedures — took option (a): `advisory: bool` and
   `advisory_reason: str` are now real fields on `ProcedureIndexEntry`. The old
   `runnable: {kind: advisory}` encoding claimed a card was linked to an
   executable definition in order to say the opposite. Schema, gates, audit tool
   and tests all agree now.
4. `scenario_generator.py` — `sada_stuck` added to the catalog.

### §2b — the ten card defects
All ten fixed, plus a much wider sweep than the ten. The AOCS mode off-by-one
turned out to be in **ten more cards** beyond `sun_acquisition.md`, and a
scripted parameter-name/hex scan across all 67 cards found and fixed a further
dozen mismatches (an entire invented `gps.*` namespace pointing at payload
parameters, `aocs.mag_valid` pointing at wheel torque, MTQ duties off by one
register block, and more). `hk_configuration.md` had the SID 5/6 swap plus six
fabricated interval values.

Two corrections to the brief itself, found in the tree: `HK_ENABLE` is **S3.5**
(the brief said S3.27 — that is `HK_REQUEST`), and `EPS_POWER_ON.line_index` was
documented as 0-7 in **three** places, not one.

Item 8 (the MCS `procedure_runner`) was the important one. `label` and `branch`
are now really executed — program-counter driven, branch predicates polled
against telemetry for `timeout_s`, GO/NO-GO arms as jumps, loop guard requiring
`max_taken` on any backward jump, and a whole-procedure step budget. Unknown
step types **fail closed**. A subtlety that would have defeated the whole fix:
`canonical_step()` launders anything unrecognised into a `note`, and notes are
inert — so unknown steps would have gone on passing. `_prepare_steps()` now
re-tags them with their original type first.

### The three extras
- The duplicate 0x0200 event in `engine.py` is deleted. **Deleting it alone
  would have made NOMINAL->ECLIPSE silent** — the AOCS model's own event
  edge-detects against a `prev_mode` captured *after* `_check_auto_transitions`,
  so autonomous and between-tick commanded transitions produced no event at all.
  Fixed with persistent edge state on `AOCSState`.
- `SET_PHASE` alias removed from `tc_catalog.yaml`.
- Five calibration-refusal codes 0x0024-0x0028 implemented per
  `DISPLAY_REQUESTS_PAYLOAD.md` §8.1, each refusal path returning its own code.

Also: `tcs.fpa_cooler_floor_c` / `fpa_cooler_margin_k` declared; the three
phantom `TCSConfig` knobs made genuinely configurable; and a **new gate**
(`test_written_params_declared.py`) that boots an engine and asserts every
written parameter id is declared. It found **11 more undeclared parameters**,
now in a ratchet list with owners.

### §2c
- The date-stamp flake is gone at the root: generated artefacts now contain no
  wall-clock value at all. `tools/source_revision.py` stamps a sha256 over the
  actual input files instead. Two regression tests forbid `date.today(` /
  `datetime.now(` in the generators and any ISO date in the artefacts.
- All four artefacts regenerated, idempotent on a second run.
- `smo-simulator` takes `--port` (precedence: flag > `$SMO_SIM_HTTP_PORT` >
  8080), and `start.sh` / `deploy/` are parameterised. **A latent bug fell out**:
  `deploy/` already advertised `SIMULATOR_HTTP_PORT`, probed it, and printed it
  in the URLs — but never passed it to the simulator, so setting it to anything
  but 8080 aimed the readiness probe at a dead port and aborted start-up.

### Suite
**5868 passed, 3 failed** before a final tidy; all three failures were missing
optional dependencies in the container (matplotlib for the two reporting tests,
scipy for one rfsim test), not defects. The rfsim one now `importorskip`s, since
scipy is not a hard dependency of `smo-rfsim`. The two reporting tests pass with
matplotlib installed.

### §1 and §3 — the textbook pass
Done across all five course documents, by chapter-owning agents working from a
shared `GROUND_TRUTH.md` and verifying every claim against the tree rather than
against any brief. Headline items:
- Every IMU citation in the §3 table corrected or deleted.
- Power figures re-derived (not nudged) from `POWER_SIZING.md` and the configs
  throughout Ch 13, Ch 3.1, Ch 4.1 and both workbooks.
- AOCS mode numbers were off by one essentially everywhere.
- LEOP-006 step 2 and COM-006 mirrored from the shipped cards.
- Owner rulings D-1 to D-7 applied.

**The workbook sweep found sixteen wrong-command errors** — pages that would
have made a student send the wrong telecommand. The worst: `STAR_TRACKER_ENABLE`
at "func 36", which is `PAYLOAD_COOLER_SETPOINT` (a student following that page
commands a 0.01 K setpoint); `htr_bat` at "func 25", which is
`EPS_EMERGENCY_LOAD_SHED`; `OBC_SWITCH_UNIT` for a *bus* switchover, which fails
over the OBC instead.

---

## Still open

### Decisions and rulings
- **D-6, the 13 misrouted S19 rules, still needs a per-rule ruling.** Nothing was
  remapped and the books say nothing about them either way. The strict-xfails
  still carry the 2026-10 deadline.
- **D-1 was implemented as ruled** (240 km swath; the model changed, not the
  book), but it exposed a real consequence: the geometry implies a 100.7 MB raw
  frame while `image_size_mb` stays at the courseware's 4 MB. That gap is now
  documented and pinned by a test rather than papered over — but it is a
  decision you have not actually made yet. At 100.7 MB no pass plan can close.
- **D-3**: the two orphan cards are wired into the nominal flow in the book, but
  they have **no NOM number** — NOM-001..013 are all occupied. If you want them
  numbered, that is a Flight-Ops call.

### Tree-side defects the books are now ahead of
The books were corrected against the canonical configs; several *shipped cards*
still disagree with those configs and should be fixed on the simulator side:
- `initial_health_check.md`, `aocs_mode_transitions.md`,
  `solar_array_degradation.md`, `solar_panel_loss_response.md`,
  `routine_health_check.md` — AOCS mode names still off by one.
- `aocs_actuator_stuck_recovery.md` step 3 invokes `AOCS_3W_MODE ... func_id 5`;
  func 5 is `ST2_POWER` and no such command exists.
- `battery_cell_failure.md` and `total_power_failure.md` quote a 120 Wh battery;
  `eps.yaml` says 100 Wh.
- `thermal_exceedance.md` requests TCS on SID 4; `payload_anomaly.md` requests
  payload on SID 6. Both are off by one against the SID map.
- `startup.md` step 5 labels SID 5 as TT&C and SID 6 as Payload.
- `first_light.md` carries an FPA band of [-32, -28] C against the -15 C setpoint.
- `eps_checkout.md` still quotes 0.78 m^2 / 40 Ah / 14.4-16.8 V.
- `ttc_link_loss.md` escalates to EMG-001; the index says EMG-004.
- **Auto-TX hold-down disagrees with itself**: `simulation.yaml` says 300 s (with
  a comment explaining the 900 -> 300 change), while `first_acquisition.md` and
  the `ttc.cmd_decode_timer` description both still say 15 minutes.

### Documentation inconsistencies found
- `POWER_SIZING.md` §2.1 says K_EOL = 267.2 W/m^2; `eps.yaml` says 267.9 and the
  derivation gives 267.83. §3 of the same document still calls the FPA cooler a
  "Peltier stack". The document needs a small correction pass so it stops
  contradicting the configs it is supposed to justify.
- **The 24.5 V FDIR undervoltage ladder could not be found in the tree.**
  `simulation.yaml` has `uv_threshold_v: 26.5`, in-model shed at 25.0 V,
  `bus_critical_v: 25.0`, and an FDIR SoC ladder at 30/20/10 %. The only 24.5 in
  the configs is a heater-power S12 limit in watts. Either the ladder was never
  implemented or it lives somewhere the sweep did not look — worth resolving,
  because the number is now quoted in several places in the books.
- `manual/06_payload.md` still carries the pre-D-1 geometry (54 km swath, 9 m
  GSD, 40 Mbps). It was outside every agent's scope. Needs a rewrite from the
  new optics block in `payload.yaml`.

### Book-level items that need a human decision
- **`facilitator_book_expanded.md` Part 1 flies a different spacecraft from Part
  2.** Part 1's running examples are a generic 3U SuperDove-class EO CubeSat at
  500 km and a 1U "UniSat-1" at 400 km; EO-Sat-1 (6U, 450 km) only appears in
  Part 2. Only the Chapter 4.1 equipment sheet was converted to EO-Sat-1. Whether
  the whole of Part 1 should be re-anchored on EO-Sat-1 is a pedagogical
  decision, not a factual one. **D-2 was applied where it belonged** — the
  "no propulsion" arguments in Chapters 1.1-2.4 are UniSat-1's and are legitimate,
  so they were left; the Chapter 3.2 and 3.4 versions were rescoped.
- **`learner_workbook_expanded.md` is a generic SpaceCDF design workbook**, not
  an EO-Sat-1 operations workbook — no func_ids, no hex ids, no answer keys. It
  got a numerical-correctness pass (a systematic +9 dB FSPL error through the
  whole reference table, from using 41.44 instead of 32.44 in the constant, and
  a 9 dB error in the worked link budget that a "let us recompute" note falsely
  reconciled). It did not need, and did not get, the simulator sweep.
- **Procedure-ID divergence between book and simulator is wider than CTG-006.**
  Six contingency IDs and five emergency IDs name different cards in the two
  systems. Read-across tables were added rather than renumbering. Converging
  them is a coordinated pass across both.
- **Chapter 14's nominal IDs were remapped to the shipped index** (eclipse card
  NOM-006 -> NOM-010, software upload -> NOM-006, manoeuvre execution ->
  NOM-013). Cross-references to NOM-005/006/008/010/011 elsewhere in the book
  should be re-checked against that remap.
- `facilitator_book_expanded.md` has a **systematic table-caption off-by-one**
  through several chapters — every caption describes the table above it but
  renders above the next one. It is a generation artefact and fixing it means
  renumbering, so it was left alone.
- `assets/figures/fig_link_loss_ladder.svg` still draws the old S17/rate/
  tx-power ladder; the alt-text and caption were corrected but the SVG needs
  redrawing. Figure 3.3.1's 1300 km guide is baked into a PNG.

### Carried forward unchanged
- The interface redesign in `docs/design_2026-08/` stays unimplemented until you
  say otherwise; nothing in the books describes it.
- `docs/STATUS.md` carries everything else.

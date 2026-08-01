# Textbook vs Simulator — Capability Differences Report

**Scope:** *SpaceCDF — A Course Textbook* (SCDF-TEXT-001 Issue 1, 546 pp.) vs the EO-Sat-1 / EOSAT-1 operations simulator (`SpaceMissionSimulation` repo, working tree as mounted 2026-07-28).
**Sources:** `textbook_inventory.md` (book side), `integration_trace.md`, `design_issues.md`, `power_model_extract.md`, `scenario_config_inventory.md`, `test_strategy.md` (sim side), plus direct spot-checks of the repo (`configs/eosat1/**`, `packages/smo-*/**`) performed for this report. Sim citations are file:line in the repo; book citations are printed page numbers.
**Premise:** the textbook deliberately teaches more than the simulator implements (Part 1 is a design course served by the separate SpaceCDF design tool). That breadth is not a defect. What this report makes explicit is (a) every capability the book teaches that the sim does not implement, (b) every place where book and sim both have a number and the numbers disagree, and (c) every explicit "the simulator does X" claim in the book checked against the code.

---

## 1. Executive summary

**Headline divergences (worst first):**

1. **Propulsion — taught in depth, absent from the sim entirely.** Part 1 teaches propulsion across four technology families with worked sizing (pp. 122–123, 192–198, 530). Part 2 then gives EOSAT-1 *operational* propulsion: orbit-maintenance procedures NOM-004/NOM-005 (FDS ΔV plan → S11 time-tagged upload → `PROP_ARM` → burn, pp. 447–449), contingency CTG-006, and App A's Propulsion Engineer role. In the simulator there is **no propulsion at all**: `PROP_ARM` has zero hits anywhere in `configs/` or `packages/`; the S8 function catalogue (0–84 + 100–107) has no propulsion block; no propulsion subsystem model exists. The only trace is the thruster *heater* circuit (`tc_catalog.yaml:805` HEATER_THRUSTER, circuit `2=thruster` at :825). NOM-004, NOM-005 and CTG-006 are un-runnable as written. The book itself half-flags this (§5.7 omits propulsion from the spacecraft description) but still ships the procedures.
2. **The book's own "simulator artefact" numbers are wrong about the simulator.** The book repeatedly states the sim idles at 70 W and builds worked operator calculations on it (pp. 309, 371, 413). In code, `platform_idle_power_w: 70.0` is loaded but **never used** (`eps_basic.py:188` vs consumption model :346–419); actual idle is **20 W** (OBC 15 + TTC RX 5) and full nominal platform is **36 W**. Every eclipse-exit SoC exercise keyed to 70 W produces answers the sim will not reproduce.
3. ~~**RF frequency plan: the sim flies UHF while the book (and the sim's own ground/rfsim configs) say S-band.**~~ — **CLOSED 2026-07-29 (DI-05, defects #84/#85).** `configs/eosat1/subsystems/ttc.yaml` now flies **S-band 2025.0 up / 2200.0 down**, matching `planning/ground_stations.yaml` and `rfsim.yaml`, and the link budget reads the *active station's* G/T instead of one global 20 dB/K. The book's COM-006 baselines (14.5 dB @ 1 kbps, 6.8 dB @ 64 kbps) still cannot be reproduced, but not because of the frequency plan: **the pair is internally inconsistent.** An 18 dB rate ratio has to produce an 18 dB margin difference and those two numbers differ by 7.7 dB, so no correct model of any band could yield them. COM-006 now carries a re-measured per-tier, per-station table from `tools/link_budget_ladder.py` (§3 row 7).

4. **The MPS → S11 autonomy workflow — the book's central "how a plan gets on board" teaching (pp. 336–337, 357–361, NOM-004) — is broken end-to-end in the sim.** The on-board S11 scheduler works, but: the planner never emits S11; the plan upload discards the scheduled time; planner activity `func_id`s command the wrong subsystems (an "imaging pass" toggles EPS power lines and does zero payload actions); and the MCS S11 builder produces embedded packets the OBC rejects at fire time (`integration_trace.md` §2). The MCS also never displays the planner schedule (schema + time-base mismatches, §1).
5. **Contact geometry: engine gates contact on Iqaluit only** (`propagator.py:69,152-161`); every Troll pass the planner (and the book: 3 Troll passes/day, §5.7; ~14 total/day, p. 358) promises is dead air — uplink rejected, no TM.
6. ~~**Payload data-volume model is three orders of magnitude off the book**~~ — **CLOSED 2026-07-29 (defect #82, roadmap N-11).** `payload.yaml image_size_mb` is now **4.0 MB**, the book's figure (pp. 360, 404). The activity catalogue's calibration (50 → 0.5 MB) and housekeeping (10 → 0.05 MB) volumes, scaled to the 800 MB world, were corrected in the same pass. **Re-derived 2026-07-29 for the rate ladder (defects #85/#86):** at the HIGH tier one image is **262 s of channel** (328 s with the planner's 20 % protocol allowance) instead of 655 s; the five-station network shifts **~227 MiB/day** if every usable second ran at HIGH and **~28 MiB/day** at LOW, or **~162 MiB/day** on the realistic blend the planner computes from each pass's geometry; and the shipped `nominal_ops_day` closes with **~80 % margin on the capacity of the passes it books at HIGH**. The lesson survived the rate increase because the ladder gates the top rung rather than leaving it always available.

7. **Housekeeping is now a first-class term in the data budget.** The shipped `telemetry/hk_structures.yaml` generates 1351.5 bps of PUS packets across SIDs 1–6 = **1557 bps of channel** after RS(255,223) and TM framing. A day of that is 16.0 MiB and consumes **36 % of the whole network's contact time at LOW** and 4.5 % at HIGH. This is why the retired 1 kbps "low rate" was indefensible: it was *below* the generation rate, so the real-time backlog could never drain.

**What is in good shape:** the command/telemetry backbone. The 14-service PUS set matches the book exactly; S8 function IDs, AOCS mode enum 0–8, payload funcs 26–39, TCS 40–49, OBDH 50–62, TTC 63–79 (79 added 2026-07-29), wing deploy 81/82, TX powers 1/5/8 W, modulations, cooler clamp [−20, 0] °C, HK SIDs 1–6+11, 26 S19 rules, procedure tree + index, `mcs/positions.yaml`, `tools/orbit_tools.py` — all verified present and matching. The sim also implements substantial capability the book doesn't cover (instructor console, 31 scenarios, 54 failure modes, breakpoints, RF-signal chain) — sim-exceeds-book, no action needed.

**Matrix totals (Section 2):** 18 Implemented · 12 Partial · 9 Absent · 8 Different-numbers, over 47 assessed capability rows (Part-1 design-only topics grouped). Of the book's 28 explicit simulator references: **13 TRUE, 10 PARTIAL, 3 FALSE, 2 not-assessable** (SpaceCDF-tool-side).

---

## 2. Master capability matrix

Status legend: **Impl** = implemented & consistent · **Part** = partially implemented · **Abs** = absent from sim · **Diff#** = implemented but numbers disagree (see §3). Actions: **SIM** = sim upgrade candidate · **CAV** = textbook caveat box · **WBK** = workbook/exercise change · **—** = no action.

### 2.1 Part 1 design capabilities (taught for design breadth; sim is an *operations* trainer)

| Capability | Book (pages) | Sim status | Divergence note | Action |
|---|---|---|---|---|
| Propulsion design (ΔV, chemical/cold-gas/electric, sizing) | 122–123, 192–198, 530 | **Abs** | Design-tool topic; but see propulsion *ops* row below — the book crosses the line into sim territory | CAV (one box: "no propulsion in the sim") |
| Passive deorbit / drag sail / tether | 119, 124, 192, 196 | Abs | Pure design topic | — |
| Structures, mechanisms design, launch env., deployers | 66–74, 229–235, 535 | Abs (deploy *commands* exist, see 2.3) | Design topic | — |
| Radiation environment / EDAC / SEU | 123–124, 143, 238 | Abs | No radiation model; OBDH memory-scrub command exists cosmetically (func 51) | — |
| Constellations, ISL, Walker | 121–122, 294–295 | Abs | Single-spacecraft sim | — |
| Cost, risk/FMECA, V&V/test programme, reviews, SE process, requirements | Ch 1.1–2.4, 4.1–4.4 | Abs | SpaceCDF design-tool territory | — |
| Regulatory (RSSSA, ISED, ITU) | 46–74 | Abs | Design/licensing topic | — |
| EO instrument design (GSD, aperture, SNR sizing) | 75–105, 263, 533–534 | Abs (ops-side imager exists, see 2.3) | Sim models an imager operationally, not radiometrically | — |
| Thermal design (balance, coatings, MLI sizing) | 153–162, 534 | Part | Sim has a zone/heater/coupling thermal model but with ad-hoc constants, not the book's radiative balance (`tcs_basic.py:135-158, 384-404`; several `tcs.yaml` sections dead) | CAV (sim thermal is behavioural, not physical) |
| Link-budget design method (18-line budget, coding gains) | 201–226, 532–533 | Part | rfsim implements a real coding/DSP chain (RS/Viterbi/BCH, BER vs Eb/N0); but the *flying* frequency plan contradicts the book — see §3 row 6 | SIM (fix ttc.yaml) |

### 2.2 EOSAT-1 platform & operations — spacecraft side

| Capability | Book (pages) | Sim status | Divergence note | Action |
|---|---|---|---|---|
| **Propulsion operations** (NOM-004/005, `PROP_ARM`, CTG-006, post-burn state vector) | 447–449, 343, 518 | **Abs** | Zero `PROP_ARM` hits repo-wide; no S8 propulsion block (catalogue verified 0–84+100–107); only HEATER_THRUSTER (func 42) exists. Procedures un-runnable | **SIM** (add minimal prop model + S8 block) or **WBK** (pull NOM-004/005/CTG-006) + CAV |
| AOCS control modes (`AOCS_SET_MODE` func 0, modes 0=OFF…8=ECLIPSE) | 312–313, 341 | **Impl** | Verified verbatim `tc_catalog.yaml:485` ("8=eclipse_propagate"). Book's legacy Ch 6.3 table (func 32, 0=IDLE…4=FINE_POINT, p. 324) does not exist in the sim — book already flags this | CAV exists; keep |
| Reaction wheels / desat / momentum mgmt | 188–190, 414–415, 450 | **Diff#** | Sim wheel max **5500 rpm** (`aocs.yaml:5`), S12 alarms at **±5000** (`s12_definitions.yaml:87-118`), and S19 wheel-overspeed rules **disable the wheel** (func 2) rather than desaturate; desat is triggered by a separate *momentum-saturation* rule (func 1) (`s19_rules.yaml:32-36,155-160`). Book: limit 5000, yellow 6000/red 8000, `aocs_wheel_saturation` >8000 → desat | CAV + WBK (Ch 13.7 exercise) or SIM (align limits) |
| Star trackers (ST1/ST2, select) | 310 vs 341 | **Impl** | `ST1_POWER`/`ST2_POWER`/`ST_SELECT` (`tc_catalog.yaml:506-526`) + S2 devices (:76,83). §5.7's "single-head" wording is the wrong side of the book's own inconsistency | CAV (fix §5.7 text) |
| GPS / time sync (func 80; GPS feeds propagator) | 173, 329 | **Impl** | Plus func **84** `AOCS_GPS_SET_START_MODE` (`tc_catalog.yaml:602-605`) not in the book's 0–82 range | CAV (extend range) |
| B-dot detumble / detumble phase (<0.5°/s) | 183, 312 | Impl | Detumble mode 2 present; phase machine in engine | — |
| EPS battery (120 Wh) | 309, 324–325 | **Impl** (capacity) / Diff# (voltages, shed) | 120.0 Wh confirmed (`eps.yaml:14`). Voltage curve & load-shed logic differ — §3 rows 3–4 | CAV |
| Solar arrays / wing deploy (funcs 81/82) | 309, 344–345 | **Diff#** | Deploy commands + 30 s deploy implemented (`eps_basic.py:205-262`); but generation is ~3× the book's 28 W BOL (§3 row 2), with a legacy 245 W fallback model whenever AOCS is off | CAV + SIM (retire fallback model) |
| Load shedding stages 0–3 | 342, 325 | **Part** | Sim has: voltage-triggered shed at 26.5 V, one line/tick, **no auto-restore** (`eps_basic.py:56-57,480-491`); EPS-mode shed (safe=2 loads, emergency=4); the SoC-staged 30/20/10 % path is **dead code** (engine reads wrong attribute, `engine.py:618`). Book's "stages 0–3 at SoC <40 %" matches none of these | SIM (fix engine.py:618) + CAV |
| 8 switched power lines, payload = line 3 | 342 | Impl | 8 lines confirmed; note obc & ttc_rx are *unswitchable* (`POWER_LINE_DEFS`, `eps_basic.py:31-41`) — "8 switched" is 6 switched + 2 essential | CAV (minor) |
| Thermal zones, 3 heater circuits (battery/OBC/thruster), setpoints, duty, decontamination, thermal map (funcs 40–49) | 326–327, 343 | **Impl** | All funcs 40–49 present incl. TCS_DECONTAMINATION_START/STOP, TCS_GET_THERMAL_MAP (`tc_catalog.yaml:805-856`). Quirk: EPS bills full heater wattage regardless of thermostat duty (`eps_basic.py:378-379` vs `tcs_basic.py:406-419`) | — (note for power exercises) |
| FPA Peltier cooler, setpoint clamp [−20, 0] °C | 326, 402, 439 | **Impl** (clamp) / **Diff#** (timing) | Clamp verified (`payload_basic.py:890-897`); default/target −15 °C (`payload.yaml:2`). But cooling time constant is **100 s** (`fpa_tau_cooling_s`) → setpoint in ~5–8 min, not the book's 60–90 min | CAV or SIM (raise τ) + WBK (COM-010) |
| Payload imaging command set (funcs 26–39: mode, scene, capture, download, delete, bad-segment, catalog, band config, integration, gain, cooler, cal start/stop, compression) | 342–343 | **Impl** | All 14 verified by name (`tc_catalog.yaml:689-784`). No spectral/radiometric model behind the band mask (no 443/560/665/865 nm anywhere in `payload.yaml`) — band config is bookkeeping only | CAV (bands are cosmetic) |
| Image size / storage / data budget | 360, 404, 350–352 | **Impl** | **4 MB/image**, 20 GB store, 80 Mbps internal readout (`payload.yaml`) — matches the book. Decision taken 2026-07-29 (SIM side, defect #82): the simulator shrank to the book's number. With the rate ladder (defects #85/#86) the shipped `nominal_ops_day` plan closes with ~80 % margin on the capacity of the passes it books — §3 row 8 | WBK: restate the "32 s at 1 Mbps" rule of thumb as **262 s at the 128 kbps HIGH tier** (328 s with the planner's 20 % protocol allowance) |
| Calibration (dark + flat), First Light, geo-registration 87 m | 392–405 | Part | Cal commands exist (37/38); no geo-registration/quality model verified beyond `last_image_quality` telemetry | — |
| OBDH ops (funcs 50–62: modes, scrub, reboot, redundant OBC, CAN A/B, boot, watchdog, diagnostics, error log) | 322–323, 344 | **Impl** | Funcs 50–62 all present (verified); bootloader mode, SID-11-only beacon behaviour implemented (`hk_structures.yaml:275-279`) | — |
| TT&C command set (rates func 65, TX power 68, antenna deploy 69, modulation 74, beacon, ranging, coherent, UL/DL freq set) | 327–328, 344, 400 | **Impl / Diff#** | 1/5/8 W and BPSK/QPSK/8PSK/OQPSK verbatim. **Rates changed 2026-07-29 (defect #85):** func 65 now selects a rung of a three-tier ladder (`ttc.yaml tm_rate_tiers`: BEACON 1 / LOW 16 / HIGH 128 kbps), and a new func 79 `TTC_SET_DOWNLINK_MODE` gates the top rung. The book's 0=low / 1=high *encoding* is preserved verbatim, so every printed command still works — only the rates behind the codes moved | **WBK** (rates + func 79; the encoding needs no change) |
| TT&C link physics (frequencies, margins, per-station G/T) | 309–310, 396, 414 | **Diff#** | UHF frequencies flown; global G/T only — §3 rows 6–7; DI-05 | **SIM** |
| PUS services (S1,S2,S3,S5,S6,S8,S9,S11,S12,S13,S15,S17,S19,S20) | 340, 311 | **Impl** | `mcs/pus_services.yaml` declares exactly these 14 (verified); dispatcher implements them (`service_dispatch.py:4-5` + S2/S13 handlers). Book's provenance detail ("12 declared + 2 only in tc_catalog") is slightly off — all 14 are declared | CAV (one-word fix) |
| S13 large-data image downlink | 340, 343 | Part | Happy path works (acceptance-tested); abort/NAK/timeout/concurrent paths untested (`test_strategy.md` #5) | SIM-adjacent (tests) |
| S11 time-tagged commanding (on board) | 336–337, 447 | **Part** | On-board scheduler fully works (`tc_scheduler.py`; `service_dispatch.py:991-1021`; ticked `engine.py:896-899`). Ground side broken: MCS builder emits malformed embedded packets that die at fire time; no UTC→CUC helper; planner never uses S11; `docs/PUS_SERVICE_REFERENCE.md:452-472` documents the *delete-all* subtype (11) as insert | **SIM** (fix builder + CUC helper) |
| S19 event-action (26 rules), enable/disable 19.4/.5, no table swap | 323, 338, 377, 498 | **Impl** | 26 rules confirmed; enable/disable per rule; no table-swap code — book's claims TRUE. Limitation the book doesn't state: an action = exactly **one parameter-less S8 function** (`service_dispatch.py:1374-1385`) | CAV (state the one-func limit) |
| S12 monitoring | 338, 497 | **Diff#** | **38** monitors in `s12_definitions.yaml` (verified count). Book p. 338 (and cited `sot_index`) says 25 — wrong; Ch 15.2's "38 active" is right. `outputs/sot_index.md` does not exist in the repo | CAV (fix p. 338) |
| FDIR hierarchy, severity ladder, safe mode | 314–316, 338–339, 406–411 | Part | Engine FDIR + subsystem `fdir.yaml` + S12/S19 give a working 3-layer net; the book's 5-level hierarchy and `fault_propagation.yaml` cascade/staged-shed layer is partially dead config (§3 row 4; `scenario_config_inventory.md` §3.5) | SIM (wire fault_propagation) |
| On-board autonomy = S11 + S19 only, **no OBCP/S18** | 336–340, 498 | **Impl** (consistent) | Verified: zero S18/OBCP anywhere (`integration_trace.md` §3). Book never claims OBCPs — book and sim agree | — |
| Mission phases 0–7, separation, bootloader ops | 309, 376–391 | Impl | Phase machine + bootloader (SID 11 beacon, allowlisted funcs) implemented | — |
| Deorbit/disposal (phase 7), passivation | 309, 350 | Abs | Declared out of Part-2 scope by the book; nothing in sim | — |

### 2.3 Ground segment & operations products

| Capability | Book (pages) | Sim status | Divergence note | Action |
|---|---|---|---|---|
| MCS console (tabs, positions, procedures panel, FDIR panel, commanding, alarms, go/no-go) | 332–335 | **Impl** | `mcs/positions.yaml`, `displays.yaml`, `limits.yaml` exist; go/no-go + procedure runner tested (69 tests). **URL wrong in book**: MCS serves :9090; :8080 is the *simulator* API; planner is :9091 | CAV (ports) |
| Dual-key commanding (first-key → FD GO → second-key) | 334 | Part | Go/no-go coordination exists; the book's exact two-key + S1-ack choreography partially implemented ("several confirmation UI features are not implemented in the MCS UI" — book p. 416, matches code) | CAV exists |
| Procedure catalogue (64: 7/13/12/27/5) | 416, 496 | **Diff#** | 64 total confirmed; actual split **7/13/11/27/6** (nominal 11 not 12; emergency 6 not 5) | CAV |
| MPS: imaging targets, activity types, contact schedule, conflict/constraint checker, plan exporter → S11 | 336–337, 357–361 | **Part** | `planning/imaging_targets.yaml` + `activity_types.yaml` + constraint checkers exist. But: activity `func_id`s are fabricated and hit wrong subsystems (imaging pass = EPS no-ops, `activity_types.yaml:17-28` vs `service_dispatch.py:461-487`); upload discards `start_time`; **no S11 export exists at all**; MCS never shows the schedule (schema + wall-clock-vs-sim-time mismatches, `integration_trace.md` §1) | **SIM** (top-ranked fixes, §5) |
| Pass cycle / contact windows display | 350–352, 357–359 | Part | Planner computes real windows for both stations; MCS contact panels render only *synthetic* fallback data (field-name mismatch: `aos` vs `aos_utc`) — trainees see fabricated passes when the planner is up | **SIM** |
| FDS: orbit tools (`orbit_tools.py predict`), eclipse schedule, GPS-reset propagation | 329–330, 350, 358 | **Impl** (tools) / Part | `tools/orbit_tools.py` exists as cited. Runtime caveat: engine ignores `t0_epoch` (starts at wall clock, `propagator.py:70`, DI-16) → non-reproducible pass/eclipse geometry vs the book's fixed examples | SIM (honour t0_epoch) |
| FDS: conjunction assessment (TLE screening) | 350 | **Abs** | Zero "conjunction" hits in `packages/` or `configs/` | CAV |
| FDS: manoeuvre planning (ΔV) | 350, 447 | **Abs** | No propulsion; no ΔV planning anywhere | CAV (with propulsion box) |
| Ground stations Iqaluit + Troll | 309, 358 | **Part** | Both configured (`orbit.yaml:12-24`); engine contact gating **Iqaluit-only** (`propagator.py:69,152-161`) — Troll passes are dead air; station truth duplicated across 4 files with 3 merge policies (DI-04) | **SIM** |
| rfsim baseband (CCSDS coding, CLTU/CADU, channel, GS failures) | 335–336, 352–353 | **Impl** | Real RS(255,223)/Viterbi/BCH/ASM chain, 175 tests — actually *richer* than the book's protocol treatment | — |
| Delayed TM / 14-bit seq-wrap operator math | 413 | Impl | S15 stores + delayed_tm_viewer tool exist | — |
| Telemetry limit files (`telemetry/parameters.yaml`, `mcs/limits.yaml`) | 347, 497 | Impl | All Table-7.13 files verified present (incl. `mcs/role_analysis/`) | — |
| Manual chapters `manual/00..11_*.md` | 310 | Impl | Present — actually 00–**12** plus duplicate-numbered 09/10 pairs (`09_command_reference.md`+`09_leop.md`, two `10_*.md`) | CAV (cite by name not number) |
| Instructor/scenario layer (31 scenarios, 54 failure modes, breakpoints, debrief) | *not covered by book* | Impl (sim-only) | Sim exceeds book; the book never teaches the instructor side — fine for a student text | — (optional appendix) |

---

## 3. "Numbers that disagree" table

| # | Quantity | Textbook value (page) | Simulator value (file:line) | Note |
|---|---|---|---|---|
| 1 | Platform idle power | **70 W** "the platform idles at 70 W in the simulator" (pp. 309, 371, 413) | **20 W** idle (obc 15 + ttc_rx 5); 36 W full nominal platform; `platform_idle_power_w: 70` loaded but never used (`eps_basic.py:188, 346-419`; `eps.yaml`) | The book's *claim about the sim* is false; all 70 W-based worked answers unreproducible |
| 2 | Solar array power | 28 W BOL deployable arrays (p. 309) | Wings deployed: sunlit-avg ≈ **82 W**, peak ≈ 105 W; body-only ≈ 22 W; AOCS-off legacy fallback ≈ **245 W** (`eps.yaml solar_panels/arrays`; `eps_basic.py:270-345`) | ~3× book value; fallback model masks LEOP deficits |
| 3 | Battery/bus voltage | 4S, ~27 V nominal; telemetry 27–29 V; 28.1 V "healthy" (pp. 309, 325, 395) | OCV 21.5–29.2 V linear, nominal **26.4 V**, bus clamped ≤29 V (`eps.yaml`; `eps_basic.py:453-471`) | Book's "healthy 27–29 V" band sits ~0.5–2.5 V above sim nominal and just above the shed trigger |
| 4 | Load-shed trigger | `eps.battery_soc_pct` red **<40 %** → load shed (p. 324); stages 0–3 (p. 342) | Bus-voltage shed at **26.5 V** (≈65 % SoC under load), one line/tick, no auto-restore (`eps_basic.py:56-57,480-491`); S12 SoC warn **20 %** / alarm **10 %** (`s12_definitions.yaml:10-24`); FDIR SoC 20/15 % (`subsystems/fdir.yaml`); staged 30/20/10 % path dead (`engine.py:618` reads nonexistent attr) | Four different shed truths, none = book's 40 % |
| 5 | Wheel speed limits | Limit 5000 rpm = 8 mN·m·s; S12 yellow 6000 / red 8000; desat trigger 75 % (3750); S19 `aocs_wheel_saturation` >8000 → desat (pp. 313, 414–415, 498) | Max **5500 rpm** (`aocs.yaml:5`); S12 single alarm band **±5000** (`s12_definitions.yaml:87-118`); S19 per-wheel overspeed → **disable wheel** (func 2), momentum-saturation → desat (func 1) (`s19_rules.yaml:32-36,155-160`) | Different limits *and* different autonomous response |
| 6 | RF frequencies | S-band prime, UHF bootloader beacon only (pp. 309–310) | **RESOLVED 2026-07-29 (DI-05).** `ttc.yaml` flies **S-band 2025.0 up / 2200.0 down**, matching `ground_stations.yaml` and `rfsim.yaml`. The bootloader beacon is the BEACON rung of the S-band ladder rather than a separate UHF chain — a simplification the book should note, but no longer a contradiction | **Closed.** Residual WBK note: the sim has no UHF whip; the beacon is S-band at 1 kbps |
| 7 | Link-margin baselines | 14.5 dB @1 kbps, 6.8 dB @64 kbps "mission baseline" (p. 396); per-station performance | **Re-measured 2026-07-29** (`tools/link_budget_ladder.py`, driving the flight model against the shipped S-band config, per-station G/T now read). At 5 deg: BEACON 56.3–59.8 dB, LOW 44.2–47.7 dB, HIGH 33.7–37.2 dB. Full matrix printed in COM-006 and `docs/LINK_BUDGET_AND_RATES.md` | **The book's pair is internally inconsistent and always was:** an 18 dB rate ratio must produce an 18 dB margin difference, and 14.5 vs 6.8 differ by 7.7 dB. No correct model can reproduce them. **WBK:** reprint COM-006's baselines from the tool. The sim's rungs *do* differ by exactly the budget's `-10*log10(R)`: 12.04 dB BEACON→LOW, 10.53 dB LOW→HIGH |
| 8 | Image size / storage / rate | ~4 MB/session; "4 MB → 32 s at 1 Mbps" (pp. 360, 404); 240 Mbps-raw design examples | **RESOLVED 2026-07-29 (defect #82, roadmap N-11):** **4 MB/image**, 20 GB store, 80 Mbps internal readout, line rate 500 Hz (`payload.yaml`) — the size MATCHES the book | **No longer a divergence.** At the HIGH tier (128 kbps, defect #85) one 4 MiB image is **262 s of channel**, or **328 s** with the planner's 20 % protocol allowance — 58 % / 74 % of the 445 s average pass. The book's "32 s at 1 Mbps" is now wrong by 8x rather than 20x, and the gap is a *rate* gap only. **WBK:** restate as "4 MB → 262 s at 128 kbps, most of a pass", which is also the better lesson: an image dump costs a schedulable fraction of a pass |
| 9 | Downlink rates | Two rates 1/64 kbps (p. 400) — but Part 1 sizes 1–5 Mbps and Ch 5.20 quotes 2–10 Mbps (p. 313) | **THREE rates since 2026-07-29 (defect #85):** BEACON 1 000 / LOW 16 000 / HIGH 128 000 bps, with per-tier modulation and coding and permission gates on the top rung (`ttc.yaml tm_rate_tiers`). Modelled on Sentinel-1's programmable 16/128/512 kbit/s S-band TM ladder | **The divergence flipped direction.** It used to be "the sim's rates are lower than the book's Part-1 figures"; it is now "the sim's *architecture* is richer than the book's". p. 400's flat two-rate claim is **FALSE** (§4 row 21). Part 1's 1–5 Mbps and Ch 5.20's 2–10 Mbps remain above the sim, but 128 kbps is a defensible S-band TT&C rate for a 6U (Sentinel-1's nominal; AX2150/EnduroSat class) whereas 64 kbps was not. **WBK** |
| 10 | Beacon interval | SID 11 @ **30 s** (p. 497 area, Ch 15) | **10 s** (`hk_structures.yaml` sid 11 `interval_s: 10.0`) | Also: beacon carries 6 params (voltage, SoC, obc_mode, reboot_count, sw_image, active_obc) |
| 11 | S12 monitor count | 25 (p. 338) vs 38 (Ch 15.2 header, p. 497) | **38** (`s12_definitions.yaml`, counted) | Ch 15.2 right, p. 338 + cited `sot_index` wrong; `outputs/sot_index.md` absent from repo |
| 12 | Procedure split | 7/13/**12**/27/**5** = 64 (p. 416) | 7/13/**11**/27/**6** = 64 (`configs/eosat1/procedures/*/`, counted) | Total right, two categories off by one |
| 13 | S8 function range | 0–82 + legacy 100–107 (p. 341) | 0–**84** + 100–107; 84 = `AOCS_GPS_SET_START_MODE` (`tc_catalog.yaml:602-605`); no func 83; **183 commands** total | |
| 14 | Passes/day | 4 Iqaluit + 3 Troll (§5.7, p. 309) vs ~14/24 h (p. 358) | Planner: both stations; **engine contact = Iqaluit only** (`propagator.py:69,152-161`) | Neither book figure is what a trainee experiences; effective commandable passes = Iqaluit's only |
| 15 | FPA cooler time-to-setpoint | 60–90 min (pp. 402, 439) | τ_cool = **100 s** (`payload.yaml:4`) → setpoint in ~5–8 min | COM-010's "wait 60–90 min" step is wrong by ~10× |
| 16 | AOCS mode integers | Authoritative: func 0, modes 0=OFF…8=ECLIPSE (pp. 341, 313); legacy Ch 6.3: func 32, 0=IDLE…4=FINE_POINT (p. 324) | Func 0, "0=off,1=safe_boot,2=detumble,3=coarse_sun,4=nominal_nadir,5=fine_point,6=slew,7=desaturation,8=eclipse_propagate" (`tc_catalog.yaml:485`) | Sim = authoritative table exactly; legacy table has no counterpart (book self-flagged). Sim-internal: `aocs.yaml:2` lists only 5 mode names |
| 17 | Orbit period | 93.7 min computed vs 94.5 min sim (pp. 309, 530) | TLE mean motion 15.24 → **94.5 min** (`orbit.yaml`); AOCS sun-sweep hard-codes 5700 s (`aocs_basic.py:1108-1110`) vs TLE's 5669 s | Book's p. 530 check TRUE; caveat: engine ignores `t0_epoch` at runtime (starts at wall clock, `propagator.py:70`) so absolute timings drift run-to-run |
| 18 | Ground-system URLs | MCS at localhost:**8080**; MPS at :8080/planning/ (pp. 332, 357) | Simulator API = :8080; **MCS = :9090**; planner = :9091 (`smo-simulator/server.py:155-158`; `smo-planner/server.py:119`) | Every "open the console" instruction points at the wrong service |
| 19 | SA current telemetry | 1.5–2.5 A lit (p. 325) | panel_current = power/28 → ≈2.9 A avg / 3.75 A peak wings-deployed (`eps_basic.py` §4) | Follows from row 2 |
| 20 | FPA operating band | −15…−3 °C in use; S12 band −15 to −3 (pp. 326, 439) | Target −15.0 °C, clamp [−20, 0] verified (`payload.yaml:2`; `payload_basic.py:890-897`) | Consistent — listed for completeness |

---

## 4. The 28 explicit simulator references — verdicts

Numbering per `textbook_inventory.md` §2. Verdict = claim checked against the mounted repo.

| # | Claim (page) | Verdict | Evidence |
|---|---|---|---|
| 1 | EOSAT-1 6U, 450 km SSO, flown against the simulator (p. 304) | **TRUE** | `orbit.yaml`: 450 km, 98°, TLE 15.24 rev/day |
| 2 | "Textbook is what an operator must know, not what the simulator implements" (p. 305) | **TRUE** (policy statement; and accurate — see all PARTIALs below) | — |
| 3 | "In the simulator, the platform idles at 70 W" (p. 309) | **FALSE** | `platform_idle_power_w` dead (`eps_basic.py:188`); actual idle 20 W, nominal 36 W (§3 row 1) |
| 4 | "The simulator runs a 120 Whr pack" (p. 309) | **TRUE** | `eps.yaml battery.capacity_wh: 120.0` |
| 5 | Manual lives at `configs/eosat1/manual/00..11_*.md` (p. 310) | **PARTIAL** | Directory exists; actual set is 00–12 with duplicate-numbered 09 and 10 files |
| 6 | Source-of-truth reconciliation note exists (p. 311) | **TRUE** | Reconciliation is real (`hk_structures.yaml:283` "canonical per SPACECDF_RECONCILE item 8") |
| 7 | No rule-table swapping; per-rule enable/disable only (pp. 323, 377) | **TRUE** | S19.4/.5 enable/disable implemented; no table-swap code path exists |
| 8 | "The simulator models a 4S bus; treat 27 V values as simulator-specific" (p. 325) | **PARTIAL** | Sim models a lumped pack (no cell count), OCV 21.5–29.2 V, nominal 26.4 V — "27–29 V healthy" only holds above ~72 % SoC and sits just above the 26.5 V shed trigger |
| 9 | Peltier cooler, `PAYLOAD_COOLER_SETPOINT` clamped [−20, 0] °C, monitor band −15…−3 (p. 326) | **TRUE** | `payload_basic.py:890-897`; `payload.yaml` target −15.0 |
| 10 | MCS single webapp at localhost:8080; `mcs/positions.yaml` maps position→tabs (p. 332) | **PARTIAL** | `positions.yaml` exists and does that; **MCS is :9090**, :8080 is the simulator |
| 11 | Procedures = Markdown under `procedures/{leop,…}` + `procedure_index.yaml` (p. 337) | **TRUE** | Verified tree + index |
| 12 | Rules in `s12_definitions.yaml`/`s19_rules.yaml`; sot_index §12: 26 S19, 25 S12 (p. 338) | **PARTIAL** | Files TRUE; 26 S19 TRUE; S12 = **38** not 25; `outputs/sot_index.md` not present in repo |
| 13 | "12 PUS services declared in `mcs/pus_services.yaml`, plus S2 and S13 present in tc_catalog" (p. 340) | **PARTIAL** | Right 14-service set, wrong provenance: `pus_services.yaml` declares **all 14** including s2 and s13 (verified) |
| 14 | `tc_catalog.yaml` defines S8 func IDs 0–82 + legacy 100–107 (p. 341) | **PARTIAL** | Actual range 0–**84** (84 = AOCS_GPS_SET_START_MODE) + 100–107; 183 commands |
| 15 | "What you will not find in the simulator" legacy-name table (pp. 345–346) | **TRUE** | No `OBC_NOOP`/`FDIR_LOAD_TABLE`/`IMU_ENABLE`/`PAYLOAD_POWER_ON` in catalog; replacements exist as stated |
| 16 | Table 7.13 config-file paths (p. 347) | **TRUE** | All eight paths verified present (incl. `mcs/role_analysis/`) |
| 17 | Pass 8–12 min; MPS exercise at :8080/planning/; FDS runs `tools/orbit_tools.py predict --hours 24` (pp. 357–358) | **PARTIAL** | `orbit_tools.py` exists; pass durations plausible; **planner is :9091 not :8080/planning/**, and the sim only honours Iqaluit contacts |
| 18 | Eclipse SoC worked example: 70 W sim idle, 120 Whr (p. 371) | **FALSE** | 70 W figure false (ref 3); 120 Whr true — the worked answer (~51 %) is unreproducible in the sim |
| 19 | "No simulator-pause to win an exercise. The clock runs." (p. 380) | **TRUE** as policy | (Note: instructor freeze/speed control does exist — `engine.py:1755` — the statement is a course rule, not a capability claim) |
| 20 | COM-010 −10 °C reachable; COM-005 28.1 V healthy; COM-006 margins 14.5/6.8 dB become baseline (pp. 394–396) | **PARTIAL** | −10 °C within clamp TRUE (and reached in minutes, not the card's 60–90 min); 28.1 V only at high SoC; the 14.5/6.8 dB baselines are **not reproducible and never were** — they are internally inconsistent (§3 row 7). COM-006 now carries a re-measured per-tier, per-station margin table generated by `tools/link_budget_ladder.py`; the printed pair must be replaced by it |
| 21 | "The EOSAT-1 simulator only supports the two rates" 1/64 kbps (p. 400) | **FALSE since 2026-07-29** | Three rates: BEACON 1 / LOW 16 / HIGH 128 kbps (`ttc.yaml tm_rate_tiers`). The *command encoding* the book prints is unchanged — func 65 still takes 0=low / 1=high — so exercises that command a rate still work; what changed is which rates those codes select, plus a new func 79 gating the top rung. **WBK:** replace the sentence and the two figures |
| 22 | "Cooler takes 60–90 minutes to reach setpoint" (p. 402) | **FALSE** | `fpa_tau_cooling_s: 100.0` → minutes, not hours (§3 row 15) |
| 23 | Operator math: eclipse SoC @70 W; wheel limit 5000 rpm = 8 mN·m·s (pp. 413–414) | **PARTIAL** | 70 W part FALSE (ref 3/18); wheel part: sim max is 5500 rpm and S12 alarms at 5000 — the 5000-rpm arithmetic is close but not the sim's actual limit, and the sim's autonomous response differs (§3 row 5) |
| 24 | 64 procedures: 7/13/12/27/5 (p. 416); some confirmation UI not implemented | **PARTIAL** | 64 TRUE; split is 7/13/**11**/27/**6**; UI caveat TRUE |
| 25 | `procedure_index.yaml` authoritative; some cards placeholders (p. 496) | **TRUE** | Index exists; condensed cards defer to files that exist (e.g. `contingency/gps_time_sync_recovery.md` family) |
| 26 | `orbit.yaml` mean motion 15.24 → 94.5 min (p. 530) | **TRUE** | Verified; runtime caveat: `t0_epoch` ignored by the engine (DI-16) |
| 27 | Appendix D: all worked examples computed with the SpaceCDF tool; descriptors in `configs/examples/` (p. 539) | **N/A here** | SpaceCDF-repo claim, outside the ops-sim repo audited; not assessed |
| 28 | Part-1 SpaceCDF tool exercises (tabs, 20 agents, export bundles) (pp. 27–28, 124…355) | **N/A here** | SpaceCDF design tool, separate system; agents/exports confirmed to exist structurally (`design_issues.md` DI-18…DI-20) but tab-level claims not audited |

**Tally: 13 TRUE · 10 PARTIAL · 3 FALSE (refs 3, 18, 22) · 2 N/A.**

---

## 5. Recommendations (ranked)

### A. Close on the simulator side (upgrade-roadmap feed)

1. **Fix the RF frequency plan** — set `ttc.yaml` to S-band (2025/2200 MHz), single ground-station file with per-station G/T resolved at contact (DI-04/DI-05). Small/Medium. Unlocks: entire Ch 3.3/Ch 13.6 link teaching, COM-006 baselines become achievable.
2. **Multi-station contact gating** — `propagator.py:150-161` compute all stations, `in_contact = any()`. ~20 lines. Unlocks: Troll passes, the book's pass-count teaching, `data_dump` activities.
3. **Repair the plan→execution chain** — correct `activity_types.yaml`/`imaging_planner.py` func_ids (26/28 payload, 65 TTC), encode capture parameters, fix the MCS S11.4 builder + add UTC→CUC helper, normalise the contact schema in the MCS proxy, use sim time in contact displays (`integration_trace.md` fixes 1.7-1/2, 2.5-1/2). Small each. Unlocks: Ch 7.3/Ch 8 MPS→S11 workflow — the book's core autonomy lesson.
4. **Decide propulsion**: either (a) add a minimal propulsion block (S8 funcs, `PROP_ARM`, propellant tank telemetry, impulsive ΔV applied to the propagator) so NOM-004/005/CTG-006 run; or (b) formally delete those three procedures and the FDS manoeuvre-planning claims from the course (see B/C). Medium if (a).
5. **Fix the dead EPS/FDIR config**: `engine.py:618` SoC attribute bug (staged load-shed can never trigger), honour or delete `platform_idle_power_w`, retire the 245 W AOCS-off fallback array model. Small.
6. **Honour `t0_epoch`** at engine start (DI-16) so pass/eclipse geometry matches printed worked examples run-to-run. Small.
7. Content-test the seams (test-strategy P1: scenario YAML loads, S19 rule targets, procedures-vs-catalog) so book↔sim drift is caught mechanically next time.

### B. Caveat in the textbook (errata/caveat boxes — cheap, do all)

1. **Correct the three FALSE claims**: 70 W idle (→ 20 W idle / 36 W nominal, or re-word once sim is fixed), the p. 371/413 eclipse-SoC worked numbers, and the 60–90 min cooler wait (→ ~5–10 min).
2. One **"No propulsion in the simulator"** box covering NOM-004/005, CTG-006, FDS manoeuvre planning and conjunction assessment (also absent).
3. Number fixes: S12 count 25→38 (p. 338), procedure split 12/5→11/6 (p. 416), S8 range 0–82→0–84 (p. 341), beacon 30 s→10 s, ports (MCS :9090, planner :9091, sim :8080), array power 28 W→ actual model, wheel 5000→5500 rpm + "overspeed disables the wheel; desat is triggered by the momentum monitor", load-shed "red <40 % SoC"→ bus-voltage 26.5 V behaviour, "single-head star tracker"→ redundant ST1/ST2, S19 actions are single parameter-less functions, image size 4 MB↔800 MB (until A- or C-side fix), pus_services.yaml declares all 14 services.

### C. Workbook exercises currently un-runnable as written

| Exercise | Why broken | Fix path |
|---|---|---|
| NOM-004/NOM-005 orbit-maintenance, CTG-006 | No propulsion (`PROP_ARM` nonexistent) | A4 or delete |
| Ch 13.3 / p. 371 eclipse-exit SoC | 70 W premise false | B1 (recompute at 36 W → ~18 % drop, not ~34 %) |
| Ch 13.7 wheel-desat scheduling | 5000 rpm/8 mN·m·s premise; sim = 5500 rpm & disable-on-overspeed | B3 or A-side alignment |
| Ch 8 MPS exercise (plan → upload → pass execution) | Wrong URL; imaging activity performs zero payload actions; schedule time discarded; S11 unloadable; MCS shows no schedule | A3 |
| "4 MB image → 32 s at 1 Mbps" rule of thumb (p. 360) | Image size now IS 4 MB (defect #82); only the rate differs — **262 s of channel at the sim's 128 kbps HIGH tier**, 328 s with the planner's protocol allowance | B3 (restate the rate; the size is correct) |
| COM-006 link baseline capture (14.5/6.8 dB) | Book pair is internally inconsistent; sim now computes an S-band budget with per-station G/T and prints a per-tier baseline table | B1 (reprint the card's baselines from `tools/link_budget_ladder.py`) |
| COM-010 "wait 60–90 min" cooler step | τ = 100 s | B1 |
| Any Troll-pass activity (incl. `data_dump` at Troll) | Iqaluit-only contact gating | A2 |
| Ch 12/15 staged load-shed contingency drills (SoC 30/20/10 stages) | Stage path dead code (`engine.py:618`) | A5 |

---

*End of report. Prepared 2026-07-28 from the mounted working copy; all sim citations re-verified against code/config this date.*

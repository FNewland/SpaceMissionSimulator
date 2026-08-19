# Position-needs research + implementation — delivery notes, 2026-08-18

Research: `POSITION_NEEDS.md` (per-position task analysis, externally sourced —
ESA/ESOC role definitions, NASA flight-controller discipline framing,
ECSS-E-ST-70-11 operability clauses, voice-loops literature, university-cubesat
ops practice — grounded in the actual tree). Suite after implementation:
**6981 passed, 0 failed**.

## The three reported defects

**PUS services "not available" on the modern MCS.** The panels existed but were
empty and undiscoverable — no JS errors, which is why it shipped. The rail said
"DIA", so nobody coming from the classic "PUS SERVICES" tab could find it; and
five renderers read response fields the API never serves (`ea_rules` vs the
served `rules`, flat FDIR keys vs the nested `fdir.state`, `param_count` vs
`parameters[]`, `exec_time_cuc` vs `time_utc` …), so S19 showed no rules, the
FDIR strip showed all dashes, S3 showed 0 params, S11 rows rendered blank. All
remapped and verified live: 32 S19 rules render, S12 shows names/values/status,
the rail now says **PUS**.

**Command entry "really hard".** Confirmed, and worse than ergonomics: every
argument was a bare text box (no units, no enum names, no live hex, manual
pack), and — a genuine safety regression — the criticality check read
`cmd.critical`, a field that does not exist (the server serves
`criticality: "critical"`), so **arm→execute was never enforced**;
`SYS_SET_MISSION_PHASE` was sendable un-armed. Now: schema-driven widgets
(enum dropdowns with names, per-bit checkboxes for bitfields, engineering-unit
number fields with ranges — you type −15 °C and the encoder produces
`24 64 D7`), live hex + length on every keystroke, paste-hex-to-decode back
into fields, validation with the rejection reason, defaults from the
catalogue, arm→execute enforced again, quick-commands carrying their preset
args (Detumble was silently sending `mode=4`).

**The radio page.** You were right. The page mixed a real ground-station rack
(lock ladder, measured Eb/N0/BER, spectrum, constellation, eye — all kept)
with the channel model (Eb/N0 knob, fading, Doppler ramp, interferers, the
truth panel — all simulation ground truth). The operator page is now purely
observational and restyled into the design system; the channel model lives at
**`/sim`** on the rfsim server behind the INSTRUCTOR accent strip, driving the
identical endpoints, with the old page frozen at `/classic` under the same
`SMO_UI` switch as everything else. An injected impairment still shows its
*effects* on the operator page without revealing its *cause* — which is the
whole diagnostic exercise.

## The research (POSITION_NEEDS.md)

Per position — the seven console positions plus SPACON, FDS, Ground Segment,
Mission Manager, and the sim team — it gives: the "what am I protecting"
statement, the questions they must answer at perceive/comprehend/project
level, which telemetry answers each (with real hex ids), the commands they
legitimately own, and the pre-pass/in-pass/post-pass rhythm. Then a per-tool
matrix (serves-well / serves-poorly / gap / misplaced) and a ranked gap list.

The headline finding: **the systematic deficit was projection (Endsley L3)**.
The ground computed exactly three derived parameters, so every "how long
until…" question — SoC at eclipse exit, momentum to the desat threshold,
buffer-to-full, will-the-data-fit-this-pass — was mental arithmetic over data
the servers already held.

## What was implemented from the gap list (all 15)

- **G1 Derived-parameter block v2** — eight new ground-derived parameters
  (0x0803-0x080A): SoC-at-eclipse-exit, time-to-SoC-yellow,
  time-to-70%-momentum, time-to-buffer-full, pass data capacity, catbed-ready
  ETA, **pass-fit ratio**, ground/OBC time offset. Computed by one projection
  helper (archive slope + limits + the ground's own orbit propagation), never
  fabricated: no data ⇒ an explicit INVALID state with the reason, and the
  rendering never shows a number in that case. Displayed on the owning L2
  pages and the SA header. Real-mode-safe by construction (received TM +
  ground orbit knowledge only).
- **G2** The two most decision-critical bitmasks — `aocs.fine_ready` 0x02C5
  and `prop.arm_inhibit_mask` 0x0B15 — now render as labelled per-bit
  pass/fail chips, legends parsed from the parameter catalogue.
- **G3** "PASS FIT 0.6 — fits / 1.8 — will NOT fit" on the TTC and PAYLOAD
  pages, per-pass fit verdicts and per-dump achievable-vs-backlog in the
  planner.
- **G4** Capture-readiness GO/NO-GO card on PAYLOAD: the five NOM-002 gates as
  chips + a composite verdict; the gate table is instructor-tunable config.
- **G5** Predicted-vs-measured link overlay on TTC (the link-budget chain is
  now an MCS endpoint), with a predicted-now vs measured-now margin readout.
- **G6** First-out alarm grouping: ≥4 alarms sharing ancestry in
  `fault_propagation.yaml` collapse to "ROOT + N consequent (expand)",
  labelled as ground-inferred.
- **G7** GO/NO-GO polls show the call-matrix row (who calls / who must respond
  / FD note) beside the buttons; the matrix moved from the manual into config.
- **G8** A console/mission log: append-only, sim-time-stamped, position-
  attributed; auto-entries for every TC + its S1 verdict, alarm acks, GO/NO-GO
  polls and results; free-text notes and MARK; a LOG tab in the bottom
  drawer; and a Console Log section in the pass report — the missing decision
  record.
- **G9** Loaded-vs-planned burn card on PROP: TM burn parameters against the
  planner's manoeuvre activity, mismatch flagged "do NOT arm until FDS
  confirms".
- **G10** Authority hygiene: ~10 orphaned func_ids assigned to owning
  positions (with a reasoned choice for each — e.g. the UHF beacon TX to
  fdir_systems, not TT&C, because killing the last independent downlink is a
  platform-survival call), and a contract test that every catalogued func is
  owned by a position or on an explicit FD-only allowlist with a reason.
- **G11** The delayed-TM viewer now runs played-back dumps through the ground
  limits: time-in-yellow/red badges per parameter, band shading on the plots,
  a per-dump summary.
- **G12** Blind-TC mode: an armed TT&C toggle that tags subsequent commands
  BLIND until a TM readback confirms, logged to the console log.
- **G13** The radio split (above).
- **G14** Per-pass RF rollups from rfsim (predicted AOS vs achieved lock, mean
  Eb/N0, FER, volume) served on an endpoint, shown in the planner and the
  wall display, and a plan-vs-achieved line in the pass report.
- **G15** Baseline-vs-now health card on TRENDS (battery, SA degradation, leak
  rate) with >5 % drift flagged — the NOM-011 view, precomputed.

## Follow-ups the research flagged but this batch did not build

- **Student-action attribution to the instructor**: position gating happens in
  the MCS and never reaches the simulator, so the instructor's event log
  cannot say *which position* sent a command. The new console log closes this
  on the ground side; piping it (or the position tag) into the instructor
  debrief would make the debrief a grading artefact. Needs a small
  simulator-side addition — your call.
- **Scenario success criteria** in the scenario YAML for auto-scoring debriefs.
- The room roles' remaining products: the FDS versioned orbit-solution card
  and the Mission-Manager weekly rollup (G14's report line is the first step).

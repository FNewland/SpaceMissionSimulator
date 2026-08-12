# Coherence review — 2026-08-12 issue batch

Eight parallel audits produced ~140 findings. This document is the step Franz asked
for explicitly: **review all the fixes together before applying any of them**, so that
independently-correct changes do not fight each other. It records the interactions,
the ordering they force, and the decisions taken.

Sources: the eight agent reports (power/EPS, AOCS, payload, TT&C, FDIR procedures,
MCS displays, IMU, textbook), plus a **live instrumented run** of the shipped
simulator (`tools/live_power_monitor.py`).

---

## 0. What the live run actually showed

Real services, real telecommands over the framed TC socket, real telemetry from
`/api/state`. Spacecraft commissioned to a full nominal configuration: application
booted, both wings deployed, ttc_tx / payload / fpa_cooler / htr_bat / htr_obc /
aocs_wheels lines up, PA on, antenna deployed, LOW rate, star tracker + 3 gyros +
4 wheels enabled, MTQ enabled, AOCS mode NOMINAL, payload STANDBY. 60× for ~7 orbits.

| condition | generation | load | net |
|---|---|---|---|
| NOMINAL (nadir), sunlit | **85 W** | 37–43 W | +45 W |
| COARSE_SUN (sun-pointing), sunlit | **108–123 W** | 30–40 W | +80 W |
| eclipse (any mode) | **0 W** | 30–42 W | −30 to −42 W |
| **wings stowed**, sunlit | **18.6 W** | 37–43 W | **−20 W** |
| mode-change slew transient | collapses to **22 W** for minutes | 41 W | −19 W |

Four conclusions that reframe the whole batch:

1. **The power chain is not mis-sized in the way it looks.** With wings deployed and
   attitude held, generation comfortably exceeds load. Franz's symptom is real but it
   is produced by *three specific operational states*, not by a sizing error.
2. **Wings stowed is power-negative by construction, and completely invisible.** Wing
   status (0x0144) lives in HK SID 8, which is not enabled by default. An operator can
   run an entire session at 18 W generation with nothing on any display saying why.
   This is finding #1 for Franz's reported symptom.
3. **"Generation drops off way too fast" is attitude, not ageing.** No decay term in
   the EPS model is capable of it. It is the SADA tracking law behaving correctly on an
   attitude it was not sized for — and the attitude keeps being lost (see 4).
4. **The spacecraft does not stay in NOMINAL.** Live, the mode went 4 (NOMINAL) → 8
   (ECLIPSE) → 3 (COARSE_SUN) and **never returned to 4** for the remaining six orbits.
   Nadir pointing was silently abandoned. That is an AOCS mode-management defect that
   masquerades as a power problem — and it is why the observed generation swings
   between 85 W and 122 W.

The corollary matters for the fix plan: **anything that reduces generation or raises
load must be judged against the NOMINAL 85 W case, not the COARSE_SUN 122 W case**,
because sun-pointing is not a state the mission is allowed to live in.

---

## 1. Franz's rulings on this batch

| # | Question | Ruling |
|---|---|---|
| R1 | eps.yaml wattages detuned below manual/brief; restoring them breaks the budget | **Neither.** "A CubeSat would generally not have a 20 W transmitter and would struggle to generate 114 W peak — research CubeSat power budgets and size accordingly." → the *documentation* is the thing that is wrong. Re-derive the whole sheet from real 6U flight hardware. |
| R2 | Beacon TM survives PA-off | **Implement the separate UHF beacon transmitter** the textbook describes. |
| R3 | FINE requires 4 wheels | **Make FINE work on 3**, as a pyramid should. Plus: review the AOCS design as a whole — B-dot/detumble, inertial, nadir, target and desat modes — for realism. |
| R4 | 21 inert procedures | **Re-tag all 21 + validation gate, AND make the contingency cards runnable from the MCS** (which requires a real branching step format first). |

Carried forward from previous sessions and still binding: propulsion is confirmed
(CAM + limited orbit maintenance, not deorbit); autonomy is "fix everything, quick
wins first"; AOCS fidelity is "best realism that still runs real-time, accelerates
and breakpoints".

---

## 2. Interaction map — where fixes collide

### 2.1 The power interactions (the tightest knot)

Five findings all move the same two numbers, and three of them move them in
*opposite* directions. Applying any subset in isolation gives a wrong answer.

| finding | effect on generation | effect on load |
|---|---|---|
| P-a Re-derive wattages from real 6U hardware (R1) | — | **changes materially, direction TBD by research** |
| P-b SADA device gate zeroes body panels too (`eps_basic.py:570`) | +up to 122 W in the failure case | — |
| P-c Stale sun vector when AOCS not ticked (`engine.py:1870`) | removes a *false* high reading | — |
| P-d Budget tool's sun vector is in the pre-Phase-C frame | tool over-reads by **28 %** | — |
| P-e Auto-TX hold (300 s per accepted TC) | — | **+8 to +13 W orbit-average** the tool never sees |

**Ordering forced:** P-d must be fixed **first**. Until the budget tool and the
simulator agree, every sizing decision made from the tool is 28 % optimistic and the
β-angle trend is inverted (the tool currently calls the *best* epoch the worst). The
corrected tool lands within 3 % of the live simulator, so it becomes a trustworthy
proxy — and only then can P-a's re-sizing be evaluated without a 20-minute sim run
per candidate.

**Ordering forced:** P-a must be decided **after** the AOCS mode fix (A-a below).
Sizing the array against a spacecraft that spends its life sun-pointing at 122 W
would under-size it by 40 %.

### 2.2 AOCS ↔ power ↔ payload

- **A-a (mode management: NOMINAL is not held)** is the single highest-leverage fix in
  the batch. It changes the measured generation baseline (85 vs 122 W), which changes
  P-a's sizing target, which changes whether the imaging load closes.
- **A-b (desat gain 100–200× too high — DESAT never completes and never exits)**
  interacts with A-a: a spacecraft parked in DESAT is not in NOMINAL either. Some of
  the observed mode loss may be this. Fix A-b before re-measuring A-a.
- **A-c (desat pre-empts FINE mid-imaging, and returns to NOMINAL not FINE)** interacts
  with the payload capture gate, which demands *exactly* mode 5. Fixing A-c without
  the payload gate leaves captures failing; fixing the payload gate without A-c leaves
  strips truncated. **Do both or neither.**
- **A-d (FINE on 3 wheels, R3)** touches the same precondition bitmask 0x02C5 that
  A-c's inhibit logic and the payload gate both read. One owner for that mask.
- **FDIR safe-mode procedures command DETUMBLE (0x02) not COARSE_SUN (0x03)** — this
  is an FDIR-config fix that lands squarely in AOCS behaviour, and it is a *cause* of
  A-a. It must be applied by the AOCS owner, not the FDIR owner, or the two will
  disagree about what safe mode means.
- **`aocs_wheel_failure_dual` cascade permanently kills the ADCS bus** if wheels are
  enabled after the line is energised — an ordering trap during commissioning that
  leaves AOCS latched OFF forever, tumbling, at 22 W generation. Same owner.

### 2.3 TT&C ↔ power ↔ FDIR ↔ procedures

- **T-a (nothing gates TM on PA state)** and **R2 (separate UHF beacon)** are one
  change, not two: the correct design is a `tx_active` parameter that gates the
  S-band downlink, plus an independent `beacon_tx` that gates **only SID 11**. Doing
  T-a alone would silence the beacon, which R2 says must survive.
- **T-b (EIRP is a constant, so `TTC_SET_TX_POWER` is inert)** interacts with P-a:
  once the PA wattage is re-derived, the EIRP must be *derived from it* or the link
  budget and the power budget will describe different radios.
- **T-c (`ttc_emergency_shutdown` sends func 66 = PA **ON** where 67 = PA OFF is
  meant)** — the FDIR level-3 recovery currently turns the transmitter on. Harmless
  today because PA state does nothing; **becomes a real fault the moment T-a lands.**
  This is the clearest example of why the coherence pass was needed: two individually
  minor findings compose into a new critical defect.
- **COM-006** needs the antenna-deploy, beacon-off and downlink-mode-off steps (Franz's
  item). Those steps only *matter* once T-a/R2 make the gates real. Procedure edits
  must therefore land **after** the TT&C model change, and be written against the new
  behaviour.

### 2.4 Payload ↔ TCS ↔ EPS

- **PL-a (three of four cooler command paths never energise the EPS line)** is why
  "FPA does not get to YES". The fix couples payload/TCS commands to an EPS line —
  i.e. it is simultaneously a payload, a TCS and an EPS change. One owner.
- **PL-b (IMAGING streams 10 MB/s of un-catalogued data)** fills the 20 GB store in
  33 minutes with zero captures, and directly contradicts the teaching note in NOM-002.
  It also destroys the pass-planning arithmetic the link-rate ladder work was built on.
- **PL-c (no image bytes exist; S13 fabricates blocks)** — "where do the image packets
  go?" The answer is nowhere. Fixing S13 to read the real catalogue depends on PL-b
  first, or the catalogue is drowned in phantom volume.
- **PL-d (calibration gated on IMAGING)** — Franz's item. Adding a CALIBRATION mode
  changes the payload mode enum, which the EPS billing table, the MCS payload panel,
  the textbook mode table and `PAYLOAD_SET_MODE`'s range all read. Fan-out is wide but
  shallow.

### 2.5 Displays ↔ everything

Every model change above adds or changes telemetry. The MCS is one 10 000-line
`index.html`; three separate owners editing it in parallel is the one collision the
last build actually hit. **Decision: a single MCS owner** takes every display change
in this batch, working from a written requirements list produced by the model owners.

The staleness fix (cadence × 1.5, Franz's item) is a *prerequisite* for several of
the new displays: PROP is SID 9 at 8 s and TCS is SID 3 at 60 s, and both would be
born wearing a permanent STALE badge under the current hard-coded 8 s / 15 s rules.

### 2.6 The IMU question — resolved without a change of direction

Franz asked whether to delete the IMU from the textbook or add it to the simulator.
**Neither.** The simulator already has a full gyro model (ARW, bias random walk,
temperature, failure injection) that the 6-state MEKF *depends on* — remove it and the
estimator stops working. The flight manual already lists three fibre-optic gyros as
baseline hardware. What is missing is three lines of wiring that make the existing
device commands look inert:
`0x025D aocs.gyro_enabled` is hard-wired to "AOCS mode ≠ 0" and ignores the device
state, and `_gyro_measurement()` never consults `device_states`, so switching the
gyros off changes nothing. Fix the wiring; keep the IMU in the book; correct ~10
textbook lines that cite func IDs and power lines that never existed.

This is a case where the reported symptom ("no commands for one") was accurate and the
inferred cause ("it isn't implemented") was not.

### 2.7 Procedures ↔ FDIR ↔ everything

- The 21 inert procedures are inert because of a **tag mismatch**, not missing content:
  their commanding is written as `**Action:**` prose, which the extractor drops. All 68
  command references in them are real and catalogued.
- The genuinely dangerous adjacent defect: `ProcedureExecutor` reports
  `PROCEDURE_COMPLETED` for a procedure with zero executable steps (`all([]) is True`),
  and silently skips steps naming an unregistered command while still marking them
  executed. No inert procedure is currently *reachable* by autonomous FDIR — but R4
  asks to make contingency cards runnable, **which is exactly the change that would
  make it reachable.** The executor fix is therefore a hard prerequisite for R4, not a
  nice-to-have.
- Procedure text must be edited **last** in each subsystem, because ~30 of the card
  corrections are corrections *to the new behaviour* (COM-006's beacon step, COM-009's
  cooler path, NOM-007's desat mode, LEOP-006 step 2).

---

## 3. Ordering

```
Phase 0  budget-tool frame fix (P-d)              → sizing becomes measurable
Phase 1  AOCS: modes, desat, 3-wheel FINE,        → generation baseline becomes real
         FDIR safe-mode target, wheel cascade,
         gyro device wiring
Phase 2  EPS: re-derive the hardware sheet from   → depends on Phase 0 + 1
         real 6U flight parts; generation-path
         bugs; battery SoC override
Phase 3  TT&C: tx_active gate + separate UHF      → independent of 1/2, but the
         beacon; EIRP from PA power                  PA wattage comes from Phase 2
Phase 4  Payload: CALIBRATION mode, cooler line
         coupling, image data path, IMAGING fix
Phase 5  Procedures + FDIR: executor fix, branching
         step format, re-tag 21, validation gates,
         card corrections against 1–4
Phase 6  MCS: staleness by cadence, PROP display,
         S12 dump decode, plus every display
         requirement raised by 1–4
Phase 7  Textbook: chapter 14 + Part 2, against the
         post-fix behaviour
```

Phases 3 and 4 are independent of each other and can run in parallel. Phase 5 and 7
must not start until the model behaviour they document is settled.

---

## 4. Conflicts explicitly resolved

| conflict | resolution |
|---|---|
| Three agents want to edit `smo-mcs/static/index.html` | Single MCS owner; model owners submit written display requirements. |
| AOCS owner and FDIR owner both want `configs/eosat1/fdir/procedures/*.yaml` | AOCS owner takes the three AOCS-behaviour files (safe_mode_entry, emergency_power_down, aocs_reset); FDIR owner takes the rest. |
| Payload, TCS and EPS all touch the FPA cooler path | Payload owner takes the whole cooler chain including the EPS line coupling and the TCS command handler. |
| `0x02C5` FINE precondition mask read by AOCS, payload and MCS | AOCS owns the mask definition; payload and MCS read it. |
| Budget tool vs live sim disagree by 28 % | Tool is wrong; fix the tool, keep the sim. |
| Textbook says separate UHF beacon; `ttc.yaml` says one radio | R2: implement the beacon; `ttc.yaml` is amended, textbook stands. |
| Textbook says IMU absent from sim | Both partly wrong; gyro exists, wiring is dead. Fix wiring, correct 10 book lines. |

---

## 5. Things deliberately NOT changed in this batch

- **The array is not being enlarged to chase the imaging case** until the R1 research
  says what a real 6U EO CubeSat generates. The previous session's mistake was writing
  an impossible 0.90 m² array onto an 11 kg 6U; that must not recur.
- **`displays.yaml` stays dead for now.** Making the UI config-driven is the design
  agent's Phase 7 recommendation and Franz has asked for interface work to wait until
  this version is stable.
- **The S19 rule content ruling** (13 misrouted funcs) still needs Franz per-rule; the
  strict-xfails and the 2026-10 deadline stay.
- **The interface redesign.** Researched and mocked up (`/root/design/`), not applied,
  per Franz's instruction.

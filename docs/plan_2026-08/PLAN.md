# EO-Sat-1 SMO Suite — Work Plan for 2026-08-01

**Prepared** 2026-07-31 · **For** Fable (primary), with delegation notes for other models
**Basis** eight parallel deep-dive audits of `~/SpaceMissionSimulation` and `~/SpaceCDF`, briefs in `briefs/01`–`briefs/08`
**Policy set by Franz** — *fix everything, quick wins first*; decide contested interfaces *on engineering merit, researched*; ADCS fidelity = *best realism that still runs real-time, accelerates, and breakpoints*.

---

## 0. Read this first

Three things changed the shape of this plan versus what was asked for.

**(1) The power deficit is not a sizing problem.** It is four model defects stacked on top of each other, and the previous "fix" made the spacecraft physically impossible without closing the budget. Generation uses a beginning-of-life constant with no packing, temperature, harness or EOL losses — 389.5 W/m² where honest physics gives 267.9, a **1.45× overstatement**. The deployable wings are mounted on the one body axis that the implemented sun-vector model sweeps through, so 0.66 m² of cells does the work of 0.42 m². `solar_array_drive_angle` — the lever that fixes that — exists as a command, a device gate and a telemetry parameter and **is read by nothing**. And the FDIR bus-voltage rule trips at **58 % SoC**, inside the nominal band, ahead of every gentler protection. That last one is almost certainly what Franz is actually watching happen. Resizing the array alone would have papered over all four.

**(2) Fine pointing is inert because there is no attitude dynamics at all.** Not a broken mode — an absent subsystem. There is no inertia tensor anywhere in the repo, no torque integration, no sensor that reads anything but truth, no estimator, and no INERTIAL mode. `FINE_POINT` is reachable only by ground command, has no exit branch, and converges to ~0.0005° because nothing stops it. This is the largest single package in the plan and it gates propulsion.

**(3) Two workstreams are coupled to power and must land together.** Thermal bills EPS a phantom 6 W for a battery heater whose thermostat can never fire (9 °C of margin to its trip point), while up to 60 W of payload + cooler runs **off the books** if the operator uses the payload commands — which is exactly what the shipped procedures instruct. Fixing wattages without fixing that coupling would produce a budget that is wrong in a new direction.

**Sequencing consequence:** the foundation packages (determinism, sub-stepping, state round-trip) are not housekeeping. Time acceleration currently changes the physics, nothing is seeded, and a breakpoint load destroys the on-board S11 schedule. Every physics change below is unverifiable until those three are true. They come first.

---

## 1. The gate: commit and branch before anything

Roadmap item N-3 has been open across four sessions. There are ~90 defects' worth of uncommitted working-tree edits. **Nothing in this plan should start until that tree is committed, branched and tagged**, because otherwise the first failed experiment cannot be backed out and there is no bisectable history for a suite of 3,900 tests.

```
git checkout -b evolution/2026-08
git add -A && git commit   # grouped by defect batch: 41-52, 53-83, 84-86, 87-90
git tag baseline/2026-07-31   # record: 3922 passed, 7 skipped, 3 xfailed, 0 failed
```

Then the root consolidation (brief 08, WP-0): 10 loose `.md` + 3 `.txt` + 5 `.docx` in the repo root, several describing an FDIR that does not run. Move dated artefacts to `docs/archive/YYYY-MM/`, delete `r24.txt`/`r27.txt`/`.DS_Store`/`__pycache__`, add a real root `README.md`.

**Effort S. Do it in the first thirty minutes.**

---

## 2. Phase plan

Phases are dependency-ordered, not time-boxed. Each is independently shippable and leaves the suite green.

### Phase A — Foundation (must precede all physics work)

| # | Package | Why it blocks | Effort | Brief |
|---|---|---|---|---|
| A-1 | **Commit + branch + tag + root cleanup** | no rollback without it | S | 08 WP-0 |
| A-2 | **Seeded RNG, per-engine `random.Random`, seed in config and in breakpoints** | `seed(` has **zero hits** in `packages/`. Separation tumble, star counts, CSS, magnetometer, gyro bias and failure flicker all draw from global `random`. No exercise is repeatable and no worked example is checkable. N-5's `t0_epoch` fix did *not* deliver reproducibility. | M | 08 B-1, 02 WP-0 |
| A-3 | **Sub-step the tick; fix √dt noise scaling** | `dt_sim = dt_real × speed` goes straight into the models. At ×60 the battery, thermal RC, heater hysteresis and wheel dynamics integrate 60-second Euler steps and give different answers. Detumble **diverges to 1.4e39** at dt=1000. Process noise scales linearly in dt instead of √dt, so random-walk variance is off by a factor of `speed`. "Speed up to reach the eclipse" is the normal student workflow. | M | 08 B-3, 02 WP-1 |
| A-4 | **State round-trip protocol + property test** | `breakpoints.py` touches ~12 of 93 engine attributes. **A breakpoint load destroys the entire S11 time-tagged schedule** (`TCScheduler` has no `get_state`/`set_state`) — the exact workflow defects #44–#47 were fixed to enable. Also lost: `_spacecraft_phase` (so the console says bootloader while application commands are accepted), injected failures, running scenarios, S12 enable flags and delta baselines, in-flight S13 transfers, load-shed stage, and every `_prev_*` edge detector (so the first tick after a load re-fires spurious AOS, eclipse-entry and mode-change events). | M | 08 B-2 |

Acceptance for the phase: same seed + same `t0_epoch` + same command script ⇒ byte-identical 1000-tick parameter trace; ×1 and ×10 agree on end-of-orbit SoC, eclipse entry time and total heater energy within a stated tolerance; save → perturb → load deep-equals on a canonical state dump, and a component without state methods **fails the test** rather than silently losing state.

### Phase B — Power and thermal (land together)

These two must be worked as one package because the coupling defect sits between them.

**B-1 · Honest array performance** — replace the bare `cell_eff × MPPT` with a full loss stack: inherent degradation/packing 0.85, temperature coefficient −0.20 %/°C at 65 °C, harness 0.95, EOL 3-year 0.92. `K: 389.5 → 267.9 W/m²`. *(brief 01 F1, WP-1)*

**B-2 · Make the solar array drive load-bearing** — the recommended architecture, and the cheapest fix available because the command, the device gate 0x0101 and the telemetry parameter already exist. Single-axis drive about the orbit normal converts the wings' `cos β · |sin φ|` duty into continuous `cos β`: **1.56× more energy from the same area**, and the run-to-run alignment spread collapses from 8 W to 1 W. It also creates a genuine new failure mode (SADA stuck) that the existing device gate already supports. *(01 F4/F5, WP-2)*

**B-3 · Rebalance the hardware sheet to a credible 6U** — wings **0.300 → 0.150 m² each** (the current value is ten 3U segments folded against a 300×100 face, on an 11 kg spacecraft; commercial 6U deployables top out at 0.24–0.30 m² *total*). Body areas 0.30 → ~0.20 m² (a box has only one pair of 300×200 faces; the current config declares two). Battery 120 → 100 Wh. OBC 15 → 6 W, RX 5 → 1.5 W. *(01 F12/F13, WP-3)*

**B-4 · Un-invert the protection ladder** — `fdir.yaml` bus rule 26.0 → 24.5 V, AOCS rule 24.0 → 23.2 V. Resulting monotonic ladder: shed 45.5 % → EPS-safe 39 % → SoC stages 30/20/10 % → AOCS-safe 22 %. Lock it with a test that converts every threshold to SoC through the configured OCV curve and asserts strict monotonicity. **This is the highest-probability cause of the symptom Franz reported.** *(01 F9, WP-6)*

**B-5 · Battery electrical model** — the terminal-voltage sign is inverted (`v = ocv − i·R` with `i > 0` on charge), so the bus sags when charging and rises when discharging; every voltage protection fires ~1.3 SoC points late. Add coulombic efficiency 0.94 and a CV taper. *(01 F8/F19, WP-4)*

**B-6 · Single FPA thermal node** — `tcs_basic.py:492` uses the −15 °C setpoint as an **additive environment offset** (`target − 20`), so TCS asymptotes to ambient −35 K, never to −15 °C. Two independent integrators with two unlinked cooler flags. In the case the shipped `COM-010` procedure actually walks students through, the two curves sit **26.4 °C apart** — and that procedure's own Step 1 asserts they should agree. TCS owns the node; payload reads it; keep the legitimate ≤3 K detector offset that `payload_basic.py:517` already models, and *name* it honestly. *(05 Defect 1, WP-1/WP-2)*

**B-7 · Make the battery heater reachable** — thermostat trips at +1 °C; the battery node's coldest achievable temperature across 12 h in any modelled orbit is **+9.2 °C**. Margin to trip: 9 °C, so it can never fire. Compounding it, `HEATER_BATTERY` returns success and is silently reverted on the next tick if the EPS line is off. Reconcile setpoints (three different answers across config, manual and procedures), lower the eclipse internal sink, and make the command **fail loudly** when it cannot take effect. *(05 Defect 2, WP-3/WP-4)*

**B-8 · Bill EPS from actuator state, not line-switch position** — the coupling fix. Today: 6 W phantom battery-heater bill (**9.45 Wh/orbit**, between 0.5× and 4.7× the entire claimed margin, for a heater that cannot energise) *and* up to 60 W of payload + cooler completely unbilled when the operator uses `PL_SET_MODE` rather than the EPS commands. Also add tier-dependent TX power (HIGH-rate dumps currently cost the same as LOW, which directly defeats the "too many dumps drains the battery" lesson). *(05 Heater-vs-EPS, 01 WP-5/WP-12)*

**B-9 · Fix the false array-degraded alarm** — event 0x010B fires on the first sunlit tick of *every* run and latches, because the expected-power reference uses the dead legacy `arrays:` key (0.628 m²) against a model that produces 102–129 W. A genuine array failure afterwards produces no event. *(01 F11, WP-10)*

**Predicted outcome** (36 h runs, real propagator, worst β): nominal ops per-orbit min SoC **85 %**, margin ratio 1.30 at worst β / 1.60 at best, ECSS margin +30 % at EOL. Payload left on through one eclipse costs 46 % DoD and crosses the shed band on the second consecutive eclipse; dumping on every eligible pass erodes the margin visibly over 6–8 orbits. **The teaching tension is preserved and sharpened** — the old configuration failed in the opposite direction, tripping FDIR at 58 % during *correct* operations so students could not tell good planning from bad.

Additional thermal work in the same phase, ranked: panel solar gain is **~80× too large** (equilibrium +Y panel reaches 362 °C at full wing current, and there is no monitor on it); the S12 monitor "TCS FPA Temperature Low" has **inverted limits** (`low: -15, high: -20` → empty admissible set) so it fires at t=0 every session and autonomously switches the payload off via S19 rule 3001; beta-angle dependence is inverted (maximum external temperature at the maximum-eclipse geometry). *(05 findings 1, 2, 8)*

### Phase C — AOCS (the big one)

Build order, from brief 02. Each is individually shippable.

| WP | Package | Effort |
|---|---|---|
| C-0 | Determinism & breakpoint completeness for AOCS state (folds into A-2/A-4) | S |
| C-1 | Fixed-step sub-stepping + **secular fallback** above 60× | M |
| C-2 | Rigid-body dynamics + wheel/MTQ actuator physics — inertia tensor, torque limits, friction, dipole | L |
| C-3 | Attitude-dependent sensor models — star tracker, sun sensors, magnetometer, gyro with bias/noise/misalignment/FOV | L |
| C-4 | **6-state MEKF** | L |
| C-5 | Quaternion-feedback PD with feed-forward; LVLH/inertial reference generator; **new mode 9 = INERTIAL** | M |
| C-6 | COARSE vs FINE differentiation + payload gating | M |
| C-7 | Disturbance torques — gravity gradient, aero, SRP, residual dipole | S |
| C-8 | Telemetry block 0x02A0–0x02C7, HK SID 7, MCS display | M |
| C-9 | Fix momentum limits and the dead autonomy | S |
| C-10 | Test suite | M |
| C-11 | Documentation reconciliation | M |

**The real-time / acceleration / breakpoint constraint is met explicitly**, and this is the part worth checking carefully. Truth dynamics: RK4 on the 7-state at **fixed h = 0.5 s**, wheels integrated analytically to remove the stiff motor pole, quaternion renormalised each step. Sub-stepping `N = ceil(dt_sim/0.5)` capped at 120 gives **full-fidelity attitude physics to 60× acceleration** at ~7 ms per wall tick — under 1 % of budget. Stability margin at h = 0.5 s is 113×. Above 60×, **secular mode**: the fast loop is bypassed, `q_true` is set to the reference plus a deterministic error drawn from the mode's own closed-loop error distribution (σ from the same gains and noise parameters, so it is consistent rather than invented), and the slow states — wheel momentum from ∫τ dt, gyro bias random walk with √dt scaling, MEKF covariance to steady state — integrate exactly over the full step. An S5 event and a downlinked `aocs.fidelity_mode` flag make the bypass **honest and visible**. A 1000× run is then a declared fast-forward, not a silent lie. Breakpoint cost: ~60 added floats, all inside the `AOCSState` dataclass that `get_state()` already serialises wholesale.

**COARSE vs FINE becomes physically real and operationally visible:**

| | COARSE_SUN | NOMINAL (nadir) | FINE_POINT |
|---|---|---|---|
| Sensors in loop | CSS + mag + gyro, TRIAD @1 Hz | ST @1 Hz + gyro, MEKF | **ST @4 Hz, both heads**, MEKF |
| Actuators | magnetorquers only | 3 wheels, ωₙ 0.020 rad/s | **4 wheels**, ωₙ 0.050, deadband 0.005° |
| Total pointing (3σ) | ~5° | 0.065° | **0.013°** |
| Geolocation @450 km | 39 km | 510 m | **100 m** |
| Settling after 30° slew | — | 285 s to 1.0° | **165 s to gate** |
| AOCS line power | 4 W | 9 W | **15 W** |

FINE entry gets a **precondition bitmask (0x02C5)** — ST tracking ≥5 stars, all four wheels active, MEKF converged and sustained 30 s, |ω| < 0.01 °/s, momentum < 70 % of envelope, slew idle ≥60 s — and `AOCS_SET_MODE(5)` is **refused with TM(1,8)** when unmet, instead of today's silent accept-then-drop-back. The payload then **refuses to capture** unless FINE is held with pointing error < 0.05°, and flags a strip PARTIAL with the actual error stamped into the catalogue entry if FINE is lost mid-acquisition. That is the chain that makes fine pointing mean something.

**One thing that must not be silently changed:** the semantics of `att_error` (0x0217). It is referenced by ops cards, S12 monitors, S19 rules, procedures and the textbook. Brief 02 §Risks lists the affected IDs — read it before touching that parameter.

### Phase D — Orbit control / propulsion

Depends on C-2 (attitude during burn) and on the orbit propagator work.

**Recommended hardware, from the trade in brief 03:** a **1U green-monopropellant module** (LMP-103S/ADN, ρ 1240 kg/m³), blowdown-pressurised, single thruster on −X firing along +X body so that retrograde burns require a deliberate 180° yaw — the AOCS coupling is intentional and is the teaching point. Thrust **0.33 N BOL → 0.14 N EOL**, Isp **220 → 196 s** (208 s mean), 0.400 kg propellant in a 560 cm³ tank blowing down 22.0 → 9.33 bar, catalyst bed preheat 12 W for 20 min reusing the existing `HEATER_THRUSTER` func 42, wet mass 1.55 kg = 12.9 % of a 12 kg spacecraft. **Total ΔV capability 66.9 m/s.**

**The critical integration question** is not the thruster, it is that a ΔV must propagate to passes, eclipse, power and imaging opportunities or the feature is cosmetic. Brief 03 §"How a ΔV is applied to SGP4" solves this with a measured (not assumed) TLE re-fit and an `epoch_version` bump that invalidates the planner and MCS caches.

**Burn model:** finite burn, sub-stepped at 1 s while FIRING, with the engine **clamping speed to ≤10×** during a burn (event `PROP_SPEED_CLAMPED`) — otherwise at 1000× a single tick is longer than the whole burn and the "abort on attitude error" lesson is unobservable. The ΔV is applied as one impulse at the burn mid-point; finite-burn gravity loss for a 32 s burn is 0.005 %, so the extra fidelity would buy nothing and cost a TLE re-fit per second.

Interface: S8 funcs **85–95**, TM block **0x0B00**, events **0x0800**, new S12 monitors and S19 rules at `ea_id` 8001+, a new EPS `prop` power line placed last in the shed order (so shedding propulsion mid-burn triggers the abort), and `htr_thruster` made real in TCS — which retires half of defect #31 and makes func 42 load-bearing for the first time.

> **⚠ Flag for Franz.** The textbook audit's independent recommendation was the *opposite*: **don't** add propulsion. Its argument is that a 6U at 450 km decays inside the FCC 5-year rule (the book's own §3.4 says so), so the honest engineering answer for this mission class is *no propulsion*, and orbit maintenance should be taught as the decision not to carry it plus a paper ΔV exercise. I have planned the build because you asked for it explicitly and because the courseware already ships NOM-004, NOM-005, CTG-006, `PROP_ARM`, `PROP_ISO_CLOSE` and §16.8 against it — but the counter-argument is real and this is the one place where "research to decide on engineering merit" points away from the request. Worth two minutes of your time before Fable spends a phase on it. See `DECISIONS.md` §1.

### Phase E — Commanding: the data-byte problem

This is the workstream most likely to change how the tool *feels* to an operator, and the audit found the underlying defect is worse than a documentation mismatch.

**`PAYLOAD_CAPTURE` (func 28)** — catalogue says `scene_id uint16 + lines uint16` (4 bytes); the handler unpacks `>f lat, >f lon` (8 bytes); the planner's `activity_types.yaml` sends **no params at all**. So every planner-scheduled image is currently taken at 0°N 0°E. Verdict on engineering merit: **scene_id + lines wins** (catalogue, textbook and MCS agree; scene-addressed tasking is what a real EO payload takes, with geolocation resolved on the ground from the attitude and ephemeris). Change the handler.

**`TTC_SET_TX_POWER` (func 68)** — everything documents a 1-byte level; the handler reads float32 watts. Three FDIR procedures are therefore silently ignored. Verdict: **enumerated `level uint8` wins**, with a corrected watt table.

**The general fix** is the one Franz asked for and it is the same fix for reports (b), (c) and (d): telemetry has calibration; **commands have none**. The plan extends `TCFieldDef` in `tc_catalog.yaml` with per-argument type, offset, endianness, engineering unit, raw↔engineering conversion, valid range, default and enum labels, generates XTCE *from* it, and makes it the single source consumed by the MCS builder, quick commands, the procedure runner, OBCP, the planner and the S11 time-tagged builder — with **four mechanical drift gates** so the six consumers cannot diverge again (they have twice).

**FPA setpoint (report (d))** gets a signed encoding with an explicit unit and scale — **int16 centi-degC** — so a negative engineering value never requires the operator to compute two's complement by hand, and the builder shows the live raw hex as the engineering value is typed, plus reverse-decode when hex is pasted. Brief 04 §"FPA setpoint worked example" gives the exact bytes under both the current and recommended encodings.

Also in this phase: expose the payload settings that exist in the model but are unreachable by any command, and make gain / integration time / lines have **radiometric consequences** (SNR and image size) — without which four textbook exercises have no referent.

### Phase F — Tooling: report generator and config editor

**Report generator.** Two facts from brief 06 shape the whole design and are worth knowing before building: the S15 archive is the **complete tape** while the MCS live view is lossy (packets released while `downlink_active` is false are discarded), so *archive-minus-MCS* is a measurable "what the link cost you" number — genuinely the operationally interesting quantity, and a report section in its own right. And the dump **filename timestamp is wall-clock** while packet CUC is **sim time**; under acceleration these axes are unrelated, so all period selection must be CUC-based. Ships with three templates — Daily ops review, Pass report, Anomaly investigation — as a library in `smo-common` so both the standalone viewer and the MCS call the same code. Self-contained HTML primary, PDF as a build step.

**Config editor.** Hosted on the **simulator's instructor server (8080)**, not the MCS — 9090 is explicitly the students' port, and putting a config-write surface there would put it in front of every student. MCS and planner get read-only proxies so students can *browse* configuration and its history, which is itself worth teaching. Version control: a **shadow git repo** at `workspace/config-history/` with `configs/` as its work-tree — real diffs, history, tags and blame without touching the source repo or its dirty tree — with an automatic snapshot fallback when `git` is absent (the Windows lab installer doesn't ship it). Revisions carry machine-readable trailers (`SMO-Actor`, `SMO-Role`, `SMO-Reason`, `SMO-CrossCheck`, `SMO-RestartScope`) so the audit survives a bare clone. Named baselines are annotated tags; **per-exercise variants are branches** — an instructor preparing "the config set for the CERISE ADCS exercise" ahead of class and switching between sessions is the single most useful thing this feature buys. Validation runs schema → range/physical plausibility → **cross-file consistency** before save, with diff-preview-before-apply.

### Phase G — Everything else worth doing

Ranked from brief 08. These were not asked for; several are higher value per hour than parts of what was.

1. **`configs/eosat1/mcs/limits.yaml` has zero consumers.** 45 limit bands; `load_limits()` is called nowhere in production; `TMProcessor` is constructed without limits, so `_check_limit` returns immediately for every parameter and `pop_alarms()` has no callers. 29 of the 45 limited parameters have no S12 equivalent either, so they are unmonitored on **both** sides. Ground limit checking is the second-most-used feature of a real control room and it is 80 % built and unreachable. *(Critical, effort S — the best line-count-to-capability ratio in the repo.)*
2. **FDIR does not run.** `FaultPropagator` is constructed and 9 cascades registered; `detect_fault()` has **zero call sites**. `ProcedureExecutor.start_procedure()` likewise — the 10 recovery procedures are registered and never started. Consequently 7 of 8 FDIR events in the catalogue can never be emitted. For an FDIR-centric course this is the largest gap between what the documentation promises and what the tool does. *(Critical, M.)*
3. **Parameter history and trending — there is none.** Live plots only. The archive, the storage and four `/api/archive/*` endpoints all exist and are called by no UI. *(M.)*
4. **`dispatch()` signals errors by side-channel** and is not reentrant. With S11 release, S19 event-actions and S18 OBCP steps all nesting, an inner command clobbers the outer command's flag and the outer reports the inner's outcome. Defect #87 built excellent plumbing on a non-reentrant mechanism. *(Major, S–M.)*
5. **Instructor snapshot is built off-thread against live state** — `_get_all_subsystem_states()` iterates every subsystem's `__dict__` on the aiohttp thread while the tick thread mutates it, and stores dicts/lists **by reference**, so `json.dumps` can hit `RuntimeError` against a growing `image_catalog`. The instructor console is the display most likely to be open during an anomaly. *(Major, S–M.)*
6. **`start.sh` launches 8 services with `sleep 2` and no readiness check** — a dead simulator leaves MCS and planner showing an empty, plausible console, and `wait` doesn't stop the suite. The `--public` banner is also untrue: three servers hard-code `0.0.0.0` regardless. *(Major, S.)*
7. Config-contract test bundle — five generic data-driven tests (parameter chain closure, limits-are-live, config-keys-have-consumers, catalogue↔dispatch bijection, endpoint reachability) that between them would have caught most of this project's recurring defect class. *(M.)*
8. Delete `files/`, `old_index.html`, `index-wide.html` — ~9,000 lines of stale parallel implementations that students grep into daily. *(S.)*

Explicitly **not** worth doing: the GNU Radio bridge (#18) — the native DSP path now covers the syllabus; defect #19 — superseded, close it; a 3D attitude visualiser; a second wall display.

---

## 3. Model allocation

Franz asked what should go to Fable and what to other versions. The split that matters is **judgement density and horizon length**, not subject matter.

### Give to Fable — long-horizon work where a wrong early decision is expensive

- **Phase A foundation** (A-2/A-3/A-4). Deceptively subtle: the sub-stepping contract, the √dt noise scaling and the state-ownership protocol are all cross-cutting design decisions that every later package inherits. Getting the protocol shape wrong here costs a rewrite of six subsystems.
- **Phase C AOCS, all of it.** The largest package, densest in physics judgement, and the one with a hard non-functional constraint (real-time × acceleration × breakpoint) that has to be held in mind across eleven work packages. The secular-mode design in particular is the kind of thing that goes quietly wrong.
- **Phase B power/thermal coupling** (B-1, B-2, B-8). Choosing the array architecture and getting the actuator-state billing right is where the engineering argument lives. The rest of Phase B is mechanical once these are settled.
- **Phase D propulsion**, if Franz confirms it — the AOCS coupling, the SGP4 re-fit and the speed-clamp interaction are all cross-subsystem.
- **The contested-encoding decisions in Phase E** (func 28, func 68, the FPA encoding) and the argument-schema design. The schema shape determines whether the drift problem is solved or merely relocated.
- **Adjudicating the nine open judgement calls** in `DECISIONS.md`, and flagging back the ones that are genuinely Franz's.

### Delegate to Sonnet-class subagents that Fable spawns — broad, mechanical, verifiable

- The **config/doc sweeps**: brief 07's WP-1…WP-14 courseware passes, once the numbers are settled upstream. These are large, repetitive, and each has an unambiguous right answer once the ruling exists.
- The **five config-contract tests** and their allowlists (08 WP-8) — well-specified, mechanically checkable.
- The **root consolidation and dead-code deletion** (A-1 tail, G-8).
- **Telemetry plumbing**: parameter-ID collision fixes, HK SID assignments, the 50 unreachable parameters, the four tier parameters missing from HK.
- **Report templates** once the schema is fixed, and the **config-editor form generation** once the schema-driven approach is chosen.
- The **MCS builder UI implementation** once the field schema exists — substantial but specified.

### Delegate to Haiku-class — bulk verification and inventory

- Running and triaging the 3,900-test suite between packages.
- Cross-referencing procedure/ops-card IDs against the catalogue after each change.
- The endpoint/parameter/config-key reachability inventories that feed the contract tests.

### Keep off the critical path

Phase F (tooling) has almost no coupling to the physics work. It can run entirely in parallel in a separate worktree by a second agent, and merge cleanly. If Franz wants two things happening at once tomorrow, that is the seam.

**One meta-recommendation:** have Fable work in `EnterWorktree` isolation per phase and commit at each phase boundary. With ~90 defects of uncommitted history already in the tree, the cost of a phase that has to be backed out is currently the whole day.

---

## 4. Suggested order for tomorrow

```
A-1  commit / branch / tag / root cleanup          ← 30 min, blocks everything
A-2  seeded RNG                        ┐
A-3  sub-stepping + √dt noise          ├─ foundation, in one sitting
A-4  state round-trip + property test  ┘
──────────────────────────────────────── suite green, commit
B-4  un-invert the protection ladder    ← quick win, likely fixes the reported symptom
B-9  false array-degraded alarm         ← quick win
B-6  single FPA node                    ← quick win, fixes the reported symptom
B-7  battery heater reachable           ← quick win, fixes the reported symptom
──────────────────────────────────────── suite green, commit — four of Franz's reports closed
B-1/2/3/5/8  power architecture + billing coupling
──────────────────────────────────────── suite green, commit, re-run tools/power_budget.py
C-0…C-11  AOCS                          ← the bulk of the day
──────────────────────────────────────── suite green, commit
E    commanding schema + builder UX     ← if the day holds
D    propulsion                         ← only if Franz confirms §1 of DECISIONS.md
```

Phase F and the Phase G quick wins (limits.yaml, start.sh readiness) run in parallel in a second worktree throughout.

---

## 5. What Fable should read, and in what order

1. This file.
2. `DECISIONS.md` — the nine open judgement calls, four of which change work below.
3. The brief for the phase in hand. Do **not** read all eight up front; they total ~450 KB and each is self-contained with file:line references.
4. `briefs/scripts/thermal/` — three runnable reproduction scripts (`fpa.py`, `bat.py`, `panel.py`) that demonstrate the FPA divergence, the battery-thermostat unreachability, and the panel-temperature error numerically. Re-run them as regression checks.

Every brief ends with a work-package table carrying files, **testable** acceptance criteria, effort and dependencies. Those are the units of work; this document is only the ordering and the reasoning behind it.

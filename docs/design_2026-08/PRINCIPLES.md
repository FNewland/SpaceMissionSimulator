# PRINCIPLES — display design standard for the SMO Mission Control System

Ten principles, derived from RESEARCH.md and aimed at the specific defects in AUDIT.md.
This document is intended to become the *style guide* that ISA-101 phase 1 requires — the thing that stops the drift documented in AUDIT §2.1 and §8.

Each principle states: the rule, the reason, what it costs, and — where relevant — **the trade-off**, i.e. the point at which "looks appealing" and "is operationally correct" pull in opposite directions and how we resolve it.

A recurring meta-principle, stated once: **EOSAT-1's console is a teaching artefact.** A student who spends forty hours in it will absorb its habits. So where a choice is close, choose the one that models industry practice, and make the reason visible on screen.

---

## P1. Grey is the resting state. Colour means "something is not nominal."

**Rule.** The default rendering of every value, every schematic element, every panel and every chart series is neutral grey on near-black. Colour is introduced *only* when a parameter is outside its limits, stale, commanded-but-not-confirmed, or otherwise abnormal. A fully nominal spacecraft produces a screen with essentially no colour on it.

**Reason.** ISA-101 and the high-performance-HMI movement: "a grayscale screen with no color present means a normal operating plant with no alarms," reported to produce a 48% improvement in detecting abnormal situations before alarms occur (RESEARCH §1.3). Colour is the strongest pre-attentive channel we have; spending it on decoration means we cannot spend it on signalling.

**What it forbids here.** The eleven ad-hoc identity hues (AUDIT §2.1). `#00C896` cannot mean both "TT&C" and "nominal." Panel headers are not blue. Section headers on the EPS schematic are not amber/blue/green.

**⚠ Trade-off.** This is the big one. A grey screen photographs badly, demos badly, and reads as "unfinished" to a stakeholder expecting a mission-control aesthetic. Three mitigations, all of which we take:
1. **Identity moves from hue to shape and position.** A schematic is recognisable by its layout. Subsystem identity is carried by a consistent *slot* in the overview and a monogram, not a colour.
2. **Beauty moves to typography, spacing and alignment** — which are free, operationally. A well-set grey screen with a strict baseline grid and generous whitespace looks *more* expensive than a neon one, and reads as premium to a modern eye.
3. **One accent, used for interaction only.** A single blue is permitted for focus, selection, links and the active navigation state — never for data. This gives the UI a "brand" without touching the status budget.

---

## P2. Four display levels, nested — never fourteen siblings.

**Rule.** Every screen declares its level and its parent.

- **L1 OVERVIEW** — one screen, no scrolling, the whole spacecraft. Answers "is EOSAT-1 OK, and if not, what and how urgent?" Reachable from anywhere with one key.
- **L2 SUBSYSTEM** — one screen per subsystem. The controller's home. Schematic + key parameters + embedded trends + that subsystem's alarms and commands.
- **L3 EQUIPMENT** — one battery, one reaction wheel, one power line, one star tracker. Full parameter set, limits, history, related commands, related procedures.
- **L4 DIAGNOSTIC** — PUS service panels, memory browser, packet inspector, raw dumps, archive queries.

Navigation is drill-down: you get to L2 by clicking the thing on L1 that concerned you, and to L3 by clicking the thing on L2. A flat tab strip may exist as an *accelerator* for experienced users, but it is not the primary model.

**Reason.** ISA-101 display hierarchy (RESEARCH §1.2); Kratos/fleet-ops pressure toward exception-based monitoring (§2.4). SMO today has no L1 at all and mixes L2/L3/L4 as peers (AUDIT §1.1).

**Cost.** Requires deciding, for every existing panel, which level it belongs to. That is a day of work and it is the most valuable day in the project.

---

## P3. A number without its limits is not information.

**Rule.** Every displayed parameter that has an entry in `limits.yaml` renders as a triad: **numeral + unit + deviation bar**. The deviation bar shows the value's position within its operating range with the yellow and red boundaries drawn as ticks and labelled numerically on hover/focus. Parameters *without* limits render with a visible "no limit defined" affordance rather than looking identical to limited ones.

**Reason.** High-performance HMI: "rather than displaying '900 psi' without meaning, pair values with normal operating ranges" (RESEARCH §1.4). OpenC3 makes this a first-class widget (`LIMITSBAR`, `LABELVALUELIMITSBAR`) whose colour is driven automatically from the telemetry dictionary (§2.3). SCOS-2000 ANDs carry a per-row OOL flag (§2.1).

**Why it matters most here.** Ground limit checking is *the* core MCS concept this simulator teaches, and the limits are currently invisible (AUDIT §1.3, §4.2). A student sees a number turn yellow and cannot learn what yellow means. This one component fixes four audit findings at once.

**Cost.** One new component; a small join between `parameters.yaml` and `limits.yaml` at render time. Both files already exist and are already loaded.

---

## P4. Every value carries its own freshness. Stale must never look like steady.

**Rule.** Age of data is a first-class visual property, not a footnote. Fresh = normal rendering. Degraded (5–15 s) = an explicit age chip. Stale (>15 s, or subsystem HK disabled, or post-LOS) = the value is replaced by an em-dash with a hatched background, and charts *stop appending* rather than repeating the last sample. Freshness is shown per parameter, per subsystem and per page.

**Reason.** Endsley Level 1 is not "perceive a number," it is "perceive the correct current number." A flat line of repeated last-values after LOS is actively misleading — a lie the display tells confidently. The existing chart staleness policy (`index.html:3303–3330`) is genuinely well-reasoned and should be promoted from a chart-only rule to a display-wide invariant.

**Trade-off.** Hatching and dashes make a post-LOS screen look broken. That is correct: after LOS you *have* no telemetry, and the display should say so loudly. Students learning to distinguish "the spacecraft is fine" from "I cannot see the spacecraft" is a core LEOP lesson.

---

## P5. One idiom per concept, everywhere.

**Rule.** A fixed, small component vocabulary, defined once and used identically on every screen:

| Concept | The one component |
|---|---|
| Scalar with limits | `value + unit + deviation bar` (P3) |
| Scalar trend | `sparkline` (inline, no chrome) or `trend panel` (framed, with time axis) — never a third thing |
| Binary / enumerated state | `state chip`: glyph + text label + optional colour |
| Progression through discrete stages | `stepper` (load shed, AOCS mode, TC verification, procedure steps — all the same component) |
| Equipment health on a schematic | `schematic node`: outline weight + glyph + optional colour |
| Tabular parameter set | `AND table`: name, description, raw, engineering, unit, limit state |
| List of things that happened | `event row`: time, severity glyph, source, text |

**Reason.** Nielsen "consistency and standards"; the seven-idiom sprawl in AUDIT §2.4, where three different components (mode chain, lock chain, load-shed stepper) all express "a chain of stages where one is current" and all look different.

**Cost / benefit.** This is the principle that makes the codebase shrink. Seven idioms × six subsystem tabs is why `index.html` is 10,595 lines. One idiom, parameterised, is how it becomes config-driven (P10).

---

## P6. Colour is never the only cue.

**Rule.** Every status encoding carries at least two of {colour, glyph/shape, text, position, fill pattern, weight}. The acceptance test: **render the page in `grayscale(1)` and it must remain operable.** Ship a greyscale toggle so this can be checked continuously, and so it can be demonstrated to students.

The scheme (from RESEARCH §6):

| State | Colour | Glyph | Third cue |
|---|---|---|---|
| Nominal | none (grey) | none | normal weight |
| Warning | amber `#E8A317` | `▲`/`▼` hollow | 3 px left border |
| Alarm | red-orange `#FF5C4D` | `▲`/`▼` filled | 3 px left border + bold |
| Stale | grey `#6E6E80` | `⌀` | hatched fill, value → `—` |
| Commanded / pending | blue `#5AA9FF` | `◇` | dashed border |
| Off / unpowered | dark grey | `○` hollow | text `OFF` |

Red-orange over pure red and amber over yellow-green: the pair separates by *lightness* as well as hue and so survives deuteranopia and protanopia.

**Reason.** WCAG 2.2 SC 1.4.1; ~8% of male students have a red-green CVD; ISA-101 explicitly requires "colour + shape + position" (RESEARCH §1.3, §6). AUDIT §7.2 lists four colour-only encodings currently shipping, including the per-tab health dot, which is the *only* persistent per-subsystem health signal in the product.

---

## P7. Alarms are a managed system, not a log.

**Rule.** Adopt the ISA-18.2 model explicitly and make it visible:

1. **Four priorities**, assigned by consequence × required response time (P1 <1 min / P2 minutes / P3 tens of minutes / P4 end of shift) — not by PUS event severity.
2. **Per-alarm rationalisation text** — purpose, cause, consequence, operator response — shown in the alarm row detail, sourced from `alarm_procedures.yaml` and `fault_propagation.yaml`.
3. **The full lifecycle is visible**: Unacknowledged → Acknowledged → Return-to-normal, plus Shelved (with timer and auto-unshelve), Suppressed-by-design (state-based, e.g. no payload-thermal alarms when the payload is off), Out-of-service.
4. **Flood handling**: an alarm-rate meter with the EEMUA/ISA thresholds drawn on it (>10 per 10 min = flood), and first-out / root-cause grouping so a cascade collapses to "1 root cause, 14 consequent."
5. **One alarm surface.** Not eight (AUDIT §4.1). A single alarm list is the authority; every other indicator is a *link into it* showing a count derived from it.
6. **Alarm → procedure is one click.** Always.
7. Blink is reserved for **unacknowledged P1 only**, and stops on acknowledgement.

**Reason.** EEMUA 191 / ISA-18.2 (RESEARCH §4). SMO ships cascade scenarios designed to produce floods, and a `fault_propagation.yaml` that already encodes the causal graph, but a UI that cannot express any of it.

**⚠ Trade-off.** Shelving *looks* like hiding problems, and an instructor may worry students will shelve their way out of an exercise. Resolve by making shelving loud and accountable: shelved alarms appear in a persistent "3 SHELVED" chip with a countdown, shelving is logged to the console log with the operator's position, and shelved items auto-unshelve. That is exactly how it works in industry, and the accountability *is* the lesson.

---

## P8. Design for Level 3 situation awareness — projection, not just perception.

**Rule.** Every subsystem screen answers a *future* question, not only a present one. Concretely, ship at minimum:

- **Time-to-limit** on trending parameters: "SoC → yellow in 14 min at current rate."
- **Event-boundary prediction**: predicted SoC at eclipse exit; predicted buffer fill at next AOS; predicted momentum at next desat.
- **Eclipse and contact arcs shaded on every trend**, past and future, so a trend is read against the orbital events that drive it.
- **A mission timeline** as a persistent element: past events, now, next AOS/LOS, next eclipse entry/exit, scheduled activities.

**Reason.** Endsley's three levels (RESEARCH §3). SMO is strong at L1 (perception), weak at L2 (comprehension), and nearly absent at L3 (projection) — while the backend already computes orbit, eclipse, contact schedule and load-shed staging. Real flight controllers are graded on anticipation.

**Cost.** Modest: mostly a linear extrapolation plus data the server already has. High teaching value per unit effort.

---

## P9. The causal chain must be traceable on one screen.

**Rule.** For any command, the operator can see — without changing screen — the whole chain:

> **command sent → TC acceptance (S1,1/1,2) → execution (S1,7/1,8) → on-board state change → telemetry parameter moves → limit crossed → alarm raised → procedure recommended**

And symmetrically, from any alarm, the operator can walk *backwards* to the command or event that caused it.

**Reason.** Nielsen "recognition over recall" and "help users recover from errors"; AUDIT §6 finds this chain split across five surfaces in four tabs. This chain *is* the mental model the simulator exists to install. Splitting it across tabs is not just inconvenient — it prevents the learning.

**Consequences.** Commanding must be available *in place* on every subsystem screen (today only PAYLOAD has it). Hazardous commands get an explicit arm → execute step, modelling real ops and satisfying "error prevention." The verification chain renders as a stepper (P5) attached to the command, not as a separate log.

---

## P10. The display is configuration, not code. And the configuration is a teaching artefact.

**Rule.** Screens are defined declaratively in `displays.yaml`, rendered by a small generic renderer over the component vocabulary of P5, with limits and units joined automatically from `limits.yaml` and `parameters.yaml`. Positions (`positions.yaml`) select *which* displays and which components are visible and commandable. Hand-written per-subsystem HTML is eliminated, not extended.

**Additionally:** the config must be *validated* against the telemetry dictionary at load, and a display referencing an unknown parameter must fail loudly. `displays.yaml` has already drifted out of sync with `parameters.yaml` with nothing detecting it (AUDIT §9).

**Reason.** ESA's Unified Synoptics work and OpenC3's screen-definition model both say the same thing: the definition format is the durable asset, the per-mission hand-coding is the liability (RESEARCH §2.1, §2.3). For SMO specifically, `displays.yaml` already exists, already has roughly the right vocabulary, and renders nothing — it is currently a trap for maintainers.

**Teaching bonus.** A config-driven console means a student can *author a display* — which is a real ground-segment engineering task, and impossible today.

---

## Bonus rules (not principles, but non-negotiable)

- **B1. 14 px minimum for primary readouts**; 12 px minimum for any text, anywhere. Delete every 6/7/8/9 px rule (AUDIT §2.3). Type scale carries importance: the number you must read is bigger than its label, always.
- **B2. WCAG 2.2 AA minimum**: 4.5:1 for text, 3:1 for graphical objects and UI components. `--text-tertiary` at ~3.6:1 is the product's most widespread accessibility failure and is one hex value.
- **B3. Keyboard-complete abnormal-condition workflow.** Jump to alarms, acknowledge, shelve, open procedure — all without a mouse. Focus traps on every overlay.
- **B4. `prefers-reduced-motion` honoured**; no infinite animation except unacknowledged P1.
- **B5. Fits 1366×768 with no vertical scroll on L1 and on any L2 screen.** Chrome budget ≤ 140 px total; the log panel is collapsible.
- **B6. One shared design-token file** across MCS, instructor and planner UIs. Three products, one palette (AUDIT §8).
- **B7. No decorative raster assets in operational chrome.** (There is currently a screenshot used as a footer logo.)

---

## Summary table: principle → audit defect fixed

| Principle | Fixes |
|---|---|
| P1 Grey baseline | §2.1 colour ambiguity, §2.2 chrome noise, §3 chart series colours |
| P2 Four levels | §1.1 fourteen siblings, §1.3 discoverability, §5.2 scroll |
| P3 Limits with values | §1.3 limits invisible, §3 no limit bands, §4.2 |
| P4 Freshness | §1.3 on-demand vs missing, promotes existing §3 policy |
| P5 One idiom | §2.4 seven idioms, §1.2 tab drift |
| P6 Never colour alone | §7.2 colour-only encodings, §2.4 invisible border cues |
| P7 Alarm system | §4.1 eight surfaces, §4.2 no priority/shelving/flood |
| P8 Projection | new capability; L3 gap |
| P9 Causal chain | §6 command console split, §1.3 procedures unreachable |
| P10 Config-driven | §9 dead config, §1.2 bespoke tabs |
| B1–B7 | §2.3 type, §7.1 contrast, §7.3 keyboard, §7.4, §5.2, §8 |

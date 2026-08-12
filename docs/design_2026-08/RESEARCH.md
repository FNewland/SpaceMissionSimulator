# RESEARCH — best practice for mission-control and process-control HMI

Compiled 2026-08-12 for the SMO / EOSAT-1 Mission Control System redesign.
Every claim below is attributed. Where I am extrapolating to *this* product rather than reporting a source, the paragraph is marked **[Application]**.

---

## 1. ISA-101 and the high-performance HMI movement

### 1.1 What the standard is

**ANSI/ISA-101.01-2015, "Human Machine Interfaces for Process Automation Systems"** governs the design, implementation, operation and maintenance of HMIs across DCS, SCADA and web dashboards. It deliberately specifies *design constraints and a lifecycle*, not pixel geometry, and is complemented by ISA-5.1 for instrumentation symbols.

The standard mandates a five-phase HMI lifecycle: (1) standards and style-guide creation — palette, fonts, symbols, alarm priorities, naming conventions; (2) design — display hierarchy, task analysis, console layout; (3) implementation; (4) operation with operator-feedback capture; (5) maintenance under formal management of change.
— [ISA-101 HMI Design Standard: Complete Guide for Engineers, HMI Library](https://hmilibrary.com/standards/isa-101)

**[Application]** The lifecycle point matters more for this product than the visual rules. SMO currently has no style guide — which is *why* eleven ad-hoc hues exist alongside a five-token status palette (AUDIT §2.1). A style guide is the deliverable that stops the drift, and it is also a teaching artefact: "here is our display standard" is a lesson an aerospace student will meet again at ESOC or in any prime's ops team.

### 1.2 The display hierarchy

Both ISA-101 and the wider high-performance HMI literature specify four levels:

- **Level 1 — Overview.** The whole process/plant at a glance: site-wide KPIs and alarm counts, for supervisory viewing. "Overall situational awareness."
- **Level 2 — Unit / Area.** The routine monitoring screen for the panel operator; one subsystem in detail.
- **Level 3 — Equipment detail.** Specific equipment (pump, valve, wheel, battery) with loops, interlocks and controls.
- **Level 4 — Diagnostics.** Deep instrument diagnostics, maintenance and troubleshooting data.

— [HMI Library, ISA-101 guide](https://hmilibrary.com/standards/isa-101); [RealPars, "What Is High-Performance HMI?"](https://www.realpars.com/blog/high-performance-hmi)

The crucial property is that levels are **nested, not parallel**: you reach Level 2 by drilling from the Level 1 element that concerned you, and Level 3 from the Level 2 element. Navigation follows the plant's structure, not an alphabetical tab strip.

**[Application]** SMO's fourteen tabs are fourteen *siblings* spanning Levels 2, 3 and 4 with **no Level 1 at all** — the OVERVIEW tab is a Level-2-density page (schematic + timeline + world map + tables), not a "one glance, is it OK?" surface. Retrofitting Level 1 is the single highest-leverage change available.

### 1.3 The grey-scale baseline and colour discipline

The defining rule of high-performance HMI:

> "A grayscale screen with no color present means a normal operating plant with no alarms."

Colour is reserved **exclusively** for abnormal conditions and state changes. RealPars reports this approach has produced a "48% improvement in detecting abnormal situations before alarms occur."
— [RealPars](https://www.realpars.com/blog/high-performance-hmi)

ISA-101's style rules, as summarised by HMI Library:
- Normal operation uses "muted gray and white."
- Colour reserved exclusively for abnormal conditions and state changes.
- Colourblind-safe combinations use **colour + shape + position** — never colour alone.
- Sans-serif fonts, **14 px minimum for primary readouts**; monospaced fonts for tag IDs and numeric columns.
- **No decorative colour, no 3D effects, no gradients, and no static blinking on non-alarms.**
- Equipment state coding: dark grey = stopped/closed, white = running/open, medium grey = no feedback.

Alarm priority colours and expected response times:

| Priority | Colour | Response time |
|---|---|---|
| P1 | Red | < 1 minute |
| P2 | Orange | Minutes |
| P3 | Yellow | Tens of minutes |
| P4 | Blue | End of shift |

— [HMI Library](https://hmilibrary.com/standards/isa-101)

**[Application]** SMO violates six of these seven style rules simultaneously (AUDIT §2.2, §2.3): gradients on the top bar, panel headers and the contingency banner; glow `text-shadow` on nominal values; a decorative dot-grid; 8–9 px primary readouts; infinite blinking on non-acknowledged-gated LEDs; and colour used for subsystem *identity*.

### 1.4 Information over raw data; embedded trends; analogue deviation

Two further high-performance HMI staples:

- **Context with the number.** "Rather than displaying '900 psi' without meaning, pair values with normal operating ranges so operators can make a quick decision to take action."
- **Embedded trend visualisation.** Always-visible historical trends for critical variables, so operators "see a quick overview of historical data and evaluate for any necessary process adjustments" proactively rather than reactively.

— [RealPars](https://www.realpars.com/blog/high-performance-hmi)

The canonical high-performance components that deliver this are the **deviation bar** (a horizontal analogue indicator showing the value's position within its normal band, with the band boundaries drawn as ticks) and the **embedded sparkline** (a 40–120 px trace with no axes, chrome or legend, sitting immediately beside the numeral). Together they turn a numeral into a judgement: *where is it, and which way is it going.*

**[Application]** SMO has neither. It has `.gauge-bar` (a 13 px solid fill with no threshold ticks) and full-chrome Chart.js panels. Adding a deviation bar + sparkline primitive, driven from `limits.yaml`, would simultaneously fix AUDIT findings §3 (no limit bands), §1.3 (limits invisible) and §4.2 — one component, four defects.

Further reading on the movement generally: [PLC Programming, "High Performance HMI (ISA-101)"](https://plcprogramming.io/blog/high-performance-hmi-isa-101); [Industrial Monitor Direct, "ISA-101 High Performance HMI Design: Color, Levels"](https://industrialmonitordirect.com/blogs/knowledgebase/isa-101-high-performance-hmi-design-principles-color-strategy); [Michelle Alzola, "Understanding ISA-101"](https://michellealzoladesign.com/understanding-isa-101/).

---

## 2. Mission-control practice: ESA, NASA and the commercial MCS vendors

### 2.1 ESA / ESOC — SCOS-2000

SCOS-2000 is "the generic satellite Mission Control System (MCS) software infrastructure developed and maintained by the European Space Agency," built to let missions share common functionality and standardised software practice. It handles "comprehensive telemetry processing, manual and automatic commanding, on-board software management, mission archive and web-based data distribution," using CCSDS frame and packet standards. It has flown 25+ missions including Mars Express, Rosetta, Gaia and BepiColombo, and is the ancestor of commercial systems including EUTELSAT NEO and GMV's hifly. From 2020 ESA missions transition to the **European Ground Systems Common Core (EGS-CC)**.

Critically for display design: "typical functions performed on receipt of a telemetry packet include checking the parameters are within range (**hard and soft limit checking**) and validation of sent telecommands."
— [SCOS-2000, Wikipedia](https://en.wikipedia.org/wiki/SCOS_2000); [ESA, SCOS-2000](http://new.www.esa.int/Enabling_Support/Operations/Ground_Systems_Engineering/SCOS-2000); [ESA Mission Control FAQ](https://www.esa.int/Enabling_Support/Operations/ESA_Mission_Control_FAQ)

The SCOS-2000 display idiom that matters here is the pairing of two view types, both **configured rather than coded**:

- **Alphanumeric Displays (AND)** — dense tabular parameter lists: name, description, raw value, engineering value, unit, validity, and a limit/OOL status flag per row. This is the ESOC workhorse; a spacecraft controller lives in ANDs, not in pictures.
- **Synoptic Displays (SYN)** — schematic mimics built in a synoptic editor, with graphical elements bound to parameters and to limit state.

ESA's own "Unified Synoptics System" work describes consolidating these editors across missions — i.e. even ESA's problem is that synoptics proliferate per-mission unless the *editor and the definition format* are shared.
— [ESA Unified Synoptics System (presentation)](https://www.slideserve.com/semah/unified-synoptics-system); [SCOS-2000: Evaluating a generic mission control system according to SPEC (ESA SP-532)](https://adsabs.harvard.edu/pdf/2003ESASP.532E..74J); [SCOS-2000 MULTISAT, AIAA 2002-T2-46](https://arc.aiaa.org/doi/pdf/10.2514/6.2002-T2-46)

**[Application]** Two lessons. (a) **The AND is a first-class display type and SMO does not have one.** SMO has bespoke labelled panels per subsystem; it has no "show me every EPS parameter with raw, engineering, unit and OOL status" table. That table is what a real spacecraft controller reads, and it is also the easiest display in the world to generate from `parameters.yaml` + `limits.yaml`. (b) SMO's `displays.yaml` is, structurally, a synoptic/AND definition format that was never given a renderer — ESA's experience says the format is the right investment, the per-mission hand-coding is the thing to eliminate.

### 2.2 NASA MCC / ISS flight-controller displays

NASA's Mission Control Center consoles are the origin of several conventions worth naming explicitly for students:

- **Flight Notes** — the numbered, logged, formally-transmitted record of a decision or deviation; the flight-control-team analogue of an issue tracker. Nothing enters the record informally.
- **The console log** — every flight controller keeps a timestamped log; handover is a formal read-and-sign event.
- **GO/NO-GO polling** — the Flight Director polls each position by callsign; each answers GO, NO-GO or STAND BY. The poll is a *structured, recorded* decision gate.
- **Callsign-per-position discipline** — a position (EGIL, ADCO, CRONUS, PLUTO on ISS; here Flight Director, Power & Thermal, Flight Dynamics, TT&C, Payload Ops, FDIR/Systems, Propulsion) owns a defined set of parameters and a defined commanding authority.

**[Application]** SMO already implements handover notes and GO/NO-GO polling — and buries both in the 160 px bottom drawer behind tab #4 and #5 (AUDIT §1.1). These are the *most* mission-control-authentic features in the product and they have the *least* visual status. `positions.yaml` already encodes the authority model (`allowed_services`, `allowed_func_ids`) — surfacing "what am I authorised to do" on the console is a direct import of the callsign discipline.

### 2.3 OpenC3 / COSMOS — the reference declarative display model

OpenC3 COSMOS (open-source, NASA-adjacent; used across smallsat and instrument ground systems) defines telemetry screens in a plain-text definition file: a `SCREEN` keyword with dimensions and polling period, followed by widget declarations.

Widget categories:
- **Layout:** `VERTICAL`, `HORIZONTAL`, `VERTICALBOX`, `HORIZONTALBOX`, `MATRIXBYCOLUMNS`, `SCROLLWINDOW`, `TABBOOK`/`TABITEM`, `IFRAME`.
- **Decoration:** `LABEL`, `TITLE`, `HORIZONTALLINE`, `SPACER`, `FILEDISPLAY`.
- **Telemetry:** `VALUE`, `LABELVALUE`, `LED`, `LIMITSBAR`, `LIMITSCOLUMN`, `LINEGRAPH`, `SPARKLINE`, `PROGRESSBAR`, `RANGEBAR`, and composites such as `LABELVALUELIMITSBAR`.
- **Interactive:** `BUTTON`, `TEXTFIELD`, `CHECKBUTTON`, `COMBOBOX`, `RADIOGROUP`.

Limit colours are applied **automatically** from the telemetry definition's limits: GREEN nominal, YELLOW warning, RED critical, BLUE out-of-range. Styling is via `WIDTH`/`HEIGHT`/`MARGIN`/`BACKCOLOR`/`TEXTCOLOR`, with `GLOBAL_SETTING`/`GLOBAL_SUBSETTING` for consistent theming across a widget class.
— [OpenC3 Docs, Telemetry Screens](https://docs.openc3.com/docs/configuration/telemetry-screens); [OpenC3 Docs, Requirements and Design](https://docs.openc3.com/docs/getting-started/requirements); [OpenC3/cosmos on GitHub](https://github.com/OpenC3/cosmos); [nasa-itc/openc3-cosmos](https://github.com/nasa-itc/openc3-cosmos)

**[Application]** This is almost exactly the vocabulary `displays.yaml` gropes toward (`gauge`, `status_indicator`, `value_table`, `line_chart`, `event_log`). The two things OpenC3 has that SMO's config lacks and needs are (a) **`LIMITSBAR`/`LIMITSCOLUMN`** — the deviation-bar primitive, automatically bound to limits, and (b) **`SPARKLINE`**. The two things OpenC3 does that SMO should copy wholesale are: limits drive colour *automatically from the dictionary* (no per-widget colour authoring), and `GLOBAL_SETTING` makes theming a one-line change. See RECOMMENDATION.md §5 for a concrete `displays.yaml` v2 schema.

### 2.4 Commercial MCS: GMV hifly, Kratos

**GMV hifly** is the satellite control system family used by commercial operators, with hifly 6 the current generation; GMV also ships **focusSuite** for flight dynamics. hifly's lineage traces to SCOS-2000 (per the SCOS-2000 entry above), so its display model — ANDs, synoptics, config-driven — is the same family.
— [GMV hifly family](https://www.gmv.com/en-es/products/space/hifly-family-products); [GMV hifly](https://www.gmv.com/en/products/space/hifly); [SpaceNews, "GMV Releases hifly 6.0"](https://spacenews.com/gmv-releases-hifly-60-satellite-control-system/); [GMV focusSuite](https://www.gmv.com/en-es/products/space/focussuite)

**Kratos quantumCMD** was introduced as a command-and-control system designed specifically for smallsats and *fleets* of smallsats — the design pressure being one operator monitoring many spacecraft, which pushes hard toward exception-based (Level-1-first) displays rather than per-spacecraft synoptic browsing.
— [Kratos, quantumCMD announcement](https://www.kratosdefense.com/newsroom/kratos-introduces-quantumcmdtm-the-first-command-and-control-system-designed-for-small-satellites-and-fleets-of-small-satellites)

**[Application]** The fleet-operations trend reinforces the same conclusion as ISA-101: **exception-based monitoring**. The default screen should be quiet and should surface only deviations; browsing every subsystem's schematic is a Level-2/3 activity, not the home page.

---

## 3. Situational awareness theory

**Endsley's model** defines three levels of situation awareness:

1. **Level 1 — Perception** of the elements in the environment (what are the values?).
2. **Level 2 — Comprehension** of their meaning (is this normal? what does it imply, together?).
3. **Level 3 — Projection** of future status (where is this going, and when will it matter?).

**Situation Awareness Oriented Design (SAOD)** operationalises this into a design method: goal-directed task analysis to derive the operator's *SA requirements* per goal, then design displays that directly supply Level 2 and Level 3 rather than leaving the operator to compute them.
— Endsley, Bolstad, Jones & Riley, "Situation Awareness Oriented Design: From User's Cognitive Requirements to Creating Effective Supporting Technologies," *Proc. HFES* 47(3), 2003 — [SAGE](https://journals.sagepub.com/doi/10.1177/154193120304700304), [ResearchGate PDF](https://www.researchgate.net/publication/237436738_Situation_Awareness_Oriented_Design_From_User's_Cognitive_Requirements_to_Creating_Effective_Supporting_Technologies), [Semantic Scholar](https://www.semanticscholar.org/paper/Situation-Awareness-Oriented-Design:-From-User's-to-Endsley-Bolstad/f3ce92d6da4922cd7798b71ac7c8427585e29f78). Book: Endsley & Jones, *Designing for Situation Awareness*, 3rd ed., Routledge — [publisher](https://www.routledge.com/Designing-for-Situation-Awareness-An-Approach-to-User-Centered-Design-Third-Edition/Endsley-Jones/p/book/9781032482118). See also [Evaluating Situation Awareness Oriented Design (DiVA)](https://www.diva-portal.org/smash/get/diva2:823781/FULLTEXT01.pdf) and [How User-Centered Design Supports Situation Awareness for Complex Interfaces (Springer)](https://link.springer.com/chapter/10.1007/978-3-030-47775-2_2).

**[Application] — mapped to EOSAT-1 spacecraft ops:**

| Level | Operator question | What SMO gives today | What it should give |
|---|---|---|---|
| L1 Perception | "What is bus voltage?" | Good — 500 parameters, all rendered | (adequate) |
| L2 Comprehension | "Is 27.4 V OK? Is the *power system* OK?" | Weak — colour on the numeral only, limits never shown, no subsystem-level verdict except a 5 px tab dot | Deviation bar with band ticks; per-subsystem health verdict with the *reason*; grouped cause/effect from `fault_propagation.yaml` |
| L3 Projection | "Will SoC hold through the eclipse? Will I make AOS before the buffer fills?" | **Almost nothing.** Eclipse countdown exists (hidden, AUDIT §5.1); no SoC-at-eclipse-exit prediction; no time-to-limit; no buffer-full-at estimate | Time-to-limit ("SoC → yellow in 14 min"), eclipse/contact overlay on every trend, predicted values at the next event boundary |

Level 3 is where a mission-ops teaching product earns its keep: real flight controllers are graded on anticipation, not on reading dials. It is also the level SMO most conspicuously omits — and much of the data needed (orbit propagation, contact schedule, eclipse timing, load-shed staging) is *already in the backend*.

---

## 4. Alarm management: EEMUA 191 and ISA-18.2

### 4.1 Rate benchmarks

Both standards set the same targets:

- **Normal operations:** no more than **one alarm per operator per ten minutes**; roughly **150 alarms per operator per day**.
- **Upset conditions:** no more than **ten alarms in the first ten minutes** of a major disturbance.

Unrationalised facilities typically exceed these by an order of magnitude.
— [Seqent, "Alarm Rationalization Explained: ISA-18.2, EEMUA 191, and What Good Looks Like"](https://seqent.com/blog/alarm-rationalization-explained/)

### 4.2 Rationalisation practice

- **Alarm philosophy document** — a prerequisite defining what constitutes a valid alarm, how priority is assigned, and expected operator behaviour.
- **Per-alarm documentation** — every alarm needs a documented **purpose, cause, consequence, operator response and priority**.
- **Bad-actor analysis** — alarm activity is heavily skewed; the top ten alarms are typically **60–80% of all occurrences**. Fixing those first delivers measurable improvement in days, not months.
- **Continuous process** — rationalisation is reviewed whenever the control system changes, under management of change.

— [Seqent](https://seqent.com/blog/alarm-rationalization-explained/); see also [exida Alarm Management resources](https://www.exida.com/Alarm-Management/Resources), [ProcessVue, ISA 18.2 standards and guidelines](https://www.processvue.com/resources/alarm-management-guidelines/), [Empowered Automation, EEMUA Alarm Management](https://www.empoweredautomation.com/eemua-alarm-management), [PLC Programming, "What Is Alarm Management? ISA-18.2 Explained"](https://plcprogramming.io/blog/what-is-alarm-management), [Process Control Guide, ISA-18.2 practitioner guide](https://processcontrolguide.com/isa-18-2-alarm-management/).

### 4.3 The ISA-18.2 alarm lifecycle states

The standard's state model — which a teaching UI should make *visible*, because it is the concept students most often never meet:

`Normal → Unacknowledged (alarm active, not acked) → Acknowledged (active, acked) → Return-to-Normal (cleared, still unacked) → Normal`, with orthogonal states **Shelved** (operator-suppressed, time-limited, auto-unshelve), **Suppressed-by-design** (logic suppresses it in a mode where it is meaningless), and **Out-of-service** (maintenance).

### 4.4 Alarm floods

A flood is conventionally defined as **>10 alarms per 10 minutes per operator**. Countermeasures: state-based suppression (don't alarm on payload temperature when the payload is off), first-out/root-cause grouping, dynamic prioritisation, and shelving.

**[Application]** SMO has an unbounded append-only journal, two severities, no shelving, no suppression, no flood metric, and no cause/consequence/action text in the row (AUDIT §4.2) — while shipping cascade scenarios (`aocs_sensor_cascade.yaml`, `eps_solar_panel_loss.yaml`, `obc_bus_failure.yaml`) designed to produce exactly the flood the standards are about, and a `fault_propagation.yaml` that already encodes the causal graph needed to do first-out grouping. **The alarm-flood lesson is the highest-value teaching content in the product and the UI currently cannot deliver it.**

Concretely, the redesign should add: an **alarm rate meter** with the EEMUA thresholds drawn on it; **shelve with a timer**; a **cause-grouped flood view** ("1 root cause, 14 consequent alarms — collapse"); and the **cause/consequence/action** text from `alarm_procedures.yaml` in the row detail.

---

## 5. Nielsen-style heuristics applied to dense monitoring UIs

Nielsen's ten usability heuristics, read specifically against a control console:

| Heuristic | Control-room reading | SMO status |
|---|---|---|
| **Visibility of system status** | Freshness of *every* value must be visible; "stale" must never look like "steady" | Partly — chart staleness policy at `index.html:3303–3330` is genuinely good; page-level SA strip is hidden |
| **Match to the real world** | Use the flight-ops vocabulary: AOS/LOS, OOL, TC acceptance, GO/NO-GO, not generic UI words | Good |
| **User control and freedom** | Undo is impossible for a sent command → therefore *confirm before*, and always show what was sent | Weak — no arm/execute step |
| **Consistency and standards** | One idiom per concept, across every screen | **Poor** — seven status idioms |
| **Error prevention** | Gate hazardous commands by position, by spacecraft mode, by arm state | Partly — position gating exists but is silent |
| **Recognition over recall** | The limit should be next to the value; the procedure next to the alarm | **Poor** — both require recall |
| **Flexibility / accelerators** | Keyboard for ack, for navigation, for command recall | **Poor** — mouse-only alarm workflow |
| **Aesthetic and minimalist design** | Every non-data pixel competes with data | **Poor** — see AUDIT §2.2 |
| **Help users recognise and recover from errors** | Rejection reason in full, next to the command, with the fix | Partly — truncated at 340 px |
| **Help and documentation** | Context help at the point of need | Weak — manual is a separate tab |

The dense-monitoring corollary to "aesthetic and minimalist" is Tufte's **data-ink ratio**: maximise the proportion of pixels that encode data, and erase redundant non-data ink. A gradient panel header, a dot-grid background and a glow on a nominal numeral are all non-data ink.

---

## 6. Dark mode and colour-vision deficiency for status encoding

**Prevalence.** Red-green colour vision deficiency affects roughly 8% of males of northern-European descent and ~0.5% of females — in a cohort of 30 aerospace undergraduates, expect one or two.

**The governing rule.** WCAG 2.2 Success Criterion **1.4.1 Use of Color**: colour must never be the *only* visual means of conveying information, indicating an action, prompting a response or distinguishing an element. Status indicators therefore need colour **plus** at least one of: shape, icon, position, text label, or pattern.
— [accessibility.chat, "WCAG Color Accessibility: Status Indicators Beyond Color Coding"](https://www.accessibility.chat/articles/when-color-coding-fails-why-status-indicators-need-more-than-pretty-colors); [Penn State Accessibility, Color Coding](https://accessibility.psu.edu/color/colorcoding/)

**Contrast.** WCAG 2.2 AA requires **4.5:1** for normal text and **3:1** for large text (≥24 px, or ≥18.66 px bold) and for non-text UI components and graphical objects (SC 1.4.11).
— [ColorArchive, WCAG Color Accessibility Guide](https://colorarchive.org/guides/wcag-color-accessibility-guide/)

**Pairs to avoid.** Red/green, green/brown, blue/purple, green/grey, and light-green/yellow are the classic confusions.
— [WebAbility, "8 Colors to Avoid for Color Blindness in Web Design"](https://www.webability.io/blog/colors-to-avoid-for-color-blindness); [Human Standards, Colour & Accessibility](https://www.humanstandards.org/perception/vision/colour-accessibility/); [accessiBe, Color Blind Accessible Web Design](https://accessibe.com/blog/knowledgebase/accessible-website-for-people-with-color-blindness); [CSSAWWWARDS, Designing for Color Blindness (2026)](https://cssawwwards.com/blog/color-blindness-accessible-design-guide-2026); [Medium, "Designing Beyond Sight"](https://medium.com/@taraneyarahmadi/designing-beyond-sight-ux-for-color-blind-accessibility-ac4351e9cce9)

**Dark-mode specifics.**
- Saturated pure colours on near-black backgrounds cause **chromatic aberration / halation** — the eye cannot focus red and blue simultaneously at high saturation, producing a vibrating edge. Desaturate and lighten status colours for dark UI; do not use `#FF0000` on `#000000`.
- Avoid pure black (`#000`) and pure white (`#FFF`) text; use `#0E0E12`-ish and `#E8E8EC`-ish to reduce halation.
- Elevation in dark mode is conveyed by *lighter* surfaces, not by shadow — shadows are nearly invisible on dark backgrounds, so SMO's `box-shadow: 0 2px 8px rgba(0,0,0,.3)` on panels (AUDIT §2.2) does essentially nothing but cost paint time.

**[Application] — a CVD-safe status scheme for this product:**

| State | Colour | Redundant cue 1 | Redundant cue 2 |
|---|---|---|---|
| Nominal | *no colour* (grey `#A9A9B8`) | no glyph | value in normal weight |
| Warning (yellow limit) | amber `#E8A317` | **▲/▼ glyph** showing which limit is breached | left border 3 px |
| Alarm (red limit) | red-orange `#FF5C4D` | **filled ▲/▼ glyph** | left border 3 px + bold |
| Stale / no data | grey `#6E6E80` | **hatched fill** | value replaced by `—` |
| Commanded / in progress | blue `#5AA9FF` | **◇ glyph** | dashed border |
| Off / not powered | dark grey | **hollow outline** | label `OFF` |

Red-orange rather than pure red, amber rather than yellow-green: this pair remains distinguishable under deuteranopia and protanopia by *lightness* as well as hue, and the arrow glyph carries the meaning even in full greyscale. Test by rendering the page in `filter: grayscale(1)` — if you can still run the mission, the encoding is sound. (Mockup C includes a greyscale toggle for exactly this demonstration.)

---

## 7. Where "appealing" and "operationally correct" pull apart

This is the tension Franz named, and it is worth stating precisely, because the resolution is not "choose correctness and accept ugliness."

| The appealing move | Why it is bad practice | The honest resolution |
|---|---|---|
| Saturated colour everywhere; each subsystem a signature hue | Consumes the colour budget; nothing is left to signal abnormality; identity-colour and state-colour become ambiguous (AUDIT §2.1) | **Colour the *chrome* neutrally and let *shape and position* carry identity.** A schematic is recognisable by its layout, not its hue. Beauty comes from spacing, alignment and type — which cost nothing operationally |
| Glow, gradients, glassmorphism, dot-grid | Raises the visual noise floor; reduces headroom for real signals; violates ISA-101 explicitly | **One elevation step, generous whitespace, one accent.** Restraint reads as "expensive" to a modern eye anyway — cf. Bloomberg, Linear, Stripe |
| Big animated gauges and dials | Radial gauges are poor at reading exact values and terrible at showing deviation-from-normal; animation draws the eye to nothing | **Deviation bars and sparklines.** They are *also* the more beautiful component — dense, quiet, information-rich |
| Dark theme with neon accents ("mission control aesthetic") | Halation on near-black; the neon *is* the colour budget being spent on decoration | **Dark theme, desaturated accents, near-black-not-black.** Still looks like mission control. Actually looks more like real mission control, which is mostly grey |
| A live 3-D spacecraft model | Expensive, low information density, occludes itself, hard to make accessible | **A flat schematic that doubles as navigation** (Mockup B). Prettier per pixel and functional |
| Everything visible at once ("look how much data") | Defeats exception-based monitoring; the L1 overview stops being scannable | **Overview-first, drill-down.** Show *fewer* things more legibly, and make the path to detail one click |
| Blinking to convey urgency | Trains ignore-response; violates EEMUA/ISA guidance if not gated on acknowledgement | **Blink only unacknowledged P1, and stop on ack.** Use *position* (top of a sorted list) and *size* for urgency instead |

**The educator's extra constraint.** Because EOSAT-1 is a teaching artefact, the interface is itself a lesson. A student who spends 40 hours in a console that spends colour on decoration will carry that habit into industry. Conversely, a console that *visibly* follows ISA-101 and ISA-18.2 — and says so, in a "why is this screen grey?" annotation — teaches display standards for free, alongside the spacecraft. That argues for making the standards-compliance *legible*: name the display level on each screen, name the alarm priority scheme, show the limit numbers, show the alarm-rate metric against the EEMUA threshold. This is the core idea behind Mockup C.

---

## Source list

**ISA-101 / high-performance HMI**
- [RealPars — What Is High-Performance HMI?](https://www.realpars.com/blog/high-performance-hmi)
- [HMI Library — ISA-101 HMI Design Standard: Complete Guide for Engineers](https://hmilibrary.com/standards/isa-101)
- [PLC Programming — High Performance HMI (ISA-101): Principles & Design](https://plcprogramming.io/blog/high-performance-hmi-isa-101)
- [PLC Programming — HMI Design Best Practices: ISA-101 Screens & Checklist](https://plcprogramming.io/blog/hmi-design-best-practices-complete-guide)
- [Industrial Monitor Direct — ISA-101 High Performance HMI Design: Color, Levels](https://industrialmonitordirect.com/blogs/knowledgebase/isa-101-high-performance-hmi-design-principles-color-strategy)
- [Michelle Alzola — Understanding ISA-101](https://michellealzoladesign.com/understanding-isa-101/)
- [Anexee — ISA-101 SCADA & HMI Compliance Guide 2026](https://www.anexee.com/blog/isa-101-scada-hmi-compliance-guide-2026)
- [Merobix — What Is ISA-101 HMI Design?](https://www.merobix.com/blog/what-is-isa-101-hmi-design)
- [AMD Machines — HMI Design Best Practices: Screen Layout & Alarm Standards](https://amdmachines.com/blog/hmi-design-best-practices-for-operators/)

**Alarm management**
- [Seqent — Alarm Rationalization Explained: ISA-18.2, EEMUA 191, and What Good Looks Like](https://seqent.com/blog/alarm-rationalization-explained/)
- [exida — Alarm Management Services, Alarm Rationalization, ANSI/ISA-18.2, EEMUA 191](https://www.exida.com/Alarm-Management/Resources)
- [ProcessVue — ISA 18.2 Alarm Management Standards and Guidelines](https://www.processvue.com/resources/alarm-management-guidelines/)
- [Empowered Automation — EEMUA Alarm Management](https://www.empoweredautomation.com/eemua-alarm-management)
- [PLC Programming — What Is Alarm Management? ISA-18.2 Explained](https://plcprogramming.io/blog/what-is-alarm-management)
- [Process Control Guide — ISA-18.2 Alarm Management practitioner guide](https://processcontrolguide.com/isa-18-2-alarm-management/)

**Mission control systems**
- [ESA — SCOS-2000](http://new.www.esa.int/Enabling_Support/Operations/Ground_Systems_Engineering/SCOS-2000)
- [Wikipedia — SCOS 2000](https://en.wikipedia.org/wiki/SCOS_2000)
- [ESA — Mission Control FAQ](https://www.esa.int/Enabling_Support/Operations/ESA_Mission_Control_FAQ)
- [ESA SP-532 — SCOS-2000: Evaluating a generic mission control system according to SPEC (PDF)](https://adsabs.harvard.edu/pdf/2003ESASP.532E..74J)
- [AIAA 2002-T2-46 — SCOS-2000 MULTISAT (PDF)](https://arc.aiaa.org/doi/pdf/10.2514/6.2002-T2-46)
- [ESA — Unified Synoptics System (presentation)](https://www.slideserve.com/semah/unified-synoptics-system)
- [JPL DESCANSO — ground system architecture paper (PDF)](https://descanso.jpl.nasa.gov/RCSGSO/Paper/A0009Paper.pdf)
- [OpenC3 Docs — Telemetry Screens](https://docs.openc3.com/docs/configuration/telemetry-screens)
- [OpenC3 Docs — Requirements and Design](https://docs.openc3.com/docs/getting-started/requirements)
- [OpenC3/cosmos (GitHub)](https://github.com/OpenC3/cosmos) · [nasa-itc/openc3-cosmos (GitHub)](https://github.com/nasa-itc/openc3-cosmos) · [OpenC3](https://openc3.com/)
- [GMV — hifly family of products](https://www.gmv.com/en-es/products/space/hifly-family-products) · [GMV hifly](https://www.gmv.com/en/products/space/hifly) · [GMV focusSuite](https://www.gmv.com/en-es/products/space/focussuite)
- [SpaceNews — GMV Releases hifly 6.0 Satellite Control System](https://spacenews.com/gmv-releases-hifly-60-satellite-control-system/)
- [Kratos — quantumCMD announcement](https://www.kratosdefense.com/newsroom/kratos-introduces-quantumcmdtm-the-first-command-and-control-system-designed-for-small-satellites-and-fleets-of-small-satellites)

**Situational awareness**
- [Endsley, Bolstad, Jones & Riley — Situation Awareness Oriented Design (HFES 2003, SAGE)](https://journals.sagepub.com/doi/10.1177/154193120304700304)
- [Same paper, ResearchGate PDF](https://www.researchgate.net/publication/237436738_Situation_Awareness_Oriented_Design_From_User's_Cognitive_Requirements_to_Creating_Effective_Supporting_Technologies)
- [Endsley & Jones — Designing for Situation Awareness, 3rd ed. (Routledge)](https://www.routledge.com/Designing-for-Situation-Awareness-An-Approach-to-User-Centered-Design-Third-Edition/Endsley-Jones/p/book/9781032482118)
- [Evaluating Situation Awareness Oriented Design (DiVA, PDF)](https://www.diva-portal.org/smash/get/diva2:823781/FULLTEXT01.pdf)
- [Springer — How User-Centered Design Supports Situation Awareness for Complex Interfaces](https://link.springer.com/chapter/10.1007/978-3-030-47775-2_2)
- [The Designer's Situation Awareness Toolkit (ResearchGate)](https://www.researchgate.net/publication/237460827_The_Designer's_Situation_Awareness_Toolkit_Support_for_User-Centered_Design)

**Colour, contrast and CVD**
- [accessibility.chat — Status Indicators Beyond Color Coding](https://www.accessibility.chat/articles/when-color-coding-fails-why-status-indicators-need-more-than-pretty-colors)
- [ColorArchive — WCAG Color Accessibility Guide](https://colorarchive.org/guides/wcag-color-accessibility-guide/)
- [WebAbility — 8 Colors to Avoid for Color Blindness in Web Design](https://www.webability.io/blog/colors-to-avoid-for-color-blindness)
- [Penn State Accessibility — Color Coding](https://accessibility.psu.edu/color/colorcoding/)
- [accessiBe — Color Blind Accessible Web Design](https://accessibe.com/blog/knowledgebase/accessible-website-for-people-with-color-blindness)
- [Human Standards — Colour & Accessibility](https://www.humanstandards.org/perception/vision/colour-accessibility/)
- [CSSAWWWARDS — Designing for Color Blindness (2026)](https://cssawwwards.com/blog/color-blindness-accessible-design-guide-2026)
- [Medium — Designing Beyond Sight: UX for Color Blind Accessibility](https://medium.com/@taraneyarahmadi/designing-beyond-sight-ux-for-color-blind-accessibility-ac4351e9cce9)

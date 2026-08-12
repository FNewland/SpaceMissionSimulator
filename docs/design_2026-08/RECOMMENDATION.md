# RECOMMENDATION

**Question:** which of the three directions should the SMO Mission Control System take, and how do we get there from a 10,595-line hand-built `index.html` without a big-bang rewrite?

---

## 1. The recommendation in one paragraph

**Adopt Mockup B — the operator console — as the shell and navigation model. Adopt Mockup A's component vocabulary and colour discipline wholesale as the design system that fills that shell. Ship Mockup C's guided layer as a toggle, not a separate product.**

They are not really three alternatives. They are a **shell** (B), a **component library** (A) and a **teaching layer** (C), and the honest answer to "which one" is that B is the right container, A is the right content, and C is the feature that makes the whole thing worth building for an educator rather than an operator. What must be *chosen*, not merged, is B's navigation model over the current fourteen-tab strip — that is the irreversible decision.

---

## 2. Why B as the shell

**It fixes the structural defect, not the surface one.** AUDIT §1.1 identifies fourteen sibling tabs spanning three display levels with no relationship between them, plus six competing navigation surfaces. Mockup A improves the *rendering* of that structure; Mockup B replaces the structure. Overview-first with drill-down is what ISA-101's display hierarchy asks for (RESEARCH §1.2) and what fleet-oriented commercial systems have converged on (§2.4).

**The schematic-as-navigation is the right teaching move.** A student clicks the battery because they are worried about the battery — not a tab labelled `EPS` whose expansion they must first learn. It builds a spatial model of the spacecraft as a side effect of navigating it. This is the single most-liked idea I would expect from a demo, and it is also correct.

**The persistent SA header answers the four questions.** *Can I see it / can I talk to it / is it well / what's next.* That framing is directly Endsley's three levels plus the link state, and it makes the currently-invisible `#sa-bar` (AUDIT §5.1 — the strip is rendered behind the header and never seen) into the most prominent element on the page rather than the most hidden.

**Chrome budget.** B's header + rail is ~120 px of vertical chrome against the current 308 px (AUDIT §5.2), and the log moves from a permanent 160 px drawer to an on-demand panel. That alone makes the product usable on a 1366×768 classroom laptop.

**The honest cost of B.** The schematic is the most expensive artefact to build and maintain, and it is a *bespoke* asset — exactly the thing PRINCIPLES P10 warns about. Mitigation: build the overview schematic as an SVG with `data-param` bindings and a small binding layer, so the *picture* is authored once but the *data* comes from config. Do not build six more of them until the L2 pages exist and are boring.

## 3. What to steal from A

Everything except the light-grey ground. Specifically:

| From Mockup A | Why | Where it lands |
|---|---|---|
| **Grey resting state, colour only for abnormal** | The single highest-value rule in the whole research (RESEARCH §1.3). Kills AUDIT §2.1 (eleven identity hues) and §2.2 (decorative chrome) | Global. B already implements it — `--ok` is deliberately grey, not green |
| **The deviation bar with limit ticks** | Makes `limits.yaml` visible; fixes four audit findings at once (§1.3, §3, §4.2, §7.2) | The core parameter component |
| **Embedded sparklines** | Trend without chrome; replaces the framed three-across Chart.js panels for at-a-glance use | Beside every scalar |
| **The SCOS-2000 alphanumeric display (AND) table** | The workhorse display of real ESOC operations, absent from SMO entirely (RESEARCH §2.1) | One per subsystem, generated from `parameters.yaml` |
| **Priority + rationalisation on every alarm row** | ISA-18.2 (RESEARCH §4.2) | Alarm drawer |
| **The alarm-rate meter with the EEMUA threshold drawn on it** | Makes the flood concept visible and measurable | Alarm drawer header |
| **The greyscale self-test toggle** | Enforces "never colour alone" continuously, and demonstrates it to students | Header, ships in production |
| **Glyph vocabulary ▲ ▼ ⌀ ◇ ○** | Redundant coding that survives CVD and greyscale (RESEARCH §6) | Global |
| **The light theme, as an option** | Projectors and printed lab reports. A's light ground is the authentic HP-HMI look and reads far better on a beamer than any dark theme | Theme toggle, dark default |

## 4. What to steal from C

C is the reason this product is different from a generic MCS, and it should not be a separate build.

| From Mockup C | Ships as |
|---|---|
| **The causal-chain spine** (command → state → telemetry → limit → alarm → procedure) | A real screen, reachable from any alarm ("Show causal chain"). This is the best single teaching artefact in the three mockups |
| **Guided mode** — numbered annotations explaining what and why | A toggle in the header, default ON for a student's first N sessions, forced OFF in assessed runs. Annotation text lives in config next to the display definition |
| **Printed limit values drawn to scale** | Merge into the deviation-bar component as an expanded state (compact bar by default, full labelled scale on focus/hover/drill-down) |
| **"Why this design?" page** | Ship it. It costs one static page and it teaches display standards for free |
| **Cause-grouped flood view** | Driven by `fdir/fault_propagation.yaml`, which already encodes the graph |
| **Explicit command authority** ("func 29 NOT PERMITTED — this step belongs to Payload Ops") | Everywhere a command is gated. Turns a silent permission failure into a coordination lesson |

**What not to take from C:** the annotation density as a default for experienced users, and the editorial serif voice in operational chrome. Guided mode must be genuinely off-able.

## 5. What the three directions cost, honestly

| | A · ISA-101 | B · Console | C · Teaching |
|---|---|---|---|
| Operational correctness | Highest | High | High |
| Demo impact | Lowest | Highest | High |
| Build cost | Low–medium | **Medium–high** (schematic + shell) | Medium (annotation content is writing, not code) |
| Teaching value | Medium (models the standard) | Medium (models the mental model) | **Highest** |
| Risk | Reads as "unfinished" to stakeholders | Schematic becomes a bespoke maintenance burden | Annotations rot out of sync with the UI |
| Config-drivability | **Highest** — it is a component list | Medium — the schematic resists config | Medium |

The blend takes A's correctness and config-drivability, B's impact and structure, C's teaching value — and pays for it with the schematic maintenance risk, which is the one item to watch.

---

## 6. Phased migration from the current `index.html`

The constraint is real: `index.html` is one 10,595-line file with ~7,400 lines of inline JS, no build step, no module system, and it works. A rewrite would take the product offline for a term. Every phase below is independently shippable and independently revertable, and **no phase requires touching more than a few hundred lines at a time.**

### Phase 0 — Stop the bleeding (½ day, ship immediately)

Pure defect fixes from AUDIT §10 P1. No design change, no risk.

1. Give `#sa-bar` a `position: fixed; top: calc(var(--top-bar-h) + var(--pos-bar-h))`, bump `--tab-bar-h`'s sibling offsets and add `--sa-bar-h: 24px` to `#main`'s top calc (`index.html:200, 344, 366`). **The SA strip becomes visible for the first time.**
2. Change `--text-tertiary` from `#6a6a88` to `#9AA0B4` (`:22`). Contrast goes 3.6:1 → ~7:1 across every label in the product. One line.
3. Delete the 6/7/8 px font-size rules; floor everything at 11 px, primary readouts at 15 px (`:840–842` and the inline SVG overrides).
4. Add `@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important } }`.
5. Add the missing `propulsion` option to `#pos-select` (`:945–953`).
6. Recolour chart series away from the status palette (`:6359` etc.) — greys and one blue.
7. Add scroll chevrons or wrap to `#tab-bar` (`:345`).
8. Replace the footer screenshot `<img>` (`:3158`) with text.

*Deliverable: the current UI, materially more usable and accessible, with zero structural change.*

### Phase 1 — Extract the design system (2–3 days)

Create `static/css/tokens.css` and `static/css/components.css`, linked from `index.html` **before** the inline `<style>` so the inline block still wins on conflict. Migrate the `:root` block into `tokens.css`, adopt the PRINCIPLES §P6 palette, and add the new component classes (`.pv`, `.dev`, `.spk`, `.chip`, `.stepper`) *without removing anything*. Same file, two new stylesheets, nothing breaks.

Then create `static/js/components.js` with three pure functions — `renderParam(def, value, limits)`, `renderDeviationBar(...)`, `renderSparkline(...)` — and use them for **one panel only** (EPS Battery, `index.html:1329–1339`). Verify. This is the proof that the vocabulary works against the real WebSocket state object.

*Deliverable: a design system exists, one panel uses it, everything else is untouched.*

### Phase 2 — The limits join (2 days)

Add `GET /api/limits` (or fold `limits.yaml` into the existing state broadcast — check what the server already sends). Join it client-side against `parameters.yaml` metadata to produce, for each parameter: name, id, description, unit, yellow/red bands, display range. **This single data structure is the prerequisite for everything that follows** and it makes AUDIT P1 #4 (limits invisible) fixable anywhere.

Then convert *all* EPS and AOCS panels to `renderParam`. Two tabs, roughly 400 lines of hand-written HTML deleted.

*Deliverable: two subsystem tabs show limits with values. Visible, demo-able win.*

### Phase 3 — The alarm system (1 week)

The highest-value single phase, and it is mostly server-side.

- Add a **priority** field (P1–P4) to the alarm model, mapped from consequence in `alarm_procedures.yaml` rather than from PUS severity.
- Add **shelve** (with timer + auto-unshelve + log), **ack-all**, and an alarm-rate counter.
- Add **cause grouping** from `fdir/fault_propagation.yaml`.
- Replace the floating overlay (`index.html:752–790`) with a docked right panel that does not dim the page, add a focus trap, add keyboard shortcuts (`a` open, `k` ack, `s` shelve).
- Make every other alarm indicator (badges, tab dots, SA strip, contingency banner) a **derived count from this one list** — killing AUDIT §4.1's eight-surface problem.
- Add the rationalisation text (cause / consequence / response / priority basis) to the row.

*Deliverable: alarm management that actually teaches EEMUA 191. Independently valuable even if nothing else ships.*

### Phase 4 — L1 overview + the shell (1–2 weeks)

Build the new overview as a **new tab** (`OVERVIEW 2`) alongside the old one. Both run; instructors compare; students vote. Build the SA header as an enhancement of the now-visible `#sa-bar`. When the new overview wins, make it the default and delete the old one.

The left rail replaces the tab strip *last*, and only once the L2 pages are all component-based — swapping navigation before the pages are consistent just moves the inconsistency.

*Deliverable: overview-first navigation, with a safe A/B period.*

### Phase 5 — The causal chain and guided mode (1 week)

The chain screen is mostly a **query**, not a UI: given an alarm, walk back through the parameter to the limit, to the recent commands affecting that subsystem, to the on-board state changes. Guided-mode annotations go in a `configs/eosat1/mcs/annotations.yaml` keyed by display element id, so instructors can edit them without touching code — and so they can be per-exercise.

*Deliverable: the thing that makes this a teaching product rather than a small MCS.*

### Phase 6 — Config-driven rendering (2–3 weeks, ongoing)

By now every L2 page is a list of component calls with a hard-coded parameter list. Replace the hard-coded list with a read of `displays.yaml` v2 (§7 below). Migrate one subsystem at a time. When the last one migrates, several thousand lines of `index.html` delete themselves.

*Only attempt this after Phase 2 — the limits join is what makes the config expressible.*

### Phase 7 — Split the file

Only now is splitting `index.html` safe and cheap, because most of it is gone. Target: `index.html` under 800 lines (shell + mount points), with `components.js`, `renderer.js`, `alarms.js`, `commanding.js`, `charts.js` and the CSS files beside it. No bundler needed — plain ES modules work in every browser the classroom will use.

### Sequencing summary

```
Ph0 defects ──┐
Ph1 tokens ───┼──► Ph2 limits join ──┬──► Ph3 alarms ──────┐
              │                       │                     ├──► Ph6 config-driven ──► Ph7 split file
              └──► Ph4 shell / L1 ────┴──► Ph5 chain+guided ┘
```

Phase 0 ships this week. Phases 1–3 are each independently valuable and can stop there without leaving the product half-migrated — which is the property that makes this not a big-bang rewrite.

**Consistency guard.** From Phase 1 onward, `tokens.css` should be shared with `smo-simulator/.../instructor/static/index.html` and `smo-planner/.../static/index.html`, which currently use three divergent palettes (AUDIT §8). This costs almost nothing during Phase 1 and becomes expensive later.

---

## 7. Making the UI config-driven: what has to change in the YAML

### 7.1 Diagnosis

`displays.yaml` (678 lines) is inert (AUDIT §9). Its widget vocabulary — `gauge`, `status_indicator`, `value_table`, `line_chart`, `event_log` — is close to OpenC3's but is missing the two components that matter most, and it has drifted out of sync with `parameters.yaml` with nothing to detect it (`sat.mode`, `fdir.level`, `contact.time_to_aos`, `proc.progress`, `eps.eclipse_active` do not exist in the telemetry dictionary).

`positions.yaml` is half-consumed: `visible_tabs` drives tab hiding, everything else (`allowed_services`, `allowed_func_ids`, `overview_subsystems`, `manual_sections`) is either enforced silently or ignored.

### 7.2 `displays.yaml` v2 — the changes

**(a) Add the missing widget types.** The minimum viable set, aligned with OpenC3 and with PRINCIPLES P5:

```yaml
# NEW — the component that makes limits visible
- type: param          # value + unit + deviation bar + sparkline
  parameter: eps.bat_soc
  # label, unit, limits, display range are ALL inherited from
  # parameters.yaml + limits.yaml. Authoring a colour is not permitted.
  trend: 300           # sparkline window, seconds. omit for no sparkline
  emphasis: primary    # primary | normal | dense  → drives type size

- type: param_table    # the SCOS-2000 alphanumeric display
  title: "EPS — all parameters"
  parameters: [eps.*]            # glob against the dictionary
  columns: [name, description, raw, eng, unit, limit_state]

- type: stepper        # ONE component for load shed / mode / TC verify / procedure
  parameter: eps.load_shed_stage
  states: ["L0 NOM","L1","L2","L3"]

- type: schematic      # binds an SVG asset to parameters
  asset: eps_power_distribution.svg
  bindings:
    "#sa-a-current": eps.sa_a_current
    "#bus-voltage":  eps.bus_voltage
    "#line-3":       { parameter: eps.line_current_3, style: node_state }

- type: command        # in-place commanding (PRINCIPLES P9)
  service: 8
  func_ids: [18, 19, 22]
  confirm: arm_execute       # none | confirm | arm_execute

- type: alarm_list
  filter: { subsystem: eps }

- type: projection     # Endsley level 3
  metric: soc_at_eclipse_exit
```

**(b) Declare the display level and the parent.** This is what turns a flat list into a hierarchy:

```yaml
displays:
  eps_overview:
    label: "Electrical Power"
    level: 2                 # 1 overview | 2 subsystem | 3 equipment | 4 diagnostic
    parent: spacecraft_overview
    subsystem: eps
    drill_down:              # which element opens which L3 display
      battery: eps_battery_detail
      line_3:  eps_line_detail
```

**(c) Replace `pages: [ {name, widgets} ]` with an explicit layout.** The current model gives the renderer no way to know how wide anything is:

```yaml
    layout:
      - row:
          - col: { span: 8, widgets: [...] }
          - col: { span: 4, widgets: [...] }
```

12-column grid, one nesting level. Anything more expressive becomes a layout language, which is a trap.

**(d) Forbid presentation attributes.** `range:` and `units:` currently appear on widgets. Delete both — they must come from `parameters.yaml` and `limits.yaml`, or the config drifts (this is exactly OpenC3's rule that limits colouring is automatic from the dictionary, RESEARCH §2.3). Colour is never authorable at all.

**(e) Add validation.** A loader that resolves every `parameter:` against `parameters.yaml` and fails the display (loudly, in the UI, not silently) if it does not exist. Add a unit test that walks `displays.yaml` and asserts every reference resolves — this is a 20-line test that would have caught the existing drift.

**(f) Delete the `positions:` block from `displays.yaml`.** It duplicates and contradicts `positions.yaml`. One file owns positions.

### 7.3 `positions.yaml` — the changes

```yaml
positions:
  eps_tcs:
    display_name: "Power & Thermal"
    callsign: "POWER"                    # NEW — NASA/ESA console discipline
    responsibilities: |                  # NEW — shown on the console, not just in the manual
      Owns the power and thermal budget. Authorises load shedding.
      Coordinates with Payload Ops before removing payload power.
    subsystems: [eps, tcs]

    # NEW — replaces visible_tabs. Positions select DISPLAYS, not tabs.
    displays:
      home: spacecraft_overview          # which L1 this position lands on
      visible: [spacecraft_overview, eps_overview, tcs_overview,
                eps_battery_detail, alarms, procedures, trends, manual]

    # NEW — overview scoping becomes a display concern, not a special case
    overview_emphasis: [eps, tcs]        # these subsystems render at full detail;
                                         # others render as summary tiles (NOT hidden —
                                         # a Flight Director asking "what's TT&C doing?"
                                         # must not get a blank box)

    authority:                           # promoted from bare lists, and now DISPLAYED
      services: [1, 3, 5, 8, 17, 20]
      func_ids: [16-25, 40-49, 81-82]    # ranges, not enumerations
      hazardous_requires: arm_execute
      coordinate_with: [payload_ops, flight_director]
```

Two behavioural changes this enables, both of which are teaching wins:
- **Authority is visible**: the console shows "you may send S8 func 16–25; func 29 belongs to Payload Ops" instead of silently shortening a dropdown (AUDIT §1.3).
- **Hidden ≠ absent**: non-owned subsystems render as read-only summary tiles rather than disappearing, which is how a real multi-position control room works.

### 7.4 New file: `configs/eosat1/mcs/style.yaml`

The ISA-101 phase-1 artefact (RESEARCH §1.1). One file, shared by all three UIs, defining the palette, type scale, glyph vocabulary, alarm priority colours and the rule that colour is reserved for abnormal conditions. Generated into `tokens.css` at build or served at runtime. Its existence is what stops the drift documented in AUDIT §2.1 and §8 from happening again — and it is itself a teaching artefact a student can read.

### 7.5 New file: `configs/eosat1/mcs/annotations.yaml`

Guided-mode text, keyed by display id and element id, so instructors author lessons without touching code and can vary them per exercise.

---

## 8. What I would do first, if there is only one week

1. **Phase 0** (½ day) — eight small fixes, including making the SA strip visible and fixing the contrast of every label in the product.
2. **The limits join + the deviation-bar component**, applied to EPS only (2 days). This is the demo that sells the whole direction, and it makes the core teaching concept visible for the first time.
3. **Alarm priority + rationalisation text + one alarm surface** (2 days, mostly server-side).

That is a week, it is all independently shippable, and it delivers the three highest-value items in the audit without committing to the shell rewrite.

---

## 9. Files delivered

| File | Contents |
|---|---|
| `/root/design/AUDIT.md` | Part 1 — full audit with `file:line` references, prioritised defect list |
| `/root/design/RESEARCH.md` | Part 2 — ISA-101, EEMUA 191/ISA-18.2, SCOS-2000, OpenC3, GMV/Kratos, Endsley, Nielsen, CVD/dark-mode, with ~45 cited URLs |
| `/root/design/PRINCIPLES.md` | Ten principles + seven non-negotiables, with the appeal/correctness trade-offs called out |
| `/root/design/mockup_a_isa101.html` | Direction A — ISA-101 high-performance HMI (light + dark, greyscale test) |
| `/root/design/mockup_b_console.html` | Direction B — operator console, schematic-as-navigation, SA header, alarm drawer |
| `/root/design/mockup_c_teaching.html` | Direction C — causal-chain spine, printed limit scales, guided annotation mode |
| `/root/design/RECOMMENDATION.md` | This document |

All three mockups are standalone: no network requests, no external CSS or JS, inline fake-but-real telemetry using the actual parameter names, IDs, units and limit values from `configs/eosat1/telemetry/parameters.yaml` and `configs/eosat1/mcs/limits.yaml`. Open them directly in a browser. Each has a **GREYSCALE TEST** button — press it to verify the "never colour alone" rule holds.

**No file under `/root/smo` was modified.**

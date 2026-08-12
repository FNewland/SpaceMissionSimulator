# AUDIT — SMO Mission Control System operator UI

**Subject:** `/root/smo/packages/smo-mcs/src/smo_mcs/static/index.html` (10,595 lines; single hand-built file — HTML + `<style>` 10–843 + `<script>` ~3,180–10,595)
**Comparators:** `/root/smo/packages/smo-simulator/src/smo_simulator/instructor/static/index.html` (2,460 lines), `/root/smo/packages/smo-planner/src/smo_planner/static/index.html`
**Unused declarative layer:** `/root/smo/configs/eosat1/mcs/displays.yaml` (678 lines), `positions.yaml` (93), `limits.yaml` (119)
**Date:** 2026-08-12. Read-only audit — no file in `/root/smo` was modified.

---

## 0. Executive summary

The MCS is a genuinely capable console. It has more real operational content than most teaching simulators: PUS S3/S5/S6/S9/S11/S12/S15/S19 panels, a real archive-backed trends tab, ground-limit checking, a procedure engine, GO/NO-GO polling, shift handover, delayed-TM playback. **The content is not the problem. The problem is that all of it is presented at one flat level of emphasis, in one visual register, at 9–10 px, in a palette where every subsystem gets its own saturated colour.**

Three structural faults dominate:

1. **No display hierarchy.** Fourteen sibling tabs, each an unbounded vertical scroll of equally-weighted panels. There is no Level-1 overview that answers "is the spacecraft OK?" without reading, and no drill-down relationship between levels. (ISA-101 §display hierarchy; see RESEARCH.md.)
2. **Colour is spent on decoration, so it cannot signal.** Green/amber/blue/violet/indigo/orange are used for *identity* (which subsystem, which section header, which panel title) as well as for *state*. A green box on the EPS diagram means "nominal"; a green label on the overview diagram means "TT&C". When something goes wrong there is no unused colour left to shout with.
3. **The declarative display layer is dead.** `displays.yaml` defines 8 displays × ~20 pages of widgets, and `positions.yaml` defines per-position `visible_tabs`. Only the *tab-visibility* half is consumed (`applyPositionFilter`, line 3631). Every widget, gauge and table in the UI is hand-written HTML with a hard-coded element id. The config is a maintenance liability that promises config-driven displays and does not deliver.

Plus one outright layout bug that hides an entire designed feature — see §5.1.

---

## 1. Information architecture

### 1.1 Inventory of the navigation surfaces

There are **six** concurrent, competing navigation mechanisms:

| # | Surface | Location | Items |
|---|---|---|---|
| 1 | Position selector | `index.html:943–956` | 6 of the 7 positions in `positions.yaml` |
| 2 | Primary tab bar | `index.html:959–974` | 14 tabs |
| 3 | PUS sub-tab bar | `index.html:2232–2242` | 7 sub-tabs (S3, S11, S6, S15, S12, S19, S9) |
| 4 | Bottom-panel tabs | `index.html:3095–3100` | 5 (EVENTS / TM PACKETS / CMD HISTORY / HANDOVER / GO-NO-GO) |
| 5 | Alarm journal overlay | `index.html:752–758` CSS, button at `887` | modal-ish right-hand drawer |
| 6 | External pages | `index.html:884–893` | `/dumps`, `/reports` in new browser tabs |

A student must learn six different "where do I click" idioms. Nothing tells them the relationship between them — e.g. that CMD HISTORY (bottom panel) and COMMAND HISTORY (ARCHIVED) (trends tab, `2049`-ish region, markup at `3053–3062`) are two different views of nearly the same thing, one volatile and one persisted.

### 1.2 Tab inventory and what is inside each

| Tab | Pane | Content |
|---|---|---|
| OVERVIEW | `986–1200` | Position blurb, orbit bar, spacecraft-bus SVG, contact-window timeline, ground-track world map, system overview board, contact schedule |
| EPS | `1202–1390` | Load-shed stepper, power-distribution SVG, battery panel, bus panel, power-lines table, 3 charts, dump playback, power-budget board, events |
| TCS | `1392–1509` | Thermal-map SVG, panels, 2 charts, events |
| AOCS | `1511–1721` | Equipment schematic SVG, mode chain, attitude/rates, star trackers, CSS, MTQ, GPS, RW cards, orbital data, 2 charts, events |
| TTC | `1723–1907` | RF-chain SVG, TM-rate ladder, dual-GS cards, 2 charts, events |
| PAYLOAD | `1909–1961` | Status panels, generated command console, 1 chart, events |
| OBDH | `1963–2148` | Dual-OBC + CAN-bus SVG, TC counters, 1 chart, events |
| COMMANDING | `2150–2229` | Command builder, structured fields, verification log |
| PUS SERVICES | `2231–2599` | 7 sub-panels (S3/S11/S6/S15/S12/S19/S9) |
| FDIR | `2803–2963` | State strip, autonomy panel, monitoring table, journal |
| PROCEDURES | `2601–2756` | Browser, execution status, log, procedure builder |
| ON-DEMAND TM | `2758–2793` | Parameter poke |
| TRENDS | `2964–3078` | Archive query, alarms/events in window, archived commands, playback |
| MANUAL | `3079–3092` | TOC + content |

**Observation.** Six of the fourteen tabs (the subsystem tabs) share an identical *skeleton* — schematic, panels, charts, events — but no shared *component*. Each is bespoke markup. That is why they drift: EPS gets a "Request S15 TM Dump" button (`1356–1359`), TCS does not; EPS gets a power-budget board, AOCS gets an orbital-data panel that duplicates the overview orbit bar and then apologises for it in prose ("Full ground track map available on the OVERVIEW tab", `1712`).

### 1.3 Concrete "a student cannot find it" problems

- **Ground limits are invisible as a concept.** `limits.yaml` defines yellow/red bands for 50 parameters. In the UI they surface only as a colour+background on the number itself (`.lim-red`/`.lim-yellow`, `171–179`). Nowhere can a student see *what the limit is*. A value of `22.8 V` turns yellow with no indication that yellow_low is 23.0 and red_low is 22.0. **This is the single most important teaching failure in the UI**: limit checking is the core MCS concept and the numbers behind it are unreachable from the console.
- **No parameter search.** With ~500 parameters in `parameters.yaml`, the only free-text find is `#trend-filter` (`2989`), buried in TRENDS, and it filters *archived* parameters only.
- **PUS service numbers as navigation.** The sub-tab bar (`2232`) is labelled by service number. A student who wants "set up a monitoring check" must already know that is S12. The manual is in a different tab.
- **Procedures are not reachable from the alarm that needs them** except via a small `.alarm-proc-btn` (`193–198`) inside the alarm-journal overlay. The FDIR tab, the alarm badge and the contingency banner do not link to a procedure.
- **`on_demand: true` parameters** (dozens in `parameters.yaml`, e.g. `0x010A`, `0x0216`, `0x012B–0x0130`) are silently absent from the normal panes. A student reading a panel cannot tell "this parameter does not exist" from "this parameter is not being downlinked in the current HK SID". Both render `--`.
- **Position ≠ what you can command.** `positions.yaml` encodes `allowed_services` and `allowed_func_ids` per position, which is excellent pedagogy. The UI shows only `pos-info` = a comma list of subsystems (`3639–3640`). A student on the `propulsion` position sees "prop" and has no way to discover they may send func 42 but not func 65.
- **`propulsion` position is missing from the selector.** `positions.yaml` defines seven positions; the `<option>` list at `945–953` has six. Propulsion is unreachable.

---

## 2. Visual language

### 2.1 Palette

Tokens at `index.html:13–41`:

```
--surface-base #08080f  --surface-primary #10101e  --surface-secondary #181828
--surface-tertiary #222236  --surface-overlay #2e2e46
--text-primary #eaeaf2  --text-secondary #a8a8c0  --text-tertiary #6a6a88
--status-nominal #00C896  --status-warning #F0B429  --status-alarm #FF6B6B
--status-stale #9999B0   --status-commanding #6BB3FF
```

Five status colours is a reasonable base. The problem is the **eleven further hard-coded hues** used outside the token system:

| Hue | Meaning here | Where |
|---|---|---|
| `#F59E0B` amber | "EPS", and also every power wire | `1025`, `1289`+ (marker `ep-a`), `1233` |
| `#6BB3FF` blue | "AOCS" *and* "commanding" *and* "GENERATION" *and* "BUS/STORAGE" *and* solar-array fill | `1034`, `1231`, `1233`, `1243` |
| `#A78BFA` violet | "OBDH", and also "MAG" on the AOCS schematic | `1043`, `1620` |
| `#ff9090` salmon | "TCS" — perceptually a desaturated `--status-alarm` | `1052` |
| `#00C896` green | "TT&C" *and* "nominal" | `1061`, `434` |
| `#818cf8` indigo | "PAYLOAD" | `1070` |
| `#FF8C00` orange | FDIR "subsystem" level, load-shed L2 | `126`, `160` |
| `#FF4444` | load-shed L4 | `127` |
| `#B0B0C0` | Chart.js tick/legend colour, not a token | `3274–3276` |

**Consequence:** `#00C896` simultaneously means "this subsystem is TT&C" (overview diagram) and "this equipment is nominal" (`.svg-eq-box.nominal`, `833`). On the overview SVG both usages appear within 300 px of each other. `#ff9090` (TCS identity) is close enough to `#FF6B6B` (alarm) that a peripheral glance at the overview reads as an alarm on a nominal spacecraft.

### 2.2 Chrome that competes with data

- Full-page dot-grid texture (`54–62`) — a persistent low-contrast pattern under every panel.
- Animated gradient accent stripe on the top bar (`272–277`).
- `text-shadow: 0 0 10px` glow on the UTC clock (`291`), the logo (`281`), the selected tab (`358`), the contact value (`470`), alarm values (`413`).
- Six `box-shadow` glows keyed to status colour (`--nom-glow` etc., `38–41`), applied to LEDs (`434–437`), mode chips (`482`), lock steps (`444`), GS dots, alarm badges.
- Gradient panel headers (`382`), gradient top bar (`267`), gradient contingency banner (`237`).
- Per-diagram SVG background `<pattern>` dot fills (`1224–1227`, `1397`).

Every one of these is a small amount of visual energy spent on decoration. Cumulatively they raise the noise floor so that the *actual* signals — a value going yellow, an LED going red — have less headroom to stand out. This is the classic "looks impressive / performs badly" trap that ISA-101 exists to counter.

### 2.3 Typography and density

- Body: `font-size: 0.75rem` = **12 px**, monospace, for everything (`50`).
- Panel headers: **9 px** with `letter-spacing: 2px` (`385–386`). All-caps 9 px monospace with 2 px tracking is at the edge of legibility on a 1080p laptop and well past it on a 1366×768 classroom machine.
- Data labels `.lbl`: **10 px** in `--text-tertiary` (`408`).
- Data values `.val`: **11 px** (`409`).
- Table cells: **10 px**, headers **9 px** (`452–458`).
- SVG: `.svg-label` **9 px**, `.svg-val` **8 px**, `.svg-label-sm` **7 px** (`840–842`), with individual overrides down to **6 px** (`1620`: `font-size:6px` for the MAG label).

ISA-101 style guidance is ~14 px minimum for primary readouts. **The most important number on the EPS page — battery SoC on the schematic (`eps-svg-bat-soc`, `1281`) — is rendered at 8 px.** The *least* important text on the page (the "Content generated with AI assistance" footnote, `3159`) is 9 px. There is essentially no typographic hierarchy: importance is not encoded in size anywhere except the top-bar clocks.

### 2.4 LED / status idioms — inconsistent between tabs

The codebase contains **seven distinct ways of showing a binary/tri-state**:

1. `.led` circles with `.on/.off/.warn/.alarm` (`428–437`) — EPS UV/OV flags, CSS valid.
2. `.svg-eq-box` fills with `.nominal/.warning/.alarm/.off/.active` (`832–837`) — all six schematics.
3. `.tag` pills `.nominal/.warn/.alarm/.stale` in panel headers (`389–392`).
4. `.mode-chip` chain, active = green border + glow (`477–484`) — AOCS mode only.
5. `.lock-step` chain `.locked/.unlocked` (`440–446`) — TTC lock chain only.
6. `.lsi-step` stepper `.active-0…4` (`117–128`) — EPS load shed only.
7. Bare text coloured by `.val-nom/.val-warn/.val-alarm/.val-cmd` (`411–414`).

Idioms 4, 5 and 6 are all "a chain of boxes where one is highlighted" and all look different. Idiom 1 vs 7: whether a flag appears as an LED or a coloured word is arbitrary — EPS UV/OV are LEDs (`1341–1342`), AOCS ST1 status is text (`1651`), even though both are enumerations.

`.led.off` uses `border-style: dashed` and `.led.warn` uses `border-style: double` (`435–436`). That is a good instinct — redundant non-colour coding — but a 1 px dashed vs double border on a **10 px circle** is invisible at normal viewing distance. It is redundant coding in theory only.

### 2.5 Motion

Three infinite animations: `blink` 1 s on `.led.alarm` (`437`), `blink` 1.5 s on the FDIR system-level value (`163`) and the load-shed top badge (`131`), `contingency-pulse` 2 s on the banner (`85–88`), `alarm-flash` on fresh alarm rows (`188–192`) and on the count badge (`193`). ISA-101 and EEMUA 191 both restrict blinking to *unacknowledged* high-priority alarms; here `.led.alarm` blinks forever regardless of acknowledgement state, so a known, acked, being-worked condition keeps flashing and trains students to ignore flashing.

---

## 3. Charting

`CHART_OPTS_BASE`, `index.html:3270–3277`; `createTimeChart`, `3283–3301`.

- **X axis is `display: false`** (`3273`) and is a synthetic monotonic counter (`chartTimeCounter`, `3281`), not a timestamp. **Every live chart in the product is a plot with no time axis.** A student cannot say "that dip was four minutes ago" or correlate two charts. The trends tab does it properly against the archive; the six subsystem tabs do not.
- **120-point ring buffer** (`MAX_CHART_POINTS = 120`, `3280`) at ~1 Hz = a two-minute window, silently. Not labelled anywhere.
- **No limit bands on live charts.** `limits.yaml` yellow/red bands exist and the trends tab draws them (`#trend-bands`, `2994`; `9700`), but the live per-subsystem charts do not. So the operator's primary trend view omits the very thing that decides whether the trend matters.
- **Series colours reuse the status palette.** `eps-chart-power` draws "Generated" in `#00C896` and "Consumed" in `#FF6B6B` (`6359`). A perfectly nominal power chart therefore renders a large red line. Same at `6441–6445` (Bat HTR red), `6568` (Roll red).
- **Legend at 9 px, ticks at 9 px, `#B0B0C0` hard-coded** (`3274–3276`) — outside the token system, so a theme change breaks it.
- Charts are `<canvas>` in a `.chart-container`; three-across on EPS (`1362–1366`) means each is ~1/3 of viewport width minus padding, i.e. ~380 px on a 1366-wide laptop, with 9 px ticks.
- **No sparklines anywhere.** Every trend is a full framed panel with a title, border, shadow and legend. There is no lightweight "value + its recent history" primitive, which is the single highest-value HMI component for a dense monitoring screen.

---

## 4. Alarms and limits

### 4.1 Where alarm state lives

Alarm/limit state is spread across **eight** surfaces with no single authority:

| Surface | Line | What it shows |
|---|---|---|
| `#alarm-badges` (nom/warn/alarm counts) | `875–879` | Counts only |
| `#loadShedTopBadge` | `883` | EPS load shed |
| `#sa-unacked` in the SA strip | `931–934` | Unacked count |
| Contingency banner | `977–980` | One text line |
| Per-tab `.tab-dot` (`361`) | `960–973` | Per-subsystem colour dot |
| Alarm journal overlay | `752–790` CSS | The actual list |
| FDIR tab strip + journal | `2805–2963` | On-board FDIR view |
| Inline `.lim-red/.lim-yellow` on values | `171–179` | Per-parameter |

Nothing reconciles them. The FDIR tab shows *on-board* autonomy state; the alarm journal shows *ground* limit violations plus S12 plus S5; the badges count something else again. A student asking "how many things are wrong?" gets up to four different answers.

### 4.2 Specific alarm-presentation problems

- **No priority model.** `.alarm-sev.s3` = warning, `.alarm-sev.s4` = alarm (`768–770`). Two levels, derived from PUS event severity, not from *operator urgency*. ISA-18.2 wants priority assigned by consequence × response time, and a documented cause/consequence/action per alarm. `alarm_procedures.yaml` (110 lines) has some of this and it is not surfaced in the journal row.
- **No shelving, no suppression, no flood handling.** The journal is an append-only list. In a cascade scenario (`configs/eosat1/scenarios/aocs_sensor_cascade.yaml`, `eps_solar_panel_loss.yaml`) the student gets an unbounded flood with no way to shelve a known-bad parameter, no "N alarms in the last minute" flood indicator, and no grouping by root cause — despite `configs/eosat1/fdir/fault_propagation.yaml` existing and encoding exactly that causal structure.
- **Acknowledgement is per-row only** (`.alarm-ack-btn`, `776`). No "ack all", no ack from the banner, no ack from a keyboard shortcut. In a flood the student clicks dozens of 9 px buttons.
- **Acked alarms drop to `opacity: 0.45`** (`781`) — combined with `--text-secondary #a8a8c0` on `--surface-primary #10101e`, an acked row's value text lands around **2.3:1** contrast. Effectively unreadable, when "what did I already ack" is exactly what you need during a cascade.
- **The journal is a floating overlay** at `top:60px; right:16px; width:520px` (`754`) over a full-screen dim (`751`). You cannot look at the alarm and the parameter it refers to at the same time. Fixed 520 px width also means the parameter/value columns (`min-width:70px`, `flex:1`) truncate on long names.
- **Limit bands are never shown numerically.** See §1.3.
- **Colour-only severity.** `.alarm-sev.s3` amber vs `.alarm-sev.s4` red, both rendering short text ("WARN"/"ALRM"). The text does carry it, but the *row* differs only by a left border colour (`767`).

---

## 5. Layout, density and the laptop test

### 5.1 The SA strip is invisible — layout bug

`#sa-bar` (`index.html:912–941`, CSS at `200–205`) is the designed "everything an operator should read without changing tab" strip: LINK freshness, NEXT AOS, TM TIER active-vs-commanded, STORE fill, UNACK count, SPEED, SUN.

It has **no `position` rule**. `#top-bar`, `#pos-bar`, `#tab-bar`, `#main`, `#bottom-panel` and `#status-bar` are all `position: fixed` (`63`), so they are out of normal flow. `#sa-bar` is therefore the first in-flow element in the body and lays out at document `top: 0`, **underneath `#top-bar`** (`z-index: 100`, `269`) which is 52 px tall and opaque. `#sa-bar` is ~20 px tall.

Additionally `#main`'s top offset is `calc(--top-bar-h + --pos-bar-h + --tab-bar-h)` (`366`) — it reserves no space for the SA strip at all, confirming the strip was never in the layout budget.

**Net effect: the single most valuable piece of situational awareness in the product is rendered behind the header and never seen.** All the JS that maintains it (`renderSaPass` etc., `9992`) runs into a hidden element.

### 5.2 Fixed vertical chrome cost

`--top-bar-h 52 + --pos-bar-h 32 + --tab-bar-h 36 + --bottom-panel-h 160 + --status-bar-h 28 = 308 px` of permanent chrome (`31–36`). On a 1366×768 laptop with browser chrome (~120 px), usable content height is roughly **340 px**. The EPS pane's first element alone (`#eps-diagram`, `max-height:200px`, `1220`) plus the load-shed strip consumes most of it. The student scrolls immediately, on every tab, forever.

The 160 px bottom log panel is fixed and not collapsible. There is no drag handle, no collapse button, no `--bottom-panel-h` toggle.

### 5.3 Responsive behaviour

Only two breakpoints (`651–657`): at ≤900 px `.grid-2x3`/`.grid-1x4` go to 2 columns; at ≤600 px everything goes to 1. Between 900 px and, say, 1500 px — **the entire classroom-laptop range** — nothing adapts. `.grid-1x3` (EPS charts, `399`) and `.grid-1x2` are not in the 900 px rule at all, so three charts stay three-across down to 600 px.

`#tab-bar` uses `overflow-x: auto` (`345`). Fourteen tabs × ~110 px ≈ 1,540 px, so **on any screen under ~1550 px the last tabs (TRENDS, MANUAL) are scrolled off with no visible affordance** — the scrollbar is styled to 5 px (`65`) and there are no chevrons.

`#alarm-journal-panel` is `width: 520px` fixed with `right: 16px` (`754`) — fine, but `.verif-reason` `max-width: 340px` with ellipsis (`556–557`) means command-rejection reasons are truncated and only recoverable by hover title.

`.manual-layout` height is a `calc()` subtracting all six chrome heights (`678`); if any of those tokens change the manual scroll breaks silently.

---

## 6. Command console

Markup `2150–2229`; builder JS around `4300–4400`.

- **The command builder lives in its own tab.** To send a command you leave the telemetry you are commanding against. There is no command affordance on the EPS/AOCS/TCS/TTC/OBDH panes at all — except PAYLOAD, which uniquely got a generated in-pane console (`1942–1951`, "Phase E WP-5"). That inconsistency is itself a finding: one of six subsystem tabs can command in place.
- **No confirmation for hazardous commands.** `.btn-danger` (`553–554`) styles destructive actions, but the guard is visual only; nothing in the builder path requires a two-step arm/execute. For a teaching product modelling real ops this is a missed lesson — real MCSs arm-then-fire.
- **Command → effect is not traceable in one place.** Send is in COMMANDING; acceptance/execution verification (S1) appears in the verification log (same tab) *and* the bottom CMD HISTORY *and* the archived command history in TRENDS; the resulting telemetry change appears on the subsystem tab; the resulting event appears in the bottom EVENTS log; a resulting limit violation appears in the alarm journal overlay. **Five surfaces in four tabs to follow one command.** This is the causal chain the product exists to teach, and the UI dismembers it.
- `Enter` sends the command when focus is in the data field (`4372`) with no confirm — combined with the byte-length chip (`.cmd-bytes-chip`, `740–746`) being advisory only.
- Field labels are 9 px uppercase `--text-tertiary` (`574`) — see contrast finding below.
- `positions.yaml` `allowed_func_ids` gating is enforced but never *explained*: a student on the wrong position sees a shorter dropdown, not a message.

---

## 7. Accessibility

Credit where due: the file has a skip link (`849`), `role="tablist"/"tab"/"tabpanel"`, `aria-selected`, arrow-key tab navigation (`3748–3760`), `aria-live` on the log and alarm badge, `visually-hidden` text mirrors for LEDs (`1341–1342`, `1663`), `role="meter"` with `aria-valuenow` on gauges, and `:focus-visible` outlines (`66`). This is far better than typical. The remaining problems are specific.

### 7.1 Contrast (WCAG 2.2 AA = 4.5:1 for text < 18.66 px bold / 24 px)

| Pair | Where | Ratio | Verdict |
|---|---|---|---|
| `--text-tertiary #6a6a88` on `--surface-primary #10101e` | `.data-row .lbl` (10 px, `408`), `.panel-hdr` label text, `.cmd-builder label` (9 px, `574`), `.sa-lbl` (9 px, `206`), `#status-bar .sbl` (`566`) | **≈3.6:1** | **Fail.** This is the colour of *every field label in the product.* |
| `--text-tertiary` on `--surface-secondary #181828` | table headers (`454`), `.lsi-label` (`109`) | **≈3.4:1** | **Fail** |
| `.svg-label-sm` `--text-tertiary` 7 px | `841` | ≈3.6:1 at 7 px | **Fail, badly** |
| Acked alarm row (`--text-secondary` × `opacity .45`) | `781` | **≈2.3:1** | **Fail** |
| `--status-nominal #00C896` on `#10101e` | values, LEDs | ≈8.7:1 | Pass |
| `--status-warning #F0B429` on `#10101e` | ≈10.0:1 | Pass |
| `--status-alarm #FF6B6B` on `#10101e` | ≈6.8:1 | Pass |
| `.btn-primary` `#06080f` on `#6BB3FF` | `548` | ≈9.6:1 | Pass |

The status colours are fine. **The failure is systematic in the "quiet" text** — labels, units, table headers, SVG annotations — which is exactly the text a student needs while learning what things are called.

### 7.2 Colour-only encoding

- `.tab-dot` (`361`) — per-tab health is encoded **purely** as the colour of a 5 px circle. No shape, no text, no count. This is the only per-subsystem health signal in the persistent chrome.
- `.svg-eq-box` variants (`832–837`) differ only in fill+stroke hue. On six schematics, equipment health is colour-only. Under deuteranopia `#00C896` (nominal) and `#F0B429` (warning) converge; under protanopia `#FF6B6B` (alarm) darkens toward the nominal green's apparent lightness.
- `.gauge-fill.nom/.warn/.alarm` (`419–422`) — bar fill colour only; no threshold ticks on the bar, so the *position* of the fill carries no limit information either.
- `.contact-pass` blocks on the timeline are coloured per ground station with a legend (`796`, `1145`) — colour-only station identity.
- `.log-sev` classes (`535–538`) — the severity *word* is present, so this one is fine.

### 7.3 Keyboard

`initKeyboardNav` (`3885–3931`) provides: `?` help, `Escape`, `F6` region cycling, `←/→` tab cycling (`3748`). Gaps:

- **No shortcut for the alarm journal, no ack shortcut, no "go to alarm".** The primary abnormal-condition workflow is mouse-only.
- `F6` cycles a hard-coded `regions` array (`3887`) that does **not include `#sa-bar`** or the alarm journal — consistent with §5.1, the SA strip was never wired into anything.
- The alarm journal (`#alarm-journal-panel`) is `role`-less and has **no focus trap and no `aria-modal`**, yet it is rendered over a full-screen dimming overlay (`751`). Keyboard focus stays behind it.
- `#kbd-help-dialog` *does* have `role="dialog" aria-modal="true"` (`3167`) but also no focus trap — only an initial `.focus()` (`3898`).
- Tabs are reachable but the 14-tab overflow (§5.3) means arrow-key navigation can move focus to an off-screen tab with no scroll-into-view.
- `onclick="…"` inline handlers on non-button elements appear in several places (e.g. `1357`, `3137`) — those specific ones are `<button>`s, which is fine, but the pattern is inconsistent with the `addEventListener` code elsewhere and makes the file harder to reason about.

### 7.4 Other

- The blinking `.led.alarm` (`437`) at 1 Hz and `alarm-flash` at ~1.1 Hz (`189`) are below the 3 Hz photosensitivity threshold, so they are safe — but there is **no `prefers-reduced-motion` media query anywhere in the file**, so users who ask for reduced motion still get five infinite animations.
- The footer contains a **screenshot file used as a logo**: `<img src="/static/Screenshot 2026-03-10 at 2.33.26 PM.png" alt="AI Generated Content" style="height:20px">` (`3158`). A raster screenshot with a space-containing filename, in the permanent chrome of an operational console.
- No `<title>`-level or landmark description of the *current* spacecraft state for screen readers; `aria-live` regions are attached to logs (verbose) rather than to state changes (useful).

---

## 8. Consistency between the three UIs

| | MCS | Instructor | Effect |
|---|---|---|---|
| Background | `#08080f` violet-black | `#070b12` blue-black | Different products |
| Accent | `#6BB3FF` | `#00c8f0` cyan | Different products |
| Nominal | `#00C896` | `#00e07a` | Two greens |
| Warning | `#F0B429` | `#ffc107` | Two ambers |
| Alarm | `#FF6B6B` | `#ff3b3b` | Two reds — the instructor's is far more saturated |
| Token names | `--surface-*`, `--status-*` | `--bg-*`, `--green/--amber/--red` | No shared vocabulary |

(Instructor tokens at `smo-simulator/src/smo_simulator/instructor/static/index.html:10–35`.)

An instructor demonstrating on the projector and a student on the console are looking at **different colours for the same states**. Nothing is shared — not a stylesheet, not a token file, not a component.

---

## 9. The dead config layer

`displays.yaml` (678 lines) defines a widget vocabulary that already almost matches OpenC3's: `gauge`, `status_indicator`, `value_table`, `line_chart`, `event_log`, with `range`, `units`, `duration_s`, `label`. It defines a `displays:` map (8 named displays, each with `pages:` → `widgets:`) and a `positions:` map (per-position pages).

**None of it renders.** `positions.yaml`'s `visible_tabs` is consumed by `applyPositionFilter` (`3631–3690`) to hide tab buttons; everything else — every `display`, every `page`, every `widget` — is inert. Several parameters referenced in `displays.yaml` (`sat.mode`, `fdir.level`, `contact.time_to_aos`, `proc.progress`, `eps.eclipse_active`) are *display-model* names that do not appear in `telemetry/parameters.yaml` at all, so the config has also drifted out of sync with the telemetry dictionary with nothing to detect it.

There is a real risk here beyond tidiness: a maintainer editing `displays.yaml` to change the UI will observe no effect and conclude the system is broken.

---

## 10. Prioritised defect list

**P1 — fix regardless of which design direction is chosen**

1. `#sa-bar` is invisible (§5.1) — `index.html:200`, `366`, `912`.
2. `--text-tertiary` fails contrast for every label in the product (§7.1) — `index.html:22`.
3. Live charts have no time axis and no limit bands (§3) — `3273`, `6358–6360`.
4. Ground limit values are never displayed (§1.3, §4.2) — `limits.yaml` unexposed.
5. Tab bar overflows below ~1550 px with no affordance (§5.3) — `345`.
6. `propulsion` position missing from selector (§1.3) — `945–953`.
7. Chart series use status colours (`#FF6B6B` for a nominal "Consumed" trace) (§3) — `6359`.

**P2 — structural, addressed by the redesign**

8. No display hierarchy / no Level-1 overview (§1.1).
9. Colour used for identity and state simultaneously (§2.1).
10. Seven status idioms (§2.4).
11. Alarm state split over eight surfaces; no priority, shelving or flood handling (§4).
12. Command → effect chain split over five surfaces (§6).
13. 308 px of permanent chrome, non-collapsible bottom panel (§5.2).
14. Only one of six subsystem tabs can command in place (§6).

**P3 — hygiene**

15. Screenshot-as-logo in the footer (`3158`).
16. No `prefers-reduced-motion`.
17. Alarm journal has no focus trap.
18. Instructor and MCS palettes diverge (§8).
19. `displays.yaml` inert and drifted (§9).
20. Blink on `.led.alarm` is not gated on acknowledgement (§2.5).

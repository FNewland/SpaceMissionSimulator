# SMO UI REORGANISATION — EXECUTION PLAN (2026-08)

Owner-accepted design direction: **Mockup B console shell + Mockup A ISA-101
component discipline + Mockup C teaching layer as a toggle.** Constraints: no
build step; classic pages byte-untouched; `SMO_UI=modern|classic` switch, modern
default (flipped only after verification passes); ground tools
spacecraft-agnostic; planner `/wide` is a distinct non-interactive wall layout;
instructor console gets the full treatment; **no loss of functionality** — the
functional inventory is the acceptance checklist.

The full inventory (three reports: functional inventory, sim-coupling audit,
static-serving survey) is at
`/root/.claude/projects/-home-claude/73ecdd67-1ca6-5343-9b56-0d47d8fb1206/tool-results/toolu_01AaK5m2qD6hzYeJji1Frbbc.json`
(read in chunks). Referenced below as "Report 1/2/3".

---

## 1. FILE / ROUTE ARCHITECTURE

### 1.1 New files

**Shared design system — one canonical runtime copy, served from `smo-common` package data.**

```
packages/smo-common/src/smo_common/ui/
    design-system.css      # tokens + component classes (§3)
    components.js          # ES module: pure render functions (§3)
    format.js              # ES module: time/units/CUC/MET formatters
    glyphs.svg             # inline-able symbol sprite (up/down/stale/commanded/off + subsystem monograms)
```

Every server mounts these at the same URL:

```python
import smo_common
app.router.add_static("/static/shared/", Path(smo_common.__file__).parent / "ui")
```

Rationale: `smo_common` is already an installed dependency of all three servers;
the repo's symlink history is bad (all three `static/vendor` symlinks dangle in
some checkouts); a single mount makes drift impossible; split deployments still
work because the files ship in the smo-common install. Plain files, no build.

**Per-package modern pages** (classic files untouched, byte-for-byte):

```
packages/smo-mcs/src/smo_mcs/static/
    index.html                    # CLASSIC — DO NOT TOUCH
    modern/index.html             # new shell + all pages (one HTML)
    modern/mcs.js                 # page logic (ES module)
    modern/schematics.svg         # L1 bus schematic + L2 schematics as <symbol>s with data-param attrs
    modern/why-this-design.html   # Mockup C static teaching page

packages/smo-planner/src/smo_planner/static/
    index.html                    # CLASSIC — DO NOT TOUCH
    modern/index.html             # interactive planner
    modern/planner.js
    modern/wide.html              # DISTINCT wall-display page (own layout, not a class toggle)
    modern/wide.js

packages/smo-simulator/src/smo_simulator/instructor/static/
    index.html                    # CLASSIC — DO NOT TOUCH
    modern/index.html
    modern/instructor.js
```

Chart.js and svg-world-map continue from `/static/vendor/` (no new vendor deps;
sparklines and deviation bars are dependency-free inline SVG). Modern pages use
the system font stack and drop the `fonts.css` dependency.

Config additions: `configs/eosat1/mcs/annotations.yaml` (guided-mode text,
keyed by page id + element id).

### 1.2 The `SMO_UI` switch — per server

Precedence mirrors the `time_source` house pattern: **CLI `--ui` > `SMO_UI` env
> mission-config `ui:` field > default.** Per-request override: `?ui=` query
param wins over everything; plus `GET /classic` always serves the classic page
(and `GET /modern` the modern one) so the two can be compared live in two tabs.

- **MCS** `server.py`: resolve `self._ui_mode` beside `self._time_source`; add
  `--ui`; `_handle_index` picks `modern/index.html` vs `index.html` with
  `Cache-Control: no-cache`. Static mount unchanged (`modern/` is inside the
  mounted dir).
- **Planner** `server.py`: same; `_handle_index_wide` serves
  `modern/wide.html` in modern mode and preserves the classic
  `/?layout=wide&ui=classic` redirect branch. `/wide` is a documented classroom
  URL — must not break.
- **Instructor** `app.py`: `create_instructor_app(engine, ui_mode=None)`
  (env fallback); `?ui=` must compose with the existing `?sub=` deep-dive param.
- **Shared pages** (`/dumps`, `/reports`, `/config` from smo_common): out of
  scope for restyling; untouched.
- **start.sh**: `SMO_UI="${SMO_UI:-modern}"` (but code default stays classic
  until the final flip — see integration order), `--classic` arg, pass `--ui`
  to all three services, banner prints both URLs.

**API rule (binding, all agents): both UIs hit the same endpoints. No endpoint
may be removed, renamed, or have a response field removed or retyped. All
server-side changes are additive.** The modern MCS page polls `/api/state`
(existing handler); `/api/sim-state` stays for classic.

---

## 2. PAGE-BY-PAGE LAYOUT SPECS

### 2.1 MCS modern console

Layout skeleton from `docs/design_2026-08/mockup_b_console.html`: flex column →
SA header → shell (62 px nav rail + work area) → collapsible bottom log drawer →
docked (non-modal) right alarm drawer. Chrome budget ≤ 140 px vertical; L1/L2
fit 1366×768 without scroll.

**SA header** — four cards answering the four questions:

| Card | Question | Contents | Source |
|---|---|---|---|
| SEE | Can I see it? | TM link freshness, TM tier active vs commanded + demotion reason, store fill | WS `state`, 0x0539/0x053A |
| TALK | Can I talk to it? | uplink/carrier/bit/frame lock, last TC verdict, GS in contact | `state` TTC block |
| WELL | Is it well? | SC MODE (TM-derived), alarm counts by priority, unacked, load-shed level | single alarm authority |
| NEXT | What is next? | next AOS/LOS countdown, next eclipse, next scheduled activity | contact schedule, `state` |

Header row: logo · UTC/MET/SC-TIME clocks · position selector (all SEVEN
positions — classic omitted `propulsion`) · ALARMS button + unacked badge ·
DELAYED TM / REPORTS links · WS dot · toggles: GUIDED, GREYSCALE, THEME, `?` ·
contingency banner strip when active · **TRAINING chip** (`TRAINING · SIM x5` /
`TRAINING · PAUSED`) rendered only when `time_source=="sim"`, absent otherwise.

**Nav rail**: OVR · EPS · TCS · AOCS · TTC · PLD · PRP · OBD · | · CMD · FDIR ·
PROC · TRND · DIAG · MAN. Health dot + numeral per button (never colour alone).
Keyboard: F6, arrows, Escape-to-OVR preserved.

**Tab mapping (all classic tabs must land):**

| Classic | Modern home |
|---|---|
| OVERVIEW | **OVR (L1)** — schematic-as-navigation (click battery → EPS L2); orbit bar; ONE contact timeline + ONE pass table (+ SA countdown) replacing three duplicate renderings; ground-track map; system board |
| EPS…OBDH (7 tabs) | **L2 pages, one skeleton ×7**: schematic · key params as pv triads · AND table (generated from `/api/param-catalog`) · steppers (one component for AOCS mode chain / TTC lock chain / load-shed) · equipment cards · **in-place position-gated quick commands for that subsystem** (extends classic PAYLOAD-only pattern) · subsystem events · alarm filter link · dump-playback container on ALL seven (classic only had EPS). Classic "commanding cross-reference" panels intentionally deleted — superseded by real in-place commanding. |
| COMMANDING | **CMD** — full builder incl. PACK/reverse-decode, provenance, quick commands, S15 dump panel, HK SID panel, verification log, history. Adds **arm→execute** for critical-flagged commands. Authority displayed: "NOT PERMITTED — belongs to <position>" instead of hiding entries. |
| PUS SERVICES (7 sub-tabs) | **DIAG (L4)** — S3, S11 (full), S6 (incl. file upload + CRC16), S15 cards, S12 editor, S19 editor, S9 time correlation; plus **PARAMS** panel (full param catalog + per-row TC(20,3) GET), with the filter promoted to a global header parameter search. |
| FDIR | **FDIR** — state strip, S12 table, S19 table, recovery controls, journal. S12/S19 renderers shared with DIAG (one implementation, two mounts). ON-BOARD AUTONOMY panel per disposition M4. |
| PROCEDURES | **PROC** — loader, step-by-step, run controls, doc viewer, manual override, execution log, full Builder, alarm→procedure link. |
| ON-DEMAND TM | **DIAG → PARAMS** (above). |
| TRENDS | **TRND** — everything Report 1 lists incl. the four-state archive provenance banner. Chart.js here. |
| MANUAL | **MAN** — TOC, viewer, search, position filter. |
| Bottom panel (5 tabs) | Collapsible bottom drawer: EVENTS / TM PACKETS / CMD HISTORY / HANDOVER / GO-NOGO. Collapses to 24 px. |
| Alarm journal | Docked right drawer, non-modal; filters persisted; ACK ALL + per-row ACK; procedure link; priority + rationalisation when provided (blank-tolerant); `a`/`k` shortcuts; focus trap. |

**Teaching layer**: GUIDED toggle → numbered annotations from
`configs/eosat1/mcs/annotations.yaml` via new additive `GET /api/annotations`;
default OFF, localStorage. **Causal-chain screen** from any alarm row: alarm →
param → limit (`/api/limits`) → recent commands (`/api/archive/commands`) → S5
events (`/api/archive/events`), client-side assembly, Mockup C spine.
`why-this-design.html` linked from the `?` dialog.

**Preserved plumbing (1:1)**: `simNow()` single time primitive; WS
`/ws?position=` + 3 s reconnect + all message types as first-class handlers;
REST fallback on `/api/state`; all polling cadences; chart staleness gate
promoted display-wide (P4); position filtering; keyboard map; skip-link;
tablist semantics; `#alert-live`; `prefers-reduced-motion`. Broken footer
screenshot replaced by text.

### 2.2 Planner modern interactive

Header: UTC clock, clock-source badge (`SIM TIME`/`UTC (REAL)`/red `WALL
CLOCK`), SUNLIT/ECLIPSE, lat/lon/alt, orbit period **from
`/api/config/planner`** (kills hard-coded 94.7), CONFIG link. Panels: ground
track (layers/legend); contact windows (canvas + next-5); activity schedule
with delete **and per-row EDIT (wires `PUT /api/schedule/{id}`)**, Add Activity
modal (with the UTC hint), template load, clear, validate, upload-to-MCS,
Validate Constraints with an **explicit SoC input** (kills hidden
`battery_soc=80.0`) and a per-domain breakdown wiring
`GET /api/constraints/*`; power budget; data budget; imaging opportunities +
**targets list** (wires `GET /api/imaging/targets`). Bottom bar: orbital
elements from config/state endpoints, next AOS, next eclipse, API dot.
No WebSocket (unchanged). Data badges default `—` until `/api/time-source`
resolves.

### 2.3 Planner `/wide` wall display (distinct page)

Non-interactive: **zero `<button>/<form>/<input>/<select>`**, no hover-needed
content, readable from metres (primary numerals ≥ 28 px, minimum 18 px).
JS polling refresh; no meta-refresh. Two width tiers (ultrawide row / 16:9
2×3 grid). Blocks:

1. Clock block — UTC huge, MET, clock-source badge; `TRAINING — SIM TIME xN`
   chip only in sim mode.
2. Next passes — next 5 contacts + big NEXT AOS/LOS countdown.
3. Plan timeline — 24 h horizontal canvas: contacts, eclipse arcs, activities,
   now-marker.
4. Ground-track map (no interactivity).
5. Budget state — power margin + data fill as two large deviation bars.
6. Link status — in-contact yes/no, station, elevation; API dot.

Freshness rule applies hard: stale block ⇒ hatch + em-dash; full-page STALE
banner after 120 s without a successful poll (watchdog).
Cadences: 1 s clock, 10 s contacts/link, 30 s schedule/budgets, 120 s track.

### 2.4 Instructor modern

Sim-side: MAY show speed, scenarios, failures, clock truth. Same design system,
plus a labelled `INSTRUCTOR` accent strip so it is never mistaken for the
operator console. Layout: header + three columns + collapsible bottom log.

- Header: SIM TIME primary clock, speed group (0.5/1/2/5/10× + FREEZE) as one
  stepper, SC mode badge, scenario chip, parameter-completeness counter,
  CONFIG link, connection chip.
- Left — SCENARIOS & SESSION: scenario list/briefing/START/STOP/progress;
  **new DEBRIEF panel wiring `GET /api/scenario/debrief`**; breakpoints
  (save/list/load).
- Centre — TRUTH DASHBOARD: subsystem cards, ONBOARD CLOCK TRUTH card (keeps
  "not downlinked" label), orbit bar, parameter search, raw-snapshot toggle,
  `?sub=` deep-dive launcher (composing `ui` param; `?sub=` full-page mode
  preserved exactly).
- Right — INTERVENTIONS: typed failure-injection form (catalog-driven
  widgets, onset controls, server-rejection error box), active failures with
  CLEAR/CLEAR ALL, Mission Phase & Separation, SET SoC, OVERRIDE PASSES (one
  copy — the classic duplicate is intentionally removed).
- Bottom log: severity-coloured, 200-cap, clear; **timestamps in sim time**.
- Command transport: send once via `POST /api/command` (WS inbound-only).

---

## 3. COMPONENT VOCABULARY

Tokens (canonical, all pages):

```css
:root{
  --bg:#0E1013; --s1:#14171C; --s2:#1A1E24; --s3:#22272F;
  --line:#2B313A; --line-2:#373F4A;
  --tx:#E6E9ED; --tx2:#A3ACB9; --tx3:#6E7885;   /* validate >=4.5:1, else lighten */
  --ok:#8A93A0;                                  /* nominal = grey, never green */
  --warn:#E8A317; --alarm:#FF5C4D; --info:#5AA9FF; --shelf:#8B7CF6;
  --stale:#6E6E80;
  --hatch:repeating-linear-gradient(45deg,transparent 0 4px,rgba(255,255,255,.06) 4px 8px);
  --mono:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;
  --sans:"Inter","Segoe UI",system-ui,sans-serif; --r:6px;
}
:root[data-theme="light"]{ /* Mockup A light ground */ }
:root[data-greyscale="1"]{ filter:grayscale(1); }
```

Type floor 12 px, primary readouts ≥ 14 px. Reduced-motion kills animation;
blink reserved for unacked P1 alarms.

`components.js` — pure functions, no framework, callers own DOM diffing:
`renderParam(def, value, limits)`, `renderDeviationBar(value, limits)` (ticks +
labels on focus, expanded scale in guided mode), `renderSparkline(series,
opts)` (stops appending when stale), `renderAndTable(params, values)`
(SCOS-2000 AND), `renderChip(state)` (glyph vocabulary: warning hollow
amber up/down, alarm filled red, stale hatched em-dash, commanded blue dashed
diamond, off ring), `renderStepper(states, activeIdx)` (ONE component for all
chains), `renderAlarmRow(alarm)`, `renderEventRow(evt)`, `freshness(ageS)`
(fresh <5 s / degraded 5-15 s / stale >15 s), `bindSchematic(svgRoot, state)`
(walks `[data-param]`, `[data-nav]` click-to-navigate). `format.js`: `fmtUTC`,
`fmtMET`, `fmtCUC`, `fmtAge`, `fmtHex`.

Data contracts: param defs from `/api/param-catalog`; limits from **new
`GET /api/limits`** (parsed `mcs/limits.yaml`); live values + ages from WS
`state`; params without limits render an explicit "no limit" affordance.

---

## 4. SIM-COUPLING DISPOSITIONS (every Report 2 finding)

KEEP-T = behind `time_source=="sim"` · MOVE-I = to instructor · TM =
TM-derived replacement · DEL = delete. (S) = additive server change (Agent 1).

| Finding | Disposition |
|---|---|
| L1 `_ingest_sim_health()` sc_mode/engine_fault/tm_rate/backlog/scheduler/fdir/tier | Modern consumes TM-derived only: tier from 0x0539/0x053A; scheduler-enabled from S11.17; sc_mode per M5; engine_fault/backlog DEL from modern; FDIR per M4. Server payload unchanged (classic compat). |
| L2 `hk_enabled` from sim state | TM: observed `hk_sid_age_s` only. |
| L3 orbit elset handover post-burn | KEEP-T (S): guard ingestion with `time_source=="sim"` in both servers; real mode keeps configured TLE (honest behaviour; TLE upload is future work, comment it). |
| L4 MET from `/api/state` | KEEP-T (already guarded); modern renders absent-MET affordance. |
| M1 SPEED pill (MCS top bar) | MOVE-I + KEEP-T: instructor is primary home; MCS gets the TRAINING chip, absent in real mode. |
| M2 second SPEED copy in SA strip | DEL. |
| M3 `simNow()` | KEEP — mandatory single time primitive; degenerates to wall clock in real mode. |
| M4 FDIR ON-BOARD AUTONOMY panel | KEEP-T retitled "SIMULATED ON-BOARD AUTONOMY (instructor truth — not downlinked)", sim mode only; real mode shows the TM-derived story (S5/S12/sc_mode). Copy also lands on instructor (MOVE-I). |
| M5-M7 sc_mode from sim state | TM (S): add 0x0700-0x0703 to an HK SID; modern reads param 0x0700; every sc_mode rendering carries a provenance sub-label (`TM` / `SIM (training)`). |
| M8 contingency banner from `state.sc_mode` | TM: drive from `obdh.mode` primary, 0x0700 secondary. |
| M9 INSTRUCTOR badge on override events | KEEP-T (inert in real mode). |
| M10 `sim_time_jump` in payloads | (S) omit in real mode; modern never reads it. |
| M11 `speed` published unconditionally | (S) publish 1.0 in real mode; modern hides chip per M1. |
| M12 `/api/sim-state` name | Modern polls `/api/state`; old route kept. No rename. |
| M13 hard-coded PLANNER_URL | Modern reads `/api/client-config`, no literal fallback; unconfigured ⇒ stale affordance. |
| P1 planner clock badge tooltip speed | Badge KEEP; speed tooltip DEL. |
| P2 planner `simNow()` | KEEP. |
| P3 static `SIMULATED` text in markup | DEL: default `—`, set after `/api/time-source`. |
| P4 hard-coded orbit literals | Read `/api/config/planner` + `/api/spacecraft-state`. |
| P5 `battery_soc=80.0` literal | Explicit operator SoC input, labelled. |
| P6 `:8080/api/state` default in help | (S) remove from `--help`; config-only. |

Headless endpoints: `/api/displays`, `/api/displays/fdir-alarms`,
`POST /api/ground-time`, `POST /api/schedule/pass-activity`,
`GET /api/config/alignment` stay headless (documented); `GET /api/schedule`
may be linked from CMD as a report. None removed.

---

## 5. WORK SPLIT

Disjoint ownership; classic index.html files owned by NO ONE (untouchable).

**Agent 1 — Platform (FIRST; output is the contract):**
`smo_common/ui/*`, all three servers' switch + shared mounts + `GET
/api/limits` + `GET /api/annotations` + real-mode guards (L3, M10, M11, P6) +
sc_mode-into-HK config change, `start.sh`, tests `test_ui_switch.py`,
`test_classic_untouched.py` (sha256 of the three classic files). Code default
`classic` until final flip. Done = tokens + component signatures frozen,
documented in file headers.

**Agent 2 — MCS modern page:** `smo-mcs/static/modern/*`,
`configs/eosat1/mcs/annotations.yaml`, `tests/test_mcs/test_modern_page_behaviour.py`,
`tests/test_mcs/js/modern_harness.js` (do not edit the shared harness). §2.1 in full.

**Agent 3 — Planner modern:** `smo-planner/static/modern/*`,
`tests/test_planner/test_modern_page.py`, `test_wide_page.py` (assert wide.html
has zero button/form/input/select; all six blocks present). §2.2 + §2.3.

**Agent 4 — Instructor modern:** `instructor/static/modern/*`,
`tests/test_simulator/test_instructor_modern.py` (parameter-completeness must
hold for modern too). §2.4.

**Integration order:** 1) Agent 1 lands (default classic). 2) Agents 2-4 in
parallel against the frozen contract. 3) Agent 5 verification. 4) Flip default
to modern (one line per server + start.sh), rerun the gate.

**Agent 5 — Verification:** `tests/test_e2e_browser/*` (new),
`test_content/test_modern_inventory.py`, screenshots under
`docs/design_2026-08/screenshots/`. Tasks: (1) transcribe Report 1 into
`inventory_checklist.md`, one row per capability, column = modern location /
intentionally-relocated / N-A-with-reason; any unticked row is a release
blocker; intentional deletions must match §2/§4 exactly. (2) Playwright E2E
against a live stack: switch behaviour on all three servers; MCS WS + SA header
+ schematic nav + alarm drawer + command round-trip + position filtering;
planner activity round-trip + wide non-interactivity; instructor inject/clear +
scenario + debrief. (3) Screenshot checks: 1366×768 (no scroll on L1/L2),
1920×1080, 2880×540 wide; greyscale pair. (4) Full suite; classic-page tests
pass UNCHANGED. (5) API-compat gate: route-table snapshot per server; fails if
any pre-existing route disappears. Every `data-param` in schematics.svg
resolves against `/api/param-catalog` (content gate).

---

## 6. RISK REGISTER

| # | Risk | Mitigation |
|---|---|---|
| R1 | Schematic drift (bespoke SVG) | data-param bindings, geometry traced from classic SVGs, content gate on every binding |
| R2 | Classic/modern API compat | additive-only rule, route-table snapshot, classic sha256 gate, full suite |
| R3 | Dangling vendor symlinks | modern needs vendor only for Chart.js + map (same as classic); graceful "vendor assets missing" state; fonts dependency dropped |
| R4 | sc_mode HK change alters TM | additive; run parameter-chain/limits/HK round-trip gates |
| R5 | Default-flip breakage | default stays classic until Agent 5 gate passes; `/classic` escape hatch |
| R6 | `/wide` documented URL | classic branch preserved; E2E covers both |
| R7 | Wall display lies when stale | watchdog: hatch + em-dash per block, STALE banner at 120 s |
| R8 | Duplicate-consolidation drops a path | explicit resolution per duplicate; checklist row each |
| R9 | Stale cached assets | no-cache on index; `?v=` literal on shared assets, bumped manually |
| R10 | `?sub=` × `?ui=` composition | explicit E2E case |
| R11 | Contrast regressions | validate token pairs ≥4.5:1 text / 3:1 graphics before freeze; greyscale screenshot review |

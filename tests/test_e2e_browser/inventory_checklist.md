# SMO UI reorganisation — functional inventory checklist (Agent 5)

Transcription of Report 1 (functional inventory), one row per capability.
Status values:

- **present** — capability exists on the modern page (location given).
- **intentionally-relocated-to-X** — same capability, different home.
- **intentionally-deleted-per-plan-§N** — deletion sanctioned by the cited
  plan section (`docs/design_2026-08/EXECUTION_PLAN.md`).

Evidence: grep inspection of `static/modern/*` plus the per-page harness
tests (`tests/test_mcs/test_modern_page_behaviour.py`,
`tests/test_planner/test_modern_page.py`, `test_wide_page.py`,
`tests/test_simulator/test_instructor_modern.py`) and the E2E run
(`tests/test_e2e_browser/test_modern_ui.py`, `SMO_E2E=1`).

Modern MCS file refs: `index.html` = `packages/smo-mcs/src/smo_mcs/static/modern/index.html`,
`mcs.js` = same dir. Planner/instructor analogous.

## 1A — MCS console

### Page-level chrome

| # | Capability | Modern location | Status |
|---|---|---|---|
| A1 | Skip-link → `#main` | `index.html:162` `.ds-skip` | present |
| A2 | Logo SMO MCS / EOSAT-1 | header brand block, `index.html` top bar | present |
| A3 | UTC clock, 1 Hz on `simNow()` | `#utc-clock`, `startClocks()` mcs.js | present |
| A4 | MET clock DDD:HH:MM:SS | `#met-clock`, `fmtMET` (shared format.js) | present |
| A5 | SC TIME clock (decoded OBC CUC) | `#sc-time-clock`, `fmtCUC` | present |
| A6 | SPEED pill (`xN` / `PAUSED`) | TRAINING chip `#training-chip` (`TRAINING · SIM xN` / `TRAINING · PAUSED`), sim-mode only; instructor speed group is primary home | intentionally-relocated-to-instructor per plan §4 M1 (KEEP-T as TRAINING chip) |
| A7 | Pass countdown NEXT AOS/LOS | SA header NEXT card (`updateNextCard`) | intentionally-relocated-to-SA-NEXT-card per plan §2.1 |
| A8 | Eclipse countdown chip | SA NEXT card sub-line (`eclipse in …`) | intentionally-relocated-to-SA-NEXT-card per plan §2.1 |
| A9 | Alarm badge triple (NOM/WARN/ALARM) | SA WELL card (alarm counts by priority + unacked) | intentionally-relocated-to-SA-WELL-card per plan §2.1 |
| A10 | Load-shed top badge SHED Ln | SA WELL card (`LOAD SHED Ln`, param 0x0134) | intentionally-relocated-to-SA-WELL-card per plan §2.1 |
| A11 | ALARMS button + unacked badge → journal | `#alarms-btn` + `#alarm-unack-n` → docked right drawer | present |
| A12 | DELAYED TM link → /dumps | `index.html:176` | present |
| A13 | REPORTS link → /reports | `index.html:177` | present |
| A14 | `?` keyboard-help button + modal | `#help-btn` + `#help-overlay` (also links why-this-design.html) | present |
| A15 | WS connection dot | `#ws-dot` | present |
| A16 | SA strip (LINK/NEXT/TIER/STORE/UNACK/SPEED/SUN) | four SA cards SEE/TALK/WELL/NEXT carry LINK freshness, tier active-vs-cmd + demotion reason, store fill, unacked, AOS/LOS, sun/eclipse. SPEED item deleted | present (SPEED sub-item intentionally-deleted-per-plan-§4-M2) |
| A17 | Position selector (6 positions) | `#pos-select`, now all SEVEN positions incl. `propulsion` (plan §2.1) | present (extended) |
| A18 | Position detail panel | OVR header `#pos-detail` (title/desc summary) | intentionally-relocated-to-OVR-header per plan §2.1 |
| A19 | Position-driven filtering (tabs/SVG/quick cmds/manual) | rail filter (`tabVisibleForPosition` mapped), quick-command gating, manual section filter, "NOT PERMITTED — belongs to X" authority display | present |
| A20 | Tab bar 15 tabs, tablist, arrow nav | 62 px nav rail (OVR…MAN), role=tablist, ←/→/F6/Escape preserved | intentionally-relocated-to-nav-rail per plan §2.1 |
| A21 | Contingency banner + ACK | `#contingency-banner`, driven from `obdh.mode` primary / 0x0700 secondary per §4 M8 | present |
| A22 | Status bar incl. broken screenshot img | status/AI-assistance line as text; broken `Screenshot…PM.png` img replaced by text | present (image intentionally-deleted-per-plan-§2.1 "Broken footer screenshot replaced by text") |
| A23 | `#alert-live` role=alert region | `index.html:301` + `announce()` | present |

### Keyboard map

| Capability | Modern location | Status |
|---|---|---|
| F6 cycle regions | mcs.js keyboard handler (§6 NAV) | present |
| ←/→ between tabs | rail arrow navigation | present |
| Escape → close dialog / return to OVR | Escape-to-OVR handler | present |
| Enter in command-data field → send | CMD builder keydown | present |
| Enter in handover input → add | `#handover-input` handler | present |
| `?` toggle help | `toggleHelp()` | present |
| `a` open alarm drawer / `k` ack first | drawer shortcuts (new per plan §2.1) | present |

### Transport & plumbing

| Capability | Modern location | Status |
|---|---|---|
| WS `/ws?position=` + 3 s reconnect | `connectWS()` / `wsClient` | present |
| WS msg `state` (clock re-anchor, schedule) | `handleState` + `adoptServerClock` equivalent | present |
| WS msg `tm` packet log | bottom drawer TM PACKETS pane | present |
| WS `go_nogo_poll/status/result` | first-class handlers → GO/NO-GO pane | present |
| WS `alarm` / `alarm_cleared` (classic monkey-patch) | first-class handlers `handleAlarmMsg`/`handleAlarmCleared` | present |
| Outbound `{type:'set_position'}` | `setPosition()` | present |
| REST fallback while WS down | `/api/state` @2 s (route rename per §4 M12; `/api/sim-state` kept server-side for classic) | present |
| `simNow()` single time primitive | shared `format.js`/mcs.js §3 | present |
| Chart staleness gate | promoted display-wide (`tmFreshness`/`updateChartGate`, plan P4) | present |
| Polling cadences (clocks 1 s, verif 5 s, sys-overview 5 s, power-budget 5 s, contact-schedule 30 s, contacts 60 s, FDIR 2 s while open, proc status 2 s while loaded) | `poller(...)` block in mcs.js init | present |
| Planner-fed overview data (ground-track/spacecraft-state via PLANNER_URL) | `/api/client-config` planner base, no literal fallback (M13); map + orbit bar from `/api/state` + `/api/contacts` | present |

### Tab 1 — OVERVIEW → modern OVR (L1)

| Capability | Modern location | Status |
|---|---|---|
| Orbit bar LAT/LON/ALT/ECLIPSE/CONTACT | `#ovr-orbit-bar` | present |
| Spacecraft bus SVG + live values + SC MODE | `sch-ovr` symbol in schematics.svg, schematic-as-navigation (click → L2), mode caption + provenance | present |
| 2×3 subsystem summary grid w/ NOM/WARN/ALARM tags | health dot + numeral per rail button + system board chips + L1 schematic values | intentionally-relocated-to-rail+system-board per plan §2.1 (consolidation) |
| Contact windows 24 h timeline + legend + Next AOS | `#ovr-timeline` canvas + legend + SA NEXT countdown | present (ONE timeline per plan §2.1) |
| Ground-track world map (track, footprint, GS, terminator) | `#ovr-map` (SvgWorldMap; SC + GS markers; vendor-absent designed fallback showing subsatellite point) | present (simplified layers — see FINDINGS) |
| System overview board (chips + key params + source label) | `#ovr-board` ← `/api/displays/system-overview` | present |
| GS pass schedule table | `#ovr-pass-table` ← `/api/displays/contact-schedule` | present (ONE pass table per plan §2.1) |

### Tabs 2–8 — subsystem tabs → L2 pages (one skeleton × 7)

| Capability | Modern location | Status |
|---|---|---|
| EPS load-shed banner | SA WELL card + load-shed stepper on EPS L2 | intentionally-relocated per plan §2.1 |
| EPS power-distribution SVG | `sch-eps` schematic symbol (data-param bound) | present |
| EPS solar/battery/bus panels + power-lines + per-subsystem draw tables | key-param triads + AND table (full EPS param set from `/api/param-catalog`) + equipment cards | intentionally-relocated-to-AND-table per plan §2.1 |
| EPS charts (soc/power/busv) | per-param inline sparklines (dependency-free) + TRND page for Chart.js trends | intentionally-relocated per plan §2.1/§3 |
| EPS stored-TM playback chart + scrubber | dump-playback container on ALL seven L2 pages (`*-dump-chart` + `*-dump-scrub`) | present (extended to 7 per plan §2.1) |
| EPS power budget board (`/api/displays/power-budget`) | EPS L2 `#eps-power-budget` card, 5 s poll (added by Agent 5 — was missing) | present |
| EPS events list | `#eps-events` (`routeEventToSubsystem` equivalent) | present |
| TCS thermal map SVG / temp table / heater status / charts / events | `sch-tcs` + AND table + equipment cards + stepper + sparklines + events | present |
| AOCS equipment SVG / mode chain / attitude / trackers / CSS / gyros / MTQ / GPS / RW cards / orbital data / charts / events | `sch-aocs` + AOCS mode stepper (one stepper component) + key params + AND table + equipment cards + events | present |
| TTC downlink / beacon panel / RF chain SVG / lock chain / RF params / PA & geometry / charts / events | `sch-ttc` + lock-chain stepper + equipment cards (incl. BEACON 0x0526, TIER 0x0539, TIER CMD 0x053A) + AND table + events | present |
| TTC ground-station status per-station panels | TTC L2 `#ttc-gs-panel` "Ground station network" card (added by Agent 5 — was missing): station list + in-contact marker + live EL/range | present |
| TTC TM data-rate ladder (active vs cmd tier + demotion reason) | SA SEE card + TTC equipment TIER/TIER CMD cards | intentionally-relocated-to-SA-SEE per plan §2.1 |
| PAYLOAD FPA readiness / calibration / image store / sensor / memory / image catalog / chart / events | PLD L2 skeleton (key params, AND table, equipment cards, stepper, events) | present |
| PAYLOAD COMMANDING cross-reference panel | superseded by in-place position-gated quick commands on PLD | intentionally-deleted-per-plan-§2.1 ("commanding cross-reference panels intentionally deleted") |
| PROP status / interlocks 0x0B15 / feed SVG / tank table / burn / dV budget / charts / events | PRP L2 (`sch-prp`, key params incl. 0x0B01, AND table, equipment, events) | present |
| PROP COMMANDING cross-reference panel | in-place quick commands on PRP (+ new `propulsion` position) | intentionally-deleted-per-plan-§2.1 |
| OBDH OBC SVG / computer / CAN / TC acceptance / S15 store fill / chart / events | OBD L2 (`sch-obd`, equipment CPU/MEM/HKTM/EVT/SEU/CAN, AND table, events); store fill also on SA SEE | present |

### Tab 9 — COMMANDING → CMD

| Capability | Modern location | Status |
|---|---|---|
| Service/subtype/data/name inputs + SEND | CMD builder → `POST /api/pus-command` | present |
| Expected-vs-entered byte-length chip | builder length chip (tc-catalog driven) | present |
| Structured field editor (catalogue, units, calibration, validation) | CMD builder structured fields (`catFieldWidth` codec) | present |
| PACK TO HEX + reverse-decode | CMD codec (pack + reverse decode) | present |
| Command provenance line | `provenanceText()` (exported, harness-tested) | present |
| QUICK COMMANDS grid, grouped, critical styling, position-filtered | CMD quick commands + per-L2 in-place quick commands; critical = arm→execute (new, plan §2.1); hidden entries now shown as "NOT PERMITTED — belongs to <position>" | present |
| STORED TM (S15) dump panel (subsystem + store-id + DUMP) | CMD S15 dump panel → `/api/tm-dump` → `/api/tm-dump-data`; per-L2 REQUEST DUMP buttons | present |
| HK SID enable/disable/one-shot panel | CMD HK SID panel (TC 3,5 / 3,6 / 3,27) | present |
| VERIFICATION LOG panel (5 s) | `#cmd-verif-log` ← `/api/verification-log` | present |
| COMMAND HISTORY panel | `#cmd-history` (local ring + `/api/archive/commands`) + bottom drawer CMD HISTORY | present |

### Tab 10 — PUS SERVICES → DIAG (L4)

| Capability | Modern location | Status |
|---|---|---|
| S3 HK manager (SID table, observed state, Request/Enable/Disable All, reload, per-row ops) | DIAG → S3 tab (`/api/hk-structures`) | present |
| S11 scheduler (refresh 11,17; enable 11,13; disable 11,9; clear; time-tag modes + NOW + CUC preview; add-from-builder; per-row delete; table from `state.schedule`) | DIAG → S11 tab | present |
| S6 memory (region select from `/api/memory-map`, dump 6,5, CRC 6,9, patch + **file upload** `#s6-file`, load 6,2, verify, read-back, local CRC16-CCITT, hex viewer) | DIAG → S6 tab (`crc16Ccitt` exported) | present |
| S15 storage four store cards (enable/disable/dump/clear + refresh 15,13) | DIAG → S15 tab | present |
| S12 monitoring editor (refresh 12,12, enable/disable 12,1/12,2, add-monitor form, per-row ops) | DIAG → S12 tab — **same renderer** `renderS12Block("diag")` as FDIR mount | present |
| S19 event-action editor (refresh 19,8, add-rule form w/ trigger kinds, per-row ops) | DIAG → S19 tab — `renderS19Block("diag")` | present |
| S9 time correlation (display + offset state + request 9,2 + sync + set-arbitrary + NOW + CUC preview) | DIAG → S9 tab | present |

### Tab 11 — FDIR

| Capability | Modern location | Status |
|---|---|---|
| FDIR state strip (level, SC MODE + source, latched, load shed, OOL, last action) | FDIR strip; SC MODE carries `TM` / `SIM (training)` provenance per §4 M5 | present |
| ON-BOARD AUTONOMY panel (sim truth) | retitled "SIMULATED ON-BOARD AUTONOMY (instructor truth — not downlinked)", sim-mode only; real-mode TM-derived story (S5/S12/mode) shown instead | present (per plan §4 M4 KEEP-T) |
| Recovery & response controls (REFRESH ALL, 12,12, 19,8, 12,1, 12,2, ACK ALL) | FDIR controls row | present |
| S12 monitor table + filter + OOL-only checkbox | `renderS12Block("fdir")` (one implementation, two mounts) | present |
| S19 rule table | `renderS19Block("fdir")` | present |
| Alarm/anomaly journal w/ five type checkboxes | FDIR journal (s5/s12/s19/tcrej/mode checkboxes) | present |

### Tab 12 — PROCEDURES → PROC

| Capability | Modern location | Status |
|---|---|---|
| Source/type selects (activity types, ops procedures, saved) | PROC loader (`/api/procedure/activity-types`, `/api/procedures`, `/api/procedure/custom`) | present |
| Step-by-step checkbox + Load/Start/Pause/Resume/Step/Skip/Abort | PROC run controls → `/api/procedure/{action}` | present |
| Procedure document viewer | `#proc-doc` ← `/api/procedures/{path}` (markdown renderer) | present |
| Manual override (service/subtype/data → override-command) | PROC manual override → `/api/procedure/override-command` | present |
| Execution status + log (2 s poll) | `#proc-exec-log` ← `/api/procedure/status` | present |
| PROCEDURE BUILDER (name, 5 step types, add, save, run, clear, load saved) | PROC builder (tlm-check step takes param name/hex directly; `/api/procedure/tlm-vocabulary` no longer called — capability preserved, vocabulary endpoint stays server-side) | present |
| Alarm row → open procedure | alarm drawer per-row procedure link | present |

### Tab 13 — ON-DEMAND TM

| Capability | Modern location | Status |
|---|---|---|
| Full param-catalog table + filter + on-demand-only + per-row GET TC(20,3) | DIAG → PARAMS panel (`GET (20,3)` buttons); filter promoted to header parameter search | intentionally-relocated-to-DIAG-PARAMS per plan §2.1 |

### Tab 14 — TRENDS → TRND

| Capability | Modern location | Status |
|---|---|---|
| Window select (15 m…7 d) | `#trnd-window` | present |
| Parameter filter + multi-select list | `#trnd-filter` + `#trnd-params` ← `/api/archive/param-names` | present |
| Four checkboxes (limit bands, eclipse/contact shading, event+alarm markers, normalise) | `#trnd-bands/-shade/-markers/-norm` | present |
| REFRESH + EXPORT CSV | `#trnd-refresh` / `#trnd-csv` | present |
| Chart ← `/api/archive/parameters` + context series orbit.eclipse/contact | TRND chart (Chart.js, vendor-guarded) | present |
| Alarms/events/commands-in-window tables | `#trnd-alarms/-events/-cmds` ← `/api/archive/{alarms,events,commands}` | present |
| Subsystem playback select + LOAD | `#trnd-playback-sub` ← `/api/archive/playback` | present |
| Alarm-trend overlay (`POST /api/displays/alarm-trends`) | markers drawn from `/api/archive/alarms`+`events` (marker checkbox); the POST endpoint remains server-side (route-table gated) | intentionally-relocated-to-archive-markers per plan §2.1 ("TRND — everything Report 1 lists"; marker overlay is the surviving rendering) |
| Four-state archive provenance banner (ok/unavailable/failed/legacy) | `#trnd-provenance` (all four states) | present |

### Tab 15 — MANUAL → MAN

| Capability | Modern location | Status |
|---|---|---|
| TOC + section viewer + markdown renderer | MAN page (`renderMarkdown` exported) ← `/api/manual`, `/api/manual/{path}` | present |
| Search filter | `#man-search` | present |
| Position-based section filtering | MAN position filter | present |

### Bottom panel

| Capability | Modern location | Status |
|---|---|---|
| EVENTS / TM PACKETS / CMD HISTORY / HANDOVER / GO-NOGO tabs | collapsible bottom drawer `#bottom-drawer` (collapses to 24 px) | present |
| Event severity select | `#evt-sev-filter` (incl. Warning+) | present |
| Jump-to-latest chip + scroll lock | `logLock` per pane | present |
| INSTRUCTOR badge on `INSTRUCTOR OVERRIDE` events | badge rendered, suppressed in real mode (per §4 M9) | present |
| Handover note input + Add ← `/api/handover` | `#handover-input`/`#handover-add`/`#handover-list` | present |
| GO/NO-GO start poll / respond GO,NO-GO,STANDBY / status | `#gonogo-*` ← `/api/go-nogo/*` + WS | present |

### Modals / overlays

| Capability | Modern location | Status |
|---|---|---|
| Keyboard-help dialog | `#help-overlay` | present |
| Alarm journal side panel (subsystem+severity filters persisted, ACK ALL, per-row ACK, close, ARCHIVE→/dumps, procedure link, beep + visual cue) | docked non-modal right drawer `#alarm-drawer` w/ focus trap, `a`/`k` shortcuts, priority + rationalisation columns; beep restored by Agent 5 (`playAlarmBeep`) | present |
| Add-activity modal (planner-owned) | planner modern Add/Edit modal | present (planner) |
| NEW: causal-chain screen (alarm→param→limit→commands→events) | `#chain-overlay` (plan §2.1 teaching layer) | present (new) |
| ~~GUIDED annotations + why-this-design.html~~ | DELETED 2026-08-19 (owner ruling: no training mode in the operator UI). `/api/annotations` still routes but serves `{}`; the operational gate/baseline tables moved to `display_config.yaml` ← `/api/display-config` | removed (deliberate) |

### Classic duplicates — explicit resolution (plan R8)

| Dup | Classic copies | Modern resolution | Status |
|---|---|---|---|
| 1 | Arbitrary PUS send ×3 | CMD builder + PROC manual override + builder command step | present (all three) |
| 2 | S12 enable/disable ×2 | one renderer, DIAG + FDIR mounts | present |
| 3 | S12.12/S19.8 requests ×2 | DIAG tabs + FDIR REFRESH ALL | present |
| 4 | S15 dump ×2 | DIAG S15 cards + CMD stored-TM panel (+ per-L2 request) | present |
| 5 | HK SID ops ×2 | DIAG S3 manager + CMD HK panel | present |
| 6 | Alarm ack ×3 | drawer per-row + ACK ALL, FDIR ACK ALL | present |
| 7 | Contact/pass info ×3-4 renderings | ONE timeline + ONE pass table + SA NEXT countdown | intentionally-consolidated per plan §2.1 |
| 8 | Ground track ×2 (MCS + planner) | both kept (OVR map; planner map) | present |
| 9 | /dumps links ×2 | header DELAYED TM + drawer ARCHIVE | present |
| 10 | Stored-TM playback ×2 backends | L2 dump playback (dump store) + TRND subsystem playback (archive) | present |
| 11 | SC MODE ×4 renderings | SA WELL card + L1 schematic caption + FDIR strip + contingency banner, each w/ provenance sub-label | present |

### MCS headless endpoints (documented, no UI client — must survive)

| Endpoint | Status |
|---|---|
| `GET /api/displays` | present in route table (headless, gated by test_modern_inventory) |
| `GET /api/schedule` | present (headless) |
| `POST /api/ground-time` | present (headless) |
| `POST /api/schedule/upload-s11` | present (planner-called) |
| `GET /api/displays/fdir-alarms` | present (headless) |
| `POST /api/displays/alarm-trends` | present (headless) |
| `GET /api/sim-state` | present (classic client; modern uses /api/state per §4 M12) |

## 1B — Mission planner

| Capability | Modern location | Status |
|---|---|---|
| `/` interactive page | `modern/index.html` + `planner.js` | present |
| `/wide` classroom URL | serves `modern/wide.html` in modern mode; classic `?ui=classic` keeps 302 → `/?layout=wide` | present (both branches, E2E-covered) |
| `/config` read-only link | header CONFIG link | present |
| UTC date+clock on planner sim clock | header clock (1 Hz, `simNow`) | present |
| Clock-source badge SIM TIME / UTC (REAL) / red WALL CLOCK | `#clockSourceBadge` ← `/api/time-source`; speed tooltip removed | present (tooltip speed intentionally-deleted-per-plan-§4-P1) |
| SUNLIT/ECLIPSE + lat/lon + altitude badges | header | present |
| Period "~94.7 min" hard-coded | orbit period from `/api/config/planner` (P4 fix) | present (literal intentionally-deleted-per-plan-§4-P4) |
| Ground-track panel (map, layers, legend, terminator, footprint, GS markers, info overlay) | ground-track panel (svg-world-map, vendor-guarded) | present |
| Contact windows canvas + next-5 table + data badge | contacts panel | present |
| Data badges hard-coded "SIMULATED" markup | default `—` until `/api/time-source` resolves | intentionally-deleted-per-plan-§4-P3 (static text; runtime badges kept) |
| Activity schedule list + per-row delete | schedule panel → `DELETE /api/schedule/{id}` | present |
| Per-row EDIT (`PUT /api/schedule/{id}`) | EDIT button + shared Add/Edit modal (new client) | present (new per plan §2.2) |
| Add Activity modal (type select + info, datetime-local + UTC hint, duration, priority, GS dependency) | Add/Edit modal (types from `/api/activity-types`) | present |
| Template load / Clear / Validate / Upload to MCS | schedule buttons → load-template / DELETE / validate / upload | present |
| Validate Constraints (`battery_soc=80.0` literal) | explicit labelled SoC input (`#socInput`), no hidden literal; per-domain breakdown wires `GET /api/constraints/{power,aocs,thermal,data-volume,conflicts}` | present (literal intentionally-deleted-per-plan-§4-P5) |
| Power budget panel | ← `/api/budget/power` | present |
| Data budget panel | ← `/api/budget/data` | present |
| Imaging opportunities + per-row Schedule | ← `/api/imaging/opportunities` → `POST /api/imaging/schedule` | present |
| Imaging targets list | new client of `GET /api/imaging/targets` | present (new per plan §2.2) |
| Bottom bar orbital elements (hard-coded) | from `/api/config/planner` + `/api/spacecraft-state` | present (literals intentionally-deleted-per-plan-§4-P4) |
| Bottom bar Next AOS / Next eclipse / API dot | bottom bar | present |
| Polling cadences (1 s clock, 5 s state/time-source, 10 s contacts, 30 s budgets/schedule, 60 s terminator, 120 s track) | planner.js pollers | present |
| No WebSocket (REST only) | unchanged | present |
| `?layout=wide` body-class wall mode | DISTINCT `modern/wide.html` page (plan §2.3): zero button/form/input/select, six blocks (clock, next passes, plan timeline, ground track, budgets, link status), stale hatching + 120 s watchdog banner, two width tiers | intentionally-relocated-to-wide.html per plan §2.3 |
| Planner headless endpoints (`/api/config/alignment`, `POST /api/schedule/pass-activity`) | server routes kept (route-table gated) | present (headless) |

## 1C — Instructor console

| Capability | Modern location | Status |
|---|---|---|
| `/` dashboard, `/ws`, `/config` RW, `/dumps`, `/reports` | all routes kept; modern index behind switch | present |
| `?sub=` deep-dive full-page mode + back link | preserved exactly; composes with `?ui=` (launcher propagates ui param) | present |
| WS inbound event/state_update/failure_injected/failure_cleared, 3 s reconnect | instructor.js WS client | present |
| Commands sent twice (WS + POST) | sent ONCE via `POST /api/command` (WS inbound-only) | intentionally-deleted-per-plan-§2.4 ("send once via POST /api/command") — duplicate transport removed |
| Sim time + speed indicator + SC mode badge | header (SIM TIME primary clock, mode badge) | present |
| Parameter-completeness counter N/318 | header counter (completeness test extended to modern) | present |
| Speed buttons 0.5–10× + FREEZE | single segmented speed group + FREEZE (one stepper, plan §2.4) | present |
| OVERRIDE PASSES (header copy + orbit-bar copy) | ONE copy in interventions column | present (duplicate intentionally-deleted-per-plan-§2.4) |
| CONFIG link | header | present |
| SoC input + SET SoC (+Enter) | interventions SET SoC | present |
| Scenario refresh/list/briefing/START/STOP/progress (sim-time) | left column SCENARIOS & SESSION | present |
| Debrief (`GET /api/scenario/debrief`, previously headless) | new DEBRIEF panel + FETCH | present (new per plan §2.4) |
| Subsystem deep-dive launcher | centre column launcher (`?sub=` links) | present |
| Subsystem cards EPS/AOCS/TCS/OBDH/TT&C/PAYLOAD | truth dashboard cards | present |
| ONBOARD CLOCK truth card w/ "not downlinked" label | clock-truth card (label kept) | present |
| Orbit bar LAT/LON/ALT/sun/contact | orbit bar (override-passes copy removed) | present |
| Parameter search + counter + clear | centre search | present |
| SHOW/HIDE RAW SNAPSHOT | `#btn-toggle-raw` + `#raw-snapshot-container` | present |
| Full snapshot browser tabs (subsystems + raw hex groups) | `?sub=` subpage browser | present |
| Failure injection (catalog-driven typed widgets, onset step/gradual/intermittent, server-rejection error box, INJECT) | right column interventions ← `/api/failure-catalog` | present |
| Mission phase select + SET PHASE + START SEPARATION | interventions | present |
| Active failures + per-row CLEAR + CLEAR ALL | interventions ← `/api/failures` | present |
| Breakpoints save/list/load | `POST /api/breakpoint/save`, `GET /api/breakpoints`, `POST /api/breakpoint/load` | present |
| Event log (200 cap, severity colours, clear) w/ wall-clock timestamps | bottom log; timestamps now in SIM time (plan §2.4) | present (timestamp base intentionally changed per plan §2.4) |
| Connection chip + toast | header connection chip | present |
| Polling 1 s state / 2 s snapshot / 5 s failures | instructor.js pollers | present |
| INSTRUCTOR accent strip (never mistaken for ops console) | labelled accent strip (new) | present (new) |

## FINDINGS (rows that needed action, all resolved)

1. **MCS power budget board** (`/api/displays/power-budget`, classic EPS tab)
   — absent from the modern page. FIXED by Agent 5: `#eps-power-budget`
   card on the EPS L2 page, 5 s poll (`refreshPowerBudget`, mcs.js).
2. **TTC ground-station status panel** (classic per-station GS panels) —
   absent. FIXED by Agent 5: `#ttc-gs-panel` "Ground station network" card
   (station list, in-contact marker, live elevation/range 0x050A/0x0509).
3. **Alarm audible beep** (classic `playAlarmBeep`) — absent. FIXED by
   Agent 5: WebAudio beep on new-alarm arrival (`playAlarmBeep`, mcs.js).
4. **Ground-track map layer fidelity** (MCS OVR): the modern map renders
   spacecraft + GS markers with a designed vendor-absent fallback, but not
   the classic track polyline / footprint / terminator layers. The planner
   modern page retains the full layer set, and the MCS map is a secondary
   rendering of the same planner data (classic duplicate #8). Recorded as
   an accepted simplification, NOT a deletion of the capability (the full
   ground track remains one click away on the planner and the wall page).
5. `/api/procedure/tlm-vocabulary` — modern builder takes param name/hex
   directly instead of a vocabulary dropdown; endpoint kept server-side.
   Accepted functional variant.

# UI reorganisation — delivery notes, 2026-08-18

Plan: `EXECUTION_PLAN.md` (built from your accepted design study in this
directory). Suite after: **6824 passed, 0 failed** (incl. 14 live-browser E2E
tests run separately). Screenshots in `screenshots/`.

## What you have

Three rebuilt UIs behind a switch, with the old ones byte-untouched on disk.

**The switch.** Default is the new UI. `SMO_UI=classic` (env), `--ui classic`
(per service), `--classic` (start.sh), `?ui=classic` (per request), or just
browsing to `/classic` — all bring back today's pages exactly as they are. A
sha256 gate test fails the build if anyone edits the classic files. Both UIs
hit the same endpoints, and a route-table snapshot test fails if any
pre-existing route disappears — so classic cannot rot silently.

**MCS console.** The fourteen-sibling-tab strip is gone. The shell is the
design study's Mockup B: a persistent situational-awareness header answering
*can I see it / can I talk to it / is it well / what's next*, a 62 px nav rail,
and an overview page where the spacecraft schematic IS the navigation — click
the battery, get the EPS page. Seven subsystem pages share one skeleton:
schematic, key parameters with deviation bars and sparklines, a SCOS-2000-style
alphanumeric display generated from the parameter catalogue, one stepper idiom
for every chain (AOCS modes, TTC lock ladder, load shed), equipment cards, and
— new — position-gated commanding in place on every subsystem page, with
"NOT PERMITTED — belongs to <position>" instead of silently hidden entries.
Critical-flagged commands now take an explicit arm→execute two-step. Grey is
the resting state; colour means something is wrong; a greyscale self-test
toggle ships in the header, plus a light theme for projectors. The teaching
layer is a GUIDED toggle (annotations live in `configs/eosat1/mcs/
annotations.yaml`, editable without touching code), a causal-chain screen
reachable from any alarm (command → state → telemetry → limit → alarm →
procedure), and a "why this design?" page.

**Planner.** Same system. The "Start time (UTC)" field genuinely parses UTC.
New since classic: per-row activity EDIT, an explicit battery-SoC input on
constraint validation (no hidden 80 % default), the per-domain constraint
breakdown, the imaging targets list, and orbit numbers read from config
instead of hard-coded.

**Planner `/wide`.** A separate page now, not a styled copy: six blocks —
clocks, next five passes with a large AOS/LOS countdown, a 24 h plan timeline,
the ground track, power/data budget bars, link status. Zero buttons, zero
forms, zero hover content (test-enforced); 28 px+ primary numerals; ultrawide
and 16:9 layouts. A wall must never lie: every block hatches and em-dashes
when its data goes stale, and a page-wide STALE banner appears after 120 s
without a successful poll.

**Instructor console.** Full treatment, with a labelled INSTRUCTOR accent
strip so a projected instructor screen is never mistaken for the operator
console. Three columns: scenarios & session (with a NEW debrief panel — the
endpoint existed and nothing called it — and breakpoints), the truth dashboard
(subsystem cards, the onboard-clock-truth card, `?sub=` deep-dive preserved),
and interventions (the typed failure-injection form, phase & separation, SoC,
one OVERRIDE PASSES). The event log now timestamps in sim time, and commands
are sent once (the classic page silently sent every command twice, via
WebSocket AND POST).

## The spacecraft-agnostic rule

The ground tools now work identically against a real spacecraft. Every leak
found in the audit has a disposition (plan §4). The ones you would notice:

- The MCS's spacecraft mode was read from the simulator's engine API, not from
  telemetry. It now comes from TM — the FDIR summary parameters 0x0700-0x0703
  were added to HK SID 4 (+4.6 bps) — and every mode rendering carries a
  provenance sub-label (`TM`, or `SIM (training)` when only sim data exists).
- The SPEED pill is gone from the MCS. In sim mode you get a chip that says
  `TRAINING · SIM x5`; against a real spacecraft it does not exist. The speed
  control itself lives where it belongs, on the instructor console.
- The FDIR "on-board autonomy" panel — which renders engine internals no real
  spacecraft downlinks — is retitled "SIMULATED ON-BOARD AUTONOMY (instructor
  truth — not downlinked)", appears only in sim mode, and the real-mode FDIR
  page tells the story from TM (S5 events, S12 transitions) instead.
- Post-burn orbit-element handover from the simulator is now guarded to sim
  mode in both ground servers; in real mode the ground keeps its own TLE.
- The planner's hard-coded orbit period/altitude and the hidden SoC default
  are gone; values come from config endpoints or an explicit operator input.

## Verification

The functional inventory (every tab, button, form, display, poll and shortcut
of all three classic pages) was transcribed into
`tests/test_e2e_browser/inventory_checklist.md` and every row placed: present,
intentionally relocated (with where), or intentionally deleted (citing the
plan). The verification pass caught three real omissions (power-budget board,
TTC ground-station panel, alarm beep) and one real bug (the REST verification
log read fields the API never emits) — all fixed. Live-browser E2E tests
(playwright, `SMO_E2E=1`) cover the switch on all three servers, WS + SA
population, schematic navigation, alarm drawer, a full command round-trip,
activity add/edit/delete, wide-page non-interactivity, and instructor
inject/clear/scenario/debrief. Screenshots at 1366×768 (no scrollbar on
overview or subsystem pages — verified programmatically), 1920×1080 and
2880×540, plus a greyscale pair.

## Intentional deletions (nothing else was dropped)

The two SPEED displays (→ TRAINING chip / instructor), the broken footer
screenshot, the PAYLOAD/PROP "commanding cross-reference" panels (superseded
by real in-place commanding), two of the three duplicate pass-schedule
renderings, the instructor's duplicate OVERRIDE PASSES, the double command
send, and the dangling `fonts.css` vendor dependency.

## Known follow-ups

- The MCS overview ground-track map renders markers only; the full layer set
  lives on the planner pages (was a classic duplicate).
- The PROC builder takes parameter name/hex directly instead of the
  tlm-vocabulary dropdown.
- `/dumps`, `/reports`, `/config` (the smo-common shared pages) were out of
  scope and keep their current look.
- The stray `instructor/static/PDF instructions.pdf` should be deleted on the
  Mac (bridge cannot delete files).
- When you are happy with the modern UI, the classic pages can be retired —
  but there is no hurry; the compat gates make coexistence cheap.

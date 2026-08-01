## Summary

The course textbook says the MCS console carries an FDIR panel (Ch 5.20 §2
pp. 314-316; Ch 7.5 pp. 338-339; Ch 12 pp. 406-411; Ch 15.2-15.4 pp. 497-499;
Figs 5.3 / 15.1) and `docs/review_2026-07/textbook_vs_sim.md:81` marked that
claim **Impl**. There was no FDIR display in the operator UI at all.

The only "FDIR" strings in the 6295-line `index.html` were an operator *position*
named `fdir_systems`, its description block, a text label inside the OBDH SVG
diagram, and the FDIR seat in the GO/NO-GO matrix. The `fdir_systems` operator
had a console seat and no display for their discipline.

Two pieces of dead code created the illusion of an implementation:

- **A client renderer that is never loaded.** `static/displays.js:166-247`
  implements `initFDIRAlarms()` / `renderFDIRAlarms()` / `updateFDIRAlarms()`
  against `GET /api/displays/fdir-alarms`. `index.html` loads exactly two
  scripts (`svg-world-map.js`, `chart.umd.min.js`); `grep -c "displays.js"
  index.html` = **0**. `class DisplayPanels` is never instantiated anywhere in
  the repo. Four other panels in the same file (contact schedule, power budget,
  procedure status, system overview) were equally orphaned.

- **A backend panel that is only half fed.** `displays/fdir_alarm_panel.py` and
  route `server.py` → `_handle_fdir_alarms` exist and work, but only the alarm
  half was ever driven. `FDIRAlarmPanel.update_s12_rules()`,
  `update_s19_rules()` and `set_fdir_level()` were **never called from anywhere
  in the repo**, so `get_display_data()` always returned
  `s12_monitoring.active_rules = 0`, `s19_event_action.active_rules = 0` and
  `fdir_level = "nominal"` / colour green, regardless of spacecraft state. Any
  client wiring that endpoint up as-is would have been actively misled.

The FDIR telemetry itself was not decoded either. `_process_tm` had no branch
for TM(12,13) (monitoring definition table) or TM(19,128) (event-action table),
so both landed in the raw packet log as opaque `(NB)` lines; TM(12,9)/(12,10)
were parsed only for their historical 6-byte prefix, throwing away the monitor
id, check type, severity and both limits the simulator now sends.

## Severity

**Major** — a training console for a mission-operations course, with an FDIR
seat, that cannot show the operator which autonomous protection is armed, which
limits are being checked, what has tripped, or what the spacecraft did about it.

## Status

**Fixed.**

### Telemetry decoders

New `smo_mcs/pus_reports.py` — pure functions, one per report, unit-tested
against bytes built from the wire spec:

```
TM(12,9)/(12,10)  param_id u16, value f32, mon_id u16, check_type u8,
                  severity u8, low f32, high f32                     [18 B]
                  (the historical 6-byte prefix is still accepted; the
                   missing fields are OMITTED, never faked)
TM(12,13)         count u16 + records: mon_id u16, param_id u16,
                  check_type u8, enabled u8, low f32, high f32       [14 B]
TM(19,128)        count u16 + records: ea_id u16, trigger_kind u8,
                  trigger_id u16, func u8, enabled u8                 [7 B]
```

### FDIR model

New `smo_mcs/displays/fdir_panel.py` (`FDIRDisplayModel`) holds three layers:

1. **Ground reference database** — monitor and rule *names*, event names, S8
   function names and parameter names, read from
   `configs/<mission>/monitoring/*.yaml`, `events/event_catalog.yaml`,
   `commands/tc_catalog.yaml` and `telemetry/parameters.yaml`. None of these are
   downlinked; they are ground knowledge, exactly as on a real programme. Default
   `mon_id`s mirror `smo_simulator.monitoring_loader.S12_CONFIG_MON_ID_BASE` so
   ground and on-board ids line up (pinned by a test).
2. **On-board truth** — TM(12,13) and TM(19,128) *replace* the on-board view, so
   a monitor deleted on board disappears from the table and a ground/on-board
   divergence is visible rather than assumed away.
3. **Live layer** — per-monitor in/out-of-limit status and violation counts from
   TM(12,9)/(12,10); a journal of S5 events, S12 transitions, inferred rule fires
   and spacecraft-mode changes in one time-ordered stream.

`FDIRAlarmPanel.update_s12_rules()` / `update_s19_rules()` / `set_fdir_level()`
are now called from `_sync_legacy_fdir_panel()`, so the old endpoint reports the
same numbers as the new window instead of a permanent zero (test asserts the two
agree).

### The window

`pane-fdir`, a 13th tab, visible to `flight_director` and to any position whose
`subsystems` include `fdir` (which is how `positions.yaml` already describes the
`fdir_systems` seat — no config change needed). Five panels:

| Panel | Content | Source |
|---|---|---|
| (a) State strip | FDIR level (colour-coded), SC MODE, SAFE / EMERGENCY latch chips, load-shed stage, monitors out of limits, last autonomous action | derived + sim state |
| (b) S12 monitoring | every definition: parameter, limits, check type, enabled, current value, in/out-of-limit status, violation count; filter + OOL-only | TM(12,13) + TM(12,9/10) + param cache |
| (c) S19 event-action | every rule: trigger kind/id (resolved through the event catalogue), action S8 function (resolved through the TC catalogue), enabled, fire count, last fire | TM(19,128) |
| (d) Alarm/anomaly journal | S5 events, S12 transitions and the rules they fired, one stream, filterable by kind — cause → autonomous action → effect | correlated |
| (e) Recovery controls | S12.12 / S19.8 report requests, S12.1 / S12.2 global enable-disable, per-row S12.3/4/7 and S19.4/5/2, ACK ALL | catalogue-backed only |

### Honesty of the display

Three things are labelled with their provenance rather than presented as
telemetry:

- **SC MODE** — the spacecraft does not downlink it. Parameters `0x0700-0x0703`
  (`fdir.level`, `fdir.active_alarms`, …) are declared in `parameters.yaml` but
  appear in **no HK SID**, so they are never packed. The MCS takes `sc_mode` from
  the simulator state endpoint it already polls for its clock anchor, and the
  strip says "source: simulator state (not TM)".
- **SAFE / EMERGENCY latches** — not downlinked as flags either; SAFE and
  EMERGENCY `sc_mode` are the observable consequence of each latch
  (`engine._update_sc_mode`), so they are reported as derived.
- **S19 fire counts** — S19 never reports "rule N fired"; the on-board dispatcher
  just runs the S8 function. Fires are *inferred on the ground* by matching each
  downlinked trigger (S5 event id, or S12 limit violation on the monitored
  parameter) against every enabled rule, **with the namespace** — event ids and
  parameter ids both live in 0x0000-0x0FFF, so a kind=event rule must not fire on
  a parameter of the same id. Rows and journal entries say "inferred".

FDIR level is derived from what IS observable: `system` on SAFE/EMERGENCY mode, a
CRITICAL alarm or load-shed stage 3; `subsystem` on a HIGH alarm, ≥2 monitors out
of limits or load shed ≥1; `equipment` on any single monitor out of limits or a
MEDIUM alarm; `nominal` otherwise.

### Only real controls are offered

Every button maps to a command in `tc_catalog.yaml`: TC(12,1/2/3/4/6/7/12) and
TC(19,1/2/4/5/8). There is deliberately **no** "set FDIR level" or "clear latched
flag" button — no such telecommand exists on this spacecraft, the level is a
ground derivation and the latches clear on board when the condition clears. ACK
is stated in the UI as a ground-only action.

### displays.js decision

**Deleted**, along with `displays.css`, rather than loaded. Rationale: it had
never been fetched by any page, it duplicated four other panels whose backends
are equally orphaned, and every other tab in this app is written inline against
the same helpers (`esc()`, `sendPusCmd()`, `addEventLog()`, the shared
`.panel`/`.data-table` CSS). Two divergent display stacks is precisely what
produced the "FDIR panel — Impl" mis-verdict. There is now one display stack.

## Files

- `packages/smo-mcs/src/smo_mcs/pus_reports.py` (new)
- `packages/smo-mcs/src/smo_mcs/displays/fdir_panel.py` (new)
- `packages/smo-mcs/src/smo_mcs/server.py` — `_process_tm` S12.9/10/13 and
  S19.128 branches, `_load_fdir_reference`, `_sync_legacy_fdir_panel`,
  `_load_shed_stage`, `GET /api/fdir`, `_handle_fdir_alarms`
- `packages/smo-mcs/src/smo_mcs/static/index.html` — `pane-fdir`, FDIR CSS,
  `initFdirTab` / `renderFdirWindow` / `renderFdirMonitors` / `renderFdirRules` /
  `renderFdirJournal`, rewritten S12 and S19 panels, `tabVisibleForPosition`
- `packages/smo-mcs/src/smo_mcs/static/displays.js`, `displays.css` (deleted)
- Tests: `tests/test_mcs/test_pus_reports.py`,
  `tests/test_mcs/test_fdir_window.py`,
  `tests/test_mcs/test_mcs_fdir_page.py`

## Related

- Defect #69 — the sim-side S12 definition loss and the TM(12,9)/(12,10)
  transition reports this window consumes.
- Defect #70 — the S19 report and the dual-triggered rules; the namespace
  discipline in the fire-correlation code is the ground half of that fix.
- Defect #13 — "MCS advanced displays unreachable (displays.js never loaded)".
  This closes the FDIR part of it by removing the file.
- **Documentation correction still owed:** `docs/review_2026-07/
  textbook_vs_sim.md:81` should no longer cite config files as evidence of a
  console FDIR panel; the panel now exists in `index.html`.
- **Configuration finding surfaced by the new display** (not fixed here — config
  is out of scope): S19 rule 1001 `eps_bus_undervoltage_payload_off` has
  `action_func_id: 10` with the comment "S8 func 10: payload mode OFF", but
  `tc_catalog.yaml` defines func 10 as `AOCS_SLEW_TO`. The rule table now shows
  the catalogue's answer, which is what the spacecraft actually executes. Flight
  Ops should reconcile the two.

## Summary

A consolidated register of smaller "defined-but-not-implemented / created-but-
not-connected" items found across the MCS during the audit. Individually each is
Minor, but together they represent a meaningful amount of dead or write-only
code and a few latent data-shape bugs. Each is evidence-backed (grep showed no
callers, or a concrete producer/consumer mismatch).

**Dead / unwired backend code (no callers anywhere):**

1. **Persistent command log never written.** `TMArchive.store_command`
   (`tm_archive.py:242`) and `update_command_state` (`:257`) have zero callers.
   Commands live only in the in-memory `_verification_log` deque (maxlen=200,
   `server.py:183`). The `tc_log` SQLite table (`tm_archive.py:59-71`) stays
   empty and command history does not survive a restart.
2. **Archive never pruned.** `TMArchive.prune` (`tm_archive.py:306`) is never
   called; parameters are inserted every ~10 s (`_archive_state_snapshot`,
   `server.py:944`) with no eviction → unbounded SQLite growth.
3. **TMProcessor limit-checking is inert.** The server constructs
   `TMProcessor(hk_structures=...)` (`server.py:140`) and never passes
   `limits=`, so `_check_limit` (`tm_processor.py:117`) can never alarm. Public
   methods `process_packet`, `get_param`, `get_history`, `pop_alarms` have no
   callers (server calls the private `_process_hk` directly).
4. **Config-driven display engine unused.** `displays/engine.py` (`DisplayEngine`)
   and `displays/widgets.py` (`GaugeWidget`, `LineChartWidget`, etc.) are never
   imported anywhere.
5. **Unused typed TC builders.** All `build_s3_*`/`build_s5_*`/`build_s6_*`/
   `build_s8_command`/`build_s12_*`/`build_s19_*`/`build_s20_*` in
   `tc_manager.py:37-121` are referenced only at their definitions; every real
   command goes through `build_command(service, subtype, raw_bytes)` with hex
   assembled in JS.
6. **Abandoned `old_index.html`.** `_handle_index` (`server.py:1075`) serves only
   `index.html`; nothing links to `static/old_index.html` (5063 lines).
7. **`sendLegacyCommand` → dead route.** `index.html:3501` posts to `/api/command`,
   which is hard-disabled and returns HTTP 410 (`server.py:1000-1011`).
   `sendLegacyCommand` itself has no callers (also duplicated in old_index.html).

**Write-only UI controls (command sent, result never shown):**

8. **Memory Browser Dump/Checksum.** `memDump()` (`index.html:5086`) writes the
   placeholder string "Requesting dump at 0x..." and the actual S6.6 dump bytes /
   checksum reports are never parsed (`handleTMPacket`, `index.html:3166`, handles
   only event/monitoring/verification). `tc_manager.build_s6_mem_load` (S6.2) has
   no UI control at all.
9. **S11 "Refresh List".** `s11ListCommands()` (`index.html:5066`) sends S11.17
   but no S11 schedule report is parsed/rendered, so the scheduled-command list
   is never populated from a downlink.

**Placeholder display data (would mislead if the panels of defect #13 were wired):**

10. `system_overview.update_subsystem_health` (`:118`), `fdir_alarm_panel`
    `update_s12_rules`/`update_s19_rules`/`set_fdir_level` (`:126/:140/:154`),
    `power_budget` per-subsystem values, and `procedure_status.log_step_execution`/
    `clear_executing_procedure` are never called → health always GREEN, FDIR rule
    counts always 0, per-subsystem power fixed.
11. **Contact-schedule key mismatch.** `ContactScheduler.update_passes`
    (`contact_pass_scheduler.py:66-70`) reads `aos_time`/`los_time`/`max_elevation`/
    `ground_station`, but producers emit `aos`/`aos_utc`/`max_elevation_deg` →
    every pass collapses to defaults.

**Minor data-shape bugs in the live UI:**

12. **S9 time-correlation NaN.** `updateTimeCorrelation()` (`index.html:5234`)
    treats `state.sim_time` as a numeric epoch, but the server sets it to an ISO
    string (`server.py:419`) → "Invalid Date".
13. **S15 storage bar loses colour >90%.** `updateStorageStatus()`
    (`index.html:3825`) sets class `gauge-fill crit`, but CSS defines only
    `nom`/`warn`/`alarm`/`cmd` — should be `alarm`.

## Severity

**Minor** — none breaks a primary operator workflow today (several are dead code
behind the unreachable displays of defect #13), but they are real correctness /
maintainability / operability issues and several become misleading the moment
the surrounding feature is wired up.

## Requirements for the fix

Triage each: wire it up if intended (1, 2, 8, 9, 10, 11), fix the bug (12, 13),
or delete the dead code (4, 5, 6, 7, and the unused TMProcessor surface in 3).

## Suggested implementation

- Call `store_command`/`update_command_state` wherever a TC is sent and when S1
  verification arrives; schedule a periodic `prune()`.
- Pass limit definitions to `TMProcessor` and consume `pop_alarms`/`get_history`,
  or trim the unused methods.
- Parse S6.6 dump / checksum and S11 schedule reports in `_process_tm` and render
  them; add a guarded S6.2 control or drop `build_s6_mem_load`.
- Delete `old_index.html`, `sendLegacyCommand` + the dead `/api/command` route,
  the unused typed TC builders, and `displays/engine.py`/`widgets.py` if the
  config-driven display path is not intended.
- Fix the S9 epoch parsing and the S15 `crit`→`alarm` class.

## Acceptance criteria

- No MCS function/route/JS handler is dead without an explicit decision recorded.
- Command history persists across restart; archive growth is bounded.
- S9 time-correlation shows a valid time; the S15 bar keeps its colour >90%.

## Affected areas

- `packages/smo-mcs/src/smo_mcs/tm_archive.py`, `tm_processor.py`, `tc_manager.py`, `server.py`
- `packages/smo-mcs/src/smo_mcs/displays/*.py`
- `packages/smo-mcs/src/smo_mcs/static/index.html`, `old_index.html`

## Related

- Defect #13 (advanced displays unreachable) — items 10 and 11 are its
  placeholder-data and key-mismatch sub-issues.
- Defect #6 (no parameter-watch widget / no S20 client).

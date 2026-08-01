## Summary

Four separate faults in the on-board S12 monitoring service. The service was
genuinely evaluated every tick and correctly edge-triggered — the problems were
in what got registered, what got reported, and what could be controlled.

1. **4 of the 38 config rules were silently discarded, and they were always the
   WARNING tier.** `load_s12_definitions()` deliberately keys rules
   `f"{param_id}_{rule_name}"` *"(to allow multiple rules per param)"*
   (`monitoring_loader.py:49-51`), and then `register_s12_definitions()` threw
   that away and re-keyed by bare `param_id` (`monitoring_loader.py:147`).
   Duplicates collided; last one won:

   ```
   rules loaded by loader: 38 ; registered in dispatcher: 34 ; LOST: 4
   colliding param_ids: {0x101: 2, 0x105: 2, 0x102: 2, 0x408: 2}
     0x0101 survivor: 'EPS Battery SoC Critical' (low=10.0)  -- 'SoC Warning' (low=20.0) LOST
     0x0105 survivor: 'EPS Bus Voltage Critical' (low=25.0)  -- 'Bus Voltage Low' (27.0) LOST
     0x0102 survivor: 'EPS Battery Undertemp'   (high=50.0)  -- 'Battery Overtemp' (45.0) LOST
   ```

   Operationally: **on every parameter carrying two rules there was no WARNING
   tier at all — only the ALARM.** The trainee never saw the early warning that
   the whole two-tier limit scheme exists to give them, and the on-board
   behaviour silently disagreed with the shipped configuration.

2. **No TM(12,9)/(12,10) check-transition reports existed.** `grep 'service=12'`
   in the simulator returned exactly one hit — the 12,13 definition report. S12
   transitions reached the ground only as S5 events with a *synthesised*
   `0x9000|param` / `0xA000|mon` id. Meanwhile `TMProcessor.process_packet`
   (`tm_processor.py:40-41`), `MCSServer._handle_tm_packet` (`server.py:777-785`)
   and the browser's `pkt.monitoring` renderer all parse S12.9/12.10 — **all of
   that ground code was dead**, and every S12 alarm was filed in the MCS alarm
   journal under `"unknown"` because `_event_id_to_subsystem` cannot map 0x9xxx.

3. **No per-definition enable/disable, and `check_type` was never used.**
   12,1 / 12,2 were global; `dispatch(12, 3|4|5|8|15, ...)` were all verified
   no-ops. `defn['enabled']` and `defn['last_value']` were stored and never
   written; the `check_type` field was read at load and never consulted in
   `check_monitoring()`, so the delta check the config schema advertises did
   not exist.

4. **`MON_ADD_DEF` in the catalog was one byte short.**
   `tc_catalog.yaml:1142-1149` declared `param_id(u16) + low(f32) + high(f32)`
   = 10 bytes, but the handler reads `check_type = data[2]`, `low = data[3:7]`,
   `high = data[7:11]` and needs 11. A catalog-shaped add produced
   `{'check_type': 63, 'low_limit': -8.97e-44, 'high_limit': 0.0}` — silent
   garbage limits. (The MCS UI inserted the byte correctly, so only the
   catalogue was wrong, which is exactly why nobody noticed.)

## Severity

**Major** — the spacecraft's limit configuration did not match its declared
configuration, the early-warning tier was absent mission-wide, and no S12
telemetry the ground could decode was ever produced.

## Status

**Fixed.**

1. **Keyed by definition id.** `_s12_definitions` is now keyed by `mon_id`, with
   `param_id` carried inside the record; `check_monitoring()` already returned a
   separate `mon_id`, so the rest of the pipeline needed no change. Config rules
   take an optional explicit `mon_id:` and otherwise get one auto-assigned by
   file order from `S12_CONFIG_MON_ID_BASE` (0x8000), above the ground-added
   range. All 38 rules now register; both SoC tiers (20 % WARNING / 10 % ALARM),
   both bus-voltage tiers (27 V / 25 V) and both battery-temperature limits are
   live.

2. **Real check-transition telemetry.** `_tick_s12_monitoring` now emits
   **TM(12,9)** on entering a limit and **TM(12,10)** on returning to nominal,
   *in addition to* the existing S5 event (which the S19 event-action path and
   the alarm store both hang off, so it had to stay). Subtypes 9/10 were chosen
   deliberately: they are what the MCS already decodes, so the fix turns dead
   ground code live rather than requiring a new parser. The record is a strict
   superset of what `_process_monitoring` reads:

   ```
   TM(12,9) / TM(12,10)   big-endian, 18 bytes
     param_id   uint16
     value      float32     (the checked quantity: the value, or the delta)
     mon_id     uint16
     check_type uint8       0 = absolute, 1 = delta
     severity   uint8       1=INFO 2=WARNING 3=ALARM 4=CRITICAL
     low_limit  float32
     high_limit float32
   ```

3. **Per-definition control and delta checks.** New **TC(12,3)** enable-one /
   **TC(12,4)** disable-one, both `mon_id(u16)`, rejected with S1.8 code 0x0007
   for an unknown definition; 12,1/12,2 remain the global switches.
   `check_type == 1` is implemented as a delta check against `last_value`: the
   first sample after a start, a re-enable or a power-down has no baseline and
   cannot violate, which stops a cold-equipment power-up registering as a step
   change.

4. **Catalogue fixed and extended.** `MON_ADD_DEF` now declares the full
   13-byte form `mon_id(u16) + param_id(u16) + check_type(u8) + low(f32) +
   high(f32)`; the handler still accepts the legacy 11-byte MCS form
   (`param_id + check_type + low + high`, `mon_id = param_id`) so the existing
   UI keeps working. `MON_DELETE_DEF` now names `mon_id`. Added
   `MON_ENABLE_DEF` (12,3), `MON_DISABLE_DEF` (12,4) and `MON_REPORT_DEFS`
   (12,12).

5. **TM(12,13) definition report extended** to a 14-byte record so the ground
   can render the table it needs: `mon_id(u16) + param_id(u16) + check_type(u8)
   + enabled(u8) + low(f32) + high(f32)`, after a `count(u16)` header.

## Files

- `packages/smo-simulator/src/smo_simulator/monitoring_loader.py`
  (`load_s12_definitions`, `register_s12_definitions`, `_as_int`,
  `S12_CONFIG_MON_ID_BASE`)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
  (`_handle_s12`, `build_s12_transition_report`, `check_monitoring`)
- `packages/smo-simulator/src/smo_simulator/engine.py`
  (`_tick_s12_monitoring`, `_emit_s12_transition`, `_s12_violation_records`)
- `configs/eosat1/commands/tc_catalog.yaml` (S12 block)
- Tests: `tests/test_simulator/test_pus_service_fixes.py`
  (`TestS12Definitions`, `TestS12TransitionReports`,
  `TestS12PerDefinitionControl`, `TestS12DeltaCheck`, `TestS12DefinitionReport`)

## Related

- Defect #70 — the S19 namespace split; S12 violations are now delivered to S19
  explicitly in the *parameter* namespace rather than as a 0x9xxx event id.
- Hand-off to the MCS: `_event_id_to_subsystem` should map 0x9xxx/0xAxxx to
  `param_id & 0x0F00` so the S5 copy of a transition stops being "unknown"; and
  `#s12-tbody` can now be populated from the extended TM(12,13).
- Four parallel limit engines still coexist (sim S12, sim FDIR thresholds,
  MCS `limits.yaml`, browser colouring). Consolidation is forward work, not a
  defect.

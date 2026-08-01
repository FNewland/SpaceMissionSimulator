## Summary

The 64 kbps high data rate was unreachable from the ground, and when nominally
"selected" nothing about the emitted frames or the MCS-visible throughput
changed. Four independent breaks, three of them on the simulator side.

1. **The command argument was decoded as the wrong type.**
   `tc_catalog.yaml:992` declares `{name: rate, type: uint8,
   description: "0=low(1kbps), 1=high(64kbps)"}`. The dispatcher did:

   ```python
   elif func_id == 65:  # set tm rate
       rate = (struct.unpack('>I', data[:4])[0] if len(data) >= 4 else 64000)
       ttc.handle_command({"command": "set_tm_rate", "rate": rate})
   ```

   An operator following the documented procedure sends one byte, so
   `len(data) < 4` and the value fell through to the literal `64000` —
   *appearing* to work, but only by accident, and **`rate=0` (LOW) silently set
   HIGH**. Padding to 4 bytes gave `rate = 1`, which `ttc_basic` then rejected as
   an invalid rate. Only `struct.pack('>I', 64000)` / `('>I', 1000)` worked,
   which is exactly what the acceptance test did — which is why the suite never
   caught it.

2. **Even a successful command was overwritten on the next tick.**
   `ttc_basic.tick()` unconditionally recomputed
   `s.tm_data_rate = base_rate * bits_per_sym` from `s.data_rate_mode`, and
   `data_rate_mode` was settable by **no telecommand anywhere** — the only
   writers were `engine.py:432` and `:2184`, both setting it to `0`
   (bootloader/beacon). Once a scenario booted through the bootloader phase the
   rate was pinned to `tm_rate_lo` forever. (Roadmap N-8.)

3. **Real-time telemetry was not rate-limited at all.** Grepping `engine.py` for
   `tm_data_rate` yielded exactly one consumer: `_tick_dump_emission()` — S15
   stored-TM dump pacing only. Real-time HK, event and verification TM went
   through `_enqueue_tm()`, which gated on `downlink_active` and nothing else.
   Switching to high rate produced **no additional real-time frames**. This is
   the single biggest reason "it doesn't seem to do anything".

4. **The frequency plan contradicted itself.** `ttc_basic.handle_command`
   validated `set_ul_freq` at 440-460 MHz and `set_dl_freq` at 400-410 MHz
   (UHF), while `tc_catalog.yaml` advertised 2000-2100 / 8400-8500 MHz, and
   `ttc.yaml`, `planning/ground_stations.yaml`, `rfsim.yaml` and the textbook all
   say S-band. Three ranges, no two the same.

## Severity

**Major** — the data-rate half of the pass-planning lesson (at 1 kbps a pass
moves ~50 kB, at 64 kbps ~3 MB) was un-teachable, and the low-rate command did
the opposite of what it said.

## Status

**Fixed.**

1. **One representation, the catalogued one.** Func 65 now decodes a **uint8
   mode**: `0` = low (1 kbps), `1` = high (64 kbps). A 4-byte payload is still
   accepted as a raw-bps value equal to one of the two configured rates, so
   older clients and the existing acceptance test keep working. A bare TC with no
   argument still means high rate. An unrecognised value returns an S1.8 failure
   instead of being silently coerced.

2. **The commanded rate is authoritative.** `set_tm_rate` now sets
   `data_rate_mode` — the field `tick()` actually consumes — and recomputes
   `tm_data_rate` immediately, so the commanded rate survives every subsequent
   tick. Added `set_data_rate_mode` as an explicit alias for procedures and
   OBCPs. Two physical refusals were added rather than silently ignored: high
   rate requires a deployed antenna, and any rate command is refused in beacon
   mode (where the rate is forced low by design).

3. **Real-time TM is rate-paced.** `_enqueue_tm` now appends to a real-time
   backlog, drained each tick by the new `_tick_rt_emission()` using the same
   byte-budget model already proven in `_tick_dump_emission`
   (`budget += (tm_data_rate / 8) * dt`; release while the budget covers the head
   packet; carried budget capped so an idle period cannot bank credit and burst).
   The backlog is bounded (`rt_tm_backlog_max`, default 2000) and overflow counts
   into `tm_queue_drops`. Measured: at 1 kbps a 40-packet, 64-byte burst releases
   ~2 packets per second and visibly backs up; at 64 kbps the same burst clears
   in one tick. `tm_rate_bps` and `rt_tm_backlog` are now in the state summary.

   Pacing is config-gated by `simulation.yaml ttc.rt_tm_pacing`:
   `auto` (default) paces only while the engine's tick loop owns the clock,
   `on` always, `off` restores the previous unpaced behaviour. `auto` exists so
   that callers which dispatch a TC and read `tm_queue` without ever ticking —
   which is most of the unit-test suite — are unaffected.

4. **S-band everywhere.** `ttc_basic.UL_BAND_MHZ = (2025.0, 2110.0)` and
   `DL_BAND_MHZ = (2200.0, 2290.0)` (CCSDS 401.0-B near-Earth allocations) are
   the single definition; `set_ul_freq`/`set_dl_freq` validate against them and
   name the band in their rejection message. `tc_catalog.yaml` now advertises the
   same bands. The stale hand-off note in `configs/eosat1/subsystems/ttc.yaml`
   is replaced with a statement that the three ranges now agree. The RF plan in
   `ttc.yaml` (2025.0 / 2200.0) is untouched — it was already correct.

## Files

- `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py`
  (`set_tm_rate`, `set_data_rate_mode`, `set_ul_freq`, `set_dl_freq`,
  `UL_BAND_MHZ`, `DL_BAND_MHZ`)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
  (`_route_ttc_cmd` func 65)
- `packages/smo-simulator/src/smo_simulator/engine.py`
  (`_enqueue_tm`, `_tick_rt_emission`, `_rt_pacing_active`,
  `_current_tm_rate_bps`, `_put_tm`, `_rt_pending`, `_rt_byte_budget`)
- `packages/smo-common/src/smo_common/config/schemas.py`
  (`SimTTCConfig.rt_tm_pacing`, `rt_tm_backlog_max`)
- `configs/eosat1/simulation.yaml`, `configs/eosat1/subsystems/ttc.yaml`,
  `configs/eosat1/commands/tc_catalog.yaml`
- Tests: `tests/test_simulator/test_pus_service_fixes.py`
  (`TestCommandedDataRate`, `TestRealTimeTMPacing`, `TestSBandFrequencyPlan`);
  `tests/test_simulator/test_ttc_enhanced.py` frequency tests re-pinned to S-band

## Related

- Roadmap N-8 (make the high rate reachable) and N-4 (make the RF plan
  coherent — the S-band move itself was done separately).
- **Not fixed here, outside this lane:** the RF pipeline symbol rate is still
  hard-wired at 32 kBd (`smo-rfsim` `coordinator.py:41-48` reads a `pipeline:`
  section that `RFSimConfig` does not define, so it is always `None`), and
  `bridge._apply_sim_feedback` uses the reported data rate only to update a text
  label — it never calls `PipelineCoordinator.reconfigure(symbol_rate=...)`.
  Until that is done the modulated signal and occupied bandwidth are identical
  at 1 and 64 kbps, even though the frame flow now differs.
- Hand-off to the MCS: there is no panel showing *achieved* throughput
  (frames/s x frame bits); `tm_rate_bps` and `rt_tm_backlog` in the state summary
  are enough to add one.

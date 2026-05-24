## Summary

A consolidated register of smaller dead/inert commands and state across the
subsystem models found in the deep audit. Each is individually Minor but
together they make several "advanced control" procedures cosmetic. All are
grep-verified (no reader / no route / effect ignored).

**AOCS:**

1. `inject_failure("st_blind")` is overwritten next tick — it sets
   `st{n}_status=3` (`models/aocs_basic.py:1564-1569`) but `_tick_star_trackers`
   recomputes status from sun geometry every tick with no persistent blind flag
   (only `failed` is honoured, `:381-422`), so the blind state is wiped within
   one tick. (No shipped scenario uses it — they use the persistent `st_failure`
   — so low live impact, but it's an inert exposed failure mode.)
2. `tle_upload` sets `tle_valid`/`tle_validity_timer` (`:1527-1538`) that the
   tick never reads or ages; the documented "30-day ECSS validity limit" is never
   enforced. Also unrouted by any S8 func_id. (Dead both ways.)
3. `MODE_FINE_POINT` (mode 5) is never entered by `_check_auto_transitions`
   (`:305-366`) — reachable only by ground command. Likely intentional; flagged
   for confirmation (previously noted as deferred "fine-pointing readiness" in
   `defects/reviews/aocs_fixed.md`).

**Payload:**

4. `cal_lamp_on` is telemetered (0x0615, in SID 5) but no command/path ever sets
   it (`payload_basic.py:77,603`) — the calibration lamp is permanently off even
   during flat-field calibration.
5. Per-band integration times and `detector_gain` are commandable
   (`set_integration_time` `:800-808`, `set_detector_gain` `:812-816`) but never
   feed the SNR/quality model (`:417-447`); `compression_override` (`:857`) is
   stored but never read. Write-only knobs.
6. Calibration coefficients (`dark/flat_frame_buffer`, `gain_coeff`,
   `bias_coeff`, `calibration_valid_mask`, `:99-113`) are set to trivial values
   on completion (`:301-304`) and never applied — image quality is identical
   whether or not the spacecraft has ever calibrated.
7. Filter wheel and shutter mechanisms (`select_filter` `:882-889`,
   `cycle_shutter` `:863-869`) change `filter_position`/`shutter_position` but
   nothing reads them and they're in no HK SID. (Partially overlaps the
   `defects/reviews/payload_fixed.md` claim that shutter/filter "mechanisms were
   added" — the commands exist but don't affect imaging.)

**EPS:**

8. Dead/unreachable `handle_command` branches (no S8 route, no other caller):
   `enable_array`/`disable_array` (`eps_basic.py:650-659`), `set_battery_heater`
   (`:684-689`), `bus_isolate` (`:681-683`, also a pure no-op), `reset_trip`
   (`:690-697`, duplicate of routed `reset_oc_flag`), `get_power_budget`
   (`:698-699`). `bus_isolate` and `set_battery_heater` are operationally
   meaningful and silently absent from the MCS.
9. `sa_a_enabled`/`sa_b_enabled` are read only in the beta-angle fallback
   generation branch (`:287-288`), never in the primary 6-panel path (`:305-323`),
   so "disable array A" is a no-op in normal sunlit flight.
10. `set_solar_array_drive` (func 24) stores/streams the SADA angle (0x0133,
    `:676`/`:598`) but the angle is never applied to generation (body-mounted
    panels) — command succeeds, effect ignored.
11. S2 `device_states` (e.g. 0x010F battery charge regulator) are stored
    (`:922-931`) but never read by the tick, so S2 device on/off for EPS devices
    changes nothing.
12. `sep_timer_active`/`sep_timer_remaining` (`:93-94`, emitted `:580-581`) are
    never written — the real timer lives on the engine (`configure_separation_state`)
    — so 0x0127/0x0128 always read 0/False.

**OBDH:**

13. `cpu_spike` (`obdh_basic.py:656-657`) only raises a telemetry number; nothing
    reacts (no watchdog feed, no mode change). (S12 limit monitor does flag high
    CPU, so it's partly observable.)
14. `memory_segments[]` health (`:96`, inject `:679-694`) is never written to
    `shared_params` and not in `_param_ids` — segment loss is invisible beyond a
    small step in `mmm_used_pct`.
15. `mmm_used_pct` (0x0303) is a frozen constant (init 20.0, only changed by
    `memory_segment_fail`) — never grows with activity, yet has an S12 ">90 %"
    monitor that is therefore untriggerable in nominal ops.
16. `obc_select_bus` rejects a switch *to* a FAILED bus (`:571-581`) — sensible
    in isolation but, combined with defect #29, never exercised. Low confidence.

**TCS:**

17. `configure()` ignores the `thermal_coupling` and `event_thresholds` config
    blocks in `configs/eosat1/subsystems/tcs.yaml` (`tcs_basic.py:150-187` reads
    only param_ids/zones/heaters/fpa target); the conductance/limits come from
    hardcoded `__init__` dicts. They match today, so no divergence yet — a latent
    trap where editing the YAML has no effect.

## Severity

**Minor** — none breaks a primary nominal workflow, but each is either dead code,
a silently-ineffective command (false success), or inert state. Cumulatively they
hollow out the "advanced control" surface (payload tuning/calibration, EPS device
isolation, AOCS TLE management, TCS config tuning).

## Requirements for the fix

Triage each: wire it into the relevant physics/telemetry if the capability is
intended, reject the command clearly if it isn't supported, or delete the dead
code/state. Prefer "reject clearly" over "return success and do nothing".

## Suggested implementation

Per item, in the owning model. High-value first: cal lamp during flat-field (4);
integration-time/gain into SNR (5); EPS `bus_isolate`/`set_battery_heater` routed
or removed (8); honour `sa_*_enabled` in the main generation path (9); drive EPS
`sep_timer_*` from the engine timer (12); wire TCS config blocks (17). Confirm
intent for fine-point auto-transition (3) and `obc_select_bus` (16).

## Acceptance criteria

- No subsystem command returns `success` while having no effect; unsupported
  commands return a clear failure.
- Each retained capability has an observable effect and a test; deleted items are
  removed along with their telemetry stubs.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/models/{aocs_basic,payload_basic,eps_basic,obdh_basic,tcs_basic}.py`
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (routing for items intended to be commandable)
- `configs/eosat1/subsystems/tcs.yaml` (item 17)

## Related

- Defect #33 (computed-but-unobservable telemetry) — the observability twin of
  this register.
- Defects #11, #26, #27, #31 — specific Major instances of the same
  false-success / inert-command pattern.

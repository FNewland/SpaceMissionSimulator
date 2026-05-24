## Summary

The `uplink_loss` failure does not produce its defining symptom. The
`ttc_no_tm_at_aos.yaml` scenario injects `uplink_loss` (and `primary_failure`)
expecting "no telemetry at AOS", but the downlink keeps producing telemetry, so
the link looks healthy and the diagnosis can't be trained.

- `inject_failure("uplink_loss")` sets `s.uplink_lost = True; s.carrier_lock =
  False` (`models/ttc_basic.py:841-843`).
- The only use of `uplink_lost` in `tick` is a no-op
  (`ttc_basic.py:274-275`: `if s.uplink_lost: s.cmd_rx_count = s.cmd_rx_count`)
  plus gating of `record_cmd_received` (`:820`). It suppresses the command-RX
  counter only.
- The lock-acquisition block (`ttc_basic.py:293-319`) recomputes
  `carrier_lock`/`bit_sync`/`frame_sync` from `_lock_timer` every tick with **no
  `uplink_lost` guard**, so the `carrier_lock=False` set at injection is
  overwritten on the very next tick. The downlink budget branch keys off
  `frame_sync` (`:329`), not `uplink_lost`, so telemetry continues to flow.

## Severity

**Major** — a shipped scenario's central symptom (no TM at AOS) never occurs from
the injection it uses, making the "lost uplink / no downlink" diagnosis
un-trainable. (`set_coherent_mode`, `cmd_auth_status`, binary `set_tx_power`, and
`set_rx_gain` inert behaviours are already documented in `defects/reviews/ttc.md`
and are not re-filed here.)

## Requirements for the fix

1. While `uplink_lost` is active, the link/lock chain must reflect a lost link so
   the operator sees the intended loss-of-telemetry symptom.

## Suggested implementation

- In the lock-acquisition block (`ttc_basic.py:293-319`), gate carrier/bit/frame
  lock (and therefore ranging and command reception) on `not s.uplink_lost` — at
  minimum hold `carrier_lock = False` while `uplink_lost`. Decide deliberately
  whether "uplink loss" should also stop the downlink (if the scenario's intent
  is loss of the whole link) or only the command path (if it is truly uplink-only)
  and align the scenario briefing with the modelled behaviour.

## Acceptance criteria

- Running `ttc_no_tm_at_aos.yaml` results in no downlinked telemetry at AOS (or
  the documented partial-link behaviour), matching the scenario briefing.
- A test injects `uplink_loss` and asserts the lock/telemetry state reflects the
  loss across subsequent ticks (not just the injection tick).

## Affected areas

- `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py` (lock-acquisition block, `uplink_lost` handling)
- `configs/eosat1/scenarios/ttc_no_tm_at_aos.yaml` (briefing alignment if needed)

## Related

- Defect #33 (unobservable telemetry) — the TTC antenna-deployment sensor is
  similarly hard to diagnose.

## Summary

The OBDH subsystem parameter displayed in the MCS as **"Buffer Fill – HK TM"**
reaches physically impossible values (353% observed after ~2 minutes of
real-time bootloader operation). A fill percentage > 100% indicates the
underlying counter is not clamped to the buffer capacity, is not decremented
when packets are drained, or is an accumulation counter mislabelled as a
fill percentage.

## Severity

Major — gives operators a misleading picture of onboard memory state, which
could drive incorrect go/no-go decisions for S15 dump scheduling and HK
cadence changes.

## Steps to reproduce

1. Start the simulator at real-time (speed = 1×) in bootloader mode
   (`start_in_bootloader: true`).
2. Leave the sim running for ~2 minutes of wall-clock time without issuing
   any commands.
3. Observe the OBDH screen → "Buffer Fill – HK TM" field.

**Expected**: 0% (bootloader should emit only SID 11 beacons at 30 s cadence,
and with the recent `sw_image != 0` gate in `_enqueue_tm`, nothing should be
routed to the HK store at all during bootloader).

**Actual**: 353% after ~2 minutes.

## Preliminary analysis

This parameter is not reading from `OnboardTMStorage.get_status()[0]['fill_pct']`
— a circular store physically cannot exceed 100% because
`store_packet_direct` does `store.pop(0)` on overflow. The source must be an
OBDH-model-internal counter in `packages/smo-simulator/src/smo_simulator/models/obdh_basic.py`
(likely `hk_tm_buffer_fill_pct` or similar) that increments on every HK packet
produced and is never capped or decremented.

## Suggested investigation

1. Grep `obdh_basic.py` for `buffer_fill`, `hk_tm`, `tm_buffer` to find the
   writer.
2. Confirm the computation: is it `written / capacity × 100` with `written`
   being a monotonic counter? That would exactly match the symptom.
3. Either (a) switch to reading from `OnboardTMStorage.get_status()`
   directly — the authoritative source — or (b) clamp and decrement the
   OBDH-local counter properly on dump/drain.

## Affected files

- `packages/smo-simulator/src/smo_simulator/models/obdh_basic.py`
- MCS OBDH screen widget binding (frontend package)

## Acceptance criteria

- Bootloader at real-time for 10 minutes shows 0% fill.
- Post-boot nominal operation shows fill % matching
  `OnboardTMStorage.get_status()[0]['fill_pct']` within ±1%.
- Fill % is clamped to [0, 100].
- New unit test in `tests/test_simulator/` that writes > capacity packets
  and asserts the parameter reads 100% (or wraps correctly) and never
  exceeds 100%.

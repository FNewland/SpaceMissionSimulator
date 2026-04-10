## Summary

The S15 HK_Store was sized at 5,000 slots, which holds only ~50 minutes of
housekeeping at the configured SID cadences (~1.64 pkts/s nominal). This is
less than one orbit and insufficient to guarantee continuous HK coverage
between ground contacts.

## Severity

Major — risk of HK gap across ground passes, which breaks trend analysis and
post-pass anomaly investigation.

## Status

**Fixed** in `packages/smo-simulator/src/smo_simulator/tm_storage.py` —
`DEFAULT_STORES[1]['capacity']` increased from 5000 to 18000, with a sizing
comment documenting the per-SID packet rate calculation and 2× headroom.

## Sizing rationale

Per-second HK packet rate (from
`configs/eosat1/telemetry/hk_structures.yaml`):

| SID | Subsystem | Interval | Rate (pkt/s) |
| --- | --------- | -------- | ------------ |
| 1   | EPS       | 1 s      | 1.000        |
| 2   | AOCS      | 4 s      | 0.250        |
| 3   | TCS       | 60 s     | 0.017        |
| 4   | TTC       | 8 s      | 0.125        |
| 5   | Payload   | 8 s      | 0.125        |
| 6   | OBDH      | 8 s      | 0.125        |
| 11  | Beacon    | 30 s     | 0.033 (bootloader only) |
| **Total nominal** |    |          | **~1.64**    |

90 min × 60 s × 1.64 ≈ 8,870 packets. With 2× headroom for cadence changes,
S3.27 on-demand reports, and faster sampling during checkout: **18,000 slots**.

## Acceptance criteria

- [x] HK_Store capacity = 18,000
- [x] HK_Store is circular (overwrites oldest on wrap so newest data always wins)
- [x] Sizing comment documents the calculation
- [ ] Regression test asserts capacity ≥ 18,000 *and* that a synthetic
      90-minute HK stream does not lose any packets to wrap
- [ ] Unit test updated (`tests/test_simulator/test_scheduler.py` — done,
      assertion now `18000`)

## Affected files

- `packages/smo-simulator/src/smo_simulator/tm_storage.py`
- `tests/test_simulator/test_scheduler.py`

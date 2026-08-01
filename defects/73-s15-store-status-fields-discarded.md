## Summary

S15 on-board storage was in better shape than the rest of the services audited:
enable (15,1), disable (15,2), dump (15,9) and clear (15,11) all work end to end
on board and are all in the command catalogue. Verified against a real engine:

```
A. initial store status (id:(count,enabled)): {1: (26, True), 2: (13, True), 3: (0, True), 4: (8, True)}
B. TC(15,2) DISABLE store1 -> enabled=False, count 26 -> 26 (frozen=True)
C. TC(15,1) ENABLE  store1 -> enabled=True,  count now 33 (grew=True)
E. store4 count before clear: 10 ; TC(15,11) DELETE store4 -> count: 0
F. TC(15,9) DUMP store1: 33 packets queued; dump file written at queue time
```

The gap was the **status report**. `OnboardTMStorage.get_status()`
(`tm_storage.py:196-215`) computes `name`, `fill_pct`, `overflow`, `circular`,
`wrap_count`, `oldest_ts` and `newest_ts` — and the TM(15,14) record was packed
`'>BHHB'`: id, count, capacity, enabled. **Five computed fields were thrown away
on the way out of the spacecraft:**

```
D. TM(15,14) status: n_stores=4, 25 bytes; record BHHB
   (no name, no fill%, no overflow, no circular flag, no wrap count)
```

The MCS already has the gauge (`updateStorageStatus()`), and could show
OVERFLOW / WRAP badges — it just had no data to show them from. An operator
could not tell a circular store that had wrapped (and silently lost the oldest
telemetry) from one that had not.

## Severity

**Minor/Major** — the service works; the operator's view of it is impoverished,
and the one genuinely dangerous condition (a wrapped circular store) was
invisible.

## Status

**Fixed.**

The four working operations were **not** re-implemented — they were already
correct. Only the status report changed:

```
TM(15,14)  "store status report"   big-endian

  header   n_stores    uint8

  record   id          uint8
           count       uint16
           capacity    uint16
           enabled     uint8
           fill_pct_x10 uint16   fill percentage x 10 (e.g. 0.0-1000)
           flags       uint8     bit0 = overflow, bit1 = circular
           wrap_count  uint16
           name_len    uint8
           name        name_len bytes, ASCII
```

Records are variable-length; walk them with `12 + name_len`. `fill_pct` is
scaled by 10 to keep one decimal place without a float. The four shipped stores
report as `HK_Store` (18000, circular), `Event_Store` (2000), `Science_Store`
(10000) and `Alarm_Store` (500).

### Disk-archive semantics: deliberately unchanged

`engine.queue_dump()` writes the complete raw dump to
`${SMO_DUMP_DIR:-mission.dump_dir:-workspace/dumps}/dump_sid<NN>_<ISO>.bin`
**at queue time**, regardless of RF coverage, and an on-board clear (TC(15,11))
does not touch it. That is correct and is left alone: a `.bin` in
`workspace/dumps` is *the ground's copy of telemetry that was already
downlinked*, and deleting it in response to a spacecraft command would model the
ground losing data it already had. It is the input to `tools/tm_report.py` and
`tools/delayed_tm_viewer.py`. A regression test pins it
(`test_disk_archive_is_written_at_queue_time_and_survives_clear`).

Store definitions remain in code (`tm_storage.py` `DEFAULT_STORES`) rather than
config; moving them to `configs/eosat1/telemetry/tm_stores.yaml` is forward work.

## Files

- `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
  (`_handle_s15` subtype 13)
- Tests: `tests/test_simulator/test_pus_service_fixes.py` (`TestS15StoreStatus`)

## Related

- Hand-off to the MCS: the S15 panel has Enable / Disable / Dump per store but
  **no Clear button**, even though TC(15,11) works and the catalogue already
  marks `DELETE_STORE` `criticality: caution` — add it behind a `confirm()`.
  Dump also has no progress feedback; the handler already returns
  `generate_s1_progress(0, count)`, which is enough for a per-store progress bar.
- Defect #72 — same class of finding (a report the spacecraft computes and then
  discards on the way out).

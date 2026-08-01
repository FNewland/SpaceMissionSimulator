## Summary

The on-board schedule report existed and was well-formed, but carried nothing an
operator could act on.

`TCScheduler.list_commands()` (`tc_scheduler.py:45-52`) **deliberately dropped
the packet** — "return list of scheduled commands (without raw packets)" —
returning only `[{'id': ..., 'exec_time': ...}]`. `_handle_s11` subtype 17 then
packed `count(uint16)` plus `id(uint16) + exec_time(uint32)` per entry into
TM(11,18).

Three genuinely meaningful TCs were scheduled (PLD set-mode IMAGING, PLD capture,
EPS payload line OFF), each embedded as a complete CCSDS packet:

```
scheduler store: [{'id': 1, 'exec_time': 838557417}, {'id': 2, ...}, {'id': 3, ...}]
scheduler internal record keys: ['id', 'exec_time', 'packet']
TM(11,18): n=3 payload=20 bytes hex=0003 0001 31fb5ee9 0002 31fb5f25 0003 31fb5f61
   entry 0: id=1 exec_time=838557417  <-- NO service/subtype/name/apid
```

The packet **was** on board — `'packet'` is in the record — the report simply did
not include it. So the operator had no way to find out what was queued on the
spacecraft: even a fully-rendered table of `(id, exec_time)` says nothing about
*which* command fires. `TCScheduler.get_status()` already computed
`enabled`/`count`/`next_id` and nothing sent those either.

## Severity

**Major** — an on-board schedule an operator cannot inspect is an operations
hazard: they can delete entry 2 but not know what entry 2 does.

## Status

**Fixed.**

`TCScheduler.insert()` now decommutates the embedded TC **once, at insert time**
and caches its identity alongside the packet: `apid`, `service`, `subtype`,
`data_len` and the first `DETAIL_DATA_BYTES` (8) of application data — enough to
carry an S8 `func_id` and its arguments, which is what the ground needs to
resolve a mnemonic from `tc_catalog.yaml`. Decommutation is best-effort: a
malformed embedded packet still schedules and still executes, it just cannot be
described. The raw packet is untouched, so execution is unchanged.

TM(11,18) was **extended in place** rather than adding a detail subtype: there is
one report, it is the one the ground already requests with TC(11,17), and
nothing on the ground parsed the old layout, so there was no compatibility to
preserve.

```
TM(11,18)  "detailed on-board schedule report"   big-endian

  header   count             uint16
           schedule_enabled  uint8    TCScheduler.enabled
           next_id           uint16   id the next insert will get

  record   id                uint16   (repeated `count` times, ordered by exec_time)
           exec_time         uint32   CUC seconds
           apid              uint16   APID of the embedded TC
           service           uint8
           subtype           uint8
           data_len          uint8    full application-data length (capped 255)
           n_bytes           uint8    number of detail bytes that follow
           data              n_bytes  first bytes of the application data
```

Records are variable-length; walk them with `12 + n_bytes`. Entries are sorted by
execution time.

## Files

- `packages/smo-simulator/src/smo_simulator/tc_scheduler.py`
  (`insert`, `list_commands`, `_decommutate_identity`, `DETAIL_DATA_BYTES`)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
  (`_handle_s11` subtype 17)
- Tests: `tests/test_simulator/test_pus_service_fixes.py`
  (`TestS11ScheduleReport`); `tests/test_integration/test_end_to_end.py`
  (`TestS11ScheduleList` updated to the new layout)

## Related

- Hand-off to the MCS: `TMProcessor` has no S11 branch, and `#s11-tbody`
  (`index.html:2091`) is referenced exactly once in the whole file and never
  written to — "Refresh List" sends TC(11,17) and produces no visible change.
  With this report the table can render `ID | Exec (UTC + CUC + T-...) | mnemonic
  | args | [Delete]`; resolve `service/subtype/data[0]` against the already-loaded
  `self._tc_catalog`, and `exec_time` via the inverse of `_utc_to_obc_cuc()`.
  `_embed_s11_packet` already logs MCS-scheduled entries as `S11-DEFERRED:S...`
  in the verification log, so the two views can be cross-linked.
- Defect #46 — the server-side CCSDS wrapping that puts a real packet in the
  store in the first place.
- Roadmap N-7 — `PUS_SERVICE_REFERENCE.md` still documents S11 subtype 11 as
  "insert", which is delete-all.

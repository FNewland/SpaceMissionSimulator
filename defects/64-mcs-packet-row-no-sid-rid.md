# Defect 64 — The TM PACKETS window showed no SID and no RID, although both were already in the WebSocket message

## Summary

Every row of the TM PACKETS window was one flattened string:

```js
// index.html, old handleTMPacket()
tmLog.unshift({ ts,
  text: `S${pkt.service}.${pkt.subtype} APID=${pkt.apid} SEQ=${pkt.seq} (${pkt.data_len}B)` });
```

so the operator saw timestamp, service.subtype, APID, sequence count and
data-field length — and nothing that identifies **which** housekeeping structure
or **which** event definition the packet carries.

Both identifiers were already on the wire *and already in the browser*:

| Field | On the wire | Parsed by the MCS | In the WS message |
|---|---|---|---|
| HK **SID** | `tm_builder.py` — `struct.pack('>H', sid)` is the first field of every S3.25 data field | `tm_processor._process_hk()` | `server.py` → `info["hk_decoded"] = {"sid": …, "param_count": …}` |
| S5 **RID** (event definition id) | `tm_builder.py` — `struct.pack('>HB', event_id, severity)` | `server.py` S5 branch | `info["event"] = {"event_id": …, …}` |

The client used `event_id` only to build a text string for the EVENTS tab; the
TM PACKETS tab never mentioned it. `hk_decoded.sid` was not read at all — and
neither was `hk_decoded.unknown_sid`, so a packet whose SID the MCS cannot
decode was rendered identically to a good one.

Two further already-forwarded, already-unused report identifiers had the same
fate: `info.monitoring.param_id` (S12.9/.10) and `info.s20_param.id` (S20.2).

## Severity

**Major (operational).** "Is SID 4 still coming down?", "did the spacecraft
report RID 0x0102?" are basic console questions, and the packet list — the one
display that exists to answer them — could not. It also silently swallowed
unknown-SID packets, which are exactly the ones worth noticing.

## Status

**Fixed.**

1. **Structured rows.** `buildPacketRow(pkt, ts)` (pure, unit-tested) replaces
   the string with `{ts, svc, sub, apid, seq, len, sid, rid, unknownSid, info}`.
2. **Real columns.** `packetRowCells()` produces
   `TIME · PKT · APID · SEQ · SID · RID · LEN · INFO`, rendered as fixed-width
   spans (`.log-sid`, `.log-rid`, …) under a persistent column header that is
   shown only on the TM tab. **Cells are blank where the column does not
   apply** — an S5 event has an RID and no SID, an S3.25 packet the reverse —
   so the grid stays readable and aligned in the existing style.
3. **Per-service ID derivation.** S3.25 → SID; S5.x → RID (`0x0102`);
   S12.9/.10 → monitored parameter id in the RID column; S20.2 → parameter id;
   S1.x → the TC sequence being reported on, in the INFO column.
4. **Unknown SIDs are surfaced**, not swallowed: the SID cell gets
   `.log-sid.unknown` (red, bold) and the INFO cell reads
   "UNKNOWN SID — not in hk_structures.yaml".
5. The EVENTS tab line for an S5 event now also names the RID
   (`EVT-258 (RID 0x0102): …`) so the two tabs can be cross-referenced.

## Acceptance criteria

- An S3.25 packet renders its SID in the SID column and leaves RID blank.
- An S5 packet renders `0x0102` in the RID column and leaves SID blank.
- `hk_decoded.unknown_sid` produces a visible marker.
- S12 and S20 reports put their parameter id in the RID column.

## Affected areas

- `packages/smo-mcs/src/smo_mcs/static/index.html` — `buildPacketRow`,
  `packetRowCells`, `hex4`, `makeLogRow`, the `#log-colhdr-tm` header and the
  `.log-pkt/.log-apid/.log-seq/.log-sid/.log-rid/.log-len` CSS.
- Tests: `tests/test_mcs/test_mcs_page_behaviour.py::TestPacketRowSidRid`.

## Forward work (not in this change)

- **Name resolution.** `event_catalog.yaml` has a name for all 110 RIDs and
  `hk_structures.yaml` names every SID, but no MCS endpoint serves either
  (`/api/hk-structures`, added under defect #68, now covers SID names; an
  `/api/event-catalog` is still missing). A NAME column is ~10 lines once it
  exists.
- A per-column "SID = n" filter in the existing `.bp-hdr` filter slot, now that
  the field is structured.

## Related

- Defect #63 (log scrolling) — same rendering path.
- Defect #68 (`/api/hk-structures`) — the source of SID names.

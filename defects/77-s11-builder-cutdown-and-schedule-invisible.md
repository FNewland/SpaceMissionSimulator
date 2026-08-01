## Summary

Two halves of the same problem: an operator could neither build a time-tagged
command properly nor see what was already queued on board.

### The builder was a cut-down duplicate

There were **two independent, hand-written command builders**. The Commanding
tab's had a service dropdown, structured per-field typing, a 90-entry S8 function
picker, byte-count feedback, subtype validation and a hex preview. The S11
"Scheduler" panel had four bare inputs — `#s11-exec-time` (raw CUC seconds),
`#s11-svc` (number, default 8), `#s11-st` (number, default 1) and `#s11-data`
(free-text hex) — string-concatenated by `s11AddCommand()`. No function dropdown,
no per-field typing, no subtype validation, no name, no byte preview, no
confirmation of what the inner command actually was, and **no UTC→CUC helper**
(one existed, `s9UpdateCucPreview()`, wired only to the S9 tab). The operator had
to compute a raw Unix-epoch second count by hand.

The full builder's own `'11.4'` entry was barely better: `exec_time(int,4)`,
`svc(int,1)`, `st(int,1)`, `data(hex_bytes)` — the inner command still a hex blob
typed by hand.

Underneath both, `PUS_FIELD_DEFS` + `FUNC_CATALOG` + `VALID_PUS_SUBTYPES` +
`STORE_CATALOG` were a hand-maintained JS duplicate of `tc_catalog.yaml` covering
40 of 188 commands. `PUS_FIELD_DEFS` had **no `18.x` entry**, the service
`<select>` had **no `value="18"`**, and `VALID_PUS_SUBTYPES` had **no key 18** —
so the S18/OBCP commands added by defect #48 could not be selected in the command
builder at all, even though `tc_catalog.yaml` and `mcs/pus_services.yaml` both
define them.

### The on-board schedule was invisible

`#s11-tbody` was referenced **exactly once** in the whole 6295-line file — its own
`<tbody>` tag. Nothing ever wrote a row into it. `TMProcessor.process_packet`
handled S3.25, S5.x and S12.9/10 only; `_handle_tm_packet` had no S11 branch. So
TM(11,18) produced one opaque line in the raw TM log (`S11.18 APID=… SEQ=… (20B)`)
and "Refresh List" sent TC(11,17) and produced **no visible change whatsoever**.

Net: the operator saw an empty table and had no way to find out what was queued on
the spacecraft — while still being offered a Clear All button.

## Severity

**Major** — deferred commanding you cannot inspect and cannot compose safely.
Both halves are training hazards in their own right.

## Status

**Fixed.**

### One builder, two instances

`renderCommandBuilder(containerEl, {prefix, onSend, sendLabel, existing})`
extracts the builder into a component parameterised by an id **prefix**. All the
behaviour functions — `updateStructuredFields`, `packStructuredFields`,
`packPusFields`, `currentCmdLengthInfo`, `updateCmdBytesChip` — now take that
prefix, and `CMD_BUILDERS` holds the live instances.

The Commanding tab keeps its markup inline (`existing: true`) and registers as
the `cmd` instance: same ids (`cmd-service`, `cmd-subtype`, `cmd-data`,
`cmd-send-btn`, `sf-<field>`), which the acceptance and E2E suites assert against
the served HTML. It is the pre-rendered instance of the same component. The S11
panel gets a generated `s11cb` instance whose structured-field ids are namespaced
(`s11cb-sf-<field>`) so two builders can be open at once.

A test packs the identical command through both instances and asserts the data
fields are byte-for-byte equal.

### Driven off /api/tc-catalog

- The **service dropdown** is generated from the catalogue — S18 appears for
  free, along with S2 and S13.
- A new **command picker** lists every catalogued command for the selected
  service (`S18.3 OBCP_START (3 B)`), sets the subtype and generates the fields.
  This also disambiguates entries that share a service.subtype — S6.2 is both
  `MEM_LOAD` and three AOCS torque-gain writes.
- **Field definitions** come from the catalogue's own `fields[]`
  (`uint8`/`uint16`/`uint32`/`float32`/`bytes`), with widget selection by field
  name: `func_id`/`action_func_id` → function picker, `store_id` → store picker,
  `check_type` and `trigger_kind` → labelled selects, `param_id`/`mon_id`/
  `address`/`sid`/`trigger_id` → hex inputs. `PUS_FIELD_DEFS` survives only as a
  fallback when the catalogue is unreachable.
- **Subtype validation** now uses `validSubtypesFor()`, which reads the
  catalogue; the literal table is the offline fallback (and gained S2, S18 and
  the missing S12.3/12.4).

### Time-tag widget

Three modes next to the S11 builder — absolute UTC (ISO 8601), relative
("now + N minutes"), or raw CUC — with a live preview showing the resolved UTC,
the CUC value in decimal and hex, and the T− countdown, turning red when the tag
is in the past. "Now" is the **MCS ground clock** (`state.ground_utc`), not the
browser clock, so it tracks sim time.

`onSend` prefixes the packed inner TC with `exec_time(u32)` and sends TC(11,4);
`MCSServer._embed_s11_packet` continues to wrap the inner svc/st/data into a
complete CCSDS/PUS packet server-side, unchanged.

### The schedule table

TM(11,18) is parsed server-side by `pus_reports.parse_s11_schedule()`:

```
header  count u16, enabled u8, next_id u16
record  id u16, exec u32 (CUC), apid u16, svc u8, st u8,
        data_len u8, n u8, data[n]        VARIABLE, stride 12 + n
```

Records are walked **per record** using each one's own `n`. A derived stride —
payload length divided by record count, which is what the MCS did for TM(15,14) —
desynchronises the parse the moment two records differ in length; a test builds
four records of 13/14/28/12 bytes and asserts no single stride fits.

`_annotate_schedule()` then adds the two things only the ground knows:

- **the mnemonic**, resolved through the already-loaded `tc_catalog` — including
  the S8 `func_id` from the first data byte, so `S8.1 1C` reads as
  `PLD_CAPTURE_IMAGE` and not as "the first S8.1 entry in the catalogue";
- **the execution UTC**, converted from on-board CUC by `_obc_cuc_to_utc()`, the
  inverse of `_utc_to_obc_cuc()`, anchored on OBC time telemetry (0x0309) when
  available so clock drift is included.

`#s11-tbody` renders `ID | Exec (UTC) | T− | CUC | Command | APID | Svc/St | Data
bytes | Delete`, plus the header's schedule-enabled badge and next id. The report
also rides on the 1 Hz state broadcast so the table survives a tab switch or
reload without re-issuing TC(11,17). Entries come back sorted by execution time.
TM(15,14) now uses the same variable-stride walk, recovering the store name, fill
percentage, overflow, circular and wrap-count fields the old reader discarded.

## Files

- `packages/smo-mcs/src/smo_mcs/pus_reports.py` — `parse_s11_schedule`,
  `parse_s15_store_status`
- `packages/smo-mcs/src/smo_mcs/server.py` — S11.18 branch in `_process_tm`,
  `_annotate_schedule`, `_obc_cuc_to_utc`, `_resolve_command_name`,
  `GET /api/schedule`, schedule on the state broadcast, S15.14 re-parse
- `packages/smo-mcs/src/smo_mcs/static/index.html` — `renderCommandBuilder` and
  the prefix-aware builder functions, `catalogFieldDefs`,
  `populateServiceOptions` / `populateCommandPicker`, `validSubtypesFor`,
  the rebuilt S11 panel, `s11InitTimeTag` / `s11ResolveTimeTag` /
  `s11AddFromBuilder` / `s11BuildS114Data`, `renderS11Schedule`
- Tests: `tests/test_mcs/test_pus_reports.py` (`TestS11Schedule`,
  `TestS15StoreStatus`), `tests/test_mcs/test_schedule_memory_stations.py`
  (`TestScheduleDisplay`), `tests/test_mcs/test_mcs_fdir_page.py`
  (`TestCommandBuilderComponent`, `TestScheduleRendering`)

## Related

- Defect #72 — the sim-side fix that put the command identity into TM(11,18) in
  the first place. This is the ground half.
- Defect #73 — the S15 store status fields; the shared variable-stride reader
  picks those up too.
- Defect #46 — `_embed_s11_packet`, the server-side CCSDS wrapping that both
  builder paths still rely on.
- Defect #67 — `/api/tc-catalog` and the byte-count chip, which this pass extends
  from one builder to all instances.
- **Hand-off to the procedures-convergence agent:** `renderCommandBuilder()` is
  the intended single place that knows how to pack a PUS TC. The procedure
  builder's `command` step should instantiate it rather than growing a third
  copy; pass a `prefix` and an `onSend` that writes the step instead of
  uplinking.

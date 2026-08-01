## Summary

The MCS "Memory Browser" (PUS Services → S6 Memory) carried its own hard-coded
copy of the spacecraft address space — eleven regions in a JavaScript array
inside `loadMemoryMap()`, introduced by a comment that admitted the problem:

```js
function loadMemoryMap() {
  // Hardcoded from config — could fetch from API
  const regions = [
    { name: 'Boot ROM',       start: 0x00000000, size: 0x10000,  type: 'readonly' },
    { name: 'Application A',  start: 0x00100000, size: 0x80000,  type: 'flash' },
    { name: 'Science Store',  start: 0x00500000, size: 0x800000, type: 'flash' },
    { name: 'Scratchpad RAM', start: 0x20000000, size: 0x40000,  type: 'ram' },
    { name: 'Stack/Heap',     start: 0x20040000, size: 0x20000,  type: 'ram' },
    …
  ];
```

Defect #71 made `configs/eosat1/memory/memory_map.yaml` the single declaration of
the address space and rebuilt the simulator's `MemoryImage` on it. The MCS was
never updated, so the console and the spacecraft disagreed about **which regions
exist** (the UI invented `HK Store`, `Event Store`, `Science Store`, `Alarm Store`
and `Stack/Heap`, and had never heard of `aocs_gains`), about **their extent**,
and about **who may write where** — the UI's `type` field was decorative and
nothing checked it before uplinking a MEM_LOAD.

The workflow was equally thin:

- Only Dump and Checksum existed. There was **no patch path at all** — no way to
  enter or upload patch data, no MEM_LOAD button, no load→verify sequence, no CRC
  comparison. The S6 upload/verify workflow the memory map exists to support was
  simply absent from the console.
- Both requests used the **historical address-first wire form**
  (`address(4) + length(2)`), not the catalogued
  `memory_id(1) + address(4) + length(2)` form that the shipped
  bootloader-recovery procedures use.
- `TM(6,6)` and `TM(6,10)` were **not decoded**. `_process_tm` had no S6 branch,
  so a dump landed in the raw packet log as an opaque `(NB)` line. The
  `#mem-dump-result` box was written once, with the placeholder string
  `"Requesting dump at 0x…"`, and never again. A CRC result was never displayed
  anywhere.

## Severity

**Major** — a memory map the console invents, with no verify step, is how you
patch the wrong address and never find out.

## Status

**Fixed.**

### The map comes from the configuration

`GET /api/memory-map` serves `configs/<mission>/memory/memory_map.yaml` — the
same declaration `smo_simulator.memory_map.MemoryMap.load()` reads — as
`{regions: [{name, start, size, end, access, writable, fill_kind, description,
binding}], limits: {max_load_bytes, max_dump_bytes, max_check_bytes}, dumps,
checks}`. The eleven JavaScript regions are gone; a test asserts every region,
extent and access right matches the YAML, and another asserts the invented names
no longer appear anywhere in `index.html`.

The table now shows start, **end**, size, access, fill kind and description, and
flags regions with a live model `binding:` — a patch to `aocs_gains` changes
spacecraft behaviour, not just bytes, and the operator is told so.

### Patch / verify workflow

A three-step strip, backed by the real limits from the YAML:

1. **LOAD (S6.2)** — hex entry or binary file upload (truncated to
   `max_load_bytes` with a message saying so). Before uplinking, the console
   checks the address against the declared map and refuses locally with the
   error the spacecraft would return: `0x0001` illegal address for an unmapped
   range, `0x0002` write protected for an `ro` region, and the byte limit.
   The expected CRC-16-CCITT of the patch is computed on the ground and shown.
2. **VERIFY (S6.9)** — TC(6,9) over exactly the patched range.
3. **Read back (S6.5)** — optional dump of the same range.

The ground CRC uses the same polynomial and seed as
`ServiceDispatcher._crc16_ccitt` (0x1021, init 0xFFFF); a test pins the JS
implementation against the Python one.

### Reports are decoded and rendered

`pus_reports.parse_s6_dump()` / `parse_s6_check()`:

```
TM(6,6)   address u32, length u16, data[length]
TM(6,10)  address u32, length u16, crc u16
```

- **Dumps** render as a proper hex dump — 16 bytes per line, offset column, hex
  column, ASCII column — with the containing region named and a `[TRUNCATED]`
  marker when the report carries fewer bytes than it declares.
- **CRC results** go into a table: address, region, length, reported CRC,
  expected CRC, and a **MATCH / MISMATCH** verdict when the range matches the
  last patch. That verdict is the point of the whole workflow and did not exist.

Both request types now use the catalogued `memory_id + address + length` form,
and the server tags every dump and check with the region it fell in
(`_memory_region_name`), so an unmapped address reads as unmapped instead of
silently looking fine.

## Files

- `packages/smo-mcs/src/smo_mcs/pus_reports.py` — `parse_s6_dump`,
  `parse_s6_check`
- `packages/smo-mcs/src/smo_mcs/server.py` — `_load_memory_map`,
  `_memory_region_name`, S6.6/S6.10 branches in `_process_tm`,
  `GET /api/memory-map`
- `packages/smo-mcs/src/smo_mcs/static/index.html` — rebuilt S6 pane, region
  picker, patch/verify strip, `loadMemoryMap` / `renderMemoryMap` /
  `memPatchLoad` / `memPatchVerify` / `renderMemoryDump` / `renderMemoryCheck` /
  `crc16Ccitt`
- Tests: `tests/test_mcs/test_pus_reports.py` (`TestS6Reports`),
  `tests/test_mcs/test_schedule_memory_stations.py` (`TestMemoryMapApi`),
  `tests/test_mcs/test_mcs_fdir_page.py` (`TestMemoryBrowser`)

## Related

- Defect #71 — the sim-side fix that created `memory/memory_map.yaml` and made
  patches actually stick. Its hand-off asked the MCS to serve the map rather than
  duplicate it; this is that.
- `configs/eosat1/memory/memory_map.yaml` is read-only to this pass — the console
  reflects whatever it declares and never overrides it.

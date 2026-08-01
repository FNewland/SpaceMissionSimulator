## Summary

PUS Service 6 was a façade over a synthetic memory image. Exactly one 12-byte
special-cased block — the AOCS torque gains at 0x20100000 — was genuinely
writable and read-backable. Everything else about the patch-upload workflow was
inert.

```
A. dump before: 0000000000000000
   MEM_LOAD responses: 1 (S1.5 progress)
   dump after : 0000000000000000  <-- ROUND-TRIP? False
B. gain dump before: 3f8000003f8000003f800000
   gain dump after : 402000004060000040900000 = (2.5, 3.5, 4.5)   <-- the one working case
C. write to 0x00000000 (boot ROM)             -> S1.8 exec-fail (write protected)
   write to 0x00020000 (Boot Backup per UI map) -> ACCEPTED (UI says readonly)
   write to unmapped 0xDEADBEEF                 -> ACCEPTED (no region check)
D. MEM_DUMP requested 4096 bytes -> returned 256 bytes (hard cap min(length,256))
   MEM_CHECK over 4096 bytes -> declared len=4096 crc=0x0ED9 (NOT capped)
E. MEM_CHECK CRC before/after a 64-byte patch: 0x0ED9 / 0x0ED9  CHANGED=False
```

1. **No backing store.** A MEM_LOAD anywhere other than 0x20100000-0x2010000B
   was logged, acknowledged and discarded. `_generate_memory_content()`
   re-synthesised bytes from the address on every read, so MEM_DUMP could not
   round-trip a patch and MEM_CHECK's CRC was computed over content that could
   never contain one (item E) — **the entire verification half of a patch
   workflow was dead.**

2. **The map was hard-coded in three disagreeing places**: the dispatcher's
   `_generate_memory_content`, the dispatcher's `_is_memory_readonly` (a single
   `0x0 <= addr < 0x20000` test), and the browser's `loadMemoryMap()` (11 regions
   in JS, with the comment *"Hardcoded from config — could fetch from API"*).
   The UI declared "Boot Backup 0x00010000+0x10000" read-only but only
   0x0-0x1FFFF was enforced; the UI's map had no entry at all for the AOCS gain
   block. Nothing was in `configs/`.

3. **Unmapped addresses were accepted.** 0xDEADBEEF was a valid write target.

4. **Catalogue and dispatcher disagreed on the wire format.**
   `tc_catalog.yaml:410-434` declares `memory_id(uint8) + address(uint32) + ...`;
   the dispatcher read `address` from bytes 0-3. Empirically a catalog-shaped
   MEM_LOAD to 0x00100000 decoded as **0x00001000**. The shipped procedures
   follow the catalogue (`contingency/boot_loader_recovery.md:123`,
   `obc_bootloader_recovery.md:147`), so **every documented memory operation
   targeted the wrong address.**

5. **Dump/check size asymmetry.** Dump capped at 256 B, check uncapped — a CRC
   over a 4 KB region could never be reconstructed from dumps.

## Severity

**Major** — "upload a patch and verify it" is a core operations lesson, and none
of upload, read-back or verification actually functioned.

## Status

**Fixed.**

1. **Config-declared memory map.** New `configs/eosat1/memory/memory_map.yaml`
   is now the single declaration of the address space: seven regions
   (`boot_rom`, `boot_backup` both `ro`; `app_a`, `app_b`, `config`,
   `scratch_ram`, `aocs_gains` all `rw`), each with `start`, `size`, `access`,
   `fill_kind`/`fill` and `description`, plus a `limits:` block. Loaded by the
   new `smo_simulator.memory_map.MemoryMap.load()`, with `MemoryMap.default()`
   reproducing the previous layout byte-for-byte so minimal test config dirs
   keep working. The fill patterns are the old synthetic ones, so an *unpatched*
   dump returns exactly the bytes it did before.

2. **A real sparse backing store.** `MemoryImage` is page-backed
   (256-byte pages, materialised only on write). `read()` composes written pages
   over the region fill pattern. Consequences: MEM_LOAD -> MEM_DUMP round-trips,
   and MEM_CHECK's CRC **changes** when a patch lands, which is the point of the
   workflow. `get_state`/`set_state` are wired into `BreakpointManager` exactly
   as `OnboardTMStorage` and `ObcpManager` are, so a verified patch survives a
   breakpoint round trip.

3. **`binding:` regions generalise the AOCS-gain special case.** A region may
   declare a model, an optional setter and a list of `{offset, type, attr, key}`
   fields; reads render the *live* model values and writes push into the model.
   `aocs_gains` is now the first instance of a general mechanism rather than a
   one-off, and a bound region is a window, not a copy — a model-side change is
   visible in the next dump. Partial writes (e.g. +4 only) still touch one axis.

4. **Access policy enforced from the map, with the right failure codes:**

   | Condition | S1.8 code |
   |---|---|
   | address (or address+length) not inside any declared region | `0x0001` illegal address |
   | region declares `access: ro` | `0x0002` write protected |
   | malformed / zero-length request | `0x0003` bad argument |
   | request longer than the configured limit | `0x0004` length exceeded |

   Applied to all three subtypes, so an unmapped MEM_DUMP or MEM_CHECK is
   rejected too, not just a load. `boot_backup` is now genuinely read-only,
   matching what the UI always claimed.

5. **Dump and check made consistent.** Both honour `limits:`
   (`max_dump_bytes: 1024`, `max_check_bytes: 0x100000`) and both read the same
   image, so a CRC is reconstructible from dumps. The silent `min(length, 256)`
   truncation is gone; an over-long dump is rejected explicitly instead.

6. **Both wire formats accepted, catalogue form fixed.** The catalogued
   `memory_id(u8) + address(u32) + ...` form and the historical
   `address(u32) + ...` form are both decoded:

   ```
   MEM_LOAD  (6,2)   [memory_id(u8)] + address(u32) + data[]
   MEM_DUMP  (6,5)   [memory_id(u8)] + address(u32) + length(u16)
   MEM_CHECK (6,9)   [memory_id(u8)] + address(u32) + length(u16)
   ```

   Dump and check are unambiguous by length (6 vs 7 bytes). MEM_LOAD is
   disambiguated by the map: whichever reading lands in a declared region wins,
   which is what makes `MEM_LOAD(memory_id=1, address=0x00100000)` land on
   0x00100000 instead of 0x00001000. When neither reading maps — the request is
   rejected either way — the historical reading is reported, so the failure
   names the address the operator typed.

   TM(6,6) and TM(6,10) are unchanged: `address(u32) + length(u16) + data[]`
   and `address(u32) + length(u16) + crc16(u16)`.

**Multi-packet upload** works as a consequence of (2): successive MEM_LOADs at
increasing offsets accumulate in the image, and one MEM_CHECK over the whole
block verifies them (test `test_multi_packet_upload_then_verify`). A distinct
session protocol (6.1 start / 6.2 data / 6.3 abort) was **not** added — the
`MEM_LOAD_START/DATA/ABORT` catalogue aliases remain aliases of 6.2, as before.

## Files

- `packages/smo-simulator/src/smo_simulator/memory_map.py` (new:
  `MemoryRegion`, `RegionBinding`, `BindingField`, `MemoryLimits`, `MemoryMap`,
  `MemoryImage`, `ERR_*` codes)
- `configs/eosat1/memory/memory_map.yaml` (new)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
  (`_handle_s6`, `_decode_mem_request`, `_memory_image`, `_is_memory_readonly`,
  `_generate_memory_content`; the `_AOCS_GAIN_*` special case and
  `_aocs_gain_load/_aocs_gain_dump` are replaced by the binding mechanism)
- `packages/smo-simulator/src/smo_simulator/engine.py` (`_memory_map`,
  `_memory`)
- `packages/smo-simulator/src/smo_simulator/breakpoints.py` (save/load `memory`)
- `configs/eosat1/commands/tc_catalog.yaml` (S6 descriptions)
- Tests: `tests/test_simulator/test_pus_service_fixes.py`
  (`TestMemoryMapConfig`, `TestS6Patching`, `TestS6BoundRegions`,
  `TestMemoryImageStore`)

## Related

- Hand-off to the MCS: serve the map as `GET /api/memory-map` from
  `configs/eosat1/memory/memory_map.yaml` and delete the 11 hard-coded regions
  in `loadMemoryMap()` (`index.html:5177-5209`); render TM(6,6) into
  `#mem-dump-result`, which today only ever shows "Requesting dump at 0x...".
  A client-side block on `access: ro` regions is now possible from real data.
- The two bootloader-recovery procedures use the catalogued `memory_id` form,
  which now reaches the address they name — no procedure edit is needed.

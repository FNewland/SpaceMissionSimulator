# On-board PUS Services & Procedures — Audit (EO-Sat-1 simulator suite)

Date: 2026-07-28. Repo: `/home/claude/repos/SpaceMissionSimulation` (container-local copy).
READ-ONLY investigation. All line numbers verified against this working copy.
Empirical scripts written to `/tmp/audit/*.py` (not in the repo); each run is quoted verbatim below.

| Q | Topic | Verdict |
|---|---|---|
| Q1 | S11 time-tagged builder vs full command builder | **PARTIAL** — two hand-written, divergent builders; neither can express the embedded TC structurally |
| Q2 | S11 on-board schedule report (11,17 → 11,18) | **BROKEN** — report carries only `(id, exec_time)`; MCS never parses it; the S11 table is never populated |
| Q3 | S6 memory patch upload | **PARTIAL/BROKEN** — only a 12-byte AOCS gain block is really writable; MEM_LOAD elsewhere is a no-op, MEM_CHECK cannot see a patch; map hard-coded in 3 places; catalog field layout wrong |
| Q4 | S15 stores enable/disable/dump/clear | **PARTIAL** — all four work on board and are in the catalog; the S15 UI panel has no Clear, and dump-to-disk is unaffected by clear |
| Q5 | S12 on-board monitoring | **PARTIAL** — genuinely evaluated every tick and edge-triggered (proved); but 4 config rules silently lost, no TM(12,9/10), no per-definition enable/disable, delta checks unimplemented |
| Q6 | S19 event-action | **PARTIAL** — engine evaluates and fires (proved); manage-at-runtime works; but TC(19,8) report returns *nothing* with the shipped rules, ground-added rules are limited to 1-byte event types, 13 actions + ~17 triggers are mis-targeted |
| Q7 | Procedures | **PARTIAL** — the runner genuinely executes end-to-end (proved, both dialects); but there are **four** divergent procedure representations and the 64 shipped `.md` procedures are not executable or even reachable from the MCS UI |

---

## Q1 — Time-tagged command list builder vs the full command builder

**Verdict: PARTIAL.** They are two independent, hand-written UIs. The S11 one is strictly
poorer, and — crucially — even the "full" builder cannot express the *embedded* TC
structurally; both hand the inner command over as a raw hex blob.

### 1.1 What the full command builder can express

Commanding tab, `packages/smo-mcs/src/smo_mcs/static/index.html`:

| Capability | Where | Notes |
|---|---|---|
| Command selection | `index.html:1889-1923` | Service `<select>` (S3,5,6,8,9,11,12,15,17,19,20), Subtype number, Data hex, free-text Name |
| Structured fields | `PUS_FIELD_DEFS` `index.html:3279-3320`; renderer `updateStructuredFields()` `index.html:3402-3465` | 40 service.subtype entries; field types `int / hex_int / float / hex_bytes / param_list / select / func_select / store_select` |
| Function picker | `FUNC_CATALOG` `index.html:3322-3360` | 60-entry S8 func dropdown (`func_select`) |
| Store picker | `STORE_CATALOG` `index.html:3362` | 4 S15 stores |
| Byte preview | `packStructuredFields()` `index.html:3466-3480` → `packPusFields()` `index.html:3376-3400` | "PACK TO HEX" writes the packed hex into `#cmd-data` — the hex *is* the preview |
| Validation | `VALID_PUS_SUBTYPES` `index.html:3482-3494`, used in `sendPUSCommand()` `index.html:3496-3510` | Warns + `confirm()` on an unknown subtype |
| Quick commands | `index.html:1933-2035` | ~60 preset buttons |
| Feedback | Verification log + command history panels `index.html:2027-2043` | Correlated S1 reports |

### 1.2 What the S11 time-tagged builder can express

PUS Services tab → "S11 Scheduler" sub-pane, `index.html:2078-2117`; JS `s11AddCommand()`
`index.html:5141-5153`:

* Four bare inputs — `#s11-exec-time` (raw CUC seconds), `#s11-svc` (number, default 8),
  `#s11-st` (number, default 1), `#s11-data` (free-text hex).
* `s11AddCommand()` string-concatenates `exec(4B) + svc(1B) + st(1B) + data` and calls
  `sendPusCmd(11, 4, …)` (`index.html:5316-5330`).
* **No** function dropdown, **no** per-field typing, **no** subtype validation, **no** name,
  **no** byte preview, **no** UTC→CUC helper (one exists — `s9UpdateCucPreview()`
  `index.html:5275-5286` — but it is wired only to the S9 tab), **no** confirmation of what
  the inner command actually is.
* The result table `#s11-table` / `#s11-tbody` (`index.html:2088-2092`) is **never populated**
  by any JS — `grep` finds `s11-tbody` exactly once in the file (see Q2).

The full builder's own `'11.4'` entry (`index.html:3296`) is barely better: `exec_time(int,4)`,
`svc(int,1)`, `st(int,1)`, `data(hex_bytes)` — the inner command is still a hex blob typed by
hand. So the "full" builder does **not** currently solve the problem either.

The wrapping into a real CCSDS packet is done server-side for both paths by
`MCSServer._embed_s11_packet` (`packages/smo-mcs/src/smo_mcs/server.py:1464-1504`, defect #46),
applied in `_handle_pus_command` (`server.py:1168-1170`) and `_proc_send_command`
(`server.py:1422`). That fix is sound and stays in place under the refactor below.

### 1.3 Systemic observation

`PUS_FIELD_DEFS` + `FUNC_CATALOG` + `VALID_PUS_SUBTYPES` + `STORE_CATALOG` are a hand-maintained
JS duplicate of `configs/eosat1/commands/tc_catalog.yaml` (183 commands). The server already
serves the real thing at `GET /api/tc-catalog` (`server.py:525`, `load_tc_catalog` at
`server.py:221-225`) — **and the UI never calls it** (`grep 'api/tc-catalog'` in `static/` → no
hits; only `/api/param-catalog` is used, `index.html:2964`). Consequences visible today:
`PUS_FIELD_DEFS` has no `18.x` entry, the service `<select>` has no `value="18"`, and
`VALID_PUS_SUBTYPES` has no key `18` — so **the S18/OBCP commands added by defect #48 cannot be
selected in the command builder at all**, even though `tc_catalog.yaml:1230-1262` and
`configs/eosat1/mcs/pus_services.yaml:69` define them.

### 1.4 Proposed refactor — "one builder, used everywhere"

1. **Extract the builder into a reusable component.** Turn the Commanding-tab markup +
   `updateStructuredFields/packStructuredFields/packPusFields` into
   `renderCommandBuilder(containerEl, {onPacked(service, subtype, dataHex, name)})`.
   The Commanding tab instantiates it with `onPacked = sendPUSCommand`.
2. **Embed it in the S11 panel.** Replace `#s11-svc / #s11-st / #s11-data` with a
   `renderCommandBuilder()` instance whose `onPacked` builds
   `exec_time(4) + <packed inner TC>` and sends S11.4 — i.e. the operator picks
   "S8.1 / PLD Capture Image / arg 00" from the same dropdowns, then adds a time tag.
   `_embed_s11_packet` continues to do the CCSDS wrapping on the server. **(S)**
3. **Add a time-tag widget** next to it: UTC datetime input + live CUC preview (reuse
   `s9UpdateCucPreview()` logic; better, call a new `GET /api/utc-to-cuc` that uses the
   server's `_utc_to_obc_cuc()` (`server.py:1506-1528`) so the conversion is anchored on
   real OBC-time TM rather than the browser clock). Offer "relative: now + N minutes". **(S)**
4. **Drive the builder from `tc_catalog.yaml`.** Fetch `/api/tc-catalog` on load and generate
   the field definitions from `fields[]` (`uint8/uint16/uint32/float32/bytes`), keeping
   `PUS_FIELD_DEFS` only as a fallback. This kills the duplicate, brings S18 and the other 140
   catalog commands into the UI for free, and makes catalog/dispatcher mismatches (Q3, Q5)
   visible in one place. **(M)**
5. **Reuse the same component** in the Procedure Builder's `command` step (Q7) and in the S6 /
   S12 / S19 mini-panels, so there is exactly one place that knows how to pack a PUS TC. **(M)**

Total effort: **M** (S for steps 1-3 alone, which already delivers the user's ask).

---

## Q2 — On-board schedule report

**Verdict: BROKEN.** The report exists and is well-formed, but carries no information an
operator can act on, and nothing on the ground decodes it.

### 2.1 Sim side — trace

* Store: `packages/smo-simulator/src/smo_simulator/tc_scheduler.py:22-31` keeps
  `{'id', 'exec_time', 'packet'}` per entry.
* `TCScheduler.list_commands()` (`tc_scheduler.py:45-52`) **deliberately drops the packet**:
  returns `[{'id': …, 'exec_time': …}]` only ("without raw packets").
* `ServiceDispatcher._handle_s11` subtype **17** (`service_dispatch.py:1019-1027`) packs
  `count(uint16)` then `id(uint16) + exec_time(uint32)` per entry → **TM(11,18)**.
* Insert is subtype **4** (`service_dispatch.py:1002-1009`, ack TM(11,5)); 7 = delete-one,
  9 = disable, 11 = delete-all, 13 = enable. Execution: `engine.py:969-975`
  (`due_tcs = self._tc_scheduler.tick(current_cuc)` → `_dispatch_tc`).

### 2.2 Empirical (`/tmp/audit/q2_s11.py`, real `SimulationEngine`)

Three genuinely meaningful TCs were scheduled (PLD set-mode IMAGING, PLD capture, EPS payload
line OFF), each embedded as a complete CCSDS packet, then TC(11,17) was issued:

```
scheduler store: [{'id': 1, 'exec_time': 838557417}, {'id': 2, ...}, {'id': 3, ...}]
scheduler internal record keys: ['id', 'exec_time', 'packet']
TM(11,18): n=3 payload=20 bytes hex=0003 0001 31fb5ee9 0002 31fb5f25 0003 31fb5f61
   entry 0: id=1 exec_time=838557417  <-- NO service/subtype/name/apid
```

The packet is on board (`'packet'` is in the record) — the report simply does not include it.

### 2.3 Ground side — what the operator sees today

* `TMProcessor.process_packet` (`packages/smo-mcs/src/smo_mcs/tm_processor.py:30-42`) handles
  S3.25, S5.x and S12.9/10 only; **S11.18 falls through to `{"service": 11, "subtype": 18}`**.
* `MCSServer._handle_tm_packet` adds `hk_decoded`, `s20_param`, `verification`, `event`,
  `monitoring`, `storage_status` — **no S11 branch** (`server.py:693-880`).
* The browser's `handleTMPacket()` (`index.html:3188-3230`) therefore records one line in the
  raw TM log: `S11.18 APID=… SEQ=… (20B)`.
* `#s11-tbody` (`index.html:2091`) is referenced **once in the whole file** — nothing ever
  writes rows into it. "Refresh List" (`s11ListCommands()`, `index.html:5135-5137`) sends the
  TC and produces no visible change.

**Net: the operator sees an empty table and one opaque log line. There is no way to find out
what is queued on board.** Even if the table were populated, `(id, exec_time)` would tell them
nothing about *which* command fires.

### 2.4 Fix

1. **Sim (S).** Add the identity of the deferred TC to the report. Keep the packet in the store
   (already there); in `list_commands()` also decommutate the stored packet once at insert time
   and cache `{'svc', 'st', 'apid', 'seq', 'data_len', 'first_bytes'}`. Extend the TM(11,18)
   record to `id(u16) + exec_time(u32) + apid(u16) + svc(u8) + st(u8) + data_len(u8) + data[…]`
   (or, cheaper and fully ECSS-flavoured, add subtype **11,19 "detailed schedule report"**
   carrying the full embedded packets and leave 11,18 as the summary). ~25 lines across
   `tc_scheduler.py` + `service_dispatch.py`.
2. **MCS (S).** Parse S11.18/11.19 in `MCSServer._handle_tm_packet` into
   `info["schedule"] = {"count": n, "entries": [...]}` and broadcast it. Resolve `svc/st/func_id`
   to a mnemonic via the already-loaded `self._tc_catalog`, and `exec_time` (CUC) → UTC using the
   inverse of `_utc_to_obc_cuc()`.
3. **UI (S).** Populate `#s11-tbody` from that message: `ID | Exec (UTC + CUC + T-…) | Command
   mnemonic | Args | [Delete]`, with the Delete button firing TC(11,7). Also mark entries the
   MCS itself scheduled — `_embed_s11_packet` already logs them as `S11-DEFERRED:S…` in the
   verification log (`server.py:1494-1503`), so the two views can be cross-linked.
4. **(S)** Report the enable/disable state too: `TCScheduler.get_status()` (`tc_scheduler.py:88`)
   already returns `enabled`/`count`/`next_id` but nothing sends it — add it to the 11,18 header.

Effort: **S** (each of the three layers is a small, independent change).

---

## Q3 — Service 6 memory patches

**Verdict: PARTIAL/BROKEN for "patch upload".** S6 is a façade over a synthetic memory image;
only one 12-byte special-cased block is genuinely writable and read-backable.

### 3.1 What exists

* `_handle_s6` (`service_dispatch.py:` `subtype 2` load / `5` dump → TM(6,6) / `9` check →
  TM(6,10)). Wire format **`address(uint32) + …`** — *no* `memory_id` byte.
* Writable-region policy: `_is_memory_readonly(addr)` — a single hard-coded test,
  `0x00000000 ≤ addr < 0x00020000` (Boot ROM). Everything else, mapped or not, is accepted.
* Memory *content*: `_generate_memory_content(addr, length)` synthesises bytes from the address
  (0x55 for boot ROM, `(addr>>8)&0xFF` for 0x00100000-0x001FFFFF, 0xAA for config, an XOR
  pattern for 0x20000000-0x2003FFFF, zeros elsewhere). **There is no backing store**: a
  MEM_LOAD is logged and acknowledged, then discarded.
* The one real feature: the AOCS torque-gain block, `_AOCS_GAIN_BASE = 0x20100000`,
  `_AOCS_GAIN_SIZE = 12`, `_AOCS_GAIN_AXES = {0:'x',4:'y',8:'z'}`, with
  `_aocs_gain_load()` / `_aocs_gain_dump()` reading and writing the live AOCS model.
* `MEM_CHECK` (6,9) computes CRC-16/CCITT over `_generate_memory_content(...)` — i.e. over the
  *synthetic* image, never over anything uploaded.
* Ground: MCS "Memory Browser" panel `index.html:2119-2144`; `memDump()` `index.html:5155-5167`,
  `memCheck()` `index.html:5168-5176`, `loadMemoryMap()` `index.html:5177-5209`
  (**11 regions hard-coded in JS**, comment: *"Hardcoded from config — could fetch from API"*).
  `#mem-dump-result` (`index.html:2141`) only ever shows
  `"Requesting dump at 0x…, N bytes..."` — the returned TM(6,6) is never rendered.

### 3.2 Empirical (`/tmp/audit/q3_s6.py`)

```
A. dump before: 0000000000000000
   MEM_LOAD responses: 1 (S1.5 progress)
   dump after : 0000000000000000  <-- ROUND-TRIP? False
B. gain dump before: 3f8000003f8000003f800000
   gain dump after : 402000004060000040900000 = (2.5, 3.5, 4.5)
   aocs model torque_gain_x = 2.5
C. write to 0x00000000 (boot ROM) -> 1 response(s) = S1.8 exec-fail (write protected)
   write to 0x00020000 (Boot Backup per UI map) -> 1 response(s) = ACCEPTED (UI says readonly)
   write to unmapped 0xDEADBEEF   -> 1 response(s) = ACCEPTED (no region check)
D. MEM_DUMP requested 4096 bytes -> returned 256 bytes (hard cap min(length,256))
   MEM_CHECK over 4096 bytes -> declared len=4096 crc=0x0ED9 (NOT capped; asymmetric with dump)
E. MEM_CHECK CRC before/after a 64-byte patch: 0x0ED9 / 0x0ED9  CHANGED=False
```

### 3.3 Gaps

1. **No memory backing store.** A patch to any address other than 0x20100000-0x2010000B
   vanishes. `MEM_LOAD → MEM_DUMP` does not round-trip; `MEM_CHECK` cannot detect a patch
   (item E) — so the *entire* verification half of a patch workflow is inert.
2. **The address map is hard-coded in three disagreeing places**: the dispatcher's
   `_generate_memory_content`, the dispatcher's `_is_memory_readonly`, and the browser's
   `loadMemoryMap()`. The UI declares "Boot Backup 0x00010000+0x10000" read-only but only
   0x0-0x1FFFF is enforced; the UI's map has no entry at all for the AOCS gain block at
   0x20100000 (its "Scratchpad RAM" ends at 0x20040000). Nothing is in `configs/`.
3. **Catalog field layout contradicts the dispatcher.** `tc_catalog.yaml:410-434` declares
   `MEM_LOAD` / `MEM_DUMP` / `MEM_CHECK` as `memory_id(uint8) + address(uint32) + …`, but the
   dispatcher reads `address` from bytes 0-3. Empirically a catalog-shaped MEM_LOAD to
   0x00100000 decodes as **0x00001000**. The shipped procedures follow the catalog
   (`configs/eosat1/procedures/contingency/boot_loader_recovery.md:123`,
   `obc_bootloader_recovery.md:147`: `MEM_LOAD(memory_id=1, address=ADDR, data=BLOCK)`), so every
   documented memory operation targets the wrong address. The MCS UI (`PUS_FIELD_DEFS['6.2']`
   `index.html:3290`, `memDump()`) is the only one that matches the code.
4. **Size asymmetry**: dump is capped at 256 B; check is not. A CRC over a 4 KB region can never
   be reconstructed from dumps.
5. **No segmented-load protocol.** `MEM_LOAD_START / MEM_LOAD_DATA / MEM_LOAD_ABORT` are
   *aliases* of the same S6.2 (`tc_catalog.yaml:411`); `nominal/software_upload.md:54-126` writes
   a three-phase upload that does not exist. Max patch size is therefore one TC's data field.
6. **No S6 report rendering** on the ground (`#mem-dump-result` never shows returned bytes).

### 3.4 Proposed design (consistent with what is there)

* **Config-declared memory map** — new `configs/eosat1/memory/memory_map.yaml`:
  ```yaml
  regions:
    - {name: boot_rom,     start: 0x00000000, size: 0x10000,  access: ro, fill: 0x55}
    - {name: app_a,        start: 0x00100000, size: 0x80000,  access: rw, patchable: true}
    - {name: app_b,        start: 0x00180000, size: 0x80000,  access: rw, patchable: true}
    - {name: config,       start: 0x00200000, size: 0x20000,  access: rw, patchable: true}
    - {name: scratch_ram,  start: 0x20000000, size: 0x40000,  access: rw}
    - {name: aocs_gains,   start: 0x20100000, size: 12,       access: rw, binding: aocs_torque_gains}
  limits: {max_load_bytes: 512, max_dump_bytes: 1024, max_check_bytes: 0x100000}
  ```
  Loader alongside `monitoring_loader.py` (`MemoryMapLoader`), consumed by the dispatcher and
  served to the UI as `GET /api/memory-map` so `loadMemoryMap()` stops hard-coding. **(S/M)**
* **A real sparse backing store** in the dispatcher: `dict[int, bytearray]` of *patched pages*,
  written by MEM_LOAD; `_generate_memory_content` becomes the *default* fill for pages that were
  never written. Then MEM_DUMP round-trips and MEM_CHECK's CRC changes when a patch lands —
  which is the whole point of an upload/verify workflow. Add `get_state`/`set_state` and wire
  into `breakpoints.py` exactly as `OnboardTMStorage`/`ObcpManager` do. **(M)**
* **`binding:` regions** generalise the existing AOCS-gain special case: a region can declare a
  model attribute list so a patch changes spacecraft behaviour, not just bytes. Keeps
  `_aocs_gain_load/_aocs_gain_dump` as the first instance rather than a one-off. **(S)**
* **Segmented load** (only if the syllabus needs a realistic SW upload): real subtypes —
  6.2 = load (as today), plus a session model `6.1 start(region, total_len, crc)` /
  `6.2 data(seq, bytes)` / `6.3 abort` / `6.9 check` — mirroring the S13 transfer session
  pattern already in `service_dispatch.py:1161-1277`. Emit S5 events on start/complete/abort so
  S19 can react. **(M/L)**
* **Fix the catalog** to `address(uint32) + data` (drop `memory_id`, or add it to the dispatcher —
  pick one) and re-point the two bootloader-recovery procedures. **(S, but a Flight-Ops content
  decision — same class as §4a items 5-6.)**
* **MCS UI**: render TM(6,6) into `#mem-dump-result` as a hex/ASCII dump; add a "Patch" panel —
  region dropdown (from `/api/memory-map`), offset, hex/file payload, computed local CRC,
  "Upload → MEM_CHECK → compare" one-click verification; block writes into `access: ro`
  regions client-side. **(M)**

Total: **M** for a credible patch-upload workflow, **L** if segmented load + a file-based patch
format are wanted.

---

## Q4 — S15 on-board stores

**Verdict: PARTIAL.** Enable / disable / dump / **clear all exist on board and are all in the
command catalogue**; the gap is the UI and the dump-archive semantics.

### 4.1 Inventory

| Function | On board | Catalog | Full cmd builder | S15 panel |
|---|---|---|---|---|
| Enable store (15,1) | `service_dispatch.py:1288-1290` → `OnboardTMStorage.enable_store` (`tm_storage.py:137-139`) | `ENABLE_STORE` `tc_catalog.yaml:1186-1191` | `PUS_FIELD_DEFS['15.1']` `index.html:3306` | ✅ `s15Enable()` `index.html:5213` |
| Disable store (15,2) | `service_dispatch.py:1291-1293` → `disable_store` (`tm_storage.py:141-143`) | `DISABLE_STORE` `tc_catalog.yaml:1192-1196` | `'15.2'` `index.html:3307` | ✅ `s15Disable()` `index.html:5214` |
| Dump store (15,9) | `service_dispatch.py:1294-1303` → `engine.queue_dump` (`engine.py:1593-1645`), paced by `_tick_dump_emission` (`engine.py:1647-1693`) | `DUMP_STORE` `tc_catalog.yaml:1197-1201` | `'15.9'` `index.html:3308` | ✅ `s15Dump()` `index.html:5215` |
| **Clear / delete contents (15,11)** | `service_dispatch.py:1304-1306` → `delete_store` (`tm_storage.py:145-151`) | `DELETE_STORE` (criticality: caution) `tc_catalog.yaml:1202-1209` | `'15.11'` `index.html:3309` | ❌ **no button** (`index.html:2146-2200` has only Enable / Disable / Dump per store) |
| Status report (15,13 → TM 15,14) | `service_dispatch.py:1307-1318` | `STORE_STATUS` `tc_catalog.yaml:1211-1215` | `'15.13'` `index.html:3310` | ✅ "Refresh Status" |

Stores are defined in code, not config: `DEFAULT_STORES` `tm_storage.py:33-38` — 1 HK (18000,
circular), 2 Event (2000), 3 Science (10000), 4 Alarm (500); routing `SERVICE_TO_STORE`
`tm_storage.py:41-44` (S3→1, S5→2, everything else→3), alarms via `store_alarm()`→4.
Breakpoint persistence: `get_state`/`set_state` `tm_storage.py:157-194`.

### 4.2 Empirical (`/tmp/audit/q4_s15.py`, real engine, `SMO_DUMP_DIR` redirected to a temp dir)

```
A. initial store status (id:(count,enabled)): {1: (26, True), 2: (13, True), 3: (0, True), 4: (8, True)}
B. TC(15,2) DISABLE store1 -> enabled=False, count 26 -> 26 (frozen=True)
C. TC(15,1) ENABLE  store1 -> enabled=True,  count now 33 (grew=True)
D. TM(15,14) status: n_stores=4, 25 bytes; record BHHB (no name, no fill%, no overflow, no circular flag)
E. store4 count before clear: 10 ; TC(15,11) DELETE store4 -> count: 0
F. TC(15,9) DUMP store1: 33 packets queued; pending=33
   dump file written immediately at queue time: ['dump_sid01_20260728T233718Z.bin'] 3188 bytes
   after ticks: pending=0 store1 count=512 (auto-cleared on dump completion, then refilled by later ticks)
H. subtypes 3/5 -> 0 responses (no-op)
```

All four operations verified working end-to-end through `engine._dispatch_tc`.

### 4.3 Dump-to-disk behaviour

`engine.queue_dump()` (`engine.py:1593-1645`) does two independent things:
1. queues every packet for **paced downlink** at the live TTC TM rate (`_tick_dump_emission`,
   `engine.py:1647-1693`); packets released while `downlink_active` is False are **lost**, and the
   source store is **auto-cleared** when the queue empties (`engine.py:1688-1693`);
2. **immediately** writes the complete raw dump to
   `${SMO_DUMP_DIR:-mission.dump_dir:-workspace/dumps}/dump_sid<NN>_<ISO>.bin`
   (`engine.py:237-239`, `1628-1637`) as `uint32 length + bytes` records, *regardless* of RF
   coverage. Consumers: `tools/tm_report.py`, `tools/delayed_tm_viewer.py`. The MCS does **not**
   read this directory (its `/api/tm-dump` path re-reads its own SQLite archive,
   `server.py` `_handle_tm_dump`).

### 4.4 Gaps and fix

| Gap | Fix | Effort |
|---|---|---|
| No **Clear** button in the S15 panel (the only one of the four missing) | Add a 4th button per store card calling `s15Clear(id) → sendPusCmd(15,11,…)`, behind a `confirm()` (the catalog already marks it `criticality: caution`) | **S** |
| Dump has no progress/completion feedback; store silently empties | Surface the S1.5 progress the handler already returns (`generate_s1_progress(0, count)`, `service_dispatch.py:1302`) as a per-store progress bar; poll TC(15,13) while a dump is active | **S** |
| TM(15,14) record `BHHB` omits `name`, `fill_pct`, `overflow`, `circular`, `wrap_count` — `get_status()` computes them all (`tm_storage.py:196-215`) and throws them away | Extend the report record; UI already has the gauge (`updateStorageStatus()` `index.html:3883-3897`) and could show OVERFLOW / WRAP badges | **S** |
| Store definitions hard-coded in `tm_storage.py:33-38` | Move to `configs/eosat1/telemetry/tm_stores.yaml`, loaded like the OBCP/S12/S19 configs | **S** |
| **Should clear affect the disk archive?** Today: no. The archive is written at *queue* time and is the "delayed TM ground truth" for `tools/tm_report.py`. | Keep it that way — a `.bin` in `workspace/dumps` is the ground's copy of what was already down-linked, so an on-board clear must not delete it (deleting it would model the ground losing data it already had). Instead: (a) **do not** archive on 15,11; (b) do archive a `dump_sid<NN>_<ISO>.cleared` marker or a line in a manifest so the operator can see the store was purged without a dump; (c) add an explicit ground-side retention policy / "purge archive" button in the delayed-TM viewer, separate from the spacecraft command. | **S** |
| No S15 subtypes for "down-link a *time range*" or "report store catalogue" (3/5/6 are no-ops) | Optional; only if the syllabus needs partial dumps | **M** |

---

## Q5 — Does S12 work on board?

**Verdict: PARTIAL — it genuinely works, with four real gaps.**

### 5.1 Implementation

* Definitions loaded at boot: `engine.__init__` → `_load_monitoring_configs()` (`engine.py:323`,
  `475-489`) → `monitoring_loader.load_s12_definitions()` (`monitoring_loader.py:14-70`) →
  `register_s12_definitions()` (`monitoring_loader.py:138-159`).
* Evaluated **every tick**: `engine._tick_once` → `_tick_s12_monitoring()` (`engine.py:987`,
  body `engine.py:1470-1524`) → `ServiceDispatcher.check_monitoring()`
  (`service_dispatch.py:1117-1163`).
* Cold-equipment suppression: `_param_source_powered()` (`service_dispatch.py:1071-1115`).
* Edge-triggering via `engine._s12_violation_active` (`engine.py:278-280, 1486-1524`).
* Transitions are reported as **S5 events**, not S12 reports: enter → `event_id = 0x9000 | param`,
  clear → `0xA000 | mon_id` (`engine.py:1508-1522`).
* Implemented subtypes: **12,1** enable-all, **12,2** disable-all, **12,6** add definition,
  **12,7** delete definition, **12,12** report-all → **TM(12,13)**
  (`service_dispatch.py:1031-1069`).

### 5.2 Empirical (`/tmp/audit/q5_s12.py`, `/tmp/audit/q5c.py`)

Ground-added monitor on 0x0101 (battery SoC) with a deliberately violated band, driven over
real engine ticks:

```
SoC = 90.0
1. ENTER violation events: [('0x9101', 'param_0x0101:  | value=89.99 limits=[95.00, 100.00]')]
2. still-violating tick events (expect none): []
3. CLEAR events (0xA000): [('0xa101', 'S12 mon 257: parameter returned to nominal')]
4. TM(12,13) report, 376 bytes payload, n_defs=34
```

Boot-time config rules also fire unprompted on the first ticks — observed
`0x9103` "EPS Solar Array A Low Current", `0x9104`, `0x9408` "TCS FPA Temperature Low".
**S12 is genuinely evaluated each tick and is correctly edge-triggered.**

Ground management round trip also proved: `TC(12,6)` adds, `TC(12,7)` deletes, `TC(12,12)`
returns one TM(12,13), `TC(12,2)` disables (`check_monitoring()` then returns 0).

### 5.3 Gaps

1. **4 of 38 config rules are silently lost.** `load_s12_definitions()` deliberately keys rules
   `f"{param_id}_{rule_name}"` *"(to allow multiple rules per param)"* (`monitoring_loader.py:49-51`),
   but `register_s12_definitions()` re-keys them by bare `param_id`
   (`monitoring_loader.py:147`), so duplicates collide — **last one wins**. Measured:
   ```
   rules loaded by loader: 38 ; registered in dispatcher: 34 ; LOST: 4
   colliding param_ids: {0x101: 2, 0x105: 2, 0x102: 2, 0x408: 2}
     0x0101 survivor: 'EPS Battery SoC Critical' (low=10.0)  -- 'SoC Warning' (low=20.0) LOST
     0x0105 survivor: 'EPS Bus Voltage Critical' (low=25.0)  -- 'Bus Voltage Low' (27.0) LOST
     0x0102 survivor: 'EPS Battery Undertemp'   (high=50.0)  -- 'Battery Overtemp' (45.0) LOST
   ```
   Operationally: **there is no WARNING tier — only the ALARM tier survives on every parameter
   that has two rules.** The trainee never sees the early warning. Fix: make
   `_s12_definitions` keyed by a synthetic `mon_id` (the loader already computes a unique key)
   and carry `param_id` inside the record — `check_monitoring()` already reads `defn['param_id']`
   and returns a separate `mon_id`, so the change is ~5 lines in `monitoring_loader.py` plus the
   TC(12,6)/(12,7) handlers. **(S)**
2. **No TM(12,9)/(12,10) check-transition reports exist.** `grep 'service=12'` in the simulator
   returns exactly one hit — the 12,13 definition report. Yet
   `TMProcessor.process_packet` (`tm_processor.py:40-41`) and
   `MCSServer._handle_tm_packet` (`server.py:777-785`) both parse S12.9/10, and
   `handleTMPacket()` renders `pkt.monitoring` (`index.html:3213-3222`). **All of that ground
   code is dead.** S12 transitions arrive only as S5 events with a synthesised
   `0x9xxx`/`0xAxxx` id, which `MCSServer._event_id_to_subsystem` (`server.py:` alarm-journal
   helper) maps to `"unknown"` — so every S12 alarm is filed under "unknown" in the journal.
   Fix: emit real TM(12,9)/(12,10) from `_tick_s12_monitoring` **in addition to** the S5 event
   (keeps S19 triggering intact), and map `0x9xxx` → `param_id & 0x0F00` in the journal. **(S)**
3. **No per-definition enable/disable, and `check_type` (delta) is never used.** 12,1/12,2 are
   global; `dispatch(12, 3|4|5|8|15, …)` are all no-ops (verified: 0 responses).
   `defn['enabled']` and `defn['last_value']` are stored but nothing ever writes them; the
   `check_type` field is read at load and never consulted in `check_monitoring()` (verified).
   Fix: add 12,3/12,4 (enable/disable one definition by `mon_id`), and implement `check_type==1`
   as a delta check against `last_value`. **(S/M)**
4. **`MON_ADD_DEF` in the catalog is one byte short.** `tc_catalog.yaml:1142-1149` declares
   `param_id(u16) + low(f32) + high(f32)` = 10 bytes, but the handler reads
   `check_type = data[2]`, `low = data[3:7]`, `high = data[7:11]` and needs 11.
   Empirically, a catalog-shaped add gives
   `{'check_type': 63, 'low_limit': -8.97e-44, 'high_limit': 0.0}` — silent garbage.
   (The MCS UI's `s12Add()` `index.html:5220-5237` and `PUS_FIELD_DEFS['12.6']` insert the
   `check_type` byte correctly, so only the catalog is wrong.) **(S — one config line.)**
5. **UI:** `#s12-tbody` (`index.html:2231`) is never populated — TM(12,13) is not parsed
   anywhere, so the monitoring table is permanently empty. Same class as Q2. **(S)**
6. **Four parallel limit engines** now coexist: sim S12 (34 defs), sim FDIR thresholds
   (`engine._tick_fdir` `engine.py:1024-…` from subsystem `fdir.yaml`),
   MCS `configs/eosat1/mcs/limits.yaml` via `TMProcessor._check_limit`
   (`tm_processor.py:117-135`), and the browser's own colouring. Worth a consolidation note,
   not a defect. **(M, forward work.)**

---

## Q6 — Does S19 work on board?

**Verdict: PARTIAL — the engine evaluates and fires correctly; the management/report surface and
the shipped rule content are the problems.**

### 6.1 Implementation

* Rules loaded at boot from `configs/eosat1/monitoring/s19_rules.yaml` (26 rules) via
  `load_s19_rules` / `register_s19_rules` (`monitoring_loader.py:73-135, 162-178`),
  registered in `engine._load_monitoring_configs()` (`engine.py:475-489`).
* Fired from every emitted event: `engine._emit_event` → `self._dispatcher.trigger_event_action(event_id)`
  (`engine.py:1557`) → `ServiceDispatcher.trigger_event_action` (`service_dispatch.py`, after
  `_s19_dispatch`), which matches either an exact event id **or** a param-violation event
  (`(event_id & 0xF000) == 0x9000` vs a rule whose `event_type & 0xF000 == 0`).
* An action is exactly **one parameter-less S8 function**: `_s19_dispatch` →
  `self._handle_s8(1, bytes([func_id]))`, with `_OBC_CRITICAL_FUNCS = {52,53,55,56}` refused
  while the OBC is in the bootloader.
* Subtypes implemented: **19,1** add, **19,2** delete, **19,4** enable, **19,5** disable,
  **19,8** report → **TM(19,128)**.

### 6.2 Empirical (`/tmp/audit/q6*.py`)

```
A. rules registered: 26 enabled: 26
AUTONOMOUS: engine _emit_event(0x42) -> pa_on = True      # rule 9100: event 0x42 -> S8 func 66 (TTC PA ON)
AFTER TC(19,5) DISABLE: pa_on = False (should stay False) # disable honoured
E. 19,5 disable -> enabled? False ; 19,4 enable -> True ; 19,2 delete -> present? False
```

**The S19 engine works.** A rule installed from the ground fires on a genuine engine-emitted
event and changes spacecraft state; enable/disable/delete take effect.

### 6.3 Confirmed problems

1. **TC(19,8) "Report all definitions" returns *nothing* with the shipped rules.**
   The record is packed `struct.pack('>HBBB', ea_id, event_type, func, enabled)` — a **1-byte**
   `event_type`. Every shipped rule has a 2-byte event type (0x0102, 0x025B, …), so `struct.error`
   is raised, swallowed by the blanket `except Exception` in `dispatch()`
   (`service_dispatch.py:111-113`), and the handler returns `[]`.
   ```
   TC(19,8) with shipped rules                    -> responses: 0   <-- report unusable
   TC(19,8) with only 1-byte event_type rules     -> responses: 1  subtype 128
   ```
   **So rule status is not reportable at all in the as-shipped configuration.** Fix: pack
   `event_type` as `>H` (and keep the report subtype — see 3 below). **(S, ~2 lines.)**
2. **Ground-added rules cannot reference a real event.** TC(19,1) reads
   `event_type = data[2]`, a single byte (`service_dispatch.py` `_handle_s19` subtype 1).
   Verified: adding a rule for event 0x0102 stores `event_type = 0x02`. Every event id and every
   monitored param id in this mission is ≥ 0x0100, so **no ground-added rule can ever match a
   real event.** The MCS UI (`s19Add()` `index.html:5238-5246`, `PUS_FIELD_DEFS['19.1']`
   `index.html:3313`) faithfully sends a 1-byte field, so the UI is "correct" for a broken wire
   format. Fix: widen to `ea_id(u16) + event_type(u16) + func(u8) [+ arg_len(u8) + arg[]]` in
   handler, catalog and UI together. **(S)**
3. **TM(19,128) is not an ECSS subtype and nothing on the ground parses it.**
   `TMProcessor` has no S19 branch; `#s19-tbody` (`index.html:2260`) is never populated.
   ECSS-E-ST-70-41 uses 19,10/19,11 for the definition report. Fix alongside 1. **(S)**
4. **13 of 26 rules dispatch a func that contradicts their description** — confirmed unchanged;
   the full list is pinned in `KNOWN_MISROUTED` in
   `tests/test_content/test_s19_rules_content.py:86-100` and gated by the strict-xfail
   `test_no_rule_is_misrouted` (I re-ran the suite: `175 passed, 1 xfailed`). Examples:
   ea 1001 "payload off" → func 10 AOCS_SLEW_TO; ea 4002 "PA off" → func 54 OBDH select-bus;
   ea 5001-5004 (OBDH) all land on TCS heater circuits.
   Sub-issue (§4a): ea 2005-2008 "disable wheel 1/2/3/4" all send bare func 2 with no index byte,
   so all four disable wheel 0 — a direct consequence of the parameter-less action model.
5. **NEW finding — the *triggers* are as mis-targeted as the actions, and 17 rules are
   dual-triggered.** Because event ids and parameter ids share the 0x0000-0x0FFF space and
   `trigger_event_action` matches *both* namespaces, a rule fires on two unrelated conditions.
   Cross-check of all 26 rules against `event_catalog.yaml` and `s12_definitions.yaml`
   (`/tmp/audit/q6g.py`): **17 event_type values match an event-catalog id *and* an
   S12-monitored parameter**; 3 match no catalog id at all; and in several the catalog name flatly
   contradicts the rule's own description —
   `1001 0x0102` is `BATTERY_SOC_CRITICAL` (rule says "Bus Undervoltage"),
   `1002/1003 0x0100` is `EPS_MODE_CHANGE` (rule says "SoC Critical"),
   `2004 0x0202` is `RW_BEARING_DEGRADED` (rule says "Attitude Error High").
   Proved live:
   ```
   S19 dispatch after event 0x9102 (S12 violation of bat_temp):
        S19 triggered: rule 1001 executing S8 func 10 for param 0x0102 violation
   S19 dispatch after event 0x0102 (catalog BATTERY_SOC_CRITICAL):
        S19 triggered: rule 1001 executing S8 func 10 for event 0x0102
   ```
   i.e. rule 1001 fires on a **battery-temperature** S12 violation *and* on the SoC-critical
   event, while its description claims bus undervoltage. The existing content test only asserts
   "resolves to a catalog id **or** an S12 param", so it cannot see this. **Recommend extending
   `test_s19_rules_content.py` with a trigger-intent check and an ambiguity check, and
   disambiguating the namespaces** (e.g. rules declare `trigger: {kind: event|param, id: …}` and
   the wire format carries a 1-byte kind discriminator). **(S for the test, M for the format.)**
6. **No arguments, no sequences, no delays.** Mitigated since defect #48: S18/OBCP now provides
   sequencing, so the natural fix is to let an S19 action be *either* an S8 func (+ optional
   argument bytes) *or* `start OBCP <proc_id>` — which reuses `ObcpManager` and immediately fixes
   the wheel-index problem in 4. **(S/M)**

### 6.4 Ranked fix list

| # | Fix | Effort |
|---|---|---|
| 1 | Widen `event_type` to `uint16` in TC(19,1) and in the 19,8 report record; move the report to TM(19,10) | **S** |
| 2 | Parse the report in `TMProcessor`/`server.py` and populate `#s19-tbody` (rule id, trigger, action mnemonic, enabled, last-fired) | **S** |
| 3 | Add a trigger-kind discriminator so event ids and param ids stop colliding; extend the content test | **S/M** |
| 4 | Allow an argument byte-string per rule, and an `action: obcp <id>` variant | **S/M** |
| 5 | Flight-Ops review of the 13 mis-routed actions + the trigger drift found in 6.3.5 (both are content decisions, gated by the existing strict xfail) | **M** |

---

## Q7 — Procedures

**Verdict: PARTIAL.** The runner works end-to-end; the *content model* is fragmented into four
divergent representations, and the 64 shipped procedures are documentation only.

### 7.1 Does the MCS procedures tab actually run? — yes, proved

`/tmp/audit/q7_proc.py` wires the real `ProcedureRunner`
(`packages/smo-mcs/src/smo_mcs/procedure_runner.py`) to a real `SimulationEngine` via
`TCManager.build_command` → `engine._dispatch_tc`, with a background tick task and telemetry read
from `engine.get_state_summary()` (the same shape the MCS caches as `_latest_state`, cf.
`_proc_get_telemetry` `server.py:1448-1460`).

```
1. ACTIVITY 'imaging_pass' -> state: COMPLETED
   step 0 PAYLOAD_SET_MODE_IMAGING    PASSED
   step 1 Wait 5s                     PASSED
   step 2 Wait for payload.mode == 2  PASSED
   step 3 PAYLOAD_CAPTURE_IMAGE       PASSED
   TCs actually sent: [(8, 1, '1a02'), (8, 1, '1c')]
   payload mode now: 2
```

and a builder-authored, `type`-tagged procedure (`/tmp/audit/q7b.py`):

```
BUILDER procedure (valid param) -> COMPLETED
   0 'PLD IMAGING'                                              PASSED
   1 "{'type': 'wait', 'seconds': 1, 'description': 'Wait 1s'}"  PASSED
   2 "{'type': 'tlm_check', 'parameter': 'payload.mode', ...}"   PASSED
   3 "{'type': 'go_nogo', 'label': 'Proceed?', ...}"             PASSED
   4 'Conn test'                                                PASSED
   TCs: [(8, 1, '1a02'), (17, 1, '')]
```

Existing suites also green: `test_procedure_runner` + `test_procedure_builder` +
`test_content/test_procedures_content` → **343 passed, 1 xfailed** (the §4a Flight-Ops gate).
The func_id fixes from the integration trace have landed —
`configs/eosat1/planning/activity_types.yaml:17-33` now uses 0x1A/0x1C (payload) and
0x41 (TTC TM rate).

Two defects surfaced by the runs:
* **`_step_desc` has no branch for `type`-tagged steps** (`procedure_runner.py:461-470` handles
  `wait_s` / `wait_for` / `service` only), so every builder-authored wait / tlm_check / go_nogo
  step is displayed to the operator as a **raw Python dict** in the MCS step list. **(S)**
* **`tlm_check.parameter` is free text with no vocabulary.** My first run failed on
  `ttc.pa_on`, which simply is not in the sim's state summary
  (`ttc` exposes `ber, bit_sync, carrier_lock, data_rate_bps, doppler_hz, eb_n0_db, elevation_deg,
  frame_sync, gs_penalty_db, link_margin_dB, link_status, mode, modulation_mode, pa_temp_C,
  range_km, rssi_dbm, tx_power_W`). The operator has to guess dot-paths; a typo yields a
  30-second timeout and a failed procedure. **(S — populate the field from `/api/param-catalog`,
  which the UI already fetches at `index.html:2964`.)**

### 7.2 The "procedure builder(s)" — where they actually are

* There is **no procedure builder in the mission planner.** `packages/smo-planner/.../static/index.html`
  has only the schedule view and one button, `uploadToMCS()` (`index.html:901`, `1866-1911`),
  which POSTs `{activity_id, mode: 's11'|'immediate', step_by_step}` to
  `/api/schedule/upload` → `smo-planner/server.py:555-612`, forwarding either to MCS
  `/api/procedure/load` or to MCS `/api/schedule/upload-s11` (`server.py:1529-…`). Activity
  content lives entirely in `configs/eosat1/planning/activity_types.yaml`.
* The procedure builder is in the **MCS**: Procedures tab, `index.html:2396-2424`
  (`#pb-name`, `#pb-step-type`, `#pb-steps`), JS `pbAddStep/pbRender/pbSave/pbLoadCustom`
  `index.html:5681-5757`. It emits `type`-tagged steps and saves YAML to
  `configs/eosat1/procedures/custom/*.yaml` via `/api/procedure/save`
  (`server.py` `_handle_proc_save`).

### 7.3 The four representations

| # | Representation | Shape | Who executes it |
|---|---|---|---|
| 1 | `configs/eosat1/planning/activity_types.yaml` `command_sequence` (9 activities) | untagged: `{service, subtype, func_id, params[]}` \| `{wait_s}` \| `{wait_for:{parameter,value,timeout_s}}` | MCS `ProcedureRunner` (ground); also converted to S11 by `/api/schedule/upload-s11` |
| 2 | MCS Procedure Builder / `procedures/custom/*.yaml` | tagged: `{type: command\|wait\|tlm_check\|go_nogo, …}` with `seconds`, `condition`, `label` | same runner, via the defect-#14 translation branch (`procedure_runner.py:266-292`) |
| 3 | `configs/eosat1/procedures/**/*.md` (64 files) + `procedure_index.yaml` (64 entries) | free-form markdown with `**TC:** MNEMONIC … (Service N, Subtype M)` annotations | **nobody** — human-read only |
| 4 | `configs/eosat1/obcp/*.yaml` (3 procedures) | `{delay_s, service, subtype, func_id, data_hex}` | on board, `ObcpManager` (`smo_simulator/obcp.py`), ticked at `engine.py:976` |

**Relationship:** 1 and 2 share a *runner* but not a *schema* — the runner accepts two dialects.
4 is a genuinely different (on-board, relative-delay, no telemetry checks) model. 3 shares
nothing but the `procedure_ref` string (`activity_types.yaml` `procedure_ref: NOM-001` ↔
`procedure_index.yaml` `id: NOM-001`).

**A planner-built procedure *can* be run by the MCS runner** — that path works today (proved in
7.1, and `/api/procedure/load` accepts a raw `steps` array, `server.py` `_handle_proc_load`).
The reverse and the sideways paths do not:

* A **builder-saved custom procedure cannot be run from the UI.** `pbLoadCustom()`
  (`index.html:5738-5757`) loads it back into the *editor* only; `procLoad()`
  (`index.html:5514-5528`) only offers activity-type names from `/api/procedure/activity-types`.
  The backend would accept it (`_handle_proc_load` takes `steps`) — the button is simply absent.
* The **64 markdown procedures are unreachable from the MCS UI**: `grep 'api/procedures'` and
  `grep 'api/procedure/index'` in `static/` return **no hits**, so `/api/procedures`,
  `/api/procedures/{path}` and `/api/procedure/index` (`server.py:520-521, 543`) are dead
  endpoints from the browser's point of view (the index is used only server-side, by
  `_procedure_status_panel.load_procedure_index`, `server.py:309`, surfaced through
  `displays.js:261`).
* A procedure cannot be promoted to an **OBCP**: `/api/schedule/upload-s11` converts steps into
  time-tagged single TCs (skipping telemetry checks — see the planner's own alert text at
  `smo-planner/static/index.html:1902`), but nothing emits an S18 load blob, and there is no S18
  entry in the MCS command builder at all (see Q1.3).

### 7.4 Convergence proposal

1. **One canonical step schema** — adopt the tagged form (representation 2) as the single truth:
   `{type: command|wait|tlm_check|go_nogo|note, …}`, with `command` carrying either a catalog
   `mnemonic` + named `args` **or** raw `service/subtype/data_hex`. Add a JSON-Schema/pydantic
   model in `smo-common` (`smo_common/config/schemas.py` already hosts the config models) and a
   loader used by the runner, the planner and the OBCP encoder. Keep the untagged dialect
   accepted for one release, then migrate `activity_types.yaml`. **(M)**
2. **Make the shipped procedures machine-readable.** Give each `.md` a YAML front-matter or a
   sibling `.steps.yaml` generated from its `**TC:**` annotations (the harness in
   `tests/test_content/test_procedures_content.py` already parses those annotations and
   validates them against the catalog — reuse its parser). Then the Procedures tab can *browse
   the real ops procedures*, show the markdown alongside, and run the executable subset. This is
   the single biggest training-value win. **(M/L)**
3. **Wire the missing buttons (S):** "Run saved custom procedure" (POST the loaded `steps` to
   `/api/procedure/load`); a procedure source selector (activity type / custom / shipped);
   fix `_step_desc`; populate `tlm_check.parameter` from `/api/param-catalog`.
4. **One target, three execution modes.** From a single canonical procedure offer:
   *Ground-run* (ProcedureRunner, as today) / *Time-tagged* (`/api/schedule/upload-s11`, as today)
   / *On board* (compile the command+wait steps into an S18 load blob via
   `ObcpManager.encode_load_blob` and uplink TC(18,1)+TC(18,3); telemetry-check and go/no-go steps
   are dropped with an explicit warning, exactly as the S11 path already does). This makes the
   OBCP capability from defect #48 reachable without hand-writing YAML. **(M)**
5. **Expose S18 in the command builder** (Q1.3) so start/stop/status of pre-loaded OBCPs is
   actually clickable. **(S)**

---

## Cross-cutting themes

1. **"The TC goes up, the TM comes back, nobody reads it."** S11.18, S12.13, S19.128, TM(6,6) and
   the richer half of S15.14 are all generated on board and all discarded on the ground; the four
   PUS-tab tables (`s11-tbody`, `s12-tbody`, `s19-tbody`, plus `#mem-dump-result`) are never
   populated. One small, uniform fix — a `TMProcessor` branch per report + a render function —
   closes Q2, Q5.5, Q6.3.3 and half of Q3 at once. **(M for all four.)**
2. **The MCS UI duplicates `tc_catalog.yaml` in JavaScript** and the two have already drifted
   (no S18; correct S6/S12 layouts in the UI, wrong ones in the catalog). Driving the builder
   from `/api/tc-catalog` (already served, never called) removes a whole class of defect.
3. **Config/dispatcher wire-format drift** is now the dominant defect class: S6 `memory_id`,
   S12 `check_type`, S19 1-byte `event_type`. A round-trip test — "for every catalog entry, build
   the command from its declared fields, dispatch it, assert the handler saw the declared values"
   — would catch all three. It is the natural extension of `tests/test_acceptance/test_commands.py`.
   **(M, high value.)**

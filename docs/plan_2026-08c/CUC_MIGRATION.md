# CUC migration — Part A landed

Companion to `TIME_ARCHITECTURE.md` (the spec). This note records **what
actually changed in `smo-common`**, so the simulator / MCS / planner / XTCE /
SMDL / archive owners can update their side without re-deriving it.

Status: Part A complete. Parts B–F are open and owned by other agents.

---

## 1. The wire

### PUS TM secondary header — 7 → 9 octets

Before:

```
offset  size  field
 0       1    0x10 | pus_version
 1       1    service
 2       1    subtype
 3       4    CUC seconds, uint32 BE
             ── total 7 ──
```

After:

```
offset  size  field
 0       1    0x10 | pus_version
 1       1    service
 2       1    subtype
 3       4    CUC coarse, uint32 BE   seconds since the mission epoch (J2000)
 7       2    CUC fine,   uint16 BE   sub-second, units of 1/65536 s (~15.26 us)
             ── total 9 ──
```

Consequences on a whole TM packet:

| | before | after |
|---|---|---|
| primary header | octets 0–5 | octets 0–5 |
| secondary header | octets 6–12 | octets **6–14** |
| **data field starts at** | **13** | **15** |
| packet length | N | **N + 2** |
| `primary.data_length` | L | **L + 2** |

Anything that hardcodes `13` as the start of the data field, or `raw[6:13]` as
the secondary header, is now wrong. Use
`smo_common.protocol.ecss_packet.TM_SEC_HDR_LEN` (== 9) and take the data field
from `6 + TM_SEC_HDR_LEN`, or just use `decommutate_packet()`.

### TC secondary header — unchanged

Still 3 octets `[0x20, service, subtype]`, no time field.
`TC_SEC_HDR_LEN == 3`.

### Bodies

* **S5 event body — unchanged.** Still
  `event_id(u16) severity(u8) cuc(u32 whole seconds) aux_len(u8) aux`.
  Deliberate: it is a legacy field every parser reads at a fixed offset, and
  the secondary header now carries the authoritative full-precision time. The
  body CUC is the *coarse* value of the header CUC.
* **S9,2 time report body — CHANGED, 4 → 6 octets.** Now `coarse(u32) fine(u16)`,
  i.e. the same 6-octet CUC wire form as the header, so a time report is
  byte-identical to `CucTime.pack()`. Nothing in the repo parsed the old body,
  but a hand-written client would need updating.
* **S11 time-tag / S9,1 set-time TC bodies — untouched by Part A.** Still
  `exec_time(u32)` whole seconds. Extending these to coarse+fine is Part B/D
  work (see the checklist).

### Epoch

One epoch, J2000 = `2000-01-01T12:00:00` UTC, now **loaded from
`configs/<mission>/mission.yaml time_epoch`** (previously dead config that
duplicated a Python literal).

Direction of the dependency: **config pushes into protocol.**
`smo_common.config.loader.load_mission_config()` calls
`init_time_epoch(mission)` → `ecss_packet.set_time_epoch(...)`.
`ecss_packet` never imports the config layer — no import cycle, and packet
handling still works with no config tree on disk (falling back to
`TIME_EPOCH_DEFAULT`).

`tests/test_common/test_cuc_time.py::test_mission_yaml_epoch_matches_python_fallback`
gates that the YAML and the literal agree.

---

## 2. Symbols

### `smo_common.protocol.ecss_packet`

**Added**

| symbol | meaning |
|---|---|
| `CUC_FINE_TICKS = 65536` | fine-time ticks per second |
| `CUC_COARSE_MAX = 0xFFFFFFFF` | uint32 coarse wire range |
| `CUC_SIZE = 6` | packed CUC size |
| `TM_SEC_HDR_LEN = 9` | TM secondary header size |
| `TC_SEC_HDR_LEN = 3` | TC secondary header size |
| `TIME_EPOCH_DEFAULT` | the J2000 literal — **fallback only** |
| `class CucTime(coarse: int, fine: int)` | dataclass; `to_seconds()`, `from_seconds()`, `to_datetime()`, `from_datetime()`, `pack()`, `unpack()`, `as_tuple()`. `fine` is normalised into `[0, 65536)` with a carry into `coarse`. `coarse` may be negative in memory; `pack()` raises `ValueError` outside the uint32 range |
| `get_time_epoch()` / `set_time_epoch(dt_or_iso_or_None)` / `reset_time_epoch()` | the module-level settable epoch and its hook |
| `cuc_seconds_to_datetime(float) -> datetime` | full-precision CUC seconds → UTC |
| `datetime_to_cuc_coarse(dt) -> int` | **the legacy whole-seconds form**, thin wrapper |
| `datetime_to_cuc_seconds(dt) -> float` | full-precision seconds since epoch |
| `SecondaryHeader.cuc_coarse`, `.cuc_fine` | the two wire fields |
| `SecondaryHeader.cuc_seconds -> float` (get/set) | full-precision view |
| `SecondaryHeader.cuc -> CucTime` (get/set) | object view |
| `build_tm_packet(..., cuc_fine=0)` | new kwarg; `cuc_time=` also accepts a `CucTime` |

**Changed, breaking**

| symbol | before | after |
|---|---|---|
| `datetime_to_cuc(dt)` | `-> int` whole seconds | `-> tuple[int, int]` `(coarse, fine)`. A call site that was not updated gets a tuple and fails loudly (`TypeError` on arithmetic) rather than silently changing meaning. Use `datetime_to_cuc_coarse()` for the old behaviour |
| `SecondaryHeader.pack()` | 7 bytes | 9 bytes |
| `SecondaryHeader.unpack()` | needs ≥ 7 bytes | needs ≥ 9; raises `ValueError` on a 7-byte buffer |
| `decommutate_packet()` | TM data field from octet 13 | from octet 15 |

**Changed, compatible**

| symbol | note |
|---|---|
| `parse_cuc_time(coarse, fine=0)` | second parameter added with a default; the single-argument call is unchanged in signature *and* meaning |
| `SecondaryHeader.cuc_time` | was a field, is now a read/write property over `cuc_coarse`. Still accepted as a constructor kwarg (`SecondaryHeader(..., cuc_time=42)`). Whole seconds only — the setter does **not** touch `cuc_fine` |
| `TIME_EPOCH` | still present and still J2000, but it is now *rebound* by `set_time_epoch`. `from ... import TIME_EPOCH` at module scope captures the value at import time — prefer `get_time_epoch()` |

`datetime_to_cuc_coarse` floors; the old `int()` truncated toward zero. These
differ only for times *before* the epoch, which the uint32 coarse field cannot
carry anyway.

### `smo_common.telemetry.tm_builder`

**Added**

* `MissingTimeSourceError(RuntimeError)`.
* `TimeSource` / `TimeSourceResult` type aliases.
* `TMBuilder.set_time_source(cb)`.

**Time-source contract** (documented on `TMBuilder.__init__`):

> `time_source` is a zero-argument callable returning **either** full-precision
> seconds since the mission epoch as a `float`/`int` — fractional seconds are
> carried into the CUC fine field, not discarded — **or** a `CucTime`.
> It must return **on-board** time (the OBDH clock), never sim time, never wall
> clock.

**Changed, breaking**

* **The wall-clock fallback in `_get_cuc` is gone.** Building any packet with
  no `time_source` raises `MissingTimeSourceError`. `_get_cuc()` now returns a
  `CucTime` (was `int`) and its `params` argument is optional.
* `_pack_tm(service, subtype, data, cuc=None)`: `cuc` now defaults to **"read
  the clock"**, not to 0. It accepts seconds or a `CucTime`. This is what fixes
  spec Part C.4 — every `_pack_tm` call in `service_dispatch.py` that omits
  `cuc=` now gets on-board time automatically, including all four S1
  verification builders. **No simulator change is required for that**; it
  follows from the default.
* `build_time_report(cuc=None)`: `cuc` is now optional (defaults to the
  on-board clock), accepts seconds or a `CucTime`, and the body is 6 octets
  (see above).

### `smo_common.config`

* `loader.init_time_epoch(mission) -> datetime` — new, exported hook.
* `loader.load_mission_config()` now has the side effect of publishing the
  epoch.
* `schemas.MissionConfig.time_epoch` — same field, now documented as live.

### `smo_common.reporting` — `zero_cuc_policy` REMOVED

**Decision: removed, not kept.** Reasons:

1. `TMBuilder._pack_tm` no longer defaults `cuc` to 0 — it reads the clock or
   raises — so "packet with no on-board timestamp" can no longer be produced.
2. More important: after Part B, **CUC 0 becomes a legitimate time.** The OBC
   clock starts at J2000/CUC 0 on first boot and after every bootloader reset,
   so telemetry emitted before the first time sync genuinely carries small
   coarse values. A policy that reinterprets 0 as "unknown, place me by file
   order" would now actively *misplace* real post-reboot telemetry — the exact
   window an instructor wants the student to look at in NOM-008 / LEOP-006.

Removed symbols:

* `PeriodSpec.zero_cuc_policy` (report spec is `extra="forbid"`, so the key is
  now a validation error — see the checklist);
* `TelemetryDataset.from_dumps(..., zero_cuc_policy=)` parameter;
* `TelemetryDataset.zero_cuc_placed`, `.zero_cuc_excluded`;
* `TelemetryDataset._assign_times()`;
* the `time_estimated` flag on events / S12 transitions / verifications /
  store status and everywhere it was threaded through `analysis.py` and
  `render_html.py` (it existed only to mark policy-placed timestamps), plus
  the report's "N packets carry no on-board timestamp" provenance band and the
  `~ marks reconstructed timestamps` caption;
* `generate_report` meta key `zero_cuc_placed`.

Also in reporting: `periods.py` and `render_html.py` now convert with
`cuc_seconds_to_datetime` / `datetime_to_cuc_seconds`, so period selection and
plot axes keep sub-second resolution instead of truncating to whole seconds.
`dump_store.py` summaries now report `first_cuc`/`last_cuc` as **floats**, and
`_cuc_to_utc` treats only `None` (not `0`) as absent.

---

## 3. Files changed in this batch

```
packages/smo-common/src/smo_common/protocol/ecss_packet.py
packages/smo-common/src/smo_common/protocol/pus_services.py      (doc only)
packages/smo-common/src/smo_common/telemetry/tm_builder.py
packages/smo-common/src/smo_common/config/loader.py
packages/smo-common/src/smo_common/config/schemas.py             (doc only)
packages/smo-common/src/smo_common/dump_store.py
packages/smo-common/src/smo_common/reporting/{dataset,periods,spec,generate,analysis,render_html,decode}.py
configs/eosat1/mission.yaml                                      (comment only)
configs/eosat1/reports/{pass_report,daily_ops_review,anomaly_investigation}.yaml
        (deleted the now-invalid `zero_cuc_policy:` line — one line each;
         without this the three shipped templates fail spec validation)
tests/test_common/test_cuc_time.py                               (new)
tests/test_common/test_reporting.py
docs/plan_2026-08c/CUC_MIGRATION.md                              (this file)
```

---

## 4. Owner checklists

### Simulator (`packages/smo-simulator/`)

- [ ] `engine._get_cuc_time()` currently returns `int`. That still works
      (ints are accepted), but it truncates. Return **full-precision seconds
      (float)** or a `CucTime` from the OBDH clock — that is the only change
      needed for fine time to appear on the wire.
- [ ] `engine.py:238` and `obdh_basic.py:192` do
      `from smo_common.protocol.ecss_packet import TIME_EPOCH` inside a
      function; that still resolves correctly after `set_time_epoch`, but
      prefer `get_time_epoch()`.
- [ ] Ensure `load_mission_config()` is called before the first packet is
      built, so the YAML epoch is in force (it already is, via the config
      load at engine construction — just don't bypass it).
- [ ] No change needed for S1 `cuc = 0` (spec Part C.4): the `_pack_tm`
      default now reads the clock. **Do not** re-add explicit `cuc=0`.
- [ ] `TMBuilder` will now **raise** instead of stamping wall clock. Anywhere
      a builder is constructed without a time source is a bug to fix, not to
      work around.
- [ ] Part B: when `obc_time_s` becomes a float, feed it straight through —
      no `int()`.
- [ ] Known failing tests to fix (header offset / missing time source), both
      test-side only:
      - `tests/test_simulator/test_dispatch_defects_bcd.py:92-95` — hardcodes
        `pkt[13:-2]`; must be `pkt[15:-2]` (or use `decommutate_packet`).
      - `tests/test_simulator/test_command_rejection_reason.py:201` —
        `TMBuilder(apid=1)` with no `time_source`.
      Everything else in `tests/test_simulator` passes (1294 passed, 2 failed).

### MCS (`packages/smo-mcs/`)

- [ ] `server.py:1420` reads `pkt.data_field[3:7]` — that is the **S5 body**
      CUC, which is unchanged. No action, but confirm it is not confused with
      the header.
- [ ] `tm_processor.py:84` — same, S5 body. Unchanged.
- [ ] Any place that slices a TM at octet 13 for the data field must move to
      15 / `6 + TM_SEC_HDR_LEN`.
- [ ] `server.py:2801` `_utc_to_obc_cuc` and `server.py:3404` — switch to
      `get_time_epoch()`/`datetime_to_cuc_coarse`, and note that
      `datetime_to_cuc` now returns a tuple.
- [ ] Surface the fine field: `pkt.secondary.cuc_seconds` gives the
      full-precision timestamp for archive rows, staleness and correlation
      (Part D). `cuc_time` still works and is whole seconds.
- [ ] Part D.6: the `> 1e9` heuristic in the `0x0309` decode still needs
      removing — untouched here.
- [ ] `static/index.html` — `s9SyncTime` / `s11ResolveTimeTag` still encode
      Unix seconds (Part D.7); still to fix. The S9,1 and S11,4 TC bodies are
      **not** widened by Part A, so those stay u32 whole seconds for now.
- [ ] Known failing tests to fix, both test-side only:
      - `tests/test_mcs/test_dump_api.py:247` — `TMBuilder(apid=100)` with no
        `time_source`.
      - `tests/test_mcs/test_report_api.py:27` — `datetime_to_cuc(...)` now
        returns a tuple (`T0_CUC = datetime_to_cuc(...)` then `+ i`); use
        `datetime_to_cuc_seconds`.
      Everything else in `tests/test_mcs` passes (1165 passed, 1 failed, 11
      errors, all from those two files).

### Planner (`packages/smo-planner/`)

- [ ] No direct dependency on the header layout was found. If you convert an
      activity start time to CUC, `datetime_to_cuc` now returns
      `(coarse, fine)` — use `datetime_to_cuc_coarse` for a u32 field, or
      `datetime_to_cuc_seconds` for arithmetic.
- [ ] Part E.4 still stands: route the sim-time → onboard-CUC conversion
      through the MCS correlation, not through a naive `datetime_to_cuc`.
- [ ] `tests/test_integration/test_planner_mcs_s11_chain.py` passes as-is.

### XTCE (`tools/generate_xtce.py`)

- [ ] `_build_pus_system()` declares `("CUCTime", 32)` and lists `"CUCTime"`
      in the parameter set and the `PUSPacket` entry list (lines ~169, 178,
      190). Split into `CUCCoarse` (32 bits) and `CUCFine` (16 bits) and add
      both to the entry list **in that order**, or every downstream parameter
      offset in the generated XTCE is 2 octets short.

### SMDL (`tools/generate_smdl.py`)

- [ ] No secondary-header model found in the generator (it emits parameter
      types only). Confirm; if a packet layout is emitted anywhere, add the
      16-bit fine field after the 32-bit coarse.

### Archive / dump tooling

- [ ] `tools/tm_report.py:171` uses `pkt.secondary.cuc_time` — still valid,
      but now whole seconds only; switch to `cuc_seconds` to keep sub-second
      resolution, and `parse_cuc_time(int(cuc))` at line 255 →
      `cuc_seconds_to_datetime(cuc)`.
- [ ] `tools/delayed_tm_viewer.py:670` — the comment "It is NOT coarse/fine
      packed" is now false; the summary's `first_cuc`/`last_cuc` are floats.
      `formatCucTime` already handles floats; fix the comment and consider
      showing sub-seconds.
- [ ] MCS TM archive (Part D.4): rows will now be able to carry a
      full-precision on-board CUC alongside sim time. Add the schema version /
      column note there as the spec requires.
- [ ] Old dump files written with the 7-octet header **cannot be read** by the
      new decoder — the data field will be misaligned by 2 octets and the CRC
      will fail. There is no version flag in the packet to detect this. Treat
      pre-migration dumps as unreadable; delete `workspace/dumps` content from
      before this change rather than trying to auto-detect.

### Documentation / courseware

- [ ] `configs/eosat1/manual/12_time_and_clocks.md` — rewrite (spec Part F);
      it must state the 9-octet header, the fine-time resolution
      (1/65536 s ≈ 15.26 us) and that `mission.yaml time_epoch` is live.
- [ ] `configs/eosat1/manual/software_architecture.md` mentions the CUC field —
      check the byte count.
- [ ] `docs/design_document.html` / `docs/EOSAT1_SMO_Design_Document_Web.html` /
      `docs/generate_design_pdf.py` describe the 7-byte secondary header.
- [ ] `docs/plan_2026-08/briefs/06_report_generator_and_config_editor.md`
      documents `zero_cuc_policy` in three places — mark as superseded.
- [ ] `SpaceCDF/docs/course/` time-correlation material — logged as out of
      scope for this batch by the spec.

---

## 5. Tests

New: `tests/test_common/test_cuc_time.py` (38 tests) — CUC round trips
including fine precision, the 9-octet header layout and the moved data-field
offset, fine wrap at 65536, negative/pre-J2000 times, the full uint32 coarse
range, epoch agreement between `mission.yaml` and the Python fallback, the
epoch actually driving conversions, raise-on-missing-time-source, both
time-source return shapes, and S1 packets carrying on-board time.

```
cd /root/smo && .venv/bin/python -m pytest tests/test_common -q
# 191 passed
```

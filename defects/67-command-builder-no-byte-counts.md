# Defect 67 — The command builder never read the TC catalogue: no expected byte counts anywhere, and 12 of 91 S8 functions unreachable

## Summary

`configs/eosat1/commands/tc_catalog.yaml` carries a fully typed `fields` list
for essentially every command, from which the expected data-field length is
arithmetic:

```
expected_data_bytes = sum(width(f.type) for f in cmd.fields)
                    + (1 if (service, subtype) == (8, 1) else 0)   # the func_id byte
```

The `+1` is required because `func_id` is a *sibling key*, not a field, and the
simulator's dispatcher strips it before routing (`_route_*(func_id, data[1:])`).
At the time of the audit this was derivable for **182 of 188** commands.

The builder used none of it:

- `PUS_FIELD_DEFS` — a hand-maintained 42-entry map in `index.html`. It knew a
  `size:` per field and so *could* have summed them; nothing did.
- `FUNC_CATALOG` — a hardcoded list of **79** S8 functions. The catalogue
  defines **91**. Missing from the dropdown, i.e. **not selectable by any
  operator from the structured builder**: 42 `HEATER_THRUSTER`,
  81 `EPS_DEPLOY_WING`, 82 `EPS_WING_STATUS`, 84 `AOCS_GPS_SET_START_MODE`, and
  the eight legacy quick-actions 100–107.
- The S8.1 argument was a bare `hex_bytes` free-text box with placeholder
  `"07=line7, 00=wheel0"`. **The builder had no idea how many argument bytes any
  function needs.**
- `GET /api/tc-catalog` has existed all along and already returned `fields` —
  and `grep -c "tc-catalog" index.html` was **0**. No client had ever called it.

## Severity

**Major (training and operational).** A short S8 command is silently swallowed
by the handler's `if len(data) >= n:` guard while S1 acceptance/completion still
report success — a false-positive command, and the console gave the operator no
way to notice before sending.

## Status

**Fixed.** Server-derived, catalogue-driven.

1. **New module `smo_mcs/tc_lengths.py`** — the width table, `field_width()`
   and `expected_data_bytes(cmd)`, which returns
   `{expected_data_bytes, variable_length, min_data_bytes, length_note}`.
   Python, one place, unit-tested.
2. **`/api/tc-catalog` annotates every command** with those four keys. This
   also serves the planner and any future tooling.
3. **Non-derivable commands never show a number.** Two families:
   - **Unbounded (`bytes`-typed field)** — reported as
     `variable_length: true`, `expected_data_bytes: null`, with
     `min_data_bytes` = the fixed prefix and a note
     `"variable — 5 fixed byte(s) + payload"`.
   - **No `fields` key at all** (`TTC_SWITCH_PRIMARY`, `TTC_SWITCH_REDUNDANT`)
     — reported as *unknown*, not zero, with the note
     `"catalogue entry has no 'fields' key — length unknown; at least the 1
     func_id byte"`. Note that `TCCommandDef` defaults `fields` to `[]`, which
     erases the distinction; the server recovers it by inspecting the raw YAML
     (`_tc_names_without_fields`) so a schema default cannot masquerade as a
     declared zero-length command.
4. **The hardcoded `FUNC_CATALOG` is deleted.** `loadTcCatalog()` fetches
   `/api/tc-catalog?position=…` on builder init and generates the S8 function
   list from it — all 91 func_ids, permanently in sync with the YAML, with the
   position-access `allowed` flag surfaced so a locked-out operator sees why.
5. **Three places surface the number in the UI:**
   - a live chip beside the data box —
     `EXPECTED 2 B · ENTERED 2 B ✓` (green), amber on mismatch,
     `VARIABLE · 5 fixed B + payload` grey, `LENGTH NOT DECLARED · …` grey;
   - every function-dropdown option is suffixed with its argument size:
     `28 - Pld Capture Image (4 B)`, `63 - Ttc Switch Primary (length undeclared)`;
   - a **soft** pre-send `confirm()` in `sendPUSCommand()`
     ("S8.1 func 8 expects 5 data bytes, you entered 3. Send anyway?") —
     deliberately soft, because operators hand-craft malformed TCs during
     fault-injection training.

## Acceptance criteria

- Over the live catalogue, >95 % of commands derive a number, and the ones that
  do not are **exactly** those with a `bytes`-typed field or no `fields` key
  (asserted by shape, not by a frozen list of names — the catalogue is
  maintained independently of this code).
- Hand-checked lengths: `S2_EPS_BATTERY_HEATER` 3, `HK_ENABLE` 2,
  `HK_SET_INTERVAL` 6, `SET_TIME` 4, `DELETE_STORE` 1, `STORE_STATUS` 0
  (a non-S8 command with `fields: []` is zero, *not* one).
- Every S8.1 entry's derived length equals its declared field sum + 1.
- The endpoint serves every `func_id` in the YAML, including all twelve that
  were previously unreachable.

## Affected areas

- `packages/smo-mcs/src/smo_mcs/tc_lengths.py` (new)
- `packages/smo-mcs/src/smo_mcs/server.py` — `_handle_tc_catalog` annotation,
  `_tc_names_without_fields`
- `packages/smo-mcs/src/smo_mcs/static/index.html` — `loadTcCatalog`,
  `FUNC_CATALOG` (now generated), `CMD_LENGTH_BY_KEY`, `lengthInfoOf`,
  `expectedBytesText`, `currentCmdLengthInfo`, `enteredDataBytes`,
  `updateCmdBytesChip`, the `#cmd-bytes-chip` element and CSS, the pre-send
  check in `sendPUSCommand`
- Tests: `tests/test_mcs/test_tc_lengths.py`,
  `tests/test_mcs/test_hk_structures_api.py::TestTcCatalogLengths`,
  `tests/test_mcs/test_mcs_page_behaviour.py::TestCommandBuilderByteCounts`

## Hand-off — catalogue data, not this lane

`configs/eosat1/commands/tc_catalog.yaml` is owned elsewhere and was **not**
edited here. One verified catalogue-vs-handler mismatch remains:

> **`RW_SET_SPEED_BIAS` (S8.1, func 8)** declares
> `wheel_idx uint8 + bias_rpm int16` = 3 data bytes after the func_id byte
> (4 including it). The handler requires **5** after the func_id byte:
> ```python
> # service_dispatch.py
> elif func_id == 8:                       # RW set speed bias
>     if len(data) >= 5:
>         wheel = data[0]
>         bias  = struct.unpack('>f', data[1:5])[0]   # float32, not int16
> ```
> A correctly-sized-per-catalogue command is silently swallowed while S1
> acceptance/completion still report success. Compare `AOCS_RW_RAMP_DOWN`
> (func 14), which correctly declares `uint8 + float32`.
> **Requested change: `bias_rpm` `int16` → `float32`.**

Also noted for whoever owns the catalogue and the dispatcher:

- `func 83` (EPS mode) is dispatched by `service_dispatch.py` but has **no**
  `tc_catalog.yaml` entry (defect #27).
- `TTC_SWITCH_PRIMARY` / `TTC_SWITCH_REDUNDANT` would be fully derivable if
  they carried an explicit `fields: []`; the handlers take no argument.
- A content test asserting, per `func_id`, that the catalogue's derived length
  matches the minimum length the dispatcher checks would have caught the
  `RW_SET_SPEED_BIAS` defect. `smo_mcs.tc_lengths.expected_data_bytes` is the
  half of that test that now exists.

## Forward work (not in this change)

Typed per-argument inputs for S8 generated from the catalogue's `fields`
(e.g. func 10 `AOCS_SLEW_TO` → four float32 quaternion boxes plus a rate box)
instead of the raw hex field. The data needed is already in the fetched
catalogue.

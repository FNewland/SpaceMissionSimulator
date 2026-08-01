# 89 — every quick command was length-checked against the wrong command

**Status:** Fixed

## Trigger

Franz, operating the console:

> "When I send a quick command with data bytes, I get a data byte size error
> (it thinks they all have 0 bytes) which I have to accept to send the
> command."

Both halves of that sentence are separate bugs in defect #67's byte-count
feature, and both were reproduced in a real browser against the running MCS.

## Reproduction

Chromium, `switchTab('commanding')`, click all 64 buttons in the QUICK
COMMANDS panel on a freshly loaded page:

```
WARN 'Desaturate'    '01' || AOCS_SET_MODE expects 2 data bytes; you entered 1.
WARN 'PA ON'         '42' || AOCS_SET_MODE expects 2 data bytes; you entered 1.
WARN 'OBC Reboot'    '34' || AOCS_SET_MODE expects 2 data bytes; you entered 1.
…
total warned: 10 / 64
```

Every warning names **`AOCS_SET_MODE`** — which is none of the commands being
sent. And the EXPECTED/ENTERED chip beside the builder read

```
EXPECTED 2 B · ENTERED 0 B ⚠
```

before *and after* clicking "Payload On" with `13 03` in the data box. That
chip is Franz's "it thinks they all have 0 bytes".

Move the builder's S8.1 function dropdown to any one-byte function — an
entirely ordinary thing to do — and re-run:

```
with func dropdown on 66 (TTC_PA_ON, 1 B): warned 45 / 64
```

The number of false warnings is a function of a dropdown the operator was not
using.

## Root cause

Two independent bugs, one per half of the report.

### 1. The length check read ambient DOM state, not the command being sent

`sendPUSCommand()` is the send path for **both** the structured command builder
*and* the quick-command buttons. Its pre-send check called

```js
const lenInfo = currentCmdLengthInfo();          // no arguments
```

and `currentCmdLengthInfo()` resolves the S8.1 func_id from
`document.getElementById('sf-func_id')` — the **structured builder's function
dropdown**. `initQuickCmds()` sets `cmd-service` / `cmd-subtype` / `cmd-data` /
`cmd-name` and calls `sendPUSCommand()` with no arguments; it never touches
that dropdown, and assigning `.value` does not fire `change`, so the dropdown
keeps whatever it was showing. On a freshly loaded page that is the first
entry, func 0 `AOCS_SET_MODE`, expected 2 bytes — hence "expects 2" against
every one-byte quick command.

The hex-first-byte fallback in `currentCmdLengthInfo()` was only reached when
the dropdown was **absent**, which is why the warnings vanished entirely when
the builder happened to be on a non-S8 service — the correct answer was
already in the code, behind the wrong precedence.

### 2. The chip was never told the data box had changed

`updateCmdBytesChip()` is driven by the data box's `input` event.
`document.getElementById('cmd-data').value = btn.dataset.d` is a programmatic
assignment, which fires nothing. The chip therefore kept the state it had at
page load — `ENTERED 0 B` — for the whole session.

### Not the cause

The catalogue is fine. All 64 quick commands resolve to a `tc_catalog.yaml`
entry and all 64 already sent exactly the declared number of bytes; the
defect-#80 gap in the S8 func-management entries does not touch the
quick-action set.

## Fix

The length check is now a **pure function of what goes on the wire**:

```js
function wireFuncId(service, subtype, dataHex)          // data[0] IS the func_id
function lengthInfoForWire(service, subtype, dataHex, name)
```

`wireFuncId` reads the first octet of the data field, which is exactly how
`service_dispatch._route_*(data[0], data[1:])` reads it, and handles both the
`"41 00"` and `"4100"` spellings. `sendPUSCommand()` calls
`lengthInfoForWire(service, subtype, dataHex, name)` — the three values it is
about to transmit — so no panel's DOM can influence another panel's check.

`name`, when it is a real catalogue name consistent with the wire, resolves the
service.subtype collisions (`S6.2` is `MEM_LOAD` *and* four torque-gain
writes). That needed a second index, `CMD_LENGTH_BY_NAME`: `CMD_LENGTH_BY_KEY`
is keyed by `service.subtype[.func_id]`, so five S6.2 commands collapsed onto
one key and the last one loaded won.

`currentCmdLengthInfo()` (the chip, in the builder) prefers the wire form too
and falls back to the function dropdown only while the data box is empty — i.e.
while the operator is still composing and has not pressed PACK TO HEX.

`initQuickCmds()` now refreshes the chip explicitly and passes the quick
command's own identity to the send path.

## Verification

Re-run in Chromium against the live MCS, clicking **every** button in the
panel:

| Case | Before | After |
|---|---|---|
| all 64 quick commands, fresh page | 10 warned | **0 warned** |
| all 64 quick commands, dropdown on func 66 | 45 warned | **0 warned** |
| chip after clicking "Payload On" (`13 03`) | `EXPECTED 2 B · ENTERED 0 B ⚠` | `EXPECTED 2 B · ENTERED 2 B ✓` |
| `S8.1 '00'` (one byte of a two-byte command) | warns | **still warns** |
| `S6.2` `MEM_LOAD`, 7 bytes (variable length) | warned, against `AOCS_SET_TORQUE_GAINS_ALL` | **no warning** |
| `S6.2` `AOCS_SET_TORQUE_GAINS_ALL`, 6 of 16 bytes | warns | **still warns** |
| `S8.1 '63'` `TTC_SWITCH_PRIMARY` (length undeclared) | — | **no warning**, chip says LENGTH NOT DECLARED |

No quick command is variable-length or undeclared, so none of them shows
"variable"; the two families are covered by the `MEM_LOAD` and
`TTC_SWITCH_PRIMARY` cases above, neither of which can now produce a spurious
"0 bytes expected".

## Tests

`tests/test_mcs/test_quick_commands.py` (190 tests).

The table is generated from the page — the 64 `.quick-cmd` buttons are parsed
out of `index.html`, so a button added tomorrow is tested tomorrow — and the
expected length is derived from the dispatcher's own source rather than
hardcoded:

* `_min_acting_length()` walks `service_dispatch.py` with `ast` and computes,
  per `func_id`, the **shortest data field for which the handler does anything
  at all**. That is precisely the defect-#80 failure mode: a command below that
  threshold reaches no handler call and is silently discarded while S1.1/S1.7
  report success. Handlers that accept several forms (func 65 takes a 4-byte
  raw rate *or* a 1-byte tier code *or* nothing) correctly report the shortest.
* `_octets_consumed()` computes the **most** octets a handler can use.

Three tests per quick command (it is in the catalogue; it sends the catalogued
number of bytes; it satisfies its handler), plus two sweeps over the whole
catalogue, plus browser tests through the Node harness: the func_id comes from
the data field and not the dropdown even with a stale dropdown; a correct quick
command raises **no** `confirm()`; a genuinely wrong length still does;
variable and undeclared commands never report a number; and source-level guards
that `initQuickCmds` refreshes the chip and that `sendPUSCommand` no longer
calls `currentCmdLengthInfo()`.

## Hand-off — two catalogue/handler divergences found by the sweep

Neither is a quick command and neither is in this lane, so both are pinned in
`KNOWN_CATALOGUE_HANDLER_DIVERGENCES` with the rationale in the test file. A
fourth entry fails the suite.

* **`PAYLOAD_CAPTURE` (S8.1 func 28)** declares `scene_id uint16 + lines uint16`
  = 4 argument bytes. The handler reads `lat float32 + lon float32` = 8, and
  the payload model assigns `scene_id` itself (`self._next_scene_id += 1`) and
  never reads `lines`. A command built to the catalogue captures at (0, 0) and
  reports success. `configs/eosat1/manual/commissioning_sequence_walkthrough.md`
  already documents `PAYLOAD_CAPTURE(lat, lon)`, while
  `docs/PUS_SERVICE_REFERENCE.md` and the payload-ops role analysis document
  `scene_id, lines`. **The catalogue and one of the two documents are wrong;
  which is a mission-data decision.**
* **`TTC_SET_TX_POWER` (S8.1 func 68)** is documented as a one-byte level
  (`0=1 W, 1=5 W, 2=8 W`) by the catalogue, `PUS_SERVICE_REFERENCE.md`, the TTC
  role analysis **and three FDIR procedures** (`ttc_reduce_power.yaml`,
  `ttc_antenna_switch.yaml`, `load_shed_stage3.yaml`). The handler reads
  `float32` **watts** and falls back to `2.0` for anything shorter, so every one
  of those callers is silently ignored — including the autonomous FDIR power
  reduction, which has been setting 2 W by accident rather than by design.
  **The handler is the outlier, but the documented 8 W high level exceeds the
  model's `pa_max_power_w` of 5.0 W**, so the watt values have to be settled
  before the handler can be corrected.

`TTC_SET_DATA_RATE` (func 65) is in the same pinned set and is **not** a
defect: its 4-byte raw-bps form is a deliberate legacy compatibility path
(defect #85) and both forms act. "Accepts a longer legacy form" and "wants more
than is declared" are not mechanically distinguishable in the source, which is
why that set is pinned rather than derived.

## Files

* `packages/smo-mcs/src/smo_mcs/static/index.html` — `wireFuncId`,
  `lengthInfoForWire`, `CMD_LENGTH_BY_NAME`, `currentCmdLengthInfo`,
  `sendPUSCommand`, `initQuickCmds`
* `tests/test_mcs/test_quick_commands.py` (new)

## Related

* **#67** introduced the byte-count feature, the chip and the soft pre-send
  confirm. It was only ever exercised through the structured builder, where the
  ambient lookup happens to be right.
* **#80** is the class of bug the new sweep generalises: `RW_SET_SPEED_BIAS`
  declared 3 argument bytes against a handler guarded by `if len(data) >= 5:`.
  That command now passes the sweep; two others do not (above).

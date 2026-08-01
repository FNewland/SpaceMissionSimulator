# 88 — error codes were not visible in any display an operator watches

**Status:** Fixed

## Trigger

Franz, operating the console:

> "How can I visualize the error codes? They are not shown in the telemetry
> window or the error window or the FDIR window."

He had not yet seen defect #87, which had landed minutes earlier — and that is
the point. #87 carried the failure code and the on-board reason to the ground
and rendered them in **the verification log and the command-history row**: the
two surfaces an operator looks at *while sending a command*. The three surfaces
Franz names are the ones he watches *the rest of the time*, and none of them
had been reached.

## What each surface actually showed

| Surface Franz means | What it is | What it showed about a failure code |
|---|---|---|
| "the telemetry window" | the live parameter panes (OBDH / TTC tabs) | **nothing.** No parameter carried a failure code, and the TC acceptance counters that *were* downlinked (0x0304/5/6) were not rendered anywhere either |
| "the error window" | the EVENTS pane in the bottom panel | `EVT-779 (RID 0x030B): S8.1 func 65 rejected at execution, err 0x0007: …` — the id in **decimal**, no event **name**, and the code as a bare number inside a sentence with no meaning attached |
| "the FDIR window" | the 13th tab's ALARM / ANOMALY JOURNAL (defect #76) | the rejection was filed as one more anonymous `S5 EVENT` row among the S12 transitions, with no code, no meaning and no filter |

### The parameter gap was the real one

`ServiceDispatcher._last_error_code` (`service_dispatch.py:41`) has always held
the code of the most recent refusal. It was read by exactly one line of
`engine._dispatch_tc` to stamp the S1.8 report and was then discarded. It was
**not a telemetry parameter**, so:

* it was not in `telemetry/parameters.yaml`;
* it was in no HK structure, so it was never downlinked;
* `obdh.tc_rej_count` (0x0306) told an operator *how many* commands had been
  refused and there was no way, from telemetry alone, to learn *why any of
  them* had been.

An operator who missed the verification row — because they were on another tab,
or the row had scrolled, or the command was sent by a procedure — had no way
back to the code at all.

## Fix

Three existing surfaces extended. No fourth display was added.

### 1. One vocabulary, served from one place

`GET /api/failure-codes` serves `FAILURE_CODES` and `MEMORY_FAILURE_CODES`
straight out of `smo_common/protocol/pus_services.py` — the same module the
spacecraft's dispatcher emits from. The browser holds `FAILURE_CODE_TEXT`,
which starts **empty** and is filled from that endpoint; `failureCodeLabel()`
is the single renderer (`0x0007 — Not permitted in the current state`) used by
the verification row, the event log, the FDIR journal and the OBDH pane. A test
fails if a JS literal ever maps a numeric code to text again.

The COMMAND_REJECTED auxiliary text has the same problem in miniature — it is
written by the spacecraft and read by the ground — so its format moved into
`pus_services` as `format_command_rejection()` / `parse_command_rejection()`.
`engine._emit_command_rejected` writes with the former, `smo_mcs.server` reads
with the latter, and content tests assert neither side carries its own copy.

### 2. The event / error surface

The S5 decode now attaches the catalogue **name** to every event and, for
`0x030B`, breaks the auxiliary text into `identity / stage / error_code /
error_text / reason`. The log line went from

```
EVT-779 (RID 0x030B): S8.1 func 65 rejected at execution, err 0x0007: TM rate is forced…
```

to

```
REJECT | 0x030B COMMAND_REJECTED: S8.1 func 65 refused at execution ·
        0x0007 — Not permitted in the current state · TM rate is forced to the
        beacon tier in beacon mode (LOW refused)
```

A rejection is filed at **warning** severity whatever the catalogue rates the
event, because a refused command is an error the operator has to act on.

`routeEventToSubsystem()` used to recover the event id by regexing `EVT-(\d+):`
out of the rendered line; it now takes the id as an argument, so the subsystem
event panes kept working across the rename.

### 3. The FDIR window's journal

New journal kind **`cmd_rej`**, alongside `s12_in` / `s12_out` / `rule` /
`mode` / `event`, with its own `TC REJECT` label, its own colour and its own
filter checkbox. `FDIRDisplayModel.ingest_event()` promotes an event to this
kind when the server hands it a decoded rejection, and the row carries the code,
its meaning, the stage and the reason:

```
17:41:48  TC REJECT  COMMAND_REJECTED (0x030B) — S8.1 func 65 refused at
                     execution · 0x0007 — Not permitted in the current state ·
                     TM rate is forced to the beacon tier in beacon mode (LOW refused)
```

### 4. The telemetry window

New parameter **`0x0315 obdh.last_tc_error_code`** — the S1 failure code of the
most recently refused telecommand, 0 = none since boot, cleared by
`OBDHBasicModel._reboot()`. It is latched by
`engine._emit_command_rejected`, which is the single choke point every refusal
already passed through (acceptance *and* execution), so no gate has to remember
to write it. Added to `parameters.yaml` and to **HK SID 4 (Platform)** as
`pack_format: H, scale: 1`, next to the `tc_rej_count` it explains — so it
arrives on the 8 s cadence without being asked for.

The OBDH tab gained a **TELECOMMAND ACCEPTANCE** panel:

```
TC Received      6
TC Accepted      4
TC Rejected      2
Last TC Failure  0x0007 — Not permitted in the current state
```

The first three are 0x0304/0x0305/0x0306 — already downlinked, never displayed.
The fourth is decoded through the served vocabulary, and reads
`none since boot` in green rather than a bare `0`.

## The reverse direction — codes the ground could not see

The question "which codes does the spacecraft report that the ground cannot
see?" turned up one substantial answer and one residue.

### Fixed here: every S1.4 execution-start failure

`ServiceDispatcher.generate_s1_exec_fail()` is used at **16 sites** — every S6
memory refusal (illegal address, write-protected, over the transfer limit,
malformed) plus the S3 / S12 / S19 argument gates. It builds and returns the
S1.4 packet itself and, unlike the S1.8 path, it did **not** set
`_last_error`. So `engine._dispatch_tc` fell through to its `else` branch and
sent an **S1.7 "execution complete"** immediately afterwards, on the real
sequence number.

The result: a memory command refused for an illegal address produced a green
`COMPLETED` verification row, no event, no journal entry, and the entire
`MEMORY_FAILURE_CODES` vocabulary was unreachable in practice.

`generate_s1_exec_fail()` now records the code and reason on
`_last_exec_fail`, and the engine (a) does not claim completion and (b) raises
the same `COMMAND_REJECTED` event every other refusal raises. Verified live:

```
S6.5 @ execution | 0x0004 Length exceeds the configured memory transfer limit
                 | S6 MEM_DUMP: 61440 bytes exceeds the 1024-byte dump limit
```

That code had never previously reached any display.

### Still open — hand-off

**Every S1.4 site passes `request_id = 0`.** The report therefore cannot be
attributed to a command on the ground: `VerificationData.parse` yields
`tc_seq = 0`, and `smo_mcs.server` matches it against a verification row with
sequence 0 — either none, or an unrelated command. The row for the refused
command now stops at `ACCEPTED` instead of falsely reaching `COMPLETED`, and
the event carries the command identity, so **nothing is lost** — but the row
still cannot turn red. Closing it means threading the current TC's sequence
count to all 16 sites (`dispatch()` already receives `primary_header`), which
changes an S1.4 wire field that `tests/test_simulator/test_command_rejection_reason.py`
pins, and belongs with defect #87's plumbing rather than with a display defect.

Nothing else was found: the S1.2 / S1.8 paths, `FAILURE_CODES` and
`MEMORY_FAILURE_CODES` are all reachable, and a content test now asserts that
every integer literal assigned to an `error_code` anywhere in the dispatcher or
the subsystem models has an entry in the shared vocabulary.

## Tests

* `tests/test_mcs/test_error_code_visibility.py` (24 tests) — the aux-text
  codec including the S6 vocabulary and the "not a rejection" case; the
  endpoint serves the shared map verbatim; the broadcast event metadata carries
  the name and the decoded rejection; the FDIR journal produces a `cmd_rej` row
  with code, meaning, stage and reason and sits alongside an `s12_in` row; the
  parameter is declared, is in SID 4, is published by the model, is cleared by
  a reboot and is latched through the engine's choke point; and the browser
  renders all of it — the event line, the journal row, the OBDH pane in both
  the "failure" and "none since boot" states — with no second copy of the
  vocabulary and with subsystem event routing intact.
* `tests/test_content/test_failure_code_vocabulary.py` (12 tests) — every
  `error_code` literal in the dispatcher and in all subsystem models has a
  meaning; `memory_map.ERR_*` matches `MEMORY_FAILURE_CODES`; the S6 readings
  are a real override rather than a duplicate; `0x030B` is catalogued with the
  right name and subsystem and its description tells a reader the aux text
  carries the code; `0x0315` is declared, is in SID 4, is packed as `H` with no
  scale, and the model publishes that exact id; and both sides of the aux-text
  format use the shared helpers.
* `tests/test_mcs/test_verification_failure_display.py` — one test updated:
  `verifCodeText` now delegates hex formatting to `failureCodeLabel`, so the
  assertion follows the delegation instead of pinning the old inline
  `toString(16)`.

## Files

* `packages/smo-common/src/smo_common/protocol/pus_services.py` —
  `COMMAND_REJECTED_EVENT_ID`, `format_command_rejection`,
  `parse_command_rejection`
* `packages/smo-simulator/src/smo_simulator/engine.py` — the shared formatter,
  the OBDH latch, the S1.4 branch that no longer reports completion
* `packages/smo-simulator/src/smo_simulator/service_dispatch.py` —
  `_last_exec_fail`
* `packages/smo-simulator/src/smo_simulator/models/obdh_basic.py` —
  `last_tc_error_code`, `record_tc_failure_code()`, reboot clear
* `configs/eosat1/telemetry/parameters.yaml`,
  `configs/eosat1/telemetry/hk_structures.yaml` — 0x0315
* `packages/smo-mcs/src/smo_mcs/server.py` — `/api/failure-codes`,
  `_event_meta`, the S5 rejection decode
* `packages/smo-mcs/src/smo_mcs/displays/fdir_panel.py` — the `cmd_rej` kind
* `packages/smo-mcs/src/smo_mcs/static/index.html` — `loadFailureCodes`,
  `failureCodeLabel`, `eventIdLabel`, `eventInfoText`, `eventLogText`,
  `routeEventToSubsystem` signature, `FDIR_JOURNAL_KINDS.cmd_rej`,
  `fdirJournalText`, the `fdir-jf-cmdrej` filter, the TELECOMMAND ACCEPTANCE
  panel and its `updateOBDH()` block

## Related

* **#87** put the code and the reason on the wire and into the verification
  log; this defect is the other three surfaces.
* **#76** built the FDIR window whose journal this extends.
* **#75** — HK SIDs ship disabled, so 0x0315 (like every other SID 4
  parameter) needs `HK_ENABLE(4)` or a one-shot `HK_REQUEST(4)` before it
  appears. That is the documented behaviour, not a gap in this fix.

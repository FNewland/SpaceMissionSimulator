# 87 — a command rejection's reason never reached the ground

**Status:** Fixed

## Summary

Franz, after booting the OBC application and commanding the LOW data rate:

> "I got a 1,8"

— with nothing else. No text, no indication of what was wrong.

The spacecraft was still in beacon mode. Booting the application image does
**not** clear beacon mode (`engine._boot_application` only steps the commanded
tier when `beacon_mode` is already false), so `ttc_basic._tier_permission_error`
correctly refused the tier. It also produced an excellent, specific,
operator-readable reason:

```
TM rate is forced to the beacon tier in beacon mode (LOW refused)
```

`ttc_basic.handle_command` returned that as `result["message"]`.
`ServiceDispatcher._make_error_response` stored it in `self._last_error`.
`engine._dispatch_tc` then read only `_last_error_code` and built the S1.8 with

```python
data = struct.pack('>IH', request_id, error_code)   # tm_builder.build_execution_failure
```

The text was **thrown away one line before it would have been downlinked.**
What reached the console was `1,8` and the number `7` — and `0x0007` is shared
by *four different gates* on this one command (beacon mode, beacon-only tier,
antenna stowed, DOWNLINK mode, elevation), so the number could not identify
which one had fired even in principle. Franz debugged a correct behaviour by
hand.

The same hole existed on the acceptance side (S1.2 `>IH`) and on the
dispatcher's own execution-start failures (S1.4 `>HH`), and it was not one
site: it was the whole plumbing.

## Scope — how much was being dropped

| Where | Sites | Before |
|---|---:|---|
| `service_dispatch` routers calling `subsys.handle_command(...)` | **92** | 6 propagated the failure via `_make_error_response`; **86 discarded the result dict entirely**, so a refusal produced a truthful-looking S1.7 "execution complete" |
| `generate_s1_exec_fail(0, code)` S1.4 sites (S3 / S6 / S12 / S19) | **16** | no reason text existed at all — only a code |
| `engine._dispatch_tc` acceptance refusals (bootloader gate, bus-failed gate, `_check_tc_acceptance`, power gate) | **4** | reason computed and written to `logger.info`, never downlinked |
| `_drain_tc_queue` uplink-inactive refusal | **1** | no reason |

**86 subsystem-command call sites were silently dropping a reason**; 21 more
reported a code with no reason available. The models are not the problem —
there are 83 `{"success": False, "message": …}` returns across
`smo_simulator/models/`, i.e. the on-board software had good diagnostics all
along and none of them were reaching an operator.

## Fix

### 1. The reason travels on the wire

ECSS-E-ST-70-41 allows a **failure notice** data field after the failure code
in an S1 failure report. That slot now carries the reason, encoded as
`reason_len(uint8) + reason(UTF-8)`, capped at 160 octets and never truncated
mid-character:

| Report | Wire format |
|---|---|
| TM 1,2 acceptance failure | `request_id(u32) + error_code(u16) [+ reason_len(u8) + reason]` |
| TM 1,4 execution-start failure | `request_id(u16) + error_code(u16) [+ reason_len(u8) + reason]` |
| TM 1,8 execution failure | `request_id(u32) + error_code(u16) [+ reason_len(u8) + reason]` |

The notice is **strictly appended**: a parser reading only `>IH` (1,2 / 1,8) or
`>HH` (1,4) is byte-for-byte unaffected, and a ground segment that reads the
notice handles a spacecraft that sends none (the field decodes as absent). 1,4
keeps its historical 16-bit request id rather than being silently widened.

Codec and vocabulary live in `smo_common/protocol/pus_services.py`
(`encode_failure_reason`, `decode_failure_reason`, `FAILURE_CODES`,
`MEMORY_FAILURE_CODES`, `failure_code_text`) so the spacecraft and the ground
segment cannot drift. `VerificationData.parse` decodes both layouts.

### 2. The plumbing is generic, not per-site

`ServiceDispatcher._cmd(subsystem, cmd)` runs a subsystem command and captures
any failure reason automatically. All 92 router call sites go through it, and
the five hand-written `if not result.get("success"): return
self._make_error_response(result)` blocks are gone. **A new handler that
returns a reason now gets it downlinked with no dispatcher change**, which is
the actual fix — the previous design required every author to remember. A test
reads the dispatcher source and fails if a router ever calls `handle_command`
directly again.

The 16 S1.4 sites and the four engine-level acceptance gates were given
specific text (`engine._acceptance_failure_reason`, `engine._reject_tc`).

### 3. A rejection is an event, not just a verification row

Every refusal — at acceptance or at execution — now raises S5 event
**`0x030B COMMAND_REJECTED`** (severity MEDIUM), added to
`configs/eosat1/events/event_catalog.yaml`. Its auxiliary text carries the
command identity (`S8.1 func 65`), the failure code and the same reason, so the
rejection appears in the event log, the on-board alarm store (severity ≥ 2) and
the MCS FDIR journal — not only against the single verification row an operator
may not be looking at. The S5 aux-text cap was raised from 32 to 120 octets
(`EVENT_AUX_TEXT_MAX`); the field is length-prefixed in every parser, so that is
a free change.

### 4. The console shows it

`smo-mcs/server.py` decodes the notice via the shared parser and stores
`reason` + `error_text` on the verification-log row. The three places the MCS
already rendered S1 outcomes were **upgraded, not duplicated**:

* the **verification log** table gained `Code` and `Reason` columns —
  `0x0007 — Not permitted in the current state` and the on-board wording;
* the **command history** table gained a `Failure` column with the same;
* the **raw packet log / event log** lines carry the code's meaning and the
  reason instead of `ERR=7`.

The code→meaning map stays server-side and arrives as `error_text`, so the
browser holds no second copy to drift.

### 5. The beacon-mode gate is now discoverable

The gate is correct; it was undocumented. Added:

* `docs/LINK_BUDGET_AND_RATES.md` §6.3 "How to change rate, and why a change
  can be refused" — every gate on func 65 with the exact failure notice each
  produces, and the literal command sequences for LOW and for a HIGH dump;
* `configs/eosat1/procedures/leop/first_acquisition.md` Step 5a — clear beacon
  mode, then select LOW, with the gate order spelled out and an off-nominal
  entry for exactly Franz's symptom;
* `configs/eosat1/procedures/contingency/obc_bootloader_recovery.md` and
  `boot_loader_recovery.md` Step 3a — the same, on every path that reaches a
  booted application;
* `docs/PUS_SERVICE_REFERENCE.md` — the S1 failure-notice wire format, the
  failure-code vocabulary, and "read the notice, not the number".

## Not changed — hand-off

**Whether `OBC_BOOT_APP` should auto-clear beacon mode is an open design
decision and was deliberately left alone.** `engine._boot_application` still
leaves `beacon_mode` latched; the procedures now make the explicit
`TTC_SET_BEACON_MODE(on=0)` step mandatory and LEOP-001's Step 5 verification
was corrected accordingly (it previously asserted `tm_tier` = LOW immediately
after boot, which cannot happen while beacon mode is latched). If the decision
goes the other way, `engine._boot_application` is the single place to change
and LEOP-001 Step 5a / CTG-021 Step 3a become optional rather than mandatory.

## Tests

* `tests/test_simulator/test_command_rejection_reason.py` (33 tests) — the
  codec including multi-byte truncation; S1.8 / S1.2 / S1.4 carry the reason
  and the legacy `>IH` / `>HH` parses still succeed; the builder appends
  nothing when there is no reason; the COMMAND_REJECTED event's identity,
  catalogue entry and alarm-store copy; a **table-driven pass over nine
  rejection paths** (bad tier code, beacon-mode gate, antenna gate,
  bootloader-blocked func, bootloader-blocked service, unknown service,
  unknown subtype, unmapped memory address, bad S19 trigger kind) proving each
  gives a non-empty reason that names *that* gate; that two gates sharing
  `0x0007` read differently; and the generic-plumbing guard.
* `tests/test_mcs/test_verification_failure_display.py` (14 tests) — the row
  carries code + meaning + reason, a legacy report with no notice still
  updates the row, the WS packet metadata carries it, the FDIR journal labels
  `0x030B` from the catalogue, and the browser renders the new columns without
  a second copy of the vocabulary.

## Files

* `packages/smo-common/src/smo_common/protocol/pus_services.py` — codec,
  vocabulary, `VerificationData.reason`
* `packages/smo-common/src/smo_common/telemetry/tm_builder.py` —
  `build_verification_failure` / `build_execution_failure` reason argument,
  `EVENT_AUX_TEXT_MAX`
* `packages/smo-simulator/src/smo_simulator/service_dispatch.py` — `_cmd`,
  `generate_s1_exec_fail(reason=…)`, 16 S1.4 reasons
* `packages/smo-simulator/src/smo_simulator/engine.py` — `_reject_tc`,
  `_acceptance_failure_reason`, `_emit_command_rejected`, `_tc_identity`,
  S1.8 reason
* `configs/eosat1/events/event_catalog.yaml` — `0x030B COMMAND_REJECTED`
* `packages/smo-mcs/src/smo_mcs/server.py`,
  `packages/smo-mcs/src/smo_mcs/static/index.html`
* `docs/PUS_SERVICE_REFERENCE.md`, `docs/LINK_BUDGET_AND_RATES.md`
* `configs/eosat1/procedures/leop/first_acquisition.md`,
  `configs/eosat1/procedures/contingency/obc_bootloader_recovery.md`,
  `configs/eosat1/procedures/contingency/boot_loader_recovery.md`

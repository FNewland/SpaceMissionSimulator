## Summary

`TMFrameBuilder.flush()` in `packages/smo-rfsim/src/smo_rfsim/ccsds/tm_frame.py`
cleared the accumulated byte buffer but did **not** clear the recorded
packet-start offsets, and it hard-coded the First Header Pointer (FHP) of the
padded flush frame to `0` even when the remaining bytes were the *continuation*
of a packet that started in an earlier frame.

Two consequences follow from the stale offsets and the wrong FHP:

1. A flush frame carrying a mid-packet continuation advertised `FHP = 0`,
   telling the receiving decommutator that a new packet header began at the
   first byte of the frame data field. The receiver therefore attempted to
   parse continuation bytes as a CCSDS Space Packet primary header, desynced,
   and discarded the reassembly buffer.
2. Because the offsets list survived the flush, the *next* frame built after a
   flush computed its FHP from offsets belonging to bytes that were no longer
   in the buffer, propagating the error.

Net effect: a full frame of telemetry was silently lost at every flush boundary
that fell inside a packet — no error, no event, no counter. The loss was
data-dependent (it required a packet to straddle the flush), which is why it
surfaced only as an intermittent, apparently order-dependent failure of
`tests/test_acceptance/test_commands.py::TestAOCSCommandsRF::test_st2_power_on`
(the ST2 power-on verification packet was the one that fell in the gap). The
test was previously assumed to be a test-isolation flake; it was in fact a real
transfer-frame defect that reproduced standalone at the same position (3/3).

## Severity

**Major** — silent telemetry loss on the RF path. No mission-ending effect
(the operator sees a missing verification report and re-issues), but silent
data loss in a training simulator teaches the wrong lesson about link
behaviour, and it masked itself as test flakiness for several sessions.

## Requirements

- R1 — `flush()` shall reset the packet-start offset list together with the
  byte buffer, so that no offset can refer to bytes that have left the buffer.
- R2 — A flush frame whose data field begins with the continuation of a packet
  started in a previous frame shall carry the CCSDS "no packet starts in this
  frame" FHP value (all-ones, `0x7FF` for the 11-bit field), not `0`.
- R3 — The FHP of any frame containing one or more packet starts shall be the
  offset of the first such start within the frame data field.
- R4 — Regression tests shall cover the straddling-packet flush case explicitly.

## Suggested implementation

In `TMFrameBuilder.flush()`, clear `self._packet_offsets` (or equivalent) in
the same statement block that clears the byte buffer, and compute the flush
frame's FHP with the same helper used by the normal full-frame path rather
than passing a literal `0` — the helper already returns the idle/continuation
pointer when the offset list is empty.

## Acceptance criteria

- A packet that straddles a flush boundary is reassembled correctly by the
  decommutator, with no lost frame.
- A flush frame containing only continuation bytes carries the continuation
  FHP; a flush frame containing a packet start carries that packet's offset.
- `tests/test_rfsim/test_tm_frame.py` covers both cases.
- `tests/test_acceptance/test_commands.py::TestAOCSCommandsRF::test_st2_power_on`
  passes both standalone and in full-suite order.

## Affected areas

- `packages/smo-rfsim/src/smo_rfsim/ccsds/tm_frame.py` (`TMFrameBuilder.flush`)
- Any consumer of the RF telemetry path: `smo-rfsim` decommutator, MCS
  telemetry display when running with `--rf`.

## Related

- Defect #18/#19 (RF bridge items) — same package, independent causes.
- Test-strategy note: this defect is the reason the "known order-dependent
  flake" annotation appeared in earlier session records; that annotation is
  now withdrawn.

## Status

**Fixed** (2026-07-28). `flush()` resets offsets and derives the FHP from the
offset list; three regression cases added to `tests/test_rfsim/test_tm_frame.py`.
Full suite now runs 2664 passed / 0 failed.

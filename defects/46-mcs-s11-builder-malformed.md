# Defect 46 — MCS S11.4 builder embedded svc+subtype+data instead of a full CCSDS packet

## Summary

The simulator's S11 handler (`service_dispatch.py` `_handle_s11`, subtype 4)
stores `data[4:]` verbatim as the deferred TC and, at fire time, the engine
runs `decommutate_packet` on it (`engine.py` `_dispatch_tc`). The MCS
"Schedule Absolute TC" builder (`static/index.html` PUS_FIELD_DEFS '11.4')
packed only `exec_time(4) + service(1) + subtype(1) + data` — a 2-byte
svc/st blob is not a CCSDS/PUS packet, so **every** UI-scheduled command
died at execution time with "TC dropped: malformed packet". Worse, the
operator saw a successful S1.1/S1.7 for the *S11 request itself*, so the
command looked scheduled and verified. There was also no ground-side
UTC→OBC-CUC conversion.

## Severity

**Major** — the entire ground-commanded time-tagged scheduling capability
was non-functional while appearing to work.

## Status

**Fixed** server-side (covers the UI builder, procedures and any API
client without touching the packing JS):

1. `MCSServer._embed_s11_packet(service, subtype, data)`: for S11.4
   payloads whose embedded bytes do not already decommutate as a packet,
   re-wraps `svc+st+data` into a complete framed TC via
   `tc_manager.build_command` (own APID/seq/CRC) and preserves the 4-byte
   exec time. Already-complete embedded packets pass through untouched.
   Applied in both `_handle_pus_command` and `_proc_send_command`. The
   embedded TC is tracked in the verification log (state `SCHEDULED`) so
   its S1 reports at fire time correlate.
2. UTC→CUC: `MCSServer._utc_to_obc_cuc` anchors on OBC-time TM
   (param 0x0309) + ground-clock delta, falling back to the shared
   `smo_common` `TIME_EPOCH` (the same epoch the OBDH model boots from).
   Used by the new S11 activity upload endpoint (defect #44).
3. Engine-side store→fire path covered by new tests, including a
   regression test documenting that the old bare payload is released but
   never executes.

## Acceptance criteria

- [x] A UI-style S11.4 request (`exec+svc+st+data` hex) is uplinked with a
      complete, CRC-valid embedded TC packet
      (tests/test_mcs/test_s11_builder.py).
- [x] The engine stores such a TC, fires it past the time tag, executes the
      embedded command, and emits S1.1/S1.7 for the embedded sequence count
      (tests/test_simulator/test_s11_schedule_fire.py).
- [x] Already-embedded full packets are not double-wrapped.
- [x] Legacy bare payloads demonstrably never execute (regression guard).

## Affected areas

- `packages/smo-mcs/src/smo_mcs/server.py`
- `tests/test_mcs/test_s11_builder.py`
- `tests/test_simulator/test_s11_schedule_fire.py`

## Related

- Defect #44 (planner S11 upload path is built on this fix). Note:
  `docs/PUS_SERVICE_REFERENCE.md` still describes S11 *subtype 11* as
  "insert" — in code subtype 4 inserts and 11 is delete-all; doc not
  changed here.

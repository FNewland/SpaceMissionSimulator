## Summary

`AOCSState.mode` in `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py`
has a dataclass default of `4` (NOMINAL), with a comment
"start in NOMINAL for backwards compat". This causes the AOCS subsystem to
emit telemetry on startup before any runtime override has applied, and users
observe "ADCS shows in DETUMBLE mode at start" (the mode is non-zero, the
rates and quaternion are the dataclass defaults, and the UI interprets the
combination as a live AOCS state).

On a real spacecraft there should be **no AOCS telemetry at all** and the
mode should be **OFF** (0) until the AOCS subsystem is explicitly powered
and commissioned.

## Severity

Major — misleading initial state; contradicts the bootloader operational
model; breaks the separation/LEOP procedure walkthrough because AOCS
appears active before it has been commanded on.

## Steps to reproduce

1. Start simulator fresh (no overrides).
2. Observe AOCS telemetry panel on the MCS.

**Expected**: "AOCS OFF", no mode reported, no attitude/rate telemetry.
**Actual**: Mode displayed as non-zero, rates and quaternion showing
dataclass defaults, UI interprets this as DETUMBLE / NOMINAL depending on
the bindings.

## Suggested fix

1. Change `AOCSState.mode: int = 4` → `AOCSState.mode: int = 0` in
   `aocs_basic.py` (line ~28). The comment justifying NOMINAL default is
   obsolete now that `_enter_bootloader_mode()` explicitly sets `mode = 0`.
2. Verify `_emit_hk_packets` correctly gates SID 2 (AOCS) in bootloader
   (it does — `sw_image == 0 and sid not in (10, 11)` skip clause).
3. Audit whether any path populates AOCS params (mode 0x020F) at
   construction before the bootloader gate can take effect — if so, gate
   the param population too.
4. Fix any unit tests that relied on mode=4 default. Follow the pattern
   from the earlier `test_star_tracker_failure` fix — explicitly set mode
   before asserting.
5. Re-run the full test suite.

## Acceptance criteria

- Fresh start shows AOCS mode = 0 (OFF), no SID 2 HK packets in bootloader.
- Post-boot, AOCS mode remains 0 until explicitly commanded (no automatic
  transition to DETUMBLE/NOMINAL from the default).
- Full pytest suite passes.
- New regression test: construct engine, assert
  `engine.subsystems['aocs']._state.mode == 0`.

## Affected files

- `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py` (line ~28)
- Any tests referencing the old default (likely 1-2 tests)
- `packages/smo-simulator/src/smo_simulator/engine.py` (audit only)

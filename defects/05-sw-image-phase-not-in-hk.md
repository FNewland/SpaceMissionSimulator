## Summary

Two of the most fundamental spacecraft state variables — **`sw_image`
(parameter 0x0311)** and **`spacecraft_phase` (parameter 0x0129)** — do not
appear in any HK structure and are not exposed through the MCS in any
widget. This means the ground cannot observe which software image is
executing or which mission phase the spacecraft is in. Both values are
required preconditions for most flight procedures and are fundamental to
FDIR decision-making on the ground.

## Severity

**Critical** — operability defect. A mission cannot be flown safely if the
MCS cannot confirm the OBSW image or the mission phase. These are the two
most basic state variables and should be in every HK packet at minimum.

## Background

PUS-C mission operations universally treat "which software image is
executing" and "which mission phase" as mandatory HK:

- They are preconditions for every S6 (memory) and S8 (function) command.
- They gate S15 (storage) commands.
- They drive ground FDIR and go/no-go for every procedure.
- They are the first things an operator checks on AOS to confirm the
  spacecraft state.

Today they exist only in `engine.params` and are never packed into an HK SID
or exposed via S20 (Parameter Management Service).

## Steps to reproduce

1. Open the MCS with the simulator running.
2. Attempt to find `sw_image` or `spacecraft_phase` in any telemetry panel.

**Expected**: Both values visible prominently — at minimum in the OBDH HK
packet, and ideally in the beacon so they are visible even in bootloader
when nothing else is downlinked.

**Actual**: Neither parameter is in any HK structure; the MCS has no
way to display them.

## Suggested fix

1. **Add `0x0129` (phase) and `0x0311` (sw_image) to SID 6 (OBDH HK)**.
   These are 1-byte fields. Impact on bandwidth is negligible (+2 bytes
   every 8 s).
2. **Add them to SID 11 (Beacon)** so they are visible in bootloader.
   The beacon already carries minimal OBDH state; these are natural
   additions.
3. **Add an MCS tile** to the OBDH screen showing "SW Image:
   BOOTLOADER/APPLICATION" and "Phase: PRE_SEP / SEP_TIMER / INIT_PWR /
   BOOTLOADER_OPS / LEOP / COMMISSIONING / NOMINAL".

## Acceptance criteria

- `configs/eosat1/telemetry/hk_structures.yaml` SID 6 entry includes
  `0x0129` and `0x0311`.
- Beacon SID 11 includes `0x0129` and `0x0311`.
- MCS OBDH screen has a clearly labelled status tile for both.
- Regression test: decom SID 6 and assert both param IDs are present with
  correct values for each mission phase transition.

## Affected files

- `configs/eosat1/telemetry/hk_structures.yaml`
- MCS OBDH screen frontend binding
- `tests/test_simulator/` — new HK structure test

## Related

- Defect #6 (MCS has no generic parameter-watch widget). This defect is a
  symptom of the deeper operability gap addressed by #6. Even if #5 is
  fixed by adding these two params to HK, #6 remains necessary for
  observability of every other PUS parameter.

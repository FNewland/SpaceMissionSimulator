## Summary

Two related faults in housekeeping enable/disable, both of which made a real
command a no-op.

1. **Every HK SID was enabled by default, contradicting the operations manual.**
   `engine.py:190` enabled every SID at construction, and `_exit_bootloader_mode`
   (`engine.py:459-460`) re-enabled all of them on application boot, as did the
   instructor `set_phase` path. But `configs/eosat1/manual/commissioning_sequence_walkthrough.md:233-238`
   specifies `HK_ENABLE(sid=1..6)` (S3.5) as six explicit commissioning steps,
   `procedures/nominal/hk_configuration.md` and `procedures/emergency/obc_reboot.md`
   both list them, and `manual/09_leop.md:149` states that full housekeeping
   "comes online only after the application software has booted". Because every
   SID was already on, **TC(3,5) HK_ENABLE was a no-op in every one of those
   procedures** — the operator ran six commands, nothing changed, and the
   commissioning step that teaches "telemetry is a resource you configure" was
   invisible.

2. **HK_DISABLE(sid=11) was accepted in bootloader mode.** SID 11 on APID 2 is
   the *only* telemetry the bootloader produces (`BOOTLOADER_BEACON_SID`;
   `_emit_hk_packets` skips everything except SIDs 10/11 while `sw_image == 0`).
   Disabling it left the spacecraft silent with no telemetry to diagnose it by,
   and the OBSW accepted the command without complaint.

## Severity

**Major** — (1) makes a documented procedure inert and teaches that a real
command does nothing; (2) is a single telecommand that renders the spacecraft
unobservable in the one mode where the operator has the fewest options.

## Status

**Fixed.**

1. **Default enable state is config-driven.** New `simulation.yaml`
   `housekeeping:` block:

   ```yaml
   housekeeping:
     default_enabled_sids: [1, 11]
     enable_all_by_default: false
   ```

   `enable_all_by_default: true` restores the previous behaviour for anyone who
   wants it. The engine resolves the set once (`_resolve_default_hk_sids()`) and
   applies it at construction, on application boot (`_exit_bootloader_mode`) and
   on an instructor phase change (`_apply_default_hk_enable()`), replacing the
   two "enable everything" loops. Bootloader mode still restricts to SID 11 as
   before.

   **Chosen set and why.** *SID 11 (Beacon)* is not optional and is forced into
   the set regardless of config: without it the spacecraft is silent at power-up
   and cannot be commissioned at all. *SID 1 (EPS platform vitals)* is included
   because the operator must be able to judge whether the spacecraft is
   power-safe before switching anything else on — commanding telemetry on with
   no view of the power state is the wrong lesson. SIDs 2 (AOCS), 3 (TCS),
   4 (platform), 5 (payload) and 6 (TTC) are **off** at boot and are brought up
   by the six `HK_ENABLE` commands the commissioning walkthrough already
   specifies, which now do something. The on-demand path (S3.27 HK_REQUEST) is
   unaffected — it was never gated on the enable flag — so a disabled SID can
   still be sampled on request, which is the correct distinction between
   *periodic generation* and *a report*.

   HK enable state is also now saved and restored by `BreakpointManager`, since
   which SIDs the operator has commanded on is part of the spacecraft
   configuration rather than a constant.

2. **Beacon HK cannot be disabled in the bootloader.** `_handle_s3` subtypes 6
   (ECSS) and 4 (legacy) now refuse `sid == BOOTLOADER_BEACON_SID` while
   `sw_image == 0`, returning **S1.8 failure code 0x0005** ("invalid in this
   mode") and leaving the SID enabled. The refusal is conditional, not blanket:
   the same command succeeds once the application image is running, and no other
   SID is ever gated.

## Files

- `packages/smo-simulator/src/smo_simulator/engine.py`
  (`_resolve_default_hk_sids`, `_apply_default_hk_enable`, HK init,
  `_exit_bootloader_mode`, `set_phase` handler)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
  (`_handle_s3` subtypes 4/6, `_is_bootloader_beacon_sid`)
- `packages/smo-common/src/smo_common/config/schemas.py`
  (`SimHousekeepingConfig`)
- `packages/smo-simulator/src/smo_simulator/breakpoints.py` (`hk_enabled`)
- `configs/eosat1/simulation.yaml` (`housekeeping:`)
- Tests: `tests/test_simulator/test_pus_service_fixes.py`
  (`TestHKDefaultEnableState`, `TestBeaconHKCannotBeDisabledInBootloader`);
  `tests/test_commissioning_sequence.py::TestPhase3` — the old
  `test_all_sids_enabled_after_boot` pinned the contra-manual behaviour and is
  replaced by `test_default_sids_after_boot_match_config` plus
  `test_hk_enable_is_not_a_no_op_after_boot`.

## Related

- The state summary now reports `hk_enabled` (sid -> bool), so the ground can
  show which SIDs are actually generating rather than assuming all of them are.
- Defect #03 (TM storage active in bootloader) — same class: a bootloader that
  behaved as if the application were running.

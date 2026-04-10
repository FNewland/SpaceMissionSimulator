## Summary

`Engine._enqueue_tm` was unconditionally routing every TM packet to the S15
onboard stores, relying solely on `disable_store()` having been called in
`_enter_bootloader_mode()` to prevent writes. On a real spacecraft the
bootloader has no PUS-C S15 storage service at all — no packets should
accumulate in HK/Event/Science/Alarm stores while `sw_image == 0`.

The same risk applied to the alarm-store path in `_emit_event`.

## Severity

Major — if any path re-enabled a store before the application booted, or if
a mission config started outside bootloader but then re-entered bootloader
after reboot, packets would be captured when they shouldn't be, giving
operators misleading store status during recovery operations.

## Status

**Fixed** in `packages/smo-simulator/src/smo_simulator/engine.py`:

1. `_enqueue_tm` now checks `int(self.params.get(0x0311, 1)) != 0` before
   decommutating and routing the packet to storage.
2. `_emit_event` alarm-store path now has the same `sw_image != 0` gate.

This gives belt-and-braces protection: stores are both `disable_store()`-ed
in bootloader *and* the routing layer refuses to hand them packets,
independent of the enable flag.

## Acceptance criteria

- [x] Bootloader phase shows 0 packets in all S15 stores regardless of how
      long the sim runs.
- [x] Application boot transition (`_exit_bootloader_mode`) re-enables stores
      and HK capture resumes.
- [ ] Regression test: start in bootloader, tick for 10 sim-minutes, assert
      `tm_storage.get_status()` shows count=0 for all stores.
- [ ] Regression test: after `_exit_bootloader_mode()`, HK packets do
      accumulate at the expected rate.

## Affected files

- `packages/smo-simulator/src/smo_simulator/engine.py`
  (`_enqueue_tm`, `_emit_event` alarm path)

## Related

- Defect #1 (OBDH Buffer Fill – HK TM exceeding 100%) was originally thought
  to be caused by this, but the 353% reading persists and points at an
  OBDH-internal counter, not the S15 stores.

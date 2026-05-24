## Summary

The OBDH watchdog is wired backwards relative to its own comment, so it never
fires during nominal operation and the injected `watchdog_reset` failure cannot
trigger a reboot in the normal flight mode.

`models/obdh_basic.py:227-239`:
```python
if s.sw_image == SW_APPLICATION and s.watchdog_armed:
    if s.mode == 0:
        # Safe mode: hold timer at zero (watchdog doesn't fire in safe)
        s.watchdog_timer = 0
    else:
        s.watchdog_timer += 1
        if s.watchdog_timer >= s.watchdog_period: ... self._reboot(REBOOT_WATCHDOG)
```
Per `OBDHState` (`:39`), mode `0 = nominal`, `1 = safe`, `2 = maintenance`. The
comment says "safe mode", but the code zeroes the timer in **mode 0 = nominal**.
So the watchdog only counts in safe/maintenance and is frozen during nominal ops —
the opposite of real watchdog behaviour.

Consequently `inject_failure("watchdog_reset")` (`:649-651`), which sets
`watchdog_timer = watchdog_period`, is erased on the next nominal-mode tick
(forced back to 0 at `:231`) before the `>=` check. The `obc_watchdog.yaml`
scenario injects `cpu_spike` then `watchdog_reset` and asks the operator to
"confirm watchdog-triggered reboot" — which cannot deterministically happen.
`cpu_spike` (`:656-657`) only nudges a CPU-load number and never feeds the
watchdog or changes mode. The developers know it's unreliable:
`tests/test_acceptance/test_failures.py:320-323` asserts nothing ("Watchdog may
or may not immediately reboot depending on timer state").

## Severity

**Major** — the watchdog-reset contingency is not deterministically trainable and
the nominal-mode watchdog is modelled backwards. A core OBDH FDIR behaviour is
effectively absent in normal flight.

## Requirements for the fix

1. The watchdog must count (and be able to time out / reboot) during nominal
   operation, and the injected `watchdog_reset` must deterministically trigger a
   reboot.

## Suggested implementation

- Invert the mode condition so the timer counts in nominal and is held/fed in
  safe mode (and fix the misleading comment). At minimum, have `watchdog_reset`
  force the reboot on the next tick regardless of mode.
- Optionally tie sustained `cpu_spike` to a missed watchdog kick so the
  scenario's two-step setup (spike → reset) is causally connected.

## Acceptance criteria

- In nominal mode, an unkicked/forced watchdog times out and triggers
  `REBOOT_WATCHDOG`.
- Running `obc_watchdog.yaml` deterministically produces the reboot the scenario
  asks the operator to confirm.
- A deterministic test replaces the current "may or may not" acceptance test.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/models/obdh_basic.py` (watchdog block, `cpu_spike`)
- `tests/test_acceptance/test_failures.py` (tighten the assertion)

## Related

- Defect #23 (events) — the reboot/SEU events also need that fix to be observed
  as S5 events.

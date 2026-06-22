## Summary

S8 function-management commands 100–107 (legacy EPS / TCS quick-actions) were
silently dropped.

`service_dispatch.py._handle_s8` (`service_dispatch.py:457-485`) routed only
func_ids 0–83 (AOCS 0–15, EPS 16–25 / 81–83, payload 26–39, TCS 40–49,
OBDH 50–62 / 80, TTC 63–78). func_ids 100–107 — defined in
`configs/eosat1/commands/tc_catalog.yaml` as the `EPS_*_LEGACY` /
`TCS_HEATER_*_LEGACY` quick-actions and offered as MCS quick-action buttons —
fell through to `return []`. The operator pressed a button and nothing
happened, with no error reported.

## Severity

**Major** — a set of operator-facing quick-action buttons were inert: no
effect, no acknowledgement, no error.

## Status

**Fixed** in `packages/smo-simulator/src/smo_simulator/service_dispatch.py`:

1. `_handle_s8` now routes `func_id in range(100, 108)` to a new
   `_route_legacy_quick_action` (`service_dispatch.py:481-485`).
2. `_route_legacy_quick_action` (`:488-515`) maps:
   - 100 / 101 → EPS `enable_array` / `disable_array` A
   - 102 / 103 → EPS `enable_array` / `disable_array` B
   - 104 / 105 → TCS battery-heater on / off
   - 106 / 107 → TCS OBC-heater on / off

   reaching the `enable_array` / `disable_array` handlers that were not
   otherwise exposed via any S8 func.

## Acceptance criteria

- [x] funcs 100–107 are routed rather than dropped.
- [x] Representative legacy quick-action funcs now have a real, observable
      effect on the EPS / TCS models.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
  (`_handle_s8` 100–107 range, new `_route_legacy_quick_action`)
- `tests/test_simulator/test_dispatch_defects_bcd.py` — representative funcs now
  have a real effect.

## Related

- Defect #32 (subsystem dead/inert commands) — this is another inert-command
  path, now wired; #39 (S8 func 49 thermal map) — found in the same dispatch
  audit.

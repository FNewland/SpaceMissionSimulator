## Summary

The MAG_SELECT telecommand cannot actually select the redundant magnetometer
(Mag B). The dispatch and handler disagree on the argument type, so the command
always lands in a legacy branch that doesn't change the selection.

- Dispatch sends the unit as a boolean: `service_dispatch.py:424-426`
  (`func_id == 7` → `aocs.handle_command({"command":"mag_select","on": bool(data[0])})`).
- The handler resolves `source` from `on` and, because it's a bool, takes the
  legacy branch that sets `mag_valid` rather than the actual A/B selector
  (`models/aocs_basic.py:1423-1430`): `if isinstance(source, bool): ...
  self._state.mag_valid = source; return {"success": True}`. The real A/B logic
  at `:1432-1441` is only reachable when `source` is the string `'A'`/`'B'`,
  which dispatch never sends.

Intended behaviour: `tc_catalog.yaml:486-493` defines MAG_SELECT with
`unit: 0=primary, 1=redundant`; `scenarios/aocs_sensor_cascade.yaml:41` lists
recovery step "Switch to MAG-B". A compounding effect masks the need entirely:
when `mag_a_failed` is injected, `_tick_magnetometer` auto-falls-back to Mag B
(`models/aocs_basic.py:581-583`), so the composite stays valid with no operator
action and the `mag_select` telemetry (0x0229, `:1040`) never changes.

## Severity

**Major** — there is no ground path to command `mag_select = 'B'`. The cascade
scenario's required "switch to MAG-B" recovery action is impossible to perform
meaningfully and has no observable effect, so that recovery step is un-trainable.

## Requirements for the fix

1. MAG_SELECT must let the operator deterministically select Mag A or Mag B, with
   an observable change in `mag_select` telemetry.

## Suggested implementation

- In `_route_aocs_cmd` func_id 7, send a concrete selector, e.g.
  `{"command":"mag_select","source": 'B' if data[0] else 'A'}`, and/or extend the
  handler to accept a numeric `unit` (0/1) directly.
- Reconsider the silent auto-fallback so an operator-driven selection is still
  meaningful (or at least observable) during the cascade scenario.

## Acceptance criteria

- Commanding MAG_SELECT unit=1 sets `mag_select='B'` and the 0x0229 telemetry
  reflects it.
- A test issues the S8 func 7 command and asserts the selection changed.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (`_route_aocs_cmd` func 7)
- `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py` (`mag_select` handler, auto-fallback)

## Related

- Defect #33 (unobservable telemetry) — the raw dual-mag A/B channels are also
  absent from HK, weakening Mag-A-vs-B fault isolation in the same scenario.

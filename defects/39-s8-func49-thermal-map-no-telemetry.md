## Summary

S8 func 49 `TCS_GET_THERMAL_MAP` acknowledged the command but returned no
telemetry, so the thermal map never downlinked.

The func-49 branch (`service_dispatch.py:816-817`) called
`tcs.handle_command({"command": "get_thermal_map"})` and stored the result, but
returned no TM ("For now, just acknowledge") — unlike the sibling query funcs 61
and 62 (`service_dispatch.py:868-879`) which pack and return an S8.2 reply. The
operator received the S1 acknowledgement but the thermal map itself was never
sent down.

## Severity

**Minor** — the command was acknowledged and the data was computed; only the
downlink of the thermal-map reply was missing, with no operational-safety
impact.

## Status

**Fixed** in `packages/smo-simulator/src/smo_simulator/service_dispatch.py`: the
func-49 branch (`service_dispatch.py:816-831`) now packs the thermal-map result
into `tm_builder._pack_tm(service=8, subtype=2, ...)` and returns it, mirroring
funcs 61 / 62. The fields are packed in a fixed order (`:822-829`, e.g.
`temp_obc`, `temp_fpa`, panel temps) so the ground segment can decode
positionally.

## Acceptance criteria

- [x] func 49 returns an S8.2 TM packet carrying thermal-map data (e.g.
      `temp_obc`, `temp_fpa`).
- [x] The reply structure mirrors the sibling query funcs 61 / 62.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (func-49
  branch)
- `tests/test_simulator/test_dispatch_defects_bcd.py` — func 49 now returns a TM
  packet carrying thermal-map data.

## Related

- Defect #38 (legacy quick-actions dropped) — found alongside this in the S8
  dispatch audit; #33 (computed-but-unobservable telemetry) — same flavour of
  data that exists but is not downlinked.

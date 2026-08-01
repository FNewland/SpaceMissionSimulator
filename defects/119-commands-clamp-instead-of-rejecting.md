# 119 — out-of-range command arguments were silently clamped into range

**Status:** Fixed

## Summary

Several command handlers "validated" an argument by clamping it to the nearest
legal value and returning success. The operator saw an accepted command, a
completed execution and a spacecraft doing something other than what was
commanded — with no failure code, no event and no indication that anything had
been altered.

## Root cause

The pattern, e.g. `models/payload_basic.py:887`:

```python
s.detector_gain = max(0.1, min(10.0, gain))
return {"success": True, ...}
```

and the same shape for the compression setting and for the EPS solar-array
drive angle (`eps_basic.py:1111`). A student commanding a gain of 25 got a gain
of 10 and an S1.7 "execution complete".

The worst instance is not a clamp at all but its cousin, an accepted argument
that cannot be executed: a **band mask of 0** — an image with no spectral
bands — was accepted, and the payload then produced a product with no data. The
command was legal by every check in the system and physically meaningless.

Two things make this a defect rather than a style question:

1. **It is not what the courseware teaches.** The operations guide teaches
   operators to read the verification chain; a clamp makes the verification
   chain lie.
2. **The infrastructure to do it properly already existed.** After **#87**,
   a refusal carries an operator-readable reason on the S1.8 and raises
   `0x030B COMMAND_REJECTED`. Clamping opted out of a facility that was already
   built.

## Fix

* Ranges are declared per argument in the Phase E `TCFieldDef` schema
  (`valid range`, `enum labels`), so validation is derived from the catalogue
  rather than hand-written per handler.
* An argument outside its declared range is **rejected** with a reason naming
  the argument and the range, not clamped.
* A band mask of 0 is rejected as a distinct case ("no bands selected").
* Where clamping is genuinely the documented behaviour of a physical device —
  a drive that mechanically stops at its end of travel — the clamp remains and
  is stated in the catalogue and the manual, so the two cases are
  distinguishable by an operator instead of looking identical.

## Tests

* `tests/test_common/test_arg_schema.py::TestSchemaConsistency::test_ranges_round_trip_to_raw_range`
  and `::test_defaults_inside_range` — every declared range is coherent;
* `test_cooler_setpoint_minus_65_rejected_but_encodable` — a value that
  encodes fine on the wire but is outside the declared range is refused, which
  is exactly the case a clamp used to swallow;
* `tests/test_simulator/test_ttc_enhanced.py::TestTxPowerLevelsWP2::test_unknown_level_is_rejected_never_clamped`;
* `tests/test_simulator/test_payload_enhanced.py` — gain, compression and band
  mask refusals, each with a reason.

## Files

* `packages/smo-simulator/src/smo_simulator/models/payload_basic.py`,
  `eps_basic.py`, `ttc_basic.py`, `tcs_basic.py`
* `configs/eosat1/commands/tc_catalog.yaml` — declared ranges and enums
* `packages/smo-common/src/smo_common/commands/`

## Related

* **#87** — the refusal-reason plumbing this defect declined to use.
* **#114**, **#115** — the same "accept and default" instinct in the argument
  decoding itself.

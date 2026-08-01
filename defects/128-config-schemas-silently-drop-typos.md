# 128 — a typo in a configuration file was silently discarded

**Status:** Fixed

## Summary

None of the pydantic configuration schemas set `extra="forbid"`, which is
pydantic's default-permissive behaviour: an unrecognised key is dropped and the
model validates. So `capacity_wh` typed as `capactiy_wh`, or a key placed one
level too deep, produced a **valid** configuration in which the value the author
wrote was silently replaced by the schema default.

## Root cause

The instructor-facing promise of this repository is that the mission is
configured, not coded — 68 schemas over `configs/eosat1/`, edited by
instructors through the Phase F configuration editor and by hand. With
permissive schemas, the feedback for a mistyped key is *nothing at all*: the
simulator boots, the exercise runs, and the spacecraft behaves as if the line
had not been written.

This is not a theoretical risk in this repo — it is how several defects in this
register survived. A dead configuration key looks identical to a live one, so
`arrays:` (**#100**), `restore_hysteresis_v` (**#99**) and the whole of
`mcs/limits.yaml` (**#120**) sat in shipped files looking authoritative.
Forbidding unknown keys does not by itself catch a key with no consumer, but it
removes the largest source of them.

## Fix

* A shared `StrictModel` base with `extra="forbid"`; **all 68 schemas** inherit
  it, so a typo is a boot failure naming the file, the path and the offending
  key.
* Paired with a consumer check: a key that validates but that no code reads is
  also a failure, with an explicit allowlist for keys that are deliberately
  documentation-only.

## Tests

`tests/test_content/test_config_keys_have_consumers.py`

* `test_schemas_reject_unknown_keys` — the defect, stated directly;
* `test_every_schema_inherits_the_strict_base` — a new schema cannot opt out;
* `test_every_shipped_config_validates_against_its_schema`;
* `test_every_config_key_has_a_consumer` (parametrised per config file) and
  `test_known_unused_entries_still_exist` — the allowlist may not rot.

`tests/test_common/test_config_validation.py` and
`tests/test_common/test_confedit.py` cover the editor's validation path, so an
instructor gets the same error before applying a change rather than at the next
boot.

## Files

* `packages/smo-common/src/smo_common/config/schemas.py` — `StrictModel`
* `packages/smo-common/src/smo_common/config/loader.py`
* `docs/SIMULATION_CONFIG.md`

## Related

* **#51** — parameters hard-coded rather than configured; this defect is the
  cost of the correction, and its guard rail.
* **#99**, **#100**, **#120** — dead configuration keys that a permissive
  schema helped to hide.

## Summary

`tc_catalog.yaml` field definitions named `on` / `off` parsed as YAML booleans
and aborted the entire catalog load.

About ten command field definitions in
`configs/eosat1/commands/tc_catalog.yaml` were written `name: on` / `name: off`,
which YAML parses as the booleans `True` / `False`. `TCFieldDef.name: str` then
raises a `ValidationError`, and `load_tc_catalog` aborts loading the **entire**
catalog. The MCS server swallows the exception in a `try/except`, so the
commands silently never loaded — **0 commands** loaded through the pydantic
loader pre-fix.

## Severity

**Major** — a single boolean-coerced field name took down the whole telecommand
catalog via the loader, with no error surfaced to the operator.

## Status

**Fixed** in `configs/eosat1/commands/tc_catalog.yaml` and
`tests/test_common/test_config_validation.py`:

1. All affected fields are now quoted as `"on"` / `"off"` (e.g.
   `tc_catalog.yaml:516`, `:523`, `:622`, `:629`, `:792`, `:799`, `:806`,
   `:813`, `:1020`, `:1073`). The catalog now loads cleanly — **182 commands**.
2. The duplicate-identifier check in
   `tests/test_common/test_config_validation.py:420-433` was extended to key S6
   (memory) commands by name, differentiating them by the memory address carried
   in the data field (mirroring the existing S2 device-id carve-out), so the new
   ADCS per-axis torque-gain S6 commands (`AOCS_SET_TORQUE_GAIN_X/Y/Z`,
   `AOCS_SET_TORQUE_GAINS_ALL`, `AOCS_DUMP_TORQUE_GAINS`,
   `tc_catalog.yaml:435-479`) do not false-positive.

## Acceptance criteria

- [x] No `name: on` / `name: off` field parses as a boolean; all are quoted.
- [x] The full catalog validates through `load_tc_catalog` (182 commands).
- [x] The duplicate-identifier check tolerates address-differentiated S6
      memory commands.

## Affected areas

- `configs/eosat1/commands/tc_catalog.yaml` (quoted `on`/`off` field names)
- `tests/test_common/test_config_validation.py`
  (`test_tc_catalog_loads_through_loader` — loads via the pydantic loader, the
  gap that hid this; S6 duplicate-check carve-out)

## Related

- Defect #17 (duplicated/diverged PUS parser) — the MCS-side swallowing of the
  loader exception is what hid this for so long; the new ADCS torque-gain S6
  commands are a feature added in the same session (not a defect).

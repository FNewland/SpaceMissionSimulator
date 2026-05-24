## Summary

`smo-common`'s PUS telemetry service parser is an orphaned parallel
implementation. `protocol/pus_services.py` defines `parse_service_data`
(`:87`) plus the dataclasses `VerificationData`, `HousekeepingData`,
`EventData`, and `ParameterValueData` — it looks like the canonical shared
decoder for downlinked PUS services (S1/S3/S5/S20). But grep shows
`parse_service_data` is imported only by `tests/test_common/test_protocol.py`;
there are **zero production importers** of `smo_common.protocol.pus_services`.

The real TM decode path is re-implemented inline in the MCS:
`smo-mcs/tm_processor.py:30-115` calls `decommutate_packet` then parses S3/S5/S12
with its own `struct.unpack` (`_process_hk`, `_process_event`,
`_process_monitoring`) and never references the shared dataclasses. The two
implementations have **already diverged**: `tm_processor` handles S12
(monitoring) which `parse_service_data` lacks, while `parse_service_data`
handles S20 (parameter values) which `tm_processor` lacks (see defect #6).

## Severity

**Major** (within smo-common) — the shared library's "official" service parser
is dead while every consumer hand-rolls its own packet parsing. This is a
maintenance trap: limit/structure changes must be made in multiple places, and
the divergence (S12 vs S20 coverage) is exactly the kind of drift a shared
parser is meant to prevent.

## Requirements for the fix

1. There should be one authoritative PUS service parser used by all TM
   consumers, or the orphaned module should be removed to avoid implying a
   shared contract that isn't used.

## Suggested implementation

- Route `smo-mcs/tm_processor.py` through `parse_service_data` (extending it to
  cover S12 and keeping S20), so the MCS consumes the shared decoder; then
  re-export the dataclasses from `protocol/__init__.py`.
- Or, if the inline MCS parsing is preferred, delete `pus_services.py` and its
  dataclasses and update the tests, so the codebase doesn't carry a dead
  "canonical" parser.

## Acceptance criteria

- Exactly one PUS service decode implementation exists and is used by the MCS
  (and any other TM consumer).
- The decoder covers all services the MCS currently handles (at least S1, S3,
  S5, S12) without regression.
- Tests exercise the unified decoder against representative packets.

## Affected areas

- `packages/smo-common/src/smo_common/protocol/pus_services.py`
- `packages/smo-common/src/smo_common/protocol/__init__.py`
- `packages/smo-mcs/src/smo_mcs/tm_processor.py`

## Related

- Defect #6 (no parameter-watch widget / no S20 client) — unifying on a parser
  that already includes S20 would help close that gap.
- Defect #21 covers the broader set of orphaned smo-common library surface
  (config loaders, parameter registry, duplicate orbit helpers).

# 116 — nineteen procedure files were written against an obsolete func_id scheme

**Status:** Fixed

## Summary

Defect **#45** found two procedure files whose S8 function ids belonged to a
superseded numbering scheme. The Phase E audit found that this was not two
files: **19 procedure files** carried the stale scheme wholesale. A student
following any of them sent commands that reached the wrong subsystem, or no
handler at all, and — before **#87** — got a success indication either way.

## Root cause

The S8 func_id allocation was renumbered at some point without the procedure
library being migrated. Nothing checked, because a procedure is markdown or
YAML: the func_id in a `**TC:**` line is prose to every tool that reads it.
`tools/procedure_audit.py` existed but did not cross-check func_ids against the
catalogue, and no test read procedures at all.

The consequences follow the same shape as **#45**: an imaging step in the old
scheme lands in the EPS block, where the func either does not exist (silently
dropped by the router) or is a real EPS function that quietly does something
else. Under the old dispatcher a dropped func produced S1.1 acceptance and
S1.7 execution-complete, so the procedure's own verification steps were the
only thing that could catch it — and those were written to match the same wrong
numbers.

## Fix

* All 19 files migrated to the current scheme, with each command re-derived
  from `tc_catalog.yaml` rather than hand-edited;
* the audit tool gained a func_id cross-check against the catalogue;
* the check is a **CI gate**, not an advisory report.

Three related content errors found by the same sweep were corrected with it: a
stale `COM-101…COM-104` procedure numbering scheme, COM-002's wrong parameter
ids, and COM-010's cool-down profile — which described a 30-minute descent
through −20 °C to a −15 °C setpoint, an artefact of the pre-Phase-B thermal bug
(**#101**).

## Tests

`tests/test_content/test_procedure_func_ids.py`

* `test_no_procedure_contradicts_the_catalogue_func_ids` — the gate, over every
  shipped procedure;
* `test_the_cross_check_actually_fires` — writes a procedure with a wrong
  func_id into a temporary tree and asserts the audit flags it, so the gate
  cannot rot into a no-op;
* `test_payload_procedures_use_real_commands_not_phantom_parameters`.

`tests/test_content/test_procedure_audit_gate.py::test_the_audit_still_scans_every_procedure`
prevents a file from silently dropping out of the audit's scope.

## Files

* 19 files under `configs/eosat1/procedures/` (commissioning, LEOP,
  contingency)
* `tools/procedure_audit.py`
* `configs/eosat1/manual/09_command_reference.md`

## Related

* **#45** — the two files found earlier; same defect, smaller sample.
* **#83** — the four divergent procedure representations, which is why a
  procedure's commands were not machine-checkable before.
* **#118** — the other systemic annotation error the same audit found.
* **#131** — the audit's own false-positive problem, which had been hiding
  findings like these.

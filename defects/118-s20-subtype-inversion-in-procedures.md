# 118 — 79 procedure steps said "read the parameter" and wrote it

**Status:** Fixed

## Summary

Across ten payload procedure files, **79 S20 annotations** told the operator
they were performing a parameter *read* while the command in the same step
performed a parameter *write*. The card said "GET_PARAM (Subtype 1)"; the
spacecraft set the parameter.

## Root cause

PUS-C Service 20 splits reading and writing by subtype:

| Subtype | Meaning |
|---|---|
| 20,1 | report parameter values (request) |
| 20,2 | parameter values report (the answer) |
| 20,3 | **set** parameter values |

The procedure library annotates each step with a human-readable service and
subtype next to the command, e.g.
`**TC:** SET_PARAM(0x0637, <nominal>) (Service 20, Subtype 3)`. In ten payload
files the annotation had been copied from a neighbouring read step and never
corrected, so a step that set the detector gain, the integration time or a
compression setting was labelled as a query.

The consequence is not a wire error — the command bytes were right — it is an
operational one, and the worse kind: a student walking a procedure believes
they are *observing* the spacecraft when they are *changing* it. In a
configuration procedure with a GO/NO-GO after it, that inverts the meaning of
the whole step. It also made the procedures unusable as a reference for what
S20 subtypes mean, in a course where S20 is taught from them.

Nothing checked, because — as with **#116** — the annotation is prose. The
`tools/procedure_audit.py` checks covered parameter and event citations, not
service/subtype annotations.

## Fix

* All 79 annotations corrected across the ten files;
* `tools/procedure_audit.py` gained an **S20 subtype check**: it reads the
  command in the step, determines whether it is a read or a write, and flags a
  contradicting annotation;
* the check runs as a CI gate, so a new procedure with a copied annotation
  fails the build.

## Tests

`tests/test_content/test_procedure_audit_gate.py`

* `test_the_procedure_audit_is_clean` (parametrised by finding category) —
  no S20 annotation contradicts its command in any shipped procedure;
* `test_each_check_fires` — a synthetic procedure with an inverted annotation
  is flagged, so the check is proven live rather than vacuous;
* `test_no_uncategorised_finding_escapes_the_gate`.

## Files

* Ten files under `configs/eosat1/procedures/` (payload commissioning,
  calibration and contingency)
* `tools/procedure_audit.py`
* `docs/PROCEDURE_SCHEMA.md`

## Related

* **#116** — the same audit sweep, the other systemic annotation defect.
* **#83** — the procedure representation work that made procedures machine
  readable enough to check this at all.

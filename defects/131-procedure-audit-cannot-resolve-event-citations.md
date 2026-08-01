# 131 — the procedure audit reported nine findings that were not findings

**Status:** Fixed

## Summary

`tools/procedure_audit.py` could not tell an **event** id from a **parameter**
id, because the two share the hex space. Every procedure that cited an event
produced a `PARAM_UNKNOWN` finding. After Phase D added the propulsion events
(`0x0800`–), the audit emitted **nine** false findings — enough noise to make
the report unusable as a gate, which is how the real findings behind **#116**
and **#118** had been sitting in it unread.

## Root cause

A procedure step cites identifiers in prose:

```
**Verify:** event 0x0811 PROP_SPEED_CLAMPED within 2 s
**Verify:** payload.fpa_ready (0x0610) = 1
```

The audit extracted every `0x….` token and looked it up in
`configs/eosat1/telemetry/parameters.yaml`. An event id is not in there, so it
was reported as an unknown parameter. The two namespaces genuinely overlap —
`0x0111` is both an event (`EPS_SAFE_MODE_ENTRY`) and inside the EPS parameter
block — so the id alone cannot disambiguate; the surrounding citation has to be
read.

A tool that reports nine false positives is not a slightly noisy tool, it is a
tool nobody runs. That is the operational cost here: the audit *had* been
finding the stale func_id scheme and the S20 inversions, and its output was
being discounted wholesale.

## Fix

* The audit resolves a citation by **context** — a token cited as an event is
  looked up in `event_catalog.yaml`, one cited as a parameter in
  `parameters.yaml` — and reports a genuinely unresolvable citation with the
  namespace it tried.
* Findings are **categorised**, and the audit is a **CI gate** rather than an
  advisory report, with an allowlist that must not contain stale entries.
* The same pass added the func_id cross-check (**#116**) and the S20 subtype
  check (**#118**).

## Tests

`tests/test_content/test_procedure_audit_gate.py`

* `test_the_procedure_audit_is_clean` (parametrised by finding category) —
  zero findings across the shipped library;
* `test_a_correct_event_citation_is_not_a_finding` — the false-positive case,
  as a regression;
* `test_each_check_fires` — every check is proven to fire on a synthetic bad
  procedure, so a clean report cannot mean a dead checker;
* `test_the_audit_still_scans_every_procedure`,
  `test_the_allowlist_has_no_stale_entries`,
  `test_no_uncategorised_finding_escapes_the_gate`.

## Files

* `tools/procedure_audit.py`
* `configs/eosat1/events/event_catalog.yaml` (Phase D propulsion events)

## Related

* **#116**, **#118** — the two real defects the noise had been hiding.

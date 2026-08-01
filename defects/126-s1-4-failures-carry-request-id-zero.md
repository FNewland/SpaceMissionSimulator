# 126 — execution-start failures could not be attributed to a command

**Status:** Fixed

## Summary

Every one of the **17** S1.4 "execution start failed" reports in the dispatcher
was generated with a literal `request_id = 0`. The MCS matches a verification
report to a command row by request id, so none of these rejections ever
attached to the command that caused them: the command row stayed at **ACCEPTED**
— green, apparently fine — while the real outcome sat in the raw packet log
against a command that does not exist.

## Root cause

`service_dispatch.generate_s1_exec_fail(request_id, error_code, …)` takes the
id as its first argument, and all 17 call sites passed `0`:

```
$ git grep -n "generate_s1_exec_fail" 7ccf41e -- .../service_dispatch.py
334, 469, 473, 478, 487, 495, 509, 513, 518, 525, 540, 544, 547, 554, 1239, 1721  (+ the definition at 97)
```

These are the S3 (housekeeping structure), S6 (memory), S12 (monitoring) and
S19 (event-action) refusals — precisely the services where a command is
*rejected before it starts* because its arguments name something that does not
exist: an undefined SID, an unmapped memory address, an unknown monitoring
definition, a bad trigger kind.

The sequence count that identifies the command is in the packet header the
dispatcher is holding when it builds the failure; it was simply never threaded
down to the reporting call. So defect **#87**'s reason text and **#88**'s error
codes both arrived correctly on the wire and landed on nothing an operator was
looking at.

The verification log's *worst* state is the operationally dangerous one: a
command that stops at ACCEPTED reads as "in progress", and a student waits for
a completion that will never come rather than diagnosing a rejection.

## Fix

* The packet's sequence count is carried into the dispatch context and passed
  to every `generate_s1_exec_fail` site, so all 17 reports name their command.
* The MCS matches the report to the row and turns it red with the code and
  reason from **#87**/**#88**.
* A source-level test forbids a literal zero at these sites.

## Tests

`tests/test_simulator/test_dispatch_result.py`

* `test_exec_start_failure_carries_the_sequence_count`;
* `test_s6_memory_refusal_carries_the_sequence_count`;
* `test_s19_bad_trigger_kind_carries_the_sequence_count`;
* `test_no_exec_fail_site_still_passes_a_literal_zero` — an `ast` walk over
  `service_dispatch.py`, so a new site added tomorrow cannot reintroduce it.

`tests/test_mcs/test_verification_failure_display.py` (from **#87**) confirms
the row now transitions to rejected rather than remaining ACCEPTED.

## Files

* `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
* `packages/smo-mcs/src/smo_mcs/server.py`

## Related

* **#87** — the reason text these reports carried to nowhere.
* **#88** — the error codes, same.
* **#125** — landed together; that defect is the verdict's integrity, this one
  is its address.

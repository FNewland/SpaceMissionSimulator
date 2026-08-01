# 125 — a nested command could overwrite the operator's command verdict

**Status:** Fixed

## Summary

`dispatch()` reported failures through a **side channel** — two attributes on
the dispatcher, set by the failing handler and read afterwards by the engine.
Command execution is re-entrant: an S11 time-tagged release, an S19
event-action, an S18 OBCP step and a propulsion sequence all dispatch commands
from inside a dispatch. Whichever one finished last owned the side channel, so
an inner command's outcome could be reported as the outer command's verdict.

## Root cause

`service_dispatch.py:40-41`:

```python
self._last_error = ""
self._last_error_code = 0
```

set in `_make_error_response` (`service_dispatch.py:801-802`) and read by the
engine after the fact (`engine.py:1601-1615`):

```python
dispatcher._last_error = None
dispatcher._last_error_code = 0
...
if getattr(dispatcher, '_last_error', None):
    exec_reason = str(getattr(dispatcher, '_last_error', ''))
    exec_code   = getattr(dispatcher, '_last_error_code', 0x0001)
```

Clear, dispatch, read. That is correct exactly once. With nesting:

* an operator's command succeeds, but triggers an S19 rule whose action fails →
  the operator's command is reported as **failed**, with a reason describing an
  autonomous action they did not issue;
* an operator's command fails, and its failure triggers an S19 rule that
  succeeds → the failure is **cleared**, and the operator sees success;
* an S11 release firing inside the same tick as a manual command could claim
  either verdict.

This is not hypothetical: **#87** made the reason text visible on the S1.8, so
the wrong reason is now shown to the operator in words. The side channel was
the reason that defect's fix could not be trusted under nesting.

## Fix

* `dispatch()` returns a **`DispatchResult`** object — packets, success, error
  code, reason, and the kind of failure (acceptance / execution-start /
  execution) — carried back with the packets rather than left on the
  dispatcher.
* `_last_error` and `_last_error_code` are **deleted**; a nested dispatch
  returns its own result to its own caller and cannot touch anyone else's.
* An S19 action failure is recorded as an FDIR/autonomy outcome, not as the
  operator's verdict.

## Tests

`tests/test_simulator/test_dispatch_result.py`

* `test_dispatch_returns_a_dispatch_result`,
  `test_failure_travels_back_with_the_packets`,
  `test_exec_start_failure_is_a_distinct_kind`;
* `test_nested_dispatch_cannot_clobber_the_outer_result` and
  `test_outer_failure_survives_a_successful_nested_dispatch` — both directions
  of the defect;
* `test_s19_action_failure_does_not_become_the_operators_verdict`;
* `test_side_channels_are_gone` — a source-level guard, so the attribute cannot
  be reintroduced.

## Files

* `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
* `packages/smo-simulator/src/smo_simulator/engine.py`

## Related

* **#87**, **#88** — the failure-reason plumbing this defect could corrupt.
* **#126** — the other half of the verification-report repair, landed with it.
* **#48** — S18 OBCP, one of the four nesting sources.

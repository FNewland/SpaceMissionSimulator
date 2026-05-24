## Summary

The instructor UI's **"CLEAR ALL FAILURES"** button does nothing. Its handler
`clearAllFailures()` (`instructor/static/index.html:1979-1984`) sends
`{type:'failure_clear_all'}` over both transports, and both reject or drop it:

- **HTTP path:** `failure_clear_all` is **not** in the instructor
  `allowed_types` set (`instructor/app.py:87-91`), so `handle_command` returns
  **HTTP 403 "denied"** (`app.py:104-109`).
- **WebSocket path:** `handle_ws` (`app.py:46-63`) places the command on the
  instructor queue with no allowlist, but the engine's `_handle_instructor_cmd`
  (`engine.py:1525-1608`) has **no `failure_clear_all` branch**, so it is
  silently dropped.

Note the engine *can* clear all failures — its `failure_clear` branch
(`engine.py:1554-1559`) clears everything when no `failure_id` is supplied —
but the UI never sends `failure_clear` without an id for the "clear all"
action, so that capability is unreachable from this button. Per-failure CLEAR
still works, so a manual workaround exists.

## Severity

**Major** — a labelled control on the instructor panel silently fails. During
a training run an instructor expects one click to reset all injected failures;
instead nothing happens and they must clear each failure individually.

## Requirements for the fix

1. Clicking "CLEAR ALL FAILURES" must clear every active injected failure.
2. The command must be accepted by whichever transport the UI uses (no 403,
   no silent drop).

## Suggested implementation

Lowest-risk option: change the UI to send `{type:'failure_clear'}` with no
`failure_id`, which the engine already handles as "clear all". Alternatively,
add `failure_clear_all` to `allowed_types` in `instructor/app.py` **and** a
matching branch in `_handle_instructor_cmd` that calls the failure manager's
clear-all path.

## Acceptance criteria

- With two or more failures injected, clicking "CLEAR ALL FAILURES" removes all
  of them and the failures list returns to empty.
- No 403 is returned and no command is dropped.
- A test injects multiple failures, issues the clear-all action, and asserts
  zero active failures remain.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/instructor/static/index.html` (`clearAllFailures`)
- `packages/smo-simulator/src/smo_simulator/instructor/app.py` (allowlist)
- `packages/smo-simulator/src/smo_simulator/engine.py` (`_handle_instructor_cmd`)

## Related

- Same root cause as defects #9 and #10: the instructor UI emits a command
  `type` the engine's dispatcher has no branch for. A general fix is to make
  the engine log (rather than silently ignore) unknown instructor command
  types so this class of defect surfaces immediately.

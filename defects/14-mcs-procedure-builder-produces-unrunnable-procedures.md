## Summary

The MCS Procedure Builder lets an operator author and save a custom procedure,
but a saved procedure can never be executed, and even if it could, its
`wait` / `tlm_check` / `go_nogo` steps would be silently skipped. Two
independent gaps:

**(a) Saved custom procedures have no execution path.** Save works
(`pbSave()`, `index.html:5649` → `POST /api/procedure/save` →
`_handle_proc_save`, `server.py:1486`, writes `procedures/custom/<name>.yaml`),
and `pbLoadCustom()` (`index.html:5667`) lists them, but it only repopulates
the *builder* (`pbSteps = procs[idx].steps`, `index.html:5678`). The runner's
load control `procLoad()` (`index.html:5445`) posts only `{name, step_by_step}`
with no `steps`, and `_handle_proc_load` (`server.py:1327-1346`) resolves
`name` exclusively against `activity_types.yaml`. The runner's procedure
dropdown is populated only from `/api/procedure/activity-types`
(`procInit()`, `index.html:5411-5420`). Custom procedures are therefore never
offered to, or loadable by, the runner.

**(b) Builder step schema diverges from what the runner understands.** The
builder emits type-tagged steps (`pbAddStep`, `index.html:5614-5625`):
`{type:'wait', seconds}`, `{type:'tlm_check', parameter, condition, value,
timeout_s}`, `{type:'go_nogo', label}`, `{type:'command', service, subtype,
data_hex}`. But the runner's `_execute_step` (`procedure_runner.py:257-273`)
dispatches only on the keys `wait_s`, `wait_for`, or `service`; anything else
hits `self._log("Unknown step type"); return True` (line 272-273) — i.e. it is
counted as **PASSED**. So a builder `wait` step (`seconds`, not `wait_s`) is
skipped, a `tlm_check` (no `service`/`wait_for`) is skipped **without
verifying**, and a `go_nogo` is skipped. The runner's `_values_match`
(`procedure_runner.py:330`) is equality-only, so the builder's `>`/`<`
conditions could not work even if the keys matched. (The canned
`activity_types.yaml` procedures use `wait_s`/`wait_for`/`service` directly and
run fine — the divergence is specific to builder output.)

## Severity

**Major** — operators can build and save procedures that are unrunnable, and
the step types most relevant to safe operations (timed waits, telemetry
verification, go/no-go gates) fail **open** (reported as passed without
executing). A procedure that appears to verify a precondition but silently
skips it is a safety-relevant illusion.

## Requirements for the fix

1. A saved custom procedure must be loadable into the runner and executable.
2. Builder-authored `wait`, `tlm_check`, and `go_nogo` steps must execute with
   their intended semantics (real delay, real telemetry check with comparison
   operators, real go/no-go gate).

## Suggested implementation

- Merge `/api/procedure/custom` into the runner's load source so custom
  procedures appear in the dropdown, and pass their `steps` to
  `/api/procedure/load`; or have `pbSave` optionally load directly into the
  runner.
- Normalise builder output to the runner schema (`seconds`→`wait_s`;
  `tlm_check`→`wait_for` with operator support; add a `go_nogo` handler), or
  teach the runner to translate `type`-tagged steps. Add comparison-operator
  support (`>`,`<`,`>=`,`<=`,`!=`) to `_wait_for_condition`/`_values_match`.
- Make `_execute_step` **fail closed** on an unrecognised step type (log an
  error and mark the step failed) rather than returning `True`.

## Acceptance criteria

- A procedure built in the UI, saved, then selected in the runner executes all
  step types correctly.
- A `wait` step actually delays; a `tlm_check` actually blocks/verifies against
  live telemetry using the chosen operator; a `go_nogo` actually gates.
- An unknown step type is reported as a failure, not a pass.
- Tests cover each builder step type round-tripping through the runner.

## Affected areas

- `packages/smo-mcs/src/smo_mcs/static/index.html` (`pbAddStep`, `pbSave`, `pbLoadCustom`, `procLoad`, `procInit`)
- `packages/smo-mcs/src/smo_mcs/procedure_runner.py` (`_execute_step`, `_wait_for_condition`, `_values_match`)
- `packages/smo-mcs/src/smo_mcs/server.py` (`_handle_proc_load`, custom-procedure resolution)

## Related

- Procedure runner controls for canned `activity_types.yaml` procedures are
  fully wired and work; this defect is specific to the builder→runner path.

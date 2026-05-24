## Summary

The shipped ADVANCED scenario `configs/eosat1/scenarios/aocs_wheel_failure.yaml`
injects a failure type the AOCS model does not handle, so running it produces a
completely healthy spacecraft.

- The scenario step injects `subsystem: aocs`, `failure: wheel_failure`
  (`aocs_wheel_failure.yaml:12-13`).
- The engine passes the name through verbatim: `engine.py:1762`
  `model.inject_failure(failure, magnitude, **extra)` (no remapping).
- AOCS `inject_failure` (`models/aocs_basic.py:1544-1613`) has branches for
  `rw_bearing`, `rw_seizure`, `gyro_bias`, `st_blind`, `st_failure`,
  `css_failure`, `mag_failure`, `mtq_failure`, `mag_a_fail`, `mag_b_fail`,
  `css_head_fail`, `multi_wheel_failure` — but **no `wheel_failure`**, and there
  is no `else`/default branch. The call falls through with zero effect.

The near-identical `aocs_actuator_stuck.yaml` correctly uses `rw_seizure`, so
this looks like simple naming drift. Net effect: none of the scenario's three
expected operator responses (detect / isolate / recover RW3) can be exercised
because the wheel never degrades.

## Severity

**Major** — an entire shipped training scenario is inert. A trainee runs it and
sees a nominal spacecraft, which is worse than no scenario (it teaches that the
failure "didn't happen").

## Requirements for the fix

1. Running `aocs_wheel_failure.yaml` must actually degrade reaction wheel 3 in a
   way consistent with the scenario briefing.

## Suggested implementation

- Simplest: change the scenario's `failure:` to a handled type (`rw_seizure` or
  `rw_bearing`) on wheel 3.
- Or add a `wheel_failure` alias branch in `AOCSBasicModel.inject_failure`.
- Recommended belt-and-braces: add an `else` branch to `inject_failure` that
  logs a warning for an unrecognised failure name, so future naming drift is
  surfaced immediately instead of silently no-oping (same class of latent bug as
  the engine's unhandled instructor commands).

## Acceptance criteria

- Running the scenario degrades RW3 (speed/temperature signature per defect #24).
- A test asserts that injecting the scenario's failure changes AOCS state.
- An unrecognised failure name logs a warning rather than silently passing.

## Affected areas

- `configs/eosat1/scenarios/aocs_wheel_failure.yaml`
- `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py` (`inject_failure`)

## Related

- Defect #24 (RW thermal signature) — together these make the wheel scenarios
  trainable.
- Same "silently dropped because no handler branch" family as defects #9/#10/#12.

## Summary

The reaction-wheel over-temperature failure signature — the thing two ADVANCED
training scenarios ask the operator to detect — cannot occur, for two
independent reasons:

1. **RW temperatures are computed but unobservable.** The model writes wheel
   temps every tick (`models/aocs_basic.py:1018`,
   `shared_params[rw{i}_temp / 0x0218+i] = s.rw_temp[i]`; thermal model at
   `:889-894`), but params `0x0218`–`0x021B` are in **no HK SID**. SID 2 (AOCS)
   in `configs/eosat1/telemetry/hk_structures.yaml:63-143` carries RW speeds
   (0x0207–0x020A) and currents (0x0250–0x0253) but not the temps. Grep for
   `0x0218|0x0219|0x021A|0x021B` in `telemetry/hk_structures.yaml` returns
   nothing. HK packets only carry SID-listed params (`tm_builder.py:58-62`), so
   RW temperature never downlinks — even though `mcs/limits.yaml:26` defines
   red/yellow limits for 0x0218 and `mcs/role_analysis/aocs_role.md` documents an
   FDIR rule "rw1_temp > 65 C → disable_rw1".

2. **A seized wheel never heats up.** In `_tick_wheels`, an inactive wheel
   short-circuits before the thermal update (`models/aocs_basic.py:861-866`):
   `if not s.active_wheels[i]: rw_speed*= ...; rw_current=0; continue`. Both
   `rw_seizure` and high-magnitude `rw_bearing` set `active_wheels[w]=False`
   (`:1551-1558`), so the seized wheel's `rw_temp[i]` is never touched again
   (it flatlines — no friction heat). A real seized bearing produces friction
   heat and a rising temperature.

Scenarios that depend on this signature: `scenarios/aocs_actuator_stuck.yaml:23`
("Detect RW-1 temperature rising due to bearing seizure") and
`scenarios/aocs_wheel_failure.yaml:19` ("…temperature rise"). Note
`aocs_actuator_stuck.yaml` also briefs the wheel as "retaining its current
speed", but the model forces speed to zero — a second contradiction.

This was previously acknowledged in `defects/reviews/aocs_fixed.md` as a deferred
out-of-scope item ("wheel bearing health"); it remains broken in current code.

## Severity

**Major** — two flagship failure/recovery scenarios are partly un-trainable: the
rising-temperature detection cue can neither be displayed (not in HK) nor
generated (physics flatlines a seized wheel).

## Requirements for the fix

1. RW temperatures must be observable in periodic HK.
2. A seized/degraded wheel must produce a rising temperature consistent with the
   scenario briefings (and, for the "stuck" case, optionally retain speed).

## Suggested implementation

- Add `0x0218`–`0x021B` to SID 2 in `hk_structures.yaml`.
- Model a stuck/seized wheel as a distinct state that adds bearing-friction
  heating (and, for `aocs_actuator_stuck`, retains speed) instead of reusing the
  generic `active_wheels=False` coast-down path that skips the thermal update.

## Acceptance criteria

- After a `rw_seizure`/`rw_bearing` injection, the affected wheel's temperature
  rises and is visible in downlinked HK.
- A test injects the failure and asserts both a temperature rise and HK
  observability; the over-temp limit/FDIR rule can fire.

## Affected areas

- `configs/eosat1/telemetry/hk_structures.yaml` (SID 2)
- `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py` (`_tick_wheels` thermal path)

## Related

- Defect #23 (events never reach operator) — the over-temp event also needs that
  fix to be seen as an S5 event rather than only via HK limit-watch.
- Defect #25 (`aocs_wheel_failure` injects a no-op) — same scenario, separate bug.

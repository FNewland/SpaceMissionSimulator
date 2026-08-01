# 132 — the commanded FPA setpoint did not survive a breakpoint load

**Status:** Fixed

## Summary

After the Phase A state-registry work (**#93**), one operator-commanded value
still failed to round-trip: the TCS cooler setpoint. An instructor could
command −20 °C, save a breakpoint, reload it, and the cooler quietly reverted to
the configured default — with the console still showing the commanded value
until the next readback.

## Root cause

`models/tcs_basic.py:722-727` (baseline):

```python
def get_state(self) -> dict[str, Any]:
    import dataclasses; return dataclasses.asdict(self._state)

def set_state(self, state: dict[str, Any]) -> None:
    for k, v in state.items():
        if hasattr(self._state, k): setattr(self._state, k, v)
```

The state is "whatever is in the `TCSState` dataclass". The commanded cooler
setpoint is **not** in that dataclass — it is an instance attribute on the
model, `self._fpa_cooler_target`, initialised from configuration at
`tcs_basic.py:204` and written by the `TCS_COOLER_SETPOINT` command handler.
`dataclasses.asdict(self._state)` cannot see it, so it was never saved; on
load, the model kept the value it had been constructed with.

This is a **field-level blind spot in the completeness check**, not a missing
component: the TCS model *was* registered, *did* have `get_state`/`set_state`,
and the registry test passed. What the test could not see is that a model's own
`get_state` omits an attribute the model owns. Every model with configuration
loaded into instance attributes and then mutated by a command had the same
exposure; the FPA setpoint was the one an exercise actually depends on.

## Fix

`get_state` explicitly includes the model-owned attributes alongside the
dataclass, and `set_state` restores them before the generic loop
(`tcs_basic.py:910-922`):

```python
d["_fpa_cooler_target"] = self._fpa_cooler_target
...
if "_fpa_cooler_target" in state:
    self._fpa_cooler_target = float(state["_fpa_cooler_target"])
_restore(..., skip=("_rng_state", "_fpa_cooler_target"))
```

The round-trip test was strengthened from "the component has state methods" to
"a **commanded** value differs from its configured default after a load", which
is the property that was actually broken.

## Tests

* `tests/test_simulator/test_breakpoint_round_trip.py::test_save_perturb_load_deep_equals`
  — the perturbation now includes commanded setpoints, so an attribute omitted
  from `get_state` fails;
* `tests/test_simulator/test_thermal_phase_b.py::TestSingleFPANode` runs against
  a restored engine, so a reverted setpoint shows up as a wrong temperature;
* `tests/test_simulator/test_aocs_dynamics.py::TestStateRoundTrip::test_state_roundtrip_preserves_new_fields`
  applies the same discipline to the Phase C attitude state.

## Files

* `packages/smo-simulator/src/smo_simulator/models/tcs_basic.py`
* `tests/test_simulator/test_breakpoint_round_trip.py`

## Related

* **#93** — the state registry; this is the field-level gap it did not close.
* **#101** — the FPA node whose setpoint this is.

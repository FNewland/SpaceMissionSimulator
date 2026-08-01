# 110 — every wheel and magnetorquer was disabled at separation and no procedure turned them back on

**Status:** Fixed

## Summary

The engine's pre-separation initialisation disabled all four reaction wheels
and the magnetorquers, and **no shipped procedure ever re-enabled them**.
Commissioning appeared to work only because the actuators were decorative: with
no attitude dynamics (**#108**), a disabled wheel and an enabled wheel produced
the same pointing. The moment the dynamics became real, the spacecraft could
not control at all. Compounding it, `aocs.wheels_enabled` (`0x0260`) was never
written, so the parameter read 0 from t = 0 and latched an FDIR SAFE-mode
entry on a freshly constructed engine.

## Root cause

`engine.py:521-528` (repeated at `engine.py:2643-2649` on the reset path):

```python
if hasattr(s, 'rw_enabled'):
    s.rw_enabled = [False] * 4
...
s.mtq_enabled = False
```

That is correct pre-separation behaviour — actuators are inhibited until the
separation switch. What is missing is the other end. A grep of
`configs/eosat1/procedures/` finds **no** `AOCS_ENABLE_WHEEL` /
`AOCS_ENABLE_MTQ` step in LEOP or in commissioning, so on the shipped timeline
the actuators were never enabled at any point in the mission. The AOCS
commissioning procedure verified mode transitions and pointing error against a
model that produced both without any actuator, so it passed.

The second half is a parameter chain gap of the class in **#107**:
`aocs.wheels_enabled` (`0x0260`) is declared, in a HK SID, and consumed by an
FDIR rule — and written by nothing. It read 0.0 = "wheels disabled" on the
first tick of every run, which is exactly what the rule is designed to escalate
on, so a fresh engine latched **FDIR SAFE at t = 0** before an operator had
done anything.

## Fix

* Actuator inhibit is still applied pre-separation, and **release is part of
  the separation sequence**, so the state machine that inhibits is the one that
  clears.
* LEOP and AOCS commissioning procedures gained explicit enable steps with
  verification, so the operator sees the transition rather than inheriting it.
* `aocs.wheels_enabled` is published every tick from the actual per-wheel
  enable state.
* The FDIR rule that consumes it evaluates against a real value and, with
  **#112**'s persistence, cannot latch on a single first-tick sample.

## Tests

`tests/test_simulator/test_aocs_dynamics.py::TestAutonomy::test_fresh_engine_not_latched_safe`
— the t = 0 latch, stated directly.

`tests/test_simulator/test_aocs_dynamics.py::TestPointingPerformance` and
`TestFineGating::test_fine_refused_with_three_wheels` cannot pass with disabled
actuators, so the enable path is exercised by the whole pointing suite rather
than by a single test.

`tests/test_commissioning/test_leop_sequence.py` and
`tests/test_commissioning_sequence.py` run the shipped procedures end to end
and now fail if the actuator-enable steps are removed.

## Files

* `packages/smo-simulator/src/smo_simulator/engine.py`
* `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py`
* `configs/eosat1/procedures/leop/*.md`,
  `configs/eosat1/procedures/commissioning/aocs_actuator_checkout.md`

## Related

* **#108** — why "commissioning passed" meant nothing.
* **#107** — the never-written-parameter class this defect's second half
  belongs to.
* **#112** — the missing persistence that turned a first-tick sample into a
  latched safe mode.

# 101 — the FPA cooler could not reach its setpoint, and there were two of it

**Status:** Fixed

## Summary

The focal plane assembly is the thermal exercise: cool it to its setpoint,
watch the readiness flag, take an image. The TCS model **could not reach any
setpoint**, because the setpoint was used as an additive offset to the ambient
environment rather than as a target. And there were two independent FPA
integrators, in two subsystems, with two unlinked cooler flags, sitting 26.4 °C
apart on the exact path the shipped COM-010 procedure walks students through.

## Root cause

`models/tcs_basic.py:492-493` (baseline):

```python
cool = (self._fpa_cooler_target - 20.0) if (s.cooler_fpa and not s.cooler_failed) else 0.0
s.temp_fpa += (env_int + cool - s.temp_fpa) / self._TAU["fpa"] * dt + random.gauss(0, 0.02)
```

The node asymptotes to `env_int + cool`, i.e. to

```
ambient + (setpoint − 20)
```

With the shipped `env_int` and a −15 °C setpoint that is **ambient − 35 K**,
not −15 °C. Commanding a *different* setpoint moved the asymptote by the same
delta, so the model responded to the command — plausibly, and always to the
wrong temperature. A student following the procedure watched the FPA sail past
the setpoint and keep going.

**The second integrator.** `payload_basic` carries its own `fpa_temp` (0x0601)
with its own `_fpa_target = -15.0`, `_fpa_ambient`, `_tau_cool`, `_tau_warm`
and its own cooler state, and publishes it as a *different parameter* from
`tcs.temp_fpa` (0x0408). Nothing linked them:

* `TCS_COOLER_SETPOINT` (func 28) moved the TCS node;
* the payload device gate 0x0601 moved the payload node;
* the two disagreed by 26.4 °C in the COM-010 sequence;
* the payload's readiness logic used one and the operator's thermal display
  showed the other.

Which of the two an S12 monitor, an event threshold or a procedure GO/NO-GO
step referred to was, in practice, arbitrary — and that arbitrariness is what
**#133**'s five-way envelope disagreement was made of.

## Fix

* **One FPA node.** `tcs_basic` owns the thermal node and integrates toward the
  commanded setpoint:

  ```python
  fpa_target = max(self._fpa_cooler_target, <achievable floor>)
  ```

  and the payload reads `tcs.temp_fpa` instead of integrating its own. The
  0x0601 parameter is retained and now carries the same physical quantity.
* **One cooler state.** The cooler flag, the EPS billing for it and the device
  gate are the same state (see **#103**): line off ⇒ no cooling *and* no bill.
* Readiness is one rule evaluated in one place, shared by both subsystems.

## Tests

`tests/test_simulator/test_thermal_phase_b.py::TestSingleFPANode`

* `test_setpoint_reached_not_offset` — the node converges **to** the setpoint;
* `test_four_command_combos_agree_within_3c` (parametrised) — the four ways an
  operator can reach a cooled FPA all give the same temperature, which is the
  26.4 °C divergence stated as a test;
* `test_unified_cooler_state_and_billing`;
* `test_line_off_means_no_cooling_no_billing`.

`tests/test_content/test_fpa_envelope_single_source.py::test_readiness_is_the_same_rule_in_both_subsystems`
pins that the two subsystems cannot drift apart again.

## Files

* `packages/smo-simulator/src/smo_simulator/models/tcs_basic.py`
* `packages/smo-simulator/src/smo_simulator/models/payload_basic.py`
* `configs/eosat1/subsystems/tcs.yaml` — `fpa_envelope`
* `configs/eosat1/manual/03_tcs.md`,
  `configs/eosat1/procedures/commissioning/fpa_cooler_activation.md`

## Related

* **#133** — the five-file disagreement about what the FPA envelope *is*; this
  defect is why no two files could agree.
* **#31** — the earlier repair of the TCS setpoint commands, which made the
  commands live against a model that could not honour them.

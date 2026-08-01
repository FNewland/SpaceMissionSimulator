# 105 — one S12 monitor fired at t = 0 every session; another could never fire

**Status:** Fixed

## Summary

Two shipped S12 monitors had limit bands that had never been checked against
the parameter they watch. One had **inverted limits**, giving an empty
admissible set, so it violated on the first tick of every session — and via
S19 rule 3001 the spacecraft **autonomously switched the payload off** before
anyone had commanded anything. The other watched total angular momentum against
a ±90 band it could not reach in a century. The loader that read both would
have swallowed a genuinely malformed definition into a silent partial load.

## Root cause

**1. Inverted band.** `configs/eosat1/monitoring/s12_definitions.yaml:190-196`:

```yaml
- param_id: 0x0408  # tcs.temp_fpa
  name: "TCS FPA Temperature Low"
  check_type: 0     # absolute
  low_limit: -15.0
  high_limit: -20.0
  severity: WARNING
```

`low_limit > high_limit`. The admissible set `[-15, -20]` is empty, so **every**
value violates. The monitor reported a check transition on the first evaluation
of every run.

That would be noise on its own. `configs/eosat1/monitoring/s19_rules.yaml:98`
turns it into an action:

```yaml
  ea_id: 3001
  trigger: {kind: param, id: 0x0408}
  action_func_id: 20        # S8 func 20: Payload mode OFF
  enabled: true
```

so the spacecraft autonomously commanded the payload off at t = 0, every
session, before the operator's first telecommand. Students met an FDIR action
as the *first* thing that happened, with no fault to explain it.

**2. Unreachable band.** `s12_definitions.yaml:120-127`:

```yaml
- param_id: 0x025B  # aocs.total_momentum
  name: "AOCS Total Momentum Saturated"
  low_limit: -90.0
  high_limit: 90.0
```

The unit is Nms. A four-wheel pyramid of small-sat reaction wheels saturates
around 0.05 Nms; ±90 Nms is three orders of magnitude out. The monitor was
dead, so the momentum-management lesson had no autonomous backstop at all —
and after Phase C made momentum a real, dynamic quantity, it was the one
monitor that still could not fire.

**3. The loader would not have told anyone.** `monitoring_loader.py:134-136`:

```python
except Exception as e:
    logger.error("Failed to load S12 definitions from %s: %s", s12_file, e)
    return definitions          # ← whatever had been parsed so far
```

Any fault in the file returns the **partial** dictionary, logs to a file nobody
reads during a class, and the simulator boots with an arbitrary subset of its
monitoring. That is the mechanism of defect **#69** ("S12 loses 4 of 38
definitions") recurring by a different route.

## Fix

* `TCS FPA Temperature Low` corrected to a real band, derived from the
  single-sourced FPA envelope (**#133**) rather than hand-written.
* The momentum monitor's band is derived from the configured wheel momentum
  capacity, so it tracks the hardware sheet.
* **The loader refuses inverted limits** and raises rather than returning a
  partial set: a malformed monitoring file is now a boot failure with a named
  rule, not a silently reduced spacecraft.

## Tests

`tests/test_simulator/test_thermal_phase_b.py::TestMonitors`

* `test_loader_refuses_inverted_limits` — writes a bad definition and asserts
  the loader rejects it, so the class of defect cannot return;
* `test_shipped_definitions_have_no_inverted_bands` — a sweep over the whole
  shipped file;
* `test_clean_nominal_run_violates_no_thermal_monitor` — no monitor fires in a
  clean run, which is what "fires at t = 0 every session" violated.

`tests/test_content/test_parameter_chain.py::test_every_s12_monitored_parameter_is_reachable`
and `tests/test_simulator/test_aocs_dynamics.py::TestAutonomy::test_momentum_saturation_triggers_autonomous_desat`
pin that the momentum rule now fires on a real saturation.

## Files

* `configs/eosat1/monitoring/s12_definitions.yaml`
* `configs/eosat1/monitoring/s19_rules.yaml`
* `packages/smo-simulator/src/smo_simulator/monitoring_loader.py`

## Related

* **#69**, **#70** — earlier S12/S19 defects; the silent partial load is the
  mechanism that let all three ship.
* **#100** — an alarm that fires at t = 0 every session conveys nothing, and
  latches out the case it exists for.

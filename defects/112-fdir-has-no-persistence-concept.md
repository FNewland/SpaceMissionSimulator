# 112 — FDIR and S12 had no persistence, so every transient was a fault

**Status:** Fixed

## Summary

Neither the S12 monitoring service nor the FDIR rules had any concept of
**persistence** — a confirmation time or a consecutive-sample count before a
limit violation is declared a fault. Every rule was a single-sample comparison.
While the models were smooth this was harmless; the moment Phase B and Phase C
introduced real transients — a slew, a heater cycle, an eclipse entry, a
detumble overshoot — the same rules became spurious, and a spacecraft that
safes itself on a 1-second overshoot is worse than one with no autonomy.

## Root cause

The S12 definition schema is `param_id / check_type / low_limit / high_limit /
severity / description` — there is no field for a confirmation time, and the
evaluator declares a violation on the first sample outside the band. The FDIR
cascade schema (`configs/eosat1/fdir/fault_propagation.yaml`) has a
`threshold` and a per-cascade `delay_s`, but the delay is applied to the
*consequence* after the fault has already been declared, not to the
*detection*. There was no way to express "attitude error above 1° for 5
consecutive seconds".

This is the same class of design gap as **#111**: the rule schema could not
express what an operations engineer would write in a real FDIR table, so the
shipped configuration expressed something weaker and the difference only
appeared once the plant was realistic. Concretely, after Phase C:

* a commanded 30° slew crosses the attitude-error limit on the way to the
  target — the manoeuvre itself looked like a fault;
* wheel speed crosses its overspeed band during momentum build-up before
  desaturation dumps it;
* eclipse entry steps the bus voltage down within one tick.

## Fix

* Persistence is a first-class field on both rule sets: S12 definitions carry a
  confirmation time, and FDIR faults carry a persistence filter, so a
  violation must hold for the declared duration before it is declared.
* The detection filter is separate from the existing cascade `delay_s`, which
  keeps its meaning (how long after the fault the consequence applies).
* The shipped rules were re-tuned against the real transients, with the
  confirmation times documented in `configs/eosat1/manual/08_fdir.md` next to
  the thresholds.
* Recovery is symmetric: a fault clears when the condition has been back inside
  the band for its confirmation time, and pending cascades are dropped when the
  fault clears before they fire.

## Tests

`tests/test_simulator/test_fault_propagation.py`

* `test_persistence_filter_suppresses_a_single_sample` — one sample outside
  the band raises nothing;
* `test_cascade_applies_after_its_declared_delay` — detection persistence and
  cascade delay are distinct and both honoured;
* `test_pending_cascades_are_dropped_when_the_fault_clears`;
* `test_escalation_delays_were_parsed` — the configured times reach the engine.

`tests/test_simulator/test_aocs_dynamics.py::TestPointingPerformance::test_slew_30deg_completes_and_resumes_nominal`
is the operational statement: a legitimate manoeuvre must not trip FDIR.

## Files

* `configs/eosat1/monitoring/s12_definitions.yaml`,
  `configs/eosat1/fdir/fault_propagation.yaml`
* `packages/smo-simulator/src/smo_simulator/monitoring_loader.py`,
  `fdir.py`
* `configs/eosat1/manual/08_fdir.md`

## Related

* **#124** — FDIR had never actually run, so no shipped rule had ever been
  exercised against a transient.
* **#105** — the two monitors whose bands were wrong; persistence does not
  rescue a band that is inverted or unreachable.

# 133 — five files carried five different answers to "how cold should the FPA be?"

**Status:** Fixed

## Summary

The focal-plane thermal envelope — the band the detector must be in to image,
and the band outside which it is unhealthy — was written down independently in
five files, and no two agreed. The textbook supplied a sixth answer. The
practical consequence: the old `0x0601` low limit turned a **legal** −20 °C
cooler setpoint into a standing red alarm, so an operator following one
document was permanently in violation of another.

## Root cause

The envelope appeared, hand-written, in:

| File | Carried |
|---|---|
| `configs/eosat1/monitoring/s12_definitions.yaml` | on-board FPA high and low monitors (one of them inverted — **#105**) |
| `configs/eosat1/mcs/limits.yaml` | ground yellow/red bands on `0x0408` and `0x0601` |
| `configs/eosat1/subsystems/tcs.yaml` / `payload.yaml` | the two models' cooler targets and warning limits |
| `configs/eosat1/planning/activity_types.yaml` | `fpa_temp_min_c` / `fpa_temp_max_c` imaging constraints and pre-conditions |
| `configs/eosat1/manual/03_tcs.md`, `06_payload.md` | the numbers taught to students |

Five hand-maintained copies of one physical fact will diverge, and these had.
Underneath the divergence were two real confusions:

1. **Two FPA nodes** (**#101**) meant "the FPA temperature" was ambiguous —
   `0x0408` and `0x0601` were different quantities, so limits written against
   one were wrong against the other.
2. **Imaging readiness and health limits were conflated.** "Cold enough to
   take a picture" and "not so cold that something is wrong" are different
   questions with different bands; writing them as one band forced every author
   to pick a compromise, and they picked different ones. That conflation is
   what the five-way disagreement was really made of.

## Fix

* The envelope is **derived from the corrected model's measured behaviour**
  (Phase B) and declared once, in `configs/eosat1/subsystems/tcs.yaml` as
  `fpa_envelope`.
* Every other consumer reads it: both models, the S12 monitors, the MCS limit
  bands, the TCS event thresholds and the planner's imaging constraints and
  pre-conditions.
* **Readiness is separated from health**: an imaging-readiness bound and a
  health band, each with its own name, so no author has to compromise.
* The decision and its rationale are recorded in `DECISIONS §3`, and the manual
  chapters quote the derived numbers.

## Tests

`tests/test_content/test_fpa_envelope_single_source.py` (17 tests) — the
single-source gate:

* `test_the_envelope_is_declared_in_tcs_yaml`,
  `test_the_envelope_is_self_consistent`;
* derivation checks per consumer — `test_mcs_limit_bands_are_derived`
  (parametrised per band), `test_s12_node_monitors_are_derived`,
  `test_s12_detector_monitor_is_derived`,
  `test_tcs_event_thresholds_are_derived`,
  `test_planner_imaging_constraints_are_derived`,
  `test_planner_preconditions_use_the_imaging_bound`,
  `test_the_models_import_the_envelope`;
* the behavioural statements — `test_a_legal_setpoint_never_violates_a_limit`
  (the standing red, as a test), `test_the_uncooled_state_never_violates_a_limit`,
  `test_readiness_requires_both_regulation_and_cold`,
  `test_readiness_is_the_same_rule_in_both_subsystems`.

## Files

* `configs/eosat1/subsystems/tcs.yaml` — `fpa_envelope` (the source)
* `configs/eosat1/subsystems/payload.yaml`,
  `configs/eosat1/monitoring/s12_definitions.yaml`,
  `configs/eosat1/mcs/limits.yaml`,
  `configs/eosat1/planning/activity_types.yaml`
* `packages/smo-simulator/src/smo_simulator/models/tcs_basic.py`,
  `payload_basic.py`
* `configs/eosat1/manual/03_tcs.md`, `06_payload.md`,
  `configs/eosat1/manual/commissioning_sequence_walkthrough.md`

## Related

* **#101** — the duplicated FPA node that made the question ambiguous.
* **#105** — the inverted S12 band, one of the five copies.
* **#120** — the ground limits that only started evaluating in this build, so
  the standing red became visible.

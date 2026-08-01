# 107 — parameters nothing ever wrote read as plausible, wrong values

**Status:** Fixed

## Summary

A parameter that is declared, packed into a housekeeping SID and rendered on a
display, but that **no code ever writes**, does not read as "missing" — it
reads as `0.0`. For an enumerated parameter, 0.0 is a meaningful value. Three
of the most-watched parameters on the console were in exactly this state, and
each of them read as a *specific, alarming, false* claim about the spacecraft.

## Root cause

| Parameter | id | Reads | Means |
|---|---|---|---|
| `sat.mode` | `0x0001` | 0.0 | **SAFE** — through every nominal pass |
| `ttc.active_gs` | — | 0.0 | no ground station selected, during a contact |
| `ttc.gs_equipment_status` | — | 0.0 | **every ground chain broken** |

`sat.mode` is the first parameter in the catalogue and the headline field of
the situational-awareness header: the spacecraft reported itself in SAFE mode
for the whole of every nominal exercise. `engine.py` maintains `self.sc_mode`
and hands it to the instructor snapshot, but never publishes it into
`self.params`, so the *ground* — which only has the downlink — saw the default.

`ttc.gs_equipment_status` is the worse one, because 0 is not "unknown", it is
the value the display renders as a red equipment fault on every ground chain.
An operator who believed the display would have concluded the network was down
during a nominal pass.

The seven EPS parameters in **#106** (`eps.power_margin`,
`eps.subsys_power_*`) are the same defect in the other direction: 0.0 W is a
plausible-looking power draw, so a per-subsystem power display could be read
with confidence and be entirely fictional.

The reason all of these survived is that nothing anywhere asserted that a
declared parameter has a producer. Adding a parameter to `parameters.yaml` and
a field to a display was sufficient to make it appear, correct-looking, on the
console.

## Fix

* `sat.mode` is **derived**, once, from a documented convention — bootloader
  and LEOP derive SAFE; commissioned with no contingency derives NOMINAL;
  payload imaging in NOMINAL derives SCIENCE; a contingency overrides upward;
  EMERGENCY takes precedence over SAFE — and published every tick.
* `ttc.active_gs` and `ttc.gs_equipment_status` are written by the TTC model
  from the contact and equipment state that already existed internally, with
  an explicit "no data" encoding distinct from "broken".
* The seven EPS parameters are written from the real per-subsystem breakdown.
* The general fix is the closure gate in **#106**: a declared parameter with no
  producer now fails the suite, so this cannot recur silently.

## Tests

`tests/test_simulator/test_sc_mode_convention.py` (11 tests) pins the whole
derivation table — `test_fresh_engine_constructs_in_safe`,
`test_commissioned_no_contingency_derives_nominal`,
`test_payload_imaging_in_nominal_derives_science`,
`test_emergency_takes_precedence_over_safe`,
`test_emergency_recovers_when_cleared` — so the parameter is not merely
non-zero but correct at every phase.

`tests/test_content/test_parameter_chain.py::test_every_parameter_is_reachable`
is the gate that makes "declared and unwritten" impossible.

`tests/test_mcs/test_ground_limits_and_archive.py::test_sa_block_carries_the_header_fields`
and `tests/test_mcs/test_trends_page.py::test_sa_strip_says_no_data_rather_than_zero`
pin the ground half: a parameter with no data must display as no data, never as
zero.

## Files

* `packages/smo-simulator/src/smo_simulator/engine.py` — `sat.mode` derivation
* `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py`
* `packages/smo-simulator/src/smo_simulator/models/eps_basic.py`
* `configs/eosat1/telemetry/parameters.yaml`
* `packages/smo-mcs/src/smo_mcs/static/index.html` — no-data rendering

## Related

* **#106** — the id-space integrity defects found in the same sweep.
* **#123** — the ground segment invented plausible defaults for absent
  parameters, which is the same failure mode one layer up.

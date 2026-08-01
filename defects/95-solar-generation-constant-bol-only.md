# 95 — the solar array generation constant was a beginning-of-life number

**Status:** Fixed

## Summary

Generation was computed as bare cell efficiency × irradiance × MPPT. That is
the number a cell datasheet quotes at 28 °C in a laboratory, not what an array
delivers on orbit. The model was **1.45× optimistic**, and every power-budget
conclusion in the courseware rested on it.

## Root cause

`models/eps_basic.py:400-414` (baseline):

```python
panel_pwr = area * self._cell_eff * self._solar_irrad * illumination
panel_pwr *= degradation * s.mppt_efficiency * s.sa_age_factor * noise
```

with `cell_efficiency: 0.295` and 1361 W/m². The effective plane-of-array
constant is therefore

```
K = 1361 × 0.295 × 0.97 (MPPT) = 389.5 W/m²
```

Missing from that chain, all of them standard and all of them multiplicative:

| Loss | Factor |
|---|---|
| Inherent degradation — cell mismatch, cover glass, packing, assembly | 0.85 |
| Array operating temperature — TJ GaAs −0.20 %/°C, 65 °C sunlit vs 28 °C reference | 0.926 |
| Harness | 0.95 |
| 3-year EOL aging (the sizing case) | 0.92 |

Correct constant: **K = 292 W/m² BOL, 267.9 W/m² at 3-year EOL** — the shipped
model was 1.45× high. `sa_age_factor` existed and did carry live aging, but it
was applied on top of a BOL number that had never had the other three losses in
it, so it degraded from the wrong starting point.

The practical effect was not a small margin error: an array 45 % better than
physical closes any budget, so no sizing decision in the courseware had ever
been tested against a real one, and defect **#50**'s "power budget does not
close" had been diagnosed against inflated generation.

## Fix

`configs/eosat1/subsystems/eps.yaml` gained an explicit, documented loss stack:

```yaml
array_losses:
  inherent_degradation: 0.85
  temp_coeff_per_c: -0.0020
  temp_ref_c: 28.0
  array_temp_sunlit_c: 65.0
  harness_efficiency: 0.95
  annual_degradation: 0.0275
  design_life_years: 3.0
```

`eps_basic` computes `k_eff` once from that stack and uses it in all three
generation regimes (tumbling fallback, per-panel cosine, SADA-tracked wings).
With `array_losses` **absent** the loss factor is exactly 1.0, so an old
configuration reproduces the legacy K = 389.5 W/m² behaviour rather than
silently changing under a user.

The array temperature is a configured constant (`array_temp_sunlit_c`) rather
than taken from the TCS panel nodes — a deliberate simplification, recorded in
the config comment, because the panel node itself was wrong (**#104**) and the
coupling is a separate piece of work.

## Tests

`tests/test_simulator/test_eps_generation_physics.py`

* `TestLossStack::test_one_square_metre_normal_incidence_eol` — 1 m² at normal
  incidence delivers the EOL constant, computed independently in the test from
  the config values rather than hard-coded;
* `TestLossStack::test_legacy_behaviour_without_loss_stack` — no `array_losses`
  block gives exactly 389.5 W/m².

`tools/power_budget.py` recomputes the orbit-average budget from the same
config and is the source of the closure numbers in the changelog.

## Files

* `packages/smo-simulator/src/smo_simulator/models/eps_basic.py`
* `configs/eosat1/subsystems/eps.yaml`
* `tools/power_budget.py`
* `configs/eosat1/manual/02_eps.md`

## Related

* **#50** — the power budget that did not close; it was diagnosed before this,
  against a 1.45× optimistic generation figure.
* **#96** — the other half of the generation error: the wings were on the
  swept axis and the drive angle was ignored.

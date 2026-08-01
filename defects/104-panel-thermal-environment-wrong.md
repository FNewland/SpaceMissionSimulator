# 104 — solar panels reached +362 °C, and nothing was watching them

**Status:** Fixed

## Summary

The panel thermal nodes had a solar-heating gain roughly **80× too large** and
a beta-angle dependence with the **wrong sign**. At full wing current the +Y
panel equilibrated at **362 °C** — hotter than the melting point of the
adhesive holding the cells on — and no monitor, limit or event existed to
notice, so it never appeared as anything but a number in a display nobody
questioned.

## Root cause

**1. Gain applied on top of the physical scaling.** `tcs_basic.py:436-438`
(baseline):

```python
solar_heat_w = solar_current * 28.0 * e["panel_solar_heat_fraction"]
heat_input   = solar_heat_w * e["panel_solar_heat_gain"]   # 10.0
env          = base_env + heat_input
```

`heat_input` is added to an environment temperature, so its units are kelvin,
but it is computed as watts × a dimensionless "gain" of 10.0. The equivalent
environment rise for heat `Q` into a first-order node is `Q/UA` with
`UA = C/τ`; the code multiplied by a flat 10 K/W **instead of** that, which for
the panel nodes is ~80× the correct scaling. With the Phase-B wing areas the
+Y panel then sat at +362 °C in sunlight.

**2. Beta dependence inverted.** `tcs_basic.py:424`:

```python
beta   = orbit_state.solar_beta_deg
sun_f  = abs(math.cos(math.radians(beta)))
env_ext = e["sunlit_external_base_c"] + e["sunlit_external_beta_gain_c"] * sun_f
```

The orbit-average external sink **rises** with |β| — a high-beta orbit has less
eclipse and more continuous sun. `|cos β|` peaks at β = 0, which is the
maximum-eclipse case: the model made the coldest orbit the hottest. The
50 °C `sunlit_external_beta_gain_c` swing therefore ran backwards across the
whole beta sweep, and the seasonal thermal exercise taught the inverse of the
physics.

**3. No monitor.** There was no S12 definition, no MCS limit band and no event
threshold on any `tcs.temp_panel_*` parameter, so an absurd value was
indistinguishable from a nominal one on every display.

## Fix

```python
solar_heat_w = solar_current * 28.0 * e["panel_solar_heat_fraction"]
ua_node      = self._CAP[key] / self._TAU[key]
heat_input   = solar_heat_w / ua_node * e["panel_solar_heat_gain"]
...
sun_f = abs(math.sin(math.radians(beta)))
```

`panel_solar_heat_gain` drops from 10.0 to 1.0 and is documented as a trim
multiplier on a scaling that is now physical. Panel nodes gained S12
definitions and MCS limit bands, so an out-of-family panel temperature is
visible on both the board and the ground.

## Tests

`tests/test_simulator/test_thermal_phase_b.py::TestMonitors::test_clean_nominal_run_violates_no_thermal_monitor`
— a clean run stays inside every thermal band at every beta, which is the
compact statement of both errors: with the old gain the panels violated
immediately, and with the old sign the violation moved to the wrong end of the
beta sweep.

`tests/test_content/test_limits_are_live.py` ensures the new panel bands are
actually evaluated rather than merely declared (**#120**).

## Files

* `packages/smo-simulator/src/smo_simulator/models/tcs_basic.py`
* `configs/eosat1/simulation.yaml` — `panel_solar_heat_gain: 10.0 → 1.0`
* `configs/eosat1/monitoring/s12_definitions.yaml`,
  `configs/eosat1/mcs/limits.yaml` — panel bands
* `configs/eosat1/manual/03_tcs.md`

## Related

* **#95** — array performance uses a configured sunlit array temperature; the
  panel nodes were not fit to supply it, which is why that coupling was left
  as declared future work rather than wired to this model.
* **#105** — the monitor half of the same story.

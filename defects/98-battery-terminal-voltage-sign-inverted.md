# 98 — the bus voltage sagged when the battery was charging

**Status:** Fixed

## Summary

The battery terminal-voltage model had the sign of the IR term inverted, so
charging pulled the bus **down** and discharging pushed it **up** — the
opposite of every battery. Bus voltage is the parameter students watch to
reason about eclipse and about load shedding, and it moved the wrong way all
the way through every orbit.

## Root cause

`models/eps_basic.py:551-560` (baseline):

```python
ocv     = self._soc_0_v + (self._soc_100_v - self._soc_0_v) * (s.bat_soc_pct / 100.0)
bat_i   = net_power_w / (ocv + 1e-6)
v_loaded = ocv - bat_i * self._internal_r
```

`bat_i` is derived from **net** power, so it is **positive when charging**.
The terminal-voltage relation for a source with internal resistance is

```
charging     (current into the cell):  V = OCV + I·R
discharging  (current out of the cell): V = OCV − I·R
```

The code applies `− I·R` to a current that is positive on charge. At the
shipped 0.05 Ω and a 30 W charge at 28 V (≈1.07 A) that is a 54 mV error in the
wrong direction — small in magnitude, but it inverts the *shape* of the curve
that the whole eclipse lesson is built on: sunlit and charging read as the
lowest bus voltage of the orbit.

Two related gaps in the same block:

* **No coulombic efficiency.** Charge in equalled charge out, so a battery
  cycled at 100 % round-trip efficiency and the per-orbit energy balance was
  optimistic by ~6 %.
* **No CV taper.** Charging continued at full rate to 100 % SoC, so the last
  10 % of SoC filled far faster than a real Li-ion cell allows.

## Fix

```python
# sign convention: bat_i > 0 = charging
v_loaded = ocv + bat_i * self._internal_r   # charging
v_loaded = ocv - abs(bat_i) * self._internal_r  # discharging
```

plus, from `configs/eosat1/subsystems/eps.yaml`:

```yaml
battery:
  charge_efficiency: 0.94        # Li-ion coulombic efficiency
  discharge_efficiency: 1.0
  cv_taper_start_soc_pct: 90.0   # charge power tapers linearly to zero at 100 %
```

Charge accepted into SoC is `0.94 ×` the electrical energy delivered; the
discharge path is unchanged, so the model's end-to-end round-trip efficiency is
the coulombic figure and nothing double-counts.

## Tests

`tests/test_simulator/test_eps_battery_physics.py`

* `TestVoltageSign::test_bus_rides_above_ocv_on_charge` and
  `test_bus_sags_below_ocv_on_discharge` — the sign, both ways;
* `TestCoulombicEfficiency::test_charge_acceptance_094` and
  `test_model_end_to_end_efficiency` — the efficiency is applied once;
* `TestCVTaper::test_charge_rate_tapers_above_90_pct`;
* `TestPowerSelfConsistency::test_net_power_equals_battery_current_times_voltage`
  — the electrical bookkeeping closes, which is what caught the original sign.

## Files

* `packages/smo-simulator/src/smo_simulator/models/eps_basic.py`
* `configs/eosat1/subsystems/eps.yaml`
* `configs/eosat1/manual/02_eps.md`

## Related

* **#99** — the protection ladder is expressed partly in bus volts, so its
  thresholds could not be reasoned about while the voltage moved the wrong way.

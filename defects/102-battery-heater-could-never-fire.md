# 102 — the battery heater could never fire, and said "success" when it could not

**Status:** Fixed

## Summary

The battery heater is the only thermostat-controlled element on the
spacecraft and the subject of a commissioning step. Its trip point was **9 °C
below the coldest temperature the battery node could ever reach**, so in three
years of runs it had never switched on. Commanding it directly returned success
and was silently reverted on the next tick whenever its EPS line was off.

## Root cause

**1. Unreachable thermostat.** The battery node's environment came from the
shared internal sink (`eclipse_internal_c: 10.0`, `sunlit_internal_c: 12.0`),
so the coldest achievable battery temperature was **+9.2 °C**. The configured
thermostat band trips at +1 °C. The comparator in `_thermostat_control`
(`tcs_basic.py:236`) was correct; it simply could never be true. The 6 W the
EPS billed for that heater (see **#103**) was therefore a bill for a device
that had never energised.

**2. Silent revert.** `HEATER_BATTERY` returned `{"success": True}` from the
command handler and set `s.htr_battery = True`. On the next tick,
`tcs_basic` re-derives the heater from its EPS line:

```python
bat_eps_on = bool(shared_params.get(0x0115, 0))
if bat_eps_on:
    self._thermostat_control("battery", s.temp_battery)
else:
    s.htr_battery = False   # No power → heater off
```

With line 5 off, the operator got an S1.7 "execution complete", watched
`tcs.htr_battery` read 1, and saw it read 0 one second later with no event, no
failure code and no explanation. The correct behaviour — refuse — was available
and not used.

**3. Setpoint defaults overrode the configuration.** The TCS state dataclass
carried per-circuit setpoint defaults as field initialisers, and those were
applied *after* the configured thermostat band was loaded, so
`subsystems/tcs.yaml`'s band was silently discarded for any circuit that had a
state-field default. Editing the config appeared to do nothing.

## Fix

* The battery node's thermal environment is its own, not the shared internal
  sink: `eclipse_battery_c: -8.0`, `sunlit_battery_c: 0.0` in
  `simulation.yaml`, tuned so the thermostat **genuinely cycles every orbit**
  and the heater is a real load with a real duty cycle. The config comment
  records the +9.2 °C history.
* `HEATER_BATTERY` with the EPS line dark now **fails** with a reason
  ("EPS line 5 (htr_bat) is off"), which reaches the operator through the
  S1.8 failure notice added in **#87**.
* Configured thermostat bands win over state-field defaults; the defaults are
  fallbacks only.

## Tests

`tests/test_simulator/test_thermal_phase_b.py::TestBatteryHeater`

* `test_thermostat_cycles_every_orbit` — the heater actually switches, which
  is the defect stated positively;
* `test_battery_reaches_5c_in_eclipse_without_heater` — the node can now get
  cold enough for the thermostat to matter;
* `test_heater_command_fails_loudly_when_line_dark` — success is no longer
  returned for an action that will be undone.

## Files

* `packages/smo-simulator/src/smo_simulator/models/tcs_basic.py`
* `configs/eosat1/simulation.yaml` — battery thermal environment
* `configs/eosat1/subsystems/tcs.yaml` — thermostat band precedence
* `configs/eosat1/manual/03_tcs.md`

## Related

* **#103** — the EPS billed 6 W for this heater from the line switch position,
  so the phantom bill and the dead thermostat were the same story from two
  ends.
* **#11**, **#31** — earlier heater-control repairs, made against a thermostat
  that could not fire.

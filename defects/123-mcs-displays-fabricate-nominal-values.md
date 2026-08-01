# 123 — two MCS displays invented nominal-looking numbers for data they did not have

**Status:** Fixed

## Summary

The system-overview dashboard and the power-budget panel filled missing
telemetry with **plausible defaults** — 75 % state of charge, −15 °C FPA, 8 dB
link margin, and a six-entry per-subsystem power breakdown that was six
hard-coded constants. Subsystem health was permanently GREEN because the method
that computes it had no callers. A display that says "everything is fine" when
it has no data is worse than a blank one, and this pair said it convincingly.

## Root cause

**Power budget.** `displays/power_budget.py:51-58` — the per-subsystem
breakdown is a literal:

```python
per_subsystem_power={
    "eps": 15.0, "aocs": 25.0, "tcs": 30.0,
    "ttc": 20.0, "payload": 20.0, "obdh": 10.0,
},
```

`update_from_telemetry` (line 61) never touches it, so those six numbers were
what the panel showed for the entire life of the product — a 120 W total on a
6U whose real load is ~34 W. And what it *does* update, it updates from key
spellings the MCS state does not use:

```python
self._current_state.power_gen_w         = float(eps.get("power_gen", 150.0))
self._current_state.power_cons_w        = float(eps.get("power_cons", 120.0))
self._current_state.battery_soc_percent = float(eps.get("bat_soc", 75.0))
```

Every one of those `.get()` calls missed, so **every field silently fell back
to its default** and the panel showed 150 W generation, 120 W consumption and
75 % SoC regardless of the spacecraft. The defaults were chosen to look
healthy, which is precisely why nobody questioned them.

**System overview.** `displays/system_overview.py` did the same for absent
parameters (75 % SoC, −15 °C FPA, 8 dB margin), and `update_subsystem_health`
had **no callers**, so every subsystem tile stayed GREEN — including during the
FDIR events of **#124** and the limit violations of **#120**.

The five `/api/displays/*` endpoints these classes back
(`server.py:967-973`) had no client page either, so the fabrication was served
but not seen — until the Phase G situational-awareness header started
consuming them, at which point invented numbers would have reached the
operator's primary display.

## Fix

* **No fabricated defaults.** A parameter with no data renders as `NO DATA`,
  distinct from zero and from nominal, at every level: panel field, subsystem
  tile and header strip.
* The power budget reads the real per-subsystem breakdown, published by the
  EPS as the seven parameters that **#106**/**#107** brought to life, using the
  key spellings the MCS state actually carries.
* `update_subsystem_health` is called from the telemetry path and derives
  health from the **live ground limits** (**#120**), so a tile turns amber or
  red for the same reason the alarm journal does.
* The five `/api/displays/*` endpoints gained their consumer (the SA header and
  the overview page) or were removed.

## Tests

`tests/test_mcs/test_ground_limits_and_archive.py`

* `test_system_overview_reports_unknown_without_telemetry` — the defect,
  inverted;
* `test_system_overview_health_follows_the_ground_limits`;
* `test_power_budget_says_no_data_instead_of_inventing_one`;
* `test_power_budget_uses_the_real_per_subsystem_breakdown`;
* `test_console_calls_every_endpoint_phase_g_wired` (parametrised by route).

`tests/test_mcs/test_trends_page.py::test_sa_strip_says_no_data_rather_than_zero`
and `tests/test_content/test_endpoint_reachability.py::test_every_route_has_a_consumer_or_a_reason`
keep both properties.

## Files

* `packages/smo-mcs/src/smo_mcs/displays/power_budget.py`,
  `system_overview.py`
* `packages/smo-mcs/src/smo_mcs/server.py`
* `packages/smo-mcs/src/smo_mcs/static/index.html`

## Related

* **#107** — the same failure mode on the spacecraft side: a parameter nothing
  writes reads as a plausible value.
* **#13**, **#20** — the display classes' earlier dead-code findings.
* **#120** — health is now derived from the limits that finally run.

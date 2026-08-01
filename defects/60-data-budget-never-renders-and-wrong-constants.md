## Summary

The operator's report "there is no data budget" was accurate as an observation
and pointed at three separate faults.

1. **The panel never rendered.** `static/index.html:2163`:
   ```js
   if (!panel || !dataBudgetData.utilization_percent) return;
   ```
   `utilization_percent` is a **number**, and `0` is falsy. On a fresh planner
   the schedule is empty, so utilisation is `0.0`, the function returned before
   touching the DOM, and the panel kept its `<div>Loading...</div>` placeholder
   **forever**. The adjacent Power panel escaped only because its guard tests
   `initial_soc`, which is 80.0 and truthy.

2. **The constants contradicted the mission config, by up to 20x.**

   | Quantity | `budget_tracker.py` | Config | Divergence |
   |---|---|---|---|
   | Onboard storage | `ONBOARD_STORAGE_MB = 1024.0` | `payload.yaml total_storage_mb: 20000.0` | **20x** |
   | Storage (2nd impl) | `DataVolumeConstraintChecker(storage_capacity_mb=20000.0)` | 20000 | the two planner modules disagreed with each other |
   | Downlink rate | `DOWNLINK_RATE_BPS = 64000` | `ttc.yaml tm_rate_hi_bps: 64000` | value matched by luck, not by reference |
   | Downlink rate (2nd impl) | `downlink_rate_mbps = 0.5` | — | **~10x** the tracker's 51.2 kbps, and applied as "Mbps x minutes", which is not a MB |
   | Battery | `BATTERY_CAPACITY_WH = 120.0` | `eps.yaml` 120.0 | docstring said "40 Ah at 28 V = 1120 Wh" — 9x stale |

3. **The model ignored ordering.** `compute_data_budget` summed generation,
   summed capacity and subtracted, so it could report a comfortable margin for a
   plan that overflows at hour 3 and drains at hour 20, and it credited a dump
   with a full pass's capacity whether or not the dump overlapped the pass.

## Severity

**Major** — the data budget was invisible by default and, when made visible,
wrong by an order of magnitude, in a mission whose central operational tension
*is* the data budget.

## Status

**Fixed.**

1. **Guard.** `if (dataBudgetData.utilization_percent === undefined) return;` —
   an empty plan now renders "0 / 20000 MB, 0.0 %".
2. **Config-driven.** `budget_params_from_config()` resolves storage capacity
   and image size from `payload.yaml`, downlink rate from `ttc.yaml` and battery
   capacity from `eps.yaml`, with optional `planner.yaml budget:` overrides
   (`null` = inherit). `GET /api/config/planner` reports the resolved values
   **and which file each came from**. `DataVolumeConstraintChecker` takes the
   same parameters (`from_params`), so the two implementations can no longer
   disagree, and its downlink subtraction is a real bits->MB conversion.
3. **Chronological integration.** `compute_data_budget` now steps over the
   merged activity/contact event list: generation is spread over each activity's
   duration, downlink flows only while a dump **overlaps a real contact
   window** (at that station's effective rate x elevation efficiency, capped by
   what is actually onboard), and the store is clamped at capacity with the
   excess recorded as overflow. Outputs: current and **peak** fill with its
   time, **first overflow** time and MB lost, end-of-horizon **backlog**,
   per-pass capacity vs planned dump, total pass capacity per day,
   **days_to_drain**, and `plan_closes`. New warning classes
   `STORAGE_OVERFLOW`, `DOWNLINK_INFEASIBLE`, `PASS_WITHOUT_DUMP`.
4. **The UI shows the truth**, including the per-pass downlink table (computed
   since Wave 7 and rendered nowhere until now) and a red
   **"PLAN DOES NOT CLOSE — N days of contact needed to drain"** line.

### The honest finding, not fudged

With the mission config as it stands the plan **cannot close**, and the budget
now says so:

* image size 800 MB (`payload.yaml image_size_mb`),
* downlink 64 kbps (`ttc.yaml tm_rate_hi_bps`), 51.2 kbps after 20 % overhead,
* five-station contact time 384 contact-min/day, ~0.6-0.9 elevation efficiency
  -> roughly **100 MB/day** of real downlink capacity.

One 800 MB image therefore needs **~8 days** of the entire five-station network
(~21 days with the old two-station network). This is a genuine configuration
contradiction, not a bug in the arithmetic, and it is left visible on purpose:
`plan_closes: false`, a `DOWNLINK_INFEASIBLE` warning quantifying the shortfall,
and `days_to_drain`. It needs a mission-level decision (see Related) — the
planner must not paper over it.

## Acceptance criteria

- An empty plan renders the panel with 0 / 20000 MB.
- Budget parameters equal the values in `payload.yaml` / `ttc.yaml` /
  `eps.yaml`, and both planner modules use the same downlink rate.
- A plan generating one 800 MB image with a dump on every pass reports
  `plan_closes: false`, a positive backlog and `days_to_drain > 1`.
- A plan whose product fits reports `plan_closes: true`.
- Storage overflow is reported with a timestamp, and a dump scheduled outside
  every contact window moves no data.

## Affected areas

- `packages/smo-planner/src/smo_planner/budget_tracker.py`
  (`budget_params_from_config`, `BudgetTracker.from_config`,
  `compute_data_budget`)
- `packages/smo-planner/src/smo_planner/constraint_checkers.py`
  (`DataVolumeConstraintChecker`, `validate_plan(data_params=...)`)
- `packages/smo-planner/src/smo_planner/activity_scheduler.py`,
  `server.py` (`_make_budget_tracker`, `/api/budget/data`,
  `/api/config/planner`), `static/index.html`
- `configs/eosat1/planning/planner.yaml` (`budget:`)
- Tests: `tests/test_planner/test_data_budget_config.py`;
  `tests/test_planner/test_budget_tracker.py::test_storage_overflow_warning`
  updated to pin its own capacity now that the default comes from config.

## Related

- `docs/review_2026-07/textbook_vs_sim.md` §3 row 8 / row 62: the course
  textbook assumes **~4 MB** per imaging session; the sim uses **800 MB** with a
  20 GB store and no downlink path faster than 64 kbps. Recommended
  reconciliation (not applied unilaterally): either set
  `payload.yaml image_size_mb: 4.0` (textbook-aligned; one image = ~11 min of
  contact, a nominal day closes comfortably), or keep 800 MB and configure the
  X-band payload downlink the config already implies
  (`payload.yaml data_rate_mbps: 80.0`) with a per-station
  `payload_downlink_rate_bps` in `planning/planner.yaml` — at 80 Mbps a
  7.6-min pass moves ~3.4 GB and one image needs ~80 s of contact.
  `planner.yaml stations[].supports_payload_downlink` /
  `downlink_rate_bps` are already in the schema for that second option.
- Defect #50 (power budget does not close) — the same class of finding on the
  energy side.
- Defect #57 — the empty default plan that exposed the falsy-zero guard.

## Summary

The planner UI treated `time_source == "sim"` — the shipped **default**
(`smo-common/.../schemas.py` `time_source: "sim"`, `mission.yaml`) — as
permission to fabricate data in the browser. In the default deployment the page
fetched the server's real, SGP4-computed contact windows and the real schedule,
then threw both away in favour of hard-coded JavaScript:

1. **Ground stations.** `GROUND_STATIONS_FALLBACK` (`static/index.html:1379-1384`)
   held Svalbard / Troll / Inuvik / O'Higgins with fabricated
   `contactOffset`/`contactDur`/`maxEl`. It drove the timeline rows, the Y-axis
   labels and the contacts table — the two places an operator actually reads a
   station name — while the configured network was Iqaluit + Troll. Worse,
   `drawTimeline` dropped any contact whose `gs_name` was not in that list
   (`findIndex(...) === -1 → return`), so a real **Iqaluit** pass was silently
   discarded from the display.
2. **Contact windows.** `generateSimulatedContacts()` manufactured passes at
   fixed offsets with `Math.random()` elevations, unconditionally in sim mode.
   The four-station display implied **2.6x the downlink budget and half the
   worst-case outage** the mission actually had — a training-integrity problem,
   not a cosmetic one.
3. **The plan.** `SAMPLE_ACTIVITIES` (`index.html:1386-1396`) seeded nine fake
   activities — including "Orbit Maintenance", which is not a defined activity
   type at all. They had no `id`, so they could not be uploaded, validated or
   costed (`alert('Activity has no ID (simulated?)')`); they were decoration
   that looked like a plan. The backend was already correct: `ActivityScheduler`
   starts with `self._schedule = []` and nothing seeds it.

There was also no planner-owned config file: horizons, refresh intervals,
budget parameters and per-station planning attributes were Python literals, and
nothing reconciled the planner's view of the ground network with `orbit.yaml`.

## Severity

**Major** — the planner displayed a ground network, a pass schedule and an
activity plan that were all fiction, in the default configuration, with no
indication that they were not the mission's.

## Status

**Fixed.**

1. **Five-station network, canonical in `orbit.yaml`.** Iqaluit, Troll,
   Svalbard, Inuvik and O'Higgins, with real-world coordinates (sources named in
   the file header) and 5 deg masks. Measured with
   `tools/pass_gap_analysis.py --hours 48`: **103 passes/48 h** (51.5/day),
   median gap **33.2 min**, max gap **78.8 min** — against 39 passes, 84.3 min
   and 147.5 min for the previous two-station network. Mirrored into
   `planning/ground_stations.yaml` (RF augmentation).
2. **The planner has its own config file**, `configs/eosat1/planning/planner.yaml`
   (schema `smo_planner.planner_config.PlannerConfig`, pydantic v2, validated at
   load): time-source defaults, horizons, refresh intervals, budget parameters,
   imaging feasibility, default-plan behaviour, alignment policy and per-station
   *planning* attributes (priority, bands, setup/teardown, min dump elevation,
   payload-downlink support). Documented in
   `packages/smo-planner/PLANNER_CONFIG.md`.
3. **Alignment mechanism.** `check_station_alignment()` compares the planner's
   station list against `orbit.yaml` (canonical) and reports missing stations
   and out-of-tolerance geometry mirrors. It runs (a) at planner start-up as a
   load-time validation warning — `alignment.on_divergence: warn|error|ignore`;
   (b) at `GET /api/config/alignment`, JSON plus a rendered table; and (c) from
   the CLI `python -m smo_planner.planner_config --config configs/eosat1`,
   which exits **0 aligned / 2 divergent** so it can gate CI.
4. **The JS fixtures are deleted.** `GROUND_STATIONS_FALLBACK`,
   `SAMPLE_ACTIVITIES`, `generateSimulatedContacts()`,
   `generateSimulatedActivities()`, `simulatedContacts`, `simulatedActivities`,
   `useSimulatedContacts`, `useSimulatedSchedule` and both
   `if (timeSource === 'sim') useSimulated* = true` overrides are gone.
   `/api/ground-stations` is now the page's ONLY station source, awaited before
   first paint, with no fallback list: a failed fetch renders
   "Ground-station list unavailable" and an empty contact list renders
   "No contact windows — check configuration" instead of inventing passes.
   The SIM/LIVE badge is kept but now names the **clock**, not the fidelity.
5. **Default empty plan + explicit standard activities.** The schedule renders
   the real (empty) plan with "No activities planned". A new
   `configs/eosat1/planning/standard_plans.yaml` defines named templates
   (`nominal_ops_day`, `downlink_catchup`, `payload_checkout`) instantiated by
   `POST /api/schedule/load-template` at the planner's **current sim time**,
   with anchors resolved against the **real** contact windows and sunlit arcs
   (`absolute`, `offset_from_now`, `pass_relative`, `daylight_relative`).
   Templates may only name types from `activity_types.yaml`, so every instance
   gets the real command sequence with its verified func_ids (defect #45);
   load-time validation rejects one that does not. `GET /api/schedule/templates`
   lists them, `DELETE /api/schedule` returns to empty.

## Acceptance criteria

- `orbit.yaml`, `planning/planner.yaml` and `planning/ground_stations.yaml` all
  carry the same five stations, and the alignment check passes.
- The alignment check fails loudly (issue text, non-zero CLI exit, start-up
  warning, or `ValueError` under `on_divergence: error`) when a station is
  missing from either file or a mirrored coordinate is out of tolerance.
- `static/index.html` contains no station names and no activity fixtures;
  `/api/ground-stations` is the single station source.
- A fresh planner shows an empty schedule; loading `nominal_ops_day` produces
  activities at sim time, on real passes, with real command sequences.

## Affected areas

- `configs/eosat1/orbit.yaml`, `configs/eosat1/planning/planner.yaml` (new),
  `configs/eosat1/planning/standard_plans.yaml` (new),
  `configs/eosat1/planning/ground_stations.yaml`
- `packages/smo-planner/src/smo_planner/planner_config.py` (new),
  `plan_templates.py` (new), `server.py`, `activity_scheduler.py`,
  `static/index.html`
- `packages/smo-planner/PLANNER_CONFIG.md` (new)
- Tests: `tests/test_planner/test_planner_config_alignment.py`,
  `test_standard_plans.py`, `test_ui_data_sources.py`;
  `tests/test_simulator/test_multi_station_contacts.py` updated to the
  five-station expectation.

## Related

- Defect #42 (ground-station config duplication) — this makes `orbit.yaml`'s
  canonical status enforceable rather than aspirational, and extends it to the
  planner's JS.
- Defect #41 (single-station contact gating).
- Defect #45 (activity func_id misroute) — templates route through
  `activity_types.yaml`, so the corrected func_ids are the only ones used.
- Defects #58, #59, #60, #61 — the other planner findings from the same audit.

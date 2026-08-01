# Planner configuration and config alignment

The mission planner has its own config file. This document says what is in it,
what still belongs to the spacecraft/simulator config, and how the two are kept
aligned.

## Files

| File | Owns | Consumed by |
|---|---|---|
| `configs/eosat1/orbit.yaml` | **Canonical** ground-station geometry (name, lat, lon, alt, elevation mask) + TLE + T-0 | simulator, planner, MCS (`load_orbit_config`) |
| `configs/eosat1/planning/planner.yaml` | **Planner-only** settings: time source defaults, horizons, refresh intervals, budget parameters, imaging feasibility, default-plan behaviour, per-station *planning* attributes, alignment policy | planner (`smo_planner.planner_config.load_planner_config`) |
| `configs/eosat1/planning/ground_stations.yaml` | RF augmentation per station (antenna, band, G/T, TX power, frequencies) | RF tooling, MCS cross-check |
| `configs/eosat1/planning/standard_plans.yaml` | Named standard-activity templates | planner (`smo_planner.plan_templates`) |
| `configs/eosat1/planning/activity_types.yaml` | Activity type catalogue incl. verified S8/S15 command sequences | planner |
| `configs/eosat1/planning/imaging_targets.yaml` | Imaging target regions and their constraints | planner |

Schemas are pydantic v2 models validated at load, following the smo-common
config pattern. The planner models live in
`packages/smo-planner/src/smo_planner/planner_config.py` (planner app settings)
and `.../plan_templates.py` (templates) rather than in `smo_common.config.schemas`,
because they describe the planning tool, not the spacecraft.

## What `planner.yaml` contains

```
time:          default_time_source, sim_state_url, anchor_refresh_s,
               jump_tolerance_s, stale_anchor_s
horizons:      contacts_hours/step, ground_track_hours/step, imaging_hours/step,
               timeline_hours
refresh:       recompute_interval_s, sim_recompute_interval_s,
               poll_interval_s, spacecraft_refresh_s
budget:        initial/min SoC, battery_capacity_wh, storage_capacity_mb,
               image_size_mb, downlink_rate_bps, protocol_overhead,
               storage_margin_percent, hk_background_mb_per_hour
imaging:       fov_deg, swath_km, max_off_nadir_deg, capture_duration_s,
               capture_power_w, enforce_solar_elevation, enforce_revisit
default_plan:  start_empty, templates_file, default_template
alignment:     canonical_source, on_divergence, tolerances
stations:      per-station planning attributes (see below)
```

`budget:` values that are `null` are **inherited from the spacecraft config** —
`payload.yaml total_storage_mb` / `image_size_mb`, `ttc.yaml tm_rate_tiers`
(the `default_tier` for routine traffic and the top rung for dumps — see
CONSTRAINTS.md; `downlink_rate_bps` is the legacy name for the dump rung only),
`eps.yaml battery.capacity_wh`. That is deliberate: the planner must not carry
its own copy of a number the spacecraft already declares (this is what produced
the 20x storage error and the 51.2 kbps vs 0.5 Mbps disagreement).
`GET /api/config/planner` shows the resolved values *and* which file each came
from.

## Per-station planning attributes

`stations:` does **not** own geometry. It adds the attributes the planner needs
and the simulator does not care about:

| Field | Meaning |
|---|---|
| `enabled` | Station is available for planning |
| `priority` | Higher wins when two stations offer the same slot |
| `bands` | Supported bands (S today) |
| `supports_payload_downlink`, `downlink_rate_bps` | Per-station payload downlink path and rate (none configured today — see hand-off note) |
| `setup_time_s`, `teardown_time_s` | Ground/onboard configuration either side of a usable pass |
| `min_dump_elevation_deg` | Elevation below which a dump is not planned |
| `max_passes_per_day` | Optional scheduling cap |
| `lat_deg`, `lon_deg`, `alt_km`, `min_elevation_deg` | Optional geometry **mirror**, cross-checked against `orbit.yaml`. Omit them to inherit silently. |

## Alignment mechanism

`orbit.yaml` is canonical. The planner reconciles it with `planner.yaml` in
three places:

1. **Load-time validation.** `PlannerServer.__init__` calls
   `check_station_alignment()`. Every divergence is logged as
   `GROUND-STATION CONFIG DIVERGENCE: ...`. `alignment.on_divergence` selects
   `warn` (default), `error` (constructor raises — fail fast in a deployment)
   or `ignore`.
2. **Endpoint.** `GET /api/config/alignment` returns the machine-readable
   report plus a rendered `text` table. `?recheck=1` re-reads both files first.
3. **CLI.**
   ```
   python -m smo_planner.planner_config --config configs/eosat1
   python -m smo_planner.planner_config --config configs/eosat1 --json
   ```
   Prints the side-by-side table and exits **0 when aligned, 2 when divergent**,
   so it can gate CI.

Example output:

```
STATION      ORBIT.YAML (lat/lon/alt/mask)          PLANNER.YAML          STATUS
Inuvik         68.3195  -133.5495  0.102km  5.0deg  prio  80 S            ok
Iqaluit        63.7470   -68.5180  0.034km  5.0deg  prio  50 S            ok
O'Higgins     -63.3211   -57.9008  0.032km  5.0deg  prio  60 S            ok
Svalbard       78.2297    15.3975  0.458km  5.0deg  prio 100 S            ok
Troll         -72.0120     2.5350  1.275km  5.0deg  prio  90 S            ok

ALIGNED — 5 station(s) in orbit.yaml, 5 in planner.yaml.
```

Detected divergences: a station present in one file and not the other, and any
geometry mirror outside the configured tolerance (default ~110 m in position,
10 m in altitude, 0.1 deg in mask). A planner station that omits geometry is
**not** a divergence — it inherits.

**To add or move a station:** edit `orbit.yaml` first, mirror into
`planner.yaml` (planning attributes) and `ground_stations.yaml` (RF), then run
the CLI.

## Standard activity templates

The planner starts with an **empty** schedule (`default_plan.start_empty`).
Standard activity sets are config-defined templates in `standard_plans.yaml`,
loaded explicitly:

* `GET /api/schedule/templates` — list;
* `POST /api/schedule/load-template {template, t0?, clear?}` — instantiate at
  the planner's current (sim) time;
* `DELETE /api/schedule` — back to empty.

Each template entry names an activity **type** from `activity_types.yaml`, so
duration/power/data volume and the command sequence with its verified func_ids
come from that catalogue; a template cannot invent a type or a command routing,
and load-time validation rejects one that tries. Anchors (`absolute`,
`offset_from_now`, `pass_relative`, `daylight_relative`) are resolved against
the planner's current time and the **real** contact windows / ground track.

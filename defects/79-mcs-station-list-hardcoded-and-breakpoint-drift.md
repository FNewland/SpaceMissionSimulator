## Summary

Two independent MCS defects, both "the console assumes instead of reading".

### (a) The ground network was hard-coded to two stations

`configs/eosat1/orbit.yaml` declares **five** ground stations — Iqaluit, Troll,
Svalbard, Inuvik and O'Higgins — and the note above the list records the measured
consequence: 2 stations give 39 passes over 48 h with a median gap of 84 min,
5 stations give 103 passes with a median gap of 33 min. The MCS's own orbit
propagator was already built from all five. The **browser** was not:

- the contact-timeline legend listed exactly two coloured dots, `Iqaluit` and
  `Troll`, as static markup;
- the GROUND STATION STATUS panel was two hand-written `<div class="gs-panel">`
  blocks with hard-coded ids (`gs-iqaluit-*`, `gs-troll-*`) and a two-button
  selector;
- `renderContactTimeline()` carried
  `const gsColors = { 'Iqaluit': '#3b82f6', 'Troll': '#22c55e' }`, so passes over
  the other three stations were all drawn in the same fallback blue and were
  indistinguishable from each other;
- `updateGsDisplay()` decided which station was tracking with
  `gsName.toLowerCase().includes('iqaluit') || (inContact && !gsName…includes('troll'))`
  — i.e. **any** contact that was not Troll was attributed to Iqaluit. A Svalbard,
  Inuvik or O'Higgins pass lit the Iqaluit card.

So three fifths of the mission's contacts were displayed under the wrong station
name, and the console showed a two-station network for a five-station mission.

### (b) No detection of a sim-time discontinuity after a breakpoint load

`_sim_state_poll_loop` re-anchored the ground clock from the simulator's
`sim_time` **unconditionally**, every 1.5 s:

```python
self._ground_epoch = parsed
self._ground_start_wall = _time_mod.time()
```

A breakpoint load, a scenario reset or an instructor time jump moves `sim_time`
arbitrarily far backward or forward. The clock followed silently — and everything
the MCS had derived at the *old* epoch stayed on screen describing a moment that
no longer existed: the schedule report's execution times (converted through the
OBC clock correlation), the S15 store status, the S6 dump and CRC results, and
the FDIR journal's timeline. Nothing invalidated them and nothing told the
operator time had moved. This is the same gap the planner carried as defect #59.

## Severity

**Major** — (a) mis-attributes real contacts to the wrong station and understates
the network; (b) leaves stale-but-plausible times and reports on the console after
an instructor jump, in a training system whose whole point is scenario replay.

## Status

**Fixed.**

### (a) Stations come from the configuration

`GET /api/ground-stations` serves the mission's real network, assembled from
`orbit.yaml` (canonical geometry — the same list the MCS propagator is built
from) merged with the RF augmentation layer in
`planning/ground_stations.yaml` (antenna diameter, band, G/T, frequencies). Each
station gets a DOM-safe id (`O'Higgins` → `o-higgins`) and a display colour
assigned **by list order**, so a sixth station needs no UI change.

The browser now generates:

- one selector button and one status card per station, showing lat/lon, the
  elevation mask and the RF augmentation when present;
- the contact-timeline legend, from the same list and the same colours;
- the pass bars' colours via `gsColorFor()`.

`updateGsDisplay()` matches the active station **by name** against
`state.ground_station_name` — the MCS state broadcast already reports the active
station across all five, because `_compute_orbital_state()` has always used the
full propagator list. No station lights up when out of contact.

Tests pin the API against `orbit.yaml` (names, geometry, count), assert the RF
merge, assert the API list equals the propagator's list, and assert the removed
literals (`'Iqaluit': '#3b82f6'`, `data-gs="iqaluit"`, `id="gs-troll-panel"`) are
gone from `index.html`.

### (b) Discontinuity detection, modelled on the planner's fix

`_detect_sim_time_jump()` runs before the re-anchor and compares the new
`sim_time` against what the **previous** anchor predicted for this wall instant:

```python
predicted = self._ground_epoch + timedelta(
    seconds=(wall - self._ground_start_wall) * self._sim_speed)
delta_s = (parsed - predicted).total_seconds()
tol = max(self._jump_tolerance_s, 3.0 * 1.5 * max(1.0, abs(self._sim_speed)))
```

The tolerance scales with sim speed, so 120 s of sim time at 60× within one poll
interval is not a jump. Beyond it, the MCS logs the discontinuity, counts it,
records `last_jump_utc` / `last_jump_delta_s`, and **drops every cache whose
meaning is tied to the old epoch**: the schedule report, the S15 store status and
the S6 dump/check history. The orbit propagator is reset at the new epoch
immediately rather than waiting for the next state poll.

The parameter cache is deliberately **kept**: its values are still the
spacecraft's last known state and its ages are wall-clock based, so the chart
staleness gate (defect #65) keeps working across the jump instead of blanking
every plot. The browser clears the S11 table when the broadcast stops carrying a
schedule, so a dropped report shows as "press Refresh List" rather than as stale
execution times.

`sim_time_jump: {count, last_utc, last_delta_s}` rides on the state broadcast for
the UI, alongside the simulator health fields the MCS now also absorbs from the
endpoint it was already polling — `engine_fault`, `engine_fault_count`,
`tm_rate_bps`, `rt_tm_backlog` and `sc_mode` — all tagged `source: sim_state` so
no display can present them as telemetry.

## Files

- `packages/smo-mcs/src/smo_mcs/server.py` — `ground_stations()`, `_station_id`,
  `_GS_COLORS`, `GET /api/ground-stations`; `_detect_sim_time_jump`,
  `_ingest_sim_health`, jump + health fields on the state broadcast
- `packages/smo-mcs/src/smo_mcs/static/index.html` — generated station cards,
  selector and legend; `loadGroundStations` / `renderGsPanels` /
  `renderContactLegend` / `gsColorFor`; name-matched `updateGsDisplay`; schedule
  cleared from the state message
- Tests: `tests/test_mcs/test_schedule_memory_stations.py`
  (`TestGroundStations`, `TestSimTimeJumpDetection`),
  `tests/test_mcs/test_mcs_fdir_page.py` (`TestGroundStationPanels`)

## Related

- Planner defect #59 — the same discontinuity problem and the implementation this
  one follows (`_refresh_sim_anchor` in `smo_planner/server.py`). The planner
  invalidates contact and ground-track caches; the MCS invalidates schedule,
  store and memory-report caches, because those are the epoch-bound things it
  holds.
- Defect #42 — the orbit.yaml / planning-ground_stations.yaml duplication the
  station endpoint merges (and the MCS already cross-checks and warns on).
- Defect #65 — the chart staleness gate, whose wall-clock ages are why the
  parameter cache survives a jump.
- `configs/eosat1/orbit.yaml` and `planning/ground_stations.yaml` are read-only
  to this pass.

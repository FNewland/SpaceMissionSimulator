# Mission Planner Audit — EO-Sat-1 Simulator Suite

Read-only investigation, 2026-07-28. Repo: `/home/claude/repos/SpaceMissionSimulation`.
All paths repo-relative. Line numbers verified by reading the cited files.

**Headline:** four of the six reported problems share ONE root cause. The planner UI
(`packages/smo-planner/src/smo_planner/static/index.html`) treats `time_source == "sim"`
as "the backend is not real, show demo data" and unconditionally substitutes hard-coded
JavaScript fixtures for the server's real, SGP4-computed contact windows and the real
(empty) schedule. Since `"sim"` is the built-in **default** time source
(`smo-common/src/smo_common/config/schemas.py:23`), the shipped default configuration
always shows fake data. The Python backend is largely correct; the front end discards it.

---

## Q1 — GROUND STATION ALIGNMENT

### Verdict
**CONFIRMED — the planner UI ground-station list is hard-coded JavaScript and is not the
configured network.** The UI *does* have a `/api/ground-stations` fetch, but it is used
only for the map markers and the activity-modal dropdown. The **timeline** and the
**contact table** — the two places the user actually reads station names — are driven by a
separate hard-coded constant `GROUND_STATIONS_FALLBACK` containing Svalbard / Troll /
Inuvik / O'Higgins, and by synthetic contact windows generated in the browser. The real
configured network is Iqaluit + Troll.

### Where every ground-station list lives

| # | Location | Content | Who consumes it |
|---|---|---|---|
| 1 | `configs/eosat1/orbit.yaml:21-32` | **Iqaluit, Troll** (lat/lon/alt/min_el) — declared canonical in the header comment (lines 5-12) | simulator engine, planner server, MCS |
| 2 | `configs/eosat1/planning/ground_stations.yaml:12-33` | Iqaluit, Troll — geometry mirrored + RF augmentation (G/T, freqs, TX power) | RF tooling; MCS merges and cross-checks |
| 3 | `configs/eosat1/mission.yaml:31` | `stations: [Iqaluit, Troll]` — name list only | display/metadata |
| 4 | `packages/smo-common/src/smo_common/config/schemas.py:41-45` | **`GroundStationConfig` field defaults = "Svalbard" 78.229/15.407/0.458/5.0** | pydantic defaults only — never produces a station unless a config omits the fields; `ground_stations` itself defaults to `[]` (schemas.py:54) |
| 5 | `.../static/index.html:1166-1167` | Iqaluit, Troll — JS **fallback** used only if `/api/ground-stations` throws | map markers + `actStation` dropdown |
| 6 | **`.../static/index.html:1379-1384`** | **`GROUND_STATIONS_FALLBACK` = Svalbard, Troll, Inuvik, O'Higgins** with fabricated `contactOffset`/`contactDur`/`maxEl` | **timeline rows (index.html:1591, 1644, 1690) and synthetic contacts (1398-1427)** |
| 7 | `.../static/index.html:1386-1396` | `SAMPLE_ACTIVITIES` referencing Svalbard/Inuvik/O'Higgins | seeded fake schedule (see Q3) |
| 8 | `.../static/index-wide.html:1134-1135` | Iqaluit, Troll — the wide UI's fallback list is **correct** | `/wide` route only |
| 9 | `files/config.py:35-39` | Legacy single-station `GS_NAME = "Svalbard"` | dead legacy module, not imported by any package |
| 10 | `packages/smo-mcs/.../static/index.html:933-934, 1619-1620, 5801` | Iqaluit/Troll hard-coded in MCS UI | MCS display (separate defect class) |

### (a) What drives the planner UI display
Two different lists, in the same page:
- **Map markers + activity-modal station dropdown**: `loadGroundStations()`
  (`index.html:1155-1173`) fetches `/api/ground-stations` → correct (Iqaluit, Troll).
- **Timeline canvas Y-axis labels, timeline rows, and the contacts table**:
  `GROUND_STATIONS_FALLBACK` (`index.html:1379-1384`, used at 1591, 1644, 1690) →
  **Svalbard, Troll, Inuvik, O'Higgins, always, unconditionally.** It is never
  overwritten from the API. Even when real API contacts *are* used, line 1644
  (`GROUND_STATIONS_FALLBACK.findIndex(...)`) drops any contact whose `gs_name` is not in
  that hard-coded list — so a real **Iqaluit** pass would be silently discarded from the
  timeline (`return` on `stIdx === -1`).

Worse, the contact data itself is fake by default. `pollAPIs()` at
`index.html:1506-1510`:
```js
  // time_source is authoritative: in sim mode always treat contacts as
  // simulated regardless of the API payload.
  if (timeSource === 'sim') {
    useSimulatedContacts = true;
  }
```
`timeSource` comes from `/api/time-source` (`index.html:1462-1474`), whose value defaults
to `"sim"` (`schemas.py:23`, `server.py:74-79`). So in the default deployment the planner
fetches `/api/contacts` successfully, parses it, and then **throws the result away** in
favour of `generateSimulatedContacts()` (`index.html:1398-1427`), which manufactures
passes for the four hard-coded stations at fixed offsets with `Math.random()` elevations.

### (b) What drives the planner's pass computation
Correct and config-driven. `PlannerServer.__init__` (`server.py:97-105`) builds
`self._gs_list` from `load_orbit_config(...)` → `orbit.yaml` only. `_compute_contacts`
(`server.py:319-346`) loops `self._gs_list` and calls `OrbitPropagator.contact_windows(gs=gs)`
per station, merging and sorting by AOS. `/api/contacts` (`server.py:416-429`) and
`/api/ground-stations` (`server.py:434-441`) both serve that list. **The backend is right.**

### (c) What drives the simulator's contact gating
Also correct and config-driven. `SimulationEngine.__init__` (`engine.py:111-124`) builds
`GroundStation` objects from `self._orbit_cfg.ground_stations` (orbit.yaml) and passes all
of them to `OrbitPropagator`; the propagator gates on *any* station above its mask and
tags `OrbitState.active_gs` (`propagator.py:39-47`). Covered by
`tests/test_simulator/test_multi_station_contacts.py:47-89`.

### The divergence, precisely
Backend (planner + simulator + MCS) = **Iqaluit, Troll** from `orbit.yaml`.
Planner UI timeline/contacts table = **Svalbard, Troll, Inuvik, O'Higgins**, hard-coded in
JS at `index.html:1379-1384`, with fabricated pass times. Only "Troll" is common to both,
which is why the display looks half-plausible. Nothing in the UI ever reconciles the two.
This is a *strictly worse* instance of the register's DI-04 (`docs/review_2026-07/design_issues.md:40-46`),
which covered the three YAML files but not the JS constant.

Secondary: `index-wide.html` was corrected (Iqaluit/Troll at 1134-1135) but `index.html` —
the one served at `/` (`server.py:400-404`) — was not. The two UIs disagree.

### Fix proposal
1. Delete `GROUND_STATIONS_FALLBACK` and `generateSimulatedContacts()` from
   `index.html`. Make `groundStations` (populated by `loadGroundStations()` from
   `/api/ground-stations`) the single source for timeline rows, the contacts table, the
   dropdown and the map. `drawTimeline` derives `gsCount` and row labels from
   `groundStations.length` / `groundStations[i].name`.
2. Delete the two `if (timeSource === 'sim') useSimulated* = true;` blocks
   (`index.html:1506-1510` and `1534-1538`, and the twins at `index-wide.html:1234, 1252`).
   `time_source` selects a *clock*, not a *data fidelity*; conflating them is the bug.
   Keep the SIM/LIVE badge (it is honest and useful) but drive the data from the API.
3. When `/api/contacts` returns an empty list, render "No contact windows — check
   configuration" rather than fabricating passes. (Same lesson as DI-03.)
4. Make `loadGroundStations()` a hard dependency of first paint: `await` it before the
   first `drawTimeline()` in `init()` (`index.html:2233-2258`), and drop the JS fallback
   list entirely — a failed fetch should show an error, not invent a network.
5. Keep `orbit.yaml` canonical. `/api/ground-stations` already returns full geometry
   (name/lat/lon/alt/min_elevation, `server.py:436-440`); no schema change needed.
6. Optional hardening: change `GroundStationConfig` field defaults (`schemas.py:41-45`)
   from the Svalbard values to required fields, so a malformed config fails loudly
   instead of silently materialising a phantom Svalbard.

**Effort: S** (front-end only, ~150 lines deleted, no backend change).

### Pass statistics — 2 stations vs the UI's 4 vs the 5-station union
Run: `python tools/pass_gap_analysis.py --config <tmp> --hours 48`, TLE and T-0
(2026-03-10T00:00:00Z) from `configs/eosat1/orbit.yaml`; orbit period 94.5 min
(half-orbit 47.2 min); 5° masks throughout. Temp configs written to `/tmp/gscfg/`
(nothing written inside the repo). Station coords for the added stations taken from
`index.html:1380-1383`; Svalbard alt 0.458 km from `schemas.py:44`.

| Network | Passes/48 h | Passes/day | Median gap | Mean gap | Max gap | Gap / half-orbit |
|---|---|---|---|---|---|---|
| **Current 2** (Iqaluit, Troll) | 39 | 19.5 | **84.3 min** | 67.2 | **147.5 min** | 1.79 |
| **UI's 4** (Svalbard, Troll, Inuvik, O'Higgins) | 86 | 43.0 | **34.4 min** | 36.3 | **84.3 min** | 0.73 |
| **Union of 5** (+ Iqaluit) | 103 | 51.5 | **33.2 min** | 34.8 | **78.8 min** | 0.70 |

Per-station (48 h): Iqaluit 17 passes, 6.8 min mean, median max-el 16.6°; Troll 22, 8.1 min,
27.1°; Svalbard 25, 7.8 min, **42.2°** (best — highest latitude, best SSO geometry);
Inuvik 20, 7.6 min, 20.7°; O'Higgins 19, 6.7 min, 15.5°.

Derived contact time: 2 stations = **147 contact-min/day**; UI's 4 = 326 min/day (2.2×);
union of 5 = **384 min/day (2.6×)**. Maximum outage collapses from 2 h 28 min to 1 h 19 min.
So the four-station display is not merely cosmetically wrong — it implies a *2.6× larger
downlink budget and half the worst-case outage* than the mission actually has. That is a
training-integrity problem, not a cosmetic one.

---

## Q2 — BREAKPOINT → PLANNER SYNC

### Verdict
**PARTIAL — the clock and the live spacecraft marker follow a breakpoint jump within
~1.5 s; the contact windows and the ground track do NOT, and stay wrong for up to 10
wall-clock minutes. There is no discontinuity detection anywhere.**

### Evidence
Breakpoint load restores two independent clocks:
`breakpoints.py:92` sets `eng._sim_time` from the snapshot, and `breakpoints.py:117-127`
calls `orbit.reset(orbit_utc)` (falling back to `sim_time` for old snapshots). Both can
move arbitrarily far **backward**.

Planner anchor mechanism (`server.py`):
- `_sim_anchor_loop` (`server.py:160-173`) calls `_refresh_sim_anchor(force=True)` every
  **1.5 s**, off-thread. Started only in sim mode (`server.py:179-180`).
- `_refresh_sim_anchor` (`server.py:270-294`) GETs `sim_state_url` (default
  `http://<host>:8080/api/state`), reads `sim_time` and `speed`, and **overwrites** the
  anchor unconditionally (lines 288-293). It never compares the new value to the old.
- `_get_now` (`server.py:296-317`) returns `anchor_time + (wall_now - anchor_wall) * speed`.

Consequences:
1. **`_get_now()` is correct within ≤1.5 s of a jump** (forward or backward) — the anchor
   is simply replaced. Good.
2. **`_live_prop` self-heals.** `_get_spacecraft_state` (`server.py:367-370`) calls
   `self._live_prop.reset(self._get_now())` on **every** request, so the live position
   marker follows the jump. `OrbitPropagator.reset` (`propagator.py:81-87`) is stateless
   w.r.t. history, so there is no drift. Good — but this is accidental robustness, not
   design: nothing resets `_live_prop` *because* a jump happened.
3. **`_contacts_cache` and `_ground_track_cache` do NOT self-heal.** They are populated in
   `start()` (`server.py:182-183`) and then only by the loop at `server.py:248-252`:
   ```python
   while True:
       await asyncio.sleep(600)
       self._compute_contacts()
       self._compute_ground_track()
   ```
   A fixed **600 wall-clock seconds**, independent of sim speed and of any jump. After
   loading a breakpoint that moves sim time back 6 hours, `/api/contacts` keeps serving
   windows computed for the *future*: every AOS is in the past relative to the new "now",
   so the planner shows zero upcoming passes for up to 10 minutes. The ground track
   (`/api/ground-track`, `server.py:443-482`, `use_cache` branch at 456) shows a stale
   3-hour arc, so the past/future colouring in the UI is inverted for the whole track.
   Cascading: `/api/budget/power`, `/api/budget/data` (`server.py:696-720`) and
   `/api/schedule/validate` (`server.py:625-634`) all consume `_contacts_cache`, so the
   budgets and validation are wrong for the same window.
   (`docs/review_2026-07/design_issues.md:196` already flagged the fixed 600 s as a
   sim-speed problem; the breakpoint case makes it a correctness problem.)
4. **No BACKWARD/discontinuous jump detection exists.** Grep for any comparison of a new
   anchor against the previous one returns nothing; `_refresh_sim_anchor` has no
   `if abs(parsed - expected) > tol` branch. The MCS has the same gap (its comment at
   `smo-mcs/server.py:906` explicitly accepts "bounded drift = poll interval" for
   "pause / speed change / breakpoint load").
5. Pausing is handled correctly by accident: pause sets `engine.speed = 0.0`
   (`engine.py:1936, 1940`), which is reported in `/api/state` (`engine.py:2285`) and makes
   `_get_now()`'s extrapolation term zero.

### Fix proposal
In `_refresh_sim_anchor` (`server.py:270-294`), before overwriting the anchor, compute the
sim time the old anchor *predicted* for this wall instant and compare:

```python
predicted = None
if self._sim_anchor_time is not None:
    predicted = self._sim_anchor_time + timedelta(
        seconds=(wall - self._sim_anchor_wall) * self._sim_anchor_speed)
jumped = predicted is not None and abs((parsed - predicted).total_seconds()) > JUMP_TOL_S
```
with `JUMP_TOL_S` ≈ `max(5.0, 3 * refresh_period * speed)`. On `jumped`:
- log at INFO ("sim clock discontinuity: −6:00:00, recomputing plan products"),
- set `self._plan_dirty = True`.

Then (a) move the recompute out of `start()`'s `while True` into its own task that wakes on
`self._plan_dirty` or on a **sim-time**-based interval (e.g. recompute when
`_get_now() - _contacts_computed_at > 30 sim-minutes`), and (b) call
`self._live_prop.reset(now)` explicitly on the jump rather than relying on per-request
reset. Optionally expose `jumped_at` on `/api/time-source` so the UI can flash "plan
recomputed — sim time jumped".

Cheap partial fix if the above is too much: make the recompute condition sim-time-aware —
in `_handle_contacts`/`_handle_ground_track`, if
`abs(_get_now() - _contacts_computed_at) > 30 min`, recompute synchronously before serving.

**Effort: S** for the jump detection + dirty flag; **M** including the sim-speed-aware
recompute scheduler and the UI notification.

---

## Q3 — DEFAULT PLAN STATE

### Verdict
**The server starts EMPTY — the pre-populated activities the user sees are nine hard-coded
JavaScript fixtures that the UI displays instead of the real schedule whenever
`time_source == "sim"` (the default).** So "start empty by default" is *already* the
backend behaviour; the work is entirely deleting the front-end fixture path and adding an
explicit load control.

### Evidence
- `ActivityScheduler.__init__` (`activity_scheduler.py:33-36`) sets
  `self._schedule: list[dict] = []`. Nothing seeds it.
- `PlannerServer.__init__` (`server.py:136`) constructs
  `ActivityScheduler(self._activity_types)` — the argument is the **type catalogue**
  (`configs/eosat1/planning/activity_types.yaml`, loaded at `server.py:124-133`), which
  becomes `self._types`, a lookup table for defaults in `add_activity`
  (`activity_scheduler.py:39-56`). **It creates zero instances.** Confirmed: the ten
  entries in `activity_types.yaml` carry no `start_time` field at all.
- There is **no persistence layer**. No load/save of a plan file; `workspace/` contains
  only `breakpoints/`, `dumps/` and `audit/` — no plan directory. No `_schedule` is ever
  read from disk (grep for plan persistence in `server.py` returns nothing).
- `/api/schedule` (`server.py:431-432`) therefore returns `{"schedule": []}` on a fresh
  start.
- The activities the user sees come from `SAMPLE_ACTIVITIES` (`index.html:1386-1396`):
  Imaging Pass @ Svalbard +15 min, Data Dump @ Inuvik +45 min, Orbit Maintenance,
  Calibration @ Troll, Data Dump @ Svalbard, Imaging Pass @ O'Higgins, Housekeeping,
  Software Upload @ Svalbard, Calibration @ Inuvik. Materialised by
  `generateSimulatedActivities()` (`index.html:1429-1439`) during `init()`
  (`index.html:2235`) and selected by `useSimulatedSchedule`, which
  `index.html:1534-1538` forces to `true` whenever `timeSource === 'sim'`.
- These fixtures are also **not real activities**: they have no `id`, so clicking upload
  hits the guard at `index.html:1875` (`alert('Activity has no ID (simulated?)')`). They
  cannot be uploaded, validated, or costed — they are decoration that looks like a plan.
- Note "Orbit Maintenance" (`index.html:1389`) is not even a defined activity type;
  `activity_types.yaml` has no such entry.

### Is there already a "standard activity set"?
**No instances anywhere.** `activity_types.yaml` is purely a type catalogue: ten types
(`imaging_pass`, `data_dump`, `calibration`, `housekeeping_collection`, `software_upload`,
`momentum_desaturation`, `thermal_rebalance`, `load_shedding`, `fdir_recovery`) each with
`duration_s`, `power_w`, `data_volume_mb`, `command_sequence`, `pre_conditions`,
`conflicts_with`. It is exposed read-only via `/api/activity-types` (`server.py:632-634`)
and used to populate the "Add Activity" modal's type dropdown. The `SAMPLE_ACTIVITIES` JS
list is the closest thing to a template set, and it is inconsistent with the catalogue.

### What needs to change
1. Delete `SAMPLE_ACTIVITIES`, `generateSimulatedActivities()`, `simulatedActivities`,
   `useSimulatedSchedule` and the `if (timeSource === 'sim')` override
   (`index.html:1386-1396, 1429-1439, 1054, 1534-1538, 1791`). The activity list then
   renders `apiSchedule`, which is empty on start — the requested behaviour, for free.
2. Add a **"Load standard activities"** button next to "Add Activity". Backend:
   `POST /api/schedule/load-template` taking `{template: "nominal_24h", t0?: iso}`. It
   should build instances from a **new config file**, e.g.
   `configs/eosat1/planning/plan_templates.yaml`:
   ```yaml
   templates:
     - id: nominal_24h
       description: "Standard 24 h ops plan"
       activities:
         - type: imaging_pass       # must exist in activity_types.yaml
           anchor: next_daylight_target   # or: absolute | offset_from_now | pass_relative
           offset_min: 0
         - type: data_dump
           anchor: pass_relative    # snap to the next real contact window
           pass_index: 0
           offset_min: 1
   ```
   Resolve `anchor` against `_get_now()` and `_contacts_cache`, so a loaded template lands
   on *real* passes at the *current sim time* — which is exactly what the JS fixtures fake.
   Reuse `ActivityScheduler.add_activity(type_name, start_time)`; type defaults
   (duration/power/data/commands) are already filled from `activity_types.yaml`.
3. Add `DELETE /api/schedule` (clear all) so the operator can get back to empty; today
   only per-id delete exists (`server.py:607-620` → `_handle_delete_activity`).
4. Optionally add plan persistence (`POST /api/plan/save`, `GET /api/plan/load`) writing to
   `workspace/plans/*.json`, mirroring the breakpoint pattern. Not required for the ask.

**Effort: S** for items 1+3 (delete fixtures, add clear-all).
**Effort: M** for item 2 (template config + anchor resolution + UI control).

---

## Q4 — SIM TIME

### Verdict
**Backend: YES, correctly, with well-defined precedence and fallbacks. Front end: NO — the
planner UI has no notion of sim time at all. Every clock, "NOW" marker, past/future split
and default activity start time in `index.html` is browser wall-clock.** This is masked in
a default run because `SimulationEngine._sim_time` is itself initialised to wall clock
(`engine.py:97`), and becomes visibly wrong the moment a breakpoint, a scenario, or a
speed ≠ 1 moves sim time away from it.

### The mechanism, precisely
**Resolution order** (`server.py:64-87`, documented at `server.py:56-63`):
1. explicit constructor arg, from CLI `--time-source {sim,real}` (`server.py:898`);
2. env `SMO_TIME_SOURCE`;
3. `mission.yaml`'s `time_source` field (`schemas.py:23`, default `"sim"`);
4. built-in default `"sim"`.
Same pattern for `sim_state_url`: ctor arg → `SMO_SIM_STATE_URL` → `mission.yaml`
`sim_state_url` → `http://{connect_host}:8080/api/state` (`server.py:80-85`).
`/api/time-source` (`server.py:636-643`) reports the resolved value.

**Anchor + extrapolation** (`server.py:88-94, 254-317`): the background loop refreshes
every 1.5 s; `_get_now()` returns `anchor_sim_time + (wall_now − anchor_wall) × speed`.
`_refresh_sim_anchor` is additionally self-rate-limited to 1/s when not forced
(`server.py:275-277`).

**Where sim time is used server-side:** `_compute_contacts` (`server.py:322`),
`_compute_ground_track` (`server.py:351`), `_get_spacecraft_state` (`server.py:369`),
`/api/ground-track` non-cached branch (`server.py:457`), `/api/imaging/opportunities`
(`server.py:741`). An explicit `?epoch=` query overrides `_get_now()` for
`/api/contacts` (`server.py:417-423`).

### Exactly when wall clock is used instead
1. `time_source == "real"` (CLI/env/config) — by design (`server.py:308-309`).
2. **`time_source == "sim"` but the simulator was never reachable**: `_sim_anchor_time`
   stays `None` and `_get_now()` silently returns `datetime.now(timezone.utc)`
   (`server.py:312-314`). No warning is logged at this site, and `/api/time-source` still
   reports `"sim"`, so the UI badge says SIM while the data is wall-clock. **This
   fallback is silent and should not be** — it should surface as
   `{"time_source": "sim", "sim_reachable": false}`.
3. Between the sim going away and the next successful refresh, `_get_now()` keeps
   extrapolating from the *stale* anchor at the last known speed — it does not degrade to
   wall clock, it drifts. Unbounded: there is no anchor-age ceiling.
4. Construction-time race: `self._live_prop.reset(self._get_now())` runs at
   `server.py:122`, before `start()` launches the anchor loop (`server.py:179-180`). The
   in-line `_refresh_sim_anchor` inside `_get_now` does one synchronous
   `urllib.request.urlopen` with a 1.5 s timeout on the event loop
   (`server.py:263-265`) — already flagged as blocking I/O in a handler at
   `docs/review_2026-07/design_issues.md:193`.

### Wall-clock in the UI where sim time is intended (all `static/index.html`)
| Line | Code | Impact |
|---|---|---|
| **1365-1375** | `updateClock()` — `const now = new Date()` | The big UTC clock/date in the header shows the **operator's wall clock**, never sim time, in every mode. `/api/spacecraft-state` already returns `utc` (`server.py:384`) — the sim time is available and ignored. |
| **1594, 1642-1660, 1707** | `drawTimeline()` — `const now = Date.now()` | The timeline's 24 h window origin and the red "NOW" line are wall-clock. Against real (sim-time) contacts, every pass renders at the wrong X or off-canvas. |
| **1726-1735** | `updateContactsTable()` — `Date.now()` filter | Real contacts are filtered "upcoming" against wall clock; after a backward breakpoint jump the whole table empties. |
| **1245-1252** | `drawGroundTrack()` — `nowMs = Date.now()` vs `pt.utc` (sim time) | Past/future track colouring inverts whenever sim time ≠ wall clock. |
| **1965-1968** | `openModal()` prefills `actStartTime` with local wall clock | **Most damaging.** A new activity defaults to a start time in wall-clock "now". If sim time is at T-0 2026-03-10 (`orbit.yaml:16`) and wall clock is 2026-07-28, the operator schedules an activity ~4.5 months in the future; it never executes and never appears in any pass window. |
| 1399, 1430 | `generateSimulatedContacts/Activities()` | Moot once the fixtures are deleted (Q1/Q3). |
| 1133 | `updateTerminator()` — `new Date()` | Day/night terminator drawn for wall clock, not sim epoch — inconsistent with the eclipse state reported by the API. |

### Fix proposal
1. Add a JS `simNow()`: maintain `simEpochMs` + `simSpeed` + `anchorWallMs` in the page,
   refreshed from `/api/spacecraft-state`'s `utc` (already returned) or from an extended
   `/api/time-source` that also returns `sim_time` and `speed`. `simNow()` returns
   `simEpochMs + (Date.now() − anchorWallMs) * simSpeed`.
2. Replace every `Date.now()` / `new Date()` at 1133, 1245, 1365, 1594, 1726, 1965 with
   `simNow()`. Prefill the modal from `simNow()`.
3. Server: return `sim_reachable` from `/api/time-source` and log a WARNING (once, not per
   call) when `_get_now()` takes the wall-clock fallback at `server.py:312-314`. Add an
   anchor-staleness ceiling (e.g. > 60 s → mark degraded).
4. Server: make `_get_now()` read-only against the cached anchor; leave all refreshing to
   the background task (removes the blocking urlopen from handlers).

**Effort: S** for the server-side honesty items (3, 4); **M** for the UI sim-clock (1, 2),
since it touches every time-consuming draw path.

---

## Q5 — DATA BUDGET

### Verdict
**A data budget EXISTS and is computed correctly-ish server-side — but the UI panel never
renders it because of a falsy-zero guard, and the model's constants are hard-coded and
contradict the mission config by up to 20×. The user's report "there is no data budget" is
accurate as an observation and points at a one-line UI bug plus a real modelling gap.**

### What is tracked today

| Budget | Where | Status |
|---|---|---|
| **Power / energy / SoC** | `budget_tracker.py:63-176` `compute_power_budget()` | Works. Per-pass SoC at AOS/LOS, eclipse fraction from ground track (`:311-336`), real solar beta from the propagator (`:338-378`), activity power overlay (`:283-309`), warnings below 25 % (`:151-160`). Rendered — `index.html:2098-2145`. |
| **Data volume / onboard storage** | `budget_tracker.py:178-268` `compute_data_budget()` | Computed but **invisible** (see below). Returns `onboard_data_mb`, `storage_capacity_mb`, `utilization_percent`, per-pass `capacity_mb`/`planned_dump_mb`, `planned_generation_mb`, `planned_downlink_mb`, warnings. Served at `/api/budget/data` (`server.py:707-720`). |
| **Storage-capacity constraint** | `constraint_checkers.py:531-640` `DataVolumeConstraintChecker` | Independent second implementation: running onboard total, capacity violation, 10 % margin warning, downlink subtraction during dumps. |
| **Duty cycle / thermal** | `constraint_checkers.py` `ThermalConstraintChecker`; `activity_types.yaml` `thermal_constraints.max_continuous_minutes` / `max_per_orbit_minutes` / `required_cooldown_s` | Works (imaging 30 min/orbit cap). |
| **Momentum** | `AOCSConstraintChecker` | Works. |

### Why the user sees nothing
`index.html:2163`:
```js
  if (!panel || !dataBudgetData.utilization_percent) return;
```
`utilization_percent` is a **number**, and `0` is falsy. On a fresh planner the schedule is
empty (Q3) → `planned_generation_mb = 0` → `onboard_mb = 0` → `utilization_percent = 0.0`
→ the function returns before touching the DOM, and the panel keeps its placeholder
`<div>Loading...</div>` from `index.html:927` **forever**. The Power panel escapes this
only because its guard tests `initial_soc`, which is 80.0 and truthy
(`index.html:2100`). Two adjacent panels, same bug pattern, one happens to be harmless.
Even after activities are added, in sim mode the *server-side* schedule may be empty while
the UI shows JS fixtures (Q3), so the budget stays 0.

### Constants are hard-coded and contradict config

| Quantity | `budget_tracker.py` | Actual config | Divergence |
|---|---|---|---|
| Onboard storage | `ONBOARD_STORAGE_MB = 1024.0` (`:31`) | `configs/eosat1/subsystems/payload.yaml` `total_storage_mb: 20000.0` | **20×** |
| Storage (2nd impl) | `DataVolumeConstraintChecker(storage_capacity_mb=20000.0)` (`constraint_checkers.py:536`) | 20000 ✔ | The two planner modules disagree with **each other** |
| Image size | `IMAGING_DATA_MB = 800` (`imaging_planner.py:31`), `data_volume_mb: 800` (`activity_types.yaml` imaging_pass) | `payload.yaml image_size_mb: 800.0` ✔ | consistent, but triplicated |
| Downlink rate | `DOWNLINK_RATE_BPS = 64000` (`:32`) | `subsystems/ttc.yaml tm_rate_hi_bps: 64000` ✔ | value matches, but this is the **S-band TT&C housekeeping rate**, not a payload downlink |
| Downlink rate (2nd impl) | `downlink_rate_mbps = 0.5` (`constraint_checkers.py:538`) | — | **~10× the tracker's 51.2 kbps.** Two planner modules, two rates |
| Payload data rate | absent | `payload.yaml data_rate_mbps: 80.0` | never read by the planner |
| Battery | `BATTERY_CAPACITY_WH = 120.0` (`:29`) | `subsystems/eps.yaml` / `schemas.py:63` `capacity_wh: 120.0` ✔ | but the module docstring (`:12`) says "40 Ah at 28 V = 1120 Wh" — a 9× stale comment |
| Base power | `32.0 W` (`:25-26`) | docstring says 95 W (`:9-10`), `schemas.py:74` default `platform_idle_power_w: 95.0`, `eps.yaml:21` `70.0` | docstring and code disagree |
| Link budget | `FREQ_GHZ = 0.4015` UHF (`:35`) | `ground_stations.yaml` S-band 2200 MHz; matches `ttc.yaml dl_freq_mhz: 401.5` | instance of DI-05 |
| S15 stores | not modelled | `tm_storage.py:33-38` `DEFAULT_STORES` = HK 18000 pkt circular, Event 2000, **Science 10000 linear**, Alarm 500 — in **packets**, not MB, and not in any YAML | planner has no view of the store the `data_dump` activity actually dumps (`activity_types.yaml` `store_id: 3`) |

### The model is physically infeasible as configured
One `imaging_pass` generates **800 MB**. Effective downlink = 64 kbps × 0.8 = 51.2 kbps.
Total contact time with the real 2-station network = **147 min/day** (Q1) ⇒ ~451 Mbit/day
⇒ **≈54 MB/day**, before the elevation-efficiency derating (`budget_tracker.py:207-217`,
typically 0.6–0.9) which brings it to **≈38 MB/day**. One image therefore takes
**~21 days** to downlink. Even the 5-station network yields ~99 MB/day → ~8 days/image.
The plan can never close. Any honest data budget will immediately raise a permanent
"onboard data exceeds downlink capacity" alarm — which is correct behaviour and a genuinely
useful teaching point, but it means the config needs a payload downlink path
(`payload.yaml data_rate_mbps: 80.0` suggests an X-band channel that is nowhere configured
on the ground-station side).

### Concrete design for a data budget

**Model** — event-driven onboard-store integration over the 24 h horizon, replacing the
current "sum generation, sum capacity, subtract" arithmetic (`budget_tracker.py:189-256`)
which ignores ordering entirely (it can report a comfortable margin for a plan that
overflows at hour 3 and drains at hour 20):

```
state:  onboard_mb per store (science, hk, event, alarm)
events: sorted merge of activity starts/ends and contact AOS/LOS
step over each interval [t0,t1]:
  generation:  sum over active activities of  data_rate_mb_s(activity) * dt
               where data_rate_mb_s = type.data_volume_mb / type.duration_s
               (piecewise, so a 300 s imaging pass fills the store over 300 s,
                not instantaneously)
  background:  hk_rate_mb_s * dt                  # HK always accumulates
  downlink:    if a dump activity overlaps a contact window:
                 min(onboard_mb, rate_mb_s(el(t), station) * dt)
               where rate_mb_s comes from the link budget, not a constant
  clamp:       onboard = min(onboard, store_capacity_mb)   -> record overflow_mb
outputs: time series (t, onboard_mb per store), peak utilisation + when,
         per-pass volume dumped vs pass capacity, total generated / downlinked /
         lost-to-overflow, first-overflow time, end-of-horizon backlog,
         days-to-drain at current plan rate
```

**Where the numbers must come from (config, not constants):**
- image size per capture → `payload.yaml image_size_mb` (currently 800.0)
- payload instantaneous rate → `payload.yaml data_rate_mbps` (80.0)
- onboard store capacity → `payload.yaml total_storage_mb` (20000.0) for the science store;
  S15 packet stores from `tm_storage.py:33-38` — these should move to
  `configs/eosat1/subsystems/obdh.yaml` (or a new `telemetry/packet_stores.yaml`) with
  both `capacity_packets` and a `mean_packet_bytes` so packets convert to MB
- downlink rate per pass → `ttc.yaml tm_rate_hi_bps` / `tm_rate_lo_bps` for TT&C, **plus a
  new `payload_downlink_rate_bps` per station** in `planning/ground_stations.yaml`
  (the file that already owns `g_t_db`, `antenna_diameter_m`, `rx_freq_mhz`)
- pass duration + max elevation → `/api/contacts` (`server.py:416-429`), already real
- protocol overhead → new `ttc.yaml` field, replacing `PROTOCOL_OVERHEAD = 0.20`
- HK background rate → derive from `telemetry/hk_structures.yaml` cadences (the
  computation is already written out as a comment in `tm_storage.py:17-31`: ~1.64 pkt/s)

**Where it plugs in:**
- `BudgetTracker.__init__` gains `payload_cfg`, `ttc_cfg`, `station_rf` params; `server.py`
  passes them (it already calls `load_orbit_config`; add `load_all_subsystem_configs`).
  Delete the module-level constants at `budget_tracker.py:25-41`.
- `DataVolumeConstraintChecker` (`constraint_checkers.py:531`) should be *reimplemented as
  a thin wrapper* over the new `compute_data_budget()` output rather than duplicating the
  arithmetic with different constants — one model, two presentations (panel + violations).
- New violation types: `STORAGE_OVERFLOW` (error, with the overflow timestamp and the MB
  lost), `DOWNLINK_INFEASIBLE` (error — backlog grows monotonically over the horizon),
  `DUMP_WITHOUT_PASS` / `PASS_WITHOUT_DUMP` (warning — the latter is a wasted-opportunity
  check the current code cannot express).

**What the UI shows (replacing `index.html:2161-2205`):**
- a stacked area chart of onboard MB vs time over 24 h, with the capacity line, contact
  windows shaded, and overflow regions in red — mirroring the timeline's X axis so the
  operator reads storage against passes at a glance;
- headline tiles: peak utilisation %, first-overflow time (or "none"), 24 h generated /
  downlinked / backlog, and **days-to-drain**;
- a per-pass table: station, AOS, duration, max el, capacity MB, planned dump MB,
  utilisation % of the pass — the data is already in
  `compute_data_budget()["pass_downlink"]` and is currently rendered *nowhere*;
- warnings inline, as today.
- **Immediately**: change the guard at `index.html:2163` to
  `if (!panel || dataBudgetData.utilization_percent === undefined) return;` so an empty
  plan renders "0 / 20000 MB, 0 %" instead of "Loading..." forever.

**Effort: S** for the falsy-zero guard (one line — this alone makes the existing budget
visible). **M** for config-driving the constants and unifying the two implementations.
**L** for the full event-driven multi-store model plus the timeline chart, and for adding
the payload downlink path to the RF config (which also forces a decision about DI-05's
UHF-vs-S-band contradiction).

---

## Q6 — IMAGING REGION SELECTION

### Verdict
**Targets are a fixed YAML list of lat/lon bounding boxes. There is no map drawing, no
lat/lon entry, and no target creation in the UI at all — the operator can only click
"Schedule" on an opportunity the server computed. The geometry check is a crude
axis-aligned box-plus-margin test, and the only "constraint" applied is a binary eclipse
flag standing in for solar elevation. No off-nadir, no slew, no cloud, no revisit
enforcement.**

### How it works today, end to end
1. **Targets are config.** `configs/eosat1/planning/imaging_targets.yaml` — **7 targets**
   (OCM-001 Gulf Stream … OCM-007), each `{id, name, description, region:{min_lat, max_lat,
   min_lon, max_lon}, priority, revisit_days, min_solar_elevation_deg}`. Loaded once at
   startup: `server.py:151-157` → `ImagingPlanner.load_targets_from_config`
   (`imaging_planner.py:110-121`). **No reload, no add, no edit** — no `POST
   /api/imaging/targets` exists; the router only registers GET `/api/imaging/targets`,
   GET `/api/imaging/opportunities`, POST `/api/imaging/schedule` (`server.py:207-211`).
   `/api/imaging/targets` (`server.py:725-732`) is served but **never fetched by the UI**
   (grep: `index.html` calls only `/api/imaging/opportunities` and `/api/imaging/schedule`)
   — a dead endpoint, matching `defects/16-planner-endpoints-with-no-ui.md`.
2. **Opportunities are auto-generated.** `GET /api/imaging/opportunities`
   (`server.py:734-759`) builds a fresh propagator, predicts a **24 h ground track at 60 s
   steps**, and calls `compute_opportunities` (`imaging_planner.py:127-207`). That walks
   each track point and for each target evaluates `within_swath(lat, lon)`
   (`imaging_planner.py:87-104`): expand the bounding box by half a swath converted to
   degrees (`swath_km / (2 × 111)` in latitude, `/cos(lat)` in longitude) and test whether
   the **sub-satellite point** falls inside. Contiguous in-swath runs become one
   opportunity with `start_utc`/`end_utc`/`duration_s`.
3. **Constraint checks that actually run:** exactly one — `if in_eclipse: skip`
   (`imaging_planner.py:151-160`). Solar elevation is `solar_ok = not in_eclipse`
   (`imaging_planner.py:169`, comment: `# simplified check`). The per-target
   `min_solar_elevation_deg: 10.0` from the YAML is parsed into
   `ImagingTarget.min_solar_elevation_deg` (`imaging_planner.py:48-50`), echoed in
   `to_dict()` — and **never compared against anything**. Likewise `revisit_days` is
   loaded and never enforced: the same target can be scheduled on consecutive orbits.
4. **Selection → activity.** The UI renders up to 8 opportunities as cards with a
   "Schedule" button (`index.html:2038-2060`). It POSTs `{target_id, start_utc}` to
   `/api/imaging/schedule` (`index.html:2062-2081`). The server
   (`server.py:761-800`) calls `generate_capture_sequence`
   (`imaging_planner.py:209-283`), which produces a `command_sequence` of
   S8,1 func 0x1A `PAYLOAD_SET_MODE_IMAGING(params=[2])` → `wait_s: 5` →
   `wait_for payload.mode == 2 (timeout 30 s)` → S8,1 func 0x1C `PAYLOAD_CAPTURE` with
   `parameters: {target_lat, target_lon, target_id}`, then hands it to
   `ActivityScheduler.add_activity` with `duration_s=120`, `power_w=60`,
   `data_volume_mb=800`, `procedure_ref="NOM-001"`. Capture lat/lon default to the
   **bounding-box centre** (`imaging_planner.py:232-233`) unless the caller supplies
   `lat`/`lon` — and **the UI never supplies them** (`index.html:2066-2069` sends only
   `target_id` and `start_time`).

### Weaknesses
- **No operator control over *where* in the region.** The capture always points at the box
  centre, regardless of where the ground track actually crosses it. For the Gulf Stream box
  (25–45 N, 80–50 W ⇒ ~2200 × 2800 km) the centre can be >1000 km from the sub-satellite
  point at the scheduled instant — far outside the 118 km swath. The activity is
  geometrically self-inconsistent: it was found because the track grazed the box edge, but
  it commands a capture at the centre.
- **Start time = opportunity start.** `scheduleImagingActivity` passes `opp.start_utc`
  (`index.html:2055`), the first track sample at which the box was entered, while the
  activity lasts 120 s from there. No centring on closest approach, no check that the
  120 s window still lies inside the opportunity (opportunities can be shorter than 120 s —
  a 60 s track step means `duration_s` is quantised to 60 s granularity).
- **Solar elevation is not modelled.** Eclipse (umbra) is a much weaker condition than
  "sun ≥ 10° above the horizon"; a target on the terminator passes the check and would be
  imaged in near-darkness. The propagator computes `solar_beta_deg` and a sun vector
  (`propagator.py:31, 33`) so the real elevation is available.
- **No off-nadir/slew model.** `within_swath` implicitly assumes the payload can image
  anything within ±59 km cross-track with zero slew, instantaneously, and says nothing
  about the attitude manoeuvre needed or its duration/momentum cost. `activity_types.yaml`
  declares `requires_attitude: nominal` but no slew activity is generated.
- **Axis-aligned box in lat/lon degrees.** No antimeridian handling (`min_lon > max_lon`
  wraps and would silently match nothing); longitude margin uses
  `cos(lat)` of the *sub-satellite* latitude (`imaging_planner.py:95-97`) rather than the
  box's; polar behaviour degenerate (`max(cos, 0.01)` clamp = 100× margin).
- **No cloud model at all** — for an optical ocean-current mission this is the single
  biggest missing constraint on real imaging yield.
- **No revisit or priority-based deconfliction.** `revisit_days` and `priority` are
  carried but unused in `compute_opportunities`; priority only tints the card border
  (`index.html:2051`).
- **Opportunities are recomputed on every poll** — a fresh `OrbitPropagator` plus a
  1440-point ground-track prediction per `/api/imaging/opportunities` call
  (`server.py:741-751`), invoked from `pollAPIs()` (`index.html:1559`) on the poll
  interval. No caching, unlike contacts/ground track.
- **No map integration.** The map (`initMap`, ground-track and GS markers) never draws the
  target boxes, so the operator cannot see where the regions are relative to the track.
- **Camera parameters are hard-coded** in `imaging_planner.py:26-32` (`ALTITUDE_KM=450`,
  `FOV_DEG=15`, `SWATH_KM≈118`, `MIN_SOLAR_ELEVATION_DEG=10`, `IMAGING_DATA_MB=800`),
  duplicating `orbit.yaml altitude_km`, `payload.yaml image_size_mb` and
  `activity_types.yaml imaging_pass`. Same class of drift as Q5.

### Improvement proposal
- **S:** draw the 7 target boxes on the planner map from `/api/imaging/targets` (endpoint
  already exists, just unused); show the target polygon and the sub-satellite point at the
  opportunity time on the card. Move the camera constants into `payload.yaml`
  (`fov_deg`, `swath_km`) and read `altitude_km` from `orbit.yaml`.
- **M:** compute the true closest-approach point of the ground track to the target within
  each opportunity and use *that* as `capture_lat/lon` and as the activity centre time;
  compute real solar elevation from the propagator's sun vector and enforce the per-target
  `min_solar_elevation_deg`; enforce `revisit_days` when generating opportunities;
  cache opportunities alongside `_contacts_cache`. Add lat/lon entry + a "capture here"
  map click that POSTs explicit `lat`/`lon` (the API already accepts them —
  `server.py:780-781`).
- **L:** off-nadir/roll model with a slew-time and momentum cost fed into the AOCS
  constraint checker; a cloud-climatology layer per target/month; priority- and
  revisit-driven automatic plan generation over the 24 h horizon.

---

## Cross-cutting recommendation

Items Q1, Q3, and most of Q4's UI findings are one change: **stop treating
`time_source == "sim"` as permission to fabricate data in the browser.** Deleting
`GROUND_STATIONS_FALLBACK`, `SAMPLE_ACTIVITIES`, `generateSimulatedContacts()`,
`generateSimulatedActivities()` and the two `if (timeSource === 'sim')` overrides from
`static/index.html` (~150 lines) simultaneously fixes the wrong ground stations, the
phantom default plan, and half the "planner shows nonsense after a breakpoint" symptom —
because the UI would then be displaying the backend's already-correct, config-driven,
sim-time-anchored data. That is the highest-value single change in this audit, and it is
**Effort: S**.

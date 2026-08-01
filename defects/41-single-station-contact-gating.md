## Summary

The orbit propagator gated `in_contact` on the FIRST configured ground
station only. `OrbitPropagator.__init__`
(`packages/smo-common/src/smo_common/orbit/propagator.py`) captured
`self._primary_gs = ground_stations[0]` (Iqaluit) and `_propagate_internal`
computed look angles / `in_contact` against that single station, while the
planner (`smo-planner/contact_planner.py` and `server.py`) planned contact
windows for ALL stations in `configs/eosat1/orbit.yaml` (Iqaluit 63.747N
plus Troll -72.012S).

Consequence: every planner-scheduled Troll pass was dead air. During a Troll
pass `orbit_state.in_contact` stayed False, so `engine.py` uplink/downlink
gating (`uplink_active` / `downlink_active`, both requiring `_in_contact`,
consumed at `engine._tick_once`) rejected all TC uplink and suppressed TM
downlink, contradicting the published contact plan. With Iqaluit alone the
contact timeline also had dead zones of up to ~11 h (48-h scan from mission
T-0: 17 passes, gap median 87 min, max 668 min).

## Severity

**Major** — half the mission's planned ground-station passes (all Troll
passes) were unusable; operators following the planner's schedule saw
uplink rejections and no telemetry, and the pass cadence bore no relation to
the mission design (two near-polar stations giving roughly half-orbit
spacing).

## Status

**Fixed** in `packages/smo-common/src/smo_common/orbit/propagator.py`,
`packages/smo-simulator/src/smo_simulator/engine.py`, plus surfacing in the
MCS/planner servers and instructor UI:

1. `_propagate_internal` now evaluates look angles for EVERY configured
   station each step. `in_contact` is True when ANY station is above its
   `min_elevation_deg`. New `OrbitState` fields: `active_gs` (name of the
   in-contact station, highest elevation wins; None out of contact) and
   `gs_elevations_deg` (per-station elevation dict). The single-valued
   `gs_elevation_deg` / `gs_azimuth_deg` / `gs_range_km` fields carry the
   best station's geometry, so single-station consumers are unchanged.
2. `contact_windows(gs=None)` now predicts windows for ALL stations in one
   time sweep, merged, AOS-sorted, and tagged with `gs_name`; passing an
   explicit `gs` keeps the legacy single-station behaviour (used by
   `ContactPlanner` / planner server per-station loops, which are
   unaffected).
3. Engine: `_tick_once` records `self._active_gs`; AOS/LOS transition events
   (`_check_transitions`) include the station name ("AOS Troll — elevation
   12.3°", "LOS Troll" via `_prev_active_gs`); `get_state_summary` adds
   `ground_station_name`, `gs_elevation_deg`, `gs_range_km` (the MCS UI's
   `updateGsDisplay` already reads `state.ground_station_name`);
   `get_instructor_snapshot` orbit block adds `active_gs`.
4. MCS server `_compute_orbital_state` and planner server
   `_get_spacecraft_state` expose the active station name; the instructor UI
   contact indicator shows "CONTACT: TROLL" / "CONTACT: IQALUIT".
5. Analysis tool `tools/pass_gap_analysis.py` (48-h scan from T-0):
   39 passes/48 h (Iqaluit 8.5/day, Troll 11.0/day), gap min 28 min,
   median 84 min, max 148 min. Gaps are bimodal: ~40 min (half-orbit,
   Troll→Iqaluit same orbit) and ~85 min (one orbit, when the ascending or
   descending node misses one station's longitude window — expected orbital
   mechanics for a 2-station network, not a residual defect). The previous
   11-h dead zones are gone.

## Acceptance criteria

- [x] Mid-Troll-pass epoch gives `in_contact == True` and
      `active_gs == "Troll"` (Iqaluit below mask at that time).
- [x] Merged `contact_windows()` covers both stations, AOS-sorted, tagged,
      and equals the union of per-station calls.
- [x] AOS/LOS events and `/api/state` include the station name.
- [x] Single-station and zero-station configs behave as before.
- [x] 48-h max gap < 3.5 h (was ~11 h); median gap between half and one
      orbit.

## Affected areas

- `packages/smo-common/src/smo_common/orbit/propagator.py`
  (`OrbitState.active_gs` / `gs_elevations_deg`, all-station
  `_propagate_internal`, merged `contact_windows`)
- `packages/smo-simulator/src/smo_simulator/engine.py`
  (`_active_gs` / `_prev_active_gs`, `_check_transitions`,
  `get_state_summary`, `get_instructor_snapshot`)
- `packages/smo-simulator/src/smo_simulator/instructor/static/index.html`
  (contact indicator shows station name)
- `packages/smo-mcs/src/smo_mcs/server.py`, 
  `packages/smo-planner/src/smo_planner/server.py` (surface station name)
- `tools/pass_gap_analysis.py` (new gap-statistics tool)
- `tests/test_simulator/test_multi_station_contacts.py` (10 tests)

## Related

- Defect #42 (ground-station config duplication) — the divergent station
  lists made this defect harder to see; design_issues findings DI-04/DI-05.
- Defect #28 (uplink loss / downlink gating) — same `_in_contact` gating
  path that made Troll passes dead air.

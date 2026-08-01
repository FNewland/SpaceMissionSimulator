## Summary

Imaging opportunities were geometrically self-inconsistent and applied almost
none of the constraints their own config declared.

1. **The capture was always aimed at the bounding-box CENTRE.**
   `generate_capture_sequence` defaulted `capture_lat/lon` to
   `target.center_lat/center_lon` (`imaging_planner.py:232-233`) and the UI never
   supplied anything else. For the Gulf Stream box (25-45 N, 80-50 W, roughly
   2200 x 2800 km) the centre can be **>1000 km** from the sub-satellite point at
   the scheduled instant — far outside the ~118 km swath. The opportunity was
   found because the track grazed the box edge, and then commanded a capture at
   a point the payload could not see.
2. **The only constraint applied was a binary eclipse flag.**
   `solar_ok = not in_eclipse  # simplified check`. The per-target
   `min_solar_elevation_deg` was parsed, echoed in `to_dict()`, and **never
   compared against anything** — so a target on the terminator passed and would
   be imaged in near-darkness. Verified: the sample epoch used by the shipped
   tests (10:00Z over 65 W) is ~05:40 local solar time, i.e. before sunrise.
3. **`revisit_days` was loaded and never enforced** — the same target could be
   scheduled on consecutive orbits.
4. **No off-nadir / swath feasibility check.** `within_swath` expanded the box
   by half a swath converted to degrees using `cos(lat)` of the *sub-satellite*
   latitude, with a `max(cos, 0.01)` clamp giving a 100x margin near the poles,
   and said nothing about the roll actually required.
5. **Start time = opportunity start**, the first 60 s track sample at which the
   box was entered, with no centring on closest approach and no check that the
   120 s capture still fitted inside the window.
6. Camera parameters (`ALTITUDE_KM`, `FOV_DEG`, `SWATH_KM`,
   `MIN_SOLAR_ELEVATION_DEG`, `IMAGING_DATA_MB`) were module constants
   duplicating `orbit.yaml` and `payload.yaml`.

## Severity

**Major** — the planner offered imaging opportunities it could not fly and
commanded captures at points outside the swath, while the constraints that
would have caught it were present in the config and inert.

## Status

**Fixed.**

1. **Access point.** `ImagingTarget.access_point()` returns the point of the
   region closest to the sub-satellite position (the swath-crossing point;
   inside the region that is nadir itself), with antimeridian-safe longitude
   clamping. Cross-track distance is a real great-circle distance, and
   `within_swath` is now that distance against half the swath rather than a
   degrees-box expansion. The capture is aimed at the access point at
   **closest approach**, and the capture window is centred on it.
   `generate_capture_sequence` uses, in order: an explicit `lat`/`lon`, the
   access point computed for that opportunity, and only as a last resort the
   region centre — which is logged as a warning and flagged in the activity as
   `aim_source: "region_centre"`.
2. **Off-nadir feasibility.** The roll needed to reach the access point,
   `atan(cross_track / altitude)`, must be within
   `planner.yaml imaging.max_off_nadir_deg` (7.5 deg = half the 15 deg FOV, i.e.
   the swath edge; a per-target `max_off_nadir_deg` override is accepted).
   Unreachable windows are not offered.
3. **Solar elevation is computed and enforced.** `solar_elevation_deg()` from
   the subsolar point (the same low-precision almanac the UI terminator uses,
   ~0.01 deg in declination); each target's `min_solar_elevation_deg` gates its
   opportunities, eclipse still gates independently, and
   `imaging.enforce_solar_elevation: false` restores the old behaviour for
   teaching contrast.
4. **`revisit_days` is enforced**, both within the computed horizon and against
   captures already committed to the plan (`note_scheduled`, called by
   `POST /api/imaging/schedule`).
5. **Rejections are inspectable.** `GET /api/imaging/opportunities?include_infeasible=1`
   returns the rejected windows with `feasible: false` and a `reject_reason`
   ("solar elevation 4.2 deg < required 10.0 deg", "revisit: only 24.0 h since
   the previous OCM-001 capture", ...). Each opportunity now reports
   `access_lat/lon`, `cross_track_km`, `off_nadir_deg`, `solar_elevation_deg`,
   `closest_approach_utc` and `capture_utc`; the UI cards show aim point,
   off-nadir and sun elevation and schedule at the capture time.
6. **Config-driven.** Camera and feasibility parameters come from
   `planner.yaml imaging:`, altitude from `orbit.yaml`, image size from
   `payload.yaml` (`ImagingPlanner.from_config`).

The `imaging_targets.yaml` format is unchanged; the only extension is an
optional per-target `max_off_nadir_deg`.

## Acceptance criteria

- The capture point for a grazing pass is the access point (within 1 km) and is
  far from the region centre; it lies inside the region.
- A region 555 km off-track yields no opportunity; one 44 km off-track (inside
  the half swath) does, with `off_nadir_deg <= max_off_nadir_deg`.
- A reachable target at 04:40 local solar time yields no opportunity; the same
  geometry at 11:40 does. A per-target threshold of 60 deg rejects a 40 deg sun.
- Two passes over the same target 24 h apart yield one opportunity for a
  3-day revisit; 4 days apart yield two. A capture already scheduled 6 h ago
  suppresses a new one.

## Affected areas

- `packages/smo-planner/src/smo_planner/imaging_planner.py` (rewritten
  opportunity/capture geometry; `subsolar_point`, `solar_elevation_deg`,
  `great_circle_km` added)
- `packages/smo-planner/src/smo_planner/server.py`
  (`/api/imaging/opportunities`, `/api/imaging/schedule`),
  `static/index.html`
- `configs/eosat1/planning/planner.yaml` (`imaging:`)
- Tests: `tests/test_planner/test_imaging_geometry.py`;
  `tests/test_planner/test_imaging_planner.py` sample epochs moved from 10:00Z
  to 14:00Z, because the old fixtures were before local sunrise and are now
  correctly rejected.

## Forward work (not in this change)

- **Map-drawing UI** for operator-defined regions and a "capture here" map
  click. The API already accepts explicit `lat`/`lon` on
  `POST /api/imaging/schedule` and `/api/imaging/targets` already serves the
  region boxes (fetched by nothing today, defect #16), so this is a front-end
  addition.
- Slew-time and momentum cost for an off-nadir capture, fed into the AOCS
  constraint checker.
- Cloud climatology per target/month — the single biggest missing constraint on
  real optical yield.
- Caching opportunities alongside `_contacts_cache` (they are recomputed, with
  a fresh propagator and a 1440-point track, on every poll).

## Related

- Defect #45 (activity func_id misroute) — the capture sequence keeps the
  verified S8 func_ids 0x1A / 0x1C.
- Defect #16 (planner endpoints with no UI) — `/api/imaging/targets`.

## Summary

The ground-station list was defined in TWO config files with no declared
owner and no consistency check (design_issues findings DI-04/DI-05):

1. `configs/eosat1/orbit.yaml` `ground_stations` — geometry only
   (name/lat/lon/alt/min_elevation). Loaded via
   `smo_common.config.loader.load_orbit_config` by the simulator engine and
   the planner server; this list drives the actual contact model and the
   planner's pass schedule.
2. `configs/eosat1/planning/ground_stations.yaml` — the same stations with
   geometry DUPLICATED plus RF fields (antenna diameter, band, `g_t_db`,
   TX power, frequencies). Read only by the MCS server
   (`smo_mcs/server.py::_init_orbit_propagator`), which merged both files
   and silently deduplicated by name.

Editing a station in one file (e.g. moving a site or changing its elevation
mask) silently desynchronised the sim/planner from the MCS view; the
historical Svalbard/Inuvik/O'Higgins vs Iqaluit/Troll drift documented in
`docs/gap_analysis/config_gaps.md` came from exactly this duplication.

## Severity

**Minor** — no runtime failure today (both files currently agree on
Iqaluit + Troll), but a latent config-drift trap that previously produced a
real station-list mismatch and undermines any single-source-of-truth
argument for contact planning.

## Status

**Fixed** — one canonical owner declared, duplication documented, and a
load-time cross-check added:

1. `configs/eosat1/orbit.yaml` is now the CANONICAL station list and
   geometry source; its header states this and that the contact model treats
   the spacecraft as in contact when ANY listed station is above its mask
   (see defect #41).
2. `configs/eosat1/planning/ground_stations.yaml` is re-scoped in its header
   as the RF AUGMENTATION layer only (antenna, band, G/T, frequencies, TX
   power). Its geometry fields are kept for standalone RF tooling
   convenience but MUST mirror orbit.yaml; the header instructs editing
   orbit.yaml first. (Fields were kept rather than removed so existing
   readers and docs referencing the file's schema stay valid.)
3. MCS `_init_orbit_propagator` still merges both sources (orbit.yaml
   precedence unchanged) but now CROSS-CHECKS geometry for same-named
   stations and logs a warning naming the station when
   lat/lon/min_elevation in the planning file diverge from the canonical
   orbit.yaml values (tolerance 1e-3), instead of silently dropping the
   duplicate.

The station list itself remains the mission-specified pair (Iqaluit +
Troll) used by the planner; no stations were added or removed.

## Acceptance criteria

- [x] Exactly one file (orbit.yaml) is documented as the canonical
      geometry source, in both file headers.
- [x] MCS logs a warning identifying the station if the planning file's
      geometry diverges from orbit.yaml.
- [x] orbit.yaml values win on divergence (behaviour unchanged, now
      explicit and observable).
- [x] Simulator, planner and MCS all derive contact geometry from the same
      station set.

## Affected areas

- `configs/eosat1/orbit.yaml` (canonical-source header)
- `configs/eosat1/planning/ground_stations.yaml` (RF-augmentation header)
- `packages/smo-mcs/src/smo_mcs/server.py`
  (`_init_orbit_propagator` divergence warning)

## Related

- Defect #41 (single-station contact gating) — fixed together; the contact
  model now consumes the canonical multi-station list.
- `docs/gap_analysis/config_gaps.md` GAP-GS-001 and
  `docs/gap_analysis/mcs_planner_gaps.md` item 65 — earlier station-list
  drift caused by this duplication.

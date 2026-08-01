# Defect 47 — MCS contact displays only rendered fabricated passes (schema + time-base mismatch)

## Summary

The planner emits contact windows as `{aos, los}` ISO strings + `gs_name` +
`max_elevation_deg` (`smo_common/orbit/propagator.py` `contact_windows`).
Both MCS consumers expected a different schema:

- UI contact timeline (`static/index.html` `renderContactTimeline`): reads
  `aos_utc`/`los_utc` unix floats + `ground_station` — with the planner UP
  every window had `aos_utc === undefined` and was skipped, so the
  "CONTACT WINDOWS (24H)" panel was empty.
- Contact-schedule display (`displays/contact_pass_scheduler.py`
  `update_passes`): read `aos_time`/`los_time`/`max_elevation`/
  `ground_station` — all absent — and **defaulted every real pass to
  aos=now, los=now+600, 45°, "Unknown"**.

The only schema that matched was `_build_synthetic_contacts`
(`server.py`), generated when the planner was DOWN — i.e. the panel
rendered *fabricated* passes precisely when no real data existed (DI-03).
`_handle_contact_schedule` additionally classified passes against wall
clock while planner passes are computed in sim time.

## Severity

**Major** — operators saw either nothing or invented passes; trainee trust
in the contact display was actively undermined (DI-03).

## Status

**Fixed** at the consumer side:

1. `MCSServer._normalize_contact`: translates the planner's real schema
   into the display schema (ISO→epoch `aos_utc`/`los_utc`,
   `gs_name`/`gs`→`ground_station`, `max_elevation_deg`→`max_elevation`,
   plus `aos_time`/`los_time`), preserving original keys. Applied in
   `_handle_contacts_proxy` and `_handle_contact_schedule`.
2. `ContactScheduler.update_passes` itself now parses BOTH schemas
   (including ISO strings and the multi-station `gs` tag) and **drops**
   entries with no usable AOS/LOS instead of faking "in-progress now"
   passes.
3. `_handle_contact_schedule` classifies passes against the MCS ground
   clock (`get_ground_utc()` — sim time in sim mode), not wall clock.
4. Fabricated-pass fallback **removed** (DI-03): `_build_synthetic_contacts`
   deleted; planner-down now returns `{contacts: [], planner_unavailable:
   true, error}` so the UI shows an honest empty state.
5. Remaining synthetic stored-TM dump playback is now explicitly tagged:
   `synthetic: true` on the `/api/tm-dump` + `/api/tm-dump-data` payloads
   and on every generated point, and the MCS UI renders a red
   "SYNTHETIC DATA — no archived TM" status instead of "Loaded N points".

## Acceptance criteria

- [x] Real planner-format windows render through both consumers with
      correct epoch times/station names
      (tests/test_mcs/test_contact_schema.py).
- [x] Planner-down returns an empty, honestly-flagged payload — no
      fabricated passes; `_build_synthetic_contacts` no longer exists.
- [x] Passes with missing fields are dropped, not defaulted to "now".
- [x] Synthetic TM dump payloads are flagged end-to-end and visibly marked
      in the UI.

## Affected areas

- `packages/smo-mcs/src/smo_mcs/server.py`
- `packages/smo-mcs/src/smo_mcs/displays/contact_pass_scheduler.py`
- `packages/smo-mcs/src/smo_mcs/static/index.html`

## Related

- DI-03 (fabricated telemetry/contacts), DI-04 (ground-station config
  duplication), defect #44 (the schedule side of planner→MCS integration).

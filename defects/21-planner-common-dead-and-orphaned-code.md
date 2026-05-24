## Summary

A consolidated register of smaller dead / orphaned code items in `smo-planner`
and the shared `smo-common` library. Each was confirmed by grepping the whole
repo for callers (definitions and tests excluded). These are "implemented but
nothing uses it" — the library/planner surface implies capabilities the running
code reaches by other means or not at all.

**smo-planner:**

1. **`ContactPlanner` is fully dead.** The entire class in `contact_planner.py`
   has zero callers; it is superseded by `OrbitPlanner` + `prop.contact_windows`.
2. **`PowerConstraintChecker.check_activity_power`** (`constraint_checkers.py:70`,
   single-activity bus-capacity check) is never called by `validate_plan` (which
   uses `check_plan_power`) or any route — only by the standalone
   `test_constraints_demo.py`.
3. **`get_command_sequence`** (`activity_scheduler.py:448`) is tested only; the
   upload handler reads `activity.get("command_sequence")` directly
   (`server.py:410`).
4. **`ImagingTarget.contains_point`** (`imaging_planner.py:79`) is tested only;
   production uses `within_swath`.

**smo-common (orphaned shared library surface):**

5. **Orphaned `ParameterRegistry` / `ParameterInfo`** (`telemetry/parameters.py`):
   a full name→ID registry (`register`, `resolve_name`, `get_by_id`,
   `load_from_config`) that is never instantiated in production or tests; config
   loading instead returns raw `list[ParameterDef]` via `loader.py:93`. The
   planner's precondition code re-implements name lookup ad hoc.
6. **Orphaned typed config loaders** (`config/loader.py`): `load_event_catalog`
   (`:137`), `load_memory_map` (`:167`), `load_limits` (`:103`), `load_scenarios`
   (`:123`), `load_pus_service_config` (`:175`), `load_activity_types` (`:191`),
   `load_subsystem_config` (`:47`) have zero callers — consumers raw-parse the
   same YAML inline (e.g. planner `server.py:62-69` and MCS `server.py:201-208`
   both re-read `activity_types.yaml`). The event-catalog and memory-map config
   features are therefore entirely unwired, and their schemas (`EventDefinition`,
   `EventCatalog`, `MemoryMapConfig`) are unused.
7. **Duplicate / dead orbit helpers.** `orbit/contacts.py` (`compute_contact_windows`,
   `compute_all_contacts`) has zero callers — the planner re-implements the same
   loop inline (`server.py:180-191`). `orbit/eclipse.py` (`is_in_eclipse`,
   `eclipse_fraction`) is referenced only by tests; production eclipse uses the
   propagator's private `_is_eclipse` (`propagator.py:241`), so the penumbra-aware
   `eclipse_fraction` is unused.
8. **`models/registry.py` helpers** `register_model` (`:92`) and `list_models`
   (`:97`) have no callers (the live `create_model`→`get_model_class`→
   `discover_models` path is used and fine).

## Severity

**Minor** — no user-facing breakage; the running code works via alternate paths.
But this is duplicated logic (two eclipse implementations, two contact-window
loops), dead config features (events, memory-map), and an unused "canonical"
parameter registry — all maintenance hazards and sources of drift.

## Requirements for the fix

Per item: consolidate consumers onto the shared implementation, or delete the
orphan. Prefer consolidation for 5/6/7 (shared loaders, registry, orbit helpers)
since duplication is the larger long-term risk; delete pure dead code (1, and the
tested-only 2/3/4/8 unless a caller is intended).

## Suggested implementation

- Route planner and MCS config reads through the typed `load_*` loaders; decide
  whether the event-catalog and memory-map features should be wired (memory-map
  relates to S6 Memory Management) or removed.
- Have the planner/propagator call the shared `orbit/contacts.py` and
  `orbit/eclipse.py` functions instead of inline duplicates.
- Use `ParameterRegistry` where name→ID resolution is needed (e.g. planner
  preconditions), or remove it.
- Delete `ContactPlanner` and the tested-only helpers if no caller is intended.

## Acceptance criteria

- No duplicate eclipse / contact-window implementations remain.
- Config loading goes through one typed path (or the unused loaders are removed).
- The codebase has no orphaned "canonical" helper that nothing uses without an
  explicit decision recorded.

## Affected areas

- `packages/smo-planner/src/smo_planner/{contact_planner,constraint_checkers,activity_scheduler,imaging_planner}.py`
- `packages/smo-common/src/smo_common/telemetry/parameters.py`
- `packages/smo-common/src/smo_common/config/{loader,schemas}.py`
- `packages/smo-common/src/smo_common/orbit/{contacts,eclipse,propagator}.py`
- `packages/smo-common/src/smo_common/models/registry.py`

## Related

- Defect #17 (orphaned PUS service parser) — the same orphaned-shared-library
  pattern in `smo-common/protocol`.
- Defect #15 (planner full validation unreachable) — `check_activity_power` and
  the precondition lookup connect to that gap.

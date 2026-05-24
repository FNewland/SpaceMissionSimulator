## Summary

The CAN-bus failure / isolation model is cosmetic: bus-status telemetry changes,
but no subsystem ever actually becomes unreachable, so the bus-failure
contingency cannot be trained.

- The only consumer of bus health is `OBDHBasicModel.is_subsystem_reachable()`
  (`models/obdh_basic.py:441`). Grep across the simulator package shows **no
  production caller** — only the method definition and tests.
- `inject_failure("bus_failure")` (`obdh_basic.py:663-668`) sets
  `bus_a/b_status = BUS_FAILED`, and `obc_select_bus` (`:571-581`) flips
  `active_bus`, but nothing downstream reads either. The HK pipeline
  (`engine.py:1478 _enqueue_tm`) gates only on `downlink_active`/`sw_image`; the
  command dispatcher (`_route_*_cmd` in `service_dispatch.py`) never checks
  reachability. So subsystems on the "failed" bus keep telemetering and keep
  accepting commands.
- The design intends otherwise: `docs/ops_research/obdh_fdir_requirements.md`
  (REQ-OBDH-017 / REQ-SIM-010) requires the reachability check be consulted.

Scenarios affected: `obc_bus_failure.yaml`, `obdh_bus_isolation.yaml`, and the
related contingency procedure — their detect→isolate→recover loop ("subsystems
on the failed bus stop responding; switch to Bus B and they resume") cannot occur.

## Severity

**Major** — the headline OBDH redundancy/contingency exercise is hollow. Operators
see a status flag flip but observe none of the operational consequences they're
being trained to diagnose and recover.

## Requirements for the fix

1. When a bus is failed, subsystems on that bus must stop producing HK and/or
   stop accepting commands until the operator switches to the healthy bus.

## Suggested implementation

- Have `_enqueue_tm` (or the per-subsystem HK build) and the command dispatch
  path consult `obdh.is_subsystem_reachable(name)` and drop HK / reject commands
  when it returns False, keyed off `active_bus` and `bus_*_status`.
- Ensure `obc_select_bus` to the healthy bus restores reachability (see defect
  #32 for the related "rejects switch to failed bus" edge).

## Acceptance criteria

- After `bus_failure`, a subsystem mapped to the failed bus goes quiet (no HK)
  and rejects commands; switching to the healthy bus restores it.
- A test injects `bus_failure`, asserts loss of reachability/HK, then switches
  bus and asserts recovery.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/engine.py` (`_enqueue_tm` / HK build)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (command dispatch reachability gate)
- `packages/smo-simulator/src/smo_simulator/models/obdh_basic.py` (`is_subsystem_reachable`, `obc_select_bus`)

## Related

- Defect #23 (events never reach operator) — the `BUS_FAILURE` event is also
  currently dead.

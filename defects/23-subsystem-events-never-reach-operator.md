## Summary

Every event that a subsystem model generates is discarded before it can reach
the operator. All six models (AOCS, EPS, OBDH, payload, TCS, TTC) contain
purpose-built event-generation code — thermal runaway, heater stuck-on/off, FPA
readiness, decontamination mode change, watchdog timeout, boot failure, SEU,
scrub complete, OBC switchover, CAN-bus failure, etc. — but none of it is
deliverable, for two compounding reasons:

1. **`self._engine` is never assigned on any model.** The model event blocks are
   guarded by `if hasattr(self, '_engine') and self._engine:` (e.g.
   `models/obdh_basic.py:350`, `models/tcs_basic.py:236-237`). The model factory
   `smo_common/models/registry.py:86-89` does `cls(); instance.configure(config)`
   and never sets `_engine`; the engine's model-creation loop (`engine.py:90`,
   `create_model(...)`) never sets it either. `tcs_basic.py:117` sets
   `self._engine = None`. Grep for any assignment of `<model>._engine` across the
   package finds none — so every guarded block is permanently skipped.

2. **`engine.event_queue` is not an S5 source and is never drained.** The models
   target `self._engine.event_queue.put(...)` (aocs_basic.py:1355,
   eps_basic.py:531, obdh_basic.py:353, payload_basic.py:568, ttc_basic.py:681,
   tcs_basic.py:350). `event_queue` is a `queue.Queue` defined at `engine.py:56`;
   it is only ever written (by `_emit_event` itself at `engine.py:1268`) and
   never read (`get`/`get_nowait`/`empty` appear nowhere). The real operator
   event path is `engine._emit_event()` (`engine.py:1248`), which builds an S5
   event packet (`tm_builder.build_event_packet`), enqueues it for downlink, and
   triggers S19 — but models never call it. So even if `_engine` were set,
   pushing onto `event_queue` would still not produce any S5 telemetry.

Additionally, `service_dispatch.py:755` appends to `self._engine._event_queue`
(note the leading underscore) — an attribute that is defined nowhere in
`engine.py`, so that path would raise `AttributeError` if reached.

**Partial compensation (verified during remediation):** the engine independently
re-derives *some* events from telemetry every tick, so the operator is not
totally blind. `engine._check_subsystem_events()` (`engine.py:1386`) synthesises
AOCS/payload/OBDH **mode-change** events and a set of EPS **threshold** events by
watching parameter deltas, and the S12 limit monitor
(`configs/eosat1/monitoring/s12_definitions.yaml`) raises absolute
threshold-crossing WARN/ALARM events. However, both of those reconstruct events
from telemetry values — they do **not** drain the models' own
`event_queue.put(...)` calls. So the model-internal **rate-based** events (e.g.
thermal runaway) and **state-transition** events (heater stuck-on/off, OBC
switchover, boot failure, SEU, CAN-bus failure, FPA readiness, decontamination
mode change) — the ones not re-derivable from a single parameter threshold — have
no delivery path and are never seen on the ground.

## Severity

**Major** (suite-wide). This silently neuters the event logic deliberately
written into every subsystem model. Many FDIR and training scenarios specify
"detect event X" as the expected operator response; for the event types not
covered by the S12 absolute-limit monitor, that cue can never appear. It is the
single highest-leverage fix in the subsystem audit because one engine-wiring
change unblocks correct event behaviour across all six models.

## Requirements for the fix

1. Subsystem models must be able to raise events that reach the operator as S5
   telemetry (and feed S19 / the alarm store) exactly like engine-internal events.
2. The fix must be one shared mechanism, not six per-model hacks.

## Suggested implementation

- In the engine's model-construction loop (around `engine.py:90`), set
  `model._engine = self` after `create_model`, so the existing guarded blocks
  become live.
- Add a single drain step in the run loop that pulls from each model's event
  output and forwards it through `engine._emit_event(...)` (normalising the
  model event dicts/tuples to the `{event_id, severity, description}` shape
  `_emit_event` expects). Standardise the model side on one structure — today
  AOCS pushes an `event` object, EPS pushes a tuple, others push dicts.
- Fix or remove the `self._engine._event_queue` reference at
  `service_dispatch.py:755`.

## Acceptance criteria

- Injecting a failure whose model raises a state-transition event (e.g. TCS
  `HEATER_STUCK_ON`, OBDH `OBC_SWITCHOVER`, AOCS momentum/SEU) produces a
  corresponding S5 event packet observable from the MCS.
- A test injects such a failure and asserts an S5 event with the expected
  event_id is downlinked.
- The S19 event-action engine fires on model-generated events, not only on
  engine-internal ones.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/engine.py` (model wiring + event-queue drain + `_emit_event`)
- `packages/smo-common/src/smo_common/models/registry.py` (or engine loop) — set `_engine`
- All six `packages/smo-simulator/src/smo_simulator/models/*.py` (standardise event push shape)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py:755` (`_event_queue`)

## Related

- This is the root cause behind several "scenario expects an event that never
  appears" symptoms in defects #24–#31. Fixing it first reduces the scope of
  those.

## Summary

The MCS has no generic "parameter watch" widget and no S20 (Parameter
Management Service) client. Operators can only see parameters that happen
to be hardcoded into dashboard tiles. Any parameter not pre-bound to a
widget is completely unobservable from the ground, regardless of whether
it is in HK or not.

This was discovered while trying to diagnose a live bug — the operator was
asked to read `0x0129` and `0x0311` from the MCS and reported "I do not
have a TM/parameter view".

## Severity

**Critical** — operability defect. An MCS without a parameter-watch view is
not flight-ready. Every real-world MCS (SCOS-2000, EGS-CC, Hummingbird,
Rocket Lab's MCC, custom MCSes at Airbus/TAS/OHB) has some form of
parameter-watch list where operators can type a parameter ID or name and
see the live value. Without this, anomaly investigation during a pass is
impossible unless the specific parameter the operator needs happens to be
pre-bound to an existing widget.

## Requirements for the fix

A minimal viable parameter-watch widget should:

1. Accept a parameter ID (hex or decimal) or a symbolic name from the
   parameter dictionary.
2. Display current value, units, last-update timestamp, and source
   (which HK SID provided it, or "on-demand via S20").
3. Allow the operator to save a watch list per screen/session.
4. For parameters not present in any HK packet, automatically issue an
   **S20,1 Report Parameter Values** request at a configurable cadence
   (default: on-demand only).
5. Highlight out-of-limit values using the S12 monitoring limits already
   defined in configs.

## Suggested implementation

- Backend: expose `engine.params` via a read-only WebSocket endpoint or
  extend the existing telemetry stream with a "parameter snapshot"
  message. Implement S20,1 (report) and S20,3 (set) handlers in the
  service dispatcher if not already present.
- Frontend: add a `ParameterWatch` component to the MCS frontend, with
  add/remove rows, save/load watch lists, and live updating.
- Parameter dictionary: generate a JSON file from the existing YAML
  configs so the widget can offer autocomplete by name.

## Acceptance criteria

- Operator can open any MCS screen, add a parameter by ID or name, and
  see its live value within 1 second.
- At least one test case covers S20,1 request → TM[S20,2] response with
  correct value.
- Per-screen watch lists persist across page reload.
- Documentation updated to describe the widget and the underlying S20
  service.

## Affected areas

- MCS frontend (new component + routing)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (S20
  handlers)
- Parameter dictionary generation
- MCS documentation

## Related

- Defect #5 (`sw_image` and `phase` not in HK) — would be immediately
  observable via this widget once implemented, even without changing the
  HK structures.

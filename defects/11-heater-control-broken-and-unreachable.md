## Summary

Heater on/off control is broken in two ways — a backend no-op for the OBC
heater, and no working UI affordance for heater control at all.

**(a) The OBC heater telecommand silently no-ops.** The S8 command path is
fully wired: `service_dispatch.py` maps func_id 40→battery, **41→obc**,
42→thruster to `tcs.handle_command({"command":"heater","circuit":...,"on":...})`
(`_route_tcs_cmd`, `circuits = {40:"battery",41:"obc",42:"thruster"}`).
`tcs_basic.py:486-500 handle_command("heater")` sets `htr_<circuit>` and
`htr_<circuit>_manual=True` and returns `{"success": True}`. But the TCS tick
overwrites the OBC heater every step with **no manual guard**:

```
# tcs_basic.py ~line 399-402  (OBC temp: "manual control only")
s.htr_obc = bool(shared_params.get(0x0116, 0))   # overwrites htr_obc every tick
```

Contrast the battery branch (`tcs_basic.py:386-390`), which honours its gating
flag before applying state. Because `htr_obc_manual` is never consulted in the
tick, an S8 func 41 command "succeeds" (and emits an S1.7 success report) yet
produces no lasting state change — the OBC heater is in practice controllable
only via the EPS power line (`power_line_on/off` → param 0x0116), not via the
heater command that reports success. (Battery heater via func 40 works because
its EPS gate is honoured; the thruster heater func 42 is intentionally forced
off for EOSAT-1 at `tcs_basic.py:413`.)

**(b) No UI exposes heater control.** The instructor TCS card
(`instructor/static/index.html:1163-1176`, updated at `:2114-2134`) shows
`htr_bat`, `htr_obc`, `cooler_fpa` as **read-only status dots** — there is no
toggle or button. The instructor `/api/command` endpoint explicitly **refuses**
spacecraft telecommands (`instructor/app.py:93-101` rejects anything with
`service`/`subtype`/`data_hex` → HTTP 403), so a heater S8 TC cannot be issued
from the instructor UI by design. The only ways to drive a heater are a
raw socket-level PUS telecommand on the TC port, or the EPS power-line command —
neither of which is a heater control any normal user sees.

Net effect matching the user's report: "turning heaters on/off does not work."

## Severity

**Major** — heater control is a real operational command. The OBC heater
command returns success while doing nothing (misleading), and there is no
direct heater UI affordance. A workaround exists (drive the OBC heater's EPS
power line), so this is Major rather than Critical.

## Requirements for the fix

1. The OBC heater state set via the heater telecommand must persist (honour
   `htr_obc_manual`, mirroring the battery logic) — or func 41 must be removed
   and the OBC heater documented as EPS-power-line-controlled only.
2. A user-facing control must exist to turn heaters on/off (operator UI via the
   real TC path, or an explicit instructor control wired to a permitted command).

## Suggested implementation

- `tcs_basic.py.tick`: before `s.htr_obc = bool(shared_params.get(0x0116, 0))`,
  short-circuit when `s.htr_obc_manual` is set (mirror the battery branch).
  Apply the same audit to every circuit so a "success" reply always reflects a
  real state change.
- Add heater toggles to the operator UI (the MCS, which owns the real TC path
  on port 8001 — confirm it exposes S8 func 40/41/43), or add instructor-side
  heater controls wired to an allowed command type rather than a raw TC.

## Acceptance criteria

- Sending S8 func 41 with on=1 turns the OBC heater on and it **stays** on
  across subsequent ticks (verify `htr_obc` and the resulting `temp_obc` rise).
- The success/failure reply matches the actual resulting state for all heater
  circuits.
- A user can toggle each heater from a UI and observe the change in telemetry.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/models/tcs_basic.py` (tick manual-guard; `handle_command`)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (`_route_tcs_cmd`, func 40-43)
- `packages/smo-simulator/src/smo_simulator/instructor/static/index.html` (read-only TCS card)
- MCS frontend / `tc_manager` (operator heater command path)

## Related

- Defect #10 (breakpoints) and #9 (scenarios): same family of "capability
  exists in code but no usable UI path reaches it."

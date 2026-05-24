## Summary

Two engine recovery callbacks invoke command names the target subsystem model
does not handle, so the action silently does nothing (the model returns
`{"success": False, "Unknown command"}` and the engine swallows it).

1. **EPS safing (FDIR).** `engine.py:403` registers
   `safe_mode_eps = lambda: eps.handle_command({"command":"set_mode","mode":1})`,
   but EPS `handle_command` has no `set_mode` branch — its mode command is
   `set_eps_mode` (`models/eps_basic.py:700`); the catch-all returns failure
   (`:715`). This callback is bound to the two most important EPS safing triggers:
   battery SoC < 15 % and bus voltage < 26 V (`configs/eosat1/subsystems/fdir.yaml:12,17`).
   The other subsystems' FDIR callbacks (`payload_poweroff`, `safe_mode_aocs`,
   `safe_mode_obc`) use `set_mode`, which those models DO handle — EPS is the odd
   one out. The callback runs inside a bare try/except that logs-and-swallows
   (`engine.py:878-883`), so the no-op is silent.

2. **TTC load-shed.** `engine.py:464-466` registers
   `ttc_power_level = lambda val: ttc.handle_command({"command":"power_level","value":int(val)})`,
   but TTC's power command is `set_tx_power` (`models/ttc_basic.py:732`); there is
   no `power_level` branch, so load-shedding can never reduce TTC TX power.
   `configs/eosat1/.../fault_propagation.yaml:232` references this command.

Compounding the EPS case: even once the command name is corrected, `eps_mode`
drives **no physics** — it is read only to emit an event (`eps_basic.py:525`) and
its telemetry (`:602`); no load-shed, bus reconfig, or power cap keys off it. And
`set_eps_mode` is not routed by any S8 func_id (`_route_eps_cmd` covers 16-25,
81, 82, none mapping to it), so an operator can't command EPS safe mode either —
despite `defects/reviews/power.md §2.10` marking it "IMPLEMENTED ✓".

## Severity

**Major** — autonomous EPS safing (the headline battery/bus protection) and TTC
power load-shedding are both inert. In a low-power or undervoltage scenario the
spacecraft fails to take the protective action the FDIR rules promise.

## Requirements for the fix

1. The FDIR/load-shed callbacks must invoke commands the target models actually
   handle.
2. EPS safe/emergency mode must produce a real protective effect, and be
   commandable from the ground.

## Suggested implementation

- `engine.py:403` → `eps.handle_command({"command":"set_eps_mode","mode":1})`
  (or add a `set_mode` alias to EPS).
- `engine.py:466` → `ttc.handle_command({"command":"set_tx_power","power_w": ...})`
  (or add a `power_level` branch to TTC).
- Make the EPS tick honour `eps_mode` (e.g. force a load-shed stage / shed
  switchable lines in safe/emergency), and route an S8 func_id to `set_eps_mode`.
- Consider having the engine log when a callback command returns `success: False`
  rather than swallowing it, so this class of mismatch surfaces.

## Acceptance criteria

- Driving battery SoC < 15 % (or bus < 26 V) puts EPS into safe mode AND produces
  an observable protective effect (loads shed / power reduced).
- Load-shedding measurably reduces TTC TX power.
- A test triggers each FDIR/load-shed rule and asserts the model state changed.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/engine.py` (`:403`, `:466`, callback error handling)
- `packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (`set_eps_mode` physics; routing)
- `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py` (power command)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (`_route_eps_cmd`)

## Related

- Same "caller invokes a command name the handler doesn't have" family as the
  instructor-command defects (#9/#10/#12) and the FDIR pattern generally.

## Summary

Three "advanced" TCS commands return success but have no physical effect — the
same false-success pattern as the OBC-heater bug (defect #11), here for the
Phase-4/5 thermal features.

1. **Decontamination heating is inert.** `decontamination_start` (S8 func 47)
   sets `decontamination_active=True` and `decontam_fpa_target_c`
   (`models/tcs_basic.py:542-546`) and publishes the flag (0x0418), but the FPA
   physics (`:408-410`) depends only on `cooler_fpa`/`cooler_failed` and never
   references `decontamination_active`/`decontam_fpa_target_c`. The FPA never
   warms toward the bake-out target. Also, the routing default disagrees with the
   handler default: `service_dispatch.py:678-679` passes `target_temp = -50.0`
   while the handler defaults to `+50.0` (`:545`), so a no-payload command would
   request −50 °C "heating".

2. **Heater duty-limit is never enforced.** `set_heater_duty_limit` (func 46)
   stores `htr_<circuit>_duty_limit_pct` (`:531-541`), and the tick tracks and
   publishes actual duty cycle (0x040E–0x0410, `:416-425`), but nothing ever
   compares measured duty to the limit or sheds the heater. Grep for
   `duty_limit` in `tcs_basic.py` finds only the state defaults and the command
   writer — no reader.

3. **OBC/thruster setpoint & auto-mode commands are no-ops.**
   `_thermostat_control` is invoked only for `"battery"` (`:388`; single call
   site). So `set_setpoint` (func 44) for OBC/thruster stores setpoints nothing
   reads (OBC heater is hard-overwritten from EPS PL6 each tick — the defect #11
   mechanism; thruster is forced off at `:413`), and `auto_mode` (func 45) for
   those circuits clears `htr_*_manual` flags that are only consulted inside the
   battery-only thermostat. Both return success for circuits 1/2 with no effect.

## Severity

**Major** — three advertised thermal capabilities (decontamination bake-out,
duty-cycle limiting, per-circuit setpoint/auto control) are scaffolding that
reports success while doing nothing. Nominal battery thermostat + FPA cooler are
fine; the advanced procedures are not rehearsable. (Defect #11 covers the
specific OBC-heater overwrite; this covers the broader advanced-command set.)

## Requirements for the fix

1. Decontamination must drive the FPA toward its bake-out target.
2. The heater duty-limit must actually cap heater duty.
3. Per-circuit setpoint/auto-mode commands must either take effect or be rejected
   for circuits that have no thermostat.

## Suggested implementation

- In `tick`, when `decontamination_active`, drive `temp_fpa` toward
  `decontam_fpa_target_c` (overriding the cooler term); reconcile the −50/+50
  default mismatch between dispatch and handler.
- In the heater control path, when measured duty ≥ `_duty_limit_pct`, force the
  heater off for the remainder of the window.
- Either run a thermostat for the OBC circuit (reconciling with the EPS-PL6
  override per #11) or reject func 44/45 for circuits without a thermostat
  instead of returning success.

## Acceptance criteria

- A decontamination command warms the FPA to the target temperature.
- A heater whose duty exceeds the configured limit is shed.
- Setpoint/auto-mode commands either change behaviour or return a clear failure
  for unsupported circuits.
- Tests cover each of the three.

## Affected areas

- `packages/smo-simulator/src/smo_simulator/models/tcs_basic.py` (`tick`, `_thermostat_control`, `handle_command`)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (`_route_tcs_cmd` func 44-47 defaults)

## Related

- Defect #11 (OBC heater overwritten each tick) — same false-success family;
  fix them together.
- Defect #23 (events) — TCS `TCS_MODE_CHANGE`/runaway events are currently dead.

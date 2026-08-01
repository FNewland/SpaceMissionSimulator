# 103 — the EPS billed the switch position, not the thing on the other end

**Status:** Fixed

## Summary

The power budget is the spacecraft's central teaching artefact, and it was
accounting for the wrong things at both ends: a **6 W phantom** for a heater
that could not energise (9.45 Wh per orbit of invented consumption), and up to
**60 W of payload plus cooler running entirely off the books** whenever the
operator used the payload's own mode command.

## Root cause

**Heaters billed from the power line, not the thermostat.** `eps_basic.py:475`:

```python
line_powers["htr_bat"] = (
    self._line_power_w.get("htr_bat", 6.0) if lines.get("htr_bat", True) else 0.0)
```

`lines["htr_bat"]` is the *EPS switch position*. The heater on the other end of
that line is thermostat-controlled by TCS and duty-cycles; at baseline it in
fact never energised at all (**#102**). The EPS billed a flat 6 W whenever the
line was closed, which it is by default — 9.45 Wh per 94-minute orbit of
consumption with no physical cause.

**The payload was billed from an EPS-internal mode nothing else set.** Payload
power came from `s.payload_mode`, which is written only by
`set_payload_mode` — reachable only through `EPS_PAYLOAD_MODE` (func 16). And
func 16 goes to the EPS **and stops there** (`service_dispatch.py:730-732`):

```python
if func_id == 16:  # payload mode
    mode = data[0] if data else 0
    self._cmd(eps, {"command": "set_payload_mode", "mode": mode})
```

There is no companion call to the payload model, so func 16 changed the *bill*
without changing the *payload*. And the command an operator actually uses,
`PL_SET_MODE` (func 26), goes only to the payload
(`service_dispatch.py:832`) — so the payload entered imaging mode, the FPA
cooler ran, and the EPS billed **zero** for both. Every procedure that images
via the payload path understated consumption by up to 60 W.

The FPA cooler had the same shape: `lines["fpa_cooler"] and s.fpa_cooler_on`
used the EPS's own copy of the cooler flag, not the one TCS/payload act on.

## Fix

The EPS bills **actuator state published in telemetry**, not switch position:

* heaters are billed from the TCS thermostat duty (`tcs.htr_battery`,
  `tcs.htr_obc`) gated by the line, so line-on/thermostat-off bills zero;
* the payload is billed from the payload model's own mode, whichever command
  set it, and the cooler from the single unified cooler state (**#101**);
* func 16 and func 26 both reach the payload model and the EPS, so the two
  paths are indistinguishable in the budget;
* TX power is tier-dependent rather than a flat transponder constant;
* Phase C/D added the same discipline for the AOCS suite draw and the
  propulsion line.

The rule is stated once in `configs/eosat1/manual/02_eps.md`: a line switch
gates power, an actuator state consumes it.

## Tests

`tests/test_simulator/test_thermal_phase_b.py::TestBillingEqualsActuators`

* `test_heater_energy_matches_tcs_integral` — EPS-billed heater energy over an
  orbit equals the integral of the TCS thermostat's on-time;
* `test_heater_line_on_thermostat_off_bills_zero` — the phantom;
* `test_payload_mode_via_payload_path_bills_eps` — the 60 W hole;
* `test_tx_power_is_tier_dependent`.

`tests/test_simulator/test_eps_telemetry_completeness.py::TestElectricalCrossChecks::test_line_currents_times_bus_equal_power_cons`
closes the bookkeeping identity, so an unbilled load fails the suite.

## Files

* `packages/smo-simulator/src/smo_simulator/models/eps_basic.py`
* `packages/smo-simulator/src/smo_simulator/service_dispatch.py` — func 16/26
* `configs/eosat1/manual/02_eps.md`

## Related

* **#102** — the heater that could not fire and was billed anyway.
* **#101** — the duplicated cooler state that made cooler billing ambiguous.
* **#50** — the power budget diagnosed against these numbers.

# 99 — the power protection ladder was inverted and had no way back up

**Status:** Fixed

## Summary

A protection ladder must be monotonic: the gentlest response first, the most
drastic last, and none of the rungs inside the band the spacecraft occupies
normally. EOSAT-1's was neither. The **harshest** rung — an FDIR cascade that
safes AOCS, powers off the payload and stands down the heaters — fired at
**58 % SoC**, inside the nominal eclipse band and *ahead* of the gentle
staged load shed at 45 %.

## Root cause

Two configuration files each held part of the ladder and neither had ever been
converted to a common unit.

`configs/eosat1/fdir/fault_propagation.yaml:7-23`:

```yaml
- fault_id: "eps_undervoltage"
  primary_param: "eps.bus_voltage"
  threshold: 26.0
  cascades:  # aocs → safe mode, payload → off, tcs → heater standby
```

`configs/eosat1/simulation.yaml` load_shed: `bus_voltage_v: 25.0`.

Through the configured OCV curve (`soc_0_v: 21.5`, `soc_100_v: 29.2`):

| Rung | Volts | **SoC** | Severity |
|---|---|---|---|
| FDIR `eps_undervoltage` cascade | 26.0 | **58.4 %** | safe AOCS + payload off |
| In-model load shed | 25.0 | 45.5 % | shed 4 lines in priority order |
| FDIR SoC stages 1/2/3 | — | 30 / 20 / 10 % | staged |

A 6U in eclipse routinely passes through 58 % SoC. The most aggressive
protection in the system was therefore a **normal-operations event**, and the
staged ladder built to precede it could never run first.

### The restore hysteresis was documented, configured, and absent

`simulation.yaml` declared `restore_hysteresis_v: 0.5`,
`smo_common/config/schemas.py:457` declared it, and `eps_basic.py:280` read it
into `self._shed_hysteresis_v` — after which **it appears nowhere else in the
model**. The shed logic (`eps_basic.py:578`) is one-directional:

```python
if s.bus_voltage < self._shed_voltage:
    for shed_line in self._shed_order:
        ...
```

There is no restore branch at all. Loads shed at 25.0 V stayed off however far
the bus recovered, so a single eclipse permanently stripped the spacecraft
until an operator restored each line by hand — while the manual described an
automatic restore with 0.5 V of hysteresis.

## Fix

* **The ladder is monotonic in SoC**, with every threshold converted through
  the configured OCV curve rather than compared in mixed units:

  ```
  shed 45.5 %  →  EPS-safe 39 %  →  SoC stages 30/20/10 %  →  AOCS-safe 22 %
  ```

  No rung sits inside the nominal band; each is strictly more drastic than the
  one above it.
* The `eps_undervoltage` cascade threshold moved out of the nominal band and
  below the shed rung, so the staged response always precedes it.
* **Restore implemented** (`eps_basic.py:804`): a shed line is restored, in
  reverse shed order, when `bus_voltage > shed_voltage + restore_hysteresis_v`.
  The configured key finally does what it says.

## Tests

`tests/test_simulator/test_protection_ladder_monotonic.py` converts **every**
threshold in both files to SoC through the configured OCV curve and asserts:

* `test_thresholds_strictly_monotonic_in_soc` — no rung out of order;
* `test_severity_increases_as_soc_falls` — harsher responses are lower;
* `test_no_rung_inside_nominal_band` — the defect, stated directly;
* `test_expected_ladder_shape` — the shipped ladder is the documented one.

The test derives the SoC of every threshold from configuration, so moving a
threshold in either file without moving the other fails the suite.

## Files

* `configs/eosat1/fdir/fault_propagation.yaml`
* `configs/eosat1/simulation.yaml`
* `packages/smo-simulator/src/smo_simulator/models/eps_basic.py`
* `configs/eosat1/manual/02_eps.md`, `configs/eosat1/manual/08_fdir.md`

## Related

* **#49**, **#50** — earlier work on the same ladder, done while the rungs
  were in two incompatible units.
* **#98** — the bus voltage the ladder is expressed in was moving the wrong
  way on charge.

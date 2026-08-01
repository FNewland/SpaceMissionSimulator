# 115 — three FDIR procedures had been silently ignored for the life of the repo

**Status:** Fixed

## Summary

`TTC_SET_TX_POWER` (S8.1 func 68) is documented everywhere as a **one-byte
level** (`0 = 1 W, 1 = 5 W, 2 = 8 W`). The handler read a **float32 in watts**
and silently fell back to 2.0 W for anything shorter. Every documented caller
therefore sent one byte, matched no branch, and got 2 W by accident — including
the autonomous FDIR power reduction that exists to save the link during a
power emergency.

## Root cause

`service_dispatch.py:1085-1091`:

```python
elif func_id == 68:  # Set TX power
    power = (
        struct.unpack('>f', data[:4])[0]
        if len(data) >= 4
        else 2.0
    )
    self._cmd(ttc, {"command": "set_tx_power", "power_w": power})
```

Against that, the one-byte level form is documented by
`configs/eosat1/commands/tc_catalog.yaml`, `docs/PUS_SERVICE_REFERENCE.md`, the
TTC role analysis, **and three FDIR procedures**:

* `configs/eosat1/procedures/contingency/ttc_reduce_power.yaml`
* `configs/eosat1/procedures/contingency/ttc_antenna_switch.yaml`
* `configs/eosat1/procedures/contingency/load_shed_stage3.yaml`

All three sent a single level byte, hit the `else` branch, and set 2.0 W. The
command reported success and the FDIR journal recorded the action, so from
every observable surface the procedure had worked. The one place it could have
been noticed — the link margin — was itself wrong until **#84**.

There was also a physical contradiction: the documented high level of **8 W**
exceeds the model's `pa_max_power_w` of **5.0 W**, so the level table could not
be implemented as written. That is why **#89** pinned this rather than fixing
it in that lane — the watt values had to be settled first.

## Fix

* Func 68 takes an **enumerated `level: uint8`** with a corrected watt table:

  | level | watts |
  |---|---|
  | 0 | 1.0 |
  | 1 | 2.0 |
  | 2 | 5.0 (the PA ceiling, not the documented 8 W) |

* An unknown level is **rejected**, not clamped and not defaulted
  (see **#119**), with a reason on the S1.8 (**#87**).
* The three FDIR procedures now do what they say; TX power is a real lever on
  the link budget corrected in **#84** and on the EPS bill (**#103**).
* `docs/PUS_SERVICE_REFERENCE.md`, the catalogue and the TTC manual chapter
  carry the same table, generated from the schema.

## Tests

`tests/test_simulator/test_ttc_enhanced.py`

* `TestTxPowerLevelsWP2` — `test_level_0_is_one_watt`,
  `test_level_1_is_two_watts`,
  `test_level_2_is_the_five_watt_ceiling_not_eight`,
  `test_unknown_level_is_rejected_never_clamped`;
* `TestTxPowerDispatchWP2` — `test_44_00_sets_one_watt`,
  `test_44_02_sets_five_watts`, `test_44_03_is_refused` — i.e. the same
  assertions made through the **wire bytes** an operator or a procedure sends.

`tests/test_content/test_procedure_refs.py` checks that the three contingency
procedures encode a level the handler accepts.

## Files

* `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
* `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py`
* `configs/eosat1/commands/tc_catalog.yaml`
* `configs/eosat1/procedures/contingency/ttc_reduce_power.yaml`,
  `ttc_antenna_switch.yaml`, `load_shed_stage3.yaml`
* `docs/PUS_SERVICE_REFERENCE.md`, `configs/eosat1/manual/05_ttc.md`

## Related

* **#89** — pinned this in `KNOWN_CATALOGUE_HANDLER_DIVERGENCES`.
* **#114** — the same root cause on a different command.
* **#84** — the link budget this command is supposed to move.

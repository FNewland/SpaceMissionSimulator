# 111 — S19 autonomy could name an action but not its argument

**Status:** Fixed

## Summary

An S19 event-action rule fired an S8 function with **no data field**. Every
rule whose action needs to say *which* unit — disable wheel 3, select star
tracker 2 — dispatched a bare function id, so the on-board autonomy either
acted on the default unit or did nothing. Seven shipped rules were affected,
including the four that exist to isolate a failed reaction wheel.

## Root cause

`service_dispatch.py:1823-1831`:

```python
func_id = defn['action_func_id']
logger.info("S19 triggered: rule %d executing S8 func %d ...", ea_id, func_id, ...)
self._s19_dispatch(ea_id, func_id)
```

The loaded rule dictionary (`monitoring_loader.py:212`) has an
`action_func_id` and no argument field at all — there is nowhere in the schema
to put one. The S8 handlers that need an argument read it from `data`:
`AOCS_DISABLE_WHEEL` (func 2) takes a wheel index, `ST_SELECT` (func 6) takes a
unit number.

The shipped rules that this breaks:

| Rule | Intent | What it could actually do |
|---|---|---|
| 2005 | RW1 overspeed → disable wheel 0 | func 2, no index — default wheel |
| 2006 | RW2 overspeed → disable wheel 1 | same |
| 2007 | RW3 overspeed → disable wheel 2 | same |
| 2008 | RW4 overspeed → disable wheel 3 | same |
| 2002 | ST1 blind → **select ST2** | func 6, no unit |
| 2003 | ST2 blind → **fall back to ST1** | func 6, no unit — identical dispatch to 2002 |

Rules 2002 and 2003 were byte-identical at the point of dispatch, so the two
opposite recovery actions were the same action. Under the old model the
consequence was invisible (**#108**); with real dynamics, a wheel overspeed
disabled the wrong wheel and left the failed one spinning.

## Fix

* The S19 rule schema gained `action_data` (a byte string, expressed in the
  YAML as the argument the catalogue declares for that function), carried
  through the loader, through the S19 report (`_handle_s19` subtype 5) and into
  the dispatch;
* `_s19_dispatch(ea_id, func_id, action_data)` builds the same data field the
  ground would have sent, so an autonomous action and a ground command take the
  identical path through `service_dispatch`;
* the seven rules above were given their arguments;
* the rule's action is encoded through the shared argument schema from
  Phase E (**#114**/**#115**), so a rule cannot encode an argument the handler
  does not accept.

## Tests

`tests/test_simulator/test_aocs_dynamics.py::TestAutonomy::test_rw3_overspeed_disables_wheel_2_not_0`
— named for the defect: the rule must disable the wheel that failed, not the
default.

`tests/test_content/test_s19_rules_content.py` checks every shipped rule's
`action_data` against the catalogue's declared argument list for that function,
so a rule with a missing or wrong-width argument fails at build time.

`tests/test_simulator/test_s12_s19_integration.py` exercises the
monitor → rule → action chain end to end.

## Files

* `configs/eosat1/monitoring/s19_rules.yaml`
* `packages/smo-simulator/src/smo_simulator/monitoring_loader.py`
* `packages/smo-simulator/src/smo_simulator/service_dispatch.py`

## Related

* **#37**, **#70** — earlier S19 defects (mis-mapped func_ids, ID namespace
  clash). This is the third structural gap in the same rule schema.
* **#112** — the persistence gap in the same autonomy path.

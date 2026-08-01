# 120 — the ground segment did no limit checking at all

**Status:** Fixed

## Summary

`configs/eosat1/mcs/limits.yaml` declared 45 limit bands. **Nothing in
production read them.** The `TMProcessor` was constructed without limits, so
its `_check_limit` returned on its first line for every parameter of every
packet, `pop_alarms()` had no callers, and the mission control system —
the component whose job is to tell an operator when a value leaves its band —
raised no alarm in any circumstance. Thirty parameters were unmonitored on
**both** the ground and the board.

## Root cause

Three links of a four-link chain existed. `smo_common/config/loader.py:120`
loads the file. `tm_processor.py:66` calls `_check_limit` on every parameter.
`tm_processor.py:117` implements a correct four-band classification.
`tm_processor.py:143` implements `pop_alarms()`.

The missing link is the constructor, `smo_mcs/server.py:232`:

```python
self._tm_processor = TMProcessor(hk_structures=hk_struct_map)
```

No `limits=` argument. `self._limits` is therefore empty and

```python
def _check_limit(self, param_id, value):
    limit = self._limits.get(param_id)
    if not limit:
        return
```

returns immediately, always. The classification code beneath it had never
executed in production. Nothing called `pop_alarms()`, so even if it had, the
result had nowhere to go.

This is the exact shape of the dead-code defects **#9**, **#10** and **#15** —
a facility complete except for the one call that would make it live — and it
had survived that audit because `limits.yaml` looked like configuration for a
feature, and the feature looked implemented.

The coverage gap underneath it was equally real: 30 parameters had no S12
definition on board **and** no ground band, so no layer of the system was
watching them.

## Fix

* `TMProcessor` is constructed with the loaded limits, and `pop_alarms()` is
  drained into a real alarm journal on every processed packet.
* The band set grew 45 → **51**, closing the parameters that had neither a
  board nor a ground check; which parameters are deliberately ground-only (no
  S12 definition) is now an explicit, tested list rather than an accident.
* An alarm *system* around it: acknowledge-with-persistence to the archive, an
  unacknowledged counter in the header, a severity filter that survives a
  reload, and an alarm → contingency-procedure link.

## Tests

`tests/test_content/test_limits_are_live.py` — the gate:

* `test_mcs_server_constructs_tm_processor_with_limits` — the missing argument,
  stated as a test;
* `test_each_band_classifies_violations` (parametrised over every shipped
  band) and `test_hk_violation_reaches_alarm_journal` — end to end from a
  telemetry packet to the journal, per band;
* `test_unlimited_parameter_is_distinguishable_from_nominal` — an unmonitored
  parameter must not read as "in limits";
* `test_repeat_violation_does_not_spam_the_journal`,
  `test_return_to_band_clears_the_alarm`,
  `test_acknowledge_writes_through_to_the_archive`;
* `test_limits_yaml_names_only_known_parameters`.

`tests/test_mcs/test_ground_limits_and_archive.py::test_every_shipped_band_reaches_the_alarm_journal`
and `::test_ground_only_parameters_are_the_ones_with_no_s12_definition` pin the
coverage split.

## Files

* `packages/smo-mcs/src/smo_mcs/server.py`, `tm_processor.py`
* `configs/eosat1/mcs/limits.yaml`
* `packages/smo-mcs/src/smo_mcs/static/index.html` — alarm journal, filters,
  unacked counter

## Related

* **#9**, **#10**, **#15** — the same "one call missing" pattern.
* **#121** — the archive the acknowledgements now persist to.
* **#123** — the displays that invented nominal-looking values, which is how
  the absence of alarms went unnoticed.

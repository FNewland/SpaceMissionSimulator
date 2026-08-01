# 129 — the solar wings announced themselves as a full battery and a blown fuse

**Status:** Fixed

## Summary

The ±Y solar wing deployment events were emitted with ids `0x010E` and
`0x010F`, which the event catalogue assigns to `CHARGE_COMPLETE` and
`PDU_OVERCURRENT`. So the single most important LEOP milestone — the wings are
out — appeared in the event log, the on-board alarm store and the MCS FDIR
journal as "battery reached 100 % SoC" followed by "power line overcurrent
trip".

## Root cause

`models/eps_basic.py:318-325` (baseline):

```python
if s.wing_py_deploying:
    s.wing_py_deployed = True
    events_to_generate.append((0x010E, f"+Y solar wing deployed: area now …"))
if s.wing_my_deploying:
    s.wing_my_deployed = True
    events_to_generate.append((0x010F, f"-Y solar wing deployed: area now …"))
```

against `configs/eosat1/events/event_catalog.yaml`:

```yaml
- { id: 0x010E, name: CHARGE_COMPLETE,  severity: INFO,   subsystem: eps }
- { id: 0x010F, name: PDU_OVERCURRENT,  severity: MEDIUM, subsystem: eps }
```

The auxiliary text is right and the identity is wrong, which is the worst
combination: an operator reading the text sees the truth, every automated
consumer sees something else. `PDU_OVERCURRENT` is MEDIUM severity, so the −Y
wing deployment was copied into the on-board alarm store as a power fault, and
the FDIR journal recorded a power anomaly at the exact moment of a nominal
deployment.

Nothing checked, because the event catalogue was a naming document with no
mechanical relationship to the emitters — the same gap that let eight FDIR
events be catalogued and unemittable (**#124**).

## Fix

* The two deployments were given their **own catalogue entries** with
  appropriate severity, and `eps_basic` emits those;
* `CHARGE_COMPLETE` and `PDU_OVERCURRENT` are emitted only by the conditions
  they name.

## Tests

`tests/test_content/test_event_catalogue_emissible.py`

* `test_wing_deploy_events_use_their_own_ids` — the defect, named;
* `test_every_catalogued_event_is_emittable` — no catalogue entry without an
  emitter;
* `test_event_ids_are_unique`.

`tests/test_simulator/test_no_spurious_eps_events.py` confirms a clean run
emits neither of the two EPS events falsely.

## Files

* `packages/smo-simulator/src/smo_simulator/models/eps_basic.py`
* `configs/eosat1/events/event_catalog.yaml`
* `configs/eosat1/procedures/leop/solar_array_verification.md`

## Related

* **#124** — the same catalogue-versus-emitter gap, at the other end (events
  catalogued and never emitted).
* **#106** — the parameter-id equivalent.

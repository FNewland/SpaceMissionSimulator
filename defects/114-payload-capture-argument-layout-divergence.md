# 114 — every planner-scheduled image was taken at 0°N 0°E

**Status:** Fixed

## Summary

`PAYLOAD_CAPTURE` (S8.1 func 28) had **three** different ideas of its argument
list, held by three components that all had to agree: the command catalogue,
the on-board handler, and the planner that issues it. The planner's idea was
"no arguments at all", so every image the planner scheduled was captured at
latitude 0, longitude 0 — in the Gulf of Guinea — and reported success.

## Root cause

| Component | Believes func 28 takes |
|---|---|
| `configs/eosat1/commands/tc_catalog.yaml` | `scene_id uint16 + lines uint16` = **4 bytes** |
| `service_dispatch.py` handler | `lat float32 + lon float32` = **8 bytes** |
| `configs/eosat1/planning/activity_types.yaml:36` | **nothing** |

The handler:

```python
elif func_id == 28:  # capture image
    lat = 0.0
    lon = 0.0
    if len(data) >= 8:
        lat = struct.unpack('>f', data[:4])[0]
        lon = struct.unpack('>f', data[4:8])[0]
    self._cmd(payload, {"command": "capture", "lat": lat, "lon": lon})
```

The `if len(data) >= 8` guard is the whole defect: a command with the wrong
argument list does not fail, it captures at the default. And the planner's
imaging activity omits the `params:` key entirely — the step immediately above
it in the same file has `params: [2]`, so the omission reads as deliberate:

```yaml
      - service: 8
        subtype: 1
        func_id: 0x1C
        description: "PAYLOAD_CAPTURE_IMAGE"
```

So the imaging planner computed a target, printed
`PAYLOAD_CAPTURE lat=… lon=…` into the activity description
(`imaging_planner.py:606`), and then sent a command containing none of it.
The image catalogue filled up with correctly-numbered scenes of the wrong place,
and the geolocation exercise — the point of an EO mission simulator — could not
be got wrong by a student because it was already wrong.

A command built by hand **to the catalogue** (4 bytes) also captured at (0, 0),
for the same reason.

This was found and pinned as a hand-off in **#89** and fixed here.

## Fix

* One declared argument list, in `tc_catalog.yaml`, expressed through the
  Phase E `TCFieldDef` schema (type, offset, endianness, engineering unit,
  conversion, valid range, default, enum labels).
* The handler is generated from — and validated against — that schema, so the
  catalogue *is* the wire format rather than a description of it.
* The planner emits the arguments it computed, encoded through the same shared
  encoder as the MCS builder, the quick commands, the procedure runner, the
  OBCP loader and the S11 builder.
* A command whose data field does not match the declared layout is **rejected**
  with a reason (**#87**), not defaulted (**#119**).

## Tests

* `tests/test_common/test_arg_schema.py::TestWorkedExamples::test_capture` —
  the encoded bytes for a named target;
* `tests/test_content/test_command_encoders_agree.py` — the six consumers
  encode the same command identically (Python encoder, procedure runner, OBCP
  loader, JS builder, and the JS decoder round-trips);
* `tests/test_planner/test_activity_funcids.py` — the planner's activity
  templates carry the arguments the catalogue declares;
* `tests/test_integration/test_planner_mcs_s11_chain.py` — a planned image
  arrives on board with the planned coordinates;
* `tests/test_content/test_catalogue_dispatch_bijection.py` — every catalogued
  func has a handler and vice versa.

## Files

* `configs/eosat1/commands/tc_catalog.yaml`
* `packages/smo-common/src/smo_common/commands/` — `TCFieldDef`, shared codec
* `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
* `configs/eosat1/planning/activity_types.yaml`
* `packages/smo-planner/src/smo_planner/imaging_planner.py`

## Related

* **#89** — found and pinned this divergence in
  `KNOWN_CATALOGUE_HANDLER_DIVERGENCES`; this is its resolution.
* **#115** — the second pinned divergence, same root cause, different command.
* **#45**, **#80** — earlier catalogue/handler mismatches.

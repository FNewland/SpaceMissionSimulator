# 100 — "solar array degraded" fired on the first sunlit tick of every run

**Status:** Fixed

## Summary

Event `0x010B SOLAR_ARRAY_DEGRADED` fired on the **first sunlit tick of every
single run** and latched there for the session. Because it had already fired,
a genuine array failure injected later produced **no event at all** — the one
condition the event exists to announce was the one condition it could not
report.

## Root cause

The event compares measured generation against an expected value. The expected
value was computed from the **legacy `arrays:` block** in
`configs/eosat1/subsystems/eps.yaml` — two arrays of 0.314 m², a two-array
model that had been superseded by the six-panel `solar_panels:` block
(`eps_basic.py:216-220`):

```python
arrays = config.get("arrays", [])
if arrays:
    total_area = sum(a.get("area_m2", 0.314) for a in arrays)
    self._cell_eff = arrays[0].get("efficiency", 0.295)
```

Expected power was therefore derived from 0.628 m² of notional always-normal
array, while actual generation came from the per-panel cosine projection of the
real geometry. Real/expected was permanently below the configured
`sa_degraded_fraction: 0.7`, so the comparison was true the instant the
spacecraft left eclipse.

The latch made it worse rather than noisier: the emitter is edge-triggered on
`_prev_sa_degraded`, so after the first firing the flag stayed set and no
further transition was possible. The event log carried one false alarm per
session and could carry nothing else.

This is the failure mode **#95** and **#96** were also symptoms of — a dead
configuration key still wired into one consumer — but it is a separate root
cause: the expected-power reference, not the generation model.

## Fix

* The legacy `arrays:` block is **deleted** from `eps.yaml`, with a comment at
  the top of the file recording why, so nothing can read it again.
* Expected power is computed from the same geometry, the same loss stack and
  the same sun vector as actual power — one expression, evaluated twice — so
  the ratio is 1.0 by construction on a healthy array and moves only when a
  panel actually degrades:

  ```python
  gen_w      = effective_area * k_eff * cos_beta * noise
  expected_w = effective_area * k_eff * cos_beta
  ```
* The comparison is against per-panel degradation state, so an injected panel
  loss produces exactly one event.

## Tests

`tests/test_simulator/test_no_spurious_eps_events.py`

* `test_clean_nominal_run_emits_zero_array_degraded_events` — a full clean
  orbit emits none;
* `test_injected_panel_loss_emits_exactly_one_event` — the failure the event
  exists for produces one, and only one.

`tests/test_content/test_config_keys_have_consumers.py` fails if a config block
is left in a shipped file with no consumer, which is the general form of this
defect.

## Files

* `packages/smo-simulator/src/smo_simulator/models/eps_basic.py`
* `configs/eosat1/subsystems/eps.yaml` — `arrays:` removed

## Related

* **#95**, **#96** — the generation model this event is measured against.
* **#105** — the same shape of defect in the S12 monitors: an alarm that fires
  at t = 0 every session tells an operator nothing.

# 127 — the instructor server read live simulation state from another thread

**Status:** Fixed

## Summary

`get_instructor_snapshot()` runs on an aiohttp request thread and walked the
**live** model objects while the simulation thread was mutating them. It also
stored dict and list values **by reference**, so `json.dumps` could raise
mid-serialisation against a growing `image_catalog`, and any caller that
modified the returned structure would have modified the running simulation.
Breakpoint save and load ran on the same request thread with no coordination
with the tick at all.

## Root cause

The parameter dict was correctly handled — `engine.py` publishes a per-tick
snapshot under `self._params_lock` and the reader takes a copy of it, with a
docstring describing the contract. Everything else in the same function was
not:

```python
'subsystems':      self._get_all_subsystem_states(),
'tm_stores':       self._tm_storage.get_status() ...,
'active_failures': self._failure_manager.active_failures() ...,
```

`_get_all_subsystem_states()` calls each model's `get_state()`, which returns
the model's own containers (see **#94**). Three consequences:

1. **Torn reads.** A subsystem state assembled while the tick is halfway
   through updating it mixes pre- and post-tick values; an instructor watching
   a live display sees combinations that never existed.
2. **Serialisation can raise.** `json.dumps` iterating a list that the
   simulation thread appends to raises `RuntimeError: dictionary changed size
   during iteration`. The payload's `image_catalog` grows during any imaging
   exercise, so the instructor display failed **more often the more interesting
   the exercise was**.
3. **The response is a handle on the simulation.** A caller mutating the
   returned dict — a test, a tool, a future endpoint — corrupts the running
   spacecraft.

**Breakpoints had the same shape and worse stakes.** Save and load ran inline
on the request thread: a save could capture a half-ticked spacecraft, and a
load could install state into a model while that model was mid-tick, leaving an
inconsistent spacecraft with no error anywhere.

## Fix

* The instructor snapshot is **built on the tick thread**, at a tick boundary,
  with **deep copies** of every container, and published for readers — the same
  contract the parameter dict already had, extended to the whole structure.
* Breakpoint save and load are **routed through the instructor queue** and
  executed at a tick boundary; the HTTP handler waits for the result.
* `docs/THREADING_AND_STATE.md` states the contract: nothing outside the tick
  thread touches model state, and everything crossing the boundary is a copy.

## Tests

`tests/test_simulator/test_instructor_snapshot.py::TestInstructorSnapshotThreadSafety`

* `test_snapshot_containers_are_not_live_model_objects` — identity check on
  every container in the response;
* `test_mutating_the_response_cannot_corrupt_the_simulation`;
* `test_soak_concurrent_polling_is_fault_free` — polling under a running tick
  for a sustained period with no exception, which is the `json.dumps` failure
  reproduced;
* `test_snapshot_json_serializable` retained from the original suite.

`tests/test_simulator/test_breakpoint_round_trip.py` covers the tick-boundary
save/load path.

## Files

* `packages/smo-simulator/src/smo_simulator/engine.py`
* `packages/smo-simulator/src/smo_simulator/instructor/` — queue, handlers
* `packages/smo-simulator/src/smo_simulator/breakpoints.py`
* `docs/THREADING_AND_STATE.md` (new)

## Related

* **#94** — the same by-reference defect on the breakpoint restore path.
* **#8** — the instructor display this snapshot feeds.

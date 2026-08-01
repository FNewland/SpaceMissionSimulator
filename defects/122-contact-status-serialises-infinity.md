# 122 — a bare `Infinity` in the JSON killed the whole contact response

**Status:** Fixed

## Summary

When no pass was upcoming, `ContactScheduler.get_current_contact_status()`
returned `float('inf')` for the time to AOS. aiohttp's JSON serialiser emits
that as the bare token `Infinity`, which is **not valid JSON**, and
`JSON.parse` rejects it — so the browser lost not just the countdown but the
**entire response**, including the in-contact flag and the ground station. The
same panel independently showed passes that had already finished.

## Root cause

**1. Infinity.** `displays/contact_pass_scheduler.py:152`:

```python
return {"in_contact": False, "ground_station": "None", "time_to_aos": float('inf')}
```

Python's `json.dumps` emits `Infinity` by default (it is a documented
extension); `JSON.parse` implements the JSON grammar, which has no such
literal. The failure is therefore total and silent: the fetch succeeds with
HTTP 200, the parse throws, and the panel keeps its last state — which, on a
freshly loaded page, is empty. The bug only appears when there is **no
upcoming pass**, i.e. exactly when an operator most wants to know that.

**2. Completed passes.** `contact_pass_scheduler.py:110-116`:

```python
def get_next_passes(self, count: int = 10, current_time=None) -> list[dict]:
    ...
    for i, pass_obj in enumerate(self._passes[:count]):
```

`self._passes` is the full computed list from the start of the propagation
window, and the slice takes the first `count` entries of it, not the first
`count` *future* entries. The `current_time` argument is resolved at the top of
the function and then not used for filtering. Several hours into a session the
"NEXT PASSES" panel was showing passes that had already happened, so the panel
was wrong in the ordinary case as well as broken in the edge case.

## Fix

* No non-finite value crosses the API. Absent data is expressed as `null`,
  which JSON has, with the display rendering "no upcoming pass" rather than a
  number.
* `get_next_passes` filters on `current_time` before slicing, so the panel
  shows the next N *future* passes; a pass in progress is labelled as in
  progress rather than dropped.
* The countdown reads from the same contact-schedule endpoint as the rest of
  the situational-awareness header, so there is one source for "when is the
  next pass".

## Tests

* `tests/test_mcs/test_ground_limits_and_archive.py::test_contact_schedule_drops_completed_passes_and_is_json_safe`
  — both halves in one assertion path: the response round-trips through a
  strict JSON parser (no `Infinity`, no `NaN`) **and** contains no pass whose
  LOS is in the past;
* `tests/test_mcs/test_trends_page.py::test_pass_countdown_uses_the_contact_schedule_endpoint`;
* `tests/test_mcs/test_contact_schema.py` and `test_contact_timeline.py`
  retain the schema coverage from **#47**.

## Files

* `packages/smo-mcs/src/smo_mcs/displays/contact_pass_scheduler.py`
* `packages/smo-mcs/src/smo_mcs/server.py`
* `packages/smo-mcs/src/smo_mcs/static/index.html`

## Related

* **#47** — the contact display schema mismatch; same panel, different link in
  the chain.
* **#65** — live plots fabricating a flat line after LOS: the same instinct of
  showing something plausible instead of nothing.

# 106 — the telemetry parameter id space had collisions, crossed ids and undeclared writes

**Status:** Fixed

## Summary

`configs/eosat1/telemetry/parameters.yaml` is the contract between the
spacecraft, the ground segment and the courseware: an id means one quantity.
It was not a bijection. Four ids carried a different quantity from the one
declared against them, one indicator was written to an id one below the one
everything consumed, two parameters were written every tick and declared
nowhere, and the EPS's per-line id scheme could not accept a ninth power line
without colliding with the next block.

## Root cause — an id space maintained by hand, with no closure check

**Crossed trio (EPS).** Three declarations and three writers, none of which
matched:

| id | declared as | actually written |
|---|---|---|
| `0x0129` | `eps.pdm_unsw_status` | the **spacecraft phase**, by `engine.py:928` |
| `0x012A` | `eps.spacecraft_phase` | nothing — `eps_basic.py:754` says "reserved, leave unset" |
| `0x013A` | `eps.eclipse_active` | **PDM unswitched-line status**, by `eps_basic.py:753` |

So a ground display asking for the PDM bitmask got a phase enum, the phase
parameter read zero forever, and the eclipse flag carried a bitmask. Defect
**#5** had reported `phase (0x0129)` as unobservable; it was observable, under
another parameter's name.

**Crossed pair (TCS/TTC).** `0x040D` is declared `ttc.pa_backup_temp` and
written by `tcs_basic.py:546` as `htr_thruster`. A thermal display showed a
heater flag where a temperature belonged.

**Off-by-one indicator.** `payload_basic.py:229` maps `"fpa_ready": 0x060F`
while `parameters.yaml:315` declares — and every consumer reads —
`payload.fpa_ready` at `0x0610`. The FPA-ready indicator, the precondition the
whole imaging exercise turns on, was **hard zero forever** while the real value
sat one id below it, unnamed.

**Written but never declared.** `payload.scene_id` (`0x0606`) is published
every tick by `payload_basic` and appears nowhere in `parameters.yaml`;
Phase D found the same for `tcs.temp_thruster`, published every tick to
`0x0409`. An undeclared id cannot be limit-checked, put in a SID, trended or
named in a procedure.

**Declared but never written.** Seven EPS parameters — `eps.power_margin`
(`0x013B`) and `eps.subsys_power_{eps,aocs,tcs,ttc,payload,obdh}`
(`0x013C`–`0x0141`) — have no writer anywhere. They read 0.0 W, which for a
power draw is a plausible and wrong number (see **#107**).

**No headroom.** The EPS line scheme is `0x0110 + i` for status and
`0x0118 + i` for current, with eight lines. Phase D's ninth line (`prop`)
would have put line status at `0x0118` — on top of line 0's current. The
scheme had no reserved gap and no test that would have caught the overlap.

## Fix

* The crossed ids are resolved to one quantity each, with the *consumer's*
  reading kept where a display already depended on it;
* `payload.fpa_ready` is published at `0x0610`;
* `payload.scene_id` and `tcs.temp_thruster` declared;
* the seven orphan EPS parameters are now written from the real per-subsystem
  breakdown (which also fixed the ground-side fabrication in **#123**);
* the EPS per-line blocks were re-based with declared headroom, so the ninth
  line fits and a tenth is a config change rather than a collision;
* `docs/PARAMETER_MAP.md` is **generated** from `parameters.yaml`, so the
  documentation cannot drift from the declaration.

## Tests

`tests/test_content/test_parameter_chain.py` — the closure gate:

* `test_parameter_ids_are_unique` — no id declared twice;
* `test_every_parameter_is_reachable` — every declared parameter is produced
  by a booted, ticked engine, so "declared but never written" fails;
* `test_stub_parameters_are_declared_not_discovered` and
  `test_the_stub_count_does_not_grow` — a deliberate stub must say so in the
  catalogue, and the stub list may only shrink;
* `test_every_sid_member_is_in_the_parameter_catalogue`,
  `test_every_sid_member_is_produced_by_a_booted_engine`.

`tests/test_simulator/test_eps_telemetry_completeness.py::test_every_declared_eps_param_is_written`,
`::test_no_duplicate_ids_in_catalogue` and `::test_collision_trio_identities`
pin the specific trio.

## Files

* `configs/eosat1/telemetry/parameters.yaml`
* `packages/smo-simulator/src/smo_simulator/engine.py`,
  `models/eps_basic.py`, `models/tcs_basic.py`, `models/payload_basic.py`
* `docs/PARAMETER_MAP.md` (generated)

## Related

* **#5**, **#33** — earlier symptoms of this id space: "unobservable" and
  "computed but not downlinked" parameters.
* **#107** — the ids that *are* correct but that nothing writes.

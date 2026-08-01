# 109 — the zenith star camera looked at nadir

**Status:** Fixed

## Summary

The star-camera blinding geometry had ST1's boresight on **+Z** and ST2's on
**−Z**. The spacecraft user manual — and the physical layout it describes —
puts the zenith star camera on the **−Z** face. The two trackers' sun-exclusion
behaviour was therefore exactly swapped, so the tracker an operator was told to
prefer was the one that went blind.

## Root cause

`models/aocs_basic.py:425-434` (baseline):

```python
# Deterministic FOV geometry: ST1 zenith (+Z), ST2 nadir (-Z)
if unit == 1:
    # ST1 zenith (+Z boresight)
    blinded = self._check_st_blinding(sun_body, (0, 0, 1))
else:
    # ST2 nadir (-Z boresight) — also susceptible to earth limb
    blinded = self._check_st_blinding(sun_body, (0, 0, -1))
```

against `configs/eosat1/manual/02_aocs.md:75-92`:

| Unit | Face | Direction | Exclusion |
|---|---|---|---|
| ST-Zenith | **−Z** | Zenith | 15° cone |
| ST-Nadir | +Z | Nadir | earth limb |

and `00_mission_overview.md:68` — "**−Z**: Zenith; zenith star camera and GPS
antenna mounted here". In the nadir-pointing LVLH frame established in Phase C,
+Z **is** the nadir boresight, so the code had ST1 staring at the Earth.

The comment in the code asserts the correct fact and the code beneath it does
the opposite, which is why it survived review. While attitude was fictional
(**#108**) the error was invisible: nothing depended on which direction a
tracker faced. With real attitude and a real sun vector it decides which
tracker is usable over which part of the orbit, and therefore whether the
S19 tracker-switch rules (**#111**) fire on the right unit.

## Fix

ST1 boresight is −Z (zenith), ST2 is +Z (nadir, with the earth-limb
susceptibility that goes with it). The boresights are read from
`configs/eosat1/subsystems/aocs.yaml` rather than being literals in the model,
so the hardware layout has one home.

## Tests

`tests/test_simulator/test_aocs_dynamics.py::TestEstimator::test_knowledge_grows_without_star_tracker`
and the star-tracker FOV cases exercise blinding against the real sun vector;
`tests/test_content/test_procedures_content.py` checks the manual's geometry
table against the configured boresights, so the document and the model cannot
disagree again.

## Files

* `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py`
* `configs/eosat1/subsystems/aocs.yaml` — boresight vectors
* `configs/eosat1/manual/02_aocs.md`

## Related

* **#108** — the rebuild that made the direction matter.
* **#111** — the S19 rules that select a tracker, which could not name one.

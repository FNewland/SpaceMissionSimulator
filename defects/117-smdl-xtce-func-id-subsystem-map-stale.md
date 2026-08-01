# 117 — the exported command catalogues filed 20 commands under "Spacecraft"

**Status:** Fixed

## Summary

`tools/xtce_smdl_common.py` maps a func_id range to a subsystem name, and it
was never updated after Phases C and D added commands. Twenty commands fell
through the ranges and were exported to XTCE and SMDL as belonging to
**"Spacecraft"**, and the entire propulsion subsystem — new in Phase D — had no
SMDL catalogue at all.

## Root cause

`tools/xtce_smdl_common.py:73-90`:

```python
FUNC_ID_SUBSYSTEM = [
    (range(0, 16), "AOCS"),
    (range(16, 26), "EPS"),
    (range(26, 40), "Payload"),
    (range(40, 50), "TCS"),
    (range(50, 63), "OBDH"),
    (range(63, 79), "TTC"),
    (range(80, 83), "OBDH"),
    (range(100, 108), "EPS"),
]

def func_id_to_subsystem(fid: int) -> str:
    for r, name in FUNC_ID_SUBSYSTEM:
        if fid in r:
            return name
    return "Spacecraft"
```

The fallback is the defect: an unmapped func gets a plausible label instead of
an error. The gaps — 79, 83–99, and everything from 108 upward — swallowed the
Phase C AOCS additions and the whole Phase D propulsion block (funcs 108–118).
The exports are the artefacts a student imports into a real ground-system tool,
so the taxonomy is the first thing they see and the last thing they would
question.

Because the table is a hand-maintained parallel copy of an allocation that
already exists in `tc_catalog.yaml` (every command declares its subsystem),
there was never a reason for it to be a second source in the first place.

## Fix

* `func_id_to_subsystem` reads the subsystem **from the catalogue entry**, so
  the allocation has one home;
* an unmapped func is an **error**, not a "Spacecraft" fallback;
* the SMDL export gained the propulsion catalogue
  (`configs/eosat1/smdl/prop.xml`) and the XTCE export the Phase C/D commands.

## Tests

`tests/test_content/test_generated_command_reference.py` regenerates the
command reference and the exports from the catalogue and fails if the generated
artefacts differ from what is checked in — so an unmapped command breaks the
build rather than being filed under a default heading.

`tests/test_content/test_catalogue_dispatch_bijection.py::test_every_catalogued_s8_func_has_a_handler`
and `::test_every_handled_s8_func_is_catalogued` keep the catalogue itself
complete, which is what the export now depends on.

## Files

* `tools/xtce_smdl_common.py`, `tools/generate_xtce.py`,
  `tools/generate_smdl.py`
* `configs/eosat1/xtce/eosat1.xml`, `configs/eosat1/smdl/*.xml`

## Related

* **#114**, **#115** — the Phase E work that made `tc_catalog.yaml` the single
  source this tool now reads.

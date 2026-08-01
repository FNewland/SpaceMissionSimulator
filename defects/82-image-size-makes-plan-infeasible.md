## Summary

`configs/eosat1/subsystems/payload.yaml` set `image_size_mb: 800.0`. Against the
mission's own downlink — the 64 kbps high TM rate with a 20 % protocol overhead,
i.e. 51.2 kbps effective — **one image was physically undownlinkable**, and every
plan containing a single imaging activity was infeasible by construction.

The arithmetic, measured against the real propagated contact windows for the
configured five-station network (52 passes, 385 contact-min/day):

| Quantity | Value |
|---|---|
| Effective downlink rate | 64 000 × 0.8 = **51.2 kbps** |
| Daily downlink capacity (whole network) | **116.7 MB/day** |
| Contact time for one 800 MB image | ~36 h effective ⇒ **6.8 days of the entire network** |
| Shipped `nominal_ops_day` plan | generated **1660 MB**, downlinked **7.3 MB**, `plan_closes: false`, `days_to_drain: 14.2` |

Two further problems were exposed rather than caused by the image size, and both
had to be fixed for the budget to be honest:

1. **The other activity data volumes were scaled to the same 800 MB world.**
   `calibration` declared 50 MB for a command sequence that is a single
   `AOCS_GYRO_CALIBRATION` over 180 s — 2.2 Mbit/s of calibration telemetry.
   `housekeeping_collection` declared 10 MB for one `S3_REQUEST_HK_ALL` over
   60 s — 1.3 Mbit/s of housekeeping. Neither is defensible at any image size.

2. **`nominal_ops_day` could not close at any image size.** Its four dumps were
   anchored to the next four *consecutive* passes (`repeat: 4`,
   `repeat_pass_stride: 1`), which all fall inside the first hour — before the
   second imaging acquisition (T+1 h 55) and the calibration (T+4 h). More than
   half the day's data was generated after the last scheduled downlink
   opportunity, so the backlog could only grow.

The size was also **triplicated**: `payload.yaml image_size_mb`,
`activity_types.yaml imaging_pass.data_volume_mb`, and
`imaging_planner.IMAGING_DATA_MB`, plus hard-coded 800s in the S13 large-data
handler, the XTCE/TC-catalogue descriptions, one contingency procedure and the
payload requirements document.

## Severity

**Major.** Not a crash, but it makes the pass-planning and data-budget lessons —
the ones the whole planner exists to teach — unteachable. Roadmap N-11 records
it as "the last thing standing between a student and a data budget that
actually closes"; `textbook_vs_sim.md` §3 row 8 recorded it as a 200× divergence
from the course textbook (~4 MB/session, pp. 360, 404).

## Status

**Fixed.** Franz's decision (authoritative): take the textbook's number.

`payload.yaml image_size_mb: 800.0 → 4.0`, with the rationale recorded in the
file. Everything that duplicated or assumed the old size was chased:

| Where | Change |
|---|---|
| `configs/eosat1/subsystems/payload.yaml` | 4.0 MB + the arithmetic, the raw-vs-compressed definition, and the deliberate decision not to rescale the store |
| `configs/eosat1/planning/activity_types.yaml` | `imaging_pass` 800 → 4; `calibration` 50 → 0.5; `housekeeping_collection` 10 → 0.05 |
| `configs/eosat1/planning/standard_plans.yaml` | `nominal_ops_day` dumps: `repeat: 4, stride: 1` → `repeat: 8, stride: 3` |
| `configs/eosat1/planning/planner.yaml` | inherit-comment corrected |
| `smo_common/config/schemas.py` | `PayloadConfig.image_size_mb` default 800 → 4 |
| `smo_simulator/models/payload_basic.py` | three 800.0 defaults → 4.0 |
| `smo_simulator/service_dispatch.py` | S13.1 zero-size fallback now reads the live payload model (`_default_image_size_mb()`), falling back to `DEFAULT_IMAGE_SIZE_MB = 4` |
| `smo_planner/imaging_planner.py` | `IMAGING_DATA_MB` 800 → 4 (fallback only; the server passes the config value) |
| `configs/eosat1/commands/tc_catalog.yaml`, `xtce/eosat1.xml` | "0 = default 800MB" descriptions corrected |
| `configs/eosat1/procedures/contingency/memory_segment_failure.md` | "Usable capacity / 800 MB per image" → 4 MB raw / 1–2.7 MB compressed |
| `docs/ops_research/payload_requirements.md` | frame size and max-stored-images rows |

### After

| Quantity | Value |
|---|---|
| Contact time for one 4 MB image | **655 s effective** ≈ one good pass |
| Images/day the network can sustain | **29** (vs 0.15 before) |
| `nominal_ops_day` generation / downlink | **8.6 MB / 8.6 MB**, backlog **0** |
| `plan_closes` | **true** |
| Capacity of the plan's own dump passes | **27.8 MB** ⇒ **19.2 MB margin (69 %)** |

### Deliberately NOT changed

**`total_storage_mb` stays 20 000.** Rescaling it proportionally (to ~100 MB)
was the roadmap's suggestion, but keeping the store large makes *downlink*, not
storage, the binding constraint — which is exactly the lesson the textbook
teaches. The consequence to be aware of: the store now holds ~5000 raw images,
so a fill-the-store exercise must be driven from the instructor console
(`store_used` injection, or the `payload_memory_failure` scenario) rather than
by capturing images. Recorded in `payload.yaml` and in roadmap N-11 as a
separate, explicit decision.

### Compression / tiling coherence

Coherent, no change needed, but now written down. `image_size_mb` is the **raw**
frame; the on-board CCSDS-123 compressor (`payload_basic.py`, scene-dependent
ratio 1.5–4.0) stores 1.0–2.7 MB of it, so the planner's 4 MB `data_volume_mb`
is the uncompressed worst case — conservative, which is the right direction for
a budget. `data_rate_mbps: 80` and `line_rate_hz: 500` describe the focal-plane
**readout**, not the 120 s acquisition window: a 4 MB frame is 0.4 s of readout
inside a window that is mostly slew, settle and cooler stabilisation. Those two
sentences are now comments in `payload.yaml`, because reading `80 Mbps` and
`120 s` together and multiplying is exactly how the 800 MB figure was arrived at
in the first place.

## Files

- `configs/eosat1/subsystems/payload.yaml`
- `configs/eosat1/planning/activity_types.yaml`, `standard_plans.yaml`, `planner.yaml`
- `configs/eosat1/commands/tc_catalog.yaml`, `configs/eosat1/xtce/eosat1.xml`
- `configs/eosat1/procedures/contingency/memory_segment_failure.md`
- `packages/smo-common/src/smo_common/config/schemas.py`
- `packages/smo-simulator/src/smo_simulator/models/payload_basic.py`
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
- `packages/smo-planner/src/smo_planner/imaging_planner.py`
- `docs/ops_research/payload_requirements.md`
- `docs/review_2026-07/textbook_vs_sim.md`, `docs/review_2026-07/UPGRADE_ROADMAP.md`
- Tests: `tests/test_planner/test_data_budget_config.py` (the 4 MB image, the
  one-image-≈-one-pass arithmetic, and a `TestShippedNominalOpsDay` class that
  builds the real template against real propagated contacts and asserts it
  closes with ≥2× margin), `tests/test_planner/test_standard_plans.py`,
  `tests/test_simulator/test_payload_enhanced.py`
  (`TestMissionConfiguredImageSize`), `tests/test_simulator/test_s13_large_data.py`,
  `tests/test_content/test_procedure_links_content.py` (image size consistent
  across the configs; every activity volume reachable at the payload's own
  readout rate)

## Related

- Roadmap **N-11** (now DONE), which this closes; supersedes the decision half
  of **C-6** and **L-9**.
- `textbook_vs_sim.md` §3 rows 8 and 9 — row 8 is no longer a divergence. Row 9
  (no 1 Mbps rate) stands: the book's "4 MB → 32 s at 1 Mbps" rule of thumb is
  655 s at the sim's 64 kbps.
- Defect **#60** (config-driven data budget) made this measurable — the planner
  reads `image_size_mb` from `payload.yaml` rather than a module constant, so
  the one-line config change propagated correctly and the budget test suite
  could prove it.
- Defect **#74** / roadmap **N-8** made 64 kbps genuinely reachable, which is
  what made this mismatch visible to students rather than latent.
- **Still open, outside this lane:** the planner's own `data_dump` activity is
  600 s while a nominal pass is 350–570 s, so a dump always over-runs LOS. The
  budget model already truncates downlink at LOS, so the arithmetic is right,
  but the duration is worth a Flight-Ops look.

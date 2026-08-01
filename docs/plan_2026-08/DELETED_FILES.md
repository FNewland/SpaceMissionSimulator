# Phase G — files deleted

This container cannot delete files on the user's Mac. The orchestrator should
relay this list so the same deletions are applied there. Every path below is
deleted in this repository and verified absent; nothing in `packages/`,
`tools/` or `tests/` imports any of them, and the suite is green without them.

## Whole files and trees

| Path | Lines | Why |
|---|---:|---|
| `files/` (whole tree, incl. its populated `__pycache__`) | 11 618 | Three-month-stale parallel copy of the simulator, MCS and UI that sat beside `packages/`. Half of every grep hit in this repo came from it (brief 08 O-17 / X-4, finding 17). Nothing imports it. Individual members listed below. |
| `packages/smo-mcs/src/smo_mcs/static/old_index.html` | 5 063 | Served by nothing — `_handle_index` serves `index.html` only (finding 15). Its `sendLegacyCommand` twin and the disabled `/api/command` route went with it. |
| `packages/smo-planner/src/smo_planner/static/index-wide.html` | 1 822 | A second, divergent planner UI served at `/wide`; `planner_audit.md` records the two being fixed independently, with different ground-station lists (finding 16). |
| `packages/smo-mcs/src/smo_mcs/displays/procedure_status.py` (`ProcedureStatusPanel`) | 145 | Unimported headless display class. The live procedure picture is now the FDIR window plus `proc.*` telemetry. |
| `packages/smo-mcs/src/smo_mcs/displays/widgets.py` (`Widget`, `TrendingData`) | 120 | Unimported. Its trending implementation is superseded by the archive query API behind the TRENDS page. |
| `packages/smo-mcs/src/smo_mcs/displays/engine.py` (`DisplayEngine`) | 62 | Not imported by `displays/__init__.py` and referenced nowhere in `packages/`. Aspiration-shaped dead code for a config-driven display path that was never taken (finding 9). |

Members of `files/`:

```
files/aocs.py               files/config.py             files/ecss_decommutator.py
files/engine.py             files/eps.html              files/eps.py
files/failure_manager.py    files/fd.html               files/fdir.html
files/fdir.py               files/instr.html            files/mcs.html
files/obdh.py               files/orbit.py              files/payload.html
files/payload.py            files/scenario_engine.py    files/service_handlers.py
files/sim_server.py         files/sys.html              files/tcs.html
files/tcs.py                files/test_client.py        files/tm_builder.py
files/ttc.html              files/ttc.py
files/__pycache__/          (whole directory)
```

## The `/wide` decision

`index-wide.html` is deleted, **the `/wide` route is kept.** Deleting the route
outright was the brief's default recommendation, and it is the cheaper answer,
but `/wide` is a *published* classroom URL: it appears in
`deploy/INSTALLATION_MANUAL.html`, `docs/DEPLOYMENT.md` and
`deploy/start-planner.sh`'s banner, and an instructor with a 5760×1080 wall
display has it bookmarked. Breaking a documented URL to save a three-line
handler is a bad trade.

So `_handle_index_wide` is now a redirect to `/?layout=wide`, and
`static/index.html` adds a `wide-mode` body class when that query parameter is
present. One page, one set of fixes, one ground-station list — which is the
actual defect (two UIs guarantee a third divergence) — and the bookmark still
works.

## Also removed (code, not whole files)

- Simulator `GET /api/catalog` (`smo-simulator/server.py`) and its handler —
  returned a hard-coded empty catalogue while 194 real commands exist
  (finding 17).
- `mission.yaml: ground_segment` — a second, unenforced copy of the
  ground-station list (finding 19 / X-11).
- `contact.current_gs` / `contact.time_to_aos` / `contact.in_contact` moved out
  of `telemetry/parameters.yaml` into the new `telemetry/derived.yaml`: they
  are computed by the ground segment, are in no HK SID, and no S20 read can
  return them (finding 12).
- `fdir.*` (`0x0700`–`0x0703`) and `proc.*` (`0x0900`–`0x0903`) stayed in the
  spacecraft catalogue and were made `on_demand: true`. Brief 08 finding 12
  grouped them with `contact.*` as ground-derived pseudo-parameters, but that
  was true only while the FDIR model and the recovery-procedure executor never
  ran: both execute **on board** since Phase G, so `fdir.level`,
  `fdir.active_alarms` and the `proc.*` block are genuine spacecraft state.
  They are written every tick by `engine._publish_fdir_parameters` and are
  S20-readable. Only `contact.*` is genuinely ground-derived, and only it
  moved. See `docs/PARAMETER_MAP.md`.

**Total: 18 830 lines of whole files, plus the residue above.**

## Repository-root consolidation (brief 08 WP-0)

The file bridge cannot unlink or rename, so these need relaying too.

### Deleted outright

| Path | Why |
|---|---|
| `r24.txt` | Scratch output from a May review session |
| `r27.txt` | Same |
| `.DS_Store` | macOS Finder metadata; now in `.gitignore` alongside `._*` |
| `.__write_test.txt` | A write-permission probe from May; now in `.gitignore` |

### Moved (delete from the old path after copying to the new one)

| From (repo root) | To |
|---|---|
| `CONSTRAINT_IMPLEMENTATION_SUMMARY.md` | `docs/archive/2026-04/` |
| `FDIR_ENHANCEMENTS.md` | `docs/archive/2026-04/` |
| `FDIR_IMPLEMENTATION_SUMMARY.txt` | `docs/archive/2026-04/` |
| `FDIR_QUICK_REFERENCE.md` | `docs/archive/2026-04/` |
| `IMPLEMENTATION_SUMMARY.md` | `docs/archive/2026-04/` |
| `INSTRUCTOR_AUDIT_SUMMARY.txt` | `docs/archive/2026-04/` |
| `SEPARATION_SCENARIO.md` | `docs/archive/2026-04/` |
| `REMEDIATION_PLAN.md` | `docs/archive/2026-05/` |
| `SPACECDF_RECONCILE.md` | `docs/archive/2026-05/` |
| `FUNCTIONALITY_AUDIT_2026-06.md` | `docs/archive/2026-06/` |
| `VV_FINDINGS_2026-06.md` | `docs/archive/2026-06/` |
| `CERISE based error scenario.docx` | `docs/source_material/` |
| `DEBRIS_IMPACT_SCENARIO_CRITIQUE.docx` | `docs/source_material/` |
| `Debris_Impact_Recovery_Procedures_Final_v2.docx` | `docs/source_material/` |
| `EO_Mission_Simulator_Architecture.docx` | `docs/source_material/` |
| `aerospace-year1-uk.skill` | `docs/source_material/` |
| `aerospace-year2-uk.skill` | `docs/source_material/` |
| `aerospace-finalyear-space-uk.skill` | `docs/source_material/` |
| `aerospace-phd-space-uk.skill` | `docs/source_material/` |
| `systems-engineering.skill` | `docs/source_material/` |

Each destination folder carries an `INDEX.md` saying what superseded what.
The root now holds `README.md` (new), `DEFECTS.md`, `LICENSE`, `start.sh`,
`pyproject.toml`, `fix_vendor_symlinks.sh` and the source directories.

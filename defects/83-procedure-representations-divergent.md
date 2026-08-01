## Summary

"Procedure" meant four different things in the tree, linked by nothing but a
`procedure_ref` string, and only one path between them worked.

| # | Representation | Where | Executed by |
|---|---|---|---|
| 1 | Untagged step list | `planning/activity_types.yaml` `command_sequence` (9 activities) | MCS `ProcedureRunner` |
| 2 | Tagged step list | MCS Procedure Builder → `procedures/custom/*.yaml` | MCS `ProcedureRunner` |
| 3 | Markdown ops procedure | `procedures/**/*.md` (64 files) + `procedure_index.yaml` | a human |
| 4 | OBCP step blob | `obcp/*.yaml` | on board, `ObcpManager` (S18) |

The runner itself works end to end for both ground dialects — that was proved in
`pus_services_audit.md` §7.1. The defects were around it:

1. **A builder-saved procedure could be edited but never run.** `pbLoadCustom()`
   loaded a saved procedure back into the *editor* only; `procLoad()` offered
   activity-type names exclusively. `_handle_proc_load` would happily have
   accepted the step list — the button was simply absent. This is the concrete
   gap an operator hits: build a procedure, save it, and then discover there is
   no way to execute it.

2. **`_step_desc` had no branch for `type`-tagged steps.** It handled `wait_s`,
   `wait_for` and `service` and otherwise fell through to `str(step)`, so every
   builder-authored wait, telemetry check and GO/NO-GO gate was shown to the
   operator in the MCS step list as a raw Python dict:
   `{'type': 'wait', 'seconds': 1, 'description': 'Wait 1s'}`.

3. **`tlm_check.parameter` was unvalidated free text.** There was no vocabulary
   anywhere in the UI, and the runner resolves dot-paths against the MCS's live
   *state snapshot*, which is not the same set of names as
   `telemetry/parameters.yaml` (the catalogue lists `ttc.pa_on`; the snapshot
   does not carry it). A typo — or a perfectly real catalogue parameter that the
   snapshot happens not to expose — was accepted silently at save time and then
   surfaced as a 30-second timeout and a FAILED procedure the first time an
   operator ran it, mid-pass.

4. **The 64 markdown procedures were unreachable from the browser.**
   `/api/procedures`, `/api/procedures/{path}` and `/api/procedure/index` were
   all served and had **no call site anywhere in `static/`**. An operator
   executing NOM-002 could not read NOM-002.

5. **No canonical schema.** Two dialects shared a runner but not a schema; the
   runner carried a translation branch per dialect, and every new consumer
   (planner upload, OBCP encoder, content tests) had to re-learn both.

## Severity

**Major.** Item 1 is a dead-end workflow in the console students use; item 3 is
a silent failure that costs a pass; item 4 removes the operations manual from
the console at the moment it is most needed. Roadmap X-12 rates the convergence
"the single biggest training-value win in the audit".

## Status

**Fixed** for (a) the canonical schema, (b) the framework plus exemplars that
make the shipped procedures reachable, and (c) the missing UI. The ground → S18
compiler and S18-in-the-command-builder remain forward work (roadmap X-12 (d)
and (e)).

### 1. One canonical schema

`packages/smo-common/src/smo_common/procedures/schema.py` defines it — the
tagged dialect wins, because it is the only one that can express a telemetry
check or a GO/NO-GO gate — plus adapters:

| Adapter | From |
|---|---|
| `from_activity_sequence` | dialect 1 (`wait_s` → `wait`, `wait_for` → `tlm_check`, `service` → `command`) |
| `from_builder_steps` | dialect 2 (normalises `seconds`/`wait_s`, `condition`/`operator`, `"0x1A"` → 26) |
| `from_markdown` | dialect 3 — extracts the `**TC:**` annotations in document order |
| `to_canonical` | either ground dialect, auto-detected |
| `describe_step` | the ONE renderer, used by `_step_desc` |
| `validate_steps` | returns problems; empty means runnable |

`ProcedureRunner.load()` canonicalises whatever it is handed, so **no config had
to be migrated** and both dialects keep working. Argument fields the runner
packs (`sid`, `store_id`, `param_id`, `address`/`data`,
`parameters.target_lat/lon`) are carried through rather than re-encoded.
Normative description: `docs/PROCEDURE_SCHEMA.md`.

### 2. Builder-saved procedures are runnable

`POST /api/procedure/load` now accepts `steps` | `path` (a saved procedure) |
`procedure_id` (an index entry with a runnable link) | `name` (an activity
type). The Procedures tab gained a **source selector** (activity type / ops
procedure / saved procedure) driving the existing `#proc-type-select`, and the
builder gained **"Run These Steps"** for the unsaved case. Saved files now carry
`schema_version`.

### 3. Readable steps, validated parameters

`_step_desc` delegates to `describe_step`, so a builder wait renders as
`Wait 5s` and a check as `Check payload.mode == 2 (timeout 30s)`.

New `GET /api/procedure/tlm-vocabulary` returns the dot-paths the runner can
actually resolve (the flattened live state snapshot). `/api/procedure/save` and
`/api/procedure/load` reject an unknown parameter with HTTP 400 and a
`problems: [...]` list including a "did you mean…?" suggestion; the builder
offers the vocabulary as a `<datalist>`. When no telemetry has arrived the
endpoint reports `source: "none"` and validation is skipped — there is nothing
to validate against, and pretending otherwise would be worse.

### 4. The operations manual is reachable

`procedure_index.yaml` entries may now carry a machine-readable link:

```yaml
runnable: {kind: activity_type, ref: imaging_pass}
runnable: {kind: steps_file,    ref: nominal/foo.steps.yaml}
runnable: {kind: markdown}
```

`GET /api/procedure/index` reports `runnable` / `runnable_steps` /
`runnable_source` per entry, `/api/procedures/{path}` is now called by the UI,
and the Procedures tab shows the markdown of the procedure being executed beside
the step list.

**Shipped exemplars — the framework, not a bulk conversion:** NOM-002 (imaging
session → `imaging_pass`), NOM-003 (data downlink → `data_dump`), NOM-009
(routine health check → `housekeeping_collection`) and NOM-008 (clock
synchronisation, via the `markdown` adapter). The other 60 stay read-only. That
is deliberate: auto-converting 64 documents is out of scope, and most
contingency procedures are not automatable at all because their commanding
depends on judgements the prose carries and the `**TC:**` annotations do not.

### 5. The command builder is not copied a third time

Per the hand-off from the MCS agent, the builder's `command` step instantiates
the existing `renderCommandBuilder()` component (defect #77) with
`prefix: 'pbcb'`, rather than adding a third hand-written command packer. The
operator picks the command from the catalogue-driven dropdowns, packs its
fields, and the builder's own SEND action appends a canonical `command` step.

## Files

- `packages/smo-common/src/smo_common/procedures/{__init__.py,schema.py}` — new
- `packages/smo-mcs/src/smo_mcs/procedure_runner.py` — `_step_desc` delegates;
  `load()` canonicalises
- `packages/smo-mcs/src/smo_mcs/server.py` — `_tlm_vocabulary`,
  `_load_saved_procedure`, `_activity_type_procedure`, `_indexed_procedure`,
  rewritten `_handle_proc_load`, validation in `_handle_proc_save`, enriched
  `_handle_proc_index`, new `/api/procedure/tlm-vocabulary`
- `packages/smo-mcs/src/smo_mcs/static/index.html` — source selector, document
  viewer, builder rework
- `configs/eosat1/procedures/procedure_index.yaml` — `runnable:` schema + 4
  exemplars
- `docs/PROCEDURE_SCHEMA.md` — new, normative
- Tests: `tests/test_mcs/test_procedure_convergence.py` (45 tests: the
  save→list→load→run→COMPLETED round trip, descriptions-are-text, unknown
  parameter rejected at save and at load, adapters, index links, UI wiring),
  `tests/test_content/test_procedure_links_content.py`

## Answer to the question that prompted this

*"Do the procedures on the procedures tab work, and are they the same as the
mission planner procedure builder effectively?"*

**Before:** the tab ran, but only the nine planner activity types. There is no
builder in the mission planner at all — the builder is in the MCS, and what it
produced could not be run from the tab it lives on. The 64 real ops procedures
were documents the console could not show.

**Now:** the tab runs all three ground sources — planner activity types,
builder-saved procedures and the runnable-linked ops procedures — through one
runner over one schema, with the operations-manual text displayed beside the
run. They are the same thing: the planner's activity `command_sequence` and the
builder's saved YAML are two dialects the adapters normalise into one canonical
step list. What is *not* the same is the on-board S18/OBCP representation, which
is a genuinely different execution model (relative delays, no telemetry checks,
survives LOS) and remains a separate target.

## Related

- Roadmap **X-12** — (a), (b-framework), (c) done here; (d) ground → OBCP
  compiler and (e) S18 in the command builder remain.
- `pus_services_audit.md` §7 (Q7), which found all five items.
- Defect **#14** (the runner's builder-dialect translation branch) and defect
  **#77** (the reusable `renderCommandBuilder()` component) are the two pieces
  this builds on.
- **Still open, flagged not fixed:** several `activity_types.yaml`
  `procedure_ref` values point at a neighbouring index id — `imaging_pass` →
  `NOM-001` "Pass Startup" rather than `NOM-002` "Imaging Session", and the same
  off-by-one on `data_dump`, `software_upload` and `momentum_desaturation`.
  Every ref resolves to a real index entry, so nothing breaks, and a new test
  pins that. Correcting the pairing is a Flight-Ops content call of the same
  class as the annotation-drift allowlists in
  `tests/test_content/test_procedures_content.py`, so it is recorded in
  `docs/PROCEDURE_SCHEMA.md` §7 rather than silently rewritten. The `runnable:`
  links are declared from the index side and are therefore correct regardless.

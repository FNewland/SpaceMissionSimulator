# Canonical Procedure Schema

*Roadmap X-12 · defect #83 · implemented in
`packages/smo-common/src/smo_common/procedures/schema.py`.*

"Procedure" used to mean four unrelated things in this tree, linked by nothing
but a `procedure_ref` string. This document defines **one** schema, says which
of the four dialects is canonical, and lists the adapters that let the other
three keep working unchanged.

---

## 1. The four dialects, and which one won

| # | Dialect | Where | Executed by |
|---|---|---|---|
| 1 | Untagged step list | `planning/activity_types.yaml` `command_sequence` | MCS `ProcedureRunner`; also converted to S11 by `/api/schedule/upload-s11` |
| 2 | **Tagged step list** | MCS Procedure Builder → `procedures/custom/*.yaml` | MCS `ProcedureRunner` |
| 3 | Markdown ops procedure | `procedures/**/*.md` (64 files) + `procedure_index.yaml` | a human |
| 4 | OBCP step blob | `obcp/*.yaml` | on board, `ObcpManager` (S18) |

**Dialect 2 is canonical.** It is the only one that can express every step kind
the ground runner supports (a telemetry check and a GO/NO-GO gate have no
representation in 1 or 4, and 3 is prose). Dialects 1 and 3 have adapters into
it; dialect 4 stays a distinct *target* (on-board, relative-delay, no telemetry
checks), not a competing source format.

---

## 2. The schema

```yaml
schema_version: 1
id: NOM-002                       # optional; a procedure_index.yaml id
name: "Imaging Session"
description: "..."
steps:
  - type: command                 # command | wait | tlm_check | go_nogo | note
    service: 8
    subtype: 1
    func_id: 26                   # accepts 26, "26" or "0x1A"
    params: [2]                   # uint8 arguments appended after func_id
    data_hex: ""                  # raw data field; wins over func_id/params
    description: "PAYLOAD_SET_MODE_IMAGING"
    verify:                       # optional post-command telemetry check
      parameter: payload.mode
      operator: "=="
      value: 2
      timeout_s: 30

  - type: wait
    seconds: 5

  - type: tlm_check
    parameter: payload.mode       # dot-path; see §4
    condition: "=="               # == != > < >= <=
    value: 2
    timeout_s: 30

  - type: go_nogo
    label: "Imaging configuration verified?"

  - type: note
    text: "Record the scene id in the pass log."
```

A `command` step may instead carry any of the argument forms the runner packs:
`sid` (S3), `store_id` (S15), `param_id` + `value` (S20), `address` + `data`
(S6), or `parameters: {target_lat, target_lon}` (imaging capture). These are
carried through unchanged — the canonical form does not invent a second
encoding for them.

`description` is filled in automatically from the step's content when it is
omitted, so the operator never sees a raw dict in the step list.

---

## 3. Adapters

```python
from smo_common.procedures import (
    to_canonical, from_activity_sequence, from_builder_steps, from_markdown,
    validate_steps, describe_step,
)
```

| Function | From | Notes |
|---|---|---|
| `to_canonical(steps)` | dialect 1 **or** 2 | auto-detects; used by `ProcedureRunner.load()` and by every MCS endpoint |
| `from_activity_sequence(seq)` | dialect 1 | `{wait_s}` → `wait`, `{wait_for}` → `tlm_check`, `{service…}` → `command` |
| `from_builder_steps(steps)` | dialect 2 | normalises `seconds`/`wait_s`, `condition`/`operator` |
| `from_markdown(text, catalog)` | dialect 3 | extracts the `**TC:**` annotations in document order into `command` steps. Prose, verification text and GO/NO-GO wording are **not** interpreted |
| `describe_step(step)` | any | the single renderer used for the MCS step list |
| `validate_steps(steps, known_parameters)` | canonical | returns a list of problems; empty means runnable |

`ProcedureRunner.load()` calls `to_canonical` on whatever it is given, so both
ground dialects continue to run and no config had to be migrated.

---

## 4. Telemetry vocabulary for `tlm_check`

`tlm_check.parameter` (and `command.verify.parameter`) is a **dot-path into the
MCS's latest spacecraft state snapshot**, e.g. `payload.mode`, `eps.soc_pct`,
`aocs.att_error_deg`. It is *not* the `telemetry/parameters.yaml` catalogue
name: the catalogue lists parameters (`ttc.pa_on` is one) that the state
snapshot does not carry, and a check on one of those can never succeed.

The MCS serves the real vocabulary at **`GET /api/procedure/tlm-vocabulary`**,
the Procedure Builder offers it as a datalist, and `/api/procedure/save` and
`/api/procedure/load` reject an unknown parameter with HTTP 400 and a
`problems: [...]` list — including a "did you mean…?" suggestion. Previously an
unknown parameter was accepted silently and surfaced as a 30-second timeout and
a FAILED procedure the first time an operator ran it.

When no telemetry has been received yet the endpoint reports
`source: "none"` and parameter names are not validated (there is nothing to
validate against).

---

## 5. Linking the 64 operations-manual procedures

The markdown procedures are the operations manual. They are **not** rewritten
into step lists. Instead `procedures/procedure_index.yaml` gains an optional
machine-readable link per entry:

```yaml
  - id: NOM-002
    name: "Imaging Session"
    file: "nominal/imaging_session.md"
    runnable: {kind: activity_type, ref: imaging_pass}
```

| `kind` | `ref` | Meaning |
|---|---|---|
| `activity_type` | a name in `planning/activity_types.yaml` | run that activity's verified command sequence |
| `steps_file` | a path under `procedures/` | run a canonical YAML step list |
| `markdown` | — | derive the commanding skeleton from this document's own `**TC:**` annotations |

`GET /api/procedure/index` reports `runnable` and `runnable_steps` per entry;
`POST /api/procedure/load {"procedure_id": "NOM-002"}` loads it; the MCS
Procedures tab shows the markdown alongside the running steps so the operator
reads the procedure they are executing.

**Shipped exemplars:** `NOM-002` (imaging session → `imaging_pass`), `NOM-003`
(data downlink → `data_dump`), `NOM-009` (routine health check →
`housekeeping_collection`) and `NOM-008` (clock synchronisation, via the
`markdown` adapter). Linking the remaining 60 is a per-procedure Flight-Ops
decision, not a mechanical conversion: most contingency procedures are
deliberately not automatable, because their commanding depends on judgements
the prose describes and the annotations do not carry.

---

## 6. Loading a procedure from the MCS

`POST /api/procedure/load` accepts, in priority order:

| Body key | Source |
|---|---|
| `steps` | an explicit step list in either ground dialect |
| `path` | a saved procedure, e.g. `custom/my_proc.yaml` |
| `procedure_id` | a `procedure_index.yaml` id with a `runnable` link |
| `name` | an activity type from `activity_types.yaml` |

All four validate through `validate_steps` before the runner is touched, and
the response carries `document` (the markdown path, when one is linked) so the
UI can show it.

---

## 7. Forward work

* **Ground → OBCP compiler.** `command` and `wait` steps map cleanly onto the
  S18 step model in `docs/OBCP_GUIDE.md` (`delay_s` + service/subtype/func_id/
  data_hex); `tlm_check`, `go_nogo` and `note` have no on-board equivalent and
  would have to be dropped with an explicit warning, exactly as the S11 upload
  path already does. Not implemented.
* **Migrating `activity_types.yaml`** to the tagged dialect. The adapter makes
  this optional, so it is deferred rather than done under a deadline.
* **`procedure_ref` drift.** Several `activity_types.yaml` `procedure_ref`
  values point at a neighbouring index id (`imaging_pass` → `NOM-001`
  "Pass Startup", not `NOM-002` "Imaging Session"). Every ref resolves to a
  real index entry, so nothing breaks, but the pairing is wrong in places.
  Recorded here rather than silently rewritten — it is a Flight-Ops call, the
  same class as the annotation-drift allowlists in
  `tests/test_content/test_procedures_content.py`. The `runnable:` links above
  are declared from the index side, so they are correct regardless.

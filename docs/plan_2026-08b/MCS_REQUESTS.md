# MCS → other owners: requests raised while building Phase 6

**From:** the single MCS owner (Phase 6 — staleness by cadence, S12 dump
decode, PROP display, and the four DISPLAY_REQUESTS lists)
**To:** the simulator/model owners, the payload owner, the FDIR/procedures
owner and whoever owns `smo-simulator/instructor/app.py`.

Per COHERENCE_REVIEW §2.5 the MCS is edited by one owner and the model owners
submit written requirements. This file is the return channel: everything below
is a change the MCS needs (or a divergence it found) in a file the MCS owner
does **not** own. Nothing here has been implemented.

---

## 1. `TM(8,2)` is not self-describing, and that blocks the image-catalogue view

**Requested by:** the MCS, for the payload owner's D-5 / D-6.
**Files:** `smo_simulator/service_dispatch.py` (payload + prop function blocks).

The payload owner's D-5 asks for an image-catalogue table fed by
`PAYLOAD_GET_IMAGE_CATALOG` (S8 func 32), whose reply is a `TM(8,2)` carrying

```
header : shown(u16) held(u16) product_mb_x100(u32) free_mb_x100(u32)
entry  : scene_id(u16) lat_x1e4(i32) lon_x1e4(i32) size_kb(u32)
         lines(u16) quality_pct(u8) status(u8) flags(u8) segment(u8)
```

**The ground cannot safely decode it, because `TM(8,2)` has no discriminator.**
At least six different producers emit `TM(8,2)` with six different bodies:

| producer | body |
|---|---|
| `PAYLOAD_GET_IMAGE_CATALOG` (func 32) | 12-octet header + N × 20 |
| `PAYLOAD_DOWNLOAD_IMAGE` (func 29) | one 20-octet record |
| `PAYLOAD_INITIATE_TRANSFER` (func 94) | `scene_id(2) bytes(4) blocks(4) crc32(4)` |
| `PAYLOAD_CAPTURE` | 20-octet record into the Science store |
| `PROP_GET_STATUS` (func 118) | propulsion status |
| OBDH health / log reports | their own layouts |

A ground decoder can only tell them apart by correlating against the last
telecommand it sent, which is wrong the moment two are in flight, wrong for a
report solicited by the on-board scheduler (S11) or by FDIR, and wrong for
anything replayed out of a dump — where there is no telecommand at all. That
last case matters most: it is exactly the "where do the image packets go?"
question D-5 exists to answer.

**Request:** give `TM(8,2)` a one-octet `func_id` prefix (or a `report_id`),
the same way `TM(3,25)` leads with its SID and `TM(12,13)` leads with a count.
It is a one-line change in each producer and it makes every S8 report
decodable offline, forever.

**Until then:** D-5 and D-6 are **deferred** on the MCS side. The payload panel
shows product count and product volume (0x0648 / 0x0647), which answers "what
can I downlink?" without needing the report; the per-scene table does not
exist. D-6's CRC verdict depends on the same routing plus S13.3 block
accumulation, so it is deferred with it.

---

## 2. `configs/eosat1/mcs/displays.yaml` is still dead config

**Status:** reconciled, not activated. Recorded here as the coherence review's
§5 asked.

`displays.yaml` described a full three-page propulsion display, served by
`/api/displays`, consumed by nothing — `index.html` is hand-built and never
fetches it. As of this batch the file's propulsion block **matches what the
PROP tab actually shows** (four pages, the interlock mask called out as eleven
named bits, the ground-limit references, the events pane), so the configuration
no longer describes a display that does not exist.

It still does not drive the UI. That is the design study's job
(`/root/design/`), and Franz's instruction stands: *"do not implement yet — I
would like this version to be stable before we change the interface."*

**The wider divergence remains and is not the MCS owner's to close alone:**
every other display block in `displays.yaml` (system_dashboard, power_monitor,
fdir_panel, contact_schedule, procedure_panel, the per-position page sets)
describes widgets that the hand-built console renders differently or not at
all. Making the console config-driven should be one change that deletes the
divergence rather than a per-tab reconciliation, so the rest was deliberately
left alone.

---

## 3. `instructor/app.py` should pass `config_dir` to the dump routes

**File:** `packages/smo-simulator/src/smo_simulator/instructor/app.py:39-45`.

`smo_common.dump_store.register_dump_routes` now takes an optional
`config_dir`, used only to put parameter **names** on the decoded S12 tables
(`eps.bat_soc` rather than a bare `0x0101`). The MCS passes it. The simulator's
instructor server does not, so a dump summarised from port 8080 renders ids
only — the same file, less legible, depending on which port a student opened.

One line:

```python
register_dump_routes(
    app,
    lambda: getattr(engine, "_dump_dir", Path("workspace/dumps")),
    owner="simulator",
    config_dir=getattr(engine, "config_dir", Path("configs/eosat1")),
)
```

(`register_report_routes` immediately below it already passes exactly that
expression, so the value is to hand.)

---

## 4. TCS parameters 0x0424 / 0x0425 are published but undeclared

**Raised by:** the payload owner (DISPLAY_REQUESTS_PAYLOAD §5.3); re-raised
here because the MCS is the consumer that suffers.

`tcs_basic` writes `0x0424 tcs.fpa_cooler_floor_c` and `0x0425
tcs.fpa_cooler_margin_k` every tick, and neither is declared in
`telemetry/parameters.yaml`. Undeclared parameters have no subsystem, no units
and no cadence, so:

* they are absent from `/api/param-catalog` and from the new cadence join, so
  the console cannot judge their freshness;
* they cannot be selected in the ON-DEMAND TM tab or the TRENDS page;
* the MCS's per-parameter cache drops them (`_param_meta` has no entry).

The MCS therefore reads the payload's mirrors (0x064A / 0x064B, HK SID 5) for
the FPA-readiness block, exactly as §1.1 of that document suggested. Declaring
the two TCS parameters — and adding them to HK SID 3 — would let the thermal
tab show the same numbers on the subsystem that owns them.

---

## 5. Two `EVENT_SUBSYSTEM_RANGES` gaps worth knowing about

The console routes events to subsystem panes by id range. The table stopped at
`0x06FF`, so all nineteen propulsion events (`0x0800-0x0812`) fell through
unrouted and appeared on no pane at all. **Fixed in the MCS** (`prop:
[0x0800, 0x08FF]`).

Two things for the model owners:

1. **`0x07xx` is unallocated.** If a future subsystem takes it, tell the MCS
   owner: an unrouted event is invisible rather than obviously wrong, which is
   how the propulsion events went unnoticed for a whole build.
2. **The synthetic S12 event ids (`0x9xxx` / `0xAxxx`) are outside every
   range** and are handled separately (`s12_event_to_param_id`). That is
   deliberate and correct; noted so nobody "fixes" it by extending the table.

---

## 6. `engine.py:2874` still emits a duplicate, wrong `0x0200`

**Raised by:** the AOCS owner (DISPLAY_REQUESTS_AOCS §D-3), unresolved.
**File:** `smo_simulator/engine.py` `_check_subsystem_events`.

```python
modes = {0: 'OFF', 1: 'DETUMBLE', 2: 'SAFE', 3: 'NADIR', 4: 'TARGET'}
```

Every label is wrong for the nine-mode state machine and modes 5-9 render as
`?`, so the event log shows e.g. `AOCS mode: TARGET -> ?` for a
NOMINAL→ECLIPSE transition — *alongside* the model's own correct, reasoned
`0x0200`. Two events with the same id and contradictory text is worse than one.

**The MCS side is done:** `AOCS_MODES` now carries all ten labels from
`aocs_mode_codes.yaml` (INERTIAL was missing, so mode 9 rendered as `MODE_9`),
and the event log wraps long descriptions instead of clipping them, so the
model's reason string and return path survive to the operator.

The MCS cannot suppress the duplicate — it arrives as a legitimate `0x0200`
with a plausible description. The AOCS owner recommended deleting the
emission; the MCS owner agrees and cannot do it (`engine.py` is not
MCS-owned).

---

## 7. `simulation.yaml` `default_enabled_sids` — deliberately NOT changed

Recorded so nobody "helpfully" changes it later.

`default_enabled_sids: [1, 11]` is correct flight-representative behaviour: a
spacecraft does not downlink every structure for free. Three displays depend on
structures that are off at start-up — the per-subsystem power breakdown
(SID 8), the propulsion tab (SID 9) and, on a cold link, everything else.

The MCS now handles this **honestly** rather than by pretending: a fourth
freshness state, `NOT ENABLED`, says *"HK SID 9 (PROP) NOT ENABLED — SEND
TC(3,5) HK_ENABLE"* with a button that sends it, instead of showing "TELEMETRY
STALE" and sending the operator after a link fault. Before this, a disabled
SID's parameters were simply absent from the cache, `subsystem_staleness` had
no key for the subsystem, the chart gate fell through to the global link age
and reported **FRESH while plotting nothing**.

Please leave the default as it is.

---

## 8. One test expectation moved, in an MCS-owned file

`tests/test_mcs/test_procedure_convergence.py::
test_markdown_adapter_works_on_the_linked_document` asserted that every step
extracted from a procedure card is a `command` step. The 2026-08b procedure
work introduced the branching step format, so NOM-008 now yields a `go_nogo`
step too and the assertion failed.

Rewritten to assert what actually matters: **no step carries a type the
executor cannot run** (`command` / `wait` / `tlm_check` / `go_nogo`, per
`ProcedureRunner._execute_step`), and at least one command step was extracted.
Flagged here because the *reason* the assertion changed belongs to the
procedures lane, not the MCS one.

---

## 9. The branching step format needs an executor decision (`label` / `branch`)

**Raised by:** the MCS, for the procedures/FDIR owner (R4).
**Files:** `smo_common/procedures/schema.py` (theirs),
`smo_mcs/procedure_runner.py` (MCS-owned, deliberately not changed here).

The 2026-08b branching format adds two declarative step types —
`{"type": "label", "name": ...}` (a jump target) and the `branch` step that
jumps to one — and cards already emit them: NOM-008 now resolves to a step list
containing a `label`.

`ProcedureRunner._execute_step` understands exactly four types: `command`,
`wait`, `tlm_check`, `go_nogo`. Anything else falls through to the legacy
key-sniffing branch and, failing that, is **counted as PASSED** — which is the
same class of defect the coherence review called out ("silently skips steps
naming an unregistered command while still marking them executed"). A `label`
being skipped is harmless; a `branch` being skipped is a procedure that runs
straight past its own decision.

The MCS owner has **not** implemented branch execution, because the semantics
(bounded loop guard, what a failed condition does to the run state, what the
operator sees while a branch is being taken) are the procedures owner's design
and `schema.py` was still being edited while this phase ran. What has been done
is to stop the MCS test suite asserting the pre-branching contract, and to say
so here.

**Request:** when R4's branching lands, tell the MCS owner what
`_execute_step` should do with `label` (skip, presumably) and `branch`, and the
runner will be extended to match — with the "unknown step type" fall-through
turned into an explicit failure rather than a silent pass.

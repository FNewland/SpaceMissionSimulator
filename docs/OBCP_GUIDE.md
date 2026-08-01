# On-Board Control Procedures (OBCP) — PUS Service 18

EO-Sat-1 supports **on-board scripting** through PUS Service 18. An OBCP is an
ordered list of steps; each step is a *relative delay* plus a *complete PUS TC*
that the spacecraft executes on itself when the delay expires. Because the
steps run on board, a procedure keeps running after LOS — unlike the MCS-side
`ProcedureRunner`, which needs live contact for every step.

| Capability | Where it runs | Survives LOS |
|---|---|---|
| MCS procedures (`configs/eosat1/procedures/`) | ground | no |
| S11 time-tagged TC | on board, one-shot | yes |
| S19 event-action | on board, single parameterless func | yes |
| **S18 OBCP** | **on board, multi-step sequence** | **yes** |

---

## 1. Procedure format

### 1.1 On disk (`configs/eosat1/obcp/*.yaml`)

Every YAML file in `configs/eosat1/obcp/` is pre-loaded at boot (like the S19
rules in `configs/eosat1/monitoring/s19_rules.yaml`), so students can activate
a procedure immediately without uploading anything.

```yaml
procedure:
  id: 1                       # uint16, unique; used by TC(18,2/3/4)
  name: pass_preparation      # free text, shown in logs and events
  description: "..."          # optional, documentation only
  steps:
    - delay_s: 0              # seconds of SIM time, relative to previous step
      service: 8              # PUS service of the step TC
      subtype: 1              # PUS subtype
      func_id: 19             # optional; prepended to the data field (S8 calls)
      data_hex: "02"          # optional; appended as raw hex bytes
```

The loader (`ObcpManager.load_from_dir`) turns each step into a real CCSDS/PUS
TC packet via `build_tc_packet`, using APID `0x1AC` to mark it as
spacecraft-generated. A malformed file is logged and skipped — it never blocks
boot.

Limits: **16 procedures**, **32 steps each** (`obcp.py`, mirrored in
`configs/eosat1/mcs/pus_services.yaml` under `s18`).

### 1.2 On the uplink (TC(18,1) application data)

```
proc_id      uint16
name_len     uint8
name         name_len bytes, UTF-8
n_steps      uint8
  per step:
    delay_s  float32 (big-endian), seconds relative to the previous step
    tc_len   uint16
    tc       tc_len bytes — a COMPLETE PUS TC packet (primary + secondary + CRC)
```

`ObcpManager.encode_load_blob(proc_id, name, steps)` builds this blob
ground-side (used by the tests); `parse_load_blob` decodes it on board.

---

## 2. Telecommands

| TC | Name | Data | Effect |
|---|---|---|---|
| **18,1** | `OBCP_LOAD` | serialized blob (§1.2) | Direct-load / replace a procedure. Replacing a running procedure aborts the run first. |
| **18,2** | `OBCP_DELETE` | `proc_id(2)` | Delete a procedure (aborts it if running). |
| **18,3** | `OBCP_START` | `proc_id(2)` `[+ param(1)]` | Activate. First step fires once its own `delay_s` has elapsed (`delay_s: 0` → next tick). |
| **18,4** | `OBCP_STOP` | `proc_id(2)` | Abort a running procedure. **`proc_id = 0` stops all.** |
| **18,6** | `OBCP_STATUS_REPORT` | — | Request status → **TM(18,7)**. |

**TM(18,7)** layout:

```
count       uint8
  per entry:
    proc_id       uint16
    status        uint8   0 = IDLE (loaded), 1 = RUNNING
    current_step  uint8   index of the step being waited on
    n_steps       uint8
```

Failures (unknown `proc_id`, store full, unparseable blob, start of an
already-running procedure) set the dispatcher error, so the engine returns an
**S1.8 execution failure** — the normal verification path.

---

## 3. Execution semantics

* **Sim time.** `ObcpManager.tick(dt_sim)` is called from the engine main loop
  right after the S11 scheduler (`engine._tick_once`). Delays are consumed in
  *simulated* seconds, so procedures automatically respect the instructor's sim
  speed — one tick at 10× consumes 10 s of delay, and several steps may fire in
  a single tick.
* **Same dispatch path as the ground.** Steps are executed through
  `engine._dispatch_tc`, exactly like uplinked TCs. Every acceptance rule
  therefore applies: the bootloader allowlist, the EPS power gate, the bus
  reachability gate, subtype validation, and full S1 verification (S1.1 / S1.3 /
  S1.7 or S1.8). A step cannot do something an operator could not do.
* **`source=OBCP`.** Self-generated executions are tagged in the log
  (`OBCP <id> step <n>: dispatching S8.1 (source=OBCP)`) and every OBCP event
  description names the procedure, so a self-commanded action is never confused
  with a ground command.
* **Fail-stop.** A step that is malformed or rejected at acceptance raises
  `OBCP_STEP_FAILED` and **aborts the rest of the procedure** — it does not
  silently skip ahead.

### Events (S5, and therefore S19-triggerable)

| ID | Name | Severity |
|---|---|---|
| `0x0700` | `OBCP_STARTED` | INFO |
| `0x0701` | `OBCP_COMPLETED` | INFO |
| `0x0702` | `OBCP_ABORTED` | MEDIUM |
| `0x0703` | `OBCP_STEP_FAILED` | MEDIUM |

All four are in `configs/eosat1/events/event_catalog.yaml`.

### Breakpoints

The procedure store *and* the interpreter state (step index, remaining delay,
activation param) are captured by `BreakpointManager.save()` under the `obcp`
key and restored by `load()`, following the same `get_state`/`set_state`
pattern as `OnboardTMStorage`. A procedure that was mid-run when the instructor
saved a breakpoint resumes at exactly the same step after a load.

---

## 4. Shipped examples

All three use only S8 functions that are genuinely routed by
`service_dispatch.py` (verified by `test_example_steps_use_only_implemented_funcs`).

| ID | Name | What it does |
|---|---|---|
| 1 | `pass_preparation` | EPS `ttc_tx` line on → PA on → deploy antenna → beacon off → set TM rate → transponder TX on |
| 2 | `safe_mode_entry` | EPS SAFE → payload mode off → payload line off → FPA cooler line off → AOCS SAFE |
| 3 | `payload_checkout` | Payload line on → STANDBY → IMAGING → capture (lat/lon) → STANDBY |

Power-line indices for S8 funcs 19/20 (`power_line_on` / `power_line_off`):

```
0 obc   1 ttc_rx   2 ttc_tx   3 payload
4 fpa_cooler   5 htr_bat   6 htr_obc   7 aocs_wheels
```

---

## 5. Running from the MCS

`configs/eosat1/commands/tc_catalog.yaml` exposes five commands under **S18**:
`OBCP_LOAD`, `OBCP_DELETE`, `OBCP_START`, `OBCP_STOP`, `OBCP_STATUS_REPORT`.

Typical flow in the MCS command UI:

1. **`OBCP_STATUS_REPORT`** (no fields) — the TM(18,7) reply lists every loaded
   procedure with its run state, confirming the boot procedures 1–3 are there.
2. **`OBCP_START`** with `proc_id = 1`, `param = 0` — starts pass preparation.
   Expect S1.1/S1.3/S1.7 for the S18 TC itself, then an `OBCP_STARTED` event,
   then one S1 verification set per step as the procedure runs.
3. **`OBCP_STATUS_REPORT`** again — procedure 1 now shows `status = 1` (RUNNING)
   and an advancing `current_step`.
4. **`OBCP_STOP`** with `proc_id = 1` (or `0` for all) if the run must be
   abandoned; **`OBCP_DELETE`** removes it from the store entirely.

**Loading a new procedure from the ground.** `OBCP_START/STOP/STATUS/DELETE`
operate on pre-loaded procedures and are fully usable from the MCS UI. A *full*
multi-step load (`OBCP_LOAD`) needs a serialized blob containing embedded TC
packets, which the catalog can only express as a raw `bytes` hex field — the
same constraint the S11 `SCHEDULE_TC` command has. In practice, new procedures
are added as a YAML file in `configs/eosat1/obcp/` (pre-loaded at boot), or
built programmatically with `ObcpManager.encode_load_blob` from a test or
script. The catalog entry is retained for advanced/raw use.

---

## 6. Known limitations

* No conditional logic, loops, or `wait_for <condition>` steps — a procedure is
  a linear timed sequence. Conditional autonomy is S19's job (single func) or
  FDIR's.
* The activation `param` byte is stored and reported but not yet consumed by
  the step interpreter (no step-level parameter substitution).
* Delays are relative and monotonic; there is no absolute-time step (use S11 to
  time-tag the `OBCP_START` itself if a procedure must begin at a fixed CUC).
* Pre-existing, *not* an OBCP issue: the TTC set-TM-rate function (S8 func 65)
  reaches the model, but the TTC tick recomputes `tm_data_rate` from
  `data_rate_mode`, which no telecommand can set. The high rate is therefore
  not reachable from the ground today, whether commanded directly or from an
  OBCP.

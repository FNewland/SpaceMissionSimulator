# Threading and state ownership

**Audience:** anyone changing the simulator, and any student asked to explain
why the instructor console and the MCS can disagree by one tick.

**Scope:** who writes what, when a reader is allowed to look, when the
snapshot publishes, and exactly what a breakpoint does and does not capture.

Until the 2026-08 build this contract existed only as a comment block in
`engine.py`. It is written down here because two of the three worst defects in
the suite's history lived in it: an instructor console that could crash the
response thread by iterating a growing image catalogue, and a breakpoint that
silently destroyed the on-board time-tagged schedule.

---

## 1. The threads

| Thread | Owns | Started by |
|---|---|---|
| **Tick thread** | *All* simulation state. The orbit propagator, `engine.params`, every subsystem model's `_state`, the S11 scheduler, the FDIR model, the dump pacer. | `SimulationEngine.start()` |
| **aiohttp thread** | The HTTP/WS servers on 8080, and nothing else. | `smo-simulator` main |
| **TC socket thread** | Accepts telecommand frames off port 8001 and hands them to the tick thread. | `smo-simulator` main |
| **TM socket thread** | Drains the outbound packet queue to port 8002. | `smo-simulator` main |

**One rule:** *simulation state is written by the tick thread and by nothing
else.* Every other thread reads a published snapshot or posts a request onto a
queue. There is no lock you may take to make it safe to write from elsewhere,
because the tick is a physics integration and a half-applied write has no
physical cause.

---

## 2. The snapshot contract

At the end of every tick, `_publish_snapshot()` builds **two** immutable
snapshots and swaps them in under `_params_lock`:

| Snapshot | Built by | Read by | Contents |
|---|---|---|---|
| **State summary** | `_build_state_summary()` | `get_state_summary()` → `/api/state`, the WS feed, the MCS | RF-gated spacecraft state — what the ground can legitimately see |
| **Instructor snapshot** | `_build_instructor_snapshot()` | `get_instructor_snapshot()` → the instructor console | Ground truth: every parameter plus every model's internal state, RF gating bypassed |

Three properties follow, and all three are load-bearing:

1. **Every field in a snapshot comes from the same tick.** A reader can never
   see the SoC from tick *n* next to the bus voltage from tick *n+1*. That
   sounds pedantic until a student is asked to explain a 0.4 V discrepancy
   that was an artefact of the display.
2. **Snapshots are deep-copied at build time.** `_build_instructor_snapshot`
   copies the dicts and lists — power lines, wheel speeds, the image catalogue
   — rather than storing references. Before this (defect A-1) the instructor
   snapshot was assembled *on the aiohttp thread*, walking every subsystem's
   `_state.__dict__` while the tick thread mutated it, and storing containers
   by reference. `json.dumps` on the response thread could raise
   `RuntimeError: dictionary changed size during iteration` against a growing
   `image_catalog`; a caller that mutated the response would have corrupted
   the simulation. The instructor console is the display most likely to be
   open during an anomaly, so this was the worst possible place for it.
3. **Readers never block the tick.** Taking `_params_lock` costs one pointer
   swap; the expensive build happens on the tick thread where the cost is
   already budgeted.

Before the first tick completes there is no snapshot, so both readers build
one inline. That is safe by construction: nothing is ticking yet.

### 2.1 The one-tick lag

A reader always sees the *previous* tick's state. At ×1 that is up to one
second stale; under acceleration it is one *simulated* tick, which is up to
`dt_max_s × max_substeps` of simulated time. Displays that need to show
freshness read `tick` and `sim_time` out of the snapshot itself — both are
snapshot fields, not live reads.

---

## 3. Writes from other threads: the instructor queue

Anything that must *change* state from off-thread goes through
`engine.instr_queue`, a bounded `queue.Queue(maxsize=200)` drained by
`_drain_instr_queue()` at the top of each tick, before the physics.

That covers failure injection, scenario start/stop, time control, power-line
overrides, **and breakpoint save/load**. Breakpoints were the last holdout:
`BreakpointManager.save()`/`load()` used to run inline on the aiohttp thread,
reassigning `eng.params`, `eng._sim_time` and every model's state with no
coordination with `_tick_once`. A load landing mid-tick produced a spacecraft
state with no physical cause — the worst thing to hand a student in the middle
of an exercise. Both now post onto `instr_queue` with a future and the HTTP
handler waits for the result.

Telecommands take the same route: the TC socket thread parses and queues; the
tick thread dispatches. This is also why a TC's effect is never visible in the
same HTTP response that acknowledged it.

---

## 4. What a breakpoint captures

### 4.1 The protocol

`smo_common.state.Stateful` is a runtime-checkable protocol with
`get_state() -> dict` and `set_state(dict)`. The engine keeps a **registry**:

```python
self.register_stateful("engine", self)
for name, model in self.subsystems.items():
    self.register_stateful(f"model:{name}", model)
self.register_stateful("tm_storage", self._tm_storage)
self.register_stateful("obcp", self._obcp_manager)
self.register_stateful("memory", self._memory)
self.register_stateful("tc_scheduler", self._tc_scheduler)
self.register_stateful("failure_manager", self._failure_manager)
self.register_stateful("scenario_engine", self._scenario_engine)
self.register_stateful("dispatcher", self._dispatcher)
self.register_stateful("load_shedding", self._load_shedding)
self.register_stateful("fault_propagator", self._fault_propagator)
self.register_stateful("procedure_executor", self._procedure_executor)
```

`BreakpointManager` iterates the registry. Two consequences, both deliberate:

* **A component that is registered but has no state methods fails the test**
  (`tests/test_simulator/test_breakpoint_round_trip.py`), rather than silently
  losing its state. The design is inverted from what it was: components must
  opt *out* loudly, not opt in quietly.
* **A partial save fails the whole save.** `breakpoints.py` used to swallow
  per-subsystem failures with a warning and write an incomplete file that
  loaded without complaint. A breakpoint you cannot trust is worse than no
  breakpoint.

### 4.2 What used to be lost

For the record, because it explains several "the simulator forgot" reports.
Every item below was **lost on load** before the Phase A/G work and is now
captured:

| State | Consequence of losing it |
|---|---|
| `_spacecraft_phase` | Param `0x0129` *was* restored, so after a load the display and the bootloader gate disagreed: the console said BOOTLOADER while application commands were accepted |
| `TCScheduler` (no state methods at all) | **The entire S11 on-board time-tagged schedule was destroyed by a load** — the exact workflow defects #44–#47 were fixed to enable |
| `FailureManager`, `ScenarioEngine` | Injected failures and running scenarios lost, so "save → inject → compare" worked in one direction only |
| S12 enable flags **and delta baselines**, S19 definitions and enable set, in-flight S13 transfers | Every commanded monitor enable/disable reverted; a transfer in progress vanished |
| `_current_load_shed_stage`, `LoadSheddingManager._stage` | Reset to 0 while the restored power lines were still shed |
| `_prev_in_contact`, `_prev_in_eclipse`, `_prev_aocs_mode`, `_prev_obdh_mode`, `_prev_payload_mode`, `_prev_active_gs`, `_in_contact` | On the first tick after a load the whole edge-detector set re-fired: spurious AOS, eclipse-entry and mode-change events in the log |
| `_dump_pending`, `_dump_active_stores`, `_dump_byte_budget`, `_rt_pending` | An in-progress S15 dump and the rate-pacing backlog |
| `_sep_timer`, `_auto_tx_remaining`, `_fdir_safe_active`, `_fdir_emergency_active`, `_override_passes`, TM sequence counters | Assorted one-shot timers and latches |
| Every component's **RNG state** | A restored run diverged from the saved one even with the same seed |
| TCS `_fpa_cooler_target` | A commanded FPA setpoint reverted to the configured default (found in G3-d — it lived on the model, not on `_state`, so `dataclasses.asdict` missed it) |

That last row is the shape of the residual risk: state stored as a plain model
attribute rather than a `_state` field is invisible to the dataclass sweep.
The round-trip property test perturbs *everything* and deep-compares, which is
what catches it.

### 4.3 What a breakpoint deliberately does **not** capture

* **Wall-clock time.** A breakpoint restores simulated time, not the hour you
  saved it at. Dump filenames carry wall clock; packet CUC carries sim time
  (see `docs/DETERMINISM.md` §5).
* **Ground-segment state.** The MCS's alarm journal, verification log and TM
  archive live in the MCS process and are not part of a simulator breakpoint.
  Loading a breakpoint rewinds the spacecraft, not the control room — which is
  realistic, and is worth saying out loud to students who expect otherwise.
* **Connected sockets.** Clients stay connected across a load.

---

## 5. Reading this in the code

| Concern | Where |
|---|---|
| Snapshot publish | `engine.py: _publish_snapshot`, `_build_state_summary`, `_build_instructor_snapshot` |
| Snapshot read | `engine.py: get_state_summary`, `get_instructor_snapshot` |
| Off-thread writes | `engine.py: instr_queue`, `_drain_instr_queue`, `submit_instructor_cmd` |
| State protocol | `packages/smo-common/src/smo_common/state.py` |
| Registry | `engine.py: register_stateful` |
| Save/load | `packages/smo-simulator/src/smo_simulator/breakpoints.py` |
| Round-trip test | `tests/test_simulator/test_breakpoint_round_trip.py` |
| Thread-safety soak | `tests/test_simulator/test_instructor_snapshot.py` |

# Design Issues Register — SpaceMissionSimulation + SpaceCDF
Review item 1 of 10 · 2026-07-28 · read-only audit of `SpaceMissionSimulation` (M) and `SpaceCDF` (C).
All paths relative to the repo roots. Every finding verified by reading the cited code.
Note: the repo carries its own defect register (`DEFECTS.md`, #1–40); overlaps are cross-referenced, not re-reported.

Severity counts: **Critical 3 · Major 15 · Minor 12** (30 findings)

---

## Critical

### DI-01 — Simulation tick thread can die silently on any unhandled exception; unvalidated WS input can trigger it
- **Severity:** Critical
- **Where:** `packages/smo-simulator/src/smo_simulator/engine.py:822-836` (`_run_loop` — no try/except around `_tick_once`), `engine.py:1755-1756` (`set_speed` → unguarded `float(cmd.get('value'))`), `engine.py:1774` (`float(cmd['duration_s'])`), `packages/smo-simulator/src/smo_simulator/instructor/app.py:55-65` (WS handler enqueues **any** parsed JSON with no allowlist, wrapped in `except Exception: pass`)
- **Description:** `_run_loop` executes `_tick_once` with no exception guard. `_drain_instr_queue` catches only `queue.Empty`, so a `ValueError`/`TypeError` raised inside `_handle_instructor_cmd` (e.g. WS message `{"type":"set_speed","value":"fast"}`) propagates up and kills the daemon sim thread. `self.running` stays `True`; the aiohttp/TCP servers keep serving the last frozen state, so the failure is invisible except that telemetry stops updating. The HTTP `/api/command` path has an allowlist and rejects bad input; the WebSocket path (`app.py:57-60`) has none — two channels, two trust levels, one queue.
- **Why it matters:** A single malformed instructor input (or any latent bug in the ~20 functions called per tick that lack their own guard, e.g. `_emit_hk_packets`, `_tick_s12_monitoring`) permanently halts the spacecraft simulation mid-exercise with no alarm.
- **Remedy:** Wrap `_tick_once` in a try/except that logs and continues (or sets a visible `engine_fault` flag); validate instructor command payloads (types/ranges) once, in one place, before enqueueing; apply the same allowlist to WS as HTTP.
- **Effort:** S

### DI-02 — Shared parameter store and model state accessed cross-thread with a lock that only the readers take
- **Severity:** Critical
- **Where:** `engine.py:75-76` (`self.params` + `_params_lock`), writes without lock at `engine.py:848` (`self.params[0x05FF]=…` in tick), `engine.py:876` (models mutate `self.params` inside `model.tick`), `engine.py:1798` (instructor handler); the **only** `with self._params_lock` sites are the readers `engine.py:2183` (`get_state_summary`) and `engine.py:2289` (`get_instructor_snapshot`). Also unsynchronised cross-thread reads of mutable objects: `_get_power_lines_state` → `eps._state.power_lines` (`engine.py:2274-2279`), `self._tm_storage.get_status()`, `self._failure_manager.active_failures()` (`engine.py:2268-2270`; neither class has a lock — see lock inventory: only `service_dispatch.py:19` and `tc_scheduler.py:16` have one).
- **Description:** The tick thread writes `params` and every subsystem `_state` dataclass while aiohttp handlers (instructor snapshot, `/api/state`, WS broadcast at 1 Hz) read them. A lock acquired only on the read side provides zero mutual exclusion. CPython's GIL prevents interpreter crashes but not torn multi-field reads (e.g. `power_lines` dict copied mid-update, SoC and battery current from different ticks) or `RuntimeError: dictionary changed size during iteration` on `dict(self.params)` (dict() of a dict being resized is not guaranteed atomic).
- **Why it matters:** Intermittent, unreproducible glitches in instructor/MCS displays and occasional 500s; the lock's presence gives false confidence that the design is thread-safe.
- **Remedy:** Either acquire `_params_lock` at the single write point (publish a snapshot dict at end of tick, readers get the immutable snapshot) or move state export onto the tick thread via a request queue. Document the threading contract on `SimulationEngine`.
- **Effort:** M

### DI-03 — MCS silently fabricates telemetry and contact data when real sources are unavailable
- **Severity:** Critical
- **Where:** `packages/smo-mcs/src/smo_mcs/server.py:1838-1874` (`_generate_dump_data`: `random.gauss`/`math.sin` synthetic HK history), used at `server.py:1815-1819` (fallback when archive empty after a real S15.9 dump command) and served by `_handle_tm_dump_data` (`server.py:1832-1836`) **with no `synthetic` flag in the response**; `server.py:1744-1772` (`_build_synthetic_contacts`: hard-coded Iqaluit/Troll passes, at least flagged `"synthetic": true` at 1746)
- **Description:** When the operator commands a stored-TM dump and the archive has nothing, the MCS invents 60 plausible-looking data points (SoC, rates, temps) and returns them as dump playback. The contacts proxy similarly invents six passes when the planner is down. The dump path gives the UI no way to distinguish fake from real.
- **Why it matters:** This is a *training* mission-control tool: trainees can perform anomaly investigation on data that never came from the simulated spacecraft, and the synthetic contact times bear no relation to the actual orbit — directly undermining the system's purpose and trust in every display.
- **Remedy:** Remove `_generate_dump_data`; return an explicit "no archived data" state the UI must render. If synthetic fallback is retained for demos, tag every payload `synthetic: true` and render it visibly (as the contacts path partially does), behind a config flag defaulting to off.
- **Effort:** S

---

## Major

### DI-04 — Ground-station truth is defined in four places with three different merge policies
- **Severity:** Major
- **Where:** `configs/eosat1/orbit.yaml:12-24` (lat/lon/alt/min_el for Iqaluit+Troll), `configs/eosat1/planning/ground_stations.yaml` (same four geo fields duplicated + RF fields), `configs/eosat1/mission.yaml` `ground_segment.stations` (name list), and consumers: engine uses **orbit.yaml only** (`engine.py:105-118`), planner uses **orbit.yaml only** (`smo-planner/server.py:76-98` via `load_orbit_config`), MCS **merges both files** with name-dedup, orbit.yaml winning (`smo-mcs/server.py:358-386`).
- **Description:** Station geometry is copy-pasted between two YAMLs; a third file lists names. Consumers disagree about which file is authoritative, so adding/editing a station in `planning/ground_stations.yaml` changes MCS pass predictions but not the simulator's actual contact gating or the planner's schedule. The planning file's own header comment ("Canonical source: mission.yaml") points at yet another file.
- **Why it matters:** Classic drift trap: sim, planner and MCS can silently compute different AOS/LOS for the "same" mission. This is exactly the divergence class the task flagged.
- **Remedy:** Single `ground_stations.yaml` holding geometry + RF, referenced (not copied) by orbit/planning concerns; one loader in `smo_common`; delete the duplicate block and the name list, or generate them.
- **Effort:** M

### DI-05 — RF frequency plan contradicts itself across configs: TTC model flies UHF while the rest of the mission is S-band
- **Severity:** Major
- **Where:** `configs/eosat1/subsystems/ttc.yaml:2-3` (`ul_freq_mhz: 449.0`, `dl_freq_mhz: 401.5` — UHF), consumed by `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py:185-190` for the link budget; vs `configs/eosat1/planning/ground_stations.yaml` (S-band 2025/2200 MHz, "per CCSDS 401.0-B") and `configs/eosat1/rfsim.yaml:19-21` (2200 MHz, S-band path loss 169 dB). Additionally the per-station `g_t_db` (20.0/18.5) in ground_stations.yaml is **never read by any code** — the TTC model uses one global `gs_g_t_db: 20.0` (`ttc_basic.py:184`; the only code references to `g_t_db` are the schema default and this config key), so Troll's poorer G/T never affects the link.
- **Description:** The simulated link budget, the RF-sim channel, and the planning config each assert a different (or partially dead) RF truth.
- **Why it matters:** Link margins, path loss and Doppler taught to trainees are internally inconsistent (~15 dB FSPL difference between 401 MHz and 2.2 GHz at the same range); per-station performance differences configured on paper have no effect.
- **Remedy:** Move the frequency plan and per-station G/T into the single ground-station file (DI-04) and have `ttc_basic` resolve the *current* station's G/T from the contact state.
- **Effort:** M

### DI-06 — `SimulationEngine` is a god object with a 300-line constructor that reaches into subsystem privates
- **Severity:** Major
- **Where:** `engine.py` (2363 lines, ~60 methods): orbit, phase state machine, TC accept/dispatch glue, FDIR ×2 generations, HK scheduler, S15 dump pacing, S12 monitoring, breakpoints, scenarios, instructor commands, TM link gating, event edge-detection. `__init__` spans lines 68–292. Encapsulation breaks: `obdh._state.sw_image = 0` (`engine.py:200-204`, again at `1826-1830`), 50 lines of `aocs._state.*` field pokes (`engine.py:226-269`), `self._tm_storage._stores` (`engine.py:316`), `dispatcher._last_error` (see DI-08). Pattern is enabled by pervasive `hasattr(...)` duck-typing (14 uses in `_enter_bootloader_mode` alone). Refactor artifact `if True:` wrapping the whole tick body at `engine.py:842`.
- **Description:** Every new feature (bootloader mode, auto-TX hold, torque gains) has been bolted onto the engine, and the engine configures subsystem internals field-by-field instead of asking models to enter a mode.
- **Why it matters:** The bootloader block silently does nothing when a model renames a private field (the `hasattr` guards turn API drift into no-ops — the exact failure mode behind historic defects #23/#27/#29); testing any one concern requires constructing the whole engine.
- **Remedy:** Extract phase/bootloader state machine, HK scheduler, and dump manager into collaborators; give models an explicit `enter_unpowered()`/`set_boot_image()` interface; delete `if True:`.
- **Effort:** L

### DI-07 — `mission.yaml` network/tick settings are dead config; ports and tick rate are hard-coded
- **Severity:** Major
- **Where:** `configs/eosat1/mission.yaml` `network:` block (tc 8001, tm 8002, instr 8003, http 8080, `sim_tick_hz: 1.0`, `tm_max_clients: 8`) vs `packages/smo-simulator/src/smo_simulator/server.py:155-158` (ports hard-coded in `main()`), `engine.py:823` (`tick_hz = 1.0  # default` — no other source), `smo_common/config/loader.py:11` imports `NetworkConfig` but no `load_network_*` function exists and nothing instantiates it. `instr_port: 8003` and `tm_max_clients` are used nowhere.
- **Description:** The config file promises tunability the code ignores; operators editing it get silent no-ops.
- **Why it matters:** Unvalidated/unused config is indistinguishable from working config — the same class of bug as fixed defect #40 (fields parsed but never checked). Also blocks running two sims side-by-side.
- **Remedy:** Load the network block (or delete it); plumb `sim_tick_hz` into the engine; fail loudly on unknown keys (see DI-15).
- **Effort:** S

### DI-08 — Command execution errors signalled through a mutable side-channel on the dispatcher
- **Severity:** Major
- **Where:** `engine.py:1262-1275` (`dispatcher._last_error = None … dispatch() … if getattr(dispatcher, '_last_error', None):`), `service_dispatch.py:21-22, 688-692` (`_make_error_response` sets `_last_error`/`_last_error_code` as its way of reporting failure)
- **Description:** `dispatch()` returns TM packets but reports success/failure by mutating instance attributes that the engine must reset before and inspect after each call. Not reentrant, invisible in the type signature, and any nested dispatch (S11 time-tagged release, S19 autonomous actions both call into the same dispatcher instance) can clobber the flag of the outer command.
- **Why it matters:** S1.7 "execution complete" vs S1.8 "failure" verification — the operator's ground truth — depends on fragile hidden state; a missed reset silently converts failures into successes.
- **Remedy:** Return a result object `(packets, error_code | None)` from `dispatch()`.
- **Effort:** S

### DI-09 — PUS service/subtype/function-ID knowledge duplicated in ≥4 hand-maintained places
- **Severity:** Major
- **Where:** `engine.py:1088-1112` (`_check_tc_acceptance` hard-codes the known services and every valid subtype set), `engine.py:1160-1180` (bootloader func-ID allowlist `{19,20,52…57,61,66,67,69}` — comment admits it "mirrors `obdh_basic._BOOTLOADER_ALLOWED`"), `service_dispatch.py:1472-1607` (`_func_id_owner`/`_func_id_subsys` routing tables), `configs/eosat1/mcs/pus_services.yaml` + `loader.py:174-180` (`load_pus_services` — **called by nothing**), `configs/eosat1/commands/tc_catalog.yaml` (MCS-side view). `smo_common/protocol/pus_services.py` exists but is imported by no package (register defect #17, still open).
- **Description:** The same routing truth is re-declared in engine, dispatcher, OBDH model, and two config files, with a dead loader and a dead common module alongside.
- **Why it matters:** This exact duplication caused defect #37 (stale S8 func map → spacecraft autonomously booted its own OBC) and #38 (commands silently dropped). The structure that produced those bugs is still in place.
- **Remedy:** Make one machine-readable service/function table (config or generated module) the source for acceptance checks, dispatch routing, bootloader allowlist, S19 validation and the MCS catalog; delete `pus_services.py`/`load_pus_services` or wire them in.
- **Effort:** M

### DI-10 — Full legacy standalone implementation retained in `files/`, drifted 3+ months from the live packages
- **Severity:** Major
- **Where:** `files/` — 20 modules incl. `engine.py` (553 lines), `service_handlers.py` (1450), `ecss_decommutator.py` (1600), `scenario_engine.py` (624), `sim_server.py` (599), plus a parallel set of HTML UIs; all dated 2026-04-10 while `packages/` continued evolving (defects #23–#40 fixed only in packages). `files/engine.py:13-17` shows the old flat-import architecture (`from config import SIM_TICK_HZ…`).
- **Description:** The pre-monorepo simulator lives beside its successor with overlapping module names (`engine.py`, `scenario_engine.py`, `failure_manager.py`, `fdir.py`). It has a populated `__pycache__`, so it has been executed from this tree.
- **Why it matters:** Copy-paste drift and wrong-file edits: a grep for a bug lands in two `engine.py`s, and fixes applied to one silently miss the other; new contributors cannot tell which decommutator is real.
- **Remedy:** Move `files/` to an `attic/` branch or delete it (git history preserves it); if anything in it is still needed (e.g. `ecss_decommutator` features), port explicitly into `smo_common`.
- **Effort:** S

### DI-11 — Instructor command surface: engine branches unreachable from HTTP, WS channel unvalidated, pause state never restored
- **Severity:** Major
- **Where:** `app.py:88-114` (HTTP allowlist lacks `pause_scenario`, `start_separation` which the engine handles at `engine.py:1836-1858`; grep of `instructor/static/index.html` shows the UI never sends them either), `app.py:55-62` (WS bypasses the allowlist entirely — see DI-01), `engine.py:1838-1843` (`pause_scenario` saves `_pause_saved_speed`, but no code path anywhere restores it — `resume` at 1759 recomputes from `max(1.0, …)`, losing the pre-pause speed)
- **Description:** Three command types exist only as latent engine code; the saved-speed handshake is half-implemented dead state.
- **Why it matters:** The historic "UI posts a type the engine has no branch for" pattern (defects #9/#10/#12) now has its mirror image: engine branches no client can reach via the validated channel, which will confuse the next person wiring UI buttons; pausing a scenario at 10× resumes at 1×.
- **Remedy:** Make the allowlist and the engine's branch set the same constant (import one from the other); finish or delete the pause/resume speed restore.
- **Effort:** S

### DI-12 — Breakpoint save/load executes on the web thread while the sim thread runs
- **Severity:** Major
- **Where:** `app.py:186-202` (`handle_breakpoint_save/load` construct `BreakpointManager(engine)` and call `save()`/`load()` inline in the aiohttp handler) vs `engine.py:1859-1863` (the WS/instructor-queue path routes `save_breakpoint`/`load_breakpoint` to the tick thread via `_handle_save_breakpoint`)
- **Description:** The same operation has two concurrency regimes. The HTTP path snapshots/restores every subsystem `_state` and the orbit clock while `_tick_once` may be mid-mutation — a load can interleave with a tick, leaving half-restored state (the bug class of fixed defect #35, reintroduced through the side door).
- **Why it matters:** Instructor "save → inject failure → load" is the core teaching workflow; a torn restore produces impossible spacecraft states that are very hard to diagnose.
- **Remedy:** Route the HTTP handlers through `instr_queue` like the WS path (return the result via a future), or pause the engine around save/load.
- **Effort:** S

### DI-13 — `MCSServer` is a 2128-line god class that also re-implements config loading and orbit propagation
- **Severity:** Major
- **Where:** `packages/smo-mcs/src/smo_mcs/server.py` — ~70 route handlers plus TM decode, TC building, ground-time model, orbit propagation, go/no-go coordination, procedure engine glue, doc serving, synthetic data (DI-03). Config bypass: `_init_orbit_propagator` (`server.py:338-390`) opens `orbit.yaml` with raw `yaml.safe_load` and hand-rolled fallbacks (`tle_line1` or `TLE_LINE1`) instead of `smo_common.config.loader.load_orbit_config` used by engine and planner. ~15 function-local `import yaml`/`import os`/`import time` statements (e.g. 191, 246, 277, 285, 1142, 1277, 1591).
- **Description:** One class owns the entire ground segment; the shared config layer exists but is bypassed, so schema validation (pydantic) protects the simulator and planner but not the MCS.
- **Why it matters:** The MCS is where field-name and schema drift bites operators (defects #13/#20/#36 were all MCS-side drift); bypassing the validated loader guarantees the MCS diverges first.
- **Remedy:** Split into modules (tm path, tc path, time, displays, procedures, api); replace raw YAML reads with `smo_common` loaders; hoist local imports.
- **Effort:** L

### DI-14 — Cross-package private-attribute reach-ins in the MCS data path
- **Severity:** Major
- **Where:** `smo-mcs/server.py:652` (`self._tm_processor._process_hk(pkt.data_field)` — private method is the *only* HK decode path, per its own comment), `server.py:1787` (`seq = self._tc_manager._seq_count` to label the verification log)
- **Description:** The server depends on private internals of its collaborators; `TMProcessor` and `TCManager` expose no public API for their primary functions.
- **Why it matters:** Renaming an internal silently breaks the only route by which spacecraft state reaches the MCS (no interface contract, no test seam).
- **Remedy:** Promote `process_hk()` and `last_seq()`/`build_command() -> (pkt, seq)` to public API.
- **Effort:** S

### DI-15 — RF-sim config loader silently drops unknown keys — and is already dropping a real one
- **Severity:** Major
- **Where:** `packages/smo-rfsim/src/smo_rfsim/config.py:84-95` (`from_yaml`: `if hasattr(cfg.section, k): setattr(...)` — unknown keys ignored without warning); live instance: `configs/eosat1/rfsim.yaml:19` sets `channel.frequency_hz: 2200.0e6`, but `ChannelConfig` (`config.py:36-45`) has no `frequency_hz` field, so the setting is discarded every start-up.
- **Description:** Typos and schema drift in `rfsim.yaml` are undetectable; one shipped key is already dead.
- **Why it matters:** Same "config lies" class as DI-07/defect #40, in the package with the least test coverage. Also note `smo_rfsim.config.NetworkConfig` name-collides with the unused `smo_common` `NetworkConfig` (DI-07), inviting wrong-import bugs.
- **Remedy:** Log (or raise on) unknown keys in `from_yaml`; add `frequency_hz` to `ChannelConfig` or remove it from the YAML; rename one of the `NetworkConfig`s.
- **Effort:** S

### DI-16 — Orbit epoch is wall-clock at process start; configured `t0_epoch` ignored by the simulator
- **Severity:** Major
- **Where:** `smo_common/orbit/propagator.py:70` (`self._sim_utc = datetime.now(timezone.utc)` default), `engine.py:105-118` (constructs `OrbitPropagator` without an epoch and never calls `reset()` with one), `configs/eosat1/orbit.yaml:7` (`t0_epoch: "2026-03-10T00:00:00Z"`), consumed **only** by the MCS for MET display (`smo-mcs/server.py:356, 441-444`)
- **Description:** Every simulator run starts the orbit at the current wall-clock UTC against a TLE with epoch day 26068.5, while the MCS computes MET against the fixed configured T-0.
- **Why it matters:** Runs are non-reproducible (eclipse/pass geometry differs every start — SGP4 error also grows with months-stale TLE age), scenario YAMLs that assume pass timing behave differently day to day, and MET shown to trainees has no relation to the sim's actual elapsed time.
- **Remedy:** Have the engine `reset(t0_epoch)` at construction (config flag `epoch: wall|t0` if wall-clock start is sometimes wanted).
- **Effort:** S

### DI-17 — 160 broad `except Exception` sites across smo-* packages, many swallowing errors to `pass`/`continue`
- **Severity:** Major
- **Where:** Count: 160 in `packages/` (grep `except:`/`except Exception`). Representative silent swallows verified: `engine.py:313-315` and `408-410` (APID switch failure → `pass` — spacecraft keeps transmitting on the wrong APID), `app.py:60-61` (WS receive loop → `pass`, client errors invisible), `smo-mcs/server.py:698-700` (S20 param report parse → `pass`), `smo-mcs/server.py:196-198, 250-252, 289-291` (config reads default silently), `smo-gateway/downstream.py:53-55`, `smo-rfsim/radio/web_ui.py:42-44`.
- **Description:** The dominant error-handling idiom is catch-everything; a minority log at `debug`/`warning`, a substantial subset discard entirely.
- **Why it matters:** This idiom is how ten of the historic defects stayed hidden ("commands silently dropped"); it converts protocol and config faults into blank UI panels.
- **Remedy:** House rule: no bare `except Exception` without a `logger.warning` including context; narrow the ones guarding a single known failure (file-not-found, parse) to specific types. Prioritise engine + MCS TM path.
- **Effort:** M

### DI-18 — SpaceCDF `exports.py`: 2516-line router with all derivation/generation logic in the web layer
- **Severity:** Major
- **Where:** `SpaceCDF/packages/spacecdf-server/src/spacecdf_server/routers/exports.py` — 12 export endpoints plus ~20 `_derive_*` engineering functions (state machines, memory maps, TM/TC tables, fault handlers, architecture derivation, lines 43–640) and inline python-docx document assembly (e.g. lines 2324-2325), 23 `except Exception` sites; meanwhile a `services/` layer exists (`docx_generator.py`, `semp_generator.py`, `did_generator.py`) that this router largely bypasses.
- **Description:** Router = controller + domain logic + document renderer in one module.
- **Why it matters:** Untestable without HTTP, uncached, and the derivation rules (the actual engineering content) are buried where no one looks for them; largest file in the repo and growing with every new export type.
- **Remedy:** Move `_derive_*` into `services/` (or `spacecdf_common`), keep routers thin; share the docx assembly with the existing generators.
- **Effort:** L

### DI-19 — SpaceCDF: cross-router import of a private in-memory global as a data source
- **Severity:** Major
- **Where:** `exports.py:2069` — `from ..routers.requirements import _requirements` inside `export_test_plan`; `_requirements` is a module-level mutable store in another router.
- **Description:** The test-plan export reads requirements from one router's process-local global while other exports read the DB (`aiosqlite` via `db/repo.py`), creating two divergent sources of requirement truth and coupling routers to each other's privates.
- **Why it matters:** Breaks under multi-worker deployment (each worker has its own `_requirements`), returns empty/stale test plans after restart, and hides a data dependency from the architecture.
- **Remedy:** Read requirements through the repository layer; make `requirements` router persist to the same store.
- **Effort:** S

### DI-20 — SpaceCDF→SMO exporter re-implements the SMO config schema by hand with no shared contract
- **Severity:** Major
- **Where:** `SpaceCDF/packages/spacecdf-agents/src/spacecdf_agents/exporters/smo/exporter.py` (generates "~20 YAML files" — mission.yaml, orbit.yaml, subsystems/*, etc. — per its docstring, lines 1-60; also reads its allocator's private `self._allocator._allocations` at line 54) with no import of, or generated schema from, `smo_common.config.schemas`
- **Description:** The concurrent-design tool emits configs the simulator consumes, but the schema knowledge is duplicated prose-style in a second repo. SMO-side schema changes (e.g. the defect-#40 `on`/`off` field rename) will silently produce configs the pydantic loaders reject — or worse, accept with changed meaning.
- **Why it matters:** This is the integration seam between the two audited systems; it is unversioned and untested against the real loaders.
- **Remedy:** Publish `smo_common` schemas as the contract (SpaceCDF imports them and validates each generated file with the same pydantic models before writing); add a round-trip test that boots the SMO loader on an exported config set.
- **Effort:** M

---

## Minor

### DI-21 — Hand-maintained three-way parameter naming contract (params YAML ↔ engine ↔ MCS UI)
- **Where:** `engine.py:2174-2273` (`get_state_summary` hard-codes ~50 param IDs and unit-suffixed field names; its own docstring admits "The MCS web UI hardcodes these field names"), `smo-mcs/server.py:26-69` (`_UI_KEY_MAP` bridging a *third* naming scheme), `configs/eosat1/telemetry/parameters.yaml` (short_keys). Drift here caused fixed defect #36 (eclipse keys never produced). Remedy: generate the summary grouping and UI keys from `parameters.yaml`. Effort: M.

### DI-22 — Magic numbers in engine that belong in mission config
- **Where:** `engine.py:229-234` (auto-TX hold 15 min), `engine.py:232` (`_sep_timer_duration = 1800.0`), `engine.py:82-90` (queue sizes 2000/500/200/500), `engine.py:56` (`SC_PHASE_NOMINAL = 6`), `engine.py:823` (tick 1 Hz — see DI-07), `smo-mcs/server.py` synthetic-data constants. Remedy: hoist to `mission.yaml` (validated). Effort: S.

### DI-23 — Stub endpoint returns hard-coded empty catalog
- **Where:** `smo-simulator/server.py:141-142` (`/api/catalog` → `{"failures": [], "commands": []}`) while a real 182-command catalog exists (`load_tc_catalog`). Either wire it or delete the route. Effort: S.

### DI-24 — TM broadcast loop busy-polls and serialises slow clients
- **Where:** `smo-simulator/server.py:104-125` — `get_nowait()` + `except Exception: sleep(0.05)` polling (20 Hz wakeups, and the same broad except hides real send errors as idle); per-client sequential `await drain(timeout=2.0)` means one slow client adds up to 2 s latency for every other client per packet. Remedy: executor-based blocking get or an asyncio queue bridge; per-client send queues. Effort: M.

### DI-25 — Planner can block the event loop with synchronous urllib
- **Where:** `smo-planner/server.py:240-247` (`urllib.request.urlopen`, 1.5 s timeout) called via `_refresh_sim_anchor` from `_get_now()` (`server.py:286-289`) **directly in request handlers**; the background loop wraps it in `asyncio.to_thread` (`server.py:149`) but the in-handler fallback path does not. Normally masked because the 1.5 s background refresh keeps the anchor fresh; bites when the background task dies or is slow. Remedy: make `_get_now` purely read cached anchor; refresh only in the background task. Effort: S.

### DI-26 — Planner `start()` never returns and hides the recompute schedule
- **Where:** `smo-planner/server.py:227-231` — after `site.start()`, an infinite `while True: sleep(600); _compute_contacts()` loop lives inside `start()`. Contacts recompute at fixed 10 wall-clock minutes regardless of sim speed (at 10× that is ~1.7 sim-hours stale). Remedy: separate task with sim-speed-aware interval. Effort: S.

### DI-27 — Vendor path discovery by 10-level parent walk
- **Where:** `smo-planner/server.py:210-222` — walks up to 10 parents looking for `vendor/` to work around a broken absolute symlink (`fix_vendor_symlinks.sh` exists at repo root for the same problem). Remedy: package the vendored JS as package data. Effort: S.

### DI-28 — tools/tests wired via `sys.path` hacks instead of the installed packages
- **Where:** `tools/tm_report.py:36`, `tools/delayed_tm_viewer.py:33-34`, `tests/conftest.py:12-13`, `tests/test_acceptance/test_ground_tools.py:31-39` — hard-coded relative `sys.path.insert` to `packages/*/src` and `tools/`. Fragile to layout changes and lets tests pass against different code than production imports. Remedy: editable installs + console-script entry points. Effort: S.

### DI-29 — Repo hygiene: build/stage artifacts, live databases and junk committed in-tree
- **Where:** repo root `_smo_part_aa/ab/ac`, `_smo_stage.tar.gz`, `_stage_probe.txt`, `venv-rfsim.tar.gz`, five screenshots, multiple `.DS_Store`; `configs/data/tm_archive.db` + `-wal`/`-shm` and `.fuse_hidden*` files (a *runtime* SQLite archive living inside the config tree, and its WAL implies unclean shutdown). Remedy: gitignore + relocate runtime data dir out of `configs/`. Effort: S.

### DI-30 — Orbit described three ways in config (TLE vs scalar fields vs mission.yaml block)
- **Where:** `configs/eosat1/orbit.yaml` (TLE **plus** `altitude_km: 450.0`, `inclination_deg: 98.0`) and `configs/eosat1/mission.yaml` `orbit:` block (450.0 / 98.0 again). Propagation uses only the TLE; the scalars are display/doc fodder that can drift from it (they already disagree with the TLE's mean motion in the third decimal of altitude). Remedy: derive scalars from the TLE at load, or mark them informational. Effort: S.

### DI-31 — SpaceCDF: position-ownership authz fails closed while claiming to be disabled, after fragile path discovery
- **Where:** `spacecdf-server/services/session_manager.py:66-88` (walks parent dirs hunting `packages/spacecdf-kb/.../positions.yaml`; on miss logs "edit scoping disabled") but `can_edit` (`session_manager.py:90-104`) then returns `False` for every position except the hard-coded `"systems_engineer"` — i.e. scoping is not disabled, everyone else is locked out, contradicting the log. Also sessions live only in `self._sessions` dict with fire-and-forget persistence (`session_manager.py:119-137`) — restart loses live sessions. Remedy: package the YAML as package data; make the miss behaviour match the message (and decide fail-open vs fail-closed explicitly). Effort: S.

### DI-32 — SpaceCDF: two parallel DOCX generation stacks
- **Where:** `spacecdf-server/services/docx_generator.py` (1414 lines, ECSS docs, itself containing "legacy per-type generators" plus a universal DID renderer per its docstring) vs `spacecdf-agents/exporters/docs/docx_generator.py` (910 lines, design-review docs) + `theme.py`/`figures.py`, plus inline docx assembly in `routers/exports.py` (DI-18). Three places build Word documents with overlapping section logic. Remedy: converge on the DID-driven renderer; delete the legacy per-type paths. Effort: M.

---

## Cross-cutting observations (not scored)
- **Positive:** `smo_common` config loading is pydantic-validated (schemas.py) — the simulator/planner side of config is in better shape than the MCS/rfsim side; `tests/` is extensive (60+ files); the "unknown instructor command → loud log" fix (engine.py:1869-1874) shows the silent-drop lesson was learned at that one seam. SpaceCDF's `db/write_queue.py` + per-session `asyncio.Lock` (session_manager.py:56-64) is a sound concurrency pattern.
- The repo's own `DEFECTS.md` lists #16–22, #32, #33 as still open; DI-09 (PUS unification) and DI-10 (`files/` removal) are the structural fixes that would retire several of them at once.
- All 2026-05/06 remediation is described as **uncommitted working-tree edits** (DEFECTS.md status note) — from a design-process standpoint, getting these committed is the highest-leverage single action.

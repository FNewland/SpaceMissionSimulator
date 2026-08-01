# Merge Assessment — Daksh's fork (`SpaceMissionSimulator`) into Franz's main (`SpaceMissionSimulation`)

Date: 2026-07-28
MAIN = `/sessions/rcw-01nwiaqbyeua69bcwofgtysz/mnt/SpaceMissionSimulation` (HEAD `4165db4`, 67 commits)
FORK = `/sessions/rcw-01nwiaqbyeua69bcwofgtysz/mnt/SpaceMissionSimulator` (HEAD `687a8a1`, 59 commits, last real work 2026-06-04)

---

## 0. Headline

**The fork is an ancestor snapshot of main, not a divergent branch.** The two repos have
unrelated git object graphs (re-initialised histories — `git cat-file` on the fork's HEAD
fails in main), but the *commit-message sequence is identical* for the fork's entire
59-commit history, and main carries 8 further commits on top. Every one of the fork's
"unique-sounding" commits already exists in main:

| Fork commit | Present in main as |
|---|---|
| `b87ef0e` Fix PLL convergence + RF diagnostic instrumentation | `ad41e61` (same message) |
| `4fa7878` Fix TC uplink reliability, relay loop stall, carrier auto-search | `75f2e6d` (same message) |
| `940762b` Fix bridge TX model: continuous carrier when link in view | `6212df1` |
| `fd929fd` Reduce beacon interval 30s→10s | `728e1ac` |
| `687a8a1` Remediate defects 09-33 | `aed2694` |

**The RF fixes Franz was worried about are already in main.** Main's `smo-rfsim` is
*strictly ahead*: it additionally has defect-43's TM-frame FHP-desync fix
(`packages/smo-rfsim/src/smo_rfsim/ccsds/tm_frame.py`, +27 lines over the fork) and an
entire new `smo_rfsim/recording/` package (`iq_recorder.py`, `cli.py`, console script
`smo-rfsim-record`) that the fork does not have.

Quantitatively: of the 86 files that differ, main has more content in **84**. The only
two exceptions are one no-op SQLite WAL/SHM binary pair and
`configs/eosat1/scenarios/scenario_2_cerise_adcs_fault.yaml` — where the fork's version is
longer but **wrong** (see §4).

**Net: there are exactly two things in the fork worth taking, and one of them is a
three-line change to `start.sh`.**

---

## 1. Summary table (area → only-fork / only-main / differing / verdict)

Exclusions applied: `.git .venv .venv-rfsim __pycache__ *.db *.pyc *.tar.gz workspace sim_captures .pytest_cache .DS_Store`.

| Area | only-FORK | only-MAIN | differing | Verdict |
|---|---|---|---|---|
| `tools/` | **1** (`adcs_sim_viewer.py`) | 1 (`pass_gap_analysis.py`) | 2 | **TAKE the 1 fork file** (the failure scenario view). Others: main ahead. |
| `configs/eosat1/scenarios/` | **4** (`test.yaml`, `test1.yaml`, `beta_test.yaml`, `testingbeta.yaml`) | 0 | 4 | Fork-only 4 = scratch UI output → **OBSOLETE**. Differing 4 → **DO-NOT-TAKE**. |
| `packages/smo-simulator` | 0 | 1 (`obcp.py`) | 14 | Main ahead on all 14 (engine +260, scenario_engine +300, service_dispatch +244, aocs +245). **DO-NOT-TAKE.** |
| `packages/smo-mcs` | 0 | 0 | 6 | Main ahead (server.py +438). **DO-NOT-TAKE.** |
| `packages/smo-planner` | 0 | 0 | 5 | Main ahead (server.py +220). **DO-NOT-TAKE.** |
| `packages/smo-rfsim` | 0 | 1 (`recording/`) | 2 | Main ahead. **DO-NOT-TAKE.** |
| `packages/smo-common` | 0 | 0 | 3 | Main ahead (schemas.py +156). **DO-NOT-TAKE.** |
| `packages/smo-gateway` | 0 | 0 | 0 | Identical. |
| `configs/eosat1/procedures/` | 0 | 0 | 22 | Wording-only; main's is the reconciled text. **DO-NOT-TAKE.** |
| `configs/eosat1/*` (non-data) | 0 | 3 (`simulation.yaml`, `obcp/`, `manual/12_time_and_clocks.md`) | 11 | Main ahead. **DO-NOT-TAKE.** |
| `tests/` | 0 | 35 | 10 | Main ahead everywhere. **DO-NOT-TAKE.** |
| `defects/` | 0 | 19 (defects 34-52) | 0 | Main-only. |
| `docs/` | 0 | 3 (`OBCP_GUIDE.md`, `SIMULATION_CONFIG.md`, `review_2026-07/`) | 0 | `docs/gap_analysis/` incl. `fdir_ui_audit.md` is **byte-identical** in both. |
| `scripts/`, root, `deploy/`, `files/`, `vendor/` | 0 | 10 | 6 | Main ahead. `start.sh` → **partial take** (see §3). |
| **TOTAL** | **5** | **81** | **86** | |

Note on `docs/gap_analysis/fdir_ui_audit.md`: identical in both trees — it is not a source
of fork-only capability.

---

## 2. THE FAILURE SCENARIO VIEW — what it actually is

### 2.1 Identification

It is **`tools/adcs_sim_viewer.py`** (833 lines, fork-only, aiohttp, port **8097**), titled
*"ADCS Simulation Viewer"* in the UI header ("Mission Q · ADCS Simulation Viewer — :8097").

It is **not** part of the simulator/MCS/instructor runtime. It is a **standalone,
offline, parametric failure-scenario explorer** — a self-contained 6-DOF CubeSat ADCS
physics simulation with a browser front-end for the **CERISE-style debris-strike
scenario**.

Critically: it is a **web wrapper around a script Franz already owns**. Main has
`ADCS_failure_sim/cubesat_adcs_sim.py` (34 KB, last touched 2026-07-24) plus
`ADCS_failure_sim/ADCS_simulation_writeup.md`, which is the same simulation as a CLI
script that writes `cubesat_adcs_telemetry.csv` and `plot_1..4_*.png` to disk. Daksh
inlined that physics into an aiohttp server and put a form + tabs around it.

### 2.2 What it does — verified from the code

**Physics core** (`run_sim(p)`, lines 177-358) — identical model and constants to main's
`cubesat_adcs_sim.py`:
- Kepler orbit + J2, IGRF dipole geomagnetic field, analytic Sun vector, cylindrical eclipse.
- Sensors: magnetometer (250 nT white noise + fixed bias), coarse Sun sensor (0.5° noise).
- Estimator: TRIAD (B-field + Sun vector) with Sun-validity/eclipse gating.
- Control: quaternion PD (`KP=[0.012,0.012,0.004]`, `KD=[0.045,0.045,0.015]`), reaction
  wheels (1 mN·m torque, 30 mN·m·s momentum saturation), magnetorquer cross-product
  momentum dumping (`MTQ_MAX_DIPOLE=0.2 A·m²`).
- Inertia `diag(0.0419, 0.0419, 0.0067)`; safe-mode rate trip at 8 °/s.

**The failure scenario** — a three-phase debris-strike storyline with a mode state machine
`NORMAL → SAFE → RECOVERY → SAFE`:
1. `t = T_IMPACT`: debris strike applies a body-rate kick `Δω = [dw_x, dw_y, dw_z]`,
   forces `SAFE`, and **simultaneously writes a latent control-gain corruption**
   `gain_corrupt[CORRUPTED_AXIS] = CORRUPTION_FACTOR` (default 10×). Invisible while
   control is inhibited.
2. Tumble dwell in `SAFE` — no actuators, attitude error grows.
3. `t = T_RECOVERY`: ground re-enables control → the corrupted axis is allowed to command
   `CORRUPTION_FACTOR × RW_MAX_TORQUE` → the wheel ramps to momentum saturation → an
   **automatic safe-mode re-trip** (`mode == "RECOVERY" and wheel saturated → SAFE`).

**What the fork adds over main's CLI script** — this is the whole value:
- **Every scenario knob is a form field instead of a source-code constant.** Sidebar
  groups: *Orbit* (`alt_km`, `inc_deg`, `ecc`, `raan_deg`, `argp_deg`, `nu_deg`, `epoch`
  as `datetime-local`), *Timing* (`t_total`, `t_impact`, `t_recovery`), *Debris Strike*
  (`corrupted_axis` dropdown X/Y/Z → RW-1/2/3, `corruption_factor`, `dw_x/y/z`).
  In main these are hard-coded module constants (`T_IMPACT = 3000.0`,
  `T_RECOVERY = 5180.0`, `CORRUPTED_AXIS = 0`, `CORRUPTION_FACTOR = 10.0`) in a
  "USER CONFIGURATION BLOCK" you must edit and re-run.
- **A structured `events` list** — `run_sim` returns `(log, events)` where events are
  `{'t', 'type': 'impact'|'recovery'|'autosafe', 'msg'}`. Main only prints these to
  stdout. The UI renders them as a colour-coded timeline (amber/green/red left border).
- **A stats row** (`compute_stats`): `max_att_err`, `max_tumble`, `max_h_pct`
  (% of momentum saturation), and minutes spent in `NORMAL` / `SAFE` / `RECOVERY`.
  Main has no equivalent.
- **Inline plots** — `make_plots` renders the same four figures via matplotlib `Agg` and
  returns them base64-encoded (`_b64`), displayed in a 4-tab strip
  (Sensors / Estimator / Actuators / Event Zoom) with a phase-shading legend.
- **CSV download** of the run's telemetry.

**Wiring:**
- Routes (line 825-827): `GET /` → `handle_index` (serves the inline `HTML` constant);
  `POST /run` → `handle_run`; `GET /download` → `handle_download`.
- `handle_run` reads the JSON parameter dict from the browser, runs `run_sim` and
  `make_plots` on a **single-worker `ThreadPoolExecutor`** (so the event loop is not
  blocked), returns `{plots, stats, events}`.
- **Data source: none.** It does not read the simulator, MCS, TM archive, or any config.
  It is fully self-contained — the physics runs inside the request. Stated run time
  4-8 s for a 3-hour scenario.
- **State: a module-level `_last_csv` global** that `/download` serves — i.e. the last run
  by *any* client. Single-user by design.
- Launched by the fork's `start.sh` as `$PYTHON tools/adcs_sim_viewer.py --port 8097 &`
  and advertised in the banner as `ADCS Sim Viewer: http://localhost:8097`.
- Dependencies: `numpy`, `matplotlib`, `aiohttp` — all already in the venv.

### 2.3 What it is NOT

- It is **not** a view onto the live simulation. Nothing connects it to `smo-simulator`.
- It is **not** the CERISE scenario that runs in the trainer
  (`configs/eosat1/scenarios/scenario_2_cerise_adcs_fault.yaml`) — that is a separate,
  unrelated artefact, and the fork's copy of it is regressive (§4).
- It does **not** replace the instructor failure-injection panel. Main's instructor UI is
  ahead of the fork's in every respect.
- `docs/EOSAT1_Failure_Modes_Catalog.html`/`.pdf` exist and are **byte-identical** in both
  trees — no fork-only failure catalogue viewer.

### 2.4 Integration proposal (recommended: option B)

**Option A — take it as-is (fastest, ~15 min).**
Copy `tools/adcs_sim_viewer.py` into main's `tools/`, add the launcher lines to main's
`start.sh`. Cost: the ADCS physics now exists twice in the repo
(`ADCS_failure_sim/cubesat_adcs_sim.py` and `tools/adcs_sim_viewer.py`), which will
drift. Also silently loses three telemetry channels main logs (`utc`, `triad_used`,
`est_err_deg`) and main's `wheel_rpm` derivation.

**Option B — port the UI, keep main's physics as the single source of truth (recommended,
~1-2 h).** Main's `cubesat_adcs_sim.py` is the *newer* file (2026-07-24, vs the fork's
2026-06 snapshot) and logs a **superset** of channels. Do this:
1. Refactor `ADCS_failure_sim/cubesat_adcs_sim.py`: lift the `USER CONFIGURATION BLOCK`
   constants into a `run(params: dict)` signature with the current values as defaults, and
   make `run()` return `(log, events)` — appending the same three structured event dicts
   (`impact`, `recovery`, `autosafe`) that it currently only prints. Keep the existing
   `__main__` CLI path working off the same defaults so nothing that uses it breaks.
2. Add `tools/adcs_sim_viewer.py` to main as a **thin** server: keep Daksh's `HTML`
   constant, `make_plots` → base64 (`_b64`), `compute_stats`, `make_csv`, the three
   route handlers and the `ThreadPoolExecutor` offload — but `from ADCS_failure_sim.cubesat_adcs_sim import run`
   instead of carrying a second copy of the physics. Map the form field names
   (`alt_km`, `inc_deg`, `t_impact`, `corrupted_axis`, …) onto main's parameter names.
3. Adapt `compute_stats`/`make_plots` to main's log key names
   (`tumble_rate_dps` not `tumble_dps`, `h_wheel_x` not `h_x`, `rate_x_dps` not `rate_x`).
   Add `est_err_deg` (estimator error) to the Estimator tab — main has it, the fork's UI
   does not plot it.
4. Replace the `_last_csv` module global with a small `dict` keyed by a run id returned
   from `/run`, so two browser tabs don't hand each other the wrong CSV.
5. Register in `start.sh` (see §3, item 2) and add a line to `docs/INSTRUCTOR_GUIDE.md`.

**Do not** try to wire this into the live simulator or the instructor UI in this pass.
It is an offline analysis/teaching tool; that is its value, and main has no equivalent
browser-based one.

---

## 3. WORTH MERGING

Only two items. Both are in `tools/` / `start.sh`.

| # | Item | Why |
|---|---|---|
| **1** | `tools/adcs_sim_viewer.py` — the ADCS failure-scenario view | The one genuine fork-only capability. Main has the physics (`ADCS_failure_sim/cubesat_adcs_sim.py`) but only as an edit-the-constants CLI script. This makes the debris-strike scenario interactively explorable in a browser: parameterised orbit/timing/fault, event timeline, stats row, tabbed plots, CSV export. Integrate per §2.4 option B. |
| **2** | `start.sh` — the two tool-launch blocks (fork lines ~138-147 + banner lines) | Main's `start.sh` **does not launch `tools/scenario_generator.py` on :8096 at all**, even though main *has* the file and main's copy is the newer one (+24 lines: `gain_corruption` failure type, the whole `payload` failure family, `axis` extras). So main currently ships a scenario generator nobody can reach from `./start.sh`. Take the fork's two `echo`+launch blocks and the two banner lines — **but rewrite them in main's idiom** (`python` from the activated venv, not the fork's `$PYTHON`, and honour main's `${PUBLIC:+--public}` convention). |

Everything else in the fork is either identical, older, or scratch.

Borderline / judgement calls, listed for completeness:

- `configs/eosat1/scenarios/{test,test1,beta_test,testingbeta}.yaml` — **OBSOLETE.**
  Read them: `name: "Test"`, `briefing: |\n  tetst`, `# test`. These are throwaway outputs
  from Daksh exercising the scenario-generator UI. Do not merge; they would pollute the
  scenario list in the instructor UI.
- `tools/adcs_sim_viewer.py`'s embedded physics — **SUPERSEDED** by main's
  `cubesat_adcs_sim.py` (newer, logs `utc`/`triad_used`/`est_err_deg` in addition).
  Take the UI, not the physics. This is the single most important nuance of the merge.

---

## 4. DO-NOT-TAKE (would regress main)

Everything below is an **older revision** of a file main has since fixed. A `git merge`,
`cp -r`, or "resolve in favour of theirs" on any of these silently reverts landed defect work.

### 4.1 Highest risk — large regressions

| Fork file | Reverts |
|---|---|
| `packages/smo-simulator/src/smo_simulator/scenario_engine.py` | main +300/−11. Loses **defect 34** (scenario auto-stop on `duration_s`), the debrief/scoring object (`last_debrief`, `is_active`, `current_name`, MTTD/MTTI/MTTR, `score_pct`) and **defect 52** (scenario-schema silent-ignore detection). Fork is 244 lines vs main's 533. |
| `packages/smo-simulator/src/smo_simulator/engine.py` | main +260/−31. |
| `packages/smo-mcs/src/smo_mcs/server.py` | main +438/−48. Loses **defect 46** (S11 builder), **defect 47** (contact display schema), `--time-source`/`sim_state_url` plumbing. |
| `packages/smo-simulator/src/smo_simulator/service_dispatch.py` | main +244/−13. Loses **defect 38** (legacy funcIDs 100-107), **defect 39** (S8 func49 thermal map), PUS **S18 OBCP** dispatch (defect 48). |
| `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py` | main +245/−30. Loses the ADCS torque-gain work (`tests/test_adcs_torque_gain.py`). |
| `packages/smo-planner/src/smo_planner/server.py` | main +220/−16. Loses **defect 44** (upload discards start time), **defect 45** (activity funcID misroute), time-source. |
| `packages/smo-common/src/smo_common/config/schemas.py` | main +156/−1. Loses the `simulation.yaml` schema (**defect 51**) and `time_source` mission field. |
| `packages/smo-simulator/src/smo_simulator/models/eps_basic.py` | main +152/−31. Loses **defect 49** (SoC-shed dead code) and **defect 50** (power budget closure). |
| `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py` | main +122/−12. Loses the TTC uplink/downlink decouple (`f6783a3`). |
| `packages/smo-common/src/smo_common/orbit/propagator.py` | main +117/−30. Loses **defect 41** multi-station contact support. |

### 4.2 RF — explicitly checked, fork is behind

- `packages/smo-rfsim/src/smo_rfsim/ccsds/tm_frame.py` — fork's `flush()` hard-codes
  `fhp = 0` and never clears stale packet-start offsets. Main's version (**defect 43**)
  computes the true first-header-pointer from recorded offsets and clears them; its
  docstring names this as the root cause of intermittent acceptance-test ACK loss.
  Taking the fork's file reintroduces the TM-frame desync bug.
- `packages/smo-rfsim/pyproject.toml` — fork lacks the `smo-rfsim-record` console script.
- `packages/smo-rfsim/src/smo_rfsim/recording/` — **absent from the fork entirely**
  (`iq_recorder.py`, `cli.py`). Do not let a directory-level sync delete it.
- The two RF commits Franz flagged (`Fix PLL convergence…`, `Fix TC uplink reliability…`)
  are **already in main** as `ad41e61` and `75f2e6d`. Nothing to cherry-pick.

### 4.3 UI — fork is behind on all three

- `packages/smo-simulator/src/smo_simulator/instructor/static/index.html` — fork has the
  **wrong `sc_mode` map** (`{0:NOMINAL, 1:SAFE, 2:EMERGENCY}`); main's is the authoritative
  `{0:SAFE, 1:NOMINAL, 2:SCIENCE, 3:EMERGENCY}` matching the engine `SC_*` constants
  (commit `4165db4`). Taking the fork's file **mislabels every spacecraft mode in the
  instructor UI**. Fork also has the un-fixed override-passes toggle (reads the CSS
  `active` class, which races with the poller, so repeated clicks never toggle off) and
  drops the ground-station name from the CONTACT badge.
- `packages/smo-simulator/src/smo_simulator/instructor/app.py` — fork lacks the
  `GET /api/scenario/debrief` route and `handle_scenario_debrief`. Taking it **breaks the
  scenario debrief panel**.
- `packages/smo-mcs/.../static/index.html` and `packages/smo-planner/.../static/index.{html,index-wide.html}` —
  the fork's apparent "extra" JS (`_simClockMs`, `_simClockWall`, `connectSimWS`,
  `fetchSimSpeed`, polling `http://localhost:8080/api/state`) is a **client-side sim-clock
  hack that main deliberately replaced** with the server-side `/api/time-source` mechanism
  (`--time-source` flag, `SMO_TIME_SOURCE` env, mission-config field; covered by
  `tests/test_mcs/test_time_source.py` and `tests/test_planner/test_time_source.py`).
  Do not reinstate it — you would end up with two competing clocks.

### 4.4 Configs

- `configs/eosat1/scenarios/scenario_2_cerise_adcs_fault.yaml` — **the trap.** The fork's
  is 249 lines vs main's 175, so a size-based heuristic picks the wrong one. Main's header
  says it was *"Reworked 2026-07 to drive the fault through the real PUS S6 torque-gain
  feature and the model's actual attitude dynamics (verified in-model)"*. The fork's
  version drives the fault with `set_parameter` puppeting of `0x0204/0x0205/0x0206`
  (rate_roll/pitch/yaw), `0x0107` (power_gen) and `0x0101` (bat_soc) — main's comments
  state explicitly that a `set_parameter` puppet **"the physics model overwrites on the
  next tick"**. The fork also asserts `RW_MAX_MOMENTUM saturation ≈ 6000 RPM`, which main
  documents as non-existent (RW max is 5,500 RPM per `aocs.yaml`, and the AOCS model does
  not integrate control effort into wheel speed at all). The fork's scenario is
  aspirational and does not work in this simulator. **DO-NOT-TAKE.**
- `configs/eosat1/procedures/**` (22 files) — differences are wording only, and the fork
  has the *pre-reconciliation* text: `"Initial Health Assessment"` vs main's
  `"LEOP-002 (Initial Health Check)"`, and `"Bidirectional VHF/UHF link"` vs main's
  `"Bidirectional S-band link"`. Main's is correct per `SPACECDF_RECONCILE.md` (main +14).
- `configs/eosat1/commands/tc_catalog.yaml` (main +100) — fork lacks the **defect 40**
  boolean-name-field fix, which aborts catalog load, and the OBCP/S18 command set.
- `configs/eosat1/monitoring/s19_rules.yaml` (main +54) — fork lacks **defect 37**
  (misrouted funcIDs / autoboot OBC).
- `configs/eosat1/planning/ground_stations.yaml`, `mcs/positions.yaml` — fork lacks
  **defects 41/42** (multi-station contacts, config de-duplication).
- Anything under `configs/data/` — 203 MB of generated `tm_archive.db*` runtime data.
  **Ignore entirely; it is already gitignored in main.**

### 4.5 Tests

All 35 main-only test files would be lost by a directory-level replace, including
`test_multi_station_contacts.py`, `test_obcp_s18.py`, `test_power_budget_regression.py`,
`test_integration/test_planner_mcs_s11_chain.py`, `test_simulation_config.py`,
`test_scenario_autostop.py`, `test_sc_mode_convention.py`, `test_rfsim/test_iq_recorder.py`,
and the whole `tests/test_content/` tree. The 10 differing test files are all older in the
fork (e.g. `test_rfsim/test_tm_frame.py` main +72/−0). **DO-NOT-TAKE any of `tests/`.**

---

## 5. Merge plan

**Governing principle: this is not a merge, it is a two-file cherry-pick.** Do not use
`git merge`, `git subtree`, `rsync`, or `cp -r` at directory level. The histories are
unrelated, so any merge would present ~86 conflicts in which the fork's side is the older
revision — exactly the shape of mistake that silently reverts defects 34-52.

### Step 0 — safety net
```
cd <main>
git checkout -b merge/daksh-adcs-viewer
git status --porcelain      # confirm clean; commit or stash first
```
Record the baseline: `pytest -q` (note pass/fail counts before touching anything).

### Step 1 — take the failure scenario view (the only real deliverable)
1. `cp <fork>/tools/adcs_sim_viewer.py <main>/tools/adcs_sim_viewer.py`
2. **Port by hand, do not keep the copy of the physics.** Per §2.4 option B:
   - In `ADCS_failure_sim/cubesat_adcs_sim.py`, convert the `USER CONFIGURATION BLOCK`
     into `def run(params: dict = None)` with today's constants as defaults; have `run()`
     return `(log, events)` and append `{'t','type','msg'}` dicts at the three points where
     it currently only prints (impact, recovery, auto-safe re-trip). Keep `__main__` working.
   - In `tools/adcs_sim_viewer.py`, delete lines ~30-360 (constants, quaternion utils,
     `Orbit`, `run_sim`) and import main's `run` instead.
   - Rename log keys used by `make_plots`/`compute_stats` to main's:
     `tumble_dps`→`tumble_rate_dps`, `rate_x`→`rate_x_dps`, `h_x`→`h_wheel_x`,
     `rpm_x`→`wheel_rpm_x`, `tau_wx`→`tau_wheel_x`, `tau_cx`→`tau_cmd_x`.
   - Add `est_err_deg` to the Estimator tab.
   - Replace the `_last_csv` global with `{run_id: csv}`; return `run_id` from `/run` and
     accept `?id=` on `/download`.
3. Commit: *"Add ADCS failure-scenario viewer (:8097), backed by ADCS_failure_sim"*.

### Step 2 — wire up both offline tools in `start.sh`
Add to main's `start.sh`, immediately after the doc-viewer block, **in main's idiom**
(main uses the activated venv's `python`, not the fork's `$PYTHON`):
```
# Start Scenario Generator (HTTP:8096)
echo "==> Starting Scenario Generator on port 8096..."
python "$SCRIPT_DIR/tools/scenario_generator.py" --port 8096 &

# Start ADCS Failure-Scenario Viewer (HTTP:8097)
echo "==> Starting ADCS Sim Viewer on port 8097..."
python "$SCRIPT_DIR/tools/adcs_sim_viewer.py" --port 8097 &
```
and two banner lines next to `Documentation:  http://localhost:8095`.
Do **not** take any other line of the fork's `start.sh` — it lacks `--public`, `--real`,
`SMO_TIME_SOURCE`, `SMO_DUMP_DIR`, the `--time-source` arguments passed to `smo-mcs`/
`smo-planner`, and the `.venv-rfsim` runnable-interpreter probe.
Commit separately so it can be reverted independently.

### Step 3 — explicitly decline everything else
Nothing else is merged. For the record, mark as reviewed-and-rejected:
`packages/**` (all 30 differing files), `tests/**`, `configs/eosat1/**`,
`configs/eosat1/scenarios/{test,test1,beta_test,testingbeta}.yaml`, the fork's `start.sh`,
`scripts/upload_and_file_defects.sh`, `.gitignore`, `DEFECTS.md`, `SPACECDF_RECONCILE.md`,
`.claude/settings.local.json`.

### Step 4 — verify
```
pytest -q                                   # must match the Step-0 baseline exactly
pytest tests/test_rfsim/ -q                 # FHP/defect-43 still fixed
pytest tests/test_simulator/test_obcp_s18.py tests/test_simulator/test_multi_station_contacts.py \
       tests/test_simulator/test_power_budget_regression.py tests/test_simulator/test_sc_mode_convention.py -q
pytest tests/test_integration/test_planner_mcs_s11_chain.py -q
pytest tests/test_mcs/test_time_source.py tests/test_planner/test_time_source.py -q
```
Then `./start.sh` and check by hand:
- `http://localhost:8097` — set `t_impact`/`t_recovery`/`corrupted_axis`/`corruption_factor`,
  Run Simulation; confirm all four plot tabs render, the three events appear in the
  timeline, the stats row is populated, and the CSV downloads with main's 38 channels
  (including `utc`, `triad_used`, `est_err_deg`).
- `http://localhost:8096` — scenario generator reachable, and the failure list includes
  `gain_corruption` and the `payload` family (proves main's newer file is being served).
- `http://localhost:8080` instructor UI — spacecraft mode badge still shows
  SAFE/NOMINAL/SCIENCE/EMERGENCY correctly, override-passes button toggles both ways,
  scenario debrief still appears at scenario end.
- `python ADCS_failure_sim/cubesat_adcs_sim.py` — the CLI path still writes its four PNGs
  and CSV (proves the Step-1 refactor didn't break the original entry point).

### Step 5 — housekeeping (optional, not part of the merge)
Main's `configs/data/` contains eight `.fuse_hidden*` stragglers and a large
`tm_archive.db*` set; `_to_delete/`, `r24.txt`, `r27.txt`, `.__write_test.txt` are also
loose in the root. Unrelated to the fork, but worth a separate cleanup commit.

---

## 6. One-paragraph answer for Franz

Daksh's tree is a June snapshot of your own repo — same commit sequence, eight commits
behind, and your tree is ahead on 84 of the 86 files that differ. The RF fixes you were
worried about (PLL convergence, TC uplink reliability, carrier auto-search) are already in
your history; his `rfsim` is actually *behind* yours by the defect-43 FHP fix and the whole
`recording/` package. The failure scenario view is a single fork-only file,
`tools/adcs_sim_viewer.py`: a browser dashboard on :8097 that wraps the debris-strike ADCS
scenario you already have as `ADCS_failure_sim/cubesat_adcs_sim.py`, turning its hard-coded
constants into form fields and adding an event timeline, a stats row, tabbed plots and CSV
export. Take that (re-pointed at your newer physics module) plus three launcher lines in
`start.sh` — which incidentally also fixes the fact that your `start.sh` never starts the
scenario generator on :8096 — and take nothing else.

# Defect 62 — ADCS failure-scenario viewer existed only in the fork (with a duplicated, older physics copy), and the scenario generator was never launched

## Summary

Two offline tools that the suite is supposed to offer were unreachable from a
normal `./start.sh` run:

1. **The ADCS failure-scenario viewer did not exist in this repo at all.** The
   only copy lived in Daksh's fork (`SpaceMissionSimulator`,
   `tools/adcs_sim_viewer.py`, 833 lines, aiohttp on :8097) — a browser dashboard
   around the CERISE-style debris-strike ADCS scenario. This repo had the physics
   (`ADCS_failure_sim/cubesat_adcs_sim.py`) but only as an *edit-the-constants*
   CLI script: `T_IMPACT`, `T_RECOVERY`, `CORRUPTED_AXIS`, `CORRUPTION_FACTOR`,
   the orbit and the epoch were module-level literals in a "USER CONFIGURATION
   BLOCK" that had to be edited and re-run for every what-if. The three storyline
   events (impact / recovery / automatic safe-mode re-trip) were `print()`
   statements, so nothing downstream could consume them.

2. **The fork's copy of the viewer carried its own inlined copy of the ADCS
   physics**, snapshotted in June 2026. This repo's `cubesat_adcs_sim.py`
   (2026-07-24) is newer and logs a **superset** of channels: `utc`,
   `triad_used`, `est_err_deg` and the `wheel_rpm_*` derivation are all absent
   from the fork's inlined version, which also used different channel names
   (`tumble_dps`, `h_x`, `tau_wx`, `rate_x`). Copying the fork's file in as-is
   would have created a second, older ADCS model in the tree, guaranteed to drift
   from the module the writeup documents.

3. **`tools/scenario_generator.py` was never started by `start.sh`.** The file is
   present and is the newer revision (it has the `gain_corruption` failure type
   and the whole `payload` failure family), and `tests/test_scenario_generator_sync.py`
   exercises it, but no launcher line existed — so the instructor-facing scenario
   authoring UI on :8096 shipped unreachable.

## Severity

**Minor / capability gap** — nothing that runs was wrong, but a documented
capability (interactive exploration of the CERISE debris-strike failure) was
missing, and a shipped tool (:8096) could not be reached from the standard
start-up path.

## Status

**Fixed.**

1. **`tools/adcs_sim_viewer.py` ported into this repo, importing the shared
   physics.** The UI (dark dashboard, sidebar form, tabbed base64 plots, event
   timeline, stats row, CSV download, single-worker `ThreadPoolExecutor` offload)
   is carried over; the fork's embedded `run_sim` / quaternion utilities / `Orbit`
   class are **not**. `run_scenario()` calls
   `ADCS_failure_sim.cubesat_adcs_sim.run(params, events)`, so there is exactly
   one ADCS model in the repo. A regression test asserts the viewer module has no
   `run_sim` attribute and that it imported the module from `ADCS_failure_sim/`.

2. **Small, backward-compatible API addition to `ADCS_failure_sim/cubesat_adcs_sim.py`:**
   `default_params()`, `PARAM_LIMITS` and `resolve_params()` expose the USER
   CONFIGURATION BLOCK programmatically, and `run(params=None, events=None)`
   accepts overrides and appends `{'t', 'type', 'msg'}` event dicts (`impact`,
   `recovery`, `autosafe`) at the three points that previously only printed.
   Every default is the previous constant, `run()` still returns `(log, orbit)`,
   and the `__main__` CLI path (four PNGs + CSV) is unchanged. The `matplotlib`
   import is now guarded so the physics can be imported on a plotting-free
   install; `make_plots()` raises a clear `RuntimeError` instead.

3. **Adapted to this repo's superset of channels.** Plots and stats use main's
   names (`tumble_rate_dps`, `h_wheel_*`, `wheel_rpm_*`, `tau_wheel_*`,
   `tau_cmd_*`, `rate_*_dps`) and the extra channels are surfaced where they earn
   their place: `est_err_deg` alongside the pointing error on the Estimator tab,
   `triad_used` on the sensor flags panel, a new wheel-RPM panel on the Actuators
   tab, and `max_est_err` / `triad_pct` / `peak_wheel` / `max_rpm` in the stats
   row. The event-zoom torque panel follows the *selected* corrupted axis instead
   of hard-coding X.

4. **Robustness fixes over the fork's version.** The `_last_csv` module global
   (which served the last run by *any* client) is replaced by a bounded
   `{run_id: csv}` map with `GET /download?id=`; unknown parameters are rejected
   with HTTP 400 instead of being silently ignored; parameters are clamped;
   `t_recovery` is forced to lie between `t_impact` and `t_total`; the per-run
   noise seed is a form field, so runs are reproducible; and event/error text is
   HTML-escaped in the browser.

5. **`start.sh` now launches both tools**, in this file's idiom (venv `python`,
   `$SCRIPT_DIR` paths, `VAR_PID=$!`, `${PUBLIC:+...}` honoured, banner line, and
   the existing `cleanup()` trap kills them with the other jobs):
   Scenario Generator on **:8096** and the ADCS Failure-Scenario viewer on
   **:8097**, both started unconditionally like every other non-RF service.

6. **Documented** in `docs/ADCS_FAILURE_VIEWER.md` (what it is, how to start it,
   the full parameter table, what each plot shows, the CERISE lineage —
   `CERISE based error scenario.docx` in the repo root — and the fact that the
   physics is the shared `ADCS_failure_sim` module).

Nothing else from the fork was taken: its `packages/**`, `tests/**`,
`configs/**` and `start.sh` are all older revisions of files this repo has since
fixed (see the merge assessment; taking them would revert defects 34–52).

## Acceptance criteria

- [x] The viewer runs in this repo on :8097 and serves the dashboard, a run and a CSV.
- [x] It contains no copy of the ADCS physics; every run goes through
      `ADCS_failure_sim/cubesat_adcs_sim.py`.
- [x] The extra channels this repo logs (`est_err_deg`, `triad_used`,
      `wheel_rpm_*`) are visible in the UI, and the CSV has all 38 channels.
- [x] `python ADCS_failure_sim/cubesat_adcs_sim.py` still behaves exactly as before.
- [x] `./start.sh` starts and advertises both :8096 and :8097 and shuts them down
      with Ctrl+C.
- [x] A missing `matplotlib` produces a clear message, not a crash.
- [x] Covered by `tests/test_adcs_sim_viewer.py` (runs in ~3 s).

## Affected areas

- `tools/adcs_sim_viewer.py` (new)
- `ADCS_failure_sim/cubesat_adcs_sim.py` (`default_params`, `PARAM_LIMITS`,
  `resolve_params`, `run(params, events)`, guarded matplotlib import,
  `phase_shading`/`make_plots` accept the scenario times)
- `start.sh` (launch + banner for :8096 and :8097)
- `docs/ADCS_FAILURE_VIEWER.md` (new)
- `tests/test_adcs_sim_viewer.py` (new)

## Related

- `ADCS_failure_sim/ADCS_simulation_writeup.md` — the model and fault description.
- `CERISE based error scenario.docx`, `DEBRIS_IMPACT_SCENARIO_CRITIQUE.docx`,
  `Debris_Impact_Recovery_Procedures_Final_v2.docx` — the scenario's provenance.
- `configs/eosat1/scenarios/scenario_2_cerise_adcs_fault.yaml` — the *live*
  trainer scenario for the same failure; a separate artefact, deliberately
  untouched.
- Defect #51 (hard-coded simulation parameters) — the same "constants should be
  parameters" complaint, applied to the offline ADCS script.

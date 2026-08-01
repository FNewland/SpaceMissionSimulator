# ADCS Failure-Scenario Viewer (`tools/adcs_sim_viewer.py`, :8097)

A browser dashboard for the **CERISE-style debris-strike ADCS failure scenario**:
set the orbit, the event timing and the fault definition in a form, press *Run
Simulation*, and the diagnostic plots, a statistics row and a colour-coded event
timeline appear inline, with the run's telemetry available as a CSV download.

It is an **offline analysis / teaching tool**. It does not connect to the
simulator, the MCS, the planner or the TM archive — the physics runs inside the
request. Nothing about a running training session is affected by it.

## The physics is the shared module — not a copy

Every run calls `ADCS_failure_sim/cubesat_adcs_sim.py`, the same module the CLI
script uses, via `run(params, events)`. There is **no second copy of the ADCS
physics in this repo**, so the viewer and the offline script cannot drift apart,
and the viewer sees all 38 telemetry channels that module logs (including `utc`,
`triad_used`, `est_err_deg` and the `wheel_rpm_*` derivation).

The model itself — Kepler + J2 orbit, tilted-dipole IGRF field, analytic Sun
vector with cylindrical eclipse, magnetometer + coarse Sun sensor, TRIAD
predict/correct estimator, quaternion PD control on reaction wheels with
magnetorquer momentum dumping — is documented in
`ADCS_failure_sim/ADCS_simulation_writeup.md`.

### Lineage

The scenario is the CERISE case study: a debris strike that both kicks the body
rate and plants a *latent* control-gain corruption, so the failure only manifests
when the ground tries to recover. See **`CERISE based error scenario.docx`** in the
repository root, plus `DEBRIS_IMPACT_SCENARIO_CRITIQUE.docx` and
`Debris_Impact_Recovery_Procedures_Final_v2.docx`. The training scenario that
drives the *live* simulator through an equivalent fault is a separate artefact
(`configs/eosat1/scenarios/scenario_2_cerise_adcs_fault.yaml`); this viewer is the
standalone physics explorer, not that scenario.

## Starting it

`./start.sh` starts it with the rest of the suite:

```
ADCS Failure Sim: http://localhost:8097
```

Standalone:

```bash
python tools/adcs_sim_viewer.py --port 8097 [--host 127.0.0.1] [--public]
```

`--public` (or `SMO_PUBLIC=1`, or `./start.sh --public`) binds `0.0.0.0` so the
viewer is reachable from the LAN. Dependencies: `aiohttp`, `numpy` (required) and
`matplotlib` (needed for the plots — if it is absent the server still starts, the
*Run* button is disabled and both the page and `POST /run` say so explicitly
rather than crashing).

## Parameters

Defaults are served from `cubesat_adcs_sim.default_params()`, i.e. they are exactly
the values in that module's `USER CONFIGURATION BLOCK`. Values are clamped to
`cubesat_adcs_sim.PARAM_LIMITS`; an unrecognised parameter is rejected with HTTP 400.

| Group | Field | Default | Meaning |
|---|---|---|---|
| Orbit | `alt_km` | 550 | Mean altitude above the Earth's surface |
| | `inc_deg` | 97.4 | Inclination (97.4° ≈ Sun-synchronous) |
| | `ecc` | 0.011 | Eccentricity |
| | `raan_deg` | 95 | Right ascension of the ascending node |
| | `argp_deg` | 0 | Argument of perigee |
| | `nu_deg` | 0 | True anomaly at epoch |
| | `epoch` | 2026-06-23T10:30Z | UTC epoch — sets Sun direction, field geometry and eclipse timing |
| Timing | `t_total` | 16000 s | Simulated duration |
| | `dt` | 1.0 s | Integration and telemetry step |
| | `t_impact` | 3000 s | Debris strike |
| | `t_recovery` | 5180 s | Ground re-enables control |
| Debris strike | `corrupted_axis` | 0 (X → RW-1) | Axis whose control gain is corrupted |
| | `corruption_factor` | 10 | Corrupted axis commands this multiple of the rated torque |
| | `dw_x`, `dw_y`, `dw_z` | 0.018, −0.011, 0.025 rad/s | Instantaneous body-rate kick from the strike |
| | `seed` | 42 | Sensor-noise seed — the same seed reproduces a run exactly |

Run time is roughly 4–8 s for a 3-hour scenario at 1 s steps; the simulation runs on
a single-worker thread pool so the event loop stays responsive.

## What you get back

**Statistics row** — max pointing error, max estimator error (`est_err_deg`, a
simulation-only truth comparison), peak tumble rate, peak wheel momentum as a
percentage of saturation together with the wheel that peaked, peak wheel speed in
RPM, minutes spent in NORMAL / SAFE / RECOVERY, the share of samples with an
accepted TRIAD fix, and the sample × channel count.

**Event timeline** — the three storyline events, colour-coded:

| Event | Colour | Meaning |
|---|---|---|
| `impact` | amber | Debris strike: rate kick, trip to SAFE, latent gain corruption planted |
| `recovery` | green | Ground re-enables control; the corrupted axis begins to over-torque |
| `autosafe` | red | A wheel saturates and the ADCS trips itself back to SAFE |

**Plots** (four tabs; background shading is green = NORMAL, orange = post-impact
tumble, red = recovery and after):

| Tab | Panels |
|---|---|
| Sensors | Magnetometer XYZ + |B|; Sun-sensor body vector; eclipse / Sun-valid / **TRIAD-accepted** flags |
| Estimator | Pointing error vs nadir **and** estimator error vs truth; body rates + tumble rate; control mode |
| Actuators | Wheel torque with the rated-torque line; wheel momentum with the saturation line; **wheel speed (RPM)**; magnetorquer dipole |
| Event Zoom | Second-by-second zooms of the impact and the recovery, with the commanded vs delivered torque on the *selected* corrupted axis |

**CSV** — the full run (38 channels), served per run id so two browser tabs cannot
hand each other the wrong file; the last eight runs are kept in memory.

## HTTP interface

| Route | Purpose |
|---|---|
| `GET /` | The dashboard (defaults injected from the physics module) |
| `GET /api/defaults` | `{defaults, limits, have_matplotlib}` — useful for scripting |
| `POST /run` | JSON parameter dict → `{run_id, plots (base64 PNG), stats, events, params}` |
| `GET /download?id=<run_id>` | Telemetry CSV for that run (falls back to the most recent) |

## Tests

`tests/test_adcs_sim_viewer.py` — route registration, "no embedded physics copy",
parameter validation, a very short run through the shared module (events, stats,
CSV) and one HTTP round trip asserting non-empty PNGs.

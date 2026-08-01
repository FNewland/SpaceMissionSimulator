# Plan pack — 2026-08-01 simulator evolutions

Prepared 2026-07-31 from eight parallel deep-dive audits of `SpaceMissionSimulation` and `SpaceCDF`.

| File | What it is |
|---|---|
| **`PLAN.md`** | **Start here.** Phase plan A→G, sequencing, the reasoning behind the order, and the model-allocation split (Fable vs delegated agents). |
| **`DECISIONS.md`** | Nine open judgement calls with a recommendation for each. Four are flagged ⚠ as worth Franz's attention before work starts. |
| `briefs/01_power_eps.md` | EPS power balance — why it doesn't close, the honest budget, the recommended array architecture. |
| `briefs/02_aocs_attitude.md` | Attitude determination & control — why fine pointing is inert, and the full proposed model (MEKF, PD, sensors, disturbances, secular mode). |
| `briefs/03_propulsion_orbit_control.md` | Orbit control — the hardware trade, ΔV budget, burn model, SGP4 integration, full interface spec. |
| `briefs/04_payload_commands_and_byte_ux.md` | Payload command divergences, the argument-schema design, the FPA setpoint worked example, builder UX spec. |
| `briefs/05_thermal_fpa_heaters.md` | FPA curve divergence, heater status, and the heater↔EPS billing coupling. |
| `briefs/06_report_generator_and_config_editor.md` | Delayed-TM report generator (schema + 3 templates) and the safe versioned config editor. |
| `briefs/07_textbook_reconciliation.md` | SpaceCDF vs simulator divergence register and the courseware work packages. |
| `briefs/08_suite_health.md` | Everything else — open items, dead/unwired code, test strategy, engineering quality, operator gaps, proposed extras. |
| `briefs/scripts/thermal/` | Runnable reproduction scripts: `fpa.py`, `bat.py`, `panel.py`. |

Each brief ends with a work-package table carrying files, testable acceptance criteria, effort and dependencies. Those are the units of work; `PLAN.md` is the ordering and the argument for it.

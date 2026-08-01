# EO-Sat-1 Power-Budget Analysis (Review Item 9)

Quantitative check of "the battery will not drain flat under standard operation", using a
standalone reproduction of the simulator's own EPS arithmetic.
Simulation code: `/home/claude/work/power_sim/eps_sim.py`; raw results `/home/claude/work/power_sim/results.json`.
Sources: `power_model_extract.md`, `scenario_config_inventory.md`, `integration_trace.md`.

## 1. Method

72 h Euler integration at dt = 1 s (speed 1×), reproducing the simulator's equations verbatim —
not textbook ones:

- `ΔSoC% = net_W·dt/4320` (120 Wh, no coulombic efficiency); OCV = 21.5 + 7.7·SoC/100 V;
  `bat_i = net/ocv`; `v_loaded = ocv − bat_i·0.05` (the sim's sign convention makes bus voltage
  *rise* ~0.07 V under discharge — reproduced as-is); bus = min(29, bat_v); noise terms
  N(0,1) W on consumption, ±0.5% per panel on generation, 0.02/0.01 V on voltages (seed 42).
- **Loads** (line-based, the wattages the tick code actually bills): obc 15 + ttc_rx 5 (hard-coded)
  + wheels 6 + heaters 6+4 (billed full, thermostat ignored) = **36 W nominal platform**;
  ttc_tx +20 W; payload 8 W standby / 45 W imaging; FPA cooler 15 W.
- **Generation**: 6-panel CSS model, wings deployed, β = 0. Sun sweeps the body X-Y plane at
  360°/5700 s; per face `0.295·1361·area·max(0,cosθ)·0.97`; ±X 0.06 m², ±Y 0.27 m² (deployed).
  Zero in eclipse (binary), fraction 0.38 of the 5700 s orbit.
  Orbit-average generation = **53.8 W** with the eclipse centred on the X-panel lobe, but only
  **46.0 W** when centred on the Y-wing lobe. Because the AOCS sweep (5700 s) and propagator
  eclipse (5669 s) drift ~90° per 72 h in the real sim, both alignments occur within days —
  the budget must close at 46 W.
- **Contacts**: Iqaluit on 2 of 3 orbits (≈10 passes/day), 600 s pass; TX billed AOS→LOS+900 s
  (the engine's auto-TX hold re-arms per TC, which also *defeats the ttc_tx shed stage during
  passes* — modelled). P3/P4 add Troll half an orbit later (TX ≈ every half orbit).
- **Imaging duty (P2/P3)**, defensible per `activity_types.yaml imaging_pass` (a few targets per
  orbit): payload line ON (8 W standby), 3 × 120 s captures/orbit at 45 W on the day-side arc,
  cooler 15 W for 1300 s bracketing them → +15.1 W orbit-average.
- **Protections**: (a) in-model bus-V shed < 26.5 V, order payload→cooler→ttc_tx→wheels, one line
  per tick, latched, no restore (26.5 V ≡ OCV at **64.9% SoC**); (b) FDIR (`fdir.yaml`, working):
  SoC<20 payload off, SoC<15 EPS-safe, bus<26 EPS-safe, bus<24 AOCS-safe; (c) the
  `fault_propagation.yaml` SoC stages 30/20/10 — **dead** (`engine.py:618` reads `eps._state.soc`,
  attribute is `bat_soc_pct`, always sees 100) — run both as-is and as-intended
  (30% payload off, 20% AOCS safe, 10% heater duty −50%, assumption stated).

## 2. Results — current configuration, start 75% SoC

Plot: `/home/claude/work/power_sim/soc_vs_time.png` (top panel) and
`/home/claude/work/power_sim/gen_vs_load.png`.

| Profile | Gen avg (best/worst align) | Load avg (commanded) | Budget Wh/orbit | Min SoC | Time-to-flat | Shed stages tripped |
|---|---|---|---|---|---|---|
| **P1** nominal, Iqaluit only | 53.8 / 46.0 W | 39.6 W | **+22 / +10** | 70.7% (no shed) | never | none — *but* if the run starts at eclipse entry: min 55.7%, **full 4-line cascade at SoC 64.6%, t = 0.7 h** |
| **P2** + imaging duty | 53.8 / 46.0 W | 53.3 W | **+0.8 / −11.6** | 63.7% | never | full 4-line bus-V cascade in **first eclipse** (t = 1.07 h, SoC 64.2%) → imaging permanently dead (no auto-restore) |
| **P3** + Troll (2 stations) | 53.8 / 46.0 W | 56.8 W | **−4.7 / −17.1** | 63.7% | never | same cascade, t = 1.07 h; without it: drains ~5.7 Wh/orbit |
| **P4** sustained science | 53.8 / 46.0 W | 103.1 W | **−78** | 46.5% | never (with sheds) / **1.73 h** with protections defeated | full cascade at t = 0.54 h (SoC 62.4%), before the first eclipse ends |

Bug as-is vs as-intended made **zero difference in every profile above**: the 26.5 V shed fires at
~64–65% SoC, so SoC never reaches the intended 30/20/10 stages. The dead-bug is completely
masked by the (mis-set) voltage shed.

Protection-degraded cases (bottom panel of `soc_vs_time.png`; voltage shed defeated, e.g.
operators re-enabling lines or a scenario disabling it):

| Case | Bug as-is (FDIR 20/15 only) | SoC stages fixed (30/20/10 working) |
|---|---|---|
| P3 | min **19.2%**, caught by FDIR SoC<20 at t = 16.9 h | min **29.4%**, caught at 30% at t = 13.7 h → intervenes **3.2 h earlier, +10.2 pts (+12.2 Wh) reserve** |
| P4 | min **9.8%** (FDIR 20% then 15%) | min **13.8%** (stage 30% at 0.92 h, stage 20% at 2.5 h) |
| All protections off | P3 flat at **26.3 h**; P4 flat at **1.73 h** | — |

## 3. Verdict

**The battery does not flatline under standard operation** — the in-model bus-voltage shed and
`fdir.yaml` rules always catch it (flatline needs *all* protections defeated: 1.7 h in P4, 26 h in P3).
**But the standard-ops budget does not close and the protection thresholds are mis-set:**

1. The shed threshold 26.5 V ≡ **64.9% SoC** sits *above* the normal eclipse operating band from
   the 75% start: P2–P4 all strip payload, cooler, TX and wheels in the first eclipse; even
   nominal P1 does if the scenario starts near eclipse entry. With no auto-restore, science is
   dead ~1 h into every session unless the instructor/operator manually re-enables lines.
2. The imaging mission (P2) is energy-marginal: **+0.8 Wh/orbit** at favourable sweep/eclipse
   alignment, **−11.6 Wh/orbit** at the alignment the sim drifts into within a day or two. The
   planned two-station fix (P3) makes standard ops **net-negative at both alignments** (−4.7 to
   −17 Wh/orbit): after fixing contacts, the EPS budget breaks.
3. The `ttc_tx` shed stage is defeated during passes by the 900 s auto-TX hold (any accepted TC
   forces the line back ON), so shedding never actually saves TX energy in contact.
4. The dead SoC-stage bug (`engine.py:618`) currently changes nothing — it is masked by the
   26.5 V shed — but it becomes the load-bearing protection layer once the shed voltage is set
   sanely (see below), and in shed-defeated scenarios fixing it intervenes 3.2 h earlier and
   preserves ~12 Wh of reserve.

## 4. Recommended changes — AS APPLIED (2026-07-28)

Minimal set, keeping shedding reachable under failure scenarios (training value).
All six numeric recommendations were applied unchanged; the table records where each one
actually landed (defects #49 / #50 / #51).

| # | Change | Applied in | From → To |
|---|---|---|---|
| 1 | Fix dead SoC shed | `engine.py::_tick_fdir_advanced` (`getattr(eps._state,'soc',100.0)` → `bat_soc_pct`); `fdir.py::LoadSheddingManager.get_required_stage` now reads thresholds injected by the engine instead of the hard-coded 30/20/10 ladder | dead → live |
| 2 | Enlarge wings | `configs/eosat1/subsystems/eps.yaml solar_panels` (hardware sheet — NOT simulation.yaml, to avoid a second source for the same number) | 0.240 → **0.300 m² per wing** |
| 3 | Realistic TX power | `eps.yaml transponder_power_w` (hardware sheet) | 20 → **12 W** |
| 4 | Shorten auto-TX hold | `simulation.yaml ttc.auto_tx_hold_s` (engine reads it; no literal left) | 900 → **300 s** |
| 5 | Configurable initial SoC | `simulation.yaml eps.initial.soc_pct` (EPS model applies it in `configure()`) | 75 → **90 %** |
| 6 | Sane shed voltage | `simulation.yaml eps.load_shed.bus_voltage_v` (+ `order`, `restore_hysteresis_v`) | 26.5 → **25.0 V** (≡ 45.5 % SoC) |
| 7 | Keep imaging duty capped | **NOT applied** — planner-side constraint (`planning/activity_types.yaml`) left unchanged; the ≤3 × 120 s captures + ≤1300 s cooler per orbit duty is what the regression test asserts the EPS can carry |

Additional finding while applying these: the pydantic schemas silently dropped
`eps.yaml solar_panels` / `power_lines` and `tcs.yaml thermal_coupling` /
`event_thresholds`, so those sections never reached the models (wings were running on the
0.24 m² code default regardless of the YAML). The schemas now declare them.
Also note the SoC-stage ladder is single-sourced in `simulation.yaml`
(`eps.load_shed.soc_stage_thresholds_pct`), with the engine warning if
`fdir/fault_propagation.yaml` stage thresholds diverge — rather than the other way round.

Not needed: battery growth (120 Wh gives a 22%-SoC eclipse swing at worst load — fine once the
per-orbit trend is positive) and `platform_idle_power_w` (dead config, delete or wire).
Optional: lower `fdir.yaml` bus rules 26/24 V → 24.5/23.5 V so the repaired 30/20/10 SoC stages
layer beneath the voltage shed rather than being pre-empted by it.

**Validation (72 h reruns with #1–#6, start 90% SoC, both alignments):**

| Profile | Trend Wh/orbit (best / worst align) | Min SoC | Sheds |
|---|---|---|---|
| P1 | +41.2 / +25.6 | 81.2% | none |
| P2 | +19.5 / **+3.9** | 72.5% | none |
| P3 (two-station, post-fix) | +17.5 / **+1.9** | 71.1% | none |
| P4 (abuse case) | recovers after protection | 48.5% | FDIR bus<26 V EPS-safe at t≈0.9 h — shedding still demonstrable |

Standard operation (P1–P3) closes with positive margin per orbit at both alignments with zero
shed events; sustained-science abuse (P4) and failure injections (bus_short +80 W, array-loss)
still exercise the full shed/FDIR ladder.

**Re-verification inside the real simulator** (`tests/test_simulator/test_power_budget_regression.py`,
24 h at dt = 10 s driving the configured `EPSBasicModel` with the P3 profile: platform + heaters +
wheels + payload standby, TX 900 s per half orbit, 3 × 120 s captures + 1300 s cooler per orbit):

| Configuration | First load shed | Per-orbit min SoC |
|---|---|---|
| Before (75 % start, 26.5 V shed, 20 W TX, 900 s hold, 0.24 m² wings) | **t = 86 min** (first eclipse), full cascade | 60.6 % then rises only because the loads were stripped |
| After (#1–#6 applied) | **none in 24 h** | converges 77.5 → **72.6 %** (Δ < 0.2 pt/orbit by orbit 12) |

The absolute end-of-run SoC is not a useful metric here because SoC saturates at 100 % each
sunlit arc; the per-orbit minimum converging is the correct closure criterion.

## 5. Caveats

- β = 0 worst-case eclipse fraction; at β = 20–40° margins improve slightly (cosβ on X/Y offset
  by the +Z sinβ term). Pre-wing-deploy generation (~22 W sunlit avg) cannot sustain even the
  30 W commissioned platform — LEOP is only survivable because the AOCS-off fallback model
  over-generates (~245 W); wing deploy must precede wheel/heater commissioning in procedures.
- Heater lines are billed at full 10 W regardless of thermostat duty (sim behaviour, reproduced);
  real margin is slightly better than modelled.
- Pass geometry is schematic (600 s passes, 2 of 3 orbits); energy-wise TX duty matches the
  Iqaluit-only reality of the current sim (~17% with hold, ~10 passes/day).

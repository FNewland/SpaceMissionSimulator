# 86 — the ground segment assumed a single downlink rate

## Summary

Defect #85 replaced the binary `tm_rate_lo_bps: 1000` / `tm_rate_hi_bps: 64000`
switch with a config-declared three-rung ladder in
`configs/eosat1/subsystems/ttc.yaml`:

| # | Tier | bps | Modulation | Coding | Code | Available in |
|---|---|---|---|---|---|---|
| 0 | `BEACON` | 1 000 | BPSK | conv(7,1/2)+RS | 0xFF | Bootloader / beacon mode ONLY |
| 1 | `LOW` | 16 000 | BPSK | conv(7,1/2)+RS | 0x00 | **ROUTINE OPERATIONS** and the safe-mode / FDIR fallback |
| 2 | `HIGH` | 128 000 | QPSK | RS(255,223) | 0x01 | Dumps only: antenna deployed **and** DOWNLINK mode (new S8 func 79) **and** elevation >= 10 deg |

That fixed the spacecraft and the RF chain. **Everything downstream of the
spacecraft still believed there was one rate**, and in three places that belief
was not merely stale text but arithmetic that produced wrong answers.

### 1. The planner budgeted every byte at one rate

`smo_planner/budget_tracker.py` carried `DOWNLINK_RATE_BPS = 64000` (`:55`) and
resolved `params["downlink_rate_bps"] = ttc.tm_rate_hi_bps` (`:121`), and that
one number was applied to every activity on every pass.
`constraint_checkers.py` repeated it three times (`:538/:549/:580`) and
`server.py:1114` a fourth.

Under the ladder that is wrong in both directions at once:

* routine and housekeeping traffic runs at **LOW (16 kbps)**, so the planner was
  crediting it with 8x the throughput it has;
* a `data_dump` reaches **HIGH (128 kbps)** only when the antenna is deployed,
  DOWNLINK mode is on and the elevation is at or above 10 deg. Nine of the 52
  contact windows in a typical day never reach 10 deg at all, so a dump booked on
  one of them is refused the tier by the spacecraft (S1.8, error 0x0007) and runs
  at LOW — moving an eighth of the volume the plan booked.

The old `_elevation_efficiency()` was a triangular guess at "time above ~10 deg"
that predated the gate actually existing.

`planning/activity_types.yaml`'s `data_dump duration_s: 600` was sized for the
retired flat rate. At HIGH, 600 s carries 1.8 images — capacity the plan books
with nothing to put in it.

### 2. The console showed a bare number

`static/index.html` printed `ttc.data_rate_bps` and nothing else. With a ladder
that is not enough to operate on. The tier carries the modulation and the coding,
so "128000 bps" hides that the link just moved from BPSK conv+RS to QPSK
RS(255,223). Worse, the tier carries **permission gates**, so the tier the ground
COMMANDED (`0x053A`) and the tier the spacecraft is FLYING (`0x0539`) can differ
— and they differ precisely when something interesting has happened. A console
that shows only the active rate turns a diagnosable condition ("HIGH was
commanded, the 10 deg gate refused it") into a mystery ("why is the dump slow?").

The four new parameters `0x0539`-`0x053C` were in neither
`telemetry/parameters.yaml` nor `mcs/limits.yaml`, and there was no UI control for
`TTC_SET_DOWNLINK_MODE` (S8 func 79) — so the HIGH tier's mode gate was
unreachable from the console. The two rate buttons also sent the 4-byte raw-bps
form `41 00 00 FA 00`, which disagrees with the catalogue's declared one-uint8
length (the defect #80 class of bug) and encoded the retired 64000.

### 3. Procedures cited a function that does not set the rate

`procedures/nominal/data_rate_change.md:38,64` and
`procedures/contingency/ber_anomaly.md:41,93` both cited **func_id 52** where
`commands/tc_catalog.yaml` says **65**. Func 52 is in the OBDH range
(50-62) — the step was a no-op against TT&C and had been for as long as the cards
have existed. Pre-existing, unrelated to the ladder, and fixed here.

Beyond that, no procedure knew about the gates: the dump procedures raised the
rate without entering DOWNLINK mode or checking elevation, so under the ladder
they would fail mid-pass with an S1.8 error the operator could not explain.

### 4. Every rate figure in the courseware was stale

`manual/05_ttc.md` (which also still carried the retired UHF frequencies),
`manual/09_leop.md`, `manual/07_orbit_ops.md`, `manual/06_payload.md`,
`docs/PUS_SERVICE_REFERENCE.md`, `docs/OPERATIONS_GUIDE.md`,
`docs/design_document.html`, `docs/EOSAT1_SMO_Design_Document_Web.html`,
`docs/generate_design_pdf.py`, `docs/sim_fidelity/ttc_fidelity.md`,
`docs/test_plan/ACCEPTANCE_TEST_PLAN.md:147`,
`docs/EOSAT1_Commissioning_Pass_Plan.html`,
`docs/EOSAT1_Commissioning_Procedures.html`,
`configs/eosat1/mcs/role_analysis/ttc_role.md`,
`packages/smo-planner/CONSTRAINTS.md` and `PLANNER_CONFIG.md`.

## Severity

**Major.** A mission plan that books capacity the spacecraft will refuse to
deliver is worse than no plan, and a console that cannot show why a commanded
tier is not active removes the diagnosis the ladder was designed to teach. The
courseware half is Serious rather than Major on its own, but it is the half a
student actually reads.

## Status

**Fixed.**

### Planner

`budget_tracker.py` now resolves **two** rungs from `tm_rate_tiers` and keeps
them apart: `routine_rate_bps` from `default_tier` (LOW) and `dump_rate_bps` from
the top rung (HIGH), plus `default_min_dump_elevation_deg` from the HIGH tier's
own `min_elevation_deg`. `downlink_rate_bps` survives as the legacy alias for the
dump rung so an out-of-lane caller still works.

The triangular efficiency guess is replaced by derived pass geometry.
`fraction_above_elevation(max_el, gate, mask, altitude)` computes the fraction of
a window above the gate from `gamma(eps) = arccos((Re/r)cos eps) - eps` and
`Delta(eps) = arccos(cos gamma(eps) / cos gamma_min)`:

| Pass max elevation | Fraction above 10 deg |
|---|---|
| 7 deg | 0.00 — cannot dump at HIGH |
| 12 deg | 0.47 |
| 30 deg | 0.76 |
| 90 deg | 0.79 (even a zenith pass has wings below the gate) |

Per-pass capacity is `usable_s x [f_high x 128 kbps + (1 - f_high) x 16 kbps] x 0.8`,
with `usable_s` the window less the station's setup and teardown. A dump booked on
a pass that never clears the gate raises `DUMP_BELOW_ELEVATION_GATE`.

The budget now shows the trade instead of hiding it behind one MB/day figure:

* `hk_stream_bps` = **1557 bps**, computed from `telemetry/hk_structures.yaml`
  (1351.5 bps of PUS packets x RS(255,223) x 1115/1107), not assumed. A day of it
  is 16.0 MiB and costs **36 % of the whole network's contact time at LOW** and
  4.5 % at HIGH (`hk_contact_fraction_percent`).
* `image_dump_seconds` = **327.7 s** and `image_pass_fraction_percent` = **74 %**
  of the 445 s average pass. One image is most of a pass at HIGH, and 589 % of one
  at LOW — so imagery does not merely slow down at LOW, it becomes impossible.
* `daily_capacity_by_tier_mb` = **{LOW: 28.4, HIGH: 227.1} MiB/day**, against
  **161.9 MiB/day** on the realistic per-pass blend.
* `dump_contact_fraction_percent` — a pass spent dumping is a pass not spent
  otherwise.

`activity_types.yaml`'s `data_dump` is re-derived: **330 s**, which is one 4 MiB
image at HIGH with the 20 % protocol allowance (327.7 s) rounded up. Its command
sequence now enters DOWNLINK mode (func 79), raises the tier (func 65, rate=1),
waits for `ttc.tm_data_rate` = 128000, dumps, and releases DOWNLINK mode so the
active tier falls back to LOW.

`plan_templates.py` gains `min_pass_elevation_deg` on `pass_relative` anchors, and
`standard_plans.yaml`'s `nominal_ops_day` uses it: dumps are booked only on the 43
of 52 windows that clear 10 deg, centred on closest approach
(`at: midpoint, offset_min: -2.75`) so the whole 330 s activity sits above the
gate. The plan generates 8.6 MB against 44 MB of booked-pass capacity and closes
with **80 % margin** — and it closes *because* it books HIGH: at LOW those same
eight passes carry 5.5 MB against 8.6 MB generated.

### MCS

`server.py` loads `tm_rate_tiers` at start-up into `_tm_rate_ladder` (labels
included) and publishes it as `state.tm_rate_ladder`, so no rate table is
duplicated in JavaScript. `_build_link_tier_view()` resolves the ACTIVE and
COMMANDED tiers from telemetry (0x0539 / 0x053A / 0x053B / 0x053C) with the
simulator's `/api/state` `ttc` block as fallback, and produces a demotion reason
naming the open gate. The TT&C page shows:

```
TM Rate (active)      LOW (16 kbps, BPSK, conv+RS)
TM Rate (commanded)   HIGH (128 kbps, QPSK, RS(255,223))
Tier gate             HIGH needs elevation >= 10 deg (now 6.4 deg)
Symbol Rate           16.00 kBd
DOWNLINK Mode         [ ] OFF
```

`/api/fdir` carries the same `link` block, and the FDIR state strip carries the
tier name and the demotion flag — FDIR's `ttc_reduce_power` moves the tier, so
"which rung, and is it the one we asked for" belongs next to the FDIR level.

`telemetry/parameters.yaml` declares 0x0539-0x053C; `mcs/limits.yaml` limit-checks
them and 0x0506, with **LOW as the nominal band and the beacon rung as the alarm
case** rather than the other way round. The quick-command buttons send the
catalogued one-uint8 form (`41 00` / `41 01`) and a DOWNLINK-mode pair
(`4F 01` / `4F 00`) sits beside them; `mcs/positions.yaml` grants func 79 to the
TT&C position.

### Procedures

func_id 52 -> 65 throughout. `data_rate_change.md` (NOM-011) rewritten around the
ladder with the gate checks, the active-vs-commanded verification and a release
step. `ber_anomaly.md` (CTG-014) steps down to LOW and quantifies what that buys
(9.03 dB of rate plus 1.5 dB of coding). `data_downlink.md` (NOM-003) commands
the real gate instead of faking a rate change through a payload `SET_PARAM` —
which retired the last `("SET_PARAM", 8, 1)` entry from the annotation-drift
allowlist. `first_acquisition.md` (LEOP-001) configures the demodulator for the
BEACON rung and re-configures it for 16 kBd at application boot.
`ttc_link_verification.md` (COM-006) gains a re-measured per-tier, per-station
margin table and two new steps: baseline each rung and confirm the 10 deg gate
rejects HIGH. `ttc_link_loss.md`, `no_telemetry_at_pass.md`,
`gs_antenna_failure.md`, `obc_bootloader_recovery.md`, `boot_loader_recovery.md`,
`loss_of_communication.md` and `shift_handover.md` updated.

### Docs and courseware

Every file listed under §4 above. New: **`docs/LINK_BUDGET_AND_RATES.md`** — the
ladder, why each rung exists, the modulation/coding rationale, the full margin
table from `tools/link_budget_ladder.py`, the mission-planning consequences and
how to change the numbers in config.

`docs/review_2026-07/textbook_vs_sim.md` rows 6/7/8/9/20/21 rewritten. The
book's p. 400 claim that "the EOSAT-1 simulator only supports the two rates
above" is now **FALSE** and flagged WBK — though the *command encoding* it prints
is unchanged, so printed exercises still run. Its COM-006 baselines (14.5 dB @
1 kbps, 6.8 dB @ 64 kbps) are recorded as **not reproducible and never were**:
an 18 dB rate ratio must give an 18 dB margin difference and those two figures
differ by 7.7 dB, so no correct model of any band could produce them.

### Measured margins (`python tools/link_budget_ladder.py`)

| Station | G/T | BEACON @5 deg | LOW @5 deg | HIGH @5 deg |
|---|---|---|---|---|
| Inuvik | 22.0 | 59.8 | 47.7 | 37.2 |
| Svalbard | 21.5 | 59.3 | 47.2 | 36.7 |
| Iqaluit | 20.0 | 57.8 | 45.7 | 35.2 |
| O'Higgins | 20.0 | 57.8 | 45.7 | 35.2 |
| Troll | 18.5 | 56.3 | 44.2 | **33.7** |

Worst case 33.7 dB. Every rung closes everywhere with tens of dB to spare, and the
rungs are separated by exactly what the budget predicts (12.04 dB BEACON->LOW,
10.53 dB LOW->HIGH). **The 10 deg gate is a policy gate, not a margin gate**, and
the courseware now says so — which is a more honest lesson than a fake power
cliff.

### Tests

* `tests/test_planner/test_rate_ladder_budget.py` (new, 15 tests) — the
  per-activity rate split, the elevation gate as derived geometry, the
  `DUMP_BELOW_ELEVATION_GATE` warning, and `nominal_ops_day` closing *because* it
  books HIGH.
* `tests/test_mcs/test_tm_rate_ladder_display.py` (new, 17 tests) — the ladder is
  config-driven, the label format, active-vs-commanded with demotion reasons, the
  new parameters and limits, and the console's command buttons.
* `tests/test_content/test_data_rate_content.py` (new, 190 checks) — the tripwire
  for the *next* rate change: no retired figure in a live document unless the same
  line flags it as history; every bps and Bd figure in a rate procedure is a
  configured tier; the func ids match the catalogue; the planner's per-station
  gate equals the tier's.
* Updated: the three pinned assertions in `test_data_budget_config.py`,
  `test_budget_tracker.py`'s capacity derivation, `test_activity_funcids.py`'s
  routing table (the TT&C S8 range runs to 79 since defect #85),
  `test_standard_plans.py`'s dump anchor, and one allowlist entry in
  `test_procedures_content.py`.

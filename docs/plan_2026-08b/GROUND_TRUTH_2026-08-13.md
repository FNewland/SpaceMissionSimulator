# EO-Sat-1 ground truth for the 2026-08 textbook pass

Authoritative source is ALWAYS the tree at `/root/smo` — `configs/eosat1/`,
`packages/*/src/`. This file is a navigation aid and a list of known traps, not
a substitute for checking. Everything here reflects the tree AFTER the
2026-08-12/13 fix batch.

## Where to check things
- Parameters + hex ids: `configs/eosat1/telemetry/parameters.yaml`
- Commands, func_ids, argument schemas: `configs/eosat1/commands/tc_catalog.yaml`
  and the generated `configs/eosat1/manual/09_command_reference.md`
- AOCS mode codes: `configs/eosat1/aocs/aocs_mode_codes.yaml`
- Procedure cards + index: `configs/eosat1/procedures/`, `procedure_index.yaml`
- Operator manual: `configs/eosat1/manual/`
- Plan/rationale docs: `docs/plan_2026-08b/` — `COHERENCE_REVIEW.md`,
  `POWER_SIZING.md`, `DISPLAY_REQUESTS_{EPS,AOCS,TTC,PAYLOAD}.md`,
  `MCS_REQUESTS.md`. Several have explicit "notes for the textbook owner".

## Owner decisions (Franz, 2026-08-13)
- **D-1 swath = 240 km.** The book was right; the MODEL was changed to match.
  New instrument chain: 8192 px x 5.5 um detector, f = 84.5 mm, full FOV
  29.856 deg, swath 2 x 450 x tan(14.928 deg) = **240 km**, **GSD 29.3 m**,
  line rate 244 Hz, raw readout 96 Mbps, 2048-line strip = 8.4 s = 60 km
  along-track. Planner roll limit is now 14.93 deg (was 7.5). Note the raw
  frame implied by the geometry is 100.7 MB while `image_size_mb` remains the
  courseware's 4 MB — that gap is deliberate and documented, do not paper over it.
- **D-2 delete the no-propulsion argument** in Part 1 section 3.4. EO-Sat-1 has a
  1U green monoprop, 66.9 m/s, for debris avoidance and limited orbit
  maintenance — NOT end-of-life deorbit. Describe it as flown.
- **D-3 the two orphan nominal cards** (Pass Plan Upload, Long-Term Trend Review)
  get wired into the nominal-operations flow and referenced from the book.
- **D-4 FDIR severity 0-4 and autonomy level 0-3 both stay**, named distinctly
  and never conflated.
- **D-5 phase transition stays console-only** (instructor advances the phase);
  say so explicitly rather than implying a telecommand exists.
- **D-6 the 13 misrouted S19 rules** are UNRESOLVED. Do not describe them either
  way; do not blanket-remap. Leave the book silent on those specific rules.
- **D-7 five-station teaching baseline.** Book and exercises assume the full
  five-station network throughout.

## AOCS
Mode codes (`aocs_mode_codes.yaml`): 0 OFF, 1 SAFE_BOOT, 2 DETUMBLE,
3 COARSE_SUN, 4 NOMINAL, 5 FINE_POINT, 6 SLEW, 7 DESAT, 8 ECLIPSE, 9 INERTIAL.
**The book and the shipped cards were systematically off by one — assume every
mode number in the book is wrong until checked.**
- FINE now works on **3** wheels. Precondition bitmask 0x02C5 bit 1 now means
  "fewer than THREE wheels", not fewer than four. `fine_degraded` exists.
- Desat completes. Honest dump time **20-50 min** (the book's 5-15 min is wrong),
  with a timeout, an imaging inhibit, and return to the pre-desat mode.
- Detumble is genuinely magnetometer-driven B-dot, `m = -k dB/dt`, dipole
  saturating at 1.6 A m^2 per axis. It converges SLOWLY: ~3.5e3 s from a
  14 deg/s tumble; below the 0.5 deg/s exit guard at ~1166 s. Any book claim of
  a fast detumble is wrong.
- New gyro telemetry 0x0294-0x0296. Body rates are `rate_roll/pitch/yaw` at
  0x0204-0x0206 — there is no `aocs.body_rates`.
- Wheel speeds are READ-ONLY at 0x0207-0x020A; temps 0x0218-0x021B; the
  commanding path is `RW_SET_SPEED_BIAS` (S8 func 8, bias is ADDED to current
  speed) and `AOCS_RW_RAMP_DOWN` (func 14, wheel_idx 255 = all).
- MTQ enable is func 9; `aocs.mtq_enabled` 0x025E; duties 0x0258-0x025A.
- Star trackers: **two** (ST1/ST2), `ST_SELECT` func 6. Not single-head.
- Gyros: three axes, **no redundant unit**. Devices `S2_AOCS_GYROSCOPE_0/1/2`
  at 0x0206-0x0208. There is no IMU power line and no IMU cold-boot delay.
  Gyro bias calibration is ON-BOARD: `AOCS_GYRO_CALIBRATION`, func 13 — not a
  ground-computed bias upload.
- `AOCS_EST_SET ss_imu` does not exist. Use `AOCS_EST_RESET` (func 91) and
  `est_source` 0x02C1.
- `gyro_bias` injection is a PERSISTENT offset (survives ticks and breakpoints
  until cleared). `instructor_book.md`'s "IMU drifts" scenario is real — keep it.

## Power / EPS
Re-derived from real 6U flight parts. **Every wattage, array area, battery
figure, generation number and margin in the book must be re-read from
`docs/plan_2026-08b/POWER_SIZING.md` and the configs.** This is the biggest
single numerical sweep in the pass. Known anchors:
- EOL solar constant after the loss stack: 267.9 W/m^2. Wings 0.18 m^2/wing.
  Battery 100 Wh. FDIR undervoltage ladder 24.5 V.
- Nine power lines, indices 0-8: obc, ttc_rx, ttc_tx, payload, fpa_cooler,
  htr_bat, htr_obc, aocs_wheels, prop. **Not seven.** Every switchable line
  defaults OFF. `EPS_POWER_ON/OFF/RESET_OC_FLAG` line_index range is 0-8.
- Wing status is 0x0144 (bit0 +Y, bit1 -Y, bit4/5 in progress); per-wing
  currents 0x0146 (+Y) / 0x0147 (-Y). 0x0110-0x0113 are power-LINE flags, not
  array telemetry.
- EPS bills from actual actuator state, not line position.
- New EPS failure `sada_stuck` (solar array drive seizure).

## Thermal / payload cooler
The FPA cooler is a **miniature Stirling-class cryocooler**, not a
thermo-electric/Peltier stack. Its authority degrades with dT
(`fpa_cooler_carnot_fraction` 0.23). It is now genuinely configurable:
`fpa_cooler_carnot_fraction`, `fpa_cooler_power_w`, `fpa_imaging_load_w` are
real `TCSConfig` keys. New declared telemetry `tcs.fpa_cooler_floor_c` (0x0424)
and `tcs.fpa_cooler_margin_k` (0x0425). FPA setpoint is a uint16 in
**centi-kelvin** (-15 C -> 0x64D7).

## TT&C
EO-Sat-1 genuinely has **two transmitters**: an S-band prime and a separate
UHF whip beacon. **PA-off stops S-band TM while the beacon survives** — that
contrast is a deliberate teaching point and should be made explicitly.
- `link_status` LOCKED is **2**, not 1.
- Link rate ladder: BEACON / LOW / HIGH = 1 / 16 / 128 kbps.

## Payload
- CALIBRATION is mode **3**. Dark cal no longer requires IMAGING mode.
- There is an absolute shutter command.
- Five dedicated calibration-refusal failure codes now exist, 0x0024-0x0028:
  0x0024 refused because IMAGING, 0x0025 FPA not ready, 0x0026 shutter in the
  wrong position, 0x0027 lamp in the wrong state, 0x0028 capture with the
  shutter closed. (0x0021-0x0023 remain the capture-pointing codes.)
- The image path is real end to end, so the book's old note that "images do not
  appear on their own" is now actually TRUE and can be stated plainly.
- `manual/06_payload.md` still carries the OLD geometry (54 km swath, 9 m GSD,
  40 Mbps) — it is wrong; the 240 km chain above is right.

## Housekeeping / PUS
- HK SID map: **1 EPS, 2 AOCS, 3 TCS, 4 OBDH, 5 Payload, 6 TT&C, 8 EPS-extended,
  9 PROP, 11 Beacon.** The book is very likely off by one.
- `default_enabled_sids` is `[1, 11]` and the engine re-applies exactly that on
  application boot. Everything else needs `HK_ENABLE`.
- `HK_ENABLE` is **S3.5**; `HK_REQUEST` is **S3.27**. Any claim of S3 subtype 1
  or 25 for an HK request is wrong.
- There is no "S8 subtype names the subsystem" scheme. That was invented.

## Propulsion
1U green monoprop, 66.9 m/s total. S8 funcs 108-118. Telemetry base 0x0B00.
HK SID 9. A burn re-fits the SGP4 element set, so passes and eclipses really do
move (1 m/s -> delta-a +1.800 km, -33.5 s at +24 h). Sim speed is clamped to
<= 10x during burns.

## Procedures
- Eight cards are machine-runnable. The executor no longer reports success for
  a procedure that did nothing, and the MCS runner now executes `label`/`branch`
  steps and FAILS CLOSED on any step type it does not understand.
- Advisory cards are marked with an `advisory: true` field (plus
  `advisory_reason`) on the index entry — NOT with a fake `runnable` link.
- **LEOP-006 step 2** and **COM-006** (antenna deploy, downlink-mode off, beacon
  off) were both rewritten in Phase 5. Mirror the shipped cards into the book.
- `clock_sync.md` is **NOM-008**; NOM-006 is Software Upload.
- `SET_PHASE` is no longer an alias of `OBC_BOOT_APP` — the alias was removed.
  Do not mention it.

## MCS
Staleness is cadence-derived. There is a PROP tab. S12 is visible in dumps.
Commanded-vs-actual mode is shown. **Do NOT describe the design study's proposed
interface** (`docs/design_2026-08/`) — it is researched and mocked up but NOT
implemented, and Franz wants the current version stable first.

## Tree-wide error families to hunt in the book
These were all found in the shipped procedure cards and are very likely in the
book too:
1. `link_status = 1` claimed as LOCKED (it is 2)
2. HK SID off-by-one against the map above
3. HK request quoted as S3 subtype 1 or 25 (it is S3.27)
4. an invented "S8 subtype names the subsystem" scheme
5. command arguments written outside the backticks
6. parameter name / hex-id disagreements
7. AOCS mode names not matching `aocs_mode_codes.yaml`
8. fabricated citations generally — every `file:line`, func_id and hex id in the
   book must be checked against the tree, not assumed

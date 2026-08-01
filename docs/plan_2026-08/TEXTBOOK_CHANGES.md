# Textbook — what must change

**For:** the courseware author working on `orbit_north_week3.md` (Part 2, the
EO-Sat-1 operations book), its workbook, the student procedure quick-reference
and the breakpoint spec. Part 1 (`facilitator_book_expanded.md`) is a generic
design course and is only touched where it is quoted as EO-Sat-1 fact.

**Why now:** the 2026-08 build changed the spacecraft. Some of these entries
are the book being wrong about a simulator that was always right; most are the
book being *out of date* about a simulator that was deliberately changed. Both
kinds are listed, and each says which.

This merges Phase E's command-encoding list with G3-d's thermal-envelope
ruling and the standing divergence register in brief 07.

**Legend** — **BOOK**: the book is wrong, change the book. **BUILD**: the
simulator changed under the book; the book was right about the old behaviour.
**DECIDE**: needs Franz.

---

## 1. Command wire format — changed by the build (Phase E)

| # | What the book says | What the spacecraft now does | Kind | Action |
|---|---|---|---|---|
| T-1 | `PAYLOAD_CAPTURE` (func 28) "with `scene_id` + `lines`" (§6.7, §7.7, §15.5) | **Exactly that.** The handler used to unpack `lat:float32, lon:float32` and ignore both fields, so every planner-scheduled image was taken at 0°N 0°E. Phase E changed the *handler*, not the catalogue | BUILD (in the book's favour) | **No change to §6.7/§7.7/§15.5.** Delete any hedging note that says the sim disagrees. Geolocation is now resolved on the ground from attitude + ephemeris — that is worth one paragraph, because it is what makes the pointing work matter to the product |
| T-2 | `TTC_SET_DATA_RATE` "0 = 1 kbps low, 1 = 64 kbps high" (§7.7 Table 7.6f, §15.5) | Three tiers: **LOW 16 kbps** BPSK conv+RS (routine), **HIGH 128 kbps** QPSK RS (dumps only), **BEACON 1 kbps** (bootloader only, not commandable). HIGH is gated on antenna deployed + `TTC_SET_DOWNLINK_MODE` (func **79**) + elevation ≥ 10° | BOOK | Rewrite the ladder table. **Add func 79** — without it the HIGH tier is unreachable and half of COM-006 cannot be run |
| T-3 | `TTC_SET_TX_POWER` (func 68) with a 1-byte level | Now genuinely a `level:uint8` enum with a corrected watt table. The handler used to read `float32` watts, so three FDIR procedures were silently ignored | BUILD (in the book's favour) | Confirm the book's level values against `tc_catalog.yaml`; the watt table changed |
| T-4 | `PAYLOAD_COOLER_SETPOINT` (func 36) "clamped −20…0 °C" (§6.5 editor's note) | Correct, and now encoded as **uint16 centi-kelvin**: `raw = round((T_degC + 273.15) × 100)`. −15.00 °C → 25815 = `0x64D7` | BUILD | Add the encoding to §7.7 Table 7.6c and to the COM-010 card. This is the worked example the book needs for "why does a negative engineering value not need two's complement?" |
| T-5 | `IMAGE_ACQUIRE` (S8 func_id 64) — §14.2 COM-012, §14.3 NOM-002, workbook WB-5.6-C012 | No such command. **Func 64 is `TTC_SWITCH_REDUNDANT`** — sending it swaps the transponder mid-pass | BOOK | Apply §7.7's own translation table (`IMAGE_ACQUIRE` → `PAYLOAD_CAPTURE`, func 28) in Ch 11, 14, 16 and the workbook |
| T-6 | `STAR_TRACKER_ENABLE` (S8 func_id 36) — §14.1 LEOP-005 | **Func 36 is `PAYLOAD_COOLER_SETPOINT`.** Correct: `ST1_POWER` (4) / `ST2_POWER` (5) / `ST_SELECT` (6) | BOOK | Fix the card |
| T-7 | `MOMENTUM_DUMP_INITIATE` (S8 func_id 42) — §14.3 NOM-007, §14.2 COM-013 | **Func 42 is `HEATER_THRUSTER`.** Correct: `AOCS_DESATURATE` (1) | BOOK | Fix both cards |
| T-8 | `htr_bat` ON (S8 func_id 25) — §14.1 LEOP-007 | **Func 25 is `EPS_EMERGENCY_LOAD_SHED`.** Correct: `HEATER_BATTERY` (40) or `EPS_POWER_ON(line=htr_bat)` (19) | BOOK | Fix the card |
| T-9 | `OBC_BOOT_APP` (S8 func_id 42) in `student_procedure_quickref.md` | Func **55**. Ch 14's LEOP-001 card already says 55 — the two student artefacts disagree | BOOK | Fix the quick-reference |
| T-10 | "Transition to COMMISSIONING phase (S8 func_id 80)" in `breakpoint_spec.md` | Func 80 is `OBC_GPS_TIME_SYNC`. **No phase-transition S8 function exists** | DECIDE | Either add an instructor-side phase-set function (the use case is real) or drop the claim |
| T-11 | `STORAGE_REQUEST_DOWNLINK`, `S19_ENABLE_RULE`/`S19_DISABLE_RULE`, `OBDH_WD_KICK`, `S6_MEM_READ`, `EPS_CELL_BAL`, `MM_MARK_BAD`, `OBC_COLD_REBOOT`, `AOCS_EST_SET`, `S11_UPDATE`, `WHEEL_DISABLE`, `PAYLOAD_POWER_OFF`, `PARAM_SET`, `TX_RATE_SET` | None exist. Real names in §7.7's own translation table and `tc_catalog.yaml` | BOOK | Sweep Ch 14 (all card families) and the workbook against the generated `configs/eosat1/manual/09_command_reference.md`, which is now regenerated in CI |
| T-12 | `AOCS_SET_MODE_2/_3/_4/_5` as distinct mnemonics; "SAFE_POINT" / "NOMINAL_POINT" mode names | One command `AOCS_SET_MODE` (func 0) with a `mode:uint8`. Canonical enum: 3 = COARSE_SUN, 4 = NOMINAL, **5 = FINE_POINT**. §7.7 gets this right; Ch 14 does not. Workbook WB-5.6-C011 says FINE_POINT = 4 | BOOK | Normalise to `AOCS_SET_MODE(mode=n)` and fix the workbook's mode number |
| T-13 | "S8 function IDs 0–82 plus a legacy block" (§7.7) | Range is now **0–118**. New since the book: **79** `TTC_SET_DOWNLINK_MODE`, **83** `EPS_SET_MODE`, **84** `AOCS_GPS_SET_START_MODE`, **108–118** the propulsion block | BUILD | Regenerate §7.7 and §15.5 from `09_command_reference.md` |
| T-14 | S2 device access mentioned but never listed | **47 S2 device commands** exist (device ids 0x0100–0x0604) | BOOK (omission) | Add an §15.5 S2 table — it is the ground's only per-device on/off path |
| T-15 | **S20 subtypes** — the book follows ECSS (20,1 = report request, 20,3 = set) | This suite uses **20,1 = SET** and **20,3 = GET**. The deviation is now documented in `docs/PUS_SERVICE_REFERENCE.md` §Service 20 and every procedure has been corrected to match the wire (79 annotations across ten files) | BOOK | Any card or workbook step quoting an S20 subtype must use 20,3 for a read and 20,1 for a write, with a one-line note that this deviates from ECSS |

---

## 2. FPA thermal envelope — the G3-d ruling

This is the section with the most numbers in it, because five files and the
book disagreed and the disagreement is now resolved from the model's measured
behaviour. Two things had to happen before it was even decidable: Phase B
fixed the setpoint bug (the setpoint was applied as an *additive environment
offset*, so the node asymptoted to ambient − 35 K and never to the setpoint),
and Phase E made the setpoint commandable.

**The single source is `configs/eosat1/subsystems/tcs.yaml: fpa_envelope`.**
Everything below is derived from it and pinned by
`tests/test_content/test_fpa_envelope_single_source.py`.

| # | Book says | Now | Kind | Action |
|---|---|---|---|---|
| T-16 | `payload.fpa_temp` / `tcs.fpa_temp_c` nominal **−15…−3 °C**, yellow > −3, red > 0 (Tables 6.3, 6.5) | **Health band** on the FPA node (0x0408): yellow **−22.0…+14.0 °C**, red **−25.5…+17.5 °C**. Cold finger (0x0601): yellow **−22.5…+13.5**, red **−26.0…+17.0**. Detector die (0x0617): as the node | BUILD | Replace both tables. Explain *why* the band is that wide: the setpoint is commandable across −20…0 °C, and cooler-off (≈ +11.5 °C, peak +12.4) is a legitimate resting state |
| T-17 | The book's −15…−3 band was doing double duty as an *imaging* band | **Split, deliberately.** Health = the bands above. **Imaging readiness** = `fpa_ready` (0x0610): the loop settled about the *active* setpoint (−1 K / +5 K) **and** the detector at or below **−3.0 °C** | BUILD | This is the single most teachable consequence of the whole build: a limit answers "is the hardware healthy", a readiness flag answers "can I take the picture", and conflating them is what produced five different answers. Worth a boxed note |
| T-18 | The book's **−3 °C** upper bound | **Survives, and is now the single source** for `FPA_OVERTEMP` (0x0604), the planner's `fpa_temp_max_c` and the imaging pre-condition. The planner's old −5 °C and the sim manual's old −12 °C were the outliers | BUILD (in the book's favour) | Keep −3 °C. Say that it is the dark-current bound |
| T-19 | COM-010: setpoint **−10 °C**, then success declared at −12 °C | With setpoint −10 the readiness window is **[−11, −5]**, so −12 °C sets `fpa_ready = FALSE` and the next `PAYLOAD_CAPTURE` is **rejected**. The card as written cannot pass | BOOK | Rewrite the card around the shipped default setpoint (−15 °C, window −16…−10 °C) and gate on `fpa_ready`, not a remembered temperature |
| T-20 | COM-010 cool-down: **60–90 minutes**, ambient +5 → −10, slope −0.2…−0.4 °C/min eclipse-side, −0.05…−0.1 day-side | Measured, seeded, payload in the loop: from the uncooled equilibrium (**+11.5 °C**, not +5) the detector reaches **−9.5 °C at T+5 min** and is inside the setpoint band by **T+10 min**. Initial rate ≈ **4 °C/min**, falling exponentially. **There is no eclipse/day-side dependence in the FPA model** | BUILD + BOOK | Rewrite the cool-down table from the sim's numbers. `configs/eosat1/procedures/commissioning/fpa_cooler_activation.md` has already been rewritten and is the reference. Either drop the eclipse-slope lesson or state that the sim does not model it |
| T-21 | COM-010: cooler power **8.5 W**, fault threshold "only 5 W" | **15.0 W** (`subsystems/payload.yaml: fpa_cooler_power_w`, and EPS and TCS agree). Expect a **14–16 W** rise | BOOK | Fix both numbers; the fault threshold has to move with them |
| T-22 | COM-010 hold "±2 °C of setpoint" | Measured 3σ about the setpoint is **0.5 K**. ±2 °C is 12σ — it would pass with a badly degraded loop | BOOK | Tighten to ±1.0 °C peak-to-peak, and say that below 0.5 °C is normal |
| T-23 | §7.1 MCS figure shows `fpa_temp −58 °C` | Unreachable: the setpoint floor is −20 °C and the alarm low is −25.5 °C | BOOK | Regenerate the figure |
| T-24 | §6.5 editor's note: "real multispectral imagers run near −50 °C (Stirling-class); EO-Sat-1 models a Peltier-class cooler clamped to [−20, 0] with an S12 target band of −15…−3" | The clamp is right; the "S12 target band" half is now wrong (see T-16/T-17) | BOOK | Keep the Stirling/Peltier framing — it is good teaching — and replace the band sentence |
| T-25 | COM-002 "heater cycling within ±2 °C of setpoint" | The shipped thermostat bands are **4–5 °C wide** (battery on 1.0 / off 5.0 °C; OBC on 5.0 / off 10.0 °C) | BOOK | Fix to the real bands |
| T-26 | FDIR TCS-04 "`fpa_temp` > 12 °C → power off payload" | Fires off the **S12 alarm** on 0x0408, i.e. outside −25.5…+17.5 °C. A bare "> 12 °C" sits *inside* the normal uncooled state and would power off a payload that is correctly off already | BOOK | Fix; `configs/eosat1/manual/08_fdir.md` has been corrected and is the reference |

---

## 3. Power — changed by the build (Phase B)

| # | Book says | Now | Kind | Action |
|---|---|---|---|---|
| T-27 | "deployable solar arrays generate **28 W** at start-of-life"; "**120 Whr** battery" (§5.7) | Wings **0.18 m² each** on a single-axis drive, honest loss stack (packing 0.85, −0.20 %/°C at 65 °C, harness 0.95, 3-year EOL 0.92), **battery 100 Wh**. Orbit-average generation **51.1 W** at max eclipse, **66.9 W** at worst β, EOL | BUILD | Replace both numbers. The array *drive* is now load-bearing: it converts the wings' `cos β · |sin φ|` duty into continuous `cos β`, 1.56× more energy from the same area, and "SADA stuck" is a real failure mode |
| T-28 | "the platform idles at **70 W**", used as `P_avg` in the §13.3 eclipse exercise | The 70 W was dead configuration — read into a variable and never added to the load. Real orbit-average nominal load is **34.2 W** at worst β, **34.4 W** at max eclipse, with **all nominal systems on** | BUILD | **The §13.3 eclipse-exit exercise must be re-derived.** With 100 Wh and ~34 W the arithmetic changes completely, and it is the exercise students check their working against |
| T-29 | Load shed "< 40 % SoC" (Table 6.2); "load-shed gate at 60 %" (§16.4); CTG-001 "bus < 26.5 V" | One monotonic ladder, locked by a test that converts every threshold to SoC through the configured OCV curve: **shed 45.5 % → EPS-safe 39 % → SoC stages 30/20/10 % → AOCS-safe 22 %** | BUILD | Replace the four different answers with the one ladder |
| T-30 | S19 `eps_overcurrent_payload` "> 0.6 A for > 2 s"; payload line "0.47 A, envelope 0.4–0.6 A" | No rule of that name. Overcurrent is a **model trip at 2.5 A / 50 ms**. Payload standby ≈ **0.29 A** at 28 V; imaging ≈ **1.61 A** | BOOK | Fix §15.3 and the COM-009 walkthrough |
| T-31 | LEOP-007 commands `aocs_imu`, `ttc_tx_b`, `obdh_mm`, `aocs_sensors`, `htr_payload`, `htr_prop` | The power lines are `obc`, `ttc_rx`, `ttc_tx`, `payload`, `fpa_cooler`, `htr_bat`, `htr_obc`, `aocs_wheels`, **`prop`** (new in Phase D). `obc` and `ttc_rx` are non-switchable | BOOK | Six of LEOP-007's sixteen steps command lines that do not exist. Rewrite the card; `prop` is now real |
| T-32 | AOCS power not quoted | **Mode-dependent**: COARSE 4 W / NOMINAL 9 W / FINE 15 W, billed from the actual mode, not the line rating | BUILD | Add to the power budget lesson — it is the cleanest example of "bill the actuator state, not the switch position" |

---

## 4. Attitude — changed by the build (Phase C)

| # | Book says | Now | Kind | Action |
|---|---|---|---|---|
| T-33 | Pointing "< 0.5° (3σ) in NADIR", achievable 0.32°; §13.4 budget ST 0.18 / IMU 0.16 / wheel 0.22 / bias 0.05 | There is now a real error budget from real dynamics: COARSE ≈ **5°**, NOMINAL **0.065°**, FINE **0.013°** total 3σ. Geolocation at 450 km: 39 km / 510 m / 100 m | BUILD | **Rewrite §13.4 and §16.3 from the model**, not the other way round. The book's 0.18/0.16/0.22 components no longer describe anything |
| T-34 | `I_wheel = 1.5 × 10⁻⁵ kg·m²`, ω_max 5000 rpm, H_max 7.85 mN·m·s (§13.7) | A real 6U wheel, taken from a current product line, with the momentum budget, desat cadence and NOM-007 entry criterion derived from its datasheet. The old book value is a nanosat MEMS figure; the old sim value (0.005) was a 200-mm-class wheel | BUILD | Re-derive the whole §13.7 desaturation exercise. It was wrong by 333× against the old sim and is now wrong against the new one for a different reason |
| T-35 | Wheel speed limits: 5000 rpm (§13.7), 7500 rpm (NOM-007 entry), 8000 rpm (§15.3) — **three limits in one book** | One set, in `aocs.yaml` and mirrored in the limits and monitors | BOOK | Pick up the shipped numbers; the internal inconsistency is the book's own |
| T-36 | "a **single-head** star tracker" (§5.7) | Two heads: `ST1_POWER` (4), `ST2_POWER` (5), `ST_SELECT` (6), and CTG-008 depends on switching to the backup | BOOK | Fix §5.7 |
| T-37 | "disturbance torque on a **3U at 500 km**" (§13.7); §13.8 "Z wheel at 4200 rpm, 70 %" | EO-Sat-1 is a **6U at 450 km**; 4200/5000 = **84 %** | BOOK | Fix the spacecraft class and the arithmetic |
| T-38 | FINE_POINT gated at < 0.1° in the Ch 14 cards | FINE entry now has a **precondition bitmask (0x02C5)**: ST tracking ≥ 5 stars, all four wheels active, MEKF converged and sustained 30 s, \|ω\| < 0.01 °/s, momentum < 70 % of envelope, slew idle ≥ 60 s. `AOCS_SET_MODE(5)` is **refused with TM(1,8)** when unmet. The payload then **refuses to capture** unless FINE is held with pointing error < 0.05° | BUILD | New content, and it is the chain that makes fine pointing mean something. Add it to COM-005 and NOM-002 |
| T-39 | Nothing about fidelity under time acceleration | Above **×60** the attitude fast loop is bypassed and `aocs.fidelity_mode` reads 1, with an S5 event | BUILD | Add a warning: **do not grade a pointing number taken from a run above ×60.** See `docs/DETERMINISM.md` |

---

## 5. Propulsion — new in the build (Phase D)

| # | Book says | Now | Kind | Action |
|---|---|---|---|---|
| T-40 | The book is self-contradictory: §5.7 omits propulsion; §14.3 NOM-004/-005, §14.4 CTG-006 and §16.8 assume thrusters; `HEATER_THRUSTER` (42) is taught as live | EO-Sat-1 **now has propulsion**: a 1U green-monoprop (LMP-103S/ADN) module, 0.400 kg, thrust **0.33 N BOL → 0.14 N EOL**, Isp **220 → 196 s**, tank blowdown 22.0 → 9.33 bar, **total ΔV 66.9 m/s**, wet mass 1.55 kg. Funcs **108–118**, TM block **0x0B00**, events **0x0800**, a new EPS `prop` line last in the shed order, and `htr_thruster` is real | BUILD | §5.7 must describe the module. NOM-004, NOM-005 and CTG-006 become runnable procedures rather than aspiration. `docs/PROPULSION_GUIDE.md` is the reference |
| T-41 | (nothing) | The thruster fires on **−X along +X body**, so a retrograde burn requires a deliberate **180° yaw**. The engine clamps `speed` to ≤ ×10 while FIRING and raises `PROP_SPEED_CLAMPED` | BUILD | This coupling is the teaching point of the whole module — at ×1000 a single tick is longer than a 32 s burn and the "abort on attitude error" lesson would be unobservable |
| T-42 | (nothing) | A ΔV **propagates**: a measured TLE re-fit bumps `epoch_version` and invalidates the planner and MCS caches, so passes, eclipse, power and imaging opportunities all move | BUILD | Say so. A manoeuvre that did not change the pass plan would be cosmetic |
| T-43 | §3.4 (Part 1) notes a 6U at 450 km decays inside the FCC 5-year rule | Still true, and it is the standing argument **against** carrying propulsion at all | DECIDE | The textbook audit's independent recommendation was to teach orbit maintenance as *the decision not to carry propulsion* plus a paper ΔV exercise. The module was built on Franz's explicit instruction. If the book keeps §3.4, it should address the tension head-on rather than leave two chapters disagreeing |

---

## 6. Ground segment, data and procedures

| # | Book says | Now | Kind | Action |
|---|---|---|---|---|
| T-44 | "two-station… Iqaluit… Troll… ~14 passes per 24 h" (§5.7, §8.2.1) | **Five stations** (Iqaluit, Troll, Svalbard, Inuvik, O'Higgins). Measured: **51.5 passes/day** (41.5 above 10° max elevation), mean pass **447 s**, **3.38 passes/orbit**, contact duty **26.6 %** | BUILD | **Ch 8's planning exercise needs rewriting, not renumbering.** The ops day is much busier than the book describes, and this is also why the old power analysis (which assumed ~10 passes/day) was wrong |
| T-45 | "4 MB per imaging session, downlink at 1 Mbps → 32 s" (§8.5, §11.9) | Image size 4.0 MB is right; the HIGH tier is **128 kbps**, so **262 s** (330 s with the planner's 20 % protocol allowance) | BOOK | Fix every dump-duration number. This is the difference between "one pass is plenty" and "you must plan the dump" |
| T-46 | "ground swath 240 km" (§11.9 COM-012) | 20 µrad IFOV × 5000 px at 450 km → **swath 45 km, GSD 9.0 m** | DECIDE | 240 km swath with 4 MB frames and 10 m GSD is not self-consistent, and 5000 px × 20 µrad is a small telescope for an ocean-colour mission. **Decide the instrument, then fix whichever side disagrees** — it determines what the mission is for |
| T-47 | Payload store "total 2 GB, used 0 MB" (COM-009) | **20 GB**, and it starts **20 % full** — deliberately, so that downlink rather than storage is the binding constraint | BOOK | Fix |
| T-48 | Mean DN per band; "mean DN at 2900 (oversaturated)"; "mean DN 32, below threshold of 50" | There is no DN telemetry. The sim models per-band **SNR in dB** (0x0620–0x0623) and `last_scene_quality` (%). Gain and integration time now have **real radiometric consequences** (SNR and product size), so the saturation lesson has a referent — but in SNR, not DN | BUILD | Rewrite COM-011 and COM-012 around SNR and `last_scene_quality`. The dark/flat thresholds must be restated in the sim's units |
| T-49 | "geo-registration error 87 m (within 100 m budget)" | No geolocation error model. Derived from pointing: FINE at 0.013° gives ≈ 100 m at 450 km, NOMINAL at 0.065° gives ≈ 510 m | BUILD | The 100 m budget is achievable **only in FINE**. That is a good exercise; make the dependence explicit |
| T-50 | HK "SIDs 1, 3, 4, 6 streaming after boot" (LEOP-001) | `default_enabled_sids: [1, 11]`. SIDs 2–6 must be commanded on with `HK_ENABLE` (S3.5) | BOOK | Add explicit `HK_ENABLE` steps to LEOP-001/-002 — this is a genuine commissioning lesson the sim supports and the book skips |
| T-51 | "SID 1 at 4 s, SID 11 beacon at 30 s" (§13.5) | SID 1 = **1.0 s**, 2 = 4.0, 3 = 60.0, 4 = 8.0, 5 = 8.0, 6 = 8.0, 7 = 4.0 (new, AOCS determination), 9 = 8.0 (new, PROP), 11 = 30.0 | BUILD | Publish the full cadence table — it drives the delayed-TM gap exercise |
| T-52 | Cards read SoC from SID 4, zone temperatures from SID 4, FDIR from SID 1 | SoC is SID 1, zones are SID 3, platform/FDIR is SID 4. Several cards contradict themselves between the action text and the TC column | BOOK | Sweep Ch 14 |
| T-53 | NOM-004…NOM-011 numbering; "12 nominal procedures"; COM-004 "folded into COM-003"; CTG-006 is a propulsion card | The index is authoritative: NOM-004 **now exists** (Orbit Maintenance Manoeuvre Planning), COM-004 is **AOCS Actuator Checkout**, CTG-006 is **Payload Anomaly**. The commissioning cards' COM-101…COM-104 numbering has been corrected to COM-009/010/011/012 throughout | BUILD + BOOK | Re-align Ch 14 against `procedures/procedure_index.yaml` |
| T-54 | Six S19 rules named in §15.3 (`eps_overcurrent_payload`, `aocs_wheel_saturation`, `tcs_payload_zone_redline`, `ttc_link_loss`, `obc_watchdog_event`, `payload_image_corrupt`) and one in §9.5 | **None of those seven names exist.** The 26 real names are in `monitoring/s19_rules.yaml` | BOOK | Replace with real rule names. **Note:** 13 of the 26 shipped rules dispatch a func that contradicts their own description and are held open pending a Flight-Ops ruling (`docs/STATUS.md` S-1) — do not print an action column for those until it lands |
| T-55 | "38 S12 monitors" (§15.2) vs "25 monitor definitions" (§7.6) | **46** | BOOK | Fix both, and prefer generating the number |
| T-56 | The book never mentions that some procedures are machine-executable | The MCS Procedures tab runs `runnable:` procedures; three kinds of link exist | BOOK (omission) | Teach the runner — it is a real MCS capability the course would use |
| T-57 | "a UHF whip for the bootloader beacon" (§5.7) | S-band only | BOOK | Fix |

---

## 7. New capability the book does not know about

Each of these is shipped, tested and currently untaught.

| # | Capability | Where |
|---|---|---|
| T-58 | **Ground limit checking and a real alarm system** — 51 limit bands enforced on every sample, colour coding, acknowledge-with-persistence, unacked count in the header, alarm→procedure linkage | MCS. The book teaches only S12 (on-board) monitoring; ground limits are the *other* half of a control room and 29 limited parameters have no S12 equivalent at all |
| T-59 | **The TRENDS page** — multi-parameter history, time-window picker, limit-band overlay, eclipse and contact shading | MCS. "Show me this parameter over the last 12 hours" is a real MCS's most-used feature |
| T-60 | **The report generator** — daily ops review, pass report, anomaly investigation, as self-contained HTML | 8080/reports and 9090/reports. The genuinely interesting quantity it exposes is **archive minus live**: the S15 archive is the complete tape while the MCS live view discards packets released with the link down, so the difference *is* "what the link cost you" |
| T-61 | **The configuration editor** — schema-validated editing, diff preview, revision history, named baselines, and **per-exercise variants as branches** | 8080/config (read-write, instructor) and 9090/9091 /config (read-only, students). Preparing "the config set for the CERISE ADCS exercise" ahead of class and switching between sessions is the most useful thing this buys |
| T-62 | **FDIR actually runs** — fault cascades propagate with their declared delays, escalation levels start the ten recovery procedures, and the FDIR window shows recovery progress | Ten YAML procedures were registered and never started; seven of the eight FDIR events in the catalogue could never be emitted. For an FDIR-centric course this was the largest gap between the documentation and the tool |
| T-63 | **Reproducibility** — same seed + same epoch + same command script ⇒ byte-identical trace; time acceleration no longer changes the physics up to ×60 | `docs/DETERMINISM.md`. Every worked example in the book is now checkable, which it was not before |
| T-64 | **Compression** — scene-entropy-dependent CCSDS-123, ratio 1.5–4.0, telemetered at 0x0614 | The reason stored size ≠ raw size. Free content |
| T-65 | **Decontamination** (`TCS_DECONTAMINATION_START`, func 47, +50 °C bake-out) | Implemented, telemetered at 0x0418, with a scenario. A real EO ops activity, listed in §7.7 and never taught. Note that the bake-out deliberately breaches the FPA red-high alarm — that is correct behaviour and a good discussion |

---

## 8. Decisions still needed from Franz

| # | Question | Why it matters |
|---|---|---|
| T-46 | **Swath 240 km or 45 km?** | Decides what the instrument is, and the data budget follows from it |
| T-43 | **Does the book keep the "no propulsion" argument** now that the module exists? | Two chapters currently disagree with each other |
| T-10 | **Add an S8 phase-transition function**, or drop the claim from the breakpoint spec? | The instructor-side use case is real |
| S-1 | **The 13 misrouted S19 rules and 4 contradicting trigger ids** | Held by strict-xfail gates with a 2026-10 deadline. The book should not print an action column for those rules until the ruling lands |

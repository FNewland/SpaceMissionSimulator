# Workstream 7 — SpaceCDF textbook vs simulator reconciliation

## Textbook operational inventory

Two books carry operational content. **Part 1 = `~/cdf/docs/course/facilitator_book_expanded.md`** (design course, generic 1U/3U CubeSats, *not* EOSAT-1). **Part 2 = `~/cdf/docs/course/orbit_north_week3.md`** (EOSAT-1 operations, Ch 5–16) with `orbit_north_week3_workbook.md` as the student pad. The Part-2 book contains **two mutually inconsistent layers**: Ch 6–7 have been reconciled against `tc_catalog.yaml` in an earlier pass; **Ch 10–16, the procedure cards, and the workbook have not** and still print command names, func_ids and limits that a previous edition used. Most of what follows is that fault line.

### Cross-cutting: command catalogue
- **§7.7 "Onboard functions (S8 Function ID — working operator subset)"** (`orbit_north_week3.md`) — 7 tables, func_id 0–82 + legacy 100–107. Claims: *"These are extracted directly from `tc_catalog.yaml` — the catalogue file is the canonical authority."*
- **§7.7 "What you will *not* find in the simulator"** — a 20-row translation table from legacy names (`IMAGE_ACQUIRE`, `STAR_TRACKER_ENABLE`, `MOMENTUM_DUMP_INITIATE`, `PAYLOAD_POWER_ON`, `EPS_LOAD_SHED`, `TTC_TX_ENABLE`, `STORAGE_REQUEST_DOWNLINK`…) to real ones. **Chapters 13, 14, 16 and the workbook then use the legacy names anyway.**
- **§15.5 "Command Reference — full list by subsystem"** — 7 tables, mnemonic + purpose only, no data fields.
- **§7.6 "PUS Service Primer"** Table 7.5 — 13 services with subservices.

### PAYLOAD
| Item | Source (file · heading) |
|---|---|
| Table 6.5 payload TM + limits (`payload.mode`, `payload.fpa_temp` −15…−3 nominal / >−3 yellow / >0 red, `payload.fpa_cooler_status`, `payload.image_count`, `payload.store_used` <80 %/>90 %/>95 %, `payload.last_image_quality`, `payload.snr` >25/15–25/<15 dB) | week3 · §6.7 Payload Operations Engineer |
| Command list: `EPS_POWER_ON(line=3)` (19) → `PAYLOAD_SET_MODE(mode=1)` (26); `PAYLOAD_CAPTURE` (28) *"with `scene_id` + `lines`"*; `PAYLOAD_START_CALIBRATION` (37); `S13_CREATE_TRANSFER`→`S13_REPORT_STATUS`; `PAYLOAD_DELETE_IMAGE` (30) | week3 · §6.7 "Commands typically issued" |
| Payload func table 26–39 (incl. `PAYLOAD_COOLER_SETPOINT` "clamped −20…0 °C", `PAYLOAD_SET_INTEGRATION_TIME` per-band, `PAYLOAD_SET_GAIN`, `PAYLOAD_SET_BAND_CONFIG` mask, `PAYLOAD_SET_COMPRESSION`) | week3 · §7.7 Table 7.6c |
| COM-009 card: `EPS_POWER_ON(line=3)`, `PAYLOAD_SET_MODE(mode=1)`, current *"0.3–0.6 A typical"*, *"Storage 2 GB, used 0 MB"* | week3 · §14.2 COM-009 |
| COM-009 walkthrough: *"line current 0.47 A, within 0.4 to 0.6 envelope"*; *"Step 2 line current at 0.68 A — above the 0.6 A nominal envelope, below the 1.0 A overcurrent trip"*; *"payload memory map reports total 2 GB, used 0 MB"* | week3 · §11.9 COM-009 |
| COM-011 card: `AOCS_SET_MODE_5`, `IMAGE_ACQUIRE` dark, `IMAGE_ACQUIRE` flat "4-band integration" | week3 · §14.2 COM-011 |
| COM-011 walkthrough: dark frame *"mean DN 32, below threshold of 50"*; flat *"gradient 3.2 percent, below 5 percent threshold"*; commit to "FPA correction LUT" | week3 · §11.9 COM-011 |
| COM-012 First Light card: `IMAGE_ACQUIRE` **(S8 func_id 64)**, `STORAGE_REQUEST_DOWNLINK` (S15) | week3 · §14.2 COM-012 |
| COM-012 walkthrough: *"bands 443/560/665/865, integration times 8/12/16/20 ms, S11 trigger at T plus 47 seconds"*; *"image LAB-CURR-01-001 stored, size 4.2 MB"*; *"ground swath 240 km"*; *"mean DN per band 1840 / 2102 / 1654 / 891"*; *"geo-registration error 87 m (within 100 m budget)"*; failure branch *"mean DN at 2900 in band 443 (oversaturated) — integration time was too long"* | week3 · §11.9 COM-012 |
| NOM-002 Imaging Session card: S11 time-tagged `IMAGE_ACQUIRE`; *"Pointing error > 0.3° at integration → image likely smeared"*; *"Storage near 95 % → halt imaging"* | week3 · §14.3 NOM-002 |
| Data rule of thumb: *"4 MB per imaging session, downlink at 1 Mbps → 32 s"* | week3 · §8.5 "What good looks like" |
| Workbook WB-5.6-C012 step 2 *"Issue `IMAGE_ACQUIRE` (S8 func_id 64)"*; WB-5.6-FL image assessment form (integration time per band, mean DN per band, geo-registration error, cloud cover) | workbook · WB-5.6-C012 / WB-5.6-FL |
| §16.7 payload case study — cloud cover 60 % vs 8 % forecast; histogram read | week3 · §16.7 |

### ATTITUDE (AOCS)
| Item | Source |
|---|---|
| *"The pointing requirement is **< 0.5° (3σ) in NADIR mode** for imaging; the achievable budget is 0.32° (3σ) with all sensors nominal"*; *"single-head star tracker"* | week3 · §5.7 |
| Table 6.1 AOCS TM + limits; mode enum, body rates, wheel speeds | week3 · §6.3 |
| Func table 0–15 with `AOCS_SET_MODE` "0=OFF, 1=SAFE_BOOT, 2=DETUMBLE, 3=COARSE_SUN, 4=NOMINAL, 5=FINE_POINT, 6=SLEW, 7=DESAT, 8=ECLIPSE" | week3 · §7.7 Table 7.6a |
| **Pointing-budget RSS exercise**: ST 0.18°, IMU 0.16°, wheel 0.22°, bias 0.05° → 0.330°; rule *"if any single component exceeds half the total budget (i.e. > 0.25° for a 0.5° NADIR budget), investigate"* | week3 · §13.4 |
| **Wheel-saturation desat exercise**: *"At the EOSAT-1 simulator limit of ω_max = 5000 rpm = 524 rad/s and I_wheel = 1.5 × 10⁻⁵ kg·m², the maximum storable momentum is H_max = 7.85 × 10⁻³ N·m·s ≈ 8 mN·m·s"*; disturbance *"on a 3U at 500 km ~1.5 × 10⁻⁶ N·m"*; t_sat ≈ 5250 s ≈ 87 min; watch rule *"75 % of the maximum (3750 rpm for a 5000-rpm wheel)"*; desat *"takes 30–40 minutes and uses magnetorquers"* | week3 · §13.7 |
| Worked shift: *"Z wheel at 4200 rpm, 70 %"* | week3 · §13.8 |
| LEOP-005 card: rates < 0.05 °/s, `STAR_TRACKER_ENABLE` (S8 func_id 36), `AOCS_SET_MODE_4` "Mode 4 NADIR", pointing < 0.5° | week3 · §14.1 LEOP-005 |
| COM-005 card: `AOCS_SET_MODE_3` SAFE_POINT (err < 2°) → `AOCS_SET_MODE_4` (err < 0.5°); settling ≤ 5 min | week3 · §14.2 COM-005 |
| COM-013 card: `MOMENTUM_DUMP_INITIATE` | week3 · §14.2 COM-013 |
| NOM-007 card: entry *"Any reaction wheel approaches 7500 rpm"*; `MOMENTUM_DUMP_INITIATE` **(S8 func_id 42)**; exit *"All wheels < 5000 rpm"* | week3 · §14.3 NOM-007 |
| S12 summary: AOCS 11 rules, *"rate > 0.05 °/s (yellow); pointing error > 0.5 ° (red)"*; S19 `aocs_wheel_saturation` *"any wheel > 8000 rpm → initiate desaturation, severity 2"* | week3 · §15.2, §15.3 |
| §16.3 AOCS case study: 0.42° total = RSS(0.18 ST, 0.16 IMU, 0.22 wheel); *"The NADIR budget is 0.5; we'd be at margin 0.04"* | week3 · §16.3 |
| Workbook WB-5.6-C011 step 1 *"AOCS → FINE_POINT (`AOCS_SET_MODE 4`) | mode = 4"* | workbook · WB-5.6-C011 |

### PROPULSION / orbit control
| Item | Source |
|---|---|
| **NOM-004 "Orbit Maintenance Manoeuvre Planning (passive)"** — FDS ΔV plan → FD authorise → *"upload time-tagged manoeuvre commands"* (S11); deviation *"Propulsion health flag set → defer manoeuvre; CTG-006 propulsion sub-procedure"* | week3 · §14.3 NOM-004 |
| **NOM-005 "Orbit Maintenance Manoeuvre Execution"** — step 2 *"Propulsion: arm thrusters per plan | `PROP_ARM` | Arm flag set"*; step 3 S11 burn; step 4 *"capture post-burn state vector … Delta-V vs plan"* | week3 · §14.3 NOM-005 |
| **CTG-006 card** — step 1 *"read all valve / pressure / temperature TM (read SID 5 / propulsion)"*; step 2 *"`PROP_ISO_CLOSE` | Both branches"*; step 3 *"compute remaining usable delta-V envelope"* | week3 · §14.4 CTG-006 |
| LEOP-007 card step 6: *"EPS: enable propulsion heaters (if applicable) | `htr_prop` ON"* | week3 · §14.1 LEOP-007 |
| `HEATER_THRUSTER` (func 42) taught as a live operator command | week3 · §6.5, §7.7 Table 7.6d, §15.5 |
| §16.8 FDS case study — *"Burns propellant unnecessarily… Saves a manoeuvre, saves propellant"* | week3 · §16.8 |
| Part 1 design content (legitimately design-side): ΔV need table (station-keeping 1–10 m/s/yr, deorbit 50–150 m/s), Tsiolkovsky, 5-technology propellant-mass trade (cold gas 1.06 kg / monoprop 0.251 kg / FEEP 0.048 kg for ΔV = 113 m/s, m_dry 5 kg), Hohmann deorbit ΔV ≈ 113 m/s from 600 km | facilitator · §3.4.6 "Propulsion System Design", §2.3.5, App C.3 |
| Propulsion Engineer position card (owns ΔV budget, Isp, plume) | facilitator · App A "Propulsion Engineer" |

### POWER (EPS)
| Item | Source |
|---|---|
| *"the platform idles at **70 W**"*; *"The deployable solar arrays generate **28 W at start-of-life** in the lit portion of the orbit, with a **120 Whr Li-ion battery** (4S configuration, ~27 V nominal)"* | week3 · §5.7 |
| Table 6.2 EPS limits: SoC 70–95 % nominal / **<60 % yellow / <40 % red (load shed)**; `bat_voltage` 27–29 / <27.0 / <26.5; `bus_voltage` 27–29 / <27.0 / **<25.0**; `sa_a_current`/`sa_b_current` **1.5–2.5 A (lit)** / <0.5 / <0.1; `load_current` 1.0–2.2 / >2.5 / >2.8; `bat_temp` 0–25 / <−5 or >30 / <−10 or >45; **"Per-line current (8 lines)"** | week3 · §6.4 |
| EPS func table 16–25 | week3 · §7.7 Table 7.6b |
| **Eclipse-exit SoC exercise**: *"Eclipse entry SoC = 85 %; P_avg = 70 W (the simulator's idle load); t_eclipse = 35 min = 0.583 h; E_battery = 120 Whr … SoC_exit = 85 % − 34.0 % = 51.0 %"*; *"Eclipse-exit SoC should land in 45–70 %… < 30 % triggers FDIR"* | week3 · §13.3 |
| LEOP-007 power-line sequence naming **`htr_bat`, `aocs_imu`, `htr_prop`, `ttc_tx_b`, `obdh_mm`, `aocs_sensors`, `aocs_wheels`, `htr_payload`**; *"enable battery heater | `htr_bat` ON (S8 func_id 25)"*; gate *"bus voltage drops below 26.5 V during enable → load shed"* | week3 · §14.1 LEOP-007 |
| CTG-001 entry *"FDIR rule `eps_bus_undervoltage_payload_off` fires, or `eps.bus_voltage` < 26.5 V for > 30 s"*; `EPS_EMERGENCY_LOAD_SHED(stage=1/2/3)`; recovery gate *"once V > 27.0 V"* | week3 · §14.4 CTG-001 |
| S19 `eps_overcurrent_payload` *"payload line current > 0.6 A for > 2 s → open payload line, severity 3"* | week3 · §15.3 |
| §16.4 EPS case study: line currents *"sum to 12.8 W. Predicted nominal is 12.2 W"*; *"`htr_payload` duty cycle is at 18 % when predicted 8 %"*; *"load-shed gate at 60 %"* | week3 · §16.4 |
| Part 1 design: SA sizing derivation (η_cell 0.295 TJ GaAs, S = 1361 W/m², f_pack 0.85–0.90, f_cover 0.97, L_d = (1−0.025)ⁿ, L_T = 1 − 0.0019(T−28)); worked 3U example → 0.0479 m², 14.62 W BOL; **CubeSat SA reference table: 6U body-mounted ~12 W, single deployable ~30 W, dual deployable ~48 W**; mode power table (Safe 2.5 / Idle 3.0 / Imaging 10.0 / Downlink 9.5 / Eclipse 3.5 W) → orbit-average 4.41 W | facilitator · §3.1.2 "Solar Array Sizing", §3.1 "CubeSat SA Power Reference", §2.4.4 "Mode-Based Power Budget" |

### THERMAL (TCS)
| Item | Source |
|---|---|
| Table 6.3: `tcs.temp_battery` 0–25 / <−5 or >30 / <−10 or >45; `tcs.temp_payload` −5…+5 / >+10 / >+15; `tcs.temp_obc` 10–30 / <0 or >35 / <−5 or >60; **`tcs.fpa_temp_c` −15…−3 (in use) / >−3 yellow / >0 red**; `tcs.htr_X_duty_cycle` 10–60 % / >80 % / 100 % | week3 · §6.5 |
| Editor's note: *"Real-mission multispectral imagers typically run FPA setpoints near −50 °C (Stirling-class cooler). The EOSAT-1 simulator models a Peltier-class cooler: its `PAYLOAD_COOLER_SETPOINT` is clamped to [−20 °C, 0 °C] and the S12 monitor target band is −15 to −3 °C."* | week3 · §6.5 |
| TCS func table 40–49 | week3 · §7.7 Table 7.6d |
| COM-010 card: goal *"from ambient (~+5 °C) to the simulator's Peltier setpoint floor (≤ −10 °C, target band −15 to −3 °C, hard floor −20 °C) within 90 min"*; `EPS_FPA_COOLER(on=1)` (17) + `PAYLOAD_COOLER_SETPOINT(setpoint_c=-10)` (36); slope *"Target −0.2 °C/min avg"*; hold *"±2 °C of setpoint"* | week3 · §14.2 COM-010 |
| COM-010 walkthrough: *"cooler status ON, power draw 8.5 W"*; eclipse slope −0.2…−0.4 °C/min, day-side −0.05…−0.1 °C/min; 30 min → −4 °C, 60 min → −10 °C, 80 min → −12 °C; anomaly branch *"if it's only 5 W, the cooler is faulted"* | week3 · §11.9 COM-010 |
| COM-002 card: heater cycling *"On/off within ±2 °C of setpoint"* | week3 · §14.2 COM-002 |
| S19 `tcs_payload_zone_redline` *"payload zone > +15 °C → force cooler ON, disable imaging, severity 3"* | week3 · §15.3 |
| Workbook WB-5.6-FPA cool-down sheet, 5-min cadence, *"1:30 setpoint expected by here"* | workbook · WB-5.6-FPA |

### REPORTING
| Artefact | What it must contain | Source |
|---|---|---|
| **Anomaly report form** | Anomaly ID (ANO-2026-042 format), Date/Time UTC, Severity (Green/Yellow/Orange/Red), Affected subsystem, Observation (TM values, trends), Diagnosis, Action taken, Result, Status, Lessons learned | week3 · §5.20.4 Table 5.10; response-time/authority ladder in Table 5.9 |
| **Shift log / mission log** | Every procedure card ends "SPACON: log … (mission log)"; §7 says the "Mark" callout *"auto-stamps shift log"*; NOM-001 step 10 *"entry logged at AOS+0:34"* | week3 · §9.2 callout vocabulary; §14 all cards |
| **NOM-012 handover template (8 items)** | phase + MET; last completed pass; open anomalies *by FDIR rule name + status + recovery procedure step*; open work items; next pass + planned procedures; customer commitments; outstanding GO authorisations; voice-loop tone. Target ≤ 6 min | week3 · §9.5 |
| **Anomaly ticket (paper)** | 3 copies per shift; cause hypothesis; opened *"immediately, not after"* | week3 · §12.2; workbook WB-5.5-AT |
| **Pass criteria / GO-NO-GO scorecards** | per-phase pass criteria tables | week3 · §10.7, §11.7; workbook WB-5.5-GO, WB-5.6-GO |
| **Debrief / hot-wash outputs** | FD reads shift-log highlights; anomaly walkthrough; peer-review readout; lessons-learned register entry | week3 · §10.9; `docs/orbit_north/week3/sim_leop_instructor.md` §Debrief |
| **CFMR (4–6 page final mission review)** | objectives PASS/FAIL + evidence; telemetry summary; anomalies log (date/anomaly/procedure/outcome/root cause); ECSS-E-ST-70-11C clause compliance ticks (5.1–5.5); crew self-assessment | workbook · WB-5.7-FR |
| **Long-term trend review (NOM-011)** | weekly trend plots from MCS archive; *"flag any trend that has degraded > 5 % vs commissioning baseline"* | week3 · §14.3 NOM-011 |
| **Editor's note** | *"The procedure cards reference an anomaly-ticket form, a contingency-active banner, and an FD second-key confirmation. **None of these are implemented in the MCS UI.**"* | week3 · §14 preamble |

---

## Divergence register

Classification: **M** matches · **D** diverges · **TO** textbook-only · **SO** sim-only.

### A. Command wire format and identity — the live complaint

| # | Item | Textbook says | Sim does | Cls | Recommended resolution | Who decides |
|---|---|---|---|---|---|---|
| A1 | `PAYLOAD_CAPTURE` (func 28) data bytes | §6.7/§7.7/§15.5: *"with `scene_id` + `lines`"*; catalogue declares `scene_id:uint16, lines:uint16` (4 B) | `tc_catalog.yaml:706-713` declares scene_id+lines; **`service_dispatch.py:838-846` unpacks `lat:float32, lon:float32` (8 B)** and ignores scene_id/lines. With a 4-byte MCS-built packet, lat=lon=0.0 silently | **D** | **Fix the sim** (both halves): pick one wire format and make catalogue, dispatch and MCS builder agree. Engineering merit: a flight EO capture TC carries a target/scene ID *and* a line count (drives readout duration and product size); geolocation belongs in the plan, not the TC. Recommend catalogue format wins; add optional lat/lon as trailing fields. Book unchanged | **User** (format choice) |
| A2 | Commanded `scene_id` | §7.7 `PAYLOAD_SET_SCENE` (27) then `PAYLOAD_CAPTURE(scene_id, …)` | `payload_basic.py:769` auto-assigns `self._next_scene_id += 1` from 101; the commanded scene_id is discarded; `set_scene` writes `current_scene_id` which capture never reads | **D** | Fix sim: honour the commanded scene_id (fall back to auto-assign if 0). This is what makes the book's target-catalogue → scene-ID → downlink-queue chain teachable | Sim team |
| A3 | `PAYLOAD_DOWNLOAD_IMAGE` (29) | §7.7 *"Queue image for downlink"* | `payload_basic.py:803` returns the catalogue entry and does nothing — no queue, no downlink, no state change | **D** | Fix sim: implement a real downlink queue, or delete func 29 and route everything through S13/S15. Book §6.7 already prefers `S13_CREATE_TRANSFER` | Sim team |
| A4 | `IMAGE_ACQUIRE` | §14.2 COM-012 step 3 and workbook WB-5.6-C012 step 2: *"`IMAGE_ACQUIRE` (S8 func_id 64)"*; §14.2 COM-011, §14.3 NOM-002 also use it | No such command. **func 64 = `TTC_SWITCH_REDUNDANT`** — sending it swaps the transponder | **TO** | **Fix the book.** §7.7 already maps `IMAGE_ACQUIRE` → `PAYLOAD_CAPTURE` (28); apply that map in Ch 11/14/16 and the workbook | Courseware |
| A5 | `STAR_TRACKER_ENABLE` | §14.1 LEOP-005 step 2: *"`STAR_TRACKER_ENABLE` (S8 func_id 36)"* | **func 36 = `PAYLOAD_COOLER_SETPOINT`** (float32 °C). Correct is `ST1_POWER` (4)/`ST2_POWER` (5)/`ST_SELECT` (6) | **TO** | Fix the book | Courseware |
| A6 | `MOMENTUM_DUMP_INITIATE` | §14.3 NOM-007 step 2: *"(S8 func_id 42)"*; §14.2 COM-013 step 3 | **func 42 = `HEATER_THRUSTER`**. Correct is `AOCS_DESATURATE` (1) | **TO** | Fix the book | Courseware |
| A7 | `htr_bat` ON as an S8 function | §14.1 LEOP-007 step 3: *"`htr_bat` ON (S8 func_id 25)"* | **func 25 = `EPS_EMERGENCY_LOAD_SHED`** (stage 0–3). Correct is `HEATER_BATTERY` (40) or `EPS_POWER_ON(line=htr_bat)` (19) | **TO** | Fix the book | Courseware |
| A8 | `OBC_BOOT_APP` func id | `docs/orbit_north/week3/student_procedure_quickref.md` LEOP-001 card: *"(S8 func_id 42)"* | func 55. 42 = `HEATER_THRUSTER`. (Ch 14 LEOP-001 card correctly says 55 — the two student artefacts disagree) | **TO** | Fix the quickref | Courseware |
| A9 | Phase transition command | `docs/orbit_north/week3/breakpoint_spec.md`: *"Transition to COMMISSIONING phase (S8 func_id 80)"* | func 80 = `OBC_GPS_TIME_SYNC`. **No phase-transition S8 function exists at all** | **TO** | Either add an S8 phase-set function (instructor-side use is real) or drop the claim from the breakpoint spec | **User** |
| A10 | `STORAGE_REQUEST_DOWNLINK` | §14.2 COM-012 step 5, §14.3 NOM-003 step 2 | Not a command. §7.7 maps it to `S13_CREATE_TRANSFER`→`S13_REPORT_STATUS`; sim also has `S15` dump (store_id 3) | **TO** | Fix the book (§7.7 map already exists) | Courseware |
| A11 | `TX_RATE_SET low/med/high` | §14.2 COM-006 steps 1/3/4, §14.3 NOM-003 step 1, §14.4 CTG-003 step 2 — **three** rates | `TTC_SET_DATA_RATE` (65), one uint8 tier code: **0 = LOW 16 kbps, 1 = HIGH 128 kbps, 0xFF = BEACON 1 kbps (not commandable)**. There is no "medium" | **TO/D** | Fix the book; rewrite COM-006 as a two-tier + beacon test | Courseware |
| A12 | `S19_ENABLE_RULE` / `S19_DISABLE_RULE` | §14.2 COM-008 step 2 (~12 rules, one TC each); §14.1 LEOP-007 entry | Real names are `EA_ENABLE` (S19.4) / `EA_DISABLE` (S19.5) — §7.7 says so; Ch 14 does not | **TO** | Fix the book | Courseware |
| A13 | `OBDH_WD_KICK`, `S6_MEM_READ` | §14.2 COM-007 steps 1, 3 | No watchdog-kick command exists (§7.7 admits this); memory read is `MEM_DUMP` (S6.5) | **TO** | Fix the book; rewrite COM-007 around `OBC_WATCHDOG_ENABLE`(59)/`OBC_SET_WATCHDOG_PERIOD`(58) + `MEM_DUMP`/`MEM_CHECK` | Courseware |
| A14 | `EPS_CELL_BAL`, `MM_MARK_BAD`, `OBC_COLD_REBOOT`, `AOCS_EST_SET`, `S11_UPDATE`, `WHEEL_DISABLE`, `PAYLOAD_POWER_OFF`, `EPS_LOAD_SHED 3`, `PARAM_SET` | §14.4 CTG-005/-013/-015/-016, §14.5 emergency cards, §14.2 COM-003 | None exist. Real: `PAYLOAD_MARK_BAD_SEGMENT` (31), `OBC_REBOOT` (52), `AOCS_DISABLE_WHEEL` (2), `EPS_POWER_OFF` (20), `EPS_EMERGENCY_LOAD_SHED` (25), `SET_PARAM` (S20.3). No cell-balance or estimator-select command | **TO** | Fix the book for the mapped ones; **cell balancing is a genuine gap** — see J3 | Courseware / **User** for cell-balance |
| A15 | `AOCS_SET_MODE_n` mnemonic form | §14.1/§14.2/§14.4 use `AOCS_SET_MODE_2/_3/_4/_5`; §11.9 uses `AOCS_SET_MODE 5` | Single command `AOCS_SET_MODE` (func 0) with a uint8 mode field | **D (cosmetic)** | Fix the book — normalise to `AOCS_SET_MODE(mode=n)` | Courseware |
| A16 | Mode name "SAFE_POINT" / "NOMINAL_POINT" | §14.1 LEOP-005, §14.2 COM-005 | Sim enum (`aocs/aocs_mode_codes.yaml`, canonical): 3 = COARSE_SUN, 4 = NOMINAL, 5 = FINE_POINT. §7.7 gets this right | **D** | Fix the book (Ch 14 only) | Courseware |
| A17 | Func-id range | §7.7: *"defines S8 function IDs 0–82 … plus a legacy block (100–107)"* | 0–84: **79 `TTC_SET_DOWNLINK_MODE`**, **83 `EPS_SET_MODE`**, **84 `AOCS_GPS_SET_START_MODE`** exist and are not in the book | **SO** | Add all three to §7.7/§15.5. 79 is load-bearing (gate on the HIGH tier) | Courseware |
| A18 | S2 device-access commands | §7.6 mentions S2 exists; no command list anywhere | **47 S2 device commands** (EPS 6, AOCS 14, TCS 12, TTC 5, OBDH 5, payload 5 + `S2_REPORT_STATUS`), device IDs 0x0100–0x0604 | **SO** | Add a §15.5 S2 table — this is the ground's only per-device on/off path | Courseware |
| A19 | S6/S20 AOCS torque gains | Not mentioned | `AOCS_SET_TORQUE_GAIN_X/Y/Z`, `AOCS_SET_TORQUE_GAINS_ALL`, `AOCS_DUMP_TORQUE_GAINS` (S6.2/6.5) — and they really change the control law (`aocs_basic.py::_commanded_control_torque`) | **SO** | Add to the AOCS position brief; this is the only tuning lever the sim offers | Courseware |
| A20 | Command metadata | Ch 6 assigns commanding authority per position; §7.8 teaches dual-key by criticality | `tc_catalog.yaml` carries `position:` on **10 of ~200** commands and `criticality:` on **9**. MCS cannot filter or gate | **TO** | **Fix the sim** — populate `position:` and `criticality:` across the catalogue; it is the cheapest way to make the book's authority model real | Sim team |

### B. Payload / imaging numbers

| # | Item | Textbook says | Sim does | Cls | Resolution | Who |
|---|---|---|---|---|---|---|
| B1 | Payload store size | §14.2 COM-009 / §11.9: *"total 2 GB, used 0 MB"* | `subsystems/payload.yaml total_storage_mb: 20000.0` (20 GB); `PayloadState.store_used_pct = 20.0`, `mem_used_mb = 4000.0` — **starts 20 % full** | **D** | **Fix the book** (20 GB is a deliberate sim decision, defect #82: keep downlink, not storage, binding). Book must also stop saying "used 0 MB" | Courseware |
| B2 | Payload line current | §11.9 COM-009: *"0.47 A, within 0.4 to 0.6 envelope"*, trip at 1.0 A; §15.3 S19 rule at *"> 0.6 A for > 2 s"* | `eps.yaml payload_standby_power_w: 8.0` → **0.29 A at 28 V**; imaging 45 W → **1.61 A**. OC trip `simulation.yaml eps.overcurrent.thresholds_a.payload: 2.5` at **50 ms**; `mcs/limits.yaml 0x011B` yellow 2.2 / red 2.5 | **D** | **Fix the book** — sim numbers are self-consistent with the hardware sheet. Book envelope should be 0.25–0.35 A standby, 1.5–1.7 A imaging, trip 2.5 A | Courseware |
| B3 | FPA cooler power | §11.9 COM-010: *"power draw 8.5 W"*, fault threshold *"only 5 W"* | `eps.yaml fpa_cooler_power_w: 15.0` = `payload.yaml fpa_cooler_power_w: 15.0` = `tcs`. Sim manual `06_payload.md §8` says *"10–15 W"* | **D** | Fix the book to 15 W; fix sim manual §8 to 15 W | Courseware + sim docs |
| B4 | Payload power in IMAGING | (book quotes no total) | `eps.yaml payload_power_w: 45.0` + cooler 15 = 60 W; **sim's own manual `06_payload.md §8.1` says "approximately 25 W"** | **D (sim-internal)** | Fix sim manual | Sim team |
| B5 | Image size / dump time | §8.5: *"4 MB per imaging session, downlink at 1 Mbps → 32 s"*; §11.9 COM-012 *"4.2 MB … ~32 seconds"* and *"32 seconds elapsed, 4.2 MB received"* | `payload.yaml image_size_mb: 4.0` ✅ but HIGH tier = **128 000 bps** → **262 s** (328 s with the planner's 20 % protocol allowance, `activity_types.yaml data_dump duration_s: 330`) | **M** on size, **D** on time (8×) | **Fix the book.** The 1 Mbps figure predates the three-rung ladder and breaks the entire pass-planning lesson (an image dump is 74 % of an average pass, not 5 %) | Courseware |
| B6 | Ground swath | §11.9 COM-012: *"ground swath 240 km"* | `payload_basic.py:458-464`: ifov 20 µrad × 5000 px → at 450 km **swath 45 km, GSD 9.0 m**; `PayloadState.swath_width_km` default 30 | **D** (5×) | **User call.** 240 km swath at 4 MB/frame and 10 m GSD is not self-consistent. Either the book's swath is wrong, or the sim's detector geometry is under-scaled for an ocean-colour mission | **User** |
| B7 | Integration times | §11.9 COM-012: *"integration times 8/12/16/20 ms"* for 443/560/665/865 | `PAYLOAD_SET_INTEGRATION_TIME` (34) accepts 4 float32 with **no validation** (`payload_basic.py:872-882`); defaults 2.0 ms/band; **integration time has zero effect** on SNR, DN, saturation or product size | **TO** | **Fix the sim** if the saturation lesson is to survive (see B8); otherwise book must stop teaching a consequence the sim cannot produce | **User** |
| B8 | Saturation / DN | §11.9 COM-012 failure branch: *"mean DN at 2900 in band 443 (oversaturated) — integration time was too long"*; §11.9 COM-011 *"mean DN 32, below threshold of 50"*; WB-5.6-FL asks for "Mean DN per band" | **No DN telemetry exists.** Sim models per-band **SNR in dB** (`band_snrs` 40/50/48/42 nominal, params 0x0620–0x0623) and `last_scene_quality` (%) | **TO** | Either add a radiometric DN model tied to integration time × gain × scene radiance (**L**), or rewrite the calibration/First-Light assessment around SNR and `last_scene_quality`. Recommend the rewrite — SNR is the physically honest observable the sim already computes | **User** |
| B9 | Geo-registration | §11.9 COM-012: *"geo-registration error 87 m (within 100 m budget)"*; WB-5.6-FL field | No geolocation error model; capture stores commanded lat/lon verbatim | **TO** | Book: mark as paper/ground-tool exercise, or derive from att_error × altitude (0.1° × 450 km = 785 m — which would blow the 100 m budget, so the numbers need re-deriving either way) | **User** |
| B10 | Payload mode before capture | §14.2 COM-012 / §14.3 NOM-002 go straight from COM-009's `PAYLOAD_SET_MODE(mode=1)` (STANDBY) to `IMAGE_ACQUIRE` — **no step ever commands mode 2** | `payload_basic.py:729-735`: capture returns `{"success": False, "message": "Not in IMAGING mode"}` unless `mode == 2`, and `{"FPA not ready"}` unless `fpa_ready`. `activity_types.yaml imaging_pass` does it correctly (26→mode 2, then 28) | **D — the book's First Light sequence cannot execute** | **Fix the book.** Insert `PAYLOAD_SET_MODE(mode=2)` before capture and `(mode=1)` after, per `manual/06_payload.md §7.1` | Courseware |
| B11 | TM mnemonics | Table 6.5 uses `payload.fpa_cooler_status`, `payload.last_image_quality` | Sim has `tcs.cooler_fpa` (0x040C) / `payload.cooler_pwr` (0x0602), and `payload.last_scene_quality` (0x060D). No `last_image_quality` | **D** | Fix the book | Courseware |
| B12 | Compression | §7.7/§15.5 list `PAYLOAD_SET_COMPRESSION`; no teaching of ratio behaviour | Sim models **scene-entropy-dependent CCSDS-123, ratio 1.5–4.0** (`payload_basic.py:498-513`), telemetered at 0x0614; catalogue's func 39 has only `ratio`, so the `algorithm` field the model supports is unreachable | **SO** (partly) | Book: teach it — it is the reason stored size ≠ raw size. Sim: add `algorithm` to the func-39 field list | Both |

### C. Attitude numbers

| # | Item | Textbook | Sim | Cls | Resolution | Who |
|---|---|---|---|---|---|---|
| C1 | Wheel inertia | §13.7: *"I_wheel = 1.5 × 10⁻⁵ kg·m²"*, H_max = 7.85 × 10⁻³ N·m·s | `aocs_basic.py:222` `_rw_inertia = 0.005` kg·m² → H_max = **2.88 N·m·s per wheel** | **D — 333×** | **Fix both.** 1.5e-5 kg·m² is a nanosat MEMS figure, 0.005 kg·m² is a 200-mm-class wheel — neither is right for a 6U (a 6U RW is ~1e-4…5e-4 kg·m²). Pick one number, put it in `aocs.yaml` as `inertia_kgm2`, and re-derive §13.7 from it | **User** |
| C2 | Wheel max speed | §13.7 *"ω_max = 5000 rpm"*; §14.3 NOM-007 entry *"approaches 7500 rpm"*, exit *"< 5000 rpm"*; §15.3 S19 *"any wheel > 8000 rpm"* — **three different limits in one book** | `aocs.yaml max_speed_rpm: 5500` (hard clamp); S12 warn ±5000; `mcs/limits.yaml 0x0207` yellow ±5000, red ±5500 | **D + internally inconsistent** | **Fix the book** to 5500 max / 5000 warn; a 7500-rpm entry criterion is unreachable | Courseware |
| C3 | Desat trigger & action | §15.3: `aocs_wheel_saturation` > 8000 rpm → *"initiate desaturation"*, severity 2 | Two different rules: `aocs_wheel_N_overspeed_disable` (0x0207–0x020A) → **func 2 = disable that wheel**; `aocs_momentum_saturation_desaturate` (0x025B) → func 1 desaturate. **The momentum monitor's limit is ±90 N·m·s while achievable total momentum is 11.5 N·m·s — it can never fire** | **D + sim bug** | **Fix the sim**: set the 0x025B S12 limit to a reachable fraction of achievable momentum (`mcs/limits.yaml` already uses 0.5/0.8 — the two files disagree). Fix the book's rule names | Both |
| C4 | Pointing spec | §5.7: *"< 0.5° (3σ) in NADIR"*, achievable *"0.32° (3σ)"*; §13.4 budget components ST 0.18 / IMU 0.16 / wheel 0.22 / bias 0.05; §14 cards gate FINE_POINT at < 0.1° | Sim has **no pointing spec and no per-component budget**. `aocs_basic.py:753,786`: NOMINAL att_error = quaternion error + N(0, 0.002°); FINE_POINT + N(0, 0.0005°) — converges to ~0.00x°, so **0.18°/0.32°/0.42° never occur in nominal runs**. `mcs/limits.yaml 0x0217`: yellow ±1.0°, red ±2.0° | **D + TO** | **Fix both.** Sim: inject a realistic pointing-error budget (ST bias, gyro drift, wheel jitter) so att_error sits near 0.2–0.4° and the RSS exercise has a referent — this is a small, high-value change. Book: align its limit table with `mcs/limits.yaml` or get the sim changed | **User** |
| C5 | Per-component TM | §13.4 and §16.3 require the operator to *"decompose the current pointing error into its components on the AOCS tab"* | `parameters.yaml` has only `aocs.att_error` (0x0217). No ST/IMU/wheel/bias contributions exist | **TO** | Sim: publish the components as TM if C4 is implemented; otherwise mark §13.4 as a paper exercise | **User** |
| C6 | Desat duration/mechanism | §13.7: *"The desat takes 30–40 minutes and uses magnetorquers"*; eclipse-side preferred | `aocs_basic.py::_tick_desat` ramps wheels toward `desaturation_speed_rpm: 200` and holds att_error ≈ 0.5°; no magnetic-field-dependence, so eclipse-side vs lit makes no difference | **D (partial)** | Book: keep the physics rationale, add a caveat that the sim does not model the B-field dependence. Sim: low priority | Courseware |
| C7 | Body rates | §14.1 LEOP-005 rates < 0.05 °/s; §5.7 detumble < 0.5 °/s | `mcs/limits.yaml 0x0204-0206` yellow ±0.5 °/s, red ±2.0 °/s; `aocs.yaml attitude_error_deadband_deg: 0.01` | **M** (thresholds are operational, not limits) | None | — |
| C8 | Star tracker count | §5.7: *"a **single-head** star tracker"* | Two: `ST1_POWER` (4), `ST2_POWER` (5), `ST_SELECT` (6), S2 devices 0x0205/0x0206, and CTG-008 depends on switching to the backup | **D** | Fix the book §5.7 | Courseware |
| C9 | Workbook mode number | WB-5.6-C011: *"AOCS → FINE_POINT (`AOCS_SET_MODE 4`) | mode = 4"* | FINE_POINT = 5 | **D** | Fix the workbook | Courseware |
| C10 | §13.8 arithmetic | *"Z wheel at 4200 rpm, 70 %"* | 4200/5000 = **84 %**, and 4200/5500 = 76 % | **Book arithmetic error** | Fix the book | Courseware |
| C11 | §13.7 spacecraft | *"disturbance torque on a 3U at 500 km"* | EOSAT-1 is a **6U at 450 km** (`mission.yaml`, `orbit.yaml`) | **D** | Fix the book | Courseware |

### D. Propulsion

| # | Item | Textbook | Sim | Cls | Resolution | Who |
|---|---|---|---|---|---|---|
| D1 | NOM-004 Orbit Maintenance Planning | Full card, S11 upload of manoeuvre commands | **No such procedure.** `procedures/procedure_index.yaml` has no NOM-004 at all | **TO** | See D5 | **User** |
| D2 | NOM-005 Orbit Maintenance Execution + `PROP_ARM` | Full card | **`PROP_ARM` has zero hits repo-wide.** Sim NOM-005 = *"HK Configuration"* | **TO** | See D5 | **User** |
| D3 | CTG-006 propulsion contingency + `PROP_ISO_CLOSE` | Valve/pressure TM, isolation, remaining-ΔV envelope | Sim CTG-006 = *"Payload Anomaly"*. No propulsion TM, no valve commands | **TO** | See D5 | **User** |
| D4 | `htr_prop` power line | LEOP-007 step 6 | Power lines are exactly: `obc, ttc_rx, ttc_tx, payload, fpa_cooler, htr_bat, htr_obc, aocs_wheels` (`eps.yaml power_lines`) | **TO** | Fix the book | Courseware |
| D5 | Does EOSAT-1 have propulsion? | Book is self-contradictory: §5.7's 600-word spacecraft description omits propulsion; §14.3/§14.4 give it thrusters; §16.8 has FDS "saving propellant"; `HEATER_THRUSTER` (42) is taught as a live command | **The sim says no, three times, in writing**: `manual/02_aocs.md:16` *"does not perform orbital control manoeuvres — EOSAT-1 has no propulsion system"*; `manual/07_orbit_ops.md:170,174,188` *"The mission has no thruster system"*; `manual/03_tcs.md:64-68` *"there is no propulsion system on EOSAT-1, so no thruster heater is required"* — **yet `tc_catalog.yaml:805` still ships `HEATER_THRUSTER` (42) and circuit code `2=thruster`, and `parameters.yaml` 0x0409 `tcs.temp_thruster` still exists** | **TO + sim-internal contradiction** | **Judgement call.** Three coherent options: **(a)** declare EOSAT-1 propulsion-free — delete NOM-004/-005, rewrite CTG-006 as the sim's Payload Anomaly, retire `HEATER_THRUSTER`/0x0409/circuit 2 from the sim, and re-teach orbit maintenance as *"why a 450 km 6U doesn't need it"* using the book's own §2.3.5 decay table (450 km decays inside the FCC 5-year rule); **(b)** add a minimal cold-gas model to the sim (state: prop mass, tank P/T, valve state, arm flag; commands: `PROP_ARM`, `PROP_DISARM`, `PROP_BURN(duration, direction)`, `PROP_ISO_CLOSE`; TM: 0x0700 block) plus a ΔV-vs-drag exercise — makes NOM-004/-005 real and gives the FDS position genuine work; **(c)** keep the procedures as **paper/ground-tool** exercises explicitly flagged "not executable in the simulator". My recommendation on engineering merit: **(a) for the spacecraft, (c) for the pedagogy** — a 6U at 450 km carrying propulsion is an unusual design choice and the book already teaches (§3.4 "When NO Propulsion is Needed": *"Orbit < 500 km: Natural atmospheric decay provides FCC 5-year deorbit compliance"*) that this mission shouldn't have it. Option (b) is the most work for the least mission realism | **User — this is the biggest single decision in the register** |

### E. Power

| # | Item | Textbook | Sim | Cls | Resolution | Who |
|---|---|---|---|---|---|---|
| E1 | Solar array output | §5.7: *"generate **28 W** at start-of-life in the lit portion of the orbit"* | `eps.yaml solar_panels`: body 0.06/0.06/0.03/0.03/0.06/0.06 m² + wings **0.300 m² each** (raised from 0.240, power_analysis item 9). Peak ≈ **0.33 m² × 0.295 × 1361 × 0.97 ≈ 128 W** on the Y-face lobe; **orbit-average 46.0–53.8 W** depending on eclipse/sweep alignment (`docs/review_2026-07/power_analysis.md §1`). `eps_basic.py` `power_gen_w` default 252 W | **D — book understates by 1.6–4.6×** | **Feed to the array-resizing agent, don't fix blindly.** The book's own reference table (facilitator §3.1 "CubeSat SA Power Reference") gives **6U dual deployable ≈ 48 W BOL** — which is exactly the sim's orbit-average, so the **sim's current array is defensible and the book's 28 W is the outlier**. Any resize must keep orbit-average ≥ the P2-imaging load (~53 W commanded) and should land in the 45–55 W band the book itself calls realistic | **User / array agent** |
| E2 | Platform idle | §5.7 and §13.3: *"the platform idles at **70 W**"*, used as `P_avg` in the eclipse exercise | `eps.yaml platform_idle_power_w: 70.0` is **read into `_platform_idle_w` (`eps_basic.py:210`) and never added to `cons_w`** (`:439-516`). Actual billed platform = obc 15 + ttc_rx 5 = **20 W bare**, **36 W** with heaters (6+4) and wheels (6). `power_analysis.md` calls it *"dead config, delete or wire"* | **D — the book's single most-used number is fiction** | **Fix both.** Sim: delete `platform_idle_power_w` (the line model is the truth). Book: re-derive §13.3 on 36 W. Worked example becomes SoC_exit = 85 − (36 × 0.583/120)×100 = **67.5 %**, not 51.0 % — which changes the "typical range 45–70 %" guidance too | Both |
| E3 | Load-shed thresholds | Table 6.2 *"< 40 % (load shed)"*; §16.4 *"load-shed gate at 60 %"*; §14.4 CTG-001 *"bus_voltage < 26.5 V"*, recover *"> 27.0 V"* | Three real layers: in-model bus-V shed at **25.0 V** (≡45.5 % SoC), order `payload→fpa_cooler→ttc_tx→aocs_wheels`, hysteresis 0.5 V, **no auto-restore**; FDIR SoC stages **30/20/10 %** (`simulation.yaml eps.load_shed.soc_stage_thresholds_pct`); `EPS_SET_MODE` (83) safe = shed 2 loads / emergency = shed all | **D — book matches none of the three** | **Fix the book.** The sim's ladder is deliberate and documented (power_analysis §4 items 1 & 6); the book must teach it as three layers, and must teach that shed lines **do not auto-restore** — that is the operationally consequential fact | Courseware |
| E4 | Battery limits | Table 6.2 SoC yellow <60 / red <40; bat_V yellow <27.0 / red <26.5; bat_temp nominal 0–25 | `mcs/limits.yaml`: SoC (0x0101) yellow_low **25**, red_low **15**; bat_V (0x0100) yellow_low **23.0**, red_low **22.0**; bat_temp (0x0102) yellow_low **2.0**, red_low **0.0**. S12: SoC WARNING <20, ALARM <10 | **D — four different SoC ladders across book, MCS, S12 and FDIR** | **Fix the sim first** (single-source SoC alarm points across `mcs/limits.yaml`, `s12_definitions.yaml`, `simulation.yaml`), then transcribe into the book | **User** (choose the ladder), then both |
| E5 | Power-line names & count | Table 6.2 *"Per-line current (8 lines)"* ✅; LEOP-007 names `aocs_imu`, `ttc_tx_b`, `obdh_mm`, `aocs_sensors`, `htr_payload`, `htr_prop` | 8 lines ✅ but named `obc, ttc_rx, ttc_tx, payload, fpa_cooler, htr_bat, htr_obc, aocs_wheels`. `obc` and `ttc_rx` are **non-switchable** | **D** | Fix the book — LEOP-007 has 16 steps of which ~6 command lines that don't exist | Courseware |
| E6 | SA current | Table 6.2 `sa_a_current`/`sa_b_current` *"1.5–2.5 A (lit)"* | Per-face currents are computed as `(gen_w × area/total_area)/28.0` (`eps_basic.py:390`); with ~128 W peak across 8 faces the Y-wing faces dominate at ~2 A each and the ±X faces sit near 0.2 A — the book's single band applies to at most two faces | **D (partial)** | Book: express as per-face expectations, or as total array current | Courseware |
| E7 | Bus voltage limits | Table 6.2 bus nominal 27–29, yellow <27.0, red **<25.0** | `mcs/limits.yaml 0x0105` yellow_low 27.0 ✅, red_low **26.5**; `simulation.yaml` shed at 25.0; `fdir.yaml` bus rules at 26/24 V | **D** | Same as E4 — single-source it | **User** |
| E8 | S19 overcurrent rule | §15.3 `eps_overcurrent_payload` *"> 0.6 A for > 2 s"* | No rule of that name. Overcurrent is a *model* trip at 2.5 A / 50 ms, not an S19 rule | **TO** | Fix the book | Courseware |
| E9 | Part 1 vs Part 2 power scale | facilitator §2.4.4: total spacecraft **10.0 W in imaging**, orbit-average **4.41 W** | EOSAT-1: payload alone 45 W, cooler 15 W, TX 12 W | **Pedagogical incoherence, not a defect** | Add one bridging paragraph in §5.7: Part 1 teaches a realistic 3U; EOSAT-1 is deliberately scaled up. The book already half-says this — make it explicit and consistent | Courseware |

### F. Thermal

| # | Item | Textbook | Sim | Cls | Resolution | Who |
|---|---|---|---|---|---|---|
| F1 | **FPA limits** | Tables 6.3 & 6.5: nominal **−15 to −3 °C**, yellow **> −3**, red **> 0** | `mcs/limits.yaml` 0x0408 and 0x0601: **yellow −18/+8, red −20/+12**. `tcs.yaml event_thresholds.fpa`: warning [−50, −5], alarm [−60, 0]. `manual/06_payload.md §6.1`: *"−18 to −12 deg C: Optimal imaging range"*, yellow >8, red >12. `activity_types.yaml imaging_pass`: `fpa_temp_min_c: −50, fpa_temp_max_c: −5`, precondition `payload.fpa_temp_c < −3.0` | **D — four incompatible bands, one of them inside the sim** | **Fix the sim first, then the book.** An operator following the book calls RED at 0 °C while the MCS shows GREEN until +8 °C. Single-source the FPA band and propagate to `mcs/limits.yaml`, `tcs.yaml`, `manual/06_payload.md`, `activity_types.yaml`, then the book. Recommend the **manual's −18…−12 optimal / −20…+8 caution** as the base since it matches `mcs/limits.yaml` | **User** (pick the band), then both |
| F2 | FPA readiness | COM-010 sets setpoint **−10 °C** then declares success at *"−12 C, in the −15 to −3 C target band"* | `payload_basic.py:397`: `fpa_ready` requires `(setpoint − 1.0) ≤ fpa_temp ≤ (setpoint + 5.0)` held for **60 s**. With setpoint −10 the window is **[−11, −5]** — so **−12 °C sets `fpa_ready = FALSE` and the next `PAYLOAD_CAPTURE` is rejected "FPA not ready"** | **D — the book's COM-010 exit state blocks COM-012** | **Fix the book**: either command setpoint −15 (default, window [−16, −10]) and declare success at −12 °C, or keep −10 and declare success at −8 °C. Also teach `payload.fpa_ready` (0x0610) as the actual gate | Courseware |
| F3 | Cool-down time | §11.9 COM-010: *"60–90 minutes"* ambient +5 → −10; slope −0.2…−0.4 °C/min eclipse, −0.05…−0.1 day-side | `payload.yaml fpa_tau_cooling_s: 100.0` first-order to target: +5 → −10 is 15 K of a 20 K step, ≈ 1.39 τ ≈ **140 s**, i.e. **~2.5 minutes, not 90**. No eclipse/day-side dependence in the FPA model at all | **D — 35×** | **Fix the sim.** A 100 s time constant makes COM-010 (a card the whole commissioning day is built around) a 3-minute non-event. τ ≈ 1500–2000 s gives the book's 60–90 min. Add a day-side/eclipse term to justify the slope split | **User** (τ value), sim team |
| F4 | Heater setpoints & power | §14.2 COM-002 *"±2 °C of setpoint"*; Table 6.3 duty 10–60 % nominal, >80 % yellow | `tcs.yaml heaters`: battery 6.0 W on 1.0 °C / off 5.0 °C; obc 4.0 W on 5.0 / off 10.0. Band is **4–5 °C wide, not ±2**. `mcs/limits.yaml 0x0411` total heater power yellow 15 W / red 18 W — but installed heater power is only 10 W, so it can never trip | **D + sim bug** | Book: quote the real thermostat bands. Sim: fix the 0x0411 limits | Both |
| F5 | Heater circuits | §6.5 and §7.7 teach three circuits (battery/OBC/thruster); Table 6.3 has `tcs.temp_payload` and `htr_payload` | `tcs.yaml heaters` has **two** (battery, obc). `manual/03_tcs.md:64` *"EOSAT-1 does not carry OBC or thruster heater…"* (itself contradicted by `tcs.yaml`, which does have an OBC heater). No payload heater; no `tcs.temp_payload` parameter | **D + sim-internal** | Book: two circuits. Sim: fix `manual/03_tcs.md` and retire circuit 2 (see D5) | Both |
| F6 | Decontamination | §7.7 lists `TCS_DECONTAMINATION_START` (47) *"typically +50 C"*; never taught | Implemented; `tcs.decontamination_active` (0x0418) telemetered; scenario `fpa_overtemp.yaml` exists | **SO** | Book: add a decontamination card — it is a real EO ops activity and free content | Courseware |
| F7 | S19 rule | §15.3 `tcs_payload_zone_redline` *"payload zone > +15 °C → force cooler ON, disable imaging"* | Real rule is `tcs_fpa_overtemp_payload_off` on **0x0408**, action func 20 (payload mode OFF). No "payload zone" parameter, no "force cooler ON" action | **TO** | Fix the book | Courseware |

### G. TT&C / data (context for payload downlink)

| # | Item | Textbook | Sim | Cls | Resolution | Who |
|---|---|---|---|---|---|---|
| G1 | Data-rate ladder | §7.7 Table 7.6f and §15.5: *"`TTC_SET_DATA_RATE` | 0 = 1 kbps low, 1 = 64 kbps high"* | **LOW 16 kbps BPSK conv+RS (routine), HIGH 128 kbps QPSK RS (dumps only, gated on antenna + DOWNLINK mode + elevation ≥ 10°), BEACON 1 kbps (bootloader only)** — `subsystems/ttc.yaml tm_rate_tiers` | **D** | **Fix the book.** Also add func 79 `TTC_SET_DOWNLINK_MODE` — without it the HIGH tier is rejected with S1.8 error 0x0007, so NOM-003 as written fails | Courseware |
| G2 | Ground segment | §5.7: *"two-station… Iqaluit… four passes per day… Troll… three passes per day"*; §8.2.1 *"~14 passes per 24 h"* | **Five stations** (`orbit.yaml`): Iqaluit, Troll, Svalbard, Inuvik, O'Higgins. Measured **103 passes / 48 h, median gap 33 min, max 79 min** (≈51/day) | **D — 3.6×** | **User call.** Five stations is a deliberate sim decision with published contact statistics; two stations is the course's operational story. If the course wants two, set `orbit.yaml` to two and re-run `pass_gap_analysis.py`; if five, rewrite §5.7/§8.2.1 and every pass-planning number that depends on contact time | **User** |
| G3 | Beacon band | §5.7: *"a UHF whip for the bootloader beacon"* | `subsystems/ttc.yaml` header: S-band only; *"The previous UHF values (449.0 / 401.5 MHz) agreed with nothing else in the repo"* | **D** | Fix the book | Courseware |
| G4 | COM-006 link margins | Card: low-rate margin *"> 8 dB"*, high-rate BER *"< 1e-6"*, three rates | `tools/link_budget_ladder.py` exists to generate the real per-tier, per-station table | **D** | Fix the book from the tool's output | Courseware |

### H. Procedure identity

| # | Item | Textbook | Sim (`procedure_index.yaml`) | Cls | Resolution | Who |
|---|---|---|---|---|---|---|
| H1 | **NOM-004 … NOM-011 are renumbered** | 004 Orbit Manoeuvre Planning · 005 Orbit Manoeuvre Execution · 006 Eclipse Entry/Exit · 007 Momentum Mgmt · 008 Pass Plan Upload · 009 Routine Health Check · 010 SW/Param Update · 011 Long-Term Trend Review | (no 004) · 005 HK Configuration · 006 Software Upload · 007 Momentum Management ✅ · 008 Clock Synchronisation · 009 Routine Health Check ✅ · 010 Eclipse Transition · 011 Data Rate Change | **D — 6 of 12 nominal IDs mean different things** | **Fix the book** — procedure IDs are called on the voice loop; a mismatch here is a live safety issue in training. Note the book's §14.6 claims *"Nominal cleanup procedures: NOM-008 onward all carded above; no further placeholders"*, which is false | Courseware |
| H2 | Procedure count | §14 preamble: *"The 64 procedures in the simulator split as 7 LEOP, 13 commissioning, 12 nominal, 27 contingency, 6 emergency"* | 7 + 13 + 11 (no NOM-004) + 27 + 6 = **64** ✅ but nominal is 11, not 12 | **D (minor)** | Fix the book | Courseware |
| H3 | COM-004 | §14.6: *"COM-004 (Magnetometer calibration — folded into COM-003) — placeholder"* | COM-004 = **AOCS Actuator Checkout**, a real procedure with a real file | **D** | Fix the book; card COM-004 | Courseware |
| H4 | CTG-006 | Card is a propulsion contingency | CTG-006 = **Payload Anomaly** | **D** | See D5 | **User** |
| H5 | Runnable procedures | Book never mentions that some procedures are machine-executable | `procedure_index.yaml` `runnable:` links exist for NOM-002 (activity_type `imaging_pass`), NOM-003 (`data_dump`), NOM-008 (markdown adapter), NOM-009 (`housekeeping_collection`) | **SO** | Book: teach the Procedures-tab runner and the three `runnable:` kinds — this is a real MCS capability the course never uses | Courseware |

### I. Telemetry / monitoring reference

| # | Item | Textbook | Sim | Cls | Resolution | Who |
|---|---|---|---|---|---|---|
| I1 | S12 rule count | §15.2 *"38 active in nominal config"* ✅; but §7.6 Table 7.5 says *"The 25 monitor definitions"* | `s12_definitions.yaml`: **38** | **D (internal)** | Fix §7.6 | Courseware |
| I2 | S19 rule names | §15.3 highlights six rules: `eps_overcurrent_payload`, `aocs_wheel_saturation`, `tcs_payload_zone_redline`, `ttc_link_loss`, `obc_watchdog_event`, `payload_image_corrupt`. §9.5 cites `eps_payload_load_yellow` | **None of these seven names exist.** The 26 real names are listed in `docs/SIM_SWEEP_REPORT.md` Task 2 and `s19_rules.yaml`. (§14.4 CTG-001's `eps_bus_undervoltage_payload_off` is the one correct citation in the book) | **TO** | **Fix the book** — replace the §15.3 table with six real rules and their real actions. NOM-012 handover requires operators to *"list each by FDIR rule name"*, so fictional names break the handover drill | Courseware |
| I3 | S19 rule count | §15.3 *"26 active"* ✅ | 26 ✅ | **M** | — | — |
| I4 | SID map | SIDs 1 EPS, 2 AOCS, 3 TCS, 4 Platform, 5 Payload, 6 TTC, 11 Beacon | Identical (`hk_structures.yaml`) | **M** | — | — |
| I5 | SID cadence | §13.5: *"SID 1 at 4 s, SID 11 beacon at 30 s"* | SID 1 = **1.0 s**, SID 2 = 4.0, SID 3 = 60.0, SID 4 = 8.0, SID 5 = 8.0, SID 6 = 8.0, SID 11 = 30.0 ✅ | **D** | Fix the book; publish the full cadence table (it drives the delayed-TM gap exercise) | Courseware |
| I6 | SID cross-references in cards | Cards say things like *"read all zone temperatures from SID 4 | (read SID 3)"*, *"no FDIR events from SID 1 | (read SID 4)"*, LEOP-007 *"confirm SoC > 60 % (read SID 4)"* | SoC is SID 1; zones are SID 3; platform/FDIR is SID 4 | **D — action text and TC column contradict each other inside single cards** | Fix the book | Courseware |
| I7 | HK enabled by default | §14.1 LEOP-001 expects *"SIDs 1, 3, 4, 6 streaming"* after boot | `simulation.yaml housekeeping.default_enabled_sids: [1, 11]`, `enable_all_by_default: false` — **SIDs 2–6 must be commanded on with `HK_ENABLE` (S3.5)** | **D** | Fix the book: LEOP-001/-002 must include explicit `HK_ENABLE` steps. This is a genuine commissioning lesson the sim now supports and the book skips | Courseware |
| I8 | Mosaic screenshot | §7.1 MCS figure shows `fpa_temp −58 °C` | Cooler setpoint is clamped to ≥ −20 °C; −58 is unreachable | **D** | Regenerate the figure | Courseware |

---

## Per-workstream findings

### 1. PAYLOAD — precise numbers the book prints

| Quantity | Book value (verbatim) | Sim value | Where |
|---|---|---|---|
| Bands | 443 / 560 / 665 / 865 nm | identical (`mission.yaml`, `SPECTRAL_BANDS`) ✅ | §5.7 |
| Integration times | **"8/12/16/20 ms"** (blue/green/red/NIR) | defaults 2.0 ms each; accepted unvalidated; **no effect on anything** | §11.9 COM-012 step 2 |
| Detector gain | not quoted | clamp 0.1–10.0 ✅ matches catalogue | — |
| Cooler setpoint | **"−10 °C"** commanded; clamp "−20…0 °C" ✅ | clamp −20…0 ✅; default target **−15.0**; readiness window = [setpoint−1, setpoint+5] | §14.2 COM-010 step 3 |
| FPA target band | **"−15 to −3 °C"** | MCS yellow −18/+8, red −20/+12; manual optimal −18…−12; planner max −5 | Tables 6.3, 6.5 |
| Cooler power | **"8.5 W"** (fault if "only 5 W") | **15.0 W** | §11.9 COM-010 step 2 |
| Cool-down | **"60–90 minutes"**, −0.2…−0.4 °C/min | τ = 100 s → **~2.5 min** | §11.9 COM-010 |
| Payload standby current | **"0.47 A, within 0.4 to 0.6"**, trip 1.0 A | 8 W / 28 V = **0.29 A**; trip **2.5 A** at 50 ms | §11.9 COM-009 |
| Store | **"total 2 GB, used 0 MB"** | **20 000 MB**, starts at **20 % used (4000 MB)** | §11.9 COM-009 step 4 |
| Image size | **"4.2 MB"**, rule of thumb "4 MB" | 4.0 MB raw; **stored size = 4.0 / compression_ratio (1.5–4.0)** → 1.0–2.7 MB | §11.9 COM-012 step 3 |
| Downlink time | **"~32 seconds at 1 Mbps"** | **262 s** at 128 kbps (330 s planned) | §8.5, §11.9 |
| Swath | **"240 km"** | **45 km** at 450 km (ifov 20 µrad × 5000 px) | §11.9 COM-012 |
| GSD | not quoted in Part 2 | 9.0 m (0x0628) | — |
| Mean DN | **"1840 / 2102 / 1654 / 891"**, dark "32", saturation "2900" | **not modelled** — SNR in dB instead (40/50/48/42 nominal) | §11.9 COM-012 step 6 |
| Geo-registration | **"87 m (within 100 m budget)"** | not modelled | §11.9 COM-012 step 6 |
| Flat-field gradient | **"3.2 percent, below 5 percent threshold"** | not modelled; calibration is a 30 s timer with a valid-mask bitfield | §11.9 COM-011 step 3 |
| SNR limits | ">25 dB / 15–25 / <15 dB" | `snr_degraded` event at **< 25.0 dB** ✅ | Table 6.5 |
| Pointing→quality coupling | "> 0.3° at integration → smeared" | `att_quality` = 1.0 to 0.1°, →0.9 at 0.5°, →0.6 at 1.0°, →0.2 at 2.0° | §14.3 NOM-002 |

**The headline payload defect** is A1: the ops card, `tc_catalog.yaml` and the MCS command builder all agree on `PAYLOAD_CAPTURE(scene_id:u16, lines:u16)`; the simulator's dispatcher reads those same bytes as `(lat:f32, lon:f32)`. This is exactly the reported symptom and it is a **simulator** defect, not a book defect. Second headline: the book's First Light never enters IMAGING mode (B10), so even with the bytes fixed the sequence fails.

### 2. ATTITUDE

The book quotes, in order of how load-bearing they are:
- **Requirement:** < 0.5° (3σ) NADIR; achievable 0.32° (3σ) — §5.7.
- **Budget decomposition:** ST 0.18° · IMU 0.16° · wheel imbalance 0.22° · bias 0.05° → RSS 0.330° (§13.4); repeated as 0.42° = RSS(0.18, 0.16, 0.22-climbing) in §16.3.
- **Gates:** FINE_POINT < 0.1° (COM-011/-012 cards) but < 0.3° in the COM-011 walkthrough (*"pointing 0.18 deg, within 0.3 deg fine budget"*) and 0.21° accepted at First Light. **The book contradicts itself by 3×.**
- **Momentum:** I = 1.5e-5 kg·m², ω_max 5000 rpm, H_max 7.85e-3 N·m·s, T_dist 1.5e-6 N·m, t_sat 5250 s ≈ 1.0 orbit, desat at 75 % = 3750 rpm, desat duration 30–40 min.
- **Modes:** the §7.7 enum is correct and canonical; every Ch 14 card uses the *previous* enum (SAFE_POINT/NOMINAL_POINT) and `AOCS_SET_MODE_n` syntax.

Against the sim: the enum matches (C-grade only in Ch 14); **nothing else does**. Wheel inertia is out by 333× (C1), so t_sat and the whole desat-scheduling exercise are wrong by the same factor; the wheel limit appears as three different numbers in the book and two in the sim (C2); the pointing budget has no telemetry to decompose (C5) and the sim's FINE_POINT error converges to ~0.0005° so the exercises' input values never occur (C4). The MCS's own att_error limits (±1.0° yellow / ±2.0° red) are 2–4× looser than the book's mission requirement, so a student watching the MCS will never see a pointing alarm the book says should fire at 0.5°.

**The exercises that die if C1/C4 are not fixed:** §13.4 (pointing RSS), §13.7 (desat scheduling), §13.8 item 3 and 5 (worked shift), §16.3 (AOCS case study), WB-5.4-FD.

### 3. PROPULSION / orbit control

Everything is in D1–D5. To state the size precisely: the book ships **two full nominal procedure cards (NOM-004, NOM-005), one contingency card (CTG-006), one LEOP step (LEOP-007 step 6), one case study (§16.8), one telemetry claim ("SID 5 / propulsion"), four commands (`PROP_ARM`, `PROP_ISO_CLOSE`, `htr_prop`, `HEATER_THRUSTER`) and one appendix role (Propulsion Engineer)** that assume a propulsion system. `PROP_ARM` and `PROP_ISO_CLOSE` have **zero occurrences** anywhere in `~/smo`. There is no ΔV-budgeting *exercise* in Part 2 — NOM-004 step 1 is *"FDS: produce delta-V plan and execution time | (FDS tool) | *(representative — verify against simulator)*"* — so nothing numeric is at stake operationally; the ΔV arithmetic all lives in Part 1 (§3.4.6, App C.3), which is design-course content and is fine as-is. What is at stake is a whole afternoon of the FDS position's role and the credibility of the procedure catalogue.

The sim's *residue* of propulsion (`HEATER_THRUSTER` func 42, circuit code `2=thruster`, `tcs.temp_thruster` 0x0409, `thruster_heater ~8 W` in `emergency/thermal_runaway.md`) directly contradicts the sim's own manuals in three places. Whatever the user decides on D5, **the residue should go with it** — right now the book teaches a heater for a subsystem the sim's manual says doesn't exist, and the sim honours the command.

### 4. POWER — for the array-resizing decision

Numbers the book commits to, precisely:

| Book statement | Location |
|---|---|
| *"The deployable solar arrays generate **28 W at start-of-life** in the lit portion of the orbit"* | week3 §5.7 |
| *"a **120 Whr Li-ion battery** (4S configuration, ~27 V nominal)"* + note *"a realistic 6U bus would typically carry 30–40 Whr"* | week3 §5.7 |
| *"the platform idles at **70 W**"* + note *"a typical 6U platform idles at 8–14 W"* | week3 §5.7 |
| Eclipse exercise: SoC_entry 85 %, P_avg **70 W**, t_ecl **35 min**, E **120 Whr** → SoC_exit **51.0 %**; typical range **45–70 %**; *"< 30 % triggers FDIR"* | week3 §13.3 |
| SA current nominal **1.5–2.5 A lit**; load current **1.0–2.2 A**, OC at 2.8 A | week3 Table 6.2 |
| SoC ladder: nominal **70–95 %**, yellow **< 60 %**, red **< 40 % (load shed)** | week3 Table 6.2 |
| **6U reference: body-mounted ~12 W · single deployable ~30 W · dual deployable ~48 W · quad ~80 W** (vendor-sourced, ASTERIA 6U 48 W BOL) | facilitator §3.1 "CubeSat SA Power Reference" |
| SA sizing constants: η_cell 0.295 TJ GaAs · S 1361 W/m² · f_pack 0.85–0.90 · f_cover 0.97 · L_d = (1−0.025)ⁿ · L_T = 1 − 0.0019(T−28), T_cell 60–80 °C · η_path,eclipse = 0.92×0.95×0.90 = **0.786** | facilitator §3.1.2 |
| Recharge sizing: P_SA,EOL = P_peak,sunlight + P_ecl·t_ecl/(t_sun·η_path); example t_ecl 35 min, t_sun 60 min | facilitator §3.1.2 |

Sim side: `eps.yaml` arrays A/B 0.314 m² @ 0.295 (legacy path), body panels 0.06/0.06/0.03/0.03/0.06/0.06 m², **wings 0.300 m² each** (raised from 0.240 by power_analysis item 9), MPPT 0.97, S 1361, battery 120 Wh / 26.4 V nom / 29.2 V @100 % / 21.5 V @0 % / 0.05 Ω. Loads actually billed: obc 15, ttc_rx 5, ttc_tx 12 (was 20), payload 8 standby / 45 imaging, fpa_cooler 15, htr_bat 6, htr_obc 4, aocs_wheels 6. **`platform_idle_power_w: 70.0` is never billed.** Post-fix validated trend: P1 nominal +41.2/+25.6 Wh/orbit, P2 imaging +19.5/**+3.9** Wh/orbit (worst alignment), min SoC 72.5 %.

**Three things the array agent needs from the book:**
1. The book's own vendor reference says a 6U dual-deployable array is **~48 W BOL**. The sim's current orbit-average of **46–54 W** is right on that line. Any resize that pushes generation materially above ~55 W orbit-average contradicts the book's own §3.1 table and should be justified as a design change, not a tuning fix.
2. The **28 W** figure in §5.7 is not a constraint — it is a stale number that agrees with nothing (it is between the book's own "6U body-mounted ~12 W" and "single deployable ~30 W" rows, i.e. it describes a single-wing 6U, which EOSAT-1 is not). It should be rewritten to whatever the resize lands on.
3. The **70 W** idle is the number every student power calculation uses and it is **not what the simulator bills**. If the array is resized against 70 W the array will be oversized by ~2×. Size against the line model (20 W bare / 36 W platform / 53 W commanded with imaging duty), and fix §5.7 + §13.3 in the same change so the book and the array agree afterwards.

### 5. THERMAL

Book numbers: FPA nominal **−15 to −3 °C**, yellow > −3, red > 0 (Tables 6.3 and 6.5, identical); setpoint commanded **−10 °C**, hard floor −20 °C, band hold **±2 °C**; cool-down **60–90 min** from +5 °C at **−0.2 to −0.4 °C/min** (eclipse) and **−0.05 to −0.1 °C/min** (day-side); cooler power **8.5 W**; battery zone 0–25 °C nominal, −5/30 yellow, −10/45 red; OBC 10–30 / 0,35 / −5,60; heater duty 10–60 % nominal, >80 % yellow, 100 % saturated; heater thermostat *"±2 °C of setpoint"*; payload zone red at **+15 °C**.

Sim: FPA limits **−18/+8 yellow, −20/+12 red** everywhere in `mcs/limits.yaml` and `manual/06_payload.md`; but `tcs.yaml event_thresholds.fpa` warning [−50, −5] / alarm [−60, 0]; and the planner's imaging window is [−50, −5]. Battery heater 6 W on 1 °C / off 5 °C; OBC heater 4 W on 5 / off 10 (**4–5 °C bands, not ±2**). Battery temp MCS limits yellow 2.0–40.0, red 0.0–45.0 (the book's −5/−10 lower bounds are unreachable). No payload heater and no `tcs.temp_payload` parameter at all, so the book's payload-zone row and the §16.4 `htr_payload` case study are unbacked. τ_cool = 100 s makes COM-010 a 2.5-minute event against the book's 90 minutes.

The FPA band question (F1) is the single most consequential thermal decision: it is quoted in **two book tables, one editor's note, one commissioning card, one commissioning walkthrough, one S19 rule, one workbook sheet and one planner precondition**, and there are four incompatible answers live in the repo.

### 6. REPORTING — what a report generator must produce

The book requires **seven** distinct written artefacts. In descending order of how well specified they are:

1. **Anomaly report** — fully specified, 10 named fields, ID convention `ANO-YYYY-NNN`, severity ladder with response time and authority (week3 §5.20.4 Tables 5.9/5.10). This is directly implementable.
2. **Shift handover (NOM-012)** — 8 named items in fixed order, with the *"read back two specific items"* confirmation step and a ≤6-minute target (week3 §9.5). Requires: current phase + MET, last pass AOS/LOS + verdict, **open anomalies keyed by FDIR rule name + status + recovery-procedure step**, open work items, next pass AOS + planned procedure IDs, customer commitments, outstanding GO authorisations.
3. **CFMR / final mission review** — 5 sections, 4–6 pages: objectives PASS/FAIL with evidence, three most-watched parameters + typical values, anomalies log (date / anomaly / procedure / outcome / root cause), ECSS-E-ST-70-11C §5.1–5.5 compliance ticks, crew self-assessment (workbook WB-5.7-FR).
4. **Pass / GO-NO-GO scorecard** — per-phase criteria tables (week3 §10.7, §11.7).
5. **Shift log** — every one of the ~55 procedure cards ends in a log entry of the form `"<PROC-ID> complete"`; §9.2 says the "Mark" callout *"auto-stamps shift log"*; timestamps are relative to AOS.
6. **Long-term trend report (NOM-011)** — weekly, flag anything degraded >5 % vs commissioning baseline.
7. **Debrief / lessons-learned register** — hot-wash, anomaly walkthrough, peer-review readout, consolidated lessons list.

Sim side: **none of these exist as generated artefacts.** The MCS has no anomaly-ticket form, no contingency banner and no FD second-key (the book's §14 editor's note admits all three and falls back to paper). The one relevant tool is **`tools/tm_report.py`**, which turns archived `workspace/dumps/dump_*.bin` into a self-contained HTML "between-pass report" with pass metadata, packet counts by service/subtype/SID, S5 event log with severity colouring, per-SID HK time series with inline SVG sparklines, and anomaly flags (S5 severity ≥ 3, >3σ single-step changes, HK cadence gaps). That is roughly **60 % of the book's pass report** already — what it lacks is procedure execution history, command log with S1 verification outcomes, FDIR firing history by rule name, GO/NO-GO record and the free-text fields. `tools/procedure_audit.py` is a second reusable asset: it already cross-checks every procedure's `**TC:**` and TM references against `tc_catalog.yaml` / `parameters.yaml` / `hk_structures.yaml`.

**Recommendation for the report-generator design:** build on `tm_report.py`'s decommutation layer; add (a) a command log from the S1 verification stream keyed to procedure ID, (b) an FDIR section that names rules from `s19_rules.yaml` (which is why I2 must be fixed — the book's fictional rule names would otherwise never match), (c) the NOM-012 handover template as a fillable section pre-populated from telemetry (phase, MET, SoC, mode, next AOS from `orbit_tools.py`), and (d) the anomaly-report 10 fields as a form seeded from the S5 event that triggered it.

---

## Textbook-only capabilities (what the sim must gain, if the book is to be taught as written)

| Capability | Book requires it for | Size |
|---|---|---|
| Propulsion subsystem: state (prop mass, tank P/T, valve, arm flag), commands `PROP_ARM`/`PROP_ISO_CLOSE`/burn, TM block, ΔV accounting | NOM-004, NOM-005, CTG-006, §16.8, LEOP-007 step 6, Propulsion Engineer role | **L** (only if D5 goes option (b)) |
| Realistic pointing-error budget with per-component telemetry (ST bias, gyro drift, wheel jitter, sensor bias) | §13.4, §13.8, §16.3, all FINE_POINT gates | **M** |
| Reachable momentum-saturation monitor (S12 0x025B limit consistent with achievable momentum) + desat-vs-disable rule coherence | §13.7, NOM-007, §15.3 | **S** |
| Radiometric DN model: integration time × gain × scene radiance → per-band DN, saturation flag | COM-011 dark/flat thresholds, COM-012 quality assessment, WB-5.6-FL | **L** |
| Geo-registration error derived from attitude knowledge | COM-012, WB-5.6-FL | **M** |
| Flat-field gradient / dark-frame mean as calibration observables | COM-011 | **M** |
| Realistic FPA cooler time constant (τ ≈ 1500–2000 s) + day-side/eclipse dependence | COM-010, WB-5.6-FPA, the whole commissioning-day-3 structure | **S** |
| Battery cell-level telemetry + `EPS_CELL_BAL` | CTG-013 Battery Cell Failure | **M** |
| Anomaly-ticket form, contingency-active banner, FD second-key confirmation in the MCS | §14 preamble (explicitly admits all three are missing), §7.8 dual-key teaching | **M** |
| `position:` and `criticality:` metadata on the whole TC catalogue | Ch 6 per-position commanding authority; §7.8 command-path risk table | **S** |
| Real downlink queue behind `PAYLOAD_DOWNLOAD_IMAGE` / S13 | NOM-003, COM-012, §6.7 handover content ("outstanding downlink queue") | **M** |
| Pass/shift/anomaly report generation | §5.20.4, §9.5, §10.9, WB-5.7-FR | **M** (extends `tm_report.py`) |

## Sim-only capabilities (what the book should gain — currently wasted investment)

| Capability | Where it lives | Why the book should teach it |
|---|---|---|
| **47 S2 device-access commands** (per-device on/off across all six subsystems) | `tc_catalog.yaml:4-365` | The only per-device power/enable path; CTG procedures would be far more precise with it |
| **`TTC_SET_DOWNLINK_MODE` (79)** and the three-rung rate ladder with hard gates (antenna + downlink mode + elevation ≥ 10°, S1.8 error 0x0007 on refusal) | `tc_catalog.yaml:1104`, `subsystems/ttc.yaml` | NOM-003 as written **fails** without it; and "the high rate must be earned" is a genuinely good ops lesson |
| **`EPS_SET_MODE` (83)** nominal/safe/emergency with explicit "loads are restored by the operator, not automatically" | `tc_catalog.yaml:965` | Directly relevant to CTG-001/CTG-023 |
| **`AOCS_GPS_SET_START_MODE` (84)** | `tc_catalog.yaml:602` | GPS cold/warm/hot start is a real LEOP decision |
| **AOCS control-torque gain tuning** (`AOCS_SET_TORQUE_GAIN_X/Y/Z`, `_ALL`, `_DUMP`, S6.2/6.5) — and they really change the control law | `tc_catalog.yaml:438-473`, `aocs_basic.py` | The only in-flight tuning lever; ideal COM-013 content |
| **Scene-entropy-dependent CCSDS-123 compression** (ratio 1.5–4.0, telemetered 0x0614) | `payload_basic.py:498-513` | Explains why stored ≠ raw size — the missing half of the data-budget lesson |
| **`payload.fpa_ready` (0x0610) with 60 s hysteresis** — the actual imaging gate | `payload_basic.py:396-404` | The book gates on temperature; the spacecraft gates on this flag |
| **Memory-segment model** (8 segments × 2500 MB, individual failure, `PAYLOAD_MARK_BAD_SEGMENT`) | `payload_basic.py`, func 31 | CTG-016 currently uses a fictional `MM_MARK_BAD` |
| **Decontamination heating** (funcs 47/48, `tcs.decontamination_active` 0x0418, `fpa_overtemp.yaml` scenario) | `tc_catalog.yaml:846-858` | A real EO ops activity, fully implemented, never taught |
| **31 scenarios / 54 failure modes / instructor console / breakpoints / scenario engine** | `configs/eosat1/scenarios/`, `instructor/`, `breakpoints.py` | The instructor docs use ~6 scenarios; the other 25 are unexercised |
| **Three OBCPs** (`01_pass_preparation`, `02_safe_mode_entry`, `03_payload_checkout`) | `configs/eosat1/obcp/` | On-board control procedures are a distinct and teachable autonomy concept; the book never mentions them |
| **Runnable procedures** (`runnable:` links: NOM-002→`imaging_pass`, NOM-003→`data_dump`, NOM-008→markdown adapter, NOM-009→`housekeeping_collection`) | `procedure_index.yaml` | The Procedures tab can *execute*; the book teaches it as read-and-do only |
| **HK commanded on, not free** (`default_enabled_sids: [1, 11]`) | `simulation.yaml` | A real commissioning step the book skips entirely |
| **Five-station network with published contact statistics** (103 passes/48 h, median gap 33 min) + `pass_gap_analysis.py` | `orbit.yaml`, `tools/` | The book teaches a two-station world |
| **`tools/link_budget_ladder.py`, `tm_report.py`, `procedure_audit.py`, `delayed_tm_viewer.py`, `generate_xtce.py`/`generate_smdl.py`** | `tools/` | XTCE/SMDL export in particular is an operations-standards teaching opportunity |
| **`last_tc_error_code` (0x0315) and the FAILURE_CODES table** | `parameters.yaml:192` | The book teaches S1.8 rejection but never how to read the reason |

---

## Judgement calls for the user

1. **Does EOSAT-1 have propulsion?** (D5) Sim manuals say no in three places; Part 2 ships NOM-004, NOM-005, CTG-006, `PROP_ARM`, `PROP_ISO_CLOSE`, `htr_prop`, `HEATER_THRUSTER` and §16.8. My recommendation on engineering merit: **no propulsion** for the spacecraft (a 6U at 450 km decays inside the FCC 5-year rule — the book's own §3.4 says so), retire the sim's thruster residue, and re-teach orbit maintenance as a *decision not to carry propulsion* plus a paper ΔV exercise. But this deletes a whole FDS work package, so it is yours to call.
2. **Two ground stations or five?** (G2) The book says two (Iqaluit + Troll, ~14 passes/day); the sim flies five with measured statistics (~51 passes/day, median gap 33 min). This changes every pass-planning, data-budget and eclipse-scheduling number in Ch 8 and Ch 13. Pick one and propagate.
3. **What is the FPA operating band?** (F1) Four live answers: book −15…−3; MCS/limits and manual −18/+8 yellow, −20/+12 red; `tcs.yaml` events −50/−5 warn; planner −50…−5. Pick one, single-source it, propagate to five files plus the book.
4. **What is the reaction-wheel inertia, and therefore the momentum budget?** (C1) Book 1.5e-5 kg·m² (nanosat MEMS), sim 0.005 kg·m² (200-mm class). Neither is a 6U wheel. The desat exercise is wrong by 333× until this is settled.
5. **Should the sim model pointing error realistically?** (C4/C5) Today FINE_POINT converges to ~0.0005°, so the book's 0.18/0.32/0.42° world never occurs and four exercises have no referent. Adding a component-wise error budget is ~M effort and unlocks the most-used AOCS teaching in the book.
6. **Should integration time and gain have radiometric consequences?** (B7/B8) Without it, the COM-011 dark/flat thresholds and the COM-012 saturation decision point are un-runnable. The alternative — rewriting those around SNR and `last_scene_quality`, which the sim already computes — is cheaper and physically honest.
7. **What is the swath / detector geometry?** (B6) Book 240 km, sim 45 km. 240 km swath with 4 MB frames and 10 m GSD is not self-consistent; the sim's 5000 px × 20 µrad is a small telescope for an ocean-colour mission. Decide the instrument, then fix whichever side is wrong.
8. **Which SoC/bus-voltage alarm ladder is canonical?** (E4/E7) Book, `mcs/limits.yaml`, `s12_definitions.yaml` and `simulation.yaml` give four different answers for the same battery.
9. **Is `PAYLOAD_CAPTURE` addressed by scene or by coordinates?** (A1/A2) Catalogue + book + MCS say scene_id + lines; the flight code says lat + lon. Both are defensible for a real EO mission; only one can be on the wire.

---

## Work packages (courseware side)

| # | Package | Files | Effort |
|---|---|---|---|
| WP-1 | **Rebuild Chapter 14 procedure cards against `tc_catalog.yaml`.** Apply the §7.7 "what you will *not* find" translation table that already exists; fix all 14 bogus command names and 4 wrong func_ids (A4–A8, A10–A15); normalise `AOCS_SET_MODE(mode=n)`; correct mode names to the canonical enum; fix the SID cross-references inside cards (I6). | `docs/course/orbit_north_week3.md` §14.1–14.5 | **L** |
| WP-2 | **Renumber the nominal procedures to match `procedure_index.yaml`** (NOM-005/006/008/010/011 all mean different things; NOM-004 doesn't exist; COM-004 is real and uncarded). Fix §14.6's false "no further placeholders" claim and the 12-vs-11 count. | week3 §14.3, §14.6 | **M** |
| WP-3 | **Payload numbers pass.** Store 20 GB / starts 20 % used; standby current 0.29 A, trip 2.5 A; cooler 15 W; downlink 262 s not 32 s; insert `PAYLOAD_SET_MODE(mode=2)` before every capture and `(mode=1)` after; teach `payload.fpa_ready` as the gate; fix `payload.last_scene_quality` mnemonic; add compression-ratio teaching. | week3 §6.7, §8.5, §11.9 COM-009/-010/-011/-012, §14.2, §14.3; workbook WB-5.6-C009/-C012/-FL | **M** |
| WP-4 | **Attitude numbers pass** — pending the C1/C4 rulings. Wheel limit 5500/5000; NOM-007 entry criterion; §13.7 re-derivation from the agreed inertia; §13.8 4200 rpm = 84 %; "3U at 500 km" → "6U at 450 km"; single FINE_POINT gate value; §5.7 "single-head star tracker" → two. | week3 §5.7, §13.4, §13.7, §13.8, §14.1 LEOP-005, §14.2 COM-005/-013, §14.3 NOM-007, §16.3; workbook WB-5.6-C011 | **M** |
| WP-5 | **Power numbers pass** — coordinate with the array-resizing agent. §5.7 array output and idle power; §13.3 re-derived on the line model (SoC_exit becomes ~67.5 % at 36 W); Table 6.2 aligned to the agreed alarm ladder; three-layer load-shed teaching with "no auto-restore"; LEOP-007 power-line names corrected to the real eight; §16.4 case study rewritten without `htr_payload`. | week3 §5.7, §6.4, §13.3, §14.1 LEOP-007, §14.4 CTG-001, §16.4 | **M** |
| WP-6 | **Thermal numbers pass** — pending the F1 ruling. FPA band in Tables 6.3/6.5 + the editor's note + COM-010 + WB-5.6-FPA; two heater circuits not three; real thermostat bands (4–5 °C, not ±2); drop `tcs.temp_payload`/payload-zone rows; cooler 15 W; re-time the cool-down against the agreed τ. | week3 §6.5, §11.9 COM-010, §14.2 COM-002/-010, §15.3; workbook WB-5.6-FPA | **M** |
| WP-7 | **Rewrite §15.3 with the 26 real S19 rule names and actions**, and §15.2's S12 counts; fix §7.6's "25 monitor definitions" → 38; fix §9.5's `eps_payload_load_yellow`. Without this the NOM-012 handover drill ("list each by FDIR rule name") cannot be performed. | week3 §7.6, §9.5, §15.2, §15.3 | **S** |
| WP-8 | **TT&C / data-rate pass.** Replace "0 = 1 kbps, 1 = 64 kbps" with the three-rung ladder and its gates; add func 79 to §7.7/§15.5 and to NOM-003/COM-006; regenerate COM-006's margin table from `tools/link_budget_ladder.py`; fix §5.7's UHF beacon; propagate the 262 s dump time into §8.5 and Ch 8's pass-planning exercise. | week3 §5.7, §7.7, §8.5, §14.2 COM-006, §14.3 NOM-003, §15.5 | **M** |
| WP-9 | **Add the sim-only capabilities the book should teach**: S2 device table; funcs 79/83/84; AOCS torque-gain tuning; `HK_ENABLE` commissioning steps in LEOP-001/-002; decontamination card; OBCPs; runnable procedures and the Procedures-tab runner; `last_tc_error_code` + failure codes. | week3 §7.7, §7.4, §14.1, §14.2, §15.5 | **M** |
| WP-10 | **Ground-segment pass** — pending G2. Two-vs-five stations in §5.7, §8.2.1 and every pass-count/gap number downstream. | week3 §5.7, §8.2.1, §8.5, §13.2 | **S** (if two) / **M** (if five) |
| WP-11 | **Reporting package for the report generator**: consolidate the seven artefacts into one authoritative appendix with field-level schemas (anomaly report 10 fields, NOM-012 8 items, CFMR 5 sections, pass scorecard, shift log entry format, trend report threshold, lessons-learned entry), so the generator has a single spec to build to. | new appendix in week3; cross-ref workbook WB-5.5-AT, WB-5.7-FR | **M** |
| WP-12 | **Expand `docs/orbit_north/week3/student_procedure_quickref.md` and `student_tool_reference.md`** — both are still unexpanded templates with 6 and 9 unresolved `[Claude Code: …]` placeholders; the one card that *is* written cites the wrong func_id for `OBC_BOOT_APP`. Same for `training_day1/2.md` (8 each), `peer_review_worksheets.md` (5), `sim_commissioning_instructor.md` (5), `sim_leop_instructor.md` (3), `breakpoint_spec.md` (1, plus a non-existent phase-transition command). | `docs/orbit_north/week3/*.md` | **L** |
| WP-13 | **Add a Part-1/Part-2 scale bridge** — one paragraph in §5.7 reconciling the 3U/10 W design course with the 6U/70 W-claimed operations spacecraft, so students don't carry the wrong order of magnitude across the book. | week3 §5.7; facilitator §2.4.4 | **S** |
| WP-14 | **Run `tools/procedure_audit.py` (or a variant) over the textbook's Chapter 14/15 command references** as a CI check, so WP-1 doesn't regress. The tool already validates command tokens against `tc_catalog.yaml` and TM references against `parameters.yaml` + `hk_structures.yaml` — it needs only a markdown-source adapter. | `~/smo/tools/procedure_audit.py` + a courseware CI job | **S** |agentId: a57595375c5788328 (use SendMessage with to: 'a57595375c5788328', summary: '<5-10 word recap>' to continue this agent)
<usage>subagent_tokens: 292382
tool_uses: 79
duration_ms: 875011</usage>

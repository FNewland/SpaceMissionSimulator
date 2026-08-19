# POSITION_NEEDS — position-level task analysis for the SMO training suite

Compiled 2026-08-18. Extends RESEARCH.md (ISA-101, SCOS-2000, Endsley, EEMUA 191),
PRINCIPLES.md (P1–P10) and AUDIT.md. Those documents answered *how a display should
behave*; this one answers *what each console position actually does, minute to minute,
and what information and command authority that implies*. It is written against the
post-2026-08-18 UI (UI_DELIVERY_NOTES.md — SA header, per-page commanding, arm→execute,
causal chain), so it does not re-litigate defects the new shell already fixed.

Repo grounding used throughout: `configs/eosat1/mcs/positions.yaml` (the seven
positions and their `allowed_services` / `allowed_func_ids` / `visible_tabs`),
`commands/tc_catalog.yaml` (120 S8 functions + quick-command groups),
`telemetry/parameters.yaml` (456 parameters) and `telemetry/derived.yaml` (3 ground
pseudo-parameters), `mcs/limits.yaml` (52 ground limits), `manual/10_desk_procedures.md`
(GO/NO-GO call matrix, escalation paths, its own MCS-gap table), the six per-position
requirement documents in `docs/ops_research/`, and the six role files in
`configs/eosat1/mcs/role_analysis/`.

---

## 1. Sources

### 1.1 New external sources (this document)

| Source | What it contributed |
|---|---|
| [ESA — Who does what at ESA Mission Control?](https://www.esa.int/Enabling_Support/Operations/Who_does_what_in_the_OCC) | Authoritative ESOC role split: **SPACON** are the "drivers who actually control the spacecraft using procedures written by the engineers"; subsystem **Operations Engineers** "know every detail" of their subsystem; **SOM** owns execution; **GOM** "ensures operational readiness of the TT&C stations" and reviews pass reports; **Flight Dynamics** owns "determination, prediction and control of the orbit"; **FOD** directs critical ops. This is the template for the SPACON / engineer / FDS / Ground Segment split below — in particular the fact that the SPACON *executes* and the engineers *judge*. |
| [HandWiki — NASA Flight controller](https://handwiki.org/wiki/Engineering:Flight_controller) (and [Wikipedia](https://en.wikipedia.org/wiki/Flight_controller)) | Discipline-by-discipline console responsibilities (EECOM "systems that keep the crew alive", INCO as the *only* uplink path, PROP fuel accounting, FDO trajectory + abort envelopes) and the Flight Controllers' Creed. The load-bearing idea imported here: every position has a one-sentence **"what am I protecting"** statement, and everything on the console exists to defend it. |
| [NASA NTRS 20110014997 — A Review of Three Decades of Flight Controller Training Methods](https://ntrs.nasa.gov/api/citations/20110014997/downloads/20110014997.pdf) | Flight controllers are certified per *position*, on *malfunction handling* in integrated sims, not on nominal monitoring — the training goal is anticipation and correct escalation. Justifies grading each position's display against its contingency questions, not its nominal ones. |
| [ECSS-E-ST-70-11C — Space segment operability](https://ecss.nl/standard/ecss-e-st-70-11c-space-segment-operability/) ([PDF](https://ecss.nl/wp-content/uploads/standards/ecss-e/ECSS-E-ST-70-11C31July2008.pdf)) | The observability/commandability contract each position's display should surface: modes and transitions "unambiguously identified" in TM (5.6.1c); "remaining on-board resources" determinable at any time (4.6h); failure diagnosis "down to function or equipment level" (5.3.1l); TC verification at acceptance/start/progress/completion (5.5.4b); critical commands need "at least two separate command actions" (5.5.2b — the arm→execute the new UI ships); after any reconfiguration the ground must determine "the cause … after completion" (5.6.2e); ground "capable of overriding any on-board autonomous function" (5.7.2f). Where a position's question below has no answering parameter, that is usually a violation of one of these clauses — the GAP rows cite them. |
| [Patterson, Watts-Perotti & Woods — Voice Loops as Coordination Aids in Space Shuttle Mission Control](https://www.interruptions.net/literature/Patterson-CSCW-JCC99.pdf) | Controllers maintain team SA by *overhearing* other positions' traffic; the display equivalent is that each console must show enough of the *neighbouring* positions' state to follow the loop (e.g. Payload Ops must see AOCS fine-point status without owning it). Basis for the "read-only view of adjacent subsystem" rows below and for `visible_tabs` ⊃ `allowed_func_ids` being the right model (already done for aocs→prop). |
| [ESA — ESA's 'team of teams' for Herschel/Planck](https://www.esa.int/Enabling_Support/Operations/ESA_s_team_of_teams_ready_for_Herschel_and_Planck_operations), [ESA Mission Control FAQ](https://www.esa.int/Enabling_Support/Operations/ESA_Mission_Control_FAQ) | Confirms the ESOC pattern of a small certified FCT plus on-call subsystem support outside critical phases — i.e. one person covers several disciplines on a routine shift, which is exactly the 2–3-person smallsat/classroom reality. |
| [Lessons Learned from IDEASSat — university CubeSat ops & anomaly analysis](https://www.academia.edu/80897867/Lessons_Learned_from_IDEASSat_Design_Testing_on_Orbit_Operations_and_Anomaly_Analysis_of_a_First_University_CubeSat_Intended_for_Ionospheric_Science) and [First-MOVE lessons learned](https://www.researchgate.net/publication/281621099_Results_and_lessons_learned_from_the_CubeSat_mission_First-MOVE) | University ops teams fail on *pass discipline*, not console skill: unprepared passes, no pre-pass checklist, no beacon-decode fallback, anomalies diagnosed from too little downlinked HK. Justifies the heavy pre-pass/post-pass weighting of the cadence tables and the beacon/bootloader emphasis in the TT&C section. |
| [UNISEC — Mission Assurance Handbook for University-built Lean Satellites](https://ma.unisec.jp/wp-content/uploads/2023/06/ma_handbook_ver2.0_en.pdf) | The "lean satellite" ops model: tiny teams, students rotating through roles, procedures as the transfer mechanism between cohorts. Supports treating the seven MCS positions as *hats* a 2–3 person shift wears, and the handover artefacts as first-class. |
| [SatNOGS — Operation](https://wiki.satnogs.org/Operation), [Observations](https://wiki.satnogs.org/Observations), [SmallSat overview paper](https://digitalcommons.usu.edu/cgi/viewcontent.cgi?article=4125&context=smallsat) | What a real station operator display shows: scheduled observation timeline, waterfall/spectrum, decoded frames, pass polar plot — and what it *never* shows: the channel as ground truth. An operator infers the channel from the waterfall; only a simulator knows Eb/N0 "for real". Basis for the MISPLACED findings on the rfsim radio page. |

### 1.2 Already-covered ground (do not re-read for this doc)

ISA-101 hierarchy and grey-baseline, SCOS-2000 AND/synoptic display types, OpenC3
widget vocabulary, EEMUA 191 / ISA-18.2 alarm management, Endsley SA levels and SAOD
— all in RESEARCH.md with citations. The book's nine-position room (FD, SPACON, AOCS,
EPS, Thermal, TT&C, Payload Ops, FDS, Ground Segment + Mission Manager) is inventoried
in `docs/review_2026-07/textbook_inventory.md` §63 and §138; the sim exposes seven
console positions, with FDS, Ground Segment and Mission Manager off-tool (ibid. note 13).

---

## 2. How to read the per-position sections

Each position gets:

- **Task statement** — the NASA-style "what am I protecting" sentence.
- **SA table** — the questions the position must answer, at Endsley L1 (perceive),
  L2 (comprehend), L3 (project), with the *real* parameter (name + hex id) that
  answers it, or **GAP** where no data exists. Parameters cited are from
  `telemetry/parameters.yaml`; ground-derived ones from `derived.yaml` (0x08xx).
- **Commands owned** — real func_ids/services from `tc_catalog.yaml` as gated by
  `positions.yaml`, with the two-key/GO items flagged per the GO/NO-GO call matrix
  (`manual/10_desk_procedures.md`).
- **Pass cadence** — pre-pass / in-pass / post-pass duties. EOSAT-1 geometry: 450 km
  SSO, Iqaluit (63.7°N) + Troll (−72°S), 8–12 min passes, ~14 contacts/24 h with
  multi-hour gaps mid-day — so the *shape* of every job is: prepare (planner, reports,
  procedures) → execute a short, dense contact → analyse (dumps, trends, handover).
  HK cadence in contact: EPS SID 1 @ 1 s, AOCS SID 2 @ 4 s, TCS SID 3 @ 60 s,
  OBDH SID 4 @ 8 s, PAYLOAD SID 5 @ 8 s, TTC SID 6 @ 8 s, PROP SID 9 @ 8 s.

A recurring L3 finding is stated once here rather than seven times: **the ground
computes only three derived parameters** (`contact.current_gs` 0x0800,
`contact.time_to_aos` 0x0801, `contact.in_contact` 0x0802). Every "how long until…"
question below that is not one of those three is currently answered by mental
arithmetic. The single biggest implementable improvement in this document is a
**derived-parameter block v2** (gap G1, §5).

---

## 3. The seven console positions

### 3.1 flight_director — Flight Director

**Protecting:** the mission — the integrity of the decision process itself. FD owns no
subsystem; FD owns the GO/NO-GO gates, the contingency declaration, and the arbitration
when two positions' needs conflict (power vs payload, desat vs imaging). Per the
GO/NO-GO call matrix, FD authorises every load-shed step, mode change, blind TC, OBC
switchover, transponder switch, antenna deployment, and every safe-mode recovery step.

| # | Question | L | Answered by | Status |
|---|---|---|---|---|
| FD1 | Is the spacecraft OK *right now*, in one glance? | L1 | SA header (see/talk/well/next) + overview schematic | SERVED (new shell) |
| FD2 | What mode is the spacecraft in, and is it the mode we commanded? | L2 | `sat.mode` (0x0700 block in SID 4), `aocs.commanded_mode` 0x0297 vs `aocs.mode_demoted` 0x0298; ECSS 5.6.1c | SERVED for AOCS; **GAP**: no system-level commanded-vs-actual for EPS mode (`eps.eps_mode` 0x0137) or payload mode — no "demoted" flag exists outside AOCS |
| FD3 | Who is asking for a GO, and what are they asking me to authorise, with what consequence? | L2 | GO/NO-GO panel + the call matrix | SERVED POORLY: the poll is generic; the matrix in `10_desk_procedures.md` (who calls, who must respond, criticality) is not rendered with the poll |
| FD4 | Are two positions about to collide? (desat during imaging, load shed dropping the payload line mid-strip, TX off during a dump) | L2/L3 | `aocs.desat_inhibit` 0x029A, `eps.load_shed_stage` 0x0134, `ttc.downlink_mode` 0x053C, `payload.mode` 0x0600 | **GAP**: the *conflict* is never computed; FD must join four subsystem pages mentally. Desk-procedures gap #3 ("no cross-subsystem impact alerts") is still open |
| FD5 | How much pass time is left against how much plan is left? | L3 | `contact.in_contact` 0x0802 + LOS countdown (SA header); plan from planner | **GAP**: pass-time-remaining vs plan-items-remaining is not shown anywhere on the MCS (plan lives in the planner, execution in PROC panel) |
| FD6 | If I say NO-GO now, what is the fallback and when is the next opportunity? | L3 | next passes (planner /wide, contact schedule) | SERVED POORLY: next-pass list exists, but no "this activity can slip to pass N" affordance |
| FD7 | What happened while I was not looking (previous shift / autonomous period)? | L2 | reports page (`tm_report`), handover notes, S5 event archive | SERVED for TM; **GAP**: no consolidated shift log (commands + GO records + alarms + procedure runs in one timeline) — book NOM-012's 8-item handover is assembled by hand |

**Commands.** `allowed_commands: all` — but FD's *legitimate* instrument is the GO,
not the send. The one command that is genuinely FD-owned is `SYS_SET_MISSION_PHASE`
(func 120, the phase gate) and the authorisation half of every `critical: true` TC
(dual-key: operator arms, FD GO, operator executes — the book's Ch 5 model; the new
arm→execute two-step implements the mechanics but **the FD GO between arm and execute
is still procedural/voice only**, there is no FD-console "release" affordance).

**Cadence.** Pre-pass: review plan + constraints verdict (planner), poll positions GO
for pass startup (NOM-001). In-pass: run the poll gates, watch FD5/FD4, arbitrate.
Post-pass: debrief against plan, sign handover, open/close anomaly tickets (paper —
sim has no ticket form; known TO item in `plan_2026-08/briefs/07`).

---

### 3.2 eps_tcs — Power & Thermal

**Protecting:** the energy budget and the temperature envelopes — "the systems that
keep the *spacecraft* alive" (EECOM's framing, one deck down: battery before payload,
always). Two hard invariants: SoC never reaches the load-shed floor uncommanded, and
no thermal zone crosses a red limit (`limits.yaml` rows for 0x0100–0x011F, 0x0407/8).

| # | Question | L | Answered by | Status |
|---|---|---|---|---|
| PT1 | Bus voltage, SoC, battery current/temp, per-line currents — now? | L1 | `eps.bus_voltage` 0x0105, `eps.bat_soc` 0x0101, `eps.bat_current` 0x0109, `eps.bat_temp` 0x0102, `eps.line_current_0..7` 0x0118–0x011F @1 s | SERVED |
| PT2 | Is generation meeting consumption, with what margin? | L2 | `eps.power_gen` 0x0107, `eps.power_cons` 0x0106, `eps.power_margin_w` 0x0135 | SERVED (margin is a real on-board parameter — good) |
| PT3 | Which loads are on, which tripped, what shed stage are we at? | L2 | `eps.pl_*` 0x0110–0x0117, `eps.oc_trip_flags` 0x010D, `eps.load_shed_stage` 0x0134, `eps.eps_mode` 0x0137 | SERVED (stepper idiom) |
| PT4 | Are we in eclipse, and when does it end / when is the next one? | L2/L3 | `eps.eclipse_flag` 0x0108 (now); next-eclipse countdown in SA header from server orbit state | SERVED for countdown; **GAP**: no eclipse *duration* and no per-orbit eclipse schedule on the EPS page itself (FDS product, `orbit_tools.py` computes it) |
| PT5 | Will SoC hold through this eclipse — what will it be at exit? | L3 | — | **GAP** (the canonical one, RESEARCH §3): needs derived `eps.soc_at_eclipse_exit_pred` = linear/coulomb projection of 0x0101 over remaining eclipse. All inputs exist server-side |
| PT6 | At current draw, when do I hit yellow/red SoC / the next shed stage? | L3 | — | **GAP**: time-to-limit derived param (PRINCIPLES P8); trivially computable from archive slope + `limits.yaml` param_id 257 |
| PT7 | Is the battery *degrading* across orbits? | L3 | `eps.bat_dod` 0x0120, `eps.bat_cycles` 0x0121, `eps.battery_health_pct` 0x0136, `eps.sa_a/b_degradation` 0x0124/5 | SERVED POORLY: parameters exist but only reachable via TRENDS query; no baseline-vs-now board (book NOM-011 wants "degraded >5 % vs commissioning baseline" flagged) |
| PT8 | Thermal: which zone is closest to its limit, and which way is it moving? | L2 | `tcs.temp_*` 0x0400–0x0409, `tcs.temp_regulation_margin` 0x041A, `tcs.fpa_cooler_margin_k` 0x0425, heater duties 0x040E–0x0410 | SERVED (deviation bars); the *margin* params are the right L2 form |
| PT9 | If I authorise the catbed heater / decon heating, does the power budget still close? | L3 | `tcs.htr_total_power` 0x0411 + PT2 | SERVED POORLY: no what-if; operator adds watts in their head. Cheap: show each heater's rated draw next to its switch (it is in `subsystems/` config) |
| PT10 | Wing deployment state (LEOP)? | L1 | `eps.wing_status` 0x0144, `eps.wing_deploy_timer` 0x0145 | verify on EPS page — was desk-procedures gap #5 |

**Commands** (funcs 16–25, 40–49, 81–82; S1/3/5/8/17/20): power lines
`EPS_POWER_ON/OFF` 19/20, loads 22, OC reset 21, charge rate 23, SA drive 24,
`EPS_EMERGENCY_LOAD_SHED` 25 (FD GO per stage), `EPS_SET_MODE` 83 — **note 83 is
absent from `allowed_func_ids` [16–25,40–49,81–82]; either the EPS mode command is
deliberately FD-only or this is a config bug — decide and document**; wing deploy 81
(irreversible → FD GO), wing status 82; heaters 40–45, duty limit 46, decon 47/48,
thermal map 49. Payload-power arbitration funcs 16–18 (`EPS_PAYLOAD_MODE`,
`EPS_FPA_COOLER`, `EPS_TRANSPONDER_TX`) are the EPS side of the payload/TT&C
suspend matrix — GO row "Payload suspend/resume: payload_ops *or* eps_tcs".

**Cadence.** Pre-pass: check predicted eclipse pattern and any planned high-draw
activities against margin (planner constraint verdict). In-pass: PT1–PT3 watch,
execute heater/line procedures, answer shed-authority calls. Post-pass: trend battery
health from dump (SID 1 in the delayed-TM viewer), log DoD/cycle deltas, prep NOM-011
inputs.

---

### 3.3 aocs — Flight Dynamics (console) 

*(The sim's `aocs` position merges the room's AOCS engineer and part of FDS; the
off-console FDS residue is §4.2.)*

**Protecting:** knowledge and control of *where the spacecraft points and how fast it
rotates* — the pointing chain that everything downstream (power, link, imaging,
burns) borrows. Its two authority products: the GO on fine pointing for imaging, and
the manoeuvre vector Propulsion executes.

| # | Question | L | Answered by | Status |
|---|---|---|---|---|
| AO1 | Attitude, rates, error — now? | L1 | quaternion 0x0200–0x0203, rates 0x0204–0x0206, `aocs.att_error` 0x0217 @4 s | SERVED |
| AO2 | What mode are we in, is it the commanded one, and why not? | L2 | `aocs.mode` 0x020F, `aocs.submode` 0x0262, `aocs.commanded_mode` 0x0297, `aocs.mode_demoted` 0x0298, demotion reason in S5 event 0x0200 | SERVED — this is the best commanded-vs-actual modelling in the product; ECSS 5.6.1c done right |
| AO3 | Can we enter FINE now, and if not which precondition fails? | L2 | `aocs.fine_ready` 0x02C5 bitmask (bit0 ST stars, bit1 wheels, bit2 MEKF, bit3 rates, bit4 momentum ≥70 %, bit5 propagate-only, bit6 slew idle) | SERVED POORLY: rendered as a raw integer on the AOCS page key-param list (`mcs.js:889`); the bit *labels* live only in the parameter description. Decode it |
| AO4 | Momentum state: how loaded, which wheel, when must we desat? | L2/L3 | wheel speeds 0x0207–0x0209 (+RW0), `aocs.momentum_saturation_pct` 0x0282, `aocs.h_body_nms` 0x029B, `aocs.desat_ineffective` 0x029C | L2 SERVED; **GAP** L3: no time-to-70 % projection (drives imaging-window planning via 0x02C5 bit4; procedure NOM-002 explicitly tells the operator to watch it) |
| AO5 | Sensor health: which ST/gyro/CSS/MAG unit is trustworthy? | L2 | `aocs.st1/2_status` 0x0240/0x0243 (0–4 enum), `aocs.gyro_units_on` 0x0294, `aocs.gyro_saturated` 0x0295, `aocs.est_source` 0x02C1, `aocs.est_innov_rej_count` 0x02C2 | SERVED |
| AO6 | Where are we (orbit), and is on-board time/ephemeris good? | L2 | `aocs.gps_fix` 0x0274, GPS pos/vel params, `obdh.obc_time` 0x0309 vs ground | SERVED; time-sync recovery procedure exists |
| AO7 | Is the estimator converged after a disturbance / time jump? | L2 | `aocs.rate_stability_3sig` 0x02C3, `aocs.settle_time_s` 0x02C4, est params above | SERVED |
| AO8 | Will pointing hold through the planned strip / the eclipse transition? | L3 | — | **GAP**: no projected momentum/pointing over the imaging window; the honest proxy (AO4 projection) is the fix |
| AO9 | Burn geometry: is the manoeuvre vector loaded on board what FDS planned, and did the burn achieve it? | L2 | PROP tab read-only: `prop.burn_dv_cmd` vs `prop.burn_dv_achieved`, `prop.dv_accum`, post-burn `prop.sma_km` 0x0B1A, `prop.orbit_epoch_version` | SERVED (deliberate read-only design in positions.yaml is exactly the Patterson "overhear" pattern) |

**Commands** (funcs 0–15 + 84; S1/2/3/5/8/11/17/20): mode 0 (FD GO for fallback
modes), desat 1 (+ inhibit via 96 — **func 96 `AOCS_SET_DESAT_INHIBIT` is NOT in
aocs's allowed list [0–15,84]; yet 0x029A says the latch is operator-settable.
Either add 96 or document it as FDIR/FD-owned**; same for 85–91: pointing frame,
target quat, geo target, control BW, fine-ready check 89, mom-mgmt config 90, est
reset 91 — all AOCS-domain funcs a Flight Dynamics operator cannot send), wheel
enable/disable 2/3, ST power/select 4–6, MAG select 7, RW bias 8, MTQ 9, slew 10,
momentum check 11, acquisition 12, gyro cal 13, RW ramp 14, deadband 15, GPS start
mode 84 + GPS device via S2 (0x020F device). S11 for time-tagged pointing ops.

**Cadence.** Pre-pass: run `orbit_tools.py predict`, check momentum vs planned
strips, prep mode-transition TCs. In-pass: AO2/AO3 gates for imaging GO, execute
desats/cals, verify S11 uploads. Post-pass: pointing-performance trend from dump
(rate stability, att error during strip), gyro-cal results 0x02C9–0x02CF, update FDS
products.

---

### 3.4 ttc — TT&C

**Protecting:** the *only* path to the spacecraft (INCO's framing: nothing reaches
the vehicle except through me). Two invariants: never lose commandability (uplink
chain, cmd-channel timer, beacon fallback), and never spend the downlink budget
blind (rate ladder vs link margin).

| # | Question | L | Answered by | Status |
|---|---|---|---|---|
| TC1 | Lock ladder: carrier / bit / frame — now? | L1 | `ttc.carrier_lock` 0x0510, `ttc.bit_sync` 0x0511, `ttc.frame_sync` 0x0512 (+ rfsim page LEDs, ground side) | SERVED (both ends of the link, correctly separated) |
| TC2 | Signal quality vs what it *should* be at this elevation | L2 | measured `ttc.rssi` 0x0502, `ttc.eb_n0` 0x0519, `ttc.link_margin` 0x0503, BER 0x050C vs predicted from range/FSPL | **GAP** on MCS: predicted-vs-measured overlay (REQ-TTC-MCS-010/011, `docs/ops_research/ttc_requirements.md` §9.2) — `link_budget_ladder.py` computes it offline; the console shows measured only, so a 6 dB pointing loss looks like weather |
| TC3 | Which tier is the TM ladder on, which was commanded, and is DOWNLINK mode gating it? | L2 | `ttc.tm_tier` 0x0539 vs `ttc.tm_tier_cmd` 0x053A, `ttc.tm_symbol_rate`, `ttc.downlink_mode` 0x053C, modulation 0x0537 | SERVED (active-vs-commanded modelled; the func-79 gate is documented in positions.yaml) |
| TC4 | Is the uplink authenticated/decoding, and how long until the cmd channel closes? | L2/L3 | `ttc.cmd_auth_status`, `ttc.cmd_rx_count` 0x0513, `ttc.cmd_decode_timer` 0x0522 (15-min channel, func 71) | SERVED; make the timer a countdown, not a raw seconds value |
| TC5 | How much data must move this pass, and will it fit? | L3 | store fill `payload.mem_used_mb` 0x060B, `obdh.hktm_buf_fill` 0x0312 / `event_buf_fill` 0x0313, tier rate × LOS countdown | **GAP**: "buffer×rate vs time-remaining" verdict is never computed (ECSS 4.6h resource projection). All three inputs are on screen in different places |
| TC6 | PA/transponder health: can I keep transmitting? | L2 | `ttc.pa_temp` 0x050F (limit row), `ttc.tx_fwd_power`, xpdr temp 0x0505, primary/redundant state | SERVED |
| TC7 | If TM is lost, is the spacecraft still *commandable*, and what is the blind procedure state? | L2 | beacon params 0x0521, `ttc.beacon_tx_on`/`beacon_active`/`beacon_link_margin`; CON-004 blind TC steps | SERVED POORLY: beacon panel exists, but blind commanding (desk gap #10) has no tracking affordance — commands sent without TM confirmation look identical to normal sends in history |
| TC8 | Which station is next, is it ready, and what is its pattern? (Iqaluit vs Troll, antenna-failure model) | L2/L3 | `ttc.active_gs` 0x0523, `ttc.gs_equipment_status`, contact schedule (0x0800/0x0801) | SERVED for schedule; GS *readiness* state (IDLE/PRE-PASS/TRACKING/FAILED, REQ-TTC-MCS-020) exists in rfsim, thin on MCS |

**Commands** (funcs 63–79; S1/3/5/8/9/11/15/17/20): transponder switch 63/64 (FD GO —
TM interruption), rate ladder 65, PA 66/67, TX power 68, antenna deploy 69
(IRREVERSIBLE — FD GO), beacon mode 70, cmd channel 71, frequencies 72/73,
modulation 74, RX gain 75, ranging 76/77, coherent mode 78, DOWNLINK gate 79. Plus
S9 (time management) and S15 (downlink of stored TM) — TT&C owns *moving the dumps*.
**Note:** UHF bootloader beacon TX funcs 97/98 are outside 63–79 — bootloader
recovery is therefore not executable from the TT&C console; confirm that is intended
(it is an FDIR/OBDH recovery context) and write it into the position card.

**Cadence.** The most pass-shaped job. Pre-pass: station readiness check (rfsim
page), predicted pass geometry + link budget, tier plan for the dump volume.
In-pass: lock ladder at AOS (the first 30 s belong to TT&C alone), tier changes,
S15 dumps, cmd-channel timer, LOS prep. Post-pass: bytes moved vs planned
(`ttc.total_bytes_tx/rx`), pass report to GOM, anomalies (RSSI vs elevation
residuals — the scatter plot in REQ-TTC-MCS-011).

---

### 3.5 payload_ops — Payload Operations

**Protecting:** the mission *product* — image quality and the storage/downlink
pipeline. The customer-facing position: everything it does is against a target list
and a quality bar, on somebody else's power, pointing and link.

| # | Question | L | Answered by | Status |
|---|---|---|---|---|
| PL1 | Payload mode, FPA temp, cooler, storage — now? | L1 | `payload.mode` 0x0600, `payload.fpa_temp` 0x0601, `tcs.temp_fpa` 0x0408 (< −10 °C imaging gate), cooler pwr 0x0602, `payload.store_used` 0x0604 / `mem_used_mb` 0x060B | SERVED |
| PL2 | Are the capture gates met? (AOCS mode = 5 exactly, FPA ready, storage, SoC > 50 %) | L2 | `aocs.mode` 0x020F + `aocs.fine_ready` 0x02C5 (read via NOM-002), `payload.fpa_ready` 0x0610, `eps.bat_soc` 0x0101 | SERVED POORLY: the gates live in the procedure text; no single "capture readiness" composite on the payload page — four parameters from three subsystems, each on its own page |
| PL3 | Did the strip come out? (quality, flags, degraded-fine bit) | L2 | `payload.last_scene_id` 0x060C, `last_scene_quality` 0x060D, `payload.snr`, checksum errors 0x0609, catalogue flags octet (bit0 = fine_degraded) | SERVED via catalogue TC 32; surface quality on the page, not only in the TC report |
| PL4 | Will a desat pre-empt my strip? | L3 | `aocs.desat_inhibit` 0x029A auto-asserts during imaging; 0x02C5 bit4 is the "a desat is coming" warning (NOM-002 note) | SERVED in data, **GAP in display**: bit4 of a hex integer on another position's page is not a warning |
| PL5 | Storage vs downlink: what is queued, what will fit in the next pass(es)? | L3 | image count 0x0605, mem params, transfer engine state (func 94), + TC5 | **GAP**: same missing verdict as TC5, seen from the product side; also func 29's downlink queue is known-fake (brief 07 A3) |
| PL6 | Thermal/mechanism health: cooler margin, shutter, filter wheel | L2 | `tcs.fpa_cooler_margin_k` 0x0425, shutter/filter positions, `payload.duty_cycle_pct` | SERVED |
| PL7 | Am I within my power envelope / duty cycle? | L2 | `payload.duty_cycle_pct`, `eps.pl_payload` 0x0113 + line current 0x011B | SERVED (read-only EPS view would help; today payload_ops sees only `payload` overview) |

**Commands** (funcs 26–39, 92–95; S1/2/3/5/8/11/13/15/17/20): mode 26, capture 28,
catalogue 32, download/delete 29/30, bad-segment 31, bands 33, integration 34, gain
35, cooler setpoint 36, cal start/stop 37/38, compression 39, filter 92, shutter
exercise 93 + absolute 119 — **119 (`PAYLOAD_SET_SHUTTER`) is not in the allowed
list [26–39,92–95]; the position that owns the shutter cannot drive it to a
position. Add or justify** — transfer engine 94, cal lamp 95, payload devices via
S2 (0x0600–0x0604), S13 large-data transfer.

**Cadence.** Pre-pass: target list from planner (imaging targets list is a planner
feature), capture-gate pre-check, S11 time-tagged capture upload (imaging happens
*outside* contact). In-pass: catalogue query, product downlink via S13/S15, quality
triage. Post-pass: catalogue reconciliation, cal trending, customer commitments into
handover (book NOM-012 item 6).

---

### 3.6 fdir_systems — FDIR / Systems

**Protecting:** the platform's brain and its immune system — OBC/CAN redundancy,
memory, time, and the *correct level* of autonomous protection (ECSS F1/F2). The
systems engineer of the room: owns cross-subsystem failure logic, S12 monitoring
definitions, S19 event-actions, S6 memory.

| # | Question | L | Answered by | Status |
|---|---|---|---|---|
| FS1 | Which OBC/bus is active, what booted us, are watchdogs armed? | L1/L2 | `obdh.active_obc` 0x030C, `obc_b_status`, `active_bus`/bus statuses, `last_reboot_cause`, `reboot_count` 0x030A, boot counters A/B, watchdog config | SERVED |
| FS2 | Is the platform computing healthily? (CPU, memory, SEU, scrub) | L2 | `obdh.cpu_load`, `mmm_used`, `seu_count`, `scrub_progress`, `mem_errors`, stack/heap | SERVED |
| FS3 | Is on-board time true? | L2 | `obdh.obc_time` 0x0309 vs ground UTC, GPS sync state (func 80 gate: needs 3D fix) | SERVED (clock-truth card exists on instructor; MCS shows offset — verify a ground-computed offset param exists rather than eyeballing two clocks: if not, derived `obdh.time_offset_ms` is a cheap GAP fix) |
| FS4 | What is the on-board FDIR doing, and at what autonomy level? | L2 | FDIR summary 0x0700–0x0703 in SID 4 (level, active/critical/high alarm counts), S12 transition events, S5 event stream | SERVED since the 2026-08 TM-provenance fix |
| FS5 | In a cascade, what is the *first-out* root cause? | L2 | S5/S12 event sequence + `fdir/fault_propagation.yaml` (causal graph, ground-side) | **GAP** (RESEARCH §4.4): no first-out grouping in the alarm drawer; the config that encodes the graph is unused for display |
| FS6 | Which S12 monitors are enabled with what thresholds, and which S19 actions will fire? | L2 | S12/S19 PUS panels | SERVED (the panels exist; keep them L4) |
| FS7 | Are the TC counters clean? (rejections = someone's command failed) | L2 | `obdh.tc_rx/acc/rej_count` 0x0304–0x0306, `last_tc_error_code` 0x0315 | SERVED; a rej-count *delta* alert would make it L2 instead of a number someone must watch |
| FS8 | Buffers: will HK/event stores wrap before the next pass? | L3 | `obdh.hktm_buf_fill` 0x0312, `event_buf_fill` 0x0313, `alarm_buf_fill` + limit rows 786–788 | **GAP**: same fill-vs-time projection family as TC5/PL5 |
| FS9 | After an autonomous reconfiguration, can I reconstruct what happened and why? (ECSS 5.6.2e) | L2 | S5 events + S12 transitions + dumps; causal-chain screen | SERVED for ground-visible chain; the delayed-TM viewer covers the out-of-contact window |
| FS10 | Should I raise/lower the autonomy level, or override it? (ECSS 5.7.2f) | L3 | FDIR level 0x0700 + procedures | SERVED in commands (S12 enable/disable, thresholds — FD GO "test only"), weak in display: no "what would fire at this level" preview |

**Commands** (funcs 50–62 + 80; S1/3/5/6/8/12/17/19/20): OBC mode 50, scrub 51,
reboot 52, switchover 53 (FD GO, ALL desks respond), bus select 54, boot 55/56,
counters 57, watchdog 58–60, diagnostics 61/62, GPS time sync 80; S6 memory
load/dump/check; S12 monitor definitions; S19 event-action definitions. This is the
right set. (S9 time management sits with TT&C — fine, but the *decision* to sync is
FS's; the position card should say "request via TT&C" or S9 should be added here.)

**Cadence.** Pre-pass: review autonomous-period events from previous dump, S12/S19
audit. In-pass: watch FS1/FS7 during everyone else's commanding, run
switchover/recovery procedures when called. Post-pass: anomaly reconstruction
(delayed-TM viewer is primarily *this position's* tool), FDIR-firing history into
the handover.

---

### 3.7 propulsion — Propulsion

**Protecting:** the ΔV budget and the *irreversibility boundary* — nothing on this
spacecraft except antenna/wing deploy and this position's valve can change the orbit
or vent the tank. Debris-avoidance + drag-make-up scope only (positions.yaml note).

| # | Question | L | Answered by | Status |
|---|---|---|---|---|
| PR1 | State machine: SAFED/ARMED/FIRING/INHIBIT — now? | L1 | `prop.state` 0x0B00, latch valves 0x0B06/7, FCV 0x0B08 | SERVED (PROP page since 2026-08b) |
| PR2 | Why can't I arm? | L2 | `prop.arm_inhibit_mask` 0x0B15 — 11 documented bits (catbed, tank P, propellant, AOCS mode, att err, SoC, phase, eclipse, valves, latched inhibit, no burn loaded) | SERVED POORLY: shown as raw "INHIBIT" value on the equipment card (`mcs.js:925`); this bitmask is the single most decision-relevant word on the page — decode it like AO3 |
| PR3 | Is the catbed warm enough, and how long until it is? | L2/L3 | `prop.catbed_temp` 0x0B05 (= `tcs.temp_thruster` 0x0409; TCS tab visible for exactly this) | L2 SERVED; L3 heat-up ETA **GAP** (same time-to-threshold primitive as PT6) |
| PR4 | Is the loaded burn the planned burn? | L2 | `prop.burn_dv_cmd`, `burn_time_cmd` vs FDS plan (planner/orbit_tools product) | **GAP**: no side-by-side loaded-vs-planned display; today it is two windows and a voice call |
| PR5 | During the burn: is it converging on cut-off, and are aborts armed? | L1/L2 | `prop.burn_status`, `burn_time_elapsed` vs cmd, thrust/Isp est 0x0B09/0x0B0A, on-board abort thresholds (func 115), `prop.abort_reason` 0x0B14 | SERVED — but burns are normally S11 time-tagged out of contact, so the *real* answer is post-hoc from the dump: make the delayed-TM viewer's SID 9 series the certified burn-reconstruction view |
| PR6 | Budget: propellant and ΔV remaining vs mission need | L3 | `prop.propellant_mass`/`pct`, `prop.dv_accum`, `prop.dv_remaining` 0x0B0C, leak rate 0x0B16 | SERVED (dv_remaining is on board — good ECSS 4.6h compliance); trend leak_rate post-pass |
| PR7 | Did the burn achieve the plan? | L2 | `prop.burn_dv_achieved` vs `burn_dv_cmd`, post-burn `prop.sma_km`, `orbit_epoch_version` (FDS confirms independently) | SERVED (aocs position shares this view read-only) |

**Commands** (funcs 42, 108–118; S1/3/5/8/11/17/20): catbed heater 42 (TCS circuit),
ARM 108 / DISARM 109 / SET_BURN 110 / EXECUTE 111 (S11-deferred normally) / ABORT
112 / valves 113–114 / limits 115 / inhibit set-clear 116–117 / status 118. The
two-step arm model in the *spacecraft* (108 rejected without 110) plus the console
arm→execute is a correct double implementation of ECSS 5.5.2b. All FD GO.

**Cadence.** Pre-pass: burn plan review with aocs/FDS, catbed preheat lead time,
inhibit-mask preflight. In-pass (upload pass): S11 upload, dress rehearsal of abort
path, confirm ARM interlocks clear except "no burn loaded". Burn (out of contact):
nothing — which is itself a lesson. Next pass: PR5/PR7 reconstruction from dump,
budget update, FDS handover.

---

## 4. The room roles (off-tool or partially-tooled)

### 4.1 SPACON — Spacecraft Controller

**Protecting:** procedural integrity of the real-time execution — the ESOC
"driver". In the classroom this is the seat that *sends* what the engineer seats
decide, keeps the log, and runs the loop discipline ("SPACON: log … (mission log)"
ends every book card).

- **Needs to know (L1/L2):** the current procedure and step (`proc.state`,
  `proc.current_step`/`total_steps`, `proc.progress` — these exist as parameters),
  each TC's verification ladder (S1 acceptance→start→progress→completion, ECSS
  5.5.4b — the new stepper), and the SA header. **L3:** what the *next* step
  requires so it can be pre-built.
- **Tool fit:** the MCS PROC panel + commanding page serve this seat well; its two
  gaps are the **shift/mission log** (no auto-stamping log surface; book §7 "Mark"
  callout unimplemented) and **step wait-timers** (desk gap #4).
- **Commands:** whatever the active procedure says, under the acting position's
  gating — SPACON is a *mode of operating* the console, not a scope. Do not give it
  its own allowed_func_ids; give it the log.

### 4.2 FDS — Flight Dynamics (ground)

**Protecting:** truth about the orbit. Products, not real-time telemetry: OD from
GPS/ranging, propagation, pass/eclipse schedules, manoeuvre plans, conjunction
screening (book claims it; sim has none — known CAV). Tools today: `orbit_tools.py`,
`pass_gap_analysis.py`, planner backend. The FDS↔console interface is exactly three
things: the contact/eclipse schedule (feeds derived 0x0800–0x0802 and the SA
header), the manoeuvre vector (feeds PR4), and the post-burn orbit confirmation
(PR7). Gap: none of the three has a *versioned product* the console can display —
"orbit solution #N, epoch T, source GPS/ranging" (`prop.orbit_epoch_version` exists
on board; the ground side shows nothing). Cheap fix: a small FDS-products card on
the planner (current TLE/state vector, solution age, next 5 passes, next eclipse).

### 4.3 Ground Segment — station operations (GOM)

**Protecting:** readiness of the pipe — the ESA GOM's "operational readiness of the
TT&C stations". In the sim this seat owns the rfsim radio page: lock LEDs, RSSI,
Eb/N0, BER, spectrum, constellation, eye diagram, CLTU counters, VC LEDs — a
faithful rack display. Two findings:

- **MISPLACED — the channel truth.** The radio page's "Channel Model" status panel
  (phase noise / fading / interferer states) and the hidden `#config-panel` (set
  Eb/N0, Doppler, fading K, interferers via `/api/channel`) expose and control the
  *simulated channel* — the one thing a real station operator can never see or set
  (SatNOGS operators infer the channel from the waterfall). Both belong behind the
  instructor boundary; leaving them on an operator-reachable page lets a student
  "fix" a link-loss exercise by editing physics.
- **GAP — pass products.** A station produces a pass report (AOS/LOS achieved vs
  predicted, data volumes, faults). The rfsim page has live counters but no
  per-pass rollup for the GOM to review (ESA role: "reviews pass reports").
  `tm_report.py` covers the TM side; the *RF* side (lock times, mean Eb/N0,
  frame-error history) is displayed then lost.

### 4.4 Mission Manager

**Protecting:** the mission *outside* the room — customer commitments, phase
schedule, resources across weeks. Needs products, not consoles: the reports page,
planner plan-vs-achieved, NOM-011 long-term trends, CFMR inputs (workbook WB-5.7-FR:
objectives PASS/FAIL, anomaly log, ECSS clause ticks). Today every one of those is
assembled by hand from TRENDS queries. One implementable improvement covers this
seat: a **weekly mission report** generator alongside `tm_report.py` (targets
imaged vs planned, data delivered, propellant/ΔV ledger, battery-health delta,
anomaly count by subsystem). No new telemetry required — archive + planner exports.

---

## 5. Per-tool matrix

Verdicts: **SW** serves well · **SP** serves poorly (exists, doesn't answer the
question) · **GAP** (question unanswerable with current data) · **MIS** (misplaced
for this audience) · — (not this position's tool).

### 5.1 MCS console (modern shell)

| Position | What it gives them | Verdict + note |
|---|---|---|
| flight_director | SA header, overview schematic, GO/NO-GO panel, all pages | SW overall; SP on FD3 (poll without the call-matrix context), GAP on FD4 (conflict computation), FD5 (pass-time vs plan), FD7 (unified shift log) |
| eps_tcs | EPS/TCS pages, deviation bars, load-shed stepper, in-place commanding | SW for L1/L2; GAP for PT5/PT6 (all L3 projections), SP for PT7 (health baseline) |
| aocs | AOCS page, mode chain, PROP read-only | SW; SP on AO3 (0x02C5 undecoded), GAP AO4-L3/AO8 |
| ttc | TTC page, tier ladder, lock states, beacon panel | SW; GAP TC2 (predicted-vs-measured), TC5 (fit verdict); SP TC7 (blind-TC tracking) |
| payload_ops | Payload page + catalogue | SW; SP PL2 (gates scattered), GAP PL4 display, PL5 |
| fdir_systems | OBDH page, PUS S6/S12/S19 panels, causal chain, FDIR-from-TM | SW; GAP FS5 (first-out grouping), FS8 |
| propulsion | PROP page + TCS view, arm→execute | SW; SP PR2 (mask undecoded), GAP PR4 |
| SPACON | PROC panel, commanding, verification steppers | SW; GAP: mission log, step timers |
| FDS / Ground Segment / Mission Manager | read-only situational use | — (correct; keep them off commanding) |
| Sim team | — | MIS if instructors coach from it: it shows TM-truth, not sim-truth — correct behaviour, but instructors need the instructor console beside it |

### 5.2 Planner (+ `/wide` wall)

| Position | What it gives them | Verdict + note |
|---|---|---|
| flight_director | plan timeline, constraint verdicts, /wide next-passes + countdowns | SW; GAP: no "plan pushed to console" state — FD cannot see whether the crew is *working* the displayed plan |
| eps_tcs | power-budget bar, SoC input on constraint check | SW pre-pass; SP: constraint check is point-in-time, not a SoC profile over the plan |
| aocs | pass geometry, imaging windows | SW; GAP: momentum constraint absent from the constraint breakdown |
| ttc | pass list, data-budget bar | SW; GAP: no per-pass downlink-volume vs backlog line (TC5 again, planning-time version) |
| payload_ops | imaging targets list, activity edit | SW |
| fdir_systems / propulsion | activity context | SW (viewer); burn activities should name the S11 upload pass explicitly |
| SPACON | /wide as room reference | SW — zero-interaction wall is the right call |
| FDS | the tool *is* partly theirs (orbit numbers from config) | SP: no FDS-products card (solution age/version — §4.2) |
| Mission Manager | plan vs achieved | GAP: nothing reports achievement against plan |
| Sim team | — | MIS: none. Planner is clean of truth leaks post-2026-08 |

### 5.3 rfsim radio page

| Position | What it gives them | Verdict |
|---|---|---|
| ttc | ground-side lock/RSSI/Eb-N0/BER, CLTU counters, VC status | SW — complements the spacecraft-side TTC page correctly |
| Ground Segment | the rack display (spectrum, constellation, eye) | SW; GAP: per-pass RF rollup (§4.3) |
| everyone else | ambient link truth | SW as a wall/secondary page |
| Sim team | Channel Model status + `#config-panel` channel controls | **MIS for operators** — move behind instructor auth (§4.3); for the sim team it is *the* channel tool and should be linked from the instructor console |

### 5.4 Delayed-TM viewer + `/dumps`

| Position | What it gives them | Verdict |
|---|---|---|
| fdir_systems | out-of-contact event/HK reconstruction, anomaly flags | SW — this is primarily their tool |
| eps_tcs / aocs / propulsion | per-SID series (SID 1/2/9), burn + eclipse reconstruction | SW; SP: anomaly flagging is 3σ/severity-based — **`limits.yaml` bands are not applied to played-back HK**, so a value that spent the whole autonomous period in yellow is invisible unless it also jumped |
| ttc / payload_ops | dump completeness (did my S15 move everything) | SP: no "expected vs received packet" accounting per dump |
| FD / SPACON | pass-report input | SW via tm_report |
| Sim team | grading evidence | SW |

### 5.5 Reports page (`tm_report`)

Covers ~60 % of the book's between-pass report (brief 07 §320). SW for all
positions' post-pass rhythm. Remaining GAPs, in the book's own list: procedure
execution history, command log with S1 outcomes, FDIR firing history, GO/NO-GO
record, free-text — i.e. the *decision* record; today the report is all telemetry.
Mission Manager: GAP (weekly rollup, §4.4).

### 5.6 Doc viewer / manual

SW: per-position `manual_sections` filtering already implements "the manual for my
console". GAP: procedures and manual are not cross-linked from alarms on every
surface (the causal-chain screen links procedures; the manual's subsystem chapter is
one more hop). Cheap: alarm detail → manual anchor.

### 5.7 Instructor console, scenario generator, config editor (sim team) — see §7.

---

## 6. Ranked gap list — the implementation queue

Ordered by (positions helped × question criticality ÷ cost). **[D]** = cheap
display/JS change; **[S]** = needs server/derived-parameter work; **[C]** = config
only.

| # | Change | Position(s) / question | Tool | What to build | Data source |
|---|---|---|---|---|---|
| G1 **[S]** | **Derived-parameter block v2** — extend `derived.yaml` beyond the three contact params: `eps.soc_at_eclipse_exit_pred`, `eps.time_to_soc_yellow`, `aocs.time_to_mom_70pct`, `obdh.time_to_buf_full`, `ttc.pass_data_capacity_bytes`, `prop.catbed_ready_eta`, `obdh.time_offset_ms`. One server-side projection helper (archive slope + limits.yaml + orbit state); render with the existing deviation-bar/SA idioms. | PT5, PT6, AO4, FS8, TC5, PR3, FS3 — five positions' entire L3 tier | MCS (+ /wide) | ~7 new 0x08xx params + display rows | orbit propagator, TM archive, limits.yaml — all existing |
| G2 **[D]** | **Bitmask decoders** for `aocs.fine_ready` 0x02C5 and `prop.arm_inhibit_mask` 0x0B15: render each documented bit as a labelled pass/fail chip (the bit legends are already in parameters.yaml descriptions — parse them or table them in annotations.yaml). | AO3, PR2, PL2, PL4 | MCS AOCS/PROP/PAYLOAD pages | chip row component reuse | parameters.yaml descriptions |
| G3 **[S]** | **"Will it fit" verdict**: one computed line `backlog (0x060B + 0x0312/13) ÷ (tier rate × time-to-LOS)` shown on TTC and PAYLOAD pages and in the planner pass rows. | TC5, PL5, FS8 | MCS + planner | single derived verdict (subset of G1) | store fill params + tier + contact times |
| G4 **[D]** | **Capture-readiness composite** on the payload page: the four NOM-002 gates (aocs.mode==5, 0x02C5==0, 0x0610, SoC>50, FPA<−10) as one GO/NO-GO card with per-gate chips. | PL2, PL4; also teaches cross-subsystem gating | MCS payload page | card driven by a small gate table in annotations.yaml | existing params |
| G5 **[S]** | **Predicted-vs-measured link overlay**: draw the link-budget prediction (range→FSPL→expected RSSI/Eb-N0) under the measured trace; add the RSSI-vs-elevation scatter (REQ-TTC-MCS-010/011). `link_budget_ladder.py` already computes the chain — port it into the MCS server as an endpoint. | TC2; Ground Segment | MCS TTC page | overlay + scatter panel | orbit range + rfsim config + measured TM |
| G6 **[S]** | **First-out alarm grouping**: when ≥N alarms arrive in a window, group by `fault_propagation.yaml` ancestry — "1 root cause, 14 consequent (collapse)". The graph exists and is unused for display (AUDIT §4.2 residue; the one big P7 item the new shell did not close). | FS5, FD1; every cascade scenario | MCS alarm drawer | grouping pass over journal + config graph | fdir/fault_propagation.yaml |
| G7 **[D]** | **GO/NO-GO context**: when a poll opens, show the matching row of the call matrix (who calls, who must respond, FD criticality note) and log the poll outcome to the command/console history. | FD3, FD7, all responders | MCS GO/NO-GO panel | render `10_desk_procedures.md` matrix (move it to a small yaml) | manual → config |
| G8 **[S]** | **Console/mission log**: an append-only per-session log surface — auto-entries (TC sent+verdict, alarm ack/shelve, procedure step complete, GO result, "Mark") + free text with position attribution; export into the pass report. Closes the SPACON log, FD7, and the report's missing decision record in one artefact. | SPACON, FD7, Mission Manager, §5.5 gaps | MCS + reports | new endpoint + persistent store + report section | new (small) |
| G9 **[D]** | **Loaded-vs-planned burn card**: PROP page card showing `prop.burn_dv_cmd/burn_time_cmd` beside the planner/FDS manoeuvre activity values, with a mismatch highlight. | PR4, AO9, FD | MCS PROP page | join TM vs planner activity export | existing both sides |
| G10 **[C/D]** | **Authority hygiene sweep** on positions.yaml: decide and fix the orphans found here — EPS_SET_MODE 83 (eps_tcs?), AOCS funcs 85–91 & 96 (aocs), PAYLOAD_SET_SHUTTER 119 (payload_ops), beacon TX 97/98 (ttc or fdir_systems), S9 (fdir_systems?). Then render each position card's authority list from config (partly done via NOT-PERMITTED states). | all seven; error-prevention | positions.yaml + MCS | config edits + a contract test that every catalogued func is owned by ≥1 non-FD position or explicitly FD-only | positions.yaml, tc_catalog.yaml |
| G11 **[D]** | **Limits on played-back dumps**: run decoded HK through `limits.yaml` in the delayed-TM viewer / reports and mark time-in-yellow/red per parameter per dump. | FS9, PT7, PR5 post-hoc | delayed-TM viewer, reports | apply existing limit checker to decoded series | limits.yaml + dump decoder |
| G12 **[D]** | **Blind-TC mode**: a TT&C-armed toggle that tags subsequent commands BLIND in history, shows them with a distinct chip until a TM-confirmed readback arrives, and requires FD GO to enable (desk gap #10, CON-004). | TC7, FD | MCS commanding | history flag + chip | existing cmd history |
| G13 **[MIS]** | **Move the rfsim channel controls behind the instructor**: gate `#config-panel` + `/api/channel`/`/api/pipeline` on instructor auth (or relocate onto the instructor console's intervention column); keep the operator page read-only rack instruments; drop the Channel Model truth panel from the operator view. | Ground Segment integrity; sim team | rfsim page + instructor console | auth gate + panel move | existing endpoints |
| G14 **[S]** | **Pass rollups**: per-pass RF summary (lock times vs predicted AOS, mean Eb/N0, FER) appended by rfsim; plan-vs-achieved line in reports; weekly Mission-Manager rollup from archive + planner exports. | Ground Segment §4.3, Mission Manager §4.4, FD5 | rfsim + reports | two small generators | counters already computed live |
| G15 **[D]** | **Battery/actuator health baseline card**: commissioning-baseline vs now for 0x0120/0x0121/0x0136, SA degradation 0x0124/5, wheel friction, leak rate 0x0B16 — the NOM-011 "flag >5 % degradation" view, precomputed. | PT7, PR6, Mission Manager | MCS trends + reports | baseline snapshot (config) + delta card | archive |

Ordering rationale: G1–G4 give every engineer position its missing L3 tier for one
server helper and two display components; G5–G9 each close a whole position's worst
SP/GAP; G10 is an afternoon of config with a test; G11–G15 round out post-pass and
room-role needs.

---

## 7. The sim team as a position

**Protecting:** the *learning objective* — the gap between what the trainees believe
and what the simulated world is actually doing, opened deliberately and closed
verifiably. The instructor's console is the anti-console: it must show precisely
what operators must never see.

**Must see (and operators must not):**

1. **Ground truth vs TM truth** — actual subsystem state regardless of what is
   downlinked, the onboard-clock-truth card, the real orbit. *Covered*: the truth
   dashboard + `?sub=` deep-dive; the 2026-08 provenance work made the boundary
   clean (FDIR panel labelled "instructor truth — not downlinked", SPEED moved
   here).
2. **Injected state** — which failures are active, with parameters, and what is
   scripted next. *Covered* for injection (typed failure form generated from
   `FAILURE_SPECS`, same ParamSpec source as the scenario generator — no drift);
   *partially covered* for "what's next": scenarios run, and breakpoints exist,
   but there is no **scenario timeline view** (elapsed/pending events with
   sim-time stamps) — the instructor discovers the next injection by reading the
   YAML. GAP.
3. **The channel** — see G13: rfsim channel state/controls are currently on an
   operator-reachable page. The instructor console should host the channel
   controls; today it does not link to rfsim at all. GAP + MIS.
4. **Student action visibility** — who commanded what, when, against which
   alarm/procedure state. The instructor event log shows *simulator* events
   (TC received, FDIR fired) in sim time, but TCs are not attributed to a console
   position: position gating happens in the MCS (`tc_manager.py`,
   `/ws?position=`), and that attribution never reaches the simulator/instructor.
   **GAP — the largest instructor gap**: for debrief the instructor cannot say
   "Power & Thermal sent the shed at T+142 s" without asking. Fix: MCS forwards
   its command-history records (position, TC, verdict, timestamp) to an
   instructor-readable endpoint; the debrief panel then merges injections,
   FDIR firings and *attributed* student commands into one timeline. G8's console
   log is the natural transport.
5. **Scenario outcome measures** — the debrief panel (new) fetches end-of-scenario
   stats; with (4) it becomes a real grading artefact. Also missing: expected-
   response annotations in scenario YAML ("correct action: CTG-007 within 10 min")
   so the debrief can auto-score — the scenario generator writes failures and
   timing but no success criteria. GAP, config-schema level.

**Tool coverage verdict.** Instructor console: SW for truth/injection, GAP for
timeline, channel, attribution. Scenario generator: SW (catalogue-driven, no
drift), GAP for success criteria. Config editor (confedit): SW for its scope
(validated edits, classification, history) — it is the *between-sessions* tool;
its one instructor-safety need is already met by validation + diffing. The
instructor-console↔scenario-generator↔rfsim triangle needs linking into one
sim-team surface; no new UI, just cross-links and the G13 relocation.

---

## 8. One-paragraph summary

The seven console positions are well-bounded in *authority* (positions.yaml is the
best artefact in the product; ten orphaned func_ids aside) and, since the 2026-08
shell, adequately served at Endsley L1/L2. The systematic deficit is L3: the ground
computes exactly three derived parameters, so every "how long until" question — SoC
at eclipse exit, momentum to desat threshold, buffer to full, data to fit, catbed to
ready — is mental arithmetic over data the servers already hold. One derived-
parameter block (G1) plus two display components (bitmask chips G2, verdict lines
G3/G4) closes most of it. The room roles need *products*, not consoles (pass
rollups, mission log, FDS solution card), and the sim team needs the three things
only it may have — the channel, the timeline, and attributed student actions —
moved fully behind the instructor boundary.

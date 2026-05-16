# 10. Desk-Based Procedure Assignments

This section assigns all commissioning and contingency procedures to specific
operator positions and defines GO/NO-GO call requirements for each.

## Operator Positions

| Position ID      | Console Name       | Subsystems              | S8 Func IDs      |
|------------------|--------------------|-------------------------|-------------------|
| flight_director  | Flight Director    | All (oversight)         | All               |
| eps_tcs          | Power & Thermal    | EPS, TCS                | 16-25, 40-49, 81-82 |
| aocs             | Flight Dynamics    | AOCS                    | 0-15              |
| ttc              | TT&C               | TTC                     | 63-78             |
| payload_ops      | Payload Operations | Payload                 | 26-39             |
| fdir_systems     | FDIR / Systems     | OBDH, FDIR, cross-system| 50-62, 80         |

---

## Commissioning Procedures by Desk

### Flight Director (overall coordination)

The Flight Director owns the commissioning timeline and authorises every
GO/NO-GO gate.  They do not execute individual steps unless marked below.

### eps_tcs — Power & Thermal

| Proc ID   | Title                        | Lead | Support     | FD GO/NO-GO |
|-----------|------------------------------|------|-------------|-------------|
| COM-001   | EPS Power System Checkout    | eps_tcs | fdir_systems | YES — before eclipse test, after power budget calc |
| COM-002   | TCS Verification             | eps_tcs | payload_ops | YES — before heater actuation tests |
| COM-102   | FPA Cooler Activation        | eps_tcs | payload_ops | YES — before cooler enable (power impact) |

**COM-001 — EPS Power System Checkout**
- **Lead:** eps_tcs (EPS engineer)
- **Support:** fdir_systems monitors OBC health during load transients
- **FD GO/NO-GO required:**
  1. Before payload transient load test (Step 7)
  2. After orbit-average power budget calculation — FD confirms positive margin
- **Key telemetry:** bus_voltage (0x0105), bat_soc (0x0101), power_gen (0x0107), power_cons (0x0106)
- **Announce on ops loop:** Bus voltage, SoC, power margin at each GO/NO-GO gate

**COM-002 — TCS Verification**
- **Lead:** eps_tcs (TCS engineer)
- **Support:** payload_ops for FPA baseline temperature check
- **FD GO/NO-GO required:**
  1. Before battery heater actuation test
  2. Before OBC heater actuation test
  3. After orbit thermal profile assessment
- **Key telemetry:** temp_obc (0x0406), temp_battery (0x0407), temp_fpa (0x0408), heater states (0x040A-0x040C)

**COM-102 — FPA Cooler Activation**
- **Lead:** eps_tcs (TCS engineer)
- **Support:** payload_ops monitors FPA temperature convergence
- **FD GO/NO-GO required:**
  1. Before cooler enable — confirm power budget supports +15W load
  2. After cooldown curve — confirm -15C setpoint reached and stable
- **Key telemetry:** fpa_temp (0x0601), bus_voltage (0x0105), bat_soc (0x0101)
- **Duration:** 45+ minutes for full cooldown — may span multiple passes

---

### aocs — Flight Dynamics

| Proc ID   | Title                        | Lead | Support     | FD GO/NO-GO |
|-----------|------------------------------|------|-------------|-------------|
| COM-005   | AOCS Sensor Calibration      | aocs | fdir_systems | YES — before star tracker loop enable |
| COM-006   | Reaction Wheel Commissioning | aocs | eps_tcs     | YES — before each wheel spin-up |
| COM-007   | AOCS Mode Transition Testing | aocs | all desks   | YES — before IDLE mode (loss of control), before FINE_POINT hold |

**COM-005 — AOCS Sensor Calibration**
- **Lead:** aocs (AOCS engineer)
- **Support:** fdir_systems for OBDH time reference
- **FD GO/NO-GO required:**
  1. Before star tracker loop enable — attitude may jump
  2. After gyro bias calibration — confirm bias values acceptable
- **Key telemetry:** rates (0x0204-0x0206), att_error (0x0217), st_status (0x0240), css_valid (0x0248)
- **Duration:** Gyro 5 min + mag 10 min + ST acquisition 2 min

**COM-006 — Reaction Wheel Commissioning**
- **Lead:** aocs (AOCS engineer)
- **Support:** eps_tcs monitors bus voltage during spin-up transients
- **FD GO/NO-GO required:**
  1. Before each wheel test — confirm attitude stable
  2. After all 4 wheels tested — confirm desaturation works
- **Key telemetry:** rw_speed (0x0207-0x020A), rw_temp (0x0218-0x021B), att_error (0x0217)

**COM-007 — AOCS Mode Transition Testing**
- **Lead:** aocs (AOCS engineer)
- **Support:** All desks observe during IDLE test (spacecraft will drift)
- **FD GO/NO-GO required:**
  1. Before IDLE mode hold — critical: spacecraft uncontrolled for 30s
  2. Before FINE_POINT mode — verify attitude ready for imaging operations
  3. After full mode matrix — confirm all transitions nominal
- **Announce on ops loop:** Mode transition + attitude error at each step

---

### ttc — TT&C

| Proc ID   | Title                        | Lead | Support     | FD GO/NO-GO |
|-----------|------------------------------|------|-------------|-------------|
| COM-004   | TT&C Link Budget Verification| ttc  | fdir_systems | YES — before redundant transponder test |

**COM-004 — TT&C Link Budget Verification**
- **Lead:** ttc (TT&C engineer)
- **Support:** fdir_systems for OBC TC counter verification
- **FD GO/NO-GO required:**
  1. Before redundant transponder switch — brief telemetry interruption
  2. After link budget comparison — confirm both transponders healthy
- **Key telemetry:** rssi (0x0502), link_margin (0x0503), ber (0x050C), carrier_lock (0x0510)

---

### fdir_systems — FDIR / Systems

| Proc ID   | Title                        | Lead | Support     | FD GO/NO-GO |
|-----------|------------------------------|------|-------------|-------------|
| COM-003   | OBDH Checkout                | fdir_systems | eps_tcs | YES — before NOMINAL mode transition |
| COM-008   | FDIR Configuration & Test    | fdir_systems | ALL desks | YES — before controlled fault injection (ALL positions on console) |

**COM-003 — OBDH Checkout**
- **Lead:** fdir_systems (OBDH engineer)
- **Support:** eps_tcs monitors power during mode transitions
- **FD GO/NO-GO required:**
  1. Before SAFE→NOMINAL mode transition
  2. After TC/TM counter verification
- **Key telemetry:** mode (0x0300), cpu_load (0x0302), reboot_count (0x030A), sw_image (0x0311)

**COM-008 — FDIR Configuration & Test**
- **Lead:** fdir_systems (FDIR engineer)
- **Support:** ALL desks must be on console — safe mode affects all subsystems
- **FD GO/NO-GO required:**
  1. Before controlled threshold lowering — ALL positions confirm ready
  2. Before safe mode recovery — confirm all subsystems responded as expected
  3. After recovery — confirm no residual effects on any subsystem
- **Critical:** This is the only commissioning procedure requiring ALL desks simultaneously.
  Each desk confirms their subsystem entered safe mode correctly and recovered.

---

### payload_ops — Payload Operations

| Proc ID   | Title                        | Lead | Support     | FD GO/NO-GO |
|-----------|------------------------------|------|-------------|-------------|
| COM-101   | Payload Power-On Sequence    | payload_ops | eps_tcs | YES — before power-on (power budget impact) |
| COM-103   | First Light Acquisition      | payload_ops | aocs | YES — before image capture (FINE_POINT confirmed) |
| COM-104   | Payload Radiometric Cal.     | payload_ops | aocs | YES — before cal site overpass imaging |

**COM-101 — Payload Power-On Sequence**
- **Lead:** payload_ops (Payload engineer)
- **Support:** eps_tcs confirms power budget and monitors bus during boot
- **FD GO/NO-GO required:**
  1. Before payload power-on — eps_tcs confirms sufficient margin
  2. After self-test — payload_ops confirms firmware and interfaces nominal
- **Key telemetry:** payload_mode (0x0600), fpa_temp (0x0601), bus_voltage (0x0105)

**COM-103 — First Light Acquisition**
- **Lead:** payload_ops (Payload engineer)
- **Support:** aocs confirms FINE_POINT stability
- **FD GO/NO-GO required:**
  1. Before FINE_POINT transition — aocs confirms attitude ready
  2. Before image capture — confirm FPA temp, pointing, storage
  3. After preview downlink — payload_ops assesses image quality
- **Announce on ops loop:** "First light capture complete" with quality assessment

**COM-104 — Payload Radiometric Calibration**
- **Lead:** payload_ops (Calibration engineer)
- **Support:** aocs for FINE_POINT nadir-pointing at cal site
- **FD GO/NO-GO required:**
  1. Before dark frame acquisition — payload in correct state
  2. Before cal site imaging — AOCS pointing confirmed, cal site in view
- **Duration:** Multi-pass procedure — dark frames + cal site overpass + downlink

---

## Contingency Procedures by Desk

### eps_tcs — Power & Thermal Contingencies

| Proc ID       | Title                            | Lead     | Support      | FD GO/NO-GO | Time-Critical |
|---------------|----------------------------------|----------|--------------|-------------|---------------|
| CON-002       | EPS Safe Mode Recovery           | eps_tcs  | all desks    | YES — each load restore step | No (recovery paced) |
| CON-008       | Solar Array Degradation          | eps_tcs  | aocs         | YES — if payload duty reduction needed | No |
| CON-019       | Progressive Load Shedding        | eps_tcs  | all desks    | YES — at each shed level | YES — eclipse countdown |
| CON-020       | Solar Panel Loss Response        | eps_tcs  | aocs         | YES — if attitude optimisation needed | No |
| PROC-EPS-OFF-001 | Overcurrent Response          | eps_tcs  | affected desk| YES — before OC flag reset | YES — 30s stability check |
| PROC-EPS-OFF-002 | Undervoltage Load Shedding    | eps_tcs  | all desks    | YES — each shed/restore step | YES — bus declining |
| PROC-EPS-OFF-003 | Battery Cell Failure          | eps_tcs  | fdir_systems | YES — before eclipse entry if margin negative | YES — eclipse timing |
| CON-006       | Thermal Limit Exceedance         | eps_tcs  | affected desk| YES — before heater actuation or payload OFF | No (15-min monitor) |
| CON-003       | Payload/FPA Thermal Anomaly      | eps_tcs  | payload_ops  | YES — before payload restart | No (cooldown timer) |

**Key coordination pattern:** eps_tcs leads all power/thermal contingencies. During
load shedding (CON-019, PROC-EPS-OFF-002), every affected desk must acknowledge
before their equipment is shed, and FD authorises each level.

**Progressive Load Shed callouts (CON-019):**
1. SoC < 50%: eps_tcs → payload_ops: "Payload OFF." FD GO.
2. SoC < 35%: eps_tcs → payload_ops: "FPA Cooler OFF." FD GO.
3. SoC < 25%: eps_tcs → ttc: "TX OFF — loss of downlink." FD GO.
4. SoC < 15%: eps_tcs → aocs: "Wheels OFF — loss of pointing." FD GO.
5. Eclipse exit: Restore in reverse order, 5-min spacing, FD GO at each step.

---

### aocs — Flight Dynamics Contingencies

| Proc ID       | Title                            | Lead | Support      | FD GO/NO-GO | Time-Critical |
|---------------|----------------------------------|------|--------------|-------------|---------------|
| CON-001       | AOCS Anomaly Recovery            | aocs | eps_tcs      | YES — if 2+ wheels failed | YES — attitude degrading |
| CON-007       | RW Bearing/Speed Anomaly         | aocs | eps_tcs      | YES — before wheel disable | YES — attitude risk |
| CON-021       | AOCS Sensor Cascade Failure      | aocs | payload_ops  | YES — fallback mode decision | YES — sensor switching |
| CON-022       | Reaction Wheel Stuck Recovery    | aocs | fdir_systems | YES — before recovery attempt | No (2-orbit assessment) |
| PROC-AOCS-OFF-001 | Star Tracker Failure         | aocs | payload_ops  | YES — if COARSE_SUN entered | YES — ST2 boot 60s |

**Key coordination pattern:** aocs leads all attitude contingencies. If pointing
degrades below imaging threshold, payload_ops is notified to suspend operations.
eps_tcs is consulted for power margin before enabling redundant sensors.

**Star tracker failure callouts (PROC-AOCS-OFF-001):**
1. aocs: "ST1 failure confirmed. Powering ST2." — announce on ops loop
2. aocs: "ST2 booting, 60s to acquisition." — FD acknowledges
3. If ST2 fails: aocs → FD: "Dual ST failure. Requesting COARSE_SUN fallback." FD GO required.
4. aocs → payload_ops: "Imaging suspended — insufficient pointing accuracy."

---

### ttc — TT&C Contingencies

| Proc ID       | Title                            | Lead | Support      | FD GO/NO-GO | Time-Critical |
|---------------|----------------------------------|------|--------------|-------------|---------------|
| CON-004       | TT&C Link Loss Recovery          | ttc  | fdir_systems | YES — before blind commands | YES — pass window |
| PROC-TTC-OFF-001 | BER Anomaly Investigation    | ttc  | eps_tcs      | YES — before xpdr switch | No |
| PROC-TTC-OFF-002 | No TM at Pass Start          | ttc  | fdir_systems | YES — at each blind command step | YES — pass window |
| PROC-TTC-OFF-003 | GS Antenna Failure           | ttc  | FD directly  | YES — alternate station coordination | YES — contact schedule |

**Key coordination pattern:** ttc leads all RF/link contingencies. Blind command
sequences require FD authorisation at each step. Link loss escalation to EMG-004
after 2 consecutive failed passes.

**No-TM-at-AOS callout sequence (PROC-TTC-OFF-002):**
1. ttc: "No TM received at AOS+60s. GS confirms nominal."
2. ttc → FD: "Request GO for blind HK." FD GO.
3. ttc: "Blind HK sent. Waiting 30s."
4. If no response: ttc → FD: "Request GO for blind xpdr switch." FD GO.
5. If still no response: ttc → FD: "Request GO for blind PA cycle." FD GO.
6. If still no response: ttc → FD: "Request GO for antenna deploy (IRREVERSIBLE)." FD GO required (critical decision).
7. After 2 failed passes: ttc → FD: "Escalating to EMG-004 Loss of Communication."

---

### payload_ops — Payload Contingencies

| Proc ID       | Title                            | Lead | Support      | FD GO/NO-GO | Time-Critical |
|---------------|----------------------------------|------|--------------|-------------|---------------|
| CON-003       | Payload/FPA Thermal Anomaly      | payload_ops | eps_tcs | YES — before payload restart | No (cooldown) |
| PROC-PLI-OFF-001 | Corrupted Image Handling     | payload_ops | fdir_systems | NO — routine (report to FD after) | No |
| PROC-PLI-OFF-002 | Memory Segment Failure       | payload_ops | fdir_systems | YES — if capacity < 50% | No |

**Key coordination pattern:** payload_ops handles payload-specific anomalies.
FPA thermal issues require eps_tcs to verify cooler power and TCS thermal state.
Memory failures may require mission replanning if capacity is significantly reduced.

---

### fdir_systems — OBDH / Systems Contingencies

| Proc ID       | Title                            | Lead | Support      | FD GO/NO-GO | Time-Critical |
|---------------|----------------------------------|------|--------------|-------------|---------------|
| CON-005       | OBC Watchdog Reset Recovery      | fdir_systems | eps_tcs | YES — if repeated resets | YES — reboot loop risk |
| PROC-OBC-OFF-001 | Boot Loader Recovery         | fdir_systems | all desks | YES — at each recovery step | YES — boot inhibit timing |
| PROC-OBC-OFF-002 | CAN Bus Failure Switchover   | fdir_systems | all desks | YES — before bus switchover | YES — 10s HK gap |
| PROC-OBC-OFF-003 | OBC Redundancy Switchover    | fdir_systems | all desks | YES — CRITICAL: zero redundancy after | YES — 60s TM gap |
| PROC-OBDH-OFF-002 | OBC Bootloader Recovery     | fdir_systems | eps_tcs | YES — before image reload | YES — multi-contact |
| PROC-OBDH-OFF-003 | CAN Bus Isolation            | fdir_systems | all desks | YES — before bus switchover | YES — 10s HK gap |
| PROC-CON-011  | GPS Time Sync & AOCS Recovery    | fdir_systems | aocs | YES — if time jump > 5s | YES — AOCS may reset |

**Key coordination pattern:** fdir_systems leads all OBDH contingencies. OBC and
CAN bus failures affect ALL subsystems, so every desk must be on console and
confirm their subsystem status after recovery. OBC switchover is the most critical
— it leaves zero OBC redundancy.

**OBC Redundancy Switchover callouts (PROC-OBC-OFF-003):**
1. fdir_systems → FD: "Requesting OBC switchover to Unit B. This eliminates OBC redundancy." FD GO required.
2. fdir_systems: "Switchover commanded. Expect 30-60s TM gap."
3. ALL desks: "Subsystem status check." Each desk confirms their subsystem nominal on OBC-B.
4. fdir_systems → FD: "All subsystems nominal on OBC-B. Zero OBC redundancy remaining."

---

## MCS Interface Gaps for Contingency Support

The following MCS features are **missing or insufficient** for proper contingency
procedure execution:

### Critical Gaps

| # | Gap | Impact | Affected Procedures |
|---|-----|--------|---------------------|
| 1 | **No eclipse countdown timer** | Load shed procedures need operators to know time to eclipse exit/entry | CON-019, PROC-EPS-OFF-002, PROC-EPS-OFF-003 |
| 2 | **No load shed level indicator** | Progressive load shedding has 4 levels — no visual state | CON-019, PROC-EPS-OFF-002 |
| 3 | **No cross-subsystem impact alerts** | When one subsystem fails, operators of dependent subsystems are not alerted | All OBC/CAN contingencies, load shedding |
| 4 | **No procedure-step countdown** | Wait steps (30s, 60s, 120s) show no remaining time | All procedures with wait_s steps |
| 5 | **No wing deployment status display** | Wing status (0x0144) not shown on any tab | CON-008, CON-020 |
| 6 | **No persistent command audit log** | Audit trail lost on server restart | All contingencies (post-incident review) |
| 7 | **No alarm severity/date filter** | Cannot filter alarm journal by severity or time range | All contingencies requiring alarm triage |
| 8 | **No per-position procedure restrictions** | Any position can execute any procedure | All — risk of wrong desk executing wrong procedure |
| 9 | **No "contingency active" banner** | No visual indication to all operators that a contingency is in progress | All contingencies |
| 10 | **No blind command tracking** | No special mode for commands sent without TM confirmation | CON-004, PROC-TTC-OFF-002 |

### Recommended Priority

**P1 (implement now):** #1 (eclipse countdown), #2 (load shed indicator), #9 (contingency banner)
**P2 (implement soon):** #3 (cross-subsystem alerts), #5 (wing status), #7 (alarm filters)
**P3 (implement later):** #4 (step countdown), #6 (persistent audit), #8 (procedure RBAC), #10 (blind cmd mode)

---

## GO/NO-GO Call Matrix

| Situation | Who Calls | Who Must Respond | FD Decision |
|-----------|-----------|------------------|-------------|
| Load shed level increase | eps_tcs | affected desks | FD authorises each level |
| Subsystem mode change | lead desk | FD | FD authorises |
| Blind command send | ttc | FD | FD authorises each blind TC |
| OBC switchover | fdir_systems | ALL desks | FD authorises (CRITICAL) |
| AOCS fallback mode | aocs | payload_ops, FD | FD authorises mode change |
| Payload suspend/resume | payload_ops or eps_tcs | FD | FD authorises |
| Transponder switch | ttc | FD | FD authorises (TM interruption) |
| FDIR threshold change | fdir_systems | ALL desks | FD authorises (test only) |
| Antenna deployment | ttc | FD | FD authorises (IRREVERSIBLE) |
| Recovery from safe mode | fdir_systems | ALL desks | FD authorises each restore step |
| Commissioning phase gate | lead desk | FD | FD declares GO for next phase |

---

## Emergency Escalation Paths

| From Contingency | Escalation Trigger | Emergency Procedure |
|------------------|--------------------|---------------------|
| CON-019 (Load Shed) | SoC < 10% or bus < 25V | EMG-001 Total Power Failure |
| CON-004 (Link Loss) | 2 consecutive failed passes | EMG-004 Loss of Communication |
| CON-001 (AOCS Anomaly) | 3+ wheel failure or rate > 5 deg/s | EMG-005 Loss of Attitude |
| CON-006 (Thermal) | Red limit breach (any zone) | EMG-002 Thermal Runaway |
| CON-005 (OBC Watchdog) | 3+ resets in 1 orbit | EMG-003 OBC Reboot (evaluate switchover) |
| PROC-OBC-OFF-003 (Switchover) | OBC-B also fails | EMG-003 → EMG-001 cascade |

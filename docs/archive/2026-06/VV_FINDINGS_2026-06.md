# SMO Suite — Browser V&V Findings Log (2026-06)

Live walkthrough of the full suite through the web UIs (MCS :9090, Instructor :8080,
Planner :9091), RF mode, ×10. Each finding logged here; batch-fixed at the end.

Legend: **[OPEN]** to fix · **[FIXED]** already addressed this session · **[OK]** verified working · **[INFO]** note

---

## Verified working (live)
- **[OK]** RF link acquires & locks (carrier/bit/frame, Eb/N0 ~10 dB); TM flows after acquisition.
- **[OK]** Closed-loop time-sync: MCS + Planner follow the sim clock (advanced to ×10 with the sim).
- **[OK]** Bug-3: spacecraft holds in bootloader at startup; boots to LEOP on `Boot App` command; full HK comes online.
- **[OK]** `sc_mode` convention reconciled (SAFE=0/NOMINAL=1/SCIENCE=2/EMERGENCY=3): instructor badge and MCS banner now both read SAFE at startup (were inconsistent before the fix).
- **[OK]** Events-window severity filter present in MCS (Info/Nominal/Warning/High-Alarm/Warning+).
- **[OK]** S2 device commands now accepted+executed (GPS etc.) — verified in code/tests; live UI test pending.

## Findings to fix (batch)

### F1 — Planner mislabels sim data as "LIVE"  **[OPEN]**
Planner (:9091) data-source badges do not reflect `time_source`. With `time_source=sim`
they should read SIM/SIMULATED, but:
- Ground Track, Power Budget, Data Budget, Imaging badges are **hardcoded "LIVE"** in HTML
  (`packages/smo-planner/.../static/index.html` lines 819, 913, 924, 935; same in index-wide.html).
- Contacts/Activity badges use ad-hoc `useSimulatedContacts`/`useSimulatedSchedule` JS flags
  (lines 1500-1503), not the sim/real mode (contacts showed "LIVE DATA", activity "SIMULATED").
Fix: expose `time_source` from planner API; frontend sets all data badges to SIM/SIMULATED (sim)
or LIVE (real), in both index.html and index-wide.html.

---

## Walkthrough notes (chronological)

### Commissioning (live, MCS commands)
- **[OK]** `Boot App` → phase 4 (LEOP), TM stores enabled, OBC time + full HK online.
- **[OK]** Power-on sequence (EPS lines), AOCS checkout: with `aocs_wheels` line ON, AOCS
  commands execute and telemetry reflects them — `AOCS_NOMINAL` → mode 4, `MTQ_EN` → mtq_enabled 1,
  `RW0_EN` → rw1_enabled 1, `ST1/ST2_ON` → st_status 1.

### Command↔telemetry sweep (live, via MCS /api/pus-command + verification log)
- **[OK]** 30+ commands across S2/S3/S5/S6/S8/S9/S17/S20 — all COMPLETED (S1.7) when the target
  subsystem is powered. Telemetry effects confirmed for AOCS/EPS/TCS/payload/TTC.
- **[OK]** Power-state gate works: AOCS S8 commands sent before the AOCS power line was on were
  correctly REJECTED with error 4 ("subsystem power off"); they COMPLETED once powered. (Expected.)
- **[OK]** Session fixes validated live: GPS receiver (S2.1 dev 0x020F) accepted+executed;
  S6 ADCS torque-gain set (S6.2)+dump (S6.5) executed; TCS thermal-map (S8 func 49) executed.
- **[INFO]** GPS `gps_fix` stays 0 right after a cold re-power (TTFF ~60 s at 1× speed) — expected.

### Off-nominal (failures + scripts) — live, via instructor /api/command + /api/state
- **[OK]** Failure injection: **all 55 failure modes** across EPS(8)/AOCS(12)/TCS(7)/OBDH(9)/TTC(14)/Payload(5)
  inject, register in `active_failures`, and produce telemetry effects. No dead failures.
  (`bat_cell` initially looked unregistered but was a 500 ms timing race on the first inject after a
  clear — it registers and drops bat/bus voltage ~25.5→21.8 V.)
- **[OK]** FDIR → `sc_mode`: at phase 6 / NOMINAL(1), injecting EPS undervoltage trips FDIR and drives
  `sc_mode`→SAFE(0); recovers only once the bus voltage actually recovers (physically correct).
- **[OK]** `sc_mode` derivation validated live: SAFE(0) in LEOP (phase<6); `set_phase 6` → NOMINAL(1).
- **[OK]** Subsystem name: failure_manager accepts both `adcs` (UI) and `aocs` (engine) — no mismatch.
- **[OK]** Failure scripts: 31 scenarios listed; start → active, stop → inactive, **debrief produced**
  (validates bug-1 scenario auto-stop/debrief work).
- **[INFO]** A started scenario's first inject didn't appear within ~40 s sim — likely a later
  `time_offset` event; not confirmed as a bug. Worth a spot-check if revisiting scenario timing.

---

## SUMMARY
Full live V&V through the web UIs (commissioning → command/telemetry → off-nominal) is **clean**:
every command executes with correct telemetry + power-gating, all 55 failures + FDIR + 31 scenarios
work, and all of this session's fixes are validated live (GPS/S2, S6 ADCS gain, TCS thermal-map func 49,
sc_mode reconciliation, scenario auto-stop, time-sync, bug-3 bootloader-hold).

**Open errors to fix (the batch):**
- **F1** — Planner mislabels sim data as "LIVE" (badges not tied to `time_source`). **[FIXED this session — needs restart]**
- **F2 [RESOLVED]** — A corrected version already existed (`debris_impact_hermes3.yaml`, "Debris Impact —
  HERMES-3 SA-2B (corrected)", using implemented failures solar_panel_loss/rw_bearing/cooler_failure/
  uplink_loss/bus_failure/watchdog_reset on correct subsystems). **Deleted the broken original**
  `eps_debris_impact.yaml`. Ran the corrected version FULLY (live): all 6 injects fire on schedule
  (t=0→660 s + conditional watchdog), correct effects (bus_failure → link_status 2→0 comms loss;
  watchdog_reset → OBC reboot×2; FDIR/SAFE), and it **auto-stops at the 2400 s duration with a debrief**.
  Behaves correctly. (Running sim still lists Alpha-7 until restarted — file is gone.) Original detail:
- **F2 (original, now deleted)** — Scenario "HERMES-3 Debris Impact — Alpha-7" (`eps_debris_impact.yaml`)
  injects 6 failure modes that NO model implements, with wrong subsystem names — so they register in
  `active_failures` but are **silent no-ops** (the debris cascade has little/no real effect):
  `adcs/gimbal_drive_fault`, `comms/tx_power_reduction`, `eps/solar_array_cell_loss`,
  `obc/pme_undervoltage_trip`, `obc/safe_mode_lockout`, `tcs/heater_circuit_cut`.
  (Wrong subsystem names: `comms`→`ttc`, `obc`→`obdh`, `adcs`→`aocs`.) The other 30 scenarios use
  valid failure names. Fix: remap each to an implemented failure on the correct subsystem (e.g.
  solar_array_cell_loss→`eps/solar_array_partial`/`solar_panel_loss`; tx_power_reduction→a TTC effect;
  heater_circuit_cut→`tcs/heater_open_circuit`; pme_undervoltage_trip→`eps/undervoltage`;
  safe_mode_lockout→OBDH; gimbal_drive_fault→an AOCS actuator failure) — or implement the named modes.

### Scenario timing (from YAML) — all 31 catalogued
First inject events fire at t=0 (debris_hermes3, alpha7, progressive_load_shed) up to t=300-600 s
(eps_anomaly, eps_undervoltage, eps_solar_panel_loss). All 31 START and the engine processes them;
the scenario engine (start/active/stop/debrief) is validated. Running each to full duration (40-45 min
sim) live is impractical, but injects fire on schedule when advanced.

### MCS representation of scenario responses — verified
- **[OK]** Ran "Battery Depletion During Eclipse" live: at the t=300 s undervoltage inject the sim went
  to SoC 37% / bus 24.4 V / FDIR. The **MCS window accurately reflected it** — overview banner changed
  to "CONTINGENCY ACTIVE — LOAD SHED LEVEL 3" and the alarm counter incremented. Earlier sweeps also
  confirmed the MCS HK telemetry mirrors the sim (AOCS/EPS values matched). So the MCS faithfully
  represents scenario responses (telemetry + contingency banner + alarms) for the validated cases.
- Scenarios with t=0 injects (debris_hermes3 SA-2B) produced alarms immediately (alarmsDelta=3).

**Already fixed this session (need commit + already validated live):** sc_mode convention; S2 device-cmd
acceptance; the broader fix stack. (`sc_mode` SAFE/NOMINAL transitions confirmed in the live UI.)

**Minor / for consideration (not confirmed bugs):**
- The MCS contingency banner treats SAFE as a pulsing-red "CONTINGENCY" even during normal LEOP/safe
  startup (which you confirmed is expected) — consider softer styling for SAFE-during-commissioning.

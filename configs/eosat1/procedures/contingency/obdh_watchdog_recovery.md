# CON-005: OBC Watchdog Reset Recovery
**Subsystem:** OBDH
**Phase:** CONTINGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Investigate and recover from an unexpected on-board computer watchdog reset, indicated by
an increment in `obdh.reboot_count` that was not commanded by ground. A watchdog reset may
be caused by software hang, CPU overload, single-event upset (SEU), or memory corruption.
This procedure verifies the OBC has re-initialised correctly, confirms no data loss, and
assesses whether the root cause is transient or systemic.

## Prerequisites
- [ ] TT&C link is active — `ttc.link_status` (0x0501) = 1
- [ ] Previous `obdh.reboot_count` (0x030A) value is known from last nominal pass
- [ ] Flight software version and patch level are documented on console
- [ ] Mass memory dump capability is available if data recovery is needed

## Procedure Steps

### Step 1 — Confirm Watchdog Reset Occurrence
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 25)
**Verify:** `obdh.reboot_count` (0x030A) — compare with last known value; confirm increment
**Verify:** `obdh.mode` (0x0300) — record current mode (expected 0=NOMINAL after clean reboot)
**Verify:** `obdh.cpu_load` (0x0302) — record value (expected < 70% post-boot)
**GO/NO-GO:** Reboot count has incremented and OBC is responsive — proceed with assessment

### Step 1b — Boot OBC into Application Image (if in Bootloader)
**Verify:** `obdh.sw_image` (0x0311) — if 0 (BOOTLOADER), the watchdog reset left the OBC in the bootloader and only boot/maintenance funcs are accepted.
**TC (conditional):** `OBC_BOOT_APP` (Service 8, Subtype 1, func_id 55) — issue if `obdh.sw_image` = 0
**Verify:** S1.1 acceptance + S1.7 execution complete within 15 s
**Verify:** `obdh.sw_image` (0x0311) = 1 (APPLICATION) within 15 s
**GO/NO-GO:** Application image running before any HK_ENABLE / OBC_SET_MODE TCs are issued.

### Step 2 — Verify OBC Nominal Post-Reboot State
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 25)
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) — if mode = 1 (SAFE), OBC detected an issue during re-init
**Verify:** `obdh.cpu_load` (0x0302) < 70% — elevated load may indicate boot loop or stuck process
**Action:** If OBC is in SAFE mode, assess whether to remain in SAFE or attempt NOMINAL restoration
**GO/NO-GO:** OBC in NOMINAL mode with CPU load < 70% — proceed. If in SAFE mode, proceed with caution.

### Step 3 — Request All Housekeeping Frames
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 25) — EPS
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 25) — AOCS
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 25) — OBDH
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 25) — TCS
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 25) — TT&C
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 25) — Payload
**Verify:** All six HK frames received within 30s
**Verify:** No subsystem is in an unexpected mode or reporting out-of-limit parameters
**GO/NO-GO:** All subsystems reporting nominal — proceed. Any anomalies trigger respective CON procedures.

### Step 4 — Verify Data Integrity and Onboard Storage
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 25)
**Verify:** Onboard time is consistent with ground reference (drift < 2s acceptable after reboot)
**Verify:** Stored telecommand queue status — confirm no commands were lost
**Verify:** Mass memory partition status — confirm science data and HK archives are intact
**GO/NO-GO:** Data integrity confirmed — proceed. If time drift > 5s, perform time synchronisation via `SET_PARAM(param_id=obdh.utc_correction, value=<delta>)`.

### Step 5 — Assess Root Cause Indicators
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 25)
**Action:** Review `obdh.cpu_load` (0x0302) trend — was CPU at 100% before reset?
**Action:** Review EPS data — was there a bus voltage dip that could cause brownout?
**Verify:** `eps.bus_voltage` (0x0105) > 27.0V (stable)
**Action:** Check radiation environment data if available from payload housekeeping
**GO/NO-GO:** If root cause appears transient (SEU, brief voltage dip): log and continue nominal. If systemic (repeated high CPU, memory error): proceed to Step 6.

### Step 6 — Restore Nominal Mode if OBC in SAFE
**TC:** `OBC_SET_MODE(mode=0)` (Service 8, Subtype 1)
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) within 10s
**Verify:** `obdh.cpu_load` (0x0302) remains < 70% after transition
**Verify:** `aocs.mode` (0x020F) — confirm AOCS has not been disrupted by mode change
**GO/NO-GO:** OBC in NOMINAL, all subsystems stable — recovery complete

## Off-Nominal Handling
- If OBC reboots again during this procedure: Command `OBC_SET_MODE(mode=1)` for SAFE, abort procedure, and escalate to ground software team for code review
- If `obdh.reboot_count` (0x030A) increments by > 3 within 1 hour: Suspect boot loop — command `OBC_SET_MODE(mode=2)` for EMERGENCY and execute EMG-003
- If CPU load remains > 90% post-reboot: Command `SET_PARAM(param_id=obdh.task_shed, value=1)` to disable non-critical background tasks
- If AOCS reverted to DETUMBLE after OBC reset: Execute CON-001 to restore pointing after OBC is confirmed stable

## Post-Conditions
- [ ] `obdh.mode` (0x0300) = 0 (NOMINAL)
- [ ] `obdh.cpu_load` (0x0302) < 70%
- [ ] `obdh.reboot_count` (0x030A) documented; no further unexpected increments
- [ ] All subsystem HK verified nominal
- [ ] Root cause classified as transient or systemic with follow-up action assigned

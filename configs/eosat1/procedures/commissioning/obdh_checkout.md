# COM-003: OBDH Checkout
**Subsystem:** OBDH
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Verify the On-Board Data Handling system. Confirm OBC operational mode, CPU
utilization, memory integrity, TC/TM packet counters, and watchdog timer function.
Validate mode transitions between SAFE and NOMINAL and confirm autonomous
housekeeping generation.

## Prerequisites
- [ ] COM-001 (EPS Checkout) and COM-002 (TCS Verification) completed
- [ ] Spacecraft in SAFE_POINT attitude mode
- [ ] `obdh.mode` (0x0300) = 0 (SAFE) or 1 (NOMINAL)
- [ ] Bidirectional VHF/UHF link active
- [ ] `eps.bat_soc` (0x0101) > 60%

## Procedure Steps

### Step 1 — Verify Current OBC Mode
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.mode` (0x0300) = 0 (SAFE) or 1 (NOMINAL) within 10s
**Action:** Record current mode. SAFE mode expected if not yet transitioned post-LEOP.
**GO/NO-GO:** OBC responding in known mode

### Step 2 — Check CPU Utilization
**TC:** `GET_PARAM(0x0302)` (Service 20, Subtype 1) — CPU load
**Verify:** `obdh.cpu_load` (0x0302) in range [10%, 50%] within 10s
**Action:** Record baseline CPU load. Nominal idle load is approximately 15-25%. Higher values indicate background tasks running.
**GO/NO-GO:** CPU load within acceptable range

### Step 3 — Verify Memory Status
**TC:** `GET_PARAM(0x0303)` (Service 20, Subtype 1) — RAM usage percentage
**TC:** `GET_PARAM(0x0304)` (Service 20, Subtype 1) — EDAC error count
**Verify:** RAM usage < 70% within 10s
**Verify:** EDAC single-bit error count reported (record value) within 10s
**Verify:** EDAC multi-bit error count = 0 within 10s
**GO/NO-GO:** Memory healthy with no uncorrectable errors

### Step 4 — Check TC/TM Packet Counters
**TC:** `GET_PARAM(0x0310)` (Service 20, Subtype 1) — TC received counter
**TC:** `GET_PARAM(0x0311)` (Service 20, Subtype 1) — TC rejected counter
**TC:** `GET_PARAM(0x0312)` (Service 20, Subtype 1) — TM generated counter
**Verify:** TC received counter > 0 (should match ground log) within 10s
**Verify:** TC rejected counter = 0 (no invalid commands sent) within 10s
**Verify:** TM generated counter > 0 and incrementing within 10s
**GO/NO-GO:** TC/TM counters consistent with operations to date

### Step 5 — Transition OBC to NOMINAL Mode
**TC:** `OBC_SET_MODE(mode=1)` (Service 8, Subtype 1)
**Verify:** `obdh.mode` (0x0300) = 1 (NOMINAL) within 15s
**Verify:** `obdh.cpu_load` (0x0302) remains < 60% within 30s
**Action:** NOMINAL mode enables full autonomous housekeeping, FDIR monitoring, and time-tagged command execution.
**GO/NO-GO:** OBC transitioned to NOMINAL mode successfully

### Step 6 — Verify Autonomous HK Generation
**Action:** In NOMINAL mode, OBC should generate periodic HK packets autonomously. Wait 60 seconds and verify TM counter increment without ground request.
**TC:** `GET_PARAM(0x0312)` (Service 20, Subtype 1) — TM counter before
**Action:** Wait 60 seconds. Do not send any TCs.
**TC:** `GET_PARAM(0x0312)` (Service 20, Subtype 1) — TM counter after
**Verify:** TM counter has incremented (autonomous HK active) within 10s
**GO/NO-GO:** Autonomous housekeeping generation confirmed

### Step 7 — Verify Watchdog Timer
**TC:** `GET_PARAM(0x0305)` (Service 20, Subtype 1) — watchdog status
**TC:** `GET_PARAM(0x0306)` (Service 20, Subtype 1) — last reset cause
**Verify:** Watchdog status = ENABLED (value 1) within 10s
**Verify:** Last reset cause = POWER_ON (value 0) — no unexpected resets within 10s
**GO/NO-GO:** Watchdog active, no anomalous resets recorded

### Step 8 — Test OBC Mode Reversion (NOMINAL to SAFE)
**TC:** `OBC_SET_MODE(mode=0)` (Service 8, Subtype 1)
**Verify:** `obdh.mode` (0x0300) = 0 (SAFE) within 15s
**Action:** Confirm all subsystems remain stable during mode change.
**TC:** `OBC_SET_MODE(mode=1)` (Service 8, Subtype 1)
**Verify:** `obdh.mode` (0x0300) = 1 (NOMINAL) within 15s
**GO/NO-GO:** Mode transitions bidirectional and clean

### Step 9 — Record OBDH Baseline
**Action:** Compile CPU load, memory usage, EDAC counters, TC/TM statistics, and watchdog status into OBDH Checkout Report. These form the baseline for anomaly detection during nominal operations.
**GO/NO-GO:** OBDH checkout complete — all parameters nominal

## Off-Nominal Handling
- If `obdh.cpu_load` > 60%: Investigate running tasks via `GET_PARAM(0x0307)` (task list). If unexpected task consuming resources, consider targeted task kill after Flight Director approval. Do not reboot unless CPU > 90%.
- If EDAC multi-bit errors > 0: Memory corruption detected. Attempt memory scrub via `SET_PARAM(0x0308, 1)`. If errors persist, identify affected memory region. May require fallback to redundant OBC.
- If OBC mode transition fails: Retry command once. If still fails, check TC acceptance status. Verify command authentication. If NOMINAL mode unreachable, continue commissioning in SAFE mode with manual HK requests.
- If watchdog status = DISABLED: Re-enable via `SET_PARAM(0x0305, 1)`. Investigate why watchdog was disabled — may indicate recovery from earlier fault.
- If TC rejected counter > 0: Review ground command log for any invalid sequences. Reset counter via `SET_PARAM(0x0311, 0)` after investigation.
- If last reset cause != POWER_ON: Investigate reset cause code. If brownout or watchdog reset occurred during LEOP, review corresponding telemetry timeline. Document in anomaly log.

## Post-Conditions
- [ ] OBC operating in NOMINAL mode
- [ ] CPU load baselined (expected 15-25% idle)
- [ ] Memory healthy — no uncorrectable EDAC errors
- [ ] TC/TM counters consistent with operations history
- [ ] Autonomous housekeeping generation confirmed
- [ ] Watchdog timer enabled and functional
- [ ] Mode transitions (SAFE <-> NOMINAL) verified
- [ ] OBDH Checkout Report generated
- [ ] GO decision for COM-004 (TT&C Link Verification)

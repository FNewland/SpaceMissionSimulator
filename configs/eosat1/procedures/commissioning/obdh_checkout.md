# COM-007: OBDH Checkout
**Subsystem:** OBDH
**Phase:** COMMISSIONING
**Revision:** 2.0 --- 2026-08c: `OBC_SET_MODE` encodings corrected throughout
(0 = NOMINAL, 1 = SAFE, 2 = EMERGENCY --- the old card had them BACKWARDS and
its final step left the OBC in SAFE while believing it had restored NOMINAL),
and the card now ends by explicitly restoring `obdh.mode` = 0.
**Approved:** Flight Operations Director

**Entry state:** COM-006 exit state --- AOCS in commanded NOMINAL, downlink at
LOW with the antenna deployed, OBC in NOMINAL (`obdh.mode` 0x0300 = 0).

## Purpose
Verify the On-Board Data Handling system. Confirm OBC operational mode, CPU
utilization, memory integrity, TC/TM packet counters, and watchdog timer function.
Validate mode transitions between SAFE and NOMINAL and confirm autonomous
housekeeping generation.

## Prerequisites
- [ ] COM-001 through COM-006 completed
- [ ] AOCS in the NOMINAL family (`aocs.mode` 0x020F in {4, 8})
- [ ] `obdh.mode` (0x0300) = 0 (NOMINAL) or 1 (SAFE)
- [ ] Bidirectional S-band link active
- [ ] `eps.bat_soc` (0x0101) > 60%

## Procedure Steps

### Step 1 — Verify Current OBC Mode
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) within 10s — the encoding is
0 = NOMINAL, 1 = SAFE, 2 = EMERGENCY (matching `OBC_SET_MODE` func 50)
**Action:** Record current mode. NOMINAL is the expected state; a SAFE reading
here means an earlier card left an excursion unrestored — investigate before
proceeding.
**GO/NO-GO:** OBC responding in NOMINAL

### Step 2 — Check CPU Utilization
**TC:** `GET_PARAM(param_id=0x0302)` (Service 20, Subtype 3) — CPU load
**Verify:** `obdh.cpu_load` (0x0302) in range [10%, 50%] within 10s
**Action:** Record baseline CPU load. Nominal idle load is approximately 15-25%. Higher values indicate background tasks running.
**GO/NO-GO:** CPU load within acceptable range

### Step 3 — Verify Memory Status
**TC:** `GET_PARAM(param_id=0x0303)` (Service 20, Subtype 3) — RAM usage percentage
**TC:** `GET_PARAM(param_id=0x0304)` (Service 20, Subtype 3) — EDAC error count
**Verify:** RAM usage < 70% within 10s
**Verify:** EDAC single-bit error count reported (record value) within 10s
**Verify:** EDAC multi-bit error count = 0 within 10s
**GO/NO-GO:** Memory healthy with no uncorrectable errors

### Step 4 — Check TC/TM Packet Counters
**TC:** `GET_PARAM(param_id=0x0310)` (Service 20, Subtype 3) — TC received counter
**TC:** `GET_PARAM(param_id=0x0311)` (Service 20, Subtype 3) — TC rejected counter
**TC:** `GET_PARAM(param_id=0x0312)` (Service 20, Subtype 3) — TM generated counter
**Verify:** TC received counter > 0 (should match ground log) within 10s
**Verify:** TC rejected counter = 0 (no invalid commands sent) within 10s
**Verify:** TM generated counter > 0 and incrementing within 10s
**GO/NO-GO:** TC/TM counters consistent with operations to date

### Step 5 — Confirm NOMINAL-Mode Services
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) within 10s
**Verify:** `obdh.cpu_load` (0x0302) remains < 60% within 30s
**Action:** NOMINAL mode carries full autonomous housekeeping, FDIR monitoring
and time-tagged command execution — confirmed by Step 6 without any mode
command (the OBC is already NOMINAL; commanding the mode you are in proves
nothing).
**GO/NO-GO:** NOMINAL-mode services confirmed

### Step 6 — Verify Autonomous HK Generation
**Action:** In NOMINAL mode, OBC should generate periodic HK packets autonomously. Wait 60 seconds and verify TM counter increment without ground request.
**TC:** `GET_PARAM(param_id=0x0312)` (Service 20, Subtype 3) — TM counter before
**Action:** Wait 60 seconds. Do not send any TCs.
**TC:** `GET_PARAM(param_id=0x0312)` (Service 20, Subtype 3) — TM counter after
**Verify:** TM counter has incremented (autonomous HK active) within 10s
**GO/NO-GO:** Autonomous housekeeping generation confirmed

### Step 7 — Verify Watchdog Timer
**TC:** `GET_PARAM(param_id=0x0305)` (Service 20, Subtype 3) — watchdog status
**TC:** `GET_PARAM(param_id=0x0306)` (Service 20, Subtype 3) — last reset cause
**Verify:** Watchdog status = ENABLED (value 1) within 10s
**Verify:** Last reset cause = POWER_ON (value 0) — no unexpected resets within 10s
**GO/NO-GO:** Watchdog active, no anomalous resets recorded

### Step 8 — Test OBC Mode Excursion (NOMINAL -> SAFE -> NOMINAL)
**TC:** `OBC_SET_MODE(mode=1)` (Service 8, Subtype 1, func_id 50) — SAFE (encoding: 1 = SAFE)
**Verify:** `obdh.mode` (0x0300) = 1 (SAFE) within 15s
**Action:** Confirm all subsystems remain stable during the excursion. The MCS
contingency banner (M8) raises while `obdh.mode` != 0 — expected.
**TC:** `OBC_SET_MODE(mode=0)` (Service 8, Subtype 1, func_id 50) — RESTORE NOMINAL (encoding: 0 = NOMINAL)
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) within 15s — THE CARD MUST NOT
END WITH THE OBC IN SAFE. The pre-2026-08c revision ended on `mode=1`
believing it was NOMINAL, and handed every subsequent card a SAFE OBC.
**GO/NO-GO:** Excursion bidirectional and clean, OBC restored to NOMINAL

### Step 9 — Record OBDH Baseline
**Action:** Compile CPU load, memory usage, EDAC counters, TC/TM statistics, and watchdog status into OBDH Checkout Report. These form the baseline for anomaly detection during nominal operations.
**GO/NO-GO:** OBDH checkout complete — all parameters nominal

## Off-Nominal Handling
- If `obdh.cpu_load` > 60%: Investigate running tasks via `GET_PARAM(param_id=0x0307)` (task list). If unexpected task consuming resources, consider targeted task kill after Flight Director approval. Do not reboot unless CPU > 90%.
- If EDAC multi-bit errors > 0: Memory corruption detected. Attempt memory scrub via `SET_PARAM(param_id=0x0308, value=1)`. If errors persist, identify affected memory region. May require fallback to redundant OBC.
- If OBC mode transition fails: Retry command once. If still fails, check TC acceptance status. Verify command authentication. If NOMINAL mode unreachable, continue commissioning in SAFE mode with manual HK requests.
- If watchdog status = DISABLED: Re-enable via `SET_PARAM(param_id=0x0305, value=1)`. Investigate why watchdog was disabled — may indicate recovery from earlier fault.
- If TC rejected counter > 0: Review ground command log for any invalid sequences. Reset counter via `SET_PARAM(param_id=0x0311, value=0)` after investigation.
- If last reset cause != POWER_ON: Investigate reset cause code. If brownout or watchdog reset occurred during LEOP, review corresponding telemetry timeline. Document in anomaly log.

## Exit state
- OBC in NOMINAL (`obdh.mode` 0x0300 = 0) — positively re-verified after the
  Step 8 excursion; watchdog enabled; nothing else changed by this card

## Post-Conditions
- [ ] OBC operating in NOMINAL mode (`obdh.mode` = 0)
- [ ] CPU load baselined (expected 15-25% idle)
- [ ] Memory healthy — no uncorrectable EDAC errors
- [ ] TC/TM counters consistent with operations history
- [ ] Autonomous housekeeping generation confirmed
- [ ] Watchdog timer enabled and functional
- [ ] Mode transitions (SAFE <-> NOMINAL) verified
- [ ] OBDH Checkout Report generated

**NEXT:** COM-008 (FDIR Configuration & Protection Functions)

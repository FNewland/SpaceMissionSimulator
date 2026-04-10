# COM-008: FDIR Configuration & Test
**Subsystem:** OBDH / All
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Verify that the Fault Detection, Isolation, and Recovery (FDIR) system is correctly
configured and operational. Confirm FDIR monitoring rules are active for all critical
subsystems. Test the autonomous safe mode trigger mechanism and verify that the
spacecraft correctly enters safe mode and recovers to nominal operations.

## Prerequisites
- [ ] COM-001 through COM-007 completed — all subsystems commissioned
- [ ] OBC in NOMINAL mode
- [ ] AOCS in SAFE_POINT mode (mode 2)
- [ ] `eps.bat_soc` (0x0101) > 70%
- [ ] Bidirectional VHF/UHF link active with link margin > 6 dB
- [ ] Flight Director has approved FDIR testing (involves intentional fault injection)
- [ ] Recovery procedure reviewed and ready

## Procedure Steps

### Step 1 — Verify FDIR Rules Are Active
**TC:** `GET_PARAM(0x0320)` (Service 20, Subtype 1) — FDIR master enable
**Verify:** FDIR master enable = ENABLED (value 1) within 10s
**TC:** `GET_PARAM(0x0321)` (Service 20, Subtype 1) — number of active FDIR rules
**Verify:** Active FDIR rules > 10 within 10s
**GO/NO-GO:** FDIR system enabled with rules loaded

### Step 2 — Review Critical FDIR Thresholds
**TC:** `GET_PARAM(0x0322)` (Service 20, Subtype 1) — EPS undervoltage threshold
**Verify:** EPS undervoltage threshold = 26.5V within 10s
**TC:** `GET_PARAM(0x0323)` (Service 20, Subtype 1) — battery SOC low threshold
**Verify:** Battery SOC low threshold = 20% within 10s
**TC:** `GET_PARAM(0x0324)` (Service 20, Subtype 1) — AOCS rate limit threshold
**Verify:** AOCS rate limit = 10.0 deg/s within 10s
**TC:** `GET_PARAM(0x0325)` (Service 20, Subtype 1) — OBC temperature high threshold
**Verify:** OBC temperature high threshold = +55C within 10s
**Action:** Record all FDIR thresholds. Confirm they match the operations database.
**GO/NO-GO:** FDIR thresholds correctly configured

### Step 3 — Verify FDIR Event Log
**TC:** `GET_PARAM(0x0330)` (Service 20, Subtype 1) — FDIR event count since boot
**Verify:** FDIR event count reported within 10s
**Action:** Record current count. Any events since launch should correspond to known LEOP activities (e.g., initial detumble triggering rate limit monitoring).
**GO/NO-GO:** FDIR event log reviewed — no unexplained events

### Step 4 — Test Safe Mode Trigger (Controlled Fault Injection)
**Action:** Temporarily lower the AOCS rate limit FDIR threshold to trigger a safe mode transition. This tests the full FDIR chain without stressing the spacecraft.
**TC:** `SET_PARAM(0x0324, 0.01)` (Service 20, Subtype 3) — lower rate limit to 0.01 deg/s
**Action:** Current body rates (~0.02-0.05 deg/s in SAFE_POINT) should exceed this threshold, triggering FDIR.
**Verify:** FDIR triggers within 30s — event logged
**Verify:** `aocs.mode` (0x020F) transitions to 1 (DETUMBLE) or 2 (SAFE_POINT) within 30s
**Verify:** `obdh.mode` (0x0300) transitions to 0 (SAFE) within 30s
**GO/NO-GO:** FDIR safe mode trigger confirmed operational

### Step 5 — Verify Safe Mode State
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `aocs.mode` (0x020F) = 1 (DETUMBLE) or 2 (SAFE_POINT) within 10s
**Verify:** `obdh.mode` (0x0300) = 0 (SAFE) within 10s
**Verify:** `payload.mode` (0x0600) = 0 (OFF) within 10s
**Action:** Confirm safe mode reconfiguration is correct: payload off, AOCS in safe mode, OBC in safe mode, heaters autonomous.
**GO/NO-GO:** Safe mode state nominal

### Step 6 — Restore FDIR Threshold
**TC:** `SET_PARAM(0x0324, 10.0)` (Service 20, Subtype 3) — restore rate limit to 10.0 deg/s
**Verify:** FDIR rate limit threshold = 10.0 deg/s within 10s
**GO/NO-GO:** FDIR threshold restored to operational value

### Step 7 — Recover from Safe Mode
**TC:** `OBC_SET_MODE(mode=1)` (Service 8, Subtype 1) — NOMINAL
**Verify:** `obdh.mode` (0x0300) = 1 (NOMINAL) within 15s
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 1) — SAFE_POINT
**Verify:** `aocs.mode` (0x020F) = 2 (SAFE_POINT) within 30s
**Verify:** `aocs.att_error` (0x0217) < 10.0 deg within 120s
**GO/NO-GO:** Recovery from safe mode successful

### Step 8 — Verify FDIR Event Was Logged
**TC:** `GET_PARAM(0x0330)` (Service 20, Subtype 1) — FDIR event count
**Verify:** FDIR event count incremented by 1 from Step 3 value within 10s
**TC:** `GET_PARAM(0x0331)` (Service 20, Subtype 1) — last FDIR event code
**Action:** Record event code. Should correspond to AOCS rate limit violation.
**GO/NO-GO:** FDIR event properly logged and traceable

### Step 9 — Verify No Residual Effects
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) in range [27.5V, 28.5V] within 10s
**Verify:** `eps.bat_soc` (0x0101) > 65% within 10s
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg within 60s
**Action:** Confirm all subsystems have returned to pre-test state. No degradation from FDIR test.
**GO/NO-GO:** Spacecraft fully recovered — FDIR test complete

## Off-Nominal Handling
- If FDIR does not trigger on lowered threshold: Check FDIR master enable. Verify rule is active via `GET_PARAM(0x0326)`. If rule not active, manually activate and retry. If FDIR framework itself non-functional, escalate to OBDH engineer.
- If safe mode transition incomplete (partial reconfiguration): Manually command remaining subsystems to safe state. Investigate FDIR action sequence via event log. May indicate FDIR action table misconfiguration.
- If recovery from safe mode fails: Retry `OBC_SET_MODE(mode=1)` once. If OBC stuck in SAFE, power cycle OBC via `OBC_SET_MODE(mode=2)` (reset) then `OBC_SET_MODE(mode=1)`. If AOCS does not recover, attempt `AOCS_SET_MODE(mode=1)` then `AOCS_SET_MODE(mode=2)`.
- If FDIR event count increases unexpectedly during test: Other FDIR rules may be triggering. Check event log for additional events. Ensure test did not cascade into multiple fault detections. Review all FDIR rule interactions.
- If original threshold not successfully restored: Retry `SET_PARAM(0x0324, 10.0)`. If parameter write fails, upload via memory patch as contingency. Do not leave spacecraft with lowered threshold.

## Post-Conditions
- [ ] FDIR master enable confirmed active
- [ ] All FDIR thresholds verified against operations database
- [ ] Safe mode trigger tested and confirmed operational
- [ ] Safe mode state verified correct (payload off, AOCS/OBC safe)
- [ ] Recovery from safe mode demonstrated
- [ ] FDIR event logging confirmed functional
- [ ] FDIR threshold restored to operational value
- [ ] No residual effects from FDIR test
- [ ] FDIR Configuration Report generated
- [ ] Platform commissioning complete — GO for payload commissioning (COM-101)

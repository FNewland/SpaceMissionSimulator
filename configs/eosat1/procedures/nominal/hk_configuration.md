# PROC-NOM-004: Housekeeping Telemetry Configuration
**Subsystem:** OBDH / Housekeeping
**Phase:** NOMINAL
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Configure housekeeping telemetry collection intervals and parameter sets for the current
mission phase. Adjust HK reporting rates based on operational needs: high-rate reporting
during ground station contacts for real-time monitoring, and low-rate reporting during
eclipse or standby periods to conserve onboard buffer space and power. This procedure also
enables or disables specific Structure IDs (SIDs) to tailor the telemetry stream to the
subsystems of interest for the current operations timeline.

## Prerequisites
- [ ] OBDH in NOMINAL mode: `obdh.mode` (0x0300) = 0
- [ ] TTC link established: `ttc.link_status` (0x0501) = 1 (LOCKED)
- [ ] `ttc.link_margin` (0x0503) > 3.0 dB (sufficient margin for TM throughput)
- [ ] Current HK configuration documented in operations log
- [ ] No critical onboard autonomy sequences depending on current HK intervals
- [ ] FDIR/Systems operator at console

## Procedure Steps

### Step 1 --- Read Current HK Configuration
**TC:** `HK_REPORT_CONFIG` SID=1 (Service 3, Subtype 25) --- request EPS HK structure report
**TC:** `HK_REPORT_CONFIG` SID=2 (Service 3, Subtype 25) --- request AOCS HK structure report
**TC:** `HK_REPORT_CONFIG` SID=3 (Service 3, Subtype 25) --- request TCS HK structure report
**TC:** `HK_REPORT_CONFIG` SID=4 (Service 3, Subtype 25) --- request OBDH HK structure report
**TC:** `HK_REPORT_CONFIG` SID=5 (Service 3, Subtype 25) --- request TTC HK structure report
**TC:** `HK_REPORT_CONFIG` SID=6 (Service 3, Subtype 25) --- request Payload HK structure report
**Verify:** Configuration reports received for all 6 SIDs within 15 s
**Action:** Record current interval_s values for each SID:
  - SID 1 (EPS): expected 1.0 s (contact) / 10.0 s (standby)
  - SID 2 (AOCS): expected 4.0 s (contact) / 30.0 s (standby)
  - SID 3 (TCS): expected 10.0 s (contact) / 60.0 s (standby)
  - SID 4 (OBDH): expected 5.0 s (contact) / 30.0 s (standby)
  - SID 5 (TTC): expected 2.0 s (contact) / 10.0 s (standby)
  - SID 6 (Payload): expected 5.0 s (contact) / 60.0 s (standby)
**GO/NO-GO:** All SID configurations successfully retrieved and recorded.

### Step 2 --- Modify HK Collection Intervals
**TC:** `HK_SET_INTERVAL` SID=1, interval=N (Service 3, Subtype 31) --- set EPS interval
**TC:** `HK_SET_INTERVAL` SID=2, interval=N (Service 3, Subtype 31) --- set AOCS interval
**TC:** `HK_SET_INTERVAL` SID=3, interval=N (Service 3, Subtype 31) --- set TCS interval
**TC:** `HK_SET_INTERVAL` SID=4, interval=N (Service 3, Subtype 31) --- set OBDH interval
**TC:** `HK_SET_INTERVAL` SID=5, interval=N (Service 3, Subtype 31) --- set TTC interval
**TC:** `HK_SET_INTERVAL` SID=6, interval=N (Service 3, Subtype 31) --- set Payload interval
**Action:** Set intervals to the appropriate rate profile:
  - **Contact profile:** SID1=1s, SID2=4s, SID3=10s, SID4=5s, SID5=2s, SID6=5s
  - **Standby profile:** SID1=10s, SID2=30s, SID3=60s, SID4=30s, SID5=10s, SID6=60s
  - **Eclipse profile:** SID1=5s, SID2=10s, SID3=30s, SID4=10s, SID5=5s, SID6=OFF
**Verify:** Command acceptance for each SET_INTERVAL within 5 s
**Note:** Interval values must be >= 1 s and a multiple of the onboard scheduling tick (1 s).
**GO/NO-GO:** All interval commands accepted.

### Step 3 --- Enable Required HK SIDs
**TC:** `HK_ENABLE` SID=1 (Service 3, Subtype 5) --- enable EPS periodic reporting
**TC:** `HK_ENABLE` SID=2 (Service 3, Subtype 5) --- enable AOCS periodic reporting
**TC:** `HK_ENABLE` SID=4 (Service 3, Subtype 5) --- enable OBDH periodic reporting
**TC:** `HK_ENABLE` SID=5 (Service 3, Subtype 5) --- enable TTC periodic reporting
**Verify:** Command acceptance for each ENABLE within 5 s
**Note:** SIDs 1, 2, 4, and 5 are always enabled during nominal operations.
**GO/NO-GO:** Core SIDs enabled and reporting.

### Step 4 --- Enable or Disable Optional SIDs Based on Phase
**Action:** For current operational context, enable/disable optional SIDs:
  - **If imaging planned:** `HK_ENABLE` SID=6 (Service 3, Subtype 5) --- Payload HK ON
  - **If no payload ops:** `HK_DISABLE` SID=6 (Service 3, Subtype 7) --- Payload HK OFF
  - **If thermal concern:** `HK_ENABLE` SID=3 (Service 3, Subtype 5) --- TCS HK ON at high rate
  - **If thermal nominal:** Set SID=3 to low rate via Step 2 standby profile
**Verify:** Command acceptance within 5 s for each enable/disable
**Verify:** `obdh.hktm_buf_fill` (0x0312) < 50 % --- buffer not overflowing with new config
**Note:** Enabling all SIDs at high rate simultaneously may exceed the onboard buffer
capacity during long non-contact periods. Monitor buffer fill level.
**GO/NO-GO:** Optional SIDs configured per current operations plan.

### Step 5 --- Verify New Configuration Active
**TC:** `HK_REPORT_CONFIG` SID=1 (Service 3, Subtype 25) --- re-read EPS config
**TC:** `HK_REPORT_CONFIG` SID=2 (Service 3, Subtype 25) --- re-read AOCS config
**Action:** Verify reported intervals match commanded values for all modified SIDs.
**Verify:** Periodic HK packets arriving at expected rates (observe TM packet count
`obdh.tm_pkt_count` (0x0307) incrementing at the expected aggregate rate).
**Verify:** `obdh.hktm_buf_fill` (0x0312) stable or decreasing (not accumulating backlog).
**GO/NO-GO:** New HK configuration confirmed active and stable.

### Step 6 --- Confirm Periodic Reports Arriving
**Action:** Monitor telemetry stream for 60 s to confirm periodic reports from all
enabled SIDs are arriving at the configured intervals.
**Verify:** SID 1 (EPS) packets received at configured rate +/- 1 s
**Verify:** SID 2 (AOCS) packets received at configured rate +/- 1 s
**Verify:** SID 4 (OBDH) packets received at configured rate +/- 1 s
**Verify:** SID 5 (TTC) packets received at configured rate +/- 1 s
**Verify:** SID 6 (Payload) packets received at configured rate (if enabled)
**Verify:** All received HK parameters contain plausible values (no stale or zero-filled data)
**Note:** Record new configuration in the operations log with effective time and rationale.

## Off-Nominal Handling
- If HK_SET_INTERVAL command rejected: Verify interval value is within valid range
  (1--3600 s). Check that the SID exists in the onboard HK structure definition. Re-send
  with corrected value.
- If HK packets not arriving after ENABLE: Check `obdh.mode` (0x0300) is still NOMINAL.
  The HK service may be inhibited if OBC is in SAFE mode. Verify the SID was not
  inadvertently disabled by another operator.
- If `obdh.hktm_buf_fill` (0x0312) > 80 %: The new configuration is generating more data
  than the downlink can handle. Reduce rates or disable non-critical SIDs immediately to
  prevent buffer overflow and data loss.
- If stale data in HK packets (values not changing): The parameter source subsystem may be
  off or unresponsive. Check subsystem mode and power status. Do not confuse a stable
  parameter with a stale one --- verify with a one-shot request (Service 3, Subtype 27).
- If configuration reverts after OBC reboot: The new intervals may not have been saved to
  non-volatile memory. Use `SAVE_CONFIG` command to persist the HK configuration if
  available, or re-apply after each boot.

## Post-Conditions
- [ ] HK collection intervals set to the target profile (contact/standby/eclipse)
- [ ] All required SIDs enabled and producing periodic reports
- [ ] Optional SIDs configured per current operations plan
- [ ] `obdh.hktm_buf_fill` (0x0312) stable and < 50 %
- [ ] Periodic reports confirmed arriving at ground at expected rates
- [ ] New HK configuration documented in operations log
- [ ] OBDH remains in NOMINAL mode: `obdh.mode` (0x0300) = 0

---
*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

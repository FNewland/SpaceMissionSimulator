# LEOP-006: Time Synchronisation
**Subsystem:** OBDH / Time Management
**Phase:** LEOP
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Perform initial spacecraft clock synchronisation after first contact during LEOP. Set the
OBC time reference to ground UTC to ensure accurate time-tagging of housekeeping and event
data from the start of the mission. During LEOP the onboard clock may have drifted
significantly since pre-launch upload (hours to days depending on launch delays and
separation timing). This procedure establishes the initial time reference that all
subsequent time-tagged operations, event logs, and science data depend upon.

## Prerequisites
- [ ] LEOP-001 First Acquisition complete --- bidirectional VHF/UHF link established
- [ ] TTC link locked: `ttc.link_status` (0x0501) = 1 (LOCKED)
- [ ] Ground UTC reference available (stratum-1 NTP or GPS-disciplined clock)
- [ ] OBC in SAFE or NOMINAL mode: `obdh.mode` (0x0300) = 0 or 1
- [ ] No time-tagged command sequences loaded onboard (fresh post-separation state)
- [ ] Flight Director has authorised time synchronisation

## Procedure Steps

### Step 1 --- Verify OBC Status and Communication
**TC:** `HK_REQUEST` SID=4 (Service 3, Subtype 25) --- request OBDH housekeeping
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) or 1 (SAFE) within 5 s
**Verify:** `obdh.cpu_load` (0x0302) < 80 %
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB
**Note:** Record `obdh.uptime` (0x0308) as initial reference. If uptime is near zero,
the OBC may have recently rebooted --- verify `obdh.reboot_count` (0x030A) before proceeding.
**GO/NO-GO:** OBC responsive and communication link healthy.

### Step 2 --- Request Onboard Time Report
**TC:** `TIME_REPORT_REQUEST` (Service 9, Subtype 2) --- request current OBC time
**Verify:** Time report telemetry (Service 9) received within 10 s
**Action:** Record the reported onboard CUC time as T_obc.
**Action:** Record ground UTC at the moment the time report TM packet is received as T_ground.
**Action:** Compute one-way light time (OWLT) from slant range: `ttc.range_km` (0x0509).
OWLT_ms = range_km / 299792.458 * 1000 (typically 2--8 ms for LEO, negligible for this
initial sync but record for completeness).
**GO/NO-GO:** Valid time report received with plausible CUC value.

### Step 3 --- Calculate Time Delta
**Action:** Compute clock offset:
  delta_t = T_obc - (T_ground - OWLT)
**Evaluate:** Record delta_t magnitude and sign.
**Evaluate:** If |delta_t| < 1.0 s --- clock is unexpectedly accurate post-separation.
Verify this is plausible (short launch-to-AOS time). If confirmed, SKIP to Step 6.
**Evaluate:** If |delta_t| >= 1.0 s and < 86400 s (24 hours) --- expected range for
LEOP. Proceed with correction.
**Evaluate:** If |delta_t| >= 86400 s --- possible OBC clock failure or epoch error.
HOLD and consult OBDH engineer before correcting.
**GO/NO-GO:** Delta within expected LEOP range (1 s to 24 h). Flight Director approves correction.

### Step 4 --- Send Time Update Command
**TC:** `SET_TIME` cuc_seconds=T_corrected (Service 9, Subtype 1) --- set OBC clock
**Action:** Compute T_corrected = T_ground_at_send + OWLT + processing_delay (typically 50 ms).
**Action:** The SET_TIME command applies the new time at the next PPS boundary onboard.
**Verify:** Command acceptance telemetry (Service 1, Subtype 1) received within 5 s
**Verify:** No command rejection (Service 1, Subtype 2) received
**Note:** If command rejected, verify CUC time value is within the onboard validation
range (epoch year +/- 1 year) and re-compute.
**GO/NO-GO:** Time update command accepted by OBC.

### Step 5 --- Verify Time Accuracy
**TC:** `TIME_REPORT_REQUEST` (Service 9, Subtype 2) --- request updated OBC time
**Verify:** Time report received within 10 s
**Action:** Record new onboard CUC time as T_obc_new.
**Action:** Compute residual delta:
  delta_residual = T_obc_new - (T_ground_verify - OWLT)
**Verify:** |delta_residual| < 1.0 s (initial LEOP accuracy threshold)
**Verify:** `obdh.mode` (0x0300) unchanged --- OBC was not disrupted by time step
**Note:** For LEOP, sub-second accuracy is sufficient. Fine synchronisation (< 0.1 s)
will be performed during nominal operations via PROC-NOM-005 (clock_sync.md).
**GO/NO-GO:** Residual delta within 1.0 s. Time synchronisation successful.

### Step 6 --- Request Confirmation Time Report and Validate Rate
**TC:** `TIME_REPORT_REQUEST` (Service 9, Subtype 2) --- second confirmation request
**Verify:** Time report received within 10 s
**Action:** Compare two successive time reports separated by known ground interval to
confirm onboard clock is incrementing at the correct rate (1 Hz +/- 100 ppm).
**Verify:** Clock rate error < 100 ppm (LEOP threshold; tighter threshold in nominal ops)
**Note:** If clock rate error exceeds 100 ppm, flag OBC oscillator for investigation
during commissioning phase. Record drift rate for trending.

### Step 7 --- Enable Event Time-Stamping and Log Results
**TC:** `HK_REQUEST` SID=4 (Service 3, Subtype 25) --- confirm OBDH state post-sync
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) or 1 (SAFE)
**Verify:** `obdh.uptime` (0x0308) is incrementing normally
**Action:** Log the following in the LEOP time-sync record:
  - Pre-correction delta: delta_t
  - Post-correction residual: delta_residual
  - Correction applied: YES / NO
  - Estimated clock rate error (ppm)
  - OBC uptime at time of sync
  - Any anomalies observed
**Action:** Confirm that all subsequent housekeeping packets carry correct UTC timestamps.

## Off-Nominal Handling
- If TIME_REPORT_REQUEST not acknowledged: Verify TTC link integrity. Re-send once. If
  still no response, the OBC time management service may not be initialised --- attempt
  an OBC soft reset via `OBC_SET_MODE(mode=1)` (Service 8, Subtype 3) and retry.
- If |delta_t| >= 86400 s: Possible epoch mismatch between ground and onboard CUC epoch.
  Verify both use the same CUC epoch (TAI 1958 or GPS 1980). Correct epoch offset on
  ground side if necessary before sending SET_TIME.
- If SET_TIME command rejected: Check onboard time validation bounds. The OBC may reject
  times that differ from the current onboard time by more than a configured threshold.
  Consult OBDH engineer for override procedure if necessary.
- If |delta_residual| > 1.0 s after correction: The correction may not have been applied
  at the expected PPS edge. Re-send SET_TIME with updated T_corrected value.
- If clock rate error > 100 ppm: OBC oscillator may be degraded or operating outside
  thermal qualification range. Check `obdh.temp` (0x0301) is within -20 C to +60 C.
  Schedule more frequent time syncs and flag for commissioning investigation.

## Post-Conditions
- [ ] Onboard clock synchronised to UTC within 1.0 s (LEOP accuracy threshold)
- [ ] Clock drift rate verified < 100 ppm
- [ ] Time synchronisation log entry completed for LEOP record
- [ ] Housekeeping telemetry packets carry correct UTC timestamps
- [ ] OBC mode unchanged (SAFE or NOMINAL)
- [ ] Ready to proceed with LEOP-007 Summary Checkout

---
*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

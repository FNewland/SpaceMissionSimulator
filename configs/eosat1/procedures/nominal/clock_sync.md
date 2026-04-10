# PROC-NOM-005: Spacecraft Clock Synchronization
**Subsystem:** OBDH
**Phase:** NOMINAL
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Synchronize the onboard OBDH clock with ground UTC to maintain time-tagging
accuracy for science data, housekeeping telemetry, and onboard autonomy
scheduling. Time accuracy is critical for image geolocation (< 1 ms required)
and correct execution of time-tagged command sequences. This procedure is
executed during each AOS window when clock drift exceeds the correction threshold.

## Prerequisites
- [ ] PROC-NOM-001 Pass Startup completed with all-GO declaration
- [ ] TTC link established: `ttc.link_status` (0x0501) = 1
- [ ] Ground station time reference synchronized to UTC (stratum-1 or GPS)
- [ ] No time-tagged command sequences executing onboard during update window
- [ ] No imaging session in progress: `payload.mode` (0x0600) != 2

## Procedure Steps

### Step 1 --- Request Current Onboard Time
**TC:** `HK_REQUEST` SID=4 (Service 3, Subtype 25) --- request OBDH housekeeping
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) within 5 s
**Note:** Record `obdh.uptime` (0x0308) value as T_obc (CUC seconds since epoch).
**Note:** Record ground UTC receipt time as T_ground (CUC seconds since epoch).
**Note:** Account for one-way light time (OWLT) and processing delay in
computation. Typical OWLT for LEO: 2-8 ms (negligible for 1 s threshold).

### Step 2 --- Compute Clock Drift
**Action:** Calculate time delta:
  delta_t = T_obc - (T_ground - OWLT)
**Evaluate:** If |delta_t| < 1.0 s --- drift is within tolerance; SKIP to Step 6.
**Evaluate:** If |delta_t| >= 1.0 s and < 10.0 s --- proceed with correction.
**Evaluate:** If |delta_t| >= 10.0 s --- anomalous drift. HOLD and investigate
  OBC oscillator health before correcting.
**GO/NO-GO:** Proceed with time set only if 1.0 s <= |delta_t| < 10.0 s.

### Step 3 --- Prepare Time Correction Command
**Action:** Compute corrected CUC time value:
  T_corrected = T_ground_at_send + OWLT + processing_delay
**Action:** Verify T_corrected is in the future relative to current onboard time
  (to avoid backward time jump if possible).
**Note:** The SET_TIME command applies the new time at the next PPS boundary
  onboard to minimize jitter.

### Step 4 --- Send Time Correction
**TC:** `SET_TIME` cuc_seconds=T_corrected (Service 9, Subtype 1) --- set OBC clock
**Verify:** Command acceptance telemetry received within 5 s
**Note:** The onboard clock will step to the new value at the next PPS edge.

### Step 5 --- Verify Clock Update
**TC:** `HK_REQUEST` SID=4 (Service 3, Subtype 25) --- request OBDH housekeeping
**Verify:** `obdh.uptime` (0x0308) consistent with corrected time within 5 s
**Action:** Compute residual delta:
  delta_residual = T_obc_new - (T_ground_verify - OWLT)
**Verify:** |delta_residual| < 0.1 s (correction applied successfully)
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) --- OBC unaffected by time step

### Step 6 --- Validate Uptime Consistency
**TC:** `HK_REQUEST` SID=4 (Service 3, Subtype 25) --- request second sample
**Verify:** `obdh.uptime` (0x0308) is incrementing at 1 Hz +/- 10 ppm
**Action:** Compare two successive uptime readings separated by known ground
  interval to confirm clock rate is nominal.
**Note:** If clock rate error exceeds 50 ppm, flag oscillator for trending.

### Step 7 --- Log Synchronization Result
**Log:** Record the following in the time-sync log:
  - Pre-correction delta: delta_t
  - Post-correction residual: delta_residual (or N/A if no correction needed)
  - Correction applied: YES / NO
  - OBC oscillator drift rate (ppm estimate)
  - Any anomalies observed

## Off-Nominal Handling
- If |delta_t| >= 10.0 s: Do NOT apply correction automatically. Report to
  Flight Director. Possible OBC oscillator degradation or reboot event. Check
  `obdh.uptime` for evidence of OBC reset (value near zero).
- If SET_TIME command not accepted: Verify TTC link integrity. Re-send once.
  If still rejected, check onboard command validation (time value out of range?).
- If |delta_residual| > 1.0 s after correction: Correction may not have been
  applied. Re-request HK and re-evaluate. Consider re-sending SET_TIME.
- If clock rate > 50 ppm drift: Schedule more frequent time sync passes. Flag
  oscillator for potential replacement in next maintenance window.
- If onboard time-tagged sequences affected: Verify all pending TC schedules
  are still valid after time step. Re-upload critical sequences if needed.

## Post-Conditions
- [ ] Onboard clock synchronized to UTC within 0.1 s (or confirmed already within tolerance)
- [ ] Clock drift rate verified nominal (< 50 ppm)
- [ ] Time synchronization log entry completed
- [ ] No disruption to onboard autonomy or time-tagged command execution
- [ ] OBDH remains in NOMINAL mode

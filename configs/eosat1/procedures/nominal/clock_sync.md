# NOM-008: Clock Synchronisation
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
- [ ] NOM-001 Pass Startup completed with all-GO declaration
- [ ] TTC link established: `ttc.link_status` (0x0501) = 2 (LOCKED)
- [ ] Ground station time reference synchronized to UTC (stratum-1 or GPS)
- [ ] No time-tagged command sequences executing onboard during update window
- [ ] No imaging session in progress: `payload.mode` (0x0600) != 2

## Procedure Steps

### Step 1 --- Request Current Onboard Time
**LABEL:** `STEP-1`
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- request Platform/OBDH housekeeping
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- request TT&C housekeeping, for the slant range
**TC:** `REQUEST_TIME` (Service 9, Subtype 2) --- explicit TM(9,2) time report
**Verify:** `obdh.mode == 0` (0x0300, timeout 10 s) --- NOMINAL
**Note:** Record **`obdh.obc_time` (0x0309)** as T_obc --- the onboard CUC time in
seconds since epoch. This card used to record `obdh.uptime` (0x0308), which is
seconds since BOOT, not since epoch: a delta computed from it is the spacecraft's
age minus a UTC timestamp, and would have called every healthy clock anomalous.
Cross-check 0x0309 against the TM(9,2) report.
**Note:** Record ground UTC receipt time as T_ground (CUC seconds since epoch).
**Note:** Account for one-way light time and processing delay. Compute OWLT from
`ttc.range_km` (0x0509) in the SID 6 report: OWLT_ms = range_km / 299792.458 * 1000,
which is **3-22 ms** on this orbit and negligible against the 1 s threshold.

### Step 2 --- Compute Clock Drift
**Action:** Calculate time delta:
  delta_t = T_obc - (T_ground - OWLT)
**Note:** If |delta_t| < 1.0 s --- drift is within tolerance; no correction.
**Note:** If 1.0 s <= |delta_t| < 10.0 s --- proceed with the correction.
**Note:** If |delta_t| >= 10.0 s --- anomalous drift. HOLD and investigate the
OBC oscillator before correcting; take the NO-GO arm and go to the Off-Nominal
section rather than continuing.
**GO/NO-GO:** delta_t is in [1.0 s, 10.0 s) and a correction is authorised -> GO `STEP-3` | NO-GO `STEP-6`
**Note:** This is an operator fork, not a telemetry one: delta_t is computed on
the GROUND from T_obc, T_ground and the OWLT, so no on-board parameter can carry
it and a `**BRANCH:**` predicate would be a fiction. GO runs the correction
(Steps 3-5); NO-GO jumps straight to the rate check at Step 6, which is what
"SKIP to Step 6" meant in prose. Run flat --- which is all the old extractor
could do --- both arms execute and a clock that is already correct gets stepped
anyway.

### Step 3 --- Prepare Time Correction Command
**LABEL:** `STEP-3`
**Action:** Compute corrected CUC time value:
  T_corrected = T_ground_at_send + OWLT + processing_delay
**Action:** Verify T_corrected is in the future relative to current onboard time
  (to avoid backward time jump if possible).
**Note:** The SET_TIME command applies the new time at the next PPS boundary
  onboard to minimize jitter.

### Step 4 --- Send Time Correction
**TC:** `SET_TIME(cuc_seconds=T_corrected)` (Service 9, Subtype 1) --- set the OBC clock
**Note:** The argument was outside the backticks, so the extractor produced a
`SET_TIME` step with no argument and the wire packed cuc_seconds = 0.
**Verify:** Command acceptance telemetry received within 5 s
**Note:** The onboard clock will step to the new value at the next PPS edge.

### Step 5 --- Verify Clock Update
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- request Platform/OBDH housekeeping
**TC:** `REQUEST_TIME` (Service 9, Subtype 2) --- confirmation time report
**Verify:** `obdh.obc_time` (0x0309) consistent with the corrected time within 5 s
**Action:** Compute residual delta:
  delta_residual = T_obc_new - (T_ground_verify - OWLT)
**Verify:** |delta_residual| < 0.1 s (correction applied successfully)
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) --- OBC unaffected by time step

### Step 6 --- Validate Clock Rate
**LABEL:** `STEP-6`
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- request a second sample
**Verify:** `obdh.obc_time` (0x0309) incrementing at 1 Hz +/- 10 ppm. `obdh.uptime`
(0x0308) increments at the same rate and is a useful corroboration, but it counts
from BOOT, so it is a rate check only --- never an epoch.
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
  `obdh.uptime` (0x0308) for evidence of an OBC reset --- a value near zero means
  the clock delta is a reboot, not a drift.
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

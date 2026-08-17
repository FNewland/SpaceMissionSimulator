# NOM-008: Clock Synchronisation
**Subsystem:** OBDH
**Phase:** NOMINAL
**Revision:** 2.0
**Approved:** Flight Operations Director

## Purpose
Measure the offset between the onboard clock and ground (sim) UTC, and correct
it by ground command when it exceeds tolerance. The OBC runs a free-running
TCXO with a nominal drift of about **12 ppm --- roughly 1 second per day** --- so
a clock left alone drifts steadily and predictably. Time accuracy matters for
image geolocation, for correlating events across subsystems, and above all for
**S11 time-tagged commands**, which fire on the *onboard* clock: a clock that is
40 s fast runs the uploaded plan 40 s early in mission time.

This card corrects a **GROUND-referenced** clock. If `obdh.time_ref` (0x0A13)
reads 2 (GPS), the clock is GPS-disciplined and holds true time with no drift ---
there is nothing to correct, and an apparent offset in that state is a fault,
not drift (see Off-Nominal Handling). GPS synchronisation itself and its AOCS
side effects are covered by CTG-027.

## Prerequisites
- [ ] NOM-001 Pass Startup completed with all-GO declaration
- [ ] TTC link established: `ttc.link_status` (0x0501) = 2 (LOCKED)
- [ ] OBC running the APPLICATION image: `obdh.sw_image` (0x0311) = 1.
      `set_time` is not in the OBDH bootloader allowlist, so Step 4 is rejected
      outright while the spacecraft is in the bootloader.
- [ ] Ground time reference available (stratum-1 or GPS-disciplined). In the
      simulator that reference is **sim time**, not your desk clock --- the MCS
      clock is anchored on the simulator and is the correct reference.
- [ ] No S11 time-tagged sequence due to execute inside the update window: a
      time step moves every stored tag relative to mission time
- [ ] No imaging session in progress: `payload.mode` (0x0600) != 2

## Procedure Steps

### Step 1 --- Read the Onboard Clock and Its Time Reference
**LABEL:** `STEP-1`
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- Platform/OBDH housekeeping: the clock, its reference and the time since sync
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- TT&C housekeeping, for the slant range
**TC:** `REQUEST_TIME` (Service 9, Subtype 2) --- explicit TM(9,2) time report
**Verify:** `obdh.mode == 0` (0x0300, timeout 10 s) --- NOMINAL
**Note:** Record the onboard time as T_obc = `obdh.obc_time` (0x0309) +
`obdh.obc_time_fine` (0x0A12) / 65536. 0x0309 is the COARSE CUC second and
0x0A12 the sub-second in units of 1/65536 s (~15.26 us). Both are **CUC seconds
since J2000** (2000-01-01T12:00:00 UTC), never a Unix timestamp.
**Note:** Record `obdh.time_ref` (0x0A13): 0 = NONE (never set since the last
bootloader reset --- the clock is still near J2000), 1 = GROUND, 2 = GPS
(disciplined, no drift), 3 = HOLDOVER (was GPS, fix lost, drifting again).
**Note:** Record `obdh.time_since_sync_s` (0x0A14) --- onboard seconds since the
last successful sync. It saturates rather than wrapping; 4294967295
(0xFFFFFFFF) means NEVER SYNCED. The ground limit is yellow at 12 h.
**Note:** Record ground UTC at the moment the time report is received as
T_ground, expressed in the same CUC seconds since J2000.
**Note:** This card used to record `obdh.uptime` (0x0308) instead. Uptime is
seconds since BOOT, not since epoch: a delta computed from it is the
spacecraft's age minus a UTC timestamp, and would have called every healthy
clock anomalous. Uptime is still useful as a reboot indicator and as a rate
corroboration --- never as an epoch.
**Note:** Compute the one-way light time from `ttc.range_km` (0x0509) in the
SID 6 report: OWLT_ms = range_km / 299792.458 * 1000, which is **3-22 ms** on
this orbit and negligible against the 1 s threshold.
**Note:** Cross-check the TM(9,2) report against 0x0309. They arrive by
different routes --- a time-service reply and a housekeeping sample --- so a
disagreement larger than the SID 4 collection interval (8 s) means those two
disagree about the clock, which is a different fault from the clock being
wrong.

### Step 2 --- Assess the Offset Against Expected Drift
**Action:** Compute the clock offset:
  delta_t = T_obc - (T_ground - OWLT)
A POSITIVE delta_t means the onboard clock is FAST, i.e. ahead of mission time,
and onboard time tags therefore fire EARLY.
**Action:** Compute the drift the offset implies:
  drift_ppm = delta_t / `obdh.time_since_sync_s` (0x0A14) * 1e6
**Note:** About 12 ppm is nominal for this oscillator. Judge the offset against
the time since sync, not in isolation: 3 s after three days is a healthy clock;
3 s ten minutes after a sync is not.
**Note:** If `obdh.time_ref` (0x0A13) = 2 (GPS), expect delta_t ~ 0 --- a
GPS-disciplined clock is held to true time every tick. A non-zero offset in
that state means stale telemetry or a fault; do NOT step a disciplined clock.
**Note:** If `obdh.time_ref` (0x0A13) = 0 (NONE), the OBC has not been set since
its last bootloader reset and reads a date near 1 January 2000. That is a
reboot, not drift --- confirm with `obdh.uptime` (0x0308) and
`obdh.reboot_count` (0x030A), and expect a delta of years.
**Note:** If |delta_t| < 1.0 s --- within tolerance; no correction.
**Note:** If 1.0 s <= |delta_t| < 10.0 s --- proceed with the correction.
**Note:** If |delta_t| >= 10.0 s --- anomalous for a clock synced this shift.
HOLD, establish whether it is a reboot (time_ref = NONE) or genuine oscillator
degradation, and take the NO-GO arm rather than continuing.
**GO/NO-GO:** delta_t is in [1.0 s, 10.0 s), time_ref is 1 (GROUND) or 3 (HOLDOVER), and a correction is authorised -> GO `STEP-3` | NO-GO `STEP-6`
**Note:** This is an operator fork, not a telemetry one: delta_t is computed on
the GROUND from T_obc, T_ground and the OWLT, so no on-board parameter can
carry it and a `**BRANCH:**` predicate would be a fiction. The GO arm runs the
correction; the NO-GO arm runs the rate check at `STEP-6` only. Run flat ---
which is all a flat runner can do --- both arms execute and a clock that is
already correct gets stepped anyway.

### Step 3 --- Prepare the Time Correction
**LABEL:** `STEP-3`
**Action:** Compute the corrected time, in CUC seconds since J2000:
  T_corrected = T_ground_at_send + OWLT + processing_delay
**Action:** Confirm T_corrected is on the **J2000** epoch. A Unix timestamp here
is about 9.5e8 s too large and slams the clock some thirty years forward; the
spacecraft accepts it, and every timestamp and time tag afterwards is wrong.
**Action:** Confirm T_corrected is ahead of the current onboard time where
possible, so the clock steps forward rather than backward.
**Note:** `SET_TIME` carries whole CUC seconds (`cuc_seconds`, uint32). The
sub-second field is not commandable; sub-second accuracy comes from GPS
discipline, not from this card.
**Note:** The correction applies at the next PPS boundary onboard, which keeps
the step clean rather than smeared across a tick.

### Step 4 --- Send the Time Correction
**TC:** `SET_TIME(cuc_seconds=T_corrected)` (Service 9, Subtype 1) --- set the OBC clock
**Verify:** Command acceptance (Service 1, Subtype 1) received within 5 s
**Verify:** No command rejection (Service 1, Subtype 2) received
**Note:** The spacecraft raises event **`OBC_TIME_SET` (0x030C)** carrying the
SIGNED jump in seconds and the resulting reference, in the form
`OBC clock set by ground (S9,1): jump +12.345s, time_ref=GROUND`. Record that
jump: it is the spacecraft's own measurement of how far the clock was out, and
it should agree with your delta_t.
**Note:** Jumps smaller than the configured event threshold (1.0 s) are
suppressed, so a small correction may raise no event. Absence of the event is
not evidence that the command was ignored --- read 0x0309 and 0x0A14 instead.
**Note:** The argument must stay INSIDE the backticks. Written outside, the
extractor produces a `SET_TIME` step with no argument and the wire packs
cuc_seconds = 0 --- an epoch reset, not a synchronisation.

### Step 5 --- Verify the Correction
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- Platform/OBDH housekeeping
**TC:** `REQUEST_TIME` (Service 9, Subtype 2) --- confirmation time report
**Verify:** `obdh.time_ref == 1` (0x0A13, timeout 15 s) --- GROUND, i.e. the set was applied
**Verify:** `obdh.mode == 0` (0x0300, timeout 10 s) --- OBC unaffected by the time step
**Action:** Recompute the residual:
  delta_residual = T_obc_new - (T_ground_verify - OWLT)
**Verify:** |delta_residual| < 0.1 s (correction applied successfully)
**Note:** `obdh.time_since_sync_s` (0x0A14) must have restarted from ~0. If it
still reads 4294967295 or a large value, the clock was not set, whatever 0x0309
shows.
**Note:** Any S11 tags already onboard were computed against the OLD clock and
have just moved by delta_t in mission time. Re-check the onboard schedule and
re-upload anything time-critical.

### Step 6 --- Validate the Clock Rate
**LABEL:** `STEP-6`
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- second sample for the rate check
**Verify:** `obdh.mode == 0` (0x0300, timeout 10 s)
**Action:** Take two SID 4 samples separated by a known ground interval and
compute the rate error:
  drift_ppm = ((T_obc_2 - T_obc_1) - (T_ground_2 - T_ground_1)) / interval * 1e6
**Note:** Expect about 12 ppm (~1 s/day) on a healthy unit, with a few ppm of
per-unit spread. `obdh.uptime` (0x0308) increments at the same rate and is a
useful corroboration --- rate only, never an epoch.
**Note:** Above ~50 ppm, flag the oscillator for trending and shorten the sync
interval. Around 500 ppm (~43 s/day) is the signature of the instructor's
`clock_drift` failure injection rather than an ageing oscillator.
**Note:** A rate check is meaningless while `obdh.time_ref` (0x0A13) = 2 (GPS):
the clock is being held to true time, so what you measure is the discipline
loop, not the oscillator.

### Step 7 --- Log the Result
**Log:** Record the following in the time-sync log:
  - Pre-correction delta: delta_t, with `obdh.time_since_sync_s` at the time
  - `obdh.time_ref` before and after
  - Jump reported by event 0x030C (if raised)
  - Post-correction residual: delta_residual (or N/A if no correction needed)
  - Measured oscillator drift (ppm)
  - Any S11 schedule re-uploaded as a consequence
  - Any anomalies observed

## Off-Nominal Handling
- **`obdh.time_ref` (0x0A13) = 0 (NONE), clock reading year 2000:** the OBC
  rebooted and the bootloader reset the clock to J2000. This is not drift.
  Confirm with `obdh.uptime` (0x0308) near zero and `obdh.reboot_count`
  (0x030A) incremented, then run the correction --- the delta will be years,
  and that is expected. Booting the application image does not restore the
  clock; a reboot always means a re-sync.
- **`obdh.time_ref` = 3 (HOLDOVER):** GPS discipline was lost (event
  `OBC_TIME_REF_LOST`, 0x030E) and the clock has been free-running since. The
  offset is drift accumulated since the fix was lost. Correct by ground command
  here; restoring GPS discipline is CTG-027.
- **`obdh.time_ref` = 2 (GPS) but delta_t is non-zero:** a disciplined clock
  does not drift. Suspect stale telemetry (check the SID 4 age), a bad ground
  reference, or an epoch error in your arithmetic. Do NOT send `SET_TIME`: it
  drops the reference to GROUND and starts the drift again.
- **|delta_t| >= 10.0 s with `time_ref` = 1 (GROUND) and a recent sync:** report
  to the Flight Director. Possible oscillator degradation; check `obdh.temp`
  (0x0301) is inside -20 C to +60 C.
- **`SET_TIME` rejected:** check `obdh.sw_image` (0x0311) --- the command is not
  in the bootloader allowlist. Otherwise verify TTC link integrity and re-send
  once.
- **|delta_residual| > 1.0 s after the correction:** the correction may not have
  been applied. Re-request SID 4, check that `obdh.time_since_sync_s` restarted,
  and re-evaluate before sending a second `SET_TIME`.
- **AOCS drops to SAFE_BOOT after a clock change:** a time jump larger than 5 s
  applied through the GPS sync path forces AOCS to SAFE_BOOT (event 0x020F),
  because the attitude solution was computed against a clock that has moved.
  Recover per CTG-027.
- **Drift above 50 ppm:** schedule more frequent time syncs, flag the oscillator
  for trending, and record the measured ppm so a trend exists.

## Post-Conditions
- [ ] Onboard clock within 0.1 s of ground UTC (or confirmed already within tolerance)
- [ ] `obdh.time_ref` (0x0A13) = 1 (GROUND) after a ground set, unchanged otherwise
- [ ] `obdh.time_since_sync_s` (0x0A14) restarted from ~0 after a correction
- [ ] Oscillator drift measured and logged (nominally ~12 ppm, < 50 ppm)
- [ ] Onboard S11 schedule re-checked against the corrected clock
- [ ] Time synchronisation log entry completed
- [ ] No disruption to onboard autonomy or time-tagged command execution
- [ ] OBDH remains in NOMINAL mode

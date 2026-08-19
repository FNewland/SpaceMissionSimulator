# LEOP-006: Time Synchronisation
**Subsystem:** OBDH / Time Management
**Phase:** LEOP
**Revision:** 3.0 --- 2026-08c flow redesign. This ONE card now owns the whole
LEOP boot-then-sync logic, both paths: the GROUND set (S9.1) as the immediate
action, then GPS discipline (S8.1 func 80) once the receiver --- powered in
LEOP-007 --- holds a fix. The duplicate `SET_TIME` step that used to live in
LEOP-001 is gone; NOM-008 remains the routine-ops clock card.
**Approved:** Flight Operations Director

**Entry state:** LEOP-005 exit state --- COARSE_SUN sun hold, application image
running, clock UNSET (`obdh.time_ref` 0x0A13 = 0, counting from J2000), GPS
receiver ON (device 0x020F, LEOP-007 Step 4) and acquiring; HK SID 12 not yet
enabled.

## Purpose
Establish the spacecraft's time reference for the first time after separation.
Until this card is run, the OBC clock is **not merely inaccurate --- it has never
been set**. The bootloader starts the clock at CUC 0, which is the mission epoch
J2000 (2000-01-01T12:00:00 UTC), and `obdh.time_ref` (0x0A13) reads 0 (NONE).
Every housekeeping packet, every event and every stored-TM record produced
before this card carries a timestamp in the year 2000, and that is correct
behaviour, not a decoding fault.

The same is true after **every** OBC reboot: the bootloader resets the clock and
booting the application image does not restore it, so a reboot during LEOP means
running this card again. This procedure establishes the initial reference that
all subsequent time-tagged operations, event logs and science data depend upon.

## Prerequisites
- [ ] LEOP-001 First Acquisition complete --- bidirectional S-band link established
      AND the OBC in the APPLICATION image (`obdh.sw_image` 0x0311 = 1).
      `set_time` is NOT in the OBDH bootloader allowlist
      (`obdh_basic._BOOTLOADER_ALLOWED` admits only the boot/maintenance funcs),
      so Step 4 is rejected outright while the spacecraft is still in the
      bootloader.
- [ ] TTC link locked: `ttc.link_status` (0x0501) = 2 (LOCKED)
- [ ] Ground UTC reference available (stratum-1 NTP or GPS-disciplined clock)
- [ ] OBC in SAFE or NOMINAL mode: `obdh.mode` (0x0300) = 0 or 1
- [ ] No time-tagged command sequences loaded onboard (fresh post-separation
      state). Any tag uplinked before the clock is set was computed against a
      clock reading the year 2000 and will not fire as intended.
- [ ] GPS receiver powered (LEOP-007 Step 4) --- `S2_AOCS_GPS_RECEIVER` device
      0x020F ON. Path B below needs `aocs.gps_fix` (0x0274) >= 2; a cold start
      can take up to 15 minutes, which the ground path does not wait for
- [ ] Flight Director has authorised time synchronisation

## Procedure Steps

### Step 1 --- Verify OBC Status and Communication
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- request OBDH housekeeping
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- request TT&C housekeeping
**Verify:** `obdh.mode == 0` (0x0300, timeout 5 s) --- NOMINAL (1 = SAFE is also acceptable at this stage)
**Verify:** `obdh.cpu_load` (0x0302) < 80 %
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB
**Note:** Record `obdh.uptime` (0x0308) and `obdh.reboot_count` (0x030A). A
small uptime with a raised reboot count means the OBC has rebooted since
separation --- the clock is back at J2000 and this card is exactly what is
needed.
**GO/NO-GO:** OBC responsive and communication link healthy.

### Step 2 --- Read the Clock, Its Reference and the Slant Range
**TC:** `REQUEST_TIME` (Service 9, Subtype 2) --- request the current OBC time; the reply is TM(9,2)
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- TT&C housekeeping, for the slant range
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- Platform housekeeping, for the clock cross-check
**Verify:** TM(9,2) time report received within 10 s
**Action:** Record the onboard time as T_obc = `obdh.obc_time` (0x0309) +
`obdh.obc_time_fine` (0x0A12) / 65536, in **CUC seconds since J2000**
(2000-01-01T12:00:00 UTC). Post-separation, before any sync, expect a value of
a few hours at most --- the clock counts from the last bootloader reset, not
from launch.
**Action:** Record `obdh.time_ref` (0x0A13). Expect **0 (NONE)** here: never set
since the last bootloader reset. 1 = GROUND, 2 = GPS (disciplined), 3 =
HOLDOVER (was GPS, fix lost).
**Action:** Record `obdh.time_since_sync_s` (0x0A14). Expect 4294967295
(0xFFFFFFFF) = NEVER SYNCED. Any other value means the clock has already been
set this mission --- establish by whom before continuing.
**Action:** Record ground UTC at the moment the time report is received as
T_ground, expressed in the same CUC seconds since J2000.
**Action:** Compute the one-way light time from the slant range in the SID 6
report: OWLT_ms = `ttc.range_km` (0x0509) / 299792.458 * 1000. Expect 3-22 ms;
record it, and note that it is two orders of magnitude below this card's 1.0 s
acceptance threshold.
**Note:** `REQUEST_TIME` is the catalogue mnemonic (`tc_catalog.yaml`).
`TIME_REPORT_REQUEST` is an alias --- it resolves on the wire, but an operator
searching the command panel for it will not find it.
**Note:** `ttc.range_km` (0x0509) lives in **HK SID 6**, which is why this step
requests SID 6 and not only SID 4. It is the GEOMETRIC slant range and is
published whether or not a station is in view: about 6530 km out of contact and
1000-2000 km during a pass.
**Note:** The OBC clock is ALSO downlinked continuously as `obdh.obc_time`
(0x0309) in HK SID 4, requested above. Cross-check the TM(9,2) value against
it: the two must agree to within the SID 4 collection interval (8 s). They come
by different routes --- one an explicit time report, the other a housekeeping
sample --- so a disagreement means the time service and the housekeeping
sampler disagree about the clock, which is a different fault from the clock
being wrong.
**GO/NO-GO:** Valid TM(9,2) time report received, `obdh.time_ref` (0x0A13) read,
the slant range recorded, and 0x0309 in agreement.

### Step 3 --- Calculate the Time Delta
**Action:** Compute the clock offset:
  delta_t = T_obc - (T_ground - OWLT)
A NEGATIVE delta_t of roughly the mission age is the expected LEOP signature: an
un-set clock counting from J2000 while the ground reads the real mission date.
**Evaluate:** Record the magnitude and sign of delta_t.
**Evaluate:** If `obdh.time_ref` (0x0A13) = 0 (NONE) --- the clock has never been
set. delta_t will be the full offset from J2000 (years). This is the normal
LEOP case; proceed with the correction.
**Evaluate:** If |delta_t| < 1.0 s --- the clock is already accurate, which
post-separation means somebody has already set it. Confirm against
`obdh.time_ref` and `obdh.time_since_sync_s` before deciding no correction is
needed; the rate check at `STEP-6` still applies.
**Evaluate:** If 1.0 s <= |delta_t| < 86400 s (24 hours) --- a set clock that has
drifted, or a partial sync. Proceed with the correction.
**Evaluate:** If |delta_t| >= 86400 s with `time_ref` = 1 (GROUND) --- a clock
that WAS set and is now days out. HOLD and consult the OBDH engineer before
correcting; this is not ordinary drift, which runs at about 12 ppm (~1 s/day).
**GO/NO-GO:** delta_t explained by the time reference state (NONE = never set, GROUND/HOLDOVER = drift). Flight Director approves the correction.

### Step 4 --- Send the Time Update Command
**Action:** Compute T_corrected = T_ground_at_send + OWLT + processing_delay
(typically 50 ms), in **CUC seconds since J2000**.
**Action:** Confirm T_corrected is on the J2000 epoch. A Unix timestamp here is
about 9.5e8 s too large; the spacecraft accepts it and every timestamp
afterwards is wrong by some thirty years.
**TC:** `SET_TIME(cuc_seconds=T_corrected)` (Service 9, Subtype 1) --- set the OBC clock
**Verify:** Command acceptance telemetry (Service 1, Subtype 1) received within 5 s
**Verify:** No command rejection (Service 1, Subtype 2) received
**Note:** The argument must stay INSIDE the backticks. Written outside, the
extractor produces a `SET_TIME` step with no argument and the wire packs
cuc_seconds = 0 --- an epoch reset, not a synchronisation.
**Note:** The `SET_TIME` command applies the new time at the next PPS boundary
onboard.
**Note:** The spacecraft raises event **`OBC_TIME_SET` (0x030C)** carrying the
SIGNED jump in seconds and the resulting reference, in the form
`OBC clock set by ground (S9,1): jump +831000000.000s, time_ref=GROUND`. On the
first LEOP sync that jump is the whole offset from J2000 and will be a very
large number; that is the expected reading, and it is the spacecraft's own
measurement of how far out the clock was.
**Note:** If the command is rejected, first check `obdh.sw_image` (0x0311) --- a
`SET_TIME` sent while the OBC is in the bootloader is refused pre-boot.
**GO/NO-GO:** Time update command accepted by the OBC.

### Step 5 --- Verify Time Accuracy
**TC:** `REQUEST_TIME` (Service 9, Subtype 2) --- request the updated OBC time
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- Platform housekeeping, for the reference state
**Verify:** `obdh.time_ref == 1` (0x0A13, timeout 15 s) --- GROUND: the set was applied and the reference is now a ground set
**Verify:** `obdh.mode == 0` (0x0300, timeout 10 s) --- OBC was not disrupted by the time step
**Action:** Record the new onboard CUC time as T_obc_new and compute the
residual:
  delta_residual = T_obc_new - (T_ground_verify - OWLT)
**Verify:** |delta_residual| < 1.0 s (initial LEOP accuracy threshold)
**Note:** `obdh.time_since_sync_s` (0x0A14) must now read a small number instead
of 4294967295. If it still reads never-synced, the clock was not set, whatever
0x0309 shows.
**Note:** For LEOP, sub-second accuracy is sufficient. `SET_TIME` carries whole
CUC seconds only; finer accuracy comes from GPS discipline, and the tighter
0.1 s threshold belongs to NOM-008 in nominal operations.
**GO/NO-GO:** Residual delta within 1.0 s. Time synchronisation successful.

### Step 6 --- Validate the Clock Rate
**LABEL:** `STEP-6`
**TC:** `REQUEST_TIME` (Service 9, Subtype 2) --- second confirmation request
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- second SID 4 sample
**Verify:** Time report received within 10 s
**Action:** Compare two samples separated by a known ground interval:
  drift_ppm = ((T_obc_2 - T_obc_1) - (T_ground_2 - T_ground_1)) / interval * 1e6
**Verify:** Clock rate error < 100 ppm (LEOP threshold; NOM-008 applies a
tighter one in nominal operations)
**Note:** Expect about 12 ppm (~1 s/day) on a healthy unit, with a few ppm of
per-unit spread. A rate near 500 ppm (~43 s/day) is the signature of an injected
oscillator fault rather than an ageing part.
**Note:** A rate measured over a short interval is dominated by the sampling
resolution: 12 ppm over 60 s is 0.7 ms, far below what two SID 4 samples can
resolve. Use the longest interval the pass allows, or defer the rate estimate to
the next pass and trend it.
**Note:** If the clock were GPS-disciplined (`obdh.time_ref` 0x0A13 = 2) the rate
check would measure the discipline loop rather than the oscillator, and means
nothing. During LEOP the reference is normally GROUND.

### Step 6a --- PATH B: GPS Time Discipline (once the receiver has a fix)
**TC:** `HK_ENABLE(sid=12)` (Service 3, Subtype 5) --- GPS_NAV housekeeping, 8 s.
NOT default-enabled: without this command the PVT solution never reaches the
ground and LEOP-003 runs blind.
**Verify:** Periodic SID 12 begins emitting within one 8 s structure interval
**Verify:** `aocs.gps_fix` (0x0274) >= 2 within 900s --- a 3D fix (encoding
0=none, 1=2D, 2=3D, 3=3D+vel; the sync command refuses below 2). Cold-start TTFF is up to 15 minutes from
the LEOP-007 power-on; if the fix is not yet in, continue the pass and send
the sync on the next contact --- the ground reference from Step 4 carries the
mission meanwhile.
**TC:** `OBC_GPS_TIME_SYNC` (Service 8, Subtype 1, func_id 80)
**Verify:** S1.1 acceptance + S1.7 execution complete received within 5s
**Verify:** `obdh.time_ref` (0x0A13) = 2 (GPS) within 15s --- disciplined; 3
(HOLDOVER) is acceptable if the fix dips right after the sync and means the
same successful set with discipline waiting on the next fix
**Verify:** `obdh.time_since_sync_s` (0x0A14) reads a small value
**Note:** ORDER MATTERS: because the ground set (Step 4) already put the clock
within a second of truth, the GPS jump here is milliseconds --- far under the
5-second jump that forces AOCS to SAFE_BOOT (event 0x020F, CTG-027). Running
this path FIRST on a never-set clock would jump the full offset from J2000
and re-initialise the AOCS mid-LEOP. That ordering is the point of this card.
**Note:** While `time_ref` = 2 the clock is continuously disciplined and the
Step 6 rate check measures the discipline loop, not the oscillator --- record
the rate figure from BEFORE this step.
**GO/NO-GO:** Clock GPS-disciplined (or HOLDOVER with a ground-set clock)

### Step 7 --- Confirm Time-Stamping and Log the Result
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- confirm OBDH state post-sync
**Verify:** `obdh.uptime` (0x0308) > 0 --- the OBC has not rebooted during the sync
**Verify:** `obdh.time_ref` (0x0A13) in {1, 2, 3} --- GROUND if Path B was
deferred, GPS/HOLDOVER once it has run
**Action:** Confirm that housekeeping packets now carry plausible mission-date
timestamps in the PUS secondary header rather than year-2000 values.
**Action:** Log the following in the LEOP time-sync record:
  - Pre-correction delta: delta_t, with `obdh.time_ref` and
    `obdh.time_since_sync_s` as read before the correction
  - Jump reported by event 0x030C
  - Post-correction residual: delta_residual
  - Correction applied: YES / NO
  - Estimated clock rate error (ppm) and the interval it was measured over
  - OBC uptime and reboot count at the time of the sync
  - Any anomalies observed
**Note:** Stored TM and events produced BEFORE this card keep their year-2000
timestamps. They are not corrupt; the onboard clock genuinely read that value
when they were made. Note the sync time in the LEOP record so the early data can
be interpreted.

## Off-Nominal Handling
- If `REQUEST_TIME` (S9.2) is not acknowledged: verify TTC link integrity. Re-send
  once. If still no response, the OBC time management service may not be
  initialised --- with FD approval cycle the OBC through SAFE and back:
  `OBC_SET_MODE(mode=1)` (SAFE) then `OBC_SET_MODE(mode=0)` (NOMINAL --- always
  restore; never leave the card with a SAFE OBC) and retry. Note that a FULL
  reboot (func 52) resets the clock again, so a reboot means re-running this
  card afterwards.
- If `obdh.time_ref` (0x0A13) reads 0 (NONE) again after a successful sync: the
  OBC rebooted. Check `obdh.uptime` (0x0308) and `obdh.reboot_count` (0x030A).
  The bootloader resets the clock to J2000 on every reset and booting the
  application image does not restore it --- a reboot always means a re-sync.
- If `SET_TIME` is rejected: check `obdh.sw_image` (0x0311) first --- the command
  is refused while the OBC is in the bootloader. Otherwise check the onboard
  validation bounds and consult the OBDH engineer.
- If |delta_t| is not explained by the time reference: an epoch error on the
  ground is the usual cause. The mission epoch is J2000
  (2000-01-01T12:00:00 UTC), declared once in `mission.yaml` as `time_epoch` and
  used by the whole ground segment. A Unix-epoch value differs by about
  9.46e8 s; a GPS-epoch value by about 6.3e8 s.
- If |delta_residual| > 1.0 s after the correction: the correction may not have
  been applied at the expected PPS edge. Confirm `obdh.time_since_sync_s`
  (0x0A14) restarted, then re-send `SET_TIME` with an updated T_corrected.
- If clock rate error > 100 ppm: the OBC oscillator may be degraded or operating
  outside thermal qualification. Check `obdh.temp` (0x0301) is within -20 C to
  +60 C, schedule more frequent time syncs, and flag it for commissioning
  investigation.
- If the spacecraft is GPS-disciplined later in the mission
  (`obdh.time_ref` = 2) the clock holds true time with no drift; losing the fix
  moves it to 3 (HOLDOVER), raises event `OBC_TIME_REF_LOST` (0x030E), and drift
  resumes. GPS synchronisation and its AOCS side effects are CTG-027 --- note
  that a GPS sync jump larger than 5 s forces AOCS to SAFE_BOOT (event 0x020F).

## Exit state
- Clock set within 1.0 s of UTC; `obdh.time_ref` (0x0A13) = 2 (GPS) --- or 1
  (GROUND) if Path B was deferred to the next pass, or 3 (HOLDOVER) if the fix
  dipped after the sync
- HK SID 12 (GPS_NAV) enabled and emitting --- LEOP-003's entry state
- GPS receiver still ON with its fix state as read; AOCS mode UNCHANGED
  (COARSE_SUN) --- the ground-first ordering guarantees no SAFE_BOOT kick
- No other state changed by this card

## Post-Conditions
- [ ] Onboard clock synchronised to UTC within 1.0 s (LEOP accuracy threshold)
- [ ] `obdh.time_ref` (0x0A13) has a reference (1 GROUND / 2 GPS / 3 HOLDOVER)
- [ ] `obdh.time_since_sync_s` (0x0A14) reads a small value, no longer 4294967295
- [ ] Clock drift rate verified < 100 ppm (measured BEFORE GPS discipline), with the measurement interval recorded
- [ ] Time synchronisation log entry completed for the LEOP record
- [ ] Housekeeping telemetry packets carry mission-date CUC timestamps
- [ ] OBC mode unchanged and no reboot during the sync
- [ ] Slant range and OWLT recorded from HK SID 6
- [ ] `obdh.obc_time` (0x0309) agrees with the TM(9,2) report

**NEXT:** LEOP-003 (Initial Orbit Determination --- ends LEOP with the phase-5
declaration)

---
*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

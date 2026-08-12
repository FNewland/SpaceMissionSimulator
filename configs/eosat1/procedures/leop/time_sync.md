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
- [ ] LEOP-001 First Acquisition complete --- bidirectional S-band link established
      AND the OBC in the APPLICATION image (`obdh.sw_image` 0x0311 = 1). `set_time`
      is NOT in the OBDH bootloader allowlist (`obdh_basic._BOOTLOADER_ALLOWED`
      admits only the boot/maintenance funcs), so Step 4 is rejected outright while
      the spacecraft is still in the bootloader.
- [ ] TTC link locked: `ttc.link_status` (0x0501) = 2 (LOCKED)
- [ ] Ground UTC reference available (stratum-1 NTP or GPS-disciplined clock)
- [ ] OBC in SAFE or NOMINAL mode: `obdh.mode` (0x0300) = 0 or 1
- [ ] No time-tagged command sequences loaded onboard (fresh post-separation state)
- [ ] Flight Director has authorised time synchronisation

## Procedure Steps

### Step 1 --- Verify OBC Status and Communication
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- request OBDH housekeeping
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- request TT&C housekeeping
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) or 1 (SAFE) within 5 s
**Verify:** `obdh.cpu_load` (0x0302) < 80 %
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB
**Note:** Record `obdh.uptime` (0x0308) as initial reference. If uptime is near zero,
the OBC may have recently rebooted --- verify `obdh.reboot_count` (0x030A) before proceeding.
**GO/NO-GO:** OBC responsive and communication link healthy.

### Step 2 --- Request Onboard Time Report and the Slant Range Together
**Note:** Corrected 2026-08b. Three things were wrong with this step, and all
three made the delta_t of Step 3 unobtainable rather than merely imprecise.
1. It named `TIME_REPORT_REQUEST`, an ALIAS. The catalogue command is
   **`REQUEST_TIME`** (`tc_catalog.yaml`; the alias resolves on the wire, but
   an operator searching the command panel for the card's mnemonic does not
   find it).
2. It asked the operator to read `ttc.range_km` (0x0509) without requesting the
   structure that carries it. 0x0509 is in **HK SID 6**, and this card only ever
   requested SID 4 --- so the range needed for the OWLT never arrived.
3. Its OWLT figure was wrong by a factor of three. `ttc.range_km` is the
   GEOMETRIC slant range and is published whether or not a station is in view:
   measured on the shipped simulator it reads about 6530 km out of contact and
   1000-2000 km during a pass, i.e. **3-22 ms**, not "2--8 ms". It is still
   negligible against the 1.0 s LEOP threshold, which is the reason to record
   it and move on --- but a card that quotes a number the spacecraft never
   produces teaches the operator not to check.
**TC:** `REQUEST_TIME` (Service 9, Subtype 2) --- request the current OBC time; the reply is TM(9,2)
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- TT&C housekeeping, for the slant range
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- Platform housekeeping, for the clock cross-check
**Verify:** TM(9,2) time report received within 10 s
**Action:** Record the reported onboard CUC time as T_obc.
**Action:** Record ground UTC at the moment the time report TM packet is received as T_ground.
**Action:** Compute the one-way light time from the slant range in the SID 6
report: OWLT_ms = `ttc.range_km` (0x0509) / 299792.458 * 1000. Expect 3-22 ms;
record it, and note that it is two orders of magnitude below this card's 1.0 s
acceptance threshold.
**Note:** The OBC clock is ALSO downlinked continuously as `obdh.obc_time`
(0x0309) in HK SID 4, requested above. Cross-check the TM(9,2) value against it:
the two must agree to within the SID 4 collection interval. They come by
different routes --- one an explicit time report, the other a housekeeping
sample --- so a disagreement means the time service and the housekeeping
sampler disagree about the clock, which is a different fault from the clock
being wrong.
**GO/NO-GO:** Valid TM(9,2) time report received with a plausible CUC value, the
slant range recorded, and 0x0309 in agreement.

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
**TC:** `SET_TIME(cuc_seconds=T_corrected)` (Service 9, Subtype 1) --- set the OBC clock
**Note:** The argument was written OUTSIDE the backticks, so the extractor produced a
`SET_TIME` step with no argument and the wire packed cuc_seconds = 0 --- an epoch
reset, not a synchronisation.
**Action:** Compute T_corrected = T_ground_at_send + OWLT + processing_delay (typically 50 ms).
**Note:** The `SET_TIME` command applies the new time at the next PPS boundary onboard.
**Verify:** Command acceptance telemetry (Service 1, Subtype 1) received within 5 s
**Verify:** No command rejection (Service 1, Subtype 2) received
**Note:** If command rejected, verify CUC time value is within the onboard validation
range (epoch year +/- 1 year) and re-compute.
**GO/NO-GO:** Time update command accepted by OBC.

### Step 5 --- Verify Time Accuracy
**TC:** `REQUEST_TIME` (Service 9, Subtype 2) --- request the updated OBC time
**Verify:** Time report received within 10 s
**Action:** Record new onboard CUC time as T_obc_new.
**Action:** Compute residual delta:
  delta_residual = T_obc_new - (T_ground_verify - OWLT)
**Verify:** |delta_residual| < 1.0 s (initial LEOP accuracy threshold)
**Verify:** `obdh.mode` (0x0300) unchanged --- OBC was not disrupted by time step
**Note:** For LEOP, sub-second accuracy is sufficient. Fine synchronisation (< 0.1 s)
will be performed during nominal operations via NOM-006 (clock_sync.md).
**GO/NO-GO:** Residual delta within 1.0 s. Time synchronisation successful.

### Step 6 --- Request Confirmation Time Report and Validate Rate
**TC:** `REQUEST_TIME` (Service 9, Subtype 2) --- second confirmation request
**Verify:** Time report received within 10 s
**Action:** Compare two successive time reports separated by known ground interval to
confirm onboard clock is incrementing at the correct rate (1 Hz +/- 100 ppm).
**Verify:** Clock rate error < 100 ppm (LEOP threshold; tighter threshold in nominal ops)
**Note:** If clock rate error exceeds 100 ppm, flag OBC oscillator for investigation
during commissioning phase. Record drift rate for trending.

### Step 7 --- Enable Event Time-Stamping and Log Results
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27) --- confirm OBDH state post-sync
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
- If `REQUEST_TIME` (S9.2) is not acknowledged: Verify TTC link integrity. Re-send once. If
  still no response, the OBC time management service may not be initialised --- attempt
  an OBC soft reset via `OBC_SET_MODE(mode=1)` (Service 8, Subtype 1, func_id 50) and retry.
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
- [ ] Slant range and OWLT recorded from HK SID 6
- [ ] `obdh.obc_time` (0x0309) agrees with the TM(9,2) report
- [ ] Ready to proceed with LEOP-007 Summary Checkout

---
*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

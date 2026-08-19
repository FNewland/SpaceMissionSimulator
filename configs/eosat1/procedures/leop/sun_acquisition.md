# LEOP-005: Sun Acquisition
**Subsystem:** AOCS
**Phase:** LEOP
**Revision:** 2.0 --- 2026-08c flow redesign. The DETUMBLE -> COARSE_SUN
transition is AUTOMATIC (rates < 0.5 deg/s sustained 30 s ---
`docs/plan_2026-08c/AOCS_MODE_TRANSITIONS.md` 1.2): this card OBSERVES it and
verifies the sun hold; it commands no mode change. Reaction-wheel checks are
gone --- the wheels are disabled until COM-004 and COARSE_SUN flies on
magnetorquers.
**Approved:** Flight Operations Director

**Entry state:** LEOP-007 exit state --- AOCS in DETUMBLE on magnetometer + MTQ,
SID 2 downlinking, wheels DISABLED, GPS receiver acquiring, wings deployed.

## Purpose
Monitor the automatic rate-damping and sun-acquisition sequence: watch B-dot
detumble bring body rates below threshold, observe the AUTOMATIC handover to
COARSE_SUN, and verify the spacecraft settles into a power-positive sun hold.
COARSE_SUN is where LEOP legitimately parks: with no star tracker powered yet,
the automatic promotion out of COARSE_SUN cannot trigger (it requires ST
tracking), so the spacecraft holds the sun-safe attitude until commissioning
brings the trackers up (COM-003).

## Prerequisites
- [ ] LEOP-007 completed --- `aocs.mode` (0x020F) = 2 (DETUMBLE),
      `aocs.mag_enabled` (0x025C) = 1, `aocs.mtq_enabled` (0x025E) = 1
- [ ] LEOP-004 completed --- wings deployed (power for the hold)
- [ ] `eps.bat_soc` (0x0101) > 40%
- [ ] SID 2 periodic housekeeping on the downlink (contact scheduled; this
      card spans more than one pass if tip-off rates are high --- the autonomy
      does not need the ground watching to proceed)

## Procedure Steps

### Step 1 --- Record Initial Body Rates
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.mode` (0x020F) = 2 (DETUMBLE) within 10s
**Verify:** `aocs.rate_roll` (0x0204) reported within 10s --- record value
**Verify:** `aocs.rate_pitch` (0x0205) reported within 10s --- record value
**Verify:** `aocs.rate_yaw` (0x0206) reported within 10s --- record value
**Action:** Log initial rates as baseline. Expected post-separation rates: 0.5 to 5.0 deg/s per axis typical.
**GO/NO-GO:** Rate telemetry valid, DETUMBLE confirmed active

### Step 2 --- Monitor Rate Damping (observe --- command nothing)
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) --- repeat at T+5 min
**Verify:** `aocs.rate_roll` (0x0204) decreasing within 10s
**Verify:** `aocs.rate_pitch` (0x0205) decreasing within 10s
**Verify:** `aocs.rate_yaw` (0x0206) decreasing within 10s
**Action:** B-dot should halve the rates roughly every 10-15 minutes. Verify magnetorquer duty via `aocs.mag_field_total` (0x0277) --- a zeroed field means the magnetometer dropped out and B-dot is blind.
**GO/NO-GO:** All body rates trending downward

### Step 3 --- Observe the AUTOMATIC Transition to COARSE_SUN
**Action:** When the body rates have been below 0.5 deg/s for 30 s sustained,
the AOCS hands itself from DETUMBLE to COARSE_SUN.
**Note:** This is an AUTOMATIC transition --- do NOT command mode 3 (S8.1
func 0). The old revision of this card commanded the mode it was about to get
for free, and then "verified" a transition the spacecraft had already made on
its own.
**Verify:** `aocs.mode` (0x020F) = 3 (COARSE_SUN) within 1800s of rates
falling below 0.5 deg/s
**Verify:** Event `AOCS mode change` in the event log carrying DETUMBLE -> COARSE_SUN
**GO/NO-GO:** Automatic handover observed and logged

### Step 4 --- Verify the Sun Hold
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) --- repeat every 60s for 5 minutes
**Verify:** `aocs.att_error` (0x0217) < 15.0 deg within 600s --- converging on the sun vector
**Verify:** `aocs.css_sun_confidence` (0x0215) > 0.5 within 60s --- CSS sees the sun
**Verify:** `aocs.rate_roll` (0x0204) < 0.5 deg/s within 10s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.5 deg/s within 10s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.5 deg/s within 10s
**Note:** COARSE_SUN without wheels flies on magnetorquers: expect a degree-
class, slowly-hunting hold, not a wheel-tight one. Rates of a few hundredths
to ~0.2 deg/s while it hunts are normal here.
**GO/NO-GO:** Sun-pointing achieved within 15 degrees and holding

### Step 5 --- Verify Power Generation in the Hold
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.power_gen` (0x0107) > 40W within 60s (sun-pointed, wings deployed)
**Verify:** `eps.bat_soc` (0x0101) stable or increasing within 120s
**GO/NO-GO:** Positive power balance in COARSE_SUN

### Step 6 --- Log Final AOCS State
**Action:** Record final body rates, attitude error, CSS confidence and power
generation. Note explicitly that the spacecraft REMAINS in COARSE_SUN: with no
star tracker powered, the automatic promotion out of the hold cannot fire, and
that is the intended LEOP parking state. Issue Sun Acquisition Report.
**GO/NO-GO:** Stable sun hold confirmed --- LEOP-005 complete

## Off-Nominal Handling
- If body rates not decreasing after 15 min: verify `aocs.mtq_enabled` (0x025E) = 1 and `aocs.mag_enabled` (0x025C) = 1. If either dropped, re-establish per LEOP-007 Step 4 and re-command DETUMBLE with S8.1 func 0 mode 2 --- re-commanding the mode you are already in is harmless. Escalate to AOCS engineer if rates still hold.
- If the automatic COARSE_SUN handover does not occur within 30 min of rates < 0.5 deg/s: the sustain condition may be breaking (a rate axis oscillating around 0.5). Watch the per-axis rates; if one axis will not settle, suspect a magnetorquer polarity or magnetometer axis fault and escalate. Do not force the mode by command --- a COARSE_SUN entered above the rate threshold will not hold.
- If attitude error not converging in COARSE_SUN: verify `aocs.css_sun_confidence` (0x0215). Low confidence in eclipse is expected --- the CSS cannot see the sun; wait for the sunlit arc and re-verify.
- If power generation < 40W after sun-pointing: check array status (LEOP-004) and attitude. Acceptable for LEOP if SoC is maintained above 40%.

## Exit state
- AOCS in COARSE_SUN (automatic, mode 3) --- the LEOP parking hold; wheels
  still DISABLED; star trackers and gyros still OFF
- Power-positive: generation > 40W, SoC stable or rising
- Nothing else changed by this card

## Post-Conditions
- [ ] Rate damping profile recorded (baseline to < 0.5 deg/s)
- [ ] Automatic DETUMBLE -> COARSE_SUN handover observed (not commanded)
- [ ] Sun hold verified: att_error < 15 deg, CSS confidence > 0.5
- [ ] Positive power balance confirmed
- [ ] Sun Acquisition Report distributed

**NEXT:** LEOP-006 (Time Synchronisation --- ground path immediately, GPS path
once the receiver powered in LEOP-007 holds a fix)

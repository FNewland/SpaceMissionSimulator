# COM-005: AOCS Mode Transitions
**Subsystem:** AOCS
**Phase:** COMMISSIONING
**Revision:** 2.0 --- rewritten 2026-08c against the definitive transitions
table (`docs/plan_2026-08c/AOCS_MODE_TRANSITIONS.md`). Revision 1.0 used a
five-mode numbering that has never matched the flight software (it called
mode 2 COARSE_SUN and mode 3 NOMINAL, tested an "IDLE" that is mode 0 OFF,
and assumed the card STARTS in COARSE_SUN --- it does not: COARSE_SUN is a
transient hold the spacecraft auto-promotes out of, and by COM-005 the AOCS
has long since promoted itself to NOMINAL).
**Approved:** Flight Operations Director

**Entry state:** COM-004 exit state --- all four wheels ENABLED, star tracker
tracking, gyros calibrated, AOCS in the NOMINAL family: mode (0x020F) = 4
NOMINAL or 8 ECLIPSE (the auto-promotion out of COARSE_SUN happened during
COM-003/COM-004; 8 is the same operational state on the shadow side).

## Purpose
Exercise every COMMANDED transition the operations concept uses, and OBSERVE
(not command) the automatic ones, so the mode-management behaviour is
demonstrated end to end before the payload flies on it. The rule throughout,
from the transitions table: **command what is commanded, wait-and-verify what
is automatic, and never verify the mode you just left.**

Canonical mode codes (`configs/eosat1/aocs/aocs_mode_codes.yaml`): 0 OFF,
1 SAFE_BOOT, 2 DETUMBLE, 3 COARSE_SUN, 4 NOMINAL, 5 FINE_POINT, 6 SLEW,
7 DESAT, 8 ECLIPSE, 9 INERTIAL. `aocs.commanded_mode` (0x0297) latches ground
INTENT (set only when 4, 5, 9 or OFF is commanded); `aocs.mode_demoted`
(0x0298) = 1 whenever the flying mode differs from the commanded one.

## Prerequisites
- [ ] COM-003 (Sensor Calibration) and COM-004 (Actuator Checkout) completed
- [ ] `aocs.wheels_enabled` (0x0260) = 4; star tracker tracking >= 5 stars
- [ ] `eps.bat_soc` (0x0101) > 65%
- [ ] OBC in NOMINAL mode (`obdh.mode` 0x0300 = 0)
- [ ] Bidirectional S-band link active; note the observation windows in this
      card outlast a single pass --- the automatic transitions proceed without
      ground contact and are verified on the next contact

## Procedure Steps

### Step 1 --- Latch the Commanded Mode (NOMINAL)
**TC:** `AOCS_SET_MODE(mode=4)` (Service 8, Subtype 1, func_id 0) --- NOMINAL
**Note:** The AOCS may already be flying mode 4 --- it auto-promoted out of
COARSE_SUN during COM-003. This command is still sent: it LATCHES
`aocs.commanded_mode` (0x0297) = 4, which is what every automatic transition
resolves its return path against. Intent must be explicit before this card
starts bouncing the vehicle through transients.
**Verify:** `aocs.mode` (0x020F) in {4, 8} within 300s --- 8 (ECLIPSE) is the
automatic shadow-side handover of the same commanded state
**Verify:** `aocs.commanded_mode` (0x0297) = 4 within 10s
**Verify:** `aocs.att_error` (0x0217) < 1.0 deg within 600s (sunlit side)
**GO/NO-GO:** NOMINAL commanded, latched and converged

### Step 2 --- COMMANDED: NOMINAL -> FINE_POINT (gated)
**TC:** `AOCS_FINE_READY_CHECK` (Service 8, Subtype 1, func_id 89) --- read the precondition mask
**Verify:** `aocs.fine_ready` (0x02C5) = 0 within 1800s --- all seven legs green
(bit0 ST < 5 stars, bit1 < 3 wheels, bit2 MEKF not converged 30 s, bit3 rate
>= 0.01 deg/s, bit4 momentum >= 70%, bit5 propagate-only/eclipse, bit6 slew
not idle 60 s). In eclipse bit5 holds the mask non-zero --- wait for the sunlit
arc rather than fighting it.
**TC:** `AOCS_SET_MODE(mode=5)` (Service 8, Subtype 1, func_id 0) --- FINE_POINT
**Verify:** `aocs.mode` (0x020F) = 5 within 30s
**Verify:** `aocs.att_error` (0x0217) < 0.1 deg within 300s
**Note:** A non-zero mask refuses the command with 0x0A22 and the mask in the
reply --- that is the gate doing its job, not a fault.
**GO/NO-GO:** FINE_POINT entered through its gate, sub-0.1 deg pointing

### Step 3 --- COMMANDED: FINE_POINT -> NOMINAL
**TC:** `AOCS_SET_MODE(mode=4)` (Service 8, Subtype 1, func_id 0)
**Verify:** `aocs.mode` (0x020F) = 4 within 30s
**Verify:** `aocs.commanded_mode` (0x0297) = 4 within 10s
**GO/NO-GO:** Clean return to NOMINAL

### Step 4 --- COMMANDED: NOMINAL -> INERTIAL and back
**TC:** `AOCS_SET_MODE(mode=9)` (Service 8, Subtype 1, func_id 0) --- INERTIAL (ECI hold)
**Verify:** `aocs.mode` (0x020F) = 9 within 30s
**Note:** INERTIAL does not yield to eclipse (transitions table 1.2) --- it is
the one operational mode that holds through the terminator.
**TC:** `AOCS_SET_MODE(mode=4)` (Service 8, Subtype 1, func_id 0) --- back to NOMINAL
**Verify:** `aocs.mode` (0x020F) = 4 within 30s
**GO/NO-GO:** INERTIAL hold entered and released by command

### Step 5 --- AUTOMATIC: COARSE_SUN is a hold, not a destination
**TC:** `AOCS_SET_MODE(mode=3)` (Service 8, Subtype 1, func_id 0) --- COARSE_SUN safe hold
**Verify:** `aocs.mode` (0x020F) = 3 within 30s
**Verify:** `aocs.mode_demoted` (0x0298) = 1 within 30s --- the spacecraft knows
it is below its commanded mode
**Action:** Now WAIT. With CSS valid, attitude error < 15 deg sustained 60 s
and the star tracker tracking, the AOCS promotes itself back to the COMMANDED
mode (0x0297 = 4) with no further ground action.
**Verify:** `aocs.mode` (0x020F) = 4 within 1800s --- the AUTOMATIC promotion
(allow a full eclipse arc on top if the hold spans the terminator)
**Verify:** `aocs.mode_demoted` (0x0298) = 0 within 30s of the promotion
**Note:** Do NOT verify mode = 3 "minutes later" --- a card that parks on
COARSE_SUN and expects to find it there is the exact defect this revision
removes. The verify after the hold is on the PROMOTED mode.
**GO/NO-GO:** Hold entered by command, LEFT automatically, demotion flag cycled

### Step 6 --- AUTOMATIC: eclipse handover (observed in passing)
**Action:** Over the card's span the orbit crosses the terminator. Confirm
from the archive that NOMINAL handed to ECLIPSE (mode 8) at entry (5 s
debounce) and back at exit, with `aocs.commanded_mode` (0x0297) = 4 constant
throughout. No command is associated with this step.
**Verify:** `aocs.mode` (0x020F) in {4, 8} at every archive sample since Step 3
**GO/NO-GO:** Eclipse handover automatic in both directions, intent preserved

### Step 7 --- COMMANDED with AUTOMATIC exit: desaturation
**TC:** `AOCS_DESATURATE` (Service 8, Subtype 1, func_id 1)
**Verify:** `aocs.mode` (0x020F) = 7 within 30s --- DESAT
**Action:** DESAT exits by ITSELF (momentum inside deadband, envelope at the
exit target, or wheel speeds down --- sustained 10 s; or the ineffective-desat
timeout, 0x029C/event 0x020E). Do not command the exit.
**Verify:** `aocs.mode` (0x020F) = 4 within 900s --- automatic return to the
commanded mode
**GO/NO-GO:** Desaturation ran and returned by itself

### Step 8 --- Document the Transition Matrix
**Action:** Compile the verification matrix. COMMANDED entries carry the TC
and its verify; AUTOMATIC entries carry the trigger and the observed time.

| Transition | Kind | Evidence |
|---|---|---|
| (entry) COARSE_SUN -> NOMINAL | automatic (COM-003/004 span) | archive |
| NOMINAL -> FINE_POINT | commanded, gated by 0x02C5 | Step 2 |
| FINE_POINT -> NOMINAL | commanded | Step 3 |
| NOMINAL -> INERTIAL -> NOMINAL | commanded | Step 4 |
| NOMINAL -> COARSE_SUN | commanded (hold) | Step 5 |
| COARSE_SUN -> NOMINAL | AUTOMATIC promotion | Step 5 |
| NOMINAL <-> ECLIPSE | AUTOMATIC (terminator) | Step 6 |
| NOMINAL -> DESAT | commanded | Step 7 |
| DESAT -> NOMINAL | AUTOMATIC exit | Step 7 |

**Note:** DETUMBLE and SAFE_BOOT are deliberately NOT exercised here: DETUMBLE
was flown for real in LEOP-007/LEOP-005, and re-entering it now would cost a
full reacquisition ladder for no new information. SLEW is exercised by the
imaging cards (`AOCS_SLEW_TO`).
**GO/NO-GO:** All rows evidenced --- AOCS mode management commissioned

## Off-Nominal Handling
- If `AOCS_SET_MODE(mode=5)` refused with 0x0A22: read the mask in the S1.8 reply and fix the named leg (tracker, wheels, convergence, rates, momentum, eclipse, slew-idle). Momentum >= 70% (bit4) is what Step 7 exists for --- desaturate and retry.
- If the Step 5 automatic promotion does not occur within the window: check the three promotion legs --- CSS validity (eclipse blocks it), `aocs.att_error` < 15 deg, and the tracker still tracking. A tracker dropout parks the vehicle in the hold BY DESIGN; recover the tracker (CTG-008) rather than forcing the mode.
- If DESAT ends with `aocs.desat_ineffective` (0x029C) = 1 / event 0x020E: the magnetorquer authority could not unload in time. Check MTQ enable/health and orbit position (equatorial arcs unload slowly); re-run over a polar arc.
- If body rates diverge in any mode: emergency detumble is AUTOMATIC above the emergency rate threshold --- do not race it. If it fires, re-enter this card at Step 1 after recovery.

## Exit state
- AOCS in NOMINAL (or ECLIPSE on the shadow side) with `aocs.commanded_mode`
  (0x0297) = 4 and `aocs.mode_demoted` (0x0298) = 0
- Wheels enabled, momentum unloaded (Step 7), tracker in the loop
- No power-line, OBC-mode, payload or TTC state touched by this card

## Post-Conditions
- [ ] Every commanded transition demonstrated with its gate behaviour
- [ ] Every automatic transition OBSERVED with trigger and timing recorded
- [ ] Transition matrix archived in the Mode Transition Report
- [ ] AOCS left in commanded NOMINAL, momentum unloaded

**NEXT:** COM-006 (TTC Link Verification)

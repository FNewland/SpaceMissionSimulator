# COM-013: ADCS Commissioning Closure & NOMINAL Declaration

**Subsystem:** AOCS / All
**Phase:** COMMISSIONING
**Revision:** 2.0 --- 2026-08c dedupe. Revision 1.0 re-ran work owned by other
cards (magnetometer checks = LEOP-007/COM-003, star trackers and gyro
calibration = COM-003, wheel and magnetorquer sign checks = COM-004, detumble
and mode transitions = LEOP-005/COM-005) and referenced hardware that is not
fitted: there is ONE magnetometer (the dual-unit TM block 0x0223-0x0229 and
the MAG_SELECT command are retired --- func 7 is REFUSED on board). This
revision is the CLOSURE card: it assumes every prior card's exit state,
reads back the consolidated end state without re-testing anything, and makes
the one declaration that ends commissioning.
**Approved:** Flight Operations Director

**Entry state:** COM-011 exit state (definitive order ... COM-010, COM-012
first light, COM-011 calibration, COM-013) --- payload commissioned: first
light captured and downlinked over S13, radiometric calibration data taken;
AOCS in commanded NOMINAL with wheels enabled and gyros calibrated; downlink
back at LOW; OBC NOMINAL; mission phase 5.

## Purpose
Close out commissioning: verify the consolidated spacecraft end state against
each card's declared exit state, poll all positions, and declare mission phase
6 (NOMINAL) --- the single commanded transition that unlocks the derived
NOMINAL spacecraft mode, arms the phase-gated FDIR rules and the propulsion
interlock. Nothing on board advances the phase for you; the declaration is
this card's telecommand and no other card carries it.

## Prerequisites
- [ ] COM-001 through COM-012 completed with their exit states verified
- [ ] `eps.bat_soc` (0x0101) > 60%
- [ ] Bidirectional S-band link active on a scheduled pass
- [ ] Flight Director briefing held; pre-transition checklist from the LEOP
      manual (§9.1) reviewed

## Procedure Steps

### Step 1 --- Consolidated AOCS End-State Readback
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.mode` (0x020F) in {4, 5, 8} --- operational family
**Verify:** `aocs.commanded_mode` (0x0297) = 4 and `aocs.mode_demoted` (0x0298) = 0
**Verify:** `aocs.wheels_enabled` (0x0260) = 4 (COM-004 exit state)
**Verify:** `aocs.gyro_units_mask` (0x02D1) = 7 (COM-003 exit state)
**Verify:** `aocs.st1_status` (0x0240) = 2 with `aocs.st1_num_stars` (0x0241) >= 5
**Verify:** `aocs.mag_enabled` (0x025C) = 1 and `aocs.mag_field_total` (0x0277)
in [5000, 70000] nT --- the single magnetometer (no B unit exists; nothing to
select)
**Verify:** `aocs.att_error` (0x0217) < 1.0 deg (sunlit side)
**GO/NO-GO:** AOCS end state matches the sum of its cards

### Step 2 --- Consolidated Platform End-State Readback
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `obdh.mode` (0x0300) = 0 --- NOMINAL (COM-007 restored it; a
spacecraft about to declare NOMINAL must not be carrying a SAFE OBC)
**Verify:** `ttc.tm_tier` (0x0539) = 1 (LOW) and `ttc.downlink_mode` (0x053C) = 0
--- no card left the HIGH gate open (COM-006/COM-012 exit states)
**Verify:** `eps.load_shed_stage` (0x0134) = 0 --- no shed latch left set (COM-008)
**Verify:** `eps.bus_voltage` (0x0105) in [26.5, 29.5] V; `eps.bat_soc`
(0x0101) > 60%
**Verify:** `obdh.time_ref` (0x0A13) != 0 --- clock referenced (LEOP-006)
**GO/NO-GO:** Platform end state clean --- nothing left armed, shed or raised

### Step 3 --- Consolidated Payload End-State Readback
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Verify:** `payload.mode` (0x0600) = 1 --- STANDBY (never left IMAGING)
**Verify:** `payload.fpa_ready` (0x0610) = 1 --- cooler regulating (COM-010)
**Verify:** `payload.transfer_active` (0x062D) = 0 --- no S13 session left open
**GO/NO-GO:** Payload commissioned and quiescent

### Step 4 --- Declare NOMINAL Operations (phase 6)
**Action:** FD polls all positions. Work through the pre-transition checklist:
OBC and AOCS modes, sustained attitude error, battery SoC and power margin,
sensors and actuators responding, link stable, GPS fix valid, thermal zones in
limits, S12 monitoring and S19 event-actions enabled.
**Note:** Declaring NOMINAL (6) is what actually unlocks nominal operations.
Below phase 6 the derived spacecraft mode (`sat.mode` 0x0001, and the console
header) is pinned to SAFE regardless of spacecraft health; the advanced
fault-propagation model and the phase-gated FDIR rules are disarmed; and the
propulsion interlock refuses to arm a burn (`prop.yaml min_phase: 6`).
**TC:** `SYS_SET_MISSION_PHASE(phase=6, flags=0)` (Service 8, Subtype 1, func_id 120)
**Verify:** `eps.spacecraft_phase` (0x0129) = 6 within 5s
**Verify:** `sat.mode` (0x0001) = 1 within 10s --- derived NOMINAL
**Action:** Confirm event `MISSION_PHASE_CHANGED` (0x0055) appears in the
event log carrying `5 (COMMISSIONING) -> 6 (NOMINAL)`, and record the
transition time in the mission log.
**Note:** Going back is refused unless the FORCE flag is set (flags bit 0 = 1)
--- a deliberate speed bump. If a declaration was premature, re-send with the
flag and record why.
**GO/NO-GO:** Phase = NOMINAL, spacecraft declared operational

### Step 5 --- Hold and Hand Over
**Action:** Hold for at least one full eclipse cycle with the now-armed FDIR
rules evaluating. Confirm from the archive that `sat.mode` (0x0001) stays 1
and `aocs.mode` (0x020F) stays in {4, 5, 8} throughout.
**Action:** Distribute the Commissioning Closure Report and hand the console
to the nominal-operations shift (NOM-001 onward).
**GO/NO-GO:** NOMINAL held through an orbit --- commissioning CLOSED

## Off-Nominal Handling
- If any Step 1-3 readback fails: do NOT declare phase 6. Return to the owning card's off-nominal section (the verify lines above name it) and re-establish its exit state first.
- If `SYS_SET_MISSION_PHASE` is refused: 0x0C03 = OBC in bootloader (a reboot happened --- re-run LEOP-001 Step 5 and LEOP-006); 0x0C04 = backwards without FORCE.
- If `sat.mode` does not derive 1 after the declaration: an FDIR contingency is latched --- read the FDIR journal, clear the cause, and the derived mode recovers by itself. There is no TC that sets sat.mode directly.
- If AOCS demotes during the Step 5 hold: the now-armed `aocs.att_error` FDIR rule commands COARSE_SUN on a real excursion; the auto-resume ladder applies. Investigate the excursion before re-declaring anything.

## Exit state
- Mission phase 6, `sat.mode` = 1 (NOMINAL); FDIR fully armed
- AOCS commanded NOMINAL; payload STANDBY with FPA regulating; downlink LOW;
  OBC NOMINAL; no latches, sessions or gates left open
- The spacecraft is the nominal-operations baseline that NOM-001 assumes

## Post-Conditions
- [ ] Every commissioning card's exit state re-verified in consolidation
- [ ] Phase 6 declared (event 0x0055 logged), `sat.mode` = 1
- [ ] NOMINAL held through a full eclipse cycle
- [ ] Commissioning Closure Report distributed

**NEXT:** Nominal operations (NOM-001 Pass Startup). Commissioning is closed.

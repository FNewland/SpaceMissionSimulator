# CTG-008: Star Tracker Failure

**Category:** Contingency
**Position Lead:** Flight Dynamics (AOCS)
**Cross-Position:** Flight Director
**Difficulty:** Advanced

## Objective
Respond to a star tracker 1 (ST1) failure by switching to the redundant star tracker 2
(ST2). If ST2 is also unavailable, fall back to coarse sun sensor (CSS) pointing mode
(COARSE_SUN) to maintain safe spacecraft attitude. This procedure ensures continuous
attitude determination capability and minimizes impact on mission operations.

## Prerequisites
- [ ] Star tracker 1 failure detected — `aocs.st1_status` (0x0240) = 4 (FAILED)
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 2 (LOCKED)
- [ ] Flight Director notified of attitude sensor anomaly
- [ ] Spacecraft bus power is nominal — `eps.bus_voltage` (0x0105) > 27.0 V

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| aocs.st1_status | 0x0240 | 4 (FAILED) — triggering condition |
| aocs.st1_num_stars | 0x0241 | 0 (no stars tracked when failed) |
| aocs.st2_status | 0x0243 | 0 (OFF) initially, then 1 (BOOT), then 2 (TRACKING) |
| aocs.mode | 0x020F | Current AOCS mode |
| aocs.att_error | 0x0217 | Monitor — may increase without ST data |
| aocs.rate_roll | 0x0204 | Monitor body rates |
| aocs.rate_pitch | 0x0205 | Monitor body rates |
| aocs.rate_yaw | 0x0206 | Monitor body rates |
| aocs.css_valid | 0x0248 | CSS sun vector validity |
| aocs.css_sun_x | 0x0245 | CSS sun vector X |
| aocs.css_sun_y | 0x0246 | CSS sun vector Y |
| aocs.css_sun_z | 0x0247 | CSS sun vector Z |
| eps.bus_voltage | 0x0105 | > 27.0 V |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| ST2_POWER | 8 | 1 | 5 | Star tracker 2 power on |
| ST_SELECT | 8 | 1 | 6 | Select primary star tracker (wire: 1=ST1, 2=ST2, 0 refused) |
| ST1_POWER | 8 | 1 | 4 | Star tracker 1 power off (isolation) |
| AOCS_SET_MODE | 8 | 1 | 0 | Set AOCS mode (fallback to COARSE_SUN) |

## Procedure Steps

### Step 1: Confirm Star Tracker 1 Failure
**LABEL:** `STEP-1`
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — Request AOCS housekeeping
**Verify:** `aocs.st1_status == 4` (0x0240, timeout 15 s) — FAILED
**Verify:** `aocs.st1_num_stars` (0x0241) = 0
**Verify:** `aocs.mode` (0x020F) — record current mode
**Verify:** `aocs.att_error` (0x0217) — record current value (may be increasing)
**Verify:** `aocs.rate_roll` (0x0204), `aocs.rate_pitch` (0x0205), `aocs.rate_yaw` (0x0206)
— record body rates
**Note:** If `aocs.st1_status` = 3 (BLIND) rather than 4 (FAILED), the star tracker may
be temporarily blinded by the Sun, Moon, or Earth limb. Wait up to 120 s for recovery
before proceeding.
**GO/NO-GO:** ST1 confirmed FAILED (status = 4) — proceed with redundancy switchover.

### Step 2: Power On Star Tracker 2
**LABEL:** `STEP-2`
**TC:** `ST2_POWER(on=1)` (Service 8, Subtype 1, func_id 5) — Command ST2 power on
**Verify:** Wait 60 s for ST2 boot sequence to complete.
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — Request AOCS housekeeping
**Verify:** `aocs.st2_status` (0x0243) transitions:
- 0 (OFF) -> 1 (BOOT) within 5 s of power on
- 1 (BOOT) -> 2 (TRACKING) within 60 s of power on
**Verify:** If `aocs.st2_status` remains at 1 (BOOT) after 60 s, wait up to an additional
60 s (total 120 s). Star tracker initialization may take longer if the attitude is
uncertain.
**BRANCH:** `aocs.st2_status == 2` -> GO `STEP-3` | NO-GO `STEP-5` (timeout 120 s)
**Note:** GO — ST2 is TRACKING; select it as primary and isolate ST1. NO-GO — ST2
did not acquire within 120 s; there is no second star tracker left, so jump
STRAIGHT to the COARSE_SUN fallback. Run flat, the old card would have selected a
star tracker that is not tracking (Step 3), powered off the only other one
(Step 4), AND commanded the degraded fallback (Step 5) — all three arms, in that
order, on a spacecraft that had just lost attitude reference.

### Step 3: Select Star Tracker 2 as Primary
**LABEL:** `STEP-3`
**TC:** `ST_SELECT(unit=2)` (Service 8, Subtype 1, func_id 6) — Select ST2 as the
primary attitude sensor. WIRE ENCODING IS 1=ST1, 2=ST2 (0 is refused): the old
`unit=1` here SILENTLY RE-SELECTED the failed ST1 while the card believed it
had switched to ST2 (2026-08c ruling)
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — AOCS housekeeping, 10 s after the select
**Verify:** `aocs.att_error` (0x0217) begins decreasing (attitude solution improving)
**Verify:** `aocs.st2_status == 2` (0x0243, timeout 15 s) — TRACKING, confirmed stable
**Verify:** `aocs.mode` (0x020F) — AOCS should remain in or return to the pre-failure mode
**GO/NO-GO:** ST2 selected and providing valid attitude solution — proceed to isolation.

### Step 4: Isolate Failed Star Tracker 1
**LABEL:** `STEP-4`
**TC:** `ST1_POWER(on=0)` (Service 8, Subtype 1, func_id 4) — Power off ST1 to prevent interference
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — AOCS housekeeping
**Verify:** `aocs.st1_num_stars == 0` (0x0241, timeout 15 s) — ST1 is producing nothing
**Note:** Do NOT verify `aocs.st1_status` (0x0240) = 0 (OFF) here. Measured on a
live engine: with `st1_failed` asserted, the model applies the FAILED verdict
AFTER the power gate, so 0x0240 stays 4 (FAILED) even once the unit is
unpowered. That is arguably right — a failed unit that has been switched off is
still a failed unit — but it means the OFF check can never pass on the very
fault this card exists to handle. The star count is the honest confirmation
that ST1 is out of the loop.
**Verify:** `aocs.att_error` (0x0217) < 0.1 deg (fine pointing restored)
**Verify:** `aocs.rate_roll` (0x0204) < 0.01 deg/s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.01 deg/s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.01 deg/s
**Action:** Log ST1 failure and ST2 activation in anomaly report.
**Note:** Spacecraft is now operating on the single remaining star tracker. Any
failure of ST2 will require fallback to COARSE_SUN mode (Step 5).
**BRANCH:** `aocs.st2_status == 2` -> GO `STEP-END` | NO-GO `STEP-5` (timeout 30 s)
**Note:** GO — the switchover is complete and the card ends here. NO-GO — ST2 has
also failed, and the only remaining option is the COARSE_SUN fallback.

### Step 5: Fallback — Both Star Trackers Failed, Enter COARSE_SUN Mode
**LABEL:** `STEP-5`
**Note:** If ST2 failed to reach TRACKING status or also failed (status = 4):
**TC:** `AOCS_SET_MODE(mode=3)` (Service 8, Subtype 1, func_id 0) — COARSE_SUN, the sun-safe hold
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — AOCS housekeeping
**Verify:** `aocs.mode == 3` (0x020F, timeout 20 s) — COARSE_SUN
**Note:** COARSE_SUN is a HOLD, not a destination: since Phase 1 it is not in the
model's operational-mode set, so commanding it does NOT overwrite
`aocs.commanded_mode` (0x0297), and the spacecraft will hand itself back to the
ground's commanded mode if a star tracker recovers. Watch `aocs.mode_demoted`
(0x0298) rather than re-commanding by hand.
**Verify:** `aocs.css_valid` (0x0248) = 1 (sun vector valid)
**Verify:** `aocs.css_sun_x` (0x0245), `aocs.css_sun_y` (0x0246), `aocs.css_sun_z` (0x0247)
— sun vector is being measured
**Verify:** `aocs.att_error` (0x0217) — will be larger in this mode (< 5 deg acceptable)
**Action:** Notify Flight Director: "Both star trackers failed. Operating in COARSE_SUN
mode. Fine pointing capability is LOST. Payload imaging is NOT possible."
**Note:** Only if the payload is still powered.
**TC:** `PAYLOAD_SET_MODE(mode=0)` (Service 8, Subtype 1, func_id 26) — payload OFF
**Note:** COARSE_SUN mode provides sun-pointing for power safety but does not support
nadir pointing or payload operations. This is a mission-degrading configuration.
**GO/NO-GO:** COARSE_SUN mode active, spacecraft safe. Escalate to engineering team
for star tracker recovery planning.

### Step 6: End of Procedure
**LABEL:** `STEP-END`
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — final AOCS housekeeping
**Note:** Reached either after a successful ST2 switchover (Step 4 GO) or after the
COARSE_SUN fallback (Step 5 falls through). Record which arm was taken in the
anomaly report — they leave the spacecraft in materially different configurations.

## Verification Criteria
- [ ] `aocs.st2_status` (0x0243) = 2 (TRACKING) — if ST2 switchover successful
- [ ] `aocs.att_error` (0x0217) < 0.1 deg — fine pointing restored (ST2 case)
- [ ] `aocs.st1_status` (0x0240) = 0 (OFF) — failed unit isolated
- [ ] OR `aocs.mode` (0x020F) = 3 (COARSE_SUN) — if both STs failed
- [ ] Body rates within limits (< 0.01 deg/s for fine pointing, < 1.0 deg/s for COARSE_SUN)
- [ ] No FDIR safe mode triggers during the switchover

## Contingency
- If ST2 fails during boot (status stuck at 1): Power cycle ST2 — `ST2_POWER(on=0)`,
  wait 10 s, `ST2_POWER(on=1)`. If still fails after second attempt, proceed to Step 5.
- If ST2 enters BLIND (status = 3) rather than TRACKING: The field of view may be
  obstructed. Wait up to 120 s. If in eclipse, star tracker may need sunlit phase to
  acquire stars. If still BLIND after two attempts, proceed to Step 5.
- If attitude error exceeds 5 deg during switchover: FDIR may trigger safe mode
  automatically. If FDIR triggers, allow autonomous safe mode entry. Then follow
  CTG-005 (EPS Safe Mode Recovery) for nominal restoration after ST2 is operational.
- If both star trackers fail and CSS is also invalid (eclipse): AOCS has no attitude
  reference. Command `AOCS_SET_MODE(mode=2)` for DETUMBLE using magnetorquers.
  Wait for sunlit phase for CSS acquisition. This is a mission-critical anomaly —
  escalate immediately.
- If power budget does not support both star trackers simultaneously: Keep only ST2
  powered. Do not attempt to power both units if `eps.bat_soc` < 40%.

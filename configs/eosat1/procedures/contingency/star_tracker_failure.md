# PROC-AOCS-OFF-001: Star Tracker Failure Response

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
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
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
| ST_SELECT | 8 | 1 | 6 | Select primary star tracker |
| ST1_POWER | 8 | 1 | 4 | Star tracker 1 power off (isolation) |
| AOCS_SET_MODE | 8 | 1 | 0 | Set AOCS mode (fallback to COARSE_SUN) |

## Procedure Steps

### Step 1: Confirm Star Tracker 1 Failure
**Action:** Request AOCS housekeeping: `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.st1_status` (0x0240) = 4 (FAILED)
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
**Action:** Command ST2 power on: `ST2_POWER(on=1)` (func_id 5)
**Verify:** Wait 60 s for ST2 boot sequence to complete.
**Action:** Request AOCS housekeeping: `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.st2_status` (0x0243) transitions:
- 0 (OFF) -> 1 (BOOT) within 5 s of power on
- 1 (BOOT) -> 2 (TRACKING) within 60 s of power on
**Verify:** If `aocs.st2_status` remains at 1 (BOOT) after 60 s, wait up to an additional
60 s (total 120 s). Star tracker initialization may take longer if the attitude is
uncertain.
**GO/NO-GO:** If `aocs.st2_status` = 2 (TRACKING) — proceed to Step 3. If ST2 fails
to reach TRACKING within 120 s, proceed to Step 5 (fallback).

### Step 3: Select Star Tracker 2 as Primary
**Action:** Select ST2 as the primary attitude sensor: `ST_SELECT(unit=1)` (func_id 6)
**Verify:** Wait 10 s, then request AOCS housekeeping: `HK_REQUEST(sid=2)`
**Verify:** `aocs.att_error` (0x0217) begins decreasing (attitude solution improving)
**Verify:** `aocs.st2_status` (0x0243) = 2 (TRACKING) — confirmed stable
**Verify:** `aocs.mode` (0x020F) — AOCS should remain in or return to the pre-failure mode
**GO/NO-GO:** ST2 selected and providing valid attitude solution — proceed to isolation.

### Step 4: Isolate Failed Star Tracker 1
**Action:** Power off ST1 to prevent interference: `ST1_POWER(on=0)` (func_id 4)
**Verify:** `aocs.st1_status` (0x0240) = 0 (OFF) within 5 s
**Verify:** `aocs.att_error` (0x0217) < 0.1 deg (fine pointing restored)
**Verify:** `aocs.rate_roll` (0x0204) < 0.01 deg/s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.01 deg/s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.01 deg/s
**Action:** Log ST1 failure and ST2 activation in anomaly report.
**Note:** Spacecraft is now operating on the single remaining star tracker. Any
failure of ST2 will require fallback to COARSE_SUN mode (Step 5).
**GO/NO-GO:** ST1 isolated, ST2 operational, attitude nominal — procedure complete.
If ST2 also fails, proceed to Step 5.

### Step 5: Fallback — Both Star Trackers Failed, Enter COARSE_SUN Mode
**Action:** If ST2 failed to reach TRACKING status or also failed (status = 4):
Command AOCS to coarse sun sensor mode: `AOCS_SET_MODE(mode=3)` (func_id 0) — COARSE_SUN
**Verify:** `aocs.mode` (0x020F) = 3 (COARSE_SUN) within 15 s
**Verify:** `aocs.css_valid` (0x0248) = 1 (sun vector valid)
**Verify:** `aocs.css_sun_x` (0x0245), `aocs.css_sun_y` (0x0246), `aocs.css_sun_z` (0x0247)
— sun vector is being measured
**Verify:** `aocs.att_error` (0x0217) — will be larger in this mode (< 5 deg acceptable)
**Action:** Notify Flight Director: "Both star trackers failed. Operating in COARSE_SUN
mode. Fine pointing capability is LOST. Payload imaging is NOT possible."
**Action:** Command payload to OFF if still active: `PAYLOAD_SET_MODE(mode=0)` (func_id 20)
**Note:** COARSE_SUN mode provides sun-pointing for power safety but does not support
nadir pointing or payload operations. This is a mission-degrading configuration.
**GO/NO-GO:** COARSE_SUN mode active, spacecraft safe. Escalate to engineering team
for star tracker recovery planning.

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
  CON-002 (EPS Safe Mode Recovery) for nominal restoration after ST2 is operational.
- If both star trackers fail and CSS is also invalid (eclipse): AOCS has no attitude
  reference. Command `AOCS_SET_MODE(mode=2)` for DETUMBLE using magnetorquers.
  Wait for sunlit phase for CSS acquisition. This is a mission-critical anomaly —
  escalate immediately.
- If power budget does not support both star trackers simultaneously: Keep only ST2
  powered. Do not attempt to power both units if `eps.bat_soc` < 40%.

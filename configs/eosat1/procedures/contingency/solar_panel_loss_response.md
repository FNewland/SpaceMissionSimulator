# CON-020: Solar Panel Degradation/Loss Response
**Subsystem:** EPS
**Phase:** CONTINGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Respond to the loss or significant degradation of one or more body-mounted solar
panels. EOSAT-1 has six solar panels on each face of the spacecraft body (+X, -X,
+Y, -Y, +Z, -Z). A panel failure reduces the total power generation capacity and
may create an asymmetric power profile depending on spacecraft attitude. This
procedure guides the operator through detection, identification, power budget
recalculation, and operational adjustments to maintain mission viability with
reduced power generation.

## Prerequisites
- [ ] EPS telemetry (SID 1) is being received at >= 0.5 Hz
- [ ] Spacecraft is in sunlit phase — `eps.eclipse_flag` (0x0108) = 0
- [ ] AOCS is in NADIR_POINT or known attitude mode — `aocs.mode` (0x020F) known
- [ ] Current attitude is nominal — `aocs.att_error` (0x0217) < 2.0 deg
- [ ] Per-panel current baseline values are available for current attitude and illumination

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| eps.sa_px_current | 0x012B | Per-panel current, +X face |
| eps.sa_mx_current | 0x012C | Per-panel current, -X face |
| eps.sa_py_current | 0x012D | Per-panel current, +Y face |
| eps.sa_my_current | 0x012E | Per-panel current, -Y face |
| eps.sa_pz_current | 0x012F | Per-panel current, +Z face |
| eps.sa_mz_current | 0x0130 | Per-panel current, -Z face |
| eps.sa_a_current | 0x0103 | Solar array A total current |
| eps.sa_b_current | 0x0104 | Solar array B total current |
| eps.power_gen | 0x0107 | Total power generation |
| eps.power_cons | 0x0106 | Total power consumption |
| eps.bat_soc | 0x0101 | Battery state of charge |
| eps.bus_voltage | 0x0105 | Main bus voltage |
| eps.eclipse_flag | 0x0108 | Eclipse flag (must be 0 for valid assessment) |
| aocs.mode | 0x020F | Current AOCS mode |
| aocs.att_error | 0x0217 | Attitude error |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| PAYLOAD_SET_MODE | 8 | 1 | 20 | Set payload mode |
| FPA_COOLER | 8 | 1 | 33 | Control FPA cooler |
| AOCS_SET_MODE | 8 | 1 | 0 | Set AOCS mode |

## Procedure Steps

### Step 1: Detect and Confirm Power Generation Anomaly
**Action:** Request EPS housekeeping: `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.power_gen` (0x0107) — compare to expected value for current attitude
and illumination. If shortfall > 15%, proceed with investigation.
**Verify:** `eps.eclipse_flag` (0x0108) = 0 — confirm spacecraft is in sunlit phase.
If in eclipse, wait for sunlit phase before assessing panel performance.
**Verify:** `eps.sa_a_current` (0x0103) — record array A total current
**Verify:** `eps.sa_b_current` (0x0104) — record array B total current
**Action:** If total generation is below expected, proceed to per-panel analysis.
**GO/NO-GO:** Power generation shortfall confirmed during sunlit phase — proceed.

### Step 2: Identify Affected Panel(s) via Per-Panel Current Analysis
**Action:** Request EPS housekeeping: `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
Sample 3 times at 30 s intervals for trend confirmation.
**Verify:** `eps.sa_px_current` (0x012B) — +X face current
**Verify:** `eps.sa_mx_current` (0x012C) — -X face current
**Verify:** `eps.sa_py_current` (0x012D) — +Y face current
**Verify:** `eps.sa_my_current` (0x012E) — -Y face current
**Verify:** `eps.sa_pz_current` (0x012F) — +Z face current
**Verify:** `eps.sa_mz_current` (0x0130) — -Z face current
**Action:** Compare each panel current to the expected value for that face's
illumination angle. A panel producing significantly less current (< 50% of expected)
or zero current is considered degraded or failed.
**Action:** Document which panel(s) are affected and the degree of degradation:
- Partial degradation: producing current but < 50% of expected
- Complete loss: producing 0 A despite being illuminated
**Note:** Panels on faces not currently illuminated (shadowed faces in current
attitude) will naturally show ~0 A and should not be flagged.
**GO/NO-GO:** Affected panel(s) identified — proceed to impact assessment.

### Step 3: Rule Out Attitude Error as Root Cause
**Action:** Request AOCS housekeeping: `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.mode` (0x020F) = 3 (NADIR_POINT) or 2 (SAFE_POINT)
**Verify:** `aocs.att_error` (0x0217) < 2.0 deg — confirms attitude is nominal
**Action:** If `aocs.att_error` > 5.0 deg, the power shortfall may be caused by
poor solar array illumination due to attitude error. Execute CON-002 (AOCS Anomaly
Recovery) first, then return to this procedure at Step 1.
**Verify:** `aocs.rate_roll` (0x0204) < 0.1 deg/s — no unexpected rotation
**Verify:** `aocs.rate_pitch` (0x0205) < 0.1 deg/s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.1 deg/s
**GO/NO-GO:** Attitude confirmed nominal. Degradation is intrinsic to panel hardware
— proceed to power budget recalculation.

### Step 4: Recalculate Power Budget with Degraded Generation
**Action:** Request EPS housekeeping: `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.power_gen` (0x0107) — actual degraded generation (sunlit)
**Verify:** `eps.power_cons` (0x0106) — current total consumption
**Action:** Calculate instantaneous power margin = generation - consumption.
**Action:** Calculate orbit-averaged power budget:
- Sunlit power margin x sunlit fraction of orbit
- Eclipse consumption x eclipse fraction of orbit
- Net must be positive for sustainable operations
**Action:** Project minimum battery SoC at end of next eclipse using degraded values.
**Verify:** `eps.bat_soc` (0x0101) — record current value as baseline.
**GO/NO-GO:** If orbit-averaged power budget is positive and projected minimum SoC
> 25%, continue nominal operations with monitoring. If negative or SoC < 25%
projected, proceed to Step 5.

### Step 5: Shed Loads to Match Reduced Power Budget
**Action:** Announce "Solar panel degradation confirmed — reducing power consumption
to match available generation" on the operations loop.

**Step 5a — Reduce Payload to STANDBY:**
**Action:** `PAYLOAD_SET_MODE(mode=1)` (func_id 20) — reduce from IMAGING to STANDBY
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10 s
**Verify:** `eps.power_cons` (0x0106) decreases by >= 12 W

**Step 5b — If margin still insufficient, disable FPA cooler:**
**Action:** `FPA_COOLER(on=0)` (func_id 33)
**Verify:** `tcs.cooler_fpa` (0x040C) = 0 within 5 s
**Verify:** `eps.power_cons` (0x0106) decreases by ~15 W

**Step 5c — If margin still insufficient, shut down payload completely:**
**Action:** `PAYLOAD_SET_MODE(mode=0)` (func_id 20) — full payload OFF
**Verify:** `payload.mode` (0x0600) = 0 (OFF) within 10 s

**Action:** Recalculate orbit-averaged power budget with reduced loads.
**Verify:** `eps.power_cons` (0x0106) — confirm margin is positive.
**GO/NO-GO:** Power budget is positive with at least 3 W reserve — proceed.

### Step 6: Consider Attitude Optimisation for Power Recovery
**Action:** If the degraded panel(s) are on a face that receives maximum illumination
in NADIR_POINT mode, consider temporarily switching to SAFE_POINT (sun-optimal) to
maximise generation from undamaged panels.
**Action:** `AOCS_SET_MODE(mode=2)` (func_id 0) — SAFE_POINT
**Verify:** `aocs.mode` (0x020F) = 2 within 15 s
**Action:** Wait 120 s for new attitude to stabilise, then request EPS HK.
**Verify:** `eps.power_gen` (0x0107) — compare to NADIR_POINT generation.
**Note:** SAFE_POINT sacrifices nadir pointing (no payload imaging) but may improve
power generation by up to 30% depending on which panel(s) are lost.
**GO/NO-GO:** If SAFE_POINT provides significantly better power margin, maintain
this mode until battery is sufficiently charged. If no improvement, return to
NADIR_POINT: `AOCS_SET_MODE(mode=3)`.

### Step 7: Establish Long-Term Monitoring and Operations Plan
**Action:** Record the following as the new degraded baseline:
- Per-panel currents (0x012B-0x0130) for each illuminated face
- Total power generation in NADIR_POINT and SAFE_POINT modes
- Orbit-averaged power budget with reduced operations
**Action:** Schedule daily trend monitoring — check if degradation is progressive
(worsening) or stable.
**Action:** Coordinate with mission planning to:
- Adjust imaging schedule to fit within degraded power budget
- Plan imaging only during orbit phases with best remaining panel illumination
- Consider duty-cycling payload (image one orbit, charge next orbit)
**Action:** Assess operations on reduced power:
- Single panel loss (~15% capacity reduction): Reduced imaging duty cycle
- Two panel loss (~30% reduction): Payload imaging only during optimal illumination
- Three or more panels lost: Mission-degrading — consider safe mode ops only
**GO/NO-GO:** Monitoring plan established and operations plan adjusted — procedure
complete.

## Verification Criteria
- [ ] Affected panel(s) identified by face and degradation level documented
- [ ] Attitude error ruled out as root cause
- [ ] Power budget recalculated with degraded generation — confirmed positive
- [ ] Operations plan adjusted to fit within available power envelope
- [ ] Daily trend monitoring initiated
- [ ] `eps.bat_soc` (0x0101) > 25% and stable or rising
- [ ] `eps.bus_voltage` (0x0105) > 27.0 V

## Off-Nominal Handling
- If all panels on sun-facing side are lost: Switch to SAFE_POINT immediately.
  If total generation < 15 W, command `OBC_SET_MODE(mode=2)` for EMERGENCY. This
  is a mission-critical anomaly — escalate immediately.
- If degradation is progressive (> 5% per day): Suspect radiation damage, thermal
  cycling failure, or wiring degradation. Accelerate mission data downlink and
  plan for end-of-mission contingency.
- If `eps.bat_soc` drops below 20% during investigation: Interrupt this procedure
  and execute CON-001 (Undervoltage Load Shed) or CON-019 (Progressive Load Shed).
- If attitude error was the cause and persists: The solar array shortfall is a
  symptom, not the root cause. Focus on AOCS recovery first.
- If per-panel telemetry is inconsistent (e.g., illuminated face shows 0 A but
  array total is nominal): Suspect telemetry sensor failure rather than panel
  failure. Cross-check with total array current values (0x0103, 0x0104).

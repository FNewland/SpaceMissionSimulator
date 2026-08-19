# LEOP-003: Initial Orbit Determination
**Subsystem:** AOCS / Flight Dynamics
**Phase:** LEOP
**Revision:** 2.0 --- 2026-08c flow redesign. This is now the LAST LEOP card: it
runs AFTER the GPS receiver has been powered (LEOP-007), the clock synced
(LEOP-006) and HK SID 12 enabled, verifies the PVT solution on the parameters
the spacecraft actually downlinks (0x0210-0x0212 / 0x0231-0x0236 --- the old
card read 0x0230-0x0236 as lat/lon/alt, which are now the ECEF state), and
ends LEOP with the phase-5 declaration.
**Approved:** Flight Operations Director

**Entry state:** LEOP-006 exit state --- COARSE_SUN sun hold, clock referenced
(0x0A13 in {1,2,3}), GPS receiver ON with `aocs.gps_fix` (0x0274) >= 2, HK
SID 12 (GPS_NAV) enabled and emitting every 8 s.

## Purpose
Verify the on-board GPS navigation solution, compare the measured orbit with
the launcher-predicted sun-synchronous orbit, distribute the refined elements
to Flight Dynamics and the ground-station network --- and, on a GO from all
positions, DECLARE THE END OF LEOP by commanding mission phase 5
(COMMISSIONING). Nothing on board advances the phase past 4; the declaration
is this card's flight telecommand, sent once, here, and nowhere else.

## Prerequisites
- [ ] LEOP-001 through LEOP-007 (definitive order 001, 002, 004, 007, 005,
      006) completed
- [ ] `aocs.gps_fix` (0x0274) >= 2 and HK SID 12 on the downlink (LEOP-006)
- [ ] On-board time referenced (`obdh.time_ref` 0x0A13 != 0, LEOP-006)
- [ ] Bidirectional S-band link active on a scheduled pass
- [ ] Flight Dynamics team ready with the predicted TLE from the launch provider

## Procedure Steps

### Step 1 --- Verify the GPS Navigation Solution Is Downlinking
**TC:** `HK_REQUEST(sid=12)` (Service 3, Subtype 27) --- one-shot GPS_NAV report
**Verify:** `aocs.gps_fix` (0x0274) >= 2 within 10s --- 3D fix (0=none, 1=2D, 2=3D, 3=3D+vel)
**Verify:** `aocs.gps_num_sats` (0x0276) >= 4 within 10s --- enough satellites for a 3D solution
**Verify:** `aocs.gps_pdop` (0x0275) reported within 10s --- record the dilution
**Note:** The PVT channels are honest-invalid: position (0x0210-0x0212,
0x0231-0x0233) reads zero below fix 2 and velocity (0x0234-0x0236) reads zero
below fix 3. A zeroed solution here means WAIT for the fix, not "the orbit is
at the centre of the Earth".
**GO/NO-GO:** Fix quality sufficient for orbit determination

### Step 2 --- Read the Position Fix
**TC:** `GET_PARAM(param_id=0x0210)` (Service 20, Subtype 3) --- `aocs.gps_lat` (deg)
**TC:** `GET_PARAM(param_id=0x0211)` (Service 20, Subtype 3) --- `aocs.gps_lon` (deg)
**TC:** `GET_PARAM(param_id=0x0212)` (Service 20, Subtype 3) --- `aocs.gps_alt` (km)
**Verify:** `aocs.gps_lat` (0x0210) in range [-90, +90] within 10s
**Verify:** `aocs.gps_lon` (0x0211) in range [-180, +180] within 10s
**Verify:** `aocs.gps_alt` (0x0212) in range [495 km, 540 km] within 10s
**Note:** Band = the TLE-propagated geodetic altitude range (503-530 km over
an orbit, mean 513 km) plus ~8-10 km of margin. The historical 450 km
declaration was retired by the 2026-08 owner ruling: the orbit.yaml TLE
(mean motion 15.24 rev/day) is the truth and `altitude_km` now records the
propagated mean, 513 km.
**GO/NO-GO:** Position fix valid and altitude in the accepted LEO band

### Step 3 --- Read the ECEF State Vector
**TC:** `GET_PARAM(param_id=0x0231)` (Service 20, Subtype 3) --- ECEF X (m)
**TC:** `GET_PARAM(param_id=0x0232)` (Service 20, Subtype 3) --- ECEF Y (m)
**TC:** `GET_PARAM(param_id=0x0233)` (Service 20, Subtype 3) --- ECEF Z (m)
**TC:** `GET_PARAM(param_id=0x0234)` (Service 20, Subtype 3) --- ECEF velocity X (m/s)
**TC:** `GET_PARAM(param_id=0x0235)` (Service 20, Subtype 3) --- ECEF velocity Y (m/s)
**TC:** `GET_PARAM(param_id=0x0236)` (Service 20, Subtype 3) --- ECEF velocity Z (m/s)
**Verify:** Position magnitude sqrt(X^2+Y^2+Z^2) in range [6700 km, 6950 km]
**Verify:** Velocity magnitude in range [7400 m/s, 7900 m/s] --- circular-orbit
speed at the measured altitude (sqrt(mu/a), mu = 398600.4418 km^3/s^2)
**Action:** Record the full state vector with its GPS timestamp for Flight
Dynamics.
**GO/NO-GO:** State vector self-consistent (r x v sensible for a near-circular LEO)

### Step 4 --- Flight Dynamics Comparison
**Action:** Transmit the GPS-derived state vector to Flight Dynamics. Flight
Dynamics computes osculating elements and compares with the launcher-predicted
orbit: inclination within 0.2 deg of 98.0 deg, eccentricity < 0.01, LTAN
within 15 minutes of the mission requirement.
**Action:** Confirm the orbit period against the TLE mean motion; note the
altitude discrepancy from Step 2 in the comparison.
**GO/NO-GO:** Orbital elements consistent with a sun-synchronous insertion

### Step 5 --- Second Fix Consistency Check
**Action:** Wait 10 minutes (SID 12 keeps sampling every 8 s).
**TC:** `HK_REQUEST(sid=12)` (Service 3, Subtype 27) --- second GPS_NAV report
**Verify:** `aocs.gps_alt` (0x0212) within 20 km of the Step 2 reading
**Verify:** `aocs.gps_fix` (0x0274) still >= 2
**Action:** Confirm the position moved along-track consistently with the
propagated orbit over the interval.
**GO/NO-GO:** GPS fixes consistent and repeatable

### Step 6 --- Distribute the Orbit Determination Report
**Action:** Compile all GPS readings, computed elements, the comparison with
the predicted orbit and the LTAN verification. Distribute the Orbit
Determination Report and the refined TLE to the ground-station network for
pass-prediction updates.
**GO/NO-GO:** Orbit determination complete

### Step 7 --- GO/NO-GO Poll and END-OF-LEOP Phase Declaration
**Action:** FD initiates the GO/NO-GO poll across all positions. Each position
reports subsystem status. This is the ONLY place the phase-5 declaration is
sent; no other card carries it.
**Note:** The command is `critical` --- the phase gates telecommand acceptance,
the derived spacecraft mode, FDIR arming and the propulsion arm interlock ---
so read the argument back before calling the step complete. It is also the
FD's quick command (OBDH group, "Set Mission Phase") in the MCS.
**TC:** `SYS_SET_MISSION_PHASE(phase=5, flags=0)` (Service 8, Subtype 1, func_id 120)
**Verify:** `eps.spacecraft_phase` (0x0129) = 5 within 5s
**Action:** Confirm event `MISSION_PHASE_CHANGED` (0x0055) appears in the
event log carrying `4 (LEOP) -> 5 (COMMISSIONING)`.
**GO/NO-GO:** All positions GO; phase = COMMISSIONING --- LEOP is over

## Off-Nominal Handling
- If `aocs.gps_fix` < 2: confirm the receiver device is ON (`S2_AOCS_GPS_RECEIVER` device 0x020F, LEOP-007). Wait out the cold start (up to 15 min). If no fix after 30 minutes, fall back to ground-based range/Doppler from Iqaluit and Troll for the initial orbit and schedule a GPS investigation for commissioning; the phase declaration may still proceed on a ground-tracked orbit with FD approval.
- If SID 12 is absent from the downlink: `HK_ENABLE(sid=12)` (Service 3, Subtype 5) was missed or lost (LEOP-006 Step 6a) --- re-issue it.
- If the altitude is outside even the wide [400, 550] km band: verify PDOP first. With a good fix this is an insertion anomaly --- alert Flight Dynamics and assess a correction manoeuvre before declaring phase 5.
- If inclination differs from 98.0 deg by more than 0.2 deg: flag to Flight Dynamics; sun-synchronous precession and mission lifetime are affected.
- If `SYS_SET_MISSION_PHASE` is refused: 0x0C03 means the OBC is (back) in the bootloader --- re-run LEOP-001 Step 5 and LEOP-006 first. 0x0C04 means a backwards declaration without FORCE.

## Exit state
- Mission phase = 5 (COMMISSIONING); `sat.mode` (0x0001) still derives SAFE ---
  only the phase-6 declaration at the END of commissioning (COM-013) unlocks
  NOMINAL
- Spacecraft still in the COARSE_SUN hold, wheels disabled, payload dark ---
  exactly the entry state COM-001 declares
- GPS receiver ON, SID 12 emitting (left running: commissioning and nominal
  ops use it)

## Post-Conditions
- [ ] GPS PVT verified on the downlinked SID 12 channels
- [ ] State vector recorded and delivered to Flight Dynamics
- [ ] Orbit Determination Report + refined TLE distributed
- [ ] Phase 5 declared (event 0x0055 logged) --- LEOP complete

**NEXT:** Commissioning, starting with COM-001 (EPS Checkout). The definitive
commissioning order is COM-001 ... COM-013 as indexed, with COM-003 (sensor
calibration) deliberately BEFORE COM-004 (wheel enable).

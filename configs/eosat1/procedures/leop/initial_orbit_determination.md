# LEOP-005: Initial Orbit Determination
**Subsystem:** AOCS / Flight Dynamics
**Phase:** LEOP
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Verify the on-board GPS receiver has achieved a valid position fix. Compare measured
orbital parameters with the predicted 500 km sun-synchronous orbit (97.4 degree
inclination). Validate orbit altitude, inclination, and LTAN against mission
requirements to confirm correct orbit insertion by the launch vehicle.

## Prerequisites
- [ ] LEOP-001 through LEOP-004 completed successfully
- [ ] Spacecraft in SAFE_POINT mode with stable attitude
- [ ] On-board time synchronized (LEOP-001 Step 5)
- [ ] GPS receiver powered on (automatic at separation)
- [ ] Bidirectional VHF/UHF link active
- [ ] Flight Dynamics team ready with predicted TLE from launch provider

## Procedure Steps

### Step 1 — Check GPS Receiver Status
**TC:** `GET_PARAM(0x0230)` (Service 20, Subtype 1) — GPS receiver power status
**Verify:** GPS receiver power = ON (value 1) within 10s
**TC:** `GET_PARAM(0x0231)` (Service 20, Subtype 1) — GPS receiver mode
**Verify:** GPS mode = NAVIGATING (value 2) within 10s
**GO/NO-GO:** GPS receiver operational and in navigation mode

### Step 2 — Verify GPS Satellite Visibility
**TC:** `GET_PARAM(0x0232)` (Service 20, Subtype 1) — number of tracked GPS satellites
**Verify:** Number of tracked satellites >= 4 within 30s
**Action:** If fewer than 4 satellites tracked, wait up to 5 minutes for acquisition. GPS cold start in LEO may require up to 15 minutes.
**GO/NO-GO:** Sufficient GPS satellites tracked for 3D position fix

### Step 3 — Read GPS Position Fix
**TC:** `GET_PARAM(0x0233)` (Service 20, Subtype 1) — GPS latitude (degrees)
**TC:** `GET_PARAM(0x0234)` (Service 20, Subtype 1) — GPS longitude (degrees)
**TC:** `GET_PARAM(0x0235)` (Service 20, Subtype 1) — GPS altitude (km)
**Verify:** GPS latitude in range [-90, +90] within 10s
**Verify:** GPS longitude in range [-180, +180] within 10s
**Verify:** GPS altitude in range [480 km, 520 km] within 10s
**GO/NO-GO:** GPS position fix valid and altitude consistent with 500 km orbit

### Step 4 — Read GPS Velocity Vector
**TC:** `GET_PARAM(0x0236)` (Service 20, Subtype 1) — GPS velocity magnitude (m/s)
**Verify:** GPS velocity in range [7550 m/s, 7650 m/s] within 10s
**Action:** Record velocity vector. Expected orbital velocity at 500 km altitude is approximately 7613 m/s.
**GO/NO-GO:** Velocity magnitude consistent with 500 km circular orbit

### Step 5 — Compare with Predicted Orbit
**Action:** Transmit GPS-derived state vector to Flight Dynamics. Flight Dynamics computes osculating orbital elements and compares with launcher-predicted orbit.
**Verify:** Semi-major axis within 5 km of predicted (6878 km nominal)
**Verify:** Eccentricity < 0.005 (near-circular)
**Verify:** Inclination within 0.1 degrees of 97.4 degrees
**GO/NO-GO:** Orbital elements consistent with predicted sun-synchronous orbit

### Step 6 — Verify Sun-Synchronous Orbit Properties
**Action:** Flight Dynamics confirms LTAN (Local Time of Ascending Node) matches mission requirement. For a 500 km SSO, the J2 precession rate maintains the sun-synchronous condition at 97.4 degree inclination.
**Verify:** LTAN within 15 minutes of target value (as specified by mission requirement)
**Verify:** Orbit period approximately 94.6 minutes (5676 seconds +/- 30s)
**GO/NO-GO:** Sun-synchronous orbit confirmed within tolerance

### Step 7 — Update On-Board Orbit Propagator
**TC:** `SET_PARAM(0x0240, <epoch_time>)` (Service 20, Subtype 3) — propagator epoch
**TC:** `SET_PARAM(0x0241, <state_vector>)` (Service 20, Subtype 3) — propagator state
**Action:** Upload refined state vector from Flight Dynamics to on-board orbit propagator for autonomous eclipse prediction and ground station pass scheduling.
**Verify:** Propagator state update acknowledged within 10s
**GO/NO-GO:** On-board propagator updated with refined orbit

### Step 8 — Second GPS Fix Comparison
**Action:** Wait 10 minutes and acquire a second GPS position fix to verify consistency.
**TC:** `GET_PARAM(0x0233)` (Service 20, Subtype 1) — GPS latitude
**TC:** `GET_PARAM(0x0234)` (Service 20, Subtype 1) — GPS longitude
**TC:** `GET_PARAM(0x0235)` (Service 20, Subtype 1) — GPS altitude
**Verify:** Altitude remains in range [480 km, 520 km] within 10s
**Action:** Confirm position is consistent with orbit propagation over the 10-minute interval.
**GO/NO-GO:** GPS fixes consistent and repeatable

### Step 9 — Generate Orbit Determination Report
**Action:** Compile all GPS readings, computed orbital elements, comparison with predicted orbit, and LTAN verification. Distribute Orbit Determination Report. Provide refined TLE to ground station network for pass prediction updates.
**GO/NO-GO:** Orbit determination complete — LEOP phase concluded

## Off-Nominal Handling
- If GPS receiver not powered on: Command `SET_PARAM(0x0230, 1)` to power on GPS. Wait 15 minutes for cold start acquisition. If receiver does not respond, check power bus via EPS telemetry.
- If fewer than 4 satellites tracked after 15 minutes: Check GPS antenna pointing (may be obscured by spacecraft body in current attitude). Consider rotating spacecraft if safe to do so. Use ground-based ranging as backup for orbit determination.
- If GPS altitude outside [480, 520] km range: Verify GPS fix quality (PDOP value). If fix quality good, alert Flight Dynamics — may indicate orbit insertion error. Assess mission impact and potential orbit correction manoeuvre requirements.
- If inclination differs from 97.4 deg by more than 0.2 deg: Flag to Flight Dynamics for assessment. Small inclination errors may be correctable; large errors may impact sun-synchronous properties and mission lifetime.
- If GPS provides no fix after 30 minutes: Use ground-based tracking (range/range-rate from Svalbard, Troll, Inuvik, O'Higgins) for initial orbit determination. Schedule GPS receiver investigation during commissioning.

## Post-Conditions
- [ ] GPS receiver operational with valid 3D fix
- [ ] Orbit altitude confirmed approximately 500 km
- [ ] Inclination confirmed approximately 97.4 degrees
- [ ] Orbit eccentricity confirmed near-circular (< 0.005)
- [ ] LTAN verified within mission requirements
- [ ] On-board orbit propagator updated with refined state vector
- [ ] Orbit Determination Report distributed to mission team
- [ ] Refined TLE distributed to ground station network
- [ ] LEOP phase complete — GO for Commissioning phase

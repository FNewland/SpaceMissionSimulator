# LEOP-003: Initial Orbit Determination
**Subsystem:** AOCS / Flight Dynamics
**Phase:** LEOP
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Verify the on-board GPS receiver has achieved a valid position fix. Compare measured
orbital parameters with the predicted 450 km sun-synchronous orbit (98.0 degree
inclination). Validate orbit altitude, inclination, and LTAN against mission
requirements to confirm correct orbit insertion by the launch vehicle.

## Prerequisites
- [ ] LEOP-001 through LEOP-004 completed successfully
- [ ] Spacecraft in COARSE_SUN mode with stable attitude
- [ ] On-board time synchronized (LEOP-001 Step 5)
- [ ] GPS receiver powered on (automatic at separation)
- [ ] Bidirectional S-band link active
- [ ] Flight Dynamics team ready with predicted TLE from launch provider

## Procedure Steps

### Step 1 — Check GPS Receiver Status
**TC:** `GET_PARAM(param_id=0x0230)` (Service 20, Subtype 3) — GPS receiver power status
**Verify:** GPS receiver power = ON (value 1) within 10s
**TC:** `GET_PARAM(param_id=0x0231)` (Service 20, Subtype 3) — GPS receiver mode
**Verify:** GPS mode = NAVIGATING (value 2) within 10s
**GO/NO-GO:** GPS receiver operational and in navigation mode

### Step 2 — Verify GPS Satellite Visibility
**TC:** `GET_PARAM(param_id=0x0232)` (Service 20, Subtype 3) — number of tracked GPS satellites
**Verify:** Number of tracked satellites >= 4 within 30s
**Action:** If fewer than 4 satellites tracked, wait up to 5 minutes for acquisition. GPS cold start in LEO may require up to 15 minutes.
**GO/NO-GO:** Sufficient GPS satellites tracked for 3D position fix

### Step 3 — Read GPS Position Fix
**TC:** `GET_PARAM(param_id=0x0233)` (Service 20, Subtype 3) — GPS latitude (degrees)
**TC:** `GET_PARAM(param_id=0x0234)` (Service 20, Subtype 3) — GPS longitude (degrees)
**TC:** `GET_PARAM(param_id=0x0235)` (Service 20, Subtype 3) — GPS altitude (km)
**Verify:** GPS latitude in range [-90, +90] within 10s
**Verify:** GPS longitude in range [-180, +180] within 10s
**Verify:** GPS altitude in range [430 km, 470 km] within 10s
**GO/NO-GO:** GPS position fix valid and altitude consistent with the 450 km orbit

### Step 4 — Read GPS Velocity Vector
**TC:** `GET_PARAM(param_id=0x0236)` (Service 20, Subtype 3) — GPS velocity magnitude (m/s)
**Verify:** GPS velocity in range [7550 m/s, 7650 m/s] within 10s
**Action:** Record velocity vector. Expected circular velocity at 450 km altitude is
approximately **7644 m/s** — sqrt(mu/a) with mu = 398600.4418 km^3/s^2 and
a = 6371 + 450 = 6821 km.
**GO/NO-GO:** Velocity magnitude consistent with the 450 km circular orbit

### Step 5 — Compare with Predicted Orbit
**Action:** Transmit GPS-derived state vector to Flight Dynamics. Flight Dynamics computes osculating orbital elements and compares with launcher-predicted orbit.
**Verify:** Semi-major axis within 5 km of predicted (**6821 km** nominal =
`earth_radius_km` 6371 + `altitude_km` 450 from `configs/eosat1/orbit.yaml`)
**Verify:** Eccentricity < 0.005 (near-circular)
**Verify:** Inclination within 0.1 degrees of 98.0 degrees
**GO/NO-GO:** Orbital elements consistent with predicted sun-synchronous orbit

### Step 6 — Verify Sun-Synchronous Orbit Properties
**Action:** Flight Dynamics confirms LTAN (Local Time of Ascending Node) matches the
mission requirement. For the 450 km SSO, the J2 precession rate maintains the
sun-synchronous condition at **98.0 degree** inclination — the value in
`orbit.yaml` (`inclination_deg: 98.0`) and in the shipped TLE.
**Verify:** LTAN within 15 minutes of target value (as specified by mission requirement)
**Verify:** Orbit period approximately **93.4 minutes (5606 seconds +/- 30 s)** —
2*pi*sqrt(a^3/mu) at a = 6821 km. Cross-check against the TLE mean motion in
`orbit.yaml`, 15.24 rev/day = 5669 s; the two differ because the TLE carries a
small eccentricity and is a mean element set, so anything in 5600-5680 s is
consistent.
**GO/NO-GO:** Sun-synchronous orbit confirmed within tolerance

### Step 7 — Update On-Board Orbit Propagator
**TC:** `SET_PARAM(param_id=0x0240, value=EPOCH)` (Service 20, Subtype 1) — propagator epoch
**TC:** `SET_PARAM(param_id=0x0241, value=STATE)` (Service 20, Subtype 1) — propagator state
**Note:** S20.1 carries ONE float32 value per command (`param_id` + `value`),
so a full state vector is uploaded one component at a time, not as a
single blob.
**Action:** Upload refined state vector from Flight Dynamics to on-board orbit propagator for autonomous eclipse prediction and ground station pass scheduling.
**Verify:** Propagator state update acknowledged within 10s
**GO/NO-GO:** On-board propagator updated with refined orbit

### Step 8 — Second GPS Fix Comparison
**Action:** Wait 10 minutes and acquire a second GPS position fix to verify consistency.
**TC:** `GET_PARAM(param_id=0x0233)` (Service 20, Subtype 3) — GPS latitude
**TC:** `GET_PARAM(param_id=0x0234)` (Service 20, Subtype 3) — GPS longitude
**TC:** `GET_PARAM(param_id=0x0235)` (Service 20, Subtype 3) — GPS altitude
**Verify:** Altitude remains in range [430 km, 470 km] within 10s
**Action:** Confirm position is consistent with orbit propagation over the 10-minute interval.
**GO/NO-GO:** GPS fixes consistent and repeatable

### Step 9 — Generate Orbit Determination Report
**Action:** Compile all GPS readings, computed orbital elements, comparison with predicted orbit, and LTAN verification. Distribute Orbit Determination Report. Provide refined TLE to ground station network for pass prediction updates.
**GO/NO-GO:** Orbit determination complete — LEOP phase concluded

## Off-Nominal Handling
- If GPS receiver not powered on: Command `SET_PARAM(param_id=0x0230, value=1)` to power on GPS. Wait 15 minutes for cold start acquisition. If receiver does not respond, check power bus via EPS telemetry.
- If fewer than 4 satellites tracked after 15 minutes: Check GPS antenna pointing (may be obscured by spacecraft body in current attitude). Consider rotating spacecraft if safe to do so. Use ground-based ranging as backup for orbit determination.
- If GPS altitude outside [430, 470] km range: Verify GPS fix quality (PDOP value). If fix quality good, alert Flight Dynamics — may indicate orbit insertion error. Assess mission impact and potential orbit correction manoeuvre requirements.
- If inclination differs from 98.0 deg by more than 0.2 deg: Flag to Flight Dynamics for assessment. Small inclination errors may be correctable; large errors may impact sun-synchronous properties and mission lifetime.
- If GPS provides no fix after 30 minutes: Use ground-based tracking (range/range-rate from Iqaluit and Troll) for initial orbit determination. Schedule GPS receiver investigation during commissioning.

## Post-Conditions
- [ ] GPS receiver operational with valid 3D fix
- [ ] Orbit altitude confirmed approximately 450 km
- [ ] Inclination confirmed approximately 98.0 degrees
- [ ] Orbit eccentricity confirmed near-circular (< 0.005)
- [ ] LTAN verified within mission requirements
- [ ] On-board orbit propagator updated with refined state vector
- [ ] Orbit Determination Report distributed to mission team
- [ ] Refined TLE distributed to ground station network
- [ ] LEOP phase complete — GO for Commissioning phase

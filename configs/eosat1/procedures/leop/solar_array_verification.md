# LEOP-003: Solar Array Deployment Verification
**Subsystem:** EPS / Structure
**Phase:** LEOP
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Verify that both solar array wings (SA-A and SA-B) have fully deployed following
launch vehicle separation. Confirm deployment switch telemetry, measure individual
string currents, and validate total power generation against predictions for the
current sun angle and orbit phase.

## Prerequisites
- [ ] LEOP-001 (First Acquisition) completed successfully
- [ ] LEOP-002 (Initial Health Assessment) completed — all subsystems nominal
- [ ] Bidirectional VHF/UHF link active with link margin > 3 dB
- [ ] Spacecraft in sunlit portion of orbit (preferred) or approaching eclipse exit
- [ ] EPS telemetry confirmed available (SID 1 responsive)
- [ ] Flight Dynamics have provided current beta angle and sun vector

## Procedure Steps

### Step 1 — Request Detailed EPS Telemetry
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) in range [27.0V, 29.5V] within 10s
**Verify:** `eps.bat_soc` (0x0101) reported within 10s
**GO/NO-GO:** EPS telemetry responding nominally

### Step 2 — Check Solar Array A Deployment Status
**TC:** `GET_PARAM(0x0110)` (Service 20, Subtype 1) — SA-A deploy switch status
**Verify:** SA-A deploy switch = DEPLOYED (value 1) within 10s
**TC:** `GET_PARAM(0x0112)` (Service 20, Subtype 1) — SA-A string current
**Verify:** SA-A string current > 0.1A when in sunlight within 10s
**GO/NO-GO:** SA-A confirmed deployed and generating current

### Step 3 — Check Solar Array B Deployment Status
**TC:** `GET_PARAM(0x0111)` (Service 20, Subtype 1) — SA-B deploy switch status
**Verify:** SA-B deploy switch = DEPLOYED (value 1) within 10s
**TC:** `GET_PARAM(0x0113)` (Service 20, Subtype 1) — SA-B string current
**Verify:** SA-B string current > 0.1A when in sunlight within 10s
**GO/NO-GO:** SA-B confirmed deployed and generating current

### Step 4 — Measure Total Power Generation
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.power_gen` (0x0107) > 20W (minimum during tumbling with partial sun) within 10s
**Action:** Record exact power generation value. Compare with prediction from Flight Dynamics based on current attitude and sun angle.
**GO/NO-GO:** Power generation consistent with both arrays deployed

### Step 5 — Monitor Power Through Eclipse Transition (if applicable)
**Action:** If pass spans an eclipse entry or exit, monitor power generation transition.
**Verify:** `eps.power_gen` (0x0107) drops to ~0W during eclipse within 30s of predicted entry
**Verify:** `eps.power_gen` (0x0107) recovers to >20W within 60s of predicted eclipse exit
**Verify:** `eps.bat_soc` (0x0101) decreasing during eclipse at rate consistent with load
**GO/NO-GO:** Eclipse power profile consistent with nominal array operation

### Step 6 — Verify Array Symmetry
**Action:** Compare SA-A and SA-B string currents. Arrays should produce similar current at equivalent sun angles, within 15% of each other.
**TC:** `GET_PARAM(0x0112)` (Service 20, Subtype 1) — SA-A current
**TC:** `GET_PARAM(0x0113)` (Service 20, Subtype 1) — SA-B current
**Verify:** |SA-A current - SA-B current| / max(SA-A, SA-B) < 0.15 within 10s
**GO/NO-GO:** Array currents balanced within 15% tolerance

### Step 7 — Estimate Power Budget Margin
**Action:** Calculate current power consumption from bus voltage and load current. Compare with power generation. Verify positive energy balance over one orbit (when sun-pointed).
**Verify:** `eps.power_gen` (0x0107) > 50W expected when sun-pointed (post LEOP-004)
**Action:** Record power budget estimate in LEOP log. Confirm sufficient margin for commissioning activities.
**GO/NO-GO:** Positive power budget confirmed or achievable after sun acquisition

## Off-Nominal Handling
- If SA-A or SA-B deploy switch reads STOWED (value 0): Check if power generation from that wing is truly zero. If zero current confirmed, attempt deployment retry via `SET_PARAM(0x0120, 1)` for SA-A or `SET_PARAM(0x0121, 1)` for SA-B — requires Flight Director approval. If retry fails, assess single-wing power budget viability.
- If both arrays deployed but power generation < 10W in sunlight: Verify spacecraft is actually in sunlight via Flight Dynamics. Check for excessive tumble rate reducing effective solar incidence. Prioritize LEOP-004 (Sun Acquisition) to stabilize attitude.
- If array currents asymmetric > 30%: Log anomaly. One array may have partial deployment or string failure. Continue LEOP with reduced power budget. Schedule detailed EPS investigation during commissioning.
- If `eps.bat_soc` dropping rapidly (> 2% per minute): Reduce spacecraft load — disable non-essential heaters via `HEATER_CONTROL(circuit=2, on=0)`. Ensure AOCS detumble is using magnetorquers only (lower power). Prioritize sun acquisition.

## Post-Conditions
- [ ] SA-A deployment confirmed via switch telemetry and current measurement
- [ ] SA-B deployment confirmed via switch telemetry and current measurement
- [ ] Total power generation measured and recorded
- [ ] Array symmetry verified within 15% tolerance
- [ ] Power budget assessment completed
- [ ] Solar Array Verification Report distributed
- [ ] GO decision for LEOP-004 (Sun Acquisition)

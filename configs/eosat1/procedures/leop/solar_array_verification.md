# LEOP-004: Solar Array Verification
**Subsystem:** EPS / Structure
**Phase:** LEOP
**Revision:** 2.1 --- 2026-08c flow redesign (entry/exit states, NEXT chain)
**Approved:** Flight Operations Director

**Entry state:** LEOP-002 exit state --- platform healthy on baseline telemetry,
wings STOWED (`eps.wing_status` 0x0144 = 0), AOCS/payload/heater/prop lines
OFF, spacecraft tumbling slowly at separation tip-off rates.

## Purpose
Deploy the +Y and -Y solar wings and verify that the body-mounted solar arrays (SA-A
and SA-B) are operational following launch vehicle separation. Confirm wing deployment
via telemetry, measure individual string currents, and validate total power generation
against predictions for the current sun angle and orbit phase. The spacecraft has
0.30 m2 of body-mounted panel area (stowed) increasing to 0.78 m2 after wing deployment
(0.24 m2 per wing on +Y and -Y faces).

## Prerequisites
- [ ] LEOP-001 (First Acquisition) completed successfully
- [ ] LEOP-002 (Initial Health Check) completed — all subsystems nominal
- [ ] Bidirectional S-band link active with link margin > 3 dB
- [ ] Spacecraft in sunlit portion of orbit (preferred) or approaching eclipse exit
- [ ] EPS telemetry confirmed available (SID 1 responsive)
- [ ] Flight Dynamics have provided current beta angle and sun vector
- [ ] Body rates within launcher tip-off spec per the separation report.
      NOTE: AOCS is NOT powered yet in this flow --- there is no rate telemetry
      to read. Deployment is qualified against tip-off rates; active detumble
      follows in LEOP-007/LEOP-005.
- [ ] Battery SoC > 50% (wing deployment draws ~2 A for 30 s per wing)

## Procedure Steps

### Step 0 — Command Wing Deployment
**TC:** `EPS_DEPLOY_WING(wing=2)` (Service 8, Subtype 1) — Deploy both wings
**Verify:** `eps.wing_status` (0x0144) bit 0x10 or 0x20 set (deploying) within 5s
**Action:** Wait 30 seconds for burn-wire release and spring deployment
**Verify:** `eps.wing_status` (0x0144) = 0x03 (both wings deployed) within 45s
**GO/NO-GO:** Both wings confirmed deployed. If only one deployed, attempt single-wing retry (see Off-Nominal).

### Step 1 — Request Detailed EPS Telemetry
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) in range [27.0V, 29.5V] within 10s
**Verify:** `eps.bat_soc` (0x0101) reported within 10s
**GO/NO-GO:** EPS telemetry responding nominally

### Step 2 — Check +Y Wing Deployment Status and Current
**TC:** `GET_PARAM(param_id=0x0144)` (Service 20, Subtype 3) — `eps.wing_status` deployment bitmask
**Verify:** `eps.wing_status` (0x0144) bit 0 set (0x01, +Y wing DEPLOYED) within 10s
**TC:** `GET_PARAM(param_id=0x0146)` (Service 20, Subtype 3) — `eps.sa_wing_py_current`
**Verify:** `eps.sa_wing_py_current` (0x0146) > 0.1A when in sunlight within 10s
**Note:** 0x0144 is the single deployment truth source (bit0=+Y deployed, bit1=-Y deployed,
bit4/bit5 = deployment in progress). 0x0110-0x0113 are PDM power-LINE status flags
(`eps.pl_obc` / `eps.pl_ttc_rx` / `eps.pl_ttc_tx` / `eps.pl_payload`) and say nothing about
wing deployment.
**GO/NO-GO:** +Y wing confirmed deployed and generating current

### Step 3 — Check -Y Wing Deployment Status and Current
**TC:** `GET_PARAM(param_id=0x0144)` (Service 20, Subtype 3) — `eps.wing_status` deployment bitmask
**Verify:** `eps.wing_status` (0x0144) bit 1 set (0x02, -Y wing DEPLOYED) within 10s
**TC:** `GET_PARAM(param_id=0x0147)` (Service 20, Subtype 3) — `eps.sa_wing_my_current`
**Verify:** `eps.sa_wing_my_current` (0x0147) > 0.1A when in sunlight within 10s
**GO/NO-GO:** -Y wing confirmed deployed and generating current

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

### Step 6 — Verify Wing Symmetry
**Action:** Compare the +Y and -Y wing currents. The two wings should produce similar current at equivalent sun angles, within 15% of each other.
**TC:** `GET_PARAM(param_id=0x0146)` (Service 20, Subtype 3) — `eps.sa_wing_py_current`
**TC:** `GET_PARAM(param_id=0x0147)` (Service 20, Subtype 3) — `eps.sa_wing_my_current`
**Verify:** |0x0146 - 0x0147| / max(0x0146, 0x0147) < 0.15 within 10s
**Note:** Body-mounted array currents are `eps.sa_a_current` (0x0103) and `eps.sa_b_current`
(0x0104); the per-wing currents above exclude the body faces.
**GO/NO-GO:** Wing currents balanced within 15% tolerance

### Step 7 — Estimate Power Budget Margin
**Action:** Calculate current power consumption from bus voltage and load current. Compare with power generation. Verify positive energy balance over one orbit (when sun-pointed).
**Verify:** `eps.power_gen` (0x0107) > 50W expected when sun-pointed (post LEOP-005)
**Action:** Record power budget estimate in LEOP log. Confirm sufficient margin for commissioning activities.
**GO/NO-GO:** Positive power budget confirmed or achievable after sun acquisition

## Off-Nominal Handling
- If a wing fails to deploy (wing_status bit not set after 45s): Retry single wing with `EPS_DEPLOY_WING(wing=0)` for +Y or `EPS_DEPLOY_WING(wing=1)` for -Y — requires Flight Director approval. If retry fails, assess single-wing power budget viability (single wing provides ~32W in sunlight, marginal for nominal ops).
- If both arrays deployed but power generation < 10W in sunlight: Verify spacecraft is actually in sunlight via Flight Dynamics. Check for excessive tumble rate reducing effective solar incidence. Prioritize LEOP-005 (Sun Acquisition) to stabilize attitude.
- If wing currents asymmetric > 30%: Log anomaly. One wing may have partial deployment or string failure. Continue LEOP with reduced power budget. Schedule detailed EPS investigation during commissioning.
- If `eps.bat_soc` dropping rapidly (> 2% per minute): Reduce spacecraft load — disable non-essential heaters via `HEATER_THRUSTER(on=0)`. Ensure AOCS detumble is using magnetorquers only (lower power). Prioritize sun acquisition.

## Exit state
- Both wings DEPLOYED (`eps.wing_status` 0x0144 = 0x03) and generating; full
  0.78 m2 array area available
- No other state changed: AOCS/payload/heater/prop lines still OFF

## Post-Conditions
- [ ] +Y wing deployment confirmed via `eps.wing_status` (0x0144) bit 0 and `eps.sa_wing_py_current` (0x0146)
- [ ] -Y wing deployment confirmed via `eps.wing_status` (0x0144) bit 1 and `eps.sa_wing_my_current` (0x0147)
- [ ] Total power generation measured and recorded (expected increase with wings)
- [ ] Wing symmetry verified within 15% tolerance
- [ ] Power budget assessment completed (0.78 m2 deployed area)
- [ ] Solar Array and Wing Deployment Verification Report distributed

**NEXT:** LEOP-007 (Sequential Power-On) --- the AOCS suite is energised there,
then LEOP-005 observes the sun acquisition.

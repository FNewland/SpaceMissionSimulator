# COM-002: TCS Verification
**Subsystem:** TCS
**Phase:** COMMISSIONING
**Revision:** 2.0 --- 2026-08c item on the heater flow: the old card
power-cycled heaters whose EPS LINES had never been turned on (the heater
switch obeyed, nothing warmed, and the "verify heating" steps read ambient
drift). Each heater test now ESTABLISHES ITS STATE FIRST --- power the line,
then cycle the heater, then restore the line to its entry state. The old
Steps 4/5 also commanded the WRONG functions (func 41 HEATER_OBC labelled
"battery", func 42 HEATER_THRUSTER labelled "OBC"); the funcs now match the
circuits: 40 = battery, 41 = OBC.
**Approved:** Flight Operations Director

**Entry state:** COM-001 exit state --- COARSE_SUN hold, phase 5. Heater lines
`htr_bat` (5) / `htr_obc` (6) are OFF unless LEOP-007 Step 2 energised one for
cause; read the LEOP log and `eps.pl_htr_bat` (0x0115) / `eps.pl_htr_obc`
(0x0116) before starting, and record both as the entry state to restore.

## Purpose
Verify the Thermal Control System by checking all monitored thermal zones against
predicted temperature ranges. Validate heater circuit operation for battery and
propulsion (if applicable). Establish baseline thermal profiles over a full orbit
(sunlit and eclipse) and verify FPA cooler interface before payload activation.

## Prerequisites
- [ ] COM-001 (EPS Checkout) completed — power budget verified
- [ ] Spacecraft in COARSE_SUN mode with stable sun-pointing
- [ ] Minimum one full orbit of thermal telemetry archived
- [ ] Bidirectional S-band link active
- [ ] `eps.bat_soc` (0x0101) > 60%

## Procedure Steps

### Step 1 — Request Thermal Housekeeping
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27)
**Verify:** `tcs.temp_obc` (0x0406) in range [-5C, +45C] within 10s
**Verify:** `tcs.temp_battery` (0x0407) in range [+5C, +35C] within 10s
**Verify:** `tcs.temp_fpa` (0x0408) reported (payload off, uncooled: expect +8C to +14C) within 10s
**GO/NO-GO:** All thermal zones within operational limits

### Step 2 — Monitor Thermal Profile Through Sunlit Phase
**Action:** Collect thermal telemetry samples every 2 minutes for 30 minutes during sunlit phase. Record temperature trends for all zones.
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27) — repeat every 120s
**Verify:** `tcs.temp_obc` (0x0406) stable or slowly increasing (< 1C/min) within 10s
**Verify:** `tcs.temp_battery` (0x0407) stable within 10s
**GO/NO-GO:** Thermal trends nominal during sunlit phase

### Step 3 — Monitor Thermal Profile Through Eclipse
**Action:** Continue thermal monitoring through eclipse entry and eclipse period.
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27) — at eclipse entry, mid-eclipse, eclipse exit
**Verify:** `tcs.temp_obc` (0x0406) > -5C throughout eclipse within 10s
**Verify:** `tcs.temp_battery` (0x0407) > +5C throughout eclipse within 10s
**Action:** Record temperature drop rates. OBC expected to cool ~0.5C/min; battery should be thermally buffered.
**GO/NO-GO:** All zones above minimum operational limits during eclipse

### Step 4 — Test Battery Heater Circuit (establish state FIRST)
**Action:** Record the entry state of `eps.pl_htr_bat` (0x0115). A heater
whose LINE is dark obeys the switch command and heats nothing — the exact
trap the old card fell into.
**TC:** `EPS_POWER_ON(line_index=5)` (Service 8, Subtype 1, func_id 19) — energise the htr_bat line
**Verify:** `eps.pl_htr_bat` (0x0115) = 1 within 5s
**TC:** `HEATER_BATTERY(on=1)` (Service 8, Subtype 1, func_id 40) — the battery circuit (func 40, not 41)
**Verify:** Battery heater ON acknowledged within 5s
**Action:** Monitor `tcs.temp_battery` (0x0407) for 3 minutes to confirm heating effect.
**Verify:** `tcs.temp_battery` (0x0407) rising or stable (heater counteracting cooling) within 180s
**TC:** `HEATER_BATTERY(on=0)` (Service 8, Subtype 1, func_id 40)
**Verify:** Battery heater OFF acknowledged within 5s
**TC:** `EPS_POWER_OFF(line_index=5)` (Service 8, Subtype 1, func_id 20) — restore, ONLY if the line was OFF at entry
**Verify:** `eps.pl_htr_bat` (0x0115) back to its recorded entry value within 5s
**GO/NO-GO:** Battery heater circuit operational, line state restored

### Step 5 — Test OBC Heater Circuit (establish state FIRST)
**Action:** Record the entry state of `eps.pl_htr_obc` (0x0116).
**TC:** `EPS_POWER_ON(line_index=6)` (Service 8, Subtype 1, func_id 19) — energise the htr_obc line
**Verify:** `eps.pl_htr_obc` (0x0116) = 1 within 5s
**TC:** `HEATER_OBC(on=1)` (Service 8, Subtype 1, func_id 41) — the OBC circuit (func 41, not 42 — 42 is the THRUSTER catbed heater and stays untouched outside manoeuvre prep)
**Verify:** OBC heater ON acknowledged within 5s
**Verify:** `tcs.temp_obc` (0x0406) stable or increasing within 120s
**TC:** `HEATER_OBC(on=0)` (Service 8, Subtype 1, func_id 41)
**Verify:** OBC heater OFF acknowledged within 5s
**TC:** `EPS_POWER_OFF(line_index=6)` (Service 8, Subtype 1, func_id 20) — restore, ONLY if the line was OFF at entry
**Verify:** `eps.pl_htr_obc` (0x0116) back to its recorded entry value within 5s
**GO/NO-GO:** OBC heater circuit operational, line state restored

### Step 6 — Verify Autonomous Heater Thermostat Settings
**TC:** `GET_PARAM(param_id=0x0410)` (Service 20, Subtype 3) — battery heater ON threshold
**TC:** `GET_PARAM(param_id=0x0411)` (Service 20, Subtype 3) — battery heater OFF threshold
**Verify:** Battery heater ON threshold = +5C within 10s
**Verify:** Battery heater OFF threshold = +10C within 10s
**Action:** Confirm autonomous thermostat will maintain battery within [+5C, +35C] range without ground intervention.
**GO/NO-GO:** Autonomous heater thresholds correctly configured

### Step 7 — FPA Cooler Interface Check (Pre-activation)
**Action:** Verify FPA cooler power interface and thermistor readback before payload commissioning.
**TC:** `GET_PARAM(param_id=0x040C)` (Service 20, Subtype 3) — `tcs.cooler_fpa`, the effective cooler state
**Verify:** `tcs.cooler_fpa` (0x040C) = 0 (cooler OFF) within 10s
**TC:** `GET_PARAM(param_id=0x0114)` (Service 20, Subtype 3) — `eps.pl_fpa_cooler`, the EPS supply to the cooler
**Verify:** `eps.pl_fpa_cooler` (0x0114) = 0 (line unpowered) within 10s
**Verify:** `tcs.temp_fpa` (0x0408) consistent with the uncooled equilibrium (+8C to +14C) within 10s
**Action:** Record baseline FPA temperature for comparison during cooler activation (COM-010).
**GO/NO-GO:** FPA thermal interface healthy, baseline temperature recorded

### Step 8 — Compile Thermal Baseline Report
**Action:** Generate orbit thermal profile showing min/max/average temperatures for each zone over one complete orbit. Compare with thermal model predictions. Document any deviations.
**GO/NO-GO:** Thermal performance within model predictions (+/- 5C tolerance)

## Off-Nominal Handling
- If `tcs.temp_battery` < +5C and heater does not respond: There is NO redundant battery heater circuit: `subsystems/tcs.yaml` declares three
  circuits only — 0 battery, 1 obc, 2 thruster. Consider an attitude adjustment to
  increase solar heating, and shed loads to protect the cell. Alert thermal engineer.
- If `tcs.temp_obc` > +45C: Verify sun-pointing attitude is not causing excessive solar heating on OBC panel. Consider temporary attitude offset. Check if heater stuck ON via `GET_PARAM(param_id=0x0412)`.
- If eclipse temperatures drop faster than predicted: Thermal model may need updating. Check for MLI damage indicators. Increase heater duty cycle if approaching survival limits.
- If FPA temperature reading invalid (outside the -25.5C to +17.5C S12 alarm band with the cooler off): cross-check `payload.fpa_temp` (0x0601), which reads the same physical chain 0.5 K colder. Agreement means the FPA really is out of band; disagreement points at one of the two sensors. If the TCS thermistor has failed, payload commissioning can proceed on 0x0601 with the loss of redundancy logged.
- If thermal zones show unexpected asymmetry: May indicate partial MLI blanket detachment or view factor change. Document and monitor trend over multiple orbits.

## Post-Conditions
- [ ] All thermal zones verified within operational limits
- [ ] Full orbit thermal profile recorded and archived
- [ ] Battery heater circuit verified operational
- [ ] OBC heater circuit verified operational
- [ ] Autonomous heater thresholds confirmed correct
- [ ] FPA cooler interface checked, baseline temperature recorded
- [ ] Thermal Baseline Report generated

## Exit state
- Heater lines restored to their recorded entry state (LEOP-007's
  as-needed configuration); heater switches OFF
- No mode, tier or latch changed by this card

**NEXT:** COM-003 (AOCS Sensor Calibration)

# COM-002: TCS Verification
**Subsystem:** TCS
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Verify the Thermal Control System by checking all monitored thermal zones against
predicted temperature ranges. Validate heater circuit operation for battery and
propulsion (if applicable). Establish baseline thermal profiles over a full orbit
(sunlit and eclipse) and verify FPA cooler interface before payload activation.

## Prerequisites
- [ ] COM-001 (EPS Checkout) completed — power budget verified
- [ ] Spacecraft in SAFE_POINT mode with stable sun-pointing
- [ ] Minimum one full orbit of thermal telemetry archived
- [ ] Bidirectional VHF/UHF link active
- [ ] `eps.bat_soc` (0x0101) > 60%

## Procedure Steps

### Step 1 — Request Thermal Housekeeping
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27)
**Verify:** `tcs.temp_obc` (0x0406) in range [-5C, +45C] within 10s
**Verify:** `tcs.temp_battery` (0x0407) in range [+5C, +35C] within 10s
**Verify:** `tcs.temp_fpa` (0x0408) reported (payload off, expect +5C to +25C) within 10s
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

### Step 4 — Test Battery Heater Circuit
**TC:** `HEATER_CONTROL(circuit=1, on=1)` (Service 8, Subtype 1)
**Verify:** Battery heater ON acknowledged within 5s
**Action:** Monitor `tcs.temp_battery` (0x0407) for 3 minutes to confirm heating effect.
**Verify:** `tcs.temp_battery` (0x0407) rising or stable (heater counteracting cooling) within 180s
**TC:** `HEATER_CONTROL(circuit=1, on=0)` (Service 8, Subtype 1)
**Verify:** Battery heater OFF acknowledged within 5s
**GO/NO-GO:** Battery heater circuit operational

### Step 5 — Test OBC Heater Circuit
**TC:** `HEATER_CONTROL(circuit=2, on=1)` (Service 8, Subtype 1)
**Verify:** OBC heater ON acknowledged within 5s
**Verify:** `tcs.temp_obc` (0x0406) stable or increasing within 120s
**TC:** `HEATER_CONTROL(circuit=2, on=0)` (Service 8, Subtype 1)
**Verify:** OBC heater OFF acknowledged within 5s
**GO/NO-GO:** OBC heater circuit operational

### Step 6 — Verify Autonomous Heater Thermostat Settings
**TC:** `GET_PARAM(0x0410)` (Service 20, Subtype 1) — battery heater ON threshold
**TC:** `GET_PARAM(0x0411)` (Service 20, Subtype 1) — battery heater OFF threshold
**Verify:** Battery heater ON threshold = +5C within 10s
**Verify:** Battery heater OFF threshold = +10C within 10s
**Action:** Confirm autonomous thermostat will maintain battery within [+5C, +35C] range without ground intervention.
**GO/NO-GO:** Autonomous heater thresholds correctly configured

### Step 7 — FPA Cooler Interface Check (Pre-activation)
**Action:** Verify FPA cooler power interface and thermistor readback before payload commissioning.
**TC:** `GET_PARAM(0x0420)` (Service 20, Subtype 1) — FPA cooler power status
**Verify:** FPA cooler power = OFF (value 0) within 10s
**Verify:** `tcs.temp_fpa` (0x0408) reading consistent with ambient (+5C to +25C) within 10s
**Action:** Record baseline FPA temperature for comparison during cooler activation (COM-102).
**GO/NO-GO:** FPA thermal interface healthy, baseline temperature recorded

### Step 8 — Compile Thermal Baseline Report
**Action:** Generate orbit thermal profile showing min/max/average temperatures for each zone over one complete orbit. Compare with thermal model predictions. Document any deviations.
**GO/NO-GO:** Thermal performance within model predictions (+/- 5C tolerance)

## Off-Nominal Handling
- If `tcs.temp_battery` < +5C and heater does not respond: Switch to redundant heater circuit via `HEATER_CONTROL(circuit=3, on=1)`. If no redundant circuit available, consider attitude adjustment to increase solar heating. Alert thermal engineer.
- If `tcs.temp_obc` > +45C: Verify sun-pointing attitude is not causing excessive solar heating on OBC panel. Consider temporary attitude offset. Check if heater stuck ON via `GET_PARAM(0x0412)`.
- If eclipse temperatures drop faster than predicted: Thermal model may need updating. Check for MLI damage indicators. Increase heater duty cycle if approaching survival limits.
- If FPA temperature reading invalid (out of range -60C to +60C): Check thermistor wiring via `GET_PARAM(0x0421)`. If thermistor failed, payload commissioning may require alternative temperature monitoring.
- If thermal zones show unexpected asymmetry: May indicate partial MLI blanket detachment or view factor change. Document and monitor trend over multiple orbits.

## Post-Conditions
- [ ] All thermal zones verified within operational limits
- [ ] Full orbit thermal profile recorded and archived
- [ ] Battery heater circuit verified operational
- [ ] OBC heater circuit verified operational
- [ ] Autonomous heater thresholds confirmed correct
- [ ] FPA cooler interface checked, baseline temperature recorded
- [ ] Thermal Baseline Report generated
- [ ] GO decision for COM-003 (OBDH Checkout)

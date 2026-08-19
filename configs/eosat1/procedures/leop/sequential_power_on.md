# LEOP-007: Sequential Power-On

**Subsystem:** All
**Phase:** LEOP
**Revision:** 3.0 --- 2026-08c flow redesign. LEOP powers ONLY what LEOP needs:
heaters as needed, the AOCS suite (magnetometer, magnetorquers, GPS) and
DETUMBLE. The payload line and the FPA cooler are commissioning loads and have
moved to COM-009 / COM-010; the reaction wheels stay DISABLED until COM-004;
the COARSE_SUN transition is AUTOMATIC and is observed in LEOP-005, not
commanded here; the phase-5 declaration has moved to LEOP-003, the last LEOP
card.
**Approved:** Flight Operations Director

**Entry state:** LEOP-004 exit state --- application image running, link on LOW,
wings deployed and generating, all switchable lines except `ttc_tx` OFF,
spacecraft tumbling at tip-off rates, AOCS in MODE_OFF.

## Purpose
Bring up the EPS power lines and subsystem modes that the REMAINDER OF LEOP
needs --- no more --- in dependency order, verifying each step before the next.
That is: heater lines if (and only if) their zones need heat; the AOCS suite
line; the magnetometer, magnetorquers and GPS receiver devices; and the
commanded transition to DETUMBLE. Everything else stays dark and belongs to
the commissioning card that uses it.

## Prerequisites
- [ ] LEOP-001, LEOP-002, LEOP-004 completed (`obdh.sw_image` 0x0311 = 1,
      wings deployed)
- [ ] Scheduled ground station pass in progress (or next pass booked ---
      Steps 1-6 fit a single 8-minute contact)
- [ ] Battery SoC > 60% and bus voltage 26-30V (Step 1 verifies)

## What this card does NOT power (and who does)
| Load | Stays | Powered by |
|---|---|---|
| `payload` (line 3) | OFF | COM-009 Payload Power-On |
| `fpa_cooler` (line 4) | OFF | COM-010 FPA Cooler Activation (any cooler path energises it) |
| Reaction wheels (enable state) | DISABLED | COM-004 Actuator Checkout (`AOCS_ENABLE_WHEEL` x4) |
| Star trackers / gyro units | OFF | COM-003 Sensor Calibration |
| `prop` (line 8) | OFF | NOM-004/NOM-013 manoeuvre preparation only |

Note the `aocs_wheels` EPS line (7) is the supply for the WHOLE AOCS suite ---
sensors, magnetorquers, GPS and wheel drive electronics alike --- so it must be
energised here; powering it does NOT enable the wheels (`aocs.wheels_enabled`
0x0260 stays 0 until COM-004, by design: a power-line switch can never silently
hand attitude authority back to the control loop).

## Procedure Steps

### Step 1 --- Verify EPS Baseline
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) in range [26.0V, 30.0V] within 10s
**Verify:** `eps.bat_soc` (0x0101) > 60% within 10s
**Verify:** `eps.power_gen` (0x0107) > 20W (wings deployed, partial illumination)
**GO/NO-GO:** EPS baseline nominal

### Step 2 --- Energise Heater Lines AS NEEDED (conditional)
**Action:** Read `tcs.temp_battery` (0x0407) and `tcs.temp_obc` (0x0406) from
the running SID 3 stream. Power a heater line ONLY if its zone needs heat;
a heater line energised "because the card says so" is the state drift the
owner flew into --- COM-002 later found a heater being power-cycled that had
never legitimately been on.
**TC:** `EPS_POWER_ON(line_index=5)` (Service 8, Subtype 1, func_id 19) --- htr_bat, ONLY if `tcs.temp_battery` < +5C
**Verify:** `eps.pl_htr_bat` (0x0115) = 1 within 5s --- only if commanded
**TC:** `EPS_POWER_ON(line_index=6)` (Service 8, Subtype 1, func_id 19) --- htr_obc, ONLY if `tcs.temp_obc` < 0C
**Verify:** `eps.pl_htr_obc` (0x0116) = 1 within 5s --- only if commanded
**Action:** Record in the LEOP log WHICH heater lines this card energised (the
COM-002 heater checkout reads this record as its entry state).
**GO/NO-GO:** Thermal zones protected; uncommanded lines confirmed still off

### Step 3 --- Energise the AOCS Suite Line and Enable Its Housekeeping
**TC:** `EPS_POWER_ON(line_index=7)` (Service 8, Subtype 1, func_id 19) --- aocs_wheels (the AOCS suite supply)
**Verify:** `eps.pl_aocs_wheels` (0x0117) = 1 within 5s
**Note:** AOCS is still in MODE_OFF --- the line being hot only allows the model
to tick. Powering the line does NOT enable SID 2: the power-up enable set is
`simulation.yaml` `default_enabled_sids: [1, 11]`, so SID 2 must be commanded
on explicitly.
**TC:** `HK_ENABLE(sid=2)` (Service 3, Subtype 5) --- enable periodic AOCS housekeeping
**Verify:** Periodic SID 2 begins emitting within one 4 s structure interval
**Verify:** `aocs.wheels_enabled` (0x0260) = 0 --- wheels DISABLED, as designed
**GO/NO-GO:** AOCS suite powered, SID 2 downlinking, wheels still disabled

### Step 4 --- Enable the DETUMBLE Sensors and Actuators BEFORE Commanding It
**Action:** DETUMBLE is the B-dot law: magnetometer in, magnetorquers out.
Since 2026-08c the mode command REFUSES honestly if either leg is missing
(0x0A20 no magnetometer, 0x0A21 no magnetorquers, 0x0004 line off) --- so this
step establishes both legs FIRST.
**TC:** `S2_AOCS_MAGNETOMETER(device_id=521, state=1)` (Service 2, Subtype 1) --- the single 3-axis magnetometer (device 0x0209)
**TC:** `MTQ_ENABLE(enable=1)` (Service 8, Subtype 1, func_id 9) --- magnetorquer set
**TC:** `S2_AOCS_GPS_RECEIVER(device_id=527, state=1)` (Service 2, Subtype 1) --- GPS receiver (device 0x020F); LEOP-006 (time) and LEOP-003 (orbit) need its TTFF running from now
**Verify:** `aocs.mtq_enabled` (0x025E) = 1 within 10s
**GO/NO-GO:** Magnetometer and magnetorquers enabled, GPS receiver acquiring

### Step 5 --- Command DETUMBLE and Verify on the Magnetometer Telemetry
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 1, func_id 0) --- DETUMBLE
**Note:** This is a COMMANDED transition (see
`docs/plan_2026-08c/AOCS_MODE_TRANSITIONS.md` 1.1). Func_id 0 is exempt from
the subsystem mode gate, so it can boot AOCS out of OFF.
**Verify:** S1.1 acceptance + S1.7 execution complete received within 5s
**Verify:** `aocs.mode` (0x020F) = 2 (DETUMBLE) within 10s
**Verify:** `aocs.mag_enabled` (0x025C) = 1 within 10s --- magnetometer valid
**Verify:** `aocs.mag_x` (0x020B), `aocs.mag_y` (0x020C), `aocs.mag_z` (0x020D)
reporting a measured body-frame field within 10s
**Verify:** `aocs.mag_field_total` (0x0277) in range [5000, 70000] nT ---
plausible LEO field magnitude, consistent with sqrt(Bx^2+By^2+Bz^2)
**Verify:** `aocs.rate_roll`, `aocs.rate_pitch`, `aocs.rate_yaw`
(0x0204..0x0206) reported; trending down within 60s
**GO/NO-GO:** AOCS in DETUMBLE with a live field measurement, body rates
trending down

### Step 6 --- Confirm the Exit Power State
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.pl_payload` (0x0113) = 0 --- payload line still OFF (COM-009's)
**Verify:** `eps.pl_fpa_cooler` (0x0114) = 0 --- cooler line still OFF (COM-010's)
**Verify:** `eps.pl_prop` (0x014A) = 0 --- prop line still OFF
**Verify:** `aocs.wheels_enabled` (0x0260) = 0 --- wheels still DISABLED (COM-004's)
**Verify:** Power margin positive: `eps.power_gen` (0x0107) exceeds the load
implied by `eps.power_cons` (0x0106), or SoC stable/increasing
**GO/NO-GO:** Only the LEOP loads are up; margin positive

## Off-Nominal Handling
- **SoC drops below 40%:** STOP, shed the last powered line (`EPS_POWER_OFF`), wait for charge recovery > 60%, resume.
- **`AOCS_SET_MODE(mode=2)` refused with 0x0004:** the `aocs_wheels` line write did not take. Re-issue `EPS_POWER_ON(line_index=7)`, verify 0x0117, retry.
- **Refused with 0x0A20 (no magnetometer):** the S2 device command in Step 4 did not take or the unit has failed. Re-issue `S2_AOCS_MAGNETOMETER(device_id=521, state=1)`; if it still refuses, this is a hardware anomaly --- there is no redundant magnetometer.
- **Refused with 0x0A21 (no magnetorquers):** re-issue `MTQ_ENABLE(enable=1)` and check per-rod device states (0x020B-0x020D devices).
- **Body rates do not decrease in DETUMBLE:** verify 0x025C = 1 and 0x0277 plausible (a zeroed field means the magnetometer is off/failed). If rates increase, command `AOCS_SET_MODE(mode=0)` (OFF) and investigate.
- **Bus voltage < 26V:** immediate load shed per CTG-001; power off the most recently enabled line first.

## Exit state
- Heater lines: as recorded in Step 2 (each ON only if its zone needed heat)
- `aocs_wheels` line ON; SID 2 enabled; magnetometer + MTQs enabled; GPS
  receiver ON and acquiring; AOCS in DETUMBLE, rates decaying
- Wheels DISABLED (0x0260 = 0); payload / fpa_cooler / prop lines OFF
- No rate-tier, downlink-mode or OBC-mode change made by this card

## Post-Conditions
- [ ] LEOP power state established exactly per the table above
- [ ] DETUMBLE active with live magnetometer telemetry
- [ ] Power margin positive
- [ ] LEOP log records which heater lines were energised

**NEXT:** LEOP-005 (Sun Acquisition --- OBSERVES the automatic
DETUMBLE -> COARSE_SUN handover; nothing to command)

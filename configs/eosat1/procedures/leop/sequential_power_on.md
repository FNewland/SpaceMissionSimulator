# LEOP-007: Sequential Power-On

**Subsystem:** All
**Phase:** LEOP
**Revision:** 2.0 — rewritten for cold-boot initial state
**Approved:** Flight Operations Director

## Purpose
Bring every switchable EPS power line and every subsystem mode up from
the LEOP cold-boot state, in dependency order, verifying each step before
proceeding to the next. Assumes LEOP-001 has already established the
command path and exited the OBDH bootloader.

## Prerequisites
- [ ] LEOP-001 completed (auto-TX hold-down active, `obdh.sw_image` = 1, time synchronised)
- [ ] Pass override engaged (or natural orbital contact maintained)
- [ ] Battery SoC > 60% and bus voltage 27–29V (Step 1 verifies)

## Initial Cold-Boot State (verify before starting)
| Line | State | Owning subsystem |
|---|---|---|
| `obc` | ON (non-switchable) | OBDH |
| `ttc_rx` | ON (non-switchable) | TT&C receiver |
| `ttc_tx` | ON (auto-TX hold-down) | TT&C transmitter |
| `htr_bat` | OFF | EPS battery heater |
| `htr_obc` | OFF | OBC heater |
| `aocs_wheels` | OFF | AOCS reaction wheels |
| `payload` | OFF | Payload imager |
| `fpa_cooler` | OFF | FPA cryocooler |

| Subsystem | Mode | Notes |
|---|---|---|
| AOCS | OFF | not ticking until line on AND `set_mode` issued |
| Payload | OFF | as above |

## Procedure Steps

### Step 1 — Verify EPS Baseline
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) in range [27.0V, 29.0V] within 10s
**Verify:** `eps.bat_soc` (0x0101) > 60% within 10s
**Verify:** `eps.power_gen` (0x0107) > 0W (at least partial illumination)
**GO/NO-GO:** EPS baseline nominal

### Step 2 — Energise Battery Heater
**TC:** `POWER_LINE_ON(line=5)` (Service 8, Subtype 1, func_id 19) — htr_bat
**Verify:** `eps.power_lines["htr_bat"]` = 1 within 5s
**Verify:** Battery temperature trending toward set point (5°C) within 120s
**Verify:** Power consumption increased by ~6W within 30s
**GO/NO-GO:** Battery heater active, power margin positive

### Step 3 — Energise OBC Heater
**TC:** `POWER_LINE_ON(line=6)` (Service 8, Subtype 1, func_id 19) — htr_obc
**Verify:** `eps.power_lines["htr_obc"]` = 1 within 5s
**Verify:** `tcs.temp_obc` (0x0406) trending toward set point within 120s
**Verify:** Power consumption increased by ~3W within 30s
**GO/NO-GO:** OBC heater active

### Step 4 — Energise AOCS Wheels Line and Boot AOCS
**TC:** `POWER_LINE_ON(line=7)` (Service 8, Subtype 1, func_id 19) — aocs_wheels
**Verify:** `eps.power_lines["aocs_wheels"]` = 1 within 5s
**Note:** AOCS is still in MODE_OFF — the line being hot only allows the model to tick. Periodic SID 2 will start emitting on the next HK cycle.
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 1, func_id 0) — DETUMBLE
**Note:** Func_id 0 (AOCS set_mode) is exempt from the subsystem mode gate, so it can boot AOCS out of OFF.
**Verify:** S1.1 acceptance + S1.7 execution complete received within 5s
**Verify:** `aocs.mode` (0x020F) = 2 (DETUMBLE) within 10s
**Verify:** `aocs.rate_roll`, `aocs.rate_pitch`, `aocs.rate_yaw` (0x0204..0x0206) decreasing within 60s
**Verify:** Power consumption increased by ~12W within 30s
**GO/NO-GO:** AOCS in DETUMBLE, body rates trending down (< 0.5 °/s within 5–15 min)

### Step 5 — Commission AOCS Sensors and Transition to Sun-Pointing
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.mag_a_x` (0x0200) responding with valid field data within 10s
**Verify:** Magnetometer field magnitude in range [25, 65] µT
**Verify:** Coarse sun sensor heads reporting illumination consistent with attitude
**Action:** Wait until body rates < 0.5 °/s on all axes, then transition to sun-point.
**TC:** `AOCS_SET_MODE(mode=3)` (Service 8, Subtype 1, func_id 0) — SAFE / Sun-pointing
**Verify:** `aocs.mode` = 3 within 10s
**Verify:** `aocs.att_error` (0x0217) decreasing within 120s
**GO/NO-GO:** Attitude error < 5° and stable

### Step 6 — Energise Payload Line and Bring Payload to Standby
**TC:** `POWER_LINE_ON(line=3)` (Service 8, Subtype 1, func_id 19) — payload
**Verify:** `eps.power_lines["payload"]` = 1 within 5s
**TC:** `SET_PAYLOAD_MODE(mode=1)` (Service 8, Subtype 1, func_id 26) — STANDBY
**Note:** Func_id 26 (payload set_mode) is exempt from the subsystem mode gate.
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10s
**Verify:** Periodic HK SID 5 begins emitting
**Verify:** `payload.fpa_temp` (0x0601) reading valid (~ambient) within 30s
**Verify:** Power consumption increased by ~8W within 30s
**GO/NO-GO:** Payload in STANDBY, FPA telemetry valid

### Step 7 — Energise FPA Cooler Line and Activate Cooler
**TC:** `POWER_LINE_ON(line=4)` (Service 8, Subtype 1, func_id 19) — fpa_cooler
**Verify:** `eps.power_lines["fpa_cooler"]` = 1 within 5s
**TC:** `SET_FPA_COOLER(on=1)` (Service 8, Subtype 1, func_id 36)
**Verify:** S1.1 acceptance + S1.7 execution complete (the payload mode gate is now satisfied because Step 6 brought payload to STANDBY)
**Verify:** `payload.fpa_temp` (0x0601) decreasing toward -30°C within 300s
**Verify:** Power consumption increased by ~15W within 30s
**GO/NO-GO:** FPA temp < -25°C

### Step 8 — Final GO/NO-GO and Phase Transition
**Action:** FD initiates GO/NO-GO poll across all positions. Each position reports subsystem status (nominal/degraded/failed).
**Action:** When GO is unanimous, the instructor advances the spacecraft phase to COMMISSIONING (5) via the simulator console. (No flight TC for `SET_PHASE` is currently exposed; this is an instructor-side advance.)
**Verify:** `spacecraft.phase` (0x0129) = 5 within 5s
**GO/NO-GO:** All positions GO; phase = COMMISSIONING

## Off-Nominal Handling
- **SoC drops below 40%:** STOP, shed last powered subsystem (POWER_LINE_OFF), wait for charge recovery > 60%, resume.
- **Subsystem fails to respond after power-on:** skip that subsystem, continue with the rest, log failure for later troubleshooting.
- **Bus voltage < 26V:** immediate load shed per CTG-001. Power off the most recently enabled line first, then walk down the priority list.
- **AOCS_SET_MODE rejected with 0x0004 after Step 4 line-on:** the line gate cleared, so this should only happen if the EPS write didn't take. Re-issue POWER_LINE_ON, verify `eps.power_lines["aocs_wheels"]`, then retry set_mode.
- **Body rates do not decrease in DETUMBLE:** verify magnetorquer polarity. If rates increase, immediately command `AOCS_SET_MODE(mode=0)` (OFF) and investigate.

## Post-Conditions
- [ ] All switchable lines energised
- [ ] AOCS in SAFE/Sun-pointing with attitude error < 5°
- [ ] Payload in STANDBY, FPA cooling, periodic HK SID 5 emitting
- [ ] All six platform HK SIDs (1, 2, 3, 4, 5, 6) emitting periodically
- [ ] Power margin positive
- [ ] Spacecraft phase = COMMISSIONING (5)
- [ ] GO/NO-GO poll complete with all positions reporting GO

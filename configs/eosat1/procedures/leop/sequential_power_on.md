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
| `prop` | OFF | Propulsion (catbed heater + valve drivers) — stays OFF; energised only for manoeuvre preparation (NOM-004 planning / NOM-013 execution) |

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
**TC:** `EPS_POWER_ON(line_index=5)` (Service 8, Subtype 1, func_id 19) — htr_bat
**Verify:** `eps.pl_htr_bat == 1` (0x0115, timeout 5 s)
**Verify:** Battery temperature trending toward set point (5°C) within 120s
**Verify:** Power consumption increased by ~6W within 30s
**GO/NO-GO:** Battery heater active, power margin positive

### Step 3 — Energise OBC Heater
**TC:** `EPS_POWER_ON(line_index=6)` (Service 8, Subtype 1, func_id 19) — htr_obc
**Verify:** `eps.pl_htr_obc == 1` (0x0116, timeout 5 s)
**Verify:** `tcs.temp_obc` (0x0406) trending toward set point within 120s
**Verify:** Power consumption increased by ~3W within 30s
**GO/NO-GO:** OBC heater active

### Step 4 — Energise AOCS Wheels Line and Boot AOCS
**TC:** `EPS_POWER_ON(line_index=7)` (Service 8, Subtype 1, func_id 19) — aocs_wheels
**Verify:** `eps.pl_aocs_wheels == 1` (0x0117, timeout 5 s)
**Note:** AOCS is still in MODE_OFF — the line being hot only allows the model to tick. Powering the line does NOT enable SID 2: the power-up enable set is `simulation.yaml` `default_enabled_sids: [1, 11]`, re-applied by `engine.py::_apply_default_hk_enable` on application boot, so SID 2 must be commanded on explicitly (below).
**TC:** `HK_ENABLE(sid=2)` (Service 3, Subtype 5) — enable periodic AOCS housekeeping
**Verify:** Periodic SID 2 begins emitting within one 4 s structure interval
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 1, func_id 0) — DETUMBLE
**Note:** Func_id 0 (AOCS set_mode) is exempt from the subsystem mode gate, so it can boot AOCS out of OFF.
**Verify:** S1.1 acceptance + S1.7 execution complete received within 5s
**Verify:** `aocs.mode` (0x020F) = 2 (DETUMBLE) within 10s
**Verify:** `aocs.rate_roll`, `aocs.rate_pitch`, `aocs.rate_yaw` (0x0204..0x0206) decreasing within 60s
**Verify:** Power consumption increased by ~12W within 30s
**GO/NO-GO:** AOCS in DETUMBLE, body rates trending down (< 0.5 °/s within 5–15 min)

### Step 5 — Commission AOCS Sensors and Transition to Sun-Pointing
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.mag_a_x` (0x0223) responding with valid field data within 10s
**Verify:** Magnetometer field magnitude in range [25, 65] µT
**Verify:** Coarse sun sensor heads reporting illumination consistent with attitude
**Action:** Wait until body rates < 0.5 °/s on all axes, then transition to sun-point.
**TC:** `AOCS_SET_MODE(mode=3)` (Service 8, Subtype 1, func_id 0) — COARSE_SUN (sun-pointing)
**Verify:** `aocs.mode` (0x020F) = 3 (COARSE_SUN) within 10s
**Verify:** `aocs.att_error` (0x0217) decreasing within 120s
**GO/NO-GO:** Attitude error < 5° and stable

### Step 6 — Energise Payload Line and Bring Payload to Standby
**TC:** `EPS_POWER_ON(line_index=3)` (Service 8, Subtype 1, func_id 19) — payload
**Verify:** `eps.pl_payload == 1` (0x0113, timeout 5 s)
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1, func_id 26) — STANDBY
**Note:** Was written `SET_PAYLOAD_MODE`, which `tc_catalog.yaml` maps to
`EPS_PAYLOAD_MODE` (func **16**) — a different command from the func 26 the
annotation named. Mnemonic and func_id now agree.
**Note:** Func_id 26 (payload set_mode) is exempt from the subsystem mode gate.
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10s
**Note:** Powering the payload line does NOT enable SID 5. Only SIDs 1 (EPS) and 11 (Beacon) are enabled at power-up (`simulation.yaml` `default_enabled_sids: [1, 11]`, re-applied on application boot); every other SID needs an explicit `HK_ENABLE`.
**TC:** `HK_ENABLE(sid=5)` (Service 3, Subtype 5) — enable periodic payload housekeeping
**Verify:** Periodic HK SID 5 begins emitting within one 8 s structure interval
**Verify:** `payload.fpa_temp` (0x0601) reading valid (~ambient) within 30s
**Verify:** Power consumption increased by ~8W within 30s
**GO/NO-GO:** Payload in STANDBY, FPA telemetry valid

### Step 7 — Energise FPA Cooler Line and Activate Cooler
**TC:** `EPS_POWER_ON(line_index=4)` (Service 8, Subtype 1, func_id 19) — fpa_cooler
**Verify:** `eps.pl_fpa_cooler == 1` (0x0114, timeout 5 s)
**TC:** `FPA_COOLER(on=1)` (Service 8, Subtype 1, func_id 43)
**Note:** Was `SET_FPA_COOLER` annotated `func_id 36`. `SET_FPA_COOLER` is an
alias of `EPS_FPA_COOLER` (func **17**), and func **36** is
`PAYLOAD_COOLER_SETPOINT`, whose argument is a uint16 setpoint in
CENTI-KELVIN — so the `01` byte this step sent decoded as a setpoint of
0.01 K, not "cooler on". Since Phase 4 all four cooler paths (funcs 16, 17, 26,
43 and S2 device 0x0601) energise EPS line 4, so func 43 both starts the cooler
and guarantees the line.
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) — payload housekeeping
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — EPS housekeeping
**Verify:** S1.1 acceptance + S1.7 execution complete (the payload mode gate is
now satisfied because Step 6 brought the payload to STANDBY)
**Verify:** `eps.pl_fpa_cooler == 1` (0x0114, timeout 10 s) — the cooler line is hot
**Verify:** `payload.fpa_temp` (0x0601) decreasing toward the **-15 C** default
setpoint within 300 s
**Verify:** Power consumption increased by ~15 W within 30 s
**Note:** The commandable setpoint envelope is **-20 C to 0 C**
(`subsystems/tcs.yaml` `setpoint_min_c` / `setpoint_max_c`, default -15 C). The
old GO criterion of "FPA temp < -25 C" and the "toward -30 C" trend were both
BELOW the floor: no commanded configuration could ever satisfy them, so this
gate could never pass. At the -15 C setpoint the steady state is -15.0 C with
sigma 0.138 K, and readiness follows the ACTIVE setpoint rather than a fixed
number.
**GO/NO-GO:** `payload.fpa_ready` (0x0610) = 1, i.e. the FPA is regulating at
its commanded setpoint. If it is not, read `payload.cooler_margin_k` (0x064B):
<= 0 means the cooler physically cannot reach the commanded setpoint at the
present sink temperature and load, and the answer is a warmer setpoint, not
more waiting.

### Step 8 — Final GO/NO-GO and Phase Transition
**Action:** FD initiates GO/NO-GO poll across all positions. Each position reports subsystem status (nominal/degraded/failed).
**Note:** When GO is unanimous, the instructor advances the spacecraft phase to
COMMISSIONING (5) via the simulator console. There is NO flight telecommand for
this — it is an instructor-side advance, so this step deliberately carries no
`**TC:**` annotation.
**Verify:** `eps.spacecraft_phase` (0x0129) = 5 within 5s
**GO/NO-GO:** All positions GO; phase = COMMISSIONING

## Off-Nominal Handling
- **SoC drops below 40%:** STOP, shed last powered subsystem (POWER_LINE_OFF), wait for charge recovery > 60%, resume.
- **Subsystem fails to respond after power-on:** skip that subsystem, continue with the rest, log failure for later troubleshooting.
- **Bus voltage < 26V:** immediate load shed per CTG-001. Power off the most recently enabled line first, then walk down the priority list.
- **AOCS_SET_MODE rejected with 0x0004 after Step 4 line-on:** the line gate cleared, so this should only happen if the EPS write didn't take. Re-issue POWER_LINE_ON, verify `eps.pl_aocs_wheels` (0x0117), then retry set_mode.
- **Body rates do not decrease in DETUMBLE:** verify magnetorquer polarity. If rates increase, immediately command `AOCS_SET_MODE(mode=0)` (OFF) and investigate.

## Post-Conditions
- [ ] All switchable lines energised
- [ ] AOCS in COARSE_SUN (`aocs.mode` 0x020F = 3) with attitude error < 5°
- [ ] Payload in STANDBY, FPA cooling, periodic HK SID 5 emitting
- [ ] All six platform HK SIDs (1, 2, 3, 4, 5, 6) emitting periodically — SID 1 by default, SIDs 3/4/6 enabled in LEOP-001 and SIDs 2/5 by the `HK_ENABLE` commands in Steps 4 and 6 (only 1 and 11 are enabled at power-up)
- [ ] Power margin positive
- [ ] Spacecraft phase = COMMISSIONING (5)
- [ ] GO/NO-GO poll complete with all positions reporting GO

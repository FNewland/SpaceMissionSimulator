# EOSAT-1 Full Commissioning Sequence: Spacecraft OFF → Payload Operations

**Reference:** Cold-boot to fully commissioned, step by step.
**Revision:** 2.0
**Date:** 2026-04-09

This document walks through every command and telemetry verification from first power-on to payload-ready, mapped to the MCS screen where you confirm each result.

---

## MCS Tab Reference

| Tab | What it shows |
|-----|---------------|
| **overview** | Spacecraft bus summary, all subsystem health, contact windows, ground track |
| **eps** | Power lines, battery, bus voltage, solar arrays, SoC trending, load-shed stage |
| **tcs** | Temperature zones, heater states, FPA cooler, thermal trending |
| **aocs** | Mode state machine, body rates, attitude error, star trackers, CSS, MTQ duty, reaction wheels, orbital data |
| **obdh** | OBC mode, CPU load, buffer fills, sw_image, uptime, TC/TM counters |
| **ttc** | Link status, RSSI, link margin, PA temp, carrier/bit/frame lock, antenna deployment |
| **payload** | Imager mode, FPA temp, cooler power, storage, image catalog |
| **commanding** | TC entry, command history, acceptance/rejection log |
| **pus** | PUS service interface (S3 HK requests, S8 func perform, S9 time, S20 param get/set) |
| **ondemand** | On-demand S20.3 parameter reads |

---

## Phase 0 — Pre-Contact Configuration (Ground Only)

No spacecraft commands. Configure the ground station.

### 0.1 Configure Ground Station & Override Pass

**Action:** Set pass override parameter to force `in_contact = True`

**TC:** `SET_PARAM(param_id=0x05FF, value=1)` (Service 20, Subtype 1)

**MCS screen:** **commanding** tab → send the S20 SET_PARAM → check acceptance in command history. The **overview** tab contact window panel should show "IN CONTACT" once the override takes effect.

---

## Phase 1 — First Signal: Bootloader Telemetry Reception

The spacecraft is in bootloader (`sw_image = 0`). Only OBC + TTC RX are powered. The beacon SID 11 is the only telemetry available (7 parameters, every 30 s). No application-mode SIDs (1–6) are active yet.

### 1.1 Ping the Spacecraft (Connection Test)

**TC:** `CONNECTION_TEST` (Service 17, Subtype 1)

**Purpose:** Verifies the uplink is received. The auto-TX hold-down fires: the engine energises `ttc_tx` for 15 minutes on acceptance of any valid TC.

**MCS screen:**
- **commanding** tab → S1.1 acceptance TM received (green row in command history)
- **ttc** tab → `link_status` changes from 0 (NO LINK) to 1 (ACQUIRING) then 2 (LOCKED); `rssi` climbs from -120 dBm toward > -110 dBm
- **eps** tab → power line `ttc_tx` shows ON (auto-TX hold-down activated)

### 1.2 Verify Beacon Telemetry (SID 11)

**TC:** `HK_REQUEST(sid=11)` (Service 3, Subtype 27)

**Purpose:** Request the bootloader beacon packet. This is the only HK available before OBC_BOOT_APP.

**MCS screen — overview tab** (beacon parameters):

| Parameter | Hex ID | Expected | Where on MCS |
|-----------|--------|----------|--------------|
| `bat_voltage` | 0x0100 | ~26.4 V | **eps** tab → Battery panel |
| `bat_soc` | 0x0101 | ~75% | **eps** tab → Battery panel |
| `obc_mode` | 0x0300 | 0 (NOMINAL) | **obdh** tab → Mode field |
| `reboot_count` | 0x030A | 0 | **obdh** tab → Reboot Count |
| `sw_image` | 0x0311 | 0 (BOOTLOADER) | **obdh** tab → SW Image field |
| `active_obc` | 0x030C | 0 (OBC-A) | **obdh** tab → Active OBC |
| `spacecraft_phase` | 0x0129 | 0 (BOOTLOADER) | **overview** tab → Phase |

**GO/NO-GO:** Beacon received, `sw_image = 0` confirmed, battery > 50%, link locked.

**⚠ If this step fails:** See [Commissioning Contingency Procedures](10_commissioning_contingency_procedures.md#phase-1--first-signal) — covers no telemetry, link failure, unexpected OBC state.

---

## Phase 2 — Antenna Deployment

The antenna starts stowed (`antenna_deployment_sensor = 1`). Even though you may already have signal via the stowed antenna, you need to deploy for full link margin.

### 2.1 Deploy Antenna

**TC:** `TTC_DEPLOY_ANTENNA` (Service 8, Subtype 1, func_id 69)

**Purpose:** Fires the burn-wire to release the antenna. One-shot, no parameters.

**Note:** This command is in the TTC func_id range (63–78). It is allowed through the bootloader gate because func_id 69 is routed through the engine as a standard S8.1 function. However, check that the engine bootloader gate allows it — the current gate permits `{19, 20, 52, 53, 54, 55, 56, 57, 61}`. **func_id 69 is NOT in that set**, so this command will currently be rejected in bootloader phase. You have two options:
1. Deploy antenna after OBC_BOOT_APP (Phase 3) when the full command set is available, OR
2. Add func_id 69 to the engine bootloader-allowed set.

**MCS screen:**
- **commanding** tab → S1.1 acceptance + S1.7 execution complete
- **ttc** tab → `antenna_deployed` (0x0520) = 1, `antenna_deployment_sensor` (0x0536) = 2 (DEPLOYED)
- **ttc** tab → `link_margin` should improve (stowed → deployed gain increase)

**Verify:** `rssi` improves, `link_margin` (0x0503) > 3.0 dB.

**GO/NO-GO:** Antenna deployed. If sensor reads 3 (PARTIAL/JAMMED), escalate — see CON-015 contingency.

**⚠ If this step fails:** See [Commissioning Contingency Procedures](10_commissioning_contingency_procedures.md#phase-2--antenna-deployment) — covers deploy failure, partial deployment, link quality issues.

---

## Phase 3 — Boot OBC to Application Image

### 3.1 OBC_BOOT_APP

**TC:** `OBC_BOOT_APP` (Service 8, Subtype 1, func_id 55)

**Purpose:** Triggers 10 s CRC verification of the application image, then transitions from bootloader to application. After this, the full PUS command set becomes available and application-mode SIDs (1–6) activate.

**MCS screen:**
- **commanding** tab → S1.1 acceptance within 5 s, then S1.7 execution complete within 15 s
- **obdh** tab → `sw_image` (0x0311) transitions from 0 → 1 (APPLICATION)
- **obdh** tab → `spacecraft_phase` (0x0129) should advance from 0

**GO/NO-GO:** `sw_image = 1`. Full command set available. If `sw_image` stays 0, the application image may be corrupt — see Recovery Action 2 in EMG-003.

**⚠ If this step fails:** See [Commissioning Contingency Procedures](10_commissioning_contingency_procedures.md#phase-3--boot-obc-to-application-image) — covers boot failure, corrupt image, reboot loops.

---

## Phase 4 — Initial Platform Health Check

Now in application mode. All SIDs available. Check everything before powering on subsystems.

### 4.1 Request All Housekeeping

**TC sequence** (six commands, can be sent rapidly):

| TC | SID | Subsystem | MCS Tab |
|----|-----|-----------|---------|
| `HK_REQUEST(sid=1)` (S3.27) | 1 | EPS | **eps** |
| `HK_REQUEST(sid=2)` (S3.27) | 2 | AOCS | **aocs** |
| `HK_REQUEST(sid=3)` (S3.27) | 3 | TCS | **tcs** |
| `HK_REQUEST(sid=4)` (S3.27) | 4 | OBDH/Platform | **obdh** |
| `HK_REQUEST(sid=5)` (S3.27) | 5 | Payload | **payload** |
| `HK_REQUEST(sid=6)` (S3.27) | 6 | TTC | **ttc** |

### 4.2 Verify EPS Power Status

**MCS screen: eps tab**

| Parameter | Hex ID | Expected Cold-Boot Value | Check |
|-----------|--------|--------------------------|-------|
| `bus_voltage` | 0x0105 | 28.2 V (regulated) | ∈ [27.0, 29.5] V |
| `bat_voltage` | 0x0100 | ~26.4 V | ∈ [24.0, 28.0] V |
| `bat_soc` | 0x0101 | ~75% | > 50% |
| `bat_temp` | 0x0102 | ~15°C | ∈ [0, 35]°C |
| `power_gen` | 0x0107 | depends on sun | > 0 W if sunlit |
| `power_cons` | 0x0106 | ~12 W (OBC + RX only) | < 30 W |
| `eclipse_flag` | 0x0108 | 0 or 1 | note for context |

### 4.3 Verify Power Line Switch States

**MCS screen: eps tab → Power Lines panel**

| Line | Index | Hex ID | Expected | Notes |
|------|-------|--------|----------|-------|
| obc | 0 | 0x0110 | ON (1) | Non-switchable |
| ttc_rx | 1 | 0x0111 | ON (1) | Non-switchable |
| ttc_tx | 2 | 0x0112 | ON (1) | Auto-TX hold-down active |
| payload | 3 | 0x0113 | OFF (0) | Not yet powered |
| fpa_cooler | 4 | 0x0114 | OFF (0) | Not yet powered |
| htr_bat | 5 | 0x0115 | OFF (0) | Not yet powered |
| htr_obc | 6 | 0x0116 | OFF (0) | Not yet powered |
| aocs_wheels | 7 | 0x0117 | OFF (0) | Not yet powered |

All OFF lines are expected — we haven't powered them on yet.

### 4.4 Verify OBDH Status

**MCS screen: obdh tab**

| Parameter | Hex ID | Expected |
|-----------|--------|----------|
| `obc_mode` | 0x0300 | 0 (NOMINAL) |
| `cpu_load` | 0x0302 | ~35% baseline |
| `sw_image` | 0x0311 | 1 (APPLICATION) |
| `reboot_count` | 0x030A | 0 |
| `active_obc` | 0x030C | 0 (OBC-A) |
| `active_bus` | 0x030E | 0 (CAN Bus A) |
| `bus_a_status` | 0x030F | 0 (OK) |

### 4.5 Verify TTC Link Quality

**MCS screen: ttc tab**

| Parameter | Hex ID | Expected |
|-----------|--------|----------|
| `link_status` | 0x0501 | 2 (LOCKED) |
| `rssi` | 0x0502 | > -110 dBm |
| `link_margin` | 0x0503 | > 3.0 dB |
| `carrier_lock` | 0x050F | 1 |
| `bit_sync` | 0x0510 | 1 |
| `frame_sync` | 0x0511 | 1 |
| `antenna_deployed` | 0x0520 | 1 (if Phase 2 done) |

### 4.6 Verify TCS Baseline Temperatures

**MCS screen: tcs tab → Temperature Zones table**

| Parameter | Hex ID | Expected (ambient) | Acceptable Range |
|-----------|--------|---------------------|-----------------|
| `temp_obc` | 0x0406 | ~20°C | [-5, +45]°C |
| `temp_battery` | 0x0407 | ~15°C | [0, +35]°C |
| `temp_fpa` | 0x0408 | ~5°C (ambient, uncooled) | [-10, +30]°C |
| Panel temps | 0x0400–0x0405 | 8–20°C range | [-40, +60]°C |

### 4.7 Verify AOCS and Payload are OFF

**MCS screen: aocs tab** → `mode` (0x020F) = 0 (OFF). Wheels unpowered.
**MCS screen: payload tab** → `mode` (0x0600) = 0 (OFF). This is expected.

**GO/NO-GO:** Platform healthy. Bus voltage nominal, battery charged, OBC in application mode, link locked, temperatures in range, all unpowered subsystems confirmed OFF.

**⚠ If this step fails:** See [Commissioning Contingency Procedures](10_commissioning_contingency_procedures.md#phase-4--initial-platform-health-check) — covers voltage anomalies, thermal exceedance, bus faults.

---

## Phase 5 — Enable Telemetry Buffering (Stored TM)

### 5.1 Enable Periodic HK Reporting for All SIDs

**TC sequence:**

| TC | SID | Interval |
|----|-----|----------|
| `HK_ENABLE(sid=1)` (S3.5) | EPS | 1.0 s |
| `HK_ENABLE(sid=2)` (S3.5) | AOCS | 4.0 s |
| `HK_ENABLE(sid=3)` (S3.5) | TCS | 60.0 s |
| `HK_ENABLE(sid=4)` (S3.5) | OBDH | 8.0 s |
| `HK_ENABLE(sid=5)` (S3.5) | Payload | 8.0 s |
| `HK_ENABLE(sid=6)` (S3.5) | TTC | 8.0 s |

**MCS screen:**
- **commanding** tab → all six S1.1 acceptances
- All subsystem tabs should now show **live updating** values (no longer requiring manual HK_REQUEST)
- **obdh** tab → buffer fill levels (`hk_store` 0x0312) should begin incrementing as packets are stored for out-of-coverage playback

**Purpose:** With HK enabled, the OBC stores telemetry in the mass memory buffer even when not in contact. You can retrieve this later via S15 TM dump. Each subsystem tab has a "Request S15 TM Dump" button for this.

---

## Phase 6 — Set On-Board Time

### 6.1 Request Current Time

**TC:** `TIME_REPORT_REQUEST` (Service 9, Subtype 2)

**MCS screen: obdh tab** → current OBC Time (CUC) field shows the current onboard clock. Before SET_TIME it will show "0 (not set)". The SC TIME clock in the top status bar will show "NOT SET".

### 6.2 Upload Corrected Time

Two options in the **obdh tab → TIME CORRELATION (S9)** panel:

**Option A — Sync to Ground UTC:**
Click "Sync Time to Ground" button. This sends `SET_TIME(cuc_seconds=<ground_utc>)` where ground UTC respects the MCS sim epoch if configured.

**Option B — Set Arbitrary Time:**
Enter an ISO 8601 timestamp (e.g., `2026-03-10T12:00:00Z`) in the "Set Arbitrary Time" input field. The CUC equivalent is shown below the input. Click "Set SC Time" to send S9.1.

**TC:** `SET_TIME(cuc_seconds=<UTC_epoch>)` (Service 9, Subtype 1)

**MCS screen:**
- **commanding** tab → S1.1 acceptance
- **obdh** tab → `OBC Time (CUC)` now shows the set UTC timestamp
- **Top bar** → SC TIME clock updates to show the onboard time (HH:MM:SS UTC)
- `uptime` (0x0308) continues incrementing independently
- Request `TIME_REPORT_REQUEST` again to verify ground–spacecraft delta < 1.0 s

**GO/NO-GO:** On-board clock synchronised to UTC within 1 second. SC TIME clock active.

---

## Phase 7 — Sequential Power-On (Heaters First, Then AOCS)

This follows LEOP-007 ordering. Each step is a power line ON followed by a mode command.

### 7.1 Battery Heater ON

**TC:** `EPS_POWER_ON(line_idx=5)` (S8.1, func_id 19, data byte = 5)

**Note:** The battery heater uses thermostat control — the EPS power line must be ON for the thermostat to operate. The heater will cycle automatically between 1°C (ON setpoint) and 5°C (OFF setpoint) once the power line is energised.

**MCS screen:**
- **eps** tab → power line `htr_bat` (0x0115) = 1 (ON)
- **eps** tab → `power_cons` increases by ~6 W
- **tcs** tab → `htr_battery` (0x040A) = 1 (active when thermostat demands heat), `temp_battery` (0x0407) trending toward setpoint

### 7.2 OBC Heater ON

**TC:** `EPS_POWER_ON(line_idx=6)` (S8.1, func_id 19, data byte = 6)

**Note:** The OBC heater is manual-control only — it stays on for as long as the EPS power line is ON. No thermostat.

**MCS screen:**
- **eps** tab → power line `htr_obc` (0x0116) = 1 (ON)
- **eps** tab → `power_cons` increases by ~3 W
- **tcs** tab → `htr_obc` (0x040B) = 1 (active)

### 7.3 AOCS Wheels Power ON

**TC:** `EPS_POWER_ON(line_idx=7)` (S8.1, func_id 19, data byte = 7)

**Note:** This powers the wheel electronics. The wheels are not individually active yet — use `AOCS_ENABLE_WHEEL` to enable each wheel individually.

**MCS screen:**
- **eps** tab → power line `aocs_wheels` (0x0117) = 1 (ON)
- **eps** tab → `power_cons` increases by ~12 W

### 7.3a Enable Individual Reaction Wheels

Enable each wheel individually (only 3 of 4 are required for a control solution):

**TC sequence:**
- `AOCS_ENABLE_WHEEL(wheel_idx=0)` (S8.1, func_id 3, data byte = 0) — RW1
- `AOCS_ENABLE_WHEEL(wheel_idx=1)` (S8.1, func_id 3, data byte = 1) — RW2
- `AOCS_ENABLE_WHEEL(wheel_idx=2)` (S8.1, func_id 3, data byte = 2) — RW3
- `AOCS_ENABLE_WHEEL(wheel_idx=3)` (S8.1, func_id 3, data byte = 3) — RW4

To disable a specific wheel: `AOCS_DISABLE_WHEEL(wheel_idx=N)` (S8.1, func_id 2, data byte = N)

**MCS screen: aocs tab**
- Reaction wheel indicators turn green for enabled wheels
- Wheel speeds (0x0207–0x020A) show small values (friction noise) once enabled
- Wheels will only develop significant speed when the AOCS controller is active (DETUMBLE or higher)

**Note:** With AOCS still in OFF mode, body rates and YPR data show 0 (no attitude determination active). This is correct — the AOCS must be in an active mode to report rates.

### 7.4 AOCS Set Mode: DETUMBLE

**TC:** `AOCS_SET_MODE(mode=2)` (S8.1, func_id 0, data byte = 2)

**Note:** AOCS func_id 0 (`set_mode`) has a **mode-gate exemption** — it is allowed even when the AOCS subsystem mode is OFF/0, specifically to allow this initial mode transition.

**MCS screen: aocs tab**
- Mode state machine shows transition to DETUMBLE (mode 2)
- Body rates (0x0204–0x0206) should be visible and trending downward
- Attitude error (0x0217) displayed
- Magnetorquer duty (MTQ X/Y/Z) becomes non-zero as the B-dot controller engages

**Monitor over several minutes:** Rates should decrease from initial tumble toward < 0.5°/s within 15 minutes.

### 7.5 Verify Power Budget After AOCS Power-On

**MCS screen: eps tab**

| Parameter | Expected After Step 7.3 |
|-----------|------------------------|
| `power_cons` | ~33 W (OBC ~5W + RX ~3W + TX ~12W + heaters ~9W + wheels ~12W) |
| `bus_voltage` | > 27.0 V |
| `bat_soc` | still > 60% |
| `power_gen` - `power_cons` | positive margin if sunlit |

**GO/NO-GO:** All heaters active, AOCS in DETUMBLE, power budget positive.

**⚠ If this step fails:** See [Commissioning Contingency Procedures](10_commissioning_contingency_procedures.md#phase-7--sequential-power-on) — covers overcurrent, EPS safe mode, wheel anomalies, AOCS mode failures.

---

## Phase 8 — Upload TLE for Orbit Determination

### 8.1 Upload TLE / Orbit State Vector

**Tool:** Use the orbit tools to convert TLE or state vector into S20.1 command format:

```bash
# TLE mode — propagate to a specific epoch
python3 tools/orbit_tools.py --tle "1 99999U ..." "2 99999 ..." --epoch 2026-03-10T12:00:00

# State vector mode — direct ECEF position/velocity (meters, m/s)
python3 tools/orbit_tools.py --sv 6878000 0 0 0 7612 0

# Web interface (interactive form with TLE and state vector tabs)
python3 tools/orbit_tools.py --serve  # opens on port 8093
```

The tool outputs hex command strings for S20.1 SET_PARAM commands targeting:
- 0x0231–0x0233: GPS ECEF position X/Y/Z (meters)
- 0x0234–0x0236: GPS ECEF velocity X/Y/Z (m/s)

**TC sequence** (6 × S20.1 SET_PARAM — copy hex strings from orbit_tools output):

**MCS screen:**
- **pus** tab or **ondemand** tab → send the S20 SET_PARAM commands
- **aocs** tab → Orbital Data panel → latitude, longitude, altitude should populate with propagated values
- **aocs** tab → Eclipse field should now predict eclipse/sunlit transitions

---

## Phase 9 — AOCS Mode Progression: Rate Damping → Sun Pointing → Nadir

### 9.1 Confirm Rates Damped

**MCS screen: aocs tab** → Body Rates panel

| Parameter | Hex ID | Target |
|-----------|--------|--------|
| `rate_roll` | 0x0204 | < 0.5°/s |
| `rate_pitch` | 0x0205 | < 0.5°/s |
| `rate_yaw` | 0x0206 | < 0.5°/s |

Also check the **BODY RATES chart** (bottom of aocs tab) for trending confirmation — rates should show a clear decay curve.

### 9.2 Transition to COARSE_SUN (Safe Pointing)

**TC:** `AOCS_SET_MODE(mode=3)` (S8.1, func_id 0, data byte = 3)

**MCS screen: aocs tab**
- Mode state machine shows COARSE_SUN (mode 3)
- `att_error` (0x0217) should decrease toward < 10° within 5 minutes
- CSS Sun Vector panel shows valid sun vector (X/Y/Z components)
- **eps** tab → `power_gen` should increase as arrays face the sun

### 9.3 Download Delayed TM — First Whole-Orbit Check

**Action:** Use the S15 TM dump buttons on each subsystem tab to retrieve stored telemetry.

**MCS screen:** Each tab has a "Request S15 TM Dump" button:
- **aocs** tab → dump button retrieves stored AOCS data → check body rates chart for whole orbit
- **eps** tab → dump button → check SoC curve over eclipse/sunlit cycle, bus voltage stability
- **tcs** tab → dump button → check temperature profiles over the orbit
- **obdh** tab → dump button → check for any events/alarms during the dark period

**What to look for:**
- Body rates stayed damped (no sudden spikes)
- Temperatures stayed in range through eclipse
- Battery SoC recovered after eclipse exit
- No unexpected reboots or events
- AOCS held pointing through eclipse (eclipse_propagate mode should engage automatically)

### 9.4 Check AOCS Signs and Scaling

With whole-orbit data available, verify:
- Wheel speeds have correct sign convention (positive = forward rotation)
- Magnetometer readings match expected field model for the orbit
- CSS sun vector points toward the sun when expected
- Attitude error correlates with power generation (lower error → higher power)

**MCS screen: aocs tab** — use the Body Rates and Attitude Error charts to cross-reference.

### 9.5 Transition to NOMINAL_NADIR (Nadir Pointing)

**TC:** `AOCS_SET_MODE(mode=4)` (S8.1, func_id 0, data byte = 4)

**MCS screen: aocs tab**
- Mode = 4 (NOMINAL_NADIR)
- `att_error` (0x0217) → < 1.0° within 2 minutes, then < 0.1° when settled
- Reaction wheel speeds adjust to maintain nadir pointing
- **eps** tab → `power_gen` should be near maximum with optimal sun angle

**GO/NO-GO:** Nadir pointing achieved, attitude error < 1°, stable over multiple orbits.

**⚠ If this step fails:** See [Commissioning Contingency Procedures](10_commissioning_contingency_procedures.md#phase-9--aocs-mode-progression) — covers rate damping failure, sensor loss, mode transition issues.

---

## Phase 10 — Further Whole-Orbit Data Verification

### 10.1 Wait for Full Orbit, Then Dump Again

**Action:** Let the spacecraft complete at least one full orbit (~95 min) in nadir pointing, then dump all stored TM again.

**MCS screen** — for each subsystem tab, request S15 dump and verify:

| Check | Tab | What to Look For |
|-------|-----|-----------------|
| Thermal stability | **tcs** | All temps stay in range, heater cycling as expected |
| Power balance | **eps** | Positive orbit-average energy balance, SoC recovering |
| Attitude stability | **aocs** | att_error < 1° sustained, no mode transitions |
| OBC health | **obdh** | CPU load stable, no reboots, buffer not filling excessively |
| Link quality | **ttc** | Consistent RSSI/margin during ground passes |

### 10.2 Check Events and Alarms

**MCS screen: overview tab** → Alarm panel shows active alarms. Should be zero.

**MCS screen: obdh tab** → check FDIR event count. If any S5 events were generated during the orbit, investigate.

---

## Phase 11 — Enable GPS and Verify Cold Start

### 11.1 Enable GPS Receiver

**TC:** `SET_PARAM(0x0230, 1)` (S20.1) — GPS receiver power ON

**MCS screen: aocs tab → Orbital Data panel**
- GPS fix status should progress: 0 (no fix) → 1 (2D) → 3 (3D+velocity)
- `gps_num_sats` should increase from 0 toward ≥ 4
- Latitude/longitude/altitude should populate with real values
- A cold start may take 5–15 minutes for first fix

**Verify on aocs tab:**

| Parameter | Hex ID | Target |
|-----------|--------|--------|
| GPS fix | 0x0210 (approx) | 3 (3D+velocity) |
| Satellites | 0x0232 | ≥ 4 |
| Altitude | 0x0235 | ~500 km ± 20 km |

**GO/NO-GO:** GPS locked, position consistent with TLE-propagated orbit.

### 11.2 Synchronise Onboard Clock with GPS Time

**TC:** `OBC_GPS_TIME_SYNC` (S8.1, func_id 80)

**Prerequisites:** GPS fix type ≥ 2 (3D fix). The command will be rejected if GPS fix is insufficient.

**WARNING:** A large time jump (> 5 seconds difference between old onboard time and GPS time) will cause the AOCS to see the spacecraft at a different point in the orbit. The AOCS will automatically transition to SAFE_BOOT mode (mode 1) and will need to be reinitialised through the mode progression (DETUMBLE → COARSE_SUN → NOMINAL).

**MCS screen:**
- **commanding** tab → S1.1 acceptance
- **obdh** tab → `OBC Time (CUC)` updates to GPS-derived UTC
- **Top bar** → SC TIME clock shows GPS-synchronised time
- If time jump > 5s: **aocs** tab → mode drops to SAFE_BOOT (1), S5 event 0x020F generated
- After GPS sync, re-establish AOCS pointing: repeat Phase 9 (DETUMBLE → COARSE_SUN → NOMINAL)

---

## Phase 12 — Star Tracker Commissioning

### 12.1 Power On Star Tracker 1

**TC:** `ST1_POWER(on=1)` (S8.1, func_id 4, data byte = 1)

**MCS screen: aocs tab → Star Trackers panel**
- `ST1 Status` changes from OFF to ACQUIRING, then TRACKING
- `ST1 Stars` should show ≥ 5 tracked stars within 90 s
- `att_error` (0x0217) should improve (star tracker gives arcsecond-class accuracy)

### 12.2 Power On Star Tracker 2 (Redundant)

**TC:** `ST2_POWER(on=1)` (S8.1, func_id 5, data byte = 1)

**MCS screen: aocs tab → Star Trackers panel**
- `ST2 Status` changes to TRACKING
- Cross-check: both trackers should agree on attitude

### 12.3 Select Primary Star Tracker

**TC:** `ST_SELECT(unit=0)` (S8.1, func_id 6, data byte = 0) — select ST1 as primary

### 12.4 Power Off ST2 (Return to Nominal — One Tracker Active)

**TC:** `ST2_POWER(on=0)` (S8.1, func_id 5, data byte = 0)

ST2 is now verified working; return to single-tracker operations to save power. ST2 remains available as hot spare.

### 12.5 Transition to FINE_POINT

**TC:** `AOCS_SET_MODE(mode=5)` (S8.1, func_id 0, data byte = 5)

**MCS screen: aocs tab**
- Mode = 5 (FINE_POINT)
- `att_error` < 0.1° within 3 minutes
- Body rates < 0.01°/s on all axes

**GO/NO-GO:** Fine pointing achieved with star tracker. This is the pointing accuracy needed for imaging.

**⚠ If this step fails:** See [Commissioning Contingency Procedures](10_commissioning_contingency_procedures.md#phase-12--star-tracker-commissioning) — covers ST boot timeout, blinding, tracker failure.

---

## Phase 13 — Redundant Equipment Checkout

### 13.1 Backup CAN Bus Check

**TC:** `OBC_SELECT_BUS(bus=1)` (S8.1, func_id 54, data byte = 1) — switch to Bus B

**MCS screen: obdh tab**
- `active_bus` (0x030E) = 1 (Bus B)
- `bus_b_status` (0x0310) = 0 (OK)
- All subsystem HK should still flow (verify all tabs still updating)

**TC:** `OBC_SELECT_BUS(bus=0)` (S8.1, func_id 54, data byte = 0) — return to Bus A

**Verify:** `active_bus` = 0, `bus_a_status` (0x030F) = 0 (OK)

### 13.2 Redundant Transponder Check

**TC:** `TTC_SWITCH_REDUNDANT` (func_id 64)

**MCS screen: ttc tab**
- `ttc_mode` (0x0500) = 1 (REDUNDANT)
- `link_status` re-acquires lock
- `rssi` within 2 dB of primary

**TC:** `TTC_SWITCH_PRIMARY` (func_id 63) — return to primary

**Verify:** `ttc_mode` = 0, link locked.

### 13.3 Redundant Magnetometer Check

**TC:** `MAG_SELECT(on=1)` (S8.1, func_id 7, data byte = 1) — select redundant mag

**MCS screen: aocs tab** → magnetometer X/Y/Z values should still be reasonable, attitude maintained.

**TC:** `MAG_SELECT(on=0)` (S8.1, func_id 7, data byte = 0) — return to primary

**GO/NO-GO:** All redundant units verified functional. Return all to nominal (primary) configuration.

**⚠ If this step fails:** See [Commissioning Contingency Procedures](10_commissioning_contingency_procedures.md#phase-13--redundant-equipment-checkout) — covers bus switchover failure, transponder issues, comms loss.

---

## Phase 14 — Time-Tagged Command Verification

### 14.1 Upload a Test Time-Tagged Command

**TC:** Upload via Service 11 — a harmless command (e.g., `HK_REQUEST(sid=1)`) scheduled for a known future time (e.g., 5 minutes from now).

**MCS screen: commanding tab** → command history should show the S11 upload accepted.

### 14.2 Verify Execution In-Pass

**Monitor:** At the scheduled time, watch for the S3.27 HK packet arriving automatically.

**MCS screen:**
- **commanding** tab → the time-tagged command shows "EXECUTED" status
- **eps** tab → fresh EPS HK data arrives at the scheduled time

### 14.3 Verify Execution Out-of-Pass

**Action:** Upload another time-tagged command scheduled for during a gap in coverage. After the next contact, dump stored TM and verify the command executed.

**MCS screen:** After re-establishing contact:
- **obdh** tab → S15 TM dump shows the HK packet was generated at the scheduled time
- **commanding** tab → time-tagged execution confirmed in stored event log

**GO/NO-GO:** Time-tagged commanding works both in and out of coverage.

**⚠ If this step fails:** See [Commissioning Contingency Procedures](10_commissioning_contingency_procedures.md#phase-14--time-tagged-command-verification) — covers schedule execution failure, BER anomalies.

---

## Phase 15 — Payload Power-On and Commissioning

### 15.1 Power Payload Line

**TC:** `EPS_POWER_ON(line_idx=3)` (S8.1, func_id 19, data byte = 3)

**MCS screen:**
- **eps** tab → `payload` power line (0x0113) = 1 (ON)
- **eps** tab → `power_cons` increases by ~8–10 W

### 15.2 Set Payload to STANDBY

**TC:** `PAYLOAD_SET_MODE(mode=1)` (S8.1, func_id 26, data byte = 1)

**Note:** Payload func_id 26 has a **mode-gate exemption** — it is allowed even when payload mode is OFF/0, to allow this initial transition.

**MCS screen: payload tab**
- `mode` (0x0600) = 1 (STANDBY)
- `imager_temp` (0x0603) reporting (~20°C ambient)
- `store_used` (0x0604) = 0%
- `data_rate` (0x0608) reporting

### 15.3 Power FPA Cooler

**TC:** `EPS_POWER_ON(line_idx=4)` (S8.1, func_id 19, data byte = 4)

**MCS screen:**
- **eps** tab → `fpa_cooler` power line (0x0114) = 1 (ON)
- **eps** tab → `power_cons` increases by ~8–12 W

### 15.4 Activate FPA Cooler

**TC:** `TCS_FPA_COOLER(on=1)` (S8.1, func_id 43, data byte = 1)

**MCS screen: tcs tab**
- `cooler_fpa` (0x040C) = 1 (ACTIVE)
- **payload tab** → `fpa_temp` (0x0601) begins decreasing from ambient toward -15°C setpoint

**Cooldown monitoring on payload tab (FPA temp) and tcs tab (cooler status):**
- T+5 min: < +15°C
- T+10 min: < 0°C
- T+20 min: < -10°C
- T+30 min: stable at -15°C ± 2°C

### 15.5 First Light — Capture Test Image

Prerequisites: AOCS in FINE_POINT (Phase 12.5), FPA stable at setpoint.

**TC:** `PAYLOAD_SET_MODE(mode=2)` (S8.1, func_id 26, data byte = 2) — IMAGING mode

**MCS screen: payload tab**
- `mode` (0x0600) = 2 (IMAGING)

**TC:** `PAYLOAD_CAPTURE(lat, lon)` (S8.1, func_id 28) — trigger image capture

**MCS screen: payload tab**
- `image_count` (0x0605) increments to 1
- `store_used` (0x0604) increases (50–200 MB per image)
- `scene_id` (0x0606) set to the captured scene

**TC:** `PAYLOAD_SET_MODE(mode=1)` (S8.1, func_id 26, data byte = 1) — return to STANDBY

**GO/NO-GO:** First image captured. Download and inspect on ground for quality.

**⚠ If this step fails:** See [Commissioning Contingency Procedures](10_commissioning_contingency_procedures.md#phase-15--payload-power-on-and-commissioning) — covers payload power failure, FPA cooling issues, image capture failure.

---

## Phase 16 — Final Configuration and Handover to Nominal Ops

### 16.1 Final Power Budget Verification

**MCS screen: eps tab**

| Parameter | Expected (Fully Commissioned) |
|-----------|------------------------------|
| `power_cons` | ~65–80 W (all subsystems on) |
| `power_gen` | > 100 W when sunlit |
| Margin | positive orbit-average |
| `bat_soc` | > 60% and stable/charging |

### 16.2 Confirm All Subsystem States

**MCS screen: overview tab** — all subsystem health indicators should show nominal (green).

| Subsystem | Expected State |
|-----------|---------------|
| OBC | NOMINAL (mode 0), sw_image = 1 |
| EPS | All 8 power lines ON, positive margin |
| AOCS | FINE_POINT (mode 5), att_error < 0.1° |
| TCS | All heaters cycling normally, FPA cooler active |
| TTC | Primary transponder, link locked |
| Payload | STANDBY (mode 1), FPA at setpoint |

### 16.3 Set Spacecraft Phase to NOMINAL

The engine should automatically transition `spacecraft_phase` (0x0129) to 5 (COMMISSIONING) and then to the nominal ops phase once all conditions are met. Verify on the **overview** tab.

---

## Summary: Command Count by Position

| Position | Key Commands | MCS Tabs to Watch |
|----------|-------------|-------------------|
| **Flight Director** | GO/NO-GO at each phase gate | **overview** (health summary, alarms) |
| **TTC** | SET_PARAM(0x05FF), S17.1 ping, antenna deploy, transponder switch | **ttc** (link status, RSSI, margin, antenna) |
| **FDIR/Systems** | OBC_BOOT_APP, OBC_SELECT_BUS, HK_ENABLE ×6, time-tagged tests | **obdh** (sw_image, mode, CPU, buffers), **commanding** (acceptance log) |
| **EPS/TCS** | EPS_POWER_ON ×5, heater verification, cooler activation | **eps** (power lines, SoC, bus voltage), **tcs** (temps, heaters, cooler) |
| **AOCS** | AOCS_SET_MODE ×4, ST power ×2, MAG_SELECT, GPS enable | **aocs** (mode, rates, att_error, wheels, star trackers, GPS) |
| **Payload Ops** | PAYLOAD_SET_MODE ×3, PAYLOAD_CAPTURE, FPA cooler | **payload** (mode, FPA temp, storage, image count) |

---

## Known Simulator Issues

All previously reported issues have been resolved:

**Resolved (Session 1):**
- ~~Antenna deploy in bootloader~~ — func_id 69 added to bootloader allowlist
- ~~Thruster references on TCS screen~~ — removed from `index.html`, `displays.yaml`, `tcs.yaml`, and `hk_structures.yaml`
- **Bootloader power gate bypass** — S20 param get/set and S8 commands now skip the EPS power-state gate during bootloader phase
- **TTC RX/TX separation** — PA off no longer kills uplink lock acquisition; RX and TX paths are independent
- **Three-state link_status** — 0x0501 now reports 0 (NO_LINK), 1 (ACQUIRING), or 2 (LOCKED)
- **Payload image_count** — initialised to 0 (was incorrectly 12); FPA cooler requires ~200s cooldown

**Resolved (Session 2 — MCS display and architecture):**
- **Field name mapping** — `_UI_KEY_MAP` in `server.py` translates YAML short_keys to UI field names (e.g., `bat_soc` → `soc_pct`, `rssi` → `rssi_dbm`)
- **Antenna label** — TTC schematic shows "VHF/UHF" (was "PATCH")
- **Antenna deployed on-demand TM** — parameter 0x0520 now marked `on_demand: true` for S3 requests
- **Event ID conflict** — 0x050E no longer reused for antenna recovery; new event 0x0512 (ANTENNA_DEPLOY_READY_RECOVERED) created
- **AOCS equipment status** — magnetometer (0x025C), gyroscope (0x025D), magnetorquer (0x025E) enabled flags now in HK SID 2; UI shows grey/off when unpowered (was hardcoded green)
- **MCS architecture** — MCS does not poll the simulator; orbit from TLE propagation, ground time configurable via `--sim-epoch`

**Resolved (Session 3 — commissioning walkthrough fixes):**
- **SC TIME clock** — now reads from OBC CUC time telemetry (0x0309) instead of ground clock; shows "NOT SET" before S9.1, updates after SET_TIME
- **OBC time in HK** — parameter 0x0309 (obc_time) added to parameters.yaml and HK SID 4
- **Battery heater EPS gating** — thermostat only operates when EPS power line 5 (`htr_bat`, 0x0115) is ON
- **YPR data in AOCS OFF** — body rates and attitude error now report 0 when AOCS mode is OFF (was leaking internal state)
- **Wheel tick in OFF mode** — reaction wheels now tick in all modes (friction, decay) even when AOCS mode is OFF, allowing pre-DETUMBLE wheel health verification
- **GPS time sync** — new command `OBC_GPS_TIME_SYNC` (func_id 80) synchronises onboard clock with GPS; time jumps > 5s trigger AOCS SAFE_BOOT transition
- **Arbitrary time set UI** — TIME CORRELATION panel now has free-form time input with CUC conversion preview
- **Delayed TM plots** — delayed TM viewer now includes interactive Chart.js time-series plots with zoom/pan capability
- **TLE/orbit tools** — new `tools/orbit_tools.py` converts TLE or state vector to S20.1 command hex format (CLI + web UI on port 8093)

---

## Tools Reference

| Tool | Port | Purpose |
|------|------|---------|
| MCS | 9090 | Mission Control System web UI |
| Delayed TM Viewer | 8092 | Stored TM analysis with plots |
| Orbit Tools | 8093 | TLE/state vector to TC command converter |

---

*AIG — Artificial Intelligence Generated Content*

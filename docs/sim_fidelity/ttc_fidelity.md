# TTC Subsystem Simulator Fidelity Analysis

**Document:** SMO-SIM-FIDELITY-TTC-001
**Subsystem:** Telemetry, Tracking & Command (TT&C)
**Model file:** `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py`
**Config file:** `configs/eosat1/subsystems/ttc.yaml`
**Date:** 2026-03-12
**Revised:** 2026-07-29 — §6 (data rates) rewritten for the shipped three-tier
TM data-rate ladder (defects #85 spacecraft side, #86 ground segment).
**Target:** Undetectably different from real spacecraft TTC behaviour

> **Status note (2026-07-29).** Gap 4 below is **CLOSED**. This document
> originally proposed a two-rate state machine — 100 bps beacon, 1 kbps low and
> the then-current 64 kbps high rate, gated on antenna deployment. All three of
> those figures are now retired. What shipped is a three-rung,
> config-driven ladder in `subsystems/ttc.yaml tm_rate_tiers`:
> **BEACON 1 kbps / LOW 16 kbps / HIGH 128 kbps**, with LOW as the *routine
> operations* rate rather than a degraded one. §6 has been rewritten to
> describe what was built. The other gaps are unchanged.

---

## Table of Contents

1. [Current Model Capabilities](#1-current-model-capabilities)
2. [Gap Analysis Summary](#2-gap-analysis-summary)
3. [Gap 1 -- Dedicated PDM Command Channel with 15-Minute Timer](#3-gap-1----dedicated-pdm-command-channel-with-15-minute-timer)
4. [Gap 2 -- Burn Wire Antenna Deployment](#4-gap-2----burn-wire-antenna-deployment)
5. [Gap 3 -- Beacon Packet Mode (Bootloader)](#5-gap-3----beacon-packet-mode-bootloader)
6. [Gap 4 -- Low-Rate Pre-Deployment / High-Rate Post-Deployment Link](#6-gap-4----low-rate-pre-deployment--high-rate-post-deployment-link)
7. [Gap 5 -- Dual Ground Station with Per-Station Link Budget](#7-gap-5----dual-ground-station-with-per-station-link-budget)
8. [Gap 6 -- Ground Station Equipment Failure Injection](#8-gap-6----ground-station-equipment-failure-injection)
9. [New Parameters and Configuration](#9-new-parameters-and-configuration)
10. [Test Cases](#10-test-cases)
11. [Implementation Priority and Dependencies](#11-implementation-priority-and-dependencies)

---

## 1. Current Model Capabilities

The existing TTC model (`TTCBasicModel`, 432 lines) in `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py` provides the following capabilities:

### 1.1 State Model (`TTCState` dataclass)

| Field | Type | Default | Purpose |
|---|---|---|---|
| `mode` | int | 0 | Transponder selection: 0=primary, 1=redundant |
| `link_active` | bool | False | Current link state |
| `rssi_dbm` | float | -120.0 | Received signal strength indicator |
| `link_margin_db` | float | 0.0 | Link margin above threshold |
| `tm_data_rate` | int | 16000 | Active TM information rate (bps) — the LOW tier, which is where routine operations sit |
| `xpdr_temp` | float | 28.0 | Transponder temperature (degC) |
| `ranging_active` | bool | False | Ranging tone active flag |
| `range_km` | float | 0.0 | Slant range to ground station |
| `elevation_deg` | float | -90.0 | Ground station elevation angle |
| `azimuth_deg` | float | 0.0 | Ground station azimuth angle |
| `primary_failed` | bool | False | Primary transponder failure flag |
| `redundant_failed` | bool | False | Redundant transponder failure flag |
| `pa_on` | bool | True | Power amplifier enabled |
| `pa_temp` | float | 35.0 | PA temperature (degC) |
| `pa_overheat_shutdown` | bool | False | Auto-shutdown at 70 degC |
| `tx_fwd_power` | float | 2.0 | Transmit forward power (Watts) |
| `ber` | float | -10.0 | Bit error rate (log10 scale) |
| `eb_n0` | float | 20.0 | Eb/N0 (dB) |
| `carrier_lock` | bool | False | Carrier lock acquired |
| `bit_sync` | bool | False | Bit synchronisation acquired |
| `frame_sync` | bool | False | Frame synchronisation acquired |
| `_lock_timer` | float | 0.0 | Seconds since AOS for lock sequence |
| `cmd_rx_count` | int | 0 | Commands received counter |
| `agc_level_db` | float | -60.0 | AGC level |
| `doppler_hz` | float | 0.0 | Doppler shift on downlink |
| `range_rate_m_s` | float | 0.0 | Range rate |
| `cmd_auth_status` | int | 1 | Command authentication status |
| `total_bytes_tx` | int | 0 | Bytes transmitted this pass |
| `total_bytes_rx` | int | 0 | Bytes received this pass |

### 1.2 Link Budget Model

- Free-space path loss (FSPL) computed from slant range and downlink frequency (2200.5 MHz S-band)
- RSSI from EIRP + spacecraft antenna gain - FSPL + Gaussian noise (sigma=0.5 dB)
- Eb/N0 from SNR + coding gain (3 dB convolutional + Reed-Solomon) - injection offsets
- BER from Eb/N0 using BPSK/QPSK Q-function approximation with erfc
- Link margin = Eb/N0 - 12.0 dB (required Eb/N0 threshold)
- All computations gated on `frame_sync == True`

### 1.3 Lock Acquisition Sequence

Three-stage sequential lock with configurable delays from AOS:
- Carrier lock at 2.0 s
- Bit sync at 5.0 s (cumulative)
- Frame sync at 10.0 s (cumulative)

Locks reset to False on LOS. Timer resets on each new AOS transition.

### 1.4 PA Thermal Model

- First-order thermal model with time constant `pa_tau` (default 60 s)
- Heat input from TX load (5.0 W active, 1.0 W idle) plus failure injection
- Auto-shutdown at 70 degC, auto-recovery with 15 degC hysteresis (at 55 degC)
- Gaussian noise on temperature (sigma=0.03 degC)

### 1.5 Transponder Thermal Model

- Slower first-order model (tau = 300 s)
- Ambient 28 degC + 8 degC TX loading contribution
- Independent of PA thermal model

### 1.6 Doppler and Range Rate

- Range rate approximated from elevation angle and assumed 7500 m/s LEO orbital velocity
- Sign flip at 45 deg elevation (approaching below, receding above)
- Doppler = f_dl * v_r / c with Gaussian noise (sigma=2 Hz)

### 1.7 Commands Supported

| Command | Parameters | Description |
|---|---|---|
| `switch_primary` | -- | Select primary transponder (mode=0) |
| `switch_redundant` | -- | Select redundant transponder (mode=1) |
| `set_tm_rate` | `rate` | Set TM rate (must be hi or lo) |
| `pa_on` | -- | Enable PA (blocked during overheat) |
| `pa_off` | -- | Disable PA |
| `set_tx_power` | `power_w` | Set nominal TX power (0 < p <= max) |

### 1.8 Failure Injection

| Failure | Effect |
|---|---|
| `primary_failure` | Disables primary transponder |
| `redundant_failure` | Disables redundant transponder |
| `high_ber` | Reduces Eb/N0 by offset dB |
| `pa_overheat` | Injects extra watts into PA thermal model |
| `uplink_loss` | Blocks command reception and carrier lock |
| `receiver_degrade` | Increases receiver noise figure by dB |

### 1.9 Telemetry Parameters Published

24 parameters written to `shared_params` on each tick, IDs `0x0500`--`0x051F`. Configured via `param_ids` dictionary in `configs/eosat1/subsystems/ttc.yaml` (though the YAML currently only defines 12 of the 24 -- the rest use hardcoded defaults in the model).

### 1.10 What the Model Does NOT Do

The model currently assumes:
- A single ground station per tick (the orbit propagator selects `_primary_gs` only)
- The antenna is always deployed and operational
- No pre-deployment bootloader or beacon mode
- No PDM (Power Distribution Module) command channel concept
- No per-station link budget differences (antenna diameter, G/T, noise temperature)
- No ground station equipment failures (only spacecraft-side failures)
- Data rate switches are instantaneous operator commands, not lifecycle-driven
- No burn wire mechanism or deployment event

---

## 2. Gap Analysis Summary

| # | Gap | Operational Impact | Fidelity Impact |
|---|---|---|---|
| G1 | Dedicated PDM command channel (15-min timer) | LEOP operations, power bus reconfiguration training | **Critical** -- operators will not learn the 15-min window constraint |
| G2 | Burn wire antenna deployment | LEOP antenna deployment procedure, failure scenarios | **Critical** -- operators skip a real mission-critical irreversible event |
| G3 | Beacon packet mode (bootloader) | First acquisition, safe mode recovery, emergency comms | **High** -- beacon is the first signal operators see post-separation |
| G4 | TM data-rate ladder (**CLOSED 2026-07-29**, §6) | LEOP rate management, link-budget awareness, pass planning | **High** -- operators must plan dumps onto passes that clear the HIGH tier's 10 deg gate; routine operations run at LOW (16 kbps) with no gate at all |
| G5 | Dual GS with per-station link budget | Pass planning, station selection, concurrent tracking | **Critical** -- real ops always involve station handover and selection |
| G6 | GS equipment failure injection | Contingency training, station failover procedures | **Medium** -- extends existing failure injection framework |

---

## 3. Gap 1 -- Dedicated PDM Command Channel with 15-Minute Timer

### 3.1 Real Spacecraft Behaviour

On real spacecraft, the Power Distribution Module (PDM) has a dedicated command channel for power bus reconfiguration. When the PDM successfully decodes an uplink command:

1. The PDM starts a 15-minute **TX+PA timer**.
2. During this window, the transmitter and PA remain powered to allow ground verification of command execution via telemetry.
3. After 15 minutes with no new decoded command, the PDM autonomously shuts down the TX/PA to conserve power.
4. Each successfully decoded command resets the 15-minute timer.
5. This is critical for LEOP when the spacecraft may be power-negative and the transmitter is a significant load (20 W from `configs/eosat1/subsystems/eps.yaml`).

### 3.2 Current Model Behaviour

- PA on/off is a simple binary state controlled by operator commands (`pa_on`, `pa_off`).
- No timer mechanism exists. PA stays on until explicitly commanded off or until thermal shutdown.
- `cmd_rx_count` is tracked but has no effect on PA state.
- The EPS model has a `ttc_tx` power line that can be toggled, but there is no autonomous timer tied to command reception.

### 3.3 Implementation Requirements

#### 3.3.1 New State Fields in `TTCState`

```python
# PDM command channel
pdm_mode: bool = False            # True when PDM command channel is active
pdm_tx_timer_s: float = 0.0      # Countdown timer (starts at 900.0 = 15 min)
pdm_tx_timer_max_s: float = 900.0  # Timer duration (configurable)
pdm_last_cmd_epoch: float = 0.0  # Sim-time of last decoded command
pdm_cmd_decode_count: int = 0    # Commands decoded via PDM channel
pdm_auto_shutdown: bool = False   # True when timer expired and TX shut down
```

#### 3.3.2 Tick Logic

```
IF pdm_mode:
    IF cmd decoded this tick:
        pdm_tx_timer_s = pdm_tx_timer_max_s   # reset 15-min timer
        pa_on = True
        pdm_auto_shutdown = False
    ELSE:
        pdm_tx_timer_s -= dt
        IF pdm_tx_timer_s <= 0:
            pa_on = False
            tx_fwd_power = 0.0
            pdm_auto_shutdown = True
            EMIT event "PDM TX timer expired -- PA shutdown"
```

#### 3.3.3 Command Interface

| Command | Parameters | Description |
|---|---|---|
| `pdm_enable` | -- | Activate PDM command channel mode |
| `pdm_disable` | -- | Deactivate PDM mode (PA follows normal control) |
| `pdm_set_timer` | `duration_s` | Override 15-min default (for testing) |

#### 3.3.4 Integration with EPS

When `pdm_auto_shutdown == True`, the TTC model should write `0` to a shared parameter (e.g., `0x0520`) that the EPS model reads to set `ttc_tx` power line to off. This creates the realistic cross-subsystem coupling where PDM timer expiry causes TX power line dropout visible in EPS telemetry.

#### 3.3.5 Event Generation

- Event `0x0540`: "PDM TX timer started (900 s)" -- severity INFO
- Event `0x0541`: "PDM TX timer expired -- PA auto-shutdown" -- severity WARNING
- Event `0x0542`: "PDM TX timer reset by command decode" -- severity INFO

---

## 4. Gap 2 -- Burn Wire Antenna Deployment

### 4.1 Real Spacecraft Behaviour

EOSAT-1 uses a stowed S-band antenna during launch, held in place by burn wire retention mechanisms. Deployment sequence:

1. **Pre-deployment state**: Antenna stowed, using a low-gain patch antenna with approximately -3 dBi gain (vs. +3 dBi deployed).
2. **Burn wire activation**: Ground command fires a resistive burn wire that melts a nylon retention cord. Typical activation requires 2 A for 30 seconds, drawing ~56 W from the bus.
3. **Deployment confirmation**: Microswitch or hall-effect sensor confirms antenna arm has reached deployed position. Link margin improves by approximately 6 dB (gain swing from -3 to +3 dBi).
4. **Irreversible**: Once deployed, antenna cannot be re-stowed. Failed deployment is a contingency scenario that degrades the mission to low-rate only.
5. **Redundancy**: Two independent burn wire circuits (primary and backup) are provided.

### 4.2 Current Model Behaviour

- `sc_gain_dbi` is a fixed configuration value (3.0 dBi) set at configure time.
- No deployment state, no stowed antenna gain, no burn wire mechanism.
- Link budget always uses the deployed antenna gain.
- No EPS load spike from burn wire activation.

### 4.3 Implementation Requirements

#### 4.3.1 New State Fields in `TTCState`

```python
# Antenna deployment
antenna_deployed: bool = False         # True after successful deployment
antenna_deploy_in_progress: bool = False  # True during burn wire activation
antenna_deploy_timer_s: float = 0.0    # Burn wire activation countdown
antenna_deploy_duration_s: float = 30.0  # Nominal burn time
antenna_stowed_gain_dbi: float = -3.0  # Gain when stowed
antenna_deployed_gain_dbi: float = 3.0 # Gain when deployed
burn_wire_primary_fired: bool = False   # Primary burn wire has been used
burn_wire_backup_fired: bool = False    # Backup burn wire has been used
burn_wire_primary_failed: bool = False  # Primary burn wire failure (injection)
burn_wire_backup_failed: bool = False   # Backup burn wire failure (injection)
deploy_microswitch: bool = False        # Deployment confirmation sensor
```

#### 4.3.2 Tick Logic

```
# Determine effective antenna gain
IF antenna_deployed:
    effective_gain = antenna_deployed_gain_dbi
ELSE:
    effective_gain = antenna_stowed_gain_dbi

# Use effective_gain in link budget instead of self._sc_gain
# This creates ~6 dB link margin improvement on deployment

# Burn wire activation sequence
IF antenna_deploy_in_progress:
    antenna_deploy_timer_s -= dt
    # Write EPS load: shared_params[0x05E0] = 56.0 (burn wire power draw)
    IF antenna_deploy_timer_s <= 0:
        antenna_deploy_in_progress = False
        # Check if burn wire was functional
        IF (using_primary AND NOT burn_wire_primary_failed) OR
           (using_backup AND NOT burn_wire_backup_failed):
            antenna_deployed = True
            deploy_microswitch = True
            EMIT event "Antenna deployment confirmed"
        ELSE:
            EMIT event "Antenna deployment FAILED -- burn wire fault"
```

#### 4.3.3 Command Interface

| Command | Parameters | Description |
|---|---|---|
| `deploy_antenna_primary` | -- | Fire primary burn wire |
| `deploy_antenna_backup` | -- | Fire backup burn wire |

Both commands should be rejected if `antenna_deployed == True` (irreversible) or if the respective wire has already been fired.

#### 4.3.4 Link Margin Impact

The link budget calculation in the `tick` method (line 177-215) currently uses `self._sc_gain`. This must be replaced with a dynamic `effective_gain` that reflects the antenna deployment state:

- **Pre-deployment**: RSSI drops by ~6 dB. The LOW tier (16 kbps) is deliberately *not* gated on antenna deployment — a stowed-antenna spacecraft must still be able to send housekeeping — so this shows up as reduced margin, not as a forced rate change
- **Post-deployment**: RSSI returns to nominal, and the antenna gate on the HIGH tier (128 kbps) opens

#### 4.3.5 Failure Injection

| Failure | Effect |
|---|---|
| `burn_wire_primary_fail` | Primary burn wire circuit open -- deployment via primary will not release antenna |
| `burn_wire_backup_fail` | Backup burn wire circuit open |
| `partial_deployment` | Antenna partially deployed -- gain at intermediate value (e.g., 0 dBi) |

---

## 5. Gap 3 -- Beacon Packet Mode (Bootloader)

### 5.1 Real Spacecraft Behaviour

After separation from the launch vehicle, the spacecraft OBC boots into a bootloader that enables a minimal beacon mode:

1. **Beacon content**: Fixed-format packet containing spacecraft ID, battery voltage, OBC temperature, transponder status, and a mission-elapsed-time counter. Approximately 64 bytes.
2. **Beacon interval**: Transmitted every 10 seconds on the downlink frequency.
3. **Beacon data rate**: real CubeSat beacons run at 1200-9600 bps (GASPACS 9600 bps 2GFSK, OPS-SAT 9.6 kbit/s GMSK). EOSAT-1 flies **1000 bps** as its BEACON rung — SID 11 is 28 bytes every 30 s = 7.5 bps, so 1 kbps carries it with 134x headroom in ~3.1 kHz of occupied bandwidth. The 100 bps figure this document originally proposed was below any flown beacon rate and was not adopted.
4. **No uplink processing**: In pure beacon mode, the transponder transmits but does not process uplink commands until the bootloader hands off to the flight software.
5. **Duration**: Beacon mode persists from separation until OBC boot completes (typically 90--180 seconds) or until ground command switches to normal TM mode.
6. **Emergency fallback**: Beacon mode can be re-entered by the FDIR system if the OBC enters safe mode, providing a minimal lifeline for recovery.

### 5.2 Current Model Behaviour

- ~~No beacon mode concept.~~ **CLOSED** — `beacon_mode` forces the BEACON rung.
- ~~`tm_data_rate` can only be 1000 or 64000 bps.~~ **CLOSED** — `tm_data_rate` takes any rung of `tm_rate_tiers`: 1000 (BEACON), 16000 (LOW), 128000 (HIGH).
- ~~No bootloader or startup sequence simulation.~~ **CLOSED** — see LEOP-001.
- No reduced-content telemetry frame (SID 11 is the reduced set, so this is largely moot).

### 5.3 Implementation Requirements

#### 5.3.1 New State Fields in `TTCState`

```python
# Beacon mode
beacon_mode: bool = True               # Start in beacon mode post-separation
beacon_interval_s: float = 10.0        # Beacon repetition interval
beacon_rate_bps: int = 100             # Beacon data rate
beacon_timer_s: float = 0.0            # Timer for next beacon transmission
beacon_packet_count: int = 0           # Number of beacon packets transmitted
bootloader_active: bool = True         # True until flight SW takes over
bootloader_timer_s: float = 0.0        # Time since boot start
bootloader_duration_s: float = 120.0   # Nominal boot time (configurable)
uplink_enabled: bool = False           # False during pure beacon mode
```

#### 5.3.2 Tick Logic

```
IF bootloader_active:
    bootloader_timer_s += dt
    beacon_mode = True
    uplink_enabled = False
    tm_data_rate = beacon_rate_bps  # 100 bps

    # Beacon packet generation
    beacon_timer_s -= dt
    IF beacon_timer_s <= 0:
        beacon_timer_s = beacon_interval_s
        beacon_packet_count += 1
        # Write beacon content to shared_params for TM builder

    IF bootloader_timer_s >= bootloader_duration_s:
        bootloader_active = False
        uplink_enabled = True
        beacon_mode = False
        tm_data_rate = tier(safe_tier).bps  # LOW, 16 kbps — the safe/FDIR rung
        EMIT event "Bootloader complete -- flight SW active"
ELSE:
    # Normal telemetry mode
    ...
```

#### 5.3.3 Beacon Packet Content

The beacon packet should be a distinct structure written to shared_params or emitted as a special TM packet:

| Field | Size | Source |
|---|---|---|
| Spacecraft ID | 2 bytes | Fixed: 0xE051 |
| Mission elapsed time | 4 bytes | Seconds since separation |
| Battery voltage | 2 bytes | `eps.bus_voltage` (0x0105) |
| Battery SoC | 1 byte | `eps.bat_soc` (0x0101) |
| OBC temperature | 2 bytes | `obdh.temp` (0x0304) |
| Transponder mode | 1 byte | `ttc.mode` (0x0500) |
| PA status | 1 byte | `ttc.pa_on` (0x0516) |
| Boot status | 1 byte | 0x00=booting, 0x01=ready |

#### 5.3.4 Command Interface

| Command | Parameters | Description |
|---|---|---|
| `enter_beacon_mode` | -- | Force transition to beacon mode (FDIR/safe mode) |
| `exit_beacon_mode` | -- | Exit beacon mode to normal TM (requires uplink) |

#### 5.3.5 Integration with FDIR

The FDIR system (configured in `configs/eosat1/fdir/fdir.yaml`) should be able to trigger beacon mode entry as a recovery action. This requires a new FDIR action type `"enter_beacon_mode"` that calls the TTC model's `enter_beacon_mode` command.

---

## 6. Gap 4 -- The TM Data-Rate Ladder  **[CLOSED 2026-07-29]**

### 6.1 Real Spacecraft Behaviour

Real LEO missions do not have "a low rate and a high rate". They have a small,
fixed, programmable set of TM rates selected by telecommand as part of the pass
plan. Copernicus Sentinel-1 is the closest precedent to this mission: an S-band
housekeeping downlink with **16 / 128 / 512 kbit/s** programmable rates over a
fixed 4 kbit/s telecommand uplink. Commodity CubeSat TT&C radios bracket the
same range (GomSpace NanoCom AX2150 tops out at 96 kbps, EnduroSat S-band at
125 kbps), so a 6U can fly the whole ladder on one radio.

The rate lifecycle through LEOP is therefore:

1. **BEACON (1 kbps)**: separation to boot complete. Bootloader only.
2. **LOW (16 kbps)**: from application boot onwards. This is the **routine
   operations** rate, not a degraded one — it carries the full periodic
   housekeeping set and plays back stored HK.
3. **HIGH (128 kbps)**: raised per dump, released afterwards.

### 6.2 What Was Built

`configs/eosat1/subsystems/ttc.yaml` defines the ladder as configuration; the
model reads it and nothing hard-codes a rate:

```yaml
tm_rate_tiers:
  - {name: BEACON, bps: 1000,   command_code: 0xFF, modulation: BPSK, coding: CONV_RS,
     coding_gain_db: 7.5, beacon_only: true,  requires_antenna: false}
  - {name: LOW,    bps: 16000,  command_code: 0x00, modulation: BPSK, coding: CONV_RS,
     coding_gain_db: 7.5, requires_antenna: false}
  - {name: HIGH,   bps: 128000, command_code: 0x01, modulation: QPSK, coding: RS,
     coding_gain_db: 6.0, requires_antenna: true, requires_downlink_mode: true,
     min_elevation_deg: 10.0}
default_tier: LOW         # routine operations
beacon_tier: BEACON       # bootloader / beacon mode
safe_tier: LOW            # safe mode, FDIR fallback
tc_uplink_rate_bps: 4000  # explicit: the uplink is NOT on the ladder
```

**Why 16 kbps and not 1 kbps for routine operations.** The shipped
`telemetry/hk_structures.yaml` generates 1351.5 bps of PUS packets across SIDs
1-6, which is **1557 bps of channel** after RS(255,223) and the 1115/1107 TM
transfer frame. 1000 bps is *below* the generation rate: the real-time backlog
grows without bound and `tm_queue_drops` climbs forever. 1 kbps was never an
operations rate; it is a beacon rate. 16 kbps clears real-time HK at 9.7 % duty
and still plays back ~0.85 orbits of stored HK in a 514 s pass.

**Why 128 kbps and not 64 kbps for the top rung.** 64 kbps was two-thirds of
what a commodity CubeSat *TT&C* radio does and a small fraction of an S-band
*payload* transmitter. 128 kbps is Sentinel-1's nominal S-band TM rate and the
top of the TT&C-radio class, and it makes one 4 MiB image cost 262 s of channel
— 58 % of an average pass — so pass planning stays the binding lesson.

### 6.3 Rate State Machine (as implemented)

```
         [SEPARATION / bootloader]
                    |
          beacon_mode = True
                    v
          +----------------------+
          |   BEACON  1 kbps     |   beacon_only, no antenna needed
          +----------------------+
                    |  application boot -> beacon_mode = False
                    v
          +----------------------+
          |    LOW   16 kbps     |   default_tier AND safe_tier.
          +----------------------+   No gates: works with the antenna stowed,
                    ^                at any elevation, in SAFE, under FDIR.
                    |
   TTC_SET_DATA_RATE(1) with ALL of:
     antenna_deployed  AND  downlink_mode  AND  elevation >= 10 deg
                    |
                    v
          +----------------------+
          |   HIGH  128 kbps     |   dumps only
          +----------------------+
                    |
      any gate closes -> ACTIVE tier demotes to LOW,
      COMMANDED tier is retained and resumes when it reopens
```

The commanded tier (`data_rate_mode`, telemetry 0x053A) and the active tier
(`tm_tier`, telemetry 0x0539) are separate state. `_effective_tier()` walks
down the ladder from the commanded tier until every gate is satisfied; it never
overwrites the commanded tier. That is what makes HIGH auto-revert at LOS and
come back on the next pass above 10 deg without re-commanding — and what makes
"commanded HIGH, active LOW" a readable diagnosis on the console rather than an
unexplained slow dump.

### 6.4 Rejection, Not Clamping

`set_tm_rate` refuses a gated tier with an S1.8 execution failure, error
`0x0007`, whose message names the open gate:

```
"HIGH (128000 bps) requires DOWNLINK/dump mode (TTC_SET_DOWNLINK_MODE)"
"HIGH (128000 bps) requires elevation >= 10 deg (now 6.4 deg)"
"HIGH (128000 bps) requires a deployed antenna"
```

This is a deliberate fidelity choice over the silent clamp this document
originally proposed. A clamp teaches an operator that the spacecraft ignores
them; a rejection with a reason teaches them the rule.

### 6.5 Link Budget Impact

The link budget uses the active tier's bit rate in the noise-bandwidth term and
the active tier's `coding_gain_db` against the 9.6 dB uncoded BPSK/QPSK
requirement. A rung step is exactly the `-10*log10(R)` term:

| Step | Rate ratio | Rate term | Coding change | Total margin change |
|---|---|---|---|---|
| BEACON -> LOW | 16x | -12.04 dB | none (both conv+RS) | **-12.04 dB** |
| LOW -> HIGH | 8x | -9.03 dB | conv+RS 7.5 -> RS 6.0 dB | **-10.53 dB** |

Measured margins, from `python tools/link_budget_ladder.py` (same model object
the simulator flies):

| Tier | Iqaluit @5 deg | Troll @5 deg (worst) | Iqaluit @90 deg |
|---|---|---|---|
| BEACON | 57.8 dB | 56.3 dB | 74.3 dB |
| LOW | 45.7 dB | 44.2 dB | 62.3 dB |
| HIGH | 35.2 dB | 33.7 dB | 51.8 dB |

Every rung closes with tens of dB at every station and geometry. **This link is
bandwidth- and policy-limited, not power-limited**, which is why the HIGH tier
is gated on mode and elevation rather than left to a margin threshold. Modelling
a fake power cliff would have been less honest than modelling the real reason a
mission withholds its top rate.

### 6.6 Occupied Bandwidth

Symbol rate = information rate / bits-per-symbol; occupied BW ~ 1.35 x symbol
rate (SRRC alpha 0.35), against the 5.00 MHz per-mission cap in 2200-2290 MHz:

| Tier | Modulation | Symbol rate | Occupied BW | % of 5 MHz |
|---|---|---|---|---|
| BEACON | BPSK | 1 kBd | 3.1 kHz | 0.1 % |
| LOW | BPSK | 16 kBd | 49.8 kHz | 1.0 % |
| HIGH | QPSK | 64 kBd | 99.5 kHz | 2.0 % |

QPSK at the top rung halves the occupied bandwidth for the same information
rate, and makes the tier step visible on the radio — the constellation, the eye
and the spectrum all change — which teaches that rate and modulation are
independent levers.

### 6.7 The Uplink Is Not On The Ladder

`tc_uplink_rate_bps: 4000` is set explicitly. It used to default to
`tm_rate_hi_bps`, so raising the top TM rung would silently have raised the
*telecommand* rate with it and changed the uplink lock timing. 4 kbps is
Sentinel-1's TC rate and matches `rfsim.yaml pipeline.tc_symbol_rate`.

---

## 7. Gap 5 -- Dual Ground Station with Per-Station Link Budget

### 7.1 Real Spacecraft Behaviour

EOSAT-1 operations use a network of four ground stations (from `configs/eosat1/planning/ground_stations.yaml`):

| Station | Latitude | Antenna Dia. | Implied G/T |
|---|---|---|---|
| Svalbard | 78.2 N | 13.0 m | ~26 dB/K |
| Troll | 72.0 S | 7.3 m | ~20 dB/K |
| Inuvik | 68.3 N | 11.0 m | ~24 dB/K |
| O'Higgins | 63.3 S | 9.0 m | ~22 dB/K |

In real operations:
- Each station has different antenna gain, system noise temperature, and G/T.
- Link budgets differ per station -- a pass over Svalbard (13 m dish) has ~6 dB more margin than a pass over Troll (7.3 m dish).
- Operators see the current station name in telemetry and plan commanding around station capabilities.
- Station handover occurs when spacecraft passes between coverage zones.
- Different stations may have different uplink EIRP capabilities.

### 7.2 Current Model Behaviour

- The orbit propagator (`OrbitPropagator`) accepts a list of `GroundStation` objects but only uses `_primary_gs` (the first station) for contact calculations.
- `gs_g_t_db` is a single fixed value (20.0 dB/K) in the TTC config.
- All passes use identical link budget parameters regardless of which station would geometrically be in view.
- `OrbitState` has single-valued `gs_elevation_deg`, `gs_azimuth_deg`, `gs_range_km`, `in_contact` -- no station identifier.
- No concept of station handover or multiple simultaneous contacts.

### 7.3 Implementation Requirements

#### 7.3.1 Orbit Propagator Changes (smo-common)

The `OrbitPropagator.advance()` method (in `packages/smo-common/src/smo_common/orbit/propagator.py`) needs to compute look angles for ALL ground stations, not just `_primary_gs`:

```python
# In advance():
best_el = -90.0
best_gs = None
all_contacts = []

for gs in self._ground_stations:
    r_ecef = _eci_to_ecef(r, gmst)
    el_deg, az_deg, rng_km = _look_angles(r_ecef, gs.ecef, gs.lat_rad, gs.lon_rad)
    if el_deg >= gs.min_elevation_deg:
        all_contacts.append({
            'name': gs.name, 'el': el_deg, 'az': az_deg,
            'range_km': rng_km, 'gs': gs
        })
    if el_deg > best_el:
        best_el = el_deg
        best_gs = gs
        best_el_deg, best_az_deg, best_rng_km = el_deg, az_deg, rng_km
```

New `OrbitState` fields:

```python
gs_name: str = ""                      # Name of best (highest elevation) GS
gs_contacts: list = field(default_factory=list)  # All stations with el > min
```

#### 7.3.2 Ground Station Configuration Extension

Extend `configs/eosat1/planning/ground_stations.yaml`:

```yaml
ground_stations:
  - name: Svalbard
    lat_deg: 78.229
    lon_deg: 15.407
    alt_km: 0.458
    min_elevation_deg: 5.0
    antenna_diameter_m: 13.0
    band: S
    # New fields for per-station link budget:
    g_t_db: 26.0              # System G/T (dB/K)
    uplink_eirp_dbw: 55.0     # Uplink EIRP (dBW)
    system_noise_temp_k: 150  # System noise temperature (K)
    polarization_loss_db: 0.3 # Polarisation mismatch loss
    pointing_loss_db: 0.5     # Antenna pointing error loss
    radome_loss_db: 0.2       # Radome loss (if applicable)
```

#### 7.3.3 TTC Model Changes

The `tick()` method must use the active station's G/T instead of the fixed `self._gs_gt`:

```python
# Determine active station G/T from orbit_state
active_gs_gt = self._gs_gt  # fallback
if hasattr(orbit_state, 'gs_contacts') and orbit_state.gs_contacts:
    active_contact = orbit_state.gs_contacts[0]  # highest elevation
    active_gs_gt = active_contact.get('g_t_db', self._gs_gt)
    s.active_gs_name = active_contact['name']
```

#### 7.3.4 New State Fields

```python
active_gs_name: str = ""          # Name of current ground station
active_gs_gt_db: float = 20.0    # G/T of current station
gs_handover_count: int = 0       # Number of station handovers this sim
```

#### 7.3.5 New Telemetry Parameters

| ID | Name | Description |
|---|---|---|
| 0x0521 | `ttc.active_gs_id` | Numeric ID of active ground station (0-3) |
| 0x0522 | `ttc.active_gs_gt` | G/T of active station (dB/K) |

#### 7.3.6 Station Selection Logic

The TTC model (or orbit propagator) must implement station selection policy:

1. **Highest elevation**: Default policy -- select station with highest current elevation angle.
2. **Operator override**: Allow operator to lock to a specific station (for testing station-specific procedures).
3. **Handover**: When a new station rises above minimum elevation while the current station is setting, generate a handover event.

---

## 8. Gap 6 -- Ground Station Equipment Failure Injection

### 8.1 Real Spacecraft Behaviour

Ground station equipment failures that affect TTC operations include:

1. **Antenna drive failure**: Station cannot track spacecraft -- loss of contact for that station.
2. **LNA failure**: Low-noise amplifier degrades receiver sensitivity by 10--20 dB -- link margin drops, possibly below threshold.
3. **Uplink transmitter failure**: Station can receive TM but cannot send TC -- read-only pass.
4. **Frequency standard drift**: Doppler compensation errors, possible loss of carrier lock.
5. **Data link failure**: Station-to-MCS network down -- pass data not available in real-time.
6. **Power failure**: Complete station outage.

### 8.2 Current Model Behaviour

- All six existing failure modes are spacecraft-side only.
- No concept of ground station health or capability degradation.
- The orbit propagator provides `in_contact` as a boolean with no station-specific failure state.
- The loss-of-communication procedure (`configs/eosat1/procedures/emergency/loss_of_communication.md`) references ground station verification but the simulator cannot inject station failures to train this procedure.

### 8.3 Implementation Requirements

#### 8.3.1 Ground Station State Model

```python
@dataclass
class GroundStationState:
    name: str = ""
    operational: bool = True        # Overall station operational flag
    antenna_tracking: bool = True   # Antenna drive functional
    lna_functional: bool = True     # Low-noise amplifier functional
    lna_degrade_db: float = 0.0    # LNA degradation (dB NF increase)
    uplink_available: bool = True   # Uplink transmitter functional
    freq_standard_drift_hz: float = 0.0  # Frequency standard error
    data_link_up: bool = True      # Station-to-MCS link available
    g_t_effective_db: float = 20.0 # Effective G/T with degradations
```

#### 8.3.2 New Failure Injection Interface

```python
def inject_gs_failure(self, station_name: str, failure: str,
                      magnitude: float = 1.0, **kw) -> None:
```

| Failure | Station Effect | TTC Impact |
|---|---|---|
| `gs_antenna_fail` | `antenna_tracking = False` | Station removed from contact list |
| `gs_lna_fail` | `lna_functional = False` | G/T drops by ~15 dB |
| `gs_lna_degrade` | `lna_degrade_db = magnitude` | G/T reduced by N dB |
| `gs_uplink_fail` | `uplink_available = False` | TC rejected on this station, TM still flows |
| `gs_freq_drift` | `freq_standard_drift_hz = magnitude` | Carrier lock acquisition delayed or lost |
| `gs_data_link_fail` | `data_link_up = False` | Pass occurs but TM not forwarded to MCS |
| `gs_power_fail` | `operational = False` | Complete station outage |

#### 8.3.3 Integration with Tick Loop

```python
# In tick():
if in_contact and active_gs is not None:
    gs_state = self._gs_states.get(active_gs.name)
    if gs_state and not gs_state.operational:
        in_contact = False  # Station down
    elif gs_state and not gs_state.antenna_tracking:
        in_contact = False  # Cannot track
    elif gs_state and gs_state.lna_degrade_db > 0:
        # Reduce effective G/T
        effective_gt = gs_gt - gs_state.lna_degrade_db
    if gs_state and not gs_state.uplink_available:
        uplink_blocked = True  # TC not possible on this pass
    if gs_state and not gs_state.data_link_up:
        tm_forwarding_blocked = True  # MCS won't see real-time TM
```

#### 8.3.4 Uplink-Only Failure (TC Blocked)

This is a particularly important training scenario: the ground station can receive telemetry but cannot transmit commands. Operators must:
- Recognise that they have TM but no TC capability on this pass
- Plan critical commands for a different station's pass
- Communicate the constraint across console positions

Implementation: when `uplink_available == False` for the active station, the engine's `uplink_active` property should return False even though `in_contact` is True. This requires the TTC model to write a `uplink_blocked_by_gs` flag to shared_params that the engine checks.

---

## 9. New Parameters and Configuration

### 9.1 New Telemetry Parameters

The following new parameters need to be added to `configs/eosat1/telemetry/parameters.yaml` in the TTC section. IDs are allocated from the next available block (`0x0520`+):

| ID | Name | Units | Description |
|---|---|---|---|
| 0x0520 | `ttc.pdm_mode` | -- | PDM command channel active (0/1) |
| 0x0521 | `ttc.pdm_timer_remaining` | s | PDM TX timer remaining seconds |
| 0x0522 | `ttc.pdm_auto_shutdown` | -- | PDM timer-expired TX shutdown (0/1) |
| 0x0523 | `ttc.antenna_deployed` | -- | Antenna deployment state (0=stowed, 1=deployed) |
| 0x0524 | `ttc.antenna_deploy_progress` | -- | Deployment in progress (0/1) |
| 0x0525 | `ttc.deploy_microswitch` | -- | Deployment confirmation sensor (0/1) |
| 0x0526 | `ttc.burn_wire_primary` | -- | Primary burn wire status (0=unfired, 1=fired) |
| 0x0527 | `ttc.burn_wire_backup` | -- | Backup burn wire status (0=unfired, 1=fired) |
| 0x0528 | `ttc.beacon_mode` | -- | Beacon mode active (0/1) |
| 0x0529 | `ttc.beacon_packet_count` | -- | Beacon packets transmitted |
| 0x052A | `ttc.bootloader_active` | -- | Bootloader running (0/1) |
| 0x052B | `ttc.uplink_enabled` | -- | Uplink processing enabled (0/1) |
| 0x052C | `ttc.effective_gain` | dBi | Current effective antenna gain |
| 0x052D | `ttc.data_rate_state` | -- | Rate state machine: 0=beacon, 1=low, 2=high |
| 0x052E | `ttc.active_gs_id` | -- | Active ground station ID (0--3) |
| 0x052F | `ttc.active_gs_gt` | dB/K | Active station G/T |
| 0x0530 | `ttc.gs_uplink_available` | -- | Active station uplink capability (0/1) |
| 0x0531 | `ttc.gs_data_link` | -- | Active station data link to MCS (0/1) |

### 9.2 New Configuration in `configs/eosat1/subsystems/ttc.yaml`

```yaml
model: ttc_basic
ul_freq_mhz: 2025.5
dl_freq_mhz: 2200.5
# The three-rung TM data-rate ladder is the source of truth (see section 6.2).
# The two flat keys below survive only as deprecated aliases for out-of-lane
# readers and are derived from the ladder when omitted.
tm_rate_tiers: [...]        # BEACON 1000 / LOW 16000 / HIGH 128000
default_tier: LOW
beacon_tier: BEACON
safe_tier: LOW
tm_rate_hi_bps: 128000      # deprecated alias -> top tier
tm_rate_lo_bps: 16000       # deprecated alias -> safe tier
tc_uplink_rate_bps: 4000    # explicit: the uplink is NOT on the ladder
eirp_dbw: 10.0
gs_g_t_db: 20.0            # Fallback G/T when per-station not available
sc_gain_dbi: 3.0           # Deployed antenna gain
sc_gain_stowed_dbi: -3.0   # Stowed antenna gain
coding_gain_db: 3.0

# PDM command channel
pdm_tx_timer_s: 900         # 15 minutes
pdm_enabled_at_start: false # PDM mode off by default (enabled for LEOP sim)

# Antenna deployment
antenna_deployed_at_start: false  # For LEOP sim; true for nominal ops
burn_wire_duration_s: 30.0
burn_wire_power_w: 56.0

# Bootloader / beacon
bootloader_duration_s: 120.0
beacon_interval_s: 10.0
start_in_beacon_mode: false  # For LEOP sim; false for nominal ops

# Data rate lifecycle
enforce_rate_vs_antenna: true
auto_rate_on_deploy: true

# PA thermal
pa_max_power_w: 5.0
pa_nominal_power_w: 2.0
pa_shutdown_temp_c: 70.0
pa_thermal_tau_s: 60.0

param_ids:
  ttc_mode: 0x0500
  link_status: 0x0501
  rssi: 0x0502
  link_margin: 0x0503
  ul_freq: 0x0504
  dl_freq: 0x0505
  tm_data_rate: 0x0506
  xpdr_temp: 0x0507
  ranging_status: 0x0508
  range_km: 0x0509
  contact_elevation: 0x050A
  contact_az: 0x050B
  ber: 0x050C
  tx_fwd_power: 0x050D
  pa_temp: 0x050F
  carrier_lock: 0x0510
  bit_sync: 0x0511
  frame_sync: 0x0512
  cmd_rx_count: 0x0513
  pa_on: 0x0516
  eb_n0: 0x0519
  pdm_mode: 0x0520
  pdm_timer_remaining: 0x0521
  pdm_auto_shutdown: 0x0522
  antenna_deployed: 0x0523
  antenna_deploy_progress: 0x0524
  deploy_microswitch: 0x0525
  burn_wire_primary: 0x0526
  burn_wire_backup: 0x0527
  beacon_mode: 0x0528
  beacon_packet_count: 0x0529
  bootloader_active: 0x052A
  uplink_enabled: 0x052B
  effective_gain: 0x052C
  data_rate_state: 0x052D
  active_gs_id: 0x052E
  active_gs_gt: 0x052F
  gs_uplink_available: 0x0530
  gs_data_link: 0x0531
```

### 9.3 Extended Ground Station Configuration

`configs/eosat1/planning/ground_stations.yaml` additions per station:

```yaml
ground_stations:
  - name: Svalbard
    lat_deg: 78.229
    lon_deg: 15.407
    alt_km: 0.458
    min_elevation_deg: 5.0
    antenna_diameter_m: 13.0
    band: S
    g_t_db: 26.0
    uplink_eirp_dbw: 55.0
    system_noise_temp_k: 150
    polarization_loss_db: 0.3
    pointing_loss_db: 0.5
    radome_loss_db: 0.0
  - name: Troll
    lat_deg: -72.012
    lon_deg: 2.535
    alt_km: 1.27
    min_elevation_deg: 5.0
    antenna_diameter_m: 7.3
    band: S
    g_t_db: 20.0
    uplink_eirp_dbw: 52.0
    system_noise_temp_k: 200
    polarization_loss_db: 0.3
    pointing_loss_db: 0.8
    radome_loss_db: 0.0
  - name: Inuvik
    lat_deg: 68.318
    lon_deg: -133.549
    alt_km: 0.1
    min_elevation_deg: 5.0
    antenna_diameter_m: 11.0
    band: S
    g_t_db: 24.0
    uplink_eirp_dbw: 54.0
    system_noise_temp_k: 170
    polarization_loss_db: 0.3
    pointing_loss_db: 0.5
    radome_loss_db: 0.2
  - name: "O'Higgins"
    lat_deg: -63.321
    lon_deg: -57.902
    alt_km: 0.01
    min_elevation_deg: 5.0
    antenna_diameter_m: 9.0
    band: S
    g_t_db: 22.0
    uplink_eirp_dbw: 53.0
    system_noise_temp_k: 180
    polarization_loss_db: 0.3
    pointing_loss_db: 0.6
    radome_loss_db: 0.0
```

---

## 10. Test Cases

### 10.1 PDM Command Channel Tests

| ID | Test | Assertion |
|---|---|---|
| T-PDM-01 | Enable PDM mode, tick 900+ seconds without commands | `pdm_auto_shutdown == True`, `pa_on == False` |
| T-PDM-02 | Enable PDM mode, send command at 800 s, verify timer resets | `pdm_tx_timer_s` resets to 900, PA remains on |
| T-PDM-03 | Enable PDM mode, send commands every 60 s for 30 min | PA stays on throughout (timer never expires) |
| T-PDM-04 | PDM auto-shutdown, verify EPS ttc_tx power line goes off | `shared_params[0x0520]` reflects shutdown state |
| T-PDM-05 | PDM disabled, verify PA follows normal on/off commands | `pdm_mode == False`, PA controlled by `pa_on`/`pa_off` commands |
| T-PDM-06 | PDM timer expiry event generated | Event `0x0541` emitted at timer expiry |
| T-PDM-07 | PDM timer with custom duration (e.g., 60 s) | Timer expires at configured duration |

### 10.2 Antenna Deployment Tests

| ID | Test | Assertion |
|---|---|---|
| T-ANT-01 | Initial state antenna stowed | `antenna_deployed == False`, effective gain = -3 dBi |
| T-ANT-02 | Deploy primary burn wire, tick 30+ s | `antenna_deployed == True`, `deploy_microswitch == True` |
| T-ANT-03 | Deploy primary, verify link margin improves by ~6 dB | Post-deploy `link_margin_db` > pre-deploy by 5--7 dB |
| T-ANT-04 | Inject `burn_wire_primary_fail`, deploy primary | Deployment fails, `antenna_deployed == False` |
| T-ANT-05 | Primary fails, deploy backup | `antenna_deployed == True` via backup wire |
| T-ANT-06 | Both wires failed, attempt deploy | Both deployments fail, antenna remains stowed |
| T-ANT-07 | Deploy when already deployed | Command rejected (`antenna_deployed == True`) |
| T-ANT-08 | Deploy command rejected after primary already fired | `burn_wire_primary_fired == True`, command returns failure |
| T-ANT-09 | EPS bus load during deployment | `shared_params` EPS power spike of ~56 W during burn |
| T-ANT-10 | Partial deployment failure injection | Effective gain at intermediate value (0 dBi) |

### 10.3 Beacon Mode Tests

| ID | Test | Assertion |
|---|---|---|
| T-BCN-01 | Start in beacon mode, verify 100 bps rate | `beacon_mode == True`, `tm_data_rate == 100` |
| T-BCN-02 | Beacon packet count increments every 10 s | After 30 s, `beacon_packet_count == 3` |
| T-BCN-03 | Bootloader completes after configured duration | `bootloader_active == False` after 120 s |
| T-BCN-04 | Uplink blocked during bootloader | `uplink_enabled == False` while `bootloader_active` |
| T-BCN-05 | Exit beacon mode transitions to low-rate | `tm_data_rate == 1000` after bootloader completes |
| T-BCN-06 | FDIR triggers beacon mode re-entry | `beacon_mode == True` after FDIR action |
| T-BCN-07 | Beacon mode link margin sufficient with stowed antenna | `link_margin_db > 0` at 100 bps with -3 dBi gain |
| T-BCN-08 | `exit_beacon_mode` command works after bootloader | Command accepted, `beacon_mode == False` |

### 10.4 Rate Lifecycle Tests

Implemented in `tests/test_simulator/test_tm_rate_ladder.py` (spacecraft),
`tests/test_planner/test_rate_ladder_budget.py` (planning) and
`tests/test_mcs/test_tm_rate_ladder_display.py` (console).

| ID | Test | Assertion |
|---|---|---|
| T-RATE-01 | `set_tm_rate(HIGH)` rejected before antenna deploy | S1.8 failure, error 0x0007, message names the antenna gate |
| T-RATE-02 | `set_tm_rate(HIGH)` rejected without DOWNLINK mode | S1.8 failure, error 0x0007, message names func 79 |
| T-RATE-03 | `set_tm_rate(HIGH)` rejected below 10 deg elevation | S1.8 failure, error 0x0007, message quotes the current elevation |
| T-RATE-04 | `set_tm_rate(HIGH)` accepted with all gates satisfied | `tm_data_rate == 128000`, `tm_tier == tm_tier_cmd == 2`, symbol rate 64000 Bd (QPSK) |
| T-RATE-05 | `set_tm_rate(LOW)` always accepted (downshift) | Accepted regardless of antenna, mode or elevation — LOW is the routine operations tier |
| T-RATE-06 | BEACON not selectable by func 65 in nominal software | Rejected: "bootloader/beacon-only tier" |
| T-RATE-07 | Gate closes mid-pass | ACTIVE tier demotes to LOW, COMMANDED tier retained, HIGH resumes when the gate reopens — no re-command |
| T-RATE-08 | Rung step vs margin | LOW -> HIGH costs 10.53 dB (9.03 dB rate + 1.5 dB coding); reproduced by `tools/link_budget_ladder.py` |
| T-RATE-09 | Modulation changes the SYMBOL rate, not the bit rate | BPSK 16 kbps -> 16 kBd; QPSK 128 kbps -> 64 kBd |
| T-RATE-10 | One image fits one pass at HIGH | 4 MiB / (128000 x 0.8) = 328 s < the 445 s average pass |

### 10.5 Dual Ground Station Tests

| ID | Test | Assertion |
|---|---|---|
| T-GS-01 | Pass over Svalbard uses Svalbard G/T (26 dB/K) | `active_gs_gt == 26.0`, `active_gs_name == "Svalbard"` |
| T-GS-02 | Pass over Troll uses Troll G/T (20 dB/K) | `active_gs_gt == 20.0` |
| T-GS-03 | Link margin difference between Svalbard and Troll | Svalbard margin ~6 dB higher than Troll at same geometry |
| T-GS-04 | Station handover event generated | Event emitted when active station changes |
| T-GS-05 | Operator station lock override | Operator locks to Troll, ignores higher-elevation Svalbard |
| T-GS-06 | Multiple simultaneous contacts reported | `gs_contacts` list contains >1 entry during overlap |
| T-GS-07 | No contact when no station above min elevation | `in_contact == False`, `active_gs_name == ""` |
| T-GS-08 | `active_gs_id` parameter written correctly | Numeric ID matches station index (0=Svalbard, etc.) |

### 10.6 Ground Station Failure Tests

| ID | Test | Assertion |
|---|---|---|
| T-GSF-01 | Inject antenna failure on Svalbard | Svalbard passes show `in_contact == False` |
| T-GSF-02 | Inject LNA failure, verify G/T drop | Effective G/T reduced by ~15 dB |
| T-GSF-03 | Inject uplink failure, verify TC blocked | `gs_uplink_available == 0`, TM still received |
| T-GSF-04 | Inject freq drift, verify lock delay increase | Carrier lock takes longer or fails |
| T-GSF-05 | Inject data link failure | TM not forwarded to MCS (pass recorded but not real-time) |
| T-GSF-06 | Inject power failure on station | Complete station outage, contact lost |
| T-GSF-07 | Clear GS failure, verify recovery | Station returns to operational after clear |
| T-GSF-08 | Multiple GS failures simultaneously | Only unaffected stations provide contact |
| T-GSF-09 | All stations failed | No contact possible, triggers loss-of-comm scenario |
| T-GSF-10 | GS LNA degrade with magnitude parameter | G/T reduced by specified dB amount |

### 10.7 Integration / End-to-End Tests

| ID | Test | Assertion |
|---|---|---|
| T-INT-01 | Full LEOP sequence: beacon -> low-rate -> deploy -> high-rate | All state transitions occur in correct order |
| T-INT-02 | PDM timer during LEOP with intermittent commanding | PA stays on during command activity, shuts off in gaps |
| T-INT-03 | Deploy failure with fallback to backup wire | Primary fails, backup succeeds, rates transition correctly |
| T-INT-04 | Station failover during pass | Active station fails, fallback to next station |
| T-INT-05 | Loss of communication scenario with GS failures | Matches procedure PROC-EMG-001 decision tree |
| T-INT-06 | Beacon mode re-entry from safe mode | FDIR triggers beacon, operators recover to nominal |
| T-INT-07 | All 18 new parameters written to shared_params | Parameters 0x0520--0x0531 all present after tick |

---

## 11. Implementation Priority and Dependencies

### 11.1 Dependency Graph

```
Gap 2 (Antenna Deploy) <--- Gap 4 (Rate Lifecycle) depends on antenna state
Gap 3 (Beacon Mode) <------ Gap 4 (Rate Lifecycle) depends on bootloader state
Gap 5 (Dual GS) <---------- Gap 6 (GS Failure) requires per-station state
Gap 1 (PDM Channel) is independent
```

### 11.2 Recommended Implementation Order

| Phase | Gap | Rationale | Estimated Effort |
|---|---|---|---|
| 1 | G5: Dual GS link budget | Foundation -- changes orbit propagator and TTC tick loop; required by G6 | High (touches smo-common and smo-simulator) |
| 2 | G2: Antenna deployment | Foundation -- changes link budget gain; required by G4 | Medium (TTC model only) |
| 3 | G3: Beacon mode | Foundation -- adds bootloader and beacon state; required by G4 | Medium (TTC model + TM builder) |
| 4 | G4: Rate lifecycle | Depends on G2 and G3 -- ties rate state machine to antenna and bootloader | Low (logic glue over existing rate infrastructure) |
| 5 | G1: PDM command channel | Independent -- can be done in parallel with G2/G3 | Medium (new state machine + EPS cross-coupling) |
| 6 | G6: GS equipment failure | Depends on G5 -- extends per-station state with failure flags | Low (follows existing failure injection pattern) |

### 11.3 Estimated Line Count Impact

| Component | Current Lines | Added Lines | New Total |
|---|---|---|---|
| `ttc_basic.py` | 432 | ~350 | ~780 |
| `propagator.py` | ~200 | ~50 | ~250 |
| `ttc.yaml` | 23 | ~45 | ~68 |
| `ground_stations.yaml` | 30 | ~30 | ~60 |
| `parameters.yaml` (TTC) | 26 | ~18 | ~44 |
| `test_ttc_enhanced.py` | 366 | ~500 | ~866 |

### 11.4 Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Orbit propagator multi-GS adds per-tick compute | Performance regression if many stations | Profile; compute only for stations within coarse range check |
| Antenna deployment is irreversible -- state persistence | Sim restart resets deployment state | Use `get_state`/`set_state` for checkpoint/restore |
| Beacon mode at 100 bps changes TM builder packet rate | TM builder may not handle sub-1-kbps rates | Add beacon packet type to TM builder, distinct from HK packets |
| PDM timer interacts with EPS power line control | Conflicting PA state between PDM and manual commands | Clear precedence: PDM mode overrides manual PA control when active |
| Per-station G/T changes link budget discontinuously at handover | Operators may see RSSI jumps | Model is correct -- real handover causes apparent jumps; document as expected |

---

*AI-generated content (AIG) -- source: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

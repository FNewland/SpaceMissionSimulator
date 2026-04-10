# EOSAT-1 Simulator Gap Analysis

**Document**: SMO-SIM-GAP-001
**Date**: 2026-03-12
**Purpose**: Compare the 6 simulator fidelity documents against the 9 actual simulator source files, classifying each gap by implementation type and effort.

![AIG -- Artificial Intelligence Generated](../../Screenshot%202026-03-10%20at%202.33.26%20PM.png)
Source: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Methodology](#2-methodology)
3. [Gap Classification Key](#3-gap-classification-key)
4. [EPS Gaps](#4-eps-gaps)
5. [AOCS Gaps](#5-aocs-gaps)
6. [TTC Gaps](#6-ttc-gaps)
7. [OBDH Gaps](#7-obdh-gaps)
8. [TCS Gaps](#8-tcs-gaps)
9. [Payload Gaps](#9-payload-gaps)
10. [Cross-Subsystem Dependencies](#10-cross-subsystem-dependencies)
11. [Implementation Roadmap](#11-implementation-roadmap)
12. [Summary Statistics](#12-summary-statistics)

---

## 1. Executive Summary

This document compares the 31 fidelity gaps identified across 6 simulator fidelity analysis documents against the actual simulator source code to determine:

- Which gaps have already been fully or partially addressed in the codebase
- The implementation type required for each remaining gap (config-only, code-modify, or new-feature)
- The estimated effort (S/M/L) for each gap

**Key findings:**

- **31 gaps** identified across all 6 fidelity documents
- **5 gaps** are already fully implemented in the current code (OBDH G1 partial, G2, G3, G4 -- all wired; TTC G1/G2/G3 partially)
- **3 gaps** are partially implemented and need completion
- **23 gaps** require new work
- **0 gaps** are config-only fixes
- **8 gaps** are code-modify (modify existing code paths)
- **20 gaps** are new-feature (new functionality, data structures, or subsystem coupling)
- **Total estimated effort**: 55--72 person-days

---

## 2. Methodology

### 2.1 Source Documents Compared

**Fidelity documents** (input -- what should exist):

| Document | Path | Gaps Defined |
|---|---|---|
| EPS Fidelity | `docs/sim_fidelity/eps_fidelity.md` | 5 gaps (G1--G5) |
| AOCS Fidelity | `docs/sim_fidelity/aocs_fidelity.md` | 6 gaps (G1--G6) |
| TTC Fidelity | `docs/sim_fidelity/ttc_fidelity.md` | 6 gaps (G1--G6) |
| OBDH Fidelity | `docs/sim_fidelity/obdh_fidelity.md` | 5 gaps (G1--G5) |
| TCS Fidelity | `docs/sim_fidelity/tcs_fidelity.md` | 5 gaps (G1--G5) |
| Payload Fidelity | `docs/sim_fidelity/payload_fidelity.md` | 4 gaps (G1--G4) |

**Source files analyzed** (actual -- what exists today):

| File | Path | Lines |
|---|---|---|
| Engine | `packages/smo-simulator/src/smo_simulator/engine.py` | 801 |
| Service Dispatch | `packages/smo-simulator/src/smo_simulator/service_dispatch.py` | 709 |
| TM Storage | `packages/smo-simulator/src/smo_simulator/tm_storage.py` | 157 |
| EPS Model | `packages/smo-simulator/src/smo_simulator/models/eps_basic.py` | 475 |
| AOCS Model | `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py` | 986 |
| TTC Model | `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py` | 483 |
| OBDH Model | `packages/smo-simulator/src/smo_simulator/models/obdh_basic.py` | 505 |
| TCS Model | `packages/smo-simulator/src/smo_simulator/models/tcs_basic.py` | 298 |
| Payload Model | `packages/smo-simulator/src/smo_simulator/models/payload_basic.py` | 460 |

### 2.2 Analysis Method

For each gap in each fidelity document:

1. Read the fidelity document's description of the target state.
2. Search the corresponding source file(s) for any existing implementation.
3. Compare the existing implementation (if any) against the target.
4. Classify the remaining work as config-only, code-modify, or new-feature.
5. Estimate the effort as S (1--2 days), M (3--5 days), or L (6+ days).

---

## 3. Gap Classification Key

| Classification | Definition | Examples |
|---|---|---|
| **config-only** | Gap can be closed by modifying YAML configuration files only (parameters, HK structures, FDIR rules). No Python code changes. | Adding a missing parameter to `parameters.yaml`, adjusting a threshold in `fdir.yaml` |
| **code-modify** | Gap requires modifying existing Python code paths (adding logic to existing methods, extending existing data structures). The architectural framework already exists. | Adding edge detection to an existing `check_monitoring()` method, wiring an existing method into the tick loop |
| **new-feature** | Gap requires new Python classes, data structures, algorithms, or subsystem coupling that does not exist today. May also require new config schemas. | 6-face solar array model, per-station link budget, multispectral band configuration |

| Effort | Definition |
|---|---|
| **S** (Small) | 1--2 person-days. Localized change, few files, minimal testing risk. |
| **M** (Medium) | 3--5 person-days. Multiple files, new data structures, moderate testing. |
| **L** (Large) | 6+ person-days. Architectural change, cross-subsystem, extensive testing. |

---

## 4. EPS Gaps

**Source file**: `packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (475 lines)

### EPS-G1: 6 Body-Panel Solar Array Model

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/eps_fidelity.md` Section 3 |
| **Priority** | Critical |
| **Classification** | **new-feature** |
| **Effort** | **L** (6--8 days) |

**Current state in code**: The EPS model uses a 2-wing solar array model with a single beta-angle cosine projection. SA power is computed as `panel_area * cell_eff * solar_irradiance * cos(beta)`, split 50/50 between two arrays (A and B). There is no concept of spacecraft attitude influencing power generation per face.

**What exists**: `sa_a_current`, `sa_b_current`, `sa_a_voltage`, `sa_b_voltage` telemetry; per-wing enable/disable; MPPT efficiency multiplier; SA aging model.

**What is missing**: Per-face `SAFace` data structure with body-frame normal vectors; quaternion rotation of sun vector to body frame; per-face illumination fraction computation; temperature coupling from TCS panel temperatures (0x0400--0x0405); 24+ new telemetry parameters (per-face power, current, illumination, temperature).

**Dependencies**: Requires AOCS attitude quaternion in `shared_params` (already published at 0x0200--0x0203). Benefits from TCS panel temperatures (already published at 0x0400--0x0405). Should share the quaternion rotation utility with TCS (Gap TCS-G2).

---

### EPS-G2: Cold-Redundant PDM with Unswitchable Lines

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/eps_fidelity.md` Section 4 |
| **Priority** | Critical |
| **Classification** | **new-feature** |
| **Effort** | **L** (6--8 days) |

**Current state in code**: Power distribution is a flat list of 8 `POWER_LINE_DEFS` tuples with per-line power draw, on/off state, and a `switchable` flag. OBC and TTC RX are marked as non-switchable. There is no PDM unit model, no redundancy, no cross-strapping.

**What exists**: Per-line current computation; overcurrent protection with trip flags; UV/OV detection flags; power line on/off commands; `reset_oc_flag` command.

**What is missing**: `PDMUnit` data structure (A/B); per-PDM switch state tracking; essential vs. switched line classification with PDM assignment; PDM switchover command with default-state restoration; cross-strap relay command; PDM-level telemetry (temperature, input current, fault flags); ~14 new telemetry parameters.

**Dependencies**: None (self-contained within EPS). Should be implemented before EPS-G5 (switchover logic depends on dual PDMs) and EPS-G3 (separation timer enables PDM main bus).

---

### EPS-G3: Separation Timer Circuit

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/eps_fidelity.md` Section 5 |
| **Priority** | High |
| **Classification** | **new-feature** |
| **Effort** | **M** (3--4 days) |

**Current state in code**: The EPS model starts in a fully powered steady state. There is no separation event, no inhibit removal, no timer, no essential-bus-only period.

**What exists**: Nothing related to separation sequence.

**What is missing**: `EPSSepState` enumeration (PRE_SEPARATION, SEPARATED_TIMER, TIMER_EXPIRED); separation timer countdown (30-minute hardware timer); essential-bus-only power during timer period; PDM main bus enable on timer expiry; `simulate_separation` and `skip_sep_timer` commands; ~5 new telemetry parameters; YAML config for timer duration and essential line list.

**Dependencies**: Depends on EPS-G2 (PDM architecture) because the separation timer enables the PDM main bus.

---

### EPS-G4: Per-Cell Solar Panel Degradation

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/eps_fidelity.md` Section 6 |
| **Priority** | Medium |
| **Classification** | **new-feature** |
| **Effort** | **M** (4--5 days) |

**Current state in code**: SA degradation is per-array (A or B) via a scalar `degradation_a/b` factor applied by `solar_array_partial` failure injection. There is also a global `sa_age_factor` that degrades at 3.14e-6 per sunlit hour. No per-cell or per-string granularity.

**What exists**: Per-array degradation injection; global aging model.

**What is missing**: `CellString` data structure per face (with `failed_cells`, `shorted_cells`, `open_circuit`, `degradation` fields); string-level power calculation (voltage fraction from effective cells, current fraction from degradation); bypass diode modeling; per-cell failure injection (`cell_fail`, `cell_short`, `string_open`); radiation degradation per string; ~6 new telemetry parameters.

**Dependencies**: Depends on EPS-G1 (6-face SA model) because the per-cell model builds on the per-face structure.

---

### EPS-G5: Switchover and Undercurrent Detection

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/eps_fidelity.md` Section 7 |
| **Priority** | High |
| **Classification** | **new-feature** |
| **Effort** | **M** (3--4 days) |

**Current state in code**: There is overcurrent detection with per-line thresholds, trip action, and bitmask flag. There is no undercurrent detection and no autonomous PDM switchover logic. Load shedding constants are defined (`LOAD_SHED_ORDER`, `LOAD_SHED_VOLTAGE = 26.5V`) but the tick() method never calls load-shedding logic.

**What exists**: Overcurrent protection framework (thresholds, trip flags, reset command).

**What is missing**: `UndercurrentState` with per-line counters and persistence threshold; undercurrent detection logic in tick() (current below 10% of nominal for N consecutive ticks); `SwitchoverState` with fault persistence counter; autonomous switchover trigger on sustained UV or multiple OC trips; `enable_auto_switchover`, `force_switchover`, `reset_uc_flag` commands; ~4 new telemetry parameters.

**Dependencies**: Depends on EPS-G2 (PDM architecture) because switchover requires dual PDMs. Also depends on the overcurrent framework which already exists.

---

## 5. AOCS Gaps

**Source file**: `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py` (986 lines)

### AOCS-G1: Dual Redundant Magnetometers

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/aocs_fidelity.md` Section 3 |
| **Priority** | P1 (High) |
| **Classification** | **new-feature** |
| **Effort** | **M** (3--4 days) |

**Current state in code**: Single magnetometer with 3-axis measurement (`mag_x`, `mag_y`, `mag_z`), single `mag_failed` flag, single `mag_valid` flag. Sinusoidal field model driven by orbit phase with 50 nT Gaussian noise per axis.

**What exists**: Magnetometer tick method (`_tick_magnetometer`); failure injection/clearing for `mag_failure`; `mag_field_total` computation.

**What is missing**: Dual unit state fields (`mag_a_x/y/z`, `mag_b_x/y/z`, `mag_a_valid`, `mag_b_valid`, `mag_a_failed`, `mag_b_failed`, `mag_a_temp`, `mag_b_temp`); per-unit bias vectors, noise sigma, and scale factors; `mag_selected` field (1=A, 2=B); composite output logic that follows the selected unit; temperature-dependent noise model; independent failure injection per unit; ~11 new telemetry parameters.

**Dependencies**: Coupled with AOCS-G2 (mag select command).

---

### AOCS-G2: Magnetometer Source Select Command

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/aocs_fidelity.md` Section 4 |
| **Priority** | P1 (High) |
| **Classification** | **code-modify** |
| **Effort** | **S** (1 day) |

**Current state in code**: The `mag_select` command in `aocs_basic.py` (lines 807--813) is a simple on/off toggle: `self._state.mag_valid = on`. The service dispatch (`service_dispatch.py` lines 262--264) passes a single byte interpreted as boolean on/off for S8 func_id 7.

**What exists**: Command routing infrastructure; S8 func_id 7 mapping.

**What is missing**: Command semantics change from `on: bool` to `unit: int` (1=A, 2=B); validation that the selected unit is not failed; backward-compatible dual-mode command acceptance (support both `on` and `unit` parameters); service dispatch data byte reinterpretation; PUS_FIELD_DEFS update in MCS UI.

**Dependencies**: Depends on AOCS-G1 (dual magnetometer state must exist before the select command can reference units).

---

### AOCS-G3: Individual CSS Heads with Geometric Projection

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/aocs_fidelity.md` Section 5 |
| **Priority** | P2 (Medium-High) |
| **Classification** | **new-feature** |
| **Effort** | **M** (3--4 days) |

**Current state in code**: The CSS model (`_tick_css`, lines 326--357) produces a composite 3-axis sun vector directly from orbit geometry (beta angle, orbit phase) with Gaussian noise. There is no concept of individual photodiode heads, face normals, or cosine-law projection.

**What exists**: Composite `css_sun_x/y/z` and `css_valid` outputs; eclipse gating; single `css_failed` flag.

**What is missing**: 6 individual head state fields (`css_head_px/mx/py/my/pz/mz`); per-head failure flags; face normal definitions; cosine-law projection from sun vector in body frame to each head; per-head bias and noise; composite sun vector reconstruction from 6 head readings; validity threshold (minimum 3 illuminated heads for 3D vector); ~6 new telemetry parameters.

**Dependencies**: Requires sun vector in body frame from attitude quaternion (same transformation needed by EPS-G1 and TCS-G2). Share the quaternion rotation utility.

---

### AOCS-G4: Star Tracker Zenith/Nadir FOV Geometry

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/aocs_fidelity.md` Section 6 |
| **Priority** | P2 (Medium) |
| **Classification** | **code-modify** |
| **Effort** | **M** (3--4 days) |

**Current state in code**: Star tracker blinding is purely probabilistic. Lines 271--324 of `aocs_basic.py` check: if not in eclipse and `beta < 5.0`, then `random.random() < 0.3` determines blinding. No boresight direction, no exclusion cone angle, no sun/earth geometry.

**What exists**: 2 independent ST units with boot time, status tracking (OFF/BOOTING/TRACKING/BLIND/FAILED), primary selection, failure injection.

**What is missing**: Per-unit boresight vectors (ST1: +Z zenith, ST2: -Z nadir); 15-degree half-cone exclusion angle; geometric sun blinding check (angle between sun direction and boresight); earth limb exclusion for nadir-pointing tracker; deterministic blinding with hysteresis at boundary; ST sun angle and earth angle diagnostic telemetry; ~4 new telemetry parameters.

**Dependencies**: Requires sun vector in body frame from attitude quaternion. Same transformation as AOCS-G3, EPS-G1, TCS-G2.

---

### AOCS-G5: Actuator Power-Reset Recovery

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/aocs_fidelity.md` Section 7 |
| **Priority** | P1 (Medium-High) |
| **Classification** | **new-feature** |
| **Effort** | **M** (3--4 days) |

**Current state in code**: The AOCS model does not read EPS power-line state from `shared_params`. The service dispatch (`service_dispatch.py` lines 703--705) checks the AOCS wheels power line before allowing S8 AOCS commands, but the AOCS model itself continues simulating wheel operation when power is off. There is no boot/recovery sequence after power restoration.

**What exists**: Service dispatch command gating on power line state; reaction wheel disable/enable commands; ST boot time model (60 seconds).

**What is missing**: `_check_power_state()` method reading `shared_params[0x0117]` (EPS AOCS wheels power line); power-off response (disable all wheels, set STs to OFF, disable MTQ, transition to MODE_OFF); power-on recovery state machine (5s self-test, then MODE_SAFE_BOOT); `aocs_powered`, `power_on_timer`, `rw_self_test_complete` state fields; FDIR interaction (suppress attitude error checks during recovery); ~2 new telemetry parameters.

**Dependencies**: Cross-subsystem coupling with EPS via `shared_params[0x0117]`. The EPS model already publishes this parameter.

---

### AOCS-G6: ADCS Commissioning Sequence Support

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/aocs_fidelity.md` Section 8 |
| **Priority** | P2 (Medium) |
| **Classification** | **new-feature** |
| **Effort** | **M** (3--4 days) |

**Current state in code**: No calibration state machines exist. The commissioning procedures (COM-005/006/007) reference parameter IDs that collide with existing telemetry (e.g., 0x0262 is `aocs.submode`, 0x0270 is `gyro_bias_x`). There is no gyro calibration timer, no mag calibration residual, no wheel commanded speed target.

**What exists**: Gyro bias tracking (`_gyro_bias` with random walk drift); commissioning procedure files reference AOCS commands.

**What is missing**: Gyro calibration state machine (300s accumulation, bias estimation); mag calibration state machine (600s, residual computation); dedicated commissioning parameter block (0x02A0--0x02A4) to avoid ID collisions; `gyro_cal_cmd`, `gyro_cal_status`, `mag_cal_cmd`, `mag_cal_status`, `mag_cal_residual` state fields and parameters; ~5 new telemetry parameters.

**Dependencies**: AOCS-G1 (dual magnetometers) would make mag calibration more meaningful, but this gap can be implemented independently.

---

## 6. TTC Gaps

**Source file**: `packages/smo-simulator/src/smo_simulator/models/ttc_basic.py` (483 lines)

### TTC-G1: Dedicated PDM Command Channel with 15-Minute Timer

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/ttc_fidelity.md` Section 3 |
| **Priority** | Critical |
| **Classification** | **code-modify** |
| **Effort** | **M** (3--4 days) |

**Current state in code**: The TTC model has partial infrastructure for this gap. `TTCState` includes `cmd_channel_active` and `cmd_decode_timer` fields (found in the state dataclass). A `cmd_channel_start` command exists. However, the implementation is simplified: there is no 15-minute countdown timer linked to command reception, no autonomous PA shutdown on timer expiry, no cross-subsystem EPS coupling for TX power line, and no event generation.

**What exists**: `cmd_channel_active` state field; `cmd_decode_timer` state field; basic `cmd_channel_start` command handler; `cmd_rx_count` counter; PA on/off commands.

**What is missing**: Full PDM timer logic in tick() (countdown from 900s, reset on command decode, PA shutdown on expiry); `pdm_mode`, `pdm_auto_shutdown` state fields; EPS cross-coupling (write shared_params flag to disable ttc_tx power line on timer expiry); `pdm_enable`, `pdm_disable`, `pdm_set_timer` commands; event generation (timer started, expired, reset); ~3 new telemetry parameters.

**Dependencies**: Cross-subsystem with EPS (TTX power line). Independent of other TTC gaps.

---

### TTC-G2: Burn Wire Antenna Deployment

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/ttc_fidelity.md` Section 4 |
| **Priority** | Critical |
| **Classification** | **code-modify** |
| **Effort** | **M** (3--4 days) |

**Current state in code**: `TTCState` includes an `antenna_deployed` field and a `deploy_antennas` command handler. However, the implementation is a simple boolean toggle with no burn wire mechanism, no activation timer, no EPS power draw, no redundant circuits, no deployment confirmation sensor, and no link margin impact.

**What exists**: `antenna_deployed` state field; basic `deploy_antennas` command; fixed `sc_gain_dbi` configuration.

**What is missing**: Burn wire activation sequence (30s timer, 56W EPS load); primary and backup wire circuits with independent failure flags; deployment confirmation microswitch; stowed vs. deployed antenna gain (dynamic `effective_gain` in link budget); `antenna_deploy_in_progress`, `burn_wire_primary_fired/failed`, `burn_wire_backup_fired/failed`, `deploy_microswitch` state fields; separate `deploy_antenna_primary` and `deploy_antenna_backup` commands; irreversibility enforcement; `partial_deployment` failure injection; ~5 new telemetry parameters.

**Dependencies**: Cross-subsystem with EPS (burn wire power draw). Links to TTC-G4 (rate lifecycle depends on antenna deployment state).

---

### TTC-G3: Beacon Packet Mode (Bootloader)

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/ttc_fidelity.md` Section 5 |
| **Priority** | High |
| **Classification** | **code-modify** |
| **Effort** | **M** (3--4 days) |

**Current state in code**: `TTCState` includes a `beacon_mode` field and a `set_beacon_mode` command handler. However, the implementation is a simple boolean with no beacon packet content, no 100 bps rate, no 10-second interval, no bootloader timer, and no uplink blocking during beacon mode.

**What exists**: `beacon_mode` state field; basic `set_beacon_mode` command.

**What is missing**: Beacon packet content definition (spacecraft ID, battery voltage, OBC temp, transponder status, mission elapsed time); 100 bps beacon data rate; 10-second beacon interval timer; bootloader duration timer (120s); uplink blocking during bootloader; `beacon_interval_s`, `beacon_rate_bps`, `beacon_packet_count`, `bootloader_active`, `bootloader_timer_s`, `uplink_enabled` state fields; `enter_beacon_mode` and `exit_beacon_mode` commands; FDIR integration for beacon mode re-entry; ~6 new telemetry parameters.

**Dependencies**: Links to TTC-G4 (rate lifecycle uses beacon mode as first state). Links to OBDH bootloader (OBDH-G1) for coordinated bootloader behavior.

---

### TTC-G4: Low-Rate Pre-Deployment / High-Rate Post-Deployment Link

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/ttc_fidelity.md` Section 6 |
| **Priority** | High |
| **Classification** | **code-modify** |
| **Effort** | **S** (1--2 days) |

**Current state in code**: `tm_data_rate` is set to `tm_rate_hi` (64000) at configure time. The `set_tm_rate` command accepts `hi` or `lo` rate without any enforcement based on antenna deployment state. The link budget math already uses `tm_data_rate` in the noise bandwidth calculation, so the physics is correct; only the state management and enforcement are missing.

**What exists**: `tm_data_rate` field; `set_tm_rate` command; `tm_rate_hi` and `tm_rate_lo` configuration; link budget noise bandwidth calculation.

**What is missing**: Rate state machine (BEACON -> LOW_RATE -> HIGH_RATE); enforcement that high rate requires deployed antenna; automatic rate switch on deployment; `data_rate_state` field; rejection of `set_tm_rate(64000)` when antenna not deployed; ~1 new telemetry parameter.

**Dependencies**: Depends on TTC-G2 (antenna deployment state) and TTC-G3 (beacon mode state).

---

### TTC-G5: Dual Ground Station with Per-Station Link Budget

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/ttc_fidelity.md` Section 7 |
| **Priority** | Critical |
| **Classification** | **new-feature** |
| **Effort** | **L** (6--8 days) |

**Current state in code**: The orbit propagator uses a single `_primary_gs` for contact calculations. `gs_g_t_db` is a single fixed value (20.0 dB/K) in the TTC config. `OrbitState` has single-valued contact geometry (elevation, azimuth, range, in_contact) with no station identifier. There is no concept of station handover or multiple simultaneous contacts.

**What exists**: `OrbitPropagator` with ground station list; `GroundStation` objects with lat/lon/alt/min_elevation; single-station contact geometry in `OrbitState`; link budget calculation using fixed G/T.

**What is missing**: Multi-station contact computation in orbit propagator (look angles for ALL stations per tick); per-station G/T, uplink EIRP, system noise temperature from config; `gs_name`, `gs_contacts` fields in `OrbitState`; TTC model reading active station G/T from orbit state; station selection policy (highest elevation, operator override); handover event generation; `active_gs_name`, `active_gs_gt_db`, `gs_handover_count` state fields; ground station config extension (G/T, EIRP, noise temp per station); changes to both `smo-common` (propagator) and `smo-simulator` (TTC model); ~4 new telemetry parameters.

**Dependencies**: Changes to `smo-common/orbit/propagator.py` (cross-package). Must be implemented before TTC-G6.

---

### TTC-G6: Ground Station Equipment Failure Injection

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/ttc_fidelity.md` Section 8 |
| **Priority** | Medium |
| **Classification** | **new-feature** |
| **Effort** | **M** (3--4 days) |

**Current state in code**: All 6 existing failure modes are spacecraft-side only (primary/redundant transponder failure, high BER, PA overheat, uplink loss, receiver degrade). No concept of ground station health or capability degradation.

**What exists**: Failure injection framework with `inject_failure()` and `clear_failure()` methods.

**What is missing**: `GroundStationState` data structure per station (operational, antenna_tracking, lna_functional, lna_degrade_db, uplink_available, freq_standard_drift_hz, data_link_up); `inject_gs_failure(station_name, failure, magnitude)` method; tick() integration (check active station health, apply G/T degradation, block uplink if station uplink failed, block TM forwarding if data link failed); ~4 new telemetry parameters.

**Dependencies**: Depends on TTC-G5 (per-station state model).

---

## 7. OBDH Gaps

**Source files**: `packages/smo-simulator/src/smo_simulator/models/obdh_basic.py` (505 lines), `packages/smo-simulator/src/smo_simulator/engine.py` (801 lines), `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (709 lines), `packages/smo-simulator/src/smo_simulator/tm_storage.py` (157 lines)

### OBDH-G1: Bootloader State Machine

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/obdh_fidelity.md` Section 3 |
| **Priority** | Critical |
| **Classification** | **code-modify** (partially implemented) |
| **Effort** | **S** (1--2 days) |

**Current state in code -- PARTIALLY IMPLEMENTED**: The engine already has significant bootloader infrastructure:

- **HK gating**: `engine.py` `_emit_hk_packets()` (lines 474--479) reads `shared_params[0x0311]` (sw_image) and gates HK emission to SID 10/11 only when `sw_image == 0` (bootloader mode). Other SIDs are suppressed in bootloader.
- **TC rejection**: `engine.py` `_dispatch_tc()` (lines 399--414) checks `sw_image` and rejects TCs that are not in the allowed set when in bootloader mode. Allowed: S17.1, S9.1, and S8.1 with func_ids in `{42, 43, 44, 45, 46, 47}`.
- **Dispatcher persistence**: `engine.py` line 135 creates a persistent `self._dispatcher = ServiceDispatcher(self)`.
- **OBDH model**: Has `sw_image` field, `_reboot()` drops to bootloader, 10s CRC timer, `boot_inhibit` flag.

**What remains**: Verify the HK gating suppresses SIDs 1--6 (not just 10/11 pass-through). The current code appears to allow SID 10 and SID 11; it should suppress ALL SIDs except 10 in bootloader. Verify TC rejection error code matches 0x0006 as specified. Add `is_bootloader` property to OBDH model. Add configurable `bootloader_beacon_sid` and `bootloader_allowed_func_ids` to `obdh.yaml`.

**Dependencies**: None. This is the highest-priority OBDH gap.

---

### OBDH-G2: Circular HK Buffer

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/obdh_fidelity.md` Section 4 |
| **Priority** | High |
| **Classification** | **code-modify** (already implemented) |
| **Effort** | **S** (0.5--1 day, verification only) |

**Current state in code -- ALREADY IMPLEMENTED**: `tm_storage.py` already implements circular buffer support for store 1 (HK_Store). The implementation uses a list with `pop(0)` for circular mode and tracks `wrap_count`. When the store reaches capacity and the mode is circular, the oldest packet is removed before appending the new one.

**What remains**: Verify the circular buffer behavior matches the fidelity document's requirements:
1. Confirm `store_packet_direct()` for store 1 never returns False (circular never rejects).
2. Confirm `is_overflow()` returns False for circular stores.
3. Add `is_wrapped()` method if not present.
4. Align OBDH model's `hktm_buf_fill` counter with actual store fill level.
5. Consider replacing `list` with `collections.deque(maxlen=capacity)` for O(1) operations.

**Dependencies**: None.

---

### OBDH-G3: S12 Monitoring Enforcement Per Tick

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/obdh_fidelity.md` Section 5 |
| **Priority** | High |
| **Classification** | **code-modify** (already wired) |
| **Effort** | **S** (1--2 days) |

**Current state in code -- ALREADY WIRED**: The engine's `_run_loop()` calls `self._tick_s12_monitoring()` at line 231. The `ServiceDispatcher` has a complete `check_monitoring()` method that iterates `_s12_definitions` and checks parameter values against limits. The dispatcher is persistent (`self._dispatcher` at line 135).

**What remains**:
1. Add **edge detection** to `check_monitoring()` -- currently reports violations every tick, not transitions only. Need `_in_violation` flag per definition.
2. Add **rate limiting** (configurable check interval, e.g., every 4 seconds instead of every tick).
3. Add **bootloader suspension** (skip S12 checks when `sw_image == 0`).
4. Verify event ID format for S12 violations (should be `0x9000 | (param_id & 0x0FFF)`).
5. Add `s12_check_interval_s` config parameter.

**Dependencies**: Dispatcher persistence (already implemented).

---

### OBDH-G4: S19 Event-Action Trigger Wiring

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/obdh_fidelity.md` Section 6 |
| **Priority** | High |
| **Classification** | **code-modify** (already wired) |
| **Effort** | **S** (1--2 days) |

**Current state in code -- ALREADY WIRED**: The engine's `_emit_event()` method at line 535 calls `self._dispatcher.trigger_event_action(severity)`. The `ServiceDispatcher` has a complete `trigger_event_action()` method that iterates `_s19_definitions`, matches `event_type`, and executes `_handle_s8()`.

**What remains**:
1. Enhance matching to support **event_id-based matching** in addition to severity-based (currently only matches on `event_type` which is severity).
2. Add **re-entrancy guard** (`_s19_in_progress` flag) to prevent infinite recursion.
3. Add **bootloader suspension** (skip S19 triggers when `sw_image == 0`).
4. Pass `event_id` from `_emit_event()` to `trigger_event_action()`.

**Dependencies**: Dispatcher persistence (already implemented).

---

### OBDH-G5: Memory Operations Beyond Stubs

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/obdh_fidelity.md` Section 7 |
| **Priority** | Medium |
| **Classification** | **new-feature** |
| **Effort** | **M** (4--5 days) |

**Current state in code**: The S6 handler in `service_dispatch.py` is stub-only:
- `MEM_LOAD` (S6.2): Logs address and data, discards payload.
- `MEM_DUMP` (S6.5): Returns address + zero bytes.
- `MEM_CHECK` (S6.9): Returns hardcoded checksum `0xABCD`.

The project already has a memory map configuration at `configs/eosat1/subsystems/memory_map.yaml` defining 11 regions with addresses, sizes, and types (readonly, flash, ram).

**What exists**: S6 TC handler routing; memory map config file; S6 response packet building.

**What is missing**: `SimulatedMemory` class with region-aware address space; `load(address, data)` method with read-only rejection; `dump(address, length)` method returning actual stored content; `checksum(address, length)` method computing CRC-16-CCITT; engine integration (`self._sim_memory` attribute); memory map loader; region boundary handling; dump size cap (256 bytes per packet); ~2 new telemetry parameters (mem_load_count, mem_dump_count).

**Dependencies**: None (self-contained enhancement to S6 handler).

---

## 8. TCS Gaps

**Source file**: `packages/smo-simulator/src/smo_simulator/models/tcs_basic.py` (298 lines)

### TCS-G1: Battery-Heater-Only Active Thermal Control

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/tcs_fidelity.md` Section 3.1 |
| **Priority** | High |
| **Classification** | **code-modify** |
| **Effort** | **S** (1--2 days) |

**Current state in code**: Three thermostat-controlled heaters exist (battery: 6W, OBC: 4W, thruster: 8W). All three have identical control logic via `_thermostat_control()`. The OBC temperature equation includes `obc_internal_heat_w` as an internal dissipation term. All three have manual mode, setpoint commands, stuck-on failure, and cannot-turn-on failure.

**What exists**: Full thermostat control infrastructure for 3 circuits; heater commands; failure injection.

**What is missing (removal/simplification)**: Remove OBC and thruster heaters from active control; make OBC and thruster temperature equations purely passive; reject `heater` commands for circuits other than battery; remove `htr_obc`, `htr_thruster` state fields and telemetry; update EPS `POWER_LINE_DEFS` to disable/remove `htr_obc` line; scope `heater_stuck_on` and `heater_failure` to battery only.

**Dependencies**: Impacts EPS power line list (parameter index stability concern). Recommendation from fidelity doc: set `htr_obc` line to "not installed" rather than removing it, to preserve index stability.

---

### TCS-G2: 6-Face Panel Temperature Coupling to Solar Illumination

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/tcs_fidelity.md` Section 3.2 |
| **Priority** | Critical |
| **Classification** | **new-feature** |
| **Effort** | **L** (6--8 days) |

**Current state in code**: All six panel temperatures use the same `env_ext` value with hardcoded offsets (+5, 0, +8, 0, -5, -5). The only orbital parameter used is `solar_beta_deg` to compute a single `env_ext` scalar. There is no relationship between spacecraft attitude and which faces are sunlit.

In eclipse: `env_ext = -30.0, env_int = 10.0`
In sunlight: `env_ext = -10.0 + 50.0 * |cos(beta)|, env_int = 12.0`

Panel temperature update: `T += (env - T) / tau * dt + noise(0, 0.05)`

**What exists**: 6 panel temperature state fields; per-zone time constants and capacitances; configurable zone parameters.

**What is missing**: `_compute_face_illumination()` method reading attitude quaternion from `shared_params[0x0200--0x0203]` and sun vector from `orbit_state.sun_eci`; quaternion rotation utility (`quat_rotate_inv`); per-face solar heat input computation (`Q_solar = S * A * alpha * illum`); Earth albedo and IR terms; radiative heat loss (linearized Stefan-Boltzmann); face geometry configuration (area, absorptivity, emissivity per face); execution order verification (AOCS before TCS); ~7 new telemetry parameters (6 illumination fractions + internal environment temp).

**Dependencies**: Requires AOCS attitude quaternion in `shared_params` (already published). Should share quaternion rotation utility with EPS-G1 and AOCS-G3/G4. Subsystem tick ordering should be verified (AOCS before TCS).

---

### TCS-G3: Heater Stuck-On Failure Mode Refinement

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/tcs_fidelity.md` Section 3.3 |
| **Priority** | Medium |
| **Classification** | **code-modify** |
| **Effort** | **S** (0.5--1 day) |

**Current state in code**: `heater_stuck_on` failure injection already exists and works correctly. When stuck-on, `_thermostat_control()` forces the heater ON regardless of temperature. The mechanism applies to all three circuits.

**What exists**: Full stuck-on implementation for all 3 circuits.

**What remains**: After TCS-G1 (battery-heater-only), scope stuck-on to battery only (remove OBC/thruster stuck-on state fields). Add overtemperature event generation when battery temperature exceeds configurable threshold (e.g., 35 degC) while heater is stuck on. Add `overtemp_event_threshold_c` and `overtemp_event_id` config parameters.

**Dependencies**: Depends on TCS-G1 (battery-heater-only architecture).

---

### TCS-G4: Heater Cannot-Turn-On Failure Mode Refinement

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/tcs_fidelity.md` Section 3.4 |
| **Priority** | Medium-High |
| **Classification** | **code-modify** |
| **Effort** | **S** (1--2 days) |

**Current state in code**: `heater_failure` exists and forces the heater OFF. The `handle_command("heater")` returns `{"success": False, "message": "Heater failed"}` when attempting to turn on a failed heater. This is unrealistic -- a real spacecraft would accept the command without knowing the relay has failed.

**What exists**: Heater failure flag; command rejection on failure.

**What is missing**: Two failure sub-modes via `htr_battery_fail_mode` field:
- Mode 1 (silent relay failure): Command accepted, TM shows ON, but no heat applied.
- Mode 2 (feedback relay failure): Command accepted, TM shows OFF (relay detects open circuit), no heat applied.

Changes needed: `handle_command("heater")` must always return `{"success": True}` regardless of failure mode; tick() heat application must check `fail_mode == 0`; telemetry output must reflect fail mode (mode 2 always reports OFF). `inject_failure("heater_cannot_turn_on", mode=1|2)`.

**Dependencies**: Depends on TCS-G1 (battery-heater-only).

---

### TCS-G5: Passive Thermal Control via Orientation

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/tcs_fidelity.md` Section 3.5 |
| **Priority** | High |
| **Classification** | **new-feature** |
| **Effort** | **M** (3--4 days) |

**Current state in code**: Internal zones (OBC, battery, FPA, thruster) use a fixed `env_int` value (10.0 degC in eclipse, 12.0 degC in sunlight). There is no coupling between panel temperatures and internal zone temperatures.

**What exists**: Internal zone temperature equations with `env_int` parameter; panel temperature state fields.

**What is missing**: `_compute_internal_environment()` method that computes area-weighted average of 6 panel temperatures; per-zone offset (e.g., thruster -3 degC from average); configurable panel-to-interior coupling weights; replacement of fixed `env_int` with computed value in battery, OBC, FPA, and thruster temperature equations; `env_internal` telemetry parameter (0x0418); `internal_coupling` config section with panel weights and zone offsets.

**Dependencies**: Depends on TCS-G2 (6-face illumination) because passive thermal control is only meaningful when panel temperatures respond to attitude.

---

## 9. Payload Gaps

**Source file**: `packages/smo-simulator/src/smo_simulator/models/payload_basic.py` (460 lines)

### PLI-G1: Multispectral Band Configuration

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/payload_fidelity.md` Section 3 |
| **Priority** | Critical |
| **Classification** | **new-feature** |
| **Effort** | **L** (6--8 days) |

**Current state in code**: The payload model is a monochromatic, single-band imager. SNR is a single value computed from FPA temperature and degradation state. There is no spectral band concept. `compression_ratio` is a fixed 2.0. `image_size_mb` is a fixed 800.0 MB.

**What exists**: Single SNR model; FPA temperature-dependent SNR factor; SNR telemetry (0x0616).

**What is missing**: `SpectralBand` data structure (band_id, center_nm, bandwidth_nm, snr_nominal, gain, dark_current, pixels_cross_track, enabled); 4-band ocean-color configuration (blue 443nm, green 560nm, red 665nm, NIR 865nm); per-band SNR computation with temperature-dependent dark current; per-band data rate contribution; aggregate SNR as worst-band; band enable/disable commands; per-band failure injection (`band_failure`, `detector_hot_pixel`); dynamic image size from band count and pixel count; `spectral_bands` config section; ~6 new telemetry parameters.

**Dependencies**: None (self-contained within payload model).

---

### PLI-G2: Image Quality Coupling to Attitude Error

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/payload_fidelity.md` Section 4 |
| **Priority** | Critical |
| **Classification** | **new-feature** |
| **Effort** | **S** (1--2 days) |

**Current state in code**: Image quality is purely failure-state driven. The `capture` command assigns quality based on `corrupt_remaining`, `ccd_line_dropout`, and `fpa_degraded` flags. There is no reading of AOCS attitude error from `shared_params`. SNR does not vary with pointing accuracy.

**What exists**: Quality assignment in capture command; SNR telemetry.

**What is missing**: Read `shared_params[0x0217]` (AOCS `att_error`) in tick(); compute `att_quality_factor` from piecewise linear function (1.0 below 0.1 deg, degrading to 0.0 above 2.0 deg); apply factor to both continuous SNR telemetry and capture quality; `att_quality_factor` and `att_error_deg` state fields; event generation on quality threshold crossings (0.5 deg warning, 1.0 deg alarm); ~2 new telemetry parameters.

**Dependencies**: Requires AOCS `att_error` in `shared_params[0x0217]` (already published). Subsystem iteration order must have AOCS before payload (confirmed: alphabetical order aocs < payload).

---

### PLI-G3: Swath Width from Altitude

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/payload_fidelity.md` Section 5 |
| **Priority** | High |
| **Classification** | **code-modify** |
| **Effort** | **S** (1 day) |

**Current state in code**: `swath_width_km` is a fixed 30.0 in `PayloadState`. It is written to `shared_params[0x0619]` as a constant. There is no reading of orbital altitude.

**What exists**: `swath_width_km` state field; telemetry parameter 0x0619.

**What is missing**: Read `orbit_state.alt_km` in tick(); configure IFOV (20 urad) and pixels (5000) from optics config section; compute `gsd_m = alt_km * 1000 * ifov_rad`; compute `swath_km = alt_km * fov_rad` where `fov_rad = pixels * ifov_rad`; make `image_size_mb` dynamic based on GSD; `gsd_m` state field; optics config section (ifov_urad, pixels_cross_track, focal_length, aperture); ~1 new telemetry parameter.

**Dependencies**: Requires `orbit_state.alt_km` (already available in orbit propagator).

---

### PLI-G4: Scene-Content-Dependent Compression Ratio

| Attribute | Value |
|---|---|
| **Fidelity doc** | `docs/sim_fidelity/payload_fidelity.md` Section 6 |
| **Priority** | High |
| **Classification** | **code-modify** |
| **Effort** | **S** (1--2 days) |

**Current state in code**: `compression_ratio` is a fixed 2.0 in `PayloadState`. It is written to `shared_params[0x0614]` as a constant. Data rate and storage accounting use raw data rate without compression adjustment.

**What exists**: `compression_ratio` state field; telemetry parameter 0x0614.

**What is missing**: `_estimate_scene_entropy(lat, lon)` method with latitude-based scene classification (ocean=low entropy, mid-latitude land=high entropy); `_compute_compression_ratio(entropy)` mapping entropy [0,1] to ratio [1.5, 4.0]; read `orbit_state.lat_deg` and `orbit_state.lon_deg` or `shared_params` GPS position; apply compression to storage accumulation in tick(); apply compression to captured image size; `scene_entropy` state field; ~1 new telemetry parameter.

**Dependencies**: Requires orbital position (lat/lon) from orbit state or GPS parameters in shared_params.

---

## 10. Cross-Subsystem Dependencies

Several gaps require cross-subsystem coupling through `shared_params` or shared utilities. The following dependency map identifies critical paths:

### 10.1 Quaternion Rotation Utility (Shared)

**Needed by**: EPS-G1, AOCS-G3, AOCS-G4, TCS-G2

All four gaps require rotating the sun vector from inertial (ECI) frame to spacecraft body frame using the attitude quaternion. A single `quat_rotate_inv(q, v)` function should be implemented once in `smo_common` and imported by all three subsystem models.

### 10.2 Attitude Quaternion (AOCS -> EPS, TCS, Payload)

**Producer**: AOCS model writes `shared_params[0x0200--0x0203]`
**Consumers**: EPS (G1: per-face illumination), TCS (G2: panel illumination), Payload (G2: attitude error via 0x0217)

All consumers require AOCS to tick before they do. Current engine iteration order is dictionary order (alphabetical), which gives: aocs, eps, obdh, payload, tcs, ttc. This means EPS and payload read current-tick AOCS data, but TCS reads it one tick late (tcs > aocs alphabetically, but actually aocs is ticked first since a < t). This is acceptable at 1 Hz tick rate.

### 10.3 EPS Power Line State (EPS -> AOCS, TTC)

**Producer**: EPS model writes power line states to `shared_params[0x0110--0x0117]`
**Consumers**: AOCS (G5: power-reset recovery reads 0x0117), TTC (G1: PDM timer writes TX state)

### 10.4 Panel Temperatures (TCS -> EPS)

**Producer**: TCS model writes `shared_params[0x0400--0x0405]`
**Consumer**: EPS (G1: per-face SA temperature coefficient)

### 10.5 Subsystem Tick Ordering

For correct cross-subsystem coupling, the recommended tick order is:

```
AOCS -> EPS -> TCS -> OBDH -> Payload -> TTC
```

This ensures:
- AOCS attitude is available for EPS, TCS, and Payload
- EPS power state is available for AOCS recovery and TTC PDM
- TCS panel temperatures are available for EPS SA temperature coefficient

The current alphabetical order (aocs, eps, obdh, payload, tcs, ttc) is close but places TCS after payload. This is acceptable with one-tick latency for the TCS-EPS panel temperature coupling.

---

## 11. Implementation Roadmap

### 11.1 Phase 0: Shared Infrastructure (2--3 days)

| ID | Task | Type | Effort |
|---|---|---|---|
| P0-1 | Quaternion rotation utility in `smo_common` | new-feature | S |
| P0-2 | Verify/finalize OBDH-G1 bootloader gating | code-modify | S |
| P0-3 | Verify OBDH-G2 circular buffer behavior | code-modify | S |

### 11.2 Phase 1: Critical Gaps -- OBDH and Core Services (3--5 days)

| ID | Task | Type | Effort |
|---|---|---|---|
| P1-1 | OBDH-G3: S12 edge detection and rate limiting | code-modify | S |
| P1-2 | OBDH-G4: S19 event-id matching and re-entrancy | code-modify | S |
| P1-3 | OBDH-G5: Simulated memory operations | new-feature | M |

### 11.3 Phase 2: AOCS Sensor Fidelity (8--12 days)

| ID | Task | Type | Effort |
|---|---|---|---|
| P2-1 | AOCS-G1: Dual redundant magnetometers | new-feature | M |
| P2-2 | AOCS-G2: Magnetometer source select command | code-modify | S |
| P2-3 | AOCS-G3: Individual CSS heads | new-feature | M |
| P2-4 | AOCS-G4: Star tracker FOV geometry | code-modify | M |

### 11.4 Phase 3: EPS Architecture (15--22 days)

| ID | Task | Type | Effort |
|---|---|---|---|
| P3-1 | EPS-G1: 6 body-panel solar array model | new-feature | L |
| P3-2 | EPS-G2: Cold-redundant PDM | new-feature | L |
| P3-3 | EPS-G5: Switchover and undercurrent detection | new-feature | M |
| P3-4 | EPS-G3: Separation timer circuit | new-feature | M |
| P3-5 | EPS-G4: Per-cell SA degradation | new-feature | M |

### 11.5 Phase 4: TCS Enhancements (10--14 days)

| ID | Task | Type | Effort |
|---|---|---|---|
| P4-1 | TCS-G1: Battery-heater-only | code-modify | S |
| P4-2 | TCS-G4: Heater cannot-turn-on refinement | code-modify | S |
| P4-3 | TCS-G3: Heater stuck-on re-scoping | code-modify | S |
| P4-4 | TCS-G2: 6-face illumination coupling | new-feature | L |
| P4-5 | TCS-G5: Passive thermal via orientation | new-feature | M |

### 11.6 Phase 5: TTC Enhancements (14--20 days)

| ID | Task | Type | Effort |
|---|---|---|---|
| P5-1 | TTC-G5: Dual ground station link budget | new-feature | L |
| P5-2 | TTC-G2: Burn wire antenna deployment | code-modify | M |
| P5-3 | TTC-G3: Beacon packet mode | code-modify | M |
| P5-4 | TTC-G4: Rate lifecycle | code-modify | S |
| P5-5 | TTC-G1: PDM command channel timer | code-modify | M |
| P5-6 | TTC-G6: GS equipment failure injection | new-feature | M |

### 11.7 Phase 6: Payload and Cross-Subsystem (8--12 days)

| ID | Task | Type | Effort |
|---|---|---|---|
| P6-1 | PLI-G2: Attitude-quality coupling | new-feature | S |
| P6-2 | PLI-G3: Swath width from altitude | code-modify | S |
| P6-3 | PLI-G4: Scene-dependent compression | code-modify | S |
| P6-4 | PLI-G1: Multispectral band configuration | new-feature | L |
| P6-5 | AOCS-G5: Actuator power-reset recovery | new-feature | M |
| P6-6 | AOCS-G6: Commissioning sequence support | new-feature | M |

---

## 12. Summary Statistics

### 12.1 Gap Count by Subsystem

| Subsystem | Total Gaps | Config-Only | Code-Modify | New-Feature |
|---|---|---|---|---|
| EPS | 5 | 0 | 0 | 5 |
| AOCS | 6 | 0 | 2 | 4 |
| TTC | 6 | 0 | 4 | 2 |
| OBDH | 5 | 0 | 4 | 1 |
| TCS | 5 | 0 | 3 | 2 |
| Payload | 4 | 0 | 2 | 2 |
| **Total** | **31** | **0** | **15** | **16** |

### 12.2 Gap Count by Effort

| Effort | Count | Total Days (range) |
|---|---|---|
| S (1--2 days) | 12 | 12--24 |
| M (3--5 days) | 14 | 42--70 |
| L (6--8 days) | 5 | 30--40 |
| **Total** | **31** | **84--134** |

Note: Phases can overlap and some effort estimates account for dependencies. The sequential roadmap estimate of 55--72 days assumes parallel work on independent subsystems and accounts for already-implemented gaps.

### 12.3 Implementation Status

| Status | Count | Gaps |
|---|---|---|
| **Fully implemented** | 2 | OBDH-G2 (circular HK buffer), OBDH-G4 (S19 wiring) |
| **Partially implemented** | 4 | OBDH-G1 (bootloader), OBDH-G3 (S12 monitoring), TTC-G1 (PDM channel partial), TTC-G2 (antenna deploy partial), TTC-G3 (beacon mode partial) |
| **Not implemented** | 25 | All EPS gaps, all AOCS gaps, TTC-G4/G5/G6, OBDH-G5, all TCS gaps, all Payload gaps |

### 12.4 New Parameters Summary

| Subsystem | New Params | Param ID Range |
|---|---|---|
| EPS | 48 | 0x0130--0x015F |
| AOCS | 27 | 0x0280--0x02A4 |
| TTC | 18 | 0x0520--0x0531 |
| OBDH | 6 | 0x031F--0x0324 |
| TCS | 9 | 0x0412--0x041A |
| Payload | 10 | 0x0620--0x0629 |
| **Total** | **118** | -- |

### 12.5 Estimated Lines of Code Impact

| Subsystem | Current LOC | Estimated Added LOC | Estimated Final LOC |
|---|---|---|---|
| `eps_basic.py` | 475 | ~950 | ~1,425 |
| `aocs_basic.py` | 986 | ~350 | ~1,336 |
| `ttc_basic.py` | 483 | ~350 | ~833 |
| `obdh_basic.py` | 505 | ~100 | ~605 |
| `tcs_basic.py` | 298 | ~450 | ~748 |
| `payload_basic.py` | 460 | ~290 | ~750 |
| `engine.py` | 801 | ~50 | ~851 |
| `service_dispatch.py` | 709 | ~80 | ~789 |
| `tm_storage.py` | 157 | ~30 | ~187 |
| New: `sim_memory.py` | 0 | ~150 | ~150 |
| New: `quat_util.py` (smo_common) | 0 | ~30 | ~30 |
| **Total** | **4,874** | **~2,830** | **~7,704** |

### 12.6 Priority Classification

| Priority | Gaps | Rationale |
|---|---|---|
| **Critical** | EPS-G1, EPS-G2, TTC-G1, TTC-G2, TTC-G5, OBDH-G1, TCS-G2, PLI-G1, PLI-G2 | Most visible to trained operators; fundamental physics or architecture gaps |
| **High** | EPS-G3, EPS-G5, TTC-G3, TTC-G4, OBDH-G3, OBDH-G4, TCS-G1, TCS-G5, PLI-G3, PLI-G4, AOCS-G1, AOCS-G2, AOCS-G5 | Important for realistic training but less immediately detectable |
| **Medium** | EPS-G4, AOCS-G3, AOCS-G4, AOCS-G6, TTC-G6, OBDH-G2, OBDH-G5, TCS-G3, TCS-G4 | Adds fidelity depth but not critical for initial operator training |

---

*This document was generated with AI assistance.*

![AIG -- Artificial Intelligence Generated](../../Screenshot%202026-03-10%20at%202.33.26%20PM.png)

Source: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/

# AOCS Simulator Fidelity Analysis

**Subsystem:** AOCS (Attitude and Orbit Control System)
**Target Fidelity:** Undetectably different from real spacecraft
**Model File:** `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py`
**Config File:** `configs/eosat1/subsystems/aocs.yaml`
**Date:** 2026-03-12

> ![AIG — Artificial Intelligence Generated](../../Screenshot%202026-03-10%20at%202.31.22%20PM.png)
> This document was generated with AI assistance.
> Icon source: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/

---

## Table of Contents

1. [Current Model Capabilities](#1-current-model-capabilities)
2. [Gap Analysis Summary](#2-gap-analysis-summary)
3. [Gap 1 — Dual Redundant Magnetometers](#3-gap-1--dual-redundant-magnetometers)
4. [Gap 2 — Magnetometer Source Select Command](#4-gap-2--magnetometer-source-select-command)
5. [Gap 3 — Individual CSS Heads with Geometric Projection](#5-gap-3--individual-css-heads-with-geometric-projection)
6. [Gap 4 — Star Tracker Zenith/Nadir FOV Geometry](#6-gap-4--star-tracker-zenithnadir-fov-geometry)
7. [Gap 5 — Actuator Power-Reset Recovery](#7-gap-5--actuator-power-reset-recovery)
8. [Gap 6 — ADCS Commissioning Sequence Support](#8-gap-6--adcs-commissioning-sequence-support)
9. [New Parameters and Configuration](#9-new-parameters-and-configuration)
10. [Test Cases Required](#10-test-cases-required)
11. [Implementation Priority and Risk](#11-implementation-priority-and-risk)

---

## 1. Current Model Capabilities

The AOCS model in `aocs_basic.py` is a 986-line Python module implementing the `AOCSBasicModel` class (extends `SubsystemModel`). The following capabilities are present in the current implementation:

### 1.1 State Machine (9 modes)

| Mode ID | Constant         | Description                                    |
|---------|------------------|------------------------------------------------|
| 0       | `MODE_OFF`       | No control; rates drift from random walk       |
| 1       | `MODE_SAFE_BOOT` | Hardware init, magnetometer only, 30s auto-transition to DETUMBLE |
| 2       | `MODE_DETUMBLE`  | B-dot control via magnetorquers, damp body rates |
| 3       | `MODE_COARSE_SUN`| CSS + magnetometer, coarse sun pointing (~5 deg accuracy) |
| 4       | `MODE_NOMINAL`   | Star tracker + gyros + RW nadir pointing       |
| 5       | `MODE_FINE_POINT`| Tight control bandwidth, highest accuracy (requires 4 wheels + ST) |
| 6       | `MODE_SLEW`      | Quaternion slew to commanded target             |
| 7       | `MODE_DESAT`     | Magnetorquer momentum dump of reaction wheels   |
| 8       | `MODE_ECLIPSE`   | Gyro-only propagation when ST blinded           |

**Automatic transitions implemented:**
- SAFE_BOOT -> DETUMBLE (after 30s)
- DETUMBLE -> COARSE_SUN (rates < 0.5 deg/s for 30 consecutive seconds)
- COARSE_SUN -> NOMINAL (CSS valid, att_error < 10 deg for 60s, ST valid)
- NOMINAL -> ECLIPSE (eclipse entry when ST not valid)
- ECLIPSE -> NOMINAL (eclipse exit, ST valid) or ECLIPSE -> COARSE_SUN (ST not valid)
- DESAT -> NOMINAL (all active wheel speeds below threshold for 10s)

**Emergency transition:** Any mode (except OFF, DETUMBLE, SAFE_BOOT) -> DETUMBLE when body rate magnitude exceeds 2.0 deg/s.

**Minimum dwell times** enforced per mode before auto-transition is allowed (0-20s depending on mode).

### 1.2 Sensor Models

#### Magnetometer (Single Unit)
- Three-axis field measurement (`mag_x`, `mag_y`, `mag_z`) in nT.
- Sinusoidal variation driven by orbit phase (simplified IGRF model).
- Gaussian noise: sigma = 50 nT per axis.
- Binary failure flag (`mag_failed` -> `mag_valid = False`).
- Total field magnitude computed (`mag_field_total`).
- **Limitation:** Single magnetometer only. No A/B redundancy, no individual bias/scale-factor modeling, no temperature-dependent noise.

#### Star Trackers (2 units, cold redundant)
- Two independent units (ST1, ST2) with 5-state status: OFF (0), BOOTING (1), TRACKING (2), BLIND (3), FAILED (4).
- 60-second boot time from power-on.
- Sun blinding model: probabilistic blinding when solar beta < 5 deg and not in eclipse (30% probability per tick).
- Star count: random 8-20 stars when TRACKING.
- Primary select register (`st_selected`): composite validity (`st_valid`) follows selected unit status.
- Independent failure flags (`st1_failed`, `st2_failed`).
- Power on/off commands, select command, failure injection and clearing.
- **Limitation:** No FOV geometry model. No distinction between zenith/nadir-mounted units. No half-cone exclusion angle for sun/earth/moon. Blinding is orbit-phase random, not geometry-driven.

#### Coarse Sun Sensor (CSS)
- Three-axis sun vector in body frame (`css_sun_x`, `css_sun_y`, `css_sun_z`).
- Driven by orbit phase and solar beta angle.
- Gaussian noise: sigma = 0.02 per axis (after normalization).
- Invalid during eclipse (sun vector zeroed, `css_valid = False`).
- Binary failure flag.
- **Limitation:** Modeled as a single composite sensor producing a sun vector directly. No individual head modeling (real spacecraft has 6 photodiode heads, one per face). No per-head noise, bias, temperature coefficient, or cosine-law projection. No partial occlusion or individual head failure.

#### Gyroscope
- Three-axis bias estimation with random-walk drift (sigma = 0.00005 * sqrt(dt) deg/s).
- Bias clamped to +/-0.1 deg/s.
- Temperature model coupled to reaction wheel thermal proximity.
- **Capability sufficient** for current fidelity target.

#### GPS Receiver
- Fix type (0-3), PDOP, satellite count.
- Fix quality depends on AOCS mode (no fix in OFF/SAFE_BOOT).
- **Capability sufficient** for current fidelity target.

### 1.3 Actuator Models

#### Reaction Wheels (4 wheels, tetrahedron configuration)
- Speed, temperature, current, enabled/active state per wheel.
- Bearing degradation model with friction increase.
- Current draw: baseline + speed-proportional + degradation component.
- Thermal model: heat from speed and degradation, radiative cooling to 20 deg C.
- Total system angular momentum computed (H = sum I*omega).
- Failure modes: bearing degradation (gradual), seizure (immediate), multi-wheel failure.
- Enable/disable commands per wheel.
- Speed bias command.
- Max speed clamped at 5500 RPM.
- **Limitation:** No power-reset recovery model. Disabling a wheel via failure leaves it disabled even after power line cycling. No spin-up/spin-down time constant model for commanded speed changes.

#### Magnetorquers (3 axes)
- Duty cycle per axis (-1.0 to +1.0).
- Enable/disable as a group.
- Per-axis failure flags (duty commanded but no torque on failed axis).
- Used in DETUMBLE (B-dot) and DESAT modes.
- **Capability sufficient** for current fidelity target.

### 1.4 Commands (handle_command)

| Command            | Description                              |
|--------------------|------------------------------------------|
| `set_mode`         | Commanded mode change (0-8)              |
| `desaturate`       | Enter DESAT mode                         |
| `slew_to`          | Quaternion slew target                   |
| `disable_wheel`    | Disable specific wheel (0-3)             |
| `enable_wheel`     | Enable specific wheel (0-3)              |
| `st_power`         | Power on/off star tracker unit (1 or 2)  |
| `st_select`        | Select primary star tracker              |
| `mag_select`       | Toggle magnetometer on/off (simplified)  |
| `rw_set_speed_bias`| Apply speed bias to a wheel              |
| `mtq_enable`       | Enable magnetorquers                     |
| `mtq_disable`      | Disable magnetorquers                    |

### 1.5 PUS Command Routing (S8 func_id 0-9)

The service dispatcher (`service_dispatch.py` lines 238-276) routes S8 function IDs to AOCS commands:

| func_id | AOCS Command       | Data Byte(s)                |
|---------|--------------------|-----------------------------|
| 0       | `set_mode`         | `data[0]` = mode            |
| 1       | `desaturate`       | (none)                      |
| 2       | `disable_wheel`    | `data[0]` = wheel index     |
| 3       | `enable_wheel`     | `data[0]` = wheel index     |
| 4       | `st_power` (ST1)   | `data[0]` = on/off          |
| 5       | `st_power` (ST2)   | `data[0]` = on/off          |
| 6       | `st_select`        | `data[0]` = unit (1 or 2)   |
| 7       | `mag_select`       | `data[0]` = on/off          |
| 8       | `rw_set_speed_bias`| `data[0]` = wheel, `data[1:5]` = float bias |
| 9       | `mtq_enable/disable`| `data[0]` = on/off         |

### 1.6 Telemetry Parameters

The model publishes 50+ telemetry parameters into `shared_params`, covering attitude quaternion, body rates, wheel speeds/temps/currents/enabled, magnetometer 3-axis + total, star tracker status/stars, CSS sun vector + valid, magnetorquer duties, total momentum, submode, time-in-mode, gyro bias/temp, GPS fix/PDOP/sats. Parameter IDs span 0x0200-0x0277.

### 1.7 Failure Injection

Supported failures: `rw_bearing`, `rw_seizure`, `gyro_bias`, `st_blind`, `st_failure`, `css_failure`, `mag_failure`, `mtq_failure`, `multi_wheel_failure`. Each has inject and clear methods.

### 1.8 Quaternion Mathematics

Full quaternion algebra: normalization, angle error, error axis, rotation application. Used for NOMINAL, FINE_POINT, and SLEW mode control loops.

---

## 2. Gap Analysis Summary

| # | Gap | Fidelity Impact | Commissioning Impact | Priority |
|---|-----|-----------------|---------------------|----------|
| 1 | Single magnetometer (no A/B redundancy) | HIGH — Real spacecraft has dual redundant magnetometers with independent noise/bias/failure characteristics. Operators cannot practice switchover procedures. | COM-005 sensor calibration does not exercise redundant mag. | P1 |
| 2 | mag_select command is a simple on/off toggle | HIGH — Should select between mag_a and mag_b with source switching, not just enable/disable a single unit. | Procedure references "magnetometer select" but current model just toggles validity. | P1 |
| 3 | CSS modeled as single composite sensor | MEDIUM-HIGH — Real CSS has 6 individual photodiode heads (one per face) with cosine-law geometric projection. Current model directly produces a sun vector. | Operators cannot diagnose individual head failures or observe face-dependent signal strengths. | P2 |
| 4 | Star tracker blinding model lacks FOV geometry | MEDIUM — No zenith/nadir FOV distinction, no half-cone exclusion angle. Blinding is probabilistic rather than geometric. | Operators cannot correlate ST blind events to orbit geometry or practice sun/earth avoidance. | P2 |
| 5 | No actuator power-reset recovery model | MEDIUM-HIGH — After EPS power line cycling (e.g., overcurrent trip on `eps.pl_aocs_wheels`), wheels and actuators do not simulate proper recovery sequence. | CTG-012 (overcurrent response) and FDIR recovery procedures cannot be realistically practiced. | P1 |
| 6 | Incomplete ADCS commissioning sequence support | MEDIUM — COM-005/006/007 procedures reference parameters and command sequences not fully modeled (gyro cal status, mag cal status, wheel commanded speed targets). | Commissioning dry-runs produce unrealistic responses. | P2 |

---

## 3. Gap 1 -- Dual Redundant Magnetometers

### 3.1 Current Implementation

`aocs_basic.py` lines 42-45 and 359-368:

```python
# State:
mag_x: float = 25000.0
mag_y: float = 10000.0
mag_z: float = -40000.0
mag_valid: bool = True

# Tick:
def _tick_magnetometer(self, s: AOCSState) -> None:
    if s.mag_failed:
        s.mag_valid = False
        return
    phi = self._orbit_phase * _DEG
    s.mag_x = 35000.0 * 0.8 * math.cos(phi) + random.gauss(0, 50.0)
    s.mag_y = 35000.0 * 0.5 * math.sin(phi) + random.gauss(0, 50.0)
    s.mag_z = -35000.0 * (0.3 + 0.2 * math.sin(2*phi)) + random.gauss(0, 50.0)
    s.mag_valid = True
```

Single magnetometer. Single failure flag. No redundancy.

### 3.2 Target State

Two independent magnetometer units (MAG-A and MAG-B), each with:
- **Independent 3-axis measurements** with unit-specific noise sigma, bias offsets, and scale factors.
- **Independent failure flags** (`mag_a_failed`, `mag_b_failed`).
- **Active source selection** (`mag_selected`): determines which unit feeds the AOCS attitude determination loop.
- **Temperature-dependent noise model**: noise sigma increases linearly above 50 deg C (typical for fluxgate magnetometers).
- **Persistent bias per unit**: hard-iron bias vector (nT) that remains constant across power cycles, calibratable via COM-005.
- **Simultaneous readout**: Both units read out telemetry continuously, but only the selected unit contributes to attitude determination.

### 3.3 Implementation Requirements

#### 3.3.1 AOCSState Additions

```python
# Magnetometer A
mag_a_x: float = 25000.0
mag_a_y: float = 10000.0
mag_a_z: float = -40000.0
mag_a_valid: bool = True
mag_a_failed: bool = False
mag_a_temp: float = 22.0

# Magnetometer B
mag_b_x: float = 25000.0
mag_b_y: float = 10000.0
mag_b_z: float = -40000.0
mag_b_valid: bool = True
mag_b_failed: bool = False
mag_b_temp: float = 22.0

# Active source (1=A, 2=B)
mag_selected: int = 1
```

#### 3.3.2 Unit-Specific Configuration

```python
# In __init__ or configure():
self._mag_a_bias = [50.0, -30.0, 20.0]    # Hard-iron bias nT
self._mag_b_bias = [-40.0, 60.0, -15.0]   # Different bias per unit
self._mag_a_noise_sigma = 50.0              # nT at room temperature
self._mag_b_noise_sigma = 55.0              # Slightly different per unit
self._mag_a_scale = [1.0, 1.0, 1.0]        # Scale factor errors
self._mag_b_scale = [1.001, 0.999, 1.002]  # Slightly different per unit
```

#### 3.3.3 New `_tick_magnetometer` Method

Replace the current single-unit method with a dual-unit method that:

1. Computes the "true" magnetic field from orbit geometry (same sinusoidal model).
2. For each unit (A and B):
   - Applies unit-specific scale factor: `measured = true * scale + bias`.
   - Adds Gaussian noise scaled by temperature: `sigma = base_sigma * (1 + max(0, (temp - 50)) * 0.02)`.
   - Sets `mag_X_valid = True` unless `mag_X_failed`.
3. Sets the composite `mag_x`, `mag_y`, `mag_z`, `mag_valid` to follow the selected unit.

#### 3.3.4 Composite Output Logic

```python
sel = s.mag_selected  # 1=A, 2=B
if sel == 1:
    s.mag_x, s.mag_y, s.mag_z = s.mag_a_x, s.mag_a_y, s.mag_a_z
    s.mag_valid = s.mag_a_valid
else:
    s.mag_x, s.mag_y, s.mag_z = s.mag_b_x, s.mag_b_y, s.mag_b_z
    s.mag_valid = s.mag_b_valid
```

This preserves backward compatibility: all existing code that reads `mag_x/y/z` and `mag_valid` continues to work. The dual units are an additional layer of detail.

#### 3.3.5 Failure Injection

Extend `inject_failure` and `clear_failure` to handle `mag_a_failure` and `mag_b_failure` independently, in addition to retaining the existing `mag_failure` (which would fail both units simultaneously for legacy compatibility).

---

## 4. Gap 2 -- Magnetometer Source Select Command

### 4.1 Current Implementation

`aocs_basic.py` lines 807-813:

```python
elif command == "mag_select":
    on = bool(cmd.get("on", True))
    if self._state.mag_failed and on:
        return {"success": False, "message": "Magnetometer failed"}
    self._state.mag_valid = on
    return {"success": True}
```

This simply toggles the magnetometer valid flag on/off. It does not select between redundant units.

`service_dispatch.py` line 262-264:

```python
elif func_id == 7:  # Magnetometer select
    on = bool(data[0]) if data else True
    aocs.handle_command({"command": "mag_select", "on": on})
```

The S8 func_id 7 passes a single byte interpreted as boolean on/off.

### 4.2 Target State

The `mag_select` command should switch the active magnetometer source between MAG-A and MAG-B. The data byte should encode the unit selection (1=A, 2=B) rather than a boolean on/off.

### 4.3 Implementation Requirements

#### 4.3.1 Updated handle_command

```python
elif command == "mag_select":
    unit = int(cmd.get("unit", 1))
    if unit not in (1, 2):
        return {"success": False, "message": f"Invalid mag unit: {unit}"}
    target_attr = f"mag_{'a' if unit == 1 else 'b'}_failed"
    if getattr(self._state, target_attr):
        return {"success": False, "message": f"MAG-{'A' if unit == 1 else 'B'} failed"}
    self._state.mag_selected = unit
    return {"success": True}
```

#### 4.3.2 Updated Service Dispatch

```python
elif func_id == 7:  # Magnetometer select (A=1, B=2)
    unit = data[0] if data else 1
    aocs.handle_command({"command": "mag_select", "unit": unit})
```

**Breaking change note:** The data byte semantics change from boolean (0=off, 1=on) to unit selection (1=A, 2=B). The value `1` is compatible in both interpretations (previously "on", now "unit A"). The value `0` previously meant "disable" and would need to be handled as an invalid unit. This must be coordinated with PUS field definitions in the MCS UI (`PUS_FIELD_DEFS`).

#### 4.3.3 Backward Compatibility

To avoid breaking existing procedures and tests, the command could accept both forms:

```python
elif command == "mag_select":
    if "unit" in cmd:
        unit = int(cmd["unit"])
        # ... unit selection logic
    elif "on" in cmd:
        # Legacy: on=True selects A (default), on=False disables both
        if not cmd["on"]:
            self._state.mag_valid = False
            return {"success": True}
        self._state.mag_selected = 1
        return {"success": True}
```

---

## 5. Gap 3 -- Individual CSS Heads with Geometric Projection

### 5.1 Current Implementation

`aocs_basic.py` lines 326-357:

```python
def _tick_css(self, s: AOCSState, orbit_state: Any) -> None:
    if s.css_failed:
        s.css_valid = False
        s.css_sun_x = s.css_sun_y = s.css_sun_z = 0.0
        return
    if orbit_state.in_eclipse:
        s.css_valid = False
        s.css_sun_x = s.css_sun_y = s.css_sun_z = 0.0
        return
    # Simplified sun vector from orbit geometry
    beta = orbit_state.solar_beta_deg * _DEG
    phase = self._orbit_phase * _DEG
    s.css_sun_x = math.cos(beta) * math.cos(phase) + random.gauss(0, 0.02)
    # ... normalize ...
```

This produces a composite sun vector directly. There is no concept of individual photodiode heads, face normals, or cosine-law projection.

### 5.2 Target State

A real CSS assembly has **6 individual photodiode heads**, one mounted on each face of the spacecraft body (+X, -X, +Y, -Y, +Z, -Z). Each head measures the cosine of the angle between its face normal and the sun direction. The on-board algorithm combines all 6 head readings to compute the sun vector.

### 5.3 Implementation Requirements

#### 5.3.1 Face Normal Definitions

```python
CSS_FACE_NORMALS = {
    'px': [ 1.0,  0.0,  0.0],  # +X face
    'mx': [-1.0,  0.0,  0.0],  # -X face
    'py': [ 0.0,  1.0,  0.0],  # +Y face
    'my': [ 0.0, -1.0,  0.0],  # -Y face
    'pz': [ 0.0,  0.0,  1.0],  # +Z face (zenith/nadir)
    'mz': [ 0.0,  0.0, -1.0],  # -Z face
}
```

#### 5.3.2 AOCSState Additions

```python
# Individual CSS head readings (0.0-1.0, cosine projection)
css_head_px: float = 0.0  # +X face head
css_head_mx: float = 0.0  # -X face head
css_head_py: float = 0.0  # +Y face head
css_head_my: float = 0.0  # -Y face head
css_head_pz: float = 0.0  # +Z face head
css_head_mz: float = 0.0  # -Z face head

# Per-head failure flags
css_head_px_failed: bool = False
css_head_mx_failed: bool = False
css_head_py_failed: bool = False
css_head_my_failed: bool = False
css_head_pz_failed: bool = False
css_head_mz_failed: bool = False
```

#### 5.3.3 Head-Level Model Algorithm

For each tick (in sunlight, not in eclipse):

1. Compute the true sun direction in body frame from orbit geometry + attitude quaternion (same source as current `css_sun_x/y/z`).
2. For each head `h` with face normal `n_h`:
   - Compute cosine projection: `cos_theta = dot(sun_body, n_h)`.
   - If `cos_theta <= 0`: head is on the dark side of the spacecraft, reading = 0.0.
   - If `cos_theta > 0`: reading = `cos_theta * (1 + noise)` where noise is Gaussian with sigma ~0.01.
   - Apply per-head bias: `reading += head_bias[h]` (small constant, ~0.005).
   - Clamp to [0.0, 1.0].
   - If head is failed: reading = 0.0 (or a stuck value for a more realistic failure).
3. Reconstruct the composite sun vector from the 6 head readings using the standard algorithm:
   ```
   sun_x = (head_px - head_mx) / (head_px + head_mx + epsilon)
   sun_y = (head_py - head_my) / (head_py + head_my + epsilon)
   sun_z = (head_pz - head_mz) / (head_pz + head_mz + epsilon)
   normalize(sun_x, sun_y, sun_z)
   ```
4. Set `css_valid = True` if at least 3 heads have non-zero readings (minimum for 3D sun vector reconstruction). If fewer than 3 heads are illuminated, `css_valid = True` only for 2D, or `False` for < 2.

#### 5.3.4 Fidelity Benefits

- **Individual head failures** can be injected and diagnosed by operators (e.g., "CSS +X head stuck at 0.3").
- **Partial occlusion** scenarios (e.g., solar array shadow on one face) are naturally supported.
- **Face-dependent noise** allows realistic calibration residuals.
- **Diagnostic telemetry**: operators see which faces are illuminated, correlating with attitude and orbit position.

#### 5.3.5 Backward Compatibility

The composite `css_sun_x/y/z` and `css_valid` outputs remain unchanged. The individual head readings are additional telemetry. Existing code that reads the composite sun vector continues to work without modification.

---

## 6. Gap 4 -- Star Tracker Zenith/Nadir FOV Geometry

### 6.1 Current Implementation

`aocs_basic.py` lines 271-324:

The star tracker model has no concept of boresight direction or FOV geometry. Blinding is determined by a simple rule: if not in eclipse and solar beta < 5 deg, there is a 30% random chance of blinding per tick. There is no distinction between ST1 and ST2 mounting (zenith vs nadir), no exclusion cone for the sun, earth, or moon, and no correlation between attitude and blinding.

```python
beta = abs(orbit_state.solar_beta_deg)
if not orbit_state.in_eclipse and beta < 5.0:
    blinded = random.random() < 0.3
```

### 6.2 Target State

EOSAT-1 should have two star trackers mounted with distinct boresight directions:
- **ST1**: Zenith boresight (+Z body axis), used for nadir-pointing operations.
- **ST2**: Nadir boresight (-Z body axis), used as backup.

Each tracker has a +/-15 degree half-cone exclusion zone around its boresight. Objects within this cone cause blinding:
- **Sun**: If the angle between the sun direction and the tracker boresight is < 15 deg, the tracker is blinded.
- **Earth limb**: If the earth subtends an angle within the exclusion zone (relevant for nadir-pointing ST).
- **Moon**: If the moon direction falls within the exclusion zone (rare but operationally relevant).

### 6.3 Implementation Requirements

#### 6.3.1 Configuration Additions

```python
# In configure() or __init__:
self._st1_boresight = [0.0, 0.0, 1.0]   # +Z body axis (zenith)
self._st2_boresight = [0.0, 0.0, -1.0]  # -Z body axis (nadir)
self._st_exclusion_half_cone_deg = 15.0
self._st_earth_exclusion_deg = 20.0      # Earth limb exclusion for nadir tracker
```

#### 6.3.2 Geometric Blinding Check

For each tracker unit, on each tick:

1. Compute the **sun direction in body frame** from orbit geometry and current attitude quaternion.
2. Compute the angle between the sun direction and the tracker's boresight vector:
   ```
   cos_angle = dot(sun_body, boresight)
   angle = acos(clamp(cos_angle, -1, 1))
   ```
3. If `angle < exclusion_half_cone` (15 deg), the tracker is **sun-blinded**.
4. For the nadir-pointing tracker (ST2 with boresight = -Z), additionally check if the earth disk fills the FOV. At 500 km altitude, the earth subtends approximately +/-65 deg from nadir. Since the nadir tracker points toward earth, it would see the bright earth limb. Check if the angle between the boresight and the nadir direction is within the earth exclusion zone.
5. The blinding determination is now **deterministic based on geometry** rather than probabilistic. Add a small stochastic component only for transition boundary (hysteresis): near the exclusion boundary (+/-2 deg), apply a probability ramp.

#### 6.3.3 Impact on Mode Transitions

The geometric blinding model affects:
- **NOMINAL -> ECLIPSE transition**: Currently triggered by eclipse entry when ST not valid. With geometric FOV, the nadir tracker (ST2) may be earth-blinded during normal nadir-pointing even outside eclipse.
- **ST selection logic**: Operators should select the tracker whose boresight has the clearest sky view for the current attitude.
- **Commissioning COM-005**: Sensor calibration must verify both trackers at appropriate orbit positions where their FOVs are clear.

#### 6.3.4 New Telemetry

```
aocs.st1_sun_angle   (deg)  — angle between ST1 boresight and sun direction
aocs.st2_sun_angle   (deg)  — angle between ST2 boresight and sun direction
aocs.st1_earth_angle (deg)  — angle between ST1 boresight and nadir
aocs.st2_earth_angle (deg)  — angle between ST2 boresight and nadir
```

These diagnostic parameters allow operators to anticipate blinding events and plan tracker handovers.

---

## 7. Gap 5 -- Actuator Power-Reset Recovery

### 7.1 Current Implementation

There is no mechanism in the AOCS model to respond to EPS power-line events. The EPS model manages an `aocs_wheels` power line (`eps.pl_aocs_wheels`, param 0x0117). The service dispatch checks this power state before allowing S8 func_id 0-9 commands (`service_dispatch.py` lines 703-705):

```python
if service == 8 and func_id in range(0, 10):
    if not lines.get('aocs_wheels', True):
        return False, "AOCS wheels power line is OFF"
```

However, the AOCS model itself does not know about power-line state. When the EPS trips the AOCS wheels power line (overcurrent or load shed), the AOCS model continues simulating wheel operation as if nothing happened. When the power line is restored, there is no boot/recovery sequence.

### 7.2 Target State

When the AOCS wheels power line is cycled OFF then ON:

1. **All reaction wheels should immediately lose control** when power goes OFF:
   - Wheel speeds begin decaying from friction (already partially modeled for disabled wheels).
   - Wheel enabled flags set to False.
   - No torque authority.
   - AOCS should detect loss of wheel control and transition to safe mode (DETUMBLE or COARSE_SUN, depending on whether magnetorquers remain powered).

2. **On power restoration**, wheels should undergo a recovery sequence:
   - Each wheel enters a "power-on self-test" state for 5 seconds.
   - After self-test, wheels are available but at zero speed.
   - Wheels must be explicitly re-enabled or the AOCS mode transition logic must include auto-recovery.
   - Star trackers on the AOCS power line should also require re-boot (60s).

3. **Magnetorquers** should also be affected if they share the AOCS power line (or have their own line).

### 7.3 Implementation Requirements

#### 7.3.1 Power State Monitoring

Add a method to AOCSBasicModel that checks EPS power-line status:

```python
def _check_power_state(self, shared_params: dict) -> bool:
    """Check if AOCS power line is active. Returns True if powered."""
    # Read eps.pl_aocs_wheels from shared_params (0x0117)
    return shared_params.get(0x0117, 1.0) > 0.5
```

#### 7.3.2 AOCSState Additions

```python
# Power-reset recovery
aocs_powered: bool = True
power_on_timer: float = 0.0        # Seconds since power restored
rw_self_test_complete: bool = True  # Wheels have completed POST
```

#### 7.3.3 Recovery Sequence in tick()

At the start of `tick()`, before sensor/actuator updates:

1. Read current power state from `shared_params`.
2. If power transitions OFF:
   - Set `aocs_powered = False`.
   - Disable all wheels (active_wheels = False, rw_enabled = False).
   - Set all star tracker statuses to OFF (power loss).
   - Set magnetorquers disabled.
   - Transition to MODE_OFF.
3. If power transitions ON (from OFF):
   - Set `aocs_powered = True`, `power_on_timer = 0.0`, `rw_self_test_complete = False`.
   - Transition to MODE_SAFE_BOOT (hardware re-initialization).
4. While `power_on_timer < 5.0`:
   - Wheels remain disabled (self-test in progress).
   - `power_on_timer += dt`.
5. After self-test completes:
   - `rw_self_test_complete = True`.
   - Wheels become available for enabling (but not auto-enabled -- requires command or auto-recovery logic).
   - Star trackers begin boot sequence (60s).

#### 7.3.4 FDIR Integration

The FDIR rule for `aocs.att_error > 5 deg` (safe_mode_aocs) should interact correctly with power-reset recovery. During recovery, the attitude error will naturally exceed 5 deg. The FDIR system should either:
- Suppress AOCS attitude error checks during power-on recovery (inhibit for 120s after power restore).
- Or the AOCS model should report a distinct mode/submode indicating recovery is in progress.

---

## 8. Gap 6 -- ADCS Commissioning Sequence Support

### 8.1 Current State

The AOCS commissioning procedures (COM-005, COM-006, COM-007) reference parameters and behaviors not fully modeled in the simulator:

| Procedure Reference | Param ID | Current Simulator Support |
|---------------------|----------|--------------------------|
| Gyro cal start      | 0x0250 (SET_PARAM) | Not modeled as command target |
| Gyro cal status     | 0x0251 | Not modeled |
| Gyro bias readout   | 0x0252-0x0254 | Partially modeled (gyro_bias_x/y/z at 0x0270-0x0272) |
| Mag cal start       | 0x0260 (SET_PARAM) | Not modeled |
| Mag cal status      | 0x0261 | Not modeled |
| Mag cal residual    | 0x0262 | Collision: 0x0262 is aocs.submode |
| ST power on         | 0x0270 (SET_PARAM) | Collision: 0x0270 is gyro_bias_x |
| Wheel commanded speed| 0x0280-0x0283 | Not modeled as SET_PARAM targets |

**Note:** There are significant parameter ID collisions between the commissioning procedures and the existing simulator telemetry. The commissioning procedures appear to have been written assuming a different parameter space layout than what the simulator implements. This needs resolution before commissioning sequence support can be added.

### 8.2 Implementation Requirements

#### 8.2.1 Parameter ID Reconciliation

The commissioning procedures reference param IDs that collide with existing telemetry. A dedicated commissioning parameter block should be allocated, for example 0x02A0-0x02BF:

| Param ID | Name | Description |
|----------|------|-------------|
| 0x02A0   | aocs.gyro_cal_cmd | Gyro calibration command (0=stop, 1=start) |
| 0x02A1   | aocs.gyro_cal_status | Gyro calibration status (0=idle, 1=in_progress, 2=complete) |
| 0x02A2   | aocs.mag_cal_cmd | Mag calibration command (0=stop, 1=start) |
| 0x02A3   | aocs.mag_cal_status | Mag calibration status (0=idle, 1=in_progress, 2=complete) |
| 0x02A4   | aocs.mag_cal_residual | Mag calibration residual (nT) |

The commissioning procedures should be updated to reference these corrected parameter IDs once the simulator implements them.

#### 8.2.2 Calibration Timer Model

Add calibration state machines to `AOCSBasicModel`:

- **Gyro calibration**: When commanded, accumulate gyro measurements over 300s, compute average bias, set `gyro_cal_status = 2 (complete)`. The estimated bias should match the simulator's internal `_gyro_bias` values with some noise.
- **Mag calibration**: When commanded, accumulate magnetometer measurements over 600s. Compute a residual value: `residual = random.gauss(150, 50)` nT (typical for successful calibration). If using the uncalibrated unit, residual may be higher.

---

## 9. New Parameters and Configuration

### 9.1 New Telemetry Parameters (parameters.yaml additions)

```yaml
# AOCS Dual Magnetometer
- { id: 0x0280, name: aocs.mag_a_x, subsystem: aocs, units: nT, description: Magnetometer A X-axis field }
- { id: 0x0281, name: aocs.mag_a_y, subsystem: aocs, units: nT, description: Magnetometer A Y-axis field }
- { id: 0x0282, name: aocs.mag_a_z, subsystem: aocs, units: nT, description: Magnetometer A Z-axis field }
- { id: 0x0283, name: aocs.mag_a_valid, subsystem: aocs, description: Magnetometer A valid flag }
- { id: 0x0284, name: aocs.mag_a_temp, subsystem: aocs, units: C, description: Magnetometer A temperature }
- { id: 0x0285, name: aocs.mag_b_x, subsystem: aocs, units: nT, description: Magnetometer B X-axis field }
- { id: 0x0286, name: aocs.mag_b_y, subsystem: aocs, units: nT, description: Magnetometer B Y-axis field }
- { id: 0x0287, name: aocs.mag_b_z, subsystem: aocs, units: nT, description: Magnetometer B Z-axis field }
- { id: 0x0288, name: aocs.mag_b_valid, subsystem: aocs, description: Magnetometer B valid flag }
- { id: 0x0289, name: aocs.mag_b_temp, subsystem: aocs, units: C, description: Magnetometer B temperature }
- { id: 0x028A, name: aocs.mag_selected, subsystem: aocs, description: "Active magnetometer (1=A, 2=B)" }

# AOCS Individual CSS Heads
- { id: 0x0290, name: aocs.css_head_px, subsystem: aocs, description: CSS +X face head reading (0-1) }
- { id: 0x0291, name: aocs.css_head_mx, subsystem: aocs, description: CSS -X face head reading (0-1) }
- { id: 0x0292, name: aocs.css_head_py, subsystem: aocs, description: CSS +Y face head reading (0-1) }
- { id: 0x0293, name: aocs.css_head_my, subsystem: aocs, description: CSS -Y face head reading (0-1) }
- { id: 0x0294, name: aocs.css_head_pz, subsystem: aocs, description: CSS +Z face head reading (0-1) }
- { id: 0x0295, name: aocs.css_head_mz, subsystem: aocs, description: CSS -Z face head reading (0-1) }

# AOCS Star Tracker FOV Geometry
- { id: 0x0296, name: aocs.st1_sun_angle, subsystem: aocs, units: deg, description: ST1 boresight-to-sun angle }
- { id: 0x0297, name: aocs.st2_sun_angle, subsystem: aocs, units: deg, description: ST2 boresight-to-sun angle }
- { id: 0x0298, name: aocs.st1_earth_angle, subsystem: aocs, units: deg, description: ST1 boresight-to-nadir angle }
- { id: 0x0299, name: aocs.st2_earth_angle, subsystem: aocs, units: deg, description: ST2 boresight-to-nadir angle }

# AOCS Power Recovery
- { id: 0x029A, name: aocs.power_state, subsystem: aocs, description: "AOCS power state (0=unpowered, 1=self-test, 2=nominal)" }
- { id: 0x029B, name: aocs.power_on_timer, subsystem: aocs, units: s, description: Time since power restoration }

# AOCS Commissioning
- { id: 0x02A0, name: aocs.gyro_cal_cmd, subsystem: aocs, description: "Gyro calibration command (0=stop, 1=start)" }
- { id: 0x02A1, name: aocs.gyro_cal_status, subsystem: aocs, description: "Gyro cal status (0=idle, 1=in_progress, 2=complete)" }
- { id: 0x02A2, name: aocs.mag_cal_cmd, subsystem: aocs, description: "Mag calibration command (0=stop, 1=start)" }
- { id: 0x02A3, name: aocs.mag_cal_status, subsystem: aocs, description: "Mag cal status (0=idle, 1=in_progress, 2=complete)" }
- { id: 0x02A4, name: aocs.mag_cal_residual, subsystem: aocs, units: nT, description: Mag calibration residual }
```

**Total new parameters: 27** (0x0280-0x029B, 0x02A0-0x02A4)

### 9.2 Configuration (aocs.yaml additions)

```yaml
magnetometers:
  mag_a:
    bias_nt: [50.0, -30.0, 20.0]
    noise_sigma_nt: 50.0
    scale_factor: [1.0, 1.0, 1.0]
  mag_b:
    bias_nt: [-40.0, 60.0, -15.0]
    noise_sigma_nt: 55.0
    scale_factor: [1.001, 0.999, 1.002]
  default_selected: 1  # 1=A, 2=B

css_heads:
  noise_sigma: 0.01        # Cosine-law projection noise
  head_bias:
    px: 0.003
    mx: -0.002
    py: 0.001
    my: 0.004
    pz: -0.001
    mz: 0.002

star_trackers:
  st1_boresight: [0.0, 0.0, 1.0]     # +Z (zenith)
  st2_boresight: [0.0, 0.0, -1.0]    # -Z (nadir)
  exclusion_half_cone_deg: 15.0
  earth_exclusion_deg: 20.0
  boot_time_s: 60.0

power_recovery:
  self_test_duration_s: 5.0
  auto_recovery_mode: safe_boot       # Mode to enter on power restore
```

---

## 10. Test Cases Required

### 10.1 Dual Magnetometer Tests

| # | Test Name | Description | Assertions |
|---|-----------|-------------|------------|
| T-MAG-01 | `test_dual_mag_independent_readings` | Both MAG-A and MAG-B produce readings simultaneously. | `mag_a_x != mag_b_x` (due to different bias). Both `mag_a_valid` and `mag_b_valid` are True. |
| T-MAG-02 | `test_mag_a_failure_independent` | Inject `mag_a_failure`. MAG-A invalid, MAG-B still valid. | `mag_a_valid == False`, `mag_b_valid == True`. |
| T-MAG-03 | `test_mag_b_failure_independent` | Inject `mag_b_failure`. MAG-B invalid, MAG-A still valid. | `mag_b_valid == False`, `mag_a_valid == True`. |
| T-MAG-04 | `test_mag_select_switches_composite` | Select MAG-B, verify composite `mag_x/y/z` matches `mag_b_x/y/z`. | `mag_x == mag_b_x`, `mag_valid == mag_b_valid`. |
| T-MAG-05 | `test_mag_select_rejects_failed_unit` | Try to select MAG-A after `mag_a_failure`. | Command returns `success: False`. |
| T-MAG-06 | `test_mag_bias_difference` | Over 100 ticks, average difference between MAG-A and MAG-B matches configured bias delta. | Mean difference within 10 nT of configured bias difference. |
| T-MAG-07 | `test_mag_noise_sigma` | Over 1000 ticks, measured noise standard deviation matches configured sigma. | Measured sigma within 20% of configured value. |
| T-MAG-08 | `test_mag_backward_compat` | With MAG-A selected (default), `mag_x/y/z` and `mag_valid` behave identically to the pre-dual-mag model. | Existing test_magnetometer_failure still passes. |
| T-MAG-09 | `test_both_mag_failure` | Inject failure on both units. `mag_valid == False` regardless of selection. | `mag_valid == False`. |
| T-MAG-10 | `test_mag_select_via_pus` | Send S8 func_id 7 with data=2, verify MAG-B becomes selected. | `mag_selected == 2`. |

### 10.2 Individual CSS Head Tests

| # | Test Name | Description | Assertions |
|---|-----------|-------------|------------|
| T-CSS-01 | `test_css_6_heads_illuminated_sunlight` | In sunlight with random attitude, at least 1-3 heads have non-zero readings. | `sum(head > 0 for head in heads) >= 1`. |
| T-CSS-02 | `test_css_cosine_projection` | With sun along +X body axis, `css_head_px` is near 1.0, `css_head_mx` is near 0.0. | `css_head_px > 0.9`, `css_head_mx < 0.05`. |
| T-CSS-03 | `test_css_all_zero_in_eclipse` | In eclipse, all 6 heads read 0.0. | All heads == 0.0. |
| T-CSS-04 | `test_css_single_head_failure` | Inject `css_head_px_failed`. That head reads 0.0. Others still work. CSS composite may still be valid. | `css_head_px == 0.0`, other heads non-zero, `css_valid == True` (enough heads remain). |
| T-CSS-05 | `test_css_multi_head_failure_invalidates` | Fail 5 of 6 heads. Composite `css_valid` becomes False (cannot reconstruct 3D sun vector). | `css_valid == False`. |
| T-CSS-06 | `test_css_composite_matches_heads` | Composite `css_sun_x/y/z` is reconstructed from individual head readings, not from a separate calculation. | Composite matches `(head_px - head_mx) / (head_px + head_mx)` etc. |
| T-CSS-07 | `test_css_backward_compat_failure` | Inject legacy `css_failure`. All heads and composite invalid. | All heads 0.0, `css_valid == False`. |

### 10.3 Star Tracker FOV Tests

| # | Test Name | Description | Assertions |
|---|-----------|-------------|------------|
| T-ST-01 | `test_st1_sun_blinding_geometric` | Set attitude so sun is within 10 deg of ST1 (+Z) boresight. ST1 should be blinded. | `st1_status == 3` (BLIND). |
| T-ST-02 | `test_st1_no_blinding_sun_far` | Set attitude so sun is > 20 deg from ST1 boresight. ST1 should track. | `st1_status == 2` (TRACKING). |
| T-ST-03 | `test_st2_earth_blinding_nadir` | With nadir-pointing attitude, ST2 (-Z boresight) points at earth. Should be earth-blinded or have degraded star count. | `st2_status == 3` or `st2_num_stars < 5`. |
| T-ST-04 | `test_st_sun_angle_telemetry` | After tick, `st1_sun_angle` and `st2_sun_angle` params are published. | Values in range [0, 180] degrees. |
| T-ST-05 | `test_st_handover_on_blinding` | ST1 blinded, auto-select ST2 if ST2 is tracking. Composite `st_valid` follows available tracker. | `st_valid == True` (if ST2 tracking). |
| T-ST-06 | `test_st_exclusion_boundary` | Sun angle at exactly 15 deg (boundary). Behavior is deterministic or has small hysteresis. | Consistent behavior across repeated runs. |

### 10.4 Power-Reset Recovery Tests

| # | Test Name | Description | Assertions |
|---|-----------|-------------|------------|
| T-PWR-01 | `test_power_off_disables_all_actuators` | Set `eps.pl_aocs_wheels = 0` in shared_params. After tick, all wheels disabled, MTQ disabled. | `rw_enabled[i] == False` for all i, `mtq_enabled == False`. |
| T-PWR-02 | `test_power_off_transitions_to_off` | After AOCS power line goes OFF, AOCS mode transitions to MODE_OFF. | `mode == MODE_OFF`. |
| T-PWR-03 | `test_power_restore_enters_safe_boot` | Power line OFF then ON. AOCS enters MODE_SAFE_BOOT. | `mode == MODE_SAFE_BOOT`. |
| T-PWR-04 | `test_power_restore_self_test_timer` | After power restore, wheels remain disabled for 5s (self-test). | `rw_self_test_complete == False` for first 5s, then True. |
| T-PWR-05 | `test_power_restore_st_reboot` | After power restore, star trackers enter BOOTING state and require 60s. | `st1_status == 1` (BOOTING) immediately after restore. |
| T-PWR-06 | `test_power_restore_full_recovery` | Power cycle, then tick through full recovery: self-test (5s), safe_boot (30s), detumble. Verify nominal recovery path. | Mode transitions: OFF -> SAFE_BOOT -> DETUMBLE -> (eventually) COARSE_SUN -> NOMINAL. |
| T-PWR-07 | `test_wheel_speed_decay_during_power_off` | Wheels spinning at 3000 RPM when power goes OFF. Verify friction-driven decay. | Wheel speeds decrease toward 0 over time while unpowered. |

### 10.5 Commissioning Sequence Tests

| # | Test Name | Description | Assertions |
|---|-----------|-------------|------------|
| T-COM-01 | `test_gyro_cal_sequence` | Command gyro cal start, wait 300s, verify status transitions idle->in_progress->complete. | `gyro_cal_status` transitions: 0 -> 1 -> 2. |
| T-COM-02 | `test_gyro_cal_bias_readback` | After gyro cal complete, bias readback matches internal `_gyro_bias` within noise tolerance. | Readback within 0.01 deg/s of internal value. |
| T-COM-03 | `test_mag_cal_sequence` | Command mag cal start, wait 600s, verify status transitions. | `mag_cal_status` transitions: 0 -> 1 -> 2. |
| T-COM-04 | `test_mag_cal_residual_range` | After mag cal, residual is in realistic range (50-500 nT). | 50 < residual < 500. |
| T-COM-05 | `test_com005_full_procedure` | Execute the full COM-005 sensor calibration procedure steps. All verifications pass. | All intermediate checks pass. |
| T-COM-06 | `test_com006_wheel_spinup` | Command wheel speed target, verify wheel reaches target within tolerance. | Wheel speed within 10% of commanded value after 60s. |

### 10.6 Integration Tests

| # | Test Name | Description | Assertions |
|---|-----------|-------------|------------|
| T-INT-01 | `test_mag_failure_triggers_fdir_with_dual` | Fail both magnetometers. Verify FDIR response (if configured). | FDIR triggers appropriate recovery action. |
| T-INT-02 | `test_css_head_failure_during_coarse_sun` | In COARSE_SUN mode, fail 2 CSS heads. Verify mode can still control (or degrades gracefully). | Mode remains COARSE_SUN or transitions appropriately. |
| T-INT-03 | `test_power_cycle_during_fine_point` | In FINE_POINT mode, cycle AOCS power. Verify full recovery to NOMINAL. | Recovery sequence completes. Final mode is NOMINAL or better. |
| T-INT-04 | `test_dual_mag_switchover_during_detumble` | In DETUMBLE with MAG-A, fail MAG-A, switch to MAG-B. Detumble continues. | Detumble control continues with MAG-B readings. |

---

## 11. Implementation Priority and Risk

### 11.1 Priority Order

| Priority | Gap | Estimated Effort | Risk |
|----------|-----|-----------------|------|
| P1       | Gap 1 + Gap 2: Dual magnetometers + select command | 3-4 days | LOW — Additive change, backward compatible via composite outputs. |
| P1       | Gap 5: Actuator power-reset recovery | 3-4 days | MEDIUM — Requires cross-subsystem coordination with EPS model via shared_params. |
| P2       | Gap 3: Individual CSS heads | 3-4 days | LOW — Additive change, backward compatible via composite outputs. |
| P2       | Gap 4: Star tracker FOV geometry | 2-3 days | MEDIUM — Requires attitude quaternion to sun-vector transformation in the blinding check. Existing attitude propagation must be leveraged. |
| P2       | Gap 6: Commissioning sequence support | 2-3 days | MEDIUM — Parameter ID conflicts with existing commissioning procedures require documentation update. |

### 11.2 Risk Analysis

**Low-risk changes** (Gaps 1, 2, 3): These are purely additive. The existing composite outputs (`mag_x/y/z`, `css_sun_x/y/z`) are preserved. New telemetry parameters are added. Existing tests should continue to pass without modification.

**Medium-risk changes** (Gaps 4, 5, 6):
- **Gap 4 (ST FOV):** Changes the blinding behavior from probabilistic to geometric. Existing tests that assume probabilistic blinding may need adjustment. The `test_star_tracker_boot_time` test uses `beta=20` which would be outside the exclusion cone, so it should pass. However, the integration tests that rely on blinding behavior will need orbit/attitude state setup.
- **Gap 5 (Power reset):** Requires the AOCS model to read EPS power-line state from `shared_params`. This creates a cross-subsystem dependency that does not currently exist in the AOCS model. If the EPS model is not loaded (e.g., unit tests with AOCS only), the AOCS model must gracefully default to "powered."
- **Gap 6 (Commissioning):** The parameter ID conflicts in the existing commissioning procedures are a documentation/configuration issue, not a code risk. However, updating the procedures to match the new parameter layout requires coordination with procedure authors.

### 11.3 Estimated Impact on Model Size

Current model: 986 lines.

Estimated additions:
- Dual magnetometer model: +80-100 lines (state, tick, commands, failure injection).
- Individual CSS heads: +60-80 lines (state, tick, failure injection).
- ST FOV geometry: +40-60 lines (blinding check replacement, telemetry).
- Power-reset recovery: +50-70 lines (power monitoring, recovery state machine).
- Commissioning support: +40-60 lines (calibration timers, status outputs).

**Estimated final model size: ~1,300-1,350 lines.** This remains within reasonable bounds for a single-file subsystem model.

### 11.4 Existing Test Impact

The existing 22 tests in `test_aocs_state_machine.py` should continue to pass after these changes, with the following exceptions:

- `test_magnetometer_failure` (test 15): May need adjustment if `mag_failure` now fails both units rather than setting `mag_valid` directly. Resolution: ensure `inject_failure("mag_failure")` sets both `mag_a_failed` and `mag_b_failed`, which results in composite `mag_valid = False`.
- Star tracker blinding tests (if any rely on probabilistic behavior): May need orbit/attitude setup for deterministic geometric blinding. Current tests use `beta=20` which is outside exclusion zone, so should be fine.

---

*End of AOCS Fidelity Analysis*

# TCS Simulator Fidelity Analysis

**Subsystem**: Thermal Control System (TCS)
**Target fidelity**: Undetectably different from real spacecraft telemetry and command response
**Baseline model**: `packages/smo-simulator/src/smo_simulator/models/tcs_basic.py` (298 lines)
**Config file**: `configs/eosat1/subsystems/tcs.yaml`
**Date**: 2026-03-12

---

## 1. Current Model Capabilities

### 1.1 Thermal Zone Architecture (10 Zones)

The model tracks temperatures for 10 distinct thermal zones, each with configurable thermal capacitance (J/degC) and radiative time constant (s):

| Zone | State Field | Capacitance | Time Constant | Initial Temp |
|------|-------------|-------------|---------------|--------------|
| panel_px (+X) | `temp_panel_px` | 5000 | 600 s | 15.0 degC |
| panel_mx (-X) | `temp_panel_mx` | 5000 | 600 s | 12.0 degC |
| panel_py (+Y) | `temp_panel_py` | 4000 | 500 s | 20.0 degC |
| panel_my (-Y) | `temp_panel_my` | 4000 | 500 s | 18.0 degC |
| panel_pz (+Z) | `temp_panel_pz` | 3000 | 800 s | 10.0 degC |
| panel_mz (-Z) | `temp_panel_mz` | 3000 | 800 s | 8.0 degC |
| OBC | `temp_obc` | 800 | 900 s | 25.0 degC |
| Battery | `temp_battery` | 2000 | 1200 s | 15.0 degC |
| FPA | `temp_fpa` | 100 | 120 s | -5.0 degC |
| Thruster | `temp_thruster` | 500 | 400 s | 5.0 degC |

### 1.2 Active Thermal Control

**Three thermostat-controlled heaters:**
- Battery heater: 6 W, ON at 1.0 degC, OFF at 5.0 degC
- OBC heater: 4 W, ON at 5.0 degC, OFF at 10.0 degC
- Thruster heater: 8 W, ON at 2.0 degC, OFF at 8.0 degC

Each heater supports:
- **Auto mode**: Thermostat control based on setpoint hysteresis band
- **Manual mode**: Direct on/off via `heater` command (disables thermostat)
- **Setpoint adjustment**: `set_setpoint` command reconfigures on/off temperatures
- **Return to auto**: `auto_mode` command re-enables thermostat

**FPA cooler:**
- Active cooler targeting -5.0 degC (configurable)
- Separate cooler_failed flag

### 1.3 Environmental Thermal Model

The current model applies a simplified environment split into eclipse/sunlit:

```
Eclipse:    env_ext = -30.0 degC,  env_int = 10.0 degC
Sunlit:     env_ext = -10.0 + 50.0 * |cos(beta)| degC,  env_int = 12.0 degC
```

Panel temperatures use the formula:
```
T += (env - T) / tau * dt + noise(0, 0.05)
```

The environment variable for each panel is offset from `env_ext` by a fixed constant:
- +X: env_ext + 5
- -X: env_ext + 0
- +Y: env_ext + 8
- -Y: env_ext + 0
- +Z: env_ext - 5
- -Z: env_ext - 5

These offsets are hardcoded constants bearing no relationship to actual spacecraft attitude or solar illumination geometry.

### 1.4 Failure Modes (Existing)

| Failure | Injection Key | Behavior |
|---------|---------------|----------|
| Heater failure | `heater_failure` | Heater forced OFF, cannot be turned ON |
| Cooler failure | `cooler_failure` | Cooler forced OFF |
| OBC thermal runaway | `obc_thermal` | Injects additional internal heat (default 30 W) |
| Sensor drift | `sensor_drift` | Adds offset to reported temperature (not actual) |
| Heater stuck-on | `heater_stuck_on` | Heater forced ON, ignores all commands |

### 1.5 Flight Hardware Features (Phase 4)

- Heater duty cycle tracking with 10-minute exponential sliding window
- Per-heater duty cycle telemetry (0x040E, 0x040F, 0x0410)
- Total heater power telemetry (0x0411)

### 1.6 Cross-Subsystem Coupling

One coupling exists today in `engine.py`:
```python
eps.set_bat_ambient_temp(tcs.get_battery_temp())
```
This feeds the TCS battery temperature into the EPS battery thermal model. There is no reverse coupling (EPS power generation state does not influence TCS panel temperatures).

### 1.7 Telemetry Parameters

The model writes 18 shared parameters (0x0400 through 0x0411). The `htr_thruster` parameter (0x040D) is written to `shared_params` in the model but is **missing from `parameters.yaml`** -- this is a known configuration issue.

### 1.8 Command Interface

| Command | Fields | Description |
|---------|--------|-------------|
| `heater` | circuit, on | Direct heater control (sets manual mode) |
| `set_setpoint` | circuit, on_temp, off_temp | Adjust thermostat band |
| `auto_mode` | circuit | Return heater to auto thermostat |
| `fpa_cooler` | on | Enable/disable FPA cooler |

---

## 2. Gap Analysis: Current vs. Target Fidelity

### Gap 1: Battery-Heater-Only Active Thermal Control

**Current**: All three heaters (battery, OBC, thruster) have active thermostat control with identical control logic.

**Target**: Only the battery heater should have flight-style active thermostat control. The OBC and thruster zones should be passively controlled (no heater circuit). This matches a typical LEO Earth observation satellite where:
- The battery is the only component requiring active heating (survival during eclipse, charge performance)
- The OBC generates enough internal heat to stay within limits
- The thruster (if propulsive system is even present) relies on passive insulation or is an operational concern handled by ground procedures, not autonomous onboard heaters

**Fidelity impact**: HIGH. An operator who sees three independently controllable heater circuits does not match the real EOSAT-1 thermal architecture. Training on the simulator would create false expectations about available thermal actuators.

### Gap 2: 6-Face Panel Temperature Coupling to Solar Illumination

**Current**: All six panel temperatures use the same `env_ext` value with hardcoded offsets (+5, 0, +8, 0, -5, -5). The only orbital parameter used is `solar_beta_deg` to compute a single `env_ext` scalar. There is no relationship between spacecraft attitude and which faces are sunlit.

**Target**: Each of the six spacecraft faces should have its solar heat input computed from:
1. The spacecraft attitude quaternion (from AOCS via `shared_params[0x0200..0x0203]`)
2. The sun vector in inertial frame (from `orbit_state.sun_eci`)
3. The eclipse flag (from `orbit_state.in_eclipse`)

The illumination fraction for each face is the dot product of the face normal (rotated by the attitude quaternion into inertial frame) with the sun unit vector, clamped to [0, 1]. A face pointing directly at the sun receives full solar flux; a face pointing away receives zero.

**Fidelity impact**: CRITICAL. Without this, panel temperatures do not respond to attitude maneuvers (slews, sun-pointing, target tracking). An operator commanding an AOCS slew should see correlated temperature changes on the illuminated panels. The current model makes it impossible to train operators on thermal-AOCS interactions, which are a primary concern during LEOP and contingency operations.

### Gap 3: Heater Stuck-On Failure Mode (Refinement)

**Current**: `heater_stuck_on` exists but applies identically to all three heater circuits. When the model changes to battery-heater-only, the stuck-on failure should only apply to the battery heater. The current implementation is functional but needs to be scoped correctly once OBC/thruster heaters are removed from active control.

**Target**: Battery heater stuck-on should cause:
- Continuous 6 W heat input into battery zone regardless of temperature
- Battery temperature rising above normal operating range
- Operator must recognize the signature (temperature rising despite commands to turn off)
- EPS power budget impact (continuous 6 W draw)
- Potential battery overtemperature event triggering FDIR

**Fidelity impact**: MEDIUM. The mechanism exists; it needs re-scoping to the battery-only architecture and ensuring the thermal runaway dynamics are physically plausible (rate of temperature rise given 6 W into a 2000 J/degC thermal mass).

### Gap 4: Heater Cannot-Turn-On Failure Mode (Refinement)

**Current**: `heater_failure` exists and forces the heater OFF. This is functionally correct for "cannot turn on."

**Target**: The failure should specifically model:
- Command is accepted (no rejection at PUS level) but heater physically does not activate
- Telemetry shows heater status ON (command echo) but temperature continues to drop -- mimicking a relay failure where the command register latches but the power switch is open
- Alternatively: telemetry correctly shows heater OFF despite ON command (switch relay feedback)
- Both variants should be selectable to train operators on different diagnostic paths

**Fidelity impact**: MEDIUM-HIGH. The current model correctly prevents the heater from turning on, but the command returns `{"success": False, "message": "Heater failed"}`, which is unrealistic. A real spacecraft would not know the heater relay has failed; it would accept the command. The operator must diagnose the failure from thermal telemetry trends, not from an explicit error message.

### Gap 5: Passive Thermal Control via Orientation Concept

**Current**: No concept of passive thermal control exists. All internal zones (OBC, battery, FPA, thruster) use a simple `env_int` environment value.

**Target**: Internal zone temperatures should be influenced by:
- The surrounding panel temperatures (conductive/radiative coupling between panels and internal zones)
- Internal heat dissipation (OBC power draw, battery I2R heating)
- The overall spacecraft thermal balance driven by the orientation-dependent panel temperatures

This creates a chain: `Sun vector + attitude -> panel illumination -> panel temps -> internal zone temps`. The orientation of the spacecraft passively controls the thermal environment of all internal components. This is the fundamental principle of passive thermal control in small satellites.

**Fidelity impact**: HIGH. Without this coupling, changing spacecraft attitude has zero effect on internal temperatures. Operators cannot learn that "pointing +Y at the sun" warms the battery bay or that "entering safe mode sun-pointing" provides passive thermal control.

---

## 3. Implementation Requirements

### 3.1 Battery-Heater-Only Active Thermal Control

#### Model Changes (`tcs_basic.py`)

**State changes:**
- Remove `htr_obc`, `htr_obc_failed`, `htr_obc_stuck_on`, `htr_obc_manual`, `htr_duty_obc` from `TCSState`
- Remove `htr_thruster`, `htr_thruster_failed`, `htr_thruster_stuck_on`, `htr_thruster_manual`, `htr_duty_thruster` from `TCSState`
- Remove `"obc"` and `"thruster"` entries from `_HEATER_POWER`, `_THERMOSTAT`
- Remove `_htr_on_accum` entries for `"obc"` and `"thruster"`

**tick() changes:**
- Remove `_thermostat_control("obc", ...)` and `_thermostat_control("thruster", ...)` calls
- OBC temperature equation becomes purely passive: `T += ((env_int - T)/tau + internal_heat/cap) * dt + noise`
- Thruster temperature equation becomes purely passive: `T += ((env_int_thruster - T)/tau) * dt + noise`
- Duty cycle tracking loop should only iterate over `("battery",)`
- `htr_total_power_w` should only sum battery heater power

**handle_command() changes:**
- `heater` command should only accept `circuit="battery"`; reject `"obc"` and `"thruster"` with `{"success": False, "message": "No heater on this circuit"}`
- `set_setpoint` and `auto_mode` should only accept battery circuit
- Consider keeping the FPA cooler command unchanged (it is a payload device, not a TCS heater)

**inject_failure() / clear_failure() changes:**
- `heater_failure`, `heater_stuck_on` should only accept `circuit="battery"`
- `obc_thermal` failure should remain (internal heat injection is independent of heater presence)

#### Config Changes (`tcs.yaml`)

Remove the OBC and thruster heater definitions:
```yaml
heaters:
  - name: battery
    power_w: 6.0
    on_temp_c: 1.0
    off_temp_c: 5.0
# OBC and thruster: passive thermal control only (no heater circuits)
```

#### Parameter Changes (`parameters.yaml`)

- Remove or deprecate `tcs.htr_obc` (0x040B)
- Remove or deprecate `tcs.htr_duty_obc` (0x040F)
- Remove or deprecate `tcs.htr_duty_thruster` (0x0410)
- Fix the existing gap: `tcs.htr_thruster` (0x040D) was already missing from `parameters.yaml` -- this becomes a non-issue once the thruster heater is removed
- Consider adding: `tcs.htr_battery_setpoint_lo` and `tcs.htr_battery_setpoint_hi` for ground-configurable setpoint telemetry

**Migration note**: The EPS model `POWER_LINE_DEFS` includes `htr_obc` as a switchable power line. If the OBC heater is removed from TCS, the EPS power line for it should also be removed or repurposed. The `htr_bat` power line remains.

### 3.2 Six-Face Panel Temperature Coupling to Solar Illumination

#### Concept

Replace the current fixed-offset environment model with a physics-based per-face solar flux computation.

Each spacecraft face has a unit normal vector in body frame:
```
+X: [1, 0, 0]    -X: [-1, 0, 0]
+Y: [0, 1, 0]    -Y: [0, -1, 0]
+Z: [0, 0, 1]    -Z: [0, 0, -1]
```

The sun vector in body frame is computed by rotating the inertial sun vector by the inverse of the attitude quaternion. The illumination fraction for each face is:
```
illum_i = max(0, dot(face_normal_i, sun_body_unit))
```

The absorbed solar power per face is:
```
Q_solar_i = S * A_i * alpha_i * illum_i
```
where:
- `S` = solar constant (1361 W/m2)
- `A_i` = face area (m2)
- `alpha_i` = solar absorptivity of the face surface coating

The radiated power per face to deep space is:
```
Q_rad_i = epsilon_i * sigma * A_i * T_i^4
```
where:
- `epsilon_i` = infrared emissivity
- `sigma` = Stefan-Boltzmann constant (5.67e-8 W/m2/K4)
- `T_i` = face temperature in Kelvin

For simulation fidelity without excessive computational cost, we can linearize the radiation term around the current temperature, resulting in the existing exponential decay form but with an environment temperature that varies per face based on illumination:

```
env_face_i = T_space + (T_solar_max - T_space) * illum_i
```
where `T_space` is the deep-space sink temperature (~-270 degC for unilluminated faces, but practically around -100 to -170 degC due to Earth albedo and IR) and `T_solar_max` is the equilibrium temperature of a fully illuminated face.

#### Data Sources Available

The following data is already available in `shared_params` at TCS tick time:
- Attitude quaternion: `shared_params[0x0200..0x0203]` (q1, q2, q3, q4) -- written by AOCS
- Sun vector in ECI: `orbit_state.sun_eci` (numpy array, from OrbitPropagator)
- Eclipse flag: `orbit_state.in_eclipse`
- CSS sun vector in body frame: `shared_params[0x0245..0x0247]` (alternative, already body-frame but noisy)

**Recommended approach**: Use `orbit_state.sun_eci` and `shared_params[0x0200..0x0203]` for a clean, deterministic computation. Do not use CSS values (they include noise and can be invalid).

#### Model Changes (`tcs_basic.py`)

**New constants (class-level or config-driven):**
```python
_FACE_NORMALS = {
    "panel_px": [1, 0, 0], "panel_mx": [-1, 0, 0],
    "panel_py": [0, 1, 0], "panel_my": [0, -1, 0],
    "panel_pz": [0, 0, 1], "panel_mz": [0, 0, -1],
}
_FACE_AREA_M2 = {  # Example for a 1U-3U-sized satellite
    "panel_px": 0.30, "panel_mx": 0.30,
    "panel_py": 0.30, "panel_my": 0.30,
    "panel_pz": 0.10, "panel_mz": 0.10,
}
_ABSORPTIVITY = 0.3    # typical white paint / MLI
_EMISSIVITY = 0.85     # typical Kapton / white paint
_SOLAR_CONST = 1361.0  # W/m^2
_STEFAN_BOLTZMANN = 5.67e-8
_EARTH_ALBEDO_FACTOR = 0.3   # fraction of solar flux reflected by Earth
_EARTH_IR_W_M2 = 237.0       # Earth IR emission (average)
```

**New helper method:**
```python
def _compute_face_illumination(self, orbit_state, shared_params):
    """Compute per-face solar illumination fractions from attitude and sun vector."""
    if orbit_state.in_eclipse:
        return {face: 0.0 for face in self._FACE_NORMALS}

    # Get attitude quaternion from AOCS
    q = [
        shared_params.get(0x0200, 0.0),  # q1 (x)
        shared_params.get(0x0201, 0.0),  # q2 (y)
        shared_params.get(0x0202, 0.0),  # q3 (z)
        shared_params.get(0x0203, 1.0),  # q4 (w)
    ]

    # Get sun vector in ECI
    sun_eci = orbit_state.sun_eci
    sun_eci_norm = sun_eci / (np.linalg.norm(sun_eci) + 1e-12)

    # Rotate sun vector from ECI to body frame using quaternion conjugate
    sun_body = quat_rotate_inv(q, sun_eci_norm)

    result = {}
    for face, normal in self._FACE_NORMALS.items():
        dot = sum(n * s for n, s in zip(normal, sun_body))
        result[face] = max(0.0, dot)
    return result
```

**tick() panel temperature changes:**

Replace the current panel loop:
```python
# Current (to be replaced):
panels = [("panel_px","temp_panel_px",env_ext+5), ...]
for key, attr, env in panels:
    T = getattr(s, attr)
    T += (env - T) / self._TAU[key] * dt + random.gauss(0, 0.05)
    setattr(s, attr, T)
```

With:
```python
# New: per-face illumination-driven thermal model
illum = self._compute_face_illumination(orbit_state, shared_params)
for face in self._FACE_NORMALS:
    attr = f"temp_{face}"
    T = getattr(s, attr)

    # Solar input (W)
    Q_solar = self._SOLAR_CONST * self._FACE_AREA_M2[face] * self._ABSORPTIVITY * illum[face]

    # Earth albedo and IR (simplified: proportional to Earth view factor ~0.3 for LEO)
    Q_earth = (self._EARTH_ALBEDO_FACTOR * self._SOLAR_CONST * illum.get(face, 0.3) * 0.3
               + self._EARTH_IR_W_M2) * self._FACE_AREA_M2[face] * 0.3

    # Radiative heat loss (linearized around current temp)
    T_kelvin = T + 273.15
    Q_rad = self._EMISSIVITY * self._STEFAN_BOLTZMANN * self._FACE_AREA_M2[face] * T_kelvin**4

    # Net heat flux -> temperature change
    dT = (Q_solar + Q_earth - Q_rad) / self._CAP[face] * dt
    T += dT + random.gauss(0, 0.05)
    setattr(s, attr, T)
```

#### Config Changes (`tcs.yaml`)

Add face geometry and surface properties:
```yaml
face_properties:
  panel_px:
    area_m2: 0.30
    absorptivity: 0.3
    emissivity: 0.85
  panel_mx:
    area_m2: 0.30
    absorptivity: 0.3
    emissivity: 0.85
  panel_py:
    area_m2: 0.30
    absorptivity: 0.92    # solar panel face, high absorptivity
    emissivity: 0.85
  panel_my:
    area_m2: 0.30
    absorptivity: 0.92    # solar panel face
    emissivity: 0.85
  panel_pz:
    area_m2: 0.10
    absorptivity: 0.3
    emissivity: 0.85
  panel_mz:
    area_m2: 0.10
    absorptivity: 0.3
    emissivity: 0.85

earth_albedo_factor: 0.3
earth_ir_w_m2: 237.0
solar_constant_w_m2: 1361.0
```

#### Quaternion Utility

A small quaternion rotation utility is needed. This can be added as a module-level function in `tcs_basic.py` or in `smo_common`:

```python
def quat_rotate_inv(q, v):
    """Rotate vector v by the inverse (conjugate) of quaternion q=[x,y,z,w]."""
    qx, qy, qz, qw = q
    # Conjugate
    qx, qy, qz = -qx, -qy, -qz
    # q * v * q_conj (Hamilton product)
    t = [
        2.0 * (qy * v[2] - qz * v[1]),
        2.0 * (qz * v[0] - qx * v[2]),
        2.0 * (qx * v[1] - qy * v[0]),
    ]
    return [
        v[0] + qw * t[0] + qy * t[2] - qz * t[1],
        v[1] + qw * t[1] + qz * t[0] - qx * t[2],
        v[2] + qw * t[2] + qx * t[1] - qy * t[0],
    ]
```

#### Execution Order Dependency

The TCS model reads AOCS attitude data from `shared_params`. This requires AOCS to have ticked **before** TCS in the engine's subsystem loop. Currently, the engine iterates `self.subsystems.items()` which is a dict. The insertion order must be verified to ensure AOCS runs first. If not guaranteed, the engine should enforce tick ordering: `aocs -> eps -> tcs -> ...`

**Current engine code (line 214-216):**
```python
for name, model in self.subsystems.items():
    model.tick(dt_sim, orbit_state, self.params)
```

This needs to either:
1. Use an `OrderedDict` with defined order, or
2. Tick AOCS/EPS explicitly before iterating remaining subsystems, or
3. Accept one-tick latency (TCS uses AOCS data from previous tick) -- acceptable at 1 Hz tick rate.

Option 3 is the simplest and introduces negligible error at 1 Hz. Document this as a known one-tick latency.

### 3.3 Heater Stuck-On Failure Mode (Battery Only)

#### Current Implementation (Already Exists)

The `heater_stuck_on` failure injection in `inject_failure()` already sets:
```python
setattr(s, f"htr_{c}_stuck_on", True)
setattr(s, f"htr_{c}", True)
```

And in `_thermostat_control()`:
```python
if stuck_on:
    setattr(s, f"htr_{circuit}", True)
    return
```

This is functionally correct. The heater stays on regardless of temperature or commands.

#### Required Changes for Battery-Only Architecture

1. Remove stuck-on state fields for OBC and thruster
2. `inject_failure("heater_stuck_on")` should only accept `circuit="battery"` (or ignore the circuit parameter since there is only one heater)
3. Verify thermal dynamics: with 6 W continuous into 2000 J/degC, the temperature rise rate is approximately:
   ```
   dT/dt = 6 W / 2000 J/degC = 0.003 degC/s = 0.18 degC/min
   ```
   Starting from the thermostat OFF temperature (5 degC), reaching a concerning 40 degC would take approximately:
   ```
   (40 - 5) / 0.003 = ~11,667 seconds = ~3.2 hours
   ```
   This is realistic for a small satellite battery heater stuck-on scenario. The temperature will eventually equilibrate where heater input equals radiative output.

4. Add an FDIR-coupled event: when battery temperature exceeds a configurable high limit (e.g., 35 degC) while the heater is commanded ON, generate a TCS overtemperature event. This trains operators to recognize stuck-on heaters from the event stream.

#### New Config

```yaml
failure_modes:
  heater_stuck_on:
    applicable_circuits: [battery]
    overtemp_event_threshold_c: 35.0
    overtemp_event_id: 0x0420
```

### 3.4 Heater Cannot-Turn-On Failure Mode (Realistic Diagnostics)

#### Current Implementation

The current `heater_failure` sets `htr_battery_failed = True`, which causes:
- `_thermostat_control()` forces heater OFF
- `handle_command("heater")` returns `{"success": False, "message": "Heater failed"}` when `on=True`

**Problem**: The explicit failure message is unrealistic. A real spacecraft does not know its heater relay has failed. The flight software would accept the command and set the heater status bit to ON, but the physical heater would not activate.

#### Required Changes

Implement two failure sub-modes:

**Mode A: Silent relay failure (switch-side failure)**
- Command is accepted (`{"success": True}`)
- Heater status telemetry shows ON (command echo to TM)
- But no heat is actually applied to the thermal zone
- Operator must diagnose from: "heater says ON but temperature is still dropping"

**Mode B: Feedback relay failure (sense-side failure)**
- Command is accepted (`{"success": True}`)
- Heater status telemetry correctly shows OFF (relay feedback detects open circuit)
- No heat is applied
- Operator diagnosis: "I commanded ON, TM says OFF, heater is not responding"

#### Implementation

**New state fields:**
```python
htr_battery_fail_mode: int = 0
# 0 = nominal
# 1 = silent relay failure (TM shows ON, no heat)
# 2 = feedback relay failure (TM shows OFF, no heat)
```

**tick() changes:**
```python
# Battery heater heat application
if s.htr_battery and s.htr_battery_fail_mode == 0:
    pwr = self._HEATER_POWER["battery"]
elif s.htr_battery and s.htr_battery_fail_mode == 1:
    pwr = 0.0  # Command accepted, TM shows ON, but no physical heat
else:
    pwr = 0.0
```

**Telemetry output changes:**
```python
# Heater status telemetry
if s.htr_battery_fail_mode == 2:
    # Feedback failure: TM always shows OFF regardless of commanded state
    shared_params[p.get("htr_battery", 0x040A)] = 0
else:
    # Normal or silent failure: TM reflects commanded state
    shared_params[p.get("htr_battery", 0x040A)] = 1 if s.htr_battery else 0
```

**handle_command() changes:**
```python
# Heater command -- always accept (no spacecraft-side failure detection)
if command == "heater":
    circuit = cmd.get("circuit", "battery")
    if circuit != "battery":
        return {"success": False, "message": "No heater on this circuit"}
    on = bool(cmd.get("on", True))
    stuck = s.htr_battery_stuck_on
    if stuck:
        return {"success": False, "message": "Heater stuck on, cannot control"}
    # Accept command regardless of failure mode
    s.htr_battery = on
    s.htr_battery_manual = True
    return {"success": True}
```

**inject_failure() changes:**
```python
elif failure == "heater_cannot_turn_on":
    mode = kw.get("mode", 1)  # 1=silent, 2=feedback
    s.htr_battery_fail_mode = mode
```

### 3.5 Passive Thermal Control via Orientation

#### Concept

Internal zones (OBC, battery, FPA, thruster) should derive their ambient environment temperature from the surrounding panel temperatures rather than from a fixed `env_int` constant. This creates the physical coupling chain:

```
Sun vector + Attitude -> Panel illumination -> Panel temperatures
Panel temperatures -> Internal zone ambient -> Internal zone temperatures
```

#### Implementation

**New helper method:**
```python
def _compute_internal_environment(self, s):
    """Compute internal ambient temperature from surrounding panel temps."""
    # Weighted average of all six panel temperatures
    # Weights represent thermal conductance from each face to the interior
    panel_temps = [
        s.temp_panel_px, s.temp_panel_mx,
        s.temp_panel_py, s.temp_panel_my,
        s.temp_panel_pz, s.temp_panel_mz,
    ]
    # For a box satellite, the internal environment is roughly the
    # area-weighted average of panel temperatures plus internal dissipation
    weights = [
        self._FACE_AREA_M2.get("panel_px", 0.30),
        self._FACE_AREA_M2.get("panel_mx", 0.30),
        self._FACE_AREA_M2.get("panel_py", 0.30),
        self._FACE_AREA_M2.get("panel_my", 0.30),
        self._FACE_AREA_M2.get("panel_pz", 0.10),
        self._FACE_AREA_M2.get("panel_mz", 0.10),
    ]
    total_w = sum(weights)
    env_int = sum(t * w for t, w in zip(panel_temps, weights)) / total_w
    return env_int
```

**tick() changes for internal zones:**
```python
env_int = self._compute_internal_environment(s)

# Battery temp (with thermostat heater)
self._thermostat_control("battery", s.temp_battery)
pwr = self._HEATER_POWER["battery"] if s.htr_battery and s.htr_battery_fail_mode == 0 else 0.0
s.temp_battery += ((env_int - s.temp_battery) / self._TAU["battery"]
                    + pwr / self._CAP["battery"]) * dt + random.gauss(0, 0.02)

# OBC temp (passive, with internal dissipation)
s.temp_obc += ((env_int - s.temp_obc) / self._TAU["obc"]
                + s.obc_internal_heat_w / self._CAP["obc"]) * dt + random.gauss(0, 0.03)

# Thruster temp (passive, slightly colder location in spacecraft)
env_thruster = env_int - 3.0  # offset for thruster location (typically on a boom or face)
s.temp_thruster += ((env_thruster - s.temp_thruster) / self._TAU["thruster"]) * dt + random.gauss(0, 0.03)

# FPA temp (with cooler)
cool = (self._fpa_cooler_target - 20.0) if (s.cooler_fpa and not s.cooler_failed) else 0.0
s.temp_fpa += ((env_int + cool - s.temp_fpa) / self._TAU["fpa"]) * dt + random.gauss(0, 0.02)
```

#### Config Changes

```yaml
internal_coupling:
  # Thermal conductance weights for panel-to-interior coupling
  panel_weights:
    panel_px: 0.30
    panel_mx: 0.30
    panel_py: 0.30
    panel_my: 0.30
    panel_pz: 0.10
    panel_mz: 0.10
  # Location offsets (degC) for zones not at spacecraft center
  zone_offsets:
    thruster: -3.0
    fpa: 0.0
    obc: 0.0
    battery: 0.0
```

---

## 4. New Parameters and Config Summary

### 4.1 New Telemetry Parameters

| Param ID | Name | Units | Description |
|----------|------|-------|-------------|
| 0x0412 | `tcs.illum_px` | - | +X face illumination fraction (0.0 - 1.0) |
| 0x0413 | `tcs.illum_mx` | - | -X face illumination fraction |
| 0x0414 | `tcs.illum_py` | - | +Y face illumination fraction |
| 0x0415 | `tcs.illum_my` | - | -Y face illumination fraction |
| 0x0416 | `tcs.illum_pz` | - | +Z face illumination fraction |
| 0x0417 | `tcs.illum_mz` | - | -Z face illumination fraction |
| 0x0418 | `tcs.env_internal` | degC | Computed internal ambient temperature |
| 0x0419 | `tcs.htr_battery_setpoint_lo` | degC | Battery heater thermostat ON threshold |
| 0x041A | `tcs.htr_battery_setpoint_hi` | degC | Battery heater thermostat OFF threshold |

### 4.2 Parameters to Remove / Deprecate

| Param ID | Name | Reason |
|----------|------|--------|
| 0x040B | `tcs.htr_obc` | OBC heater removed (passive thermal) |
| 0x040D | `tcs.htr_thruster` | Thruster heater removed (was already missing from parameters.yaml) |
| 0x040F | `tcs.htr_duty_obc` | OBC heater removed |
| 0x0410 | `tcs.htr_duty_thruster` | Thruster heater removed |

### 4.3 Config File Changes Summary

**`configs/eosat1/subsystems/tcs.yaml`:**
- Remove OBC and thruster heater definitions
- Add `face_properties` section (area, absorptivity, emissivity per face)
- Add `internal_coupling` section (panel weights, zone offsets)
- Add `failure_modes` section (overtemp thresholds, event IDs)
- Add thermal constants (solar constant, Earth albedo, Earth IR)

**`configs/eosat1/telemetry/parameters.yaml`:**
- Add 9 new TCS parameters (0x0412 - 0x041A)
- Remove 4 deprecated parameters (0x040B, 0x040D, 0x040F, 0x0410)

**`configs/eosat1/subsystems/fdir.yaml`:**
- Update heater-related FDIR rules to reference battery heater only
- Add overtemperature rule for stuck-on heater detection

**`configs/eosat1/subsystems/eps.yaml`** (or `eps_basic.py`):
- Remove `htr_obc` power line from `POWER_LINE_DEFS`
- Verify `htr_bat` power line remains functional

### 4.4 EPS Power Line Impact

The EPS model's `POWER_LINE_DEFS` currently lists:
```python
("htr_bat", True, True, 6.0, "Battery heater"),
("htr_obc", True, True, 4.0, "OBC heater"),
```

After the change:
- `htr_bat` remains unchanged
- `htr_obc` should be removed from the power line list
- This affects the power line index numbering (indices 0-7), which impacts:
  - `shared_params[0x0110..0x0117]` (power line status)
  - `shared_params[0x0118..0x011F]` (per-line currents)
  - Overcurrent thresholds
  - Load shedding order
  - PUS commands referencing line indices

**Recommendation**: Rather than removing the power line and breaking indices, set the `htr_obc` line to a "not installed" state: permanently OFF, not switchable, 0 W nominal. This preserves index stability.

---

## 5. Test Cases

### 5.1 Battery-Heater-Only Tests

```
test_battery_heater_thermostat_auto
    Given: battery temp starts at 10 degC, heater in auto mode
    When: temperature drops below ON threshold (1 degC) over multiple ticks
    Then: heater turns ON, temperature begins rising
    And: heater turns OFF when temperature reaches OFF threshold (5 degC)

test_battery_heater_manual_override
    Given: heater in auto mode
    When: operator sends heater ON command
    Then: heater turns ON, manual mode is set, thermostat is bypassed

test_battery_heater_return_to_auto
    Given: heater in manual mode, ON
    When: operator sends auto_mode command
    Then: manual mode cleared, thermostat resumes control

test_obc_heater_command_rejected
    Given: battery-only heater architecture
    When: operator sends heater command with circuit="obc"
    Then: command returns success=False, message="No heater on this circuit"

test_thruster_heater_command_rejected
    Given: battery-only heater architecture
    When: operator sends heater command with circuit="thruster"
    Then: command returns success=False, message="No heater on this circuit"

test_obc_passive_thermal
    Given: OBC zone with no heater, internal heat 10 W
    When: simulation runs for 1000 ticks
    Then: OBC temp stabilizes above ambient due to internal heat only
    And: no heater telemetry is generated for OBC

test_thruster_passive_thermal
    Given: thruster zone with no heater
    When: simulation runs for 1000 ticks
    Then: thruster temp tracks internal ambient minus offset
    And: no heater telemetry is generated for thruster
```

### 5.2 Six-Face Illumination Tests

```
test_sun_pointing_plus_x
    Given: spacecraft attitude with +X axis pointing at sun
    And: not in eclipse
    When: illumination fractions computed
    Then: illum_px ~= 1.0, illum_mx ~= 0.0
    And: illum_py, illum_my, illum_pz, illum_mz ~= 0.0

test_sun_pointing_45_deg
    Given: spacecraft attitude with sun at 45 deg between +X and +Y
    When: illumination fractions computed
    Then: illum_px ~= 0.707, illum_py ~= 0.707
    And: illum_mx = 0.0, illum_my = 0.0

test_eclipse_zero_illumination
    Given: orbit_state.in_eclipse = True
    When: illumination fractions computed
    Then: all six faces have illum = 0.0

test_panel_temp_response_to_attitude_change
    Given: spacecraft in sun-pointing +Y attitude, panels at equilibrium
    When: AOCS slews to +X sun-pointing (quaternion change in shared_params)
    Then: +X panel temp begins rising, +Y panel temp begins falling
    And: response timescale matches thermal time constant

test_panel_temp_eclipse_cooling
    Given: panels at sunlit equilibrium temperatures
    When: orbit_state.in_eclipse transitions to True
    Then: all panel temperatures begin exponential decay toward cold equilibrium
    And: rate of decay matches tau values

test_panel_temp_not_negative_270
    Given: panel in permanent eclipse (worst case)
    When: simulation runs for 10000 ticks
    Then: panel temperature stabilizes above -100 degC (Earth IR prevents deep-space cold)
```

### 5.3 Heater Stuck-On Tests

```
test_heater_stuck_on_temperature_rise
    Given: battery heater in auto mode, battery at 10 degC
    When: heater_stuck_on failure injected
    Then: heater remains ON continuously
    And: battery temperature rises above OFF threshold (5 degC) and continues rising
    And: temperature rise rate is approximately 0.003 degC/s

test_heater_stuck_on_ignores_off_command
    Given: heater stuck-on failure active
    When: operator sends heater OFF command
    Then: command returns success=False, "Heater stuck on, cannot control"
    And: heater remains ON

test_heater_stuck_on_power_budget
    Given: heater stuck-on failure active
    When: total heater power queried
    Then: htr_total_power_w includes 6 W from battery heater
    And: value persists even when thermostat would normally turn heater OFF

test_heater_stuck_on_overtemp_event
    Given: heater stuck-on failure active
    When: battery temperature exceeds overtemp threshold (35 degC)
    Then: TCS overtemperature event (0x0420) is generated

test_heater_stuck_on_clear
    Given: heater stuck-on failure active, battery at elevated temp
    When: failure cleared
    Then: thermostat resumes normal control
    And: battery temperature begins returning to setpoint band
```

### 5.4 Heater Cannot-Turn-On Tests

```
test_heater_silent_failure_command_accepted
    Given: heater_cannot_turn_on failure (mode=1) injected
    When: operator sends heater ON command
    Then: command returns success=True (no error indication)
    And: heater status TM (0x040A) shows 1 (ON)
    But: no heat is applied (power = 0 W in thermal equation)

test_heater_silent_failure_temp_continues_dropping
    Given: heater_cannot_turn_on failure (mode=1), heater commanded ON
    When: battery in eclipse-cooling scenario, run 200 ticks
    Then: battery temperature continues to fall despite heater "ON"
    And: operator must diagnose from temperature trend vs heater status mismatch

test_heater_feedback_failure_tm_shows_off
    Given: heater_cannot_turn_on failure (mode=2) injected
    When: operator sends heater ON command
    Then: command returns success=True
    And: heater status TM (0x040A) shows 0 (OFF) -- relay feedback detects failure
    And: no heat is applied

test_heater_failure_clear_restores_operation
    Given: heater_cannot_turn_on failure active
    When: failure cleared
    Then: heater responds normally to commands
    And: heat is applied when heater is ON
    And: TM correctly reflects heater state

test_heater_failure_thermostat_mode
    Given: heater_cannot_turn_on failure (mode=1), heater in auto mode
    When: temperature drops below ON threshold
    Then: thermostat logic activates heater (sets htr_battery = True)
    But: no heat is applied due to relay failure
    And: temperature continues dropping below survival limit
```

### 5.5 Passive Thermal Control / Internal Environment Tests

```
test_internal_env_tracks_panel_average
    Given: all six panels at 20 degC
    When: internal environment computed
    Then: env_internal ~= 20 degC (area-weighted average)

test_internal_env_asymmetric_panels
    Given: +Y panel at 60 degC (sunlit), all others at -10 degC
    When: internal environment computed
    Then: env_internal > -10 degC but << 60 degC (weighted by area fractions)

test_attitude_change_affects_battery_temp
    Given: spacecraft sun-pointing +Y, battery at equilibrium
    When: AOCS slews to +Z sun-pointing
    Then: panel temperatures redistribute
    And: internal environment temperature changes
    And: battery temperature follows new equilibrium over several time constants

test_safe_mode_sun_pointing_warms_interior
    Given: spacecraft in eclipse, all temps dropping
    When: exit eclipse with sun-pointing attitude (+Y toward sun)
    Then: +Y panel heats up rapidly
    And: internal environment temperature rises
    And: battery temperature stabilizes above survival limit even without heater
    (This validates the passive thermal control concept)

test_tumble_mode_thermal_averaging
    Given: spacecraft in DETUMBLE mode (rotating, AOCS rates nonzero)
    When: quaternion changes each tick (simulating tumble)
    Then: illumination fractions cycle across faces
    And: all panel temps converge toward similar values (thermal averaging)
    And: internal environment is roughly average of all-face equilibrium
```

### 5.6 Cross-Subsystem Integration Tests

```
test_eps_tcs_battery_temp_coupling
    Given: TCS battery temperature at 20 degC
    When: engine runs cross-subsystem coupling
    Then: EPS battery thermal model uses 20 degC as ambient input

test_aocs_tcs_attitude_coupling
    Given: AOCS writes quaternion [0, 0, 0, 1] to shared_params
    When: TCS computes face illumination
    Then: illumination fractions are consistent with identity quaternion and sun position

test_eps_power_budget_with_heater_only
    Given: battery heater ON (6 W)
    When: EPS computes power consumption
    Then: htr_bat power line shows 6 W
    And: htr_obc power line shows 0 W (line disabled/removed)
    And: total power consumption includes exactly 6 W heater contribution
```

### 5.7 Regression / Backward Compatibility Tests

```
test_tcs_config_without_face_properties_uses_defaults
    Given: tcs.yaml without face_properties section
    When: model configured
    Then: default face areas and surface properties are used
    And: model functions correctly (backward compatible)

test_tcs_config_without_internal_coupling_uses_fixed_env
    Given: tcs.yaml without internal_coupling section
    When: model configured
    Then: internal environment defaults to fixed value (legacy behavior)
    And: model functions correctly

test_existing_sensor_drift_still_works
    Given: sensor_drift failure injected on battery zone
    When: battery temp read from shared_params
    Then: reported temperature includes drift offset
    And: actual temperature (for heater thermostat) is undrifted

test_existing_obc_thermal_runaway_still_works
    Given: obc_thermal failure injected (30 W internal heat)
    When: simulation runs
    Then: OBC temperature rises due to excess internal heat
    And: no heater is involved (passive thermal only)
```

---

## 6. Implementation Priority and Effort Estimates

| Gap | Priority | Effort | Dependencies |
|-----|----------|--------|--------------|
| Battery-heater-only | 1 (do first) | Small (remove code, simplify) | None |
| Heater cannot-turn-on refinement | 2 | Small (modify existing failure) | Gap 1 |
| Heater stuck-on re-scoping | 3 | Small (remove OBC/thruster variants) | Gap 1 |
| 6-face illumination coupling | 4 | Medium (new physics, quaternion math) | AOCS shared_params |
| Passive thermal via orientation | 5 | Medium (new coupling, tuning) | Gap 4 |

**Total estimated effort**: ~400-500 new/modified lines of Python, ~60 lines of YAML config changes, ~200 lines of new tests.

**Risk**: The largest risk is in Gap 4 (illumination coupling) where incorrect quaternion math or execution order could produce physically impossible temperatures. Extensive unit testing of the quaternion rotation and illumination computation is essential before integration.

---

## 7. Verification Criteria for "Undetectably Different"

The enhanced TCS model should satisfy these criteria when observed by a trained satellite operator:

1. **Panel temperatures correlate with attitude**: Commanding a slew produces visible, physically-plausible temperature changes on the expected faces within the expected timescale.

2. **Eclipse transitions produce correct signatures**: Entry into eclipse causes all panel temps to drop; exit causes the sun-facing panels to rise. The rates match thermal time constants.

3. **Battery heater is the only controllable thermal actuator**: Attempting to command OBC or thruster heaters returns a clear "no heater" response, matching the flight database.

4. **Heater failures require diagnostic skill**: Stuck-on and cannot-turn-on failures are not announced to the operator; they must be diagnosed from telemetry trends (temperature vs. heater status mismatch).

5. **Passive thermal control is observable**: Changing spacecraft attitude measurably changes internal temperatures over several orbits, demonstrating that orientation is a thermal control lever.

6. **Thermal-power coupling is consistent**: Heater power draw in TCS telemetry matches the EPS power line current for the battery heater, and toggling the EPS power line for the heater has the same effect as the TCS heater command.

7. **Temperature ranges are physically plausible**: No zone reaches temperatures that would be impossible for a 500 km LEO satellite (e.g., below -150 degC or above 100 degC under normal operations).

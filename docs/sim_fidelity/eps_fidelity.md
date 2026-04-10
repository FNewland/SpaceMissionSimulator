# EPS Simulator Fidelity Analysis

**Subsystem**: Electrical Power Subsystem (EPS) -- EOSAT-1
**Current model**: `packages/smo-simulator/src/smo_simulator/models/eps_basic.py` (475 lines)
**Config**: `configs/eosat1/subsystems/eps.yaml`
**Date**: 2026-03-12

![AIG -- Artificial Intelligence Generated](../../Screenshot%202026-03-10%20at%202.33.26%20PM.png)
Source: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/

---

## Table of Contents

1. [Current Model Capabilities](#1-current-model-capabilities)
2. [Gap Analysis Summary](#2-gap-analysis-summary)
3. [Gap 1 -- 6 Body-Panel Solar Array Model](#3-gap-1----6-body-panel-solar-array-model)
4. [Gap 2 -- Cold-Redundant PDM with Unswitchable Lines](#4-gap-2----cold-redundant-pdm-with-unswitchable-lines)
5. [Gap 3 -- Separation Timer Circuit](#5-gap-3----separation-timer-circuit)
6. [Gap 4 -- Per-Cell Solar Panel Degradation](#6-gap-4----per-cell-solar-panel-degradation)
7. [Gap 5 -- Switchover and Undercurrent Detection](#7-gap-5----switchover-and-undercurrent-detection)
8. [New Parameters Summary](#8-new-parameters-summary)
9. [New Config Fields Summary](#9-new-config-fields-summary)
10. [Test Cases](#10-test-cases)
11. [Implementation Priority and Dependencies](#11-implementation-priority-and-dependencies)

---

## 1. Current Model Capabilities

### 1.1 Architecture

The EPS model (`EPSBasicModel`) is a single-class implementation inheriting from `SubsystemModel`. It maintains state via the `EPSState` dataclass and is driven by a 1 Hz `tick()` call from the simulation engine. The model reads orbital state (eclipse flag, solar beta angle) and writes telemetry into the shared parameter store.

### 1.2 What Exists and Works

| Capability | Implementation Detail | Fidelity Level |
|---|---|---|
| **Battery SoC tracking** | Coulomb-counting via net power integration. Linear OCV model between `soc_0_v` (21.5V) and `soc_100_v` (29.2V). Internal resistance voltage drop. | Medium |
| **Battery temperature** | Thermal model with exponential relaxation toward ambient, I^2*R heating contribution. Configurable time constant (600s). | Medium |
| **Battery DoD / cycle count** | Tracks depth-of-discharge (100 - SoC) and counts charge-discharge transitions. Maximum DoD limit field exists but is not enforced. | Low-Medium |
| **2-wing solar array** | Two arrays (A, B) split power 50/50. Generation = panel_area * cell_eff * solar_irradiance * cos(beta_angle). Per-wing enable/disable. | Low |
| **MPPT efficiency** | Fixed 97% efficiency multiplier applied to SA power. | Low (static) |
| **SA aging** | Cumulative sunlit-hours tracker, linear degradation at 3.14e-6 per hour (~2.75%/year). Age factor floors at 0.5. | Medium |
| **SA degradation (failure)** | Per-array degradation factor (0.0-1.0) injectable via `solar_array_partial` failure. | Medium |
| **Bus voltage** | Simplified: 28.2V baseline + SoC-proportional offset, clamped 20-29V. Independent of battery OCV model. | Low |
| **8 power lines** | Named lines with per-line power draw, on/off state, switchable/non-switchable classification. OBC and TTC RX are non-switchable. | Medium-High |
| **Per-line current** | Computed as power/bus_voltage for each line. Published as params 0x0118-0x011F. | Medium |
| **Overcurrent protection** | Per-line thresholds at ~150% nominal. Trip action: disable line, set bitmask flag, apply side effects. Reset via command. | Medium |
| **UV/OV detection** | Simple threshold comparison on bus voltage. Flags published as params 0x010E/0x010F. | Low |
| **Load shedding** | Priority list defined (`LOAD_SHED_ORDER`) and voltage threshold (`LOAD_SHED_VOLTAGE` = 26.5V), but NOT implemented in the tick loop. | Defined only |
| **Failure injection** | 5 failure types: `solar_array_partial`, `bat_cell` (-3.7V), `bus_short` (+80W load), `overcurrent` (multiplier), `undervoltage` (SoC reduction). | Medium |
| **Telemetry noise** | Gaussian noise on SA power (0.25%), battery voltage (0.02V), battery current (0.1A), power consumption (1W). | Low |
| **Command handling** | 7 commands: `set_payload_mode`, `set_fpa_cooler`, `set_transponder_tx`, `enable/disable_array`, `power_line_on/off`, `reset_oc_flag`. | Medium |

### 1.3 Telemetry Coverage

The model publishes 39 telemetry parameters (0x0100-0x0126) into the shared parameter store, all packed into HK SID 1 at 1 Hz. This covers battery state, SA currents/voltages, bus voltage, power budget, eclipse flag, power line status, per-line currents, and flight-hardware realism params.

### 1.4 Key Limitations of Current Model

1. **Solar array geometry is physically wrong**: The model treats two planar "wings" with a single beta-angle cosine projection. Real body-mounted panels have 6 faces with independent illumination fractions determined by spacecraft attitude relative to the sun vector.

2. **No PDM architecture**: Power distribution is a flat list of lines with hardcoded switchable/non-switchable flags. There is no model of redundant PDM units, no cross-strapping, and no concept of which PDM controls which line.

3. **No separation sequence**: The model starts in a steady-state configuration. There is no simulation of the launcher separation event, inhibit removal, or timer-initiated power-up.

4. **No per-cell degradation**: SA degradation is per-array (A or B). Real panels have individual cells that can fail, short, or degrade with different mechanisms (radiation, micrometeorite, hot-spot).

5. **No switchover detection**: There is no redundant bus switching, no undercurrent detection (which indicates a load has failed or disconnected), and no automatic switchover logic.

6. **Bus voltage is decoupled from battery**: The bus voltage calculation does not use the battery OCV or load current. It is a simple SoC-scaled value independent of the actual power balance.

7. **Load shedding is not implemented**: The priority list and threshold exist in constants but the tick() method never calls any load-shedding logic.

---

## 2. Gap Analysis Summary

| # | Gap | Real Spacecraft Behavior | Current Sim | Impact |
|---|-----|--------------------------|-------------|--------|
| G1 | 6 body-panel SA model | Per-face illumination from attitude quaternion + sun vector; each face has different cell population, area, efficiency | 2-wing model with single beta-angle cosine | **Critical** -- power generation is physically incorrect for body-mounted arrays |
| G2 | Cold-redundant PDM | Dual PDM units (A/B), some lines unswitchable (RX, OBC always powered from both), cross-strap relays | Flat line list, no redundancy concept | **Critical** -- no realistic power distribution architecture |
| G3 | Separation timer circuit | 30-min hardware timer after separation switch opens; enables PDM main bus and unswitchable lines | Model starts fully powered | **High** -- LEOP procedures cannot be tested realistically |
| G4 | Per-cell SA degradation | Individual cell failure, hot-spot, radiation degradation, string-level effects | Per-array scalar degradation | **Medium** -- insufficient granularity for anomaly investigation training |
| G5 | Switchover / undercurrent | PDM monitors load current; undercurrent = load disconnected/failed; triggers autonomous switchover to backup | No undercurrent detection, no switchover | **High** -- FDIR training impossible for PDM switchover scenarios |

---

## 3. Gap 1 -- 6 Body-Panel Solar Array Model

### 3.1 Real Spacecraft Behavior

A body-mounted solar array spacecraft like EOSAT-1 has solar cells on each of the six body panels (+X, -X, +Y, -Y, +Z, -Z). The power generated by each face depends on:

1. **Illumination angle**: The dot product of the face normal vector (in ECI or body frame) with the sun direction vector. Only positive dot products generate power (sun must be on the panel side).

2. **Spacecraft attitude**: The attitude quaternion rotates the body-frame face normals into the inertial frame (or equivalently rotates the sun vector into body frame).

3. **Eclipse state**: During eclipse, all faces generate zero power.

4. **Per-face properties**: Each face may have different cell areas, cell counts, cell efficiencies, and temperatures.

### 3.2 Required Implementation

#### 3.2.1 Face Definition Data Structure

```python
@dataclass
class SAFace:
    name: str            # "+X", "-X", "+Y", "-Y", "+Z", "-Z"
    normal_body: tuple   # Unit normal in body frame, e.g. (1,0,0) for +X
    area_m2: float       # Active cell area on this face
    num_strings: int     # Number of cell strings
    cells_per_string: int  # Cells in series per string
    cell_efficiency: float  # BOL efficiency
    degradation: float   # Per-face degradation factor (1.0 = BOL)
    temperature_c: float # Current face temperature (from TCS)
    current_a: float     # Output current from this face
    voltage_v: float     # Output voltage from this face
    power_w: float       # Generated power from this face
    illumination: float  # Fraction of face illuminated (0.0-1.0)
```

#### 3.2.2 Per-Face Power Calculation

The tick() method must:

1. **Read the sun vector in body frame** from the AOCS shared parameters (0x0245, 0x0246, 0x0247 -- CSS sun vector). If the CSS is invalid (eclipse), all illumination fractions are zero.

   Alternatively, transform `orbit_state.sun_eci` into body frame using the attitude quaternion from shared_params (0x0200-0x0203):

   ```
   q = [q1, q2, q3, q4]  # from shared_params
   sun_body = quaternion_rotate_inverse(q, sun_eci_unit)
   ```

   The CSS-based approach is simpler but introduces CSS sensor noise and failure modes. The quaternion-based approach is more accurate but requires implementing quaternion rotation. Recommendation: use the quaternion-based approach as the primary calculation, since the CSS output is itself a derived measurement with intentional noise.

2. **Compute per-face illumination fraction**:

   ```python
   for face in self._sa_faces:
       cos_angle = dot(face.normal_body, sun_body)
       face.illumination = max(0.0, cos_angle)  # Only positive hemisphere
   ```

3. **Compute per-face power**:

   ```python
   for face in self._sa_faces:
       # Temperature coefficient: GaAs triple-junction loses ~0.2%/degC above 25C
       temp_factor = 1.0 - 0.002 * max(0.0, face.temperature_c - 25.0)

       face.power_w = (
           face.area_m2
           * face.cell_efficiency
           * self._solar_irrad   # 1361 W/m^2
           * face.illumination
           * face.degradation
           * temp_factor
           * self._mppt_efficiency
           * self._sa_age_factor
       )
       face.voltage_v = face.power_w / max(face.current_a, 0.01) if face.power_w > 0.1 else 0.0
       face.current_a = face.power_w / 28.0  # Simplified: MPPT regulates to bus voltage
   ```

4. **Total SA power**: Sum of all face powers replaces the current two-wing calculation.

5. **Cross-subsystem coupling**: Read face temperatures from TCS shared_params (0x0400-0x0405 map to panel temperatures +X, -X, +Y, -Y, +Z, -Z). This creates a realistic coupling where hot panels generate less power.

#### 3.2.3 Quaternion Rotation Helper

```python
def quat_rotate_inverse(q, v):
    """Rotate vector v from inertial to body frame using quaternion q = [x,y,z,w]."""
    # Conjugate of q rotates from inertial to body
    qx, qy, qz, qw = q
    # q_conj * v * q
    # Using Hamilton product expansion:
    t = [
        2.0 * (qy*v[2] - qz*v[1]),
        2.0 * (qz*v[0] - qx*v[2]),
        2.0 * (qx*v[1] - qy*v[0]),
    ]
    return [
        v[0] + qw*t[0] + qy*t[2] - qz*t[1],
        v[1] + qw*t[1] + qz*t[0] - qx*t[2],
        v[2] + qw*t[2] + qx*t[1] - qy*t[0],
    ]
```

Note: this must match the quaternion convention used by the AOCS model, which stores `[x, y, z, w]` in `AOCSState.q`.

#### 3.2.4 Backward Compatibility

The existing `sa_a_current`, `sa_b_current`, `sa_a_voltage`, `sa_b_voltage` telemetry (0x0103, 0x0104, 0x010B, 0x010C) should be remapped. Options:

- **Option A**: Define SA-A as the sum of +X, +Y, +Z faces and SA-B as the sum of -X, -Y, -Z faces (splitting by redundant PDM assignment). This preserves the existing two-channel telemetry semantics.
- **Option B**: Deprecate the two-wing params and add 6 per-face params. This is more realistic but breaks existing displays and HK structures.

Recommendation: **Option A** for backward compatibility, plus add the 6 per-face params as new parameters for high-fidelity monitoring.

---

## 4. Gap 2 -- Cold-Redundant PDM with Unswitchable Lines

### 4.1 Real Spacecraft Behavior

A real EPS Power Distribution Module (PDM) architecture for a small LEO satellite typically includes:

1. **Dual PDM units** (PDM-A and PDM-B) in cold redundancy. PDM-A is the primary; PDM-B is unpowered until switchover.

2. **Unswitchable (essential) lines**: Certain loads are wired to BOTH PDMs through isolation diodes or cross-strap relays, so they remain powered regardless of which PDM is active. Typically:
   - OBC (both A and B connected via ORing diodes)
   - TTC Receiver (must always listen for ground commands)
   - Battery heater (survival-critical)

3. **Switchable lines**: All other loads are switched by latching relays or solid-state switches controlled by the active PDM. When the inactive PDM is selected, its switch states are undefined (typically all off until explicitly commanded).

4. **PDM status telemetry**: Each PDM reports its own health (temperature, input current, switch states, fault flags).

5. **Cross-strap relays**: Allow transferring individual loads between PDM-A and PDM-B without full switchover.

### 4.2 Required Implementation

#### 4.2.1 PDM State Data Structure

```python
@dataclass
class PDMUnit:
    name: str            # "A" or "B"
    active: bool         # True if this PDM is currently the active unit
    powered: bool        # True if the unit has power (can be powered but inactive)
    temperature_c: float # PDM board temperature
    input_current_a: float  # Total input current to this PDM
    input_voltage_v: float  # Input voltage
    fault_flags: int     # Bitmask of PDM-level faults
    switch_states: dict  # {line_name: bool} for switchable lines on this PDM
    oc_trip_flags: int   # Per-line overcurrent trip bitmask for this PDM
```

#### 4.2.2 Line Classification Rework

Replace the current flat `POWER_LINE_DEFS` with a hierarchical structure:

```python
POWER_LINE_DEFS = [
    # (name, category, pdm_assignment, default_on, power_w, description)
    # category: "essential" (unswitchable, always powered) or "switched"
    # pdm_assignment: "both" (cross-strapped), "A", "B"
    ("obc",         "essential", "both", True,  40.0, "OBC computer"),
    ("ttc_rx",      "essential", "both", True,   5.0, "TTC receiver"),
    ("htr_bat",     "essential", "both", True,   6.0, "Battery heater (survival)"),
    ("ttc_tx",      "switched",  "A",   True,  20.0, "TTC transmitter"),
    ("payload",     "switched",  "A",   False,  8.0, "Payload imager"),
    ("fpa_cooler",  "switched",  "A",   False, 15.0, "FPA cooler"),
    ("htr_obc",     "switched",  "A",   True,   4.0, "OBC heater"),
    ("aocs_wheels", "switched",  "B",   True,  12.0, "Reaction wheels"),
]
```

#### 4.2.3 PDM Switchover Logic

In `tick()`:

```python
# Essential lines are always powered (from whichever PDM has power)
for line in essential_lines:
    line_power = line.nominal_power  # Always on, cannot be switched off

# Switched lines only powered if their assigned PDM is active
for line in switched_lines:
    if pdm_units[line.pdm_assignment].active:
        line_power = line.nominal_power if line.switch_state else 0.0
    else:
        line_power = 0.0  # PDM not active, line has no power regardless
```

#### 4.2.4 New Commands

- `pdm_switchover`: Switch active PDM from A to B or vice versa. Must reset all switched-line states on the newly activated PDM to their default power-on states.
- `pdm_cross_strap`: Transfer a specific load from one PDM to the other (only for lines that support cross-strapping).
- `pdm_power_on` / `pdm_power_off`: Manually power on/off a PDM unit (for testing/recovery).

#### 4.2.5 Behavioral Differences from Current Model

- Switching PDM-A off while PDM-B is in cold standby would cause all PDM-A switched loads to lose power until PDM-B is activated and loads are re-enabled.
- Essential lines would experience a brief glitch (diode-ORed changeover) during switchover -- this can be modeled as a 1-tick transient on essential line currents.
- The OBC and TTC RX remain powered throughout any PDM switchover, which is the correct behavior for the current model's non-switchable lines, but the mechanism (ORing diodes vs. hardcoded flag) should be explicitly modeled.

---

## 5. Gap 3 -- Separation Timer Circuit

### 5.1 Real Spacecraft Behavior

When a spacecraft separates from the launch vehicle:

1. **Separation switches** open (mechanical microswitches on the separation ring). There are typically 2-4 redundant switches.

2. **Inhibit removal**: The launch vehicle umbilical disconnects, removing the "arm" signal that keeps the spacecraft in safe mode.

3. **Hardware timer starts**: A non-resettable hardware timer (typically 30-45 minutes) begins counting. This timer is independent of the OBC and cannot be overridden by software. It is an ECSS-mandated safety requirement.

4. **During timer period**: Only essential lines (OBC, TTC RX, battery heater) are powered via direct battery connection through the separation timer relay. The PDM main bus is NOT energized. The OBC boots and runs its bootloader.

5. **Timer expires**: The separation timer relay closes, energizing the PDM main bus. The PDM then powers switchable lines according to their default power-on states.

6. **First TTC contact**: The OBC has been running since separation (powered by essential bus). Once the PDM enables the TTC transmitter at timer expiry, the ground can establish contact.

### 5.2 Required Implementation

#### 5.2.1 Separation State Machine

```python
class EPSSepState(Enum):
    PRE_SEPARATION = 0    # On launch vehicle, umbilical connected
    SEPARATED_TIMER = 1   # Timer running, essential bus only
    TIMER_EXPIRED = 2     # PDM main bus enabled, normal operations
```

#### 5.2.2 State Variables

```python
@dataclass
class SeparationTimerState:
    sep_state: EPSSepState = EPSSepState.TIMER_EXPIRED  # Default: skip for non-LEOP sims
    sep_timer_s: float = 0.0            # Elapsed time since separation
    sep_timer_duration_s: float = 1800.0  # 30-minute timer (configurable)
    sep_switches: list[bool] = field(default_factory=lambda: [False, False])  # 2 redundant switches
    umbilical_connected: bool = False
    main_bus_enabled: bool = True       # PDM main bus relay state
```

#### 5.2.3 Tick Logic

```python
def _tick_separation(self, dt: float) -> None:
    sep = self._sep_state

    if sep.sep_state == EPSSepState.PRE_SEPARATION:
        # All lines powered via umbilical, battery on trickle charge
        # No SA power (inside fairing)
        return

    if sep.sep_state == EPSSepState.SEPARATED_TIMER:
        sep.sep_timer_s += dt

        # Only essential lines powered
        for line_name in POWER_LINE_NAMES:
            if POWER_LINE_CATEGORY[line_name] != "essential":
                self._state.power_lines[line_name] = False

        # Timer expiry
        if sep.sep_timer_s >= sep.sep_timer_duration_s:
            sep.sep_state = EPSSepState.TIMER_EXPIRED
            sep.main_bus_enabled = True
            # Enable PDM main bus: switchable lines go to their defaults
            for line_name, default_on in POWER_LINE_DEFAULTS.items():
                if POWER_LINE_CATEGORY[line_name] == "switched":
                    self._state.power_lines[line_name] = default_on

    # EPSSepState.TIMER_EXPIRED: normal operations, no special handling
```

#### 5.2.4 New Commands

- `simulate_separation`: Trigger the separation event (opens separation switches, starts timer). Used to begin LEOP simulation scenarios.
- `skip_sep_timer`: Fast-forward the separation timer to expiry (for testing convenience, would not exist on real hardware).

#### 5.2.5 Configuration

```yaml
separation:
  enabled: true              # Set false to start in TIMER_EXPIRED state
  timer_duration_s: 1800     # 30 minutes
  num_switches: 2
  essential_lines:            # Lines powered during timer period
    - obc
    - ttc_rx
    - htr_bat
```

---

## 6. Gap 4 -- Per-Cell Solar Panel Degradation

### 6.1 Real Spacecraft Behavior

Solar panel degradation in orbit occurs at multiple levels:

1. **Cell level**: Individual cell degradation from:
   - **Radiation damage**: Proton/electron fluence reduces minority carrier lifetime. Rate depends on orbit altitude, inclination, and shielding.
   - **Micrometeorite/debris damage**: Random impacts that can crack cover glass or damage cell interconnects.
   - **Hot-spot failure**: If one cell in a series string is shadowed or damaged, it can become reverse-biased and dissipate power as heat, potentially destroying adjacent cells.
   - **Interconnect fatigue**: Thermal cycling between eclipse and sunlight causes solder joint fatigue.

2. **String level**: Cells are wired in series into strings. If one cell in a string fails open, the entire string is lost. If one cell fails short, the string voltage drops by one cell voltage (~2.4V for triple-junction GaAs) but the string continues to produce current.

3. **Panel level**: Strings are wired in parallel. Loss of one string reduces panel current proportionally but does not affect other strings.

4. **Bypass diodes**: Each cell (or group of 2-3 cells) has a bypass diode. When a cell fails or is shadowed, the bypass diode conducts, losing that cell's voltage contribution but preventing hot-spot damage. This is the most common failure mitigation.

### 6.2 Required Implementation

#### 6.2.1 Cell String Model

```python
@dataclass
class CellString:
    string_id: int
    face: str               # Which SA face this string belongs to
    num_cells: int           # Cells in series (typically 10-20)
    failed_cells: int = 0   # Number of cells that have failed (bypass diode active)
    shorted_cells: int = 0  # Number of cells that have failed short
    open_circuit: bool = False  # True if interconnect is broken (string dead)
    degradation: float = 1.0   # Overall string degradation factor

    @property
    def effective_cells(self) -> int:
        """Cells actually contributing voltage."""
        return self.num_cells - self.failed_cells - self.shorted_cells

    @property
    def voltage_fraction(self) -> float:
        """Fraction of nominal string voltage."""
        if self.open_circuit:
            return 0.0
        return self.effective_cells / self.num_cells

    @property
    def current_fraction(self) -> float:
        """Fraction of nominal string current."""
        if self.open_circuit:
            return 0.0
        return self.degradation
```

#### 6.2.2 Per-Face Power with Cell Strings

```python
def _compute_face_power(self, face: SAFace) -> float:
    total_current = 0.0
    for string in face.strings:
        if string.open_circuit:
            continue
        string_current = (
            face.illumination
            * face.area_per_string
            * face.cell_efficiency
            * self._solar_irrad
            * string.current_fraction
            / (string.num_cells * self._cell_voltage_nom)
        )
        total_current += string_current

    face_voltage = face.strings[0].voltage_fraction * face.strings[0].num_cells * self._cell_voltage_nom
    return total_current * face_voltage * self._mppt_efficiency
```

#### 6.2.3 Failure Injection

New failure types for per-cell effects:

```python
def inject_failure(self, failure: str, magnitude: float = 1.0, **kwargs):
    if failure == "cell_fail":
        face = kwargs.get("face", "+X")
        string_id = kwargs.get("string_id", 0)
        num_cells = int(magnitude)
        # Fail N cells in the specified string (bypass diode active)
        self._sa_faces[face].strings[string_id].failed_cells += num_cells

    elif failure == "cell_short":
        face = kwargs.get("face", "+X")
        string_id = kwargs.get("string_id", 0)
        self._sa_faces[face].strings[string_id].shorted_cells += int(magnitude)

    elif failure == "string_open":
        face = kwargs.get("face", "+X")
        string_id = kwargs.get("string_id", 0)
        self._sa_faces[face].strings[string_id].open_circuit = True

    elif failure == "panel_degradation":
        face = kwargs.get("face", "+X")
        factor = max(0.0, min(1.0, 1.0 - magnitude))
        for string in self._sa_faces[face].strings:
            string.degradation = factor
```

#### 6.2.4 Radiation Degradation Model

For long-duration mission simulation, implement gradual radiation degradation:

```python
def _apply_radiation_degradation(self, dt: float):
    """Apply time-dependent radiation degradation to all strings.

    Typical LEO (500km, 97deg SSO) GaAs triple-junction:
    - ~2.75% power loss per year from proton/electron fluence
    - Dominated by displacement damage dose (Dd)
    - Non-linear: faster initial degradation, then asymptotic
    """
    # Remaining factor model: P/P0 = 1 - C * log10(1 + Dd/Dx)
    # For simulation, linearize over dt:
    degradation_rate = 3.14e-6  # per sunlit hour (matches existing sa_age_factor)
    for face in self._sa_faces.values():
        if face.illumination > 0:
            for string in face.strings:
                string.degradation *= (1.0 - degradation_rate * dt / 3600.0)
                string.degradation = max(0.5, string.degradation)
```

---

## 7. Gap 5 -- Switchover and Undercurrent Detection

### 7.1 Real Spacecraft Behavior

#### 7.1.1 Undercurrent Detection

PDMs monitor the current drawn by each load. If the current drops below a minimum threshold (undercurrent), it indicates:

- The load has failed (open circuit)
- The load has been disconnected (connector failure, cable harness damage)
- The load is in an unexpected low-power state

Undercurrent detection is complementary to overcurrent detection: overcurrent protects the bus from shorts; undercurrent detects load failures. Both are essential for autonomous FDIR.

#### 7.1.2 Autonomous Switchover

When the active PDM detects a critical fault (bus undervoltage, multiple overcurrent trips, PDM internal failure), it can trigger an autonomous switchover to the backup PDM. The sequence is:

1. Active PDM detects fault condition (persists for N ticks)
2. Active PDM asserts "switchover request" to the OBC
3. OBC (or hardware watchdog) commands PDM switchover
4. Old PDM switches off its main bus relay
5. New PDM switches on its main bus relay
6. New PDM loads default switch states for all switchable lines
7. Essential lines experience a brief glitch (ORing diode transfer)

In some designs, the switchover is fully hardware-autonomous (watchdog-driven), not requiring OBC involvement.

### 7.2 Required Implementation

#### 7.2.1 Undercurrent Detection

```python
# Undercurrent thresholds (amps) per line -- 10% of nominal at 28V
UC_THRESHOLDS = {
    "obc": 0.14, "ttc_rx": 0.02, "ttc_tx": 0.07, "payload": 0.03,
    "fpa_cooler": 0.05, "htr_bat": 0.02, "htr_obc": 0.01, "aocs_wheels": 0.04,
}
UC_PERSIST_TICKS = 5  # Must persist for 5 consecutive ticks to trigger

@dataclass
class UndercurrentState:
    uc_flags: int = 0          # Bitmask: bit i = line i undercurrent detected
    uc_counters: dict = field(default_factory=lambda: {n: 0 for n in POWER_LINE_NAMES})
```

In tick():

```python
for i, line_name in enumerate(POWER_LINE_NAMES):
    if lines.get(line_name, False):  # Only check powered lines
        current = s.line_currents[line_name]
        threshold = UC_THRESHOLDS.get(line_name, 0.0)
        if current < threshold and current >= 0:
            s.uc_counters[line_name] += 1
            if s.uc_counters[line_name] >= UC_PERSIST_TICKS:
                s.uc_flags |= (1 << i)
        else:
            s.uc_counters[line_name] = 0
            s.uc_flags &= ~(1 << i)
```

#### 7.2.2 Autonomous Switchover Logic

```python
@dataclass
class SwitchoverState:
    switchover_enabled: bool = True     # Can be disabled by ground command
    switchover_count: int = 0           # Total switchover events
    last_switchover_cause: int = 0      # 0=none, 1=undervoltage, 2=overcurrent, 3=commanded, 4=pdm_fault
    fault_persist_counter: int = 0      # Ticks of persistent fault condition
    fault_persist_threshold: int = 10   # Ticks before auto-switchover triggers

def _check_auto_switchover(self) -> bool:
    """Check if conditions warrant autonomous PDM switchover."""
    s = self._state

    # Condition 1: Sustained bus undervoltage
    if s.uv_flag:
        s.fault_persist_counter += 1
    # Condition 2: Multiple overcurrent trips (>= 3 lines tripped)
    elif bin(s.oc_trip_flags).count('1') >= 3:
        s.fault_persist_counter += 1
    else:
        s.fault_persist_counter = max(0, s.fault_persist_counter - 1)

    if s.fault_persist_counter >= s.fault_persist_threshold and s.switchover_enabled:
        self._execute_switchover()
        return True
    return False

def _execute_switchover(self) -> None:
    """Execute PDM switchover from active to backup."""
    s = self._state
    active_pdm = self._pdm_a if self._pdm_a.active else self._pdm_b
    backup_pdm = self._pdm_b if self._pdm_a.active else self._pdm_a

    # Deactivate current PDM
    active_pdm.active = False

    # Activate backup PDM
    backup_pdm.active = True
    backup_pdm.powered = True

    # Reset all switched lines to defaults on new PDM
    for line_name, default_on in POWER_LINE_DEFAULTS.items():
        if POWER_LINE_CATEGORY.get(line_name) == "switched":
            s.power_lines[line_name] = default_on
            backup_pdm.switch_states[line_name] = default_on

    # Clear fault counters
    s.fault_persist_counter = 0
    s.oc_trip_flags = 0

    # Record switchover
    s.switchover_count += 1
    s.last_switchover_cause = self._determine_cause()
```

#### 7.2.3 New Commands

- `enable_auto_switchover` / `disable_auto_switchover`: Ground can inhibit autonomous switchover during planned operations.
- `force_switchover`: Ground-commanded PDM switchover (cause = commanded).
- `reset_uc_flag`: Clear undercurrent flag for a specific line (analogous to `reset_oc_flag`).

---

## 8. New Parameters Summary

### 8.1 Per-Face Solar Array Parameters (0x0130-0x0147)

| Param ID | Name | Units | Description |
|----------|------|-------|-------------|
| 0x0130 | `eps.sa_px_power` | W | +X face SA power |
| 0x0131 | `eps.sa_mx_power` | W | -X face SA power |
| 0x0132 | `eps.sa_py_power` | W | +Y face SA power |
| 0x0133 | `eps.sa_my_power` | W | -Y face SA power |
| 0x0134 | `eps.sa_pz_power` | W | +Z face SA power |
| 0x0135 | `eps.sa_mz_power` | W | -Z face SA power |
| 0x0136 | `eps.sa_px_current` | A | +X face SA current |
| 0x0137 | `eps.sa_mx_current` | A | -X face SA current |
| 0x0138 | `eps.sa_py_current` | A | +Y face SA current |
| 0x0139 | `eps.sa_my_current` | A | -Y face SA current |
| 0x013A | `eps.sa_pz_current` | A | +Z face SA current |
| 0x013B | `eps.sa_mz_current` | A | -Z face SA current |
| 0x013C | `eps.sa_px_illum` | -- | +X face illumination fraction |
| 0x013D | `eps.sa_mx_illum` | -- | -X face illumination fraction |
| 0x013E | `eps.sa_py_illum` | -- | +Y face illumination fraction |
| 0x013F | `eps.sa_my_illum` | -- | -Y face illumination fraction |
| 0x0140 | `eps.sa_pz_illum` | -- | +Z face illumination fraction |
| 0x0141 | `eps.sa_mz_illum` | -- | -Z face illumination fraction |
| 0x0142 | `eps.sa_px_temp` | C | +X face SA temperature (read from TCS) |
| 0x0143 | `eps.sa_mx_temp` | C | -X face SA temperature |
| 0x0144 | `eps.sa_py_temp` | C | +Y face SA temperature |
| 0x0145 | `eps.sa_my_temp` | C | -Y face SA temperature |
| 0x0146 | `eps.sa_pz_temp` | C | +Z face SA temperature |
| 0x0147 | `eps.sa_mz_temp` | C | -Z face SA temperature |

### 8.2 PDM Parameters (0x0148-0x0155)

| Param ID | Name | Units | Description |
|----------|------|-------|-------------|
| 0x0148 | `eps.pdm_active` | -- | Active PDM (0=A, 1=B) |
| 0x0149 | `eps.pdm_a_powered` | -- | PDM-A powered flag |
| 0x014A | `eps.pdm_b_powered` | -- | PDM-B powered flag |
| 0x014B | `eps.pdm_a_temp` | C | PDM-A temperature |
| 0x014C | `eps.pdm_b_temp` | C | PDM-B temperature |
| 0x014D | `eps.pdm_a_current` | A | PDM-A total input current |
| 0x014E | `eps.pdm_b_current` | A | PDM-B total input current |
| 0x014F | `eps.pdm_a_fault` | -- | PDM-A fault bitmask |
| 0x0150 | `eps.pdm_b_fault` | -- | PDM-B fault bitmask |
| 0x0151 | `eps.switchover_count` | -- | Total PDM switchover count |
| 0x0152 | `eps.last_switchover_cause` | -- | Last switchover cause (0=none, 1=UV, 2=OC, 3=cmd, 4=fault) |

### 8.3 Separation Timer Parameters (0x0153-0x0157)

| Param ID | Name | Units | Description |
|----------|------|-------|-------------|
| 0x0153 | `eps.sep_state` | -- | Separation state (0=pre-sep, 1=timer, 2=nominal) |
| 0x0154 | `eps.sep_timer_elapsed` | s | Separation timer elapsed time |
| 0x0155 | `eps.sep_timer_remaining` | s | Separation timer remaining time |
| 0x0156 | `eps.main_bus_enabled` | -- | PDM main bus relay state |
| 0x0157 | `eps.sep_switch_status` | -- | Separation switch bitmask |

### 8.4 Undercurrent and Cell-Level Parameters (0x0158-0x015F)

| Param ID | Name | Units | Description |
|----------|------|-------|-------------|
| 0x0158 | `eps.uc_flags` | -- | Undercurrent detection bitmask |
| 0x0159 | `eps.auto_switchover_enabled` | -- | Auto-switchover enabled flag |
| 0x015A | `eps.sa_total_strings` | -- | Total SA cell strings across all faces |
| 0x015B | `eps.sa_failed_strings` | -- | Count of failed (open) strings |
| 0x015C | `eps.sa_degraded_strings` | -- | Count of strings with failed cells |
| 0x015D | `eps.sa_px_strings_ok` | -- | +X face healthy string count |
| 0x015E | `eps.sa_py_strings_ok` | -- | +Y face healthy string count |
| 0x015F | `eps.sa_pz_strings_ok` | -- | +Z face healthy string count |

**Total new parameters: 48**

### 8.5 HK Structure Updates

A new SID (or extension of SID 1) is needed for the expanded EPS telemetry. Recommendation: keep SID 1 at 1 Hz with essential parameters (backward compatible) and add a new SID for extended EPS telemetry at 4-8 Hz:

```yaml
- sid: 7
  name: EPS_Extended
  interval_s: 4.0
  parameters:
    # Per-face SA power (6 params)
    - { param_id: 0x0130, pack_format: H, scale: 10 }
    - { param_id: 0x0131, pack_format: H, scale: 10 }
    - { param_id: 0x0132, pack_format: H, scale: 10 }
    - { param_id: 0x0133, pack_format: H, scale: 10 }
    - { param_id: 0x0134, pack_format: H, scale: 10 }
    - { param_id: 0x0135, pack_format: H, scale: 10 }
    # Per-face illumination (6 params)
    - { param_id: 0x013C, pack_format: H, scale: 10000 }
    - { param_id: 0x013D, pack_format: H, scale: 10000 }
    - { param_id: 0x013E, pack_format: H, scale: 10000 }
    - { param_id: 0x013F, pack_format: H, scale: 10000 }
    - { param_id: 0x0140, pack_format: H, scale: 10000 }
    - { param_id: 0x0141, pack_format: H, scale: 10000 }
    # PDM status
    - { param_id: 0x0148, pack_format: B, scale: 1 }
    - { param_id: 0x0149, pack_format: B, scale: 1 }
    - { param_id: 0x014A, pack_format: B, scale: 1 }
    - { param_id: 0x014B, pack_format: h, scale: 100 }
    - { param_id: 0x014C, pack_format: h, scale: 100 }
    - { param_id: 0x014D, pack_format: H, scale: 1000 }
    - { param_id: 0x014E, pack_format: H, scale: 1000 }
    # Separation timer
    - { param_id: 0x0153, pack_format: B, scale: 1 }
    - { param_id: 0x0154, pack_format: I, scale: 1 }
    - { param_id: 0x0156, pack_format: B, scale: 1 }
    # Undercurrent / switchover
    - { param_id: 0x0158, pack_format: B, scale: 1 }
    - { param_id: 0x0151, pack_format: H, scale: 1 }
    - { param_id: 0x0152, pack_format: B, scale: 1 }
```

---

## 9. New Config Fields Summary

### 9.1 eps.yaml Additions

```yaml
model: eps_basic  # or eps_fidelity when the enhanced model is ready

# ---- Existing fields (unchanged) ----
technology: triple_junction_gaas
battery: { ... }
platform_idle_power_w: 95.0
# ...

# ---- NEW: 6-face solar array configuration ----
solar_array_faces:
  "+X":
    area_m2: 0.12
    num_strings: 4
    cells_per_string: 12
    efficiency: 0.295
  "-X":
    area_m2: 0.12
    num_strings: 4
    cells_per_string: 12
    efficiency: 0.295
  "+Y":
    area_m2: 0.18
    num_strings: 6
    cells_per_string: 12
    efficiency: 0.295
  "-Y":
    area_m2: 0.18
    num_strings: 6
    cells_per_string: 12
    efficiency: 0.295
  "+Z":
    area_m2: 0.08
    num_strings: 3
    cells_per_string: 12
    efficiency: 0.295
  "-Z":
    area_m2: 0.08
    num_strings: 3
    cells_per_string: 12
    efficiency: 0.295
  temperature_coefficient: -0.002  # Fraction per degree C above 25C

# ---- NEW: Cell-level parameters ----
cell:
  voltage_nom_v: 2.41        # Triple-junction GaAs nominal cell voltage
  radiation_degradation_rate: 3.14e-6  # Per sunlit hour
  min_degradation_factor: 0.5

# ---- NEW: PDM configuration ----
pdm:
  redundancy: cold           # "cold" or "hot"
  active_unit: A             # Default active PDM at startup
  essential_lines:
    - obc
    - ttc_rx
    - htr_bat
  pdm_a_lines:               # Switched lines on PDM-A
    - ttc_tx
    - payload
    - fpa_cooler
    - htr_obc
  pdm_b_lines:               # Switched lines on PDM-B
    - aocs_wheels
  switchover:
    enabled: true
    fault_persist_ticks: 10
    oc_trip_threshold: 3     # Number of tripped lines to trigger switchover
    uv_persist_s: 10.0       # Sustained UV duration to trigger switchover

# ---- NEW: Separation timer ----
separation:
  enabled: false             # Set true for LEOP scenarios
  timer_duration_s: 1800     # 30 minutes
  num_switches: 2
  essential_lines:
    - obc
    - ttc_rx
    - htr_bat

# ---- NEW: Undercurrent detection ----
undercurrent:
  enabled: true
  persist_ticks: 5
  thresholds:
    obc: 0.14
    ttc_rx: 0.02
    ttc_tx: 0.07
    payload: 0.03
    fpa_cooler: 0.05
    htr_bat: 0.02
    htr_obc: 0.01
    aocs_wheels: 0.04

# ---- NEW: Extended param IDs ----
param_ids_extended:
  sa_px_power: 0x0130
  sa_mx_power: 0x0131
  sa_py_power: 0x0132
  sa_my_power: 0x0133
  sa_pz_power: 0x0134
  sa_mz_power: 0x0135
  # ... (full list as defined in Section 8)
  pdm_active: 0x0148
  sep_state: 0x0153
  uc_flags: 0x0158
```

---

## 10. Test Cases

### 10.1 Per-Face Solar Array Tests

| Test ID | Description | Setup | Assertions |
|---------|-------------|-------|------------|
| `test_face_illumination_nadir_pointing` | With nadir-pointing attitude (q=[0,0,0,1]), verify +Z face gets no sun and -Z face gets full illumination when sun is along -Z body axis | Set sun vector to [0,0,-1] in body frame, quaternion to identity | `sa_pz_power == 0`, `sa_mz_power > 0`, illumination fractions correct |
| `test_face_illumination_sun_pointing` | Rotate spacecraft so +X faces the sun | Set quaternion to rotate +X toward sun vector | `sa_px_power == max`, other faces reduced proportionally |
| `test_eclipse_all_faces_zero` | In eclipse, all faces produce zero power | `orbit_state.in_eclipse = True` | All 6 face powers == 0.0, total gen == 0.0 |
| `test_face_power_sums_to_total` | Sum of 6 face powers equals total `power_gen_w` | Any sunlit attitude | `sum(face_powers) == power_gen_w` within noise |
| `test_face_temperature_coupling` | Higher face temperature reduces face power output | Set TCS panel temperature to 80C for one face | That face produces less power than at 25C |
| `test_backward_compat_sa_a_b` | SA-A and SA-B currents/voltages still populated | Normal tick | Params 0x0103, 0x0104, 0x010B, 0x010C still present and > 0 |
| `test_tumbling_power_varies` | When spacecraft tumbles, per-face power changes each tick | Set non-zero body rates | Face powers change between consecutive ticks |

### 10.2 PDM Tests

| Test ID | Description | Setup | Assertions |
|---------|-------------|-------|------------|
| `test_pdm_default_active_a` | PDM-A is active by default, all its lines powered | Default config | `pdm_active == 0 (A)`, PDM-A switched lines enabled |
| `test_pdm_switchover_command` | Commanded switchover from A to B | Send `force_switchover` command | `pdm_active == 1 (B)`, PDM-A lines disabled, PDM-B lines at defaults |
| `test_pdm_essential_lines_survive_switchover` | OBC, TTC RX, battery heater remain powered during switchover | Execute switchover | Essential line powers never go to zero (check every tick) |
| `test_pdm_cold_redundant_b_unpowered` | PDM-B is unpowered in cold standby | Default config | `pdm_b_powered == 0`, `pdm_b_current == 0` |
| `test_pdm_switchover_resets_oc_flags` | Switchover clears overcurrent trip flags | Trip 2 lines on PDM-A, then switchover | `oc_trip_flags == 0` after switchover |
| `test_pdm_auto_switchover_on_sustained_uv` | Autonomous switchover triggers after sustained undervoltage | Drain battery to trigger UV, wait 10 ticks | `pdm_active` changes, `last_switchover_cause == 1 (UV)` |
| `test_pdm_auto_switchover_disabled` | No auto-switchover when disabled by ground | Disable auto-switchover, trigger UV for 20 ticks | `pdm_active` does NOT change |
| `test_pdm_cross_strap_command` | Transfer a single load between PDMs | Send `pdm_cross_strap` for `aocs_wheels` from B to A | Wheels now powered by PDM-A, continues running |

### 10.3 Separation Timer Tests

| Test ID | Description | Setup | Assertions |
|---------|-------------|-------|------------|
| `test_sep_timer_essential_only` | During timer period, only essential lines are powered | Start in SEPARATED_TIMER state | OBC, TTC_RX, HTR_BAT on; all others off |
| `test_sep_timer_expiry` | After 1800s, main bus enables and switchable lines go to defaults | Tick 1800 times at dt=1.0 | `sep_state == 2 (TIMER_EXPIRED)`, `main_bus_enabled == True`, TTC_TX on |
| `test_sep_timer_telemetry` | Timer elapsed and remaining time are published correctly | Tick 600 times at dt=1.0 | `sep_timer_elapsed == 600`, `sep_timer_remaining == 1200` |
| `test_sep_disabled_starts_nominal` | When separation.enabled=false, model starts in TIMER_EXPIRED | Default config | `sep_state == 2`, all default lines powered |
| `test_sep_sa_generation_zero_pre_separation` | In PRE_SEPARATION state, SA generates zero (inside fairing) | Set state to PRE_SEPARATION | `power_gen_w == 0` |
| `test_sep_skip_timer_command` | `skip_sep_timer` fast-forwards to nominal | Start in SEPARATED_TIMER, send skip command | `sep_state == 2` immediately |

### 10.4 Per-Cell Degradation Tests

| Test ID | Description | Setup | Assertions |
|---------|-------------|-------|------------|
| `test_cell_fail_reduces_string_voltage` | Failing 1 cell in a 12-cell string reduces voltage by 1/12 | Inject `cell_fail` on face +X, string 0, magnitude=1 | String voltage fraction == 11/12, power reduced proportionally |
| `test_cell_short_reduces_voltage` | Shorting a cell reduces string voltage similarly | Inject `cell_short` on face +X, string 0, magnitude=1 | String voltage fraction == 11/12 |
| `test_string_open_kills_string` | Open-circuit string produces zero power | Inject `string_open` on face +X, string 0 | String current == 0, face power reduced by 1/num_strings |
| `test_multiple_cell_failures` | Multiple cell failures compound | Fail 3 cells in same string | Voltage fraction == 9/12 = 75% |
| `test_radiation_degradation_over_time` | Long-duration simulation shows gradual degradation | Tick 8760*3600 = 1 year of sim time at dt=10 | `sa_age_factor` reduced by ~2.75% |
| `test_panel_degradation_all_strings` | `panel_degradation` failure affects all strings on a face | Inject on face +Y, magnitude 0.3 | All strings on +Y have degradation == 0.7 |
| `test_cell_fail_clears` | Clearing `cell_fail` restores string | Inject then clear | `failed_cells == 0`, power restored |

### 10.5 Undercurrent / Switchover Detection Tests

| Test ID | Description | Setup | Assertions |
|---------|-------------|-------|------------|
| `test_undercurrent_flag_set` | Load drawing zero current when line is on triggers UC flag | Enable payload line but set payload_mode=0 (0W draw) | After 5 ticks, `uc_flags` bit for payload is set |
| `test_undercurrent_flag_clears` | UC flag clears when current returns to normal | Re-enable payload draw after UC flag | `uc_flags` bit for payload clears |
| `test_undercurrent_requires_persistence` | UC flag NOT set if undercurrent lasts only 3 ticks | Transient undercurrent for 3 ticks, then normal | `uc_flags` bit NOT set |
| `test_reset_uc_flag_command` | `reset_uc_flag` command clears the flag | After UC flag set, send reset command | `uc_flags` bit cleared |
| `test_auto_switchover_on_multi_oc` | 3+ overcurrent trips triggers auto-switchover | Trip 3 lines, wait 10 ticks | PDM switches, cause == OC |
| `test_switchover_count_increments` | Each switchover increments the counter | Perform 2 switchovers | `switchover_count == 2` |

### 10.6 Integration / Regression Tests

| Test ID | Description |
|---------|-------------|
| `test_existing_eps_params_unchanged` | All 39 existing params (0x0100-0x0126) are still published with the same semantics |
| `test_hk_sid1_backward_compat` | SID 1 structure remains unchanged; new params go to SID 7 |
| `test_eps_config_backward_compat` | Old eps.yaml (2-wing config) still loads and runs without errors |
| `test_command_backward_compat` | All 7 existing commands still work identically |
| `test_failure_injection_backward_compat` | All 5 existing failure types still function |
| `test_full_orbit_power_profile` | Run a full 96-minute orbit and verify power generation matches expected illumination profile for a body-mounted array spacecraft |

---

## 11. Implementation Priority and Dependencies

### 11.1 Dependency Graph

```
                    +-----------+
                    | G1: 6-Face|
                    | SA Model  |
                    +-----+-----+
                          |
              +-----------+-----------+
              |                       |
        +-----v-----+          +-----v-----+
        | G4: Per-   |          | G2: PDM   |
        | Cell Degr. |          | Redundancy|
        +-----+-----+          +-----+-----+
              |                       |
              |                 +-----v-----+
              |                 | G5: Switch |
              |                 | over/UC    |
              |                 +-----+-----+
              |                       |
              +-----------+-----------+
                          |
                    +-----v-----+
                    | G3: Sep   |
                    | Timer     |
                    +-----------+
```

G1 (6-face SA) must be implemented first because G4 (per-cell) builds on the per-face structure. G2 (PDM) must precede G5 (switchover) because switchover requires dual PDMs. G3 (separation timer) depends on G2 (PDM) because the timer enables the PDM main bus.

### 11.2 Implementation Phases

| Phase | Gaps | Estimated Effort | New LOC | New Tests |
|-------|------|------------------|---------|-----------|
| **Phase 1** | G1: 6-face SA model | 2-3 days | ~200 | 7 |
| **Phase 2** | G2: Cold-redundant PDM | 2-3 days | ~250 | 8 |
| **Phase 3** | G5: Switchover/undercurrent | 1-2 days | ~150 | 6 |
| **Phase 4** | G3: Separation timer | 1 day | ~100 | 6 |
| **Phase 5** | G4: Per-cell degradation | 2 days | ~200 | 7 |
| **Integration** | Regression + full-orbit | 1 day | ~50 | 6 |
| **Total** | All gaps | ~10-12 days | ~950 | 40 |

### 11.3 Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Quaternion convention mismatch between AOCS and EPS | Medium | High -- incorrect illumination | Unit test with known attitude/sun geometry; compare against AOCS CSS output |
| Backward compatibility breakage | Low | High -- existing tests fail | Phase 1 must pass all 691 existing tests before proceeding |
| Performance regression at 1 Hz tick with 6 faces, per-cell math | Low | Medium -- slower sim | Profile early; per-cell math is O(faces * strings) which is small |
| Config migration complexity | Medium | Medium -- operator confusion | Provide a migration script that converts 2-wing config to 6-face config |

### 11.4 Acceptance Criteria for "Undetectably Different"

The enhanced EPS model should satisfy these criteria:

1. **Power generation** varies realistically with spacecraft attitude and sun angle, not just beta angle.
2. **LEOP sequence** starts with separation timer, essential-bus-only period, and PDM activation -- matching the real timeline.
3. **PDM switchover** produces the correct transient behavior: essential lines stay up, switched lines cycle through their power-on defaults.
4. **Undercurrent detection** fires when a load silently fails, exactly as a real PDM would.
5. **Cell-level failures** produce the correct voltage and current signatures at the string and face level.
6. **An experienced operator** reviewing telemetry data from a full orbit cannot distinguish the simulated EPS from flight telemetry, given equivalent noise levels and timing.

---

*End of EPS fidelity analysis.*

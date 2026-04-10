# EOSAT-1 Electrical Power Subsystem (EPS)

**Document ID:** EOSAT1-UM-EPS-002
**Issue:** 2.0
**Date:** 2026-03-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Overview

The Electrical Power Subsystem (EPS) generates, stores, regulates, and distributes electrical
power to all spacecraft subsystems. It comprises six body-mounted GaAs solar panels (one per
spacecraft face), a lithium-ion battery, a cold-redundant Power Distribution Module (PDM) with
switchable and unswitchable lines, and associated harness.

## 2. Architecture

### 2.1 Solar Panels — 6-Face Body-Mounted Model

| Parameter            | Value                      |
|----------------------|----------------------------|
| Configuration        | 6 body-mounted panels (one per face: +X, -X, +Y, -Y, +Z, -Z) |
| Cell Technology      | Triple-junction GaAs       |
| Peak Power (BOL)     | ~40 W (combined, best case) |
| Operating Voltage    | 28 V (regulated bus)       |
| Orientation          | Fixed to body faces        |

Each of the six spacecraft faces carries a body-mounted solar panel. Power generation per
panel depends on the cosine projection of the Sun vector onto the panel normal:

| Panel Face | Normal Vector | Notes                                           |
|------------|---------------|--------------------------------------------------|
| +X         | Along-track   | Illuminated during orbit-normal sun angles       |
| -X         | Anti-track    | Illuminated during orbit-normal sun angles       |
| +Y         | Cross-track   | Primary generation for dawn-dusk orbit           |
| -Y         | Anti-cross    | Primary generation for dawn-dusk orbit           |
| +Z         | Nadir         | Earth-facing; reduced direct solar illumination  |
| -Z         | Zenith        | Space-facing; good solar exposure                |

The per-panel power output is computed as:

```
P_panel = P_max * max(0, cos(angle_to_sun)) * degradation_factor
```

where `angle_to_sun` is the angle between the Sun vector and the panel normal in the body
frame, and `degradation_factor` accounts for panel aging and damage. The total spacecraft
power generation is the sum of all six panel contributions.

In the dawn-dusk SSO at 450 km, the +Y and -Y panels typically provide the majority of
power generation. The -Z (zenith) panel also contributes significantly when oriented towards
the Sun. The +Z (nadir) panel receives primarily albedo illumination.

### 2.2 Battery

| Parameter            | Value                      |
|----------------------|----------------------------|
| Chemistry            | Lithium-Ion (Li-Ion)       |
| Nominal Voltage      | 28 V                       |
| Capacity             | 40 Ah                      |
| Depth of Discharge   | Max 35% (nominal ops)      |
| Charge Method        | CC/CV with thermal cutoff  |

### 2.3 Power Distribution Module (PDM)

The EPS uses a cold-redundant Power Distribution Module (PDM) with two categories of
power lines:

#### Unswitchable Lines

Unswitchable PDM lines are permanently powered after separation and cannot be commanded
off. These ensure that critical functions remain available at all times:

| Line          | Load               | Purpose                                      |
|---------------|--------------------|----------------------------------------------|
| UNSW-1        | OBC (primary)      | Ensures onboard processing is always available |
| UNSW-2        | RX (receiver)      | Ensures spacecraft is always commandable      |

After separation switch activation, the unswitchable lines power on automatically as part
of the POWER_STABILIZE phase, guaranteeing that the OBC and receiver are operational before
any ground contact.

#### Switchable Lines

Switchable PDM lines can be individually commanded on/off. Over-current protection is
implemented per outlet. These feed all other subsystem loads:

| Line          | Load               | Default State (after boot)  |
|---------------|--------------------|-----------------------------|
| SW-1          | Transmitter + PA   | OFF (enabled after 30-min timer) |
| SW-2          | AOCS sensors       | OFF (enabled during commissioning) |
| SW-3          | AOCS actuators     | OFF (enabled during commissioning) |
| SW-4          | Payload            | OFF (enabled for imaging)    |
| SW-5          | Heaters            | ON (battery heater)          |
| SW-6          | Redundant OBC      | OFF (standby)                |
| SW-7          | Redundant XPDR     | OFF (standby)                |

#### Cold-Redundant PDM Path

A redundant PDM path (PDM-B) provides an alternate power distribution path. PDM-B is
normally unpowered (cold standby) and can be activated by ground command if PDM-A develops
a fault. Both PDM paths share the same unswitchable/switchable line topology.

### 2.4 Dedicated PDM Command Channel

The transponder receiver is powered through a dedicated PDM command channel that is
independent of the main switchable line for the transmitter and power amplifier. This
design ensures that the spacecraft remains commandable even when the transmitter is off
(e.g., during the 30-minute separation timer). The dedicated command channel powers only
the receiver chain; the transmitter and power amplifier are on a separate switchable line
with a 15-minute auto-off timer after the last command decode to conserve power.

### 2.5 Separation Timer Circuit

The EPS includes a hardware separation timer circuit that enforces the 30-minute no-transmit
period after launcher separation:

1. Separation switches open upon release from the launcher.
2. The timer circuit inhibits all switchable PDM lines for 30 minutes.
3. Only unswitchable lines (OBC + RX) are active during this period.
4. After timer expiry, the OBC commands burn wire antenna deployment.
5. The transmitter switchable line is then enabled for beacon transmissions.

## 3. Power Modes

| Mode       | Loads Powered                                  | Typical Consumption |
|------------|------------------------------------------------|---------------------|
| NOMINAL    | All subsystems, payload active                 | ~80 W               |
| SAFE       | OBC, TTC, TCS heaters, AOCS (safe point)      | ~45 W               |
| EMERGENCY  | OBC, TTC essential only                        | ~25 W               |
| ECLIPSE    | Battery-only supply, non-essential loads shed   | ~60 W               |

During eclipse transitions, the PCDU autonomously switches from solar array to battery
supply. The `eclipse_flag` telemetry parameter (0x0108) indicates the current illumination
state.

## 4. Telemetry Parameters

| Param ID | Name            | Unit   | Description                          |
|----------|-----------------|--------|--------------------------------------|
| 0x0100   | bat_voltage     | V      | Battery terminal voltage             |
| 0x0101   | bat_soc         | %      | Battery state of charge              |
| 0x0102   | bat_temp        | deg C  | Battery pack temperature             |
| 0x0103   | sa_a_current    | A      | Solar array wing A current           |
| 0x0104   | sa_b_current    | A      | Solar array wing B current           |
| 0x0105   | bus_voltage     | V      | Main bus voltage (regulated)         |
| 0x0106   | power_cons      | W      | Total power consumption              |
| 0x0107   | power_gen       | W      | Total power generation               |
| 0x0108   | eclipse_flag    | bool   | Eclipse state (1 = in eclipse)       |
| 0x0109   | bat_current     | A      | Battery charge/discharge current     |
| 0x010A   | bat_capacity    | Ah     | Remaining battery capacity           |

## 5. Limit Definitions

| Parameter     | Yellow Low | Yellow High | Red Low | Red High |
|---------------|------------|-------------|---------|----------|
| bat_soc (%)   | 25         | 95          | 15      | 100      |
| bat_voltage (V)| 23        | 29          | 22      | 29.5     |
| bat_temp (C)  | 2          | 40          | 0       | 45       |
| bus_voltage (V)| 27        | 29          | 26.5    | 29.5     |

### 5.1 Limit Response

- **Yellow violation**: Operator warning; investigate trend.
- **Red violation (bat_soc < 15%)**: FDIR triggers autonomous load shedding and potential
  safe mode transition (see `08_fdir.md`).
- **Red violation (bat_temp)**: Heater control adjustment; if temperature exceeds 45 deg C,
  battery charging is inhibited.

## 6. Relevant Commands

| Command           | Service  | Description                           |
|-------------------|----------|---------------------------------------|
| HEATER_CONTROL    | S8,S1    | Control battery heater circuit        |
| OBC_SET_MODE      | S8,S1    | Transition to SAFE (triggers load shed)|
| HK_REQUEST        | S3,S27   | Request EPS housekeeping (SID=1)      |
| GET_PARAM         | S20,S3   | Read individual EPS parameter         |
| SET_PARAM         | S20,S1   | Modify EPS configuration parameter    |

### 6.1 Battery Heater Control

The battery heater is controlled via the `HEATER_CONTROL` command with `circuit=battery`
and `on=true/false`. The heater maintains battery temperature above 2 deg C during eclipse.
Autonomous heater control is implemented in the TCS FDIR logic and will activate the heater
if `bat_temp` falls below 5 deg C.

## 7. Operational Notes

1. During LEOP, solar array deployment is confirmed by monitoring `sa_a_current` and
   `sa_b_current`. A current reading above 0.5 A on each wing indicates successful
   deployment.
2. Battery reconditioning is not required for Li-Ion cells. The battery management system
   autonomously balances cells.
3. The `power_gen` vs. `power_cons` balance should be monitored each orbit. A sustained
   negative power margin indicates a configuration issue or degraded solar array performance.
4. The PCDU telemetry is sampled at 1 Hz and included in the EPS housekeeping packet
   (SID=1).

## 8. Failure Modes

| Failure                  | Detection                      | Autonomous Response              |
|--------------------------|--------------------------------|----------------------------------|
| Solar panel loss (single)| Panel current = 0 for >60s     | Load shedding if power margin negative |
| Solar panel degradation  | Reduced panel current vs. model| Operator warning; adjust power budget |
| Multiple panel loss      | Total power_gen < power_cons   | Progressive load shed, safe mode |
| Battery over-temperature | bat_temp > 45 C                | Inhibit charge, alert            |
| Bus under-voltage        | bus_voltage < 26.5 V           | Emergency mode transition        |
| PDM outlet trip          | Individual current monitoring  | Auto-retry once, then isolate    |
| PDM-A failure            | Multiple outlet trips          | Ground commands switch to PDM-B  |

### 8.1 Per-Panel Degradation and Loss

Because EOSAT-1 uses six body-mounted panels rather than deployable wings, the loss of a
single panel reduces total power generation by approximately 10-25% depending on which
panel is affected and the current attitude. The impact depends on which face is lost:

| Lost Panel | Impact                                                       |
|------------|--------------------------------------------------------------|
| +Y or -Y   | High impact: primary generation faces in dawn-dusk orbit     |
| -Z (zenith) | Moderate impact: significant solar exposure face            |
| +X or -X   | Low-moderate impact: secondary generation faces              |
| +Z (nadir)  | Low impact: primarily receives albedo illumination           |

Operators should monitor per-panel current telemetry and compare against the solar model
prediction for the current attitude. A sustained discrepancy exceeding 20% indicates panel
degradation requiring power budget re-assessment.

### 8.2 Progressive Load Shed Thresholds

When battery state of charge declines, the EPS FDIR implements progressive load shedding:

| SoC Threshold | Action                                              |
|---------------|-----------------------------------------------------|
| < 50%         | Payload operations inhibited                        |
| < 35%         | Non-essential loads shed (AOCS to SAFE_POINT)       |
| < 25%         | Safe mode entry                                     |
| < 15%         | Emergency mode (OBC + TTC only)                     |

See `08_fdir.md` for the complete FDIR response matrix.

---

*End of Document — EOSAT1-UM-EPS-002*

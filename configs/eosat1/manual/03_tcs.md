# EOSAT-1 Thermal Control Subsystem (TCS)

**Document ID:** EOSAT1-UM-TCS-004
**Issue:** 2.0
**Date:** 2026-03-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Overview

The Thermal Control Subsystem (TCS) maintains all spacecraft components within their
allowable temperature ranges throughout the mission. EOSAT-1 uses primarily passive thermal
control (MLI, radiators, surface coatings) combined with battery heaters as the only active
thermal control element. The OBC and other components rely on passive thermal management
through spacecraft orientation and internal heat dissipation. The focal plane array (FPA)
cooler is managed as part of the payload subsystem.

## 2. Passive Thermal Control

### 2.1 Multi-Layer Insulation (MLI)

MLI blankets cover the majority of the spacecraft external surfaces, providing thermal
isolation from the space environment. Key blanketed areas include:

- Spacecraft side panels (+/-X, +/-Y faces)
- Battery compartment
- OBC module enclosure
- Payload electronics bay

### 2.2 Radiators

Dedicated radiator panels are mounted on the -Y and +Y faces to reject waste heat.
The radiator sizing is optimised for the nominal power dissipation of ~80 W.

| Radiator        | Location | Area     | Reject Capacity |
|-----------------|----------|----------|-----------------|
| Primary (-Y)    | -Y face  | 0.25 m2  | ~50 W           |
| Secondary (+Y)  | +Y face  | 0.15 m2  | ~30 W           |

### 2.3 Surface Coatings

External surfaces use a combination of white paint (high emissivity) on radiators and
black Kapton on internal structure surfaces to promote radiative heat transfer within
the spacecraft cavity.

## 3. Active Thermal Control

### 3.1 Battery Heater Circuit

The battery heater is the only active thermal control element on EOSAT-1. All other
components (OBC, structure, transponder) rely on passive thermal management through
spacecraft orientation, internal heat dissipation, and MLI/radiator design.

| Circuit     | Location         | Setpoint On | Setpoint Off | Power  |
|-------------|------------------|-------------|--------------|--------|
| Battery     | Battery pack     | 5 deg C     | 10 deg C     | 8 W    |

The battery heater is controlled via the `HEATER_CONTROL` command (S8,S1) with parameters
`circuit=battery` and `on` (boolean). Autonomous heater control is implemented in the FDIR
logic and will activate the heater if battery temperature falls below 5 deg C, overriding
ground commands if temperatures approach red limits.

**Note:** Unlike the original design, EOSAT-1 does not carry OBC or thruster heater
circuits. The OBC is maintained within its thermal limits through passive means: internal
power dissipation provides self-heating, and the MLI blankets and radiator sizing maintain
the OBC within the qualified temperature range under all orbital conditions. There is no
propulsion system on EOSAT-1, so no thruster heater is required.

### 3.2 Orientation for Passive Thermal Control

EOSAT-1 uses spacecraft orientation as a passive thermal control strategy. The AOCS
attitude can be adjusted to present different faces to the Sun, allowing operators to
influence the thermal environment:

| Strategy                    | Effect                                          |
|-----------------------------|-------------------------------------------------|
| Nadir-point (nominal)       | Balanced thermal environment, standard ops      |
| Sun-point (+Y to Sun)       | Maximises solar heating on +Y, warms battery    |
| Eclipse-prep (thermal spin) | Distributes heat across faces before eclipse     |

This orientation-based thermal control is particularly important during LEOP and contingency
scenarios where active heater power must be conserved. In safe mode, the AOCS SAFE_POINT
mode orients the spacecraft towards the Sun, providing passive heating.

### 3.3 Heater Failure Modes

| Failure Mode    | Detection                           | Impact                          |
|-----------------|-------------------------------------|---------------------------------|
| Heater stuck_on | Battery temp rising above setpoint; `htr_battery` stays 1 | Excessive power draw, potential battery over-temp |
| Heater open_circuit | Battery temp dropping below setpoint; `htr_battery` = 1 but no temp response | Battery freeze risk in eclipse |

If a stuck-on heater is detected, ground should command `HEATER_CONTROL` (circuit=battery,
on=false) and monitor. If the heater remains on, the PDM outlet feeding the heater circuit
can be commanded off as an isolation measure.

### 3.4 FPA Cooler

The focal plane array cooler is a miniature Stirling-cycle cryocooler that maintains the
imager detector at its operational temperature of approximately -15 deg C. The cooler
draws up to 15 W and requires a 30-minute cooldown period from ambient temperature.

| Parameter         | Value                        |
|-------------------|------------------------------|
| Cooler Type       | Stirling-cycle cryocooler    |
| Target FPA Temp   | -15 deg C (nominal)          |
| Cooldown Time     | ~30 min from ambient         |
| Power Consumption | 10–15 W                      |
| Control           | Autonomous via payload mode   |

The cooler is activated automatically when the payload transitions to STANDBY or IMAGING
mode and deactivated when the payload is set to OFF.

## 4. Temperature Zones

| Zone           | Nominal Range    | Yellow Limits    | Red Limits       |
|----------------|------------------|------------------|------------------|
| Battery        | 5–35 deg C       | 2–40 deg C       | 0–45 deg C       |
| OBC            | 15–50 deg C      | 5–60 deg C       | 0–70 deg C       |
| FPA Detector   | -18 to -12 deg C | -18 to 8 deg C   | -20 to 12 deg C  |
| Solar Panels   | -80 to 80 deg C  | —                | —                |
| Structure      | -20 to 50 deg C  | —                | —                |

## 5. Telemetry Parameters

| Param ID | Name            | Unit   | Description                          |
|----------|-----------------|--------|--------------------------------------|
| 0x0400   | temp_panel_px   | deg C  | +X panel temperature                 |
| 0x0401   | temp_panel_mx   | deg C  | -X panel temperature                 |
| 0x0402   | temp_panel_py   | deg C  | +Y panel temperature                 |
| 0x0403   | temp_panel_my   | deg C  | -Y panel temperature                 |
| 0x0404   | temp_panel_pz   | deg C  | +Z panel temperature (nadir)         |
| 0x0405   | temp_panel_mz   | deg C  | -Z panel temperature (zenith)        |
| 0x0406   | temp_obc        | deg C  | OBC module temperature               |
| 0x0407   | temp_battery    | deg C  | Battery pack temperature             |
| 0x0408   | temp_fpa        | deg C  | Focal plane array temperature        |
| 0x0409   | temp_thruster   | deg C  | Thruster valve temperature           |
| 0x040A   | htr_battery     | bool   | Battery heater status (on/off)       |
| 0x040B   | htr_obc         | bool   | OBC heater status (on/off)           |
| 0x040C   | cooler_fpa      | bool   | FPA cooler status (on/off)           |

## 6. Limit Definitions

| Parameter       | Yellow Low | Yellow High | Red Low | Red High |
|-----------------|------------|-------------|---------|----------|
| temp_obc (C)    | 5          | 60          | 0       | 70       |
| temp_battery (C)| 2          | 40          | 0       | 45       |
| temp_fpa (C)    | -18        | 8           | -20     | 12       |

## 7. Commands

| Command           | Service  | Parameters              | Description                        |
|-------------------|----------|-------------------------|------------------------------------|
| HEATER_CONTROL    | S8,S1    | circuit, on             | Enable/disable heater circuit      |
| HK_REQUEST        | S3,S27   | sid=4                   | Request TCS housekeeping packet    |
| GET_PARAM         | S20,S3   | param_id (0x0400–040C)  | Read individual TCS parameter      |
| SET_PARAM         | S20,S1   | param_id, value         | Modify TCS configuration parameter |

## 8. Panel Temperature Coupling to Solar Illumination

The six body-mounted solar panels act as the primary thermal interface between the
spacecraft and the space environment. Panel temperatures are strongly coupled to solar
illumination:

| Condition            | Panel Response                                       |
|----------------------|------------------------------------------------------|
| Sunlit (direct)      | Panel temp rises towards +80 deg C (steady state)    |
| Sunlit (oblique)     | Reduced heating; temp proportional to cos(sun angle) |
| Eclipse (shadowed)   | Rapid cooling towards -80 deg C at ~5 deg C/min      |
| Earth albedo only    | Mild heating on nadir face (+Z)                      |

The thermal coupling mechanism works in both directions:

1. **Solar heating**: Direct solar flux heats the illuminated panel, which conducts heat
   into the spacecraft structure and adjacent components.
2. **Radiative cooling**: Shadowed panels radiate heat to deep space, drawing heat from
   the spacecraft interior.

This coupling is significant for thermal management because the solar panel temperatures
directly influence the battery compartment temperature (adjacent to the +Y/-Y panels) and
the OBC module temperature. Operators should monitor panel temperatures during attitude
manoeuvres to anticipate thermal transients.

## 9. Operational Notes

1. Eclipse entry causes a rapid temperature drop on exposed panels. The thermal gradient
   can reach 5 deg C/min on directly exposed surfaces.
2. Battery heater activation during eclipse is critical to maintain cell temperature above
   the minimum qualification limit of 0 deg C.
3. The FPA cooler should not be activated until the payload electronics have completed
   their power-on self-test (approximately 10 seconds after STANDBY command).
4. Panel temperatures (0x0400–0x0405) are informational and not subject to FDIR action.
   They are useful for thermal model correlation during commissioning and for predicting
   internal component temperatures based on the solar illumination pattern.
5. In SAFE mode, only the battery heater remains active. The FPA cooler is powered off
   to conserve energy. The OBC relies on passive thermal control (self-heating from
   internal power dissipation).
6. Spacecraft orientation directly affects the thermal environment. During contingency
   operations, the Flight Director should coordinate with Power/Thermal and AOCS operators
   to select an attitude that balances power generation and thermal constraints.

## 10. Thermal Design Margins

The thermal design provides at least 5 deg C margin to qualification limits under worst-case
hot and cold conditions (end-of-life, worst-case eclipse duration, maximum/minimum solar flux).
Margin verification is based on the thermal mathematical model correlated during the
commissioning phase.

---

*End of Document — EOSAT1-UM-TCS-004*

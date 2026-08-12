# EOSAT-1 Electrical Power Subsystem (EPS)

**Document ID:** EOSAT1-UM-EPS-002
**Issue:** 2.0
**Date:** 2026-03-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Overview

The Electrical Power Subsystem (EPS) generates, stores, regulates, and distributes electrical
power to all spacecraft subsystems. It comprises six body-mounted GaAs solar panels (one per
spacecraft face), two deployable solar wings on the +/-Y faces, a lithium-ion battery, a
cold-redundant Power Distribution Module (PDM) with switchable and unswitchable lines, and
associated harness.

## 2. Architecture

### 2.1 Solar Panels — Body-Mounted Panels and Deployable Wings

| Parameter            | Value                      |
|----------------------|----------------------------|
| Configuration        | 5 body-mounted panels + 2 deployable wings (+/-Y) on a single-axis solar array drive |
| Cell Technology      | Triple-junction GaAs (29.5 % cell efficiency) |
| Body-mounted area    | 0.198 m2 total (stowed configuration) |
| Deployed total area  | 0.438 m2 (body panels + both wings) |
| Wing area            | 0.12 m2 of cells per wing (+Y and -Y, 2 folding panels of 209 x 342 mm) |
| Wing assembly mass   | ~1.07 kg per wing incl. hinges and HDRM |
| Peak Power (BOL)     | ~89 W instantaneous, wings sun-tracking at beta = 0, fully deployed (wings alone 70 W, matching the MMA eHaWK 6U class rating) |
| Orbit-average (BOL, nadir) | ~55 W at beta = 24 deg, ~42 W at beta = 50 deg (incl. eclipse) |
| EOL performance      | K = 267.2 W/m2 of projected area (full loss stack, 3-year EOL, sa_age_factor 0.9175) |
| Operating Voltage    | 28 V (regulated bus)       |
| Wing deploy time     | ~30 s per wing (burn wire release) |

Five spacecraft faces carry body-mounted solar panels (the -Z face carries the
imager aperture and radiator and has no cells). Deployable solar wings are
mounted on the +Y and -Y faces on a shared single-axis solar array drive
(SADA) whose rotation axis is body Y — the orbit normal in the LVLH
nadir-pointing frame (z = nadir, y = -orbit normal). When stowed, the
total solar collection area is 0.198 m2 (body panels only). After wing
deployment the total area is 0.438 m2 (0.12 m2 per wing added). Power
generation per body panel depends on the cosine projection of the Sun vector
onto the panel normal; the wing pair, when the SADA is tracking, follows the
Sun's projection perpendicular to the drive axis continuously (a cos-beta
law, independent of orbit phase):

| Panel Face | Orientation (nadir-pointing frame) | Notes                              |
|------------|------------------------------------|-------------------------------------|
| +X         | Orbit plane                        | Swept through the sun line each orbit |
| -X         | Orbit plane                        | Swept through the sun line each orbit |
| +Y         | Orbit plane (wing on SADA)         | Wing tracks the sun when SADA enabled |
| -Y         | Orbit plane (wing on SADA)         | Wing tracks the sun when SADA enabled |
| +Z         | Orbit normal                       | Continuous sin-beta illumination     |
| -Z         | Anti orbit-normal                  | Imager aperture + radiator, no cells |

The per-panel power output is computed as:

```
P_panel = A_panel * K * max(0, cos(angle_to_sun)) * degradation_factor
```

where `K` is the effective plane-of-array constant including the full loss
stack (inherent degradation/packing 0.85, temperature at 65 C, MPPT 0.97,
harness 0.95, and aging), and `degradation_factor` accounts for panel damage.
Two distinct SADA fault cases, both real and both diagnosable:

* **Drive powered OFF** (S2 device 0x0101). The wings stop contributing, but
  the five BODY-mounted panels are bonded to the structure and wired straight
  to the MPPT — they need no drive and keep producing (~15-18 W sunlit).
  Generation collapsing to *exactly zero* would mean a total array loss, not a
  drive fault; the residual body generation is what tells the two apart.
* **Drive STUCK** (`sada_stuck` failure injection, or `sada_tracking`
  disabled). The shaft freezes where it is, so `solar_array_drive_angle`
  (0x0133) goes flat while the Sun walks away from the frozen normal. The
  wings revert to the fixed-wing |sin(orbit phase)| duty — roughly a 36 %
  orbit-average generation loss, with a characteristic once-per-orbit ripple
  on the per-wing currents 0x0146 / 0x0147.

The drive is rate-limited to `sada_max_rate_deg_s` (1.0 deg/s). Sun tracking
needs one revolution per orbit = 0.064 deg/s, so there is ~15x margin in
nominal ops; a commanded reposition of 90 deg takes ~90 s and is visible as a
ramp on 0x0133.

### 2.1.1 Deployable Solar Wings

The spacecraft carries two deployable solar wings, one on each of the +Y and -Y faces.
Each wing adds 0.12 m2 of solar collection area and is held in the stowed position by a
burn wire restraint mechanism. Deployment is commanded via S8 function management:

| Parameter            | Value                      |
|----------------------|----------------------------|
| Wing location        | +Y face and -Y face (shared SADA shaft) |
| Area per wing        | 0.12 m2 of cells (0.143 m2 geometric, ~84 % packing) |
| Deploy mechanism     | Burn wire (resistive heating) |
| Deploy time          | ~30 seconds per wing       |
| Deploy command       | EPS_DEPLOY_WING (func_id 81) |
| Status query         | EPS_WING_STATUS (func_id 82) |
| Status telemetry     | wing_status (0x0144) — bitmask: bit 0 = +Y, bit 1 = -Y |
| Deploy timer TM      | wing_deploy_timer (0x0145) — countdown in seconds |
| Deploy events        | 0x0112 (+Y deployed), 0x0113 (-Y deployed) |

**Deployment procedure:**

1. Verify spacecraft attitude is stable (body rates < 0.5 deg/s).
2. Verify battery SoC > 50% (deployment consumes ~2 A for 30 s per wing).
3. Send `EPS_DEPLOY_WING` with `wing=0` (+Y), `wing=1` (-Y), or `wing=2` (both).
4. Monitor `wing_deploy_timer` (0x0145) counting down from 30 s.
5. Confirm deployment via `wing_status` (0x0144) bitmask and event generation.
6. Verify increased power generation on the corresponding +Y / -Y panel currents.

**Constraints:**
- Wings should be deployed after antenna deployment and initial detumble.
- Deploy one wing at a time when possible to manage attitude disturbance.
- If a wing fails to deploy, a retry can be commanded after 60 s cooldown.

### 2.2 Battery

| Parameter            | Value                      |
|----------------------|----------------------------|
| Chemistry            | Lithium-Ion (Li-Ion)       |
| Nominal Voltage      | 28 V                       |
| Capacity             | 100 Wh (3.6 Ah at 28 V)    |
| Reference part       | GomSpace NanoPower BPX, 8S-1P: 100 Wh, 3 Ah, 28.8 V nominal, ~500 g, 18650 cells |
| Coulombic efficiency | 0.94 (charge acceptance)   |
| Depth of Discharge   | Max 35% (nominal ops)      |
| Charge Method        | CC/CV taper above 90 % SoC, thermal cutoff |

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
| NOMINAL    | All subsystems, payload standby                | ~29 W orbit-average |
| NOMINAL+IMAGING | As above plus imager and FPA cooler on duty | ~33 W orbit-average |
| SAFE       | OBC, TTC, TCS heaters, AOCS (safe point)       | ~20 W               |
| EMERGENCY  | OBC, TTC essential only                        | ~11 W               |
| ECLIPSE    | Battery-only supply, non-essential loads shed  | ~25 W               |

### 3.1 Power Budget Breakdown (Nominal Subsystem Consumption)

Every wattage below is the value `subsystems/eps.yaml` bills and is derived
from a named flight part — see `docs/plan_2026-08b/POWER_SIZING.md` for the
research, citations and the "part class -> flight heritage -> value adopted"
table. Ruling R1: *"a CubeSat would generally not have a 20 W transmitter and
would struggle to generate 114 W peak"* — correct on both counts, and this
table is what was wrong, not the model.

| Subsystem          | Typical Power | Notes                             |
|--------------------|---------------|-----------------------------------|
| OBC (primary)      | 6 W           | OBC 3 W + PDU/EPS housekeeping 3 W |
| TTC receiver (RX)  | 1.5 W         | Always on (unswitchable). Endurosat S-band idle/Rx 1.2 W + ~0.3 W UHF beacon Rx |
| TTC transmitter+PA | 5 W LOW / 11 W HIGH tier | On during contacts only. 2 W RF (33 dBm) at the HIGH tier — Endurosat S-band transceiver, 11 W DC at 33 dBm, 1.2 W idle |
| AOCS suite         | 12 W line rating; 4 / 9 / 15 W billed by mode (COARSE / NOMINAL / FINE) | Billed from the AOCS model's live draw at 0x02C8, not the line rating |
| Payload (imaging)  | 5.5 W         | Simera Sense TriScape100 imaging mode, 1100 mA at 5 V |
| Payload (standby)  | 2.4 W         | TriScape100 idle/readout, 470 mA at 5 V |
| FPA cooler         | 15 W          | Thermo-electric (Peltier) stack, own power line |
| Battery heater     | 8 W           | Thermostat ON +5 C / OFF +10 C    |
| OBC heater         | 4 W           | Manual control                    |
| Propulsion         | 12 W catbed + 2 W valves, when armed | Line OFF outside manoeuvre preparation |

**Standby must be below imaging.** Until 2026-08b this table and eps.yaml
disagreed with each other and with physics: standby was billed at 6 W against
5 W for imaging, i.e. the imager drew *less* while imaging. Both ends are now
a TriScape100 datasheet figure, and
`tests/test_content/test_eps_config_manual_consistency.py` asserts the
ordering as well as the values.

### 3.2 Orbit-Average Budget (worst beta, EOL)

Produced by `tools/power_budget.py`, which ticks the production EPS and TCS
models over the real propagated orbit — not a spreadsheet.

| Profile | Generation | Load | Margin | Ratio |
|---------|-----------|------|--------|-------|
| quiescent        | 38.0 W | 25.5 W | +12.5 W | 1.49 |
| nominal          | 38.0 W | 28.7 W | +9.3 W  | 1.33 |
| nominal + imaging| 38.0 W | 32.5 W | +5.5 W  | 1.17 |
| manoeuvre        | 38.0 W | 32.0 W | +6.0 W  | 1.19 |

The acceptance criterion — orbit-average generation exceeds orbit-average load
with ALL nominal systems on, including ADCS — closes at 1.33x in NOMINAL nadir
pointing at the worst beta angle of the year (49.8 deg) at 3-year EOL.

**Imaging is duty-cycled, not continuous.** The budget above assumes three
90-second captures per orbit with the FPA cooler running for ~20 minutes
around them. Holding FINE pointing and the cooler for a whole orbit
(the `worst` profile) is power-NEGATIVE by design: it is the exercise, not a
sizing failure.

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
| 0x0144   | wing_status     | bitmask| Solar wing deployment status (bit0=+Y, bit1=-Y) |
| 0x0145   | wing_deploy_timer| s     | Wing deployment countdown timer      |

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
| EPS_DEPLOY_WING   | S8,S1    | Deploy solar wing (func_id 81)        |
| EPS_WING_STATUS   | S8,S1    | Query wing deployment status (func_id 82) |

### 6.1 Battery Heater Control

The battery heater is controlled via the `HEATER_CONTROL` command with `circuit=battery`
and `on=true/false`. The heater maintains battery temperature above 2 deg C during eclipse.
Autonomous heater control is implemented in the TCS FDIR logic and will activate the heater
if `bat_temp` falls below 5 deg C.

## 7. Operational Notes

1. During LEOP, body-mounted solar array operation is confirmed by monitoring `sa_a_current`
   and `sa_b_current`. Deployable wing deployment is confirmed by monitoring `wing_status`
   (0x0144) and the corresponding panel currents. A current increase on the +Y / -Y panel
   after wing deployment indicates successful extension.
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

EOSAT-1 uses six body-mounted panels plus two deployable wings on the +/-Y faces. The loss
of a single panel or wing reduces total power generation depending on which element is
affected and the current attitude. The impact depends on which face is lost:

| Lost Element       | Impact                                                       |
|--------------------|--------------------------------------------------------------|
| +Y or -Y wing      | High impact: major generation area in dawn-dusk orbit        |
| +Y or -Y body panel| Moderate-high impact: primary generation faces               |
| -Z (zenith)        | Moderate impact: significant solar exposure face             |
| +X or -X           | Low-moderate impact: secondary generation faces              |
| +Z (nadir)         | Low impact: primarily receives albedo illumination           |

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

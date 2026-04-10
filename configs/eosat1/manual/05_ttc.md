# EOSAT-1 Telemetry, Tracking and Command (TTC)

**Document ID:** EOSAT1-UM-TTC-006
**Issue:** 2.0
**Date:** 2026-03-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Overview

The Telemetry, Tracking and Command (TTC) subsystem provides the radio frequency
communication link between EOSAT-1 and the ground segment. It supports telecommand
reception, telemetry transmission, and ranging for orbit determination. The subsystem
comprises a cold-redundant VHF/UHF transponder pair with a dedicated PDM command channel
for the receiver, burn wire deployable antennas, and low-rate/high-rate data modes.

## 2. Architecture

### 2.1 Transponder Configuration

| Parameter           | Primary          | Redundant        |
|---------------------|------------------|------------------|
| Uplink Frequency    | 449.0 MHz (UHF)  | 449.0 MHz (UHF)  |
| Downlink Frequency  | 401.5 MHz (UHF)  | 401.5 MHz (UHF)  |
| Uplink Data Rate    | 4 kbps           | 4 kbps           |
| Downlink (Low-Rate) | 1 kbps           | 1 kbps           |
| Downlink (High-Rate)| 64 kbps          | 64 kbps          |
| Modulation          | BPSK (TC) / QPSK (TM) | BPSK / QPSK |
| RF Output Power     | 2 W              | 2 W              |
| Redundancy          | Cold standby     | —                |

Only one transponder is active at any time. The redundant unit is powered off (cold standby)
and can be activated by ground command via `TTC_SWITCH_REDUNDANT`.

### 2.2 Dedicated PDM Command Channel

The transponder receiver is powered through a dedicated PDM command channel, independent of
the main switchable PDM line that feeds the transmitter and power amplifier. This critical
design feature ensures the spacecraft is always commandable:

| Component          | Power Source                | Always On? |
|--------------------|-----------------------------|------------|
| Receiver (RX)      | Unswitchable PDM (UNSW-2)   | Yes        |
| Transmitter (TX)   | Switchable PDM (SW-1)       | No         |
| Power Amplifier    | Switchable PDM (SW-1)       | No         |

After the last valid command decode, a 15-minute auto-off timer begins for the TX and PA
switchable line. If no further commands are received within 15 minutes, the transmitter
and power amplifier are automatically powered off to conserve energy. The receiver remains
powered indefinitely via the unswitchable PDM line.

This timer mechanism is particularly important during the 30-minute separation hold period
when the TX must remain off, while the RX is ready to receive ground commands immediately
after antenna deployment.

### 2.3 Antenna System — Burn Wire Deployment

| Antenna              | Type          | Coverage        | Gain     |
|----------------------|---------------|-----------------|----------|
| Zenith (-Z) Patch    | Patch array   | Hemispherical   | 3 dBi    |
| Nadir (+Z) Patch     | Patch array   | Hemispherical   | 3 dBi    |

The antennas are stowed during launch and deployed after the 30-minute separation timer
expires using a burn wire mechanism:

1. **Pre-deployment**: Antennas are held in the stowed position by a restraining wire.
2. **Burn command**: After the separation timer expires, the OBC commands the burn wire
   circuit, which passes current through a resistive wire melting the restraint.
3. **Deployment**: Spring-loaded hinges deploy the antenna elements into their operational
   configuration.
4. **Verification**: Deployment is confirmed by monitoring RSSI improvement and deployment
   microswitch telemetry.

The burn wire deployment is a one-time, irreversible operation. If the primary burn wire
fails, a redundant burn wire circuit is available.

### 2.4 Data Rate Modes

EOSAT-1 supports two downlink data rate modes:

| Mode      | Data Rate | Use Case                                      |
|-----------|-----------|-----------------------------------------------|
| Low-Rate  | 1 kbps    | Beacon mode, bootloader, low-margin contacts  |
| High-Rate | 64 kbps   | Nominal operations, stored TM/data downlink   |

**Low-Rate Mode (1 kbps):** Used during LEOP beacon transmissions and for contacts with
marginal link margin (low elevation passes). The reduced data rate provides additional
link margin of approximately 18 dB compared to high-rate mode.

**High-Rate Mode (64 kbps):** Used during nominal operations for housekeeping telemetry
downlink and stored payload/science data dump. Requires adequate link margin (typically
passes with maximum elevation above 10 deg).

Mode switching is commanded via `TTC_SET_RATE` or occurs automatically based on link
margin thresholds.

### 2.5 Beacon Packet Mode

During LEOP and BOOTLOADER operations, the TTC transmits beacon packets at low rate:

| Parameter          | Value                                      |
|--------------------|--------------------------------------------|
| Beacon Rate        | 1 packet every 100 seconds (0.01 Hz)       |
| Data Rate          | 1 kbps (low-rate mode)                     |
| Content            | SID 11 — minimal OBC health telemetry      |
| Modulation         | BPSK                                       |

The beacon packet allows ground stations to acquire and identify the spacecraft signal
during first contact. The beacon contains sufficient information to assess basic spacecraft
health and initiate recovery commanding.

## 3. Link Budget Summary

### 3.1 Uplink (Ground to Spacecraft)

| Parameter            | Value              |
|----------------------|--------------------|
| Ground Station EIRP  | 53 dBW             |
| Free Space Loss      | -162.5 dB (450 km) |
| S/C Antenna Gain     | 3 dBi              |
| System Noise Temp    | 800 K              |
| Required Eb/N0       | 9.6 dB             |
| Link Margin (min)    | > 6 dB at 5 deg    |

### 3.2 Downlink (Spacecraft to Ground)

| Parameter            | High-Rate (64 kbps) | Low-Rate (1 kbps)  |
|----------------------|---------------------|---------------------|
| S/C EIRP             | 33 dBW              | 33 dBW              |
| Free Space Loss      | -162.5 dB (450 km)  | -162.5 dB (450 km)  |
| Ground Station G/T   | 20 dB/K             | 20 dB/K             |
| Required Eb/N0       | 9.6 dB              | 9.6 dB              |
| Data Rate Margin     | —                   | +18 dB (vs high)    |
| Link Margin (min)    | > 3 dB at 5 deg     | > 21 dB at 5 deg    |

Link margin decreases with lower elevation angles. At the minimum elevation of 5 deg,
the path loss increases and atmospheric effects become more significant. The low-rate
mode provides substantial additional margin for beacon operations and low-elevation
contacts.

## 4. Telemetry Parameters

| Param ID | Name              | Unit   | Description                          |
|----------|-------------------|--------|--------------------------------------|
| 0x0500   | ttc_mode          | enum   | TTC mode (active transponder)        |
| 0x0501   | link_status       | enum   | Link state (0=no link, 1=locked)     |
| 0x0502   | rssi              | dBm    | Received signal strength indicator   |
| 0x0503   | link_margin       | dB     | Current link margin                  |
| 0x0506   | tm_data_rate      | kbps   | Current TM downlink data rate        |
| 0x0507   | xpdr_temp         | deg C  | Active transponder temperature       |
| 0x0509   | range_km          | km     | Slant range to active ground station |
| 0x050A   | contact_elevation | deg    | Elevation of active ground station   |

## 5. Ground Station Contacts

| Station    | Latitude   | Longitude  | Min Elev. | Typical Pass Duration |
|------------|------------|------------|-----------|----------------------|
| Iqaluit    | 63.747 N   | 68.518 W   | 5 deg     | 5–12 min             |
| Troll      | 72.012 S   | 2.535 E    | 5 deg     | 5–10 min             |

EOSAT-1 uses a two-station ground network providing bipolar coverage:

### 5.1 Iqaluit Ground Station (Nunavut, Canada)

| Parameter          | Value                                      |
|--------------------|--------------------------------------------|
| Location           | 63.747 deg N, 68.518 deg W                 |
| Antenna Diameter   | 3.7 m VHF/UHF                               |
| Min. Elevation     | 5 deg                                      |
| Contacts/Day       | 2–4 (typical)                              |
| Pass Duration      | 5–12 min                                   |
| Primary Role       | TT&C + science data downlink               |

Iqaluit provides northern hemisphere coverage. Its sub-Arctic latitude ensures multiple
daily contacts with the 450 km, 98 deg inclination orbit.

### 5.2 Troll Ground Station (Queen Maud Land, Antarctica)

| Parameter          | Value                                      |
|--------------------|--------------------------------------------|
| Location           | 72.012 deg S, 2.535 deg E                  |
| Antenna Diameter   | 3.7 m VHF/UHF                               |
| Min. Elevation     | 5 deg                                      |
| Contacts/Day       | 2–4 (typical)                              |
| Pass Duration      | 5–10 min                                   |
| Primary Role       | TT&C + science data downlink               |

Troll provides southern hemisphere coverage, reducing the maximum gap between contacts
compared to a single northern station.

### 5.3 Per-Station Link Budget

Link margin varies between stations due to different slant range geometries:

| Parameter             | Iqaluit            | Troll              |
|-----------------------|--------------------|---------------------|
| Typical max elevation | 20–70 deg          | 20–60 deg           |
| Best-case margin      | > 10 dB            | > 10 dB             |
| Worst-case margin     | ~3 dB (5 deg elev) | ~3 dB (5 deg elev)  |
| Free space loss (450 km) | -162.5 dB       | -162.5 dB           |

EOSAT-1 typically has 4–8 ground contacts per day across both stations, providing
cumulative contact time of 30–60 minutes for telecommand uplink and telemetry/data
downlink.

## 6. Commands

| Command               | Service  | Parameters | Description                          |
|-----------------------|----------|------------|--------------------------------------|
| TTC_SWITCH_PRIMARY    | S8,S1    | —          | Activate primary transponder         |
| TTC_SWITCH_REDUNDANT  | S8,S1    | —          | Activate redundant transponder       |
| HK_REQUEST            | S3,S27   | sid=5      | Request TTC housekeeping packet      |
| GET_PARAM             | S20,S3   | param_id   | Read individual TTC parameter        |
| SET_PARAM             | S20,S1   | param_id, value | Modify TTC configuration parameter |

### 6.1 Transponder Switchover Procedure

1. Verify current transponder health via `HK_REQUEST` (sid=5).
2. Send `TTC_SWITCH_REDUNDANT` (or `TTC_SWITCH_PRIMARY`) command.
3. Wait for transponder warm-up (approximately 30 seconds).
4. Confirm link re-acquisition via `link_status` (0x0501) = 1.
5. Verify `rssi` and `link_margin` are within acceptable bounds.

**Note:** A transponder switchover will cause a temporary link outage of 20–40 seconds.
Schedule switchover commands with sufficient contact time remaining.

## 7. Operational Notes

1. The TTC subsystem enters receive-only mode during spacecraft safe mode to conserve
   power. Telemetry transmission continues but at low rate (1 kbps).
2. The onboard GPS receiver provides position and velocity data for orbit determination,
   supplemented by ranging measurements (0x0509) during ground contacts.
3. RSSI values below -100 dBm indicate marginal link conditions. Operations should be
   restricted to high-priority commands at low RSSI. Consider switching to low-rate mode.
4. The transponder temperature (0x0507) should remain within 0–50 deg C during operation.
   Thermal protection is provided by passive means (TCS radiator and MLI).
5. Stored telemetry and payload data are downlinked during contacts using high-rate mode
   (64 kbps). Real-time HK telemetry is interleaved with stored data.
6. The dedicated PDM command channel ensures the receiver remains powered even when the
   transmitter auto-off timer expires. This is by design — the spacecraft is always
   commandable.
7. After antenna deployment (burn wire), verify RSSI improvement from pre-deployment
   levels. A marginal improvement may indicate incomplete deployment.
8. During LEOP first contact, expect beacon packets at 1 kbps low-rate. The ground station
   should be configured for low-rate acquisition before switching to high-rate after
   application boot.

## 8. Failure Modes

| Failure                     | Detection                   | Response                        |
|-----------------------------|-----------------------------|---------------------------------|
| Primary transponder failure | link_status = 0 for > 120s | Switch to redundant (auto FDIR) |
| RSSI degradation            | rssi < -105 dBm            | Switch to low-rate, investigate |
| Antenna deployment failure  | No RSSI after burn wire     | Try redundant burn wire circuit |
| TX auto-off (15 min timer)  | No downlink, RX still active| Expected behaviour; send command to re-enable TX |
| No TM from either station   | Both Iqaluit and Troll report no signal | Investigate attitude; check transponder health |

---

*End of Document — EOSAT1-UM-TTC-006*

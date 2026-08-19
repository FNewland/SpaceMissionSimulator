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
| Uplink Frequency    | 2025.0 MHz (S-band) | 2025.0 MHz (S-band) |
| Downlink Frequency  | 2200.0 MHz (S-band) | 2200.0 MHz (S-band) |
| Uplink Data Rate    | 4 kbps (fixed)   | 4 kbps (fixed)   |
| Downlink (SAFE)     | 1 kbps           | 1 kbps           |
| Downlink (LOW)      | 16 kbps          | 16 kbps          |
| Downlink (HIGH)     | 128 kbps         | 128 kbps         |
| Modulation          | BPSK (TC) / BPSK or QPSK (TM, per tier) | same |
| RF Output Power     | 2 W              | 2 W              |
| Redundancy          | Cold standby     | —                |

The three downlink rates are the rungs of the **TM data-rate ladder**
(§2.4). The telecommand uplink is a fixed 4 kbps and is *not* on the ladder:
raising the downlink rate never raises the uplink rate.

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

### 2.4 The TM Data-Rate Ladder

EOSAT-1 does not have "a downlink rate". It has a three-rung **ladder**, defined once in
`configs/eosat1/subsystems/ttc.yaml` under `tm_rate_tiers` and read from there by the
spacecraft, the RF chain, the mission planner and the console. Each rung carries its own
modulation, its own coding and its own permission gates.

| Tier | Rate | Modulation / coding | Symbol rate | Occupied BW | Purpose |
|------|------|---------------------|-------------|-------------|---------|
| SAFE | 1 kbps | BPSK, conv(7,1/2)+RS | 1 kBd | 3.1 kHz | Bootloader / contingency lifeline (renamed from BEACON, 2026-08c) |
| LOW | 16 kbps | BPSK, conv(7,1/2)+RS | 16 kBd | 49.8 kHz | **Routine operations** and the safe-mode / FDIR fallback |
| HIGH | 128 kbps | QPSK, RS(255,223) | 64 kBd | 99.5 kHz | Delayed-telemetry playback and image dumps only |

**LOW (16 kbps) is where the spacecraft normally sits.** This is the single most
important thing to understand about the ladder. The periodic housekeeping set (SIDs 1-6)
generates 1351.5 bps of PUS packets, which is 1557 bps of channel after RS(255,223) and
TM transfer framing. LOW carries that at under 10 % duty *and* plays back roughly
0.85 orbits of stored housekeeping in a single good pass, so a spacecraft at LOW never
falls behind. Nothing is degraded about routine operations at LOW; the antenna may even
be stowed.

**SAFE (1 kbps) is a lifeline, not an operations rate.** (This rung was renamed from
BEACON in 2026-08c: "beacon" now always means the UHF whip transmitter, the second
radio.) 1000 bps is *below* the
1557 bps housekeeping generation rate, so a spacecraft held at SAFE accumulates a
real-time backlog that can never drain. It exists to carry SID 11 (28 bytes every 30 s =
7.5 bps) out of the bootloader with over 100x headroom and the narrowest possible
occupied bandwidth. It is entered by `TTC_SET_BEACON_MODE` (func 70) or by the
bootloader — never by `TTC_SET_DATA_RATE`.

**HIGH (128 kbps) is earned, not selected.** On board it is refused unless BOTH
gates the spacecraft can itself know are satisfied:

1. the antenna is deployed (`ttc.antenna_deployed`, 0x0520 = 1);
2. DOWNLINK / payload-dump mode is on (`TTC_SET_DOWNLINK_MODE`, func 79;
   `ttc.downlink_mode`, 0x053C = 1).

A tier whose gates are open is **rejected** with an S1.8 execution failure
(error 0x0007), never silently clamped — the failure text names the open gate.
(While beacon MODE is latched — func 70 — every tier except SAFE is refused
with the same 0x0007.)

**The 10 deg elevation rule is a GROUND policy, not an on-board gate**
(2026-08c): a real spacecraft does not know its elevation as seen from a
station, so the flight software neither refuses nor demotes HIGH on elevation.
The policy number lives in `subsystems/ttc.yaml`
`tm_rate_tiers[HIGH].min_elevation_deg` (10 deg) and is enforced by the
GROUND segment: the mission planner schedules HIGH dumps above it, and the
MCS commanding page raises an arm-through hold when HIGH is commanded below
it, computed from the ground's own contact geometry. Below 10 deg the physics
still bites honestly — cosec-scaled atmospheric loss and the G/T de-rate cost
real margin and raise the BER the ground sees.

**Commanding.** `TTC_SET_DATA_RATE` (Service 8, Subtype 1, **func 65**) takes one uint8
tier code. The legacy binary encoding is preserved verbatim: `rate=0` is "the low rate"
and selects LOW, `rate=1` is "the high rate" and selects HIGH. Existing procedures and
the FDIR `ttc_reduce_power` action therefore keep their meaning and simply land on usable
rates.

**Active vs commanded.** The gates act on the ACTIVE tier, never on the COMMANDED one.
`ttc.tm_tier_cmd` (0x053A) is what the ground asked for; `ttc.tm_tier` (0x0539) is what
is on air. They diverge when a gate closes — most commonly as a pass descends below
10 deg — and converge again when it reopens, without re-commanding (gate closures are the
antenna, DOWNLINK mode or the beacon-mode latch — never elevation). A console that shows
only one of the two turns a diagnosable condition into a mystery.

**Rate and margin.** Each rung costs `10*log10(R2/R1)` of Eb/N0: SAFE to LOW is
12.04 dB, LOW to HIGH is 9.03 dB. Coding adds to that — conv(7,1/2)+RS is worth 7.5 dB
against RS(255,223)'s 6.0 dB — so stepping down from HIGH to LOW recovers 9.03 + 1.5 =
10.5 dB in total. See `docs/LINK_BUDGET_AND_RATES.md` for the derivation and the
per-station table, or run `python tools/link_budget_ladder.py`.

### 2.5 Beacon Packet Mode

During LEOP and BOOTLOADER operations, the TTC transmits beacon packets on the SAFE
rung of the ladder:

| Parameter          | Value                                      |
|--------------------|--------------------------------------------|
| Beacon Rate        | 1 packet every 30 seconds                  |
| Data Rate          | 1 kbps (SAFE tier)                         |
| Symbol Rate        | 1 kBd, BPSK, conv(7,1/2)+RS, ~3.1 kHz occupied |
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

Computed by `python tools/link_budget_ladder.py`, which drives the same model object the
simulator flies, so these numbers cannot drift from the spacecraft.

| Parameter            | SAFE (1 kbps)   | LOW (16 kbps) | HIGH (128 kbps) |
|----------------------|-----------------|---------------|-----------------|
| EIRP (10 dBW + 3 dBi)| 13 dBW          | 13 dBW        | 13 dBW          |
| Free Space Loss @5 deg | -165.1 dB (1944.5 km) | -165.1 dB | -165.1 dB |
| Ground Station G/T (Iqaluit) | 20 dB/K | 20 dB/K      | 20 dB/K         |
| Coding               | conv(7,1/2)+RS, 7.5 dB | conv(7,1/2)+RS, 7.5 dB | RS(255,223), 6.0 dB |
| Required Eb/N0 (9.6 dB uncoded, less coding gain) | 2.1 dB | 2.1 dB | 3.6 dB |
| **Link margin @ 5 deg, Iqaluit** | **57.8 dB** | **45.7 dB** | **35.2 dB** |
| **Link margin @ 5 deg, Troll (worst station)** | **56.3 dB** | **44.2 dB** | **33.7 dB** |
| **Link margin @ zenith, Iqaluit** | 74.3 dB | 62.3 dB | 51.8 dB |

Two things follow, and both are on the syllabus:

* **Every rung closes with tens of dB to spare.** At 450 km to a professional polar dish
  this link is not power-limited. What differentiates the tiers is occupied bandwidth,
  PA power and thermal cost, ground-modem configuration and mode policy — not a power
  cliff. The 10 deg rule on HIGH is a GROUND policy (planner scheduling + MCS
  commanding hold), not the point at which the link fails — and not an on-board gate.
* **Margin still responds to rate exactly as the budget says it should.** LOW is 12.04 dB
  below SAFE and HIGH is 10.5 dB below LOW (9.03 dB of rate plus 1.5 dB of coding).
  Read the ladder off the `-10*log10(R)` term and it comes out right.

Link margin decreases with lower elevation angles: path loss rises, atmospheric loss is
scaled by cosec(elevation) below 30 deg, and the ground station's G/T is de-rated for
ground and sky noise pickup (-1.5 dB below 10 deg, -0.5 dB below 30 deg).

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
   power. Telemetry transmission continues on the LOW tier (16 kbps) — the configured
   `safe_tier`. Safe mode does not cost you housekeeping; it costs you the ability to
   dump.
2. The onboard GPS receiver provides position and velocity data for orbit determination,
   supplemented by ranging measurements (0x0509) during ground contacts.
3. RSSI values below -100 dBm indicate marginal link conditions. Operations should be
   restricted to high-priority commands at low RSSI. Step the ladder down to LOW
   (`TTC_SET_DATA_RATE(rate=0)`), which recovers 10.5 dB.
4. The transponder temperature (0x0507) should remain within 0–50 deg C during operation.
   Thermal protection is provided by passive means (TCS radiator and MLI).
5. Stored telemetry and payload data are downlinked during contacts on the HIGH tier
   (128 kbps), which must be entered deliberately: DOWNLINK mode first, then the rate.
   Real-time HK telemetry is interleaved with stored data. One 4 MiB image is 262 s of
   channel at HIGH — 328 s with the mission planner's 20 % protocol allowance, or about
   three-quarters of an average pass — so a dump is a scheduled activity, not an
   afterthought.
6. The dedicated PDM command channel ensures the receiver remains powered even when the
   transmitter auto-off timer expires. This is by design — the spacecraft is always
   commandable.
7. After antenna deployment (burn wire), verify RSSI improvement from pre-deployment
   levels. A marginal improvement may indicate incomplete deployment.
8. During LEOP first contact, expect beacon packets on the SAFE tier (1 kbps, 1 kBd,
   BPSK conv(7,1/2)+RS). Configure the ground demodulator for 1 kBd before AOS and
   re-configure it for 16 kBd immediately after application boot, when the ladder's
   `default_tier` (LOW) becomes active — the beacon demodulator will not lock 16 kbps.

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

# EOSAT-1 Payload — Multispectral Imager

**Document ID:** EOSAT1-UM-PLD-007
**Issue:** 2.0
**Date:** 2026-03-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Overview

The EOSAT-1 payload is a nadir-mounted multispectral pushbroom imager optimised for ocean
colour observation and current monitoring. It provides medium-resolution imagery across four
spectral bands in the visible and near-infrared (VNIR) range, specifically selected for
ocean colour science (chlorophyll absorption, water-leaving radiance, suspended sediment
detection). It features a cooled focal plane array (FPA) for optimal signal-to-noise
performance and scene-dependent onboard data compression.

## 2. Instrument Characteristics

| Parameter              | Value                          |
|------------------------|--------------------------------|
| Instrument Type        | Pushbroom multispectral imager |
| Spectral Range         | 443–865 nm (VNIR)             |
| Number of Bands        | 4 (Blue, Green, Red, NIR)     |
| Ground Sampling Dist.  | ~9 m (at 450 km altitude)     |
| Swath Width            | ~54 km (at 450 km altitude)   |
| Detector Type          | CCD linear array               |
| FPA Operating Temp     | -15 deg C (nominal)           |
| Data Rate (raw)        | ~40 Mbps                       |
| Compression Ratio      | 2:1 to 8:1 (scene-dependent)  |
| Effective Data Rate    | ~5–20 Mbps (compressed)       |
| Mounting               | Nadir face (+Z), boresight along +Z |

### 2.1 Spectral Bands — Ocean Colour Configuration

The spectral bands are selected specifically for ocean colour science and current monitoring:

| Band | Name  | Centre Wavelength | Bandwidth | Primary Application                    |
|------|-------|-------------------|-----------|----------------------------------------|
| 1    | Blue  | 443 nm            | 20 nm     | Chlorophyll absorption, ocean colour   |
| 2    | Green | 560 nm            | 35 nm     | Water-leaving radiance, turbidity      |
| 3    | Red   | 665 nm            | 30 nm     | Suspended sediment, coastal features   |
| 4    | NIR   | 865 nm            | 40 nm     | Atmospheric correction, land-sea mask  |

**Ocean Colour Application:** The blue band at 443 nm is centred on the chlorophyll-a
absorption peak, making it the primary channel for detecting phytoplankton blooms and
ocean colour variations associated with current boundaries. The green band captures
water-leaving radiance for turbidity mapping. The red and NIR bands provide atmospheric
correction and land/sea discrimination.

### 2.2 Altitude-Dependent Geometry

The imaging geometry scales with orbital altitude. At the nominal 450 km altitude:

| Parameter              | At 450 km        | At 500 km (ref)  | Scaling            |
|------------------------|------------------|-------------------|--------------------|
| Ground Sampling Dist.  | ~9 m             | ~10 m             | Proportional to alt|
| Swath Width            | ~54 km           | ~60 km            | Proportional to alt|
| Dwell Time per Pixel   | ~1.27 ms         | ~1.41 ms          | Proportional to alt|

The lower 450 km altitude provides improved spatial resolution at the cost of slightly
reduced swath width and increased atmospheric drag.

### 2.3 Scene-Dependent Compression

The onboard compression algorithm adapts its ratio based on scene content:

| Scene Type         | Typical Ratio | Notes                                      |
|--------------------|---------------|--------------------------------------------|
| Open ocean (uniform)| 6:1 to 8:1  | Low spatial complexity, high compressibility|
| Coastal/mixed      | 3:1 to 5:1   | Moderate complexity                        |
| Land (urban/varied)| 2:1 to 3:1   | High spatial complexity                    |
| Cloud-covered      | 4:1 to 6:1   | Moderate complexity, low science value     |

The compression ratio is reported in telemetry and affects the data volume budget for
downlink planning.

### 2.4 Attitude-Quality Coupling

Image quality is directly coupled to the spacecraft attitude stability during acquisition:

| Attitude Error   | Image Quality Impact                                    |
|------------------|---------------------------------------------------------|
| < 0.1 deg        | Full quality — nominal GSD and MTF                      |
| 0.1 – 0.5 deg   | Acceptable — minor smearing, usable for science         |
| 0.5 – 1.0 deg   | Degraded — significant smearing, reduced spatial resolution |
| > 1.0 deg        | Unusable — imaging should not be attempted              |

The AOCS `att_error` parameter (0x0217) must be monitored before and during imaging. If
attitude error exceeds 0.5 deg during acquisition, image quality is significantly degraded
and the data may not meet science requirements for ocean current detection. The FDIR rule
PLD-02 will abort imaging if `att_error` exceeds 1 deg.

## 3. Payload Modes

| Mode ID | Mode Name  | Description                                           |
|---------|------------|-------------------------------------------------------|
| 0       | OFF        | Payload powered off, no thermal control               |
| 1       | STANDBY    | Electronics powered, FPA cooler active, no imaging    |
| 2       | IMAGING    | Active image acquisition and onboard processing       |

**Note:** A PLAYBACK function is managed by the OBDH mass memory subsystem and does not
require a dedicated payload mode. Stored image data is downlinked via the TTC subsystem
during ground contacts.

### 3.1 Mode Transitions

```
OFF --> STANDBY --> IMAGING --> STANDBY --> OFF
```

- **OFF to STANDBY**: Ground commanded via `PAYLOAD_SET_MODE` (mode=1). FPA cooler begins
  cooldown (~30 min to reach operating temperature).
- **STANDBY to IMAGING**: Ground commanded via `PAYLOAD_SET_MODE` (mode=2). FPA must be
  at operating temperature (temp_fpa <= -12 deg C).
- **IMAGING to STANDBY**: Ground commanded or autonomous at end of imaging window.
- **STANDBY to OFF**: Ground commanded via `PAYLOAD_SET_MODE` (mode=0).

### 3.2 Imaging Constraints

| Constraint                   | Value / Condition                    |
|------------------------------|--------------------------------------|
| Min. FPA Temperature         | -12 deg C (for IMAGING entry)        |
| Max. Continuous Imaging      | 10 min (thermal/power limited)       |
| Min. Battery SoC             | 40% (for imaging activation)         |
| Required AOCS Mode           | NADIR_POINT or TARGET_TRACK (SLEW)   |
| Required Attitude Error      | < 0.5 deg                            |

## 4. Data Storage and Management

Acquired images are stored in the OBDH mass memory. Each image strip is assigned a
sequence number and a CRC checksum for data integrity verification.

| Parameter           | Value                          |
|---------------------|--------------------------------|
| Mass Memory Total   | 2 GB                           |
| Bytes per Image     | ~50–200 MB (depending on strip)|
| Max Stored Images   | ~10–40 images                  |
| Checksum Algorithm  | CRC-32                         |

The `store_used` parameter (0x0604) indicates the percentage of mass memory occupied
by payload data. The `image_count` (0x0605) tracks the number of stored image files.
The `checksum_errors` parameter (0x0609) counts data integrity failures.

## 5. Telemetry Parameters

| Param ID | Name            | Unit   | Description                          |
|----------|-----------------|--------|--------------------------------------|
| 0x0600   | pld_mode        | enum   | Current payload mode (0/1/2)         |
| 0x0601   | fpa_temp        | deg C  | Focal plane array temperature        |
| 0x0602   | cooler_pwr      | W      | FPA cooler power consumption         |
| 0x0604   | store_used      | %      | Payload data storage utilisation     |
| 0x0605   | image_count     | count  | Number of stored images              |
| 0x0609   | checksum_errors | count  | Cumulative checksum error count      |

## 6. Limit Definitions

| Parameter       | Yellow Low | Yellow High | Red Low | Red High |
|-----------------|------------|-------------|---------|----------|
| fpa_temp (C)    | -18        | 8           | -20     | 12       |

### 6.1 FPA Temperature Interpretation

- **Below -18 deg C**: Cooler over-performance. Cooler power may be reduced.
- **-18 to -12 deg C**: Optimal imaging range.
- **-12 to 8 deg C**: Degraded imaging quality; STANDBY operations acceptable.
- **Above 8 deg C**: Yellow alarm. Imaging should not be initiated.
- **Above 12 deg C**: Red alarm. Cooler malfunction suspected; FDIR may power off payload.

## 7. Commands

| Command           | Service  | Parameters    | Description                          |
|-------------------|----------|---------------|--------------------------------------|
| PAYLOAD_SET_MODE  | S8,S1    | mode (0/1/2)  | Set payload operating mode           |
| HK_REQUEST        | S3,S27   | sid=6         | Request payload housekeeping packet  |
| GET_PARAM         | S20,S3   | param_id      | Read individual payload parameter    |
| SET_PARAM         | S20,S1   | param_id, val | Modify payload config parameter      |

### 7.1 Typical Imaging Sequence

1. Verify spacecraft is in NADIR_POINT mode with `att_error` < 0.5 deg.
2. Verify battery SoC > 40% and sufficient mass memory available.
3. Send `PAYLOAD_SET_MODE` (mode=1) to enter STANDBY. Monitor `fpa_temp` (0x0601).
4. Wait for FPA cooldown: `fpa_temp` <= -12 deg C (approximately 30 minutes from OFF).
5. At imaging window start, send `PAYLOAD_SET_MODE` (mode=2).
6. Monitor `image_count` (0x0605) incrementing during acquisition.
7. At imaging window end, send `PAYLOAD_SET_MODE` (mode=1) to return to STANDBY.
8. If no further imaging is planned, send `PAYLOAD_SET_MODE` (mode=0).

## 8. Operational Notes

1. The FPA cooler consumes 10–15 W. The total payload power budget in IMAGING mode is
   approximately 25 W (cooler + electronics + detector).
2. Imaging during eclipse is possible but produces dark frames (useful for calibration).
3. If `checksum_errors` (0x0609) increments, the most recent image may be corrupted.
   Re-imaging of the target should be considered.
4. Payload data downlink at 64 kbps (high-rate mode) requires approximately 25 minutes per
   100 MB of compressed data. Downlink scheduling should account for available contact time
   across Iqaluit and Troll passes.
5. The payload should not be left in STANDBY for more than 4 hours continuously to
   manage cooler lifetime.
6. For ocean current monitoring, prioritise imaging over regions with known current
   boundaries (e.g., Gulf Stream, Kuroshio, Antarctic Circumpolar Current). The 443 nm
   blue band provides the highest sensitivity to chlorophyll-a gradients at current
   boundaries.
7. Scene-dependent compression ratios mean that open ocean images consume less storage
   than coastal or land images. The mission planner should account for this when estimating
   data volume budgets.
8. Attitude stability is critical for ocean colour measurements. Verify `att_error` < 0.5 deg
   before commanding IMAGING mode. Degraded attitude produces blurred images that are
   unusable for ocean current velocity estimation.

---

*End of Document — EOSAT1-UM-PLD-007*

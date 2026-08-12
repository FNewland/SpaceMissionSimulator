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
  cooldown. The loop reaches the setpoint band in about 10 minutes from the uncooled
  equilibrium and `fpa_ready` (0x0610) sets after a further 60 s of hysteresis.
- **STANDBY to IMAGING**: Ground commanded via `PAYLOAD_SET_MODE` (mode=2). The FPA must
  be thermally ready — `fpa_ready` (0x0610) = 1. That flag is the gate; it means the loop
  has settled about the **active** cooler setpoint (-1 K / +5 K) *and* `fpa_temp` is at or
  below **-3.0 deg C**. At the default -15 deg C setpoint that works out to
  **-16.0 … -10.0 deg C**. Do not key on a remembered number: the setpoint is commandable
  over -20…0 deg C, so any fixed threshold is wrong for most of the envelope.
- **IMAGING to STANDBY**: Ground commanded or autonomous at end of imaging window.
- **STANDBY to OFF**: Ground commanded via `PAYLOAD_SET_MODE` (mode=0).

### 3.2 Imaging Constraints

| Constraint                   | Value / Condition                    |
|------------------------------|--------------------------------------|
| FPA thermal gate             | `fpa_ready` (0x0610) = 1 — settled about the active setpoint (-1 K/+5 K) **and** at or below -3.0 deg C |
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
| fpa_temp (C)      | -22.5      | 13.5        | -26.0   | 17.0     |
| detector_temp (C) | -22.0      | 14.0        | -25.5   | 17.5     |

Derived from `subsystems/tcs.yaml: fpa_envelope` (DECISIONS §3) — the TCS FPA
node bands (03_tcs.md §6) shifted down the payload thermal chain:

| Reading | Param | Where it sits |
|---|---|---|
| FPA bracket (TCS control node) | 0x0408 | the regulated setpoint |
| Cold finger | 0x0601 `fpa_temp` | bracket **-0.5 K** |
| Detector die | 0x0617 `detector_temp` | cold finger **+0.5 K** (self-heating) |

### 6.1 FPA Temperature Interpretation

Limits answer "is the hardware healthy". Readiness answers "can I image".
They are different questions and they used to be the same table, which is why
five files disagreed.

- **Below -22.5 deg C**: colder than the cooler can command. The loop has lost
  control — this is a fault, not an over-performing cooler.
- **Inside the commanded band** (active setpoint -1 K / +5 K) **and at or below
  -3.0 deg C**: imaging range. `fpa_ready` = 1.
- **Warmer than -3.0 deg C**: `FPA_OVERTEMP` (0x0604). Dark current makes the
  product unusable. Legitimate in STANDBY; imaging must not be initiated.
- **Around +11.5 deg C**: the normal uncooled resting state with the cooler
  off. Not an alarm.
- **Above +13.5 deg C**: yellow. Above **+17.0 deg C**: red — the radiator path
  has been lost, or a decontamination bake-out is running (0x0418).

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
4. Wait for FPA cooldown: `fpa_ready` (0x0610) = 1 — about 10 minutes from the uncooled
   equilibrium, plus 60 s of readiness hysteresis.
   At the default -15 deg C setpoint that corresponds to `fpa_temp` in -16.0 … -10.0 deg C.
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
4. Payload data downlink runs on the HIGH tier of the TM rate ladder (128 kbps QPSK
   RS(255,223), see `manual/05_ttc.md` §2.4). One 4 MiB raw frame is 262 s of channel,
   or 328 s once the mission planner's 20 % protocol allowance is applied — about
   three-quarters of the 445 s average pass in this mission's five-station contact
   statistics. 100 MB of compressed product is therefore roughly 2.3 hours of contact
   time, which is most of a day's usable network. Downlink scheduling must account for
   available contact time across all five stations, and the HIGH tier is only available
   on passes reaching 10 deg elevation with DOWNLINK mode commanded.
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

---

## Commandable settings

Every payload setting reachable over the wire, with its encoding.

<!-- BEGIN GENERATED: payload commandable settings -->
<!-- Generated by tools/gen_command_reference.py — do not edit by hand. -->

| Setting | Command | func_id | Wire form | Engineering range | Default | Readback |
|---|---|---|---|---|---|---|
| Set payload mode. Modes 1 (STANDBY), 2 (IMAGING) and 3 (CALI | `PAYLOAD_SET_MODE` | 26 (0x1A) | mode:uint8 | 0 … 3 | 0 | payload.mode (0x0600) |
| DEPRECATED — pre-set the next scene id. Superseded by PAYLOA | `PAYLOAD_SET_SCENE` | 27 (0x1B) | scene_id:uint16 | 0 … 65535 | 0 | payload.scene_id (0x0606) |
| Trigger one image acquisition. The WHERE is set by the WHEN: | `PAYLOAD_CAPTURE` | 28 (0x1C) | scene_id:uint16, lines:uint16 | 0 … 65535; 256 … 20000 | 0; 2048 | payload.last_scene_id (0x060C) |
| Report one stored image product. 2026-08b PL-c: the handler | `PAYLOAD_DOWNLOAD_IMAGE` | 29 (0x1D) | scene_id:uint16 | 0 … 65535 | 0 | — |
| Delete specific image from payload memory | `PAYLOAD_DELETE_IMAGE` | 30 (0x1E) | scene_id:uint16 | 0 … 65535 | 0 | — |
| Mark memory segment as unusable | `PAYLOAD_MARK_BAD_SEGMENT` | 31 (0x1F) | segment_id:uint8 | 0 … 7 | 0 | payload.mem_segments_bad (0x0612) |
| Request the stored image catalogue. 2026-08b PL-c: this now | `PAYLOAD_GET_IMAGE_CATALOG` | 32 (0x20) | no arguments | — | — | — |
| Set spectral band enable mask | `PAYLOAD_SET_BAND_CONFIG` | 33 (0x21) | mask:uint8 | 1 … 15 | 15 | payload.band_enable_mask (0x0625) |
| Set per-band detector integration times. Fixed point, 0.01 m | `PAYLOAD_SET_INTEGRATION_TIME` | 34 (0x22) | blue_ms:uint16 x0.01, green_ms:uint16 x0.01, red_ms:uint16 x0.01, nir_ms:uint16 x0.01 | 0.1 … 655.35; 0.1 … 655.35; 0.1 … 655.35; 0.1 … 655.35 | 2.0; 2.0; 2.0; 2.0 | payload.integration_ms (average) (0x0635) |
| Set detector gain. Fixed point, 0.001x/LSB (was float32). Ga | `PAYLOAD_SET_GAIN` | 35 (0x23) | gain:uint16 x0.001 | 0.1 … 10.0 | 1.0 | payload.gain_code (0x0636) |
| Set the FPA thermo-electric cooler regulation setpoint (driv | `PAYLOAD_COOLER_SETPOINT` | 36 (0x24) | setpoint:uint16 x0.01 | -20.0 … 0.0 | -15.0 | payload.fpa_setpoint (0x0631) |
| Begin a radiometric calibration sequence (dark frame / flat | `PAYLOAD_START_CALIBRATION` | 37 (0x25) | mode:uint8 | — | 2 | payload.cal_active (0x062A) |
| Abort calibration sequence | `PAYLOAD_STOP_CALIBRATION` | 38 (0x26) | no arguments | — | — | — |
| Select compression algorithm and ratio override (was a singl | `PAYLOAD_SET_COMPRESSION` | 39 (0x27) | algorithm:uint8, ratio:uint8 x0.1 | —; 0.0 … 4.0 | 3; 0.0 | payload.compression_ratio (0x0614) |
| Rotate the filter wheel to a position (2 s per 90 deg step) | `PAYLOAD_SELECT_FILTER` | 92 (0x5C) | position:uint8 | — | 0 | payload.filter_pos (0x063D) |
| Run a shutter mechanism EXERCISE. This is a mechanism health | `PAYLOAD_CYCLE_SHUTTER` | 93 (0x5D) | cycles:uint8 | 1 … 100 | 1 | payload.shutter_pos (0x063C) |
| Start the on-board image transfer engine for a stored scene | `PAYLOAD_INITIATE_TRANSFER` | 94 (0x5E) | scene_id:uint16 | 1 … 65535 | 1 | payload.transfer_active (0x062D) |
| Calibration lamp on/off (also reachable as S2 device 0x0602) | `PAYLOAD_CAL_LAMP` | 95 (0x5F) | on:uint8 | — | 0 | payload.cal_lamp_on (0x0615) |

<!-- END GENERATED: payload commandable settings -->

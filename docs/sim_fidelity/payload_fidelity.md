# EOSAT-1 Payload Subsystem -- Simulator Fidelity Analysis

**Document ID**: SMO-SFA-PLI-001
**Date**: 2026-03-12
**Target Fidelity**: Undetectably different from real spacecraft telemetry
**Scope**: `packages/smo-simulator/src/smo_simulator/models/payload_basic.py` (460 lines)

---

## Table of Contents

1. [Current Model Capabilities](#1-current-model-capabilities)
2. [Gap Analysis Summary](#2-gap-analysis-summary)
3. [Gap G1: Multispectral Band Configuration](#3-gap-g1-multispectral-band-configuration)
4. [Gap G2: Image Quality Coupling to Attitude Error](#4-gap-g2-image-quality-coupling-to-attitude-error)
5. [Gap G3: Swath Width from Altitude](#5-gap-g3-swath-width-from-altitude)
6. [Gap G4: Scene-Content-Dependent Compression Ratio](#6-gap-g4-scene-content-dependent-compression-ratio)
7. [New Telemetry Parameters](#7-new-telemetry-parameters)
8. [Configuration Schema Changes](#8-configuration-schema-changes)
9. [Cross-Subsystem Coupling Requirements](#9-cross-subsystem-coupling-requirements)
10. [Test Plan](#10-test-plan)
11. [Implementation Priority and Effort](#11-implementation-priority-and-effort)

---

## 1. Current Model Capabilities

### 1.1 Architecture

The payload model (`PayloadBasicModel`) is a subclass of `SubsystemModel` (ABC defined in
`packages/smo-common/src/smo_common/models/subsystem.py`). It implements the standard
interface: `configure()`, `tick()`, `get_telemetry()`, `handle_command()`,
`inject_failure()`, `clear_failure()`, `get_state()`, `set_state()`.

The simulation engine (`packages/smo-simulator/src/smo_simulator/engine.py`) calls
`model.tick(dt_sim, orbit_state, self.params)` on every subsystem in iteration order.
All subsystem models share a single `dict[int, float]` parameter store (`shared_params`)
keyed by ECSS-style parameter IDs. This is the mechanism for cross-subsystem coupling:
any model can read any other model's parameters by their `param_id`.

### 1.2 State Model (`PayloadState` dataclass)

| Field | Type | Default | Description |
|---|---|---|---|
| `mode` | `int` | 0 | 0=OFF, 1=STANDBY (cooler on), 2=IMAGING |
| `fpa_temp` | `float` | 5.0 | Focal Plane Array temperature (C) |
| `cooler_active` | `bool` | False | TEC cooler running |
| `imager_temp` | `float` | 5.0 | Imager electronics temperature (C) |
| `store_used_pct` | `float` | 20.0 | Storage usage percentage |
| `image_count` | `int` | 12 | Total images onboard |
| `current_scene_id` | `int` | 0 | Active scene ID |
| `line_rate` | `float` | 0.0 | CCD line scan rate (Hz) |
| `data_rate_mbps` | `float` | 0.0 | Data generation rate (Mbps) |
| `checksum_errors` | `int` | 0 | Cumulative checksum errors |
| `cooler_on_time_s` | `float` | 0.0 | Cumulative cooler runtime |
| `image_size_mb` | `float` | 800.0 | Size per image (MB) |
| `total_storage_mb` | `float` | 20000.0 | Total onboard storage (MB) |
| `cooler_failed` | `bool` | False | Cooler hardware failure flag |
| `fpa_degraded` | `bool` | False | FPA performance degradation flag |
| `num_segments` | `int` | 8 | Memory segment count |
| `segment_size_mb` | `float` | 2500.0 | Size per segment (MB) |
| `bad_segments` | `list[int]` | [] | Failed segment indices |
| `image_catalog` | `list[dict]` | [] | In-memory image metadata catalog |
| `mem_total_mb` | `float` | 20000.0 | Usable storage (total minus bad segments) |
| `mem_used_mb` | `float` | 4000.0 | Current used storage (MB) |
| `last_scene_id` | `int` | 0 | Last captured scene ID |
| `last_scene_quality` | `float` | 100.0 | Quality of last captured image (%) |
| `fpa_ready` | `bool` | False | FPA at operational temperature |
| `mem_segments_bad` | `int` | 0 | Count of bad segments |
| `duty_cycle_pct` | `float` | 0.0 | Imaging duty cycle (%) |
| `compression_ratio` | `float` | 2.0 | Image compression ratio (fixed) |
| `cal_lamp_on` | `bool` | False | Calibration lamp status |
| `snr` | `float` | 45.0 | Signal-to-noise ratio (dB) |
| `detector_temp_c` | `float` | -5.0 | Detector temperature (C) |
| `integration_time_ms` | `float` | 2.0 | Detector integration time (ms) |
| `swath_width_km` | `float` | 30.0 | Ground swath width (fixed) |
| `corrupt_remaining` | `int` | 0 | Fault injection: remaining corrupt images |
| `ccd_line_dropout` | `bool` | False | Fault injection: CCD line dropout active |

### 1.3 Thermal Model

The FPA thermal model uses first-order exponential approach:

```
fpa_temp += (target - fpa_temp) / tau * dt + noise
```

- Cooling target: -5.0 C, time constant: 100 s
- Warming target (ambient): 5.0 C, time constant: 120 s
- FPA readiness threshold: `fpa_temp <= (target + 5.0)` = 0.0 C
- Detector temperature tracks FPA with +0.5 C offset and noise
- Imager electronics temperature driven by eclipse state (5.0 C sunlit, -5.0 C eclipse)
  with 400 s time constant

### 1.4 SNR Model

Implemented in Phase 4, the SNR depends on FPA temperature and degradation state:

```
fpa_factor = max(0.5, 1.0 - (fpa_temp - fpa_target) * 0.02)
degrade_factor = 0.85 if fpa_degraded else 1.0
snr = 45.0 * fpa_factor * degrade_factor + noise
```

Clamped to [10.0, 60.0] dB range. Active only in IMAGING mode with FPA ready.

### 1.5 Image Capture Pipeline

The `capture` command:
1. Validates mode == 2 (IMAGING) and `fpa_ready`
2. Checks available storage (accounting for bad segments)
3. Finds a free memory segment
4. Determines image quality based on failure state:
   - `corrupt_remaining > 0`: status=CORRUPT, quality 10-30%
   - `ccd_line_dropout`: status=PARTIAL, quality 60-85%
   - `fpa_degraded`: quality 80-95%
   - Normal: quality 100%
5. Creates metadata record in `image_catalog`
6. Updates `mem_used_mb`, `store_used_pct`, `image_count`

### 1.6 Commands Supported

| Command | Description |
|---|---|
| `set_mode` | Transition between OFF(0)/STANDBY(1)/IMAGING(2) |
| `set_scene` | Set current scene ID for next capture |
| `capture` | Capture image at current position |
| `download_image` | Retrieve image metadata by scene_id |
| `delete_image` | Delete by scene_id or legacy count-based delete |
| `mark_bad_segment` | Mark a memory segment as failed |
| `get_image_catalog` | Return full image catalog |

### 1.7 Failure Modes

| Failure ID | Effect |
|---|---|
| `cooler_failure` | Disables cooler, FPA warms to ambient |
| `fpa_degraded` | Reduces image quality and SNR |
| `image_corrupt` | Next N images are corrupted (status=2) |
| `memory_segment_fail` | Marks specific segment bad, reduces usable storage |
| `ccd_line_dropout` | Images marked PARTIAL, quality 60-85%, 3x checksum error rate |

### 1.8 Telemetry Published

17 parameters are written to `shared_params` per tick, mapped from `param_ids` config
(0x0600-0x0619). The YAML config at `configs/eosat1/subsystems/payload.yaml` only
defines 10 param_ids; the remaining 7 (0x060A-0x0613) use code defaults. Phase 4
parameters (0x0614-0x0619) are written with hardcoded IDs, not through `param_ids`.

### 1.9 What Is NOT Modeled (Current Gaps)

The current model treats the payload as a **monochromatic, single-band imager** with:
- Fixed swath width (30 km constant, not derived from orbital geometry)
- Fixed compression ratio (2.0 constant, not scene-dependent)
- No spectral band differentiation (no per-band SNR, gain, or radiometric properties)
- No attitude-error-to-image-quality coupling (quality is purely failure-state driven)
- No GSD (ground sample distance) computation from altitude and optics
- No per-band data rate or image size accounting
- Image size is a fixed 800 MB regardless of compression, band config, or scene content

---

## 2. Gap Analysis Summary

| Gap ID | Gap Description | Fidelity Impact | Complexity |
|---|---|---|---|
| **G1** | No multispectral band configuration | **Critical** -- an ocean-color imager without spectral bands is not credible | High |
| **G2** | No attitude-error-to-quality coupling | **Critical** -- real imagery degrades with pointing error; operators must see this | Medium |
| **G3** | Fixed swath width, no altitude dependence | **High** -- swath changes ~3% per 10 km altitude change; operators notice | Low |
| **G4** | Fixed compression ratio | **High** -- compression varies 1.5x-4x with scene entropy; affects storage planning | Medium |

---

## 3. Gap G1: Multispectral Band Configuration

### 3.1 Requirement

EOSAT-1 is an ocean-color mission. The real instrument would have at minimum four
spectral bands tuned to ocean-color remote sensing:

| Band | Center Wavelength (nm) | Bandwidth (nm) | Primary Use |
|---|---|---|---|
| Blue | 443 | 20 | Chlorophyll absorption, ocean color |
| Green | 560 | 20 | Chlorophyll reflectance peak |
| Red | 665 | 20 | Sediment, chlorophyll absorption edge |
| NIR | 865 | 40 | Atmospheric correction, vegetation |

Each band has distinct radiometric properties:
- **Per-band SNR**: Blue band has lower SNR due to lower ocean reflectance (~0.01-0.03 sr^-1
  at TOA for open ocean) compared to NIR (~0.1 sr^-1 for vegetation targets).
- **Per-band gain/offset**: Each band has a different electronic gain and dark-current offset.
- **Per-band integration time**: May differ or be globally set and band-equalized via gain.
- **Per-band data contribution**: Each band generates its own data stream; total image size
  = sum of per-band data.

### 3.2 What Must Change in `PayloadState`

Add the following new fields:

```python
@dataclass
class SpectralBand:
    band_id: str = ""           # e.g. "blue", "green", "red", "nir"
    center_nm: float = 0.0      # Center wavelength (nm)
    bandwidth_nm: float = 0.0   # Bandwidth (nm)
    snr_nominal: float = 45.0   # Nominal SNR at reference radiance (dB)
    gain: float = 1.0           # Electronic gain (DN/radiance)
    dark_current: float = 0.0   # Dark current (DN/s) at operational temp
    enabled: bool = True        # Band can be disabled for reduced data mode
    pixels_cross_track: int = 5000  # Detector pixel count in cross-track
```

New `PayloadState` fields:

```python
bands: list[SpectralBand]       # Configured spectral bands
band_snr: dict[str, float]     # Per-band current SNR values
active_bands: int               # Number of enabled bands
band_data_rate_mbps: dict[str, float]  # Per-band data rate
```

### 3.3 What Must Change in `tick()`

The SNR model (lines 206-215 of `payload_basic.py`) must be replaced with a per-band
SNR calculation:

```
For each band in self._state.bands:
    if not band.enabled:
        continue
    # Base SNR depends on band's nominal and FPA temp
    temp_factor = max(0.5, 1.0 - (fpa_temp - fpa_target) * 0.02)
    # Dark current increases with temperature (doubles per ~7 C)
    dark_factor = 2.0 ** ((detector_temp - (-5.0)) / 7.0)
    effective_dark = band.dark_current * dark_factor
    # SNR degrades with dark current noise
    dark_noise_factor = 1.0 / (1.0 + effective_dark / 100.0)
    band_snr = band.snr_nominal * temp_factor * dark_noise_factor * degrade_factor
    band_snr += noise
    store in band_snr[band.band_id]
```

The data rate must sum per-band contributions:

```
total_data_rate = sum(
    band.pixels_cross_track * line_rate * bits_per_pixel / 1e6
    for band in bands if band.enabled
)
```

The aggregate SNR telemetry (0x0616) should report the **minimum** band SNR (worst-case)
or a weighted average, to maintain backward compatibility.

### 3.4 What Must Change in `handle_command()`

New commands required:

| Command | Parameters | Description |
|---|---|---|
| `set_band_config` | `band_id`, `enabled` | Enable/disable individual bands |
| `set_integration_time` | `integration_time_ms` | Global integration time |
| `cal_lamp` | `on` (bool) | Toggle calibration lamp |
| `get_band_status` | -- | Return per-band SNR and status |

### 3.5 Config Changes (`payload.yaml`)

```yaml
spectral_bands:
  - band_id: blue
    center_nm: 443
    bandwidth_nm: 20
    snr_nominal: 40.0
    gain: 1.2
    dark_current: 5.0
    pixels_cross_track: 5000
  - band_id: green
    center_nm: 560
    bandwidth_nm: 20
    snr_nominal: 50.0
    gain: 1.0
    dark_current: 4.0
    pixels_cross_track: 5000
  - band_id: red
    center_nm: 665
    bandwidth_nm: 20
    snr_nominal: 48.0
    gain: 1.1
    dark_current: 4.5
    pixels_cross_track: 5000
  - band_id: nir
    center_nm: 865
    bandwidth_nm: 40
    snr_nominal: 42.0
    gain: 1.3
    dark_current: 6.0
    pixels_cross_track: 5000

bits_per_pixel: 12
```

### 3.6 New Telemetry Parameters

| Param ID | Name | Units | Description |
|---|---|---|---|
| 0x0620 | `payload.band_blue_snr` | dB | Blue band SNR |
| 0x0621 | `payload.band_green_snr` | dB | Green band SNR |
| 0x0622 | `payload.band_red_snr` | dB | Red band SNR |
| 0x0623 | `payload.band_nir_snr` | dB | NIR band SNR |
| 0x0624 | `payload.active_bands` | -- | Count of enabled bands |
| 0x0625 | `payload.band_enable_mask` | -- | Bitmask of enabled bands (bit0=blue...bit3=NIR) |

### 3.7 Failure Modes to Add

| Failure ID | Effect |
|---|---|
| `band_failure` | Disables a specific band (kwarg `band_id`), reduces data rate |
| `detector_hot_pixel` | Increases dark current on specific band, degrades SNR |

---

## 4. Gap G2: Image Quality Coupling to Attitude Error

### 4.1 Requirement

On a real spacecraft, image quality is directly coupled to attitude pointing error.
For a push-broom scanner like EOSAT-1:

- **Below 0.1 degrees**: No visible degradation. Sub-pixel smear.
- **0.1 to 0.5 degrees**: Minor degradation. Cross-track smear of 1-5 pixels depending
  on GSD. Quality 90-100%.
- **0.5 to 1.0 degrees**: Significant degradation. Smear exceeds 5 pixels. Geometric
  distortion visible. Quality 60-90%.
- **1.0 to 2.0 degrees**: Severe degradation. Image registration fails. Quality 30-60%.
- **Above 2.0 degrees**: Unusable. Quality 0-30%.

This coupling is critical for operator training: the payload engineer must coordinate
with the AOCS operator to ensure fine pointing before imaging.

### 4.2 Cross-Subsystem Data Flow

The AOCS model (`aocs_basic.py`) publishes `att_error` to `shared_params[0x0217]`.
The payload model can read this value directly from `shared_params` during its `tick()`
call. The engine calls all subsystem `tick()` methods in dictionary iteration order
(line 214 of `engine.py`). AOCS is loaded before payload (alphabetical order of
`_subsys_configs` keys: aocs, eps, obdh, payload, tcs, ttc), so `shared_params[0x0217]`
is populated when the payload tick executes.

No engine changes are required. The coupling is purely through `shared_params`.

### 4.3 What Must Change in `tick()`

Add attitude error reading and quality degradation computation:

```python
# Read AOCS attitude error from shared params
att_error_deg = shared_params.get(0x0217, 0.0)

# Compute image quality factor from attitude error
if att_error_deg <= 0.1:
    att_quality_factor = 1.0
elif att_error_deg <= 0.5:
    # Linear degradation: 1.0 at 0.1 deg to 0.9 at 0.5 deg
    att_quality_factor = 1.0 - (att_error_deg - 0.1) * 0.25
elif att_error_deg <= 1.0:
    # Steeper degradation: 0.9 at 0.5 deg to 0.6 at 1.0 deg
    att_quality_factor = 0.9 - (att_error_deg - 0.5) * 0.6
elif att_error_deg <= 2.0:
    # Severe: 0.6 at 1.0 deg to 0.3 at 2.0 deg
    att_quality_factor = 0.6 - (att_error_deg - 1.0) * 0.3
else:
    att_quality_factor = max(0.0, 0.3 - (att_error_deg - 2.0) * 0.1)
```

This factor must be applied in two places:

1. **Continuous SNR telemetry** (during IMAGING mode): multiply the SNR by the attitude
   quality factor, so operators see real-time SNR drops when AOCS is unstable.

2. **Image capture quality** (in `handle_command("capture")`): multiply the base quality
   score by `att_quality_factor` before storing in the image catalog.

### 4.4 What Must Change in `handle_command()`

The `capture` command needs access to the current attitude error. Two approaches:

**Approach A (recommended)**: Store `att_quality_factor` as a `PayloadState` field,
updated each `tick()`. The `handle_command` reads it from state:

```python
# In capture command, after existing quality computation:
quality *= self._state.att_quality_factor
```

**Approach B**: Pass `shared_params` into `handle_command()`. This requires changing
the `SubsystemModel` ABC, which affects all subsystems. Not recommended.

### 4.5 New State Fields

```python
att_quality_factor: float = 1.0   # Attitude-induced quality degradation [0-1]
att_error_deg: float = 0.0        # Cached AOCS attitude error for telemetry
```

### 4.6 New Telemetry Parameters

| Param ID | Name | Units | Description |
|---|---|---|---|
| 0x0626 | `payload.att_quality_factor` | -- | Image quality factor from attitude error [0-1] |
| 0x0627 | `payload.att_error_deg` | deg | AOCS attitude error as seen by payload |

### 4.7 Event Generation

New events to emit from the payload when attitude degrades during imaging:

| Event ID | Severity | Trigger | Description |
|---|---|---|---|
| 0x0604 | 2 (WARNING) | `att_error > 0.5` while mode=2 | "Imaging quality degraded: attitude error X.XX deg" |
| 0x0605 | 3 (ALARM) | `att_error > 1.0` while mode=2 | "Imaging unusable: attitude error X.XX deg" |

These events require the payload model to track the previous attitude error state for
edge detection (emit once per threshold crossing, not every tick).

---

## 5. Gap G3: Swath Width from Altitude

### 5.1 Requirement

Swath width is a function of orbital altitude and the instrument's Instantaneous Field
of View (IFOV) and total field of view (FOV):

```
GSD = alt_km * IFOV_rad * 1000        (meters)
swath_width_km = alt_km * FOV_rad     (km)
```

Where:
- `IFOV_rad` = angular subtense of one detector pixel (radians)
- `FOV_rad` = total cross-track field of view (radians)
- For EOSAT-1 at 500 km with 10 m GSD: `IFOV = 10 / (500 * 1000) = 20 urad`
- For 5000 pixels cross-track: `FOV = 5000 * IFOV = 0.1 rad` = 5.73 degrees
- Swath = 500 * 0.1 = 50 km (at 500 km altitude)

Current model hardcodes `swath_width_km = 30.0`, which is altitude-independent.

### 5.2 What Must Change in `configure()`

New config parameters:

```yaml
optics:
  ifov_urad: 20.0                 # Instantaneous FOV per pixel (microradians)
  pixels_cross_track: 5000        # Cross-track detector pixel count
  focal_length_mm: 500.0          # For reference/documentation
  aperture_mm: 100.0              # For SNR computation (f-number)
nominal_altitude_km: 500.0        # Reference altitude for nominal GSD
```

### 5.3 What Must Change in `tick()`

Read altitude from `orbit_state` and compute swath dynamically:

```python
# Read current altitude (orbit_state has alt_km)
alt_km = orbit_state.alt_km  # From OrbitPropagator via GPS

# Compute GSD and swath from optics
ifov_rad = self._ifov_urad * 1e-6
gsd_m = alt_km * 1000.0 * ifov_rad
fov_rad = self._pixels_cross_track * ifov_rad
swath_km = alt_km * fov_rad

# Store in state
s.swath_width_km = swath_km
s.gsd_m = gsd_m
```

At 500 km nominal altitude with 20 urad IFOV and 5000 pixels:
- GSD = 500000 * 20e-6 = 10.0 m
- Swath = 500 * 0.1 = 50.0 km

At 490 km (orbit decay): GSD = 9.8 m, Swath = 49.0 km (2% change, observable).

### 5.4 New State Fields

```python
gsd_m: float = 10.0              # Ground sample distance (meters)
```

### 5.5 New Telemetry Parameters

| Param ID | Name | Units | Description |
|---|---|---|---|
| 0x0628 | `payload.gsd_m` | m | Current ground sample distance |

Note: `payload.swath_width_km` (0x0619) already exists but must become dynamic.

### 5.6 Impact on Image Size Computation

With altitude-dependent GSD, the actual ground area captured per image changes.
The image data size per scene should also account for this:

```
scene_pixels = pixels_cross_track * lines_along_track
lines_along_track = scene_length_km * 1000 / gsd_m
data_mb = scene_pixels * num_bands * bits_per_pixel / 8 / 1e6
```

This replaces the fixed `image_size_mb = 800.0` with a computed value that varies
with altitude and number of active bands.

---

## 6. Gap G4: Scene-Content-Dependent Compression Ratio

### 6.1 Requirement

Image compression ratio varies significantly with scene content entropy:

| Scene Type | Typical Compression Ratio | Entropy Characteristic |
|---|---|---|
| Open ocean | 3.5 - 4.0 | Very low spatial frequency, uniform |
| Coastal zone | 2.0 - 3.0 | Mixed water/land, moderate complexity |
| Urban / complex terrain | 1.5 - 2.0 | High spatial frequency, high entropy |
| Cloud cover | 2.5 - 3.5 | Moderate frequency, textured but repetitive |
| Desert / bare soil | 3.0 - 4.0 | Low frequency, uniform |

The current model uses a fixed `compression_ratio = 2.0`. A real operator would
see compression ratio vary as the spacecraft flies over different terrain types,
affecting storage predictions and downlink planning.

### 6.2 What Must Change in `tick()`

The compression ratio should vary based on latitude/longitude, using a simplified
scene classification:

```python
def _estimate_scene_entropy(self, lat: float, lon: float) -> float:
    """Estimate scene entropy factor [0-1] from lat/lon.
    0 = uniform (ocean), 1 = complex (urban/coastal).
    """
    # Simplified model based on latitude zones:
    abs_lat = abs(lat)

    # Open ocean: most of Earth is ocean, especially |lat| > 50
    # Coarse check: land fraction increases at mid-latitudes
    # This is a rough proxy; a lookup table would be better

    # Base: assume ocean-like (low entropy)
    entropy = 0.2

    # Mid-latitudes (land masses): higher entropy
    if 20 < abs_lat < 60:
        # Seasonal land mass probability
        entropy += 0.3 * (1.0 - abs(abs_lat - 40) / 20.0)

    # Add longitude-based land mass hints
    # (very rough: continental masses at certain longitude ranges)

    # Random per-scene variation (cloud cover, seasonal changes)
    entropy += random.gauss(0, 0.1)

    return max(0.0, min(1.0, entropy))


def _compute_compression_ratio(self, entropy: float) -> float:
    """Map scene entropy [0-1] to compression ratio."""
    # Low entropy (ocean) -> high compression (4.0)
    # High entropy (urban) -> low compression (1.5)
    return 4.0 - entropy * 2.5  # Range: [1.5, 4.0]
```

### 6.3 What Must Change in `handle_command()`

The `capture` command should compute the scene-dependent compression ratio and use
it to determine the actual stored image size:

```python
entropy = self._estimate_scene_entropy(lat, lon)
comp_ratio = self._compute_compression_ratio(entropy)
raw_size_mb = self._compute_raw_image_size()  # from G3 pixel-based calculation
stored_size_mb = raw_size_mb / comp_ratio
s.compression_ratio = comp_ratio
```

### 6.4 What Must Change in `tick()`

During continuous imaging (mode=2), the compression ratio should fluctuate based on
the current orbit position:

```python
if s.mode == 2 and s.fpa_ready:
    lat = shared_params.get(0x0210, 0.0)  # AOCS GPS lat
    lon = shared_params.get(0x0211, 0.0)  # AOCS GPS lon
    entropy = self._estimate_scene_entropy(lat, lon)
    s.compression_ratio = self._compute_compression_ratio(entropy)
    # Effective data rate accounts for compression
    effective_data_rate = s.data_rate_mbps / s.compression_ratio
    # Storage accumulation uses compressed rate
    data_mb = effective_data_rate * 1e6 / 8 / 1e6 * dt
    ...
```

### 6.5 New State Fields

```python
scene_entropy: float = 0.2       # Current scene entropy estimate [0-1]
```

### 6.6 New Telemetry Parameters

| Param ID | Name | Units | Description |
|---|---|---|---|
| 0x0629 | `payload.scene_entropy` | -- | Scene entropy estimate [0-1] |

Note: `payload.compression_ratio` (0x0614) already exists but must become dynamic.

---

## 7. New Telemetry Parameters

### 7.1 Complete New Parameter Table

All new parameters required for the four gaps, with proposed IDs continuing from the
existing payload range (0x0600-0x0619):

| Param ID | Name | Subsystem | Units | Description | Gap |
|---|---|---|---|---|---|
| 0x0620 | `payload.band_blue_snr` | payload | dB | Blue band (443 nm) SNR | G1 |
| 0x0621 | `payload.band_green_snr` | payload | dB | Green band (560 nm) SNR | G1 |
| 0x0622 | `payload.band_red_snr` | payload | dB | Red band (665 nm) SNR | G1 |
| 0x0623 | `payload.band_nir_snr` | payload | dB | NIR band (865 nm) SNR | G1 |
| 0x0624 | `payload.active_bands` | payload | -- | Count of enabled spectral bands | G1 |
| 0x0625 | `payload.band_enable_mask` | payload | -- | Bitmask of enabled bands | G1 |
| 0x0626 | `payload.att_quality_factor` | payload | -- | Image quality factor from attitude [0-1] | G2 |
| 0x0627 | `payload.att_error_deg` | payload | deg | AOCS attitude error seen by payload | G2 |
| 0x0628 | `payload.gsd_m` | payload | m | Ground sample distance | G3 |
| 0x0629 | `payload.scene_entropy` | payload | -- | Scene entropy estimate [0-1] | G4 |

### 7.2 Parameters That Become Dynamic (Existing)

| Param ID | Name | Change |
|---|---|---|
| 0x0616 | `payload.snr` | Becomes worst-case across all enabled bands (was single-band) |
| 0x0614 | `payload.compression_ratio` | Becomes scene-dependent (was fixed 2.0) |
| 0x0619 | `payload.swath_width_km` | Becomes altitude-dependent (was fixed 30.0) |

### 7.3 Registration in `parameters.yaml`

All 10 new parameters must be added to `configs/eosat1/telemetry/parameters.yaml` under
the `# ===== Payload =====` section, and the corresponding `param_ids` entries must be
added to `configs/eosat1/subsystems/payload.yaml`.

---

## 8. Configuration Schema Changes

### 8.1 Updated `payload.yaml`

The complete target configuration file:

```yaml
model: payload_basic
fpa_cooler_target_c: -5.0
fpa_ambient_c: 5.0
fpa_tau_cooling_s: 100.0
fpa_tau_warming_s: 120.0
fpa_cooler_power_w: 15.0
total_storage_mb: 20000.0
line_rate_hz: 500.0
data_rate_mbps: 80.0
num_memory_segments: 8

# --- NEW: Optics ---
optics:
  ifov_urad: 20.0
  pixels_cross_track: 5000
  focal_length_mm: 500.0
  aperture_mm: 100.0
nominal_altitude_km: 500.0

# --- NEW: Multispectral bands ---
spectral_bands:
  - band_id: blue
    center_nm: 443
    bandwidth_nm: 20
    snr_nominal: 40.0
    gain: 1.2
    dark_current: 5.0
    pixels_cross_track: 5000
  - band_id: green
    center_nm: 560
    bandwidth_nm: 20
    snr_nominal: 50.0
    gain: 1.0
    dark_current: 4.0
    pixels_cross_track: 5000
  - band_id: red
    center_nm: 665
    bandwidth_nm: 20
    snr_nominal: 48.0
    gain: 1.1
    dark_current: 4.5
    pixels_cross_track: 5000
  - band_id: nir
    center_nm: 865
    bandwidth_nm: 40
    snr_nominal: 42.0
    gain: 1.3
    dark_current: 6.0
    pixels_cross_track: 5000
bits_per_pixel: 12

# --- NEW: Attitude coupling ---
attitude_coupling:
  quality_threshold_deg: 0.5
  quality_critical_deg: 1.0
  quality_unusable_deg: 2.0

# --- REMOVED: image_size_mb (now computed from bands + GSD) ---
# image_size_mb: 800.0  # DEPRECATED: replaced by per-band pixel computation

param_ids:
  pli_mode: 0x0600
  fpa_temp: 0x0601
  cooler_pwr: 0x0602
  imager_temp: 0x0603
  store_used: 0x0604
  image_count: 0x0605
  scene_id: 0x0606
  line_rate: 0x0607
  data_rate: 0x0608
  checksum_errors: 0x0609
  mem_total_mb: 0x060A
  mem_used_mb: 0x060B
  last_scene_id: 0x060C
  last_scene_quality: 0x060D
  fpa_ready: 0x0610
  mem_segments_bad: 0x0612
  duty_cycle_pct: 0x0613
  compression_ratio: 0x0614
  cal_lamp_on: 0x0615
  snr: 0x0616
  detector_temp: 0x0617
  integration_time: 0x0618
  swath_width_km: 0x0619
  # New G1-G4 params
  band_blue_snr: 0x0620
  band_green_snr: 0x0621
  band_red_snr: 0x0622
  band_nir_snr: 0x0623
  active_bands: 0x0624
  band_enable_mask: 0x0625
  att_quality_factor: 0x0626
  att_error_deg: 0x0627
  gsd_m: 0x0628
  scene_entropy: 0x0629
```

### 8.2 HK Structure Updates

A new HK structure (or expanded existing SID 6) must include the new parameters.
The per-band SNR values should be collected in the payload HK packet for downlink.

---

## 9. Cross-Subsystem Coupling Requirements

### 9.1 AOCS -> Payload (G2)

**Data path**: `shared_params[0x0217]` (AOCS `att_error`) read by payload `tick()`.

**No engine changes required.** The existing `shared_params` dict is written by AOCS
before payload reads it (confirmed by subsystem iteration order in `engine.py` line 214:
subsystems are iterated in `dict` order, and `_subsys_configs` is loaded from YAML files
which are read alphabetically: aocs before payload).

**Risk**: If the engine's subsystem iteration order changes, the payload could read
stale attitude error. Mitigation: the engine should define explicit tick ordering, or
the payload should accept one-tick latency (1 second at 1x speed), which is acceptable
for this coupling.

### 9.2 Orbit -> Payload (G3, G4)

**Data path**: `orbit_state.alt_km`, `orbit_state.lat_deg`, `orbit_state.lon_deg`
passed directly to `tick()` via the `orbit_state` parameter.

**No engine changes required.** The orbit propagator provides these values.
Verified that `orbit_state` contains `alt_km`, `lat_deg`, `lon_deg` (confirmed in
AOCS model usage at lines 632-634 of `engine.py`).

### 9.3 Payload -> EPS (Existing, Unchanged)

The payload cooler power (`cooler_pwr`, 0x0602) is already published and read by EPS
for power budget computation. The new multispectral model should continue to publish
the same parameter. Per-band power consumption is not needed at this fidelity level
(the FPA cooler is the dominant payload power consumer).

---

## 10. Test Plan

### 10.1 Existing Test Coverage

The file `tests/test_simulator/test_payload_enhanced.py` contains 16 tests covering:
- Mode-gated capture (tests 1-2)
- Image catalog CRUD (tests 3, 6-8, 11)
- Corruption and line dropout injection (tests 4-5)
- Storage limits (test 12)
- Memory segment management (tests 9-10, 13)
- FPA readiness (tests 14-15)
- Telemetry parameter publication (test 16)

All existing tests must continue to pass unchanged (backward compatibility).

### 10.2 New Tests for G1: Multispectral Bands

| Test ID | Test Name | Description | Assertion |
|---|---|---|---|
| G1-01 | `test_band_config_loaded` | Configure with 4 bands, verify state has 4 SpectralBand entries | `len(model._state.bands) == 4` |
| G1-02 | `test_per_band_snr_published` | Tick in IMAGING mode, verify 0x0620-0x0623 in shared_params | All four param IDs present, values > 0 |
| G1-03 | `test_band_snr_varies_by_band` | In IMAGING mode, verify each band has distinct SNR (different nominals) | Blue SNR < Green SNR (per config) |
| G1-04 | `test_disable_band_reduces_data_rate` | Disable NIR band, verify data rate decreases ~25% | `data_rate < 0.80 * full_rate` |
| G1-05 | `test_band_snr_degrades_with_fpa_temp` | Set FPA temp to 5.0 C (warm), verify all band SNRs degraded | All band SNRs < nominal |
| G1-06 | `test_dark_current_temp_dependence` | Verify dark current doubles per ~7 C increase | SNR at +7 C significantly worse |
| G1-07 | `test_aggregate_snr_is_worst_band` | Verify 0x0616 equals min of per-band SNRs | `snr == min(band_snrs)` |
| G1-08 | `test_band_failure_injection` | Inject `band_failure(band_id='nir')`, verify NIR band disabled | `active_bands == 3`, NIR SNR = 0 |
| G1-09 | `test_image_size_scales_with_bands` | Capture with 4 bands vs 2 bands, compare stored sizes | 4-band image ~2x size of 2-band |
| G1-10 | `test_set_band_config_command` | Send `set_band_config` command, verify band state changes | Band enable/disable toggles correctly |

### 10.3 New Tests for G2: Attitude-Quality Coupling

| Test ID | Test Name | Description | Assertion |
|---|---|---|---|
| G2-01 | `test_quality_full_at_zero_att_error` | Set att_error=0.0 in shared_params, tick, verify `att_quality_factor == 1.0` | Quality factor 1.0 |
| G2-02 | `test_quality_degrades_above_0_5_deg` | Set att_error=0.6, tick, verify quality factor < 1.0 | `att_quality_factor < 0.95` |
| G2-03 | `test_quality_severe_above_1_deg` | Set att_error=1.5, tick, verify quality factor < 0.5 | `att_quality_factor < 0.5` |
| G2-04 | `test_quality_unusable_above_2_deg` | Set att_error=3.0, tick, verify quality factor near 0 | `att_quality_factor < 0.2` |
| G2-05 | `test_capture_quality_includes_att_error` | Set att_error=0.8, capture, verify image quality < 100 | `result['quality'] < 90` |
| G2-06 | `test_capture_quality_combines_att_and_fpa` | Set att_error=0.6 AND fpa_degraded, capture | Quality < either factor alone |
| G2-07 | `test_snr_degrades_with_att_error` | Set att_error=1.0, tick, verify SNR < nominal | `snr < 45.0` |
| G2-08 | `test_att_quality_telemetry_published` | Tick, verify 0x0626 and 0x0627 in shared_params | Both params present |
| G2-09 | `test_quality_factor_stable_below_threshold` | Set att_error=0.05 for 100 ticks, verify factor stays 1.0 | No quality degradation |
| G2-10 | `test_att_quality_responds_to_changing_error` | Vary att_error from 0 to 2.0 over ticks, verify monotonic degradation | Quality factor decreases monotonically |

### 10.4 New Tests for G3: Swath from Altitude

| Test ID | Test Name | Description | Assertion |
|---|---|---|---|
| G3-01 | `test_swath_at_nominal_altitude` | Configure with 500 km, tick, verify swath ~50 km | `abs(swath - 50.0) < 1.0` |
| G3-02 | `test_swath_decreases_with_lower_altitude` | Set alt=490 km, verify swath < nominal | `swath < 50.0` |
| G3-03 | `test_swath_increases_with_higher_altitude` | Set alt=510 km, verify swath > nominal | `swath > 50.0` |
| G3-04 | `test_gsd_at_nominal_altitude` | At 500 km, 20 urad IFOV, verify GSD = 10.0 m | `abs(gsd - 10.0) < 0.1` |
| G3-05 | `test_gsd_published_to_telemetry` | Tick, verify 0x0628 in shared_params | Param present with plausible value |
| G3-06 | `test_swath_dynamic_during_orbit` | Simulate 100 ticks with varying altitude, verify swath changes | `max(swaths) > min(swaths)` |
| G3-07 | `test_image_size_varies_with_altitude` | Capture at two altitudes, verify different stored sizes | Sizes differ |

### 10.5 New Tests for G4: Compression Ratio

| Test ID | Test Name | Description | Assertion |
|---|---|---|---|
| G4-01 | `test_compression_varies_with_latitude` | Tick at lat=0 (ocean) vs lat=45 (land), verify different ratios | Ratios differ |
| G4-02 | `test_ocean_has_high_compression` | Set lat=60, lon=180 (Pacific Ocean), verify ratio > 3.0 | `compression_ratio > 3.0` |
| G4-03 | `test_midlat_has_moderate_compression` | Set lat=45, verify ratio between 2.0 and 3.5 | `2.0 < ratio < 3.5` |
| G4-04 | `test_compression_affects_stored_size` | Capture at high vs low compression, verify stored sizes differ | High compression -> smaller image |
| G4-05 | `test_compression_ratio_telemetry_dynamic` | Tick over multiple lat positions, verify 0x0614 changes | Not constant |
| G4-06 | `test_scene_entropy_telemetry_published` | Tick, verify 0x0629 in shared_params | Param present in [0, 1] |
| G4-07 | `test_storage_accounting_uses_compressed_size` | Image IMAGING for 100 ticks, verify mem_used_mb growth rate varies | Not constant growth rate |

### 10.6 Integration Tests

| Test ID | Test Name | Description |
|---|---|---|
| INT-01 | `test_aocs_payload_coupling_e2e` | Run AOCS in DETUMBLE (high att_error) while payload is IMAGING. Verify degraded image quality. Then transition AOCS to FINE_POINT, verify quality recovers. |
| INT-02 | `test_altitude_swath_compression_combined` | Run full orbit (5700 s) with varying alt and lat/lon. Verify swath, GSD, and compression ratio all vary realistically. |
| INT-03 | `test_multispectral_capture_full_pipeline` | Configure 4 bands, go through OFF->STANDBY->IMAGING->capture->download->delete cycle. Verify per-band SNR in telemetry, correct image size accounting. |
| INT-04 | `test_band_failure_plus_att_error` | Inject band failure on blue channel while att_error=0.8. Capture. Verify both degradations reflected: 3 bands only, reduced quality. |

### 10.7 Backward Compatibility Tests

| Test ID | Test Name | Description |
|---|---|---|
| BC-01 | `test_default_config_backward_compat` | Configure with the OLD config (no spectral_bands, no optics). Verify the model still works with default single-band behavior. |
| BC-02 | `test_existing_param_ids_unchanged` | Verify all existing param IDs (0x0600-0x0619) are still published with compatible semantics. |
| BC-03 | `test_existing_commands_unchanged` | Verify all 7 existing commands still work identically. |

---

## 11. Implementation Priority and Effort

### 11.1 Recommended Implementation Order

| Priority | Gap | Reason | Estimated Effort |
|---|---|---|---|
| 1 | **G2** (Attitude coupling) | Highest training value. Makes operators understand AOCS-payload relationship. Minimal code changes. | 2-3 hours |
| 2 | **G3** (Swath from altitude) | Simple physics, high fidelity payoff. Almost no new infrastructure. | 1-2 hours |
| 3 | **G4** (Compression ratio) | Medium complexity, good telemetry realism. No new dataclass needed. | 2-3 hours |
| 4 | **G1** (Multispectral bands) | Highest complexity but most transformative. Requires new dataclass, new config schema, new commands, new HK structure updates. | 6-8 hours |

### 11.2 Lines of Code Estimate

| Component | Current LOC | Estimated LOC After | Delta |
|---|---|---|---|
| `payload_basic.py` (model) | 460 | ~750 | +290 |
| `payload.yaml` (config) | 23 | ~75 | +52 |
| `parameters.yaml` (payload section) | 22 | ~32 | +10 |
| `test_payload_enhanced.py` (tests) | 412 | ~900 | +488 |
| **Total** | **917** | **~1757** | **+840** |

### 11.3 Files to Modify

| File | Changes |
|---|---|
| `packages/smo-simulator/src/smo_simulator/models/payload_basic.py` | All four gaps: new state fields, new tick logic, new commands, new failure modes |
| `configs/eosat1/subsystems/payload.yaml` | New config sections: optics, spectral_bands, attitude_coupling |
| `configs/eosat1/telemetry/parameters.yaml` | 10 new parameter definitions (0x0620-0x0629) |
| `tests/test_simulator/test_payload_enhanced.py` | ~40 new test functions |
| `configs/eosat1/telemetry/hk_structures.yaml` | Add new params to payload HK SID |

### 11.4 No Engine Changes Required

All four gaps can be implemented entirely within the payload model, using the existing
`shared_params` mechanism for cross-subsystem coupling and the `orbit_state` parameter
for orbital geometry. The `SubsystemModel` ABC does not need modification.

---

*This document was generated with AI assistance.*

![AIG - Artificial Intelligence Generated](../../Screenshot%202026-03-10%20at%202.33.26%20PM.png)

Source: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/

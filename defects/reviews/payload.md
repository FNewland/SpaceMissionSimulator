# EO Payload Simulator Review
## Operability Assessment per ECSS Standards

**Review Date**: 2026-04-06
**Reviewer**: Spacecraft Optical Payload & ECSS Systems Engineering Expert
**Scope**: EOSAT-1 EO imaging instrument simulator (payload_basic.py), telemetry/HK structures (SID 5), MCS payload screens, imaging planning tool, and LEOP/nominal operations workflow

---

## 1. Scope & Assumptions

### Mission Context
- **Spacecraft**: EOSAT-1, 450 km sun-synchronous orbit, nadir-pointing EO satellite
- **Payload**: Multispectral pushbroom imager, 4 spectral bands (blue, green, red, NIR), ~800 MB images
- **Image storage**: 20 GB onboard, 8 memory segments, swath width ~30–120 km (altitude-dependent), GSD ~10 m
- **Operators**: Flight Director, Payload Ops, Ground Station with planning/tasking tool

### Standards & References
- **ECSS-E-ST-70-41C**: Packet Utilization Standard (PUS), Services 3, 5, 6, 8, 9, 11, 12, 15, 17, 19, 20
- **CCSDS 121/122/123**: Image compression standards (lossless, DWT, predictive)
- **Common EO practice**: LEOP payload commissioning (first-light, dark/flat-field calibration, detector tuning, focal-plane cooling), nominal imaging campaigns, safe modes, FDIR

### Key Simulator Components
- `payload_basic.py`: FPA thermal model, image catalog, memory segments, capture/download/delete, calibration state machine, multispectral band control, compression, detector settings
- `hk_structures.yaml` SID 5: 20 HK parameters covering mode, temperature, storage, compression, calibration status
- MCS payload screen: mode, FPA temperature, storage fill, image count, memory segments
- Imaging planner: target-opportunity computation, ground-track intersection, swath coverage
- Tools: telemetry report processor, procedure audit

---

## 2. Category 1 — Described, Implemented, Works

### 2.1 Core Payload Model
- **FPA Thermal Control**: Exponential cooling/warming model with ambient coupling, realistic time constants (tau_cool=100s, tau_warm=120s).
- **Payload Modes**: OFF (0), STANDBY (cooler on, ready), IMAGING (active capture). Thermal and power state correctly coupled.
- **Image Capture**: `capture` command succeeds only if mode=IMAGING, FPA ready (temp ≤ -10°C), storage available. Creates image metadata in catalog.
- **Image Download/Delete**: Retrieve by scene_id, delete with storage accounting. Works correctly.
- **Memory Segment Abstraction**: 8 segments, 2.5 GB each, support for bad segments. Total capacity reduced when segment marked bad.
- **Multispectral Bands**: 4 bands defined (blue 443 nm, green 560 nm, red 665 nm, NIR 865 nm), per-band SNR with temperature and attitude degradation.
- **Attitude-Quality Coupling**: SNR degrades with attitude error; quality factor 1.0 at <0.1° to 0.0 at >2°. Flight-realistic.
- **Detector Integration Time**: Per-band integration time configurable (times[0–3] for BGRI), average stored.
- **Detector Gain/Offset**: Set via `set_detector_gain` command (0.1–10.0 clamp).
- **Cooler Setpoint**: Adjustable target temperature (-20°C to 0°C).
- **Calibration State Machine**: `start_calibration` enters DARK_FRAME (0–50% progress), transitions to FLAT_FIELD (50–100%), emits completion event.
- **Compression Ratio**: Scene-entropy model based on latitude (ocean=4.0, complex terrain=1.5).
- **HK Parameters**: 30 parameters mapped to ID 0x0600–0x062F in `hk_structures.yaml` SID 5. Telemetry written at 8 s cadence.
- **Event Generation**: 14 event types (IMAGING_START/STOP, STORAGE_WARNING/CRITICAL/FULL, FPA_OVERTEMP/UNDERTEMP, COOLER_FAILURE, IMAGE_CHECKSUM_ERROR, SNR_DEGRADED, BAD_SEGMENT_DETECTED, CALIBRATION_COMPLETE, COMPRESSION_ERROR) with severity levels.
- **Failure Injection**: `inject_failure()` supports cooler_failure, fpa_degraded, image_corrupt (N images), memory_segment_fail, ccd_line_dropout.
- **Image Corruption Handling**: Corrupt images marked status=2 with low quality (10–30%); partial images status=1 (60–85%); normal status=0.
- **Device-Level Control**: S2 Device Access model (device_states dict) for FPA, cooler, cal lamp, shutter, compression unit.

### 2.2 Telemetry Structure (SID 5)
| Param ID | Name | Type | Scale | Cadence | Notes |
|----------|------|------|-------|---------|-------|
| 0x0600 | payload_mode | B | 1 | 8 s | 0=OFF, 1=STANDBY, 2=IMAGING |
| 0x0601 | fpa_temp | h | 100 | 8 s | FPA temperature, °C × 100 |
| 0x0602 | cooler_power | H | 10 | 8 s | Cooler power, W × 10 |
| 0x0603 | imager_temp | h | 100 | 8 s | Imager enclosure temp, °C × 100 |
| 0x0604 | store_used_pct | H | 100 | 8 s | Storage used, % × 100 |
| 0x0605 | image_count | H | 1 | 8 s | Total images in catalog |
| 0x0609 | checksum_errors | H | 1 | 8 s | Cumulative checksum error count |
| 0x060A | mem_total_mb | I | 1 | 8 s | Total usable memory, MB |
| 0x060B | mem_used_mb | I | 1 | 8 s | Used memory, MB |
| 0x060C | last_scene_id | H | 1 | 8 s | Most recently captured scene ID |
| 0x060D | last_scene_quality | B | 1 | 8 s | Quality of last scene, % |
| 0x0610 | (TBD) | B | 1 | 8 s | Reserved (not currently used) |
| 0x0612 | mem_segments_bad | B | 1 | 8 s | Count of failed segments |
| 0x0614 | compression_ratio | H | 100 | 8 s | Compression ratio × 100 |
| 0x0615 | cal_lamp_status | B | 1 | 8 s | 0=OFF, 1=ON |
| 0x0616 | snr_aggregate | H | 100 | 8 s | Worst-case SNR across enabled bands, dB × 100 |
| 0x0617 | detector_temp_c | h | 100 | 8 s | Detector temperature, °C × 100 |
| 0x062A | calibration_active | B | 1 | 8 s | 0=idle, 1=in progress |
| 0x062B | calibration_progress | H | 100 | 8 s | Progress %, 0–100 × 100 |
| 0x062C | last_calibration_time | I | 1 | 8 s | Epoch time of last completion |
| 0x062D | transfer_active | B | 1 | 8 s | 0=idle, 1=downlinking image |
| 0x062E | transfer_progress | H | 100 | 8 s | Downlink progress, % × 100 |
| 0x062F | integration_time_ms | H | 100 | 8 s | Average integration time, ms × 100 |
| 0x0620–0x0623 | band_snrs (BGRI) | H | 100 | 8 s | Per-band SNR, dB × 100 |
| 0x0624 | active_bands | H | 1 | 8 s | Count of enabled spectral bands |
| 0x0625 | band_enable_mask | H | 1 | 8 s | Bit mask: bits 0–3 = BGRI enable |
| 0x0626 | att_quality_factor | H | 100 | 8 s | Attitude quality, 0–1.0 × 100 |
| 0x0627 | att_error_deg | H | 1000 | 8 s | Attitude error, degrees × 1000 |
| 0x0628 | gsd_m | H | 1 | 8 s | Ground sample distance, m |
| 0x0629 | scene_entropy | H | 100 | 8 s | Scene complexity, 0–1.0 × 100 |
| 0x0613 | duty_cycle_pct | H | 100 | 8 s | FPA duty cycle, % × 100 |

### 2.3 MCS Payload Operations Screen
- **Imager Status** page displays: FPA temperature (gauge), FPA ready (status), storage used (gauge), mode, cooler power, image count, checksum errors, duty cycle.
- **Memory & Catalog** page: memory used/total (MB), bad segments count, last scene ID, last scene quality.
- Both pages update at SID 5 cadence (8 s).

### 2.4 Imaging Planner
- **Target Definition**: Lat/lon bounding boxes, priority, revisit cadence, minimum solar elevation.
- **Opportunity Detection**: Computes when ground track intersects target swath during sunlit conditions (simplified check: not in eclipse).
- **Geometry Computation**: Swath width from FOV and altitude; outputs start/end times, lat/lon, geometry.
- **Output Format**: List of dicts with target_id, priority, timing, geometry for scheduling.

### 2.5 Tests
- `test_payload_enhanced.py`: 30+ unit tests covering capture (requires mode, FPA ready, storage), download, delete, memory segments, corruption, CCD line dropout, band config, integration times, detector gain, cooler setpoint, calibration start/stop, compression override.

---

## 3. Category 2 — Described but Not Implemented

### 3.1 Radiometric Calibration (Critical for Science)
**Issue**: Payload model has `cal_lamp_on` (HK param 0x0615) and calibration state machine (DARK_FRAME → FLAT_FIELD), but **no radiometric gain/bias coefficients** computed or stored.

**What's missing**:
- Dark current subtraction: Dark frame capture and per-pixel dark map update are described in code comments but **not executed** during calibration. Model does not generate or store bias/dark reference images.
- Flat-field correction: Flat frame acquisition mentioned but **no flat-field correction matrix or PRNU map computed**.
- Radiometric gain coefficients: No per-pixel gain map, no absolute radiance calibration to TOA or TOC.
- Validation: No commands to retrieve calibration metadata, no per-band gain curves.

**Impact**: Without radiometric calibration products, the simulator cannot support a realistic imaging campaign. Real EO missions require dark-subtracted, flat-fielded, radiometrically calibrated images.

**Recommended fix**:
- Add `dark_frame_buffer` (image-sized array per band) and `flat_frame_buffer` to PayloadState.
- During DARK_FRAME phase, accumulate dark frames; during FLAT_FIELD, accumulate flat frames.
- Emit calibration-complete event with metadata (frame count, timestamp, validity flags per band).
- Support ground commands to upload/apply calibration matrices (S20 parameter set).
- Include gain/bias per band in HK telemetry (0x063x range).

---

### 3.2 Focal-Plane Array (FPA) Readiness Computation
**Issue**: FPA readiness (`fpa_ready`) is computed as `temp ≤ (target + 5°C)`, i.e., within 5°C of setpoint. **No consideration of**:
- Thermal stabilization time (detector noise degrades during transient)
- Cooler performance margin (risk of thermal runaway if cooler degrades)
- Detector settling time post-power-up
- Thermal hysteresis (minimum time at temperature before declaring ready)

**What's missing**:
- Hysteresis: Model should require `fpa_temp` to stay within range for a minimum duration (e.g., 60 s) before `fpa_ready=True`.
- Thermal sensor error: No ±0.5°C sensor uncertainty.
- Cooler health: Cooler failure should prevent readiness; model has `cooler_failed` flag but readiness ignores it in some code paths.

**Recommended fix**:
- Add `fpa_ready_timer` to PayloadState; increment only if `fpa_temp` is in range; set `fpa_ready=True` only after timer > hysteresis_duration.
- Check `not cooler_failed` in readiness logic.
- Document hysteresis duration in config (default 60 s).

---

### 3.3 Shutter & Filter Wheel Mechanisms
**Issue**: Devices 0x0603 (Shutter) and 0x0604 (Filter wheel) listed in `device_states` dict but **no functional model**:
- No command to cycle shutter (open/close/test).
- No filter selection commands or filter position readback.
- No shutter jitter or duty-cycle limits.
- Filter wheel rotation time not modeled.

**Impact**: LEOP first-light procedure typically includes shutter test and filter wheel verification. Without these, simulator cannot fully exercise commissioning workflows.

**Recommended fix**:
- Add `shutter_position` (CLOSED=0, OPEN=1, STUCK=2) to PayloadState.
- Add `filter_position` (0–N for N filters) and `filter_rotation_time_s`.
- Implement `cycle_shutter(cycles)` and `select_filter(position)` commands.
- Model shutter response time (e.g., 0.5 s open, 0.2 s close).
- Add `shutter_test_cycles` command for self-test.

---

### 3.4 Detector Focus Mechanism
**Issue**: No focus model. Real pushbroom imagers have:
- Focus stage with measured defocus and auto-focus capability.
- MTF (modulation transfer function) degradation with focus error.
- Periodic focus maintenance.

**What's missing**:
- `focus_position_um` (microns) with tolerance ±0.5 μm.
- `defocus_error_um` computed from thermal drift and mechanical creep.
- `mtf_nominal` and `mtf_current` (estimated from defocus).
- Commands: `auto_focus()`, `set_focus(position)`, `get_focus_status()`.

**Impact**: Without focus control, simulator cannot model image quality degradation over mission life or exercise focus correction procedures.

**Recommended fix**:
- Add focus thermal sensitivity (e.g., 10 μm/°C) and compute `defocus = focus_nom + (temp – temp_ref) × sensitivity`.
- Model MTF degradation: `mtf_relative = 1.0 – (defocus_um / tolerance_um)^2` (parabolic).
- Add image SNR penalty for defocus.
- Implement `auto_focus` command that measures edge contrast, adjusts position iteratively.

---

### 3.5 Time-Delay Integration (TDI) vs Frame Mode
**Issue**: Code mentions pushbroom imager but does not distinguish between TDI and frame-mode operations:
- No TDI line rate validation against orbit velocity.
- No frame-mode readout time model.
- Integration time is global, not per-TDI-stage.

**What's missing**:
- `imaging_mode` (TDI, FRAME) in PayloadState.
- TDI-specific: number of stages (8, 16, 32), line rate locked to ground velocity.
- Frame-mode-specific: readout time, frame rate, exposure time.
- Mode-specific MTF and smear characteristics.

**Impact**: TDI and frame modes have different radiometry, smear, and MTF properties. Simulator cannot distinguish them.

**Recommended fix**:
- Add `imaging_mode` enum and per-mode readout logic.
- Validate line rate against orbit state: `line_rate_expected = velocity_m_s / (gsd_m)` within 2%.
- Model TDI smear (charge-transfer loss) and frame smear (integration blur).

---

### 3.6 Image Compression On-Orbit
**Issue**: Compression ratio is modeled (entropy-based, 1.5–4.0×) and stored in HK, but **compression is not applied to image data**:
- No CCSDS 121/122/123 implementation.
- No command to set compression ratio or algorithm.
- No stored image size reduction based on compression.
- Image catalog always uses `image_size_mb = 800.0` regardless of entropy/algorithm.

**What's missing**:
- Payload command: `set_compression(algorithm, ratio)` to select CCSDS 121/122/123 or raw.
- Compression: apply ratio to captured image size in catalog: `stored_size = uncompressed_size / compression_ratio`.
- Per-image compression status in catalog metadata.
- Compression error handling: `handle_command("set_compression", algorithm="invalid")` should fail gracefully.

**Impact**: Storage utilization and downlink scheduling depend on actual compressed size. Without compression modeling, MCS/planner cannot accurately forecast storage exhaustion or downlink duration.

**Recommended fix**:
- Add `compression_algorithm` (0=none, 1=CCSDS121, 2=CCSDS122, 3=CCSDS123) to PayloadState.
- Modify `capture` command: `actual_stored_size = image_size_mb / compression_ratio` (or per-algorithm formula).
- Add `compression_status` (success, error) to image catalog metadata.
- Implement `set_compression` command with validation.

---

### 3.7 Image Downlink & Transfer Control
**Issue**: `transfer_active` and `transfer_progress` are in HK and state machine, but **no downlink command or transfer scheduling model**:
- No S15 (downlink) service integration or command handler.
- No segmented downlink (multiple passes required for large images).
- No downlink rate or link budget coupling.
- `transfer_progress` increments are not driven by real downlink commands.

**What's missing**:
- Payload command: `initiate_transfer(scene_id, segment_size_bytes)` to start downlink.
- Ground service: S15 enable/disable TM transfer.
- Transfer state: track TM packet queue, downlink rate, expected transfer time.
- Link integration: transfer speed depends on TT&C link margin, modulation, data rate.

**Impact**: LEOP and nominal ops include imaging → compression → storage → downlink → deletion workflows. Without downlink modeling, operators cannot verify end-to-end flow.

**Recommended fix**:
- Add `transfer_scene_id`, `transfer_segment_bytes_remaining` to PayloadState.
- Implement `initiate_transfer(scene_id)` command that queues image for S15 downlink.
- Tick: if `transfer_active` and link available, decrement `transfer_segment_bytes_remaining` at link data rate.
- On completion, emit TRANSFER_COMPLETE event.
- MCS: display current downlink progress and ETA.

---

### 3.8 Payload Safe Mode & FDIR Fault Thresholds
**Issue**: Payload has failure injection (`cooler_failure`, `fpa_degraded`, `image_corrupt`, `ccd_line_dropout`) and event generation, but **no FDIR decision logic** within the payload:
- No threshold checks for fault isolation (e.g., checksum error count > 10/min → declare line dropout).
- No automatic payload shutdown or mode transition on fault.
- No hot-spare or redundancy switching (real EO payloads have dual detectors or filter wheels).

**What's missing**:
- FDIR logic: Monitor event frequency; if checksum_errors > threshold in time window, set `fpa_degraded=True` and emit HIGH alarm.
- Safe mode: Payload should enter OFF or STANDBY on unrecoverable fault, not continue imaging.
- Recovery procedures: Cooler restart, detector reset, memory scrub.
- Redundancy: Model for dual FPA or filter wheel options.

**Impact**: Real payload ops include fault detection and recovery playbooks. Simulator cannot exercise these without FDIR logic.

**Recommended fix**:
- Add payload FDIR rules to `/configs/eosat1/fdir/` (similar to existing AOCS/EPS rules).
- Monitor payload event rate; transition mode to OFF or STANDBY on threshold breach.
- Implement `reset_payload()` command for cold restart.
- Document expected FDIR thresholds and recovery times.

---

### 3.9 Parameter Commanding via S20 (PUS Service 20)
**Issue**: Payload commands are handled via `handle_command()` internal dict, but **no S20 integration**:
- S20 Parameter Management should support uplink of per-parameter settings (detector_gain, cooler_setpoint, integration_time, band_enable_mask, compression_ratio).
- No ground-to-payload parameter load-store workflow.
- No parameter lock/unlock or change-counters.

**What's missing**:
- S20.1 (report parameter value) handlers for all payload params.
- S20.2 (enable parameter monitoring) for critical params (FPA temp, storage, SNR).
- S20.3 (disable parameter monitoring).
- S20.4 (set parameter value) routing for payloads commands (set_cooler_setpoint, set_band_config, etc.).

**Impact**: Ground operators expect to command payload via PUS-compliant telecommand packets, not proprietary APIs.

**Recommended fix**:
- Map all writable payload parameters to S20 parameter IDs (0x0680–0x06FF range).
- Add S20 dispatcher in `service_dispatch.py` to route parameter sets to payload.
- Implement S20.3 (report current value) handlers with proper scaling.
- Test S20 up/down workflows in commissioning tests.

---

## 4. Category 3 — Not Yet Described but Needed

### 4.1 Detector Noise & Readout Electronics
**Issue**: SNR is modeled (temperature, attitude), but **no shot noise, read noise, or quantization**:
- No shot noise level (√N Poisson) per band.
- No read noise floor (e.g., 50 e- rms for typical CMOS).
- No ADC quantization (10, 12, 14 bits) and associated signal-to-noise degradation.
- No fixed-pattern noise (FPN) or pixel-to-pixel gain variations (PRNU).

**Why needed**: LEOP calibration workflows include measuring read noise (via dark frames) and PRNU (via flat fields). Without these models, commissioning test results won't match flight hardware.

**Recommended implementation**:
- Add `read_noise_e_rms` per band to config.
- Add `shot_noise_factor` (typically 1.0).
- In SNR calculation: `snr_db = 20 * log10(signal / sqrt(read_noise^2 + shot_noise))`.
- Model PRNU: `gain_variation_pct` per band, applied during flat-field acquisition.

---

### 4.2 Bad-Pixel Map & Pixel-Level Failures
**Issue**: Memory segments can fail, but individual detector **pixels cannot fail**:
- No bad-pixel map (cosmetic defects) or dead pixels.
- No hot pixels (elevated dark current, visible as stripes in dark frames).
- No column/line defects (inherent to CCD/CMOS architectures).

**Why needed**: Flight payloads accumulate bad pixels over mission life (radiation, thermal cycling, delamination). Ground processing must know pixel-level defect locations for image restoration.

**Recommended implementation**:
- Add `bad_pixel_count` and `hot_pixel_count` to PayloadState.
- Track pixel coordinates in a sparse map (e.g., `bad_pixel_map: list[tuple(row, col)]`).
- Include bad-pixel metadata in image catalog (or separately in HK).
- Provide command: `report_bad_pixel_map()` to retrieve defect locations.
- Gradually increase bad-pixel count under radiation stress or thermal cycling faults.

---

### 4.3 Instrument Spectral Response Variation
**Issue**: Spectral bands have nominal SNR and center wavelength, but **no spectral response function (SRF) or out-of-band rejection**:
- No wavelength-dependent transmission (passband shape).
- No stray light or cross-talk between bands.
- No temperature-dependent filter shift (real optical filters shift wavelength with temperature).

**Why needed**: Multispectral calibration requires accurate knowledge of each band's SRF for vicarious calibration. Stray light can bias radiance measurements, especially in coastal/desert scenes.

**Recommended implementation**:
- Add `spectral_response_curve` per band (list of wavelength, transmission pairs).
- Model temperature-dependent center-wavelength shift: `λ_eff(T) = λ_nom + dλ/dT × (T – T_ref)`.
- Implement stray-light model: add 1–3% of neighboring-band signal to output.

---

### 4.4 Attitude-Dependent Image Quality
**Issue**: SNR degradation with attitude is modeled, but **no image smear or jitter**:
- No smear (directional blur along-track) from spacecraft angular velocity during exposure.
- No jitter (high-frequency pointing noise) causing loss of MTF.
- No motion-compensation or drift-correction logic.

**Why needed**: Real pushbroom imagers require attitude stability <0.05° (3-sigma). Image smear and jitter directly impact GSD and classification accuracy. LEOP includes jitter measurement and mitigation procedures.

**Recommended implementation**:
- Add `along_track_smear_pixels` computed from attitude rate and integration time: `smear = rate_deg_s × integration_time × pixel_scale_deg`.
- Model MTF loss from smear: `mtf_smear_factor = sinc(π × smear_fraction)`.
- Add high-frequency jitter RMS (e.g., 0.01° 3-sigma) and convolve with PSF.
- Include smear/jitter in `last_scene_quality` metric.

---

### 4.5 Thermal-Vacuum Cycling & Detector Aging
**Issue**: Cooler failure is injected, but **no gradual degradation** over mission time:
- No dark-current increase with time (thermal aging, annealing).
- No cooler performance degradation (compressor wear, refrigerant loss).
- No permanent FPA damage model (e.g., if overhemp >20°C for >5 min, pixel damage occurs).

**Why needed**: Extended LEOP and operational campaigns reveal aging effects. Realistic commissioning must account for measurable drift in dark current and cooler efficiency.

**Recommended implementation**:
- Add `mission_days` counter; use it to scale dark-current baseline: `dark_current = nominal + age_factor × sqrt(mission_days)`.
- Cooler efficiency: `cooler_power_actual = cooler_power_nom × (1 – 0.001 × mission_days)` (0.1% degradation per day).
- Overheat damage: if `fpa_temp > limit` for duration > threshold, increment `fpa_degraded` flag and emit damage alert.
- Track with new HK params: `mission_days`, `fpa_damage_indicator`, `cooler_efficiency_pct`.

---

### 4.6 Geocoding & Attitude Quaternion Logging
**Issue**: Image metadata includes lat/lon from command input, but **no actual geocoding** from AOCS state:
- Captured images use manually-set lat/lon from capture command, not actual sub-satellite point.
- No attitude quaternions stored with image (needed for ortho-rectification).
- No GCP (ground control point) matching or geometric correction.

**Why needed**: Science data processing requires accurate geolocation. LEOP includes geometric validation and GCP matching exercises.

**Recommended implementation**:
- Modify `capture` command handler to read orbit_state (attitude, position) and use actual sub-satellite point, not command lat/lon.
- Store quaternion, velocity vector, and altitude with image metadata.
- Compute RPC (rational polynomial coefficients) on-ground from geocoded calibration scenes.

---

### 4.7 Polarization & Image Stacking
**Issue**: No mention of polarization or image stacking:
- Some modern EO payloads measure orthogonal polarizations (VV, VH, HH, HV for radar, or linear polarizations for optical).
- Multi-frame averaging (stack N frames to reduce noise).

**Why needed**: Scientific applications (change detection, SAR, polarimetric analysis) require polarization. Stacking is a noise-reduction technique.

**Recommended implementation** (advanced, future phase):
- If applicable to EOSAT-1 mission, add polarization bands and stacking parameters to config.
- Model polarization-specific SNR and cross-talk.

---

## 5. Category 4 — Implemented but Not Helpful for This Mission

### 5.1 Ocean-Current-Specific Entropy Model
**Issue**: `scene_entropy` is modeled as latitude-based: `entropy = 0.2 (base) + 0.3 × seasonal_factor(lat)`, mapping to compression ratio 4.0–1.5.

**Why not helpful**:
- Entropy model is hardcoded for ocean/land scenes (latitude-dependent).
- Real compression depends on actual image data (histogram, texture) and chosen algorithm (CCSDS 121/122/123 each have different efficiency).
- Model does not reflect algorithm-specific performance.

**Recommendation**: Simplify to algorithm-driven compression ratios (e.g., CCSDS 121: 2.0–3.5×, CCSDS 123: 3.0–5.0×) rather than scene-dependent curves. Actual compression would be post-processing on ground or a real lossy codec.

---

### 5.2 Manual Scene ID Advancement
**Issue**: `current_scene_id` can be set via `set_scene` command, but image capture auto-increments `_next_scene_id`. This decouples user scene numbering from actual capture sequence.

**Why not helpful**:
- If ground tries to track images by `current_scene_id` and payload increments `_next_scene_id`, counters will diverge.
- Leads to confusion in image catalog retrieval.

**Recommendation**: Use `_next_scene_id` as single source of truth; remove `set_scene` command or clarify its purpose (e.g., only for planning, not image numbering).

---

## 6. Category 5 — Inconsistent / Incoherent Implementation

### 6.1 Image Quality Parameter Mismatch
**Issue**: Image catalog stores `quality` (0–100 %), but quality is not consistently derived from SNR, smear, defocus, or attitude:
- In `capture`, quality is set to 100%, 10–30% (corrupt), or 60–85% (CCD dropout) — hardcoded ranges, no formula.
- In HK, `last_scene_quality` is reported, but no ongoing SNR-to-quality conversion.
- No quality metric reflected in image size or compression ratio.

**Recommendation**: Define quality metric: `quality = min(snr_factor, mtf_factor, attitude_factor) × 100`, where each factor is 0–1. Store formula in config; apply consistently to all capture paths.

---

### 6.2 Memory Segment Accounting Ambiguity
**Issue**: `segment_size_mb` is `total_storage_mb / num_segments = 20000 / 8 = 2500 MB`. But images are 800 MB each. When marking a segment bad:
- Code sets `mem_total_mb = usable_segments × segment_size_mb`.
- But image allocation logic does not explicitly place images in segments; it just checks `_available_storage_mb()`.
- If an 800 MB image spans two segments (bytes 2400–3200 MB of 2500 MB boundary), and second segment fails, is image lost?

**Recommendation**: Clarify segment allocation model: does image land in first available segment, or can it span multiple? Add explicit image-to-segment mapping in catalog and validate on bad-segment fault.

---

### 6.3 Calibration State Machine Semantics
**Issue**: Calibration states are DARK_FRAME (0–50% progress) → FLAT_FIELD (50–100% progress), but:
- No distinction between in-flight vs ground-based calibration.
- No input requirements (solar illumination for flat field, thermal stability for dark frame).
- Progress bars are synthetic timer-driven, not actual frame-accumulation counts.
- No output: dark/flat coefficients are not generated or stored.

**Recommendation**:
- Rename states for clarity: PRE_IMAGING (thermal stabilization), DARK_FRAME (accumulate bias), FLAT_FIELD (accumulate flat under solar/cal-lamp), COMPLETE (compute coefficients).
- Add prerequisites: dark frame requires cooler stable, flat field requires adequate illumination.
- Store actual frame counts and coefficient generation logic.

---

### 6.4 Cooler Failure vs. FPA Degradation
**Issue**: Both `cooler_failed` (cooler compressor broken) and `fpa_degraded` (detector damage) are separate flags, but their effects are not clearly distinguished:
- `cooler_failed=True` should prevent FPA temperature from reaching setpoint; code does this.
- `fpa_degraded=True` should reduce SNR and increase dark current; code reduces SNR but doesn't increase dark noise.
- If cooler fails in middle of imaging pass, what happens to current frame? Not modeled.

**Recommendation**: Clarify fault semantics in config; ensure fpa_degraded increases read noise and dark current per band; model cooler transient response (warm-up ramp time).

---

### 6.5 Band Enable Mask vs. Active Bands
**Issue**: Both `band_enable_mask` (bitwise 0x0F) and `active_bands` (count) are in PayloadState. They should be consistent, but no explicit sync:
- `set_band_config(mask=0x0F)` sets both mask and `active_bands = bin(mask).count('1')`.
- But if code directly mutates `band_enable_mask` without updating `active_bands`, they will diverge.

**Recommendation**: Use property setter or validation in tick() to ensure `active_bands == popcount(band_enable_mask)` after every operation.

---

### 6.6 Parameter ID Ranges Fragmented
**Issue**: Payload HK parameters span 0x0600–0x062F with gaps:
- 0x0606, 0x0607, 0x0608 are line_rate, data_rate (omitted from hk_structures.yaml).
- 0x0610 is reserved/unused.
- 0x060E, 0x060F, 0x0611, 0x0613 are missing or unclear.

**Recommendation**: Document all 0x0600–0x062F allocations in a central parameter registry. Add missing parameters to hk_structures.yaml (line_rate, data_rate) and clarify reservations.

---

## 7. Top-5 Prioritized Defects

### Defect 1: Missing Radiometric Calibration Products

**Severity**: CRITICAL
**Impact**: Payload simulator cannot produce radiometrically calibrated images, breaking ground science-data-processing workflows and operator confidence in LEOP calibration procedures.

**Title**: Payload simulator lacks dark frame, flat field, and gain coefficient generation

**Description**:
The calibration state machine (DARK_FRAME → FLAT_FIELD → COMPLETE) exists but produces no calibration images or coefficients. Real EO missions require:
1. Dark frames (N stacked; dark current subtraction)
2. Flat-field frames (N stacked; vignetting correction)
3. Per-pixel gain map and bias offset per spectral band
4. Validity flags per band and frame set

Current implementation:
- `calibration_active`, `calibration_state`, `calibration_progress` tracked in state.
- On completion, emits event 0x060B (CALIBRATION_COMPLETE).
- **But**: No dark or flat-frame buffers created; no coefficients computed; no metadata stored in image catalog or HK.

**Test scenario** (from LEOP):
1. Power on payload, cool to setpoint.
2. Issue `start_calibration` command.
3. Expect: on completion, ground can retrieve dark image, flat image, gain/bias coefficients per band via S20 or S5 downlink.
4. Actual: no data returned.

**Files affected**:
- `packages/smo-simulator/src/smo_simulator/models/payload_basic.py` (PayloadState, handle_command, tick)
- `configs/eosat1/subsystems/payload.yaml` (add parameters: dark_frame_buffer_ready, flat_frame_buffer_ready, gain_coeff_valid_mask)
- `configs/eosat1/telemetry/hk_structures.yaml` SID 5 (add 0x0630–0x0635 for calibration metadata)

**Suggested fix**:
1. Add to PayloadState:
   ```python
   dark_frame_buffer: dict = field(default_factory=lambda: {
       'blue': None, 'green': None, 'red': None, 'nir': None
   })
   flat_frame_buffer: dict = ...
   gain_coeff: dict = ...  # per-band arrays [H × W]
   bias_coeff: dict = ...
   calibration_valid_mask: int = 0x00  # bitwise flags for 4 bands
   ```

2. During DARK_FRAME phase: accumulate N frames into dark_frame_buffer[band].
3. During FLAT_FIELD phase: accumulate N frames into flat_frame_buffer[band].
4. On completion:
   - Compute `bias_coeff = mean(dark_frame_buffer)` per band
   - Compute `gain_coeff = mean(flat_frame_buffer) / reference_flat` per band
   - Set `calibration_valid_mask = 0x0F` (all bands valid)
   - Emit event with metadata

5. Add HK params 0x0630–0x0635:
   - 0x0630: `calibration_valid_mask` (B, scale 1)
   - 0x0631: `dark_frame_count_accumulated` (H, scale 1)
   - 0x0632: `flat_frame_count_accumulated` (H, scale 1)
   - 0x0633: `gain_coeff_mean_value` (H, scale 1000) [for status only, not full 2D array]

6. Add S20 commands to retrieve calibration data (ground can request dark/flat images via S5).

---

### Defect 2: FPA Readiness Lacks Hysteresis & Cooler Dependency

**Severity**: HIGH
**Impact**: Payload can report "ready" while still thermally unstable or with cooler degraded, leading to poor image quality and operator confusion during LEOP.

**Title**: FPA readiness (`fpa_ready`) ignores cooler health and lacks thermal hysteresis

**Description**:
Current logic:
```python
s.fpa_ready = s.fpa_temp <= (self._fpa_target + 5.0)
```

Issues:
1. No hysteresis: If FPA temperature oscillates around setpoint (e.g., cooler hunting ±2°C), `fpa_ready` flaps on and off every second, causing image capture failures and alarm spam.
2. Ignores `cooler_failed`: If cooler is broken, FPA will warm toward ambient and never stabilize at setpoint, but readiness is still computed. Should fail fast.
3. No settling time: Real detectors need 2–5 minutes of stable temperature before dark-current noise baseline is achieved.

**Scenario**:
1. Boot payload at 5°C ambient.
2. Cooler on; FPA cools from +5°C to –15°C target over ~100 s (tau_cool).
3. Current logic: at t=100 s, `fpa_temp ≈ –15°C`, so `fpa_ready=True`.
4. Actual: Thermal oscillations, cooler transients, and sensor lag mean dark current is still drifting. Real operator would wait another 60–120 s.

**Test scenario**:
1. In LEOP, issue `set_mode(1)` (STANDBY) to power cooler.
2. Poll `fpa_ready` every second.
3. Expected: `fpa_ready=False` for ~100 s, then transitions to `True` and **stays true** (no flapping).
4. Actual: May flap (false – true – false – true) if temperature oscillation.

**Files affected**:
- `packages/smo-simulator/src/smo_simulator/models/payload_basic.py` (PayloadState, tick method)
- `configs/eosat1/subsystems/payload.yaml` (add fpa_ready_hysteresis_duration_s)

**Suggested fix**:
1. Add to PayloadState:
   ```python
   fpa_ready_timer: float = 0.0  # time temp has been in range
   fpa_ready_hysteresis_s: float = 60.0  # config parameter
   ```

2. In tick(), modify readiness logic:
   ```python
   temp_in_range = (self._fpa_target - 1.0) <= s.fpa_temp <= (self._fpa_target + 5.0)
   cooler_ok = not s.cooler_failed

   if temp_in_range and cooler_ok:
       s.fpa_ready_timer += dt
       if s.fpa_ready_timer >= s.fpa_ready_hysteresis_s:
           s.fpa_ready = True
   else:
       s.fpa_ready = False
       s.fpa_ready_timer = 0.0
   ```

3. In config, set `fpa_ready_hysteresis_s: 60.0` (or tunable per mission phase).

4. Add unit test: cool payload, poll readiness every 1 s for 120 s, assert no flapping.

---

### Defect 3: Image Compression Not Applied; Stored Size Constant

**Severity**: HIGH
**Impact**: Storage and downlink planning is inaccurate. Operators expect compressed images but model always uses full 800 MB, leading to storage exhaustion underestimation and downlink scheduling errors.

**Title**: Image compression ratio computed but not applied to stored image size

**Description**:
Current state:
- Payload computes `compression_ratio` (1.5–4.0× based on scene entropy) and reports in HK (0x0614).
- But all captured images in catalog have fixed `size_mb = 800.0` regardless of compression.
- In real systems, stored size would be `image_size_mb / compression_ratio`.

Example:
- Ocean scene (low entropy): compression_ratio = 4.0×, stored_size = 800 / 4 = 200 MB.
- Complex scene (high entropy): compression_ratio = 1.5×, stored_size = 800 / 1.5 ≈ 533 MB.
- Planner can pack 25 ocean images vs. 16 complex images in 5 GB.

Current simulator: always 25 images (800 MB) in 5 GB regardless of scene type.

**Test scenario**:
1. Capture images over ocean (entropy ~ 0.2, compression_ratio ~ 4.0).
2. Query image catalog; check stored size.
3. Expected: size_mb ≈ 200 MB.
4. Actual: size_mb = 800 MB.
5. Storage utilization: expected 25 images, actual 5.

**Files affected**:
- `packages/smo-simulator/src/smo_simulator/models/payload_basic.py` (handle_command("capture"))
- `configs/eosat1/subsystems/payload.yaml` (add compression_algorithm, compression_enabled)

**Suggested fix**:
1. Add to PayloadState:
   ```python
   compression_algorithm: int = 3  # 0=none, 1=CCSDS121, 2=CCSDS122, 3=CCSDS123
   compression_enabled: bool = True
   ```

2. Modify `handle_command("capture")`:
   ```python
   base_size = s.image_size_mb
   if s.compression_enabled:
       stored_size = base_size / s.compression_ratio
   else:
       stored_size = base_size

   img = {
       "scene_id": ...,
       "timestamp": ...,
       ...
       "size_mb": stored_size,  # CHANGE FROM FIXED TO COMPUTED
       "compression_ratio": s.compression_ratio,
       "algorithm": s.compression_algorithm,
   }
   ```

3. In memory accounting:
   ```python
   s.mem_used_mb += stored_size
   s.store_used_pct = min(100.0, s.mem_used_mb / s.mem_total_mb * 100.0)
   ```

4. Add command `set_compression(algorithm, ratio=0)`:
   ```python
   elif command == "set_compression":
       algo = int(cmd.get("algorithm", 3))
       if 0 <= algo <= 3:
           s.compression_algorithm = algo
           if "ratio" in cmd:
               ratio = float(cmd.get("ratio", 0.0))
               if ratio > 0:
                   s.compression_override = ratio
                   s.compression_ratio = ratio
           return {"success": True}
   ```

5. Add test: capture ocean & complex scenes, verify stored_size differs per scene type.

---

### Defect 4: No Shutter/Filter Wheel Functional Models; First-Light Procedure Incomplete

**Severity**: MEDIUM
**Impact**: LEOP first-light procedures (shutter cycle, filter test, thermal stabilization) cannot be fully simulated or validated. Operators have no way to verify shutter and filter wheel are functional.

**Title**: Payload shutter (0x0603) and filter wheel (0x0604) are stubs; no functional commands or state tracking

**Description**:
Current state:
- `device_states` dict has entries for shutter (0x0603) and filter wheel (0x0604).
- Only S2 Device Access on/off control is available (no detail).
- No shutter position, rotation time, or filter selection commands.
- No shutter jitter model, duty-cycle limits, or failure modes.

Real LEOP first-light procedure includes:
1. Shutter cycle test (open → close → open) to verify mechanism.
2. Filter wheel rotation test (step through all positions) to verify detents and limit switches.
3. Shutter thermal drift measurement (how much does closing time increase with temperature).

**Test scenario** (from LEOP checklist):
1. Issue command: `cycle_shutter(cycles=10)` to open/close shutter 10 times.
2. Expect: operation completes in ~10 s (0.5 s per cycle), no jams, position reaches open/closed end-stops.
3. Actual: no such command; device_states[0x0603] can only be turned on/off globally.

**Files affected**:
- `packages/smo-simulator/src/smo_simulator/models/payload_basic.py` (PayloadState, handle_command)
- `configs/eosat1/subsystems/payload.yaml` (add shutter and filter parameters)

**Suggested fix**:
1. Add to PayloadState:
   ```python
   shutter_position: int = 1  # 0=CLOSED, 1=OPEN, 2=STUCK_OPEN, 3=STUCK_CLOSED
   shutter_cycles_completed: int = 0
   shutter_test_active: bool = False
   shutter_test_cycles_remaining: int = 0

   filter_position: int = 0  # 0–N for N filters
   filter_rotation_in_progress: bool = False
   filter_target_position: int = 0
   filter_rotation_timer: float = 0.0
   filter_rotation_time_s: float = 2.0  # per 90° step
   ```

2. Add command handlers:
   ```python
   elif command == "cycle_shutter":
       cycles = int(cmd.get("cycles", 1))
       if not s.shutter_test_active:
           s.shutter_test_active = True
           s.shutter_test_cycles_remaining = cycles
           s.shutter_position = 0 if s.shutter_position == 1 else 1  # toggle
           return {"success": True, "message": f"Starting shutter cycle {cycles}"}
       else:
           return {"success": False, "message": "Shutter test already in progress"}

   elif command == "select_filter":
       position = int(cmd.get("position", 0))
       if 0 <= position <= num_filters:
           s.filter_target_position = position
           s.filter_rotation_in_progress = True
           s.filter_rotation_timer = s.filter_rotation_time_s * abs(position - s.filter_position) / 90
           return {"success": True, "message": f"Rotating to filter {position}"}
       else:
           return {"success": False, "message": "Invalid filter position"}

   elif command == "get_shutter_status":
       return {
           "success": True,
           "position": s.shutter_position,
           "test_in_progress": s.shutter_test_active,
           "cycles_completed": s.shutter_cycles_completed,
       }

   elif command == "get_filter_status":
       return {
           "success": True,
           "position": s.filter_position,
           "rotation_in_progress": s.filter_rotation_in_progress,
           "target_position": s.filter_target_position,
       }
   ```

3. In tick():
   ```python
   # Shutter cycle test
   if s.shutter_test_active:
       s.shutter_test_cycles_remaining -= 1
       if s.shutter_test_cycles_remaining <= 0:
           s.shutter_test_active = False
           s.shutter_cycles_completed += 1
           events_to_emit.append(0x060E)  # SHUTTER_TEST_COMPLETE
       else:
           s.shutter_position = 0 if s.shutter_position == 1 else 1  # toggle every tick

   # Filter wheel rotation
   if s.filter_rotation_in_progress:
       s.filter_rotation_timer -= dt
       if s.filter_rotation_timer <= 0:
           s.filter_position = s.filter_target_position
           s.filter_rotation_in_progress = False
           events_to_emit.append(0x060F)  # FILTER_ROTATION_COMPLETE
   ```

4. Add HK params (0x0640–0x0645):
   - 0x0640: `shutter_position` (B: 0=closed, 1=open)
   - 0x0641: `shutter_cycles_completed` (H)
   - 0x0642: `filter_position` (B)
   - 0x0643: `filter_rotation_in_progress` (B)

5. Add test: cycle shutter 5 times, verify position transitions; select filters, verify rotation time.

---

### Defect 5: No Integration of Downlink Commands; Transfer Progress Not Driven

**Severity**: MEDIUM
**Impact**: Complete imaging workflows (capture → compress → store → downlink → delete) cannot be validated. Operators cannot verify end-to-end data flow or estimate downlink completion time.

**Title**: Payload `transfer_active` and `transfer_progress` exist but are never actuated by downlink commands

**Description**:
Current state:
- PayloadState has `transfer_active`, `transfer_id`, `transfer_progress`.
- HK params 0x062D (transfer_active), 0x062E (transfer_progress) are reported.
- But no downlink command triggers these.
- `transfer_progress` is never incremented; stays at 0.0.

Missing integration:
- S15 (Onboard Data Repository) downlink commands should query payload for image data.
- Payload should queue image for download, start transfer, and update progress.
- TT&C downlink rate should couple to payload transfer speed.

Real workflow:
1. Payload captures image (800 MB → 200 MB after compression).
2. Ground plans pass with 2 Mbps downlink rate.
3. Ground sends S15 downlink command: "Download image scene_id=101, start at byte 0."
4. Payload queues image, sets `transfer_active=True`.
5. Over 800 s (5 Mbps link rate × 200 MB compressed size), payload ticks down `transfer_progress`.
6. Ground monitors via HK telemetry.

Current simulator: no such coupling; transfer is phantom telemetry.

**Test scenario** (from nominal ops):
1. Capture an image (size=200 MB after compression).
2. Command: `initiate_transfer(scene_id=101)`.
3. Every tick, monitor HK param 0x062E (transfer_progress).
4. Expected: progress increments from 0 to 100 over ~160 s (at nominal 10 Mbps TT&C rate).
5. Actual: no initiate_transfer command; transfer_progress stays 0.

**Files affected**:
- `packages/smo-simulator/src/smo_simulator/models/payload_basic.py` (handle_command, tick)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (S15 service integration)
- `configs/eosat1/subsystems/payload.yaml` (add transfer parameters)

**Suggested fix** (Phase 1 — payload side):
1. Add to PayloadState:
   ```python
   transfer_scene_id: int = 0
   transfer_bytes_total: int = 0
   transfer_bytes_sent: int = 0
   transfer_rate_mbps: float = 10.0  # nominal TT&C downlink rate
   transfer_segment_start_byte: int = 0
   transfer_segment_size_bytes: int = 0
   ```

2. Add command handler:
   ```python
   elif command == "initiate_transfer":
       scene_id = int(cmd.get("scene_id", 0))
       # Find image in catalog
       for img in s.image_catalog:
           if img["scene_id"] == scene_id:
               s.transfer_scene_id = scene_id
               s.transfer_bytes_total = int(img["size_mb"] * 1e6)
               s.transfer_bytes_sent = 0
               s.transfer_active = True
               s.transfer_progress = 0.0
               events_to_emit.append(0x0610)  # TRANSFER_START
               return {"success": True, "message": f"Transfer {scene_id} initiated"}
       return {"success": False, "message": f"Image {scene_id} not found"}
   ```

3. In tick():
   ```python
   if s.transfer_active and s.transfer_bytes_total > 0:
       # Downlink rate: bytes per second
       bytes_per_sec = s.transfer_rate_mbps * 1e6 / 8
       bytes_this_tick = bytes_per_sec * dt
       s.transfer_bytes_sent = min(s.transfer_bytes_total, s.transfer_bytes_sent + bytes_this_tick)
       s.transfer_progress = 100.0 * s.transfer_bytes_sent / s.transfer_bytes_total

       if s.transfer_bytes_sent >= s.transfer_bytes_total:
           s.transfer_active = False
           s.transfer_progress = 100.0
           events_to_emit.append(0x0611)  # TRANSFER_COMPLETE
   ```

4. Add HK params:
   - 0x0644: `transfer_rate_mbps` (H, scale 10)
   - 0x0645: `transfer_bytes_total` (I, scale 1000)

5. Add test: initiate transfer, tick for duration, verify progress increments to 100.

6. Phase 2 (later): Integrate S15 downlink service to automatically trigger `initiate_transfer` when ground sends downlink request.

---

## 8. Parameter/Command Coverage Table

Comprehensive mapping of all payload parameters: HK availability, S20 commandability, MCS visibility.

| Param ID | Name | In HK? | HK Scale | S20 Writable? | S20 Readable? | MCS Widget? | Notes |
|----------|------|--------|-----------|---------------|---------------|------------|-------|
| 0x0600 | payload_mode | Yes | 1 | Yes (via S19.65) | Yes | Gauge (mode) | OFF/STANDBY/IMAGING |
| 0x0601 | fpa_temp | Yes | 100 | No | Yes | Gauge (°C) | °C × 100 |
| 0x0602 | cooler_power | Yes | 10 | No | Yes | Value table | W × 10 |
| 0x0603 | imager_temp | Yes | 100 | No | Yes | No | °C × 100 |
| 0x0604 | store_used_pct | Yes | 100 | No | Yes | Gauge (%) | % × 100 |
| 0x0605 | image_count | Yes | 1 | No | Yes | Value table | Count |
| 0x0609 | checksum_errors | Yes | 1 | No | Yes | Value table | Count |
| 0x060A | mem_total_mb | Yes | 1 | No | Yes | Gauge (MB) | MB |
| 0x060B | mem_used_mb | Yes | 1 | No | Yes | Gauge (MB) | MB |
| 0x060C | last_scene_id | Yes | 1 | No | Yes | Value table | Scene ID |
| 0x060D | last_scene_quality | Yes | 1 | No | Yes | Value table | % |
| 0x0610 | (reserved) | No | — | — | — | — | — |
| 0x0612 | mem_segments_bad | Yes | 1 | No | Yes | Value table | Count |
| 0x0613 | duty_cycle_pct | Yes | 100 | No | Yes | Value table | % × 100 |
| 0x0614 | compression_ratio | Yes | 100 | Partial | Yes | No | Ratio × 100; read-only (scene-dependent) |
| 0x0615 | cal_lamp_status | Yes | 1 | Yes | Yes | No | 0/1 |
| 0x0616 | snr_aggregate | Yes | 100 | No | Yes | No | dB × 100 |
| 0x0617 | detector_temp_c | Yes | 100 | No | Yes | No | °C × 100 |
| 0x062A | calibration_active | Yes | 1 | No | Yes | No | 0/1 |
| 0x062B | calibration_progress | Yes | 100 | No | Yes | No | % × 100 |
| 0x062C | last_calibration_time | Yes | 1 | No | Yes | No | Epoch timestamp |
| 0x062D | transfer_active | Yes | 1 | No | Yes | No | 0/1 |
| 0x062E | transfer_progress | Yes | 100 | No | Yes | No | % × 100 |
| 0x062F | integration_time_ms | Yes | 100 | Partial | Yes | No | ms × 100; set via capture command |
| 0x0620 | band_snr_blue | Yes | 100 | No | Yes | No | dB × 100 |
| 0x0621 | band_snr_green | Yes | 100 | No | Yes | No | dB × 100 |
| 0x0622 | band_snr_red | Yes | 100 | No | Yes | No | dB × 100 |
| 0x0623 | band_snr_nir | Yes | 100 | No | Yes | No | dB × 100 |
| 0x0624 | active_bands | Yes | 1 | No | Yes | No | Count |
| 0x0625 | band_enable_mask | Yes | 1 | Yes | Yes | No | 0x0F = all; writable via set_band_config |
| 0x0626 | att_quality_factor | Yes | 100 | No | Yes | No | Factor 0–1.0 × 100 |
| 0x0627 | att_error_deg | Yes | 1000 | No | Yes | No | degrees × 1000 |
| 0x0628 | gsd_m | Yes | 1 | No | Yes | No | meters |
| 0x0629 | scene_entropy | Yes | 100 | No | Yes | No | 0–1.0 × 100 |

**Notes on S20 Integration**:
- Most parameters are **readable only** (S20.3 / House-Keeping, Diagnostic & Test service).
- **Writable parameters** (set via S20.4 or payload-specific commands):
  - `payload_mode`: via S19.65 (Function Management, activate payload mode)
  - `band_enable_mask`: via payload-specific `set_band_config(mask)` (currently internal API, not S20)
  - `cal_lamp_status`: via S2 Device Access on/off (device 0x0602)
  - `compression_ratio`: **NOT writable** (scene-dependent; can override via `set_compression(ratio)` internal command)
  - `integration_time_ms`: via `set_integration_time([...])` internal command (should be S20.4)
  - `cooler_setpoint`: via `set_cooler_setpoint(...)` internal command (should be S20.4)
  - `detector_gain`: via `set_detector_gain(...)` internal command (should be S20.4)

**Gaps**:
- No S20.4 (Set Parameter Value) handlers mapped to payload commands; all currently use internal API.
- Band SNR parameters are **read-only** (computed, not set by ground).
- Focus position, shutter position, filter position not in HK or S20 (need to add).

---

## 9. Imaging Workflow Coverage Table

End-to-end LEOP and nominal imaging campaigns: which simulator subsystems, MCS, and planning tool support each phase.

| Workflow Phase | Operation | Simulator Modeled? | MCS Support? | Planning Tool? | Notes |
|---|---|---|---|---|---|
| **LEOP – Phase 1: Thermal Control** | Power on cooler | Yes | Mode command (S19.65) | — | `set_mode(1)` → cooler_active=True |
| | Monitor FPA temperature | Yes | HK gauge (0x0601) | — | 8 s cadence, ±0.02°C noise |
| | Verify FPA ready | Partial | Status indicator (0x060F) | — | Lacks hysteresis; may flap |
| | Measure thermal time constant | Yes | HK trend (0x0601 over 200 s) | — | ~100 s tau_cool is realistic |
| **LEOP – Phase 2: Shutter & Filter Test** | Cycle shutter | No | — | — | **DEFECT 4**: no shutter model |
| | Select filter position | No | — | — | **DEFECT 4**: no filter model |
| | Verify limit switches | No | — | — | Missing |
| **LEOP – Phase 3: Dark/Flat Calibration** | Acquire dark frames | Partial | Calibration command (`start_calibration`) | — | State machine exists, but no frame buffers |
| | Acquire flat-field frames | Partial | Calibration progress (0x062B) | — | Lacks actual frame accumulation |
| | Compute gain/bias coefficients | No | — | — | **DEFECT 1**: no coefficient generation |
| | Retrieve calibration data | No | S5 downlink (not configured) | — | Missing |
| | Verify per-band coefficients | No | — | — | No validation HK params |
| **LEOP – Phase 4: First-Light Imaging** | Enable imaging mode | Yes | Mode command (S19.65) | — | `set_mode(2)` → imaging active |
| | Capture test image | Yes | Internal `capture` command | — | Works; stores in catalog |
| | Monitor image quality | Partial | HK (0x060D last_scene_quality) | — | Quality is hardcoded, not SNR-derived |
| | Verify geolocation | No | — | — | Image lat/lon from command input, not orbit state |
| **LEOP – Phase 5: Radiometric Validation** | Retrieve dark-subtracted image | No | — | — | **DEFECT 1**: no radiometric products |
| | Perform vicarious calibration | No | — | — | Missing |
| | Validate TOA/TOC radiance | No | — | — | Missing |
| **Nominal – Tasking** | Load imaging targets | Yes | — | Yes (YAML config) | Targets loaded from planning/imaging_targets.yaml |
| | Generate ground-track | Partial | — | Yes | Planner generates track; coupling to simulator weak |
| | Compute opportunities | Yes | — | Yes | Planner computes windows; outputs JSON |
| | Schedule image captures | No | — | Partial | Planner generates schedule; no uplink to payload |
| **Nominal – Capture** | Issue capture command | Yes | — | — | Internal `capture(lat, lon)` command |
| | Acquire image | Yes | Image count HK (0x0605) | — | Produces image in catalog |
| | Monitor storage usage | Yes | HK gauge (0x0604, 0x060B) | — | 8 s cadence |
| | Store with compression | Partial | Compression ratio HK (0x0614) | — | **DEFECT 3**: ratio not applied to size |
| **Nominal – Calibration Maintenance** | Schedule periodic dark frames | No | — | — | No automatic maintenance |
| | Perform in-orbit radiometric update | No | — | — | Missing |
| | Detect & replace bad pixels | No | — | — | **DEFECT (Cat 3)**: no bad-pixel map |
| **Nominal – Downlink** | Query image catalog | Yes | Image count HK (0x0605) | — | Supported via `get_image_catalog` |
| | Initiate image download | No | — | — | **DEFECT 5**: no downlink command |
| | Monitor downlink progress | Partial | Transfer progress HK (0x062E) | — | Progress phantom (not driven) |
| | Verify image received | No | — | — | Missing ground-to-payload ACK |
| | Delete from onboard storage | Yes | — | — | `delete_image(scene_id)` command works |
| **Nominal – Memory Management** | Monitor segment health | Yes | Bad segments HK (0x0612) | — | Supports marking segments bad |
| | Initiate memory scrub | No | — | — | Missing |
| | Redistribute images post-failure | No | — | — | No automatic recovery |
| **Contingency – Cooler Failure** | Detect cooler failure | Yes | Event 0x0606 emitted | — | `inject_failure("cooler_failure")` |
| | Transition to safe mode | No | — | — | No automatic mode change |
| | Recover from cooler restart | No | — | — | No recovery command |
| **Contingency – Memory Fault** | Detect bad segment | Yes | Event 0x060A, HK 0x0612 | — | `inject_failure("memory_segment_fail", segment=0)` |
| | Exclude segment from use | Yes | — | — | `mark_bad_segment(0)` command works |
| | Redistribute stored images | No | — | — | Manual, not automatic |
| **Contingency – Image Corruption** | Inject corrupt images | Yes | Event 0x0607, status field | — | `inject_failure("image_corrupt", count=3)` |
| | Detect via checksum | Partial | Checksum error HK (0x0609) | — | Counter increments; no frame-level CRC |
| | Quarantine corrupt image | Yes | Catalog status=2 | — | Marked but not deleted automatically |

**Summary**:
- **Well covered**: Mode control, FPA thermal monitoring, image capture, storage accounting, failure injection.
- **Partially covered**: Calibration state machine (progress tracked, but no frame buffers), downlink progress (HK params exist, not driven by commands), image quality (hardcoded, not derived from SNR).
- **Poorly covered**: Radiometric calibration, downlink workflows, focus/shutter/filter mechanisms, bad-pixel management, FDIR automation.

---

## 10. Recommended Implementation Priorities

### Immediate (LEOP-blocking)
1. **Defect 1 — Radiometric Calibration**: Add dark/flat frame buffers and coefficient generation. Unblocks calibration validation.
2. **Defect 2 — FPA Readiness Hysteresis**: Add settling time and cooler health check. Prevents spurious "ready" events.

### High (nominal-ops stability)
3. **Defect 3 — Image Compression**: Apply compression to stored size. Fixes storage forecasting.
4. **Defect 5 — Downlink Integration**: Add transfer command and progress drive. Completes end-to-end workflow.

### Medium (feature completeness)
5. **Defect 4 — Shutter/Filter Models**: Add functional commands. Enables LEOP first-light checklist.
6. **Category 3.1 — Detector Noise**: Add read noise, shot noise, quantization. Improves commissioning fidelity.
7. **Category 3.2 — Bad-Pixel Map**: Track pixel-level defects. Realistic aging/degradation.

### Lower priority (advanced fidelity)
8. **Category 3.4 — Focus Mechanism**: Model focus drift and MTF. Useful for later LEOP phases.
9. **Category 3.5 — TDI/Frame Mode**: Distinguish imaging modes. Future pushbroom refinement.

---

## Conclusion

The EOSAT-1 EO payload simulator has a solid foundation (core FPA thermal model, image capture, memory management, event generation, multispectral bands, calibration state machine). However, **five critical gaps** prevent realistic LEOP and nominal-ops workflows:

1. **No radiometric calibration products** — dark/flat frames and gain coefficients not generated.
2. **FPA readiness lacks safeguards** — no hysteresis or cooler health check, risking poor image quality.
3. **Image compression not applied** — storage forecasting is inaccurate.
4. **Shutter/filter mechanisms stubbed** — first-light procedures incomplete.
5. **Downlink workflows missing** — end-to-end imaging campaign cannot be validated.

Additionally, **poor S20 integration** and **scattered HK parameter coverage** limit operator confidence in ground commanding. Addressing the top-5 defects and adding S20 handlers will significantly improve operability and mission simulation fidelity.

**Review complete. All findings and recommendations documented for engineering intake.**

---

Sources referenced:
- [ECSS-E-ST-70-41C](https://ecss.nl/standard/ecss-e-st-70-41c-space-engineering-telemetry-and-telecommand-packet-utilization-15-april-2016/)
- [PUSopen Service 20](https://pusopen.com/)
- [CCSDS 121/122/123 Image Compression](https://ccsds.org/Pubs/120x1g3.pdf)
- [Satellite LEOP Best Practices](https://www.esa.int/Enabling_Support/Operations/Nonstop_LEOP_full_stop)
- [EO Concept of Operations (OSSAT)](https://www.opensourcesatellite.org/downloads/KS-DOC-01221-01_OSSAT_Optical_Earth_Observation_CONOPs.pdf)
- [FDIR Techniques](https://www.mathworks.com/discovery/fdir.html)

# EOSAT-1 Payload Operations Requirements Document

**Document ID:** EOSAT1-REQ-PLD-001
**Issue:** 1.0
**Date:** 2026-03-12
**Position:** Payload Operations Engineer
**Classification:** UNCLASSIFIED -- For Simulation Use Only

---

## 1. Scope and Mission Context

This document defines the operational requirements for the EOSAT-1 multispectral imaging payload from the perspective of the Payload Operations Engineer. EOSAT-1 is an ocean current monitoring cubesat carrying a single nadir-pointing multispectral pushbroom imager. The primary science objective is ocean color imaging for inferring surface current patterns through chlorophyll-a distribution, suspended sediment transport, and sea surface temperature gradients.

The Payload Operations Engineer is responsible for all aspects of the multispectral camera lifecycle: commissioning, imaging campaign planning and execution, data management (onboard storage, downlink scheduling, image quality assurance), and contingency response for payload anomalies.

### 1.1 Reference Documents

| ID | Title | File |
|---|---|---|
| EOSAT1-UM-PLD-007 | Payload -- Multispectral Imager Manual | `configs/eosat1/manual/06_payload.md` |
| payload.yaml | Subsystem configuration | `configs/eosat1/subsystems/payload.yaml` |
| parameters.yaml | Telemetry parameter definitions | `configs/eosat1/telemetry/parameters.yaml` |
| hk_structures.yaml | Housekeeping packet structures | `configs/eosat1/telemetry/hk_structures.yaml` |
| payload_basic.py | Simulator payload model | `packages/smo-simulator/src/smo_simulator/models/payload_basic.py` |
| procedure_index.yaml | Master procedure index | `configs/eosat1/procedures/procedure_index.yaml` |

---

## 2. Equipment Under Payload Responsibility

### 2.1 Multispectral Imager Assembly

| Component | Specification | Notes |
|---|---|---|
| Instrument type | Pushbroom multispectral imager | CCD linear array detector |
| Spectral range | 450--900 nm (VNIR) | 4 discrete spectral bands |
| Ground sampling distance | ~10 m at 500 km altitude | Drives attitude pointing requirement |
| Swath width | ~60 km nominal (30 km in simulator model) | `swath_width_km` telemetry at 0x0619 |
| Detector operating temperature | -15 deg C nominal (-5 deg C in simulator) | `fpa_temp` at 0x0601 |
| Raw data rate | ~40 Mbps (80 Mbps in simulator model) | Pre-compression |
| Compression ratio | 4:1 lossy, configurable (2:1 in simulator) | `compression_ratio` at 0x0614 |
| Effective compressed data rate | ~10 Mbps | Post-compression |
| Line rate | 500 Hz | `line_rate` at 0x0607 |
| Integration time | 2.0 ms (configurable) | `integration_time` at 0x0618 |

### 2.2 Spectral Band Configuration for Ocean Color

The EOSAT-1 imager carries four spectral bands specifically selected for ocean color science and ocean current inference:

| Band | Name | Centre (nm) | Bandwidth (nm) | Ocean Color Application |
|---|---|---|---|---|
| 1 | Blue | 490 | 65 | **Primary ocean color band.** Chlorophyll-a absorption maximum. Water-leaving radiance detection. Coastal turbidity mapping. Ocean current boundary detection through chlorophyll gradient analysis. |
| 2 | Green | 560 | 35 | **Chlorophyll reflectance peak.** Phytoplankton bloom mapping. Suspended sediment concentration. Complementary to Blue for chlorophyll-a ratio algorithms (e.g., OC4 band ratio). |
| 3 | Red | 665 | 30 | **Chlorophyll-a fluorescence baseline.** Vegetation classification for coastal zones. Sediment plume mapping. Sun glint estimation for atmospheric correction. |
| 4 | NIR | 842 | 115 | **Atmospheric correction reference.** Water absorbs strongly in NIR, so signal is dominated by atmosphere. Used to remove atmospheric contribution from ocean color bands. Land/water mask generation. Vegetation index (NDVI) for coastal ecosystem monitoring. |

**REQ-PLD-BAND-001:** The simulator shall model all four spectral bands as a single combined data stream. The SNR telemetry parameter (0x0616) shall represent the composite signal quality across all bands.

**REQ-PLD-BAND-002:** Ocean color imaging shall be restricted to sunlit, dayside passes. Eclipse imaging produces dark frames useful only for calibration. The Payload Operations Engineer shall coordinate with the planner to schedule imaging windows exclusively during daylight overpasses of target ocean regions.

**REQ-PLD-BAND-003:** The Blue band (490 nm) is the most critical band for ocean current inference. Image quality requirements (SNR, geometric accuracy) are driven by the Blue band performance, which has the lowest water-leaving radiance signal.

### 2.3 Focal Plane Array (FPA) Cooler

| Parameter | Value | Source |
|---|---|---|
| Cooler target temperature | -5.0 deg C (simulator) / -15 deg C (manual) / -30 deg C (procedures) | `payload.yaml` / `06_payload.md` / procedure files |
| Ambient temperature (cooler off) | +5.0 deg C | `payload.yaml` |
| Cooling time constant | 100 s | `payload.yaml` |
| Warming time constant | 120 s | `payload.yaml` |
| Cooler power consumption | 15 W (10--15 W range) | `payload.yaml` / `06_payload.md` |
| Max continuous STANDBY duration | 4 hours | `06_payload.md` (cooler lifetime) |

**REQ-PLD-COOL-001:** The cooler activation sequence (COM-010) shall be executed before any imaging activity. FPA readiness is indicated by `payload.fpa_ready` (0x0610) = 1, which triggers when `fpa_temp <= (target + 5 deg C)`.

**REQ-PLD-COOL-002:** The Payload Operations Engineer shall monitor the cooldown curve and record the cooldown profile for performance trending. Cooldown rate degradation over the mission lifetime is a leading indicator of cooler end-of-life.

**REQ-PLD-COOL-003:** The cooler shall not be left active in STANDBY for more than 4 continuous hours. The Payload Operations Engineer shall schedule OFF periods between imaging campaigns to manage cooler lifetime.

### 2.4 Onboard Mass Memory

| Parameter | Value | Telemetry |
|---|---|---|
| Total storage | 20,000 MB (20 GB) | `payload.mem_total_mb` (0x060A) |
| Memory segments | 8 segments, 2,500 MB each | Simulator model |
| Image size (per frame) | 800 MB (raw, pre-compression) | `payload.yaml` |
| Maximum stored images | ~25 images (at 800 MB/image) | Derived |
| Storage utilisation | Percentage | `payload.store_used` (0x0604) |
| Bad segment count | Count | `payload.mem_segments_bad` (0x0612) |
| Checksum algorithm | CRC-32 | `06_payload.md` |

**REQ-PLD-MEM-001:** The Payload Operations Engineer shall monitor storage utilisation and ensure imaging is not initiated when `payload.store_used` >= 90%. At >= 95%, imaging shall be immediately terminated.

**REQ-PLD-MEM-002:** Bad memory segments shall be identified and marked via the `mark_bad_segment` command. The Payload Operations Engineer shall maintain a segment health log and account for reduced effective capacity when planning imaging campaigns.

### 2.5 Calibration Lamp

| Parameter | Value | Telemetry |
|---|---|---|
| Calibration lamp status | ON/OFF | `payload.cal_lamp_on` (0x0615) |

**REQ-PLD-CAL-001:** The onboard calibration lamp shall be used periodically for flat-field and radiometric stability trending. The Payload Operations Engineer shall schedule calibration lamp acquisitions at least once per week during nominal operations.

---

## 3. Telemetry Requirements

### 3.1 Payload Telemetry Parameters

The following parameters fall under Payload Operations Engineer monitoring responsibility:

| Param ID | Name | Unit | Description | Criticality |
|---|---|---|---|---|
| 0x0600 | `payload.mode` | enum | Operating mode (0=OFF, 1=STANDBY, 2=IMAGING) | **Critical** |
| 0x0601 | `payload.fpa_temp` | deg C | FPA temperature | **Critical** |
| 0x0602 | `payload.cooler_pwr` | W | FPA cooler power consumption | High |
| 0x0603 | `payload.imager_temp` | deg C | Imager electronics temperature | High |
| 0x0604 | `payload.store_used` | % | Storage utilisation | **Critical** |
| 0x0605 | `payload.image_count` | count | Stored image count | High |
| 0x0609 | `payload.checksum_errors` | count | Cumulative CRC error count | **Critical** |
| 0x060A | `payload.mem_total_mb` | MB | Total memory (accounting for bad segments) | Medium |
| 0x060B | `payload.mem_used_mb` | MB | Used memory | High |
| 0x060C | `payload.last_scene_id` | ID | Last captured scene identifier | High |
| 0x060D | `payload.last_scene_quality` | % | Quality metric of last image | High |
| 0x0610 | `payload.fpa_ready` | boolean | FPA at operational temperature | **Critical** |
| 0x0612 | `payload.mem_segments_bad` | count | Bad memory segment count | High |
| 0x0613 | `payload.duty_cycle_pct` | % | Imaging duty cycle | Medium |
| 0x0614 | `payload.compression_ratio` | ratio | Image compression ratio | Medium |
| 0x0615 | `payload.cal_lamp_on` | boolean | Calibration lamp status | Medium |
| 0x0616 | `payload.snr` | dB | Signal-to-noise ratio | **Critical** |
| 0x0617 | `payload.detector_temp` | deg C | CCD detector temperature | High |
| 0x0618 | `payload.integration_time` | ms | Detector integration time | Medium |
| 0x0619 | `payload.swath_width_km` | km | Ground swath width | Low |

### 3.2 Cross-Subsystem Telemetry Dependencies

The Payload Operations Engineer shall also monitor the following parameters from other subsystems that directly affect imaging operations:

| Param ID | Name | Subsystem | Relevance |
|---|---|---|---|
| 0x0217 | `aocs.att_error` | AOCS | Pointing accuracy impacts image quality |
| 0x020F | `aocs.mode` | AOCS | Must be NADIR_POINT or FINE_POINT for imaging |
| 0x0204-0x0206 | `aocs.rate_roll/pitch/yaw` | AOCS | Body rates affect image smear |
| 0x0101 | `eps.bat_soc` | EPS | Minimum 40% for imaging activation |
| 0x0105 | `eps.bus_voltage` | EPS | Bus stability under imaging load |
| 0x0408 | `tcs.temp_fpa` | TCS | Cross-check with payload FPA sensor |
| 0x040C | `tcs.cooler_fpa` | TCS | Cooler active status |
| 0x0501 | `ttc.link_status` | TTC | Required for data downlink |
| 0x0503 | `ttc.link_margin` | TTC | Must be > 3 dB for downlink |

### 3.3 Housekeeping Structure

**Payload HK Packet -- SID 5:**

| Param ID | Pack Format | Scale | Description |
|---|---|---|---|
| 0x0600 | B | 1 | Mode |
| 0x0601 | h (signed) | 100 | FPA temperature |
| 0x0602 | H | 10 | Cooler power |
| 0x0603 | h (signed) | 100 | Imager temperature |
| 0x0604 | H | 100 | Storage used (%) |
| 0x0605 | H | 1 | Image count |
| 0x0609 | H | 1 | Checksum errors |
| 0x060A | I | 1 | Memory total (MB) |
| 0x060B | I | 1 | Memory used (MB) |
| 0x060C | H | 1 | Last scene ID |
| 0x060D | B | 1 | Last scene quality |
| 0x0610 | B | 1 | FPA ready flag |
| 0x0612 | B | 1 | Bad segment count |
| 0x0614 | H | 100 | Compression ratio |
| 0x0615 | B | 1 | Cal lamp status |
| 0x0616 | H | 100 | SNR |
| 0x0617 | h (signed) | 100 | Detector temperature |

**REQ-PLD-TM-001:** The Payload HK packet (SID 5) shall be generated at an interval of 8.0 seconds during all imaging operations.

**REQ-PLD-TM-002:** During FPA cooler activation (cooldown monitoring), the Payload Operations Engineer shall request one-shot HK reports (`HK_REQUEST sid=5` or `sid=6`) at 2-minute intervals to build the cooldown curve.

**REQ-PLD-TM-003:** The Payload Operations Engineer shall monitor `payload.checksum_errors` (0x0609) in real time. Any increment triggers the Corrupted Image Handling procedure (CTG-015).

---

## 4. Command Requirements (PUS Services)

### 4.1 PUS Services Required by Payload Operations

| Service | Subtype | Command | Description |
|---|---|---|---|
| S3 | 25/27 | `HK_REQUEST` | Request one-shot housekeeping report (SID 5 for Payload, SID 3 for TCS) |
| S5 | 5/6/7/8 | Event reports | Enable/disable event reporting for payload events |
| S8 | 1 | `PAYLOAD_SET_MODE` | Set payload mode (OFF/STANDBY/IMAGING) -- func_id 20 |
| S8 | 1 | `PAYLOAD_CAPTURE` | Trigger image capture -- func_id 22 |
| S8 | 1 | `PAYLOAD_DELETE_IMAGE` | Delete image by scene_id -- func_id 24 |
| S8 | 1 | `PAYLOAD_MARK_BAD_SEGMENT` | Mark memory segment unusable -- func_id 25 |
| S8 | 1 | `FPA_COOLER` | Control FPA cooler ON/OFF -- func_id 33 |
| S11 | 4 | Time-tagged commands | Pre-schedule imaging mode transitions for autonomous execution |
| S15 | 9/13 | Storage management | Onboard storage download control |
| S17 | 1 | Connection test | Verify TC link before commanding payload |
| S20 | 1/3 | `GET_PARAM`/`SET_PARAM` | Read/write individual payload parameters |

### 4.2 Allowed Command Function IDs

Per `positions.yaml`, the Payload Operations position is authorized for function IDs: **20, 21, 22, 23, 24, 25, 26**. These correspond to:

| Func ID | Command | Description |
|---|---|---|
| 20 | `PAYLOAD_SET_MODE` | Set imager mode (OFF=0, STANDBY=1, IMAGING=2) |
| 21 | `PAYLOAD_SET_SCENE` | Set current scene ID for targeting |
| 22 | `PAYLOAD_CAPTURE` | Trigger image capture at current position |
| 23 | `PAYLOAD_DOWNLOAD_IMAGE` | Download image data by scene_id |
| 24 | `PAYLOAD_DELETE_IMAGE` | Delete image by scene_id or count |
| 25 | `PAYLOAD_MARK_BAD_SEGMENT` | Mark a memory segment as unusable |
| 26 | `PAYLOAD_GET_CATALOG` | Retrieve the onboard image catalog |

**REQ-PLD-CMD-001:** All payload commanding shall be restricted to the authorized function IDs (20--26) and allowed PUS services (1, 3, 5, 8, 11, 15, 17, 20). The MCS shall enforce this filtering at the position level.

**REQ-PLD-CMD-002:** The `PAYLOAD_SET_MODE(mode=2)` (IMAGING) command shall be rejected by the operator if any of the following preconditions are not met:
- `payload.fpa_ready` (0x0610) = 1
- `aocs.att_error` (0x0217) < 0.5 deg
- `eps.bat_soc` (0x0101) > 40%
- `payload.store_used` (0x0604) < 90%

**REQ-PLD-CMD-003:** The Payload Operations Engineer shall use time-tagged commands (PUS S11) to pre-schedule imaging mode transitions for autonomous execution during non-contact periods over ocean target areas.

---

## 5. Operational Procedures

### 5.1 Commissioning Procedures

| ID | Name | File | Payload Ops Role |
|---|---|---|---|
| COM-009 | Payload Power On | `commissioning/payload_power_on.md` | Power on imager, verify telemetry, confirm boot, check data interface |
| COM-010 | FPA Cooler Activation | `commissioning/fpa_cooler_activation.md` | Activate cooler, monitor cooldown curve to -30 deg C, verify stability |
| COM-011 | Payload Calibration | `commissioning/payload_calibration.md` | Execute dark frame and radiometric calibration sequence |
| COM-012 | First Light | `commissioning/first_light.md` | Capture first image, assess quality, distribute First Light Report |

#### 5.1.1 FPA Cooler Commissioning Sequence (Detailed)

The FPA cooler commissioning (COM-010) is a critical multi-step process:

1. **Pre-activation baseline:** Record FPA ambient temperature (expected +5 to +25 deg C), bus voltage, and power consumption.
2. **Cooler enable:** Command `SET_PARAM(0x0620, 1)`. Verify cooler running, power increase of 8--12 W.
3. **Initial cooldown (0--10 min):** Sample FPA temperature every 2 minutes. Expected rate 2--3 deg C/min initially. Verify temperature decreasing monotonically.
4. **Extended cooldown (10--30 min):** Sample every 5 minutes as rate decreases. Target milestones: < 0 deg C at T+10 min, < -20 deg C at T+20 min, < -28 deg C at T+30 min.
5. **Setpoint stabilisation:** Verify temperature at -30 +/- 2 deg C. Monitor for 10 minutes. Peak-to-peak variation must be < 1 deg C.
6. **Eclipse test (if applicable):** Verify cooler maintains setpoint through eclipse with adequate bus voltage.
7. **Report:** Document complete cooldown curve, cooler power, stability data.

**REQ-PLD-COOL-COM-001:** The FPA cooler commissioning shall not proceed until COM-009 (Payload Power-On) post-conditions are satisfied, including payload self-test PASS and firmware version confirmation.

**REQ-PLD-COOL-COM-002:** If cooldown rate is < 1 deg C/min from start, the Payload Operations Engineer shall log an anomaly but may allow additional time. If FPA cannot reach -28 deg C within 60 minutes, imaging may proceed with reduced performance at elevated FPA temperature.

**REQ-PLD-COOL-COM-003:** If excessive vibration is detected from the cooler (AOCS attitude jitter with cooler ON vs OFF), the Payload Operations Engineer shall coordinate with the AOCS Engineer to assess impact on image quality and potentially schedule cooler operation outside imaging windows.

### 5.2 Nominal Operations Procedures

| ID | Name | File | Payload Ops Role |
|---|---|---|---|
| NOM-002 | Imaging Session | `nominal/imaging_session.md` | Configure and execute imaging campaign |
| NOM-003 | Data Downlink | `nominal/data_downlink.md` | Select and prioritize data for download |

#### 5.2.1 Imaging Campaign Execution

A standard ocean color imaging session (NOM-002) follows this sequence:

1. **Pre-imaging checks:** Verify AOCS in NADIR_POINT, `att_error` < 0.1 deg, body rates < 0.01 deg/s.
2. **FPA thermal confirmation:** Verify `tcs.temp_fpa` < -25 deg C, cooler active.
3. **Storage check:** Verify `payload.store_used` < 90%. Record baseline image count.
4. **Mode transition:** Command IMAGING mode. Verify transition within 15 s.
5. **Acquisition monitoring:** Poll SID 5 every 30 s. Verify image_count incrementing, storage < 95%, `att_error` < 0.1 deg, FPA < -20 deg C throughout.
6. **Session termination:** Command STANDBY. Record final image count.
7. **Post-imaging assessment:** Log images acquired, storage delta, session duration.

**REQ-PLD-OPS-001:** Maximum continuous imaging duration shall not exceed 10 minutes per session (thermal and power limited per manual section 3.2).

**REQ-PLD-OPS-002:** If `aocs.att_error` exceeds 0.5 deg during an imaging session, the Payload Operations Engineer shall immediately terminate imaging. Images captured during high pointing error are flagged for ground quality review.

**REQ-PLD-OPS-003:** If FPA temperature rises above -15 deg C during imaging, the session shall be aborted to protect the detector.

#### 5.2.2 Data Management and Downlink Scheduling

Data downlink (NOM-003) coordinates between Payload Ops and TTC:

1. **Link quality confirmation:** `ttc.link_margin` > 3 dB, `ttc.rssi` > -100 dBm.
2. **Storage baseline:** Record `payload.store_used` and `payload.image_count`.
3. **Playback mode:** Command PLAYBACK (mode=3). Monitor storage percentage decreasing.
4. **Transfer monitoring:** Poll every 60 s. Maintain link margin > 2 dB.
5. **Ground reception verification:** Confirm frame sync and archive integrity.
6. **Session termination:** Return to STANDBY. Log volume transferred.

**REQ-PLD-DL-001:** Payload data downlink at 128 kbps requires approximately 12 minutes per 100 MB of compressed data. The Payload Operations Engineer shall account for available contact time when scheduling downlink sessions.

**REQ-PLD-DL-002:** The Payload Operations Engineer shall prioritize downlink of ocean color science data over calibration data and housekeeping data. Within science data, priority shall be given to images of ocean current target regions with low cloud cover probability.

**REQ-PLD-DL-003:** Data downlink shall be scheduled to ensure onboard storage does not exceed 80% utilisation for more than one orbit period, to preserve margin for contingency imaging opportunities.

### 5.3 Contingency Procedures

| ID | Name | File | Payload Ops Role |
|---|---|---|---|
| CTG-004 | Thermal Exceedance | `contingency/thermal_exceedance.md` | Safe payload if thermally affected |
| CTG-006 | Payload Anomaly | `contingency/payload_anomaly.md` | Diagnose FPA thermal anomaly, safe and recover payload |
| CTG-015 | Corrupted Image Recovery | `contingency/corrupted_image.md` | Assess corruption root cause, retake if possible |
| EMG-006 | Thermal Runaway | `emergency/thermal_runaway.md` | Emergency payload power off |

#### 5.3.1 Corrupted Image Handling

The Corrupted Image procedure (CTG-015) follows a diagnostic tree:

1. **Detect:** `payload.checksum_errors` (0x0609) incremented. Record affected scene_id and quality metric.
2. **FPA assessment:** If `payload.fpa_temp` > -20 deg C, the corruption is thermal. Abort imaging, wait for cooldown.
3. **Memory assessment:** If FPA is cold but `payload.mem_segments_bad` > 0, memory failure is the cause. Mark the bad segment.
4. **Transient assessment:** If FPA is cold and no bad segments, corruption was a single-event upset. Proceed to re-capture.
5. **Recovery:** Delete corrupted image, re-capture if imaging window is available. Verify no new checksum errors on re-capture.

**REQ-PLD-CTG-001:** Any checksum error increment shall trigger an immediate investigation. If multiple consecutive images are corrupted, the Payload Operations Engineer shall command payload to STANDBY and initiate a full diagnostic before resuming imaging.

### 5.4 FDIR Rules Affecting Payload

| Parameter | Condition | Action |
|---|---|---|
| `eps.bat_soc` | < 20% | `payload_poweroff` (Level 1) |

**REQ-PLD-FDIR-001:** The Payload Operations Engineer shall be aware that autonomous FDIR will power off the payload if battery SoC drops below 20%. After an FDIR-triggered payload power-off, the full power-on and cooler activation sequence (COM-009, COM-010) must be repeated before imaging can resume.

**REQ-PLD-FDIR-002:** The current FDIR configuration lacks a dedicated FPA over-temperature rule. A rule for `payload.fpa_temp > 12 deg C` triggering automatic payload power-off should be implemented. This is a configuration gap that the Payload Operations Engineer shall raise with the FDIR/Systems position.

---

## 6. Image Quality Coupling to Attitude Error

### 6.1 Pointing Accuracy Requirements

| Condition | Attitude Error Threshold | Impact on Image Quality |
|---|---|---|
| Fine imaging (ocean color) | < 0.1 deg | Nominal SNR, no smear, full geometric accuracy |
| Acceptable imaging | < 0.5 deg | Minor geometric distortion, correctable in ground processing |
| Degraded imaging | 0.5--1.0 deg | Significant pixel displacement (~87 m at 10 m GSD), cross-band misregistration |
| Unusable | > 1.0 deg | Image quality below threshold for ocean color retrieval |

### 6.2 Impact Analysis

**REQ-PLD-IQ-001:** Ocean color retrieval algorithms require sub-pixel co-registration between spectral bands. The attitude error during a pushbroom scan directly translates to cross-track pixel displacement. At 10 m GSD, a 0.1 deg pointing error produces approximately 8.7 m of cross-track displacement (less than 1 pixel). At 0.5 deg, displacement is approximately 43.5 m (~4.4 pixels), requiring ground correction.

**REQ-PLD-IQ-002:** Body rates during imaging shall be < 0.01 deg/s in all axes. Higher rates cause along-track smearing, which degrades the Blue band (490 nm) most severely due to its lower signal-to-noise ratio in ocean scenes.

**REQ-PLD-IQ-003:** The SNR parameter (0x0616) shall be monitored during imaging. Nominal SNR is 45 dB. An SNR drop below 35 dB indicates degraded imaging conditions (FPA temperature drift, FPA degradation, or CCD line dropout). The Payload Operations Engineer shall correlate SNR with FPA temperature and `att_error` for quality trending.

**REQ-PLD-IQ-004:** The simulator models SNR as a function of FPA temperature and degradation state:
- `fpa_factor = max(0.5, 1.0 - (fpa_temp - target) * 0.02)` -- warmer FPA reduces SNR
- `degrade_factor = 0.85` if FPA degraded
- CCD line dropout further reduces image quality (status = PARTIAL, quality 60--85%)

**REQ-PLD-IQ-005:** Images captured with `aocs.att_error` >= 0.5 deg shall be flagged with a quality warning in the image catalog. The ground processing pipeline shall apply enhanced geometric correction to these images before ocean color retrieval.

---

## 7. Imaging Target Planning

### 7.1 Ocean Current Monitoring Targets

**REQ-PLD-TGT-001:** The Payload Operations Engineer shall maintain a target database of ocean current monitoring regions. Primary targets for EOSAT-1 ocean color imaging include:

| Region | Latitude Range | Longitude Range | Science Priority | Rationale |
|---|---|---|---|---|
| Gulf Stream frontal zone | 30--45 N | 75--55 W | **Critical** | Strong chlorophyll gradients at current boundaries |
| Kuroshio Current | 25--40 N | 125--145 E | **Critical** | Western boundary current, high productivity |
| Agulhas Current retroflection | 35--42 S | 15--30 E | High | Warm/cold water boundary, mesoscale eddies |
| Equatorial Pacific upwelling | 5 S--5 N | 160 E--100 W | High | Upwelling-driven chlorophyll enhancement |
| California Current system | 25--45 N | 130--115 W | High | Coastal upwelling, phytoplankton blooms |
| North Sea / Baltic | 50--60 N | 5 W--25 E | Medium | Sediment transport, algal blooms |
| Mediterranean Sea | 30--45 N | 5 W--35 E | Medium | Mesoscale circulation features |

### 7.2 Imaging Window Computation

**REQ-PLD-TGT-002:** The Payload Operations Engineer shall use the orbit planner (`OrbitPlanner.predict_ground_track()`) and contact planner (`ContactPlanner.compute_windows()`) to identify imaging opportunities over target regions. The required planning outputs are:

1. **Ground track prediction** over the next 24--72 hours.
2. **Target overpasses:** Times when the spacecraft ground track intersects target regions within the 60 km swath width.
3. **Daylight filter:** Only sunlit overpasses are valid for ocean color imaging (eclipse flag = false).
4. **Cloud cover assessment:** Coordinate with external weather data to reject cloud-covered overpasses.
5. **Contact window correlation:** Ensure a data downlink window is available within 6 hours of imaging for priority data.

**REQ-PLD-TGT-003:** The activity scheduler (`ActivityScheduler`) shall be used to schedule imaging sessions with the following attributes:

| Field | Value |
|---|---|
| `name` | "ocean_color_imaging" |
| `duration_s` | 300--600 (5--10 minutes per session maximum) |
| `power_w` | 25 (cooler + electronics + detector) |
| `data_volume_mb` | 800--4000 (1--5 images per session) |
| `priority` | "high" for current boundary targets, "medium" for routine monitoring |
| `procedure_ref` | "NOM-002" |
| `conflicts_with` | ["momentum_dump"] |
| `pre_conditions` | ["fpa_ready", "nadir_point", "bat_soc_gt_40", "storage_lt_90"] |

**REQ-PLD-TGT-004:** The planner swath footprint visualization (defined in the planner static UI with `SWATH_HALF_ANGLE_DEG = 3.5`) shall be displayed on the MCS ground track map to allow the Payload Operations Engineer to visually confirm target coverage.

### 7.3 Imaging Campaign Cadence

**REQ-PLD-TGT-005:** The Payload Operations Engineer shall plan imaging campaigns with the following cadence:

| Campaign Type | Frequency | Duration | Description |
|---|---|---|---|
| Primary ocean current monitoring | Daily (1--3 per day) | 5--10 min each | Imaging over priority target regions |
| Calibration site overpass | Weekly | 5 min | Radiometric calibration over known ground site |
| Dark frame acquisition | Bi-weekly | 5 min | Eclipse dark frames for FPA characterisation |
| Coastal zone monitoring | 2--3 per week | 5 min | Coastal ecosystem and sediment transport |

---

## 8. Data Downlink Scheduling

### 8.1 Downlink Budget

| Parameter | Value |
|---|---|
| Downlink data rate (S-band) | 128 kbps |
| Effective throughput (with overhead) | ~100 kbps |
| Time per 100 MB compressed data | ~12 minutes |
| Typical image size (compressed 4:1) | 50--200 MB |
| Typical contact window duration | 8--12 minutes |
| Images per contact (best case) | 1--2 compressed images |

**REQ-PLD-DL-004:** Given the limited downlink bandwidth, the Payload Operations Engineer shall implement a data prioritization scheme:

| Priority | Data Type | Downlink Deadline |
|---|---|---|
| 1 (highest) | Ocean current boundary images (Gulf Stream, Kuroshio) | Within 6 hours (next available contact) |
| 2 | General ocean color science images | Within 24 hours |
| 3 | Calibration site images | Within 48 hours |
| 4 | Dark frames and calibration data | Within 1 week |
| 5 | Housekeeping and ancillary data | As bandwidth permits |

### 8.2 Storage Management Strategy

**REQ-PLD-DL-005:** The Payload Operations Engineer shall maintain a storage management plan that coordinates imaging acquisition rate with downlink capacity:

- **Daily imaging budget:** Maximum 3--5 images per day (~2,400--4,000 MB raw, ~600--1,000 MB compressed).
- **Daily downlink capacity:** Approximately 2--3 contact windows, each ~10 minutes = ~75 MB per contact = ~150--225 MB per day.
- **Buffer management:** Maintain at least 20% free storage (4,000 MB) at all times for contingency imaging opportunities.
- **Purge after downlink:** Delete images from onboard storage only after ground station confirms successful archive.

**REQ-PLD-DL-006:** If storage utilisation exceeds 80%, the Payload Operations Engineer shall defer non-priority imaging until downlink clears sufficient capacity.

---

## 9. MCS Display and Tool Requirements

### 9.1 Payload Tab

The current MCS payload tab (in `packages/smo-mcs/src/smo_mcs/static/index.html`) provides:

| Panel | Content | Status |
|---|---|---|
| Sensor Status | FPA Temperature, FPA Ready LED, Imager Temperature, Duty Cycle | Implemented |
| Memory | Total MB, Used MB, Memory usage gauge, Bad segments count | Implemented |
| Image Catalog | Image Count, Last Scene ID, Last Scene Quality, Checksum Errors | Implemented |
| FPA Temperature Chart | Time-series chart of FPA temperature | Implemented |
| Payload Events | Filtered event log for payload subsystem | Implemented |

### 9.2 Additional Display Requirements

**REQ-PLD-MCS-001:** The Payload tab shall display the following additional Phase 4 telemetry parameters that are collected but not currently shown in the UI:

| Parameter | Telemetry ID | Desired Display |
|---|---|---|
| SNR | 0x0616 | Numeric value with color-coded status (green >= 40 dB, yellow 30--40 dB, red < 30 dB) |
| Compression Ratio | 0x0614 | Numeric value |
| Calibration Lamp | 0x0615 | LED indicator (ON/OFF) |
| Detector Temperature | 0x0617 | Numeric value (deg C) |
| Integration Time | 0x0618 | Numeric value (ms) |
| Swath Width | 0x0619 | Numeric value (km) |

**REQ-PLD-MCS-002:** The FPA Temperature chart shall include a horizontal reference line at the FPA target temperature (e.g., -5 deg C / -15 deg C) and the operational threshold temperature, to provide visual context for readiness assessment.

**REQ-PLD-MCS-003:** An SNR time-series chart shall be added to the Payload tab, showing signal-to-noise ratio history during imaging sessions. This is critical for ocean color data quality trending.

**REQ-PLD-MCS-004:** The Image Catalog panel shall be enhanced to display a tabular catalog of all onboard images (from the `get_image_catalog` command), showing for each image:
- Scene ID
- Timestamp
- Latitude/Longitude
- Quality percentage
- Status (OK / PARTIAL / CORRUPT)
- Size (MB)
- Memory segment

**REQ-PLD-MCS-005:** The Payload Overview panel on the Overview tab shall display the payload mode as text (OFF / STANDBY / IMAGING) with color-coded status, and the FPA temperature as a summary value. This is currently implemented.

**REQ-PLD-MCS-006:** A storage management gauge on the Overview tab shall provide at-a-glance storage utilisation with color thresholds: green (< 70%), yellow (70--90%), red (> 90%).

### 9.3 Position Configuration

Per `configs/eosat1/mcs/positions.yaml`, the Payload Operations position:

| Setting | Value |
|---|---|
| `display_name` | "Payload Operations" |
| `subsystems` | [payload] |
| `allowed_subsystems` | [payload] |
| `allowed_services` | [1, 3, 5, 8, 11, 15, 17, 20] |
| `allowed_func_ids` | [20, 21, 22, 23, 24, 25, 26] |
| `visible_tabs` | [overview, payload, commanding, procedures, manual] |
| `overview_subsystems` | [payload] |
| `manual_sections` | [06_payload] |

**REQ-PLD-MCS-007:** The Payload Operations position shall have read-only visibility of AOCS attitude error (0x0217) and EPS battery SoC (0x0101) on the Overview tab, as these are critical pre-conditions for imaging decisions. This does not require commanding capability on those subsystems.

---

## 10. Planner Requirements

### 10.1 Current Planner Capabilities

The planner package (`packages/smo-planner`) currently provides:

- **OrbitPlanner:** Ground track prediction with lat/lon/alt/eclipse state per timestep.
- **ContactPlanner:** Ground station contact window computation (AOS/LOS).
- **ActivityScheduler:** Activity scheduling with conflict detection, state management, and procedure references.

### 10.2 Imaging-Specific Planner Requirements

**REQ-PLD-PLAN-001:** The planner shall support an **imaging opportunity calculator** that, given a list of target regions (lat/lon bounding boxes), identifies all overpasses where the spacecraft ground track places the target within the imaging swath (60 km width). Inputs:
- Target region database (see Section 7.1)
- Ground track prediction (24--72 hour horizon)
- Swath width parameter (configurable, default 60 km)
- Daylight constraint (eclipse flag = false)
Output: list of imaging opportunity windows with start time, duration, target name, and viewing geometry.

**REQ-PLD-PLAN-002:** The planner shall support **imaging-downlink coordination.** When an imaging activity is scheduled, the planner shall automatically verify that a ground station contact window is available within a configurable time window (default 6 hours) for data downlink. If no contact is available, a warning shall be raised.

**REQ-PLD-PLAN-003:** The planner shall enforce the following resource constraints on imaging activities:
- Power: imaging draws ~25 W. Combined with cooler, total payload power is ~40 W. Must not exceed EPS power budget.
- Data volume: each image is ~800 MB raw (~200 MB compressed). Must not exceed available storage.
- Thermal: maximum 10 minutes continuous imaging. Must have at least 20 minutes cooldown between imaging sessions.
- Attitude: imaging conflicts with orbit maintenance maneuvers and momentum dumps.

**REQ-PLD-PLAN-004:** The planner shall support an **imaging campaign planner** that generates a multi-day schedule of imaging sessions over target regions, balanced against downlink capacity, power budget, and thermal constraints. The output shall be a validated activity schedule that the Flight Director can approve.

**REQ-PLD-PLAN-005:** The planner swath footprint visualization on the ground track map shall use the correct swath width (60 km, not derived solely from `SWATH_HALF_ANGLE_DEG = 3.5`). The swath half-angle should be computed from the orbital altitude and swath width: `half_angle = atan(swath_width / (2 * altitude))`.

---

## 11. Simulator Fidelity Requirements

### 11.1 Current Simulator Model

The payload simulator (`PayloadBasicModel` in `payload_basic.py`) models:

| Feature | Implementation | Fidelity |
|---|---|---|
| Mode transitions (OFF/STANDBY/IMAGING) | 3-state model | Adequate |
| FPA thermal model | Exponential approach to target/ambient with tau constants | Adequate |
| Cooler power draw | Configurable (15 W) | Adequate |
| Image capture | Scene-by-scene with lat/lon, quality, status | Good |
| Image catalog | Full catalog with metadata (scene_id, timestamp, position, quality, status, size, segment) | Good |
| Memory segment model | 8 segments with individual failure injection | Good |
| Checksum errors | Probabilistic with FPA degradation and CCD dropout multipliers | Adequate |
| SNR model | FPA temp-dependent with degradation factor | Good |
| Compression ratio | Configurable parameter | Basic |
| Calibration lamp | ON/OFF status | Basic |
| Failure injection | Cooler failure, FPA degradation, image corruption, memory segment failure, CCD line dropout | Good |

### 11.2 Fidelity Enhancement Requirements

**REQ-PLD-SIM-001: Multi-band simulation.** The simulator currently models all spectral bands as a single data stream. For higher fidelity, the simulator should model per-band SNR, as ocean color algorithms rely on band-to-band radiometric ratios. At minimum, report per-band SNR as:
- Blue band (490 nm): SNR_blue = base_snr * 0.8 (lowest signal over water)
- Green band (560 nm): SNR_green = base_snr * 1.0 (reference)
- Red band (665 nm): SNR_red = base_snr * 0.9
- NIR band (842 nm): SNR_nir = base_snr * 0.7 (strong water absorption)

**REQ-PLD-SIM-002: Scene-dependent quality model.** Image quality should vary based on the imaging geometry:
- Solar zenith angle (eclipse vs sunlit affects illumination)
- View angle (nadir vs off-nadir)
- Sun glint probability (function of solar/view geometry)
- Cloud cover probability (could be modeled as random probability per scene)

**REQ-PLD-SIM-003: Attitude-coupled image quality.** The simulator should degrade image quality as a function of `aocs.att_error` during capture:
- `att_error < 0.1 deg`: no quality penalty
- `0.1 <= att_error < 0.5 deg`: quality * 0.95
- `0.5 <= att_error < 1.0 deg`: quality * 0.80
- `att_error >= 1.0 deg`: quality * 0.50

Currently, the capture command does not check AOCS state. The simulator should read `aocs.att_error` from `shared_params` and apply the quality modifier.

**REQ-PLD-SIM-004: Compression model.** The simulator should reduce the effective stored image size by the compression ratio. Currently, `image_size_mb` is stored at the raw size (800 MB) regardless of the compression ratio setting. The effective stored size should be `image_size_mb / compression_ratio`.

**REQ-PLD-SIM-005: Cooler lifetime model.** The simulator should track cumulative cooler-on time (`cooler_on_time_s`) and model gradual cooler performance degradation. After a configurable number of hours (e.g., 5000 hours), the cooling time constant should increase, reflecting reduced cooler efficiency.

**REQ-PLD-SIM-006: Data rate model accuracy.** The manual specifies raw data rate of ~40 Mbps and effective compressed rate of ~10 Mbps, but the simulator uses 80 Mbps. These values should be reconciled or the discrepancy documented as a known simulation simplification.

**REQ-PLD-SIM-007: FPA temperature discrepancy.** The simulator uses a cooler target of -5 deg C (`payload.yaml`), while the manual specifies -15 deg C and the commissioning procedures reference -30 deg C. The Payload Operations Engineer shall use the configured value for training but should be aware that the operational spacecraft may have different thermal performance. The simulator configuration should be updated to match the reference documentation.

### 11.3 Failure Injection Requirements

The simulator supports the following failure modes relevant to payload operations:

| Failure | Injection Method | Training Value |
|---|---|---|
| `cooler_failure` | `inject_failure("cooler_failure")` | Critical -- tests FPA thermal anomaly response (CTG-006) |
| `fpa_degraded` | `inject_failure("fpa_degraded")` | High -- tests degraded imaging quality recognition |
| `image_corrupt` | `inject_failure("image_corrupt", count=N)` | High -- tests corrupted image handling (CTG-015) |
| `memory_segment_fail` | `inject_failure("memory_segment_fail", segment=N)` | High -- tests memory management response |
| `ccd_line_dropout` | `inject_failure("ccd_line_dropout")` | Medium -- tests partial image quality assessment |

**REQ-PLD-SIM-008:** The following additional failure modes should be supported for comprehensive payload operations training:
- **Shutter stuck closed:** Payload captures dark frames only, regardless of mode.
- **Storage interface failure:** Capture command returns "Insufficient storage" even with capacity available.
- **Compression failure:** Images stored at raw size, causing rapid storage exhaustion.
- **Cal lamp stuck on:** Calibration lamp remains active during science imaging, contaminating data.

---

## 12. Training Scenarios

### 12.1 Commissioning Training

| Scenario | Description | Skills Tested |
|---|---|---|
| **COMM-PLD-01** | Nominal payload power-on and cooler activation | Power-on sequence, cooldown monitoring, telemetry interpretation |
| **COMM-PLD-02** | Cooler fails to start (cooler_failure injected) | Anomaly detection, off-nominal decision tree, FDIR coordination |
| **COMM-PLD-03** | First light with degraded pointing | Imaging with att_error > 0.1 deg, quality assessment, GO/NO-GO decision |
| **COMM-PLD-04** | Radiometric calibration sequence | Dark frame capture, calibration site targeting, multi-integration time images |

### 12.2 Nominal Operations Training

| Scenario | Description | Skills Tested |
|---|---|---|
| **NOM-PLD-01** | Routine ocean color imaging session | Full imaging cycle, target verification, data quality monitoring |
| **NOM-PLD-02** | Multi-target imaging campaign | Campaign planning, storage management, downlink scheduling |
| **NOM-PLD-03** | Data downlink with link degradation | Priority data selection, downlink abort/resume, link margin monitoring |
| **NOM-PLD-04** | Storage management under pressure | Imaging with > 80% storage, prioritization decisions, image deletion |

### 12.3 Contingency Training

| Scenario | Description | Skills Tested |
|---|---|---|
| **CTG-PLD-01** | Corrupted image -- single transient SEU | Checksum error detection, root cause diagnosis, re-capture |
| **CTG-PLD-02** | Corrupted images -- memory segment failure | Memory segment identification, bad segment marking, capacity reassessment |
| **CTG-PLD-03** | FPA thermal runaway (cooler degraded) | Thermal anomaly recognition, payload safing, recovery sequence |
| **CTG-PLD-04** | CCD line dropout during ocean current imaging | Degraded quality detection, SNR trending, impact assessment |
| **CTG-PLD-05** | Battery SoC drop triggers FDIR payload power-off | FDIR awareness, power-on recovery, cooler reactivation, missed imaging window management |
| **CTG-PLD-06** | Multiple bad memory segments | Cascading storage reduction, campaign replanning, data triage |

### 12.4 Emergency Training

| Scenario | Description | Skills Tested |
|---|---|---|
| **EMG-PLD-01** | Thermal runaway -- emergency payload power-off | Rapid safing, coordination with EPS/TCS, post-emergency assessment |
| **EMG-PLD-02** | FDIR-triggered safe mode during imaging | Payload state after autonomous power-off, recovery sequencing |

---

## 13. Requirements Traceability Matrix

| Req ID | Category | Section | Priority |
|---|---|---|---|
| REQ-PLD-BAND-001 | Band config | 2.2 | Medium |
| REQ-PLD-BAND-002 | Band config | 2.2 | High |
| REQ-PLD-BAND-003 | Band config | 2.2 | High |
| REQ-PLD-COOL-001 | Cooler ops | 2.3 | Critical |
| REQ-PLD-COOL-002 | Cooler ops | 2.3 | High |
| REQ-PLD-COOL-003 | Cooler ops | 2.3 | High |
| REQ-PLD-MEM-001 | Memory mgmt | 2.4 | Critical |
| REQ-PLD-MEM-002 | Memory mgmt | 2.4 | High |
| REQ-PLD-CAL-001 | Calibration | 2.5 | Medium |
| REQ-PLD-TM-001 | Telemetry | 3.3 | High |
| REQ-PLD-TM-002 | Telemetry | 3.3 | High |
| REQ-PLD-TM-003 | Telemetry | 3.3 | Critical |
| REQ-PLD-CMD-001 | Commands | 4.2 | Critical |
| REQ-PLD-CMD-002 | Commands | 4.2 | Critical |
| REQ-PLD-CMD-003 | Commands | 4.2 | High |
| REQ-PLD-COOL-COM-001 | Commissioning | 5.1.1 | Critical |
| REQ-PLD-COOL-COM-002 | Commissioning | 5.1.1 | High |
| REQ-PLD-COOL-COM-003 | Commissioning | 5.1.1 | Medium |
| REQ-PLD-OPS-001 | Nominal ops | 5.2.1 | High |
| REQ-PLD-OPS-002 | Nominal ops | 5.2.1 | Critical |
| REQ-PLD-OPS-003 | Nominal ops | 5.2.1 | Critical |
| REQ-PLD-DL-001 | Downlink | 5.2.2 | High |
| REQ-PLD-DL-002 | Downlink | 5.2.2 | High |
| REQ-PLD-DL-003 | Downlink | 5.2.2 | High |
| REQ-PLD-CTG-001 | Contingency | 5.3.1 | Critical |
| REQ-PLD-FDIR-001 | FDIR | 5.4 | High |
| REQ-PLD-FDIR-002 | FDIR | 5.4 | High |
| REQ-PLD-IQ-001 | Image quality | 6.2 | High |
| REQ-PLD-IQ-002 | Image quality | 6.2 | High |
| REQ-PLD-IQ-003 | Image quality | 6.2 | High |
| REQ-PLD-IQ-004 | Image quality | 6.2 | Medium |
| REQ-PLD-IQ-005 | Image quality | 6.2 | High |
| REQ-PLD-TGT-001 | Target planning | 7.1 | High |
| REQ-PLD-TGT-002 | Target planning | 7.2 | High |
| REQ-PLD-TGT-003 | Target planning | 7.2 | High |
| REQ-PLD-TGT-004 | Target planning | 7.2 | Medium |
| REQ-PLD-TGT-005 | Target planning | 7.3 | Medium |
| REQ-PLD-DL-004 | Downlink | 8.1 | High |
| REQ-PLD-DL-005 | Downlink | 8.2 | High |
| REQ-PLD-DL-006 | Downlink | 8.2 | High |
| REQ-PLD-MCS-001 | MCS display | 9.2 | High |
| REQ-PLD-MCS-002 | MCS display | 9.2 | Medium |
| REQ-PLD-MCS-003 | MCS display | 9.2 | High |
| REQ-PLD-MCS-004 | MCS display | 9.2 | High |
| REQ-PLD-MCS-005 | MCS display | 9.2 | Medium |
| REQ-PLD-MCS-006 | MCS display | 9.2 | Medium |
| REQ-PLD-MCS-007 | MCS display | 9.2 | Medium |
| REQ-PLD-PLAN-001 | Planner | 10.2 | High |
| REQ-PLD-PLAN-002 | Planner | 10.2 | High |
| REQ-PLD-PLAN-003 | Planner | 10.2 | High |
| REQ-PLD-PLAN-004 | Planner | 10.2 | Medium |
| REQ-PLD-PLAN-005 | Planner | 10.2 | Low |
| REQ-PLD-SIM-001 | Simulator | 11.2 | Medium |
| REQ-PLD-SIM-002 | Simulator | 11.2 | Medium |
| REQ-PLD-SIM-003 | Simulator | 11.2 | High |
| REQ-PLD-SIM-004 | Simulator | 11.2 | Medium |
| REQ-PLD-SIM-005 | Simulator | 11.2 | Low |
| REQ-PLD-SIM-006 | Simulator | 11.2 | Low |
| REQ-PLD-SIM-007 | Simulator | 11.2 | Medium |
| REQ-PLD-SIM-008 | Simulator | 11.3 | Medium |

---

## 14. Known Issues and Configuration Gaps

### 14.1 Simulator vs Documentation Discrepancies

| Item | Simulator Value | Documentation Value | Impact |
|---|---|---|---|
| FPA cooler target | -5 deg C | -15 deg C (manual) / -30 deg C (procedures) | Training realism -- operators may be confused by different thresholds |
| Raw data rate | 80 Mbps | 40 Mbps (manual) | Data volume calculations will differ |
| Swath width | 30 km (default state) | 60 km (manual) | Target planning coverage area |
| Total storage | 20 GB (simulator) | 2 GB (manual) | Image capacity calculations significantly different |
| Image size | 800 MB | 50--200 MB (manual) | Number of storable images |

### 14.2 Missing FDIR Configuration

The current FDIR rules (`configs/eosat1/subsystems/fdir.yaml`) lack a dedicated payload FPA over-temperature rule. The only payload-related FDIR trigger is `eps.bat_soc < 20%` causing `payload_poweroff`. The following rules should be added:

- `payload.fpa_temp > 12 deg C` --> `payload_poweroff` (Level 1)
- `payload.fpa_temp > 25 deg C` --> `payload_emergency_off` (Level 2) -- risk of permanent detector damage
- `payload.store_used > 98%` --> `payload_imaging_inhibit` (Level 1) -- prevent storage overflow

### 14.3 Telemetry Parameter Gaps

The payload simulator generates telemetry for parameters 0x0614 through 0x0619 (Phase 4 flight hardware realism), but these are not listed in the `param_ids` section of `configs/eosat1/subsystems/payload.yaml`. They are hardcoded in the simulator model. The configuration file should be updated to include these parameter IDs for consistency.

---

## 15. Appendix A -- Payload Mode State Diagram

```
                    PAYLOAD_SET_MODE(mode=1)
    +-------+  --------------------------->  +---------+
    |  OFF  |                                | STANDBY |
    | (0)   |  <---------------------------  | (1)     |
    +-------+    PAYLOAD_SET_MODE(mode=0)    +---------+
                                               |     ^
                         PAYLOAD_SET_MODE      |     |  PAYLOAD_SET_MODE
                              (mode=2)         |     |     (mode=1)
                                               v     |
                                             +---------+
                                             | IMAGING |
                                             | (2)     |
                                             +---------+

    Constraints for OFF --> STANDBY:
      - eps.bat_soc > 75% (commissioning) / > 50% (nominal)
      - Bidirectional link active

    Constraints for STANDBY --> IMAGING:
      - fpa_ready = 1 (fpa_temp <= target + 5 C)
      - aocs.att_error < 0.5 deg
      - eps.bat_soc > 40%
      - store_used < 90%
      - AOCS mode = NADIR_POINT or FINE_POINT
```

## 16. Appendix B -- Imaging Sequence Timeline

```
T-30 min    Enter STANDBY (mode=1), cooler starts
            Monitor FPA cooldown every 2 min
T-5 min     FPA reaches operational temperature, fpa_ready = 1
            Verify AOCS pointing: att_error < 0.1 deg
            Verify EPS: bat_soc > 40%
            Verify storage: store_used < 90%
T+0         Enter IMAGING (mode=2)
            Continuous monitoring: SID 5 poll every 30 s
T+0 to T+10 Image acquisition (max 10 min)
            Monitor: image_count incrementing
            Monitor: att_error < 0.5 deg
            Monitor: fpa_temp < -20 C (manual threshold)
            Monitor: store_used < 95%
T+10        Exit IMAGING, return to STANDBY (mode=1)
            Record final image_count, storage delta
T+20        Cooldown period (20 min minimum before next session)
            If no more imaging: return to OFF (mode=0)
```

---

![AIG - Artificial Intelligence Generated](../../Screenshot%202026-03-10%20at%202.31.22%20PM.png)

*This document was generated with AI assistance.*
*AIG logo source: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

---

*End of Document -- EOSAT1-REQ-PLD-001*

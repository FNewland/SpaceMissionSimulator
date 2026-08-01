# Payload Operations (payload_ops) -- Role Analysis

**Position ID:** `payload_ops`
**Display Name:** Payload Operations
**Subsystems:** payload
**Allowed PUS Services:** 1, 3, 5, 8, 11, 15, 17, 20
**Allowed func_ids:** 20, 21, 22, 23, 24, 25, 26
**Visible Tabs:** overview, payload, commanding, procedures, manual
**Manual Sections:** 06_payload

## 1. Mission Lifecycle Phases and Applicable Procedures

### Commissioning

| Procedure | ID | payload_ops Role |
|---|---|---|
| Payload Power On | COM-009 | Power on imager, verify telemetry |
| FPA Cooler Activation | COM-010 | Activate cooler, monitor FPA temp |
| Payload Calibration | COM-011 | Execute calibration sequence, verify image quality |
| First Light | COM-012 | Capture first image, assess quality |

### Nominal Operations

| Procedure | ID | payload_ops Role |
|---|---|---|
| Imaging Session | NOM-002 | Configure and execute imaging |
| Data Downlink | NOM-003 | Select and prioritize data for download |

### Contingency

| Procedure | ID | payload_ops Role |
|---|---|---|
| Thermal Exceedance | CTG-004 | Safe payload if affected |
| Payload Anomaly | CTG-006 | Diagnose and safe payload |
| Corrupted Image Recovery | CTG-015 | Assess corruption, retake if possible |

### Emergency

| Procedure | ID | payload_ops Role |
|---|---|---|
| Thermal Runaway | EMG-006 | Emergency payload power off |

## 2. Available Commands and Telemetry

### Commands

#### Payload Function Commands (S8.1, func_ids 26-39 + 92-95)

The whole block below was documented at func_ids 20-26 — a stale numbering
scheme in which every entry named the wrong function and seven of the
fourteen commands were missing. Encodings are the authoritative ones from
`commands/tc_catalog.yaml`; the generated `manual/09_command_reference.md`
carries the full argument tables.

| Command | func_id | Description | Arguments (engineering) | Wire example |
|---|---|---|---|---|
| PAYLOAD_SET_MODE | 26 (0x1A) | Set payload mode | mode enum 0=OFF, 1=STANDBY, 2=IMAGING | `1A 02` |
| PAYLOAD_SET_SCENE | 27 (0x1B) | Pre-set scene id (deprecated — CAPTURE carries its own) | scene_id uint16 | `1B 04 12` |
| PAYLOAD_CAPTURE | 28 (0x1C) | Trigger one acquisition | scene_id uint16 (0=auto), lines uint16 (256-20000) | `1C 04 12 08 00` |
| PAYLOAD_DOWNLOAD_IMAGE | 29 (0x1D) | Queue image for downlink | scene_id uint16 | `1D 04 12` |
| PAYLOAD_DELETE_IMAGE | 30 (0x1E) | Delete image from payload memory | scene_id uint16 | `1E 04 12` |
| PAYLOAD_MARK_BAD_SEGMENT | 31 (0x1F) | Mark memory segment unusable | segment_id uint8 (0-7) | `1F 03` |
| PAYLOAD_GET_IMAGE_CATALOG | 32 (0x20) | Request stored image catalog | (none) | `20` |
| PAYLOAD_SET_BAND_CONFIG | 33 (0x21) | Spectral band enable mask | mask bitfield b0 BLUE, b1 GREEN, b2 RED, b3 NIR (mask != 0) | `21 0F` |
| PAYLOAD_SET_INTEGRATION_TIME | 34 (0x22) | Per-band integration times | 4x uint16, 0.01 ms/LSB, 0.10-655.35 ms | `22 03 20 04 B0 06 40 07 D0` (8/12/16/20 ms) |
| PAYLOAD_SET_GAIN | 35 (0x23) | Detector gain | gain uint16, 0.001x/LSB, 0.100-10.000 | `23 03 E8` (1.000x) |
| PAYLOAD_COOLER_SETPOINT | 36 (0x24) | FPA TEC regulation setpoint | setpoint uint16 centi-KELVIN, -20.0 to 0.0 degC | `24 64 D7` (-15.00 degC) |
| PAYLOAD_START_CALIBRATION | 37 (0x25) | Begin calibration | mode enum 0=DARK, 1=FLAT, 2=BOTH | `25 00` (dark only) |
| PAYLOAD_STOP_CALIBRATION | 38 (0x26) | Abort calibration | (none) | `26` |
| PAYLOAD_SET_COMPRESSION | 39 (0x27) | Compression algorithm + ratio | algorithm enum 0-3, ratio uint8 0.1x/LSB (0=AUTO) | `27 03 00` |
| PAYLOAD_SELECT_FILTER | 92 (0x5C) | Filter wheel position | position enum 0=CLEAR, 1=ND2, 2=DIFFUSER, 3=DARK | `5C 00` |
| PAYLOAD_CYCLE_SHUTTER | 93 (0x5D) | Shutter cycle test | cycles uint8 1-100 | `5D 05` |
| PAYLOAD_INITIATE_TRANSFER | 94 (0x5E) | Start on-board transfer of a stored scene | scene_id uint16 | `5E 04 12` |
| PAYLOAD_CAL_LAMP | 95 (0x5F) | Calibration lamp on/off | on enum 0/1 | `5F 01` |

Payload devices are additionally reachable over **S2.1** (FPA 0x0600, cooler
0x0601, cal lamp 0x0602, shutter 0x0603, compression unit 0x0604) — service 2
is granted to this position.

#### General Services

| Service | Commands | Description |
|---|---|---|
| S1 | (TM only) | Request verification reports |
| S3 | HK_REQUEST, HK_ENABLE, HK_DISABLE, HK_SET_INTERVAL | Housekeeping for SID 5 (Payload) |
| S5 | EVENT_ENABLE, EVENT_DISABLE | Event report control |
| S11 | SCHEDULE_TC, DELETE_SCHEDULED, ENABLE/DISABLE_SCHEDULE, LIST_SCHEDULE | Time-tagged scheduling (pre-plan imaging sessions, capture triggers) |
| S15 | ENABLE_STORE, DISABLE_STORE, DUMP_STORE, DELETE_STORE, STORE_STATUS | Science store management (store_id=3 for science data) |
| S17 | CONNECTION_TEST | Link verification |
| S20 | SET_PARAM, GET_PARAM | Direct parameter read/write for payload parameters |

### Telemetry

#### Payload Parameters (SID 5, 8 s interval)

**Imager Status:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| payload.mode | 0x0600 | -- | Payload mode (off/standby/imaging) |
| payload.fpa_temp | 0x0601 | C | Focal plane array temperature |
| payload.cooler_pwr | 0x0602 | W | Cooler power draw |
| payload.imager_temp | 0x0603 | C | Imager electronics temperature |
| payload.fpa_ready | 0x0610 | -- | FPA temperature in operational range |
| payload.duty_cycle_pct | 0x0613 | % | Imaging duty cycle |

**Storage and Data:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| payload.store_used | 0x0604 | % | Storage utilisation percentage |
| payload.image_count | 0x0605 | -- | Number of stored images |
| payload.checksum_errors | 0x0609 | -- | Image checksum error count |
| payload.mem_total_mb | 0x060A | MB | Total payload memory |
| payload.mem_used_mb | 0x060B | MB | Used payload memory |
| payload.mem_segments_bad | 0x0612 | -- | Bad memory segment count |

**Image Quality:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| payload.last_scene_id | 0x060C | -- | Last captured scene ID |
| payload.last_scene_quality | 0x060D | % | Quality metric of last image |
| payload.compression_ratio | 0x0614 | -- | Image compression ratio |
| payload.cal_lamp_on | 0x0615 | -- | Calibration lamp status |
| payload.snr | 0x0616 | dB | Signal-to-noise ratio |
| payload.detector_temp | 0x0617 | C | CCD/CMOS detector temperature |
| payload.integration_time | 0x0618 | ms | Detector integration time |
| payload.swath_width_km | 0x0619 | km | Ground swath width |

#### Limit Monitoring

| Parameter | Yellow | Red |
|---|---|---|
| payload.fpa_temp | -18.0 -- 8.0 C | -20.0 -- 12.0 C |
| payload.snr | 30.0 -- 55.0 dB | 20.0 -- 60.0 dB |
| payload.detector_temp | -18.0 -- 8.0 C | -20.0 -- 12.0 C |

### Display Widgets

**Imager Status page:** FPA temperature gauge (-20 to 20 C); FPA ready indicator; storage used gauge (0-100%); value table of mode, cooler_pwr, image_count, checksum_errors, duty_cycle_pct.
**Memory & Catalog page:** Memory used gauge (0-20 GB); memory total gauge; value table of mem_segments_bad, last_scene_id, last_scene_quality.

## 3. Inter-Position Coordination Needs

| Scenario | Coordinating With | Coordination Details |
|---|---|---|
| Payload power on (COM-009) | flight_director, eps_tcs | FD authorizes; eps_tcs confirms power budget supports ~25 W imager draw |
| FPA cooler activation (COM-010) | flight_director, eps_tcs | FD authorizes; eps_tcs monitors additional ~8 W cooler power and thermal |
| Payload calibration (COM-011) | flight_director | FD authorizes calibration sequence |
| First light (COM-012) | flight_director, aocs | FD authorizes; AOCS confirms fine pointing; payload captures and assesses |
| Imaging session (NOM-002) | aocs | AOCS ensures pointing for imaging windows; payload configures and triggers capture |
| Data downlink (NOM-003) | ttc | TTC configures high-rate link; payload selects and prioritizes images for download |
| Thermal exceedance (CTG-004) | flight_director, eps_tcs | Payload safes imager (mode=off or standby); eps_tcs adjusts thermal |
| Payload anomaly (CTG-006) | flight_director | FD authorizes response; payload diagnoses and safes imager |
| Corrupted image (CTG-015) | (independent) | Assess corruption, mark bad segments (PAYLOAD_MARK_BAD_SEGMENT), retake if possible |
| Thermal runaway (EMG-006) | flight_director, eps_tcs | Emergency payload power off via PAYLOAD_SET_MODE(off) |

### Science Store Management

The Payload Operations position shares S15 access with the TTC position. Coordination protocol:

1. Payload_ops selects images for downlink (PAYLOAD_DOWNLOAD_IMAGE) which queues them to the Science Store (store_id=3).
2. TTC position dumps the Science Store during high-rate passes (DUMP_STORE store_id=3).
3. After confirmed downlink, payload_ops deletes images from payload memory (PAYLOAD_DELETE_IMAGE).
4. If store_used approaches capacity, payload_ops manages deletions (PAYLOAD_DELETE_IMAGE) to free space.

## 4. GO/NO-GO Responsibilities

The Payload Operations position provides GO/NO-GO input to the Flight Director for:

- **Imaging readiness:** Confirm payload.mode=standby or imaging, fpa_ready=1, fpa_temp within operational range, checksum_errors not increasing, sufficient storage available.
- **FPA cooler activation:** Confirm fpa_temp trending toward operational range; cooler_pwr nominal.
- **Image capture:** Confirm last_scene_quality acceptable (no degradation trend); snr within limits; detector_temp in range.
- **Data downlink priority:** Report image catalog, recommended download order, and data volume to TTC position.

**Critical Decision Points:**
- If fpa_temp exits yellow range (-18 to 8 C), recommend halting imaging and investigating.
- If checksum_errors increment during a capture session, recommend aborting and assessing memory health.
- If store_used exceeds 80%, recommend prioritising downlink or deleting low-priority images.
- If mem_segments_bad increases, execute PAYLOAD_MARK_BAD_SEGMENT and report to Flight Director.
- If last_scene_quality drops below acceptable threshold, recommend recalibration (COM-011) or adjusting integration_time.
- If payload.snr drops below 30 dB (yellow), investigate root cause (detector temp, pointing error, or calibration drift).

---
*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

# TT&C (ttc) -- Role Analysis

**Position ID:** `ttc`
**Display Name:** TT&C
**Subsystems:** ttc
**Allowed PUS Services:** 1, 3, 5, 8, 9, 11, 15, 17, 20
**Allowed func_ids:** 50, 51, 52, 53, 54, 55
**Visible Tabs:** overview, ttc, commanding, procedures, manual
**Manual Sections:** 05_ttc

## 1. Mission Lifecycle Phases and Applicable Procedures

### LEOP

| Procedure | ID | ttc Role |
|---|---|---|
| First Acquisition of Signal | LEOP-001 | Configure ground station, monitor RF acquisition |
| Initial Orbit Determination | LEOP-003 | Provide range/Doppler data |

### Commissioning

| Procedure | ID | ttc Role |
|---|---|---|
| TTC Link Verification | COM-006 | Verify uplink/downlink at all data rates |

### Nominal Operations

| Procedure | ID | ttc Role |
|---|---|---|
| Pass Startup | NOM-001 | Verify link acquisition |
| Data Downlink | NOM-003 | Configure high-rate downlink |
| Data Rate Change | NOM-011 | Configure and verify new data rate |

### Contingency

| Procedure | ID | ttc Role |
|---|---|---|
| TTC Link Loss Recovery | CTG-003 | Reconfigure RF chain and ground station |
| BER Anomaly | CTG-014 | Diagnose BER issue, adjust modulation/rate |

### Emergency

| Procedure | ID | ttc Role |
|---|---|---|
| Loss of Communication | EMG-004 | Execute blind commanding, reconfigure RF |

## 2. Available Commands and Telemetry

### Commands

#### TTC Function Commands (S8, func_ids 50-55)

| Command | func_id | Description | Fields |
|---|---|---|---|
| TTC_SWITCH_PRIMARY | 50 | Switch to primary transponder | (none) |
| TTC_SWITCH_REDUNDANT | 51 | Switch to redundant transponder | (none) |
| TTC_SET_DATA_RATE | 65 | Select a rung of the TM data-rate ladder | rate (uint8 tier code): 0=LOW (16 kbps, routine operations), 1=HIGH (128 kbps, dumps only), 255=BEACON (1 kbps, bootloader only, not accepted here) |
| TTC_SET_DOWNLINK_MODE | 79 | Enter/leave DOWNLINK (payload-dump) mode | on (uint8): 1=enter, 0=leave. This is the mode gate on the HIGH tier |
| TTC_PA_ON | 53 | Enable power amplifier | (none) |
| TTC_PA_OFF | 54 | Disable power amplifier | (none) |
| TTC_SET_TX_POWER | 68 | Select an SSPA operating point (discrete steps; unknown codes are REJECTED, not clamped) | level enum: 0=LOW (1.0 W RF), 1=NOMINAL (2.0 W RF), 2=HIGH (5.0 W RF = ttc.yaml pa_max_power_w). The old '2 = 8 W' was never executable. |

#### General Services

| Service | Commands | Description |
|---|---|---|
| S1 | (TM only) | Request verification reports |
| S3 | HK_REQUEST, HK_ENABLE, HK_DISABLE, HK_SET_INTERVAL | Housekeeping for SID 6 (TTC) |
| S5 | EVENT_ENABLE, EVENT_DISABLE | Event report control |
| S9 | SET_TIME, REQUEST_TIME | Time management (time sync support) |
| S11 | SCHEDULE_TC, DELETE_SCHEDULED, ENABLE/DISABLE_SCHEDULE, LIST_SCHEDULE | Time-tagged scheduling (pass planning, automated rate changes) |
| S15 | ENABLE_STORE, DISABLE_STORE, DUMP_STORE, DELETE_STORE, STORE_STATUS | Onboard TM storage management (HK/Event/Science/Alarm stores) |
| S17 | CONNECTION_TEST | Echo-based connectivity test |
| S20 | SET_PARAM, GET_PARAM | Direct parameter read/write for TTC parameters |

### Telemetry

#### TTC Parameters (SID 6, 8 s interval)

**Link Status:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| ttc.mode | 0x0500 | -- | Transponder mode |
| ttc.link_status | 0x0501 | -- | Link active indicator |
| ttc.rssi | 0x0502 | dBm | Received signal strength |
| ttc.link_margin | 0x0503 | dB | Link margin |
| ttc.eb_n0 | 0x0519 | dB | Energy per bit to noise density |
| ttc.ber | 0x050C | -- | Bit error rate (log10 scale) |
| ttc.carrier_lock | 0x0510 | -- | Carrier lock status |
| ttc.bit_sync | 0x0511 | -- | Bit synchronisation status |
| ttc.frame_sync | 0x0512 | -- | Frame synchronisation status |

**Hardware:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| ttc.pa_on | 0x0516 | -- | Power amplifier status |
| ttc.pa_temp | 0x050F | C | PA temperature |
| ttc.tx_fwd_power | 0x050D | dBm | Transmit forward power |
| ttc.xpdr_temp | 0x0507 | C | Transponder temperature |
| ttc.tm_data_rate | 0x0506 | bps | Active TM information rate (BEACON 1000 / LOW 16000 / HIGH 128000) |
| ttc.tm_tier | 0x0539 | -- | ACTIVE rate tier index (0=BEACON, 1=LOW, 2=HIGH) |
| ttc.tm_tier_cmd | 0x053A | -- | COMMANDED tier index; differs from 0x0539 when a gate demotes the link |
| ttc.tm_symbol_rate | 0x053B | Bd | Symbol rate on air (16 kBd at LOW, 64 kBd at HIGH) |
| ttc.downlink_mode | 0x053C | -- | DOWNLINK/dump mode (0/1) |
| ttc.cmd_rx_count | 0x0513 | -- | Total received command counter |

**Ranging and Tracking:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| ttc.range_km | 0x0509 | km | Range to ground station |
| ttc.contact_elevation | 0x050A | deg | Ground station elevation angle |
| ttc.agc_level | 0x051A | dB | Automatic gain control level |
| ttc.doppler_hz | 0x051B | Hz | Doppler shift on downlink |
| ttc.range_rate | 0x051C | m/s | Range rate to ground station |

**Security and Statistics:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| ttc.cmd_auth_status | 0x051D | -- | Command authentication status |
| ttc.total_bytes_tx | 0x051E | B | Total bytes transmitted this pass |
| ttc.total_bytes_rx | 0x051F | B | Total bytes received this pass |

#### Limit Monitoring

| Parameter | Yellow | Red |
|---|---|---|
| ttc.ber (log10) | -7.0 -- -5.0 | -8.0 -- -4.0 |
| ttc.pa_temp | 0.0 -- 55.0 C | -10.0 -- 65.0 C |
| ttc.agc_level | -80.0 -- -30.0 dB | -100.0 -- -20.0 dB |

### Display Widgets

**Link Status page:** Link active, carrier lock, bit sync, frame sync indicators; RSSI gauge (-120 to -60 dBm); link margin gauge (-5 to 20 dB); Eb/N0 gauge (0-30 dB); BER gauge (log10, -12 to -1).
**PA & Hardware page:** PA status indicator; PA temperature gauge (20-70 C); TX forward power gauge; value table of mode, xpdr_temp, cmd_rx_count, range_km, elevation. The rate is shown on the RF page as a named TIER — `LOW (16 kbps, BPSK, conv+RS)` — with the ACTIVE and COMMANDED tiers on separate rows plus the symbol rate and DOWNLINK-mode state, never as a bare bit rate.
**Link Trends page:** Signal quality chart (RSSI + link margin, 10 min); BER chart (10 min); PA temperature chart (10 min).

## 3. Inter-Position Coordination Needs

| Scenario | Coordinating With | Coordination Details |
|---|---|---|
| First acquisition (LEOP-001) | flight_director | TTC configures GS and monitors RF; FD authorizes pass start |
| Initial orbit determination (LEOP-003) | flight_director, aocs | TTC provides range/Doppler data; AOCS processes orbit |
| Pass startup (NOM-001) | flight_director | TTC verifies link acquired (carrier_lock, bit_sync, frame_sync); FD issues GO |
| Data downlink (NOM-003) | payload_ops | TTC enters DOWNLINK mode (func 79) then raises the ladder to HIGH (128 kbps, func 65 rate=1); payload_ops selects data for download via S15 |
| Link loss recovery (CTG-003) | flight_director | TTC reconfigures RF chain; FD coordinates recovery effort |
| BER anomaly (CTG-014) | flight_director | TTC adjusts data rate or TX power; FD authorizes |
| Loss of communication (EMG-004) | flight_director | TTC executes blind commanding; FD coordinates with ground station network |

### Onboard Storage Coordination

The TTC position has S15 (Onboard Storage) access, making it responsible for managing the four onboard TM stores:

| Store | ID | Capacity | Coordination |
|---|---|---|---|
| HK Store | 1 | 1 MB | Periodic dump during pass; coordinate with fdir_systems for HK config |
| Event Store | 2 | 512 KB | Dump as needed; high priority after anomalies |
| Science Store | 3 | 8 MB | Primary downlink target; coordinate with payload_ops for prioritization |
| Alarm Store | 4 | 256 KB | Highest priority dump; always download first |

## 4. GO/NO-GO Responsibilities

The TT&C position provides GO/NO-GO input to the Flight Director for:

- **Pass startup:** Confirm carrier_lock=1, bit_sync=1, frame_sync=1; RSSI and link_margin within nominal range; BER below -5 (log10).
- **Data downlink session:** Confirm the ACTIVE tier is HIGH (128 kbps) and equals the COMMANDED tier (0x0539 = 0x053A = 2), DOWNLINK mode on (0x053C = 1), elevation at or above 10 deg, PA on, TX power at nominal (5 W), sufficient link margin (>3 dB).
- **Commanding authorization:** Confirm uplink healthy (cmd_rx_count incrementing, no authentication issues).
- **End of pass:** Report pass statistics (total_bytes_tx, total_bytes_rx), stores remaining, next contact window.

**Critical Decision Points:**
- If BER degrades above -5.0 (yellow), recommend stepping the ladder down to LOW (TTC_SET_DATA_RATE rate=0, func 65) to the Flight Director — worth 10.5 dB (9.03 dB of rate plus 1.5 dB of coding gain).
- If pa_temp exceeds 55 C (yellow), recommend reducing TX power or cycling PA off temporarily.
- If carrier_lock, bit_sync, or frame_sync drops, immediately report to Flight Director; initiate link loss recovery (CTG-003) if all three lost.
- If link margin drops below 3 dB during a HIGH-tier dump, recommend falling back to LOW and re-planning the dump onto a higher pass. LOW is the routine operations rate, so nothing but the dump is lost.
- Before Loss of Communication (EMG-004), exhaust all options: switch transponder (TTC_SWITCH_REDUNDANT), increase TX power (TTC_SET_TX_POWER level=2), hold at LOW (16 kbps), and as a last resort force the BEACON rung with TTC_SET_BEACON_MODE (func 70) — 1 kbps in 3.1 kHz is the most robust waveform the spacecraft has.

---
*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

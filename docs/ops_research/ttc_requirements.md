# EOSAT-1 TT&C Position -- Operations and Simulation Requirements

**Document ID:** EOSAT1-OPS-REQ-TTC-001
**Issue:** 1.0
**Date:** 2026-03-12
**Author:** Communications Engineer (TT&C + Ground Stations)
**Classification:** UNCLASSIFIED -- For Simulation Use Only

---

## 1. Scope and Purpose

This document defines the operational, simulation, and tooling requirements for the
Telemetry, Tracking and Command (TT&C) position on the EOSAT-1 ocean current monitoring
cubesat mission. It covers the space segment RF equipment, the ground station network,
operational procedures from LEOP through nominal operations, Mission Control System (MCS)
display needs, planner integration, simulator fidelity, and training scenarios.

The mission profile calls for:

- Cold-redundant S-band transponder pair
- Dedicated PDM (Pulse Duration Modulation) command channel with 15-minute TX+PA timer
  after successful decode
- Burn wire antenna deployment mechanism
- Beacon packet transmission from bootloader
- Dual ground stations: Iqaluit (63.747N, 68.518W) and Troll (72.012S, 2.535E)

---

## 2. Equipment Under TT&C Responsibility

### 2.1 Space Segment

| Equipment | Designation | Notes |
|---|---|---|
| Primary S-band Transponder | XPDR-A | Active by default post-separation |
| Redundant S-band Transponder | XPDR-B | Cold standby, ground-commanded activation |
| Power Amplifier (Primary) | PA-A | 2 W nominal, 5 W max; auto-shutdown at 70 C |
| Power Amplifier (Redundant) | PA-B | 2 W nominal, 5 W max; auto-shutdown at 70 C |
| Zenith (+Z) Patch Antenna | ANT-Z+ | Hemispherical coverage, 3 dBi gain |
| Nadir (-Z) Patch Antenna | ANT-Z- | Hemispherical coverage, 3 dBi gain |
| Burn Wire Antenna Deployment Mechanism | BWADM | Releases stowed antenna elements on command |
| PDM Command Decoder | PDM-DEC | Hardware decoder on dedicated command channel |

**REQ-TTC-EQP-001:** The simulator shall model both transponders (XPDR-A and XPDR-B) as
independently failable units with cold-redundancy switching via ground command.

**REQ-TTC-EQP-002:** Only one transponder shall be active at any time. Activating one
transponder shall automatically power-off the other.

**REQ-TTC-EQP-003:** The PA thermal model shall simulate heat generation during
transmission, auto-shutdown at 70 C, and re-enable with 15 C hysteresis (cool to 55 C
before allowing PA re-enable).

**REQ-TTC-EQP-004:** The antenna system shall provide near-omnidirectional coverage via
automatic selection of the strongest-signal antenna.

### 2.2 Transponder Dedicated PDM Command Channel

The transponder includes a dedicated PDM command channel that operates independently
of the main telecommand processing chain. This channel provides a last-resort uplink
path when the OBC is unresponsive or stuck in bootloader.

**REQ-TTC-PDM-001:** The PDM decoder shall be modelled as a hardware-level command
receiver that functions independently of OBC software state (including bootloader mode).

**REQ-TTC-PDM-002:** Upon successful decode of a valid PDM command, the transponder
shall automatically start a 15-minute timer that enables the TX path and PA.

**REQ-TTC-PDM-003:** The 15-minute TX+PA timer shall be non-resettable by software.
After timer expiry, the TX and PA shall be disabled automatically to conserve power.

**REQ-TTC-PDM-004:** The PDM channel shall support a minimum command set:
- Switch to primary transponder
- Switch to redundant transponder
- Enable PA
- Force OBC reset

**REQ-TTC-PDM-005:** The simulator shall model the PDM channel state, including:
- PDM decode success/failure based on uplink signal quality
- 15-minute timer countdown (visible in telemetry)
- Automatic TX+PA shutdown at timer expiry

**REQ-TTC-PDM-006:** The PDM channel shall remain functional when the main TC chain is
non-operational (e.g., OBC in bootloader, application crash, or total software hang).

### 2.3 Burn Wire Antenna Deployment

**REQ-TTC-BWD-001:** The simulator shall model the burn wire antenna deployment mechanism
as a one-shot irreversible action. Once deployed, the antenna state cannot revert to
stowed.

**REQ-TTC-BWD-002:** Prior to antenna deployment, the transponder shall operate at
low-rate only (1 kbps downlink) with reduced link margin due to the stowed antenna
configuration.

**REQ-TTC-BWD-003:** The burn wire deployment command shall be gated by a safety
interlock requiring two sequential commands within 30 seconds (arm + fire pattern).

**REQ-TTC-BWD-004:** The simulator shall model deployment telemetry:
- Antenna deploy status (0=stowed, 1=deploying, 2=deployed, 3=failed)
- Burn wire current draw during deployment (transient, approximately 2 A for 5 s)
- Post-deployment RSSI improvement (expected +6 to +10 dB vs stowed)

**REQ-TTC-BWD-005:** Antenna deployment failure shall be injectable as a fault scenario.
Partial deployment (one element deployed, one stuck) shall be a supported failure mode
with degraded but non-zero antenna gain.

**REQ-TTC-BWD-006:** The EPS subsystem shall model the burn wire deployment current
draw as a transient load on the appropriate power line.

### 2.4 Beacon Packet in Bootloader

**REQ-TTC-BCN-001:** When the OBC is in bootloader mode (`obdh.sw_image` = 0), the
transponder shall autonomously transmit a beacon packet at a fixed interval (16 s,
matching HK SID 10 interval).

**REQ-TTC-BCN-002:** The beacon packet shall contain minimal spacecraft health data:
- Active OBC unit (`obdh.active_obc`, 0x030C)
- Active CAN bus (`obdh.active_bus`, 0x030E)
- Bus voltage (`eps.bus_voltage`, 0x0105)
- OBC temperature (`obdh.temp`, 0x0301)
- Uptime (`obdh.uptime`, 0x0308)
- Reboot count (`obdh.reboot_count`, 0x030A)
- Software image flag (`obdh.sw_image`, 0x0311)
- Last reboot cause (`obdh.last_reboot_cause`, 0x0316)

This matches the existing BootLoader HK structure (SID 10) in `hk_structures.yaml`.

**REQ-TTC-BCN-003:** The beacon shall be transmitted at low data rate (1 kbps) regardless
of the configured nominal data rate.

**REQ-TTC-BCN-004:** The beacon shall be receivable by the ground station without
requiring prior uplink lock, enabling passive detection of the spacecraft.

### 2.5 Low-Rate Operation Without Antenna Deployment

**REQ-TTC-LR-001:** The transponder shall support a low-rate mode (1 kbps downlink)
that provides positive link margin even with antennas in the stowed configuration.

**REQ-TTC-LR-002:** The uplink shall close at low rate (4 kbps TC) with stowed antennas,
provided ground station elevation exceeds 15 degrees (tighter constraint than the 5
degree minimum with deployed antennas).

**REQ-TTC-LR-003:** The simulator link budget model shall account for the stowed antenna
gain penalty (approximately -6 to -10 dB compared to deployed configuration).

**REQ-TTC-LR-004:** The MCS shall indicate the antenna deployment state and adjust link
budget predictions accordingly on the TTC display.

### 2.6 Ground Segment

#### 2.6.1 Iqaluit Ground Station

| Parameter | Value |
|---|---|
| Station Name | Iqaluit |
| Latitude | 63.747 N |
| Longitude | 68.518 W |
| Altitude | 0.03 km (approx) |
| Minimum Elevation | 5.0 deg |
| Antenna Diameter | 7.3 m (S-band) |
| Band | S |
| Role | Northern hemisphere primary |

**REQ-TTC-GS-IQ-001:** The Iqaluit ground station shall be added to the ground station
configuration (`ground_stations.yaml` and `orbit.yaml`), replacing or supplementing
existing northern stations as required by the mission profile.

**REQ-TTC-GS-IQ-002:** Contact window predictions for Iqaluit shall be computed using
the standard 5-degree minimum elevation mask.

**Note:** The current codebase defines ground stations at Svalbard (78.2N, 15.4E),
Troll (72.0S, 2.5E), Inuvik (68.3N, 133.5W), and O'Higgins (63.3S, 57.9W). The mission
profile specifies Iqaluit + Troll as the operational network. Configuration changes
are required to reflect this.

#### 2.6.2 Troll Ground Station

| Parameter | Value |
|---|---|
| Station Name | Troll |
| Latitude | 72.012 S |
| Longitude | 2.535 E |
| Altitude | 1.27 km |
| Minimum Elevation | 5.0 deg |
| Antenna Diameter | 7.3 m (S-band) |
| Band | S |
| Role | Southern hemisphere primary |

**REQ-TTC-GS-TR-001:** The Troll ground station configuration shall match the parameters
above (already present in the codebase at the correct coordinates).

#### 2.6.3 Ground Station Antenna Failure Model

**REQ-TTC-GSF-001:** The simulator shall model ground station antenna failures as
injectable faults, including:
- Total antenna failure (no uplink/downlink capability at affected station)
- Tracking failure (antenna unable to follow spacecraft, resulting in rapid RSSI decrease)
- Reduced G/T (partial feed or LNA degradation, modelled as dB reduction in G/T)
- Uplink-only failure (ground transmitter fault; receive chain still functional)
- Receive-only failure (LNA or receiver fault; can still transmit commands)

**REQ-TTC-GSF-002:** When a ground station antenna failure is active, contact windows
for that station shall still be computed (the failure is not a geometric constraint)
but the link budget shall reflect the degradation or outage.

**REQ-TTC-GSF-003:** The instructor station shall provide controls to inject and clear
ground station antenna failures for each station independently.

**REQ-TTC-GSF-004:** With only two ground stations (Iqaluit + Troll), a single-station
failure significantly reduces contact opportunities. The simulator shall correctly
model the reduced contact frequency so that operators experience realistic gap durations.

**REQ-TTC-GSF-005:** The MCS display shall show ground station health status for each
station, including:
- Antenna tracking state (IDLE / TRACKING / FAILED)
- Last contact time
- Next predicted contact window
- G/T status (NOMINAL / DEGRADED / FAILED)
- Uplink/downlink status independently

---

## 3. RF Link Parameters

### 3.1 Frequency Plan

| Parameter | Value | Source |
|---|---|---|
| Uplink Frequency | 2025.5 MHz | `ttc.yaml` `ul_freq_mhz` |
| Downlink Frequency | 2200.5 MHz | `ttc.yaml` `dl_freq_mhz` |
| Modulation (TC) | BPSK | Manual 05_ttc Section 2.1 |
| Modulation (TM) | QPSK | Manual 05_ttc Section 2.1 |
| Uplink Data Rate | 4 kbps | Manual 05_ttc Section 2.1 |
| Downlink Data Rate (high) | 64 kbps | `ttc.yaml` `tm_rate_hi_bps` |
| Downlink Data Rate (low) | 1 kbps | `ttc.yaml` `tm_rate_lo_bps` |
| RF Output Power (nominal) | 2 W | `ttc_basic.py` `_pa_nominal_power_w` |
| RF Output Power (max) | 5 W | `ttc_basic.py` `_pa_max_power_w` |

### 3.2 Link Budget

| Parameter | Uplink | Downlink |
|---|---|---|
| S/C Antenna Gain | 3 dBi | 3 dBi |
| GS G/T | -- | 20 dB/K |
| S/C EIRP | -- | 10 dBW (config) / 33 dBW (manual) |
| Required Eb/N0 | 9.6 dB | 9.6 dB |
| Min Link Margin at 5 deg | > 6 dB (UL) | > 3 dB (DL) |
| Coding Gain | 3 dB | 3 dB |
| PA Shutdown Temperature | 70 C | -- |

**REQ-TTC-LINK-001:** The simulator link budget calculation shall use the free-space
path loss model: `FSPL = 20*log10(d_m) + 20*log10(f_Hz) - 147.55`, consistent with
the current `ttc_basic.py` implementation.

**REQ-TTC-LINK-002:** Eb/N0 shall be computed from received power, system noise
temperature, data rate, and coding gain. BER shall be derived from Eb/N0 using the
BPSK/QPSK erfc approximation.

**REQ-TTC-LINK-003:** Link margin shall be defined as `Eb/N0 - 12.0 dB` (the 12 dB
reference includes the required Eb/N0 plus implementation losses).

---

## 4. Telemetry Parameters

### 4.1 TTC Housekeeping (HK SID 6, 8 s interval)

| Parameter | ID | Units | Description |
|---|---|---|---|
| ttc.mode | 0x0500 | enum | Active transponder (0=primary, 1=redundant) |
| ttc.link_status | 0x0501 | enum | Link state (0=no link, 1=locked) |
| ttc.rssi | 0x0502 | dBm | Received signal strength indicator |
| ttc.link_margin | 0x0503 | dB | Current link margin |
| ttc.tm_data_rate | 0x0506 | bps | Current TM downlink data rate |
| ttc.xpdr_temp | 0x0507 | C | Active transponder temperature |
| ttc.ranging_status | 0x0508 | enum | Ranging active flag |
| ttc.range_km | 0x0509 | km | Slant range to ground station |
| ttc.contact_elevation | 0x050A | deg | Ground station elevation angle |
| ttc.contact_az | 0x050B | deg | Ground station azimuth angle |
| ttc.ber | 0x050C | log10 | Bit error rate (log10 scale) |
| ttc.tx_fwd_power | 0x050D | W | Transmit forward power |
| ttc.pa_temp | 0x050F | C | Power amplifier temperature |
| ttc.carrier_lock | 0x0510 | enum | Carrier lock (0=no, 1=yes) |
| ttc.bit_sync | 0x0511 | enum | Bit synchronisation (0=no, 1=yes) |
| ttc.frame_sync | 0x0512 | enum | Frame synchronisation (0=no, 1=yes) |
| ttc.cmd_rx_count | 0x0513 | count | Total received command counter |
| ttc.pa_on | 0x0516 | enum | PA status (0=off, 1=on) |
| ttc.eb_n0 | 0x0519 | dB | Energy per bit to noise density |

### 4.2 Phase 4 -- Flight Hardware Realism Parameters

| Parameter | ID | Units | Description |
|---|---|---|---|
| ttc.agc_level | 0x051A | dB | Automatic gain control level |
| ttc.doppler_hz | 0x051B | Hz | Doppler shift on downlink |
| ttc.range_rate | 0x051C | m/s | Range rate to ground station |
| ttc.cmd_auth_status | 0x051D | enum | Command authentication status |
| ttc.total_bytes_tx | 0x051E | B | Total bytes transmitted this pass |
| ttc.total_bytes_rx | 0x051F | B | Total bytes received this pass |

### 4.3 Proposed New Parameters

**REQ-TTC-TM-001:** The following additional parameters are required to support the
PDM channel, burn wire deployment, and beacon features:

| Parameter | Proposed ID | Units | Description |
|---|---|---|---|
| ttc.pdm_timer_active | 0x0520 | enum | PDM 15-min TX timer active (0/1) |
| ttc.pdm_timer_remaining | 0x0521 | s | PDM timer seconds remaining |
| ttc.antenna_deploy_status | 0x0522 | enum | 0=stowed, 1=deploying, 2=deployed, 3=failed |
| ttc.burn_wire_armed | 0x0523 | enum | Burn wire arm status (0=safe, 1=armed) |
| ttc.beacon_mode | 0x0524 | enum | Beacon mode active (0=no, 1=yes) |
| ttc.gs_id | 0x0525 | enum | Currently tracked ground station ID |

### 4.4 Limit Monitoring

| Parameter | Yellow Low | Yellow High | Red Low | Red High |
|---|---|---|---|---|
| ttc.ber (log10) | -- | -5.0 | -- | -4.0 |
| ttc.pa_temp | 0.0 C | 55.0 C | -10.0 C | 65.0 C |
| ttc.xpdr_temp | 0.0 C | 50.0 C | -10.0 C | 60.0 C |
| ttc.agc_level | -80.0 dB | -- | -100.0 dB | -20.0 dB |
| ttc.link_margin | 3.0 dB | -- | 1.0 dB | -- |

---

## 5. Commands and PUS Services

### 5.1 TTC Function Commands (PUS Service 8)

| Command | func_id | Description | Parameters |
|---|---|---|---|
| TTC_SWITCH_PRIMARY | 50 | Activate primary transponder | (none) |
| TTC_SWITCH_REDUNDANT | 51 | Activate redundant transponder | (none) |
| TTC_SET_DATA_RATE | 52 | Set TM data rate | rate: 0=low (1 kbps), 1=high (64 kbps) |
| TTC_PA_ON | 53 | Enable power amplifier | (none) |
| TTC_PA_OFF | 54 | Disable power amplifier | (none) |
| TTC_SET_TX_POWER | 55 | Set transmit power level | level: 0=low (1 W), 1=nom (2 W), 2=high (5 W) |

### 5.2 Proposed New Commands

**REQ-TTC-CMD-001:** The following additional commands are required:

| Command | Proposed func_id | Description | Parameters |
|---|---|---|---|
| TTC_DEPLOY_ANTENNA_ARM | 56 | Arm burn wire deployment | (none) |
| TTC_DEPLOY_ANTENNA_FIRE | 57 | Fire burn wire (must be armed) | (none) |
| TTC_DEPLOY_ANTENNA_DISARM | 58 | Disarm burn wire (cancel arm) | (none) |
| TTC_PDM_RESET | 59 | Reset PDM timer (test/commissioning only) | (none) |

### 5.3 PUS Service Usage

| PUS Service | Usage by TTC Position |
|---|---|
| S1 (Verification) | Receive TC acceptance/execution reports |
| S3 (Housekeeping) | Request TTC HK (SID 6), enable/disable/set interval |
| S5 (Events) | Enable/disable TTC event reports |
| S8 (Function Mgmt) | Execute TTC function commands (func_ids 50-59) |
| S9 (Time Mgmt) | Time sync support |
| S11 (Scheduling) | Schedule time-tagged commands for automated pass operations |
| S15 (Onboard Storage) | Manage onboard TM stores (HK, Event, Science, Alarm) |
| S17 (Test) | Connection test (echo) for link verification |
| S20 (Param Mgmt) | Read/write individual TTC parameters |

**REQ-TTC-PUS-001:** All TTC commands shall generate S1 verification reports (acceptance,
start, completion) to confirm command processing.

**REQ-TTC-PUS-002:** The TTC position shall have access to PUS services
{1, 3, 5, 8, 9, 11, 15, 17, 20} as defined in the position configuration.

---

## 6. Operational Procedures

### 6.1 LEOP RF Sequence

The LEOP RF sequence is the most critical phase for the TTC position. It defines the
first contact with the spacecraft after separation from the launch vehicle.

**REQ-TTC-LEOP-001:** The LEOP RF sequence shall follow this timeline:

| Phase | Time (T+min) | Action | Expected Telemetry |
|---|---|---|---|
| Pre-AOS | T-30 | Verify GS antenna calibration, configure receiver | -- |
| Pre-AOS | T-15 | Arm uplink transmitter, load Doppler predictions | -- |
| Pre-AOS | T-5 | Begin antenna autotrack sweep on predicted ephemeris | -- |
| AOS | T+0 | Predicted AOS; GS receiver searches for carrier | -- |
| Carrier Detect | T+0 to T+2 | Carrier lock expected within 2 s of AOS | carrier_lock = 1 |
| Bit Sync | T+2 to T+5 | Bit synchronisation achieved | bit_sync = 1 |
| Frame Sync | T+5 to T+10 | Frame synchronisation achieved | frame_sync = 1 |
| Beacon Rx | T+10 to T+26 | Receive bootloader beacon packet (SID 10) | HK data |
| First TC | T+12 | Send HK_REQUEST (SID 1) -- first uplink command | EPS HK response |
| Health Check | T+15 | Assess spacecraft health from first HK | All HK nominal |

**REQ-TTC-LEOP-002:** If no carrier lock is detected within 60 s of predicted AOS, the
operator shall:
1. Verify TLE accuracy and Doppler prediction
2. Widen receiver bandwidth
3. Check alternate antenna polarisation
4. Wait for next pass before declaring anomaly

**REQ-TTC-LEOP-003:** During LEOP, the spacecraft transmits at low rate (1 kbps) with
antennas in stowed configuration. The link budget shall be evaluated for this worst-case
geometry.

**REQ-TTC-LEOP-004:** LEOP first contact shall be planned for the ground station pass
with the highest maximum elevation to maximise link margin during initial acquisition.

### 6.2 Antenna Deployment Procedure

**REQ-TTC-ANT-001:** Antenna deployment shall be performed as a dedicated procedure
during LEOP, after the first health check confirms:
- `eps.bus_voltage` > 27.0 V
- `eps.bat_soc` > 50%
- `obdh.sw_image` = 1 (application running)
- `aocs.rate_roll`, `rate_pitch`, `rate_yaw` < 1.0 deg/s (spacecraft stable)
- Sufficient contact time remaining (> 5 min)

**REQ-TTC-ANT-002:** The antenna deployment sequence shall be:

| Step | Command | Verification | Timeout |
|---|---|---|---|
| 1 | Record pre-deployment RSSI baseline | `ttc.rssi` recorded | -- |
| 2 | TTC_DEPLOY_ANTENNA_ARM (func_id 56) | `ttc.burn_wire_armed` = 1 | 5 s |
| 3 | TTC_DEPLOY_ANTENNA_FIRE (func_id 57) | `ttc.antenna_deploy_status` = 1 | 10 s |
| 4 | Wait for deployment | `ttc.antenna_deploy_status` = 2 | 30 s |
| 5 | Verify RSSI improvement | `ttc.rssi` improved by > 5 dB | 15 s |
| 6 | Record post-deployment RSSI | `ttc.rssi` recorded | -- |
| 7 | Switch to high data rate | TTC_SET_DATA_RATE(rate=1) | 10 s |

**REQ-TTC-ANT-003:** If deployment fails (`ttc.antenna_deploy_status` = 3 or no RSSI
improvement), the operator shall:
1. Remain at low data rate
2. Attempt a second fire command on the next pass
3. If still failed, evaluate whether low-rate operations are sustainable for the mission

### 6.3 Nominal Link Management

**REQ-TTC-NOM-001:** At each AOS, the TTC operator shall verify the lock acquisition
sequence (carrier_lock -> bit_sync -> frame_sync) and confirm link margin before
declaring GO for pass operations.

**REQ-TTC-NOM-002:** Data rate transitions shall follow the procedure in
PROC-TTC-NOM-001 (Data Rate Change):
- High rate requires: `ttc.link_margin` > 3.0 dB, `ttc.eb_n0` > 10 dB,
  `ttc.contact_elevation` > 10 deg
- Low rate: no minimum margin required

**REQ-TTC-NOM-003:** The TTC operator shall provide GO/NO-GO at:
- Pass startup: carrier_lock=1, bit_sync=1, frame_sync=1, RSSI and link_margin nominal,
  BER < 1e-5
- Data downlink session: link stable at high rate, PA on, TX power nominal, margin > 3 dB
- Commanding authorization: uplink healthy, cmd_rx_count incrementing
- End of pass: report total_bytes_tx/rx, store status, next contact window

**REQ-TTC-NOM-004:** Per-pass statistics shall be logged:
- Total bytes transmitted and received
- Average and minimum link margin
- Maximum BER
- Pass duration (AOS to LOS)
- Ground station used
- Any anomalies or frame sync losses

### 6.4 Transponder Switchover

**REQ-TTC-SW-001:** Transponder switchover shall cause a link outage of 20-40 seconds.
The operator shall ensure at least 3 minutes of pass time remain before commanding a
switchover.

**REQ-TTC-SW-002:** After switchover, the operator shall verify:
- `ttc.link_status` = 1 within 60 s
- `ttc.rssi` > -100 dBm
- `ttc.link_margin` > 3.0 dB

### 6.5 Contingency and Emergency Procedures

**REQ-TTC-CTG-001:** Link loss recovery (CTG-003) shall follow the defined escalation:
1. Confirm ground station is nominal
2. Attempt re-acquisition on primary transponder (blind HK request)
3. Switch to redundant transponder (blind command)
4. Retry primary transponder
5. If unrecovered, escalate to EMG-004

**REQ-TTC-CTG-002:** BER anomaly investigation (CTG-014) shall systematically check:
1. Link geometry (elevation effect)
2. PA temperature and health
3. Data rate reduction
4. Transponder switchover

**REQ-TTC-EMG-001:** Loss of communication (EMG-004) escalation thresholds:
- 0-6h: Cycle all ground stations, blind command both transponders
- 6-12h: Wait for onboard autonomous recovery timer
- 12-24h: Polar pass focused campaign
- 24-48h: Emergency network escalation
- >48h: Extended loss assessment, 30-day listen campaign

---

## 7. FDIR Rules (TTC-Related)

| Rule ID | Condition | Response |
|---|---|---|
| TTC-01 | No link for > 24 hours | Autonomous switch to redundant XPDR |
| TTC-02 | XPDR temp out of range (0-50 C) | Switch transponder |

**REQ-TTC-FDIR-001:** The simulator shall implement the 24-hour no-contact autonomous
transponder switch as an onboard FDIR action.

**REQ-TTC-FDIR-002:** The 6-hour autonomous communication recovery sequence (referenced
in EMG-004) shall be modelled, including transponder cycling and beacon power increase.

**REQ-TTC-FDIR-003:** PA auto-shutdown at 70 C and re-enable at 55 C (15 C hysteresis)
shall be modelled as an autonomous hardware protection (already implemented in
`ttc_basic.py`).

---

## 8. Training Scenarios

### 8.1 LEOP Scenarios

| Scenario ID | Name | Description | Faults Injected |
|---|---|---|---|
| TRN-TTC-001 | Nominal First AOS | Standard first acquisition, health check, antenna deploy | None |
| TRN-TTC-002 | Delayed AOS | Carrier not found at predicted time; operator must troubleshoot | TLE offset, Doppler error |
| TRN-TTC-003 | Bootloader Beacon Only | Spacecraft stuck in bootloader; only beacon packets received | OBC boot failure |
| TRN-TTC-004 | Antenna Deploy Failure | Burn wire fails on first attempt | Antenna deployment failure |
| TRN-TTC-005 | Low Battery LEOP | Spacecraft has low SoC after extended tumble | Low battery, antenna stowed |

### 8.2 Nominal Operations Scenarios

| Scenario ID | Name | Description | Faults Injected |
|---|---|---|---|
| TRN-TTC-010 | Standard Data Pass | Nominal AOS, rate change to high, downlink, LOS | None |
| TRN-TTC-011 | Rate Margin Limited | High rate not achievable due to low elevation | Low max elevation pass |
| TRN-TTC-012 | PA Thermal Management | PA temperature rising during extended pass | PA heat injection |
| TRN-TTC-013 | Multi-Pass Downlink | Science data requires two consecutive passes to complete | Large data volume |

### 8.3 Contingency Scenarios

| Scenario ID | Name | Description | Faults Injected |
|---|---|---|---|
| TRN-TTC-020 | Primary Transponder Failure | Primary fails during pass; switchover to redundant | primary_failure |
| TRN-TTC-021 | BER Degradation | Gradual BER increase during pass | high_ber (offset) |
| TRN-TTC-022 | Uplink Loss | Receive path fails; telemetry continues but no commanding | uplink_loss |
| TRN-TTC-023 | Receiver Degradation | Noise figure increase causes gradual SNR loss | receiver_degrade |
| TRN-TTC-024 | PA Overheat and Shutdown | PA auto-shuts down; must wait for cooldown | pa_overheat |
| TRN-TTC-025 | Ground Station Failure | Primary GS goes offline mid-campaign | GS antenna failure |

### 8.4 Emergency Scenarios

| Scenario ID | Name | Description | Faults Injected |
|---|---|---|---|
| TRN-TTC-030 | Total Link Loss | No telemetry on either transponder; blind commanding | Both XPDRs intermittent |
| TRN-TTC-031 | Single Station Emergency | Only one GS available; extended gap management | GS failure + geometry |
| TRN-TTC-032 | PDM Recovery | OBC crashed; must use PDM channel to recover | OBC hang + normal TC blocked |

### 8.5 Simulator Failure Injection Requirements

**REQ-TTC-FAIL-001:** The following failure modes shall be injectable via the instructor
station (all are currently implemented in `ttc_basic.py`):

| Failure | Injection Parameter | Effect |
|---|---|---|
| primary_failure | `magnitude` (bool) | Primary XPDR inoperative |
| redundant_failure | `magnitude` (bool) | Redundant XPDR inoperative |
| high_ber | `offset` (dB) | Eb/N0 reduction, BER increase |
| pa_overheat | `heat_w` (W) | Extra PA heat, triggers auto-shutdown |
| uplink_loss | (bool) | Receiver stops processing commands |
| receiver_degrade | `nf_db` (dB) | Noise figure increase |

**REQ-TTC-FAIL-002:** The following additional failures shall be supported:

| Failure | Effect |
|---|---|
| antenna_deploy_failure | Burn wire does not fire; antenna remains stowed |
| partial_antenna_deploy | One element deployed, degraded gain pattern |
| gs_antenna_failure | Ground station tracking failure (per station) |
| gs_lna_degrade | Ground station G/T reduction (per station) |
| gs_tx_failure | Ground station uplink transmitter failure |
| pdm_decoder_failure | PDM channel inoperative |

---

## 9. MCS Display and Tool Requirements

### 9.1 TTC Tab -- Link Status Display

**REQ-TTC-MCS-001:** The TTC tab shall display the following real-time widgets:

**Link Status Panel:**
- Link active indicator (LED with LOCKED/UNLOCKED state)
- Carrier lock, bit sync, frame sync indicators (three-stage lock acquisition display)
- RSSI gauge (-120 to -60 dBm range, with -100 dBm yellow and -110 dBm red thresholds)
- Link margin gauge (-5 to +20 dB, with 3 dB yellow and 1 dB red thresholds)
- Eb/N0 gauge (0 to 30 dB, with 10 dB yellow threshold)
- BER gauge (log10 scale, -12 to -1, with -5 yellow and -4 red thresholds)

**PA and Hardware Panel:**
- PA status indicator (ON/OFF/OVERHEAT)
- PA temperature gauge (20-70 C, with 55 C yellow and 65 C red thresholds)
- TX forward power value
- Transponder mode indicator (PRIMARY / REDUNDANT)
- Transponder temperature value
- Data rate indicator (1 kbps / 64 kbps)
- Command received counter

**Ranging and Tracking Panel:**
- Range (km)
- Elevation angle (deg) with azimuth
- AGC level (dB)
- Doppler shift (Hz)
- Range rate (m/s)

**Pass Statistics Panel:**
- Total bytes TX / RX this pass
- Command authentication status

### 9.2 Link Budget Display

**REQ-TTC-MCS-010:** The MCS shall provide a real-time link budget display showing:

- Computed vs measured RSSI comparison
- Free space path loss (computed from range)
- Expected link margin at current geometry
- Stowed vs deployed antenna gain assumption
- Ground station G/T for the active station
- Per-station link budget history

**REQ-TTC-MCS-011:** The link budget display shall include an RSSI vs elevation scatter
plot showing measured values overlaid on the predicted link budget curve, to identify
systematic gain pattern deviations.

### 9.3 Ground Station Status Display

**REQ-TTC-MCS-020:** The MCS Overview tab (or a dedicated GS panel on the TTC tab) shall
display ground station status:

| Field | Description |
|---|---|
| Station Name | Iqaluit / Troll |
| Status | IDLE / PRE-PASS / TRACKING / POST-PASS / FAILED |
| Antenna Az/El | Current antenna pointing |
| Next AOS/LOS | Predicted next contact window times |
| Max Elevation | Maximum elevation for next pass |
| Last Contact | Timestamp of last successful contact |
| Health | NOMINAL / DEGRADED / FAILED |

**REQ-TTC-MCS-021:** The world map on the Overview tab shall display:
- Ground station locations with status icons
- Current ground track with +/- 50 min prediction
- Active contact footprint circles (visibility circles at min elevation)
- Ground station-to-spacecraft line during contact

### 9.4 Signal Quality Trend Charts

**REQ-TTC-MCS-030:** The TTC tab shall include Chart.js time-series charts:

| Chart | Parameters | Duration |
|---|---|---|
| Signal Quality | RSSI + link_margin (dual axis) | 10 min rolling |
| BER | BER (log10 scale) | 10 min rolling |
| PA Temperature | pa_temp + xpdr_temp | 10 min rolling |
| Doppler | doppler_hz | 10 min rolling (full pass) |

### 9.5 Antenna Deployment Status

**REQ-TTC-MCS-040:** The MCS shall display a clear antenna deployment state indicator:
- STOWED (yellow warning): antennas not yet deployed
- DEPLOYING (flashing): deployment in progress
- DEPLOYED (green): antennas successfully deployed
- FAILED (red): deployment failed, low-rate only

### 9.6 PDM Channel Status

**REQ-TTC-MCS-050:** When the PDM 15-minute timer is active, the MCS shall display:
- Timer countdown (mm:ss)
- PDM decode status
- TX+PA enabled via PDM indicator

---

## 10. Planner Requirements

### 10.1 Contact Window Planning

**REQ-TTC-PLN-001:** The contact planner (`ContactPlanner` class in
`smo-planner/contact_planner.py`) shall compute visibility windows for all configured
ground stations using the orbit propagator.

**REQ-TTC-PLN-002:** For each contact window, the planner shall compute:
- AOS time (acquisition of signal at min elevation)
- LOS time (loss of signal at min elevation)
- Maximum elevation during the pass
- Pass duration
- Ground station identity

**REQ-TTC-PLN-003:** The planner shall support planning horizons of at least 72 hours
to enable multi-day operations scheduling.

**REQ-TTC-PLN-004:** With the Iqaluit + Troll two-station configuration, the planner
shall compute the expected number of contacts per day and cumulative contact time. For
a 500 km SSO at 97.4 deg inclination:
- Iqaluit (63.7N): Expected 3-5 passes/day
- Troll (72.0S): Expected 3-5 passes/day
- Total: Expected 6-10 passes/day, 40-80 minutes cumulative contact

**REQ-TTC-PLN-005:** The planner shall identify and flag contact gaps exceeding 4 hours,
as these approach the 6-hour autonomous recovery timer threshold.

### 10.2 Pass Scheduling and Prioritisation

**REQ-TTC-PLN-010:** The planner shall support pass prioritisation based on:
- Maximum elevation (higher = better link margin = more data throughput)
- Contact duration (longer passes preferred for data downlink)
- Ground station availability (accounting for maintenance windows)
- Data backlog urgency

**REQ-TTC-PLN-011:** The planner shall compute estimated data volume transferable per
pass based on:
- Pass duration at each rate threshold
- Time above 10 deg elevation (high-rate window)
- Time between 5-10 deg (low-rate only window)
- Lock acquisition overhead (10 s at start of pass)

### 10.3 Conflict and Gap Analysis

**REQ-TTC-PLN-020:** The planner shall flag conflicts where:
- Two ground stations have overlapping visibility (handover opportunity or conflict)
- A scheduled imaging activity overlaps with a contact window
- Orbit maintenance manoeuvres are scheduled during contact windows

**REQ-TTC-PLN-021:** The planner shall provide a 24-hour timeline view showing all
contact windows, gaps, and scheduled activities.

### 10.4 Ground Station Configuration for Planner

**REQ-TTC-PLN-030:** The ground station configuration for the planner shall be:

```yaml
ground_stations:
  - name: Iqaluit
    lat_deg: 63.747
    lon_deg: -68.518
    alt_km: 0.03
    min_elevation_deg: 5.0
    antenna_diameter_m: 7.3
    band: S
  - name: Troll
    lat_deg: -72.012
    lon_deg: 2.535
    alt_km: 1.27
    min_elevation_deg: 5.0
    antenna_diameter_m: 7.3
    band: S
```

---

## 11. Simulator Fidelity Requirements

### 11.1 Link Budget Model (Currently Implemented)

The existing `TTCBasicModel` in `ttc_basic.py` provides:

| Feature | Status | Fidelity Level |
|---|---|---|
| Free-space path loss | Implemented | High |
| BER from Eb/N0 (BPSK/QPSK erfc) | Implemented | Medium |
| PA thermal model with auto-shutdown | Implemented | Medium |
| Lock acquisition sequence (carrier -> bit -> frame) | Implemented | Medium |
| Dual transponder cold redundancy | Implemented | High |
| AGC, Doppler, range rate | Implemented | Medium |
| Command receive counter | Implemented | High |
| Pass byte counters | Implemented | High |

### 11.2 Required Enhancements

**REQ-TTC-SIM-001:** Antenna deployment model:
- Stowed antenna gain penalty (-6 to -10 dB)
- Deployment transient (current draw, status transitions)
- Post-deployment gain restoration

**REQ-TTC-SIM-002:** PDM command channel model:
- Independent decode path from main TC
- 15-minute timer with countdown
- Automatic TX+PA enable/disable

**REQ-TTC-SIM-003:** Beacon mode model:
- Autonomous beacon transmission in bootloader
- SID 10 structure at 16 s interval
- Low rate (1 kbps) fixed

**REQ-TTC-SIM-004:** Ground station antenna failure model:
- Per-station injectable failures
- G/T degradation model
- Tracking failure (loss of autotrack)
- Independent uplink/downlink failure

**REQ-TTC-SIM-005:** Atmospheric effects:
- Rain attenuation model (significant at S-band in polar regions)
- Tropospheric scintillation at low elevation angles (< 10 deg)
- Ionospheric Faraday rotation (minimal at S-band but included for completeness)

**REQ-TTC-SIM-006:** Lock acquisition timing shall include random jitter:
- Carrier lock: 1-3 s (currently fixed at 2 s)
- Bit sync: 3-7 s cumulative (currently fixed at 5 s)
- Frame sync: 8-15 s cumulative (currently fixed at 10 s)

**REQ-TTC-SIM-007:** The Doppler model shall use actual range rate computation from
orbit geometry rather than the current elevation-based approximation. The current model
(`ttc_basic.py` lines 230-243) uses a simplified cos(elevation) approximation that does
not correctly model the approaching/receding sign change at the point of closest approach.

**REQ-TTC-SIM-008:** RF interference model:
- Random transient interference events (ground-based RFI)
- Injectable continuous interference at configurable power level
- Effect on BER and lock status

### 11.3 Existing Failure Modes (Already Implemented)

| Failure | Method | Parameters |
|---|---|---|
| Primary transponder failure | `inject_failure("primary_failure")` | magnitude (bool) |
| Redundant transponder failure | `inject_failure("redundant_failure")` | magnitude (bool) |
| High BER | `inject_failure("high_ber")` | offset (dB Eb/N0 reduction) |
| PA overheat | `inject_failure("pa_overheat")` | heat_w (W extra heat) |
| Uplink loss | `inject_failure("uplink_loss")` | (bool) |
| Receiver degradation | `inject_failure("receiver_degrade")` | nf_db (dB NF increase) |

### 11.4 Clock and Timing

**REQ-TTC-SIM-010:** The simulator tick rate shall be configurable. The TTC model must
behave correctly at tick intervals from 0.1 s to 10 s without numerical instability in
the PA thermal model (currently using time constant tau = 60 s, which is stable for dt
up to approximately 30 s).

---

## 12. Orbit and Coverage Analysis

### 12.1 Orbit Parameters

| Parameter | Value | Source |
|---|---|---|
| Altitude | 500 km | `orbit.yaml` |
| Inclination | 97.4 deg | `orbit.yaml` (SSO) |
| Orbital Period | ~95 min | Derived from altitude |
| Revolutions/Day | ~15.15 | `orbit.yaml` TLE |
| Earth Radius | 6371 km | `orbit.yaml` |

### 12.2 Two-Station Coverage (Iqaluit + Troll)

With Iqaluit at 63.7N and Troll at 72.0S, the station network provides:
- Full polar coverage: every ascending/descending node is visible from at least one station
- Hemisphere balance: approximately equal contact opportunities north and south
- Maximum gap: approximately 3-4 hours between contacts (worst-case geometry)
- Daily contacts: 6-10 passes total across both stations

**REQ-TTC-COV-001:** The operations team shall be aware that the two-station configuration
provides less redundancy than the four-station baseline in the manual. A single-station
failure reduces contact opportunities by approximately 50%.

**REQ-TTC-COV-002:** The planner shall compute and display the worst-case gap duration
for the 24-hour planning horizon and alert if it exceeds 6 hours.

---

## 13. Interface Requirements

### 13.1 TTC to EPS Interface

| Interface | Parameter | Description |
|---|---|---|
| TTC RX power line | `eps.pl_ttc_rx` (0x0111) | Non-switchable; always on |
| TTC TX power line | `eps.pl_ttc_tx` (0x0112) | Switchable; controlled by EPS/FDIR |
| TTC RX current | `eps.line_current_1` (0x0119) | Receiver current draw |
| TTC TX current | `eps.line_current_2` (0x011A) | Transmitter + PA current draw |

**REQ-TTC-IF-001:** The TTC RX path shall be non-switchable (always powered) to ensure
the spacecraft can always receive commands, including PDM commands.

**REQ-TTC-IF-002:** The TTC TX path may be shed by EPS during under-voltage conditions
but shall be restored when voltage recovers above the safe threshold.

### 13.2 TTC to OBDH Interface

| Interface | Description |
|---|---|
| TC packet routing | Received TCs forwarded to OBC for processing |
| TM packet routing | OBC-generated TM packets queued for downlink |
| Beacon generation | Bootloader generates beacon packets autonomously |
| PDM bypass | PDM commands bypass OBC TC processing chain |

### 13.3 TTC to AOCS Interface

| Interface | Description |
|---|---|
| Ranging data | Range and range rate used for orbit determination |
| Doppler data | Doppler measurements for flight dynamics |
| Attitude dependency | Antenna gain pattern depends on S/C attitude |

---

## 14. Configuration File Changes Required

### 14.1 Ground Station Configuration

**REQ-TTC-CFG-001:** Update `configs/eosat1/planning/ground_stations.yaml` and
`configs/eosat1/orbit.yaml` to reflect the Iqaluit + Troll station network:

Replace the existing four-station configuration with:

```yaml
ground_stations:
  - name: Iqaluit
    lat_deg: 63.747
    lon_deg: -68.518
    alt_km: 0.03
    min_elevation_deg: 5.0
    antenna_diameter_m: 7.3
    band: S
  - name: Troll
    lat_deg: -72.012
    lon_deg: 2.535
    alt_km: 1.27
    min_elevation_deg: 5.0
    antenna_diameter_m: 7.3
    band: S
```

### 14.2 TTC Subsystem Configuration

**REQ-TTC-CFG-002:** Update `configs/eosat1/subsystems/ttc.yaml` to include additional
configuration parameters for PDM, antenna deployment, and beacon:

```yaml
# PDM Command Channel
pdm_enabled: true
pdm_timer_duration_s: 900  # 15 minutes

# Antenna Deployment
antenna_deployed: false  # Initial state at launch
antenna_stowed_gain_penalty_db: -8.0
burn_wire_current_a: 2.0
burn_wire_duration_s: 5.0

# Beacon
beacon_rate_bps: 1000
beacon_interval_s: 16.0
```

### 14.3 New Telemetry Parameters

**REQ-TTC-CFG-003:** Add the proposed new parameters (Section 4.3) to
`configs/eosat1/telemetry/parameters.yaml`.

### 14.4 HK Structure Update

**REQ-TTC-CFG-004:** Add a new HK SID or extend SID 6 to include PDM and antenna
deployment parameters.

---

## 15. Known Issues and Discrepancies

### 15.1 Codebase Issues

| Issue | Location | Description |
|---|---|---|
| SID 6 references 0x0508 | `hk_structures.yaml` line 188 | Param 0x0508 (ranging_status) not in `parameters.yaml` -- known xfail |
| EIRP inconsistency | `ttc.yaml` vs `05_ttc.md` | Config says 10 dBW; manual says 33 dBW |
| Manual GS list | `05_ttc.md` Section 5 | Lists Svalbard, Troll, Inuvik, O'Higgins; mission profile specifies Iqaluit + Troll |
| Manual data rates | `05_ttc.md` Section 2.1 | Says 128 kbps nominal downlink; config says 64 kbps |
| Manual uplink freq | `05_ttc.md` Section 2.1 | Says 2.2 GHz uplink; config says 2025.5 MHz |
| PA max power | `05_ttc.md` Section 2.1 vs role analysis | Manual says 2 W; role analysis says 8 W high; model says 5 W max |

**REQ-TTC-DOC-001:** The manual (`05_ttc.md`) shall be updated to align with the
configuration values in `ttc.yaml` and the mission profile ground station network.

### 15.2 Model Limitations

| Limitation | Impact | Priority |
|---|---|---|
| No antenna deployment model | Cannot train LEOP antenna deploy | High |
| No PDM channel model | Cannot train last-resort recovery | High |
| No beacon mode model | Cannot train bootloader-only scenarios | Medium |
| No GS antenna failure model | Cannot train single-station scenarios | Medium |
| Simplified Doppler model | Incorrect sign change at TCA | Low |
| No atmospheric attenuation | Slightly optimistic link margins | Low |
| Fixed lock acquisition timing | Unrealistically repeatable | Low |

---

## 16. Traceability Matrix

| Requirement | Category | Procedure Reference | Priority |
|---|---|---|---|
| REQ-TTC-EQP-001 to 004 | Equipment | All | Must Have |
| REQ-TTC-PDM-001 to 006 | PDM Channel | EMG-004, TRN-TTC-032 | Must Have |
| REQ-TTC-BWD-001 to 006 | Antenna Deployment | LEOP-001, TRN-TTC-001/004 | Must Have |
| REQ-TTC-BCN-001 to 004 | Beacon | CTG-018, TRN-TTC-003 | Should Have |
| REQ-TTC-LR-001 to 004 | Low Rate Ops | LEOP, pre-deployment | Must Have |
| REQ-TTC-GS-IQ-001 to 002 | Iqaluit Station | All | Must Have |
| REQ-TTC-GS-TR-001 | Troll Station | All | Must Have |
| REQ-TTC-GSF-001 to 005 | GS Failure Model | TRN-TTC-025/031 | Should Have |
| REQ-TTC-LEOP-001 to 004 | LEOP RF Sequence | LEOP-001 | Must Have |
| REQ-TTC-ANT-001 to 003 | Antenna Deploy Proc | LEOP, COM-006 | Must Have |
| REQ-TTC-NOM-001 to 004 | Nominal Ops | NOM-001/003/011 | Must Have |
| REQ-TTC-MCS-001 to 050 | MCS Displays | All operations | Should Have |
| REQ-TTC-PLN-001 to 030 | Planner | Contact planning | Must Have |
| REQ-TTC-SIM-001 to 010 | Simulator Fidelity | Training scenarios | Mixed |
| REQ-TTC-FAIL-001 to 002 | Failure Injection | Training | Should Have |
| REQ-TTC-CFG-001 to 004 | Configuration | Setup | Must Have |

---

*AIG -- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

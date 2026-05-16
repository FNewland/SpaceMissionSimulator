# EOSAT-1 RF Simulation Layer

**Document ID:** EOSAT1-UM-RFS-011
**Issue:** 1.0
**Date:** 2026-04-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Purpose

This document describes the RF Simulation Bridge (smo-rfsim), an optional layer that inserts
CCSDS Transfer Framing and RF channel simulation between the spacecraft simulator and the
Mission Control System (MCS). It enables realistic ground-segment training scenarios including
frame synchronization, error correction, and link budget exercises.

## 2. Operating Modes

The bridge supports three operating modes, selectable via configuration or environment variable:

| Mode   | Framing          | RF              | Dependencies        | Latency |
|--------|------------------|-----------------|---------------------|---------|
| PACKET | No               | No              | None (default)      | ~0 ms   |
| FRAME  | CCSDS TF + CLTU  | BER bit-flip    | Pure Python only    | <5 ms   |
| RF     | CCSDS TF + CLTU  | BPSK (GR/numpy) | gnuradio (optional) | ~50 ms  |

### 2.1 PACKET Mode

Transparent TCP relay. The bridge simply forwards length-prefixed ECSS packets between the
simulator and MCS with no processing. This is the default mode and matches the system's
original behaviour.

### 2.2 FRAME Mode

Applies the full CCSDS Transfer Framing protocol:

- **Downlink (TM):** ECSS packets are packed into fixed-length TM Transfer Frames
  (CCSDS 132.0-B-3), with an Attached Sync Marker (ASM), Frame Error Control Field (FECF),
  and virtual channel multiplexing. Frames pass through a channel model that injects bit
  errors at a rate determined by the Eb/N0 setting. The receiver correlates the ASM,
  validates the FECF, and extracts packets.

- **Uplink (TC):** TC packets are encoded as Communications Link Transmission Units (CLTUs)
  per CCSDS 232.0-B-4, using BCH(64,56) code blocks. CLTUs pass through the channel model
  and are decoded with single-bit error correction.

### 2.3 RF Mode

Adds BPSK modulation/demodulation to the FRAME mode chain. When GNU Radio is installed,
real signal processing flowgraphs handle modulation, root-raised-cosine pulse shaping,
AGC, Costas loop carrier recovery, and M&M clock recovery. When GNU Radio is not available,
a numpy-based channel simulator provides AWGN noise injection and Doppler frequency offset.

## 3. Architecture

```
                    PACKET mode (bypass)
Simulator ──TCP:8002────────────────────────────── MCS
  TM:8002                                        connects to
  TC:8001           FRAME / RF mode                8012/8011
     │        ┌────────────────────────────┐
     └───────>│     smo-rfsim Bridge       │──TCP:8012──> MCS
              │                            │
  TC:8001<────│  TMFrameBuilder            │<──TCP:8011── MCS
              │  VCMultiplexer             │
              │  [RS Encoder]              │
              │  [Conv Encoder]            │    ┌──────────┐
              │  ChannelModel              │    │ Radio   │
              │  [Viterbi Decoder]         │───>│ Web UI   │
              │  [RS Decoder]              │    │ :8094    │
              │  FrameSynchronizer         │    └──────────┘
              │  TMFrameParser             │
              │  CLTUEncoder/Decoder       │
              └────────────────────────────┘
                Polls sim WS :8080 for
                Eb/N0, Doppler, range
```

The MCS requires no code changes — it connects to ports 8012/8011 instead of 8002/8001.

## 4. CCSDS Transfer Frame Details

### 4.1 TM Downlink Frames

- **Frame length:** 1115 bytes (configurable, without ASM)
- **ASM:** 4 bytes (0x1ACFFC1D), prepended to each frame
- **Primary header:** 6 bytes — SCID (10 bits), VCID (3 bits), frame counter, First Header Pointer
- **Data zone:** Variable (frame length - 6 header - 2 FECF)
- **FECF:** 2 bytes, CRC-16/CCITT-FALSE over header + data zone

### 4.2 Virtual Channels

| VCID | Purpose            | Priority |
|------|--------------------|----------|
| 0    | Realtime HK        | Highest  |
| 1    | Stored TM playback | Medium   |
| 7    | Idle fill          | Lowest   |

### 4.3 TC Uplink CLTUs

- **Start sequence:** 2 bytes (0xEB90)
- **Code blocks:** 8 bytes each (7 data + 1 BCH parity), BCH(64,56)
- **Tail sequence:** 8 bytes (0xC5C5C5C5C5C5C579)
- **Error correction:** Single-bit correction per code block

### 4.4 Channel Coding

- **Reed-Solomon:** RS(255,223) over GF(2^8), 16 symbol error correction
- **Convolutional:** Rate 1/2, constraint length K=7 (G1=0x79, G2=0x5B)
- **Viterbi decoder:** Pure-Python add-compare-select trellis decoder (64 states)

## 5. Frame Synchronization

The frame synchronizer implements a three-state acquisition model:

1. **SEARCH:** Scans the byte stream for ASM correlation (up to 3 bit errors tolerated)
2. **VERIFY:** Confirms ASM at expected frame interval (configurable, default 3 frames)
3. **LOCK:** Extracts frames at fixed cadence; flywheel allows up to 4 consecutive misses

## 6. Channel Model

The channel model applies configurable impairments:

| Parameter     | Description              | Default  |
|---------------|--------------------------|----------|
| `eb_n0_db`    | Eb/N0 in dB              | 10.0     |
| `ber_target`  | Target BER               | 1e-6     |
| `path_loss_db`| Free-space path loss     | 150.0 dB |
| `doppler_hz`  | Doppler frequency shift  | 0.0 Hz   |
| `delay_ms`    | Propagation delay        | 3.0 ms   |

The BER is computed from Eb/N0 using the theoretical BPSK formula:
BER = 0.5 × erfc(√(Eb/N0)).

When the bridge is connected to the simulator's WebSocket (port 8080), it reads the TTC
model's link budget parameters and updates the channel model in real time.

## 7. Radio Front-End Display

The Radio web UI (port 8094) displays real-time RF link status:

- **Lock indicators:** Carrier Lock, Bit Sync, Frame Sync (LED: green/yellow/red)
- **RF measurements:** RSSI, Eb/N0, BER, Link Margin with bar meters
- **Dynamics:** Doppler, Range, Data Rate, Propagation Delay
- **Frame counters:** Good/Bad frames, CLTU Sent/Ack, Frame Error Rate bar
- **Virtual channels:** VC0/1/7 activity LEDs
- **History charts:** Eb/N0 and BER time series, frame rate per second

## 8. Configuration

Configuration is loaded from `configs/eosat1/rfsim.yaml`:

```yaml
mode: PACKET   # PACKET | FRAME | RF

ccsds:
  tm_frame_length: 1115
  scid: 1
  fecf_present: true
  rs_enabled: true
  convolutional_enabled: true

channel:
  eb_n0_db: 10.0
  doppler_hz: 0.0
  delay_ms: 3.0

network:
  sim_tm_port: 8002
  sim_tc_port: 8001
  mcs_tm_port: 8012
  mcs_tc_port: 8011
  radio_port: 8094
```

## 9. Startup

### 9.1 Via Environment Variable

```bash
SMO_RF_MODE=FRAME ./start.sh
```

### 9.2 Via CLI

```bash
smo-rfsim --config configs/eosat1/rfsim.yaml --mode FRAME --radio-web
```

### 9.3 CLI Options

| Option          | Description                          |
|-----------------|--------------------------------------|
| `--config`      | Path to rfsim.yaml                   |
| `--mode`        | Override mode (PACKET/FRAME/RF)      |
| `--eb-n0`       | Override Eb/N0 in dB                 |
| `--radio-ui`   | Launch terminal (rich) dashboard     |
| `--radio-web`  | Launch web dashboard on radio_port  |
| `-v`            | Verbose logging                      |

## 10. Training Scenarios

### 10.1 Signal Degradation Exercise

1. Start in FRAME mode with Eb/N0 = 12 dB (clean link)
2. Gradually reduce Eb/N0 to observe increasing BER in Radio display
3. At ~6 dB, observe frame errors beginning to appear (FECF failures)
4. At ~3 dB, observe significant packet loss; MCS displays stale data

### 10.2 Frame Sync Loss and Recovery

1. Start with clean link in LOCK state
2. Inject burst errors (low Eb/N0 for a few seconds) to trigger LOCK → SEARCH
3. Observe Radio frame sync LED transition: green → red → yellow → green
4. Verify MCS recovers and resumes telemetry display

### 10.3 CLTU Uplink Errors

1. Send TC commands through the bridge in FRAME mode
2. Lower Eb/N0 to inject errors into CLTU code blocks
3. Observe BCH single-bit correction succeeding (CLTU Ack increments)
4. Further degrade until CLTU decode fails (Ack stops incrementing)

## 11. Related Documents

| Document                        | Description                    |
|---------------------------------|--------------------------------|
| EOSAT1-UM-MIS-001 (Section 00) | Mission Overview               |
| EOSAT1-UM-TTC-006 (Section 06) | TTC Subsystem                  |
| EOSAT1-UM-CMD-009 (Section 09) | Command Reference              |
| CCSDS 132.0-B-3                 | TM Space Data Link Protocol    |
| CCSDS 232.0-B-4                 | TC Space Data Link Protocol    |
| CCSDS 131.0-B-4                 | TM Synchronization & Coding    |
| CCSDS 401.0-B-32                | Earth Stations and Spacecraft  |

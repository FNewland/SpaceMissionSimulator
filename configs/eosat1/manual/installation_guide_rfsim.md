# EOSAT-1 Startup Guide — RF and Non-RF Modes

**Document ID:** EOSAT1-UM-START-012
**Issue:** 1.0
**Date:** 2026-04-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Quick Reference

| Mode | Command | MCS Ports | RF Sim | Radio UI |
|------|---------|-----------|--------|-----------|
| **No RF** (default) | `./start.sh` | TM:8002, TC:8001 | None | None |
| **FRAME mode** | `SMO_RF_MODE=FRAME ./start.sh` | TM:8012, TC:8011 | CCSDS framing + BER | http://localhost:8094 |
| **RF mode** | `SMO_RF_MODE=RF ./start.sh` | TM:8012, TC:8011 | BPSK mod/demod | http://localhost:8094 |

---

## 2. Starting Without RF Simulation (Default)

This is the standard operating mode. Packets flow directly between the
simulator and MCS over TCP with length-prefix framing.

```bash
cd /path/to/SpaceMissionSimulation
./start.sh
```

**Services started:**

| Service | URL | Port |
|---------|-----|------|
| Simulator | http://localhost:8080 | TC:8001, TM:8002, WS:8080 |
| MCS | http://localhost:9090 | HTTP:9090 |
| Planner | http://localhost:9091 | HTTP:9091 |
| Delayed TM Viewer | http://localhost:8092 | HTTP:8092 |
| Orbit Tools | http://localhost:8093 | HTTP:8093 |

The MCS connects directly to the simulator's TM (8002) and TC (8001) ports.
No framing overhead, no channel simulation. Suitable for:
- Procedure walkthrough and training
- Software development and testing
- Quick demonstrations

---

## 3. Starting With RF Simulation (FRAME Mode)

FRAME mode inserts CCSDS Transfer Framing between the simulator and MCS.
This is the recommended mode for realistic ground segment training.

```bash
SMO_RF_MODE=FRAME ./start.sh
```

**Additional service started:**

| Service | URL | Port |
|---------|-----|------|
| RF Bridge | — | TM out:8012, TC in:8011 |
| Radio Front-End | http://localhost:8094 | HTTP:8094 |

**Data path:**
```
Simulator (TM:8002) → RF Bridge → CCSDS framing → Channel model → MCS (TM:8012)
MCS (TC:8011) → RF Bridge → CLTU encoding → Channel model → Simulator (TC:8001)
```

**What FRAME mode adds:**
- TM Transfer Frames with ASM (0x1ACFFC1D), FECF (CRC-16), virtual channels
- TC CLTUs with BCH(64,56) error correction
- Configurable Eb/N0-based bit error injection
- Frame synchronizer (SEARCH → VERIFY → LOCK state machine)
- Radio web dashboard with constellation diagram, lock indicators, charts

**No additional dependencies** — pure Python only.

---

## 4. Starting With RF Simulation (RF Mode)

RF mode adds BPSK modulation/demodulation on top of FRAME mode.
Falls back to a numpy-based channel model if GNU Radio is not installed.

```bash
SMO_RF_MODE=RF ./start.sh
```

With GNU Radio installed (optional):
```bash
brew install gnuradio   # macOS
SMO_RF_MODE=RF ./start.sh
```

**What RF mode adds beyond FRAME:**
- BPSK modulation with RRC pulse shaping (when GNU Radio available)
- Costas loop carrier recovery + M&M clock recovery
- AWGN + Doppler channel simulation via GNU Radio or numpy
- Convolutional encoding (rate 1/2, K=7) + Viterbi decoding
- Reed-Solomon RS(255,223) encoding + decoding (16-symbol correction)

---

## 5. Standalone RF Bridge (Advanced)

You can start the RF bridge independently of `start.sh`:

```bash
# Start simulator first
smo-simulator --config configs/eosat1/ &

# Start RF bridge
smo-rfsim --config configs/eosat1/rfsim.yaml --mode FRAME --radio-web --eb-n0 12.0

# Start MCS pointing at bridge ports
smo-mcs --config configs/eosat1/ --port 9090 --tm-port 8012 --tc-port 8011 &
```

**CLI options:**

| Option | Description |
|--------|-------------|
| `--config` | Path to rfsim.yaml |
| `--mode PACKET\|FRAME\|RF` | Operating mode |
| `--eb-n0 <dB>` | Override Eb/N0 |
| `--radio-web` | Enable web dashboard (port 8094) |
| `--radio-ui` | Enable terminal dashboard (requires `rich`) |
| `-v` | Verbose logging |

---

## 6. Radio Front-End Dashboard

Open http://localhost:8094 when running in FRAME or RF mode.

### 6.1 Display Panels

| Panel | Shows |
|-------|-------|
| Lock Status | Carrier lock, bit sync, frame sync LEDs; signal quality bar |
| RF Measurements | RSSI, Eb/N0, BER, link margin with bar meters |
| Dynamics | Doppler, range, data rate, propagation delay |
| Eb/N0 & BER History | 120-point rolling time-series chart |
| Counters | Good/bad frames, CLTU sent/ack, frame error rate |
| Frame Rate | Good/bad frames per second chart |
| Virtual Channels | VC0/1/7 activity LEDs, CLTU rate |
| **BPSK Constellation** | I/Q scatter plot showing demodulated symbol quality |
| **Ground Failure Injection** | Drop-down to inject/clear ground segment failures |

### 6.2 Constellation Diagram

The I/Q scatter plot shows 128 demodulated BPSK symbols. At high Eb/N0,
symbols cluster tightly around the ideal points (+1,0) and (-1,0). As
signal quality degrades, the clusters spread. During carrier lock loss,
symbols rotate randomly in the I/Q plane.

**Reading the constellation:**
- Tight clusters at ±1 → good lock, low BER
- Spread clusters → low Eb/N0, expect frame errors
- Rotating cloud → carrier lock lost
- Single cluster at origin → no signal

---

## 7. Failure Injection

### 7.1 Space Segment Failures (via Simulator Instructor)

These are injected through the simulator's instructor interface at
http://localhost:8080/instructor or via the HTTP API:

```bash
# PA overheat (gradual, 300s ramp)
curl -X POST http://localhost:8080/api/command -H 'Content-Type: application/json' \
  -d '{"type":"failure_inject","subsystem":"ttc","failure":"pa_overheat","magnitude":1.0,"onset":"gradual","onset_duration_s":300}'

# High BER (ionospheric interference)
curl -X POST http://localhost:8080/api/command -H 'Content-Type: application/json' \
  -d '{"type":"failure_inject","subsystem":"ttc","failure":"high_ber","magnitude":0.5}'

# Uplink loss
curl -X POST http://localhost:8080/api/command -H 'Content-Type: application/json' \
  -d '{"type":"failure_inject","subsystem":"ttc","failure":"uplink_loss","magnitude":1.0}'

# Receiver degradation
curl -X POST http://localhost:8080/api/command -H 'Content-Type: application/json' \
  -d '{"type":"failure_inject","subsystem":"ttc","failure":"receiver_degrade","magnitude":0.8}'

# Primary transponder failure
curl -X POST http://localhost:8080/api/command -H 'Content-Type: application/json' \
  -d '{"type":"failure_inject","subsystem":"ttc","failure":"primary_failure","magnitude":1.0}'

# Clear a failure
curl -X POST http://localhost:8080/api/command -H 'Content-Type: application/json' \
  -d '{"type":"failure_clear","failure_id":"<id>"}'
```

**Available space segment TTC failures:**

| Failure | Effect | Typical Magnitude |
|---------|--------|-------------------|
| `primary_failure` | Transponder A fails, loss of link | 1.0 |
| `redundant_failure` | Transponder B fails | 1.0 |
| `high_ber` | Eb/N0 offset (interference) | 0.1–1.0 (= 1–10 dB) |
| `pa_overheat` | PA thermal runaway, auto-shutdown at 70°C | 0.5–1.0 |
| `uplink_loss` | Uplink signal lost, no commands | 1.0 |
| `receiver_degrade` | RX noise figure increase | 0.1–1.0 (= 0.5–5 dB) |
| `antenna_deploy_failed` | Antenna stuck, TX blocked | 1.0 |

### 7.2 Ground Segment Failures (via Radio API)

These are injected through the Radio web dashboard or API at port 8094.
They degrade the effective Eb/N0 seen by the frame synchronizer.

**Via the web UI:** Use the "Ground Segment Failure Injection" panel —
select a failure type, set magnitude with the slider, click "Inject".

**Via the API:**

```bash
# Inject LNA degradation at 50% magnitude (3 dB penalty)
curl -X POST http://localhost:8094/api/failure/inject \
  -H 'Content-Type: application/json' \
  -d '{"name":"lna_degradation","magnitude":0.5}'

# Inject antenna tracking loss at full magnitude (15 dB)
curl -X POST http://localhost:8094/api/failure/inject \
  -H 'Content-Type: application/json' \
  -d '{"name":"tracking_loss","magnitude":1.0}'

# Clear a specific failure
curl -X POST http://localhost:8094/api/failure/clear \
  -H 'Content-Type: application/json' \
  -d '{"name":"lna_degradation"}'

# Clear all ground failures
curl -X POST http://localhost:8094/api/failure/clear \
  -H 'Content-Type: application/json' -d '{}'

# List available and active failures
curl http://localhost:8094/api/failures
```

**Available ground segment failures:**

| Failure | Max Penalty | Description |
|---------|-------------|-------------|
| `lna_degradation` | 6 dB | Low-noise amplifier noise figure increase |
| `antenna_mispoint` | 10 dB | Antenna pointing error (servo drift) |
| `feed_loss` | 4 dB | Feed horn / waveguide insertion loss |
| `rfi_interference` | 8 dB | Radio frequency interference raises noise floor |
| `hpa_degradation` | 5 dB | High-power amplifier output reduction (uplink) |
| `reference_oscillator_drift` | 3 dB | Reference oscillator drift degrades demodulation |
| `tracking_loss` | 15 dB | Complete antenna tracking system failure |

Magnitude ranges from 0.0 (no effect) to 1.0 (full penalty). Multiple
failures stack additively.

### 7.3 Combined Scenarios

The most realistic training involves both space and ground failures:

1. **Start RF bridge:** `SMO_RF_MODE=FRAME ./start.sh`
2. **Inject space failure:** PA overheat via instructor
3. **Inject ground failure:** LNA degradation via Radio
4. **Observe combined effect:** Eb/N0 drops from both sources
5. **Diagnose:** Radio shows ground failures explicitly; space failures require TM analysis
6. **Recover:** Clear ground failures via Radio; command spacecraft recovery via MCS

---

## 8. Configuration Reference

Edit `configs/eosat1/rfsim.yaml` to change defaults:

```yaml
mode: PACKET           # PACKET | FRAME | RF
ccsds:
  tm_frame_length: 1115
  scid: 1
  fecf_present: true
channel:
  eb_n0_db: 10.0       # Baseline Eb/N0 (dB)
  doppler_hz: 0.0
  delay_ms: 3.0
network:
  sim_tm_port: 8002
  sim_tc_port: 8001
  mcs_tm_port: 8012
  mcs_tc_port: 8011
  radio_port: 8094
```

---

## 9. Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| MCS shows no telemetry | MCS connected to wrong ports | Ensure MCS uses TM:8012, TC:8011 in FRAME mode |
| Radio shows all red LEDs | Bridge not connected to sim | Check simulator is running on 8002/8001 |
| Constellation is a single blob | Carrier not locked | Check Eb/N0 > 3 dB, clear ground failures |
| Frame sync stuck in SEARCH | Too many bit errors | Increase Eb/N0 or clear channel impairments |
| "smo-rfsim not found" | Package not installed | Run `pip install -e packages/smo-rfsim` |

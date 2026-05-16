# SMO Platform Architecture

**Document ID:** EOSAT1-AR-001
**Issue:** 2.0
**Date:** 2026-05-16

---

## 1. System Overview

The Space Mission Operations (SMO) platform simulates a complete ground segment for the EOSAT-1 6U multispectral imaging cubesat. It consists of 7 services communicating via TCP sockets and WebSockets.

```
┌──────────────────────────────────────────────────────────────┐
│                    OPERATOR WORKSTATIONS                     │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │  MCS    │ │ Planner │ │ Delayed  │ │ Orbit   │ Radio  │ │
│  │  :9090  │ │  :9091  │ │ TM :8092 │ │ :8093   │ :8094  │ │
│  └────┬────┘ └────┬────┘ └────┬─────┘ └───┬────┘└───┬────┘ │
└───────┼──────────┼─────────┼──────────┼──────────┼─────────┘
        │          │         │          │          │
  TC:8011│TM:8012   │         │          │          │
        │          │         │          │          │
┌───────┴──────────┴─────────┴──────────┴──────────┴─────────┐
│                    RF BRIDGE (optional)                      │
│                    smo-rfsim :8011/8012/8094                 │
│  TC:8001 ↕ TM:8002     TX → Channel → RX pipeline          │
└───────┬──────────────────────────────────────────────────────┘
        │
  TC:8001│TM:8002  WS:8080
        │
┌───────┴──────────────────────────────────────────────────────┐
│                 SPACECRAFT SIMULATOR                         │
│                 smo-simulator :8001/8002/8080                │
│  ┌─────┐ ┌──────┐ ┌──────┐ ┌─────┐ ┌─────┐ ┌───────┐      │
│  │ EPS │ │ AOCS │ │ OBDH │ │ TTC │ │ TCS │ │Payload│      │
│  └─────┘ └──────┘ └──────┘ └─────┘ └─────┘ └───────┘      │
│  SimulationEngine + ServiceDispatcher + FailureManager       │
└──────────────────────────────────────────────────────────────┘
```

## 2. Packages

| Package | Purpose | Port(s) | Dependencies |
|---------|---------|---------|-------------|
| **smo-common** | Shared library: ECSS protocol, config loading, orbit math | N/A | numpy |
| **smo-simulator** | Spacecraft simulation engine, 6 subsystem models | TC:8001, TM:8002, HTTP:8080 | smo-common, aiohttp |
| **smo-mcs** | Mission Control System — operator UI | HTTP:9090 | smo-common, aiohttp |
| **smo-rfsim** | RF simulation bridge — CCSDS framing, signal processing | TC:8011, TM:8012, Radio:8094 | smo-common, aiohttp, reedsolo |
| **smo-planner** | Mission planning — pass scheduling, budgets | HTTP:9091 | smo-common, aiohttp, sgp4 |
| **smo-gateway** | TM/TC relay for multi-site deployments | TCP:10025 | smo-common |

## 3. Data Flows

### 3.1 TM Downlink (Spacecraft → Ground)

```
Engine._emit_hk_packets()
  → engine._enqueue_tm(pkt)     [checks downlink_active]
    → engine.tm_queue             [maxsize=2000]
      → server._tm_broadcast_loop()  [drain + TCP send, 2s drain timeout]
        → bridge._relay_tm()       [reads from TCP 8002]
          → pipeline.enqueue_tm_packet()
            → SpacecraftTX           [VC mux, RS encode, ASM, BPSK mod]
              → SampleBuffer[tx→ch]  [max_depth=128]
                → ChannelStage       [Eb/N0, AWGN, Doppler]
                  → SampleBuffer[ch→rx] [max_depth=128]
                    → GroundStationRX   [demod, frame sync, RS decode]
                      → _on_packet_recovered()
                        → _recovered_queue   [maxsize=500]
                          → bridge._relay_recovered_tm()  [batch drain]
                            → _broadcast_tm()  [TCP to MCS :8012]
                              → MCS._tm_receive_loop()
                                → _process_tm()
                                  → _param_cache update
```

### 3.2 TC Uplink (Ground → Spacecraft)

```
MCS UI command builder
  → POST /api/pus-command
    → build_tc_packet()
      → MCS._tc_forward() [TCP to bridge :8011 or sim :8001]
        → sim.tc_queue     [maxsize=500]
          → engine._drain_tc_queue()
            → engine._dispatch_tc()
              → uplink_active check
              → bootloader allowlist check
              → power gate check (EPS line + subsystem mode)
              → S1.1 acceptance ACK
              → dispatcher.dispatch(service, subtype, data)
              → S1.7 completion ACK (or S1.8 failure)
```

### 3.3 Diagnostic Counters

Every handoff point has a persistent counter (no silent drops):

| Counter | Location | What it counts |
|---------|----------|----------------|
| `engine.tm_packets_enqueued` | engine.py | Packets entering tm_queue |
| `engine.tm_queue_drops` | engine.py | Packets dropped (queue full) |
| `server.tm_packets_broadcast` | server.py | Packets sent to TCP clients |
| `bridge._tm_packets_relayed` | bridge.py | Packets received from sim |
| `bridge._tm_packets_delivered` | bridge.py | Packets sent to MCS clients |
| `pipeline.tx_packet_drops` | tx_chain.py | TX queue full drops |
| `pipeline.tx/rx_buffer_overflows` | sample_buffer.py | Sample buffer overflows |
| `pipeline.rx_good/bad_frames` | rx_chain.py | Frame decode results |
| `pipeline.rx_rs/fecf_failures` | rx_chain.py | FEC failure breakdown |
| `pipeline.rx_flywheel_misses` | rx_chain.py | Frame sync misses |
| `pipeline.recovered_queue_drops` | coordinator.py | Recovery queue full |
| `mcs.tm_packets_received` | server.py | Packets processed by MCS |

Access all pipeline counters: `coordinator.get_diagnostics()`.

## 4. Simulator Engine

### 4.1 Tick Loop Order

```
while self.running:
    1. _drain_instr_queue()        # Instructor commands
    2. orbit.advance(dt_sim)       # Orbital mechanics
    3. _tick_spacecraft_phase()    # Phase state machine (0→6)
    4. _tick_auto_tx_hold()        # TX hold-down timer (15 min)
    5. subsystem.tick() × 6        # EPS, AOCS, OBDH, TTC, TCS, Payload
    6. _tick_s12_monitoring()      # Parameter limit checks
    7. _tick_fdir()                # Fault detection/isolation/recovery
    8. _check_subsystem_events()   # Edge-triggered events
    9. _check_transitions()        # AOS/LOS transitions
   10. _emit_hk_packets()         # Periodic HK emission
   11. _tick_dump_emission()      # S15 paced TM dump
   12. _drain_tc_queue()          # TC processing (AFTER subsystem ticks)
   13. _failure_manager.tick()    # Failure timing
```

**Key:** TCs are drained AFTER subsystem ticks (step 12) so `downlink_active` reads current link status when generating S1.1 ACKs.

### 4.2 Spacecraft Phases

| Phase | Name | Active Subsystems | HK SIDs | Transition |
|-------|------|-------------------|---------|------------|
| 0 | PRE_SEPARATION | None | None | Separation bolt → 1 |
| 1 | SEPARATION_TIMER | EPS, TTC, OBDH | None | 30 min timer → 2 |
| 2 | INITIAL_POWER_ON | EPS, TTC, OBDH | None | Immediate → 3 |
| 3 | BOOTLOADER_OPS | EPS, TTC, OBDH, TCS | SID 11 (beacon) | OBC_BOOT_APP (S8.1 func=55) → 4 |
| 4 | LEOP | All | All SIDs | Manual → 5 |
| 5 | COMMISSIONING | All | All SIDs | Manual → 6 |
| 6 | NOMINAL | All | All SIDs | — |

### 4.3 Power Gating

Commands rejected at acceptance (S1.2) if target subsystem's EPS power line is OFF:

| func_id range | Power line | Subsystem mode gate |
|---------------|-----------|---------------------|
| 0-15 | aocs_wheels | AOCS mode > 0 (except set_mode, enable/disable) |
| 26-39 | payload | Payload mode > 0 (except set_mode) |
| 63-78 | ttc_tx | None (line gate only) |
| 16-25, 40-62, 80-82 | None | None (always allowed) |

### 4.4 HK Telemetry

| SID | Name | Interval | Params | Power-gated |
|-----|------|----------|--------|-------------|
| 1 | EPS | 1.0s | 51 | No |
| 2 | AOCS | 4.0s | 67 | aocs_wheels |
| 3 | TCS | 60.0s | 17 | No |
| 4 | Platform | 8.0s | 26 | No |
| 5 | Payload | 8.0s | 23 | payload |
| 6 | TTC | 8.0s | 27 | No |
| 11 | Beacon | 30.0s | 7 | No (bootloader-safe) |

## 5. RF Simulation Layer

### 5.1 Operating Modes

| Mode | Processing | Use case |
|------|-----------|----------|
| PACKET | Transparent relay | Development, quick testing |
| FRAME | CCSDS Transfer Framing + BER injection | Ground segment training |
| RF | Full BPSK modulation/demodulation + FEC | Realistic RF chain testing |

### 5.2 Frame Synchronizer

- Three-state: SEARCH → VERIFY (3 consecutive) → LOCK
- Detects both normal ASM (`0x1ACFFC1D`) and inverted (`0xE53003E2`) for 180° BPSK ambiguity
- ±2 byte alignment window in LOCK state (demodulator timing drift)
- Flywheel: 4 misses → LOCK loss
- Buffer capped at 10× frame length (memory safety)

### 5.3 Packet Extraction

Uses First Header Pointer (FHP) from TM frame header for resynchronization after frame loss. The builder tracks packet-start offsets, and the parser uses FHP to align extraction. Reassembly buffer capped at 5× data zone length.

## 6. Failure Injection (42 modes)

- **AOCS** (12): rw_seizure/bearing, gyro_bias, st_blind/failure, css/mag/mtq failures
- **EPS** (8): solar degradation, battery/bus faults, overcurrent, load shedding
- **TTC** (14): transponder failures, BER/PA/uplink issues, 7× ground segment faults
- **OBDH** (9): watchdog/crash, memory errors, bus failure, bootloader stuck
- **Payload** (5): cooler/FPA degradation, image corruption, memory faults
- **TCS** (7): heater failures (stuck on/open circuit), cooler, thermal anomalies

## 7. Memory Safety

All data structures are bounded (fixes applied 2026-05-16):

| Structure | Max size | Eviction |
|-----------|---------|----------|
| tm_queue | 2000 packets | Drop + log |
| _recovered_queue | 500 packets | Drop + log |
| Sample buffers | 128 chunks | Drop oldest |
| Image catalog | 1000 entries | FIFO eviction |
| Dump pending | 5000 packets | Truncate + log |
| S13 transfers | Auto-cleaned | Removed on completion |
| Handover log | 500 entries | deque rotation |
| Procedure log | 500 entries | Tail truncation |
| Frame sync buffer | 10× frame length | Head truncation |
| Reassembly buffer | 5× data zone | Clear on overflow |

## 8. Test Suite (~1200 tests)

| Suite | Tests | Runtime | What it covers |
|-------|-------|---------|----------------|
| Unit (sim/rfsim/common/mcs) | ~932 | ~15s | All subsystems, protocol, UI |
| Acceptance (RF pipeline) | ~240 | ~3 min | Every command/service/failure through RF |
| Integration (diagnostics) | ~9 | ~8s | Pipeline counters, nominal mode |
| E2E Browser (Playwright) | ~35 | ~35 min | Full LEOP+commissioning through MCS UI |
| Memory profiling | 2 | ~2s | Sustained load, bounded structures |

## 9. Configuration Reference

See `configs/eosat1/` for all YAML configuration. Key files:
- `mission.yaml` — spacecraft identity, APID, bootloader settings
- `orbit.yaml` — TLE, ground stations, orbital parameters
- `rfsim.yaml` — RF bridge mode, CCSDS framing, channel model
- `telemetry/hk_structures.yaml` — 7 SIDs, 218 parameters
- `procedures/procedure_index.yaml` — 57 procedures across 5 categories

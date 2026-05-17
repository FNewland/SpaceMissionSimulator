"""Packet-loss diagnostic tests for the full TM chain.

Verifies counters at every handoff point and reports where packets
are lost. Exercises both the simulator-side queue and the RF pipeline.
"""

import asyncio
import queue
import struct
import time
import pytest


# ── Helpers ──

def _build_test_tm_packet(seq: int = 0, data_len: int = 20) -> bytes:
    """Build a minimal valid ECSS TM packet (S3.25 HK)."""
    # CCSDS primary header (6 bytes)
    # Version=0, Type=0 (TM), SecHdrFlag=1, APID=1
    word0 = (0 << 13) | (0 << 12) | (1 << 11) | 1
    # Sequence: standalone=3, count=seq
    word1 = (3 << 14) | (seq & 0x3FFF)
    # Data length = secondary header + payload - 1
    payload = bytes(range(data_len))
    # PUS secondary header: version=2, service=3, subtype=25, counter, time(4B)
    sec_hdr = struct.pack('>BBBBL', 0x20, 3, 25, seq & 0xFF, 0)
    total_data = sec_hdr + payload
    pkt_data_length = len(total_data) - 1
    header = struct.pack('>HHH', word0, word1, pkt_data_length)
    return header + total_data


def _print_chain_report(diag: dict, header: str = "TM CHAIN DIAGNOSTIC REPORT"):
    """Print a formatted diagnostic report."""
    print(f"\n=== {header} ===")
    for key, val in diag.items():
        label = key.replace("_", " ").title()
        print(f"  {label:.<40} {val}")
    print("=" * (len(header) + 8) + "\n")


# ── Test 1: Engine TM queue drop counter ──

class TestEngineDiagnostics:
    """Verify simulator engine drop counters."""

    def test_tm_queue_drop_counter(self):
        """Overfill tm_queue and verify tm_queue_drops increments."""
        from smo_simulator.engine import SimulationEngine
        from pathlib import Path

        config_dir = Path(__file__).parent.parent.parent / "configs" / "eosat1"
        engine = SimulationEngine(config_dir)

        # Force downlink active
        engine._override_passes = True
        engine._in_contact = True
        engine.params[0x0501] = 2  # LOCKED

        assert engine.tm_queue_drops == 0
        assert engine.tm_packets_enqueued == 0

        # Fill the queue to capacity
        pkt = _build_test_tm_packet()
        filled = 0
        while not engine.tm_queue.full():
            engine._enqueue_tm(pkt)
            filled += 1

        assert engine.tm_packets_enqueued == filled
        assert engine.tm_queue_drops == 0

        # Next one should drop
        engine._enqueue_tm(pkt)
        assert engine.tm_queue_drops == 1
        assert engine.tm_packets_enqueued == filled  # didn't increment

        # A few more
        for _ in range(5):
            engine._enqueue_tm(pkt)
        assert engine.tm_queue_drops == 6

    def test_enqueue_count_increments(self):
        """Verify tm_packets_enqueued increments on successful enqueue."""
        from smo_simulator.engine import SimulationEngine
        from pathlib import Path

        config_dir = Path(__file__).parent.parent.parent / "configs" / "eosat1"
        engine = SimulationEngine(config_dir)
        engine._override_passes = True

        pkt = _build_test_tm_packet()
        engine._enqueue_tm(pkt)
        engine._enqueue_tm(pkt)
        engine._enqueue_tm(pkt)
        assert engine.tm_packets_enqueued == 3


# ── Test 2: RF Pipeline end-to-end counters ──

class TestPipelineDiagnostics:
    """Verify RF pipeline counters with get_diagnostics()."""

    def test_end_to_end_high_snr(self):
        """Inject packets through the pipeline at high Eb/N0, expect zero loss."""
        from smo_rfsim.config import RFSimConfig, CCSDSConfig, ChannelConfig
        from smo_rfsim.pipeline.coordinator import PipelineCoordinator

        config = RFSimConfig()
        config.ccsds.rs_enabled = True
        config.ccsds.convolutional_enabled = False  # keep it fast
        config.channel.eb_n0_db = 20.0  # very high SNR — no errors
        config.network.zmq_samples_port = 0  # disable ZMQ for tests

        coord = PipelineCoordinator(config)
        coord.set_link_in_view(True)
        coord.start()
        coord.set_transmitting(True)

        # Inject N packets with pacing
        N = 10
        for i in range(N):
            pkt = _build_test_tm_packet(seq=i)
            coord.enqueue_tm_packet(pkt)
            time.sleep(0.15)  # let TX generate frames

        # Wait for recovery using batch drain
        recovered = []
        deadline = time.monotonic() + 15.0
        while len(recovered) < N and time.monotonic() < deadline:
            batch = coord.drain_recovered_packets()
            if batch:
                recovered.extend(batch)
            else:
                time.sleep(0.2)

        coord.stop()

        diag = coord.get_diagnostics()
        diag["injected"] = N
        diag["recovered_by_test"] = len(recovered)
        _print_chain_report(diag)

        # At 20 dB Eb/N0, expect zero loss
        assert diag["tx_packet_drops"] == 0, "TX queue dropped packets"
        assert diag["tx_buffer_overflows"] == 0, "TX→Channel sample buffer overflow"
        assert diag["rx_buffer_overflows"] == 0, "Channel→RX sample buffer overflow"
        assert diag["rx_bad_frames"] == 0, f"RX had {diag['rx_bad_frames']} bad frames"
        assert diag["recovered_queue_drops"] == 0, "Recovery queue overflow"
        assert diag["rx_packets_recovered"] >= N, \
            f"Only recovered {diag['rx_packets_recovered']}/{N} packets"

    def test_diagnostics_method_fields(self):
        """Verify get_diagnostics() returns all expected fields."""
        from smo_rfsim.config import RFSimConfig
        from smo_rfsim.pipeline.coordinator import PipelineCoordinator

        config = RFSimConfig()
        config.network.zmq_samples_port = 0
        coord = PipelineCoordinator(config)
        diag = coord.get_diagnostics()

        expected_keys = {
            "tx_frames_transmitted", "tx_data_frames", "tx_idle_frames",
            "tx_packet_drops", "tx_buffer_overflows", "rx_buffer_overflows",
            "rx_good_frames", "rx_bad_frames", "rx_rs_failures",
            "rx_fecf_failures", "rx_packets_recovered", "rx_flywheel_misses",
            "rx_phase_nudges", "rx_mod_searches", "rx_pll_resets",
            "recovered_queue_drops", "tm_queue_depth", "recovered_queue_depth",
        }
        assert set(diag.keys()) == expected_keys

    def test_tx_packet_drops_counter(self):
        """Verify TX packet_drops increments on queue overflow."""
        from smo_rfsim.pipeline.tx_chain import SpacecraftTX
        import queue as q

        small_queue = q.Queue(maxsize=3)
        tx = SpacecraftTX(packet_queue=small_queue)

        pkt = _build_test_tm_packet()
        for _ in range(3):
            tx.enqueue_packet(pkt)
        assert tx.packet_drops == 0

        tx.enqueue_packet(pkt)
        assert tx.packet_drops == 1

    def test_rx_failure_counters_initialized(self):
        """Verify RX failure counters start at zero."""
        from smo_rfsim.config import RFSimConfig
        from smo_rfsim.pipeline.coordinator import PipelineCoordinator

        config = RFSimConfig()
        config.network.zmq_samples_port = 0
        coord = PipelineCoordinator(config)
        diag = coord.get_diagnostics()

        assert diag["rx_rs_failures"] == 0
        assert diag["rx_fecf_failures"] == 0
        assert diag["rx_flywheel_misses"] == 0

    def test_flywheel_misses_exposed(self):
        """Verify flywheel_misses is accessible through the coordinator."""
        from smo_rfsim.config import RFSimConfig
        from smo_rfsim.pipeline.coordinator import PipelineCoordinator

        config = RFSimConfig()
        config.network.zmq_samples_port = 0
        coord = PipelineCoordinator(config)
        assert coord.flywheel_misses == 0


# ── Test 3: Full engine in nominal mode, sustained run ──

class TestNominalModeSustained:
    """Run the engine in nominal mode (phase 6) for an extended period
    and verify no packets are lost at any stage."""

    def test_nominal_hk_sustained(self):
        """Run engine in nominal mode for 60 ticks, verify all HK reaches
        tm_queue with zero drops."""
        from smo_simulator.engine import SimulationEngine
        from pathlib import Path
        import threading

        config_dir = Path(__file__).parent.parent.parent / "configs" / "eosat1"
        engine = SimulationEngine(config_dir)

        # Force nominal mode (skip bootloader)
        engine._spacecraft_phase = 6
        engine.params[0x0311] = 1  # sw_image = APPLICATION
        engine._override_passes = True  # force downlink active

        # Run 60 ticks at 1s intervals (simulated, not wall-clock)
        N_TICKS = 60
        dt = 1.0

        # Manually tick the engine (don't use start() which spawns a thread)
        from types import SimpleNamespace
        orbit = SimpleNamespace(
            in_contact=True, in_eclipse=False,
            solar_beta_deg=20.0, lat_deg=45.0, lon_deg=10.0,
            alt_km=450.0, vel_x=0.0, vel_y=7.5, vel_z=0.0,
            gs_elevation_deg=30.0, gs_azimuth_deg=180.0,
            gs_range_km=800.0,
        )

        # Patch orbit.advance to return our mock orbit
        original_advance = engine.orbit.advance
        engine.orbit.advance = lambda dt_sim: orbit

        enqueued_before = engine.tm_packets_enqueued

        for tick in range(N_TICKS):
            dt_sim = dt
            engine._drain_instr_queue()

            # Advance orbit (patched)
            orbit_state = engine.orbit.advance(dt_sim)
            engine._in_contact = orbit_state.in_contact
            engine.params[0x05FF] = 1 if engine._override_passes else 0

            engine._tick_spacecraft_phase(dt_sim)
            engine._tick_auto_tx_hold(dt_sim)

            for name, model in engine.subsystems.items():
                try:
                    model.tick(dt_sim, orbit_state, engine.params)
                except Exception:
                    pass

            engine._tick_s12_monitoring()
            if engine._fdir_enabled:
                engine._tick_fdir()
                engine._tick_fdir_advanced(dt_sim)
            engine._check_subsystem_events()
            engine._check_transitions(orbit_state)

            # This is where HK packets are generated
            engine._emit_hk_packets(dt_sim)
            engine._tick_dump_emission(dt_sim)

            # TC processing
            engine._drain_tc_queue()

            engine._tick_count += 1

        total_enqueued = engine.tm_packets_enqueued - enqueued_before

        # Drain the tm_queue to count what's there
        packets_in_queue = 0
        while not engine.tm_queue.empty():
            try:
                engine.tm_queue.get_nowait()
                packets_in_queue += 1
            except Exception:
                break

        print(f"\n=== NOMINAL MODE SUSTAINED TEST ({N_TICKS} ticks) ===")
        print(f"  Packets enqueued:     {total_enqueued}")
        print(f"  Packets in queue:     {packets_in_queue}")
        print(f"  Queue drops:          {engine.tm_queue_drops}")
        print(f"  Queue max size:       {engine.tm_queue.maxsize}")
        print(f"================================================\n")

        assert engine.tm_queue_drops == 0, \
            f"Engine dropped {engine.tm_queue_drops} packets from tm_queue"
        assert total_enqueued > 0, "No HK packets were generated"
        assert total_enqueued == packets_in_queue, \
            f"Mismatch: enqueued {total_enqueued} but only {packets_in_queue} in queue"

    def test_nominal_through_rf_pipeline(self):
        """Run engine in nominal mode, feed HK through the RF pipeline,
        verify packet recovery matches enqueue count."""
        from smo_simulator.engine import SimulationEngine
        from smo_rfsim.config import RFSimConfig
        from smo_rfsim.pipeline.coordinator import PipelineCoordinator
        from pathlib import Path
        from types import SimpleNamespace

        config_dir = Path(__file__).parent.parent.parent / "configs" / "eosat1"
        engine = SimulationEngine(config_dir)
        engine._spacecraft_phase = 6
        engine.params[0x0311] = 1
        engine._override_passes = True

        # Set up RF pipeline
        rf_config = RFSimConfig()
        rf_config.ccsds.rs_enabled = True
        rf_config.ccsds.convolutional_enabled = False
        rf_config.channel.eb_n0_db = 20.0
        rf_config.network.zmq_samples_port = 0

        coord = PipelineCoordinator(rf_config)
        coord.set_link_in_view(True)
        coord.start()
        coord.set_transmitting(True)

        orbit = SimpleNamespace(
            in_contact=True, in_eclipse=False,
            solar_beta_deg=20.0, lat_deg=45.0, lon_deg=10.0,
            alt_km=450.0, vel_x=0.0, vel_y=7.5, vel_z=0.0,
            gs_elevation_deg=30.0, gs_azimuth_deg=180.0,
            gs_range_km=800.0,
        )
        engine.orbit.advance = lambda dt_sim: orbit

        # Run 30 ticks, feeding each HK packet into the pipeline
        N_TICKS = 30
        packets_injected = 0

        for tick in range(N_TICKS):
            engine._drain_instr_queue()
            orbit_state = engine.orbit.advance(1.0)
            engine._in_contact = True
            engine.params[0x05FF] = 1

            engine._tick_spacecraft_phase(1.0)
            engine._tick_auto_tx_hold(1.0)
            for name, model in engine.subsystems.items():
                try:
                    model.tick(1.0, orbit_state, engine.params)
                except Exception:
                    pass
            engine._tick_s12_monitoring()
            engine._check_subsystem_events()
            engine._check_transitions(orbit_state)
            engine._emit_hk_packets(1.0)
            engine._drain_tc_queue()
            engine._tick_count += 1

            # Drain tm_queue and feed into pipeline
            while not engine.tm_queue.empty():
                try:
                    pkt = engine.tm_queue.get_nowait()
                    coord.enqueue_tm_packet(pkt)
                    packets_injected += 1
                except Exception:
                    break

            # Small delay to let TX process
            time.sleep(0.1)

        # Wait for recovery
        # Wait for recovery using batch drain with retries
        recovered = []
        empty_rounds = 0
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline and empty_rounds < 5:
            batch = coord.drain_recovered_packets()
            if batch:
                recovered.extend(batch)
                empty_rounds = 0
            else:
                empty_rounds += 1
                time.sleep(0.3)
        coord.stop()

        diag = coord.get_diagnostics()
        diag["engine_enqueued"] = engine.tm_packets_enqueued
        diag["engine_drops"] = engine.tm_queue_drops
        diag["injected_to_pipeline"] = packets_injected
        diag["recovered_by_test"] = len(recovered)

        _print_chain_report(diag, f"NOMINAL MODE RF PIPELINE ({N_TICKS} ticks)")

        assert engine.tm_queue_drops == 0, \
            f"Engine dropped {engine.tm_queue_drops} packets"
        assert diag["tx_packet_drops"] == 0, \
            f"TX dropped {diag['tx_packet_drops']} packets"
        assert diag["tx_buffer_overflows"] == 0, \
            f"TX buffer had {diag['tx_buffer_overflows']} overflows"
        assert diag["rx_buffer_overflows"] == 0, \
            f"RX buffer had {diag['rx_buffer_overflows']} overflows"
        assert diag["recovered_queue_drops"] == 0, \
            f"Recovery queue dropped {diag['recovered_queue_drops']} packets"
        assert diag["rx_bad_frames"] == 0, \
            f"RX had {diag['rx_bad_frames']} bad frames"
        assert len(recovered) > 0, "No packets recovered from pipeline"
        # At high SNR, we should recover all injected packets
        assert len(recovered) >= packets_injected, \
            f"Only recovered {len(recovered)}/{packets_injected} packets"

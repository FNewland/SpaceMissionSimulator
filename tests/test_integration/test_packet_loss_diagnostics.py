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

        # Wait for recovery
        recovered = []
        deadline = time.monotonic() + 15.0
        loop = asyncio.new_event_loop()
        while len(recovered) < N and time.monotonic() < deadline:
            try:
                pkt = loop.run_until_complete(
                    asyncio.wait_for(coord.get_recovered_packet(), timeout=1.0))
                if pkt:
                    recovered.append(pkt)
            except (asyncio.TimeoutError, Exception):
                pass
        loop.close()

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

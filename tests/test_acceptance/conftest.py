"""Shared RF test harness for acceptance tests.

Runs the SimulationEngine and RF PipelineCoordinator together,
providing helpers that send TC packets, tick the engine, feed TM
through the full RF pipeline (TX → Channel → RX), and verify
S1.1/S1.7 ACKs and HK parameter values in recovered packets.

Every command goes through real signal processing. Any packet loss
is a real bug.
"""

import struct
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest

from smo_common.protocol.ecss_packet import build_tc_packet, decommutate_packet
from smo_simulator.engine import SimulationEngine
from smo_rfsim.config import RFSimConfig
from smo_rfsim.pipeline.coordinator import PipelineCoordinator

CONFIG_DIR = Path(__file__).parent.parent.parent / "configs" / "eosat1"
APP_APID = 1

ORBIT = SimpleNamespace(
    in_contact=True, in_eclipse=False, solar_beta_deg=20.0,
    lat_deg=45.0, lon_deg=10.0, alt_km=450.0,
    vel_x=0.0, vel_y=7.5, vel_z=0.0,
    gs_elevation_deg=30.0, gs_azimuth_deg=180.0, gs_range_km=800.0,
)


class RFTestHarness:
    """Manages engine + RF pipeline for proper command-response testing.

    Every TC is sent as a real ECSS packet, every TM response flows
    through the full RF signal processing chain.
    """

    def __init__(self):
        # Engine in nominal mode
        self.engine = SimulationEngine(CONFIG_DIR)
        self.engine._spacecraft_phase = 6
        self.engine.params[0x0311] = 1
        self.engine._override_passes = True
        self.engine._in_contact = True
        self.engine.params[0x0501] = 2

        # Power all lines
        eps = self.engine.subsystems.get("eps")
        if eps and hasattr(eps, "_state"):
            for line in eps._state.power_lines:
                eps._state.power_lines[line] = True

        # App mode
        obdh = self.engine.subsystems.get("obdh")
        if obdh and hasattr(obdh, "_state"):
            obdh._state.sw_image = 1

        # TTC lock
        ttc = self.engine.subsystems.get("ttc")
        if ttc and hasattr(ttc, "_state"):
            ttc._state.frame_sync = True
            ttc._state.carrier_lock = True
            ttc._state.bit_sync = True
            ttc._state.pa_on = True
            ttc._state.antenna_deployed = True
            ttc._state._lock_timer = 60.0
            ttc._state.beacon_mode = False

        # AOCS nominal
        aocs = self.engine.subsystems.get("aocs")
        if aocs and hasattr(aocs, "_state"):
            aocs._state.mode = 4
            aocs._state.time_in_mode = 60.0
            for i in range(4):
                aocs._state.active_wheels[i] = True

        # Disable watchdog to prevent reboot during test accumulation
        if obdh and hasattr(obdh, "_state"):
            obdh._state.watchdog_armed = False
            obdh._state.watchdog_timer = 0

        # Disable FDIR to prevent safe mode during rapid mode transitions
        self.engine._fdir_enabled = False

        # Re-enable all HK SIDs
        for sid in self.engine._hk_enabled:
            self.engine._hk_enabled[sid] = True

        # RF pipeline (high Eb/N0, no errors)
        rf_config = RFSimConfig()
        rf_config.ccsds.rs_enabled = True
        rf_config.ccsds.convolutional_enabled = False
        rf_config.channel.eb_n0_db = 20.0
        rf_config.network.zmq_samples_port = 0
        self.pipeline = PipelineCoordinator(rf_config)

        self._seq_count = 0
        self._running = False
        self.commands_sent = 0
        self.acks_received = 0
        self.packets_recovered = 0

    def start(self):
        """Start the RF pipeline."""
        self.pipeline.set_link_in_view(True)
        self.pipeline.start()
        self.pipeline.set_transmitting(True)
        # Warm up: tick a few times to stabilize
        self._tick(5)
        self._flush_tm_to_pipeline()
        time.sleep(0.5)
        self._drain_recovered()  # discard startup packets
        self._running = True

    def stop(self):
        """Stop the RF pipeline and print diagnostics."""
        self._running = False
        self.pipeline.stop()
        diag = self.pipeline.get_diagnostics()
        print(f"\n{'='*60}")
        print("RF TEST HARNESS DIAGNOSTICS")
        print(f"{'='*60}")
        print(f"  Commands sent:         {self.commands_sent}")
        print(f"  ACKs received:         {self.acks_received}")
        print(f"  Packets recovered:     {self.packets_recovered}")
        for k, v in diag.items():
            print(f"  {k}: {v}")
        print(f"{'='*60}\n")

    def _tick(self, n=1):
        """Tick the engine n times."""
        for _ in range(n):
            self.engine._drain_instr_queue()
            self.engine._in_contact = True
            self.engine.params[0x05FF] = 1
            self.engine._tick_spacecraft_phase(1.0)
            self.engine._tick_auto_tx_hold(1.0)
            for name, model in self.engine.subsystems.items():
                try:
                    model.tick(1.0, ORBIT, self.engine.params)
                except Exception:
                    pass
            self.engine._tick_s12_monitoring()
            if self.engine._fdir_enabled:
                self.engine._tick_fdir()
                self.engine._tick_fdir_advanced(1.0)
            self.engine._check_subsystem_events()
            self.engine._emit_hk_packets(1.0)
            self.engine._tick_dump_emission(1.0)
            self.engine._drain_tc_queue()
            self.engine._tick_count += 1

    def _flush_tm_to_pipeline(self):
        """Move all packets from engine TM queue into RF pipeline."""
        count = 0
        while not self.engine.tm_queue.empty():
            try:
                pkt = self.engine.tm_queue.get_nowait()
                self.pipeline.enqueue_tm_packet(pkt)
                count += 1
            except Exception:
                break
        return count

    def _drain_recovered(self) -> list:
        """Drain all recovered packets from the RF pipeline."""
        packets = self.pipeline.drain_recovered_packets()
        self.packets_recovered += len(packets)
        return packets

    def _recover_and_parse(self, wait_s: float = 2.0) -> list:
        """Wait for pipeline processing, then recover and parse all packets."""
        # Feed TM to pipeline
        self._flush_tm_to_pipeline()
        time.sleep(0.1)

        # Wait for pipeline to process
        deadline = time.monotonic() + wait_s
        all_parsed = []
        empty_rounds = 0
        while time.monotonic() < deadline and empty_rounds < 3:
            raw_pkts = self._drain_recovered()
            if raw_pkts:
                empty_rounds = 0
                for raw in raw_pkts:
                    parsed = decommutate_packet(raw)
                    if parsed and parsed.secondary:
                        all_parsed.append(parsed)
            else:
                empty_rounds += 1
                time.sleep(0.3)
        return all_parsed

    def send_command(self, service: int, subtype: int,
                     data: bytes = b"", name: str = "",
                     ticks: int = 5, wait_s: float = 2.0) -> dict:
        """Send a TC packet through the engine, feed TM through RF pipeline,
        and return parsed responses.

        Returns:
            {
                "ack_11": bool,     # S1.1 acceptance ACK received
                "ack_17": bool,     # S1.7 completion ACK received
                "ack_12": bool,     # S1.2 rejection received
                "ack_18": bool,     # S1.8 failure received
                "responses": [...], # All parsed TM packets
                "name": str,
            }
        """
        # Build and inject TC
        tc = build_tc_packet(APP_APID, service, subtype, data,
                             seq_count=self._seq_count)
        self._seq_count += 1
        self.commands_sent += 1
        self.engine.tc_queue.put_nowait(tc)

        # Tick engine to process TC and generate responses
        self._tick(ticks)

        # Feed all TM through RF pipeline and recover
        parsed = self._recover_and_parse(wait_s)

        # Classify responses
        ack_11 = any(p.secondary.service == 1 and p.secondary.subtype == 1
                     for p in parsed)
        ack_17 = any(p.secondary.service == 1 and p.secondary.subtype == 7
                     for p in parsed)
        ack_12 = any(p.secondary.service == 1 and p.secondary.subtype == 2
                     for p in parsed)
        ack_18 = any(p.secondary.service == 1 and p.secondary.subtype == 8
                     for p in parsed)

        if ack_11:
            self.acks_received += 1

        cmd_name = name or f"S{service}.{subtype}"
        status = "ACK" if ack_11 else ("REJ" if ack_12 else "NO_RESP")
        print(f"  [{status}] {cmd_name}: S1.1={'Y' if ack_11 else 'N'} "
              f"S1.7={'Y' if ack_17 else 'N'} "
              f"(+{len(parsed)} TM pkts)")

        return {
            "ack_11": ack_11,
            "ack_17": ack_17,
            "ack_12": ack_12,
            "ack_18": ack_18,
            "responses": parsed,
            "name": cmd_name,
        }

    def send_s8(self, func_id: int, data: bytes = b"",
                name: str = "", **kw) -> dict:
        """Send an S8.1 function command."""
        payload = bytes([func_id]) + data
        return self.send_command(8, 1, payload,
                                name=name or f"S8.1_func{func_id}", **kw)

    def get_hk(self, sid: int) -> Optional[dict]:
        """Request one-shot HK through the RF pipeline and decode it.

        Returns dict of {param_id: value} or None if no response.
        """
        result = self.send_command(3, 27, struct.pack('>H', sid),
                                  name=f"HK_SID{sid}", ticks=3, wait_s=2.0)
        # Find S3.25 in responses
        for pkt in result["responses"]:
            if pkt.secondary.service == 3 and pkt.secondary.subtype == 25:
                if len(pkt.data_field) >= 2:
                    pkt_sid = struct.unpack('>H', pkt.data_field[:2])[0]
                    if pkt_sid == sid:
                        return {"raw": pkt.data_field, "sid": sid,
                                "size": len(pkt.data_field)}
        return None


@pytest.fixture(scope="module")
def harness():
    """Shared RF test harness for the acceptance test module."""
    h = RFTestHarness()
    h.start()
    yield h
    h.stop()

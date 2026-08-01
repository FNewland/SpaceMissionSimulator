"""Shared RF test harness for acceptance tests.

Runs the SimulationEngine and RF PipelineCoordinator together,
providing helpers that send TC packets, tick the engine, feed TM
through the full RF pipeline (TX → Channel → RX), and verify
S1.1/S1.7 ACKs and HK parameter values in recovered packets.

Every command goes through real signal processing. Any packet loss
is a real bug.
"""

import math
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

    # ── Link timing ────────────────────────────────────────────────────
    # Everything below is DERIVED from the rate the pipeline is actually
    # running at (see PipelineCoordinator.frame_air_time_s), never pinned to
    # a constant. The downlink is framed, so recovered telemetry arrives in
    # whole-frame quanta; a timeout expressed in seconds is only meaningful
    # relative to how long a frame takes to transmit, and that changes by a
    # factor of 16 across the BEACON/LOW/HIGH data-rate ladder.

    #: Frames of grace before declaring telemetry lost: one for the frame
    #: already on the air when the packet was handed over, one for the frame
    #: that carries it, one for the receiver to demodulate/FEC-decode and
    #: hand it up, and x2 for a loaded machine (the pipeline is three Python
    #: threads competing with the test process). Measured first-ACK latency
    #: is ~0.3 frame idle and ~1.4 frames under 2x CPU oversubscription.
    _DELIVERY_FRAMES = 6.0

    #: Frames of continuous silence that mean "nothing more is coming".
    #: Must exceed 1, or the gap between two consecutive frames would be
    #: mistaken for the end of the burst.
    _QUIET_FRAMES = 2.0

    @property
    def frame_air_time_s(self) -> float:
        """Seconds to transmit one coded TM frame at the configured rate."""
        return self.pipeline.frame_air_time_s

    @property
    def recovery_timeout_s(self) -> float:
        """How long to wait for the first telemetry after handing TM over."""
        return self._DELIVERY_FRAMES * self.frame_air_time_s

    @property
    def poll_interval_s(self) -> float:
        """Drain cadence — a fraction of a frame, clamped for sanity."""
        return min(0.25, max(0.02, self.frame_air_time_s / 4.0))

    def start(self):
        """Start the RF pipeline."""
        self.pipeline.set_link_in_view(True)
        self.pipeline.start()
        self.pipeline.set_transmitting(True)
        # Warm up: tick a few times to stabilize
        self._tick(5)
        self._flush_tm_to_pipeline()
        time.sleep(self.recovery_timeout_s)
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

    def _recover_and_parse(self, wait_s: Optional[float] = None) -> list:
        """Wait for pipeline processing, then recover and parse all packets.

        Two distinct waits, which an earlier version of this helper conflated:

        * **Acquisition** — until the *first* packet of the burst appears. The
          pipeline cannot deliver anything until the frame carrying it has
          been transmitted, so this is bounded by `recovery_timeout_s`
          (frames, not seconds) and nothing shortens it. Counting empty polls
          against this budget made the harness give up 0.7 s after the flush
          however slow the link was — only 2.2 frame air-times at the 32 kBd
          the harness runs at, and less than ONE frame at the ladder's 16 kbps
          LOW tier. That cliff is what made a command's S1.1 occasionally
          "vanish" on a loaded machine.
        * **Drain** — once telemetry is flowing, stop as soon as the link has
          been silent for `_QUIET_FRAMES` frames, so a quiet link does not
          cost the full timeout.
        """
        if wait_s is None:
            wait_s = self.recovery_timeout_s
        poll = self.poll_interval_s
        quiet_polls_needed = max(2, math.ceil(
            self._QUIET_FRAMES * self.frame_air_time_s / poll))

        # Feed TM to pipeline
        self._flush_tm_to_pipeline()

        deadline = time.monotonic() + wait_s
        all_parsed = []
        quiet_polls = 0
        seen_any = False
        while time.monotonic() < deadline:
            raw_pkts = self._drain_recovered()
            if raw_pkts:
                seen_any = True
                quiet_polls = 0
                for raw in raw_pkts:
                    parsed = decommutate_packet(raw)
                    if parsed and parsed.secondary:
                        all_parsed.append(parsed)
            elif seen_any:
                quiet_polls += 1
                if quiet_polls >= quiet_polls_needed:
                    break  # burst over — the link has gone quiet
            time.sleep(poll)
        return all_parsed

    def send_command(self, service: int, subtype: int,
                     data: bytes = b"", name: str = "",
                     ticks: int = 5,
                     wait_s: Optional[float] = None) -> dict:
        """Send a TC packet through the engine, feed TM through RF pipeline,
        and return parsed responses.

        `wait_s` defaults to `recovery_timeout_s`, i.e. it is derived from the
        link rate the pipeline is running at. Do not pass a literal here.

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
        seq = self._seq_count
        tc = build_tc_packet(APP_APID, service, subtype, data,
                             seq_count=seq)
        self._seq_count += 1
        self.commands_sent += 1
        self.engine.tc_queue.put_nowait(tc)

        # Tick engine to process TC and generate responses
        self._tick(ticks)

        # Feed all TM through RF pipeline and recover
        parsed = self._recover_and_parse(wait_s)

        # Classify responses. Verification packets are matched on the PUS
        # request ID (TC APID + sequence count, ECSS-E-ST-70-41C §6.1), so an
        # ACK left over from an EARLIER command cannot be mistaken for this
        # one's. Without that check the harness reports success whenever any
        # S1.1 happens to be in the window, which is exactly the condition a
        # backlogged link produces — the acceptance suite would then stop
        # detecting the telemetry loss it exists to detect.
        request_id = ((APP_APID & 0x7FF) << 14) | (seq & 0x3FFF)

        def _matches(p, subtype_wanted: int) -> bool:
            if p.secondary.service != 1 or p.secondary.subtype != subtype_wanted:
                return False
            if len(p.data_field) < 4:
                return False
            return struct.unpack('>I', p.data_field[:4])[0] == request_id

        ack_11 = any(_matches(p, 1) for p in parsed)
        ack_17 = any(_matches(p, 7) for p in parsed)
        ack_12 = any(_matches(p, 2) for p in parsed)
        ack_18 = any(_matches(p, 8) for p in parsed)

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
                                  name=f"HK_SID{sid}", ticks=3)
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

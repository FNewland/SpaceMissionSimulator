"""SMO RF Simulation Bridge — main server.

Sits between the spacecraft and ground station, applying CCSDS Transfer
Framing and channel impairments depending on the operating mode.

PACKET mode: transparent TCP relay (no processing)
FRAME mode:  CCSDS TF framing + BER bit-flip injection (byte-level)
RF mode:     Continuous baseband signal processing pipeline
             (real modulation, channel, carrier/clock recovery)
"""

import argparse
import asyncio
import json
import logging
import struct
import time
from pathlib import Path
from typing import Optional

from smo_common.protocol.framing import frame_packet, read_framed_packet

from .config import RFSimConfig
from .mode import RFSimMode
from .ccsds.asm import attach_asm, ASM_LENGTH
from .ccsds.tm_frame import TMFrameBuilder, TMFrameParser
from .ccsds.tc_cltu import encode_cltu, decode_cltu
from .ccsds.frame_sync import FrameSynchronizer, SyncState
from .ccsds.virtual_channel import VCMultiplexer, VCDemultiplexer
from .channel.model import ChannelModel
from .radio.frontend import RadioFrontend, LockState

logger = logging.getLogger(__name__)


class RFSimBridge:
    """Main bridge server that relays TM/TC between spacecraft and ground station."""

    def __init__(self, config: RFSimConfig):
        self.config = config
        self.mode = config.mode

        # FRAME mode components (byte-level processing)
        self._frame_builder: Optional[TMFrameBuilder] = None
        self._frame_parser: Optional[TMFrameParser] = None
        self._frame_sync: Optional[FrameSynchronizer] = None
        self._vc_mux: Optional[VCMultiplexer] = None
        self._vc_demux: Optional[VCDemultiplexer] = None
        self._channel: Optional[ChannelModel] = None

        # RF mode: continuous signal processing pipeline
        self._pipeline = None

        # Radio frontend (observational)
        self.radio = RadioFrontend()
        self.radio.status.mode = self.mode.value

        # Connection state
        self._sim_tm_reader: Optional[asyncio.StreamReader] = None
        self._sim_tm_writer: Optional[asyncio.StreamWriter] = None
        self._sim_tc_reader: Optional[asyncio.StreamReader] = None
        self._sim_tc_writer: Optional[asyncio.StreamWriter] = None
        self._mcs_clients_tm: list[asyncio.StreamWriter] = []
        self._running = False

        # Stats
        self._tm_packets_relayed = 0
        self._tc_packets_relayed = 0
        self._tm_packets_delivered = 0
        self._sim_eb_n0: float = config.channel.eb_n0_db
        self._sim_rssi: float = -120.0

        if self.mode == RFSimMode.FRAME:
            self._init_frame_mode()
        elif self.mode == RFSimMode.RF:
            self._init_rf_mode()

    def _init_frame_mode(self):
        """Initialize FRAME mode (byte-level CCSDS processing)."""
        cc = self.config.ccsds
        self._frame_builder = TMFrameBuilder(
            scid=cc.scid, frame_length=cc.tm_frame_length,
            fecf_present=cc.fecf_present)
        self._frame_parser = TMFrameParser(
            frame_length=cc.tm_frame_length, fecf_present=cc.fecf_present)
        self._frame_sync = FrameSynchronizer(
            frame_length=cc.tm_frame_length)
        self._vc_mux = VCMultiplexer(
            scid=cc.scid, frame_length=cc.tm_frame_length,
            fecf_present=cc.fecf_present)
        self._vc_demux = VCDemultiplexer()
        self._channel = ChannelModel(
            eb_n0_db=self.config.channel.eb_n0_db)

    def _init_rf_mode(self):
        """Initialize RF mode (continuous signal processing pipeline)."""
        from .pipeline.coordinator import PipelineCoordinator
        self._pipeline = PipelineCoordinator(self.config)

    async def start(self):
        """Start the bridge."""
        self._running = True
        net = self.config.network
        logger.info("Starting RF bridge in %s mode", self.mode.value)

        # Connect to spacecraft TM port
        try:
            self._sim_tm_reader, self._sim_tm_writer = await asyncio.open_connection(
                net.sim_tm_host, net.sim_tm_port)
            logger.info("Connected to spacecraft TM at %s:%d",
                        net.sim_tm_host, net.sim_tm_port)
        except OSError as e:
            logger.error("Cannot connect to spacecraft TM: %s", e)
            return

        # Start RF pipeline threads (RF mode only)
        if self._pipeline:
            self._pipeline.start()

        # Start ground-station-facing servers
        tm_server = await asyncio.start_server(
            self._handle_mcs_tm_client, net.mcs_bind, net.mcs_tm_port)
        tc_server = await asyncio.start_server(
            self._handle_mcs_tc_client, net.mcs_bind, net.mcs_tc_port)
        logger.info("MCS TM server listening on %s:%d", net.mcs_bind, net.mcs_tm_port)
        logger.info("MCS TC server listening on %s:%d", net.mcs_bind, net.mcs_tc_port)

        # Start async tasks
        tasks = [
            asyncio.create_task(self._relay_tm()),
            asyncio.create_task(self._radio_updater()),
            asyncio.create_task(self._sim_ws_feedback()),
        ]
        # RF mode: relay recovered packets + monitor TX state
        if self._pipeline:
            tasks.append(asyncio.create_task(self._relay_recovered_tm()))
            tasks.append(asyncio.create_task(self._monitor_tx_state()))

        try:
            async with tm_server, tc_server:
                await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            if self._pipeline:
                self._pipeline.stop()

    # ── MCS client handling ──

    async def _handle_mcs_tm_client(self, reader: asyncio.StreamReader,
                                     writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        logger.info("MCS TM client connected: %s", addr)
        self._mcs_clients_tm.append(writer)
        try:
            await reader.read(-1)
        except Exception:
            pass
        finally:
            self._mcs_clients_tm.remove(writer)
            writer.close()
            logger.info("MCS TM client disconnected: %s", addr)

    async def _handle_mcs_tc_client(self, reader: asyncio.StreamReader,
                                     writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        logger.info("MCS TC client connected: %s", addr)
        try:
            while self._running:
                packet = await read_framed_packet(reader)
                if packet is None:
                    break
                await self._forward_tc(packet)
        except Exception as e:
            logger.debug("MCS TC client error: %s", e)
        finally:
            writer.close()
            logger.info("MCS TC client disconnected: %s", addr)

    # ── TC uplink ──

    async def _forward_tc(self, packet: bytes):
        if self.mode == RFSimMode.PACKET:
            await self._send_to_sim_tc(packet)
        elif self.mode == RFSimMode.RF and self._pipeline:
            # Process TC through the uplink RF chain
            decoded = self._pipeline.process_tc(packet)
            if decoded is not None:
                await self._send_to_sim_tc(decoded)
                self.radio.update_cltu(
                    self.radio.status.cltu_sent + 1,
                    self.radio.status.cltu_acked + 1)
            else:
                logger.warning("TC uplink decode failed — command dropped")
                self.radio.update_cltu(
                    self.radio.status.cltu_sent + 1,
                    self.radio.status.cltu_acked)
        elif self.mode == RFSimMode.FRAME:
            cltu = encode_cltu(packet)
            if self._channel:
                cltu = self._channel.impair(cltu)
            decoded = decode_cltu(cltu)
            if decoded is not None:
                await self._send_to_sim_tc(decoded[:len(packet)])
                self.radio.update_cltu(
                    self.radio.status.cltu_sent + 1,
                    self.radio.status.cltu_acked + 1)
            else:
                self.radio.update_cltu(
                    self.radio.status.cltu_sent + 1,
                    self.radio.status.cltu_acked)
        self._tc_packets_relayed += 1

    async def _send_to_sim_tc(self, packet: bytes):
        try:
            if self._sim_tc_writer is None or self._sim_tc_writer.is_closing():
                net = self.config.network
                self._sim_tc_reader, self._sim_tc_writer = \
                    await asyncio.open_connection(net.sim_tc_host, net.sim_tc_port)
            self._sim_tc_writer.write(frame_packet(packet))
            await self._sim_tc_writer.drain()
        except OSError as e:
            logger.error("Failed to send TC to spacecraft: %s", e)

    # ── TM downlink ──

    async def _relay_tm(self):
        """Read TM from spacecraft and route based on mode.

        The sim only sends TM packets when its downlink_active property is
        True (contact + transponder OK, or override). So receiving a packet
        here is proof the sim considers the link up. We use this to drive
        the pipeline TX state — no WS feedback needed.
        """
        last_packet_time = 0.0
        while self._running:
            try:
                packet = await read_framed_packet(self._sim_tm_reader)
                if packet is None:
                    logger.warning("Spacecraft TM connection lost")
                    break

                if self.mode == RFSimMode.PACKET:
                    await self._broadcast_tm(packet)
                elif self.mode == RFSimMode.RF and self._pipeline:
                    # Enable TX if not already on AND we have line of sight.
                    # The sim's downlink_active gate means receiving a packet
                    # here proves the sim thinks the link is up. But the
                    # bridge's _sim_ws_feedback may have already set
                    # _link_in_view=False (LOS), in which case we should
                    # not re-enable TX — the packet is a stale remnant.
                    if not self._pipeline._tx._transmitting:
                        if self._pipeline._link_in_view:
                            logger.info("TM packet received — enabling pipeline TX")
                            self._pipeline.set_transmitting(True)
                        else:
                            logger.debug("TM packet received but no LOS — TX stays off")
                    last_packet_time = asyncio.get_event_loop().time()
                    self._pipeline.enqueue_tm_packet(packet)
                elif self.mode == RFSimMode.FRAME:
                    await self._process_tm_frame_mode(packet)

                self._tm_packets_relayed += 1
            except Exception as e:
                logger.error("TM relay error: %s", e)
                await asyncio.sleep(0.1)

    async def _monitor_tx_state(self):
        """RF mode: disable TX when TM packets stop flowing from the sim.

        The sim stops sending TM when the pass ends or the transponder
        is disabled. We detect this by watching for gaps in the TM stream.
        """
        if not self._pipeline:
            return
        while self._running:
            await asyncio.sleep(3.0)
            # If no TM packets received recently and TX is on, disable it
            if (self._pipeline._tx._transmitting and
                    self._tm_packets_relayed > 0):
                # Check if the sim's state says no contact
                # (WS feedback updates _link_in_view)
                if not self._pipeline._link_in_view:
                    logger.info("No contact — disabling pipeline TX")
                    self._pipeline.set_transmitting(False)

    async def _relay_recovered_tm(self):
        """RF mode: relay packets recovered by the RX pipeline to MCS clients."""
        while self._running:
            packet = await self._pipeline.get_recovered_packet()
            if packet is not None:
                await self._broadcast_tm(packet)

    async def _process_tm_frame_mode(self, packet: bytes):
        """FRAME mode: byte-level CCSDS framing (no real signal processing)."""
        self._vc_mux.add_packet(packet, vcid=0)
        while self._vc_mux.has_data():
            frame = self._vc_mux.get_next_frame()
            frame_bytes = frame.header.pack() + frame.data
            if frame.fecf is not None:
                frame_bytes += struct.pack('>H', frame.fecf)
            wire_bytes = attach_asm(frame_bytes)
            impaired = self._channel.impair(wire_bytes)
            raw_frames = self._frame_sync.feed(impaired)
            for raw_frame in raw_frames:
                parsed = self._frame_parser.parse_frame(raw_frame)
                if parsed is None:
                    continue
                self.radio.update_vc_activity(parsed.header.vcid)
                self._vc_demux.process_frame(parsed)
                packets = self._frame_parser.extract_packets(parsed)
                for pkt in packets:
                    await self._broadcast_tm(pkt)
        if self._frame_parser:
            self.radio.update_frame_counts(
                self._frame_parser.good_frames,
                self._frame_parser.bad_frames)

    async def _broadcast_tm(self, packet: bytes):
        framed = frame_packet(packet)
        dead = []
        for writer in self._mcs_clients_tm:
            try:
                writer.write(framed)
                await writer.drain()
            except Exception:
                dead.append(writer)
        for w in dead:
            self._mcs_clients_tm.remove(w)
        self._tm_packets_delivered += 1

    # ── Radio status updates ──

    async def _radio_updater(self):
        while self._running:
            if self.mode == RFSimMode.RF and self._pipeline:
                # RF mode: all indicators come from the real receiver
                eb_n0 = self.config.channel.eb_n0_db
                self.radio.update_rf(
                    eb_n0, self.config.channel.doppler_hz,
                    getattr(self.config.channel, 'range_km', 0.0),
                    self._sim_rssi)

                # Lock state from actual receiver signal processing
                carrier = self._pipeline.carrier_locked
                clock = self._pipeline.clock_locked
                frame = self._pipeline.frame_locked
                self.radio.update_lock(
                    LockState.LOCKED if carrier else LockState.UNLOCKED,
                    LockState.LOCKED if clock else LockState.UNLOCKED,
                    LockState.LOCKED if frame else LockState.UNLOCKED)

                # Constellation from actual Costas loop output
                self.radio.status.iq_samples = self._pipeline.get_constellation()

                # Frame counts from actual FEC decoder
                self.radio.update_frame_counts(
                    self._pipeline.good_frames,
                    self._pipeline.bad_frames)

            elif self.mode == RFSimMode.FRAME:
                eb_n0 = self.config.channel.eb_n0_db
                if self._channel:
                    self._channel.eb_n0_db = eb_n0
                self.radio.update_rf(
                    eb_n0, self.config.channel.doppler_hz,
                    getattr(self.config.channel, 'range_km', 0.0),
                    self._sim_rssi)
                if self._frame_sync:
                    fs = self._frame_sync.state
                    if fs == SyncState.LOCK:
                        self.radio.update_lock(LockState.LOCKED,
                                                LockState.LOCKED,
                                                LockState.LOCKED)
                    elif fs == SyncState.VERIFY:
                        self.radio.update_lock(LockState.LOCKED,
                                                LockState.LOCKED,
                                                LockState.ACQUIRING)
                    else:
                        self.radio.update_lock(LockState.ACQUIRING,
                                                LockState.UNLOCKED,
                                                LockState.UNLOCKED)
            else:
                # PACKET mode
                self.radio.update_lock(LockState.LOCKED, LockState.LOCKED,
                                        LockState.LOCKED)
                self.radio.update_rf(99.0)

            await asyncio.sleep(1.0)

    # ── Sim WS feedback ──

    async def _sim_ws_feedback(self):
        try:
            import aiohttp
        except ImportError:
            logger.warning("aiohttp not available, skipping WS feedback")
            return

        ws_url = self.config.network.sim_ws_url
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url) as ws:
                        logger.info("Connected to spacecraft WS at %s", ws_url)
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                    self._apply_sim_feedback(data)
                                except (json.JSONDecodeError, KeyError):
                                    pass
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                break
            except Exception as e:
                logger.warning("WS feedback connection failed: %s (retrying in 5s)", e)
            await asyncio.sleep(5.0)

    def _apply_sim_feedback(self, data: dict):
        state = data.get("state", data)
        ttc = state.get("ttc", {})
        if not ttc:
            return

        # Eb/N0 (ignore 0 = not yet computed)
        eb_n0 = ttc.get("eb_n0_db")
        if eb_n0 is not None and float(eb_n0) != 0.0:
            self._sim_eb_n0 = float(eb_n0)
            self.config.channel.eb_n0_db = float(eb_n0)
            if self._channel:
                self._channel.eb_n0_db = float(eb_n0)
            if self._pipeline:
                self._pipeline.set_eb_n0(float(eb_n0))

        # Doppler
        doppler = ttc.get("doppler_hz")
        if doppler is not None:
            self.config.channel.doppler_hz = float(doppler)
            if self._pipeline:
                self._pipeline.set_doppler(float(doppler))

        # Link in view (determines if RF path exists — line of sight).
        # Driven by contact state (orbital geometry or pass override).
        in_contact = state.get("in_contact") or state.get("override_passes")
        if in_contact is not None and self._pipeline:
            self._pipeline.set_link_in_view(bool(in_contact))

        # Transmitter state: the spacecraft PA produces a carrier whenever
        # it's powered on and the spacecraft is in view. The link_status
        # from the sim's internal TTC model is NOT used here — that would
        # be circular (the sim models lock acquisition, but in RF mode the
        # actual lock comes from the pipeline's real signal processing).
        # TX state is driven by TM packet flow (see _relay_tm), not by
        # WS fields. The sim's _enqueue_tm already gates on downlink_active.

        # Lock state for Radio display (from the sim's TTC model,
        # NOT from the pipeline RX — the pipeline RX provides its own
        # lock state in the radio_updater for RF mode)
        if self.mode == RFSimMode.FRAME:
            carrier = ttc.get("carrier_lock")
            bit_sync = ttc.get("bit_sync")
            frame_sync = ttc.get("frame_sync")
            if carrier is not None:
                self.radio.update_lock(
                    LockState.LOCKED if carrier else LockState.UNLOCKED,
                    LockState.LOCKED if bit_sync else LockState.UNLOCKED,
                    LockState.LOCKED if frame_sync else LockState.UNLOCKED)

        # Modulation mode
        mod_mode = ttc.get("modulation_mode")
        if mod_mode is not None:
            self.radio.update_modulation(int(mod_mode))
            if self._pipeline:
                self._pipeline.reconfigure(modulation=int(mod_mode))

        # Ground segment penalty
        gs_penalty = ttc.get("gs_penalty_db")
        if gs_penalty is not None:
            self.radio.update_gs_penalty(float(gs_penalty))

        # RSSI, range, data rate
        rssi = ttc.get("rssi_dbm")
        if rssi is not None:
            self._sim_rssi = float(rssi)
        range_km = ttc.get("range_km")
        if range_km is not None:
            self.config.channel.range_km = float(range_km)
        data_rate = ttc.get("data_rate_bps")
        if data_rate is not None:
            self.radio.status.data_rate_kbps = float(data_rate) / 1000.0

        # Push RF updates to Radio
        self.radio.update_rf(
            self.config.channel.eb_n0_db,
            self.config.channel.doppler_hz,
            getattr(self.config.channel, 'range_km', 0.0),
            self._sim_rssi)

    async def stop(self):
        self._running = False
        if self._pipeline:
            self._pipeline.stop()
        for w in self._mcs_clients_tm:
            w.close()
        if self._sim_tm_writer:
            self._sim_tm_writer.close()
        if self._sim_tc_writer:
            self._sim_tc_writer.close()


def main():
    parser = argparse.ArgumentParser(description="SMO RF Bridge")
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--mode", type=str, default=None,
                        choices=["PACKET", "FRAME", "RF"])
    parser.add_argument("--eb-n0", type=float, default=None)
    parser.add_argument("--radio-ui", action="store_true")
    parser.add_argument("--radio-web", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    if args.config:
        config = RFSimConfig.from_yaml(Path(args.config))
    else:
        config = RFSimConfig()

    if args.mode:
        config.mode = RFSimMode(args.mode)
    if args.eb_n0 is not None:
        config.channel.eb_n0_db = args.eb_n0

    bridge = RFSimBridge(config)

    async def run():
        tasks = [asyncio.create_task(bridge.start())]
        if args.radio_ui:
            from .radio.terminal_ui import run_terminal_ui
            tasks.append(asyncio.create_task(run_terminal_ui(bridge.radio)))
        if args.radio_web:
            from .radio.web_ui import run_web_ui
            tasks.append(asyncio.create_task(
                run_web_ui(bridge.radio, port=config.network.radio_port)))
        await asyncio.gather(*tasks)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Bridge shutting down")


if __name__ == "__main__":
    main()

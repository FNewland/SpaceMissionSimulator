"""Pipeline coordinator — wires TX → Channel → RX and manages lifecycle.

Provides the interface that bridge.py uses for RF mode: enqueue TM
packets from the sim, get recovered packets for MCS, read constellation
and lock state for Radio, and process TC uplink.
"""

import asyncio
import logging
import queue
import threading
from typing import Optional

import numpy as np

from ..ccsds.tc_cltu import encode_cltu, decode_cltu
from ..config import RFSimConfig
from ..channel.space_link import SpaceLinkConfig
from ..dsp.modulator import Modulator, BITS_PER_SYMBOL
from ..dsp.channel import BasebandChannel
from ..dsp.demodulator import Demodulator

from .sample_buffer import SampleBuffer
from .tx_chain import SpacecraftTX
from .channel_stage import ChannelStage
from .rx_chain import GroundStationRX

logger = logging.getLogger(__name__)


class PipelineCoordinator:
    """Orchestrates the continuous RF signal processing pipeline.

    Creates three threads (TX, Channel, RX) connected by sample buffers.
    Provides async-safe methods for the bridge to interact with.
    """

    def __init__(self, config: RFSimConfig):
        self.config = config
        cc = config.ccsds
        ch = config.channel
        pc = config.pipeline

        # Pipeline parameters (config.pipeline is a real section since the
        # 2026-07 fixes — it used to be absent, pinning the pipeline at
        # 32 kBd / BPSK forever. See defect #55.)
        symbol_rate = pc.symbol_rate
        sps = pc.sps
        rolloff = pc.rrc_rolloff
        buf_depth = pc.sample_buffer_depth
        modulation = pc.modulation
        self._symbol_rate = symbol_rate
        self._sps = sps

        # Sample buffers between stages
        self._tx_to_channel = SampleBuffer(max_depth=buf_depth, name="tx→ch")
        self._channel_to_rx = SampleBuffer(max_depth=buf_depth, name="ch→rx")

        # Packet queues
        self._tm_packet_queue: queue.Queue[bytes] = queue.Queue(maxsize=500)
        self._recovered_queue: queue.Queue[bytes] = queue.Queue(maxsize=500)
        self._recovered_queue_drops: int = 0

        # TX stage
        self._tx = SpacecraftTX(
            scid=cc.scid,
            frame_length=cc.tm_frame_length,
            fecf_present=cc.fecf_present,
            modulation=modulation,
            symbol_rate=symbol_rate,
            sps=sps,
            rolloff=rolloff,
            rs_enabled=cc.rs_enabled,
            conv_enabled=cc.convolutional_enabled,
            output_buffer=self._tx_to_channel,
            packet_queue=self._tm_packet_queue)

        # Channel stage — full SpaceLinkChannel impairment chain
        bps = BITS_PER_SYMBOL.get(modulation, 1)
        zmq_port = getattr(config.network, 'zmq_samples_port', 0)
        self._channel = ChannelStage(
            eb_n0_db=ch.eb_n0_db,
            sps=sps,
            bits_per_symbol=bps,
            freq_offset_hz=ch.doppler_hz,
            sample_rate=symbol_rate * sps,
            input_buffer=self._tx_to_channel,
            output_buffer=self._channel_to_rx,
            zmq_pub_port=zmq_port,
            link_config=self._build_link_config())

        # Link budget (CCSDS 401.0-B) — drives the radio UI Link Budget panel
        self._link_budget = self._channel.channel.link_budget

        # RX stage
        self._rx = GroundStationRX(
            frame_length=cc.tm_frame_length,
            fecf_present=cc.fecf_present,
            modulation=modulation,
            sps=sps,
            rolloff=rolloff,
            rs_enabled=cc.rs_enabled,
            conv_enabled=cc.convolutional_enabled,
            input_buffer=self._channel_to_rx,
            packet_callback=self._on_packet_recovered,
            loop_bw=pc.rx_loop_bw,
            symbol_rate=symbol_rate,
            gray_coding=pc.gray_coding,
            gmsk_bt=pc.gmsk_bt)

        # TC uplink (synchronous, not pipelined — commands are infrequent).
        # The uplink is independent of the downlink: the ground station HPA
        # is powerful and the spacecraft RX is always listening (dedicated PDM).
        # Uplink Eb/N0 is typically 15-20 dB better than downlink because
        # the ground station transmits at much higher power than the cubesat.
        tc_sr = pc.tc_symbol_rate
        tc_sps = pc.tc_sps
        self._tc_modulator = Modulator(modulation=0, sps=tc_sps, rolloff=0.5,
                                       gray_coding=pc.gray_coding)
        self._tc_uplink_eb_n0 = pc.tc_uplink_eb_n0_db
        self._tc_channel = BasebandChannel(
            eb_n0_db=self._tc_uplink_eb_n0, sps=tc_sps, bits_per_symbol=1,
            sample_rate=tc_sr * tc_sps)
        self._tc_demodulator = Demodulator(modulation=0, sps=tc_sps, loop_bw=0.02)
        self._link_in_view = False  # spacecraft in view (pass active)
        self._active_station: Optional[str] = None

    def _build_link_config(self) -> "SpaceLinkConfig":
        """Translate the rfsim.yaml channel/link_budget sections into the
        SpaceLinkChannel configuration dataclass."""
        ch = self.config.channel
        lb = self.config.link_budget
        pc = self.config.pipeline
        return SpaceLinkConfig(
            eirp_dbw=lb.eirp_dbw,
            frequency_hz=ch.frequency_hz,
            gs_gt_dbk=lb.gt_for_station(lb.default_ground_station),
            atmospheric_loss_db=lb.atmospheric_loss_db,
            implementation_loss_db=lb.implementation_loss_db,
            polarization_loss_db=lb.polarization_loss_db,
            coding_gain_db=lb.coding_gain_db,
            awgn_enabled=ch.awgn_enabled,
            eb_n0_db=ch.eb_n0_db,
            phase_noise_enabled=ch.phase_noise_enabled,
            phase_noise_linewidth_hz=ch.phase_noise_linewidth_hz,
            fading_enabled=ch.fading_enabled,
            fading_k_factor_db=ch.fading_k_factor_db,
            fading_max_doppler_hz=ch.fading_max_doppler_hz,
            multipath_enabled=ch.multipath_enabled,
            multipath_taps=list(ch.multipath_taps),
            interferers=[dict(i) for i in ch.interferers],
            doppler_hz=ch.doppler_hz,
            sample_rate=pc.symbol_rate * pc.sps,
            sps=pc.sps,
            bits_per_symbol=BITS_PER_SYMBOL.get(pc.modulation, 1),
            seed=ch.seed,
        )

    def start(self):
        """Start all three processing threads."""
        logger.info("Starting RF pipeline: TX → Channel → RX")
        self._tx.start()
        self._channel.start()
        self._rx.start()

    def stop(self):
        """Stop all threads (TX first, then channel, then RX)."""
        logger.info("Stopping RF pipeline")
        self._tx.stop()
        self._channel.stop()
        self._rx.stop()
        # Wait for threads to finish
        if self._tx.is_alive():
            self._tx.join(timeout=2.0)
        if self._channel.is_alive():
            self._channel.join(timeout=2.0)
        if self._rx.is_alive():
            self._rx.join(timeout=2.0)

    # ── Link timing (what the pipeline can promise a caller) ──

    @property
    def channel_bit_rate_bps(self) -> float:
        """Coded bit rate on the air: symbol rate x bits per symbol.

        Follows the active data-rate tier — ``reconfigure()`` and
        ``set_data_rate_bps()`` both update it — so callers that need to
        reason about link timing never have to hard-code a rate.
        """
        return self._symbol_rate * BITS_PER_SYMBOL.get(
            self.config.pipeline.modulation, 1)

    @property
    def frame_air_time_s(self) -> float:
        """Wall-clock seconds to put one coded TM transfer frame on the air.

        The downlink is framed, so this is the *quantum* in which recovered
        telemetry appears at the far end: a packet handed to
        ``enqueue_tm_packet()`` cannot be recovered sooner than the frame
        carrying it takes to transmit, and in the worst case it waits for the
        frame already in progress as well. Anything that waits on recovered
        telemetry must scale its timeout by this, not by a constant — at the
        ladder's LOW tier (16 kbps BPSK conv+RS) a frame takes 1.28 s, at the
        HIGH tier (128 kbps QPSK RS) 0.08 s, a factor of 16.
        """
        return self._tx.frame_interval

    # ── TM downlink (sim → pipeline → MCS) ──

    def enqueue_tm_packet(self, packet: bytes) -> None:
        """Feed a TM packet from the simulator into the TX chain."""
        self._tx.enqueue_packet(packet)

    def _on_packet_recovered(self, packet: bytes) -> None:
        """Callback from RX thread when a packet is recovered."""
        try:
            self._recovered_queue.put_nowait(packet)
        except queue.Full:
            self._recovered_queue_drops += 1
            logger.warning("Recovered packet dropped: queue full (%d)",
                           self._recovered_queue.maxsize)

    async def get_recovered_packet(self) -> Optional[bytes]:
        """Async get a recovered packet for forwarding to MCS."""
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, self._recovered_queue.get, True, 0.5),
                timeout=1.0)
        except (asyncio.TimeoutError, queue.Empty):
            return None

    def drain_recovered_packets(self, max_count: int = 100) -> list[bytes]:
        """Non-blocking batch drain of all available recovered packets.

        Much faster than repeated get_recovered_packet() calls because
        it avoids the async/executor overhead per packet. Use this from
        synchronous code or when you need to catch up with a burst.
        """
        packets = []
        for _ in range(max_count):
            try:
                packets.append(self._recovered_queue.get_nowait())
            except queue.Empty:
                break
        return packets

    # ── TC uplink (MCS → pipeline → sim) ──

    def process_tc(self, tc_packet: bytes) -> Optional[bytes]:
        """Process a TC packet through the uplink RF chain (synchronous).

        The uplink is independent of the downlink. It works whenever the
        spacecraft is in view — regardless of PA state, antenna deployment,
        or downlink lock. The ground station HPA is powerful enough to
        reach the spacecraft even with a stowed antenna.

        CLTU encode → modulate → channel → demodulate → CLTU decode.
        """
        if not self._link_in_view:
            logger.info("TC uplink blocked: spacecraft not in view")
            return None

        # CLTU encode
        cltu = encode_cltu(tc_packet)

        # Modulate (always BPSK at low rate for uplink)
        samples = self._tc_modulator.modulate(cltu)

        # Channel (uplink has its own Eb/N0 — ground HPA is strong)
        impaired = self._tc_channel.process(samples)

        # Reset demodulator for each independent command — TC commands
        # are not a continuous stream; each CLTU is freshly modulated
        # starting from phase 0. Stale PLL state from the previous
        # command would cause phase misalignment.
        self._tc_demodulator.reset()

        # Demodulate
        recovered = self._tc_demodulator.demodulate(impaired)

        # CLTU decode
        if not recovered:
            logger.info("TC uplink: demodulation produced no bytes")
            return None
        decoded = decode_cltu(recovered, correct_errors=True)
        if decoded is None:
            logger.info("TC uplink: CLTU decode failed (CRC error)")
            return None
        return decoded[:len(tc_packet)]

    # ── Dynamic parameter updates (called from bridge main thread) ──

    def set_eb_n0(self, db: float):
        """Set downlink Eb/N0. Does NOT affect the uplink — the ground
        station HPA provides an independent, strong uplink."""
        self._channel.set_eb_n0(db)

    def set_doppler(self, hz: float):
        self._channel.set_freq_offset(hz)
        # Feed known Doppler to RX for pre-compensation
        self._rx._demod.set_doppler(hz)

    def set_link_in_view(self, in_view: bool):
        """Set whether the spacecraft is in view of the ground station.

        When in view: uplink commands can reach the spacecraft.
        When not in view: uplink blocked, TX off, channel outputs noise.
        """
        self._link_in_view = in_view
        if not in_view:
            # No line of sight — disable TX and channel
            self._tx.set_transmitting(False)
            self._channel.set_link_active(False)
        else:
            self._channel.set_link_active(self._tx._transmitting)

    def set_transmitting(self, on: bool):
        """Set whether the spacecraft PA is on (downlink carrier present).

        Called from bridge when the sim reports PA state changes.
        """
        self._tx.set_transmitting(on)
        # Channel passes signal only when in view AND TX is on
        self._channel.set_link_active(self._link_in_view and on)

    # Keep old name as alias for bridge compatibility
    def set_link_active(self, active: bool):
        self.set_link_in_view(active)

    def reconfigure(self, modulation: int = None, symbol_rate: float = None):
        """Change modulation and/or symbol rate mid-stream, TX **and** RX.

        A symbol-rate change now propagates all the way through: the TX
        frame cadence, the channel sample rate (so Eb/N0 and Doppler stay
        correctly scaled) and the RX Doppler pre-compensation. Previously
        only the TX saw it — and nothing ever called it with a rate at all.
        """
        if modulation is not None:
            self._tx.reconfigure(modulation=modulation)
            self._rx.reconfigure(modulation=modulation)
            bps = BITS_PER_SYMBOL.get(modulation, 1)
            self._channel.set_bits_per_symbol(bps)
            self.config.pipeline.modulation = int(modulation)
        if symbol_rate is not None:
            symbol_rate = float(symbol_rate)
            pc = self.config.pipeline
            symbol_rate = max(pc.min_symbol_rate,
                              min(pc.max_symbol_rate, symbol_rate))
            if symbol_rate != self._symbol_rate:
                self._symbol_rate = symbol_rate
                pc.symbol_rate = symbol_rate
                self._tx.reconfigure(symbol_rate=symbol_rate)
                self._channel.set_sample_rate(symbol_rate * self._sps)
                self._rx.reconfigure(symbol_rate=symbol_rate)
                logger.info("Pipeline symbol rate → %.0f Bd (%.1f kHz occupied)",
                            symbol_rate, symbol_rate * 1.35 / 1000.0)

    def set_data_rate_bps(self, bit_rate_bps: float) -> float:
        """Retune the pipeline to carry a commanded TM bit rate.

        symbol_rate = bit_rate / bits_per_symbol. This is what makes a
        high/low rate change *observable*: the occupied bandwidth on the
        spectrum analyser and the eye timing both follow it.
        Returns the symbol rate actually applied.
        """
        if not self.config.pipeline.track_data_rate:
            return self._symbol_rate
        bps = BITS_PER_SYMBOL.get(self.config.pipeline.modulation, 1)
        symbol_rate = float(bit_rate_bps) / max(bps, 1)
        self.reconfigure(symbol_rate=symbol_rate)
        return self._symbol_rate

    @property
    def symbol_rate(self) -> float:
        return self._symbol_rate

    @property
    def sample_rate(self) -> float:
        return self._symbol_rate * self._sps

    # ── Impairments / channel model ──

    def set_impairments(self, **kwargs) -> dict:
        """Enable/disable/retune live RF impairments (fading, phase noise,
        interferers, AWGN). See ``SpaceLinkChannel.set_impairments``."""
        status = self._channel.set_impairments(**kwargs)
        ch = self.config.channel
        for key, value in kwargs.items():
            if value is not None and hasattr(ch, key):
                setattr(ch, key, value)
        return status

    def get_channel_status(self) -> dict:
        """Live channel-model state for the radio UI."""
        status = self._channel.get_channel_status()
        status["sample_rate"] = self.sample_rate
        status["symbol_rate"] = self._symbol_rate
        return status

    def set_ground_station(self, name: Optional[str]) -> float:
        """Point the link budget at the station currently in contact.

        Per-station G/T comes from the planner's ground_stations.yaml.
        """
        lb = self.config.link_budget
        gt = lb.gt_for_station(name)
        self._active_station = name
        self._link_budget.gs_gt_dbk = gt
        return gt

    def get_link_budget(self, range_km: float = None,
                        data_rate_bps: float = None,
                        elevation_deg: float = 90.0) -> dict:
        """Compute the current CCSDS 401.0-B link budget."""
        if range_km is None:
            range_km = getattr(self.config.channel, "range_km", 0.0) or 0.0
        if data_rate_bps is None:
            bits = BITS_PER_SYMBOL.get(self.config.pipeline.modulation, 1)
            data_rate_bps = self._symbol_rate * bits
        budget = self._link_budget.compute(range_km, data_rate_bps,
                                           elevation_deg=elevation_deg)
        budget["ground_station"] = self._active_station
        budget["gs_gt_dbk"] = self._link_budget.gs_gt_dbk
        return budget

    # ── Display taps ──

    def get_spectrum_samples(self, max_samples: int = 4096):
        """Post-impairment baseband samples for the spectrum analyser."""
        return self._channel.get_recent_samples(max_samples)

    def get_eye_samples(self, max_samples: int = 1024):
        """Oversampled post-matched-filter samples for the eye diagram."""
        return self._rx.get_eye_samples(max_samples)

    @property
    def eye_timing_offset(self) -> int:
        return self._rx.eye_timing_offset

    @property
    def sps(self) -> int:
        return self._sps

    @property
    def evm_db(self) -> float:
        return self._rx.evm_db

    # ── Status accessors for Radio ──

    @property
    def carrier_locked(self) -> bool:
        return self._rx.carrier_locked

    @property
    def clock_locked(self) -> bool:
        return self._rx.clock_locked

    @property
    def frame_locked(self) -> bool:
        return self._rx.frame_locked

    def get_constellation(self, max_points: int = 128) -> list[list[float]]:
        """Real demodulated I/Q points from the Costas loop output."""
        return self._rx.get_constellation(max_points)

    @property
    def good_frames(self) -> int:
        return self._rx.good_frames

    @property
    def bad_frames(self) -> int:
        return self._rx.bad_frames

    @property
    def flywheel_misses(self) -> int:
        return self._rx.flywheel_misses

    @property
    def tx_stats(self) -> dict:
        return {
            "frames_transmitted": self._tx.frames_transmitted,
            "data_frames": self._tx.data_frames,
            "idle_frames": self._tx.idle_frames,
            "tx_buffer_depth": self._tx_to_channel.depth,
            "rx_buffer_depth": self._channel_to_rx.depth,
            "tx_buffer_overflows": self._tx_to_channel.overflow_count,
            "rx_buffer_overflows": self._channel_to_rx.overflow_count,
            "tm_queue_depth": self._tm_packet_queue.qsize(),
            "recovered_queue_depth": self._recovered_queue.qsize(),
        }

    def get_diagnostics(self) -> dict:
        """Snapshot of all pipeline counters for loss diagnosis."""
        return {
            "tx_frames_transmitted": self._tx.frames_transmitted,
            "tx_data_frames": self._tx.data_frames,
            "tx_idle_frames": self._tx.idle_frames,
            "tx_packet_drops": self._tx.packet_drops,
            "tx_buffer_overflows": self._tx_to_channel.overflow_count,
            "rx_buffer_overflows": self._channel_to_rx.overflow_count,
            "rx_good_frames": self._rx.good_frames,
            "rx_bad_frames": self._rx.bad_frames,
            "rx_rs_failures": self._rx.rs_failures,
            "rx_fecf_failures": self._rx.fecf_failures,
            "rx_packets_recovered": self._rx.packets_recovered,
            "rx_flywheel_misses": self._rx.flywheel_misses,
            "rx_phase_nudges": self._rx.phase_nudges,
            "rx_mod_searches": self._rx.mod_searches,
            "rx_pll_resets": self._rx.pll_resets,
            "recovered_queue_drops": self._recovered_queue_drops,
            "tm_queue_depth": self._tm_packet_queue.qsize(),
            "recovered_queue_depth": self._recovered_queue.qsize(),
        }

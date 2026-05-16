"""Ground Station RX — continuous sample-to-packet receiver.

Runs in its own thread. Demodulates the continuous sample stream using
real carrier recovery (Costas PLL), clock recovery (Mueller & Muller),
then performs frame synchronization, Viterbi decoding, RS decoding,
and ECSS packet extraction.

The constellation tap, lock indicators, and BER all come from the
actual signal processing — not from formulas or synthetic data.
"""

import logging
import queue
import struct
import threading
from typing import Callable, Optional

import numpy as np

from ..ccsds.asm import ASM_LENGTH
from ..ccsds.frame_sync import FrameSynchronizer, SyncState
from ..ccsds.reed_solomon import decode as rs_decode
from ..ccsds.tm_frame import TMFrameParser
from ..ccsds.viterbi import decode as viterbi_decode
from ..dsp.correlator_rx import CorrelatorRX

from .sample_buffer import SampleBuffer

logger = logging.getLogger(__name__)


class GroundStationRX(threading.Thread):
    """Continuous ground station receiver with real signal processing."""

    def __init__(self, frame_length: int = 1115, fecf_present: bool = True,
                 modulation: int = 0, sps: int = 8, rolloff: float = 0.35,
                 rs_enabled: bool = True, conv_enabled: bool = True,
                 input_buffer: SampleBuffer = None,
                 packet_callback: Callable[[bytes], None] = None,
                 loop_bw: float = 0.01):
        super().__init__(daemon=True, name="GroundStationRX")
        self._running = False

        # Demodulator (matched-filter correlator receiver)
        self._demod = CorrelatorRX(sps=sps, rolloff=rolloff, modulation=modulation)

        # Coding config
        self._rs_enabled = rs_enabled
        self._conv_enabled = conv_enabled
        self._frame_length = frame_length  # uncoded frame length

        # Compute coded frame length (what the frame sync sees between ASMs)
        coded_len = frame_length
        if rs_enabled:
            n_blocks = (frame_length + 222) // 223
            coded_len = n_blocks * 255
        if conv_enabled:
            coded_len = coded_len * 2 + 2  # rate 1/2 + flush

        # Frame synchronizer searches for ASM at coded frame intervals
        self._frame_sync = FrameSynchronizer(frame_length=coded_len)
        self._coded_frame_length = coded_len

        # Frame parser (operates on decoded frames)
        self._frame_parser = TMFrameParser(
            frame_length=frame_length, fecf_present=fecf_present)

        # I/O
        self._input = input_buffer or SampleBuffer(name="rx_in")
        self._packet_callback = packet_callback

        # Byte reassembly buffer (demod may produce partial bytes at boundaries)
        self._byte_buffer = bytearray()

        # Stats
        self.good_frames = 0
        self.bad_frames = 0
        self.packets_recovered = 0
        self._lock = threading.Lock()

    @property
    def carrier_locked(self) -> bool:
        return self._demod.carrier_locked

    @property
    def clock_locked(self) -> bool:
        return self._demod.clock_locked

    @property
    def frame_locked(self) -> bool:
        return self._frame_sync.state == SyncState.LOCK

    @property
    def frame_sync_state(self) -> SyncState:
        return self._frame_sync.state

    def get_constellation(self, max_points: int = 128) -> list[list[float]]:
        """Return recent demodulated I/Q points from the Costas loop output."""
        return self._demod.get_constellation_iq(max_points)

    @property
    def measured_ber(self) -> float:
        """Frame error rate as a proxy for BER."""
        with self._lock:
            total = self.good_frames + self.bad_frames
        if total == 0:
            return 0.0
        return self.bad_frames / total

    def reconfigure(self, modulation: int):
        """Change modulation scheme (resets PLL/clock and frame sync state).

        Only resets if the modulation actually changed — repeated calls
        with the same value are no-ops so we don't destroy an active
        frame sync lock.
        """
        if modulation == self._demod._modulation:
            return
        self._demod.set_modulation(modulation)
        # Reset frame sync — its buffer contains bytes demodulated
        # under the old modulation which would prevent lock.
        self._frame_sync = FrameSynchronizer(frame_length=self._coded_frame_length)

    def run(self):
        self._running = True
        logger.info("GroundStationRX started")
        try:
            self._run_loop()
        except Exception as e:
            logger.error("GroundStationRX crashed: %s", e, exc_info=True)
        finally:
            self._running = False

    def _run_loop(self):
        while self._running:
            try:
                samples = self._input.get(timeout=0.1)
            except queue.Empty:
                continue

            # 1. Demodulate: AGC → matched filter → Costas PLL → M&M → bit decisions
            recovered_bytes = self._demod.demodulate(samples)
            if not recovered_bytes:
                continue

            # 2. Feed into frame synchronizer (ASM correlation on recovered bytes)
            raw_frames = self._frame_sync.feed(recovered_bytes)

            for raw_frame in raw_frames:
                try:
                    self._process_frame(raw_frame)
                except Exception as e:
                    logger.warning("RX frame processing error: %s", e)
                    with self._lock:
                        self.bad_frames += 1

    def _process_frame(self, raw_frame: bytes) -> None:
        """Process a synchronized frame through FEC decoding and packet extraction."""
        frame_data = raw_frame

        # 3. Viterbi decode (if convolutional coding was applied on TX)
        if self._conv_enabled:
            # Original frame length (with RS parity if enabled) determines output size.
            orig_len = self._frame_length
            if self._rs_enabled:
                # RS adds 32 parity bytes per 223-byte block
                n_blocks = (self._frame_length + 222) // 223
                orig_len = self._frame_length + n_blocks * 32
            frame_data = viterbi_decode(frame_data, original_length=orig_len)

        # 4. RS decode in 255-byte blocks (matching TX encoding)
        if self._rs_enabled:
            rs_out = bytearray()
            ok = True
            for i in range(0, len(frame_data), 255):
                block = frame_data[i:i + 255]
                if len(block) < 33:  # too short for RS
                    rs_out.extend(block)
                    continue
                decoded = rs_decode(block)
                if decoded is None:
                    ok = False
                    break
                rs_out.extend(decoded)
            if not ok:
                with self._lock:
                    self.bad_frames += 1
                return
            frame_data = bytes(rs_out)

        # 5. Parse TM frame (validate FECF)
        parsed = self._frame_parser.parse_frame(frame_data)
        if parsed is None:
            with self._lock:
                self.bad_frames += 1
            return

        with self._lock:
            self.good_frames += 1

        # 6. Extract ECSS packets
        if not parsed.is_idle:
            packets = self._frame_parser.extract_packets(parsed)
            for pkt in packets:
                self.packets_recovered += 1
                if self._packet_callback:
                    self._packet_callback(pkt)

    def stop(self):
        self._running = False

"""Spacecraft TX — continuous frame-to-sample transmitter.

Runs in its own thread. Continuously generates CCSDS TM Transfer Frames
(real data when available, idle fill when not), applies RS + convolutional
encoding, attaches the ASM, modulates to complex baseband samples, and
pushes them to the output sample buffer at wall-clock rate.

The transmitter never stops — a real spacecraft transmitter outputs a
continuous carrier with framed data or idle fill.
"""

import logging
import queue
import struct
import threading
import time

import numpy as np

from ..ccsds.asm import attach_asm
from ..ccsds.convolutional import encode as conv_encode
from ..ccsds.reed_solomon import encode as rs_encode
from ..ccsds.tm_frame import TMFrameBuilder
from ..ccsds.virtual_channel import VCMultiplexer
from ..dsp.modulator import Modulator, BITS_PER_SYMBOL

from .sample_buffer import SampleBuffer

logger = logging.getLogger(__name__)


class SpacecraftTX(threading.Thread):
    """Continuous spacecraft transmitter.

    Generates a never-ending stream of modulated baseband samples from
    TM packets (or idle fill when no data is available).
    """

    def __init__(self, scid: int = 1, frame_length: int = 1115,
                 fecf_present: bool = True,
                 modulation: int = 0, symbol_rate: float = 32000.0,
                 sps: int = 8, rolloff: float = 0.35,
                 rs_enabled: bool = True, conv_enabled: bool = True,
                 output_buffer: SampleBuffer = None,
                 packet_queue: queue.Queue = None):
        super().__init__(daemon=True, name="SpacecraftTX")
        self._running = False
        self._transmitting = False  # PA off until link is established

        # CCSDS framing
        self._frame_builder = TMFrameBuilder(
            scid=scid, frame_length=frame_length, fecf_present=fecf_present)
        self._vc_mux = VCMultiplexer(
            scid=scid, frame_length=frame_length, fecf_present=fecf_present)
        self._frame_length = frame_length
        self._fecf_present = fecf_present
        self._rs_enabled = rs_enabled
        self._conv_enabled = conv_enabled

        # Modulation
        self._modulator = Modulator(modulation=modulation, sps=sps, rolloff=rolloff)
        self._modulation = modulation
        self._symbol_rate = symbol_rate
        self._sps = sps

        # I/O
        self._output = output_buffer or SampleBuffer(name="tx_out")
        self._packet_queue = packet_queue or queue.Queue()

        # Pacing
        self._frame_interval = self._calc_frame_interval()

        # Stats
        self.frames_transmitted = 0
        self.idle_frames = 0
        self.data_frames = 0

    def _calc_frame_interval(self) -> float:
        """Time in seconds to transmit one coded frame."""
        frame_bytes = self._frame_length
        if self._rs_enabled:
            # RS adds 32 parity bytes per 223-byte block
            n_blocks = (frame_bytes + 222) // 223
            frame_bytes += n_blocks * 32
        if self._conv_enabled:
            frame_bytes = frame_bytes * 2 + 2  # rate 1/2 + K-1 flush
        frame_bytes += 4  # ASM
        bits = frame_bytes * 8
        bps = BITS_PER_SYMBOL.get(self._modulation, 1)
        symbols = bits / bps
        return symbols / self._symbol_rate

    def _encode_frame(self, frame) -> np.ndarray:
        """Serialize, FEC-encode, attach ASM, and modulate a single frame."""
        frame_bytes = frame.header.pack() + frame.data
        if frame.fecf is not None:
            frame_bytes += struct.pack('>H', frame.fecf)
        if self._rs_enabled:
            rs_out = bytearray()
            for i in range(0, len(frame_bytes), 223):
                block = frame_bytes[i:i + 223]
                rs_out.extend(rs_encode(block))
            frame_bytes = bytes(rs_out)
        if self._conv_enabled:
            frame_bytes = conv_encode(frame_bytes)
        wire_bytes = attach_asm(frame_bytes)
        return self._modulator.modulate(wire_bytes)

    def enqueue_packet(self, packet: bytes) -> None:
        """Add a TM packet to be transmitted (called from main thread)."""
        try:
            self._packet_queue.put_nowait(packet)
        except queue.Full:
            logger.warning("TX packet dropped: queue full (%d)",
                           self._packet_queue.maxsize)

    def set_transmitting(self, on: bool):
        """Enable/disable the transmitter. When off, no samples are produced
        (PA is off — no carrier on the downlink)."""
        self._transmitting = on

    def reconfigure(self, modulation: int = None, symbol_rate: float = None):
        """Change modulation or symbol rate (called from main thread)."""
        if modulation is not None and modulation != self._modulation:
            self._modulation = modulation
            self._modulator.set_modulation(modulation)
            logger.info("TX: modulation → %d", modulation)
        if symbol_rate is not None:
            self._symbol_rate = symbol_rate
            logger.info("TX: symbol rate → %.0f sps", symbol_rate)
        self._frame_interval = self._calc_frame_interval()

    def run(self):
        """Thread main loop — generate continuous sample stream."""
        self._running = True
        logger.info("SpacecraftTX started: mod=%d, SR=%.0f, frame_interval=%.3fs",
                     self._modulation, self._symbol_rate, self._frame_interval)
        try:
            self._run_loop()
        except Exception as e:
            logger.error("SpacecraftTX crashed: %s", e, exc_info=True)
        finally:
            self._running = False

    def _run_loop(self):
        while self._running:
            # When PA is off, don't produce samples — no carrier on the downlink
            if not self._transmitting:
                time.sleep(0.1)
                continue

            t0 = time.monotonic()

            # 1. Drain incoming packets into VC mux
            while True:
                try:
                    pkt = self._packet_queue.get_nowait()
                    self._vc_mux.add_packet(pkt, vcid=0)
                except queue.Empty:
                    break

            # 2. Get next frame: data frame, flushed partial frame, or idle
            if self._vc_mux.has_data():
                frame = self._vc_mux.get_next_frame()
                self.data_frames += 1
            else:
                # Flush any partial data sitting in VC buffers
                flushed = self._vc_mux.flush_all()
                if flushed:
                    frame = flushed[0]
                    # Re-queue remaining flushed frames
                    for f in flushed[1:]:
                        self._output.put(self._encode_frame(f))
                        self.frames_transmitted += 1
                        self.data_frames += 1
                    self.data_frames += 1
                else:
                    frame = self._vc_mux.get_next_frame()  # idle VC7
                    self.idle_frames += 1

            # 3. Encode and modulate frame
            samples = self._encode_frame(frame)
            self._output.put(samples)
            self.frames_transmitted += 1

            # 8. Pace to wall-clock time
            elapsed = time.monotonic() - t0
            sleep_time = self._frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def stop(self):
        self._running = False

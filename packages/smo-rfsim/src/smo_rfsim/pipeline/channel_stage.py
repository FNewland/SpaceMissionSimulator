"""Channel stage — applies AWGN and Doppler to the continuous sample stream.

Runs in its own thread between TX and RX. When the link is inactive
(no pass), injects only noise (no signal forwarded).

Optionally publishes raw impaired samples via ZMQ PUB socket for
external listeners (SatHack, gr-satellites, I/Q recording, etc.).
"""

import logging
import queue
import threading

import numpy as np

from ..dsp.channel import BasebandChannel
from .sample_buffer import SampleBuffer

logger = logging.getLogger(__name__)

try:
    import zmq
    HAS_ZMQ = True
except ImportError:
    HAS_ZMQ = False

try:
    from gnuradio import channels as gr_channels
    HAS_GR = True
except ImportError:
    HAS_GR = False


class ChannelStage(threading.Thread):
    """Applies channel impairments to the continuous sample stream.

    Optionally publishes impaired samples via ZMQ PUB for external
    blind signal analysis tools.
    """

    def __init__(self, eb_n0_db: float = 10.0, sps: int = 8,
                 bits_per_symbol: int = 1,
                 freq_offset_hz: float = 0.0,
                 sample_rate: float = 256000.0,
                 input_buffer: SampleBuffer = None,
                 output_buffer: SampleBuffer = None,
                 zmq_pub_port: int = 0,
                 seed: int = 42):
        super().__init__(daemon=True, name="ChannelStage")
        self._running = False
        self._link_active = False

        self._channel = BasebandChannel(
            eb_n0_db=eb_n0_db, sps=sps, bits_per_symbol=bits_per_symbol,
            freq_offset_hz=freq_offset_hz, sample_rate=sample_rate,
            seed=seed)
        self._sample_rate = sample_rate
        self._rng = np.random.default_rng(seed + 100)

        self._input = input_buffer or SampleBuffer(name="ch_in")
        self._output = output_buffer or SampleBuffer(name="ch_out")

        # ZMQ PUB socket for external listeners
        self._zmq_socket = None
        self._zmq_port = zmq_pub_port
        if zmq_pub_port > 0 and HAS_ZMQ:
            ctx = zmq.Context.instance()
            self._zmq_socket = ctx.socket(zmq.PUB)
            self._zmq_socket.setsockopt(zmq.SNDHWM, 64)  # drop if subscriber too slow
            self._zmq_socket.bind(f"tcp://*:{zmq_pub_port}")
            logger.info("Channel ZMQ PUB on tcp://*:%d", zmq_pub_port)

    def set_eb_n0(self, db: float):
        self._channel.set_eb_n0(db)

    def set_freq_offset(self, hz: float):
        self._channel.set_freq_offset(hz)

    def set_link_active(self, active: bool):
        self._link_active = active

    def set_bits_per_symbol(self, bps: int):
        self._channel.bits_per_symbol = bps

    def run(self):
        self._running = True
        logger.info("ChannelStage started: Eb/N0=%.1f dB, ZMQ=%s",
                     self._channel.eb_n0_db,
                     f"tcp://*:{self._zmq_port}" if self._zmq_socket else "off")
        try:
            self._run_loop()
        except Exception as e:
            logger.error("ChannelStage crashed: %s", e, exc_info=True)
        finally:
            self._running = False
            if self._zmq_socket:
                self._zmq_socket.close()

    _NOISE_BLOCK = 8000

    def _run_loop(self):
        while self._running:
            try:
                samples = self._input.get(timeout=0.1)
            except queue.Empty:
                if not self._link_active:
                    noise = (self._rng.normal(0, 0.3, self._NOISE_BLOCK) +
                             1j * self._rng.normal(0, 0.3, self._NOISE_BLOCK)
                             ).astype(np.complex64)
                    self._output.put(noise)
                    self._zmq_publish(noise)
                continue

            if self._link_active:
                impaired = self._channel.process(samples)
            else:
                n = len(samples)
                noise = (self._rng.normal(0, 0.3, n) +
                         1j * self._rng.normal(0, 0.3, n)).astype(np.complex64)
                impaired = noise

            self._output.put(impaired)
            self._zmq_publish(impaired)

    def _zmq_publish(self, samples: np.ndarray):
        """Publish raw complex64 samples to ZMQ subscribers."""
        if self._zmq_socket is not None:
            try:
                self._zmq_socket.send(samples.tobytes(), zmq.NOBLOCK)
            except zmq.Again:
                pass  # subscriber too slow, drop

    def stop(self):
        self._running = False

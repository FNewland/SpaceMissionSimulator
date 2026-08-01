"""Channel stage — applies AWGN and Doppler to the continuous sample stream.

Runs in its own thread between TX and RX. When the link is inactive
(no pass), injects only noise (no signal forwarded).

Optionally publishes raw impaired samples via ZMQ PUB socket for
external listeners (SatHack, gr-satellites, I/Q recording, etc.).
"""

import logging
import queue
import threading
from collections import deque

import numpy as np

from ..channel.space_link import SpaceLinkChannel, SpaceLinkConfig
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

    The impairment engine is ``SpaceLinkChannel`` — Doppler → multipath →
    Rician fading → phase noise → AWGN → interferers — so fading, phase
    noise and CW/wideband interference are injectable live rather than
    existing only as unreachable code (defect #56).

    Optionally publishes impaired samples via ZMQ PUB for external
    blind signal analysis tools.
    """

    #: Post-impairment samples retained for the spectrum analyser.
    SPECTRUM_BUFFER_SIZE = 8192

    def __init__(self, eb_n0_db: float = 10.0, sps: int = 8,
                 bits_per_symbol: int = 1,
                 freq_offset_hz: float = 0.0,
                 sample_rate: float = 256000.0,
                 input_buffer: SampleBuffer = None,
                 output_buffer: SampleBuffer = None,
                 zmq_pub_port: int = 0,
                 seed: int = 42,
                 link_config: SpaceLinkConfig = None):
        super().__init__(daemon=True, name="ChannelStage")
        self._running = False
        self._link_active = False

        cfg = link_config or SpaceLinkConfig()
        cfg.eb_n0_db = eb_n0_db
        cfg.sps = sps
        cfg.bits_per_symbol = bits_per_symbol
        cfg.doppler_hz = freq_offset_hz
        cfg.sample_rate = sample_rate
        if cfg.seed is None:
            cfg.seed = seed
        self._channel = SpaceLinkChannel(cfg)
        self._sample_rate = sample_rate
        self._rng = np.random.default_rng(seed + 100)

        # Rolling window of the most recent impaired samples — exactly what
        # a ground-station SDR would see. Feeds the spectrum analyser.
        self._spectrum_lock = threading.Lock()
        self._spectrum_buf: deque = deque(maxlen=self.SPECTRUM_BUFFER_SIZE)

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
        self._channel.set_bits_per_symbol(bps)

    def set_sample_rate(self, sample_rate: float):
        """Retune the channel after a symbol-rate change."""
        self._sample_rate = float(sample_rate)
        self._channel.set_sample_rate(float(sample_rate))

    def set_impairments(self, **kwargs) -> dict:
        """Enable/disable/retune RF impairments live. See
        ``SpaceLinkChannel.set_impairments``."""
        return self._channel.set_impairments(**kwargs)

    def get_channel_status(self) -> dict:
        """Channel model state for the radio UI 'Channel Model' panel."""
        status = self._channel.get_status()
        status["link_active"] = self._link_active
        return status

    @property
    def channel(self) -> SpaceLinkChannel:
        return self._channel

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    def get_recent_samples(self, max_samples: int = 4096) -> np.ndarray:
        """Snapshot of the most recent post-impairment baseband samples.

        Copied under a lock so the spectrum analyser never touches the
        channel thread's working arrays.
        """
        with self._spectrum_lock:
            if not self._spectrum_buf:
                return np.array([], dtype=np.complex64)
            data = np.fromiter(self._spectrum_buf, dtype=np.complex64,
                               count=len(self._spectrum_buf))
        if max_samples and len(data) > max_samples:
            return data[-max_samples:]
        return data

    def _record_spectrum(self, samples: np.ndarray) -> None:
        if samples is None or len(samples) == 0:
            return
        # Only the tail is ever displayed; extending the whole chunk would
        # cost O(chunk) per frame for no benefit.
        tail = samples[-self.SPECTRUM_BUFFER_SIZE:]
        with self._spectrum_lock:
            self._spectrum_buf.extend(tail.astype(np.complex64).tolist())

    def run(self):
        self._running = True
        logger.info("ChannelStage started: Eb/N0=%.1f dB, fading=%s, "
                    "phase_noise=%s, interferers=%d, ZMQ=%s",
                    self._channel.config.eb_n0_db,
                    self._channel.config.fading_enabled,
                    self._channel.config.phase_noise_enabled,
                    len(self._channel._interferers),
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
                    self._record_spectrum(noise)
                continue

            if self._link_active:
                impaired = self._channel.process(samples)
            else:
                logger.debug("Channel: link not active, replacing %d samples with noise", len(samples))
                n = len(samples)
                noise = (self._rng.normal(0, 0.3, n) +
                         1j * self._rng.normal(0, 0.3, n)).astype(np.complex64)
                impaired = noise

            self._output.put(impaired)
            self._zmq_publish(impaired)
            self._record_spectrum(impaired)

    def _zmq_publish(self, samples: np.ndarray):
        """Publish raw complex64 samples to ZMQ subscribers."""
        if self._zmq_socket is not None:
            try:
                self._zmq_socket.send(samples.tobytes(), zmq.NOBLOCK)
            except zmq.Again:
                pass  # subscriber too slow, drop

    def stop(self):
        self._running = False

"""GNU Radio channel simulation block.

Adds AWGN noise, frequency offset (Doppler), and timing offset
to complex baseband samples using GNU Radio's built-in channel model.

When GNU Radio is not installed, provides a pure-Python numpy fallback.
"""

import logging
import math

logger = logging.getLogger(__name__)

try:
    from gnuradio import gr, channels, blocks
    import numpy as np
    HAS_GR = True
except ImportError:
    HAS_GR = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


class GRChannelSim:
    """GNU Radio channel model with AWGN and frequency offset."""

    def __init__(self, noise_voltage: float = 0.1,
                 freq_offset: float = 0.0,
                 timing_offset: float = 1.0):
        if not HAS_GR:
            raise ImportError("GNU Radio not available")
        self.tb = gr.top_block()

        self.src = blocks.vector_source_c([], False)
        self.channel = channels.channel_model(
            noise_voltage=noise_voltage,
            frequency_offset=freq_offset,
            epsilon=timing_offset,
            noise_seed=42)
        self.sink = blocks.vector_sink_c()

        self.tb.connect(self.src, self.channel, self.sink)

        self._noise_voltage = noise_voltage
        self._freq_offset = freq_offset

    def set_eb_n0(self, eb_n0_db: float, sps: int = 8):
        """Set channel noise from Eb/N0 in dB."""
        eb_n0_linear = 10 ** (eb_n0_db / 10.0)
        noise_voltage = 1.0 / math.sqrt(2.0 * eb_n0_linear * sps)
        self._noise_voltage = noise_voltage
        self.channel.set_noise_voltage(noise_voltage)

    def set_doppler(self, doppler_hz: float, sample_rate: float):
        """Set frequency offset from Doppler shift."""
        self._freq_offset = doppler_hz / sample_rate
        self.channel.set_frequency_offset(self._freq_offset)

    def process(self, sample_bytes: bytes) -> bytes:
        samples = np.frombuffer(sample_bytes, dtype=np.complex64)
        self.src.set_data(samples.tolist())
        self.sink.reset()
        self.tb.run()
        result = np.array(self.sink.data(), dtype=np.complex64)
        return result.tobytes()

    def stop(self):
        self.tb.stop()
        self.tb.wait()


class NumpyChannelSim:
    """Pure-numpy channel simulation fallback when GNU Radio is unavailable."""

    def __init__(self, eb_n0_db: float = 10.0, freq_offset_hz: float = 0.0,
                 sample_rate: float = 256000.0, seed: int = 42):
        if not HAS_NUMPY:
            raise ImportError("numpy not available")
        self.eb_n0_db = eb_n0_db
        self.freq_offset_hz = freq_offset_hz
        self.sample_rate = sample_rate
        self._rng = np.random.default_rng(seed)
        self._sample_counter = 0

    def set_eb_n0(self, eb_n0_db: float, sps: int = 8):
        self.eb_n0_db = eb_n0_db

    def set_doppler(self, doppler_hz: float, sample_rate: float = None):
        self.freq_offset_hz = doppler_hz
        if sample_rate:
            self.sample_rate = sample_rate

    def process(self, sample_bytes: bytes) -> bytes:
        """Apply AWGN and frequency offset to complex samples."""
        samples = np.frombuffer(sample_bytes, dtype=np.complex64).copy()
        n = len(samples)

        # AWGN
        eb_n0_lin = 10 ** (self.eb_n0_db / 10.0)
        signal_power = np.mean(np.abs(samples) ** 2)
        if signal_power > 0:
            noise_power = signal_power / (2.0 * eb_n0_lin)
            noise = self._rng.normal(0, math.sqrt(noise_power), n) + \
                    1j * self._rng.normal(0, math.sqrt(noise_power), n)
            samples += noise.astype(np.complex64)

        # Frequency offset (Doppler)
        if self.freq_offset_hz != 0:
            t = (self._sample_counter + np.arange(n)) / self.sample_rate
            shift = np.exp(2j * np.pi * self.freq_offset_hz * t).astype(np.complex64)
            samples *= shift
            self._sample_counter += n

        return samples.tobytes()


def create_channel_sim(eb_n0_db: float = 10.0, freq_offset_hz: float = 0.0,
                       sample_rate: float = 256000.0,
                       use_gnuradio: bool = False):
    """Factory to create channel simulator."""
    if use_gnuradio and HAS_GR:
        try:
            sim = GRChannelSim()
            sim.set_eb_n0(eb_n0_db)
            sim.set_doppler(freq_offset_hz, sample_rate)
            return sim
        except Exception as e:
            logger.warning("GR channel sim failed, using numpy: %s", e)
    if HAS_NUMPY:
        return NumpyChannelSim(eb_n0_db, freq_offset_hz, sample_rate)
    raise RuntimeError("Neither GNU Radio nor numpy available for channel simulation")

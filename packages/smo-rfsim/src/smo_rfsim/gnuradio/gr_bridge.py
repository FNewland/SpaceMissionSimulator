"""Async adaptor bridging GNU Radio flowgraphs to the asyncio bridge.

Provides a common interface that the bridge server uses regardless of
whether GNU Radio is available. When GR is not installed, falls back
to the pure-Python FRAME mode processing chain.
"""

import asyncio
import logging
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

try:
    import gnuradio  # noqa: F401
    HAS_GNURADIO = True
except ImportError:
    HAS_GNURADIO = False


class RFProcessor(Protocol):
    """Interface for RF processing chains (GR or pure-Python)."""

    def modulate_and_transmit(self, frame_bytes: bytes) -> bytes:
        """Modulate frame bytes into baseband samples (or impaired bytes)."""
        ...

    def receive_and_demodulate(self, samples: bytes) -> Optional[bytes]:
        """Demodulate baseband samples back to frame bytes."""
        ...


class PurePythonRFProcessor:
    """Fallback RF processor using pure-Python channel model.

    Used when GNU Radio is not installed. Applies the same channel
    impairments as FRAME mode (BER injection) but logged as RF mode.
    """

    def __init__(self, channel_model):
        self.channel = channel_model

    def modulate_and_transmit(self, frame_bytes: bytes) -> bytes:
        return self.channel.impair(frame_bytes)

    def receive_and_demodulate(self, samples: bytes) -> Optional[bytes]:
        # In pure-Python mode, "samples" are already impaired bytes
        return samples


class GnuRadioRFProcessor:
    """GNU Radio RF processor using real BPSK modulation/demodulation.

    Wraps the GR flowgraphs in downlink_mod.py / downlink_demod.py.
    Requires GNU Radio to be installed via system package manager.
    """

    def __init__(self, sample_rate: float = 256000.0,
                 symbol_rate: float = 32000.0,
                 eb_n0_db: float = 10.0,
                 freq_offset_hz: float = 0.0):
        if not HAS_GNURADIO:
            raise RuntimeError(
                "GNU Radio is not installed. Install via: brew install gnuradio")
        self.sample_rate = sample_rate
        self.symbol_rate = symbol_rate
        self.eb_n0_db = eb_n0_db
        self.freq_offset_hz = freq_offset_hz
        self._mod_flowgraph = None
        self._demod_flowgraph = None
        self._init_flowgraphs()

    def _init_flowgraphs(self):
        """Initialize GNU Radio flowgraphs."""
        try:
            from .downlink_mod import create_modulator
            from .downlink_demod import create_demodulator
            self._mod_flowgraph = create_modulator(
                self.sample_rate, self.symbol_rate)
            self._demod_flowgraph = create_demodulator(
                self.sample_rate, self.symbol_rate)
            logger.info("GNU Radio flowgraphs initialized: SR=%.0f, SymR=%.0f",
                        self.sample_rate, self.symbol_rate)
        except Exception as e:
            logger.error("Failed to initialize GR flowgraphs: %s", e)
            raise

    def modulate_and_transmit(self, frame_bytes: bytes) -> bytes:
        """BPSK modulate frame bytes into complex baseband samples."""
        if self._mod_flowgraph is None:
            return frame_bytes
        try:
            return self._mod_flowgraph.process(frame_bytes)
        except Exception as e:
            logger.error("GR modulation error: %s", e)
            return frame_bytes

    def receive_and_demodulate(self, samples: bytes) -> Optional[bytes]:
        """Demodulate complex baseband samples back to frame bytes."""
        if self._demod_flowgraph is None:
            return samples
        try:
            return self._demod_flowgraph.process(samples)
        except Exception as e:
            logger.error("GR demodulation error: %s", e)
            return None


def create_rf_processor(channel_model, use_gnuradio: bool = False,
                        **kwargs) -> RFProcessor:
    """Factory function to create the appropriate RF processor.

    Falls back to PurePythonRFProcessor if GNU Radio is not available.
    """
    if use_gnuradio and HAS_GNURADIO:
        try:
            return GnuRadioRFProcessor(**kwargs)
        except Exception as e:
            logger.warning("GNU Radio init failed, falling back to pure Python: %s", e)
    return PurePythonRFProcessor(channel_model)

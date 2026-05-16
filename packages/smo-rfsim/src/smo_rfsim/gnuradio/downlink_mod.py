"""GNU Radio BPSK downlink modulator flowgraph.

Implements: bytes → unpacker → BPSK mapper → RRC pulse shaping → output

This module requires GNU Radio to be installed. When imported without
GNU Radio, it raises ImportError which is caught by gr_bridge.py.

Reference: CCSDS 401.0-B-32 (Earth Stations and Spacecraft)
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)

try:
    from gnuradio import gr, blocks, digital, filter as gr_filter
    HAS_GR = True
except ImportError:
    HAS_GR = False


class BPSKModulator:
    """BPSK modulator using GNU Radio blocks.

    Signal chain:
      bytes → packed_to_unpacked → chunks_to_symbols (BPSK) →
      RRC filter → output (complex baseband)
    """

    def __init__(self, sample_rate: float = 256000.0,
                 symbol_rate: float = 32000.0,
                 rrc_rolloff: float = 0.35,
                 rrc_taps: int = 33):
        if not HAS_GR:
            raise ImportError("GNU Radio not available")
        self.sample_rate = sample_rate
        self.symbol_rate = symbol_rate
        self.sps = int(sample_rate / symbol_rate)
        self.rrc_rolloff = rrc_rolloff

        # Build flowgraph
        self.tb = gr.top_block()

        # BPSK constellation: {0: -1+0j, 1: +1+0j}
        self.constellation = digital.constellation_bpsk().base()

        # RRC filter taps
        self.rrc_filter_taps = gr_filter.firdes.root_raised_cosine(
            1.0, sample_rate, symbol_rate, rrc_rolloff, rrc_taps * self.sps)

        # Blocks
        self.src = blocks.vector_source_b([], False)
        self.unpacker = blocks.packed_to_unpacked_bb(1, gr.GR_MSB_FIRST)
        self.mapper = digital.chunks_to_symbols_bc(
            self.constellation.points(), 1)
        self.rrc = gr_filter.interp_fir_filter_ccf(self.sps, self.rrc_filter_taps)
        self.sink = blocks.vector_sink_c()

        # Connect
        self.tb.connect(self.src, self.unpacker, self.mapper, self.rrc, self.sink)

    def process(self, data: bytes) -> bytes:
        """Modulate data bytes into complex baseband samples."""
        self.src.set_data(list(data))
        self.sink.reset()
        self.tb.run()
        samples = np.array(self.sink.data(), dtype=np.complex64)
        return samples.tobytes()

    def stop(self):
        self.tb.stop()
        self.tb.wait()


def create_modulator(sample_rate: float = 256000.0,
                     symbol_rate: float = 32000.0) -> BPSKModulator:
    """Create and return a BPSK modulator instance."""
    return BPSKModulator(sample_rate=sample_rate, symbol_rate=symbol_rate)

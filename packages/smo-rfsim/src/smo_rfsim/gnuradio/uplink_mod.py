"""GNU Radio uplink modulator flowgraph (TC direction).

Implements TC uplink modulation for the command path.
Uses NRZ-L/BPSK modulation per CCSDS 401.0-B.

The uplink uses a lower data rate than downlink (typically 4 kbps for
S-band command vs 32 kbps for telemetry).
"""

import logging

logger = logging.getLogger(__name__)

try:
    from gnuradio import gr, blocks, digital, filter as gr_filter
    import numpy as np
    HAS_GR = True
except ImportError:
    HAS_GR = False


class UplinkModulator:
    """BPSK uplink modulator for TC CLTUs."""

    def __init__(self, sample_rate: float = 64000.0,
                 symbol_rate: float = 4000.0,
                 rrc_rolloff: float = 0.5):
        if not HAS_GR:
            raise ImportError("GNU Radio not available")
        self.sample_rate = sample_rate
        self.symbol_rate = symbol_rate
        self.sps = int(sample_rate / symbol_rate)

        self.tb = gr.top_block()

        constellation = digital.constellation_bpsk().base()
        rrc_taps = gr_filter.firdes.root_raised_cosine(
            1.0, sample_rate, symbol_rate, rrc_rolloff, 33 * self.sps)

        self.src = blocks.vector_source_b([], False)
        self.unpacker = blocks.packed_to_unpacked_bb(1, gr.GR_MSB_FIRST)
        self.mapper = digital.chunks_to_symbols_bc(constellation.points(), 1)
        self.rrc = gr_filter.interp_fir_filter_ccf(self.sps, rrc_taps)
        self.sink = blocks.vector_sink_c()

        self.tb.connect(self.src, self.unpacker, self.mapper, self.rrc, self.sink)

    def process(self, data: bytes) -> bytes:
        self.src.set_data(list(data))
        self.sink.reset()
        self.tb.run()
        samples = np.array(self.sink.data(), dtype=np.complex64)
        return samples.tobytes()

    def stop(self):
        self.tb.stop()
        self.tb.wait()

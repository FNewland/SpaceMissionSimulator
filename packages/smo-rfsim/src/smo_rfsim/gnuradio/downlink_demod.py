"""GNU Radio BPSK downlink demodulator flowgraph.

Implements: complex baseband → AGC → Costas loop → M&M clock recovery →
            constellation decoder → output bytes

This module requires GNU Radio to be installed.

Reference: CCSDS 401.0-B-32 (Earth Stations and Spacecraft)
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)

try:
    from gnuradio import gr, blocks, digital, filter as gr_filter, analog
    HAS_GR = True
except ImportError:
    HAS_GR = False


class BPSKDemodulator:
    """BPSK demodulator using GNU Radio blocks.

    Signal chain:
      complex baseband → AGC → matched RRC filter → Costas loop →
      M&M clock recovery → constellation decoder → pack bits → output bytes
    """

    def __init__(self, sample_rate: float = 256000.0,
                 symbol_rate: float = 32000.0,
                 rrc_rolloff: float = 0.35,
                 rrc_taps: int = 33,
                 costas_bw: float = 0.005,
                 mm_gain_mu: float = 0.175,
                 mm_gain_omega: float = 0.25 * 0.175 * 0.175):
        if not HAS_GR:
            raise ImportError("GNU Radio not available")
        self.sample_rate = sample_rate
        self.symbol_rate = symbol_rate
        self.sps = int(sample_rate / symbol_rate)

        self.tb = gr.top_block()

        # BPSK constellation
        self.constellation = digital.constellation_bpsk().base()

        # Matched RRC filter
        rrc_filter_taps = gr_filter.firdes.root_raised_cosine(
            1.0, sample_rate, symbol_rate, rrc_rolloff, rrc_taps * self.sps)

        # Blocks
        self.src = blocks.vector_source_c([], False)
        self.agc = analog.agc2_cc(0.6e-1, 1e-3, 1.0, 1.0)
        self.rrc = gr_filter.fir_filter_ccf(1, rrc_filter_taps)
        self.costas = digital.costas_loop_cc(costas_bw, 2)  # order=2 for BPSK
        self.mm = digital.clock_recovery_mm_cc(
            float(self.sps), mm_gain_omega, 0.5, mm_gain_mu, 0.005)
        self.decoder = digital.constellation_decoder_cb(self.constellation)
        self.packer = blocks.pack_k_bits_bb(8)
        self.sink = blocks.vector_sink_b()

        # Connect
        self.tb.connect(self.src, self.agc, self.rrc, self.costas,
                        self.mm, self.decoder, self.packer, self.sink)

    def process(self, sample_bytes: bytes) -> bytes:
        """Demodulate complex baseband samples back to data bytes."""
        samples = np.frombuffer(sample_bytes, dtype=np.complex64)
        self.src.set_data(samples.tolist())
        self.sink.reset()
        self.tb.run()
        return bytes(self.sink.data())

    def stop(self):
        self.tb.stop()
        self.tb.wait()


def create_demodulator(sample_rate: float = 256000.0,
                       symbol_rate: float = 32000.0) -> BPSKDemodulator:
    """Create and return a BPSK demodulator instance."""
    return BPSKDemodulator(sample_rate=sample_rate, symbol_rate=symbol_rate)

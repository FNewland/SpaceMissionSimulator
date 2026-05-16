"""GNU Radio uplink demodulator flowgraph (TC direction).

Demodulates the uplink BPSK signal at the spacecraft receiver.
Mirror of downlink_demod but at the uplink data rate (4 kbps).
"""

import logging

logger = logging.getLogger(__name__)

try:
    from gnuradio import gr, blocks, digital, filter as gr_filter, analog
    import numpy as np
    HAS_GR = True
except ImportError:
    HAS_GR = False


class UplinkDemodulator:
    """BPSK uplink demodulator for TC CLTUs."""

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

        self.src = blocks.vector_source_c([], False)
        self.agc = analog.agc2_cc(0.6e-1, 1e-3, 1.0, 1.0)
        self.rrc = gr_filter.fir_filter_ccf(1, rrc_taps)
        self.costas = digital.costas_loop_cc(0.01, 2)
        self.mm = digital.clock_recovery_mm_cc(
            float(self.sps), 0.25 * 0.175 * 0.175, 0.5, 0.175, 0.005)
        self.decoder = digital.constellation_decoder_cb(constellation)
        self.packer = blocks.pack_k_bits_bb(8)
        self.sink = blocks.vector_sink_b()

        self.tb.connect(self.src, self.agc, self.rrc, self.costas,
                        self.mm, self.decoder, self.packer, self.sink)

    def process(self, sample_bytes: bytes) -> bytes:
        samples = np.frombuffer(sample_bytes, dtype=np.complex64)
        self.src.set_data(samples.tolist())
        self.sink.reset()
        self.tb.run()
        return bytes(self.sink.data())

    def stop(self):
        self.tb.stop()
        self.tb.wait()

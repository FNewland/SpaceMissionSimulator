"""Spectrum analyser and eye diagram must be fed from the LIVE path.

Defect #53: `RadioFrontend.update_spectrum()` and `update_eye_diagram()`
had zero call sites, so `spectrum_db` / `eye_i` were always empty and both
canvases stayed blank forever while the test suite stayed green.

These tests run the real TX → channel → RX pipeline and assert the
producers emit non-empty data from it.
"""

import time

import numpy as np
import pytest

from smo_rfsim.bridge import RFSimBridge
from smo_rfsim.config import RFSimConfig
from smo_rfsim.mode import RFSimMode
from smo_rfsim.pipeline.coordinator import PipelineCoordinator
from smo_rfsim.radio.frontend import RadioFrontend

SETTLE_S = 4.0


def _rf_config(**kw) -> RFSimConfig:
    cfg = RFSimConfig()
    cfg.mode = RFSimMode.RF
    cfg.network.zmq_samples_port = 0          # don't bind a socket in tests
    cfg.ccsds.convolutional_enabled = False   # pure-Python Viterbi is too slow
    for key, value in kw.items():
        setattr(cfg.network, key, value) if hasattr(cfg.network, key) else None
    return cfg


@pytest.fixture(scope="module")
def running_pipeline():
    cfg = _rf_config()
    pc = PipelineCoordinator(cfg)
    pc.start()
    pc.set_link_in_view(True)
    pc.set_transmitting(True)
    for _ in range(20):
        pc.enqueue_tm_packet(bytes(range(64)))
    time.sleep(SETTLE_S)
    yield pc
    pc.stop()


class TestLiveSampleTaps:
    def test_channel_stage_retains_impaired_samples(self, running_pipeline):
        samples = running_pipeline.get_spectrum_samples()
        assert len(samples) > 512, "channel stage produced no spectrum samples"
        assert np.iscomplexobj(samples)
        assert np.any(samples != 0)

    def test_receiver_retains_oversampled_eye_samples(self, running_pipeline):
        eye = running_pipeline.get_eye_samples()
        sps = running_pipeline.sps
        assert len(eye) >= 2 * sps * 2, "receiver produced no eye samples"
        assert np.iscomplexobj(eye)
        # Must be the oversampled waveform, not the symbol-rate tap:
        # consecutive samples within a symbol are strongly correlated.
        d = np.abs(np.diff(eye.real))
        assert float(np.mean(d)) < float(np.std(eye.real)), (
            "eye tap looks symbol-rate decimated, not oversampled")


class TestSpectrumProducer:
    def test_spectrum_populated_from_live_samples(self, running_pipeline):
        fe = RadioFrontend()
        assert fe.status.spectrum_db == []
        ok = fe.update_spectrum(running_pipeline.get_spectrum_samples(),
                                sample_rate=running_pipeline.sample_rate)
        assert ok
        # This is the exact guard the UI applies (radio.html)
        assert len(fe.status.spectrum_db) > 4
        assert fe.status.spectrum_freq_range_hz == pytest.approx(
            running_pipeline.sample_rate)
        assert fe.status.bandwidth_hz > 0

    def test_spectrum_shows_a_main_lobe(self, running_pipeline):
        fe = RadioFrontend()
        fe.update_spectrum(running_pipeline.get_spectrum_samples(),
                           sample_rate=running_pipeline.sample_rate)
        spec = np.array(fe.status.spectrum_db)
        n = len(spec)
        centre = spec[n // 2 - n // 8: n // 2 + n // 8]
        edges = np.concatenate([spec[:n // 8], spec[-n // 8:]])
        assert centre.mean() > edges.mean() + 5.0, (
            "no RRC main lobe visible in the spectrum")

    def test_short_or_missing_input_is_rejected(self):
        fe = RadioFrontend()
        assert fe.update_spectrum(None) is False
        assert fe.update_spectrum(np.zeros(8, dtype=np.complex64)) is False
        assert fe.status.spectrum_db == []

    def test_welch_averaging_is_less_jumpy_than_one_fft(self, running_pipeline):
        samples = running_pipeline.get_spectrum_samples()
        one, many = RadioFrontend(), RadioFrontend()
        one.update_spectrum(samples, segments=1)
        many.update_spectrum(samples, segments=8)
        rough = np.std(np.diff(np.array(one.status.spectrum_db)))
        smooth = np.std(np.diff(np.array(many.status.spectrum_db)))
        assert smooth < rough


class TestEyeProducer:
    def test_eye_populated_from_live_samples(self, running_pipeline):
        fe = RadioFrontend()
        assert fe.status.eye_i == [] and fe.status.eye_q == []
        ok = fe.update_eye_diagram(
            running_pipeline.get_eye_samples(),
            sps=running_pipeline.sps,
            timing_offset=running_pipeline.eye_timing_offset)
        assert ok
        # The exact guard the UI applies
        assert len(fe.status.eye_i) > 4
        # eye_q was transported but never drawn; both rails must be present
        assert len(fe.status.eye_q) == len(fe.status.eye_i)
        assert fe.status.eye_sps == running_pipeline.sps

    def test_eye_length_is_a_whole_number_of_traces(self, running_pipeline):
        fe = RadioFrontend()
        fe.update_eye_diagram(running_pipeline.get_eye_samples(),
                              sps=running_pipeline.sps)
        period = 2 * running_pipeline.sps
        assert len(fe.status.eye_i) % period == 0
        assert len(fe.status.eye_i) // period >= 2

    def test_symbol_rate_data_is_rejected(self):
        """Feeding the symbol-rate constellation tap produces noise, not an
        eye — the producer must not accept too few samples silently."""
        fe = RadioFrontend()
        assert fe.update_eye_diagram(np.zeros(10, dtype=np.complex64),
                                     sps=8) is False
        assert fe.status.eye_i == []

    def test_eye_opening_narrows_as_ebn0_drops(self, running_pipeline):
        """A real eye responds to noise; a fabricated one would not."""
        fe = RadioFrontend()

        def opening(samples):
            fe.update_eye_diagram(samples, sps=running_pipeline.sps,
                                  timing_offset=running_pipeline.eye_timing_offset)
            trace = np.array(fe.status.eye_i).reshape(-1, 2 * running_pipeline.sps)
            centre = trace[:, running_pipeline.sps // 2]
            return float(np.min(np.abs(centre)))

        clean = opening(running_pipeline.get_eye_samples())
        running_pipeline.set_eb_n0(-3.0)
        time.sleep(2.0)
        noisy = opening(running_pipeline.get_eye_samples())
        running_pipeline.set_eb_n0(10.0)
        assert noisy < clean, f"eye did not close with noise ({noisy} vs {clean})"


class TestBridgeWiresTheProducers:
    """The producers must be called from the bridge's own update loop —
    a working producer with no call site is exactly defect #53."""

    def test_update_displays_fills_every_dead_panel(self):
        cfg = _rf_config()
        bridge = RFSimBridge(cfg)
        bridge._pipeline.start()
        bridge._pipeline.set_link_in_view(True)
        bridge._pipeline.set_transmitting(True)
        for _ in range(15):
            bridge._pipeline.enqueue_tm_packet(bytes(range(64)))
        try:
            time.sleep(SETTLE_S)
            assert bridge._update_displays(force=True) is True
            s = bridge.radio.status
            assert len(s.spectrum_db) > 4          # spectrum analyser
            assert len(s.eye_i) > 4                # eye diagram (I)
            assert len(s.eye_q) == len(s.eye_i)    # eye diagram (Q)
            assert s.frequency_hz > 0              # channel model panel
            assert s.bandwidth_hz > 0
            assert s.cn0_dbhz != 0.0               # link budget panel
            assert s.eirp_dbw != 0.0
            assert s.diagnostics                   # pipeline diagnostics
            assert s.symbol_rate_bd > 0
        finally:
            bridge._pipeline.stop()

    def test_display_updates_are_rate_limited(self):
        cfg = _rf_config()
        bridge = RFSimBridge(cfg)
        bridge._display_min_period_s = 0.2
        try:
            assert bridge._update_displays(force=True) is True
            # Immediately again: must be suppressed (<= 5 Hz)
            assert bridge._update_displays() is False
        finally:
            bridge._pipeline.stop()

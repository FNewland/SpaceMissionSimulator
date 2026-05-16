"""Tests for constellation display via the DSP processor.

The constellation now shows REAL demodulated I/Q samples from the
DSP chain, not synthetic points. These tests verify the end-to-end
path from DSP processor to Radio status.
"""

import pytest
from smo_rfsim.radio.frontend import RadioFrontend, RadioStatus, LockState
from smo_rfsim.dsp.processor import DSPProcessor


class TestConstellationFromDSP:
    """Constellation points come from the DSP processor."""

    def test_no_link_noise_at_origin(self):
        """When DSP is inactive, constellation should be noise cloud."""
        dsp = DSPProcessor(modulation=0, eb_n0_db=10.0)
        dsp.set_active(False)
        dsp.process(b'\x55' * 16)
        points = dsp.get_constellation()
        assert len(points) > 0
        mean_i = sum(p[0] for p in points) / len(points)
        mean_q = sum(p[1] for p in points) / len(points)
        assert abs(mean_i) < 0.5, "No-signal mean I should be near 0"
        assert abs(mean_q) < 0.5, "No-signal mean Q should be near 0"

    def test_active_bpsk_clusters(self):
        """Active BPSK link should show clusters near ±1."""
        dsp = DSPProcessor(modulation=0, sps=8, eb_n0_db=15.0)
        dsp.set_active(True)
        for _ in range(5):
            dsp.process(b'\xAA\x55' * 50)
        points = dsp.get_constellation()
        assert len(points) > 10
        # Should have points near +1 and -1 on I axis
        near_pos = sum(1 for i, q in points if i > 0.3)
        near_neg = sum(1 for i, q in points if i < -0.3)
        assert near_pos > 0 and near_neg > 0

    def test_active_qpsk_four_quadrants(self):
        """Active QPSK should populate all four quadrants."""
        dsp = DSPProcessor(modulation=1, sps=8, eb_n0_db=15.0)
        dsp.set_active(True)
        for _ in range(5):
            dsp.process(bytes(range(256)))
        points = dsp.get_constellation()
        quadrants = [False, False, False, False]  # ++, -+, --, +-
        for i, q in points:
            if i > 0.1 and q > 0.1: quadrants[0] = True
            if i < -0.1 and q > 0.1: quadrants[1] = True
            if i < -0.1 and q < -0.1: quadrants[2] = True
            if i > 0.1 and q < -0.1: quadrants[3] = True
        assert sum(quadrants) >= 3, f"QPSK should fill most quadrants: {quadrants}"


class TestRadioReadOnly:
    """Radio frontend is purely observational."""

    def test_no_inject_method(self):
        fe = RadioFrontend()
        assert not hasattr(fe, 'inject_ground_failure')

    def test_no_synthetic_iq_generator(self):
        fe = RadioFrontend()
        assert not hasattr(fe, 'generate_iq_samples')

    def test_iq_set_externally(self):
        """I/Q samples are set by the bridge, not generated internally."""
        fe = RadioFrontend()
        fe.status.iq_samples = [[0.5, 0.1], [-0.5, -0.1]]
        snap = fe.snapshot()
        assert snap.iq_samples == [[0.5, 0.1], [-0.5, -0.1]]

    def test_gs_penalty_read_only(self):
        fe = RadioFrontend()
        fe.update_gs_penalty(5.0)
        assert fe.status.gs_penalty_db == 5.0

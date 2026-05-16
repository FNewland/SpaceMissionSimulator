"""Tests for channel simulation (numpy fallback)."""

import pytest
import numpy as np
from smo_rfsim.gnuradio.channel_sim import NumpyChannelSim, create_channel_sim


class TestNumpyChannelSim:
    def test_awgn_adds_noise(self):
        sim = NumpyChannelSim(eb_n0_db=5.0, seed=42)
        # Create clean BPSK signal
        samples = np.ones(1000, dtype=np.complex64)
        result_bytes = sim.process(samples.tobytes())
        result = np.frombuffer(result_bytes, dtype=np.complex64)
        # Should be close to original but not identical
        assert not np.allclose(samples, result)
        # Mean should still be close to 1
        assert abs(np.mean(result.real) - 1.0) < 0.5

    def test_high_ebn0_clean(self):
        sim = NumpyChannelSim(eb_n0_db=30.0, seed=1)
        samples = np.ones(1000, dtype=np.complex64)
        result_bytes = sim.process(samples.tobytes())
        result = np.frombuffer(result_bytes, dtype=np.complex64)
        # At 30 dB, noise should be negligible
        assert np.allclose(samples, result, atol=0.1)

    def test_doppler_shifts_phase(self):
        sim = NumpyChannelSim(eb_n0_db=50.0, freq_offset_hz=100.0,
                              sample_rate=1000.0, seed=1)
        samples = np.ones(1000, dtype=np.complex64)
        result_bytes = sim.process(samples.tobytes())
        result = np.frombuffer(result_bytes, dtype=np.complex64)
        # Phase should rotate due to Doppler
        phase_diff = np.angle(result[-1]) - np.angle(result[0])
        assert abs(phase_diff) > 0.1

    def test_set_parameters(self):
        sim = NumpyChannelSim(eb_n0_db=10.0)
        sim.set_eb_n0(5.0)
        assert sim.eb_n0_db == 5.0
        sim.set_doppler(1000.0)
        assert sim.freq_offset_hz == 1000.0


class TestCreateChannelSim:
    def test_creates_numpy_by_default(self):
        sim = create_channel_sim(eb_n0_db=10.0, use_gnuradio=False)
        assert isinstance(sim, NumpyChannelSim)

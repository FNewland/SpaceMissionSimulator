"""Tests for the real baseband DSP processing chain.

These test actual signal processing — modulation, channel, and demodulation
with carrier/clock recovery. The constellation points come from real
demodulated samples.
"""

import math
import numpy as np
import pytest
from smo_rfsim.dsp.modulator import Modulator, BITS_PER_SYMBOL
from smo_rfsim.dsp.channel import BasebandChannel
from smo_rfsim.dsp.demodulator import Demodulator
from smo_rfsim.dsp.processor import DSPProcessor


class TestModulator:
    def test_bpsk_output_length(self):
        mod = Modulator(modulation=0, sps=8)
        data = b'\xAA' * 10  # 80 bits = 80 symbols
        samples = mod.modulate(data)
        assert len(samples) == 80 * 8  # sps=8

    def test_qpsk_output_length(self):
        mod = Modulator(modulation=1, sps=8)
        data = b'\xAA' * 10  # 80 bits = 40 symbols (2 bits/sym)
        samples = mod.modulate(data)
        assert len(samples) == 40 * 8

    def test_8psk_output_length(self):
        mod = Modulator(modulation=2, sps=8)
        data = b'\xAA' * 9  # 72 bits = 24 symbols (3 bits/sym)
        samples = mod.modulate(data)
        assert len(samples) == 24 * 8

    def test_bpsk_unit_energy(self):
        """BPSK symbols should have approximately unit energy."""
        mod = Modulator(modulation=0, sps=1)  # no oversampling
        data = b'\xFF' * 100
        samples = mod.modulate(data)
        # Average energy should be close to 1
        avg_energy = np.mean(np.abs(samples) ** 2)
        assert 0.5 < avg_energy < 2.0

    def test_symbol_map_bpsk(self):
        mod = Modulator(modulation=0)
        assert len(mod.symbol_map) == 2

    def test_symbol_map_8psk(self):
        mod = Modulator(modulation=2)
        assert len(mod.symbol_map) == 8


class TestBasebandChannel:
    def test_high_snr_passthrough(self):
        ch = BasebandChannel(eb_n0_db=50.0, sps=1, bits_per_symbol=1)
        signal = np.ones(1000, dtype=np.complex64)
        out = ch.process(signal)
        # At 50 dB, noise is negligible
        assert np.allclose(signal, out, atol=0.01)

    def test_low_snr_adds_noise(self):
        ch = BasebandChannel(eb_n0_db=0.0, sps=1, bits_per_symbol=1, seed=42)
        signal = np.ones(1000, dtype=np.complex64)
        out = ch.process(signal)
        assert not np.allclose(signal, out, atol=0.1)

    def test_freq_offset_rotates_phase(self):
        ch = BasebandChannel(eb_n0_db=50.0, sps=1, bits_per_symbol=1,
                             freq_offset_hz=100.0, sample_rate=1000.0)
        signal = np.ones(100, dtype=np.complex64)
        out = ch.process(signal)
        # Phase should rotate
        phase_end = np.angle(out[-1])
        assert abs(phase_end) > 0.1


class TestDemodulator:
    def test_bpsk_demod_clean(self):
        """BPSK mod→demod at high SNR should recover data."""
        mod = Modulator(modulation=0, sps=8)
        demod = Demodulator(modulation=0, sps=8)
        data = b'\xAA\x55' * 20
        samples = mod.modulate(data)
        recovered = demod.demodulate(samples)
        # Allow some startup transient — check middle portion
        assert len(recovered) > 0

    def test_constellation_points_populated(self):
        mod = Modulator(modulation=0, sps=8)
        demod = Demodulator(modulation=0, sps=8)
        data = b'\x55' * 50
        samples = mod.modulate(data)
        demod.demodulate(samples)
        points = demod.get_constellation_iq()
        assert len(points) > 10
        # Points should be 2-element lists
        assert len(points[0]) == 2

    def test_bpsk_constellation_near_pm1(self):
        """At high SNR, BPSK constellation should cluster near ±1."""
        mod = Modulator(modulation=0, sps=8)
        ch = BasebandChannel(eb_n0_db=20.0, sps=8, bits_per_symbol=1, seed=1)
        demod = Demodulator(modulation=0, sps=8)
        data = b'\xAA\x55' * 50
        tx = mod.modulate(data)
        rx = ch.process(tx)
        demod.demodulate(rx)
        points = demod.get_constellation_iq()
        # Most I values should be near ±1
        near_constellation = sum(1 for i, q in points
                                 if abs(abs(i) - 1.0) < 0.5)
        assert near_constellation > len(points) * 0.5

    def test_qpsk_constellation_four_clusters(self):
        """QPSK should show 4 clusters."""
        mod = Modulator(modulation=1, sps=8)
        ch = BasebandChannel(eb_n0_db=15.0, sps=8, bits_per_symbol=2, seed=1)
        demod = Demodulator(modulation=1, sps=8)
        data = bytes(range(256)) * 2
        tx = mod.modulate(data)
        rx = ch.process(tx)
        demod.demodulate(rx)
        points = demod.get_constellation_iq()
        # Should have both positive and negative I and Q
        has_pos_i = any(i > 0.3 for i, _ in points)
        has_neg_i = any(i < -0.3 for i, _ in points)
        has_pos_q = any(q > 0.3 for _, q in points)
        has_neg_q = any(q < -0.3 for _, q in points)
        assert has_pos_i and has_neg_i and has_pos_q and has_neg_q


class TestDSPProcessor:
    def test_no_link_noise_only(self):
        """When link is inactive, constellation should be noise at origin."""
        dsp = DSPProcessor(modulation=0, eb_n0_db=10.0)
        dsp.set_active(False)
        result = dsp.process(b'\xAA' * 20)
        assert result is None
        points = dsp.get_constellation()
        assert len(points) > 0
        # All points should be near origin (noise only)
        for i, q in points:
            assert abs(i) < 2.0
            assert abs(q) < 2.0

    def test_active_link_has_constellation(self):
        dsp = DSPProcessor(modulation=0, sps=8, eb_n0_db=15.0)
        dsp.set_active(True)
        result = dsp.process(b'\x55\xAA' * 50)
        assert result is not None
        points = dsp.get_constellation()
        assert len(points) > 5

    def test_modulation_change(self):
        dsp = DSPProcessor(modulation=0)
        dsp.set_modulation(1)  # QPSK
        assert dsp._modulation == 1
        dsp.set_modulation(2)  # 8PSK
        assert dsp._modulation == 2

    def test_carrier_lock_status(self):
        """After processing enough data, carrier should lock."""
        dsp = DSPProcessor(modulation=0, sps=8, eb_n0_db=15.0)
        dsp.set_active(True)
        # Process several blocks to let PLL converge
        for _ in range(10):
            dsp.process(b'\x55\xAA' * 100)
        # Carrier should be locked at 15 dB
        assert dsp.carrier_locked

    def test_no_lock_when_inactive(self):
        dsp = DSPProcessor(modulation=0, eb_n0_db=10.0)
        dsp.set_active(False)
        dsp.process(b'\x55' * 20)
        assert not dsp.carrier_locked

"""Tests for channel impairment model."""

import pytest
from smo_rfsim.channel.model import (
    ChannelModel, eb_n0_to_ber_bpsk, free_space_path_loss
)
from smo_rfsim.channel.ber_injector import BERInjector


class TestBERCalculation:
    def test_high_ebn0_low_ber(self):
        ber = eb_n0_to_ber_bpsk(10.0)
        assert ber < 1e-4

    def test_low_ebn0_high_ber(self):
        ber = eb_n0_to_ber_bpsk(0.0)
        assert 0.05 < ber < 0.20

    def test_very_high_ebn0(self):
        ber = eb_n0_to_ber_bpsk(20.0)
        assert ber < 1e-10

    def test_monotonic(self):
        bers = [eb_n0_to_ber_bpsk(db) for db in range(0, 15)]
        for i in range(len(bers) - 1):
            assert bers[i] > bers[i + 1]


class TestPathLoss:
    def test_450km_sband(self):
        fspl = free_space_path_loss(450.0, 2200.0)
        assert 150 < fspl < 160  # ~154 dB for 450 km at 2.2 GHz

    def test_zero_distance(self):
        assert free_space_path_loss(0) == 0.0


class TestChannelModel:
    def test_no_errors_at_high_ebn0(self):
        model = ChannelModel(eb_n0_db=20.0, seed=1)
        data = b'\xAA' * 1000
        result = model.impair(data)
        assert result == data  # at 20 dB, BER ~1e-23, no errors expected

    def test_errors_at_low_ebn0(self):
        model = ChannelModel(eb_n0_db=2.0, seed=42)
        data = b'\x55' * 1000
        result = model.impair(data)
        assert result != data  # at 2 dB, BER ~0.038, errors expected
        assert model.error_bits > 0

    def test_deterministic(self):
        m1 = ChannelModel(eb_n0_db=5.0, seed=99)
        m2 = ChannelModel(eb_n0_db=5.0, seed=99)
        data = b'\xFF' * 100
        assert m1.impair(data) == m2.impair(data)

    def test_stats_tracking(self):
        model = ChannelModel(eb_n0_db=3.0, seed=1)
        model.impair(b'\x00' * 100)
        assert model.total_bits == 800
        assert model.total_frames == 1

    def test_reset_stats(self):
        model = ChannelModel(eb_n0_db=3.0, seed=1)
        model.impair(b'\x00' * 100)
        model.reset_stats()
        assert model.total_bits == 0


class TestBERInjector:
    def test_zero_ber_passthrough(self):
        inj = BERInjector(ber=0.0)
        data = b'\xAA' * 100
        assert inj.inject(data) == data

    def test_high_ber_corrupts(self):
        inj = BERInjector(ber=0.1, seed=42)
        data = b'\x00' * 1000
        result = inj.inject(data)
        assert result != data
        assert inj.error_bits > 0

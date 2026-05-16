"""Tests for GNU Radio bridge (pure-Python fallback mode)."""

import pytest
from smo_rfsim.channel.model import ChannelModel
from smo_rfsim.gnuradio.gr_bridge import (
    create_rf_processor, PurePythonRFProcessor, HAS_GNURADIO
)


class TestPurePythonRFProcessor:
    def test_passthrough_high_ebn0(self):
        channel = ChannelModel(eb_n0_db=20.0, seed=1)
        proc = PurePythonRFProcessor(channel)
        data = b'\xAA' * 100
        result = proc.modulate_and_transmit(data)
        assert result == data  # at 20 dB, no errors

    def test_impair_low_ebn0(self):
        channel = ChannelModel(eb_n0_db=2.0, seed=42)
        proc = PurePythonRFProcessor(channel)
        data = b'\x55' * 100
        result = proc.modulate_and_transmit(data)
        assert result != data

    def test_demodulate_passthrough(self):
        channel = ChannelModel(eb_n0_db=10.0)
        proc = PurePythonRFProcessor(channel)
        data = b'\xFF' * 50
        result = proc.receive_and_demodulate(data)
        assert result == data


class TestCreateRFProcessor:
    def test_creates_pure_python_by_default(self):
        channel = ChannelModel(eb_n0_db=10.0)
        proc = create_rf_processor(channel, use_gnuradio=False)
        assert isinstance(proc, PurePythonRFProcessor)

    def test_falls_back_without_gnuradio(self):
        channel = ChannelModel(eb_n0_db=10.0)
        proc = create_rf_processor(channel, use_gnuradio=True)
        if not HAS_GNURADIO:
            assert isinstance(proc, PurePythonRFProcessor)

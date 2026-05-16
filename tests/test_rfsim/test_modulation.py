"""Tests for multi-modulation support across the RF chain."""

import math
import pytest
from smo_rfsim.radio.frontend import RadioFrontend, LockState
from smo_rfsim.channel.model import eb_n0_to_ber


class TestModulationBER:
    def test_bpsk_ber(self):
        ber = eb_n0_to_ber(10.0, modulation=0)
        assert ber < 1e-4

    def test_qpsk_same_as_bpsk(self):
        """QPSK has same BER per bit as BPSK at same Eb/N0."""
        bpsk = eb_n0_to_ber(8.0, modulation=0)
        qpsk = eb_n0_to_ber(8.0, modulation=1)
        assert abs(bpsk - qpsk) < 1e-10

    def test_oqpsk_same_as_qpsk(self):
        oqpsk = eb_n0_to_ber(8.0, modulation=3)
        qpsk = eb_n0_to_ber(8.0, modulation=1)
        assert abs(oqpsk - qpsk) < 1e-10

    def test_8psk_worse_than_bpsk(self):
        """8PSK requires ~3.6 dB more Eb/N0 for same BER."""
        bpsk = eb_n0_to_ber(8.0, modulation=0)
        psk8 = eb_n0_to_ber(8.0, modulation=2)
        assert psk8 > bpsk  # 8PSK is worse at same Eb/N0

    def test_8psk_at_high_ebn0(self):
        ber = eb_n0_to_ber(14.0, modulation=2)
        assert ber < 1e-4

    def test_zero_ebn0(self):
        for mod in range(4):
            ber = eb_n0_to_ber(0.0, mod)
            assert 0.01 < ber < 0.5


class TestModulationDisplay:
    """Test modulation name tracking in Radio (display only)."""

    def test_modulation_name_bpsk(self):
        fe = RadioFrontend()
        fe.update_modulation(0)
        assert fe.status.modulation_name == "BPSK"

    def test_modulation_name_qpsk(self):
        fe = RadioFrontend()
        fe.update_modulation(1)
        assert fe.status.modulation_name == "QPSK"

    def test_modulation_name_8psk(self):
        fe = RadioFrontend()
        fe.update_modulation(2)
        assert fe.status.modulation_name == "8PSK"

    def test_modulation_name_oqpsk(self):
        fe = RadioFrontend()
        fe.update_modulation(3)
        assert fe.status.modulation_name == "OQPSK"

    def test_modulation_in_snapshot(self):
        fe = RadioFrontend()
        fe.update_modulation(2)
        snap = fe.snapshot()
        assert snap.modulation == 2
        assert snap.modulation_name == "8PSK"
        assert snap.modulation_name == "8PSK"


class TestTTCModulation:
    """Test modulation handling in the TTC model."""

    def test_ber_for_modulation_bpsk(self):
        from smo_simulator.models.ttc_basic import TTCBasicModel
        ber = TTCBasicModel._ber_for_modulation(0, 10.0)  # 10 dB linear = 10
        assert ber < 0.01

    def test_ber_for_modulation_8psk_worse(self):
        from smo_simulator.models.ttc_basic import TTCBasicModel
        bpsk_ber = TTCBasicModel._ber_for_modulation(0, 10.0)
        psk8_ber = TTCBasicModel._ber_for_modulation(2, 10.0)
        assert psk8_ber > bpsk_ber

    def test_modulation_params_table(self):
        from smo_simulator.models.ttc_basic import TTCBasicModel
        assert 0 in TTCBasicModel.MODULATION_PARAMS
        assert TTCBasicModel.MODULATION_PARAMS[0]["name"] == "BPSK"
        assert TTCBasicModel.MODULATION_PARAMS[2]["bits_per_symbol"] == 3

    def test_set_modulation_command(self):
        from smo_simulator.models.ttc_basic import TTCBasicModel
        model = TTCBasicModel()
        model.configure({})
        result = model.handle_command({"command": "set_modulation", "mode": 2})
        assert result["success"]
        assert model._state.modulation_mode == 2
        # Data rate should triple (8PSK = 3 bits/symbol)
        assert model._state.tm_data_rate == 64000 * 3

    def test_set_modulation_invalid(self):
        from smo_simulator.models.ttc_basic import TTCBasicModel
        model = TTCBasicModel()
        model.configure({})
        result = model.handle_command({"command": "set_modulation", "mode": 5})
        assert not result["success"]

    def test_set_modulation_back_to_bpsk(self):
        from smo_simulator.models.ttc_basic import TTCBasicModel
        model = TTCBasicModel()
        model.configure({})
        model.handle_command({"command": "set_modulation", "mode": 2})
        assert model._state.tm_data_rate == 64000 * 3
        model.handle_command({"command": "set_modulation", "mode": 0})
        assert model._state.tm_data_rate == 64000
        assert model._state.modulation_mode == 0

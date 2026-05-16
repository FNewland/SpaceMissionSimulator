"""Tests for RF simulation configuration."""

import pytest
from pathlib import Path
from smo_rfsim.config import RFSimConfig, CCSDSConfig, ChannelConfig, NetworkConfig
from smo_rfsim.mode import RFSimMode


RFSIM_YAML = Path(__file__).parent.parent.parent / "configs" / "eosat1" / "rfsim.yaml"


class TestRFSimConfig:
    def test_defaults(self):
        cfg = RFSimConfig()
        assert cfg.mode == RFSimMode.PACKET
        assert cfg.ccsds.tm_frame_length == 1115
        assert cfg.ccsds.scid == 1
        assert cfg.channel.eb_n0_db == 10.0
        assert cfg.network.sim_tm_port == 8002
        assert cfg.network.mcs_tm_port == 8012

    def test_load_yaml(self):
        if not RFSIM_YAML.exists():
            pytest.skip("rfsim.yaml not found")
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        assert cfg.mode == RFSimMode.PACKET
        assert cfg.ccsds.scid == 1
        assert cfg.network.radio_port == 8094


class TestRFSimMode:
    def test_enum_values(self):
        assert RFSimMode.PACKET.value == "PACKET"
        assert RFSimMode.FRAME.value == "FRAME"
        assert RFSimMode.RF.value == "RF"

    def test_from_string(self):
        assert RFSimMode("PACKET") == RFSimMode.PACKET
        assert RFSimMode("FRAME") == RFSimMode.FRAME

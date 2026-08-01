"""RF pipeline follows the TM data-rate ladder — defect #85.

A tier change must be visible on the radio, not just in a text box: the tier
drives the modulation (constellation), the bit rate and therefore the symbol
rate (occupied bandwidth and eye timing), and the FEC that is reported.

    BEACON    1 000 bps  BPSK   1 000 Bd   3.1 kHz occupied
    LOW      16 000 bps  BPSK  16 000 Bd    50 kHz  (routine operations)
    HIGH    128 000 bps  QPSK  64 000 Bd   100 kHz  (dumps only)
"""
from pathlib import Path

import pytest
import yaml

from smo_rfsim.bridge import RFSimBridge
from smo_rfsim.config import RFSimConfig
from smo_rfsim.mode import RFSimMode
from smo_rfsim.pipeline.coordinator import PipelineCoordinator

REPO = Path(__file__).resolve().parents[2]
RFSIM_YAML = REPO / "configs" / "eosat1" / "rfsim.yaml"
TTC_YAML = REPO / "configs" / "eosat1" / "subsystems" / "ttc.yaml"

TIERS = yaml.safe_load(TTC_YAML.read_text())["tm_rate_tiers"]
BITS_PER_SYMBOL = {"BPSK": 1, "QPSK": 2, "8PSK": 3, "OQPSK": 2}
MOD_INDEX = {"BPSK": 0, "QPSK": 1, "8PSK": 2, "OQPSK": 3}

#: NASA Explorers S-band table: 5.00 MHz maximum per mission in 2200-2290 MHz.
CHANNEL_BW_HZ = 5.0e6


def _rf_config():
    cfg = RFSimConfig()
    cfg.mode = RFSimMode.RF
    cfg.network.zmq_samples_port = 0
    cfg.network.radio_control_enabled = True
    cfg.ccsds.convolutional_enabled = False
    return cfg


def _feed(tier: dict) -> dict:
    """The `ttc` block the simulator's WS feed publishes for a tier."""
    return {"state": {"ttc": {
        "data_rate_bps": tier["bps"],
        "modulation_mode": MOD_INDEX[tier["modulation"]],
        "tm_tier": TIERS.index(tier),
        "tm_tier_name": tier["name"],
        "tm_coding": tier["coding"],
    }}}


class TestConfigAcceptsTheLadder:

    def test_max_symbol_rate_admits_the_top_tier(self):
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        top = TIERS[-1]
        needed = top["bps"] / BITS_PER_SYMBOL[top["modulation"]]
        assert cfg.pipeline.max_symbol_rate >= needed, (
            f"pipeline.max_symbol_rate clamps the {top['name']} tier")

    def test_max_symbol_rate_is_the_regulatory_ceiling(self):
        """Re-derived, not picked: 5.00 MHz / (1 + alpha) at alpha = 0.35."""
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        legal = CHANNEL_BW_HZ / (1.0 + cfg.pipeline.rrc_rolloff)
        assert cfg.pipeline.max_symbol_rate == pytest.approx(legal, rel=1e-3)

    def test_default_symbol_rate_is_the_routine_operations_tier(self):
        """The pipeline idles at LOW — routine ops — not at the top tier."""
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        low = next(t for t in TIERS if t["name"] == "LOW")
        assert cfg.pipeline.symbol_rate == (
            low["bps"] / BITS_PER_SYMBOL[low["modulation"]])

    def test_tc_symbol_rate_is_the_uplink_rate_not_a_tm_tier(self):
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        ttc = yaml.safe_load(TTC_YAML.read_text())
        assert cfg.pipeline.tc_symbol_rate == ttc["tc_uplink_rate_bps"]

    def test_link_budget_agrees_with_ttc_yaml(self):
        """rfsim.yaml's link budget and ttc.yaml's must be one budget."""
        rf = RFSimConfig.from_yaml(RFSIM_YAML)
        ttc = yaml.safe_load(TTC_YAML.read_text())
        lb = rf.link_budget
        assert lb.eirp_dbw == ttc["eirp_dbw"]
        assert lb.gs_gt_dbk == ttc["gs_g_t_db"]
        assert lb.atmospheric_loss_db == ttc["atmospheric_loss_db"]
        assert lb.implementation_loss_db == ttc["implementation_loss_db"]
        assert lb.polarization_loss_db == ttc["polarization_loss_db"]
        assert lb.coding_gain_db == ttc["coding_gain_db"]
        # rfsim's flat coding gain is the RS-only figure, i.e. the HIGH tier.
        for t in TIERS:
            if t["coding"] == "RS":
                assert t["coding_gain_db"] == lb.coding_gain_db

    def test_downlink_frequency_agrees(self):
        rf = RFSimConfig.from_yaml(RFSIM_YAML)
        ttc = yaml.safe_load(TTC_YAML.read_text())
        assert rf.channel.frequency_hz == pytest.approx(
            ttc["dl_freq_mhz"] * 1e6)


class TestPipelineRetunesPerTier:

    @pytest.mark.parametrize("tier", TIERS, ids=[t["name"] for t in TIERS])
    def test_symbol_rate_and_bandwidth_per_tier(self, tier):
        cfg = _rf_config()
        pc = PipelineCoordinator(cfg)
        try:
            pc.reconfigure(modulation=MOD_INDEX[tier["modulation"]])
            sr = pc.set_data_rate_bps(tier["bps"])
            expected = tier["bps"] / BITS_PER_SYMBOL[tier["modulation"]]
            assert sr == expected, (
                f"{tier['name']}: {sr} Bd, expected {expected} Bd")
            assert pc._tx._symbol_rate == expected
            assert pc._rx._symbol_rate == expected
            assert pc._channel.sample_rate == expected * cfg.pipeline.sps
            occupied = expected * (1.0 + cfg.pipeline.rrc_rolloff)
            assert occupied < CHANNEL_BW_HZ, (
                f"{tier['name']} occupies {occupied / 1e6:.2f} MHz, over the "
                f"{CHANNEL_BW_HZ / 1e6:.2f} MHz mission channel")
        finally:
            pc.stop()

    def test_top_tier_is_not_clamped(self):
        """The ceiling must never silently clamp a configured tier."""
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        cfg.mode = RFSimMode.RF
        cfg.network.zmq_samples_port = 0
        pc = PipelineCoordinator(cfg)
        try:
            pc.reconfigure(modulation=1)         # QPSK
            assert pc.set_data_rate_bps(128000) == 64000.0
            # And it still has room if the tier is retuned upward.
            assert pc.set_data_rate_bps(1024000) == 512000.0
        finally:
            pc.stop()


class TestBridgeFollowsTheTier:

    def _bridge(self):
        return RFSimBridge(_rf_config())

    def test_each_tier_moves_modulation_and_symbol_rate(self):
        bridge = self._bridge()
        try:
            for tier in TIERS:
                bridge._apply_sim_feedback(_feed(tier))
                expected = tier["bps"] / BITS_PER_SYMBOL[tier["modulation"]]
                assert bridge._pipeline.symbol_rate == expected, tier["name"]
                assert bridge.radio.status.modulation == MOD_INDEX[
                    tier["modulation"]], tier["name"]
                assert bridge.radio.status.modulation_name == tier["modulation"]
                assert bridge.radio.status.tm_tier_name == tier["name"]
                assert bridge.radio.status.tm_coding == tier["coding"]
                assert bridge.radio.status.data_rate_kbps == tier["bps"] / 1000.0
        finally:
            bridge._pipeline.stop()

    def test_a_modulation_change_at_the_same_bit_rate_halves_the_symbol_rate(self):
        """The rate cache must not suppress a retune driven by modulation.

        Two tiers could share a bit rate and differ in modulation; more to the
        point, BPSK->QPSK at a fixed bit rate halves the occupied bandwidth,
        and that has to reach the spectrum display.
        """
        bridge = self._bridge()
        try:
            bpsk = {"state": {"ttc": {"data_rate_bps": 128000,
                                      "modulation_mode": 0,
                                      "tm_tier_name": "X-BPSK",
                                      "tm_coding": "RS"}}}
            qpsk = {"state": {"ttc": {"data_rate_bps": 128000,
                                      "modulation_mode": 1,
                                      "tm_tier_name": "X-QPSK",
                                      "tm_coding": "RS"}}}
            bridge._apply_sim_feedback(bpsk)
            assert bridge._pipeline.symbol_rate == 128000.0
            bridge._apply_sim_feedback(qpsk)
            assert bridge._pipeline.symbol_rate == 64000.0
        finally:
            bridge._pipeline.stop()

    def test_occupied_bandwidth_is_published(self):
        bridge = self._bridge()
        try:
            high = next(t for t in TIERS if t["name"] == "HIGH")
            bridge._apply_sim_feedback(_feed(high))
            bw = bridge.radio.status.occupied_bw_hz
            assert bw == pytest.approx(64000 * 1.35, rel=1e-6)
            assert bw < 0.05 * CHANNEL_BW_HZ
        finally:
            bridge._pipeline.stop()

    def test_coding_follows_the_tier_when_enabled(self):
        bridge = self._bridge()
        bridge.config.ccsds.convolutional_follow_tier = True
        try:
            low = next(t for t in TIERS if t["name"] == "LOW")
            high = next(t for t in TIERS if t["name"] == "HIGH")
            bridge._apply_sim_feedback(_feed(low))
            assert bridge.config.ccsds.convolutional_enabled is True
            bridge._apply_sim_feedback(_feed(high))
            assert bridge.config.ccsds.convolutional_enabled is False
        finally:
            bridge._pipeline.stop()

    def test_coding_is_left_alone_when_follow_is_off(self):
        """Default: the pure-Python Viterbi is too slow, so the flag is
        reported but the decoder is not switched on behind the operator."""
        bridge = self._bridge()
        assert bridge.config.ccsds.convolutional_follow_tier is False
        try:
            low = next(t for t in TIERS if t["name"] == "LOW")
            bridge._apply_sim_feedback(_feed(low))
            assert bridge.config.ccsds.convolutional_enabled is False
            assert bridge.radio.status.tm_coding == "CONV_RS"
        finally:
            bridge._pipeline.stop()

    def test_manual_override_still_wins(self):
        bridge = self._bridge()
        try:
            bridge.apply_pipeline({"symbol_rate": 12345.0})
            bridge._apply_sim_feedback(
                _feed(next(t for t in TIERS if t["name"] == "HIGH")))
            assert bridge._pipeline.symbol_rate == 12345.0
        finally:
            bridge._pipeline.stop()

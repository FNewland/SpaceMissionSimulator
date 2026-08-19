"""Radio configuration: dead config, strict validation, and the runtime
control plane (defect #55).

Before these fixes `channel.frequency_hz` / `awgn_enabled` / `path_loss_db`
were silently discarded by a `hasattr` filter, `RFSimConfig` had no
`pipeline` section at all (so the pipeline was pinned at 32 kBd BPSK), and
the radio web UI had three GET routes and no way to change anything.
"""

from pathlib import Path

import pytest
import yaml

from smo_rfsim.config import (
    ChannelConfig, ConfigError, LinkBudgetConfig, PipelineConfig, RFSimConfig,
)
from smo_rfsim.mode import RFSimMode
from smo_rfsim.pipeline.coordinator import PipelineCoordinator
from smo_rfsim.bridge import RFSimBridge

REPO = Path(__file__).resolve().parents[2]
RFSIM_YAML = REPO / "configs" / "eosat1" / "rfsim.yaml"
GS_YAML = REPO / "configs" / "eosat1" / "planning" / "ground_stations.yaml"


def _rf_config():
    cfg = RFSimConfig()
    cfg.mode = RFSimMode.RF
    cfg.network.zmq_samples_port = 0
    cfg.network.radio_control_enabled = True
    cfg.ccsds.convolutional_enabled = False
    return cfg


class TestDeadConfigIsNowLive:
    def test_pipeline_section_exists(self):
        cfg = RFSimConfig()
        assert isinstance(cfg.pipeline, PipelineConfig)
        assert cfg.pipeline.symbol_rate == 32000.0

    def test_frequency_hz_is_no_longer_discarded(self):
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        assert cfg.channel.frequency_hz == pytest.approx(2200.0e6)
        assert cfg.channel.uplink_frequency_hz == pytest.approx(2025.0e6)

    def test_yaml_exponent_without_sign_is_coerced_to_float(self):
        """PyYAML parses `2200.0e6` as a *string*; the loader must coerce."""
        assert isinstance(
            yaml.safe_load(RFSIM_YAML.read_text())["channel"]["frequency_hz"],
            str)
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        assert isinstance(cfg.channel.frequency_hz, float)

    def test_awgn_enabled_reaches_the_channel(self):
        cfg = _rf_config()
        cfg.channel.awgn_enabled = False
        pc = PipelineCoordinator(cfg)
        assert pc.get_channel_status()["awgn_enabled"] is False

    def test_pipeline_config_drives_the_pipeline(self):
        cfg = _rf_config()
        cfg.pipeline.symbol_rate = 8000.0
        cfg.pipeline.sps = 4
        cfg.pipeline.modulation = 1
        pc = PipelineCoordinator(cfg)
        assert pc.symbol_rate == 8000.0
        assert pc.sample_rate == 32000.0
        assert pc.sps == 4
        assert pc._tx._modulation == 1


class TestStrictValidation:
    def test_unknown_section_key_is_rejected(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("channel:\n  eb_no_db: 10.0\n")   # typo
        with pytest.raises(ConfigError) as exc:
            RFSimConfig.from_yaml(p)
        assert "channel.eb_no_db" in str(exc.value)

    def test_unknown_top_level_key_is_rejected(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("chanel:\n  eb_n0_db: 10.0\n")
        with pytest.raises(ConfigError):
            RFSimConfig.from_yaml(p)

    def test_non_strict_mode_warns_instead(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("nonsense: 1\n")
        cfg = RFSimConfig.from_yaml(p, strict=False)
        assert cfg.mode == RFSimMode.PACKET

    def test_out_of_range_values_are_rejected(self):
        with pytest.raises(ConfigError):
            PipelineConfig(sps=1).validate()
        with pytest.raises(ConfigError):
            PipelineConfig(modulation=9).validate()
        with pytest.raises(ConfigError):
            ChannelConfig(frequency_hz=100.0).validate()
        with pytest.raises(ConfigError):
            LinkBudgetConfig(coding_gain_db=99.0).validate()

    def test_shipped_config_validates(self):
        RFSimConfig.from_yaml(RFSIM_YAML).validate()


class TestFrequencyPlanCoherence:
    """Roadmap N-4 / DI-05: one S-band plan across every config file."""

    def test_rfsim_matches_ground_stations(self):
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        stations = yaml.safe_load(GS_YAML.read_text())["ground_stations"]
        for st in stations:
            assert st["rx_freq_mhz"] * 1e6 == pytest.approx(
                cfg.channel.frequency_hz), f"{st['name']} downlink mismatch"
            assert st["tx_freq_mhz"] * 1e6 == pytest.approx(
                cfg.channel.uplink_frequency_hz), f"{st['name']} uplink mismatch"

    def test_ttc_subsystem_matches_rfsim(self):
        ttc = yaml.safe_load(
            (REPO / "configs" / "eosat1" / "subsystems" / "ttc.yaml").read_text())
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        assert ttc["dl_freq_mhz"] * 1e6 == pytest.approx(cfg.channel.frequency_hz)
        assert ttc["ul_freq_mhz"] * 1e6 == pytest.approx(
            cfg.channel.uplink_frequency_hz)

    def test_frequencies_are_in_the_ccsds_s_band_allocations(self):
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        assert 2025e6 <= cfg.channel.uplink_frequency_hz <= 2110e6
        assert 2200e6 <= cfg.channel.frequency_hz <= 2290e6


class TestGroundStationGT:
    def test_per_station_gt_is_loaded(self):
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        assert cfg.link_budget.station_gt_dbk, "no per-station G/T loaded"
        assert cfg.link_budget.gt_for_station("Iqaluit") == pytest.approx(20.0)
        assert cfg.link_budget.gt_for_station("Troll") == pytest.approx(18.5)

    def test_unknown_station_falls_back_to_default(self):
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        assert cfg.link_budget.gt_for_station("Nowhere") == pytest.approx(20.0)

    def test_station_selection_changes_the_link_budget(self):
        cfg = RFSimConfig.from_yaml(RFSIM_YAML)
        cfg.mode = RFSimMode.RF
        cfg.network.zmq_samples_port = 0
        pc = PipelineCoordinator(cfg)
        pc.set_ground_station("Iqaluit")
        iqaluit = pc.get_link_budget(range_km=800, data_rate_bps=32000)
        pc.set_ground_station("Troll")
        troll = pc.get_link_budget(range_km=800, data_rate_bps=32000)
        assert iqaluit["gs_gt_dbk"] == pytest.approx(20.0)
        assert troll["gs_gt_dbk"] == pytest.approx(18.5)
        assert iqaluit["cn0_dbhz"] - troll["cn0_dbhz"] == pytest.approx(1.5,
                                                                       abs=0.01)


class TestSymbolRateTracksDataRate:
    def test_set_data_rate_bps_retunes_tx_channel_and_rx(self):
        cfg = _rf_config()
        pc = PipelineCoordinator(cfg)
        assert pc.symbol_rate == 32000.0
        pc.set_data_rate_bps(64000)
        assert pc.symbol_rate == 64000.0            # BPSK: 1 bit/symbol
        assert pc.sample_rate == 64000.0 * 8
        assert pc._tx._symbol_rate == 64000.0
        assert pc._rx._symbol_rate == 64000.0
        assert pc._channel.sample_rate == 64000.0 * 8

    def test_qpsk_halves_the_symbol_rate_for_the_same_bit_rate(self):
        cfg = _rf_config()
        pc = PipelineCoordinator(cfg)
        pc.reconfigure(modulation=1)
        pc.set_data_rate_bps(64000)
        assert pc.symbol_rate == 32000.0

    def test_rate_is_clamped_to_configured_limits(self):
        cfg = _rf_config()
        pc = PipelineCoordinator(cfg)
        pc.set_data_rate_bps(10_000_000)
        assert pc.symbol_rate == cfg.pipeline.max_symbol_rate
        pc.set_data_rate_bps(1)
        assert pc.symbol_rate == cfg.pipeline.min_symbol_rate

    def test_tracking_can_be_disabled(self):
        cfg = _rf_config()
        cfg.pipeline.track_data_rate = False
        pc = PipelineCoordinator(cfg)
        pc.set_data_rate_bps(64000)
        assert pc.symbol_rate == 32000.0

    def test_sim_feedback_retunes_the_pipeline(self):
        """bridge._apply_sim_feedback used to only update a text label."""
        bridge = RFSimBridge(_rf_config())
        try:
            bridge._apply_sim_feedback(
                {"state": {"ttc": {"data_rate_bps": 64000}}})
            assert bridge.radio.status.data_rate_kbps == 64.0
            assert bridge._pipeline.symbol_rate == 64000.0
            bridge._apply_sim_feedback(
                {"state": {"ttc": {"data_rate_bps": 1000}}})
            assert bridge._pipeline.symbol_rate == 1000.0
        finally:
            bridge._pipeline.stop()


class TestControlPlane:
    def test_control_disabled_by_default(self):
        cfg = _rf_config()
        cfg.network.radio_control_enabled = False
        bridge = RFSimBridge(cfg)
        try:
            with pytest.raises(PermissionError):
                bridge.apply_channel({"eb_n0_db": 5.0})
            with pytest.raises(PermissionError):
                bridge.apply_pipeline({"modulation": 1})
            with pytest.raises(PermissionError):
                bridge.apply_link({"in_view": True})
        finally:
            bridge._pipeline.stop()

    def test_channel_config_round_trip(self):
        bridge = RFSimBridge(_rf_config())
        try:
            out = bridge.apply_channel({
                "eb_n0_db": 6.5,
                "fading_enabled": True,
                "fading_k_factor_db": 4.0,
                "phase_noise_enabled": True,
                "phase_noise_linewidth_hz": 250.0,
                "interferers": [{"type": "cw", "freq_offset_hz": 15000,
                                 "power_dbm": -45}],
            })
            ch = out["channel"]
            assert ch["eb_n0_db"] == 6.5
            assert ch["fading_enabled"] is True
            assert ch["fading_k_factor_db"] == 4.0
            assert ch["phase_noise_enabled"] is True
            status = out["channel_status"]
            assert status["n_interferers"] == 1
            assert status["fading_k_factor_db"] == 4.0
            # And it comes back the same way through GET /api/config
            again = bridge.get_effective_config()
            assert again["channel"]["eb_n0_db"] == 6.5
            assert again["channel_status"]["phase_noise_enabled"] is True
        finally:
            bridge._pipeline.stop()

    def test_pipeline_config_round_trip(self):
        bridge = RFSimBridge(_rf_config())
        try:
            out = bridge.apply_pipeline({"modulation": 2, "symbol_rate": 16000})
            assert out["pipeline"]["modulation"] == 2
            assert out["pipeline"]["symbol_rate"] == 16000.0
            assert out["symbol_rate_bd"] == 16000.0
            assert bridge.radio.status.modulation_name == "8PSK"
        finally:
            bridge._pipeline.stop()

    def test_bad_channel_key_is_ignored_and_bad_impairment_raises(self):
        bridge = RFSimBridge(_rf_config())
        try:
            # Unknown top-level keys are simply not applied...
            bridge.apply_channel({"not_a_key": 1})
            # ...but a bad impairment type is a loud error
            with pytest.raises(ValueError):
                bridge.apply_channel({"interferers": [{"type": "wormhole"}]})
        finally:
            bridge._pipeline.stop()

    def test_manual_override_beats_sim_feedback(self):
        """Sim WS feedback must not stomp an operator setting a second later."""
        bridge = RFSimBridge(_rf_config())
        try:
            bridge.apply_channel({"eb_n0_db": 3.0})
            bridge._apply_sim_feedback({"state": {"ttc": {"eb_n0_db": 18.0}}})
            assert bridge.config.channel.eb_n0_db == 3.0
            # ...until released
            bridge.apply_channel({"release": "all"})
            bridge._apply_sim_feedback({"state": {"ttc": {"eb_n0_db": 18.0}}})
            assert bridge.config.channel.eb_n0_db == 18.0
        finally:
            bridge._pipeline.stop()

    def test_link_control(self):
        bridge = RFSimBridge(_rf_config())
        try:
            bridge.apply_link({"in_view": True, "transmitting": True,
                               "ground_station": "Troll"})
            assert bridge._pipeline._link_in_view is True
            assert bridge._pipeline._tx._transmitting is True
            bridge.apply_link({"in_view": False})
            assert bridge._pipeline._link_in_view is False
        finally:
            bridge._pipeline.stop()

    def test_diagnostics_are_exposed(self):
        bridge = RFSimBridge(_rf_config())
        try:
            diag = bridge.get_diagnostics()
            assert "rx_good_frames" in diag
            assert "tx_frames_transmitted" in diag
        finally:
            bridge._pipeline.stop()


class TestWebRoutes:
    """The web UI gained a control plane; the routes must exist and honour
    the enable flag."""

    @pytest.fixture
    def bridge(self):
        b = RFSimBridge(_rf_config())
        yield b
        b._pipeline.stop()

    def _client(self, bridge):
        from aiohttp.test_utils import TestClient, TestServer
        from smo_rfsim.radio.web_ui import build_app
        return TestClient(TestServer(build_app(bridge.radio, control=bridge)))

    @pytest.mark.asyncio
    async def test_get_config_and_diagnostics(self, bridge):
        client = self._client(bridge)
        await client.start_server()
        try:
            r = await client.get("/api/config")
            assert r.status == 200
            body = await r.json()
            assert body["channel"]["frequency_hz"] > 0
            assert body["pipeline"]["symbol_rate"] > 0
            r = await client.get("/api/diagnostics")
            assert r.status == 200
            assert "rx_good_frames" in await r.json()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_post_channel_round_trip(self, bridge):
        client = self._client(bridge)
        await client.start_server()
        try:
            r = await client.post("/api/channel",
                                  json={"eb_n0_db": 7.25,
                                        "fading_enabled": True})
            assert r.status == 200
            body = await r.json()
            assert body["ok"] is True
            assert body["config"]["channel"]["eb_n0_db"] == 7.25
            r = await client.get("/api/config")
            assert (await r.json())["channel"]["fading_enabled"] is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_post_rejected_when_control_disabled(self, bridge):
        bridge.config.network.radio_control_enabled = False
        client = self._client(bridge)
        await client.start_server()
        try:
            r = await client.post("/api/pipeline", json={"modulation": 1})
            assert r.status == 403
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_invalid_body_is_a_400(self, bridge):
        client = self._client(bridge)
        await client.start_server()
        try:
            r = await client.post("/api/channel", data="not json",
                                  headers={"Content-Type": "application/json"})
            assert r.status == 400
        finally:
            await client.close()


class TestSpacecraftTxGating:
    """2026-08c item 6: PA-off must drop the carrier; pass-override must not
    resurrect a transmitter that is off."""

    def _feedback(self, bridge, *, tx_active, in_contact=True,
                  override=False, pa_on=None):
        bridge._apply_sim_feedback({"state": {
            "ttc": {"tx_active": tx_active,
                    **({"pa_on": pa_on} if pa_on is not None else {})},
            "in_contact": in_contact,
            "override_passes": override,
        }})

    def test_pa_off_drops_the_carrier_even_in_view(self):
        from smo_rfsim.bridge import RFSimBridge
        bridge = RFSimBridge(_rf_config())
        try:
            self._feedback(bridge, tx_active=True, in_contact=True)
            assert bridge._pipeline._tx._transmitting is True
            # PA goes off mid-pass: the sim reports tx_active False.
            self._feedback(bridge, tx_active=False, in_contact=True,
                           pa_on=False)
            assert bridge._pipeline._tx._transmitting is False
            assert bridge.radio.status.sc_tx_on is False
            assert bridge.radio.status.sc_pa_on is False
        finally:
            bridge._pipeline.stop()

    def test_pass_override_cannot_resurrect_a_dead_tx(self):
        from smo_rfsim.bridge import RFSimBridge
        bridge = RFSimBridge(_rf_config())
        try:
            # Out of geometric contact, override ON, but the PA is OFF:
            # the override forces VISIBILITY only — no carrier.
            self._feedback(bridge, tx_active=False, in_contact=False,
                           override=True)
            assert bridge._pipeline._link_in_view is True
            assert bridge._pipeline._tx._transmitting is False
            assert bridge.radio.status.pass_override is True
            # PA back on with the override still up: carrier returns.
            self._feedback(bridge, tx_active=True, in_contact=False,
                           override=True)
            assert bridge._pipeline._tx._transmitting is True
        finally:
            bridge._pipeline.stop()

    def test_manual_transmit_override_wins(self):
        from smo_rfsim.bridge import RFSimBridge
        bridge = RFSimBridge(_rf_config())
        try:
            bridge.apply_link({"transmitting": True})
            self._feedback(bridge, tx_active=False, in_contact=True)
            # The /api/link manual override outranks the sim feedback.
            assert bridge._pipeline._tx._transmitting is True
        finally:
            bridge._pipeline.stop()
